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
// For the large-repo demo (ERPNext); select erpnext alone when using these.
const ERP_SCENARIOS = [
  { label: "E1. Business-day due dates", text: "Add a 'Business Days' option to Payment Terms so credit_days can skip weekends and holidays when calculating invoice due dates." },
  { label: "E2. Rename credit_days", text: "Rename the credit_days field on Payment Term to credit_period_days." },
];

type Repo = { name: string; display_name: string; commit: string; files: number; kind: "fixture" | "github"; quality: number | null };
type Ingest = { id: string; status: string; full_name: string | null; error: string | null; info: Record<string, any> };
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
  const [ghUrl, setGhUrl] = useState("");
  const [ghToken, setGhToken] = useState("");
  const [ingest, setIngest] = useState<Ingest | null>(null);

  const loadRepos = () => api<Repo[]>("/repositories").then(setRepos).catch((e) => setError(e.message));

  async function addRepo() {
    setError("");
    try {
      const r = await api<{ ingest_id: string }>("/repositories", {
        method: "POST", body: JSON.stringify({ url: ghUrl, token: ghToken || null }),
      });
      setGhToken("");  // drop the token from the page as soon as it's sent
      let st: Ingest;
      do {
        await new Promise((res) => setTimeout(res, 1500));
        st = await api<Ingest>(`/repositories/ingests/${r.ingest_id}`);
        setIngest(st);
      } while (!["ready", "failed"].includes(st.status));
      if (st.status === "ready") {
        await loadRepos();
        setSelected([st.info.name]);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }

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
          {SCENARIOS.map((s) => <button key={s.label} onClick={() => { setText(s.text); setSelected(repos.filter((r) => r.kind === "fixture").map((r) => r.name)); }}>{s.label}</button>)}
        </div>
        {repos.some((r) => r.display_name.endsWith("erpnext")) && (
          <div className="row" style={{ marginTop: 6 }}>
            <span className="muted">ERPNext (large repo):</span>
            {ERP_SCENARIOS.map((s) => <button key={s.label} onClick={() => { setText(s.text); setSelected(repos.filter((r) => r.display_name.endsWith("erpnext")).map((r) => r.name)); }}>{s.label}</button>)}
          </div>
        )}
        <h2 style={{ marginTop: 16 }}>Repositories</h2>
        {repos.map((r) => (
          <label key={r.name} className="row" style={{ marginBottom: 4 }}>
            <input type="checkbox" checked={selected.includes(r.name)} onChange={() => toggle(r.name)} />
            <b>{r.display_name}</b> <code className="muted">@{r.commit.slice(0, 7)}</code>
            <span className="muted">{r.files.toLocaleString()} files</span>
            {r.kind === "github" && <span className="badge">GitHub</span>}
            {r.quality != null && <span className="badge" title="Code-graph quality score">graph {r.quality}/100</span>}
          </label>
        ))}
        <details style={{ marginTop: 10 }}>
          <summary><b>Add a GitHub repository</b> <span className="muted">(cloned at a pinned commit, then indexed)</span></summary>
          <div style={{ marginTop: 8 }}>
            <input style={{ width: "100%", padding: 8, border: "1px solid var(--line)", borderRadius: 7, font: "inherit" }}
              placeholder="https://github.com/owner/repo" value={ghUrl} onChange={(e) => setGhUrl(e.target.value)} />
            <input type="password" autoComplete="off" style={{ width: "100%", marginTop: 6, padding: 8, border: "1px solid var(--line)", borderRadius: 7, font: "inherit" }}
              placeholder="Read-only token (private repos only)" value={ghToken} onChange={(e) => setGhToken(e.target.value)} />
            <p className="muted" style={{ margin: "6px 0" }}>The token is used once to clone and is never stored or logged.
              Code from the repository is sent to Anthropic and OpenRouter models during investigations.</p>
            <div className="row">
              <button onClick={addRepo} disabled={!ghUrl || (!!ingest && !["ready", "failed"].includes(ingest.status))}>Add repository</button>
              {ingest && <span className={`pill s-${ingest.status === "ready" ? "COMPLETED" : ingest.status === "failed" ? "FAILED" : "INDEXING"}`}>
                {ingest.status}{ingest.full_name ? ` · ${ingest.full_name}` : ""}{ingest.info?.sha ? ` @${String(ingest.info.sha).slice(0, 7)}` : ""}
                {ingest.status === "ready" && ingest.info?.quality != null ? ` · graph ${ingest.info.quality}/100` : ""}</span>}
              {ingest?.error && <span className="error">{ingest.error}</span>}
            </div>
          </div>
        </details>
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
