"""#1361: `box-queue` records WHY a box is dirty in a structured
`dirty_kind` field, so a reader (odoo-erp `erp_test_lease.py reset-acquire`)
no longer parses the free-text `reason` to tell a voluntary release from a
reclaim. Contract:

  dirty_kind  "released"        a holder's voluntary `release` (no --clean)
              "reclaimed"       a dead / lapsed / over-max-hold holder taken over
              "refresh-failed"  a refresh that did not complete (failed,
                                interrupted, or a stale refreshing record)
              ""                pristine, or unknown provenance (a corrupt
                                state file, an old file without the key)

The free-text `reason` is unchanged (human context only).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_box_queue as bq  # noqa: E402

BOX = "erp-test-demo"


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class _Base(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "box-queue"
        self._old = os.environ.get("AIRULESET_BOX_QUEUE_DIR")
        os.environ["AIRULESET_BOX_QUEUE_DIR"] = str(self.dir)
        self.me = os.getpid()
        self.write_config(refresh="true")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AIRULESET_BOX_QUEUE_DIR", None)
        else:
            os.environ["AIRULESET_BOX_QUEUE_DIR"] = self._old
        self._tmp.cleanup()

    def write_config(self, **cfg):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{BOX}.config.json").write_text(json.dumps(cfg))

    def state_path(self):
        return self.dir / f"{BOX}.json"

    def state(self):
        return json.loads(self.state_path().read_text())

    def hold(self, lane="a", t=1000.0):
        r = bq.take(BOX, lane, self.me, now_fn=lambda: t)
        self.assertTrue(r["ok"], r)


class TestDirtyKindTransitions(_Base):
    def test_fresh_box_has_empty_dirty_kind(self):
        self.assertEqual(bq.status(BOX, now=1000.0)["dirty_kind"], "")

    def test_voluntary_release_is_released(self):
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        st = self.state()
        self.assertEqual(st["state"], "dirty:a")
        self.assertEqual(st["dirty_kind"], "released")
        self.assertEqual(st["reason"], "released dirty by a")

    def test_release_with_custom_reason_is_still_released(self):
        self.hold()
        bq.release(BOX, "a", reason="deployed module x", now=1001.0)
        st = self.state()
        self.assertEqual(st["dirty_kind"], "released")
        self.assertEqual(st["reason"], "deployed module x")

    def test_clean_release_stays_empty(self):
        self.hold()
        bq.release(BOX, "a", clean=True, now=1001.0)
        st = self.state()
        self.assertEqual(st["state"], "pristine")
        self.assertEqual(st["dirty_kind"], "")

    def test_expired_lease_is_reclaimed(self):
        self.hold()
        st = bq.status(BOX, now=1000.0 + 10 * 3600)
        self.assertTrue(st["state"].startswith("dirty:"), st)
        self.assertEqual(st["dirty_kind"], "reclaimed")
        self.assertTrue(st["reason"].startswith("reclaimed: "))

    def test_dead_holder_is_reclaimed(self):
        dead = _dead_pid()
        r = bq.take(BOX, "a", dead, now_fn=lambda: 1000.0)
        self.assertTrue(r["ok"], r)
        st = bq.status(BOX, now=1001.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertEqual(st["dirty_kind"], "reclaimed")

    def test_failed_refresh_is_refresh_failed(self):
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        self.write_config(refresh="false")
        r = bq.refresh(BOX)
        self.assertFalse(r["ok"], r)
        st = self.state()
        self.assertEqual(st["state"], "dirty:a")
        self.assertEqual(st["dirty_kind"], "refresh-failed")
        self.assertTrue(st["reason"].startswith("refresh failed: "))

    def test_successful_refresh_clears_to_empty(self):
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        r = bq.refresh(BOX)
        self.assertTrue(r["ok"], r)
        st = self.state()
        self.assertEqual(st["state"], "pristine")
        self.assertEqual(st["dirty_kind"], "")

    def test_lost_refresh_is_refresh_failed(self):
        # A `refreshing` record whose refresh lock is free (every process of
        # the refresh gone) is reaped back to dirty.
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        st = self.state()
        st["state"] = "refreshing"
        st["refresh"] = {"token": "t", "pid": _dead_pid(), "started_at": 1002.0,
                         "deadline": 99999.0, "from": "dirty:a"}
        self.state_path().write_text(json.dumps(st))
        st = bq.status(BOX, now=1003.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertEqual(st["dirty_kind"], "refresh-failed")

    def test_retired_stale_refresh_record_is_refresh_failed(self):
        # refresh() over a `refreshing` record whose lock is free retires it
        # (its earlier refresh died) — the box is dirty because that refresh
        # never completed. Snapshot the state from INSIDE the new refresh.
        snap = Path(self._tmp.name) / "snap.json"
        self.write_config(refresh=f"cp {self.state_path()} {snap}")
        st = {"version": 1, "box": BOX, "state": "refreshing", "queue": [],
              "holder": None, "reason": "released dirty by a",
              "dirty_kind": "released", "updated_at": 1.0,
              "refresh": {"token": "old", "pid": self.me, "started_at": 1.0,
                          "deadline": 9e12, "from": "dirty:a"}}
        self.state_path().write_text(json.dumps(st))
        self.assertTrue(bq.refresh(BOX)["ok"])
        mid = json.loads(snap.read_text())
        self.assertEqual(mid["state"], "refreshing")
        self.assertEqual(mid["dirty_kind"], "refresh-failed")
        self.assertEqual(mid["reason"], "released dirty by a")
        self.assertEqual(self.state()["dirty_kind"], "")

    def test_take_after_refresh_keeps_empty(self):
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        bq.refresh(BOX)
        self.hold("b", t=1002.0)
        self.assertEqual(self.state()["dirty_kind"], "")

    def test_corrupt_state_is_unknown_empty(self):
        self.state_path().write_text("{not json")
        st = bq.status(BOX, now=1000.0)
        self.assertEqual(st["state"], "dirty:unknown")
        self.assertEqual(st["dirty_kind"], "")


class TestOldStateFileAndStatus(_Base):
    def test_old_state_file_without_field_reads_empty(self):
        old = {"version": 1, "box": BOX, "state": "dirty:a", "queue": [],
               "holder": None, "refresh": None,
               "reason": "released dirty by a", "updated_at": 1000.0}
        self.state_path().write_text(json.dumps(old))
        st = bq.status(BOX, now=1001.0)
        self.assertEqual(st["state"], "dirty:a")
        self.assertEqual(st["dirty_kind"], "")

    def test_non_string_dirty_kind_is_corruption(self):
        bad = {"version": 1, "box": BOX, "state": "dirty:a", "queue": [],
               "holder": None, "refresh": None, "reason": "",
               "dirty_kind": 7, "updated_at": 1000.0}
        self.state_path().write_text(json.dumps(bad))
        st = bq.status(BOX, now=1001.0)
        self.assertEqual(st["state"], "dirty:unknown")
        self.assertEqual(st["dirty_kind"], "")

    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "box-queue", *args],
            capture_output=True, text=True, timeout=60, env=dict(os.environ))

    def test_status_json_and_text_carry_dirty_kind(self):
        self.hold()
        bq.release(BOX, "a", now=1001.0)
        r = self._cli("status", "--box", BOX, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["dirty_kind"], "released")
        r = self._cli("status", "--box", BOX)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dirty_kind=released", r.stdout)

    def test_status_text_on_pristine_box_omits_kind(self):
        r = self._cli("status", "--box", BOX)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("dirty_kind=", r.stdout)

    def test_fresh_box_json_has_field(self):
        r = self._cli("status", "--box", BOX, "--json")
        self.assertEqual(json.loads(r.stdout)["dirty_kind"], "")


if __name__ == "__main__":
    main()
