"""watchdog #271 — draft-rescue: EVERY keystroke-sending delivery path
(`deliver_with_stash`, `_send_goal_verified`) persists a pane's non-empty
input-box content to disk BEFORE its own first `send-keys`, so a stash whose
on-screen auto-restore silently fails still leaves a recoverable trace.

2026-08-06 incident: the user had a long draft typed into a Claude Code
pane; watchdog job 9 (goal-autoarm) delivered a `/goal` arm into that pane
and the draft was gone with NO trace anywhere — CC's input box renders
in-place via cursor addressing, so unsent text never even enters tmux
scrollback, and `deliver_with_stash`'s own recovery story (Claude Code's
async on-screen auto-restore once the delivered turn completes) is never
directly observable from this process. This file proves the fix: whenever
either primitive is about to touch a non-empty box, a rescue file exists on
disk with that content BEFORE any keystroke lands, and the file's path is
journaled — regardless of whether the delivery itself ultimately succeeds.

`draft_rescue_dir()` is patched with `create=True` in every test here on
purpose — it lets the SAME test file prove genuine RED against the pre-fix
`watchdog/__init__.py` (the patch succeeds either way; pre-fix, nothing in
`deliver_with_stash`/`_send_goal_verified` ever calls it, so the rescue file
simply never appears and the assertion fails for the RIGHT reason — content
loss — not because a symbol is missing)."""

import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

DRAFT_TEXT = "toto je dlha nedokoncena sprava ktoru som este neodoslal"
DRAFT_CAP = "● turn done\n❯\xa0" + DRAFT_TEXT + "\n  ctx ░░\n"
BARE_CAP = "● turn done\n❯\xa0\n  ctx ░░\n"
STASHED_BARE = "● turn done\n❯\xa0\n  ctx ░░  › stashed\n"
BARE_AFTER_SUBMIT = "● turn done\n❯\xa0\n  ctx ░░\n"
GOAL_TEXT = "/goal " + "STOP CONDITIONS " + "x" * 40


class _Recorder:
    """A scripted `run` fake — capture-pane calls consume a fixed queue."""

    def __init__(self, captures=None):
        self.captures = list(captures or [])
        self.sent = []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        if "capture-pane" in " ".join(argv):
            return self.captures.pop(0) if self.captures else ""
        return ""


def _typed_boundary(text):
    return "● typing\n❯ " + text + "\n  ctx ░░\n"


class _RescueIsolated(unittest.TestCase):
    """Shared setUp: `draft_rescue_dir()` -> a throwaway temp directory,
    patched with `create=True` so this passes cleanly whether or not the
    function exists yet (the pre-fix RED state)."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rescue_dir = Path(self._tmp.name) / "draft-rescue"
        patcher = m.patch.object(wd, "draft_rescue_dir",
                                 return_value=self.rescue_dir, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rescued_files(self):
        try:
            return sorted(self.rescue_dir.iterdir())
        except OSError:
            return []

    def rescued_text(self):
        files = self.rescued_files()
        return [f.read_text(encoding="utf-8") for f in files]


# --------------------------------------------------------------------------- #
# 1. _draft_rescue_text — pure extraction, no I/O
# --------------------------------------------------------------------------- #

class TestDraftRescueText(unittest.TestCase):
    def test_bare_box_is_empty(self):
        self.assertEqual(wd._draft_rescue_text(BARE_CAP), "")

    def test_a_single_row_draft_is_extracted_without_the_glyph(self):
        self.assertEqual(wd._draft_rescue_text(DRAFT_CAP), DRAFT_TEXT)

    def test_no_input_box_at_all_is_empty(self):
        busy = "● Baking… (2m · esc to interrupt)\n"
        self.assertEqual(wd._draft_rescue_text(busy), "")

    def test_a_wrapped_draft_reconstructs_from_the_structural_box_rows(self):
        # A bordered box (`_input_box_rows_raw`'s STRUCTURAL strategy) whose
        # draft wraps across two rows — the head carries the glyph, the tail
        # does not.
        cap = ("────────────\n"
               "❯\xa0prva cast draftu ktora sa\n"
               "zalomila na druhy riadok\n"
               "────────────\n"
               "  ctx ░░\n")
        text = wd._draft_rescue_text(cap)
        self.assertIn("prva cast draftu", text)
        self.assertIn("zalomila na druhy riadok", text)


# --------------------------------------------------------------------------- #
# 2. _draft_rescue_persist / _draft_rescue_prune — filesystem behaviour
# --------------------------------------------------------------------------- #

class TestDraftRescuePersist(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir_path = Path(self._tmp.name) / "rescue"

    def test_bare_box_writes_nothing(self):
        path = wd._draft_rescue_persist("%1", BARE_CAP, dir_path=self.dir_path)
        self.assertIsNone(path)
        self.assertFalse(self.dir_path.exists())

    def test_draft_is_written_and_returned_path_readable(self):
        path = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path)
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), DRAFT_TEXT)

    def test_logs_carry_the_rescue_path(self):
        logs = []
        path = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                        logs=logs)
        self.assertTrue(logs, "a rescue must be journaled")
        self.assertIn(str(path), logs[-1])
        self.assertIn("draft-rescue", logs[-1])

    def test_pane_id_is_sanitized_into_a_safe_filename(self):
        # a real tmux pane id looks like `%3`
        path = wd._draft_rescue_persist("%3", DRAFT_CAP, dir_path=self.dir_path)
        self.assertNotIn("%", Path(path).name)

    def test_prune_removes_only_stale_files(self):
        self.dir_path.mkdir(parents=True)
        old = self.dir_path / "old.txt"
        old.write_text("x")
        fresh = self.dir_path / "fresh.txt"
        fresh.write_text("y")
        now = time.time()
        old_mtime = now - wd.DRAFT_RESCUE_TTL_S - 3600
        os.utime(old, (old_mtime, old_mtime))
        wd._draft_rescue_prune(now, dir_path=self.dir_path)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_prune_never_raises_on_a_missing_directory(self):
        wd._draft_rescue_prune(time.time(),
                               dir_path=self.dir_path / "does-not-exist")

    def test_persist_prunes_stale_siblings_inline(self):
        # #271: no new watchdog job exists for this — pruning happens on
        # every write.
        self.dir_path.mkdir(parents=True)
        old = self.dir_path / "old-pane.txt"
        old.write_text("stale")
        now = time.time()
        old_mtime = now - wd.DRAFT_RESCUE_TTL_S - 60
        os.utime(old, (old_mtime, old_mtime))
        wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path, now=now)
        self.assertFalse(old.exists())


class TestDraftRescueDir(unittest.TestCase):
    def test_default_resolves_under_home(self):
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_DRAFT_RESCUE_DIR", None)
            got = wd.draft_rescue_dir()
        self.assertEqual(got, Path.home() / ".claude" / "draft-rescue")

    def test_env_override_wins(self):
        with m.patch.dict(os.environ, {"AIRULESET_DRAFT_RESCUE_DIR": "/tmp/xyz"}):
            self.assertEqual(wd.draft_rescue_dir(), Path("/tmp/xyz"))


# --------------------------------------------------------------------------- #
# 3. deliver_with_stash — the REGRESSION reproduction (#271's own acceptance)
# --------------------------------------------------------------------------- #

class TestDeliverWithStashRescue(_RescueIsolated):
    def test_a_held_draft_is_rescued_before_the_first_keystroke_on_success(self):
        run = _Recorder([STASHED_BARE, _typed_boundary("nova sprava"),
                         BARE_AFTER_SUBMIT])
        logs = []
        ok = wd.deliver_with_stash("%7", "nova sprava", run,
                                   captured=DRAFT_CAP, logs=logs)
        self.assertTrue(ok, logs)
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT])
        self.assertTrue(any("draft-rescue" in ln for ln in logs), logs)
        # the rescue must precede the FIRST send-keys — never merely happen
        # somewhere in the call.
        rescue_idx = next(i for i, ln in enumerate(logs) if "draft-rescue" in ln)
        self.assertEqual(rescue_idx, 0, logs)

    def test_content_survives_even_when_the_delivery_itself_fails(self):
        # A type-verify failure AFTER the rescue already ran — content loss
        # must be impossible regardless of the eventual outcome.
        run = _Recorder([STASHED_BARE, "● typing\n❯ garbage\n  ctx ░░\n",
                         "● typing\n❯\xa0\n  ctx ░░\n"])
        logs = []
        ok = wd.deliver_with_stash("%7", "nova sprava", run,
                                   captured=DRAFT_CAP, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT],
                         "the draft must be on disk even though delivery failed")

    def test_a_bare_box_never_creates_a_rescue_file(self):
        run = _Recorder([BARE_AFTER_SUBMIT, _typed_boundary("x"),
                         BARE_AFTER_SUBMIT])
        wd.deliver_with_stash("%7", "x", run, captured=BARE_CAP,
                              logs=[], sleep_fn=lambda s: None)
        self.assertEqual(self.rescued_files(), [])

    def test_occupied_slot_abort_never_creates_a_rescue_file(self):
        # zero keystrokes ever sent — nothing at risk, nothing to persist.
        run = _Recorder([])
        wd.deliver_with_stash("%7", "x", run, captured=STASHED_BARE, logs=[])
        self.assertEqual(self.rescued_files(), [])


# --------------------------------------------------------------------------- #
# 4. _send_goal_verified — defense-in-depth for the (rare) TOCTOU race
# --------------------------------------------------------------------------- #

class TestSendGoalVerifiedRescue(_RescueIsolated):
    def test_a_non_bare_box_is_rescued_before_the_refusal(self):
        run = _Recorder([])
        logs = []
        ok = wd._send_goal_verified("%9", GOAL_TEXT, run, captured=DRAFT_CAP,
                                    logs=logs)
        self.assertFalse(ok, "must never type over a non-empty box")
        self.assertEqual(run.sent, [], "must not send a single keystroke")
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT])
        self.assertTrue(any("draft-rescue" in ln for ln in logs), logs)

    def test_a_bare_box_creates_no_rescue_file(self):
        run = _Recorder([_typed_boundary(GOAL_TEXT), BARE_AFTER_SUBMIT])
        ok = wd._send_goal_verified("%9", GOAL_TEXT, run, captured=BARE_CAP,
                                    sleep_fn=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(self.rescued_files(), [])


if __name__ == "__main__":
    unittest.main()
