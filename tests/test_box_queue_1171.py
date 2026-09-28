"""#1171: `airuleset.py box-queue` — the ONE tested mechanism by which the
parallel autopilot lanes of one stream share that stream's single erp-test
PROD-copy box (montalu4, 2026-09-28: six improvised prose patches in one day —
non-FIFO flock starvation, a 3-hour hold, an out-of-turn take, a refresh
script that rewrote `~/.ssh/config`).

Real temp dirs, real `fcntl.flock`, real pid liveness, a fake clock for lease
expiry, fake refresh/health scripts, and real subprocesses for concurrency.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_box_queue as bq  # noqa: E402

BOX = "erp-test-demo"


def dead_pid():
    """A pid that WAS valid and is now guaranteed dead (reaped)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def run_cli(args, env):
    return subprocess.run(
        [sys.executable, str(REPO / "airuleset.py"), "box-queue"] + args,
        capture_output=True, text=True, timeout=60, env=env,
    )


class _Base(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "box-queue"
        self._old = os.environ.get("AIRULESET_BOX_QUEUE_DIR")
        os.environ["AIRULESET_BOX_QUEUE_DIR"] = str(self.dir)
        self.me = os.getpid()
        self.env = dict(os.environ)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AIRULESET_BOX_QUEUE_DIR", None)
        else:
            os.environ["AIRULESET_BOX_QUEUE_DIR"] = self._old
        self._tmp.cleanup()

    def state(self):
        return json.loads((self.dir / f"{BOX}.json").read_text())

    def write_config(self, **cfg):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{BOX}.config.json").write_text(json.dumps(cfg))

    def script(self, name, body):
        p = Path(self._tmp.name) / name
        p.write_text("#!/bin/bash\nset -euo pipefail\n" + textwrap.dedent(body))
        p.chmod(0o755)
        return str(p)

    def log_lines(self):
        p = self.dir / "decisions.log"
        return p.read_text().splitlines() if p.exists() else []


class TestFifoTake(_Base):
    def test_fresh_box_is_pristine_and_empty(self):
        st = bq.status(BOX, now=1000.0)
        self.assertEqual(st["state"], "pristine")
        self.assertEqual(st["queue"], [])
        self.assertIsNone(st["holder"])

    def test_take_refused_for_non_head_lane(self):
        for lane in ("a", "b", "c"):
            self.assertTrue(bq.enqueue(BOX, lane, self.me, now=1000.0)["ok"])
        r = bq.take(BOX, "b", self.me, now_fn=lambda: 1001.0)
        self.assertFalse(r["ok"])
        self.assertIn("head", r["msg"])
        self.assertEqual(self.state()["state"], "pristine")
        r = bq.take(BOX, "a", self.me, now_fn=lambda: 1002.0)
        self.assertTrue(r["ok"], r)
        st = self.state()
        self.assertEqual(st["state"], "held:a")
        self.assertEqual([e["lane"] for e in st["queue"]], ["b", "c"])

    def test_strict_fifo_order_across_releases(self):
        for lane in ("a", "b", "c"):
            bq.enqueue(BOX, lane, self.me, now=1000.0)
        self.assertTrue(bq.take(BOX, "a", self.me, now_fn=lambda: 1001.0)["ok"])
        self.assertTrue(bq.release(BOX, "a", now=1002.0)["ok"])
        self.assertFalse(bq.take(BOX, "c", self.me, now_fn=lambda: 1003.0)["ok"])
        self.assertTrue(bq.take(BOX, "b", self.me, now_fn=lambda: 1004.0)["ok"])

    def test_enqueue_is_idempotent(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.enqueue(BOX, "b", self.me, now=1001.0)
        bq.enqueue(BOX, "a", self.me, now=1002.0)
        self.assertEqual([e["lane"] for e in self.state()["queue"]], ["a", "b"])

    def test_take_refused_for_non_pristine_box(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.enqueue(BOX, "b", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1001.0)
        bq.release(BOX, "a", dirty=True, reason="deployed a branch", now=1002.0)
        self.assertEqual(self.state()["state"], "dirty:a")
        r = bq.take(BOX, "b", self.me, now_fn=lambda: 1003.0)
        self.assertFalse(r["ok"])
        self.assertIn("dirty:a", r["msg"])

    def test_take_auto_enqueues_at_the_tail(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        r = bq.take(BOX, "z", self.me, now_fn=lambda: 1001.0)
        self.assertFalse(r["ok"])
        self.assertEqual([e["lane"] for e in self.state()["queue"]], ["a", "z"])

    def test_bounded_wait_polls_then_gives_up(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        clock = [1000.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            clock[0] += s

        r = bq.take(BOX, "b", self.me, wait_s=30, poll_s=10,
                    now_fn=lambda: clock[0], sleep_fn=sleep)
        self.assertFalse(r["ok"])
        self.assertEqual(sum(sleeps), 30)
        self.assertLessEqual(clock[0], 1030.0 + 1e-9)

    def test_wait_succeeds_when_head_releases(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1000.0)
        bq.enqueue(BOX, "b", self.me, now=1000.0)
        clock = [1000.0]

        def sleep(s):
            clock[0] += s
            if clock[0] >= 1020.0 and self.state()["state"] == "held:a":
                bq.release(BOX, "a", now=clock[0])

        r = bq.take(BOX, "b", self.me, wait_s=60, poll_s=10,
                    now_fn=lambda: clock[0], sleep_fn=sleep)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.state()["state"], "held:b")


class TestLeaseAndLiveness(_Base):
    def test_expired_lease_is_reclaimed_as_dirty(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, lease_s=60, now_fn=lambda: 1000.0)
        st = bq.status(BOX, now=1061.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertIsNone(st["holder"])
        self.assertIn("lease expired", st["reason"])
        self.assertTrue(any("RECLAIM" in ln for ln in self.log_lines()))

    def test_renew_extends_lease(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, lease_s=60, now_fn=lambda: 1000.0)
        self.assertTrue(bq.renew(BOX, "a", lease_s=60, now=1050.0)["ok"])
        self.assertEqual(bq.status(BOX, now=1100.0)["state"], "held:a")

    def test_renew_after_expiry_fails_and_release_cannot_launder_it(self):
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, lease_s=60, now_fn=lambda: 1000.0)
        self.assertFalse(bq.renew(BOX, "a", now=1100.0)["ok"])
        r = bq.release(BOX, "a", now=1101.0)
        self.assertFalse(r["ok"])
        self.assertEqual(self.state()["state"], "dirty:a")

    def test_max_hold_bounds_a_renew_loop(self):
        self.write_config(refresh="true", lease_s=60, max_hold_s=300)
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1000.0)
        t = 1000.0
        while t < 1250.0:
            t += 50.0
            self.assertTrue(bq.renew(BOX, "a", now=t)["ok"], t)
        self.assertFalse(bq.renew(BOX, "a", now=1301.0)["ok"])
        self.assertEqual(self.state()["state"], "dirty:a")
        self.assertIn("max hold", self.state()["reason"])

    def test_dead_holder_pid_is_reclaimed_as_dirty(self):
        dead = dead_pid()
        bq.enqueue(BOX, "a", dead, now=1000.0)
        st = bq.status(BOX, now=1000.0)
        self.assertEqual(st["queue"], [])   # a dead-pid queue entry is dropped
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1000.0)
        raw = self.state()
        raw["holder"]["pid"] = dead
        (self.dir / f"{BOX}.json").write_text(json.dumps(raw))
        st = bq.status(BOX, now=1001.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertIn("pid", st["reason"])

    def test_stale_queue_entry_is_dropped_so_it_cannot_block_the_head(self):
        self.write_config(refresh="true", queue_ttl_s=100)
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.enqueue(BOX, "b", self.me, now=1150.0)
        r = bq.take(BOX, "b", self.me, now_fn=lambda: 1160.0)
        self.assertTrue(r["ok"], r)

    def test_corrupt_state_file_is_treated_as_dirty_never_pristine(self):
        self.dir.mkdir(parents=True)
        (self.dir / f"{BOX}.json").write_text("{not json")
        st = bq.status(BOX, now=1000.0)
        self.assertTrue(st["state"].startswith("dirty:"), st)


class TestRefresh(_Base):
    def test_release_dirty_then_refresh_runs_config_and_goes_pristine(self):
        marker = Path(self._tmp.name) / "refreshed"
        refresh = self.script("refresh.sh", f"echo ran >> {marker}\n")
        health = self.script("health.sh", "exit 0\n")
        self.write_config(refresh=[refresh], health=[health])
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1000.0)
        bq.release(BOX, "a", dirty=True, now=1001.0)
        r = bq.refresh(BOX, now_fn=time.time)
        self.assertTrue(r["ok"], r)
        self.assertEqual(marker.read_text(), "ran\n")
        st = self.state()
        self.assertEqual(st["state"], "pristine")
        self.assertEqual(st["reason"], "")
        events = " ".join(self.log_lines())
        self.assertIn("REFRESH-START", events)
        self.assertIn("REFRESH-OK", events)

    def test_failing_refresh_stays_dirty_with_logged_reason(self):
        refresh = self.script("refresh.sh", "echo boom >&2\nexit 3\n")
        self.write_config(refresh=[refresh])
        bq.enqueue(BOX, "a", self.me, now=1000.0)
        bq.take(BOX, "a", self.me, now_fn=lambda: 1000.0)
        bq.release(BOX, "a", dirty=True, now=1001.0)
        r = bq.refresh(BOX, now_fn=time.time)
        self.assertFalse(r["ok"])
        st = self.state()
        self.assertEqual(st["state"], "dirty:a")
        self.assertIn("rc=3", st["reason"])
        self.assertTrue(any("REFRESH-FAIL" in ln and "rc=3" in ln
                            for ln in self.log_lines()))

    def test_failing_health_stays_dirty(self):
        refresh = self.script("refresh.sh", "exit 0\n")
        health = self.script("health.sh", "exit 1\n")
        self.write_config(refresh=[refresh], health=[health],
                          health_timeout_s=1, health_interval_s=0.2)
        raw = {"version": 1, "box": BOX, "state": "dirty:a", "queue": [],
               "holder": None, "refresh": None, "reason": "x",
               "updated_at": 1000.0}
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{BOX}.json").write_text(json.dumps(raw))
        r = bq.refresh(BOX, now_fn=time.time)
        self.assertFalse(r["ok"])
        self.assertEqual(self.state()["state"], "dirty:a")
        self.assertIn("health", self.state()["reason"])

    def test_refresh_refused_while_held(self):
        marker = Path(self._tmp.name) / "refreshed"
        refresh = self.script("refresh.sh", f"touch {marker}\n")
        self.write_config(refresh=[refresh])
        bq.enqueue(BOX, "a", self.me, now=time.time())
        bq.take(BOX, "a", self.me, now_fn=time.time)
        r = bq.refresh(BOX, force=True, now_fn=time.time)
        self.assertFalse(r["ok"])
        self.assertIn("held", r["msg"])
        self.assertFalse(marker.exists())
        self.assertEqual(self.state()["state"], "held:a")

    def test_take_refused_while_refreshing(self):
        raw = {"version": 1, "box": BOX, "state": "refreshing", "queue": [],
               "holder": None,
               "refresh": {"token": "t", "pid": self.me, "started_at": 1000.0,
                           "deadline": 99999.0, "from": "dirty:a"},
               "reason": "", "updated_at": 1000.0}
        self.dir.mkdir(parents=True)
        (self.dir / f"{BOX}.json").write_text(json.dumps(raw))
        r = bq.take(BOX, "b", self.me, now_fn=lambda: 1001.0)
        self.assertFalse(r["ok"])
        self.assertIn("refreshing", r["msg"])

    def test_interrupted_refresh_is_reclaimed_as_dirty(self):
        raw = {"version": 1, "box": BOX, "state": "refreshing", "queue": [],
               "holder": None,
               "refresh": {"token": "t", "pid": dead_pid(), "started_at": 1000.0,
                           "deadline": 99999.0, "from": "dirty:a"},
               "reason": "", "updated_at": 1000.0}
        self.dir.mkdir(parents=True)
        (self.dir / f"{BOX}.json").write_text(json.dumps(raw))
        st = bq.status(BOX, now=1001.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertIn("refresh", st["reason"])

    def test_refresh_without_config_is_a_usage_error(self):
        raw = {"version": 1, "box": BOX, "state": "dirty:a", "queue": [],
               "holder": None, "refresh": None, "reason": "", "updated_at": 1.0}
        self.dir.mkdir(parents=True)
        (self.dir / f"{BOX}.json").write_text(json.dumps(raw))
        with self.assertRaises(bq.BoxQueueError):
            bq.refresh(BOX, now_fn=time.time)
        self.assertEqual(self.state()["state"], "dirty:a")


class TestCliAndBoundaries(_Base):
    def test_box_name_path_traversal_refused(self):
        r = run_cli(["enqueue", "--box", "../../evil", "--lane", "a",
                     "--pid", str(self.me)], self.env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse((Path(self._tmp.name) / "evil.json").exists())

    def test_lane_with_newline_refused(self):
        r = run_cli(["enqueue", "--box", BOX, "--lane", "a\nFORGED",
                     "--pid", str(self.me)], self.env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_cli_exit_codes_and_status_json(self):
        pid = str(self.me)
        self.assertEqual(run_cli(["enqueue", "--box", BOX, "--lane", "a",
                                  "--pid", pid], self.env).returncode, 0)
        self.assertEqual(run_cli(["take", "--box", BOX, "--lane", "b",
                                  "--pid", pid], self.env).returncode, 1)
        self.assertEqual(run_cli(["take", "--box", BOX, "--lane", "a",
                                  "--pid", pid], self.env).returncode, 0)
        r = run_cli(["status", "--box", BOX, "--json"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual(st["state"], "held:a")
        self.assertEqual(run_cli(["release", "--box", BOX, "--lane", "a",
                                  "--dirty"], self.env).returncode, 0)
        self.assertEqual(json.loads(run_cli(["status", "--box", BOX, "--json"],
                                            self.env).stdout)["state"], "dirty:a")

    def test_three_concurrent_enqueues_lose_nothing_and_keep_fifo(self):
        code = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r)
            import time, cli_box_queue as bq
            tag, pid = sys.argv[1], int(sys.argv[2])
            for i in range(25):
                bq.enqueue(%r, "%%s-%%02d" %% (tag, i), pid, now=time.time())
        """) % (str(REPO), BOX)
        procs = [subprocess.Popen([sys.executable, "-c", code, tag, str(self.me)],
                                  env=self.env) for tag in ("x", "y", "z")]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        q = self.state()["queue"]
        lanes = [e["lane"] for e in q]
        self.assertEqual(len(lanes), 75)
        self.assertEqual(len(set(lanes)), 75)
        for tag in ("x", "y", "z"):
            mine = [ln for ln in lanes if ln.startswith(tag)]
            self.assertEqual(mine, sorted(mine))  # each writer's own order kept
        stamps = [e["enqueued_at"] for e in q]
        self.assertEqual(stamps, sorted(stamps))

    def test_concurrent_takes_on_a_pristine_box_have_exactly_one_winner(self):
        pid = str(self.me)
        procs = [subprocess.Popen(
            [sys.executable, str(REPO / "airuleset.py"), "box-queue", "take",
             "--box", BOX, "--lane", f"lane{i}", "--pid", pid],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(3)]
        rcs = [p.wait(timeout=60) for p in procs]
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(sorted(rcs), [0, 1, 1])
        self.assertTrue(self.state()["state"].startswith("held:lane"))

    def test_tool_never_writes_outside_its_state_dir(self):
        home = Path(self._tmp.name) / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh" / "config").write_text(
            "Host dev2\n  HostName 100.82.64.27\nHost mac-mini\n  HostName 10.0.0.9\n")
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").write_text("{}")
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        refresh = self.script("refresh.sh", f"echo ok > {outside}/ran\n")
        env = dict(self.env)
        env.pop("AIRULESET_BOX_QUEUE_DIR", None)
        env["HOME"] = str(home)
        qdir = home / ".claude" / "box-queue"
        qdir.mkdir()
        (qdir / f"{BOX}.config.json").write_text(json.dumps({"refresh": [refresh]}))

        def snapshot():
            out = {}
            for p in sorted(home.rglob("*")):
                if qdir in p.parents or p == qdir:
                    continue
                st = p.lstat()
                out[str(p)] = (st.st_mtime_ns, p.read_bytes() if p.is_file() else None)
            return out

        before = snapshot()
        pid = str(self.me)
        for args in (["enqueue", "--box", BOX, "--lane", "a", "--pid", pid],
                     ["take", "--box", BOX, "--lane", "a", "--pid", pid],
                     ["renew", "--box", BOX, "--lane", "a"],
                     ["release", "--box", BOX, "--lane", "a", "--dirty"],
                     ["refresh", "--box", BOX],
                     ["status", "--box", BOX],
                     ["status"]):
            r = run_cli(args, env)
            self.assertIn(r.returncode, (0,), f"{args}: {r.stdout}{r.stderr}")
        self.assertEqual(snapshot(), before)
        self.assertEqual((outside / "ran").read_text(), "ok\n")
        self.assertEqual(json.loads((qdir / f"{BOX}.json").read_text())["state"],
                         "pristine")


class TestSkillContract(TestCase):
    def test_worker_contract_and_skill_name_box_queue(self):
        for rel in ("skills/autopilot/SKILL.md", "agents/autopilot-worker.md"):
            text = (REPO / rel).read_text()
            for needle in ("box-queue take", "box-queue release"):
                self.assertIn(needle, text, rel)
        self.assertIn("box-queue refresh",
                      (REPO / "skills/autopilot/SKILL.md").read_text())

    def test_subcommand_registered(self):
        import airuleset
        self.assertIs(airuleset.SUBCOMMANDS["box-queue"], bq.cmd_box_queue)


if __name__ == "__main__":
    main()
