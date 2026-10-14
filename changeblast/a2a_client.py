"""Orchestrator side of A2A: discover a repo agent from its card, send one task, get findings."""
import asyncio

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest

from changeblast.config import AGENT_TIMEOUT_SECONDS, agent_url


class AgentUnavailable(Exception):
    pass


def _ints(value):
    """A2A data parts are protobuf Structs, where every number is a double. Restore whole numbers to int."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _ints(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ints(v) for v in value]
    return value


async def discover(repo: str) -> dict:
    """Fetch the agent card. Returns a JSON-able summary for the trace."""
    async with httpx.AsyncClient(timeout=5) as http:
        card = await A2ACardResolver(http, agent_url(repo)).get_agent_card()
    return {"name": card.name, "url": card.supported_interfaces[0].url,
            "skills": [s.id for s in card.skills], "version": card.version}


async def send_task(repo: str, payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT_SECONDS) as http:
            card = await A2ACardResolver(http, agent_url(repo)).get_agent_card()
            client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
            msg = new_data_message(payload, role=Role.ROLE_USER)
            async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
                async for event in client.send_message(SendMessageRequest(message=msg)):
                    if event.HasField("message"):
                        return _ints(get_data_parts(event.message.parts)[0])
    except Exception as e:  # noqa: BLE001 - any transport/discovery failure = missing coverage
        raise AgentUnavailable(f"{repo} agent unreachable: {type(e).__name__}") from None
    raise AgentUnavailable(f"{repo} agent returned no message")
