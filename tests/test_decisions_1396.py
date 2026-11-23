"""tests/test_decisions_1396.py -- #1396: epic decisions collector + hand-off gate.

`airuleset.py decisions <owner/repo>#<N>` prints every `ROZHODNUTÉ` of an epic
FAMILY (the epic, its `Part of #E` / `Epic: #E` children, its checklist refs),
verbatim, newest first. The hand-off gate refuses (enforce) / warns (default) a
child ticket whose newest `Decisions-checked: #E (<n> read)` line is older than
the newest family decision or counts a different n. Every gh read goes through
the `gates.ghread` runner seam, so no test here reaches real gh.
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from gates import decisions as dc  # noqa: E402
import cli_decisions  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"
OTHER = "zbynekdrlik/other"
EPIC = 6656
ENFORCE = {dc.MODE_ENV: "enforce"}
WARN = {dc.MODE_ENV: "warn"}


def _issue(slug, n, body, title=None, created="2026-10-01T08:00:00Z", pr=False):
    obj = {"number": n, "title": title or "ticket %d" % n, "body": body,
           "html_url": "https://github.com/%s/issues/%d" % (slug, n),
           "created_at": created, "user": {"login": "zbynekdrlik"},
           "repository_url": "https://api.github.com/repos/%s" % slug}
    if pr:
        obj["pull_request"] = {"url": "x"}
    return obj


def _c(cid, created, body, login="david4", slug=SLUG, n=1):
    return {"id": cid, "created_at": created, "body": body,
            "user": {"login": login},
            "html_url": "https://github.com/%s/issues/%d#issuecomment-%d"
                        % (slug, n, cid)}


class FakeGH:
    """`runner(argv) -> (rc, out, err)` over an in-memory issue store."""

    def __init__(self, issues, comments, fail=(), incomplete=False, total=None,
                 page=None):
        self.issues = {(i["repository_url"].split("/repos/")[1], i["number"]): i
                       for i in issues}
        self.comments = comments
        self.fail = set(fail)
        self.incomplete = incomplete
        self.total = total        # a total_count above the items = the 1000 cap
        self.page = page          # split items into pages of this size
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] != ["gh", "api"]:
            return 1, "", "unexpected argv %r" % (argv,)
        path = [a for a in argv[2:] if a.startswith(("repos/", "search/"))][0]
        if path.startswith("search/"):
            if "search" in self.fail:
                return 1, "", "HTTP 502 Bad Gateway"
            q = [a for a in argv if a.startswith("q=")][0][2:]
            slug = re.search(r"repo:(\S+)", q).group(1)
            num = re.search(r"\b(\d+) in:body", q).group(1)
            items = [i for (s, _), i in self.issues.items()
                     if s == slug and num in (i.get("body") or "")]
            total = len(items) if self.total is None else self.total
            size = self.page or max(len(items), 1)
            lines = []
            for k in range(0, max(len(items), 1), size):   # gh --paginate -q
                lines += [json.dumps(self.incomplete), json.dumps(total)]
                lines += [json.dumps(i) for i in items[k:k + size]]
            return 0, "\n".join(lines), ""
        m = re.match(r"^repos/([^/]+/[^/]+)/issues/(\d+)(/comments)?$", path)
        slug, n, is_c = m.group(1), int(m.group(2)), bool(m.group(3))
        if is_c:
            if ("comments", slug, n) in self.fail or "comments" in self.fail:
                return 1, "", "API rate limit exceeded"
            return 0, "\n".join(json.dumps(c) for c in
                                self.comments.get((slug, n), [])), ""
        if ("issue", slug, n) in self.fail:
            return 1, "", "HTTP 404 Not Found"
        obj = self.issues.get((slug, n))
        if obj is None:
            return 1, "", "HTTP 404 Not Found"
        return 0, json.dumps(obj), ""


EPIC_BODY = ("Epic for Široké.\n\n## Plan\n- [x] #8860 ingredients\n"
             "- [ ] zbynekdrlik/other#12 printer side\n- [ ] #9999 not a member? yes it is\n")


def _family(extra_comments=None, child_comments=()):
    issues = [
        _issue(SLUG, EPIC, EPIC_BODY, title="Široké epic"),
        _issue(SLUG, 8860, "Part of #6656\n\nlabels", title="Zloženie"),
        _issue(SLUG, 10385, "**Epic:** #6656\n\nŠiroké labels", title="Etikety"),
        _issue(SLUG, 10457, "Part of zbynekdrlik/odoo-erp#6656\nprice tags",
               title="Cenovky"),
        _issue(SLUG, 9999, "checklist-only child", title="Checklist child"),
        _issue(OTHER, 12, "printer side", title="Printer"),
        # NOT members: prose mention, quoted, other epic number, other repo form.
        _issue(SLUG, 7000, "see the epic 6656 for context", title="Prose"),
        _issue(SLUG, 7001, "> Part of #6656\nquoted only", title="Quoted"),
        _issue(SLUG, 7002, "Part of #66567", title="Longer number"),
        _issue(SLUG, 7003, "Part of zbynekdrlik/third#6656", title="Other repo epic"),
        _issue(SLUG, 7004, "Part of #6656", title="A PR", pr=True),
    ]
    comments = {
        (SLUG, 8860): [
            _c(84, "2026-10-08T07:00:00Z",
               "ROZHODNUTÉ (q3): polotovary sa rozpustia, rovnaká surovina "
               "sa sčíta a uvedie raz.", login="zbynekdrlik", n=8860),
            _c(85, "2026-10-09T07:00:00Z", "just a status note", n=8860),
        ],
        (SLUG, 10457): [
            _c(90, "2026-11-23T09:00:00Z",
               "Owner wrote:\n- **ROZHODNUTÉ:** na cenovkách Širokého nesmie "
               "byť Pekárova žena", login="zbynekdrlik", n=10457),
            _c(91, "2026-11-23T10:00:00Z",
               "> ROZHODNUTÉ: quoted, not a decision", n=10457),
            _c(92, "2026-11-23T11:00:00Z",
               "```\nROZHODNUTÉ: inside a fence\n```", n=10457),
        ],
        (OTHER, 12): [
            _c(95, "2026-10-20T07:00:00Z", "ROZHODNUTÉ: tlačiareň Zebra",
               login="zbynekdrlik", slug=OTHER, n=12),
        ],
        (SLUG, 10385): list(child_comments),
    }
    for k, v in (extra_comments or {}).items():
        comments.setdefault(k, []).extend(v)
    # The epic body itself carries a decision paragraph.
    issues[0]["body"] = EPIC_BODY + ("\nROZHODNUTÉ: logo vľavo hore.\nDruhý "
                                     "riadok rozhodnutia.\n\nnext paragraph\n")
    return issues, comments


class TestParsing(unittest.TestCase):
    def test_parent_refs_forms(self):
        self.assertEqual(dc.parent_refs("Part of #12"), [(None, 12)])
        self.assertEqual(dc.parent_refs("- **Epic:** [#12](u)"), [(None, 12)])
        self.assertEqual(dc.parent_refs("Part of a/b#12"), [("a/b", 12)])
        self.assertEqual(dc.parent_refs("> Part of #12"), [])
        self.assertEqual(dc.parent_refs("the epic: #12 is big"), [])
        self.assertEqual(dc.parent_refs("```\nPart of #12\n```"), [])
        self.assertEqual(dc.parent_refs("Epic-rehearsal: #12"), [])

    def test_checklist_refs(self):
        self.assertEqual(dc.checklist_refs(EPIC_BODY),
                         [(None, 8860), (OTHER, 12), (None, 9999)])
        self.assertEqual(dc.checklist_refs("- [ ] write #5 later"), [])
        self.assertEqual(dc.checklist_refs("1. [ ] #5\n+ [x] #6"),
                         [(None, 5), (None, 6)])

    def test_traversal_slug_is_skipped(self):
        self.assertEqual(dc.checklist_refs("- [ ] ../..#5\n- [ ] a/..#6"), [])
        self.assertEqual(dc.parent_refs("Part of ../..#5"), [])

    def test_parent_refs_line_order(self):
        self.assertEqual(dc.parent_refs("Epic: #3\nPart of #4"),
                         [(None, 3), (None, 4)])

    def test_regexes_linear_on_long_lines(self):
        import time
        bad = ["Part of" + " " * 100000 + "x", "Epic:" + " " * 100000 + "x",
               "- [ ]" + " " * 100000 + "x", "ROZHODNUT" + "*" * 100000,
               "Decisions-checked: #1 (" + " " * 100000 + "x",
               "Part of" + " *:" * 33000 + "x", "Part of " + "a" * 100000,
               "- [x] a/" + "a." * 50000, "Epic: " + "a-" * 50000 + "#"]
        t0 = time.time()
        for line in bad:
            dc.parent_refs(line)
            dc.checklist_refs(line)
            dc.checked_markers(line)
            dc.is_decision_comment(line)
            dc.decision_paragraphs(line)
        self.assertLess(time.time() - t0, 2.0)

    def test_decision_comment_detection(self):
        self.assertTrue(dc.is_decision_comment("ROZHODNUTÉ: x"))
        self.assertTrue(dc.is_decision_comment("intro\n## ROZHODNUTÉ\nbody"))
        self.assertTrue(dc.is_decision_comment("- **ROZHODNUTÉ:** x"))
        self.assertFalse(dc.is_decision_comment("> ROZHODNUTÉ: quoted"))
        self.assertFalse(dc.is_decision_comment("prose ROZHODNUTÉ: mid-line"))
        self.assertFalse(dc.is_decision_comment("```\nROZHODNUTÉ: x\n```"))
        # A checked-marker comment quoting decisions never counts itself.
        self.assertFalse(dc.is_decision_comment(
            "Decisions-checked: #1 (2 read)\nROZHODNUTÉ: quoted back"))
        # Only a WELL-FORMED marker exempts; prose naming it does not.
        self.assertTrue(dc.is_decision_comment(
            "Decisions-checked: will follow\nROZHODNUTÉ: real"))

    def test_body_paragraphs_verbatim(self):
        body = "intro\n\nROZHODNUTÉ: A.\nline two\n\nother\n\n- ROZHODNUTÉ: B"
        self.assertEqual(dc.decision_paragraphs(body),
                         ["ROZHODNUTÉ: A.\nline two", "- ROZHODNUTÉ: B"])

    def test_checked_marker(self):
        self.assertEqual(dc.checked_markers("Decisions-checked: #6656 (3 read)"),
                         [(None, 6656, 3)])
        self.assertEqual(dc.checked_markers(
            "- **Decisions-checked:** zbynekdrlik/odoo-erp#6656 (12 read)"),
            [(SLUG, 6656, 12)])
        self.assertEqual(dc.checked_markers("> Decisions-checked: #6656 (3 read)"), [])


class TestCollector(unittest.TestCase):
    def setUp(self):
        issues, comments = _family()
        self.gh = FakeGH(issues, comments)

    def test_family_resolution(self):
        fam, err = dc.resolve_family(SLUG, EPIC, runner=self.gh)
        self.assertIsNone(err)
        got = sorted((m["slug"], m["number"]) for m in fam)
        self.assertEqual(got, sorted([
            (SLUG, EPIC), (SLUG, 8860), (SLUG, 10385), (SLUG, 10457),
            (SLUG, 9999), (OTHER, 12)]))

    def test_decisions_verbatim_newest_first(self):
        groups, err = dc.collect(SLUG, EPIC, runner=self.gh)
        self.assertIsNone(err)
        items = [d for g in groups for d in g["decisions"]]
        self.assertEqual(len(items), 4)
        texts = {d["html_url"]: d["text"] for d in items}
        url84 = "https://github.com/%s/issues/8860#issuecomment-84" % SLUG
        self.assertEqual(texts[url84], "ROZHODNUTÉ (q3): polotovary sa rozpustia, "
                         "rovnaká surovina sa sčíta a uvedie raz.")
        body_items = [d for d in items if d["kind"] == "body"]
        self.assertEqual(body_items[0]["text"],
                         "ROZHODNUTÉ: logo vľavo hore.\nDruhý riadok rozhodnutia.")
        # Groups ordered by their newest decision; decisions newest first.
        self.assertEqual([g["number"] for g in groups], [10457, 12, 8860, EPIC])
        flat = [d["created_at"] for d in dc.flatten(groups)]
        self.assertEqual(flat, sorted(flat, reverse=True))
        self.assertEqual(groups[0]["title"], "Cenovky")
        self.assertEqual(groups[0]["decisions"][0]["author"], "zbynekdrlik")

    def test_gh_error_is_reported_not_partial(self):
        for fail in ({"search"}, {("comments", OTHER, 12)}, {("issue", SLUG, EPIC)},
                     {("issue", OTHER, 12)}):
            issues, comments = _family()
            groups, err = dc.collect(SLUG, EPIC, runner=FakeGH(issues, comments,
                                                               fail=fail))
            self.assertIsNone(groups, fail)
            self.assertIn("gate-unavailable", err)

    def test_search_cap_is_an_error(self):
        # GitHub serves at most 1000 hits with rc 0 and incomplete=false.
        issues, comments = _family()
        groups, err = dc.collect(SLUG, EPIC, runner=FakeGH(issues, comments,
                                                           total=6093))
        self.assertIsNone(groups)
        self.assertIn("total_count=6093", err)

    def test_exactly_1000_or_no_total_is_an_error(self):
        from gates import decisions as d
        for out in ("false\n1000\n" + "\n".join('{"number": %d}' % i for i in range(1000)),
                    'false\n{"number": 1}'):
            items, err = d.search_children(SLUG, 1, runner=lambda argv, o=out: (0, o, ""))
            self.assertIsNone(items)
            self.assertIn("not complete", err)

    def test_paged_search_is_complete(self):
        issues, comments = _family()
        fam, err = dc.resolve_family(SLUG, EPIC, runner=FakeGH(issues, comments,
                                                               page=2))
        self.assertIsNone(err)
        self.assertIn(10457, [m["number"] for m in fam])

    def test_incomplete_search_is_an_error(self):
        issues, comments = _family()
        groups, err = dc.collect(SLUG, EPIC, runner=FakeGH(issues, comments,
                                                           incomplete=True))
        self.assertIsNone(groups)
        self.assertIn("incomplete", err)


class TestCLI(unittest.TestCase):
    def _run(self, ref, as_json=False, gh=None):
        issues, comments = _family()
        gh = gh or FakeGH(issues, comments)
        buf, ebuf = io.StringIO(), io.StringIO()
        args = argparse.Namespace(ref=ref, json=as_json)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc = cli_decisions.cmd_decisions(args, runner=gh)
        return rc, buf.getvalue(), ebuf.getvalue()

    def test_text_output(self):
        rc, out, _ = self._run("%s#%d" % (SLUG, EPIC))
        self.assertEqual(rc, 0)
        self.assertIn("4 decisions", out)
        self.assertIn("Cenovky", out)
        self.assertIn("> ROZHODNUTÉ (q3): polotovary sa rozpustia", out)
        # Europe/Bratislava: 2026-10-08T07:00Z is 09:00 CEST.
        self.assertIn("8.10.2026 09:00", out)
        self.assertIn("issuecomment-84", out)
        self.assertLess(out.index("Cenovky"), out.index("Zloženie"))
        # A pasted listing never counts as new decisions (quoted lines).
        self.assertFalse(dc.is_decision_comment(out))

    def test_json_output(self):
        rc, out, _ = self._run("%s#%d" % (SLUG, EPIC), as_json=True)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["epic"], "%s#%d" % (SLUG, EPIC))
        self.assertEqual(data["count"], 4)
        self.assertEqual(data["tickets"][0]["number"], 10457)

    def test_gh_error_nonzero(self):
        issues, comments = _family()
        rc, out, err = self._run("%s#%d" % (SLUG, EPIC),
                                 gh=FakeGH(issues, comments, fail={"search"}))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("decisions:", err)

    def test_bad_ref_nonzero(self):
        rc, _, err = self._run("not-a-ref")
        self.assertEqual(rc, 2)
        self.assertIn("owner/repo#N", err)

    def test_registered(self):
        import airuleset
        self.assertIs(airuleset.SUBCOMMANDS["decisions"].__module__,
                      cli_decisions.cmd_decisions.__module__)


class TestGate(unittest.TestCase):
    TICKET = 10385

    def _check(self, child_comments=(), env=ENFORCE, authority="fork-no-merge",
               fail=(), ticket=None, extra=None):
        issues, comments = _family(extra_comments=extra,
                                   child_comments=child_comments)
        gh = FakeGH(issues, comments, fail=fail)
        out = io.StringIO()
        blk, fields = dc.composer_check(ticket or self.TICKET, SLUG, runner=gh,
                                        env=env, authority=authority, out=out)
        return blk, fields, out.getvalue()

    def _marker(self, n, created="2026-11-23T12:00:00Z", ref="#6656"):
        return _c(500, created, "Design ...\nDecisions-checked: %s (%d read)\n"
                  "- ROZHODNUTÉ q3 (#8860) met: summed once" % (ref, n),
                  n=self.TICKET)

    def test_bare_marker_is_no_evidence(self):
        bare = _c(501, "2026-11-23T12:00:00Z", "Decisions-checked: #6656 (4 read)",
                  n=self.TICKET)
        blk, _, _ = self._check([bare])
        self.assertIn("names no decision", blk)
        cited = dict(bare, body=bare["body"] + "\n- issuecomment-90: brand none")
        self.assertIsNone(self._check([cited])[0])

    def test_every_parent_ref_is_gated(self):
        for decoy in ("Part of #10385", "Part of #9999"):   # self ref, no-decision epic
            issues, comments = _family()
            for i in issues:
                if i["number"] == self.TICKET:
                    i["body"] = decoy + "\nEpic: #6656"
            blk, f = dc.composer_check(self.TICKET, SLUG, runner=FakeGH(issues, comments),
                                       env=ENFORCE, authority="fork-no-merge",
                                       out=io.StringIO())
            self.assertIn("handoff BLOCK", blk or "", decoy)
            self.assertIn("#6656", f["decisions_epic"])

    def test_edited_old_marker_does_not_reattest(self):
        old = self._marker(4, created="2026-11-22T12:00:00Z")
        old["updated_at"] = "2026-11-23T12:00:00Z"
        blk, _, _ = self._check([old])
        self.assertIn("older than", blk)

    def test_pass_fresh_and_counted(self):
        blk, f, out = self._check([self._marker(4)])
        self.assertIsNone(blk)
        self.assertEqual(f["decisions_gate"], "pass")
        self.assertEqual(f["decisions_count"], 4)

    def test_pass_cross_repo_marker(self):
        blk, f, _ = self._check([self._marker(4, ref="%s#6656" % SLUG)])
        self.assertIsNone(blk)

    def test_missing_enforce_blocks(self):
        blk, f, _ = self._check([])
        self.assertTrue(blk.startswith("handoff BLOCK:"))
        self.assertIn("Decisions-checked: #6656 (4 read)", blk)
        self.assertIn("decisions", blk)

    def test_missing_warn_allows(self):
        blk, f, out = self._check([], env=WARN)
        self.assertIsNone(blk)
        self.assertEqual(f["decisions_gate"], "warn")
        self.assertIn("handoff WARN", out)

    def test_default_mode_is_warn(self):
        blk, f, out = self._check([], env={})
        self.assertIsNone(blk)
        self.assertIn("handoff WARN", out)

    def test_stale_marker_blocks(self):
        # Marker counted 4 but predates the 23.11 09:00 decision.
        blk, f, _ = self._check([self._marker(4, created="2026-11-22T12:00:00Z")])
        self.assertIn("older than", blk)
        self.assertEqual(f["decisions_gate"], "enforce")

    def test_count_mismatch_blocks(self):
        blk, f, _ = self._check([self._marker(3)])
        self.assertIn("3 read", blk)
        self.assertIn("4", blk)

    def test_edited_decision_makes_marker_stale(self):
        edited = _c(700, "2026-10-01T08:00:00Z", "ROZHODNUTÉ: edited", n=9999)
        edited["updated_at"] = "2026-11-24T08:00:00Z"
        blk, _, _ = self._check([self._marker(5)], extra={(SLUG, 9999): [edited]})
        self.assertIn("older than", blk)

    def test_new_decision_after_marker_goes_stale(self):
        late = {(SLUG, 9999): [_c(600, "2026-11-24T08:00:00Z", "ROZHODNUTÉ: new",
                                  n=9999)]}
        blk, _, _ = self._check([self._marker(5)], extra=late)
        self.assertIn("older than", blk)

    def test_no_epic_unchanged(self):
        blk, f, out = self._check([], ticket=7000)
        self.assertIsNone(blk)
        self.assertEqual(f, {})
        self.assertEqual(out, "")

    def test_epic_itself_unchanged(self):
        blk, f, _ = self._check([], ticket=EPIC)
        self.assertIsNone(blk)
        self.assertEqual(f, {})

    def test_full_authority_never_gated(self):
        blk, f, out = self._check([], authority="full")
        self.assertIsNone(blk)
        self.assertEqual(f, {})

    def test_unreadable_fails_open(self):
        for fail in ({("issue", SLUG, TestGate.TICKET)}, {"search"}):
            blk, f, out = self._check([], fail=fail)
            self.assertIsNone(blk, fail)
            self.assertEqual(f.get("decisions_gate"), "unknown")
            self.assertIn("fail-open", out)

    def test_no_decisions_passes(self):
        issues = [_issue(SLUG, 1, "plain epic"), _issue(SLUG, 2, "Part of #1")]
        gh = FakeGH(issues, {})
        blk, f = dc.composer_check(2, SLUG, runner=gh, env=ENFORCE,
                                   authority="fork-no-merge", out=io.StringIO())
        self.assertIsNone(blk)
        self.assertEqual(f["decisions_gate"], "no-decisions")


class TestWiring(unittest.TestCase):
    def test_rehearsal_block_skips_decisions_gate(self):
        import unittest.mock as mk
        import airuleset
        import gates.epic_rehearsal as er
        with mk.patch.object(er, "composer_check",
                             return_value=("handoff BLOCK: r", {"epic": 1})), \
                mk.patch.object(dc, "composer_check") as dcc:
            blk, f = airuleset._handoff_epic_preflight(5, SLUG, None, _REPO)
        self.assertEqual((blk, f), ("handoff BLOCK: r", {"epic": 1}))
        dcc.assert_not_called()

    def test_decisions_crash_keeps_rehearsal_result(self):
        import unittest.mock as mk
        import airuleset
        import gates.epic_rehearsal as er
        with mk.patch.object(er, "composer_check", return_value=(None, {"epic": 1})), \
                mk.patch.object(dc, "composer_check", side_effect=RuntimeError("x")), \
                contextlib.redirect_stderr(io.StringIO()):
            blk, f = airuleset._handoff_epic_preflight(5, SLUG, None, _REPO)
        self.assertEqual((blk, f), (None, {"epic": 1}))

    def test_preflight_merges_fields_and_blocks(self):
        import unittest.mock as mk
        import airuleset
        import gates.epic_rehearsal as er
        with mk.patch.object(er, "composer_check", return_value=(None, {"epic": 1})), \
                mk.patch.object(dc, "composer_check",
                                return_value=("handoff BLOCK: x", {"decisions_gate": "enforce"})):
            blk, f = airuleset._handoff_epic_preflight(5, SLUG, None, _REPO)
        self.assertEqual(blk, "handoff BLOCK: x")
        self.assertEqual(f, {"epic": 1, "decisions_gate": "enforce"})


if __name__ == "__main__":
    unittest.main()
