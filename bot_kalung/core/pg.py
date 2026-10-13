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

import re
from contextlib import contextmanager

_ROWID_RE = re.compile(r"\browid\b")


def _translate(sql: str) -> str:
    # psycopg treats % as the parameter marker, so escape any literal % (LIKE)
    # first, then turn the SQLite ? placeholders into %s.
    sql = sql.replace("%", "%%").replace("?", "%s")
    # SQLite's `rowid` pseudo-column is used only as an insertion-order tiebreaker
    # (ORDER BY <ts> DESC, rowid DESC) on append-mostly tables; ctid approximates
    # it in Postgres.
    sql = _ROWID_RE.sub("ctid", sql)
    if "INSERT OR IGNORE INTO" in sql:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        sql = sql + " ON CONFLICT DO NOTHING"
    return sql


def statements(text: str) -> list[str]:
    """The CREATE statements of a SQL script. Comments go first — whole-line AND
    trailing ones: a ";" inside a trailing comment ("-- set by …; the worker …")
    would split a CREATE TABLE in half and stop the worker from starting."""
    src = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return [s.strip() for s in src.split(";") if "CREATE" in s.upper()]


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
        self._pool = None

    @property
    def pool(self):
        """Lazily-opened connection pool. Reusing warm connections avoids a
        TCP+TLS handshake per query over the Supabase pooler — a poll cycle fires
        many small queries, so this cuts real latency and connection churn. The
        worker is single-threaded, so a tiny pool (1 warm, up to 4) is plenty."""
        if self._pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                self.dsn, min_size=1, max_size=4,
                kwargs={"row_factory": dict_row}, open=True)
        return self._pool

    def connect(self):
        """A standalone connection (used by initialize(); pooling covers the
        hot query path)."""
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialize(self) -> None:
        """Create the schema, idempotently. Reuses the app's SQLite DDL (single
        source of truth): strip `--` comments (some contain ';'), split into
        statements, and apply each to Postgres. CREATE ... IF NOT EXISTS makes it
        a no-op on an already-provisioned database."""
        from .db import INDEXES, SCHEMA, _expected_columns

        with self.connect() as conn:
            for statement in statements(SCHEMA):
                conn.execute(statement)
            # CREATE ... IF NOT EXISTS never adds a column to an existing table, so
            # mirror SQLite's _add_missing_columns: add any schema column the live
            # table lacks (additive only, type only — like SQLite). Before the
            # indexes, which may reference a new column.
            have = {(r["table_name"], r["column_name"]) for r in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public'").fetchall()}
            for table, columns in _expected_columns().items():
                for name, declaration in columns.items():
                    if (table, name) not in have:
                        conn.execute(f'ALTER TABLE public."{table}" ADD COLUMN '
                                     f'IF NOT EXISTS {name} {declaration.split()[0]}')
            for statement in statements(INDEXES):
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
        with self.pool.connection() as conn:
            return conn.execute(_translate(sql), params).fetchall()

    def query_one(self, sql: str, params=()):
        with self.pool.connection() as conn:
            return conn.execute(_translate(sql), params).fetchone()

    def execute(self, sql: str, params=()) -> None:
        with self.pool.connection() as conn:
            conn.execute(_translate(sql), params)
            conn.commit()

    @contextmanager
    def cursor(self, write: bool = False):
        with self.pool.connection() as conn:
            yield _Cursor(conn.cursor())
            if write:
                conn.commit()

    def close(self) -> None:
        """Close the pool (the long-running worker never calls this, but tests and
        one-shot runs can)."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
