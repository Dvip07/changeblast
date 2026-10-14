import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Git repos built from fixtures/ by scripts/build_repos.sh (deterministic SHAs).
FIXTURES_DIR = os.getenv("FIXTURES_DIR", str(ROOT / "repos"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:dev@localhost:5433/changeblast")
MODEL = os.getenv("CHANGEBLAST_MODEL", "claude-opus-5-5")

# Repo agents run as separate A2A services, one per repository, on consecutive ports.
AGENT_BASE_PORT = int(os.getenv("AGENT_BASE_PORT", "9300"))
AGENT_REPOS = ["billing-service", "payments-service", "notification-service", "checkout-web", "shared-contracts"]


def agent_url(repo: str) -> str:
    return os.getenv(f"AGENT_URL_{repo.upper().replace('-', '_')}",
                     f"http://localhost:{AGENT_BASE_PORT + AGENT_REPOS.index(repo)}")


# Per-agent budgets (spec: bounded tool calls and time per task).
AGENT_MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "14"))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "180"))
