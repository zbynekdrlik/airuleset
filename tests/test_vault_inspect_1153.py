"""#1153 (b) — `airuleset.py secret inspect <path>`: a key file's FORMAT without its VALUE.

The odoo-erp 8236 leak happened "while checking the format of the key file":
the only format check that existed needed the value. `inspect` answers every
format question a lane actually has — size, lines, trailing newline, CRLF,
`NAME=` shape and the variable NAMES, a short hash to compare two copies,
owner and mode — and never the value. It refuses any path outside the home
key-file root or the credential store.

Every test uses a throwaway HOME with a KNOWN fake value and asserts the
value (and every line of it) is absent from stdout AND stderr.
"""
import hashlib
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOT = "." + "sec" + "rets"
FAKE = "fake-1153-value-not-a-key"   # never a real key
FAKE2 = "second-fake-1153-value-abcdef"


def inspect(home, *args, extra_env=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["python3", str(REPO / "airuleset.py"), "secret", "inspect", *args],
                          capture_output=True, text=True, env=env, cwd=str(home))


class InspectReports(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.root = self.home / DOT
        self.root.mkdir(mode=0o700)

    def tearDown(self):
        self._td.cleanup()

    def _key(self, name, data, mode=0o600):
        p = self.root / name
        p.write_bytes(data)
        os.chmod(p, mode)
        return p

    def assertNoValue(self, r, *values):
        for v in values:
            self.assertNotIn(v, r.stdout)
            self.assertNotIn(v, r.stderr)

    def test_a_bare_value_file(self):
        data = (FAKE + "\n").encode()
        p = self._key("montalu_handover.env", data)
        r = inspect(self.home, str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNoValue(r, FAKE, FAKE[:12], FAKE[-12:])
        out = r.stdout
        self.assertIn("bytes: %d" % len(data), out)
        self.assertIn("lines: 1", out)
        self.assertIn("trailing_newline: yes", out)
        self.assertIn("crlf: no", out)
        self.assertIn("name_shape: no", out)
        self.assertIn("sha256_12: %s" % hashlib.sha256(data).hexdigest()[:12], out)
        self.assertNotIn(hashlib.sha256(data).hexdigest(), out)   # a PREFIX only
        self.assertIn("mode: 0600", out)
        self.assertIn("owner: %s" % pwd.getpwuid(os.getuid()).pw_name, out)

    def test_a_tilde_path_is_accepted(self):
        self._key("k", FAKE.encode())
        r = inspect(self.home, "~/%s/k" % DOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("trailing_newline: no", r.stdout)
        self.assertNoValue(r, FAKE)

    def test_a_name_equals_file_reports_names_only(self):
        data = ("# comment\nexport ODOO_KEY=%s\nODOO_URL=%s\n\n" % (FAKE, FAKE2)).encode()
        p = self._key("pair.env", data)
        r = inspect(self.home, str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name_shape: yes", r.stdout)
        self.assertIn("ODOO_KEY", r.stdout)
        self.assertIn("ODOO_URL", r.stdout)
        self.assertNoValue(r, FAKE, FAKE2)

    def test_crlf_is_reported(self):
        p = self._key("win.env", (FAKE + "\r\n").encode())
        r = inspect(self.home, str(p))
        self.assertIn("crlf: yes", r.stdout)
        self.assertNoValue(r, FAKE)

    def test_a_base64_value_is_not_mistaken_for_a_name(self):
        # `abc==` / `abc=` must not report `abc` as a variable NAME — that
        # would print most of the value under a "names" label.
        for val in ("QWxhZGRpbjpvcGVuIHNlc2FtZQ==", "QWxhZGRpbjpvcGVuIHNlc2FtZQo=",
                    "MZXW6YTBOIFAKEVALUEA="):
            with self.subTest(val=val):
                p = self._key("b64", (val + "\n").encode())
                r = inspect(self.home, str(p))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("name_shape: no", r.stdout)
                self.assertNoValue(r, val, val[:10])

    def test_a_store_value_file_is_accepted(self):
        store = Path(tempfile.mkdtemp(dir=self._td.name))
        (store / "DB_PASS.secret").write_bytes(FAKE.encode())
        r = inspect(self.home, str(store / "DB_PASS.secret"),
                    extra_env={"AIRULESET_SECRETS_DIR": str(store)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNoValue(r, FAKE)
        self.assertIn("bytes: %d" % len(FAKE), r.stdout)


class InspectRefuses(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        (self.home / DOT).mkdir(mode=0o700)

    def tearDown(self):
        self._td.cleanup()

    def test_a_path_outside_both_roots(self):
        other = self.home / "notes.txt"
        other.write_text(FAKE)
        r = inspect(self.home, str(other))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertNotIn(FAKE, r.stdout + r.stderr)
        self.assertNotIn("bytes:", r.stdout)

    def test_a_symlink_escaping_the_root(self):
        outside = self.home / "outside.txt"
        outside.write_text(FAKE)
        link = self.home / DOT / "link"
        link.symlink_to(outside)
        r = inspect(self.home, str(link))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertNotIn("bytes:", r.stdout)

    def test_dotdot_escape(self):
        outside = self.home / "outside.txt"
        outside.write_text(FAKE)
        r = inspect(self.home, str(self.home / DOT / ".." / "outside.txt"))
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_a_directory_and_a_missing_file(self):
        r = inspect(self.home, str(self.home / DOT))
        self.assertNotEqual(r.returncode, 0)
        r = inspect(self.home, str(self.home / DOT / "absent"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("bytes:", r.stdout)

    def test_no_path(self):
        r = inspect(self.home)
        self.assertEqual(r.returncode, 2)


class Wiring(unittest.TestCase):
    def test_inspect_is_a_secret_action(self):
        import cli_vault
        self.assertIn("inspect", cli_vault.SECRET_ACTIONS)


if __name__ == "__main__":
    unittest.main()
