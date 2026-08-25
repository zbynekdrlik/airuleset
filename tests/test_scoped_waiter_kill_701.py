"""#701 — scoped waiter kills: doctrine + hook content locks.

montalu1 (odoo-erp, 2026-08-25) cancelled its own mistakenly ``&``-detached
CI waiter with ``pkill -f "sleep 60"`` on the SHARED subdev box — a pattern
that also matched montalu3's live waiters, because the liveness/CI recipes
(`modules/core/ci-monitoring.md` background waiter, the
`verify-launched-work-liveness` skill's until-loop) deliberately mint
byte-IDENTICAL waiter bodies on every stream fleet-wide. A killed foreign
waiter leaves the other stream waiting forever (a dead process sends no
"done"), so a broad kill-by-pattern is a fleet-wide friendly-fire vector.

These tests lock the #701 outcome:

* the `verify-launched-work-liveness` skill (the waiter-lifecycle owner)
  carries the scoped-kill doctrine — kill by the PID you already hold, or
  by a unique discriminator embedded in the command body (the run-id),
  ``-u "$USER"`` as defense-in-depth, generic patterns banned by name;
* `modules/core/ci-monitoring.md` carries a one-line risk-site pointer at
  the recipe that mints the identical bodies;
* `hooks/block-broad-pkill.sh` exists, is wired under PreToolUse(Bash),
  and blocks exactly the high-confidence generic shapes (bare
  ``sleep``/``sleep N``/``gh run view`` patterns, ``killall sleep``) while
  leaving scoped kills, read-only ``pgrep``, quoted prose, and heredoc
  documentation bodies untouched — failing OPEN on malformed input.
"""

import json
import subprocess
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "verify-launched-work-liveness" / "SKILL.md"
CI_MONITORING = REPO / "modules" / "core" / "ci-monitoring.md"
HOOK = REPO / "hooks" / "block-broad-pkill.sh"
HOOKS_JSON = REPO / "settings" / "hooks.json"

# Same harness hang-guard rationale as tests/test_working_liveness.py (#444):
# the hook does bounded work, but this box runs many concurrent full-suite
# runs; a generous bound changes nothing about what any test asserts.
HOOK_TIMEOUT_S = 120


def _text(path):
    return path.read_text(encoding="utf-8")


def run_hook(cmd):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=HOOK_TIMEOUT_S,
    )


class TestSkillScopedKillDoctrine(TestCase):
    """The waiter-lifecycle owner names the rule, the banned shapes, and
    the scoped alternatives — the doctrine half of #701."""

    def test_scoped_kill_section_exists(self):
        text = _text(SKILL)
        self.assertIn("#### Killing your own waiter", text)
        self.assertIn("#701", text)

    def test_names_the_incident_literal_as_banned(self):
        # The exact incident shape is spelled out, not paraphrased away.
        text = _text(SKILL)
        self.assertIn('pkill -f "sleep 60"', text)
        self.assertIn("killall sleep", text)
        self.assertIn('pkill -f "gh run view"', text)

    def test_names_the_scoped_alternatives(self):
        text = _text(SKILL)
        # 1) the PID you already hold; 2) a unique in-body discriminator
        # (the run-id) with -u as defense-in-depth.
        self.assertIn('kill "$PID"', text)
        self.assertIn('-u "$USER"', text)
        self.assertRegex(text, r"run-?id")

    def test_names_the_friendly_fire_mechanism(self):
        # WHY: identical recipe-minted bodies fleet-wide on a shared box.
        text = _text(SKILL)
        self.assertRegex(text, r"(?i)friendly.fire")
        self.assertRegex(text, r"(?i)identical")

    def test_anti_pattern_bullet_present(self):
        # The skill's anti-pattern list carries the shape too, so a future
        # trimming pass can't drop the section without tripping twice.
        text = _text(SKILL)
        self.assertRegex(text, r"(?m)^- Killing .*generic pattern")


class TestCiMonitoringRiskSitePointer(TestCase):
    """The module that MINTS the identical waiter bodies points at the rule
    right where the recipe lives — one line, not a parallel doctrine."""

    def test_pointer_names_the_banned_shape_and_ticket(self):
        text = _text(CI_MONITORING)
        self.assertIn('pkill -f "sleep 60"', text)
        self.assertIn("#701", text)

    def test_pointer_routes_to_the_owning_skill(self):
        text = _text(CI_MONITORING)
        self.assertRegex(text, r"(?i)friendly.fire")
        # the full doctrine lives in the liveness skill, the module only
        # points — the rule-intake gate's "one-line pointer" shape.
        self.assertIn("verify-launched-work-liveness", text)


class TestHookWiring(TestCase):
    def test_hook_exists_and_executable(self):
        self.assertTrue(HOOK.exists(), f"missing hook: {HOOK}")
        self.assertTrue(HOOK.stat().st_mode & 0o111, "hook not executable")

    def test_wired_under_pretooluse_bash(self):
        cfg = json.loads(_text(HOOKS_JSON))
        bash_groups = [
            g for g in cfg["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"
        ]
        commands = [
            h["command"] for g in bash_groups for h in g["hooks"]
        ]
        self.assertTrue(
            any("block-broad-pkill.sh" in c for c in commands),
            "block-broad-pkill.sh not wired under PreToolUse(Bash)",
        )


class TestHookBlocksGenericShapes(TestCase):
    def assertBlocked(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 2, f"expected BLOCK for: {cmd}\nstderr={r.stderr}"
        )
        # exit-2 reasons go to stderr (stdout is invisible to the model)
        # and must hand back the scoped recipe, not just say "no".
        self.assertIn("pkill", r.stderr)
        self.assertRegex(r.stderr, r"(?i)pid|run-?id")

    def test_the_incident_literal(self):
        self.assertBlocked('pkill -f "sleep 60"')

    def test_other_sleep_durations(self):
        self.assertBlocked("pkill -f 'sleep 3300'")

    def test_bare_sleep_pattern_with_f(self):
        self.assertBlocked("pkill -f sleep")

    def test_bare_sleep_name_match(self):
        self.assertBlocked("pkill sleep")

    def test_killall_sleep(self):
        self.assertBlocked("killall sleep")

    def test_gh_run_view_without_run_id(self):
        self.assertBlocked('pkill -f "gh run view"')

    def test_gh_run_watch_without_run_id(self):
        self.assertBlocked('pkill -f "gh run watch"')

    def test_sudo_and_signal_prefixes_do_not_hide_it(self):
        self.assertBlocked('sudo pkill -9 -f "sleep 60"')

    def test_dash_u_alone_is_not_sufficient(self):
        # -u bounds the blast radius but shared-uid boxes exist — the unique
        # discriminator is the load-bearing part, so a generic pattern is
        # blocked even with -u.
        self.assertBlocked('pkill -u newlevel -f "sleep 60"')

    def test_anchored_regex_spelling_of_the_same_pattern(self):
        self.assertBlocked('pkill -f "^sleep 60$"')

    def test_inside_bash_c_wrapper(self):
        self.assertBlocked('bash -c \'pkill -f "sleep 60"\'')

    def test_in_a_later_compound_segment(self):
        self.assertBlocked("echo cleaning up && pkill -f sleep")


class TestHookAllowsScopedAndUnrelated(TestCase):
    def assertAllowed(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 0, f"expected ALLOW for: {cmd}\nstderr={r.stderr}"
        )

    def test_kill_by_pid(self):
        self.assertAllowed("kill 12345")

    def test_liveness_probe_kill_0(self):
        # the skill's own liveness probe must never trip its own guard hook
        self.assertAllowed('kill -0 "$PID"')

    def test_run_id_scoped_pkill(self):
        self.assertAllowed('pkill -u "$USER" -f "gh run view 17234567890"')

    def test_run_id_scoped_without_dash_u(self):
        self.assertAllowed('pkill -f "gh run view 17234567890"')

    def test_unique_token_pattern_containing_sleep(self):
        # contains "sleep 60" but is discriminated by a unique marker —
        # exactly the scoped shape the doctrine prescribes.
        self.assertAllowed('pkill -f "wait-run-a1b2c3.*sleep 60"')

    def test_pgrep_is_read_only_and_untouched(self):
        self.assertAllowed('pgrep -u "$USER" -f "sleep 60"')

    def test_quoted_prose_in_commit_message(self):
        self.assertAllowed(
            'git commit -m "docs: ban pkill -f sleep 60 friendly-fire (#701)"'
        )

    def test_heredoc_documentation_body_not_scanned(self):
        self.assertAllowed(
            "cat > body.md <<'EOF'\n"
            'Never run pkill -f "sleep 60" on a shared box.\n'
            "EOF\n"
            "gh issue comment 701 -F body.md"
        )

    def test_real_violation_after_heredoc_still_blocked(self):
        r = run_hook(
            "cat > note.md <<'EOF'\n"
            "documentation only\n"
            "EOF\n"
            'pkill -f "sleep 60"'
        )
        self.assertEqual(r.returncode, 2, f"stderr={r.stderr}")

    def test_bypass_marker(self):
        self.assertAllowed(
            'pkill -f "sleep 60" # airuleset:pkill-ok orphan cleanup, single-user box'
        )

    def test_unrelated_command(self):
        self.assertAllowed("ls -la && echo done")

    def test_empty_command_fails_open(self):
        r = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"tool_input": {}}),
            capture_output=True, text=True, timeout=HOOK_TIMEOUT_S,
        )
        self.assertEqual(r.returncode, 0)

    def test_garbage_stdin_fails_open(self):
        r = subprocess.run(
            ["bash", str(HOOK)], input="{not json",
            capture_output=True, text=True, timeout=HOOK_TIMEOUT_S,
        )
        self.assertEqual(r.returncode, 0)


class TestHookReviewHardening(TestCase):
    """Pass-2 fresh-context review findings, fixed in-branch (#701).

    (1) 🔴 the bypass marker was a whole-raw-command substring check, so a
    heredoc doc body or an unrelated segment QUOTING the marker disarmed a
    real chained kill — now the marker only lifts the block when it sits on
    the flagged segment/line itself; (2) 🟡 ``/usr/bin/pkill`` bypassed the
    exact-token binary match — now basename-compared; (3) 🟡 the composite
    ``kill $(pgrep -f "sleep 60")`` / ``pgrep -f "sleep 60" | xargs kill``
    shapes had the identical blast radius but were never classified — now
    the generic-pattern check covers a pgrep that FEEDS a kill (read-only
    pgrep alone stays untouched).
    """

    def assertBlocked(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 2, f"expected BLOCK for: {cmd}\nstderr={r.stderr}"
        )

    def assertAllowed(self, cmd):
        r = run_hook(cmd)
        self.assertEqual(
            r.returncode, 0, f"expected ALLOW for: {cmd}\nstderr={r.stderr}"
        )

    # -- 🔴 bypass marker scoping --------------------------------------
    def test_marker_in_heredoc_doc_body_does_not_disarm_a_real_kill(self):
        self.assertBlocked(
            "cat > note.md <<'EOF'\n"
            "Use the bypass: append # airuleset:pkill-ok <reason>\n"
            "EOF\n"
            'pkill -f "sleep 60"'
        )

    def test_marker_in_unrelated_segment_does_not_disarm_a_real_kill(self):
        self.assertBlocked(
            'git commit -m "note: airuleset:pkill-ok marker exists" '
            '&& pkill -f "sleep 60"'
        )

    def test_marker_on_the_kill_segment_itself_still_bypasses(self):
        self.assertAllowed(
            'echo cleanup && pkill -f "sleep 60" # airuleset:pkill-ok single-user box'
        )

    # -- 🟡 absolute-path binaries -------------------------------------
    def test_absolute_path_pkill_still_blocked(self):
        self.assertBlocked('/usr/bin/pkill -f "sleep 60"')

    def test_absolute_path_killall_still_blocked(self):
        self.assertBlocked("/usr/bin/killall sleep")

    # -- 🟡 composite pgrep-feeds-kill shapes --------------------------
    def test_kill_command_substitution_of_generic_pgrep_blocked(self):
        self.assertBlocked('kill $(pgrep -f "sleep 60")')

    def test_kill_dash9_command_substitution_blocked(self):
        self.assertBlocked('kill -9 $(pgrep -f "sleep 3300")')

    def test_generic_pgrep_piped_to_xargs_kill_blocked(self):
        self.assertBlocked('pgrep -f "sleep 60" | xargs kill')

    def test_kill_backtick_generic_pgrep_blocked(self):
        self.assertBlocked('kill `pgrep -f "sleep 60"`')

    def test_scoped_pgrep_kill_composite_allowed(self):
        self.assertAllowed('kill $(pgrep -u "$USER" -f "gh run view 17234567890")')

    def test_plain_read_only_pgrep_still_allowed(self):
        # pgrep that feeds NOTHING stays read-only and untouched — the
        # doctrine's own "find + READ the listing first" step.
        self.assertAllowed('pgrep -f "sleep 60"')
        self.assertAllowed('pgrep -u "$USER" -a -f "sleep 60"')

    def test_composite_with_marker_on_its_line_allowed(self):
        self.assertAllowed(
            'pgrep -f "sleep 60" | xargs kill # airuleset:pkill-ok single-user box'
        )


if __name__ == "__main__":
    main()
