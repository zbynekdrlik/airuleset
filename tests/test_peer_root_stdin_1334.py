"""#1334 live failure (push 0.1.664, 15.11.): the subdev root leg failed with
`OSError(7, 'Argument list too long')`. The rendered root script (which embeds
peer_channel.py and the config) went to ssh as ONE argv string `bash -c '<script>'`,
and Linux caps a single argument at 128 KiB (MAX_ARG_STRLEN). The script must go
on stdin to `bash -s`, never inside the argv."""
import unittest
from unittest import mock

import cli_peer_channel as cpc


class TestRootScriptOnStdin(unittest.TestCase):
    def _run(self, script):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        hosts = [{"name": "subdev", "host": "100.118.174.27",
                  "identity": "~/.ssh/k", "admin_user": "root"}]
        with mock.patch.object(cpc, "validate_links", return_value=[]), \
                mock.patch.object(cpc, "apply_home_policy", return_value=[]), \
                mock.patch.object(cpc, "accounts_for_host", return_value=["david3"]), \
                mock.patch.object(cpc, "render_root_script", return_value=script):
            failed = cpc.provision_peer_channel_root(
                hosts=hosts, run=run, links_doc={"hosts": {"subdev": {}}}, registry=[])
        self.assertEqual(failed, [])
        (argv, kw), = calls
        return argv, kw

    def test_large_script_goes_on_stdin_not_argv(self):
        script = "echo x\n" * 40000          # ~280 KB, over MAX_ARG_STRLEN
        argv, kw = self._run(script)
        self.assertLess(max(len(a) for a in argv), 131072)
        self.assertEqual(kw.get("input"), script)
        self.assertTrue(argv[-1].endswith("bash -s"), argv[-1])

    def test_small_script_also_on_stdin(self):
        argv, kw = self._run("echo ok\n")
        self.assertEqual(kw.get("input"), "echo ok\n")
        self.assertNotIn("echo ok", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
