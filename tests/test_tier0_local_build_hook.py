"""block-tier0-local-build.sh — close the evidenced #381 bypass shapes.

#381 (user, 2026-08-11, repeatedly, angry): ~25GB of Rust `target/` grew on
dev1 in Tier-0 (markerless) projects despite this hook existing since
`b25a893` and being registered as a PreToolUse(Bash) hook. STEP 0's
investigation (issue #381, comment) traced this to THREE evidenced gaps in
the hook itself, none of them a bug in its direct `cargo build`/`cargo test`
regex (that part already worked correctly):

  Shape A — the sanctioned `# airuleset:build-ok <reason>` one-off escape
    hatch is self-granted with zero accountability: nothing ever logs who
    used it, when, or why (camera-box worktree agent-ad32605ec3b7d0e03,
    spinbike main + worktree agent-a035c9b671927d76d, all traced from
    session transcripts). This file adds a durable audit log for every
    bypass/block decision — the marker itself stays untouched/sanctioned.

  Shape B — a heavy build hidden INSIDE an invoked wrapper/deploy script
    (`dantesync/install.sh:72`'s unconditional `cargo build --release`) is
    invisible to the hook: `is_heavy()` only ever pattern-matched the
    literal `tool_input.command` text, and `bash install.sh` / `sudo
    ./install.sh` never contains the substring "cargo build" itself.

  Shape C — discovered while designing the Shape-A fix, and evidenced
    directly against the OLD (unfixed) hook by this file's own
    `MarkerIsQuoteAwareTest`: the marker check was a naive
    `case "$CMD" in *"airuleset:build-ok"*)` with NO quote-stripping, so the
    marker text merely being MENTIONED inside an unrelated quoted string
    (e.g. a commit message) incorrectly exempted a REAL, unrelated heavy
    build chained on the same command line. `block-destructive-remote.sh`
    already documents fixing this exact class of bug for its own marker
    (`# airuleset:destructive-ok`) — same fix applied here.

Every test in this file shells out to the REAL, shipped
`hooks/block-tier0-local-build.sh` (never mocked) — `airuleset.py`'s own
`_tier0_via_hook()` depends on this hook being the single source of truth
for tier resolution (test_target_purge.py's own docstring), and a mock here
would let the two drift apart with nothing to catch it.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "block-tier0-local-build.sh"


def payload(command, cwd):
    return json.dumps({"tool_input": {"command": command}, "cwd": cwd})


class _Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.audit_log = self.root / "audit.log"

    def _mkproj(self, name="proj", marker=None):
        """A minimal Tier-0 (or Tier-1/2, if `marker` given) project: a real
        `.git` dir (so `git rev-parse --show-toplevel` resolves for the
        audit log's project name) + a CLAUDE.md."""
        p = self.root / name
        p.mkdir(parents=True, exist_ok=True)
        (p / ".git").mkdir(exist_ok=True)
        body = "# no marker\n"
        if marker:
            body = "## Local Build Policy\n\n<!-- airuleset:local-builds=%s -->\n" % marker
        (p / "CLAUDE.md").write_text(body)
        return p

    def run_hook(self, command, cwd, extra_env=None):
        env = dict(os.environ)
        env["AIRULESET_TIER0_AUDIT_LOG"] = str(self.audit_log)
        env.pop("AIRULESET_ALLOW_LOCAL_BUILD", None)
        if extra_env:
            env.update(extra_env)
        out = subprocess.run(
            ["bash", str(HOOK)], input=payload(command, str(cwd)),
            text=True, env=env, capture_output=True, timeout=30)
        return out

    def audit_lines(self):
        if not self.audit_log.exists():
            return []
        return [ln for ln in self.audit_log.read_text().splitlines() if ln.strip()]


class WiringTest(_Runner):
    def test_hook_exists_and_is_wired_as_a_pretooluse_bash_hook(self):
        self.assertTrue(HOOK.exists(), "hooks/block-tier0-local-build.sh missing")
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = [h.get("command", "")
                 for entry in cfg["hooks"]["PreToolUse"]
                 if entry.get("matcher") == "Bash"
                 for h in entry.get("hooks", [])]
        self.assertTrue(any("block-tier0-local-build.sh" in c for c in wired),
                         "hook is not registered under PreToolUse/Bash")


class DirectHeavyBuildUnchangedTest(_Runner):
    """Regression net: the pre-existing direct-command detection must be
    byte-for-byte unchanged in outcome after #381's fix."""

    def test_cargo_build_release_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_cargo_test_running_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_check_is_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo check --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_test_no_run_is_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_build_mentioned_inside_a_string_is_not_matched(self):
        proj = self._mkproj()
        out = self.run_hook('echo "reminder: run cargo build later"', proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier1_allowed_marker_exempts(self):
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier2_fast_iterate_marker_exempts(self):
        proj = self._mkproj(marker="fast-iterate")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_unmanaged_dir_not_enforced(self):
        # No CLAUDE.md anywhere above `root` (a bare tempdir) -> not enforced.
        bare = self.root / "unmanaged"
        bare.mkdir()
        out = self.run_hook("cargo build --release", bare)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_inline_marker_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release # airuleset:build-ok testing", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_env_override_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class WrapperScriptBypassClosedTest(_Runner):
    """Shape B: a heavy build hidden inside an invoked .sh script."""

    def _mkscript(self, proj, name, body):
        s = proj / name
        s.write_text(body)
        s.chmod(0o755)
        return s

    def test_bash_invoked_script_with_unconditional_build_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "#!/bin/bash\ncargo build --release\n")
        out = self.run_hook("bash install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("install.sh", out.stderr)

    def test_direct_exec_of_script_with_build_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "#!/bin/bash\ncargo build --release\n")
        out = self.run_hook("./install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sudo_bash_invoked_script_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "cargo build --release\n")
        out = self.run_hook("sudo bash install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sourced_script_with_test_run_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "setup.sh", "cargo test --lib foo\n")
        out = self.run_hook("source setup.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_script_with_only_cheap_checks_is_not_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "check.sh", "#!/bin/bash\ncargo check\ncargo clippy\n")
        out = self.run_hook("bash check.sh", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_referenced_but_missing_script_is_not_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("bash does-not-exist.sh", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_marker_on_outer_command_still_bypasses_a_heavy_script(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "cargo build --release\n")
        out = self.run_hook("bash install.sh # airuleset:build-ok one-off", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class MarkerIsQuoteAwareTest(_Runner):
    """Shape C: the marker text merely mentioned inside a quoted string must
    NOT bypass a real, unrelated heavy command on the same line."""

    def test_marker_mentioned_in_a_commit_message_does_not_bypass_a_real_build(self):
        proj = self._mkproj()
        cmd = ('git commit -m "docs: mention airuleset:build-ok in readme" '
               '&& cargo build --release')
        out = self.run_hook(cmd, proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_unquoted_trailing_marker_comment_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --lib foo # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class AuditLogTest(_Runner):
    """Shape A: every bypass/block decision on a genuinely HEAVY command is
    now a durable, greppable line — never silent."""

    def test_blocked_heavy_build_is_logged(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("project=camera-box", lines[0])
        self.assertIn("cargo build --release", lines[0])

    def test_inline_marker_bypass_is_logged(self):
        proj = self._mkproj(name="spinbike")
        out = self.run_hook("cargo build --release # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("inline-bypass", lines[0])
        self.assertIn("project=spinbike", lines[0])

    def test_env_override_bypass_is_logged(self):
        proj = self._mkproj(name="dantesync")
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 0)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("env-bypass", lines[0])

    def test_wrapper_script_block_is_logged_with_script_path(self):
        proj = self._mkproj(name="dantesync")
        s = proj / "install.sh"
        s.write_text("cargo build --release\n")
        out = self.run_hook("bash install.sh", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("install.sh", lines[0])

    def test_non_heavy_command_with_marker_is_not_logged(self):
        """No noise: a marker on a command that was never heavy in the
        first place is not the accountability gap this ticket closes."""
        proj = self._mkproj()
        out = self.run_hook("echo hi # airuleset:build-ok", proj)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(self.audit_lines(), [])

    def test_tier1_exempt_heavy_build_is_not_logged(self):
        """A Tier-1/2 project's own declared, visible marker is not the
        silent one-off gap either — don't add log noise for it."""
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(self.audit_lines(), [])


class AdversarialReviewFindingsTest(_Runner):
    """Round-2 findings from a fresh-context adversarial review of the
    #381 fix itself (fable, gate OPEN) — each reproduces a real regression
    or a real fail-open path the review found in the shipped hook."""

    def test_audit_log_write_failure_never_changes_the_verdict(self):
        """CRITICAL-1: an unwritable/unusable audit-log path must NEVER
        turn a real BLOCK into a silent hook-error pass-through. Under
        `set -euo pipefail`, an unguarded `echo … >> "$AUDIT_LOG"` failing
        (here: AUDIT_LOG points at an existing DIRECTORY, so the redirect
        itself fails at the OS level) aborts the shell with rc=1 BEFORE
        the caller's own `exit 2` ever runs — and this repo's own #118/
        #196 lesson is that Claude Code treats a hook exiting anything
        other than 0/2 as a HOOK ERROR and runs the blocked command
        anyway. That is the exact silent-target/-growth hole #381 exists
        to close, reachable here via one self-triggerable `mkdir -p
        <the-log-path>` making it a directory."""
        proj = self._mkproj()
        bad_log_dir = self.root / "audit_is_a_dir"
        bad_log_dir.mkdir()
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_TIER0_AUDIT_LOG": str(bad_log_dir)})
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_referencing_a_heavy_script_without_executing_it_is_not_blocked(self):
        """MAJOR-2: the direct-exec regex's old boundary (bare `\\s`)
        treated ANY whitespace before a `./x.sh`/`/abs/x.sh` path as a
        command-start position — so merely reading/inspecting a script
        whose CONTENT happens to be heavy (never actually invoking it)
        was misclassified as Shape B and blocked. A real command position
        needs an actual separator (`;`/`&`/`|`/`(`/newline) or start-of-
        string before the path, not bare whitespace."""
        proj = self._mkproj()
        s = proj / "install.sh"
        s.write_text("cargo build --release\n")
        for cmd in ("cat ./install.sh", "grep cargo ./install.sh",
                    "git add ./install.sh", "wc -l ./install.sh"):
            with self.subTest(cmd=cmd):
                out = self.run_hook(cmd, proj)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_direct_exec_after_separator_or_sudo_still_blocks(self):
        """Positive control for the MAJOR-2 fix: tightening the boundary
        must not lose any of the genuine invocation shapes the review
        itself validated as real command positions."""
        proj = self._mkproj()
        s = proj / "install.sh"
        s.write_text("cargo build --release\n")
        for cmd in ("sudo ./install.sh", "make; ./install.sh",
                    "cd .. && ./install.sh", "./install.sh"):
            with self.subTest(cmd=cmd):
                out = self.run_hook(cmd, proj)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_multiline_command_produces_exactly_one_audit_log_line(self):
        """MAJOR-3: `cmd=${CMD}` wrote the RAW command unsanitized — a
        newline inside it (a heredoc, a deliberately crafted payload)
        split the audit record into extra physical lines, one of them
        fully attacker-controlled (a forged project/disposition), which
        defeats the whole point of the accountability log and breaks its
        one-line-per-event grep contract even for benign multi-line
        commands. The command must always collapse to exactly one line.

        #381-review discovery (not part of the original finding): the
        adversarial payload embeds the literal text "project=totally-legit"
        inside what becomes the `cmd=` field's OWN value — a raw
        `.count("project=")` over the whole line would therefore never be
        able to reach 1, for ANY command containing that substring
        (including, ordinarily, a commit fixing "project= handling", or
        this very payload). That is not a forgeable SEPARATE entry (already
        ruled out by `len(lines) == 1`) — it is text sitting inside the
        one genuine field that legitimately holds arbitrary content. The
        real, achievable security property is that the REAL project field
        (positionally right after the timestamp, which is what any correct
        parser reads first) names the real project, not a forged one."""
        proj = self._mkproj(name="realproj")
        cmd = ("cargo build --release # x\n"
               "2026-01-01T00:00:00+00:00  project=totally-legit  "
               "inline-bypass  cmd=nothing to see")
        out = self.run_hook(cmd, proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("project=realproj", lines[0])
        self.assertRegex(lines[0], r"^\S+\s+project=realproj\s")


class NewlyDetectedHeavyBuildShapesTest(_Runner):
    """#470: heavy compile/run shapes that ESCAPED is_heavy() entirely — no
    block, no audit line, ran silently and overloaded dev1. camera-box
    provably uses all five (STEP 0 evidence + the #470 inventory comment):
    `cargo run --release`, `cargo bench`, `cargo nextest run` (the real CI
    test runner), `cargo mutants`, `cmake --build <dir>` (the vendored-libobs
    local dev1 build). They are now detected via the SAME command-position
    anchor + `--no-run` cheap-check exemption the direct build/test regexes
    already use.

    The two SANCTIONED camera-box Tier-0 workarounds must stay ALLOWED: a
    `--no-run` compile-only, a direct `target/.../deps/<bin>` execution (running
    an already-compiled binary is not a compile), and a `gcc`/`cc` harness
    compile (CI-only vendor tree). So must every cheap check (`cargo check`/
    `clippy`/`nextest list`/`bench --no-run`), and both sanctioned escapes
    (the `# airuleset:build-ok` marker, a Tier-1/2 project marker)."""

    def test_cargo_run_release_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run --release", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_bare_cargo_run_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_bench_running_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo bench", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_bench_no_run_is_allowed(self):
        # compile-only, exactly like the existing `cargo test --no-run` carve-out
        proj = self._mkproj()
        out = self.run_hook("cargo bench --no-run", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_nextest_run_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo nextest run --all-features", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_nextest_list_is_allowed(self):
        # a compile+list, not a full run — deliberately left uncaught (cheap-ish)
        proj = self._mkproj()
        out = self.run_hook("cargo nextest list", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_mutants_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo mutants --in-place", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cmake_build_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cmake --build /tmp/obs-vendor-build", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cmake_configure_step_is_allowed(self):
        # the configure step (no --build) is light — only the build is heavy
        proj = self._mkproj()
        out = self.run_hook("cmake -S . -B build", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_new_shape_in_a_compound_command_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cd /home/x/camera-box && cargo bench 2>&1 | tail", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_direct_test_binary_run_is_allowed(self):
        # SANCTIONED Tier-0 workaround: run an already-compiled binary (the
        # compile happened via the allowed `cargo test --no-run`)
        proj = self._mkproj()
        out = self.run_hook(
            "target/debug/deps/harness_rig_lease-abc123 --nocapture", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_gcc_harness_compile_is_allowed(self):
        # SANCTIONED Tier-0 workaround: gcc/cc harness compile for the CI-only
        # vendored C tree — must never be blocked
        proj = self._mkproj()
        out = self.run_hook("gcc -O2 harness.c -o /tmp/harness", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_run_mentioned_inside_a_string_is_not_matched(self):
        proj = self._mkproj()
        out = self.run_hook('echo "reminder: cargo run --release later"', proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_inline_marker_still_bypasses_a_new_shape(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run --release # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier1_marker_still_exempts_a_new_shape(self):
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo bench", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_blocked_new_shape_is_logged_with_project(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo nextest run", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("project=camera-box", lines[0])
        self.assertIn("cargo nextest run", lines[0])


class AnchorEscapeAndNoRunSegmentScopeTest(_Runner):
    """#471: the two PRE-EXISTING residuals #470's own review filed as this ticket,
    fixed uniformly across EVERY cargo shape at once.

    F1 -- trailing-metachar anchor escape. Every anchored heavy shape closes with
    `([[:space:]]|$)`, so a shell metacharacter glued directly to the subcommand
    token (`cargo run;`, `(cargo run)`, `cargo run|tee`, `cargo run&`, the loop
    body `for i in 1 2; do cargo run; done`) satisfied neither and ran unblocked.
    The widened anchor `([[:space:]]|$|[;&|)(<>])` catches every metachar while
    still rejecting a longer-word decoy (`cargo runner`, `cargo run-script`).

    F2 -- whole-command `--no-run` exemption looseness. The `cargo test`/`cargo
    bench` carve-out grepped the ENTIRE command for `--no-run`, so an unrelated
    `--no-run` in a DIFFERENT top-level segment exempted a genuine RUN
    (`cargo test --no-run && cargo bench`, `cargo bench --no-run && cargo bench`),
    and a substring decoy (`cargo bench -- --no-run-decoy`) exempted a real run.
    The exemption is now segment-scoped (via the repo's quote-aware
    `split_top_level` splitter) and `--no-run` is matched as a WHOLE flag.

    Every SANCTIONED Tier-0 workaround stays allowed and is locked below: a
    `--no-run` compile, a direct `target/.../deps/<bin>` execution, a `gcc`/`cc`
    harness compile, the `# airuleset:build-ok` marker, a Tier-1/2 project marker,
    and the longer-word decoys `cargo runner`/`cargo run-script`.
    """

    # ---- F1: trailing-metachar anchor escape (was rc=0, must block) ----

    def test_cargo_build_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_cargo_run_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_run_trailing_ampersand_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run&", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_run_in_parens_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("(cargo run)", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_run_piped_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run|tee log", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_test_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_bench_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo bench;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_mutants_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo mutants;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_nextest_run_trailing_semicolon_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo nextest run;", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_run_glued_inside_loop_body_is_blocked(self):
        # `do cargo run; done` -- the `;` glued to `run` escaped the old anchor
        proj = self._mkproj()
        out = self.run_hook("for i in 1 2; do cargo run; done", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- F2: --no-run exemption looseness (was rc=0, must block) ----

    def test_unrelated_no_run_does_not_exempt_a_real_bench(self):
        # the test's own --no-run must not exempt the genuine bench RUN
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run && cargo bench", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_no_run_compile_then_real_bench_run_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo bench --no-run && cargo bench", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_no_run_substring_decoy_does_not_exempt_bench(self):
        # `--no-run-decoy` is a bench harness arg, NOT the cargo --no-run flag
        proj = self._mkproj()
        out = self.run_hook("cargo bench -- --no-run-decoy", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_no_run_in_unrelated_echo_does_not_exempt_test(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test && echo --no-run", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_no_run_substring_decoy_does_not_exempt_test(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test -- --no-run-decoy", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- SANCTIONED workarounds + decoys: MUST stay allowed (before & after) ----

    def test_cargo_test_no_run_compile_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_bench_no_run_compile_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo bench --no-run", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_two_no_run_compiles_chained_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run && cargo bench --no-run", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_runner_decoy_still_allowed(self):
        # widened anchor must NOT match `run` followed by a letter
        proj = self._mkproj()
        out = self.run_hook("cargo runner", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_run_script_decoy_still_allowed(self):
        # widened anchor must NOT match `run` followed by `-`
        proj = self._mkproj()
        out = self.run_hook("cargo run-script deploy", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_direct_test_binary_run_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook(
            "target/debug/deps/harness_rig_lease-abc123 --nocapture", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_gcc_harness_compile_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("gcc -O2 harness.c -o /tmp/harness", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_run_flag_only_exempts_test_and_bench_not_build(self):
        # `cargo build` has no --no-run carve-out, so it still blocks even when
        # an unrelated `cargo test --no-run` precedes it
        proj = self._mkproj()
        out = self.run_hook(
            "cargo test --no-run && cargo build --release", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_inline_marker_still_bypasses_an_escape_shape(self):
        proj = self._mkproj()
        out = self.run_hook("cargo run; # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier1_marker_still_exempts_an_escape_shape(self):
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo run;", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_blocked_escape_shape_is_logged_with_project(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo bench;", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("project=camera-box", lines[0])
        self.assertIn("cargo bench;", lines[0])

    # ---- #471-review: F1 must widen the `--no-run` EXEMPT-flag boundary too,
    # symmetrically with the heavy-token anchors -- else a sanctioned
    # compile-only workaround with a trailing metachar wrongly BLOCKS (rc=2). ----

    def test_no_run_compile_with_trailing_semicolon_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run;", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_bench_no_run_compile_with_trailing_semicolon_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo bench --no-run;", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_run_compile_in_parens_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("(cargo test --no-run)", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_run_compile_piped_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run|tee log", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_run_compile_backgrounded_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run&", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_run_compile_redirected_still_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run>log", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_glued_semicolon_chain_no_run_then_real_bench_still_blocks(self):
        # positive control: widening the --no-run boundary must NOT accidentally
        # exempt the real bench run in a `;`-glued chain -- segment 2's `cargo
        # bench` has no --no-run of its own.
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run;cargo bench", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class CameraBoxBypassDisabledTest(_Runner):
    """#477: the ad-hoc build-ok bypasses (the `# airuleset:build-ok` inline
    marker AND the `AIRULESET_ALLOW_LOCAL_BUILD` env var) are DISABLED for the
    camera-box repo -- heavy camera-box tests/builds go through CI only (user
    decision 2026-08-14; workers ran local `cargo test` 247x/2 days through the
    marker, overloading the shared dev1 box). Detection is by the repo's
    AUTHORITATIVE identity (`git remote get-url origin` basename == camera-box),
    falling back to a `camera-box` path component ONLY when no remote resolves
    (a detached/no-origin checkout, or a worktree). Every OTHER repo, and
    camera-box's own DELIBERATE project-level Tier-1/2 opt-in, are unchanged --
    only the two ad-hoc per-command bypasses are removed, and only for
    camera-box.  is_heavy() (#470/#471) is untouched: this is the bypass layer."""

    def _mkgit_remote(self, name, remote_url):
        """A REAL git repo (so `git remote get-url origin` resolves) with a
        Tier-0 CLAUDE.md and the given origin URL -- exercises the remote-URL
        detection path independent of the directory name."""
        p = self.root / name
        p.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=p, check=True,
                       capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=p,
                       check=True, capture_output=True)
        (p / "CLAUDE.md").write_text("# no marker\n")
        return p

    # ---- the core decision: camera-box + build-ok + heavy = BLOCKED ----

    def test_camera_box_inline_marker_no_longer_bypasses_cargo_test(self):
        # the exact 247x-abused shape
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo test # airuleset:build-ok testing", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_inline_marker_no_longer_bypasses_cargo_build(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo build --release # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_env_override_no_longer_bypasses(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo test", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_block_message_points_to_ci(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo test # airuleset:build-ok testing", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("camera-box", out.stderr)
        self.assertIn("CI", out.stderr)

    def test_camera_box_detected_by_ssh_remote_url_not_just_dir_name(self):
        # dir named 'renamed-checkout', origin remote = camera-box -> still blocked
        proj = self._mkgit_remote("renamed-checkout",
                                  "git@github.com:zbynekdrlik/camera-box.git")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_detected_by_https_remote_url(self):
        proj = self._mkgit_remote("cb2",
                                  "https://github.com/zbynekdrlik/camera-box.git")
        out = self.run_hook("cargo build --release # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_new_heavy_shape_with_marker_is_blocked(self):
        # a #470/#471 shape (cargo nextest run) + build-ok in camera-box
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo nextest run # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- NEGATIVE controls: every OTHER repo's bypass is untouched ----

    def test_non_camera_box_inline_marker_still_bypasses(self):
        proj = self._mkproj(name="spinbike")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_non_camera_box_env_override_still_bypasses(self):
        proj = self._mkproj(name="dantesync")
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_non_camera_box_git_remote_marker_still_bypasses(self):
        proj = self._mkgit_remote("some-rust-proj",
                                  "git@github.com:zbynekdrlik/restreamer.git")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_remote_authoritatively_non_camera_box_overrides_dir_name(self):
        # dir literally named 'camera-box' BUT origin remote = restreamer ->
        # remote is authoritative -> NOT camera-box -> the bypass still works.
        proj = self._mkgit_remote("camera-box",
                                  "git@github.com:zbynekdrlik/restreamer.git")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- camera-box: cheap checks unaffected; the Tier opt-in still works ----

    def test_camera_box_cheap_check_still_allowed(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo check --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_camera_box_no_run_compile_still_allowed(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo test --no-run --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_camera_box_non_heavy_command_with_marker_untouched(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("echo hi # airuleset:build-ok", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_camera_box_tier1_project_marker_still_exempts(self):
        # the deliberate project-level opt-in is unaffected -- only the ad-hoc
        # per-command build-ok bypass is disabled for camera-box.
        proj = self._mkproj(name="camera-box", marker="allowed")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_camera_box_blocked_command_is_audit_logged(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("project=camera-box", lines[0])

    # ---- review-fix hardening: robust remote-basename parsing ----

    def test_camera_box_detected_case_insensitively(self):
        # git treats repo names case-insensitively -- a `Camera-Box` origin
        # spelling still resolves to the same repo and must still be caught.
        proj = self._mkgit_remote("cb-case",
                                  "git@github.com:zbynekdrlik/Camera-Box.git")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_camera_box_detected_with_trailing_slash_git_url(self):
        # a `.git/` trailing-slash origin URL must still resolve to camera-box.
        proj = self._mkgit_remote("cb-slash",
                                  "https://github.com/zbynekdrlik/camera-box.git/")
        out = self.run_hook("cargo test # airuleset:build-ok x", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
