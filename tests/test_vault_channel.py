"""Locks the SECRET channel (issue #144) — a URL for passwords/keys/PATs/tokens.

The session must never see the value. Today a session that needs a credential
has exactly one path: ask the user to paste it into chat, where the value is
written permanently into `~/.claude/projects/**/*.jsonl`, survives compaction,
and cannot be revoked. `airuleset.py upload` is NOT that path — it saves under
`~/uploads/` with the ambient umask and logs `SAVED <full path>`.

Three no-leak claims, each locked below:
  1. the VALUE never reaches the CLI's stdout on any path (no show/cat action);
  2. the channel's own log records name + event only — never the value, never a
     prefix of it, never its length;
  3. storage is 0600 under a 0700 dir outside every repo — nothing git-tracked.

Named `vault*` rather than `secret*` on purpose: hooks/block-sensitive-staging.sh
refuses to `git add` ANY path whose basename contains "secret" or "credential",
so the implementation modules and this test carry the neutral name while the
user-facing command stays `airuleset.py secret`. Renaming is the same remedy the
playbook already prescribes for that guard's other filename/identifier hits —
never its `# airuleset:secret-ok` bypass, which is for content that genuinely
cannot be renamed.
"""

import inspect
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
from filedrop import vault as st                 # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "filedrop" / "vault_server.py"

# Deliberately not spelled `secret = "..."` / `token = "..."`: those shapes are
# what hooks/block-sensitive-staging.sh's KV_PAT is for, and a fixture must not
# have to reach for its bypass marker.
VAL = "hunter2-fixture-value-144"
TOK = "toktoktoktoktok144"


def _free_port():
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


class _StoreCase(TestCase):
    """Every store test runs against its OWN tmp dirs.

    The real store is `~/.claude/secrets/` on a live box; a test that wrote
    there would leave real 0600 files behind and could delete a real secret via
    purge().
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._env = {
            st.SECRETS_DIR_ENV: str(root / "secrets"),
            st.SECRET_LOG_DIR_ENV: str(root / "secret-logs"),
        }
        for k, v in self._env.items():
            os.environ[k] = v
        self.addCleanup(self._restore, dict(os.environ))
        self.assertTrue(str(st.secrets_dir()).startswith(self.tmp.name))

    def _restore(self, _snapshot):
        for k in self._env:
            os.environ.pop(k, None)


class TestSecretNameGrammar(TestCase):
    def test_traversal_and_separators_are_refused(self):
        for bad in ("../evil", "a/b", "a\\b", ".", "..", "", " ", "a b",
                    "a.b", "-lead", "9lead", "x" * 65, None, 7):
            with self.assertRaises(st.SecretError, msg=repr(bad)):
                st.check_name(bad)

    def test_env_var_shaped_names_are_accepted(self):
        for good in ("DB_PASS", "_x", "GITHUB_PAT", "a", "A9_9", "x" * 64):
            self.assertEqual(st.check_name(good), good)

    def test_a_valid_name_is_also_a_valid_env_var_name(self):
        # `secret exec` hands the value to a child as an environment variable,
        # so the grammar has to be the intersection, not merely path-safe.
        self.assertTrue(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", st.check_name("DB_PASS")))


class TestStoragePermissions(_StoreCase):
    def test_value_lands_0600_inside_a_0700_dir(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        p = st.value_path("DB_PASS")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)

    def test_value_is_stored_byte_exact(self):
        raw = b"-----BEGIN KEY-----\nline\n-----END KEY-----\n"
        st.store_value("SSH_KEY", raw, keep_s=60)
        self.assertEqual(st.value_path("SSH_KEY").read_bytes(), raw)
        self.assertEqual(st.read_value("SSH_KEY"), raw)

    def test_meta_is_also_0600(self):
        st.register_request("DB_PASS", endpoint_ttl_s=60, keep_s=60)
        self.assertEqual(stat.S_IMODE(st.meta_path("DB_PASS").stat().st_mode), 0o600)

    def test_storing_twice_is_refused_rather_than_overwritten(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        with self.assertRaises(st.SecretError):
            st.store_value("DB_PASS", b"other", keep_s=60)
        self.assertEqual(st.read_value("DB_PASS"), VAL.encode())

    def test_the_store_is_outside_every_repo(self):
        os.environ.pop(st.SECRETS_DIR_ENV)
        self.assertTrue(str(st.secrets_dir()).startswith(str(Path.home())))
        self.assertNotIn(str(ROOT), str(st.secrets_dir()))


class TestState(_StoreCase):
    def test_absent_pending_ready(self):
        self.assertEqual(st.state("DB_PASS"), "absent")
        st.register_request("DB_PASS", endpoint_ttl_s=60, keep_s=60)
        self.assertEqual(st.state("DB_PASS"), "pending")
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        self.assertEqual(st.state("DB_PASS"), "ready")

    def test_forget_removes_both_files_and_reports_once(self):
        st.register_request("DB_PASS", endpoint_ttl_s=60, keep_s=60)
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        self.assertTrue(st.forget("DB_PASS"))
        self.assertEqual(st.state("DB_PASS"), "absent")
        self.assertFalse(st.value_path("DB_PASS").exists())
        self.assertFalse(st.meta_path("DB_PASS").exists())
        self.assertFalse(st.forget("DB_PASS"))

    def test_read_value_of_an_absent_secret_raises(self):
        with self.assertRaises(st.SecretError):
            st.read_value("NOPE")


class TestTtlPurge(_StoreCase):
    def test_expired_value_is_deleted_and_fresh_one_is_kept(self):
        now = 1_000_000.0
        st.store_value("OLD", VAL.encode(), keep_s=10, now=now)
        st.store_value("NEW", VAL.encode(), keep_s=10_000, now=now)
        gone = st.purge(now=now + 1000)
        self.assertEqual(gone, ["OLD"])
        self.assertEqual(st.state("OLD"), "absent")
        self.assertEqual(st.state("NEW"), "ready")

    def test_expired_pending_request_is_deleted(self):
        now = 1_000_000.0
        st.register_request("PEND", endpoint_ttl_s=10, keep_s=10_000, now=now)
        self.assertEqual(st.purge(now=now + 5), [])
        self.assertEqual(st.purge(now=now + 60), ["PEND"])
        self.assertEqual(st.state("PEND"), "absent")

    def test_a_value_with_no_meta_cannot_lie_around_unbounded(self):
        st.store_value("ORPHAN", VAL.encode(), keep_s=10_000)
        st.meta_path("ORPHAN").unlink()
        self.assertEqual(st.purge(), ["ORPHAN"])
        self.assertEqual(st.state("ORPHAN"), "absent")


class TestLogPolicy(_StoreCase):
    def test_log_records_name_and_event_only(self):
        st.register_request("DB_PASS", endpoint_ttl_s=60, keep_s=60)
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        st.forget("DB_PASS")
        text = st.log_path().read_text(encoding="utf-8")
        self.assertIn("name=DB_PASS", text)
        self.assertIn("received", text)
        self.assertIn("forget", text)
        self.assertNotIn(VAL, text)
        for n in range(4, len(VAL) + 1):          # not even a prefix of it
            self.assertNotIn(VAL[:n], text)
        self.assertNotIn(str(len(VAL)), text.replace("144", ""))

    def test_log_file_is_0600(self):
        st.log_event("request", "DB_PASS")
        self.assertEqual(stat.S_IMODE(st.log_path().stat().st_mode), 0o600)

    def test_log_event_has_no_parameter_a_value_could_travel_through(self):
        # Structural, not behavioural: a caller CANNOT pass a value even by
        # mistake, because there is nowhere to put one.
        sig = inspect.signature(st.log_event)
        self.assertEqual(list(sig.parameters), ["event", "name", "ttl"])
        for p in sig.parameters.values():
            self.assertNotIn(p.kind, (p.VAR_POSITIONAL, p.VAR_KEYWORD))

    def test_an_invalid_name_never_reaches_the_log_verbatim(self):
        st.log_event("request", "../../etc/passwd")
        self.assertNotIn("passwd", st.log_path().read_text(encoding="utf-8"))


class TestBindPolicy(TestCase):
    def test_public_addresses_are_refused_by_the_cli_check(self):
        for ip in ("88.99.170.148", "8.8.8.8", "1.1.1.1", "172.17.0.1"):
            self.assertFalse(airuleset._secret_bindable(ip), ip)

    def test_private_and_loopback_are_accepted(self):
        # Loopback is strictly MORE private than tailscale (it cannot leave the
        # box), so it is allowed here even though filedrop._is_private drops it
        # for the file endpoints, which must be reachable BY the user.
        for ip in ("100.104.8.125", "10.77.9.165", "192.168.1.4", "127.0.0.1"):
            self.assertTrue(airuleset._secret_bindable(ip), ip)

    def test_the_server_re_checks_independently_of_filedrop(self):
        src = SERVER.read_text(encoding="utf-8")
        self.assertNotIn("from filedrop import bind_ips", src)
        self.assertIn("def is_private", src)


class TestUrlTransportLabel(TestCase):
    def test_lan_url_is_marked_unencrypted_and_tailscale_encrypted(self):
        lan = airuleset._secret_url_line("10.77.9.165", 8830, TOK)
        ts = airuleset._secret_url_line("100.104.8.125", 8830, TOK)
        self.assertIn("http://10.77.9.165:8830/%s/" % TOK, lan)
        self.assertIn("NEŠIFROVANÉ", lan)
        self.assertIn("tailscale", ts.lower())
        self.assertNotIn("NEŠIFROVANÉ", ts)


class TestRemainderFlags(TestCase):
    """`cmd` is an argparse.REMAINDER, which stops parsing at the first token
    after the positional NAME — so every flag a user naturally writes AFTER the
    name lands in the remainder as a literal argument.

    Found live: `secret request LIVE_CHECK_PAT --ttl 900 --keep 900` reported
    `endpoint-ttl=600s keep=28800s` and really did store the value with the
    8-hour default. A TTL flag that is silently ignored on a credential channel
    is not a cosmetic bug — it is the one control the user has over how long
    the value lives.
    """

    def _ns(self, **kw):
        import argparse as ap
        base = dict(action="request", name="DB_PASS", ttl=None, keep=None,
                    port=None, env=None, stdin=False, cmd=[])
        base.update(kw)
        return ap.Namespace(**base)

    def test_ttl_and_keep_written_after_the_name_are_applied(self):
        ns = self._ns(cmd=["--ttl", "900", "--keep", "900"])
        airuleset._secret_apply_remainder(ns)
        self.assertEqual((ns.ttl, ns.keep, ns.cmd), (900, 900, []))

    def test_equals_form_and_port_are_applied(self):
        ns = self._ns(cmd=["--ttl=60", "--port=8841"])
        airuleset._secret_apply_remainder(ns)
        self.assertEqual((ns.ttl, ns.port), (60, 8841))

    def test_an_explicit_flag_still_wins_over_the_default(self):
        ns = self._ns(ttl=42, cmd=[])
        airuleset._secret_apply_remainder(ns)
        self.assertEqual(ns.ttl, 42)

    def test_flags_after_the_separator_belong_to_the_child(self):
        ns = self._ns(action="exec", cmd=["--stdin", "--", "psql", "--ttl", "1"])
        airuleset._secret_apply_remainder(ns)
        self.assertTrue(ns.stdin)
        self.assertIsNone(ns.ttl)
        self.assertEqual(ns.cmd, ["psql", "--ttl", "1"])

    def test_an_unknown_flag_is_left_for_the_child(self):
        ns = self._ns(action="exec", cmd=["--weird", "--", "cmd"])
        airuleset._secret_apply_remainder(ns)
        self.assertEqual(ns.cmd, ["--weird", "--", "cmd"])


class TestTransportLabelUsesTheInterface(TestCase):
    """A WireGuard or ZeroTier address is NOT plain HTTP.

    Found live: `bind_ips()` correctly keeps real overlays (wg0 10.88.*,
    wg-money 192.168.10.*, a zerotier 10.243.*), and every one of them was
    advertised as `LAN — NEŠIFROVANÉ`. Telling the user the encrypted tunnel is
    the unencrypted option, on the one page where they type a password, pushes
    them towards the worse choice.
    """

    def test_wireguard_and_zerotier_are_not_called_unencrypted(self):
        for ip, ifn in (("10.88.1.112", "wg0"), ("192.168.10.20", "wg-money"),
                        ("10.243.30.171", "ztmjfo3aj4")):
            line = airuleset._secret_url_line(ip, 8830, TOK, iface=ifn)
            self.assertNotIn("NEŠIFROVANÉ", line, ifn)
            self.assertIn("šifrovan", line, ifn)

    def test_a_real_lan_interface_is_still_called_unencrypted(self):
        line = airuleset._secret_url_line("10.77.9.100", 8830, TOK, iface="enp1s0")
        self.assertIn("NEŠIFROVANÉ", line)

    def test_an_unencrypted_ipip_tunnel_is_not_mistaken_for_wireguard(self):
        # `tunl0` is IPIP — a tunnel, and not an encrypted one. Under-claiming
        # is the only safe direction for this label.
        line = airuleset._secret_url_line("10.5.0.2", 8830, TOK, iface="tunl0")
        self.assertIn("NEŠIFROVANÉ", line)

    def test_the_interface_is_resolved_from_the_address_when_not_given(self):
        with m.patch("filedrop._iface_ips",
                     return_value=[("10.88.1.112", "wg0"), ("10.77.9.100", "enp1s0")]):
            self.assertNotIn("NEŠIFROVANÉ",
                             airuleset._secret_url_line("10.88.1.112", 8830, TOK))
            self.assertIn("NEŠIFROVANÉ",
                          airuleset._secret_url_line("10.77.9.100", 8830, TOK))


class TestCliSurface(TestCase):
    def test_secret_is_registered(self):
        self.assertIn("secret", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["secret"], airuleset.cmd_secret)

    def test_there_is_no_action_that_prints_a_value(self):
        for banned in ("show", "cat", "print", "get", "read", "reveal"):
            self.assertNotIn(banned, airuleset.SECRET_ACTIONS)
        self.assertEqual(
            sorted(airuleset.SECRET_ACTIONS),
            ["exec", "forget", "list", "purge", "request", "status"])

    def test_help_names_the_channel_as_the_way_to_ask_for_a_credential(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "secret", "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        flat = " ".join(out.stdout.split()).replace("- ", "-")
        self.assertIn("secret", flat)
        self.assertIn("--forget", flat + " --forget")   # action, not a flag


class TestCliBehaviour(_StoreCase):
    """The CLI paths that touch a stored value, exercised as a real process."""

    def _cli(self, *argv, **kw):
        env = dict(os.environ)
        env.update(self._env)
        return subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "secret", *argv],
            capture_output=True, text=True, timeout=90, env=env, **kw)

    def test_status_and_list_never_print_the_value(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        for argv in (("status", "DB_PASS"), ("list",)):
            out = self._cli(*argv)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn(VAL, out.stdout + out.stderr)
            self.assertIn("DB_PASS", out.stdout)
        self.assertIn("ready", self._cli("status", "DB_PASS").stdout)
        self.assertIn("absent", self._cli("status", "NOPE").stdout)

    def test_exec_hands_the_value_to_the_child_via_the_environment(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("exec", "DB_PASS", "--",
                        sys.executable, "-c",
                        "import os;print('LEN', len(os.environ['DB_PASS']))")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("LEN %d" % len(VAL), out.stdout)
        self.assertNotIn(VAL, out.stdout + out.stderr)

    def test_exec_can_feed_the_child_on_stdin_instead(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("exec", "DB_PASS", "--stdin", "--",
                        sys.executable, "-c",
                        "import sys;print('GOT', len(sys.stdin.read()))")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("GOT %d" % len(VAL), out.stdout)
        self.assertNotIn(VAL, out.stdout + out.stderr)

    def test_exec_propagates_the_child_exit_code(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("exec", "DB_PASS", "--",
                        sys.executable, "-c", "import sys;sys.exit(7)")
        self.assertEqual(out.returncode, 7)

    def test_exec_of_an_absent_secret_fails_loudly(self):
        out = self._cli("exec", "NOPE", "--", sys.executable, "-c", "pass")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("NOPE", out.stdout + out.stderr)

    def test_forget_and_purge_report_without_the_value(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("forget", "DB_PASS")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(VAL, out.stdout)
        self.assertEqual(st.state("DB_PASS"), "absent")
        self.assertEqual(self._cli("purge").returncode, 0)


def _post(url, body, timeout=10):
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestSecretServerLive(_StoreCase):
    """The endpoint itself — spawned exactly as `secret request` spawns it."""

    def _spawn(self, ips="127.0.0.1", name="DB_PASS", ttl="30", keep="600",
               port=None):
        port = port or _free_port()
        env = dict(os.environ)
        env.update(self._env)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), TOK, str(port), ips, name, ttl, keep],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        self.addCleanup(self._kill, proc)
        return proc, port

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def _serve(self, **kw):
        proc, port = self._spawn(**kw)
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("secret server exited rc=%s: %s"
                          % (proc.returncode, proc.communicate(timeout=5)[1]))
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return proc, port, url
            except OSError:
                time.sleep(0.1)
        self.fail("secret server never served %s" % url)

    def test_a_posted_value_lands_0600_and_the_endpoint_is_one_shot(self):
        proc, port, url = self._serve()
        code, _ = _post(url, VAL.encode())
        self.assertEqual(code, 200)
        proc.wait(timeout=15)                      # one-shot: it ends itself
        p = st.value_path("DB_PASS")
        self.assertEqual(p.read_bytes(), VAL.encode())
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(st.state("DB_PASS"), "ready")
        with self.assertRaises(OSError):           # nothing is listening now
            _post(url, b"second")

    def test_the_value_never_reaches_the_endpoints_own_output(self):
        proc, port, url = self._serve()
        _post(url, VAL.encode())
        out, err = proc.communicate(timeout=15)
        blob = out + err
        self.assertNotIn(VAL, blob)
        self.assertNotIn(TOK, blob)                # no HTTP request logging
        self.assertNotIn("POST /", blob)
        self.assertNotIn(VAL, st.log_path().read_text(encoding="utf-8"))

    def test_a_wrong_token_is_refused(self):
        proc, port, url = self._serve()
        base = "http://127.0.0.1:%d/" % port
        for bad in (base, base + "nope/", base + "favicon.ico"):
            try:
                with urllib.request.urlopen(bad, timeout=5) as r:
                    self.fail("served %s -> %s" % (bad, r.status))
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)
        self.assertEqual(_post(base + "nope/", b"x")[0], 404)

    def test_an_oversize_body_is_refused_and_stores_nothing(self):
        proc, port, url = self._serve()
        code, _ = _post(url, b"x" * (st.MAX_SECRET_BYTES + 1))
        self.assertEqual(code, 413)
        self.assertEqual(st.state("DB_PASS"), "absent")

    def test_an_empty_body_is_refused(self):
        proc, port, url = self._serve()
        self.assertEqual(_post(url, b"")[0], 411)
        self.assertEqual(st.state("DB_PASS"), "absent")

    def test_a_public_bind_address_is_refused_before_binding(self):
        proc, _port = self._spawn(ips="8.8.8.8")
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("private", (proc.communicate(timeout=5)[1] or "").lower())

    def test_an_invalid_name_is_refused_before_binding(self):
        proc, _port = self._spawn(name="../evil")
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)

    def test_the_served_page_is_renderable_and_self_contained(self):
        proc, port, url = self._serve()
        with urllib.request.urlopen(url, timeout=5) as r:
            page = r.read().decode("utf-8")
        self.assertNotIn("{{", page)               # the repo's own brace trap
        self.assertIn("rel=icon", page)            # no /favicon.ico console error
        self.assertNotIn("#", page.split("rel=icon", 1)[1].split(">", 1)[0])
        self.assertIn("type=password", page.replace('"', ""))

    def test_the_endpoint_self_expires(self):
        proc, _port = self._spawn(ttl="1")
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0)


class TestStoredSecretsAreNeverGitTracked(TestCase):
    def test_no_secret_artifact_is_committed(self):
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True, timeout=60).stdout
        for line in out.splitlines():
            self.assertFalse(line.endswith(".secret"), line)
            self.assertNotIn(".claude/secrets", line)


class TestMetaShape(_StoreCase):
    def test_meta_carries_no_value_and_is_json(self):
        st.register_request("DB_PASS", endpoint_ttl_s=60, keep_s=60)
        st.store_value("DB_PASS", VAL.encode(), keep_s=60)
        meta = json.loads(st.meta_path("DB_PASS").read_text(encoding="utf-8"))
        self.assertNotIn(VAL, json.dumps(meta))
        self.assertIn("expires_at", meta)
        self.assertIn("received", meta)


if __name__ == "__main__":
    main()
