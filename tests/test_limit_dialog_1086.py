"""#1086 — Job 6 branch: dismiss Claude Code's INTERACTIVE usage-limit DIALOG
and resume the armed /goal once the box provably has capacity again (a claudy
credential switch), instead of waiting for the banner's reset time.

WHY (gk incident 2026-09-19): a hard weekly 429 on the gatekeeper's then-current
account rendered the INTERACTIVE dialog

    ● Goal paused · usage limit reached · send a message after it resets to continue
       What do you want to do?
       ❯ 1. Stop and wait for limit to reset
         2. Wait here, then continue automatically at Sep 20, 5pm
         3. Switch to usage credits
       Enter to confirm · Esc to cancel

claudy switched the box's credentials to a fresh account ~10 s later (the usage
cache already showed the new account at 5 %), but pane `zbynek:1` sat blocked
for 1.5 h+: job 6 only knows the plain BANNER (`pane_session_limited`) and waits
for the parsed reset epoch — the wrong signal once capacity is already back.

The detector (`decide.pane_limit_dialog`) is anchored on the dialog STRUCTURE
(BOTH `What do you want to do?` and `Stop and wait for limit to reset`), never
the bare `usage limit reached` headline (#175/#183). The predicate
(`decide.capacity_recovered`) is PURE and reads the box's own usage cache. The
branch (`watchdog/limit_dialog.py`) dismisses (Escape) + resumes (`continue`)
through the SAME verified recovery primitives (`keys`/`send_verified`, recovery
nudge `resume`), gated by the recent-human veto — machine-channel journal only.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import watchdog  # noqa: E402
from watchdog.decide import (  # noqa: E402
    pane_limit_dialog,
    capacity_recovered,
)
from watchdog import limit_dialog  # noqa: E402


# --- fixtures -------------------------------------------------------------- #

# The real gk 2026-09-19 dialog wording, with a little conversation scrollback
# above it (so the bottom-scoping is exercised). The input-box selector glyph is
# the real `❯`.
GK_DIALOG = (
    "● pokracujem v praci na tickete #1086\n"
    "─────────────────────────────────────────────\n"
    "● Goal paused · usage limit reached · send a message after it resets to continue\n"
    "   What do you want to do?\n"
    "   ❯ 1. Stop and wait for limit to reset\n"
    "     2. Wait here, then continue automatically at Sep 20, 5pm\n"
    "     3. Switch to usage credits\n"
    "   Enter to confirm · Esc to cancel\n"
)
# the PLAIN weekly-limit banner job 6 already handles — must NOT be the dialog
PLAIN_BANNER = (
    "⎿ You've hit your weekly limit · resets Sep 20, 5pm\n"
    "  Run /usage-credits to finish\n"
    "❯\n"
)
# ordinary prose that merely MENTIONS a usage limit — never the dialog
PROSE = (
    "The usage limit reached about 80% last week, so I stopped early.\n"
    "❯\n"
)
# an unrelated numbered menu (a permission/plan dialog) — has `❯ 1.` but NOT the
# two limit-dialog anchors
UNRELATED_MENU = (
    "Do you want to proceed?\n"
    "   ❯ 1. Yes\n"
    "     2. No, keep going\n"
    "   Enter to confirm · Esc to cancel\n"
)
BARE_IDLE = "● hotovo\n❯"
# exactly ONE of the two anchors — a DIFFERENT dialog that happens to ask "what
# do you want to do?" but is NOT the limit dialog (BOTH anchors required).
PROMPT_ANCHOR_ONLY = (
    "   What do you want to do?\n"
    "   ❯ 1. Keep the current plan\n"
    "     2. Start over\n"
    "   Enter to confirm · Esc to cancel\n"
)
# and the option phrase alone, in prose, with no "what do you want to do?"
OPTION_ANCHOR_ONLY = (
    "I could stop and wait for limit to reset, but let me try one more thing.\n"
    "❯\n"
)
# BOTH phrases present but in PROSE (a session quoting the dialog — this review
# session is itself such a case) — the option is NOT a numbered menu row, so it
# must NOT match (kills the "phrase anywhere" mutation).
BOTH_IN_PROSE = (
    "The modal asked: What do you want to do? I chose to stop and wait for "
    "limit to reset.\n"
    "❯\n"
)


class TestLimitDialogDetector(unittest.TestCase):
    def test_gk_capture_is_the_dialog(self):
        self.assertTrue(pane_limit_dialog(GK_DIALOG))

    def test_plain_banner_is_not_the_dialog(self):
        self.assertFalse(pane_limit_dialog(PLAIN_BANNER))

    def test_prose_mentioning_usage_limit_is_not_the_dialog(self):
        self.assertFalse(pane_limit_dialog(PROSE))

    def test_unrelated_numbered_menu_is_not_the_dialog(self):
        self.assertFalse(pane_limit_dialog(UNRELATED_MENU))

    def test_requires_BOTH_anchors_not_either(self):
        # a "what do you want to do?" dialog that is NOT the limit dialog, and the
        # option phrase alone in prose — each carries ONE anchor, neither is the
        # dialog (kills the and→or mutation).
        self.assertFalse(pane_limit_dialog(PROMPT_ANCHOR_ONLY))
        self.assertFalse(pane_limit_dialog(OPTION_ANCHOR_ONLY))

    def test_option_must_be_a_menu_row_not_prose(self):
        # BOTH phrases present but the option is in PROSE, not a numbered menu row
        # — a pane quoting the dialog must NOT false-match (kills the
        # phrase-anywhere mutation; the #1086 false-positive review finding).
        self.assertFalse(pane_limit_dialog(BOTH_IN_PROSE))

    def test_empty_and_none(self):
        self.assertFalse(pane_limit_dialog(""))
        self.assertFalse(pane_limit_dialog(None))

    def test_stale_dialog_echo_scrolled_high_does_not_count(self):
        # bottom-scoped like pane_session_limited: a dialog echo far above fresh
        # work is NOT still-open (the gk 2026-07-24 freshest-thing discipline).
        cap = (GK_DIALOG
               + "\n".join("● riadok %d prace" % i for i in range(1, 20))
               + "\n❯")
        self.assertFalse(pane_limit_dialog(cap))


class TestCapacityRecovered(unittest.TestCase):
    EP = {"first_seen": 100, "account": "old@x.bid"}

    def test_account_changed_after_first_seen_true(self):
        cache = {"ts": 200, "account_email": "new@y.bid",
                 "windows": [{"percent": 40}]}
        self.assertTrue(capacity_recovered(self.EP, cache))

    def test_same_account_all_windows_below_cap_true(self):
        cache = {"ts": 200, "account_email": "old@x.bid",
                 "windows": [{"percent": 5}, {"percent": 20}]}
        self.assertTrue(capacity_recovered(self.EP, cache))

    def test_same_account_a_window_at_cap_false(self):
        cache = {"ts": 200, "account_email": "old@x.bid",
                 "windows": [{"percent": 5}, {"percent": 100}]}
        self.assertFalse(capacity_recovered(self.EP, cache))

    def test_cache_not_newer_than_first_seen_false(self):
        cache = {"ts": 100, "account_email": "new@y.bid",
                 "windows": [{"percent": 5}]}
        self.assertFalse(capacity_recovered(self.EP, cache))
        older = {"ts": 50, "account_email": "new@y.bid", "windows": [{"percent": 5}]}
        self.assertFalse(capacity_recovered(self.EP, older))

    def test_missing_or_unparseable_cache_false(self):
        self.assertFalse(capacity_recovered(self.EP, None))
        self.assertFalse(capacity_recovered(self.EP, "not-a-dict"))
        self.assertFalse(capacity_recovered(self.EP, {}))
        self.assertFalse(capacity_recovered(self.EP, {"ts": "bad"}))

    def test_empty_windows_same_account_false(self):
        # no window data + same account → cannot prove capacity
        cache = {"ts": 200, "account_email": "old@x.bid", "windows": []}
        self.assertFalse(capacity_recovered(self.EP, cache))

    def test_account_switch_needs_both_emails_known(self):
        # an episode recorded with an UNREADABLE account ("") must not read a
        # later readable email as a "switch" — fall through to the window check.
        ep = {"first_seen": 100, "account": ""}
        cache = {"ts": 200, "account_email": "new@y.bid",
                 "windows": [{"percent": 100}]}   # window still capped
        self.assertFalse(capacity_recovered(ep, cache))
        cache_ok = {"ts": 200, "account_email": "new@y.bid",
                    "windows": [{"percent": 5}]}
        self.assertTrue(capacity_recovered(ep, cache_ok))

    def test_bool_percent_is_rejected(self):
        cache = {"ts": 200, "account_email": "old@x.bid",
                 "windows": [{"percent": True}]}
        self.assertFalse(capacity_recovered(self.EP, cache))

    def test_switch_to_a_still_capped_account_is_not_recovery(self):
        # a review finding (#1086): a switch to a NEW account whose OWN window is
        # still at cap is NOT capacity — never dismiss straight into a re-cap.
        capped = {"ts": 200, "account_email": "new@y.bid",
                  "windows": [{"percent": 5}, {"percent": 100}]}
        self.assertFalse(capacity_recovered(self.EP, capped))
        # but a switch to a below-cap account IS recovery (the gk incident: ~5%)
        below = {"ts": 200, "account_email": "new@y.bid",
                 "windows": [{"percent": 5}]}
        self.assertTrue(capacity_recovered(self.EP, below))
        # and a switch with NO window data is trusted blind (the switch alone)
        nodata = {"ts": 200, "account_email": "new@y.bid"}
        self.assertTrue(capacity_recovered(self.EP, nodata))


class TestPrioritizePanes(unittest.TestCase):
    def test_gk_checkouts_come_first_stable_otherwise(self):
        panes = [
            ("%1", "/home/gatekeeper/devel/airuleset"),
            ("%2", "/home/gatekeeper/devel/odoo/odoo-erp-quality"),
            ("%3", "/home/gatekeeper/devel/other"),
            ("%4", "/home/gatekeeper/devel/odoo/odoo-erp"),
            ("%5", "/home/gatekeeper/devel/odoo/odoo-erp-infra"),
        ]
        out = limit_dialog.prioritize_panes(panes)
        # the three gk checkouts float to the front, in gk / gk-infra / gk-quality
        # order; everything else keeps its original relative order (stable).
        self.assertEqual([cwd.rsplit("/", 1)[-1] for _, cwd in out],
                         ["odoo-erp", "odoo-erp-infra", "odoo-erp-quality",
                          "airuleset", "other"])

    def test_no_gk_checkouts_is_identity(self):
        panes = [("%1", "/a/repo"), ("%2", "/b/repo"), ("%3", "/c/repo")]
        self.assertEqual(limit_dialog.prioritize_panes(panes), panes)

    def test_empty(self):
        self.assertEqual(limit_dialog.prioritize_panes([]), [])


# --- the branch body, injected fakes --------------------------------------- #

def _drive(*, captured=GK_DIALOG, usage_cache=None, state=None,
           in_mode=False, recent_human=False, deliver_ok=True, dry_run=False,
           sid="sess1086", now=1000.0):
    """Run limit_dialog.handle_limit_dialog with fakes; return
    (out, state, delivered) where `delivered` counts deliver() calls."""
    state = {} if state is None else state
    delivered = []

    def deliver():
        delivered.append(True)
        return deliver_ok

    out = limit_dialog.handle_limit_dialog(
        now, state, pid="%1", cwd="/repo", tpath="/t/repo.jsonl",
        sid=sid, project="repo", captured=captured, usage_cache=usage_cache,
        in_mode=lambda: in_mode,
        recent_human=lambda: recent_human,
        deliver=deliver, dry_run=dry_run)
    return out, state, delivered


class TestHandleLimitDialog(unittest.TestCase):
    CACHE_RECOVERED = {"ts": 900, "account_email": "new@y.bid",
                       "windows": [{"percent": 5}]}
    CACHE_STILL_CAPPED = {"ts": 900, "account_email": "old@x.bid",
                          "windows": [{"percent": 100}]}

    def _seeded(self, **kw):
        ep = {"first_seen": 1, "account": "old@x.bid",
              "dismissed": False, "attempts": 0}
        ep.update(kw)
        return {"limit-dialog:sess1086": ep}

    def test_first_sighting_records_episode_no_keystroke(self):
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED)
        self.assertEqual(delivered, [], out)
        ep = state["limit-dialog:sess1086"]
        self.assertEqual(ep["first_seen"], 1000)
        self.assertEqual(ep["account"], "new@y.bid")
        self.assertFalse(ep["dismissed"])

    def test_no_capacity_journals_waiting_no_keystroke(self):
        out, state, delivered = _drive(usage_cache=self.CACHE_STILL_CAPPED,
                                       state=self._seeded())
        self.assertEqual(delivered, [], out)
        self.assertTrue(any("waiting" in ln for ln in out), out)
        # episode preserved, never dismissed
        self.assertFalse(state["limit-dialog:sess1086"]["dismissed"])

    def test_capacity_but_recent_human_vetoes_no_keystroke(self):
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=self._seeded(), recent_human=True)
        self.assertEqual(delivered, [], out)
        self.assertTrue(any("recent-human" in ln for ln in out), out)
        self.assertFalse(state["limit-dialog:sess1086"]["dismissed"])

    def test_capacity_but_in_mode_holds_no_keystroke(self):
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=self._seeded(), in_mode=True)
        self.assertEqual(delivered, [], out)
        self.assertTrue(any("in-mode" in ln for ln in out), out)

    def test_capacity_and_idle_dismisses_once(self):
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=self._seeded())
        self.assertEqual(delivered, [True], out)
        ep = state["limit-dialog:sess1086"]
        self.assertTrue(ep["dismissed"])
        self.assertTrue(any("dismissed" in ln for ln in out), out)

    def test_one_dismiss_per_episode(self):
        # once dismissed (latch set), a still-visible dialog is NEVER re-dismissed
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=self._seeded(dismissed=True))
        self.assertEqual(delivered, [], out)
        self.assertTrue(any("already dismissed" in ln for ln in out), out)

    def test_failed_deliver_retries_and_is_bounded(self):
        # a swallowed dismiss is NOT latched → retried, but bounded (never a loop)
        state = self._seeded()
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=state, deliver_ok=False)
        self.assertEqual(delivered, [True], out)
        ep = state["limit-dialog:sess1086"]
        self.assertFalse(ep["dismissed"])
        self.assertEqual(ep["attempts"], 1)
        # drive until the cap → then no more keystrokes (bounded, no owner ping)
        for _ in range(limit_dialog.LIMIT_DIALOG_MAX_TRIES + 2):
            out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                           state=state, deliver_ok=False)
        self.assertLessEqual(state["limit-dialog:sess1086"]["attempts"],
                             limit_dialog.LIMIT_DIALOG_MAX_TRIES)
        self.assertTrue(any("gave up" in ln for ln in out), out)

    def test_dry_run_persists_nothing(self):
        state = {}
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=state, dry_run=True)
        # nothing written to the real state under dry-run
        self.assertEqual(state, {})

    def test_dry_run_on_a_recovered_episode_persists_nothing(self):
        # the dry-run contract that MATTERS: a seeded episode + capacity back +
        # dry_run must NOT persist `dismissed`/`attempts` (deliver still runs but
        # the caller's dry_run makes it a no-op keystroke — modelled by the fake).
        state = self._seeded()
        before = dict(state["limit-dialog:sess1086"])
        out, state, delivered = _drive(usage_cache=self.CACHE_RECOVERED,
                                       state=state, dry_run=True)
        # the state dict is UNCHANGED (no dismissed latch, no attempts bump)
        self.assertEqual(state["limit-dialog:sess1086"], before)


# --- deliver_dismiss ------------------------------------------------------- #

class TestDeliverDismiss(unittest.TestCase):
    def _staged_capture(self, escaped):
        """A capture that returns the MODAL until an Escape has been observed,
        then the BARE box — a real dismiss. `escaped` is a 1-element mutable list
        the keys_fn / fake run flips when it sees the Escape. Uses the REAL
        detectors (pane_limit_dialog / pane_at_idle_prompt) as the defaults so
        the pre-Escape re-confirm + post-Escape gates are exercised for real."""
        def cap():
            return BARE_IDLE if escaped[0] else GK_DIALOG
        return cap

    def test_reconfirm_escape_then_continue_recovery_kind(self):
        escaped = [False]
        cap = self._staged_capture(escaped)
        calls = []

        def keys_fn(pane, *ks, kind=None, nudge=None, run=None, logs=None):
            calls.append(("keys", pane, ks, kind, nudge))
            if "Escape" in ks:
                escaped[0] = True
            return True

        def sv_fn(pane, text, run=None, tpath=None, sleep_fn=None, logs=None, nudge=None):
            calls.append(("send_verified", pane, text, nudge))
            return True

        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=None, sleep_fn=None, logs=[],
            keys_fn=keys_fn, send_verified_fn=sv_fn, capture_fn=cap,
            dialog_fn=pane_limit_dialog, at_idle_fn=None)   # real detectors
        self.assertTrue(ok)
        # Escape FIRST (cancel the modal), then the continue submit
        self.assertEqual(calls[0][0], "keys")
        self.assertIn("Escape", calls[0][2])
        self.assertEqual(calls[0][4], limit_dialog.RESUME_NUDGE)
        self.assertEqual(calls[1][0], "send_verified")
        self.assertEqual(calls[1][2], watchdog.NUDGE_TEXT)          # "continue"
        self.assertEqual(calls[1][3], limit_dialog.RESUME_NUDGE)

    def test_aborts_before_escape_when_modal_vanished(self):
        # a review finding (#1086, the parked_wake TOCTOU): if the modal is gone
        # by the time deliver runs (human resolved it / self-resume), NEVER Escape.
        calls = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=None, logs=[],
            keys_fn=lambda *a, **k: calls.append("keys") or True,
            send_verified_fn=lambda *a, **k: calls.append("sv") or True,
            capture_fn=lambda: BARE_IDLE,       # modal already gone
            dialog_fn=pane_limit_dialog, at_idle_fn=None)
        self.assertFalse(ok)
        self.assertEqual(calls, [])              # NO Escape, NO continue

    def test_aborts_when_dialog_still_open_after_escape(self):
        # dialog-gone MUST be confirmed BEFORE the resume text — a still-open
        # modal means the resume text would land IN the modal, not the prompt.
        sv_called = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=None, logs=[],
            keys_fn=lambda *a, **k: True,
            send_verified_fn=lambda *a, **k: sv_called.append(a) or True,
            capture_fn=lambda: GK_DIALOG,        # modal present pre AND post Escape
            dialog_fn=pane_limit_dialog, at_idle_fn=None)
        self.assertFalse(ok)
        self.assertEqual(sv_called, [])          # NO resume text into a modal

    def test_dialog_gone_confirm_has_independent_teeth(self):
        # the POST-Escape dialog-gone check is its OWN guard, not covered by the
        # bare-idle check: with the detector reporting the modal STILL up but a
        # (contrived) idle box, the resume text must NOT be typed into the modal.
        sv_called = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=None, logs=[],
            keys_fn=lambda *a, **k: True,
            send_verified_fn=lambda *a, **k: sv_called.append(a) or True,
            capture_fn=lambda: GK_DIALOG,
            dialog_fn=lambda cap: True,          # modal still up per the detector
            at_idle_fn=lambda cap: True)         # box (contrived) reads idle
        self.assertFalse(ok)
        self.assertEqual(sv_called, [])          # NO resume text while the modal is up

    def test_aborts_when_not_bare_idle_after_escape(self):
        escaped = [False]

        def cap():
            # modal pre-Escape, a DRAFT box (not bare-idle) post-Escape
            return "❯ half-typed draft" if escaped[0] else GK_DIALOG

        def keys_fn(pane, *ks, kind=None, nudge=None, run=None, logs=None):
            if "Escape" in ks:
                escaped[0] = True
            return True

        sv_called = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=None, logs=[],
            keys_fn=keys_fn,
            send_verified_fn=lambda *a, **k: sv_called.append(a) or True,
            capture_fn=cap, dialog_fn=pane_limit_dialog, at_idle_fn=None)
        self.assertFalse(ok)
        self.assertEqual(sv_called, [])

    def test_dry_run_sends_nothing_returns_true(self):
        calls = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/r.jsonl", run=None, logs=[],
            keys_fn=lambda *a, **k: calls.append(a) or True,
            send_verified_fn=lambda *a, **k: calls.append(a) or True,
            capture_fn=lambda: GK_DIALOG, dialog_fn=pane_limit_dialog,
            at_idle_fn=None, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_resume_kind_is_always_on_recovery(self):
        self.assertIn(limit_dialog.RESUME_NUDGE, watchdog.tmux_io.RECOVERY_NUDGE_KINDS)
        import watchdog.nudge_gate as ng
        self.assertIn(limit_dialog.RESUME_NUDGE, ng.RECOVERY_NUDGE_KINDS)
        self.assertTrue(watchdog.nudges_enabled(limit_dialog.RESUME_NUDGE))

    def test_escape_fires_even_with_every_machine_kind_off(self):
        # the recovery-kind Escape must fire through the REAL keys primitive even
        # when every machine nudge kind is OFF (a nudges-off box must still
        # recover a limit-dialog-blocked session).
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = d.name
        exp = m.patch("os.path.expanduser",
                      side_effect=lambda p: p.replace("~", home, 1))
        exp.start()
        self.addCleanup(exp.stop)
        envp = m.patch.dict(os.environ)
        envp.start()
        self.addCleanup(envp.stop)
        os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
        # sanity: the real predicate reads all machine kinds OFF here
        self.assertEqual(watchdog.nudges_on_kinds(home), set())

        escaped = [False]

        def fake_run(argv, timeout=8):
            sent.append(argv)
            if argv[:2] == ["tmux", "send-keys"] and "Escape" in argv:
                escaped[0] = True
            return ""

        sent = []
        ok = limit_dialog.deliver_dismiss(
            "%1", "/t/repo.jsonl", run=fake_run, sleep_fn=lambda s: None, logs=[],
            send_verified_fn=lambda *a, **k: True,
            capture_fn=self._staged_capture(escaped),
            dialog_fn=pane_limit_dialog, at_idle_fn=None)
        self.assertTrue(ok)
        # a REAL Escape send-keys reached the pane (recovery kind, not suppressed)
        self.assertTrue(any(a[:2] == ["tmux", "send-keys"] and "Escape" in a
                            for a in sent), sent)


# --- read_usage_cache ------------------------------------------------------ #

class TestReadUsageCache(unittest.TestCase):
    def test_reads_the_box_cache(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "usage-cache.json")
        with open(p, "w") as fh:
            json.dump({"ts": 5, "account_email": "a@x.bid", "windows": []}, fh)
        got = limit_dialog.read_usage_cache(path=p)
        self.assertEqual(got["account_email"], "a@x.bid")

    def test_missing_or_unparseable_returns_none(self):
        self.assertIsNone(limit_dialog.read_usage_cache(path="/no/such/file.json"))
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = os.path.join(d.name, "bad.json")
        with open(p, "w") as fh:
            fh.write("{not json")
        self.assertIsNone(limit_dialog.read_usage_cache(path=p))

    def test_default_path_is_the_usage_module_cache(self):
        # the reader defaults to watchdog.usage._USAGE_CACHE_PATH (usage.py stays
        # READ-ONLY: we import its path constant, never add a reader there).
        self.assertEqual(limit_dialog._USAGE_CACHE_PATH,
                         watchdog.usage._USAGE_CACHE_PATH)


# --- static recovery-nudge guard pin --------------------------------------- #

class TestRecoveryDeliverySitePinned(unittest.TestCase):
    def test_deliver_dismiss_is_pinned_as_a_recovery_site(self):
        from test_goal_disarm_recovery_1063 import RECOVERY_DELIVERY_SITES
        self.assertIn("watchdog/limit_dialog.py", RECOVERY_DELIVERY_SITES)
        self.assertIn("deliver_dismiss",
                      RECOVERY_DELIVERY_SITES["watchdog/limit_dialog.py"])


# --- run_once end-to-end integration --------------------------------------- #

class TestLimitDialogRunOnceIntegration(unittest.TestCase):
    """End-to-end through the REAL run_once wiring (find_active_transcript, the
    usage cache reader, pane_limit_dialog, capacity_recovered,
    _recovery_recent_human, deliver_dismiss) — the piece the fake drive cannot
    prove: that a real dialog pane, with capacity back, gets an Escape + a
    `continue` and the episode latches dismissed."""

    def _write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def test_run_once_dismisses_dialog_after_capacity_recovered(self):
        tmp = tempfile.mkdtemp()
        projects = os.path.join(tmp, "projects")
        os.makedirs(projects)
        state_path = os.path.join(tmp, "state.json")
        cwd = "/devel/repo1086"
        sid = "5e55abc0-51d0-4a5e-9f1e-00000000d1a6"
        pid = "%9"

        enc = watchdog.encode_project_dir(cwd)
        tpath = os.path.join(projects, enc, sid + ".jsonl")
        self._write(tpath, json.dumps({
            "type": "assistant", "isApiErrorMessage": False,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "working"}]}}) + "\n")

        # Pre-seed the episode with the OLD account (a prior sweep saw the dialog).
        self._write(state_path, json.dumps(
            {"limit-dialog:" + sid: {"first_seen": 1, "account": "old@x.bid",
                                     "dismissed": False, "attempts": 0}}))

        # The box usage cache: newer than first_seen, account SWITCHED to a free one.
        cache_path = os.path.join(tmp, "usage-cache.json")
        self._write(cache_path, json.dumps(
            {"ts": 500, "account_email": "new@y.bid",
             "windows": [{"percent": 5}]}))

        # The pane: the modal BEFORE Escape, a bare `❯` AFTER Escape (a real dismiss).
        state = {"escaped": False}

        def fake_run(argv, timeout=8):
            if argv[:2] == ["tmux", "list-panes"]:
                return "%s\tclaude\t%s\t12345\n" % (pid, cwd)
            if argv[:2] == ["tmux", "capture-pane"]:
                return BARE_IDLE if state["escaped"] else GK_DIALOG
            if argv[:2] == ["tmux", "send-keys"]:
                if "Escape" in argv:
                    state["escaped"] = True
                sent.append(argv)
                return ""
            if argv[:3] == ["tmux", "display-message", "-p"]:
                return ""          # pane_in_mode → "" (not in mode); owner empty
            return ""

        sent = []

        def _typing_send_verified(p, text, run=None, tpath=None, sleep_fn=None,
                                  logs=None, user_authored=False, nudge=None,
                                  state=None, **kw):
            run(["tmux", "send-keys", "-t", p, "-l", "--", text])
            run(["tmux", "send-keys", "-t", p, "Enter"])
            return True

        with m.patch.object(watchdog.usage, "_USAGE_CACHE_PATH", cache_path), \
                m.patch.object(limit_dialog, "_USAGE_CACHE_PATH", cache_path), \
                m.patch.object(watchdog, "send_verified", _typing_send_verified):
            logs = watchdog.run_once(
                now=1000.0, run=fake_run, send_fn=lambda *a, **k: "sent",
                projects_dir=projects, state_path=state_path)

        self.assertTrue(any("limit-dialog" in ln and "dismissed" in ln
                            for ln in logs), logs)
        # an Escape (cancel the modal) AND a literal `continue` reached the pane
        self.assertTrue(any(a[:2] == ["tmux", "send-keys"] and "Escape" in a
                            and pid in a for a in sent), sent)
        self.assertTrue(any("-l" in a and "continue" in a and pid in a
                            for a in sent), sent)
        # the episode latched dismissed
        st = json.load(open(state_path))
        self.assertTrue(st.get("limit-dialog:" + sid, {}).get("dismissed"), st)

    def test_run_once_no_capacity_does_not_dismiss(self):
        tmp = tempfile.mkdtemp()
        projects = os.path.join(tmp, "projects")
        os.makedirs(projects)
        state_path = os.path.join(tmp, "state.json")
        cwd = "/devel/repo1086b"
        sid = "5e55abc0-51d0-4a5e-9f1e-00000000d1a7"
        pid = "%7"
        enc = watchdog.encode_project_dir(cwd)
        tpath = os.path.join(projects, enc, sid + ".jsonl")
        self._write(tpath, json.dumps({
            "type": "assistant", "isApiErrorMessage": False,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "working"}]}}) + "\n")
        self._write(state_path, json.dumps(
            {"limit-dialog:" + sid: {"first_seen": 1, "account": "old@x.bid",
                                     "dismissed": False, "attempts": 0}}))
        # cache: SAME account, a window still at cap → no capacity
        cache_path = os.path.join(tmp, "usage-cache.json")
        self._write(cache_path, json.dumps(
            {"ts": 500, "account_email": "old@x.bid",
             "windows": [{"percent": 100}]}))
        sent = []

        def fake_run(argv, timeout=8):
            if argv[:2] == ["tmux", "list-panes"]:
                return "%s\tclaude\t%s\t12345\n" % (pid, cwd)
            if argv[:2] == ["tmux", "capture-pane"]:
                return GK_DIALOG
            if argv[:2] == ["tmux", "send-keys"]:
                sent.append(argv)
                return ""
            return ""

        with m.patch.object(watchdog.usage, "_USAGE_CACHE_PATH", cache_path), \
                m.patch.object(limit_dialog, "_USAGE_CACHE_PATH", cache_path):
            logs = watchdog.run_once(
                now=1000.0, run=fake_run, send_fn=lambda *a, **k: "sent",
                projects_dir=projects, state_path=state_path)

        self.assertTrue(any("limit-dialog" in ln and "waiting" in ln
                            for ln in logs), logs)
        self.assertFalse(any("Escape" in a for a in sent), sent)
        st = json.load(open(state_path))
        self.assertFalse(st.get("limit-dialog:" + sid, {}).get("dismissed"), st)


if __name__ == "__main__":
    unittest.main()
