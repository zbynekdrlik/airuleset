"""Watchdog job 29 — the credential store's TTL must not depend on the CLI.

Adversarial-review finding #6 (MEDIUM) on issue #144. `purge()` ran from
exactly one place: the top of `cmd_secret`. The ordinary shape of this feature
is a one-off — request a credential, use it once, never run `airuleset.py
secret` on that box again — so the 8-hour keep never fired and the value sat
0600 on disk forever. That is precisely the property the channel exists to
provide ("tajomstvá nesmú ležať na disku donekonečna"), and it was unbacked.

Every managed box already runs the api-watchdog every 60s, which is where an
expiry that nobody has to remember belongs.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
import watchdog as wd                                   # noqa: E402


class VaultPurgeJob(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.calls = []

    def _purge(self, removed=()):
        def fn():
            self.calls.append(time.time())
            return list(removed)
        return fn

    def test_not_wired_means_not_run(self):
        # The "wired = on" convention every other injected job follows: a
        # caller that knows nothing about this job sees no behavior change.
        self.assertEqual(wd.vault_purge_job(1_000_000.0, self.state), [])
        self.assertEqual(self.state, {})

    def test_it_sweeps_and_reports_what_expired(self):
        logs = wd.vault_purge_job(1_000_000.0, self.state,
                                  purge_fn=self._purge(["OLD_PAT"]))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any("OLD_PAT" in ln for ln in logs), logs)

    def test_a_sweep_that_removed_nothing_is_silent(self):
        logs = wd.vault_purge_job(1_000_000.0, self.state, purge_fn=self._purge())
        self.assertEqual(logs, [])
        self.assertEqual(len(self.calls), 1)

    def test_at_most_once_per_hour(self):
        # One minute INTO its hour, per the repo's own hour-bucket lesson: at
        # 1_000_000 the offsets below cross a boundary and the test fails for
        # the fixture's reason rather than the code's.
        now = 277 * 3600 + 60.0
        wd.vault_purge_job(now, self.state, purge_fn=self._purge())
        wd.vault_purge_job(now + 60, self.state, purge_fn=self._purge())
        wd.vault_purge_job(now + 1800, self.state, purge_fn=self._purge())
        self.assertEqual(len(self.calls), 1)
        wd.vault_purge_job(now + 3700, self.state, purge_fn=self._purge())
        self.assertEqual(len(self.calls), 2)

    def test_dry_run_neither_sweeps_nor_claims_the_hour(self):
        now = 1_000_000.0
        logs = wd.vault_purge_job(now, self.state, purge_fn=self._purge(),
                                  dry_run=True)
        self.assertEqual(self.calls, [])
        self.assertTrue(any("dry-run" in ln for ln in logs), logs)
        wd.vault_purge_job(now, self.state, purge_fn=self._purge())
        self.assertEqual(len(self.calls), 1, "a dry run must not eat the hour")


class RunOnceVaultWiring(unittest.TestCase):
    def _run(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        proj.mkdir()
        return wd.run_once(now=1_000_000.0, run=lambda argv, timeout=8: "",
                           send_fn=lambda *a, **k: None, projects_dir=proj,
                           state_path=Path(tmp.name) / "state.json",
                           pending_prefix=str(Path(tmp.name) / "pending-"), **kw)

    def test_an_unwired_caller_sees_no_sweep(self):
        # #153 finding 5. This asserted `calls == []` on a list that was local
        # to the test and never wired into `_run` — it could not fail, and so
        # verified nothing. The observable it should have been reading is
        # run_once's OWN log output, which does change if an unwired caller
        # ever starts sweeping.
        logs = self._run(dry_run=False)
        self.assertEqual([ln for ln in logs if "vault-purge" in ln], [], logs)

    def test_the_job_runs_when_the_purge_callable_is_given(self):
        calls = []

        def purge():
            calls.append(1)
            return ["EXPIRED_ONE"]
        logs = self._run(dry_run=False, vault_purge=purge)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any("EXPIRED_ONE" in ln for ln in logs), logs)

    def test_a_failing_sweep_is_logged_and_never_breaks_the_poll(self):
        def purge():
            raise OSError("store unreadable")
        logs = self._run(dry_run=False, vault_purge=purge)
        self.assertTrue(any("vault-purge error" in ln for ln in logs), logs)


class CliInjection(unittest.TestCase):
    def test_cmd_watchdog_passes_the_real_store_sweep(self):
        self.assertTrue(callable(airuleset._watchdog_vault_purge))
        with m.patch("filedrop.vault.purge", return_value=["X"]) as p:
            self.assertEqual(airuleset._watchdog_vault_purge(), ["X"])
        self.assertEqual(p.call_count, 1)


if __name__ == "__main__":
    unittest.main()
