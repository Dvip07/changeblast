"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "./lib/api";

const SCENARIOS = [
  { label: "A. Policy change", text: "Increase refund eligibility from 14 to 30 days. Customers who have consumed over 50% of subscription credits remain ineligible. Preserve existing API compatibility." },
  { label: "B. Breaking event change", text: "Rename the refund_amount_cents field in the RefundApproved event to amount." },
  { label: "C. Customer-facing copy", text: "Update every customer-facing message that describes the refund window so it says 30 days." },
];

type Repo = { name: string; commit: string; files: number; kind: "fixture" | "cloned" };
type Recent = { id: string; change_request: string; status: string; created_at: string };

export default function Setup() {
  const router = useRouter();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState(SCENARIOS[0].text);
  const [compat, setCompat] = useState(true);
  const [recent, setRecent] = useState<Recent[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Repo[]>("/repositories").then((r) => { setRepos(r); setSelected(r.filter((x) => x.kind === "fixture").map((x) => x.name)); }).catch((e) => setError(e.message));
    api<Recent[]>("/investigations").then(setRecent).catch(() => {});
  }, []);

  async function run() {
    setBusy(true);
    setError("");
    try {
      const res = await api<{ investigation_id: string }>("/investigations", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ change_request: text, repositories: selected, constraints: { backward_compatible: compat, read_only: true } }),
      });
      router.push(`/investigations/${res.investigation_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const toggle = (name: string) => setSelected((s) => (s.includes(name) ? s.filter((x) => x !== name) : [...s, name]));

  return (
    <div className="grid2">
      <section>
        <h1>New investigation</h1>
        <p className="muted">Describe the change. ChangeBlast searches the selected repositories, delegates repo-specific
          investigations to independent agents over A2A, and verifies every citation against the pinned commit.</p>
        <h2 style={{ marginTop: 16 }}>Change request</h2>
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ marginTop: 6 }}>
          <span className="muted">Seeded scenarios:</span>
          {SCENARIOS.map((s) => <button key={s.label} onClick={() => setText(s.text)}>{s.label}</button>)}
        </div>
        <h2 style={{ marginTop: 16 }}>Repositories</h2>
        {repos.map((r) => (
          <label key={r.name} className="row" style={{ marginBottom: 4 }}>
            <input type="checkbox" checked={selected.includes(r.name)} onChange={() => toggle(r.name)} />
            <b>{r.name}</b> <code className="muted">@{r.commit.slice(0, 7)}</code>
            <span className="muted">{r.files.toLocaleString()} files</span>
            {r.kind === "cloned" && <span className="badge">GitHub clone</span>}
          </label>
        ))}
        <h2 style={{ marginTop: 16 }}>Constraints</h2>
        <label className="row"><input type="checkbox" checked={compat} onChange={(e) => setCompat(e.target.checked)} /> Backward compatible</label>
        <label className="row"><input type="checkbox" checked disabled /> Read-only (agents cannot modify code)</label>
        <div className="row" style={{ marginTop: 16 }}>
          <button className="primary" onClick={run} disabled={busy || !selected.length || text.length < 10}>
            {busy ? "Starting…" : "Run investigation"}
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </section>
      <section>
        <h2>Recent investigations</h2>
        {recent.length === 0 && <p className="muted">None yet.</p>}
        {recent.map((r) => (
          <Link key={r.id} href={`/investigations/${r.id}`} className="list-item">
            <span className={`pill s-${r.status}`}>{r.status}</span> <code className="muted">{r.id}</code>
            <div>{r.change_request}</div>
            <div className="muted">{new Date(r.created_at).toLocaleString()}</div>
          </Link>
        ))}
      </section>
    </div>
  );
}
