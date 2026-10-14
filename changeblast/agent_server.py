"""A repository agent served over A2A. One process per repository.

Run: REPO=billing-service PORT=9300 uvicorn changeblast.agent_server:app --port 9300
Card: GET /.well-known/agent-card.json   RPC: POST / (JSON-RPC SendMessage)
"""
import os
from contextlib import asynccontextmanager

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI

from changeblast import repos, router, store
from changeblast.config import agent_url
from changeblast.investigator import LLMUnavailable, investigate

REPO = os.environ["REPO"]
repos.repo_root(REPO)  # refuse to start for a repository outside the allowlist

card = AgentCard(
    name=f"{REPO} agent",
    description=f"Investigates change impact inside the {REPO} repository using read-only code tools. "
                f"Only this agent can read {REPO}.",
    version="1.0.0",
    supported_interfaces=[AgentInterface(url=agent_url(REPO) + "/", protocol_binding="JSONRPC",
                                         protocol_version="1.0")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["application/json"],
    default_output_modes=["application/json"],
    skills=[AgentSkill(
        id="investigate_change_impact",
        name="Investigate change impact",
        description="Input: {investigation_id, task_id, change_request, task, context[]}. Output: "
                    "{findings[], cross_service_questions[], open_questions[], coverage_note, commit} with "
                    "file/line evidence from this repository.",
        tags=["code-search", "impact-analysis", REPO],
        input_modes=["application/json"],
        output_modes=["application/json"],
    )],
)


class RepoAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        req = get_data_parts(context.message.parts)[0]
        # Attribute this agent's LLM spend to the investigation in the shared cost ledger.
        router.current_investigation.set(req.get("investigation_id"))
        router.current_component.set(f"agent:{REPO}")
        try:
            result = await investigate(REPO, req["change_request"], req["task"], req.get("context"))
            result["status"] = "completed"
        except LLMUnavailable as e:
            # Report failure as data so the orchestrator records partial coverage instead of crashing.
            result = {"status": "failed", "error": str(e), "repo": REPO, "findings": []}
        result["task_id"] = req.get("task_id")
        await event_queue.enqueue_event(new_data_message(result, context_id=context.context_id))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("investigations are not cancellable in the MVP")


handler = DefaultRequestHandler(agent_executor=RepoAgentExecutor(), task_store=InMemoryTaskStore(),
                                agent_card=card)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.pool.open()
    await store.setup()
    yield
    await store.pool.close()


app = FastAPI(title=f"{REPO} agent (A2A)", lifespan=lifespan)
add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card),
                          jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))
