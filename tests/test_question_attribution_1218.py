"""#1218: ONE unanswered ❓ map entry is at most ONE U member.

Live odoo-erp gk FLOW (7./8.10.2026): the question lived on #9065 (needs-answer)
and its block ended `❓ ASKED: … (sledované na #9065)`, but it also said "po
nasadení #8952". Every `#N` in the entry went into the #1213 asked refs, so
the context ticket #8952 moved from I to U next to #9065: U=2 for one question.
The other way round, a ping that names only context tickets was dropped from
the ticketless count and counted nowhere.

Attribution order: an explicit tracking marker → that ticket only; else the
referenced tickets that are U by their OWN labels cover the entry (it adds
nothing); else the entry counts ONCE as a ticketless ping.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import statusbar  # noqa: E402

TRACKED = ("**Otázka — projekt odoo-erp:** uid 15/452 po nasadení #8952 "
           "nesedí s exportom.\n- 1. ponechať\n- 2. opraviť\n"
           "❓ ASKED: ako s tým naložiť? (sledované na #9065)")
CONTEXT_ONLY = ("**Otázka — projekt odoo-erp:** po PR 8730 (#8294) a #8952 "
                "sa zmenil export.\n❓ NEEDS YOU: nasadiť teraz?")


def _seed_map(home, cwd, block):
    d = Path(home) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "discord-questions.json").write_text(json.dumps({
        "suppressed:s-1218": {"session": "s-1218", "cwd": str(cwd),
                              "channel": "", "ts": "1000", "asked": "1000",
                              "question": " ".join(block.split()),
                              "block": block, "suppressed": True}}))


def _labels(*names):
    return [{"name": n} for n in names]


def _gh(bindir, rows, extra_cases=""):
    gh = Path(bindir) / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
        '  *rate_limit*) echo \'{"resources":{"graphql":{"remaining":5000}}}\';;\n'
        '  *"--search label:autopilot-skip"*"-q"*) echo 0;;\n'
        '  *"--search label:autopilot-skip"*) echo "[]";;\n'
        + extra_cases +
        "  *) echo '%s';;\n" % json.dumps(rows) +
        "esac\n")
    gh.chmod(0o755)


def _env(home, bindir, **extra):
    return {**os.environ, "HOME": home, "AIRULESET_QUALS_NO_SNAPSHOT": "1",
            "PATH": "%s:%s" % (bindir, os.environ["PATH"]), **extra}


class CoreQualsAttribution(unittest.TestCase):
    """The gk FLOW shape: `core-quals` on the full-authority box."""

    def _run(self, block, rows, *argv):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            _gh(bindir, rows)
            _seed_map(home, repo, block)
            return subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals"] + list(argv),
                capture_output=True, text=True, cwd=repo,
                env=_env(home, bindir))

    ROWS = [{"number": 8952, "title": "context", "createdAt": "",
             "labels": []},
            {"number": 9065, "title": "the question", "createdAt": "",
             "labels": _labels("needs-answer")}]

    @staticmethod
    def _members(stdout):
        return [ln.split("\t") for ln in stdout.splitlines()
                if ln.strip() and not ln.startswith("#")]

    def test_tracked_entry_lists_only_its_ticket(self):
        r = self._run(TRACKED, self.ROWS, "--waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([m[0] for m in self._members(r.stdout)], ["9065"],
                         "U = the tracked ticket only, never the context "
                         "#8952 and never a ping row:\n" + r.stdout)

    def test_the_context_ticket_stays_workable(self):
        r = self._run(TRACKED, self.ROWS, "--count")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "1",
                         "#8952 (no owner label) is I, #9065 is U")

    TWO_CONTEXT = [{"number": 8952, "title": "a", "createdAt": "", "labels": []},
                   {"number": 8294, "title": "b", "createdAt": "", "labels": []}]

    def test_two_context_refs_count_once_as_a_ping(self):
        r = self._run(CONTEXT_ONLY, self.TWO_CONTEXT, "--waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        members = self._members(r.stdout)
        self.assertEqual(len(members), 1, r.stdout)
        self.assertEqual(members[0][0], "-", "a ticketless ping row")
        self.assertEqual(members[0][3], "ping", r.stdout)

    def test_two_context_tickets_stay_workable(self):
        r = self._run(CONTEXT_ONLY, self.TWO_CONTEXT, "--count")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "2", r.stderr)

    def test_a_family_acceptance_draft_delivers_every_member(self):
        # guard (#755): one draft lists several bare needs-acceptance tickets;
        # every one stays a DELIVERED acceptance, none falls back to `queued`.
        rows = [{"number": n, "title": "t", "createdAt": "",
                 "labels": _labels("needs-acceptance")} for n in (7001, 7002)]
        block = ("**Otázka — projekt odoo-erp:** jeden návrh pre rodinu:\n"
                 "- #7001 (export) — zavrie sa potvrdením\n"
                 "- #7002 (import) — zavrie sa potvrdením\n"
                 "❓ NEEDS YOU: schváliť a poslať?")
        r = self._run(block, rows, "--waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual({m[0]: m[3] for m in self._members(r.stdout)},
                         {"7001": "acceptance", "7002": "acceptance"},
                         r.stdout)

    def test_a_labelled_ref_covers_an_unmarked_ping(self):
        # rule 2: the ask-and-continue shape names its needs-answer ticket in
        # the briefing with no marker; the context #8952 stays I, no ping row.
        block = ("**Otázka — projekt odoo-erp:** #9065 po nasadení #8952?\n"
                 "❓ NEEDS YOU: A či B?")
        r = self._run(block, self.ROWS, "--waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([m[0] for m in self._members(r.stdout)], ["9065"],
                         r.stdout)


class FooterAttribution(unittest.TestCase):
    """The footer on a reduced-authority (slice) box: the refresh cache plus
    the live render agree with `--waiting`."""

    def _refresh_and_render(self, block, numbers_labels):
        user = airuleset._current_user()
        rows = [{"number": n, "title": "t%d" % n, "createdAt": "",
                 "labels": _labels("stream:%s" % user, *extra)}
                for n, extra in numbers_labels]
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir, TemporaryDirectory() as tok:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            _gh(bindir, rows, extra_cases=(
                '  *"label list"*"stream:"*) echo \'[{"name":"stream:%s"}]\';;\n'
                % user))
            _seed_map(home, repo, block)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env=_env(home, bindir, GH_APP_TOKEN_DIR=tok))
            self.assertEqual(r.returncode, 0, r.stderr)
            return _plain(statusbar.tickets_segment(repo, home=home,
                                                    spawn=False))

    def test_tracked_question_is_u_1(self):
        seg = self._refresh_and_render(
            TRACKED, [(8952, ()), (9065, ("needs-answer",))])
        self.assertIn("I 1", seg)
        self.assertIn("U 1", seg)

    def test_context_only_question_is_one_ping(self):
        seg = self._refresh_and_render(CONTEXT_ONLY, [(8952, ()), (8294, ())])
        self.assertIn("I 2", seg)
        self.assertIn("U 1", seg)


def _plain(text):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


class SupplementAttribution(unittest.TestCase):
    """#948 supplement (tickets outside the slice rows): only the ticket an
    entry is attributed to joins U, never a label-U context ticket."""

    def test_tracked_entry_adds_only_its_ticket(self):
        objs = {8952: {"state": "OPEN", "title": "ctx", "createdAt": "",
                       "labels": _labels("needs-answer")},
                9065: {"state": "OPEN", "title": "q", "createdAt": "",
                       "labels": _labels("needs-answer")}}
        root = "/fake/odoo-erp"
        with TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"HOME": home}):
            _seed_map(home, root, TRACKED)
            got = cli_quals._question_map_u_supplement(
                {}, root, lambda argv, cd: json.dumps(objs[int(argv[3])]))
        self.assertEqual(set(got), {9065})


class DeliveredFlagAttribution(unittest.TestCase):
    """#539: a label-U ticket mentioned only as CONTEXT in a question tracked
    on another ticket was not asked: `no-question!` when it has no question
    comment either. The tracked ticket keeps its delivered question."""

    def test_context_needs_answer_ticket_is_flagged(self):
        rows = [{"number": n, "title": "t", "createdAt": "",
                 "labels": _labels("needs-answer")} for n in (8952, 9065)]
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            _gh(bindir, rows, extra_cases=(
                '  *"issue view"*"--json comments"*) '
                'echo \'{"comments": []}\';;\n'))
            _seed_map(home, repo, TRACKED)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--waiting"],
                capture_output=True, text=True, cwd=repo,
                env=_env(home, bindir))
        self.assertEqual(r.returncode, 0, r.stderr)
        reason = {ln.split("\t")[0]: ln.split("\t")[3]
                  for ln in r.stdout.splitlines() if ln[:1].isdigit()}
        self.assertEqual(reason, {"8952": "answer no-question!",
                                  "9065": "answer"}, r.stdout)


class AttributeUnit(unittest.TestCase):
    """The per-entry verdict of the shared leaf."""

    def test_order_marker_then_label_then_ticketless(self):
        import question_attribution as qa
        tracked = {"block": TRACKED}
        self.assertEqual(qa.attribute(tracked, frozenset()), (qa.TRACKED, frozenset({9065})))
        self.assertEqual(qa.attribute(tracked, None), (qa.TRACKED, frozenset({9065})))
        ctx = {"block": CONTEXT_ONLY}
        self.assertEqual(qa.attribute(ctx, frozenset({8294})), (qa.COVERED, frozenset({8294})))
        self.assertEqual(qa.attribute(ctx, frozenset()), (qa.TICKETLESS, frozenset()))
        # cover unknown (no rows read): every reference covers, the #512 dedup
        self.assertEqual(qa.attribute(ctx, None), (qa.COVERED, frozenset({8294, 8952})))
        self.assertEqual(qa.attribute({"block": "bez ticketu"}, None), (qa.TICKETLESS, frozenset()))

    def test_marker_variants_and_last_one_wins(self):
        import question_attribution as qa
        for text in ("x (Tracked on #12)", "x TRACKING #12", "sledovaná na #12",
                     "SLEDOVANÉ NA #3 … (sledované na #12)"):
            self.assertEqual(qa.attribute({"question": text}, frozenset()),
                             (qa.TRACKED, frozenset({12})), text)


if __name__ == "__main__":
    unittest.main()
