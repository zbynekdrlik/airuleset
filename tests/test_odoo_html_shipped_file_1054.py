# airuleset:html-ok -- every string below is a HOOK-TEST payload, not a real
# Odoo post; the marker keeps the #915/#1054 PreToolUse hook from blocking the
# Write/Edit of this fixture file itself.
"""#1054 -- close the two gaps in the Odoo HTML-body posting guard (#915).

Gap 1: a driver moved with `scp` / run via `ssh ... odoo shell < driver.py`
bypassed the hook -- the posting call lived in the FILE, not the command line
(odoo-erp #4650). Gap 2: an already entity-escaped body passed when body_is_html
was present. Locked: double-escape blocks all tools; a shipped .py is resolved
and checked (missing flag / double-escape / driver read-back); read-back is
enforced at SHIP time, never at authoring; existing #915 shapes unchanged; and a
ship WORD counts only at command position, never in quoted prose -- so
`python3 airuleset.py --goal '... scp ...'` no longer opens airuleset.py
(the live #1054 fleet-wide false positive).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-odoo-message-post-without-html.sh"

# Reuse the #915 harness (same _run_hook + the existing fixtures for the
# "existing shapes unchanged" guard).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_odoo_message_post_html_915 import (  # noqa: E402
    _run_hook,
    TASK_HTML_NO_FLAG,
    TASK_HTML_WITH_FLAG,
    PLAIN_TEXT,
    SUBTYPE_XMLID,
)


def _run_cmd(command, cwd=None):
    """Drive the hook with a Bash command payload, optionally carrying `.cwd`
    (the field the hook resolves relative local paths against)."""
    payload = {"tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = cwd
    r = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode, r.stderr


# Fixtures.
# (a) Double-escaped bodies -- already entity-escaped tags + the flag.
DOUBLE_ESCAPE_P = (
    'task.message_post(body="&lt;p&gt;Dobrý deň&lt;/p&gt;", body_is_html=True)'
)
DOUBLE_ESCAPE_A = (
    'task.message_post(body="&lt;a href=x&gt;klik&lt;/a&gt;", body_is_html=True)'
)
DOUBLE_ESCAPE_BR = (
    'task.message_post(body="riadok&lt;br/&gt;ďalší", body_is_html=True)'
)
# Double-escaped WITHOUT the flag -- still a double-escape block (the escaped
# tag is the fix target, not the missing flag).
DOUBLE_ESCAPE_NO_FLAG = (
    'task.message_post(body="&lt;p&gt;Dobrý deň&lt;/p&gt;")'
)
# A literal "less than" that is NOT an escaped tag -> must NOT double-escape.
ESCAPED_MATH_NOT_TAG = (
    'task.message_post(body="ak x &lt; y potom …", body_is_html=True)'
)

# Driver scripts (connection idiom -> DRIVER-shaped).
DRIVER_NO_FLAG = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "<p>Dobrý deň, odovzdávam funkciu.</p>"})\n'
)
DRIVER_FLAG_NO_READBACK = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "<p>Dobrý deň.</p>", "body_is_html": True})\n'
    "print('done')\n"
)
DRIVER_FLAG_READBACK = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'mid = models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "<p>Dobrý deň.</p>", "body_is_html": True})\n'
    'stored = models.execute_kw(db, uid, pwd, "mail.message", "read",\n'
    '    [[mid]], {"fields": ["body"]})[0]["body"]\n'
    'assert stored.startswith("<p>") and "&lt;" not in stored\n'
)
DRIVER_DOUBLE_ESCAPE = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "&lt;p&gt;Dobrý deň&lt;/p&gt;", "body_is_html": True})\n'
)
DRIVER_NO_POST = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'print(models.execute_kw(db, uid, pwd, "res.partner", "search", [[]]))\n'
)
DRIVER_PLAIN_POST = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "Dobry den, plain text bez HTML."})\n'
)
# A driver carrying the in-file bypass marker -> allowed even flag-less.
DRIVER_BYPASSED = (
    "# airuleset:html-ok legacy internal driver\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "<p>legacy</p>"})\n'
)
# Product-code-shaped content: self.message_post, NO connection idiom.
PRODUCT_CODE_FLAG_NO_READBACK = (
    "def _notify_client(self):\n"
    '    self.message_post(body="<p>Objednávka je hotová.</p>",\n'
    "                      body_is_html=True)\n"
)

# Read-back + a TRAILING message_post mention -> must PASS (window anchors to
# the FIRST post, review #1054 F2).
DRIVER_FLAG_READBACK_TRAILING = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'mid = models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "<p>Dobry den.</p>", "body_is_html": True})\n'
    'stored = models.execute_kw(db, uid, pwd, "mail.message", "read",\n'
    '    [[mid]], {"fields": ["body"]})[0]["body"]\n'
    'assert stored.startswith("<p>") and "&lt;" not in stored\n'
    'print("message_post complete")\n'
)

# Body double-escapes NON-allowlisted tags -> generic escaped detector (F4).
DRIVER_ESC_UNCOMMON = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url + '/xmlrpc/2/object')\n"
    'models.execute_kw(db, uid, pwd, "discuss.channel", "message_post",\n'
    '    [[283]], {"body": "&lt;i&gt;x&lt;/i&gt; &lt;code&gt;y&lt;/code&gt;",\n'
    '     "body_is_html": True})\n'
)


def _write(tmpdir, name, content):
    p = Path(tmpdir) / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# (a) double-escape -- all three tools.
# --------------------------------------------------------------------------- #
class TestDoubleEscape(unittest.TestCase):
    def test_escaped_p_with_flag_blocked_bash(self):
        rc, err = _run_hook(DOUBLE_ESCAPE_P, "Bash")
        self.assertEqual(rc, 2, err)
        self.assertRegex(err.lower(), r"dvojit|double|escap")

    def test_escaped_p_with_flag_blocked_write(self):
        rc, _ = _run_hook(DOUBLE_ESCAPE_P, "Write")
        self.assertEqual(rc, 2)

    def test_escaped_p_with_flag_blocked_edit(self):
        rc, _ = _run_hook(DOUBLE_ESCAPE_P, "Edit")
        self.assertEqual(rc, 2)

    def test_escaped_a_blocked(self):
        rc, _ = _run_hook(DOUBLE_ESCAPE_A, "Bash")
        self.assertEqual(rc, 2)

    def test_escaped_br_blocked(self):
        rc, _ = _run_hook(DOUBLE_ESCAPE_BR, "Bash")
        self.assertEqual(rc, 2)

    def test_escaped_without_flag_still_double_escape(self):
        rc, _ = _run_hook(DOUBLE_ESCAPE_NO_FLAG, "Bash")
        self.assertEqual(rc, 2)

    def test_escaped_math_not_a_tag_allowed(self):
        # "&lt; y" is a literal less-than, not an escaped tag -> not a
        # double-escape; it carries the flag, so it passes.
        rc, _ = _run_hook(ESCAPED_MATH_NOT_TAG, "Bash")
        self.assertEqual(rc, 0)

    def test_inline_uncommon_escaped_tag_blocked(self):
        # An escaped NON-allowlisted tag inline (any tool) -> BLOCK (generic
        # escaped detector, review #1054 F4).
        payload = 'task.message_post(body="&lt;code&gt;x&lt;/code&gt;", body_is_html=True)'
        for tool in ("Bash", "Write", "Edit"):
            rc, _ = _run_hook(payload, tool)
            self.assertEqual(rc, 2, "tool=%s" % tool)


# --------------------------------------------------------------------------- #
# (b)/(c) shipped-file driver via scp / ssh< / cat| / docker shell<.
# --------------------------------------------------------------------------- #
class TestShippedFile(unittest.TestCase):
    def test_scp_driver_no_flag_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 2, err)
            self.assertIn("body_is_html", err)

    def test_scp_driver_flag_no_readback_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_FLAG_NO_READBACK)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 2, err)
            self.assertRegex(err.lower(), r"read.?back")

    def test_scp_driver_flag_readback_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_FLAG_READBACK)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 0, err)

    def test_scp_driver_double_escape_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_DOUBLE_ESCAPE)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 2, err)
            self.assertRegex(err.lower(), r"dvojit|double|escap")

    def test_ssh_stdin_redirect_no_flag_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd(
                "ssh gk 'docker compose run --rm web odoo shell' < %s" % f
            )
            self.assertEqual(rc, 2, err)

    def test_ssh_stdin_redirect_readback_missing_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_FLAG_NO_READBACK)
            rc, err = _run_cmd(
                "ssh gk 'docker compose run --rm web odoo shell' < %s" % f
            )
            self.assertEqual(rc, 2, err)
            self.assertRegex(err.lower(), r"read.?back")

    def test_cat_pipe_ssh_no_flag_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("cat %s | ssh gk 'odoo shell'" % f)
            self.assertEqual(rc, 2, err)

    def test_local_docker_shell_redirect_readback_ok_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_FLAG_READBACK)
            rc, err = _run_cmd(
                "docker compose run --rm web odoo shell < %s" % f
            )
            self.assertEqual(rc, 0, err)

    def test_scp_driver_no_post_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "query.py", DRIVER_NO_POST)
            rc, err = _run_cmd("scp %s gk:/tmp/query.py" % f)
            self.assertEqual(rc, 0, err)

    def test_scp_plain_text_post_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_PLAIN_POST)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 0, err)

    def test_scp_absent_path_fail_open(self):
        rc, err = _run_cmd("scp /nonexistent/definitely/absent.py gk:/tmp/x.py")
        self.assertEqual(rc, 0, err)

    def test_scp_in_file_bypass_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_BYPASSED)
            rc, err = _run_cmd("scp %s gk:/tmp/driver.py" % f)
            self.assertEqual(rc, 0, err)

    def test_relative_path_resolved_against_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("scp driver.py gk:/tmp/driver.py", cwd=d)
            self.assertEqual(rc, 2, err)

    def test_non_ship_command_with_py_fail_open(self):
        # A non-ship command that merely names a .py must NOT be resolved
        # (no scp/rsync, no stdin-redirect, no cat|) -> fail open.
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("ruff check %s" % f)
            self.assertEqual(rc, 0, err)

    # -- review #1054 regressions (data-driven; name, filename, fixture, cmd, rc)
    _REGRESSIONS = [
        ("f1_filename", "send_message_post.py", "DRIVER_NO_FLAG",
         "scp %s gk:/tmp/x.py", 2),          # ship cmdline mentions the call
        ("f1_comment", "driver.py", "DRIVER_NO_FLAG",
         "scp %s gk:/tmp/x.py # runs the driver", 2),
        ("f3_apostrophe", "driver.py", "DRIVER_NO_FLAG",
         "scp %s gk:/tmp/x.py # Milan's driver", 2),   # unbalanced quote
        ("f4_uncommon_esc", "driver.py", "DRIVER_ESC_UNCOMMON",
         "scp %s gk:/tmp/x.py", 2),          # escaped i/code tags (generic)
        ("f5_sftp", "driver.py", "DRIVER_NO_FLAG",
         "sftp -b %s gk:/tmp/", 2),          # sftp command-position
        ("f2_readback_trailing", "driver.py", "DRIVER_FLAG_READBACK_TRAILING",
         "scp %s gk:/tmp/x.py", 0),          # read-back + trailing mention -> ok
    ]

    def test_review_1054_regressions(self):
        for name, fn, fx, tpl, exp in self._REGRESSIONS:
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                f = _write(d, fn, globals()[fx])
                rc, err = _run_cmd(tpl % f)
                self.assertEqual(rc, exp, "%s: %s" % (name, err))

    def test_shipped_block_message_has_no_shell_errors(self):
        # Path B block messages must render cleanly (no command-substitution
        # from backticks in an unquoted heredoc) and keep their key tokens.
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("scp %s gk:/tmp/x.py" % f)
            self.assertEqual(rc, 2, err)
            self.assertNotIn("command not found", err)
            self.assertNotIn("syntax error", err)
            self.assertIn("body_is_html", err)
            self.assertIn("driver.py", err)


# --------------------------------------------------------------------------- #
# COMMAND-POSITION ship detection (#1054 live FP fix). A ship WORD counts only
# at command position and never inside quotes; the fleet-wide FP was a quoted
# ship word in prose + `python3 airuleset.py` opening airuleset.py.
# --------------------------------------------------------------------------- #
# A driver-shaped module (like airuleset.py): posting call + connection idiom.
MODULE_DRIVERISH = (
    "import xmlrpc.client\n"
    "models = xmlrpc.client.ServerProxy(url)\n"
    'CALL = "message_post"\n'
    "def notify():\n"
    "    env.message_post(body='<p>done</p>', body_is_html=True)\n"
)


class TestShipCommandPosition(unittest.TestCase):
    # A quoted ship word in prose + a `python3 airuleset.py` run must NOT open
    # any local .py (the fleet-wide #1054 false positive) -> all rc 0.
    _NEG = [
        "python3 airuleset.py notify --run-card "
        "--goal 'driver poslaný cez scp/ssh odoo shell'",
        'git commit -m "fix: scp driver docs" && python3 airuleset.py push',
        'echo "use rsync for backups" && python3 tool.py',
        'gh issue comment 5 --body "we use sftp for backups" --body-file note.md',
        "echo ship tool.py via scp && python3 airuleset.py notify",  # anchor-only guard
    ]

    def test_quoted_ship_word_in_prose_allowed(self):
        for cmd in self._NEG:
            with self.subTest(cmd[:36]), tempfile.TemporaryDirectory() as d:
                _write(d, "airuleset.py", MODULE_DRIVERISH)
                _write(d, "tool.py", MODULE_DRIVERISH)
                _write(d, "note.md", "notes")
                rc, err = _run_cmd(cmd, cwd=d)
                self.assertEqual(rc, 0, err)

    def test_python3_run_of_driver_is_not_shipped(self):
        # item (3): a `python3 FILE.py` invocation is a RUN, never a shipped file.
        with tempfile.TemporaryDirectory() as d:
            f = _write(d, "driver.py", DRIVER_NO_FLAG)
            rc, err = _run_cmd("python3 %s" % f)
            self.assertEqual(rc, 0, err)

    def test_ship_word_at_command_position_still_blocks(self):
        for tpl in ("sudo scp %s gk:/tmp/", "cd /tmp && scp %s gk:/tmp/"):
            with self.subTest(tpl), tempfile.TemporaryDirectory() as d:
                f = _write(d, "driver.py", DRIVER_NO_FLAG)
                rc, err = _run_cmd(tpl % f)
                self.assertEqual(rc, 2, err)


# Write/Edit read-back is NOT enforced at authoring time (over-blocks docs /
# product code / tests fleet-wide, review #1054) -- only at SHIP time.
class TestWriteEditNoAuthoringReadback(unittest.TestCase):
    def test_write_driver_flag_no_readback_allowed(self):
        # Authoring a driver (flag, no read-back) is allowed -- the read-back is
        # required at SHIP time, not at Write time.
        rc, err = _run_hook(DRIVER_FLAG_NO_READBACK, "Write")
        self.assertEqual(rc, 0, err)

    def test_edit_driver_flag_no_readback_allowed(self):
        rc, err = _run_hook(DRIVER_FLAG_NO_READBACK, "Edit")
        self.assertEqual(rc, 0, err)

    def test_write_driver_flag_readback_allowed(self):
        rc, err = _run_hook(DRIVER_FLAG_READBACK, "Write")
        self.assertEqual(rc, 0, err)

    def test_write_product_code_allowed(self):
        # self.message_post + flag, no read-back -> allowed (product code).
        rc, err = _run_hook(PRODUCT_CODE_FLAG_NO_READBACK, "Write")
        self.assertEqual(rc, 0, err)

    def test_write_doc_discussing_driver_mechanics_allowed(self):
        # A DOC that mentions message_post + "odoo shell" + raw <p> + the flag
        # (e.g. this very doctrine file) must stay editable fleet-wide -- the
        # authoring-time read-back check would have blocked it (review #1054).
        doc = (
            "Chatter helper: a driver shipped by `ssh gk 'odoo shell'` posts\n"
            'via message_post(body="<p>Dobry den</p>", body_is_html=True).\n'
        )
        rc, err = _run_hook(doc, "Write")
        self.assertEqual(rc, 0, err)

    def test_bash_inline_flag_no_readback_unchanged(self):
        # An INLINE Bash post with the flag but no read-back stays #915-clean.
        rc, err = _run_hook(DRIVER_FLAG_NO_READBACK, "Bash")
        self.assertEqual(rc, 0, err)


# --------------------------------------------------------------------------- #
# (e) existing #915 shapes unchanged.
# --------------------------------------------------------------------------- #
class TestExistingUnchanged(unittest.TestCase):
    def test_task_html_no_flag_still_blocked(self):
        rc, _ = _run_hook(TASK_HTML_NO_FLAG, "Bash")
        self.assertEqual(rc, 2)

    def test_task_html_with_flag_still_allowed(self):
        rc, _ = _run_hook(TASK_HTML_WITH_FLAG, "Bash")
        self.assertEqual(rc, 0)

    def test_plain_text_still_allowed(self):
        rc, _ = _run_hook(PLAIN_TEXT, "Bash")
        self.assertEqual(rc, 0)

    def test_subtype_xmlid_still_allowed(self):
        rc, _ = _run_hook(SUBTYPE_XMLID, "Bash")
        self.assertEqual(rc, 0)


# --------------------------------------------------------------------------- #
# (f-doctrine) handover-compose.md gk "no ad-hoc driver" bullet -- window teeth
# (#498/#500): the operative negation must live INSIDE the new bullet, not just
# somewhere in the file.
# --------------------------------------------------------------------------- #
COMPOSE = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"


class TestDoctrine1054(unittest.TestCase):
    def _bullet(self):
        text = COMPOSE.read_text(encoding="utf-8")
        idx = text.find("ad-hoc driver")
        self.assertGreater(
            idx, -1,
            "handover-compose.md is missing the #1054 gk no-ad-hoc-driver bullet",
        )
        start = text.rfind("- **", 0, idx)
        self.assertGreater(start, -1, "no bullet start before the ad-hoc anchor")
        nxt = text.find("\n- **", idx)
        return text[start:nxt if nxt != -1 else len(text)]

    def test_operative_negation_in_bullet(self):
        b = " ".join(self._bullet().split())
        self.assertIn("NIKDY ad-hoc driver", b)

    def test_points_at_stream_approved_poster_and_readback(self):
        b = " ".join(self._bullet().split())
        self.assertRegex(b, r"stream")
        self.assertIn("read-back", b)

    def test_cites_hook_and_ticket(self):
        b = self._bullet()
        self.assertIn("block-odoo-message-post-without-html.sh", b)
        self.assertIn("#1054", b)


if __name__ == "__main__":
    unittest.main()
