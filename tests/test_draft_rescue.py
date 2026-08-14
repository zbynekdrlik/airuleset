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

import inspect
import os
import stat
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import goal as wdgoal  # #403 -- _send_goal_verified/_await_typed
                                     # moved here verbatim, off wd itself

# Captured at IMPORT time — before `tests/conftest.py`'s autouse
# `_isolate_draft_rescue` fixture (a pytest-only mechanism, invisible to
# `python -m unittest`) ever patches `wd.draft_rescue_dir` for a real test.
# `TestDraftRescueDir` below tests the REAL function's own env-var/default
# resolution and must call this direct reference, never `wd.draft_rescue_dir`
# by name, or it would only ever observe whichever tmp dir the conftest
# fixture (or `unittest`'s absence of one) happens to have installed.
_REAL_DRAFT_RESCUE_DIR = wd.draft_rescue_dir

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

    def test_directory_and_file_are_owner_only(self):
        # #271 adversarial-review CRITICAL finding: the content can be
        # arbitrary user-typed text, including a pasted credential, and
        # these boxes host foreign uids by design — dir 0700, file 0600.
        path = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path)
        self.assertEqual(stat.S_IMODE(self.dir_path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)

    def test_a_symlinked_directory_is_refused_not_followed(self):
        real_target = Path(self._tmp.name) / "elsewhere"
        real_target.mkdir()
        self.dir_path.symlink_to(real_target)
        path = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path)
        self.assertIsNone(path)
        self.assertEqual(list(real_target.iterdir()), [],
                         "must never write THROUGH a planted symlink")

    def test_a_filename_collision_retries_with_a_suffix_never_overwrites(self):
        self.dir_path.mkdir(parents=True)
        now = 1_700_000_000.0
        first = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                         now=now)
        second_cap = "● turn done\n❯\xa0iny druhy draft\n  ctx ░░\n"
        second = wd._draft_rescue_persist("%1", second_cap, dir_path=self.dir_path,
                                          now=now)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(encoding="utf-8"), DRAFT_TEXT)
        self.assertEqual(Path(second).read_text(encoding="utf-8"),
                         "iny druhy draft")

    def test_a_write_failure_is_logged_never_silent(self):
        # a directory we cannot secure (refused before any open() attempt)
        real_target = Path(self._tmp.name) / "elsewhere2"
        real_target.mkdir()
        self.dir_path.symlink_to(real_target)
        logs = []
        path = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                        logs=logs)
        self.assertIsNone(path)
        self.assertTrue(logs, "a failed rescue must still be journaled")
        self.assertIn("draft-rescue: FAILED", logs[-1])
        self.assertIn("would have been lost", logs[-1])

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
        old = self.dir_path / "1-1000000000000.txt"
        old.write_text("x")
        fresh = self.dir_path / "1-2000000000000.txt"
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

    def test_prune_never_touches_a_file_not_shaped_like_its_own_naming(self):
        # #271 adversarial-review MAJOR finding: a misconfigured
        # AIRULESET_DRAFT_RESCUE_DIR pointed at an already-populated
        # directory must never let a routine prune delete unrelated content.
        self.dir_path.mkdir(parents=True)
        unrelated = self.dir_path / "old.txt"
        unrelated.write_text("not ours")
        now = time.time()
        old_mtime = now - wd.DRAFT_RESCUE_TTL_S - 3600
        os.utime(unrelated, (old_mtime, old_mtime))
        wd._draft_rescue_prune(now, dir_path=self.dir_path)
        self.assertTrue(unrelated.exists())

    def test_persist_prunes_stale_siblings_inline(self):
        # #271: no new watchdog job exists for this — pruning happens on
        # every write.
        self.dir_path.mkdir(parents=True)
        old = self.dir_path / "1-1000000000000.txt"
        old.write_text("stale")
        now = time.time()
        old_mtime = now - wd.DRAFT_RESCUE_TTL_S - 60
        os.utime(old, (old_mtime, old_mtime))
        wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path, now=now)
        self.assertFalse(old.exists())

    # ---------------------------------------------------------------- #
    # #479 — content dedup: a LIVE draft parked across sweeps must NOT
    # spawn a fresh rescue file every sweep (the 2026-08-14 storm: the same
    # 702-byte draft rescued 7x over ~3h on pane %1). Identical content is
    # deduped to the ONE existing file, whose mtime is refreshed so the
    # 14-day TTL prune never removes a still-parked draft; genuinely
    # different content is always a new rescue (the safety net is never
    # weakened).
    # ---------------------------------------------------------------- #

    def test_479_identical_content_across_sweeps_writes_no_duplicate(self):
        self.dir_path.mkdir(parents=True)
        first = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                         now=1000.0)
        logs = []
        second = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                          now=2000.0, logs=logs)
        files = [p.name for p in self.dir_path.iterdir()]
        self.assertEqual(len(files), 1,
                         "identical content must not create a duplicate rescue")
        self.assertEqual(first, second,
                         "dedup returns the existing rescue path")
        self.assertTrue(any("identical content already parked" in ln
                            for ln in logs), logs)

    def test_479_dedup_refreshes_mtime_so_ttl_keeps_the_parked_draft(self):
        self.dir_path.mkdir(parents=True)
        first = wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                         now=1000.0)
        os.utime(first, (1000.0, 1000.0))
        wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                 now=5000.0)
        self.assertGreaterEqual(Path(first).stat().st_mtime, 5000.0 - 1,
                                "the surviving rescue's mtime must be refreshed "
                                "so the 14-day TTL never prunes a still-parked "
                                "draft")

    def test_479_different_content_still_writes_a_new_rescue(self):
        # control — the dedup is CONTENT-scoped; an edited draft is a
        # genuinely new rescue and must always be saved.
        self.dir_path.mkdir(parents=True)
        wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                 now=1000.0)
        edited = "● turn done\n❯\xa0" + DRAFT_TEXT + " a pridane\n  ctx ░░\n"
        wd._draft_rescue_persist("%1", edited, dir_path=self.dir_path, now=2000.0)
        self.assertEqual(len(list(self.dir_path.iterdir())), 2)

    def test_479_dedup_is_per_pane_never_cross_pane(self):
        # control — two different panes with coincidentally identical draft
        # text are independent conversations; each keeps its own rescue.
        self.dir_path.mkdir(parents=True)
        wd._draft_rescue_persist("%1", DRAFT_CAP, dir_path=self.dir_path,
                                 now=1000.0)
        wd._draft_rescue_persist("%2", DRAFT_CAP, dir_path=self.dir_path,
                                 now=2000.0)
        self.assertEqual(len(list(self.dir_path.iterdir())), 2)


class TestDraftRescueDir(unittest.TestCase):
    def test_default_resolves_under_home(self):
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_DRAFT_RESCUE_DIR", None)
            got = _REAL_DRAFT_RESCUE_DIR()
        self.assertEqual(got, Path.home() / ".claude" / "draft-rescue")

    def test_env_override_wins(self):
        with m.patch.dict(os.environ, {"AIRULESET_DRAFT_RESCUE_DIR": "/tmp/xyz"}):
            self.assertEqual(_REAL_DRAFT_RESCUE_DIR(), Path("/tmp/xyz"))


# --------------------------------------------------------------------------- #
# 3. deliver_with_stash — the REGRESSION reproduction (#271's own acceptance)
# --------------------------------------------------------------------------- #

class TestDeliverWithStashRescue(_RescueIsolated):
    def test_a_held_draft_is_rescued_before_the_first_keystroke_on_success(self):
        run = _Recorder([STASHED_BARE, _typed_boundary("nova sprava"),
                         BARE_AFTER_SUBMIT])
        logs = []
        # #271 adversarial-review MINOR finding: `rescue line is logs[0]`
        # alone has no teeth — it also holds true if the persist call moves
        # to AFTER the stash's own Escape+C-s (mutation-proven). Spy on the
        # REAL primitive and assert `run.sent` is still EMPTY at the moment
        # it is called — that is the only assertion a moved persist call
        # cannot satisfy.
        calls_at = []
        real_persist = wd._draft_rescue_persist

        def _spy(pid, captured, **kw):
            calls_at.append(len(run.sent))
            return real_persist(pid, captured, **kw)

        with m.patch.object(wd, "_draft_rescue_persist", side_effect=_spy):
            ok = wd.deliver_with_stash("%7", "nova sprava", run,
                                       captured=DRAFT_CAP, logs=logs)
        self.assertTrue(ok, logs)
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT])
        self.assertTrue(any("draft-rescue" in ln for ln in logs), logs)
        self.assertEqual(calls_at, [0],
                         "the rescue must run before ANY keystroke is sent")

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
        ok = wdgoal._send_goal_verified("%9", GOAL_TEXT, run, captured=DRAFT_CAP,
                                    logs=logs)
        self.assertFalse(ok, "must never type over a non-empty box")
        self.assertEqual(run.sent, [], "must not send a single keystroke")
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT])
        self.assertTrue(any("draft-rescue" in ln for ln in logs), logs)

    def test_a_bare_box_creates_no_rescue_file(self):
        # #271: the function re-captures FRESH immediately before typing —
        # the first queued capture answers THAT re-check (still bare), the
        # rest serve the type/submit verify polls as before.
        run = _Recorder([BARE_CAP, _typed_boundary(GOAL_TEXT), BARE_AFTER_SUBMIT])
        ok = wdgoal._send_goal_verified("%9", GOAL_TEXT, run, captured=BARE_CAP,
                                    sleep_fn=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(self.rescued_files(), [])

    def test_a_draft_appearing_between_the_callers_check_and_the_type_is_rescued(self):
        # #271 adversarial-review MAJOR finding: the primitive's OWN persist
        # call must catch a race the caller's check cannot see — a draft
        # that appears strictly AFTER the caller verified bare but BEFORE
        # the real type keystroke. The caller's own `captured=BARE_CAP`
        # passes the first check; the fresh re-capture then shows content.
        run = _Recorder([DRAFT_CAP])
        logs = []
        ok = wdgoal._send_goal_verified("%9", GOAL_TEXT, run, captured=BARE_CAP,
                                    logs=logs, sleep_fn=lambda s: None)
        self.assertFalse(ok, "must never type over content that appeared")
        self.assertFalse(any("-l" in a for a in run.sent),
                         "the content keystroke must never be sent")
        self.assertEqual(self.rescued_text(), [DRAFT_TEXT])
        self.assertTrue(any("draft-rescue" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 5. cmd_push's own AIRULESET_DRAFT_RESCUE_DIR isolation line — a source-scan
#    lock (same shape as tests/test_dev_env_provisioning.py's REMOTE_DEPLOY_
#    TIMEOUT_S locks), because #271's adversarial review found this line
#    currently fails no test at all if deleted.
# --------------------------------------------------------------------------- #

class TestCmdPushIsolatesDraftRescue(unittest.TestCase):
    def test_cmd_push_points_the_test_suite_at_a_throwaway_rescue_dir(self):
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("AIRULESET_DRAFT_RESCUE_DIR", src)
        self.assertIn("TemporaryDirectory", src)
        # the env var must actually flow into the unittest-discover
        # subprocess call, not just get computed and discarded.
        self.assertIn("env=test_env", src)


if __name__ == "__main__":
    unittest.main()
