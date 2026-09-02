"""authority_core_quals tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
from unittest import TestCase, main
from unittest import mock as m
from authority_testlib import (  # noqa: E402
    airuleset,
    cli_quals_cmd,
    _fake_gh_by_search,
    _renamed_repo_gh,
    _drive,
    _labelled_rows_gh,
    _fake_gh_search_filtered,
)


class TestCoreQualsCountsTheObligationSet(TestCase):
    """#181 round 3, CRITICAL: `_core_search_excl()` is the FOOTER's *display*
    partition ("which population am I showing"); round 2's I4 fix reused it as
    the /goal stop-proof's *obligation* partition ("what must I finish before
    I may stop"). Those are not the same set.

    Live on zbynekdrlik/odoo-erp 2026-07-30: 83 open non-skip, 40 in the core
    partition, 5 open `needs-gatekeeper` of which #2396 and #2377 carry
    `stream:montalu` and are therefore INVISIBLE to a core-only count. The
    gatekeeper closes its 40, the proof prints 0, the loop stops, and those
    tickets stay blocked on the very box that stopped. That is #181 verbatim
    at a new address.

    #307 (2026-08-07) correction: `prio:bounce` is NOT one of
    MAINTAINER_ACTION_LABELS any more — it means the gatekeeper returned the
    ticket to the SUB-DEV, who acts next, not this box. A bare open
    `prio:bounce` (no `needs-gatekeeper`/`ready-for-review` alongside it) is
    the sub-dev's own work and must NOT be counted; a ticket carrying BOTH
    still counts via the `ready-for-review` qual (the hand-off is the live
    signal)."""

    POPULATIONS = {
        "-label:stream:": {1, 2},                 # the core partition
        "label:needs-gatekeeper": {2396, 2377},   # stream-labelled, only I can act
        "label:prio:bounce": {5},                 # #307: sub-dev's ball, never queried
        "label:ready-for-review": {8},            # a hand-off awaiting my review
    }

    def _run(self, populations=None, **flags):
        import contextlib
        import io
        import unittest.mock as mk

        gh, searches = _fake_gh_by_search(
            self.POPULATIONS if populations is None else populations)
        buf = io.StringIO()
        args = dict(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None)
        args.update(flags)
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(mk.Mock(**args))
        return buf.getvalue(), searches

    def test_a_stream_ticket_only_this_box_can_action_is_counted(self):
        out, _ = self._run()
        # {1, 2} ∪ {2396, 2377} ∪ {8} — five tickets; #5 (bare prio:bounce)
        # is deliberately NOT one of them (#307).
        self.assertEqual(out.strip(), "5")

    def test_the_listing_names_the_tickets_only_this_box_can_unblock(self):
        out, _ = self._run(count=False, list=True)
        self.assertIn("2396", out)
        self.assertIn("2377", out)
        self.assertIn("8\t", out)

    def test_a_bare_bounce_ticket_is_not_this_boxs_obligation(self):
        """#307: `prio:bounce` alone means the SUB-DEV acts next, not the
        gatekeeper — a bare bounce ticket (no `needs-gatekeeper`/
        `ready-for-review` alongside it) must not appear in the obligation
        listing OR count."""
        out, _ = self._run(count=False, list=True)
        self.assertNotIn("5\t", out)
        count_out, _ = self._run()
        self.assertNotEqual(count_out.strip(), "6")   # would be 6 if #5 leaked in

    def test_a_ticket_carrying_both_bounce_and_handoff_still_counts(self):
        """A ticket carrying BOTH `prio:bounce` AND `ready-for-review` still
        counts — the hand-off is the live signal (#307)."""
        out, _ = self._run({
            "-label:stream:": set(),
            "label:needs-gatekeeper": set(),
            "label:prio:bounce": {6},
            "label:ready-for-review": {6},
        })
        self.assertEqual(out.strip(), "1")

    def test_prio_bounce_is_never_queried_by_the_plain_obligation_set(self):
        """#307: unlike `needs-gatekeeper`/`ready-for-review`, `prio:bounce`
        is no longer one of MAINTAINER_ACTION_LABELS — the plain (no
        `--extra`) proof must never query it at all."""
        _, searches = self._run()
        joined = " | ".join(searches)
        self.assertNotIn("label:prio:bounce", joined)

    def test_a_stream_ticket_the_subdev_is_working_still_does_not_block_me(self):
        """NOT a revert to the whole-repo count — that is the never-stops
        failure the original ticket explicitly rejected."""
        out, searches = self._run({
            "-label:stream:": {1},
            "label:needs-gatekeeper": set(),
            "label:prio:bounce": set(),
            "label:ready-for-review": set(),
        })
        self.assertEqual(out.strip(), "1")
        # #362: the "bare" shape is now AUTOPILOT_SKIP_EXCL (autopilot-skip
        # + ops-channel), never the plain literal alone — checking against
        # the shared constant keeps this a real (non-vacuous) assertion.
        bare = [s for s in searches
                if s.strip() == airuleset.AUTOPILOT_SKIP_EXCL]
        self.assertEqual(
            bare, [],
            "a bare whole-repo query is back — that is the never-stops "
            "failure #181 rejected, not the obligation set")

    def test_every_maintainer_action_label_is_actually_queried(self):
        _, searches = self._run()
        joined = " | ".join(searches)
        for label in ("needs-gatekeeper", "ready-for-review"):
            self.assertIn("label:" + label, joined)

    def test_extra_filter_also_runs_a_bare_query_for_the_bounce_seed(self):
        """#307: with `prio:bounce` dropped from MAINTAINER_ACTION_LABELS, a
        per-qual AND (base + each obligation qual) can never find a ticket
        that is ONLY bounce-labelled — the common real case the bounce-lane
        SEED (Step 3.1, `core-quals --list --extra "label:prio:bounce"`)
        depends on. `--extra` must ALSO issue the BARE base query (no extra
        AND) so that coverage survives the correction."""
        _, searches = self._run(count=False, list=True, waiting=False,
                                extra="label:prio:bounce")
        self.assertIn(
            airuleset.AUTOPILOT_SKIP_EXCL + " label:prio:bounce", searches)

    def test_whitespace_only_extra_never_leaks_the_bare_whole_repo_query(self):
        """Adversarial review of #307: `extra=" "` is truthy, so an
        unstripped check would still take the bare-extra branch and union in
        a plain AUTOPILOT_SKIP_EXCL query -- the exact whole-repo
        never-stops shape #181 rejected."""
        _, searches = self._run(count=False, list=True, waiting=False, extra="   ")
        bare = [s for s in searches
                if s.strip() == airuleset.AUTOPILOT_SKIP_EXCL]
        self.assertEqual(
            bare, [],
            "a whitespace-only --extra leaked the bare whole-repo query")

    def test_reduced_authority_box_refuses_instead_of_answering(self):
        """I-3: C1's fix applied in the mirror direction. `slice-quals`
        correctly refuses on a full box; `core-quals` answered on ANY box, so
        run on montalu it printed a number that is neither that box's slice
        nor a valid stop-proof for it."""
        import contextlib
        import io
        import unittest.mock as mk

        gh, _ = _fake_gh_by_search(self.POPULATIONS)
        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="branch-merge"):
            with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        airuleset.cmd_core_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_failed_gh_query_never_prints_a_number(self):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", return_value="not json"):
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        airuleset.cmd_core_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")


class TestObligationVsDisplayPartition(TestCase):
    """The two partitions answer DIFFERENT questions on purpose (the same
    resolution round 2 reached for I3). Locked so a future round cannot
    "fix" the divergence into numeric equality and re-open #181, nor fold
    the display partition back into the obligation one and re-open #164."""

    def test_the_footers_exclusion_still_hides_every_reduced_stream(self):
        excl = airuleset._core_search_excl()
        for user, profile in airuleset.AUTHORITY_BY_USER.items():
            if profile != "full":
                self.assertIn("-label:stream:" + user, excl)

    def test_a_full_profile_entry_is_not_treated_as_a_sub_dev_stream(self):
        """M-5: `_core_search_excl()` keyed on AUTHORITY_BY_USER's KEYS, so a
        future `full` entry would silently remove a whole population from
        every full-authority count."""
        import unittest.mock as mk

        with mk.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"david": "fork-no-merge", "boss": "full"}):
            excl = airuleset._core_search_excl()
        self.assertIn("-label:stream:david", excl)
        self.assertNotIn("stream:boss", excl)

    def test_core_quals_declares_the_obligation_set_it_counts(self):
        """Driven through the command, so it fails when the COUNT is core-only
        rather than merely when a constant is missing. The two live tickets
        that proved the CRITICAL — odoo-erp #2396/#2377 — carry
        `stream:montalu` AND `needs-gatekeeper`, actionable ONLY by the box
        whose loop would otherwise stop.

        #307: `prio:bounce` is deliberately NOT declared any more — a bare
        bounce ticket is the sub-dev's own work, not this box's obligation."""
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_gh_out", return_value="[]"):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        mk.Mock(count=False, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        printed = buf.getvalue()
        self.assertIn(airuleset._core_search_excl(), printed)
        for label in ("needs-gatekeeper", "ready-for-review"):
            self.assertIn("label:" + label, printed)
        self.assertNotIn("label:prio:bounce", printed)


class TestSearchIndexCrossCheckAssertsNonEmpty(TestCase):
    """#181 I-1: round 2's `involves:@me` cross-check only required the
    response to PARSE — and `[]` parses. "Search returns nothing everywhere"
    IS `[]`, i.e. the exact state it claimed to detect was the state it
    accepted (reviewer executed it: every query [] -> rc 0, stdout `0`)."""

    def _run(self, gh):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        exc = None
        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="branch-merge"):
            with mk.patch.object(airuleset, "_gh_login",
                                 return_value="zbynekdrlik"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="montalu1"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(buf):
                            try:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                            except SystemExit as e:
                                exc = e
        return buf.getvalue(), exc

    def test_an_empty_search_index_refuses_instead_of_reporting_zero(self):
        """Every SEARCH query empty while the repo demonstrably has open
        issues: the search index is not answering, so 0 is not evidence."""
        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "--search" in args:
                return "[]"          # search sees nothing, anywhere
            return '[{"number": 4242}]'   # the REST listing path does

        out, exc = self._run(gh)
        self.assertIsNotNone(exc, "an unanswering search index printed a count")
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(out.strip(), "")

    def test_a_healthy_search_index_still_trusts_a_real_zero(self):
        def gh(*a, **k):
            args = [str(x) for x in a]
            j = " ".join(args)
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return '[{"number": 4242}]'   # search demonstrably answers
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNone(exc, "a validated zero must still be reportable")
        self.assertEqual(out.strip(), "0")

    def test_a_repo_with_no_open_issues_at_all_still_trusts_zero(self):
        """The sort-only probe is legitimately empty here, and the REST path
        confirms it — an empty slice on an empty repo is trivially correct."""
        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")

    def test_a_failing_cross_check_probe_refuses(self):
        def gh(*a, **k):
            args = [str(x) for x in a]
            j = " ".join(args)
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return ""            # gh error on the probe itself
            return "[]"

        out, exc = self._run(gh)
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(out.strip(), "")


class TestEveryStopProofRefusesAnUnansweringSearchIndex(TestCase):
    """#181 round 4, CRITICAL. `_search_index_healthy()` had exactly ONE call
    site: inside `cmd_slice_quals`, nested behind
    `len(quals) == 1 and quals[0].startswith("label:")` — the SHARED-account
    shape. So it was not a guard on "an empty result out of the search index",
    it was an extra validation for one caller's zero, and two live paths walked
    straight past it.

    Both reproduced on dev1, 2026-07-30, against the shipped code:

      gh issue list --state open -L 1000 --json number --jq length        110
      gh issue list --state open --search "sort:created-desc" -L 1000       0
      python3 airuleset.py core-quals --count                              0  (rc 0)

      _slice_quals("david") -> ['assignee:@me','author:@me','label:stream:david']
      cmd_slice_quals(count=True), every search [] -> stdout '0', no SystemExit

    The rename is only the cheapest trigger; any state where search answers
    empty while REST does not reaches the same line."""

    def _assert_refused(self, out, err, exc, why):
        self.assertIsNotNone(exc, why)
        self.assertNotEqual(exc.code, 0, why)
        self.assertEqual(out.strip(), "", "a refusal must print NOTHING to stdout")
        self.assertNotEqual(err.strip(), "", "a refusal must say why on stderr")

    def test_core_quals_count_refuses_when_search_is_dead_but_rest_is_not(self):
        out, err, exc = _drive(airuleset.cmd_core_quals, _renamed_repo_gh())
        self._assert_refused(
            out, err, exc,
            "core-quals printed a stop-proof `0` while the repository listing "
            "path shows open issues — the gatekeeper pastes that 0, writes "
            "BACKLOG EMPTY and stops with the whole backlog outstanding")

    def test_core_quals_list_refuses_too(self):
        """`--list` is the mandated backlog SELECTION source, so an
        unanswering index empties the worker's work queue just as silently as
        it zeroes the count."""
        out, err, exc = _drive(airuleset.cmd_core_quals, _renamed_repo_gh(),
                               count=False, list=True)
        self._assert_refused(out, err, exc,
                             "core-quals --list returned an empty backlog")

    def test_own_account_slice_quals_refuses_when_search_is_dead(self):
        """The hole is open in the command round 3 fixed, too: an own-account
        stream has THREE quals, so `len(quals) == 1` is False and the guard is
        skipped entirely."""
        out, err, exc = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                               authority="fork-no-merge", user="david1",
                               login="kvaskodev")
        self._assert_refused(
            out, err, exc,
            "slice-quals printed a false SLICE EMPTY for an own-account "
            "stream — the guard is nested behind the shared-account shape")

    def test_own_account_slice_quals_list_refuses_too(self):
        out, err, exc = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                               authority="fork-no-merge", user="david1",
                               login="kvaskodev", count=False, list=True)
        self._assert_refused(out, err, exc, "slice-quals --list went empty")

    def test_both_stop_proofs_refuse_with_the_IDENTICAL_contract(self):
        """The refusal contract must not be two parallel copies — that is the
        shape that let it drift for three rounds."""
        core = _drive(airuleset.cmd_core_quals, _renamed_repo_gh())
        slic = _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(),
                      authority="fork-no-merge", user="david1", login="kvaskodev")
        for out, err, exc in (core, slic):
            self.assertIsNotNone(exc)
            self.assertNotEqual(exc.code, 0)
            self.assertEqual(out.strip(), "")
            self.assertNotEqual(err.strip(), "")

    def test_both_commands_route_through_the_SAME_helper(self):
        """The assertion above is satisfied by two parallel copies, which is
        exactly the shape this round set out to remove — so it cannot be the
        only thing standing behind the word "identical" (adversarial review,
        round 4). This drives both real commands and records who asked."""
        import unittest.mock as mk

        callers = []

        def recorder(cmd, quals, cwd=None):
            callers.append(cmd)

        with mk.patch.object(cli_quals_cmd, "_refuse_unless_empty_is_trustworthy",
                             side_effect=recorder):
            _drive(airuleset.cmd_core_quals, _renamed_repo_gh(healthy=True))
            _drive(airuleset.cmd_slice_quals, _renamed_repo_gh(healthy=True),
                   authority="fork-no-merge", user="david1", login="kvaskodev")
        self.assertEqual(
            sorted(callers), ["core-quals", "slice-quals"],
            "one of the two stop-proofs does not go through the shared "
            "refusal helper: %r" % (callers,))

    # ----- controls: a legitimate zero must still be reportable ------------ #

    def _drive_unenrolled(self, gh):
        """Pin the repo identity: these controls reach the hand-off gate, and
        leaving them on the REAL `notify.repo_name_for` made them pass only
        because this repo happens not to be enrolled in the cross-stream flow
        — they would flip the day it was (adversarial review, round 4). A
        control whose result depends on where it is run is not a control."""
        import unittest.mock as mk
        with mk.patch("notify.repo_name_for", return_value="not-enrolled"):
            return _drive(airuleset.cmd_core_quals, gh)

    def test_a_healthy_index_still_lets_core_quals_report_a_real_zero(self):
        out, _, exc = self._drive_unenrolled(_renamed_repo_gh(healthy=True))
        self.assertIsNone(exc, "a validated zero must still be reportable")
        self.assertEqual(out.strip(), "0")

    def test_a_repo_with_no_open_issues_at_all_still_reports_zero(self):
        out, _, exc = self._drive_unenrolled(_renamed_repo_gh(rest="[]"))
        self.assertIsNone(exc)
        self.assertEqual(out.strip(), "0")


class TestTheSelectionSourceCarriesTheOwnershipDiscriminator(TestCase):
    """#181 round 4, HIGH. `core-quals --list` is the mandated backlog
    SELECTION source and emitted no not-mine-to-implement discriminator:
    `_union_open_issues` asked for `number,title,createdAt` — labels were never
    fetched at all — and `_print_issue_rows` printed number/createdAt/title.

    On odoo-erp the obligation set is 55 rows, and the FULL template's own
    bounce-lane instruction seeds every new batch from the OLDEST open
    `prio:bounce` ticket via `core-quals --list --extra "label:prio:bounce"` —
    which is #2150, `stream:david`. Nothing but a prose clause stood between
    that instruction and the gatekeeper writing code on a sub-dev's ticket,
    and prose in exactly this position is what this ticket has been about for
    four rounds.

    #307 (2026-08-07): `prio:bounce` is no longer part of the PLAIN obligation
    set (a bare bounce ticket is the sub-dev's own work) — #2150 now surfaces
    ONLY through the `--extra "label:prio:bounce"` bounce-lane seed path,
    exactly how the FULL template actually invokes it, still marked
    `action-only` there."""

    def test_the_union_queries_actually_fetch_the_labels(self):
        gh, seen = _labelled_rows_gh()
        _drive(airuleset.cmd_core_quals, gh, count=False, list=True)
        json_fields = [a[a.index("--json") + 1] for a in seen
                       if "--json" in a and a and a[0] == "issue"]
        self.assertTrue(json_fields)
        self.assertTrue(
            any("labels" in f for f in json_fields),
            "labels are never fetched, so no row can carry a discriminator: %r"
            % (json_fields,))

    def test_a_bounce_stream_row_is_marked_action_only_via_the_seed(self):
        # The REAL invocation (SKILL.md Step 3.1) always passes `--extra
        # "label:prio:bounce"` for the seed — never a bare `--list`.
        gh, _ = _labelled_rows_gh()
        out, _, exc = _drive(airuleset.cmd_core_quals, gh, count=False,
                             list=True, extra="label:prio:bounce")
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("2150\t")]
        self.assertTrue(row, out)
        self.assertIn(
            "action-only", row[0],
            "the oldest open prio:bounce ticket is stream:david's — the seed "
            "instruction points straight at it and the row says nothing")

    def test_a_bare_bounce_ticket_is_absent_from_the_plain_obligation_list(self):
        """#307: WITHOUT `--extra`, #2150 (bare prio:bounce, no
        needs-gatekeeper/ready-for-review) must NOT appear at all — it is the
        sub-dev's own work, not this box's obligation."""
        gh, _ = _labelled_rows_gh()
        out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                             count=False, list=True)
        self.assertIsNone(exc)
        self.assertNotIn("2150\t", out)

    def test_a_core_row_is_marked_implement(self):
        gh, _ = _labelled_rows_gh()
        out, _, _ = _drive(airuleset.cmd_core_quals, gh, count=False, list=True)
        row = [ln for ln in out.splitlines() if ln.startswith("11\t")]
        self.assertTrue(row, out)
        self.assertIn("implement", row[0])

    def test_a_row_whose_ownership_cannot_be_read_defaults_to_action_only(self):
        """M11's lesson, applied to the new column. `labels` ABSENT from a row
        is undeterminable, not "unlabelled" — and the two failure directions
        are not symmetric: a sub-dev's ticket printed `implement` invites the
        gatekeeper to write code on a foreign stream's ticket (a silent
        authority violation, the exact harm this column exists to prevent),
        while a core ticket printed `action-only` merely stalls visibly. So an
        unreadable row takes the conservative side. A row with `labels: []` is
        a genuinely unlabelled core ticket and must still read `implement`."""
        import json as _json

        def gh(*a, **k):
            args = [str(x) for x in a]
            if "sort:created-desc" in " ".join(args):
                return '[{"number": 999}]'
            if "--search" in args:
                return _json.dumps([
                    {"number": 31, "title": "ownership unreadable",
                     "createdAt": "2026-07-01T00:00:00Z"}])
            return "[]"

        out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                             count=False, list=True)
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("31\t")]
        self.assertTrue(row, out)
        self.assertIn(
            "action-only", row[0],
            "a row whose labels could not be read was advertised as ordinary "
            "work — that is the dangerous direction")

    def test_labels_present_but_malformed_is_also_undeterminable(self):
        """`"labels" in row` is not enough: a `labels` value that is not a list
        of dicts (bare strings, an explicit null) is unreadable ownership, not
        an absence of ownership, and printing `implement` for it is the
        dangerous direction (adversarial review, round 4)."""
        import json as _json

        for value in (["stream:david"], None, "stream:david", [None]):
            with self.subTest(labels=value):
                def gh(*a, _v=value, **k):
                    args = [str(x) for x in a]
                    if "sort:created-desc" in " ".join(args):
                        return '[{"number": 999}]'
                    if "--search" in args:
                        return _json.dumps([
                            {"number": 41, "title": "malformed ownership",
                             "createdAt": "2026-07-01T00:00:00Z",
                             "labels": _v}])
                    return "[]"

                out, _, exc = _drive(airuleset.cmd_core_quals, gh,
                                     count=False, list=True)
                self.assertIsNone(exc)
                row = [ln for ln in out.splitlines() if ln.startswith("41\t")]
                self.assertTrue(row, out)
                self.assertIn("action-only", row[0])

    def test_my_OWN_streams_ticket_is_mine_to_implement(self):
        """A reduced-authority box's own `stream:<me>` tickets must not come
        back marked action-only — the discriminator is relative to THIS box."""
        import json as _json

        def gh(*a, **k):
            args = [str(x) for x in a]
            if args and args[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "--search" in args:
                return _json.dumps([
                    {"number": 7, "title": "mine",
                     "createdAt": "2026-07-01T00:00:00Z",
                     "labels": [{"name": "stream:montalu"}]}])
            return "[]"

        out, _, exc = _drive(airuleset.cmd_slice_quals, gh,
                             authority="branch-merge", user="montalu1",
                             login="zbynekdrlik", count=False, list=True)
        self.assertIsNone(exc)
        row = [ln for ln in out.splitlines() if ln.startswith("7\t")]
        self.assertTrue(row, out)
        self.assertIn("implement", row[0])
        self.assertNotIn("action-only", row[0])

    def test_core_quals_can_scope_the_bounce_seed_like_slice_quals_does(self):
        """The full-authority bounce seed went through a raw `gh issue list`,
        so the single highest-priority selection path had neither the guard nor
        the discriminator."""
        gh, seen = _labelled_rows_gh()
        _drive(airuleset.cmd_core_quals, gh, count=False, list=True, waiting=False,
               extra="label:prio:bounce")
        searches = [a[a.index("--search") + 1] for a in seen
                    if "--search" in a and a and a[0] == "issue"]
        searches = [s for s in searches if "sort:created-desc" not in s]
        self.assertTrue(searches)
        for s in searches:
            self.assertIn("label:prio:bounce", s)


class TestQualsExcludePermanentOpsChannelTickets(TestCase):
    """#362: a stream self-declares a ticket PERMANENT via the `ops-channel`
    label (odoo-erp #1861 -- "[TRVALY OPS KANAL -- NEZATVARAT] erp-test-*
    teardown/recreate/refresh", and #3037 -- a snapshot-retention alert log)
    so it deliberately never auto-closes. Before this fix `core-quals`/
    `slice-quals` treated it as ordinary workable backlog -- the /goal
    stop-proof's `--count 0` was UNREACHABLE while it stayed open, and the
    /autopilot loop dispatched a full worker onto it that found "nothing to
    do -- permanent log ticket" (~220k wasted subagent tokens)."""

    def test_core_quals_count_excludes_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        items = [
            {"number": 1, "labels": set()},              # ordinary core work
            {"number": 2, "labels": {"ops-channel"}},     # permanent channel
        ]
        gh, searches = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority", return_value="full"):
            with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        m.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a permanent ops-channel ticket is still counted as workable "
            "backlog -- searches issued: %r" % searches)

    def test_core_quals_list_omits_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        items = [
            {"number": 1, "labels": set()},
            {"number": 2, "labels": {"ops-channel"}},
        ]
        gh, _ = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority", return_value="full"):
            with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                with contextlib.redirect_stdout(buf):
                    airuleset.cmd_core_quals(
                        m.Mock(count=False, list=True, waiting=False, ops_wait=False, audit=False, extra=None))
        out = buf.getvalue()
        self.assertIn("1\t", out)
        self.assertNotIn("2\t", out)

    def test_slice_quals_count_excludes_a_permanent_ops_channel_ticket(self):
        import contextlib
        import io

        # The SHARED-account, single-label slice shape (montalu/marek/simap):
        # _slice_quals() mocked directly so this test is about cmd_slice_quals'
        # own query-building, not about resolving gh identity.
        items = [
            {"number": 1, "labels": {"stream:montalu"}},
            {"number": 2, "labels": {"stream:montalu", "ops-channel"}},
        ]
        gh, searches = _fake_gh_search_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "resolve_authority",
                            return_value="branch-merge"):
            with m.patch.object(airuleset, "_slice_quals",
                                return_value=["label:stream:montalu"]):
                with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            m.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a permanent ops-channel ticket is still in this stream's own "
            "slice -- searches issued: %r" % searches)


if __name__ == "__main__":
    main()
