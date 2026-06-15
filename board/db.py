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
        c = sqlite3.connect(self.path, timeout=5, check_same_thread=True)
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

    # ----- single-writer thread ---------------------------------------------
    # The server (Task 11/12) routes HTTP-thread + gh-poller writes through this
    # one queue so only one connection ever writes — eliminating writer
    # contention. Tests call apply_event() directly (synchronous). BOTH paths
    # converge on _apply, so the correctness logic is identical.

    def start_writer(self):
        t = threading.Thread(target=self._writer_loop, daemon=True)
        t.start()
        return t

    def _writer_loop(self):
        c = self.conn()
        try:
            while True:
                job = self._wq.get()
                if job is None:
                    break
                fn, ev, done = job
                try:
                    fn(c, ev)
                    c.commit()
                except Exception:
                    c.rollback()
                finally:
                    if done is not None:
                        done.set()
        finally:
            c.close()

    def submit(self, ev, wait=True, timeout=5):
        """Enqueue an event for the writer thread. Returns when committed (if
        wait) or immediately. Requires start_writer() to have been called."""
        done = threading.Event() if wait else None
        self._wq.put((self._apply, ev, done))
        if done is not None:
            done.wait(timeout)

    # ----- synchronous apply (direct + via writer) ---------------------------

    def apply_event(self, ev):
        """Synchronous apply — used directly by tests and any single-threaded
        caller. The server uses submit()/the writer thread instead. A single
        upsert statement makes the write atomic; COALESCE preserves populated
        fields; the seq guard + phase-rank monotonicity (with PAUSE exemption)
        prevent stale or regressing reports from moving state."""
        c = self.conn()
        try:
            self._apply(c, ev)
            c.commit()
        finally:
            c.close()

    def _apply(self, c, ev):
        from board import PHASE_RANK, TERMINAL_PHASES, PAUSE_PHASES
        now = time.time()
        rid = ev["run_id"]
        seq = ev.get("seq", 0) or 0
        cur = c.execute(
            "SELECT seq, phase FROM runs WHERE run_id=?", (rid,)).fetchone()

        # events row — idempotent by event_id (duplicate event_id inserts once)
        if ev.get("event_id"):
            c.execute(
                """INSERT OR IGNORE INTO events(
                       run_id, event_id, seq, phase, message, event_ts, recv_ts)
                   VALUES(?,?,?,?,?,?,?)""",
                (rid, ev["event_id"], seq, ev.get("phase"),
                 ev.get("note") or ev.get("message"), ev.get("event_ts"), now))

        # ---- decide the phase to persist ----
        new_phase = ev.get("phase")
        cur_phase = cur["phase"] if cur else None
        cur_seq = (cur["seq"] or 0) if cur else 0

        if not new_phase:
            # phase-less report (e.g. goal/result only): never touch phase.
            new_phase = cur_phase
        elif cur is None:
            # first event for this run: accept the incoming phase as-is.
            pass
        elif seq < cur_seq:
            # stale replay: a lower-seq report must NOT move state.
            new_phase = cur_phase
        elif cur_phase in TERMINAL_PHASES:
            # never leave a terminal phase, regardless of seq.
            new_phase = cur_phase
        elif new_phase in PAUSE_PHASES or cur_phase in PAUSE_PHASES:
            # PAUSE exemption: when entering OR leaving asking-user, skip the
            # rank-monotonicity check so implementing -> asking-user ->
            # implementing works (the pause is non-linear by design).
            pass
        elif PHASE_RANK.get(new_phase, -1) < PHASE_RANK.get(cur_phase or "", -1):
            # never regress rank (a lower-rank incoming phase keeps current).
            new_phase = cur_phase

        # ---- staleness gate for free-text content ----
        # A report is "fresh" when it's the first event for the run OR its seq
        # is >= the stored seq. Only a fresh report may advance the free-text
        # content columns; a stale (lower-seq) replay carrying a DIFFERENT
        # non-null value must NOT move state backwards (spec §4). We pass None
        # for those columns when stale so COALESCE(excluded.x, runs.x) preserves
        # the newer stored value. (phase is already handled above; seq=MAX stays.)
        fresh = (cur is None) or (seq >= cur_seq)

        def _content(key):
            return ev.get(key) if fresh else None

        # ---- atomic seq-guarded COALESCE upsert ----
        # COALESCE(excluded.x, runs.x): a NULL incoming field preserves the
        # stored value (phase-only reports never null goal/approach/result, and
        # stale reports pass None so they cannot overwrite newer content).
        # seq=MAX(runs.seq, excluded.seq): the stored seq never decreases.
        c.execute(
            """
          INSERT INTO runs(
              run_id, repo, issue, title, goal, approach, result, phase, status,
              machine, worker, seq, is_bug_fix, has_deploy, merge_mode, pr_url,
              started_at, updated_at)
          VALUES(:run_id,:repo,:issue,:title,:goal,:approach,:result,:phase,:status,
                 :machine,:worker,:seq,:is_bug_fix,:has_deploy,:merge_mode,:pr_url,
                 :now,:now)
          ON CONFLICT(run_id) DO UPDATE SET
            repo=COALESCE(excluded.repo, runs.repo),
            issue=COALESCE(excluded.issue, runs.issue),
            title=COALESCE(excluded.title, runs.title),
            goal=COALESCE(excluded.goal, runs.goal),
            approach=COALESCE(excluded.approach, runs.approach),
            result=COALESCE(excluded.result, runs.result),
            pr_url=COALESCE(excluded.pr_url, runs.pr_url),
            machine=COALESCE(excluded.machine, runs.machine),
            worker=COALESCE(excluded.worker, runs.worker),
            phase=:phase,
            status=COALESCE(excluded.status, runs.status),
            seq=MAX(runs.seq, excluded.seq),
            updated_at=:now
        """,
            {"run_id": rid, "repo": ev.get("repo"), "issue": ev.get("issue"),
             "title": _content("title"), "goal": _content("goal"),
             "approach": _content("approach"), "result": _content("result"),
             "phase": new_phase, "status": _content("status"),
             "machine": ev.get("machine"), "worker": ev.get("worker"),
             "seq": seq, "is_bug_fix": int(ev.get("is_bug_fix", 0)),
             "has_deploy": int(ev.get("has_deploy", 0)),
             "merge_mode": ev.get("merge_mode", "auto"),
             "pr_url": _content("pr_url"), "now": now})

    def get_run(self, rid):
        c = self.conn()
        try:
            return c.execute(
                "SELECT * FROM runs WHERE run_id=?", (rid,)).fetchone()
        finally:
            c.close()

    # ----- gate rows ---------------------------------------------------------

    def seed_gates(self, rid, is_bug_fix, has_deploy):
        """Seed the applicable required checks at 'pending'. INSERT OR IGNORE so
        re-seeding never clobbers an already-recorded gate state. The source is
        board-fixed per board.gate.source_of (verified vs claimed)."""
        from board.gate import applicable_gates, source_of
        now = time.time()
        c = self.conn()
        try:
            for g in applicable_gates(is_bug_fix, has_deploy):
                c.execute(
                    """INSERT OR IGNORE INTO gate(
                           run_id, check_name, state, source, seq, recv_ts)
                       VALUES(?,?, 'pending', ?, 0, ?)""",
                    (rid, g, source_of(g), now))
            c.commit()
        finally:
            c.close()

    def set_gate(self, rid, check, state, seq, claimed):
        """Record a gate check result. seq-guarded: a lower-seq report is
        dropped. The `source` is board-decided (source_of(check)) — the worker's
        `claimed` intent is intentionally IGNORED so a worker can never mark a
        board-verified check as merely 'claimed' or vice-versa."""
        from board.gate import source_of
        src = source_of(check)  # board decides; worker's `claimed` is ignored
        now = time.time()
        c = self.conn()
        try:
            cur = c.execute(
                "SELECT seq FROM gate WHERE run_id=? AND check_name=?",
                (rid, check)).fetchone()
            if cur is not None and seq < (cur["seq"] or 0):
                return  # stale — older than what we already have
            c.execute(
                """INSERT INTO gate(
                       run_id, check_name, state, source, seq, recv_ts)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(run_id, check_name) DO UPDATE SET
                     state=excluded.state, source=excluded.source,
                     seq=MAX(gate.seq, excluded.seq), recv_ts=excluded.recv_ts""",
                (rid, check, state, src, seq, now))
            c.commit()
        finally:
            c.close()

    def gate_map(self, rid):
        c = self.conn()
        try:
            rows = c.execute(
                "SELECT check_name, state FROM gate WHERE run_id=?",
                (rid,)).fetchall()
            return {r["check_name"]: r["state"] for r in rows}
        finally:
            c.close()
