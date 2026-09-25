"""#1153 (c) — every tool's OUTPUT is scrubbed of stored credential values.

Verified on Claude Code 2.1.281 (ticket comment 5828264462): a PostToolUse
hook's `updatedToolOutput` replaces a BUILT-IN tool's result before Claude sees
it, and the session jsonl persists the replacement, not the original. So a
credential-store value that reaches ANY tool's output (a verbose curl, a
config dump, a log file Read) no longer has to reach the transcript.

The hook preserves the tool's output SHAPE exactly (the docs: a replacement
that does not match a built-in tool's schema is ignored), rewrites only the
strings that carried a value, and emits nothing at all when there is nothing
to redact — so the common case costs one builtin glob and no rewrite.

All values here are fakes in a throwaway store under the system temp dir.
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
FAKE = "zq7Fake1153StoreValue0x9"
MARK = "<<REDACTED>>"


class Redaction(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.home = base / "home"
        self.home.mkdir()
        self.store = base / "store"
        self.store.mkdir(mode=0o700)

    def tearDown(self):
        self._td.cleanup()

    def _store(self, name, value):
        p = self.store / (name + ".secret")
        p.write_bytes(value)
        os.chmod(p, 0o600)

    def run_hook(self, response, tool="Bash", raw=None):
        payload = raw if raw is not None else json.dumps({
            "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": {"command": "x"}, "tool_response": response})
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home),
               "AIRULESET_SECRETS_DIR": str(self.store)}
        return subprocess.run(["/bin/bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=env)

    def _updated(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        return hso["updatedToolOutput"]

    def test_bash_stdout_is_redacted_and_the_shape_kept(self):
        self._store("DB_PASS", FAKE.encode())
        resp = {"stdout": "token=%s ok\n" % FAKE, "stderr": "warn %s" % FAKE,
                "interrupted": False, "isImage": False, "noOutputExpected": False}
        r = self.run_hook(resp)
        upd = self._updated(r)
        self.assertEqual(sorted(upd), sorted(resp))
        self.assertEqual(upd["stdout"], "token=%s ok\n" % MARK)
        self.assertEqual(upd["stderr"], "warn %s" % MARK)
        self.assertIs(upd["interrupted"], False)
        self.assertNotIn(FAKE, r.stdout + r.stderr)

    def test_an_escaped_rendering_is_redacted_too(self):
        # Reuses the `secret exec` filter, so JSON/percent/base64 renderings
        # of the value are caught, not only the raw bytes.
        value = 'p"a\\ss/%s' % FAKE
        self._store("Q", value.encode())
        resp = {"stdout": json.dumps({"pw": value}), "stderr": "",
                "interrupted": False, "isImage": False}
        r = self.run_hook(resp)
        upd = self._updated(r)
        self.assertNotIn(FAKE, upd["stdout"])
        self.assertIn(MARK, upd["stdout"])

    def test_a_read_tool_result_nested_shape(self):
        self._store("API", FAKE.encode())
        resp = {"type": "text", "file": {"filePath": "/tmp/app.log",
                                          "content": "line1\nkey %s\n" % FAKE,
                                          "numLines": 2, "startLine": 1, "totalLines": 2}}
        r = self.run_hook(resp, tool="Read")
        upd = self._updated(r)
        self.assertEqual(upd["file"]["content"], "line1\nkey %s\n" % MARK)
        self.assertEqual(upd["file"]["numLines"], 2)
        self.assertEqual(upd["file"]["filePath"], "/tmp/app.log")

    def test_a_list_and_a_plain_string_result(self):
        self._store("API", FAKE.encode())
        r = self.run_hook(["a", {"b": FAKE}])
        self.assertEqual(self._updated(r), ["a", {"b": MARK}])
        r = self.run_hook("prefix %s" % FAKE, tool="mcp__x__y")
        self.assertEqual(self._updated(r), "prefix %s" % MARK)

    def test_several_values(self):
        self._store("A", FAKE.encode())
        self._store("B", b"another-fake-1153-value")
        r = self.run_hook({"stdout": "%s / another-fake-1153-value" % FAKE,
                           "stderr": "", "interrupted": False, "isImage": False})
        self.assertEqual(self._updated(r)["stdout"], "%s / %s" % (MARK, MARK))

    def test_nothing_to_redact_emits_nothing(self):
        self._store("A", FAKE.encode())
        r = self.run_hook({"stdout": "clean output", "stderr": "",
                           "interrupted": False, "isImage": False})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_an_empty_store_emits_nothing(self):
        r = self.run_hook({"stdout": "anything %s" % FAKE, "stderr": "",
                           "interrupted": False, "isImage": False})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_a_too_short_value_is_not_used(self):
        # The `secret exec` filter's own floor: at < 4 bytes a value matches
        # ordinary text everywhere and would shred every output.
        self._store("PIN", b"ab")
        r = self.run_hook({"stdout": "about abba", "stderr": "",
                           "interrupted": False, "isImage": False})
        self.assertEqual(r.stdout.strip(), "")

    def test_a_large_output_stays_well_inside_the_hook_timeout(self):
        self._store("A", FAKE.encode())
        big = ("x" * 1000 + "\n") * 2000 + FAKE
        t0 = time.monotonic()
        r = self.run_hook({"stdout": big, "stderr": "", "interrupted": False,
                           "isImage": False})
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertTrue(self._updated(r)["stdout"].endswith(MARK))

    def test_an_unreadable_payload_is_loud_not_blocking(self):
        self._store("A", FAKE.encode())
        r = self.run_hook(None, raw="{not json")
        self.assertNotEqual(r.returncode, 2)       # PostToolUse exit 2 = feedback
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("redact-vault-output", r.stderr)

    def test_the_hook_never_prints_a_value(self):
        self._store("A", FAKE.encode())
        r = self.run_hook({"stdout": FAKE * 3, "stderr": FAKE, "interrupted": False,
                           "isImage": False})
        self.assertNotIn(FAKE, r.stdout)
        self.assertNotIn(FAKE, r.stderr)


class Wiring(unittest.TestCase):
    def test_registered_for_every_tool_after_it_runs(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        entries = cfg["hooks"]["PostToolUse"]
        hit = [e for e in entries
               if any("redact-vault-output.sh" in h.get("command", "")
                      for h in e.get("hooks", []))]
        self.assertEqual(len(hit), 1, "exactly one PostToolUse registration")
        self.assertEqual(hit[0].get("matcher", ""), "",
                         "the empty matcher = every tool, built-in and MCP")


if __name__ == "__main__":
    unittest.main()
