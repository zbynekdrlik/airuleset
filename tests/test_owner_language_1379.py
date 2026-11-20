"""#1379 -- gates.ownerlang + hooks/stop-check-owner-language.sh.

Owner 20.11.2026 (verbatim): "preco sa devbridge napriek uz mesiace
pozadovanemu pravidlu komunikovat so mnou slvoensky neustale prepina do
anglictiny?!" The devbridge transcript showed hours of English answers to the
owner's Slovak prompts; the always-on rule covered only QUESTIONS, and no Stop
hook read the language of the final message.

The gate: when the last genuine owner prompt is Slovak and the final message's
prose (code, inline code, URLs, paths and `> ` quotes stripped) is mainly
English, exit 2 once. Never on a short message, never when the newest user
entry is Stop-hook feedback, a task notification or a `<channel …>` peer
message, never on an English prompt; fail-open on every read error.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from gates import ownerlang as ol  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-owner-language.sh"
MODULE = REPO / "modules" / "core" / "user-questions-slovak.md"
HOOKS_JSON = REPO / "settings" / "hooks.json"

# The owner's real devbridge prompts (issue evidence, 19.-20.11.2026).
OWNER_PROMPTS = (
    "som pri terminali. Podme na to",
    "si robis asi srandu!!",
    "ale ja nemam jednu hodinu!!!",
    "slovensky!!!",
)
# devbridge's English answers. Only their first words are quoted on the issue
# ("Starting now. The terminal will ask for a card …", "You're right, and I'm
# sorry", "The fix for #158 … is stuck"); the rest of each line is a faithful
# continuation, NOT transcript text (the devbridge transcript lives in another
# account and was not read).
EN_ANSWER = ("Starting now. The terminal will ask for a card first; once it shows "
             "the amount, tap the card and tell me what the screen says, then I "
             "will check the logs on the cash PC.")
EN_SORRY = ("You're right, and I'm sorry. The fix for #158 is stuck because the "
            "build on the cash PC failed and I did not notice it until now.")
# devbridge's Slovak line after the owner wrote "slovensky!!!", verbatim as the
# dispatch quoted it (the issue body quotes only its first sentence).
SK_APOLOGY = ("Prepáč, odteraz píšem po slovensky. Pokladničné PC v SNV beží bez "
              "eKasy, verzia 0.8.82, terminál je pripojený.")
# English material a Slovak report legitimately carries: code, a command, a
# path, an URL, an exact error string in a quote.
EN_MATERIAL = ("the build is not running because the service could not find the "
               "config file and it will retry after the next push to the branch")
SK_WITH_CODE = (
    "Opravil som hook a pridal test, všetko je zelené a nasadené na dev1.\n"
    "Spustil som `python3 -m pytest tests/test_x.py -q` a prešlo to.\n"
    "Zmena je v súbore ~/devel/airuleset/hooks/stop-check-x.sh a v "
    "gates/ownerlang.py, záznam je na https://github.com/o/r/issues/1#issuecomment-2\n"
    "> " + EN_MATERIAL + "\n"
    "```\n" + EN_MATERIAL + "\n" + EN_MATERIAL + "\n```\n"
    "`" + EN_MATERIAL + "`\n"
    "✅ DONE: hook je opravený")


def _payload(msg, tp="", active=False, cwd=None):
    return {"session_id": "i1379-test", "transcript_path": tp,
            "cwd": cwd or str(REPO), "hook_event_name": "Stop",
            "stop_hook_active": active, "last_assistant_message": msg}


class _Transcript:
    """A synthetic Claude Code transcript (one JSON object per line)."""

    def __init__(self, tc):
        self.dir = tempfile.mkdtemp(prefix="i1379-tp-")
        tc.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "s.jsonl")
        self.lines = []
        self.n = 0

    def _add(self, entry):
        self.n += 1
        entry.setdefault("uuid", "e%d" % self.n)
        entry.setdefault("timestamp", "2026-11-20T04:%02d:00Z" % (self.n % 60))
        self.lines.append(entry)
        return self

    def user(self, content, **extra):
        return self._add(dict(type="user", message={"role": "user", "content": content},
                              **extra))

    def prompt(self, text):
        return self.user(text)

    def feedback(self, text="Stop hook feedback:\n[bash ~/x.sh]: blocked"):
        return self.user(text, isMeta=True)

    def meta(self, text):
        return self.user([{"type": "text", "text": text}], isMeta=True)

    def assistant(self, text):
        return self._add({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}})

    def tool(self, result="ok"):
        tid = "toolu_%d" % self.n
        self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "Bash",
             "input": {"command": "true"}}]}})
        return self.user([{"type": "tool_result", "tool_use_id": tid, "content": result}])

    def raw(self, text):
        self.lines.append(text)
        return self

    def write(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            for ln in self.lines:
                fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")
        return self.path


class TestProse(unittest.TestCase):
    def test_devbridge_english_answers_are_mainly_english(self):
        for msg in (EN_ANSWER, EN_SORRY):
            with self.subTest(msg=msg[:30]):
                self.assertGreaterEqual(len(ol.prose_words(msg)), ol.MIN_PROSE_WORDS)
                self.assertTrue(ol.message_is_mainly_english(msg))

    def test_devbridge_slovak_line_is_judged_and_passes(self):
        self.assertGreaterEqual(len(ol.prose_words(SK_APOLOGY)), ol.MIN_PROSE_WORDS)
        self.assertFalse(ol.message_is_mainly_english(SK_APOLOGY))

    def test_slovak_reply_full_of_code_and_paths_passes(self):
        self.assertFalse(ol.message_is_mainly_english(SK_WITH_CODE))
        # the stripping is load-bearing: the same English as bare prose blocks
        bare = SK_WITH_CODE.split("\n")[0] + "\n" + "\n".join([EN_MATERIAL] * 3)
        self.assertTrue(ol.message_is_mainly_english(bare))

    def test_short_message_is_never_judged(self):
        msg = "Done, pushed it to the branch and CI is green."
        self.assertLess(len(ol.prose_words(msg)), ol.MIN_PROSE_WORDS)
        self.assertFalse(ol.message_is_mainly_english(msg))

    def test_titles_after_issue_refs_do_not_count(self):
        msg = ("Hotovo, oba tickety sú zatvorené a nasadené na produkcii, overil "
               "som to aj v prehliadači: "
               "#12 (Fix the login bug in the app when the token is expired), "
               "#13 (Make the dashboard show the version on every page of the app).")
        words = ol.prose_words(msg)
        self.assertGreaterEqual(len(words), ol.MIN_PROSE_WORDS)
        for w in ("expired", "dashboard", "the"):
            self.assertNotIn(w, words)
        self.assertFalse(ol.message_is_mainly_english(msg))

    def test_acronyms_and_status_keywords_are_not_words(self):
        words = ol.prose_words("✅ DONE: PC v SNV beží, CI je zelené, NEEDS YOU nie")
        for w in ("done", "pc", "snv", "ci", "needs", "you"):
            self.assertNotIn(w, words)

    def test_linear_on_pathological_input(self):
        big = ("a" * 100000 + " " + "x.y" * 30000 + " " + "#1 (" * 20000 + " "
               + "`" * 5000 + "\n" + "> q\n" * 5000)
        t0 = time.monotonic()
        ol.message_is_mainly_english(big)
        ol.prompt_language(big)
        self.assertLess(time.monotonic() - t0, 2.0)


class TestPromptLanguage(unittest.TestCase):
    def test_owner_prompts_are_slovak(self):
        for p in OWNER_PROMPTS:
            with self.subTest(prompt=p):
                self.assertEqual(ol.prompt_language(p), "sk")

    def test_owner_corpus_prompts_are_slovak(self):
        # real owner prompts from the controller's main session that a first
        # cut read as "unknown" (corpus replay before shipping)
        for p in ("pokracuj", "cize co dalej?", "zopakuj otazku", "hotovo, over",
                  "goal nechcem", "ty neries"):
            with self.subTest(prompt=p):
                self.assertEqual(ol.prompt_language(p), "sk")

    def test_english_instruction_wrapping_a_slovak_ticket_is_english(self):
        # corpus replay: an eval wrapper with a pasted Slovak ticket body
        ticket = ("Ticket: relokácia dát na nový disk, dopočet swapu a deklarácia "
                  "v zázname. Je to potrebné preto, lebo disk je plný a služby "
                  "padajú; riešenie musí byť idempotentné, bez straty dát, a ešte "
                  "pred tým, ako sa spustí ďalší krok, musí overiť cieľ. ") * 3
        p = ("You are the design author for a ticket in a Python (stdlib-only) "
             "operations repo. Read the ticket below, trace the root cause in the "
             "code and write the design with the chosen approach and the rejected "
             "alternative. The ticket:\n\n" + ticket)
        sk, en = ol.scores(ol.prose_words(p))
        self.assertGreater(sk, en)                  # Slovak by volume ...
        self.assertEqual(ol.prompt_language(p), "en")   # ... English by its lead

    def test_hyphenated_and_dotted_tokens_are_not_words(self):
        words = ol.prose_words("run the pre-commit hook on v0.1.688 and e-mail it")
        for w in ("pre", "commit", "e", "mail"):
            self.assertNotIn(w, words)

    def test_english_prompt(self):
        self.assertEqual(ol.prompt_language("Please fix the build and push it"), "en")

    def test_machine_or_empty_prompt_is_unknown(self):
        for p in ("continue", "/compact", "", "<command-name>/model</command-name>"):
            with self.subTest(prompt=p):
                self.assertEqual(ol.prompt_language(p), "unknown")

    def test_system_reminder_inside_a_prompt_is_ignored(self):
        p = ("som tu\n<system-reminder>\nThe user is working in this repository "
             "and the files below were changed by the user or a linter.\n"
             "</system-reminder>")
        self.assertEqual(ol.prompt_language(p), "sk")


class TestEvaluate(unittest.TestCase):
    def _eval(self, t, msg, **kw):
        return ol.evaluate(_payload(msg, t.write(), **kw))[0]

    def test_slovak_prompt_english_answer_blocks(self):
        for p in OWNER_PROMPTS:
            with self.subTest(prompt=p):
                t = _Transcript(self).prompt(p).assistant(EN_ANSWER)
                verdict, reason = ol.evaluate(_payload(EN_ANSWER, t.write()))
                self.assertEqual(verdict, "block")
                self.assertIn("slovensky", reason)
                self.assertIn("```", reason)

    def test_slovak_prompt_slovak_answer_allows(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(SK_APOLOGY)
        self.assertEqual(self._eval(t, SK_APOLOGY), "allow")

    def test_slovak_prompt_code_heavy_slovak_answer_allows(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).tool().assistant(SK_WITH_CODE)
        self.assertEqual(self._eval(t, SK_WITH_CODE), "allow")

    def test_english_prompt_is_not_checked(self):
        t = _Transcript(self).prompt("Please check the terminal and the card reader")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_stop_hook_feedback_last_is_not_checked(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(EN_ANSWER).feedback()
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    # The wake texts below are Slovak on purpose: read as an owner prompt they
    # would BLOCK, so an allow proves the entry kind was recognised.
    def test_task_notification_last_is_not_checked(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant("…").user(
            "<task-notification>\n<task-id>x</task-id>\n<summary>Agent dokončil "
            "prácu na úlohe a výsledok je pripravený</summary>\n</task-notification>")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_channel_message_last_is_not_checked(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant("…").user(
            '<channel source="plugin:peer:peer" from="dasio">nasadenie je hotové, '
            'skontroluj prosím dashboard a daj mi vedieť</channel>')
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_short_message_is_not_checked(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        self.assertEqual(self._eval(t.assistant("Done, pushed."), "Done, pushed."), "allow")

    def test_tool_results_and_meta_entries_are_skipped(self):
        t = (_Transcript(self).prompt(OWNER_PROMPTS[0]).tool("x" * 5000)
             .meta("Base directory for this skill: /x\nUse this skill when the user "
                   "wants to check the build and the logs on the server.")
             .tool().assistant(EN_ANSWER))
        self.assertEqual(self._eval(t, EN_ANSWER), "block")

    def test_list_content_prompt_is_read(self):
        t = _Transcript(self).user([{"type": "text", "text": OWNER_PROMPTS[1]}])
        self.assertEqual(self._eval(t.assistant(EN_SORRY), EN_SORRY), "block")

    def test_signal_free_prompt_inherits_the_owner_language(self):
        image = [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]
        for later in ("1", image, "continue"):
            with self.subTest(later=str(later)[:20]):
                t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant("…").user(later)
                self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "block")
                t = _Transcript(self).prompt("Please check the card reader and the logs")
                t.assistant("…").user(later)
                self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_harness_echo_is_not_the_owner_language(self):
        # a slash-command echo / local command output is Claude Code's English,
        # not the owner's words: it inherits like a bare "1"
        for echo in ("<local-command-stdout>Set effort level to xhigh (saved as your "
                     "default for new sessions)</local-command-stdout>",
                     "<command-name>/goal</command-name>\n<command-args>the loop is "
                     "done when all of the issues are closed and the CI is green"
                     "</command-args>"):
            with self.subTest(echo=echo[:24]):
                t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant("…").user(echo)
                self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "block")

    def test_signal_free_prompt_alone_is_not_checked(self):
        t = _Transcript(self).prompt("1")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_inheritance_stops_at_a_compact_summary(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).user(
            "Táto relácia pokračuje z predchádzajúcej konverzácie.",
            isCompactSummary=True).assistant("…").prompt("1")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_inheritance_is_bounded(self):
        for n, want in ((ol.PROMPT_LOOKBACK - 1, "block"), (ol.PROMPT_LOOKBACK, "allow")):
            with self.subTest(n=n):
                t = _Transcript(self).prompt(OWNER_PROMPTS[0])
                for _ in range(n):
                    t.assistant("…").prompt("1")
                self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), want)

    def test_compact_summary_last_is_not_checked(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).user(
            "Táto relácia pokračuje z predchádzajúcej konverzácie, ktorá sa minula "
            "a owner je pri termináli.", isCompactSummary=True)
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "allow")

    def test_sidechain_entries_are_ignored(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).user(
            "Please review the diff and report the findings", isSidechain=True)
        self.assertEqual(self._eval(t.assistant(EN_ANSWER), EN_ANSWER), "block")

    def test_stop_hook_active_allows(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(EN_ANSWER)
        self.assertEqual(self._eval(t, EN_ANSWER, active=True), "allow")

    def test_missing_or_unreadable_transcript_allows(self):
        for tp in ("", "/no/such/dir/s.jsonl", tempfile.gettempdir()):
            with self.subTest(tp=tp):
                self.assertEqual(ol.evaluate(_payload(EN_ANSWER, tp))[0], "allow")

    def test_torn_lines_are_skipped(self):
        t = (_Transcript(self).prompt(OWNER_PROMPTS[0])
             .raw('{"type":"user","message":{"content":"x"}}{"type":"assist')
             .raw("not json at all").assistant(EN_ANSWER))
        self.assertEqual(self._eval(t, EN_ANSWER), "block")

    def test_prompt_beyond_the_scan_window_allows(self):
        # the window is read from the module at CALL time, so a small one can
        # be patched in instead of writing a 32 MiB fixture
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        for _ in range(4):
            t.tool("y" * 100000)
        path = t.assistant(EN_ANSWER).write()
        with mock.patch.object(ol, "MAX_SCAN_BYTES", 300000):
            self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "allow")
        self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "block")

    def test_scan_window_covers_a_long_turn(self):
        # review round 1: Claude Code 2.1.3xx writes api-request records at
        # about 2 MiB a minute (watchdog/owner_stop.py TAIL_BYTES), so the old
        # 4 MB window lost the prompt of any turn longer than two minutes
        self.assertGreaterEqual(ol.MAX_SCAN_BYTES, 32 * 1024 * 1024)
        self.assertLessEqual(ol.MAX_SCAN_BYTES, 64 * 1024 * 1024)

    def test_prompt_behind_6_mb_of_api_records_is_found_fast(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        for _ in range(30):
            t.raw(json.dumps({"type": "api-request-blob", "messages": [
                {"role": "user", "content": "x" * 200000}]}))
        path = t.assistant(EN_ANSWER).write()
        self.assertGreater(os.path.getsize(path), 6_000_000)
        t0 = time.monotonic()
        self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "block")
        self.assertLess(time.monotonic() - t0, 2.0)


class TestHook(unittest.TestCase):
    def _run(self, payload, env=None):
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(["bash", str(HOOK)], input=stdin, capture_output=True,
                              text=True, timeout=30, cwd=str(REPO),
                              env=env or hermetic_hook_env(self))

    def test_hook_blocks_with_a_slovak_reason(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(EN_ANSWER)
        r = self._run(_payload(EN_ANSWER, t.write()))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("slovensky", r.stderr)

    def test_hook_allows_a_slovak_answer(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(SK_APOLOGY)
        r = self._run(_payload(SK_APOLOGY, t.write()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_hook_fails_open_on_garbage_stdin(self):
        for stdin in ("", "not json", "[1, 2]", '{"last_assistant_message": 7}'):
            with self.subTest(stdin=stdin):
                self.assertEqual(self._run(stdin).returncode, 0)

    def test_block_is_audited(self):
        env = hermetic_hook_env(self)
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(EN_ANSWER)
        self.assertEqual(self._run(_payload(EN_ANSWER, t.write()), env).returncode, 2)
        log = Path(env["HOME"]) / "devel" / "airuleset" / "audits" / ol.GATE_LOG
        self.assertIn("BLOCK", log.read_text(encoding="utf-8"))


class TestWiring(unittest.TestCase):
    def test_registered_on_stop_for_every_box(self):
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        hits = [h for blk in cfg["hooks"]["Stop"] for h in blk.get("hooks", [])
                if h.get("command", "").endswith("/stop-check-owner-language.sh")]
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["command"],
                         "bash ~/devel/airuleset/hooks/stop-check-owner-language.sh")
        self.assertLessEqual(hits[0].get("timeout", 60), 10)

    def test_hook_is_executable_and_strict(self):
        self.assertTrue(HOOK.exists())
        self.assertTrue(os.access(HOOK, os.X_OK))
        self.assertIn("set -euo pipefail", HOOK.read_text(encoding="utf-8"))


class TestDoctrine(unittest.TestCase):
    def test_every_owner_message_is_slovak(self):
        text = MODULE.read_text(encoding="utf-8")
        for phrase in ("Every message to the owner is SLOVAK", "not only questions",
                       "stop-check-owner-language.sh", "peer messages",
                       # review round 1: the operative restriction + every
                       # keyword the hooks key on (#799: lock the rule, not nouns)
                       "English only in code, commands, paths, exact error strings",
                       "`NEEDS YOU`", "`ASKED`", "`WORKING`", "`DONE`",
                       "`Work Complete`"):
            self.assertIn(phrase, text)


# --------------------------------------------------------------------------- #
# Review round 1 (two fresh-context adversarial reviews): every finding with a
# behaviour gets a fixture here. The probe texts are the reviewers' own.
# --------------------------------------------------------------------------- #
# a SHORT Slovak lead (sk 4): with the English stripped the message is too short
# to judge (allow); with any stripper broken it blocks (the teeth)
SK_SHORT = "Pozrel som to, výsledok je takýto:\n"
EN_HEAVY = EN_MATERIAL + " and " + EN_MATERIAL
FP_NAMES = (
    "The Pokladničné PC in Spišská Nová Ves is running without eKasa, and the "
    "terminal in Kežmarok is connected. I checked the logs on the cash PC in Stará "
    "Ľubovňa, and the build there is fine.",
    "The cash PC in Spišská Nová Ves is back online, Kežmarok and Stará Ľubovňa "
    "still show the old version, I will push the build to them next.",
    "I created the invoice for Pekáreň Hriňová and sent it to Jozef Šťastný; the "
    "order for Mäsiarstvo Ďurík is still open because it has no address.",
)
TITLES_TABLE = (
    "| Ticket | Názov | Stav |\n|---|---|---|\n"
    "| #1379 | Every message to the owner is Slovak and not only the questions that we ask | otvorený |\n"
    "| #1378 | The filing brake counts the history with the same predicate as the live filing | zatvorený |\n"
    "| #1377 | The footer should show the number of the tickets that are waiting on the owner | otvorený |\n"
    "| #1376 | Make the watchdog skip a pane when the user is typing in it and it is busy | otvorený |\n")
TITLES_DASH = (
    "- #1379 — Every message to the owner is Slovak, not only the questions\n"
    "- #1378 — Filing brake counts history with the same predicate as the live filing\n"
    "- #1377 — The footer should show the number of tickets that wait on the owner\n")
TITLES_PR = (
    "- PR #7: Refactor the driver so that it handles the reconnect when the device is gone\n"
    "- PR #8: Add a test for the case where the card reader is not connected at all\n"
    "- PR #9: Fix the crash when the config file is missing on the first start of the app\n")
TITLES_NESTED = (
    "Zatvoril som #1379 (Every message to the owner (not only the questions) is Slovak "
    "and the Stop gate holds it for all of the boxes), #1380 (Make the footer (the U "
    "bucket) show what is waiting on the owner and not on the client).\n")
QUOTED_ERRORS = (
    'Build padol s chybou "could not find the config file and it will not retry until '
    'the next push to the branch" a potom ešte "the service is not running because it '
    'was not able to bind to the port".\n')
LOG_LINES = (
    "2026-11-20 05:21:03 ERROR the connection to the database was lost and it could not be restored\n"
    "2026-11-20 05:21:04 WARN retrying in 5 seconds because the server is not available\n"
    "05:21:09 the connection to the database was lost and it could not be restored\n"
    "INFO giving up after the third attempt, the job will run again in an hour\n")


class TestRound1Prose(unittest.TestCase):
    def test_slovak_names_do_not_hide_an_english_reply(self):
        for msg in FP_NAMES:
            with self.subTest(msg=msg[:30]):
                self.assertTrue(ol.message_is_mainly_english(msg))

    def test_a_sentence_initial_slovak_word_still_counts(self):
        self.assertIn("pokladničné", ol.prose_words(SK_APOLOGY))
        self.assertNotIn("kežmarok", ol.prose_words("The PC in Kežmarok is up."))
        self.assertIn("i'm", ol.prose_words("Sure, I'm on it."))

    def test_digit_glued_tokens_and_over_are_no_slovak_signal(self):
        self.assertEqual(ol.prose_words("v2 k8s s3 10k o2"), [])
        for msg in ("The v2 rollout on k8s is done. I bumped the s3 bucket policy, set "
                    "the 10k limit, the v3 API is next. It's all in the k8s namespace "
                    "for v2.",
                    "I went over the logs on v2 and k8s, took over the s3 sync and moved "
                    "it over to v3; the 10k batch is over."):
            with self.subTest(msg=msg[:30]):
                self.assertTrue(ol.message_is_mainly_english(msg))

    def test_ticket_title_forms_are_not_prose(self):
        for block in (TITLES_TABLE, TITLES_DASH, TITLES_PR, TITLES_NESTED):
            with self.subTest(block=block[:24]):
                self.assertFalse(ol.message_is_mainly_english(SK_SHORT + block))

    def test_double_quoted_strings_and_log_lines_are_not_prose(self):
        for msg in (QUOTED_ERRORS + "Opravím to.\n",
                    "Pozrel som logy služby:\n" + LOG_LINES + "Príčina je výpadok DB.\n"):
            with self.subTest(msg=msg[:24]):
                self.assertFalse(ol.message_is_mainly_english(msg))

    def test_each_stripper_is_load_bearing(self):
        ident = " ".join(w + "_" + w for w in EN_HEAVY.split())
        for name, wrapped in (("backtick fence", "```\n" + EN_HEAVY + "\n```\n"),
                              ("tilde fence", "~~~\n" + EN_HEAVY + "\n~~~\n"),
                              ("inline code", "`" + EN_HEAVY + "`\n"),
                              ("quote", "> " + EN_HEAVY + "\n"),
                              ("identifiers", ident + "\n")):
            with self.subTest(stripper=name):
                self.assertFalse(ol.message_is_mainly_english(SK_SHORT + wrapped))
        self.assertTrue(ol.message_is_mainly_english(SK_SHORT + EN_HEAVY))

    def test_a_backtick_line_inside_a_tilde_fence_stays_fenced(self):
        msg = SK_SHORT + "~~~\n```\n" + EN_HEAVY + "\n~~~\n"
        self.assertFalse(ol.message_is_mainly_english(msg))

    def test_curly_apostrophe_contractions_are_english(self):
        self.assertEqual(ol.scores(ol.prose_words("it’s I’m don’t")), (0, 3))

    def test_min_en_boundary(self):
        base = ("Deployment finished, services restarted, dashboards verified, metrics "
                "checked, logs rotated, configs updated, caches warmed")
        self.assertFalse(ol.message_is_mainly_english(base + " the and the"))
        self.assertTrue(ol.message_is_mainly_english(base + " the and the for"))

    def test_en_ratio_boundary(self):
        sk = ["som", "je", "na", "pri"]
        self.assertFalse(ol.message_is_mainly_english(" ".join(sk + ["the"] * 11)))
        self.assertTrue(ol.message_is_mainly_english(" ".join(sk + ["the"] * 12)))

    def test_diacritics_alone_make_a_prompt_slovak(self):
        self.assertEqual(ol.prompt_language("Pokladničné terminály hlásia výpadok, opravíš?"),
                         "sk")

    def test_a_pasted_english_log_around_a_slovak_line(self):
        # Re-pinned in review round 2: a "short Slovak first/last line" rule
        # read English prompts ending in a shell line, a checklist item or a
        # Slovak quote as Slovak (false blocks). An UNFENCED paste now reads as
        # English (the safe direction, never judged); a fenced or quoted paste
        # leaves only the owner's own words.
        paste = ("Error: the connection to the database was lost and it could not be "
                 "restored because the server is not available and the job will run "
                 "again in an hour when the network is back")
        self.assertEqual(ol.prompt_language(paste + "\n\nco s tym?"), "en")
        self.assertEqual(ol.prompt_language("```\n" + paste + "\n```\nco s tym?"), "sk")
        self.assertEqual(ol.prompt_language("pozri toto:\n> " + paste), "sk")
        self.assertEqual(ol.prompt_language(paste), "en")

    def test_block_reason_names_the_kept_keywords(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(EN_ANSWER)
        reason = ol.evaluate(_payload(EN_ANSWER, t.write()))[1]
        for kw in ("NEEDS YOU", "ASKED", "WORKING", "DONE", "Work Complete", '"'):
            self.assertIn(kw, reason)


class TestRound1Transcript(unittest.TestCase):
    def _eval(self, t, msg=EN_ANSWER):
        return ol.evaluate(_payload(msg, t.write()))[0]

    def _queued(self, t, text, origin=None):
        att = {"type": "queued_command", "prompt": text, "commandMode": "prompt"}
        if origin is not None:
            att["origin"] = origin
        return t._add({"type": "attachment", "attachment": att})

    def test_a_queued_owner_message_is_the_newest_turn(self):
        t = _Transcript(self).prompt("Please check the card reader and the logs")
        t.assistant("…").user("<task-notification>\n<status>completed</status>\n"
                              "</task-notification>")
        self._queued(t.tool(), "slovensky!!! preco pises po anglicky?")
        self.assertEqual(self._eval(t.tool().assistant(EN_ANSWER)), "block")

    def test_a_queued_machine_continuation_is_skipped(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        self._queued(t, "Goal set: STOP CONDITIONS — the loop is done when all of the "
                        "issues are closed and the CI is green", {"kind": "auto-continuation"})
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_an_english_machine_nudge_inherits_the_owner_language(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant("…").prompt(
            "stuck-check: goal arm failed 3x. Owner: paste the /goal line above and "
            "check the session, it is not running")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_an_interrupt_marker_inherits_the_owner_language(self):
        t = (_Transcript(self).prompt(OWNER_PROMPTS[0]).tool()
             .prompt("[Request interrupted by user for tool use]").prompt("1"))
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_a_discord_relay_is_the_owner_speaking(self):
        t = _Transcript(self).prompt("Please check the card reader and the logs")
        t.assistant("…").prompt("Odpoveď z Discordu: áno, sprav to hneď")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_pasted_stop_hook_output_is_an_owner_prompt(self):
        t = _Transcript(self).prompt("Please check the card reader and the logs")
        t.assistant("…").prompt("Stop hook feedback: zablokovalo mi to, čo s tým "
                                "spravíme? prečo to tak je?")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_a_reminder_block_does_not_hide_the_prompt(self):
        t = _Transcript(self).prompt("Please check the card reader and the logs")
        t.assistant("…").user([
            {"type": "text", "text": "<system-reminder>\nThe user is working in this "
                                     "repository and the file was changed.\n</system-reminder>"},
            {"type": "text", "text": OWNER_PROMPTS[0]}])
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_a_string_message_is_skipped_not_fatal(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).raw(
            json.dumps({"type": "user", "message": "hi"}))
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_tool_and_meta_entries_do_not_exhaust_the_lookback(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        for _ in range(ol.PROMPT_LOOKBACK + 3):
            t.tool().meta("Base directory for this skill: /x\nUse it when the user "
                          "wants to check the logs.")
        self.assertEqual(self._eval(t.assistant(EN_ANSWER)), "block")

    def test_a_prompt_line_across_the_chunk_edge(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        prompt_len = len(json.dumps(t.lines[0])) + 1
        t.assistant("z" * (ol.CHUNK - 220))
        path = t.write()
        size = os.path.getsize(path)
        self.assertTrue(0 < size - ol.CHUNK < prompt_len, (size, prompt_len))
        self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "block")


class TestRound1Run(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="i1379-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sid = "i1379-run-%d-%s" % (os.getpid(), self.id().rsplit(".", 1)[-1])
        self.marker = "/tmp/airuleset-owner-language-block-" + self.sid
        self.addCleanup(lambda: os.path.exists(self.marker) and os.remove(self.marker))

    def _log(self):
        p = Path(self.home) / "devel" / "airuleset" / "audits" / ol.GATE_LOG
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _run(self, payload):
        return ol.run(json.dumps(payload) if isinstance(payload, dict) else payload)

    def test_fail_open_paths_are_journalled(self):
        self.assertEqual(self._run("{not json"), 0)
        self.assertEqual(self._run(_payload(EN_ANSWER, "/no/such/dir/s.jsonl")), 0)
        log = self._log()
        self.assertIn("BADPAYLOAD", log)
        self.assertIn("UNREADABLE", log)

    def test_a_non_string_cwd_never_raises(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        payload = _payload(EN_ANSWER, t.assistant(EN_ANSWER).write())
        payload["cwd"] = 7
        self.assertEqual(self._run(payload), 2)

    def test_the_block_survives_a_failing_journal(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        payload = _payload(EN_ANSWER, t.assistant(EN_ANSWER).write())
        with mock.patch.object(ol.audit, "append_line", side_effect=OSError("ro")), \
                mock.patch("sys.stderr") as err:
            self.assertEqual(self._run(payload), 2)
        self.assertIn("slovensky", "".join(c.args[0] for c in err.write.call_args_list))

    def test_a_block_leaves_the_notify_marker(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        payload = _payload(EN_ANSWER, t.assistant(EN_ANSWER).write())
        payload["session_id"] = self.sid
        self.assertEqual(self._run(payload), 2)
        self.assertTrue(os.path.isfile(self.marker))
        self.assertLess(time.time() - os.path.getmtime(self.marker), 30)


class _NotifyBase(unittest.TestCase):
    """A hermetic HOME with a fake Discord bot + a fake curl for
    notify-discord-pending.sh (the #467 harness shape)."""

    PENDING = REPO / "hooks" / "notify-discord-pending.sh"
    QUESTION = ("**Otázka — projekt airuleset (správa pravidiel):** The gate needs a "
                "decision on the window size before I continue with the tests.\n\n"
                "- Keep 32 MiB (recommended) — covers a long turn\n"
                "- Use 8 MiB — cheaper\n\n"
                "❓ NEEDS YOU: which window size should the gate use?")

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="i1379-notify-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True)
        # a fake bot credential for the fake curl (built, never a literal value)
        (d / ".env").write_text("%s=%s\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n" % (
            "DISCORD_BOT_TOKEN", "fake"))
        self.bin = Path(tempfile.mkdtemp(prefix="i1379-curl-"))
        self.addCleanup(shutil.rmtree, self.bin, True)
        (self.bin / "curl").write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n%s' '{\"id\":\"137901\"}' '200'\n")
        (self.bin / "curl").chmod(0o755)
        self.sid = "i1379-notify-%d" % os.getpid()
        for p in ("/tmp/airuleset-owner-language-block-" + self.sid,
                  "/tmp/claude-discord-lastq-" + self.sid,
                  "/tmp/claude-discord-pending-" + self.sid,
                  "/tmp/claude-discord-cardchk-" + self.sid):
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))



class TestRound1Notify(_NotifyBase):
    """The English ❓ draft a block rejects must not reach the phone (review B
    🔴): notify-discord-pending suppresses it exactly like a question-quality
    rewrite, so the Slovak rewrite is a fresh ask, never a blocked repeat."""

    def test_a_rejected_english_question_is_not_pinged(self):
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": "",
               "AIRULESET_SHARED_CHANNEL_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(self.QUESTION)
        payload = _payload(self.QUESTION, t.write(), cwd=str(self.home))
        payload["session_id"] = self.sid
        stdin = json.dumps(payload)
        r = subprocess.run(["bash", str(HOOK)], input=stdin, text=True,
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 2, r.stderr)
        r = subprocess.run(["bash", str(self.PENDING)], input=stdin, text=True,
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        qmap = self.home / ".claude" / "discord-questions.json"
        self.assertFalse(qmap.exists() and "137901" in qmap.read_text())
        dlog = (self.home / ".claude" / "notify-delivery.log").read_text()
        self.assertIn("owner-language-rewrite", dlog)


class TestRound1Structure(unittest.TestCase):
    def test_the_gate_never_imports_watchdog(self):
        code = ("import sys; sys.path.insert(0, %r); import gates.ownerlang; "
                "print('watchdog' in sys.modules)" % str(REPO))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.stdout.strip(), "False", r.stderr)

    def test_machine_prompt_constants_have_one_home(self):
        import machine_prompts as mp
        import watchdog
        from watchdog import owner_stop, questions
        self.assertIs(watchdog._MACHINE_PROMPT_PREFIXES, mp.MACHINE_PROMPT_PREFIXES)
        self.assertIs(watchdog._MACHINE_PROMPT_EXACT, mp.MACHINE_PROMPT_EXACT)
        self.assertIs(questions._DISCORD_ANSWER_PREFIXES, mp.DISCORD_ANSWER_PREFIXES)
        self.assertEqual(owner_stop._INTERRUPT, mp.INTERRUPT_PREFIX)
        for p in mp.DISCORD_ANSWER_PREFIXES:
            self.assertIn(p, mp.MACHINE_PROMPT_PREFIXES)


# --------------------------------------------------------------------------- #
# Review round 2 (a fresh-context verification of the round-1 fix).
# --------------------------------------------------------------------------- #
SK_MOSTLY = (
    "Stav ticketov po dnešnom kole:\n"
    "- #1379: brána je opravená, nasadená a overená na dev1 aj dev2\n"
    "- #1380: čaká na review, CI je zelené\n"
    "- #1381: zatvorené ako duplicitné, odkaz je v komentári\n"
    "Next: I will run the mutation tests and then push the fix.\n",
    "Zatvoril som tieto tickety:\n"
    "- #1379 — opravená brána, nasadené na všetky boxy\n"
    "- #1380 — doplnený test, CI je zelené\n"
    "The rest of the work is in the next PR and it will be done today.\n",
    "PR #7: mergnutý a nasadený, overil som to v prehliadači a verzia sedí.\n"
    "PR #8: čaká na CI, potom ho mergnem.\n"
    "It is all on the dev branch for now and the release is after that.\n",
    "Priebeh:\n"
    "2026-11-20 07:10 nasadil som opravu na dev1 a overil ju\n"
    "2026-11-20 07:20 spustil som testy, všetko prešlo\n"
    "2026-11-20 07:30 nasadil som to na všetky boxy\n"
    "Zhrnutie: the gate is live on all of the boxes and it is working as it should now.\n",
    "INFO je teraz zapnuté pre všetky služby a logy sa ukladajú na disk.\n"
    "ERROR hlášky sa posielajú na Discord.\n"
    "The rest of the config is the same as before and it is in the repo.\n",
)
EN_PROMPTS_WITH_SLOVAK_TAIL = (
    "Please summarise the client's message below in English for the CRM note.\n\n"
    "Dobrý deň, faktúra je zlá.",
    "Please check why the health check fails and fix it.\ncurl -s https://example.com/health",
    "Run the migration on the staging box and check the logs afterwards.\nsu - deploy",
    "Why does this command print nothing for the service logs?\n"
    "journalctl -u api | grep -v DEBUG",
    "Fix the failing test and push the branch when the suite is green.\n- [ ] overiť na dev1",
)


class TestRound2Prose(unittest.TestCase):
    def test_slovak_status_in_structured_places_still_counts(self):
        # language-aware cuts: a ticket title, a table cell, a quote or a log
        # line is dropped only when it carries no Slovak signal
        for msg in SK_MOSTLY:
            with self.subTest(msg=msg[:28]):
                self.assertFalse(ol.message_is_mainly_english(msg))
        words = ol.prose_words("Zatvoril som #12 (oprava terminálu v SNV) a nasadil to.")
        self.assertIn("oprava", words)
        self.assertIn("terminálu", words)

    def test_a_single_letter_does_not_make_an_english_title_slovak(self):
        # final probe replay: "(the U bucket)" kept a whole English title,
        # because the single letter "u" is a Slovak preposition
        self.assertFalse(ol.message_is_mainly_english(TITLES_NESTED))
        self.assertNotIn("footer", ol.prose_words(TITLES_NESTED))

    def test_english_behind_a_level_word_or_a_long_quote_counts(self):
        for msg in ("WARNING: the build is not green and the deploy did not run, so I "
                    "will look at it again after the tests are done and tell you what "
                    "is wrong.",
                    'He wrote "the build is not green and the deploy did not run, so I '
                    "will look at it again after the tests are done and tell you what "
                    "is wrong, and then I will push the fix to the branch and wait for "
                    'the checks to pass before anything else is done" and left.'):
            with self.subTest(msg=msg[:24]):
                self.assertTrue(ol.message_is_mainly_english(msg))

    def test_sentence_start_after_a_bullet_bold_colon_or_ellipsis(self):
        for text in ("- Kežmarok beží", "**Úloha:** Oprava beží", "Stav: Kežmarok beží",
                     "Hotovo… Kežmarok beží", "1. Kežmarok beží"):
            with self.subTest(text=text):
                words = ol.prose_words(text)
                self.assertTrue({"kežmarok", "oprava"} & set(words), words)
        self.assertNotIn("kežmarok", ol.prose_words("PC v Kežmarok beží"))

    def test_min_prose_words_boundary(self):
        eleven = "the build and the deploy is done for the cash register"
        self.assertEqual(len(ol.prose_words(eleven)), 11)
        self.assertFalse(ol.message_is_mainly_english(eleven))
        self.assertTrue(ol.message_is_mainly_english(eleven + " again"))

    def test_ref_cut_edges(self):
        self.assertIn("three", ol.prose_words("#12-3 three"))
        words = ol.prose_words("Ticket #12 (the reader is not fixed and it will take")
        self.assertNotIn("reader", words)

    def test_quote_stripping_is_linear(self):
        # 100k unpaired openers: the bounded-regex cut took ~2.3 s here (n x 600
        # backtracking), the find loop ~0.2 s; 1.5 s leaves room for a loaded box
        # (a 1.0 s bound with a second, linear-anyway case flaked once at load 3)
        t0 = time.monotonic()
        ol.message_is_mainly_english("„" * 100000)
        self.assertLess(time.monotonic() - t0, 1.5)


class TestRound2Prompt(unittest.TestCase):
    def test_english_prompt_with_a_short_slovak_tail_stays_english(self):
        for p in EN_PROMPTS_WITH_SLOVAK_TAIL:
            with self.subTest(prompt=p[:28]):
                self.assertEqual(ol.prompt_language(p), "en")


class TestRound2Transcript(unittest.TestCase):
    def _eval(self, t, msg=EN_ANSWER):
        return ol.evaluate(_payload(msg, t.write()))[0]

    def _queued(self, t, text, kind=None):
        att = {"type": "queued_command", "prompt": text, "commandMode": "prompt"}
        if kind is not None:
            att["origin"] = {"kind": kind}
        return t._add({"type": "attachment", "attachment": att})

    def test_a_queued_human_message_is_a_prompt(self):
        t = _Transcript(self).prompt("Please check the card reader and the logs")
        self._queued(t.tool(), "slovensky!!! preco pises po anglicky?", "human")
        self.assertEqual(self._eval(t.tool().assistant(EN_ANSWER)), "block")

    def test_queued_wakes_and_machine_text_do_not_end_the_owner_turn(self):
        # an absorbed message does not START a turn: the owner's prompt decides
        for kind, text in (("channel", '<channel source="plugin:peer:peer" from="x">'
                                       'the deploy is done</channel>'),
                           ("task-notification", "<task-notification>done</task-notification>"),
                           (None, "stuck-check: the goal arm failed, check the session"),
                           (None, "<system-reminder>\nThe user changed a file.\n"
                                  "</system-reminder>")):
            with self.subTest(kind=kind, text=text[:20]):
                t = _Transcript(self).prompt(OWNER_PROMPTS[0])
                self._queued(t.tool(), text, kind)
                self.assertEqual(self._eval(t.tool().assistant(EN_ANSWER)), "block")

    def test_a_long_tool_result_line_is_read_in_linear_time(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        path = t.tool("y" * (24 * 1024 * 1024)).assistant(EN_ANSWER).write()
        t0 = time.monotonic()
        self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "block")
        self.assertLess(time.monotonic() - t0, 1.5)


class TestRound2Marker(unittest.TestCase):
    """Review round 2 🔴: the block marker must never eat the ping of the
    accepted Slovak rewrite."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="i1379-r2-")
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sid = "i1379-r2-%d-%s" % (os.getpid(), self.id().rsplit(".", 1)[-1])
        self.marker = "/tmp/airuleset-owner-language-block-" + self.sid
        self.addCleanup(lambda: os.path.lexists(self.marker) and os.remove(self.marker))

    def _payload(self, msg, active=False):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(msg)
        p = _payload(msg, t.write(), active=active)
        p["session_id"] = self.sid
        return json.dumps(p)

    def test_an_allowed_stop_clears_the_marker(self):
        self.assertEqual(ol.run(self._payload(EN_ANSWER)), 2)
        self.assertTrue(os.path.exists(self.marker))
        self.assertEqual(ol.run(self._payload(SK_APOLOGY, active=True)), 0)
        self.assertFalse(os.path.lexists(self.marker))
        Path(self.marker).write_text("1")
        self.assertEqual(ol.run(self._payload(SK_APOLOGY)), 0)
        self.assertFalse(os.path.lexists(self.marker))

    def test_a_planted_symlink_is_never_written_through(self):
        target = Path(self.home) / "victim"
        target.write_text("keep")
        os.symlink(target, self.marker)
        self.assertEqual(ol.run(self._payload(EN_ANSWER)), 2)
        self.assertEqual(target.read_text(), "keep")

    def test_the_budget_path_is_journalled(self):
        with mock.patch.object(ol, "SCAN_BUDGET_S", -1.0):
            self.assertEqual(ol.run(self._payload(EN_ANSWER)), 0)
        log = Path(self.home) / "devel" / "airuleset" / "audits" / ol.GATE_LOG
        self.assertIn("owner-language:budget", log.read_text(encoding="utf-8"))


class TestRound2Notify(_NotifyBase):
    REWRITE = ("**Otázka — projekt airuleset (správa pravidiel):** Brána potrebuje "
               "rozhodnutie o veľkosti okna, kým budem pokračovať s testami.\n\n"
               "- Nechať 32 MiB (odporúčam) — pokryje dlhý ťah\n"
               "- Použiť 8 MiB — lacnejšie\n\n"
               "❓ NEEDS YOU: akú veľkosť okna má brána použiť?")

    def test_the_slovak_rewrite_after_a_block_is_pinged(self):
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": "",
               "AIRULESET_SHARED_CHANNEL_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        t = _Transcript(self).prompt(OWNER_PROMPTS[0]).assistant(self.QUESTION)
        draft = _payload(self.QUESTION, t.write(), cwd=str(self.home))
        draft["session_id"] = self.sid
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(draft), text=True,
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 2, r.stderr)
        rewrite = dict(draft, last_assistant_message=self.REWRITE, stop_hook_active=True)
        stdin = json.dumps(rewrite)
        r = subprocess.run(["bash", str(HOOK)], input=stdin, text=True,
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run(["bash", str(self.PENDING)], input=stdin, text=True,
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        qmap = self.home / ".claude" / "discord-questions.json"
        self.assertTrue(qmap.exists() and "137901" in qmap.read_text(),
                        (self.home / ".claude" / "notify-delivery.log").read_text())


class TestRound2Leaf(unittest.TestCase):
    def test_compact_reads_the_discord_prefixes_from_the_leaf(self):
        import machine_prompts as mp
        from watchdog import compact
        self.assertIs(compact._COMPACT_DISCORD_ANSWER_PREFIXES, mp.DISCORD_ANSWER_PREFIXES)


if __name__ == "__main__":
    unittest.main()
