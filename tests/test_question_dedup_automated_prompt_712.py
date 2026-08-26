"""#712 — automated prompts must NOT clear the ❓/✅ dedup or mark the user present.

Incident (owner report 2026-08-26, claude-david thread): the identical ❓ from
odoo-erp-david1 posted HOURLY 23:29→7:40 including the whole sleep window.
Verified root cause (david1 box forensics, issue #712 VALIDATED comment): CC
background *task-notification* re-invocations fire UserPromptSubmit, and
`hooks/clear-question-dedup.sh` treated EVERY UserPromptSubmit as a genuine
human prompt — wiping the LASTQ verbatim-repeat dedup (and LASTOK, #668) and
touching the presence marker each hour, so every re-printed verbatim ❓ posted
as a fresh question. Secondary channel of the same class: watchdog-typed
machine nudges (`lane-check:` / `stuck-check:` / …) arrive through the same
UserPromptSubmit channel.

The contract locked here: ONLY a genuine human prompt clears LASTQ/LASTOK and
stamps `/tmp/claude-user-active-<sid>`. Automated shapes — an empty/absent
`.prompt`, a `<task-notification>` re-invocation, and the machine-nudge
prefixes the codebase already catalogs as "unambiguous OWN payload no human
ever types" (watchdog/stash.py `_JANITOR_OWN_PREFIXES` doctrine) — skip both,
as a LOGGED decision (never a silent drop). Human prompts keep today's
behavior byte-exact, including arbitrary Discord-reply text typed on the
user's behalf (job 7) — an answer MUST re-open the dedup.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import _hook_state_cleanup as hsc               # noqa: E402

CLEAR = ROOT / "hooks" / "clear-question-dedup.sh"

_TASK_NOTIFICATION_PROMPT = (
    "<task-notification>\n<task-id>b3qamzoj1</task-id>\n"
    "<tool-use-id>toolu_015xKDDxoDjhxh43KDK4QetX</tool-use-id>\n"
    "<output-file>/tmp/claude-1000/x/tasks/b3qamzoj1.output</output-file>\n"
    "</task-notification>"
)

# One representative live payload per machine-nudge family the fix must skip
# (heads copied from the real constants: GOAL_LANE_NUDGE_TEXT, WORKING_NUDGE_TEXT,
# cross_stream bounce/gkreq nudges, OAUTH_REVOKED_NUDGE_TEXT).
_MACHINE_NUDGES = [
    "lane-check: backlog=3 OTVORENÝCH tiketov (nie všetky musia byť hneď "
    "rozpracovateľné...), no BEŽÍ 0 dispatched workerov",
    "stuck-check: tvrdíš ⏳ WORKING ale dlho ticho a nebeží žiadny podagent.",
    "bounce-backstop: ticket #5 sa vrátil s prio:bounce — spracuj ho.",
    "gk-request backstop: čaká nevybavený gk-request — pozri frontu.",
    "oauth-resume: práve prebehla rotácia OAuth tokenu — pokračuj v práci.",
]


class _Base(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-712-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    def _sid(self):
        return hsc.new_hook_sid(self, "t712")

    def _lastq(self, sid):
        return Path("/tmp/claude-discord-lastq-%s" % sid)

    def _lastok(self, sid):
        return Path("/tmp/claude-discord-lastok-%s" % sid)

    def _active(self, sid):
        return Path("/tmp/claude-user-active-%s" % sid)

    def _seed(self, sid):
        self._lastq(sid).write_text("❓ NEEDS YOU: akceptuješ prehľady?")
        self._lastok(sid).write_text("hotovo v1")

    def _run(self, sid, prompt=None, include_prompt=True):
        payload = {"session_id": sid}
        if include_prompt:
            payload["prompt"] = prompt if prompt is not None else ""
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(CLEAR)], input=json.dumps(payload),
                           text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _dlog(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""


class AutomatedPromptsDoNotClear(_Base):
    """The #712 fix: automated UserPromptSubmit firings leave the dedup alone."""

    def test_task_notification_reinvocation_does_not_clear_dedup(self):
        # The PROVEN hourly channel (david1): a background task-notification
        # re-invocation fires UserPromptSubmit — it must NOT wipe LASTQ/LASTOK,
        # or every verbatim ❓ re-print posts as a fresh question (9x hourly).
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt=_TASK_NOTIFICATION_PROMPT)
        self.assertTrue(self._lastq(sid).exists(),
                        "task-notification wiped LASTQ — hourly ❓ re-ping (#712)")
        self.assertTrue(self._lastok(sid).exists(),
                        "task-notification wiped LASTOK (#668 sibling)")

    def test_task_notification_does_not_mark_user_present(self):
        # The presence marker feeds stop-check-question-quality, goal_scan's
        # recent-human gate and block-main-implementation — an automated
        # re-invocation claiming "the user is AT the terminal" is a defect on
        # its own (an away user's question would skip the phone-shape gate).
        sid = self._sid()
        self._run(sid, prompt=_TASK_NOTIFICATION_PROMPT)
        self.assertFalse(self._active(sid).exists(),
                         "task-notification stamped the user-present marker")

    def test_machine_nudge_prefixes_do_not_clear(self):
        for nudge in _MACHINE_NUDGES:
            with self.subTest(nudge=nudge.split(":", 1)[0]):
                sid = self._sid()
                self._seed(sid)
                self._run(sid, prompt=nudge)
                self.assertTrue(self._lastq(sid).exists(),
                                "machine nudge wiped LASTQ: %s" % nudge[:30])
                self.assertFalse(self._active(sid).exists(),
                                 "machine nudge stamped user-present: %s"
                                 % nudge[:30])

    def test_task_notification_with_leading_whitespace_still_classified(self):
        # Review hardening: a payload variant delivering the tag after a
        # leading newline/indent must not dodge the classifier
        # (trim-then-prefix — never an anywhere-match, which would over-match
        # a human prompt QUOTING a task-notification).
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt="\n  " + _TASK_NOTIFICATION_PROMPT)
        self.assertTrue(self._lastq(sid).exists(),
                        "leading whitespace dodged the classifier")

    def test_bare_continue_nudge_does_not_clear(self):
        # NUDGE_TEXT — the api-error auto-resume types exactly "continue".
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt="continue")
        self.assertTrue(self._lastq(sid).exists(),
                        "the bare 'continue' auto-resume wiped LASTQ")

    def test_missing_prompt_field_is_not_a_human_clear(self):
        # A human cannot submit an empty prompt in CC — a payload with no
        # usable `.prompt` is an automated firing (or an unreadable payload):
        # the safe direction keeps the dedup (a NEW question always pings
        # regardless — only a VERBATIM repeat stays deduped).
        sid = self._sid()
        self._seed(sid)
        self._run(sid, include_prompt=False)
        self.assertTrue(self._lastq(sid).exists(),
                        "payload without .prompt cleared LASTQ")
        self.assertFalse(self._active(sid).exists())

    def test_auto_skip_is_a_logged_decision_not_silent(self):
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt=_TASK_NOTIFICATION_PROMPT)
        log = self._dlog()
        self.assertIn("promptclear", log,
                      "auto-skip left no delivery-log trace (silent drop)")
        self.assertIn("task-notification", log)


class HumanPromptsKeepTodaysBehavior(_Base):
    """The other half of the contract — the fix must not overreach: a genuine
    human prompt still clears the dedup and stamps presence, byte-exact."""

    def test_human_prompt_still_clears_and_marks_present(self):
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt="pokracuj podla planu a dokonci ticket")
        self.assertFalse(self._lastq(sid).exists(),
                         "a real human prompt must clear LASTQ")
        self.assertFalse(self._lastok(sid).exists(),
                         "a real human prompt must clear LASTOK (#668)")
        self.assertTrue(self._active(sid).exists(),
                        "a real human prompt must stamp the presence marker")

    def test_arbitrary_discord_reply_text_still_clears(self):
        # Watchdog job 7 TYPES the user's Discord answer into the pane — it is
        # a genuine human answer and MUST re-open the dedup (the conversation
        # moved on), even though a machine did the typing.
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt="Ano, akceptujem tie prehlady, spusti to.")
        self.assertFalse(self._lastq(sid).exists(),
                         "a typed Discord answer must clear LASTQ")

    def test_human_prompt_containing_continue_word_still_clears(self):
        # Only the EXACT bare NUDGE_TEXT is automated — a human sentence that
        # merely starts with the word must keep clearing (guards over-broad
        # prefix matching).
        sid = self._sid()
        self._seed(sid)
        self._run(sid, prompt="continue with the plan and merge it")
        self.assertFalse(self._lastq(sid).exists(),
                         "'continue …' sentence misclassified as the nudge")


class PrefixCatalogAntiRot(unittest.TestCase):
    """The hook's bash prefix list deliberately duplicates the python catalog
    (a bash hook cannot import python constants) — this lock keeps the two
    from drifting: every TYPED plain-text nudge prefix the janitor catalog
    names must appear in the hook source."""

    def test_hook_covers_janitor_typed_nudge_prefixes(self):
        from watchdog import stash
        src = CLEAR.read_text()
        typed = [p for p in stash._JANITOR_OWN_PREFIXES
                 if not p.startswith("/")]
        self.assertTrue(typed, "janitor catalog lost its typed-nudge members?")
        for p in typed:
            self.assertIn(p, src,
                          "hook prefix list is missing janitor-cataloged %r" % p)
        self.assertIn("<task-notification>", src,
                      "hook is missing the proven CC task-notification shape")


if __name__ == "__main__":
    unittest.main()
