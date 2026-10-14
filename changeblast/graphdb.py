"""Read-only queries over the code index (repo@sha). Sync on purpose: used inside the MCP server
subprocess as well as by candidate selection (wrapped in a thread there)."""
from functools import lru_cache

import psycopg

from changeblast import repos
from changeblast.config import DATABASE_URL

CONF_WEIGHT = {"resolved": 1.0, "name_match": 0.6, "ambiguous": 0.25}


def _conn():
    return psycopg.connect(DATABASE_URL, autocommit=True)


@lru_cache(maxsize=32)
def index_id(repo: str, sha: str | None = None) -> int | None:
    with _conn() as c:
        row = c.execute("SELECT id FROM code_indexes WHERE repo = %s AND commit_sha = %s AND status = 'ready'",
                        (repo, sha or repos.head_sha(repo))).fetchone()
    return row[0] if row else None


def _idx(repo: str) -> int:
    idx = index_id(repo)
    if idx is None:
        raise repos.RepoError(f"{repo} is not indexed at its pinned commit")
    return idx


def find_symbol(repo: str, name: str, limit: int = 20) -> list[dict]:
    """Definitions whose name equals `name` (case-insensitive), then names containing it."""
    with _conn() as c:
        rows = c.execute(
            "SELECT path, name, kind, line_start, line_end FROM code_symbols WHERE index_id = %s AND "
            "(lower(name) = lower(%s) OR name ILIKE %s) ORDER BY (lower(name) = lower(%s)) DESC, path LIMIT %s",
            (_idx(repo), name, f"%{name}%", name, limit)).fetchall()
    return [{"path": r[0], "name": r[1], "kind": r[2], "line_start": r[3], "line_end": r[4]} for r in rows]


def who_uses(repo: str, path: str, symbol: str | None = None, limit: int = 40) -> list[dict]:
    """Reverse edges: files that import or reference `path` (optionally only via `symbol`)."""
    sql = ("SELECT src_path, kind, symbol, confidence, min(line), count(*) FROM code_edges "
           "WHERE index_id = %s AND dst_path = %s" + (" AND symbol = %s" if symbol else "") +
           " GROUP BY src_path, kind, symbol, confidence "
           "ORDER BY CASE confidence WHEN 'resolved' THEN 0 WHEN 'name_match' THEN 1 ELSE 2 END, src_path LIMIT %s")
    args = (_idx(repo), path, symbol, limit) if symbol else (_idx(repo), path, limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [{"path": r[0], "kind": r[1], "symbol": r[2], "confidence": r[3], "line": r[4], "count": r[5]} for r in rows]


def dependencies_of(repo: str, path: str, limit: int = 40) -> list[dict]:
    """Forward edges: files that `path` imports or references."""
    with _conn() as c:
        rows = c.execute(
            "SELECT dst_path, kind, symbol, confidence, min(line), count(*) FROM code_edges "
            "WHERE index_id = %s AND src_path = %s GROUP BY dst_path, kind, symbol, confidence "
            "ORDER BY CASE confidence WHEN 'resolved' THEN 0 WHEN 'name_match' THEN 1 ELSE 2 END, dst_path LIMIT %s",
            (_idx(repo), path, limit)).fetchall()
    return [{"path": r[0], "kind": r[1], "symbol": r[2], "confidence": r[3], "line": r[4], "count": r[5]} for r in rows]


def outline(repo: str, path: str) -> dict:
    """A file: its definitions with line spans. A directory: its files with definition counts."""
    path = path.strip("/")
    idx = _idx(repo)
    with _conn() as c:
        rows = c.execute("SELECT name, kind, line_start, line_end FROM code_symbols WHERE index_id = %s AND path = %s "
                         "ORDER BY line_start", (idx, path)).fetchall()
        if rows:
            return {"path": path, "definitions": [{"name": r[0], "kind": r[1], "lines": [r[2], r[3]]} for r in rows]}
        files = c.execute("SELECT path, language, n_defs FROM code_files WHERE index_id = %s AND path LIKE %s "
                          "AND category <> 'skipped' ORDER BY path LIMIT 300", (idx, f"{path}/%" if path else "%")).fetchall()
    return {"path": path or "/", "files": [{"path": r[0], "language": r[1], "definitions": r[2]} for r in files],
            "truncated": len(files) == 300}


def symbol_seeds(repo: str, tokens: list[str], limit: int = 60) -> list[dict]:
    """Non-test definitions whose names contain any of the given tokens (exact names first)."""
    from changeblast.indexer.resolve import is_test
    tokens = [t for t in {t.lower() for t in tokens} if len(t) >= 4]
    if not tokens:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT path, name, kind, line_start FROM code_symbols WHERE index_id = %s AND lower(name) LIKE ANY(%s) "
            "ORDER BY (lower(name) = ANY(%s)) DESC, length(name) LIMIT %s",
            (_idx(repo), [f"%{t}%" for t in tokens], tokens, limit * 3)).fetchall()
    return [{"path": r[0], "name": r[1], "kind": r[2], "line": r[3]} for r in rows if not is_test(r[0])][:limit]


def reverse_edges(repo: str, paths: list[str]) -> list[tuple[str, str, str, str]]:
    """(src, dst, symbol, confidence) for every edge pointing into `paths`."""
    if not paths:
        return []
    with _conn() as c:
        return c.execute("SELECT src_path, dst_path, min(symbol), confidence FROM code_edges WHERE index_id = %s "
                         "AND dst_path = ANY(%s) AND src_path <> dst_path GROUP BY src_path, dst_path, confidence",
                         (_idx(repo), paths)).fetchall()


def file_count(repo: str) -> int:
    with _conn() as c:
        row = c.execute("SELECT count(*) FROM code_files WHERE index_id = %s AND category IN ('code', 'text')",
                        (_idx(repo),)).fetchone()
    return row[0]


def reverse_refs(repo: str, paths: list[str], per_target: int = 200) -> list[tuple]:
    """(src, dst, kind, symbol, confidence, line) for edges into `paths` (capped per target file)."""
    if not paths:
        return []
    with _conn() as c:
        return c.execute(
            "SELECT src_path, dst_path, kind, symbol, confidence, line FROM ("
            "  SELECT *, row_number() OVER (PARTITION BY dst_path ORDER BY CASE confidence WHEN 'resolved' THEN 0 "
            "    WHEN 'name_match' THEN 1 ELSE 2 END) AS rn FROM code_edges "
            "  WHERE index_id = %s AND dst_path = ANY(%s) AND src_path <> dst_path) e WHERE rn <= %s",
            (_idx(repo), paths, per_target)).fetchall()


def enclosing_symbols(repo: str, path_lines: list[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """Innermost definition containing each (path, line), e.g. the function that makes a call."""
    if not path_lines:
        return {}
    paths = sorted({p for p, _ in path_lines})
    with _conn() as c:
        rows = c.execute("SELECT path, name, line_start, line_end FROM code_symbols WHERE index_id = %s "
                         "AND path = ANY(%s)", (_idx(repo), paths)).fetchall()
    by_path: dict[str, list] = {}
    for p, name, a, b in rows:
        by_path.setdefault(p, []).append((name, a, b))
    out = {}
    for p, line in path_lines:
        inside = [(b - a, name) for name, a, b in by_path.get(p, []) if a <= line <= b]
        if inside:
            out[(p, line)] = min(inside)[1]
    return out
