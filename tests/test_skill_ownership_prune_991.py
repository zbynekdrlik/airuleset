"""#991 — cmd_install must PRUNE a managed agent/skill symlink whose source was
REMOVED from the repo (dangling) or dropped from AGENT_NAMES/SKILL_NAMES.

Root cause: cmd_install LINKS AGENT_NAMES -> ~/.claude/agents/ and SKILL_NAMES
-> ~/.claude/skills/, but the only prune (step 2a) iterates SKILL_NAMES for the
box-scoping case — it never removes a symlink whose agent/skill was deleted from
the repo entirely (name no longer in the lists), and agents had no prune at all.
After #991 deleted 3 tier agents + 2 skills, their installed symlinks dangled
fleet-wide. Same class as #972 (dangling managed symlink), different trigger.

Tests (fake home dir + fake REPO_DIR):
  1. dangling airuleset-owned agent + skill symlinks are pruned
  2. a managed symlink not in the names list (non-dangling) is pruned
  3. a foreign-target symlink survives (win-mcp — Skill Ownership rule)
  4. a regular file survives
  5. a real directory survives
  6. the printed `Removed:  <path>` line shape
  7. _check_agent_symlinks reports a removed-agent dangling symlink as a row
  8. _check_skill_symlinks reports a removed-skill dangling row + returns it
     so cmd_status can exclude it from the foreign "Unmanaged skills" list
"""

import contextlib
import io
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest import mock

import airuleset


class _Base(TestCase):
    """Fake REPO_DIR + fake ~/.claude/{agents,skills}, all module globals
    patched so nothing touches the real host."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.repo = root / "repo"
        (self.repo / "agents").mkdir(parents=True)
        (self.repo / "skills").mkdir(parents=True)

        self.agents_dir = root / "home" / ".claude" / "agents"
        self.skills_dir = root / "home" / ".claude" / "skills"
        self.agents_dir.mkdir(parents=True)
        self.skills_dir.mkdir(parents=True)

        self.foreign = root / "foreign"
        self.foreign.mkdir()

        for target, attr in (
            (airuleset, "REPO_DIR"),
        ):
            p = mock.patch.object(target, attr, self.repo)
            p.start()
            self.addCleanup(p.stop)
        for attr, val in (
            ("AGENTS_DIR", self.agents_dir),
            ("SKILLS_DIR", self.skills_dir),
            ("AGENT_NAMES", ["autopilot-worker", "ticket-validator"]),
            ("SKILL_NAMES", ["ci-monitor", "deploy-ssh"]),
        ):
            p = mock.patch.object(airuleset, attr, val)
            p.start()
            self.addCleanup(p.stop)

    # --- helpers ---
    def _repo_agent(self, name, create=True):
        src = self.repo / "agents" / f"{name}.md"
        if create:
            src.write_text("agent\n")
        return src

    def _repo_skill(self, name, create=True):
        src = self.repo / "skills" / name
        if create:
            src.mkdir()
        return src

    def _link_agent(self, name, target):
        link = self.agents_dir / f"{name}.md"
        link.symlink_to(target)
        return link

    def _link_skill(self, name, target):
        link = self.skills_dir / name
        link.symlink_to(target)
        return link


class TestPruneDangling(_Base):
    def test_dangling_owned_agent_and_skill_symlinks_are_pruned(self):
        # removed agents/skills: source does NOT exist under the repo -> dangling
        dead_agent = self._link_agent(
            "fable-advisor", self.repo / "agents" / "fable-advisor.md")
        dead_skill = self._link_skill(
            "fable-advisor", self.repo / "skills" / "fable-advisor")
        # a currently-valid linked agent/skill (in the names list, target real)
        self._repo_agent("autopilot-worker")
        live_agent = self._link_agent(
            "autopilot-worker", self.repo / "agents" / "autopilot-worker.md")
        self._repo_skill("ci-monitor")
        live_skill = self._link_skill(
            "ci-monitor", self.repo / "skills" / "ci-monitor")

        airuleset._prune_stale_managed_symlinks(
            self.skills_dir, "skills", airuleset.SKILL_NAMES)
        airuleset._prune_stale_managed_symlinks(
            self.agents_dir, "agents", airuleset.AGENT_NAMES, suffix=".md")

        self.assertFalse(dead_agent.is_symlink(), "dangling agent not pruned")
        self.assertFalse(dead_skill.is_symlink(), "dangling skill not pruned")
        self.assertTrue(live_agent.is_symlink(), "live agent wrongly pruned")
        self.assertTrue(live_skill.is_symlink(), "live skill wrongly pruned")

    def test_managed_symlink_not_in_names_list_is_pruned(self):
        # source EXISTS (not dangling) but the name was dropped from the list
        self._repo_agent("oldagent")
        gone_agent = self._link_agent(
            "oldagent", self.repo / "agents" / "oldagent.md")
        self._repo_skill("oldskill")
        gone_skill = self._link_skill(
            "oldskill", self.repo / "skills" / "oldskill")
        self.assertTrue(gone_agent.exists(), "precondition: not dangling")
        self.assertTrue(gone_skill.exists(), "precondition: not dangling")

        airuleset._prune_stale_managed_symlinks(
            self.skills_dir, "skills", airuleset.SKILL_NAMES)
        airuleset._prune_stale_managed_symlinks(
            self.agents_dir, "agents", airuleset.AGENT_NAMES, suffix=".md")

        self.assertFalse(gone_agent.is_symlink(),
                         "name-dropped agent not pruned")
        self.assertFalse(gone_skill.is_symlink(),
                         "name-dropped skill not pruned")

    def test_foreign_symlink_survives(self):
        # win-mcp points OUTSIDE the repo (belongs to winremote-setup) -> keep
        (self.foreign / "win-mcp").mkdir()
        foreign = self._link_skill("win-mcp", self.foreign / "win-mcp")
        # even a DANGLING foreign symlink is not airuleset-owned -> keep
        dangling_foreign = self._link_skill(
            "other-mcp", self.foreign / "does-not-exist")

        airuleset._prune_stale_managed_symlinks(
            self.skills_dir, "skills", airuleset.SKILL_NAMES)

        self.assertTrue(foreign.is_symlink(), "foreign symlink wrongly pruned")
        self.assertTrue(dangling_foreign.is_symlink(),
                        "dangling foreign symlink wrongly pruned")

    def test_regular_file_survives(self):
        reg = self.agents_dir / "notes.md"
        reg.write_text("hand-made\n")
        airuleset._prune_stale_managed_symlinks(
            self.agents_dir, "agents", airuleset.AGENT_NAMES, suffix=".md")
        self.assertTrue(reg.is_file() and not reg.is_symlink(),
                        "regular file wrongly removed")

    def test_real_directory_survives(self):
        d = self.skills_dir / "handmade-skill"
        d.mkdir()
        airuleset._prune_stale_managed_symlinks(
            self.skills_dir, "skills", airuleset.SKILL_NAMES)
        self.assertTrue(d.is_dir() and not d.is_symlink(),
                        "real directory wrongly removed")

    def test_removed_line_shape(self):
        self._link_agent(
            "fable-advisor", self.repo / "agents" / "fable-advisor.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            airuleset._prune_stale_managed_symlinks(
                self.agents_dir, "agents", airuleset.AGENT_NAMES, suffix=".md")
        out = buf.getvalue()
        expected = f"  Removed:  {self.agents_dir / 'fable-advisor.md'}"
        self.assertIn(expected, out.splitlines(),
                      f"exact 'Removed:  <path>' line missing; got: {out!r}")


class TestStatusRows(_Base):
    def test_check_agent_symlinks_reports_removed_agent_row(self):
        # removed agent still linked (dangling) — must be reported as a row
        self._link_agent(
            "fable-advisor", self.repo / "agents" / "fable-advisor.md")
        # a valid in-list agent, so the base loop has something normal too
        self._repo_agent("autopilot-worker")
        self._link_agent(
            "autopilot-worker", self.repo / "agents" / "autopilot-worker.md")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            airuleset._check_agent_symlinks()
        out = buf.getvalue()
        self.assertIn("fable-advisor", out,
                      "removed agent not reported as a status row")
        self.assertIn("MISMATCH", out)
        self.assertIn("dangling", out.lower())

    def test_check_skill_symlinks_reports_removed_skill_and_returns_it(self):
        # removed skill still linked (dangling) — reported + returned as stale
        self._link_skill(
            "fable-advisor", self.repo / "skills" / "fable-advisor")
        # a valid box skill (in the box set)
        self._repo_skill("ci-monitor")
        self._link_skill("ci-monitor", self.repo / "skills" / "ci-monitor")
        # a genuinely foreign skill must NOT be reported as owned-stale
        (self.foreign / "win-mcp").mkdir()
        self._link_skill("win-mcp", self.foreign / "win-mcp")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            stale = airuleset._check_skill_symlinks(["ci-monitor"])
        out = buf.getvalue()
        self.assertIn("fable-advisor", out,
                      "removed skill not reported as a status row")
        self.assertIn("MISMATCH", out)
        self.assertEqual(stale, {"fable-advisor"},
                         "stale-owned set must carry the removed skill only")
        self.assertNotIn("win-mcp", stale,
                         "foreign skill wrongly classified as owned-stale")


if __name__ == "__main__":
    main()
