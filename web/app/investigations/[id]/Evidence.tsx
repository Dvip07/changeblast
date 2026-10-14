"use client";

import { useEffect, useState } from "react";
import { api, type EvidenceView, type Finding } from "../../lib/api";

export function FindingCard({ f, active, onClick }: { f: Finding; active?: boolean; onClick?: () => void }) {
  return (
    <div className={`list-item${active ? " selected" : ""}`} onClick={onClick} style={{ cursor: onClick ? "pointer" : undefined }}>
      <div className="row" style={{ gap: 4 }}>
        <span className={`pill s-${f.evidence_status}`}>{f.evidence_status}</span>
        <span className={`pill l-${f.impact_level}`}>{f.impact_level.replace(/_/g, " ")}</span>
        <code className="muted">{f.finding_id}</code>
      </div>
      <div className="mono" style={{ marginTop: 4 }}>{f.repository}/{f.path}:{f.line_start}{f.line_end !== f.line_start ? `-${f.line_end}` : ""}</div>
      <div style={{ fontSize: 13 }}>{f.claim}</div>
    </div>
  );
}

export function EvidenceInspector({ invId, finding }: { invId: string; finding: Finding | null }) {
  const [view, setView] = useState<EvidenceView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setView(null);
    setError("");
    if (finding) api<EvidenceView>(`/investigations/${invId}/evidence/${finding.finding_id}`).then(setView).catch((e) => setError(e.message));
  }, [invId, finding]);

  if (!finding) return <p className="muted">Select a node in the graph or a finding to see the source evidence.</p>;
  const f = finding;
  return (
    <div>
      <div className="row" style={{ gap: 4 }}>
        <span className={`pill s-${f.evidence_status}`}>{f.evidence_status}</span>
        <span className={`pill l-${f.impact_level}`}>{f.impact_level.replace(/_/g, " ")}</span>
        <span className="badge">{f.impact_type}</span>
        <code className="muted">{f.finding_id}</code>
      </div>
      <h3 style={{ marginTop: 8 }} className="mono">{f.repository}/{f.path}</h3>
      <div className="muted">
        lines {f.line_start}-{f.line_end} @ <code>{f.commit_sha.slice(0, 7)}</code>
        {f.symbol && <> · symbol <code>{f.symbol}</code></>} · reported by {f.agent_id}
        {f.round === "follow_up" && <> (A2A follow-up from {f.requested_by} about <code>{f.via_interface}</code>)</>}
      </div>
      {f.verification_note && <p className="callout" style={{ marginTop: 8 }}>Verifier: {f.verification_note}</p>}
      <div className="code" style={{ margin: "10px 0" }}>
        {error && <div className="error" style={{ padding: 8 }}>{error}</div>}
        {!view && !error && <div className="muted" style={{ padding: 8 }}>Re-reading file at pinned commit…</div>}
        {view?.lines.map((l) => (
          <div key={l.n} className={`ln${l.cited ? " cited" : ""}`}><span className="n">{l.n}</span><span>{l.text}</span></div>
        ))}
      </div>
      <h2>Why it is affected</h2>
      <p style={{ marginTop: 0 }}>{f.claim}</p>
      <h2>Suggested change</h2>
      <p style={{ marginTop: 0 }}>{f.suggested_change}</p>
      {f.downstream_refs.length > 0 && <>
        <h2>Crosses boundary via</h2>
        <div className="row">{f.downstream_refs.map((r) => <code key={r} className="badge">{r}</code>)}</div>
      </>}
      {f.corroborated_by && f.corroborated_by.length > 0 && <>
        <h2 style={{ marginTop: 12 }}>Also reported by</h2>
        {f.corroborated_by.map((c) => <p key={c.finding_id} className="muted" style={{ margin: "0 0 6px" }}><code>{c.finding_id}</code> ({c.round}): {c.claim}</p>)}
      </>}
    </div>
  );
}
