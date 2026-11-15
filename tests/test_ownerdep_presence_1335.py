"""#1335 — an owner-dependent step never sits in prose while U stays 0 (owner
15.11.2026: "Opakovany a opakovany problem!!! Do u nejdu veci ktore tam maju ist").

The dasio 15.11. incident: the final report said "Zostáva izolácia jadier a
vypnutie Wi-Fi/BT, keď bude niekto pri PC. Zapísané na #106", then `⏳ WORKING`;
no `❓`, no `needs-owner-action`, footer U 0. `gates.ownerdep` (#1307) judged only
ticket writes and only approval/answer verbs. It now also judges the FINAL
message, and its phrase set covers a step that waits on a person at a machine.
A line passes when the owner's court already holds it (the footer cache: its
`#N` in U, or U > 0 for a line naming no ticket), when it names another project's
ticket / session, when the message asks (`❓`), or when the turn adds a U label.

The positive / negative lines are real final-message lines measured on dev1
(17 accounts, 14 days, ticket comment on #1335). Fixtures only.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import hermetic_hook_env, sweep_session_files  # noqa: E402
from test_ownerdep_1307 import COMMENTED, _Transcript  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-question-quality.sh"

DASIO = ("- **dsg:** bridge na MiniFuse beží. Za hodinu mal jeden výpadok o 16:32 "
         "(33 blokov). Test nechávam bežať cez noc. Zostáva izolácia jadier a "
         "vypnutie Wi-Fi/BT, keď bude niekto pri PC. Zapísané na #106 (bridge pre "
         "SoundGrid).")
DASIO_MSG = DASIO + "\n\n⏳ WORKING: test beží cez noc"
NO_REF = "Zapnúť ju vie len niekto pri počítači.\n\n✅ DONE: karta je vypnutá"

STEPS = (
    DASIO,
    "Zapnúť ju vie len niekto pri počítači.",
    "Na diaľku iem-snv už späť nedostaneme. Niekto pri ňom ho musí vypnúť a zapnúť.",
    "Treba, aby niekto na mieste skontroloval router alebo internet.",
    "Treba ju fyzicky skontrolovať: napájanie a kábel.",
    "- #179: treba fyzicky skontrolovať iem-snv.",
    "Do routra nemám prístup, musíš to spraviť ty alebo mi ho dať.",
    "Krok s licenciou Live potrebuje tvoje prihlásenie na ableton.com.",
    "Inštalácia na FOH čaká, kým bude niekto pri ňom.",
    "Remaining: power-cycle the NIC once someone is at the box.",
    "Someone needs to physically replug the Dante cable.",
    "The owner must reboot the router manually.",
    "This needs a physical power-cycle of the switch.",
)
NOT_STEPS = (
    "O 9:25 niekto pri PC reštartoval počítač cez ponuku Štart.",
    "LAN fungovala znova o 00:37:13 a nikto pri počítači nemusel byť.",
    "Ak pri tom bude niekto pri FOH, nech počúva PA.",
    "Každý z týchto mutantov som ručne vložil do kódu a testy ho zachytili.",
    "Tlačiareň fyzicky visí na USB na Pam-pc.",
    "Prvý by som preveril fyzicky.",
    "Check your Discord channel - you should see the test messages.",
    "Musíš ale súhlasiť s tým, že Dante multicast pôjde na všetky porty.",
    "Ty len zapoj sieť a napájanie, pri počítači nemusíš nič robiť.",
    "**Na tebe to teraz nestojí.** Záloha beží.",
    "Fyzický papier z USB tlačiarne overiť nevieme.",
    "A physical NIC was replaced last week; nothing is pending.",
    "Gate example: `keď bude niekto pri PC` is the dasio shape.",
    # measured FP: an audit REPORTING what a ticket line said (dcontroller,
    # iemmixer, wall-bridge 11.-14.11.)
    "Audit našiel riadok v #46, ktorý písal, že vytvorenie toku potrebuje tvoj súhlas.",
    "Hľadal som v nich zápis, že práca čaká na tvoje schválenie, rozhodnutie alebo odpoveď.",
    "Airuleset požiadal o kontrolu, či v niektorom nestojí, že práca čaká na tvoj súhlas.",
)
# measured: another session asks the owner (songplayer, dstack 12.-14.11.)
ELSEWHERE = (
    "#314 som zaparkoval (čaká na tvoj súhlas, ktorý pýtala camera-box).",
    "#240 (VBAN linky) čaká na tvoje odpovede, ktoré zbiera dasio.",
    "Zvyšok #240 čaká na tvoje odpovede, ktoré zisťuje dasio.",
)


def _cache(home, cwd, u_numbers=(), u_total=None, ping_refs=()):
    """The footer cache the gate reads (statusbar.cache_dir / cwd_key)."""
    import statusbar
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    nums = list(u_numbers)
    body = {"ts": time.time(), "user_waiting": len(nums) if u_total is None else u_total,
            "user_waiting_numbers": nums, "question_cover_numbers": nums,
            "question_cover_rows": nums + list(ping_refs)}
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(body), encoding="utf-8")
    if ping_refs:
        qmap = {"q1": {"cwd": cwd, "question": "otázka k #%d" % ping_refs[0],
                       "ts": time.time()}}
        (Path(home) / ".claude" / "discord-questions.json").write_text(
            json.dumps(qmap), encoding="utf-8")


def _gate():
    return importlib.import_module("gates.ownerdep")


class TestStepText(unittest.TestCase):
    def test_real_step_lines_match(self):
        for text in STEPS:
            self.assertTrue(_gate().owner_dependency(text), text)

    def test_measured_false_positives_never_match(self):
        for text in NOT_STEPS:
            self.assertEqual(_gate().owner_dependency(text), "", text)

    def test_another_session_asking_is_held(self):
        g = _gate()
        for text in ELSEWHERE:
            self.assertTrue(g.owner_dependency(text), text)
            self.assertTrue(g.held_in_u(text, (set(), 0, "songplayer")), text)

    def test_long_line_is_linear(self):
        text = ("keď bude " + "x" * 60_000 + " niekto pri ") * 3
        t0 = time.monotonic()
        _gate().owner_dependency(text)
        self.assertLess(time.monotonic() - t0, 1.0)


class TestFinal(unittest.TestCase):
    """The final message itself, no ticket write in the turn."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="od1335h-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        self.t = _Transcript(self).prompt("prior").prompt()
        self.cwd = os.path.join(self.t.dir, "dasio")
        os.makedirs(self.cwd)

    def _eval(self, message, u_numbers=(), u_total=None, cache=True, ping_refs=()):
        if cache:
            _cache(self.home, self.cwd, u_numbers, u_total, ping_refs)
        return _gate().evaluate(self.t.write(), message, self.cwd, home=self.home)

    def test_dasio_report_blocks_with_u_zero(self):
        verdict, reason = self._eval(DASIO_MSG)
        self.assertEqual(verdict, "block")
        for token in ("1335", "#106", "needs-owner-action", "❓ ASKED", "Otázka — projekt",
                      "airuleset:owner-dep-ok"):
            self.assertIn(token, reason)

    def test_asked_question_settles(self):
        msg = DASIO + "\n\n❓ ASKED: izoláciu jadier spravíš ty?\n\n⏳ WORKING: test"
        self.assertEqual(self._eval(msg)[0], "allow")

    def test_needs_you_settles(self):
        self.assertEqual(self._eval(DASIO + "\n\n❓ NEEDS YOU: kedy budeš pri PC?")[0],
                         "allow")

    def test_u_label_this_turn_settles(self):
        self.t.bash("gh issue edit 106 --add-label needs-owner-action", "")
        self.assertEqual(self._eval(DASIO_MSG)[0], "allow")

    def test_failed_label_does_not_settle(self):
        self.t.bash("gh issue edit 106 --add-label needs-owner-action", "HTTP 403", error=True)
        self.assertEqual(self._eval(DASIO_MSG)[0], "block")

    def test_ticket_already_in_u_passes(self):
        self.assertEqual(self._eval(DASIO_MSG, u_numbers=(106,))[0], "allow")

    def test_other_ticket_in_u_does_not_cover(self):
        self.assertEqual(self._eval(DASIO_MSG, u_numbers=(24,))[0], "block")

    def test_delivered_ping_on_the_ticket_covers(self):
        self.assertEqual(self._eval(DASIO_MSG, ping_refs=(106,))[0], "allow")

    def test_line_without_ref_passes_only_when_u_nonzero(self):
        self.assertEqual(self._eval(NO_REF, u_total=0)[0], "block")
        self.assertEqual(self._eval(NO_REF, u_numbers=(24,))[0], "allow")

    def test_own_repo_qualified_ref_counts_as_this_repo(self):
        msg = DASIO_MSG.replace("na #106", "na dasio#106")
        self.assertEqual(self._eval(msg)[0], "block")
        self.assertEqual(self._eval(msg, u_numbers=(106,))[0], "allow")

    def test_other_project_or_session_is_not_ours(self):
        for msg in ("Mikrotik ho má v zbynekdrlik/network#5 a čaká na tvoje schválenie.",
                    "Vrátenie čaká na tvoj súhlas v relácii mikrotik.",
                    "Oprava čaká na tvoje rozhodnutie, camera-box sa ťa na to pýta vo "
                    "svojom okne (camera-box issue 1482)."):
            self.assertEqual(self._eval(msg + "\n\n✅ DONE: x")[0], "allow", msg)

    def test_no_cache_fails_open(self):
        self.assertEqual(self._eval(DASIO_MSG, cache=False)[0], "allow")

    def test_bypass_line_passes(self):
        msg = DASIO + "\n\nairuleset:owner-dep-ok krok robí dasio sám cez MCP\n\n✅ DONE: x"
        self.assertEqual(self._eval(msg)[0], "allow")

    def test_clean_report_passes(self):
        self.assertEqual(self._eval("Nasadené v0.1.80, testy zelené.\n\n✅ DONE: x")[0],
                         "allow")


class TestComment(unittest.TestCase):
    """Gate 2: a ticket comment naming a presence step owes the label this turn."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="od1335c-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        self.t = _Transcript(self).prompt()
        self.body = "Zostáva izolácia jadier, keď bude niekto pri PC."

    def _eval(self, u_numbers=()):
        _cache(self.home, self.t.dir, u_numbers)
        return _gate().evaluate(self.t.write(), "Zapísané.\n\n✅ DONE: x", self.t.dir,
                                home=self.home)

    def test_presence_comment_unlabelled_blocks(self):
        self.t.bash("gh issue comment 106 --body %s" % json.dumps(self.body, ensure_ascii=False), COMMENTED)
        verdict, reason = self._eval()
        self.assertEqual(verdict, "block")
        self.assertIn("#106", reason)

    def test_same_command_label_settles(self):
        self.t.bash("gh issue comment 106 --body %s && gh issue edit 106 --add-label "
                    "needs-owner-action" % json.dumps(self.body, ensure_ascii=False), COMMENTED)
        self.assertEqual(self._eval()[0], "allow")

    def test_comment_on_ticket_already_in_u_passes(self):
        self.t.bash("gh issue comment 106 --body %s" % json.dumps(self.body, ensure_ascii=False), COMMENTED)
        self.assertEqual(self._eval(u_numbers=(106,))[0], "allow")


class TestHook(unittest.TestCase):
    """Driven through the real Stop hook (prefilter + python + rc 2)."""

    def _run(self, message, u_numbers=()):
        t = _Transcript(self).prompt()
        sid = "od1335-" + uuid.uuid4().hex[:12]
        self.addCleanup(sweep_session_files, sid)
        self.addCleanup(lambda: Path("/tmp/airuleset-owner-dep-block-" + sid).unlink(
            missing_ok=True))
        env = hermetic_hook_env(self)
        _cache(env["HOME"], t.dir, u_numbers)
        payload = {"last_assistant_message": message, "session_id": sid,
                   "transcript_path": t.write(), "cwd": t.dir}
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=120)

    def test_dasio_report_is_blocked(self):
        r = self._run(DASIO_MSG)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("1335", r.stderr)

    def test_report_with_ticket_in_u_passes(self):
        r = self._run(DASIO_MSG, u_numbers=(106,))
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
