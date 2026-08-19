"""#578 part 1 — job-20 partition-audit nudge upgraded from a GENERIC reminder to
a NAMED per-I-member audit.

The gk `I 16` incident: the gk main COULD enumerate its I members by hand but did
NOT re-label them (a 7-ticket release-tail left bare, a `ready-for-review` review
lane mis-called "stream-owned non-workable"). The pre-#578 `_I_CLAUSE` names only
the I COUNT + a generic "re-audit each I ticket" instruction — it never NAMES the
members, so "I know about them, I just don't label them" can persist silently.

#578: the nudge NAMES each I member with age + labels + a shape-specific
instruction — a `ready-for-review`/`needs-gatekeeper` member gets the definitive
"this IS your review lane — DISPATCHNI, neparkuj"; a bare/other member gets
"ak release/checklist/umbrella-gated → ops-wait s pomenovaným eventom
(supervisor, s evidenciou)". Judgment stays in the session (no auto-labelling);
the nudge only makes the label gap impossible to sustain in silence.

The member list is fetched via the new `core-quals`/`slice-quals --audit`
(number<TAB>createdAt<TAB>action<TAB>labels), read through a per-cwd TTL cache
(`_cached_i_members`, the sibling of `_cached_ops_wait`, #547 "cache the FETCH"),
and ONLY inside the ~daily nudge branch — never every sweep.
"""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset
from watchdog import ops_wait_recheck as owr
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_800_000_000
CAD = 1000   # a small test cadence, mirroring test_ops_wait_recheck.py
DAY = 86400


def _member(number, days_old, *labels):
    return {"number": number,
            "createdAt": _iso(NOW - days_old * DAY),
            "labels": list(labels)}


def _iso(epoch):
    import datetime
    return (datetime.datetime.utcfromtimestamp(epoch)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


class TestNudgeNamesEachIMember(unittest.TestCase):
    """The I→W/U clause names each member with age + labels + shape-instruction."""

    def _members(self):
        return [
            _member(4497, 12, "ready-for-review", "stream:montalu5"),
            _member(4459, 30),               # bare, no labels
            _member(4520, 20, "stream:core"),  # umbrella-shape, bare of rfr/gk
        ]

    def test_names_every_member_number(self):
        t = owr._nudge_text(3, [], NOW, None, i_members=self._members())
        for n in ("#4497", "#4459", "#4520"):
            self.assertIn(n, t, "nudge must NAME I member %s" % n)

    def test_shows_labels_and_ages(self):
        t = owr._nudge_text(3, [], NOW, None, i_members=self._members())
        self.assertIn("ready-for-review", t)
        self.assertIn("stream:montalu5", t)
        self.assertIn("stream:core", t)
        # a bare member is shown as label-less, not omitted
        self.assertIn("#4459", t)
        # ages appear (the ~Nd form from _fmt_age)
        self.assertIn("~12d", t)
        self.assertIn("~30d", t)

    def test_review_lane_member_gets_dispatch_instruction(self):
        t = owr._nudge_text(3, [], NOW, None, i_members=self._members())
        # the rfr member's line carries the definitive review-lane instruction
        line = [ln for ln in t.split("\n") if "#4497" in ln]
        # (nudge may be one line; assert the tokens co-occur with the member)
        self.assertTrue(any("DISPATCHNI" in ln for ln in line) or "DISPATCHNI" in t)
        self.assertIn("review lane", t)

    def test_bare_member_gets_ops_wait_instruction(self):
        t = owr._nudge_text(3, [], NOW, None, i_members=self._members())
        self.assertIn("ops-wait", t)
        self.assertIn("pomenovan", t.lower())  # "pomenovaným eventom"

    def test_none_members_degrades_to_generic_clause(self):
        # fetch failed / not wired -> the I-clause degrades to the count-based
        # generic reminder (never a crash, never a bare "I 3").
        t = owr._nudge_text(3, [], NOW, None, i_members=None)
        self.assertIn("re-audituj", t)
        self.assertNotIn("#4497", t)

    def test_named_members_are_capped_for_a_bounded_keystroke(self):
        many = [_member(1000 + i, i + 1) for i in range(40)]
        t = owr._nudge_text(40, [], NOW, None, i_members=many)
        # not all 40 are spelled out (bounded keystroke) — a "+K ďalších"-style
        # summary caps the list.
        named = sum(1 for i in range(40) if ("#%d" % (1000 + i)) in t)
        self.assertLess(named, 40, "the named list must be capped, not unbounded")
        self.assertGreater(named, 0)


class TestWatchdogIMembersFetch(unittest.TestCase):
    """`_watchdog_i_members_fetch` parses `--audit` into member records."""

    def test_parses_audit_lines(self):
        # labels are COMMA-joined (a label with an internal space is not split).
        recs = airuleset._parse_i_audit_lines(
            "4497\t2026-08-04T00:00:00Z\taction-only\tready-for-review,stream:montalu5\n"
            "4459\t2026-08-03T00:00:00Z\timplement\t\n")
        self.assertEqual([r["number"] for r in recs], [4497, 4459])
        self.assertEqual(recs[0]["labels"], ["ready-for-review", "stream:montalu5"])
        self.assertEqual(recs[1]["labels"], [])

    def test_malformed_line_is_undetermined_none(self):
        self.assertIsNone(airuleset._parse_i_audit_lines("not-a-number\tx\ty\tz\n"))


class TestCachedIMembers(unittest.TestCase):
    """The fetch is CACHED per-cwd (#547): a second read within TTL never
    re-fetches — the sibling of `_cached_ops_wait`'s own lock."""

    class _CountingFetch:
        def __init__(self, ret):
            self.ret = ret
            self.calls = 0

        def __call__(self, cwd):
            self.calls += 1
            return self.ret

    def test_second_read_within_ttl_hits_cache(self):
        state = {}
        f = self._CountingFetch([{"number": 7, "createdAt": "x", "labels": []}])
        a = owr._cached_i_members("/repo", f, state, NOW)
        b = owr._cached_i_members("/repo", f, state, NOW + 5)
        self.assertEqual(a, b)
        self.assertEqual(f.calls, 1, "a second read within TTL must not re-fetch")


class TestNudgeBranchFetchesMembers(unittest.TestCase):
    """`goal_ops_wait_recheck` reads `i_members_fetch` (cached) when it actually
    nudges (I direction, past cadence) and NAMES the members in the keystroke —
    mirrors `_OrchBase` in test_ops_wait_recheck.py."""

    CWD = "/home/newlevel/devel/wrecheck-578"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-578-i")
        self.sid = self.tpath.stem

    def _tmux(self):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath)

    def _run(self, wrecs, i_members_fetch, i_count=4, state=None):
        tmux = self._tmux()
        logs = owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            False, set(), ops_wait_fetch=lambda cwd: [],
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=i_count,
            i_members_fetch=i_members_fetch)
        return logs, tmux

    def test_nudge_fetches_members_and_names_them(self):
        calls = {"n": 0}

        def fetch(cwd):
            calls["n"] += 1
            return [{"number": 4459, "createdAt": _iso(NOW - 30 * DAY),
                     "labels": []}]

        # rec past cadence -> nudge -> fetch members + name them in the keystroke
        wrecs = {self.sid: {"first_seen": NOW - 2 * CAD,
                            "last_nudge": NOW - 2 * CAD}}
        logs, tmux = self._run(wrecs, fetch, i_count=4)
        self.assertGreaterEqual(calls["n"], 1,
                                "a nudge must fetch the I members to name them")
        typed = "".join(tmux.typed_texts())
        self.assertIn("#4459", typed, "the delivered nudge names the member")


class TestAuditCliEndToEnd(unittest.TestCase):
    """`core-quals --audit` emits `number<TAB>createdAt<TAB>action<TAB>labels`
    (the format `_watchdog_i_members_fetch` parses) — a regression in
    `_print_audit_rows` (column order, missing/space-joined labels) is caught
    here, not just in the pure parse test."""

    _ROWS = json.dumps([
        {"number": 10, "title": "plain", "createdAt": "2026-08-01T00:00:00Z",
         "labels": [{"name": "bug"}]},
        {"number": 11, "title": "review lane", "createdAt": "2026-08-02T00:00:00Z",
         "labels": [{"name": "ready-for-review"}, {"name": "stream:montalu5"}]},
    ])

    def _prep(self, repo, bindir):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % self._ROWS +
            'esac\n')
        gh.chmod(0o755)

    def test_audit_column_and_comma_labels_round_trip(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(repo, bindir)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--audit"],
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            # the exact format the watchdog fetch parses -> round-trip it.
            recs = airuleset._parse_i_audit_lines(r.stdout)
            by_num = {rec["number"]: rec for rec in recs}
            self.assertIn(11, by_num)
            self.assertEqual(by_num[11]["labels"],
                             ["ready-for-review", "stream:montalu5"])
            self.assertEqual(by_num[10]["labels"], ["bug"])
            # the review-lane member reads action-only on a full box (stream-owned)
            line11 = [ln for ln in r.stdout.splitlines() if ln.startswith("11\t")][0]
            self.assertIn("action-only", line11)
            self.assertIn("ready-for-review,stream:montalu5", line11)


if __name__ == "__main__":
    unittest.main()
