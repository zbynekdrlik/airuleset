"""#433 item G step 8 — `watchdog/card_flags.py` split.

The #297/#298 flag-reaction + card-reopen cluster — eleven functions
(`_repo_live_pane`, `_nudge_repo_pane`, `_card_remark_comment_body`,
`compose_card_reopen_nudge`, `_gh_call`, `_card_reopen_flow`, `_flagged_emoji`,
`_flag_target`, `_flag_delivery_target`, `_deliver_flag_prompt_to_exact_session`,
`compose_flag_prompt`) plus three cluster-private constants
(`BOUNCE_REMARK_LABEL`, `_FLAG_EMOJI`, `_FLAG_PROMPT_TEMPLATE`) — were moved
VERBATIM out of `watchdog/__init__.py` into `watchdog.card_flags`, then
re-exported IN PLACE by a positional facade import at the earliest removed
block's position. Like `stash.py`/`handoff_alarm.py`, this is a BACK-REFERENCE
module: the bodies call `watchdog`-resident helpers (the tmux/pane primitives
`capture_pane`/`pane_in_mode`/`pane_at_idle_prompt`/`send_continue`, the
cross-stream `_safe_to_bounce_nudge`/`_try_stash_nudge`/`_gh_env`, and
`clean_reply_text` still resident in `__init__`), ALL written call-time as
`watchdog.<name>` (module top: `import watchdog`). The three constants are read
module-locally (grep-proven unpatched) and re-exported.

These tests lock the invariants that keep every existing `watchdog.<name>` seam
(the whole discord-reply monkeypatch surface in test_discord_reply.py, hooks,
job 7) working unchanged after the move:

  1. `import watchdog` stays clean in a FRESH subprocess, AND importing the
     `watchdog.card_flags` submodule FIRST initializes the package cleanly — the
     one circular-import surface (`import watchdog` inside a submodule imported
     by `__init__`'s own facade) resolves because every `watchdog.<name>` access
     is call-time, never at card_flags import time.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.card_flags.<name>` — the C2 re-export contract. A future edit that
     lets a name drift to a private copy breaks `is` and fails loudly (a dead
     `monkeypatch.setattr(watchdog, "<name>", ...)` seam is exactly this bug).
  3. The back-references RESOLVE through the package at call time. Two teeth
     classes, per the module-split lessons:
       - RESIDENT helpers (`capture_pane`, `send_continue`, `_try_stash_nudge`,
         `clean_reply_text`, ...) live in OTHER modules, so a bare reversion
         NameErrors — a functional/patch-observing call has teeth. EXCEPT
         `_gh_call`'s `_gh_env` back-ref sits INSIDE a `try/except Exception`
         that would SWALLOW the NameError into the fail-safe `(False, "")`, so
         only a PATCH-OBSERVING test has teeth there (step-4(c) lesson).
       - CO-MOVED intra-module cross-calls (`_card_reopen_flow` -> `_gh_call`,
         `_card_reopen_flow` -> `_card_remark_comment_body`,
         `_flag_delivery_target` -> `_repo_live_pane`) are written
         `watchdog.<name>`, NOT bare — a bare name would resolve to the
         module-local co-moved function and PASS every functional test while
         SILENTLY bypassing a `patch.object(watchdog, ...)` seam (the design's #1
         hazard). Only a patch-observing test has teeth here (step-3 lesson).
"""

import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.card_flags as card_flags  # noqa: E402

# The eleven functions moved into card_flags.py, in definition order. Kept
# explicit — a hand-maintained checklist the reviewer diffs against the facade
# import block in `__init__.py`.
MOVED_FUNCS = [
    "_repo_live_pane",
    "_nudge_repo_pane",
    "_card_remark_comment_body",
    "compose_card_reopen_nudge",
    "_gh_call",
    "_card_reopen_flow",
    "_flagged_emoji",
    "_flag_target",
    "_flag_delivery_target",
    "_deliver_flag_prompt_to_exact_session",
    "compose_flag_prompt",
]
# The three cluster-private constants moved alongside (read module-locally,
# re-exported for namespace stability). No `__module__` on a str/tuple, so these
# are checked for identity only, never for reported module.
MOVED_CONSTS = [
    "BOUNCE_REMARK_LABEL",
    "_FLAG_EMOJI",
    "_FLAG_PROMPT_TEMPLATE",
]
MOVED_NAMES = MOVED_FUNCS + MOVED_CONSTS


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_card_flags_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly — card_flags's `import watchdog` binds the partially-initialized
        # package object, and every `watchdog.<name>` access is call-time, so no
        # attribute is touched during import. `_flagged_emoji({})` is pure.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.card_flags as c; print(repr(c._flagged_emoji({})))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "''")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(card_flags, name),
                                f"{name} missing from watchdog.card_flags")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(card_flags, name),
                              f"watchdog.{name} is not watchdog.card_flags.{name}")

    def test_card_flags_lives_in_its_own_module_file(self):
        self.assertTrue(card_flags.__file__.endswith("watchdog/card_flags.py"),
                        card_flags.__file__)

    def test_moved_functions_report_card_flags_as_their_module(self):
        for name in MOVED_FUNCS:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.card_flags")


class PureFunctionSmoke(unittest.TestCase):
    """The pure (no back-reference) movers still behave after the move. The full
    behavioural coverage lives in test_discord_reply.py; these are load-and-run
    smokes so this split test is self-sufficient."""

    def test_flagged_emoji(self):
        self.assertEqual(watchdog._flagged_emoji(
            {"reactions": [{"emoji": {"name": "❓"}, "count": 1}]}), "❓")
        self.assertEqual(watchdog._flagged_emoji({}), "")

    def test_flag_target(self):
        qmap = {"q1": {"session": "s1", "cwd": "/c"}}
        cardmap = {"c1": {"repo": "o/r", "issue": 7}}
        self.assertEqual(watchdog._flag_target("q1", qmap, cardmap)["kind"],
                         "question")
        self.assertEqual(watchdog._flag_target("c1", qmap, cardmap)["kind"], "card")
        self.assertIsNone(watchdog._flag_target("nope", qmap, cardmap))

    def test_pure_string_builders(self):
        self.assertIn("42", watchdog.compose_card_reopen_nudge(42))
        self.assertIn("the remark", watchdog._card_remark_comment_body("the remark"))


class CoMovedCrossCallsGoThroughPackageSeam(unittest.TestCase):
    """The three INTRA-module co-moved cross-calls are written `watchdog.<name>`,
    NOT bare — so a `patch.object(watchdog, ...)` seam still reaches the caller
    after the move (C3). A bare name would resolve to the module-local co-moved
    function and pass every FUNCTIONAL test while SILENTLY bypassing the patch
    (the design's #1 hazard) — only a patch-observing test has teeth here."""

    def test_card_reopen_flow_builds_comment_via_card_remark_seam(self):
        # _card_reopen_flow -> watchdog._card_remark_comment_body: the sentinel
        # body must reach the injected gh_fn's comment call. A bare co-moved call
        # would build the REAL body and never surface the sentinel.
        calls = []

        def gh(argv, input_text=None, timeout=25):
            calls.append((argv, input_text))
            return True, "[]"

        with mock.patch.object(watchdog, "_card_remark_comment_body",
                               return_value="SENTINEL-BODY"):
            ok = watchdog._card_reopen_flow("o/r", 42, "the remark", gh_fn=gh)
        self.assertTrue(ok)
        comment = [c for c in calls if c[0][:3] == ["gh", "issue", "comment"]]
        self.assertTrue(comment, "no gh issue comment call was made")
        self.assertEqual(comment[0][1], "SENTINEL-BODY")

    def test_card_reopen_flow_falls_back_to_card_flags_gh_call_seam(self):
        # _card_reopen_flow -> (gh_fn or watchdog._gh_call): with NO gh_fn the
        # fallback must reach the PATCHED watchdog._gh_call, never a bare
        # module-local (which would run a real `gh` subprocess).
        calls = []

        def fake_gh_call(argv, input_text=None, timeout=25):
            calls.append(argv)
            return True, "[]"

        with mock.patch.object(watchdog, "_gh_call", side_effect=fake_gh_call):
            ok = watchdog._card_reopen_flow("o/r", 42, "x")  # no gh_fn -> fallback
        self.assertTrue(ok)
        self.assertTrue(calls, "the patched watchdog._gh_call was never reached")
        self.assertEqual(calls[0][:3], ["gh", "issue", "reopen"])

    def test_flag_delivery_target_resolves_via_repo_live_pane_seam(self):
        # _flag_delivery_target -> watchdog._repo_live_pane: patch the package
        # seam; the caller must observe the sentinel pane. A bare co-moved call
        # would use the REAL _repo_live_pane (None for empty maps) -> got None.
        target = {"kind": "card", "repo": "owner/airuleset", "issue": 5}
        with mock.patch.object(watchdog, "_repo_live_pane",
                               return_value=("sid-x", "%9", "/cwd")):
            got = watchdog._flag_delivery_target(target, {}, {})
        self.assertEqual(got, ("sid-x", "%9", "/cwd", False))


class ResidentBackReferencesResolveAtCallTime(unittest.TestCase):
    """Patch-observing teeth for the `watchdog`-resident helpers the moved bodies
    call. A bare reversion NameErrors (the helper is not in card_flags' globals),
    and — for `_gh_env`, which sits inside `_gh_call`'s try/except — the NameError
    would be SWALLOWED into the fail-safe return, so only a patch-observing test
    catches it (step-4(c) lesson)."""

    def test_nudge_repo_pane_delivers_through_the_tmux_pane_seams(self):
        with mock.patch.object(watchdog, "capture_pane", return_value="CAP") as cp, \
             mock.patch.object(watchdog, "pane_in_mode", return_value=False) as pim, \
             mock.patch.object(watchdog, "_safe_to_bounce_nudge",
                               return_value=True) as sb, \
             mock.patch.object(watchdog, "pane_at_idle_prompt",
                               return_value=True) as idle, \
             mock.patch.object(watchdog, "send_continue") as sc:
            ok = watchdog._nudge_repo_pane("%1", "/cwd", None, "hi", False, "/proj")
        self.assertTrue(ok)
        cp.assert_called_once_with("%1", None)
        pim.assert_called_once()
        sb.assert_called_once()
        idle.assert_called_once_with("CAP")
        sc.assert_called_once_with("%1", "hi", None)

    def test_nudge_repo_pane_stashes_when_not_at_idle_prompt(self):
        with mock.patch.object(watchdog, "capture_pane", return_value="CAP"), \
             mock.patch.object(watchdog, "pane_in_mode", return_value=False), \
             mock.patch.object(watchdog, "_safe_to_bounce_nudge", return_value=True), \
             mock.patch.object(watchdog, "pane_at_idle_prompt", return_value=False), \
             mock.patch.object(watchdog, "_try_stash_nudge",
                               return_value="STASHED") as ts:
            got = watchdog._nudge_repo_pane("%1", "/cwd", None, "hi", False, "/proj")
        self.assertEqual(got, "STASHED")
        ts.assert_called_once()

    def test_deliver_flag_prompt_to_exact_session_uses_job7_gate_seams(self):
        # This delivery path uses job 7's OWN gate (pane_at_idle_prompt /
        # _try_stash_nudge) — NOT _safe_to_bounce_nudge. Patch the seams and drive
        # the send branch.
        with mock.patch.object(watchdog, "capture_pane", return_value="CAP") as cp, \
             mock.patch.object(watchdog, "pane_in_mode", return_value=False), \
             mock.patch.object(watchdog, "pane_at_idle_prompt",
                               return_value=True), \
             mock.patch.object(watchdog, "send_continue") as sc:
            ok = watchdog._deliver_flag_prompt_to_exact_session(
                "%2", None, "flag prompt", False)
        self.assertTrue(ok)
        cp.assert_called_once_with("%2", None)
        sc.assert_called_once_with("%2", "flag prompt", None)

    def test_deliver_flag_prompt_stashes_when_not_at_idle_prompt(self):
        with mock.patch.object(watchdog, "capture_pane", return_value="CAP"), \
             mock.patch.object(watchdog, "pane_in_mode", return_value=False), \
             mock.patch.object(watchdog, "pane_at_idle_prompt", return_value=False), \
             mock.patch.object(watchdog, "_try_stash_nudge",
                               return_value="STASHED") as ts:
            got = watchdog._deliver_flag_prompt_to_exact_session(
                "%2", None, "flag prompt", False)
        self.assertEqual(got, "STASHED")
        ts.assert_called_once()

    def test_gh_call_uses_watchdog_gh_env_seam_despite_try_except(self):
        # _gh_env sits INSIDE _gh_call's try/except — a bare reversion would
        # NameError and be swallowed into (False, ""), so a functional test has
        # no teeth. Patch-observing: the sentinel env must reach subprocess.run.
        class R:
            returncode = 0
            stdout = "OUT"

        captured = {}

        def fake_run(argv, **kw):
            captured["env"] = kw.get("env")
            return R()

        with mock.patch.object(watchdog, "_gh_env", return_value={"SENT": "1"}), \
             mock.patch("subprocess.run", side_effect=fake_run):
            ok, out = watchdog._gh_call(["gh", "x"])
        self.assertTrue(ok)
        self.assertEqual(out, "OUT")
        self.assertEqual(captured["env"], {"SENT": "1"})

    def test_compose_flag_prompt_cleans_via_watchdog_seam(self):
        with mock.patch.object(watchdog, "clean_reply_text",
                               return_value="CLEANED-TEXT"):
            p = watchdog.compose_flag_prompt("raw <@123> text")
        self.assertIn("CLEANED-TEXT", p)


if __name__ == "__main__":
    unittest.main()
