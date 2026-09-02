"""Behaviour + CORPUS-REPLAY test for hooks/block-ungated-issue-filing.sh (#137).

Per the repo's own repeated lesson (#80, #91, #96, #88, #108, #112...): a
classifier hook is proven with a REAL corpus run through the REAL shipped
script, not synthetic fixtures alone. This file does both.

#329 extended the hook with three more gates (dedup, daily cap, chain-width
cap) on top of #311's chain-depth cap and >300-loc self-contradiction check
-- their own tests live in TestDedupGate / TestDailyCap / TestChainWidthCap
below. `body_cmd()`'s new `dedup=` default keeps every PRE-EXISTING PASS-path
test in this file satisfying the new dedup-gate requirement automatically.

The corpus is the exact real material #137's own investigation named: three
CONFIRMED leak cases (camera-box #846, #843; odoo-erp #2388 — each explicitly
cited in the ticket as filed with the violation CONFESSED in its own body,
with no genuine follow-up-gate criterion anywhere in the text) and fifteen
real camera-box issues whose bodies genuinely justify one of the follow-up
gate's own exemptions (cross-cutting / >300-loc / needs-user-decision — read
by hand, not guessed) — fetched once via `gh issue view --json body` and
embedded verbatim (truncated to the load-bearing excerpt) so the replay is
reproducible offline. Excerpts are UNMODIFIED substrings of the real bodies.

Bidirectional claim under test:
  - Feeding any of the 18 REAL bodies through the hook AS-IS (no Scope-gate
    line — none of them has one; the line is new) BLOCKS all 18. That is the
    hook's baseline coverage: nothing passes silently.
  - Adding ONE truthful `Scope-gate: <criterion>` line to each of the 15
    LEGITIMATE bodies (the criterion genuinely matches what the body already
    says) makes all 15 PASS.
  - The 3 LEAK bodies have no genuine criterion anywhere in their text (read
    by hand) and stay BLOCKED even when a plausible-sounding one is
    fabricated for them, since the hook cannot verify truth — it can only
    prove the mechanism did not silently wave a confessed violation through.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-ungated-issue-filing.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT))
import airuleset  # noqa: E402


_DEFAULT_EMPTY_GH_DIR = None


def _default_gh_stub():
    """A `gh` stub answering `issue list` with an EMPTY list (making the
    #329 near-duplicate network check inert) and failing everything else
    (`issue view`, etc.) -- the DEFAULT `gh` on PATH for every test that
    doesn't explicitly pass its own `gh_bin`, so the suite never depends
    on live GitHub state (adversarial-review finding, #329: without this,
    every test using body_cmd()'s default made a REAL, if fast-failing,
    `gh issue list` network call). Built once and reused -- cheap, and
    matches the pre-existing per-test-dir convention `_fake_gh`/
    `_fake_gh_list` already use."""
    global _DEFAULT_EMPTY_GH_DIR
    if _DEFAULT_EMPTY_GH_DIR is None:
        tmp = tempfile.mkdtemp(prefix="airuleset-scopegate-defaultgh-")
        _DEFAULT_EMPTY_GH_DIR = _fake_gh_list(tmp, [])
    return _DEFAULT_EMPTY_GH_DIR


def run(cmd, home=None, cwd=None, gh_bin=None, user=None, hook_path=None,
        spoof_login=None, session_id=None, agent_id=None):
    """`user` (#390): the simulated filer's sub-dev stream identity. Two halves,
    since airuleset#839:
    - The AUTHORITY PROFILE gate (`resolve_authority(cwd) != full`) is now
      uid-based (`_current_user()`), so env `LOGNAME`/`USER` no longer control
      it — run() writes a `<!-- airuleset:authority=<profile> -->` marker into
      the hook's cwd (honored FIRST by `resolve_authority`), the profile derived
      from `user` via AUTHORITY_BY_USER.
    - The OWN-STREAM half (`stream:<x>` comparison) now derives from the hook's
      un-spoofable `airuleset._current_user()` (uid-based, airuleset#840), NOT
      the env-spoofable `getpass.getuser()`. Because a test cannot change its own
      uid, run() supplies the true own-stream identity through the un-spoofable
      TEST-IDENTITY SEAM env `AIRULESET_SCOPE_GATE_TEST_STREAM_USER` = `user`,
      which the hook honors ONLY when the REAL invoking account is NOT itself a
      reduced stream (a full/maintainer/test-runner box — never a real stream,
      whose uid IS in AUTHORITY_BY_USER). The test runner (newlevel/runner/root)
      is such a box, so the seam is active in the suite and unreachable in prod.
    A reduced `user` therefore both writes the reduced-profile marker AND sets
    the own-stream seam; a full account / None writes no marker (the real full box).

    `spoof_login` (#840): a DIFFERENT env `LOGNAME`/`USER` value — the env-spoof a
    real stream would attempt. Under the pre-fix `getpass.getuser()` path the
    own-stream would FOLLOW `spoof_login` (the vulnerability); under the uid-based
    fix it follows the SEAM (`user`, the true identity), ignoring the env spoof.
    Default `None` → LOGNAME/USER == `user`, so every pre-existing test keeps both
    the seam and the (now vestigial) env in agreement and passes on either hook.

    `hook_path` (#390 adversarial-review MINOR-2): run a DIFFERENT copy of
    the hook script (e.g. one deliberately isolated under a directory with
    no sibling `airuleset.py`, to prove the import-failure degrade path
    genuinely skips the gate rather than blocking). `None` (the default)
    runs the real `HOOK` in this checkout, exactly like every pre-existing
    test."""
    # #842: session_id drives the shared presence marker
    # (/tmp/claude-user-active-<sid>); agent_id makes the payload look like a
    # SUBAGENT (a worktree autopilot-worker) for the req-1 hard-block.
    _payload = {"tool_input": {"command": cmd},
                "session_id": session_id or "test-scope-gate"}
    if agent_id is not None:
        _payload["agent_id"] = agent_id
    payload = json.dumps(_payload)
    env = dict(os.environ)
    env["HOME"] = home or tempfile.mkdtemp(prefix="airuleset-scopegate-test-")
    env["PATH"] = (gh_bin or _default_gh_stub()) + os.pathsep + env.get("PATH", "")
    if user is not None:
        env["AIRULESET_SCOPE_GATE_TEST_STREAM_USER"] = user
        login = spoof_login if spoof_login is not None else user
        env["LOGNAME"] = login
        env["USER"] = login
    profile = airuleset.AUTHORITY_BY_USER.get(user) if user else None
    run_cwd = cwd
    if profile is not None:
        if run_cwd is None:
            run_cwd = tempfile.mkdtemp(prefix="airuleset-scopegate-cwd-")
        Path(run_cwd, "CLAUDE.md").write_text(
            "<!-- airuleset:authority=%s -->\n" % profile, encoding="utf-8")
    return subprocess.run(
        ["bash", str(hook_path or HOOK)], input=payload, capture_output=True, text=True,
        env=env, cwd=run_cwd or str(REPO_ROOT),
    )


def _fake_gh(tmpdir, responses, require_repo=None):
    """A minimal `gh` stub prepended onto PATH: `gh issue view <N> --json
    title,body` prints the JSON in `responses[str(N)]` when present, else
    exits 1 (simulating any real lookup failure -- offline, no auth, the
    issue genuinely doesn't exist). Never touches the real network.

    `require_repo` (optional): when given, the stub ONLY answers when the
    invocation's own `-R <require_repo>` flag is present and matches --
    any other repo (or no `-R` at all) exits 1, exactly like a real `gh`
    resolving the WRONG repo would find no such issue there. This is what
    gives a cross-repo test real teeth: a caller that silently drops `-R`
    gets the SAME failure a real mismatched lookup would, not a
    repo-blind stand-in that answers regardless."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "responses = %r\n"
        "require_repo = %r\n"
        "if require_repo is not None:\n"
        "    repo = None\n"
        "    for i, a in enumerate(argv):\n"
        "        if a in ('-R', '--repo') and i + 1 < len(argv):\n"
        "            repo = argv[i + 1]\n"
        "        elif a.startswith('--repo='):\n"
        "            repo = a.split('=', 1)[1]\n"
        "    if repo != require_repo:\n"
        "        sys.exit(1)\n"
        "if len(argv) >= 3 and argv[0] == 'issue' and argv[1] == 'view':\n"
        "    n = argv[2]\n"
        "    if n in responses:\n"
        "        print(json.dumps(responses[n]))\n"
        "        sys.exit(0)\n"
        "sys.exit(1)\n" % (responses, require_repo)
    )
    script.chmod(0o755)
    return str(bin_dir)


def _fake_gh_list(tmpdir, issues):
    """A minimal `gh` stub for the #329 near-duplicate check: `gh issue
    list --state open ... --json number,title` prints `issues` (a list of
    {"number": int, "title": str} dicts) as JSON when `issues` is not
    None, else exits 1 (simulating a real lookup failure -- offline, no
    auth, rate-limited). Never touches the real network."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "issues = %r\n"
        "if len(argv) >= 2 and argv[0] == 'issue' and argv[1] == 'list':\n"
        "    if issues is None:\n"
        "        sys.exit(1)\n"
        "    print(json.dumps(issues))\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n" % (issues,)
    )
    script.chmod(0o755)
    return str(bin_dir)


def _fake_gh_stream(tmpdir, labels, issues=(), call_log=None):
    """A minimal `gh` stub for the #390 stream-routing gate: `gh label list
    --json name ...` prints `labels` (a list of NAME strings) as
    `[{"name": ...}, ...]` JSON when `labels` is not None, else exits 1
    (simulating a real lookup failure -- offline, no auth, `gh` missing) --
    the gate must degrade to "cannot verify stream-aware, skip" on that
    failure, never block on its own. `gh issue list ...` (the #329 near-dup
    check, reached once a filing passes the stream-routing gate) answers
    `issues` (default empty, so near-dup never fires). Never touches the
    real network.

    `call_log` (#390 adversarial-review MAJOR-1): when given, EVERY
    invocation's argv is appended as one line -- lets a test prove a
    network call (`gh label list`) was, or was NOT, made at all (e.g. for
    a full-authority filer, which must never pay it)."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "gh"
    label_rows = None if labels is None else [{"name": n} for n in labels]
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "call_log = %r\n"
        "if call_log:\n"
        "    with open(call_log, 'a', encoding='utf-8') as fh:\n"
        "        fh.write(' '.join(argv) + chr(10))\n"
        "label_rows = %r\n"
        "issues = %r\n"
        "if len(argv) >= 2 and argv[0] == 'label' and argv[1] == 'list':\n"
        "    if label_rows is None:\n"
        "        sys.exit(1)\n"
        "    print(json.dumps(label_rows))\n"
        "    sys.exit(0)\n"
        "if len(argv) >= 2 and argv[0] == 'issue' and argv[1] == 'list':\n"
        "    print(json.dumps(list(issues)))\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n" % (call_log, label_rows, issues)
    )
    script.chmod(0o755)
    return str(bin_dir)


def body_cmd(title, body_text, scope_gate=None,
             dedup="searched open issues for a similar title, found none",
             labels=None):
    """Build the standard gh-cli-recipes.md filing recipe: heredoc -> file -> -F.

    `dedup` (#329): prepended as a `Dedup-checked: <dedup>` line unless
    explicitly `None`/falsy -- a truthful-shaped default so every
    PRE-EXISTING PASS-path test in this file keeps satisfying the #329
    dedup-gate requirement with ZERO per-call-site change; a test proving
    the gate itself (its own absence) passes `dedup=None`.

    `labels` (#390): an optional list of `-l <value>` flags inserted BEFORE
    `-t <title>` -- e.g. `labels=["stream:david2"]`. `None`/empty adds
    nothing, so every PRE-EXISTING test keeps filing with no label at all."""
    body = body_text
    if dedup:
        body = "Dedup-checked: %s\n%s" % (dedup, body)
    if scope_gate:
        body = body.rstrip("\n") + "\nScope-gate: %s\n" % scope_gate
    label_flags = "".join(" -l %s" % lb for lb in (labels or []))
    return "cat > body.md <<'EOF'\n%s\nEOF\ngh issue create%s -t %r -F body.md" % (
        body, label_flags, title)


def _multi_body_cmd(items):
    """Chain several `gh issue create` filings in ONE command, each with
    its OWN body FILE NAME (body0.md, body1.md, ...) so the hook's
    heredoc-body extraction (keyed by filename) never collides between
    them -- a real batch would do the same. `items`: list of
    (title, body_text, scope_gate).

    Joined with `" &&\\n"` (a NEWLINE after `&&`), never a bare `" && "`
    on one line -- the hook's own heredoc pass only registers a
    `cat > FILE <<DELIM` trigger as FILE-attached when that exact text
    starts its own line (`CATFILE_RE` is anchored at `^`). Joining two
    items' heredocs on the SAME physical line (`... -F body0.md && cat >
    body1.md <<'EOF'`) makes the SECOND item's trigger fail that anchor,
    so its heredoc silently falls back to `direct_bodies` under a
    filename nothing looks up -- `resolve_body` then finds no file on
    disk and returns None, and every item past the first classifies as
    "missing Scope-gate" instead of whatever it was actually testing.
    Found live while testing the #329 in-batch near-dup fix, whose own
    assertion (a SPECIFIC block reason on item 2) is exactly the shape
    that would have caught this and previously did not, since
    `test_batch_within_one_call_hits_the_cap_too` only ever asserted a
    bare `returncode == 2` (true either way, for the wrong reason)."""
    parts = []
    for idx, (title, body_text, scope_gate) in enumerate(items):
        fname = "body%d.md" % idx
        body = "Dedup-checked: searched open issues, found none\n%s" % body_text
        if scope_gate:
            body = body.rstrip("\n") + "\nScope-gate: %s\n" % scope_gate
        parts.append(
            "cat > %s <<'EOF'\n%s\nEOF\ngh issue create -t %r -F %s" %
            (fname, body, title, fname))
    return " &&\n".join(parts)


class TestBasicBehavior(TestCase):
    def test_blocks_filing_with_no_scope_gate(self):
        r = run(body_cmd("small bug", "Found this small bug while fixing #100."))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Scope-gate:", r.stderr)

    def test_allows_filing_with_valid_criterion(self):
        r = run(body_cmd("cross-repo lease", "Two repos coordinate one rig.",
                          scope_gate="cross-cutting"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_invalid_criterion(self):
        r = run(body_cmd("bad", "x", scope_gate="because-i-said-so"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_direct_heredoc_dash_F_dash(self):
        cmd = ("gh issue create -t direct -F - <<'EOF'\n"
               "Dedup-checked: searched, found none\n"
               "Needs the user's decision.\nScope-gate: needs-user-decision\nEOF")
        r = run(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_inline_body_flag(self):
        r = run('gh issue create -t x --body "Dedup-checked: searched, found none\n'
                'Scope-gate: user-request"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_marker(self):
        r = run("gh issue create -t x --body y  # airuleset:scope-gate-ok testing")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unrelated_command_untouched(self):
        r = run("git status && echo done")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_issue_edit_not_gated(self):
        # Only CREATE is gated -- editing an existing issue (labels, title)
        # is not a filing decision.
        r = run("gh issue edit 5 --add-label bug")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pre_existing_body_file_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            bf = Path(d) / "body.md"
            bf.write_text("Dedup-checked: searched, found none\n"
                           "Scope-gate: schema-migration\n")
            r = run("gh issue create -t pre-existing -F body.md", cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_api_post_issues_gated(self):
        r = run("gh api repos/o/r/issues -X POST -f title=x -f body=y")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_api_post_issues_with_scope_gate_passes(self):
        r = run("gh api repos/o/r/issues -X POST -f title=x "
                "-f body='Dedup-checked: searched, found none\nScope-gate: api-break'")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pass_and_block_both_logged(self):
        home = tempfile.mkdtemp(prefix="airuleset-scopegate-log-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run(body_cmd("logged-block", "no criterion here"), home=home)
        run(body_cmd("logged-pass", "ok", scope_gate="planned-work"), home=home)
        log = Path(home) / ".claude" / "scope-gate.log"
        self.assertTrue(log.exists())
        text = log.read_text()
        self.assertIn("verdict=BLOCK", text)
        self.assertIn("verdict=PASS", text)
        self.assertIn("logged-block", text)
        self.assertIn("logged-pass", text)
        self.assertIn("criterion=planned-work", text)


class TestCdPrefixRelativeBodyPath(TestCase):
    """#483 -- a `-F <relative>` body file after a leading `cd <dir> &&`/`;`
    was resolved against the HOOK's OWN cwd, not the effective cwd after the
    cd, so `open()` failed and a fully-compliant filing was BLOCKED with the
    opaque per-item reason `-> none` (live incident gk@odoo-erp filing
    odoo-erp#4102; the identical body PASSED with an absolute path). The fix
    tracks a leading `cd` (option 1) and, when a `-F` disk path still cannot
    be read, gives an explicit actionable reason (option 2)."""

    def test_cd_prefix_relative_body_file_resolves(self):
        # Body file lives in `bodydir`; the hook runs from a DIFFERENT cwd
        # (`hookdir`, no body.md), and the command cd's into `bodydir`
        # before filing -- exactly the incident shape.
        with tempfile.TemporaryDirectory() as bodydir, \
                tempfile.TemporaryDirectory() as hookdir:
            (Path(bodydir) / "body.md").write_text(
                "Dedup-checked: searched, found none\n"
                "Scope-gate: schema-migration\n")
            r = run("cd %s && gh issue create -t 'cd-relative' -F body.md" % bodydir,
                    cwd=hookdir)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cd_prefix_semicolon_separator_resolves(self):
        # The `;` separator (not just `&&`) must resolve the same way.
        with tempfile.TemporaryDirectory() as bodydir, \
                tempfile.TemporaryDirectory() as hookdir:
            (Path(bodydir) / "body.md").write_text(
                "Dedup-checked: searched, found none\n"
                "Scope-gate: cross-cutting\n")
            r = run("cd %s ; gh issue create -t 'cd-semicolon' -F body.md" % bodydir,
                    cwd=hookdir)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_unreadable_relative_body_gives_explicit_reason(self):
        # A relative -F that genuinely cannot be read must block with an
        # ACTIONABLE reason naming the file and telling the filer to use an
        # absolute path -- never the opaque `-> none`.
        with tempfile.TemporaryDirectory() as hookdir:
            # No body.md anywhere; cd into a real but body-less dir.
            r = run("cd %s && gh issue create -t 'missing' -F body.md" % hookdir,
                    cwd=hookdir)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("not readable", r.stderr)
            self.assertIn("absolute", r.stderr)
            self.assertNotIn("-> none", r.stderr)

    def test_unresolvable_cd_target_relative_body_explicit_reason(self):
        # A `cd` into an unexpandable target ($VAR) makes the effective cwd
        # statically unknowable -- a relative -F must still degrade to the
        # explicit reason, never a wrong resolution or a fail-open pass.
        with tempfile.TemporaryDirectory() as hookdir:
            r = run("cd $SOMEDIR && gh issue create -t 'var-cd' -F body.md",
                    cwd=hookdir)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("not readable", r.stderr)
            self.assertIn("absolute", r.stderr)

    def test_absolute_body_path_still_reads(self):
        # Regression guard: an absolute -F path keeps working unchanged,
        # even behind a cd prefix.
        with tempfile.TemporaryDirectory() as bodydir, \
                tempfile.TemporaryDirectory() as hookdir:
            bf = Path(bodydir) / "body.md"
            bf.write_text("Dedup-checked: searched, found none\n"
                          "Scope-gate: schema-migration\n")
            r = run("cd %s && gh issue create -t 'abs' -F %s" % (hookdir, bf),
                    cwd=hookdir)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_scope_gate_still_blocks_with_real_reason(self):
        # No regression on the genuinely-missing-Scope-gate path: a READABLE
        # body (resolved via the cd prefix) with NO criterion still blocks,
        # and NOT with the #483 not-readable reason.
        with tempfile.TemporaryDirectory() as bodydir, \
                tempfile.TemporaryDirectory() as hookdir:
            (Path(bodydir) / "body.md").write_text(
                "Dedup-checked: searched, found none\n"
                "Just a plain finding with no criterion.\n")
            r = run("cd %s && gh issue create -t 'no-crit' -F body.md" % bodydir,
                    cwd=hookdir)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("not readable", r.stderr)

    def test_unreadable_absolute_body_gives_explicit_reason(self):
        # #483 review 🔵: the absolute-path unreadable branch also gives the
        # explicit reason, not the opaque `-> none`.
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "nope.md"  # never created
            r = run("gh issue create -t 'abs-missing' -F %s" % missing)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("not readable", r.stderr)
            self.assertNotIn("-> none", r.stderr)

    def test_unreadable_body_reason_cannot_inject_log_fields(self):
        # #483 review 🔴: the body-unreadable reason embeds the
        # attacker-controlled `-F` token, and it MUST be whitespace-collapsed
        # (_clean_field) before crossing the tab-separated hand-off to bash /
        # the scope-gate.log -- otherwise an embedded newline+tab in the `-F`
        # value forges a SECOND record (a `verdict=PASS` for an arbitrary
        # repo string) out of a command that was entirely BLOCKED, re-opening
        # #329's field-injection the log-field ORDER + _clean_field closed.
        home = tempfile.mkdtemp(prefix="airuleset-scopegate-inject-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        # An unreadable relative -F whose value spells a forged PASS record.
        evil = "x\nPASS\tzbynekdrlik/FORGED\tt.md"
        r = run("gh issue create -t inject -F '%s'" % evil, home=home)
        self.assertEqual(r.returncode, 2, r.stderr)
        log = Path(home) / ".claude" / "scope-gate.log"
        text = log.read_text() if log.exists() else ""
        # The whole command was BLOCKED -> exactly ONE record, a BLOCK, and
        # never a forged PASS / forged repo in a counting field.
        self.assertEqual(text.count("verdict="), 1, text)
        self.assertNotIn("verdict=PASS", text)


class TestChainDepthCap(TestCase):
    """#311 -- a review-finding follow-up (this issue names its own PARENT
    issue as a "follow-up" -- the EXACT phrasing the real odoo-erp
    scope-gate.log corpus already uses naturally: "(#3224 follow-up)")
    whose PARENT is ITSELF such a follow-up is a depth-2 review-finding
    chain -- the self-reinforcing sequence a criterion satisfied honestly
    at each individual hop cannot see. Blocked regardless of whether a
    Scope-gate criterion is present."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-chaindepth-test-")

    def test_first_generation_followup_with_non_chained_parent_passes(self):
        gh_bin = _fake_gh(self.tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        r = run(body_cmd("finding (#100 follow-up)",
                         "Found while fixing #100.", scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_second_generation_followup_is_blocked_even_with_valid_criterion(self):
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        })
        r = run(body_cmd("grandchild finding (#101 follow-up)",
                         "Found while fixing #101 (#101 follow-up).",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("chain", r.stderr.lower())

    def test_no_followup_phrasing_is_unaffected(self):
        gh_bin = _fake_gh(self.tmp, {})
        r = run(body_cmd("ordinary ticket", "Just a normal cleanup finding.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_lookup_failure_degrades_never_blocks_on_its_own(self):
        # every `gh issue view` call fails (simulates offline / no auth /
        # a genuinely nonexistent parent) -- the chain-depth check must
        # degrade to "cannot verify", never block BY ITSELF when a valid
        # Scope-gate criterion is already present.
        gh_bin = _fake_gh(self.tmp, {})
        r = run(body_cmd("finding (#999999 follow-up)",
                         "Found while fixing #999999.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_followup_wording_in_the_title_alone_is_detected(self):
        # the real corpus signature: the parent reference lives in the
        # TITLE ("(#N follow-up)"), not necessarily the body.
        gh_bin = _fake_gh(self.tmp, {
            "200": {"title": "parent (#50 follow-up)",
                    "body": "Found while fixing #50 (#50 follow-up)."},
        })
        r = run(body_cmd("child (#200 follow-up)",
                         "Some unrelated body text.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestLocMismatch(TestCase):
    """#311 point 3 -- Scope-gate verifiability. `>300-loc` is checked
    mechanically ONLY where trivially checkable: the body's OWN text
    states a bare number next to "loc"/"lines" (the exact "violation
    CONFESSED in the issue body itself" shape #137 already established as
    this hook's founding evidence). A body with no such number is left
    exactly as before -- the claim cannot be verified, so it is trusted,
    matching the hook's own documented limit."""

    def test_body_confessing_a_small_loc_count_is_blocked(self):
        r = run(body_cmd("small change", "This is a small ~50 loc fix.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("loc", r.stderr.lower())

    def test_body_confessing_exactly_300_lines_is_blocked(self):
        # the criterion is literally spelled ">300-loc" -- exactly 300 does
        # not clear it.
        r = run(body_cmd("borderline", "About 300 lines of changes.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_body_stating_a_genuinely_large_count_passes(self):
        r = run(body_cmd("big rework", "Roughly 850 lines across 6 files.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_stated_number_is_unaffected_trusted_as_before(self):
        r = run(body_cmd("no number", "A genuinely large rework, no count "
                                      "given.", scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_check_only_applies_to_the_loc_criterion(self):
        # a small number mentioned in a body filed under a DIFFERENT
        # criterion must never be misread as a >300-loc self-contradiction.
        r = run(body_cmd("unrelated number", "Affects 12 lines in an "
                                             "unrelated config note.",
                         scope_gate="cross-cutting"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_quoting_the_gate_threshold_before_the_real_count_still_passes(self):
        # adversarial-review finding F1 (this batch's own review, TRIGGERED
        # live) -- a `.search()`-only match takes the FIRST number next to
        # "loc"/"lines", and quoting the follow-up gate's OWN threshold text
        # (the natural way to justify the criterion) is a real, common
        # shape: "under ~100 LoC ... roughly 620 LoC across 5 modules."
        # matched the FIRST number (100) and false-blocked the honest,
        # correctly-labelled filing. Every stated number must clear 300,
        # not just the first one found.
        r = run(body_cmd(
            "big rework",
            "The follow-up gate says a cleanup under ~100 LoC must be "
            "fixed in-PR. This one is not: roughly 620 LoC across 5 "
            "modules.", scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_quoting_the_threshold_itself_before_the_real_count_still_passes(self):
        # the second F1 example: "Well over the 300 line threshold: ~740
        # lines total." matched "300 line" first and false-blocked exactly
        # the author who is being MOST honest about clearing the gate.
        r = run(body_cmd(
            "big rework 2",
            "Well over the 300 line threshold: ~740 lines total.",
            scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestChainDepthCapReviewFixes(TestCase):
    """#311 -- adversarial-review findings on the chain-depth cap itself
    (this batch's own review, all TRIGGERED live against the real hook)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-chaindepth-review-test-")

    def test_a_root_ticket_linking_its_own_children_is_not_a_followup_itself(self):
        # finding F2 -- a root/umbrella ticket's body naturally LINKS the
        # follow-ups it spawned ("Spawned work: #3250 follow-up, #3251
        # follow-up.") -- that FORWARD reference must never be misread as
        # THIS ticket itself being a follow-up of something. Only a
        # BACKWARD reference (to a lower issue number) is a genuine
        # ancestry link.
        gh_bin = _fake_gh(self.tmp, {
            "3035": {"title": "root ticket",
                     "body": "Spawned work: #3250 follow-up, #3251 follow-up."},
        })
        r = run(body_cmd("real child (#3035 follow-up)",
                         "Found while fixing #3035.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plural_followups_wording_is_detected(self):
        # finding F7 -- `\bfollow[- ]?up\b` misses the plural "follow-ups".
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        })
        r = run(body_cmd("grandchild finding (#101 follow-ups)",
                         "Found while fixing #101 (#101 follow-up).",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_cross_repo_parent_is_looked_up_in_the_named_repo(self):
        # finding F8 -- `_gh_view_text` ignored an explicit `-R owner/repo`
        # and always resolved the parent against the LOCAL cwd's repo,
        # silently looking up the WRONG issue #101 when the real parent
        # lives in a different repo. The stub ONLY answers when `-R
        # other-owner/other-repo` is actually passed through -- a caller
        # that drops it gets a lookup failure, same as a real mismatch.
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        }, require_repo="other-owner/other-repo")
        cmd = ("cat > body.md <<'EOF'\nFound while fixing #101 "
               "(#101 follow-up).\nScope-gate: cross-cutting\nEOF\n"
               "gh issue create -R other-owner/other-repo "
               "-t 'grandchild finding (#101 follow-up)' -F body.md")
        r = run(cmd, gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestDedupGate(TestCase):
    """#329 -- the dedup gate's two halves: a REQUIRED `Dedup-checked: ...`
    line (structural, no network -- same logged-claim shape as
    `Scope-gate:`) and a REAL bounded near-duplicate title check against
    the target repo's own open issues (`gh issue list`, degrading to
    "cannot verify" on any failure -- never blocking on its own)."""

    def test_blocks_filing_with_valid_criterion_but_no_dedup_line(self):
        # #329 adversarial review: loose keyword checks (e.g. "dedup" in
        # stderr) pass against the hook's own generic static message
        # regardless of WHICH reason actually fired -- assert the exact
        # per-item SUMMARY line the hook prints instead, which names the
        # real classified reason.
        r = run(body_cmd("real finding", "A genuinely distinct thing.",
                          scope_gate="cross-cutting", dedup=None))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn('"real finding" -> no-dedup-line', r.stderr)

    def test_allows_filing_with_dedup_line_present(self):
        r = run(body_cmd("real finding", "A genuinely distinct thing.",
                          scope_gate="cross-cutting"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_near_duplicate_title_blocks_naming_the_existing_issue(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-dedup-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh_list(tmp, [
            {"number": 500, "title": "Retry queue drops messages under load"},
        ])
        r = run(body_cmd("Retry queue drops messages under heavy load",
                          "Found this while testing.", scope_gate="cross-cutting"),
                gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)
        # #329 adversarial review: assert the exact classified reason
        # (naming the real duplicate issue number), not a loose keyword.
        self.assertIn(
            '"Retry queue drops messages under heavy load" -> '
            "near-duplicate:#500", r.stderr)

    def test_near_duplicate_already_referenced_by_number_passes(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-dedup-test2-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh_list(tmp, [
            {"number": 500, "title": "Retry queue drops messages under load"},
        ])
        r = run(body_cmd("Retry queue drops messages under heavy load",
                          "A distinct follow-on to #500, explicitly linked.",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_dissimilar_titles_pass(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-dedup-test3-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh_list(tmp, [
            {"number": 500, "title": "Unrelated dashboard rendering glitch"},
        ])
        r = run(body_cmd("Retry queue drops messages under heavy load",
                          "A genuinely distinct finding.",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_list_failure_degrades_never_blocks_on_its_own(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-dedup-test4-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh_list(tmp, None)  # simulates a real lookup failure
        r = run(body_cmd("real finding", "A genuinely distinct thing.",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDailyCap(TestCase):
    """#329 -- soft per-day, per-repo cap on agent-authored filings
    (8/day/repo). Real SEQUENTIAL filings into a shared HOME (never a
    hand-seeded log line), so the test exercises the hook's OWN log
    format end-to-end instead of a fixture's own assumption about it."""

    def test_ninth_non_exempt_filing_in_one_day_blocks(self):
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-test-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            for i in range(8):
                r = run(body_cmd(
                    "finding %d" % i,
                    "A genuinely distinct topic %d: %s." % (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting"), home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "seed %d: %s" % (i, r.stderr))
            r = run(body_cmd(
                "finding 9",
                "One more genuinely distinct topic: %s." % os.urandom(4).hex(),
                scope_gate="cross-cutting"), home=home, cwd=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            # #329 adversarial review: assert the exact classified reason.
            self.assertIn('"finding 9" -> daily-cap', r.stderr)

    def test_eighth_filing_in_one_day_still_passes(self):
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-test2-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            for i in range(7):
                r = run(body_cmd(
                    "finding %d" % i,
                    "A genuinely distinct topic %d: %s." % (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting"), home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "seed %d: %s" % (i, r.stderr))
            r = run(body_cmd(
                "finding 8",
                "The 8th genuinely distinct topic: %s." % os.urandom(4).hex(),
                scope_gate="cross-cutting"), home=home, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_exempt_criteria_bypass_the_cap(self):
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-test3-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            for i in range(8):
                r = run(body_cmd(
                    "finding %d" % i,
                    "A genuinely distinct topic %d: %s." % (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting"), home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "seed %d: %s" % (i, r.stderr))
            r = run(body_cmd(
                "user requested this",
                "The user explicitly asked for this: %s." % os.urandom(4).hex(),
                scope_gate="user-request"), home=home, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cap_is_scoped_per_repo(self):
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-test4-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d1:
            for i in range(8):
                r = run(body_cmd(
                    "finding %d" % i,
                    "A genuinely distinct topic %d: %s." % (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting"), home=home, cwd=d1)
                self.assertEqual(r.returncode, 0, "seed %d: %s" % (i, r.stderr))
            with tempfile.TemporaryDirectory() as d2:
                r = run(body_cmd(
                    "finding in a different repo",
                    "A genuinely distinct topic: %s." % os.urandom(4).hex(),
                    scope_gate="cross-cutting"), home=home, cwd=d2)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_batch_within_one_call_hits_the_cap_too(self):
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-test5-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            items = [
                ("finding %d" % i,
                 "A genuinely distinct topic %d: %s." % (i, os.urandom(4).hex()),
                 "cross-cutting")
                for i in range(9)
            ]
            r = run(_multi_body_cmd(items), home=home, cwd=d)
            self.assertEqual(r.returncode, 2, r.stderr)


class TestChainWidthCap(TestCase):
    """#329 -- chain-WIDTH cap: siblings off ONE parent, same day, same
    repo (cap=2, the 3rd blocks). Extends #311's chain-DEPTH cap with the
    WIDTH half of the same measured failure (odoo-erp's real
    #3250/#3251/#3252/#3258 -- four siblings off one parent, each
    individually depth-1). Reuses the SAME "(#N follow-up)" wording the
    depth cap already detects."""

    def test_third_sibling_off_one_parent_same_day_blocks(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            for i in range(2):
                r = run(body_cmd(
                    "sibling %d (#100 follow-up)" % i,
                    "Found while fixing #100 (#100 follow-up).",
                    scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
                self.assertEqual(r.returncode, 0, "sibling %d: %s" % (i, r.stderr))
            r = run(body_cmd(
                "sibling 2 (#100 follow-up)",
                "Found while fixing #100 (#100 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 2, r.stderr)
            # #329 adversarial review: assert the exact classified reason.
            self.assertIn(
                '"sibling 2 (#100 follow-up)" -> chain-width-cap', r.stderr)

    def test_second_sibling_off_one_parent_still_passes(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-test2-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            r = run(body_cmd(
                "sibling 0 (#100 follow-up)",
                "Found while fixing #100 (#100 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = run(body_cmd(
                "sibling 1 (#100 follow-up)",
                "Found while fixing #100 (#100 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_width_cap_scoped_per_parent(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-test3-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug A", "body": "Just a normal ticket."},
            "200": {"title": "original bug B", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            for i in range(2):
                r = run(body_cmd(
                    "sibling-A %d (#100 follow-up)" % i,
                    "Found while fixing #100 (#100 follow-up).",
                    scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = run(body_cmd(
                "sibling-B 0 (#200 follow-up)",
                "Found while fixing #200 (#200 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_width_cap_exempt_for_user_request(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-test4-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            for i in range(2):
                r = run(body_cmd(
                    "sibling %d (#100 follow-up)" % i,
                    "Found while fixing #100 (#100 follow-up).",
                    scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = run(body_cmd(
                "sibling 2 (#100 follow-up)",
                "A user directive, distinct from the cross-cutting siblings.",
                scope_gate="user-request"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_parents_field_is_written_to_the_log(self):
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-test5-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            r = run(body_cmd(
                "sibling 0 (#100 follow-up)",
                "Found while fixing #100 (#100 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)
        log = Path(tmp) / ".claude" / "scope-gate.log"
        text = log.read_text()
        self.assertIn("parents=100", text)

    def test_width_cap_checks_every_referenced_parent_not_just_the_first(self):
        # #329 adversarial review MAJOR: the first cut only ever checked
        # parents[0] while crediting ALL referenced parents on the PASS
        # side -- a filing naming an early DECOY parent (with zero
        # siblings) before its real, already-saturated parent would
        # wrongly pass. `parents[0]` here is the decoy #999 (title comes
        # before body in the concatenated text _chain_parents scans); the
        # real, already-capped parent is #100, referenced second.
        tmp = tempfile.mkdtemp(prefix="airuleset-widthcap-allparents-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "999": {"title": "decoy parent", "body": "An unrelated ticket."},
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            for i in range(2):
                r = run(body_cmd(
                    "sibling %d (#100 follow-up)" % i,
                    "Found while fixing #100 (#100 follow-up).",
                    scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
                self.assertEqual(r.returncode, 0, "sibling %d: %s" % (i, r.stderr))
            r = run(body_cmd(
                "decoy first (#999 follow-up)",
                "Also found while fixing #100 (#100 follow-up).",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("chain-width-cap", r.stderr)


class TestScopeGate329ReviewFixes(TestCase):
    """#329 -- regression tests for the adversarial-review findings on the
    original #329 implementation. Each test locks one specific finding
    that was independently reproduced and fixed; see the hook's own
    header comments for the full write-up of each."""

    def test_phantom_pass_in_a_blocked_batch_never_counts_toward_the_cap(self):
        # CRITICAL: a Bash command that blocks on ANY segment blocks the
        # WHOLE tool call -- nothing in it runs, including a sibling this
        # hook itself classified PASS. That sibling must be logged as
        # NOTFILED, never PASS, or it silently burns cap budget for an
        # issue that was never actually filed.
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-phantom-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            cmd = (
                "cat > body1.md <<'EOF'\n"
                "Dedup-checked: searched, found none\n"
                "A genuinely distinct phantom topic: %s.\n"
                "Scope-gate: cross-cutting\n"
                "EOF\n"
                "gh issue create -t 'phantom finding' -F body1.md && "
                "cat > body2.md <<'EOF'\n"
                "No scope gate line at all here.\n"
                "EOF\n"
                "gh issue create -t 'blocked finding' -F body2.md"
            ) % os.urandom(4).hex()
            r = run(cmd, home=home, cwd=d)
            self.assertEqual(r.returncode, 2, r.stderr)

            log = Path(home) / ".claude" / "scope-gate.log"
            text = log.read_text()
            self.assertIn("verdict=NOTFILED", text)
            self.assertNotIn("verdict=PASS", text)

            # If the phantom entry had wrongly counted, only 7 of these 8
            # genuinely distinct real filings would pass.
            for i in range(8):
                r = run(body_cmd(
                    "real finding %d" % i,
                    "A genuinely distinct real topic %d: %s." %
                    (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting"), home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "real %d: %s" % (i, r.stderr))

    def test_exempt_filings_never_consume_daily_cap_budget(self):
        # MAJOR: `_log_pass_count` used to count EVERY PASS line
        # regardless of criterion, so an exempt planned-work/user-request
        # batch silently consumed the SAME budget the block message
        # promises is reserved for non-exempt filings.
        home = tempfile.mkdtemp(prefix="airuleset-dailycap-exempt-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            for i in range(10):
                r = run(body_cmd(
                    "user request %d" % i,
                    "The user explicitly asked for this %d: %s." %
                    (i, os.urandom(4).hex()),
                    scope_gate="user-request"), home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "exempt %d: %s" % (i, r.stderr))
            # The FIRST non-exempt filing of the day must still pass --
            # if any exempt filing had wrongly counted, the daily count
            # would already be >= 8 by now.
            r = run(body_cmd(
                "first real finding",
                "A genuinely distinct real topic: %s." % os.urandom(4).hex(),
                scope_gate="cross-cutting"), home=home, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_api_issues_path_resolves_the_same_target_repo_as_dash_R(self):
        # MAJOR: the caps used to be keyed on the cwd-derived repo while
        # the near-dup check used the filing's own -R target -- a
        # `gh api repos/<o>/<r>/issues` filing (no -R flag at all) had NO
        # target-repo resolution whatsoever and was capped/checked
        # against the wrong (cwd) repo. Seed the cap via an explicit -R,
        # then confirm the SAME target repo is reached via the API path.
        home = tempfile.mkdtemp(prefix="airuleset-targetrepo-api-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            for i in range(8):
                r = run(body_cmd(
                    "finding %d" % i,
                    "A genuinely distinct topic %d: %s." %
                    (i, os.urandom(4).hex()),
                    scope_gate="cross-cutting") + " -R owner/repo",
                    home=home, cwd=d)
                self.assertEqual(r.returncode, 0, "seed %d: %s" % (i, r.stderr))
            r = run(
                "gh api repos/owner/repo/issues -X POST -f title=x "
                "-f body='Dedup-checked: searched, found none\n"
                "One more distinct topic.\nScope-gate: cross-cutting'",
                home=home, cwd=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("daily-cap", r.stderr)

    def test_title_with_embedded_tab_does_not_corrupt_downstream_fields(self):
        # MAJOR: an embedded tab/newline in a TITLE used to be able to
        # shift every field after it in the tab-separated hand-off from
        # the embedded python script to bash (criterion/session/parents).
        tmp = tempfile.mkdtemp(prefix="airuleset-tabtitle-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh(tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        with tempfile.TemporaryDirectory() as d:
            title_with_tab = "weird\ttitle (#100 follow-up)"
            cmd = (
                "cat > body.md <<'EOF'\n"
                "Dedup-checked: searched, found none\n"
                "Found while fixing #100 (#100 follow-up).\n"
                "Scope-gate: cross-cutting\n"
                "EOF\n"
                "gh issue create -t '%s' -F body.md" % title_with_tab
            )
            r = run(cmd, home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)
        log = Path(tmp) / ".claude" / "scope-gate.log"
        text = log.read_text()
        self.assertNotIn("\t", text)
        self.assertIn("parents=100", text)

    def test_two_near_identical_titles_in_one_batch_are_caught(self):
        # MAJOR: the near-dup check only ever compared against REMOTE
        # open issues, so two near-identical titles filed in the SAME
        # Bash call never saw each other (neither exists yet at
        # PreToolUse time).
        home = tempfile.mkdtemp(prefix="airuleset-inbatch-dedup-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            cmd = _multi_body_cmd([
                ("Retry queue drops messages under load",
                 "First filing of the pair.", "cross-cutting"),
                ("Retry queue drops messages under heavy load",
                 "Second filing, a near-duplicate of the first, same batch.",
                 "cross-cutting"),
            ])
            r = run(cmd, home=home, cwd=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("near-duplicate:in-this-batch", r.stderr)

    def test_token_jaccard_does_not_flag_distinct_box_numbered_titles(self):
        # CRITICAL (root cause of the near-dup redesign): a
        # character-level SequenceMatcher ratio cannot separate this
        # fleet's real duplicates from its real distinct tickets --
        # cam4/cam5-shaped titles measured at ratio 0.983, HIGHER than
        # the corpus's own genuine duplicate pair (0.925). Token-Jaccard
        # must correctly pass this (entirely different tokens, ratio 0).
        tmp = tempfile.mkdtemp(prefix="airuleset-jaccard-boxnum-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        gh_bin = _fake_gh_list(tmp, [
            {"number": 900,
             "title": "cam4 restart loop blocks the E2E preflight"},
        ])
        with tempfile.TemporaryDirectory() as d:
            r = run(body_cmd(
                "cam5 restart loop blocks the E2E preflight",
                "A genuinely distinct box, cam5, has the identical symptom.",
                scope_gate="cross-cutting"), home=tmp, cwd=d, gh_bin=gh_bin)
            self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# CORPUS REPLAY — real issue bodies, fetched once via `gh issue view --json
# body`. Excerpts below are unmodified substrings of the real text (never
# paraphrased). #846/#843/#2388 are the three cases #137 itself names as
# confirmed leaks (violation confessed in-body, no genuine gate criterion
# anywhere). The 15 legitimate ones are real recent camera-box issues whose
# bodies genuinely state a reason matching one of the gate's own exemptions.
# --------------------------------------------------------------------------- #

# (repo#issue, title, real body excerpt, verdict is always "leak" here)
LEAK_CORPUS = [
    ("camera-box#846",
     "imag-gpu-contention-sampler.sh (#674) also hard-requires nvidia-smi -- same class as #845, but standalone/unwired",
     "While fixing #845 (the [4e/8] E2E-gate headroom preflight hard-requiring nvidia-smi, which does "
     "not exist on the replacement imag notebook 10.77.9.187 -- Intel iGPU only, no discrete GPU), I "
     "found a SIBLING script with the exact same class of assumption: scripts/imag-gpu-contention-"
     "sampler.sh (#674) unconditionally requires nvidia-smi and hard-FATALs when it's absent. This one "
     "is NOT wired into recording-e2e.sh's automated [4e/8] preflight or any CI job -- it's a standalone "
     "manual diagnostic tool a session would run by hand, so it is out of #845's scope. Filing "
     "separately per no-dropped-work.md. Same fix shape as #845 applies."),
    ("camera-box#843",
     "verify-imag.sh check (n) always FAILs: MV-scenes regex doesn't match imag_scenes.py's real "
     "\"(multiview, low-bw)\" output",
     "## Symptom\nscripts/verify-imag.sh check (n) (\"scenes present + Multiview populated\") FAILS "
     "on a fully healthy box. ## Root cause\nimag_scenes_output_ok() in scripts/verify-imag.sh requires "
     "the literal regex ^MV scenes: ${count}/${count} OK -- but imag_scenes.py's own print statement "
     "puts (multiview, low-bw) BETWEEN the count and OK, so the check's regex has no wildcard there and "
     "can never match. This is a pre-existing bug, not something introduced by #840's changes (I did "
     "not touch imag_scenes.py or imag_scenes_output_ok in #840's diff)."),
    ("odoo-erp#2388",
     "montalu payout marker (#2378): drop the dead 'done' state, enforce the date/uid invariant, fix @api.model",
     "Three cosmetic findings from the gatekeeper ride-along review of PR #2386 (#2378, release "
     "19.0.2.137.0). None blocked the release -- filing so they are not lost. 1. Dead 'done' state "
     "value in the payout action domain. 'done' was removed from the sale.order.state selection in "
     "Odoo 17+, so the value matches nothing and a test string-asserts the literal domain, locking the "
     "dead value in. Drop 'done' and update the asserting test. 2. _settled_date/_settled_uid can be "
     "written without the flag -- manager-only, exposure negligible. 3. @api.model on a record-"
     "inspecting method -- works, but semantically off."),
]

# (repo#issue, title, real body excerpt, truthful criterion a worker could
# honestly attach -- chosen from what the body itself already states)
LEGIT_CORPUS = [
    ("camera-box#850",
     "Test fixture leaks SIGTERM-immune orphan processes on assertion failure",
     "A dev1 disk/process audit found orphaned processes (ppid=1) in /home/newlevel/devel/camera-box: "
     "five leaked spawn_renamed_sigterm_ignoring() processes, oldest 15 days, still actively leaking "
     "(a new one appeared 2026-07-27). tests/harness_rig_test_ledger_723.rs:262 spawns a child that "
     "traps TERM and never exits -- this is a test-harness-wide leak, not one test's problem.",
     "cross-cutting"),
    ("camera-box#839",
     "stream box LAN NIC: 23-65 ms RTT on a 1 ms LAN (100 Mbps link) — refuses the #704 gate",
     "The stream box answers on its LAN NIC with 23-65 ms round trip while every other rig node "
     "answers in ~1 ms. This is what makes the DanteSync NTP spread on that node exceed the E2E "
     "gate's stability bound across the whole fleet gate, not just one check.",
     "cross-cutting"),
    ("camera-box#830",
     "Rig has no cross-repo lease: camera-box E2E and restreamer E2E fight over stream OBS",
     "Two repositories drive the SAME physical rig from CI, and neither knows about the other. "
     "camera-box's full-path-e2e.yml reroutes strih+stream program scenes; restreamer's Rust CI "
     "starts OBS streaming and holds it for a sustained soak, releasing it only in its own step. Live "
     "collision confirmed 2026-07-27.",
     "cross-cutting"),
    ("camera-box#818",
     "Fleet genlock lineage is split three ways — the E2E gate refuses (genlock_parity DRIFT)",
     "With the DanteSync clock gate repaired and both boxes' per-box drift-guard clean, the hard gate "
     "now refuses on the CROSS-BOX parity facet: three boxes (strih/stream/imag), three different "
     "genlock build SHAs. Each box individually matches its own pinned set -- this is purely the "
     "'one lineage across the whole fleet' assert.",
     "cross-cutting"),
    ("camera-box#821",
     "No acceptance gate for the imag notebook — add scripts/verify-imag.sh (the imag twin of verify-device.sh)",
     "There is no acceptance gate for an imag notebook -- the cam fleet has scripts/verify-device.sh "
     "(#454), the imag box has nothing. Deliverable: scripts/verify-imag.sh -- run from dev1 over SSH "
     "AFTER the swap, re-deriving every fact from live signals, checking hostname, static IP, "
     "ssh.service, display-manager, autologin, OBS, and more -- a brand new multi-hundred-line "
     "acceptance script.",
     ">300-loc"),
    ("camera-box#827",
     "Rig is now a 4-box fleet (cam1-cam4) — reconfigure camera-set/harness/workflow; cam5-cam7",
     "The [0/8] fleet preflight (#758) requires every box cam1..cam7 to be either reachable with "
     "camera-box.service active, or explicitly acknowledged offline via CAMBOX_OFFLINE_ACK. The "
     "workflow never sets that variable, so the gate hard-fails the moment the physical fleet is "
     "smaller than seven -- this needs a fleet-wide workflow + harness reconfiguration, not a local fix.",
     "cross-cutting"),
    ("camera-box#816",
     "setup-imag.sh musí byť hardvérovo agnostický (dGPU podmienene, CPU izolácia z topológie, ...)",
     "Cast #791 (nula rucnych krokov) -- setup-imag.sh dnes predpoklada KONKRETNY hardver dnesneho "
     "imagu. Novy notebook padne na tychto predpokladoch. Co opravit: step 9 NVIDIA podmienene, step 8 "
     "CPU izolacia odvodena z topologie namiesto natvrdo 2-11/12-15, a dalsie kroky -- viacpolozkovy "
     "checklist prerabajuci viacero krokov instalacneho skriptu (stovky riadkov).",
     ">300-loc"),
    ("camera-box#844",
     "A cancelled E2E gate run leaks the measurement burn and hard-blocks every later run",
     "full-path-e2e.yml runs under a ref-keyed concurrency group, so every push to dev cancels the "
     "in-flight gate run. The harness turns the per-source measurement burn ON at [4b/8] and clears it "
     "during its own cleanup -- but a CANCELLED run never reaches cleanup, so the burn stays ON on the "
     "rig and blocks every subsequent CI run fleet-wide until cleared by hand.",
     "cross-cutting"),
    ("camera-box#849",
     "imag-obs-watchdog.py's wedge forensic snapshot() hardcodes NVIDIA (nvidia-smi + PCI 01:00.0)",
     "Found while sweeping the repo for other NVIDIA-only assumptions during #847. Not fixed there -- "
     "different subsystem, not CI-gate-blocking, needs its own investigation into what the "
     "Intel-iGPU-equivalent forensics should be -- fails the 'genuinely separate unit of work' "
     "bundling gate for that PR.",
     "cross-cutting"),
    ("camera-box#848",
     "QSV (obs_qsv11_v2) does not actually work on imag-nb — a Texture/VAAPI MFX_ERR_UNSUPPORTED at Init()",
     "Filed while fixing #847. QSV was the OBVIOUS-looking fallback and was the fallback #847's own "
     "issue text recommended investigating -- but 3 rounds of LIVE testing on the box proved it does "
     "not actually record end-to-end. This is its own independent hardware investigation, not a "
     "same-file cleanup of #847's diff.",
     "cross-cutting"),
    ("camera-box#847",
     "imag-nb recording never actually starts — RecEncoder is hardcoded to NVENC, box has no NVIDIA GPU",
     "Found while fixing #845. With #845's fix landed, the gate's [4e/8] headroom preflight now "
     "correctly passes on imag-nb, and the run advances all the way to [5/8] StartRecord -- but "
     "imag's own OBS recording never actually starts. This needs a real encoder-selection rework "
     "across the whole recording pipeline, well past a same-file cleanup.",
     ">300-loc"),
    ("camera-box#842",
     "Observation: per-source genlock fifo accumulating underruns and drops on EVERY sampled camera",
     "While diagnosing the Program-projector stutter (#841) the running OBS log showed the per-source "
     "genlock fifo accumulating underruns and drops on EVERY sampled camera, every ~5 s, across a ~25 "
     "minute window -- affects every camera source fleet-wide, not the one file #841 touches.",
     "cross-cutting"),
    ("camera-box#836",
     "DanteSync gate judges each node on ONE sample: a noisy node passes ~10% of the time (false pass/fail)",
     "The [0/8] DanteSync gate takes ONE sample of each node's ntp_offset_us and turns it into a hard "
     "pass/fail. Measured on the live rig: 22 samples from the stream box, 25 s apart -- only 2 of "
     "those 22 fall inside the bound. This needs a real statistical redesign of the gate's sampling "
     "algorithm, not a same-file tweak.",
     ">300-loc"),
    ("camera-box#828",
     "cam4 has no grabber card fitted — camera-box restart-loops 27k times and blocks the E2E preflight",
     "cam4 is powered, reachable over SSH and otherwise healthy, but has no capture device: "
     "ls /dev/video* -> No such file or directory, lsusb shows no grabber. The grabber card was lent "
     "out physically -- restoring it, or deciding to run the fleet without cam4, needs the user's call "
     "on the physical hardware.",
     "needs-user-decision"),
    ("camera-box#817",
     "bundle-state-server is Ready-but-not-Running after a box restart — E2E gate refuses (exit 11)",
     "recording-e2e.sh's version-integrity gate fetches each Windows box's stack state from the "
     "standing bundle-state-server (#650). When that server is not running the gate reports the box "
     "UNKNOWN and REFUSES the run for every box in the fleet -- a CI-infrastructure-wide gate failure, "
     "not a single file's bug.",
     "cross-cutting"),
]


class TestStreamRoutingGate(TestCase):
    """#390 -- a stream-aware repo (>=1 real `stream:*` label, per `gh label
    list`) requires a `gh issue create` filed by a KNOWN sub-dev stream
    account (linux user in AUTHORITY_BY_USER, resolved via
    `airuleset.resolve_authority()`) to carry an explicit `stream:<x>`
    label; when the applied stream differs from the filer's OWN
    (`stream:<their-linux-username>`), a `Stream-routing: <reason>` body
    line is also required. A full-authority filer (airuleset#827: in
    FULL_AUTHORITY_USERS -- the maintainer/gatekeeper, e.g. newlevel) has no
    "own" stream to compare against and is NEVER gated -- ordinary core-ticket
    filing carries no stream label by design (`_core_search_excl()`). Since
    airuleset#839 the PROFILE gate is uid-based, so a reduced fixture (`david2`)
    writes a `fork-no-merge` marker into the hook's cwd to engage the gate (via
    AUTHORITY_BY_USER); a `gatekeeper` fixture writes NO marker -> the real full
    box -> not gated (the profile half is what makes the gate engage, proven
    load-bearing by the reduced-marker tests that DO block). Since airuleset#840
    the OWN-STREAM identity is the un-spoofable uid-based `_current_user()`,
    supplied to the subprocess via the `AIRULESET_SCOPE_GATE_TEST_STREAM_USER`
    seam, NOT the env-spoofable `getpass.getuser()` -- so a `LOGNAME`/`USER`
    spoof (`spoof_login`) can no longer route a filing under a foreign stream
    (`test_env_spoofed_own_stream_cannot_bypass_routing`)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-streamroute-test-")

    def test_repo_not_stream_aware_is_unaffected(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=[])
        r = run(body_cmd("ordinary bug", "no stream labels anywhere on this repo",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stream_account_missing_label_on_aware_repo_blocks(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("no label at all", "filed with no stream label",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("stream", r.stderr.lower())

    def test_stream_account_own_label_passes(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("own stream", "filed for our own work",
                          scope_gate="cross-cutting", labels=["stream:david2"]),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stream_account_foreign_label_no_justification_blocks(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("foreign", "found this while working my own module",
                          scope_gate="cross-cutting", labels=["stream:david"]),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Stream-routing", r.stderr)

    def test_stream_account_foreign_label_with_justification_passes(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        body = ("Stream-routing: david -- patri im, defekt je v ich module\n"
                "found this while working my own module")
        r = run(body_cmd("foreign justified", body, scope_gate="cross-cutting",
                          labels=["stream:david"]),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_full_authority_filer_is_never_gated(self):
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("core ticket", "ordinary core work, no stream label",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="gatekeeper")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_env_spoofed_own_stream_cannot_bypass_routing(self):
        # airuleset#840: a stream whose TRUE identity is david2 sets
        # LOGNAME/USER=david to spoof its own-stream, then files a stream:david
        # ticket with NO Stream-routing justification. Under the pre-#840
        # getpass.getuser() path the own-stream would follow the env spoof
        # (stream:david == the applied label -> silent PASS). The un-spoofable
        # uid-based own-stream (stream:david2) makes the applied stream:david a
        # FOREIGN label -> BLOCKED. This is the vulnerability #840 closes.
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("env spoof", "found this while working my own module",
                          scope_gate="cross-cutting", labels=["stream:david"]),
                gh_bin=gh_bin, user="david2", spoof_login="david")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Stream-routing", r.stderr)

    def test_label_list_lookup_failure_degrades_never_blocks_on_its_own(self):
        # gh label list itself fails (offline/unauthenticated) -- must
        # degrade to "cannot verify stream-awareness", never block.
        gh_bin = _fake_gh_stream(self.tmp, labels=None)
        r = run(body_cmd("cant verify", "no label at all",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_api_post_is_out_of_scope(self):
        # gh api ... POST is deliberately out of scope for this gate (see
        # the hook's own header) -- gh issue create is this repo's
        # dominant, documented filing recipe.
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run("gh api repos/o/r/issues -X POST -f title=x "
                "-f body='Dedup-checked: searched, found none\nScope-gate: cross-cutting'",
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestStreamRoutingGateReviewFixes(TestCase):
    """#390 adversarial-review findings (fresh-context fable review of the
    already-shipped GREEN commit) -- each proven live against the real
    shipped hook before being fixed here."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-streamroute-review-test-")

    def test_full_authority_filer_never_pays_the_network_label_lookup(self):
        # MAJOR-1: `_stream_routing_block_reason` used to call the network
        # `gh label list` BEFORE the cheap, local authority check -- so a
        # full-authority filer (never gated by this new check at all) paid
        # a real network round-trip on EVERY filing, fleet-wide, violating
        # this hook's own #329 "cheap local checks before the network call"
        # discipline. The authority check must run FIRST.
        call_log = str(Path(self.tmp) / "gh-calls.log")
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"],
                                  call_log=call_log)
        r = run(body_cmd("core ticket", "ordinary core work, no stream label",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="gatekeeper")
        self.assertEqual(r.returncode, 0, r.stderr)
        log_text = Path(call_log).read_text() if Path(call_log).exists() else ""
        self.assertNotIn(
            "label list", log_text,
            "a full-authority filer must never pay the network gh label "
            "list call (#390 adversarial-review MAJOR-1); calls made: %r"
            % log_text)

    def test_blocked_segment_also_never_pays_the_network_label_lookup(self):
        # MAJOR-1 companion: a segment that already blocks for a DIFFERENT
        # reason (here, a full-authority filer with no Scope-gate line at
        # all) must not pay the network call either -- it never reaches
        # the crit-validity branch either way, but the ordering fix must
        # hold regardless of WHICH other branch ultimately wins.
        call_log = str(Path(self.tmp) / "gh-calls.log")
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"],
                                  call_log=call_log)
        r = run(body_cmd("no scope gate", "missing the scope-gate line entirely"),
                gh_bin=gh_bin, user="gatekeeper")
        self.assertEqual(r.returncode, 2, r.stderr)  # blocks on missing Scope-gate
        log_text = Path(call_log).read_text() if Path(call_log).exists() else ""
        self.assertNotIn("label list", log_text)

    def test_attached_short_flag_own_label_is_recognized(self):
        # MAJOR-2: `-lstream:david2` (no space, no `=`) is genuine, real
        # `gh` syntax (verified live against the real `gh` binary: it
        # parses identically to `-l stream:david2`, distinct from a truly
        # unknown flag like `-zstream:david2` which `gh` itself rejects
        # with "unknown shorthand flag") -- the hook's own label extractor
        # only recognized the spaced/`=` forms and FALSE-BLOCKED this
        # compliant, own-label filing.
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        body = ("Dedup-checked: searched, found none\n"
                "filed for our own work\nScope-gate: cross-cutting\n")
        cmd = ("cat > body.md <<'EOF'\n%s\nEOF\n"
               "gh issue create -lstream:david2 -t 'attached form' -F body.md" % body)
        r = run(cmd, gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_attached_short_flag_with_equals_own_label_is_recognized(self):
        # MAJOR-2 companion: `-l=stream:david2` -- the other real attached
        # form (verified live the same way).
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        body = ("Dedup-checked: searched, found none\n"
                "filed for our own work\nScope-gate: cross-cutting\n")
        cmd = ("cat > body.md <<'EOF'\n%s\nEOF\n"
               "gh issue create -l=stream:david2 -t 'attached equals form' -F body.md" % body)
        r = run(cmd, gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_own_plus_foreign_label_together_needs_no_justification(self):
        # MINOR-1: a documented, intentional residual -- when the filer's
        # OWN label is present ALONGSIDE a foreign one, no
        # Stream-routing: line is required (their own ticket, possibly
        # cross-tagging a second stream too). Pinned here explicitly so a
        # future change to `_stream_routing_block_reason`'s `own_label in
        # applied` check is a deliberate decision, not a silent drift.
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("both labels", "our own work, also relevant to david's module",
                          scope_gate="cross-cutting",
                          labels=["stream:david2", "stream:david"]),
                gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_authority_import_failure_degrades_never_blocks_on_its_own(self):
        # MINOR-2: `_filer_authority_and_own_stream`'s `except Exception:
        # (None, None)` fallback already existed and already worked
        # functionally -- but had ZERO test coverage (a mutant narrowing
        # the guard to `if profile == "full":` survived the whole suite).
        # Run an ISOLATED copy of the hook under a directory with NO
        # sibling airuleset.py, so `import airuleset` genuinely fails --
        # the gate must degrade to "cannot verify a stream identity",
        # never block.
        #
        # `cwd` is ALSO pointed at the isolated dir, not just `repo_dir`
        # (via `hook_path`'s own directory) -- `python3 -` (reading its
        # script from stdin) implicitly prepends "" (the process's OWN
        # CURRENT WORKING DIRECTORY) to `sys.path`, so leaving `cwd` at
        # this checkout's real root would let `import airuleset` silently
        # succeed via THAT fallback regardless of what `repo_dir` says --
        # verified live: the first draft of this test (cwd left at
        # REPO_ROOT) reproduced exactly that false pass. In real
        # production the hook's `cwd` is always the PROJECT being worked
        # (never airuleset's own checkout), so this fallback never rescues
        # it there -- only this test's OWN harness needed the extra
        # isolation to reproduce the same absence.
        isolated_repo = Path(self.tmp) / "isolated-repo"
        isolated_hooks = isolated_repo / "hooks"
        isolated_hooks.mkdir(parents=True)
        isolated_hook = isolated_hooks / "block-ungated-issue-filing.sh"
        isolated_hook.write_text(HOOK.read_text())
        isolated_hook.chmod(0o755)
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run(body_cmd("isolated import failure", "no label at all",
                          scope_gate="cross-cutting"),
                gh_bin=gh_bin, user="david2", hook_path=str(isolated_hook),
                cwd=str(isolated_repo))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestCorpusReplay(TestCase):
    """Bidirectional replay of the real corpus through the REAL shipped hook."""

    def test_all_corpus_bodies_block_with_no_scope_gate(self):
        """Baseline: NONE of the 18 real bodies has a Scope-gate line today
        (the line is new) -- every one of them must BLOCK as-is."""
        failures = []
        for repo_issue, title, body, *_ in LEAK_CORPUS + LEGIT_CORPUS:
            r = run(body_cmd(title, body))
            if r.returncode != 2:
                failures.append((repo_issue, r.returncode, r.stderr[:200]))
        self.assertEqual(
            failures, [],
            "expected ALL 18 real corpus bodies to BLOCK with no Scope-gate "
            "line; these did not: %r" % (failures,))

    def test_leak_cases_stay_blocked_even_with_a_fabricated_criterion(self):
        """#846/#843/#2388: the ticket's own confirmed leaks. Verify the hook
        does not accidentally pass them even when a bare unqualified label is
        attempted -- and that WITHOUT any Scope-gate line (the honest state
        of these three tickets as actually filed) they block, proving the
        hook would have caught all three real violations."""
        for repo_issue, title, body in LEAK_CORPUS:
            with self.subTest(repo_issue):
                r = run(body_cmd(title, body))
                self.assertEqual(
                    r.returncode, 2,
                    "%s should BLOCK as filed (no genuine criterion in body): %s"
                    % (repo_issue, r.stderr[:200]))

    def test_legit_cases_pass_with_one_truthful_scope_gate_line(self):
        """The 15 legitimate real issues: each genuinely states (in its own
        real text) a reason matching one of the follow-up gate's exemptions.
        Adding exactly that ONE truthful line makes each PASS."""
        failures = []
        for repo_issue, title, body, criterion in LEGIT_CORPUS:
            r = run(body_cmd(title, body, scope_gate=criterion))
            if r.returncode != 0:
                failures.append((repo_issue, criterion, r.returncode, r.stderr[:200]))
        self.assertEqual(
            failures, [],
            "expected all 15 legitimate corpus bodies to PASS once their own "
            "truthful Scope-gate line is added; these did not: %r" % (failures,))

    def test_corpus_split_matches_the_ticket_measurement(self):
        """15 of 18 (83%) pass with one truthful line, matching the ticket's
        own stated ~18/25 (72%) order of magnitude for a real sample -- never
        assert exact parity with a DIFFERENT sample, only that the mechanism
        keeps filing possible for the large legitimate majority while still
        catching every confirmed leak."""
        self.assertEqual(len(LEAK_CORPUS), 3)
        self.assertEqual(len(LEGIT_CORPUS), 15)
        total = len(LEAK_CORPUS) + len(LEGIT_CORPUS)
        legit_rate = len(LEGIT_CORPUS) / total
        self.assertGreater(legit_rate, 0.6)


class TestConcreteBlockReason(TestCase):
    """#802 -- every BLOCK must emit a CONCRETE per-item reason; the opaque
    `-> none` (an empty criterion string) is itself a defect.

    Live incident (montalu1, 2026-09-01, odoo-erp title "Provizie cast 2"):
    attempt 3 carried `Scope-gate: user-request` inside a `--body
    "$(printf ...)"` command-substitution, so no real `Scope-gate:` LINE
    existed for CRITERION_RE to match -> crit=None. The missing-Scope-gate
    BLOCK branch then computed `_clean_field(crit)` == "" which the print
    rendered as `-> none`, giving no actionable reason -- the block was
    undiagnosable and the stream had to fall back to the
    `# airuleset:scope-gate-ok` bypass. Root cause verified against the
    shipped hook; the "retry-state" hypothesis was empirically DISPROVEN
    (see test_compliant_retry_after_blocked_attempts_passes below)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-blockreason-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _block_line_from_log(self, home):
        """The single BLOCK log line written to this run's scope-gate.log,
        or "" if none -- used to assert the LOGGED criterion is concrete
        too, not just the stderr per-item summary."""
        log = Path(home) / ".claude" / "scope-gate.log"
        if not log.exists():
            return ""
        for ln in log.read_text(encoding="utf-8").splitlines():
            if "verdict=BLOCK" in ln:
                return ln
        return ""

    def test_resolved_body_with_no_scope_gate_gives_concrete_reason(self):
        # Body IS readable (inline --body) but carries no Scope-gate line;
        # from a stream account whose OWN label is present (so the #390
        # stream gate passes and we reach the missing-Scope-gate branch).
        home = tempfile.mkdtemp(prefix="airuleset-blockreason-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run("gh issue create -t Provizie -l stream:david2 "
                "--body 'plain finding, no criterion line here'",
                home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("-> none", r.stderr)
        self.assertIn("no-scope-gate", r.stderr)
        self.assertNotIn("criterion=none", self._block_line_from_log(home))

    def test_unresolvable_body_gives_concrete_reason_not_none(self):
        # No -F / --body at all -> resolve_body returns (None, None): the
        # exact (body is None AND body_err is None) hole that rendered the
        # opaque `-> none`. Reason must name that the body was unresolvable.
        home = tempfile.mkdtemp(prefix="airuleset-blockreason-home2-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run("gh issue create -t Provizie -l stream:david2",
                home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("-> none", r.stderr)
        self.assertIn("body-unresolved", r.stderr)
        self.assertNotIn("criterion=none", self._block_line_from_log(home))

    def test_command_substitution_body_is_the_montalu1_shape(self):
        # The literal montalu1 attempt-3 shape: the Scope-gate line rides
        # inside a `--body "$(printf ...)"` command-substitution token, so no
        # newline-anchored `Scope-gate:` LINE exists for CRITERION_RE. It
        # must still BLOCK (correct -- the gate cannot read a runtime
        # substitution) but with a CONCRETE reason, never `-> none`.
        home = tempfile.mkdtemp(prefix="airuleset-blockreason-home3-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        r = run("gh issue create -t Provizie -l stream:david2 "
                "--body \"$(printf 'Scope-gate: user-request')\"",
                home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("-> none", r.stderr)
        self.assertNotIn("criterion=none", self._block_line_from_log(home))

    def test_compliant_retry_after_blocked_attempts_passes(self):
        # #802 requirement 2 (validator "retry-state" hypothesis): a
        # compliant filing must NOT be blocked by the SESSION's own earlier
        # BLOCKED attempts. Three attempts share ONE home (== one
        # scope-gate.log): (1) missing Scope-gate BLOCKS, (2) an unreadable
        # -F BLOCKS, (3) a fully-compliant heredoc filing of the SAME title
        # PASSES. Prior BLOCK/NOTFILED lines never charge the daily/width
        # caps (they count verdict=PASS only) and never manufacture a
        # near-duplicate (that check reads open issues, never the log).
        home = tempfile.mkdtemp(prefix="airuleset-blockreason-retry-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])
        a1 = run("gh issue create -t 'Provizie cast 2' -l stream:david2 "
                 "--body 'plain finding no criterion'",
                 home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(a1.returncode, 2, a1.stderr)
        a2 = run("gh issue create -t 'Provizie cast 2' -l stream:david2 -F body.md",
                 home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(a2.returncode, 2, a2.stderr)
        a3 = run(body_cmd("Provizie cast 2",
                          "Stream-routing: belongs to us\nreal work",
                          scope_gate="user-request", labels=["stream:david2"]),
                 home=home, gh_bin=gh_bin, user="david2")
        self.assertEqual(a3.returncode, 0, a3.stderr)


class TestBlockReasonHardening(TestCase):
    """#802 adversarial-review hardening (2x fresh-context review of the
    GREEN fix): (1) a whitespace-only title must not empty its field and
    shift the tab hand-off; (2) an attacker-influenced invalid-criterion
    value must not carry a `<countingfield>=` decoy token into the
    free-text `criterion=` field; (3) C0/DEL control bytes (ESC) that `\\s`
    does not cover must be stripped before reaching stderr / the log."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-blockhardening-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gh_bin = _fake_gh_stream(self.tmp, labels=["stream:david", "stream:david2"])

    def _block_line(self, home):
        log = Path(home) / ".claude" / "scope-gate.log"
        if not log.exists():
            return ""
        for ln in log.read_text(encoding="utf-8").splitlines():
            if "verdict=BLOCK" in ln:
                return ln
        return ""

    def test_whitespace_only_title_does_not_empty_the_field(self):
        home = tempfile.mkdtemp(prefix="airuleset-blockhardening-h1-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = run("gh issue create -t '   ' -l stream:david2 --body 'no criterion'",
                home=home, gh_bin=self.gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        line = self._block_line(home)
        # A whitespace-only title cleans to "" without the guard -> the log
        # would carry title="" and bash's IFS-tab read would collapse it; the
        # guard pins "(no title)". The real trailing fields must stay intact.
        self.assertIn('title="(no title)"', line)
        self.assertIn("session=test-scope-gate", line)

    def test_invalid_criterion_decoy_token_is_neutralized(self):
        home = tempfile.mkdtemp(prefix="airuleset-blockhardening-h2-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        # An invalid Scope-gate value crafted to spell a `parents=999` decoy;
        # the surfaced free-text criterion must NOT let a `\bparents=` first
        # match find the forged value ahead of the real `parents=none` field.
        r = run("gh issue create -t decoy -l stream:david2 "
                "--body 'Scope-gate: x-parents=999'",
                home=home, gh_bin=self.gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        line = self._block_line(home)
        self.assertNotIn("parents=999", line)          # decoy neutralized
        self.assertIn("invalid-scope-gate:x-parents:999", line)
        self.assertIn("parents=none", line)            # real field intact

    def test_control_bytes_in_title_are_stripped(self):
        home = tempfile.mkdtemp(prefix="airuleset-blockhardening-h3-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        # A raw ESC (\x1b) is not whitespace, so \s+ leaves it -- it must be
        # stripped before reaching the user's terminal (escape injection).
        r = run("gh issue create -t '\x1b]0;pwn\x07X' -l stream:david2 "
                "--body 'no criterion'",
                home=home, gh_bin=self.gh_bin, user="david2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("\x1b", r.stderr)
        self.assertNotIn("\x1b", self._block_line(home))


def _fake_gh_netdrain(tmpdir, created, closed, open_issues=(), labels=None):
    """#842 fake `gh` for the net-drain ratchet tests. Answers:
      - `issue list --search "created:>=…" … -q length` -> `created`
      - `issue list --search "closed:>=…"  … -q length` -> `closed`
      - `issue list --state open … --json number,title` (near-dup) -> open_issues JSON
      - `label list …` -> labels rows (None -> exit 1, so not stream-aware)
    `created`/`closed` = None makes the matching count query exit 1 (a gh error,
    so the ratchet BLOCKS fail-safe)."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    label_rows = None if labels is None else [{"name": n} for n in labels]
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "joined = ' '.join(argv)\n"
        "created = %r\n"
        "closed = %r\n"
        "open_issues = %r\n"
        "label_rows = %r\n"
        "if len(argv) >= 2 and argv[0] == 'label' and argv[1] == 'list':\n"
        "    if label_rows is None:\n"
        "        sys.exit(1)\n"
        "    print(json.dumps(label_rows)); sys.exit(0)\n"
        "if len(argv) >= 2 and argv[0] == 'issue' and argv[1] == 'list':\n"
        "    if 'created:' in joined:\n"
        "        if created is None: sys.exit(1)\n"
        "        print(created); sys.exit(0)\n"
        "    if 'closed:' in joined:\n"
        "        if closed is None: sys.exit(1)\n"
        "        print(closed); sys.exit(0)\n"
        "    print(json.dumps(list(open_issues))); sys.exit(0)\n"
        "sys.exit(1)\n" % (created, closed, list(open_issues), label_rows))
    (bin_dir / "gh").chmod(0o755)
    return str(bin_dir)


class TestNetDrainHarness842(TestCase):
    """#842 — the worker hard-block, presence-gated user-request/planned-work,
    dismissal-word block, and the per-repo net-drain ratchet. All the new gates
    engage ONLY on the UNATTENDED path (a stale presence marker) or the SUBAGENT
    path (agent_id); the ATTENDED path is unchanged."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-netdrain-test-")
        self.home = tempfile.mkdtemp(prefix="airuleset-netdrain-home-")

    def _away_sid(self):
        """A unique session id whose presence marker is stale (> 900s) -> the
        hook reads the session as UNATTENDED."""
        sid = "t-nd-" + uuid.uuid4().hex[:10]
        mark = Path("/tmp/claude-user-active-%s" % sid)
        mark.write_text("")
        old = time.time() - 1000
        os.utime(mark, (old, old))
        self.addCleanup(lambda: mark.unlink(missing_ok=True))
        return sid

    # ---- req 1: worker (subagent) cannot file ----
    def test_worker_subagent_cannot_file_even_a_valid_issue(self):
        r = run(body_cmd("worker finding", "genuinely out of scope work",
                          scope_gate="security-boundary"),
                gh_bin=_default_gh_stub(), agent_id="sub-worker-1")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("followup_candidates", r.stderr)

    def test_worker_block_ignores_the_scope_gate_ok_bypass(self):
        # The worker block sits ABOVE the bypass, so a worker cannot self-exempt.
        r = run(body_cmd("worker finding", "out of scope\n# airuleset:scope-gate-ok worker",
                          scope_gate="security-boundary"),
                gh_bin=_default_gh_stub(), agent_id="sub-worker-2")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_worker_api_issues_post_also_blocked(self):
        r = run("gh api repos/o/r/issues -X POST -f title=x -f body=y",
                gh_bin=_default_gh_stub(), agent_id="sub-worker-3")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_main_session_non_filing_command_untouched_for_worker(self):
        # A worker running a NON-filing command is not gated by this hook.
        r = run("echo hello", gh_bin=_default_gh_stub(), agent_id="sub-worker-4")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---- req 3: presence-gated user-request / planned-work ----
    def test_unattended_user_request_is_blocked(self):
        r = run(body_cmd("loop wants this", "an unattended loop cannot claim the owner asked",
                          scope_gate="user-request"),
                gh_bin=_default_gh_stub(), session_id=self._away_sid())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("presence", r.stderr.lower())

    def test_unattended_planned_work_is_blocked(self):
        r = run(body_cmd("plan step", "converged-plan decomposition",
                          scope_gate="planned-work"),
                gh_bin=_default_gh_stub(), session_id=self._away_sid())
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_attended_user_request_still_passes(self):
        # PRESENT (no stale marker) -> user-request is accepted, unchanged.
        r = run(body_cmd("owner asked", "the owner asked for this ticket",
                          scope_gate="user-request"),
                gh_bin=_default_gh_stub(), session_id="t-nd-present-" + uuid.uuid4().hex[:6])
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---- req 4: dismissal-word block for an unattended filing ----
    def test_unattended_flaky_body_word_is_blocked(self):
        r = run(body_cmd("flaky test", "this test is flaky, skip it for now",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=0, closed=9),
                session_id=self._away_sid())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dismissal", r.stderr.lower())

    def test_unattended_pre_existing_body_word_is_blocked(self):
        r = run(body_cmd("known break", "this is a pre-existing failure",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=0, closed=9),
                session_id=self._away_sid())
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_attended_flaky_body_word_is_not_blocked_by_this_gate(self):
        # The dismissal-word gate engages only on the UNATTENDED path.
        r = run(body_cmd("flaky note", "mentions the word flaky in prose",
                          scope_gate="security-boundary"),
                gh_bin=_default_gh_stub(),
                session_id="t-nd-present-" + uuid.uuid4().hex[:6])
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---- req 2: net-drain ratchet ----
    def test_unattended_discovery_blocked_when_not_draining(self):
        # created(9) >= closed(5) -> repo is NOT draining -> BLOCK.
        r = run(body_cmd("net drain", "genuinely out of scope",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=9, closed=5),
                session_id=self._away_sid(), home=self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("drain", r.stderr.lower())

    def test_unattended_discovery_blocked_at_parity(self):
        # created == closed -> parity blocks (0/0 too, the day's first).
        r = run(body_cmd("parity", "genuinely out of scope",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=5, closed=5),
                session_id=self._away_sid(), home=self.home)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unattended_discovery_passes_when_draining(self):
        # created(3) < closed(9) -> repo IS draining -> the ratchet allows.
        r = run(body_cmd("draining ok", "genuinely out of scope",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=3, closed=9),
                session_id=self._away_sid(), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ratchet_gh_error_blocks_fail_safe(self):
        # A gh error computing the counts -> BLOCK, never a wrong ALLOW.
        r = run(body_cmd("gh error", "genuinely out of scope",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=None, closed=None),
                session_id=self._away_sid(), home=self.home)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_attended_discovery_never_ratchet_blocked(self):
        # PRESENT session -> the ratchet never engages, even when NOT draining.
        r = run(body_cmd("attended ok", "genuinely out of scope",
                          scope_gate="security-boundary"),
                gh_bin=_fake_gh_netdrain(self.tmp, created=99, closed=0),
                session_id="t-nd-present-" + uuid.uuid4().hex[:6], home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
