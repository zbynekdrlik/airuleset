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
# devbridge's English answers; the issue quotes their first words
# ("Starting now. The terminal will ask for a card …", "You're right, and I'm
# sorry", "The fix for #158 … is stuck").
EN_ANSWER = ("Starting now. The terminal will ask for a card first; once it shows "
             "the amount, tap the card and tell me what the screen says, then I "
             "will check the logs on the cash PC.")
EN_SORRY = ("You're right, and I'm sorry. The fix for #158 is stuck because the "
            "build on the cash PC failed and I did not notice it until now.")
# devbridge's Slovak line after the owner wrote "slovensky!!!" (verbatim).
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

    def test_prompt_beyond_the_scan_window_allows_fast(self):
        t = _Transcript(self).prompt(OWNER_PROMPTS[0])
        blob = "y" * 200000
        for _ in range(int(ol.MAX_SCAN_BYTES / 200000) + 3):
            t.tool(blob)
        t.assistant(EN_ANSWER)
        path = t.write()
        self.assertGreater(os.path.getsize(path), ol.MAX_SCAN_BYTES)
        t0 = time.monotonic()
        self.assertEqual(ol.evaluate(_payload(EN_ANSWER, path))[0], "allow")
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_scan_window_is_bounded(self):
        self.assertLessEqual(ol.MAX_SCAN_BYTES, 4_000_000)


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
                       "stop-check-owner-language.sh", "peer messages"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
