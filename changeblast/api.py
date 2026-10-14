"""FastAPI service boundary: validation, job/status endpoints, evidence and report export."""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field

from changeblast import graph as workflow, indexer, repos, router, store
from changeblast.export import to_markdown

log = logging.getLogger("changeblast")
graph = None
running: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global graph
    await store.pool.open()
    await store.setup()
    checkpointer = AsyncPostgresSaver(store.pool)
    await checkpointer.setup()
    graph = workflow.build(checkpointer)
    yield
    await store.pool.close()


app = FastAPI(title="ChangeBlast", lifespan=lifespan)


def cfg(inv_id: str) -> dict:
    return {"configurable": {"thread_id": inv_id}}


async def run(inv_id: str, initial: dict | None) -> None:
    """Run (or continue) the graph. Any crash becomes FAILED with a safe message, never a stack trace."""
    router.current_investigation.set(inv_id)
    try:
        await graph.ainvoke(initial, cfg(inv_id))
    except Exception as e:  # noqa: BLE001
        log.exception("investigation %s failed", inv_id)
        await store.set_status(inv_id, "FAILED", error=f"{type(e).__name__} during investigation; "
                                                       f"POST /resume to continue from the last checkpoint")


def start(inv_id: str, initial: dict | None) -> None:
    task = asyncio.create_task(run(inv_id, initial))
    running.add(task)
    task.add_done_callback(running.discard)


class Constraints(BaseModel):
    backward_compatible: bool = True
    read_only: bool = True


class NewInvestigation(BaseModel):
    change_request: str = Field(min_length=10, max_length=2000)
    repositories: list[str] = Field(min_length=1, max_length=8)
    constraints: Constraints = Constraints()


@app.get("/api/repositories")
def list_repositories():
    return [{"name": r, "commit": repos.head_sha(r), "files": len(repos.list_files(r))}
            for r in repos.available_repos()]


indexing: dict[str, asyncio.Task] = {}


@app.get("/api/repositories/{repo}/index")
async def get_repo_index(repo: str):
    if repo not in repos.available_repos():
        raise HTTPException(404, "repository not in allowlist")
    idx = await indexer.get_index(repo)
    if idx:
        return idx
    if repo in indexing and not indexing[repo].done():
        return {"repo": repo, "status": "indexing"}
    raise HTTPException(404, "not indexed yet; POST to this URL to build the index")


@app.post("/api/repositories/{repo}/index", status_code=202)
async def build_repo_index(repo: str, force: bool = False):
    if repo not in repos.available_repos():
        raise HTTPException(404, "repository not in allowlist")
    if repo not in indexing or indexing[repo].done():
        indexing[repo] = asyncio.create_task(indexer.ensure_index(repo, force=force))
    return {"repo": repo, "status": "indexing"}


@app.post("/api/investigations", status_code=202)
async def create_investigation(body: NewInvestigation, idempotency_key: str | None = Header(default=None)):
    allowed = set(repos.available_repos())
    unknown = [r for r in body.repositories if r not in allowed]
    if unknown:
        raise HTTPException(400, f"repositories not in allowlist: {unknown}")
    if not body.constraints.read_only:
        raise HTTPException(400, "only read-only investigations are supported")
    try:
        await router.check_budget()
    except router.BudgetExceeded as e:
        raise HTTPException(429, str(e)) from None
    inv_id = f"inv_{uuid.uuid4().hex[:10]}"
    inv_id, created = await store.create(inv_id, body.change_request, body.repositories,
                                         body.constraints.model_dump(), idempotency_key)
    if created:
        start(inv_id, {"investigation_id": inv_id, "change_request": body.change_request,
                       "repositories": body.repositories, "constraints": body.constraints.model_dump()})
    inv = await store.get(inv_id)
    return {"investigation_id": inv_id, "status": inv["status"], "created": created,
            "commits": {r: repos.head_sha(r) for r in body.repositories}}


@app.get("/api/usage")
async def usage_today():
    return await router.usage_summary()


@app.get("/api/investigations")
async def list_investigations():
    return await store.recent()


async def _get(inv_id: str) -> dict:
    inv = await store.get(inv_id)
    if not inv:
        raise HTTPException(404, "unknown investigation")
    return inv


@app.get("/api/investigations/{inv_id}")
async def get_investigation(inv_id: str):
    inv = await _get(inv_id)
    snap = await graph.aget_state(cfg(inv_id))
    inv["checkpoint_next"] = list(snap.next) if snap else []
    inv.pop("report")
    return inv


@app.get("/api/investigations/{inv_id}/events")
async def get_events(inv_id: str, after: int = 0):
    await _get(inv_id)
    return await store.events(inv_id, after)


@app.get("/api/investigations/{inv_id}/report")
async def get_report(inv_id: str, format: str = "json"):
    inv = await _get(inv_id)
    if not inv["report"]:
        raise HTTPException(409, f"report not ready (status {inv['status']})")
    if format == "md":
        return PlainTextResponse(to_markdown(inv["report"]), media_type="text/markdown")
    return inv["report"]


@app.get("/api/investigations/{inv_id}/graph")
async def get_graph(inv_id: str):
    inv = await _get(inv_id)
    if not inv["report"]:
        raise HTTPException(409, f"graph not ready (status {inv['status']})")
    return inv["report"]["graph"]


@app.get("/api/investigations/{inv_id}/evidence/{finding_id}")
async def get_evidence(inv_id: str, finding_id: str, context: int = 4):
    """The cited lines, re-read at the pinned commit, with surrounding context for the inspector."""
    inv = await _get(inv_id)
    f = next((f for f in (inv["report"] or {}).get("findings", []) if f["finding_id"] == finding_id), None)
    if not f:
        raise HTTPException(404, "unknown finding")
    try:
        lines = repos.file_lines(f["repository"], f["path"], f["commit_sha"])
    except repos.RepoError as e:
        raise HTTPException(404, str(e)) from None
    start, end = max(1, f["line_start"] - context), min(len(lines), f["line_end"] + context)
    return {"finding": f, "lines": [{"n": i, "text": lines[i - 1], "cited": f["line_start"] <= i <= f["line_end"]}
                                    for i in range(start, end + 1)]}


@app.post("/api/investigations/{inv_id}/resume", status_code=202)
async def resume(inv_id: str):
    inv = await _get(inv_id)
    snap = await graph.aget_state(cfg(inv_id))
    if snap.next:  # crashed or stopped mid-graph: continue from the last checkpoint
        await store.event(inv_id, "resume", "orchestrator", {"from": list(snap.next)})
        start(inv_id, None)
        return {"investigation_id": inv_id, "resumed_from": list(snap.next)}
    if inv["status"] == "PARTIAL":  # finished with failed agents: retry only those, then re-reconcile
        failed = [t for t in workflow.latest_results(snap.values["results"]) if t.get("status") != "completed"]
        tasks = [t for t in snap.values["tasks"] + snap.values.get("follow_up_tasks", [])
                 if t["task_id"] in {f["task_id"] for f in failed}]
        await store.event(inv_id, "resume", "orchestrator", {"retrying": [t["task_id"] for t in tasks]})
        await store.set_status(inv_id, "INVESTIGATING")

        async def retry():
            results = []
            for t in tasks:
                out = await workflow.run_agent({**t, "investigation_id": inv_id,
                                                "change_request": inv["change_request"]})
                results += out["results"]
            # Write the new results as if the follow-up agents produced them; the graph continues at validate.
            await graph.aupdate_state(cfg(inv_id), {"results": results}, as_node="follow_up_agent")
            await run(inv_id, None)

        task = asyncio.create_task(retry())
        running.add(task)
        task.add_done_callback(running.discard)
        return {"investigation_id": inv_id, "retrying": [t["task_id"] for t in tasks]}
    raise HTTPException(409, f"nothing to resume (status {inv['status']})")
