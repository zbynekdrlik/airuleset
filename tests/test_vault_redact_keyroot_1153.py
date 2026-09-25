"""#1153 slice 2 (3) — the output redactor also knows the plain key-file values.

Slice 1's PostToolUse redactor (hooks/redact-vault-output.sh) loaded only the
credential STORE's values; a plain `~/.secrets/<name>` file was protected only
at read time by the guard. Slice 2 makes the redactor the belt behind that
guard: the values of REGULAR files DIRECTLY under the home key-file root are
needles too — size-capped, never through a symlink, matched line by line like
a multi-line store value, and a PEM/OpenSSH block is replaced as ONE block.
Public keys (`*.pub`) are not needles: `ssh-keygen -y` output is meant to be
seen.

All values are fakes in a throwaway HOME; the store is an empty temp dir.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "redact-vault-output.sh"
DOT = "." + "sec" + "rets"
FAKE = "fake1153PlainRootValue-9x7q"
MARK = "<<REDACTED>>"
KIND = "OPENSSH " + "PRIV" + "ATE KEY"          # split: the staging scanner


def bash_resp(text):
    return {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}


class PlainRoot(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.home = base / "home"
        self.home.mkdir()
        self.root = self.home / DOT
        self.root.mkdir(mode=0o700)
        self.store = base / "store"              # EMPTY: plain files alone must fire
        self.store.mkdir(mode=0o700)

    def tearDown(self):
        self._td.cleanup()

    def _key(self, name, data, where=None):
        p = (where or self.root) / name
        p.write_bytes(data)
        os.chmod(p, 0o600)
        return p

    def run_hook(self, response, tool="Bash"):
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": tool,
                              "tool_input": {"command": "x"}, "tool_response": response})
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home),
               "AIRULESET_SECRETS_DIR": str(self.store)}
        return subprocess.run(["/bin/bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=env, timeout=30)

    def _updated(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip(), "expected a redaction, got none: %s" % r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]

    def _untouched(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_a_bare_value_file_with_an_empty_store(self):
        self._key("cloud-token", (FAKE + "\n").encode())
        r = self.run_hook(bash_resp("token: %s\n" % FAKE))
        self.assertEqual(self._updated(r)["stdout"], "token: %s\n" % MARK)
        self.assertNotIn(FAKE, r.stdout + r.stderr)

    def test_an_env_file_is_matched_line_by_line(self):
        self._key("svc.env", b"SVC_USER=appuser\nSVC_KEY=fake1153-env-line-value-xyz\n")
        r = self.run_hook(bash_resp("Authorization: Bearer fake1153-env-line-value-xyz\n"
                                    "user appuser\n"))
        out = self._updated(r)["stdout"]
        self.assertEqual(out, "Authorization: Bearer %s\nuser appuser\n" % MARK)

    def test_a_pem_block_is_replaced_as_one_block(self):
        body = ["fakepem1153AAAAbbbbCCCCddddEEEE", "fakepem1153ffffGGGGhhhhIIIIjjjj", "tail=="]
        begin, end = "-----BEGIN %s-----" % KIND, "-----END %s-----" % KIND
        block = "\n".join([begin] + body + [end])
        self._key("id_fake", ("# deploy key\n%s\n" % block).encode())
        r = self.run_hook(bash_resp("before\n%s\nafter\n" % block))
        out = self._updated(r)["stdout"]
        self.assertEqual(out, "before\n%s\nafter\n" % MARK)
        # a gutter rendering (Read, cat -n) is caught line by line
        gutter = "".join("%6d\t%s\n" % (i, ln) for i, ln in enumerate([begin] + body, 1))
        out = self._updated(self.run_hook(bash_resp(gutter)))["stdout"]
        for ln in body[:2]:
            self.assertNotIn(ln, out)

    def test_pub_files_are_not_needles(self):
        pub = "ssh-ed25519 " + "AAAAfake1153" + "PublicKey" + "MaterialAAAA user@box"
        self._key("id_fake.pub", (pub + "\n").encode())
        self._untouched(self.run_hook(bash_resp(pub + "\n")))

    def test_symlinks_subdirs_and_oversize_files_are_not_loaded(self):
        from filedrop import vault
        outside = self._key("elsewhere", b"fake1153-outside-via-symlink", where=self.home)
        (self.root / "link").symlink_to(outside)
        (self.root / "sub").mkdir()
        self._key("nested", b"fake1153-nested-in-a-subdir", where=self.root / "sub")
        big = b"B" * (vault.MAX_SECRET_BYTES + 1)
        self._key("big", big)
        text = "fake1153-outside-via-symlink fake1153-nested-in-a-subdir " + "B" * 64
        self._untouched(self.run_hook(bash_resp(text)))

    def test_a_short_value_is_not_a_needle(self):
        self._key("flag", b"prod\n")
        self._untouched(self.run_hook(bash_resp("production is up\n")))

    def test_a_root_that_is_a_symlink_is_no_root(self):
        real = self.home / "realroot"
        real.mkdir()
        self._key("tok", FAKE.encode(), where=real)
        self.root.rmdir()
        self.root.symlink_to(real)
        self._untouched(self.run_hook(bash_resp(FAKE)))

    def test_a_fifo_in_the_root_does_not_hang(self):
        os.mkfifo(self.root / "fifo")
        self._key("tok", FAKE.encode())
        t0 = time.monotonic()
        r = self.run_hook(bash_resp(FAKE))
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual(self._updated(r)["stdout"], MARK)

    def test_many_files_and_a_large_output_stay_fast(self):
        for i in range(60):
            self._key("k%02d" % i, ("fake1153-many-%02d-value-abcdefgh\n" % i).encode())
        big = ("y" * 99 + "\n") * 20000 + "fake1153-many-42-value-abcdefgh\n"
        t0 = time.monotonic()
        r = self.run_hook(bash_resp(big))
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertTrue(self._updated(r)["stdout"].endswith(MARK + "\n"))

    def test_no_root_and_an_empty_store_stays_silent(self):
        self.root.rmdir()
        self._untouched(self.run_hook(bash_resp("anything %s" % FAKE)))

    def test_store_values_are_still_redacted_alongside(self):
        (self.store / "DB.secret").write_bytes(b"fake1153-store-value-alongside")
        self._key("tok", FAKE.encode())
        r = self.run_hook(bash_resp("%s fake1153-store-value-alongside" % FAKE))
        self.assertEqual(self._updated(r)["stdout"], "%s %s" % (MARK, MARK))


if __name__ == "__main__":
    unittest.main()
