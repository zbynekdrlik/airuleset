"""#1323 — the peer root leg removes a retired bridge's far-end account.

The root leg creates `<project>-peer` on the far box (#1266) but never removed
one whose bridge left `peer-links.json`, so its forced-command key stayed. Each
test runs the REAL rendered bash against a scratch root; only the account tools
(`id`, `getent`, `useradd`, `userdel`, `pkill`, `pgrep`) are stubbed over a fake
passwd file.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_peer_channel as cpc  # noqa: E402

MARKERS = "/etc/airuleset/peer-far-ends"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGkRnAroNZk+tD6jrYVbJUalRDoBk/pxTiLwB/rUZvfW p"
STUBS = {
    "id": '#!/bin/sh\n[ "$1" = -u ] && grep -q "^$2:" "$FAKE_PASSWD"\n',
    "getent": '#!/bin/sh\n[ "$1" = passwd ] && grep "^$2:" "$FAKE_PASSWD"\n',
    "useradd": ('#!/bin/bash\nset -e\necho "useradd $*" >> "$FAKE_LOG"\n'
                'while [ $# -gt 1 ]; do [ "$1" = --home-dir ] && h="$2"; shift; done\n'
                'echo "$1:x:990:990::$h:/bin/sh" >> "$FAKE_PASSWD"\nmkdir -p "$h"\n'),
    "userdel": ('#!/bin/bash\nset -e\nk=0; [ -e "$FAKE_KEYS" ] && k=1\n'
                'echo "userdel $* key_present=$k" >> "$FAKE_LOG"\n'
                'grep -v "^${@: -1}:" "$FAKE_PASSWD" > "$FAKE_PASSWD.n" || true\n'
                'mv "$FAKE_PASSWD.n" "$FAKE_PASSWD"\n'),
    "pkill": '#!/bin/sh\necho "pkill $*" >> "$FAKE_LOG"\nexit 1\n',
    "pgrep": "#!/bin/sh\nexit 1\n",
    "systemctl": "#!/bin/sh\nexit 0\n",
}


def _bridge(far_account):
    return {"account": "nl", "home": "dev2", "path": "~/p",
            "hosts": {"h1": {"account": far_account, "ssh": far_account + "@1.2.3.4",
                             "identity": "~/.ssh/k", "path": cpc.SNAPSHOT_DIR, "key": KEY}}}


def _doc(*far_accounts):
    return {"hosts": {"h1": {"links": [], "topics": {}}},
            "bridges": {"b%d" % i: _bridge(a) for i, a in enumerate(far_accounts)}}


class _World(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pfe")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.root], check=False))
        self.passwd = os.path.join(self.root, "passwd")
        self.log = os.path.join(self.root, "calls.log")
        Path(self.passwd).write_text("root:x:0:0::/root:/bin/bash\n")
        Path(self.log).write_text("")
        self.bindir = os.path.join(self.root, "bin")
        os.makedirs(self.bindir)
        for name, body in STUBS.items():
            p = os.path.join(self.bindir, name)
            Path(p).write_text(body)
            os.chmod(p, 0o755)

    def home(self, acct):
        return self.root + "/var/lib/" + acct

    def add_account(self, acct, home=None, shell="/bin/sh", marked=True, key=True):
        home = home or self.home(acct)
        with open(self.passwd, "a") as f:
            f.write("%s:x:991:991::%s:%s\n" % (acct, home, shell))
        os.makedirs(os.path.join(home, ".ssh"), exist_ok=True)
        if key:
            Path(home, ".ssh", "authorized_keys").write_text("restrict " + KEY + "\n")
        if marked:
            os.makedirs(self.root + MARKERS, exist_ok=True)
            Path(self.root + MARKERS, acct).write_text(acct + "\n")

    def accounts(self):
        return [ln.split(":")[0] for ln in Path(self.passwd).read_text().splitlines()]

    def marked(self, acct):
        return os.path.exists(self.root + MARKERS + "/" + acct)

    def run_script(self, script, keys_of=None):
        script = (script.replace("/etc/", self.root + "/etc/")
                  .replace("/var/lib/", self.root + "/var/lib/")
                  .replace("cd /\n", "cd %s\n" % self.root))
        env = {**os.environ, "PATH": self.bindir + ":" + os.environ["PATH"],
               "FAKE_PASSWD": self.passwd, "FAKE_LOG": self.log,
               "FAKE_KEYS": os.path.join(self.home(keys_of or "-"), ".ssh", "authorized_keys")}
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)

    def teardown_with(self, doc, keys_of=None):
        with mock.patch.object(cpc, "load_links", return_value=doc):
            script = cpc.render_teardown_script()
        return self.run_script(script, keys_of)


class TestRetire(_World):
    def test_a_dropped_bridge_far_end_is_removed_key_first_and_logged(self):
        self.add_account("old-peer")
        r = self.teardown_with(_doc(), keys_of="old-peer")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("old-peer", self.accounts())
        self.assertFalse(os.path.exists(self.home("old-peer")))
        self.assertFalse(self.marked("old-peer"))
        calls = Path(self.log).read_text()
        self.assertIn("userdel old-peer key_present=0", calls)   # key removed BEFORE userdel
        self.assertLess(calls.index("pkill"), calls.index("userdel"))
        self.assertIn("retired far end old-peer: key removed", r.stdout)
        self.assertIn("retired far end old-peer: account removed", r.stdout)

    def test_a_live_bridge_far_end_stays(self):
        self.add_account("live-peer")
        self.add_account("old-peer")
        r = self.teardown_with(_doc("live-peer"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("live-peer", self.accounts())
        self.assertTrue(self.marked("live-peer"))
        self.assertTrue(Path(self.home("live-peer"), ".ssh", "authorized_keys").exists())
        self.assertNotIn("old-peer", self.accounts())

    def test_a_live_bridge_on_another_host_also_stays(self):
        self.add_account("live-peer")
        doc = _doc("live-peer")
        doc["bridges"]["b0"]["hosts"] = {"h9": doc["bridges"]["b0"]["hosts"]["h1"]}
        r = self.teardown_with(doc)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("live-peer", self.accounts())

    def test_an_unmarked_account_is_never_touched(self):
        self.add_account("other-peer", marked=False)
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("other-peer", self.accounts())
        self.assertTrue(Path(self.home("other-peer"), ".ssh", "authorized_keys").exists())
        self.assertEqual(Path(self.log).read_text(), "")

    def test_a_marked_account_that_is_no_longer_ours_is_left_alone(self):
        self.add_account("x-peer", home=self.root + "/home/x-peer", shell="/bin/bash")
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("x-peer", self.accounts())
        self.assertTrue(Path(self.root, "home", "x-peer", ".ssh", "authorized_keys").exists())
        self.assertNotIn("userdel", Path(self.log).read_text())
        self.assertFalse(self.marked("x-peer"))
        self.assertIn("x-peer", r.stderr)

    def test_a_marker_for_an_already_gone_account_is_cleared(self):
        os.makedirs(self.root + MARKERS)
        Path(self.root + MARKERS, "gone-peer").write_text("gone-peer\n")
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.marked("gone-peer"))
        self.assertNotIn("userdel", Path(self.log).read_text())

    def test_a_failed_userdel_fails_loud_and_keeps_the_marker(self):
        self.add_account("old-peer")
        Path(self.bindir, "userdel").write_text("#!/bin/sh\necho busy >&2\nexit 8\n")
        r = self.teardown_with(_doc())
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.marked("old-peer"))

    def test_a_far_end_that_moved_to_another_host_is_removed_from_the_old_one(self):
        self.add_account("live-peer")
        doc = _doc("live-peer")    # declared on h1 only
        r = self.run_script(cpc.render_teardown_script(doc, host="h2"), keys_of="live-peer")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("live-peer", self.accounts())
        self.add_account("live-peer")
        r = self.run_script(cpc.render_teardown_script(doc, host="h1"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("live-peer", self.accounts())

    def test_a_bad_marker_name_is_dropped_never_userdel(self):
        os.makedirs(self.root + MARKERS)
        Path(self.root + MARKERS, "Bad_Name").write_text("x\n")
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.marked("Bad_Name"))
        self.assertEqual(Path(self.log).read_text(), "")
        self.assertIn("Bad_Name", r.stderr)

    def test_an_unsafe_keep_entry_is_refused_at_render(self):
        doc = _doc("live-peer")
        doc["bridges"]["b0"]["hosts"]["h1"]["account"] = "x$(id)"
        with self.assertRaises(ValueError):
            cpc.render_teardown_script(doc)

    def test_a_symlinked_ssh_dir_is_refused_and_fails_loud(self):
        self.add_account("old-peer", key=False)
        os.rmdir(os.path.join(self.home("old-peer"), ".ssh"))
        os.symlink(self.root, os.path.join(self.home("old-peer"), ".ssh"))
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("old-peer", self.accounts())
        self.assertTrue(self.marked("old-peer"))

    def test_one_stuck_account_neither_skips_the_others_nor_the_teardown(self):
        self.add_account("a-peer")
        self.add_account("b-peer")
        Path(self.bindir, "userdel").write_text(
            '#!/bin/bash\n[ "$1" = a-peer ] && exit 8\n'
            'grep -v "^$1:" "$FAKE_PASSWD" > "$FAKE_PASSWD.n"; mv "$FAKE_PASSWD.n" "$FAKE_PASSWD"\n')
        os.makedirs(os.path.dirname(self.root + cpc.POLICY_PATH))
        Path(self.root + cpc.POLICY_PATH).write_text("x")
        r = self.teardown_with(_doc())
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertIn("a-peer", self.accounts())
        self.assertTrue(self.marked("a-peer"))
        self.assertNotIn("b-peer", self.accounts())
        self.assertIn("torn down", r.stdout)
        self.assertFalse(os.path.exists(self.root + cpc.POLICY_PATH))

    def test_the_home_script_sweeps_too(self):
        self.add_account("old-peer")
        with mock.patch.object(cpc, "load_links", return_value=_doc()):
            script = cpc.render_home_script(cpc.OFFICIAL_CHANNEL_PLUGINS)
        r = self.run_script(script)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("old-peer", self.accounts())

    def test_the_full_root_script_sweeps_too(self):
        doc = _doc("live-peer")
        script = cpc.render_root_script(["live-peer"], doc, [])
        self.assertIn("retired far end", script)
        # the sweep runs after the read-back, so a sweep failure never blocks the setup
        self.assertLess(script.index("policy unreadable"), script.index("retired far end"))
        self.assertLess(script.index("retired far end"), script.index("peer-channel-root: ok"))


class TestMarker(_World):
    def _provision(self, doc, accounts):
        script = ("set -euo pipefail\ncd /\numask 022\nCHANGED=''\n" + cpc._INSTALL_FN
                  + cpc._far_end_script(accounts, doc))
        return self.run_script(script)

    def test_a_far_end_the_root_leg_creates_is_marked(self):
        r = self._provision(_doc("new-peer"), ["new-peer"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("new-peer", self.accounts())
        self.assertTrue(self.marked("new-peer"))

    def test_a_legacy_far_end_with_our_signature_is_adopted(self):
        self.add_account("airuleset-peer", marked=False)
        r = self._provision(_doc("airuleset-peer"), ["airuleset-peer"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.marked("airuleset-peer"))
        self.assertNotIn("useradd", Path(self.log).read_text())

    def test_a_same_named_foreign_account_is_never_marked(self):
        self.add_account("p-peer", home=self.root + "/home/p-peer", shell="/bin/bash",
                         marked=False)
        r = self._provision(_doc("p-peer"), ["p-peer"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.marked("p-peer"))

    def test_dropping_the_bridge_after_provisioning_removes_it(self):
        self.assertEqual(self._provision(_doc("new-peer"), ["new-peer"]).returncode, 0)
        r = self.teardown_with(_doc(), keys_of="new-peer")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("new-peer", self.accounts())

    def test_the_committed_far_ends_are_all_kept(self):
        live = sorted(far["account"] for spec in cpc.load_links()["bridges"].values()
                      for far in spec["hosts"].values())
        self.assertEqual(live, ["airuleset-peer", "devbridge-peer", "presenter-peer"])
        for acct in live:
            self.add_account(acct)
        r = self.teardown_with(cpc.load_links())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for acct in live:
            self.assertIn(acct, self.accounts())
            self.assertTrue(self.marked(acct))


if __name__ == "__main__":
    unittest.main()
