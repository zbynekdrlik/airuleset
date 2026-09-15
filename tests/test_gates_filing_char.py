"""#1020 Part 2 — CHARACTERIZATION pins for hooks/block-ungated-issue-filing.sh.

Committed as `[char]` BEFORE the classifier is migrated out of the bash heredoc
into the `gates/filing` package, so the migration is proven behaviour-preserving
at the observable boundary: for each canonical block reason, the (exit code,
first stderr line, per-item reason token) the SHIPPED hook produces today must be
byte-identical after the migration. The exhaustive behaviour contract stays
`test_scope_gate.py` (the ~120-test end-to-end oracle); this file is the tight,
fast, reason-by-reason pin the migration commit is diffed against.

Every EXPECTED value here was captured by running the CURRENT (pre-migration)
hook — it is a snapshot of today's behaviour, not a fresh assertion of what is
"right"; a divergence after the migration is a regression to investigate, never
a fixture to loosen.
"""

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-ungated-issue-filing.sh"


def _empty_gh(tmpdir):
    """A `gh` stub: `issue list` -> empty JSON (near-dup inert), everything else
    exits 1. The default PATH `gh` for every scenario, so no test reaches live
    GitHub."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "a = sys.argv[1:]\n"
        "if len(a) >= 2 and a[0] == 'issue' and a[1] == 'list':\n"
        "    print(json.dumps([])); sys.exit(0)\n"
        "sys.exit(1)\n")
    gh.chmod(0o755)
    return str(bin_dir)


def _run(cmd, home, gh_bin, session_id="char-sid", agent_id=None, cwd=None):
    payload = {"tool_input": {"command": cmd}, "session_id": session_id}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = gh_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env,
                          cwd=cwd or str(REPO))


def _first_stderr_line(r):
    for ln in r.stderr.splitlines():
        if ln.strip():
            return ln
    return ""


def _filing(title, body, scope_gate=None, dedup="searched, none", labels=None):
    b = body
    if dedup:
        b = "Dedup-checked: %s\n%s" % (dedup, b)
    if scope_gate:
        b = b.rstrip("\n") + "\nScope-gate: %s\n" % scope_gate
    lbl = "".join(" -l %s" % x for x in (labels or []))
    return "cat > body.md <<'EOF'\n%s\nEOF\ngh issue create%s -t %r -F body.md" % (
        b, lbl, title)


# Canonical (first stderr line, per-item reason token) pins captured from the
# SHIPPED hook. Two first-line shapes exist by design: the worker hard-block
# prints its own message first; every classifier block prints the shared
# `🚫 BLOCKED — per-item reason:` header, with the discriminating reason on the
# SUMMARY line just below (asserted separately via reason_token).
_BLOCKED_HEADER = "🚫 BLOCKED — per-item reason:"
_WORKER_FIRST = ("BLOCKED: you are a worktree WORKER (subagent) — you may NOT "
                 "`gh issue create`")


class TestFilingBlockCharacterization(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-filing-char-")
        self.home = tempfile.mkdtemp(prefix="airuleset-filing-char-home-")
        self.gh = _empty_gh(self.tmp)
        # #1020 fix-forward (run 34920539429): every scenario relocates HOME to
        # `self.home`, and the classifier resolves its FALLBACK target repo from
        # the invoking cwd's `origin` via `git -C <repo> remote get-url origin`
        # (gates.filing.caps.cwd_repo_of). Without a seeded `.gitconfig`, that
        # git call dies `fatal: detected dubious ownership` whenever the checkout
        # is owned by a DIFFERENT uid than the test process -- exactly the CI
        # `python:3.12` container (uid 0) vs a runner-owned checkout, where
        # cwd_repo_of then silently fell back to the basename `airuleset` (not
        # `zbynekdrlik/airuleset`), the daily-cap seed lines went uncounted and
        # test_daily_cap failed `0 != 2`. Seed the same two `safe.directory`
        # entries CI sets (mirrors cli_remote._runner_shape_env / the #1012
        # Pass-B test_env) so cwd_repo_of resolves the real slug in ANY ownership
        # situation. Proven hermetic by running this class with
        # GIT_CONFIG_GLOBAL=/dev/null (it stays green).
        Path(self.home, ".gitconfig").write_text(
            "[safe]\n\tdirectory = %s\n\tdirectory = %s\n" % (REPO, REPO / ".git"))

    def _away_sid(self):
        sid = "char-away-" + uuid.uuid4().hex[:8]
        mark = Path("/tmp/claude-user-active-%s" % sid)
        mark.write_text("")
        old = time.time() - 1000
        os.utime(mark, (old, old))
        self.addCleanup(lambda: mark.unlink(missing_ok=True))
        return sid

    def _assert(self, r, first_line, reason_token):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(_first_stderr_line(r), first_line, r.stderr)
        self.assertIn(reason_token, r.stderr)

    # 1. worker (subagent) hard-block
    def test_worker_block(self):
        r = _run(_filing("w", "x", scope_gate="security-boundary"),
                 self.home, self.gh, agent_id="sub-1")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(_first_stderr_line(r), _WORKER_FIRST, r.stderr)
        self.assertIn("followup_candidates", r.stderr)

    # 2. no Scope-gate line
    def test_no_scope_gate(self):
        r = _run(_filing("t", "just prose, no gate"), self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "no-scope-gate")

    # 3. invalid Scope-gate criterion
    def test_invalid_scope_gate(self):
        r = _run(_filing("t", "prose", scope_gate="bogus-crit"), self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "invalid-scope-gate:bogus-crit")

    # 4. missing Dedup-checked line
    def test_no_dedup_line(self):
        r = _run(_filing("t", "prose", scope_gate="cross-cutting", dedup=None),
                 self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "no-dedup-line")

    # 5. >300-loc self-contradiction
    def test_loc_mismatch(self):
        r = _run(_filing("t", "this is only 50 lines of change",
                          scope_gate=">300-loc"), self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "loc-mismatch")

    # 6. unresolvable body (gh api POST with no body= field)
    def test_body_unresolved(self):
        r = _run("gh api repos/o/r/issues -X POST -f title=t", self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "body-unresolved")

    # 7. architecture-rework missing Area: line
    def test_arch_rework_missing_area(self):
        r = _run(_filing("t", "a rework", scope_gate="architecture-rework"),
                 self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "architecture-rework-missing-area")

    # 8. architecture-rework missing -l architecture-rework label
    def test_arch_rework_missing_label(self):
        r = _run(_filing("t", "a rework\nArea: gate-family",
                         scope_gate="architecture-rework"), self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "architecture-rework-missing-label")

    # 9. daily cap (9th non-exempt filing today) — seed 8 PASS lines
    def test_daily_cap(self):
        log = Path(self.home) / ".claude" / "scope-gate.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        # target repo is resolved from the cwd's origin remote (no -R here);
        # the checkout's remote is zbynekdrlik/airuleset, so the seeded PASS
        # lines must be logged under that exact repo to be counted. This
        # resolution needs git to TRUST the (relocated-HOME) checkout -- the
        # setUp `.gitconfig` safe.directory seed is what makes cwd_repo_of
        # return the real slug even in the CI uid-0 container (#1020 fix-forward,
        # run 34920539429); without it the target silently degraded to the
        # basename `airuleset`, the count stayed 0 and this assert failed.
        with log.open("w") as fh:
            for i in range(8):
                fh.write("%sT10:00:00+02:00  verdict=PASS  repo=zbynekdrlik/airuleset  "
                         "criterion=cross-cutting  session=s  parents=none  "
                         'title="x%d"  dedup="d"\n' % (today, i))
        r = _run(_filing("ninth", "prose", scope_gate="cross-cutting"),
                 self.home, self.gh)
        self._assert(r, _BLOCKED_HEADER, "daily-cap")

    # 10. unattended presence-required (user-request from an away session)
    def test_presence_required(self):
        r = _run(_filing("t", "owner wanted this", scope_gate="user-request"),
                 self.home, self.gh, session_id=self._away_sid())
        self._assert(r, _BLOCKED_HEADER, "presence-required")

    # 11. unattended dismissal-word
    def test_dismissal_word(self):
        r = _run(_filing("t", "this test is flaky, skip", scope_gate="security-boundary"),
                 self.home, self.gh, session_id=self._away_sid())
        self._assert(r, _BLOCKED_HEADER, "dismissal-word")

    # 12. #1020 fix-forward: cwd_repo_of's fail-OPEN is VISIBLE. When the
    # invoking cwd's `git remote get-url origin` fails, the caps' target repo
    # degrades to the cwd basename -- a fail-open for EVERY cap (a repo git
    # cannot read never reaches its caps). That degradation used to be SILENT,
    # so the exact CI failure (a wrong-repo resolution letting the daily cap
    # slip) had no trace. This pins the honest behaviour: the filing still
    # PASSES (count under the wrong basename repo is 0 -- fail-open, rc 0), but
    # a journal line NOW names the unresolvable-cwd + basename fallback on
    # stderr. Ownership cannot be faked locally, so this reproduces the SAME
    # fallback deterministically via a fresh `git init` cwd with no `origin`.
    def test_cwd_repo_unresolvable_emits_journal_line(self):
        reddir = tempfile.mkdtemp(prefix="airuleset-filing-char-nogit-")
        self.addCleanup(lambda: __import__("shutil").rmtree(reddir, ignore_errors=True))
        # A real git repo, but with NO `origin` remote -> `git remote get-url
        # origin` exits non-zero ("No such remote 'origin'"), the fallback path.
        subprocess.run(["git", "init", "-q", reddir], check=True,
                       capture_output=True, text=True)
        # Seed 8 PASS lines under the REAL slug, exactly like test_daily_cap:
        # on CI (dubious-ownership fallback) the target became the basename, so
        # these went uncounted and the cap silently did not fire -- this test
        # makes that silent degradation observable.
        log = Path(self.home) / ".claude" / "scope-gate.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        with log.open("w") as fh:
            for i in range(8):
                fh.write("%sT10:00:00+02:00  verdict=PASS  repo=zbynekdrlik/airuleset  "
                         "criterion=cross-cutting  session=s  parents=none  "
                         'title="x%d"  dedup="d"\n' % (today, i))
        r = _run(_filing("ninth-unresolvable-cwd", "prose", scope_gate="cross-cutting"),
                 self.home, self.gh, cwd=reddir)
        # Honest fail-open behaviour: the wrong (basename) repo has 0 counted
        # PASS lines, so the filing is ALLOWED (rc 0), NOT blocked by the cap.
        self.assertEqual(r.returncode, 0, r.stderr)
        # ...but the degradation is no longer silent.
        self.assertIn("filing: target repo unresolvable from cwd", r.stderr)
        self.assertIn("basename fallback", r.stderr)
        self.assertIn(os.path.basename(reddir), r.stderr)


if __name__ == "__main__":
    main()
