"""Behaviour test for hooks/block-gk-request-without-selfservice.sh (#516).

Side A of the two-sided self-service gate: at the MOMENT a sub-dev stream files
a gatekeeper ACTION request (`airuleset.py gk-request`, or a raw `gh` command
adding `needs-gatekeeper` / carrying a `GATEKEEPER-ACTION:` body), the request
must carry a falsifiable `Self-service-checked:` line — exactly the LOGGED-claim
shape block-ungated-issue-filing.sh (#137/#329) proved for filing.

The claims under test (bidirectional, mirroring test_scope_gate.py):
  - A gk action request from a REDUCED-authority stream with NO Self-service-
    checked line BLOCKS (exit 2).
  - The SAME request WITH the line PASSES (exit 0) — a genuine intervention
    request (a stuck queue restart, a RUNTIME_DEPS install) passes by truthfully
    filling the line. THIS is the core false-positive-avoidance test.
  - An ordinary CODE-REVIEW hand-off (ready-for-review / stream:<x> label, or a
    READY-FOR-REVIEW: body) is NEVER gated — untouched even with no line.
  - A FULL-authority box (maintainer / gatekeeper) is never gated.

The hook resolves authority via airuleset.resolve_authority(cwd). Since #839
`_current_user()` is uid-based (env `LOGNAME`/`USER` no longer spoof identity),
a test engages the gate the UN-spoofable way — a `<!-- airuleset:authority=
<profile> -->` marker written into the hook's cwd (honored FIRST by
`resolve_authority`), the profile derived from `user` via AUTHORITY_BY_USER
(david2 = fork-no-merge, montalu1 = branch-merge). Same seam as
test_scope_gate.py's #390 stream-routing tests.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "block-gk-request-without-selfservice.sh"

sys.path.insert(0, str(REPO_ROOT))
import airuleset  # noqa: E402


def run(cmd, user="david2", cwd=None, home=None):
    """Invoke the hook with the PreToolUse(Bash) stdin payload.

    airuleset#839: `_current_user()` is now uid-based (env `LOGNAME`/`USER` no
    longer spoof identity), so authority is simulated the UN-spoofable,
    #828-sanctioned way — a `<!-- airuleset:authority=<profile> -->` marker
    written into the hook's cwd, which `resolve_authority(cwd)` honors FIRST.
    `user` maps to that profile via AUTHORITY_BY_USER (david2 -> fork-no-merge =
    a reduced stream, the default, so the gate engages); a full account / None
    writes no marker (the real full box, never gated). exit 0 = allowed,
    exit 2 = blocked."""
    payload = json.dumps({"tool_input": {"command": cmd},
                          "session_id": "test-selfservice-gate"})
    env = dict(os.environ)
    env["HOME"] = home or tempfile.mkdtemp(prefix="airuleset-selfservice-test-")
    profile = airuleset.AUTHORITY_BY_USER.get(user) if user else None
    run_cwd = cwd
    if profile is not None:
        if run_cwd is None:
            run_cwd = tempfile.mkdtemp(prefix="airuleset-selfservice-cwd-")
        Path(run_cwd, "CLAUDE.md").write_text(
            "<!-- airuleset:authority=%s -->\n" % profile, encoding="utf-8")
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        env=env, cwd=run_cwd or str(REPO_ROOT),
    )


class GkRequestChannel(TestCase):
    def test_gk_request_no_line_blocks(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "Skontroluj počty odoslaných mailov za 14 dní."')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Self-service-checked", r.stderr)

    def test_gk_request_with_line_passes(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "Zaseknutá odchádzajúca fronta. '
                'Self-service-checked: čítal som z čerstvej PROD kópie (psql), '
                'fronta má 40 zaseknutých; potrebujem živý reštart odosielača."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gk_request_title_body_file_heredoc_with_line_passes(self):
        cmd = ("cat > body.md <<'EOF'\n"
               "Potrebujem doinštalovať balík na PROD box.\n"
               "Self-service-checked: overil som na čerstvej PROD kópii, balík "
               "chýba; potrebujem živú inštaláciu jq do RUNTIME_DEPS.\n"
               "EOF\n"
               "python3 ~/devel/airuleset/airuleset.py gk-request "
               '--title "install jq" --body-file body.md')
        r = run(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gk_request_title_body_file_heredoc_no_line_blocks(self):
        cmd = ("cat > body.md <<'EOF'\n"
               "Prečítaj mi prosím, či je user X v skupine Y na PRODe.\n"
               "EOF\n"
               "python3 ~/devel/airuleset/airuleset.py gk-request "
               '--title "membership check" --body-file body.md')
        r = run(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_gk_request_no_comment_blocks(self):
        # --issue with no --comment: cmd_gk_request supplies a default text that
        # never carries the line -> the hook sees no resolvable body -> BLOCK.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5')
        self.assertEqual(r.returncode, 2, r.stderr)


class RawGhEscalation(TestCase):
    def test_bare_add_needs_gatekeeper_label_blocks(self):
        r = run("gh issue edit 5 --add-label needs-gatekeeper")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_gatekeeper_action_comment_no_line_blocks(self):
        r = run('gh issue comment 5 --body "GATEKEEPER-ACTION: '
                'over počty odoslaných mailov za 14 dní"')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_gatekeeper_action_comment_with_line_passes(self):
        r = run('gh issue comment 5 --body "GATEKEEPER-ACTION: zaseknutá fronta. '
                'Self-service-checked: prečítal som stav z čerstvej PROD kópie; '
                'potrebujem živý reštart služby odosielača na PRODe."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_create_with_needs_gatekeeper_label_no_line_blocks(self):
        cmd = ("cat > body.md <<'EOF'\n"
               "Prečítaj mi počet riadkov v tabuľke X na PRODe.\n"
               "EOF\n"
               'gh issue create -t "prod read" -F body.md -l needs-gatekeeper')
        r = run(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)


class ReviewHandoffNeverGated(TestCase):
    def test_ready_for_review_comment_untouched(self):
        # READY-FOR-REVIEW: hand-off carries no Self-service line and must NOT
        # be gated — it is not a gk action request at all.
        r = run('gh issue comment 5 --body "READY-FOR-REVIEW: branch dev '
                'merged into develop; lokálne testy zelené."')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ready_for_review_label_untouched(self):
        r = run("gh issue edit 5 --add-label ready-for-review")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_carveout_needs_gatekeeper_plus_stream_label_untouched(self):
        # rule 8: needs-gatekeeper + stream:<user> = REVIEW queue hand-off, NOT
        # an action request. Must be skipped even with no Self-service line.
        r = run("gh issue edit 5 --add-label needs-gatekeeper "
                "--add-label stream:david2")
        self.assertEqual(r.returncode, 0, r.stderr)


class AuthorityScope(TestCase):
    def test_full_authority_box_not_gated(self):
        # a full-authority account (airuleset#827: in FULL_AUTHORITY_USERS) is
        # never gated, even for a gk-request with no line. `gatekeeper` is not in
        # AUTHORITY_BY_USER, so run() writes NO authority marker -> the hook's cwd
        # resolves to the real full box (newlevel) -> not gated. The distinct-
        # from-full check is now that a REDUCED marker (the default david2 path)
        # DOES block (test_gk_request_no_line_blocks), not a LOGNAME override.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "prod read"', user="gatekeeper")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_branch_merge_stream_also_gated(self):
        # montalu1 (renamed from montalu, #537) is a branch-merge reduced
        # stream — its gk-request without a self-service line is still gated.
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "prod read"', user="montalu1")
        self.assertEqual(r.returncode, 2, r.stderr)


class BypassAndInert(TestCase):
    def test_unrelated_command_passes(self):
        r = run('gh issue comment 5 --body "just a normal comment"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_marker(self):
        r = run('python3 ~/devel/airuleset/airuleset.py gk-request --issue 5 '
                '--comment "no line here"  # airuleset:selfservice-ok legacy tweak')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plain_stream_label_not_action_request(self):
        # a bare stream tag with no needs-gatekeeper/gk-request/GATEKEEPER-ACTION
        # is not an escalation at all -> pre-filter exits 0.
        r = run("gh issue edit 5 --add-label stream:david2")
        self.assertEqual(r.returncode, 0, r.stderr)


class AdversarialReviewFixes(TestCase):
    """Regressions for the #516 side-A adversarial review findings F1/F2."""

    def test_cd_relative_body_file_with_line_passes(self):
        # F1 (#483 class): a compliant request with a cd-relative -F disk body
        # that DOES carry the line must PASS — resolved against the effective
        # cwd after `cd`, not the hook's own cwd.
        d = tempfile.mkdtemp(prefix="airuleset-selfservice-cd-")
        with open(os.path.join(d, "body.md"), "w") as f:
            f.write("Zaseknutá fronta.\nSelf-service-checked: čítal som z čerstvej "
                    "PROD kópie; potrebujem živý reštart odosielača.\n")
        r = run("cd %s && python3 ~/devel/airuleset/airuleset.py gk-request "
                '--title "restart" --body-file body.md' % d)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cd_relative_body_file_no_line_blocks(self):
        d = tempfile.mkdtemp(prefix="airuleset-selfservice-cd-")
        with open(os.path.join(d, "body.md"), "w") as f:
            f.write("Prečítaj mi počet riadkov v tabuľke X na PRODe.\n")
        r = run("cd %s && python3 ~/devel/airuleset/airuleset.py gk-request "
                '--title "read" --body-file body.md' % d)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_grep_exploration_not_blocked(self):
        # F2: `grep gk-request airuleset.py` names the file but is NOT an
        # escalation — gk-request must be the SUBCOMMAND, not merely co-present.
        r = run("grep gk-request airuleset.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_grep_absolute_path_exploration_not_blocked(self):
        r = run("grep -n gk-request /home/newlevel/devel/airuleset/airuleset.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gk_request_help_not_blocked(self):
        # a --help query files nothing, so it is not gated.
        r = run("python3 ~/devel/airuleset/airuleset.py gk-request --help")
        self.assertEqual(r.returncode, 0, r.stderr)


class HookRegistered(TestCase):
    def test_hook_file_exists_executable_and_registered(self):
        self.assertTrue(HOOK.is_file(), "hook script missing: %s" % HOOK)
        hooks_json = json.loads(
            (REPO_ROOT / "settings" / "hooks.json").read_text())
        pretooluse = hooks_json["hooks"]["PreToolUse"]
        commands = [h["command"]
                    for group in pretooluse if group.get("matcher") == "Bash"
                    for h in group["hooks"]]
        self.assertTrue(
            any("block-gk-request-without-selfservice.sh" in c for c in commands),
            "hook not registered in settings/hooks.json PreToolUse/Bash: %r" % commands)


if __name__ == "__main__":
    main()
