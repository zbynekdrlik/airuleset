"""#972 REOPEN deliverable 3 — the `status` MISMATCH consumer.

`airuleset.py status` already DETECTS dangling / worktree-target managed
symlinks (`_check_symlink_health`), but nothing consumed that verdict, so the
2026-09-11 drift sat unacted for 19 h. This adds a FIFTH dimension to the EXISTING
watchdog conformance job (Job 34, `watchdog/conformance.py`) — no new job — so the
drift is reported once/day to the journal + a deduped notify row.

Tests:
  1. `classify_symlinks` pure decider: None (scan error) → UNDETERMINED, [] → OK,
     drift list → DRIFT.
  2. `run_conformance_check` with an injected `symlink_scan` seam emits a
     `[symlinks]` DRIFT log + one delivered ping when the scan returns drift, and
     an OK log with no symlinks ping when the scan is clean.
  3. `_scan_managed_symlink_drift` real scanner: flags a worktree-target symlink
     and an airuleset-owned dangling symlink; ignores a foreign dangling symlink.
"""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import watchdog.conformance as conf  # noqa: E402

NOW = 1786000000.0
ROOT = "/repo/airuleset"


def fake_git(head="aaaaaaaa1111", origin="aaaaaaaa1111"):
    def g(args, cwd, timeout=None):
        sub = args[0]
        if sub == "fetch":
            return (0, "")
        if sub == "rev-parse":
            return (0, (head if args[-1] == "HEAD" else origin) + "\n")
        if sub == "merge-base":
            return (0, "")
        if sub == "status":
            return (0, "")
        return (0, "")
    return g


def collect_send():
    calls = []
    seen = set()

    def send(body, dedup_key=None, dry_run=False):
        if dedup_key is not None and dedup_key in seen:
            return "dedup"
        if dedup_key is not None:
            seen.add(dedup_key)
        calls.append({"body": body, "dedup_key": dedup_key})
        return "sent"
    return calls, send


def _seed_conformant(tmp):
    """Write a CLAUDE.md + a baseline whose md5 matches, so every non-symlink
    dimension is CONFORMANT and only the injected symlink_scan can drift."""
    cmd = os.path.join(tmp, "CLAUDE.md")
    with open(cmd, "w") as fh:
        fh.write("managed content\n")
    base = os.path.join(tmp, conf.CONFORMANCE_BASELINE_NAME)
    import json
    with open(base, "w") as fh:
        json.dump({"claude_md_md5": conf._md5_file(cmd),
                   "head_sha": "aaaaaaaa1111"}, fh)
    return cmd, base


def _run(symlink_scan, tmp):
    cmd, base = _seed_conformant(tmp)
    calls, send = collect_send()
    logs = conf.run_conformance_check(
        NOW, {}, send_fn=send, dry_run=False, repo_root=ROOT,
        claude_md_path=cmd, baseline_path=base,
        git_run=fake_git(), timer_check=lambda unit=None: "active",
        is_target_check=lambda: True, symlink_scan=symlink_scan,
        persist=lambda: None)
    return logs, calls


class TestClassifySymlinks(unittest.TestCase):
    def test_none_is_undetermined(self):
        dim, ok, _ = conf.classify_symlinks(None)
        self.assertEqual(dim, "symlinks")
        self.assertIsNone(ok)

    def test_empty_is_conformant(self):
        dim, ok, _ = conf.classify_symlinks([])
        self.assertEqual((dim, ok), ("symlinks", True))

    def test_drift_is_reported(self):
        dim, ok, detail = conf.classify_symlinks(
            [("autopilot-worker.md", "worktree", "/x/.claude/worktrees/a/agents/x")])
        self.assertEqual((dim, ok), ("symlinks", False))
        self.assertIn("autopilot-worker", detail)


class TestConformanceSymlinkDimension(unittest.TestCase):
    def test_drift_scan_pings_and_logs(self):
        with TemporaryDirectory() as tmp:
            drift = [("autopilot-worker.md", "worktree",
                      "/h/.claude/worktrees/agent-x/agents/autopilot-worker.md")]
            logs, calls = _run(lambda: drift, tmp)
        joined = "\n".join(logs)
        self.assertIn("[symlinks]", joined)
        self.assertIn("DRIFT", joined)
        self.assertEqual(len(calls), 1,
                         "exactly one drift dimension (symlinks) must ping")
        self.assertIn("autopilot-worker", calls[0]["body"])

    def test_clean_scan_no_symlink_ping(self):
        with TemporaryDirectory() as tmp:
            logs, calls = _run(lambda: [], tmp)
        joined = "\n".join(logs)
        self.assertIn("[symlinks]", joined)
        self.assertIn("OK", joined)
        self.assertEqual(calls, [], "a clean scan must not ping")

    def test_scan_error_undetermined_no_ping(self):
        with TemporaryDirectory() as tmp:
            logs, calls = _run(lambda: None, tmp)
        self.assertIn("[symlinks]", "\n".join(logs))
        self.assertEqual(calls, [], "an UNDETERMINED scan must never ping")


class TestScanManagedSymlinkDrift(unittest.TestCase):
    def test_worktree_and_owned_dangling_flagged_foreign_ignored(self):
        with TemporaryDirectory() as tmp:
            agents = Path(tmp) / ".claude" / "agents"
            skills = Path(tmp) / ".claude" / "skills"
            agents.mkdir(parents=True)
            skills.mkdir(parents=True)
            # (a) worktree-target symlink (target may exist) — flagged.
            wt = Path(tmp) / ".claude" / "worktrees" / "agent-x" / "agents"
            wt.mkdir(parents=True)
            (wt / "autopilot-worker.md").write_text("x")
            (agents / "autopilot-worker.md").symlink_to(
                wt / "autopilot-worker.md")
            # (b) airuleset-owned dangling symlink (target under REPO_DIR, gone).
            (agents / "ticket-validator.md").symlink_to(
                Path(airuleset.REPO_DIR) / "agents" / "does-not-exist.md")
            # (c) foreign dangling symlink (target elsewhere) — IGNORED.
            (skills / "win-mcp").symlink_to("/nonexistent/foreign/win-mcp")
            out = airuleset._scan_managed_symlink_drift(
                agents_dir=str(agents), skills_dir=str(skills))
        names = {n for n, _r, _t in out}
        reasons = {n: r for n, r, _t in out}
        self.assertIn("autopilot-worker.md", names)
        self.assertEqual(reasons["autopilot-worker.md"], "worktree")
        self.assertIn("ticket-validator.md", names)
        self.assertEqual(reasons["ticket-validator.md"], "dangling")
        self.assertNotIn("win-mcp", names,
                         "a foreign (non-owned) dangling symlink must be ignored")


if __name__ == "__main__":
    unittest.main()
