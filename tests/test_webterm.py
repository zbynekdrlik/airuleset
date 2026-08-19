"""Tests for the web terminal gateway (#555, cli_webterm.py).

Covers: inventory generation from the fleet table (never a hand list), the
per-host ssh identity rule matching the deploy loop, the connect-argv shapes
(local sh -c / identity ssh / sshpass ssh), the connect allowlist (an unknown
id is REFUSED, never execed — the security-critical path), dashboard/launcher/
unit rendering, and the dev1-only provisioning gate.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402


# A small, controlled fleet: one owner box (identity), one owner box (no
# identity/sshpass), one stream (identity), one stream (no identity), and one
# PENDING host that must be filtered out.
_FAKE_HOSTS = [
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "gatekeeper", "host": "10.0.0.9", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "david@subdev", "host": "10.0.0.5", "user": "david",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "montalu@subdev", "host": "10.0.0.5", "user": "montalu"},
    {"name": "ghost@subdev", "host": "10.0.0.5", "user": "ghost", "pending": True},
]
_FAKE_AUTHORITY = {"david": "fork-no-merge", "montalu": "branch-merge"}


def _fake_inventory():
    import airuleset
    with m.patch.object(airuleset, "REMOTE_HOSTS", _FAKE_HOSTS), \
            m.patch.object(airuleset, "AUTHORITY_BY_USER", _FAKE_AUTHORITY):
        return w.webterm_inventory()


class TestInventory(unittest.TestCase):
    def test_generated_from_fleet_plus_dev1_never_hand_list(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        # dev1 (localhost) is always first; the pending ghost host is filtered.
        self.assertEqual(ids[0], "dev1")
        self.assertIn("dev2", ids)
        self.assertIn("gatekeeper", ids)
        self.assertIn("david-subdev", ids)     # `@` sanitized to `-`
        self.assertIn("montalu-subdev", ids)
        self.assertNotIn("ghost-subdev", ids)   # pending -> excluded
        self.assertEqual(len(inv), 5)           # dev1 + 4 live hosts

    def test_ids_unique_and_url_safe(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9-]+$")  # no @, no ., URL-safe

    def test_dev1_is_local(self):
        dev1 = next(e for e in _fake_inventory() if e["id"] == "dev1")
        self.assertTrue(dev1["local"])
        self.assertEqual(dev1["preferred"], "zbynek")
        self.assertEqual(dev1["kind"], "owner")

    def test_preferred_group_stream_is_user_owner_is_zbynek(self):
        inv = {e["id"]: e for e in _fake_inventory()}
        # stream accounts -> their unix user (the #264 whoami convention)
        self.assertEqual(inv["david-subdev"]["preferred"], "david")
        self.assertEqual(inv["david-subdev"]["kind"], "stream")
        self.assertEqual(inv["montalu-subdev"]["preferred"], "montalu")
        # owner boxes -> the owner group
        self.assertEqual(inv["dev2"]["preferred"], "zbynek")
        self.assertEqual(inv["dev2"]["kind"], "owner")
        self.assertEqual(inv["gatekeeper"]["preferred"], "zbynek")

    def test_identity_decision_matches_deploy_loop(self):
        # The web terminal's identity-vs-sshpass DECISION must not drift from the
        # deploy loop's: an entry has an identity iff its fleet row does.
        inv = {e["id"]: e for e in _fake_inventory()}
        self.assertEqual(inv["david-subdev"]["identity"],
                         "~/.secrets/gatekeeper_access_ed25519")
        self.assertIsNone(inv["montalu-subdev"]["identity"])
        self.assertIsNone(inv["dev2"]["identity"])


class TestConnectArgv(unittest.TestCase):
    def test_local_is_sh_c_with_attach_snippet(self):
        e = {"id": "dev1", "local": True, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sh")
        self.assertEqual(argv[1], "-c")
        self.assertIn("P=zbynek", argv[2])
        self.assertIn("new-session -A -s", argv[2])
        # No ssh for a local target.
        self.assertNotIn("ssh", argv)

    def test_identity_host_uses_ssh_i_with_pty(self):
        e = {"local": False, "user": "david", "host": "10.0.0.5",
             "identity": "~/.secrets/gatekeeper_access_ed25519", "preferred": "david"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser("~/.secrets/gatekeeper_access_ed25519"), argv)
        self.assertIn("-t", argv)                       # force a PTY
        self.assertIn("david@10.0.0.5", argv)
        self.assertNotIn("sshpass", argv)

    def test_no_identity_host_uses_sshpass(self):
        e = {"local": False, "user": "newlevel", "host": "10.0.0.2",
             "identity": None, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sshpass")
        self.assertIn("ssh", argv)
        self.assertIn("-t", argv)
        self.assertIn("newlevel@10.0.0.2", argv)

    def test_preferred_is_shell_quoted(self):
        # A pathological preferred value must be shell-quoted, never injected.
        e = {"id": "x", "local": True, "preferred": "a; rm -rf /"}
        argv = w.build_connect_argv(e)
        self.assertIn("'a; rm -rf /'", argv[2])


class TestConnectAllowlist(unittest.TestCase):
    """The security-critical path: connect_main validates the id against the
    generated inventory, never interpolates an unknown value into a shell."""

    def _inv_file(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "inv.json"
        p.write_text(json.dumps([
            {"id": "dev1", "local": True, "preferred": "zbynek"},
        ]), encoding="utf-8")
        return p

    def test_unknown_id_is_refused_and_never_execs(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")) as ex:
            rc = w.connect_main(["totally-unknown"], inventory_path=p)
        self.assertEqual(rc, 2)
        ex.assert_not_called()

    def test_no_id_is_refused(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main([], inventory_path=p)
        self.assertEqual(rc, 2)

    def test_known_id_execs_the_built_argv(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["dev1"], inventory_path=p)
        ex.assert_called_once()
        cmd = ex.call_args[0][1]
        self.assertEqual(cmd[0], "sh")
        self.assertIn("P=zbynek", cmd[2])

    def test_unreadable_inventory_is_refused(self):
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main(["dev1"], inventory_path="/no/such/inventory.json")
        self.assertEqual(rc, 2)


class TestRendering(unittest.TestCase):
    def test_dashboard_is_tabbed_ui_with_iframe_wiring(self):
        # #579: the dashboard is a single-page Windows-Terminal-style tabbed UI
        # (one iframe per session at the IP-first ttyd URL), NOT a landing page
        # of anchor cards.
        inv = [
            {"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
             "local": True, "host": None, "user": None},
            {"id": "david-subdev", "label": "david@subdev", "kind": "stream",
             "local": False, "host": "10.0.0.5", "user": "david"},
        ]
        html = w.render_dashboard_html(inv, ttyd_base="/t")
        # #584: same-origin RELATIVE ttyd base (`/t`); the JS builds `?arg=<id>`
        # from it. No cross-origin `<ip>:7682` any more.
        self.assertIn('"ttyd_base": "/t"', html)
        self.assertNotIn(":7682", html)
        self.assertIn("?arg=", html)              # JS constructs the ttyd URL
        # A top tab bar + lazy iframe container — the SPA, not a card grid.
        self.assertIn('id="tabbar"', html)
        self.assertIn('class="tab"', html)
        self.assertIn("iframe", html)
        self.assertIn("Ctrl+Alt", html)           # keyboard-switch hint
        # NOT the old landing-page card anchors.
        self.assertNotIn('class="card"', html)
        # session ids are in the embedded session list the JS iterates.
        self.assertIn('"dev1"', html)
        self.assertIn('"david-subdev"', html)

    def test_dashboard_escapes_label_and_alias(self):
        # A malicious label must never render as raw HTML in the tab / JSON.
        # (The page legitimately contains its OWN <script> for the tab logic,
        # so we assert on the injected payload specifically, not "<script>".)
        inv = [{"id": "x", "label": "<b>PWN</b>", "kind": "owner",
                "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("<b>PWN</b>", html)      # never unescaped
        self.assertIn("PWN", html)                # but the text survives escaped

    def test_dashboard_label_cannot_close_the_script_element(self):
        # A `</script>` in a label must not break out of the embedded config JS.
        inv = [{"id": "x", "label": "</script><script>alert(1)</script>",
                "kind": "owner", "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("</script><script>", html)   # neutralized in the JSON
        self.assertIn("\\u003c/script\\u003e", html)   # rendered as escaped codepoints

    def test_dashboard_sentinel_label_does_not_splice_config(self):
        # A label equal to a template sentinel must NOT splice the config JSON
        # into the tab markup (single-pass substitution guards this).
        inv = [{"id": "x", "label": "@@CFG_JSON@@", "kind": "owner",
                "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://100.64.0.9:7682")
        # The sentinel-shaped label survives as literal text in the tab title,
        # and the config JSON appears exactly ONCE (in the <script>, not spliced
        # into a title attribute).
        self.assertIn("@@CFG_JSON@@", html)
        self.assertEqual(html.count('"ttyd_base"'), 1)

    def test_launch_script_binds_loopback_behind_gateway(self):
        # #584: ttyd binds LOOPBACK only, behind a `-b /t` base path — the
        # gateway is the sole tailnet entry + authenticator. NO basic-auth
        # (`-c`), NO `-O` (the gateway does the Origin check; `-O` would break
        # the proxied WS), NO tailscale-IP bind.
        s = w.render_webterm_launch_script()
        self.assertIn("exec ttyd", s)
        self.assertIn("-p 7682", s)
        self.assertIn("-i 127.0.0.1", s)          # loopback bind
        self.assertIn("-b /t", s)                 # base path for the gateway proxy
        self.assertNotIn("-i 100.", s)            # never a tailscale/public IP
        self.assertNotIn(" -O", s)                # gateway does the Origin check
        self.assertNotIn(" -c ", s)               # no basic-auth (Bitwarden couldn't fill it)
        self.assertIn("-a", s)                    # url-arg
        self.assertIn("-W", s)                    # writable
        self.assertIn("cli_webterm.py webterm-connect", s)
        self.assertNotIn(str(w.WEBTERM_CRED_PATH), s)  # ttyd never reads the credential now

    def test_unit_substitutes_launch_script_placeholder(self):
        unit = w._render_webterm_unit()
        self.assertNotIn("{{LAUNCH_SCRIPT}}", unit)
        self.assertIn(str(w.WEBTERM_LAUNCH_PATH), unit)
        self.assertIn("WantedBy=default.target", unit)


_FAKE_TMUX = r"""#!/usr/bin/env bash
# Fake tmux: LOGS every invocation's argv to $FAKETMUX_LOG (one line each) and
# answers has-session/list-sessions from FAKETMUX_SESSIONS (newline-separated
# `group::name`, group may be empty). Proves the REAL _ATTACH_BODY builds the
# right grouped-clone multi-attach commands and NEVER detaches another client.
echo "$*" >> "$FAKETMUX_LOG"
cmd="$1"; shift
case "$cmd" in
  has-session)
    name="${2#=}"
    while IFS= read -r line; do
      [ -n "$line" ] && [ "${line#*::}" = "$name" ] && exit 0
    done <<< "$FAKETMUX_SESSIONS"
    exit 1 ;;
  list-sessions)
    if [ "$2" = "#{session_group}::#{session_name}" ]; then
      printf '%s\n' "$FAKETMUX_SESSIONS"
    else
      while IFS= read -r line; do
        [ -n "$line" ] && printf '%s\n' "${line#*::}"
      done <<< "$FAKETMUX_SESSIONS"
    fi
    exit 0 ;;
esac
exit 0
"""


class TestAttachSnippetBehavior(unittest.TestCase):
    """#584: standard tmux multi-attach that NEVER disturbs an existing client.
    Run the REAL _ATTACH_BODY against a logging fake tmux and prove: an existing
    session is JOINED via a throwaway GROUPED clone (independent view, cleaned
    up on disconnect, base never touched), the window sizes to the ACTIVE client
    (window-size latest + aggressive-resize on), and `attach -d` (the only verb
    that detaches other clients) is NEVER used."""

    def setUp(self):
        import subprocess
        self.subprocess = subprocess
        self.d = tempfile.mkdtemp()
        faketmux = Path(self.d) / "tmux"
        faketmux.write_text(_FAKE_TMUX, encoding="utf-8")
        os.chmod(faketmux, 0o755)
        self.log = Path(self.d) / "log"

    def _run(self, preferred, sessions):
        env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"],
                   FAKETMUX_SESSIONS=sessions, FAKETMUX_LOG=str(self.log))
        cmd = w._remote_command(preferred)
        self.subprocess.run(["sh", "-c", cmd], capture_output=True,
                            text=True, env=env, timeout=10)
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_grouped_owner_joins_survivor_via_grouped_clone(self):
        # dev1: zbynek + marek co-tenant. P=zbynek joins zbynek-4 (group
        # survivor) via a NEW grouped clone, never marek, never a bare attach.
        log = self._run("zbynek", "zbynek::zbynek-4\nmarek::marek-12")
        self.assertRegex(log, r"new-session -t zbynek-4 -s zbynek-4-web-\d+")
        self.assertNotIn("attach -d", log)
        self.assertNotRegex(log, r"\battach -t\b")     # never a shared mirror attach

    def test_standalone_stream_joins_exact_via_grouped_clone(self):
        log = self._run("david", "::david\n::montalu")
        self.assertRegex(log, r"new-session -t david -s david-web-\d+")

    def test_single_session_joins_via_grouped_clone_not_shared_attach(self):
        # gk: one session "0". Previously a bare `attach -t 0` (shared/mirrored
        # view); now an independent grouped clone.
        log = self._run("zbynek", "::0")
        self.assertRegex(log, r"new-session -t 0 -s 0-web-\d+")
        self.assertNotRegex(log, r"\battach -t 0\b")

    def test_cleanup_kills_only_the_clone_never_the_base(self):
        # The disconnect trap fires on EXIT (the fake new-session returns at
        # once) -> the throwaway clone is killed, the BASE session never is.
        log = self._run("zbynek", "zbynek::zbynek-4")
        # exact-match (`=`) kill of ONLY the throwaway clone
        self.assertRegex(log, r"kill-session -t =zbynek-4-web-\d+")
        # never a kill of the bare base session (either `=zbynek-4` or `zbynek-4`)
        self.assertNotRegex(log, r"kill-session -t =?zbynek-4(?!-web)")

    def test_sizes_window_to_active_client(self):
        # window-size latest + aggressive-resize on -> the shared window sizes to
        # whoever is actively viewing, never the smallest client (the owner's WT
        # view can't be shrunk by a small web client).
        log = self._run("zbynek", "zbynek::zbynek-4")
        self.assertIn("window-size latest", log)
        self.assertIn("aggressive-resize on", log)

    def test_no_sessions_creates_preferred_and_does_not_kill_it(self):
        # No existing session -> create the owner's own base, which must PERSIST
        # (never trap-killed — it is not a throwaway clone).
        log = self._run("zbynek", "")
        self.assertRegex(log, r"new-session -A -s zbynek")
        self.assertNotIn("kill-session", log)

    def test_two_sessions_no_match_creates_preferred(self):
        log = self._run("gatekeeper", "zbynek::zbynek-4\nmarek::marek-12")
        self.assertRegex(log, r"new-session -A -s gatekeeper")

    def test_never_uses_detach_flag_anywhere(self):
        # `attach -d` is the ONLY tmux verb that detaches OTHER clients; it must
        # never appear in any resolution path (the owner's WT stays attached).
        for sess in ("zbynek::zbynek-4\nmarek::marek-12", "::0", ""):
            log = self._run("zbynek", sess)
            self.assertNotIn("attach -d", log)


class TestProvisioningGate(unittest.TestCase):
    def test_setup_is_dev1_only(self):
        fake = os.uname_result(("Linux", "dev2", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertFalse(w.is_webterm_gateway())
            self.assertFalse(w.setup_webterm_service())

    def test_gateway_true_on_dev1(self):
        fake = os.uname_result(("Linux", "dev1", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertTrue(w.is_webterm_gateway())


def _run_returning(stdout="", rc=0):
    """A fake subprocess.run that ignores the argv and returns a fixed result."""
    def _run(cmd, **kw):
        return _R(rc, stdout, "")
    return _run


class _R:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class TestTailscaleIP(unittest.TestCase):
    """#579: the bind IP is dev1's DYNAMIC tailscale IP, validated to the CGNAT
    range (100.64.0.0/10) so a public/garbage value can NEVER become a bind."""

    def test_accepts_cgnat_ip(self):
        self.assertEqual(
            w._tailscale_ip(run=_run_returning("100.104.8.125\n")), "100.104.8.125")

    def test_rejects_public_ip(self):
        # A non-tailnet address must NEVER be returned (would risk a public bind).
        self.assertIsNone(w._tailscale_ip(run=_run_returning("1.2.3.4\n")))

    def test_rejects_empty(self):
        self.assertIsNone(w._tailscale_ip(run=_run_returning("")))

    def test_rejects_nonzero_rc(self):
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.104.8.125\n", rc=1)))

    def test_rejects_out_of_cgnat_100_range(self):
        # 100.200.x is NOT in 100.64.0.0/10 (second octet must be 64..127).
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.200.0.1\n")))

    def test_rejects_malformed_octets(self):
        # A malformed value in the CGNAT band (octet > 255) is still rejected.
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.64.999.1\n")))
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.64.1\n")))

    def test_boundary_ips(self):
        self.assertEqual(w._tailscale_ip(run=_run_returning("100.64.0.0\n")),
                         "100.64.0.0")
        self.assertEqual(w._tailscale_ip(run=_run_returning("100.127.255.255\n")),
                         "100.127.255.255")
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.63.0.1\n")))
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.128.0.1\n")))


class TestGatewayUnit(unittest.TestCase):
    def test_gateway_unit_binds_tailscale_ip_and_wires_ttyd_loopback(self):
        # #584: the gateway unit runs cli_webterm_gateway.py bound to the
        # tailscale IP on 8080, proxying to the loopback ttyd.
        unit = w._render_webterm_gateway_unit("100.104.8.125")
        self.assertNotIn("{{", unit)                       # every placeholder filled
        self.assertIn("cli_webterm_gateway.py", unit)
        self.assertIn("--bind 100.104.8.125", unit)        # tailnet-only bind
        self.assertIn("--port %d" % w.WEBTERM_GATEWAY_PORT, unit)   # 8080
        self.assertIn("--ttyd-host 127.0.0.1", unit)       # proxies loopback ttyd
        self.assertIn("--ttyd-port %d" % w.WEBTERM_TTYD_PORT, unit)
        self.assertIn("--cred %s" % str(w.WEBTERM_CRED_PATH), unit)
        self.assertIn("--dash-index %s" % str(w.WEBTERM_DASH_INDEX), unit)
        self.assertIn("--base-path /t", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn("--bind 0.0.0.0", unit)           # never public/wildcard
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        self.assertIn("--bind 100.104.8.125", exec_line)
        self.assertNotIn("http.server", exec_line)         # not the old static server


class TestShortAlias(unittest.TestCase):
    def _e(self, **kw):
        base = {"id": kw.get("id"), "label": kw.get("label", kw.get("id")),
                "local": False, "user": kw.get("user")}
        base.update(kw)
        return base

    def test_owner_and_family_aliases(self):
        self.assertEqual(w._short_alias(self._e(id="dev1", local=True, user=None)), "dev1")
        self.assertEqual(w._short_alias(self._e(id="dev2", user="newlevel")), "dev2")
        self.assertEqual(w._short_alias(self._e(id="gatekeeper", user="gatekeeper")), "gk")
        self.assertEqual(w._short_alias(self._e(id="montalu3-subdev", user="montalu3")), "m3")
        self.assertEqual(w._short_alias(self._e(id="montalu8-subdev", user="montalu8")), "m8")
        self.assertEqual(w._short_alias(self._e(id="miva1-subdev", user="miva1")), "miva")
        self.assertEqual(w._short_alias(self._e(id="david-subdev", user="david")), "d1")
        self.assertEqual(w._short_alias(self._e(id="david4-subdev", user="david4")), "d4")

    def test_unknown_gets_sensible_short_form(self):
        # An unrecognized session still gets a short, non-empty alias.
        a = w._short_alias(self._e(id="admin-forestshop-dev", user="admin"))
        self.assertTrue(0 < len(a) <= 8)
        self.assertNotIn("@", a)

    def test_tab_order_stable(self):
        # dev1, dev2, gk, m1.., miva, d*, then the rest alphabetically.
        aliases = ["d2", "m3", "gk", "dev2", "miva", "dev1", "m1", "zzz", "d1", "aaa"]
        ordered = sorted(aliases, key=w._tab_order_key)
        self.assertEqual(
            ordered, ["dev1", "dev2", "gk", "m1", "m3", "miva", "d1", "d2", "aaa", "zzz"])

    def test_tab_sessions_end_to_end_order(self):
        # The FULL pipeline (alias derivation -> sort) over a realistic
        # inventory produces the owner's stable Windows-Terminal tab order.
        inv = [
            {"id": "montalu3-subdev", "label": "montalu3@subdev", "user": "montalu3"},
            {"id": "dev2", "label": "dev2", "user": "newlevel"},
            {"id": "david-subdev", "label": "david@subdev", "user": "david"},
            {"id": "dev1", "label": "dev1 (localhost)", "user": None, "local": True},
            {"id": "gatekeeper", "label": "gatekeeper", "user": "gatekeeper"},
            {"id": "montalu1-subdev", "label": "montalu1@subdev", "user": "montalu1"},
            {"id": "miva1-subdev", "label": "miva1@subdev", "user": "miva1"},
            {"id": "admin-forestshop-dev", "label": "admin@forestshop-dev", "user": "admin"},
        ]
        aliases = [t["alias"] for t in w._tab_sessions(inv)]
        self.assertEqual(
            aliases, ["dev1", "dev2", "gk", "m1", "m3", "miva", "d1", "admin"])


class TestSetupWiring(unittest.TestCase):
    """Drives setup_webterm_service() with every path constant redirected into a
    temp dir + systemctl/whoami mocked, proving the install path (a) never runs
    `tailscale serve --bg`, (b) resets any leftover serve config, (c) writes an
    IP-first tabbed dashboard + dash unit, (d) refuses (writes nothing) with no
    tailscale IP, and (e) is idempotent over a re-run (converges, never dups)."""

    def _isolate(self, stack, tmp):
        p = Path(tmp)
        (p / ".claude").mkdir(parents=True, exist_ok=True)
        (p / ".secrets").mkdir(parents=True, exist_ok=True)
        (p / ".config" / "systemd" / "user").mkdir(parents=True, exist_ok=True)
        patches = {
            "CLAUDE_DIR": p / ".claude",
            "SECRETS_DIR": p / ".secrets",
            "WEBTERM_INVENTORY_PATH": p / ".claude" / "webterm-inventory.json",
            "WEBTERM_DASH_DIR": p / ".claude" / "webterm-dash",
            "WEBTERM_DASH_INDEX": p / ".claude" / "webterm-dash" / "index.html",
            "WEBTERM_LAUNCH_PATH": p / ".claude" / "airuleset-webterm-ttyd.sh",
            "WEBTERM_CRED_PATH": p / ".secrets" / "webterm_credential",
            "WEBTERM_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-ttyd.service",
            "WEBTERM_GATEWAY_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-gateway.service",
            "WEBTERM_OLD_DASH_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-dash.service",
        }
        for name, val in patches.items():
            stack.enter_context(m.patch.object(w, name, val))
        fake = os.uname_result(("Linux", "dev1", "x", "x", "x"))
        stack.enter_context(m.patch.object(w.os, "uname", return_value=fake))
        stack.enter_context(m.patch.object(w.shutil, "which", return_value="/usr/bin/ttyd"))
        stack.enter_context(m.patch.object(
            w, "webterm_inventory",
            return_value=[{"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
                           "local": True, "host": None, "user": None, "preferred": "zbynek"}]))
        import cli_filedrop_watchdog as fw
        stack.enter_context(m.patch.object(fw, "_run_systemctl", lambda args: (0, "", "")))
        stack.enter_context(m.patch.object(fw, "_whoami", lambda: "zbynek"))
        return patches

    class _RunRec:
        def __init__(self, ip="100.104.8.125"):
            self.calls, self.ip = [], ip

        def __call__(self, cmd, **kw):
            self.calls.append(list(cmd))
            if list(cmd[:3]) == ["tailscale", "ip", "-4"]:
                return _R(0, (self.ip + "\n") if self.ip else "", "")
            return _R(0, "", "")

    def test_install_path_has_no_serve_but_resets_and_writes_same_origin(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            rec = self._RunRec()
            ok = w.setup_webterm_service(run=rec)
            self.assertTrue(ok)
            # (a) NEVER runs `tailscale serve --bg` (the 404-causing layer).
            self.assertFalse(any(c[:2] == ["tailscale", "serve"] and "--bg" in c
                                 for c in rec.calls), rec.calls)
            # (b) resets any leftover serve config.
            self.assertTrue(any(list(c[:3]) == ["tailscale", "serve", "reset"]
                                for c in rec.calls), rec.calls)
            # (c) #584: SAME-ORIGIN tabbed dashboard (relative /t, no :7682) +
            # the GATEWAY unit bound to the tailscale IP + the loopback ttyd launch.
            html = pt["WEBTERM_DASH_INDEX"].read_text()
            self.assertIn('"ttyd_base": "/t"', html)
            self.assertNotIn(":7682", html)
            self.assertIn('id="tabbar"', html)
            self.assertNotIn('class="card"', html)
            unit = pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text()
            self.assertIn("--bind 100.104.8.125", unit)
            self.assertIn("cli_webterm_gateway.py", unit)
            launch = pt["WEBTERM_LAUNCH_PATH"].read_text()
            self.assertIn("-i 127.0.0.1", launch)     # ttyd is loopback now
            self.assertIn("-b /t", launch)
            self.assertNotIn("-i 100.", launch)

    def test_no_tailscale_ip_refuses_and_writes_no_unit(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            rec = self._RunRec(ip="")          # tailscale ip -4 returns nothing
            ok = w.setup_webterm_service(run=rec)
            self.assertFalse(ok)               # LOUD refusal
            # NEVER writes a unit with a possibly-public bind.
            self.assertFalse(pt["WEBTERM_GATEWAY_SERVICE_DEST"].exists())
            self.assertFalse(pt["WEBTERM_SERVICE_DEST"].exists())

    def test_reinstall_is_idempotent(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            self.assertTrue(w.setup_webterm_service(run=self._RunRec()))
            first = pt["WEBTERM_DASH_INDEX"].read_text()
            first_unit = pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text()
            self.assertTrue(w.setup_webterm_service(run=self._RunRec()))
            self.assertEqual(first, pt["WEBTERM_DASH_INDEX"].read_text())
            self.assertEqual(first_unit, pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text())


class TestTabSwitchingUX(unittest.TestCase):
    """#582: Ctrl+Alt+N can't switch tabs while focus is inside a cross-origin
    ttyd iframe (a web-platform limitation — the parent :8080 page never
    receives keydowns dispatched inside the :7682 iframe). The proportionate
    fix keeps reliable CLICK switching and adds discoverable UX: ordinal
    badges (the visible Ctrl+Alt+N map), Prev/Next cycle controls that step
    through ALL sessions, Ctrl+Alt+arrow cycling, and an honest hint."""

    def _inv(self, n):
        return [{"id": "s%02d" % i, "label": "sess %02d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "usr%02d" % i}
                for i in range(1, n + 1)]

    def test_ordinal_badges_on_first_nine_tabs_only(self):
        # 11 sessions -> exactly 9 ordinal badges (the Ctrl+Alt+1..9 range),
        # numbered 1..9; the 10th/11th tabs carry no badge.
        html = w.render_dashboard_html(self._inv(11), ttyd_base="http://b:7682")
        self.assertEqual(html.count('class="ord"'), 9)
        self.assertIn('<span class="ord">1</span>', html)
        self.assertIn('<span class="ord">9</span>', html)
        self.assertNotIn('<span class="ord">10</span>', html)
        self.assertNotIn('<span class="ord">11</span>', html)

    def test_ordinal_badge_count_matches_small_inventory(self):
        html = w.render_dashboard_html(self._inv(3), ttyd_base="http://b:7682")
        self.assertEqual(html.count('class="ord"'), 3)
        self.assertIn('<span class="ord">1</span>', html)
        self.assertIn('<span class="ord">3</span>', html)

    def test_ordinal_badges_are_only_digits_never_a_label(self):
        # Even a hostile label must never leak into the ordinal badge — the
        # badge is a fixed position digit, not user data.
        inv = [{"id": "x", "label": "<b>PWN</b>", "kind": "owner",
                "local": True, "host": None, "user": None}]
        # A single-tab inventory still gets one badge — assert it EXISTS so the
        # digits-only guarantee can never be met vacuously (0 badges).
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        import re as _re
        matches = _re.findall(r'<span class="ord">([^<]*)</span>', html)
        self.assertTrue(matches)                      # a badge is actually present
        for content in matches:
            self.assertRegex(content, r"^[0-9]+$")

    def test_prev_next_cycle_buttons_present(self):
        html = w.render_dashboard_html(self._inv(4), ttyd_base="http://b:7682")
        self.assertIn('class="cyc"', html)
        self.assertIn('data-cyc="-1"', html)   # prev
        self.assertIn('data-cyc="1"', html)    # next
        self.assertIn("function cycle(", html)  # the JS helper the buttons call

    def test_keydown_is_direct_jump_only_no_arrow_binding(self):
        # Ctrl+Alt+1..9 direct-jumps when the tab bar has focus — gate the ACTUAL
        # handler code, not the decorative "Ctrl+Alt" hint text.
        html = w.render_dashboard_html(self._inv(4), ttyd_base="http://b:7682")
        self.assertIn("e.key >= '1'", html)          # the digit-jump handler
        self.assertIn("function cycle(", html)        # cycling stays (via ◀ ▶ buttons)
        # No Ctrl+Alt+arrow binding: Ctrl+Alt+Left/Right is the Linux desktop
        # workspace-switch shortcut, grabbed by the compositor before the page
        # sees it — advertising it would over-promise (both #582 reviewers).
        self.assertNotIn("ArrowLeft", html)
        self.assertNotIn("ArrowRight", html)

    def test_hint_advertises_click_cycle_and_working_shortcut(self):
        # #584 fixed the #582 residual: the hint no longer warns of a
        # focus/origin limitation. It still surfaces the always-works paths
        # (click + ◀ ▶) AND the now-working Ctrl+Alt shortcut.
        html = w.render_dashboard_html(self._inv(4), ttyd_base="/t")
        hint = next(ln for ln in html.splitlines() if 'id="hint"' in ln)
        self.assertIn("klik", hint.lower())          # click always works
        self.assertIn("Ctrl+Alt", hint)              # the shortcut is advertised
        self.assertTrue("◀" in hint or "▶" in hint)   # cycle affordance
        self.assertNotIn("iný origin", hint)          # the old limitation note is gone

    def test_ux_additions_do_not_break_escaping_or_single_pass(self):
        # The security invariants from #579 must survive the UX additions.
        inv = [{"id": "x", "label": "</script><script>alert(1)</script>",
                "kind": "owner", "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("</script><script>", html)
        self.assertEqual(html.count('"ttyd_base"'), 1)


class TestSameOriginKeyboard(unittest.TestCase):
    """#584 supersedes #582's residual: the gateway makes the terminal iframes
    SAME-ORIGIN, so Ctrl+Alt+1..9 now works even while focus is IN a terminal —
    a per-iframe capture-phase keydown forwarder (only possible same-origin)
    reaches the parent's switch logic before xterm consumes the keystroke."""

    def _inv(self, n=3):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_dashboard_uses_same_origin_ttyd_base(self):
        # The caller now passes a RELATIVE base (`/t`) so each iframe is
        # same-origin with the dashboard — never an absolute `<ip>:7682` origin.
        html = w.render_dashboard_html(self._inv(2), ttyd_base="/t")
        self.assertIn('"ttyd_base": "/t"', html)
        self.assertIn("?arg=", html)              # iframe src still built from it
        self.assertNotIn(":7682", html)           # no cross-origin ttyd port

    def test_per_iframe_keydown_forwarder_is_wired_on_load(self):
        # The fix: attach a keydown listener to each terminal iframe's OWN window
        # when it loads (same-origin makes `contentWindow` reachable), in the
        # CAPTURE phase so it fires before xterm swallows the key.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("contentWindow", html)
        self.assertIn("addEventListener('load'", html)
        # capture-phase keydown forwarder (the `true` 3rd arg is load-bearing)
        self.assertRegex(html, r"addEventListener\('keydown',\s*\w+,\s*true\)")

    def test_hotkey_handler_shared_and_stops_propagation(self):
        # ONE shared handler used by BOTH the parent bar AND the per-iframe
        # forwarder; it stops propagation so xterm never also processes the key.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("e.key >= '1'", html)        # the digit-jump logic
        self.assertIn("stopPropagation", html)     # xterm must not also get it

    def test_hint_no_longer_says_shortcut_fails_while_typing(self):
        # The honest #582 residual ("during typing it's a different origin — use
        # click") is GONE now that the shortcut works while typing.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        hint = next(ln for ln in html.splitlines() if 'id="hint"' in ln)
        self.assertNotIn("iný origin", hint)
        # click / cycle buttons still advertised as the always-works path
        self.assertIn("klik", hint.lower())


if __name__ == "__main__":
    unittest.main()
