"""#740 — a genuine re-poke (no user input since the last ask, same question)
must NOT force the full self-contained `**Otázka — projekt …:**` block back
into the turn. Live incident (odoo-erp / david2, 2026-08-28): a session
blocked on one unanswered question re-wrote the ENTIRE block 8 times over one
night — the phone was only pinged once (delivery-side dedup already worked),
but every re-poke still burned a full turn re-emitting text the owner had
already seen, and cluttered the chat/terminal with 8 identical walls.

`message-status-marker.md` / `user-questions-slovak.md` said to repeat the
`❓` **line** verbatim, but never said the rest of the block could be
OMITTED — so sessions defaulted to re-writing the whole thing out of an
abundance of caution. This file proves the enforcement machinery never
needed that: both real hooks already key their bypass/dedup on the bare
marker-LINE text alone, and both already accept (and correctly handle) a
turn that carries ONLY `❓ NEEDS YOU: <text>` with no briefing/options at
all — end to end:

  1. `hooks/stop-check-question-quality.sh` — the "VERBATIM REPEAT ... PASS
     without shape checks" bypass matches on the bare marker line and does
     NOT require the block to be present.
  2. `hooks/notify-discord-pending.sh` — a terse marker-only repeat is
     delivered exactly like a full one on the FIRST ask (the block is
     synthesized from context when present, or falls back to the marker line
     alone when there is none to pull), and is SUPPRESSED (no second
     Discord POST, no second `discord-questions.json` entry) on an identical
     terse repeat, exactly like a full-block repeat already was.

No hook code changed for #740 — this is a doctrine-lock test proving the
existing mechanism already supports (and safely handles) the terser form the
doctrine now mandates for a genuine re-poke.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import new_hook_sid  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"
GATE = ROOT / "hooks" / "stop-check-question-quality.sh"

FULL_MSG = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Pri tickete #740 je otvorené jedno rozhodnutie o formáte re-poke otázky "
    "a potrebujem tvoje potvrdenie.\n"
    "\n"
    "- Terse marker-only re-poke (odporúčam) — bez opakovania celého bloku\n"
    "- Ponechať plný blok pri každom prebudení — viac kontextu, viac šumu\n"
    "\n"
    "❓ NEEDS YOU: mám pri re-poke vypisovať len holý riadok otázky?")

# The bare marker line the message above ends with, WITHOUT the "❓" glyph or
# the "NEEDS YOU:" label — exactly what strip_md()/KEYLINE derive as the
# dedup key on BOTH hooks (notify-discord-pending.sh's send_q, and
# stop-check-question-quality.sh's verbatim-repeat bypass).
BARE_QUESTION_TEXT = "mám pri re-poke vypisovať len holý riadok otázky?"

# The terse re-poke turn #740 wants to be legal: ONLY the marker line, no
# briefing, no options — nothing else in the whole message.
TERSE_MSG = "❓ NEEDS YOU: " + BARE_QUESTION_TEXT


def _path_with_fake_curl(http_code="200", msg_id="740000111"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-740-"))
    fake = d / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '%s'\n" % (msg_id, http_code))
    fake.chmod(0o755)
    return str(d), str(d) + os.pathsep + os.environ.get("PATH", "")


class TerseRepokeIsSafe(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-740-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-740-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")

    def _sid(self):
        return new_hook_sid(self, "test-740-repoke",
                            ["*test-740-repoke-*"])

    def _fire_pending(self, sid, msg, msg_id="740000111"):
        curl_dir, path = _path_with_fake_curl(msg_id=msg_id)
        self.addCleanup(shutil.rmtree, curl_dir, True)
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": str(self.cwd)})
        env = {**os.environ, "HOME": str(self.home), "PATH": path,
              "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "",
              "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        return subprocess.run(["bash", str(PENDING)], input=payload,
                              text=True, capture_output=True, env=env)

    def _run_gate(self, msg, sid):
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        return subprocess.run(["bash", str(GATE)], input=payload,
                              capture_output=True, text=True)

    def _questions_map(self):
        p = self.home / ".claude" / "discord-questions.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def test_terse_repoke_passes_the_quality_gate_after_a_full_first_ask(self):
        # Turn 1: the FULL self-contained question is asked and delivered —
        # this is the real first-ask shape and seeds LASTQ exactly as the
        # live pending hook would.
        sid = self._sid()
        r1 = self._fire_pending(sid, FULL_MSG, msg_id="740000111")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn("740000111", self._questions_map(), self._questions_map())

        # Turn 2: a genuine re-poke (no user input in between) writes ONLY
        # the bare marker line — #740's new doctrine. The quality gate must
        # NOT demand the briefing/options back; the verbatim-repeat bypass
        # must fire on the bare marker line alone.
        r2 = self._run_gate(TERSE_MSG, sid)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotIn('"block"', r2.stdout, r2.stdout)

    def test_terse_repoke_is_deduped_at_delivery_not_re_posted(self):
        # Same two-turn shape, but through the ACTUAL delivery hook: turn 1
        # (full block) delivers once; turn 2 (terse repeat) must NOT create
        # a second discord-questions.json entry or re-POST to Discord.
        sid = self._sid()
        r1 = self._fire_pending(sid, FULL_MSG, msg_id="740000222")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        qmap_after_first = self._questions_map()
        self.assertIn("740000222", qmap_after_first, qmap_after_first)

        r2 = self._fire_pending(sid, TERSE_MSG, msg_id="740000333")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        qmap_after_second = self._questions_map()
        # No NEW message id was recorded — the terse repeat never reached a
        # confirmed POST at all (dedup short-circuited it).
        self.assertNotIn("740000333", qmap_after_second, qmap_after_second)
        self.assertEqual(qmap_after_first, qmap_after_second)

    def test_a_bare_terse_first_ask_with_no_prior_lastq_still_gets_full_shape_checked(self):
        # Guard rail: the terse form is legal ONLY as a genuine repeat of an
        # already-delivered question. A bare `❓ NEEDS YOU: ...` with NO
        # matching LASTQ (i.e. this would be a brand-new, never-before-asked
        # question written in terse form) must still be rejected by the
        # quality gate for missing a briefing — #740 narrows re-poke
        # behavior, it does not relax the FIRST-ask template requirement.
        sid = self._sid()
        r = self._run_gate(TERSE_MSG, sid)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"block"', r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
