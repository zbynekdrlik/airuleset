"""#1270 (owner 31.10.2026: "nemigroval vsetko ale len cast veci"): go-live fails
while the old checkout still sits in newlevel, plus the review findings of the
peer snapshot and the peer issue tokens."""
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_verify as verify  # noqa: E402
import cli_peer_channel as cpc  # noqa: E402
import cli_project_gh_token as tok  # noqa: E402
import peer_channel  # noqa: E402

SPEC = {"host": "dev1", "repo": "zbynekdrlik/dante-stack", "extra_repos": ["zbynekdrlik/dantesync"]}


class TestOldCheckout(unittest.TestCase):

    def _run(self, stdout="", rc=0):
        self.calls = []

        def run(argv, **kw):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, rc, stdout, "")
        return run

    def test_a_listed_leftover_fails(self):
        ok, detail = verify.check_old_checkout("dante-stack", SPEC, self._run("dantesync\n"))
        self.assertFalse(ok)
        self.assertIn("dantesync", detail)

    def test_no_leftover_passes_and_names_each_repo(self):
        ok, _ = verify.check_old_checkout("dante-stack", SPEC, self._run())
        self.assertTrue(ok)
        self.assertIn("newlevel@", " ".join(self.calls[0]))
        self.assertIn("dante-stack dantesync", self.calls[0][-1])

    def test_a_failed_ssh_is_no_pass(self):
        self.assertFalse(verify.check_old_checkout("dante-stack", SPEC, self._run(rc=255))[0])

    def test_it_is_a_check(self):
        self.assertIn("old-checkout", verify.CHECKS)


class TestMintPeerTokens(unittest.TestCase):

    def test_a_broken_links_file_never_stops_the_timer(self):
        with mock.patch("cli_peer_channel.peer_repos", side_effect=ValueError("bad json")):
            rc, records = tok.mint_peer_tokens("a", "o/a", 1, "j", {"name": "a"}, 0)
        self.assertEqual(rc, 1)
        self.assertEqual(records, {})

    def test_an_extra_repo_keeps_the_account_token(self):
        seen = []
        with mock.patch("cli_peer_channel.peer_repos", return_value=["o/extra", "o/b"]), \
                mock.patch.object(tok, "mint_issues_token",
                                  side_effect=lambda *a, **k: seen.append(k["target"]) or (0, {})):
            tok.mint_peer_tokens("a", "o/a", 1, "j", {"name": "a"}, 0, exclude=["o/extra"])
        self.assertEqual(seen, ["o/b"])


class TestPeerStatusLine(unittest.TestCase):

    def test_a_skipped_peer_shows(self):
        state = {"peers": {"o/b": {"ok": True}, "o/c": {"ok": False, "skipped": True}}}
        with mock.patch.object(tok, "read_state", return_value=state):
            line = tok.peer_status_line("a")
        self.assertIn("1 OK", line)
        self.assertIn("o/c", line)


class TestShimExpiredPeer(unittest.TestCase):

    def test_an_expired_peer_token_falls_back_to_primary(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        d = Path(home, ".config/gh-app-tokens")
        d.mkdir(parents=True)
        (d / "primary").write_text("tok-primary\n")
        (d / "zbynekdrlik__gone").write_text("tok-gone\n")
        (d / "zbynekdrlik__gone.expires").write_text("2020-01-01T00:00:00Z\n")
        bindir = Path(home, "bin")
        bindir.mkdir()
        shutil.copy("/usr/bin/env", bindir / "gh")
        s = bindir / "issue"
        s.write_text('#!/bin/bash\necho "seen $GH_TOKEN"\n')
        s.chmod(s.stat().st_mode | stat.S_IEXEC)
        shim = Path(home, "shim")
        shim.write_text(tok.render_gh_app_shim())
        r = subprocess.run(["bash", str(shim), "issue", "list", "-R", "zbynekdrlik/gone"],
                           capture_output=True, text=True,
                           env={"HOME": home, "PATH": "%s:/usr/bin:/bin" % bindir})
        self.assertEqual(r.stdout.strip(), "seen tok-primary", r.stderr)


class TestSnapshotStatus(unittest.TestCase):

    def test_a_snapshot_behind_the_checkout_is_named(self):
        links = {"hosts": {"h1": {"links": [["a", "b"]]}}}
        with tempfile.TemporaryDirectory() as d:
            Path(d, peer_channel.LINKS_FILE).write_text(json.dumps({"hosts": {}}))
            Path(d, peer_channel.REGISTRY_FILE).write_text("[]")
            with mock.patch.object(cpc, "SNAPSHOT_DIR", d):
                lines = cpc.status_lines("h1", links_doc=links, registry=[])
        self.assertTrue(any("root-apply" in ln for ln in lines), lines)


class TestRootLeg(unittest.TestCase):

    def test_a_snapshot_change_does_not_reapply_tmpfiles(self):
        script = cpc.render_root_script(["root"], links_doc={"hosts": {}}, registry=[])
        self.assertLess(script.index("TMPFILES_CHANGED="), script.index(cpc.SNAPSHOT_LINKS))
        self.assertIn('[ -n "$TMPFILES_CHANGED" ] && need=1', script)

    def test_an_unknown_host_fails(self):
        hosts = [{"name": "h1", "host": "1.1.1.1", "identity": "k"}]
        failed = cpc.provision_peer_channel_root(
            hosts=hosts, run=lambda *a, **k: None, links_doc={"hosts": {}}, registry=[],
            only="nope")
        self.assertEqual(failed, [("nope", "unknown host")])


if __name__ == "__main__":
    unittest.main()
