# ChangeBlast v2: plan

Status: **agreed; building the MVP.** Nothing in this file is built yet unless the "Today" section says so.
Prices are from OpenRouter's public model list on 14 Oct 2026; they change, so the router reads them from config.

## Goals

1. **Real repositories:** point ChangeBlast at a GitHub repo, public or private, instead of only the seeded fixtures.
2. **Large codebases:** CRM/ERP-sized repos (thousands of files, several languages) must work within a sensible time and budget.
3. **Cheap and fast:** route each LLM task to the cheapest model that can do it, run independent calls in parallel, and cache everything that can be cached.
4. **Live on AWS:** the client's AI lead can open a URL, read the README, see the agents and their skills, browse demo reports, and run an investigation on the demo codebase or their own repo.

## Today (built and tested)

- **Fixtures:** five fixture repos, an answer key, and an eval (recall 0.9–1.0, citation validity 1.0).
- **Agents:** one A2A agent per repo, reaching its code only through a per-repo MCP server (list, search, read).
- **Orchestration:** LangGraph with Postgres checkpoints, resume, partial results, and idempotency.
- **Verification:** deterministic re-checking of every citation at a pinned SHA.
- **Models:** every LLM call goes to Claude Opus 5.5.
- **What breaks on large repos:** agents navigate by grep and file reads only. One agent per repo, with a 14-call budget, can't cover a 5,000-file ERP.

## Proposed architecture

```
GitHub URL ─► resolve (GitHub MCP: default branch, head SHA, private?) ─► shallow clone at SHA
          ─► INDEX (deterministic, once per repo@sha, cached)
               tree-sitter parse ─► symbols, imports, references ─► dependency graph (Postgres)
               text index for non-code (SQL, YAML, JSON, templates, OpenAPI)
               embeddings per code chunk (OpenRouter)
               index quality score
          ─► INVESTIGATE
               interpret (cheap model) ─► seeds (lexical + symbol + embedding match)
               ─► blast-radius walk over the graph (reverse deps, bounded hops)
               ─► rank (personalized PageRank) ─► relevance check (bool model, parallel)
               ─► partition by module ─► A2A agents (Claude Sonnet 5.5) with graph-aware MCP tools
               ─► verify citations ─► graph ─► report (Claude Sonnet 5.5)
```

### 1. Repo ingestion (GitHub)

- **GitHub MCP server** (official, `github/github-mcp-server`) gets repo metadata: default branch, head commit SHA, visibility, size, and languages. This is where we check that the repo exists and is small enough before doing any work.
- **Shallow clone at that SHA** (`git clone --depth 1` + checkout) for the actual reading. GitHub's code-search API (used by the GitHub MCP search tools) only covers the default branch, can't pin a commit, and allows about 10 searches a minute. That breaks citation checking and won't sustain about 90 tool calls per investigation.
- **Tokens:** one server-side read-only fine-grained PAT for metadata. For a client's private repo, the client pastes their own read-only token. It's used for the clone, then thrown away; it's never stored or logged.
- **Limits:** a maximum repo size (e.g. 1 GB, 30k files), and vendored or generated folders are skipped (`node_modules`, `vendor`, `dist`, `build`, `.min.js`, lockfiles, migrations data).
- **Retention:** clones are deleted after N days. The index is kept by `repo@sha`.

### 2. Code index (AST + dependency graph)

Uses `tree-sitter-language-pack` (tested: Python, TS/TSX, JS, Java, PHP, Go, C#, Ruby, SQL all parse).

- **`process()`** returns, per file, its imports (source and imported names), its structure (classes, functions and methods with line spans and signatures), and its symbols.
- **Tags queries** (`get_tags_query`, the same `tags.scm` files GitHub code navigation uses) return definitions and references (calls, type usages).
- **Graph tables:** `files`, `symbols` (name, kind, file, span, signature), and `edges` (`imports`, `calls`, `references`, `inherits`, `exposes_route`, `reads_config`).
- **Edge confidence:** every edge carries one of these values.
  - `resolved`: the import path maps to a file in the repo.
  - `name_match`: a reference whose name is unique in the repo.
  - `ambiguous`: the name is defined in several places, so the edge is kept but weighted down.
- **Non-code text index:** a Postgres trigram/full-text index over SQL, configs, templates, and docs. In the fixtures, the hidden dependencies were in exactly these files: the SQL interval and the email copy.
- **Embeddings:** chunks of about 40–80 lines, aligned to function and class boundaries using the parse. Used for meaning-based seeds, e.g. "refund window" matching `REFUND_WINDOW_MS` and "two weeks".

### 3. Index quality score (deterministic)

A 0–100 score per language and for the whole repo, shown in the UI so users know how far to trust the graph:

| Signal | Meaning |
|---|---|
| Parse coverage | Share of source files in supported languages that parsed |
| Parse error rate | Share of files whose tree has ERROR nodes |
| Import resolution | Share of internal imports resolved to a file in the repo |
| Reference resolution | Share of references resolved as `resolved` or `name_match` |
| Unsupported share | Files in languages we can't parse (counted, not hidden) |

Example message: "Graph quality 82/100. Python 94, PHP 71: dynamic includes unresolved. 6% of files unsupported (Twig templates); those are text-indexed only."

### 4. Selecting candidates from the graph

1. **Seeds:** symbols and lines matching the intent terms (exact), plus the top-k embedding matches (semantic), plus text-index hits in non-code files.
2. **Blast-radius walk:** follow reverse edges (who imports, calls, or references this) from the seeds, up to 2–3 hops, weighting each edge by its confidence.
3. **Ranking:** personalized PageRank from the seeds, giving the top N candidate files or symbols.
4. **Relevance gate:** a cheap yes/no model, run in parallel: "Given this change, could this snippet need to change or break?" This removes noise before expensive agents run.
5. **Partitions:** candidates are grouped by module (top-level package or directory, or graph communities in a monorepo). Each partition becomes one A2A agent task. A 5,000-file ERP might produce about 6 partitions of about 20 candidates each, instead of one agent drowning in grep.

### 5. Agent tools (MCP, per partition, read-only)

The existing tools (`search_code`, `read_file`, `list_files`) plus graph tools:

- `find_symbol(name)`: definitions with spans.
- `who_uses(symbol)`: reverse edges, with confidence.
- `dependencies_of(file)`: forward edges.
- `outline(path)`: signatures only, so the agent can see a whole module cheaply.
- `semantic_search(query)`: embedding search.

### 6. Model routing

Routing is **fixed by task type in code**, not decided by an LLM, so it's predictable and testable. A cheap tier escalates one level when its output fails schema validation.

| Tier | Tasks | Model | Price per 1M tokens (in / out) | Called via |
|---|---|---|---|---|
| T0 | Parsing, graph, search, ranking, citation checks | none (deterministic) | $0 | n/a |
| T1, yes/no | Relevance gate; "is this hit about the concept?"; dedupe "same issue?" | `typesafe/jev-router` (as you asked); fallback `openai/gpt-5-nano` | Jev: **variable** (it's a router, listed price -1) / nano $0.05 / $0.40 | OpenRouter, parallel and batched |
| T2, cheap extraction | Interpret request and expand search terms; per-file summaries for `outline` | `deepseek/deepseek-v4.1-flash` or `openai/gpt-oss-120b` | $0.016 / $0.89, or $0.037 / $0.17 | OpenRouter |
| T3, complex reasoning | Partition agents (tool use) and the final report | `claude-sonnet-5-5` | $2 / $10 | Anthropic SDK directly (keeps prompt caching, strict tool schemas, refusal fallbacks) |
| Embeddings | Code chunks and queries | `qwen/qwen3-embedding-8b` (start); compare `voyageai/voyage-code-4` | $0.01, or $0.12 | OpenRouter |

**About Jev:** `typesafe/jev-router` picks its own model and reasoning effort for each request, so its cost and latency aren't fixed. My suggestion is to use it for T1 as you asked, log the real cost of every call (OpenRouter returns usage cost), and compare it with `gpt-5-nano` on the eval. Whichever is cheaper at the same accuracy becomes the default. Both are config values, so switching needs no code change.

**Expected cost per investigation on a large repo:**
- Today: about 90 Opus calls, roughly $2–4.
- Target: a few hundred T1 checks at fractions of a cent each, about 6 Sonnet agents with cached prefixes, and one report. Roughly $0.50–1.50.
- These are estimates; the eval will measure them.

### 7. Parallelism

- One asyncio semaphore per provider, sized to its rate limits (e.g. OpenRouter 20 concurrent, Anthropic 6).
- T1 checks run in batches with `gather`. Agents run in parallel, as they do today.
- Indexing parses files in a process pool, since parsing uses the CPU.

### 8. Caching (cheapest first)

| Layer | Key | Saves |
|---|---|---|
| Repo index | `repo@sha` | Re-parsing; a second investigation on the same commit starts instantly |
| Embeddings | hash of chunk content | Re-embedding unchanged code across commits |
| LLM result cache (T1/T2) | hash(model, prompt version, input) | Repeated relevance checks and summaries |
| Anthropic prompt cache | Stable system prompt + tool list as prefix | About 90% of input cost on agent turns after the first |
| Investigation idempotency | `Idempotency-Key` | Duplicate runs (already built) |

### 9. Live site and access

- **Public, no login:** README, architecture, agent cards and skills, the index quality score of the demo repo, and finished demo reports.
- **Running investigations:** requires an access code you give the AI lead, with a daily cap (e.g. 20 runs and $X per day). Spend is tracked from the providers' usage data, and runs stop when the cap is hit.
- **Demo codebase:** the current fixtures, plus optionally one real open-source CRM/ERP indexed ahead of time.
- **Disclosure before a run:** "Code from this repository is sent to Anthropic and OpenRouter model providers for analysis."

### 10. AWS deployment

- **Option A, recommended for now:** one EC2 instance (t3.large, about $60/month) running docker compose with Postgres, the API, the agents, and the web app. Caddy provides automatic HTTPS. Simple, cheap, and easy to explain.
- **Option B, later:** ECS Fargate + RDS + ALB. More production-shaped, about $150+/month, and more setup time.
- **Domain:** `changeblast.dvip.dev` needs one A record added at Namecheap, by you. Otherwise an automatic `<ip>.sslip.io` HTTPS hostname.
- **Secrets:** API keys go into AWS SSM Parameter Store, not into the image or the repo.

## Build phases (each one ships something usable)

| # | Phase | Result |
|---|---|---|
| 1 | Model router + caches + cost tracking | **Done.** $0.47 per run, down from ~$3; see Phase 1 results |
| 2 | Indexer + quality score on the fixtures and one large open-source repo | The graph exists and is scored |
| 3 | Graph-based candidates + relevance gate + partitioned agents + graph MCP tools | Large repos work |
| 4 | GitHub ingestion (MCP metadata + clone at SHA) | Real repos |
| 5 | Access code, spend cap, public pages | Safe to share |
| 6 | AWS deploy | Live URL |
| 7 | Re-run the eval on fixtures and a large repo, then update the README with the numbers | Proof |

## Phase 1 results (done, 14 Oct 2026)

Router, cost ledger, $15/day cap, result cache, and Anthropic prompt caching are built and tested.

| Scenario A | Before (Opus for everything) | After (routed) |
|---|---|---|
| Cost | about $2–4 | **$0.47** (Sonnet $0.446, Jev $0.026 for 120 checks, DeepSeek $0.001) |
| Time | about 4 min | **116 s** |
| Recall / precision / citation validity | 0.9–1.0 / 0.69 / 1.0 | 0.9 / **0.92** / 1.0 |
| Tool calls | 86–89 | 53 |

- **Prompt caching works:** Sonnet's uncached input was 88 tokens across 32 calls; 74k tokens were read from cache.
- **Cap:** `POST /investigations` returns HTTP 429 once today's spend reaches the limit, and in-flight calls stop.
- **Fallback works:** an invalid primary model falls through to the next one.
- **GPT-5 nano fix:** at default reasoning effort it spent all its tokens thinking and returned nothing. Cheap tiers now send `reasoning.effort = low`.
- **Yes/no gate, labeled test** (`evals/gate/gate_eval.py`, 9 real dependencies + 9 unrelated lines):

  | Model | Dependencies caught | False yes | Cost per call | Latency |
  |---|---|---|---|---|
  | Jev router | 6–7 / 9 | 0–1 / 9 | $0.00011 | 2.3 s |
  | GPT-5 nano | 7 / 9 | 1 / 9 | $0.00016 | 5.9 s |

  Both say "no" to the payments 20-day reversal limit. **Decision:** the gate only orders seeds and never removes them; screened-out lines stay visible to agents. Cheap models miss about 1 in 4 real dependencies.
- **Known gap:** `credits.py` (where the new 50% rule plugs in) is missed in 2 of 3 runs. Agents put the rule in `policy.py` and don't cite the helper it would call.

## Decisions (agreed 14 Oct 2026)

1. **T1 yes/no:** `typesafe/jev-router` as the main model, with `openai/gpt-5-nano` as automatic fallback. Real cost is logged for every call.
2. **Large demo repo:** `frappe/erpnext`, indexed ahead of time.
3. **"Skills" on the site:** the agents' skills from their A2A agent cards.
4. **AWS:** option A (single EC2, docker compose, Caddy). Domain `changeblast.dvip.dev`; Dvip adds the DNS A record.
5. **Spend cap:** $15/day across all providers. Runs are refused once it's reached.
6. **GitHub token:** Dvip creates a read-only fine-grained PAT and puts it in `.env` as `GITHUB_TOKEN`.

## MVP scope

**In:**
1. Model router (Jev → nano fallback, DeepSeek flash for T2, Sonnet 5.5 for T3), cost tracking, $15/day hard stop, LLM result cache, Anthropic prompt caching.
2. Indexer: tree-sitter symbols, imports, references; file-level dependency graph; text index for non-code; quality score.
3. Graph-based candidates: seeds, weighted reverse-dependency walk, parallel T1 relevance gate, partitions by module.
4. Graph MCP tools: `find_symbol`, `who_uses`, `dependencies_of`, `outline`.
5. GitHub ingestion: GitHub MCP metadata, then a clone at the SHA. Private repos via a pasted read-only token that is never stored.
6. An agent host serving one A2A agent per partition, each with its own card and skills.
7. Public pages (README, agent cards and skills, demo reports, quality score); access code for runs.
8. ERPNext pre-indexed, plus one finished demo investigation on it.
9. AWS EC2 deploy, Caddy HTTPS, secrets in SSM.

**Out (later, with reasons):**
- **Embeddings / semantic search:** cheap search-term expansion covered synonyms on the fixtures. Added only if ERPNext runs show misses.
- **Personalized PageRank, graph-community partitions:** the weighted walk and directory modules are simpler and probably enough.
- **Precise call graphs** (type inference, method resolution) and **dynamic ERP hooks** (Frappe `hooks.py` string dispatch): made visible by the quality score and text search, not solved.
- **Full import resolution beyond Python and JS/TS:** other languages get parsing and name-match edges only.
- **An ERPNext answer key:** about a day of careful labeling. MVP numbers come from the fixtures eval.
- GitHub App, webhooks, PR comments, SSO and accounts, ECS/RDS, SSE, patch proposals, test execution, OPA.

## Risks

- **Dynamic code in ERPs** (Odoo/Frappe hooks, string-based dispatch, PHP includes) won't show up as graph edges. The quality score makes this visible, and embeddings and text search partly cover it.
- **Jev's variable pricing and routing** could make costs and results inconsistent between runs. We'll measure before relying on it.
- **Client code leaves their environment** when sent to model providers. The disclosure covers this, and enterprise clients may need a VPC or Bedrock deployment later.
- **Scope:** phases 1–6 are several days of work, not hours. Each phase can be shown on its own.
