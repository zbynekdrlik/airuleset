"""#915 — block Odoo message_post payloads without body_is_html.

Owner escalation (montalu 6.9.2026): clients keep seeing RAW HTML tags (<p>,
<b>) in Odoo chatter/Discuss messages because streams forget body_is_html=True.
Incident: 3 project.task comments by a montalu W-sweep lane with escaped HTML
(mail.message ids 1794757-1794759).

Locked here:
  1. The hook EXISTS and is wired for Bash, Write, Edit in settings/hooks.json.
  2. A message_post with HTML tags + no body_is_html => BLOCKED (exit 2).
  3. A message_post with HTML tags + body_is_html=True => ALLOWED (exit 0).
  4. A message_post with HTML tags + subtype_xmlid=mail.mt_comment => ALLOWED.
  5. A message_post with NO HTML tags => ALLOWED (plain text is fine).
  6. The airuleset:html-ok bypass marker => ALLOWED (logged).
  7. Content without message_post => ALLOWED (pre-filter exit 0).
"""

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-odoo-message-post-without-html.sh"
SETTINGS = ROOT / "settings" / "hooks.json"


def _run_hook(content, tool="Bash"):
    """Drive the real hook with a synthetic payload, return (rc, stderr)."""
    if tool == "Bash":
        payload = json.dumps({"tool_input": {"command": content}})
    elif tool == "Write":
        payload = json.dumps({"tool_input": {"content": content}})
    elif tool == "Edit":
        payload = json.dumps({"tool_input": {"new_string": content}})
    else:
        raise ValueError(tool)
    r = subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode, r.stderr


# --- Incident-shape fixtures (the exact shapes from the montalu 6.9. report) ---

# project.task chatter message_post with HTML, NO body_is_html (the BUG).
TASK_HTML_NO_FLAG = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "<p>Dobrý deň, posielam aktualizáciu.</p>'
    '<p><b>Stav:</b> hotové</p>"})'
)

# Same with body_is_html=True (the FIX).
TASK_HTML_WITH_FLAG = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "<p>Dobrý deň, posielam aktualizáciu.</p>'
    '<p><b>Stav:</b> hotové</p>", "body_is_html": True})'
)

# discuss.channel message_post with HTML, NO body_is_html.
DISCUSS_HTML_NO_FLAG = (
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post", '
    '[[101]], {"body": "<p>Informácia pre klienta.</p>"})'
)

# JSON-RPC shape with HTML, NO body_is_html.
JSONRPC_HTML_NO_FLAG = (
    '{"jsonrpc": "2.0", "method": "call", "params": {'
    '"model": "project.task", "method": "message_post", '
    '"args": [[42]], "kwargs": {"body": "<p>Test</p>"}}}'
)

# JSON-RPC shape WITH body_is_html.
JSONRPC_HTML_WITH_FLAG = (
    '{"jsonrpc": "2.0", "method": "call", "params": {'
    '"model": "project.task", "method": "message_post", '
    '"args": [[42]], "kwargs": {"body": "<p>Test</p>", '
    '"body_is_html": true}}}'
)

# ORM Python shape with HTML, NO body_is_html.
ORM_HTML_NO_FLAG = (
    "task = self.env['project.task'].browse(42)\n"
    "task.message_post(body='<p>Stav projektu</p>')"
)

# ORM Python shape WITH body_is_html.
ORM_HTML_WITH_FLAG = (
    "task = self.env['project.task'].browse(42)\n"
    "task.message_post(body='<p>Stav projektu</p>', body_is_html=True)"
)

# subtype_xmlid alternative.
SUBTYPE_XMLID = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "<p>Test</p>", '
    '"subtype_xmlid": "mail.mt_comment"})'
)

# Plain text, no HTML tags.
PLAIN_TEXT = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "Dobry den, posielam aktualizaciu."})'
)

# No message_post at all.
NO_MESSAGE_POST = (
    'models.execute_kw(db, uid, pwd, "project.task", "write", '
    '[[42]], {"name": "<p>New name</p>"})'
)

# Bypass marker.
BYPASSED = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "<p>Legacy post</p>"})'
    "  # airuleset:html-ok"
)

# Self-closing tags like <br/>, <img src="..."/>.
SELF_CLOSING = (
    'models.execute_kw(db, uid, pwd, "project.task", "message_post", '
    '[[42]], {"body": "Line 1<br/>Line 2"})'
)


class TestHookExists(unittest.TestCase):
    """RED lock: the hook file must exist."""

    def test_hook_file_exists(self):
        self.assertTrue(HOOK.exists(), f"Hook not found: {HOOK}")


class TestHookWired(unittest.TestCase):
    """The hook is wired for Bash, Write, Edit in settings/hooks.json."""

    def test_wired_in_settings(self):
        text = SETTINGS.read_text()
        self.assertIn("block-odoo-message-post-without-html.sh", text)

    def _hooks_for_matcher(self, matcher):
        data = json.loads(SETTINGS.read_text())
        pre = data.get("hooks", {}).get("PreToolUse", [])
        cmds = []
        for entry in pre:
            if entry.get("matcher") == matcher:
                for h in entry.get("hooks", []):
                    cmds.append(h.get("command", ""))
        return cmds

    def test_wired_for_bash(self):
        cmds = self._hooks_for_matcher("Bash")
        found = any("block-odoo-message-post-without-html" in c for c in cmds)
        self.assertTrue(found, "Hook not wired for Bash matcher")

    def test_wired_for_write(self):
        cmds = self._hooks_for_matcher("Write")
        found = any("block-odoo-message-post-without-html" in c for c in cmds)
        self.assertTrue(found, "Hook not wired for Write matcher")

    def test_wired_for_edit(self):
        cmds = self._hooks_for_matcher("Edit")
        found = any("block-odoo-message-post-without-html" in c for c in cmds)
        self.assertTrue(found, "Hook not wired for Edit matcher")


class TestBlockShapes(unittest.TestCase):
    """message_post + HTML tags + no body_is_html => exit 2."""

    def test_task_html_no_flag_blocked(self):
        rc, err = _run_hook(TASK_HTML_NO_FLAG)
        self.assertEqual(rc, 2, f"Expected block, got rc={rc}")
        self.assertIn("body_is_html", err)

    def test_discuss_html_no_flag_blocked(self):
        rc, err = _run_hook(DISCUSS_HTML_NO_FLAG)
        self.assertEqual(rc, 2)

    def test_jsonrpc_html_no_flag_blocked(self):
        rc, err = _run_hook(JSONRPC_HTML_NO_FLAG)
        self.assertEqual(rc, 2)

    def test_orm_html_no_flag_blocked(self):
        rc, err = _run_hook(ORM_HTML_NO_FLAG)
        self.assertEqual(rc, 2)

    def test_self_closing_tag_blocked(self):
        rc, _ = _run_hook(SELF_CLOSING)
        self.assertEqual(rc, 2)

    def test_write_tool_blocked(self):
        rc, _ = _run_hook(TASK_HTML_NO_FLAG, tool="Write")
        self.assertEqual(rc, 2)

    def test_edit_tool_blocked(self):
        rc, _ = _run_hook(TASK_HTML_NO_FLAG, tool="Edit")
        self.assertEqual(rc, 2)


class TestAllowShapes(unittest.TestCase):
    """Compliant payloads => exit 0."""

    def test_body_is_html_true_allowed(self):
        rc, _ = _run_hook(TASK_HTML_WITH_FLAG)
        self.assertEqual(rc, 0)

    def test_jsonrpc_with_flag_allowed(self):
        rc, _ = _run_hook(JSONRPC_HTML_WITH_FLAG)
        self.assertEqual(rc, 0)

    def test_orm_with_flag_allowed(self):
        rc, _ = _run_hook(ORM_HTML_WITH_FLAG)
        self.assertEqual(rc, 0)

    def test_subtype_xmlid_allowed(self):
        rc, _ = _run_hook(SUBTYPE_XMLID)
        self.assertEqual(rc, 0)

    def test_plain_text_allowed(self):
        rc, _ = _run_hook(PLAIN_TEXT)
        self.assertEqual(rc, 0)

    def test_no_message_post_allowed(self):
        rc, _ = _run_hook(NO_MESSAGE_POST)
        self.assertEqual(rc, 0)

    def test_empty_content_allowed(self):
        rc, _ = _run_hook("")
        self.assertEqual(rc, 0)


class TestBypass(unittest.TestCase):
    """The airuleset:html-ok bypass marker => exit 0."""

    def test_bypass_marker_allows(self):
        rc, _ = _run_hook(BYPASSED)
        self.assertEqual(rc, 0)


class TestBlockMessage(unittest.TestCase):
    """The block message is informative."""

    def test_message_mentions_body_is_html(self):
        _, err = _run_hook(TASK_HTML_NO_FLAG)
        self.assertIn("body_is_html", err)

    def test_message_mentions_915(self):
        _, err = _run_hook(TASK_HTML_NO_FLAG)
        self.assertIn("915", err)

    def test_message_mentions_subtype_xmlid(self):
        _, err = _run_hook(TASK_HTML_NO_FLAG)
        self.assertIn("subtype_xmlid", err)


if __name__ == "__main__":
    unittest.main()
