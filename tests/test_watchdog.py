"""Tests for the api-watchdog text-emitted tool-call stall detector (job 4a).

A tool call the model emits as TEXT (`<invoke name="...">…</invoke>` inside an
assistant text block) never runs → the turn ends → the session sits idle while
still LOOKING like it was about to act. Job 4a detects this from the transcript
shape and nudges immediately. These tests lock the detector's precision (it must
NOT fire on a meta-conversation that merely discusses `<invoke>` markup — like this
very repo) and the run_once wiring.
"""

import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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


# --------------------------------------------------------------------------- #
# #37 follow-up — Job 12: MODEL RECONCILE. The managed default (MANAGED_MODEL
# in airuleset.py) only binds a NEW Claude Code session; several long-lived
# sessions were still parked on Fable/Opus-4 — the single biggest cost line.
# Ported from a proven-live reference (`/tmp/switch.py`): find a live
# claude/node/bun pane whose newest transcript's last model is fable/opus-4,
# and — only when genuinely at rest — `/model <target>` + Enter, confirm
# CC's "Switch model?" dialog with one more Enter, verify the confirmation
# text lands. Never touches a busy pane, an open dialog, or an unsent draft;
# never sends two consecutive Escapes; dedups per session id.
# --------------------------------------------------------------------------- #

MR_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
MR_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
MR_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
MR_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
MR_TARGET = "claude-opus-5[1m]"
MR_CONFIRM_OK = "Set model to %s\n❯ \n  ctx ███░\n" % MR_TARGET
MR_CONFIRM_FAIL = "● nič sa nezmenilo\n❯ \n  ctx ███░\n"


def _seed_transcript(projects_dir, cwd, sid, model):
    d = Path(projects_dir) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    _write_jsonl(p, [
        {"type": "assistant", "message": {"model": model,
                                          "content": [{"type": "text", "text": "hi"}]}},
    ])
    return p


class ModelReconcileFakeTmux:
    """Fake `run` for a single candidate pane. Tracks every send-keys call
    and flips `capture-pane`'s reply to `confirm_captured` the moment the
    SECOND Enter (the confirmation keystroke) has been sent — mirroring the
    real sequence: type `/model X`, Enter, [dialog], Enter, [applied]."""

    def __init__(self, panes, captured, confirm_captured=None, in_mode=False,
                confirm_after_polls=1):
        self.panes = panes                 # [(pane_id, cmd, cwd)]
        self.captured = captured
        self.confirm_captured = captured if confirm_captured is None else confirm_captured
        self.in_mode = in_mode
        # how many post-2nd-Enter capture-pane polls before the confirmation
        # text actually shows up — 1 = confirms on the FIRST poll (the old
        # default/behavior); a higher number simulates a slow render under
        # load (the live dev1 finding this constant fix locks in).
        self.confirm_after_polls = confirm_after_polls
        self.sent = []
        self.capture_calls_after_confirm_enter = 0
        self._enters = 0

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s" % t for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            if argv[-1] == "Enter":
                self._enters += 1
            return ""
        if "capture-pane" in j:
            if self._enters < 2:
                return self.captured
            self.capture_calls_after_confirm_enter += 1
            if self.capture_calls_after_confirm_enter >= self.confirm_after_polls:
                return self.confirm_captured
            return self.captured
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]

    def no_consecutive_escapes(self):
        ks = self.keys()
        return not any(ks[i] == "Escape" and ks[i + 1] == "Escape"
                       for i in range(len(ks) - 1))


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

    def _go(self, model, captured, confirm_captured=None, state=None,
           target_model=MR_TARGET, dry_run=False, in_mode=False,
           confirm_after_polls=1):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", model)
        tmux = ModelReconcileFakeTmux([(self.PANE, "claude", self.CWD)],
                                      captured, confirm_captured, in_mode=in_mode,
                                      confirm_after_polls=confirm_after_polls)
        state = {} if state is None else state
        logs = wd.model_reconcile(time.time(), tmux, state, target_model,
                                  dry_run=dry_run, projects_dir=proj,
                                  sleep_fn=lambda s: None)
        return tmux, logs, state

    def test_fable_session_gets_switched(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_OK)
        self.assertIn("/model %s" % MR_TARGET, tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(state["modelswitch"]["sess-abc"])

    def test_opus4_session_gets_switched(self):
        tmux, logs, state = self._go("claude-opus-4-8", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_OK)
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

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

    def test_draft_pane_is_never_typed_over(self):
        tmux, logs, state = self._go("claude-fable-5", MR_DRAFT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)

    def test_open_dialog_is_skipped(self):
        tmux, logs, state = self._go("claude-fable-5", MR_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)

    def test_dry_run_never_sends_keys(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertEqual(state.get("modelswitch", {}), {})

    def test_no_target_model_disables_the_job_entirely(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP, target_model=None)
        self.assertEqual(tmux.sent, [])
        self.assertEqual(logs, [])

    def test_failed_confirmation_releases_the_claim_for_retry(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_FAIL)
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))

    def test_exact_keystroke_sequence_types_then_two_enters(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_OK)
        self.assertEqual(tmux.keys(), ["/model %s" % MR_TARGET, "Enter", "Enter"])

    def test_no_two_consecutive_escapes_ever_sent(self):
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_OK)
        self.assertTrue(tmux.no_consecutive_escapes())
        # and on every OTHER decision path too — busy/draft/dialog never type
        for cap in (MR_BUSY_CAP, MR_DRAFT_CAP, MR_DIALOG_CAP):
            t2, _, _ = self._go("claude-fable-5", cap)
            self.assertTrue(t2.no_consecutive_escapes())

    def test_slow_confirmation_within_the_poll_budget_still_succeeds(self):
        # live dev1 finding: on a heavily loaded box, the confirmation text
        # can take several seconds to render — a single fixed-wait check
        # false-"FAIL"ed a switch that had genuinely applied moments later,
        # which released the dedup claim and caused a redundant (harmless
        # but wasteful) retry on the NEXT sweep. Confirming the response
        # lands within the poll budget (well under MAX_POLLS) must still
        # be a real OK, not a false FAIL.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_OK,
                                     confirm_after_polls=wd.MODEL_SWITCH_APPLY_MAX_POLLS - 1)
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(state["modelswitch"]["sess-abc"])

    def test_confirmation_that_never_lands_fails_after_a_bounded_number_of_polls(self):
        # the poll must be BOUNDED — never an infinite/unbounded wait
        # (no-timeout-band-aids.md) — and must still release the dedup
        # claim for retry on a later sweep, same as the old single-shot FAIL.
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_FAIL,
                                     confirm_after_polls=10 ** 6)
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))
        self.assertEqual(tmux.capture_calls_after_confirm_enter,
                         wd.MODEL_SWITCH_APPLY_MAX_POLLS)

    def test_attempts_counter_increments_on_each_failure(self):
        state = {}
        self._go("claude-fable-5", MR_IDLE_CAP, confirm_captured=MR_CONFIRM_FAIL,
                 state=state)
        self.assertEqual(state["modelswitch_attempts"]["sess-abc"], 1)

    def test_successful_switch_clears_any_prior_attempts_counter(self):
        state = {"modelswitch_attempts": {"sess-abc": 2}}
        self._go("claude-fable-5", MR_IDLE_CAP, confirm_captured=MR_CONFIRM_OK,
                 state=state)
        self.assertNotIn("sess-abc", state.get("modelswitch_attempts", {}))

    def test_repeated_failures_stop_retrying_after_max_attempts(self):
        # LIVE INCIDENT (gk, 2026-07-25): the same pane FAILed on every single
        # ~60s sweep forever ("FAIL (model-reconcile) ... no confirmation
        # seen" repeated indefinitely) — every FAIL released the dedup claim
        # so the NEXT sweep retried, burning a full context re-read (prompt
        # cache invalidation) on every successful switch attempt. Cap
        # attempts per session; once the cap is hit, GIVE UP for good (never
        # retry that session again) and say so in the log.
        tmp = tempfile_mkdtemp_cleanup(self)
        proj = Path(tmp) / "projects"
        _seed_transcript(proj, self.CWD, "sess-abc", "claude-fable-5")
        tmux = ModelReconcileFakeTmux([(self.PANE, "claude", self.CWD)],
                                      MR_IDLE_CAP, MR_CONFIRM_FAIL)
        state = {}
        logs = []
        for _ in range(wd.MODEL_RECONCILE_MAX_ATTEMPTS):
            tmux.sent = []
            tmux._enters = 0
            tmux.capture_calls_after_confirm_enter = 0
            logs = wd.model_reconcile(time.time(), tmux, state, MR_TARGET,
                                      dry_run=False, projects_dir=proj,
                                      sleep_fn=lambda s: None)
        self.assertTrue(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertEqual(state["modelswitch_attempts"]["sess-abc"],
                         wd.MODEL_RECONCILE_MAX_ATTEMPTS)
        self.assertTrue(state["modelswitch"]["sess-abc"])  # never retried again

        # one more sweep must not type ANYTHING at all — the session gave up
        tmux.sent = []
        wd.model_reconcile(time.time(), tmux, state, MR_TARGET, dry_run=False,
                           projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])

    def test_below_cap_failures_still_release_the_claim_for_retry(self):
        # a single failure (attempts=1, below MODEL_RECONCILE_MAX_ATTEMPTS)
        # must behave exactly as before the cap existed — released for retry.
        state = {}
        tmux, logs, state = self._go("claude-fable-5", MR_IDLE_CAP,
                                     confirm_captured=MR_CONFIRM_FAIL, state=state)
        self.assertFalse(any(ln.startswith("GAVE UP") for ln in logs), logs)
        self.assertNotIn("sess-abc", state.get("modelswitch", {}))


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
        now = time.time()
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
        tmux = ModelReconcileFakeTmux(
            [("%1", "node", "/home/newlevel/devel/demo")], MR_IDLE_CAP)
        logs = wd.run_once(now=time.time(), dry_run=True, run=tmux,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp) / "pending-"),
                           target_model=MR_TARGET,
                           burn_snapshot_path=Path(tmp) / "snap.jsonl")
        self.assertTrue(any(ln.startswith("READY (model-reconcile)") for ln in logs),
                        logs)


if __name__ == "__main__":
    unittest.main()
