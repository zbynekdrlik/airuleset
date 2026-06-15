# Autopilot Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a central, always-on web board on dev1 where every `/autopilot` worker (dev1+dev2) reports per-phase — ticket, goal, approach, result, phase, and a review-gate audit with a "merged-with-incomplete-gate" alarm — so the user can catch work done/solved wrong.

**Architecture:** Python-stdlib `http.server` + `sqlite3` daemon on dev1 (`board/server.py`); a fire-and-forget reporter client (`board/reporter.py`) workers call via `airuleset.py report`; a background `gh` refresher fills objective signals so claims can't fake green; integrity from single-writer WAL SQLite, monotonic seq, idempotent event_id, token auth, and board-fixed gate sources. Full design: `docs/superpowers/specs/2026-06-15-autopilot-board-design.md`.

**Tech Stack:** Python 3 stdlib only (`http.server`, `sqlite3`, `json`, `urllib`, `threading`, `queue`, `fcntl`, `hmac`, `secrets`, `uuid`, `socket`, `html`, `subprocess`→`gh`). Tests: stdlib `unittest`.

**Conventions for every task:** run tests with `python3 -m unittest tests.test_board -v` (new file) or `python3 -m unittest discover -s tests`. Commit messages imperative; end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. Do NOT `airuleset.py push` until the whole feature is green (one deploy at the end).

---

## File Structure

- Create `board/__init__.py` — package marker + shared constants.
- Create `board/db.py` — schema, migrations, single-writer queue, seq-guarded UPSERT, gate seeding, queries.
- Create `board/reporter.py` — client: run_id/seq markers, queue (flock flush), circuit breaker, secret scrub, POST.
- Create `board/gate.py` — REQUIRED_GATES enum, applicability, source map, alarm computation (pure functions — easy to test).
- Create `board/gh.py` — argv gh calls, JSON parse, mergeable mapping, field validation.
- Create `board/server.py` — HTTP handler, token auth, html.escape render, refresher+reaper threads, version label.
- Create `board/render.py` — pure HTML rendering (card grid, detail, health strip, empty state) — separated so it's unit-testable without a server.
- Create `tests/test_board.py` — all board tests.
- Create `settings/autopilot-board.service.template` — systemd --user unit template.
- Create `hooks/autopilot-report.sh` — Stop-event skeleton heartbeat hook.
- Modify `airuleset.py` — `BOARD_HOST_IP`/`is_board_host()`, `report`+`board` subcommands, install branching, validate wiring, push-runs-tests.
- Modify `agents/autopilot-worker.md`, `skills/autopilot/SKILL.md`, `settings/hooks.json`, `CLAUDE.md` — governance.

---

## Phase A — Constants & gate logic (pure, no I/O)

### Task 1: Board constants

**Files:**
- Create: `board/__init__.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_board.py`:
```python
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        from board import (PORT, BOARD_HOST_IP, REPORT_TIMEOUT, CIRCUIT_BREAKER_S,
                           FLUSH_CAP, QUEUE_MAX_BYTES, QUEUE_TTL_S, BODY_MAX,
                           EVENT_CAP_PER_RUN, STALE_ACTIVE_S, STALE_WAIT_S,
                           GH_POLL_FLOOR_S, TERMINAL_PHASES)
        self.assertEqual(PORT, 8787)
        self.assertEqual(BOARD_HOST_IP, "10.77.9.21")
        self.assertEqual(REPORT_TIMEOUT, 2)
        self.assertIn("done", TERMINAL_PHASES)
        self.assertIn("obsolete-closed", TERMINAL_PHASES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_board.TestConstants -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'board'`.

- [ ] **Step 3: Write minimal implementation**

`board/__init__.py`:
```python
"""Autopilot Board — central live tracking + review-gate audit (stdlib only)."""
import os

PORT = 8787
BOARD_HOST_IP = os.environ.get("BOARD_HOST", "10.77.9.21")
REPORT_TIMEOUT = 2          # seconds, reporter connect+read
CIRCUIT_BREAKER_S = 60      # skip network this long after a failure
FLUSH_CAP = 200             # max queued events flushed per reporter invocation
QUEUE_MAX_BYTES = 5 * 1024 * 1024
QUEUE_TTL_S = 6 * 3600      # drop queued events older than this on flush
BODY_MAX = 64 * 1024        # max POST body
EVENT_CAP_PER_RUN = 500     # prune older events beyond this per run
STALE_ACTIVE_S = 8 * 60     # heartbeat threshold, active phases
STALE_WAIT_S = 30 * 60      # heartbeat threshold, CI/deploy waits
GH_POLL_FLOOR_S = 30        # min seconds between gh polls
AUTO_REFRESH_S = 10         # browser meta refresh

TERMINAL_PHASES = frozenset({"done", "stopped", "obsolete-closed"})
WAIT_PHASES = frozenset({"CI", "deploy"})
ALL_PHASES = ("validating", "version-bump", "implementing", "RED", "GREEN",
              "CI", "review", "merge", "deploy", "done", "asking-user",
              "stopped", "obsolete-closed")
PHASE_RANK = {p: i for i, p in enumerate(ALL_PHASES)}

def board_url():
    return f"http://{BOARD_HOST_IP}:{PORT}/"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_board.TestConstants -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add board/__init__.py tests/test_board.py
git commit -m "feat(board): constants module"
```

### Task 2: Required-gate enum, applicability & source map

**Files:**
- Create: `board/gate.py`
- Test: `tests/test_board.py::TestGate`

- [ ] **Step 1: Write the failing test**

```python
class TestGate(unittest.TestCase):
    def test_required_set_and_source(self):
        from board.gate import REQUIRED_GATES, source_of
        self.assertEqual(source_of("ci"), "verified")
        self.assertEqual(source_of("mergeable"), "verified")
        self.assertEqual(source_of("review"), "claimed")
        self.assertEqual(source_of("deploy_verified"), "claimed")
        self.assertIn("requesting_code_review", REQUIRED_GATES)

    def test_applicable_gates(self):
        from board.gate import applicable_gates
        feat = applicable_gates(is_bug_fix=False, has_deploy=False)
        self.assertNotIn("regression", feat)
        self.assertNotIn("deploy_verified", feat)
        self.assertIn("review", feat)
        bug = applicable_gates(is_bug_fix=True, has_deploy=True)
        self.assertIn("regression", bug)
        self.assertIn("deploy_verified", bug)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_board.TestGate -v` → FAIL (no module).

- [ ] **Step 3: Implement** `board/gate.py`:
```python
"""Pure gate/alarm logic — no I/O, fully unit-testable."""

# source is a property of the CHECK, never of the report payload.
_VERIFIED = {"ci", "mergeable", "merged", "issue_state"}
REQUIRED_GATES = (
    "ticket_validated", "ci", "mergeable", "plan_check", "review",
    "requesting_code_review", "regression", "deploy_verified",
)
# supervisor_verify is tracked but not in the merge-required set (it gates the
# UNVERIFIED-CLAIM warning, not the MERGED-INCOMPLETE alarm).

def source_of(check):
    return "verified" if check in _VERIFIED else "claimed"

def applicable_gates(is_bug_fix, has_deploy):
    out = []
    for g in REQUIRED_GATES:
        if g == "regression" and not is_bug_fix:
            continue
        if g == "deploy_verified" and not has_deploy:
            continue
        out.append(g)
    return out

def mergeable_ok(mergeable, mergeable_state):
    if mergeable is None:
        return "pending"
    if mergeable is True and mergeable_state == "CLEAN":
        return "ok"
    return "fail"
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git add board/gate.py tests/test_board.py && git commit -m "feat(board): gate enum, applicability, source map"`

### Task 3: Alarm computation

**Files:** Modify `board/gate.py`; Test `tests/test_board.py::TestAlarm`

- [ ] **Step 1: Write the failing test**

```python
class TestAlarm(unittest.TestCase):
    def _run(self, **kw):
        base = dict(merged=False, merge_mode="auto", is_bug_fix=False,
                    has_deploy=False, phase="implementing",
                    last_report_age_s=10, gate={})
        base.update(kw); return base

    def test_merged_all_ok_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"ok",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertNotIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_missing_rcr_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ci":"ok","mergeable":"ok","plan_check":"ok",
                            "review":"ok","ticket_validated":"ok"})  # rcr missing→pending
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_unstable_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"fail",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_manual_unmerged_green_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, merge_mode="manual", phase="done",
                      gate={k:"ok" for k in ("ticket_validated","ci","mergeable",
                            "plan_check","review","requesting_code_review")})
        self.assertEqual(compute_alarms(r), [])

    def test_pending_gate_recent_report_is_verifying_not_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="merge", last_report_age_s=30,
                      gate={"ci":"ok","mergeable":"ok"})  # rest pending, fresh
        a = compute_alarms(r)
        self.assertIn("VERIFYING", a)
        self.assertNotIn("MERGED_INCOMPLETE_GATE", a)

    def test_stale_abandoned_midgate(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, phase="review", last_report_age_s=9999)
        self.assertIn("STALE_ABANDONED", compute_alarms(r))
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_board.TestAlarm -v` → FAIL.

- [ ] **Step 3: Implement** — append to `board/gate.py`:
```python
GRACE_S = 5 * 60  # while a gate is pending and a report arrived this recently → "verifying"

def compute_alarms(r):
    """r: dict(merged, merge_mode, is_bug_fix, has_deploy, phase, last_report_age_s, gate{check:state}).
    Returns a list of alarm codes. Claims can NEVER silence MERGED_INCOMPLETE_GATE."""
    from board import TERMINAL_PHASES, STALE_ACTIVE_S, STALE_WAIT_S, WAIT_PHASES
    alarms = []
    req = applicable_gates(r["is_bug_fix"], r["has_deploy"])
    gate = r.get("gate", {})
    not_ok = [g for g in req if gate.get(g, "pending") != "ok"]

    if r["merged"]:
        if not_ok:
            # grace: still settling and a fresh report arrived → verifying, not alarm
            if all(gate.get(g, "pending") == "pending" for g in not_ok) \
               and r["last_report_age_s"] < GRACE_S:
                alarms.append("VERIFYING")
            else:
                alarms.append("MERGED_INCOMPLETE_GATE")
    # manual mode: green-but-unmerged is a valid done — no alarm (handled by not entering above)

    # stale / abandoned mid-gate (the other wrong-work mode)
    if r["phase"] not in TERMINAL_PHASES and r["phase"] != "asking-user":
        thresh = STALE_WAIT_S if r["phase"] in WAIT_PHASES else STALE_ACTIVE_S
        if r["last_report_age_s"] > thresh:
            alarms.append("STALE_ABANDONED")
    return alarms
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): alarm computation (merged-incomplete, verifying grace, stale)"`

---

## Phase B — DB layer (schema, single-writer, seq-guarded upsert)

### Task 4: Schema init + migration runner

**Files:** Create `board/db.py`; Test `tests/test_board.py::TestSchema`

- [ ] **Step 1: Write the failing test**

```python
import tempfile, os
class TestSchema(unittest.TestCase):
    def _db(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "b.sqlite")
        from board.db import Board
        return Board(p)

    def test_cold_init_creates_tables(self):
        b = self._db()
        tabs = {r[0] for r in b.conn().execute(
            "select name from sqlite_master where type='table'")}
        for t in ("runs","events","gate","gh_state","schema_version"):
            self.assertIn(t, tabs)

    def test_migration_idempotent(self):
        b = self._db()
        v1 = b.schema_version()
        b.migrate()  # re-run
        self.assertEqual(b.schema_version(), v1)
        self.assertGreaterEqual(v1, 1)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `board/db.py` (schema + WAL + migration). Open each connection with WAL/busy_timeout. Per the spec §7 schema:
```python
import sqlite3, threading, queue, time

_SCHEMA = [
    # migration 1 — initial
    """
    CREATE TABLE IF NOT EXISTS runs(
      run_id TEXT PRIMARY KEY, repo TEXT, issue INTEGER, title TEXT,
      goal TEXT, approach TEXT, result TEXT, phase TEXT, status TEXT,
      machine TEXT, worker TEXT, seq INTEGER DEFAULT 0,
      is_bug_fix INTEGER DEFAULT 0, has_deploy INTEGER DEFAULT 0, merge_mode TEXT DEFAULT 'auto',
      validated_evidence TEXT, merge_sha TEXT, main_ci_run TEXT,
      regression_red_test TEXT, regression_green_test TEXT, unverified TEXT, filed_issues TEXT,
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
      issue_state TEXT, deploy_version TEXT, refreshed_at REAL, gh_ok INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS schema_version(version INTEGER);
    """,
    # future migrations append here as ALTER TABLE ADD COLUMN strings
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
        c = self.conn()
        cur = c.execute("SELECT version FROM schema_version" ) if \
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone() else None
        have = cur.fetchone()[0] if cur and cur.fetchone() is None else 0
        # simpler: ensure table 0 then migrate
        c.executescript(_SCHEMA[0])
        if not c.execute("SELECT version FROM schema_version").fetchone():
            c.execute("INSERT INTO schema_version(version) VALUES (1)")
        c.commit(); c.close()
        self.migrate()

    def schema_version(self):
        c = self.conn(); v = c.execute("SELECT version FROM schema_version").fetchone()[0]; c.close()
        return v

    def migrate(self):
        c = self.conn()
        v = c.execute("SELECT version FROM schema_version").fetchone()[0]
        for i in range(v, len(_SCHEMA)):
            c.executescript(_SCHEMA[i])
            c.execute("UPDATE schema_version SET version=?", (i + 1,))
        c.commit(); c.close()
```
> Note for implementer: simplify `_init` to: create tables, ensure a single `schema_version` row = len of applied migrations (start at 1). The fiddly cursor code above is illustrative — make `schema_version()` return ≥1 and `migrate()` idempotent. The test is the contract.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestSchema -v` → PASS (fix `_init` until green).

- [ ] **Step 5: Commit** `git commit -am "feat(board): sqlite schema + WAL + idempotent migrations"`

### Task 5: Single-writer thread + seq-guarded atomic UPSERT

**Files:** Modify `board/db.py`; Test `tests/test_board.py::TestUpsert`, `::TestConcurrentWrites`

- [ ] **Step 1: Write the failing tests**

```python
class TestUpsert(unittest.TestCase):
    def _b(self):
        import tempfile, os
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_coalesce_no_null_clobber(self):
        b = self._b()
        b.apply_event({"run_id":"r1","repo":"o/x","issue":1,"seq":1,
                       "phase":"implementing","goal":"G","event_id":"e1","event_ts":1.0})
        b.apply_event({"run_id":"r1","seq":2,"phase":"CI","event_id":"e2","event_ts":2.0})
        row = b.get_run("r1")
        self.assertEqual(row["goal"], "G")      # preserved
        self.assertEqual(row["phase"], "CI")    # advanced

    def test_seq_guard_ignores_stale(self):
        b = self._b()
        b.apply_event({"run_id":"r1","repo":"o/x","issue":1,"seq":5,"phase":"merge","event_id":"e5","event_ts":5.0})
        b.apply_event({"run_id":"r1","seq":2,"phase":"CI","event_id":"e2","event_ts":2.0})  # stale replay
        self.assertEqual(b.get_run("r1")["phase"], "merge")

    def test_event_id_idempotent(self):
        b = self._b()
        ev = {"run_id":"r1","repo":"o/x","issue":1,"seq":1,"phase":"CI","event_id":"dup","event_ts":1.0}
        b.apply_event(ev); b.apply_event(ev)
        n = b.conn().execute("SELECT count(*) FROM events WHERE run_id='r1'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_terminal_not_regressed(self):
        b = self._b()
        b.apply_event({"run_id":"r1","repo":"o/x","issue":1,"seq":1,"phase":"done","event_id":"e1","event_ts":1.0})
        b.apply_event({"run_id":"r1","seq":9,"phase":"implementing","event_id":"e9","event_ts":9.0})
        self.assertEqual(b.get_run("r1")["phase"], "done")

class TestConcurrentWrites(unittest.TestCase):
    def test_parallel_posts_no_loss(self):
        import tempfile, os, threading
        from board.db import Board
        b = Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))
        def w(i):
            b.apply_event({"run_id":f"r{i%5}","repo":"o/x","issue":i%5,"seq":i,
                           "phase":"implementing","event_id":f"e{i}","event_ts":float(i)})
        ts=[threading.Thread(target=w,args=(i,)) for i in range(100)]
        [t.start() for t in ts]; [t.join() for t in ts]
        n=b.conn().execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(n,100)
```

- [ ] **Step 2: Run** → FAIL (`apply_event` missing).

- [ ] **Step 3: Implement** — add the writer thread + atomic upsert to `board/db.py`:
```python
    def start_writer(self):
        t = threading.Thread(target=self._writer_loop, daemon=True); t.start()

    def _writer_loop(self):
        c = self.conn()
        while True:
            job = self._wq.get()
            if job is None: break
            fn, ev, done = job
            try: fn(c, ev); c.commit()
            except Exception as e: c.rollback()
            finally:
                if done: done.set()

    def apply_event(self, ev):
        """Synchronous in tests; in the server, route through the writer queue.
        Single statement → atomic; COALESCE preserves; seq guard + phase-rank monotonic."""
        c = self.conn()
        try:
            self._apply(c, ev); c.commit()
        finally:
            c.close()

    def _apply(self, c, ev):
        from board import PHASE_RANK, TERMINAL_PHASES
        now = time.time()
        rid = ev["run_id"]; seq = ev.get("seq", 0)
        cur = c.execute("SELECT seq, phase FROM runs WHERE run_id=?", (rid,)).fetchone()
        # event row — idempotent by event_id
        if ev.get("event_id"):
            c.execute("""INSERT OR IGNORE INTO events(run_id,event_id,seq,phase,message,event_ts,recv_ts)
                         VALUES(?,?,?,?,?,?,?)""",
                      (rid, ev["event_id"], seq, ev.get("phase"), ev.get("note") or ev.get("message"),
                       ev.get("event_ts"), now))
        # phase monotonic: only advance if seq newer AND not regressing rank AND not leaving terminal
        new_phase = ev.get("phase")
        keep_phase = cur["phase"] if cur else None
        if new_phase and (not cur or seq >= (cur["seq"] or 0)):
            if cur and cur["phase"] in TERMINAL_PHASES:
                new_phase = cur["phase"]                       # never leave terminal
            elif cur and PHASE_RANK.get(new_phase, -1) < PHASE_RANK.get(cur["phase"] or "", -1):
                new_phase = cur["phase"]                        # never regress rank
        else:
            new_phase = keep_phase
        c.execute("""
          INSERT INTO runs(run_id,repo,issue,title,goal,approach,result,phase,status,
                           machine,worker,seq,is_bug_fix,has_deploy,merge_mode,pr_url,started_at,updated_at)
          VALUES(:run_id,:repo,:issue,:title,:goal,:approach,:result,:phase,:status,
                 :machine,:worker,:seq,:is_bug_fix,:has_deploy,:merge_mode,:pr_url,:now,:now)
          ON CONFLICT(run_id) DO UPDATE SET
            title=COALESCE(excluded.title,runs.title),
            goal=COALESCE(excluded.goal,runs.goal),
            approach=COALESCE(excluded.approach,runs.approach),
            result=COALESCE(excluded.result,runs.result),
            pr_url=COALESCE(excluded.pr_url,runs.pr_url),
            phase=:phase,
            status=COALESCE(excluded.status,runs.status),
            seq=MAX(runs.seq, excluded.seq),
            updated_at=:now
        """, {"run_id":rid,"repo":ev.get("repo"),"issue":ev.get("issue"),"title":ev.get("title"),
              "goal":ev.get("goal"),"approach":ev.get("approach"),"result":ev.get("result"),
              "phase":new_phase,"status":ev.get("status"),"machine":ev.get("machine"),
              "worker":ev.get("worker"),"seq":seq,"is_bug_fix":int(ev.get("is_bug_fix",0)),
              "has_deploy":int(ev.get("has_deploy",0)),"merge_mode":ev.get("merge_mode","auto"),
              "pr_url":ev.get("pr_url"),"now":now})

    def get_run(self, rid):
        c=self.conn(); r=c.execute("SELECT * FROM runs WHERE run_id=?",(rid,)).fetchone(); c.close(); return r
```
> Note: tests call `apply_event` directly (synchronous). The server (Task 11) routes through `_wq`+`start_writer` so HTTP threads and the gh poller never write concurrently. Both paths call `_apply`.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestUpsert tests.test_board.TestConcurrentWrites -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): single-writer + seq-guarded atomic upsert (no NULL-clobber, no regress, idempotent events)"`

### Task 6: Gate rows — seeding + seq-guarded state + board-fixed source

**Files:** Modify `board/db.py`; Test `::TestGateRows`

- [ ] **Step 1: Write the failing test**

```python
class TestGateRows(unittest.TestCase):
    def _b(self):
        import tempfile, os
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(),"b.sqlite"))
    def test_seed_pending(self):
        b=self._b()
        b.seed_gates("r1", is_bug_fix=False, has_deploy=False)
        g=b.gate_map("r1")
        self.assertEqual(g["review"], "pending")
        self.assertNotIn("regression", g)        # not applicable
    def test_set_gate_source_is_board_fixed(self):
        b=self._b(); b.seed_gates("r1", False, False)
        # worker tries to claim review verified — board forces 'claimed'
        b.set_gate("r1","review","ok",seq=3,claimed=True)
        row=b.conn().execute("SELECT source,state FROM gate WHERE run_id='r1' AND check_name='review'").fetchone()
        self.assertEqual(row["source"],"claimed"); self.assertEqual(row["state"],"ok")
    def test_gate_seq_guard(self):
        b=self._b(); b.seed_gates("r1",False,False)
        b.set_gate("r1","ci","ok",seq=5,claimed=False)
        b.set_gate("r1","ci","fail",seq=2,claimed=False)   # stale
        self.assertEqual(b.gate_map("r1")["ci"],"ok")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** in `board/db.py`:
```python
    def seed_gates(self, rid, is_bug_fix, has_deploy):
        from board.gate import applicable_gates, source_of
        c=self.conn()
        for g in applicable_gates(is_bug_fix, has_deploy):
            c.execute("""INSERT OR IGNORE INTO gate(run_id,check_name,state,source,seq,recv_ts)
                         VALUES(?,?, 'pending', ?, 0, ?)""",(rid,g,source_of(g),time.time()))
        c.commit(); c.close()

    def set_gate(self, rid, check, state, seq, claimed):
        from board.gate import source_of
        src = source_of(check)  # board decides; worker's intent ignored
        c=self.conn()
        cur=c.execute("SELECT seq FROM gate WHERE run_id=? AND check_name=?",(rid,check)).fetchone()
        if cur and seq < (cur["seq"] or 0):
            c.close(); return  # stale
        c.execute("""INSERT INTO gate(run_id,check_name,state,source,seq,recv_ts)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(run_id,check_name) DO UPDATE SET
                       state=excluded.state, source=excluded.source,
                       seq=MAX(gate.seq,excluded.seq), recv_ts=excluded.recv_ts""",
                  (rid,check,state,src,seq,time.time()))
        c.commit(); c.close()

    def gate_map(self, rid):
        c=self.conn()
        rows=c.execute("SELECT check_name,state FROM gate WHERE run_id=?",(rid,)).fetchall()
        c.close(); return {r["check_name"]: r["state"] for r in rows}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): gate seeding + seq-guarded state + board-fixed source"`

---

## Phase C — Reporter client

### Task 7: run_id / seq markers (mint once, reuse)

**Files:** Create `board/reporter.py`; Test `::TestRunId`

- [ ] **Step 1: Write the failing test**

```python
class TestRunId(unittest.TestCase):
    def setUp(self):
        import tempfile, board.reporter as rp
        self.home=tempfile.mkdtemp(); rp.STATE_DIR=self.home
    def test_mint_format_and_reuse(self):
        import board.reporter as rp
        rid=rp.start_run("o/x",1,"title",is_bug_fix=True,has_deploy=False,merge_mode="auto")
        self.assertRegex(rid, r"^o_x-1-\d+-[0-9a-f]{4}$")
        self.assertEqual(rp.current_run("o/x",1), rid)      # persisted, reusable
    def test_seq_monotonic(self):
        import board.reporter as rp
        rid=rp.start_run("o/x",2,"t")
        s1=rp.next_seq(rid); s2=rp.next_seq(rid)
        self.assertEqual(s2, s1+1)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `board/reporter.py` (markers; `repo` slashes → `_` in the id prefix only; raw repo still sent in payload):
```python
import os, json, time, uuid, re, fcntl, urllib.request, urllib.error

STATE_DIR = os.path.expanduser("~/.claude")
def _p(name): return os.path.join(STATE_DIR, name)

def _safe_prefix(repo): return re.sub(r"[^A-Za-z0-9._-]", "_", repo)

def start_run(repo, issue, title, is_bug_fix=False, has_deploy=False, merge_mode="auto"):
    rid = f"{_safe_prefix(repo)}-{issue}-{int(time.time()*1000)}-{uuid.uuid4().hex[:4]}"
    runs = _load(_p("autopilot-board-runs.json"))
    runs[f"{repo}#{issue}"] = rid
    _save(_p("autopilot-board-runs.json"), runs)
    _save(_p(f"autopilot-board-seq-{rid}.json"), {"seq": 0})
    report(rid, phase="validating", repo=repo, issue=issue, title=title,
           is_bug_fix=is_bug_fix, has_deploy=has_deploy, merge_mode=merge_mode, _start=True)
    return rid

def current_run(repo, issue):
    return _load(_p("autopilot-board-runs.json")).get(f"{repo}#{issue}")

def next_seq(rid):
    f=_p(f"autopilot-board-seq-{rid}.json"); d=_load(f); d["seq"]=d.get("seq",0)+1; _save(f,d); return d["seq"]

def _load(f):
    try:
        with open(f) as h: return json.load(h)
    except Exception: return {}
def _save(f, d):
    with open(f,"w") as h: json.dump(d,h)
```

- [ ] **Step 4: Run** → FAIL (`report` undefined) — implement a stub `def report(*a,**k): pass` to get TestRunId green, real body in Task 8.

- [ ] **Step 5: Commit** `git commit -am "feat(board): reporter run_id/seq markers"`

### Task 8: Reporter POST + secret scrub + circuit breaker + queue flush (flock)

**Files:** Modify `board/reporter.py`; Test `::TestReporter`

- [ ] **Step 1: Write the failing tests**

```python
class TestReporter(unittest.TestCase):
    def setUp(self):
        import tempfile, board.reporter as rp
        self.home=tempfile.mkdtemp(); rp.STATE_DIR=self.home
    def test_secret_scrub(self):
        from board.reporter import scrub
        self.assertNotIn("ghp_", scrub("token ghp_ABCDEF123456 here"))
        self.assertIn("[redacted]", scrub("Bearer abc.def.ghi"))
    def test_queue_on_unreachable(self):
        import board.reporter as rp
        rp.BOARD_URL="http://127.0.0.1:1/"   # nothing listening
        rid=rp.start_run("o/x",3,"t")        # _start queues
        rp.report(rid, phase="CI")
        import os
        self.assertTrue(os.path.exists(rp._p("autopilot-board-queue.jsonl")))
    def test_flush_idempotent_event_ids(self):
        import board.reporter as rp, json
        # two queued lines with distinct event_id; a fake sender that records
        sent=[]
        rp._post_one=lambda body: sent.append(body) or True
        with open(rp._p("autopilot-board-queue.jsonl"),"w") as h:
            h.write(json.dumps({"event_id":"a","run_id":"r"})+"\n")
            h.write(json.dumps({"event_id":"b","run_id":"r"})+"\n")
        rp.flush_queue()
        self.assertEqual({json.loads(x)["event_id"] for x in sent}, {"a","b"})
        self.assertEqual(os.path.getsize(rp._p("autopilot-board-queue.jsonl")), 0)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** the real `report`, `scrub`, `_post_one`, `flush_queue` in `board/reporter.py`:
```python
from board import REPORT_TIMEOUT, CIRCUIT_BREAKER_S, FLUSH_CAP, QUEUE_TTL_S, board_url

BOARD_URL = board_url()
_SECRET_RE = re.compile(r"(ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AKIA[0-9A-Z]+|xox[a-z]-[A-Za-z0-9-]+|-----BEGIN[^\n]*|Bearer\s+[A-Za-z0-9._-]+)")
def scrub(s):
    if not s: return s
    return _SECRET_RE.sub("[redacted]", str(s))[:2000].replace("\n"," ").strip()

def _token():
    try:
        with open(_p("autopilot-board.token")) as h: return h.read().strip()
    except Exception: return ""

def _down_recently():
    try:
        ts=float(open(_p("autopilot-board-down")).read().strip())
        return (time.time()-ts) < CIRCUIT_BREAKER_S
    except Exception: return False

def _mark_down(): open(_p("autopilot-board-down"),"w").write(str(time.time()))
def _clear_down():
    try: os.remove(_p("autopilot-board-down"))
    except OSError: pass

def _post_one(body):
    req=urllib.request.Request(BOARD_URL.rstrip("/")+"/report",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type":"application/json","X-Board-Token":_token()})
    try:
        with urllib.request.urlopen(req, timeout=REPORT_TIMEOUT) as r:
            return 200 <= r.status < 300
    except Exception:
        return False

def report(rid, phase=None, _start=False, reviews=None, **fields):
    for k in ("goal","approach","result","note","title"):
        if k in fields: fields[k]=scrub(fields[k])
    ev={"run_id":rid,"event_id":uuid.uuid4().hex,"seq":next_seq(rid),
        "phase":phase,"event_ts":time.time(),"machine":os.uname().nodename}
    ev.update({k:v for k,v in fields.items() if v is not None})
    if reviews: ev["reviews"]=reviews     # [(check,state), ...]
    _enqueue_and_flush(ev)

def _enqueue_and_flush(ev):
    # append first (durability), then try to flush under lock
    with open(_p("autopilot-board-queue.jsonl"),"a") as h:
        h.write(json.dumps(ev)+"\n")
    if _down_recently(): return
    flush_queue()

def flush_queue():
    qf=_p("autopilot-board-queue.jsonl")
    if not os.path.exists(qf): return
    try:
        h=open(qf,"r+"); fcntl.flock(h, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return  # another reporter owns it
    try:
        lines=h.readlines(); remaining=[]; sent=0; now=time.time()
        for i,ln in enumerate(lines):
            if sent>=FLUSH_CAP: remaining.append(ln); continue
            try: body=json.loads(ln)
            except Exception: continue  # poison line: skip+drop
            if now-body.get("event_ts",now) > QUEUE_TTL_S: continue  # TTL drop
            if _post_one(body): sent+=1; _clear_down()
            else: _mark_down(); remaining.append(ln); remaining.extend(lines[i+1:]); break
        h.seek(0); h.truncate(); h.writelines(remaining)
    finally:
        fcntl.flock(h, fcntl.LOCK_UN); h.close()
```

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestReporter -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): reporter POST, secret scrub, circuit breaker, flock idempotent flush"`

---

## Phase D — gh refresher + validation

### Task 9: gh field validation + JSON parse + mergeable mapping

**Files:** Create `board/gh.py`; Test `::TestGh`

- [ ] **Step 1: Write the failing tests**

```python
class TestGh(unittest.TestCase):
    def test_validate_repo_issue_runid(self):
        from board.gh import valid_repo, valid_issue, valid_run_id
        self.assertTrue(valid_repo("owner/name")); self.assertFalse(valid_repo("--version"))
        self.assertFalse(valid_repo("a;b/c"))
        self.assertTrue(valid_issue(5)); self.assertFalse(valid_issue("-1")); self.assertFalse(valid_issue("x"))
        self.assertTrue(valid_run_id("o_x-1-123-ab12")); self.assertFalse(valid_run_id("a/b"))
    def test_parse_pr_mergeable(self):
        from board.gh import classify_pr
        ok=classify_pr({"state":"MERGED","mergedAt":"...","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"})
        self.assertTrue(ok["merged"]); self.assertEqual(ok["mergeable_gate"],"ok")
        un=classify_pr({"state":"OPEN","mergeable":"MERGEABLE","mergeStateStatus":"UNSTABLE"})
        self.assertEqual(un["mergeable_gate"],"fail")
        pend=classify_pr({"state":"OPEN","mergeable":"UNKNOWN","mergeStateStatus":"UNKNOWN"})
        self.assertEqual(pend["mergeable_gate"],"pending")
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `board/gh.py`:
```python
import re, subprocess, json
from board.gate import mergeable_ok

_REPO=re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_RID=re.compile(r"^[A-Za-z0-9._-]+$")
def valid_repo(s): return bool(isinstance(s,str) and _REPO.match(s))
def valid_run_id(s): return bool(isinstance(s,str) and _RID.match(s))
def valid_issue(n):
    try: return int(n) > 0 and str(n).lstrip().isdigit() if isinstance(n,str) else int(n)>0
    except Exception: return False

ALLOWED=({"pr","view"},{"pr","list"},{"run","list"},{"issue","view"},{"issue","list"})

def _gh(args, timeout=20):
    # argv ONLY, never shell. Caller passes pre-validated values.
    r=subprocess.run(["gh"]+args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr

def classify_pr(pr):
    merged = pr.get("state")=="MERGED" or bool(pr.get("mergedAt"))
    m=pr.get("mergeable"); ms=pr.get("mergeStateStatus")
    mb = None if m in (None,"UNKNOWN") else (m=="MERGEABLE")
    gate = mergeable_ok(mb, ms)
    return {"merged":merged,"pr_state":pr.get("state"),"mergeable":mb,
            "mergeable_state":ms,"mergeable_gate":gate}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): gh validation + PR classification (mergeable mapping)"`

### Task 10: Refresher (batched, backoff, sentinel) + reaper + alarm wiring

**Files:** Modify `board/db.py` (queries), `board/gh.py` (batched fetch), Test `::TestRefresh`

- [ ] **Step 1: Write the failing test** (mock `_gh` to return canned JSON; assert gh_state written, UNSTABLE-merged → alarm via `compute_alarms`, gh failure → `gh_ok=0`):

```python
class TestRefresh(unittest.TestCase):
    def test_unstable_merged_alarms_end_to_end(self):
        import tempfile, os
        from board.db import Board
        from board.gate import compute_alarms
        b=Board(os.path.join(tempfile.mkdtemp(),"b.sqlite")); b.seed_gates("r1",False,False)
        b.apply_event({"run_id":"r1","repo":"o/x","issue":1,"seq":1,"phase":"done","event_id":"e","event_ts":1.0})
        for g in ("ticket_validated","ci","plan_check","review","requesting_code_review"):
            b.set_gate("r1",g,"ok",seq=1,claimed=True)
        b.set_gate("r1","mergeable","fail",seq=1,claimed=False)  # UNSTABLE
        snap=b.alarm_input("r1", merged=True)
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(snap))
```

- [ ] **Step 2: Run** → FAIL (`alarm_input` missing).

- [ ] **Step 3: Implement**
  - `Board.alarm_input(rid, merged=None)` — assembles the dict `compute_alarms` needs (reads `runs` for is_bug_fix/has_deploy/merge_mode/phase, `gate_map`, last_report_age from `updated_at` vs board now, merged from gh_state unless overridden).
  - `Board.set_gh(rid, **gh_fields)` upsert into `gh_state`.
  - `gh.fetch_repo_active(repo, issues)` — ONE `gh pr list`/`gh issue list` (validated repo, allowlisted subcommand, `--json` fields), return parsed; on non-zero/rate-limit return `{"gh_ok":False}`.
  - `board/server.py` refresher thread (Task 11) calls these with `GH_POLL_FLOOR_S` floor, `try/except` per repo, backoff on 403.
  - Reaper: `Board.mark_stale(now)` sets `status='stale'` for non-terminal runs past threshold; reconcile: gh merged+closed & non-terminal → finalize `done`.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestRefresh -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): alarm_input, gh_state upsert, batched fetch, reaper/reconcile"`

---

## Phase E — HTTP server + render + security

### Task 11: Render (pure HTML, escaped) + empty state + version label

**Files:** Create `board/render.py`; Test `::TestRender`

- [ ] **Step 1: Write the failing tests**

```python
class TestRender(unittest.TestCase):
    def test_escapes_xss(self):
        from board.render import card_grid
        html=card_grid(live=[{"run_id":"r","repo":"o/x","issue":1,
            "title":"<script>alert(1)</script>","phase":"CI","goal":"g","gate":{},"alarms":[]}],
            recent=[], version="vX", health={})
        self.assertIn("&lt;script&gt;", html); self.assertNotIn("<script>alert", html)
    def test_empty_state(self):
        from board.render import card_grid
        html=card_grid(live=[], recent=[], version="vX", health={"last_report":"never"})
        self.assertIn("No autopilot runs yet", html); self.assertIn("vX", html)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `board/render.py` with `card_grid(live, recent, version, health)` and `ticket_detail(run, events, gate, gh)` — every interpolation via `html.escape()`; PR links validated `https://github.com/` before `href`; alarms rendered as red banners; version in footer. Pure string functions (no server dependency).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): html-escaped card-grid + detail render, empty state, version footer"`

### Task 12: HTTP server — token auth, body cap, endpoints, threads

**Files:** Create `board/server.py`; Test `::TestServer`

- [ ] **Step 1: Write the failing tests** (start the server on an ephemeral port in `setUp`, real HTTP via `urllib`):

```python
class TestServer(unittest.TestCase):
    def setUp(self):
        import tempfile, os, threading, board.server as srv
        from board.db import Board
        self.dir=tempfile.mkdtemp(); self.token="t0ken"
        open(os.path.join(self.dir,"tok"),"w").write(self.token)
        self.b=Board(os.path.join(self.dir,"b.sqlite")); self.b.start_writer()
        self.httpd=srv.make_server(self.b, token=self.token, host="127.0.0.1", port=0)
        self.port=self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
    def tearDown(self): self.httpd.shutdown()
    def _post(self, body, token="t0ken"):
        import urllib.request, json
        req=urllib.request.Request(f"http://127.0.0.1:{self.port}/report",
            data=json.dumps(body).encode(), method="POST",
            headers={"X-Board-Token":token,"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req) as r: return r.status
        except urllib.error.HTTPError as e: return e.code
    def test_rejects_bad_token(self):
        self.assertEqual(self._post({"run_id":"r1","phase":"CI"}, token="wrong"), 403)
    def test_accepts_and_persists(self):
        self.assertEqual(self._post({"run_id":"r1","repo":"o/x","issue":1,"seq":1,
                                     "phase":"CI","event_id":"e1","event_ts":1.0}), 200)
        import time; time.sleep(0.2)
        self.assertEqual(self.b.get_run("r1")["phase"], "CI")
    def test_rejects_bad_repo(self):
        self.assertEqual(self._post({"run_id":"r1","repo":"a;b/c","issue":1,
                                     "phase":"CI","event_id":"e2"}), 400)
    def test_body_too_large(self):
        self.assertEqual(self._post({"run_id":"r1","note":"x"*70000,"event_id":"e3"}), 413)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `board/server.py`:
  - `make_server(board, token, host, port)` returns a `ThreadingHTTPServer` with a handler closure.
  - POST `/report`: `hmac.compare_digest` token check (403); `Content-Length` ≤ `BODY_MAX` (413); JSON parse; validate `run_id`/`repo`/`issue` (400 per `gh.valid_*`); enqueue to the board writer (`board._wq.put`) → 200 after the writer signals commit (use an `Event` per request, short timeout). Apply gate rows from `reviews`.
  - GET `/`: `render.card_grid(...)` with CSP + `text/html; charset=utf-8`, no CORS header.
  - GET `/ticket/<run>`: `render.ticket_detail`.
  - GET `/api/state`: token-gated JSON.
  - per-IP token-bucket rate limit (in-memory dict).
  - `run_server()`: bind `BOARD_HOST_IP:PORT`, fail loud if bound; `board.start_writer()`; start refresher + reaper threads; version from `git describe`.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestServer -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(board): http server — token auth, validation, body cap, threaded endpoints"`

---

## Phase E2 — Planned queue / backlog (ADDED 2026-06-15)

Implements spec §11a. Slots in AFTER Phase E (needs the db, server, and render to exist). Governance piece (supervisor reports the queue) is wired in Phase G.

### Task 19: Queue table + reporter + server + render + gh-prune

**Files:** Modify `board/db.py`, `board/reporter.py`, `board/server.py`, `board/render.py`, `airuleset.py`; Test `tests/test_board.py::TestQueue`.

- [ ] **Step 1: Write the failing tests**

```python
class TestQueue(unittest.TestCase):
    def _b(self):
        import tempfile, os
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))
    def test_set_queue_replaces_atomically_and_orders(self):
        b=self._b()
        b.set_queue("o/x", [(5,"five"),(9,"nine")])
        b.set_queue("o/x", [(9,"nine"),(7,"seven")])   # replace
        q=b.get_queue()                                 # [{repo,issue,title,position}], excludes active/closed
        nums=[r["issue"] for r in q if r["repo"]=="o/x"]
        self.assertEqual(nums, [9,7])                   # new order, old #5 gone
    def test_prune_closed(self):
        b=self._b()
        b.set_queue("o/x", [(5,"five"),(9,"nine")])
        b.prune_queue("o/x", open_issues={9})           # 5 no longer open → dropped
        self.assertEqual([r["issue"] for r in b.get_queue()], [9])
    def test_queue_excludes_active_run(self):
        b=self._b()
        b.set_queue("o/x", [(5,"five"),(9,"nine")])
        b.apply_event({"run_id":"o_x-5-1-aa","repo":"o/x","issue":5,"seq":1,
                       "phase":"implementing","event_id":"e","event_ts":1.0})
        nums=[r["issue"] for r in b.get_queue()]        # 5 is now active → not in queue
        self.assertNotIn(5, nums); self.assertIn(9, nums)
    def test_render_queue_count_and_escape(self):
        from board.render import card_grid
        html=card_grid(live=[], recent=[], version="vX",
                       health={}, queue=[{"repo":"o/x","issue":1,"title":"<b>t</b>","position":0}])
        self.assertIn("Up next", html); self.assertIn("1 queued", html)
        self.assertIn("&lt;b&gt;", html); self.assertNotIn("<b>t</b>", html)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_board.TestQueue -v` → FAIL.

- [ ] **Step 3: Implement**
  - `board/db.py`: migration 2 — append to `_SCHEMA` a `CREATE TABLE IF NOT EXISTS queue(repo TEXT, issue INTEGER, title TEXT, position INTEGER, reported_at REAL, PRIMARY KEY(repo,issue))`. Methods: `set_queue(repo, items)` (atomic: `DELETE FROM queue WHERE repo=?` then insert each `(repo,issue,title,position,now)`), `prune_queue(repo, open_issues)` (delete rows whose issue not in `open_issues`), `get_queue()` (return ordered rows EXCLUDING any `(repo,issue)` that has a non-terminal run OR whose issue is a closed/terminal run — join against `runs`).
  - `board/reporter.py`: `queue_report(repo, items)` → POST a `{"kind":"queue","repo":repo,"items":[[n,t],...]}` body (token, scrubbed titles, through the same enqueue/flush path).
  - `airuleset.py` `cmd_report`: add `--queue --repo R --items '<json>'` → `reporter.queue_report(R, json.loads(items))`.
  - `board/server.py` POST `/report`: if body `kind=="queue"`, validate repo + each issue int + scrub/cap titles, then `board.set_queue(...)` (via the writer queue). Else the normal event path.
  - `board/render.py`: `card_grid(..., queue=None)` renders a top "**Up next — N queued**" section (N = len(queue)); per-repo groups, ordered by position, `#issue title` each `html.escape`d. Empty queue → omit or show "queue empty".
  - `board/gh.py` refresher loop (Phase D code): after fetching a repo's open issues, call `board.prune_queue(repo, open_issue_numbers)`.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_board.TestQueue -v` → PASS; then full `python3 -m unittest discover -s tests`.

- [ ] **Step 5: Commit** `git commit -am "feat(board): planned-queue (backlog) — table, reporter --queue, server, render, gh-prune"`

## Phase F — airuleset.py integration

### Task 13: `BOARD_HOST_IP` + `is_board_host()` + `report`/`board` subcommands

**Files:** Modify `airuleset.py`; Test `tests/test_airuleset.py::TestBoardHost`

- [ ] **Step 1: Write the failing test**

```python
class TestBoardHost(TestCase):
    def test_is_board_host_helper_exists(self):
        import airuleset
        self.assertTrue(hasattr(airuleset, "is_board_host"))
        self.assertEqual(airuleset.BOARD_HOST_IP, "10.77.9.21")
    def test_report_subcommand_registered(self):
        import airuleset
        self.assertIn("report", airuleset.SUBCOMMANDS)  # however the CLI registers them
        self.assertIn("board", airuleset.SUBCOMMANDS)
```
> Adapt the assertion to airuleset.py's actual arg-dispatch (argparse subparsers or a dict). The contract: `report` and `board` are invokable.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_airuleset.TestBoardHost -v` → FAIL.

- [ ] **Step 3: Implement** in `airuleset.py`:
```python
import socket
BOARD_HOST_IP = os.environ.get("BOARD_HOST", "10.77.9.21")
def is_board_host():
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
        ips.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        ips = []
    return BOARD_HOST_IP in ips
```
  - Add `cmd_report(args)` → `from board import reporter; reporter.report(...)` / `reporter.start_run(...)` / `reporter.flush_queue()` / `--selftest`.
  - Add `cmd_board(args)` → `--url` (curl-check then print `board_url()`, restart if dead), `status`, run-foreground (`from board.server import run_server`).
  - Wire both into the existing CLI dispatch.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(airuleset): BOARD_HOST_IP, is_board_host, report+board subcommands"`

### Task 14: install branching + systemd unit + validate wiring + push-runs-tests

**Files:** Modify `airuleset.py`; Create `settings/autopilot-board.service.template`; Test `::TestInstallBranch`

- [ ] **Step 1: Write the failing test**

```python
class TestInstallBranch(TestCase):
    def test_service_setup_gated_on_board_host(self, ):
        import airuleset, unittest.mock as m
        with m.patch.object(airuleset,"is_board_host",return_value=False):
            calls=[]
            with m.patch.object(airuleset,"setup_board_service",side_effect=lambda *a:calls.append(1)):
                airuleset.maybe_setup_board()   # the gated wrapper
            self.assertEqual(calls, [])         # NOT called off board host
        with m.patch.object(airuleset,"is_board_host",return_value=True):
            calls=[]
            with m.patch.object(airuleset,"setup_board_service",side_effect=lambda *a:calls.append(1)):
                airuleset.maybe_setup_board()
            self.assertEqual(calls, [1])
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**
  - `settings/autopilot-board.service.template` — `[Service] ExecStart=python3 .../airuleset.py board --serve`, hardening directives (NoNewPrivileges, ProtectSystem=strict + ReadWritePaths=~/.claude, ProtectHome=read-only, PrivateTmp, RestrictAddressFamilies, MemoryMax, TasksMax, Restart=on-failure, RestartSec, StartLimitBurst), `[Install] WantedBy=default.target`.
  - `setup_board_service()`: generate token (`secrets.token_urlsafe`, 0600 at `~/.claude/autopilot-board.token`) if absent; write unit to `~/.config/systemd/user/`; `loginctl enable-linger`; `systemctl --user daemon-reload && enable --now` with explicit `XDG_RUNTIME_DIR`; check rc, on failure print exact manual command; curl `127.0.0.1:8787` for 200 then print the `10.77.9.21` LAN URL.
  - `maybe_setup_board()` = `if is_board_host(): setup_board_service() else: ensure reporter+queue dir; print "board: skipped (reports go to <url>)"`. Call from `cmd_install`.
  - `cmd_validate`: `importlib` load `board/*.py` (assert import clean); assert the service template + token-handling exist.
  - `cmd_push`: run `subprocess.run([sys.executable,"-m","unittest","discover","-s","tests"])`, `sys.exit(1)` on failure, BEFORE the git push / dev2 deploy.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_airuleset.TestInstallBranch -v` → PASS; then full `python3 -m unittest discover -s tests`.

- [ ] **Step 5: Commit** `git commit -am "feat(airuleset): install branching, systemd unit, validate wiring, push-runs-tests"`

---

## Phase G — Governance wiring

### Task 15: Stop-event skeleton hook

**Files:** Create `hooks/autopilot-report.sh`; Modify `settings/hooks.json`; Test `::TestReportHook`

- [ ] **Step 1: Write the failing test** (the hook is a no-op unless `AUTOPILOT_RUN` is set; with it set + board down it must exit 0 and never block):

```python
class TestReportHook(TestCase):
    HOOK = airuleset.REPO_DIR / "hooks" / "autopilot-report.sh"
    def test_noop_without_env(self):
        import subprocess
        r=subprocess.run(["bash",str(self.HOOK)],input="{}",capture_output=True,text=True,
                         env={"PATH":os.environ["PATH"]})
        self.assertEqual(r.returncode,0)
    def test_exits_zero_with_env_board_down(self):
        import subprocess, os as _os
        e=dict(_os.environ, AUTOPILOT_RUN="r1", AUTOPILOT_PHASE="implementing", BOARD_HOST="127.0.0.1")
        r=subprocess.run(["bash",str(self.HOOK)],input="{}",capture_output=True,text=True,env=e)
        self.assertEqual(r.returncode,0)
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `hooks/autopilot-report.sh`:
```bash
#!/usr/bin/env bash
# Stop hook: emit a skeleton heartbeat+phase for the current autopilot run.
# No-op unless AUTOPILOT_RUN is set. NEVER blocks: backgrounds + always exit 0.
set -u
[ -n "${AUTOPILOT_RUN:-}" ] || exit 0
( python3 "$HOME/devel/airuleset/airuleset.py" report \
    --run "$AUTOPILOT_RUN" --heartbeat ${AUTOPILOT_PHASE:+--phase "$AUTOPILOT_PHASE"} \
    >/dev/null 2>&1 & ) || true
exit 0
```
Register in `settings/hooks.json` under the `Stop` event array.

- [ ] **Step 4: Run** → PASS; re-run `airuleset.py validate`.

- [ ] **Step 5: Commit** `git commit -am "feat(board): Stop-event skeleton heartbeat hook"`

### Task 16: Worker + skill + CLAUDE.md governance text

**Files:** Modify `agents/autopilot-worker.md`, `skills/autopilot/SKILL.md`, `CLAUDE.md`

- [ ] **Step 1 (no test — prose governance):** Add to `agents/autopilot-worker.md` ONE compact REPORTING block near the top (per spec §12): Step 0a START THE RUN (`RUN=$(python3 ~/devel/airuleset/airuleset.py report --start --repo <r> --issue <N> --title "..." [--is-bug-fix] [--has-deploy] [--merge-mode ..])`), then "after each phase transition run one `report --run $RUN --phase <p> [--goal/--approach/--result/--review k=v]` line; fire-and-forget, exits 0, never a reason to pause." Do NOT interleave into the 9 steps.

- [ ] **Step 2:** Add to `skills/autopilot/SKILL.md`: a bullet under *How it works* (board URL + workers self-report); Step 1b — supervisor reports the ticket-validator OVERCOME auto-close (`report --start ... ; report --run $R --phase obsolete-closed --result "<evidence>"`); Step 4 — after independent verify, `report --run $R --review supervisor-verify=ok|fail`; print the board URL in the Step 1 preflight banner + the milestone ping.

- [ ] **Step 3:** Add `## Dashboards` to airuleset `CLAUDE.md` with the board URL `http://10.77.9.21:8787/`.

- [ ] **Step 4:** Run `python3 airuleset.py validate` (governance files resolve) + full `python3 -m unittest discover -s tests`.

- [ ] **Step 5: Commit** `git commit -am "docs(board): wire reporting into autopilot-worker, autopilot skill, CLAUDE.md"`

---

## Phase H — End-to-end + deploy

### Task 17: End-to-end smoke (reporter → live server → board state)

**Files:** Test `tests/test_board.py::TestEndToEnd`

- [ ] **Step 1: Write the failing test** — start a real server (ephemeral port + token), point `reporter.BOARD_URL` at it, run `start_run` + a few `report` phase calls + a `--review` claim, then assert `/api/state` (with token) shows the run at the right phase with the gate chips, and a merged+incomplete scenario yields the alarm in the JSON.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3:** Fix any wiring gaps the e2e exposes (this task is integration glue, not new design).

- [ ] **Step 4: Run** full `python3 -m unittest discover -s tests` → all PASS.

- [ ] **Step 5: Commit** `git commit -am "test(board): end-to-end reporter→server→state+alarm smoke"`

### Task 18: Seed a version tag, deploy, verify live

**Files:** repo tag; live dev1.

- [ ] **Step 1:** `git tag board-v0.1.0` (so `git describe --tags` yields a semver for the board version label).
- [ ] **Step 2:** `python3 airuleset.py validate` clean; full test suite green.
- [ ] **Step 3:** `python3 airuleset.py push` — runs tests (fail-closed), pushes to GitHub, installs locally (dev1 → service comes up; verify `board --url` returns 200), deploys to dev2 (reporter-only, board skipped).
- [ ] **Step 4: Verify live (Playwright, real browser, not curl):** open `http://10.77.9.21:8787/`, confirm the empty-state + version label render; run `airuleset.py report --selftest` on dev1 AND dev2 (over the LAN) and confirm the synthetic ping appears on the board; check browser console = zero errors; read the version label from the DOM and confirm it matches `git describe`.
- [ ] **Step 5: Commit / report** — completion report per `completion-report.md` with the live board URL in a `🌐` line and the DOM-read version on the `✅ Deploy:` line.

---

## Self-Review (author checklist)

**Spec coverage:** §3 components → Tasks 1–12,15; §4 identity/seq/lifecycle → Tasks 5,7,10; §5 reporter → Tasks 7–8; §6 server → Tasks 11–12; §7 schema → Task 4; §8 gh refresher → Tasks 9–10; §9 gate/alarm → Tasks 2–3,6,10; §10 security → Tasks 6,8,9,11,12,14; §11 UI → Task 11; §12 governance → Tasks 13–16; §13 constants → Task 1; §14 tests → interleaved per task + Task 17. All sections covered.

**Placeholders:** Task 4's `_init` carries an explicit "simplify until the test is green" note (the test is the contract) — acceptable, not a silent gap. No TBD/TODO elsewhere.

**Type consistency:** `apply_event`/`_apply`, `seed_gates`/`set_gate`/`gate_map`, `start_run`/`current_run`/`next_seq`/`report`/`flush_queue`, `classify_pr`/`valid_*`, `card_grid`/`ticket_detail`, `is_board_host`/`setup_board_service`/`maybe_setup_board` — names consistent across tasks.
