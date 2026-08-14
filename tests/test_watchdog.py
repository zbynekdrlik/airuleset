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
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

import burn
import watchdog as wd


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


def _user_toolresult():
    """A `tool_result`-carrying "user" entry — pairs with `_assistant_tooluse()`
    to represent a tool call that actually RETURNED, i.e. genuine progress
    (#287's `_subagent_transcript_unsalvageable` bar is 0 COMPLETED tool
    calls, not 0 issued ones — an issued tool_use with no matching
    tool_result is exactly as un-investigable as none at all)."""
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "content": "ok"}]}}


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

    def test_counted_variant_also_normalizes(self):
        # #176 item 4: CC also renders a COUNTED form of the same hint
        # ("Press up to edit 2 queued messages") once more than one message
        # is queued — the exact-equality check at :796 missed this shape
        # entirely and misread it as a real held draft (the verdict's own
        # reproduction table: `_find_boundary_line` returned it un-normalized
        # while the singular/uncounted form normalized fine).
        cap = "● hotovo\n❯ Press up to edit 2 queued messages\n  ctx ░\n"
        self.assertEqual(wd._find_boundary_line(cap), "❯")
        self.assertEqual(wd._input_line_text(cap), "")
        self.assertEqual(wd._classify_boundary(cap), ("input", ""))
        self.assertTrue(wd.pane_at_idle_prompt(cap))

    def test_a_draft_starting_with_the_real_prefix_but_with_extra_words_is_still_a_draft(self):
        # #176 REOPENED F8: `test_a_genuine_draft_mentioning_similar_words_is_still_a_draft`
        # above starts "press up LATER..." — it never even starts with the real
        # "press up to edit" prefix, so it gives ZERO protection against a future
        # widening of `_QUEUED_PLACEHOLDER_RX` to something like
        # `press up to edit.*queued messages` (a `.*` in place of the tight
        # `(?:\s+\d+)?\s+`). THIS fixture starts with the genuine prefix and
        # inserts real words before "queued messages" — a real draft that a `.*`
        # variant would wrongly swallow and discard as the empty-box placeholder.
        cap = ("● hotovo\n❯ press up to edit the config, then flush queued "
               "messages\n  ctx ░\n")
        self.assertEqual(
            wd._input_line_text(cap),
            "press up to edit the config, then flush queued messages")
        self.assertFalse(wd.pane_at_idle_prompt(cap))
        self.assertEqual(
            wd._classify_boundary(cap),
            ("input", "press up to edit the config, then flush queued messages"))


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

    # --- #172 carried over from #175/#176's own closing pass: the WEEKLY
    # cap banner names an explicit CALENDAR DATE ahead of the clock
    # ("resets Jul 31, 9pm"), not just a bare time-of-day. The old regex
    # required a digit immediately after "resets "/"resets at ", so this
    # form matched `is_usage_cap` (bounded, #175 F2) but `parse_reset_epoch`
    # returned None — job 6 could ping once but never compute a resume
    # instant, so it never auto-resumed even once the real reset passed.
    def test_dated_weekly_reset_parses_the_named_date(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Prague")
        now = datetime(2026, 7, 26, 10, 0, tzinfo=tz).timestamp()
        epoch = wd.parse_reset_epoch(
            "You've hit your weekly limit · resets Jul 31, 9pm (Europe/Prague)",
            now)
        self.assertIsNotNone(epoch)
        got = datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(got, "2026-07-31 21:00")

    def test_dated_reset_far_in_the_past_this_year_rolls_to_next_year(self):
        # "resets Jan 2, ..." seen in December must mean NEXT January, not a
        # date that already passed 11 months ago this year.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Prague")
        now = datetime(2026, 12, 20, 10, 0, tzinfo=tz).timestamp()
        epoch = wd.parse_reset_epoch(
            "You've hit your weekly limit · resets Jan 2, 9am (Europe/Prague)",
            now)
        self.assertIsNotNone(epoch)
        got = datetime.fromtimestamp(epoch, tz).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(got, "2027-01-02 09:00")

    def test_dated_reset_does_not_break_the_existing_time_only_forms(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 8, 0, tzinfo=tz).timestamp()
        for banner, expect in (
                ("resets 11:20pm (Europe/Prague)", "23:20"),
                ("resets 12pm (Europe/Prague)", "12:00"),
                ("resets 11am (Europe/Prague)", "11:00"),
                ("resets at 18:10 (Europe/Prague)", "18:10")):
            epoch = wd.parse_reset_epoch(banner, now)
            self.assertIsNotNone(epoch, "banner %r" % banner)
            got = datetime.fromtimestamp(epoch, tz).strftime("%H:%M")
            self.assertEqual(got, expect, "banner %r" % banner)

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

    # --- #175 F2: the weekly and bare cap banners — no "session"/"usage"
    # qualifier word at all, real Claude Code shapes.
    WEEKLY_LIMIT_BANNER = (
        "❯ continue\n"
        "  ⎿  You've hit your weekly limit · resets 12pm (Europe/Prague)\n\n❯ ")
    BARE_LIMIT_BANNER = (
        "❯ continue\n"
        "  ⎿  You've hit your limit · resets 11am (Europe/Prague)\n\n❯ ")

    def test_weekly_and_bare_banner_match(self):
        # The old regex only recognized "session"/"usage" before "limit" —
        # the real weekly-cap and bare-cap banners use NEITHER qualifier
        # word, so they used to fall through to job 1's generic nudge path
        # (continue every ~30 min for the whole cap window) instead of job
        # 6's bounded ping-once-then-wait-for-reset treatment.
        self.assertTrue(wd.pane_session_limited(self.WEEKLY_LIMIT_BANNER))
        self.assertTrue(wd.pane_session_limited(self.BARE_LIMIT_BANNER))


class ResetTimeParseRegressions(unittest.TestCase):
    """(#183) Six correctness findings in the widened reset-time parse the
    #172 livelock ticket shipped (`_RESET_TIME_RX`, `parse_reset_epoch`,
    `_human_clock`) — three ready-made reproductions from the ticket plus
    the acceptance criteria's own required regressions."""

    def _epoch(self, y, mo, d, h, mi=0, tz_name="Europe/Prague"):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz_name)).timestamp()

    def _hhmm(self, epoch, tz_name="Europe/Prague"):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(epoch, ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")

    # --- finding 1: an unrecognised month word must return None, never fall
    # through to the bare-clock branch (which would silently reuse TODAY's
    # date with this banner's clock — an epoch DAYS too early).
    def test_unrecognized_month_word_returns_none(self):
        now = self._epoch(2026, 7, 26, 10)
        epoch = wd.parse_reset_epoch(
            "resets Thu 31, 9pm (Europe/Prague)", now)
        self.assertIsNone(
            epoch, "an unrecognised month word must return None, got %r"
            % (self._hhmm(epoch) if epoch else epoch))

    # --- finding 2: a 4-digit year must not be absorbed into the hour group
    # (one hour early is the reproduced case) — and the three shapes that
    # were ALREADY fail-safe before the #172 widening must stay that way.
    def test_four_digit_year_no_longer_corrupts_the_hour(self):
        now = self._epoch(2026, 7, 26, 10)
        epoch = wd.parse_reset_epoch("resets Jul 31, 2026 9pm", now)
        self.assertIsNone(
            epoch, "a 4-digit year must not be absorbed into the hour "
            "group, got %r" % (self._hhmm(epoch) if epoch else epoch))

    def test_previously_fail_safe_malformed_shapes_still_return_none(self):
        now = self._epoch(2026, 7, 26, 10)
        for banner in ("resets 31 Jul",                 # reversed order
                       "resets Jul 31, 26 9pm",          # 2-digit year
                       "resets Feb 30, 9pm"):            # invalid calendar day
            epoch = wd.parse_reset_epoch(banner, now)
            self.assertIsNone(epoch, "banner %r must stay fail-safe (None), "
                              "got %r" % (banner, epoch))

    # --- finding 3: the parse must be bottom-scoped exactly like the
    # detector (`pane_session_limited`) — a STALE echo higher on screen must
    # never beat a FRESHER banner lower down.
    def test_stale_dated_echo_above_a_fresh_banner_prefers_the_fresh_one(self):
        cap = ("resets Jul 29, 9pm (Europe/Prague)\n"
               "● pokracujem v praci\n"
               "● pokracujem v praci\n"
               "resets Aug 6, 9pm (Europe/Prague)\n❯ \n")
        now = self._epoch(2026, 8, 3, 10)
        epoch = wd.parse_reset_epoch(cap, now)
        self.assertIsNotNone(epoch)
        self.assertEqual(self._hhmm(epoch), "2026-08-06 21:00",
                         "expected the FRESH (bottom) banner to win, got "
                         "the stale one instead")

    # --- findings 4/5: a dated target slightly stale (including a small
    # negative delta -- "the reset already happened") is returned AS-IS,
    # never rolled a whole year forward for a merely-hours-old banner; only
    # staleness beyond a realistic weekly-cap cycle means "next year". 20h
    # stale is deliberately PAST the bare-clock branch's OWN 6h window (the
    # exact width the round-2 fix mistakenly reused for the dated branch
    # too) but comfortably inside a weekly cap's real cycle -- a specimen
    # that actually discriminates the fix from the pre-#183 6h behaviour,
    # not one both old and new code happen to agree on.
    def test_dated_target_recently_past_returns_as_is_no_year_jump(self):
        now = self._epoch(2026, 8, 1, 17)      # 20h after the 21:00 target
        epoch = wd.parse_reset_epoch("resets Jul 31, 9pm", now)
        self.assertIsNotNone(epoch)
        self.assertLessEqual(epoch, now)
        self.assertEqual(self._hhmm(epoch), "2026-07-31 21:00")

    def test_dated_target_stale_beyond_the_weekly_grace_rolls_forward(self):
        now = self._epoch(2026, 8, 20, 10)      # ~3 weeks after the target
        epoch = wd.parse_reset_epoch("resets Jul 31, 9pm", now)
        self.assertIsNotNone(epoch)
        self.assertEqual(self._hhmm(epoch), "2027-07-31 21:00")

    # --- finding 6: a reset several days out must not read as "tonight".
    def test_human_clock_renders_the_date_when_not_today(self):
        now = self._epoch(2026, 8, 3, 10)
        future = self._epoch(2026, 8, 6, 21)
        self.assertEqual(wd._human_clock(future, now=now), "06.08 21:00")

    def test_human_clock_stays_bare_hhmm_for_todays_reset(self):
        now = self._epoch(2026, 8, 3, 10)
        today = self._epoch(2026, 8, 3, 21)
        self.assertEqual(wd._human_clock(today, now=now), "21:00")


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

    def test_dated_reset_ping_uses_run_onces_own_now_not_real_wall_clock(self):
        """(adversarial review of this batch's own #183 diff) job 6's ping
        text renders the reset time via `_human_clock`, which formats
        differently depending on whether the reset falls on "today" --
        that comparison must use run_once's OWN `now` parameter, never the
        REAL wall clock silently read behind the scenes. A weekly-cap
        banner ("resets Jul 31, 9pm", no day-of-week qualifier -- job 6's
        dated branch) with `now` fixed to that SAME calendar date must
        render the bare 'HH:MM' form (today's reset), regardless of what
        the real world's current date happens to be when this test runs."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Prague")
        now = datetime(2026, 7, 31, 10, 0, tzinfo=tz).timestamp()
        capture = ("❯ continue\n"
                  "  ⎿  You've hit your weekly limit · resets Jul 31, 9pm "
                  "(Europe/Prague)\n\n❯ ")
        logs, sent, keys, _ = self._harness(now, capture=capture)
        self.assertTrue(sent, "expected a session-limit ping: %r" % logs)
        self.assertTrue(
            any("Reset o 21:00." in b for b in sent),
            "the SAME calendar date must render the bare HH:MM form, not "
            "DD.MM HH:MM (which would mean it compared against the REAL "
            "wall clock instead of run_once's own `now`): %r" % sent)


class ParseResetEpochFromErrorText(unittest.TestCase):
    """(#336) The SAME clock/timezone-parsing `parse_reset_epoch` runs on a
    captured PANE, exposed directly over a plain error-message STRING (job
    1's own `transcript_last_error()` output) -- no pane/box scoping needed,
    since a transcript's `isApiErrorMessage` text is already just the
    message, with no agent-strip/ANSI chrome to strip first. This is what
    lets a session-limit hit that never renders its banner on the live pane
    at all (a background Agent/subagent dying on the account's 5h limit,
    #336's own montalu2 incident) still get a resume time parked from the
    error TEXT itself."""

    def test_matches_the_pane_based_parse_for_the_same_clock_text(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()
        text = "You've hit your session limit · resets 6:10pm (Europe/Prague)"
        pane_epoch = wd.parse_reset_epoch(text, now)
        text_epoch = wd.parse_reset_epoch_from_error_text(text, now)
        self.assertIsNotNone(text_epoch)
        self.assertEqual(pane_epoch, text_epoch)

    def test_the_montalu2_incidents_own_error_text(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Prague")
        now = datetime(2026, 8, 8, 15, 0, tzinfo=tz).timestamp()
        text = ('Agent "Implement Task 9: E2E + CHANGELOG + gates" failed: '
                'Agent terminated early due to an API error: You\'ve hit '
                'your session limit · resets 8pm (Europe/Prague)')
        epoch = wd.parse_reset_epoch_from_error_text(text, now)
        self.assertIsNotNone(epoch)
        got = datetime.fromtimestamp(epoch, tz).strftime("%H:%M")
        self.assertEqual(got, "20:00")

    def test_missing_clock_returns_none(self):
        self.assertIsNone(
            wd.parse_reset_epoch_from_error_text("You've hit your session limit", 0))

    def test_empty_or_missing_text_returns_none(self):
        self.assertIsNone(wd.parse_reset_epoch_from_error_text("", 0))
        self.assertIsNone(wd.parse_reset_epoch_from_error_text(None, 0))


class SessionUserStoppedPredicate(unittest.TestCase):
    """(#336) The narrow, session-limit-scoped counterpart of #335's own
    (not-yet-landed) general user-stop invariant: a session the user
    explicitly told to stop (`/exit`) since a limit episode began must
    never be auto-resumed by delivering `continue`, even once the parked
    reset time has passed."""

    CWD = "/home/newlevel/devel/exit-test"
    SID = "exit-test-sid"

    def _tpath(self, entries):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, entries)
        return tpath

    def _exit_entry(self, ts_iso):
        return {"type": "user", "timestamp": ts_iso,
                "message": {"role": "user",
                           "content": "<command-name>/exit</command-name>"}}

    def test_no_exit_command_at_all_returns_false(self):
        tpath = self._tpath([_assistant("pracujem…")])
        self.assertFalse(wd.session_user_stopped(tpath))

    def test_exit_command_present_returns_true(self):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        tpath = self._tpath([_assistant("pracujem…"), self._exit_entry(ts)])
        self.assertTrue(wd.session_user_stopped(tpath))

    def test_exit_before_since_ts_is_not_counted(self):
        from datetime import datetime, timezone
        old_ts = datetime.fromtimestamp(1_000_000, timezone.utc).isoformat()
        tpath = self._tpath([self._exit_entry(old_ts), _assistant("pracujem…")])
        self.assertFalse(wd.session_user_stopped(tpath, since_ts=2_000_000))

    def test_exit_after_since_ts_is_counted(self):
        from datetime import datetime, timezone
        newer_ts = datetime.fromtimestamp(3_000_000, timezone.utc).isoformat()
        tpath = self._tpath([self._exit_entry(newer_ts)])
        self.assertTrue(wd.session_user_stopped(tpath, since_ts=2_000_000))

    def test_missing_transcript_fails_safe_to_false(self):
        self.assertFalse(wd.session_user_stopped(
            Path("/nonexistent/path/does-not-exist-336.jsonl")))

    def test_other_slash_commands_are_not_mistaken_for_exit(self):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        tpath = self._tpath([{"type": "user", "timestamp": ts,
                              "message": {"role": "user",
                                         "content": "<command-name>/compact</command-name>"}}])
        self.assertFalse(wd.session_user_stopped(tpath))

    def test_real_transcript_exit_marker_shape_is_detected(self):
        # (adversarial review of this ticket's own fix, F1 — CRITICAL) Claude
        # Code writes a real `/exit` entry as a COMPOSITE string, never the
        # bare marker this predicate's own fixtures elsewhere in this file
        # use — verified live against real transcripts on this box:
        #   <command-name>/exit</command-name>\n            <command-message>
        #   exit</command-message>\n            <command-args></command-args>
        # A strict `content.strip() == "<command-name>/exit</command-name>"`
        # equality check NEVER matches this real shape, so the whole
        # user-stop safety gate was inert against every genuine `/exit` a
        # user ever types — the exact harm class (auto-resuming a session
        # the user explicitly stopped) this predicate exists to prevent.
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        real_exit_content = (
            "<command-name>/exit</command-name>\n"
            "            <command-message>exit</command-message>\n"
            "            <command-args></command-args>")
        tpath = self._tpath([{"type": "user", "timestamp": ts,
                              "message": {"role": "user",
                                         "content": real_exit_content}}])
        self.assertTrue(wd.session_user_stopped(tpath),
                        "must detect the REAL composite /exit shape, not "
                        "only the bare marker no real transcript ever "
                        "actually writes")

    def test_exit_foo_lookalike_command_name_does_not_false_match(self):
        # The fix for the finding above widens the match from strict
        # equality to a PREFIX check -- must not become so loose that an
        # unrelated command sharing the "/exit" substring false-matches.
        # The closing tag is part of the required prefix, so a DIFFERENT
        # command name (never a real Claude Code shape, but the fix must
        # not accidentally accept it) is correctly refused.
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        tpath = self._tpath([{"type": "user", "timestamp": ts,
                              "message": {"role": "user",
                                         "content": "<command-name>/exit-foo</command-name>"}}])
        self.assertFalse(wd.session_user_stopped(tpath))


NO_BANNER_IDLE_PANE = "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"


def _assistant_usage_cap(text):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class SessionLimitTranscriptSeeded(unittest.TestCase):
    """(#336) A session-limit error that hits a BACKGROUND Agent/subagent —
    or otherwise never renders Claude Code's banner as the pane's own
    bottom-most content — must still auto-resume after its reset. Job 1's
    OWN transcript-based detection (`isApiErrorMessage` + `is_usage_cap`)
    now seeds job 6's `sesslimit:<key>` tracking straight from the ERROR
    TEXT, so the resume no longer depends on `pane_session_limited` ever
    becoming True on a pane that may never show the banner at all — the
    real montalu2 incident: idle at a bare prompt, no banner visible,
    nothing auto-resumed until a human typed `continue` by hand at 20:40,
    forty minutes after the 20:00 reset."""

    CWD = "/home/newlevel/devel/montalu2"
    PANE = "%3"
    SID = "9a8b7c6d-0000-4000-8000-0000000336aa"

    def _harness(self, now, entries, capture=NO_BANNER_IDLE_PANE, state_path=None,
                age_s=700):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, entries)
        os.utime(tpath, (now - age_s, now - age_s))
        if state_path is None:
            state_path = Path(tmp.name) / "state.json"
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
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           grace=300, interval=300, max_nudges=3)
        return logs, sent, keys, state_path, tpath

    def test_job1_seeds_sesslimit_state_from_transcript_alone(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()   # before 18:10 reset
        entries = [_assistant_usage_cap(
            'Agent "Implement Task 9" failed: Agent terminated early due to '
            "an API error: You've hit your session limit · resets 6:10pm "
            "(Europe/Prague)")]
        logs, sent, keys, sp, _ = self._harness(now, entries)
        self.assertEqual(keys, [], "no continue may be sent before the reset")
        self.assertEqual(len(sent), 1, "expected job 1's own single usage-cap ping")
        st = json.loads(Path(sp).read_text())
        self.assertIn("sesslimit:" + self.SID, st,
                      "job 1 must park a resume time straight from the error "
                      "TEXT -- the pane never shows the banner at all")
        self.assertIsNotNone(st["sesslimit:" + self.SID]["resets_at"])

    def test_auto_resumes_after_reset_even_though_the_pane_never_showed_the_banner(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now_before = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()
        now_after = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()   # past 18:10
        entries = [_assistant_usage_cap(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)")]
        # first sweep: job 1 seeds the parked resume time (pane never shows the banner)
        _, sent1, keys1, sp, _ = self._harness(now_before, entries)
        # second sweep, well past the reset: job 6 must pick up the parked
        # episode purely from state and deliver `continue`, with no pane
        # banner ever involved.
        _, sent2, keys2, _, _ = self._harness(now_after, entries, state_path=sp)
        self.assertEqual(keys1, [], "no continue before the reset")
        self.assertTrue(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys2),
            "expected exactly one `continue` after reset, with the pane never "
            "showing the banner at all: %r" % keys2)

    def test_user_exit_since_the_limit_hit_blocks_auto_resume(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()   # past 18:10
        first_seen = now - 3600
        exit_iso = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
        entries = [_assistant_usage_cap(
                       "You've hit your session limit · resets 6:10pm (Europe/Prague)"),
                   {"type": "user", "timestamp": exit_iso,
                    "message": {"role": "user",
                               "content": "<command-name>/exit</command-name>"}}]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": False,
            "attempts": 0, "first_seen": int(first_seen), "last_seen": int(now - 60)}}
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        logs, sent, keys, _, _ = self._harness(now, entries, capture=SESSION_LIMIT_BANNER,
                                               state_path=sp)
        self.assertEqual(keys, [], "must NEVER auto-resume a session the user /exit'd "
                                   "since the limit hit: %r" % keys)
        st = json.loads(sp.read_text())
        self.assertNotIn("sesslimit:" + self.SID, st,
                         "tracking must be dropped once the user-stop invariant fires")

    def test_resets_at_refines_from_transcript_not_pane_when_banner_absent(self):
        # (adversarial review of this ticket's own fix, F3) An earlier poll
        # could not parse a resume time (`resets_at is None`) -- the
        # refinement attempt must read whichever surface THIS episode's own
        # evidence actually comes from: the transcript for a transcript-only
        # episode, never the live pane (which, for a transcript-only
        # episode, never shows the banner at all -- refining from it would
        # only ever find whatever the session's own SUBSEQUENT, unrelated
        # reply happens to render near the bottom of the screen).
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()   # before 18:10 reset
        entries = [_assistant_usage_cap(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)")]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": None, "pinged": True, "continued": False,
            "attempts": 0, "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        logs, sent, keys, _, _ = self._harness(now, entries, state_path=sp)
        self.assertEqual(keys, [], "no continue before the (now-refined) reset")
        st = json.loads(sp.read_text())
        self.assertIsNotNone(
            st["sesslimit:" + self.SID]["resets_at"],
            "the refinement must have parsed the clock from the "
            "TRANSCRIPT's own error text, since the pane (NO_BANNER_IDLE_PANE) "
            "never carries a clock to find")

    def test_still_erroring_without_a_banner_keeps_retrying_bounded(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        entries = [_assistant_usage_cap(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)")]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": True,
            "attempts": 1, "last_try": now - 400,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        logs, sent, keys, _, _ = self._harness(now, entries, state_path=sp)
        self.assertTrue(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys),
            "still erroring (never resolved) and past the retry interval, "
            "with no pane banner ever involved -- expected a SECOND retry: "
            "%r" % keys)

    def test_tracking_clears_once_the_transcript_shows_recovery(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 20, tzinfo=tz).timestamp()
        entries = [_assistant_usage_cap(
                       "You've hit your session limit · resets 6:10pm (Europe/Prague)"),
                   _assistant("Hotovo, pokracujem.")]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 600, "pinged": True, "continued": True,
            "attempts": 1, "last_try": now - 120,
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        logs, sent, keys, _, _ = self._harness(now, entries, state_path=sp)
        self.assertEqual(keys, [],
                         "a genuinely recovered session must not get another `continue`")
        st = json.loads(sp.read_text())
        self.assertNotIn("sesslimit:" + self.SID, st,
                         "tracking must clear the moment the transcript shows "
                         "recovery, not leak forever (the pane never had a "
                         "banner to lose in the first place)")

    def test_job1_bare_uuid_tracking_survives_a_sweep_job6_owns(self):
        # (adversarial review finding 6b) Job 6 owning a parked episode
        # (via `continue` at the end of its own block) means job 1's code
        # never runs for THIS session on THIS sweep -- without
        # `stalled.add(key)`, the end-of-sweep generic cleanup pass would
        # prune job 1's own bare-UUID dormant-tracking entry every single
        # sweep job 6 manages, since it never gets a chance to protect
        # itself via its OWN `stalled.add(key)` call (which only runs when
        # job 1's own code path is reached).
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 15, 0, tzinfo=tz).timestamp()   # before 18:10 reset
        entries = [_assistant_usage_cap(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)")]
        seed = {
            self.SID: {"hash": "deadbeef0000", "first_seen": int(now - 3600),
                      "nudges": [], "escalated": True, "dormant": True},
            "sesslimit:" + self.SID: {
                "resets_at": now + 10000, "pinged": True, "continued": False,
                "attempts": 0, "first_seen": int(now - 3600),
                "last_seen": int(now - 60)},
        }
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        self._harness(now, entries, state_path=sp)
        st = json.loads(sp.read_text())
        self.assertIn(self.SID, st,
                     "job 6 owning this session's poll must not let job 1's "
                     "own bare-UUID tracking entry get pruned by the "
                     "end-of-sweep cleanup pass")

    def test_transcript_only_episode_state_persists_across_multiple_sweeps(self):
        # (adversarial review finding 6a) `last_seen` must be refreshed on
        # EVERY sweep job 6 manages, even for a transcript-only episode
        # that never shows the pane banner -- otherwise the end-of-sweep
        # generic cleanup pass (wait_clear=90s) would prune the parked
        # episode BETWEEN two SESSLIMIT_RETRY_S-spaced (300s) retry
        # sweeps, silently collapsing the whole bounded-retry mechanism
        # down to a single attempt.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now0 = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()   # past 18:10
        entries = [_assistant_usage_cap(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)")]
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now0 - 300, "pinged": True, "continued": False,
            "attempts": 0, "first_seen": int(now0 - 3600),
            "last_seen": int(now0 - 60)}}
        tmp2 = TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        sp = Path(tmp2.name) / "state.json"
        sp.write_text(json.dumps(seed))
        now = now0
        for _ in range(3):
            self._harness(now, entries, state_path=sp)
            now += wd.SESSLIMIT_RETRY_S
        st = json.loads(sp.read_text())
        self.assertIn("sesslimit:" + self.SID, st,
                     "tracking must survive across SESSLIMIT_RETRY_S-spaced "
                     "sweeps -- last_seen must be refreshed every sweep, "
                     "not just at creation")
        self.assertEqual(
            st["sesslimit:" + self.SID]["attempts"], 3,
            "expected exactly 3 delivered retries across the 3 sweeps: %r"
            % st["sesslimit:" + self.SID])


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


def _pane_list_fake_run(pane_cwd_pairs, cap="● Predošlá práca hotová.\n❯ \n"):
    """(#172) A minimal tmux-shim fake covering only what the sweep-budget
    tests need: a fixed multi-pane `list-panes` response (in the given
    order, so `by_transcript`'s insertion order -- and therefore which
    session the budget check reaches first -- is deterministic), an idle
    `capture-pane` for every pane, and harmless answers for the small
    handful of `display-message`/`send-keys` calls every session touches."""
    def fake_run(argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "".join("%s\tclaude\t%s\n" % (pid, cwd) for pid, cwd in pane_cwd_pairs)
        if "capture-pane" in j:
            return cap
        if "display-message" in argv[0:2] or "display-message" in j:
            if "pane_in_mode" in j:
                return "0"
            if "session_group" in j or argv[-1] == "#S":
                return "zbynek"
            return ""
        if "send-keys" in j:
            return ""
        return ""
    return fake_run


def _fixed_calls_time_fn(values, default=999.0):
    """(#172) A `time_fn` stub returning each of `values` in order, then
    `default` forever after -- lets a test script the EXACT sequence of
    wall-clock reads run_once's sweep-budget check makes (one for the
    deadline, one per loop-top check) without depending on real timing."""
    calls = iter(values)

    def time_fn():
        return next(calls, default)
    return time_fn


class RunOnceSweepWallClockBudget(unittest.TestCase):
    """(#172) Live evidence on dev1: the sweep is still occasionally
    SIGTERM-killed by systemd's TimeoutStartSec=120 (~4/24h, self-recovering
    next tick, never the original 7h+ livelock) with NOT ONE log line
    printed before the kill -- meaning a slow tick can still overrun well
    before jobs 27/28's own already-bounded per-repo work (their cadence
    markers were progressing normally the whole time this was observed). The
    per-transcript pane loop must self-bound against wall clock and exit
    gracefully -- printing what it decided so far and reaching this sweep's
    own trailing save_state() -- instead of relying on systemd's external
    kill, which loses whatever the tick hadn't reached AND prints nothing."""

    CWD_A = "/home/newlevel/devel/proj-a"
    CWD_B = "/home/newlevel/devel/proj-b"
    PANE_A = "%1"
    PANE_B = "%2"

    def test_budget_exceeded_stops_processing_remaining_sessions(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"

        enc_a = wd.encode_project_dir(self.CWD_A)
        (proj / enc_a).mkdir(parents=True)
        tpath_a = proj / enc_a / "aaaaaaaa.jsonl"
        _write_jsonl(tpath_a, [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT), _system()])

        enc_b = wd.encode_project_dir(self.CWD_B)
        (proj / enc_b).mkdir(parents=True)
        tpath_b = proj / enc_b / "bbbbbbbb.jsonl"
        _write_jsonl(tpath_b, [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT), _system()])

        now = time.time()
        idle_seconds = 600
        os.utime(tpath_a, (now - idle_seconds, now - idle_seconds))
        os.utime(tpath_b, (now - idle_seconds, now - idle_seconds))

        state_path = Path(tmp.name) / "state.json"
        fake_run = _pane_list_fake_run([(self.PANE_A, self.CWD_A), (self.PANE_B, self.CWD_B)])

        # 1st call: sweep_deadline = 0.0 + 10 = 10.0. 2nd call: the loop's
        # idx=0 check for session A (0.0 < 10.0 -> processed). 3rd call: the
        # loop's idx=1 check for session B (50.0 >= 10.0 -> break, never
        # processed this tick).
        time_fn = _fixed_calls_time_fn([0.0, 0.0, 50.0])

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           time_fn=time_fn, sweep_budget_s=10)

        self.assertTrue(
            any(ln.startswith("sweep-budget-exceeded") for ln in logs),
            "expected a sweep-budget-exceeded log line once the wall-clock "
            "budget is spent, got: %r" % logs)
        self.assertTrue(
            any(ln.startswith("textcall-nudge#1") for ln in logs),
            "expected session A (processed BEFORE the budget tripped) to "
            "still be handled, got: %r" % logs)
        b_sid = tpath_b.stem
        self.assertFalse(
            any(b_sid in ln for ln in logs),
            "session B must show NO per-job processing this tick (the "
            "budget must have tripped before it was ever reached), got: %r"
            % logs)

    def test_skipped_session_keeps_its_existing_episode_state(self):
        """The identical #175 F1 hazard, reached through a different door:
        a session skipped by the wall-clock budget (rather than a
        busy-pane/copy-mode gate) must not have its job-1 api-error episode
        state silently wiped by the cleanup pass at the end of run_once."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"

        enc_a = wd.encode_project_dir(self.CWD_A)
        (proj / enc_a).mkdir(parents=True)
        tpath_a = proj / enc_a / "aaaaaaaa.jsonl"
        _write_jsonl(tpath_a, [_assistant("hello, all good here")])

        sid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        enc_b = wd.encode_project_dir(self.CWD_B)
        (proj / enc_b).mkdir(parents=True)
        tpath_b = proj / enc_b / (sid_b + ".jsonl")
        _write_jsonl(tpath_b, [_assistant_apierror()])

        now = time.time()
        idle_seconds = 600
        os.utime(tpath_a, (now - idle_seconds, now - idle_seconds))
        os.utime(tpath_b, (now - idle_seconds, now - idle_seconds))

        err_hash = wd._hash("API Error: 529 Overloaded")
        state_path = Path(tmp.name) / "state.json"
        seeded = {sid_b: {"hash": err_hash, "first_seen": int(now - idle_seconds),
                          "nudges": [int(now - 400)], "escalated": False}}
        wd.save_state(state_path, seeded)

        fake_run = _pane_list_fake_run([(self.PANE_A, self.CWD_A), (self.PANE_B, self.CWD_B)])
        time_fn = _fixed_calls_time_fn([0.0, 0.0, 50.0])

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           time_fn=time_fn, sweep_budget_s=10)

        self.assertTrue(any(ln.startswith("sweep-budget-exceeded") for ln in logs),
                        "expected the budget to trip, got: %r" % logs)
        saved = json.loads(state_path.read_text())
        self.assertIn(
            sid_b, saved,
            "session B's job-1 episode state must survive being skipped by "
            "the wall-clock budget -- saved keys: %r" % list(saved.keys()))
        self.assertEqual(
            len(saved[sid_b].get("nudges", [])), 1,
            "nudge history must be UNCHANGED for a session the budget "
            "skipped this tick, got: %r" % saved.get(sid_b))

    def test_a_nonpositive_env_budget_does_not_disable_the_sweep(self):
        """(adversarial review of this batch's own #172 diff) A `<= 0`
        AIRULESET_SWEEP_BUDGET_S (0, or a negative value -- both parse as
        valid ints, so the ValueError fallback never catches them) would
        set the deadline to now-or-earlier, tripping the very FIRST
        loop-top check and skipping EVERY session on EVERY sweep forever --
        silently disabling jobs 1/2/4/4a/5/6/7/9/10 fleet-wide, exactly the
        opposite of what this fix exists to do. Must clamp to the default
        instead, exactly like `_repo_sweep_batch`'s own `max_repos <= 0`."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc_a = wd.encode_project_dir(self.CWD_A)
        (proj / enc_a).mkdir(parents=True)
        tpath_a = proj / enc_a / "aaaaaaaa.jsonl"
        _write_jsonl(tpath_a, [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT), _system()])
        now = time.time()
        os.utime(tpath_a, (now - 600, now - 600))
        fake_run = _pane_list_fake_run([(self.PANE_A, self.CWD_A)])

        for bad in (0, -5):
            with self.subTest(sweep_budget_s=bad):
                # a FRESH state file per value -- reusing one across
                # iterations means the first (correctly clamped) run seeds
                # textcall: state, and the second run's "nudge" then
                # legitimately reads as "wait" (retry-interval not yet
                # elapsed), which is a TEST bug, not a production one.
                state_path = Path(tmp.name) / ("state-%s.json" % bad)
                logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                                   send_fn=lambda *a, **k: None,
                                   projects_dir=proj, state_path=state_path,
                                   pending_prefix=str(Path(tmp.name) / "pending-"),
                                   sweep_budget_s=bad)
                self.assertFalse(
                    any(ln.startswith("sweep-budget-exceeded") for ln in logs),
                    "a non-positive sweep_budget_s must clamp to the "
                    "default, not disable the sweep entirely: %r" % logs)
                self.assertTrue(
                    any(ln.startswith("textcall-nudge#1") for ln in logs),
                    "session A must still be processed with the clamped "
                    "default budget, got: %r" % logs)


class RunOnceTailBudgetForJobs8And20(unittest.TestCase):
    """#255 (adversarial review, MAJOR finding), re-verified for the #403
    goal.py collapse: jobs 8/20 must NOT receive the bare `sweep_deadline`
    used by the per-transcript pane loop -- that deadline is scoped to the
    pane loop alone (which runs BEFORE jobs 8/20) and can legitimately
    already be exhausted by the time jobs 8/20 run, silently zeroing their
    entire ~30s margin exactly when a real backlog is most likely to exist
    (measured live: 26 of 3837 sweeps over 3 days exceeded the pane loop's
    own 90s budget). They get `sweep_deadline + TAIL_BUDGET_S` instead,
    still comfortably under the 120s systemd hard kill.

    #403 STEP 0 explicitly required this collapse's own timing plumbing to
    respect #172's sweep_deadline/tail_deadline mechanism -- job 9's own
    `goal_sweep` is bounded by the tiny pending-arm-request count (not by
    the box's pane count) and does not need it, but job 20's TWO halves
    (`goal_dark_watch`, `goal_lane_sweep`) both walk EVERY live candidate
    pane, the identical unbounded-by-repo-count shape `bounce_backstop`
    already guards -- so both get the SAME `time_fn`/`sweep_deadline`
    contract `bounce_backstop` has carried since #255."""

    def test_bounce_backstop_and_job20_both_halves_get_the_extended_tail_deadline(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        proj.mkdir(parents=True)
        state_path = Path(tmp.name) / "state.json"

        captured = {}

        def bounce_probe(*a, **kw):
            captured["bounce_deadline"] = kw.get("sweep_deadline")
            return []

        def dark_watch_probe(*a, **kw):
            captured["dark_watch_deadline"] = kw.get("sweep_deadline")
            return []

        def lane_sweep_probe(*a, **kw):
            captured["lane_sweep_deadline"] = kw.get("sweep_deadline")
            return []

        from watchdog import goal as _goal_mod

        budget = 10
        with unittest.mock.patch.object(wd, "bounce_backstop",
                                        side_effect=bounce_probe), \
             unittest.mock.patch.object(_goal_mod, "goal_dark_watch",
                                        side_effect=dark_watch_probe), \
             unittest.mock.patch.object(_goal_mod, "goal_lane_sweep",
                                        side_effect=lane_sweep_probe):
            wd.run_once(now=time.time(), dry_run=False,
                       run=lambda *a, **k: "",
                       send_fn=lambda *a, **k: None, projects_dir=proj,
                       state_path=state_path,
                       pending_prefix=str(Path(tmp.name) / "pending-"),
                       time_fn=lambda: 0.0, sweep_budget_s=budget,
                       bounce_fetch=lambda root: [],
                       goal_jobs_enabled=True)

        expected = 0.0 + budget + wd.TAIL_BUDGET_S
        self.assertEqual(captured.get("bounce_deadline"), expected,
                         "bounce_backstop must get the EXTENDED tail "
                         "deadline, not the bare pane-loop sweep_deadline")
        self.assertEqual(captured.get("dark_watch_deadline"), expected,
                         "goal_dark_watch must get the EXTENDED tail "
                         "deadline, not the bare pane-loop sweep_deadline")
        self.assertEqual(captured.get("lane_sweep_deadline"), expected,
                         "goal_lane_sweep must get the EXTENDED tail "
                         "deadline, not the bare pane-loop sweep_deadline")
        self.assertLess(expected, 120,
                        "the tail deadline must stay comfortably under "
                        "the 120s systemd hard kill")

    def test_goal_requests_path_reaches_job_9(self):
        # #403 (successor to the deleted #320 shape 2 — job 9 no longer
        # resolves templates during a sweep at all; the SKILL.md text is
        # resolved once, at `goal-arm --self` CLI time). What still needs
        # threading through `run_once` is the PENDING-REQUESTS store path
        # (`goal_requests_path`, the renamed run_once param) reaching
        # `goal_sweep`'s own `requests_path` kwarg.
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        proj.mkdir(parents=True)
        state_path = Path(tmp.name) / "state.json"
        captured = {}

        def goal_sweep_probe(*a, **kw):
            captured["requests_path"] = kw.get("requests_path")
            return []

        from watchdog import goal as _goal_mod

        with unittest.mock.patch.object(_goal_mod, "goal_sweep",
                                        side_effect=goal_sweep_probe):
            wd.run_once(now=time.time(), dry_run=False,
                       run=lambda *a, **k: "",
                       send_fn=lambda *a, **k: None, projects_dir=proj,
                       state_path=state_path,
                       pending_prefix=str(Path(tmp.name) / "pending-"),
                       goal_jobs_enabled=True,
                       goal_requests_path="/tmp/goal-requests.json")
        self.assertEqual(captured.get("requests_path"),
                         "/tmp/goal-requests.json")


class RunOnceSubagentVisibility(unittest.TestCase):
    """(issue #6) run_once must apply job 1's api-error detector AND job 4a's
    text-toolcall-stall detector to the newest subagents/*.jsonl too, not just the
    SUPERVISOR transcript — so a dying BACKGROUND WORKER (e.g. an autopilot-worker)
    is caught fast (idle pane → a targeted nudge naming the worker; busy pane →
    ping-only, never a keystroke) instead of waiting up to ~30 min for job 4's
    indirect subagent_active() mtime path."""

    def setUp(self):
        # (#449 gate-run live catch — pre-existing, reproduced against the
        # pre-#449 code too) run_once with dry_run=False wires
        # reping_stale_questions against notify's REAL question map: a
        # GENUINE production ❓ on this box crossing its 24h re-ask
        # boundary mid-test injected an unexpected re-ask ping into this
        # class's fake send_fn, failing the pings3==[] lock. Sandbox the
        # map (the grace store derives from the same dirname) so the class
        # only ever sees its own state.
        import notify
        tmpq = TemporaryDirectory()
        self.addCleanup(tmpq.cleanup)
        qp = str(Path(tmpq.name) / "discord-questions.json")
        p = unittest.mock.patch.object(notify, "_questions_path", lambda: qp)
        p.start()
        self.addCleanup(p.stop)

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
                           pending_prefix=str(Path(proj).parent / "pending-"),
                           # hermetic: without this, the daily question-reping
                           # sweep reads the LIVE ~/.claude/discord-questions.json
                           # and a real pending question on the box leaks an
                           # extra ping into these assertions (observed live:
                           # the ping-count check below flaked from 1 to 2 the
                           # moment a real question crossed its reping bucket).
                           questions_path=str(Path(proj).parent
                                              / "questions.json"))
        return logs, sent, pings

    def test_question_reping_reads_only_the_injected_questions_path(self):
        # The passthrough itself must be real: a stale entry in the INJECTED
        # map fires a reping ping through run_once; the live box map is never
        # consulted. (Pre-fix this failed with TypeError: unexpected keyword
        # 'questions_path' — run_once parameterized every other state source
        # but read the questions map from the live default.)
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.")], [_assistant("ok")],
            sup_age=10, sub_age=10)
        qpath = Path(tmp) / "questions.json"
        qpath.write_text(json.dumps({
            "123456": {"session": "s-x", "cwd": "/tmp/qproj",
                       "ts": now - 2 * 24 * 3600,
                       "block": "**Otázka — projekt qproj:**\ntest?\n❓ NEEDS YOU: test?"},
        }))
        pings = []

        def fake_run(argv, timeout=8):
            return ""

        def fake_send(body, **k):
            pings.append((body, k))
            return ("sent", "m-1")

        # This test exercises the injected-questions-path PASSTHROUGH, not the
        # 00:00-05:59 Europe/Bratislava sleep window that reping_stale_questions
        # defers a re-ask past (#368). `now` is wall-clock (`time.time()`), so
        # whenever the suite ran overnight the reping deferred and this
        # assertion flaked to 0 (#457 — the batch-15 blocker); the grace store
        # is a red herring, already sandboxed by setUp's `_questions_path`
        # patch. Pin the sleep gate OFF so the reping-due path is exercised
        # deterministically at any wall-clock time.
        with unittest.mock.patch.object(
                wd, "_in_sleep_window", lambda *a, **k: False):
            wd.run_once(now=now, dry_run=False, run=fake_run, send_fn=fake_send,
                        projects_dir=proj, state_path=Path(tmp) / "state.json",
                        pending_prefix=str(Path(tmp) / "pending-"),
                        questions_path=str(qpath))
        reping = [p for p in pings
                  if (p[1] or {}).get("dedup_key", "").startswith("question-reping:")]
        self.assertEqual(len(reping), 1,
                         "the injected stale entry must fire exactly one "
                         "reping ping: %r" % pings)
        self.assertIn("projekt qproj", reping[0][0])

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

    # --- #287: nudge repeats forever on an already-triaged dead subagent -------

    def test_subagent_apierror_unsalvageable_transcript_nudges_at_most_once(self):
        # (#287) A subagent transcript that never issued a single tool_use
        # call and ends on a bare api-error has NOTHING left to
        # investigate — exactly the odoo-erp#3036 shape (4 lines total, the
        # dispatch's own tool_use never landed, last line a bare
        # `API Error: 529 Overloaded`; _assistant_apierror() with no
        # preceding tool_use fixture is that shape). Such a transcript is
        # nudged AT MOST ONCE, ever — never the full nudge x3 + escalate
        # cycle a genuinely-recoverable stall earns, and never a repeated
        # identical nudge on later sweeps.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")],
            [_assistant_apierror()],
            sup_age=10, sub_age=400)
        state_path = Path(tmp) / "state.json"
        logs1, sent1, pings1 = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertTrue(any(ln.startswith("subagent-apierr-nudge#1") for ln in logs1),
                        "expected the one allowed nudge, got: %r" % logs1)
        literal_nudges1 = [a for a in sent1 if "-l" in a]
        self.assertEqual(len(literal_nudges1), 1, sent1)

        # a LATER sweep (well within SUBAGENT_MAX_AGE_SECONDS, comfortably
        # past RETRY_INTERVAL_SECONDS since the first nudge) must NOT type a
        # second keystroke — at most a passive escalate PING (decide_working's
        # own one-shot give-up), never a repeat of the typed `stuck-check:`
        # nudge, which is what actually costs the session a paid turn.
        now2 = now + 600
        logs2, sent2, pings2 = self._run(proj, now2, state_path, self.IDLE_CAP)
        self.assertEqual(sent2, [], "must NOT send a second keystroke nudge — "
                                    "already-nudged/unsalvageable: %r" % logs2)
        self.assertTrue(any(ln.startswith("subagent-apierr-escalate") for ln in logs2),
                        "expected decide_working's one-shot give-up to fire "
                        "here (max_nudges capped at 1): %r" % logs2)
        self.assertEqual(len(pings2), 1, "the escalate ping fires exactly once: %r" % pings2)
        # (#287 adversarial-review MINOR) The unsalvageable escalate must NOT
        # claim "session nereaguje na nudge" (not responding) -- it fires
        # after just ONE nudge, often the very next sweep, with no time to
        # respond and often nothing to respond about.
        self.assertNotIn("nereaguje na nudge", pings2[0][0])

        # a THIRD sweep still must not repeat either — neither the typed
        # nudge NOR the escalate ping (decide_working's own `escalated` flag
        # makes every later evaluation a permanent noop).
        now3 = now2 + 600
        logs3, sent3, pings3 = self._run(proj, now3, state_path, self.IDLE_CAP)
        self.assertEqual(sent3, [], "must stay permanently silent (keystrokes) "
                                    "after the single unsalvageable nudge: %r" % logs3)
        self.assertEqual(pings3, [], "the one-shot escalate ping must not repeat "
                                     "either: %r" % pings3)

    def test_subagent_apierror_nudge_state_survives_supervisor_marker_gap(self):
        # (#287, corrected root cause) The per-worker nudge/escalate dedup
        # state used for a SALVAGEABLE dying subagent (decide_working's own
        # 3-nudge cycle) must NOT be wiped just because the SUPERVISOR's own
        # marker briefly reads something other than `⏳ WORKING` — a busy
        # /goal loop working OTHER tickets routinely produces `✅ DONE`/`❓`
        # turns for minutes at a stretch between `⏳` sightings, far longer
        # than the generic episode-cleanup's WAIT_CLEAR_SECONDS (90s).
        # Before this fix, that gap alone reset the nudge counter to zero on
        # every re-sighting, so a genuinely-recoverable worker's nudge#1
        # fired again and again instead of ever accumulating toward its own
        # 3-nudge escalation — the exact "identical nudge delivered
        # forever" shape #287 reports. (#287's OWN stated cause —
        # decide_working's `responded=True` exponential-backoff branch —
        # does NOT apply here: `_nudge_dying_subagent` never passes
        # `responded=` at all, so that branch is structurally UNREACHABLE
        # for this wkey; this test reproduces the REAL mechanism instead —
        # verified against the current source before writing this fix.)
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        now1 = time.time()
        tpath = proj / enc / (self.SID + ".jsonl")
        subdir = proj / enc / self.SID / "subagents"
        subdir.mkdir(parents=True)
        spath = subdir / (self.WORKER + ".jsonl")
        # a SALVAGEABLE worker — it made real progress (a tool call that
        # actually RETURNED, i.e. a genuine tool_use/tool_result pair) before
        # dying, so it earns the FULL nudge/nudge/nudge/escalate cycle, not
        # the (separately-tested) unsalvageable at-most-once path. A bare
        # ISSUED tool_use with no tool_result would NOT qualify here —
        # `_subagent_transcript_unsalvageable`'s bar is 0 COMPLETED tool
        # calls, matching the ticket's own stated incident shape.
        _write_jsonl(spath, [_assistant_tooluse(), _user_toolresult(), _assistant_apierror()])
        death_mtime = now1 - 400        # > GRACE_SECONDS (300)
        os.utime(spath, (death_mtime, death_mtime))
        state_path = Path(tmp) / "state.json"

        # sweep 1: supervisor is `⏳ WORKING` -> nudge #1.
        _write_jsonl(tpath, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")])
        os.utime(tpath, (now1, now1))
        logs1, sent1, pings1 = self._run(proj, now1, state_path, self.IDLE_CAP)
        self.assertTrue(any(ln.startswith("subagent-apierr-nudge#1") for ln in logs1), logs1)

        # sweep 2: the supervisor moved on to OTHER work for well over
        # WAIT_CLEAR_SECONDS — the marker gate is closed this whole sweep,
        # so the wkey's last_seen is NOT refreshed; this sweep's own
        # end-of-poll cleanup pass is what decides whether the state
        # survives the gap.
        now2 = now1 + wd.WAIT_CLEAR_SECONDS + 30
        _write_jsonl(tpath, [_assistant("✅ DONE: iný ticket hotový.")])
        os.utime(tpath, (now2, now2))
        logs2, sent2, pings2 = self._run(proj, now2, state_path, self.IDLE_CAP)
        self.assertEqual(sent2, [], "supervisor is DONE, not ⏳ — must not type "
                                    "anything this sweep: %r" % logs2)

        # sweep 3: the supervisor is back on `⏳ WORKING` (a later ticket's
        # own background dispatch) — with enough elapsed since nudge#1 for a
        # SECOND nudge to be due (interval=RETRY_INTERVAL_SECONDS).
        now3 = now1 + wd.RETRY_INTERVAL_SECONDS + 20
        _write_jsonl(tpath, [_assistant("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")])
        os.utime(tpath, (now3, now3))
        logs3, sent3, pings3 = self._run(proj, now3, state_path, self.IDLE_CAP)
        self.assertTrue(any(ln.startswith("subagent-apierr-nudge#2") for ln in logs3),
                        "state must have SURVIVED the supervisor's marker gap and "
                        "accumulated to nudge#2, not reset to nudge#1 again: %r" % logs3)
        self.assertFalse(any(ln.startswith("subagent-apierr-nudge#1") for ln in logs3),
                         "a repeated nudge#1 here means the dedup state was wiped "
                         "and the SAME nudge fired again from scratch: %r" % logs3)


class SubagentTranscriptUnsalvageable(unittest.TestCase):
    """(#287, adversarial-review MAJOR finding) The classifier's bar is
    "0 COMPLETED tool calls" — matching the reporting incident's own stated
    shape verbatim (odoo-erp#3036: "1 tool_use — the dispatch itself, 0
    completed tool calls") — not "0 tool_use ever ISSUED", which would
    wrongly classify that exact incident's own worker as salvageable."""

    def _classify(self, entries):
        with TemporaryDirectory() as d:
            p = Path(d) / "sub.jsonl"
            _write_jsonl(p, entries)
            return wd._subagent_transcript_unsalvageable(p)

    def test_the_real_incidents_own_shape_is_unsalvageable(self):
        # one ISSUED tool_use, never returned (no tool_result before the
        # fatal error) — the odoo-erp#3036 shape verbatim.
        self.assertTrue(self._classify([_assistant_tooluse(), _assistant_apierror()]))

    def test_zero_tool_use_at_all_is_unsalvageable(self):
        self.assertTrue(self._classify([_assistant_apierror()]))

    def test_a_genuinely_completed_tool_call_is_salvageable(self):
        # a real tool_use/tool_result PAIR — actual progress was made.
        self.assertFalse(self._classify(
            [_assistant_tooluse(), _user_toolresult(), _assistant_apierror()]))

    def test_a_normal_non_error_ending_is_never_unsalvageable(self):
        self.assertFalse(self._classify([_assistant_tooluse(), _assistant("Hotovo.")]))

    def test_empty_transcript_fails_safe_salvageable(self):
        self.assertFalse(self._classify([]))


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


# --------------------------------------------------------------------------- #
# Shared single-pane fixtures. These were introduced for job 12 (MODEL
# RECONCILE, #42); that job and its two siblings — 18 and 23 — were REMOVED in
# #132 after their restart helper typed `/exit` into a session the user was
# working in. What survives here is only what other jobs still use: the idle
# capture, the transcript seeder, and the fake tmux. Every fixture that existed
# solely to drive a restart (busy/dialog/draft/bg-agent/strip-selected/resume
# captures, the target-model and settings-hash helpers) went with the jobs.
# --------------------------------------------------------------------------- #

MR_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


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
    """Generic single-pane fake `run`. Built for job 12's restart sequence
    (#42); that job and its two siblings were removed in #132, but jobs 20/22
    and the run_once wiring tests still use this fake as a plain pane stub, so
    it stays (renaming it would churn every call site for no gain).
    `initial_captured` is what `capture-pane` returns for the very FIRST call —
    the guard-check frame a job's main loop reads before deciding whether to
    act at all. Every capture-pane call AFTER that first one pulls from
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


class TestBurnSnapshotJob(unittest.TestCase):
    # #269 review finding m3: burn_snapshot_job -> hourly_snapshot() now
    # reads the LOCAL usage cache for account_email; with no isolation these
    # tests would read this box's own REAL ~/.claude/airuleset-usage-
    # cache.json (machine-dependent, and the exact real-file coupling this
    # repo's own compact_claims_path/notify._claude_dir precedent forbids).
    # Patching burn.usage_cache_path (the function load_usage_cache() falls
    # back to when no explicit path is given) isolates every test in this
    # class with zero call-site changes.
    def setUp(self):
        patcher = unittest.mock.patch.object(
            burn, "usage_cache_path",
            return_value=Path(tempfile_mkdtemp_cleanup(self)) / "no-usage-cache.json")
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_account_email_is_isolated_from_the_real_local_cache(self):
        # Proves the setUp isolation genuinely takes effect (not just "tests
        # still pass") -- without it this row would carry whatever real
        # account_email is in THIS box's own ~/.claude/airuleset-usage-
        # cache.json, which is non-deterministic across boxes/developers.
        tmp = tempfile_mkdtemp_cleanup(self)
        transcripts = Path(tmp) / "projects"
        transcripts.mkdir()
        snap_path = Path(tmp) / "snapshots.jsonl"
        wd.burn_snapshot_job(time.time(), {}, snapshot_path=snap_path,
                             transcripts_root=str(transcripts), host="dev1")
        row = json.loads(snap_path.read_text().strip().splitlines()[0])
        self.assertEqual(row["account_email"], "")

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
    """run_once must actually invoke its wired jobs, best-effort, and must NOT
    change behavior for a caller that passes none of the optional params.

    Jobs 12/18/23 (the three restart jobs) were REMOVED in #132 — their
    wiring tests went with them, and the first test here is now the inverse
    guard: a sweep must never emit a restart line at all."""

    def test_a_sweep_never_reports_a_session_restart(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        proj.mkdir()
        # A live-looking pane that the removed jobs 12/18/23 would each have
        # picked up (node/claude foreground, resolvable transcript, idle at a
        # bare prompt). Nothing may propose restarting it.
        _seed_transcript(proj, "/home/newlevel/devel/demo", "sess-x",
                         "claude-fable-5")
        state_path = Path(tmp) / "state.json"
        tmux = RestartFakeTmux(
            [("%1", "node", "/home/newlevel/devel/demo")], MR_IDLE_CAP)
        logs = wd.run_once(now=time.time(), dry_run=True, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           burn_snapshot_path=Path(tmp) / "snap.jsonl")
        for needle in ("model-reconcile", "hooks-reconcile",
                       "model-gen-reconcile", "restart"):
            self.assertFalse(
                any(needle in ln for ln in logs),
                "a sweep proposed %r — jobs 12/18/23 were removed in #132: %s"
                % (needle, logs))
        # …and it must not have typed anything into the pane either.
        self.assertEqual(tmux.sent, [],
                         "a dry sweep sent keystrokes: %s" % (tmux.sent,))

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


class TestStaleExecMarkerCleanup(unittest.TestCase):
    """Job 22 (#97): block-main-implementation.sh's one-shot bypass markers
    (/tmp/airuleset-main-exec-ok-<sid>, legacy -fable- form too) are consumed
    on use, but a session that ends without another guarded call never
    consumes its own marker -- it just sits in /tmp forever (a real one
    found on gk: 0 bytes, ~21h old, no matching session anywhere). Cleanup
    must require BOTH: old enough, AND no live pane's transcript stem still
    matches the session id -- a live session's marker must survive no
    matter how old the file looks (it may be a long-running deliberate
    exception), and a dead session's marker must go once past the age
    threshold."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        proj_tmp = TemporaryDirectory()
        self.addCleanup(proj_tmp.cleanup)
        self.projects_dir = proj_tmp.name

    def _marker(self, sid, age_s, legacy=False, tmp_dir=None):
        name = ("airuleset-fable-exec-ok-%s" if legacy
                else "airuleset-main-exec-ok-%s") % sid
        p = Path(tmp_dir or self.tmp_dir, name)
        p.write_text("")
        mtime = time.time() - age_s
        os.utime(p, (mtime, mtime))
        return p

    def test_old_marker_with_no_live_session_is_removed(self):
        sid = "dead-" + os.urandom(4).hex()
        marker = self._marker(sid, age_s=7 * 3600)
        logs = wd.cleanup_stale_exec_markers(
            time.time(), run=RestartFakeTmux([], "n/a"),
            projects_dir=self.projects_dir, tmp_dir=self.tmp_dir)
        self.assertFalse(marker.exists(), "an orphaned marker must be removed")
        self.assertTrue(any("exec-marker-cleanup" in ln for ln in logs), logs)

    def test_fresh_marker_is_left_alone(self):
        sid = "fresh-" + os.urandom(4).hex()
        marker = self._marker(sid, age_s=60)
        wd.cleanup_stale_exec_markers(
            time.time(), run=RestartFakeTmux([], "n/a"),
            projects_dir=self.projects_dir, tmp_dir=self.tmp_dir)
        self.assertTrue(marker.exists(), "too fresh to be an orphan yet")

    def test_old_marker_of_a_still_live_session_is_never_removed(self):
        sid = "live-" + os.urandom(4).hex()
        cwd = str(Path(self.tmp_dir) / "devel" / "proj")
        Path(cwd).mkdir(parents=True)
        proj = Path(self.projects_dir) / wd.encode_project_dir(cwd)
        proj.mkdir(parents=True)
        (proj / (sid + ".jsonl")).write_text(
            json.dumps({"type": "assistant", "message": {"content": "hi"}}) + "\n")
        marker = self._marker(sid, age_s=7 * 3600)     # old, but session is LIVE
        tmux = RestartFakeTmux([("%1", "claude", cwd)], "n/a")
        logs = wd.cleanup_stale_exec_markers(
            time.time(), run=tmux, projects_dir=self.projects_dir,
            tmp_dir=self.tmp_dir)
        self.assertTrue(marker.exists(),
                        "a live session's marker must never be revoked "
                        "mid-work, no matter its age")
        self.assertFalse(logs, logs)

    def test_legacy_fable_marker_name_is_also_cleaned(self):
        sid = "dead2-" + os.urandom(4).hex()
        marker = self._marker(sid, age_s=7 * 3600, legacy=True)
        wd.cleanup_stale_exec_markers(
            time.time(), run=RestartFakeTmux([], "n/a"),
            projects_dir=self.projects_dir, tmp_dir=self.tmp_dir)
        self.assertFalse(marker.exists())

    def test_unrelated_tmp_files_are_untouched(self):
        other = Path(self.tmp_dir, "airuleset-main-exec-block.log")
        other.write_text("unrelated log\n")
        mtime = time.time() - 7 * 3600
        os.utime(other, (mtime, mtime))
        wd.cleanup_stale_exec_markers(
            time.time(), run=RestartFakeTmux([], "n/a"),
            projects_dir=self.projects_dir, tmp_dir=self.tmp_dir)
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
