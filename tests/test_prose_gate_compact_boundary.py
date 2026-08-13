"""#411 -- a served/stream session's own genuine `## ✅ Work Complete` report
is the ONLY durable proof (per `message-status-marker.md`'s "Compact at your
own boundary" contract) that a `/compact` boundary genuinely happened for a
NON-worker session. After #400 removed the passive text-sniffing Stop-hook
fallback entirely (a permanent no-op), `compact-request --self` is the ONLY
mechanism left that records this boundary -- but nothing MECHANICAL ever
calls it. `completion-report.md` only tells the SESSION to call it as its own
last tool call BEFORE the report text; a sonnet-tier stream session (the live
david@subdev incident this ticket was filed from) reliably skips that step.

THE FIX (mirrors `hooks/notify-compact-subagent-boundary.sh`'s own shape,
which already does the identical thing for the SubagentStop/worker-boundary
case): `hooks/stop-check-prose-violations.sh` already computes
`IS_COMPLETION_HEADING` -- the SAME canonical `^## ✅ Work Complete|^✅ Work
Complete` classifier `watchdog/compact.py`'s own `_COMPACT_COMPLETION_
HEADING_RX` anchors on -- as part of validating the report's own structure.
Once a turn reaches the "no hard violations" tail (the report is genuinely
well-formed), a completion-heading turn is by construction a real boundary,
so the hook fires ONE best-effort `airuleset.py compact-request --record
--session <sid> --cwd <cwd> --origin self-callback` call -- the SAME CLI
entry point + the SAME `self-callback` proven-boundary origin `--self`
already uses, so the exact same, already-adversarially-reviewed exemption
machinery (`_compact_self_reported_complete`, #425) applies unchanged. Never
`--self` itself: that resolves the pane via `$TMUX_PANE`, which is correct
for a session calling it as ITS OWN mid-turn tool call, but a HOOK process
has no reliable `$TMUX_PANE` of its own -- `--record` takes the payload's own
`session_id`/`cwd` directly, exactly like the SubagentStop sibling does.

These tests fake `python3` on PATH (the hook resolves `airuleset.py` via
`BASH_SOURCE`-relative path, then shells out to it) so no real request state
is ever touched, and assert on the FAKE'S OWN INVOCATION LOG -- never on
`~/.claude/compact-requests.json`, which stays completely untouched by this
test file regardless of how the fake behaves.
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# The exact fork-shaped `## ✅ Work Complete` report this repo's own sibling
# test (`tests/test_prose_gate_pipeline_race.py::TestLargeCorrectReportIsNot
# FalselyBlocked`) already proves is genuinely clean end-to-end -- reused
# here verbatim so a failure in THESE tests can never be blamed on the
# report's own structural validity.
HEAD = ("## ✅ Work Complete\n\n"
        "**Audits & deploy:**\n"
        "✅ /plan-check: 3/3 fulfilled\n"
        "✅ /review: clean — 0 🔴 0 🟡 0 🔵\n"
        "✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵\n"
        "✅ Lokálne overenie: testy + lint zelené (fork vetva david/kiosk)\n"
        "✅ Hand-off: READY-FOR-REVIEW komentár na #1393 (kiosk) + karta\n"
        # The '✅ Výstup:' content-verification line became MANDATORY for every
        # completion report (montalu3 0 € email incident) — a "genuinely clean"
        # report now carries it by definition.
        "✅ Výstup: kiosk obrazovka zobrazuje meno zamestnanca a čas 07:45\n\n")
TAIL = ("\n---\n\n"
        "**Goal:** Dochádzkový kiosk pre výrobu.\n"
        "**What changed:** Kiosk beží na erp-test-david.\n\n"
        "✅ DONE: #1393 (kiosk) odovzdané na review, nič ďalšie nečaká.")
CLEAN_COMPLETION_REPORT = HEAD + TAIL

# A well-formed, ordinary (non-completion) `⏳ WORKING` progress message --
# must NEVER trigger a compact-request call, since it carries no completion
# heading at all.
NON_COMPLETION_MSG = ("⏳ WORKING: dispatched worker for #1393, will report "
                      "when it finishes. Nothing needed from you.")

# A message carrying the completion heading but otherwise missing every
# required field -- BLOCKED by the hard-violations gate, so the "no hard
# violations" tail (where the new call lives) is never reached.
BLOCKED_COMPLETION_MSG = "## ✅ Work Complete\n\nnot enough structure here"

_FAKE_PYTHON3_TEMPLATE = """#!/usr/bin/env bash
# #411-review MINOR (fresh-context adversarial review, live-triggered): a
# fake that intercepts EVERY python3 invocation unconditionally also
# swallows the SAME hook's own OTHER python3 use -- `strip_mentions()`'s
# `python3 - "$_f" <<'PYEOF'` heredoc calls (invoked twice per turn, for
# MSG and MSG_NOGOAL). A generic "log argv, print 'sent'" reply makes
# those calls return `sent` instead of the real classification, silently
# disabling the whole mention-vs-use family for the duration of these
# tests -- demonstrated live: a genuine `gh pr merge --admin` bypass offer
# stopped being blocked at all under the naive fake. Dispatch on `$1`
# instead: the compact-request call's own argv[1] is always an absolute
# path ending in `airuleset.py`; strip_mentions's own call is always
# literally `-` (read script from stdin). Anything else falls through to
# the REAL interpreter (its absolute path baked in at write time, from
# BEFORE this fake ever went on PATH) so no other python3 use in the hook
# is ever affected by this fake's presence.
if [ "$1" = "-" ]; then
    exec "%s" "$@"
fi
echo "$*" >> "%s"
printf 'sent'
"""


class _HookCase(unittest.TestCase):
    """Shared harness: an isolated scratch dir holding a fake `python3` on
    PATH, so the hook's own `python3 "$AIRULESET_PY" compact-request ...`
    call is observed rather than reaching any real state file."""

    def setUp(self):
        # Resolved BEFORE the fake bindir goes on PATH -- the fake's own
        # passthrough branch execs this absolute path, never a bare
        # `python3` (which would recurse into the fake itself).
        real_python3 = shutil.which("python3")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        self.fake_log = Path(self.tmp.name) / "python3-calls.log"
        fake = bindir / "python3"
        fake.write_text(_FAKE_PYTHON3_TEMPLATE % (real_python3, str(self.fake_log)))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.env = dict(os.environ)
        self.env["PATH"] = "%s:%s" % (bindir, os.environ.get("PATH", ""))

    def _run(self, msg, session_id=None, cwd=None):
        sid = session_id if session_id is not None else (
            "pcb-%s" % uuid.uuid4().hex[:12])
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(missing_ok=True))
        payload = {"last_assistant_message": msg, "session_id": sid}
        if cwd is not None:
            payload["cwd"] = cwd
        r = subprocess.run(
            ["bash", str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, env=self.env)
        return r

    def _calls(self):
        if not self.fake_log.exists():
            return []
        return [ln for ln in self.fake_log.read_text().splitlines() if ln.strip()]

    def _compact_request_calls(self):
        return [ln for ln in self._calls() if "compact-request" in ln]


class TestCompactRequestFiresOnGenuineCompletion(_HookCase):
    """The core positive case -- #411's own reported gap."""

    def test_clean_completion_report_triggers_a_compact_request_call(self):
        r = self._run(CLEAN_COMPLETION_REPORT, session_id="pcb-clean-1",
                      cwd="/home/x/devel/some-repo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('"decision"', r.stdout, "a clean report must never block")
        calls = self._compact_request_calls()
        self.assertEqual(
            len(calls), 1,
            "expected exactly one compact-request call, got: %r" % calls)
        self.assertIn("--record", calls[0])
        self.assertIn("--session pcb-clean-1", calls[0])
        self.assertIn("--cwd /home/x/devel/some-repo", calls[0])
        self.assertIn("--origin self-callback", calls[0])

    def test_the_call_never_reaches_stdout_or_blocks(self):
        """Best-effort: the hook's own decision output must be completely
        unaffected by whatever the compact-request call prints/returns."""
        r = self._run(CLEAN_COMPLETION_REPORT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("sent", r.stdout)
        self.assertNotIn('"decision"', r.stdout)


class TestCompactRequestNeverFiresOnANonCompletionTurn(_HookCase):

    def test_a_plain_working_message_never_calls_compact_request(self):
        r = self._run(NON_COMPLETION_MSG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._compact_request_calls(), [])


class TestCompactRequestNeverFiresWhenTheReportIsBlocked(_HookCase):

    def test_a_blocked_completion_report_never_calls_compact_request(self):
        """The heading is present, but the report is otherwise malformed --
        the turn is BLOCKED before ever reaching the 'no hard violations'
        tail where the new call lives."""
        r = self._run(BLOCKED_COMPLETION_MSG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"decision"', r.stdout, "expected this message to be blocked")
        self.assertEqual(self._compact_request_calls(), [])


if __name__ == "__main__":
    unittest.main()
