"""Shared Claude client settings."""
import anthropic

from changeblast.config import MODEL

client = anthropic.AsyncAnthropic(max_retries=2, timeout=120)

# Opus 5.5 can decline on safety classifiers; "default" fallbacks reroute those requests
# server-side instead of failing the investigation. (Opt-in beta.)
COMMON = dict(
    model=MODEL,
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
)


class LLMUnavailable(Exception):
    """Raised with a safe message when the model can't be reached or declines."""


def describe_error(e: Exception) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "LLM authentication failed (check ANTHROPIC_API_KEY)"
    if isinstance(e, anthropic.RateLimitError):
        return "LLM rate limited"
    if isinstance(e, anthropic.APIStatusError):
        return f"LLM API error {e.status_code}"
    if isinstance(e, anthropic.APIConnectionError):
        return "LLM unreachable (network)"
    return f"{type(e).__name__}"
