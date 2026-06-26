"""Persistence layer. Dual-mode:

  - DATABASE_URL set  → PostgreSQL (Supabase) via psycopg3 + pool   (prod)
  - DATABASE_URL unset → local SQLite file                          (dev)

The SQL in store.py uses `?` placeholders and `"order"` quoting, both of which
work as-is on SQLite; for Postgres the db layer translates `?` → `%s`. The DDL
below is intentionally written to run unchanged on both engines (TEXT/INTEGER/
REAL, IF NOT EXISTS, ON DELETE CASCADE — all portable).
"""
import json
import os
import sqlite3
import threading

DATABASE_URL = os.getenv("DATABASE_URL")
IS_PG = bool(DATABASE_URL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stages (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    "order" INTEGER NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    model TEXT NOT NULL,
    prompt_template TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 16000,
    reasoning_effort TEXT,
    expects_json INTEGER NOT NULL DEFAULT 1,
    web_search INTEGER NOT NULL DEFAULT 0,
    validator_code TEXT,
    input_mapping TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    input_data TEXT,
    status TEXT NOT NULL,
    stop_on_failure INTEGER NOT NULL DEFAULT 1,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    fid INTEGER PRIMARY KEY,
    company_name TEXT NOT NULL,
    company_code TEXT,
    actuals INTEGER NOT NULL DEFAULT 5,
    estimates INTEGER NOT NULL DEFAULT 10,
    input_data TEXT,
    last_run_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL,
    "order" INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    request_payload TEXT,
    raw_response TEXT,
    parsed_json TEXT,
    validator_passed INTEGER,
    validator_report TEXT,
    tokens_prompt INTEGER DEFAULT 0,
    tokens_completion INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    finish_reason TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    model TEXT
);
"""

_STATEMENTS = [s.strip() for s in SCHEMA.split(";") if s.strip()]


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------
if IS_PG:
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    _pool = ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        # prepare_threshold=None → no server-side prepared statements, required
        # for Supabase's PgBouncer transaction pooler (port 6543).
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": None,
        },
        open=True,
    )

    def _conv(sql):
        return sql.replace("?", "%s")

    def init_db():
        with _pool.connection() as conn:
            for stmt in _STATEMENTS:
                conn.execute(stmt)
            for mig in (
                "ALTER TABLE stage_results ADD COLUMN IF NOT EXISTS model TEXT",
                "ALTER TABLE stages ADD COLUMN IF NOT EXISTS "
                "web_search INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(mig)
                except Exception:
                    pass

    def query(sql, params=()):
        with _pool.connection() as conn:
            cur = conn.execute(_conv(sql), tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def execute(sql, params=()):
        with _pool.connection() as conn:
            return conn.execute(_conv(sql), tuple(params))

# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------
else:
    _DB_PATH = os.getenv("PIPELINE_DB", "pipeline.db")
    _local = threading.local()

    def _conn():
        c = getattr(_local, "conn", None)
        if c is None:
            c = sqlite3.connect(_DB_PATH, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            _local.conn = c
        return c

    def init_db():
        c = _conn()
        c.executescript(SCHEMA)
        c.commit()
        for mig in (
            "ALTER TABLE stage_results ADD COLUMN model TEXT",
            "ALTER TABLE stages ADD COLUMN web_search INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                c.execute(mig)
                c.commit()
            except Exception:
                pass  # column already exists

    def query(sql, params=()):
        cur = _conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def execute(sql, params=()):
        c = _conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def jdump(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def jload(v):
    if v is None or v == "":
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return None
