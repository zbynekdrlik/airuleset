"""#272 -- a run-card whose 🎯 Cieľ is only the bare ticket number, or whose
✅ Dosiahnuté is empty/generic filler, is undecodable from a phone and must
never reach Discord silently.

Live report: codex-bridge (david stream) posted 4 cards (#457-#460) shaped

    🎯 Cieľ: #457
    ✅ Dosiahnuté: PR zmergnutý, deploy beží

`_notify_run_card`'s pre-existing fallback (`goal = args.goal or title`,
`achieved = args.achieved or "PR zmergnutý, deploy beží"`) only fires when the
field is OMITTED (falsy) -- a truthy-but-contentless value like the literal
string "#457" sails through untouched, and the hardcoded generic achieved
string is itself exactly the kind of filler the fix must refuse.

Fix, in `_notify_run_card`:

  * a bare/blank/whitespace-only/bare-numeric-ref GOAL is auto-ENRICHED from
    the already-fetched `gh issue view` title when one is usable (extending
    the pre-existing omitted-goal fallback to also cover a goal that merely
    LOOKS omitted);
  * a goal that is still unusable (no usable title either) is a hard
    REFUSAL -- same `log_delivery` + stderr + `sys.exit(1)` shape #135's
    "NOT delivered" branch already uses;
  * an empty/whitespace/generic-filler ACHIEVED has nothing to enrich it
    FROM (it is worker-authored content, not something `gh` can supply) and
    is always a hard refusal.

Every test in this file exercises the fix and fails against the pre-fix
`_notify_run_card` (the manual live repro proving today's exact bug shape --
a card with `Cieľ: #457` and `Dosiahnuté: PR zmergnutý, deploy beží` --
was captured separately on the ticket, per STEP 0)."""
import contextlib
import io
import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402


def _run_card_args(**overrides):
    """A `notify --run-card` args Mock with every OTHER `cmd_notify`
    early-return flag pinned False -- `m.Mock` auto-creates every attribute
    as a truthy Mock, so an unpinned flag would silently hijack the
    dispatch (see `test_run_card_gathers_title_and_backlog_then_sends`'s own
    note in `tests/test_airuleset.py`)."""
    base = dict(
        run_card=True, autopilot_done=False, mention_prefix=False,
        repo_name=False, newest_card=False, backfill_digest=False, provision_question_thread=False,
        record_question=False, edit_question=False, channel_id=False,
        owner=False, mirror_owners=False, body=None, run=None,
        repo="o/x", issue=457, pr=None, achieved="Oprava nasadená a overená",
        result=None, goal="Tunel občas vypadne pri krátkom výpadku",
        version="0.4.0-dev.206", merge_sha=None, url=None, review="ok",
        handoff=False, dedup_key=None, dry_run=False,
    )
    base.update(overrides)
    return m.Mock(**base)


def _fake_gh(title="Real Issue Title", labels=None, view_fails=False):
    payload = json.dumps({"title": title, "labels": labels or []})

    def fake(*a, **k):
        if "view" in a:
            return "" if view_fails else payload
        return "3"          # the remaining-backlog count call
    return fake


class _RunCard(unittest.TestCase):
    """Drives `airuleset.cmd_notify` in-process against a mocked `_gh_out`
    and `notify.send` -- the same harness `test_run_card_gathers_title_and_
    backlog_then_sends` (tests/test_airuleset.py) already uses."""

    def _send(self, args, *, gh=None):
        """Run cmd_notify; return (captured-body-or-None, stderr-text,
        systemexit-code-or-None)."""
        captured = {}

        def fake_send(body, **k):
            captured["body"] = body
            captured["dedup"] = k.get("dedup_key")
            return "sent"

        err = io.StringIO()
        code = None
        with m.patch.object(airuleset, "_gh_out",
                            side_effect=gh or _fake_gh()):
            with m.patch("notify.send", side_effect=fake_send):
                with contextlib.redirect_stderr(err):
                    try:
                        airuleset.cmd_notify(args)
                    except SystemExit as exc:
                        code = exc.code
        return captured.get("body"), err.getvalue(), code


class TestBareGoalIsEnrichedFromTheTitle(_RunCard):

    def test_bare_hash_number_goal_is_enriched_from_the_title(self):
        args = _run_card_args(goal="#457")
        body, err, code = self._send(args, gh=_fake_gh(title="Oprava tunela"))
        self.assertIsNone(code, err)
        self.assertIn("🎯 **Cieľ:** Oprava tunela", body)
        self.assertNotIn("Cieľ:** #457", body)
        self.assertIn("enrich", err.lower())

    def test_bare_number_without_the_hash_is_enriched(self):
        args = _run_card_args(goal="457")
        body, _err, code = self._send(args, gh=_fake_gh(title="Oprava tunela"))
        self.assertIsNone(code)
        self.assertIn("🎯 **Cieľ:** Oprava tunela", body)

    def test_empty_goal_is_enriched_from_the_title(self):
        args = _run_card_args(goal="")
        body, _err, code = self._send(args, gh=_fake_gh(title="Oprava tunela"))
        self.assertIsNone(code)
        self.assertIn("🎯 **Cieľ:** Oprava tunela", body)

    def test_whitespace_only_goal_is_enriched_from_the_title(self):
        args = _run_card_args(goal="   ")
        body, _err, code = self._send(args, gh=_fake_gh(title="Oprava tunela"))
        self.assertIsNone(code)
        self.assertIn("🎯 **Cieľ:** Oprava tunela", body)

    def test_a_goal_matching_the_issue_number_with_the_hash_is_enriched(self):
        args = _run_card_args(issue=457, goal="#457")
        body, _err, code = self._send(args, gh=_fake_gh(title="Oprava tunela"))
        self.assertIsNone(code)
        self.assertIn("🎯 **Cieľ:** Oprava tunela", body)


class TestBareGoalWithNoUsableTitleRefuses(_RunCard):

    def test_view_lookup_failure_refuses(self):
        args = _run_card_args(goal="#457")
        body, err, code = self._send(args, gh=_fake_gh(view_fails=True))
        self.assertIsNone(body, "no card body may reach send()")
        self.assertEqual(code, 1)
        self.assertIn("goal", err.lower())

    def test_a_bare_title_cannot_enrich_a_bare_goal(self):
        # A title that is ITSELF just the issue number carries no more
        # meaning than the goal did -- refuse, do not enrich with junk.
        args = _run_card_args(issue=457, goal="#457")
        body, err, code = self._send(args, gh=_fake_gh(title="#457"))
        self.assertIsNone(body)
        self.assertEqual(code, 1)
        self.assertIn("goal", err.lower())


class TestGenericAchievedIsRefused(_RunCard):

    def test_omitted_achieved_is_refused_even_with_a_good_goal(self):
        args = _run_card_args(achieved=None)
        body, err, code = self._send(args)
        self.assertIsNone(body)
        self.assertEqual(code, 1)
        self.assertIn("achieved", err.lower())

    def test_the_literal_leaked_phrase_is_refused_even_when_explicit(self):
        args = _run_card_args(achieved="PR zmergnutý, deploy beží")
        body, _err, code = self._send(args)
        self.assertIsNone(body)
        self.assertEqual(code, 1)

    def test_a_short_generic_word_is_refused(self):
        for bad in ("hotovo", "Done", "MERGED", "  ", ""):
            with self.subTest(bad=bad):
                args = _run_card_args(achieved=bad)
                body, _err, code = self._send(args)
                self.assertIsNone(body, bad)
                self.assertEqual(code, 1, bad)

    def test_a_real_achieved_still_sends(self):
        args = _run_card_args(achieved="Fix nasadený, overený na reálnej ticke")
        body, err, code = self._send(args)
        self.assertIsNone(code, err)
        self.assertIn("Fix nasadený, overený na reálnej ticke", body)


class TestAGoodCardStillSendsNormally(_RunCard):

    def test_a_valid_goal_and_achieved_pass_through_verbatim(self):
        args = _run_card_args(
            goal="Tunel do Money občas vypadne, treba to opraviť",
            achieved="Spojenie sa teraz samo obnoví pri krátkom výpadku")
        body, err, code = self._send(args)
        self.assertIsNone(code, err)
        self.assertIn("Tunel do Money občas vypadne, treba to opraviť", body)
        self.assertIn("Spojenie sa teraz samo obnoví pri krátkom výpadku", body)
        self.assertNotIn("enrich", err.lower())

    def test_existing_short_content_from_test_airuleset_still_passes(self):
        # tests/test_airuleset.py's own established fixtures use "g"/"a" --
        # this fix must not retroactively flag them.
        args = _run_card_args(goal="g", achieved="a")
        body, err, code = self._send(args)
        self.assertIsNone(code, err)
        self.assertIn("Cieľ:** g", body)
        self.assertIn("Dosiahnuté:** a", body)


class TestDryRunStillRefusesButNeverLogs(_RunCard):

    def test_dry_run_with_bad_content_refuses_and_logs_nothing(self):
        args = _run_card_args(achieved=None, dry_run=True)
        calls = []
        with m.patch("notify.log_delivery", side_effect=lambda *a, **k:
                     calls.append((a, k))):
            body, _err, code = self._send(args)
        self.assertIsNone(body)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [], "dry-run must write no durable state")

    def test_a_real_refusal_is_logged_durably(self):
        # Assert the kwargs, not just "something was called" -- the outer
        # `except Exception` handler in `_notify_run_card` ALSO calls
        # log_delivery, so a mutant where `_run_card_refuse` raised before
        # ever logging would still pass a bare `assertTrue(calls)` (round-2
        # adversarial review finding).
        args = _run_card_args(achieved=None, dry_run=False)
        calls = []
        with m.patch("notify.log_delivery", side_effect=lambda *a, **k:
                     calls.append((a, k))):
            self._send(args)
        self.assertTrue(calls, "a real (non-dry-run) refusal must be logged")
        _args, kwargs = calls[0]
        self.assertEqual(kwargs.get("kind"), "run-card")
        self.assertIn("achieved", kwargs.get("reason", "").lower())

    def test_a_refusal_never_logs_the_raw_content(self):
        # #157's own lesson, applied one layer up: a guard's own AUDIT LOG
        # is a second place the thing it protects can come to rest -- the
        # durable log gets a short CLASSIFICATION, never the raw --goal/
        # --achieved text (round-2 adversarial review finding 6). Whatever
        # gets refused is, by construction, always a denylisted/contentless
        # value -- so the regression is "does the LOG line quote it back",
        # not "could the refused value itself be sensitive".
        args = _run_card_args(achieved="zmergnutý")
        calls = []
        with m.patch("notify.log_delivery", side_effect=lambda *a, **k:
                     calls.append((a, k))):
            self._send(args)
        self.assertTrue(calls)
        self.assertNotIn("zmergnutý", calls[0][1].get("reason", ""))


class TestAdversarialContentShapes(_RunCard):
    """Round-2 adversarial review of #272's own first cut: every one of
    these live-reproduced against the FIRST (pre-review) implementation as
    a card that composed and "sent" successfully despite being just as
    contentless as the reported bug — a trailing period, a dropped
    diacritic, an extra space before a comma, "##457"/"#457.", pure
    punctuation, a placeholder word, or an emoji."""

    def test_trailing_punctuation_on_the_leaked_phrase_is_still_refused(self):
        for bad in ("PR zmergnutý, deploy beží.", "hotovo.", "Done.", "done!"):
            with self.subTest(bad=bad):
                args = _run_card_args(achieved=bad)
                body, _err, code = self._send(args)
                self.assertIsNone(body, bad)
                self.assertEqual(code, 1, bad)

    def test_a_missing_diacritic_is_still_refused(self):
        # "hotove" (no diacritic) must refuse exactly like "hotové" does --
        # the pre-review denylist listed "dokoncene" but not "hotove",
        # which was an inconsistency, not a deliberate choice.
        for bad in ("hotove", "zmergnuty", "dokoncene"):
            with self.subTest(bad=bad):
                args = _run_card_args(achieved=bad)
                _body, _err, code = self._send(args)
                self.assertEqual(code, 1, bad)

    def test_extra_whitespace_around_punctuation_is_still_refused(self):
        args = _run_card_args(achieved="PR zmergnutý , deploy beží")
        _body, _err, code = self._send(args)
        self.assertEqual(code, 1)

    def test_ok_ano_yes_family_is_refused(self):
        for bad in ("OK", "ok", "áno", "ano", "yes", "y"):
            with self.subTest(bad=bad):
                args = _run_card_args(achieved=bad)
                _body, _err, code = self._send(args)
                self.assertEqual(code, 1, bad)

    def test_pure_punctuation_or_symbol_achieved_is_refused(self):
        for bad in ("-", ".", "n/a", "tbd", "TODO", "✅"):
            with self.subTest(bad=bad):
                args = _run_card_args(achieved=bad)
                _body, _err, code = self._send(args)
                self.assertEqual(code, 1, bad)

    def test_punctuation_only_or_placeholder_goal_is_refused(self):
        for bad in (".", "-", "...", "?", "n/a", "None", "null", "[]", "{}",
                   "TODO", "tbd", "xxx", "🎉"):
            with self.subTest(bad=bad):
                args = _run_card_args(goal=bad)
                body, err, code = self._send(
                    args, gh=_fake_gh(title="TODO"))
                # the title itself is ALSO a placeholder here, so this must
                # refuse rather than silently enrich with junk
                self.assertIsNone(body, bad)
                self.assertEqual(code, 1, bad)
                self.assertIn("goal", err.lower())

    def test_near_miss_bare_refs_are_still_bare(self):
        # "##457"/"#457."/"#457," carry no more meaning than a bare "#457"
        # -- they must be treated the SAME way: enriched from a usable title.
        for bad in ("##457", "#457.", "#457,"):
            with self.subTest(bad=bad):
                args = _run_card_args(goal=bad)
                body, err, code = self._send(
                    args, gh=_fake_gh(title="Oprava tunela"))
                self.assertIsNone(code, err)
                self.assertIn("Cieľ:** Oprava tunela", body)

    def test_a_real_word_that_is_not_on_any_denylist_still_sends(self):
        # Positive control: the classifier must not become so aggressive it
        # starts refusing genuinely short-but-real content.
        args = _run_card_args(achieved="Oprava nasadená")
        body, err, code = self._send(args)
        self.assertIsNone(code, err)
        self.assertIn("Oprava nasadená", body)


if __name__ == "__main__":
    unittest.main()
