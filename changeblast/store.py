"""Postgres persistence: investigations, and an append-only event log for the live trace."""
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from changeblast.config import DATABASE_URL

pool = AsyncConnectionPool(DATABASE_URL, max_size=10, open=False,
                           kwargs={"autocommit": True, "prepare_threshold": 0})

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,
  change_request TEXT NOT NULL,
  repositories JSONB NOT NULL,
  constraints JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  report JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS investigation_events (
  id BIGSERIAL PRIMARY KEY,
  investigation_id TEXT NOT NULL REFERENCES investigations(id),
  type TEXT NOT NULL,
  agent TEXT,
  payload JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ie_inv ON investigation_events (investigation_id, id);
CREATE TABLE IF NOT EXISTS llm_usage (
  id BIGSERIAL PRIMARY KEY,
  investigation_id TEXT,
  component TEXT NOT NULL,
  tier TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  cache_read_tokens INT NOT NULL DEFAULT 0,
  cache_write_tokens INT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  cache_hit BOOLEAN NOT NULL DEFAULT false,
  fallback_used BOOLEAN NOT NULL DEFAULT false,
  latency_ms INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_usage_day ON llm_usage (created_at);
CREATE INDEX IF NOT EXISTS llm_usage_inv ON llm_usage (investigation_id);
CREATE TABLE IF NOT EXISTS llm_cache (
  key TEXT PRIMARY KEY,
  tier TEXT NOT NULL,
  model TEXT NOT NULL,
  response JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS code_indexes (
  id BIGSERIAL PRIMARY KEY,
  repo TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  status TEXT NOT NULL,
  stats JSONB,
  quality JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE (repo, commit_sha)
);
CREATE TABLE IF NOT EXISTS code_files (
  index_id BIGINT NOT NULL REFERENCES code_indexes(id) ON DELETE CASCADE,
  path TEXT NOT NULL, language TEXT, category TEXT NOT NULL, loc INT, has_error BOOLEAN,
  n_defs INT NOT NULL DEFAULT 0, n_refs INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS code_files_idx ON code_files (index_id, path);
CREATE TABLE IF NOT EXISTS code_symbols (
  index_id BIGINT NOT NULL REFERENCES code_indexes(id) ON DELETE CASCADE,
  path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, line_start INT, line_end INT
);
CREATE INDEX IF NOT EXISTS code_symbols_name ON code_symbols (index_id, name);
CREATE INDEX IF NOT EXISTS code_symbols_path ON code_symbols (index_id, path);
CREATE TABLE IF NOT EXISTS code_edges (
  index_id BIGINT NOT NULL REFERENCES code_indexes(id) ON DELETE CASCADE,
  src_path TEXT NOT NULL, dst_path TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT,
  confidence TEXT NOT NULL, line INT
);
CREATE INDEX IF NOT EXISTS code_edges_dst ON code_edges (index_id, dst_path);
CREATE INDEX IF NOT EXISTS code_edges_src ON code_edges (index_id, src_path);
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'investigation_events is append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS events_immutable ON investigation_events;
CREATE TRIGGER events_immutable BEFORE UPDATE OR DELETE ON investigation_events
FOR EACH ROW EXECUTE FUNCTION events_append_only();
"""


async def setup() -> None:
    async with pool.connection() as conn:
        # The API and every agent process call setup at startup; the advisory lock serializes them.
        await conn.execute("SELECT pg_advisory_lock(7351)")
        try:
            await conn.execute(SCHEMA, prepare=False)  # multi-statement script can't be a prepared statement
        finally:
            await conn.execute("SELECT pg_advisory_unlock(7351)")


async def create(inv_id: str, change_request: str, repositories: list[str], constraints: dict,
                 idempotency_key: str | None) -> tuple[str, bool]:
    """Returns (id, created). With an idempotency key, a retry returns the original investigation."""
    async with pool.connection() as conn:
        if idempotency_key:
            row = await (await conn.execute("SELECT id FROM investigations WHERE idempotency_key = %s",
                                            (idempotency_key,))).fetchone()
            if row:
                return row[0], False
        row = await (await conn.execute(
            "INSERT INTO investigations (id, idempotency_key, change_request, repositories, constraints, status) "
            "VALUES (%s, %s, %s, %s, %s, 'CREATED') ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
            (inv_id, idempotency_key, change_request, Jsonb(repositories), Jsonb(constraints)))).fetchone()
        if row is None:  # lost a race with an identical request
            row = await (await conn.execute("SELECT id FROM investigations WHERE idempotency_key = %s",
                                            (idempotency_key,))).fetchone()
            return row[0], False
        return row[0], True


async def set_status(inv_id: str, status: str, report: dict | None = None, error: str | None = None) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE investigations SET status = %s, report = COALESCE(%s, report), error = %s, updated_at = now() "
            "WHERE id = %s", (status, Jsonb(report) if report is not None else None, error, inv_id))
    await event(inv_id, "status", None, {"status": status, **({"error": error} if error else {})})


async def event(inv_id: str, type_: str, agent: str | None, payload: dict) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO investigation_events (investigation_id, type, agent, payload) VALUES (%s, %s, %s, %s)",
            (inv_id, type_, agent, Jsonb(payload)))


async def get(inv_id: str) -> dict | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT id, change_request, repositories, constraints, status, report, error, created_at, updated_at "
            "FROM investigations WHERE id = %s", (inv_id,))).fetchone()
    if not row:
        return None
    keys = ["id", "change_request", "repositories", "constraints", "status", "report", "error", "created_at",
            "updated_at"]
    out = dict(zip(keys, row))
    out["created_at"], out["updated_at"] = out["created_at"].isoformat(), out["updated_at"].isoformat()
    return out


async def events(inv_id: str, after: int = 0) -> list[dict]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, type, agent, payload, created_at FROM investigation_events "
            "WHERE investigation_id = %s AND id > %s ORDER BY id", (inv_id, after))).fetchall()
    return [{"id": r[0], "type": r[1], "agent": r[2], "payload": r[3], "created_at": r[4].isoformat()}
            for r in rows]


async def recent(limit: int = 20) -> list[dict]:
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, change_request, status, created_at FROM investigations ORDER BY created_at DESC LIMIT %s",
            (limit,))).fetchall()
    return [{"id": r[0], "change_request": r[1], "status": r[2], "created_at": r[3].isoformat()} for r in rows]
