"""#1015 — the managed launcher OWNS the Claude Code mouse / alternate-screen
environment, so a stray shell `export CLAUDE_CODE_DISABLE_MOUSE=1` (dev2's
unmanaged ~/.bashrc lines) can never take Claude Code's native scrolling away
again; `status` + conformance report the drift.

Design (Approach 1, items 1-3):
 (a) `render_claude_launch_script` (every mode) emits
     `unset CLAUDE_CODE_DISABLE_MOUSE CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN`
     BEFORE the exec line (an unconditional pre-`case` line, so it precedes
     EVERY mode's `exec claude`, including the vanilla `plain` escape hatch —
     the fleet default IS Claude Code's native mouse + alternate screen).
 (b) a real subprocess launch of the rendered script, with both vars exported
     in the PARENT shell, shows them ABSENT in the child (an `env`-dumping shim
     substituted for the `claude` binary via the script's own `$HOME/.local/bin`
     PATH seam).
 (c) `bashrc-drift` scan (leaf `cli_bashrc_drift`): a stray `export CLAUDE_CODE_*`
     line OUTSIDE the managed marker block is reported naming file:line; a clean
     file, or the SAME export INSIDE a managed block, reports nothing.
 (d) the daily conformance sweep (Job 34) carries a report-only `bashrc_drift`
     fact (the #1047 root_guard_provisioned pattern), injectable + never a raise.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The repo root is the worktree dir itself; add it to sys.path so the modules
# under test import as top-level.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_claude_scripts  # noqa: E402

_UNSET = "unset CLAUDE_CODE_DISABLE_MOUSE CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"


# --------------------------------------------------------------------------- #
# (a) static: the unset is present in every rendered mode, before the exec
# --------------------------------------------------------------------------- #
class TestLauncherUnsetsMouseEnv(unittest.TestCase):
    def _render(self):
        # render (model substituted) — the write site always uses the renderer,
        # never the raw constant.
        return cli_claude_scripts.render_claude_launch_script()

    def test_unset_present_in_rendered_launcher(self):
        content = self._render()
        self.assertIn(
            _UNSET, content,
            "the launcher must unset both CLAUDE_CODE_DISABLE_MOUSE and "
            "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN so a stray shell export can "
            "never win over the fleet default")

    def test_unset_is_a_single_pre_case_line(self):
        # A SINGLE unconditional line (not per-branch) — like the guarded
        # PRESSURE_REAP export — appearing exactly once, and BEFORE the mode
        # `case`, so it covers every mode including plain (which the guarded
        # exports deliberately skip). Exactly-once gives a mutant that scopes it
        # to one branch nowhere to hide.
        content = self._render()
        self.assertEqual(content.count(_UNSET), 1, content)
        case_idx = content.index('case "$mode" in')
        unset_idx = content.index(_UNSET)
        self.assertLess(
            unset_idx, case_idx,
            "the unset must be an unconditional line BEFORE the mode case, so "
            "every mode (plain included) inherits it")

    def test_unset_before_every_exec_claude(self):
        # Positive proof of "before the exec line" for EVERY mode: the unset's
        # line index precedes the FIRST `exec claude` (plain's), and since it is
        # a single pre-case line it precedes all the others too.
        lines = self._render().splitlines()
        unset_line = next(i for i, ln in enumerate(lines) if _UNSET in ln)
        exec_lines = [i for i, ln in enumerate(lines)
                      if ln.strip().startswith("exec claude")]
        self.assertTrue(exec_lines, "no `exec claude` line found in the launcher")
        for ei in exec_lines:
            self.assertLess(
                unset_line, ei,
                "the unset must appear before every `exec claude` line")

    def test_unset_is_unconditional_not_guarded_by_mode(self):
        # Unlike --model / PRESSURE_REAP (guarded `[ "$mode" = plain ] || ...`),
        # the unset applies to EVERY mode: the design says the fleet default is
        # native mouse + altscreen for plain too. So the unset line must NOT
        # carry a `plain` guard.
        content = self._render()
        for ln in content.splitlines():
            if _UNSET in ln:
                self.assertNotIn(
                    "plain", ln,
                    "the unset must be unconditional (plain gets it too), never "
                    "guarded by the mode")


# --------------------------------------------------------------------------- #
# (b) runtime: a real subprocess launch strips the inherited vars from the child
# --------------------------------------------------------------------------- #
class TestLauncherRuntimeStripsInheritedMouseEnv(unittest.TestCase):
    def _run_launcher(self, mode):
        """Render the launcher, substitute an env-dumping shim for `claude` via
        the script's own $HOME/.local/bin PATH seam, launch it with both mouse
        vars exported in the parent, and return the child's env dump (stdout)."""
        content = cli_claude_scripts.render_claude_launch_script()
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            script = tdp / "airuleset-claude-launch.sh"
            script.write_text(content)
            script.chmod(script.stat().st_mode | stat.S_IEXEC)

            # The claude "binary": a shim that ignores ALL args and just dumps
            # its own environment. `exec env` with no args prints KEY=VALUE lines
            # — so a mode that passes `--model ... --allowedTools ...` to claude
            # is fine (the shim never looks at $@). Placed at $HOME/.local/bin,
            # which the launcher prepends to PATH — so it wins over any real
            # claude on the parent PATH.
            binp = tdp / ".local" / "bin"
            binp.mkdir(parents=True)
            shim = binp / "claude"
            shim.write_text("#!/bin/sh\nexec /usr/bin/env\n")
            shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

            env = {
                # a clean base PATH; the launcher prepends $HOME/.local/bin so
                # the shim is found first.
                "PATH": "/usr/bin:/bin",
                "HOME": str(tdp),
                # the exact dev2 contamination, exported in the PARENT shell:
                "CLAUDE_CODE_DISABLE_MOUSE": "1",
                "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
            }
            proc = subprocess.run(
                ["bash", str(script), mode],
                env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(
                proc.returncode, 0,
                "launcher exited non-zero (mode=%s):\nstdout=%s\nstderr=%s"
                % (mode, proc.stdout, proc.stderr))
            return proc.stdout

    def test_plain_mode_child_has_neither_var(self):
        out = self._run_launcher("plain")
        # positive control: the env dump actually happened (HOME is always set),
        # so an absent MOUSE var means genuinely unset, not a failed dump.
        self.assertIn("HOME=", out, "env shim did not dump the environment")
        self.assertNotIn("CLAUDE_CODE_DISABLE_MOUSE", out,
                         "CLAUDE_CODE_DISABLE_MOUSE leaked into the child")
        self.assertNotIn("CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN", out,
                         "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN leaked into the child")

    def test_default_mode_child_has_neither_var(self):
        out = self._run_launcher("default")
        self.assertIn("HOME=", out, "env shim did not dump the environment")
        self.assertNotIn("CLAUDE_CODE_DISABLE_MOUSE", out,
                         "CLAUDE_CODE_DISABLE_MOUSE leaked into the child")
        self.assertNotIn("CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN", out,
                         "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN leaked into the child")


# --------------------------------------------------------------------------- #
# (c) the bashrc-drift scan (leaf cli_bashrc_drift)
# --------------------------------------------------------------------------- #
class TestBashrcDriftScan(unittest.TestCase):
    def _write(self, td, text):
        p = Path(td) / ".bashrc"
        p.write_text(text)
        return p

    def test_stray_export_outside_block_is_reported_with_file_and_line(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            body = (
                "# a normal bashrc\n"
                "export PATH=$HOME/.local/bin:$PATH\n"
                "export CLAUDE_CODE_DISABLE_MOUSE=1\n"   # line 3 — the drift
                "alias ll='ls -la'\n"
            )
            p = self._write(td, body)
            hits = bd.scan_bashrc_drift([str(p)])
            self.assertEqual(len(hits), 1, hits)
            path, lineno, line = hits[0]
            self.assertEqual(str(path), str(p))
            self.assertEqual(lineno, 3, "must name the exact stray export line")
            self.assertIn("CLAUDE_CODE_DISABLE_MOUSE", line)

            row = bd.bashrc_drift_status_row([str(p)])
            self.assertIsNotNone(row)
            self.assertIn("bashrc-drift:", row)
            self.assertIn("%s:3" % p, row)
            self.assertIn("CLAUDE_CODE_DISABLE_MOUSE", row)

    def test_clean_bashrc_reports_nothing(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            p = self._write(
                td,
                "# clean\nexport PATH=$HOME/.local/bin:$PATH\nalias ll='ls -la'\n")
            self.assertEqual(bd.scan_bashrc_drift([str(p)]), [])
            self.assertIsNone(bd.bashrc_drift_status_row([str(p)]))

    def test_export_inside_managed_block_is_not_drift(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            # the SAME export, but INSIDE a managed marker block — the launcher/
            # helpers own that region, so it is configuration, not drift.
            p = self._write(
                td,
                "# top\n"
                "# >>> airuleset: ultracode default >>>\n"
                "export CLAUDE_CODE_DISABLE_MOUSE=1\n"
                "# <<< airuleset: ultracode default <<<\n"
                "alias ll='ls -la'\n")
            self.assertEqual(bd.scan_bashrc_drift([str(p)]), [])
            self.assertIsNone(bd.bashrc_drift_status_row([str(p)]))

    def test_commented_export_is_not_drift(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, "# export CLAUDE_CODE_DISABLE_MOUSE=1\n")
            self.assertEqual(bd.scan_bashrc_drift([str(p)]), [])

    def test_missing_file_is_not_an_error(self):
        import cli_bashrc_drift as bd
        # a non-existent path is skipped silently (a box may have no ~/.profile).
        missing = "/nonexistent/definitely-not-here/.bashrc"
        self.assertEqual(bd.scan_bashrc_drift([missing]), [])
        self.assertIsNone(bd.bashrc_drift_status_row([missing]))

    def test_multiple_strays_across_files_all_reported(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / ".bashrc"
            b.write_text("export CLAUDE_CODE_DISABLE_MOUSE=1\n")
            pr = Path(td) / ".profile"
            pr.write_text("x=1\nexport CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1\n")
            hits = bd.scan_bashrc_drift([str(b), str(pr)])
            self.assertEqual(len(hits), 2, hits)
            row = bd.bashrc_drift_status_row([str(b), str(pr)])
            self.assertIn("%s:1" % b, row)
            self.assertIn("%s:2" % pr, row)

    def test_count_matches_scan(self):
        import cli_bashrc_drift as bd
        with tempfile.TemporaryDirectory() as td:
            p = self._write(
                td, "export CLAUDE_CODE_DISABLE_MOUSE=1\nexport CLAUDE_CODE_X=1\n")
            self.assertEqual(bd.count_bashrc_drift([str(p)]), 2)


# --------------------------------------------------------------------------- #
# (d) conformance carries the report-only bashrc_drift fact (#1047 pattern)
# --------------------------------------------------------------------------- #
class TestConformanceBashrcDriftFact(unittest.TestCase):
    def _run_conf(self, state, td, drift_fn, dry_run=False):
        from watchdog import conformance as conf
        cmd = str(Path(td) / "CLAUDE.md")
        Path(cmd).write_text("managed\n")
        base = str(Path(td) / conf.CONFORMANCE_BASELINE_NAME)
        Path(base).write_text('{"claude_md_md5": %r, "head_sha": "aaaa1111"}'
                              % conf._md5_file(cmd))

        def _git(args, cwd, timeout=None):
            sub = args[0]
            if sub == "rev-parse":
                return (0, "aaaa1111\n")
            return (0, "")

        return conf.run_conformance_check(
            1_000_000, state, dry_run=dry_run, repo_root=td,
            claude_md_path=cmd, baseline_path=base,
            git_run=_git, timer_check=lambda unit=None: "active",
            is_target_check=lambda: True, symlink_scan=lambda: [],
            doctrine_scan=lambda: {"high": 0, "medium": 0},
            persist=lambda: None,
            root_guard_provisioned_fn=lambda: True,
            bashrc_drift_fn=drift_fn)

    def test_conformance_records_the_drift_count(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            self._run_conf(state, td, drift_fn=lambda: 2)
            self.assertEqual(state.get("bashrc_drift"), 2)

    def test_conformance_records_zero_when_clean(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            self._run_conf(state, td, drift_fn=lambda: 0)
            self.assertEqual(state.get("bashrc_drift"), 0)

    def test_conformance_uses_the_injected_drift_fn_once(self):
        called = {"n": 0}

        def _fn():
            called["n"] += 1
            return 1

        with tempfile.TemporaryDirectory() as td:
            self._run_conf({}, td, drift_fn=_fn)
        self.assertEqual(called["n"], 1,
                         "the conformance job must call the drift fn exactly once")

    def test_conformance_dry_run_does_not_persist_the_fact(self):
        with tempfile.TemporaryDirectory() as td:
            state = {}
            self._run_conf(state, td, drift_fn=lambda: 3, dry_run=True)
            self.assertNotIn("bashrc_drift", state)

    def test_conformance_drift_fn_error_degrades_to_unknown_never_raises(self):
        def _boom():
            raise RuntimeError("scan blew up")

        with tempfile.TemporaryDirectory() as td:
            state = {}
            # must not raise; the fact is simply not recorded (None/absent).
            self._run_conf(state, td, drift_fn=_boom)
            self.assertIsNone(state.get("bashrc_drift"))

    def test_status_row_shows_drift_suffix_when_positive(self):
        from watchdog.conformance import conformance_status_row
        row = conformance_status_row(
            {"conformance_last_check": 1, "bashrc_drift": 2})
        self.assertIn("bashrc-drift: 2", row)

    def test_status_row_omits_suffix_when_zero(self):
        from watchdog.conformance import conformance_status_row
        row = conformance_status_row(
            {"conformance_last_check": 1, "bashrc_drift": 0})
        self.assertNotIn("bashrc-drift", row,
                         "a clean box (0 drift) must not add a noisy suffix")

    def test_status_row_omits_suffix_when_absent(self):
        from watchdog.conformance import conformance_status_row
        row = conformance_status_row({"conformance_last_check": 1})
        self.assertNotIn("bashrc-drift", row)


if __name__ == "__main__":
    unittest.main()
