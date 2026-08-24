"""#669 — pin the ssh host key for raw-public-IP owner_vps targets.

The deploy loop (`_deploy_to_all_remotes`) and the shared secret-delivery loop
(`_deliver_secret_to_hosts`) both used a hardcoded `-o StrictHostKeyChecking=no`
for EVERY target — a TOFU posture that accepts an unknown AND a silently changed
host key. That was harmless for the private tailscale/subdev fleet, but
`spinbike-vps` (`167.233.245.147`) is the first managed target reached over the
public internet by raw IP, where a first-contact MITM or a rotated host key on
the deploy leg would hand the pushed code (and, before its removal, an owner
secret) to an attacker.

The fix pins a committed PUBLIC host key on such a target and switches its ssh
legs to `-o UserKnownHostsFile=<pin> -o GlobalKnownHostsFile=/dev/null
-o StrictHostKeyChecking=yes` (refuses unknown AND changed → a MITM/rotated key
hard-fails, ssh exit 255), while every unpinned tailscale/subdev host keeps the
current `=no` posture unchanged.

RED lock (this module): a deploy ssh leg to a PINNED host must use
`StrictHostKeyChecking=yes` + a `UserKnownHostsFile`, NEVER `=no`; an UNPINNED
host must keep `=no` (regression). Fails by assertion on the pre-fix `=no` code.
"""
import unittest
import unittest.mock as m

import airuleset
import cli_remote


def _fake_run(calls):
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        return m.Mock(returncode=0, stdout="ok", stderr="")
    return run


# A raw-public-IP target carrying a committed PUBLIC host-key pin (`host_keys`)
# and a pinned identity — deliberately NOT owner_vps, so the deploy loop touches
# neither the soniox nor (pre-removal) the headless-token delivery phase: the
# pin decision is keyed on `host_keys` presence alone, independent of owner_vps.
PINNED = {
    "name": "pin-vps", "host": "203.0.113.7", "user": "newlevel",
    "repo_path": "~/devel/airuleset", "identity": "~/.ssh/pin_vps",
    "host_keys": [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleEx",
    ],
}
# A normal tailscale target — no `host_keys`, must keep the unchanged `=no`.
PLAIN = {
    "name": "dev2", "host": "100.82.64.27", "user": "newlevel",
    "repo_path": "~/devel/airuleset",
}


def _deploy_argv_for(calls, host_ip):
    """The single deploy-loop ssh argv that targets `host_ip` (the `git pull &&
    install` connection), never the ruff/test/git-push subprocess calls."""
    deploy = [c for c in calls
              if any("python3 airuleset.py install" in str(a) for a in c)
              and any(host_ip in str(a) for a in c)]
    assert len(deploy) == 1, "expected exactly one deploy leg to %s, got %d" % (
        host_ip, len(deploy))
    return deploy[0]


class TestDeployLegPinsPublicIpHost(unittest.TestCase):
    def _run_push(self):
        calls = []
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=_fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", [PINNED, PLAIN]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                               return_value=None):
            airuleset.cmd_push(args)
        return calls

    def test_pinned_host_deploy_leg_uses_strict_yes_not_no(self):
        calls = self._run_push()
        argv = _deploy_argv_for(calls, "203.0.113.7")
        joined = " ".join(argv)
        self.assertIn("StrictHostKeyChecking=yes", argv,
                      "a pinned owner_vps deploy leg must verify the host key "
                      "strictly, never TOFU — argv was: %s" % joined)
        self.assertTrue(any(a.startswith("UserKnownHostsFile=") for a in argv),
                        "a pinned deploy leg must point ssh at the committed "
                        "known_hosts pin — argv was: %s" % joined)
        self.assertNotIn("StrictHostKeyChecking=no", argv,
                         "a pinned deploy leg must NOT keep the TOFU posture — "
                         "argv was: %s" % joined)

    def test_unpinned_tailscale_host_deploy_leg_keeps_strict_no(self):
        # Regression: pinning scope is the raw-public-IP owner_vps class ONLY.
        calls = self._run_push()
        argv = _deploy_argv_for(calls, "100.82.64.27")
        self.assertIn("StrictHostKeyChecking=no", argv,
                      "an unpinned tailscale target must keep its unchanged "
                      "TOFU-none posture — argv was: %s" % " ".join(argv))
        self.assertNotIn("StrictHostKeyChecking=yes", argv)


if __name__ == "__main__":
    unittest.main()
