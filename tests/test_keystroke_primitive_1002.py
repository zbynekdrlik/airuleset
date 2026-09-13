"""#1002 — architecture-rework: ONE keystroke primitive for all machine keys.

The REWORK verdict from the #994 main review: raw ``tmux send-keys`` argv was
built at ~40 call sites across 8 watchdog files, so the #994 kill switch needed
9 gate sites (literal typing had a primitive; control keys — Escape/Enter/C-s/
BSpace/`-H` hex — were sent straight from each helper). This suite locks the
target concept:

* ``watchdog.keys(pane_id, *keystrokes, kind, ...)`` is the SINGLE place that
  builds a ``tmux send-keys`` argv (control keys, ``-l --`` literal, ``-H`` hex).
* The gate (``nudges_enabled`` + future owner switches) is evaluated ONCE inside
  ``keys`` via ``_keystroke_suppressed(kind, user_authored)``; the journal line
  ``nudges OFF: suppressed <kind> ...`` is written there and nowhere else.
* Repo lock (AST): no ``send-keys`` argv is built anywhere in ``watchdog/``
  except inside ``keys``; no ``paste-buffer``/``load-buffer`` typing path exists.
* Kind contract: every ``kind=`` literal passed to the keystroke primitives is
  classified (GATED machine-nudge vs RECOVERY), and the two sets are disjoint.
"""

import ast
import sys
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402

PID = "%9"
WATCHDOG_DIR = REPO / "watchdog"


class _Recorder:
    """A fake `run` that records every argv and echoes "" — a suppressed
    keystroke must never reach it, so `sent_keys()` stays empty."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=8):
        self.calls.append(argv)
        return ""

    def sent_keys(self):
        return [a for a in self.calls if "send-keys" in " ".join(map(str, a))]


# --------------------------------------------------------------------------- #
# The primitive exists and gates by kind.
# --------------------------------------------------------------------------- #
class TestKeysPrimitive(unittest.TestCase):
    def _off(self):
        return m.patch.object(wd, "nudges_enabled", lambda *a, **k: False)

    def test_keys_and_helpers_exist(self):
        self.assertTrue(callable(getattr(wd, "keys", None)))
        self.assertTrue(callable(getattr(wd, "_keystroke_suppressed", None)))
        self.assertIsInstance(wd.GATED_KINDS, frozenset)
        self.assertIsInstance(wd.RECOVERY_KINDS, frozenset)

    def test_keys_sends_control_key_when_on(self):
        rec = _Recorder()
        ok = wd.keys(PID, "Enter", kind="continue", run=rec)
        self.assertTrue(ok)
        self.assertEqual(rec.calls, [["tmux", "send-keys", "-t", PID, "Enter"]])

    def test_keys_builds_byte_identical_argv_for_multi_key(self):
        rec = _Recorder()
        wd.keys(PID, "BSpace", "BSpace", "BSpace", kind="janitor", run=rec)
        self.assertEqual(
            rec.calls,
            [["tmux", "send-keys", "-t", PID, "BSpace", "BSpace", "BSpace"]])

    def test_gated_kind_suppressed_at_off(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.keys(PID, "Enter", kind="goal", run=rec, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self.assertTrue(any("nudges OFF: suppressed goal" in ln for ln in logs),
                        "expected a journal line: %r" % logs)

    def test_gated_kind_journal_uses_journal_text(self):
        rec = _Recorder()
        logs = []
        with self._off():
            wd.keys(PID, "-l", "--", "lane-check: backlog=5", kind="send",
                    run=rec, logs=logs, journal_text="lane-check: backlog=5")
        self.assertTrue(any("lane-check: backlog=5" in ln for ln in logs),
                        "journal should carry the payload snippet: %r" % logs)

    def test_recovery_kind_runs_at_off(self):
        # A recovery / cleanup keystroke (janitor / undo / wedge / resurrect /
        # user-draft) is NOT a machine nudge and MUST fire even when nudges are
        # OFF — the janitor must clean up a stranded nudge, an undo must back
        # our own text off, a user-draft submit forwards the OWNER's own prompt.
        for kind in ("janitor", "undo", "wedge", "resurrect", "user-draft"):
            rec = _Recorder()
            with self._off():
                ok = wd.keys(PID, "C-s", kind=kind, run=rec)
            self.assertTrue(ok, "recovery kind %r must run at OFF" % kind)
            self.assertEqual(len(rec.sent_keys()), 1,
                             "recovery kind %r must send at OFF" % kind)

    def test_user_authored_bypasses_gate_at_off(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.keys(PID, "Enter", kind="goal", run=rec, logs=logs,
                         user_authored=True)
        self.assertTrue(ok)
        self.assertEqual(len(rec.sent_keys()), 1)
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs))

    def test_keystroke_suppressed_predicate(self):
        with self._off():
            self.assertTrue(wd._keystroke_suppressed("goal", False))
            self.assertFalse(wd._keystroke_suppressed("goal", True))   # owner reply
            self.assertFalse(wd._keystroke_suppressed("janitor", False))  # recovery
        with m.patch.object(wd, "nudges_enabled", lambda *a, **k: True):
            self.assertFalse(wd._keystroke_suppressed("goal", False))


# --------------------------------------------------------------------------- #
# Repo lock — AST: `send-keys` argv is built ONLY in `tmux_io.keys`.
# --------------------------------------------------------------------------- #
class TestSendKeysArgvRepoLock(unittest.TestCase):
    @staticmethod
    def _list_is_argv(node, second):
        """True iff `node` is an `["tmux", "<second>", ...]` list literal."""
        if not isinstance(node, ast.List) or len(node.elts) < 2:
            return False
        a, b = node.elts[0], node.elts[1]
        return (isinstance(a, ast.Constant) and a.value == "tmux"
                and isinstance(b, ast.Constant) and b.value == second)

    def _enclosing_func(self, tree, target):
        """The name of the innermost FunctionDef enclosing `target`, or None."""
        best = None
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(fn):
                if sub is target:
                    best = fn.name
        return best

    def test_only_keys_builds_send_keys_argv(self):
        offenders = []
        for path in sorted(WATCHDOG_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not self._list_is_argv(node, "send-keys"):
                    continue
                fn = self._enclosing_func(tree, node)
                if not (path.name == "tmux_io.py" and fn == "keys"):
                    offenders.append("%s::%s" % (path.name, fn))
        self.assertEqual(
            offenders, [],
            "send-keys argv must be built ONLY in tmux_io.keys; found: %r"
            % offenders)

    def test_no_paste_or_load_buffer_typing_path(self):
        offenders = []
        for path in sorted(WATCHDOG_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (self._list_is_argv(node, "paste-buffer")
                        or self._list_is_argv(node, "load-buffer")):
                    offenders.append(path.name)
        self.assertEqual(offenders, [],
                         "no paste-buffer/load-buffer typing path may exist: %r"
                         % offenders)


# --------------------------------------------------------------------------- #
# Kind contract — every `kind=` literal used with the keystroke primitives is
# classified; GATED and RECOVERY are disjoint and cover every used kind.
# --------------------------------------------------------------------------- #
class TestKindContract(unittest.TestCase):
    PRIMITIVES = {"keys", "type_literal", "_type_literal", "_type_literal_verified",
                  "_type_two_phase_head_checkpoint", "deliver_with_stash",
                  "send_continue", "send_verified", "submit_own_draft_verified",
                  "submit_own_goal_verified", "_send_goal_verified"}

    def test_gated_and_recovery_disjoint(self):
        self.assertEqual(wd.GATED_KINDS & wd.RECOVERY_KINDS, frozenset())

    def test_every_kind_literal_is_classified(self):
        known = wd.GATED_KINDS | wd.RECOVERY_KINDS
        seen = set()
        for path in sorted(WATCHDOG_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "attr", getattr(fn, "id", None))
                if name not in self.PRIMITIVES:
                    continue
                for kw in node.keywords:
                    if kw.arg in ("kind", "nudge_kind") and isinstance(kw.value, ast.Constant):
                        seen.add(kw.value.value)
        unknown = {k for k in seen if k not in known}
        self.assertEqual(
            unknown, set(),
            "every kind= literal passed to a keystroke primitive must be in "
            "GATED_KINDS or RECOVERY_KINDS; unclassified: %r" % sorted(unknown))
        # Sanity: we actually saw the core machine-nudge kinds.
        self.assertTrue({"goal", "send", "continue"} <= seen,
                        "expected the core nudge kinds to be used; saw %r" % sorted(seen))


if __name__ == "__main__":
    unittest.main()
