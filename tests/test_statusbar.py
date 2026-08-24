"""Statusline 🎫 ticket segment (statusbar.py + tickets-status CLI).

The user's ask (#367, third simplification round after #307/#313): the
bottom status bar (next to the ctx/limit meters) shows ONE live, decreasing
number — how many tickets this box still has left before everything is
done — never a ratio/total pair and never a badge the user cannot explain.
Hard rule: the statusline render NEVER blocks on gh — it reads local caches
and refreshes them via a detached background command.

`_seed_progress`/`~/.claude/autopilot-progress/<repo>.json` still exists
below purely for `AutopilotProgressFeed` (which tests the WRITER,
`airuleset._write_autopilot_progress` — still used by `notify --run-card`
and by watchdog job 20's own goal-armed evidence check) and for the
inertness test in `TicketsSegment` proving the render no longer reads it.
"""

import getpass
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar


def _seed_cache(home, cwd, open_n=None, name="", ts=None, gk=None, scope=None,
                skipped=None, user_waiting=None):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    entry = {"open": open_n, "name": name, "root": str(cwd),
             "ts": int(time.time() if ts is None else ts)}
    if gk is not None:
        entry["gk"] = gk
    if scope is not None:
        entry["scope"] = scope
    if skipped is not None:
        entry["skipped"] = skipped
    if user_waiting is not None:
        entry["user_waiting"] = user_waiting
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))


def _seed_progress(home, name, done, remaining, ts=None):
    d = statusbar.progress_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (name + ".json")).write_text(json.dumps(
        {"done": done, "remaining": remaining,
         "ts": int(time.time() if ts is None else ts)}))


class TicketsSegment(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.cwd = "/home/x/devel/demo"

    def _seg(self, now=None):
        # spawn=False: unit tests never launch the background refresher
        return statusbar.tickets_segment(self.cwd, now=now, home=self.home,
                                         spawn=False)

    def test_open_issue_count_when_no_autopilot(self):
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        self.assertIn("I 14", self._seg())

    def test_progress_cache_presence_never_affects_render(self):
        # #367 dropped the whole 'run D/T' branch that used to read
        # ~/.claude/autopilot-progress/<repo>.json — a fresh, stale, or
        # absent progress file must all render byte-identically. Locks the
        # removal with real teeth: a mutant reintroducing the read would
        # make the fresh-progress case diverge from the other two.
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        no_progress = self._seg()
        self.assertIn("I 14", no_progress)
        self.assertNotIn("run", no_progress)
        _seed_progress(self.home, "demo", done=3, remaining=14)
        self.assertEqual(self._seg(), no_progress)
        _seed_progress(self.home, "demo", done=3, remaining=14,
                       ts=time.time() - statusbar.AUTOPILOT_RUN_WINDOW_S - 60)
        self.assertEqual(self._seg(), no_progress)
        self.assertNotIn("run", no_progress)

    def test_render_never_resurrects_dropped_forms_from_a_legacy_cache(self):
        # #367 adversarial review: a REAL box carries a cache written by the
        # PRE-#367 code for up to TTL after deploy — scope=core plus the
        # legacy `streamy`/`gk_req` fields the refresh no longer writes.
        # The render must treat every dropped form as fully inert: plain
        # `I N`, no `core` suffix, no `· str M`, no `· gkq N`. Locks the
        # drops with real teeth — a mutant re-adding the `core` suffix (or
        # the `streamy`/`gk_req` reads) passed the whole rewritten suite,
        # because every other assertion is a one-sided assertIn("I N").
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / (statusbar.cwd_key(self.cwd) + ".json")).write_text(json.dumps(
            {"open": 4, "name": "demo", "root": str(self.cwd),
             "ts": int(time.time()), "scope": "core",
             "streamy": 5, "gk_req": 3}))
        seg = self._seg()
        self.assertIn("I 4", seg)
        self.assertNotIn("core", seg)
        self.assertNotIn("str", seg)
        self.assertNotIn("gkq", seg)

    def test_unknown_repo_renders_nothing(self):
        _seed_cache(self.home, self.cwd, open_n=None, name="")   # gh unavailable
        self.assertEqual(self._seg(), "")

    def test_no_cache_renders_nothing(self):
        self.assertEqual(self._seg(), "")

    def test_empty_cwd_renders_nothing(self):
        self.assertEqual(statusbar.tickets_segment("", home=self.home,
                                                   spawn=False), "")

    def test_spawn_guard_marker_throttles(self):
        # _spawn_refresh must be a no-op while the guard marker is fresh — a
        # burst of renders may spawn at most one refresher per SPAWN_GUARD_S.
        import unittest.mock as m
        calls = []
        with m.patch.object(statusbar.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            statusbar._spawn_refresh(self.cwd, home=self.home)
            statusbar._spawn_refresh(self.cwd, home=self.home)
        self.assertEqual(len(calls), 1, "second spawn within guard must be skipped")


class RefreshCLI(unittest.TestCase):
    """`airuleset.py tickets-status --refresh --cwd <dir>` — the only place that
    calls gh; writes the per-cwd cache the statusline reads."""

    def test_refresh_writes_cache_from_git_and_gh(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            # #367: the full-authority open-count query now unions
            # `_obligation_quals()` (3 quals, each a real `gh issue list
            # --json ...` array call) via `_union_open_issues` -- a bare
            # count no longer parses. All 3 quals return the SAME 7-item
            # array here (numbers unify by key, so the union is still 7).
            SEVEN = '[{"number":1},{"number":2},{"number":3},{"number":4},' \
                    '{"number":5},{"number":6},{"number":7}]'
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = repo ]; then echo "zbynekdrlik/demo"; '
                'else echo \'%s\'; fi\n' % SEVEN)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("open=7", r.stdout)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 7)
            self.assertEqual(cache["name"], "demo")
            # and the segment composes from that cache
            self.assertIn("I 7", statusbar.tickets_segment(repo, home=home,
                                                               spawn=False))

    def test_refresh_scopes_count_to_own_slice_for_reduced_authority(self):
        # Gatekeeper goal (2026-07-11): a sub-dev stream's statusline must show ITS
        # OWN slice (assignee:@me OR author:@me, open, non-skip), not the whole repo
        # backlog — David saw "Issues 16" instead of his 6 ("je to chaos"). Authority
        # comes from resolve_authority (marker-aware); full boxes keep the full count.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                # scoped calls return JSON arrays; union {1,2} ∪ {2,3} = 3 issues
                '  *assignee:@me*) echo \'[{"number":1},{"number":2}]\';;\n'
                '  *author:@me*)   echo \'[{"number":2},{"number":3}]\';;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'   # the full-repo count a scoped box must NOT use
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 3)          # own slice, NOT 16
            self.assertEqual(cache.get("scope"), "mine")
            self.assertIn("I 3", statusbar.tickets_segment(repo, home=home,
                                                                spawn=False))

    def test_scoped_render_splits_active_vs_gk_bucket(self):
        # Gatekeeper follow-up (2026-07-11): the sub-dev slice renders TWO numbers —
        # active-on-me vs already handed off to the gatekeeper ("aby bolo jasne ze
        # dalsie tickety su uz preradene na gatekeeper"). Format: "Issues 1 · gk 5".
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=1, name="demo", gk=5, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("I 1", seg)
            self.assertIn("gk 5", seg)

    def test_scoped_render_zero_active_still_shows_gk(self):
        # David's expected live state: "Issues 0 · gk 5" — nothing active, 5 waiting.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=0, name="demo", gk=5, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("I 0", seg)
            self.assertIn("gk 5", seg)

    def test_scoped_render_gk_zero_is_hidden(self):
        # #313 pt 3 REVERSES the #164 "gk 0 must render too" call above: the
        # user reads a bare '· gk 0' as noise on every repo where it is
        # routinely 0 ("nechapem na co vidim str 0"). Hide it like every
        # other zero-value bucket on this line already does.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=4, name="demo", gk=0, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("I 4", seg)
            self.assertNotIn("gk", seg)

    def test_full_authority_render_has_no_gk(self):
        # A full box's cache has no gk key → plain single number, never "gk".
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=4, name="demo")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("I 4", seg)
            self.assertNotIn("gk", seg)

    def test_refresh_partitions_slice_by_ready_for_review_label(self):
        # The gk bucket = own-slice tickets carrying the ready-for-review label
        # (auto-labeled at the sub-dev hand-off by subdev-handoff-label.yml, PR #1420).
        # #391 REVERSES #367 for the reduced-authority path: N (`cache["open"]`)
        # is now own UNHANDLED work (`len(mine) - gk`), never the full slice --
        # a sub-dev's responsibility is fulfilled once a ticket is handed off,
        # not once the gatekeeper has also closed it. `gk` is unchanged, an
        # informational badge of how many are parked with the gatekeeper.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                # union {1 (no label), 2 (r4r), 3 (r4r)} -> mine=3, gk=2,
                # N = own UNHANDLED = 3 - 2 = 1 (#391)
                '  *assignee:@me*) echo \'[{"number":1,"labels":[]},'
                '{"number":2,"labels":[{"name":"ready-for-review"}]}]\';;\n'
                '  *author:@me*)   echo \'[{"number":2,"labels":[{"name":"ready-for-review"}]},'
                '{"number":3,"labels":[{"name":"ready-for-review"}]}]\';;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 1)      # own UNHANDLED (#391): 3 - gk(2)
            self.assertEqual(cache["gk"], 2)        # handed off, waiting on gatekeeper
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 1", seg)
            self.assertIn("gk 2", seg)

    def test_refresh_needs_gatekeeper_lane_also_counts_as_handed_off(self):
        # #191 root cause 1 ("different lane"): needs-gatekeeper is
        # airuleset's OWN hand-off lane (cmd_gk_request) — a ticket carrying
        # it is equally out-of-my-hands as one carrying ready-for-review, so
        # it must fold into the SAME gk bucket (against pre-#191 main this
        # ticket miscounted as ACTIVE, gk 0). #391 REVERSES #367 for the
        # reduced-authority path: N is own UNHANDLED work, so this single
        # handed-off ticket now takes N to 0 (mine=1, gk=1) -- gk=1 is what
        # marks it handed-off, no longer a subset of N.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":7,'
                '"labels":[{"name":"needs-gatekeeper"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)      # own UNHANDLED (#391): 1 - gk(1)
            self.assertEqual(cache["gk"], 1)         # ... and it's handed off
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 0", seg)
            self.assertIn("gk 1", seg)

    def test_refresh_skips_the_comment_fallback_entirely_when_the_slice_query_failed(self):
        # #313 pt 2 adversarial review MAJOR-4: a broken slice query already
        # forces open/gk to None -- spending up to 40 extra `gh api` calls
        # on a KNOWN gh-outage/rate-limit path only makes that outage worse
        # for nothing. `assignee:@me` returns malformed JSON here so the
        # slice loop's own exception handler sets `failed = True`; a
        # DIFFERENT qual (`author:@me`) still adds a real, unhandled ticket
        # to `mine` (otherwise the fallback loop has nothing to iterate
        # regardless of the guard, and this test would prove nothing) --
        # the comment fallback must never run for it in this state.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            log = Path(bindir) / "calls.log"
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'echo "$*" >> "%s"\n' % log +
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'not-json{{{\';;\n'
                '  *author:@me*)   echo \'[{"number":1,"labels":[]}]\';;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *"issues/1/timeline"*) echo \'[{"event": "commented", "body":'
                '"READY-FOR-REVIEW: fork pushed, tests green"}]\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertIsNone(cache["open"])       # gh error, never a wrong number
            self.assertIsNone(cache["gk"])
            calls = log.read_text() if log.exists() else ""
            self.assertNotIn("timeline", calls, calls)

    def test_refresh_recovers_a_comment_only_handoff_when_the_label_never_landed(self):
        # #313 pt 2, live-verified against zbynekdrlik/odoo-erp#3239: a
        # fork-no-merge collaborator's own `gh issue edit --add-label`
        # 403s, and the repo's own hand-off-label workflow can independently
        # be broken -- so a genuinely handed-off ticket can carry NO label
        # at all while still having a real READY-FOR-REVIEW comment. The
        # counter must recover it directly from that comment.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":[]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *"issues/1/timeline"*) echo \'[{"event": "commented", "body":"looks good"},'
                '{"event": "commented", "body":"READY-FOR-REVIEW: fork pushed, tests green"}]\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)      # own UNHANDLED (#391): 1 - gk(1)
            self.assertEqual(cache["gk"], 1)         # recovered -- handed off
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 0", seg)
            self.assertIn("gk 1", seg)

    def test_refresh_does_not_recover_a_ticket_with_no_ready_for_review_comment(self):
        # Negative control for the fix above: a ticket with no label and no
        # matching comment stays counted as ACTIVE -- never swept into gk.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":[]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *"issues/1/timeline"*) echo \'[{"event": "commented", "body":"still working on it"}]\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 1)      # still active
            self.assertEqual(cache["gk"], 0)
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 1", seg)
            self.assertNotIn("gk", seg)   # hidden at 0 (#313 pt 3)

    def test_refresh_never_calls_the_comment_fallback_for_an_already_labeled_ticket(self):
        # Cost discipline: a ticket ALREADY handed off via the label needs no
        # extra `gh api .../timeline` call at all.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            log = Path(bindir) / "calls.log"
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'echo "$*" >> "%s"\n' % log +
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"ready-for-review"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = log.read_text() if log.exists() else ""
            self.assertNotIn("timeline", calls, calls)

    def test_refresh_a_bounce_label_overrides_a_stale_ready_for_review_label(self):
        # #313 pt 2 adversarial review round 2, F3: `prio:bounce` is the
        # gatekeeper's own "returned to the sub-dev, not ready" verdict —
        # it must override a stale/lagged `ready-for-review` LABEL too
        # (a case the round-1 fix left open: a ticket carrying BOTH labels
        # at once still counted as handed-off via the label OR-chain
        # alone). No comments needed here — the label alone is decisive.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":['
                '{"name":"ready-for-review"},{"name":"prio:bounce"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *"issues/1/timeline"*) echo \'[]\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 1)      # still active — bounced
            self.assertEqual(cache["gk"], 0)

    def test_refresh_a_bounce_ticket_stays_in_unhandled_count_alongside_real_handoffs(self):
        # #391's own explicit safety test: a `prio:bounce` ticket must stay
        # counted in the reduced-authority N (own UNHANDLED work) even when
        # OTHER tickets in the same slice are genuinely handed off -- the
        # loop must re-activate on a returned bounce, never read it as done
        # just because it once carried a hand-off label. mine = {1, 2, 3}:
        # #1 genuinely handed off (ready-for-review, no bounce), #2 also
        # genuinely handed off (needs-gatekeeper), #3 carries BOTH
        # ready-for-review AND prio:bounce -- the bounce override forces it
        # back to unhandled. gk=2 (#1, #2); N = own UNHANDLED = 3 - 2 = 1
        # (#3 only).
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":['
                '{"name":"ready-for-review"}]},{"number":2,"labels":['
                '{"name":"needs-gatekeeper"}]},{"number":3,"labels":['
                '{"name":"ready-for-review"},{"name":"prio:bounce"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *"issues/1/timeline"*) echo \'[]\';;\n'
                '  *"issues/2/timeline"*) echo \'[]\';;\n'
                '  *"issues/3/timeline"*) echo \'[]\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["open"], 1,
                "the bounced ticket #3 was not counted back into own "
                "UNHANDLED work alongside the 2 genuine hand-offs")
            self.assertEqual(cache["gk"], 2)

    def test_refresh_invalidates_a_stale_hand_off_once_a_bounce_finding_follows_it(self):
        # #313 pt 2 adversarial review round 2, F1/F2: a stale, PRE-bounce
        # hand-off comment must not read as still current once a LATER
        # GATEKEEPER-authored finding/bounce comment supersedes it — the
        # comment-order walk keeps the LAST signal, not the first match.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},
                {"event": "commented", "body": "**GATEKEEPER FINDING:** needs another fix, "
                         "bouncing back."},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"prio:bounce"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 1)      # still active — bounced
            self.assertEqual(cache["gk"], 0)

    def test_refresh_recovers_a_genuine_re_hand_off_after_a_bounce_finding(self):
        # #313 pt 2 adversarial review round 2, F2 (the positive control for
        # the test above): a genuine RE-hand-off comment posted AFTER the
        # bounce's own GATEKEEPER finding comment must be recognised — this
        # is the exact scenario round 1's hard exclusion of bounced tickets
        # broke (odoo-erp#2584's broken hand-off-label workflow means the
        # label alone never updates, so this comment is the ONLY signal).
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},
                {"event": "commented", "body": "**GATEKEEPER FINDING:** needs another fix, "
                         "bouncing back."},
                {"event": "commented", "body": "READY-FOR-REVIEW: addressed the finding, fixed "
                         "and re-pushed."},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"prio:bounce"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)      # own UNHANDLED (#391): 1 - gk(1)
            self.assertEqual(cache["gk"], 1)         # recovered -- re-handed

    def test_refresh_an_invisible_bounce_stays_unhandled_despite_a_stale_hand_off_comment(self):
        # #391 CRITICAL-1 (fresh-context adversarial review): the comment
        # fallback used to OVERWRITE the label-derived bounce override with
        # handed=True whenever the LAST (and only) comment signal was a
        # stale pre-bounce READY-FOR-REVIEW -- reachable through the
        # sanctioned Discord nudge-lane bounce (a BARE prio:bounce label +
        # a sub-dev-authored ACK, no gatekeeper-shaped comment at all --
        # skills/autopilot/SKILL.md). An invisible bounce (no recognised
        # gatekeeper comment anywhere in the thread) must never re-upgrade
        # a bounce-labeled ticket back to handed -- the safe (never-stop)
        # direction for a /goal stop-proof. Distinguishes this from
        # test_refresh_invalidates_a_stale_hand_off_once_a_bounce_finding_
        # follows_it above, whose fixture's LAST comment IS a recognised
        # gatekeeper finding (so it already resolved correctly even before
        # this fix) -- here there is NO gatekeeper-shaped comment at all.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"ready-for-review"},'
                '{"name":"prio:bounce"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["open"], 1,
                "a bounce-labeled ticket must not be flipped back to "
                "handed by a stale hand-off comment when no gatekeeper "
                "comment is visible anywhere in the thread")
            self.assertEqual(cache["gk"], 0)

    def test_refresh_a_processed_needs_acceptance_handoff_is_not_counted_as_gk(self):
        # #507: the READY-FOR-REVIEW comment fallback (#313 pt 2) keys on the
        # comment's PERMANENT existence, so a hand-off the gatekeeper ALREADY
        # processed -- moved `ready-for-review` -> `needs-acceptance` (the
        # "done = client saw it" doctrine, odoo-erp #3145: the ticket stays
        # OPEN pending client acceptance) -- still carries its old
        # READY-FOR-REVIEW comment forever and was counted as parked-with-gk
        # indefinitely (montalu3 live: gk=19, 0 genuinely parked, ALL 19
        # carrying needs-acceptance). A `needs-acceptance` ticket must NOT fall
        # to gk regardless of a stale hand-off comment.
        #
        # #512 (owner decision 2026-08-16) SUPERSEDES #507's FOOTER placement of
        # a BARE `needs-acceptance` ticket: instead of the stream's own workable
        # `I N` it now lands in `U N` (waiting on the OWNER's acceptance) — but
        # it still must NOT count as gk. The discriminator proven in ONE slice:
        # #1 processed (needs-acceptance + stale READY-FOR-REVIEW comment) must
        # NOT be gk and now leaves `I N` into `U N`; #2 genuinely parked
        # (ready-for-review) MUST stay gk.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: fork pushed, tests green"},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"needs-acceptance"}]},'
                '{"number":2,"labels":[{"name":"ready-for-review"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["gk"], 1,
                "only the genuinely-parked ready-for-review ticket (#2) is "
                "gk; the processed needs-acceptance ticket (#1) must not be "
                "re-counted by its stale READY-FOR-REVIEW comment")
            # #622 (REVERSES #539 chained-I): a bare needs-acceptance is queued for
            # owner approval → U unconditionally, whether or not a draft was
            # delivered. So #1 leaves the workable I N into U N. (Its gk-suppression
            # — the test's core purpose — is unchanged.)
            self.assertEqual(
                cache["open"], 0,
                "#622: the bare needs-acceptance #1 is queued → U, not the "
                "workable I; only the gk-parked #2 remains, netted out of open")
            self.assertEqual(
                cache.get("user_waiting"), 1,
                "#622: #1 (bare needs-acceptance) is queued on the owner → U N")

    def test_refresh_a_needs_acceptance_ticket_still_labeled_ready_for_review_stays_gk(self):
        # #507 adversarial defense: the needs-acceptance suppression must
        # NEVER drop a ticket that is GENUINELY still parked with the
        # gatekeeper. A genuine (re-)hand-off carries the `ready-for-review`
        # (or `needs-gatekeeper`) LABEL -- the repo's subdev-handoff-label
        # workflow re-adds it on the hand-off comment (live-observed on
        # odoo-erp#3068, github-actions[bot] labeled needs-gatekeeper) -- so
        # it is handed via the LABEL path (label_handed=True) and never even
        # reaches the comment-fallback candidate loop the suppression guards.
        # A ticket carrying BOTH needs-acceptance AND ready-for-review is
        # therefore still counted as gk (label wins); the suppression only
        # affects handed=False tickets.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"needs-acceptance"},'
                '{"name":"ready-for-review"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["gk"], 1,
                "a genuinely-parked ticket (ready-for-review label) must stay "
                "gk even when it also carries needs-acceptance -- the "
                "suppression must not drop a labeled hand-off")
            self.assertEqual(cache["open"], 0)
            # #512 negative control: the gk/bounce override keeps a
            # needs-acceptance + ready-for-review ticket OUT of U — it is a
            # genuine re-hand-off (gk), never "waiting on the owner's acceptance".
            self.assertEqual(cache.get("user_waiting"), 0,
                             "needs-acceptance + ready-for-review must stay gk, "
                             "never fold into U (#507 precedence, #512)")

    def test_refresh_processed_needs_acceptance_not_gk_on_the_shared_account_slice(self):
        # #507 review MINOR (test fidelity): the two tests above exercise the
        # OWN-account 3-qual slice (assignee/author/stream). But the real
        # montalu3 incident (and the fix's live-verify) is a SHARED-account box
        # -- gh login == the maintainer, so `_slice_quals` yields the SINGLE
        # `label:stream:<user>` qual and the recovery block (cli_quals.py, the
        # `len(quals)==1 and startswith("label:stream:")` branch) ALSO runs.
        # This locks the fix on that exact topology: the recovery candidate
        # query (needs-gatekeeper,ready-for-review) returns none, so a
        # needs-acceptance-only ticket is never re-added handed there, and the
        # comment-fallback exclusion keeps its stale READY-FOR-REVIEW comment
        # from re-counting it. #1 processed (needs-acceptance + stale comment)
        # must NOT count; #2 (ready-for-review) MUST.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=branch-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: merged into develop, tests green"},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *"api user"*) echo "' + airuleset.MAINTAINER_GH_LOGIN +
                '";;\n'
                '  *needs-gatekeeper,ready-for-review*) echo "[]";;\n'
                '  *label:stream:*) echo \'[{"number":1,'
                '"labels":[{"name":"needs-acceptance"}]},'
                '{"number":2,"labels":[{"name":"ready-for-review"}]}]\';;\n'
                '  *"issues/1/timeline"*) echo \'' + comments + '\';;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["gk"], 1,
                "shared-account slice: only the ready-for-review ticket (#2) "
                "is gk; the processed needs-acceptance ticket (#1) must not be "
                "re-counted by its stale comment nor re-added by the recovery "
                "block")
            # #622 (REVERSES #539 chained-I): the bare needs-acceptance #1 is
            # queued for owner approval → U unconditionally, on the shared-account
            # topology too. Only the gk-parked #2 remains, netted out of open.
            self.assertEqual(cache["open"], 0)
            self.assertEqual(cache.get("user_waiting"), 1)

    def test_refresh_a_comment_only_re_handoff_of_needs_acceptance_is_a_known_safe_under_count(self):
        # #507 review MAJOR (accepted, SAFE-direction residual): the label-based
        # suppression cannot distinguish a STALE hand-off comment on a processed
        # ticket (must NOT count) from a GENUINE FRESH re-hand-off comment on a
        # ticket that still carries needs-acceptance and was NOT re-labeled (the
        # auto-labeller broken / a 403'd label-add -- the very label
        # unreliability the #313 comment fallback exists for). Telling them
        # apart needs a per-ticket timeline query (the cost #507 rejected). Such
        # a genuinely-re-handed-off ticket is UNDER-counted here (shown as own
        # workable, not gk). This test LOCKS that known behaviour AND its SAFE
        # direction: the ticket moves INTO the workable/open set, so a /goal
        # loop keeps it alive and never falsely declares the backlog empty -- a
        # bounded, self-healing under-count (resolves the moment the label
        # lands), never the PERMANENT over-count #507 fixed. A precise
        # timeline-based fix is the needs-user-decision follow-up. If that
        # follow-up lands, this assertion is EXPECTED to flip (gk 0 -> 1) and
        # should be updated with its justification, not silently deleted.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            # A genuine FRESH re-hand-off comment (client feedback addressed,
            # re-submitted) -- but no fresh ready-for-review label was added.
            comments = json.dumps([
                {"event": "commented", "body": "READY-FOR-REVIEW: addressed client feedback, "
                         "re-submitted after the needs-acceptance bounce"},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,'
                '"labels":[{"name":"needs-acceptance"}]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["gk"], 0,
                "KNOWN #507 residual (safe direction): a comment-only "
                "re-hand-off of a needs-acceptance ticket is not re-counted as "
                "gk by label alone (the auto-labeller unreliability #313 exists "
                "for) -- so it must NOT falsely stop the loop")
            # #622 (REVERSES #539 chained-I): a bare needs-acceptance is queued for
            # owner approval → U unconditionally. So this comment-only re-hand-off
            # (needs-acceptance by label, not gk-detected) leaves the workable I N
            # into U N — SURFACED on this fork box in `--waiting` (U), where the
            # loop PARKS on it (not silently dropped). NOTE the honest narrowing of
            # the #507/#508 residual: it is NOT picked up gk-side either (a
            # `needs-acceptance` ticket is in GATEKEEPER_PROCESSED_LABELS, EXCLUDED
            # from the READY-FOR-REVIEW comment fallback, and the gk core set is
            # label-based + stream-excluded), so it self-heals ONLY when the repo
            # auto-labeller re-adds `ready-for-review` — #622 trades #507/#508's
            # keep-alive-in-I for this narrow residual per the owner's I/U/W model.
            self.assertEqual(
                cache["open"], 0,
                "#622: a bare needs-acceptance is queued → U, not the workable I N")
            self.assertEqual(
                cache.get("user_waiting"), 1,
                "#622: the comment-only re-hand-off needs-acceptance is in U N")

    def test_footer_refresh_actually_calls_the_shared_handed_derivation(self):
        # MAJOR-2 (fresh-context adversarial review of #391): mirrors the
        # sibling cmd_slice_quals sentinel test (#181 I7's own shape) for
        # the FOOTER'S own --refresh path -- proves it genuinely consumes
        # `_slice_mine_and_handed`'s own returned `(rows, handed, failed)`
        # rather than a re-inlined, potentially-drifted derivation. A
        # reimplementation that re-derives handed status itself instead of
        # calling the shared function fails this even though every
        # label-only fixture test above would still pass it.
        sentinel_rows = {
            5: {"number": 5, "title": "handed",
                "createdAt": "2026-07-01T00:00:00Z", "labels": []},
            6: {"number": 6, "title": "unhandled",
                "createdAt": "2026-07-02T00:00:00Z", "labels": []},
        }
        sentinel_handed = {5: True, 6: False}

        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *) echo "[]";;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            with unittest.mock.patch.dict(
                    os.environ,
                    {"HOME": home,
                     "PATH": "%s:%s" % (bindir, os.environ["PATH"])}):
                with unittest.mock.patch.object(
                        airuleset, "_slice_quals",
                        return_value=["label:stream:montalu"]):
                    with unittest.mock.patch.object(
                            airuleset, "_slice_mine_and_handed",
                            return_value=(sentinel_rows, sentinel_handed,
                                          False)) as sm:
                        airuleset.cmd_tickets_status(
                            unittest.mock.Mock(cwd=repo, refresh=True))
            sm.assert_called()
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
        self.assertEqual(
            cache["open"], 1,
            "the footer's --refresh did not consume _slice_mine_and_"
            "handed's own returned handed map")
        self.assertEqual(cache["gk"], 1)

    def test_refresh_rejects_a_bare_mention_or_a_gatekeeper_finding_comment(self):
        # #313 pt 2 adversarial review MAJOR-2: `_is_readiness_comment` is
        # the SAME precise, line-anchored matcher
        # `skills/process-subdev/templates/subdev-handoff-match.sh` (#1500)
        # already enforces — a bare substring check re-introduces THAT
        # exact over-match incident. Two comments here would both trigger a
        # naive `"ready-for-review" in body.lower()` check and must NOT
        # recover the ticket: (1) the word merely MENTIONED mid-sentence,
        # never at a line start; (2) a GATEKEEPER finding comment whose
        # FIRST line starts with `**GATEKEEPER`, even though a later line
        # quotes the marker.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            comments = json.dumps([
                {"event": "commented", "body": "Note: earlier I said READY-FOR-REVIEW but that "
                         "was premature, still fixing a bug."},
                {"event": "commented", "body": "**GATEKEEPER FINDING:** not ready.\n"
                         "READY-FOR-REVIEW is NOT accurate here."},
            ])
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":[]}]\';;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                "  *\"issues/1/timeline\"*) echo '%s';;\n" % comments +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 1)      # still active
            self.assertEqual(cache["gk"], 0)

    def test_refresh_reattributes_relabeled_handoff_for_shared_account(self):
        # #191 root cause 2 ("ownership relabel") + the issue's own literal
        # acceptance spec: a SHARED-gh-account stream's slice is
        # `label:stream:<user>` ALONE. Ticket #42 was relabelled away from
        # stream:<user> to stream:core once the fix moved to shared code —
        # the ONLY query the shared-account slice runs finds nothing. GitHub's
        # own LABELED timeline event survives that relabel and is the one
        # signal a shared identity can still use to recover it. Against
        # pre-#191 main this rendered "I 0" with no gk bucket at all — the
        # ticket was completely invisible.
        user = getpass.getuser()
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            graphql_body = json.dumps({"data": {"repository": {"i42": {
                "timelineItems": {"nodes": [
                    {"label": {"name": "stream:%s" % user}}]}}}}})
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api user"*) echo "zbynekdrlik";;\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                # shared-account slice: the ONLY query it runs finds nothing
                '  *label:stream:*) echo "[]";;\n'
                # repo-wide hand-off candidates not yet in the slice
                '  *label:needs-gatekeeper,ready-for-review*) '
                'echo \'[{"number":42,"labels":[{"name":"needs-gatekeeper"},'
                '{"name":"stream:core"}]}]\';;\n'
                '  *graphql*) echo \'%s\';;\n' % graphql_body +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache.get("scope"), "mine")
            self.assertEqual(cache["open"], 0)      # own UNHANDLED (#391): 1 - gk(1)
            self.assertEqual(cache["gk"], 1)        # re-attributed, handed off
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertNotEqual(seg, "")
            self.assertIn("I 0", seg)
            self.assertIn("gk 1", seg)

    def test_refresh_reattributes_via_the_new_handed_by_marker(self):
        # #191 adversarial review, CRITICAL C1: Part C's origin marker is
        # `handed-by:<user>`, NOT `stream:<user>` (reusing the ownership
        # label would have made a needs-gatekeeper ticket permanently part
        # of the stream's own `/goal` stop-proof slice). This proves the
        # NEW marker form alone -- with no `stream:*` history at all --
        # is sufficient for the footer to recover it.
        user = getpass.getuser()
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            graphql_body = json.dumps({"data": {"repository": {"i7": {
                "timelineItems": {"nodes": [
                    {"label": {"name": "needs-gatekeeper"}},
                    {"label": {"name": "handed-by:%s" % user}}]}}}}})
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api user"*) echo "zbynekdrlik";;\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *label:needs-gatekeeper,ready-for-review*) '
                'echo \'[{"number":7,"labels":[{"name":"needs-gatekeeper"}]}]\';;\n'
                '  *graphql*) echo \'%s\';;\n' % graphql_body +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)      # own UNHANDLED (#391): 1 - gk(1)
            self.assertEqual(cache["gk"], 1)

    def test_refresh_reattribution_uses_the_temporally_last_origin_event(self):
        # #191 adversarial review, MAJOR M3: "was my label EVER applied" let
        # TWO streams that both once owned a ticket (A -> B -> unlabelled)
        # BOTH reclaim it. Ticket #8 has NO current stream label (so the
        # cheap current-owner skip does not filter it out) but its history
        # shows stream:marek FIRST, then this stream's own handed-by LAST --
        # only the temporally-last event may win. `getpass.getuser()`'s own
        # value is this stream's identity in the test.
        user = getpass.getuser()
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            graphql_body = json.dumps({"data": {"repository": {"i8": {
                "timelineItems": {"nodes": [
                    {"label": {"name": "stream:marek"}},        # earlier
                    {"label": {"name": "handed-by:%s" % user}},  # LAST
                ]}}}}})
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api user"*) echo "zbynekdrlik";;\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *label:needs-gatekeeper,ready-for-review*) '
                'echo \'[{"number":8,"labels":[{"name":"needs-gatekeeper"}]}]\';;\n'
                '  *graphql*) echo \'%s\';;\n' % graphql_body +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["gk"], 1)   # LAST event wins -> this stream

    def test_refresh_reattribution_skipped_for_own_account_slice(self):
        # An own-account stream (assignee/author quals present, 3-qual union)
        # already recovers a relabelled hand-off for free via author:@me —
        # the GraphQL re-attribution step must not even run for it.
        #
        # #191 adversarial review, MAJOR M5 (mutation-verified): the ORIGINAL
        # version of this test stubbed graphql with an UNRELATED label
        # ("stream:x"), so deleting the len(quals)==1 guard entirely still
        # passed (the stub never matched anything either way) -- no teeth.
        # The stub now answers with THIS test's own real identity, so if the
        # guard were removed the count WOULD move and the assertion below
        # would fail.
        user = getpass.getuser()
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                # own-account: gh api user answers something other than the
                # maintainer login (matches the existing convention in this
                # file — the "16" fallback below never matches "api user").
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo "[]";;\n'
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                # if the (own-account) code wrongly ran the re-attribution
                # step it would hit one of these and MATCH.
                '  *label:needs-gatekeeper,ready-for-review*) '
                'echo \'[{"number":99,"labels":[{"name":"needs-gatekeeper"}]}]\';;\n'
                '  *graphql*) echo \'{"data":{"repository":{"i99":'
                '{"timelineItems":{"nodes":[{"label":{"name":"handed-by:%s"}}'
                ']}}}}}\';;\n' % user +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)
            self.assertEqual(cache["gk"], 0)

    def test_refresh_reattribution_bounded_to_unowned_candidates(self):
        # #191 design review: a candidate currently owned by a DIFFERENT
        # registered stream must never be re-attributed to THIS stream, even
        # if this stream's label happens to appear in its history (a
        # legitimate transfer).
        #
        # #191 adversarial review, MAJOR M5 (mutation-verified): the ORIGINAL
        # graphql stub returned "stream:whoever" here too -- deleting the
        # `_stream_owner_of` skip still passed. The stub now answers with
        # THIS test's own real identity, so removing the skip would make the
        # candidate reach GraphQL, match, and move `gk` off 0.
        user = getpass.getuser()
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api user"*) echo "zbynekdrlik";;\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *label:needs-gatekeeper,ready-for-review*) '
                'echo \'[{"number":55,"labels":[{"name":"needs-gatekeeper"},'
                '{"name":"stream:marek"}]}]\';;\n'
                '  *graphql*) echo \'{"data":{"repository":{"i55":'
                '{"timelineItems":{"nodes":[{"label":{"name":"handed-by:%s"}}'
                ']}}}}}\';;\n' % user +
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 0)
            self.assertEqual(cache["gk"], 0)     # #55 stays marek's, not ours

    def test_refresh_full_authority_excludes_other_streams_labels(self):
        # Stream-label ownership (odoo-erp PR #1440, 2026-07-11): the FULL box's
        # counter = tickets THIS box should work via /autopilot — open minus
        # autopilot-skip minus stream:david/montalu/marek (sub-dev-owned). #367:
        # N is now `_obligation_quals()`'s own union (`_core_search_excl()` +
        # needs-gatekeeper + ready-for-review), each a REAL `gh issue list
        # --json ...` array call. Only the core-partition qual's search string
        # carries the stream exclusion, so it alone matches the specific
        # pattern below (a 10-item array); the other two quals fall to the
        # catch-all (empty) — a mutant dropping the exclusion would make the
        # core-partition qual ALSO fall to the empty catch-all, open=0.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            TEN = "[" + ",".join('{"number":%d}' % n for n in range(1, 11)) + "]"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
                '  *-label:stream:david*-label:stream:marek*-label:stream:montalu*) '
                "echo '%s';;\n" % TEN +
                '  *) echo "[]";;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 10)      # own slice, exclusion applied
            self.assertEqual(cache.get("scope"), "core")

    def test_refresh_full_authority_counts_maintainer_action_tickets_outside_core(self):
        # #367's actual semantic change (and the stated reason the `gkq`
        # badge could be dropped at all): the full-authority footer count is
        # `_obligation_quals()`'s union — the core partition PLUS every open
        # `needs-gatekeeper`/`ready-for-review` ticket a sub-dev stream owns.
        # Every other full-authority refresh test returns the SAME array for
        # all three quals (union invariant to which quals run), so a mutant
        # reverting to the old core-partition-only query passed the whole
        # rewritten suite. Here the maintainer-action quals contribute
        # tickets OUTSIDE the 10-ticket core array (#11, #12; #1 dedups):
        # obligation union = 12, core alone = 10.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            TEN = "[" + ",".join('{"number":%d}' % n for n in range(1, 11)) + "]"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                # the POSITIVE skip query ("--search label:autopilot-skip ...")
                # — must not be swallowed by the stream-exclusion pattern below
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                '  *label:needs-gatekeeper*) echo \'[{"number":11}]\';;\n'
                '  *label:ready-for-review*) '
                "echo '[{\"number\":12},{\"number\":1}]';;\n"
                "  *-label:stream:*) echo '%s';;\n" % TEN +
                '  *) echo "[]";;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["open"], 12,
                "full-authority open must be the OBLIGATION union "
                "(core ∪ needs-gatekeeper ∪ ready-for-review), not the "
                "core partition alone")

    def test_refresh_full_authority_foreign_stream_userwaiting_leaves_U(self):
        # #654: the FOOTER core branch (cmd_tickets_status, own_stream=None) must
        # NOT count a FOREIGN stream:<user> answer/decision/action row into `U N`
        # even when it enters the obligation set via the needs-gatekeeper UNION
        # arm — it routes to workable `I` (action-only). The gk box's OWN
        # stream:core / bare user-waiting rows still surface as `U`. Direct footer
        # assertion of the same 4607 label set the CLI tests lock (#367 hardening —
        # the footer branch is hand-duplicated from the CLI partition path).
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            # core arm: #1 plain workable + #2 stream:core+decision (gk's own U);
            # needs-gatekeeper arm: 4607 = stream:david+needs-gatekeeper+decision
            # (foreign → workable I, NOT U).
            core = ('[{"number":1,"labels":[{"name":"bug"}]},'
                    '{"number":2,"labels":[{"name":"stream:core"},'
                    '{"name":"needs-decision"}]}]')
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                '  *label:needs-gatekeeper*) echo \'[{"number":4607,"labels":'
                '[{"name":"stream:david"},{"name":"needs-gatekeeper"},'
                '{"name":"needs-decision"}]}]\';;\n'
                '  *label:ready-for-review*) echo "[]";;\n'
                "  *-label:stream:*) echo '%s';;\n" % core +
                '  *) echo "[]";;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache.get("user_waiting"), 1,
                "#654: only the gk's OWN stream:core+decision #2 is U; the "
                "foreign stream:david row 4607 must NOT inflate U")
            self.assertEqual(
                cache["open"], 2,
                "#654: workable I = {#1 plain, 4607 foreign action-only}; "
                "the foreign row is counted in I, not U")

    def test_refresh_core_count_excludes_permanent_ops_channel_tickets(self):
        # #362: a self-declared PERMANENT `ops-channel` ticket (odoo-erp
        # #1861/#3037 -- a teardown/refresh channel, an automated alert log)
        # must never inflate the footer's own core count either, or the
        # footer and the /goal stop-proof (`core-quals --count`, which
        # already excludes it) would disagree about what "done" means.
        # #367: AUTOPILOT_SKIP_EXCL (which already carries -label:ops-channel)
        # is the shared BASE for every one of `_obligation_quals()`'s 3
        # per-qual queries, so all 3 match the ops-channel pattern below and
        # return the SAME 5-item array -- a mutant dropping the exclusion
        # would fall every one of them to the 9-item catch-all instead.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            FIVE = "[" + ",".join('{"number":%d}' % n for n in range(1, 6)) + "]"
            NINE = "[" + ",".join('{"number":%d}' % n for n in range(1, 10)) + "]"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
                "  *-label:ops-channel*) echo '%s';;\n" % FIVE +
                "  *) echo '%s';;\n" % NINE +
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(
                cache["open"], 5,
                "the core-count query never excludes -label:ops-channel")

    def test_reduced_authority_mine_slice_excludes_ops_channel(self):
        # #362 review: the reduced-authority "mine" slice loop
        # (cmd_tickets_status, own-account 3-qual shape: assignee/author/
        # stream) must ALSO AND -label:ops-channel onto every per-qual
        # search -- the SAME exclusion the full-authority core query above
        # already has. Own-account shape (assignee:@me/author:@me present)
        # mirrors test_refresh_scopes_count_to_own_slice_for_reduced_
        # authority's own established fixture shape.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *-label:ops-channel*assignee:@me*)'
                ' echo \'[{"number":1}]\';;\n'
                '  *assignee:@me*) echo \'[{"number":1},{"number":4}]\';;\n'
                '  *-label:ops-channel*author:@me*)'
                ' echo \'[{"number":2}]\';;\n'
                '  *author:@me*) echo \'[{"number":2},{"number":4}]\';;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            # {1} ∪ {2} = 2, ticket #4 (ops-channel, assigned+authored to
            # me) must NOT leak in -- a mutant reverting AUTOPILOT_SKIP_EXCL
            # back to the bare "-label:autopilot-skip" literal at this call
            # site loses the "-label:ops-channel" branch of the fake
            # entirely and reads 3.
            self.assertEqual(
                cache["open"], 2,
                "ops-channel ticket #4 leaked into the reduced-authority "
                "mine-slice footer count")

    def test_reduced_authority_origin_recovery_candidates_exclude_ops_channel(self):
        # #362 review: the ORIGIN-RECOVERY candidates query (the SHARED-
        # account, single-qual `label:stream:<user>` shape only -- the one
        # branch that finds a ticket relabelled away from stream:<user> via
        # `_last_origin_owner`) must also AND -label:ops-channel onto its
        # own search. This test captures the literal search string sent for
        # THAT specific query (rather than driving the whole GraphQL
        # recovery pipeline to a final count) -- a mutant reverting the
        # AUTOPILOT_SKIP_EXCL usage at this call site changes the captured
        # string and fails the assertion.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            log_path = Path(home) / "gh-calls.log"
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'echo "$*" >> "%s"\n'
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
                '  *"api user"*) echo "zbynekdrlik";;\n'
                '  *) echo "[]";;\n'
                'esac\n' % log_path)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            log = log_path.read_text() if log_path.exists() else ""
            candidate_lines = [ln for ln in log.splitlines()
                               if "needs-gatekeeper,ready-for-review" in ln]
            self.assertTrue(
                candidate_lines,
                "the origin-recovery candidates query never ran -- "
                "log:\n%s" % log)
            for ln in candidate_lines:
                self.assertIn(
                    "-label:ops-channel", ln,
                    "origin-recovery candidates query missing the "
                    "ops-channel exclusion: %r" % ln)

    def test_core_and_total_queries_no_longer_clamp_at_200(self):
        # #181 I5 (round 2): cmd_tickets_status's own core/total queries used
        # to clamp at -L 200. #367 replaced that pair with a single call into
        # the SHARED `_union_open_issues` (used verbatim by `core-quals`
        # too), which hardcodes -L 1000 -- so this window can no longer
        # literally contain "-L", "200" for the open-count computation at
        # all. Kept as a light regression guard against a future re-inlined
        # per-scope query reintroducing the old clamp.
        src = Path(airuleset.__file__).read_text()
        i = src.index('entry["scope"] = "core"')
        window = src[i:i + 2000]
        self.assertNotIn('"-L", "200"', window)

    def test_refresh_subdev_slice_includes_own_stream_label(self):
        # Consistency with the ownership convention: a ticket labeled
        # stream:<this-stream> belongs to this box even when not assigned/authored
        # by it — it must land in the sub-dev slice too.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"labels":[]}]\';;\n'
                '  *author:@me*)   echo \'[{"number":2,"labels":[]}]\';;\n'
                # the stream-labeled ticket nobody assigned yet — union adds #9
                '  *label:stream:*) echo \'[{"number":9,"labels":[]}]\';;\n'
                '  *) echo 17;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 3)       # {1} ∪ {2} ∪ {9}
            self.assertEqual(cache.get("scope"), "mine")

    def test_refresh_outside_git_repo_writes_null(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as nonrepo:
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", nonrepo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     # git rev-parse must FAIL here even under a parent repo
                     "GIT_CEILING_DIRECTORIES": nonrepo})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(nonrepo) + ".json")).read_text())
            self.assertIsNone(cache["open"])
            # → the statusline renders nothing for this dir (and won't re-spawn
            # until the TTL passes)
            self.assertEqual(statusbar.tickets_segment(nonrepo, home=home,
                                                       spawn=False), "")

    def test_refresh_falls_back_to_git_credentials_token_when_gh_unauthenticated(self):
        # #25: david (and every sub-dev stream) never runs `gh auth login` —
        # CLAUDE.md's External Developer Workflow extracts a token from
        # ~/.git-credentials per-command instead. Without a fallback, `gh` in
        # that shell fails every call and the cache is stuck at open=None.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            # #367: the open-count query now unions `_obligation_quals()`
            # (real gh issue-list arrays), so the catch-all must return a
            # JSON array of the target size, not a bare count.
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ -z "$GH_TOKEN" ]; then echo "gh: not authenticated" >&2; exit 1; fi\n'
                'if [ "$1" = repo ]; then echo "kvaskodev/odoo-erp"; else '
                'echo \'[{"number":1},{"number":2},{"number":3},{"number":4},'
                '{"number":5}]\'; fi\n')
            fake_gh.chmod(0o755)
            Path(home, ".git-credentials").write_text(
                "https://kvaskodev:ghp_faketoken123@github.com\n")
            env = {**os.environ, "HOME": home,
                   "PATH": f"{bindir}:{os.environ['PATH']}"}
            env.pop("GH_TOKEN", None)
            env.pop("GITHUB_TOKEN", None)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("open=5", r.stdout)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 5)

    def test_refresh_prefers_real_gh_token_over_git_credentials(self):
        # A box that IS gh-authenticated must never be overridden by a stale
        # ~/.git-credentials token.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$GH_TOKEN" != "real-token" ]; then '
                'echo "wrong token" >&2; exit 1; fi\n'
                'if [ "$1" = repo ]; then echo "zbynekdrlik/demo"; else '
                'echo \'[{"number":1},{"number":2},{"number":3},{"number":4},'
                '{"number":5},{"number":6},{"number":7},{"number":8},'
                '{"number":9}]\'; fi\n')
            fake_gh.chmod(0o755)
            Path(home, ".git-credentials").write_text(
                "https://someone:stale-token@github.com\n")
            env = {**os.environ, "HOME": home,
                   "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GH_TOKEN": "real-token"}
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("open=9", r.stdout)

    def test_refresh_descends_one_level_when_cwd_is_above_the_repo(self):
        # #61: montalu's session cwd (~/devel/odoo) is the PARENT of the real
        # repo (~/devel/odoo/odoo-slovnormal) — git rev-parse only walks UP,
        # so it never finds a repo BELOW cwd unless we look for one.
        with TemporaryDirectory() as home, TemporaryDirectory() as parent, \
                TemporaryDirectory() as bindir:
            repo = Path(parent) / "odoo-slovnormal"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = repo ]; then echo "zbynekdrlik/odoo-slovnormal"; '
                'else echo \'[{"number":1},{"number":2},{"number":3},'
                '{"number":4},{"number":5},{"number":6},{"number":7},'
                '{"number":8},{"number":9},{"number":10},{"number":11},'
                '{"number":12}]\'; fi\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", parent],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("open=12", r.stdout)
            # cached under the ORIGINAL cwd (the session's actual cwd), not the
            # descended repo path — so the statusline (keyed by session cwd)
            # finds it.
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(parent) + ".json")).read_text())
            self.assertEqual(cache["open"], 12)
            self.assertEqual(cache["name"], "odoo-slovnormal")
            self.assertIn("I 12", statusbar.tickets_segment(parent, home=home,
                                                                 spawn=False))

    def test_refresh_stays_null_when_multiple_subdir_repos_are_ambiguous(self):
        # Two candidate repos below cwd → never guess which one.
        with TemporaryDirectory() as home, TemporaryDirectory() as parent:
            for name in ("repo-a", "repo-b"):
                d = Path(parent) / name
                d.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", parent],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(parent) + ".json")).read_text())
            self.assertIsNone(cache["open"])


class SkippedBucket(unittest.TestCase):
    """User ask (2026-07-16): the statusline should also show how many tickets
    are labeled autopilot-skip ("Issues N ... · skipped K"). Unlike the gk
    bucket (a partition of the user's own visible tickets — hiding its zero
    looked like a broken counter), skipped is an EXCLUSION count: 0 means "no
    exclusions", so it renders only when ≥ 1 and stays off the line otherwise.
    """

    def _seg(self, home, cwd):
        return statusbar.tickets_segment(cwd, home=home, spawn=False)

    def test_render_shows_skipped_when_positive(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=12, name="demo", skipped=3)
            seg = self._seg(home, cwd)
            self.assertIn("I 12", seg)
            self.assertIn("skip 3", seg)

    def test_render_hides_skipped_at_zero_or_missing(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=12, name="demo", skipped=0)
            self.assertNotIn("skip", self._seg(home, cwd))
            _seed_cache(home, cwd, open_n=12, name="demo")
            self.assertNotIn("skip", self._seg(home, cwd))

    def test_render_combines_with_gk_bucket(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=1, name="demo", gk=5, scope="mine",
                        skipped=2)
            seg = self._seg(home, cwd)
            self.assertIn("I 1", seg)
            self.assertIn("gk 5", seg)
            self.assertIn("skip 2", seg)

    def test_render_shows_skipped_regardless_of_a_stale_progress_cache(self):
        # #367: the 'run D/T' progress-cache read is GONE -- a progress file
        # (fresh or stale) must never affect this render at all. Skips are
        # exactly the tickets a run would NOT touch, so they must still show.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=9, name="demo", skipped=2)
            _seed_progress(home, "demo", done=1, remaining=3)
            seg = self._seg(home, cwd)
            self.assertNotIn("run", seg)
            self.assertIn("I 9", seg)
            self.assertIn("skip 2", seg)

    def test_refresh_counts_skipped_for_full_authority(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                # the POSITIVE label query (skip count) — must match before the
                # open-count query, whose search embeds -label:autopilot-skip
                '  *"--search label:autopilot-skip"*) echo 2;;\n'
                # #367: the open-count query now unions `_obligation_quals()`
                # (real gh issue-list arrays), not a bare "-q length" count.
                '  *) echo \'[{"number":1},{"number":2},{"number":3},'
                '{"number":4},{"number":5},{"number":6},{"number":7}]\';;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 7)
            self.assertEqual(cache["skipped"], 2)
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 7", seg)
            self.assertIn("skip 2", seg)

    def test_refresh_counts_skipped_for_own_slice(self):
        # Reduced authority: skipped = union of the SAME slice quals, but with
        # the POSITIVE label:autopilot-skip filter ({9} ∪ {9,10} = 2).
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  *"--search label:autopilot-skip assignee:@me"*) echo \'[{"number":9}]\';;\n'
                '  *"--search label:autopilot-skip author:@me"*) echo \'[{"number":9},{"number":10}]\';;\n'
                '  *"--search label:autopilot-skip label:stream:"*) echo "[]";;\n'
                '  *assignee:@me*) echo \'[{"number":1},{"number":2}]\';;\n'
                '  *author:@me*)   echo \'[{"number":2},{"number":3}]\';;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo 16;;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 3)
            self.assertEqual(cache["skipped"], 2)


class UserWaitingSegment(unittest.TestCase):
    """#468 — a `· U N` bucket for tickets parked on the USER's answer
    (`needs-answer`/`needs-decision`). They LEAVE the workable `I N` count (they
    are the user's responsibility, not this box's) and render orange-adjacent,
    hidden at 0, on BOTH scopes (a full box's core tickets and a sub-dev's own
    slice can both be parked on the user)."""

    def _seg(self, home, cwd):
        return statusbar.tickets_segment(cwd, home=home, spawn=False)

    def test_render_shows_user_waiting_when_positive(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=17, name="demo", user_waiting=2)
            seg = self._seg(home, cwd)
            self.assertIn("I 17", seg)
            self.assertIn("U 2", seg)

    def test_render_hides_user_waiting_at_zero_or_missing(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=12, name="demo", user_waiting=0)
            self.assertNotIn("U ", self._seg(home, cwd))
            # a legacy cache with NO user_waiting key must not crash + not render U
            _seed_cache(home, cwd, open_n=12, name="demo")
            seg = self._seg(home, cwd)
            self.assertIn("I 12", seg)
            self.assertNotIn("U ", seg)

    def test_render_combines_user_waiting_with_gk_and_skip(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=3, name="demo", gk=5, scope="mine",
                        skipped=2, user_waiting=4)
            seg = self._seg(home, cwd)
            self.assertIn("I 3", seg)
            self.assertIn("U 4", seg)
            self.assertIn("gk 5", seg)
            self.assertIn("skip 2", seg)

    def test_render_shows_user_waiting_on_a_full_authority_cache(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=8, name="demo", scope="core",
                        user_waiting=3)
            seg = self._seg(home, cwd)
            self.assertIn("I 8", seg)
            self.assertIn("U 3", seg)
            self.assertNotIn("gk", seg)   # full box has no gk bucket

    def test_refresh_full_authority_partitions_user_waiting_out_of_open(self):
        # The obligation union returns 5 rows; #4/#5 carry needs-answer/
        # needs-decision → open=3 (workable), user_waiting=2. ONE fetch, one
        # partition — the footer and the /goal stop-proof cannot disagree.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            FIVE = json.dumps([
                {"number": 1, "labels": [{"name": "bug"}]},
                {"number": 2, "labels": []},
                {"number": 3, "labels": [{"name": "enhancement"}]},
                {"number": 4, "labels": [{"name": "needs-answer"}]},
                {"number": 5, "labels": [{"name": "needs-decision"}]}])
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                "  *) echo '%s';;\n" % FIVE +
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 3, "user-waiting must leave I N")
            self.assertEqual(cache["user_waiting"], 2)
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 3", seg)
            self.assertIn("U 2", seg)

    def test_refresh_reduced_authority_partitions_user_waiting_out_of_open(self):
        # own-account slice {1,4} ∪ {2}; #4 needs-answer + #2 needs-decision are
        # user-waiting → open (own unhandled workable) = {1} = 1, user_waiting=2.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            fake_gh = Path(bindir) / "gh"
            A = json.dumps([{"number": 1, "labels": [{"name": "bug"}]},
                            {"number": 4, "labels": [{"name": "needs-answer"}]}])
            B = json.dumps([{"number": 2, "labels": [{"name": "needs-decision"}]}])
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  */timeline*) echo "[]";;\n'
                '  *"--search label:autopilot-skip"*) echo "[]";;\n'
                '  *assignee:@me*) echo \'%s\';;\n' % A +
                '  *author:@me*)   echo \'%s\';;\n' % B +
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo "kvaskodev";;\n'
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache.get("scope"), "mine")
            self.assertEqual(cache["open"], 1, "user-waiting must leave I N")
            self.assertEqual(cache["user_waiting"], 2)


class AutopilotProgressFeed(unittest.TestCase):
    """notify --run-card feeds ~/.claude/autopilot-progress/<repo>.json — done
    increments within one run window, resets after a ≥6h gap."""

    def setUp(self):
        import unittest.mock as m
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        p = m.patch.object(statusbar, "progress_dir",
                           lambda home=None: Path(self.home) / "autopilot-progress")
        p.start()
        self.addCleanup(p.stop)

    def _read(self, name):
        return json.loads((Path(self.home) / "autopilot-progress" /
                           (name + ".json")).read_text())

    def test_first_card_starts_at_one(self):
        airuleset._write_autopilot_progress("demo", 16)
        d = self._read("demo")
        self.assertEqual(d["done"], 1)
        self.assertEqual(d["remaining"], 16)

    def test_cards_increment_within_run_window(self):
        airuleset._write_autopilot_progress("demo", 16)
        airuleset._write_autopilot_progress("demo", 15)
        d = self._read("demo")
        self.assertEqual(d["done"], 2)
        self.assertEqual(d["remaining"], 15)

    def test_gap_starts_a_new_run(self):
        airuleset._write_autopilot_progress("demo", 5)
        p = Path(self.home) / "autopilot-progress" / "demo.json"
        old = json.loads(p.read_text())
        old["ts"] = int(time.time()) - statusbar.AUTOPILOT_RUN_WINDOW_S - 60
        p.write_text(json.dumps(old))
        airuleset._write_autopilot_progress("demo", 20)
        self.assertEqual(self._read("demo")["done"], 1)     # new run, not 2

    def test_non_int_remaining_keeps_previous(self):
        airuleset._write_autopilot_progress("demo", 9)
        airuleset._write_autopilot_progress("demo", None)   # gh count failed
        d = self._read("demo")
        self.assertEqual(d["done"], 2)
        self.assertEqual(d["remaining"], 9)

    def test_hostile_name_is_defanged(self):
        # separators stripped + leading dots removed → '../evil' lands as
        # 'evil.json', never a traversal or a hidden file
        airuleset._write_autopilot_progress("../evil", 1)
        files = [f.name for f in
                 (Path(self.home) / "autopilot-progress").glob("*")]
        self.assertEqual(files, ["evil.json"])


if __name__ == "__main__":
    unittest.main()


class SharedAccountSliceScoping(unittest.TestCase):
    """Montalu incident 2026-07-20: the montalu box's gh login is the SHARED
    zbynekdrlik account (scoped PAT), so author:@me matched every user-authored
    ticket and the footer showed foreign streams' numbers (open=20/skipped=26
    while his real slice is label:stream:montalu only). When the gh login is
    the maintainer account, the reduced slice = the stream LABEL alone; a
    stream with its OWN account (david/kvaskodev) keeps the @me union."""

    def _refresh(self, home, repo, bindir, login):
        fake_gh = Path(bindir) / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"api user"*) echo "%s";;\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
            '  *"search label:autopilot-skip"*) echo \'[{"number":9}]\';;\n'
            '  *"label:stream:"*) echo \'[{"number":1},{"number":2}]\';;\n'
            '  *assignee:@me*) echo \'[{"number":1},{"number":50},{"number":51}]\';;\n'
            '  *author:@me*)   echo \'[{"number":60},{"number":61},{"number":62}]\';;\n'
            '  *) echo 99;;\n'
            'esac\n' % login)
        fake_gh.chmod(0o755)
        r = subprocess.run(
            [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
             "tickets-status", "--refresh", "--cwd", repo],
            capture_output=True, text=True,
            env={**os.environ, "HOME": home,
                 "PATH": f"{bindir}:{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((statusbar.cache_dir(home) /
                           (statusbar.cwd_key(repo) + ".json")).read_text())

    def test_shared_login_counts_stream_label_only(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=branch-merge -->\n")
            cache = self._refresh(home, repo, bindir, "zbynekdrlik")
            self.assertEqual(cache["open"], 2)        # {1,2} — stream label only
            self.assertEqual(cache["skipped"], 1)     # {9}
            # the @me piles ({50,51},{60..62}) must NOT leak in
            self.assertNotEqual(cache["open"], 6)

    def test_own_account_login_keeps_me_union(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            cache = self._refresh(home, repo, bindir, "kvaskodev")
            # union {1,2} ∪ {1,50,51} ∪ {60,61,62} = 7
            self.assertEqual(cache["open"], 7)


class QuestionPingMerge(unittest.TestCase):
    """#512: the standalone `Q N` ❓ badge is REMOVED from the footer render;
    its count folds into `U N` ("waiting on the OWNER"), deduped so a ping that
    references a ticket (`#N` in its text — already counted in the label-based
    `user_waiting`) is NOT double-counted. The scoping (this-project-only,
    either-direction cwd containment) is preserved from the old badge. The
    question map + watchdog re-ask jobs are untouched — this is render/count
    only."""

    CWD = "/home/x/devel/demo"

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def _seed(self, entries):
        d = statusbar._claude_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def _count(self, cwd=None):
        return statusbar.question_ping_count(self.CWD if cwd is None else cwd,
                                             home=self.home)

    def test_counts_only_this_projects_ticketless_pings(self):
        # A question recorded against a DIFFERENT project is invisible here
        # (it pinged its own project's phone). Same project = counted.
        self._seed({"1": {"cwd": self.CWD, "block": "co s tym", "ts": 1},
                    "2": {"cwd": self.CWD, "block": "a s tamtym", "ts": 2},
                    "3": {"cwd": "/home/x/devel/other", "block": "iny", "ts": 3}})
        self.assertEqual(self._count(), 2)

    def test_a_ping_referencing_a_ticket_is_not_counted_dedup(self):
        # #512 dedup: a ping whose text carries a `#N` ticket reference is
        # assumed already in the label-based U count (its ticket carries a
        # needs-answer/needs-decision/needs-acceptance label), so it must NOT
        # be counted here — only the ticketless session question is.
        self._seed({
            "1": {"cwd": self.CWD, "block": "otazka bez ticketu", "ts": 1},
            "2": {"cwd": self.CWD, "block": "otazka k ticketu #742", "ts": 2},
            "3": {"cwd": self.CWD, "question": "kratka o #900", "ts": 3},
        })
        self.assertEqual(self._count(), 1)

    def test_foreign_project_pings_count_zero(self):
        self._seed({"3": {"cwd": "/home/x/devel/other", "block": "iny", "ts": 3}})
        self.assertEqual(self._count(), 0)

    def test_zero_when_map_missing_or_empty(self):
        self.assertEqual(self._count(), 0)
        self._seed({})
        self.assertEqual(self._count(), 0)

    def test_garbage_entries_are_safe(self):
        self._seed({"1": "not-a-dict", "2": {"cwd": self.CWD, "block": "ok", "ts": 1}})
        self.assertEqual(self._count(), 1)

    def test_subdir_and_trailing_slash_containment(self):
        # Either-direction containment (montalu launch-dir vs recorded subdir).
        self._seed({"1": {"cwd": self.CWD + "/subrepo", "block": "sub", "ts": 1}})
        self.assertEqual(self._count(), 1)
        self._seed({"1": {"cwd": self.CWD, "block": "parent", "ts": 1}})
        self.assertEqual(self._count(cwd=self.CWD + "/subrepo"), 1)

    def test_tickets_segment_folds_pings_into_U(self):
        # The footer render combines the cache's label-based user_waiting with
        # the LIVE ticketless ping count into ONE `U N`.
        cwd = self.CWD
        _seed_cache(self.home, cwd, open_n=3, name="demo", user_waiting=2)
        self._seed({"1": {"cwd": cwd, "block": "otazka bez ticketu", "ts": 1}})
        seg = statusbar.tickets_segment(cwd, home=self.home, spawn=False)
        self.assertIn("I 3", seg)
        self.assertIn("U 3", seg)          # 2 labels + 1 ticketless ping

    def test_tickets_segment_shows_U_from_pings_alone(self):
        # A fresh ❓ with ZERO user-waiting labels still surfaces `U 1`.
        cwd = self.CWD
        _seed_cache(self.home, cwd, open_n=5, name="demo", user_waiting=0)
        self._seed({"1": {"cwd": cwd, "block": "len ping", "ts": 1}})
        seg = statusbar.tickets_segment(cwd, home=self.home, spawn=False)
        self.assertIn("U 1", seg)

    def test_tickets_segment_hides_U_when_labels_and_pings_both_zero(self):
        cwd = self.CWD
        _seed_cache(self.home, cwd, open_n=5, name="demo", user_waiting=0)
        self._seed({})
        self.assertNotIn("U ", statusbar.tickets_segment(cwd, home=self.home, spawn=False))

    def test_ping_stays_visible_when_open_is_none_gh_error(self):
        # #512 review (both adversaries, live-reproduced): the old `Q` badge was
        # gh-INDEPENDENT. On a gh error / non-repo cwd / SliceUnresolved the cache
        # carries open=None (and user_waiting=None), so `tickets_segment` used to
        # return "" and a pending ❓ vanished from the footer. It must still
        # surface a STANDALONE `U N` from the live pings.
        cwd = self.CWD
        _seed_cache(self.home, cwd, open_n=None, name="demo")  # gh-failure cache
        self._seed({"1": {"cwd": cwd, "block": "otazka bez ticketu", "ts": 1}})
        seg = statusbar.tickets_segment(cwd, home=self.home, spawn=False)
        self.assertIn("U 1", seg)
        self.assertNotIn("I ", seg)          # no renderable I count during a gh error
        self.assertNotIn("· U", seg)         # standalone U, no leading "· "

    def test_ping_stays_visible_with_no_tickets_cache(self):
        # A cold/first-render session with no tickets cache yet must still show a
        # pending ❓ (the old badge rendered from the local map with no cache).
        cwd = self.CWD
        self._seed({"1": {"cwd": cwd, "block": "otazka bez ticketu", "ts": 1}})
        seg = statusbar.tickets_segment(cwd, home=self.home, spawn=False)
        self.assertIn("U 1", seg)

    def test_no_cache_and_no_pings_renders_nothing(self):
        cwd = self.CWD
        self._seed({})
        self.assertEqual(statusbar.tickets_segment(cwd, home=self.home, spawn=False), "")

    def test_shim_no_longer_renders_a_standalone_Q_segment(self):
        # #512: the Q ❓ badge is gone from the render — the shim must not call
        # a standalone questions_segment; the pings fold into U via
        # tickets_segment instead.
        import airuleset
        self.assertNotIn("questions_segment", airuleset.CAVEMAN_SHIM_CONTENT)
        self.assertFalse(hasattr(statusbar, "questions_segment"),
                         "questions_segment is dead after #512 (folded into U)")


class ContextCostSegment(unittest.TestCase):
    """'ctx <size> ~$<cost>' (was 'ctx <size> · ~$<cost>/ťah' before the
    footer's labels were shortened, #223) — the CURRENT turn's context size
    + its real dollar cost (2026-07-25 cost-fix package). Source: the statusline
    stdin payload's `context_window.current_usage` (the exact token
    breakdown of the LAST billed API call) + `model.id`, priced via the
    SAME per-Mtok table `burn` uses. Colour-escalates on RAW token count:
    green <150K, yellow 150-400K, red >400K."""

    def _payload(self, model_id, i=0, cw=0, cr=0, o=0, transcript_path=None):
        d = {"model": {"id": model_id}}
        if i or cw or cr or o:
            d["context_window"] = {"current_usage": {
                "input_tokens": i, "cache_creation_input_tokens": cw,
                "cache_read_input_tokens": cr, "output_tokens": o}}
        if transcript_path is not None:
            d["transcript_path"] = transcript_path
        return d

    def test_green_below_150k(self):
        seg = statusbar.context_cost_segment(self._payload("claude-opus-5", cr=50000))
        self.assertIn("\033[38;5;40m", seg)
        self.assertIn("ctx 50K", seg)

    def test_yellow_150k_to_400k(self):
        seg = statusbar.context_cost_segment(self._payload("claude-opus-5", cr=200000))
        self.assertIn("\033[38;5;220m", seg)
        self.assertIn("ctx 200K", seg)

    def test_red_above_400k(self):
        seg = statusbar.context_cost_segment(self._payload("claude-opus-5", cr=500000))
        self.assertIn("\033[38;5;196m", seg)
        self.assertIn("ctx 500K", seg)

    def test_cost_uses_the_burn_price_table(self):
        # fable cache_read $1.0/Mtok * 570,000 = $0.57 exactly — the example
        # in the cost-fix package's own spec.
        seg = statusbar.context_cost_segment(
            self._payload("claude-fable-5[1m]", cr=570000))
        self.assertIn("ctx 570K", seg)
        self.assertIn("$0.57", seg)

    def test_compaction_turn_prices_at_cache_read_rate_not_write_rate(self):
        # LIVE BUG (2026-07-25, gatekeeper): right after a compaction the LAST
        # billed call has a huge cache_creation (a full context re-write) and
        # a tiny cache_read — pricing the ACTUAL per-call mix showed
        # 'ctx 175K · ~$1.10/ťah' (priced mostly at Opus's cache-WRITE rate,
        # $6.25/Mtok) when the STEADY-STATE cost of carrying 175K forward is
        # ctx * cache-READ rate ($0.50/Mtok) = 175000*0.5/1e6 = ~$0.09. The
        # estimate must reflect what an ORDINARY turn pays to resend this
        # context, never what one freak compaction/cache-miss turn billed.
        seg = statusbar.context_cost_segment(
            self._payload("claude-opus-5", cw=170000, cr=5000, o=2000))
        self.assertIn("ctx 175K", seg)
        self.assertIn("$0.09", seg)

    def test_empty_on_missing_or_garbage_data(self):
        self.assertEqual(statusbar.context_cost_segment({}), "")
        self.assertEqual(statusbar.context_cost_segment(None), "")
        self.assertEqual(statusbar.context_cost_segment("garbage"), "")

    def test_empty_on_unknown_model_tier(self):
        seg = statusbar.context_cost_segment(
            self._payload("some-other-vendor-model", cr=50000))
        self.assertEqual(seg, "")

    def test_falls_back_to_transcript_tail_when_context_window_missing(self):
        with TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            with open(path, "w") as fh:
                fh.write(json.dumps({"type": "system"}) + "\n")
                fh.write(json.dumps({"message": {
                    "model": "claude-sonnet-5",
                    "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 100000,
                              "output_tokens": 500}}}) + "\n")
                fh.write(json.dumps({"type": "other-no-usage"}) + "\n")
            seg = statusbar.context_cost_segment({"transcript_path": path})
            self.assertIn("ctx 100K", seg)

    def test_shim_renders_the_context_cost_segment(self):
        self.assertIn("context_cost_segment", airuleset.CAVEMAN_SHIM_CONTENT)

    def test_show_cost_false_drops_the_dollar_suffix(self):
        # #313 pt 4: the width-budget trim's last-resort shortening -- keep
        # the size, drop only the '~$<cost>' tail.
        full = statusbar.context_cost_segment(
            self._payload("claude-opus-5", cr=50000))
        short = statusbar.context_cost_segment(
            self._payload("claude-opus-5", cr=50000), show_cost=False)
        self.assertIn("ctx 50K", short)
        self.assertNotIn("~$", short)
        self.assertIn("~$", full)
        self.assertLess(statusbar.visible_len(short), statusbar.visible_len(full))
        self.assertIn("\033[38;5;40m", short)   # same colour as the full form


class WidthBudget(unittest.TestCase):
    """#313 pt 4: fit the statusline inside the pane width MINUS a reserve
    for Claude Code's own right-edge indicators (live evidence: a 176-col
    row fully consumed truncated the armed-'/goal' glyph clean off, twice
    misread as "the goal died"). `pane_width()` is the one live input
    (tmux); `fit_statusline()` is the pure trimming logic, tested here with
    synthetic segments and no tmux/subprocess involved at all."""

    def test_visible_len_ignores_ansi_codes(self):
        self.assertEqual(statusbar.visible_len("\033[38;5;40mI 5\033[0m"), 3)
        self.assertEqual(statusbar.visible_len(""), 0)
        self.assertEqual(statusbar.visible_len(None), 0)

    def test_pane_width_none_without_tmux_pane(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMUX_PANE", None)
            self.assertIsNone(statusbar.pane_width())

    def test_pane_width_reads_the_injected_runner_never_the_real_tmux(self):
        # A test must NEVER let this call the REAL tmux binary -- this box's
        # own live $TMUX_PANE would make the query genuinely succeed,
        # sizing the render non-deterministically against whatever pane the
        # TEST happens to run in.
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return unittest.mock.Mock(returncode=0, stdout="176\n")

        with unittest.mock.patch.dict(os.environ, {"TMUX_PANE": "%3"}):
            self.assertEqual(statusbar.pane_width(run=fake_run), 176)
        self.assertEqual(calls[0][:3], ["tmux", "display-message", "-p"])

    def test_pane_width_none_on_a_failed_call(self):
        def fake_run(argv, **kw):
            raise OSError("no tmux binary")
        with unittest.mock.patch.dict(os.environ, {"TMUX_PANE": "%3"}):
            self.assertIsNone(statusbar.pane_width(run=fake_run))

    def test_pane_width_none_on_nonzero_exit_or_garbage_stdout(self):
        def bad_rc(argv, **kw):
            return unittest.mock.Mock(returncode=1, stdout="176\n")

        def garbage(argv, **kw):
            return unittest.mock.Mock(returncode=0, stdout="not-a-number\n")

        with unittest.mock.patch.dict(os.environ, {"TMUX_PANE": "%3"}):
            self.assertIsNone(statusbar.pane_width(run=bad_rc))
            self.assertIsNone(statusbar.pane_width(run=garbage))

    def test_fit_statusline_untrimmed_when_it_fits(self):
        segs = ["\033[38;5;75mI 5\033[0m"]
        line = statusbar.fit_statusline(segs, "email sub", "cm", "", "", 999)
        self.assertIn("I 5", line)
        self.assertIn("email sub", line)
        self.assertIn("cm", line)

    def test_fit_statusline_none_width_never_trims(self):
        segs = ["I 5"]
        line = statusbar.fit_statusline(segs, "email sub", "cm", "", "", None)
        self.assertIn("email sub", line)
        self.assertIn("cm", line)

    def test_shim_clamps_the_budget_at_zero_for_a_measured_width_this_small(self):
        # #313 pt 4 adversarial review round 2, THEORETICAL F7: `width` is
        # `is not None` (measured), so a genuinely tiny width no longer
        # falls into the "unmeasurable, never trim" branch (round-1 fix)
        # -- but an UNCLAMPED `width - RESERVE` could still go negative,
        # which `fit_statusline` would then treat as an already-overflowed
        # budget. Locks that the shim clamps it at 0 instead of computing a
        # bare, possibly-negative subtraction.
        self.assertIn("max(0, width - statusbar.STATUSLINE_RESERVE_COLS)",
                       airuleset.CAVEMAN_SHIM_CONTENT)

    def test_fit_statusline_drops_identity_first(self):
        segs = ["I 5"]
        identity = "drlik.marek@gmail.com sub 12.8.(4d)"
        # width fits segs+cm but not segs+identity+cm
        width = statusbar.visible_len("  ".join(["I 5", "cm"]))
        line = statusbar.fit_statusline(segs, identity, "cm", "", "", width)
        self.assertIn("I 5", line)
        self.assertIn("cm", line)
        self.assertNotIn("drlik.marek", line)

    def test_fit_statusline_drops_caveman_tag_next(self):
        segs = ["I 5"]
        identity = "drlik.marek@gmail.com sub 12.8.(4d)"
        width = statusbar.visible_len("I 5")   # only the core segment fits
        line = statusbar.fit_statusline(segs, identity, "cm", "", "", width)
        self.assertIn("I 5", line)
        self.assertNotIn("cm", line)
        self.assertNotIn("drlik.marek", line)

    def test_fit_statusline_shortens_ctx_as_last_resort(self):
        ctx_full = "ctx 50K ~$0.05"
        ctx_short = "ctx 50K"
        segs = ["I 5", ctx_full]
        # width fits everything except identity/cm AND fits the SHORT ctx
        # form but not the full one alongside "I 5"
        width = statusbar.visible_len("  ".join(["I 5", ctx_short]))
        line = statusbar.fit_statusline(segs, "email sub", "cm",
                                        ctx_full, ctx_short, width)
        self.assertIn("I 5", line)
        self.assertIn(ctx_short, line)
        self.assertNotIn("~$", line)
        self.assertNotIn("email sub", line)
        self.assertNotIn("cm", line)

    def test_fit_statusline_gives_up_gracefully_when_nothing_fits(self):
        # Even the core segments alone don't fit -- returns the smallest
        # composed form it has (never raises, never returns garbage).
        segs = ["a very very very long core segment that does not fit"]
        line = statusbar.fit_statusline(segs, "identity", "cm", "", "", 3)
        self.assertIsInstance(line, str)


class ModelSegment(unittest.TestCase):
    """The session-model identity segment (#133): a short alias of the
    CURRENT session's model (fable/opus/sonnet/haiku), source
    `payload["model"]["id"]` (fallback `display_name`) -- the same field
    `context_cost_segment` already reads, never guessed from config.
    Highlighted (yellow) when it differs from this box's MANAGED_MODEL
    default; green when it matches or the comparison is unresolvable
    (never a false alarm). Compares via `burn.tier()`, never a raw string
    equality -- MANAGED_MODEL's `[1m]` launch-flag suffix never appears in
    what a session reports back for its own model id."""

    def test_matches_managed_model_renders_green(self):
        seg = statusbar.model_segment({"model": {"id": "claude-opus-5"}},
                                       managed_model="claude-opus-5[1m]")
        self.assertIn("\033[38;5;40m", seg)
        self.assertIn("opus", seg)

    def test_mismatch_renders_yellow_highlight(self):
        seg = statusbar.model_segment({"model": {"id": "claude-sonnet-5"}},
                                       managed_model="claude-opus-5[1m]")
        self.assertIn("\033[38;5;220m", seg)
        self.assertIn("sonnet", seg)

    def test_suffix_agnostic_managed_comparison(self):
        # The exact regression the removed watchdog job 23 hit (#132):
        # MANAGED_MODEL carries a launch-flag suffix that never appears in
        # what the session itself reports -- a bare string compare would be
        # permanently mismatched. Comparing via tier() must not repeat that.
        seg = statusbar.model_segment({"model": {"id": "claude-opus-5"}},
                                       managed_model="claude-opus-5[1m]")
        self.assertIn("\033[38;5;40m", seg)

    def test_empty_on_missing_model_field(self):
        self.assertEqual(statusbar.model_segment({}), "")
        self.assertEqual(statusbar.model_segment(None), "")
        self.assertEqual(statusbar.model_segment("garbage"), "")

    def test_empty_on_non_dict_model_field(self):
        self.assertEqual(statusbar.model_segment({"model": "opus"}), "")

    def test_empty_on_unknown_model_tier(self):
        seg = statusbar.model_segment(
            {"model": {"id": "some-other-vendor-model"}},
            managed_model="claude-opus-5[1m]")
        self.assertEqual(seg, "")

    def test_falls_back_to_display_name_when_id_missing(self):
        seg = statusbar.model_segment(
            {"model": {"display_name": "Fable"}}, managed_model="claude-opus-5[1m]")
        self.assertIn("fable", seg)

    def test_unresolvable_managed_model_never_false_alarms(self):
        # An explicit empty override (mirrors a failed lazy `import airuleset`)
        # must render as a plain match, never a manufactured mismatch.
        seg = statusbar.model_segment({"model": {"id": "claude-sonnet-5"}},
                                       managed_model="")
        self.assertIn("\033[38;5;40m", seg)
        self.assertIn("sonnet", seg)

    def test_label_is_the_bare_tier_word_no_prefix(self):
        # #223 fits-on-one-line discipline: no redundant "m " label -- the
        # tier word alone is already the shortest possible spelling.
        seg = statusbar.model_segment({"model": {"id": "claude-opus-5"}},
                                       managed_model="claude-opus-5[1m]")
        self.assertEqual(seg, "\033[38;5;40mopus\033[0m")

    def test_default_managed_model_resolves_via_real_airuleset_constant(self):
        # No managed_model override -- resolves airuleset.MANAGED_MODEL via
        # a lazy import (no module-level `import statusbar` in airuleset.py,
        # so this is not a real circular import).
        seg = statusbar.model_segment({"model": {"id": airuleset.MANAGED_MODEL}})
        self.assertIn("\033[38;5;40m", seg)

    def test_shim_renders_the_model_segment(self):
        self.assertIn("model_segment", airuleset.CAVEMAN_SHIM_CONTENT)

    def test_non_string_model_id_does_not_crash(self):
        # Adversarial review MAJOR-1: a truthy non-string `id` (int/float/
        # list/dict/bool) reached burn.tier()'s `.lower()` uncaught -- a
        # crash here, being first in the shim's shared try block, silently
        # deleted 5 unrelated working segments (sub/tickets/questions/ctx/
        # account-email). model_id must be coerced to str before tiering.
        for hostile_id in (123, 4.5, [1, 2], {"nested": "opus"}, True):
            seg = statusbar.model_segment({"model": {"id": hostile_id}},
                                           managed_model="claude-opus-5[1m]")
            self.assertIsInstance(seg, str)

    def test_non_string_display_name_does_not_crash(self):
        seg = statusbar.model_segment({"model": {"display_name": 9}},
                                       managed_model="claude-opus-5[1m]")
        self.assertIsInstance(seg, str)

    def test_unrecognized_managed_model_never_false_alarms(self):
        # Adversarial review MAJOR-2: burn.tier() returns "other" for an
        # unrecognized MANAGED_MODEL (a future alias, or any string with no
        # tier word). The session's own "other" tier already renders "" --
        # but the MANAGED side let "other" stand in as a real tier and
        # compared it, permanently mismatching (a manufactured false alarm)
        # even though the comparison is genuinely unresolvable. Not firing
        # today (MANAGED_MODEL="claude-opus-5[1m]" tiers to "opus"), but a
        # regression waiting for the next MANAGED_MODEL value.
        for unrecognized in ("default", "gpt-5", "claude-5-titan"):
            seg = statusbar.model_segment(
                {"model": {"id": "claude-opus-5"}}, managed_model=unrecognized)
            self.assertIn("\033[38;5;40m", seg,
                           "managed_model=%r must never manufacture a "
                           "mismatch" % unrecognized)

    def test_managed_model_lazy_import_failure_falls_back_to_no_alarm(self):
        # Adversarial review MINOR-3: _managed_model()'s try/except had zero
        # coverage -- the import always succeeds in the test environment.
        # Force the failure and confirm the fail-safe path still holds.
        import builtins
        real_import = builtins.__import__

        def _hostile_import(name, *a, **kw):
            if name == "airuleset":
                raise ImportError("simulated: airuleset unimportable")
            return real_import(name, *a, **kw)

        with unittest.mock.patch("builtins.__import__", side_effect=_hostile_import):
            self.assertIsNone(statusbar._managed_model())
            seg = statusbar.model_segment({"model": {"id": "claude-sonnet-5"}})
        self.assertIn("\033[38;5;40m", seg)
        self.assertIn("sonnet", seg)


class FmtTokens(unittest.TestCase):
    def test_formats_thousands_and_millions(self):
        self.assertEqual(statusbar._fmt_tokens(999), "999")
        self.assertEqual(statusbar._fmt_tokens(1000), "1K")
        self.assertEqual(statusbar._fmt_tokens(570000), "570K")
        self.assertEqual(statusbar._fmt_tokens(1500000), "1.5M")


def _write_claude_json(home, data):
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / ".claude.json").write_text(json.dumps(data))


class SubscriptionSegment(unittest.TestCase):
    """'sub <D.M.>(<Nd>)' -- the monthly subscription-renewal anchor of the
    Claude account logged in on THIS box (#223). Source:
    ~/.claude.json -> oauthAccount.subscriptionCreatedAt; the renewal is
    the NEXT occurrence of that day-of-month at/after today, clamped for
    short months (31 -> the month's last day). Fails silently on any
    missing/malformed input -- a statusline segment must never raise."""

    def test_renders_days_until_next_anniversary(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": "2026-01-12T16:34:03.439322Z"}})
            now = datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp()
            seg = statusbar.subscription_segment(home=home, now=now)
            self.assertIn("sub 12.8.(8d)", seg)

    def test_renewal_today_renders_zero_days_and_red(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": "2026-01-12T16:34:03Z"}})
            now = datetime(2026, 8, 12, 10, tzinfo=timezone.utc).timestamp()
            seg = statusbar.subscription_segment(home=home, now=now)
            self.assertIn("sub 12.8.(0d)", seg)
            self.assertIn("38;5;196m", seg)             # red on the last day

    def test_renewal_far_away_renders_green(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": "2026-01-12T00:00:00Z"}})
            now = datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp()
            seg = statusbar.subscription_segment(home=home, now=now)
            self.assertIn("38;5;40m", seg)

    def test_short_month_clamps_the_day(self):
        # anniversary day-of-month 31; the current month (Feb 2026) only has
        # 28 days -- clamp to the 28th, never crash / overflow into March.
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": "2026-01-31T00:00:00Z"}})
            now = datetime(2026, 2, 5, tzinfo=timezone.utc).timestamp()
            seg = statusbar.subscription_segment(home=home, now=now)
            self.assertIn("sub 28.2.", seg)

    def test_missing_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_missing_oauth_account_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {})
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_missing_subscription_created_at_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {"emailAddress": "x@y.z"}})
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_unparseable_date_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": "not-a-date"}})
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_garbage_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            Path(home).mkdir(parents=True, exist_ok=True)
            (Path(home) / ".claude.json").write_text("not json at all")
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_non_dict_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, [1, 2, 3])
            self.assertEqual(statusbar.subscription_segment(home=home), "")

    def test_never_raises_on_hostile_nested_input(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "subscriptionCreatedAt": {"nested": "garbage"}}})
            self.assertEqual(statusbar.subscription_segment(home=home), "")


class AccountEmailSegment(unittest.TestCase):
    """The Claude account's login email (~/.claude.json ->
    oauthAccount.emailAddress, #223) -- WHICH account this box is logged in
    as. #313 pt 6: rendered in a READABLE color (the SGR dim attribute used
    to make it near-invisible on many real terminals). Fails silently on any
    missing/malformed input."""

    def test_renders_the_email_readable(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {
                "emailAddress": "drlik.marek@gmail.com"}})
            seg = statusbar.account_email_segment(home=home)
            self.assertIn("drlik.marek@gmail.com", seg)
            self.assertNotIn("\033[2m", seg)     # no longer the dim attribute
            self.assertIn("\033[38;5;250m", seg)  # a real, readable colour

    def test_missing_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            self.assertEqual(statusbar.account_email_segment(home=home), "")

    def test_missing_oauth_account_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {})
            self.assertEqual(statusbar.account_email_segment(home=home), "")

    def test_non_string_email_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, {"oauthAccount": {"emailAddress": 12345}})
            self.assertEqual(statusbar.account_email_segment(home=home), "")

    def test_garbage_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            Path(home).mkdir(parents=True, exist_ok=True)
            (Path(home) / ".claude.json").write_text("{not json")
            self.assertEqual(statusbar.account_email_segment(home=home), "")

    def test_non_dict_claude_json_is_silent(self):
        with TemporaryDirectory() as home:
            _write_claude_json(home, "just a string")
            self.assertEqual(statusbar.account_email_segment(home=home), "")


class GraphqlBudgetGuard(unittest.TestCase):
    """#370: the statusline refresh is the fleet's dominant periodic gh
    consumer — ~5 GraphQL calls/box every 120s, ALL into ONE shared 5000/h
    graphql bucket (measured: `gh issue list`/`gh repo view` are `POST
    /graphql`). With no rate-awareness, a box near the shared limit keeps
    refreshing and pushes the budget to 0, breaking the FUNCTIONAL calls
    (`gh issue create`, autopilot, run-cards) on EVERY box. The refresh must
    YIELD when the shared graphql budget is low, checked via the FREE
    `gh api rate_limit` endpoint (zero quota cost)."""

    @staticmethod
    def _rl(remaining):
        return ('{"resources":{"graphql":{"remaining":%d,"limit":5000},'
                '"core":{"remaining":4999,"limit":5000}}}' % remaining)

    def _runner(self, remaining):
        return lambda *a, **k: self._rl(remaining)

    def test_budget_ok_true_above_floor_false_below(self):
        self.assertEqual(
            airuleset._graphql_budget_ok(1000, runner=self._runner(4000)),
            (True, 4000))
        self.assertEqual(
            airuleset._graphql_budget_ok(1000, runner=self._runner(200)),
            (False, 200))
        # boundary: exactly AT the floor is still ok (>=)
        self.assertEqual(
            airuleset._graphql_budget_ok(1000, runner=self._runner(1000)),
            (True, 1000))

    def test_budget_ok_fails_open_when_unmeasurable(self):
        # A probe that errors / returns junk must NOT block the refresh — the
        # guard only ever skips on POSITIVE evidence of a low budget (pure
        # additive; identical to today's behaviour when the budget cannot be
        # read). rate_limit is not itself rate-limited, so a probe failure is a
        # genuine connectivity/auth error where the expensive calls would fail
        # too and the existing error path already handles it.
        for junk in ("", "not json", "[]", '{"resources":{}}',
                     '{"resources":{"graphql":{}}}'):
            ok, rem = airuleset._graphql_budget_ok(
                1000, runner=lambda *a, **k: junk)
            self.assertTrue(ok, junk)
            self.assertIsNone(rem, junk)

    def test_refresh_skips_expensive_calls_when_budget_low(self):
        # Under a low shared budget the refresh makes ZERO issue/repo GraphQL
        # calls and leaves the existing (stale) cache untouched — the footer
        # serves the last-known counts instead of blanking or draining the
        # last of the shared budget on a cosmetic read.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            _seed_cache(home, repo, open_n=42, name="demo")   # last-known
            marker = Path(bindir) / "EXPENSIVE_CALLED"
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api rate_limit"*) echo '
                "'" + self._rl(150) + "';;\n"
                '  *) touch "%s"; echo "SHOULD_NOT_BE_CALLED";;\n'
                'esac\n' % marker)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(
                marker.exists(),
                "refresh made an expensive GraphQL call under low budget")
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 42, "stale cache must be preserved")
            self.assertIn("budget", r.stdout.lower())

    def test_refresh_proceeds_when_budget_healthy(self):
        # Regression guard: a HEALTHY budget must not change the happy path —
        # the refresh runs the real queries and rewrites the cache as before.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            SEVEN = '[{"number":1},{"number":2},{"number":3},{"number":4},' \
                    '{"number":5},{"number":6},{"number":7}]'
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api rate_limit"*) echo '
                "'" + self._rl(4900) + "';;\n"
                '  *"repo view"*) echo "zbynekdrlik/demo";;\n'
                "  *) echo '" + SEVEN + "';;\n"
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("open=7", r.stdout)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 7)
            self.assertEqual(cache["name"], "demo")

    def test_refresh_skips_reduced_authority_path_too_when_budget_low(self):
        # The guard sits BEFORE the full-vs-reduced authority split, so a
        # reduced-authority (sub-dev stream) box must yield identically. This
        # covers the branch the full-authority skip test cannot reach.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            _seed_cache(home, repo, open_n=9, name="demo", scope="mine")
            marker = Path(bindir) / "EXPENSIVE_CALLED"
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"api rate_limit"*) echo '
                "'" + self._rl(150) + "';;\n"
                '  *) touch "%s"; echo "SHOULD_NOT_BE_CALLED";;\n'
                'esac\n' % marker)
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(
                marker.exists(),
                "reduced-authority refresh made a GraphQL call under low budget")
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 9, "stale slice cache must be preserved")
            self.assertIn("budget", r.stdout.lower())

    def test_gh_graphql_floor_default_and_env_override(self):
        import unittest.mock as m
        # No override -> the documented default.
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_GH_GRAPHQL_FLOOR", None)
            self.assertEqual(airuleset._gh_graphql_floor(),
                             airuleset.GH_GRAPHQL_REFRESH_FLOOR)
        # A valid override wins.
        with m.patch.dict(os.environ, {"AIRULESET_GH_GRAPHQL_FLOOR": "500"}):
            self.assertEqual(airuleset._gh_graphql_floor(), 500)
        # 0 is a deliberate, honoured disable (never rewritten to the default).
        with m.patch.dict(os.environ, {"AIRULESET_GH_GRAPHQL_FLOOR": "0"}):
            self.assertEqual(airuleset._gh_graphql_floor(), 0)
        # Non-numeric and negative fall back to the default rather than
        # silently disabling the guard.
        for bad in ("", "abc", "-5", "1.5"):
            with m.patch.dict(os.environ,
                              {"AIRULESET_GH_GRAPHQL_FLOOR": bad}):
                self.assertEqual(airuleset._gh_graphql_floor(),
                                 airuleset.GH_GRAPHQL_REFRESH_FLOOR, bad)


class ObligationCountSafeguard(unittest.TestCase):
    """#478 — obligation_count is goal_dark_watch's auto-re-arm gate. It must
    return the WORKABLE `open` count (which the cache computes as
    len(workable_rows) - gk via airuleset._partition_user_waiting) and NEVER
    fold the user-waiting (U-bucket) tickets back in — a user-waiting-only
    backlog must read as open==0 so a dead /goal loop is never re-armed for
    work only the user can unblock."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.cwd = "/home/x/devel/oblig"

    def test_open_is_the_workable_count_excluding_user_waiting(self):
        _seed_cache(self.home, self.cwd, open_n=0, user_waiting=3, ts=1500)
        open_n, ts = statusbar.obligation_count(self.cwd, home=self.home)
        self.assertEqual(open_n, 0, "a user-waiting-only backlog reads open==0")
        self.assertEqual(ts, 1500)

    def test_positive_workable_open_is_reported_with_its_ts(self):
        _seed_cache(self.home, self.cwd, open_n=5, user_waiting=2, ts=1500)
        self.assertEqual(statusbar.obligation_count(self.cwd, home=self.home),
                         (5, 1500))

    def test_absent_or_null_open_reads_as_none(self):
        self.assertEqual(statusbar.obligation_count(self.cwd, home=self.home),
                         (None, None))                 # no cache at all
        _seed_cache(self.home, self.cwd, open_n=None, user_waiting=1)
        self.assertEqual(statusbar.obligation_count(self.cwd, home=self.home),
                         (None, None))                 # open is null


if __name__ == "__main__":
    unittest.main()
