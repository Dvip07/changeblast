"""MCP server exposing bounded, read-only tools over ONE repository (plus shared contracts).

Each repo agent launches its own instance: `python -m changeblast.mcp_repo_server billing-service`.
The repository is fixed at launch, so an agent physically cannot read another team's code; it has
to ask that team's agent over A2A. No shell, no writes, no secret files.
"""
import json
import sys

from mcp.server.mcpserver import MCPServer

from changeblast import graphdb, repos

REPO = sys.argv[1] if len(sys.argv) > 1 else ""
SCOPE = sys.argv[2] if len(sys.argv) > 2 else ""  # module this agent focuses on ("" = whole repo)
CONTRACTS = "shared-contracts"
MAX_LIST = 300

mcp = MCPServer(f"repo-tools:{REPO}", instructions=f"Read-only access to {REPO} at a pinned commit.")


def _safe(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs))
    except repos.RepoError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_files(path_prefix: str | None = None) -> str:
    """List files at the pinned commit under path_prefix (defaults to your module). Max 300 per call."""
    prefix = (SCOPE if path_prefix is None else path_prefix).strip("/")
    files = [f for f in repos.list_files(REPO) if not prefix or f.startswith(prefix + "/")]
    return json.dumps({"repo": REPO, "commit": repos.head_sha(REPO), "prefix": prefix or "/",
                       "files": files[:MAX_LIST], "total": len(files), "truncated": len(files) > MAX_LIST})


@mcp.tool()
def find_symbol(name: str) -> str:
    """Where is `name` defined? Exact matches first, then names containing it. From the tree-sitter index."""
    return _safe(graphdb.find_symbol, REPO, name)


@mcp.tool()
def who_uses(path: str, symbol: str | None = None) -> str:
    """Files that import or reference `path` (optionally only via `symbol`): the blast radius of changing it.
    Each edge has a confidence: resolved (import traced), name_match (unique name), ambiguous (name defined in
    several places: verify before relying on it). Calls that pick their target from a string at runtime
    (getattr, frappe.get_doc("Doctype")) are NOT in the graph; use search_code for those."""
    return _safe(graphdb.who_uses, REPO, path, symbol)


@mcp.tool()
def dependencies_of(path: str) -> str:
    """Files that `path` imports or references (forward edges), with confidence."""
    return _safe(graphdb.dependencies_of, REPO, path)


@mcp.tool()
def outline(path: str) -> str:
    """Signatures without bodies: a file's definitions with line spans, or a directory's files.
    Cheaper than read_file for getting oriented in a large module."""
    return _safe(graphdb.outline, REPO, path)


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
