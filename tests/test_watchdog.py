"""Tests for the api-watchdog text-emitted tool-call stall detector (job 4a).

A tool call the model emits as TEXT (`<invoke name="...">…</invoke>` inside an
assistant text block) never runs → the turn ends → the session sits idle while
still LOOKING like it was about to act. Job 4a detects this from the transcript
shape and nudges immediately. These tests lock the detector's precision (it must
NOT fire on a meta-conversation that merely discusses `<invoke>` markup — like this
very repo) and the run_once wiring.
"""

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

    def test_no_double_continue_when_already_resumed(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()
        seed = {"sesslimit:" + self.SID: {
            "resets_at": now - 300, "pinged": True, "continued": True,   # already resumed
            "first_seen": int(now - 3600), "last_seen": int(now - 60)}}
        logs, sent, keys, _ = self._harness(now, seed_state=seed)
        self.assertEqual(keys, [], "must not re-send `continue` once resumed: %r" % keys)

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
            tmp, [_assistant("Bežím ďalej.")], [_assistant_apierror()],
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
            tmp, [_assistant("Bežím ďalej.")], [_assistant_apierror()],
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
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.")], [_assistant_apierror()],
            sup_age=10, sub_age=30)           # well under GRACE_SECONDS (300)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.IDLE_CAP)
        self.assertEqual(sent, [], "must not nudge before grace elapses")
        self.assertFalse(any("subagent-apierr" in ln for ln in logs), logs)

    # --- (4a-sub) subagent text-toolcall stall -----------------------------------

    def test_subagent_textcall_stall_nudges_idle_pane(self):
        tmp = tempfile_mkdtemp_cleanup(self)
        proj, now = self._build(
            tmp, [_assistant("Bežím ďalej.")],
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
            tmp, [_assistant("Bežím ďalej.")],
            [_assistant("Earlier."), _assistant(CAMERA_BOX_TEXT)],
            sup_age=10, sub_age=200)
        state_path = Path(tmp) / "state.json"
        logs, sent, pings = self._run(proj, now, state_path, self.BUSY_CAP)
        self.assertEqual(sent, [], "MUST NOT type into a busy pane")
        self.assertTrue(any("subagent-textcall-busy" in ln for ln in logs), logs)
        self.assertTrue(pings, "expected a Discord ping instead of a keystroke")


def tempfile_mkdtemp_cleanup(testcase):
    tmp = TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    return tmp.name


if __name__ == "__main__":
    unittest.main()
