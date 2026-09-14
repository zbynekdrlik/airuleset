"""#1020 -- fast, subprocess-free unit tests for the secret classifier that
used to live embedded in block-sensitive-staging.sh. The end-to-end behaviour
(git diff scanning, blocking) stays covered by test_block_staged_content_values.py
via the thin bash adapter; this file locks the pure classifier at the module
level so a regression is caught in milliseconds without spawning a shell.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import secrets                                  # noqa: E402


class TestScanLine(unittest.TestCase):
    def test_prefixed_token_blocks(self):
        self.assertEqual(
            secrets.scan_line("token = ghp_" + "A" * 30),
            "high-confidence secret token prefix",
        )

    def test_sk_ant_key_blocks(self):
        self.assertEqual(
            secrets.scan_line("sk-ant-api03-" + "a" * 40),
            "high-confidence secret token prefix",
        )

    def test_sk_kebab_css_class_not_flagged(self):
        # #1003 review-2 F2: a hyphenated sk- BEM/CSS class is not a token.
        self.assertIsNone(secrets.scan_line("class='sk-loading-indicator-wrapper'"))

    def test_sshpass_literal(self):
        # Assemble the value at runtime so this test file's own committed source
        # carries no scannable secret literal (repo #981 split convention).
        pw = "hunter2pw"
        self.assertEqual(
            secrets.scan_line("sshpass -p '%s'" % pw),
            "sshpass literal password",
        )

    def test_kv_literal_value(self):
        val = "s3cr3tvalue"
        self.assertEqual(
            secrets.scan_line('password: "%s"' % val),
            "literal password value",
        )

    def test_kv_placeholder_not_flagged(self):
        self.assertIsNone(secrets.scan_line('password: "$MY_PASSWORD"'))

    # A varied 40-hex (a realistic git object id), NOT a single-char filler run
    # (which is_placeholder catches as filler before the SHA logic runs).
    _SHA40 = "0123456789abcdef0123456789abcdef01234567"

    def test_bare_git_sha_not_flagged(self):
        # #1003: a bare 40-hex git object id under a SHA-context key reads inert.
        self.assertIsNone(secrets.scan_line('"head_sha": "' + self._SHA40 + '"'))

    def test_40hex_under_credential_key_still_blocks(self):
        # #1003 review F1: a 40-hex under a secret key is a credential.
        self.assertEqual(
            secrets.scan_line('"token": "' + self._SHA40 + '"'),
            "40+ char hex blob (possible key/token)",
        )

    def test_hex_blob_blocks(self):
        # 50 varied hex chars (not a git-object length, not filler).
        self.assertEqual(
            secrets.scan_line("value=" + "0123456789" * 5),
            "40+ char hex blob (possible key/token)",
        )

    def test_clean_line(self):
        self.assertIsNone(secrets.scan_line("just a normal line of prose"))


class TestIsPlaceholder(unittest.TestCase):
    def test_env_ref(self):
        self.assertTrue(secrets.is_placeholder("$SECRET"))

    def test_angle_placeholder(self):
        self.assertTrue(secrets.is_placeholder("<your-token>"))

    def test_repeated_filler(self):
        self.assertTrue(secrets.is_placeholder("xxxxxxxx"))

    def test_real_value(self):
        self.assertFalse(secrets.is_placeholder("s3cr3tRealValue123"))


class TestFilenameViolation(unittest.TestCase):
    def test_env_file(self):
        self.assertEqual(secrets.filename_violation("git add .env"), ".env")

    def test_env_example_allowed(self):
        self.assertEqual(secrets.filename_violation("git add .env.example"), "")

    def test_pem(self):
        self.assertEqual(secrets.filename_violation("git add certs/server.pem"),
                         "certs/server.pem")

    def test_targets_md(self):
        self.assertEqual(secrets.filename_violation("git add TARGETS.md"), "TARGETS.md")

    def test_credential_in_name(self):
        self.assertEqual(secrets.filename_violation("git add my-credentials.json"),
                         "my-credentials.json")

    def test_flag_and_normal_file_ok(self):
        self.assertEqual(secrets.filename_violation("git add -A src/main.py"), "")

    def test_stops_at_separator(self):
        # The words after a separator (a commit message) must not false-trip.
        self.assertEqual(
            secrets.filename_violation("git add src/x.py && git commit -m 'add secret handling'"),
            "",
        )


class TestBypassReason(unittest.TestCase):
    def test_bare_marker(self):
        self.assertEqual(
            secrets.bypass_reason("git add x # airuleset:secret-ok not a real key"),
            "not a real key",
        )

    def test_marker_inside_quotes_does_not_bypass(self):
        self.assertEqual(
            secrets.bypass_reason(
                "git commit -m 'docs: mention # airuleset:secret-ok syntax'"),
            "",
        )

    def test_no_marker(self):
        self.assertEqual(secrets.bypass_reason("git add x"), "")


if __name__ == "__main__":
    unittest.main()
