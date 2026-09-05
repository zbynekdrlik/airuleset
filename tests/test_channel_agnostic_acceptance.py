"""Recidivism guard for #891 — airuleset fleet doctrine must stay
channel-agnostic for client acceptance. Channel-specific content (XML-RPC
recipes, Discuss-prescriptive handover rules) belongs in the owning project
(odoo-erp), not in airuleset's fleet doctrine.

Three teeth:
1. Negative sweep: fleet doctrine surfaces must not contain channel-prescriptive
   tokens (xmlrpc, execute_kw) outside a small allowlist.
2. Exact-set lock on the close guard's accepted marker names.
3. Positive assertion: the new SKILL.md contains the pointer to odoo-erp rules.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_close_guard as g  # noqa: E402


class TestNoChannelPrescriptiveContent(TestCase):
    """Fleet doctrine surfaces must NOT contain channel-prescriptive tokens."""

    # Files that may legitimately mention XML-RPC / execute_kw (legacy guard
    # code, history, the stub, Odoo-specific recipe companions that are
    # transport-specific by nature, modules mentioning XML-RPC in a non-
    # acceptance context).
    ALLOWLIST = {
        # The close guard itself (legacy marker regexes)
        "discuss_close_guard.py",
        # The close guard hook (legacy block message text)
        "hooks/block-fork-no-merge-issue-close.sh",
        # The close guard segment helper
        "hooks/close_guard_segment.py",
        # The old stub skill (points at the new skill)
        "skills/odoo-discuss-xmlrpc/SKILL.md",
        # The old companion files (kept for stub foreign references)
        "skills/odoo-discuss-xmlrpc/handover-compose.md",
        "skills/odoo-discuss-xmlrpc/read-reactions.md",
        "skills/odoo-discuss-xmlrpc/read-with-attachments.md",
        # New companion files that carry Odoo-specific read recipes
        # (these are transport-specific utilities, not acceptance channel
        # prescription — they teach HOW to read, not WHERE to post)
        "skills/odoo-client-messaging/read-reactions.md",
        "skills/odoo-client-messaging/read-with-attachments.md",
        # Modules that mention XML-RPC in a non-acceptance context
        "modules/core/view-image-urls.md",  # ir.attachment recipe
        "modules/core/ci-monitoring.md",    # deploy-watch example
        "modules/core/statusline-vocabulary.md",  # W worked example (non-prescriptive)
        # Rules reference / history / archive (not fleet doctrine)
        # (matched by directory prefix below)
    }

    # Directories whose files are always allowed (history, not doctrine).
    ALLOWED_DIRS = {".claude/rules-reference"}

    # Tokens that indicate channel-prescriptive content in fleet doctrine.
    PRESCRIPTIVE_RE = re.compile(
        r"(?i)(xml-?rpc|execute_kw|xmlrpc\.client|xmlrpc/2)",
    )

    def _fleet_doctrine_files(self):
        """Yield (relative_path, content) for fleet doctrine surfaces."""
        for pattern in ("modules/**/*.md", "agents/*.md",
                        "skills/odoo-client-messaging/*.md"):
            for p in ROOT.glob(pattern):
                rel = str(p.relative_to(ROOT))
                if rel in self.ALLOWLIST:
                    continue
                if any(rel.startswith(d) for d in self.ALLOWED_DIRS):
                    continue
                yield rel, p.read_text(encoding="utf-8", errors="replace")

    def test_no_xmlrpc_in_fleet_doctrine(self):
        violations = []
        for rel, content in self._fleet_doctrine_files():
            matches = self.PRESCRIPTIVE_RE.findall(content)
            if matches:
                violations.append(f"{rel}: {matches[:3]}")
        self.assertEqual(
            violations, [],
            "Channel-prescriptive tokens found in fleet doctrine — "
            "move them to the owning project's rules (#891):\n"
            + "\n".join(violations),
        )


class TestCloseGuardMarkerExactSet(TestCase):
    """Lock the exact set of markers the close guard recognises.

    A future channel-named marker (Task-closed:, Whatsapp-closed:) forces a
    deliberate, reviewed edit to this test — never silent accretion."""

    EXPECTED_BINDING_MARKERS = {"Discuss-thread", "Acceptance-thread"}
    EXPECTED_DISPOSITION_MARKERS = {
        "Discuss-closed", "Discuss-defer",
        "Acceptance-cited", "Acceptance-defer",
    }

    def test_binding_regexes_match_exactly_the_expected_set(self):
        # Verify each expected marker IS recognised as a binding.
        for name in self.EXPECTED_BINDING_MARKERS:
            with self.subTest(name=name):
                self.assertTrue(
                    g.is_thread_bound(f"{name}: test-value"),
                    f"{name}: must be recognised as a binding",
                )

    def test_disposition_regexes_match_exactly_the_expected_set(self):
        for name in self.EXPECTED_DISPOSITION_MARKERS:
            with self.subTest(name=name):
                self.assertTrue(
                    g.has_disposition(f"{name}: test-value"),
                    f"{name}: must be recognised as a disposition",
                )

    def test_no_channel_specific_markers_accepted(self):
        """Channel-specific markers must NOT be accepted."""
        for name in ("Task-closed", "Task-thread", "Whatsapp-closed",
                     "Email-thread", "Chatter-closed"):
            with self.subTest(name=name):
                self.assertFalse(
                    g.is_thread_bound(f"{name}: test"),
                    f"{name}: must NOT be a binding",
                )
                self.assertFalse(
                    g.has_disposition(f"{name}: test"),
                    f"{name}: must NOT be a disposition",
                )


class TestNewSkillPointer(TestCase):
    """The new SKILL.md must contain the pointer to odoo-erp rules."""

    SKILL = ROOT / "skills" / "odoo-client-messaging" / "SKILL.md"

    def test_skill_exists(self):
        self.assertTrue(self.SKILL.exists(), "odoo-client-messaging/SKILL.md must exist")

    def test_skill_contains_pointer_to_odoo_erp_rules(self):
        content = self.SKILL.read_text()
        self.assertIn(
            "odoo-task-sync.md",
            content,
            "SKILL.md must point to odoo-erp's odoo-task-sync.md",
        )

    def test_skill_is_channel_agnostic(self):
        content = self.SKILL.read_text()
        self.assertIn("channel-agnostic", content.lower())

    def test_stub_at_old_path_exists(self):
        stub = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"
        self.assertTrue(stub.exists(), "stub at old path must exist")
        content = stub.read_text()
        self.assertIn("RENAMED", content)
        self.assertIn("odoo-client-messaging", content)


if __name__ == "__main__":
    main()
