"""#433 item G step 5 — `watchdog/stash.py` split.

The SAFETY-CRITICAL stash-around-delivery keystroke cluster (#35/#372 lineage)
— the machinery that types the watchdog's OWN text into a live Claude Code pane
WITHOUT destroying a human's in-progress draft (Ctrl+S park, verified type +
submit, bounded settle poll, non-destructive undo, single-slot overwrite abort),
the draft-rescue on-disk persistence helpers, and the #372 own-stuck-content
ownership tests — was moved VERBATIM out of `watchdog/__init__.py` into
`watchdog.stash`, then re-exported IN PLACE by a positional facade import at the
earliest removed line's position. 16 functions + 23 co-moved module constants
(the `STASH_*` / `DRAFT_RESCUE_*` / `GOAL_TYPE_CHUNK_*` / `JANITOR_*` /
`_PASTED_PLACEHOLDER_RX` / `_PASTE_EXPAND_HINT_RX` families).

Like `tmux_io.py`, this is a BACK-REFERENCE module: the bodies call primitives
still resident elsewhere (`watchdog.capture_pane` / `_input_line_text` /
`_input_box_rows_raw` / `_normalize_queued_hint` / `_is_draft_head` /
`_default_run` / `_has_free_prompt` / `_strip_selected` / `_box_is_wrapped`)
AND make intra-module co-moved cross-calls (`deliver_with_stash` ->
`_type_literal`/`_await_stash_settled`/`_typed_landed`/...; `_draft_rescue_persist`
-> `draft_rescue_dir`/`_draft_rescue_text`/...; `_looks_like_own_stuck_content`
-> `_looks_like_own_payload`), ALL written as call-time `watchdog.<name>`
(module top: `import watchdog`). The co-moved CONSTANTS are referenced bare (no
test patches any of them — the step-5 C5 grep proved it), so a module-local read
is correct and byte-verbatim.

These tests lock the invariants that keep every existing `watchdog.<name>` seam
(goal ×2 + cross_stream + job 1 for `deliver_with_stash`, conftest +
test_draft_rescue for `draft_rescue_dir`/`_draft_rescue_persist`, janitor for
`JANITOR_*`, and every monkeypatch across the suite) working unchanged:

  1. `import watchdog` stays clean in a FRESH subprocess, AND importing the
     `watchdog.stash` submodule FIRST initializes the package cleanly — the
     circular-import surface (`import watchdog` inside a submodule imported by
     `__init__`'s own facade) resolves because every `watchdog.<name>` access is
     call-time, never at stash import time.
  2. Every one of the 39 moved names is the SAME object at `watchdog.<name>` and
     at `watchdog.stash.<name>` — the C2 re-export contract. A future edit that
     lets a name drift to a private copy breaks `is` and fails loudly (a dead
     `monkeypatch.setattr(watchdog, "<name>", ...)` seam is exactly this bug).
  3. The EXTERNAL back-references actually RESOLVE at call time: a call through
     `_await_stash_settled` reaching `watchdog.capture_pane` /
     `watchdog._input_line_text` would NameError the instant a `watchdog.` prefix
     were reverted to a bare name (those two live in `tmux_io`/`pane_text`, never
     in `stash.py`) — the design's #1 hazard (a silent seam bypass) has teeth.
  4. The INTRA-module co-moved cross-calls go through the PACKAGE seam, not a
     bare module-local name — proven by patch-observing tests (a bare co-moved
     call would resolve to `stash.py`'s own copy and SILENTLY bypass the patch
     while passing every functional test).
"""

import contextlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.stash as stash  # noqa: E402

# The 39 moved names, in facade (definition) order. Hand-maintained — a
# checklist the reviewer diffs against the facade import block in `__init__.py`.
MOVED_NAMES = [
    "STASH_MARKER",
    "_PASTED_PLACEHOLDER_RX",
    "_typed_landed",
    "GOAL_TYPE_CHUNK_THRESHOLD",
    "GOAL_TYPE_CHUNK_SIZE",
    "GOAL_TYPE_CHUNK_DELAY_S",
    "_PASTE_EXPAND_HINT_RX",
    "_pane_shows_collapsed_paste",
    "_type_literal",
    "STASH_VERIFY_SETTLE_POLLS",
    "STASH_VERIFY_SETTLE_S",
    "STASH_PARKED",
    "STASH_NOOP",
    "STASH_UNRESOLVED",
    "STASH_NO_BOUNDARY",
    "_await_stash_settled",
    "_typed_exclusively",
    "STASH_UNDO_MAX_BACKSPACES",
    "STASH_UNDO_SETTLE_POLLS",
    "STASH_UNDO_SETTLE_S",
    "_undo_appended_text",
    "_undo_typed_text",
    "draft_rescue_dir",
    "DRAFT_RESCUE_TTL_S",
    "_DRAFT_RESCUE_NAME_RX",
    "_draft_rescue_text",
    "_draft_rescue_prune",
    "_draft_rescue_ensure_dir",
    "_DRAFT_RESCUE_MAX_RETRIES",
    "_draft_rescue_persist",
    "_undo_and_release_slot",
    "deliver_with_stash",
    "_JANITOR_OWN_PREFIXES",
    "JANITOR_CLEAR_MAX_ITER",
    "JANITOR_CLEAR_BATCH_MAX",
    "JANITOR_CLEAR_SETTLE_S",
    "JANITOR_WATCH_MAX_AGE_S",
    "_looks_like_own_payload",
    "_looks_like_own_stuck_content",
]

# Only the FUNCTIONS carry a `__module__`; constants do not.
MOVED_FUNCS = [
    "_typed_landed", "_pane_shows_collapsed_paste", "_type_literal",
    "_await_stash_settled", "_typed_exclusively", "_undo_appended_text",
    "_undo_typed_text", "draft_rescue_dir", "_draft_rescue_text",
    "_draft_rescue_prune", "_draft_rescue_ensure_dir", "_draft_rescue_persist",
    "_undo_and_release_slot", "deliver_with_stash", "_looks_like_own_payload",
    "_looks_like_own_stuck_content",
]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_stash_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly — stash's `import watchdog` binds the partially-initialized
        # package object, and every `watchdog.<name>` access is call-time, so
        # no attribute is touched during import.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.stash as s; print(s._looks_like_own_payload('/goal x'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "True")
        self.assertEqual(r.stderr.strip(), "")

    def test_reexport_identity_and_module_in_fresh_process(self):
        # Identity + __module__ are STATIC module-code properties, verified in a
        # child process — authoritative, and free of this suite's autouse
        # `conftest._isolate_draft_rescue` fixture, which replaces
        # `watchdog.draft_rescue_dir` with a Mock for the WHOLE in-process
        # session (so an in-process `is` check on that one name would spuriously
        # fail). The child asserts every one of the 39 moved names is the SAME
        # object at `watchdog.<n>` and `watchdog.stash.<n>`, and that each of the
        # 16 functions reports `watchdog.stash` as its `__module__`.
        child = (
            "import watchdog, watchdog.stash as st\n"
            "names = %r\n" % MOVED_NAMES +
            "funcs = %r\n" % MOVED_FUNCS +
            "assert len(names) == 39 and len(set(names)) == 39\n"
            "for n in names:\n"
            "    assert hasattr(st, n), 'missing from stash: ' + n\n"
            "    assert hasattr(watchdog, n), 'not re-exported: ' + n\n"
            "    assert getattr(watchdog, n) is getattr(st, n), 'identity: ' + n\n"
            "for n in funcs:\n"
            "    assert getattr(watchdog, n).__module__ == 'watchdog.stash', 'module: ' + n\n"
            "assert st.__file__.endswith('watchdog/stash.py'), st.__file__\n"
            "print('IDENTITY_OK')\n"
        )
        r = subprocess.run([sys.executable, "-c", child],
                           capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "IDENTITY_OK")


class ReExportPresenceInProcess(unittest.TestCase):
    def test_facade_name_count_is_exactly_39(self):
        self.assertEqual(len(MOVED_NAMES), 39)
        self.assertEqual(len(set(MOVED_NAMES)), 39)

    def test_every_moved_name_is_present_in_both_namespaces(self):
        # In-process PRESENCE only — the authoritative object-identity `is` check
        # runs in the fresh subprocess above, since the autouse
        # `conftest._isolate_draft_rescue` fixture patches `watchdog.draft_rescue_dir`
        # to a Mock for the whole in-process session.
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(stash, name),
                                f"{name} missing from watchdog.stash")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")

    def test_stash_lives_in_its_own_module_file(self):
        self.assertTrue(stash.__file__.endswith("watchdog/stash.py"), stash.__file__)


class BackReferencesResolveAtCallTime(unittest.TestCase):
    """A functional call through each EXTERNAL back-reference. `capture_pane` and
    `_input_line_text` live in other submodules, never in `stash.py`, so a bare
    reference would NameError at call time the instant a `watchdog.` prefix were
    reverted — the C3 seam-preservation contract has hard teeth here."""

    def test_await_stash_settled_reaches_capture_pane_and_input_line_text(self):
        # Patch the two external seams; the co-moved settle-poll must reach both.
        # A bare `capture_pane(...)`/`_input_line_text(...)` (not defined in
        # stash.py) would NameError, not just bypass a patch.
        with mock.patch.object(watchdog, "capture_pane",
                               return_value="❯ ") as cp, \
             mock.patch.object(watchdog, "_input_line_text",
                               return_value="") as il:
            cap, outcome, boundary = watchdog._await_stash_settled(
                "%1", lambda *a, **k: None, lambda *a, **k: None)
        self.assertTrue(cp.called, "capture_pane back-ref never reached")
        self.assertTrue(il.called, "_input_line_text back-ref never reached")
        # bare box + no `› stashed` marker => NOOP, empty boundary text
        self.assertEqual(outcome, watchdog.STASH_NOOP)
        self.assertEqual(boundary, "")


class CoMovedCrossCallsGoThroughPackageSeam(unittest.TestCase):
    """The INTRA-module co-moved cross-calls are written `watchdog.<name>`, NOT
    bare — so a `patch.object(watchdog, ...)` seam still reaches the caller after
    the move (C3). A bare name would resolve to the module-local co-moved copy
    and pass every FUNCTIONAL test while SILENTLY bypassing the patch (the
    design's #1 hazard). Only a patch-observing test has teeth; these are those."""

    def test_looks_like_own_stuck_content_calls_own_payload_via_watchdog(self):
        # Patch the package seam to accept ANY text; the caller must observe it.
        # A bare intra-call would use the real `_looks_like_own_payload`, which
        # returns False for this non-`/goal`/`/compact`, non-placeholder text.
        with mock.patch.object(watchdog, "_looks_like_own_payload",
                               return_value=True):
            self.assertTrue(
                watchdog._looks_like_own_stuck_content("not an own payload at all"))
        # And the real path still returns False when the seam says so.
        with mock.patch.object(watchdog, "_looks_like_own_payload",
                               return_value=False):
            self.assertFalse(
                watchdog._looks_like_own_stuck_content("not an own payload at all"))

    def test_draft_rescue_prune_calls_draft_rescue_dir_via_watchdog(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(watchdog, "draft_rescue_dir",
                                   return_value=Path(d)) as drd:
                watchdog._draft_rescue_prune(time.time())  # no dir_path -> seam
            self.assertTrue(drd.called,
                            "draft_rescue_dir back-ref never reached from _draft_rescue_prune")

    def test_draft_rescue_persist_calls_dir_and_text_via_watchdog(self):
        # Force past the empty-draft early return by patching the text-extractor
        # seam, then confirm the dir seam is reached — both are co-moved and MUST
        # go through the package namespace.
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(watchdog, "_draft_rescue_text",
                                   return_value="a real draft") as drt, \
                 mock.patch.object(watchdog, "draft_rescue_dir",
                                   return_value=Path(d)) as drd:
                path = watchdog._draft_rescue_persist("%1", "❯ a real draft")
            self.assertTrue(drt.called, "_draft_rescue_text back-ref never reached")
            self.assertTrue(drd.called, "draft_rescue_dir back-ref never reached")
            # a persisted file actually landed in the patched dir
            self.assertIsNotNone(path)
            self.assertTrue(Path(path).parent == Path(d))


class CoMovedKeystrokeSeamsGoThroughPackageSeam(unittest.TestCase):
    """#35/#372 keystroke-safety teeth. The delivery/undo/rescue path makes 14
    intra-module co-moved cross-calls; the class above already locks 4 of them.
    This class locks the remaining 10 — INCLUDING every function that actually
    TYPES (`_type_literal`), BACKSPACES (`_undo_typed_text` /
    `_undo_appended_text` / `_undo_and_release_slot`), POLLS the settle window
    (`_await_stash_settled`), or VERIFIES what landed (`_typed_landed` /
    `_typed_exclusively` / `_pane_shows_collapsed_paste`), plus the two
    draft-rescue helpers (`_draft_rescue_prune` / `_draft_rescue_ensure_dir`).

    Each is a PATCH-OBSERVING test, the only kind with teeth for a co-moved
    cross-call (a functional test passes even against a bare-name revert, since
    the bare name resolves to stash.py's own copy — the design's #1 silent-
    bypass hazard, here on the keystroke path). A spy is patched at the PACKAGE
    seam (`watchdog.<name>`) and the caller is driven far enough to reach it; a
    future bare revert makes the spy's `.called` False and fails the test. The
    external seams are patched to steer `deliver_with_stash` down a specific
    branch without touching a real pane."""

    @staticmethod
    def _recorder():
        sent = []

        def run(argv):
            sent.append(argv)
            return ""

        return sent, run

    @staticmethod
    def _noop(*_a, **_k):
        return None

    def test_deliver_happy_path_reaches_type_await_paste_and_landed(self):
        # NOOP settle -> type -> verify-landed -> submit. Locks _await_stash_settled,
        # _type_literal, _pane_shows_collapsed_paste, _typed_landed (+ _draft_rescue_persist).
        _, run = self._recorder()
        with contextlib.ExitStack() as es:
            def P(name, **kw):
                return es.enter_context(mock.patch.object(watchdog, name, **kw))
            P("capture_pane", return_value="❯ ")
            P("_has_free_prompt", return_value=True)
            P("_strip_selected", return_value=False)
            P("_input_line_text", return_value="")
            P("_box_is_wrapped", return_value=False)
            drp = P("_draft_rescue_persist", return_value=None)
            aws = P("_await_stash_settled",
                    return_value=("❯ ", watchdog.STASH_NOOP, ""))
            tl = P("_type_literal", return_value=None)
            psc = P("_pane_shows_collapsed_paste", return_value=False)
            tvl = P("_type_verify_landed", return_value=True)  # #670: the bare-box
            # happy path verifies via the NEW head-inclusive package seam
            tld = P("_typed_landed", return_value=False)  # post-submit swallowed-
            # submit check: False = the Enter consumed the text (happy path)
            ok = watchdog.deliver_with_stash(
                "%1", "/goal x", run, sleep_fn=self._noop)
        self.assertTrue(ok)
        for spy, nm in ((drp, "_draft_rescue_persist"),
                        (aws, "_await_stash_settled"),
                        (tl, "_type_literal"),
                        (psc, "_pane_shows_collapsed_paste"),
                        (tvl, "_type_verify_landed"),
                        (tld, "_typed_landed")):
            self.assertTrue(spy.called,
                            "%s co-moved seam never reached via watchdog.<name>" % nm)

    def test_deliver_verify_fail_reaches_undo_and_release_slot(self):
        # NOOP settle -> type -> verify FAILS -> the box was VERIFIED BARE, so
        # recovery goes through _undo_and_release_slot. Locks that seam.
        _, run = self._recorder()
        with contextlib.ExitStack() as es:
            def P(name, **kw):
                return es.enter_context(mock.patch.object(watchdog, name, **kw))
            P("capture_pane", return_value="❯ ")
            P("_has_free_prompt", return_value=True)
            P("_strip_selected", return_value=False)
            P("_input_line_text", return_value="")
            P("_box_is_wrapped", return_value=False)
            P("_draft_rescue_persist", return_value=None)
            P("_await_stash_settled",
              return_value=("❯ ", watchdog.STASH_NOOP, ""))
            P("_type_literal", return_value=None)
            P("_pane_shows_collapsed_paste", return_value=False)
            P("_typed_landed", side_effect=[False])
            uars = P("_undo_and_release_slot", return_value=None)
            ok = watchdog.deliver_with_stash(
                "%1", "/goal x", run, sleep_fn=self._noop)
        self.assertFalse(ok)
        self.assertTrue(uars.called,
                        "_undo_and_release_slot co-moved seam never reached")

    def test_undo_and_release_slot_reaches_undo_typed_text(self):
        # _undo_and_release_slot's own co-moved cross-call to _undo_typed_text.
        _, run = self._recorder()
        with mock.patch.object(watchdog, "_undo_typed_text",
                               return_value=True) as utt, \
             mock.patch.object(watchdog, "capture_pane", return_value="❯ "), \
             mock.patch.object(watchdog, "_input_line_text", return_value=""):
            watchdog._undo_and_release_slot(
                "%1", run, "abc", False, [].append, "stash-abort",
                sleep_fn=self._noop)
        self.assertTrue(utt.called,
                        "_undo_typed_text co-moved seam never reached")

    def test_deliver_unresolved_exclusive_reaches_typed_exclusively(self):
        # UNRESOLVED settle (box held something) -> the STRICT _typed_exclusively
        # verify is used instead of _typed_landed. Locks that seam.
        _, run = self._recorder()
        with contextlib.ExitStack() as es:
            def P(name, **kw):
                return es.enter_context(mock.patch.object(watchdog, name, **kw))
            P("capture_pane", return_value="ghost")
            P("_has_free_prompt", return_value=True)
            P("_strip_selected", return_value=False)
            P("_input_line_text", return_value="ghost")
            P("_box_is_wrapped", return_value=False)
            P("_draft_rescue_persist", return_value=None)
            P("_await_stash_settled",
              return_value=("ghost", watchdog.STASH_UNRESOLVED, "ghost"))
            P("_type_literal", return_value=None)
            P("_pane_shows_collapsed_paste", return_value=False)
            texc = P("_typed_exclusively", return_value=True)
            P("_typed_landed", side_effect=[False])
            ok = watchdog.deliver_with_stash(
                "%1", "/goal x", run, sleep_fn=self._noop)
        self.assertTrue(ok)
        self.assertTrue(texc.called,
                        "_typed_exclusively co-moved seam never reached")

    def test_deliver_unresolved_append_reaches_undo_appended_text(self):
        # UNRESOLVED + strict verify FAILS + box ends with pre_text+text ->
        # recovery goes through _undo_appended_text. Locks that seam.
        _, run = self._recorder()
        with contextlib.ExitStack() as es:
            def P(name, **kw):
                return es.enter_context(mock.patch.object(watchdog, name, **kw))
            P("capture_pane", return_value="pre/goal x")
            P("_has_free_prompt", return_value=True)
            P("_strip_selected", return_value=False)
            P("_input_line_text", return_value="pre/goal x")
            P("_box_is_wrapped", return_value=False)
            P("_draft_rescue_persist", return_value=None)
            P("_await_stash_settled",
              return_value=("pre/goal x", watchdog.STASH_UNRESOLVED, "pre"))
            P("_type_literal", return_value=None)
            P("_pane_shows_collapsed_paste", return_value=False)
            P("_typed_exclusively", return_value=False)
            uat = P("_undo_appended_text", return_value=True)
            ok = watchdog.deliver_with_stash(
                "%1", "/goal x", run, sleep_fn=self._noop)
        self.assertFalse(ok)
        self.assertTrue(uat.called,
                        "_undo_appended_text co-moved seam never reached")

    def test_draft_rescue_persist_reaches_prune_and_ensure_dir(self):
        # _draft_rescue_persist's own two co-moved cross-calls.
        _, _run = self._recorder()
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(watchdog, "_draft_rescue_text",
                                   return_value="a draft"), \
                 mock.patch.object(watchdog, "draft_rescue_dir",
                                   return_value=Path(d)), \
                 mock.patch.object(watchdog, "_draft_rescue_ensure_dir",
                                   return_value=True) as ed, \
                 mock.patch.object(watchdog, "_draft_rescue_prune") as pr:
                path = watchdog._draft_rescue_persist("%1", "❯ a draft")
            self.assertIsNotNone(path)
            self.assertTrue(ed.called,
                            "_draft_rescue_ensure_dir co-moved seam never reached")
            self.assertTrue(pr.called,
                            "_draft_rescue_prune co-moved seam never reached")


if __name__ == "__main__":
    unittest.main()
