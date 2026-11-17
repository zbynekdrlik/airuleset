"""#1306 — two follow-up invariants of the montalu retirement.

(B) `cli_aliases` no longer treats montaluN as a stream family. The accounts
were deleted with their homes on subdev (owner 16.11.2026, "ano mozes ich
zrusit"), so the #596/#598 Discuss guards (`discuss_thread_guard`, keyed on
`stream_number`), the short tab alias and the erp-test deploy user must not
classify a `montaluN` unix user as a client stream any more. A recreated
account with that name would otherwise silently inherit stream gates.

(C) The managed fail2ban hardening (#841) keeps the fleet tailnet, and with it
the controller (100.101.214.103), in the ignore list of the jails it relies
on, even when a box carries a hand-made /etc/fail2ban/jail.local. fail2ban
reads jail.conf -> jail.d/*.conf -> jail.local -> jail.d/*.local, so the
`[DEFAULT] ignoreip` of `jail.d/50-airuleset-hardening.conf` LOSES to any
jail.local. subdev's jail.local listed dev1 + gk but not the controller, which
was banned three times on 16.11.2026. The fix is a `jail.d/*.local` drop-in
that loads after jail.local and APPENDS (`%(known/ignoreip)s`) per jail, so
the hand-made entries stay and the tailnet is guaranteed. `known/` inside
`[DEFAULT]` makes fail2ban 1.0.2 refuse the whole config (measured), so the
append must sit in the jail sections, never in `[DEFAULT]`.
"""
import ipaddress
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_aliases  # noqa: E402
import cli_disk_guard_root as g  # noqa: E402
import discuss_thread_guard  # noqa: E402

MONTALU_USERS = ["montalu"] + ["montalu%d" % i for i in range(1, 9)]


class TestMontaluIsNoStreamFamily(unittest.TestCase):
    def test_stream_number_is_none(self):
        for u in MONTALU_USERS:
            self.assertIsNone(cli_aliases.stream_number(u), u)

    def test_erp_test_deploy_user_is_none(self):
        for u in MONTALU_USERS:
            self.assertIsNone(cli_aliases.erp_test_deploy_user(u), u)
            self.assertIsNone(cli_aliases.erp_test_deploy_key_name(u), u)

    def test_alias_is_the_generic_cut_not_m_n(self):
        self.assertEqual(cli_aliases.short_target_alias("montalu2", "subdev"),
                         "montalu2")

    def test_no_montalu_stem_left_in_the_classifier_source(self):
        src = (ROOT / "cli_aliases.py").read_text()
        code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        self.assertNotIn('"montalu"', code)
        self.assertNotRegex(code, r"montalu[|(\\]")

    def test_discuss_guard_is_silent_for_a_montalu_user(self):
        create = ('models.execute_kw(db, uid, pw, "discuss.channel", "create", '
                  '[{"name": "Bez cisla"}])')
        self.assertIsNone(discuss_thread_guard.evaluate(create, "montalu2"))
        # control: the same create by a live stream is still a violation
        self.assertIsNotNone(discuss_thread_guard.evaluate(create, "david2"))

    def test_live_families_still_classify(self):
        self.assertEqual(cli_aliases.stream_number("david3"), "3")
        self.assertEqual(cli_aliases.stream_number("miva2"), "2")
        self.assertEqual(cli_aliases.erp_test_deploy_user("miva1"), "mdeploy")
        self.assertEqual(cli_aliases.erp_test_deploy_user("david2"), "ddeploy")


def _sections(text):
    """{section: {key: value}} of an ini text (no interpolation)."""
    out, cur = {}, None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[(.+)\]$", s)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        k, _, v = s.partition("=")
        cur[k.strip()] = v.strip()
    return out


class TestFail2banControllerLock(unittest.TestCase):
    def setUp(self):
        self.local = g.render_fail2ban_ignoreip_local()
        self.sec = _sections(self.local)

    def test_path_is_a_jail_d_local_so_it_loads_after_jail_local(self):
        self.assertTrue(g.FAIL2BAN_JAIL_LOCAL_PATH.startswith(
            "/etc/fail2ban/jail.d/"))
        self.assertTrue(g.FAIL2BAN_JAIL_LOCAL_PATH.endswith(".local"))

    def test_appends_per_jail_never_in_default(self):
        self.assertNotIn("DEFAULT", self.sec)
        for jail in ("sshd", "recidive"):
            v = self.sec[jail]["ignoreip"]
            self.assertTrue(v.startswith("%(known/ignoreip)s "), (jail, v))

    def test_controller_is_covered_in_every_jail(self):
        ctl = ipaddress.ip_address(g.CONTROLLER_TAILSCALE_IP)
        for jail in ("sshd", "recidive"):
            entries = self.sec[jail]["ignoreip"].split()[1:]
            nets = [ipaddress.ip_network(e, strict=False) for e in entries]
            self.assertTrue(any(ctl in n for n in nets if n.version == 4),
                            (jail, entries))

    def test_does_not_enable_any_jail(self):
        for jail, kv in self.sec.items():
            self.assertNotIn("enabled", kv, jail)

    def test_installed_inside_the_fail2ban_present_gate(self):
        s = g.build_apply_script()
        gate = s.index("if command -v fail2ban-client")
        end = s.index("\nfi", gate)
        block = s[gate:end]
        self.assertIn(g.FAIL2BAN_JAIL_LOCAL_PATH, block)
        self.assertIn("%(known/ignoreip)s", block)
        # written before the reload so the reload picks it up
        self.assertLess(block.index(g.FAIL2BAN_JAIL_LOCAL_PATH),
                        block.index("fail2ban-client reload"))

    def test_config_test_gates_the_reload(self):
        # a config fail2ban refuses must never reach a reload (and the next
        # restart would then leave sshd unprotected): -t first, a failure
        # removes the .local, skips the reload and fails the read-back.
        s = g.build_apply_script()
        block = s[s.index("if command -v fail2ban-client"):]
        t = block.index("fail2ban-client -t")
        self.assertLess(t, block.index("fail2ban-client reload"))
        self.assertIn("rm -f %s" % g.FAIL2BAN_JAIL_LOCAL_PATH, block[t:])
        self.assertIn('if [ -n "$F2B_FAIL" ]; then fail=1; fi', s)

    def test_readback_checks_the_effective_sshd_ignoreip(self):
        s = g.build_apply_script()
        rb = s[s.index("fail=0"):]
        self.assertIn("fail2ban-client get sshd ignoreip", rb)
        self.assertIn("*100.64.0.0/10*", rb)

    def test_not_in_the_always_install_list(self):
        paths = [p for (p, _c, _m) in g.guard_files()]
        self.assertNotIn(g.FAIL2BAN_JAIL_LOCAL_PATH, paths)


if __name__ == "__main__":
    unittest.main()
