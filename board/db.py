"""Autopilot Board DB layer — WAL SQLite, idempotent migrations, single-writer
thread, seq-guarded atomic COALESCE upsert, gate seeding/state/queries.

Correctness guarantees (see plan Tasks 4-6, design §6/§7):
  * Every connection opens WAL + busy_timeout=5000 + synchronous=NORMAL so
    concurrent writers never hit "database is locked".
  * `_apply` performs a single-statement INSERT ... ON CONFLICT upsert with
    COALESCE(excluded.x, runs.x) so a phase-only report NEVER nulls a populated
    goal/approach/result. seq is monotonic via MAX(runs.seq, excluded.seq); a
    stale (lower-seq) report cannot move state.
  * Phase is monotonic with exemptions: never leaves a TERMINAL phase, never
    regresses PHASE_RANK — EXCEPT when the incoming OR current phase is a
    PAUSE phase (asking-user), where the rank check is skipped so
    implementing → asking-user → implementing works.
  * events.event_id is UNIQUE + INSERT OR IGNORE → duplicate events insert once.
  * gate rows: seed_gates seeds applicable checks at 'pending'; set_gate is
    seq-guarded and the source is board-fixed (board.gate.source_of) — any
    worker-supplied source is ignored.
"""
import sqlite3
import threading
import queue
import time

# Migration list. Index i (0-based) is migration version i+1. Migration 1 is the
# initial schema; future migrations append idempotent ALTER TABLE ADD COLUMN
# scripts. `migrate()` applies any migration whose version exceeds the stored
# schema_version, so re-running is a no-op.
_SCHEMA = [
    # migration 1 — initial schema
    """
    CREATE TABLE IF NOT EXISTS runs(
      run_id TEXT PRIMARY KEY, repo TEXT, issue INTEGER, title TEXT,
      goal TEXT, approach TEXT, result TEXT, phase TEXT, status TEXT,
      machine TEXT, worker TEXT, seq INTEGER DEFAULT 0,
      is_bug_fix INTEGER DEFAULT 0, has_deploy INTEGER DEFAULT 0,
      merge_mode TEXT DEFAULT 'auto',
      validated_evidence TEXT, merge_sha TEXT, main_ci_run TEXT,
      regression_red_test TEXT, regression_green_test TEXT,
      unverified TEXT, filed_issues TEXT,
      started_at REAL, updated_at REAL, pr_url TEXT);
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_id TEXT UNIQUE,
      seq INTEGER, phase TEXT, message TEXT, event_ts REAL, recv_ts REAL);
    CREATE TABLE IF NOT EXISTS gate(
      run_id TEXT, check_name TEXT, state TEXT, source TEXT, detail TEXT,
      seq INTEGER, recv_ts REAL, UNIQUE(run_id, check_name));
    CREATE TABLE IF NOT EXISTS gh_state(
      run_id TEXT PRIMARY KEY, pr_url TEXT, pr_state TEXT, merged INTEGER,
      ci_conclusion TEXT, mergeable INTEGER, mergeable_state TEXT,
      issue_state TEXT, deploy_version TEXT, refreshed_at REAL,
      gh_ok INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS schema_version(version INTEGER);
    """,
    # future migrations append here as idempotent ALTER TABLE ADD COLUMN scripts
]


class Board:
    def __init__(self, path):
        self.path = path
        self._wq = queue.Queue()
        self._init()

    def conn(self):
        c = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=NORMAL")
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        """Create the initial schema (migration 1) on a cold DB, then run any
        pending migrations. Idempotent — CREATE TABLE IF NOT EXISTS + a single
        schema_version row."""
        c = self.conn()
        try:
            c.executescript(_SCHEMA[0])
            if c.execute("SELECT version FROM schema_version").fetchone() is None:
                c.execute("INSERT INTO schema_version(version) VALUES (1)")
            c.commit()
        finally:
            c.close()
        self.migrate()

    def schema_version(self):
        c = self.conn()
        try:
            return c.execute("SELECT version FROM schema_version").fetchone()[0]
        finally:
            c.close()

    def migrate(self):
        """Apply any migration newer than the stored version. Idempotent: when
        the stored version already equals len(_SCHEMA) this is a no-op."""
        c = self.conn()
        try:
            v = c.execute("SELECT version FROM schema_version").fetchone()[0]
            for i in range(v, len(_SCHEMA)):
                c.executescript(_SCHEMA[i])
                c.execute("UPDATE schema_version SET version=?", (i + 1,))
            c.commit()
        finally:
            c.close()
