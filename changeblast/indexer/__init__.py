"""Build (once per repo@sha) the code index: files, symbols, dependency edges, and a quality score.

Run: python -m changeblast.indexer <repo> [--force]
"""
import asyncio
import os
import re
import posixpath
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor

import tree_sitter_language_pack as tslp
from psycopg.types.json import Jsonb

from changeblast import repos, store
from changeblast.indexer.parse import CODE_LANGS, UNSUPPORTED_CODE_EXT, parse_file
from changeblast.indexer.quality import quality_score
from changeblast.indexer.resolve import Resolver

SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "__pycache__", "coverage", ".next", "bower_components",
             ".git", ".venv", "venv", "site-packages"}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                 ".snap", ".pyc")
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar", ".woff", ".woff2",
              ".ttf", ".eot", ".otf", ".mp4", ".mp3", ".wav", ".jar", ".so", ".dylib", ".exe", ".bin", ".db",
              ".sqlite", ".xlsx", ".xls", ".docx", ".pptx"}
MAX_FILE_BYTES = 512 * 1024
# Calls whose target is chosen from a string at runtime: invisible to a static graph, so we count them.
DYNAMIC_DISPATCH = re.compile(rb"""\bgetattr\(|importlib\.import_module\(|__import__\(|\brequire\(\s*[^"'\s)]|"""
                              rb"""\bfrappe\.(get_doc|get_cached_doc|new_doc|get_all|get_list|get_value|get_attr|call)\(\s*["']|"""
                              rb"""\bfrappe\.db\.(get_value|get_all|set_value)\(\s*["']""")


def _tree(repo: str, sha: str) -> list[tuple[str, str, int]]:
    """(path, blob_id, size) for every file at the commit."""
    out = subprocess.run(["git", "-C", str(repos.repo_root(repo)), "ls-tree", "-r", "-l", sha],
                         capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        _mode, kind, blob, size = meta.split()
        if kind == "blob":
            rows.append((path, blob, int(size) if size != "-" else 0))
    return rows


def _read_blobs(repo: str, blob_ids: list[str]) -> dict[str, bytes]:
    """Stream many blobs through one `git cat-file --batch` process (no per-file subprocess)."""
    proc = subprocess.Popen(["git", "-C", str(repos.repo_root(repo)), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    blobs = {}
    # One request, one read: writing every id first deadlocks once git's output fills the pipe buffer.
    for blob in blob_ids:
        proc.stdin.write(blob.encode() + b"\n")
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        size = int(header[2])
        blobs[blob] = proc.stdout.read(size)
        proc.stdout.read(1)  # trailing newline
    proc.stdin.close()
    proc.wait()
    return blobs


def classify(path: str, size: int) -> tuple[str, str | None]:
    """category in code | unsupported_code | text | skipped, plus the detected language."""
    parts = path.split("/")
    ext = posixpath.splitext(path)[1].lower()
    if any(p in SKIP_DIRS for p in parts[:-1]) or path.endswith(SKIP_SUFFIXES):
        return "skipped", None
    if ext in BINARY_EXT or size > MAX_FILE_BYTES:
        return "skipped", None
    lang = tslp.detect_language_from_path(path)
    if lang in CODE_LANGS:
        return "code", lang
    if ext in UNSUPPORTED_CODE_EXT:
        return "unsupported_code", lang
    return "text", lang


def _parse_batch(batch: list[tuple[str, str, bytes]]) -> list[dict]:
    return [parse_file(p, lang, src) for p, lang, src in batch]


def build(repo: str) -> dict:
    """CPU part (sync): walk, parse in a process pool, resolve. Returns everything to store."""
    t0 = time.monotonic()
    sha = repos.head_sha(repo)
    tree = _tree(repo, sha)
    categories: dict[str, str] = {}
    langs: dict[str, str | None] = {}
    for path, _blob, size in tree:
        categories[path], langs[path] = classify(path, size)
    code = [(p, b) for p, b, _ in tree if categories[p] == "code"]
    blobs = _read_blobs(repo, [b for _, b in code])
    items = [(p, langs[p], blobs[b]) for p, b in code if b"\x00" not in blobs[b][:4096]]

    tslp.download(sorted({lang for _, lang, _ in items}))  # fetch grammars once, before forking workers
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    batches = [items[i:i + 50] for i in range(0, len(items), 50)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        files = [f for chunk in pool.map(_parse_batch, batches) for f in chunk]
    t_parse = time.monotonic()

    edges, ref_stats = Resolver(files).build()
    dynamic = sum(len(DYNAMIC_DISPATCH.findall(src)) for _, _, src in items)
    quality = quality_score(files, ref_stats, categories, dynamic)
    counts = {c: sum(1 for v in categories.values() if v == c) for c in ("code", "unsupported_code", "text", "skipped")}
    stats = {"files_total": len(tree), **counts, "symbols": sum(len(f["defs"]) for f in files), "edges": len(edges),
             "edges_by_confidence": {c: sum(1 for e in edges if e["confidence"] == c)
                                     for c in ("resolved", "name_match", "ambiguous")},
             "parse_seconds": round(t_parse - t0, 1), "total_seconds": round(time.monotonic() - t0, 1),
             "workers": workers}
    return {"repo": repo, "sha": sha, "files": files, "edges": edges, "categories": categories, "langs": langs,
            "stats": stats, "quality": quality}


async def store_index(result: dict) -> int:
    async with store.pool.connection() as conn:
        await conn.execute("DELETE FROM code_indexes WHERE repo = %s AND commit_sha = %s",
                           (result["repo"], result["sha"]))
        row = await (await conn.execute(
            "INSERT INTO code_indexes (repo, commit_sha, status, stats, quality, finished_at) "
            "VALUES (%s, %s, 'ready', %s, %s, now()) RETURNING id",
            (result["repo"], result["sha"], Jsonb(result["stats"]), Jsonb(result["quality"])))).fetchone()
        idx = row[0]
        parsed = {f["path"]: f for f in result["files"]}
        async with conn.cursor() as cur:
            async with cur.copy("COPY code_files (index_id, path, language, category, loc, has_error, n_defs, n_refs) "
                                "FROM STDIN") as cp:
                for path, cat in result["categories"].items():
                    f = parsed.get(path)
                    await cp.write_row((idx, path, result["langs"][path], cat, f["loc"] if f else None,
                                        bool(f and (f["has_error"] or f["error"])), len(f["defs"]) if f else 0,
                                        len(f["refs"]) if f else 0))
            async with cur.copy("COPY code_symbols (index_id, path, name, kind, line_start, line_end) FROM STDIN") as cp:
                for f in result["files"]:
                    for d in f["defs"]:
                        await cp.write_row((idx, f["path"], d["name"], d["kind"], d["line_start"], d["line_end"]))
            async with cur.copy("COPY code_edges (index_id, src_path, dst_path, kind, symbol, confidence, line) "
                                "FROM STDIN") as cp:
                for e in result["edges"]:
                    await cp.write_row((idx, e["src"], e["dst"], e["kind"], e["symbol"][:300], e["confidence"],
                                        e["line"]))
    return idx


async def get_index(repo: str) -> dict | None:
    async with store.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT id, commit_sha, status, stats, quality, finished_at FROM code_indexes "
            "WHERE repo = %s AND commit_sha = %s", (repo, repos.head_sha(repo)))).fetchone()
    if not row:
        return None
    return {"index_id": row[0], "repo": repo, "commit": row[1], "status": row[2], "stats": row[3],
            "quality": row[4], "finished_at": row[5].isoformat() if row[5] else None}


async def ensure_index(repo: str, force: bool = False) -> dict:
    """Index repo@sha unless it's already indexed (the index is cached per commit)."""
    if not force and (existing := await get_index(repo)):
        return existing
    result = await asyncio.get_running_loop().run_in_executor(None, build, repo)
    await store_index(result)
    return await get_index(repo)
