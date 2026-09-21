# airuleset:html-ok -- every string below is a HOOK-TEST payload, not a real
# Odoo post; the marker keeps the #915/#1054 PreToolUse hook from blocking the
# Write/Edit of this fixture file itself.
"""#1054 -- close the two gaps left in the Odoo HTML-body posting guard.

Gap 1 (shipped-file drivers): a driver written locally, moved with `scp` and
run with `ssh ... odoo shell < driver.py` bypassed the #915 hook, because the
Bash payload the hook saw was the `scp`/`ssh` command line -- the posting call
lived in the FILE, which the hook never opened (odoo-erp #4650, montalu PROD
thread 283, 16.9., mail.message 1847975, gk driver send_handover_283_v5.py).

Gap 2 (double wrapping): a body already HTML-escaped (`&lt;p&gt;...`) passed
whenever `body_is_html` was present -- the flag whitewashed an escaped body that
Odoo renders as raw tags.

Locked here (Approach 1 -- extend the #915 hook in place):
  (a) Bash/Write/Edit content with an ALREADY-ESCAPED tag (`&lt;p&gt;`, `&lt;a`,
      `&lt;br`) AND body_is_html=True => BLOCK (dedicated double-escape reason).
  (b) Bash `scp <local.py> gk:...` where the local file posts HTML without the
      flag => BLOCK; same file WITH the flag but WITHOUT a mail.message read +
      assert after the post => BLOCK (read-back reason); with flag + read-back
      => PASS.
  (c) `ssh gk 'docker compose run ... odoo shell' < <local.py>` and
      `cat <local.py> | ssh ...` => same as (b).
  (d) `scp` of a `.py` that never posts => PASS; an unreadable/absent path
      => PASS (fail-open, never guess).
  (e) the existing #915 shapes are unchanged.
  (f) Write/Edit of a driver-shaped `.py` (connection idiom present) that posts
      HTML with the flag but no read-back => BLOCK; product-code-shaped content
      (self.message_post, no connection idiom) => PASS (no false positive).
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


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# (f) Write/Edit of a driver-shaped file (direct payload) -- read-back.
# --------------------------------------------------------------------------- #
class TestWriteEditReadback(unittest.TestCase):
    def test_write_driver_flag_no_readback_blocked(self):
        rc, err = _run_hook(DRIVER_FLAG_NO_READBACK, "Write")
        self.assertEqual(rc, 2, err)
        self.assertRegex(err.lower(), r"read.?back")

    def test_edit_driver_flag_no_readback_blocked(self):
        rc, err = _run_hook(DRIVER_FLAG_NO_READBACK, "Edit")
        self.assertEqual(rc, 2, err)

    def test_write_driver_flag_readback_allowed(self):
        rc, err = _run_hook(DRIVER_FLAG_READBACK, "Write")
        self.assertEqual(rc, 0, err)

    def test_write_product_code_no_false_positive(self):
        # self.message_post + flag, no connection idiom -> not a driver ->
        # read-back not required -> PASS (no false positive on product code).
        rc, err = _run_hook(PRODUCT_CODE_FLAG_NO_READBACK, "Write")
        self.assertEqual(rc, 0, err)

    def test_bash_inline_flag_no_readback_unchanged(self):
        # An INLINE Bash post with the flag but no read-back stays #915-clean
        # (read-back is a driver-FILE requirement, not an inline-post one).
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
