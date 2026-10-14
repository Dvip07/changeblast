"""Investigation workflow (LangGraph). Checkpointed to Postgres per investigation (thread_id = id).

interpret (LLM) -> discover (grep) -> plan -> agents (A2A fan-out, parallel)
  -> follow_up_plan (grep-routed A2A follow-ups) -> follow_up agents
  -> validate (re-read every citation) -> build_graph -> report (LLM) -> finalize

LLM steps: interpret, the repo agents, report. Everything else is deterministic.
"""
import operator
import re
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from changeblast import a2a_client, repos, router, store

MAX_FOLLOW_UPS = 6
SEED_HITS_PER_REPO = 25
GATE_MAX_HITS_PER_REPO = 60


class State(TypedDict, total=False):
    investigation_id: str
    change_request: str
    repositories: list[str]
    constraints: dict
    intent: dict
    discovery: dict  # repo -> list of {term, path, line, text}
    literal_baseline: dict  # repo -> files a plain grep for the request's own literals finds
    tasks: list[dict]
    results: Annotated[list[dict], operator.add]  # agent results from both rounds (parallel writers)
    follow_up_tasks: list[dict]
    unrouted_questions: list[dict]
    findings: list[dict]
    impact_graph: dict
    report: dict
    warnings: Annotated[list[str], operator.add]


def latest_results(results: list[dict]) -> list[dict]:
    """Last result per task_id: a resumed (retried) task supersedes its failed attempt."""
    latest: dict[str, dict] = {}
    for r in results:
        latest[r["task_id"]] = r
    return list(latest.values())


# --------------------------------------------------------------------------- interpret (LLM)

class Intent(BaseModel):
    summary: str = Field(description="One sentence restating the requested change")
    target_behavior: str
    invariants: list[str] = Field(description="Things that must stay true (compatibility, constraints)")
    search_terms: list[str] = Field(description="8-20 literal strings to grep for: identifiers, domain terms, "
                                                "field names, and equivalent encodings of any numbers "
                                                "(e.g. a day count in hours, seconds, milliseconds, or words)")
    ambiguities: list[str] = Field(description="Product questions a human must answer")


INTERPRET_PROMPT = """You are normalising a change request before a code investigation across these repositories:
{repos}

Change request:
{request}

Constraints: {constraints}

Extract the intent. For search_terms, think about how the concept might be written in code, configs,
SQL, API specs, UI copy, and email templates. Return only what is asked."""


async def interpret(state: State) -> State:
    inv = state["investigation_id"]
    router.current_investigation.set(inv)
    await store.set_status(inv, "INDEXING")
    try:
        # Cheap tier: structured extraction, cached by input.
        intent = (await router.extract(
            Intent, "You prepare code investigations. Reply with JSON matching the schema.",
            INTERPRET_PROMPT.format(repos=", ".join(state["repositories"]), request=state["change_request"],
                                    constraints=state.get("constraints") or {}),
            prompt_version="interpret-v1")).model_dump()
        warnings = []
    except (router.RouterError, router.BudgetExceeded) as e:
        # Degrade, don't fail: fall back to words and numbers from the request itself.
        words = re.findall(r"[A-Za-z_][A-Za-z_]{3,}|\d+", state["change_request"])
        intent = {"summary": state["change_request"], "target_behavior": state["change_request"],
                  "invariants": [], "search_terms": sorted(set(words))[:20], "ambiguities": []}
        warnings = [f"interpret step used fallback terms: {e}"]
    await store.event(inv, "intent", "orchestrator", intent)
    return {"intent": intent, "warnings": warnings}


# --------------------------------------------------------------------------- discover (deterministic)

async def discover(state: State) -> State:
    discovery: dict[str, list] = {}
    for repo in state["repositories"]:
        hits, seen = [], set()
        for term in state["intent"]["search_terms"]:
            if len(term.strip()) < 2:
                continue
            for h in repos.search(repo, term)["hits"]:
                key = (h["path"], h["line"])
                if key not in seen:
                    seen.add(key)
                    hits.append({"term": term, **h})
        discovery[repo] = hits
    # Baseline for comparison: grep only for numbers and identifiers literally written in the request.
    literals = set(re.findall(r"\b\d+\b|\b[a-z]+(?:_[a-z]+)+\b", state["change_request"]))
    baseline = {repo: sorted({h["path"] for t in literals for h in repos.search(repo, t)["hits"]})
                for repo in state["repositories"]}
    await store.event(state["investigation_id"], "discovery", "orchestrator", {
        repo: {"hits": len(h), "files": sorted({x["path"] for x in h})} for repo, h in discovery.items()})
    return {"discovery": discovery, "literal_baseline": {"terms": sorted(literals), "files": baseline}}


# --------------------------------------------------------------------------- plan + fan-out

async def gate_hits(state: State) -> dict[str, dict]:
    """Cheap parallel yes/no check on each keyword hit, used to ORDER seeds, never to hide them.

    Measured on fixture lines (logs/gate_eval.py): Jev and gpt-5-nano both say "no" to about 1 in 4 real
    dependencies (e.g. a 20-day card-reversal limit that only matters after reasoning about a 30-day window).
    So "likely" hits are listed first, and screened-out hits stay visible to the agent in a compact list.
    """
    question = (f"A developer will implement this change: \"{state['intent']['summary']}\". Could this line of code, "
                f"config, or text need to change or be affected by it? Lines that merely mention a related word "
                f"in an unrelated sense are not affected.")
    gated: dict[str, dict] = {}
    for repo, hits in state["discovery"].items():
        hits = hits[:GATE_MAX_HITS_PER_REPO]
        answers = await router.ask_bool_many(question, [f"{repo}/{h['path']}:{h['line']}: {h['text']}" for h in hits],
                                             prompt_version="seed-gate-v1")
        likely = [h for h, a in zip(hits, answers) if a is None or a.answer]  # unanswered counts as likely
        screened = [h for h, a in zip(hits, answers) if a is not None and not a.answer]
        gated[repo] = {"likely": likely, "screened": screened}
    await store.event(state["investigation_id"], "seed_gate", "orchestrator", {
        repo: {"hits": len(g["likely"]) + len(g["screened"]), "likely": len(g["likely"]), "screened": len(g["screened"])}
        for repo, g in gated.items()})
    return gated


async def plan(state: State) -> State:
    router.current_investigation.set(state["investigation_id"])
    await store.set_status(state["investigation_id"], "INVESTIGATING")
    gated = await gate_hits(state)
    tasks = []
    for i, repo in enumerate(state["repositories"], start=1):
        g = gated.get(repo, {"likely": [], "screened": []})
        likely = "\n".join(f"- {h['path']}:{h['line']} {h['text']}" for h in g["likely"][:SEED_HITS_PER_REPO])
        screened = ", ".join(sorted({f"{h['path']}:{h['line']}" for h in g["screened"]})[:20])
        task = (f"Determine what in {repo} must change, or could break, for this change.\n"
                f"Keyword search found these lines; a quick screen rated them likely relevant:\n{likely or '- (none)'}\n"
                + (f"The quick screen rated these unlikely (it misses about 1 in 4 real dependencies, so check any "
                   f"that look suspicious): {screened}\n" if screened else "")
                + "Look beyond these hits for equivalent representations the keyword search could miss.")
        tasks.append({"task_id": f"task_{i}", "repo": repo, "task": task, "context": [], "round": "plan",
                      "from_agent": "orchestrator"})
    for t in tasks:
        await store.event(state["investigation_id"], "task_planned", t["repo"],
                          {"task_id": t["task_id"], "round": "plan",
                           "seed_hits": len(gated.get(t["repo"], {}).get("likely", []))})
    return {"tasks": tasks}


def dispatch_plan(state: State) -> list[Send]:
    return [Send("agent", {**t, "investigation_id": state["investigation_id"],
                           "change_request": state["change_request"]}) for t in state["tasks"]]


async def run_agent(task: dict[str, Any]) -> State:
    """One A2A round-trip to a repository agent. Failure becomes data (missing coverage)."""
    inv, repo = task["investigation_id"], task["repo"]
    try:
        card = await a2a_client.discover(repo)
        await store.event(inv, "a2a_dispatch", repo, {"task_id": task["task_id"], "round": task["round"],
                                                      "from": task["from_agent"], "card": card,
                                                      "question": task.get("question")})
        result = await a2a_client.send_task(repo, {
            "investigation_id": inv, "task_id": task["task_id"], "change_request": task["change_request"],
            "task": task["task"], "context": task.get("context", [])})
    except a2a_client.AgentUnavailable as e:
        result = {"status": "failed", "error": str(e), "repo": repo, "findings": []}
    result.update(task_id=task["task_id"], round=task["round"], from_agent=task["from_agent"],
                  interface=task.get("interface"))
    await store.event(inv, "a2a_result", repo, {
        "task_id": task["task_id"], "round": task["round"], "status": result.get("status"),
        "error": result.get("error"), "findings": len(result.get("findings", [])),
        "tool_calls": result.get("tool_calls"), "trace": result.get("trace", []),
        "questions": result.get("cross_service_questions", [])})
    return {"results": [result]}


# --------------------------------------------------------------------------- follow-ups (grep-routed A2A)

def _interface_token(interface: str) -> str | None:
    """The most specific literal to grep for, e.g. 'billing.refund.approved' or 'refund-eligibility'."""
    body = interface.split(":", 1)[-1].strip()
    body = re.sub(r"\(.*?\)", "", body).strip()
    if body.split(" ")[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        segments = [s for s in body.split(" ", 1)[-1].split("/") if s and not s.startswith("{")]
        return segments[-1] if segments else None
    return body or None


async def follow_up_plan(state: State) -> State:
    inv = state["investigation_id"]
    by_target: dict[tuple[str, str], dict] = {}
    unrouted = []
    for r in latest_results(state["results"]):
        if r.get("round") != "plan" or r.get("status") != "completed":
            continue
        for q in r.get("cross_service_questions", []):
            token = _interface_token(q["interface"])
            targets = []
            if token and len(token) >= 4:
                for repo in state["repositories"]:
                    if repo == r["repo"]:
                        continue
                    hits = repos.search(repo, token)["hits"]
                    if hits:
                        targets.append((repo, hits[:5]))
            if not targets:
                unrouted.append({"from": r["repo"], **q, "note": "no repository in scope references this interface"})
                continue
            for repo, hits in targets:
                key = (repo, token)
                entry = by_target.setdefault(key, {"repo": repo, "interface": q["interface"], "token": token,
                                                   "questions": [], "askers": [], "evidence": hits})
                entry["questions"].append(q["question"])
                entry["askers"].append(r["repo"])

    follow_ups = []
    for n, entry in enumerate(list(by_target.values())[:MAX_FOLLOW_UPS], start=1):
        evidence = "\n".join(f"- {h['path']}:{h['line']} {h['text']}" for h in entry["evidence"])
        task = (f"Follow-up from {', '.join(sorted(set(entry['askers'])))} about interface {entry['interface']}.\n"
                f"Your repository references it here:\n{evidence}\n\nQuestions:\n"
                + "\n".join(f"- {q}" for q in entry["questions"])
                + "\n\nReport how your code consumes this interface, which fields/assumptions it relies on, and "
                  "what breaks or changes under the change request. Cite file/line evidence.")
        t = {"task_id": f"followup_{n}", "repo": entry["repo"], "task": task, "round": "follow_up",
             "from_agent": ",".join(sorted(set(entry["askers"]))), "interface": entry["interface"],
             "question": entry["questions"][0], "context": entry["questions"]}
        follow_ups.append(t)
        await store.event(inv, "a2a_followup_routed", entry["repo"], {
            "task_id": t["task_id"], "from": t["from_agent"], "interface": entry["interface"],
            "routing_evidence": entry["evidence"]})
    return {"follow_up_tasks": follow_ups, "unrouted_questions": unrouted}


def dispatch_follow_ups(state: State):
    if not state.get("follow_up_tasks"):
        return "validate"
    return [Send("follow_up_agent", {**t, "investigation_id": state["investigation_id"],
                                     "change_request": state["change_request"]}) for t in state["follow_up_tasks"]]


# --------------------------------------------------------------------------- validate (deterministic)

async def validate(state: State) -> State:
    await store.set_status(state["investigation_id"], "RECONCILING")
    findings: list[dict] = []
    n = 0
    for r in latest_results(state["results"]):
        if r.get("status") != "completed":
            continue
        for f in r.get("findings", []):
            n += 1
            check = repos.verify_evidence(r["repo"], r["commit"], f["path"], f["line_start"], f["line_end"],
                                          f["evidence_excerpt"])
            if check["status"] == "unverified":
                status = "unverified"
            elif f["basis"] == "inferred":
                status = "inferred"
            else:
                status = "verified"
            line_start = check.get("line_start", f["line_start"])
            line_end = check.get("line_end", f["line_end"])
            findings.append({
                "finding_id": f"f_{n:03d}", "agent_id": f"{r['repo']}-agent", "repository": r["repo"],
                "commit_sha": r["commit"], "path": f["path"], "line_start": line_start, "line_end": line_end,
                "symbol": f.get("symbol") or None, "claim": f["claim"], "evidence_excerpt": f["evidence_excerpt"],
                "evidence_status": status, "verification_note": check.get("note"),
                "impact_type": f["impact_type"], "impact_level": f.get("impact_level", "may_break"),
                "suggested_change": f["suggested_change"],
                "downstream_refs": f.get("downstream_refs", []), "task_id": r["task_id"], "round": r["round"],
                "via_interface": r.get("interface"), "requested_by": r.get("from_agent"),
            })

    # Deduplicate: same file and overlapping lines -> one finding, other reports kept as corroboration.
    merged: list[dict] = []
    for f in findings:
        dup = next((m for m in merged if m["repository"] == f["repository"] and m["path"] == f["path"]
                    and f["line_start"] <= m["line_end"] and m["line_start"] <= f["line_end"]), None)
        if dup:
            dup.setdefault("corroborated_by", []).append({"finding_id": f["finding_id"], "claim": f["claim"],
                                                          "round": f["round"]})
            dup["downstream_refs"] = sorted(set(dup["downstream_refs"]) | set(f["downstream_refs"]))
            rank = {"must_change": 0, "may_break": 1, "reviewed_no_change": 2}
            if rank[f["impact_level"]] < rank[dup["impact_level"]]:
                dup["impact_level"] = f["impact_level"]
            if dup["evidence_status"] != "verified" and f["evidence_status"] == "verified":
                dup.update({k: f[k] for k in ("evidence_status", "line_start", "line_end", "evidence_excerpt")})
        else:
            merged.append(f)
    counts = {s: sum(1 for f in merged if f["evidence_status"] == s) for s in ("verified", "inferred", "unverified")}
    await store.event(state["investigation_id"], "validated", "orchestrator",
                      {"raw": len(findings), "deduplicated": len(merged), **counts})
    return {"findings": merged}


# --------------------------------------------------------------------------- impact graph (deterministic)

def _norm_interface(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s).strip().lower()
    return re.sub(r"\s+", " ", s)


async def build_graph(state: State) -> State:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(id_: str, **attrs):
        nodes.setdefault(id_, {"id": id_, **attrs})

    for repo in state["repositories"]:
        node(f"service:{repo}", kind="service", label=repo)
    for f in state["findings"]:
        if f["evidence_status"] == "unverified" or f["impact_level"] == "reviewed_no_change":
            continue  # never draw an edge we can't back with evidence; checked-and-fine isn't impact
        fid = f"file:{f['repository']}/{f['path']}"
        node(fid, kind="file", label=f["path"], service=f["repository"])
        nodes[fid].setdefault("findings", []).append(f["finding_id"])
        edges.append({"from": f"service:{f['repository']}", "to": fid, "type": "contains",
                      "finding_ids": [f["finding_id"]], "status": f["evidence_status"]})
        refs = set(f["downstream_refs"]) | ({f["via_interface"]} if f.get("via_interface") else set())
        for ref in refs:
            iid = f"interface:{_norm_interface(ref)}"
            node(iid, kind="interface", label=_norm_interface(ref))
            consumer = f["round"] == "follow_up" and f.get("via_interface") and \
                _norm_interface(f["via_interface"]) == _norm_interface(ref)
            edges.append({"from": iid if consumer else fid, "to": fid if consumer else iid,
                          "type": "consumed_by" if consumer else "references",
                          "finding_ids": [f["finding_id"]], "status": f["evidence_status"]})
    services_touched = sorted({n["service"] for n in nodes.values() if n["kind"] == "file"})
    graph = {"nodes": list(nodes.values()), "edges": edges, "services_touched": services_touched}
    await store.event(state["investigation_id"], "graph_built", "orchestrator",
                      {"nodes": len(nodes), "edges": len(edges), "services_touched": services_touched})
    return {"impact_graph": graph}


# --------------------------------------------------------------------------- report (LLM, citations checked)

class Risk(BaseModel):
    title: str
    severity: str = Field(description="high | medium | low")
    category: str = Field(description="breaking_contract | duplicate_refund | stale_copy | missing_tests | "
                                      "data_reporting | behaviour_change | other")
    description: str
    finding_ids: list[str]


class PlanStep(BaseModel):
    order: int
    service: str
    change: str
    depends_on: list[int]
    effort_band: str = Field(description="S | M | L (rough estimate)")
    finding_ids: list[str]


class TestCase(BaseModel):
    name: str
    service: str
    kind: str = Field(description="update_existing | new")
    description: str
    finding_ids: list[str]


class Report(BaseModel):
    summary: str
    confidence: str = Field(description="high | medium | low")
    confidence_rationale: str
    risks: list[Risk]
    plan: list[PlanStep]
    tests: list[TestCase]
    open_questions: list[str]


REPORT_PROMPT = """Write the change-impact report for this investigation.

Change request: {request}
Intent: {intent}

Findings (evidence_status was checked by re-reading the file at the pinned commit; only "verified" is fact,
"inferred" is a hypothesis):
{findings}

Cross-service edges: {edges}
Questions agents asked that no repository in scope could answer: {unrouted}
Agent open questions: {agent_questions}
Missing coverage (agents that failed): {missing}

Rules: every risk, plan step, and test must cite finding_ids from the list above. Recommendations based only
on inferred findings must say so. Order plan steps so producers/contracts change before consumers when needed.
Be concise and concrete."""


async def report(state: State) -> State:
    inv = state["investigation_id"]
    await store.set_status(inv, "REPORTING")
    findings = state["findings"]
    known = {f["finding_id"] for f in findings}
    results = latest_results(state["results"])
    missing = [{"repo": r["repo"], "task_id": r["task_id"], "error": r.get("error")}
               for r in results if r.get("status") != "completed"]
    agent_questions = sorted({q for r in results for q in r.get("open_questions", [])})
    compact = "\n".join(
        f"{f['finding_id']} [{f['evidence_status']}, {f['impact_level']}] {f['repository']}:{f['path']}:"
        f"{f['line_start']}-{f['line_end']} ({f['impact_type']}) {f['claim']} | change: {f['suggested_change']}"
        for f in findings)
    cross = [e for e in state["impact_graph"]["edges"] if e["type"] in ("references", "consumed_by")]
    narrative, warnings = None, []
    router.current_investigation.set(inv)
    try:
        resp = await router.reason(
            parse_as=Report, max_tokens=16000, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": REPORT_PROMPT.format(
                request=state["change_request"], intent=state["intent"], findings=compact or "(none)",
                edges=cross, unrouted=state.get("unrouted_questions", []), agent_questions=agent_questions,
                missing=missing or "none")}])
        narrative = resp.parsed_output.model_dump()
        # Citation check: drop ids that don't exist; flag items left with no evidence as speculative.
        for section in ("risks", "plan", "tests"):
            for item in narrative[section]:
                bad = [i for i in item["finding_ids"] if i not in known]
                item["finding_ids"] = [i for i in item["finding_ids"] if i in known]
                statuses = {f["evidence_status"] for f in findings if f["finding_id"] in item["finding_ids"]}
                item["speculative"] = not item["finding_ids"] or statuses <= {"inferred", "unverified"}
                if bad:
                    item["dropped_citations"] = bad
    except (router.RouterError, router.BudgetExceeded, AttributeError) as e:
        warnings.append(f"report narrative unavailable: {e}")

    status = "partial" if missing else (
        "completed_with_open_questions" if (narrative and narrative["open_questions"]) or agent_questions
        else "completed")
    out = {
        "investigation_id": inv, "status": status, "change_request": state["change_request"],
        "intent": state["intent"],
        "repositories": [{"name": r, "commit": repos.head_sha(r)} for r in state["repositories"]],
        "summary": narrative["summary"] if narrative else "Narrative unavailable; findings below are verified.",
        "narrative": narrative, "findings": findings, "graph": state["impact_graph"],
        "missing_coverage": missing, "unrouted_questions": state.get("unrouted_questions", []),
        "agent_open_questions": agent_questions,
        "stats": {
            "verified": sum(f["evidence_status"] == "verified" for f in findings),
            "inferred": sum(f["evidence_status"] == "inferred" for f in findings),
            "unverified": sum(f["evidence_status"] == "unverified" for f in findings),
            "must_change": sum(f["impact_level"] == "must_change" for f in findings),
            "may_break": sum(f["impact_level"] == "may_break" for f in findings),
            "reviewed_no_change": sum(f["impact_level"] == "reviewed_no_change" for f in findings),
            "services_touched": state["impact_graph"]["services_touched"],
            "a2a_tasks": len(results),
            "tool_calls": sum(r.get("tool_calls") or 0 for r in results),
            "grep_baseline_files": {r: sorted({h["path"] for h in hits}) for r, hits in state["discovery"].items()},
            "literal_baseline": state.get("literal_baseline", {}),
        },
        "warnings": state.get("warnings", []) + warnings,
        "agent_trace": [{k: r.get(k) for k in ("repo", "task_id", "round", "from_agent", "interface", "status",
                                                 "error", "tool_calls", "coverage_note", "trace", "prompt_version")}
                        for r in results],
    }
    return {"report": out}


async def finalize(state: State) -> State:
    rep = dict(state["report"])
    rep["cost"] = await router.usage_summary(state["investigation_id"])  # every tier, every agent
    await store.set_status(state["investigation_id"], "PARTIAL" if rep["status"] == "partial" else "COMPLETED",
                           report=rep)
    return {}


def build(checkpointer):
    g = StateGraph(State)
    g.add_node("interpret", interpret)
    g.add_node("discover", discover)
    g.add_node("plan", plan)
    g.add_node("agent", run_agent)
    g.add_node("follow_up_plan", follow_up_plan)
    g.add_node("follow_up_agent", run_agent)
    g.add_node("validate", validate)
    g.add_node("build_graph", build_graph)
    g.add_node("report", report)
    g.add_node("finalize", finalize)
    g.add_edge(START, "interpret")
    g.add_edge("interpret", "discover")
    g.add_edge("discover", "plan")
    g.add_conditional_edges("plan", dispatch_plan, ["agent"])
    g.add_edge("agent", "follow_up_plan")
    g.add_conditional_edges("follow_up_plan", dispatch_follow_ups, ["follow_up_agent", "validate"])
    g.add_edge("follow_up_agent", "validate")
    g.add_edge("validate", "build_graph")
    g.add_edge("build_graph", "report")
    g.add_edge("report", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
