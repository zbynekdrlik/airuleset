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


def assert_unwired_caller_did_not_sweep(case, module, tmpdir, **kw):
    """THE GUARD — a caller that knows nothing about job 29 must not sweep.

    Written as a free function taking the module under test so the SAME
    assertions can be pointed at a mutated `watchdog` (TheUnwiredGuardHasTeeth
    below). A guard that cannot be aimed at the regression it exists to catch
    cannot be shown to catch it.

    #153 finding 5 asserted `calls == []` on a list local to the test and never
    wired in, so it could not fail. #156 finding 4: the replacement read
    run_once's LOG, and the job is silent when nothing expired (`if not gone:
    return []`), so a REAL sweep against an empty store looked identical to no
    sweep at all — precisely the two states it exists to tell apart. The
    observable is now the artifact a sweep leaves WHATEVER it finds: the hour
    it claims in the persisted state.
    """
    import json
    proj = Path(tmpdir) / "projects"
    proj.mkdir(exist_ok=True)
    state_path = Path(tmpdir) / "state.json"
    logs = module.run_once(now=1_000_000.0, run=lambda argv, timeout=8: "",
                           send_fn=lambda *a, **k: None, projects_dir=proj,
                           state_path=state_path,
                           pending_prefix=str(Path(tmpdir) / "pending-"),
                           dry_run=False, **kw)
    case.assertEqual([ln for ln in logs if "vault-purge" in ln], [], logs)
    state = (json.loads(state_path.read_text())
             if state_path.exists() else {})
    case.assertNotIn("vault_purge_hour", state,
                     "an unwired caller swept the store and claimed the hour")


class RunOnceVaultWiring(unittest.TestCase):
    def _tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def _run(self, **kw):
        tmp = self._tmp()
        proj = Path(tmp) / "projects"
        proj.mkdir()
        self.state_path = Path(tmp) / "state.json"
        return wd.run_once(now=1_000_000.0, run=lambda argv, timeout=8: "",
                           send_fn=lambda *a, **k: None, projects_dir=proj,
                           state_path=self.state_path,
                           pending_prefix=str(Path(tmp) / "pending-"), **kw)

    def test_an_unwired_caller_sees_no_sweep(self):
        assert_unwired_caller_did_not_sweep(self, wd, self._tmp())

    def test_a_wired_sweep_that_finds_nothing_is_still_observable(self):
        # The positive control. Without it, "no artifact" is
        # indistinguishable from "the artifact is never written at all".
        import json
        swept = []
        self._run(dry_run=False, vault_purge=lambda: (swept.append(1) or []))
        self.assertEqual(len(swept), 1)
        self.assertIn("vault_purge_hour",
                      json.loads(self.state_path.read_text()),
                      "a real sweep must be observable even when the store is "
                      "empty, or the guard above has nothing to catch")

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


class TheUnwiredGuardHasTeeth(unittest.TestCase):
    """#156 finding 4 — the mutation the guard above must actually catch.

    "Someone hands `run_once` a live default" is the realistic regression: the
    store then gets swept on every poll on every box regardless of what the
    caller asked for. Against an EMPTY store that sweep produces no log line,
    so the shipped log-based assertion passed while it happened.

    This applies the mutation for real — `vault_purge=None` in run_once's own
    signature rewritten to a live sweep — and requires the guard to fail.
    """

    def _mutant(self):
        """`watchdog` re-imported with the live-default mutation applied."""
        import importlib.util
        import sys as _sys
        src_path = Path(wd.__file__)
        src = src_path.read_text()
        # (#182 re-pin: `reopen_fetch=None` was added after `log_fn=None` in
        # run_once's own signature — the anchor moved, not the guard.)
        # (batch-3 #172 re-pin: `time_fn=None, sweep_budget_s=None` were
        # added on a NEW trailing line after `reopen_fetch=None,` — the
        # closing `):` moved off this line entirely. Anchor spans both
        # lines now; the mutation itself still targets only `vault_purge`.)
        # (#160 re-pin: `backlog_fetch=None` was added on the SAME trailing
        # line as `sweep_budget_s=None`, right before the closing `):` —
        # the anchor moved again, the guard and the mutation target did not.)
        # (#312 re-pin: `progress_dir=None` was added on a NEW trailing
        # line after `backlog_fetch=None,` — the closing `):` moved off
        # this line again. The anchor and mutation target did not.)
        # (#440 re-pin: `questions_path=None` was added on the SAME trailing
        # line as `progress_dir=None`, right before the closing `):` — the
        # anchor moved again; the guard and the mutation target did not.)
        # (#461 re-pin: `owner_decision_fetch=None` was added on a NEW trailing
        # line after `questions_path=None,` — the closing `):` moved off this
        # line again. The anchor and mutation target did not.)
        # (#516 re-pin: `gk_selfservice_fetch=None` was added on the SAME
        # trailing line as `owner_decision_fetch=None`, right before the
        # closing `):` — the anchor moved again; the guard and the mutation
        # target did not.)
        # (#515 re-pin: `u_reconcile_clear=None` was added on a NEW trailing
        # line after `gk_selfservice_fetch=None,` — the closing `):` moved off
        # that line again. The anchor and mutation target did not.)
        # (#529 re-pin: `vault_backstop=None` was inserted right AFTER the
        # `vault_purge=None` mutation target on this same first line — the
        # anchor's first line grew; the mutation target `vault_purge=None`
        # itself is untouched.)
        # (#535 re-pin: `conformance_root=None` was added on the SAME trailing
        # line as `u_reconcile_clear=None`; the review-fix then added
        # `conformance_is_target=None` on a NEW trailing line, so the closing `):`
        # moved off that line again — the anchor's LAST line grew; the mutation
        # target `vault_purge=None` did not.)
        # (#543 re-pin: `conformance_hb_enabled=False` (job 35's dev1-only gate)
        # was appended AFTER `conformance_is_target=None` on that same LAST line,
        # so the closing `):` moved once more; the anchor's LAST line grew again,
        # the mutation target `vault_purge=None` is still untouched.)
        # (#547 re-pin: `ops_wait_fetch=None` was inserted on its own NEW line
        # right after the `backlog_fetch=None,` line — the anchor grew a middle
        # line; the mutation target `vault_purge=None` is untouched.)
        # (#714 re-pin: `i_members_fetch=None` was REMOVED (the job-20 nudge is
        # now a compact COUNT trigger that points the session at `slice-quals
        # --audit` instead of the watchdog fetching + naming the I members), so
        # `ops_wait_fetch=None,` now sits alone on that line; the anchor's middle
        # line shrank, the mutation target `vault_purge=None` is untouched.)
        # (#551 re-pin: `gkorphan_fetch=None` (job 36's fetch) was appended on
        # its own NEW trailing line, so the closing `):` moved off the
        # `conformance_hb_enabled=False` line — the anchor grew a last line;
        # the mutation target `vault_purge=None` is untouched.)
        # #707 re-pin: `owner_decision_fetch=None` was REMOVED (the #461
        # owner-decision digest class is retired — its run_once param and
        # registry entry are gone), so `gk_selfservice_fetch=None` now sits
        # alone on that line; the anchor shrank a middle line, the mutation
        # target `vault_purge=None` is untouched.
        # #795 re-pin: `questions_path=None` was REMOVED (the #368 daily
        # question re-ask class is retired — `reping_stale_questions` is
        # now a permanent no-op tombstone, and it was the ONLY consumer of
        # this param), so `progress_dir=None,` now sits alone on that line;
        # the anchor shrank a middle line, the mutation target
        # `vault_purge=None` is untouched.
        old = ("             vault_purge=None, vault_backstop=None, "
               "log_fn=None, reopen_fetch=None,\n"
               "             time_fn=None, sweep_budget_s=None, "
               "backlog_fetch=None,\n"
               "             ops_wait_fetch=None,\n"
               "             progress_dir=None,\n"
               "             gk_selfservice_fetch=None,\n"
               "             u_reconcile_clear=None, conformance_root=None,\n"
               "             conformance_is_target=None, "
               "conformance_hb_enabled=False,\n"
               # #570 re-pin: run_once gained gkorphan_handoff_fetch (the
               # comment-handoff pass's own wired=on fetch seam), so the
               # signature-lock anchor's LAST line moved (the #504/#547/#551
               # re-pin-on-every-signature-change discipline).
               # #616 re-pin: release_state_fetch=None (job 20's release-gap
               # rider seam) was appended on a NEW trailing line after
               # gkorphan_handoff_fetch=None, so the closing `):` moved off that
               # line again; the anchor's LAST line grew, the mutation target
               # vault_purge=None is untouched.
               # #733 re-pin: queue_fetch=None (job 20's gk queue-arrival
               # rider seam) joined release_state_fetch on the trailing line,
               # so the closing `):` moved along it; the anchor's LAST line
               # grew again, the mutation target vault_purge=None is untouched.
               # #776 re-pin: reaper_ps_fetch=None, reaper_kill_fn=None (job
               # 37's runaway shadow-ugrep OS-process reaper seams) were
               # appended on a NEW trailing line after queue_fetch, so the
               # closing `):` moved off the queue_fetch line onto the new one;
               # the anchor grew a last line, the mutation target
               # vault_purge=None is untouched.
               # #775 re-pin: resource_guard_gk_request=None (job 39's
               # VERIFY-ONLY shared-stream resource-guard gk-request seam) was
               # appended on a NEW trailing line after reaper_kill_fn, so the
               # closing `):` moved off the reaper line onto the new one; the
               # anchor grew a last line, the mutation target vault_purge=None
               # is untouched.
               # #797 re-pin: u_fetch=None (job 20's U-freshness reconcile
               # rider's tickets-status-cache read seam) was appended on a NEW
               # trailing line after resource_guard_gk_request, so the closing
               # `):` moved off that line onto the new one; the anchor grew a
               # last line, the mutation target vault_purge=None is untouched.
               "             gkorphan_fetch=None, gkorphan_handoff_fetch=None,\n"
               "             release_state_fetch=None, queue_fetch=None,\n"
               "             reaper_ps_fetch=None, reaper_kill_fn=None,\n"
               "             resource_guard_gk_request=None,\n"
               "             u_fetch=None):")
        self.assertIn(old, src, "the mutation target moved; re-pin it")
        # Mutate ONLY the guard's default (`vault_purge=None` ->
        # `vault_purge=lambda: []`) and keep every other param intact — a
        # truncating replacement would drop later params whose body
        # references sit outside a try/except (historically #461's own
        # `if owner_decision_fetch is not None:` guard, a param since removed
        # by #707; any bare registry gate closure has the same shape),
        # NameError-ing the
        # mutant for a reason unrelated to the guard under test. Preserving
        # the whole signature is both correct and future-proof.
        mutated = src.replace(
            old, old.replace("vault_purge=None", "vault_purge=lambda: []"), 1)
        self.assertNotEqual(mutated, src, "the mutation did not apply")

        name = "watchdog_live_default_mutant"
        spec = importlib.util.spec_from_loader(name, loader=None)
        mod = importlib.util.module_from_spec(spec)
        mod.__file__ = str(src_path)
        mod.__package__ = name
        _sys.modules[name] = mod
        self.addCleanup(_sys.modules.pop, name, None)
        exec(compile(mutated, str(src_path), "exec"), mod.__dict__)
        return mod

    def test_the_guard_fails_against_a_live_default(self):
        mod = self._mutant()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(AssertionError,
                               msg="the guard passed against a watchdog whose "
                                   "default sweeps the store on every poll — "
                                   "it has no teeth"):
            assert_unwired_caller_did_not_sweep(self, mod, tmp.name)

    def test_the_mutant_is_otherwise_a_working_watchdog(self):
        # Guards the mutation itself: a mutant that crashed on import would
        # make the test above pass for entirely the wrong reason.
        mod = self._mutant()
        self.assertTrue(callable(mod.run_once))
        self.assertEqual(mod.vault_purge_job(1_000_000.0, {}), [])


class CliInjection(unittest.TestCase):
    def test_cmd_watchdog_passes_the_real_store_sweep(self):
        self.assertTrue(callable(airuleset._watchdog_vault_purge))
        with m.patch("filedrop.vault.purge", return_value=["X"]) as p:
            self.assertEqual(airuleset._watchdog_vault_purge(), ["X"])
        self.assertEqual(p.call_count, 1)


if __name__ == "__main__":
    unittest.main()
