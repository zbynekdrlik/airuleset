"""#1083 — `core-quals`/`slice-quals` exclude `M` from `--count`/`--list-dispatchable`,
and `core-quals --audit` surfaces the release-hygiene `merged-released-still-open`
line (an open ticket whose fix already reached main).

RED-first: the base tree neither excludes M from --count nor emits the audit line.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


def _labels(*names):
    return [{"name": n} for n in names]


# Filter-aware fake gh (models server-side --search label filtering) + a PR-meta
# reader for `gh api repos/<slug>/pulls/<N>`.
_FAKE_GH = r'''#!/usr/bin/env python3
import json, sys
FIXTURE = %(fixture)s
PR_META = %(prmeta)s
args = sys.argv[1:]
if "repo" in args and "view" in args:
    print("zbynekdrlik/demo"); sys.exit(0)
if args and args[0] == "api":
    # gh api repos/<slug>/pulls/<N> --jq {...}
    path = args[1] if len(args) > 1 else ""
    for pr, meta in PR_META.items():
        if path.endswith("/pulls/%%s" %% pr):
            print(json.dumps(meta)); sys.exit(0)
    print("{}"); sys.exit(0)
if "issue" in args and "list" in args and "--search" in args:
    search = args[args.index("--search") + 1]
    exclude, require = set(), set()
    for tok in search.split():
        if tok.startswith("-label:"):
            exclude.add(tok[len("-label:"):])
        elif tok.startswith("label:"):
            require.add(tok[len("label:"):])
    out = []
    for row in FIXTURE:
        names = {l["name"] for l in row.get("labels") or []}
        if names & exclude:
            continue
        if require and not (require <= names):
            continue
        out.append(row)
    # -q length support (skip query)
    if "length" in args:
        print(len(out)); sys.exit(0)
    print(json.dumps(out)); sys.exit(0)
print("[]")
'''


def _write_fake_gh(bindir, fixture, prmeta):
    gh = Path(bindir) / "gh"
    gh.write_text(_FAKE_GH % {"fixture": json.dumps(fixture),
                              "prmeta": json.dumps(prmeta)})
    gh.chmod(0o755)


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", repo, *args], check=True, env=env,
                   capture_output=True)


_GENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}


def _run(flag, repo, home, bindir):
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
         "core-quals", flag],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "HOME": home,
             "PATH": f"{bindir}:{os.environ['PATH']}"})


class CoreQualsExcludesMergedFromCount(unittest.TestCase):
    def test_count_and_list_dispatchable_exclude_M(self):
        fixture = [
            {"number": 2, "title": "plain workable",
             "createdAt": "2026-08-01T00:00:00Z", "labels": _labels("bug")},
            {"number": 105, "title": "fix merged to develop",
             "createdAt": "2026-08-02T00:00:00Z", "labels": []},
        ]
        prmeta = {"5": {"title": "PR five", "body": "Closes #105"}}
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q", "-b", "main", repo],
                           check=True, env=_GENV, capture_output=True)
            # A real origin remote so the LOCAL slug resolver (#1083 review — no
            # gh on the hot --count path) names the cache/PR-meta from git.
            _git(repo, "remote", "add", "origin",
                 "https://github.com/zbynekdrlik/demo", env=_GENV)
            _git(repo, "commit", "--allow-empty", "-q", "-m", "base", env=_GENV)
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD",
                 env=_GENV)
            _git(repo, "checkout", "-q", "-b", "devwork", env=_GENV)
            _git(repo, "commit", "--allow-empty", "-q", "-m",
                 "Merge pull request #5 from stream/5-fix", env=_GENV)
            _git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD",
                 env=_GENV)
            _write_fake_gh(bindir, fixture, prmeta)
            r = _run("--count", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            # #105 is merged-to-develop-not-main -> leaves I -> count is 1 (only #2).
            self.assertEqual(r.stdout.strip(), "1", r.stdout + r.stderr)
            rl = _run("--list-dispatchable", repo, home, bindir)
            self.assertEqual(rl.returncode, 0, rl.stderr)
            self.assertNotIn("105", rl.stdout,
                             "a merged-unreleased ticket must not be dispatchable")


class CoreQualsAuditReleaseHygiene(unittest.TestCase):
    def test_audit_flags_merged_released_still_open(self):
        fixture = [
            {"number": 2, "title": "plain workable",
             "createdAt": "2026-08-01T00:00:00Z", "labels": _labels("bug")},
            {"number": 106, "title": "released but still open",
             "createdAt": "2026-08-02T00:00:00Z", "labels": []},
        ]
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q", "-b", "main", repo],
                           check=True, env=_GENV, capture_output=True)
            _git(repo, "remote", "add", "origin",
                 "https://github.com/zbynekdrlik/demo", env=_GENV)
            _git(repo, "commit", "--allow-empty", "-q", "-m", "base", env=_GENV)
            main_sha = subprocess.run(
                ["git", "-C", repo, "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True, env=_GENV).stdout.strip()
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD",
                 env=_GENV)
            # Pre-seed the append-only PR cache (full-slug filename, #1083
            # review): PR #6 -> #106, its merge commit is the main HEAD
            # (released). merged_released_still_open sees it reachable from main.
            tsdir = Path(home) / ".claude" / "tickets-status"
            tsdir.mkdir(parents=True, exist_ok=True)
            (tsdir / "pr-issues-zbynekdrlik__demo.json").write_text(json.dumps(
                {"6": {"issues": [106], "oid": main_sha}}))
            _write_fake_gh(bindir, fixture, {})
            r = _run("--audit", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("merged-released-still-open", r.stdout, r.stdout + r.stderr)
            self.assertIn("#106", r.stdout)
            self.assertNotIn("#2", r.stdout.split("merged-released-still-open")[-1])


if __name__ == "__main__":
    unittest.main()
