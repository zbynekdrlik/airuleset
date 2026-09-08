"""#948 — a question referencing #N on a shared-gh-identity/app-token box where
#N lacks the stream label produces U=0 in the footer despite a real pending
question. The ticket falls through BOTH paths: the label-based user_waiting (the
ticket isn't in the slice search) AND the ticketless-ping count (the ping
references #N, so it's excluded from the ticketless count).

RED test: reproduces the incident shape (app-token box, question map entry with
#N reference, ticket #N carries needs-answer but NOT the stream label -> U=0).
The fix makes the cmd_tickets_status refresh supplement user_waiting with
question-map-referenced tickets that fell outside the slice."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar


def _labels(*names):
    return [{"name": n} for n in names]


class QuestionSliceGap(unittest.TestCase):
    """On an app-token box, a ping referencing #42 (which carries
    needs-answer but NOT stream:<user>) must still show U >= 1 in the
    footer cache, not U=0."""

    def _seed_question_map(self, home, cwd, refs_ticket=42):
        """Seed the question map with a single entry whose block references
        #<refs_ticket> — the shape the ask-and-continue flow produces."""
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps({
            "suppressed:test-session": {
                "session": "test-session",
                "cwd": str(cwd),
                "channel": "",
                "ts": 1000,
                "asked": 1000,
                "question": "Otazka k #%d nieco" % refs_ticket,
                "block": "**Otazka -- projekt odoo-erp:**\nOtazka k #%d nieco\n"
                         "NEEDS YOU: rozhodnutie" % refs_ticket,
                "suppressed": True,
            }
        }))

    def _fake_gh_app_token_box(self, bindir, tokendir):
        """Set up a fake gh for an app-token box where:
        - stream label search returns ticket #10 (workable, no user-waiting)
        - ticket #42 carries needs-answer but NOT the stream label
        - gh issue view 42 returns its labels (needs-answer only)

        Uses `stream:<_current_user()>` as the stream label so that
        `_partition_workable(own_stream=_current_user())` correctly routes
        own-stream tickets to user_waiting (the #654 foreign-stream check
        compares `_stream_owner_of(labels)` against `own_stream`; a
        `stream:<X>` label where X is NOT in AUTHORITY_BY_USER returns ""
        from `_stream_owner_of`, which is falsy, so the #654 gate does not
        fire — the ticket stays in user_waiting, matching real behaviour
        for this test's own stream)."""
        # Create the app-token dir to make _is_gh_app_token_box() True
        tokendir_path = Path(tokendir)
        tokendir_path.mkdir(parents=True, exist_ok=True)

        user = airuleset._current_user()
        stream_label = "stream:%s" % user

        gh = Path(bindir) / "gh"
        # The fake gh:
        # - repo view -> slug
        # - label:stream:* search -> only #10 (workable)
        # - rate_limit -> high budget (skip guard)
        # - issue view 42 -> labels=[needs-answer]
        ten = json.dumps([{
            "number": 10, "title": "workable",
            "createdAt": "2026-01-01T00:00:00Z",
            "labels": [{"name": stream_label}],
        }])
        fortytwo = json.dumps({
            "labels": [{"name": "needs-answer"}],
            "state": "OPEN",
        })
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
            '  *rate_limit*) echo \'{"resources":{"graphql":{"remaining":5000}}}\';;\n'
            '  *"label:stream:"*autopilot-skip*) echo "[]";;\n'
            "  *\"label:stream:\"*) echo '%s';;\n" % ten +
            "  *\"issue\"*\"view\"*\"42\"*) echo '%s';;\n" % fortytwo +
            '  *) echo "[]";;\n'
            'esac\n')
        gh.chmod(0o755)

    def test_user_waiting_counts_question_map_ticket_outside_slice(self):
        """RED: a question-map entry referencing #42 (needs-answer, no stream
        label) on an app-token box should produce user_waiting >= 1. Before the
        fix, user_waiting = 0 because #42 is outside the slice search AND the
        ping is excluded from ticketless count (it references #42)."""
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir, TemporaryDirectory() as tokendir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            self._fake_gh_app_token_box(bindir, tokendir)
            self._seed_question_map(home, repo, refs_ticket=42)

            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": "%s:%s" % (bindir, os.environ["PATH"]),
                     "GH_APP_TOKEN_DIR": tokendir})
            self.assertEqual(r.returncode, 0, r.stderr)

            cache_path = (statusbar.cache_dir(home) /
                          (statusbar.cwd_key(repo) + ".json"))
            self.assertTrue(cache_path.exists(), "cache file must exist")
            cache = json.loads(cache_path.read_text())
            self.assertGreaterEqual(
                cache.get("user_waiting", 0), 1,
                "user_waiting must be >= 1 because #42 (needs-answer) is "
                "referenced in the question map but falls outside the slice "
                "search. Before the fix, user_waiting = 0 -- the #948 gap.")

    def test_no_double_count_when_ticket_is_in_slice(self):
        """When the question-map-referenced ticket IS in the slice (carries
        the stream label + needs-answer), user_waiting must NOT double-count."""
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir, TemporaryDirectory() as tokendir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")

            tokendir_path = Path(tokendir)
            tokendir_path.mkdir(parents=True, exist_ok=True)

            user = airuleset._current_user()
            stream_label = "stream:%s" % user
            both = json.dumps([
                {"number": 10, "title": "workable",
                 "createdAt": "2026-01-01T00:00:00Z",
                 "labels": [{"name": stream_label}]},
                {"number": 42, "title": "question ticket",
                 "createdAt": "2026-01-01T00:00:00Z",
                 "labels": [{"name": stream_label},
                            {"name": "needs-answer"}]},
            ])
            gh = Path(bindir) / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
                '  *rate_limit*) echo \'{"resources":{"graphql":{"remaining":5000}}}\';;\n'
                '  *"label:stream:"*autopilot-skip*) echo "[]";;\n'
                "  *\"label:stream:\"*) echo '%s';;\n" % both +
                '  *) echo "[]";;\n'
                'esac\n')
            gh.chmod(0o755)

            self._seed_question_map(home, repo, refs_ticket=42)

            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": "%s:%s" % (bindir, os.environ["PATH"]),
                     "GH_APP_TOKEN_DIR": tokendir})
            self.assertEqual(r.returncode, 0, r.stderr)

            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            # #42 already in slice -> user_waiting = 1 (from partition), not 2
            self.assertEqual(
                cache.get("user_waiting", 0), 1,
                "user_waiting must be exactly 1 -- the ticket is in the slice "
                "AND in the question map, but must NOT be double-counted.")


class SupplementUnit(unittest.TestCase):
    """Unit tests for _question_map_u_supplement — pure Python, no subprocess.
    Covers the review findings: MAJOR-1 (closed ticket), MINOR-3 (cap),
    MINOR-4 (corrupt map, gh failure).

    Each test sets os.environ["HOME"] to a temp dir so
    `statusbar.question_map_ticket_refs(root)` reads the seeded map
    (it resolves `~/.claude/` from HOME). setUp/addCleanup restores HOME
    (#385 isolation discipline)."""

    def setUp(self):
        self._orig_home = os.environ.get("HOME")
        self._tmpdir = TemporaryDirectory()
        self._home = self._tmpdir.name
        os.environ["HOME"] = self._home
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        else:
            os.environ.pop("HOME", None)
        self._tmpdir.cleanup()

    def _seed_qmap(self, cwd, refs):
        """Seed question map with entries referencing each number in refs."""
        d = statusbar._claude_dir(self._home)
        d.mkdir(parents=True, exist_ok=True)
        entries = {}
        for i, n in enumerate(refs):
            entries["suppressed:s%d" % i] = {
                "session": "s%d" % i, "cwd": str(cwd),
                "channel": "", "ts": 1000, "asked": 1000,
                "question": "q #%d" % n,
                "block": "q #%d" % n,
                "suppressed": True,
            }
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def test_returns_set_of_open_user_waiting_numbers(self):
        """Basic: an OPEN needs-answer ticket outside rows is returned."""
        cwd = "/fake/repo"
        self._seed_qmap(cwd, [42])
        rows = {}  # empty slice

        def runner(argv, cd):
            if "42" in argv:
                return json.dumps({"labels": _labels("needs-answer"),
                                   "state": "OPEN"})
            return ""

        result = airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertIsInstance(result, set)
        self.assertEqual(result, {42})

    def test_closed_ticket_excluded(self):
        """MAJOR-1: a CLOSED ticket with needs-answer must NOT inflate U."""
        cwd = "/fake/repo"
        self._seed_qmap(cwd, [42])
        rows = {}

        def runner(argv, cd):
            if "42" in argv:
                return json.dumps({"labels": _labels("needs-answer"),
                                   "state": "CLOSED"})
            return ""

        result = airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertEqual(result, set(),
                         "a CLOSED needs-answer ticket must NOT be counted")

    def test_ticket_in_rows_skipped(self):
        """No-double-count: a ticket already in rows is not re-fetched."""
        cwd = "/fake/repo"
        self._seed_qmap(cwd, [42])
        rows = {42: {"number": 42, "labels": _labels("needs-answer")}}
        calls = []

        def runner(argv, cd):
            calls.append(argv)
            return ""

        result = airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertEqual(result, set())
        self.assertEqual(len(calls), 0,
                         "runner must NOT be called for a ticket in rows")

    def test_gh_failure_returns_empty(self):
        """MINOR-4: gh returning empty string (failure) is safe."""
        cwd = "/fake/repo"
        self._seed_qmap(cwd, [42])
        rows = {}

        def runner(argv, cd):
            return ""  # gh failure

        result = airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertEqual(result, set())

    def test_corrupt_map_returns_empty(self):
        """MINOR-4: a corrupt question map is safe."""
        cwd = "/fake/repo"
        d = statusbar._claude_dir(self._home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text("NOT JSON")
        rows = {}

        def runner(argv, cd):
            return ""

        result = airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertEqual(result, set())

    def test_cap_limits_gh_calls(self):
        """MINOR-3: at most _QMAP_SUPPLEMENT_CAP gh calls."""
        import cli_quals
        cwd = "/fake/repo"
        many = list(range(1, cli_quals._QMAP_SUPPLEMENT_CAP + 5))
        self._seed_qmap(cwd, many)
        rows = {}
        calls = []

        def runner(argv, cd):
            calls.append(argv)
            return json.dumps({"labels": _labels("needs-answer"),
                               "state": "OPEN"})

        airuleset._question_map_u_supplement(rows, cwd, runner)
        self.assertLessEqual(
            len(calls), cli_quals._QMAP_SUPPLEMENT_CAP,
            "must not exceed _QMAP_SUPPLEMENT_CAP gh calls")


if __name__ == "__main__":
    unittest.main()
