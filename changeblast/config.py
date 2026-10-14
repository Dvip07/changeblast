import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Git repos built from fixtures/ by scripts/build_repos.sh (deterministic SHAs).
FIXTURES_DIR = os.getenv("FIXTURES_DIR", str(ROOT / "repos"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:dev@localhost:5433/changeblast")
# Model routing (see changeblast/router.py). Complex reasoning uses Sonnet, not Opus.
REASON_MODEL = os.getenv("REASON_MODEL", "claude-sonnet-5-5")
BOOL_MODELS = os.getenv("BOOL_MODELS", "typesafe/jev-router,openai/gpt-5-nano").split(",")
EXTRACT_MODELS = os.getenv("EXTRACT_MODELS", "deepseek/deepseek-v4.1-flash,openai/gpt-oss-120b").split(",")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
OPENROUTER_CONCURRENCY = int(os.getenv("OPENROUTER_CONCURRENCY", "20"))
ANTHROPIC_CONCURRENCY = int(os.getenv("ANTHROPIC_CONCURRENCY", "6"))
DAILY_LIMIT_USD = float(os.getenv("DAILY_LIMIT_USD", "15"))
# Claude Sonnet 5.5, USD per 1M tokens (cache write = 5-minute TTL, 1.25x input).
ANTHROPIC_PRICES = {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50}

# A2A agent host (changeblast/agent_host.py): one addressable agent per repository or module.
AGENT_HOST_URL = os.getenv("AGENT_HOST_URL", "http://localhost:9300")


def agent_url(agent_id: str) -> str:
    return f"{AGENT_HOST_URL}/agents/{agent_id}"


# Per-agent budgets (spec: bounded tool calls and time per task).
AGENT_MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "14"))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "180"))
