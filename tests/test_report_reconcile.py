"""#525 — job-25 REPORT reconciliation (supervisor per-ticket report owed).

A saturated `/process-subdev` (or `/autopilot`) supervisor consumes a worker
return and continues `⏳ WORKING` WITHOUT writing the per-ticket
`## ✅ Work Complete` report, so the ticket's compact (#411 `--self` path) also
never fires. Job 25 already computes "closed/merged tickets of this box" for
missing CARDS; `report_reconcile` adds a SECOND check over the SAME closed set
— a closed ticket whose closing supervisor session has NO subsequent report
boundary (`## ✅ Work Complete` in the transcript OR a `origin=self-callback`
line in `compact-sync.log`) since the close → after grace → ONE deduplicated
in-band nudge into the supervisor pane (transcript-verified `send_verified`),
with a consecutive-swallow give-up that escalates to the owner (#442-F2 /
#502 / #509 / #511 anti-silence class).

These tests drive the PURE decision function `report_boundary_after`
(facts-in / verdict-out, #504) and the `report_reconcile` orchestration
directly with injected seams — no tmux, no network, no real transcript I/O.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402,F401
import watchdog.cards as cards  # noqa: E402


HEADING = "## ✅ Work Complete\n\n**Audits & deploy:**\n✅ CI: green"


def arow(ts_iso, text, api_err=False, typ="assistant"):
    """A CC transcript jsonl row (dict)."""
    d = {"type": typ, "timestamp": ts_iso,
         "message": {"content": [{"type": "text", "text": text}]}}
    if api_err:
        d["isApiErrorMessage"] = True
    return d


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


# --------------------------------------------------------------------------- #
# 1. The pure decision function.
# --------------------------------------------------------------------------- #

class ReportBoundaryAfter(unittest.TestCase):
    def test_heading_after_since_is_a_boundary(self):
        rows = [arow(iso(1000), "working on it"),
                arow(iso(2000), HEADING)]
        self.assertTrue(cards.report_boundary_after(rows, 1500))

    def test_heading_before_since_is_not_a_boundary(self):
        rows = [arow(iso(1000), HEADING),
                arow(iso(1200), "⏳ WORKING: next ticket")]
        self.assertFalse(cards.report_boundary_after(rows, 1500))

    def test_no_heading_after_since_is_not_a_boundary(self):
        rows = [arow(iso(2000), "⏳ WORKING: merged, on to the next")]
        self.assertFalse(cards.report_boundary_after(rows, 1500))

    def test_api_error_turn_with_heading_is_skipped(self):
        rows = [arow(iso(2000), HEADING, api_err=True)]
        self.assertFalse(cards.report_boundary_after(rows, 1500))

    def test_mid_line_heading_mention_is_not_a_boundary(self):
        rows = [arow(iso(2000), "I will write the ## ✅ Work Complete report soon")]
        self.assertFalse(cards.report_boundary_after(rows, 1500))

    def test_empty_rows(self):
        self.assertFalse(cards.report_boundary_after([], 1500))


# --------------------------------------------------------------------------- #
# 2. compact-sync.log self-callback parsing.
# --------------------------------------------------------------------------- #

class SelfCallbackRecords(unittest.TestCase):
    def test_send_self_callback_is_parsed(self):
        lines = ["%s SEND sid=supX cwd=/r origin=self-callback" % iso(3000)]
        recs = cards._self_callback_records(lines)
        self.assertIn("supX", recs)
        self.assertEqual([3000.0], [round(t) for t in recs["supX"]])

    def test_skip_expired_self_callback_is_parsed(self):
        lines = ["%s SKIP expired sid=supX cwd=/r origin=self-callback" % iso(3000)]
        self.assertIn("supX", cards._self_callback_records(lines))

    def test_non_origin_skip_is_ignored(self):
        lines = ["%s SKIP recent-human sid=supX cwd=/r" % iso(3000),
                 "%s SEND sid=supY cwd=/r origin=subagent-stop" % iso(3100)]
        recs = cards._self_callback_records(lines)
        self.assertEqual({}, recs)


# --------------------------------------------------------------------------- #
# 3. The orchestration.
# --------------------------------------------------------------------------- #

SUP_ROOT = "/home/dev/proj"
SUP_CWD = "/home/dev/proj"
WORKTREE_CWD = "/home/dev/proj/.claude/worktrees/agent-abc"
SID = "sup-sid-1"


def make_git(root, closes):
    """Fake git_run. `closes` = {issue_num: commit_epoch_ts}."""
    def g(argv, timeout=10):
        if "rev-parse" in argv and "--show-toplevel" in argv:
            # the -C arg says which cwd; return that cwd's toplevel
            i = argv.index("-C")
            cwd = argv[i + 1]
            if ".claude/worktrees/" in cwd:
                return cwd + "\n"
            return root + "\n"
        if "symbolic-ref" in argv:
            return "refs/remotes/origin/main\n"
        if "log" in argv:
            recs = ""
            for n, ts in closes.items():
                recs += "\x1e%d\x1f Closes #%d fix thing\n\n" % (int(ts), n)
            return recs
        return None
    return g


class _Recorder:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, pane_id, text, tpath):
        self.calls.append((pane_id, text, tpath))
        return self.result


def _tmp_log(lines):
    p = Path(tempfile.mkdtemp()) / "compact-sync.log"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def run_report(now, state, rows, closes, *, dry_run=False,
               vs_result=True, mutex=False, recent=False,
               send_fn=None, grace=None, max_swallows=None, reprobe=0,
               compact_lines=None, cwd=SUP_CWD, sid=SID, owned_closed=None):
    vs = _Recorder(vs_result)
    tmp = _tmp_log(compact_lines) if compact_lines is not None else None
    extra = {} if owned_closed is None else {"owned_closed": owned_closed}
    logs = cards.report_reconcile(
        now, None, state, {sid: cwd}, {sid: ("%42", "captured")},
        send_fn=send_fn, dry_run=dry_run,
        git_run=make_git(SUP_ROOT, closes),
        owner_by_sid={sid: "zbynek"}, projects_dir="/p",
        grace=grace, max_swallows=max_swallows, reprobe=reprobe,
        mutex_held=lambda root: mutex,
        recent_human=lambda c, s: recent,
        transcript_fn=lambda pd, s, c: "/p/%s.jsonl" % s,
        rows_fn=lambda tp: rows,
        verified_send=vs,
        compact_log_path=tmp,
        **extra)
    return logs, vs


def reduced_filter(me="montalu", owners=None, authority="branch-merge",
                   raise_owner=False):
    """A `make_owned_closed_filter` wired for a REDUCED-authority box, with the
    gh ownership lookup faked (`owners` = {issue_num: owning_user}). `owners=None`
    or `raise_owner=True` models an undeterminable lookup (fail-safe: own none)."""
    def owner_fn(root, nums):
        if raise_owner:
            raise RuntimeError("gh down")
        return dict(owners or {})
    return cards.make_owned_closed_filter(
        current_user_fn=lambda: me,
        authority_fn=lambda root: authority,
        owner_fn=owner_fn)


class ReportReconcileNudge(unittest.TestCase):
    def test_owed_ticket_nudges_the_supervisor_pane(self):
        now = 100000
        closes = {41: now - 3600}          # closed 1h ago, well past grace
        rows = [arow(iso(now - 100), "⏳ WORKING: on to #42")]  # no heading
        state = {}
        logs, vs = run_report(now, state, rows, closes)
        self.assertEqual(1, len(vs.calls), "one in-band nudge expected")
        pane_id, text, tpath = vs.calls[0]
        self.assertEqual("%42", pane_id)
        self.assertIn("#41", text)
        self.assertIn("compact-request --self", text)
        # marked nudged so it never fires again
        self.assertIn("41", state["report_owed"][SUP_ROOT]["nudged"])

    def test_second_sweep_after_delivery_does_not_renudge(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        run_report(now, state, rows, closes)               # sweep 1: nudged
        _logs, vs2 = run_report(now + 60, state, rows, closes)  # sweep 2
        self.assertEqual(0, len(vs2.calls), "dedup: no second nudge")

    def test_transcript_heading_boundary_suppresses_nudge(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 1800), HEADING)]   # report written after close
        logs, vs = run_report(now, {}, rows, closes)
        self.assertEqual(0, len(vs.calls))

    def test_self_callback_record_suppresses_nudge(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]   # heading gone (compacted)
        lines = ["%s SEND sid=%s cwd=%s origin=self-callback"
                 % (iso(now - 1800), SID, SUP_CWD)]
        logs, vs = run_report(now, {}, rows, closes, compact_lines=lines)
        self.assertEqual(0, len(vs.calls))

    def test_within_grace_does_not_nudge(self):
        now = 100000
        closes = {41: now - 60}            # closed 60s ago, inside grace
        rows = [arow(iso(now - 30), "⏳ next")]
        logs, vs = run_report(now, {}, rows, closes)
        self.assertEqual(0, len(vs.calls))

    def test_mutex_held_does_not_nudge(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        logs, vs = run_report(now, {}, rows, closes, mutex=True)
        self.assertEqual(0, len(vs.calls))

    def test_recent_human_does_not_nudge(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        logs, vs = run_report(now, {}, rows, closes, recent=True)
        self.assertEqual(0, len(vs.calls))

    def test_worktree_session_is_not_treated_as_supervisor(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        logs, vs = run_report(now, {}, rows, closes, cwd=WORKTREE_CWD)
        self.assertEqual(0, len(vs.calls), "worker worktree owes no report")


class ReportReconcileAntiSilence(unittest.TestCase):
    def test_persistent_swallow_escalates_to_owner_once(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        pings = []

        def send_fn(text, owner=None, dedup_key=None, dry_run=False, project=None):
            pings.append((text, owner, dedup_key))
            return "sent"

        state = {}
        # swallow the in-band nudge every sweep (verified_send -> False)
        for i in range(5):
            run_report(now + i * 60, state, rows, closes,
                       vs_result=False, send_fn=send_fn, max_swallows=3)
        self.assertEqual(1, len(pings), "exactly one escalation ping")
        self.assertIn("#41", pings[0][0])
        self.assertEqual("zbynek", pings[0][1])
        self.assertIn("41", state["report_owed"][SUP_ROOT]["pinged"])

    def test_verified_delivery_clears_swallow_streak(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        run_report(now, state, rows, closes, vs_result=False, max_swallows=3)
        self.assertEqual(1, state["report_owed"][SUP_ROOT]["swallows"])
        # a later verified send resets the streak and marks nudged
        run_report(now + 60, state, rows, closes, vs_result=True, max_swallows=3)
        self.assertEqual(0, state["report_owed"][SUP_ROOT]["swallows"])
        self.assertIn("41", state["report_owed"][SUP_ROOT]["nudged"])

    def test_escalation_does_not_permanently_stop_the_reprobe(self):
        # #511: a swallow give-up escalates ONCE (owner pinged) but must NOT
        # permanently stop nudging — the moment the pane un-wedges the nudge
        # must still land. Marking the ticket handled-forever on escalation is
        # the stranded-re-probe anti-silence bug this locks against.
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        pings = []

        def send_fn(text, owner=None, dedup_key=None, dry_run=False, project=None):
            pings.append(text)
            return "sent"

        state = {}
        for i in range(4):     # swallow to escalation (pinged at sweep 3)
            run_report(now + i * 60, state, rows, closes,
                       vs_result=False, send_fn=send_fn, max_swallows=3)
        self.assertEqual(1, len(pings))
        self.assertIn("41", state["report_owed"][SUP_ROOT]["pinged"])
        self.assertNotIn("41", state["report_owed"][SUP_ROOT]["nudged"])
        # pane un-wedges: the re-probe still fires and NOW lands the nudge
        _logs, vs = run_report(now + 400, state, rows, closes, vs_result=True,
                               send_fn=send_fn, max_swallows=3)
        self.assertEqual(1, len(vs.calls), "re-probe must still nudge post-escalation")
        self.assertIn("41", state["report_owed"][SUP_ROOT]["nudged"])

    def test_reprobe_backoff_throttles_retries_within_the_window(self):
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        # sweep 1 swallows (streak 1, last_try set); sweep 2 is 60s later,
        # well inside a 600s reprobe -> backed off, no second send attempt.
        run_report(now, state, rows, closes, vs_result=False, reprobe=600)
        _logs, vs2 = run_report(now + 60, state, rows, closes,
                                vs_result=False, reprobe=600)
        self.assertEqual(0, len(vs2.calls), "within reprobe window -> no retry")
        # past the window it retries again
        _logs, vs3 = run_report(now + 700, state, rows, closes,
                                vs_result=False, reprobe=600)
        self.assertEqual(1, len(vs3.calls), "past reprobe window -> retry")


class ReportReconcileDryRun(unittest.TestCase):
    def test_dry_run_neither_sends_nor_persists_the_latch(self):
        # #516 trap: a one-shot latch persisted during a --dry-run sweep would
        # suppress the genuine nudge on every later REAL timer sweep, forever.
        now = 100000
        closes = {41: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        logs, vs = run_report(now, state, rows, closes, dry_run=True)
        self.assertEqual(0, len(vs.calls), "dry-run sends nothing")
        self.assertNotIn("report_owed", state,
                         "dry-run must not persist the one-shot latch (#516)")


# --------------------------------------------------------------------------- #
# 4. #534 — owner scoping of the owed set.
#
# `merged_closes` reads the SHARED base branch (origin/HEAD). On a multi-stream
# repo (odoo-erp) it carries every stream's + the gatekeeper's merges, so an
# unscoped owed set nudges a REDUCED-authority stream box for FOREIGN tickets
# (montalu3 got report-owed nudges for stream:core / stream:miva1). The fix
# scopes the owed set to the box's OWN slice via `_last_origin_owner`; a
# full-authority box is unchanged (owns the whole set).
# --------------------------------------------------------------------------- #

class OwnedClosedFilter(unittest.TestCase):
    def test_full_authority_owns_everything_unchanged(self):
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "gatekeeper",
            authority_fn=lambda root: "full",
            owner_fn=lambda root, nums: {})  # never even consulted
        closed = {41: 1000.0, 42: 2000.0}
        self.assertEqual(closed, owned("/r", closed))

    def test_reduced_authority_keeps_only_own_slice(self):
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu",
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: {4373: "miva1", 4379: "montalu"})
        closed = {4373: 1000.0, 4379: 2000.0}
        self.assertEqual({4379: 2000.0}, owned("/r", closed),
                         "only the OWN (stream:montalu) ticket survives")

    def test_reduced_authority_matches_own_legacy_stream_label(self):
        # #564: a RENAMED box (montalu1) must still recognize a merged ticket
        # whose temporally-last origin label is the LEGACY stream:montalu — its
        # OWN card/report reconciliation, not a foreign ticket. Routed through
        # the SAME _stream_rename_equivalents primitive as item 2. RED before the
        # fix (owners.get(n)="montalu" != me="montalu1" -> dropped -> None), so a
        # montalu1 box silently skipped its own legacy-labeled merged tickets.
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu1",
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: {4373: "miva1", 4379: "montalu"})
        closed = {4373: 1000.0, 4379: 2000.0}
        self.assertEqual({4379: 2000.0}, owned("/r", closed),
                         "the box's own legacy stream:montalu ticket survives")

    def test_reduced_authority_undeterminable_owner_returns_none(self):
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu",
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: (_ for _ in ()).throw(RuntimeError()))
        # gh lookup raised -> None = skip this sweep WITHOUT popping the dedup
        # (#534 review MINOR-2), never {} (which would pop it and re-nudge).
        self.assertIsNone(owned("/r", {4373: 1000.0}))

    def test_reduced_authority_unresolved_identity_returns_none(self):
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: None,   # can't identify this box
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: {4379: "montalu"})
        self.assertIsNone(owned("/r", {4379: 1000.0}))

    def test_reduced_authority_owns_none_returns_none_not_empty(self):
        # own-nothing is indistinguishable from a gh-{} flake, so it also skips
        # (None) rather than popping the dedup with a bare {} (#534 MINOR-2).
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu",
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: {4373: "miva1"})  # all foreign
        self.assertIsNone(owned("/r", {4373: 1000.0}))

    def test_empty_closed_returns_empty_without_a_lookup(self):
        calls = []
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu",
            authority_fn=lambda root: "branch-merge",
            owner_fn=lambda root, nums: calls.append(nums) or {})
        self.assertEqual({}, owned("/r", {}))
        self.assertEqual([], calls, "no gh lookup for an empty closed set")

    def test_owner_lookup_is_memoized_per_root_within_a_sweep(self):
        calls = []

        def owner_fn(root, nums):
            calls.append(nums)
            return {4379: "montalu"}
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "montalu",
            authority_fn=lambda root: "branch-merge",
            owner_fn=owner_fn)
        closed = {4379: 1000.0}
        owned("/r", closed)   # job 24
        owned("/r", closed)   # job 25 — same root, must reuse the memo
        self.assertEqual(1, len(calls),
                         "one shared GraphQL lookup per repo per sweep")


class ReportReconcileOwnerScoping(unittest.TestCase):
    def test_reduced_box_does_not_nudge_a_foreign_ticket(self):
        now = 100000
        closes = {4373: now - 3600,    # stream:miva1 — FOREIGN
                  4379: now - 3600}    # stream:montalu — OWN
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        owned = reduced_filter(me="montalu",
                               owners={4373: "miva1", 4379: "montalu"})
        logs, vs = run_report(now, state, rows, closes, owned_closed=owned)
        self.assertEqual(1, len(vs.calls), "only the OWN ticket nudges")
        text = vs.calls[0][1]
        self.assertIn("#4379", text)
        self.assertNotIn("#4373", text)   # foreign must NEVER be nudged
        self.assertIn("4379", state["report_owed"][SUP_ROOT]["nudged"])
        self.assertNotIn("4373", state["report_owed"][SUP_ROOT]["nudged"])

    def test_reduced_box_owns_the_ticket_still_nudges(self):
        now = 100000
        closes = {4379: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        owned = reduced_filter(me="montalu", owners={4379: "montalu"})
        logs, vs = run_report(now, {}, rows, closes, owned_closed=owned)
        self.assertEqual(1, len(vs.calls))
        self.assertIn("#4379", vs.calls[0][1])

    def test_reduced_box_all_foreign_nudges_nothing(self):
        now = 100000
        closes = {4373: now - 3600, 4413: now - 3600}   # both foreign/core
        rows = [arow(iso(now - 100), "⏳ next")]
        owned = reduced_filter(me="montalu",
                               owners={4373: "miva1"})   # 4413 has no owner
        logs, vs = run_report(now, {}, rows, closes, owned_closed=owned)
        self.assertEqual(0, len(vs.calls), "no own ticket -> no nudge")

    def test_reduced_box_undeterminable_ownership_nudges_nothing(self):
        now = 100000
        closes = {4379: now - 3600}      # genuinely own, but gh is down
        rows = [arow(iso(now - 100), "⏳ next")]
        owned = reduced_filter(me="montalu", raise_owner=True)
        logs, vs = run_report(now, {}, rows, closes, owned_closed=owned)
        self.assertEqual(0, len(vs.calls),
                         "undeterminable owner -> own nothing this sweep")

    def test_full_authority_box_nudges_the_whole_set(self):
        now = 100000
        closes = {41: now - 3600, 42: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        owned = cards.make_owned_closed_filter(
            current_user_fn=lambda: "gatekeeper",
            authority_fn=lambda root: "full",
            owner_fn=lambda root, nums: {})
        logs, vs = run_report(now, {}, rows, closes, owned_closed=owned)
        self.assertEqual(1, len(vs.calls))
        text = vs.calls[0][1]
        self.assertIn("#41", text)
        self.assertIn("#42", text)

    def test_gh_flake_preserves_dedup_and_does_not_renudge(self):
        # #534 review MINOR-2: an UNDETERMINABLE-ownership sweep (gh flake) must
        # NOT pop the permanent nudged dedup — gh recovering next sweep would
        # otherwise re-nudge an already-delivered ticket. (Against the pre-fix
        # {}-returning filter, sweep 2's pop makes sweep 3 re-nudge.)
        now = 100000
        closes = {4379: now - 3600}
        rows = [arow(iso(now - 100), "⏳ next")]
        state = {}
        ok = reduced_filter(me="montalu", owners={4379: "montalu"})
        _l, vs1 = run_report(now, state, rows, closes, owned_closed=ok)
        self.assertEqual(1, len(vs1.calls), "sweep 1: own ticket nudged")
        self.assertIn("4379", state["report_owed"][SUP_ROOT]["nudged"])
        # sweep 2: gh flakes -> undeterminable -> skip, dedup MUST survive
        flake = reduced_filter(me="montalu", raise_owner=True)
        _l, vs2 = run_report(now + 60, state, rows, closes, owned_closed=flake)
        self.assertEqual(0, len(vs2.calls))
        self.assertIn("4379", state["report_owed"][SUP_ROOT]["nudged"],
                      "dedup must survive an undeterminable sweep")
        # sweep 3: gh recovers -> dedup preserved -> NO re-nudge
        _l, vs3 = run_report(now + 120, state, rows, closes, owned_closed=ok)
        self.assertEqual(0, len(vs3.calls), "must not re-nudge after a gh flake")


if __name__ == "__main__":
    unittest.main()
