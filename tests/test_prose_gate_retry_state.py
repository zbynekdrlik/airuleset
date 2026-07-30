"""#196 + #198 — the prose gate's retry bookkeeping must never be able to
suppress the verdict it is bookkeeping for.

Both defects live in the same eight lines of
`hooks/stop-check-prose-violations.sh`, and #198 poisons the environment #196
has to be tested in, so they are locked together here.

MEASURED against 03874a2 before the fix, real hook, one unique session id per
call, message `Let's just merge --admin this one, the check is informational
only.`:

    baseline, fresh unique session                rc=0  BLOCKED=True
    counter file mode 0444                        rc=1  BLOCKED=False
    session_id containing '/'                     rc=1  BLOCKED=False
    counter holding `not-a-number`                rc=0  BLOCKED=False
    no session_id, shared bucket at 5             rc=0  BLOCKED=False
    real session id, counter at 5, mtime -2 days  rc=0  BLOCKED=False
    a genuinely CLEAN message                     rc=0  BLOCKED=False

Note the last two rows. At the cap, a message carrying a quality-bypass phrase
and a clean message are byte-identical to any caller: rc 0, empty stdout.

The rule the fix encodes, and the one these tests hold: THE THROTTLE IS USED
ONLY WHEN THIS INVOCATION'S OWN RETRY STATE IS POSITIVELY ESTABLISHED. No
session id, an id that is not a safe path component, a counter that is not
digits, a counter of unknown or stale age -> no state, `RETRIES=0`, the verdict
goes out. That is the settled per-polarity fail direction for bookkeeping state:
it may never suppress a verdict.

Losing the throttle is a degradation, not a runaway -- Claude Code's own
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8) overrides ANY blocking Stop hook
after 8 consecutive blocking Stops, so the counter is a courtesy throttle and
never the loop's only bound.

One test writes `/tmp/airuleset-stop-block-unknown`, the shared per-BOX bucket,
because its whole subject is that this path must not be consulted. Post-fix the
hook never touches it, so the write is inert; it is restored to its prior
content on teardown either way.
"""

import json
import os
import re
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

# The one shared, never-expiring bucket every session-id-less invocation used
# to land in. Named here so a reader can grep it; never used as a key by the
# fixed hook.
SHARED_BUCKET = Path("/tmp/airuleset-stop-block-unknown")

RED = "\U0001f534"
YELLOW = "\U0001f7e1"
BLUE = "\U0001f535"

# An unambiguous quality-bypass offer: a HARD violation decided by a `-qiE`
# grep, so the verdict under test never depends on a PCRE limit.
BYPASS_MSG = "Let's just merge --admin this one, the check is informational only.\n"
CLEAN_MSG = "Fixed the typo in the README. Nothing else to report.\n"


def _max_retries():
    """Read the cap out of the hook so this file cannot drift from it."""
    m = re.search(r"^MAX_RETRIES=(\d+)", HOOK.read_text(), re.M)
    if not m:  # pragma: no cover - the constant is part of the contract
        raise AssertionError("MAX_RETRIES is no longer a literal in the hook")
    return int(m.group(1))


MAX_RETRIES = _max_retries()


class _HookCase(unittest.TestCase):

    def _counter(self, sid):
        p = Path("/tmp/airuleset-stop-block-%s" % sid)
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return p

    def _sid(self):
        sid = "retry-%s" % uuid.uuid4().hex[:12]
        self._counter(sid)
        return sid

    def _run(self, msg=BYPASS_MSG, sid=None):
        payload = {"last_assistant_message": msg}
        if sid is not None:
            payload["session_id"] = sid
        return subprocess.run(
            ["bash", str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=300)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def assertBlocks(self, r, why):
        self.assertTrue(
            self._blocked(r),
            "%s -- the gate emitted no block. rc=%s stdout=%r stderr=%r"
            % (why, r.returncode, r.stdout[:200], r.stderr.strip()[-300:]))
        self.assertEqual(
            r.returncode, 0,
            "%s -- the gate blocked but exited non-zero, which the harness"
            " reports as a hook ERROR. stderr=%r"
            % (why, r.stderr.strip()[-300:]))


# --------------------------------------------------------------------------- #
# #196 -- bookkeeping must not be able to delete the verdict
# --------------------------------------------------------------------------- #

class TestBookkeepingNeverSuppressesTheVerdict(_HookCase):
    """`set -euo pipefail`, the counter write at line 735, the verdict at 737.

    A failed redirect exits the shell before the verdict exists; a counter that
    is not a number makes `[` exit 2, so the `&&` chain is false and the branch
    never runs at all. In each case a real quality-bypass offer ships."""

    def test_control_a_writable_counter_blocks(self):
        """Fixture check. Without it, every failure below could mean the
        message simply does not trip the gate."""
        r = self._run(sid=self._sid())
        self.assertBlocks(r, "a plain unique session")

    def test_an_unwritable_counter_still_blocks(self):
        sid = self._sid()
        p = self._counter(sid)
        p.write_text("0\n")
        os.chmod(p, 0o444)
        self.addCleanup(lambda: os.chmod(p, 0o644) if p.exists() else None)
        r = self._run(sid=sid)
        self.assertBlocks(r, "the counter file was unwritable (mode 0444)")

    def test_an_unbuildable_counter_path_still_blocks(self):
        """A `/` in the id makes the counter path name a directory that does
        not exist. The redirect fails with ENOENT."""
        r = self._run(sid="a/b-%s" % uuid.uuid4().hex[:8])
        self.assertBlocks(r, "the counter path could not be built")

    def test_a_non_numeric_counter_still_blocks(self):
        """The quietest of the three: `[` exits 2, the `&&` is false, and the
        hook exits 0 with no JSON and no complaint."""
        sid = self._sid()
        self._counter(sid).write_text("not-a-number\n")
        r = self._run(sid=sid)
        self.assertBlocks(r, "the counter held non-numeric text")

    def test_an_empty_counter_still_blocks(self):
        """A truncated write leaves an empty file, which `[` rejects the same
        way."""
        sid = self._sid()
        self._counter(sid).write_text("")
        r = self._run(sid=sid)
        self.assertBlocks(r, "the counter file was empty")

    def test_a_clean_message_stays_silent_when_the_counter_is_unwritable(self):
        """Control for the opposite direction: hardening the bookkeeping must
        not turn a clean message into a block or into a hook error."""
        sid = self._sid()
        p = self._counter(sid)
        p.write_text("0\n")
        os.chmod(p, 0o444)
        self.addCleanup(lambda: os.chmod(p, 0o644) if p.exists() else None)
        r = self._run(CLEAN_MSG, sid=sid)
        self.assertEqual(r.returncode, 0, r.stderr.strip()[-300:])
        self.assertEqual(r.stdout, "",
                         "a clean message produced output: %r" % r.stdout[:200])


# --------------------------------------------------------------------------- #
# #198 -- no session id must not resolve to a shared, immortal counter
# --------------------------------------------------------------------------- #

class TestUnknownSessionIsNotAThrottleBucket(_HookCase):

    def setUp(self):
        self._prior = (SHARED_BUCKET.read_text()
                       if SHARED_BUCKET.exists() else None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prior is None:
            SHARED_BUCKET.unlink(missing_ok=True)
        else:
            SHARED_BUCKET.write_text(self._prior)

    def test_a_poisoned_shared_bucket_does_not_disarm_a_no_session_call(self):
        """The live shape: `/tmp/airuleset-stop-block-unknown` was already at 5
        on dev1 from unrelated prior activity, so every session-id-less
        invocation was silently unguarded -- for the life of the box's /tmp."""
        SHARED_BUCKET.write_text("%d\n" % MAX_RETRIES)
        r = self._run(sid=None)
        self.assertBlocks(r, "a shared per-box counter at the cap was consulted"
                             " for an invocation that has no session id")

    def test_a_no_session_call_does_not_write_the_shared_bucket(self):
        """Below the cap the pre-fix hook blocked AND incremented the shared
        bucket, which is how it reached 5 in the first place. The fixed hook
        keeps no state at all for an unidentifiable invocation."""
        SHARED_BUCKET.write_text("1\n")
        self._run(sid=None)
        self.assertEqual(
            SHARED_BUCKET.read_text(), "1\n",
            "the hook advanced a per-BOX counter shared by every session-id-less"
            " invocation on this machine")

    def test_the_194_reproducer_without_a_session_id_blocks(self):
        """#198's own acceptance: this exact invocation is what read a shipped,
        correct, deployed fix as broken. The report drives the `/review` probe
        with repeated counters, so it also exercises the #194 path."""
        SHARED_BUCKET.write_text("%d\n" % MAX_RETRIES)
        report = ("## ✅ Work Complete\n\n"
                  + "/review: " + ("0 " + RED) * 5000 + "\n"
                  + "plan-check: 3/3 fulfilled\n"
                  + "requesting-code-review: clean\n"
                  + "Goal: x\nWhat changed: y\n\n"
                  + "I will merge despite the failing check.\n")
        r = self._run(report, sid=None)
        self.assertBlocks(r, "the #194 reproducer run without a session id")

    def test_a_hostile_session_id_is_not_used_to_build_a_path(self):
        """An id that is not already a safe path component gets no state --
        never a mangled key, which would be many-to-one and so a shared bucket
        with a new spelling."""
        tag = "airuleset-pwn-%s" % uuid.uuid4().hex[:8]
        stray = Path("/tmp/%s" % tag)
        self.addCleanup(lambda: stray.unlink(missing_ok=True))
        r = self._run(sid="../%s" % tag)
        self.assertBlocks(r, "a hostile session id")
        self.assertFalse(stray.exists(),
                         "the hook wrote outside its own counter namespace")

    def test_a_semicolon_in_the_session_id_still_blocks(self):
        """Control, not a reproduction: `;` is a legal filename character and
        the path is a quoted variable, so this blocked before the fix too. It
        pins that REFUSING such an id as a key does not cost the verdict."""
        r = self._run(sid="a;b-%s" % uuid.uuid4().hex[:8])
        self.assertBlocks(r, "a session id carrying a shell metacharacter")


# --------------------------------------------------------------------------- #
# #198 -- the counter must expire
# --------------------------------------------------------------------------- #

class TestAStaleCounterCannotDisarmALaterSession(_HookCase):
    """A per-session key is NOT enough on its own: `claude -c` reuses a session
    id across a restart, so the key does not die with the session."""

    def test_a_counter_at_the_cap_from_two_days_ago_still_blocks(self):
        sid = self._sid()
        p = self._counter(sid)
        p.write_text("%d\n" % MAX_RETRIES)
        old = os.stat(p).st_mtime - 172800
        os.utime(p, (old, old))
        r = self._run(sid=sid)
        self.assertBlocks(r, "a two-day-old counter at the cap")

    def test_a_fresh_counter_below_the_cap_is_still_counted(self):
        """Control: expiry must not delete the throttle. A fresh counter is
        read AND advanced."""
        sid = self._sid()
        p = self._counter(sid)
        p.write_text("2\n")
        r = self._run(sid=sid)
        self.assertBlocks(r, "a fresh counter below the cap")
        self.assertEqual(p.read_text().strip(), "3",
                         "the counter stopped advancing, so the throttle no"
                         " longer bounds anything")


# --------------------------------------------------------------------------- #
# #198 -- exhausting the cap must be visible
# --------------------------------------------------------------------------- #

class TestCapExhaustionIsVisible(_HookCase):

    def _at_cap(self):
        sid = self._sid()
        self._counter(sid).write_text("%d\n" % MAX_RETRIES)
        return sid

    def test_the_cap_still_stops_a_genuine_loop(self):
        """Control: the throttle is still a throttle. This must pass both
        before and after the fix."""
        r = self._run(sid=self._at_cap())
        self.assertFalse(self._blocked(r),
                         "the retry cap no longer bounds the block loop")
        self.assertEqual(r.returncode, 0, r.stderr.strip()[-300:])

    def test_cap_exhaustion_is_distinguishable_from_a_clean_message(self):
        """The reported harm: rc 0 and an empty stdout, identical to a message
        that carried no violation at all. A verification cannot tell a real
        bypass from a clean pass."""
        exhausted = self._run(sid=self._at_cap())
        clean = self._run(CLEAN_MSG, sid=self._sid())
        self.assertNotEqual(
            (exhausted.returncode, exhausted.stdout),
            (clean.returncode, clean.stdout),
            "a message carrying an unfixed hard violation is byte-identical to"
            " a clean one: rc=%s stdout=%r"
            % (exhausted.returncode, exhausted.stdout[:200]))

    def test_cap_exhaustion_reports_what_was_let_through(self):
        r = self._run(sid=self._at_cap())
        self.assertTrue(r.stdout.strip(),
                        "the cap branch still emits nothing on stdout")
        try:
            payload = json.loads(r.stdout)
        except ValueError:
            self.fail("the cap branch emitted non-JSON on stdout, which a Stop"
                      " hook's caller parses as a decision: %r" % r.stdout[:200])
        self.assertNotIn(
            "decision", payload,
            "the cap branch emitted a DECISION, which would either re-block"
            " (the runaway the cap exists to stop) or assert something the hook"
            " has no business asserting: %r" % payload)
        blob = json.dumps(payload).lower()
        self.assertIn("airuleset", blob, payload)
        self.assertTrue(
            "cap" in blob or "retry" in blob,
            "the message does not say the retry cap was exhausted: %r" % payload)


if __name__ == "__main__":
    unittest.main()
