"""authority_slice_quals tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
import os
from pathlib import Path
from unittest import TestCase, main
from authority_testlib import (  # noqa: E402
    airuleset,
)


class TestSliceQualsIsTheOneSliceDefinition(TestCase):
    """#181: montalu@subdev's armed /goal declared the backlog EMPTY (its own
    stop-proof printed 0) while the footer on the SAME box read 'Issues 5' —
    a real FALSE STOP, not a labeling mismatch. Root cause: the footer's
    `_slice_quals()` already branches on the gh LOGIN (shared-account box ->
    the stream LABEL alone, since `@me` there resolves to the maintainer and
    matches nothing assigned) but the /goal proof template hardcoded
    `--assignee @me` with no such branch. `slice-quals` is the fix: ONE
    definition, consumed by the footer AND the /goal proof, so they cannot
    drift apart again."""

    def test_shared_account_box_gets_a_nonzero_count_when_work_is_open(self):
        # Reproduces the live montalu incident exactly: gh login == the
        # maintainer account, an open ticket carries label:stream:montalu and
        # is NOT assigned to anyone. --assignee @me finds nothing (the old,
        # broken proof); slice-quals must still report it.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "assignee:@me" in j:
                return "[]"   # the OLD proof's key — must NEVER be relied on
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z"}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_cmd_slice_quals_actually_calls_the_shared_slice_quals_function(self):
        # I7 (round 2 review): the old test here only asserted
        # _slice_quals()'s OWN output, which is byte-identical before and
        # after the #181 round-1 fix (only cmd_slice_quals/its callers were
        # added around it) -- so it passed UNCHANGED pre-fix and proved
        # nothing about cmd_slice_quals itself. This one mocks
        # _slice_quals() to return a DISTINCTIVE sentinel qual and proves
        # cmd_slice_quals's own gh queries are genuinely built from THAT
        # value -- it fails against a reimplementation that re-derives its
        # own query instead of calling the shared function (the
        # single-source-of-truth claim this mechanism exists to guarantee).
        import contextlib
        import io
        import unittest.mock as mk

        calls = []

        def fake_gh(*a, **k):
            calls.append(a)
            # Non-empty so the C2 shared-account-zero validation path (a
            # DIFFERENT, unrelated check) never engages here — this test is
            # only about WHICH qual the query was built from.
            return '[{"number": 1, "title": "t", "createdAt": "2026-07-01T00:00:00Z"}]'

        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="fork-no-merge"):
            with mk.patch.object(airuleset, "_slice_quals",
                                 return_value=["label:__sentinel_qual__"]) as sq:
                with mk.patch.object(airuleset, "_gh_out", side_effect=fake_gh):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        sq.assert_called()
        self.assertTrue(
            any("label:__sentinel_qual__" in " ".join(str(x) for x in c)
                for c in calls),
            "cmd_slice_quals never queried gh using _slice_quals()'s own "
            "returned qual -- it is not actually the single source of truth")

    def test_own_account_box_still_unions_all_three_quals(self):
        # david/kvaskodev (own-account) must keep the full 3-way union — a
        # naive "just use the label" fix would under-count them.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "assignee:@me" in j:
                return ('[{"number": 1, "title": "a", '
                        '"createdAt": "2026-07-01T00:00:00Z"}]')
            if "author:@me" in j:
                return ('[{"number": 2, "title": "b", '
                        '"createdAt": "2026-07-02T00:00:00Z"}]')
            if "label:stream:david" in j:
                return ('[{"number": 2, "title": "b", '
                        '"createdAt": "2026-07-02T00:00:00Z"}]')   # dup of #2
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "2")   # {1, 2} unioned, not 3

    def test_a_failed_gh_query_never_prints_zero(self):
        # The false-stop's exact shape: a wrong/failed query must NEVER
        # collapse to a printed 0 — that IS the bug this exists to prevent.
        import contextlib
        import io
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", return_value="not json"):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                    self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())


class TestSliceQualsIncludesOwnBounceTickets(TestCase):
    """#307's symmetric check: `core-quals` DROPS `prio:bounce` from the
    full-authority obligation set (it is the SUB-DEV's own work, not this
    box's), but `slice-quals` on the sub-dev's OWN box must still INCLUDE its
    own `prio:bounce` tickets — that is the stream's priority lane (Step 3.1
    seeds from it), and the two sides must stay complementary rather than
    both claiming or both dropping the same ticket.

    No code change was needed for this half: a bounce ticket always ALSO
    carries `stream:<user>` per the cross-stream protocol, and
    `_slice_quals()` never excluded that label — this locks the invariant so
    a future change to `_slice_quals()` cannot silently drop it."""

    def test_own_account_stream_slice_still_finds_its_own_bounce_ticket(self):
        # Own-account stream (david/kvaskodev): assignee ∪ author ∪
        # stream:<user> — a bounce ticket carrying ONLY stream:david (not
        # assigned/authored) must still be found via the stream-label qual.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:david" in j:
                return ('[{"number": 42, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}, '
                        '{"name": "stream:david"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_shared_account_stream_slice_still_finds_its_own_bounce_ticket(self):
        # Shared-account stream (montalu/marek/simap): the slice is
        # `label:stream:<user>` ALONE — a bounce ticket must still surface
        # through that one qual.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 99, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}, '
                        '{"name": "stream:montalu"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")


class TestSliceQualsRefusesRatherThanGuessing(TestCase):
    """Round-2 adversarial review of 49cd3d4..2612400: the #181 round-1 fix
    RELOCATED the false-empty-0 failure instead of removing it (C1, C2 —
    both live-confirmed / reproduced). Also locks I3's finding: the
    footer's gk-partitioned display and slice-quals's raw open count
    deliberately answer DIFFERENT questions, and that difference is safe,
    not a bug to "fix" into numeric equality."""

    def test_full_authority_box_refuses_instead_of_printing_zero(self):
        # C1: live-confirmed on dev1 -- cmd_slice_quals never consulted
        # resolve_authority()/AUTHORITY_BY_USER, so on a full-authority box
        # (no stream at all) it silently built label:stream:<linux-user>,
        # which matches nothing, and printed a clean 0 with 29 real open
        # tickets sitting untouched. It must refuse, never print a count.
        import contextlib
        import io
        import unittest.mock as mk

        with mk.patch.object(airuleset, "resolve_authority", return_value="full"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="newlevel"):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="zbynekdrlik"):
                    with mk.patch.object(airuleset, "_gh_out",
                                         return_value="[]"):
                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            with self.assertRaises(SystemExit) as cm:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
                        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_refuses_when_the_label_does_not_exist(self):
        # C2: a forgotten/never-created stream:<user> label makes gh search
        # return [] with exit 0 for a query that can never match anything —
        # a false-empty stop under a different key than round 1's @me.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            return "[]"   # the label itself was never created; every query empty

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_refuses_when_the_cross_check_query_fails(self):
        # C2: the label genuinely exists but nothing carries it — a
        # cross-check must ALSO prove gh's SEARCH path works for this
        # identity/repo before a 0 is trusted. #181 I-1 (round 3) replaced
        # round 2's `involves:@me` probe, which accepted `[]` — the very
        # state it claimed to detect — with a SORT-ONLY search that cannot
        # legitimately be empty; this test now drives that probe failing.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a and a[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return ""   # gh error on the cross-check probe
            return "[]"     # the slice query itself: empty

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        with self.assertRaises(SystemExit) as cm:
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertNotIn("0", buf.getvalue())

    def test_shared_account_zero_is_trusted_once_validated(self):
        # The genuinely legitimate case must still work: the label exists,
        # nothing carries it, AND gh search is demonstrably healthy for
        # this identity/repo (the cross-check succeeds) — 0 is real here.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if a and a[0] == "label":
                return '[{"name": "stream:montalu"}]'
            if "sort:created-desc" in j:
                return '[{"number": 999}]'   # search demonstrably answers
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user", return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_a_handed_off_ticket_no_longer_counts_once_handed_off(self):
        # #391 REVERSES this test's own prior premise for the REDUCED-
        # authority path only. I3 (round 2) had reasoned the footer's
        # len(mine)-gk was merely a DISPLAY partition and that slice-quals's
        # raw count must keep counting a handed-off-but-still-OPEN ticket --
        # but the SHIPPED #367 footer never actually computed len(mine)-gk
        # (it computed the FULL len(mine), gk as a subset badge), so that
        # reasoning rested on a formula the footer didn't implement. #391's
        # own explicit ask: a sub-dev's responsibility for a ticket is
        # fulfilled once it is HANDED OFF, not once the gatekeeper has also
        # closed it -- review-watch (the /goal loop staying alive while a
        # hand-off is outstanding) is the STOP-CONDITION's own job, checked
        # separately from this count via the loop's own review-watch clause,
        # never by keeping a handed-off ticket in the raw count. This is
        # scoped ONLY to the reduced-authority `slice-quals` path -- the
        # analogous FULL-authority `core-quals`/`_obligation_quals()` side is
        # UNCHANGED: a handed-off ticket correctly stays in the
        # full-authority box's obligation set until the gatekeeper actually
        # closes it (that box is the one still on the hook for it).
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:david" in j:
                return ('[{"number": 7, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            with mk.patch.object(airuleset, "_current_user", return_value="david1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")


class TestSliceQualsExcludesHandedOffLikeTheFooter(TestCase):
    """#391: `slice-quals --count`/`--list` (the reduced-authority `/goal`
    stop-proof) excludes handed-off work IDENTICALLY to `cmd_tickets_status`'s
    own footer -- the #367 consistency guard, applied to the reduced-
    authority reversal (own UNHANDLED work, not the raw slice). Both consume
    the SAME shared derivation, so a future change to one cannot silently
    diverge from the other. Full-authority `core-quals` is untouched --
    covered separately by `TestObligationVsDisplayPartition`/
    `TestCoreQualsCountsTheObligationSet`."""

    def test_count_excludes_a_ready_for_review_ticket(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_count_still_includes_a_genuinely_unhandled_ticket(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "t", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": []}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "1")

    def test_list_omits_a_handed_off_ticket_too(self):
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "handed off", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "needs-gatekeeper"}]},'
                        '{"number": 6, "title": "still mine", '
                        '"createdAt": "2026-07-02T00:00:00Z", '
                        '"labels": []}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=False, list=True, waiting=False, ops_wait=False, audit=False, extra=None))
        out = buf.getvalue()
        self.assertNotIn("5\t", out)
        self.assertIn("6\t", out)

    def test_a_bounce_ticket_stays_counted_even_though_it_once_was_handed_off(self):
        # #391's own explicit safety test, at the slice-quals layer: a
        # `prio:bounce` ticket (also carrying a stale ready-for-review label)
        # must stay in the UNHANDLED count -- never read as done just
        # because it once carried a hand-off label. Mirrors
        # `test_statusbar.py::test_refresh_a_bounce_ticket_stays_in_
        # unhandled_count_alongside_real_handoffs` for the footer.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "bounced", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}, '
                        '{"name": "prio:bounce"}]}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a returned bounce ticket must stay in the stop-proof's own "
            "count, or the loop reads REVIEW as DONE")

    def test_an_invisible_bounce_stays_unhandled_even_with_a_stale_hand_off_comment(self):
        # #391 CRITICAL-1 (fresh-context adversarial review): mirrors
        # test_statusbar.py's own footer-layer pin. A `prio:bounce` ticket
        # whose comment thread carries ONLY a stale pre-bounce
        # READY-FOR-REVIEW comment (no gatekeeper-shaped comment anywhere
        # -- the sanctioned Discord nudge-lane bounce shape,
        # skills/autopilot/SKILL.md: a bare label + a sub-dev-authored ACK)
        # must stay in the stop-proof's own UNHANDLED count -- the comment
        # fallback must not silently overwrite the label-derived bounce
        # override just because the last (and only) comment signal is True.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "bounced no comment", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "ready-for-review"}, '
                        '{"name": "prio:bounce"}]}]')
            if "issues/5/timeline" in j:
                return '[{"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"}]'
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "a bounce-labeled ticket must not be flipped back to handed "
            "by a stale hand-off comment when no gatekeeper comment is "
            "visible anywhere in the thread")

    def test_a_genuine_re_hand_off_visible_in_comments_still_counts_as_handed(self):
        # The positive control for the test above: a bounce comment IS
        # visible in the thread, followed by a genuine later re-hand-off --
        # this must still resolve to handed. Mirrors test_statusbar.py::
        # test_refresh_recovers_a_genuine_re_hand_off_after_a_bounce_finding
        # for the footer, at the slice-quals layer.
        import contextlib
        import io
        import unittest.mock as mk

        def gh(*a, **k):
            j = " ".join(str(x) for x in a)
            if "label:stream:montalu" in j:
                return ('[{"number": 5, "title": "re-handed", '
                        '"createdAt": "2026-07-01T00:00:00Z", '
                        '"labels": [{"name": "prio:bounce"}]}]')
            if "issues/5/timeline" in j:
                return ('[{"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},'
                        '{"event": "commented", "body": "**GATEKEEPER FINDING:** needs another fix, '
                        'bouncing back."},'
                        '{"event": "commented", "body": "READY-FOR-REVIEW: addressed the finding, '
                        're-pushed."}]')
            return "[]"

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertEqual(buf.getvalue().strip(), "0")

    def test_slice_quals_actually_calls_the_shared_handed_derivation(self):
        # MAJOR-2 (fresh-context adversarial review of #391): no test
        # anywhere in this repo referenced `_slice_mine_and_handed` by name
        # before this one -- a re-inlined, drifted derivation inside
        # cmd_slice_quals (e.g. silently dropping the comment-fallback/
        # recovery enrichment) would pass every pre-existing slice-quals
        # test above, since all of them are label-only fixtures. Mirrors
        # #181 I7's own sentinel-mock shape (`test_cmd_slice_quals_
        # actually_calls_the_shared_slice_quals_function`).
        import contextlib
        import io
        import unittest.mock as mk

        sentinel_rows = {
            5: {"number": 5, "title": "handed",
                "createdAt": "2026-07-01T00:00:00Z", "labels": []},
            6: {"number": 6, "title": "unhandled",
                "createdAt": "2026-07-02T00:00:00Z", "labels": []},
        }
        sentinel_handed = {5: True, 6: False}

        buf = io.StringIO()
        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            with mk.patch.object(airuleset, "_current_user",
                                 return_value="montalu1"):
                with mk.patch.object(
                        airuleset, "_slice_mine_and_handed",
                        return_value=(sentinel_rows, sentinel_handed,
                                      False)) as sm:
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_slice_quals(
                            mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        sm.assert_called()
        self.assertEqual(
            buf.getvalue().strip(), "1",
            "cmd_slice_quals did not consume _slice_mine_and_handed's own "
            "returned handed map -- it may be re-deriving handed status "
            "itself instead of calling the shared derivation")


class TestSliceQualsRefusesAnUnresolvableIdentity(TestCase):
    """#181 I-2, live-reproduced on david@subdev: `_gh_login`'s bare
    `except: return ""` made "gh api user failed" indistinguishable from
    "not the maintainer", so a broken-gh box silently got the own-account
    3-qual union — C2's shared-account validation skipped entirely, and on
    odoo-erp `author:@me` re-opens the 2026-07-20 foreign-stream leak the
    branch exists to prevent."""

    def _failing_gh_api_user(self):
        """A `subprocess.run` stand-in that reproduces the real failure:
        `gh api user` exits 4 ("please run gh auth login")."""
        import subprocess
        import unittest.mock as mk

        real = subprocess.run

        def run(argv, *a, **k):
            if list(argv[:3]) == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(
                    argv, 4, "",
                    "To get started with GitHub CLI, please run: gh auth login")
            return real(argv, *a, **k)

        return mk.patch("subprocess.run", side_effect=run)

    def test_slice_quals_never_returns_a_guessed_qual_set(self):
        with self._failing_gh_api_user():
            try:
                quals = airuleset._slice_quals("montalu")
            except Exception as exc:      # noqa: BLE001 - the type is asserted
                self.assertIsInstance(exc, airuleset.SliceUnresolved)
                return
        self.fail(
            "_slice_quals guessed %r from an identity it could not resolve — "
            "on a shared-account box that means C2's validation is skipped, "
            "and on odoo-erp `author:@me` is the whole maintainer backlog "
            "(the 2026-07-20 foreign-stream leak)" % (quals,))

    def test_the_cli_refuses_rather_than_printing_a_count(self):
        import contextlib
        import io
        import unittest.mock as mk

        buf = io.StringIO()
        with self._failing_gh_api_user():
            with mk.patch.object(airuleset, "resolve_authority",
                                 return_value="branch-merge"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="montalu1"):
                    with mk.patch.object(
                            airuleset, "_gh_out",
                            return_value='[{"number": 1, "title": "t", '
                                         '"createdAt": "2026-07-01T00:00:00Z"}]'):
                        with contextlib.redirect_stdout(buf):
                            with self.assertRaises(SystemExit) as cm:
                                airuleset.cmd_slice_quals(
                                    mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")

    def test_a_resolved_login_still_picks_the_right_branch(self):
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            # #537: montalu is a base-stream rename target, so its shared-account
            # slice now carries BOTH the old and the new stream label (the
            # transition alias). The BRANCH selection (shared-account -> label
            # only) is unchanged; only the label set expanded.
            self.assertEqual(airuleset._slice_quals("montalu"),
                             ["label:stream:montalu", "label:stream:montalu1"])
        with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            # #537: david's own-account slice keeps assignee/author + BOTH the
            # old and new stream label (david + david1) -> 4 quals, not 3.
            self.assertEqual(len(airuleset._slice_quals("david")), 4)

    def test_montalu5_8_slice_quals_derive_generically_no_new_map_needed(self):
        # airuleset#378: statusline/`/goal` stop-proof scoping for
        # montalu5..montalu8 needs NO dedicated map -- `_slice_quals()`
        # already derives a shared-account stream's slice generically from
        # ANY user string once `_gh_login()` resolves to the maintainer
        # login (the exact branch montalu/montalu2/3/4 already take). This
        # PINS that genericity for the new accounts, per the ticket's own
        # explicit "derived automatically ... just PIN it with a test"
        # instruction.
        import unittest.mock as mk

        with mk.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            for user in ("montalu5", "montalu6", "montalu7", "montalu8"):
                self.assertEqual(airuleset._slice_quals(user),
                                 ["label:stream:%s" % user], user)


class TestSliceQualsHandlesAppTokenBoxes(TestCase):
    """#356: david2/david3/david4 (odoo-erp#3282, spun up by airuleset#326)
    authenticate `gh` via a GitHub App INSTALLATION token
    (odoo-erp#3281's `gh-app-stream-tokens` mechanism), never a PAT. An
    App installation token carries NO user identity at all, so
    `gh api user` 403s ("Resource not accessible by integration") on
    EVERY call, structurally — not intermittently. `_gh_login()`
    correctly returns `None` on that (#181 I-2's own established
    contract, unchanged here), which used to make `_slice_quals()`
    raise `SliceUnresolved` unconditionally, so `slice-quals --count`
    could never reach 0 and the fork-no-merge/branch-merge `/goal`
    stop-proof condition (B) could never legitimately terminate on
    these boxes.

    Detection is a LOCAL, static fact — the App-token directory's
    presence (`~/.config/gh-app-tokens/`, the exact path
    `scripts/gh-app/gh-app-token.sh`/`push-stream-tokens.sh` read from
    and create in the real, deployed `zbynekdrlik/odoo-erp` mechanism)
    — never the network call itself. That is what makes it safe: it
    removes the identity ambiguity structurally instead of trying to
    classify a transient/generic gh failure (see the rejected
    alternative on the ticket's own design comment)."""

    def _app_token_dir(self, tmp):
        d = Path(tmp) / "gh-app-tokens"
        d.mkdir(parents=True)
        return d

    def _failing_gh_api_user(self):
        # Mirrors TestSliceQualsRefusesAnUnresolvableIdentity's own helper
        # ABOVE — a real 403 is just one more non-zero exit, and
        # `_gh_login` already treats ANY non-zero exit identically
        # (#181 I-2). Reusing the SAME shape here is deliberate: it proves
        # the fix works for the real failure mode, not a hand-picked one.
        import subprocess
        import unittest.mock as mk

        real = subprocess.run

        def run(argv, *a, **k):
            if list(argv[:3]) == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(
                    argv, 1, "",
                    "gh: HTTP 403: Resource not accessible by integration "
                    "(https://api.github.com/user)")
            return real(argv, *a, **k)

        return mk.patch("subprocess.run", side_effect=run)

    def test_an_app_token_box_gets_the_label_only_slice_never_slice_unresolved(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with self._failing_gh_api_user():
                    quals = airuleset._slice_quals("david2")
        self.assertEqual(quals, ["label:stream:david2"])

    def test_an_app_token_box_never_calls_gh_login_at_all(self):
        # The whole point is removing the network dependency, not just
        # tolerating its failure — assert the call never happens.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with mk.patch.object(airuleset, "_gh_login") as spy:
                    quals = airuleset._slice_quals("david3")
        spy.assert_not_called()
        self.assertEqual(quals, ["label:stream:david3"])

    def test_a_pat_box_with_no_app_token_dir_is_completely_unaffected(self):
        # False-positive control: without the App-token dir, both of
        # _slice_quals()'s EXISTING branches behave byte-identically to
        # before this fix.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "gh-app-tokens"      # deliberately never created
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(missing)}):
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="zbynekdrlik"):
                    # #537: the base-stream rename alias expands montalu's
                    # shared-account slice to both labels; the App-token
                    # branch selection this test guards is still unaffected.
                    self.assertEqual(airuleset._slice_quals("montalu"),
                                     ["label:stream:montalu", "label:stream:montalu1"])
                with mk.patch.object(airuleset, "_gh_login",
                                     return_value="kvaskodev"):
                    self.assertEqual(len(airuleset._slice_quals("david")), 4)

    def test_the_default_token_dir_matches_the_real_deployed_path(self):
        # Locks the ACTUAL path the real scripts/gh-app/*.sh in
        # zbynekdrlik/odoo-erp read from and create -- not an invented
        # convention.
        import unittest.mock as mk

        with mk.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GH_APP_TOKEN_DIR", None)
            got = airuleset._gh_app_token_dir()
        self.assertEqual(got, Path.home() / ".config" / "gh-app-tokens")

    def test_a_plain_file_at_that_path_is_not_treated_as_the_token_dir(self):
        # A stray FILE (not a directory) at the path must not be misread as
        # "provisioned" -- `.is_dir()`, never a bare `.exists()`.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            stray = Path(tmp) / "gh-app-tokens"
            stray.write_text("not a directory")
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(stray)}):
                with self._failing_gh_api_user():
                    with self.assertRaises(airuleset.SliceUnresolved):
                        airuleset._slice_quals("david4")

    def test_a_network_failure_counting_the_slice_still_refuses_never_a_false_zero(self):
        # Fail-SAFE, not fail-OPEN: once the identity ambiguity is resolved
        # LOCALLY (never calls _gh_login at all -- asserted below), a
        # genuine gh failure while actually COUNTING the slice must still
        # refuse (non-zero exit, empty stdout) -- never a false
        # "0 = slice empty".
        import contextlib
        import io
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as tmp:
            d = self._app_token_dir(tmp)
            buf = io.StringIO()
            with mk.patch.dict(os.environ, {"GH_APP_TOKEN_DIR": str(d)}):
                with mk.patch.object(airuleset, "resolve_authority",
                                     return_value="fork-no-merge"):
                    with mk.patch.object(airuleset, "_current_user",
                                         return_value="david2"):
                        with mk.patch.object(airuleset, "_gh_login") as spy:
                            with mk.patch.object(airuleset, "_gh_out",
                                                 return_value=""):
                                with contextlib.redirect_stdout(buf):
                                    with self.assertRaises(SystemExit) as cm:
                                        airuleset.cmd_slice_quals(
                                            mk.Mock(count=True, list=False, waiting=False,
                                                   ops_wait=False, audit=False, extra=None))
        spy.assert_not_called()
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), "")


class TestSliceQualsResolvesAgainstTheRepoRoot(TestCase):
    """#181 I-5: `cmd_slice_quals` resolved authority against the PROCESS cwd
    (`resolve_authority()` reads `Path.cwd()/CLAUDE.md`) while the footer
    resolves against the repo ROOT — so a project marker was invisible to one
    of the two consumers of "THE one definition", and they could disagree
    about which profile the box is even running."""

    def _fake_gh(self, bindir):
        import os as _os
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"api user"*) echo "kvaskodev";;\n'
            '  *"repo view"*) echo "kvaskodev/demo";;\n'
            '  *assignee:@me*) echo \'[{"number":1,"title":"t",'
            '"createdAt":"2026-07-01T00:00:00Z"}]\';;\n'
            '  *) echo "[]";;\n'
            "esac\n")
        gh.chmod(0o755)
        _os.environ  # noqa: B018 - keep the import used and explicit
        return gh

    def test_a_project_marker_at_the_root_is_honoured_from_a_subdirectory(self):
        import os
        import subprocess
        import sys as _sys
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=branch-merge -->\n")
            sub = Path(repo, "addons", "deep")
            sub.mkdir(parents=True)
            self._fake_gh(bindir)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
            env.update(HOME=home, PATH="%s:%s" % (bindir, os.environ["PATH"]))
            r = subprocess.run(
                [_sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals", "--count"],
                cwd=str(sub), capture_output=True, text=True, env=env)
            self.assertEqual(
                r.returncode, 0,
                "the root marker was invisible from a subdirectory: %s"
                % r.stderr)
            self.assertEqual(r.stdout.strip(), "1")


class TestSliceQualsDoesNotSilentlyCapItsOwnCount(TestCase):
    """M-2: `slice-quals` still asked for `-L 200` while `core-quals` asked
    for 1000. A single population can only be UNDER-counted by a clamp, never
    zeroed — but the documented "0 = slice empty" contract must not silently
    cap either."""

    def test_the_slice_queries_ask_for_the_same_limit_as_core_quals(self):
        import contextlib
        import io
        import unittest.mock as mk

        seen = []

        def gh(*a, **k):
            seen.append([str(x) for x in a])
            return ('[{"number": 1, "title": "t", '
                    '"createdAt": "2026-07-01T00:00:00Z"}]')

        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="fork-no-merge"):
            with mk.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
                with mk.patch.object(airuleset, "_current_user",
                                     return_value="david1"):
                    with mk.patch.object(airuleset, "_gh_out", side_effect=gh):
                        with contextlib.redirect_stdout(io.StringIO()):
                            airuleset.cmd_slice_quals(
                                mk.Mock(count=True, list=False, waiting=False, ops_wait=False, audit=False, extra=None))
        issue_queries = [q for q in seen if q and q[0] == "issue"]
        self.assertTrue(issue_queries)
        for q in issue_queries:
            self.assertIn("1000", q)
            self.assertNotIn("200", q)


if __name__ == "__main__":
    main()
