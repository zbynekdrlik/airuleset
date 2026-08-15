"""Watchdog job 8 — bounce backstop (2026-07-19).

Incident: gatekeeper review returns tickets (`prio:bounce`) AFTER the sub-dev's
autopilot loop ended — nobody picks them up and the user must intervene (4
bounced david tickets sat re-handed-off; montalu had no tmux server at all, so
the gatekeeper's ssh/tmux nudge had nowhere to land). The watchdog is the
machine-local backstop: every ~30 min it checks the repos this box recently
worked (tickets-status cache roots) for open prio:bounce tickets scoped to this
box's stream; found + live IDLE claude pane → type a nudge (the skill's
nudge-ack dispatches a worker); found + NO pane → ONE deduped Discord ping.
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar
import watchdog as wd

IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")


def seed_repo_cache(home, root, name):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
        {"open": 1, "name": name, "root": root, "ts": int(time.time())}))


# --------------------------------------------------------------------------- #
# #497 -- bounce_backstop's BARE-box send now goes through the transcript-proof
# `send_verified` (the swallowed-submit-books-as-delivered fix). These tests
# assert bounce_backstop's own JOB LOGIC (which repo is nudged, busy-pane skip,
# budget, dedup, cadence), not the keystroke MECHANICS -- so `send_verified` is
# replaced module-wide by a happy-path stand-in that TYPES + submits the nudge
# via the same tmux `run` (single -l, mirroring the pre-#497 send_continue
# shape) and reports the submit VERIFIED. Every existing `tmux.typed()` /
# persist-order assertion then holds unchanged. The real transcript-proof
# verification / chunk-typing / undo-on-swallow is covered end to end in
# test_send_verified.py; the swallowed-submit (False) job handling in
# test_send_verified_adoption.py.
def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None, logs=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


_SV_PATCHER = None


def setUpModule():
    global _SV_PATCHER
    _SV_PATCHER = m.patch.object(wd, "send_verified", _typing_send_verified)
    _SV_PATCHER.start()


def tearDownModule():
    if _SV_PATCHER is not None:
        _SV_PATCHER.stop()


class FakeTmux:
    """Static capture by default. `model_stash=True` additionally models Claude
    Code's SINGLE-SLOT prompt stash (Ctrl+S) and the input box, so the
    stash-around delivery path (`_try_stash_nudge` -> `deliver_with_stash`) is
    exercised against a pane that actually REACTS to keystrokes.

    Without the model every capture is frozen, so a Ctrl+S never appears to do
    anything and the delivery always aborted before typing — which made a pane
    holding a user draft LOOK like it refused the nudge, when in production the
    draft is parked and the nudge is delivered around it (issue 35, which is
    what `_try_stash_nudge` exists for)."""

    def __init__(self, panes=None, captured=IDLE, model_stash=False):
        self.panes = panes or []            # [(pane_id, cwd)]
        self.captured = captured
        self.sent = []
        self.model_stash = model_stash
        self.stash = None
        self.submitted = []
        self._box = self._box_of(captured)

    @staticmethod
    def _box_of(cap):
        for ln in cap.splitlines():
            if ln.strip().startswith("❯"):
                return ln.strip()[1:].strip()
        return ""

    def _render(self):
        out = []
        for ln in self.captured.splitlines():
            if ln.strip().startswith("❯"):
                out.append("❯\xa0" + self._box if self._box else "❯\xa0")
            elif ln.strip().startswith("ctx ") and self.stash is not None:
                out.append(ln + "  " + wd.STASH_MARKER)
            else:
                out.append(ln)
        return "\n".join(out) + "\n"

    def _key(self, k):
        if k == "C-s":
            if self._box and self.stash is None:
                self.stash, self._box = self._box, ""
            elif not self._box and self.stash is not None:
                self._box, self.stash = self.stash, None
        elif k == "Enter" and self._box:
            self.submitted.append(self._box)
            self._box = ""

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self._render() if self.model_stash else self.captured
        if "display" in j:
            return "0"
        if self.model_stash and argv[:2] == ["tmux", "send-keys"]:
            if "-l" in argv:
                self._box += argv[-1]
            else:
                for k in argv[4:]:
                    self._key(k)
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestBounceBackstop(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _go(self, state, tickets, panes=None, captured=IDLE, now=None):
        tmux = FakeTmux(panes if panes is not None else [("%1", self.root)],
                        captured)
        logs = wd.bounce_backstop(
            now or time.time(), tmux, state, self._send, home=self.home,
            gh_fetch=lambda root: tickets, cross_stream_repos={"demo"})
        return logs, tmux

    def test_nudges_idle_pane_with_ticket_numbers(self):
        logs, tmux = self._go({}, [1705, 1434])
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertIn("#1705", typed[0])
        self.assertIn("#1434", typed[0])
        self.assertIn("autopilot-worker", typed[0])   # points at the skill flow
        self.assertTrue(any("bounce-nudge" in ln for ln in logs), logs)
        self.assertFalse(self.pings)                  # pane existed → no Discord

    def test_busy_pane_is_left_alone(self):
        logs, tmux = self._go({}, [1705], captured=BUSY)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_pane_pings_discord_once(self):
        state = {}
        logs, _ = self._go(state, [1705, 1434], panes=[])
        self.assertEqual(len(self.pings), 1)
        body = self.pings[0][0]
        self.assertIn("demo", body)
        self.assertIn("2", body)                      # count of waiting tickets
        # second sweep, same set → deduped (no second ping)
        state["bounce"]["last_check"] = 0             # re-open the cadence gate
        self._go(state, [1705, 1434], panes=[])
        self.assertEqual(len(self.pings), 1)

    def test_no_pane_ping_carries_the_stream_qualified_project_label(self):
        # #369: the no-pane Discord fallback must route via `send_fn`'s new
        # `project=` kwarg, so it lands in the project's own thread instead
        # of the owner's plain pile -- stream-qualified the SAME way a
        # run-card / idle ping for the SAME repo checkout on THIS box would.
        state = {}
        with m.patch("getpass.getuser", return_value="david2"):
            self._go(state, [1705], panes=[])
        self.assertEqual(self.pings[0][1].get("project"), "demo-david2")

    def test_worktree_root_covered_by_main_checkout_pane(self):
        # 2026-07-23 false ping: David's claude ran in the MAIN checkout while
        # the cached bounce root was the repo's .claude/worktrees/<agent> path
        # — bounce tickets are per-repo, so that pane COVERS the worktree root
        # and no "nebeží žiadna Claude session" Discord ping may fire.
        wt_root = str(Path(self.root) / ".claude" / "worktrees" / "agent-x")
        Path(wt_root).mkdir(parents=True)
        seed_repo_cache(self.home, wt_root, "demo-wt")
        # pane sits at the MAIN checkout; it is BUSY so the nudge branch stays
        # quiet too — the assertion is purely "no Discord ping for wt_root".
        self._go({}, [1705], panes=[("%1", self.root)], captured=BUSY)
        self.assertFalse(self.pings, self.pings)

    def test_changed_ticket_set_renudges(self):
        state = {}
        _, t1 = self._go(state, [1705])
        state["bounce"]["last_check"] = 0
        logs, t2 = self._go(state, [1705, 1434])
        self.assertTrue(t2.typed(), "new bounce ticket must re-nudge")

    def test_same_set_does_not_renudge_within_window(self):
        state = {}
        self._go(state, [1705])
        state["bounce"]["last_check"] = 0
        _, t2 = self._go(state, [1705])
        self.assertFalse(t2.typed())

    def test_cadence_gated(self):
        state = {}
        calls = []
        now = time.time()
        for _ in range(2):
            wd.bounce_backstop(now, FakeTmux([]), state, self._send,
                               home=self.home, cross_stream_repos={"demo"},
                               gh_fetch=lambda root: calls.append(root) or [])
        self.assertEqual(len(calls), 1)

    def test_gh_error_is_failsafe(self):
        logs, tmux = self._go({}, None)               # gh_fetch error → None
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_tickets_clears_state(self):
        state = {}
        self._go(state, [1705], panes=[])
        state["bounce"]["last_check"] = 0
        self._go(state, [], panes=[])
        self.assertNotIn("demo", (state.get("bounce") or {}).get("seen", {}))


class TestBounceBackstopSweepBudget(unittest.TestCase):
    """#255 Fix 1: bounce_backstop's own per-target loop previously had NO
    wall-clock self-bound at all -- unlike the per-transcript pane loop,
    which #172 already protects via time_fn/sweep_deadline. This closes
    that gap so job 8 can never be the thing that runs long enough (many
    cross-stream repos, each a real `gh` fetch) to be the reason a hard
    systemd kill lands mid-delivery for some LATER target. The check sits
    strictly BETWEEN targets -- checked BEFORE a target's delivery starts,
    never nested inside one target's own send_continue (a single atomic
    type+submit pair) -- so a target already being delivered always
    finishes; only a NOT-YET-STARTED target is deferred to the next sweep."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _two_target_setup(self):
        root1 = str(Path(self.home) / "devel" / "demo1")
        root2 = str(Path(self.home) / "devel" / "demo2")
        Path(root1).mkdir(parents=True)
        Path(root2).mkdir(parents=True)
        seed_repo_cache(self.home, root1, "demo1")
        seed_repo_cache(self.home, root2, "demo2")
        tmux = FakeTmux([("%1", root1), ("%2", root2)], IDLE)
        return tmux

    def test_stops_before_starting_a_new_target_once_budget_exhausted(self):
        tmux = self._two_target_setup()
        clock = [0.0]

        def time_fn():
            return clock[0]

        def fetch(root):
            clock[0] += 10          # each fetch "costs" real wall-clock time
            return [1705]

        logs = wd.bounce_backstop(
            time.time(), tmux, {}, self._send, home=self.home,
            gh_fetch=fetch, cross_stream_repos={"demo1", "demo2"},
            time_fn=time_fn, sweep_deadline=5)
        typed = tmux.typed()
        self.assertEqual(len(typed), 1, typed)   # only the FIRST target delivered
        self.assertTrue(any("bounce-budget-exceeded" in ln for ln in logs), logs)

    def test_deferred_target_is_not_dedup_marked_so_it_retries_next_sweep(self):
        tmux = self._two_target_setup()
        clock = [0.0]

        def time_fn():
            return clock[0]

        def fetch(root):
            clock[0] += 10
            return [1705]

        state = {}
        wd.bounce_backstop(
            time.time(), tmux, state, self._send, home=self.home,
            gh_fetch=fetch, cross_stream_repos={"demo1", "demo2"},
            time_fn=time_fn, sweep_deadline=5)
        # demo1 (processed) is marked seen; demo2 (deferred by budget) is not
        # -- a skipped target must be retried, never silently dropped.
        seen = (state.get("bounce") or {}).get("seen") or {}
        self.assertIn("demo1", seen, seen)
        self.assertNotIn("demo2", seen, seen)

    def test_no_deadline_given_means_unbounded_as_before(self):
        # Backward compatible: every existing caller (and every OTHER
        # existing test in this file) omits time_fn/sweep_deadline entirely
        # -- that must behave exactly as it always has, no budget check at
        # all.
        tmux = self._two_target_setup()
        logs = wd.bounce_backstop(
            time.time(), tmux, {}, self._send, home=self.home,
            gh_fetch=lambda root: [1705],
            cross_stream_repos={"demo1", "demo2"})
        self.assertEqual(len(tmux.typed()), 2, tmux.typed())
        self.assertFalse(any("bounce-budget-exceeded" in ln for ln in logs), logs)


class TestBounceQuals(unittest.TestCase):
    """Scoping is derived from the PANE's home dir, not the watchdog user:
    montalu's claude runs inside NEWLEVEL's tmux (a `sudo su - montalu`
    window), so newlevel's watchdog serves that pane — its cwd
    /home/montalu/... names the stream, and the stream LABEL (the #1599
    convention) scopes the query; @me is useless (gh identity is the same
    zbynekdrlik account across boxes)."""

    def test_stream_home_scopes_by_label(self):
        self.assertEqual(wd._bounce_quals("/home/montalu/devel/odoo-erp"),
                         ["label:stream:montalu"])
        self.assertEqual(wd._bounce_quals("/home/david/devel/x"),
                         ["label:stream:david"])

    def test_montalu_family_home_scopes_by_own_label(self):
        # airuleset#251: montalu2/3/4 work on odoo-erp (a _CROSS_STREAM_REPOS
        # member) — each pane must scope to ITS OWN stream:<user> label, not
        # fall through to the full-authority exclude-all branch.
        for u in ("montalu2", "montalu3", "montalu4"):
            self.assertEqual(wd._bounce_quals("/home/%s/devel/odoo-erp" % u),
                             ["label:stream:%s" % u], u)

    def test_david_family_home_scopes_by_own_label(self):
        # airuleset#326 (adversarial-review MAJOR finding, live-triggered):
        # david2/david3/david4 ALSO work on odoo-erp (a _CROSS_STREAM_REPOS
        # member) from day one -- unlike simap/miva1 (which merge nowhere and
        # were correctly left out of _REDUCED_STREAM_USERS), the matching
        # precedent here is montalu2/3/4 above, whose entries were added AT
        # onboarding for exactly this reason. Without its own entry, a
        # david2/3/4 pane fell through to the full-authority exclude-all
        # branch instead of its own label -- confirmed live before this fix:
        # `_bounce_quals("/home/david2/devel/odoo-erp")` returned the
        # exclude-all fragment, not `["label:stream:david2"]`.
        for u in ("david2", "david3", "david4"):
            self.assertEqual(wd._bounce_quals("/home/%s/devel/odoo-erp" % u),
                             ["label:stream:%s" % u], u)

    def test_full_authority_home_excludes_subdev_streams(self):
        # Live dry-run finding (2026-07-19): an unscoped full-box query picked
        # up DAVID's stream bounces from newlevel's dev1 checkout and would
        # have pinged the wrong person — the sub-dev's own box nudges those.
        # Full authority = the core slice (same exclusions as tickets-status).
        quals = wd._bounce_quals("/home/newlevel/devel/demo")
        self.assertEqual(len(quals), 1)
        for u in ("david", "marek", "montalu", "montalu2", "montalu3",
                  "montalu4", "david2", "david3", "david4"):
            self.assertIn("-label:stream:%s" % u, quals[0])

    def test_david_family_pane_is_not_a_gkreq_supervisor_root(self):
        # airuleset#326 (adversarial-review MAJOR finding, live-triggered):
        # _gkreq_supervisor_root must be False under a reduced stream's own
        # HOME -- nudging the REQUESTER about its own request is backwards
        # (the inverse of _bounce_quals' gatekeeper skip, same docstring).
        # Before this fix: `_gkreq_supervisor_root("/home/david2/...")`
        # returned True (treated as a full-authority supervisor).
        for u in ("david2", "david3", "david4"):
            self.assertFalse(wd._gkreq_supervisor_root("/home/%s/devel/odoo-erp" % u), u)
        self.assertTrue(wd._gkreq_supervisor_root("/home/newlevel/devel/odoo-erp"))


class TestCrossStreamRepoScope(unittest.TestCase):
    """#89: live incident (restreamer, 2026-07-26) — a restreamer session
    added `prio:bounce` to its OWN ticket #337 as a generic priority marker
    (author + label both zbynekdrlik, nothing to do with the gatekeeper<->
    sub-dev flow). Job 8's `_bounce_quals` scopes WHO the query excludes
    (sub-dev streams) but never WHICH REPOS the label can even mean anything
    in — a bare `prio:bounce` on any repo the box has ever worked matched.
    `prio:bounce` only has protocol meaning inside repos that actually
    PARTICIPATE in the cross-stream flow (`## Cross-stream protocol`,
    skills/autopilot/SKILL.md) — job 8 must never even query, let alone
    nudge, a repo that isn't one of them."""

    def test_repo_in_cross_stream_flow_helper(self):
        self.assertTrue(wd._repo_in_cross_stream_flow(
            "/home/newlevel/devel/odoo-erp"))
        self.assertFalse(wd._repo_in_cross_stream_flow(
            "/home/newlevel/devel/restreamer"))

    def test_helper_respects_explicit_override(self):
        self.assertTrue(wd._repo_in_cross_stream_flow(
            "/home/newlevel/devel/demo", cross_stream_repos={"demo"}))
        self.assertFalse(wd._repo_in_cross_stream_flow(
            "/home/newlevel/devel/odoo-erp", cross_stream_repos={"demo"}))

    def test_non_cross_stream_repo_is_never_nudged(self):
        # the exact restreamer #337 shape: a genuine open ticket carrying
        # the bare label, in a repo that has NOTHING to do with the
        # cross-stream flow. gh_fetch stands in for a real GitHub query
        # that WOULD return it — job 8 must not even ask, let alone nudge.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "restreamer")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "restreamer")
            pings = []
            fetch_calls = []
            logs = wd.bounce_backstop(
                time.time(), FakeTmux([("%1", root)], IDLE), {},
                lambda b, **kw: pings.append(b), home=home,
                gh_fetch=lambda r: fetch_calls.append(r) or [337])
            self.assertFalse(fetch_calls, "must never even query a "
                             "non-cross-stream repo")
            self.assertFalse(pings)
            self.assertTrue(
                any("not-cross-stream" in ln or "skip" in ln for ln in logs),
                logs)

    def test_cross_stream_repo_still_nudges_with_real_registry(self):
        # the genuine odoo-erp case must be UNCHANGED — no override needed,
        # the real default registry covers it.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "odoo-erp")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "odoo-erp")
            tmux = FakeTmux([("%1", root)], IDLE)
            logs = wd.bounce_backstop(
                time.time(), tmux, {}, lambda b, **kw: None, home=home,
                gh_fetch=lambda r: [1705])
            self.assertTrue(tmux.typed(), "a genuine cross-stream repo must "
                            "still nudge with no override")
            self.assertTrue(any("bounce-nudge" in ln for ln in logs), logs)


class TestGhEnvTokenFallback(unittest.TestCase):
    def test_bashrc_export_is_picked_up(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text(
                "# stuff\nexport GH_TOKEN=ghp_testtoken123\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertEqual(env.get("GH_TOKEN"), "ghp_testtoken123")

    def test_existing_env_token_untouched(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text("export GH_TOKEN=other\n")
            env = wd._gh_env(home, base={"GH_TOKEN": "keepme"})
            self.assertEqual(env.get("GH_TOKEN"), "keepme")

    def test_no_bashrc_is_failsafe(self):
        with TemporaryDirectory() as home:
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertNotIn("GH_TOKEN", env)


WORKFLOW_WAIT = ("● Review beží\n"
                 "  ⏳ WORKING: review Workflow beží — verdikt čaká. Nič netreba.\n"
                 "✻ Waiting for 1 dynamic workflow to finish\n"
                 "❯ \n  ctx ███░  ultracode\n")
GOAL_ACTIVE = ("● Hotovo, pokračujem ďalším ticketom.\n"
               "❯ \n  ctx ███░  ◎ /goal active (58m)\n")
NUDGED_ALREADY = ("● predtým prišiel nudge\n"
                  "❯ bounce-backstop: open prio:bounce tickets #1 in demo — x\n"
                  "❯ \n")


class TestNeverTypeIntoWorkingSession(unittest.TestCase):
    """2026-07-19 LIVE incident (user: 'dokolecka sa mu nieco pastuje do
    promptu pocas behu!!!'): the gatekeeper session sat at a `❯` prompt while
    WAITING on a background review Workflow (CC renders a free prompt then) and
    job 8 pasted the same nudge 4×. The safe-to-type gate must refuse: a pane
    showing a background-wait spinner (✻ / esc to interrupt), an armed /goal
    (◎ /goal active in the statusline), a still-visible previous nudge (belt
    against state loss), and a transcript whose last marker is ⏳ WORKING."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")
        self.pings = []

    def _go(self, captured, state=None):
        tmux = FakeTmux([("%1", self.root)], captured)
        wd.bounce_backstop(time.time(), tmux, state if state is not None else {},
                           lambda body, **kw: self.pings.append(body),
                           home=self.home, gh_fetch=lambda root: [1705],
                           cross_stream_repos={"demo"})
        return tmux

    def test_background_workflow_wait_is_not_typed_into(self):
        self.assertFalse(self._go(WORKFLOW_WAIT).typed())

    def test_armed_goal_loop_is_not_typed_into(self):
        # the label alone is the insertion — the loop re-queries each turn
        self.assertFalse(self._go(GOAL_ACTIVE).typed())

    def test_visible_previous_nudge_blocks_repeat_even_with_lost_state(self):
        self.assertFalse(self._go(NUDGED_ALREADY).typed())

    def test_working_marker_in_transcript_blocks_nudge(self):
        # transcript readable + last marker ⏳ → session mid-flight, never type
        proj = Path(self.home, ".claude", "projects",
                    wd.encode_project_dir(self.root))
        proj.mkdir(parents=True)
        entry = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "robím\n⏳ WORKING: čakám na worker"}]}}
        (proj / "s1.jsonl").write_text(json.dumps(entry) + "\n")
        tmux = FakeTmux([("%1", self.root)], IDLE)
        wd.bounce_backstop(time.time(), tmux, {},
                           lambda body, **kw: self.pings.append(body),
                           home=self.home, gh_fetch=lambda root: [1705],
                           projects_dir=str(Path(self.home, ".claude", "projects")),
                           cross_stream_repos={"demo"})
        self.assertFalse(tmux.typed())

    def test_truly_resting_session_is_nudged(self):
        self.assertTrue(self._go(IDLE).typed())


class TestGatekeeperNeverBounceNudged(unittest.TestCase):
    def test_gatekeeper_home_is_skipped_entirely(self):
        # bounce lane direction is reviewer→sub-dev; the gatekeeper is the
        # reviewer — nudging IT about bounces it filed is backwards (the live
        # incident). No quals = no query = no nudge for /home/gatekeeper/.
        self.assertEqual(wd._bounce_quals("/home/gatekeeper/devel/odoo-erp"), [])


class TestStatePersistedBeforeTyping(unittest.TestCase):
    def test_persist_callback_fires_before_send(self):
        # TimeoutStartSec killed the run AFTER the nudge but BEFORE run_once's
        # save_state → no dedup memory → the 4× repeat. Job 8 must persist its
        # seen-set BEFORE any keystroke/ping leaves the process.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo")
            order = []
            tmux = FakeTmux([("%1", root)], IDLE)
            real_call = tmux.__call__

            def spy(argv, timeout=8):
                if "-l" in argv:
                    order.append("send")
                return real_call(argv, timeout)
            wd.bounce_backstop(time.time(), spy, {}, lambda b, **k: None,
                               home=home, gh_fetch=lambda r: [7],
                               persist=lambda: order.append("persist"),
                               cross_stream_repos={"demo"})
            self.assertIn("persist", order)
            self.assertIn("send", order)
            self.assertLess(order.index("persist"), order.index("send"))


DONE_PARKED = ("● Hotový beh.\n"
               "  ✅ DONE: celý backlog odovzdaný — 3 čakajú na review.\n"
               "✻ Worked for 1h 5m · 1 monitor still running\n"
               "❯ \n  ctx ███░  ◎ /goal active (3h)  caveman\n")


class TestDoneParkedLoopIsNudged(unittest.TestCase):
    """2026-07-20 deadlock: david's session sat at ✅ DONE under a SATISFIED
    old /goal — the ◎ /goal indicator stays lit but no turn will ever fire, so
    'the label alone is the insertion' was a dead assumption and the bounce
    rotted while the gatekeeper waited. A pane whose last output is ✅ DONE is
    AT REST — the ◎ /goal + turn-summary ✻ lines must not block the nudge.
    (A pane with USER-TYPED unsubmitted text gets the nudge STASHED AROUND
    that text — parked, delivered, auto-restored — and its text is never
    submitted. That has been the behaviour since issue 35 shipped
    `_try_stash_nudge`; the assertion here used to read "always refuses"
    only because a frozen capture made every Ctrl+S look like a no-op.)"""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")

    def _go(self, captured, model_stash=False):
        tmux = FakeTmux([("%1", self.root)], captured, model_stash=model_stash)
        wd.bounce_backstop(time.time(), tmux, {}, lambda b, **k: None,
                           home=self.home, gh_fetch=lambda r: [1528],
                           cross_stream_repos={"demo"})
        return tmux

    def test_done_parked_goal_session_gets_the_nudge(self):
        self.assertTrue(self._go(DONE_PARKED).typed())

    def test_live_workflow_wait_still_refused(self):
        self.assertFalse(self._go(WORKFLOW_WAIT).typed())

    def test_armed_working_loop_still_refused(self):
        self.assertFalse(self._go(GOAL_ACTIVE.replace(
            "● Hotovo, pokračujem ďalším ticketom.",
            "● Dispatchol som workera, pokračujem.")).typed())

    def test_user_typed_text_is_stashed_around_and_never_submitted(self):
        parked_with_input = DONE_PARKED.replace(
            "❯ \n", "❯ chekni ci nemas nieco nove\n")
        tmux = self._go(parked_with_input, model_stash=True)
        self.assertTrue(tmux.typed(), tmux.sent)
        self.assertEqual(len(tmux.submitted), 1, tmux.sent)
        self.assertIn("bounce-backstop:", tmux.submitted[0])
        self.assertNotIn("chekni ci nemas nieco nove", tmux.submitted[0])
        self.assertEqual(tmux.stash, "chekni ci nemas nieco nove",
                         "the user's draft stays parked for CC to auto-restore")


class TestGhEnvCatSubstitution(unittest.TestCase):
    def test_cat_command_substitution_is_resolved(self):
        # david's real .bashrc (found 2026-07-20, the 401 root cause):
        #   export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)
        # A literal-value regex captured the string '$(cat' and gh got 401 —
        # the backstop silently found nothing while #1801 rotted for 3 hours.
        with TemporaryDirectory() as home:
            Path(home, ".config").mkdir()
            Path(home, ".config", "gh-token").write_text("ghp_realtoken42\n")
            Path(home, ".bashrc").write_text(
                "export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertEqual(env.get("GH_TOKEN"), "ghp_realtoken42")

    def test_unresolvable_substitution_is_failsafe(self):
        with TemporaryDirectory() as home:
            Path(home, ".bashrc").write_text(
                "export GH_TOKEN=$(some-helper --fetch)\n")
            env = wd._gh_env(home, base={"PATH": "/usr/bin"})
            self.assertNotIn("GH_TOKEN", env)   # never a garbage literal


class TestForeignTmuxUserNeverPings(unittest.TestCase):
    def test_montalu_user_watchdog_no_longer_skips_job_8(self):
        # Historical: a false Discord ping 2026-07-20 ("nebeží žiadna Claude
        # session" while the montalu session actually ran INSIDE NEWLEVEL's
        # tmux on dev1) — montalu had no tmux server of its own BY DESIGN, so
        # its watchdog could never see the pane and job 8 had to no-op.
        # Since the subdev migration (airuleset#33 + odoo-erp#1895,
        # 2026-07-24) montalu runs in its OWN tmux session on subdev, so job
        # 8 must now run NORMALLY for it — the empty _FOREIGN_TMUX_USERS
        # mechanism stays wired for a future shared-tmux stream, but
        # currently skips nobody.
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo")
            pings = []
            logs = wd.bounce_backstop(
                time.time(), FakeTmux([]), {},
                lambda b, **k: pings.append(b), home=home,
                gh_fetch=lambda r: [1727], user="montalu",
                cross_stream_repos={"demo"})
            self.assertTrue(pings, "montalu is no longer foreign-tmux — "
                            "job 8 must run normally, not no-op")
            self.assertTrue(any("bounce-ping" in ln for ln in logs), logs)


class TestFetchBounceTicketsExcludesOpsChannel(unittest.TestCase):
    """#364 (follow-up to #362): job 8's own bounce-nudge candidate query
    (`_fetch_bounce_tickets`, the `gh_fetch` default) still hand-rolled a
    bare `-label:autopilot-skip` with no `ops-channel` awareness — #362
    fixed the `/goal` stop-proof (`core-quals`/`slice-quals` in
    airuleset.py) but never touched this watchdog-side query. A PERMANENT
    ops-channel ticket (a stream's own self-declared "never auto-closes"
    channel) that also happened to carry `prio:bounce` would still surface
    here as a nudge candidate."""

    def test_query_excludes_ops_channel(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="[]")

        with TemporaryDirectory() as root:
            with m.patch("subprocess.run", side_effect=run):
                wd._fetch_bounce_tickets(root)
        self.assertTrue(calls, "no gh call recorded")
        flat = json.dumps(calls)
        self.assertIn("-label:ops-channel", flat)


class TestBounceDiscordRepingSurvivesNotifyDedup(unittest.TestCase):
    """#360 — the Discord-fallback ping (no live session) built its dedup key
    from the ticket-set CONTENT (`bounce:%s:%s` % (name, tick_str)), so for an
    UNCHANGED bounce set every 6h re-ping carried a BYTE-IDENTICAL key.
    `notify.send`'s own SEPARATE, pre-existing 14-day marker TTL
    (`notify._DEDUP_TTL_S` via `_dedup_claim`'s `O_CREAT|O_EXCL`) then swallowed
    every re-ping past the first — job 8's own 6h `BOUNCE_RENUDGE_SECONDS`
    re-nudge cadence never actually reached Discord (the identical class #353
    fixed for the sibling gk_request_backstop, `cross_stream.py:723-746`). The
    fix mirrors #353's proven dedup-key shape: fresh per DECISION INSTANT
    (`bounce:%s:%d` % (name, int(now))), so job 8's own cadence is the sole
    authority over whether a Discord send happens."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        seed_repo_cache(self.home, self.root, "demo")

    def _reping(self, send, state, now):
        # no pane -> the Discord-fallback branch; SAME unchanged ticket set
        tmux = FakeTmux([], IDLE)
        return wd.bounce_backstop(
            now, tmux, state, send, home=self.home,
            gh_fetch=lambda root: [1705], cross_stream_repos={"demo"})

    def test_reping_after_window_is_not_swallowed_by_notify_dedup(self):
        # Model notify.send's real dedup: a key already claimed within its TTL
        # is swallowed ('dedup'), never re-posted -- exactly notify._dedup_claim's
        # O_CREAT|O_EXCL behaviour over its 14-day marker window (the TTL never
        # elapses across the 6h span of this test).
        claimed = set()
        calls = []              # (body, dedup_key) per send_fn invocation
        delivered = []          # bodies that actually reached Discord ('sent')

        def send(body, dedup_key=None, **kw):
            calls.append((body, dedup_key))
            if dedup_key in claimed:
                return "dedup"
            claimed.add(dedup_key)
            delivered.append(body)
            return "sent"

        state = {}
        t0 = 1_000_000.0
        self._reping(send, state, t0)                 # first Discord ping
        # 6h+ later, SAME unchanged set, still no session -> job 8 decides to
        # re-ping (its own `same and fresh` 6h window has elapsed).
        state["bounce"]["last_check"] = 0             # reopen the cadence gate
        t1 = t0 + wd.BOUNCE_RENUDGE_SECONDS + 60
        logs2 = self._reping(send, state, t1)

        # BOTH re-pings must actually reach Discord -- the second must NOT be
        # swallowed by notify's own 14-day marker (the #360 defect: the two
        # keys were byte-identical, so notify.send returned 'dedup' forever).
        self.assertEqual(len(delivered), 2, calls)
        # ...because the two dedup keys are DISTINCT (fresh per decision instant),
        # not the byte-identical content-based key the old code built.
        keys = [k for _b, k in calls]
        self.assertEqual(len(set(keys)), 2, keys)
        # #360 observability (gk-request sibling parity, cross_stream.py:761):
        # the journal line records the send RESULT, so a live box can tell a
        # delivered re-ping ('sent') from one notify swallowed ('dedup') --
        # exactly the delivery-vs-swallow distinction this fix exists to make
        # verifiable per the repo's live-box verification bar.
        self.assertTrue(any("(send='sent')" in ln for ln in logs2), logs2)


if __name__ == "__main__":
    unittest.main()
