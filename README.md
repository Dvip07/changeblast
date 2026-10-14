# ChangeBlast

**Cross-repository change impact investigator.** Give it a change request in plain English and a set of repositories. It works out how the change spreads across code, API contracts, events, SQL, UI, and customer-facing copy. It returns an impact map where every claim cites a file and line range at a pinned commit, plus an implementation plan, a risk register, and a regression-test plan.

> "Increase refund eligibility from 14 to 30 days" looks like a one-line change. Grepping for `14` finds 3 of the 10 files that actually need attention. ChangeBlast finds the rest: `1_209_600_000` ms in a React component, `INTERVAL '336 hours'` in a finance report, "within two weeks" in an email template, and a 20-day card-reversal limit in another team's payments service that quietly changes how refunds on days 21–30 are paid out.

## What's real in this repo

| Claim | Status |
|---|---|
| Repository agents run as **separate A2A services** (one process and port per repo), discovered by agent card | Built (`a2a-sdk` 1.2, protocol 1.0, JSON-RPC) |
| Agents reach code only through an **MCP server** locked to their own repo (plus read-only shared contracts) | Built (`mcp` 2.3, stdio) |
| **LangGraph** orchestration, parallel fan-out, **Postgres checkpoints**, resume after a crash, retry of failed agents only | Built |
| Every citation **re-checked** against the file at the pinned commit before it can be shown as `verified` | Built (deterministic, no LLM) |
| Cross-agent follow-ups **routed by grep**, not by guessing | Built |
| Answer key and scoring for precision, recall, and citation validity | Built (`evals/`) |
| Read-only: no writes, no shell, no secret files, repo allowlist | Built |
| OPA, GitHub integration, tree-sitter indexing, running generated tests | Not built (see Roadmap) |

## Architecture

```
Next.js UI (:3100) ── /api ──► FastAPI (:8100) ──► LangGraph orchestrator ──► Postgres (:5433)
                                                     │                          checkpoints, investigations,
                                                     │                          append-only event log
   interpret (LLM) ─► discover (grep) ─► plan ─► fan-out ──A2A──►  billing-service agent       :9300
                                                     │      ──A2A──►  payments-service agent      :9301
                                                     │      ──A2A──►  notification-service agent  :9302
                                                     │      ──A2A──►  checkout-web agent          :9303
                                                     │      ──A2A──►  shared-contracts agent      :9304
                                                     │                    │
                                                     │                    └─ each agent: Claude tool loop
                                                     │                       over its own MCP server
                                                     │                       (list_files, search_code,
                                                     │                        read_file, read_contract)
   follow_up_plan (grep-routed A2A follow-ups) ─► validate (re-read citations) ─► build_graph ─► report (LLM) ─► finalize
```

**Where the AI is used:** interpreting the request (search terms, including equivalent encodings of numbers), each repository agent's investigation, and writing the final report.

**Where it is not (deterministic):** repository access, search, routing follow-ups to the right agent, citation verification, deduplication, the dependency graph, citation checks on the report, idempotency, and status transitions.

### How a cross-service link is found

1. The billing agent reads `service.py` and sees it publishes `billing.refund.approved`. It can't read other repos, so it returns a `cross_service_question` about `event:billing.refund.approved`.
2. The orchestrator greps every other repo for `billing.refund.approved`. Only payments and notification reference it, so only their agents get an A2A follow-up. The matching lines are recorded as routing evidence.
3. The payments agent investigates its consumer and processor and reports the 20-day `CARD_REVERSAL_MAX_AGE` with file and line evidence.
4. The verifier re-reads `processor.py` at commit `1cee2aa` and confirms the excerpt. The graph draws `event → payments/processor.py` as a `consumed_by` edge.

### Evidence statuses

- **verified:** the agent read the code (`basis: observed`) and the verifier found the excerpt in the cited lines. If the excerpt is in the same file at different lines, the verifier corrects the range and leaves a note.
- **inferred:** the agent marked it as a hypothesis. It's shown, but labeled.
- **unverified:** the excerpt wasn't found at the pinned commit. It's hidden from the graph and never presented as fact.

Separately, each finding has an **impact level**: `must_change`, `may_break` (verify it or add a regression test), or `reviewed_no_change`. The last one records that an agent checked a location and found it unaffected. It counts as coverage, not impact, and isn't drawn in the graph.

## Run it

Requires Docker, Python 3.12+, Node 20+, and an Anthropic API key.

```bash
make setup                 # builds repos/ from fixtures/, venv, npm install, .env from .env.example
# put ANTHROPIC_API_KEY in .env
make infra                 # Postgres on :5433
make agents                # five A2A repo agents on :9300-9304 (logs in logs/)
make api                   # orchestrator API on :8100
make web                   # UI on :3100
```

Open http://localhost:3100, pick a seeded scenario, and run it. An investigation takes about 2–4 minutes: 5 planned agent tasks plus up to 6 follow-ups, each agent using roughly 5–12 tool calls.

`scripts/build_repos.sh` turns each `fixtures/<name>` into its own git repo with a fixed author and timestamp, so commit SHAs are identical on every machine.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/repositories` | Allowlisted repos with pinned commit |
| POST | `/api/investigations` | `202 Accepted`. Optional `Idempotency-Key` header: a retry returns the same investigation |
| GET | `/api/investigations/{id}` | Status (`CREATED → INDEXING → INVESTIGATING → RECONCILING → REPORTING → COMPLETED / PARTIAL / FAILED`) and checkpoint position |
| GET | `/api/investigations/{id}/events?after=` | Append-only event log (poll for the live view) |
| GET | `/api/investigations/{id}/report[?format=md]` | Machine-readable JSON or Markdown |
| GET | `/api/investigations/{id}/graph` | Impact graph |
| GET | `/api/investigations/{id}/evidence/{finding_id}` | Cited lines re-read at the pinned commit, with context |
| POST | `/api/investigations/{id}/resume` | Continue from the checkpoint after a crash, or retry only the failed agents |

## Evaluation

`evals/answer_key.json` lists the required, optional, and distractor files for each scenario. It's outside the repos the agents can read. Run `make eval` (or `python -m evals.run_eval A B C`).

Measured on 14 Oct 2026, Claude Opus 5.5, one run per scenario (two for A):

| Scenario | Recall | Precision | Citation validity | Literal grep recall | LLM-expanded grep recall | Tool calls |
|---|---|---|---|---|---|---|
| A. 14 → 30 days + 50% credits (run 1) | **1.00** | 0.68 | 1.00 | 0.30 | 0.60 | 89 |
| A. same request (run 2) | 0.90 | 0.69 | 1.00 | 0.30 | 1.00 | 86 |
| B. Rename `refund_amount_cents` | **1.00** | 0.86 | 1.00 | 1.00 | 1.00 | 79 |
| C. Customer-facing copy only | **1.00** | 0.36 | 1.00 | 0.00 | 1.00 | 86 |

What the numbers show:

- **A (the main demo):** grep for `14`, `30`, `50` finds 3 of the 10 files that need attention. The agents find 9–10, including the payments reversal limit, which no keyword points to.
- **B:** a field rename is easy for grep. Here the value is the contract analysis: `additionalProperties: false`, the `/v2` dual-publish rule, and which consumers break.
- **C:** the request contains no useful literal ("30" matches nothing), so grep recall is 0. Recall is 1.0, but precision is poor: the agents widened a copy-only request into the policy logic and flagged the payments processor (a distractor). The fix is better scoping in the agent prompt, measured against this answer key.

- **Recall** is the share of required files reported as impacted. **Precision** counts required and optional files as relevant and everything else against.
- **Citation validity** is the share of findings whose excerpt was found again at the pinned commit.
- **Literal grep** searches only for the numbers and identifiers written in the request. **LLM-expanded grep** also uses the interpret step's search terms.

**Known weaknesses:**
- **Precision is the weak spot.** Agents sometimes mark consumers of an unchanged event as `may_break` "in case the schema changes."
- **Recall varies between runs:** 0.9 to 1.0 on scenario A, with `credits.py` sometimes missed.
- **Cost:** each investigation uses about 80–90 tool calls (before model routing). See docs/PLAN.md for routed numbers: about $0.47 and 2 minutes per run.

## Security and reliability

- **Bounded access:** repo allowlist, reads through `git show <sha>:<path>`, path traversal and secret-file patterns rejected, no shell, no writes. An agent's MCP server is launched for one repo, so it can't read another team's code; it has to ask that team's agent.
- **Untrusted content:** repository text is treated as untrusted data, and the agent prompt says never to follow instructions found in code or comments.
- **Budgets:** agents are capped at 14 tool calls and 180 s. Each investigation has at most 6 follow-ups.
- **Failures surface as failures:** a failed agent produces a `PARTIAL` report that lists the missing coverage. A crash produces `FAILED` with a safe message, and `/resume` continues from the last checkpoint. The UI never shows a failed run as green.
- **Audit trail:** the event log is append-only, enforced by a Postgres trigger. Every agent run records its prompt version and its full tool-call trace.

## Roadmap

- **OPA,** only once there is a real policy decision to make, such as who may read which repos, or approval before proposing patches.
- **Git provider integration** with least-privilege read scopes. tree-sitter symbol indexing to replace line-level grep.
- **Contract compatibility checks** (JSON Schema diff), and generated tests run in a sandbox.
- **Human approval** before any patch proposal (LangGraph `interrupt()`).
- **SSE** instead of polling. A Postgres task store for A2A agents.

## Interview notes

- **Why A2A?** Each repo specialist is independently addressable and limited to its own repository. The billing agent can ask the payments agent about a consumer without having access to payments code. In a monorepo I'd use one orchestrator with ordinary function calls.
- **Why MCP?** It gives a small, reusable tool surface with the access boundary enforced in the tool server rather than in the prompt.
- **Why LangGraph?** Investigations fan out, wait on other agents, and need retries and resume. A checkpoint after every step means a crash in `validate` resumes there without paying for 11 agent runs again.
- **What if the LLM makes up a dependency?** It can't be shown as verified: the verifier re-reads the cited lines at the pinned commit, and graph edges are only drawn from findings that pass.
- **What if an agent fails?** You get a partial report that names the missing coverage, and `/resume` re-runs only the failed agents.
