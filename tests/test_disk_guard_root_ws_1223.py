"""#1223 — the sudo workstations (dev1, dev2) get the #841 root leg.

dev1 hit 90 % with the per-user drain exhausted while its system journal sat
at 4 GB with no cap: `DISK_GUARD_ROOT_HOSTS` named only subdev and gk, so the
journald cap, the btmp/wtmp rotation and the root report never reached the
workstations. A workstation is reached as `newlevel` with `sudo -n` (no root
ssh) and gets the ROTATE + SURFACE legs only — never the #895 cross-user
drain, whose rung (d) deletes a `*.tgz` anywhere under /home that sits next
to a same-stem directory (a project fixture or backup under ~/devel).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_disk_guard_root as g   # noqa: E402
import cli_fleet                  # noqa: E402
import cli_key_rotation           # noqa: E402

PUSH_KEY = "~/.secrets/airuleset_push_ed25519"
WORKSTATIONS = {"dev1": "100.104.8.125", "dev2": "100.82.64.27"}
DRAIN_PATHS = {g.DRAIN_SCRIPT_PATH, g.DRAIN_SERVICE_PATH, g.DRAIN_TIMER_PATH}
DRAIN_UNIT = "airuleset-disk-guard-root-drain.timer"


def _recorder(calls):
    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    return fake_run


class TestWorkstationTargets(unittest.TestCase):

    def test_dev1_and_dev2_are_sudo_targets_without_the_drain(self):
        by_name = {h["name"]: h for h in cli_fleet.DISK_GUARD_ROOT_HOSTS}
        for name, ip in WORKSTATIONS.items():
            self.assertIn(name, by_name)
            h = by_name[name]
            self.assertEqual(h["host"], ip)
            self.assertEqual(h["admin_user"], "newlevel")
            self.assertIs(h["sudo"], True)
            self.assertIs(h["drain"], False)
            self.assertEqual(h["identity"], PUSH_KEY)

    def test_public_boxes_keep_root_and_the_drain(self):
        for h in cli_fleet.DISK_GUARD_ROOT_HOSTS:
            if h["name"] in WORKSTATIONS:
                continue
            self.assertEqual(h["admin_user"], "root")
            self.assertNotIn("sudo", h)
            self.assertNotIn("drain", h)

    def test_no_new_key_rotation_target(self):
        # newlevel@dev1/dev2 are already REMOTE_HOSTS keys, so the guard loop
        # dedups them: no workstation guard entry is ever yielded.
        for _hk, entry, _hop in cli_key_rotation._rotation_targets(
                include_dev1=True):
            self.assertNotIn("sudo", entry)


class TestWorkstationScript(unittest.TestCase):

    def test_guard_files_without_the_drain(self):
        full = [p for p, _c, _m in g.guard_files()]
        ws = [p for p, _c, _m in g.guard_files(drain=False)]
        self.assertEqual(ws, [p for p in full if p not in DRAIN_PATHS])
        self.assertIn(g.JOURNALD_CAP_PATH, ws)
        self.assertIn(g.ROOT_TIMER_PATH, ws)

    def test_apply_script_without_the_drain(self):
        s = g.build_apply_script(drain=False)
        for p in DRAIN_PATHS:
            self.assertNotIn(p, s)
        self.assertNotIn(DRAIN_UNIT, s)
        self.assertIn("SystemMaxUse=%s" % g.JOURNAL_MAX_USE, s)
        self.assertIn("enable --now airuleset-disk-guard-root.timer", s)
        self.assertIn("exit 4", s)

    def test_default_script_is_the_full_leg(self):
        self.assertEqual(g.build_apply_script(), g.build_apply_script(drain=True))
        self.assertIn(DRAIN_UNIT, g.build_apply_script())


class TestReviewFixes(unittest.TestCase):
    """Reviews of 1223 + the owner's 2G answer (ROZHODNUTÉ 14.10.2026)."""

    def test_workstations_keep_a_2g_journal(self):
        by_name = {h["name"]: h for h in cli_fleet.DISK_GUARD_ROOT_HOSTS}
        for name in WORKSTATIONS:
            self.assertEqual(by_name[name]["journal_max"], "2G")
        s = g.build_apply_script(drain=False, journal_max="2G")
        self.assertIn("SystemMaxUse=2G", s)
        self.assertNotIn("SystemMaxUse=%s" % g.JOURNAL_MAX_USE, s)
        self.assertIn("SystemMaxUse=%s" % g.JOURNAL_MAX_USE, g.build_apply_script())

    def test_provision_passes_the_host_journal_cap(self):
        calls = []
        hosts = [{"name": "dev2", "host": "100.82.64.27", "admin_user": "newlevel",
                  "sudo": True, "journal_max": "2G", "identity": PUSH_KEY}]
        g.provision_disk_guard_root(hosts=hosts, run=_recorder(calls))
        self.assertIn("SystemMaxUse=2G", calls[0][-1])

    def test_a_sudo_host_never_gets_the_drain_by_omission(self):
        calls = []
        hosts = [{"name": "ws", "host": "1.2.3.4", "admin_user": "u", "sudo": True,
                  "identity": PUSH_KEY}]
        g.provision_disk_guard_root(hosts=hosts, run=_recorder(calls))
        self.assertNotIn(DRAIN_UNIT, calls[0][-1])

    def test_the_seed_runs_only_on_a_missing_or_stale_report_and_niced(self):
        s = g.build_apply_script()
        seed = [ln for ln in s.splitlines() if g.REPORTER_SCRIPT_PATH in ln
                and "timeout" in ln]
        self.assertEqual(len(seed), 1, seed)
        self.assertIn("ionice -c3 nice", seed[0])
        self.assertIn("-mmin -1200", s)

    def test_journald_restarts_only_when_the_cap_changed(self):
        s = g.build_apply_script()
        restart = s.index("try-restart systemd-journald")
        guard = s.rfind("if [ -n \"$JOURNALD_CHANGED\" ]", 0, restart)
        self.assertNotEqual(guard, -1)
        self.assertIn("cmp -s", s)

    def test_the_duplicate_entry_check_reads_all_of_logrotate(self):
        s = g.build_apply_script()
        self.assertNotIn('grep -qi "duplicate log entry"', s)
        self.assertIn('grep -i "duplicate log entry" >/dev/null', s)


class TestWorkstationProvision(unittest.TestCase):

    def test_sudo_host_runs_the_drainless_script_under_sudo_n(self):
        calls = []
        hosts = [{"name": "dev1", "host": "100.104.8.125",
                  "admin_user": "newlevel", "sudo": True, "drain": False,
                  "identity": PUSH_KEY}]
        self.assertEqual(
            g.provision_disk_guard_root(hosts=hosts, run=_recorder(calls)), [])
        argv = calls[0]
        self.assertIn("newlevel@100.104.8.125", argv)
        self.assertTrue(argv[-1].startswith("sudo -n bash -c "), argv[-1][:40])
        self.assertNotIn(DRAIN_UNIT, argv[-1])
        self.assertIn(g.JOURNALD_CAP_PATH, argv[-1])

    def test_root_host_is_unchanged(self):
        calls = []
        hosts = [{"name": "gatekeeper", "host": "5.6.7.8",
                  "admin_user": "root", "identity": "~/.secrets/k"}]
        g.provision_disk_guard_root(hosts=hosts, run=_recorder(calls))
        argv = calls[0]
        self.assertIn("root@5.6.7.8", argv)
        self.assertTrue(argv[-1].startswith("bash -c "), argv[-1][:40])
        self.assertIn(DRAIN_UNIT, argv[-1])


if __name__ == "__main__":
    unittest.main()
