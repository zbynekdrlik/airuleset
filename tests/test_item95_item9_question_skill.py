"""#95 item 9 (2026-08-09) — middle path for `user-questions-slovak.md`
(~4344 tok always-on, per issue #95's own audit).

User decision (comment 5230653165): shrink the always-on module to a short
core (Slovak requirement + the self-contained gate — the QUALITATIVE half
nothing mechanically checks) and move the long examples/rationale VERBATIM
(conversion, never deletion) to a new skill wired into the ALREADY-registered
`AskUserQuestion` PreToolUse matcher (extending `settings/hooks.json` +
`hooks/situational-triggers.conf` — no new hook, FREEZE-compatible).

Every phrase the PRE-EXISTING tests already lock in
`modules/core/user-questions-slovak.md` (test_airuleset.py,
test_question_policy.py) MUST still be present after the shrink — this file
re-asserts the same set defensively, so a future trim of the core doesn't
silently break those tests without an obvious local failure first.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent

CORE = ROOT / "modules" / "core" / "user-questions-slovak.md"
SKILL = ROOT / "skills" / "user-questions-slovak" / "SKILL.md"
CONF = ROOT / "hooks" / "situational-triggers.conf"
HOOKS_JSON = ROOT / "settings" / "hooks.json"
INJECT_HOOK = ROOT / "hooks" / "inject-situational-rule.sh"

# The ORIGINAL file's byte size (measured before this change) was 18218.
# "rádovo stovky tokenov" (order-of-hundreds) at this file's own established
# ~4.2 chars/token ratio (18218 bytes / 4344 tok, per the ticket's own
# estimate) is a few thousand bytes; the hard-locked phrases from the
# PRE-EXISTING tests below make an exact "few hundred tokens" unreachable
# without breaking those tests, so this asserts a real, substantial cut
# (well under half the original) rather than an arbitrary round number.
MAX_CORE_BYTES = 9000


def read(rel_or_path):
    p = rel_or_path if isinstance(rel_or_path, Path) else ROOT / rel_or_path
    return p.read_text(encoding="utf-8")


class TestCoreModuleShrunk(TestCase):
    def test_core_module_is_genuinely_shorter(self):
        size = len(CORE.read_bytes())
        self.assertLess(
            size, MAX_CORE_BYTES,
            f"core module is {size} bytes, still not a short always-on core",
        )

    def test_core_module_still_carries_every_pre_existing_locked_phrase(self):
        # Mirrors test_airuleset.py::test_user_questions_slovak_rule_present
        # and test_question_policy.py's several assertions against this
        # SAME file -- defensive re-check so a future trim fails HERE with
        # an obvious name, not only in an unrelated test file.
        t = read(CORE)
        for phrase in [
            "SLOVAK",
            "AskUserQuestion",
            "NEVER a bare number or range",
            "one decision at a time",
            "ZERO context",
            "cross-reference",
            "restreamer",
            "60",
            "UNLIMITED",
            "NANOVO a CELÁ",
            "zákaz odvolávok do histórie",
            "VERBATIM, byte-identical",
            "ANY conversation happened in between",
            "pýtal som sa skôr",
            "jediné otvorené rozhodnutie je X",
            "stop-check-question-quality.sh",
        ]:
            self.assertIn(phrase, t, f"core module lost locked phrase: {phrase!r}")

    def test_core_module_points_at_the_skill(self):
        t = read(CORE)
        self.assertIn("user-questions-slovak", t)

    def test_core_module_still_carries_the_final_rewordings_clause(self):
        # #95 item 11 leaves every "applies to all rewordings" clause
        # untouched -- this module's own is not to be dropped as a side
        # effect of the item 9 trim.
        t = read(CORE)
        self.assertIn("Applies to all rewordings and semantic equivalents", t)


class TestSkillCarriesTheMovedContentVerbatim(TestCase):
    def test_skill_file_exists(self):
        self.assertTrue(SKILL.is_file(), "skills/user-questions-slovak/SKILL.md missing")

    def test_skill_has_the_hook_enforced_template_section(self):
        t = read(SKILL)
        self.assertIn("Povinná ŠTRUKTÚRA otázky", t)
        self.assertIn("**Otázka — projekt <meno>", t)
        self.assertIn("ONE ❓ ping = ONE decision", t)

    def test_skill_has_both_worked_anti_pattern_pairs(self):
        t = read(SKILL)
        self.assertIn("Anti-pattern #2", t)
        self.assertIn("Correct #2", t)
        self.assertIn("Anti-pattern — English + jargon", t)
        # the restreamer worked example's own reaction quote, moved verbatim
        self.assertIn("nerozumiem!!!", t)
        # the FB-push jargon worked example, moved verbatim
        self.assertIn("FB-push E2E gate", t)

    def test_skill_has_tickets_and_small_parts_sections_verbatim(self):
        t = read(SKILL)
        self.assertIn("Tickets in a question — explain EACH", t)
        self.assertIn("Ask in SMALL parts", t)
        self.assertIn("#684", t)  # the range worked example

    def test_skill_has_the_away_user_delivery_section_verbatim(self):
        t = read(SKILL)
        self.assertIn("60-second", t)
        self.assertIn("UNLIMITED", t)
        self.assertIn("codex-bridge", t)


class TestAskUserQuestionWiring(TestCase):
    def setUp(self):
        # #95 item 9's own real-call test needs the SAME per-test TMPDIR
        # isolation test_situational_injection.py's TestInjection already
        # uses — the injector's once-per-session dedup marker lives under
        # $TMPDIR/airuleset-situational-<session_id>/, so a fixed session
        # id with no isolation collides across repeated pytest invocations
        # (the second run sees the first run's marker and correctly
        # returns nothing — a real dedup, not a bug, but it makes THIS
        # test flaky/order-dependent without its own scratch TMPDIR).
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _rows(self):
        rows = []
        for line in CONF.read_text(encoding="utf-8").splitlines():
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p for p in line.split("\t") if p != ""]
            if len(parts) not in (4, 5):
                continue
            rows.append(tuple(parts[:4]))
        return rows

    def test_conf_has_an_askuserquestion_row_pointing_at_the_skill(self):
        rows = self._rows()
        matches = [r for r in rows if r[1] == "AskUserQuestion"]
        self.assertTrue(matches, "no situational-triggers.conf row binds AskUserQuestion")
        self.assertTrue(
            any(r[3] == "skills/user-questions-slovak/SKILL.md" for r in matches),
            "no AskUserQuestion row points at the new skill",
        )

    def test_hooks_json_wires_inject_situational_rule_on_askuserquestion(self):
        conf = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        entry = None
        for e in conf["hooks"]["PreToolUse"]:
            if e.get("matcher") == "AskUserQuestion":
                entry = e
                break
        self.assertIsNotNone(entry, "no AskUserQuestion PreToolUse matcher entry at all")
        cmds = [h.get("command", "") for h in entry.get("hooks", [])]
        self.assertTrue(
            any("inject-situational-rule.sh" in c for c in cmds),
            "inject-situational-rule.sh is not wired onto the AskUserQuestion matcher",
        )
        # the pre-existing hook must still be there too — this is an
        # EXTENSION of the matcher, never a replacement.
        self.assertTrue(
            any("pre-ask-auto-answer.sh" in c for c in cmds),
            "pre-ask-auto-answer.sh must stay wired — this is an addition, not a swap",
        )

    def test_skill_names_includes_the_new_skill(self):
        import sys

        sys.path.insert(0, str(ROOT))
        import airuleset

        self.assertIn("user-questions-slovak", airuleset.SKILL_NAMES)

    def test_a_real_askuserquestion_call_injects_the_skill_body(self):
        payload = json.dumps(
            {
                "session_id": "sess-item9-real",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Ktorý prístup zvoliť?",
                            "header": "Prístup",
                            "options": [{"label": "A"}, {"label": "B"}],
                        }
                    ]
                },
            }
        )
        env = dict(os.environ, TMPDIR=self.tmpdir)
        r = subprocess.run(
            ["bash", str(INJECT_HOOK)], input=payload, capture_output=True, text=True, env=env
        )
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip(), "AskUserQuestion call injected nothing")
        data = json.loads(r.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Povinná ŠTRUKTÚRA otázky", ctx)


if __name__ == "__main__":
    main()
