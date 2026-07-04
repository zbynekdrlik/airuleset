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


def _seed_cache(home, cwd, open_n=None, name="", ts=None):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(
        {"open": open_n, "name": name, "root": str(cwd),
         "ts": int(time.time() if ts is None else ts)}))


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
        self.assertIn("🎫 14", self._seg())

    def test_autopilot_progress_wins_when_fresh(self):
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        _seed_progress(self.home, "demo", done=3, remaining=14)
        self.assertIn("🎫 3/17", self._seg())

    def test_stale_progress_falls_back_to_open_count(self):
        _seed_cache(self.home, self.cwd, open_n=14, name="demo")
        _seed_progress(self.home, "demo", done=3, remaining=14,
                       ts=time.time() - statusbar.AUTOPILOT_RUN_WINDOW_S - 60)
        self.assertIn("🎫 14", self._seg())

    def test_backlog_empty_renders_green(self):
        _seed_cache(self.home, self.cwd, open_n=0, name="demo")
        _seed_progress(self.home, "demo", done=17, remaining=0)
        seg = self._seg()
        self.assertIn("🎫 17/17", seg)
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
            self.assertIn("🎫 7", statusbar.tickets_segment(repo, home=home,
                                                            spawn=False))

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
