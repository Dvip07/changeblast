"""A2A agent host: serves one independently addressable agent per repository or module.

  GET  /agents                                         list agents created so far (with their cards)
  GET  /agents/{agent_id}/.well-known/agent-card.json  agent card (discovery)
  POST /agents/{agent_id}/                             A2A JSON-RPC (SendMessage)

agent_id is `repo` (whole repository) or `repo--module.path` (one module of a large repository),
e.g. `erpnext--erpnext.accounts`. Agents are created on first request for any allowlisted repo, so
a new GitHub repo needs no new process. Each agent reads code only through an MCP server launched
for its repository.

Run: uvicorn changeblast.agent_host:app --port 9300
"""
import re
from contextlib import asynccontextmanager

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI, HTTPException
from starlette.applications import Starlette
from google.protobuf.json_format import MessageToDict as pb_to_dict

from changeblast import repos, router, store
from changeblast.config import agent_url
from changeblast.investigator import LLMUnavailable, investigate

AGENT_ID = re.compile(r"^[A-Za-z0-9_.-]+(--[A-Za-z0-9_.-]+)?$")


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """-> (repo, scope path). Raises RepoError for ids outside the allowlist."""
    if not AGENT_ID.match(agent_id):
        raise repos.RepoError("invalid agent id")
    repo, _, module = agent_id.partition("--")
    repos.repo_root(repo)  # allowlist check
    scope = "" if module in ("", "other") else module.replace(".", "/")
    return repo, scope


def make_card(agent_id: str, repo: str, scope: str) -> AgentCard:
    where = f"the `{scope}` module of {repo}" if scope else f"the {repo} repository"
    return AgentCard(
        name=f"{agent_id} agent",
        description=f"Investigates change impact in {where} with read-only code and dependency-graph tools. "
                    f"Reports findings with file/line evidence at the pinned commit.",
        version="2.0.0",
        supported_interfaces=[AgentInterface(url=agent_url(agent_id) + "/", protocol_binding="JSONRPC",
                                             protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="investigate_change_impact", name="Investigate change impact",
                description="Input: {investigation_id, task_id, change_request, task, context[]}. Output: "
                            "{findings[], cross_service_questions[], open_questions[], coverage_note, commit}.",
                tags=["impact-analysis", repo] + ([scope] if scope else []),
                examples=["Which code in this module must change if the refund window moves to 30 days?"],
                input_modes=["application/json"], output_modes=["application/json"]),
            AgentSkill(
                id="trace_dependencies", name="Trace dependencies",
                description="Follows who-uses / depends-on edges in the code graph (tree-sitter index) and "
                            "reads the cited code before reporting.",
                tags=["code-graph", "tree-sitter"], input_modes=["application/json"],
                output_modes=["application/json"]),
        ],
    )


class RepoAgentExecutor(AgentExecutor):
    def __init__(self, agent_id: str, repo: str, scope: str):
        self.agent_id, self.repo, self.scope = agent_id, repo, scope

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        req = get_data_parts(context.message.parts)[0]
        router.current_investigation.set(req.get("investigation_id"))
        router.current_component.set(f"agent:{self.agent_id}")
        try:
            result = await investigate(self.repo, req["change_request"], req["task"], req.get("context"),
                                       scope=self.scope)
            result["status"] = "completed"
        except LLMUnavailable as e:
            result = {"status": "failed", "error": str(e), "repo": self.repo, "findings": []}
        result.update(task_id=req.get("task_id"), agent_id=self.agent_id)
        await event_queue.enqueue_event(new_data_message(result, context_id=context.context_id))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("investigations are not cancellable in the MVP")


_agents: dict[str, tuple[AgentCard, Starlette]] = {}


def get_agent(agent_id: str) -> tuple[AgentCard, Starlette]:
    if agent_id not in _agents:
        repo, scope = parse_agent_id(agent_id)
        card = make_card(agent_id, repo, scope)
        handler = DefaultRequestHandler(agent_executor=RepoAgentExecutor(agent_id, repo, scope),
                                        task_store=InMemoryTaskStore(), agent_card=card)
        sub = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/"))
        _agents[agent_id] = (card, sub)
    return _agents[agent_id]


class AgentDispatcher:
    """ASGI app mounted at /agents: routes /agents/{id}/... to that agent's A2A app, creating it lazily."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        # Starlette 1.x passes the full path to mounted apps and records the mount prefix in root_path.
        root = scope.get("root_path", "")
        rel = scope["path"][len(root):] if root and scope["path"].startswith(root) else scope["path"]
        agent_id, _, _rest = rel.lstrip("/").partition("/")
        try:
            _, sub = get_agent(agent_id)
        except repos.RepoError as e:
            from starlette.responses import JSONResponse
            await JSONResponse({"detail": str(e)}, status_code=404)(scope, receive, send)
            return
        # The agent's own app routes relative to root_path, so extend it with /{agent_id}.
        await sub(dict(scope, root_path=f"{root}/{agent_id}"), receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.pool.open()
    await store.setup()
    yield
    await store.pool.close()


app = FastAPI(title="ChangeBlast agent host (A2A)", lifespan=lifespan)


@app.get("/agents")
def list_agents():
    return [{"agent_id": aid, "card": pb_to_dict(card)} for aid, (card, _) in sorted(_agents.items())]


@app.get("/agents/{agent_id}/card")
def agent_card_json(agent_id: str):
    try:
        card, _ = get_agent(agent_id)
    except repos.RepoError as e:
        raise HTTPException(404, str(e)) from None
    return pb_to_dict(card)


app.mount("/agents", AgentDispatcher())
