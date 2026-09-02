"""authority_run_card tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from pathlib import Path
from unittest import TestCase, main
from authority_testlib import (  # noqa: E402
    airuleset,
    _bump_done_of,
)


class TestRunCardRemainingScopedToStream(TestCase):
    """david incident 2026-07-19: the statusline showed 'Issues 2/26' — the
    run-card's remaining was the WHOLE repo's open backlog (26) although
    david's own slice was 5. On a reduced-authority box the card's remaining
    (which feeds the statusline D/T) must count the STREAM's slice — the same
    quals as tickets-status (assignee:@me ∪ author:@me ∪ label:stream:<user>,
    non-skip); full boxes keep the repo-wide count."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                       repo_name=False, newest_card=False,
                       backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
                    repo="kvaskodev/odoo-erp", issue=1408, pr=None,
                    achieved="Fix nasadený a overený", result=None, goal="cieľ", version=None,
                    merge_sha=None, url=None, review="ok", handoff=True,
                    dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def test_reduced_authority_counts_own_slice(self):
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "assignee:@me" in j:
                return '[{"number":1},{"number":2}]'
            if "author:@me" in j:
                return '[{"number":2}]'
            if "label:stream:" in j:
                return "[]"
            return "26"          # the repo-wide count a scoped box must NOT use

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="fork-no-merge"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    with mk.patch("notify.send",
                                  side_effect=lambda body, **k: (
                                      captured.setdefault("b", body),
                                      "sent")[1]):
                        airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(m.group(1), "2", captured["b"])   # slice, NOT 26

    def test_reduced_authority_remaining_excludes_ops_channel(self):
        # #362 review: the reduced-authority "remaining" queries (both the
        # per-qual slice loop and the origin-recovery candidates query, one
        # frame up in cmd_tickets_status) must AND -label:ops-channel onto
        # the same base as -label:autopilot-skip. #4 below carries BOTH
        # assignee:@me and author:@me AND ops-channel -- if the exclusion is
        # ever dropped from the search string the fake reverts to the
        # inflated (assignee/author-only) population, so this fails against
        # a mutant that removes AUTOPILOT_SKIP_EXCL's ops-channel half.
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "-label:ops-channel" in j and "assignee:@me" in j:
                return '[{"number":1}]'
            if "assignee:@me" in j:
                return '[{"number":1},{"number":4}]'
            if "-label:ops-channel" in j and "author:@me" in j:
                return '[{"number":2}]'
            if "author:@me" in j:
                return '[{"number":2},{"number":4}]'
            if "label:stream:" in j:
                return "[]"
            return "26"

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="fork-no-merge"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    with mk.patch("notify.send",
                                  side_effect=lambda body, **k: (
                                      captured.setdefault("b", body),
                                      "sent")[1]):
                        airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "ops-channel ticket #4 leaked into the reduced-authority "
            "'remaining' count -- body: %r" % captured["b"])

    def test_full_authority_remaining_excludes_ops_channel(self):
        # #362 review: the full-authority "remaining" query must AND
        # -label:ops-channel onto EVERY per-qual search string -- #382
        # widened this from a single core-only query to a union of core +
        # needs-gatekeeper + ready-for-review, and the exclusion must
        # survive onto each of the three new queries too, not just the
        # original core one. Without it, a query would read the inflated
        # whole-population figure (5 items); with it, the correctly-scoped
        # 2 -- and since AUTOPILOT_SKIP_EXCL is the shared `base` for every
        # qual, all three per-qual queries below see the exclusion and
        # union to the SAME 2 numbers, never accumulating to 5.
        import unittest.mock as mk

        def gh(*a, **k):
            # Argv-position match for "issue view", never a substring on
            # the joined text -- "label:ready-for-review" itself contains
            # the literal substring "view" ("re-VIEW").
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            j = " ".join(str(x) for x in a)
            if "-label:ops-channel" in j:
                return '[{"number":1},{"number":2}]'
            return '[{"number":1},{"number":2},{"number":3},{"number":4},{"number":5}]'

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: (
                                  captured.setdefault("b", body),
                                  "sent")[1]):
                    airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "the full-authority obligation-scoped 'remaining' count did "
            "not exclude ops-channel -- body: %r" % captured["b"])

    def test_full_authority_remaining_matches_the_obligation_set_not_core_alone(self):
        # #382: #367's own adversarial review (F4) found the card's
        # `remaining` still uses the NARROWER core-only derivation while the
        # footer's `I N` (and the `/goal` stop-proof, `core-quals --count`)
        # already widened to the OBLIGATION set (`_obligation_quals()` --
        # core partition UNION needs-gatekeeper/ready-for-review, regardless
        # of which stream owns the ticket). #10 is a plain core ticket; #20
        # is a STREAM-OWNED ticket that ALSO carries needs-gatekeeper --
        # outside the core partition, but part of the obligation set. The
        # pre-#382 code queried ONLY the core partition (a single `-q
        # length` count) and would report "1", missing #20 entirely; the
        # fix must union in the maintainer-action-labelled tickets too and
        # report "2" -- the SAME number the footer's I N would show for
        # this repo.
        import unittest.mock as mk

        core_excl = airuleset._core_search_excl()

        def gh(*a, **k):
            # Match "issue view" by ARGV POSITION, never by substring on
            # the joined command text -- "label:ready-for-review" itself
            # contains the literal substring "view" ("re-VIEW"), so a bare
            # `"view" in j` check would misclassify that qual's own query
            # as the title/labels lookup and return the wrong shape.
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            j = " ".join(str(x) for x in a)
            if "length" in j:
                # pre-#382 shape: a single core-only "-q length" count --
                # never fired by the fixed code, since it never asks for
                # length any more, but kept here so this fixture would
                # correctly reproduce the OLD (narrower) behaviour too.
                return "1"
            if "label:needs-gatekeeper" in j:
                return '[{"number":20}]'
            if "label:ready-for-review" in j:
                return "[]"
            if core_excl in j:
                return '[{"number":10}]'
            return "[]"

        captured = {}
        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: (
                                  captured.setdefault("b", body),
                                  "sent")[1]):
                    airuleset.cmd_notify(self._args())
        import re
        m = re.search(r"ostáva (\d+)(?:\s+(\S+))?", captured["b"])
        self.assertIsNotNone(m, captured["b"])
        self.assertEqual(
            m.group(1), "2",
            "full-authority 'remaining' still counts only the core "
            "partition (missed the needs-gatekeeper-labelled #20 outside "
            "it) -- body: %r" % captured["b"])
        self.assertIsNone(
            m.group(2),
            "'remaining' now counts the SAME obligation set the footer's "
            "unlabeled I N shows -- a 'core' scope_label next to it would "
            "misrepresent the count as narrower than it actually is -- "
            "body: %r" % captured["b"])

    def test_full_authority_remaining_targets_the_explicit_repo_not_cwd(self):
        # #382 adversarial-review MAJOR: `_union_open_issues(quals, base,
        # repo=repo)` threads `--repo` through as `-R <repo>` on EVERY
        # per-qual query -- dropping that kwarg silently falls back to gh's
        # own cwd-resolved repo, which is WRONG for the run-card: it fires
        # from an arbitrary worker cwd (a worktree, a subdev checkout, a
        # cross-project process-subdev fire) that need not match the
        # `--repo` it was explicitly given. The two sibling tests above
        # only ever assert on the RETURNED counts, so a mutant dropping
        # `repo=repo` from the call is invisible to them (the fake `gh`
        # ignores argv entirely) -- this test asserts on the argv ITSELF:
        # `-R <repo>` must be present on every one of the three per-qual
        # union queries the full-authority branch issues.
        import unittest.mock as mk

        seen_argvs = []

        def gh(*a, **k):
            if len(a) > 1 and a[0] == "issue" and a[1] == "view":
                return "T"
            seen_argvs.append(a)
            return "[]"

        with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="full"):
                with mk.patch("notify.send",
                              side_effect=lambda body, **k: "sent"):
                    airuleset.cmd_notify(self._args())

        union_calls = [a for a in seen_argvs
                      if len(a) > 1 and a[0] == "issue" and a[1] == "list"]
        self.assertEqual(
            len(union_calls), 3,
            "expected exactly 3 per-qual union queries (core, "
            "needs-gatekeeper, ready-for-review) -- got %r" % (union_calls,))
        for a in union_calls:
            self.assertIn(
                "-R", a,
                "a full-authority union query did not target an explicit "
                "repo via -R -- it would silently fall back to gh's own "
                "cwd-resolved repo instead of the --repo the card was "
                "given -- argv: %r" % (a,))
            r_idx = a.index("-R")
            self.assertEqual(
                a[r_idx + 1], "kvaskodev/odoo-erp",
                "the union query's -R value did not match the card's own "
                "--repo -- argv: %r" % (a,))


class TestRunCardHeartbeatSurvivesStreamCards(TestCase):
    """#181 I6 (round 2 regression): _write_autopilot_progress is the ONLY
    writer of `ts`, which keeps statusbar's 6h AUTOPILOT_RUN_WINDOW_S run
    window alive. #164's fix skipped calling it ENTIRELY for a stream
    ticket's card on a full-authority box — that also skipped the
    heartbeat, so a run carding only stream tickets never activates
    'Issues D/T' at all. Also locks M11: a failed issue lookup must
    default to NOT bumping `done`, never the pre-#164 wrong direction."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                    repo_name=False, newest_card=False,
                    backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False, channel_id=False,
                    owner=False, mirror_owners=False, question_ping_off=False, body=None, run=None,
                    repo="o/r", issue=5, pr=None,
                    achieved="Fix nasadený a overený", result=None, goal="cieľ", version=None,
                    merge_sha=None, url=None, review="ok", handoff=False,
                    dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def test_stream_tickets_card_still_writes_the_heartbeat(self):
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return '{"title": "t", "labels": [{"name": "stream:montalu"}]}'
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), False)

    def test_core_tickets_card_still_bumps_done(self):
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return '{"title": "t", "labels": []}'
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), True)

    def test_a_failed_issue_lookup_defaults_to_not_bumping_done(self):
        # M11: a parse/lookup failure used to leave is_core_ticket=True
        # (view.get("labels") is None -> _ticket_is_stream_labeled(None) is
        # False -> `not False` is True), silently restoring the PRE-#164
        # wrong behaviour. It must default the SAFE direction: do not bump
        # `done` when we could not tell.
        import unittest.mock as mk

        def gh(*a, **k):
            if a and a[0] == "issue" and "view" in a:
                return ""     # gh error / empty response
            return "3"

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch("notify.send", return_value="sent"):
                    with mk.patch.object(airuleset,
                                         "_write_autopilot_progress") as wap:
                        airuleset.cmd_notify(self._args())
        wap.assert_called_once()
        self.assertEqual(_bump_done_of(wap.call_args), False)


class TestHeartbeatRefreshesALiveRunNeverOpensOne(TestCase):
    """#164 M-1 (round 3): a heartbeat-only write with no prior progress file
    created `{"done": 0}` + a fresh `ts`, and statusbar then rendered
    `Issues 0/40` for a full 6h window — an assertion that a run is active and
    has achieved nothing, replacing the correct `Issues 40 core`. Round 2
    fixed a heartbeat that stopped too early with one that started too
    eagerly."""

    def setUp(self):
        import unittest.mock as mk
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        p = mk.patch("statusbar.progress_dir", return_value=self.dir)
        p.start()
        self.addCleanup(p.stop)

    def _read(self, name="demo"):
        import json as _json
        return _json.loads((self.dir / (name + ".json")).read_text())

    def _seed(self, done, age_s, name="demo"):
        import json as _json
        import time
        (self.dir / (name + ".json")).write_text(_json.dumps(
            {"done": done, "remaining": 7, "ts": int(time.time()) - age_s}))

    def test_a_heartbeat_with_no_live_run_writes_nothing(self):
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertFalse(
            (self.dir / "demo.json").exists(),
            "a stream-only card opened a run window at 0/40 — the footer now "
            "claims an active run that has achieved nothing")

    def test_a_heartbeat_after_the_window_expired_does_not_reopen_it(self):
        import statusbar
        self._seed(done=4, age_s=statusbar.AUTOPILOT_RUN_WINDOW_S + 60)
        before = self._read()
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertEqual(
            self._read(), before,
            "an expired run window was reopened at done=0 by a heartbeat")

    def test_a_heartbeat_inside_the_window_advances_ts_and_leaves_done(self):
        """#164 I-4: the invariant round 2 shipped with NO behavioural test —
        replacing `done = base_done + 1 if bump_done else base_done` with
        `done = base_done + 1` left 104 tests passing."""
        self._seed(done=4, age_s=300)
        before = self._read()
        airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        after = self._read()
        self.assertEqual(after["done"], 4, "bump_done=False incremented done")
        self.assertGreater(after["ts"], before["ts"],
                           "the heartbeat did not advance ts")

    def test_a_core_ticket_card_still_increments_done(self):
        self._seed(done=4, age_s=300)
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        self.assertEqual(self._read()["done"], 5)

    def test_a_first_core_ticket_card_opens_the_run_window(self):
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        self.assertEqual(self._read()["done"], 1)


class TestRunCardResolvesIdentityAgainstTheRepoRoot(TestCase):
    """#181 I-5 residual (item 4): `_notify_run_card` was the fourth call site
    and the only one still resolving identity against the PROCESS cwd — both
    `resolve_authority()` and `_slice_quals()` were called with no cwd, so the
    run-card and the footer could resolve the same box differently whenever a
    session ran from a subdirectory. "One definition, resolved per box" is not
    true until all four agree."""

    def _args(self, **over):
        import unittest.mock as mk
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False, content_dedup_claim=False,
                    repo_name=False, newest_card=False, backfill_digest=False, provision_question_thread=False, provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False,
                    channel_id=False, owner=False, mirror_owners=False, question_ping_off=False,
                    body=None, run=None, repo="kvaskodev/odoo-erp", issue=1408,
                    pr=None, achieved="Fix nasadený a overený", result=None, goal="cieľ",
                    version=None, merge_sha=None, url=None, review="ok",
                    handoff=True, dedup_key=None, dry_run=False)
        base.update(over)
        return mk.Mock(**base)

    def _capture(self):
        import unittest.mock as mk

        seen = {}

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "view" in j:
                return "T"
            if "--search" in [str(x) for x in a]:
                return "[]"
            return "26"

        def auth(cwd=None):
            seen["authority_cwd"] = cwd
            return "fork-no-merge"

        def quals(user, cwd=None):
            seen["slice_cwd"] = cwd
            return ["label:stream:david"]

        with mk.patch.object(airuleset, "_repo_root", return_value="/repo/root"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with mk.patch.object(airuleset, "resolve_authority",
                                     side_effect=auth):
                    with mk.patch.object(airuleset, "_slice_quals",
                                         side_effect=quals):
                        with mk.patch("notify.send", return_value="sent"):
                            airuleset.cmd_notify(self._args())
        return seen

    def test_the_card_resolves_the_slice_against_the_repo_root(self):
        seen = self._capture()
        self.assertEqual(
            seen.get("slice_cwd"), "/repo/root",
            "the run-card still resolves 'my slice' against the process cwd "
            "while the footer resolves it against the repo root")

    def test_the_card_resolves_authority_against_the_repo_root_too(self):
        seen = self._capture()
        self.assertEqual(
            seen.get("authority_cwd"), "/repo/root",
            "a project CLAUDE.md authority marker is invisible to the card "
            "whenever the session cwd is a subdirectory")


class TestHeartbeatOnAReviewOnlyRun(TestCase):
    """#164 M-1 residual (item 5). The GUARD is right; its stated reason was
    false. The docstring claimed the run a heartbeat exists to keep alive
    "always has an in-window file already" — which does not hold for a
    gatekeeper run whose cards are ALL sub-dev stream tickets, a shape the
    obligation-set change makes more common.

    Opening the window there is NOT the fix and this round does not do it:
    `done` and `remaining` are both core-scoped by deliberate design (#164), so
    a review-only run would open at `0/N` and hold it for a full 6h window —
    M-1 verbatim. Making D/T meaningful for such a run means scoping
    `remaining` to the OBLIGATION set while the idle render stays core-scoped,
    i.e. one label over two populations depending on run state, which is #164's
    own title. So the behaviour stays and the false sentence goes."""

    def setUp(self):
        import unittest.mock as mk
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        p = mk.patch("statusbar.progress_dir", return_value=self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_a_whole_review_only_run_still_opens_no_window(self):
        for _ in range(5):
            airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        self.assertFalse(
            (self.dir / "demo.json").exists(),
            "a review-only run opened a progress window at 0/40 — the footer "
            "would assert an active run that has achieved nothing")

    def test_the_first_core_card_of_that_run_opens_it_immediately(self):
        for _ in range(5):
            airuleset._write_autopilot_progress("demo", 40, bump_done=False)
        airuleset._write_autopilot_progress("demo", 40, bump_done=True)
        import json as _json
        self.assertEqual(
            _json.loads((self.dir / "demo.json").read_text())["done"], 1)

    def test_the_docstring_no_longer_claims_the_file_is_always_there(self):
        # Whitespace-normalised: the docstring is hard-wrapped, so a raw
        # assertNotIn would pass vacuously on the newline and lock nothing.
        doc = " ".join((airuleset._write_autopilot_progress.__doc__ or "").split())
        self.assertNotIn(
            "always has an in-window file already", doc,
            "the single source of truth for this invariant states a reason "
            "that is false for a review-only gatekeeper run")

    def test_the_docstring_states_the_real_reason_and_the_consequence(self):
        doc = " ".join((airuleset._write_autopilot_progress.__doc__ or "").split())
        self.assertIn("review-only", doc)
        self.assertIn("no evidence of CORE progress", doc)


if __name__ == "__main__":
    main()
