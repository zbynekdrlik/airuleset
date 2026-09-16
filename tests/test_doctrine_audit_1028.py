"""#1028 — doctrine-drift audit + anti-drift guard.

A rule that GRADUATES to the fleet (an airuleset module/skill) leaves its
per-stream restatement behind in `~/.claude/projects/*/memory/*.md` (and
project `.claude/rules/*.md`), each in slightly different wording, so the same
doctrine DRIFTS across streams (the #1027/#1033 ack-emoji incident). This
suite locks the matcher, the archive+pointer rewrite, and the report-only
conformance dimension `doctrine-drift`.

RED-before-GREEN: `cli_doctrine_audit` does not exist yet, so every test in
this file fails on import / attribute-error until the GREEN commit lands the
module + the conformance dimension.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cli_doctrine_audit as da
from watchdog import conformance as conf


# --------------------------------------------------------------------------
# Fixture — a fake stream home carrying the two known miva1 restatements +
# one owner-preference memory (must be KEPT) + one fleet-installed file
# (a symlink, must be SKIPPED).
# --------------------------------------------------------------------------
WORKER_REACTION = """\
---
name: client-message-worker-reaction-no-workarounds
description: "React worker on a client message; never send an interim workaround"
metadata:
  node_type: memory
  type: project
---
# Client message: react worker, no workarounds

When a client message arrives, add the ack reaction (the worker 👷,
`ack_reaction_emoji`) BEFORE composing any reply. Do not send an interim
workaround while the fix is in flight — reply once, after the fix is on PROD.
"""

MIVA_ZERO_MANUAL = """\
---
name: miva-zero-manual-attendance-work
description: "MIVA owner goal — zero manual attendance work + generic no-workaround clause"
metadata:
  node_type: memory
  type: project
---
# MIVA — zero manual attendance work

Client-specific: for the MIVA tenant the attendance import must be fully
automatic — never ask the MIVA client to key attendance rows by hand.

Generic clause (graduated): never push manual work onto the client — no interim
workaround while the fix is in flight; reply once, after the fix is on PROD.
"""

# A user-CONTEXT memory that merely MENTIONS a workaround (for the USER, not a
# client) must NOT match — the #1028 live-smoke false positive
# (`user_terminal_environment.md`). `type: user` is owner/user context, kept.
USER_TERMINAL_NOTE = """\
---
name: user-terminal-environment
description: "The user's terminal — only claude panes; never offer a CLI workaround"
metadata:
  node_type: memory
  type: user
---
# User terminal environment

Interim workaround for the user = only what he can do from a claude pane. Never
offer a shell workaround; it just annoys him.
"""

OWNER_PREF_FEEDBACK = """\
---
name: feedback-ack-emoji-owner-preference
description: "How the owner wants the ack reaction handled on his channel"
metadata:
  node_type: memory
  type: feedback
---
# Owner preference — ack reaction emoji

The owner wants the worker 👷 (`ack_reaction_emoji`) on his client channel;
this note records HIS standing preference, not a copy of the fleet rule.
"""

FLEET_INSTALLED = """\
# Acknowledging a client message — worker reaction

React with the worker 👷 (`ack_reaction_emoji`) before replying.
"""


def _make_fake_home():
    """Return (tmpdir_obj, home_path) with a populated memory dir. Caller
    keeps tmpdir_obj alive for the test's duration."""
    tmp = TemporaryDirectory()
    home = Path(tmp.name)
    mem = home / ".claude" / "projects" / "-home-miva1-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "client-message-worker-reaction-no-workarounds.md").write_text(
        WORKER_REACTION, encoding="utf-8")
    (mem / "miva-zero-manual-attendance-work.md").write_text(
        MIVA_ZERO_MANUAL, encoding="utf-8")
    (mem / "feedback_ack_emoji_owner_preference.md").write_text(
        OWNER_PREF_FEEDBACK, encoding="utf-8")
    (mem / "user_terminal_environment.md").write_text(
        USER_TERMINAL_NOTE, encoding="utf-8")
    # A fleet-installed file: a real file elsewhere + a SYMLINK into the memory
    # dir (the shape a managed install produces). The scan must SKIP symlinks.
    fleet_real = home / ".claude" / "skills-src" / "ack-reaction.md"
    fleet_real.parent.mkdir(parents=True)
    fleet_real.write_text(FLEET_INSTALLED, encoding="utf-8")
    os.symlink(str(fleet_real), str(mem / "fleet_installed_ack.md"))
    return tmp, home


def _by_name(matches):
    return {os.path.basename(m.path): m for m in matches}


# --------------------------------------------------------------------------
# MATCHER
# --------------------------------------------------------------------------
class TestMatcher(unittest.TestCase):
    def test_pure_restatement_is_high_rewrite(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            by = _by_name(matches)
            m = by["client-message-worker-reaction-no-workarounds.md"]
            self.assertEqual(m.confidence, da.HIGH)
            self.assertEqual(m.action, da.ACTION_REWRITE)
            self.assertIn("odoo-client-messaging", m.fleet_source)

    def test_client_specific_mixed_note_is_medium_list(self):
        # Has a graduated "never push manual work onto the client" clause AND a
        # MIVA-specific keep-part → MEDIUM, listed only (human keeps the client
        # part), NEVER auto-rewritten.
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            by = _by_name(matches)
            m = by["miva-zero-manual-attendance-work.md"]
            self.assertEqual(m.confidence, da.MEDIUM)
            self.assertEqual(m.action, da.ACTION_LIST)

    def test_owner_preference_feedback_is_kept(self):
        # A feedback memory recording an OWNER preference is NEVER touched, even
        # when it anchor-matches the graduated rule.
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            by = _by_name(matches)
            m = by["feedback_ack_emoji_owner_preference.md"]
            self.assertEqual(m.action, da.ACTION_KEEP)

    def test_owner_preference_never_counts_as_actionable(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            pref = _by_name(matches)["feedback_ack_emoji_owner_preference.md"]
            self.assertNotIn(pref.action, (da.ACTION_REWRITE, da.ACTION_LIST))

    def test_generic_workaround_mention_is_not_matched(self):
        # A user-context note that merely mentions a workaround (for the USER,
        # not a client) must not anchor-match — the #1028 live-smoke false
        # positive. `type: user` is owner/user context → never a rewrite.
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            by = _by_name(matches)
            m = by.get("user_terminal_environment.md")
            # Either no row at all, or (if it anchor-matched) action=keep — never
            # a rewrite/list.
            if m is not None:
                self.assertEqual(m.action, da.ACTION_KEEP)

    def test_type_user_memory_is_kept_not_rewritten(self):
        tmp, home = _make_fake_home()
        with tmp:
            da.apply_fixes(da.scan_home(str(home)), str(home), today="2026-09-16")
            note = home / (".claude/projects/-home-miva1-proj/memory/"
                           "user_terminal_environment.md")
            self.assertFalse(note.read_text(encoding="utf-8").startswith(
                "See airuleset "))

    def test_fleet_installed_symlink_is_skipped(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            names = set(_by_name(matches))
            self.assertNotIn("fleet_installed_ack.md", names,
                             "a symlink (fleet-installed) must not be scanned")

    def test_repo_dir_files_skipped(self):
        # A memory file whose realpath is under the airuleset repo dir (the fleet
        # SOURCE) is never a drift copy.
        tmp, home = _make_fake_home()
        with tmp:
            repo = home / ".claude" / "projects" / "-home-miva1-proj"
            matches = da.scan_home(str(home), repo_dir=str(repo))
            for m in matches:
                self.assertFalse(os.path.realpath(m.path).startswith(
                    os.path.realpath(str(repo)) + os.sep))

    def test_fuzzy_heading_match_is_medium(self):
        # A memory that does not anchor-match the allowlist but whose heading is
        # near an airuleset module/skill heading → MEDIUM (listed).
        tmp = TemporaryDirectory()
        with tmp:
            home = Path(tmp.name)
            mem = home / ".claude" / "projects" / "-p" / "memory"
            mem.mkdir(parents=True)
            (mem / "device-notifs.md").write_text(
                "---\nname: device-notifs\n---\n"
                "# Device Notifications — Mobile-App Model: Only When ASKING\n\n"
                "Ping the device only on a question or a done state.\n",
                encoding="utf-8")
            headings = ["Device Notifications — Mobile-App Model: "
                        "Only When ASKING or FULLY DONE"]
            matches = da.scan_home(str(home), module_headings=headings)
            by = _by_name(matches)
            self.assertIn("device-notifs.md", by)
            self.assertEqual(by["device-notifs.md"].confidence, da.MEDIUM)

    def test_already_pointer_is_not_rematched(self):
        tmp = TemporaryDirectory()
        with tmp:
            home = Path(tmp.name)
            mem = home / ".claude" / "projects" / "-p" / "memory"
            mem.mkdir(parents=True)
            (mem / "x.md").write_text(
                "See airuleset skills/odoo-client-messaging/ack-reaction.md#H "
                "— fleet rule since 0.1.303; local copy retired 2026-09-16\n",
                encoding="utf-8")
            self.assertEqual(da.scan_home(str(home)), [])


# --------------------------------------------------------------------------
# REWRITE — archive + one-line pointer, idempotent, home-scoped
# --------------------------------------------------------------------------
class TestRewrite(unittest.TestCase):
    def test_fix_rewrites_high_and_archives(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            res = da.apply_fixes(matches, str(home), today="2026-09-16")
            target = home / (".claude/projects/-home-miva1-proj/memory/"
                             "client-message-worker-reaction-no-workarounds.md")
            body = target.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("See airuleset "))
            self.assertIn("fleet rule since", body)
            # original archived verbatim
            arch = (home / ".claude" / "doctrine-archive" / "2026-09-16" /
                    "projects" / "-home-miva1-proj" / "memory" /
                    "client-message-worker-reaction-no-workarounds.md")
            self.assertTrue(arch.exists())
            self.assertIn("worker 👷", arch.read_text(encoding="utf-8"))
            self.assertEqual(
                [os.path.basename(p) for p in res["rewritten"]],
                ["client-message-worker-reaction-no-workarounds.md"])

    def test_fix_does_not_touch_medium(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches = da.scan_home(str(home))
            da.apply_fixes(matches, str(home), today="2026-09-16")
            miva = home / (".claude/projects/-home-miva1-proj/memory/"
                           "miva-zero-manual-attendance-work.md")
            self.assertIn("zero manual attendance",
                          miva.read_text(encoding="utf-8"))
            self.assertFalse(miva.read_text(encoding="utf-8").startswith(
                "See airuleset "))

    def test_fix_is_idempotent(self):
        tmp, home = _make_fake_home()
        with tmp:
            da.apply_fixes(da.scan_home(str(home)), str(home),
                           today="2026-09-16")
            # second pass: nothing left to rewrite
            res2 = da.apply_fixes(da.scan_home(str(home)), str(home),
                                  today="2026-09-17")
            self.assertEqual(res2["rewritten"], [])
            # no second archive dir created
            self.assertFalse(
                (home / ".claude" / "doctrine-archive" / "2026-09-17").exists())

    def test_rewrite_refuses_outside_home(self):
        tmp, home = _make_fake_home()
        with tmp:
            outside = TemporaryDirectory()
            with outside:
                bogus = Path(outside.name) / "evil.md"
                bogus.write_text("x", encoding="utf-8")
                m = da.DoctrineMatch(
                    path=str(bogus), fleet_source="s", heading="h",
                    fleet_since="0.1.303", confidence=da.HIGH,
                    action=da.ACTION_REWRITE, anchors_hit=2)
                with self.assertRaises(ValueError):
                    da.archive_and_rewrite(m, str(home), today="2026-09-16")


# --------------------------------------------------------------------------
# COUNTS + audit()
# --------------------------------------------------------------------------
class TestCounts(unittest.TestCase):
    def test_counts_high_and_medium(self):
        tmp, home = _make_fake_home()
        with tmp:
            counts = da.doctrine_counts(da.scan_home(str(home)))
            self.assertEqual(counts["high"], 1)
            self.assertEqual(counts["medium"], 1)

    def test_counts_after_fix_high_zero(self):
        tmp, home = _make_fake_home()
        with tmp:
            da.apply_fixes(da.scan_home(str(home)), str(home),
                           today="2026-09-16")
            counts = da.doctrine_counts(da.scan_home(str(home)))
            self.assertEqual(counts["high"], 0)
            self.assertEqual(counts["medium"], 1)

    def test_audit_fix_returns_matches_and_results(self):
        tmp, home = _make_fake_home()
        with tmp:
            matches, res = da.audit(str(home), fix=True, today="2026-09-16")
            self.assertTrue(any(m.action == da.ACTION_REWRITE for m in matches))
            self.assertEqual(len(res["rewritten"]), 1)

    def test_format_table_lists_all_columns(self):
        tmp, home = _make_fake_home()
        with tmp:
            table = da.format_table(da.scan_home(str(home)))
            self.assertIn("client-message-worker-reaction", table)
            self.assertIn("high", table)
            self.assertIn("medium", table)


# --------------------------------------------------------------------------
# CONFORMANCE dimension `doctrine-drift`  (#1032: report-only, no owner ping)
# --------------------------------------------------------------------------
class TestClassifyDoctrineDrift(unittest.TestCase):
    def test_none_is_undetermined(self):
        dim, ok, _ = conf.classify_doctrine_drift(None)
        self.assertEqual(dim, "doctrine-drift")
        self.assertIsNone(ok)

    def test_zero_is_conformant(self):
        _, ok, _ = conf.classify_doctrine_drift({"high": 0, "medium": 0})
        self.assertIs(ok, True)

    def test_nonzero_is_drift(self):
        _, ok, detail = conf.classify_doctrine_drift({"high": 0, "medium": 2})
        self.assertIs(ok, False)
        self.assertIn("2", detail)

    def test_high_unfixed_is_drift(self):
        _, ok, detail = conf.classify_doctrine_drift({"high": 1, "medium": 0})
        self.assertIs(ok, False)


NOW = 1_000_000.0


def _clean_git(args, cwd, timeout=None):
    """A ``git_run(args, cwd) -> (rc, stdout)`` seam with every other dimension
    conformant: HEAD == origin (not behind), clean tree, fetch ok."""
    sub = args[0]
    if sub == "rev-parse":
        return (0, "aaaa1111\n")
    if sub == "fetch":
        return (0, "")
    if sub == "merge-base":
        return (0, "")          # HEAD is ancestor of origin (== origin) → not behind
    if sub == "status":
        return (0, "")          # clean
    return (0, "")


def _run_with_doctrine(doctrine_counts):
    """Drive run_conformance_check with all other dimensions conformant/clean
    and an injected doctrine scan returning `doctrine_counts`."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cmd = os.path.join(d, "CLAUDE.md")
        with open(cmd, "w") as fh:
            fh.write("x\n")
        state = {}
        return conf.run_conformance_check(
            NOW, state, repo_root=os.getcwd(), claude_md_path=cmd,
            baseline_path=os.path.join(d, "baseline.json"),
            git_run=_clean_git,
            timer_check=lambda *a, **k: "active",
            is_target_check=lambda: False,
            symlink_scan=lambda: [],
            doctrine_scan=lambda: doctrine_counts,
            persist=lambda: None), state


class TestConformanceDimensionWired(unittest.TestCase):
    def test_doctrine_dimension_present_and_ok(self):
        logs, _ = _run_with_doctrine({"high": 0, "medium": 0})
        self.assertTrue(any("[doctrine-drift] OK" in ln for ln in logs))

    def test_doctrine_drift_surfaces(self):
        logs, state = _run_with_doctrine({"high": 0, "medium": 2})
        self.assertTrue(any("[doctrine-drift]" in ln and "SURFACED" in ln
                            for ln in logs))
        self.assertIn("doctrine-drift", state.get("conformance", {}))

    def test_doctrine_scan_error_undetermined(self):
        logs, _ = _run_with_doctrine(None)
        self.assertTrue(any("[doctrine-drift] unknown" in ln for ln in logs))
        self.assertFalse(any("[doctrine-drift]" in ln and "SURFACED" in ln
                             for ln in logs))


# --------------------------------------------------------------------------
# INSTALL step helper — factored for direct testability (#410 review F2 idiom)
# --------------------------------------------------------------------------
class TestInstallStep(unittest.TestCase):
    def test_install_step_fixes_high_for_own_home(self):
        import airuleset
        tmp, home = _make_fake_home()
        with tmp:
            lines = airuleset._run_doctrine_audit_step(
                home=str(home), repo_dir=None, today="2026-09-16")
            target = home / (".claude/projects/-home-miva1-proj/memory/"
                             "client-message-worker-reaction-no-workarounds.md")
            self.assertTrue(target.read_text(encoding="utf-8").startswith(
                "See airuleset "))
            self.assertTrue(any("client-message-worker-reaction" in ln
                                for ln in lines))

    def test_install_step_leaves_medium_and_keep(self):
        import airuleset
        tmp, home = _make_fake_home()
        with tmp:
            airuleset._run_doctrine_audit_step(
                home=str(home), repo_dir=None, today="2026-09-16")
            miva = home / (".claude/projects/-home-miva1-proj/memory/"
                           "miva-zero-manual-attendance-work.md")
            pref = home / (".claude/projects/-home-miva1-proj/memory/"
                           "feedback_ack_emoji_owner_preference.md")
            self.assertFalse(miva.read_text(encoding="utf-8").startswith(
                "See airuleset "))
            self.assertFalse(pref.read_text(encoding="utf-8").startswith(
                "See airuleset "))

    def test_install_step_non_fatal_on_bad_home(self):
        import airuleset
        # A non-existent home must not raise — best-effort, never fails install.
        lines = airuleset._run_doctrine_audit_step(
            home="/nonexistent/doctrine/home", repo_dir=None,
            today="2026-09-16")
        self.assertIsInstance(lines, list)


if __name__ == "__main__":
    unittest.main()
