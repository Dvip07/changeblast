"use client";

import type { Finding, Report } from "../../lib/api";

const COL_W = 230, BOX_W = 208, BOX_H = 38, ROW_H = 50, TOP = 40, FILES_Y = 190;

function short(path: string) {
  return path.length > 30 ? "…" + path.slice(-29) : path;
}

export function ImpactGraph({ report, selected, onSelect }: {
  report: Report; selected: string | null; onSelect: (nodeId: string) => void;
}) {
  const services = report.repositories.map((r) => r.name);
  const byId = Object.fromEntries(report.findings.map((f) => [f.finding_id, f])) as Record<string, Finding>;
  const files = report.graph.nodes.filter((n) => n.kind === "file");
  const interfaces = report.graph.nodes.filter((n) => n.kind === "interface");
  const width = Math.max(services.length * COL_W + 20, 600);

  const pos: Record<string, { x: number; y: number }> = {};
  services.forEach((s, i) => {
    files.filter((f) => f.service === s).forEach((f, j) => {
      pos[f.id] = { x: 10 + i * COL_W + (COL_W - BOX_W) / 2, y: FILES_Y + j * ROW_H };
    });
  });
  const ifW = Math.min(260, (width - 20) / Math.max(interfaces.length, 1) - 12);
  interfaces.forEach((n, i) => {
    const slot = (width - 20) / interfaces.length;
    pos[n.id] = { x: 10 + i * slot + (slot - ifW) / 2, y: TOP };
  });
  const maxRows = Math.max(1, ...services.map((s) => files.filter((f) => f.service === s).length));
  const height = FILES_Y + maxRows * ROW_H + 20;

  const level = (ids: string[] = []) => {
    const levels = ids.map((i) => byId[i]?.impact_level);
    return levels.includes("must_change") ? "must_change" : "may_break";
  };
  const status = (ids: string[] = []) => {
    const s = ids.map((i) => byId[i]?.evidence_status);
    return s.includes("verified") ? "verified" : "inferred";
  };

  const crossEdges = report.graph.edges.filter((e) => e.type !== "contains");

  return (
    <div className="graph-wrap">
      <svg width={width} height={height} role="img" aria-label="Impact graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="#8a8a84" />
          </marker>
        </defs>
        {interfaces.length > 0 && <text x={10} y={22} fontSize={11} fill="#6a6a64" fontWeight={600}>INTERFACES (events / APIs)</text>}
        {services.map((s, i) => (
          <g key={s}>
            <rect x={10 + i * COL_W + 4} y={FILES_Y - 44} width={COL_W - 8} height={height - FILES_Y + 34} rx={10}
              fill={report.graph.services_touched.includes(s) ? "#f3f5fd" : "#f6f6f3"} stroke="#e2e2dc" />
            <text x={10 + i * COL_W + COL_W / 2} y={FILES_Y - 22} textAnchor="middle" fontSize={12} fontWeight={700} fill="#1b1b19">{s}</text>
            {!files.some((f) => f.service === s) &&
              <text x={10 + i * COL_W + COL_W / 2} y={FILES_Y + 16} textAnchor="middle" fontSize={11} fill="#8a8a84">no impacted files</text>}
          </g>
        ))}
        {crossEdges.map((e, i) => {
          const a = pos[e.from], b = pos[e.to];
          if (!a || !b) return null;
          const fromIsIf = e.from.startsWith("interface:");
          const ax = a.x + (fromIsIf ? ifW : BOX_W) / 2, ay = fromIsIf ? a.y + BOX_H : a.y;
          const bx = b.x + (fromIsIf ? BOX_W : ifW) / 2, by = fromIsIf ? b.y : b.y + BOX_H;
          const hot = selected === e.from || selected === e.to;
          return (
            <path key={i} d={`M${ax},${ay} C${ax},${(ay + by) / 2} ${bx},${(ay + by) / 2} ${bx},${by}`}
              fill="none" stroke={e.type === "consumed_by" ? "#8a3ffc" : "#3550c8"} strokeOpacity={hot ? 0.95 : 0.35}
              strokeWidth={hot ? 2.2 : 1.3} strokeDasharray={e.status === "inferred" ? "5 4" : undefined} markerEnd="url(#arrow)" />
          );
        })}
        {interfaces.map((n) => {
          const p = pos[n.id];
          return (
            <g key={n.id} onClick={() => onSelect(n.id)} style={{ cursor: "pointer" }}>
              <rect x={p.x} y={p.y} width={ifW} height={BOX_H} rx={19} fill={selected === n.id ? "#efe5ff" : "#fff"}
                stroke="#8a3ffc" strokeWidth={selected === n.id ? 2 : 1} />
              <text x={p.x + ifW / 2} y={p.y + 23} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" fill="#4b1fa3">
                {n.label.length > 36 ? n.label.slice(0, 35) + "…" : n.label}
              </text>
              <title>{n.label}</title>
            </g>
          );
        })}
        {files.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          const lv = level(n.findings);
          const inferredOnly = status(n.findings) === "inferred";
          return (
            <g key={n.id} onClick={() => onSelect(n.id)} style={{ cursor: "pointer" }}>
              <rect x={p.x} y={p.y} width={BOX_W} height={BOX_H} rx={7}
                fill={selected === n.id ? "#e9edfb" : lv === "must_change" ? "#fff1ef" : "#fff8ea"}
                stroke={selected === n.id ? "#3550c8" : lv === "must_change" ? "#d9776b" : "#d8a54a"}
                strokeWidth={selected === n.id ? 2 : 1} strokeDasharray={inferredOnly ? "4 3" : undefined} />
              <text x={p.x + 8} y={p.y + 16} fontSize={11} fontFamily="ui-monospace, monospace" fill="#1b1b19">{short(n.label)}</text>
              <text x={p.x + 8} y={p.y + 30} fontSize={10} fill="#6a6a64">
                {lv.replace("_", " ")} · {n.findings?.length} finding{n.findings?.length === 1 ? "" : "s"}
              </text>
              <title>{n.label}</title>
            </g>
          );
        })}
      </svg>
      <div className="row muted" style={{ padding: "6px 10px", borderTop: "1px solid var(--line)" }}>
        <span><b style={{ color: "#d9776b" }}>■</b> must change</span>
        <span><b style={{ color: "#d8a54a" }}>■</b> may break</span>
        <span><b style={{ color: "#3550c8" }}>—</b> references</span>
        <span><b style={{ color: "#8a3ffc" }}>—</b> consumed by (A2A follow-up)</span>
        <span>dashed = inferred only</span>
      </div>
    </div>
  );
}
