export type EvidenceStatus = "verified" | "inferred" | "unverified";
export type ImpactLevel = "must_change" | "may_break" | "reviewed_no_change";

export type Finding = {
  finding_id: string;
  agent_id: string;
  repository: string;
  commit_sha: string;
  path: string;
  line_start: number;
  line_end: number;
  symbol: string | null;
  claim: string;
  evidence_excerpt: string;
  evidence_status: EvidenceStatus;
  verification_note: string | null;
  impact_type: string;
  impact_level: ImpactLevel;
  suggested_change: string;
  downstream_refs: string[];
  round: "plan" | "follow_up";
  via_interface: string | null;
  requested_by: string | null;
  corroborated_by?: { finding_id: string; claim: string; round: string }[];
};

export type GraphNode = { id: string; kind: "service" | "file" | "interface"; label: string; service?: string; findings?: string[] };
export type GraphEdge = { from: string; to: string; type: "contains" | "references" | "consumed_by"; finding_ids: string[]; status: EvidenceStatus };

type Cited = { finding_ids: string[]; speculative?: boolean; dropped_citations?: string[] };
export type Narrative = {
  summary: string;
  confidence: string;
  confidence_rationale: string;
  risks: (Cited & { title: string; severity: string; category: string; description: string })[];
  plan: (Cited & { order: number; service: string; change: string; depends_on: number[]; effort_band: string })[];
  tests: (Cited & { name: string; service: string; kind: string; description: string })[];
  open_questions: string[];
};

export type TraceStep = { tool: string; input: Record<string, unknown>; chars: number };
export type AgentRun = {
  repo: string; agent_id?: string; scope?: string; task_id: string; round: string; from_agent: string; interface: string | null;
  status: string; error: string | null; tool_calls: number; coverage_note: string; trace: TraceStep[];
  prompt_version: string;
};

export type Report = {
  investigation_id: string;
  status: string;
  change_request: string;
  intent: { summary: string; search_terms: string[]; invariants: string[]; ambiguities: string[] };
  repositories: { name: string; commit: string }[];
  summary: string;
  narrative: Narrative | null;
  findings: Finding[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[]; services_touched: string[] };
  missing_coverage: { repo: string; task_id: string; error: string }[];
  unrouted_questions: { from: string; interface: string; question: string; note: string }[];
  agent_open_questions: string[];
  stats: Record<string, number | string[] | Record<string, string[]>> & {
    verified: number; inferred: number; unverified: number; tool_calls: number; a2a_tasks: number;
    grep_baseline_files: Record<string, string[]>;
    literal_baseline?: { terms: string[]; files: Record<string, string[]> };
  };
  warnings: string[];
  agent_trace: AgentRun[];
  exact_references?: Record<string, { occurrences: { repo: string; path: string; line: number; text: string }[]; files: number; truncated: boolean }>;
};

export type Investigation = {
  id: string; change_request: string; repositories: string[]; status: string; error: string | null;
  created_at: string; updated_at: string; checkpoint_next: string[];
};

export type InvEvent = { id: number; type: string; agent: string | null; payload: Record<string, any>; created_at: string };
export type EvidenceView = { finding: Finding; lines: { n: number; text: string; cited: boolean }[] };

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : res.statusText);
  }
  return res.json();
}

export const TERMINAL = ["COMPLETED", "PARTIAL", "FAILED"];
export const STAGES = ["CREATED", "INDEXING", "INVESTIGATING", "RECONCILING", "REPORTING", "COMPLETED"];
// RESUMING is transient: the next graph node sets the real stage within seconds.
