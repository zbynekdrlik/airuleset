"""Hook-level enforcement: a Stop turn ending on the ⏳ WORKING marker must
PROVE something will wake the session (airuleset #120).

Two real specimens (2026-07-28, one hour apart): restreamer ended its turn
"⏳ WORKING: dobehnutie release buildu 0.29.24 -- nic od teba netreba." on an
IDLE pane with nothing that would ever wake it. eft5000 ended "⏳ WORKING:
bezi novy test suite s podrobnym vypisom, strazca zachyti aj dokoncenie aj
zaseknutie" whose only live process was an ORPHAN launched inside a Bash
tool call the session had already abandoned.

`stop-check-working-liveness.sh` closes this: when the LAST non-blank line
of the message is the WORKING marker, it requires the harness's OWN
`background_tasks` payload field (confirmed present at a plain top-level
Stop event, live-captured 2026-07-28 -- see
.claude/rules/airuleset-internals.md) to carry at least one entry with
status=="running". Empty (key present) -> BLOCK. Key absent entirely
(older harness) -> fail open, nothing to check against.
"""

import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset
from _hook_state_cleanup import sweep_session_files

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "stop-check-working-liveness.sh"


def payload(msg, background_tasks="__ABSENT__", session_id=None):
    d = {
        "session_id": session_id or ("t-" + uuid.uuid4().hex[:10]),
        "hook_event_name": "Stop",
        "last_assistant_message": msg,
    }
    if background_tasks != "__ABSENT__":
        d["background_tasks"] = background_tasks
    return json.dumps(d)


def running_shell(tid="b26esxqc2", command="sleep 90"):
    return {"id": tid, "type": "shell", "status": "running", "command": command}


def running_subagent(tid="ad824a29dea11cc84"):
    return {"id": tid, "type": "subagent", "status": "running",
            "agent_type": "general-purpose"}


def completed_shell(tid="bdone1"):
    return {"id": tid, "type": "shell", "status": "completed", "command": "make"}


class HookBase(unittest.TestCase):
    def _run(self, msg, background_tasks="__ABSENT__", session_id=None):
        # #427: compute the sid HERE (rather than leaving payload() to mint
        # one _run never sees) so cleanup can be scoped to the EXACT file
        # this call may have written -- never a fixed-prefix wildcard that
        # would also match a concurrently-running SIBLING process's retry
        # file (every generated sid shares the same "t-" prefix). Swept via
        # the SAME sweep_session_files() helper #202 already uses in 8
        # other test files; safe to register once per distinct sid even
        # across several _run() calls in one test (sweep is idempotent).
        sid = session_id or ("t-" + uuid.uuid4().hex[:10])
        self.addCleanup(sweep_session_files, sid)
        return subprocess.run(
            ["bash", str(HOOK)],
            input=payload(msg, background_tasks=background_tasks,
                          session_id=sid),
            capture_output=True, text=True, timeout=15)


class TestHookExistsAndWired(unittest.TestCase):
    def test_hook_exists_and_executable(self):
        self.assertTrue(HOOK.exists(), "hooks/stop-check-working-liveness.sh missing")
        self.assertTrue(os.access(HOOK, os.X_OK))

    def test_hooks_json_wires_it_under_stop(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = [h.get("command", "") for entry in cfg["hooks"]["Stop"]
                 for h in entry.get("hooks", [])]
        self.assertTrue(any("stop-check-working-liveness.sh" in c for c in wired),
                         "hook is not registered under Stop")


class TestBlocksTheFalseWorkingClaim(HookBase):
    def test_empty_background_tasks_blocks(self):
        out = self._run("⏳ WORKING: dobehnutie release buildu 0.29.24 — nič od teba netreba.",
                         background_tasks=[])
        self.assertIn('"decision": "block"', out.stdout)

    def test_real_restreamer_specimen_blocks(self):
        # verbatim last_assistant_message from the real restreamer incident
        # transcript (2026-07-28, projects/-home-newlevel-devel-restreamer)
        msg = ("- Release beh na tag `restreamer-v0.29.24` ešte builduje "
               "artefakty (GitHub Release) — nasadenie na snv už prebehlo "
               "cez CI, toto je len balík na stiahnutie.\n"
               "- Otvorené ostáva **#347** — box je offline, spravím to hneď "
               "ako bude dostupný.\n\n"
               "⏳ WORKING: dobehnutie release buildu 0.29.24 — nič od teba netreba.")
        out = self._run(msg, background_tasks=[])
        self.assertIn('"decision": "block"', out.stdout)

    def test_real_eft5000_orphan_specimen_blocks(self):
        # verbatim last_assistant_message from the real eft5000 incident —
        # the "live" process was an orphan the harness never tracked, so the
        # true payload at that Stop would have carried an empty array too
        msg = ("služba sa vie zaseknúť ... Toto nie je moja zmena, je to "
               "vlastnosť toho čakania; ale robí to z každého takého "
               "zaseknutia nekonečný vis.\n\n"
               "⏳ WORKING: beží nový test suite s podrobným výpisom, "
               "strážca zachytí aj dokončenie aj zaseknutie — nič od teba "
               "netreba.")
        out = self._run(msg, background_tasks=[])
        self.assertIn('"decision": "block"', out.stdout)

    def test_only_non_running_entries_blocks(self):
        out = self._run("⏳ WORKING: čakám na výsledok", background_tasks=[completed_shell()])
        self.assertIn('"decision": "block"', out.stdout)

    def test_block_message_names_the_two_fixes(self):
        out = self._run("⏳ WORKING: niečo beží", background_tasks=[])
        self.assertIn("run_in_background", out.stdout)
        self.assertIn("✅ DONE", out.stdout)
        self.assertIn("❓ NEEDS YOU", out.stdout)


class TestAllowsTheGenuineLiveWaiter(HookBase):
    def test_live_background_bash_passes(self):
        out = self._run("⏳ WORKING: sleep task running in background — nothing needed.",
                         background_tasks=[running_shell()])
        self.assertNotIn('"decision"', out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_live_async_agent_passes(self):
        out = self._run("⏳ WORKING: background agent dispatched — nothing needed.",
                         background_tasks=[running_subagent()])
        self.assertNotIn('"decision"', out.stdout)

    def test_mixed_running_and_completed_passes(self):
        out = self._run("⏳ WORKING: one done, one still going",
                         background_tasks=[completed_shell(), running_shell("bx2")])
        self.assertNotIn('"decision"', out.stdout)

    def test_real_captured_live_bash_payload_passes(self):
        # verbatim shape captured live 2026-07-28 (headless claude -p probe)
        out = self._run("⏳ WORKING: sleep task running in background — nothing needed from you.",
                        background_tasks=[{
                            "id": "bn60a553k", "type": "shell", "status": "running",
                            "description": "Sleep 45s then echo marker",
                            "command": "sleep 45 && echo done-marker"}])
        self.assertNotIn('"decision"', out.stdout)

    def test_real_captured_live_agent_payload_passes(self):
        out = self._run("⏳ WORKING: background agent dispatched — nothing needed from you.",
                        background_tasks=[{
                            "id": "ad824a29dea11cc84", "type": "subagent",
                            "status": "running", "description": "Sleep then report",
                            "agent_type": "general-purpose"}])
        self.assertNotIn('"decision"', out.stdout)


class TestNotApplicableTurnsPassUntouched(HookBase):
    def test_done_marker_not_touched(self):
        out = self._run("✅ DONE: nič nebeží", background_tasks=[])
        self.assertNotIn('"decision"', out.stdout)

    def test_needs_you_marker_not_touched(self):
        out = self._run("❓ NEEDS YOU: schváliš merge?", background_tasks=[])
        self.assertNotIn('"decision"', out.stdout)

    def test_working_not_on_last_line_not_touched(self):
        msg = "⏳ WORKING mentioned mid-message.\nBut this is the actual last line, unmarked."
        out = self._run(msg, background_tasks=[])
        self.assertNotIn('"decision"', out.stdout)

    def test_unrelated_hourglass_mention_not_touched(self):
        # corpus-observed false trigger this hook must NOT reproduce: a UI
        # label discussing an hourglass toggle, not the status marker
        out = self._run("`renderOrderRow` — add `.waiting` row class + the ⏳ toggle.",
                         background_tasks=[])
        self.assertNotIn('"decision"', out.stdout)

    def test_empty_message_not_touched(self):
        out = self._run("", background_tasks=[])
        self.assertNotIn('"decision"', out.stdout)


class TestFailOpenWhenUnprovable(HookBase):
    def test_missing_background_tasks_key_passes(self):
        # harness on this version doesn't expose it at all -- nothing to check
        out = self._run("⏳ WORKING: something claimed running",
                         background_tasks="__ABSENT__")
        self.assertNotIn('"decision"', out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_no_jq_no_input_never_crashes(self):
        out = subprocess.run(["bash", str(HOOK)], input="", capture_output=True,
                              text=True, timeout=15)
        self.assertEqual(out.returncode, 0)

    def test_garbage_stdin_fails_open(self):
        out = subprocess.run(["bash", str(HOOK)], input="not json at all",
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('"decision"', out.stdout)


class TestRetryCapNeverWedgesASession(HookBase):
    def test_retry_cap_fails_open_after_max(self):
        sid = "t-" + uuid.uuid4().hex[:10]
        results = [self._run("⏳ WORKING: still claiming", background_tasks=[],
                              session_id=sid) for _ in range(5)]
        blocked = [r for r in results if '"decision": "block"' in r.stdout]
        passed = [r for r in results if '"decision"' not in r.stdout]
        # MAX_RETRIES=2 -> first 2 block, the rest fail open
        self.assertEqual(len(blocked), 2, [r.stdout for r in results])
        self.assertEqual(len(passed), 3, [r.stdout for r in results])

    def test_a_genuinely_live_call_clears_the_retry_counter(self):
        sid = "t-" + uuid.uuid4().hex[:10]
        r1 = self._run("⏳ WORKING: claim 1", background_tasks=[], session_id=sid)
        self.assertIn('"decision": "block"', r1.stdout)
        r2 = self._run("⏳ WORKING: claim 2, now genuinely live",
                        background_tasks=[running_shell()], session_id=sid)
        self.assertNotIn('"decision"', r2.stdout)
        r3 = self._run("⏳ WORKING: claim 3, false again", background_tasks=[],
                        session_id=sid)
        self.assertIn('"decision": "block"', r3.stdout)


class TestCleanupIsScopedToOwnSession(HookBase):
    """#427: `HookBase.setUp()` used to register ONE cleanup per test —
    `glob.glob("/tmp/airuleset-working-liveness-block-t-*")` — a
    FIXED-PREFIX wildcard that matches EVERY session's retry-counter file,
    not just the one(s) THIS test/process created (every generated sid
    shares the literal "t-" prefix, see `payload()` above). Harmless in a
    solo run (no sibling process has a live retry file at teardown time —
    "green standalone 22/22"), but under the fleet-dispatch architecture
    this repo runs (#317 — several worktree workers executing this SAME
    suite concurrently on the SAME shared /tmp, e.g. wave7gate3), one
    worker's own test cleanup could delete a DIFFERENT worker's in-flight
    counter file mid-sequence. Locks the fix: cleanup must only ever touch
    the retry file(s) this test's own process actually created — same
    scoped-sid idiom #202 already established (`_hook_state_cleanup.py`)."""

    def test_cleanup_never_touches_a_different_sessions_retry_file(self):
        foreign_sid = "t-" + uuid.uuid4().hex[:10]
        foreign_file = Path("/tmp/airuleset-working-liveness-block-" + foreign_sid)
        foreign_file.write_text("1")
        try:
            out = self._run("⏳ WORKING: niečo beží", background_tasks=[])
            self.assertIn('"decision": "block"', out.stdout)
            self.doCleanups()   # run only what THIS test registered so far
            self.assertTrue(
                foreign_file.exists(),
                "a DIFFERENT session's retry file must survive this test's "
                "own teardown -- #427 cross-session wildcard-cleanup bug")
        finally:
            if foreign_file.exists():
                foreign_file.unlink()

    def test_own_retry_file_is_actually_removed_by_cleanup(self):
        # #427-review F5: the sibling test above proves a FOREIGN file
        # survives, but never that this test's OWN file is genuinely
        # swept -- a mutant registering NO cleanup at all (reintroducing
        # the #202-shaped litter regression, ~2418 leftover
        # /tmp/airuleset-*-block-* files measured in one real day) would
        # still pass it. Assert the positive half explicitly.
        own_sid = "t-" + uuid.uuid4().hex[:10]
        own_file = Path("/tmp/airuleset-working-liveness-block-" + own_sid)
        out = self._run("⏳ WORKING: niečo beží", background_tasks=[],
                         session_id=own_sid)
        self.assertIn('"decision": "block"', out.stdout)
        self.assertTrue(
            own_file.exists(),
            "the hook itself must have written a retry-state file for "
            "this sid before cleanup can be meaningfully tested")
        self.doCleanups()
        self.assertFalse(
            own_file.exists(),
            "cleanup must actually REMOVE the file THIS test's own "
            "session created, not merely spare foreign ones")


class TestHarnessTimeoutHasContentionHeadroom(unittest.TestCase):
    """#444: the hook-subprocess bound in this file is a pure hang-guard --
    the hook's normal runtime is 0.03-0.04s (measured, 3 runs) -- yet a 15s
    bound was genuinely exceeded by a HEALTHY hook under this box's
    6-19-concurrent-full-suite contention (subprocess.TimeoutExpired, the
    ticket's own evidence). Lock BOTH halves of the fix: the shared
    constant keeps real contention headroom, and no call site regresses to
    a private numeric literal the constant cannot govern."""

    def test_timeout_constant_keeps_contention_headroom(self):
        self.assertGreaterEqual(
            globals().get("HOOK_TIMEOUT_S", 15), 120,
            "HOOK_TIMEOUT_S must keep ~8x headroom past the 15s bound that "
            "real suite-contention load already exceeded on a healthy hook")

    def test_no_call_site_uses_a_private_numeric_timeout(self):
        src = Path(__file__).read_text()
        hits = re.findall(r"timeout\s*=\s*\d+", src)
        self.assertEqual(
            hits, [],
            "every subprocess timeout in this file must use the shared "
            "HOOK_TIMEOUT_S constant, never a private numeric literal")


if __name__ == "__main__":
    unittest.main()
