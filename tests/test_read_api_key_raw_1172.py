"""#1172 — `cli_odoo_ro.read_api_key` accepts the RAW single-line key files the
stream boxes hold (no `NAME=`), keeps `NAME=value` parsing unchanged, and never
echoes the value. Fake values only; every file is a temp file."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_odoo_ro as ro  # noqa: E402


class TestReadApiKeyRawFile(unittest.TestCase):
    """#1172 — the stream key files are RAW one-line values (no `NAME=`). A file
    whose ONLY non-comment, non-blank line has no `=` is a raw key file; every
    other shape keeps the `NAME=value` parsing and its error."""

    FAKE = "fake-raw-key-1172"

    def _env(self, text):
        d = tempfile.mkdtemp(prefix="i1172-env-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        p = os.path.join(d, "handover-key")
        with open(p, "w", encoding="utf-8") as h:
            h.write(text)
        return p

    def _read_capturing(self, p, var="ODOO_API_KEY"):
        import contextlib
        import io
        import logging
        err, out = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(err)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old_level)
        exc = None
        val = None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                val = ro.read_api_key(p, var)
            except ro.OdooError as e:
                exc = e
        return val, exc, err.getvalue() + out.getvalue()

    def test_raw_single_line_returns_value(self):
        p = self._env(self.FAKE + "\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), self.FAKE)

    def test_raw_single_line_without_trailing_newline(self):
        p = self._env("  " + self.FAKE + "  ")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), self.FAKE)

    def test_raw_with_comments_and_blank_lines_ignored(self):
        p = self._env("# montalu handover key\n\n" + self.FAKE + "\n\n# end\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), self.FAKE)

    def test_raw_file_ignores_configured_var_name(self):
        # a raw file carries no name — any configured api_key_var reads it
        p = self._env(self.FAKE + "\n")
        self.assertEqual(ro.read_api_key(p, "SOME_OTHER_VAR"), self.FAKE)

    def test_raw_via_client_from_config(self):
        p = self._env(self.FAKE + "\n")
        cfg = {"instance_url": "https://erp.example.invalid",
               "api_key_env_file": p}
        client = ro.client_from_config(cfg, transport=lambda *a, **k: None)
        self.assertIsInstance(client, ro.OdooReadOnlyClient)
        self.assertNotIn(self.FAKE, repr(client))
        self.assertNotIn(self.FAKE, str(client))

    def test_name_value_file_unchanged(self):
        p = self._env("# c\nODOO_API_KEY=abc123\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), "abc123")

    def test_single_name_value_line_for_other_var_still_raises(self):
        # ONE non-comment line WITH `=` is an env file, never a raw key
        p = self._env("OTHER_VAR=" + self.FAKE + "\n")
        val, exc, captured = self._read_capturing(p)
        self.assertIsNone(val)
        self.assertIsInstance(exc, ro.OdooError)
        self.assertIn("not found", str(exc))
        self.assertNotIn(self.FAKE, str(exc))
        self.assertNotIn(self.FAKE, captured)

    def test_raw_two_lines_raises_existing_error(self):
        p = self._env(self.FAKE + "\nsecondRawLine\n")
        val, exc, captured = self._read_capturing(p)
        self.assertIsNone(val)
        self.assertIsInstance(exc, ro.OdooError)
        self.assertIn("not found", str(exc))
        self.assertNotIn(self.FAKE, str(exc))
        self.assertNotIn(self.FAKE, repr(exc))
        self.assertNotIn("secondRawLine", str(exc))
        self.assertNotIn(self.FAKE, captured)

    def test_raw_line_plus_name_value_line_is_env_file(self):
        p = self._env(self.FAKE + "\nODOO_API_KEY=abc123\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), "abc123")

    def test_raw_value_never_printed_or_logged(self):
        p = self._env(self.FAKE + "\n")
        val, exc, captured = self._read_capturing(p)
        self.assertIsNone(exc)
        self.assertEqual(val, self.FAKE)
        self.assertEqual(captured, "")

    def test_comment_only_file_raises(self):
        p = self._env("# only a comment\n\n")
        with self.assertRaises(ro.OdooError):
            ro.read_api_key(p, "ODOO_API_KEY")

    def test_zero_byte_file_raises(self):
        p = self._env("")
        with self.assertRaises(ro.OdooError):
            ro.read_api_key(p, "ODOO_API_KEY")

    def _env_bytes(self, data):
        p = self._env("")
        with open(p, "wb") as h:
            h.write(data)
        return p

    def test_utf8_bom_is_not_part_of_the_raw_key(self):
        p = self._env_bytes(b"\xef\xbb\xbf" + self.FAKE.encode() + b"\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), self.FAKE)

    def test_utf8_bom_env_file_reads_named_var(self):
        line = "%s=%s\n" % (ro.DEFAULT_API_KEY_VAR, self.FAKE)
        p = self._env_bytes(b"\xef\xbb\xbf" + line.encode())
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), self.FAKE)

    def test_undecodable_file_raises_structured_error_without_content(self):
        p = self._env_bytes(self.FAKE.encode() + b"\xff\xfe-tail\n")
        val, exc, captured = self._read_capturing(p)
        self.assertIsNone(val)
        self.assertIsInstance(exc, ro.OdooError)
        self.assertIn("cannot read", str(exc))
        self.assertNotIn(self.FAKE, str(exc))
        self.assertNotIn(self.FAKE, repr(exc))
        self.assertNotIn(self.FAKE, captured)
        # the decode error (whose .object holds the file bytes) is not chained
        self.assertIsNone(exc.__cause__)
        self.assertTrue(exc.__suppress_context__)

    def test_template_documents_both_key_file_shapes(self):
        tmpl = ro.config_template()
        self.assertIn("NAME=value", tmpl)
        self.assertIn("raw", tmpl.lower())
        # still a valid, parseable config
        d = tempfile.mkdtemp(prefix="i1172-tmpl-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        cp = os.path.join(d, "odoo-task-tracking.json")
        with open(cp, "w", encoding="utf-8") as h:
            h.write(tmpl)
        ok, missing = ro.config_valid(ro.load_config(cp))
        self.assertTrue(ok, missing)


if __name__ == "__main__":
    unittest.main()
