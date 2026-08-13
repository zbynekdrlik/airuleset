"""Canonical stream→supervisor action channel (airuleset #30, 2026-07-24).

Streams (montalu/david/marek) kept needing a gatekeeper/supervisor ACTION
(box access, workflow re-dispatch, infra) and the only real path was the USER
as a middleman — 3× in one day, explicitly rejected ("je to blbé, že robím
prostredníka medzi vami"). The canonical mechanism, owned by airuleset
(odoo-erp#2085 becomes a repo-adapter):

- REQUEST = a ticket labeled `needs-gatekeeper` in the upstream repo, filed
  via `airuleset.py gk-request` (label 403 → the `GATEKEEPER-ACTION:` title/
  comment prefix fallback for read-only-fork streams).
- DELIVERY = watchdog job (gk_request_backstop, the mirror of job 8): ~30 min
  sweep; IDLE supervisor pane gets a typed nudge, BUSY pane gets NOTHING (the
  label alone queues it for the master loop), no live pane → ONE deduped
  Discord ping. Reduced-stream homes are never nudged.
- VISIBILITY = counted inside the full-authority box's own live `I N`
  obligation count (`_obligation_quals()` unions `needs-gatekeeper`
  regardless of stream) -- #367 dropped the dedicated `gkq N` statusline
  badge (was `gk-req N` before the footer's labels were shortened, #223) as
  duplicate decoration of a number `I N` already includes.
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar
import watchdog as wd

IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
BUSY = ("● Validate issue\n  ⎿ running…\n"
        "✳ Baking… (2m · esc to interrupt)\n")


def seed_repo_cache(home, root, name, **extra):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    entry = {"open": 1, "name": name, "root": root, "ts": int(time.time())}
    entry.update(extra)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(entry))


class FakeTmux:
    def __init__(self, panes=None, captured=IDLE):
        self.panes = panes or []
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestGkRequestBackstop(unittest.TestCase):
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
        logs = wd.gk_request_backstop(
            now or time.time(), tmux, state, self._send, home=self.home,
            gh_fetch=lambda root: tickets, user="gatekeeper")
        return logs, tmux

    def test_nudges_idle_supervisor_pane(self):
        logs, tmux = self._go({}, [2081, 2083])
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertTrue(typed[0].startswith("gk-request backstop:"), typed[0])
        self.assertIn("#2081", typed[0])
        self.assertIn("needs-gatekeeper", typed[0])
        self.assertTrue(any("gkreq-nudge" in ln for ln in logs), logs)

    def test_busy_pane_gets_nothing(self):
        _logs, tmux = self._go({}, [7], captured=BUSY)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_no_pane_pings_discord_once(self):
        state = {}
        now = time.time()
        logs, _t = self._go(state, [7], panes=[], now=now)
        self.assertEqual(len(self.pings), 1)
        self.assertIn("needs-gatekeeper", self.pings[0][0])
        # same set within the renudge window → silent
        wd.gk_request_backstop(now + wd.GKREQ_INTERVAL + 5, FakeTmux([]),
                               state, self._send, home=self.home,
                               gh_fetch=lambda root: [7], user="gatekeeper")
        self.assertEqual(len(self.pings), 1)

    def test_no_pane_ping_carries_the_stream_qualified_project_label(self):
        # #369: mirrors the identical bounce_backstop fix -- the no-pane
        # Discord fallback routes to the project's own thread, never the
        # owner's plain pile.
        import unittest.mock as m
        state = {}
        with m.patch("getpass.getuser", return_value="david2"):
            self._go(state, [7], panes=[])
        self.assertEqual(self.pings[0][1].get("project"), "demo-david2")

    def test_stale_cache_root_never_pings(self):
        # LIVE false positive (2026-07-24): the no-pane Discord fallback fired
        # for a checkout untouched for 16 DAYS whose supervisor session lives
        # on ANOTHER box. Only a root with a FRESH cache entry (a session ran
        # here recently and is now gone) justifies the "session missing" ping.
        with TemporaryDirectory() as home2:
            root = str(Path(home2) / "devel" / "olddemo")
            Path(root).mkdir(parents=True)
            d = statusbar.cache_dir(home2)
            d.mkdir(parents=True, exist_ok=True)
            (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
                {"open": 1, "name": "olddemo", "root": root,
                 "ts": int(time.time()) - 16 * 24 * 3600}))
            wd.gk_request_backstop(
                time.time(), FakeTmux([]), {}, self._send, home=home2,
                gh_fetch=lambda r: [7], user="gatekeeper")
        self.assertFalse(self.pings,
                         "a 16-day-stale root must never Discord-ping")

    def test_reduced_stream_home_never_nudged(self):
        # the requester must not be nudged about its own request — only a
        # supervisor session works gk-requests (fresh home: no cached roots)
        with TemporaryDirectory() as home2:
            root = "/home/david/devel/odoo-erp"
            tmux = FakeTmux([("%9", root)])
            logs = wd.gk_request_backstop(
                time.time(), tmux, {}, self._send, home=home2,
                gh_fetch=lambda r: [5], user="david")
        self.assertFalse(tmux.typed(), logs)
        self.assertFalse(self.pings)

    def test_gh_error_keeps_state_and_stays_silent(self):
        _logs, tmux = self._go({}, None)
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)

    def test_empty_backlog_is_silent(self):
        _logs, tmux = self._go({}, [])
        self.assertFalse(tmux.typed())
        self.assertFalse(self.pings)


class TestGkreqRepingBackoff(unittest.TestCase):
    """#353: the fixed 6h `GKREQ_RENUDGE_SECONDS` re-ping window re-pinged
    Discord up to 4x/day forever for a persistently-unaddressed no-action
    state (simap deliberately off, 21 open needs-gatekeeper tickets on
    odoo-erp -- "toto je spam!!!"). Replaced with an EXPLICIT staged
    schedule (24h -> 3d -> 7d, holding at 7d), reset ONLY on a materially
    different observation: a changed ticket SET, or a supervisor pane
    appearing then disappearing again."""

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

    def _sweep(self, now, state, tickets, panes=None, captured=IDLE,
              persist=None):
        tmux = FakeTmux(panes if panes is not None else [], captured)
        return wd.gk_request_backstop(
            now, tmux, state, self._send, home=self.home,
            gh_fetch=lambda root: tickets, user="gatekeeper",
            persist=persist)

    def test_schedule_is_the_explicit_24h_3d_7d_staged_shape(self):
        # regression pin -- a future edit shrinking/widening the schedule
        # must fail this test loudly rather than silently drift.
        self.assertEqual(wd.GKREQ_REPING_SCHEDULE_S,
                         (24 * 3600, 3 * 24 * 3600, 7 * 24 * 3600))

    def test_same_state_stays_silent_well_past_the_old_6h_window(self):
        state = {}
        now = time.time()
        self._sweep(now, state, [7])
        self.assertEqual(len(self.pings), 1)
        # 12h later -- well past the OLD 6h fixed renudge, well under the
        # NEW 24h first stage -- must stay silent.
        self._sweep(now + 12 * 3600, state, [7])
        self.assertEqual(len(self.pings), 1,
                         "must not re-ping before the 24h first stage")

    def test_reping_fires_once_the_24h_stage_clears(self):
        state = {}
        now = time.time()
        self._sweep(now, state, [7])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 24 * 3600 + 5, state, [7])
        self.assertEqual(len(self.pings), 2)

    def test_escalates_24h_then_3d_then_holds_at_7d_cap(self):
        state = {}
        t = time.time()
        self._sweep(t, state, [7])
        self.assertEqual(len(self.pings), 1)
        t += 24 * 3600 + 5                      # stage 1 clears
        self._sweep(t, state, [7])
        self.assertEqual(len(self.pings), 2)
        self._sweep(t + 24 * 3600, state, [7])    # +1d, still inside 3d stage
        self.assertEqual(len(self.pings), 2, "must hold for the 3d stage")
        t += 3 * 24 * 3600 + 5                  # stage 2 clears
        self._sweep(t, state, [7])
        self.assertEqual(len(self.pings), 3)
        t += 7 * 24 * 3600 + 5                  # stage 3 (cap) clears
        self._sweep(t, state, [7])
        self.assertEqual(len(self.pings), 4)
        t += 7 * 24 * 3600 + 5                  # cap holds indefinitely
        self._sweep(t, state, [7])
        self.assertEqual(len(self.pings), 5)
        self._sweep(t + 3 * 24 * 3600, state, [7])   # +3d, still under 7d cap
        self.assertEqual(len(self.pings), 5,
                         "the 7d cap must never re-escalate further")

    def test_a_new_ticket_joining_the_set_resets_to_an_immediate_ping(self):
        # false-positive control: a genuinely NEW request must never be
        # swallowed by an in-progress backoff window.
        state = {}
        now = time.time()
        self._sweep(now, state, [7])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 3600, state, [7, 9])   # 1h later, well under 24h
        self.assertEqual(len(self.pings), 2,
                         "a new ticket in the set must ping immediately")

    def test_a_ticket_leaving_the_set_also_resets(self):
        state = {}
        now = time.time()
        self._sweep(now, state, [7, 9])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 3600, state, [7])      # 9 resolved -- a different set
        self.assertEqual(len(self.pings), 2)

    def test_supervisor_pane_appear_then_disappear_resets_to_fresh_ping(self):
        # #353 round 2 (MAJOR-1): the disappearance itself must be
        # CONFIRMED on a SECOND consecutive absent sweep before it resets
        # anything -- a single absent sweep is only PENDING (see the
        # dedicated single-blip test below).
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1)
        # +1h: a supervisor pane APPEARS (idle) -- well inside the 24h
        # stage, so this alone must not ping.
        self._sweep(now + 3600, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 1, "appearing alone must not ping")
        # +2h: the pane is GONE -- the FIRST absent sweep, only pending.
        self._sweep(now + 2 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1,
                         "a single absent sweep must not reset yet")
        # +2.5h: STILL gone -- the SECOND consecutive absent sweep confirms
        # the disappearance -- must ping immediately even though only 2.5h
        # elapsed since the first ping.
        self._sweep(now + 2.5 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 2,
                         "a CONFIRMED appear-then-disappear must reset "
                         "the backoff")

    def test_a_single_transient_pane_absence_does_not_reset_the_backoff(self):
        # #353 round 2, MAJOR-1 (live-reproduced regression against the
        # round-1 code): a single `list_claude_panes` read blip (#199's own
        # documented "an empty read is not a genuine negative" class) must
        # NOT be mistaken for a real disappearance and must NOT fire a
        # false "nebeží žiadna supervízorská Claude session" ping while the
        # session is genuinely still alive.
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 3600, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 1)
        # +2h: ONE transient absent sweep -- the blip.
        self._sweep(now + 2 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1, "a lone blip must not ping")
        # +2h5m: the pane is back -- the blip resolved itself before a
        # second consecutive absence could ever confirm it.
        self._sweep(now + 2 * 3600 + 300, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 1,
                         "a resolved blip must never have reset the backoff")

    def test_a_scheduled_reping_landing_on_the_first_absent_sweep_still_confirms(self):
        # round-2 review MINOR (coverage gap, code already correct): the
        # PING branch's own `seen[name]` write must persist the DEBOUNCED
        # `pane_seen`/`pane_absent_pending` values, never the raw
        # `pane_now` -- otherwise a STAGED reping that happens to land on
        # the exact same sweep as the FIRST (unconfirmed) pane absence
        # silently destroys the pending-confirmation bookkeeping, and the
        # disappearance is never confirmed on the NEXT (second consecutive)
        # absent sweep.
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 3600, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 1)
        # +25h: the 24h staged schedule is due on THIS sweep, and it is
        # ALSO the first absent sweep since the pane appeared.
        self._sweep(now + 25 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 2, "the scheduled reping fires")
        # +25.5h: the SECOND consecutive absent sweep must still confirm
        # the disappearance and reset, even though the prior sweep also
        # pinged (for an unrelated, schedule-driven reason).
        self._sweep(now + 25.5 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 3,
                         "confirmation must still fire after a scheduled "
                         "reping shared the same sweep as the first absence")

    def test_pane_target_falls_back_to_basename_with_no_cache_entry(self):
        # round-2 review MINOR (coverage gap, code already correct): a
        # brand-new root with NO tickets-status cache entry at all (never
        # seen before) must still resolve a usable pane-target name via
        # the `os.path.basename(cwd)` fallback -- the cache-name-preferred
        # path (MAJOR-3's own fix) must never make an uncached root
        # unnamed/unnudgeable.
        with TemporaryDirectory() as home2:
            root = str(Path(home2) / "devel" / "freshrepo")
            Path(root).mkdir(parents=True)
            # deliberately NO seed_repo_cache() call here.
            tmux = FakeTmux([("%1", root)], IDLE)
            logs = wd.gk_request_backstop(
                time.time(), tmux, {}, self._send, home=home2,
                dry_run=False, gh_fetch=lambda r: [7], user="newlevel")
        typed = tmux.typed()
        self.assertTrue(typed, logs)
        self.assertIn("freshrepo", typed[0])

    def test_pane_target_uses_the_cache_name_not_the_directory_basename(self):
        # #353 round 2, MAJOR-3 (TRIGGERED live: dev1's own real cache has
        # `forestshop_app` -> `forestshop-app`, `odoo-slovnormal` ->
        # `odoo-erp` -- the incident's OWN repo): a pane target used to key
        # `seen` on `os.path.basename(cwd)`, while a no-pane (cache-only)
        # sweep of the SAME root keys on the cache's own origin-derived
        # name. When those differ, the appear-then-disappear reset above
        # is structurally DEAD CODE -- the two observation types write to
        # two different `seen[]` records that never see each other's half
        # of the cycle. Re-seed this root's cache entry under a name that
        # genuinely differs from its directory basename ("demo") and
        # confirm the reset still fires through a full
        # present -> absent -> absent(confirmed) cycle.
        seed_repo_cache(self.home, self.root, "demo-origin-name")
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 3600, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 1)
        self._sweep(now + 2 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 1, "first absence only pending")
        self._sweep(now + 2.5 * 3600, state, [7], panes=[])
        self.assertEqual(len(self.pings), 2,
                         "the reset must fire even though the pane's "
                         "directory basename ('demo') differs from the "
                         "cache's own origin-derived name")

    def test_dedup_key_is_unique_per_real_ping_decision(self):
        # #353 round 2, MAJOR-2/MAJOR-A (both TRIGGERED against notify's
        # real 14-day marker TTL): the OLD dedup key embedded only the
        # ticket-set text, so notify.send's own unrelated dedup mechanism
        # silently swallowed every STAGED reping of an unchanged set (the
        # 24h/3d/7d schedule never actually reached Discord past the first
        # send) and swallowed a genuine material-change reset whenever the
        # set reverted to one seen within the last 14 days. The key must
        # now be unique per real DECISION instant, not per ticket content.
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[])
        first_key = self.pings[0][1]["dedup_key"]
        self._sweep(now + 24 * 3600 + 5, state, [7], panes=[])
        second_key = self.pings[1][1]["dedup_key"]
        self.assertNotEqual(first_key, second_key,
                            "each real ping decision must claim its own "
                            "dedup key, or notify's own 14-day marker TTL "
                            "silently swallows every staged re-ping")
        # a reverted-then-reverted-back ticket set (a MATERIAL change each
        # time) must also claim distinct keys, not collide with the very
        # first send's key.
        self._sweep(now + 25 * 3600, state, [9], panes=[])   # different set
        third_key = self.pings[2][1]["dedup_key"]
        self._sweep(now + 26 * 3600, state, [7], panes=[])   # reverted back
        fourth_key = self.pings[3][1]["dedup_key"]
        self.assertNotIn(fourth_key, (first_key, second_key, third_key))

    def test_pane_appearing_with_no_prior_disappearance_is_not_a_reset(self):
        # a pane simply BEING there from the start (never having been
        # absent) must not, by itself, count as the appear/disappear cycle.
        state = {}
        now = time.time()
        self._sweep(now, state, [7], panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 0)   # nudged the pane, no Discord ping
        self._sweep(now + 3600, state, [7],
                   panes=[("%1", self.root)], captured=IDLE)
        self.assertEqual(len(self.pings), 0,
                         "a still-present pane must stay on the schedule")

    def test_state_persists_across_a_simulated_watchdog_restart(self):
        # #353 requirement 4: a restart must not forget the backoff clock
        # and re-spam from a fresh "first sighting".
        state_path = str(Path(self.home) / "gkreq-state.json")
        state1 = wd.load_state(state_path)
        now = time.time()
        self._sweep(now, state1, [7],
                   persist=lambda: wd.save_state(state_path, state1))
        self.assertEqual(len(self.pings), 1)
        # simulate the process restarting: throw away `state1`, reload a
        # FRESH dict from disk (exactly what a killed-and-relaunched
        # systemd timer tick does).
        state2 = wd.load_state(state_path)
        self.assertTrue(state2, "the sweep must have persisted something")
        self._sweep(now + 12 * 3600, state2, [7],
                   persist=lambda: wd.save_state(state_path, state2))
        self.assertEqual(len(self.pings), 1,
                         "a restarted process must still honour the "
                         "backoff clock it already persisted")


class TestGkreqFetch(unittest.TestCase):
    def test_label_and_title_fallback_queries_union(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            out = ([{"number": 5}] if "--label" in argv
                   else [{"number": 9,
                          "title": "GATEKEEPER-ACTION: obnov docker sock"}])
            return m.Mock(returncode=0, stdout=json.dumps(out))

        with m.patch("subprocess.run", side_effect=run):
            got = wd._fetch_gkreq_tickets("/tmp/x")
        self.assertEqual(got, [5, 9])
        flat = json.dumps(calls)
        self.assertIn("needs-gatekeeper", flat)
        self.assertIn("GATEKEEPER-ACTION", flat)

    def test_tokenized_search_match_is_filtered_client_side(self):
        # LIVE false positive (2026-07-24, first minutes of the job): GitHub
        # search TOKENIZES — '"GATEKEEPER-ACTION:" in:title' matched odoo-erp
        # #1768 "P1 hardening: … gatekeeper GitHub Actions runner" (tokens
        # gatekeeper + actions) and pinged the user's Discord about a
        # non-request. Only a title carrying the LITERAL marker counts.
        def run(argv, **kw):
            out = ([] if "--label" in argv
                   else [{"number": 1768,
                          "title": "P1 hardening: dedicated non-sudo OS "
                                   "identity for the gatekeeper GitHub "
                                   "Actions runner"}])
            return m.Mock(returncode=0, stdout=json.dumps(out))

        with m.patch("subprocess.run", side_effect=run):
            self.assertEqual(wd._fetch_gkreq_tickets("/tmp/x"), [])

    def test_any_query_error_returns_none(self):
        with m.patch("subprocess.run",
                     return_value=m.Mock(returncode=1, stdout="")):
            self.assertIsNone(wd._fetch_gkreq_tickets("/tmp/x"))


class TestGkreqFetchExcludesOpsChannel(unittest.TestCase):
    """#364 (follow-up to #362): same defect class as the bounce backstop
    -- BOTH `_fetch_gkreq_tickets` queries (the `needs-gatekeeper` label
    query AND the `GATEKEEPER-ACTION:` title-fallback query) still
    hand-rolled a bare `-label:autopilot-skip` with no `ops-channel`
    exclusion. A PERMANENT ops-channel ticket that also carried
    `needs-gatekeeper` (or a `GATEKEEPER-ACTION:` title) would still
    surface here as a gk-request nudge candidate."""

    def test_both_queries_exclude_ops_channel(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="[]")

        with m.patch("subprocess.run", side_effect=run):
            wd._fetch_gkreq_tickets("/tmp/x")
        self.assertEqual(len(calls), 2, calls)
        label_call = next(c for c in calls if "--label" in c)
        title_call = next(c for c in calls if "--label" not in c)
        self.assertIn("-label:ops-channel", json.dumps(label_call),
                       "needs-gatekeeper label query missing ops-channel excl")
        self.assertIn("-label:ops-channel", json.dumps(title_call),
                       "GATEKEEPER-ACTION: title-fallback query missing "
                       "ops-channel excl")


class TestMachinePrefixes(unittest.TestCase):
    def test_gkreq_nudge_is_a_machine_prompt(self):
        self.assertTrue(any(
            p.startswith("gk-request backstop") for p in
            wd._MACHINE_PROMPT_PREFIXES))

    def test_job10_auto_enters_gkreq_nudges(self):
        # MACHINE_NUDGE_PREFIX is consumed via str.startswith → tuple form
        self.assertTrue(
            "gk-request backstop:" in wd.MACHINE_NUDGE_PREFIX
            if isinstance(wd.MACHINE_NUDGE_PREFIX, tuple)
            else wd.MACHINE_NUDGE_PREFIX.startswith("gk-request"))

    def test_run_once_wires_the_job(self):
        src = Path(wd.__file__).read_text()
        self.assertIn("gkreq_fetch", src)
        i = src.index("def run_once")
        self.assertIn("gk_request_backstop(", src[i:])


class TestStatuslineNoLongerHasADedicatedBadge(unittest.TestCase):
    """#367 (third footer simplification round): the dedicated `gkq N`
    badge (open needs-gatekeeper tickets, whole repo) was DROPPED as
    duplicate decoration -- `_obligation_quals()` (the SAME union
    `core-quals --count` computes for the full-authority box's own `I N`)
    already folds `needs-gatekeeper` into the live obligation count, so a
    separate badge repeated a number `I N` already includes."""

    def test_no_gk_req_field_is_computed_any_more(self):
        src = Path(airuleset.__file__).read_text()
        i = src.index('entry["scope"] = "core"')
        self.assertNotIn('entry["gk_req"]', src[i:i + 3000])

    def test_gkq_never_renders(self):
        with TemporaryDirectory() as home:
            root = str(Path(home) / "devel" / "demo")
            Path(root).mkdir(parents=True)
            seed_repo_cache(home, root, "demo", scope="core")
            seg = statusbar.tickets_segment(root, home=home, spawn=False)
            self.assertNotIn("gkq", seg)


class TestCmdGkRequest(unittest.TestCase):
    def _args(self, **kw):
        base = dict(repo=None, issue=None, title=None, body=None,
                    body_file=None, comment=None)
        base.update(kw)
        return m.Mock(**base)

    def test_create_with_label(self):
        # #221 fix: the label is applied via its OWN `--add-label` call
        # AFTER a bare create, never baked into the create call itself —
        # baking it in is exactly the shape GitHub silently drops the
        # label from when the actor lacks push access. This test used to
        # assert `needs-gatekeeper` was present in the CREATE call itself
        # (the pre-#221-fix, vulnerable shape); it now asserts the correct
        # split.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/31\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Obnov prístup na box", body="detail"))
        self.assertIn(rc, (0, None))
        create = calls[0]
        self.assertIn("create", create)
        self.assertNotIn("needs-gatekeeper", " ".join(create), create)
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in add_label_calls),
            add_label_calls)
        # #221 adversarial review, MINOR: a "retitle unconditionally,
        # ignore whether the label landed" mutant must NOT pass this
        # test -- the label succeeded here, so no --title edit (the
        # GATEKEEPER-ACTION degrade) should ever be attempted.
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_create_label_denied_falls_back_to_title_prefix(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "needs-gatekeeper" in " ".join(argv):
                return m.Mock(returncode=1, stdout="",
                              stderr="could not add label")
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/32\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(self._args(title="Re-dispatch CI"))
        self.assertIn(rc, (0, None))
        titles = [argv[argv.index("--title") + 1] for argv in calls
                  if "--title" in argv]
        self.assertTrue(any(t.startswith("GATEKEEPER-ACTION:")
                            for t in titles), titles)

    def test_create_label_silently_dropped_degrades_to_prefix(self):
        # #221 LIVE bug: GitHub's issue-create endpoint silently DROPS a
        # `labels` field the actor lacks push access for -- unlike the
        # dedicated add-label endpoint, it does NOT 403 the whole request
        # (documented GitHub REST behavior: "Only users with push access
        # can set labels for new issues... labels are silently dropped
        # otherwise"). A read-only-fork actor's `gh issue create --label
        # needs-gatekeeper` therefore returned rc=0 with the issue created
        # and NO label on it at all, and cmd_gk_request reported "filed"
        # as if the escalation were visible. Simulate the real split: the
        # label must be applied in its OWN edit call (not baked into
        # create), and that dedicated call correctly fails (403) for a
        # read-only actor -- prove the command then degrades to the
        # GATEKEEPER-ACTION title prefix instead of silently declaring
        # success with neither signal present.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/zbynekdrlik/"
                                     "odoo-erp/issues/2779\n",
                              stderr="")
            if "--add-label" in argv and "needs-gatekeeper" in argv:
                return m.Mock(returncode=1, stdout="",
                              stderr="HTTP 403: Resource not accessible")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="DNS chyba blokuje hand-off",
                           body="detail"))
        self.assertIn(rc, (0, None))
        # the initial create must NEVER bake the label into the same call
        # -- that is precisely the field GitHub silently drops
        create_call = [c for c in calls
                       if "create" in c and "issue" in c][0]
        self.assertNotIn("needs-gatekeeper", create_call, create_call)
        # a real, separate add-label attempt must have been made and
        # denied (the dedicated label endpoint correctly 403s)
        self.assertTrue(any("--add-label" in c and "needs-gatekeeper" in c
                            for c in calls), calls)
        # denial must degrade to the GATEKEEPER-ACTION title prefix so the
        # escalation stays discoverable by job 11's `in:title` query
        edits = [c for c in calls if "edit" in c and "--title" in c]
        self.assertTrue(edits, calls)
        self.assertIn("GATEKEEPER-ACTION:", json.dumps(edits))

    def test_create_neither_label_nor_prefix_fails_loudly(self):
        # both the label add AND the retitle are denied -- must NEVER
        # report success while the escalation is invisible to the
        # supervisor (script-failure-policy: fail loudly, never guess).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/o/r/issues/2780\n",
                              stderr="")
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "edit" in argv and "--title" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Nejaky problem", body="detail"))
        self.assertEqual(rc, 1)

    def test_create_unparseable_issue_number_fails_loudly(self):
        # #221 adversarial review, MAJOR: a `gh issue create` success whose
        # stdout does NOT end in a parseable issue number must not be
        # allowed to short-circuit past both the label-add AND the
        # retitle attempt straight to a false "gk-request filed" — that
        # silently reproduces the exact invisible-escalation class this
        # ticket exists to kill, just triggered by anomalous `gh` stdout
        # instead of a denied label.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "create" in argv and "issue" in argv:
                return m.Mock(returncode=0, stdout="done\n", stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(title="Neparsovatelne cislo", body="detail"))
        self.assertEqual(rc, 1)
        # never attempted a label/retitle against a garbage "issue number"
        self.assertFalse(any("--add-label" in c for c in calls), calls)
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_issue_mode_labels_and_comments(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            airuleset.cmd_gk_request(
                self._args(issue=2081, comment="obnov docker sock prístup"))
        flat = json.dumps(calls)
        self.assertIn("--add-label", flat)
        self.assertIn("needs-gatekeeper", flat)
        self.assertIn("comment", flat)

    def test_issue_mode_label_denied_comment_carries_marker(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0, stdout="Stary titulok\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=7, comment="akcia"))
        # #283 adversarial review, MINOR: label denied but the retitle
        # fallback SUCCEEDED is a genuinely visible, successful escalation
        # -- must return 0, never the loud-failure rc=1. A mutant that
        # drops the `retitled` clause from the failure guard (leaving only
        # `not labeled and not already_prefixed`) survived every
        # pre-existing test in this file because none of them asserted
        # the return code for this exact combination.
        self.assertEqual(rc, 0)
        comments = [argv for argv in calls if "comment" in argv]
        self.assertTrue(comments)
        self.assertIn("GATEKEEPER-ACTION:", json.dumps(comments))
        # a comment-only marker is INVISIBLE to job 11's queries (label +
        # in:title only) — the fallback must ALSO best-effort retitle the
        # issue so the request stays machine-discoverable
        edits = [argv for argv in calls
                 if "edit" in argv and "--title" in argv]
        self.assertTrue(edits, calls)
        self.assertIn("GATEKEEPER-ACTION: Stary titulok", json.dumps(edits))

    def test_issue_mode_already_prefixed_title_not_retitled(self):
        # boundary (review 2026-07-24): a title already carrying the marker
        # must not be double-prefixed
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0,
                              stdout="GATEKEEPER-ACTION: uz oznacene\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            airuleset.cmd_gk_request(self._args(issue=8, comment="akcia"))
        edits = [argv for argv in calls
                 if "edit" in argv and "--title" in argv]
        self.assertFalse(edits, "already-prefixed title must not be retitled")

    def test_issue_mode_neither_label_nor_retitle_succeeds_fails_loudly(self):
        # #283: mirrors #221's create-mode hardening for the --issue
        # (mark-existing-ticket) branch — label denied AND the retitle
        # edit also denied must NEVER report success while the escalation
        # is invisible to job 11's needs-gatekeeper/in:title queries
        # (script-failure-policy: fail loudly, never guess).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0, stdout="Stary titulok\n",
                              stderr="")
            if "edit" in argv and "--title" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=9, comment="akcia"))
        self.assertEqual(rc, 1)
        # the comment itself must still have been posted (it's the retitle
        # that failed, not the comment) -- the failure is about visibility,
        # not about the comment call
        self.assertTrue(any("comment" in c for c in calls), calls)

    def test_issue_mode_view_fallback_failure_fails_loudly(self):
        # #283: the retitle fallback's OWN `gh issue view` read can fail
        # (network hiccup, permissions) -- when it does, we cannot tell
        # whether the title already carries the GATEKEEPER-ACTION marker,
        # so a denied label plus an unreadable title must ALSO fail loudly
        # rather than silently assuming the escalation is fine.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=1, stdout="",
                              stderr="could not view issue")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=10, comment="akcia"))
        self.assertEqual(rc, 1)
        # never attempted a retitle against an unread title
        self.assertFalse(
            any("edit" in c and "--title" in c for c in calls), calls)

    def test_issue_mode_already_prefixed_title_still_returns_success(self):
        # #283 regression guard: the existing already-prefixed boundary
        # case (test_issue_mode_already_prefixed_title_not_retitled) must
        # keep returning 0 -- the escalation IS already visible via the
        # title, even though no retitle *call* was made, so the new
        # loud-failure gate must not treat "retitled == False" alone as
        # a failure signal.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "--add-label" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "view" in argv:
                return m.Mock(returncode=0,
                              stdout="GATEKEEPER-ACTION: uz oznacene\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run):
            rc = airuleset.cmd_gk_request(
                self._args(issue=11, comment="akcia"))
        self.assertEqual(rc, 0)

    def test_registered_in_cli(self):
        src = Path(airuleset.__file__).read_text()
        self.assertIn('"gk-request"', src)
        self.assertIn("cmd_gk_request", src)

    def test_handoff_label_never_enters_the_goal_stop_proof_slice(self):
        # #191 adversarial review, CRITICAL C1: a needs-gatekeeper ticket
        # tagged with Part C's origin marker must NEVER appear in
        # `slice-quals --count` (the `/goal` stop-proof's own termination
        # check) -- that is exactly what reusing `stream:<user>` as the
        # marker would have broken (the loop could never reach a real 0
        # while any such ticket stayed open). `_slice_quals()` for a
        # shared-account stream is `label:stream:<user>` ALONE and is
        # completely unmodified by this fix; this proves `handed-by:<user>`
        # structurally cannot match it.
        import contextlib
        import io

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a[:2] == ("label", "list"):
                # _label_exists_on_repo's own probe: stream:simap genuinely
                # IS a defined repo label (the pre-existing ownership
                # convention) -- it is simply never APPLIED to this ticket.
                return '[{"name": "stream:simap"}]'
            if "label:stream:simap" in j:
                return "[]"    # the ticket carries handed-by:, not stream:
            if "sort:created-desc" in j:
                # proves the search index genuinely works (#181's own C2
                # health guard) so the 0 above is trusted, not refused
                return '[{"number": 999}]'
            return "[]"

        buf = io.StringIO()
        with m.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"), \
                m.patch.object(airuleset, "_current_user", return_value="simap"), \
                m.patch.object(airuleset, "_gh_out", side_effect=gh):
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_slice_quals(
                    m.Mock(count=True, list=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_issue_mode_also_applies_origin_handoff_label(self):
        # #191 Part C: a registered sub-dev stream's gk-request ALSO applies
        # its own handed-by:<user> label at hand-off time -- the origin
        # marker cmd_tickets_status's re-attribution step
        # (_last_origin_owner) reads later, even after a subsequent relabel
        # removes it.
        #
        # #191 adversarial review, CRITICAL C1: deliberately handed-by:,
        # NEVER stream: -- reusing the ownership label would have made a
        # needs-gatekeeper ticket permanently part of the stream's own
        # /goal stop-proof slice (slice-quals --count could never reach 0).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        # Patch `_current_user` (exists both pre- and post-fix) rather than
        # the not-yet-existing `_own_handoff_label` -- this drives the SAME
        # real entry point pre-fix, so a red run fails on genuine missing
        # behaviour (no origin-label calls) rather than on an AttributeError
        # from mocking an attribute the pre-fix module never had.
        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            airuleset.cmd_gk_request(
                self._args(issue=2081, comment="obnov docker sock prístup"))
        flat = json.dumps(calls)
        self.assertIn("label", flat)
        self.assertIn("create", flat)           # gh label create (checked-first)
        self.assertIn("handed-by:simap", flat)
        self.assertNotIn("stream:simap", flat)  # never the ownership label
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)

    def test_ensure_origin_label_never_overwrites_an_existing_label(self):
        # #191 adversarial review, MAJOR M1 (live-verified against real
        # odoo-erp stream:* labels, each hand-curated with its own colour +
        # Slovak description): the original --force version overwrote an
        # EXISTING label's colour/description on EVERY call. A label
        # `gh label list --search` reports as already present must never be
        # passed to `gh label create` at all.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "list" in argv:
                return m.Mock(returncode=0, stdout="handed-by:simap\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            airuleset.cmd_gk_request(
                self._args(issue=3, comment="akcia"))
        create_calls = [c for c in calls
                        if "label" in c and "create" in c]
        self.assertEqual(create_calls, [], create_calls)
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertTrue(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)

    def test_create_mode_also_applies_origin_handoff_label(self):
        # #191 adversarial review, MAJOR M4: the origin label must be
        # applied AFTER the primary `gh issue create` succeeds, in a
        # SEPARATE call -- baking it into the create call meant a rejected
        # origin label failed the WHOLE create (dropping needs-gatekeeper
        # too) and silently fell through to the title-prefix fallback.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0,
                          stdout="https://github.com/o/r/issues/40\n",
                          stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(title="Adopt the pipeline", body="detail"))
        self.assertIn(rc, (0, None))
        create = [c for c in calls if "issue" in c and "create" in c][0]
        # #221 fix: needs-gatekeeper is ALSO applied via its own separate
        # `--add-label` call now, never baked into create -- same reason
        # as the origin label this test was originally about.
        self.assertNotIn("needs-gatekeeper", create, create)
        self.assertNotIn("handed-by:simap", create)   # NOT baked into create
        edit_calls = [c for c in calls
                     if "issue" in c and "edit" in c and "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in edit_calls), edit_calls)
        self.assertTrue(
            any("40" in c and "handed-by:simap" in c for c in edit_calls),
            edit_calls)

    def test_create_mode_origin_label_failure_never_drops_needs_gatekeeper(self):
        # A rejected origin --add-label (after a successful create) must
        # never retroactively undo the create or its needs-gatekeeper label
        # -- the origin label's own denial is deliberately independent of
        # the primary needs-gatekeeper --add-label call (#221: also its own
        # separate call now, never baked into create), so only the ORIGIN
        # label is denied here to prove the two are not coupled.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "edit" in argv and "--add-label" in argv \
                    and "handed-by:simap" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            if "issue" in argv and "create" in argv:
                return m.Mock(returncode=0,
                              stdout="https://github.com/o/r/issues/41\n",
                              stderr="")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(title="Adopt the pipeline", body="detail"))
        self.assertIn(rc, (0, None))
        creates = [c for c in calls if "issue" in c and "create" in c]
        self.assertEqual(len(creates), 1, creates)   # never a fallback retry
        self.assertNotIn("needs-gatekeeper", creates[0], creates[0])
        add_label_calls = [c for c in calls
                           if "issue" in c and "edit" in c
                           and "--add-label" in c]
        self.assertTrue(
            any("needs-gatekeeper" in c for c in add_label_calls),
            add_label_calls)

    def test_origin_label_skipped_when_not_a_registered_stream(self):
        # A full-authority box (dev1/gatekeeper) must never stamp a
        # meaningless origin label -- `_current_user` here resolves to
        # whatever box actually runs this test, never a registered stream,
        # so no `gh label` call should be attempted at all.
        #
        # #191 adversarial review, MINOR m5: this must patch `_current_user`
        # to a KNOWN non-registered name -- unpatched, the assertion would
        # fail outright if the suite is ever run AS one of the streams this
        # feature targets (marek/montalu/david/simap).
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="newlevel"):
            airuleset.cmd_gk_request(
                self._args(issue=99, comment="akcia"))
        flat = json.dumps(calls)
        self.assertNotIn("handed-by:", flat)
        label_subcmds = [c for c in calls
                        if len(c) > 1 and c[0] == "gh" and c[1] == "label"]
        self.assertEqual(label_subcmds, [], label_subcmds)

    def test_origin_label_create_failure_is_logged_not_fatal(self):
        # The needs-gatekeeper hand-off (gk-request's PRIMARY job) must
        # never be blocked by the best-effort origin-label enrichment
        # failing -- and the --add-label attempt must never even fire once
        # the ensure-step reports the label unusable.
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "label" in argv and "create" in argv:
                return m.Mock(returncode=1, stdout="", stderr="403")
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=run), \
                m.patch.object(airuleset, "_current_user",
                               return_value="simap"):
            rc = airuleset.cmd_gk_request(
                self._args(issue=5, comment="akcia"))
        self.assertIn(rc, (0, None))
        add_label_calls = [c for c in calls if "--add-label" in c]
        self.assertFalse(
            any("handed-by:simap" in c for c in add_label_calls),
            add_label_calls)
        # needs-gatekeeper itself still went through
        self.assertTrue(any("needs-gatekeeper" in c for c in add_label_calls))


class TestProtocolDocs(unittest.TestCase):
    def test_autopilot_skill_documents_the_channel(self):
        txt = (Path(airuleset.__file__).parent / "skills" / "autopilot"
               / "SKILL.md").read_text()
        self.assertIn("needs-gatekeeper", txt)
        # #426: cross-stream rule 7 (the actual gk-request / GATEKEEPER-ACTION
        # channel definition) moved VERBATIM to this reference file (SKILL.md
        # exceeded its own #414 line budget) — SKILL.md itself keeps only a
        # short pointer paragraph, which does not name the channel's own
        # exact commands.
        cross_stream = (Path(airuleset.__file__).parent / "skills" /
                        "autopilot" / "references" /
                        "cross-stream-protocol.md").read_text()
        self.assertIn("gk-request", cross_stream)
        self.assertIn("GATEKEEPER-ACTION", cross_stream)

    def test_statusline_vocabulary_no_longer_documents_the_gkq_badge(self):
        # #367 (third footer simplification round): the dedicated `· gkq N`
        # badge (spoken "gk-req") was dropped as duplicate decoration -- see
        # TestStatuslineNoLongerHasADedicatedBadge above for why. The doc
        # must not claim it still exists.
        txt = (Path(airuleset.__file__).parent / "modules" / "core"
               / "statusline-vocabulary.md").read_text()
        self.assertNotIn("gk-req", txt)
        self.assertNotIn("gkq", txt)


class TestAutopilotSkipExclConstantsStayInSync(unittest.TestCase):
    """#364 review finding: `watchdog.AUTOPILOT_SKIP_EXCL` is a deliberately
    INDEPENDENT literal (never `from airuleset import ...`, per its own
    docstring -- import-cost + layering-fragility reasons, not a circular
    import). #364 exists precisely because a second hand-rolled copy of
    this exclusion drifted from #362's original one; a THIRD copy with
    nothing pinning it equal to the first would be the same bet again. If
    either the label or the exclusion shape ever changes in one module
    without the other, this fails loudly instead of relying on a human
    `grep -rn ops-channel`."""

    def test_watchdog_and_airuleset_constants_are_identical(self):
        self.assertEqual(wd.AUTOPILOT_SKIP_EXCL, airuleset.AUTOPILOT_SKIP_EXCL)


if __name__ == "__main__":
    unittest.main()
