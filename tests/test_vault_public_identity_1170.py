"""#1170 — a credential value that is part of the box's PUBLIC identity is never a needle.

On dev1 four plain key files held the 8-byte account word. The #1153
redactor used every 8+-byte value as a needle anywhere in any tool output, so
every `https://drop-dev1.<zone>/<token>/` URL (secret show / share / upload)
and every `/home/<account>/…` path reached the session masked, and the
one-shot credential URL flow could not be delivered.

The fix: a needle that occurs (case-insensitively) inside a public identity
string of this box — the account name, $HOME, the host name, every
name/host/user of the fleet registry, the fleet's drop hosts (which carry the
public zone) — is dropped from the needle set, in ONE place used by both the
PostToolUse redactor and `secret exec --file`, and each drop is logged by NAME
only. A genuinely secret value stays a needle.

All values are fakes in a throwaway HOME; nothing reads a real key file.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import cli_drop_lanes
import cli_fleet

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "redact-vault-output.sh"
DOT = "." + "sec" + "rets"
MARK = "<<REDACTED>>"
ACCOUNT = "zq1170acct"                       # a fake account word (10 bytes)
GENUINE = "fake1170-genuine-value-7q"         # a genuine credential: never public
# The fleet's public zone, derived from the drop-host builder (never a literal).
ZONE = cli_drop_lanes._generated_drop_host("box", "user", False).split(".", 1)[1]
ZONE_WORD = ZONE.split(".", 1)[0]            # the brand/account word of the zone
DROP_URL = "https://drop-dev1.%s/tok1170abc/" % ZONE
LOG_NAME = "public-identity.log"


def bash_resp(text):
    return {"stdout": text, "stderr": "", "interrupted": False, "isImage": False}


def fleet_hostname():
    """A real, non-IP host of the fleet registry (a public DNS name)."""
    for e in cli_fleet.REMOTE_HOSTS:
        host = e.get("host", "")
        if isinstance(host, str) and not host.replace(".", "").isdigit():
            return host
    raise AssertionError("the fleet registry has no DNS-named host to test with")


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.home = base / "home"
        self.home.mkdir()
        self.root = self.home / DOT
        self.root.mkdir(mode=0o700)
        self.store = base / "store"
        self.store.mkdir(mode=0o700)

    def tearDown(self):
        self._td.cleanup()

    def env(self):
        return {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "USER": ACCOUNT,
                "LOGNAME": ACCOUNT, "AIRULESET_SECRETS_DIR": str(self.store)}

    def key(self, name, value):
        p = self.root / name
        p.write_bytes((value + "\n").encode())
        os.chmod(p, 0o600)
        return p

    def log_text(self):
        p = self.home / ".claude" / "secret-logs" / LOG_NAME
        return p.read_text() if p.exists() else ""


class Redactor(Base):
    def run_hook(self, text):
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                              "tool_input": {"command": "x"},
                              "tool_response": bash_resp(text)})
        r = subprocess.run(["/bin/bash", str(HOOK)], input=payload, capture_output=True,
                           text=True, env=self.env(), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_the_account_word_does_not_mask_a_home_path_and_is_logged_by_name(self):
        self.key("fohabl-admin-password", ACCOUNT)
        r = self.run_hook("cd /home/%s/devel && ls\n" % ACCOUNT)
        self.assertEqual(r.stdout.strip(), "", "a public value was redacted")
        log = self.log_text()
        self.assertIn("fohabl-admin-password", log)
        self.assertIn("public-identity", log)
        self.assertNotIn(ACCOUNT, log, "the log must never carry the value")

    def test_the_zone_word_does_not_mask_a_drop_url(self):
        self.key("netcfg-switch-pw", ZONE_WORD)
        r = self.run_hook("open %s now\n" % DROP_URL)
        self.assertEqual(r.stdout.strip(), "", "the drop URL was masked")
        self.assertIn("netcfg-switch-pw", self.log_text())

    def test_a_genuine_secret_is_still_redacted_next_to_a_public_value(self):
        self.key("resolume-password", ZONE_WORD)
        self.key("cloud-token", GENUINE)
        r = self.run_hook("url %s home /home/%s token %s\n" % (DROP_URL, ACCOUNT, GENUINE))
        out = json.loads(r.stdout)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        self.assertEqual(out, "url %s home /home/%s token %s\n" % (DROP_URL, ACCOUNT, MARK))
        self.assertNotIn(GENUINE, r.stdout + r.stderr + self.log_text())

    def test_a_value_equal_to_a_fleet_hostname_is_dropped(self):
        host = fleet_hostname()
        self.key("fleet-host-file", host)
        r = self.run_hook("ssh admin@%s uptime\n" % host)
        self.assertEqual(r.stdout.strip(), "", "a fleet hostname was masked")
        self.assertIn("fleet-host-file", self.log_text())

    def test_the_drop_is_logged_once_not_per_tool_call(self):
        self.key("fohabl-admin-user", ACCOUNT)
        for _ in range(3):
            self.run_hook("/home/%s/x\n" % ACCOUNT)
        lines = [ln for ln in self.log_text().splitlines() if "fohabl-admin-user" in ln]
        self.assertEqual(len(lines), 1, lines)

    def test_case_is_ignored(self):
        self.key("shouty", ZONE_WORD.capitalize())
        r = self.run_hook("open %s\n" % DROP_URL.replace(ZONE_WORD, ZONE_WORD.capitalize()))
        self.assertEqual(r.stdout.strip(), "")


class ExecFile(Base):
    def secret(self, *args):
        return subprocess.run(["python3", str(REPO / "airuleset.py"), "secret", *args],
                               capture_output=True, text=True, env=self.env(),
                               cwd=str(self.home), timeout=30)

    def test_exec_file_uses_the_same_filter(self):
        p = self.key("netcfg-switch-pw", ZONE_WORD)
        r = self.secret("exec", "--file", str(p), "--env", "PW", "--",
                        "sh", "-c", 'echo "%s /home/%s"' % (DROP_URL, ZONE_WORD))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "%s /home/%s\n" % (DROP_URL, ZONE_WORD))
        self.assertIn("netcfg-switch-pw", self.log_text())

    def test_exec_file_still_redacts_a_genuine_secret(self):
        p = self.key("cloud-token", GENUINE)
        r = self.secret("exec", "--file", str(p), "--env", "TOK", "--",
                        "sh", "-c", 'echo "%s $TOK"' % DROP_URL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "%s %s\n" % (DROP_URL, MARK))
        self.assertNotIn(GENUINE, r.stdout + r.stderr)


class Identity(unittest.TestCase):
    """The identity set itself, with FAKE fleet data (no real registry)."""
    FLEET = [{"name": "u9@fakebox", "host": "fakebox.example.net", "user": "u9"},
             {"name": "lonebox", "host": "100.64.9.9", "user": "svcacct9x"}]

    def ident(self, strings=None, lazy=()):
        import cli_vault_public as pub
        base = pub.fleet_identity(self.FLEET) if strings is None else strings
        return pub.PublicIdentity(strings=base, lazy_hosts=lambda: list(lazy))

    def test_a_fleet_hostname_user_and_generated_drop_host_are_public(self):
        ident = self.ident()
        for needle in (b"fakebox.example.net", b"FakeBox.Example.NET", b"svcacct9x",
                       b"drop-fakebox", b"drop-lonebox"):
            self.assertTrue(ident.contains(needle), needle)

    def test_a_secret_that_merely_shares_a_prefix_is_not_public(self):
        ident = self.ident()
        self.assertFalse(ident.contains(b"fakebox.example.net-supersecret"))
        self.assertFalse(ident.contains(b"svcacct9x!Pass"))

    def test_short_or_empty_identity_strings_never_exempt_a_secret(self):
        ident = self.ident(strings=["", "/", " ", "a"])
        self.assertFalse(ident.contains(b"zzsecret-value"))
        self.assertFalse(ident.contains(b"a" * 8))

    def test_a_needle_under_eight_bytes_is_never_exempted(self):
        ident = self.ident(strings=["verylonghostname.example.org"])
        self.assertFalse(ident.contains(b"host"))
        self.assertFalse(ident.contains(b""))

    def test_a_multi_line_needle_is_never_public(self):
        ident = self.ident(strings=["alpha.example.org", "beta.example.org"])
        self.assertFalse(ident.contains(b"alpha.example.org\nbeta.example.org"))

    def test_lazy_hosts_are_consulted_only_for_a_hostname_shaped_needle(self):
        calls = []

        def lazy():
            calls.append(1)
            return ["drop-seedbox.example.org"]

        import cli_vault_public as pub
        ident = pub.PublicIdentity(strings=["unrelated.example.org"], lazy_hosts=lazy)
        self.assertFalse(ident.contains(b"Pa$$w0rd!!xyz"))
        self.assertEqual(calls, [], "a non-hostname needle loaded the lane registry")
        self.assertTrue(ident.contains(b"drop-seedbox"))
        self.assertTrue(ident.contains(b"seedbox.example"))
        self.assertEqual(len(calls), 1, "the lane registry must load at most once")

    def test_a_failing_lane_registry_keeps_the_needle(self):
        import cli_vault_public as pub

        def boom():
            raise RuntimeError("registry broken")

        ident = pub.PublicIdentity(strings=[], lazy_hosts=boom)
        self.assertFalse(ident.contains(b"drop-seedbox"))

    def test_the_default_identity_carries_the_zone_and_this_account(self):
        import cli_vault_public as pub
        old = os.environ.get("USER")
        os.environ["USER"] = ACCOUNT
        try:
            ident = pub.PublicIdentity(lazy_hosts=lambda: [])
        finally:
            if old is None:
                del os.environ["USER"]
            else:
                os.environ["USER"] = old
        self.assertTrue(ident.contains(ZONE.encode()))
        self.assertTrue(ident.contains(ACCOUNT.encode()))
        self.assertFalse(ident.contains(GENUINE.encode()))


if __name__ == "__main__":
    unittest.main()
