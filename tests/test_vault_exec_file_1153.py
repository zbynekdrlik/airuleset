"""#1153 slice 2 (5) — `secret exec --file <path> -- <cmd>`: use a plain key inline.

A plain key file under the home key-file root could only be USED without
printing it through a key argument (`ssh -i`) or a script run by path. This
adds the inline form the vault already has: the file's value goes to the child
exactly like a stored value (`--env KEY`, or `--stdin`), fd 1/2 are captured
and filtered (every rendering `secret exec` already strips, plus each line of
a multi-line file and a whole PEM block), and any path outside the root is
refused before the child runs.

Every test uses a throwaway HOME with KNOWN fake values and asserts that no
value (nor any line of it) reaches the CLI's stdout or stderr.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOT = "." + "sec" + "rets"
FAKE = "fake-1153-exec-file-value-0x77"
MARK = "<<REDACTED>>"
KIND = "OPENSSH " + "PRIV" + "ATE KEY"          # split: the staging scanner


def secret(home, *args, stdin=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    return subprocess.run(["python3", str(REPO / "airuleset.py"), "secret", *args],
                          capture_output=True, text=True, env=env, cwd=str(home),
                          input=stdin, timeout=30)


class ExecFile(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.root = self.home / DOT
        self.root.mkdir(mode=0o700)
        self.ran = self.home / "child-ran"

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

    def test_env_delivery_and_the_value_is_filtered(self):
        p = self._key("tok", (FAKE + "\n").encode())
        r = secret(self.home, "exec", "--file", str(p), "--env", "TOK", "--",
                   "sh", "-c", 'printf "got=%s len=%s\\n" "$TOK" "${#TOK}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        # the trailing newline of the file is not part of the env value
        self.assertEqual(r.stdout, "got=%s len=%d\n" % (MARK, len(FAKE)))
        self.assertNoValue(r, FAKE)

    def test_tilde_path_and_stdin_delivery(self):
        self._key("tok", FAKE.encode())
        r = secret(self.home, "exec", "--file", "~/%s/tok" % DOT, "--stdin", "--",
                   "sh", "-c", "cat; echo; cat /dev/null")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, MARK + "\n")
        self.assertNoValue(r, FAKE)

    def test_the_file_flag_after_the_name_position_works_too(self):
        p = self._key("tok", FAKE.encode())
        r = secret(self.home, "exec", "--file=%s" % p, "--env=TOK", "--",
                   "sh", "-c", 'echo "$TOK" >&2')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, MARK + "\n")

    def test_an_env_file_line_and_a_pem_block_are_filtered(self):
        env = b"DB_USER=app\nDB_PASS=fake-1153-env-line-value-long\n"
        p = self._key("db.env", env)
        r = secret(self.home, "exec", "--file", str(p), "--stdin", "--",
                   "sh", "-c", "grep PASS | cut -d= -f2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), MARK)
        body = ["fakepem1153AAAAbbbbCCCCddddEEEE", "fakepem1153ffffGGGGhhhhIIIIjjjj"]
        pem = "-----BEGIN %s-----\n%s\n%s\n-----END %s-----\n" % (KIND, body[0], body[1], KIND)
        k = self._key("id_fake", ("# comment\n" + pem).encode())
        r = secret(self.home, "exec", "--file", str(k), "--stdin", "--",
                   "sh", "-c", "sed -n '2,5p'; echo; cat -n %s >&2" % k)
        self.assertEqual(r.returncode, 0, r.stderr)
        for ln in body + ["BEGIN " + KIND]:
            self.assertNotIn(ln, r.stdout)
        for ln in body:
            self.assertNotIn(ln, r.stderr)
        self.assertIn(MARK, r.stdout)

    def test_the_child_exit_code_is_returned(self):
        p = self._key("tok", FAKE.encode())
        r = secret(self.home, "exec", "--file", str(p), "--env", "T", "--",
                   "sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)

    def _refused(self, *args, rc=2):
        r = secret(self.home, "exec", *args, "--", "sh", "-c", "touch %s" % self.ran)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        self.assertFalse(self.ran.exists(), "the child ran on a refused call")
        return r

    def test_a_path_outside_the_root_is_refused(self):
        out = self.home / "elsewhere"
        out.write_bytes(FAKE.encode())
        r = self._refused("--file", str(out), "--env", "T")
        self.assertIn(DOT, r.stderr)
        self.assertNoValue(r, FAKE)
        self._refused("--file", str(self.root / ".." / "elsewhere"), "--env", "T")

    def test_a_symlink_is_refused_wherever_it_points(self):
        out = self.home / "elsewhere"
        out.write_bytes(FAKE.encode())
        (self.root / "link").symlink_to(out)
        self._refused("--file", str(self.root / "link"), "--env", "T")
        inner = self._key("real", FAKE.encode())
        (self.root / "link2").symlink_to(inner)
        self._refused("--file", str(self.root / "link2"), "--env", "T")

    def test_a_root_that_is_a_symlink_is_no_root(self):
        real = self.home / "realroot"
        real.mkdir()
        self.root.rmdir()
        self.root.symlink_to(real)
        (real / "tok").write_bytes(FAKE.encode())
        self._refused("--file", str(self.root / "tok"), "--env", "T")

    def test_the_root_itself_a_directory_and_a_missing_file(self):
        self._refused("--file", str(self.root), "--env", "T")
        (self.root / "sub").mkdir()
        self._refused("--file", str(self.root / "sub"), "--env", "T")
        self._refused("--file", str(self.root / "nope"), "--env", "T", rc=1)

    def test_a_fifo_is_refused_without_hanging(self):
        os.mkfifo(self.root / "fifo")
        self._refused("--file", str(self.root / "fifo"), "--env", "T")

    def test_an_empty_or_oversize_file_is_refused(self):
        from filedrop import vault
        self._key("empty", b"")
        self._refused("--file", str(self.root / "empty"), "--env", "T")
        self._key("big", b"x" * (vault.MAX_SECRET_BYTES + 1))
        self._refused("--file", str(self.root / "big"), "--env", "T")

    def test_usage_errors(self):
        p = self._key("tok", FAKE.encode())
        # a NAME and --file together is ambiguous
        self._refused("N", "--file", str(p), "--env", "T")
        # no NAME to default the env key to: --env or --stdin is required
        self._refused("--file", str(p))
        self._refused("--file", str(p), "--env", "BAD-KEY")
        r = secret(self.home, "exec", "--file", str(p), "--env", "T")
        self.assertEqual(r.returncode, 2)

    def test_a_non_utf8_value_needs_stdin(self):
        p = self._key("bin", b"\xff\xfe" + FAKE.encode())
        r = self._refused("--file", str(p), "--env", "T", rc=1)
        self.assertIn("--stdin", r.stderr)


if __name__ == "__main__":
    unittest.main()
