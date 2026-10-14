"""MCP server exposing bounded, read-only tools over ONE repository (plus shared contracts).

Each repo agent launches its own instance: `python -m changeblast.mcp_repo_server billing-service`.
The repository is fixed at launch, so an agent physically cannot read another team's code; it has
to ask that team's agent over A2A. No shell, no writes, no secret files.
"""
import json
import sys

from mcp.server.mcpserver import MCPServer

from changeblast import repos

REPO = sys.argv[1] if len(sys.argv) > 1 else ""
CONTRACTS = "shared-contracts"

mcp = MCPServer(f"repo-tools:{REPO}", instructions=f"Read-only access to {REPO} at a pinned commit.")


def _safe(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs))
    except repos.RepoError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_files() -> str:
    """List every file in this repository at the pinned commit."""
    return json.dumps({"repo": REPO, "commit": repos.head_sha(REPO), "files": list(repos.list_files(REPO))})


@mcp.tool()
def search_code(query: str, regex: bool = False, path_glob: str | None = None) -> str:
    """Case-insensitive search. Returns path, line number, and line text for each hit.
    Use regex=true for patterns (e.g. 'refund.*(days|window)'). path_glob filters files, e.g. '*.py'."""
    return _safe(repos.search, REPO, query, regex=regex, path_glob=path_glob)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a file with line numbers (max 400 lines per call). Cite these exact line numbers."""
    return _safe(repos.read_file, REPO, path, start_line, end_line)


@mcp.tool()
def search_contracts(query: str) -> str:
    """Search the shared event/API contracts repository (read-only)."""
    return _safe(repos.search, CONTRACTS, query)


@mcp.tool()
def read_contract(path: str) -> str:
    """Read a file from the shared contracts repository, e.g. 'events/refund-approved.schema.json'."""
    return _safe(repos.read_file, CONTRACTS, path)


if __name__ == "__main__":
    repos.repo_root(REPO)  # refuse to start for a repository outside the allowlist
    mcp.run("stdio")
