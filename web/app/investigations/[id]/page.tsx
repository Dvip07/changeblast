"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import { api, STAGES, TERMINAL, type Finding, type InvEvent, type Investigation, type Report } from "../../lib/api";
import { EvidenceInspector, FindingCard } from "./Evidence";
import { ImpactGraph } from "./ImpactGraph";

type Tab = "impact" | "plan" | "trace";

function describe(e: InvEvent): { text: string; cls?: string } {
  const p = e.payload;
  switch (e.type) {
    case "status": return { text: `Status → ${p.status}${p.error ? ` (${p.error})` : ""}`, cls: p.status === "FAILED" ? "fail" : undefined };
    case "intent": return { text: `Interpreted request; ${p.search_terms.length} search terms: ${p.search_terms.slice(0, 8).join(", ")}…` };
    case "discovery": return { text: "Keyword search: " + Object.entries(p).map(([r, v]: [string, any]) => `${r} ${v.hits} hits`).join(" · ") };
    case "task_planned": return { text: `Planned ${p.task_id} for ${e.agent} (${p.seed_hits} seed hits)` };
    case "a2a_dispatch": return { text: `A2A → ${e.agent} agent: ${p.task_id}${p.round === "follow_up" ? ` (follow-up from ${p.from})` : ""}`, cls: "a2a" };
    case "a2a_followup_routed": return { text: `Routed ${p.interface} question from ${p.from} → ${e.agent} (grep found ${p.routing_evidence.length} references)`, cls: "a2a" };
    case "a2a_result": return {
      text: p.status === "completed" ? `${e.agent} agent returned ${p.findings} findings after ${p.tool_calls} MCP tool calls (${p.task_id})`
        : `${e.agent} agent FAILED: ${p.error}`, cls: p.status === "completed" ? "a2a" : "fail" };
    case "validated": return { text: `Verified citations: ${p.verified} verified, ${p.inferred} inferred, ${p.unverified} unverified (${p.raw} raw → ${p.deduplicated} after dedupe)` };
    case "graph_built": return { text: `Impact graph: ${p.nodes} nodes, ${p.edges} edges, services ${p.services_touched.join(", ")}` };
    case "resume": return { text: `Resumed ${p.from ? "from checkpoint at " + p.from.join(", ") : "retrying " + p.retrying.join(", ")}` };
    default: return { text: e.type };
  }
}

export default function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [inv, setInv] = useState<Investigation | null>(null);
  const [events, setEvents] = useState<InvEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [tab, setTab] = useState<Tab>("impact");
  const [node, setNode] = useState<string | null>(null);
  const [finding, setFinding] = useState<Finding | null>(null);
  const [levelFilter, setLevelFilter] = useState<"impact" | "all">("impact");
  const [error, setError] = useState("");

  const poll = useCallback(async () => {
    try {
      const i = await api<Investigation>(`/investigations/${id}`);
      setInv(i);
      setEvents((prev) => prev);
      const last = await api<InvEvent[]>(`/investigations/${id}/events`);
      setEvents(last);
      if (TERMINAL.includes(i.status) && i.status !== "FAILED") setReport(await api<Report>(`/investigations/${id}/report`));
      return TERMINAL.includes(i.status);
    } catch (e) {
      setError((e as Error).message);
      return true;
    }
  }, [id]);

  useEffect(() => {
    let stop = false;
    (async function loop() {
      while (!stop) {
        if (await poll()) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
    })();
    return () => { stop = true; };
  }, [poll]);

  const findings = report?.findings ?? [];
  const nodeFindings = useMemo(() => {
    if (!node || !report) return [];
    const n = report.graph.nodes.find((x) => x.id === node);
    if (n?.kind === "file") return findings.filter((f) => n.findings?.includes(f.finding_id));
    const ids = new Set(report.graph.edges.filter((e) => e.from === node || e.to === node).flatMap((e) => e.finding_ids));
    return findings.filter((f) => ids.has(f.finding_id));
  }, [node, report, findings]);

  function selectNode(n: string) {
    setNode(n);
    const first = report && findingsFor(n)[0];
    setFinding(first ?? null);
  }
  function findingsFor(n: string): Finding[] {
    if (!report) return [];
    const gn = report.graph.nodes.find((x) => x.id === n);
    if (gn?.kind === "file") return findings.filter((f) => gn.findings?.includes(f.finding_id));
    const ids = new Set(report.graph.edges.filter((e) => e.from === n || e.to === n).flatMap((e) => e.finding_ids));
    return findings.filter((f) => ids.has(f.finding_id));
  }

  async function resume() {
    setError("");
    try { await api(`/investigations/${id}/resume`, { method: "POST" }); poll(); location.reload(); }
    catch (e) { setError((e as Error).message); }
  }

  if (!inv) return <section>{error ? <span className="error">{error}</span> : "Loading…"}</section>;
  const stageIdx = STAGES.indexOf(inv.status);
  const running = !TERMINAL.includes(inv.status);
  const visible = levelFilter === "impact" ? findings.filter((f) => f.impact_level !== "reviewed_no_change") : findings;
  const n = report?.narrative;

  return (
    <>
      <section>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div style={{ minWidth: 0 }}>
            <div className="row"><code className="muted">{inv.id}</code> <span className={`pill s-${inv.status}`}>{inv.status}</span>
              {report && <span className={`pill s-${report.status}`}>{report.status.replace(/_/g, " ")}</span>}</div>
            <h1 style={{ marginTop: 6 }}>{inv.change_request}</h1>
          </div>
          <div className="row">
            {(inv.status === "FAILED" || inv.status === "PARTIAL") && <button className="primary" onClick={resume}>Resume</button>}
            {report && <>
              <a href={`/api/investigations/${id}/report`} download={`${id}.json`}><button>JSON</button></a>
              <a href={`/api/investigations/${id}/report?format=md`} download={`${id}.md`}><button>Markdown</button></a>
            </>}
          </div>
        </div>
        <div className="stepper">
          {STAGES.map((s, i) => (
            <span key={s} className={`step${inv.status === "FAILED" || inv.status === "PARTIAL" ? (i < 4 ? " done" : "") : i < stageIdx ? " done" : i === stageIdx ? " current" : ""}`}>{s}</span>
          ))}
          {inv.status === "PARTIAL" && <span className="step" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>PARTIAL</span>}
          {inv.status === "FAILED" && <span className="step" style={{ background: "var(--bad-soft)", color: "var(--bad)" }}>FAILED</span>}
        </div>
        {inv.error && <p className="error">{inv.error}</p>}
        {error && <p className="error">{error}</p>}
        {report && (
          <div className="row" style={{ marginTop: 12 }}>
            <div className="stat"><b>{report.stats.verified}</b><span>verified</span></div>
            <div className="stat"><b>{report.stats.inferred}</b><span>inferred</span></div>
            <div className="stat"><b>{report.stats.unverified}</b><span>unverified (hidden)</span></div>
            <div className="stat"><b>{report.graph.services_touched.length}</b><span>services touched</span></div>
            <div className="stat"><b>{report.stats.a2a_tasks}</b><span>A2A tasks</span></div>
            <div className="stat"><b>{report.stats.tool_calls}</b><span>MCP tool calls</span></div>
          </div>
        )}
      </section>

      {(running || !report) && (
        <section>
          <h2>Live investigation {running && <span className="muted">(updates every 2s)</span>}</h2>
          <ul className="timeline">
            {events.map((e) => { const d = describe(e); return (
              <li key={e.id} className={d.cls}><span className="muted">{new Date(e.created_at).toLocaleTimeString()}</span> {d.text}</li>
            ); })}
          </ul>
        </section>
      )}

      {report && (
        <>
          {report.missing_coverage.length > 0 && (
            <div className="callout">Missing coverage: {report.missing_coverage.map((m) => `${m.repo} (${m.error})`).join("; ")}.
              This report is partial. Use Resume to retry only the failed agents.</div>
          )}
          <div className="row" style={{ borderBottom: "1px solid var(--line)" }}>
            {(["impact", "plan", "trace"] as Tab[]).map((t) => (
              <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
                {t === "impact" ? "Impact map" : t === "plan" ? "Plan, risks & tests" : "Agent trace"}
              </button>
            ))}
          </div>

          {tab === "impact" && (
            <div className="impact">
              <section style={{ maxHeight: 820, overflow: "auto" }}>
                <h2>Summary</h2>
                <p style={{ marginTop: 0 }}>{report.summary}</p>
                {n && <p className="muted">Confidence: <b>{n.confidence}</b>. {n.confidence_rationale}</p>}
                <div className="row" style={{ margin: "10px 0" }}>
                  <h2 style={{ margin: 0 }}>Findings</h2>
                  <button className={`tab${levelFilter === "impact" ? " active" : ""}`} onClick={() => setLevelFilter("impact")}>impact</button>
                  <button className={`tab${levelFilter === "all" ? " active" : ""}`} onClick={() => setLevelFilter("all")}>+ checked, no change</button>
                </div>
                {visible.map((f) => <FindingCard key={f.finding_id} f={f} active={finding?.finding_id === f.finding_id}
                  onClick={() => { setFinding(f); setNode(`file:${f.repository}/${f.path}`); }} />)}
              </section>
              <section style={{ minWidth: 0 }}>
                <h2>Dependency graph</h2>
                <ImpactGraph report={report} selected={node} onSelect={selectNode} />
                {node && nodeFindings.length > 1 && (
                  <div style={{ marginTop: 10 }}>
                    <h2>{nodeFindings.length} findings on this node</h2>
                    {nodeFindings.map((f) => <FindingCard key={f.finding_id} f={f} active={finding?.finding_id === f.finding_id} onClick={() => setFinding(f)} />)}
                  </div>
                )}
                {(() => {
                  const lit = report.stats.literal_baseline;
                  if (!lit?.files) return null;
                  const impacted = [...new Set(findings.filter((f) => f.impact_level !== "reviewed_no_change" && f.evidence_status !== "unverified")
                    .map((f) => `${f.repository}/${f.path}`))];
                  const missed = impacted.filter((p) => { const [r, ...rest] = p.split("/"); return !(lit.files[r] ?? []).includes(rest.join("/")); });
                  return <>
                    <h2 style={{ marginTop: 14 }}>Versus plain keyword search</h2>
                    <p style={{ marginTop: 0, fontSize: 13 }}>
                      Grepping for the request&apos;s own terms ({lit.terms.map((t) => <code key={t}>{t} </code>)}) finds{" "}
                      <b>{impacted.length - missed.length} of {impacted.length}</b> impacted files. It misses:
                    </p>
                    <ul className="mono" style={{ marginTop: 0 }}>{missed.map((m) => <li key={m}>{m}</li>)}</ul>
                  </>;
                })()}
              </section>
              <section style={{ position: "sticky", top: 12 }}>
                <h2>Evidence inspector</h2>
                <EvidenceInspector invId={id} finding={finding} />
              </section>
            </div>
          )}

          {tab === "plan" && n && (
            <div className="grid2">
              <section>
                <h2>Implementation plan</h2>
                <table><tbody>
                  {[...n.plan].sort((a, b) => a.order - b.order).map((s) => (
                    <tr key={s.order}><td><b>{s.order}</b></td><td><b>{s.service}</b> <span className="badge">{s.effort_band}</span>
                      {s.speculative && <span className="pill s-inferred" style={{ marginLeft: 4 }}>speculative</span>}
                      <div>{s.change}</div>
                      <div className="muted">{s.depends_on.length > 0 && `after step ${s.depends_on.join(", ")} · `}evidence: {s.finding_ids.join(", ")}</div></td></tr>
                  ))}
                </tbody></table>
                <p className="muted">Effort bands are rough estimates (S &lt; half a day, M 1-2 days, L more).</p>
              </section>
              <section>
                <h2>Risk register</h2>
                {n.risks.map((r, i) => (
                  <div key={i} className="list-item">
                    <span className={`pill ${r.severity === "high" ? "s-failed" : r.severity === "medium" ? "s-inferred" : "s-verified"}`}>{r.severity}</span>{" "}
                    <span className="badge">{r.category}</span> <b>{r.title}</b>
                    {r.speculative && <span className="pill s-inferred" style={{ marginLeft: 4 }}>speculative</span>}
                    <div style={{ fontSize: 13 }}>{r.description}</div>
                    <div className="muted">evidence: {r.finding_ids.join(", ") || "none"}</div>
                  </div>
                ))}
              </section>
              <section>
                <h2>Test plan</h2>
                {n.tests.map((t, i) => (
                  <div key={i} className="list-item">
                    <span className="badge">{t.kind === "new" ? "new test" : "update existing"}</span> <b>{t.name}</b> <span className="muted">({t.service})</span>
                    <div style={{ fontSize: 13 }}>{t.description}</div>
                    <div className="muted">evidence: {t.finding_ids.join(", ")}</div>
                  </div>
                ))}
                <p className="muted">Proposed only. No tests were generated or run.</p>
              </section>
              <section>
                <h2>Open questions</h2>
                <ul>{n.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
                {report.unrouted_questions.length > 0 && <>
                  <h2>Questions no repository in scope could answer</h2>
                  <ul>{report.unrouted_questions.map((q, i) => <li key={i}><code>{q.interface}</code> (from {q.from}): {q.question}</li>)}</ul>
                </>}
              </section>
            </div>
          )}
          {tab === "plan" && !n && <section className="callout">The report narrative is unavailable ({report.warnings.join("; ")}). Findings are still verified.</section>}

          {tab === "trace" && (
            <div className="grid2">
              <section>
                <h2>Agent runs (A2A tasks)</h2>
                {report.agent_trace.map((a) => (
                  <details key={a.task_id} className="list-item">
                    <summary>
                      <span className={`pill s-${a.status}`}>{a.status}</span> <b>{a.repo}</b> <code className="muted">{a.task_id}</code>{" "}
                      <span className="muted">{a.round === "follow_up" ? `follow-up from ${a.from_agent} re ${a.interface}` : "planned"} · {a.tool_calls ?? 0} tool calls</span>
                    </summary>
                    {a.error && <p className="error">{a.error}</p>}
                    {a.coverage_note && <p style={{ fontSize: 13 }}><b>Coverage:</b> {a.coverage_note}</p>}
                    <ol className="mono" style={{ paddingLeft: 18 }}>
                      {(a.trace ?? []).map((t, i) => <li key={i}>{t.tool}({Object.entries(t.input).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</li>)}
                    </ol>
                    <div className="muted">prompt {a.prompt_version}</div>
                  </details>
                ))}
              </section>
              <section>
                <h2>Event log (append-only)</h2>
                <ul className="timeline" style={{ maxHeight: 900 }}>
                  {events.map((e) => { const d = describe(e); return (
                    <li key={e.id} className={d.cls}><span className="muted">{new Date(e.created_at).toLocaleTimeString()}</span> {d.text}</li>
                  ); })}
                </ul>
              </section>
            </div>
          )}
        </>
      )}
    </>
  );
}
