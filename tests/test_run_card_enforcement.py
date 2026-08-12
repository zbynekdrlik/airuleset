"""#134 — the per-ticket completion card can no longer be silently skipped.

Five days, ~85 merged PRs, ~103 closed issues, and not one completion report
on the user's phone. Nothing was broken: the card is an ACTION WITH NO
ARTIFACT ANYONE CHECKS. The mandate lives in `agents/autopilot-worker.md`
(which does reach the worker — it IS its system prompt) but the
full-authority evidence block has no card field at all, so the supervisor
"re-verifies every line" of a block that never had a line to verify.
Measured on the real corpus: 2 of 339 worker evidence blocks mention
`cards_fired`.

Three mechanisms are locked here, plus the parser facts that only a corpus
read gives you.

  1. GATE (`hooks/subagent-stop-check-run-card.sh`, SubagentStop) — a worker
     that claims a real merge AND closed issues, with no DELIVERED marker for
     one of them, is blocked once and told to fire the card. Bounded by
     construction: at most one block per (session, repo#issue), so a worker
     that genuinely cannot deliver still finishes.
  2. BACKSTOP (watchdog job 25) — the gate cannot see a worker that DIED
     mid-run (it never reaches SubagentStop) or a delivery that failed after
     it returned. Job 25 reads local git: merges that landed on the base
     branch and the `Closes #N` they carry, against the markers.
  3. SUPPRESSION (`notify-discord-pending.sh`) — the `✅` is dropped only
     when a card was actually DELIVERED for this repo since the previous `✅`
     boundary. Never merely because a `/goal` is armed. That premise is the
     design error the ticket names: marek's pane has had a goal armed for 18h+
     across 7 re-arms with zero `Goal cleared:`, so the suppression was total
     rather than occasional, and it deferred to something with no guarantee of
     happening.

Parser facts, taken from the 339 real evidence blocks rather than from the
template (both would be invisible to a test written against the docs):

  * `merge_sha:` is frequently NOT a sha — `NOT MERGED — dispatch = ...`,
    `STOPPED: DYNAX eshop in maintenance ...`. Requiring hex is what keeps
    the gate off a worker that correctly did not merge.
  * `issue_state:` carries `#6=closed`, `#7=closed, #8=closed`,
    `#10 = CLOSED (via PR #11 merge)`, `#183=OPEN (auto-closes on merge)`,
    and the trap `#109=closed (auto-closed by Closes #109 at 15:18:14Z on
    the #133 merge)` — where a naive "any #N near the word closed" claims
    #133, an issue that was never closed at all.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add this file's own directory so sibling test-support modules import as
# top-level names (mirrors test_ask_before_assuming_dropped_rows.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify                                            # noqa: E402
import watchdog as wd                                    # noqa: E402
from _hook_state_cleanup import sweep_session_files       # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "hooks" / "subagent-stop-check-run-card.sh"
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"

DAY = 86400
NOW = 1785200000.0

# Real 40-char merge shas from the corpus, spelled as two halves so the
# source carries no 40-char hex RUN. `block-sensitive-staging.sh` cannot
# tell a git sha from a leaked key and is right not to try; the repo's own
# rule is to break the literal, never to reach for the bypass marker.

SHA40_014e = ("014e9159ade4c55fb02a"
              "5eca823771bee26da677")
SHA40_226a = ("226ab46446196f758d14"
              "9b9bc5baa57a58c30a8d")
SHA40_3c09 = ("3c094683b7ad770ee5e6"
              "5d50279809c3c6b242ce")
SHA40_7f82 = ("7f821cbf05672e2199c6"
              "59500097be11f5721b4d")
SHA40_8153 = ("8153549c5fa670a58555"
              "b6d50d3ddd6963379ba2")
SHA40_edee = ("edee6619fb4b604f0a44"
              "99546cb5ba22908acc11")


class _Args(types.SimpleNamespace):
    """A stand-in for argparse's Namespace — deliberately NOT `Mock`, which
    auto-creates every attribute as a truthy Mock and so silently arms
    unrelated `--flag` branches in a `cmd_*` dispatcher (this repo's own
    documented trap)."""


def _git(repo, *args, ts=None):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if ts is not None:
        stamp = "%d +0000" % int(ts)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


# --------------------------------------------------------------------------- #
# the evidence-block parser
# --------------------------------------------------------------------------- #

class TestWorkerEvidenceParser(unittest.TestCase):
    """Every specimen below is VERBATIM from the real corpus (339 evidence
    blocks extracted from 5,180 subagent transcripts under
    `~/.claude/projects/**`)."""

    def p(self, text):
        return notify.parse_worker_evidence(text)

    def test_a_plain_merge_and_one_closed_issue(self):
        r = self.p("merge_sha: " + SHA40_014e + "\n"
                   "issue_state: #106=closed")
        self.assertTrue(r["merged"])
        self.assertEqual(r["closed"], [106])

    def test_two_closed_issues_on_one_line(self):
        r = self.p("merge_sha: " + SHA40_edee + "\n"
                   "issue_state: #7=closed, #8=closed (both auto-closed by the merge)")
        self.assertEqual(r["closed"], [7, 8])

    def test_spaces_around_the_equals_and_uppercase_state(self):
        r = self.p("merge_sha: " + SHA40_3c09 + "\n"
                   "issue_state: #10 = CLOSED (via PR #11 merge)")
        self.assertEqual(r["closed"], [10],
                         "#11 is the PR, not a closed issue")

    def test_the_trap_a_hash_inside_the_parenthetical_is_not_claimed(self):
        # verbatim corpus line — a naive scan claims #133, which was never
        # closed by this worker at all.
        r = self.p("merge_sha: " + SHA40_226a + "\n"
                   "issue_state: #109=closed (auto-closed by Closes #109 at "
                   "15:18:14Z on the #133 merge)")
        self.assertEqual(r["closed"], [109])

    def test_open_issues_are_never_claimed(self):
        r = self.p("merge_sha: " + SHA40_7f82 + "\n"
                   "issue_state: #183=OPEN (auto-closes on merge), "
                   "#185=OPEN (auto-closes on merge)")
        self.assertEqual(r["closed"], [])

    def test_not_merged_is_not_a_merge(self):
        r = self.p("merge_sha: NOT MERGED — dispatch = STOP-at-green-PR "
                   "(autorita FULL, ale supervízor reviewne + merguje)\n"
                   "issue_state: #60 = OPEN (Closes #60 v PR body)")
        self.assertFalse(r["merged"])

    def test_stopped_is_not_a_merge(self):
        r = self.p("merge_sha: STOPPED: DYNAX eshop in maintenance (HTTP 503) "
                   "— hard blocker\nissue_state: #76=OPEN")
        self.assertFalse(r["merged"])

    def test_a_sha_with_a_trailing_parenthetical_still_reads_as_merged(self):
        r = self.p("merge_sha: " + SHA40_7f82 + "  "
                   "(merge commit; auto-merge, no manual marker)\n"
                   "issue_state: #14=closed")
        self.assertTrue(r["merged"])

    def test_a_fenced_block_parses_the_same(self):
        r = self.p("```\nissues: #5 t\n"
                   "merge_sha: " + SHA40_8153 + "\n"
                   "issue_state: #5=closed\n```")
        self.assertTrue(r["merged"])
        self.assertEqual(r["closed"], [5])

    def test_a_batch_that_lists_one_sha_PER_ISSUE_is_a_merge(self):
        # Found by the corpus replay, not by reading the template: 6 real
        # merges write the shas per issue instead of one merge commit, and
        # the first rule (sha must START the value) missed every one.
        r = self.p("merge_sha: #133 = 2223f12 (feature, v0.51.0) ; "
                   "#134 = 4d56813 (docs, v0.52.0)\n"
                   "issue_state: #133=closed, #134=closed")
        self.assertTrue(r["merged"])
        self.assertEqual(r["closed"], [133, 134])

    def test_a_batch_with_red_green_shas_per_issue_is_a_merge(self):
        r = self.p("merge_sha: #82 test:3c280b2[red]→fix:246dc57[green]; "
                   "#81 test:0b34762[red]→feat:6a733ce[green]\n"
                   "issue_state: #82=closed")
        self.assertTrue(r["merged"])

    def test_a_refusal_that_merely_MENTIONS_issue_numbers_is_not_a_merge(self):
        # The shape the widening had to stay off: a NOT-MERGED value dense
        # with issue references, none of them carrying a sha.
        r = self.p("merge_sha: NOT MERGED — required Full-path E2E gate still "
                   "red on unrelated pre-existing blockers "
                   "(#707/#689/#726/#741)\nissue_state: #740=closed")
        self.assertFalse(r["merged"])

    def test_a_message_with_neither_field_is_inert(self):
        r = self.p("⏳ WORKING: monitoring CI run 123")
        self.assertFalse(r["merged"])
        self.assertEqual(r["closed"], [])

    def test_closes_elsewhere_in_the_block_is_not_an_issue_state_claim(self):
        r = self.p("pr: #12 https://example.invalid (body Closes #99)\n"
                   "merge_sha: " + SHA40_014e + "\n"
                   "issue_state: #12=closed")
        self.assertEqual(r["closed"], [12], "#99 is outside issue_state")


# --------------------------------------------------------------------------- #
# 1. the GATE
# --------------------------------------------------------------------------- #

class _GateBase(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-gate-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-gate-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/parovanie-produktov.git")
        self.sid = "gate-%d" % os.getpid()
        self.addCleanup(lambda: [
            os.remove(f) for f in
            Path("/tmp").glob("airuleset-runcard-gate-gate-*")
            if os.path.exists(f)])

    def mark(self, key, status="sent"):
        d = self.home / ".claude" / "autopilot-notify-sent"
        d.mkdir(parents=True, exist_ok=True)
        (d / key).write_text("%s %s" % (NOW, status))

    def run_gate(self, msg, agent_type="autopilot-worker", cwd=None, sid=None):
        payload = {"session_id": sid or self.sid, "agent_id": "aG1",
                   "hook_event_name": "SubagentStop", "agent_type": agent_type,
                   "cwd": str(self.repo if cwd is None else cwd),
                   "last_assistant_message": msg}
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def blocked(self, r):
        """The repo's block contract, exactly: `{"decision":"block"}` on
        stdout with exit 0 (`stop-check-playbook-review.sh`,
        `subagent-stop-check-bg-work.sh`).

        Deliberately NOT "any non-zero exit": during the RED run the hook file
        does not exist yet, `bash <missing>` exits 127, and a looser helper
        would report three gate tests as PASSING before a line of the gate was
        written — red for the wrong reason is the same failure as green for
        the wrong reason."""
        if r.returncode != 0:
            return False
        try:
            return json.loads(r.stdout or "{}").get("decision") == "block"
        except ValueError:
            return False


MERGED = ("issues: #41 x\nmerge_sha: " + SHA40_014e + "\n"
          "issue_state: #41=closed")


class TestGateBlocksAMissingCard(_GateBase):

    def test_a_merged_ticket_with_no_marker_is_blocked(self):
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.returncode, r.stdout, r.stderr))
        self.assertIn("41", r.stdout + r.stderr)
        self.assertIn("run-card", r.stdout + r.stderr)

    def test_a_delivered_marker_lets_the_worker_stop(self):
        self.mark("parovanie-produktov#41", "sent")
        r = self.run_gate(MERGED)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_a_claimed_but_undelivered_marker_is_still_a_missing_card(self):
        # the exact #135 hole: the marker is written BEFORE the POST, so its
        # presence is not evidence the user ever saw anything.
        self.mark("parovanie-produktov#41", "error")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r))

    def test_the_repo_name_comes_from_the_REMOTE_not_the_directory(self):
        # the live trap: the checkout is `.../parovanie_produktov` (underscore)
        # while the marker key is `parovanie-produktov` (hyphen), because
        # `_notify_run_card` keys on the --repo argument.
        self.mark(self.repo.name + "#41", "sent")     # directory-named marker
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r),
                        "a marker keyed on the DIRECTORY name must not count")

    def test_only_one_block_per_session_and_issue(self):
        first = self.run_gate(MERGED)
        self.assertTrue(self.blocked(first))
        second = self.run_gate(MERGED)
        self.assertFalse(self.blocked(second),
                         "a second block on the same issue would wedge a "
                         "worker that genuinely cannot deliver")

    def test_a_different_issue_in_the_same_session_still_blocks(self):
        self.run_gate(MERGED)
        other = MERGED.replace("#41", "#42")
        self.assertTrue(self.blocked(self.run_gate(other)))


class TestRepoResolvedFromPrLine(_GateBase):
    """#220 -- a worker dispatched with session cwd = repo A (self.repo,
    origin parovanie-produktov), working repo B (dantesync), must have its
    delivered-card check (and the gate's own printed --repo example) resolve
    against repo B's markers -- the evidence block's own `pr: #<N> <url>`
    line names the real repo, over the payload's static cwd."""

    PR_MSG = ("issues: #61 x\nmerge_sha: " + SHA40_014e + "\n"
              "pr: #63 https://github.com/zbynekdrlik/dantesync/pull/63\n"
              "issue_state: #61=closed")

    def test_a_delivered_card_under_the_pr_line_repo_lets_the_worker_stop(self):
        self.mark("dantesync#61", "sent")
        r = self.run_gate(self.PR_MSG)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_a_card_only_under_the_stale_session_cwd_repo_still_blocks(self):
        # OLD cwd-only resolution would have accepted this (repo
        # "parovanie-produktov" matches self.repo's remote) -- the fix must
        # not, since the PR line says the real repo is dantesync.
        self.mark("parovanie-produktov#61", "sent")
        r = self.run_gate(self.PR_MSG)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        self.assertIn("dantesync", r.stdout + r.stderr)

    def test_no_pr_line_still_resolves_from_cwd_unchanged(self):
        self.mark("parovanie-produktov#41", "sent")
        r = self.run_gate(MERGED)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))


class TestGateStaysOutOfTheWay(_GateBase):

    def test_a_non_worker_subagent_is_ignored(self):
        r = self.run_gate(MERGED, agent_type="general-purpose")
        self.assertFalse(self.blocked(r))

    def test_an_unmerged_worker_is_ignored(self):
        r = self.run_gate("merge_sha: NOT MERGED — supervisor merges\n"
                          "issue_state: #41=OPEN")
        self.assertFalse(self.blocked(r))

    def test_a_worker_that_closed_nothing_is_ignored(self):
        r = self.run_gate("merge_sha: " + SHA40_014e + "\n"
                          "issue_state: #41=OPEN (auto-closes on merge)")
        self.assertFalse(self.blocked(r))

    def test_a_cwd_that_is_not_a_git_repo_is_ignored(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-gate-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        r = self.run_gate(MERGED, cwd=d)
        self.assertFalse(self.blocked(r),
                         "unmeasurable is never a block (never guess)")

    def test_garbage_stdin_is_ignored(self):
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(GATE)], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))

    def test_empty_stdin_is_ignored(self):
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(GATE)], input="",
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))

    def test_a_null_last_assistant_message_is_ignored(self):
        payload = {"session_id": self.sid, "agent_type": "autopilot-worker",
                   "cwd": str(self.repo), "last_assistant_message": None}
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))

    def test_an_allowed_stop_leaves_stderr_empty(self):
        self.mark("parovanie-produktov#41", "sent")
        r = self.run_gate(MERGED)
        self.assertEqual(r.stderr.strip(), "",
                         "a hook that pollutes stderr while exiting 0 is "
                         "invisible (#124)")


class TestGateIsWired(unittest.TestCase):

    def test_registered_on_subagentstop(self):
        d = json.loads((ROOT / "settings" / "hooks.json").read_text())
        cmds = [h["command"] for grp in d["hooks"]["SubagentStop"]
                for h in grp["hooks"]]
        self.assertTrue(any("subagent-stop-check-run-card.sh" in c
                            for c in cmds), cmds)


# --------------------------------------------------------------------------- #
# 2. the BACKSTOP — watchdog job 25
# --------------------------------------------------------------------------- #

class TestCardReconcile(unittest.TestCase):
    """Local git only: which issues did a merge on the base branch close, and
    does each have a delivered card? No `gh` call — the `Closes #N` in a merge
    commit is the same fact, free, and does not spend a 5,000/h API budget
    once per sweep per repo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-cardrec-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sent = []
        self.state = {}
        self.delivered = set()

    def send(self, msg, owner=None, dedup_key=None, dry_run=False, project=None):
        self.sent.append({"msg": msg, "owner": owner, "dedup": dedup_key,
                          "project": project})
        return "sent"

    def repo(self, name="proj", closes=(3, 4), age=3600, base="main"):
        """A repo whose base branch carries a merge closing `closes`."""
        r = self.tmp / name
        r.mkdir()
        _git(r, "init", "-q", "-b", base)
        (r / "f").write_text("1")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "init", ts=NOW - 10 * DAY)
        for n in closes:
            (r / "f").write_text(str(n))
            _git(r, "add", "f")
            _git(r, "commit", "-qm", "feat: thing\n\nCloses #%d" % n,
                 ts=NOW - age)
        # a bare "origin" so origin/<base> exists and resolves as the base ref
        bare = self.tmp / (name + ".git")
        _git(r, "clone", "-q", "--bare", str(r), str(bare))
        _git(r, "remote", "add", "origin", str(bare))
        _git(r, "fetch", "-q", "origin")
        _git(r, "remote", "set-head", "origin", base)
        return r

    def close_more(self, r, n, age):
        """Add and PUSH a second closing commit — `merged_closes` reads
        `origin/<base>` (a remote-tracking ref), so a commit only on the
        local branch is invisible to it until it is actually pushed."""
        (r / "f").write_text(str(n))
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "feat: another thing\n\nCloses #%d" % n,
             ts=NOW - age)
        _git(r, "push", "-q", "origin", "main")
        _git(r, "fetch", "-q", "origin")

    def reconcile(self, cwds, **kw):
        return self.reconcile_at(NOW, cwds, **kw)

    def reconcile_at(self, now_val, cwds, **kw):
        """Like `reconcile`, but with an explicit `now` — lets a test
        simulate the clock advancing past `grace` against a repo whose
        commits (and therefore whose closing timestamps) never change."""
        kw.setdefault("card_probe", lambda root, base: None)
        kw.setdefault("marker_ok", lambda key: key in self.delivered)
        kw.setdefault("send_fn", self.send)
        return wd.card_reconcile(now_val, None, self.state,
                                 {("s%d" % i): str(c)
                                  for i, c in enumerate(cwds)}, **kw)

    def test_an_unreported_merged_ticket_pings(self):
        r = self.repo(closes=(3, 4))
        logs = self.reconcile([r])
        self.assertTrue(self.sent, logs)
        self.assertIn("#3", self.sent[0]["msg"])
        self.assertIn("#4", self.sent[0]["msg"])

    def test_unreported_ping_carries_the_stream_qualified_project_label(self):
        # #369 review M1 (TRIGGERED): "N finished tickets never reported" is
        # ticket-work-scoped traffic per the ticket's own design criterion —
        # must route to the project thread, mirroring _notify_run_card's own
        # project=stream_qualified(name).
        r = self.repo(name="proj", closes=(3, 4))
        with m.patch("getpass.getuser", return_value="david2"):
            self.reconcile([r])
        self.assertTrue(self.sent)
        self.assertEqual(self.sent[0]["project"], "proj-david2")

    def test_a_reported_ticket_is_silent(self):
        r = self.repo(closes=(3,))
        self.delivered.add("proj#3")
        self.reconcile([r])
        self.assertEqual(self.sent, [])

    def test_a_claimed_but_undelivered_card_still_counts_as_unreported(self):
        r = self.repo(closes=(3,))
        # marker_ok is `notify.marker_delivered` in production; an 'error'
        # marker reads False, so the ticket is still unreported.
        self.reconcile([r])
        self.assertTrue(self.sent)

    def test_a_merge_older_than_the_window_is_ignored(self):
        r = self.repo(closes=(3,), age=10 * DAY)
        self.reconcile([r])
        self.assertEqual(self.sent, [],
                         "an old merge is history, not an actionable gap")

    def test_a_repo_with_no_closing_merges_is_silent(self):
        r = self.repo(closes=())
        self.reconcile([r])
        self.assertEqual(self.sent, [])

    def test_a_non_git_cwd_is_silent(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.reconcile([d])
        self.assertEqual(self.sent, [])

    def test_the_ping_is_deduped_per_ticket_forever(self):
        # #224 defect 2 — this used to be a per-repo-per-day bucket
        # (CARD_REPING_S); a repeat call on the same still-unreported
        # ticket, however soon, must never ping it twice AT ALL, not just
        # "not again today".
        r = self.repo(closes=(3,))
        self.reconcile([r])
        self.reconcile([r])
        self.assertEqual(len(self.sent), 1,
                         "a given ticket earns exactly one nag, ever")

    # --- #224 defect 1: a grace period, gated on the CLOSING COMMIT'S OWN --
    # timestamp, not the window edge --------------------------------------

    def test_a_fresh_merge_is_silent_within_grace(self):
        r = self.repo(closes=(3,), age=30)              # merged 30s ago
        self.reconcile_at(NOW, [r])
        self.assertEqual(self.sent, [],
                         "the worker's own post-merge sequence (deploy, "
                         "verify, THEN the card) takes minutes by design")

    def test_the_same_merge_pings_once_grace_passes(self):
        r = self.repo(closes=(3,), age=30)              # merged 30s ago
        self.reconcile_at(NOW, [r])                      # too fresh: silent
        self.assertEqual(self.sent, [])
        self.reconcile_at(NOW + 30 * 60, [r])             # 30m30s later
        self.assertTrue(self.sent, "the grace window must not block forever")
        self.assertIn("#3", self.sent[0]["msg"])

    def test_grace_is_per_ticket_not_per_repo(self):
        # An OLD unreported ticket (#3, past grace) sharing a repo with a
        # BRAND NEW one (#5, still inside grace) — #3 must ping, #5 must
        # not, in the SAME sweep. The grace check reads each ticket's own
        # closing commit, never the repo's newest or oldest commit.
        r = self.repo(closes=(3,), age=3600)
        self.close_more(r, 5, age=30)
        self.reconcile([r])
        self.assertTrue(self.sent)
        self.assertIn("#3", self.sent[0]["msg"])
        self.assertNotIn("#5", self.sent[0]["msg"],
                         "#5 is still inside its own grace window")

    # --- #224 defect 2: dedup per TICKET, not per repo per day ------------

    def test_the_same_ticket_twice_yields_one_ping_a_new_ticket_yields_a_new_one(self):
        r = self.repo(closes=(3,))                       # default age: past grace
        self.reconcile([r])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("#3", self.sent[0]["msg"])

        # a genuinely NEW ticket closes in the same repo, also past grace
        self.close_more(r, 5, age=3600)
        self.reconcile([r])
        self.assertEqual(len(self.sent), 2,
                         "a genuinely new unreported ticket must still "
                         "ping immediately")
        self.assertIn("#5", self.sent[1]["msg"])
        self.assertNotIn("#3", self.sent[1]["msg"],
                         "a ticket already pinged must never be repeated")

    def test_detection_is_logged_every_sweep_even_when_the_ping_is_deduped(self):
        r = self.repo(closes=(3,))
        self.reconcile([r])
        logs = self.reconcile([r])
        self.assertTrue(any("card-unreported" in ln for ln in logs), logs)

    def test_it_is_off_unless_the_probe_is_wired(self):
        r = self.repo(closes=(3,))
        logs = wd.card_reconcile(NOW, None, self.state, {"s": str(r)},
                                 send_fn=self.send, card_probe=None,
                                 marker_ok=lambda k: False)
        self.assertEqual(logs, [])
        self.assertEqual(self.sent, [])

    def test_one_repo_is_examined_once_however_many_panes_sit_in_it(self):
        r = self.repo(closes=(3,))
        self.reconcile([r, r, r])
        self.assertEqual(len(self.sent), 1)

    def test_a_missing_send_fn_never_marks_it_pinged(self):
        r = self.repo(closes=(3,))
        self.reconcile([r], send_fn=None)
        self.reconcile([r])
        self.assertEqual(len(self.sent), 1,
                         "nothing was delivered, so the user is still owed it")

    def test_dry_run_delivers_nothing_and_records_nothing(self):
        r = self.repo(closes=(3,))
        self.reconcile([r], dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.get("card_unreported"), None)

    def test_a_repo_that_never_uses_closes_trailers_falls_back_to_the_probe(self):
        # LIVE-FOUND, and it invalidated this job's original "no gh needed"
        # reasoning: tvdole — one of the two repos in #134's own evidence, and
        # the one that has NEVER produced a card — had 20 commits in 48h and
        # ZERO `Closes #N` trailers in its last 200 commits. Its issues close
        # from the PR body, a GitHub-side fact no local read can see. Local
        # git alone is therefore structurally blind to a whole repo.
        r = self.repo(closes=(), age=3600)
        # give it fresh commits with no trailer at all
        (r / "f").write_text("x")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "feat: no trailer here", ts=NOW - 3600)
        _git(r, "push", "-q", "origin", "main")
        _git(r, "fetch", "-q", "origin")
        self.reconcile([r], closed_fetch=lambda root, since: [77, 78])
        self.assertTrue(self.sent, "the probe's answer must be used")
        self.assertIn("#77", self.sent[0]["msg"])

    def test_trailers_that_answer_locally_are_still_verified_exactly_once(self):
        # #232 DELIBERATELY REVERSES this test's pre-#232 assertion (which
        # was `calls == []`, i.e. "the expensive path must stay off for a
        # repo that answers locally"). A trailer match found by
        # `merged_closes` is a bare commit-message-keyword match, never
        # proof GitHub actually closed the ticket from it (odoo-erp: 22
        # local matches, zero real GitHub auto-closes) — so a genuinely
        # PINGABLE local-trailer candidate must still be confirmed against
        # GitHub, exactly ONCE per repo per sweep (never twice — see
        # `test_the_fallback_verified_path_is_not_re_verified_a_second_time`
        # for the sibling bound on the OTHER path).
        calls = []
        r = self.repo(closes=(3,))
        self.reconcile([r], closed_fetch=lambda root, since: calls.append(root))
        self.assertEqual(calls, [str(r)],
                         "a pingable local-trailer candidate must be "
                         "verified against GitHub before it can nag")

    def test_a_fresh_candidate_still_inside_grace_costs_zero_verify_calls(self):
        # #232's own explicit bound: "zero API calls when there are no
        # fresh candidates". `closed` (from `merged_closes`) is non-empty
        # here, but `pingable` is EMPTY — the one ticket is still inside
        # its own grace window — so the verifier must never be reached at
        # all, not just never SEND.
        calls = []
        r = self.repo(closes=(3,), age=30)              # merged 30s ago
        self.reconcile_at(NOW, [r],
                          closed_fetch=lambda root, since: calls.append(root))
        self.assertEqual(calls, [],
                         "nothing pingable this sweep — verifying it "
                         "would cost an API call for no possible ping")

    def test_the_probe_is_not_consulted_for_a_parked_repo(self):
        calls = []
        r = self.repo(closes=(), age=10 * DAY)
        self.reconcile([r], closed_fetch=lambda root, since: calls.append(root))
        self.assertEqual(calls, [],
                         "no fresh merges means nothing to report on")

    def test_a_failing_probe_is_silent_not_fatal(self):
        def boom(root, since):
            raise RuntimeError("no gh")
        r = self.repo(closes=(), age=3600)
        (r / "f").write_text("x")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "feat: no trailer", ts=NOW - 3600)
        _git(r, "push", "-q", "origin", "main")
        _git(r, "fetch", "-q", "origin")
        logs = self.reconcile([r], closed_fetch=boom)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("closed-fetch" in ln for ln in logs), logs)

    # --- #232: a `merged_closes` candidate — the LOCAL, unverified read —
    # is a commit-message-keyword match, not proof GitHub actually closed
    # the ticket from it. This is the DOMINANT path (unlike #230's fallback
    # above, which only ever runs when `merged_closes` found ZERO trailers)
    # and it is where the odoo-erp false positives actually came from: 22
    # local `Fixes #N` matches on a non-default branch, none of which ever
    # triggered a GitHub auto-close. Every `pingable` candidate must be
    # confirmed against the SAME `_watchdog_closed_fetch` GraphQL check
    # #230 already taught to require a merged-PR closer, before it ever
    # reaches `send_fn`.

    def test_a_merged_closes_candidate_confirmed_by_a_merged_pr_still_pings(self):
        r = self.repo(closes=(3,))
        self.reconcile([r], closed_fetch=lambda root, since: {3: NOW - 3600})
        self.assertTrue(self.sent, "a genuinely merged-PR-closed ticket "
                                   "must still nag — no regression")
        self.assertIn("#3", self.sent[0]["msg"])

    def test_a_merged_closes_candidate_hand_closed_on_github_never_pings(self):
        # the odoo-erp shape via a different door: local git found a real
        # `Closes #3` trailer, but GitHub's own CLOSED_EVENT.closer for #3
        # is None (hand-closed) — `_watchdog_closed_fetch`'s own contract
        # already excludes such a ticket from its returned dict, so an
        # empty dict here is exactly what it would hand back.
        r = self.repo(closes=(3,))
        self.reconcile([r], closed_fetch=lambda root, since: {})
        self.assertEqual(self.sent, [],
                         "a hand-closed ticket was never owed a card")

    def test_a_merged_closes_candidate_still_open_on_github_never_pings(self):
        # the odoo-erp `Fixes #N`-on-`develop` shape: the commit message
        # trailer matched locally, but the issue is still OPEN on GitHub —
        # `_watchdog_closed_fetch`'s `states: CLOSED` query would never
        # even return it. Confirmed dict is non-empty (other tickets DID
        # verify) but genuinely never contains THIS candidate — proves the
        # check is a real membership test, not "any confirmed -> allow all".
        r = self.repo(closes=(3,))
        self.reconcile([r], closed_fetch=lambda root, since: {9: NOW - 100})
        self.assertEqual(self.sent, [])

    def test_the_verifier_erroring_drops_the_ping_but_does_not_swallow_it(self):
        def boom(root, since):
            raise RuntimeError("gh api graphql timed out")
        r = self.repo(closes=(3,))
        logs = self.reconcile([r], closed_fetch=boom)
        self.assertEqual(self.sent, [],
                         "an unverifiable candidate must not ping")
        self.assertTrue(any("verify" in ln for ln in logs), logs)
        # NOT swallowed forever: nothing was added to the per-ticket dedup
        # (`pinged`) on a failed verification, so a LATER sweep — once
        # verification succeeds — must still be able to report it.
        self.reconcile([r], closed_fetch=lambda root, since: {3: NOW - 3600})
        self.assertTrue(self.sent, "a later successful verification must "
                                   "still be able to report it")

    def test_the_fallback_verified_path_is_not_re_verified_a_second_time(self):
        # when `merged_closes` itself found nothing (the tvdole/#230 shape),
        # `closed_fetch` already ran once as the FALLBACK and its answer IS
        # already the verified truth — calling it again for the SAME sweep
        # would double the API cost the ticket explicitly bounds against
        # ("at most one query per repo per sweep").
        calls = []
        def fetch(root, since):
            calls.append(since)
            return {77: NOW - 3600}
        r = self.repo(closes=(), age=3600)
        (r / "f").write_text("x")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "feat: no trailer here", ts=NOW - 3600)
        _git(r, "push", "-q", "origin", "main")
        _git(r, "fetch", "-q", "origin")
        self.reconcile([r], closed_fetch=fetch)
        self.assertEqual(len(calls), 1,
                         "at most one query per repo per sweep")
        self.assertTrue(self.sent)

    def test_dry_run_still_verifies_but_sends_and_records_nothing(self):
        # Adversarial-review finding on #232: the verify call runs BEFORE
        # the `dry_run`/`send_fn is None`/`not pingable` short-circuit,
        # exactly mirroring the pre-existing #230 fallback (also
        # unconditioned on dry_run) — deliberate, not an oversight: a
        # `--dry-run --verbose` diagnostic run (the maintainer's own
        # evidence-gathering command in this ticket) must show the SAME
        # verified answer a live sweep would compute, or the diagnostic
        # output would be misleadingly full of unverified false positives
        # even after this fix is deployed. Locking it here so the
        # behavior is provably intentional, not merely untested.
        calls = []
        r = self.repo(closes=(3,))
        self.reconcile([r], closed_fetch=lambda root, since: calls.append(root),
                       dry_run=True)
        self.assertEqual(calls, [str(r)],
                         "dry-run must still verify, to preview the real "
                         "answer")
        self.assertEqual(self.sent, [], "dry-run never sends")
        self.assertEqual(self.state.get("card_unreported"), None,
                         "dry-run never records anything either")

    def test_two_repos_in_one_sweep_each_cost_at_most_one_verify_call(self):
        # Adversarial-review coverage gap on #232: the "at most one query
        # per repo per sweep" claim was only exercised with a single repo.
        # Two repos in the SAME sweep, sharing ONE closed_fetch callable —
        # one taking the #230 FALLBACK path (merged_closes finds nothing
        # locally), the other taking the NEW verify path (merged_closes
        # finds a real trailer) — must cost exactly one call each, and
        # `verified_source` (a per-iteration local) must never leak
        # between them.
        calls = []

        def fetch(root, since):
            calls.append(root)
            if root.endswith("fallback-repo"):
                return {77: NOW - 3600}
            return {3: NOW - 3600}

        rf = self.repo(name="fallback-repo", closes=(), age=3600)
        (rf / "f").write_text("x")
        _git(rf, "add", "f")
        _git(rf, "commit", "-qm", "feat: no trailer here", ts=NOW - 3600)
        _git(rf, "push", "-q", "origin", "main")
        _git(rf, "fetch", "-q", "origin")

        rv = self.repo(name="verify-repo", closes=(3,))

        self.reconcile([rf, rv], closed_fetch=fetch)
        self.assertEqual(sorted(calls), sorted([str(rf), str(rv)]),
                         "each repo verified exactly once — no double "
                         "query, no skipped query")
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(any("#77" in s["msg"] for s in self.sent))
        self.assertTrue(any("#3" in s["msg"] for s in self.sent))

    # --- #141 half 2: a DELIVERED digest is a report too ------------------
    # The two mechanisms used disjoint key namespaces: the digest writes one
    # marker for `backfill:<repo>:<since>` while job 25 asks about
    # `<repo>#<n>`, so no digest — however successfully delivered — could
    # ever affect this verdict, and its tickets stayed flagged for the full
    # 48h window. Both tests below drive the REAL notify primitives on both
    # sides, against an isolated marker store.

    def _home(self):
        h = self.tmp / "home"
        h.mkdir(exist_ok=True)
        return str(h)

    def test_a_DELIVERED_digest_stops_job_25_re_flagging_its_tickets(self):
        r = self.repo(closes=(3,))
        with m.patch.object(notify, "_claude_dir", self._home):
            notify.mark_backfill_reported("proj", [3], "sent")
            self.reconcile([r], marker_ok=notify.marker_delivered)
        self.assertEqual(self.sent, [],
                         "the user was already told about this ticket, in "
                         "digest form — re-flagging it is a FALSE alert")

    def test_a_digest_that_never_reached_discord_silences_nothing(self):
        # THE constraint (#134): the marker records the POST's outcome, so a
        # digest that failed to send cannot suppress a real report.
        r = self.repo(closes=(3,))
        with m.patch.object(notify, "_claude_dir", self._home):
            notify.mark_backfill_reported("proj", [3], "error")
            self.reconcile([r], marker_ok=notify.marker_delivered)
        self.assertTrue(self.sent, "a failed digest must leave the gap open")

    def test_a_missing_backfill_symbol_degrades_to_card_only_and_logs(self):
        # The new name was added to the SAME `from notify import ...` whose
        # ImportError path `continue`s the repo — so during any window where
        # watchdog/ is newer on disk than notify/ (the timer runs the working
        # tree every 60s, so a mid-push checkout is exactly that window) the
        # whole backstop would go silently dead. That is the #134 shape.
        r = self.repo(closes=(3,))
        saved = notify.backfill_marker_key
        del notify.backfill_marker_key
        try:
            logs = self.reconcile([r])
        finally:
            notify.backfill_marker_key = saved
        self.assertTrue(self.sent, "the card-only check must still run")
        self.assertTrue(any("backfill" in ln for ln in logs), logs)

    def test_it_never_sends_keystrokes(self):
        import inspect
        src = inspect.getsource(wd.card_reconcile)
        self.assertNotIn("send-keys", src,
                         "job 25 is detection only, like jobs 21 and 24")

    def test_wired_into_run_once(self):
        import inspect
        self.assertIn("card_reconcile", inspect.getsource(wd.run_once))


class TestCardReconcile_NoOverlapWithJob24(unittest.TestCase):
    """Checked as the dispatch asked. Job 24 fires when the base branch is
    FROZEN (merges stopped); job 25 fires when the base branch MOVED (merges
    happened) and the reports did not — so in the common case they are
    mutually exclusive by construction, and they share no state key."""

    def test_distinct_state_keys(self):
        import inspect
        self.assertIn("delivery_stall", inspect.getsource(wd.delivery_stall_watch))
        self.assertIn("card_unreported", inspect.getsource(wd.card_reconcile))
        self.assertNotIn("delivery_stall", inspect.getsource(wd.card_reconcile))


# --------------------------------------------------------------------------- #
# 2b. #182 — a REOPENED ticket's stale marker must not dedup its NEXT card
# --------------------------------------------------------------------------- #

class TestCardReconcileReopenClear(unittest.TestCase):
    """#182: the run-card dedup key is `<repo>#<issue>` alone, claimed the
    instant the FIRST close's card delivers — `notify._dedup_claim` uses
    O_CREAT|O_EXCL, so it refuses EVERY later claim for that key forever. A
    reopened ticket's second, independently-earned card therefore silently
    dedups against the first close's marker and never reaches Discord.

    Job 25 (`card_reconcile`), which already reconciles per-repo card state
    every sweep, gains an ADDITIVE `reopen_fetch` step: for every issue
    number that already has a run-card marker for a repo, ask (once per
    repo per sweep) which of those are OPEN again; a reopened one's stale
    marker is cleared via `notify.forget_marker`, so the next close's
    `_notify_run_card` claims fresh. Every existing consumer of the plain
    `<repo>#<issue>` key (`marker_delivered`, the SubagentStop gate, this
    same job's own `missing` check) is untouched — the key format itself
    never changes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-cardrec-reopen-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._patcher = m.patch.object(notify, "_claude_dir",
                                       lambda: str(self.home / ".claude"))
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.state = {}
        self.sent = []
        self._post_patcher = m.patch.object(notify, "_post_discord", return_value=True)
        self._post_patcher.start()
        self.addCleanup(self._post_patcher.stop)

    def send(self, msg, owner=None, dedup_key=None, dry_run=False, project=None):
        self.sent.append({"msg": msg, "dedup": dedup_key})
        return "sent"

    def repo(self, name="proj", closes=(3,), age=3600, base="main"):
        r = self.tmp / name
        r.mkdir()
        _git(r, "init", "-q", "-b", base)
        (r / "f").write_text("1")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "init", ts=NOW - 10 * DAY)
        for n in closes:
            (r / "f").write_text(str(n))
            _git(r, "add", "f")
            _git(r, "commit", "-qm", "feat: thing\n\nCloses #%d" % n, ts=NOW - age)
        bare = self.tmp / (name + ".git")
        _git(r, "clone", "-q", "--bare", str(r), str(bare))
        _git(r, "remote", "add", "origin", str(bare))
        _git(r, "fetch", "-q", "origin")
        _git(r, "remote", "set-head", "origin", base)
        return r

    def _fire_card(self, key):
        """Deliver a REAL marker via the real notify.send() — the same
        primitive `_notify_run_card` actually uses."""
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        status = notify.send("card body", env=env, owner="", dedup_key=key)
        self.assertEqual(status, "sent", "test setup must genuinely deliver")

    def reconcile(self, r, **kw):
        kw.setdefault("card_probe", lambda root, base: None)
        kw.setdefault("marker_ok", notify.marker_delivered)
        kw.setdefault("send_fn", self.send)
        return wd.card_reconcile(NOW, None, self.state, {"s": str(r)}, **kw)

    def test_same_round_the_marker_survives_untouched(self):
        r = self.repo(closes=(3,))
        self._fire_card("proj#3")
        # the ticket is STILL closed this whole round — reopen_fetch has
        # nothing to report as open.
        self.reconcile(r, reopen_fetch=lambda root, nums: set())
        self.assertTrue(notify.marker_delivered("proj#3"),
                        "same round: the marker must survive untouched")
        self.assertEqual(self.sent, [],
                         "already has a delivered card — no nag")

    def test_reopened_ticket_marker_is_cleared_so_the_next_close_cards_again(self):
        r = self.repo(closes=(3,))
        self._fire_card("proj#3")
        self.assertTrue(notify.marker_delivered("proj#3"))
        # the watchdog observes #3 is OPEN again (reopened after its card)
        self.reconcile(
            r, reopen_fetch=lambda root, nums: {3} if 3 in nums else set())
        self.assertFalse(notify.marker_delivered("proj#3"),
                         "a reopened ticket's stale marker must be cleared")
        # the SAME real primitive _notify_run_card uses can now claim fresh
        self._fire_card("proj#3")
        self.assertTrue(notify.marker_delivered("proj#3"),
                        "the second close's card must actually deliver")

    def test_reopen_fetch_unwired_changes_nothing(self):
        r = self.repo(closes=(3,))
        self._fire_card("proj#3")
        self.reconcile(r)                 # no reopen_fetch kwarg at all
        self.assertTrue(notify.marker_delivered("proj#3"),
                        "unwired reopen_fetch must be a complete no-op, "
                        "like every other 'wired = on' fetch in this job")

    def test_a_failing_reopen_fetch_never_clears_a_marker_it_could_not_confirm(self):
        r = self.repo(closes=(3,))
        self._fire_card("proj#3")

        def boom(root, nums):
            raise RuntimeError("no gh")

        logs = self.reconcile(r, reopen_fetch=boom)
        self.assertTrue(notify.marker_delivered("proj#3"),
                        "an unmeasurable check must never clear a marker")
        self.assertTrue(any("reopen-fetch-failed" in ln for ln in logs), logs)

    def test_reopen_fetch_only_asked_about_numbers_with_an_existing_marker(self):
        r = self.repo(closes=(3,))
        self._fire_card("proj#3")
        seen = []

        def spy(root, nums):
            seen.append(set(nums))
            return set()

        self.reconcile(r, reopen_fetch=spy)
        self.assertEqual(seen, [{3}])

    def test_reopen_clear_runs_even_when_nothing_closed_in_this_sweeps_window(self):
        # CRITICAL adversarial-review finding: the reopen-clear step used to
        # sit AFTER `if not closed: continue` — a repo whose ONLY close (the
        # one the stale marker is for) has aged out of the 48h window, with
        # no OTHER ticket closing in this sweep, never even reached name
        # resolution — reopen_fetch was never called and the stale marker
        # was never cleared, reproducing the exact #182 bug this job exists
        # to fix.
        r = self.repo(closes=(3,), age=10 * DAY)   # aged out of the window
        self._fire_card("proj#3")
        seen = []

        def spy(root, nums):
            seen.append(set(nums))
            return {3} if 3 in nums else set()

        self.reconcile(r, reopen_fetch=spy)
        self.assertEqual(seen, [{3}],
                         "reopen_fetch must be consulted even when nothing "
                         "closed in this sweep's own window")
        self.assertFalse(notify.marker_delivered("proj#3"),
                         "the stale marker must still be cleared")


# --------------------------------------------------------------------------- #
# 3. the SUPPRESSION becomes conditional on DELIVERY
# --------------------------------------------------------------------------- #

class TestSuppressionIsConditionalOnDelivery(unittest.TestCase):
    """THE design error the ticket names, restated as an invariant: the `✅`
    is suppressed because a card was DELIVERED, never because a `/goal` is
    armed. The old tests asserted the armed-goal premise directly and are
    rewritten here — that premise is what produced the silence."""

    _n = 0

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-supp-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-supp-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/parovanie-produktov.git")
        TestSuppressionIsConditionalOnDelivery._n += 1
        self.sid = "test-supp-%d-%d" % (os.getpid(),
                                        TestSuppressionIsConditionalOnDelivery._n)
        # (#293) sweep_session_files is shape-agnostic (globs "*<sid>*") --
        # never hand-enumerate marker paths here again, or a FUTURE new
        # marker silently reopens this same leak (2026-08-07: 207 real
        # leftover lastq files from this class's own ❓ test, never cleaned
        # by the old pending+cardchk-only list).
        self.addCleanup(sweep_session_files, self.sid)
        self.pending = Path("/tmp/claude-discord-pending-%s" % self.sid)

    def mark(self, key, status="sent"):
        d = self.home / ".claude" / "autopilot-notify-sent"
        d.mkdir(parents=True, exist_ok=True)
        (d / key).write_text("%s %s" % (__import__("time").time(), status))

    def stop(self, msg, pane="◎ /goal   ctx 50K\n", cwd=None):
        env = {**os.environ, "HOME": str(self.home),
               "DISCORD_NOTIFY_DRYRUN": "1", "AIRULESET_NOTIFY_OWNER": "",
               "TMUX_PANE": "", "ND_FAKE_PANE_CAPTURE": pane}
        payload = json.dumps({"session_id": self.sid,
                              "last_assistant_message": msg,
                              "cwd": str(self.repo if cwd is None else cwd)})
        return subprocess.run(["bash", str(PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    DONE = "## ✅ Work Complete\n\n✅ DONE: #41 zmergnuté -> v1.2.3"

    def test_armed_goal_with_a_delivered_card_still_suppresses(self):
        self.mark("parovanie-produktov#41", "sent")
        self.stop(self.DONE)
        self.assertFalse(self.pending.exists(),
                         "the card gave phone visibility — no second ping")

    def test_armed_goal_with_NO_card_lets_the_ping_through(self):
        self.stop(self.DONE)
        self.assertTrue(self.pending.exists(),
                        "this is the whole ticket: a suppression that defers "
                        "to an unenforced action is a silence generator")
        self.assertIn("zmergnuté", self.pending.read_text())

    def test_armed_goal_with_a_FAILED_card_lets_the_ping_through(self):
        self.mark("parovanie-produktov#41", "error")
        self.stop(self.DONE)
        self.assertTrue(self.pending.exists())

    def test_a_card_older_than_the_previous_boundary_does_not_suppress(self):
        # ticket 1 delivered, so its ✅ is suppressed; ticket 2 delivers
        # nothing, and ticket 1's stale marker must not cover it.
        self.mark("parovanie-produktov#41", "sent")
        self.stop(self.DONE)
        self.assertFalse(self.pending.exists())
        self.stop("✅ DONE: #42 zmergnuté")
        self.assertTrue(self.pending.exists(),
                        "a previous ticket's card cannot cover this one")

    def test_an_unresolvable_repo_never_suppresses(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-supp-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        self.stop(self.DONE, cwd=d)
        self.assertTrue(self.pending.exists(),
                        "cannot prove delivery -> never suppress")

    def test_no_goal_armed_still_queues_the_ping(self):
        self.mark("parovanie-produktov#41", "sent")
        self.stop(self.DONE, pane="ctx 50K\n")
        self.assertTrue(self.pending.exists())

    def test_a_question_still_pings_regardless(self):
        r = self.stop("❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("cardchk-abort", r.stderr)


class TestSuppressionSetupCleansEveryMarkerShape(unittest.TestCase):
    """(#293 adversarial review, finding 3) TestSuppressionIsConditionalOnDelivery
    .setUp hand-enumerated only pending + cardchk cleanup — its own
    test_a_question_still_pings_regardless sends a ❓, which writes LASTQ
    (hooks/notify-discord-pending.sh), never cleaned. Live proof on this box
    (measured 2026-08-07): 207 leftover
    /tmp/claude-discord-lastq-test-supp-* files, still growing on every
    unittest run touching this file. Same class of bug as #293's own fix
    (test_airuleset.py's _sid helpers) — reused here via
    tests/_hook_state_cleanup.py::sweep_session_files, shape-agnostic by
    construction so a future new marker can never reopen this again."""

    def test_no_marker_of_any_shape_survives_the_question_test(self):
        case = TestSuppressionIsConditionalOnDelivery(
            "test_a_question_still_pings_regardless")
        result = unittest.TestResult()
        case.run(result)
        self.assertTrue(result.wasSuccessful(),
                        (result.failures, result.errors))
        leftover = glob.glob("/tmp/claude-discord-*-" + case.sid)
        self.assertEqual(leftover, [], leftover)


# --------------------------------------------------------------------------- #
# 4. the evidence block gains the field the gate reads
# --------------------------------------------------------------------------- #

class TestBackfillDigest(unittest.TestCase):
    """ONE catch-up message per repo, never one card per ticket. The silent
    window held ~103 closed issues across two repos; firing a retroactive
    card per ticket would put a hundred pings on the user's phone to
    apologise for having sent none."""

    def compose(self, n, extra=""):
        import airuleset
        return airuleset.compose_backfill_digest(
            "parovanie-produktov",
            [{"number": i, "title": "ticket %d%s" % (i, extra)}
             for i in range(1, n + 1)], "2026-07-23")

    def test_it_names_the_repo_the_count_and_the_window(self):
        body = self.compose(4)
        self.assertIn("parovanie-produktov", body)
        self.assertIn("**4**", body)
        self.assertIn("2026-07-23", body)

    def test_it_is_one_message_not_one_per_ticket(self):
        self.assertEqual(self.compose(60).count("dobiehacie hlásenie"), 1)

    def test_a_long_list_is_bounded(self):
        body = self.compose(60)
        self.assertLess(body.count("• #"), 15, body)
        self.assertIn("a ďalších 50", body)

    def test_a_long_title_is_clipped(self):
        body = self.compose(1, extra=" " + "x" * 300)
        self.assertTrue(all(len(ln) < 200 for ln in body.splitlines()), body)

    def test_it_says_the_gap_is_now_watched(self):
        self.assertIn("kontroluje", self.compose(2))

    def test_slovak_plural_agrees_with_the_count(self):
        self.assertIn("**1** ticket,", self.compose(1))
        self.assertIn("**3** tickety,", self.compose(3))
        self.assertIn("**9** ticketov,", self.compose(9))

    def test_it_does_not_claim_a_deploy_it_never_verified(self):
        # a catch-up message that overclaims is a worse repair than the
        # silence it apologises for — nothing here checked a deploy.
        self.assertNotIn("nasaden", self.compose(5))


class TestBackfillDigestNeedsALocalCheckout(unittest.TestCase):
    """#141 half 1 — the digest is computed from THIS box's card markers,
    and those markers are machine-local (`notify._dedup_dir()` resolves
    against the running box's own `$HOME`).

    Job 25 is safe from this by construction: it only ever examines
    checkouts a live pane sits in, so the markers it reads are always the
    ones belonging to that checkout. `--backfill-digest` is operator-driven
    and takes a bare `owner/name`, so nothing correlates the repo with the
    box — point it at a repo whose checkout lives elsewhere and every ticket
    reads as unreported, producing a digest that apologises for reports
    which were in fact delivered on another box. A false statement on the
    user's phone is worse than no statement, so this refuses rather than
    under-reports."""

    def setUp(self):
        import airuleset
        self.a = airuleset
        self.sent = []
        self.sent_owners = []
        # This class drives the REAL `_notify_backfill_digest`, which reaches
        # the REAL `mark_backfill_reported` — so without this the suite
        # writes live SUPPRESSION markers into `~/.claude/autopilot-notify-
        # sent/` and silences job 25 for any repo named `proj`, for the
        # 14-day marker TTL. Found in review AFTER it had already happened.
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-bfguard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = m.patch.object(notify, "_claude_dir", lambda: str(self.tmp))
        p.start()
        self.addCleanup(p.stop)

    def send(self, body, owner=None, dedup_key=None, dry_run=False, project=None):
        self.sent.append(body)
        self.sent_owners.append(owner)
        return "dry-run" if dry_run else "sent"

    def args(self, **kw):
        d = {"repo": "owner/proj", "since": "2026-07-20T00:00:00Z",
             "owner_name": None, "dry_run": False, "force": False}
        d.update(kw)
        return _Args(**d)

    def test_this_class_never_writes_into_the_real_marker_store(self):
        # the isolation is the assertion: a marker written during this test
        # must land in the temp dir, never in the box's live store.
        self.assertTrue(notify._dedup_dir().startswith(str(self.tmp)))
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name, **kw: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: json.dumps(
                 [{"number": 7, "title": "t",
                   "closedAt": "2026-07-24T00:00:00Z"}])):
            self.a._notify_backfill_digest(self.args(), self.send)
        self.assertTrue(
            (self.tmp / "autopilot-notify-sent" /
             notify.backfill_marker_key("proj", 7)).exists())

    # --- the resolver must not refuse a legitimate operator ---------------
    def test_a_checkout_deeper_than_the_default_sweep_is_still_found(self):
        # `discover_managed_repos` stops at depth 4 (it `continue`s BEFORE
        # testing for .git), so a real repo one level deeper resolves to
        # None and the operator standing on the right box is refused with no
        # way through. Measured on dev1: 40 discovered vs 55 real checkouts.
        deep = self.tmp / "a" / "b" / "c" / "d" / "proj"
        (deep / ".git").mkdir(parents=True)
        found = self.a._local_checkout_for_repo(
            "proj", home=str(self.tmp), name_of=lambda r: Path(r).name)
        self.assertEqual(found, str(deep))

    def test_a_worktree_or_submodule_checkout_is_still_found(self):
        # a git worktree / submodule has `.git` as a FILE, which the sweep
        # never even looks for. JUCE on dev1 is exactly this shape.
        wt = self.tmp / "wt" / "proj"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/proj\n")
        found = self.a._local_checkout_for_repo(
            "proj", home=str(self.tmp), name_of=lambda r: Path(r).name)
        self.assertEqual(found, str(wt))

    def test_the_invocation_cwd_short_circuits_the_sweep(self):
        # standing IN the repo is the common case and must cost no walk.
        swept = []
        got = self.a._local_checkout_for_repo(
            "proj", cwd="/home/x/devel/proj",
            roots=lambda: swept.append(1) or [],
            name_of=lambda r: "proj" if r == "/home/x/devel/proj" else "")
        self.assertEqual(got, "/home/x/devel/proj")
        self.assertEqual(swept, [], "no $HOME walk when the cwd answers it")

    def test_one_bad_root_does_not_abort_the_whole_scan(self):
        def flaky(root):
            if root == "/bad":
                raise RuntimeError("weird")
            return "proj" if root == "/good" else ""
        self.assertEqual(
            self.a._local_checkout_for_repo(
                "proj", roots=lambda: ["/bad", "/good"], name_of=flaky,
                cwd="/nowhere"),
            "/good")

    def test_force_runs_it_anyway(self):
        # the command exists to REPAIR a silence; a detector that can be
        # wrong must never be the only thing standing between the operator
        # and the repair.
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name, **kw: None), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: json.dumps(
                 [{"number": 7, "title": "t",
                   "closedAt": "2026-07-24T00:00:00Z"}])):
            self.a._notify_backfill_digest(self.args(force=True), self.send)
        self.assertEqual(len(self.sent), 1)

    def test_the_help_documents_the_override(self):
        r = subprocess.run([sys.executable, str(ROOT / "airuleset.py"),
                            "notify", "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("--force", " ".join(r.stdout.split()))

    # --- the resolver -----------------------------------------------------
    def test_the_resolver_matches_on_the_ORIGIN_name_not_the_directory(self):
        # the live trap `repo_name_for` exists for: marek's checkout is
        # `parovanie_produktov` while every marker is keyed
        # `parovanie-produktov`.
        root = "/home/x/devel/forestshop/parovanie_produktov"
        self.assertEqual(
            self.a._local_checkout_for_repo(
                "parovanie-produktov", cwd="/elsewhere", roots=[root],
                name_of=lambda r: ("parovanie-produktov" if r == root else "")),
            root)

    def test_the_resolver_returns_None_when_no_checkout_matches(self):
        self.assertIsNone(self.a._local_checkout_for_repo(
            "odoo-erp", roots=["/home/x/devel/airuleset"],
            name_of=lambda r: "airuleset"))

    def test_the_resolver_never_raises_on_an_unreadable_tree(self):
        def boom(root):
            raise OSError("gone")
        self.assertIsNone(self.a._local_checkout_for_repo(
            "proj", roots=["/nope"], name_of=boom))

    # --- the refusal ------------------------------------------------------
    def test_a_repo_with_no_local_checkout_is_refused_loudly(self):
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: None):
            with self.assertRaises(SystemExit) as cm:
                self.a._notify_backfill_digest(self.args(), self.send)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.sent, [],
                         "a digest computed from the wrong marker store must "
                         "never reach the phone")

    def test_the_refusal_happens_before_any_gh_call(self):
        calls = []
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: None), \
             m.patch.object(self.a, "_gh_out",
                            lambda *a, **k: calls.append(a) or "[]"):
            with self.assertRaises(SystemExit):
                self.a._notify_backfill_digest(self.args(), self.send)
        self.assertEqual(calls, [])

    def test_a_local_checkout_lets_the_digest_run(self):
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(self.args(), self.send)
        self.assertEqual(len(self.sent), 1)

    def test_the_help_states_the_machine_local_constraint(self):
        r = subprocess.run([sys.executable, str(ROOT / "airuleset.py"),
                            "notify", "--help"],
                           capture_output=True, text=True, timeout=60)
        # argparse hard-wraps, and textwrap breaks ON HYPHENS — so
        # "machine-local" really renders as "machine-\nlocal" and a plain
        # whitespace join yields "machine- local". Re-join that one artifact
        # rather than asserting around where the wrap happened to land.
        h = " ".join(r.stdout.split())
        flat = h.replace("- ", "-")
        self.assertIn("--backfill-digest", h)
        self.assertIn("machine-local", flat,
                      "the constraint must be visible where the operator "
                      "reads the command, not only in the source")
        self.assertIn("checkout", h)
        self.assertIn("refuses", h)

    # --- who the digest is ADDRESSED to ------------------------------------
    # The owner used to be whatever the operator typed after `--owner-name`,
    # with `resolve_owner()` (the OPERATOR's own tmux, or nothing at all over
    # ssh) as the only fallback. Both are guesses about someone else's repo:
    # on 2026-07-29 a codex-bridge catch-up — a repo whose every pane belongs
    # to `david` — was sent `--owner-name zbynek` from a dev1 session and
    # landed in the wrong thread. The checkout's own pane knows the answer, so
    # the flag stops being the primary source and a flag that contradicts the
    # pane is refused rather than obeyed.

    def test_the_owner_comes_from_the_pane_sitting_in_the_checkout(self):
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_checkout_pane_owner",
                            lambda path: "david"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(self.args(), self.send)
        self.assertEqual(self.sent_owners, ["david"])

    def test_a_stated_owner_contradicting_the_checkouts_pane_is_refused(self):
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_checkout_pane_owner",
                            lambda path: "david"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            with self.assertRaises(SystemExit) as cm:
                self.a._notify_backfill_digest(
                    self.args(owner_name="zbynek"), self.send)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(self.sent, [],
                         "a digest addressed to the wrong thread must never "
                         "leave — the misdirected report is the whole defect")

    def test_a_stated_owner_still_carries_when_no_pane_answers(self):
        # the repair path must stay usable on a box where the repo has no
        # live pane at all — that is why the flag exists.
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_checkout_pane_owner",
                            lambda path: ""), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(
                self.args(owner_name="zbynek"), self.send)
        self.assertEqual(self.sent_owners, ["zbynek"])

    def test_a_stated_owner_that_is_a_raw_stream_persona_is_redirected(self):
        # #302 review MAJOR: --owner-name is documented for exactly the "no
        # live pane on this box" case (derived == "") -- so `stated` is the
        # ONLY signal, and if it's a raw stream-persona account name (e.g.
        # the operator typed the box's own unix account, not the person),
        # it must still redirect through notify.STREAM_NOTIFY_OWNER, not
        # reach send() as the raw account name.
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_checkout_pane_owner",
                            lambda path: ""), \
             m.patch.dict(notify.STREAM_NOTIFY_OWNER,
                          {"montalu2": "zbynek"}, clear=False), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(
                self.args(owner_name="montalu2"), self.send)
        self.assertEqual(self.sent_owners, ["zbynek"])

    def test_a_stated_owner_that_AGREES_with_the_pane_is_not_refused(self):
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_checkout_pane_owner",
                            lambda path: "david"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(
                self.args(owner_name="david"), self.send)
        self.assertEqual(self.sent_owners, ["david"])

    # --- the pane resolver itself -----------------------------------------
    def test_the_pane_resolver_matches_the_checkout_directory(self):
        got = self.a._checkout_pane_owner(
            "/home/x/devel/proj",
            panes=lambda: [("%1", "/home/x/devel/other"),
                           ("%4", "/home/x/devel/proj")],
            owner_of=lambda pid: {"%1": "marek", "%4": "david"}[pid])
        self.assertEqual(got, "david")

    def test_the_pane_resolver_matches_a_pane_deeper_in_the_checkout(self):
        # a session parked in a subdirectory of the repo is the same repo.
        got = self.a._checkout_pane_owner(
            "/home/x/devel/proj",
            panes=lambda: [("%4", "/home/x/devel/proj/src/deep")],
            owner_of=lambda pid: "david")
        self.assertEqual(got, "david")

    def test_a_sibling_directory_sharing_a_prefix_is_not_a_match(self):
        # `/proj-old` starts with `/proj` as a STRING and is a different repo.
        got = self.a._checkout_pane_owner(
            "/home/x/devel/proj",
            panes=lambda: [("%9", "/home/x/devel/proj-old")],
            owner_of=lambda pid: "marek")
        self.assertEqual(got, "")

    def test_two_owners_on_one_checkout_resolve_to_nothing(self):
        # the same ambiguity job 5 refuses to guess through: no mention beats
        # a wrong one.
        got = self.a._checkout_pane_owner(
            "/home/x/devel/proj",
            panes=lambda: [("%1", "/home/x/devel/proj"),
                           ("%2", "/home/x/devel/proj")],
            owner_of=lambda pid: {"%1": "david", "%2": "marek"}[pid])
        self.assertEqual(got, "")

    def test_the_pane_resolver_never_raises_without_tmux(self):
        def boom():
            raise OSError("no tmux here")
        self.assertEqual(
            self.a._checkout_pane_owner("/home/x/devel/proj", panes=boom), "")

    # --- #302: the raw owner must be redirected through STREAM_NOTIFY_OWNER
    def test_the_pane_resolver_redirects_a_stream_persona_owner(self):
        # airuleset#302: `_checkout_pane_owner`'s own `owner_of(pid)` call
        # had NO knowledge of notify.STREAM_NOTIFY_OWNER (#259/#212) --
        # unlike every OTHER pane-owner consumer in this codebase
        # (watchdog's own run_once wraps every pane_owner() call in
        # stream_redirect()). A stream persona's raw pane owner (its own
        # unix account name, e.g. 'montalu2') must resolve to the real
        # Discord identity it is configured to redirect to, not the raw
        # account name.
        with m.patch.dict(notify.STREAM_NOTIFY_OWNER,
                          {"montalu2": "zbynek"}, clear=False):
            got = self.a._checkout_pane_owner(
                "/home/x/devel/proj",
                panes=lambda: [("%4", "/home/x/devel/proj")],
                owner_of=lambda pid: "montalu2")
        self.assertEqual(got, "zbynek")

    def test_the_digest_owner_is_redirected_through_stream_notify_owner(self):
        # End-to-end through the REAL production code path (no owner_of/
        # panes override) -- _notify_backfill_digest must send with the
        # REDIRECTED owner, never the stream persona's raw unix account.
        issues = json.dumps([{"number": 7, "title": "t",
                              "closedAt": "2026-07-24T00:00:00Z"}])
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name: "/home/x/devel/proj"), \
             m.patch.object(wd, "list_claude_panes",
                            lambda run=None: [("%4", "/home/x/devel/proj")]), \
             m.patch.object(wd, "pane_owner",
                            lambda pid, run=None: "montalu2"), \
             m.patch.dict(notify.STREAM_NOTIFY_OWNER,
                          {"montalu2": "zbynek"}, clear=False), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues), \
             m.patch.object(notify, "marker_delivered", lambda k: False):
            self.a._notify_backfill_digest(self.args(), self.send)
        self.assertEqual(self.sent_owners, ["zbynek"])


class TestBackfillMarkerIsWrittenOnlyOnPROVENDelivery(unittest.TestCase):
    """#141 half 2 — the digest records what it reported, in its OWN
    namespace, and only when the message provably reached Discord.

    The #134 hard constraint, restated as an invariant: a marker that
    suppresses a later signal must be written from the ARTIFACT the action
    leaves behind (here `send()`'s post-POST return value), never from the
    intent to act. The rule lives inside `mark_backfill_reported` rather
    than at the call site, so no caller can lose it by forgetting a
    branch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-bfmark-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = m.patch.object(notify, "_claude_dir", lambda: str(self.tmp))
        p.start()
        self.addCleanup(p.stop)

    def key(self, n):
        return notify.backfill_marker_key("proj", n)

    def test_a_delivered_digest_records_one_marker_per_ticket(self):
        self.assertEqual(notify.mark_backfill_reported("proj", [3, 4], "sent"),
                         2)
        self.assertTrue(notify.marker_delivered(self.key(3)))
        self.assertTrue(notify.marker_delivered(self.key(4)))

    def test_no_status_other_than_sent_records_anything(self):
        for status in ("error", "no-config", "dedup", "dry-run", "", None):
            with self.subTest(status=status):
                self.assertEqual(
                    notify.mark_backfill_reported("proj", [3], status), 0)
                self.assertFalse(notify.marker_delivered(self.key(3)))

    def test_it_never_writes_the_per_ticket_CARD_namespace(self):
        notify.mark_backfill_reported("proj", [3], "sent")
        self.assertFalse(
            notify.marker_delivered("proj#3"),
            "a digest is not a card: `<repo>#<n>` means 'this ticket got its "
            "OWN delivered card' and is read by the SubagentStop gate — "
            "writing it here would let a genuinely missing card pass")

    def test_the_two_namespaces_cannot_collide(self):
        # Teeth, not formatting: the claim is that NO repo name can make a
        # card key land on the same FILE as a backfill key. Asserting the
        # format string back to itself proved nothing — `name` reaches the
        # key builder from an unvalidated `--repo` split and from
        # `repo_name_for`, which parses any remote URL (this suite's own
        # fixtures use `/tmp/...git` paths), so "GitHub forbids `#`" is a
        # convention, not an enforced property.
        # Scope of the claim, stated exactly: for every name a GitHub repo
        # can actually have (`[A-Za-z0-9._-]`), the two namespaces land on
        # different files. `backfill` and `backfill_proj` are in the list
        # because they are the names that LOOK like they should collide.
        names = ["proj", "backfill", "backfill_proj", "backfill-proj",
                 "backfill.proj", "proj.git", "PROJ", "a_b-c.d"]
        cards = {notify._dedup_path("%s#%s" % (n, i))
                 for n in names for i in (3, 7, 37)}
        backs = {notify._dedup_path(notify.backfill_marker_key(n, i))
                 for n in names for i in (3, 7, 37)}
        self.assertEqual(cards & backs, set(),
                         "a card marker and a digest marker collapsed onto "
                         "one file — one fact would overwrite the other")

    def test_the_key_builder_neutralises_a_hostile_name(self):
        # The residual exposure is a name carrying `#`, which needs a
        # non-GitHub remote (`repo_name_for` parses any URL, and this
        # suite's own fixtures use `/tmp/...git` paths). The DIGEST side can
        # never be the one that forges it: the builder sanitises its name to
        # the card alphabet, so its key always has exactly two `#`.
        for hostile in ["a#b", "backfill#proj", "a:b", "a/b", "a b"]:
            with self.subTest(name=hostile):
                key = notify.backfill_marker_key(hostile, 3)
                self.assertEqual(key.count("#"), 2, key)
                self.assertTrue(key.startswith("backfill#"))

    def test_the_count_is_writes_that_happened_not_writes_attempted(self):
        # this is the one function whose whole premise is honesty about
        # artifacts; returning N while nothing reached disk (read-only or
        # full filesystem) is the same class of lie it exists to prevent.
        with m.patch.object(notify, "_dedup_mark_status",
                            lambda key, status: False):
            self.assertEqual(
                notify.mark_backfill_reported("proj", [3, 4], "sent"), 0)

    def test_it_works_on_a_box_that_has_never_sent_anything(self):
        # no marker directory exists yet — the write must create it rather
        # than silently no-op, which would look exactly like the bug.
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertEqual(notify.mark_backfill_reported("proj", [9], "sent"), 1)
        self.assertTrue(notify.marker_delivered(self.key(9)))


class TestBackfillDigestRecordsWhatItReported(unittest.TestCase):
    """The same invariant end to end, through the real CLI entry point."""

    def setUp(self):
        import airuleset
        self.a = airuleset
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-bfrun-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = m.patch.object(notify, "_claude_dir", lambda: str(self.tmp))
        p.start()
        self.addCleanup(p.stop)
        self.status = "sent"
        self.sent = []
        self.sent_projects = []

    def send(self, body, owner=None, dedup_key=None, dry_run=False, project=None):
        if dry_run:
            return "dry-run"
        self.sent.append(body)
        self.sent_projects.append(project)
        return self.status

    def run_digest(self, since="2026-07-20T00:00:00Z", dry_run=False):
        issues = json.dumps(
            [{"number": 7, "title": "a", "closedAt": "2026-07-24T00:00:00Z"},
             {"number": 8, "title": "b", "closedAt": "2026-07-25T00:00:00Z"}])
        args = _Args(repo="owner/proj", since=since, owner_name=None,
                     dry_run=dry_run, force=False)
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name, **kw: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: issues):
            try:
                self.a._notify_backfill_digest(args, self.send)
            except SystemExit as e:
                return e.code
        return 0

    def key(self, n):
        return notify.backfill_marker_key("proj", n)

    def test_a_delivered_digest_marks_every_ticket_it_accounted_for(self):
        self.run_digest()
        self.assertEqual(len(self.sent), 1, "still ONE message, not N cards")
        self.assertTrue(notify.marker_delivered(self.key(7)))
        self.assertTrue(notify.marker_delivered(self.key(8)))

    def test_digest_carries_the_stream_qualified_project_label(self):
        # #369 review M1 (TRIGGERED): the SAME ticket-work-scoped routing
        # every other wired call site now uses.
        with m.patch("getpass.getuser", return_value="david2"):
            self.run_digest()
        self.assertEqual(self.sent_projects, ["proj-david2"])

    def test_a_digest_that_failed_to_send_marks_nothing(self):
        self.status = "error"
        self.assertNotEqual(self.run_digest(), 0)
        self.assertFalse(notify.marker_delivered(self.key(7)))

    def test_dry_run_records_nothing(self):
        self.run_digest(dry_run=True)
        self.assertFalse(notify.marker_delivered(self.key(7)))

    def test_a_ticket_a_delivered_digest_covered_is_not_re_listed(self):
        self.run_digest()
        self.sent = []
        self.run_digest(since="2026-07-01T00:00:00Z")   # a wider window
        self.assertEqual(self.sent, [],
                         "a second digest must not re-report tickets an "
                         "earlier delivered one already accounted for")

    def test_it_suppresses_only_the_tickets_the_message_actually_NAMED(self):
        # The digest shows at most 10 titles and then "a ďalších N" — the
        # overflow tickets are counted but never named, so the user has no
        # number to chase. Marking them would let the artifact claim
        # per-ticket coverage the message never gave, and job 25 would go
        # permanently quiet on tickets the user cannot identify. The bound
        # is deliberate (the phone gets numbers, not a wall), so the honest
        # resolution is to suppress exactly what was named and let job 25
        # keep surfacing the rest, a batch at a time.
        big = json.dumps([{"number": i, "title": "t%d" % i,
                           "closedAt": "2026-07-24T00:00:00Z"}
                          for i in range(1, 26)])
        args = _Args(repo="owner/proj", since="2026-07-20T00:00:00Z",
                     owner_name=None, dry_run=False, force=False)
        with m.patch.object(self.a, "_local_checkout_for_repo",
                            lambda name, **kw: "/home/x/devel/proj"), \
             m.patch.object(self.a, "_gh_out", lambda *a, **k: big):
            self.a._notify_backfill_digest(args, self.send)
        named = [i for i in range(1, 26)
                 if notify.marker_delivered(self.key(i))]
        self.assertEqual(named, list(range(1, 11)))
        for i in named:
            self.assertIn("#%d " % i, self.sent[0] + " ")

    def test_a_dedup_return_does_not_read_as_a_delivery(self):
        # the digest-level key is date-truncated, so a same-day re-run after
        # a NEW ticket closes hits the claim, sends nothing, and used to
        # print `dedup (1 tickets)` and exit 0 — indistinguishable from a
        # delivery, while that ticket stayed unreported.
        self.status = "dedup"
        code = self.run_digest()
        self.assertNotEqual(code, 0,
                            "nothing was sent and tickets are still pending")
        self.assertFalse(notify.marker_delivered(self.key(7)))


class TestWorkerEvidenceBlockDeclaresTheCard(unittest.TestCase):

    def test_full_authority_block_has_a_cards_fired_line(self):
        t = (ROOT / "agents" / "autopilot-worker.md").read_text()
        head = t.index("issues: #<A>")
        tail = t.index("fork-no-merge variant of the FINAL MESSAGE")
        self.assertIn("cards_fired:", t[head:tail],
                      "the full-authority block had NO card field at all — "
                      "2 of 339 real evidence blocks mention it (#134)")

    def test_the_gate_is_documented_for_the_worker(self):
        t = (ROOT / "agents" / "autopilot-worker.md").read_text()
        self.assertIn("subagent-stop-check-run-card", t)


# --------------------------------------------------------------------------- #
# 4. #230 — the GitHub fallback must ask WHO closed the ticket, not just
#    WHETHER it is closed.
#
# `_watchdog_closed_fetch` (airuleset.py) used to run
# `gh issue list --state closed --json number,closedAt`, which cannot tell a
# hand close, a close-by-a-bare-commit, and a close-by-a-merged-PR apart —
# every closed issue in the window looked like "merged but unreported".
# Measured live on odoo-erp before writing the fix: six of the seven
# tickets named in the ticket were hand-closed (`closer: null`), one closed
# from a commit (`closer: {"__typename": "Commit"}`) — none of the seven
# ever had anything merged, so none was ever owed a card.
#
# The fix reads each closed issue's `CLOSED_EVENT.closer` via GraphQL and
# keeps only `closer.__typename == "PullRequest" and closer.merged`. These
# tests mock `subprocess.run` with the exact response shape `gh api
# graphql` returns for that query (verified live against odoo-erp and
# camera-box), so no test here talks to the network.
# --------------------------------------------------------------------------- #

class TestWatchdogClosedFetchOnlyCountsMergedPRs(unittest.TestCase):

    def _graphql_stdout(self, issues, errors=None):
        """`issues` = [(number, closedAt_iso, closer_dict_or_None), ...] —
        the exact shape `gh api graphql`'s CLOSED_EVENT -> closer query
        returns."""
        nodes = [
            {
                "number": number,
                "closedAt": closed_at,
                "timelineItems": {"nodes": [{"closer": closer}]},
            }
            for number, closed_at, closer in issues
        ]
        body = {"data": {"repository": {"issues": {"nodes": nodes}}}}
        if errors is not None:
            body["errors"] = errors
        return json.dumps(body)

    def _fetch(self, issues, since_ts=0, returncode=0, errors=None,
              side_effect=None):
        import airuleset
        if side_effect is not None:
            with m.patch("subprocess.run", side_effect=side_effect) as run:
                result = airuleset._watchdog_closed_fetch("/tmp/whatever",
                                                           since_ts)
            return result, run
        out = m.Mock(returncode=returncode,
                     stdout=self._graphql_stdout(issues, errors=errors))
        with m.patch("subprocess.run", return_value=out) as run:
            result = airuleset._watchdog_closed_fetch("/tmp/whatever",
                                                       since_ts)
        return result, run

    # --- the three shapes named in #230 -----------------------------------

    def test_a_hand_closed_issue_contributes_nothing(self):
        # #2181's own shape from the ticket: closer is NONE at all.
        result, _ = self._fetch([(2181, "2026-08-04T11:18:52Z", None)])
        self.assertEqual(result, {})

    def test_an_unmerged_pr_contributes_nothing(self):
        result, _ = self._fetch([(500, "2026-08-04T11:18:52Z",
                                  {"__typename": "PullRequest",
                                   "merged": False})])
        self.assertEqual(result, {})

    def test_a_merged_pr_still_contributes_with_its_own_timestamp(self):
        result, _ = self._fetch([(940, "2026-08-04T15:18:18Z",
                                  {"__typename": "PullRequest",
                                   "merged": True})])
        self.assertEqual(list(result.keys()), [940])
        import burn
        expected = burn._parse_ts("2026-08-04T15:18:18Z").timestamp()
        self.assertAlmostEqual(result[940], expected, delta=1,
                               msg="the grace period (#224) reads this "
                                   "timestamp per ticket")

    # --- a close-by-bare-commit is not a merged PR either -------------------

    def test_a_commit_closer_contributes_nothing(self):
        # #1768's own shape from the ticket: closed by a plain commit, not
        # a pull request at all.
        result, _ = self._fetch([(1768, "2026-08-04T16:10:45Z",
                                  {"__typename": "Commit"})])
        self.assertEqual(result, {})

    def test_a_mix_keeps_only_the_genuinely_merged_ticket(self):
        result, _ = self._fetch([
            (2181, "2026-08-04T11:18:52Z", None),
            (1768, "2026-08-04T16:10:45Z", {"__typename": "Commit"}),
            (940, "2026-08-04T15:18:18Z",
             {"__typename": "PullRequest", "merged": True}),
        ])
        self.assertEqual(list(result.keys()), [940])

    # --- contract preserved -------------------------------------------------

    def test_the_since_window_still_filters(self):
        result, _ = self._fetch(
            [(940, "2020-01-01T00:00:00Z",
              {"__typename": "PullRequest", "merged": True})],
            since_ts=1785000000.0)
        self.assertEqual(result, {})

    def test_owner_and_name_are_resolved_via_field_placeholders(self):
        # Confirmed empirically before writing this fix: gh's `-F`/`--field`
        # (typed) values expand `{owner}`/`{repo}` from the cwd's git
        # remote; `-f`/`--raw-field` (string) values do NOT. Asserts the
        # PAIRING, not just presence — a mutant that swaps the two flags
        # (query gets `-F`, owner/name get `-f`) passed the earlier,
        # presence-only version of this test (a real adversarial-review
        # finding) even though it makes `gh` fail with "Could not resolve
        # to a Repository with the name '{owner}/{repo}'" every time.
        _, run = self._fetch([])
        args = run.call_args[0][0]
        for placeholder in ("owner={owner}", "name={repo}"):
            self.assertIn(placeholder, args)
            i = args.index(placeholder)
            self.assertGreater(i, 0, placeholder)
            self.assertEqual(args[i - 1], "-F",
                             "%s must be paired with -F/--field (typed), "
                             "the flag whose value gh actually expands — "
                             "not -f/--raw-field" % placeholder)
        joined = " ".join(args)
        self.assertNotIn("gh issue list", joined)

    def test_it_runs_in_the_repo_root_with_a_10s_timeout(self):
        import airuleset
        out = m.Mock(returncode=0, stdout=self._graphql_stdout([]))
        with m.patch("subprocess.run", return_value=out) as run:
            airuleset._watchdog_closed_fetch("/repo/root", 0)
        _, kw = run.call_args
        self.assertEqual(kw.get("cwd"), "/repo/root")
        self.assertEqual(kw.get("timeout"), 10)

    def test_non_zero_exit_degrades_to_none(self):
        result, _ = self._fetch([], returncode=1)
        self.assertIsNone(result)

    def test_a_graphql_errors_field_degrades_to_none(self):
        result, _ = self._fetch(
            [(940, "2026-08-04T15:18:18Z",
              {"__typename": "PullRequest", "merged": True})],
            errors=[{"message": "boom"}])
        self.assertIsNone(result)

    def test_a_raised_exception_degrades_to_none(self):
        result, _ = self._fetch([], side_effect=RuntimeError("no gh"))
        self.assertIsNone(result)

    def test_malformed_json_degrades_to_none(self):
        import airuleset
        out = m.Mock(returncode=0, stdout="not json")
        with m.patch("subprocess.run", return_value=out):
            result = airuleset._watchdog_closed_fetch("/tmp/x", 0)
        self.assertIsNone(result)


class TestWatchdogClosedFetchEndToEndThroughCardReconcile(unittest.TestCase):
    """The same real (mocked-subprocess) `_watchdog_closed_fetch` wired as
    job 25's `closed_fetch=` callback, proving the fix end-to-end the way
    the ticket's own incident happened: a repo with no local `Closes #N`
    trailers, whose closed tickets are a mix of hand-closed and genuinely
    merged."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-closedfetch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False, project=None):
        self.sent.append(msg)
        return "sent"

    def repo(self, name="odoo-erp"):
        r = self.tmp / name
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        (r / "f").write_text("1")
        _git(r, "add", "f")
        # no Closes trailer at all — the exact tvdole/odoo-erp signature
        _git(r, "commit", "-qm", "feat: no trailer here", ts=NOW - 3600)
        bare = self.tmp / (name + ".git")
        _git(r, "clone", "-q", "--bare", str(r), str(bare))
        _git(r, "remote", "add", "origin", str(bare))
        _git(r, "fetch", "-q", "origin")
        _git(r, "remote", "set-head", "origin", "main")
        return r

    def test_hand_closed_tickets_never_ping_but_a_merged_one_does(self):
        import airuleset
        import time

        def iso(age_s):
            # past grace (CARD_GRACE_S=1200), inside the 48h window — the
            # same shape the repo()/close_more() git helpers already use
            # for commit timestamps, just formatted as GitHub's closedAt.
            return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                 time.gmtime(NOW - age_s))

        def graphql_stdout(issues):
            nodes = [
                {"number": n, "closedAt": ts,
                 "timelineItems": {"nodes": [{"closer": closer}]}}
                for n, ts, closer in issues
            ]
            return json.dumps(
                {"data": {"repository": {"issues": {"nodes": nodes}}}})

        out = m.Mock(returncode=0, stdout=graphql_stdout([
            (2181, iso(3600), None),
            (2300, iso(7200), None),
            (1768, iso(1800), {"__typename": "Commit"}),
            (940, iso(3600), {"__typename": "PullRequest", "merged": True}),
        ]))
        r = self.repo()
        # card_reconcile also runs real `git` subprocess calls internally
        # (rev-parse, log, rev-list) — only the `gh` call is faked, every
        # other subprocess.run passes straight through to the real thing.
        real_run = subprocess.run

        def fake_run(args, *a, **kw):
            if args and args[0] == "gh":
                return out
            return real_run(args, *a, **kw)

        with m.patch("subprocess.run", side_effect=fake_run):
            wd.card_reconcile(
                NOW, None, self.state, {"s": str(r)},
                card_probe=lambda root, base: None,
                marker_ok=lambda key: False,
                send_fn=self.send,
                closed_fetch=airuleset._watchdog_closed_fetch)
        self.assertTrue(self.sent, "the genuinely merged ticket is owed a "
                                   "card")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("#940", self.sent[0])
        self.assertNotIn("#2181", self.sent[0])
        self.assertNotIn("#2300", self.sent[0])
        self.assertNotIn("#1768", self.sent[0])


# --------------------------------------------------------------------------- #
# 5. #232 — the same GitHub verification, reached through the DOMINANT path:
#    `merged_closes` (the LOCAL, unverified read) is non-empty, so #230's
#    fallback branch is never entered on its own. This is the real odoo-erp
#    shape end-to-end: local `Closes #N` trailers exist, and a real (mocked
#    subprocess) `gh api graphql` call answers with a mix of hand-closed,
#    still-open and genuinely-merged tickets.
# --------------------------------------------------------------------------- #

class TestMergedClosesCandidatesEndToEndVerifiedAgainstGitHub(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-mergedver-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False, project=None):
        self.sent.append(msg)
        return "sent"

    def repo(self, name="odoo-erp", closes=(3, 5, 7)):
        r = self.tmp / name
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        (r / "f").write_text("0")
        _git(r, "add", "f")
        _git(r, "commit", "-qm", "init", ts=NOW - 10 * DAY)
        for n in closes:
            (r / "f").write_text(str(n))
            _git(r, "add", "f")
            _git(r, "commit", "-qm", "feat: thing\n\nCloses #%d" % n,
                 ts=NOW - 3600)
        bare = self.tmp / (name + ".git")
        _git(r, "clone", "-q", "--bare", str(r), str(bare))
        _git(r, "remote", "add", "origin", str(bare))
        _git(r, "fetch", "-q", "origin")
        _git(r, "remote", "set-head", "origin", "main")
        return r

    def _graphql(self, issues):
        nodes = [
            {"number": n, "closedAt": ts,
             "timelineItems": {"nodes": [{"closer": closer}]}}
            for n, ts, closer in issues
        ]
        return json.dumps(
            {"data": {"repository": {"issues": {"nodes": nodes}}}})

    def _run_with_gh(self, r, gh_out):
        import airuleset
        real_run = subprocess.run

        def fake_run(args, *a, **kw):
            if args and args[0] == "gh":
                return gh_out
            return real_run(args, *a, **kw)

        with m.patch("subprocess.run", side_effect=fake_run):
            return wd.card_reconcile(
                NOW, None, self.state, {"s": str(r)},
                card_probe=lambda root, base: None,
                marker_ok=lambda key: False,
                send_fn=self.send,
                closed_fetch=airuleset._watchdog_closed_fetch)

    def test_only_the_genuinely_merged_ticket_pings_the_rest_stay_silent(self):
        import time

        def iso(age_s):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age_s))

        r = self.repo(closes=(3, 5, 7))
        gh_out = m.Mock(returncode=0, stdout=self._graphql([
            (3, iso(3600), None),                                          # hand-closed
            (7, iso(3600), {"__typename": "PullRequest", "merged": True}), # genuinely merged
            # #5 never appears at all — still OPEN on GitHub
        ]))
        self._run_with_gh(r, gh_out)
        self.assertTrue(self.sent)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("#7", self.sent[0])
        self.assertNotIn("#3", self.sent[0])
        self.assertNotIn("#5", self.sent[0])

    def test_gh_failure_drops_the_ping_this_sweep_but_a_later_success_still_reports(self):
        r = self.repo(closes=(3,))
        boom = m.Mock(returncode=1, stdout="")
        self._run_with_gh(r, boom)
        self.assertEqual(self.sent, [],
                         "an unverifiable candidate never pings")

        import time

        def iso(age_s):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age_s))

        gh_out = m.Mock(returncode=0, stdout=self._graphql([
            (3, iso(3600), {"__typename": "PullRequest", "merged": True})]))
        self._run_with_gh(r, gh_out)
        self.assertTrue(self.sent, "not swallowed forever — a later "
                                   "successful verification must report it")


if __name__ == "__main__":
    unittest.main()
