"""Tests for the api-watchdog text-emitted tool-call stall detector (job 4a).

A tool call the model emits as TEXT (`<invoke name="...">…</invoke>` inside an
assistant text block) never runs → the turn ends → the session sits idle while
still LOOKING like it was about to act. Job 4a detects this from the transcript
shape and nudges immediately. These tests lock the detector's precision (it must
NOT fire on a meta-conversation that merely discusses `<invoke>` markup — like this
very repo) and the run_once wiring.
"""

import datetime
import hashlib
import json
import os
import subprocess
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

import watchdog as wd


def _spawn_dummy_proc(testcase):
    """A real, short-lived subprocess (`sleep 60`) for #82's process-
    fingerprint tests — mirrors the identical helper in
    tests/test_compact_request.py (kept local rather than cross-imported,
    same convention this file already uses for its own transcript-seeding
    helpers). Always killed in cleanup, even if the test already
    terminated it.
    # airuleset:script-ok best-effort test cleanup of a process the test
    # may have already killed itself -- nothing left to log or handle.
    """
    p = subprocess.Popen(["sleep", "60"])

    def _cleanup():
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass
    testcase.addCleanup(_cleanup)
    return p


def _alive_proc_fingerprint(testcase):
    """#83 -- a genuine, currently-alive process fingerprint (the
    `_proc_fingerprint` `{"pid", "starttime"}` shape) for tests that need a
    `/compact` claim to PERSIST across multiple evaluations, exactly like a
    real production send does (there, `_pane_claude_proc_fingerprint`
    resolves a REAL running `claude` process, so the claim always carries a
    "proc" key). The fake tmux `run`s in this file can never walk a real
    /proc tree (their `display-message` fakes return a bogus pane pid), so
    without this helper a claim they set always ends up "proc"-less --
    and, per #83, a proc-less entry is now (correctly) dropped and made
    eligible again on its very NEXT evaluation, breaking any test that
    expects persistence across sweeps."""
    p = _spawn_dummy_proc(testcase)
    return wd._proc_fingerprint(p.pid)


def _write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _assistant(text, **extra):
    e = {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": text}]}}
    e.update(extra)
    return e


def _assistant_tooluse(name="Read"):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def _system():
    return {"type": "system", "content": ""}


# the exact shape that stalled camera-box (PR #305): garbled lead-in + a Read call
# rendered as literal text, then trailing hook/system entries.
CAMERA_BOX_TEXT = ('court <invoke name="Read"><parameter name="file_path">'
                   '/tmp/x/tasks/b0kqzh3do.output</parameter></invoke>')


class TextToolcallStallDetector(unittest.TestCase):
    def _stall(self, entries):
        with TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            _write_jsonl(p, entries)
            return wd.transcript_text_toolcall_stall(p)

    def test_camera_box_incident_is_a_stall(self):
        self.assertTrue(self._stall([
            _assistant("Earlier normal turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(), _system(),          # hook noise after the broken turn
        ]))

    def test_antml_prefixed_invoke_is_a_stall(self):
        txt = ('Pozriem výstup. <invoke name="Bash">'
               '<parameter name="command">ls</parameter></invoke>')
        self.assertTrue(self._stall([_assistant(txt), _system()]))

    def test_unclosed_invoke_tail_is_a_stall(self):
        # turn died mid-emit — opening tag with no close, still the trailing content
        self.assertTrue(self._stall([_assistant('Reading it now <invoke name="Read">')]))

    def test_meta_discussion_does_not_match(self):
        # this repo literally documents <invoke> — markup buried mid-prose, normal
        # marker at the tail → MUST NOT fire (the key false-positive guard).
        txt = ("The failure mode: the model emits `<invoke name=\"Read\">` as text. "
               + ("Explanation continues. " * 30)
               + "\n\n✅ DONE: vysvetlené, žiadna akcia.")
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_real_tool_use_is_not_a_stall(self):
        # a parsed tool_use block means the harness DID call the tool
        self.assertFalse(self._stall([_assistant("Earlier"), _assistant_tooluse("Read")]))

    def test_api_error_is_not_a_textcall_stall(self):
        # job 1 owns api errors, even if the text happens to contain <invoke
        self.assertFalse(self._stall([
            _assistant("API Error: overloaded <invoke name=\"Read\">",
                       isApiErrorMessage=True)]))

    def test_progressed_user_entry_is_not_a_stall(self):
        # a user / tool_result entry AFTER the text-invoke → conversation moved on
        self.assertFalse(self._stall([
            _assistant(CAMERA_BOX_TEXT),
            {"type": "user", "message": {"role": "user", "content": "ok"}},
        ]))

    def test_normal_marker_turn_is_not_a_stall(self):
        self.assertFalse(self._stall([_assistant("Done.\n\n⏳ WORKING: CI beží.")]))

    def test_synthetic_sentinel_is_skipped(self):
        # trailing "No response requested." must not mask the real broken turn before it
        self.assertTrue(self._stall([
            _assistant(CAMERA_BOX_TEXT),
            _assistant("No response requested."),
        ]))

    def test_missing_transcript_is_not_a_stall(self):
        self.assertFalse(wd.transcript_text_toolcall_stall("/no/such/file.jsonl"))

    # --- precision guards added after adversarial review (tail-window was too loose) ---

    def test_short_completion_report_mention_does_not_match(self):
        # a SHORT report that mentions <invoke> within 400 chars of the end then ends
        # on a status marker — the old tail_window=400 heuristic wrongly fired here
        txt = ("Fixed it. The model emitted `<invoke name=\"Read\">` as text, so it "
               "never ran.\n\n✅ DONE: opravené, nasadené.")
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_inline_quoted_mention_at_end_does_not_match(self):
        # the markup is the last thing mentioned, but quoted + punctuation after it
        self.assertFalse(self._stall([
            _assistant("The opening tag is `<invoke name=\"Read\">`.")]))

    def test_unclosed_tag_with_prose_after_does_not_match(self):
        # an unclosed <invoke ...> followed by a natural-language sentence = discussion
        self.assertFalse(self._stall([
            _assistant("You write <invoke name=\"Read\"> to call a tool in the harness.")]))

    def test_closing_tag_in_a_fence_does_not_match(self):
        # a fenced example whose block closes, then the code fence closes after it
        txt = ("Example:\n```\n<invoke name=\"Read\"><parameter name=\"file_path\">"
               "/x</parameter></invoke>\n```")
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_block_in_unterminated_fence_does_not_match(self):
        # a debug note that pastes the block inside an OPEN code fence (no closing ```)
        txt = ("Here is the literal text that never ran:\n```\ncourt "
               "<invoke name=\"Read\"><parameter name=\"file_path\">/x</parameter></invoke>")
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_blockquoted_example_does_not_match(self):
        # a markdown blockquote example — a real emitted call is never blockquoted
        txt = "> <invoke name=\"Read\"><parameter name=\"file_path\">/x</parameter></invoke>"
        self.assertFalse(self._stall([_assistant(txt)]))

    def test_bare_final_invoke_block_is_treated_as_stall(self):
        # ACCEPTED RESIDUAL (documented in _ends_with_toolcall): a marker-LESS message
        # whose final content is a bare, unfenced, unquoted block is indistinguishable
        # from a real stall → True. The hook-enforced status-marker convention protects
        # compliant turns; the worst case is one benign stuck-check the session answers.
        txt = ("I confirmed it. The last assistant text was literally:\n\ncourt "
               "<invoke name=\"Read\"><parameter name=\"file_path\">/x</parameter></invoke>")
        self.assertTrue(self._stall([_assistant(txt)]))

    def test_inflight_tooluse_over_prior_textcall_does_not_match(self):
        # ORDERING GUARD: a real in-flight tool_use is the last entry (empty text) — it
        # must short-circuit to False, NOT be skipped as an empty sentinel and let the
        # scan walk back to a prior stall-shaped message and wrongly fire.
        self.assertFalse(self._stall([
            _assistant(CAMERA_BOX_TEXT),       # an earlier stall-shaped message
            _assistant_tooluse("Bash"),        # ...but a real tool is running NOW
        ]))

    def test_closed_block_with_multiline_param_is_a_stall(self):
        txt = ('<invoke name="Bash"><parameter name="command">echo a\necho b'
               '</parameter></invoke>')
        self.assertTrue(self._stall([_assistant(txt)]))

    def test_stall_buried_under_many_system_entries(self):
        # trailing hook/system bursts must not push the broken turn out of view
        self.assertTrue(self._stall(
            [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT)] + [_system()] * 100))


class EntryHasToolUse(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(wd._entry_has_tool_use(_assistant_tooluse()))

    def test_negative_text_only(self):
        self.assertFalse(wd._entry_has_tool_use(_assistant("just text")))

    def test_negative_garbage(self):
        self.assertFalse(wd._entry_has_tool_use({"type": "assistant"}))


class RunOnceTextcallWiring(unittest.TestCase):
    """run_once must emit a textcall-nudge for a stalled pane, and NOT for a pane
    whose last turn merely discusses <invoke>."""

    CWD = "/home/newlevel/devel/camera-box"
    PANE = "%9"

    # A pane IDLE at a free `❯` prompt (turn ended) — safe to type. `.strip()` of the
    # real prompt (`❯`+NBSP) is a bare `❯`. No `_WAITING_RX` footer → not waiting-on-user.
    IDLE_PROMPT_CAP = ("● Predošlá práca hotová.\n❯ \n"
                       "  ctx ███░  caveman:lite\n"
                       "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")
    # A pane actively RUNNING a foreground agent — spinner, "esc to interrupt", NO free
    # `❯` prompt. A keystroke here would INTERRUPT the live work.
    BUSY_CAP = ("● Validate issue #233\n  ⎿ running…\n"
                "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n")

    def _run_with_transcript(self, entries, idle_seconds=600, capture=None):
        """Build a temp projects dir + state, a fake tmux `run`, call run_once, return logs."""
        cap = self.IDLE_PROMPT_CAP if capture is None else capture
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / "90bc51f3.jsonl"
        _write_jsonl(tpath, entries)
        now = time.time()
        os.utime(tpath, (now - idle_seconds, now - idle_seconds))
        state_path = Path(tmp.name) / "state.json"
        sent = []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in argv[0:2] or "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return cap
            if "send-keys" in j:
                sent.append(argv)
                return ""
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))
        return logs, sent

    def test_stalled_pane_gets_textcall_nudge(self):
        logs, sent = self._run_with_transcript([
            _assistant("Earlier turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(),
        ])
        self.assertTrue(any(ln.startswith("textcall-nudge#1") for ln in logs),
                        "expected a textcall-nudge log line, got: %r" % logs)
        # the nudge keystroke must actually have been sent (not dry_run)
        self.assertTrue(any("send-keys" in " ".join(a) and wd.TEXTCALL_NUDGE_TEXT in a
                            for a in sent),
                        "expected TEXTCALL_NUDGE_TEXT typed into the pane")

    def test_stalled_pane_but_busy_is_not_nudged(self):
        # THE #233 INCIDENT: the transcript looks stalled (a text-toolcall + 10min idle),
        # but the pane is actively running a FOREGROUND agent (spinner, no free `❯`). A
        # keystroke would INTERRUPT it → must skip busy-pane, send NOTHING.
        logs, sent = self._run_with_transcript([
            _assistant("Earlier turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(),
        ], capture=self.BUSY_CAP)
        self.assertTrue(any("skip busy-pane (textcall-stall)" in ln for ln in logs),
                        "busy pane must be skipped, got: %r" % logs)
        self.assertEqual(sent, [], "MUST NOT type into a pane running a foreground agent")

    def test_meta_discussion_pane_is_not_nudged(self):
        txt = ("The bug: model emits `<invoke name=\"Read\">` as text. "
               + ("More prose. " * 30) + "\n\n✅ DONE: vysvetlené.")
        logs, sent = self._run_with_transcript([_assistant(txt)])
        self.assertFalse(any("textcall" in ln for ln in logs),
                         "meta-discussion must not trigger a textcall stall: %r" % logs)
        self.assertEqual(sent, [], "no keystroke should be sent for a healthy pane")

    def test_short_report_mentioning_invoke_is_not_nudged(self):
        # the exact false-positive the review found: a short healthy turn that mentions
        # <invoke> near its end (well within 400 chars) then ends on a status marker —
        # the old heuristic injected a keystroke here; the precise check must not.
        txt = ("Fixed. Model emitted `<invoke name=\"Read\">` as text.\n\n"
               "✅ DONE: opravené.")
        logs, sent = self._run_with_transcript([_assistant(txt)])
        self.assertFalse(any("textcall" in ln for ln in logs),
                         "a short report mentioning <invoke> must not fire: %r" % logs)
        self.assertEqual(sent, [], "no keystroke into a healthy pane")

    def test_fresh_stall_within_grace_is_not_nudged_yet(self):
        # idle below STALL_TEXTCALL_SECONDS → hold (guard against a mid-write turn)
        logs, sent = self._run_with_transcript([_assistant(CAMERA_BOX_TEXT)],
                                               idle_seconds=30)
        self.assertFalse(any("textcall-nudge" in ln for ln in logs),
                         "a sub-grace stall must not nudge yet: %r" % logs)


class PaneWaitingOnUser(unittest.TestCase):
    """The false-"čaká na teba" fix: a dialog footer matching the loose regex ANYWHERE
    is not enough. A LIVE blocking dialog occupies the input area (no free `❯` prompt
    at the bottom); a CLOSED dialog's footer lingering above a free `❯` prompt is NOT
    a wait."""
    LIVE = ("● Claude asked:\n  · Zavrieť #137 alebo overiť naživo?\n"
            "     1. Zavrieť\n     2. Overiť\n"
            "  Tab/Arrow keys to navigate · Enter to select\n")
    CLOSED_FOOTER_ABOVE_PROMPT = (
        "● Claude asked:\n     Enter to select\n"
        "● Odpovedané — pokračujem.\n"
        "❯ \n"
        "  ctx ███░░  caveman:lite\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")

    def test_live_dialog_is_waiting(self):
        self.assertTrue(wd.pane_waiting_on_user(self.LIVE))

    def test_lingering_footer_above_free_prompt_is_not_waiting(self):
        self.assertFalse(wd.pane_waiting_on_user(self.CLOSED_FOOTER_ABOVE_PROMPT))

    def test_no_footer_or_empty_is_not_waiting(self):
        self.assertFalse(wd.pane_waiting_on_user("built ok\n❯ "))
        self.assertFalse(wd.pane_waiting_on_user(""))

    def test_typed_at_prompt_is_not_waiting(self):
        self.assertFalse(wd.pane_waiting_on_user(
            "  Enter to select\n❯ nejaký text\n  ctx caveman:lite"))

    def test_menu_pointer_option_is_still_waiting(self):
        # REGRESSION (review finding #1): CC renders the highlighted menu option with a
        # leading `❯` (e.g. `❯ 1. Yes` in a tool-permission / plan-approval dialog). That
        # is an OPEN menu — the session IS blocked. The free-prompt guard must NOT treat
        # `❯ 1. Yes` as an idle prompt (which would suppress the "čaká na teba" ping).
        self.assertTrue(wd.pane_waiting_on_user(
            "  Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
            "  Enter to select · Tab/Arrow keys to navigate\n"))
        # and with the pointer as the very last line (menu at the bottom)
        self.assertTrue(wd.pane_waiting_on_user(
            "  Do you want to proceed?\n  Enter to select\n  1. Yes\n❯ 2. No\n"))

    def test_dialog_with_stray_typed_prompt_above_footer_is_still_waiting(self):
        # #2 LOW (same root cause as the #1 typing-gate hole): a LIVE dialog whose
        # transcript ABOVE the footer shows an example command line `❯ git status` must
        # still register as waiting. The old multi-line window matched that stray
        # `❯ <text>` and suppressed the "čaká na teba" ping. The boundary line is the
        # footer (not a `❯`), so the free-prompt check is False → waiting True.
        self.assertTrue(wd.pane_waiting_on_user(
            "  Do you want to proceed?\n❯ git status\n"
            "  Enter to select · Tab/Arrow keys to navigate\n"))


class PaneAtIdlePrompt(unittest.TestCase):
    """Never type a stuck-check nudge into a pane that is NOT at a free `❯` idle prompt.
    The #233 incident: a FOREGROUND agent blocked the parent (transcript looked 30-min
    idle) while the pane ran the agent — the nudge INTERRUPTED it."""

    # real prompt renders as `❯` + U+00A0 + space → strips to a bare `❯`
    IDLE = "● Hotovo.\n❯  \n  ctx ███  caveman:lite\n  ⏵⏵ bypass permissions on\n"
    IDLE_TYPED = "● Hotovo.\n❯ nejaký rozpísaný text\n  ctx ███  caveman:lite\n"
    # THE #1 FINDING: a `⏳ WORKING` session IDLE at `❯` with TWO background validators —
    # the agent strip (● main + 2× ◯ rows) + statusline + borders push `❯` to position 7
    # from the bottom. A fixed 6-line tail false-skips; chrome-stripping must still find it.
    IDLE_TALL_STRIP = (
        "⏳ WORKING: validujem #459 + #461\n"
        "✻ Waiting for 2 background agents to finish\n──────────\n❯  \n──────────\n"
        "  ctx ██  5h 27%  Fable 51%  caveman:lite\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
        "● main\n◯ ticket-validator  Checking fps pins in drift-guard.sh\n"
        "◯ ticket-validator  Checking is_zero in recording-verdict.rs\n")
    BUSY = ("● Validate issue #233\n  ⎿ running…\n"
            "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
            "  ctx ██  5h 20%  caveman:lite\n  ⏵⏵ bypass permissions on\n")
    MENU = ("  Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
            "  Enter to select · Tab/Arrow keys to navigate\n")

    def test_bare_idle_prompt_is_typeable(self):
        self.assertTrue(wd.pane_at_idle_prompt(self.IDLE))

    def test_tall_agent_strip_still_finds_prompt(self):
        # #1 regression: `❯` past a 6-line tail (2 bg workers) must still be found.
        self.assertTrue(wd.pane_at_idle_prompt(self.IDLE_TALL_STRIP))

    def test_user_typed_text_is_not_typeable(self):
        # #4: a prompt with user-typed unsubmitted text → the user is PRESENT and
        # interacting; a nudge keystroke would corrupt their input → NOT typeable.
        self.assertFalse(wd.pane_at_idle_prompt(self.IDLE_TYPED))

    # THE #1 HIGH FINDING (adversarial review, 2026-07-03): a running foreground turn
    # (spinner is the boundary line) whose STREAMED TRANSCRIPT tail just above the spinner
    # contains a lone `❯` line — realistic: shell-prompt help, tool output like
    # `printf '❯'`, or a session editing THIS very pane-detection code. A window that
    # scanned lines ABOVE the boundary matched that stray `❯`, called the BUSY pane idle,
    # and would have typed a nudge INTO the running turn (the exact #233 scar). The `❯`
    # must be the boundary line ITSELF (first non-chrome up from the bottom = the spinner
    # here), never the transcript above it.
    BUSY_STRAY_PROMPT = (
        "The starship prompt symbol is:\n❯\n"
        "✻ Herding… (esc to interrupt)\n"
        "  ctx ███  caveman:lite\n  ⏵⏵ bypass permissions on\n")
    BUSY_STRAY_PROMPT_STRIP = (
        "● Bash(printf '%s' '❯')\n❯\n"
        "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
        "  ctx ██  5h 20%  caveman:lite\n  ⏵⏵ bypass permissions on\n")

    def test_busy_foreground_agent_is_not_typeable(self):
        # THE FIX: a running foreground agent (no free `❯`) must NOT be typed into.
        self.assertFalse(wd.pane_at_idle_prompt(self.BUSY))

    def test_busy_with_stray_prompt_in_transcript_is_not_typeable(self):
        # #1 HIGH: a BUSY pane whose transcript tail ends on a lone `❯` above the spinner
        # must still be classified BUSY — the boundary line is the spinner, not the stray.
        self.assertFalse(wd.pane_at_idle_prompt(self.BUSY_STRAY_PROMPT))
        self.assertFalse(wd.pane_at_idle_prompt(self.BUSY_STRAY_PROMPT_STRIP))

    def test_open_menu_is_not_a_free_prompt(self):
        # a `❯ 1.` pointer is an open dialog, not a free prompt → not typeable
        self.assertFalse(wd.pane_at_idle_prompt(self.MENU))

    def test_empty_capture_is_not_typeable(self):
        self.assertFalse(wd.pane_at_idle_prompt(""))
        self.assertFalse(wd.pane_at_idle_prompt(None))


class StructuralInputLineDetection(unittest.TestCase):
    """Issue #46: the input box is found STRUCTURALLY (the last pair of
    separator lines, with the box content between them) rather than by
    enumerating known chrome glyphs below it. Enumeration is a dead end — any
    UNRECOGNIZED line rendered below the box (a brand-new Claude Code UI
    element) stops the old bottom-up scan early and the pane vanishes from
    every keystroke job. Live incident (2026-07-25, dev2 marek-1:5.0, project
    upomienky-prehlad): a `⧉  upomienky-prehlad` row rendered below the box
    and job 12 logged `skip busy` forever, even though the session merely
    held a draft. The glyph-based peel is kept as a FALLBACK for captures
    that render the box borderless (most of this file's older fixtures)."""

    # The live incident, reproduced: a real separator-bounded box holding a
    # DRAFT, with the never-seen-before `⧉  <project>` chrome row below it.
    UNKNOWN_CHROME_DRAFT_CAP = (
        "● Predošlá práca hotová.\n"
        "──────────\n"
        "❯ rozpisany draft text\n"
        "──────────\n"
        "⧉  upomienky-prehlad\n"
        "  ctx ███░  caveman:lite\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")

    # Same unknown chrome row, but the box is BARE (empty prompt) — must
    # still resolve to a genuinely idle, empty input line.
    UNKNOWN_CHROME_IDLE_CAP = (
        "● Predošlá práca hotová.\n"
        "──────────\n"
        "❯  \n"
        "──────────\n"
        "⧉  upomienky-prehlad\n"
        "  ctx ███░  caveman:lite\n")

    # #36-style: a selected agent-strip row + its selector hint, but with NO
    # separators at all — must still resolve via the glyph-based FALLBACK.
    ISSUE36_STYLE_CAP = (
        "❯ ● main\n"
        "❯ \n"
        "  ↑/↓ to select · Enter to view\n"
        "  ctx ███░  caveman:lite\n")

    # A line that matches NO known chrome shape today, and never will be
    # enumerated — the whole point of structural detection: it must resolve
    # correctly regardless of what Claude Code renders below the box.
    NEVER_SEEN_CHROME_CAP = (
        "● Predošlá práca hotová.\n"
        "──────────\n"
        "❯ \n"
        "──────────\n"
        "▶ brand-new-widget: nikdy predtym nevidena vec\n"
        "  ctx ███░  caveman:lite\n")

    # No separators anywhere — exercises the glyph-based fallback path
    # directly (the pre-#46 behavior, unchanged).
    NO_SEPARATOR_CAP = ("● Hotovo.\n❯ niečo napísané\n"
                        "  ctx ███  caveman:lite\n  ⏵⏵ bypass permissions on\n")

    # The whole capture IS chrome — no boundary line exists at all, under
    # EITHER strategy. Distinct from "busy" (a real incident case: #46 part 1
    # requires the callers to tell these apart).
    ALL_CHROME_NO_BOX_CAP = ("  ctx ███░  caveman:lite\n"
                             "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
                             "● main\n")

    def test_unknown_chrome_below_box_still_finds_the_draft(self):
        self.assertEqual(wd._input_line_text(self.UNKNOWN_CHROME_DRAFT_CAP),
                         "rozpisany draft text")
        self.assertFalse(wd.pane_at_idle_prompt(self.UNKNOWN_CHROME_DRAFT_CAP))

    def test_unknown_chrome_below_box_still_finds_bare_idle_prompt(self):
        self.assertEqual(wd._input_line_text(self.UNKNOWN_CHROME_IDLE_CAP), "")
        self.assertTrue(wd.pane_at_idle_prompt(self.UNKNOWN_CHROME_IDLE_CAP))

    def test_issue36_style_with_no_separators_uses_fallback(self):
        self.assertEqual(wd._input_line_text(self.ISSUE36_STYLE_CAP), "")
        self.assertTrue(wd.pane_at_idle_prompt(self.ISSUE36_STYLE_CAP))

    def test_never_before_seen_chrome_shape_still_resolves(self):
        # THE point of structural detection: a chrome row that matches no
        # enumerated pattern today (and never will be added to one) must
        # still resolve correctly, because it never has to be recognized —
        # it just has to be BELOW the box's own closing separator.
        self.assertEqual(wd._input_line_text(self.NEVER_SEEN_CHROME_CAP), "")
        self.assertTrue(wd.pane_at_idle_prompt(self.NEVER_SEEN_CHROME_CAP))

    def test_no_separators_at_all_exercises_the_fallback(self):
        self.assertEqual(wd._input_line_text(self.NO_SEPARATOR_CAP), "niečo napísané")
        self.assertFalse(wd.pane_at_idle_prompt(self.NO_SEPARATOR_CAP))

    def test_no_boundary_at_all_is_distinct_from_busy(self):
        # Neither strategy finds a boundary line — genuinely different from
        # a busy pane (where a boundary IS found, it's just not `❯`-shaped).
        self.assertIsNone(wd._find_boundary_line(self.ALL_CHROME_NO_BOX_CAP))
        self.assertIsNone(wd._input_line_text(self.ALL_CHROME_NO_BOX_CAP))
        self.assertFalse(wd.pane_at_idle_prompt(self.ALL_CHROME_NO_BOX_CAP))

    def test_classify_boundary_distinguishes_busy_from_no_input_line(self):
        # `_classify_boundary` is what jobs 12/14/15 use to split their
        # "skip busy" logging into the two genuinely different causes.
        self.assertEqual(wd._classify_boundary(self.ALL_CHROME_NO_BOX_CAP),
                         ("no-input-line", None))
        self.assertEqual(wd._classify_boundary(
            "● Baking…\n✳ Baking… (2m 30s · esc to interrupt)\n  ctx ███░\n"),
            ("busy", None))
        self.assertEqual(wd._classify_boundary(self.UNKNOWN_CHROME_DRAFT_CAP),
                         ("input", "rozpisany draft text"))


class QueuedMessagesPlaceholderNotADraft(unittest.TestCase):
    """#65 acceptance: CC's greyed 'Press up to edit queued messages' HINT
    (an otherwise-empty box, recallable via Up-arrow — never text the user
    typed) must be normalized to a bare `❯` boundary, never mistaken for a
    real draft. A synchronous /compact delivery — or any other keystroke
    job — must never treat it as unsafe to type over."""

    QUEUED_PLACEHOLDER_CAP = ("● Predošlá práca hotová.\n"
                              "❯ Press up to edit queued messages\n"
                              "  ctx ███░  caveman:lite\n")
    QUEUED_PLACEHOLDER_STRUCTURAL_CAP = (
        "● Predošlá práca hotová.\n"
        "──────────\n"
        "❯ Press up to edit queued messages\n"
        "──────────\n"
        "  ctx ███░  caveman:lite\n")
    # case/whitespace variance is tolerated — the boundary line is already
    # fully stripped by `_find_boundary_line_raw` before normalization.
    QUEUED_PLACEHOLDER_CASE_CAP = "● hotovo\n❯ PRESS UP TO EDIT QUEUED MESSAGES\n  ctx ░\n"

    def test_find_boundary_line_normalizes_to_bare_prompt(self):
        self.assertEqual(wd._find_boundary_line(self.QUEUED_PLACEHOLDER_CAP), "❯")
        self.assertEqual(
            wd._find_boundary_line(self.QUEUED_PLACEHOLDER_STRUCTURAL_CAP), "❯")
        self.assertEqual(
            wd._find_boundary_line(self.QUEUED_PLACEHOLDER_CASE_CAP), "❯")

    def test_input_line_text_is_empty_not_the_placeholder(self):
        self.assertEqual(wd._input_line_text(self.QUEUED_PLACEHOLDER_CAP), "")

    def test_classify_boundary_reports_empty_draft(self):
        self.assertEqual(wd._classify_boundary(self.QUEUED_PLACEHOLDER_CAP),
                         ("input", ""))

    def test_pane_at_idle_prompt_is_true_safe_to_type(self):
        self.assertTrue(wd.pane_at_idle_prompt(self.QUEUED_PLACEHOLDER_CAP))

    def test_a_genuine_draft_mentioning_similar_words_is_still_a_draft(self):
        # never over-match — only the EXACT placeholder text normalizes.
        cap = "● hotovo\n❯ press up later to see queued messages maybe\n  ctx ░\n"
        self.assertEqual(wd._input_line_text(cap),
                         "press up later to see queued messages maybe")
        self.assertFalse(wd.pane_at_idle_prompt(cap))


class PaneQuestionExcerpt(unittest.TestCase):
    """The job-2 "čaká na teba" ping must CARRY the question + options extracted from
    the pane — the user's explicit complaint (2026-07-04) was pings saying only that
    "a question is waiting" with no question text in them."""

    DIALOG = (
        "starý transcript vyššie — nesúvisiaci text\n"
        "╭──────────────────────────────────────────────╮\n"
        "│ Ktorý prístup pre reset EQ?                  │\n"
        "│                                              │\n"
        "│ ❯ 1. Reset na 0 dB (odporúčam)               │\n"
        "│   2. Posledný preset                         │\n"
        "╰──────────────────────────────────────────────╯\n"
        "  Tab/Arrow keys to navigate · Enter to select\n")

    def test_extracts_question_and_options(self):
        out = wd.pane_question_excerpt(self.DIALOG)
        self.assertIn("Ktorý prístup pre reset EQ?", out)
        self.assertIn("1. Reset na 0 dB (odporúčam)", out)
        self.assertIn("2. Posledný preset", out)

    def test_border_bounds_question_never_leaks_transcript(self):
        # The question walk stops at the dialog's border rule — transcript prose
        # ABOVE the box must never end up in the phone ping.
        self.assertNotIn("nesúvisiaci", wd.pane_question_excerpt(self.DIALOG))

    def test_borderless_permission_dialog(self):
        out = wd.pane_question_excerpt(
            "  Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
            "  Enter to select · Tab/Arrow keys to navigate\n")
        self.assertIn("Do you want to proceed?", out)
        self.assertIn("1. Yes", out)

    def test_borderless_bullet_header_bounds_question(self):
        # AskUserQuestion commonly renders BORDERLESS with `● Claude asked:` as
        # its top. The bullet must act as the question boundary — transcript
        # prose above it must never leak into the ping (review finding).
        out = wd.pane_question_excerpt(
            "nesúvisiaca próza vyššie v transkripte\n"
            "● Claude asked:\n  · Zavrieť #137 alebo overiť?\n"
            "     1. Zavrieť\n     2. Overiť\n"
            "  Tab/Arrow keys to navigate · Enter to select\n")
        self.assertIn("Zavrieť #137 alebo overiť?", out)
        self.assertIn("1. Zavrieť", out)
        self.assertNotIn("nesúvisiaca", out)

    def test_no_dialog_returns_empty(self):
        # No numbered options visible → "" (caller falls back to the generic text).
        self.assertEqual(wd.pane_question_excerpt("built ok\n❯ \n"), "")
        self.assertEqual(wd.pane_question_excerpt(""), "")

    def test_truncated_to_max_chars(self):
        out = wd.pane_question_excerpt(
            "Otázka?\n❯ 1. " + "x" * 500 + "\n", max_chars=100)
        self.assertLessEqual(len(out), 100)
        self.assertTrue(out.endswith("…"))

    def test_default_cap_fits_a_full_dialog_question(self):
        # The user's complaint (2026-07-04): device questions arrive CUT — the
        # default cap must carry a realistic full dialog (long question + option
        # descriptions, ~600 chars), not chop it at the old 350.
        q = "Ktorý prístup pre migráciu objednávok zvolíme? " * 8   # ~376 chars
        out = wd.pane_question_excerpt(
            q + "\n❯ 1. Skript (odporúčam) — " + "rýchle. " * 20
            + "\n  2. Nechať tak — " + "nuly ostanú. " * 10 + "\n")
        self.assertGreater(len(out), 500,
                           "default cap must fit a full realistic dialog")
        self.assertIn("2. Nechať tak", out)


    # CC 2.1.20x (fullscreen renderer): the dialog interleaves WRAPPED option
    # descriptions and appends UI affordance rows ("4. Type something." +
    # "5. Chat about this" below a border). Anchoring on the LAST numbered row
    # from the bottom picked the affordance — Dávid's phone got a ping whose
    # whole "question" was "5. Chat about this" (gk, 2026-07-09).
    FULLSCREEN_DIALOG = (
        "  Tvoja dochadzka NIE JE Odoo — dva zdroje pravdy.\n"
        "  Jedna vec ale rozhoduje rozsah:\n"
        "────────────────────────────────────────\n"
        " ☐ Rozsah\n"
        "Ktoré časti kiosku majú byť Odoo-native?\n"
        "❯ 1. Dochádzka + žiadosti o voľno (odporúčam)\n"
        "     Príchod/odchod/prestávka do Odoo hr.attendance; dovolenka do\n"
        "     hr.leave. Jeden zdroj pravdy pre mzdy.\n"
        "  2. Len dochádzka (úplne jadro)\n"
        "     Iba príchod/odchod/prestávka. Voľno ostáva na grena.sk.\n"
        "  3. Všetko vrátane zmien/plánovania\n"
        "     Aj shift-planning prerobiť do Odoo (najväčší kus práce).\n"
        "  4. Type something.\n"
        "────────────────────────────────────────\n"
        "  5. Chat about this\n"
        "Enter to select · ↑/↓ to navigate · Esc to cancel\n")

    def test_fullscreen_dialog_carries_question_not_ui_affordances(self):
        out = wd.pane_question_excerpt(self.FULLSCREEN_DIALOG)
        self.assertIn("Ktoré časti kiosku majú byť Odoo-native?", out)
        self.assertIn("1. Dochádzka + žiadosti o voľno (odporúčam)", out)
        self.assertIn("3. Všetko vrátane zmien/plánovania", out)
        self.assertNotIn("Chat about this", out)     # UI affordance, not an option
        self.assertNotIn("Type something", out)      # UI affordance, not an option
        self.assertNotIn("dva zdroje pravdy", out)   # transcript prose above the box



class WaitingPersistenceGate(unittest.TestCase):
    """Job 2 must NOT ping on the FIRST poll that sees a dialog footer (a transient
    bypass-permissions / 60s-auto-continue flash) — only after it PERSISTS to a later
    poll. A flash that is gone by the next poll never pings."""
    CWD = "/home/newlevel/devel/codex-bridge"
    PANE = "%7"
    WAITING = ("● Claude asked:\n  · Zavrieť #137 alebo overiť?\n"
               "     1. Zavrieť\n     2. Overiť\n"
               "  Tab/Arrow keys to navigate · Enter to select\n")

    def _poll(self, tmp, capture, now, sent=None):
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        d = proj / enc
        d.mkdir(parents=True, exist_ok=True)
        tpath = d / "sess.jsonl"
        _write_jsonl(tpath, [_assistant("⏳ WORKING: robím ETL.")])
        os.utime(tpath, (now - 600, now - 600))
        state_path = Path(tmp.name) / "state.json"

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return capture
            return ""

        return wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=(lambda *a, **k: sent.append(a[0]))
                                   if sent is not None else (lambda *a, **k: None),
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))

    def test_first_poll_silent_second_poll_pings(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        now = time.time()
        logs1 = self._poll(tmp, self.WAITING, now)
        self.assertFalse(any("waiting" in ln for ln in logs1),
                         "first sight must NOT ping (persistence gate): %r" % logs1)
        logs2 = self._poll(tmp, self.WAITING, now + 90)
        self.assertTrue(any("waiting" in ln for ln in logs2),
                        "a persisted footer must ping on the 2nd poll: %r" % logs2)

    def test_transient_flash_never_pings(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        now = time.time()
        self._poll(tmp, self.WAITING, now)                     # flash seen once
        logs2 = self._poll(tmp, "postavené ok\n❯ ", now + 90)  # gone by next poll
        self.assertFalse(any("waiting" in ln for ln in logs2),
                         "a transient flash must never ping: %r" % logs2)

    def test_ping_body_carries_the_question(self):
        # The user's complaint (2026-07-04): "čaká na teba" pings that do NOT say
        # WHAT is asked force a trip to the terminal just to read the question.
        # The ping body must carry the pane's question + options.
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        now = time.time()
        sent = []
        self._poll(tmp, self.WAITING, now, sent=sent)          # persistence gate
        self._poll(tmp, self.WAITING, now + 90, sent=sent)     # → pings here
        self.assertEqual(len(sent), 1, "expected exactly one waiting ping: %r" % sent)
        self.assertIn("Zavrieť #137 alebo overiť?", sent[0])
        self.assertIn("1. Zavrieť", sent[0])


# --- job 6: 5-hour SESSION LIMIT — ping once, `continue` only AFTER the reset ------

SESSION_LIMIT_BANNER = (
    "❯ continue\n"
    "  ⎿  You've hit your session limit · resets 6:10pm (Europe/Prague)\n"
    "     /usage-credits to finish what you're working on.\n\n❯ ")


class SessionLimitDetector(unittest.TestCase):
    def test_banner_matches(self):
        self.assertTrue(wd.pane_session_limited(SESSION_LIMIT_BANNER))

    def test_healthy_pane_does_not_match(self):
        self.assertFalse(wd.pane_session_limited("built ok\n❯ "))
        self.assertFalse(wd.pane_session_limited(""))

    def test_parse_reset_epoch_today(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()
        epoch = wd.parse_reset_epoch(SESSION_LIMIT_BANNER, now)
        self.assertIsNotNone(epoch)
        self.assertGreater(epoch, now)
        got = datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(got, "2026-07-01 18:10")

    def test_recently_passed_reset_resumes_now_not_tomorrow(self):
        # A reset only slightly in the past means it JUST happened → resume now
        # (epoch <= now), NOT wait a whole day. This is what makes the after-reset
        # `continue` fire promptly when the watchdog sees the banner just past reset.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 19, 0, tzinfo=tz).timestamp()   # 50 min past 18:10
        epoch = wd.parse_reset_epoch(SESSION_LIMIT_BANNER, now)
        self.assertLessEqual(epoch, now)
        got = datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(got, "2026-07-01 18:10")

    def test_late_night_am_reset_rolls_to_next_day(self):
        # A late-night "resets 12:10am" seen at 23:50 is > 6h in the past as 'today'
        # → it is really tomorrow's early-morning reset → roll forward.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 23, 50, tzinfo=tz).timestamp()
        epoch = wd.parse_reset_epoch("resets 12:10am (Europe/Prague)", now)
        got = datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(got, "2026-07-02 00:10")

    def test_parse_24h_and_am_pm(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 8, 0, tzinfo=tz).timestamp()
        for banner, expect in (("resets 18:10 (Europe/Prague)", "18:10"),
                               ("resets 11am (Europe/Prague)", "11:00"),
                               ("resets 12pm (Europe/Prague)", "12:00")):
            epoch = wd.parse_reset_epoch(banner, now)
            got = datetime.fromtimestamp(epoch, tz).strftime("%H:%M")
            self.assertEqual(got, expect, "banner %r" % banner)

    def test_parse_missing_time_returns_none(self):
        self.assertIsNone(wd.parse_reset_epoch("You've hit your session limit", 0))

    # --- FIX A: gk incident 2026-07-24 — a UTC-hosted box's banner reads
    # "resets 4:40pm (UTC)". The old `_RESET_TZ_RX` only matched "Area/City"
    # (e.g. "Europe/Prague"), so a bare "(UTC)" fell through to the
    # Europe/Bratislava default and computed a reset epoch 2h EARLY — the
    # Discord ping showed a nonsense past reset time ("16:40" while the real
    # reset was "18:40" local).
    def test_utc_banner_parses_in_utc(self):
        from datetime import datetime, timezone
        now = datetime(2026, 7, 24, 15, 20, tzinfo=timezone.utc).timestamp()
        epoch = wd.parse_reset_epoch(
            "You've hit your session limit · resets 4:40pm (UTC)\n", now)
        self.assertIsNotNone(epoch)
        got = datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M")
        self.assertEqual(got, "16:40")

    def test_tz_read_near_the_clock_not_anywhere(self):
        # An earlier, unrelated parenthetical ("(debug)") must NOT hijack the
        # tz lookup — only the ~80 chars starting at the TIME match are
        # searched, so the real "(UTC)" right after the clock still wins.
        from datetime import datetime, timezone
        now = datetime(2026, 7, 24, 15, 20, tzinfo=timezone.utc).timestamp()
        cap = ("● verbose mode (debug) enabled\n"
               "You've hit your session limit · resets 4:40pm (UTC)\n")
        epoch = wd.parse_reset_epoch(cap, now)
        self.assertIsNotNone(epoch)
        got = datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M")
        self.assertEqual(got, "16:40")

    # --- FIX B: a dead BACKGROUND WORKER can leave a `⎿ You've hit your
    # session limit …` ECHO line sitting HIGH in the transcript output, with
    # many later "● pokracujem v praci"-style lines scrolling underneath it
    # for hours — a whole-capture search kept the episode "limited" long
    # after a real resume already happened (gk incident 2026-07-24).
    # Detection is now scoped to the last 10 lines above the input box.
    def test_stale_echo_lines_high_in_viewport_do_not_detect(self):
        stale = (
            '● Agent "x" failed: You\'ve hit your session limit · '
            'resets 4:40pm (UTC)\n'
            "  ⎿  You've hit your session limit · resets 4:40pm (UTC)\n"
            "     /usage-credits to finish what you're working on.\n"
            + ("● pokracujem v praci\n" * 12)
            + "❯ \n  ctx ██  caveman\n")
        self.assertFalse(wd.pane_session_limited(stale))
        # the real, bottom-scoped banner must still detect.
        self.assertTrue(wd.pane_session_limited(SESSION_LIMIT_BANNER))


class SessionLimitWiring(unittest.TestCase):
    """run_once job 6: ping once on the banner, NO `continue` before the reset,
    exactly ONE `continue` after it."""

    CWD = "/home/newlevel/devel/odoo-erp"
    PANE = "%7"
    SID = "s1t2u3v4"

    def _harness(self, now, seed_state=None, capture=SESSION_LIMIT_BANNER):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant("pracujem…")])       # healthy transcript, no api-error
        os.utime(tpath, (now - 60, now - 60))
        state_path = Path(tmp.name) / "state.json"
        if seed_state is not None:
            state_path.write_text(json.dumps(seed_state))
        sent, keys = [], []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return capture
            if "send-keys" in j:
                keys.append(argv)
                return ""
            return ""

        def fake_send(body, **k):
            sent.append(body)

        logs = wd.run_once(now=now, dry_run=False, run=fake_run, send_fn=fake_send,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))
        return logs, sent, keys, state_path

    def test_pings_once_and_no_continue_before_reset(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()   # before 18:10 reset
        logs, sent, keys, _ = self._harness(now)
        self.assertTrue(any(ln.startswith("session-limit") and "ping" in ln for ln in logs),
                        "expected a session-limit ping log, got: %r" % logs)
        self.assertTrue(any("5-hodinový limit" in b for b in sent),
                        "expected the 5h-limit Discord ping: %r" % sent)
        self.assertEqual(keys, [], "NO keystroke may be sent before the reset")

    def test_continue_after_reset(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()  # past the 18:10 reset
        # already pinged in a prior poll (reset now in the past) → this poll resumes.
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": False,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed)
        self.assertTrue(any("reset passed" in ln for ln in logs),
                        "expected a reset-passed resume log: %r" % logs)
        self.assertTrue(any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys),
                        "expected exactly one `continue` keystroke after reset: %r" % keys)
        self.assertTrue(any("resetol" in b for b in sent),
                        "expected the resume Discord ping: %r" % sent)

    # (FIX C) A single one-shot `continue` deadlocked forever when the first
    # post-reset poll landed inside the SESSLIMIT_RETRY_S window of a prior
    # attempt — job 6 now retries, bounded, so a recent attempt must WAIT
    # rather than double-fire.
    def test_recent_attempt_waits_out_the_retry_interval(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": True,
            "attempts": 1, "last_try": now - 60,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed)
        self.assertEqual(keys, [], "must wait out the retry interval: %r" % keys)

    def test_bounced_attempt_retries_after_interval(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": True,
            "attempts": 1, "last_try": now - 400,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed,
                                            capture=SESSION_LIMIT_BANNER)
        self.assertTrue(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys),
            "expected a SECOND `continue` retry keystroke: %r" % keys)

    def test_gives_up_after_max_tries_with_one_ping(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": True,
            "attempts": 4, "last_try": now - 400,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed)
        self.assertEqual(keys, [], "must not keep retrying past max tries: %r" % keys)
        self.assertTrue(any("ručne" in b for b in sent),
                        "expected the one give-up ping: %r" % sent)

        # A second poll, already given up → no second ping.
        seed2 = {"sesslimit:" + self.SID: dict(
            seed["sesslimit:" + self.SID], gave_up=True)}
        logs2, sent2, keys2, _ = self._harness(now, seed_state=seed2)
        self.assertEqual(keys2, [])
        self.assertEqual(sent2, [], "must not re-ping once already given up: %r" % sent2)

    # a session-limit banner still on screen, but the user manually resumed and the pane
    # is now running a FOREGROUND agent (spinner, no bare `❯`). Typing `continue` would
    # interrupt it → job 6 must skip busy-pane WITHOUT setting `continued` (finding #2).
    LIMITED_BUT_BUSY = (
        "  ⎿  You've hit your session limit · resets 6:10pm (Europe/Prague)\n"
        "     /usage-credits to finish what you're working on.\n"
        "● Validate issue #99\n✳ Baking… (1m 12s · esc to interrupt)\n"
        "  ctx ██  caveman:lite\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")

    def test_no_continue_into_busy_pane_after_reset(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": False,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, sp = self._harness(now, seed_state=seed,
                                             capture=self.LIMITED_BUT_BUSY)
        self.assertEqual(keys, [], "MUST NOT type `continue` into a busy pane")
        self.assertTrue(any("skip busy-pane (session-limit resume)" in ln for ln in logs),
                        "expected busy-pane skip, got: %r" % logs)
        # continued must remain False so a later poll (at a genuine idle prompt) can resume
        st = json.loads(Path(sp).read_text())
        self.assertFalse(st["sesslimit:" + self.SID]["continued"])

    # (FIX C) The gk incident: the user's OWN hand-typed draft sat in the input
    # box of a limit-parked session, unsubmitted — it could go nowhere while
    # limited, and a `pane_at_idle_prompt` bare-❯ gate never matched a box
    # holding text, so the resume deadlocked. A draft must be TRACKED first
    # (never typed over blind), then SUBMITTED once it is byte-stable across
    # at least one more sweep.
    DRAFT_CAPTURE = (
        "❯ continue\n"
        "  ⎿  You've hit your session limit · resets 6:10pm (Europe/Prague)\n"
        "     /usage-credits to finish what you're working on.\n\n"
        "❯ ako to ide, stihne sa deploy?")
    DRAFT_TEXT = "ako to ide, stihne sa deploy?"

    def test_draft_first_sight_tracked_not_typed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": False,
            "attempts": 0, "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, sp = self._harness(now, seed_state=seed,
                                             capture=self.DRAFT_CAPTURE)
        self.assertEqual(keys, [], "must not type over a freshly-seen draft: %r" % keys)
        st = json.loads(Path(sp).read_text())
        self.assertIn("draft_hash", st["sesslimit:" + self.SID])

    def test_stable_draft_submitted_with_escape_enter(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        draft_hash = hashlib.sha1(self.DRAFT_TEXT.encode()).hexdigest()[:12]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": False,
            "attempts": 0, "draft_hash": draft_hash,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed,
                                            capture=self.DRAFT_CAPTURE)
        flat = [a for call in keys for a in call]
        self.assertIn("Escape", flat)
        self.assertIn("Enter", flat)
        self.assertNotIn("-l", flat, "must never type text over the user's draft: %r" % keys)


class RunOnceLoopIsolation(unittest.TestCase):
    """(issue #3) One pane raising inside the per-transcript loop body — a
    corrupted transcript, an unexpected tmux-shim output shape, a raise inside a
    job handler — must NOT abort the whole poll and blank state for every OTHER
    healthy pane this cycle. The bad pane is skipped with a clear log line; the
    healthy pane's work (and its state) still lands."""

    BAD_CWD = "/home/newlevel/devel/bad-project"
    GOOD_CWD = "/home/newlevel/devel/camera-box"
    BAD_PANE = "%1"
    GOOD_PANE = "%2"

    def test_one_bad_pane_does_not_abort_the_whole_poll(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"

        bad_enc = wd.encode_project_dir(self.BAD_CWD)
        (proj / bad_enc).mkdir(parents=True)
        bad_tpath = proj / bad_enc / "bad1111a.jsonl"
        _write_jsonl(bad_tpath, [_assistant("hello")])

        good_enc = wd.encode_project_dir(self.GOOD_CWD)
        (proj / good_enc).mkdir(parents=True)
        good_tpath = proj / good_enc / "90bc51f3.jsonl"
        _write_jsonl(good_tpath, [
            _assistant("Earlier turn."),
            _assistant(CAMERA_BOX_TEXT),
            _system(),
        ])

        now = time.time()
        idle_seconds = 600
        os.utime(bad_tpath, (now - idle_seconds, now - idle_seconds))
        os.utime(good_tpath, (now - idle_seconds, now - idle_seconds))

        state_path = Path(tmp.name) / "state.json"
        idle_cap = ("● Predošlá práca hotová.\n❯ \n"
                    "  ctx ███░  caveman:lite\n"
                    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return ("%s\tclaude\t%s\n%s\tclaude\t%s\n"
                        % (self.BAD_PANE, self.BAD_CWD, self.GOOD_PANE, self.GOOD_CWD))
            if "capture-pane" in j:
                # simulate a corrupt/unexpected tmux-shim response for the bad pane
                # only — this is what raises inside the loop body for that pane.
                if self.BAD_PANE in argv:
                    raise RuntimeError("simulated corrupt tmux-shim output")
                return idle_cap
            if "display-message" in argv[0:2] or "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "send-keys" in j:
                return ""
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))

        # the healthy pane's work still happened despite the bad pane raising
        self.assertTrue(any(ln.startswith("textcall-nudge#1") for ln in logs),
                        "expected the healthy pane to still be processed, got: %r" % logs)
        # the bad pane was skipped with a clear log line, not silently dropped
        self.assertTrue(any("skip error" in ln and "bad1111a" in ln for ln in logs),
                        "expected a 'skip error' log line naming the bad transcript, "
                        "got: %r" % logs)
        # state was actually persisted (save_state ran despite the raise)
        self.assertTrue(state_path.exists(), "expected state to be saved despite the raise")
        saved = json.loads(state_path.read_text())
        self.assertTrue(any(k.startswith("textcall:") for k in saved.keys()),
                        "expected the healthy pane's textcall state to be saved, "
                        "got: %r" % saved)


def _assistant_apierror(text="API Error: 529 Overloaded"):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class RunOncePreservesStateOnError(unittest.TestCase):
    """(adversarial-review finding) A TRANSIENT per-pane exception (a
    corrupted capture, an unexpected tmux-shim shape) must NOT be conflated
    with "the session recovered" — job 1's own bare-key state entry (its
    nudge/escalation history) must survive a poll where THIS session's
    processing raised, so a LATER successful poll continues the SAME
    episode instead of silently resetting to nudge#1 (and re-pinging /
    re-counting from scratch)."""

    CWD = "/home/newlevel/devel/some-project"
    PANE = "%9"
    SID = "sess-transient"

    def test_transient_capture_error_preserves_job1_state(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant_apierror()])
        now = time.time()
        idle_seconds = 600
        os.utime(tpath, (now - idle_seconds, now - idle_seconds))

        err_hash = wd._hash("API Error: 529 Overloaded")
        state_path = Path(tmp.name) / "state.json"
        seeded = {self.SID: {"hash": err_hash, "first_seen": int(now - idle_seconds),
                             "nudges": [int(now - 400)], "escalated": False}}
        wd.save_state(state_path, seeded)

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "capture-pane" in j:
                # simulate a transient tmux-shim / capture failure for THIS pane
                raise RuntimeError("simulated transient capture failure")
            if "display-message" in argv[0:2] or "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "send-keys" in j:
                return ""
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))

        self.assertTrue(any("skip error" in ln and self.SID in ln for ln in logs),
                        "expected a 'skip error' log line naming the session, "
                        "got: %r" % logs)
        saved = json.loads(state_path.read_text())
        self.assertIn(self.SID, saved,
                      "a TRANSIENT per-pane error must not clear an existing "
                      "job-1 episode — saved keys: %r" % list(saved.keys()))
        self.assertEqual(len(saved[self.SID].get("nudges", [])), 1,
                         "nudge history must survive a transient per-pane error, "
                         "got: %r" % saved.get(self.SID))

    def test_skip_error_log_includes_exception_repr(self):
        # the bare "skip error <sid>" log line carried NO exception detail
        # at all — impossible to diagnose a recurring transient failure
        # from the log alone.
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant("hello")])
        now = time.time()
        os.utime(tpath, (now - 60, now - 60))
        state_path = Path(tmp.name) / "state.json"

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "capture-pane" in j:
                raise RuntimeError("boom-detail-12345")
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"))
        self.assertTrue(any("boom-detail-12345" in ln for ln in logs),
                        "expected the exception detail in the skip-error log, "
                        "got: %r" % logs)


class RunOnceSubagentVisibility(unittest.TestCase):
    """(issue #6) run_once must apply job 1's api-error detector AND job 4a's
    text-toolcall-stall detector to the newest subagents/*.jsonl too, not just the
    SUPERVISOR transcript — so a dying BACKGROUND WORKER (e.g. an autopilot-worker)
    is caught fast (idle pane → a targeted nudge naming the worker; busy pane →
    ping-only, never a keystroke) instead of waiting up to ~30 min for job 4's
    indirect subagent_active() mtime path."""

    CWD = "/home/newlevel/devel/some-project"
    PANE = "%3"
    SID = "sess-abc"
    WORKER = "worker-1"

    IDLE_CAP = ("● Predošlá práca hotová.\n❯ \n"
               "  ctx ███░  caveman:lite\n"
               "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n")
    BUSY_CAP = ("● Validate issue #233\n  ⎿ running…\n"
               "✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n")

    def _build(self, tmp, sup_entries, sub_entries, sup_age, sub_age):
        proj = Path(tmp) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        now = time.time()
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, sup_entries)
        os.utime(tpath, (now - sup_age, now - sup_age))
        subdir = proj / enc / self.SID / "subagents"
        subdir.mkdir(parents=True)
        spath = subdir / (self.WORKER + ".jsonl")
        _write_jsonl(spath, sub_entries)
        os.utime(spath, (now - sub_age, now - sub_age))
        return proj, now

    def _run(self, proj, now, state_path, capture):
        sent, pings = [], []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "capture-pane" in j:
                return capture
            if "display-message" in argv[0:2] or "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "send-keys" in j:
                sent.append(argv)
                return ""
            return ""

        def fake_send(body, **k):
            pings.append((body, k))

        logs = wd.run_once(now=now, dry_run=False, run=fake_run, send_fn=fake_send,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(proj).parent / "pending-"))
        return logs, sent, pings

    # --- (1b) subagent api-error ------------------------------------------------

    def test_subagent_apierror_undetected_by_default_detectors(self):
        # sanity: the SUPERVISOR-level detectors alone see nothing wrong — this is
        # exactly the blind spot issue #6 describes (proves the scenario is real).
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.")], [_assistant_apierror()],
            sup_age=10, sub_age=400)
        sup_tpath = proj / wd.encode_project_dir(self.CWD) / (self.SID + ".jsonl")
        self.assertEqual(wd.transcript_last_error(sup_tpath), "",
                         "supervisor transcript itself has no error (as designed)")

    def test_subagent_apierror_nudges_idle_pane(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            # supervisor must itself still be `⏳ WORKING` — the marker gate
            # added by the adversarial-review fix requires it (a historical
            # worker file must not nudge into an already-DONE session).
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=400)          # sub_age > GRACE_SECONDS (300)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertTrue(any(ln.startswith("subagent-apierr-nudge#1") for ln in logs),
                        "expected a subagent-apierr nudge log line, got: %r" % logs)
        nudges = [a for a in sent if "-l" in a
                 and any("background worker" in x and self.WORKER in x for x in a)]
        self.assertTrue(nudges, "expected a targeted nudge naming the worker, "
                                "sent=%r" % sent)
        self.assertTrue(any("api-error" in x for a in nudges for x in a))

    def test_subagent_apierror_busy_pane_pings_only(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=400)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.BUSY_CAP)
        self.assertEqual(sent, [], "MUST NOT type into a busy pane")
        self.assertTrue(any("subagent-apierr-busy" in ln for ln in logs),
                        "expected a busy-pane ping-only log line, got: %r" % logs)
        self.assertTrue(pings, "expected a Discord ping instead of a keystroke")

    def test_subagent_apierror_within_grace_does_not_nudge_yet(self):
        # a fresh subagent error (younger than GRACE_SECONDS) may still recover on
        # its own — mirrors job 1's own grace before its first supervisor nudge.
        # Supervisor is `⏳ WORKING` here too, so this exercises the GRACE reason
        # specifically, not the (also-new) marker gate.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=30)           # well under GRACE_SECONDS (300)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertEqual(sent, [], "must not nudge before grace elapses")
        self.assertFalse(any("subagent-apierr" in ln for ln in logs), logs)

    # --- (4a-sub) subagent text-toolcall stall -----------------------------------

    def test_subagent_textcall_stall_nudges_idle_pane(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT)],
            sup_age=10, sub_age=200)          # sub_age > STALL_TEXTCALL_SECONDS (120)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertTrue(any(ln.startswith("subagent-textcall-nudge#1") for ln in logs),
                        "expected a subagent-textcall nudge log line, got: %r" % logs)
        nudges = [a for a in sent if "-l" in a
                 and any("background worker" in x and self.WORKER in x for x in a)]
        self.assertTrue(nudges, "expected a targeted nudge naming the worker, "
                                "sent=%r" % sent)

    def test_subagent_textcall_stall_busy_pane_pings_only(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT)],
            sup_age=10, sub_age=200)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.BUSY_CAP)
        self.assertEqual(sent, [], "MUST NOT type into a busy pane")
        self.assertTrue(any("subagent-textcall-busy" in ln for ln in logs), logs)
        self.assertTrue(pings, "expected a Discord ping instead of a keystroke")

    # --- adversarial-review findings (autopilot cumulative-diff review) ---------

    def test_subagent_apierror_nudge_does_not_double_inject_with_job4(self):
        # Job 1b (subagent api-error) and job 4 (supervisor ⏳-working-stall)
        # can BOTH be true for the SAME pane in the SAME poll: the
        # supervisor itself is `⏳ WORKING` and idle past STALL_WORKING_
        # SECONDS, AND its own subagent is dying. Job 1b's own nudge did
        # not `continue` afterward, so job 4 fell through and injected a
        # SECOND keystroke into the same pane, gated on the now-STALE
        # pre-injection pane capture. Exactly ONE literal-text nudge must
        # be sent per pane per poll.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("⏳ WORKING: čakám na workera.")],
            [_assistant_apierror()],
            sup_age=2000, sub_age=2000)      # both > STALL_WORKING_SECONDS (1800)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        literal_nudges = [a for a in sent if "-l" in a]
        self.assertEqual(len(literal_nudges), 1,
                         "exactly ONE keystroke injection per pane per poll, "
                         "got: %r" % sent)
        self.assertTrue(any("background worker" in x and self.WORKER in x
                            for a in literal_nudges for x in a),
                        "the one injection must be job 1b's targeted subagent "
                        "nudge, not job 4's generic self-check: sent=%r" % sent)

    def test_subagent_apierror_skipped_once_supervisor_reports_done(self):
        # A dying background worker whose transcript happens to be the
        # newest file under subagents/ must stop nudging once the
        # SUPERVISOR itself has moved past `⏳ WORKING` (here: `✅ DONE`) —
        # a historical worker's frozen last-entry api-error must never
        # keep firing into an already-finished session.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("✅ DONE: hotovo, PR zmergovaný.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=2000)         # sub_age well past GRACE_SECONDS (300)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertEqual(sent, [], "must NOT type into a pane whose session is done")
        self.assertFalse(any("subagent-apierr" in ln for ln in logs),
                         "must not even attempt the subagent-apierr detector "
                         "once the supervisor reports done: %r" % logs)

    def test_subagent_apierror_skipped_when_file_older_than_max_age(self):
        # `newest_subagent_transcript` always returns the MOST RECENTLY
        # WRITTEN file under subagents/, even if that file itself is
        # ancient — with no age ceiling, a single old dying-worker file
        # would nudge/escalate FOREVER. Supervisor is genuinely still
        # `⏳ WORKING` here (so the marker gate alone would NOT stop it) —
        # only the max-age ceiling on the subagent file itself does.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("⏳ WORKING: stále bežím.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=3 * 3600)     # 3h — older than the 2h ceiling
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertEqual(sent, [], "must NOT nudge for a subagent file past the age ceiling")
        self.assertFalse(any("subagent-apierr" in ln for ln in logs), logs)


class ForeignTmuxUsersNowEmpty(unittest.TestCase):
    """montalu was the ONLY foreign-tmux user (its claude session ran inside
    NEWLEVEL's tmux on dev1, so its own watchdog could never see the pane —
    job 8 would always conclude "no session runs" and false-ping, #1727/
    #1732/#1827). Since the subdev migration (airuleset#33 + odoo-erp#1895,
    2026-07-24) montalu runs in its OWN tmux session on subdev, so no user's
    watchdog should skip pane-driven jobs anymore. The mechanism itself stays
    wired (empty tuple, not deleted) for a future shared-tmux stream."""

    def test_no_users_are_foreign_tmux_anymore(self):
        self.assertEqual(wd._FOREIGN_TMUX_USERS, ())


def tempfile_mkdtemp_cleanup(testcase):
    tmp = TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    return tmp.name


def _isolate_compact_claims(testcase):
    """#78 — give this test its OWN isolated compact-claims.json AND
    compact-sync.log instead of the real `~/.claude/` copies. Unlike the
    pre-#78 compact-delivered.json dedup (only touched when a msg_hash is
    present), the shared claim gate is consulted UNCONDITIONALLY on every
    `/compact` send attempt — so any test exercising job 14/15/17 or the
    synchronous #65 path would otherwise read/write the REAL files. The
    live systemd watchdog executes this repo's WORKING TREE every 60s on
    THIS box (this repo's own CLAUDE.md) — a test process touching the
    real files would race a live production job. Call from `setUp` in any
    test class that exercises those code paths."""
    tmp = tempfile_mkdtemp_cleanup(testcase)
    p = Path(tmp) / "compact-claims-test.json"
    patcher = unittest.mock.patch.object(wd, "compact_claims_path", return_value=p)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    logp = Path(tmp) / "compact-sync-test.log"
    log_patcher = unittest.mock.patch.object(wd, "compact_sync_log_path",
                                             return_value=logp)
    log_patcher.start()
    testcase.addCleanup(log_patcher.stop)
    return p


# --------------------------------------------------------------------------- #
# #42 rework — Job 12: MODEL RECONCILE, restart-based. The managed default
# (MANAGED_MODEL in airuleset.py) only binds a NEW Claude Code session;
# several long-lived sessions were still parked on Fable/Opus-4 — the single
# biggest cost line. The ORIGINAL #37 version typed `/model <target>` into
# the stale session — but a live incident (gatekeeper, 2026-07-25) proved
# that is structurally futile: a running session's available-model list is
# fixed at its own start, so a model released after that can never be
# selected via `/model`, no matter how many retries. This job now RESTARTS
# the session instead: `/exit`, wait for the shell, relaunch `claude` (the
# managed bashrc function bakes `--model` into every launch — this job never
# passes one), accept the "Resume from summary" dialog for a large prior
# session (or proceed directly when none appears). Never touches a busy
# pane, an open dialog, an unsent draft, a copy-mode pane, or one running a
# BACKGROUND AGENT (a restart would kill it); never sends two consecutive
# Escapes; dedups per session id; bounded attempts, then gives up for good.
# --------------------------------------------------------------------------- #

MR_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
MR_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
MR_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
MR_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
# A live background agent — must NEVER be restarted (issue #42 item 2/#36).
MR_BG_AGENT_CAP = ("● main\n◯ autopilot-worker  Waiting for deploy-prod.yml jobs\n"
                   "❯ \n  ctx ███░  caveman:lite\n")
MR_BG_WAIT_CAP = "✻ Waiting for 2 background agents to finish\n❯ \n  ctx ███░\n"
# The agent-strip SELECTOR holding focus (issue #36) while otherwise idle —
# the ONE Escape `_restart_pane` must send before typing `/exit`.
MR_STRIP_SELECTED_IDLE_CAP = "❯ ● main\n❯ \n  ctx ███░  caveman:lite\n"
MR_TARGET = "claude-opus-5[1m]"
# Claude Code's large-prior-session resume dialog (verified live, gk 2026-07-25).
MR_RESUME_DIALOG_CAP = (
    "This session is 1h 21m old and 701.9k tokens.\n"
    "Resuming the full session will consume a substantial portion of your "
    "usage limits. We recommend resuming from a summary.\n"
    "❯ 1. Resume from summary (recommended)\n"
    "  2. Resume full session as-is\n"
    "  3. Don't ask me again\n")


def _seed_transcript(projects_dir, cwd, sid, model):
    d = Path(projects_dir) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    _write_jsonl(p, [
        {"type": "assistant", "message": {"model": model,
                                          "content": [{"type": "text", "text": "hi"}]}},
    ])
    return p


class RestartFakeTmux:
    """Fake `run` for job 12's RESTART sequence (#42). `initial_captured` is
    what `capture-pane` returns for the very FIRST call — the guard-check
    frame `model_reconcile`'s main loop reads before deciding whether to
    restart at all. Every capture-pane call AFTER that first one pulls from
    `cap_seq` in order, clamped at the last entry once exhausted (simulates
    "never resolves" for a timeout scenario). `shell_after`: how many
    `#{pane_current_command}` polls before the shell prompt is reported (a
    huge number simulates `/exit` never taking effect, for the
    bounded-poll-fails test)."""

    def __init__(self, panes, initial_captured, cap_seq=(), shell_after=1,
                in_mode=False):
        self.panes = panes                 # [(pane_id, cmd, cwd)]
        self.initial_captured = initial_captured
        self.cap_seq = list(cap_seq)
        self.shell_after = shell_after
        self.in_mode = in_mode
        self.sent = []
        self._cap_calls = 0
        self._cmd_polls = 0

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s" % t for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            if argv[-1] == "#{pane_current_command}":
                self._cmd_polls += 1
                return "bash" if self._cmd_polls >= self.shell_after else "claude"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            self._cap_calls += 1
            if self._cap_calls == 1:
                return self.initial_captured
            if not self.cap_seq:
                return self.initial_captured
            idx = min(self._cap_calls - 2, len(self.cap_seq) - 1)
            return self.cap_seq[idx]
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]

    def no_consecutive_escapes(self):
        ks = self.keys()
        return not any(ks[i] == "Escape" and ks[i + 1] == "Escape"
                       for i in range(len(ks) - 1))

    def reset_calls(self):
        """Reuse the same fake (same config) across simulated SWEEPS — a
        real sweep re-polls tmux from scratch every ~60s, so a multi-sweep
        test resets the call counters (never the config) between rounds."""
        self.sent = []
        self._cap_calls = 0
        self._cmd_polls = 0


class TestTranscriptLastModel(unittest.TestCase):
    def test_returns_last_assistant_model(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [
            {"type": "assistant",
             "message": {"model": "claude-fable-5",
                        "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant",
             "message": {"model": "claude-opus-5[1m]",
                        "content": [{"type": "text", "text": "bye"}]}},
        ])
        self.assertEqual(wd.transcript_last_model(p), "claude-opus-5[1m]")

    def test_missing_model_field_returns_empty(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [{"type": "user", "message": {"content": "hi"}}])
        self.assertEqual(wd.transcript_last_model(p), "")

    def test_nonexistent_file_returns_empty(self):
        self.assertEqual(wd.transcript_last_model(Path("/nonexistent/x.jsonl")), "")


class TestTranscriptCurrentContext(unittest.TestCase):
    """Job 15 (#39/#43 follow-up) needs the session's CURRENT context size —
    cache_read_input_tokens + cache_creation_input_tokens off the newest
    assistant usage entry. A single API call can render as several
    transcript LINES (thinking / text / tool_use), each carrying an
    IDENTICAL usage snapshot under the SAME `message.id` (verified live
    against a real forestshop-parovanie-produktov transcript, 2026-07-25) —
    grouping by id and taking MAX (never SUM) is what keeps one turn from
    being triple-counted."""

    def test_sums_cache_read_and_cache_creation(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {
                "id": "msg_1",
                "usage": {"cache_read_input_tokens": 400000,
                         "cache_creation_input_tokens": 5000}}},
        ])
        self.assertEqual(wd.transcript_current_context(p), 405000)

    def test_groups_by_message_id_never_sums_across_records(self):
        # ONE API call rendered as thinking + text + tool_use — three
        # transcript lines, the SAME message.id, IDENTICAL usage. Must
        # count as ONE turn, never 3x.
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        u = {"cache_read_input_tokens": 634095, "cache_creation_input_tokens": 5943}
        _write_jsonl(p, [
            {"type": "assistant", "message": {"id": "msg_A", "usage": u,
                                              "content": [{"type": "thinking", "thinking": "..."}]}},
            {"type": "assistant", "message": {"id": "msg_A", "usage": u,
                                              "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "message": {"id": "msg_A", "usage": u,
                                              "content": [{"type": "tool_use", "name": "Read"}]}},
        ])
        self.assertEqual(wd.transcript_current_context(p), 634095 + 5943)

    def test_only_the_newest_message_id_group_counts(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {
                "id": "msg_OLD",
                "usage": {"cache_read_input_tokens": 100000,
                         "cache_creation_input_tokens": 0}}},
            {"type": "assistant", "message": {
                "id": "msg_NEW",
                "usage": {"cache_read_input_tokens": 450000,
                         "cache_creation_input_tokens": 1000}}},
        ])
        self.assertEqual(wd.transcript_current_context(p), 451000)

    def test_takes_max_not_sum_within_a_group(self):
        # defensive — shouldn't happen live (same API call = same usage
        # snapshot) but grouping must never ADD two records sharing an id.
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {
                "id": "msg_A",
                "usage": {"cache_read_input_tokens": 300000,
                         "cache_creation_input_tokens": 0}}},
            {"type": "assistant", "message": {
                "id": "msg_A",
                "usage": {"cache_read_input_tokens": 305000,
                         "cache_creation_input_tokens": 0}}},
        ])
        self.assertEqual(wd.transcript_current_context(p), 305000)

    def test_missing_message_id_returns_that_entrys_context_standalone(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {
                "usage": {"cache_read_input_tokens": 100000,
                         "cache_creation_input_tokens": 0}}},
        ])
        self.assertEqual(wd.transcript_current_context(p), 100000)

    def test_no_usage_entries_returns_zero(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "s1.jsonl"
        _write_jsonl(p, [{"type": "assistant",
                         "message": {"content": [{"type": "text", "text": "hi"}]}}])
        self.assertEqual(wd.transcript_current_context(p), 0)

    def test_nonexistent_file_returns_zero(self):
        self.assertEqual(wd.transcript_current_context(Path("/nonexistent/x.jsonl")), 0)


class TestReconcileCandidatePanes(unittest.TestCase):
    def test_filters_to_claude_node_bun(self):
        def fake_run(argv, timeout=8):
            return "\n".join([
                "%1\tclaude\t/home/x/proj-a",
                "%2\tnode\t/home/x/proj-b",
                "%3\tbun\t/home/x/proj-c",
                "%4\tbash\t/home/x/proj-d",
            ])
        panes = wd._reconcile_candidate_panes(fake_run)
        cwds = {cwd for pid, cwd, cmd in panes}
        self.assertIn("/home/x/proj-a", cwds)
        self.assertIn("/home/x/proj-b", cwds)
        self.assertIn("/home/x/proj-c", cwds)
        self.assertNotIn("/home/x/proj-d", cwds)

    def test_dedups_the_same_pane_id_listed_under_multiple_grouped_sessions(self):
        # live dev1 finding: a tmux GROUPED session (e.g. marek-10/marek-25
        # sharing linked windows) makes `tmux list-panes -a` list the SAME
        # underlying pane_id once per session name it's linked under.
        def fake_run(argv, timeout=8):
            return "\n".join([
                "%9\tclaude\t/home/x/proj-a",   # session 1
                "%9\tclaude\t/home/x/proj-a",   # session 2 (same grouped pane)
                "%9\tclaude\t/home/x/proj-a",   # session 3 (same grouped pane)
            ])
        panes = wd._reconcile_candidate_panes(fake_run)
        self.assertEqual(len(panes), 1, panes)


class TestModelReconcile(unittest.TestCase):
    CWD = "/home/newlevel/devel/demo"
    PANE = "%9"

    def _go(self, model, initial_captured, cap_seq=(), state=None,
           target_model=MR_TARGET, dry_run=False, in_mode=False,
           shell_after=1):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", model)
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                               initial_captured, cap_seq=cap_seq,
                               shell_after=shell_after, in_mode=in_mode)
        state = {} if state is None else state
        logs = wd.model_reconcile(time.time(), tmux, state, target_model,
                                  dry_run=dry_run, projects_dir=proj,
                                  sleep_fn=lambda s: None)
        return tmux, logs, state

    def test_fable_session_restarts_no_dialog(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_IDLE_CAP])
        self.assertIn("/exit", tmux.typed_texts())
        self.assertIn(wd.RELAUNCH_CMD, tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(state["modelswitch"]["sess-abc"])

    def test_restart_sources_bashrc_before_relaunching_claude(self):
        # #79 -- a shell OLDER than #77's launcher rewrite never re-reads
        # .bashrc on its own (bash reads it once, at shell start). A bare
        # `claude` typed into such a shell resolves to the FROZEN old fat
        # function with `--settings '{"ultracode":true}'` baked in, so
        # every watchdog restart (job 12 / job 18) silently resurrects
        # ultracode. The restart must re-source .bashrc in the SAME
        # command so it always resolves the CURRENT wrapper, regardless
        # of how old the target shell is -- a bare "claude" must never be
        # typed by the restart sequence again.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_IDLE_CAP])
        self.assertIn(wd.RELAUNCH_CMD, tmux.typed_texts())
        self.assertNotIn("claude", tmux.typed_texts())

    def test_opus4_session_restarts_with_resume_dialog(self):
        tmux, logs, state = self._go("claude-opus-4-8", MR_IDLE_CAP,
                                     cap_seq=[MR_RESUME_DIALOG_CAP, MR_IDLE_CAP])
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(state["modelswitch"]["sess-abc"])

    def test_already_on_target_model_is_skipped(self):
        tmux, logs, state = self._go(MR_TARGET, MR_IDLE_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state.get("modelswitch", {}), {})

    def test_sonnet_session_is_skipped(self):
        tmux, logs, state = self._go("claude-sonnet-5", MR_IDLE_CAP)
        self.assertEqual(tmux.sent, [])

    def test_already_reconciled_session_is_never_retried(self):
        state = {"modelswitch": {"sess-abc": True}}
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, state=state)
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_is_skipped(self):
        tmux, logs, state = self._go("claude-fable-5", MR_BUSY_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"], "busy")

    def test_draft_pane_is_never_typed_over(self):
        tmux, logs, state = self._go("claude-fable-5", MR_DRAFT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"], "draft")

    def test_unknown_chrome_below_box_is_reported_as_draft_not_busy(self):
        # THE #46 live incident: an unrecognized chrome row (`⧉  <project>`)
        # below a separator-bounded box holding a draft must be classified
        # by its ACTUAL content — "skip draft", never "skip busy" (which
        # made the pane invisible to every keystroke job, mislabeled).
        cap = StructuralInputLineDetection.UNKNOWN_CHROME_DRAFT_CAP
        tmux, logs, state = self._go("claude-fable-5", cap)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"], "draft")

    def test_no_boundary_at_all_is_logged_as_no_input_line_not_busy(self):
        # A capture where NEITHER strategy can locate a boundary at all is a
        # genuinely different situation from a busy pane — split logging so
        # the real cause is visible instead of collapsing into "busy".
        cap = StructuralInputLineDetection.ALL_CHROME_NO_BOX_CAP
        tmux, logs, state = self._go("claude-fable-5", cap)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip no-input-line" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"],
                         "no-input-line")

    def test_open_dialog_is_skipped(self):
        tmux, logs, state = self._go("claude-fable-5", MR_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"],
                         "dialog-open")

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"],
                         "in-mode")

    def test_bg_agent_strip_row_blocks_restart(self):
        # issue #42 item 2: a `◯ <agent>` agent-strip row means a background
        # worker is in flight — a restart (`/exit`) would KILL it. Must
        # NEVER touch this pane, even though it still shows a free `❯`.
        tmux, logs, state = self._go("claude-fable-5", MR_BG_AGENT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("bg-agent" in ln for ln in logs), logs)
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"],
                         "bg-agent")

    def test_bg_agent_ambient_wait_text_blocks_restart(self):
        # the OTHER in-flight-agent signal: CC's ambient "Waiting for N
        # background agents to finish" line, with no strip row visible.
        tmux, logs, state = self._go("claude-fable-5", MR_BG_WAIT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("bg-agent" in ln for ln in logs), logs)

    def test_dry_run_never_sends_keys(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertEqual(state.get("modelswitch", {}), {})

    def test_no_target_model_disables_the_job_entirely(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, target_model=None)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(logs, [])

    def test_exact_keystroke_sequence_no_dialog(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_IDLE_CAP])
        self.assertEqual(tmux.keys(), ["/exit", "Enter", wd.RELAUNCH_CMD, "Enter"])

    def test_exact_keystroke_sequence_with_dialog(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_RESUME_DIALOG_CAP, MR_IDLE_CAP])
        self.assertEqual(tmux.keys(),
                         ["/exit", "Enter", wd.RELAUNCH_CMD, "Enter", "Enter"])

    def test_strip_selected_gets_one_escape_before_exit(self):
        # issue #36: the agent-strip SELECTOR holding focus swallows a bare
        # Enter as navigation instead of submit — `/exit` must be preceded
        # by ONE Escape when (and only when) the strip is selected.
        tmux, logs, state = self._go("claude-fable-5", MR_STRIP_SELECTED_IDLE_CAP,
                                     cap_seq=[MR_IDLE_CAP])
        self.assertEqual(tmux.keys(),
                         ["Escape", "/exit", "Enter", wd.RELAUNCH_CMD, "Enter"])
        self.assertTrue(tmux.no_consecutive_escapes())

    def test_no_two_consecutive_escapes_ever_sent(self):
        # issue #35: a rapid double-Escape into a pane holding a draft
        # PERMANENTLY DELETES it — never sent anywhere in this job, on
        # ANY decision path (restart, no-restart, or the one Escape case).
        scenarios = [
            (MR_IDLE_CAP, [MR_IDLE_CAP], 1),
            (MR_IDLE_CAP, [MR_RESUME_DIALOG_CAP, MR_IDLE_CAP], 1),
            (MR_STRIP_SELECTED_IDLE_CAP, [MR_IDLE_CAP], 1),
            (MR_BUSY_CAP, (), 1),
            (MR_DRAFT_CAP, (), 1),
            (MR_DIALOG_CAP, (), 1),
            (MR_BG_AGENT_CAP, (), 1),
            (MR_IDLE_CAP, (), 10 ** 6),   # shell never returns after /exit
        ]
        for cap, seq, shell_after in scenarios:
            tmux, _logs, _state = self._go("claude-fable-5", cap, cap_seq=seq,
                                          shell_after=shell_after)
            self.assertTrue(tmux.no_consecutive_escapes(), cap)

    def test_shell_never_returns_after_exit_fails_bounded(self):
        # the poll must be BOUNDED — never an infinite/unbounded wait
        # (no-timeout-band-aids.md). `claude` must NEVER be typed if the
        # shell never came back — retyping a command over a dead `/exit`
        # would land inside whatever the pane is ACTUALLY still showing.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     shell_after=10 ** 6)
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertTrue(any("shell did not return" in ln for ln in logs), logs)
        self.assertNotIn("claude", tmux.typed_texts())
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))
        self.assertEqual(state["modelswitch_attempts"]["sess-abc"], 1)
        # never polled capture-pane past the initial guard-check frame —
        # the shell-return wait uses ONLY display-message polls.
        self.assertEqual(tmux._cap_calls, 1)

    def test_relaunch_never_renders_fails_bounded(self):
        # shell comes back fine, but the relaunched `claude` never shows
        # EITHER the resume dialog or a bare idle prompt — bounded, not
        # an infinite wait.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_BUSY_CAP])
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertTrue(any("relaunch did not render" in ln for ln in logs), logs)
        self.assertIn(wd.RELAUNCH_CMD, tmux.typed_texts())
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))
        self.assertEqual(tmux._cap_calls - 1, wd.MODEL_RESTART_LAUNCH_MAX_POLLS)

    def test_dialog_that_never_settles_fails_bounded(self):
        # the dialog IS seen and accepted (confirm Enter sent), but the
        # session never settles at an idle prompt afterwards — bounded.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     cap_seq=[MR_RESUME_DIALOG_CAP, MR_BUSY_CAP])
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertTrue(any("did not settle idle" in ln for ln in logs), logs)
        self.assertEqual(tmux.keys(),
                         ["/exit", "Enter", wd.RELAUNCH_CMD, "Enter", "Enter"])
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))

    def test_attempts_counter_increments_on_each_failure(self):
        state = {}
        self._go("claude-fable-5", MR_IDLE_CAP, shell_after=10 ** 6, state=state)
        self.assertEqual(state["modelswitch_attempts"]["sess-abc"], 1)

    def test_successful_switch_clears_any_prior_attempts_counter(self):
        state = {"modelswitch_attempts": {"sess-abc": 2}}
        self._go("claude-fable-5", MR_IDLE_CAP, cap_seq=[MR_IDLE_CAP], state=state)
        self.assertNotIn("sess-abc", state.get("modelswitch_attempts", {}))

    def test_repeated_failures_stop_retrying_after_max_attempts(self):
        # LIVE INCIDENT (gk, 2026-07-25): the same pane FAILed on every single
        # ~60s sweep forever — every FAIL released the dedup claim so the
        # NEXT sweep retried, burning a full context re-read (prompt cache
        # invalidation) every time. Cap attempts per session; once the cap
        # is hit, GIVE UP for good (never retry that session again) and say
        # so in the log.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        state = {}
        logs = []
        for _ in range(wd.MODEL_RECONCILE_MAX_ATTEMPTS):
            # a fresh fake per simulated sweep — a real sweep re-polls tmux
            # from scratch every ~60s; the shell never returning is the
            # deterministic FAIL every round.
            tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                   MR_IDLE_CAP, shell_after=10 ** 6)
            logs = wd.model_reconcile(time.time(), tmux, state, MR_TARGET,
                                      dry_run=False, projects_dir=proj,
                                      sleep_fn=lambda s: None)
        self.assertTrue(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertEqual(state["modelswitch_attempts"]["sess-abc"],
                         wd.MODEL_RECONCILE_MAX_ATTEMPTS)
        self.assertTrue(state["modelswitch"]["sess-abc"])  # never retried again

        # one more sweep must not type ANYTHING at all — the session gave up
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.model_reconcile(time.time(), tmux, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])

    def test_below_cap_failures_still_release_the_claim_for_retry(self):
        # a single failure (attempts=1, below MODEL_RECONCILE_MAX_ATTEMPTS)
        # must behave exactly as before the cap existed — released for retry.
        state = {}
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     shell_after=10 ** 6, state=state)
        self.assertFalse(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))

    def test_needs_restart_recorded_when_unsafe_and_cleared_once_safe(self):
        # issue #42 item 4: a NOT-safe pane is recorded as "needs restart"
        # in state and re-evaluated on a later sweep — never forced.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        state = {}
        tmux1 = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
        wd.model_reconcile(time.time(), tmux1, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None)
        self.assertIn("sess-abc", state.get("modelswitch_pending", {}))
        self.assertEqual(state["modelswitch_pending"]["sess-abc"]["reason"], "busy")

        tmux2 = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                MR_IDLE_CAP, cap_seq=[MR_IDLE_CAP])
        wd.model_reconcile(time.time(), tmux2, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None)
        self.assertNotIn("sess-abc", state.get("modelswitch_pending", {}))


class TestModelReconcileHandledSet(unittest.TestCase):
    """#70: job 12 must record every sid it actually attempts to restart THIS
    sweep into a shared `handled` set, so job 18 (hooks-reconcile) can see —
    within the SAME sweep — that this session is already being restarted for
    a MODEL change, and skip firing a SECOND restart for a hooks change that
    happens to land in the same sweep."""

    CWD = "/home/newlevel/devel/demo"
    PANE = "%9"

    def test_handled_set_records_sid_on_real_restart_claim(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                               MR_IDLE_CAP, cap_seq=[MR_IDLE_CAP])
        state = {}
        handled = set()
        wd.model_reconcile(time.time(), tmux, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None,
                           handled=handled)
        self.assertIn("sess-abc", handled)

    def test_dry_run_never_populates_the_handled_set(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        state = {}
        handled = set()
        wd.model_reconcile(time.time(), tmux, state, MR_TARGET, dry_run=True,
                           projects_dir=proj, sleep_fn=lambda s: None,
                           handled=handled)
        self.assertEqual(handled, set())

    def test_skipped_pane_never_populates_the_handled_set(self):
        # a busy pane is never claimed at all -- nothing to coalesce against.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
        state = {}
        handled = set()
        wd.model_reconcile(time.time(), tmux, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None,
                           handled=handled)
        self.assertEqual(handled, set())


# --------------------------------------------------------------------------- #
# #70 — Job 18: HOOKS RECONCILE, restart-based. Claude Code snapshots its
# hook set ONCE at process START (`rCu()` / telemetry event
# `setup_hooks_captured`, read directly out of the CC 2.1.220 binary -- #70's
# own binary citation) and NEVER re-reads it -- so a hook deployed into
# `settings.json` while a session is ALREADY RUNNING has ZERO effect on that
# session for its entire remaining lifetime, no matter how many new hooks get
# deployed. This job is job 12's EXACT restart machinery
# (`_restart_pane`/`_pane_has_bg_agent`/the boundary-classification guards)
# driven by a DIFFERENT staleness signal: the CONTENT hash (never mtime) of
# the effective settings.json `"hooks"` block, tracked per session id from
# the first sweep this job observes that session, rather than a target-model
# string read out of the transcript.
#
# There is no way to know retroactively what hash a session ALREADY RUNNING
# at this job's own deploy time actually started with -- so the FIRST sweep
# this job ever sees a given session, it bootstraps: records the CURRENT
# hash as that session's known baseline, takes no action. A LATER sweep
# where the current hash no longer matches that stored baseline is what
# proves the session is genuinely stale, and only then does it restart.
# --------------------------------------------------------------------------- #


def _write_settings(path, hooks_block):
    Path(path).write_text(json.dumps({"hooks": hooks_block}))


HR_HOOKS_A = {"PreToolUse": [{"matcher": "Bash",
                              "hooks": [{"type": "command", "command": "a.sh"}]}]}
HR_HOOKS_B = {"PreToolUse": [{"matcher": "Bash",
                              "hooks": [{"type": "command", "command": "b.sh"}]}]}


class TestHooksConfigHash(unittest.TestCase):
    def test_same_content_same_hash(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "settings.json"
        _write_settings(p, HR_HOOKS_A)
        h1 = wd._hooks_config_hash(p)
        h2 = wd._hooks_config_hash(p)
        self.assertIsNotNone(h1)
        self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "settings.json"
        _write_settings(p, HR_HOOKS_A)
        ha = wd._hooks_config_hash(p)
        _write_settings(p, HR_HOOKS_B)
        hb = wd._hooks_config_hash(p)
        self.assertNotEqual(ha, hb)

    def test_missing_file_returns_none(self):
        self.assertIsNone(wd._hooks_config_hash(Path("/nonexistent/settings.json")))

    def test_invalid_json_returns_none(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "settings.json"
        p.write_text("{not json")
        self.assertIsNone(wd._hooks_config_hash(p))

    def test_key_order_does_not_change_the_hash(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        p = Path(tmp) / "settings.json"
        p.write_text('{"hooks": {"A": 1, "B": 2}}')
        h1 = wd._hooks_config_hash(p)
        p.write_text('{"hooks": {"B": 2, "A": 1}}')
        h2 = wd._hooks_config_hash(p)
        self.assertEqual(h1, h2)


class TestHooksReconcile(unittest.TestCase):
    CWD = "/home/newlevel/devel/demo"
    PANE = "%9"

    def _seed(self, model="claude-sonnet-5"):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", model)
        settings = Path(tmp) / "settings.json"
        _write_settings(settings, HR_HOOKS_A)
        return proj, settings

    def _go(self, settings, initial_captured, proj, cap_seq=(), state=None,
           dry_run=False, in_mode=False, shell_after=1, handled=None):
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                               initial_captured, cap_seq=cap_seq,
                               shell_after=shell_after, in_mode=in_mode)
        state = {} if state is None else state
        logs = wd.hooks_reconcile(time.time(), tmux, state, dry_run=dry_run,
                                  projects_dir=proj, sleep_fn=lambda s: None,
                                  settings_path=settings, handled=handled)
        return tmux, logs, state

    def test_first_sighting_bootstraps_without_restarting(self):
        proj, settings = self._seed()
        state = {}
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state["hooks_session_hash"]["sess-abc"],
                         wd._hooks_config_hash(settings))

    def test_unchanged_hash_does_nothing(self):
        proj, settings = self._seed()
        state = {}
        self._go(settings, MR_IDLE_CAP, proj, state=state)   # bootstrap sweep
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state.get("hooks_restarted", {}), {})

    def test_changed_hash_restarts_no_dialog(self):
        proj, settings = self._seed()
        state = {}
        self._go(settings, MR_IDLE_CAP, proj, state=state)   # bootstrap on hooks A
        _write_settings(settings, HR_HOOKS_B)                 # config changes
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state,
                                     cap_seq=[MR_IDLE_CAP])
        self.assertIn("/exit", tmux.typed_texts())
        self.assertIn(wd.RELAUNCH_CMD, tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK restart (hooks changed)")
                            for ln in logs), logs)
        self.assertTrue(state["hooks_restarted"]["sess-abc"])

    def test_changed_hash_restarts_with_resume_dialog(self):
        proj, settings = self._seed()
        state = {}
        self._go(settings, MR_IDLE_CAP, proj, state=state)
        _write_settings(settings, HR_HOOKS_B)
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state,
                                     cap_seq=[MR_RESUME_DIALOG_CAP, MR_IDLE_CAP])
        self.assertTrue(any(ln.startswith("OK restart (hooks changed)")
                            for ln in logs), logs)

    def test_already_restarted_session_is_never_retried(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"},
                 "hooks_restarted": {"sess-abc": True}}
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_is_skipped(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_BUSY_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)

    def test_draft_pane_is_never_typed_over(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_DRAFT_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)

    def test_open_dialog_is_skipped(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_DIALOG_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)

    def test_in_mode_pane_is_skipped(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state,
                                     in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)

    def test_bg_agent_blocks_restart(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_BG_AGENT_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("bg-agent" in ln for ln in logs), logs)

    def test_dry_run_never_sends_keys(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state,
                                     dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertEqual(state.get("hooks_restarted", {}), {})

    def test_unreadable_settings_disables_job_entirely(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        tmux, logs, state = self._go(Path("/nonexistent/settings.json"),
                                     MR_IDLE_CAP, proj, state=state)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(logs, [])
        self.assertEqual(state["hooks_session_hash"], {"sess-abc": "stale-hash"})

    def test_repeated_failures_stop_retrying_after_max_attempts(self):
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        logs = []
        for _ in range(wd.HOOKS_RECONCILE_MAX_ATTEMPTS):
            tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                   MR_IDLE_CAP, shell_after=10 ** 6)
            logs = wd.hooks_reconcile(time.time(), tmux, state, dry_run=False,
                                      projects_dir=proj, sleep_fn=lambda s: None,
                                      settings_path=settings)
        self.assertTrue(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertEqual(state["hooks_restart_attempts"]["sess-abc"],
                         wd.HOOKS_RECONCILE_MAX_ATTEMPTS)
        self.assertTrue(state["hooks_restarted"]["sess-abc"])
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.hooks_reconcile(time.time(), tmux, state, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None,
                           settings_path=settings)
        self.assertEqual(tmux.sent, [])

    def test_coalesces_with_model_reconcile_handled_set(self):
        # #70 acceptance: if job 12 already restarted (or is restarting) this
        # sid THIS sweep for a model change, job 18 must NOT also fire a
        # second restart for a hooks change landing in the same sweep.
        proj, settings = self._seed()
        state = {"hooks_session_hash": {"sess-abc": "stale-hash"}}
        handled = {"sess-abc"}
        tmux, logs, state = self._go(settings, MR_IDLE_CAP, proj, state=state,
                                     handled=handled)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state.get("hooks_restarted", {}), {})


class RunOnceHooksReconcileWiring(unittest.TestCase):
    """Job 18 is ALWAYS wired (no gating param) -- run_once must invoke it
    every sweep, best-effort, and coalesce with job 12 into ONE restart when
    both a model change and a hooks change hit the same session the same
    sweep (#70)."""

    def test_qualifying_session_fires_through_run_once(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        cwd = "/home/newlevel/devel/hooks-demo"
        # cmd="node" so run_once's OWN list_claude_panes-based jobs (1-9)
        # skip it entirely -- isolates this to job 18's wiring, same pattern
        # as test_target_model_wires_into_model_reconcile.
        _seed_transcript(proj, cwd, "sess-x", "claude-sonnet-5")
        settings = Path(tmp) / "settings.json"
        _write_settings(settings, HR_HOOKS_A)
        state_path.write_text(json.dumps(
            {"hooks_session_hash": {"sess-x": "stale-hash"}}))
        tmux = RestartFakeTmux([("%1", "node", cwd)], MR_IDLE_CAP,
                               cap_seq=[MR_IDLE_CAP])
        logs = wd.run_once(now=time.time(), dry_run=False, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           hooks_settings_path=settings)
        self.assertTrue(any(ln.startswith("OK restart (hooks changed)")
                            for ln in logs), logs)

    def test_no_qualifying_session_produces_no_hooks_reconcile_restart_logs(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertFalse(any("hooks changed" in ln for ln in logs), logs)

    def test_model_and_hooks_change_together_coalesce_into_one_restart(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        cwd = "/home/newlevel/devel/coalesce-demo"
        _seed_transcript(proj, cwd, "sess-y", "claude-fable-5")  # stale model too
        settings = Path(tmp) / "settings.json"
        _write_settings(settings, HR_HOOKS_A)
        state_path.write_text(json.dumps(
            {"hooks_session_hash": {"sess-y": "stale-hash"}}))
        tmux = RestartFakeTmux([("%1", "node", cwd)], MR_IDLE_CAP,
                               cap_seq=[MR_IDLE_CAP])
        logs = wd.run_once(now=time.time(), dry_run=False, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           target_model=MR_TARGET,
                           hooks_settings_path=settings)
        self.assertTrue(any(ln.startswith("OK (model-reconcile)") for ln in logs), logs)
        self.assertFalse(any(ln.startswith("OK restart (hooks changed)")
                             for ln in logs), logs)
        # exactly ONE restart sequence -- not two
        self.assertEqual(tmux.typed_texts().count("/exit"), 1, tmux.typed_texts())
        self.assertEqual(tmux.typed_texts().count(wd.RELAUNCH_CMD), 1, tmux.typed_texts())


# --------------------------------------------------------------------------- #
# #39/#43 follow-up — Job 15: COMPACT OVERGROWN IDLE SESSIONS. Job 14 only
# compacts at a completed-ticket boundary; a long-lived session that is NOT
# an autopilot loop (no ticket boundary ever fires) can sit on a huge,
# still-growing context forever with no mechanism to shrink it. This job
# closes that gap: /compact a session whose current context exceeds
# COMPACT_CONTEXT_THRESHOLD, but ONLY once its pane has been genuinely idle
# for COMPACT_MIN_IDLE_S (no draft, no dialog, not busy, not in copy-mode,
# no in-flight background agent) — deliberately NOT Claude Code's own
# autoCompactWindow (which fires regardless of what the session is doing
# and cuts a long task off mid-work; airuleset STRIPS that setting).
# --------------------------------------------------------------------------- #


def _seed_context_transcript(projects_dir, cwd, sid, ctx_tokens, idle_s=0,
                             now=None, mid="msg_1"):
    """Write one assistant usage entry whose cache_read + cache_creation sums
    to `ctx_tokens`, and set the transcript FILE's mtime `idle_s` seconds
    into the past relative to `now` — the SAME clock job 15's idle gate
    reads via `find_active_transcript`."""
    now = time.time() if now is None else now
    d = Path(projects_dir) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    cache_read = ctx_tokens // 2
    cache_creation = ctx_tokens - cache_read
    _write_jsonl(p, [
        {"type": "assistant", "message": {
            "id": mid, "model": "claude-opus-5[1m]",
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"cache_read_input_tokens": cache_read,
                     "cache_creation_input_tokens": cache_creation}}},
    ])
    mtime = now - idle_s
    os.utime(p, (mtime, mtime))
    return p


def _append_compact_boundary(path, ts=None):
    """#78 — append a `system`/`compact_boundary` entry (Claude Code's own
    durable "a real compaction landed" marker) onto an EXISTING transcript
    file, without disturbing its earlier entries or the file's mtime.
    Real transcripts are append-only; this mirrors that instead of
    `_seed_context_transcript`'s overwrite shortcut."""
    ts = time.time() if ts is None else ts
    iso = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
          .isoformat().replace("+00:00", "Z"))
    entry = {"type": "system", "subtype": "compact_boundary", "timestamp": iso}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _append_usage_entry(path, ctx_tokens, mid):
    """#78 — append ONE assistant usage entry (a UNIQUE `mid` per call,
    never reused, so `transcript_current_context`'s "group by the newest
    message.id" logic never merges entries from different simulated
    sweeps) onto an EXISTING transcript file, mirroring real append-only
    growth instead of `_seed_context_transcript`'s overwrite shortcut."""
    cache_read = ctx_tokens // 2
    cache_creation = ctx_tokens - cache_read
    entry = {"type": "assistant", "message": {
        "id": mid, "model": "claude-opus-5[1m]",
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"cache_read_input_tokens": cache_read,
                 "cache_creation_input_tokens": cache_creation}}}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# #67 (2026-07-26) — job 15's own stash-around-a-draft fixtures. A capture
# already showing the `› stashed` marker means some OTHER command already
# occupies the ONE stash slot (a legitimate skip, zero keystrokes); a
# capture with no marker is the free-slot case, scripted step-by-step via
# `cap_seq` to exercise deliver_with_stash's full C-s / type / Enter
# sequence followed by job 15's own `_wait_for_compact_return` poll.
CS_DRAFT_STASH_OCCUPIED_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  › stashed\n"
CS_STASH_BARE_CAP = "● Hotovo.\n❯\n  ctx ███░  › stashed\n"
CS_STASH_TYPED_CAP = "● Hotovo.\n❯ /compact\n  ctx ███░\n"
CS_STASH_SUBMITTED_CAP = "● Baking…\n✳ Baking… (2s · esc to interrupt)\n  ctx ███░\n"


class TestCompactStaleContext(unittest.TestCase):
    CWD = "/home/newlevel/devel/demo"
    PANE = "%9"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _go(self, ctx_tokens, idle_s, initial_captured=MR_IDLE_CAP, cap_seq=(),
           state=None, dry_run=False, in_mode=False, now=None, sid="sess-abc",
           send_fn=None):
        now = time.time() if now is None else now
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_context_transcript(proj, self.CWD, sid, ctx_tokens, idle_s=idle_s, now=now)
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                               initial_captured, cap_seq=cap_seq, in_mode=in_mode)
        state = {} if state is None else state
        logs = wd.compact_stale_context(now, tmux, state, dry_run=dry_run,
                                        projects_dir=proj, sleep_fn=lambda s: None,
                                        send_fn=send_fn)
        return tmux, logs, state

    def test_below_threshold_context_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD - 1,
                                     wd.COMPACT_MIN_IDLE_S + 10)
        self.assertEqual(tmux.sent, [])

    def test_idle_under_twenty_minutes_is_skipped_even_with_huge_context(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 100000,
                                     wd.COMPACT_MIN_IDLE_S - 10)
        self.assertEqual(tmux.sent, [])

    def test_queued_compact_in_the_pane_blocks_a_second_one(self):
        # #84 — see TestCompactHardCeiling's equivalent test.
        tmux, logs, state = self._go(
            wd.COMPACT_CONTEXT_THRESHOLD + 100000, wd.COMPACT_MIN_IDLE_S + 10,
            initial_captured=CEIL_QUEUED_COMPACT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip queued-compact (compact-stale)" in ln
                            for ln in logs), logs)

    def test_qualifying_session_gets_compacted(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     cap_seq=[MR_BUSY_CAP, MR_IDLE_CAP])
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(state["compact_stale"]["sess-abc"])

    def test_idle_send_threads_pane_id_into_the_shared_claim(self):
        # #82 -- job 15 is one of the four senders the fix must cover;
        # lock that it threads the sending pane through too.
        with unittest.mock.patch.object(wd, "compact_claim_set") as claim_mock:
            self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                    wd.COMPACT_MIN_IDLE_S + 10,
                    cap_seq=[MR_BUSY_CAP, MR_IDLE_CAP])
        self.assertTrue(claim_mock.called)
        self.assertEqual(claim_mock.call_args.kwargs.get("pane_id"), self.PANE)

    def test_bg_agent_strip_row_is_never_compacted(self):
        # issue #36/#42-style guard: a `◯ <agent>` row means a background
        # worker is in flight — /compact must never touch that pane.
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=MR_BG_AGENT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("bg-agent" in ln for ln in logs), logs)

    def test_bg_agent_ambient_wait_text_is_never_compacted(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=MR_BG_WAIT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("bg-agent" in ln for ln in logs), logs)

    def test_busy_pane_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=MR_BUSY_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)

    # ------------------------------------------------------------------- #
    # #67 (2026-07-26) — a draft-holding pane is no longer a dead end: job 15
    # tries deliver_with_stash instead of skipping forever. The occupied-slot
    # case keeps the pre-#67 OUTCOME (zero keystrokes, retried next sweep) —
    # only the REASON changes, since the pre-#67 code never even looked at
    # the stash slot.
    # ------------------------------------------------------------------- #

    def test_draft_with_occupied_stash_slot_is_skipped_and_kept_for_retry(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=CS_DRAFT_STASH_OCCUPIED_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip draft (stash occupied)" in ln for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("compact_stale", {}))
        self.assertEqual(state["compact_stash_skips"]["sess-abc"], 1)

    def test_draft_with_free_stash_slot_gets_stash_delivered(self):
        tmux, logs, state = self._go(
            wd.COMPACT_CONTEXT_THRESHOLD + 1, wd.COMPACT_MIN_IDLE_S + 10,
            initial_captured=MR_DRAFT_CAP,
            cap_seq=[CS_STASH_BARE_CAP, CS_STASH_TYPED_CAP,
                    CS_STASH_SUBMITTED_CAP, MR_IDLE_CAP])
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-stale, stash)")
                            for ln in logs), logs)
        self.assertEqual(state["compact_stale"]["sess-abc"],
                         wd.COMPACT_STALE_PENDING_CONFIRM)
        self.assertNotIn("sess-abc", state.get("compact_stash_skips", {}))

    def test_dry_run_never_attempts_stash_on_a_draft(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=MR_DRAFT_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY (compact-stale, draft)")
                            for ln in logs), logs)
        self.assertEqual(state.get("compact_stash_skips", {}), {})

    def test_stash_skip_counter_pings_owner_every_nth_consecutive_skip(self):
        sent = []

        def fake_send(body, **kw):
            sent.append((body, kw))
            return "sent"

        state = {}
        for _ in range(wd.COMPACT_STASH_SKIP_PING_EVERY - 1):
            self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1, wd.COMPACT_MIN_IDLE_S + 10,
                    initial_captured=CS_DRAFT_STASH_OCCUPIED_CAP, state=state,
                    send_fn=fake_send)
        self.assertEqual(sent, [])
        self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1, wd.COMPACT_MIN_IDLE_S + 10,
                initial_captured=CS_DRAFT_STASH_OCCUPIED_CAP, state=state,
                send_fn=fake_send)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(state["compact_stash_skips"]["sess-abc"],
                         wd.COMPACT_STASH_SKIP_PING_EVERY)

    def test_unknown_chrome_below_box_is_reported_as_draft_not_busy(self):
        # #46 live incident, same class as job 12's. ALSO carries an
        # occupied stash slot (#67) so the assertion stays about
        # kind-classification, not stash mechanics.
        cap = (
            "● Predošlá práca hotová.\n"
            "──────────\n"
            "❯ rozpisany draft text\n"
            "──────────\n"
            "⧉  upomienky-prehlad\n"
            "  ctx ███░  caveman:lite  › stashed\n")
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=cap)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)

    def test_no_boundary_at_all_is_logged_as_no_input_line_not_busy(self):
        cap = StructuralInputLineDetection.ALL_CHROME_NO_BOX_CAP
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=cap)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip no-input-line" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)

    def test_open_dialog_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     initial_captured=MR_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)

    def test_dry_run_never_sends_keys(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertEqual(state.get("compact_stale", {}), {})

    def test_already_compacted_session_is_never_retried(self):
        # the dedup THIS job needs (unlike job 14's fire-and-forget): right
        # after /compact is sent, the transcript's LAST usage record still
        # reads the PRE-compaction (huge) context until the session's next
        # real turn — without a permanent dedup, an idle post-compaction
        # session that never gets a new turn would have /compact re-sent on
        # EVERY ~60s sweep forever.
        state = {"compact_stale": {"sess-abc": True}}
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10, state=state)
        self.assertEqual(tmux.sent, [])

    # ----------------------------------------------------------------- #
    # Resettable dedup (issue #46 part 2): a REAL success must not claim
    # the session PERMANENTLY — only until the observed context confirms
    # the compaction landed, so a session that lives for days can be
    # compacted again after it re-grows past the threshold. The give-up
    # (attempt-cap) claim stays permanent, unchanged.
    # ----------------------------------------------------------------- #

    def test_pending_confirm_with_context_still_high_does_not_resend(self):
        # Right after a real success the transcript's last usage record
        # still reads the PRE-compaction (huge) context until the session's
        # next real turn (job 15's own docstring) — must NOT resend /compact
        # while that stale high reading persists.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, "sess-abc",
                                 wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                 idle_s=wd.COMPACT_MIN_IDLE_S + 10, now=now)
        state = {"compact_stale": {"sess-abc": wd.COMPACT_STALE_PENDING_CONFIRM}}
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.compact_stale_context(now, tmux, state, dry_run=False,
                                 projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state["compact_stale"]["sess-abc"],
                         wd.COMPACT_STALE_PENDING_CONFIRM)

    def test_pending_confirm_with_context_confirmed_low_clears_the_claim(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, "sess-abc",
                                 wd.COMPACT_CONTEXT_THRESHOLD - 1,
                                 idle_s=5, now=now)
        state = {"compact_stale": {"sess-abc": wd.COMPACT_STALE_PENDING_CONFIRM}}
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        logs = wd.compact_stale_context(now, tmux, state, dry_run=False,
                                        projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])
        self.assertNotIn("sess-abc", state.get("compact_stale", {}))
        self.assertTrue(any("CLEARED" in ln for ln in logs), logs)

    # ------------------------------------------------------------------- #
    # #78 (2026-07-26 live incident) — the SHARED /compact claim gate:
    # generalizes #72's job-17-only model to job 15 too. A claim already
    # queued by ANOTHER sender (job 14, job 17, or the synchronous #65
    # path) blocks job 15's own send THIS sweep, regardless of its own
    # context/idle qualification.
    # ------------------------------------------------------------------- #

    def test_another_senders_queued_claim_blocks_job15s_send(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        sid = "sess-abc"
        _seed_context_transcript(proj, self.CWD, sid, wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                 idle_s=wd.COMPACT_MIN_IDLE_S + 10, now=now)
        # #83 -- a real claim always carries a live "proc" fingerprint; a
        # proc-less claim now resolves (and re-enables sending) on its very
        # first evaluation, so this test's claim needs one to stay blocking.
        wd.compact_claim_set(sid, self.CWD, proc=_alive_proc_fingerprint(self))
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.compact_stale_context(now, tmux, {}, dry_run=False,
                                 projects_dir=proj, sleep_fn=lambda s: None)
        # job 15 would otherwise fully qualify (high context, idle enough,
        # idle pane) -- the ONLY reason nothing is sent is the shared claim.
        self.assertEqual(tmux.sent, [])

    def test_after_clearing_a_later_regrowth_triggers_again(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        sid = "sess-abc"

        # sweep 1: qualifies + succeeds — claim set to PENDING-CONFIRM, not True
        p = _seed_context_transcript(proj, self.CWD, sid, wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     idle_s=wd.COMPACT_MIN_IDLE_S + 10, now=now)
        state = {}
        tmux1 = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                MR_IDLE_CAP, cap_seq=[MR_BUSY_CAP, MR_IDLE_CAP])
        logs1 = wd.compact_stale_context(now, tmux1, state, dry_run=False,
                                         projects_dir=proj, sleep_fn=lambda s: None)
        self.assertIn("/compact", tmux1.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs1), logs1)
        self.assertEqual(state["compact_stale"][sid], wd.COMPACT_STALE_PENDING_CONFIRM)

        # sweep 2: transcript still reads the stale high context — no resend
        tmux2 = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.compact_stale_context(now, tmux2, state, dry_run=False,
                                 projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux2.sent, [])
        self.assertEqual(state["compact_stale"][sid], wd.COMPACT_STALE_PENDING_CONFIRM)

        # sweep 3: a real compaction landed — a compact_boundary marker
        # (#78's proof of CONSUMPTION for the SHARED claim, never a bare
        # context read) followed by a fresh, lower-context turn. Real
        # transcripts are append-only, so this APPENDS onto the same file
        # (never `_seed_context_transcript`'s overwrite) — both this job's
        # OWN state (context-based) and the shared claim (boundary-based)
        # clear together, matching real production.
        _append_compact_boundary(p, ts=time.time())
        _append_usage_entry(p, wd.COMPACT_CONTEXT_THRESHOLD - 1, mid="msg_after_compact")
        os.utime(p, (now - 5, now - 5))
        tmux3 = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.compact_stale_context(now, tmux3, state, dry_run=False,
                                 projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux3.sent, [])
        self.assertNotIn(sid, state.get("compact_stale", {}))

        # sweep 4: a real RE-GROWTH past the threshold — eligible again
        # (the shared claim already resolved CONSUMED in sweep 3).
        _append_usage_entry(p, wd.COMPACT_CONTEXT_THRESHOLD + 1, mid="msg_regrowth")
        os.utime(p, (now - wd.COMPACT_MIN_IDLE_S - 10, now - wd.COMPACT_MIN_IDLE_S - 10))
        tmux4 = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                MR_IDLE_CAP, cap_seq=[MR_BUSY_CAP, MR_IDLE_CAP])
        logs4 = wd.compact_stale_context(now, tmux4, state, dry_run=False,
                                         projects_dir=proj, sleep_fn=lambda s: None)
        self.assertIn("/compact", tmux4.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs4), logs4)
        self.assertEqual(state["compact_stale"][sid], wd.COMPACT_STALE_PENDING_CONFIRM)

    def test_permanent_gaveup_claim_is_never_cleared_by_low_context(self):
        # The give-up (attempt-cap) state is DIFFERENT from pending-confirm
        # and must stay permanent regardless of context — never reconsidered.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, "sess-abc",
                                 wd.COMPACT_CONTEXT_THRESHOLD - 1,
                                 idle_s=5, now=now)
        state = {"compact_stale": {"sess-abc": True}}
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_IDLE_CAP)
        wd.compact_stale_context(now, tmux, state, dry_run=False,
                                 projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])
        self.assertIs(state["compact_stale"]["sess-abc"], True)

    def test_exact_keystroke_sequence(self):
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     cap_seq=[MR_BUSY_CAP, MR_IDLE_CAP])
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_strip_selected_gets_one_escape_first(self):
        tmux, logs, state = self._go(
            wd.COMPACT_CONTEXT_THRESHOLD + 1, wd.COMPACT_MIN_IDLE_S + 10,
            initial_captured=MR_STRIP_SELECTED_IDLE_CAP,
            cap_seq=[MR_STRIP_SELECTED_IDLE_CAP, MR_IDLE_CAP])
        self.assertEqual(tmux.keys(), ["Escape", "/compact", "Enter"])
        self.assertTrue(tmux.no_consecutive_escapes())

    def test_no_two_consecutive_escapes_ever_sent(self):
        # issue #35: a rapid double-Escape into a pane holding a draft
        # PERMANENTLY DELETES it — never sent anywhere in this job, on ANY
        # decision path (compact, no-compact, or the one-Escape case).
        scenarios = [
            (MR_IDLE_CAP, [MR_BUSY_CAP, MR_IDLE_CAP]),
            (MR_STRIP_SELECTED_IDLE_CAP, [MR_STRIP_SELECTED_IDLE_CAP, MR_IDLE_CAP]),
            (MR_BUSY_CAP, ()),
            (MR_DRAFT_CAP, ()),
            (MR_DIALOG_CAP, ()),
            (MR_BG_AGENT_CAP, ()),
            (MR_IDLE_CAP, [MR_BUSY_CAP]),   # /compact never returns idle
        ]
        for cap, seq in scenarios:
            tmux, _logs, _state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                          wd.COMPACT_MIN_IDLE_S + 10,
                                          initial_captured=cap, cap_seq=seq)
            self.assertTrue(tmux.no_consecutive_escapes(), cap)

    def test_verify_never_returns_idle_fails_bounded(self):
        # the poll must be BOUNDED (no-timeout-band-aids.md) — /compact has
        # no reliable confirmation TEXT (job 14's own docstring notes this),
        # so "the pane came back to a free idle prompt" is the only
        # observable success signal available.
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     cap_seq=[MR_BUSY_CAP])
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("compact_stale", {}))
        self.assertEqual(state["compact_stale_attempts"]["sess-abc"], 1)

    def test_below_cap_failure_releases_the_claim_for_retry(self):
        state = {}
        tmux, logs, state = self._go(wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                     wd.COMPACT_MIN_IDLE_S + 10,
                                     cap_seq=[MR_BUSY_CAP], state=state)
        self.assertFalse(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("compact_stale", {}))

    def test_repeated_verify_failures_never_resend_the_shared_claim_blocks_it(self):
        # #78 generalizes #72's lesson here too: once the FIRST send sets
        # the SHARED /compact claim, a bounded verification timeout on a
        # LATER sweep must NOT trigger a resend — the claim only resolves
        # via CONSUMED (a real compact_boundary) or FAILED (a session
        # swap), never a mere verification timeout. So the pre-#78
        # "resend every sweep until MAX_ATTEMPTS, then GIVE UP" path never
        # runs past sweep 1 anymore: `attempts_map` records exactly ONE
        # failure and stays there, GIVE UP never fires (unreachable now —
        # there is nothing left to "give up" on since nothing resends).
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, "sess-abc",
                                 wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                 idle_s=wd.COMPACT_MIN_IDLE_S + 10, now=now)
        state = {}
        logs = []
        # #83 -- the fake tmux `run` can never resolve a real proc
        # fingerprint (its `display-message` fake returns a bogus pane
        # pid), so the claim the FIRST sweep sets would otherwise be
        # "proc"-less and (correctly, per #83) resolve again on sweep 2 --
        # patched to a genuinely alive process so this test still proves
        # the claim persists exactly like a real production send does.
        alive_proc = _alive_proc_fingerprint(self)
        with unittest.mock.patch.object(wd, "_pane_claude_proc_fingerprint",
                                        return_value=alive_proc):
            for i in range(wd.COMPACT_STALE_MAX_ATTEMPTS + 2):
                tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                                       MR_IDLE_CAP, cap_seq=[MR_BUSY_CAP])
                logs = wd.compact_stale_context(now, tmux, state, dry_run=False,
                                                projects_dir=proj, sleep_fn=lambda s: None)
                if i == 0:
                    self.assertIn("/compact", tmux.typed_texts())
                else:
                    self.assertEqual(tmux.sent, [], "sweep %d must send nothing" % i)
        self.assertFalse(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertEqual(state["compact_stale_attempts"]["sess-abc"], 1)
        self.assertNotIn("sess-abc", state.get("compact_stale", {}))


# --------------------------------------------------------------------------- #
# #37 follow-up — Job 13: HOURLY BURN SNAPSHOT. Once per hour, append this
# host's $/msgs/avg-context/by-model row (the PREVIOUS full hour) to
# `snapshots.jsonl` — the feed `airuleset.py burn --compare` reads.
# --------------------------------------------------------------------------- #


class TestBurnSnapshotJob(unittest.TestCase):
    def test_writes_once_and_updates_state_guard(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "snapshots.jsonl"
        state = {}
        now = time.time()
        logs = wd.burn_snapshot_job(now, state, snapshot_path=snap_path,
                                    transcripts_root=str(transcripts),
                                    host="dev1", user="z")
        self.assertTrue(snap_path.exists())
        lines = snap_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["host"], "dev1")
        self.assertEqual(row["window_h"], 1)
        self.assertIn("burn_snapshot_hour", state)
        self.assertTrue(any("burn-snapshot" in ln for ln in logs), logs)

    def test_second_call_within_same_hour_is_a_noop(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "snapshots.jsonl"
        state = {}
        # #59 -- FIXED timestamp, not time.time(): a real wall-clock `now`
        # within the last 30s of an hour makes `now + 30` land in the NEXT
        # hour bucket, spuriously failing this "same hour = noop" assertion
        # whenever a test run happens to straddle the boundary.
        now = datetime.datetime(2026, 7, 25, 19, 30, 0,
                                tzinfo=datetime.timezone.utc).timestamp()
        wd.burn_snapshot_job(now, state, snapshot_path=snap_path,
                             transcripts_root=str(transcripts), host="dev1")
        logs2 = wd.burn_snapshot_job(now + 30, state, snapshot_path=snap_path,
                                     transcripts_root=str(transcripts), host="dev1")
        self.assertEqual(logs2, [])
        lines = snap_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1, "must write at most once per hour")

    def test_next_hour_writes_a_second_row(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "snapshots.jsonl"
        state = {}
        now = time.time()
        wd.burn_snapshot_job(now, state, snapshot_path=snap_path,
                             transcripts_root=str(transcripts), host="dev1")
        wd.burn_snapshot_job(now + 3600, state, snapshot_path=snap_path,
                             transcripts_root=str(transcripts), host="dev1")
        lines = snap_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_dry_run_never_writes_or_claims(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "snapshots.jsonl"
        state = {}
        logs = wd.burn_snapshot_job(time.time(), state, snapshot_path=snap_path,
                                    transcripts_root=str(transcripts),
                                    host="dev1", dry_run=True)
        self.assertFalse(snap_path.exists())
        self.assertEqual(state, {})
        self.assertTrue(any("dry-run" in ln for ln in logs), logs)

    def test_creates_parent_directory(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "nested" / "dir" / "snapshots.jsonl"
        state = {}
        wd.burn_snapshot_job(time.time(), state, snapshot_path=snap_path,
                             transcripts_root=str(transcripts), host="dev1")
        self.assertTrue(snap_path.exists())

    def test_failure_never_raises(self):
        # a totally unwritable snapshot path must not blow up the job — the
        # caller (run_once) wraps it in try/except too, but the job itself
        # should behave (log, don't raise) whenever it can.
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        bad_path = Path(tmp) / "not-a-dir" / "x" / "y.jsonl"
        os.makedirs(Path(tmp) / "not-a-dir")
        os.chmod(Path(tmp) / "not-a-dir", 0o400)   # read-only — mkdir(parents) fails
        try:
            with self.assertRaises(Exception):
                wd.burn_snapshot_job(time.time(), {}, snapshot_path=bad_path,
                                     transcripts_root=str(transcripts), host="dev1")
        finally:
            os.chmod(Path(tmp) / "not-a-dir", 0o700)


class RunOnceNewJobsWiring(unittest.TestCase):
    """run_once must actually invoke both new jobs, best-effort, and must NOT
    change behavior for any EXISTING caller that doesn't pass the new params
    (target_model default None keeps model-reconcile fully off)."""

    def test_no_target_model_never_attempts_a_model_switch(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertFalse(any("model-reconcile" in ln for ln in logs), logs)

    def test_writes_a_burn_snapshot_every_call(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        snap_path = Path(tmp) / "snap.jsonl"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           burn_snapshot_path=snap_path)
        self.assertTrue(snap_path.exists())
        self.assertTrue(any("burn-snapshot" in ln for ln in logs), logs)

    def test_target_model_wires_into_model_reconcile(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        # cmd="node" so run_once's OWN per-pane job loop (list_claude_panes
        # only matches "claude") skips it — isolating this to job 12's wiring.
        _seed_transcript(proj, "/home/newlevel/devel/demo", "sess-x",
                        "claude-fable-5")
        state_path = Path(tmp) / "state.json"
        tmux = RestartFakeTmux(
            [("%1", "node", "/home/newlevel/devel/demo")], MR_IDLE_CAP)
        logs = wd.run_once(now=time.time(), dry_run=True, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           target_model=MR_TARGET,
                           burn_snapshot_path=Path(tmp) / "snap.jsonl")
        self.assertTrue(any(ln.startswith("READY (model-reconcile)") for ln in logs),
                        logs)


class RunOnceCompactStaleWiring(unittest.TestCase):
    """Job 15 is ALWAYS wired (no gating param, same shape as job 9's
    goal_autoarm) — run_once must invoke it every sweep, best-effort."""

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def test_qualifying_session_fires_through_run_once(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        now = time.time()
        cwd = "/home/newlevel/devel/demo"
        _seed_context_transcript(proj, cwd, "sess-x", wd.COMPACT_CONTEXT_THRESHOLD + 1,
                                 idle_s=wd.COMPACT_MIN_IDLE_S + 10, now=now)
        # cmd="node" so run_once's OWN per-pane job loop (list_claude_panes
        # only matches "claude") skips it — isolating this to job 15's
        # wiring, same pattern as test_target_model_wires_into_model_reconcile.
        tmux = RestartFakeTmux([("%1", "node", cwd)], MR_IDLE_CAP)
        logs = wd.run_once(now=now, dry_run=True, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertTrue(any(ln.startswith("READY (compact-stale)") for ln in logs), logs)

    def test_no_qualifying_session_produces_no_compact_stale_logs(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertFalse(any("compact-stale" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# #55 — Job 16: HOURLY FLEET BURN. Coordinator-only merge of every managed
# box's own hourly burn-snapshot row (fetched over ssh by the INJECTED
# `fetch` callable — never a real ssh call in these tests) into
# ~/.claude/burn-history/fleet.jsonl, plus a deduped Discord ping when the
# observed weekly-%/day pace exceeds the budget implied by the usage cache.
# --------------------------------------------------------------------------- #

def _snap_row(ts, host, usd, msgs, avg_ctx):
    return {"ts": ts, "host": host, "user": "z", "window_h": 1,
           "usd": usd, "msgs": msgs, "avg_ctx": avg_ctx, "by_model": {}}


def _fleet_now(hour=20, minute=30):
    """FIXED (never wall-clock) timestamp for fleet-burn-job tests, safely
    past the `FLEET_BURN_DELAY_MINUTES` (5) HH:05 gate (#60 point 4) and far
    from any hour boundary — #59: a real `time.time()` here straddles both
    the hour-boundary noop tests AND (since #60) the delay gate depending on
    the wall-clock minute when CI happens to run."""
    return datetime.datetime(2026, 7, 25, hour, minute, 0,
                             tzinfo=datetime.timezone.utc).timestamp()


class TestFleetBurnJob(unittest.TestCase):
    def test_writes_once_and_updates_state_guard(self):
        # #63 -- job 13 stamps the hour that JUST COMPLETED (`_fleet_now()` is
        # 20:30 UTC, so the completed hour is 19:00-20:00 -> ts "19:00:00").
        # The local row must match THAT bucket to count as fresh.
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        local_snap = Path(tmp) / "snapshots.jsonl"
        with open(local_snap, "w") as f:
            f.write(json.dumps(_snap_row("2026-07-25T19:00:00+00:00", "dev1", 1.0, 5, 1000)) + "\n")
        state = {}
        now = _fleet_now()
        hosts = [{"name": "dev2", "host": "5.6.7.8", "user": "newlevel"}]

        def fetch(hs, hb):
            return {"dev2": {"usd": 2.0, "msgs": 3, "avg_ctx": 500, "by_model": {}}}
        logs = wd.fleet_burn_job(now, state, hosts, lambda *a, **k: None,
                                 fetch=fetch, local_snapshot_path=local_snap,
                                 fleet_path=fleet_path)
        self.assertTrue(fleet_path.exists())
        lines = fleet_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["per_host"]["dev1"]["usd"], 1.0)
        self.assertEqual(row["per_host"]["dev2"]["usd"], 2.0)
        self.assertEqual(row["total_usd"], 3.0)
        self.assertIn("fleet_burn_hour", state)
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)
        # the row itself is stamped with the COMPLETED hour, not the current
        # (still-open) one -- #63's other half of the fix.
        import burn as burn_mod
        self.assertEqual(burn_mod.hour_bucket_of_ts(row["ts"]), int(now // 3600) - 1)

    def test_local_row_for_wrong_hour_is_excluded_not_silently_used(self):
        # #63 core regression: dev1's own local snapshots.jsonl tail line must
        # be freshness-checked against the SAME completed-hour bucket as every
        # remote host -- before the fix, the local row was used UNCONDITIONALLY
        # regardless of which hour it was actually for (the "dev1 always has a
        # number, every remote is always --" asymmetry from the issue).
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        local_snap = Path(tmp) / "snapshots.jsonl"
        now = _fleet_now()  # 2026-07-25T20:30:00+00:00 -- completed hour is 19:00
        with open(local_snap, "w") as f:
            # stamped for the CURRENT (not-yet-completed) hour -- must NOT be
            # trusted as this cycle's sample.
            f.write(json.dumps(_snap_row("2026-07-25T20:00:00+00:00", "dev1", 999.0, 5, 1000)) + "\n")
        logs = wd.fleet_burn_job(now, {}, [], lambda *a, **k: None,
                                 fetch=lambda hs, hb: {}, local_snapshot_path=local_snap,
                                 fleet_path=fleet_path)
        row = json.loads(fleet_path.read_text().strip().splitlines()[0])
        self.assertIn("error", row["per_host"]["dev1"])
        self.assertTrue(row["per_host"]["dev1"].get("stale"))
        self.assertEqual(row["total_usd"], 0.0)
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)

    def test_second_call_within_same_hour_is_a_noop(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        state = {}
        now = _fleet_now()
        wd.fleet_burn_job(now, state, [], lambda *a, **k: None,
                          fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        logs2 = wd.fleet_burn_job(now + 30, state, [], lambda *a, **k: None,
                                  fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        self.assertEqual(logs2, [])
        lines = fleet_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1, "must write at most once per hour")

    def test_next_hour_writes_a_second_row(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        state = {}
        now = _fleet_now()
        wd.fleet_burn_job(now, state, [], lambda *a, **k: None,
                          fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        wd.fleet_burn_job(now + 3600, state, [], lambda *a, **k: None,
                          fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        lines = fleet_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_dry_run_never_writes_or_claims(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        state = {}
        logs = wd.fleet_burn_job(_fleet_now(), state, [], lambda *a, **k: None,
                                 fetch=lambda hs, hb: {}, fleet_path=fleet_path, dry_run=True)
        self.assertFalse(fleet_path.exists())
        self.assertEqual(state, {})
        self.assertTrue(any("dry-run" in ln for ln in logs), logs)

    def test_a_raising_fetch_still_writes_local_only_row(self):
        # local row stamped for the COMPLETED hour (#63) so it still counts
        # as fresh even though the remote fetch raises.
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        local_snap = Path(tmp) / "snapshots.jsonl"
        with open(local_snap, "w") as f:
            f.write(json.dumps(_snap_row("2026-07-25T19:00:00+00:00", "dev1", 1.0, 5, 1000)) + "\n")

        def raising_fetch(hs, hb):
            raise RuntimeError("boom")
        logs = wd.fleet_burn_job(_fleet_now(), {}, [{"name": "dev2"}], lambda *a, **k: None,
                                 fetch=raising_fetch, local_snapshot_path=local_snap,
                                 fleet_path=fleet_path)
        self.assertTrue(fleet_path.exists())
        row = json.loads(fleet_path.read_text().strip().splitlines()[0])
        self.assertEqual(row["per_host"]["dev1"]["usd"], 1.0)
        self.assertIn("error", row["per_host"]["dev2"])
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)

    def test_no_local_snapshot_yet_still_merges_remote_only(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        missing_local = Path(tmp) / "no-such-snapshots.jsonl"
        wd.fleet_burn_job(_fleet_now(), {}, [{"name": "dev2"}], lambda *a, **k: None,
                          fetch=lambda hs, hb: {"dev2": {"usd": 1.0, "msgs": 1, "avg_ctx": 1}},
                          local_snapshot_path=missing_local, fleet_path=fleet_path)
        row = json.loads(fleet_path.read_text().strip().splitlines()[0])
        self.assertEqual(row["total_usd"], 1.0)
        self.assertNotIn("dev1", row["per_host"])

    def test_budget_alert_fires_once_deduped_per_hour(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        now = datetime.datetime(2026, 7, 25, 12, 30, tzinfo=datetime.timezone.utc).timestamp()
        # seed a fleet.jsonl row 24h earlier so observed_pct_per_day has 2 samples
        with open(fleet_path, "w") as f:
            f.write(json.dumps({
                "ts": "2026-07-24T12:00:00+00:00", "per_host": {}, "total_usd": 0.0,
                "total_msgs": 0, "weighted_avg_ctx": 0, "weekly_pct": 80,
                "resets_at": "2026-08-01T00:00:00+00:00",
            }) + "\n")
        # weekly at 90%, reset in 6.5 days -> budget (100-90)/6.5 = 1.54%/day;
        # observed (90-80)/24h*24 = 10%/day -> way over budget.
        cache = {"windows": [{"group": "weekly", "percent": 90, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}
        sent = []
        wd.fleet_burn_job(now, {}, [], lambda body, **k: sent.append((body, k)) or "sent",
                          fetch=lambda hs, hb: {}, fleet_path=fleet_path, usage_cache=cache)
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("prekracuje", sent[0][0])
        self.assertTrue(sent[0][1]["dedup_key"].startswith("fleet-burn-budget:"))

    def test_no_alert_when_within_budget(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        now = datetime.datetime(2026, 7, 25, 12, 30, tzinfo=datetime.timezone.utc).timestamp()
        with open(fleet_path, "w") as f:
            f.write(json.dumps({
                "ts": "2026-07-24T12:00:00+00:00", "per_host": {}, "total_usd": 0.0,
                "total_msgs": 0, "weighted_avg_ctx": 0, "weekly_pct": 8,
                "resets_at": "2026-08-01T00:00:00+00:00",
            }) + "\n")
        cache = {"windows": [{"group": "weekly", "percent": 10, "model": None,
                              "resets_at": "2026-08-01T00:00:00+00:00"}]}
        sent = []
        wd.fleet_burn_job(now, {}, [], lambda body, **k: sent.append((body, k)) or "sent",
                          fetch=lambda hs, hb: {}, fleet_path=fleet_path, usage_cache=cache)
        self.assertEqual(sent, [])

    def test_too_early_in_hour_is_a_noop_and_does_not_claim(self):
        # #60 point 4 -- at HH:02 (before FLEET_BURN_DELAY_MINUTES=5) the job
        # must not collect/write/claim the hour at all, so the NEXT sweep
        # (60s later, still within the delay window or past it) can retry.
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        state = {}
        now = datetime.datetime(2026, 7, 25, 20, 2, 0,
                                tzinfo=datetime.timezone.utc).timestamp()
        logs = wd.fleet_burn_job(now, state, [], lambda *a, **k: None,
                                 fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        self.assertEqual(logs, [])
        self.assertFalse(fleet_path.exists())
        self.assertNotIn("fleet_burn_hour", state)

    def test_at_delay_boundary_minute_proceeds_normally(self):
        # exactly HH:05 -- the gate is `< FLEET_BURN_DELAY_MINUTES`, so this
        # minute must NOT be held back.
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        state = {}
        now = datetime.datetime(2026, 7, 25, 20, 5, 0,
                                tzinfo=datetime.timezone.utc).timestamp()
        logs = wd.fleet_burn_job(now, state, [], lambda *a, **k: None,
                                 fetch=lambda hs, hb: {}, fleet_path=fleet_path)
        self.assertTrue(fleet_path.exists())
        self.assertIn("fleet_burn_hour", state)
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)

    def test_fetch_receives_the_last_completed_hour_bucket_not_current(self):
        # #63 -- job 13 (burn_snapshot_job) stamps the hour that JUST
        # completed, never the current (still-open) one. Job 16 must request
        # that SAME completed-hour bucket, or a remote's freshly-written row
        # can never match (`_fleet_remote_row` would always see it as stale)
        # -- the root cause of "every remote column is permanently --".
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        now = _fleet_now()
        received = []

        def fetch(hs, hb):
            received.append(hb)
            return {}
        wd.fleet_burn_job(now, {}, [], lambda *a, **k: None,
                          fetch=fetch, fleet_path=fleet_path)
        self.assertEqual(received, [int(now // 3600) - 1])


class RunOnceFleetWiring(unittest.TestCase):
    """Job 16 is wired only when `fleet_fetch` is given (coordinator-only —
    cmd_watchdog gates this on os.uname().nodename == 'dev1'). Must not
    change behavior for any existing caller that doesn't pass it."""

    def test_no_fleet_fetch_never_attempts_fleet_burn(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertFalse(any("fleet-burn" in ln for ln in logs), logs)

    def test_fleet_fetch_wires_the_job_and_writes_fleet_path(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        fleet_path = Path(tmp) / "fleet.jsonl"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=_fleet_now(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           fleet_fetch=lambda hs, hb: {}, fleet_hosts=[],
                           fleet_path=fleet_path)
        self.assertTrue(fleet_path.exists())
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)

    def test_a_raising_fleet_fetch_never_breaks_the_sweep(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        fleet_path = Path(tmp) / "fleet.jsonl"

        def raising_fetch(hs, hb):
            raise RuntimeError("ssh exploded")

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=_fleet_now(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           fleet_fetch=raising_fetch, fleet_hosts=[{"name": "dev2"}],
                           fleet_path=fleet_path)
        self.assertTrue(fleet_path.exists())
        self.assertTrue(any("fleet-burn" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# #81 — Job 19: HOURLY BURN ALERT. Runs right after job 16; reads the
# LATEST merged fleet.jsonl row and, at most once per hour bucket, pings
# when it crosses an absolute/relative/weekly-step threshold. Plain JSONL
# read + one POST — no agent, no model, never gated on remembering to check.
# --------------------------------------------------------------------------- #

class TestBurnAlertJob(unittest.TestCase):
    def _fleet_file(self, tmp, rows):
        p = Path(tmp) / "fleet.jsonl"
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def test_no_fleet_file_is_a_noop(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"   # never written
        state = {}
        logs = wd.burn_alert_job(time.time(), state, lambda *a, **k: "sent",
                                 fleet_path=fleet_path)
        self.assertEqual(logs, [])
        self.assertNotIn("burn_alert_hour", state)

    def test_quiet_hour_sends_nothing_but_claims_the_bucket(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = self._fleet_file(tmp, [
            {"ts": "2026-07-26T14:00:00+00:00", "total_usd": 1.0, "total_msgs": 5}])
        state = {}
        sent = []
        logs = wd.burn_alert_job(time.time(), state,
                                 lambda *a, **k: sent.append((a, k)) or "sent",
                                 fleet_path=fleet_path, abs_usd=20.0)
        self.assertEqual(sent, [])
        self.assertTrue(any("quiet" in ln for ln in logs), logs)
        self.assertIsNotNone(state.get("burn_alert_hour"))

    def test_triggered_hour_sends_exactly_one_message(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = self._fleet_file(tmp, [
            {"ts": "2026-07-26T14:00:00+00:00", "total_usd": 64.88, "total_msgs": 337}])
        state = {}
        sent = []

        def fake_send(msg, **kw):
            sent.append((msg, kw))
            return "sent"
        logs = wd.burn_alert_job(time.time(), state, fake_send,
                                 fleet_path=fleet_path, abs_usd=20.0)
        self.assertEqual(len(sent), 1)
        self.assertIn("64.88", sent[0][0])
        self.assertEqual(sent[0][1].get("dedup_key"),
                         "burn-alert:%d" % state["burn_alert_hour"])
        self.assertTrue(any("TRIGGERED" in ln for ln in logs), logs)

    def test_second_call_within_the_same_hour_sends_nothing_more(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = self._fleet_file(tmp, [
            {"ts": "2026-07-26T14:00:00+00:00", "total_usd": 64.88, "total_msgs": 337}])
        state = {}
        sent = []

        def fake_send(msg, **kw):
            sent.append((msg, kw))
            return "sent"
        now = time.time()
        wd.burn_alert_job(now, state, fake_send, fleet_path=fleet_path, abs_usd=20.0)
        wd.burn_alert_job(now, state, fake_send, fleet_path=fleet_path, abs_usd=20.0)
        self.assertEqual(len(sent), 1)

    def test_dry_run_never_claims_the_hour_or_sends(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = self._fleet_file(tmp, [
            {"ts": "2026-07-26T14:00:00+00:00", "total_usd": 64.88, "total_msgs": 337}])
        state = {}
        sent = []
        logs = wd.burn_alert_job(time.time(), state,
                                 lambda *a, **k: sent.append((a, k)) or "sent",
                                 fleet_path=fleet_path, abs_usd=20.0, dry_run=True)
        self.assertEqual(sent, [])
        self.assertNotIn("burn_alert_hour", state)
        self.assertTrue(any(ln.startswith("[dry-run]") for ln in logs), logs)

    def test_env_override_lowers_the_absolute_threshold(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = self._fleet_file(tmp, [
            {"ts": "2026-07-26T14:00:00+00:00", "total_usd": 5.0, "total_msgs": 5}])
        state = {}
        sent = []
        with unittest.mock.patch.dict(
                os.environ, {"AIRULESET_BURN_ALERT_ABS_USD": "1"}):
            wd.burn_alert_job(time.time(), state,
                              lambda *a, **k: sent.append((a, k)) or "sent",
                              fleet_path=fleet_path)
        self.assertEqual(len(sent), 1)


class RunOnceBurnAlertWiring(unittest.TestCase):
    """Job 19 is wired only when `burn_alert_enabled` is truthy
    (coordinator-only — cmd_watchdog gates this on
    os.uname().nodename == 'dev1', the SAME check job 16 already uses).
    Must not change behavior for any existing caller that doesn't pass it."""

    def test_disabled_by_default_never_attempts_burn_alert(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        fleet_path = Path(tmp) / "fleet.jsonl"
        with open(fleet_path, "w") as f:
            f.write(json.dumps({"ts": "2026-07-26T14:00:00+00:00",
                                "total_usd": 999.0, "total_msgs": 5}) + "\n")

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           fleet_path=fleet_path)
        self.assertFalse(any("burn-alert" in ln for ln in logs), logs)

    def test_enabled_wires_the_job_and_evaluates_the_fleet_file(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        fleet_path = Path(tmp) / "fleet.jsonl"
        with open(fleet_path, "w") as f:
            f.write(json.dumps({"ts": "2026-07-26T14:00:00+00:00",
                                "total_usd": 999.0, "total_msgs": 5}) + "\n")

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: "sent",
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           fleet_path=fleet_path, burn_alert_enabled=True)
        self.assertTrue(any("burn-alert" in ln and "TRIGGERED" in ln
                           for ln in logs), logs)

    def test_a_raising_burn_alert_never_breaks_the_sweep(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        # a fleet.jsonl containing a genuinely malformed line -- forces
        # burn.load_fleet's own JSON parse to skip it silently, so instead
        # point fleet_path at a path whose PARENT does not exist to force a
        # real exception path inside burn_alert_job's own file handling.
        fleet_path = Path(tmp) / "nonexistent-dir" / "fleet.jsonl"

        def fake_run(argv, timeout=8):
            return ""
        with unittest.mock.patch.object(
                wd, "burn_alert_job", side_effect=RuntimeError("boom")):
            logs = wd.run_once(now=time.time(), dry_run=False, run=fake_run,
                               send_fn=lambda *a, **k: None,
                               projects_dir=proj, state_path=state_path,
                               pending_prefix=str(Path(tmp) / "pending-"),
                               fleet_path=fleet_path, burn_alert_enabled=True)
        self.assertTrue(any("burn-alert error" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# #69 — Job 17: HARD CONTEXT CEILING BACKSTOP. Job 14 only fires at a
# completed-ticket boundary; job 15 only fires once a pane has been
# genuinely idle for COMPACT_MIN_IDLE_S. A WHOLE CLASS of sessions has
# neither trigger (a continuous review/merge master loop; a governance
# session that ends every turn `⏳ WORKING` and is never idle 20 minutes).
# This job closes that gap: above COMPACT_HARD_CEILING, COMPACT_MIN_IDLE_S
# is IGNORED and a busy pane is a perfectly fine send target (a short
# send-keys reliably queues even mid-turn — #65).
# --------------------------------------------------------------------------- #

CEIL_COMPACTING_CAP = ("✻ Compacting conversation… (12s · esc to interrupt)\n"
                       "  ctx ███░  caveman:lite\n")

# #84 — the live gk shape: a long-running turn with `/compact` already queued
# below the spinner, waiting for a turn boundary that never comes.
CEIL_QUEUED_COMPACT_CAP = (
    "· Germinating… (2h 40m 36s · ↓ 69.3k tokens)\n"
    "  ❯ /compact\n"
    "❯ \n"
    "  ctx ███░  caveman:lite\n")


class TestCompactHardCeiling(unittest.TestCase):
    CWD = "/home/newlevel/devel/demo-ceiling"
    PANE = "%9"
    SID = "sess-ceil"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _go(self, ctx_tokens, initial_captured=MR_IDLE_CAP, cap_seq=(),
           state=None, dry_run=False, in_mode=False, now=None, sid=None,
           send_fn=None, ceiling=None, stuck_cycles=None):
        now = time.time() if now is None else now
        sid = sid or self.SID
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_context_transcript(proj, self.CWD, sid, ctx_tokens, idle_s=0, now=now)
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)],
                               initial_captured, cap_seq=cap_seq, in_mode=in_mode)
        state = {} if state is None else state
        logs = wd.compact_hard_ceiling(now, tmux, state, dry_run=dry_run,
                                       projects_dir=proj, send_fn=send_fn,
                                       ceiling=ceiling, stuck_cycles=stuck_cycles)
        return tmux, logs, state

    def test_below_ceiling_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING - 1)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state.get("compact_ceiling", {}), {})

    def test_busy_pane_gets_compacted_this_is_the_whole_point(self):
        # #65's proven busy-pane queuing: unlike job 15, a busy pane is NOT
        # a skip here -- it is the exact scenario this job exists to close
        # (a continuously-busy session that never goes idle).
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-ceiling)") for ln in logs), logs)
        # #72 -- the send-time log must NEVER claim "compacted" -- only
        # keystrokes were sent, nothing has been verified yet.
        self.assertFalse(any("compacted" in ln for ln in logs
                             if ln.startswith("OK")), logs)
        self.assertEqual(state["compact_ceiling"][self.SID],
                         {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD})

    def test_free_idle_input_also_gets_compacted(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_IDLE_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-ceiling)") for ln in logs), logs)

    # ---- #84: a pane that ALREADY has a /compact queued gets no second one.
    # This job is the most exposed sender: it deliberately types into a BUSY
    # pane, and a busy pane is exactly where a queued command sits
    # unexecuted (CC drains its type-ahead queue only where a turn really
    # ENDS). Stacking a second one produces "Not enough messages to compact"
    # when the queue finally drains — the duplicate-compact spam of #84.

    def test_queued_compact_in_the_pane_blocks_a_second_one(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=CEIL_QUEUED_COMPACT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip queued-compact (compact-ceiling)" in ln
                            for ln in logs), logs)
        # …and nothing is claimed, so the session stays eligible once the
        # queue drains and the context is still above the ceiling.
        self.assertEqual(state.get("compact_ceiling", {}), {})

    def test_exact_keystroke_sequence(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_already_compacting_in_pane_is_deduped_no_resend(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=CEIL_COMPACTING_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("already-compacting" in ln for ln in logs), logs)
        self.assertEqual(state.get("compact_ceiling", {}), {})

    def test_draft_with_free_stash_slot_gets_stash_delivered(self):
        tmux, logs, state = self._go(
            wd.COMPACT_HARD_CEILING + 1, initial_captured=MR_DRAFT_CAP,
            cap_seq=[CS_STASH_BARE_CAP, CS_STASH_TYPED_CAP, CS_STASH_SUBMITTED_CAP])
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-ceiling, stash)")
                            for ln in logs), logs)
        self.assertEqual(state["compact_ceiling"][self.SID],
                         {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD})
        self.assertNotIn(self.SID, state.get("compact_stash_skips", {}))

    def test_draft_with_occupied_stash_slot_is_skipped_and_kept_for_retry(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=CS_DRAFT_STASH_OCCUPIED_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip draft (stash occupied)" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, state.get("compact_ceiling", {}))
        self.assertEqual(state["compact_stash_skips"][self.SID], 1)

    def test_dry_run_never_sends_keys(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY (compact-ceiling)") for ln in logs), logs)
        self.assertEqual(state.get("compact_ceiling", {}), {})

    def test_dry_run_never_attempts_stash_on_a_draft(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_DRAFT_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY (compact-ceiling, draft)")
                            for ln in logs), logs)
        self.assertEqual(state.get("compact_stash_skips", {}), {})

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)

    def test_open_dialog_is_skipped(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)

    def test_no_boundary_at_all_is_skipped(self):
        cap = StructuralInputLineDetection.ALL_CHROME_NO_BOX_CAP
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1, initial_captured=cap)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("no-input-line" in ln for ln in logs), logs)

    def test_strip_selected_gets_one_escape_first(self):
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_STRIP_SELECTED_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["Escape", "/compact", "Enter"])

    def test_no_two_consecutive_escapes_ever_sent(self):
        scenarios = [
            (MR_IDLE_CAP, ()),
            (MR_BUSY_CAP, ()),
            (MR_STRIP_SELECTED_IDLE_CAP, ()),
            (MR_DRAFT_CAP, [CS_STASH_BARE_CAP, CS_STASH_TYPED_CAP, CS_STASH_SUBMITTED_CAP]),
            (MR_DIALOG_CAP, ()),
        ]
        for cap, seq in scenarios:
            tmux, _logs, _state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                          initial_captured=cap, cap_seq=seq)
            self.assertTrue(tmux.no_consecutive_escapes(), cap)

    def test_env_override_lowers_the_ceiling(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_COMPACT_HARD_CEILING": "500"}):
            tmux, logs, state = self._go(1000, initial_captured=MR_BUSY_CAP)
        self.assertIn("/compact", tmux.typed_texts())

    # ------------------------------------------------------------------- #
    # #72 (2026-07-26 live incident, gatekeeper pane 0:0.0) -- QUEUED state
    # machine: exactly ONE `/compact` per ceiling-trigger, then WAIT --
    # regardless of elapsed time or how long the current turn runs. The
    # ONLY two exits from "queued" are CONSUMED (context confirmed dropped
    # -- a REAL compaction landed) and FAILED (the delivery target session
    # is demonstrably gone -- a DIFFERENT, newer session now owns that cwd,
    # i.e. the pane went through a restart) -- never a timer, never an
    # attempt cap, never a permanent give-up while still above the ceiling.
    #
    # Live evidence job 17 must never reproduce again: it sent THREE
    # `/compact` into gatekeeper's pane 0:0.0 over 12 minutes while a
    # SINGLE turn ran for 1h14m, logged each as "compacted" (only keystrokes
    # were sent, nothing was consumed), then GAVE UP while context kept
    # climbing 306900 -> 308250 -> 311408 -> 323K with three duplicate,
    # never-consumed `/compact` sitting in the pane's own queue.
    # ------------------------------------------------------------------- #

    def test_queued_still_above_ceiling_never_resends_no_matter_how_long(self):
        # the exact #72 bug: a session STILL busy in the SAME long turn must
        # NEVER get a second /compact, no matter how much time passes.
        # #83 -- needs a live "proc" fingerprint: a proc-less entry now
        # resolves (and re-enables sending) on its very first evaluation.
        now = time.time()
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD,
                            "proc": _alive_proc_fingerprint(self)}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP,
                                     state=state, now=now)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

        # a full hour later -- still busy, still above ceiling: still nothing.
        now2 = now + 3600
        tmux2, logs2, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                       initial_captured=MR_BUSY_CAP,
                                       state=state, now=now2)
        self.assertEqual(tmux2.sent, [])
        self.assertFalse(any("GAVE UP" in ln for ln in logs2), logs2)
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    def test_never_gives_up_across_many_sweeps_while_above_ceiling(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        sid = "sess-noresign"
        state = {}
        sent = []

        def fake_send(body, **kw):
            sent.append((body, kw))
            return "sent"

        # #83 -- RestartFakeTmux can never resolve a real proc fingerprint
        # (its `display-message` fake returns a bogus pane pid), so the
        # entry sweep 0 sends would otherwise be "proc"-less and resolve
        # again on sweep 1 -- patched to a genuinely alive process so this
        # still proves persistence exactly like a real production send.
        alive_proc = _alive_proc_fingerprint(self)
        with unittest.mock.patch.object(wd, "_pane_claude_proc_fingerprint",
                                        return_value=alive_proc):
            for i in range(5):
                now_i = now + i * 10 ** 6
                _seed_context_transcript(proj, self.CWD, sid, wd.COMPACT_HARD_CEILING + 1,
                                         idle_s=0, now=now_i)
                tmux_i = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
                logs_i = wd.compact_hard_ceiling(now_i, tmux_i, state, dry_run=False,
                                                 projects_dir=proj, send_fn=fake_send)
                if i == 0:
                    self.assertEqual(tmux_i.typed_texts().count("/compact"), 1, logs_i)
                else:
                    self.assertEqual(tmux_i.sent, [], logs_i)
                self.assertFalse(any("GAVE UP" in ln for ln in logs_i), logs_i)
        self.assertEqual(sent, [])   # never pinged a give-up -- there is none
        self.assertEqual(state["compact_ceiling"][sid]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    def test_send_time_log_never_claims_compacted(self):
        # #72's core complaint: "OK ... -> compacted" is misleading at send
        # time -- only KEYSTROKES were sent, nothing was verified yet.
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP)
        ok_lines = [ln for ln in logs if ln.startswith("OK (compact-ceiling)")]
        self.assertTrue(ok_lines, logs)
        self.assertFalse(any("compacted" in ln for ln in ok_lines), ok_lines)

    def test_queued_with_context_confirmed_low_is_consumed_and_cleared(self):
        # #83 -- needs a live "proc" fingerprint, or the #83 pre-pass drops
        # this entry (proc-less) BEFORE the main loop below ever reaches
        # the CONSUMED check this test is locking.
        now = time.time()
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD,
                            "proc": _alive_proc_fingerprint(self)}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING - 1, state=state, now=now)
        self.assertEqual(tmux.sent, [])
        self.assertNotIn(self.SID, state.get("compact_ceiling", {}))
        self.assertTrue(any(ln.startswith("CONSUMED (compact-ceiling)")
                            for ln in logs), logs)

    def test_lost_delivery_after_session_replaced_gets_exactly_one_resend(self):
        # #72 acceptance: "strata dorucenia -> jedno nove poslanie" -- the
        # pane's underlying session went through a restart (a DIFFERENT,
        # newer session now owns this cwd) while the old /compact was still
        # queued and unconsumed. This is the ONLY condition that legitimately
        # triggers a resend.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        old_sid, new_sid = "sess-old", "sess-new"
        _seed_context_transcript(proj, self.CWD, old_sid, wd.COMPACT_HARD_CEILING + 1,
                                 idle_s=100, now=now)
        # #83 -- needs a live "proc", or the #83 pre-pass drops this entry
        # as proc-less BEFORE it ever reaches the session-swap FAILED check
        # this test is locking (both end in "drop, resend" but with a
        # different log line -- STUCK vs FAILED).
        state = {"compact_ceiling":
                 {old_sid: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD,
                           "proc": _alive_proc_fingerprint(self)}}}
        # a NEW session's transcript is now the newest one for this cwd.
        _seed_context_transcript(proj, self.CWD, new_sid, wd.COMPACT_HARD_CEILING + 1,
                                 idle_s=0, now=now)
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
        logs = wd.compact_hard_ceiling(now, tmux, state, dry_run=False,
                                       projects_dir=proj)
        self.assertTrue(any(ln.startswith("FAILED (compact-ceiling)") for ln in logs), logs)
        self.assertEqual(tmux.typed_texts().count("/compact"), 1, logs)
        self.assertNotIn(old_sid, state.get("compact_ceiling", {}))
        self.assertEqual(state["compact_ceiling"][new_sid],
                         {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD})

    def test_intact_delivery_same_session_is_never_treated_as_lost(self):
        # the SAME sid still owns the cwd -- never mistaken for a restart,
        # even long after the send (false-positive-direction regression
        # lock for the #72 fix). #83 -- needs a live "proc" fingerprint, or
        # this entry is dropped as proc-less and gets a genuine resend.
        now = time.time()
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD,
                            "proc": _alive_proc_fingerprint(self)}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP,
                                     state=state, now=now + 10 ** 5)
        self.assertEqual(tmux.sent, [])
        self.assertFalse(any(ln.startswith("FAILED (compact-ceiling)") for ln in logs), logs)
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    # ------------------------------------------------------------------- #
    # #82 (2026-07-26 live incident, gatekeeper) -- a watchdog-driven
    # RESTART (`_restart_pane`, jobs 12/18) relaunches via `claude -c`,
    # which CONTINUES the SAME transcript -- the session id never changes,
    # so the #72 pre-pass's session-id-replace check above can NEVER fire
    # for it, and the claim wedges forever. The fix: this job's OWN
    # `compact_ceiling` entry now also carries the fingerprint of the
    # process the keystrokes were delivered to -- a process that no longer
    # exists (or a different one that has since reused its PID) is a
    # demonstrated delivery loss, independent of session id.
    # ------------------------------------------------------------------- #

    def test_process_death_with_unchanged_session_id_gets_exactly_one_resend(self):
        # the EXACT #82 incident: same sid (a `-c` restart never changes
        # it), but the process that held the queued keystrokes is gone.
        proc_handle = _spawn_dummy_proc(self)
        proc = wd._proc_fingerprint(proc_handle.pid)
        now = time.time()
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED,
                            "cwd": self.CWD, "proc": proc}}}
        proc_handle.terminate()
        proc_handle.wait(timeout=5)
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP,
                                     state=state, now=now)
        self.assertTrue(any(ln.startswith("FAILED (compact-ceiling)")
                            for ln in logs), logs)
        self.assertEqual(tmux.typed_texts().count("/compact"), 1, logs)
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    def test_process_alive_same_starttime_is_never_treated_as_lost(self):
        # false-positive-direction lock: a genuinely still-alive process
        # (matching starttime) must NEVER be mistaken for a restart, no
        # matter how long the send sat queued.
        proc_handle = _spawn_dummy_proc(self)
        proc = wd._proc_fingerprint(proc_handle.pid)
        now = time.time()
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED,
                            "cwd": self.CWD, "proc": proc}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP,
                                     state=state, now=now + 10 ** 5)
        self.assertEqual(tmux.sent, [])
        self.assertFalse(any(ln.startswith("FAILED (compact-ceiling)")
                            for ln in logs), logs)
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    def test_send_computes_and_threads_a_proc_fingerprint_into_the_shared_claim(self):
        # #82 -- job 17 is one of the four senders the fix must cover; it
        # resolves the fingerprint ONCE (to share with its own
        # `compact_ceiling` entry) and passes it through explicitly, rather
        # than leaving `compact_claim_set` to resolve it a second time —
        # lock that the `proc=` channel is actually used (pre-#82 this
        # call carried no such kwarg at all).
        with unittest.mock.patch.object(wd, "compact_claim_set") as claim_mock:
            self._go(wd.COMPACT_HARD_CEILING + 1, initial_captured=MR_BUSY_CAP)
        self.assertTrue(claim_mock.called)
        self.assertIn("proc", claim_mock.call_args.kwargs)

    # ------------------------------------------------------------------- #
    # #82 -- STUCK visibility: a claim that stays queued AND above the
    # ceiling for a long time must be LOGGED, not silently skipped forever
    # (the exact reason the live incident ran for HOURS before an
    # accident uncovered it).
    # ------------------------------------------------------------------- #

    def test_stuck_is_logged_after_threshold_cycles_still_queued(self):
        # #83 -- needs a live "proc": a proc-less entry is now dropped (and
        # logged as its OWN kind of STUCK line) on the very first
        # evaluation, before ever reaching this job's cycle counter.
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD,
                            "proc": _alive_proc_fingerprint(self)}}}
        now = time.time()
        logs = []
        for i in range(5):
            _tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                          initial_captured=MR_BUSY_CAP,
                                          state=state, now=now + i)
        # below the (default 30) threshold -- never logged as STUCK yet.
        self.assertFalse(any("STUCK" in ln for ln in logs), logs)
        self.assertEqual(state["compact_ceiling"][self.SID].get("cycles"), 5)

    def test_stuck_logs_every_sweep_once_threshold_is_crossed(self):
        # #83 -- needs a live "proc" (see the comment above).
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED,
                            "cwd": self.CWD, "cycles": 2,
                            "proc": _alive_proc_fingerprint(self)}}}
        now = time.time()
        _tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                      initial_captured=MR_BUSY_CAP,
                                      state=state, now=now, ceiling=None,
                                      stuck_cycles=3)
        self.assertTrue(any(ln.startswith("STUCK (compact-ceiling)")
                            for ln in logs), logs)
        self.assertEqual(state["compact_ceiling"][self.SID]["cycles"], 3)

    def test_below_stuck_threshold_never_logs_stuck(self):
        # #83 -- needs a live "proc" (see the comment above).
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED,
                            "cwd": self.CWD, "cycles": 0,
                            "proc": _alive_proc_fingerprint(self)}}}
        now = time.time()
        _tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                      initial_captured=MR_BUSY_CAP,
                                      state=state, now=now, stuck_cycles=30)
        self.assertFalse(any("STUCK" in ln for ln in logs), logs)

    def test_env_override_lowers_the_stuck_threshold(self):
        # #83 -- needs a live "proc" (see the comment above).
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED,
                            "cwd": self.CWD, "cycles": 1,
                            "proc": _alive_proc_fingerprint(self)}}}
        now = time.time()
        with unittest.mock.patch.dict(
                os.environ, {"AIRULESET_COMPACT_CEILING_STUCK_CYCLES": "2"}):
            _tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                          initial_captured=MR_BUSY_CAP,
                                          state=state, now=now)
        self.assertTrue(any("STUCK" in ln for ln in logs), logs)

    # ------------------------------------------------------------------- #
    # #69 acceptance: the `handled` set is what job 14/15 populate and job
    # 17 consults -- unit-level lock on the exact mechanism (the run_once
    # end-to-end version is RunOnceCompactHardCeilingWiring below).
    # ------------------------------------------------------------------- #

    def test_sid_already_in_handled_set_is_skipped_entirely(self):
        tmux, logs, state = self._go(
            wd.COMPACT_HARD_CEILING + 1, initial_captured=MR_IDLE_CAP)
        # re-run against the SAME transcript/sid but pre-mark it handled --
        # even though everything else looks perfectly eligible.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, self.SID,
                                 wd.COMPACT_HARD_CEILING + 1, idle_s=0, now=now)
        tmux2 = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
        state2 = {}
        wd.compact_hard_ceiling(now, tmux2, state2, dry_run=False,
                                projects_dir=proj, handled={self.SID})
        self.assertEqual(tmux2.sent, [])
        self.assertEqual(state2.get("compact_ceiling", {}), {})

    # ------------------------------------------------------------------- #
    # #78 (2026-07-26 live incident) — the SHARED /compact claim gate,
    # checked BEFORE this job's OWN #72 compact_ceiling state machine. A
    # claim already queued by ANOTHER sender (job 14, job 15, or the
    # synchronous #65 path) blocks job 17's send THIS sweep too, even
    # though this job's whole point is to back-stop a continuously-busy
    # session that no other job can touch.
    # ------------------------------------------------------------------- #

    def test_another_senders_queued_claim_blocks_job17s_send(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        now = time.time()
        _seed_context_transcript(proj, self.CWD, self.SID,
                                 wd.COMPACT_HARD_CEILING + 1, idle_s=0, now=now)
        # #83 -- a real claim always carries a live "proc" fingerprint; a
        # proc-less claim now resolves (re-enabling sending) on its very
        # first evaluation, so this test's claim needs one to stay blocking.
        wd.compact_claim_set(self.SID, self.CWD, proc=_alive_proc_fingerprint(self))
        tmux = RestartFakeTmux([(self.PANE, "claude", self.CWD)], MR_BUSY_CAP)
        state = {}
        wd.compact_hard_ceiling(now, tmux, state, dry_run=False,
                                projects_dir=proj)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(state.get("compact_ceiling", {}), {})

    # ------------------------------------------------------------------- #
    # #83 (2026-07-26 live incident, gatekeeper) — #82 added the process
    # fingerprint, but it never reaches a claim already written BEFORE #82
    # shipped: such an entry has NO "proc" key at all, so #82's own
    # process-death check is a no-op, and (for a `claude -c` restart, which
    # never changes the session id) neither can the session-id-replace
    # check ever fire. Live evidence: gatekeeper stayed queued 3.5h,
    # context 397010, zero compaction — it never even reached the STUCK
    # cycle counter below, because the SHARED compact_claim_active gate
    # (also proc-less) blocked this job's own state machine from ever
    # running. The fix: a proc-less QUEUED entry is dropped on the FIRST
    # evaluation instead — logged as STUCK for visibility, sending
    # re-enabled immediately.
    # ------------------------------------------------------------------- #

    def test_missing_proc_key_in_own_state_resolves_at_first_evaluation(self):
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP, state=state)
        self.assertTrue(any(ln.startswith("STUCK (compact-ceiling)")
                            for ln in logs), logs)
        self.assertTrue(any("no process fingerprint" in ln for ln in logs), logs)
        # dropped -> immediately eligible again -> resent THIS SAME sweep.
        self.assertIn("/compact", tmux.typed_texts())
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)

    def test_live_incident_both_structures_proc_less_unblocks_in_one_cycle(self):
        # reproduces the EXACT live gatekeeper finding: BOTH the shared
        # compact-claims.json AND this job's OWN state['compact_ceiling']
        # carried a proc-less entry for the same session. A single sweep
        # must drop both and resend.
        p = wd.compact_claims_path()   # isolated by setUp's _isolate_compact_claims
        wd.compact_claim_set(self.SID, self.CWD, path=p)   # no proc
        state = {"compact_ceiling":
                 {self.SID: {"status": wd.COMPACT_CEILING_QUEUED, "cwd": self.CWD}}}
        tmux, logs, state = self._go(wd.COMPACT_HARD_CEILING + 1,
                                     initial_captured=MR_BUSY_CAP, state=state)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertEqual(state["compact_ceiling"][self.SID]["status"],
                         wd.COMPACT_CEILING_QUEUED)


class RunOnceCompactHardCeilingWiring(unittest.TestCase):
    """Job 17 is ALWAYS wired (no gating param) -- run_once must invoke it
    every sweep, best-effort, entirely independent of job 14's request file
    and job 15's idle-only threshold."""

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def test_qualifying_busy_session_fires_through_run_once(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        now = time.time()
        cwd = "/home/newlevel/devel/ceiling-demo"
        _seed_context_transcript(proj, cwd, "sess-x", wd.COMPACT_HARD_CEILING + 1,
                                 idle_s=0, now=now)
        # cmd="node" so run_once's OWN by_transcript loop (list_claude_panes
        # only matches "claude") skips it -- isolates this to job 17's wiring,
        # same pattern as test_target_model_wires_into_model_reconcile.
        tmux = RestartFakeTmux([("%1", "node", cwd)], MR_BUSY_CAP)
        logs = wd.run_once(now=now, dry_run=False, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertTrue(any(ln.startswith("OK (compact-ceiling)") for ln in logs), logs)
        self.assertIn("/compact", tmux.typed_texts())

    def test_no_qualifying_session_produces_no_compact_ceiling_logs(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"))
        self.assertFalse(any("compact-ceiling" in ln for ln in logs), logs)

    def test_ticket_boundary_fires_first_and_ceiling_never_double_fires(self):
        # #69 regression lock: a session that DOES report a ticket boundary
        # (job 14's own mechanism, unchanged and primary) must be handled by
        # job 14 -- and job 17 (the backstop) must NOT also independently
        # fire /compact for the SAME session in the SAME sweep, even though
        # its context sits above COMPACT_HARD_CEILING too.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        state_path = Path(tmp) / "state.json"
        reqs_path = Path(tmp) / "compact-requests.json"
        now = time.time()
        cwd = "/home/newlevel/devel/ticket-boundary-demo"
        sid = "sess-boundary"
        # context strictly BETWEEN the hard ceiling and job 15's own idle
        # threshold, so job 15 never even looks at this pane (isolates the
        # assertion to job 14 vs job 17).
        ctx = wd.COMPACT_HARD_CEILING + 1
        self.assertLess(ctx, wd.COMPACT_CONTEXT_THRESHOLD)
        _seed_context_transcript(proj, cwd, sid, ctx, idle_s=0, now=now)
        reqs_path.write_text(json.dumps({sid: {"cwd": cwd, "ts": int(now)}}))
        # cmd="claude" so job 14 (which reuses run_once's own panes_by_sid,
        # built only for "claude" panes) actually sees this session.
        tmux = RestartFakeTmux([("%1", "claude", cwd)], MR_IDLE_CAP)
        logs = wd.run_once(now=now, dry_run=False, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           compact_requests_path=reqs_path)
        self.assertTrue(any(ln.startswith("OK (compact-request)") for ln in logs), logs)
        self.assertFalse(any(ln.startswith("OK (compact-ceiling)") for ln in logs), logs)
        self.assertEqual(tmux.typed_texts().count("/compact"), 1, tmux.typed_texts())
        # the request was consumed by job 14 -- never left pending
        self.assertEqual(wd.load_compact_requests(reqs_path), {})


if __name__ == "__main__":
    unittest.main()
