"""#1378 (owner 19.11.2026) -- Claude-born tickets need a worth-it bar and a
per-stream net-flow brake.

Owner, verbatim: "david3 ma neustale rastuci pocet issues ktore ani za rok nebudu
vyriesene pritom my mame akutne spustanie prevadzky a za den sa vyriesi tak 5
issues no vygeneruje sa 20!!"

ROZHODNUTÉ (answer "1"): (1) a Claude-born ticket (`Scope-gate: separate-work`,
no `Requested by <owner>/<repo>#N` handover from ANOTHER project) only for a real
defect someone hits, proven by an `Impact: <who is hit and how>` line; a nit /
cleanup / 🔵 finding is fixed in the PR in hand or dropped; (2) one screen / one
flow = one ticket with `- [ ]` lines in its DESCRIPTION, never comments; (3) a
per-stream net-flow brake over 72 h: Claude-born filed >= closed (completed) ->
BLOCK; a gh error fails OPEN with a logged `brake-unverified`; (4) worker
`followup_candidates:` carry their Impact and are not filed by default.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOOK = ROOT / "hooks" / "block-ungated-issue-filing.sh"

GOOD_IMPACT = "Impact: clients get an invoice with the wrong VAT total on the order screen"


def _born(n):
    """`n` Claude-born separate-work issue rows, as the created query returns them."""
    return [{"number": 1000 + i,
             "body": "Dedup-checked: x\nFound by gk r3 \U0001f7e12\nScope-gate: separate-work\n"}
            for i in range(n)]


def _done(n, reason="COMPLETED"):
    return [{"number": 2000 + i, "stateReason": reason} for i in range(n)]


def _flow_gh(tmpdir, created=(), closed=(), fail=False, call_log=None):
    """A fake `gh` for the brake: `issue list … created:>=…` prints `created` rows
    as JSON, `issue list … closed:>=…` prints `closed` rows, any other `issue
    list` (the near-duplicate check) prints `[]`, everything else exits 1 (so
    `label list` reads as not stream-aware). `fail` makes both brake queries exit
    1 (a gh error). `call_log` gets one line per invocation."""
    bin_dir = Path(tmpdir) / "flowbin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "joined = ' '.join(argv)\n"
        "call_log = %r\n"
        "if call_log:\n"
        "    with open(call_log, 'a', encoding='utf-8') as fh:\n"
        "        fh.write(joined + chr(10))\n"
        "if argv[:2] == ['issue', 'list']:\n"
        "    for key, rows in (('created:', %r), ('closed:', %r)):\n"
        "        if key in joined:\n"
        "            if %r:\n"
        "                sys.exit(1)\n"
        "            print(json.dumps(rows)); sys.exit(0)\n"
        "    print('[]'); sys.exit(0)\n"
        "sys.exit(1)\n" % (call_log, list(created), list(closed), fail))
    (bin_dir / "gh").chmod(0o755)
    return str(bin_dir)


def _body(extra="", crit="separate-work"):
    return ("Dedup-checked: searched open issues for the VAT total symptom, none\n"
            "The order screen computes the VAT total from the rounded line totals.\n"
            "%sScope-gate: %s\n" % (extra, crit))


def _cmd(body, labels=(), repo="o/r"):
    title = "vat total %s" % uuid.uuid4().hex[:8]
    flags = "".join(" -l %s" % lb for lb in labels)
    return "cat > body.md <<'EOF'\n%s\nEOF\ngh issue create -R %s%s -t %r -F body.md" % (
        body, repo, flags, title)


def _away_sid(testcase):
    sid = "t1378-away-" + uuid.uuid4().hex[:10]
    mark = Path("/tmp/claude-user-active-%s" % sid)
    mark.write_text("")
    old = time.time() - 1000
    os.utime(mark, (old, old))
    testcase.addCleanup(lambda: mark.unlink(missing_ok=True))
    return sid


def _run(cmd, gh_bin, home, session_id=None):
    payload = json.dumps({"tool_input": {"command": cmd},
                          "session_id": session_id or "t1378-" + uuid.uuid4().hex[:8]})
    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = gh_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                          text=True, env=env, cwd=tempfile.mkdtemp(prefix="t1378-cwd-"))


class TestImpactBar(unittest.TestCase):
    """Point 1: the Impact line, pure predicate."""

    def setUp(self):
        from gates.filing import worth
        self.w = worth

    def test_real_defects_pass(self):
        for line in (GOOD_IMPACT,
                     "Impact: klienti dostanú faktúru so zlou sumou DPH",
                     "Impact: none of the clients can log in after the release",
                     "impact: the warehouse loses the stock reservation on save",
                     "Impact: the legal deadline for the VAT return is missed"):
            self.assertIsNone(self.w.impact_reason(_body(line + "\n")), line)

    def test_missing_line_is_no_impact(self):
        reason = self.w.impact_reason(_body())
        self.assertTrue(reason.startswith("no-impact"), reason)

    def test_weak_values_are_rejected(self):
        for line in ("Impact:", "Impact:   ", "Impact: data", "Impact: wrong money",
                     "Impact: <who is hit and how>",
                     "Impact: none", "Impact: n/a — internal only", "Impact: N/A",
                     "Impact: no user impact, internal helper",
                     "Impact: žiadny dopad na klienta",
                     "Impact: code cleanup of the order helper",
                     "Impact: refactor for readability of the module",
                     "Impact: consistency with the other screens",
                     "Impact: gk r3 \U0001f5351 naming of the helper function",
                     "Impact: hygiene of the shared test fixtures",
                     "Impact: a nit from the second gk review round",
                     "Impact: upratovanie kódu v module objednávok",
                     "Impact: cosmetic spacing on the dashboard header"):
            reason = self.w.impact_reason(_body(line + "\n"))
            self.assertIsNotNone(reason, line)
            self.assertTrue(reason.startswith("impact-not-a-defect")
                            or reason.startswith("no-impact"), (line, reason))

    def test_one_valid_line_among_several_is_enough(self):
        self.assertIsNone(self.w.impact_reason(_body("Impact:\n" + GOOD_IMPACT + "\n")))

    def test_reason_names_the_exits(self):
        reason = self.w.impact_reason(_body())
        self.assertIn("Impact: <who is hit and how>", reason)
        self.assertIn("PR in hand", reason)
        self.assertIn("\U0001f535", reason)
        self.assertNotIn("\n", reason)
        self.assertNotIn("\t", reason)


class TestClaudeBorn(unittest.TestCase):

    def setUp(self):
        from gates.filing import worth
        self.w = worth

    def test_separate_work_without_handover_is_claude_born(self):
        self.assertTrue(self.w.is_claude_born("separate-work", _body(), "o/r"))
        self.assertTrue(self.w.is_claude_born("Separate-Work", _body(), "o/r"))

    def test_other_criteria_are_not(self):
        for crit in ("user-request", "cross-cutting", ">300-loc", None):
            self.assertFalse(self.w.is_claude_born(crit, _body(crit=str(crit)), "o/r"), crit)

    def test_handover_from_another_project_is_not(self):
        for line in ("Requested by other/proj#12", "Requested by Other/Proj#12.",
                     "Requested by https://github.com/other/proj/issues/12"):
            self.assertFalse(self.w.is_claude_born(
                "separate-work", _body(line + "\n"), "o/r"), line)

    def test_requested_by_the_same_repo_is_no_handover(self):
        self.assertTrue(self.w.is_claude_born(
            "separate-work", _body("Requested by o/r#12\n"), "o/r"))
        self.assertTrue(self.w.is_claude_born(
            "separate-work", _body("Requested by zbynekdrlik/r#12\n"), "r"))

    def test_scope_criterion_prefers_an_accepted_token(self):
        self.assertEqual(self.w.scope_criterion(
            "Scope-gate: >300 LoC? nie\nScope-gate: separate-work\n"), "separate-work")
        self.assertIsNone(self.w.scope_criterion("no gate line"))


class TestBrake(unittest.TestCase):
    """Point 3: the decision with INJECTED counts, and the live counter with an
    injected runner -- no network."""

    NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        from gates.filing import worth
        self.w = worth
        self.w._flow_cache.clear()
        self.calls = []

    def _runner(self, created, closed, fail=False, raw=None):
        def run(argv, cwd):
            self.calls.append(argv)
            if fail:
                return None, "gh exited 1"
            if raw is not None:
                return raw, None
            joined = " ".join(argv)
            return json.dumps(created if "created:" in joined else closed), None
        return run

    def test_decision(self):
        self.assertTrue(self.w.brake_blocks(0, 0))
        self.assertTrue(self.w.brake_blocks(3, 3))
        self.assertTrue(self.w.brake_blocks(33, 3))
        self.assertFalse(self.w.brake_blocks(2, 3))

    def test_scope_is_the_filing_stream_label(self):
        self.assertEqual(self.w.brake_scope(["stream:david3"]), "stream:david3")
        self.assertIsNone(self.w.brake_scope([]))

    def test_counts_only_claude_born_created_and_completed_closes(self):
        created = _born(2) + [
            {"number": 7, "body": "Scope-gate: user-request\n"},
            {"number": 8, "body": "Requested by other/proj#3\nScope-gate: separate-work\n"},
            {"number": 9, "body": None}]
        closed = _done(2) + _done(1, "NOT_PLANNED") + [{"number": 5}]
        counts, err = self.w.flow_counts("o/r", "stream:david3", "/", now=self.NOW,
                                         runner=self._runner(created, closed))
        self.assertIsNone(err)
        self.assertEqual(counts, (2, 2))

    def test_queries_are_72h_scoped_and_labelled(self):
        self.w.flow_counts("o/r", "stream:david3", "/", now=self.NOW,
                           runner=self._runner([], []))
        self.assertEqual(len(self.calls), 2)
        joined = [" ".join(a) for a in self.calls]
        self.assertTrue(any("created:>=2026-11-16T12:00:00Z" in j for j in joined), joined)
        self.assertTrue(any("closed:>=2026-11-16T12:00:00Z" in j for j in joined), joined)
        for a in self.calls:
            self.assertIn("--label", a)
            self.assertEqual(a[a.index("--label") + 1], "stream:david3")
            self.assertEqual(a[a.index("-R") + 1], "o/r")

    def test_no_scope_means_the_whole_repo(self):
        self.w.flow_counts("o/r", None, "/", now=self.NOW, runner=self._runner([], []))
        for a in self.calls:
            self.assertNotIn("--label", a)

    def test_cached_per_invocation(self):
        run = self._runner(_born(1), _done(1))
        self.w.flow_counts("o/r", "stream:x", "/", now=self.NOW, runner=run)
        self.w.flow_counts("o/r", "stream:x", "/", now=self.NOW, runner=run)
        self.assertEqual(len(self.calls), 2)

    def test_gh_errors_are_unmeasurable(self):
        for kw in ({"fail": True}, {"raw": "0"}, {"raw": "not json"}, {"raw": "{}"}):
            self.w._flow_cache.clear()
            counts, err = self.w.flow_counts("o/r", None, "/", now=self.NOW,
                                             runner=self._runner([], [], **kw))
            self.assertIsNone(counts, kw)
            self.assertTrue(err, kw)

    def test_block_reason_end_to_end_pure(self):
        log = Path(tempfile.mkdtemp(prefix="t1378-log-")) / "scope-gate.log"
        body = _body(GOOD_IMPACT + "\n")
        r = self.w.block_reason("separate-work", body, "o/r", ["stream:david3"], "/",
                                str(log), "t", runner=self._runner(_born(33), _done(3)),
                                now=self.NOW)
        self.assertTrue(r.startswith("net-flow-brake"), r)
        self.assertIn("stream:david3", r)
        self.assertIn("33", r)
        self.w._flow_cache.clear()
        r = self.w.block_reason("separate-work", body, "o/r", [], "/", str(log), "t",
                                runner=self._runner(_born(1), _done(4)), now=self.NOW)
        self.assertIsNone(r)
        self.w._flow_cache.clear()
        r = self.w.block_reason("separate-work", body, "o/r", [], "/", str(log), "t",
                                runner=self._runner([], [], fail=True), now=self.NOW)
        self.assertIsNone(r)
        text = log.read_text(encoding="utf-8")
        self.assertIn("brake-unverified", text)
        self.assertNotIn("verdict=", text)
        # not Claude-born: never asks for Impact, never counts
        self.calls.clear()
        self.assertIsNone(self.w.block_reason("user-request", _body(crit="user-request"),
                                              "o/r", [], "/", str(log), "t",
                                              runner=self._runner(_born(9), []),
                                              now=self.NOW))
        self.assertEqual(self.calls, [])


class TestWorthGateEndToEnd(unittest.TestCase):
    """Through the real hook with a fake gh."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t1378-")
        self.home = tempfile.mkdtemp(prefix="t1378-home-")
        self.log = Path(self.tmp) / "calls.log"

    def _gh(self, created=(), closed=(), fail=False):
        return _flow_gh(self.tmp, created, closed, fail=fail, call_log=str(self.log))

    def test_no_impact_blocks(self):
        r = _run(_cmd(_body(), labels=["stream:david3"]), self._gh(_born(0), _done(9)), self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> no-impact", r.stderr)

    def test_a_cleanup_impact_blocks(self):
        r = _run(_cmd(_body("Impact: code cleanup of the order helper\n")),
                 self._gh(_born(0), _done(9)), self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> impact-not-a-defect", r.stderr)

    def test_a_real_defect_on_a_draining_stream_passes(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n"), labels=["stream:david3"]),
                 self._gh(_born(1), _done(5)), self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        brake = [ln for ln in self.log.read_text().splitlines()
                 if "created:" in ln or "closed:" in ln]
        self.assertEqual(len(brake), 2, brake)
        for ln in brake:
            self.assertIn("--label stream:david3", ln)
            self.assertIn("-R o/r", ln)

    def test_a_stream_filing_faster_than_it_closes_is_braked(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n"), labels=["stream:david3"]),
                 self._gh(_born(33), _done(3)), self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> net-flow-brake", r.stderr)
        self.assertIn("stream:david3", r.stderr)
        self.assertIn("DESCRIPTION", r.stderr)
        self.assertIn("never a comment", r.stderr)

    def test_zero_closes_and_zero_filings_still_brakes(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n")), self._gh([], []), self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> net-flow-brake", r.stderr)

    def test_not_planned_closes_do_not_drain(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n")), self._gh(_born(2), _done(9, "NOT_PLANNED")),
                 self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> net-flow-brake", r.stderr)

    def test_unlabelled_filing_counts_the_whole_repo(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n")), self._gh(_born(0), _done(2)), self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        brake = [ln for ln in self.log.read_text().splitlines()
                 if "created:" in ln or "closed:" in ln]
        self.assertEqual(len(brake), 2, brake)
        self.assertFalse(any("--label" in ln for ln in brake), brake)

    def test_gh_error_fails_open_and_logs(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n")), self._gh(fail=True), self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (Path(self.home) / ".claude" / "scope-gate.log").read_text(encoding="utf-8")
        self.assertIn("brake-unverified", log)

    def test_gh_error_never_waives_the_impact_rule(self):
        r = _run(_cmd(_body()), self._gh(fail=True), self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> no-impact", r.stderr)

    def test_owner_requests_and_handovers_and_other_criteria_are_untouched(self):
        gh = self._gh(_born(50), _done(0))
        for body in (_body(crit="user-request"),
                     _body("Requested by other/proj#12\n"),
                     _body(crit="cross-cutting")):
            r = _run(_cmd(body), gh, self.home)
            self.assertEqual(r.returncode, 0, (body, r.stderr))

    def test_unattended_filings_are_braked_too(self):
        r = _run(_cmd(_body(GOOD_IMPACT + "\n")), self._gh(_born(5), _done(1)), self.home,
                 session_id=_away_sid(self))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("-> net-flow-brake", r.stderr)


class TestDoctrine1378(unittest.TestCase):

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_block_wall_names_the_bar_and_the_brake(self):
        from gates.filing import render
        low = render.BLOCK_MSG.lower()
        self.assertNotIn("no net-drain brake", low)
        self.assertNotIn("fold", low)
        self.assertIn("Impact: <who is hit and how>", render.BLOCK_MSG)
        self.assertIn("net-flow-brake", render.BLOCK_MSG)
        self.assertIn("DESCRIPTION", render.BLOCK_MSG)
        self.assertIn("Impact:", render.WORKER_MSG)

    def test_module_states_the_bar_in_no_more_bytes(self):
        rel = "modules/quality/no-dropped-work.md"
        text = self._read(rel)
        self.assertLessEqual(len(text.encode("utf-8")), 1184)
        self.assertNotIn("A NEW ticket for every separate deliverable", text)
        self.assertIn("`Impact: <who is hit and how>`", text)
        self.assertIn("one flow", text)
        self.assertIn("never comments", text)
        self.assertIn("\U0001f535", text)

    def test_no_surface_still_promises_a_ticket_per_item_or_no_brake(self):
        bad = ("no net-drain brake", "every separate deliverable gets its own ticket",
               "always gets its own ticket", "filed as its own ticket linked to")
        hits = []
        for top in ("modules", "skills", "agents", "hooks", "gates", "rules"):
            for p in (ROOT / top).rglob("*"):
                if p.is_file() and p.suffix in (".md", ".sh", ".py", ".conf"):
                    low = p.read_text(encoding="utf-8", errors="replace").lower()
                    hits += ["%s: %s" % (p.relative_to(ROOT), b) for b in bad if b in low]
        self.assertEqual(hits, [])

    def test_review_findings_doctrine(self):
        deep = self._read("skills/no-dropped-work-deep/DEEP.md")
        fm2 = deep[deep.index("#### Failure mode 2"):deep.index("#### Failure mode 3")]
        self.assertIn("\U0001f535", fm2)
        self.assertIn("never a ticket", fm2)
        self.assertIn("Impact:", fm2)

    def test_followup_candidates_carry_their_impact(self):
        for rel in ("agents/autopilot-worker.md", "skills/autopilot/SKILL.md"):
            text = self._read(rel)
            idx = [i for i in range(len(text)) if text.startswith("(title + why it is separate", i)]
            self.assertTrue(idx, rel)
            for i in idx:
                self.assertIn("Impact:", text[i:i + 160], rel)
        skill = self._read("skills/autopilot/SKILL.md")
        self.assertIn("files only the candidates that pass", skill)

    def test_untracked_work_hook_asks_for_the_impact(self):
        hook = self._read("hooks/stop-check-untracked-work.sh")
        self.assertIn("Impact:", hook)


if __name__ == "__main__":
    unittest.main()
