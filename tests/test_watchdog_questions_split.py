"""#433 item G step 7 -- `watchdog/questions.py` split.

The 15-function question-lifecycle family (the prune / re-ping / owner-decision
jobs, the #449 never-silent owner-answer floor, the FOREIGN-user hosted-map
helpers, and the transcript-timestamp readers those jobs use) was moved VERBATIM
out of `watchdog/__init__.py` into `watchdog.questions`, then re-exported IN
PLACE by a single positional facade import at the earliest removed block's
position (`_orphan_answer_reason`, above every later removed block and above
every `__init__`-resident caller).

Direction: BACK-REFERENCE. Every reference to a name that was a top-level
`watchdog` name before the split -- co-moved step-7 helpers, an `__init__`-
resident constant/function, or a facade re-export from another submodule -- is
written call-time `watchdog.<name>`, so every existing `monkeypatch.setattr(
watchdog, ...)` / `patch.object(wd, ...)` seam stays live (design C3/C5, the #1
hazard). The one def-time default (`prune_answered_questions`'s
`projects_dir=PROJECTS_DIR`) is a module-top `from watchdog import PROJECTS_DIR`.

These tests lock the invariants that keep those seams live:

  1. `import watchdog` stays clean in a FRESH subprocess, and importing
     `watchdog.questions` FIRST initializes the package cleanly (a real call
     through a back-ref -- `clean_reply_text` -- resolves).
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.questions.<name>` (the C2 re-export contract), and reports
     `watchdog.questions` as its `__module__`.
  3. The CO-MOVED intra-module cross-calls go through the PACKAGE seam, not a
     module-local frozen copy -- the ONLY teeth that catch a bare-name
     regression a functional call passes right through, because the co-moved
     target IS defined in the new module (design #1 hazard; #433 step-3 lesson).
  4. The facade-reexport and `__init__`-resident CONSTANT / FUNCTION seams are
     read call-time `watchdog.<name>` -- patch-observing teeth that fail against
     a frozen module-top `from watchdog import <name>` or a bare-name revert.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.questions as questions  # noqa: E402

# The 15 names moved into questions.py, in definition (facade) order. A hand-
# maintained checklist every other test trusts; the two tests in
# MovedNamesChecklistIsSelfValidating cross-check it against the module's own
# defs and the facade import block.
MOVED_NAMES = [
    "_orphan_answer_reason",
    "_orphan_ping_text",
    "_foreign_user",
    "_foreign_session_info",
    "_foreign_questions",
    "_foreign_drop_question",
    "_last_human_prompt_ts",
    "_is_genuine_human_prompt",   # #522
    "_last_real_turn_ts",
    "_transcript_for_session",
    "prune_answered_questions",
    "_in_sleep_window",
    "reping_stale_questions",
    "_fetch_owner_decision_tickets",
    "_owner_decision_digest_block",
    "reping_owner_decision_tickets",
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

    def test_import_questions_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly, and a real call through the `watchdog.clean_reply_text`
        # back-ref must resolve (the parent package is imported first, so the
        # bare `import watchdog` inside questions binds the fully-initialized
        # module and every `watchdog.<name>` access is call-time).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.questions as q; "
             "print(q._orphan_ping_text({'content': 'HELLOFRAG'}, 'untracked-ref'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertIn("HELLOFRAG", r.stdout)


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(questions, name),
                                f"{name} missing from watchdog.questions")
                self.assertTrue(hasattr(wd, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(wd, name), getattr(questions, name),
                              f"watchdog.{name} is not watchdog.questions.{name}")

    def test_questions_lives_in_its_own_module_file(self):
        self.assertTrue(questions.__file__.endswith("watchdog/questions.py"),
                        questions.__file__)

    def test_moved_functions_report_questions_as_their_module(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertEqual(getattr(wd, name).__module__,
                                 "watchdog.questions")


class MovedNamesChecklistIsSelfValidating(unittest.TestCase):
    """`MOVED_NAMES` is a hand-maintained checklist -- every OTHER test trusts
    it. These two tests cross-check it against the two authoritative sources
    (the module's own top-level defs, and the facade import block in
    `__init__.py`), so a name that was moved-but-omitted-from-the-list (or added
    to the list but never moved / never re-exported) fails loudly here rather
    than silently reducing every other test's coverage."""

    def test_module_defines_exactly_the_moved_names(self):
        src = Path(questions.__file__).read_text(encoding="utf-8")
        defs = {n.name for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef)}
        self.assertEqual(defs, set(MOVED_NAMES))

    def test_facade_imports_exactly_the_moved_names(self):
        init_src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(init_src)
        imported = set()
        for node in tree.body:
            if (isinstance(node, ast.ImportFrom)
                    and node.module == "watchdog.questions"):
                imported.update(a.name for a in node.names)
        self.assertEqual(imported, set(MOVED_NAMES))


class CoMovedCrossCallsGoThroughPackageSeam(unittest.TestCase):
    """The FOUR intra-module co-moved cross-calls are written `watchdog.<name>`,
    NOT bare. A bare name would resolve to the module-local co-moved function
    and PASS every functional test while silently bypassing the
    `patch.object(watchdog, ...)` seam (design #1 hazard; #433 step-3 lesson).
    Only these patch-observing tests have teeth for a co-moved target."""

    def test_prune_observes_patched_transcript_and_human_prompt(self):
        # prune -> watchdog._transcript_for_session + watchdog._last_human_prompt_ts
        # (+ the facade watchdog.project_label). Reverting any to bare makes the
        # answered-in-session log vanish (transcript -> None) or NameError
        # (project_label is not defined in questions.py).
        qmap = {"qseam01": {"session": "s1", "cwd": "/proj", "ts": 1000}}
        with mock.patch("notify.load_questions", return_value=dict(qmap)), \
             mock.patch.object(wd, "_transcript_for_session",
                               return_value="/fake/tp"), \
             mock.patch.object(wd, "_last_human_prompt_ts", return_value=5000), \
             mock.patch.object(wd, "project_label", return_value="SENTLBL"):
            logs = wd.prune_answered_questions(9000, dry_run=True)
        self.assertTrue(
            any("answered in-session" in ln and "SENTLBL" in ln for ln in logs),
            logs)

    def test_reping_stale_observes_patched_sleep_window(self):
        qmap = {"qdue01": {"session": "s", "cwd": "/c", "ts": 0, "block": "BLK"}}
        sends = []

        def send_fn(*a, **k):
            sends.append((a, k))
            return ("sent", "mid")

        # NOON epoch (2024-01-01 12:00 CET, hour 12) -- OUTSIDE the sleep
        # window, so the REAL _in_sleep_window returns False here. That is what
        # gives the patch teeth: a bare-revert of `watchdog._in_sleep_window`
        # inside reping_stale would compute night=False and SEND, failing both
        # assertions. (A now inside the window, e.g. 10**9 == 03:46 CEST, reads
        # True for real, so a bare-revert would still defer and the test would
        # pass -- no teeth; round-1 review MINOR.)
        with mock.patch("notify.load_questions", return_value=dict(qmap)), \
             mock.patch.object(wd, "_in_sleep_window", return_value=True), \
             mock.patch.object(wd, "project_label", return_value="L"):
            logs = wd.reping_stale_questions(1704106800, send_fn, path=None)
        self.assertTrue(any("deferred sleep-window" in ln for ln in logs), logs)
        self.assertEqual(sends, [])   # night -> nothing sent

    def test_reping_owner_observes_patched_sleep_and_digest(self):
        # reping_owner -> watchdog._in_sleep_window + watchdog._owner_decision_
        # digest_block. authority='full' injected to isolate these from the
        # _box_authority gate (its own seam is tested below).
        sends = []

        def send_fn(block, **k):
            sends.append(block)
            return "sent"

        state = {}
        with mock.patch.object(wd, "_in_sleep_window", return_value=False), \
             mock.patch.object(wd, "_owner_decision_digest_block",
                               return_value="SENT-DIGEST"):
            wd.reping_owner_decision_tickets(
                10 ** 9, send_fn, state, authority="full",
                fetch=lambda home: [("r", 1, "t")], account_owner="owner")
        self.assertIn("SENT-DIGEST", sends)

    def test_reping_owner_deferred_by_patched_sleep_window(self):
        # A True sleep window (patched) must defer before any fetch -- proves
        # _in_sleep_window is reached through the package seam on this path too.
        fetched = []
        state = {}
        # NOON epoch (real _in_sleep_window False) so patched True diverges from
        # reality -> a bare-revert would fetch, failing both assertions (teeth).
        with mock.patch.object(wd, "_in_sleep_window", return_value=True):
            logs = wd.reping_owner_decision_tickets(
                1704106800, lambda *a, **k: "sent", state, authority="full",
                fetch=lambda home: fetched.append(1) or [])
        self.assertTrue(any("deferred sleep-window" in ln for ln in logs), logs)
        self.assertEqual(fetched, [])


class FacadeReexportSeamsGoThroughPackage(unittest.TestCase):
    """Facade re-exports from other submodules (`clean_reply_text` /
    `_snowflake_ts` <- discord_api; `encode_project_dir` <- transcripts;
    `_cache_repo_roots` / `_gh_env` <- cross_stream) are read call-time
    `watchdog.<name>`. Reverting to bare NameErrors (they are not defined in
    questions.py); these patch-observing tests also fail against a frozen
    module-top `from watchdog import <name>`."""

    def test_orphan_ping_text_uses_patched_clean_reply_text(self):
        with mock.patch.object(wd, "clean_reply_text",
                               return_value="SENTINEL-CLEAN"):
            out = wd._orphan_ping_text({"content": "raw"}, "untracked-ref")
        self.assertIn("SENTINEL-CLEAN", out)

    def test_orphan_answer_reason_uses_patched_snowflake_ts(self):
        msg = {"id": "1", "author": {"id": "owner"}, "content": "hi",
               "message_reference": {"message_id": "zzz"}}
        with mock.patch.object(wd, "_snowflake_ts", return_value=1000.0), \
             mock.patch.object(wd, "clean_reply_text", return_value="hi"):
            # recent (now-sts small) + ref in posted_ids (#652 ownership)
            # -> proceeds to the untracked-ref verdict
            r = wd._orphan_answer_reason(msg, {"owner"}, {}, {}, {"ch"},
                                         "ch", 1001.0, {"zzz"})
        self.assertEqual(r, "untracked-ref")
        with mock.patch.object(wd, "_snowflake_ts", return_value=1.0), \
             mock.patch.object(wd, "clean_reply_text", return_value="hi"):
            # a stale patched snowflake -> the age gate returns None
            r2 = wd._orphan_answer_reason(msg, {"owner"}, {}, {}, {"ch"},
                                          "ch", 10 ** 9, {"zzz"})
        self.assertIsNone(r2)

    def test_transcript_for_session_calls_patched_encode_project_dir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(d) if os.path.isdir(d) else None)
        enc = mock.MagicMock(return_value="ENCDIR")
        with mock.patch.object(wd, "encode_project_dir", enc):
            wd._transcript_for_session(d, "sid1", "/some/cwd")
        enc.assert_called_once_with("/some/cwd")

    def test_fetch_owner_decision_uses_patched_gh_env_and_repo_roots(self):
        genv = mock.MagicMock(return_value={"GH": "X"})
        with mock.patch.object(wd, "_gh_env", genv), \
             mock.patch.object(wd, "_cache_repo_roots", return_value={}):
            out = wd._fetch_owner_decision_tickets()
        self.assertEqual(out, [])          # empty roots -> [] (no subprocess)
        genv.assert_called_once()


class ConstantSeamsGoThroughPackage(unittest.TestCase):
    """The `__init__`-resident constants + the `_box_authority` function are
    read call-time `watchdog.<name>`, NOT frozen by a module-top import or a
    bare-name copy. These patch-observing tests fail against a frozen-import
    regression."""

    def _write_transcript(self, entries):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(p, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        self.addCleanup(os.unlink, p)
        return p

    def test_orphan_answer_reason_tracks_patched_window_constant(self):
        msg = {"id": "1", "author": {"id": "o"}, "content": "hi",
               "message_reference": {"message_id": "z"}}
        with mock.patch.object(wd, "_snowflake_ts", return_value=1000.0), \
             mock.patch.object(wd, "clean_reply_text", return_value="hi"), \
             mock.patch.object(wd, "ORPHAN_ANSWER_WINDOW_S", 5):
            # now-sts = 10 > 5 -> None
            r = wd._orphan_answer_reason(msg, {"o"}, {}, {}, {"ch"}, "ch",
                                         1010.0, {"z"})
        self.assertIsNone(r)
        with mock.patch.object(wd, "_snowflake_ts", return_value=1000.0), \
             mock.patch.object(wd, "clean_reply_text", return_value="hi"), \
             mock.patch.object(wd, "ORPHAN_ANSWER_WINDOW_S", 100000):
            r2 = wd._orphan_answer_reason(msg, {"o"}, {}, {}, {"ch"}, "ch",
                                          1010.0, {"z"})
        self.assertEqual(r2, "untracked-ref")

    def test_last_human_prompt_skips_patched_machine_exact(self):
        p = self._write_transcript([
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "ZAPWORDHUMAN"}},
        ])
        self.assertIsNotNone(wd._last_human_prompt_ts(p))
        with mock.patch.object(wd, "_MACHINE_PROMPT_EXACT", ("ZAPWORDHUMAN",)):
            self.assertIsNone(wd._last_human_prompt_ts(p))

    def test_last_human_prompt_skips_patched_machine_prefix(self):
        p = self._write_transcript([
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "PFXQ hello there"}},
        ])
        self.assertIsNotNone(wd._last_human_prompt_ts(p))
        with mock.patch.object(wd, "_MACHINE_PROMPT_PREFIXES", ("PFXQ",)):
            self.assertIsNone(wd._last_human_prompt_ts(p))

    def test_last_human_prompt_skips_patched_compact_prefix(self):
        p = self._write_transcript([
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "ZQXcontinued session body"}},
        ])
        self.assertIsNotNone(wd._last_human_prompt_ts(p))
        with mock.patch.object(wd, "_COMPACT_CONTINUATION_PREFIX", "ZQX"):
            self.assertIsNone(wd._last_human_prompt_ts(p))

    def test_last_real_turn_uses_patched_real_turn_types(self):
        p = self._write_transcript([
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "hi"}},
        ])
        self.assertIsNotNone(wd._last_real_turn_ts(p))
        with mock.patch.object(wd, "_REAL_TURN_TYPES", ()):
            self.assertIsNone(wd._last_real_turn_ts(p))

    def test_fetch_owner_decision_search_uses_patched_label_constants(self):
        captured = {}

        class _R:
            returncode = 1
            stdout = ""

        def fake_run(argv, **k):
            captured["argv"] = argv
            return _R()

        with mock.patch.object(wd, "_gh_env", return_value={}), \
             mock.patch.object(wd, "_cache_repo_roots", return_value={"/r": "repo"}), \
             mock.patch.object(wd, "OWNER_DECISION_LABELS", ("SENT-LABEL",)), \
             mock.patch.object(wd, "AUTOPILOT_SKIP_EXCL", "-label:SENT-EXCL"), \
             mock.patch("subprocess.run", fake_run):
            wd._fetch_owner_decision_tickets()
        argv = captured["argv"]
        search = argv[argv.index("--search") + 1]
        self.assertIn("SENT-LABEL", search)
        self.assertIn("SENT-EXCL", search)

    def test_reping_owner_bucket_uses_patched_reping_constant(self):
        state = {}
        with mock.patch.object(wd, "QUESTION_REPING_S", 1000), \
             mock.patch.object(wd, "_in_sleep_window", return_value=False):
            wd.reping_owner_decision_tickets(
                50000, lambda *a, **k: "sent", state, authority="full",
                fetch=lambda h: [])
        # 50000 // 1000 == 50 (default 86400 would give bucket 0)
        self.assertEqual(state["owner_decision_digest"]["bucket"], 50)

    def test_reping_owner_gates_on_patched_box_authority(self):
        state = {}
        fetched = []
        with mock.patch.object(wd, "_box_authority",
                               return_value="fork-no-merge"):
            logs = wd.reping_owner_decision_tickets(
                10 ** 9, lambda *a, **k: "sent", state,
                fetch=lambda h: fetched.append(1) or [])
        self.assertTrue(any("reduced-authority" in ln for ln in logs), logs)
        self.assertEqual(fetched, [])   # skipped before any fetch


if __name__ == "__main__":
    unittest.main()
