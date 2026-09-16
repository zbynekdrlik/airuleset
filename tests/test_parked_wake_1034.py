"""#1034 — Job 48: wake a Claude Code session parked on the usage-limit
auto-continue banner after a claudy ACCOUNT SWITCH.

The gap this closes: with `autoContinueAtUsageLimit: true` a limit-hit pane parks
on `Usage limit reached · continuing automatically at <cas>` (and, after a retry
re-hits the limit, `... the automatic-continue setting no longer ends this wait`)
and waits for the ORIGINAL account's reset clock. When claudy switches the box's
on-disk account (`~/.claude.json` oauthAccount.emailAddress) to a free one,
neither existing recovery path re-fires `continue`: job 1 stays dormant for a
usage cap, job 6 waits for the original reset. This job wakes it EARLY, keyed on
the account-email change.

Dependency-injected for a tmux/network-free unit test (the model_float_audit_job
template): the job takes `account_email`/`find_transcript`/`capture`/`is_parked`/
`in_mode`/`recent_human`/`deliver` callables; `deliver_wake` takes `keys_fn`/
`send_verified_fn`. NEVER pings the owner — machine-channel journal only.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watchdog  # noqa: E402
from watchdog.decide import pane_auto_continue_parked  # noqa: E402
from watchdog.parked_wake import (  # noqa: E402
    WAKE_PARKED_NUDGE,
    deliver_wake,
    parked_wake_job,
)


# --- fixtures -------------------------------------------------------------- #

# Real Claude Code 2.1.268 banner forms (grepped live from the installed CLI).
BANNER_AUTOCONTINUE = (
    "* Working on it...\n"
    "------------------------------------------\n"
    "Usage limit reached  continuing automatically at 9:50am  esc or type to cancel\n"
    "> \n"
)
BANNER_NO_LONGER_ENDS = (
    "Usage limit reached again after you continued.\n"
    "The automatic-continue setting no longer ends this wait.\n"
    "> \n"
)
BANNER_CONTINUING_ONLY = "Continuing automatically at 9:50am  esc to cancel\n> \n"
NOT_PARKED = "* pokracujem v praci\n> \n"
# a stale echo scrolled HIGH with fresh work underneath — must NOT count (the
# bottom-scoping discipline pane_session_limited established, gk 2026-07-24)
STALE_ECHO_HIGH = (
    "> Usage limit reached  continuing automatically at 9:50am\n"
    + "\n".join("* riadok %d prace" % i for i in range(1, 15))
    + "\n> \n"
)


def _find(cwd_to_tpath):
    def find(projects_dir, cwd):
        return (cwd_to_tpath[cwd], 0.0) if cwd in cwd_to_tpath else None
    return find


def _job(panes, *, cur_email, caps, state=None, tpaths=None,
         in_mode=False, recent_human=False, dry_run=False, deliver_ok=True):
    """Run parked_wake_job with fakes; return (out, state, delivered) where
    `delivered` is the list of (pid, tpath) the injected deliver was called with."""
    state = {} if state is None else state
    tpaths = tpaths or {cwd: "/t/%s.jsonl" % cwd.strip("/") for _, cwd in panes}
    delivered = []

    def deliver(pid, tpath):
        delivered.append((pid, tpath))
        return deliver_ok

    out = parked_wake_job(
        100.0, state, panes, "/proj",
        account_email=lambda: cur_email,
        find_transcript=_find(tpaths),
        capture=lambda pid: caps.get(pid, ""),
        is_parked=pane_auto_continue_parked,
        in_mode=lambda pid: in_mode,
        recent_human=lambda sid, cwd, tpath, pid: recent_human,
        deliver=deliver,
        dry_run=dry_run,
    )
    return out, state, delivered


class TestParkedBannerDetector(TestCase):
    def test_detects_every_real_autocontinue_form(self):
        for cap in (BANNER_AUTOCONTINUE, BANNER_NO_LONGER_ENDS, BANNER_CONTINUING_ONLY):
            self.assertTrue(pane_auto_continue_parked(cap), cap)

    def test_ignores_bare_pane_and_empty(self):
        self.assertFalse(pane_auto_continue_parked(NOT_PARKED))
        self.assertFalse(pane_auto_continue_parked(""))
        self.assertFalse(pane_auto_continue_parked(None))

    def test_ignores_stale_echo_scrolled_high(self):
        # bottom-scoped: a banner echo far above fresh work is not "still parked"
        self.assertFalse(pane_auto_continue_parked(STALE_ECHO_HIGH))


class TestParkedWakeJob(TestCase):
    def test_first_observation_records_email_no_send(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        out, state, delivered = _job(panes, cur_email="a@x.bid", caps=caps)
        self.assertEqual(delivered, [], out)
        self.assertEqual(state["parked_wake"]["repo"]["email"], "a@x.bid")
        self.assertFalse(any(" -> " in ln for ln in out), out)

    def test_email_changed_sends_esc_continue_and_logs(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="new@y.bid", caps=caps, state=state)
        self.assertEqual(delivered, [("%1", "/t/repo.jsonl")], out)
        self.assertTrue(any("wake-parked: %1 old@x.bid -> new@y.bid" in ln for ln in out), out)
        # parked mark cleared after a successful wake
        self.assertNotIn("repo", state["parked_wake"])

    def test_same_email_sends_nothing_no_log(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "same@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="same@x.bid", caps=caps, state=state)
        self.assertEqual(delivered, [])
        self.assertEqual(out, [])
        # mark preserved (still parked, still same account)
        self.assertIn("repo", state["parked_wake"])

    def test_banner_gone_clears_parked_mark(self):
        panes = [("%1", "/repo")]
        caps = {"%1": NOT_PARKED}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="new@y.bid", caps=caps, state=state)
        self.assertEqual(delivered, [])
        self.assertNotIn("repo", state["parked_wake"])

    def test_recent_human_holds_no_send(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="new@y.bid", caps=caps,
                                     state=state, recent_human=True)
        self.assertEqual(delivered, [], out)
        # NOT cleared — retry next sweep once the human goes quiet
        self.assertIn("repo", state["parked_wake"])
        self.assertTrue(any("recent-human" in ln for ln in out), out)

    def test_in_mode_holds_no_send(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="new@y.bid", caps=caps,
                                     state=state, in_mode=True)
        self.assertEqual(delivered, [])
        self.assertIn("repo", state["parked_wake"])

    def test_failed_deliver_keeps_mark_for_retry(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="new@y.bid", caps=caps,
                                     state=state, deliver_ok=False)
        self.assertEqual(delivered, [("%1", "/t/repo.jsonl")])
        self.assertIn("repo", state["parked_wake"])  # kept → retried
        # no SUCCESS line on a failed submit
        self.assertFalse(any(ln == "wake-parked: %1 old@x.bid -> new@y.bid" for ln in out), out)

    def test_dead_pane_state_pruned(self):
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        # a stale sid for a pane that no longer exists this sweep
        state = {"parked_wake": {"repo": {"email": "a@x.bid", "first_seen": 1},
                                 "gonesid": {"email": "z@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="a@x.bid", caps=caps, state=state)
        self.assertNotIn("gonesid", state["parked_wake"])

    def test_unreadable_email_does_nothing(self):
        # account_email "" (unreadable ~/.claude.json) → never fabricate a switch
        panes = [("%1", "/repo")]
        caps = {"%1": BANNER_AUTOCONTINUE}
        state = {"parked_wake": {"repo": {"email": "old@x.bid", "first_seen": 1}}}
        out, state, delivered = _job(panes, cur_email="", caps=caps, state=state)
        self.assertEqual(delivered, [])


class TestDeliverWake(TestCase):
    def test_sends_escape_then_continue_recovery_kind(self):
        calls = []

        def keys_fn(pane, *ks, kind=None, nudge=None, run=None, logs=None):
            calls.append(("keys", pane, ks, kind, nudge))
            return True

        def sv_fn(pane, text, run=None, tpath=None, sleep_fn=None, logs=None, nudge=None):
            calls.append(("send_verified", pane, text, nudge))
            return True

        ok = deliver_wake("%1", "/t/repo.jsonl", run=None, sleep_fn=None, logs=[],
                          keys_fn=keys_fn, send_verified_fn=sv_fn)
        self.assertTrue(ok)
        # Escape FIRST (cancel the auto-continue wait), then the continue submit
        self.assertEqual(calls[0][0], "keys")
        self.assertIn("Escape", calls[0][2])
        self.assertEqual(calls[0][4], WAKE_PARKED_NUDGE)
        self.assertEqual(calls[1][0], "send_verified")
        self.assertEqual(calls[1][2], watchdog.NUDGE_TEXT)  # "continue"
        self.assertEqual(calls[1][3], WAKE_PARKED_NUDGE)

    def test_dry_run_sends_nothing_returns_true(self):
        calls = []
        ok = deliver_wake("%1", "/t/r.jsonl", run=None, logs=[],
                          keys_fn=lambda *a, **k: calls.append(a) or True,
                          send_verified_fn=lambda *a, **k: calls.append(a) or True,
                          dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_wake_kind_is_always_on_recovery(self):
        self.assertIn(WAKE_PARKED_NUDGE, watchdog.tmux_io.RECOVERY_NUDGE_KINDS)
        import watchdog.nudge_gate as ng
        self.assertIn(WAKE_PARKED_NUDGE, ng.RECOVERY_NUDGE_KINDS)
        # and the always-on predicate agrees
        self.assertTrue(watchdog.nudges_enabled(WAKE_PARKED_NUDGE))


if __name__ == "__main__":
    main()
