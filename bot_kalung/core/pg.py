"""Postgres (Supabase) backend for the PC-free server ingest.

A drop-in for the query/query_one/execute/cursor surface of `core.db.Database`,
backed by psycopg, so the existing services (Shipments, ActionItems, Containers,
…) write to Postgres unchanged. Server-only — nothing on the desktop imports it,
so psycopg is not bundled into the exe.

The services' SQL is written for SQLite: `?` placeholders and `INSERT OR IGNORE`.
`_translate` rewrites both for Postgres/psycopg (which uses `%s` params, treats
`%` specially, and spells the upsert `ON CONFLICT DO NOTHING`).
"""

from __future__ import annotations

from contextlib import contextmanager


def _translate(sql: str) -> str:
    # psycopg treats % as the parameter marker, so escape any literal % (LIKE)
    # first, then turn the SQLite ? placeholders into %s.
    sql = sql.replace("%", "%%").replace("?", "%s")
    if "INSERT OR IGNORE INTO" in sql:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        sql = sql + " ON CONFLICT DO NOTHING"
    return sql


class _Cursor:
    """Wraps a psycopg cursor so `.execute` translates the SQLite-flavoured SQL."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params=()):
        self._cur.execute(_translate(sql), params)
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()


class PostgresDatabase:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialize(self) -> None:
        """Create the schema, idempotently. Reuses the app's SQLite DDL (single
        source of truth): strip `--` comments (some contain ';'), split into
        statements, and apply each to Postgres. CREATE ... IF NOT EXISTS makes it
        a no-op on an already-provisioned database."""
        from .db import INDEXES, SCHEMA

        def statements(text: str) -> list[str]:
            src = "\n".join(line for line in text.splitlines()
                            if not line.strip().startswith("--"))
            return [s.strip() for s in src.split(";") if "CREATE" in s.upper()]

        with self.connect() as conn:
            for statement in statements(SCHEMA) + statements(INDEXES):
                conn.execute(statement)
            # Lock every table with row-level security so the public REST API
            # (anon/publishable key) exposes nothing until explicit policies are
            # added for the PWA. The server worker connects via direct Postgres,
            # which bypasses RLS, so its ingest writes are unaffected.
            tables = [r["table_name"] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'").fetchall()]
            for table in tables:
                conn.execute(
                    f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            conn.commit()

    # -- the Database surface the services use ------------------------------

    def query(self, sql: str, params=()) -> list:
        with self.connect() as conn:
            return conn.execute(_translate(sql), params).fetchall()

    def query_one(self, sql: str, params=()):
        with self.connect() as conn:
            return conn.execute(_translate(sql), params).fetchone()

    def execute(self, sql: str, params=()) -> None:
        with self.connect() as conn:
            conn.execute(_translate(sql), params)
            conn.commit()

    @contextmanager
    def cursor(self, write: bool = False):
        conn = self.connect()
        try:
            yield _Cursor(conn.cursor())
            if write:
                conn.commit()
        finally:
            conn.close()
