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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402
import watchdog as wd                                    # noqa: E402

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

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "owner": owner, "dedup": dedup_key})
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

    def run(self, cwds, **kw):
        kw.setdefault("card_probe", lambda root, base: None)
        kw.setdefault("marker_ok", lambda key: key in self.delivered)
        kw.setdefault("send_fn", self.send)
        return wd.card_reconcile(NOW, None, self.state,
                                 {("s%d" % i): str(c)
                                  for i, c in enumerate(cwds)}, **kw)

    def test_an_unreported_merged_ticket_pings(self):
        r = self.repo(closes=(3, 4))
        logs = self.run([r])
        self.assertTrue(self.sent, logs)
        self.assertIn("#3", self.sent[0]["msg"])
        self.assertIn("#4", self.sent[0]["msg"])

    def test_a_reported_ticket_is_silent(self):
        r = self.repo(closes=(3,))
        self.delivered.add("proj#3")
        self.run([r])
        self.assertEqual(self.sent, [])

    def test_a_claimed_but_undelivered_card_still_counts_as_unreported(self):
        r = self.repo(closes=(3,))
        # marker_ok is `notify.marker_delivered` in production; an 'error'
        # marker reads False, so the ticket is still unreported.
        self.run([r])
        self.assertTrue(self.sent)

    def test_a_merge_older_than_the_window_is_ignored(self):
        r = self.repo(closes=(3,), age=10 * DAY)
        self.run([r])
        self.assertEqual(self.sent, [],
                         "an old merge is history, not an actionable gap")

    def test_a_repo_with_no_closing_merges_is_silent(self):
        r = self.repo(closes=())
        self.run([r])
        self.assertEqual(self.sent, [])

    def test_a_non_git_cwd_is_silent(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.run([d])
        self.assertEqual(self.sent, [])

    def test_the_ping_is_deduped_per_repo_per_window(self):
        r = self.repo(closes=(3,))
        self.run([r])
        self.run([r])
        self.assertEqual(len(self.sent), 1,
                         "a daily reminder, not one per 60s sweep")

    def test_detection_is_logged_every_sweep_even_when_the_ping_is_deduped(self):
        r = self.repo(closes=(3,))
        self.run([r])
        logs = self.run([r])
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
        self.run([r, r, r])
        self.assertEqual(len(self.sent), 1)

    def test_a_missing_send_fn_never_marks_it_pinged(self):
        r = self.repo(closes=(3,))
        self.run([r], send_fn=None)
        self.run([r])
        self.assertEqual(len(self.sent), 1,
                         "nothing was delivered, so the user is still owed it")

    def test_dry_run_delivers_nothing_and_records_nothing(self):
        r = self.repo(closes=(3,))
        self.run([r], dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.get("card_unreported"), None)

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
        for p in ("/tmp/claude-discord-pending-%s" % self.sid,
                  "/tmp/claude-discord-cardchk-%s" % self.sid):
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
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


# --------------------------------------------------------------------------- #
# 4. the evidence block gains the field the gate reads
# --------------------------------------------------------------------------- #

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


if __name__ == "__main__":
    unittest.main()
