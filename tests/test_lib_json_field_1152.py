"""#1152: `hooks/lib-json-field.sh`, the jq-first payload reader with a
fork-free fallback parser.

The fallback only runs when jq fails, and the value it returns becomes the
turn's message and session id. It must therefore decode exactly what
`json.loads` decodes, for every escape JSON allows, including payloads that
Python writes with `ensure_ascii` on (every non-ASCII char as \\uXXXX,
astral chars as surrogate pairs) and off (raw UTF-8, which Claude Code
sends). jq is forced to fail with a PATH shim.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "hooks" / "lib-json-field.sh"

CASES = [
    "plain",
    "## ✅ Work Complete\n\n✅ DONE: #41 zmergnuté -> v1.2.3",
    'a quote " a backslash \\ a slash / tab\t cr\r ff\f bs\b',
    "astral 🎫 🚀 and BMP ✅ čšžťľ",
    "a literal \\u2705 and a literal \\\\n stay literal",
    'a quoted key inside a value: "session_id": "evil"',
    "trailing newline\n",
]

# The payload goes in on stdin, like a hook's: one argv string is capped at
# 128 KiB by the kernel.
READ = ('set -euo pipefail; . "$1"; P=$(cat); rc=0; json_str_field "$P" "$2" '
        '|| rc=$?; printf "%s\\001%s\\001%s" "$rc" "$JSON_FIELD_VIA" '
        '"$JSON_FIELD_VALUE"')


class JsonStrField(unittest.TestCase):

    def setUp(self):
        self.bin = Path(tempfile.mkdtemp(prefix="airuleset-1152-jq-"))
        self.addCleanup(shutil.rmtree, self.bin, True)
        (self.bin / "jq").write_text("#!/usr/bin/env bash\nexit 1\n")
        (self.bin / "jq").chmod(0o755)

    def read(self, payload, key, jq_fails=True):
        path = os.environ["PATH"]
        if jq_fails:
            path = str(self.bin) + os.pathsep + path
        r = subprocess.run(["bash", "-c", READ, "x", str(LIB), key],
                           input=payload.encode("utf-8"), capture_output=True,
                           env=hermetic_hook_env(self, PATH=path))
        self.assertEqual(r.returncode, 0, r)
        rc, via, value = r.stdout.split(b"\x01", 2)
        return int(rc), via.decode(), value.decode("utf-8")

    def test_the_fallback_decodes_exactly_what_json_loads_decodes(self):
        for ensure_ascii in (True, False):
            for text in CASES:
                payload = json.dumps({"session_id": "s-1", "cwd": "/w",
                                      "last_assistant_message": text},
                                     ensure_ascii=ensure_ascii)
                with self.subTest(ensure_ascii=ensure_ascii, text=text):
                    rc, via, value = self.read(payload, "last_assistant_message")
                    self.assertEqual((rc, via), (0, "fallback"))
                    # `$(...)` in the hook strips trailing newlines on the jq
                    # path too; compare like for like.
                    self.assertEqual(value, text.rstrip("\n"))

    def test_jq_answers_first_when_it_works(self):
        payload = json.dumps({"session_id": "s-1"})
        self.assertEqual(self.read(payload, "session_id", jq_fails=False),
                         (0, "jq", "s-1"))

    def test_a_key_quoted_inside_another_value_is_not_read_as_the_key(self):
        payload = json.dumps({"last_assistant_message": '"session_id": "evil"',
                              "session_id": "real"})
        self.assertEqual(self.read(payload, "session_id"),
                         (0, "fallback", "real"))

    def test_an_unreadable_payload_reports_rc_1(self):
        self.assertEqual(self.read("this is not json", "session_id"),
                         (1, "fallback", ""))

    def test_a_large_report_decodes_in_linear_time(self):
        # 300 KB with 50 000 escapes: about 2 s idle on a 2-core box. The
        # quadratic `${s//…}` replacements this replaced needed about 30 s,
        # so the bound catches a regression and still leaves 10x headroom
        # for a loaded CI runner.
        text = ("riadok č. %d ✅ \"ok\"\n" * 10000) % tuple(range(10000))
        payload = json.dumps({"last_assistant_message": text})
        t = time.monotonic()
        rc, via, value = self.read(payload, "last_assistant_message")
        self.assertEqual((rc, via), (0, "fallback"))
        self.assertEqual(value, text.rstrip("\n"))
        self.assertLess(time.monotonic() - t, 20.0)


if __name__ == "__main__":
    unittest.main()
