"""GitHub ingestion: resolve a repo with GitHub's official MCP server, clone it at the pinned SHA, index it.

- Metadata (default branch, head SHA, visibility) comes from GitHub's hosted MCP server, read-only
  endpoint (https://api.githubcopilot.com/mcp/readonly): no write tools are even exposed.
- The clone fetches exactly that SHA (`git fetch --depth 1 origin <sha>`), so the index and every citation
  are pinned to the commit GitHub reported.
- A private repo's token is passed to git through environment variables (http.extraHeader), never in
  the remote URL or .git/config, and is never stored or logged.
"""
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from changeblast import repos
from changeblast.config import FIXTURES_DIR, GITHUB_TOKEN

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/readonly"
URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
                    r"(?:/tree/([A-Za-z0-9_./-]+))?/?$")
MAX_FILES = 40_000
MAX_CHECKOUT_BYTES = 1_500 * 1024 * 1024  # files at the commit, not GitHub's history-inclusive size
_clone_lock = asyncio.Lock()


class GitHubError(Exception):
    """Safe, user-facing message (never contains a token)."""


def parse_url(url: str) -> tuple[str, str, str | None]:
    m = URL_RE.match(url.strip())
    if not m:
        raise GitHubError("expected a GitHub URL like https://github.com/owner/repo")
    return m.group(1), m.group(2), m.group(3)


def local_name(owner: str, repo: str) -> str:
    return f"{owner}__{repo}"


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


async def resolve(owner: str, repo: str, ref: str | None, token: str | None) -> dict:
    """Repo metadata + the commit SHA to pin, via the GitHub MCP server."""
    auth = token or GITHUB_TOKEN
    if not auth:
        raise GitHubError("server has no GITHUB_TOKEN configured")
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {auth}"})
    try:
        async with Client(streamable_http_client(GITHUB_MCP_URL, http_client=http)) as gh:
            r = await gh.call_tool("search_repositories", {"query": f"repo:{owner}/{repo}", "minimal_output": False})
            if r.is_error:
                raise GitHubError(f"repository {owner}/{repo} not found, or the token can't read it")
            data = json.loads(r.content[0].text)
            items = data.get("items") or data.get("repositories") or []
            if not items:
                raise GitHubError(f"repository {owner}/{repo} not found, or the token can't read it")
            meta = items[0]
            branch = ref or meta["default_branch"]
            c = await gh.call_tool("list_commits", {"owner": owner, "repo": repo, "sha": branch, "perPage": 1})
            if c.is_error:
                raise GitHubError(f"branch or ref '{branch}' not found")
            commits = json.loads(c.content[0].text)
            sha = (commits[0] if isinstance(commits, list) else commits)["sha"]
    except GitHubError:
        raise
    except BaseExceptionGroup as eg:  # the MCP client's task group wraps errors raised inside it
        ours = eg.subgroup(GitHubError)
        if ours:
            leaf = ours.exceptions[0]
            while isinstance(leaf, BaseExceptionGroup):
                leaf = leaf.exceptions[0]
            raise leaf from None
        raise GitHubError(_scrub(f"GitHub MCP lookup failed: {type(eg.exceptions[0]).__name__}", auth)) from None
    except Exception as e:  # noqa: BLE001
        raise GitHubError(_scrub(f"GitHub MCP lookup failed: {type(e).__name__}", auth)) from None
    return {"full_name": meta["full_name"], "default_branch": meta["default_branch"], "ref": branch, "sha": sha,
            "private": bool(meta.get("private")), "language": meta.get("language"),
            "github_size_kb": meta.get("size"), "description": (meta.get("description") or "")[:300]}


def _git_env(token: str | None) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


def _run(args: list[str], cwd: Path, token: str | None, timeout: int = 600) -> None:
    r = subprocess.run(args, cwd=cwd, env=_git_env(token), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise GitHubError(_scrub(f"git {args[1]} failed: {r.stderr.strip()[-300:]}", token))


def clone_at(owner: str, repo: str, sha: str, token: str | None) -> dict:
    """Shallow-fetch exactly `sha` into repos/<owner>__<repo> (replacing an older checkout)."""
    root = Path(FIXTURES_DIR)
    name = local_name(owner, repo)
    tmp = root / f".tmp-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    try:
        _run(["git", "init", "-q", "-b", "main"], tmp, None)
        _run(["git", "remote", "add", "origin", f"https://github.com/{owner}/{repo}.git"], tmp, None)
        _run(["git", "fetch", "-q", "--depth", "1", "origin", sha], tmp, token)
        _run(["git", "checkout", "-q", "FETCH_HEAD"], tmp, None)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
        if head != sha:
            raise GitHubError(f"checked out {head[:7]}, expected {sha[:7]}")
        files = subprocess.run(["git", "ls-tree", "-r", "-l", sha], cwd=tmp, capture_output=True, text=True).stdout
        sizes = [int(l.split()[3]) for l in files.splitlines() if l.split()[3] != "-"]
        if len(sizes) > MAX_FILES or sum(sizes) > MAX_CHECKOUT_BYTES:
            raise GitHubError(f"repository too large for the demo ({len(sizes):,} files, "
                              f"{sum(sizes) / 1e6:,.0f} MB at this commit; limits {MAX_FILES:,} files / "
                              f"{MAX_CHECKOUT_BYTES / 1e6:,.0f} MB)")
        if token and token in (tmp / ".git" / "config").read_text():
            raise GitHubError("refusing to keep a checkout that contains a token")  # defence in depth
        target = root / name
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    for fn in (repos.list_files, repos.text_files):  # keyed by SHA; cleared to free memory
        fn.cache_clear()
    return {"name": name, "files": len(sizes), "bytes": sum(sizes)}


async def ingest(url: str, token: str | None, on_status) -> dict:
    """Resolve -> clone at SHA -> (caller indexes). `on_status(status, info)` reports progress."""
    owner, repo, ref = parse_url(url)
    await on_status("resolving", {"repo": f"{owner}/{repo}"})
    meta = await resolve(owner, repo, ref, token)
    if meta["private"] and not token:
        raise GitHubError("this repository is private: paste a read-only token for it")
    await on_status("cloning", meta)
    async with _clone_lock:  # one clone at a time keeps disk and bandwidth predictable
        local = await asyncio.to_thread(clone_at, owner, repo, meta["sha"], token)
    return {**meta, **local}
