"""#1092 fix-forward — the job-25 report-owed rider must treat a kind-OFF
suppression as a distinct `withheld` decision, NEVER as a pane swallow.

When the `card` nudge kind is OFF (#1023 per-kind staging — gk today, all
streams at 9/13), `watchdog.send_verified(..., nudge="card")` returns False
because `keys()` suppresses the keystroke: nothing is typed. Before this
fix-forward `report_reconcile` could not tell that False apart from a genuine
pane swallow, so every ~600 s re-probe grew `swallows`, wrote two journal noise
lines per sweep per root (`nudges OFF: suppressed card` + `report-owed
swallowed`), and at `REPORT_MAX_SWALLOWS` fired the one-shot OWNER escalation
ping — a false owner alert caused purely by the kind being off (live on gk:
`report-owed ESCALATE …/odoo-erp -> sent issues=#4` at 08:17, for a ticket
never typed into any pane).

Approach 1 (the decided design): a `nudges_enabled=None` seam on
`report_reconcile` (defaulting to the package `nudges_enabled` facade); after
the mutex/recent-human vetoes and before the #511 backoff, if `card` is OFF the
rider journals ONE `report-owed withheld (kind card off) <root> issues=…`
line, leaves `swallows`/`last_try`/`nudged` untouched, never calls the send
primitive, and never escalates.

These tests inject the seam explicitly: the suite's conftest sets
`AIRULESET_TEST_IGNORE_DISABLE=1`, so the REAL `nudges_enabled` predicate
returns True in-test — the kind-OFF path is only reachable by injecting
`nudges_enabled=lambda k: False`.
"""

import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402,F401
import watchdog.cards as cards  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal test kit (same shape as tests/test_report_reconcile.py — kept local
# so this suite is hermetic and independent of that file's internals).
# --------------------------------------------------------------------------- #

SUP_ROOT = "/home/dev/proj"
SUP_CWD = "/home/dev/proj"
SID = "sup-sid-1"


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def arow(ts_iso, text, typ="assistant"):
    """A CC transcript jsonl row (dict)."""
    return {"type": typ, "timestamp": ts_iso,
            "message": {"content": [{"type": "text", "text": text}]}}


def make_git(root, closes):
    """Fake git_run. `closes` = {issue_num: commit_epoch_ts}."""
    def g(argv, timeout=10):
        if "rev-parse" in argv and "--show-toplevel" in argv:
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


class _RecordingSend:
    """Records every in-band verified_send call. `result` is what it returns
    (True = delivered, False = swallowed) IF it is ever called."""

    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, pane_id, text, tpath):
        self.calls.append((pane_id, text, tpath))
        return self.result


def run(now, state, closes, *, nudges_enabled, vs_result=True,
        send_fn=None, rows=None, dry_run=False):
    """Drive report_reconcile with the seam injected and every I/O faked.

    `nudges_enabled` is REQUIRED (the point of these tests). `send_fn` is the
    owner-escalation ping fn (recorded so we can assert it never fires)."""
    if rows is None:
        rows = [arow(iso(now - 100), "⏳ WORKING: on to the next")]  # no report boundary
    vs = _RecordingSend(vs_result)
    logs = cards.report_reconcile(
        now, None, state, {SID: SUP_CWD}, {SID: ("%42", "captured")},
        send_fn=send_fn, dry_run=dry_run,
        git_run=make_git(SUP_ROOT, closes),
        owner_by_sid={SID: "zbynek"}, projects_dir="/p",
        grace=0, reprobe=0,
        mutex_held=lambda root: False,
        recent_human=lambda c, s: False,
        transcript_fn=lambda pd, s, c: "/p/%s.jsonl" % s,
        rows_fn=lambda tp: rows,
        verified_send=vs,
        compact_log_path=None,
        nudges_enabled=nudges_enabled)
    return logs, vs


def _count(logs, needle):
    return sum(1 for line in logs if needle in line)


class ReportOwedKindOff(unittest.TestCase):
    """Case A — kind OFF: the rider withholds, never swallows, never escalates."""

    def test_kind_off_withholds_without_send_or_swallow(self):
        now = 100000
        closes = {41: now - 3600}          # closed 1h ago, well past grace
        # seed a NON-zero swallow streak so we can prove it is left untouched.
        state = {"report_owed": {SUP_ROOT: {
            "nudged": {}, "pinged": {}, "swallows": 1, "last_try": now - 5000}}}

        send_pings = []

        def send_fn(text, owner=None, dedup_key=None, dry_run=False, project=None):
            send_pings.append((text, owner))
            return "sent"

        logs, vs = run(now, state, closes,
                       nudges_enabled=lambda k: False, send_fn=send_fn)

        # the send primitive is NEVER reached — nothing is typed.
        self.assertEqual(0, len(vs.calls),
                         "verified_send must not be called when the kind is OFF")
        # the owner is NEVER escalated from a kind-off state.
        self.assertEqual(0, len(send_pings), "no owner escalation ping")
        # exactly ONE withheld decision line, and no swallow/escalate noise.
        self.assertEqual(1, _count(logs, "report-owed withheld (kind card off)"),
                         "exactly one withheld line: %r" % logs)
        self.assertEqual(0, _count(logs, "swallowed"),
                         "kind-off must not log a swallow: %r" % logs)
        self.assertEqual(0, _count(logs, "ESCALATE"),
                         "kind-off must not escalate: %r" % logs)
        # the withheld line names the root and the owed issue.
        withheld = [ln for ln in logs if "withheld (kind card off)" in ln][0]
        self.assertIn(SUP_ROOT, withheld)
        self.assertIn("#41", withheld)
        # the print-always detection line (#36) still fires.
        self.assertTrue(any(ln.startswith("report-owed %s sid=" % SUP_ROOT)
                            for ln in logs),
                        "detection line must stay print-always: %r" % logs)
        # swallows / last_try untouched; ticket stays owed (not nudged).
        saved = state["report_owed"][SUP_ROOT]
        self.assertEqual(1, saved["swallows"], "swallows must be untouched")
        self.assertEqual(now - 5000, saved["last_try"],
                         "last_try must be untouched")
        self.assertNotIn("41", saved["nudged"],
                         "the ticket stays owed for when the kind is re-enabled")


class ReportOwedKindOffNoEscalationAtThreshold(unittest.TestCase):
    """Case B — kind OFF with a streak ONE below the escalation threshold:
    the rider must still not escalate (the whole false-ping class)."""

    def test_kind_off_below_threshold_never_escalates(self):
        now = 100000
        closes = {41: now - 3600}
        # swallows = REPORT_MAX_SWALLOWS - 1: a genuine swallow here WOULD tip
        # into escalation on this sweep; kind-off must not.
        seeded = cards.REPORT_MAX_SWALLOWS - 1
        state = {"report_owed": {SUP_ROOT: {
            "nudged": {}, "pinged": {}, "swallows": seeded, "last_try": 0}}}

        escalations = []

        def send_fn(text, owner=None, dedup_key=None, dry_run=False, project=None):
            escalations.append(text)
            return "sent"

        logs, vs = run(now, state, closes,
                       nudges_enabled=lambda k: False, send_fn=send_fn)

        self.assertEqual(0, len(escalations),
                         "no escalation ping from a kind-off state, even at "
                         "streak %d (threshold %d)"
                         % (seeded, cards.REPORT_MAX_SWALLOWS))
        self.assertEqual(0, len(vs.calls), "verified_send must not be called")
        self.assertEqual(1, _count(logs, "report-owed withheld (kind card off)"))
        self.assertEqual(0, _count(logs, "ESCALATE"))
        # streak stays exactly where it was seeded.
        self.assertEqual(seeded, state["report_owed"][SUP_ROOT]["swallows"])


class ReportOwedKindOnUnchanged(unittest.TestCase):
    """Case C — kind ON: the seam is a no-op; the existing send path runs."""

    def test_kind_on_still_nudges(self):
        now = 100000
        closes = {41: now - 3600}
        state = {}
        logs, vs = run(now, state, closes,
                       nudges_enabled=lambda k: True, vs_result=True)

        # the in-band nudge is delivered exactly as before the fix-forward.
        self.assertEqual(1, len(vs.calls),
                         "kind ON must still nudge the supervisor pane")
        self.assertIn("#41", vs.calls[0][1])
        # no withheld line when the kind is ON.
        self.assertEqual(0, _count(logs, "withheld (kind card off)"))
        # the delivered ticket is marked nudged (permanent dedup).
        self.assertIn("41", state["report_owed"][SUP_ROOT]["nudged"])

    def test_kind_on_swallow_still_grows_streak(self):
        # ON path unchanged: a GENUINE swallow (verified_send -> False) still
        # advances the swallow streak — the seam must not touch this.
        now = 100000
        closes = {41: now - 3600}
        state = {}
        logs, vs = run(now, state, closes,
                       nudges_enabled=lambda k: True, vs_result=False)
        self.assertEqual(1, len(vs.calls))
        self.assertEqual(1, state["report_owed"][SUP_ROOT]["swallows"],
                         "a genuine swallow at kind ON still counts")
        self.assertEqual(1, _count(logs, "swallowed"))
        self.assertEqual(0, _count(logs, "withheld (kind card off)"))


if __name__ == "__main__":
    unittest.main()
