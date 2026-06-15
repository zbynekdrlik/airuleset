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
import logging

_log = logging.getLogger("autopilot_board")

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
    # Writes serialize via WAL + busy_timeout=5000; the writer thread owns only
    # the runs/events upsert, while set_gate/set_gh write on their own
    # connections — all safe under WAL. Tests call apply_event() directly
    # (synchronous). BOTH paths converge on _apply, so the correctness logic
    # is identical.

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
                fn, ev, done, outcome = job
                ok = False
                try:
                    fn(c, ev)
                    c.commit()
                    ok = True
                except Exception:
                    _log.exception(
                        "board writer failed to apply event %s",
                        (ev or {}).get("event_id"),
                    )
                    c.rollback()
                finally:
                    if outcome is not None:
                        outcome.append(ok)
                    if done is not None:
                        done.set()
        finally:
            c.close()

    def submit(self, ev, wait=True, timeout=5):
        """Enqueue an event for the writer thread. When wait=True, blocks until
        the job is processed and returns True only if the write committed
        successfully within `timeout` seconds; returns False on write failure or
        timeout. When wait=False, returns True immediately (fire-and-forget).
        Requires start_writer() to have been called."""
        if wait:
            done = threading.Event()
            outcome = []
            self._wq.put((self._apply, ev, done, outcome))
            timed_out = not done.wait(timeout)
            if timed_out:
                return False
            return bool(outcome and outcome[0])
        else:
            self._wq.put((self._apply, ev, None, None))
            return True

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
        # equal seq treated as fresh (last-writer-wins); ties unexpected in
        # practice because seq is reporter-monotonic per run.
        fresh = (cur is None) or (seq >= cur_seq)

        def _content(key):
            return ev.get(key) if fresh else None

        # ---- atomic seq-guarded COALESCE upsert ----
        # COALESCE(excluded.x, runs.x): a NULL incoming field preserves the
        # stored value (phase-only reports never null goal/approach/result, and
        # stale reports pass None so they cannot overwrite newer content).
        # seq=MAX(runs.seq, excluded.seq): the stored seq never decreases.
        # machine/worker use _content() (seq-guarded) for consistency with all
        # other run-stable content columns.
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
             "machine": _content("machine"), "worker": _content("worker"),
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

    # ----- read-only queries for the server/render (Task 11/12) --------------

    def list_runs(self, limit=500):
        """All runs, newest first (updated_at DESC), capped at `limit`.

        Read-only — the server fans these into the render's live/recent buckets
        (a run is 'live' when its phase is non-terminal AND status != 'stale';
        everything else is 'recent'). Returns a list of sqlite Rows."""
        c = self.conn()
        try:
            return c.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC, started_at DESC "
                "LIMIT ?", (limit,)).fetchall()
        finally:
            c.close()

    def get_events(self, rid, limit=500):
        """Events for `rid`, ordered by seq then event_ts (the timeline order
        the ticket detail renders). Capped at `limit` (most-recent kept)."""
        c = self.conn()
        try:
            return c.execute(
                "SELECT seq, phase, message, event_ts, recv_ts FROM events "
                "WHERE run_id=? ORDER BY seq ASC, event_ts ASC LIMIT ?",
                (rid, limit)).fetchall()
        finally:
            c.close()

    def get_gh(self, rid):
        """The gh_state row for `rid` (or None). Read-only."""
        c = self.conn()
        try:
            return c.execute(
                "SELECT * FROM gh_state WHERE run_id=?", (rid,)).fetchone()
        finally:
            c.close()

    def runs_touched_since(self, since_ts):
        """Count runs whose updated_at >= since_ts (health strip: runs touched
        in the last hour)."""
        c = self.conn()
        try:
            return c.execute(
                "SELECT count(*) FROM runs WHERE updated_at >= ?",
                (since_ts,)).fetchone()[0]
        finally:
            c.close()

    def last_report_ts(self):
        """The newest events.recv_ts across all runs (health strip: 'last report
        received'), or None if no events yet."""
        c = self.conn()
        try:
            row = c.execute("SELECT MAX(recv_ts) FROM events").fetchone()
            return row[0] if row else None
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
        """Record a gate check result via the WORKER path. seq-guarded: a
        lower-seq report is dropped. The `source` is board-decided
        (source_of(check)) — the worker's `claimed` intent is intentionally
        IGNORED so a worker can never mark a board-verified check as merely
        'claimed' or vice-versa.

        Alarm-integrity lock (spec §9/§10): a worker can NEVER write a
        gh-verified check (ci/mergeable/merged/issue_state). Those rows are
        owned exclusively by the gh refresher (set_gh / _set_gate_verified). A
        worker set_gate on a verified check is refused outright — otherwise a
        worker claim could overwrite a gh-verified `mergeable=fail`/`ci=fail`
        row (flipping state→ok AND source→claimed) and silence
        MERGED_INCOMPLETE_GATE. The old `seq < cur.seq` guard did NOT stop this
        because the gh refresher writes verified rows with the seq=0 sentinel."""
        from board.gate import source_of
        if source_of(check) == "verified":
            # ci/mergeable/merged/issue_state are gh-verified only; a worker
            # claim must NEVER write (or overwrite) them. Drop silently.
            return
        src = source_of(check)  # board decides; worker's `claimed` is ignored
        now = time.time()
        c = self.conn()
        try:
            cur = c.execute(
                "SELECT seq FROM gate WHERE run_id=? AND check_name=?",
                (rid, check)).fetchone()
            # equal seq treated as fresh (last-writer-wins); ties unexpected in
            # practice because seq is reporter-monotonic per run.
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

    # ----- gh state + alarm assembly (Task 10) -------------------------------

    # Columns gh_state can carry. set_gh accepts these plus the derived
    # *_gate aliases that ALSO write verified gate rows (mergeable_gate, ci_*).
    _GH_COLS = ("pr_url", "pr_state", "merged", "ci_conclusion", "mergeable",
                "mergeable_state", "issue_state", "deploy_version")

    def set_gh(self, rid, **fields):
        """Upsert objective gh signals into gh_state for `rid`, and — for the
        gh-verified checks (mergeable, ci) — ALSO write the corresponding gate
        rows with the board-fixed source ('verified' per gate.source_of) so the
        alarm sees gh truth a worker can never spoof.

        Recognised kwargs:
          * gh_state columns: pr_url, pr_state, merged, ci_conclusion,
            mergeable, mergeable_state, issue_state, deploy_version
          * mergeable_gate (ok|fail|pending) — writes the `mergeable` gate row
            (pending is skipped so a 'still computing' poll never clobbers a
            known state).
          * ci_conclusion (success|failure|...) — also mapped to a `ci` gate
            state via gate.ci_gate (success→ok, terminal-failure→fail,
            non-terminal/in-progress→pending which is NOT written) and stored.
          * gh_ok=False — sets the STALE sentinel; gh_ok defaults True on write.

        A gh refresh is a write — it routes through this method, which the
        server calls on the single writer thread (same as _apply)."""
        from board.gate import ci_gate
        now = time.time()
        mergeable_gate = fields.pop("mergeable_gate", None)
        gh_ok = fields.pop("gh_ok", True)
        cols = {k: fields[k] for k in self._GH_COLS if k in fields}
        # bools/ints normalise to 0/1 for the INTEGER columns
        if "merged" in cols and cols["merged"] is not None:
            cols["merged"] = int(bool(cols["merged"]))

        c = self.conn()
        try:
            # ---- gh_state upsert (COALESCE so a partial poll never nulls) ----
            set_parts = ["refreshed_at=:refreshed_at", "gh_ok=:gh_ok"]
            params = {"run_id": rid, "refreshed_at": now,
                      "gh_ok": int(bool(gh_ok))}
            for k, v in cols.items():
                set_parts.append(f"{k}=COALESCE(:{k}, {k})")
                params[k] = v
            # INSERT skeleton row if absent, then UPDATE (two statements keep the
            # COALESCE-against-existing semantics simple and correct).
            c.execute(
                "INSERT OR IGNORE INTO gh_state(run_id, refreshed_at, gh_ok) "
                "VALUES(?,?,?)", (rid, now, int(bool(gh_ok))))
            c.execute(
                f"UPDATE gh_state SET {', '.join(set_parts)} WHERE run_id=:run_id",
                params)

            # ---- mirror gh-verified truth into the gate rows ----
            # mergeable gate (ok|fail). 'pending' is intentionally NOT written:
            # GitHub is still computing, so we leave the prior known state alone.
            if mergeable_gate in ("ok", "fail"):
                self._set_gate_verified(c, rid, "mergeable", mergeable_gate, now)
            # ci gate, derived from ci_conclusion via gate.ci_gate: only a
            # terminal failure is 'fail', success is 'ok', and anything
            # non-terminal (None/in_progress/neutral/skipped) maps to 'pending'
            # — which we do NOT write (same as mergeable pending) so an
            # in-progress CI never clobbers a known state or raises a false
            # MERGED_INCOMPLETE_GATE.
            cc = cols.get("ci_conclusion")
            if cc is not None:
                ci_state = ci_gate(cc)
                if ci_state in ("ok", "fail"):
                    self._set_gate_verified(c, rid, "ci", ci_state, now)
            c.commit()
        finally:
            c.close()

    @staticmethod
    def _set_gate_verified(c, rid, check, state, now):
        """Write a gh-verified gate row. The source is board-fixed to
        source_of(check) (which is 'verified' for ci/mergeable). Not seq-guarded
        the same way as worker reports: gh is the objective authority, so its
        latest read wins for the verified checks (seq=0 sentinel, recv_ts=now)."""
        from board.gate import source_of
        c.execute(
            """INSERT INTO gate(run_id, check_name, state, source, seq, recv_ts)
               VALUES(?,?,?,?,0,?)
               ON CONFLICT(run_id, check_name) DO UPDATE SET
                 state=excluded.state, source=excluded.source,
                 recv_ts=excluded.recv_ts""",
            (rid, check, state, source_of(check), now))

    def alarm_input(self, rid, merged=None):
        """Assemble the dict gate.compute_alarms expects for `rid`, or None if
        the run is unknown.

        Pulls merge_mode / is_bug_fix / has_deploy / phase from runs, the gate
        states from gate_map, `merged` from gh_state unless overridden, and
        last_report_age_s from the AUTHORITATIVE board clock (now - updated_at,
        NOT the worker-stamped event_ts — dev1/dev2 clock skew makes event_ts
        unsafe for cross-machine timing)."""
        c = self.conn()
        try:
            run = c.execute(
                "SELECT phase, status, is_bug_fix, has_deploy, merge_mode, "
                "updated_at FROM runs WHERE run_id=?", (rid,)).fetchone()
            if run is None:
                return None
            gh = c.execute(
                "SELECT merged FROM gh_state WHERE run_id=?", (rid,)).fetchone()
        finally:
            c.close()
        if merged is None:
            merged = bool(gh["merged"]) if gh and gh["merged"] is not None else False
        else:
            merged = bool(merged)
        updated_at = run["updated_at"] or time.time()
        age = max(0.0, time.time() - updated_at)
        return {
            "merged": merged,
            "merge_mode": run["merge_mode"] or "auto",
            "is_bug_fix": bool(run["is_bug_fix"]),
            "has_deploy": bool(run["has_deploy"]),
            "phase": run["phase"],
            "last_report_age_s": age,
            "gate": self.gate_map(rid),
        }

    def newest_active_run(self, repo, issue):
        """The run_id of the NEWEST non-terminal run for (repo, issue), or None.

        'Newest' = greatest started_at (ties broken by updated_at). gh signals
        for an issue are mapped to THIS run (design §4/§8: poll once per
        (repo,issue), fan to the active attempt; older attempts auto-demote)."""
        from board import TERMINAL_PHASES
        placeholders = ",".join("?" * len(TERMINAL_PHASES))
        c = self.conn()
        try:
            row = c.execute(
                f"""SELECT run_id FROM runs
                    WHERE repo=? AND issue=?
                      AND (phase IS NULL OR phase NOT IN ({placeholders}))
                      AND (status IS NULL OR status != 'stale')
                    ORDER BY started_at DESC, updated_at DESC
                    LIMIT 1""",
                (repo, issue, *TERMINAL_PHASES)).fetchone()
            return row["run_id"] if row else None
        finally:
            c.close()

    def mark_stale(self, now):
        """Reaper + reconcile (design §4):

          * RECONCILE FIRST — any non-terminal run whose gh_state shows merged
            (issue closed or PR merged) is finalized as `done` with the result
            "completed per gh, no final worker report".
          * Then any remaining non-terminal run (phase not terminal and not
            asking-user) whose last board-side update (updated_at) is older than
            its phase-aware threshold → status='stale'.

        Runs on the single writer thread in the server. Returns (reconciled,
        stale) counts for logging/tests."""
        from board import (TERMINAL_PHASES, PAUSE_PHASES, WAIT_PHASES,
                           STALE_ACTIVE_S, STALE_WAIT_S)
        term = ",".join("?" * len(TERMINAL_PHASES))
        reconciled = 0
        stale = 0
        c = self.conn()
        try:
            # ---- reconcile: gh merged+closed & non-terminal → done ----
            rows = c.execute(
                f"""SELECT r.run_id FROM runs r
                    JOIN gh_state g ON g.run_id = r.run_id
                    WHERE (r.phase IS NULL OR r.phase NOT IN ({term}))
                      AND g.merged = 1""",
                tuple(TERMINAL_PHASES)).fetchall()
            for row in rows:
                c.execute(
                    """UPDATE runs SET phase='done', status='done',
                         result=COALESCE(result,'') ||
                                CASE WHEN result IS NULL OR result=''
                                     THEN 'completed per gh, no final worker report'
                                     ELSE '' END,
                         updated_at=?
                       WHERE run_id=?""",
                    (now, row["run_id"]))
                reconciled += 1
            # ---- stale: remaining non-terminal past phase threshold ----
            cand = c.execute(
                f"""SELECT run_id, phase, updated_at FROM runs
                    WHERE (phase IS NULL OR phase NOT IN ({term}))
                      AND (status IS NULL OR status != 'stale')""",
                tuple(TERMINAL_PHASES)).fetchall()
            for row in cand:
                phase = row["phase"]
                if phase in PAUSE_PHASES:
                    continue  # asking-user is a legitimate pause, never stale
                upd = row["updated_at"] or now
                thresh = STALE_WAIT_S if phase in WAIT_PHASES else STALE_ACTIVE_S
                if (now - upd) > thresh:
                    c.execute("UPDATE runs SET status='stale' WHERE run_id=?",
                              (row["run_id"],))
                    stale += 1
            c.commit()
        finally:
            c.close()
        return reconciled, stale
