"""Deterministic, read-only repository access. No LLM in this file.

Every read goes through `git show <sha>:<path>` at a pinned commit, so:
- findings are reproducible (same SHA, same bytes),
- uncommitted edits in a working tree can't change what agents see,
- paths can't escape the repository (git resolves them inside the tree).
"""
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from changeblast.config import FIXTURES_DIR

MAX_READ_LINES = 400
MAX_SEARCH_RESULTS = 60
SECRET_PATTERNS = re.compile(r"(^|/)(\.env[^/]*|.*\.pem|.*\.key|id_rsa[^/]*|secrets?\.[a-z]+)$", re.IGNORECASE)


class RepoError(Exception):
    """Safe, user-facing error (no stack traces leak to agents or the UI)."""


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo_root(repo)), *args],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RepoError(f"git error in {repo}: {result.stderr.strip()[:200]}")
    return result.stdout


def available_repos() -> list[str]:
    """The allowlist: only directories under fixtures/ that are git repositories."""
    return sorted(p.name for p in Path(FIXTURES_DIR).iterdir() if (p / ".git").is_dir())


def repo_root(repo: str) -> Path:
    if repo not in available_repos():
        raise RepoError(f"repository not in allowlist: {repo!r}")
    return Path(FIXTURES_DIR) / repo


@lru_cache(maxsize=64)
def head_sha(repo: str) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _check_path(path: str) -> str:
    path = path.strip()
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise RepoError(f"invalid path: {path!r}")
    path = path.removeprefix("./")
    if not path:
        raise RepoError(f"invalid path: {path!r}")
    if SECRET_PATTERNS.search(path):
        raise RepoError(f"reading secret-like files is not allowed: {path!r}")
    return path


@lru_cache(maxsize=64)
def list_files(repo: str, sha: str | None = None) -> tuple[str, ...]:
    sha = sha or head_sha(repo)
    files = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    return tuple(f for f in files if not SECRET_PATTERNS.search(f) and not f.endswith(".gitignore"))


@lru_cache(maxsize=512)
def file_lines(repo: str, path: str, sha: str | None = None) -> tuple[str, ...]:
    sha = sha or head_sha(repo)
    path = _check_path(path)
    if path not in list_files(repo, sha):
        raise RepoError(f"file not found in {repo}@{sha[:7]}: {path}")
    return tuple(_git(repo, "show", f"{sha}:{path}").splitlines())


def read_file(repo: str, path: str, start: int = 1, end: int | None = None) -> dict:
    lines = file_lines(repo, path)
    start = max(1, start)
    end = min(len(lines), end or len(lines), start + MAX_READ_LINES - 1)
    return {
        "repo": repo, "commit": head_sha(repo), "path": _check_path(path),
        "line_start": start, "line_end": end, "total_lines": len(lines),
        "content": "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1)),
    }


def search(repo: str, query: str, regex: bool = False, path_glob: str | None = None) -> dict:
    """Case-insensitive literal (or regex) search across the repo at its pinned commit."""
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as e:
        raise RepoError(f"invalid regex: {e}") from None
    hits = []
    for path in list_files(repo):
        if path_glob and not Path(path).match(path_glob):
            continue
        for i, line in enumerate(file_lines(repo, path), start=1):
            if pattern.search(line):
                hits.append({"path": path, "line": i, "text": line.strip()[:200]})
                if len(hits) >= MAX_SEARCH_RESULTS:
                    return {"repo": repo, "commit": head_sha(repo), "query": query, "hits": hits, "truncated": True}
    return {"repo": repo, "commit": head_sha(repo), "query": query, "hits": hits, "truncated": False}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_evidence(repo: str, commit: str, path: str, line_start: int, line_end: int, excerpt: str) -> dict:
    """Re-open the cited file at the pinned commit and check the excerpt is really there.

    verified   - excerpt found inside the cited line range
    relocated  - excerpt found in the same file, but at different lines (corrected range returned)
    unverified - file, commit, or excerpt not found; the claim can't be shown to the user as fact
    """
    try:
        if commit != head_sha(repo):
            return {"status": "unverified", "note": f"cited commit {commit[:7]} is not the pinned commit"}
        lines = file_lines(repo, path, commit)
    except RepoError as e:
        return {"status": "unverified", "note": str(e)}

    line_start, line_end = int(line_start), int(line_end)
    needle = _norm(excerpt)
    if len(needle) < 4:
        return {"status": "unverified", "note": "excerpt too short to verify"}
    n_excerpt_lines = max(1, excerpt.strip().count("\n") + 1)

    def window(a: int, b: int) -> str:
        return _norm(" ".join(lines[max(0, a - 1):min(len(lines), b)]))

    if 1 <= line_start <= line_end <= len(lines) and needle in window(line_start, line_end):
        return {"status": "verified", "line_start": line_start, "line_end": line_end}
    for i in range(1, len(lines) + 1):
        if needle in window(i, i + n_excerpt_lines - 1):
            return {"status": "relocated", "line_start": i, "line_end": i + n_excerpt_lines - 1,
                    "note": f"excerpt is at lines {i}-{i + n_excerpt_lines - 1}, not {line_start}-{line_end}"}
    return {"status": "unverified", "note": "excerpt not found in file"}
