"""Statusline 🎫 ticket segment (statusbar.py + tickets-status CLI + run-card
progress feed).

The user's ask: the bottom status bar (next to the ctx/limit meters) shows
autopilot progress "done/total" during a run, else the count of open GitHub
issues. Hard rule: the statusline render NEVER blocks on gh — it reads local
caches and refreshes them via a detached background command.
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar


def _seed_cache(home, cwd, open_n=None, name="", ts=None, gk=None, scope=None,
                skipped=None):
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
        self.assertIn("Issues 14", self._seg())

    def test_autopilot_progress_wins_when_fresh(self):
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        _seed_progress(self.home, "demo", done=3, remaining=14)
        self.assertIn("Issues 3/17", self._seg())

    def test_stale_progress_falls_back_to_open_count(self):
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        _seed_progress(self.home, "demo", done=3, remaining=14,
                       ts=time.time() - statusbar.AUTOPILOT_RUN_WINDOW_S - 60)
        self.assertIn("Issues 14", self._seg())

    def test_backlog_empty_renders_green(self):
        _seed_cache(self.home, self.cwd, open_n=0, name="demo")
        _seed_progress(self.home, "demo", done=17, remaining=0)
        seg = self._seg()
        self.assertIn("Issues 17/17", seg)
        self.assertIn("38;5;40m", seg)          # green

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
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = repo ]; then echo "zbynekdrlik/demo"; else echo 7; fi\n')
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
            self.assertIn("Issues 7", statusbar.tickets_segment(repo, home=home,
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
            self.assertIn("Issues 3", statusbar.tickets_segment(repo, home=home,
                                                                spawn=False))

    def test_scoped_render_splits_active_vs_gk_bucket(self):
        # Gatekeeper follow-up (2026-07-11): the sub-dev slice renders TWO numbers —
        # active-on-me vs already handed off to the gatekeeper ("aby bolo jasne ze
        # dalsie tickety su uz preradene na gatekeeper"). Format: "Issues 1 · gk 5".
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=1, name="demo", gk=5, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("Issues 1", seg)
            self.assertIn("gk 5", seg)

    def test_scoped_render_zero_active_still_shows_gk(self):
        # David's expected live state: "Issues 0 · gk 5" — nothing active, 5 waiting.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=0, name="demo", gk=5, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("Issues 0", seg)
            self.assertIn("gk 5", seg)

    def test_scoped_render_gk_zero_is_still_shown(self):
        # gk=0 MUST render too ("Issues 4 · gk 0"): hiding the zero bucket looks
        # exactly like a broken/regressed counter — the user panicked when the
        # gatekeeper returned tickets (labels off → gk 0 → "gk" vanished):
        # "zase tam ukazuje issues 6 a ziadne gk N!!!" (2026-07-11). On a scoped
        # box the split is ALWAYS visible so it's clear the mechanism lives.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=4, name="demo", gk=0, scope="mine")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("Issues 4", seg)
            self.assertIn("gk 0", seg)

    def test_full_authority_render_has_no_gk(self):
        # A full box's cache has no gk key → plain single number, never "gk".
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=4, name="demo")
            seg = statusbar.tickets_segment(cwd, home=home, spawn=False)
            self.assertIn("Issues 4", seg)
            self.assertNotIn("gk", seg)

    def test_refresh_partitions_slice_by_ready_for_review_label(self):
        # The gk bucket = own-slice tickets carrying the ready-for-review label
        # (auto-labeled at the sub-dev hand-off by subdev-handoff-label.yml, PR #1420).
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
                # union {1 (no label), 2 (r4r), 3 (r4r)} → open=1 active, gk=2
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
            self.assertEqual(cache["open"], 1)      # active on the sub-dev
            self.assertEqual(cache["gk"], 2)        # handed off, waiting on gatekeeper
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("Issues 1", seg)
            self.assertIn("gk 2", seg)

    def test_refresh_full_authority_excludes_other_streams_labels(self):
        # Stream-label ownership (odoo-erp PR #1440, 2026-07-11): the FULL box's
        # counter = tickets THIS box should work via /autopilot — open minus
        # autopilot-skip minus stream:david/montalu/marek (sub-dev-owned). The fake
        # gh returns 10 ONLY when the search carries the stream exclusions (17
        # without) — gatekeeper's live numbers.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
                '  *-label:stream:david*-label:stream:marek*-label:stream:montalu*) echo 10;;\n'
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
            self.assertEqual(cache["open"], 10)      # own slice, NOT the 17 backlog
            self.assertEqual(cache.get("scope"), "core")

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
            self.assertIn("Issues 12", seg)
            self.assertIn("skipped 3", seg)

    def test_render_hides_skipped_at_zero_or_missing(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=12, name="demo", skipped=0)
            self.assertNotIn("skipped", self._seg(home, cwd))
            _seed_cache(home, cwd, open_n=12, name="demo")
            self.assertNotIn("skipped", self._seg(home, cwd))

    def test_render_combines_with_gk_bucket(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=1, name="demo", gk=5, scope="mine",
                        skipped=2)
            seg = self._seg(home, cwd)
            self.assertIn("Issues 1", seg)
            self.assertIn("gk 5", seg)
            self.assertIn("skipped 2", seg)

    def test_render_shows_skipped_during_autopilot_run(self):
        # done/total mode must not hide the skip info — skips are exactly the
        # tickets the run will NOT touch.
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=9, name="demo", skipped=2)
            _seed_progress(home, "demo", done=1, remaining=3)
            seg = self._seg(home, cwd)
            self.assertIn("Issues 1/4", seg)
            self.assertIn("skipped 2", seg)

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
                '  *) echo 7;;\n'
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
            self.assertIn("Issues 7", seg)
            self.assertIn("skipped 2", seg)

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
