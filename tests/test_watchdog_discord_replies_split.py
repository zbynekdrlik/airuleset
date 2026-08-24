"""#433 item G step 9 — `watchdog/discord_replies.py` split ("the beast").

The Discord-reply DELIVERY cluster — job 7's owner-reply router
`deliver_discord_replies` (the 459-line core, moved WHOLE, never split
internally), its compose/record/verify helpers (`compose_reply_prompt`,
`_record_dreply_typed`, `_is_dreply_machine_text`, `_box_holds_our_own_text`),
the #449 never-silent orphan floor + grace/channel-memory merge
(`_merge_grace_questions`, `_refresh_channel_memory`, `_refresh_posted_memory`,
`_orphan_floor`), the
ticket-fallback gh-comment lane (`_ticket_fallback_text`, `_gh_comment`) and ALL
eight `dreply` constants — was moved VERBATIM out of `watchdog/__init__.py` into
`watchdog.discord_replies`, then re-exported IN PLACE by a single positional
facade import at the earliest removed block's position (the constant
`DISCORD_REPLY_MAX_CHARS`, above every later removed block and above every
`__init__`-resident caller of the cluster, incl. `run_once`'s job-7 call).

Direction: BACK-REFERENCE. Every cross-cluster reference to a name living
OUTSIDE this module — still in `__init__` (`_orphan_answer_reason`/
`_orphan_ping_text`, the step-8 card/flag cluster, `_gh_call`,
`_DREPLY_TYPED_TTL_S`) or already relocated + re-exported by an earlier step
(`discord_api`, `stash`, `tmux_io`, `pane_text`, `pane_classify`,
`transcripts`, `cross_stream`) — is written call-time `watchdog.<name>` (module
top: `import watchdog`), so every existing `patch.object(watchdog, ...)` seam
stays live unchanged. The DI defaults are all call-time `None` + in-body
resolution (`discord_fetch or watchdog.fetch_channel_messages`, `gh_comment or
watchdog._gh_comment`, `card_gh_fn or watchdog._gh_call`), so NO def-time
binding on `discord_api`/`card_flags` exists and step 9 is safe to land before
steps 7/8. Co-moved names NO test patches at the package path stay bare
(module-local); `_gh_comment` and the three `clean_reply_text` regex/cap
constants ARE patched, so their references keep the `watchdog.` form. The one
def-time default (`deliver_discord_replies(projects_dir=PROJECTS_DIR)`) uses
`from watchdog import PROJECTS_DIR`.

These tests lock the invariants that keep those seams live:

  1. `import watchdog` stays clean in a FRESH subprocess, and importing
     `watchdog.discord_replies` FIRST initializes the package cleanly.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.discord_replies.<name>` (the C2 re-export contract), and the move
     ACTUALLY HAPPENED — none of the names is still defined in `__init__.py`.
  3. The call-time back-references RESOLVE and go through the PACKAGE seam, not a
     module-local/frozen copy — patch-observing teeth (design #1 hazard), the
     ONLY kind that catches a bare-name / module-top-`from`-import regression
     that every functional test passes right through.
"""

import ast
import subprocess
import sys
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.discord_replies as discord_replies  # noqa: E402

# The 10 functions moved into discord_replies.py, in definition order.
MOVED_FUNCS = [
    "_merge_grace_questions",
    "_refresh_channel_memory",
    "_refresh_posted_memory",
    "_orphan_floor",
    "_ticket_fallback_text",
    "_gh_comment",
    "compose_reply_prompt",
    "_record_dreply_typed",
    "_is_dreply_machine_text",
    "_box_holds_our_own_text",
    "deliver_discord_replies",
]
# The 8 dreply constants moved alongside them (design's explicit list).
MOVED_CONSTS = [
    "DISCORD_REPLY_MAX_CHARS",
    "_DREPLY_DONE_CAP",
    "_DQ_POSTED_CAP",
    "_MENTION_TOKEN_RX",
    "_CONTROL_CHAR_RX",
    "DREPLY_TICKET_FALLBACK_S",
    "DREPLY_NOPANE_FALLBACK_S",
    "_TICKET_NUM_RX",
    "DREPLY_CHANNEL_MEMORY_S",
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

    def test_import_discord_replies_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly — its `from watchdog import PROJECTS_DIR` (the ONE def-time
        # default) requires the parent package already bound, which Python
        # guarantees (parent imported before child). A real call through a pure
        # co-moved function proves the module loaded end-to-end.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.discord_replies as d; "
             "print(d.compose_reply_prompt({'text':'hi','question':''})[:2])"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertEqual(r.stdout.strip(), "hi")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(discord_replies, name),
                                f"{name} missing from watchdog.discord_replies")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name),
                              getattr(discord_replies, name),
                              f"watchdog.{name} is not "
                              f"watchdog.discord_replies.{name}")

    def test_discord_replies_lives_in_its_own_module_file(self):
        self.assertTrue(
            discord_replies.__file__.endswith("watchdog/discord_replies.py"),
            discord_replies.__file__)

    def test_moved_functions_report_discord_replies_as_their_module(self):
        for name in MOVED_FUNCS:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.discord_replies")


class MovedNamesChecklistIsSelfValidating(unittest.TestCase):
    """`MOVED_FUNCS`/`MOVED_CONSTS` are hand-maintained checklists every OTHER
    test trusts. Cross-check them against the three authoritative sources (the
    module's own top-level defs + module-level assigns, and the facade import
    block in `__init__.py`), so a moved-but-omitted name (or a listed-but-never-
    moved one) fails loudly here rather than silently reducing coverage."""

    def test_module_defines_exactly_the_moved_functions(self):
        src = Path(discord_replies.__file__).read_text(encoding="utf-8")
        defs = {n.name for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef)}
        self.assertEqual(defs, set(MOVED_FUNCS))

    def test_module_assigns_exactly_the_moved_constants(self):
        # The ONLY module-level assignments in discord_replies.py are the eight
        # dreply constants (imports are ImportFrom/Import, not Assign) — so a
        # ninth constant that crept in, or one of the eight left behind, fails.
        src = Path(discord_replies.__file__).read_text(encoding="utf-8")
        assigns = set()
        for n in ast.parse(src).body:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        assigns.add(t.id)
        self.assertEqual(assigns, set(MOVED_CONSTS))

    def test_facade_imports_exactly_the_moved_names(self):
        init_src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(init_src)
        imported = set()
        for node in tree.body:
            if (isinstance(node, ast.ImportFrom)
                    and node.module == "watchdog.discord_replies"):
                imported.update(a.name for a in node.names)
        self.assertEqual(imported, set(MOVED_NAMES))


class MoveActuallyHappenedNotACopy(unittest.TestCase):
    """A botched surgery that COPIED the cluster (leaving the originals in
    `__init__.py`) would leave dead duplicate defs behind the facade import.
    The move is only complete when `__init__.py` no longer DEFINES any of the
    names itself — it may only re-import them."""

    def _init_toplevel(self):
        init_src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(init_src)
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assigns = set()
        for n in tree.body:
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        assigns.add(t.id)
        return defs, assigns

    def test_no_moved_function_still_defined_in_init(self):
        defs, _ = self._init_toplevel()
        leftover = sorted(set(MOVED_FUNCS) & defs)
        self.assertEqual(leftover, [], f"still defined in __init__: {leftover}")

    def test_no_moved_constant_still_assigned_in_init(self):
        _, assigns = self._init_toplevel()
        leftover = sorted(set(MOVED_CONSTS) & assigns)
        self.assertEqual(leftover, [], f"still assigned in __init__: {leftover}")


class SeamsGoThroughPackage(unittest.TestCase):
    """Patch-observing teeth for the call-time `watchdog.<name>` back-references
    that live OUTSIDE this module. A bare name / module-top frozen `from`-import
    would pass every functional test yet ignore a `patch.object(watchdog, ...)`
    seam (design #1 hazard) — only these patch-observing tests catch it."""

    def test_box_holds_our_own_text_reads_find_input_box_via_watchdog(self):
        # _box_holds_our_own_text -> watchdog._find_input_box (pane_classify).
        # A non-wrapped box `("❯hello", "", False)` -> content "hello".
        with mock.patch.object(watchdog, "_find_input_box",
                               return_value=("❯hello", "", False)):
            self.assertTrue(
                watchdog._box_holds_our_own_text("cap", "please say hello"))
            self.assertFalse(
                watchdog._box_holds_our_own_text("cap", "unrelated text"))
        with mock.patch.object(watchdog, "_find_input_box", return_value=None):
            self.assertFalse(watchdog._box_holds_our_own_text("cap", "x"))

    def test_record_dreply_typed_reads_ttl_via_watchdog(self):
        # _record_dreply_typed prunes on watchdog._DREPLY_TYPED_TTL_S (the ONE
        # dreply constant NOT in the move list — stays in __init__). With the
        # real 48h TTL a 1000s-old entry is KEPT; patching TTL to 100 drops it.
        now = 10_000
        base = {"dreply_typed": {"old": {"head": "h", "tail": "t",
                                         "ts": now - 1000}}}
        st = {"dreply_typed": dict(base["dreply_typed"])}
        watchdog._record_dreply_typed(st, "new", "prompt", now)
        self.assertIn("old", st["dreply_typed"])  # real TTL keeps it

        st = {"dreply_typed": dict(base["dreply_typed"])}
        with mock.patch.object(watchdog, "_DREPLY_TYPED_TTL_S", 100):
            watchdog._record_dreply_typed(st, "new", "prompt", now)
        self.assertNotIn("old", st["dreply_typed"])  # patched TTL prunes it
        self.assertIn("new", st["dreply_typed"])

    def test_gh_comment_reads_gh_env_via_watchdog(self):
        # _gh_comment (no `user`) calls subprocess.run(..., env=watchdog._gh_env())
        # — a bare `_gh_env` would NameError, a frozen import would ignore this
        # package-level patch. Assert the sentinel env reaches subprocess.
        sentinel = {"SENTINEL_GH_ENV": "1"}
        fake = mock.Mock(returncode=0)
        with mock.patch.object(watchdog, "_gh_env", return_value=sentinel), \
                mock.patch("subprocess.run", return_value=fake) as run:
            ok = watchdog._gh_comment("/some/cwd", 5, "body text")
        self.assertTrue(ok)
        self.assertEqual(run.call_args.kwargs.get("env"), sentinel)

    def test_moved_constant_stays_patchable_through_reexport(self):
        # DISCORD_REPLY_MAX_CHARS physically MOVED to discord_replies.py, but its
        # only in-repo consumer (`clean_reply_text` in discord_api.py) reads it
        # as watchdog.DISCORD_REPLY_MAX_CHARS — the re-export must keep a
        # package-level patch effective across the module boundary.
        with mock.patch.object(watchdog, "DISCORD_REPLY_MAX_CHARS", 4):
            self.assertEqual(watchdog.clean_reply_text("abcdefgh"), "abcd")


class MovedFunctionsResolveEndToEnd(unittest.TestCase):
    """A pure functional call through each self-contained moved function — any
    unresolved bare cross-reference (a `watchdog.` prefix dropped by mistake)
    NameErrors here even without a patch."""

    def test_compose_reply_prompt_with_question(self):
        out = watchdog.compose_reply_prompt(
            {"question": "Merge?", "text": "yes 1", "asked_ts": 0})
        self.assertIn("Merge?", out)
        self.assertIn("yes 1", out)

    def test_ticket_fallback_text_embeds_question_and_answer(self):
        out = watchdog._ticket_fallback_text({"question": "Which?", "text": "opt 2"})
        self.assertIn("Which?", out)
        self.assertIn("opt 2", out)

    def test_is_dreply_machine_text_matches_recorded_head_and_tail(self):
        now = time.time()
        st = {}
        watchdog._record_dreply_typed(st, "%1", "HELLO world TAIL", now)
        rec = st["dreply_typed"]["%1"]
        # both ends must agree (#193): head starts, tail ends
        self.assertTrue(
            watchdog._is_dreply_machine_text(st, "%1", rec["head"][:5], rec["tail"][-4:]))
        self.assertFalse(
            watchdog._is_dreply_machine_text(st, "%1", "NOPE", "NOPE"))

    def test_refresh_channel_memory_expires_and_restamps(self):
        now = 1_000_000
        # a fresh channel is restamped now; a stale remembered one is expired.
        stale = now - watchdog.DREPLY_CHANNEL_MEMORY_S - 1
        chan_seen = {"old": {"ts": stale, "q": True}}
        kept = watchdog._refresh_channel_memory(chan_seen, ["chNew"], {"chNew"}, now)
        self.assertNotIn("old", kept)
        self.assertEqual(kept["chNew"]["ts"], now)
        self.assertTrue(kept["chNew"]["q"])


if __name__ == "__main__":
    unittest.main()
