"""#1157 slice 3 — a machine nudge reaches the pane only as ONE pointer row.

Owner, 26.9.2026: „nechapem ten nudge odrolovanie, to tu uz bolo tolko krat
riesene! preco zasa!!!" Every earlier fix (#746, #1022, #1092, #1023/#1104,
slices 1-2) patched one shape of the same mechanism: a long multi-line nudge
paragraph typed into Claude Code's input box and verified by reading the
rendered, wrapping, scrolling box back. ROZHODNUTÉ (issuecomment-5841333507):
every machine nudge kind except the `/goal` arm is typed as ONE short line
`nudge: [<kind>] <headline> — celý text: ~/.claude/nudges/<file>`; the full text
goes to that file first. The `/goal` arm, `/compact` and the owner's own reply
are typed unchanged.

Fakes only here (no tmux, no gh, no Discord); the real private-tmux proof at the
incident geometry is tests/test_nudge_pointer_tmux_1157.py.
"""
import json
import os
import stat
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402,F401
import watchdog as wd  # noqa: E402
from watchdog import send_outcome  # noqa: E402
from watchdog import tmux_io  # noqa: E402

from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP  # noqa: E402
from test_send_verified_undo_1157 import partition_batch_text  # noqa: E402

PID = "%9"
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"
NOW = 1_790_000_000

# Every kind `send_verified` delivers as a MACHINE nudge: the stageable
# PRIORITY set plus the RECOVERY revivals whose text is not a slash command.
POINTER_KINDS = sorted((tmux_io.MACHINE_NUDGE_KINDS | tmux_io.RECOVERY_NUDGE_KINDS)
                       - {"goal-arm", "goal-disarm", "compact"})


def _nf():
    from watchdog import nudge_file
    return nudge_file


def _long_text(kind):
    """A realistic long multi-sentence nudge (~800 chars, wraps to 5+ rows)."""
    return ("stuck-check: %s — pätička hlási I=7, W=12 parked a deploy okno je "
            "otvorené. Over labely `/goal` slučky, prejdi ops-wait tikety "
            "#100 #101 #102 #103 #104 #105 #106 #107 #108 #109 #110 #111 a "
            "pre každý rozhodni, či ešte čaká na tretiu stranu. Potom "
            "re-audituj (gated → workable) každý, ktorého podmienka už "
            "neplatí, a zapíš dôkaz na tiket. Ak release landed pre #5 #6 #7 "
            "#8, zatvor ich s citáciou verzie. Nič nerob naslepo; každé "
            "rozhodnutie zapíš na tiket v tom istom turne a pokračuj ďalším "
            "tiketom, kým nie je partícia čistá. Toto je dlhý odsek, ktorý "
            "sa v úzkom okne zalomí a odroluje, presne ako 18:26 incident "
            "v gk-infra. Koniec." % kind)


class _WidthFake(DeliverGoalFakeTmux):
    """The stateful box model plus tmux's real `#{pane_width}` answer; the box
    wraps at that width, so a payload wider than one row renders 2+ rows."""

    def __init__(self, *a, width=176, **kw):
        kw.setdefault("wrap_width", width)
        super().__init__(*a, **kw)
        self.width = width

    def __call__(self, argv, timeout=8):
        if "display-message" in argv and argv[-1] == "#{pane_width}":
            return "%d\n" % self.width
        return super().__call__(argv, timeout)

    def literals(self):
        return [a[-1] for a in self.sent if "-l" in a]


class _Base(unittest.TestCase):

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name)
        env = m.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AIRULESET_NUDGE_FILE_DIR", None)   # the real default dir
        self.ndir = self.home / ".claude" / "nudges"
        self.tpath = self.home / "sess.jsonl"
        self.tpath.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")

    def _fake(self, width=176, **kw):
        return _WidthFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                          model_type=True, transcript_path=self.tpath,
                          width=width, **kw)

    def _send(self, fake, text, **kw):
        logs = []
        res = wd.send_verified(PID, text, fake, self.tpath, sleep_fn=lambda _s: None,
                               logs=logs, **kw)
        return res, logs

    def _submitted(self):
        rows = [json.loads(ln) for ln in self.tpath.read_text().splitlines()]
        return [r["message"]["content"] for r in rows if r["type"] == "user"]

    def _files(self):
        return sorted(self.ndir.glob("*.md")) if self.ndir.is_dir() else []


class EveryMachineKindIsOnePointerRow(_Base):
    """The renderers keep their texts; the ONE chokepoint turns each into a
    one-row pointer whose file holds the original byte-identically."""

    def test_every_kind_types_one_row_and_the_file_holds_the_text(self):
        self.assertIn("partition-audit", POINTER_KINDS)
        self.assertIn("resume", POINTER_KINDS)
        for kind in POINTER_KINDS:
            with self.subTest(kind=kind):
                for f in self._files():
                    f.unlink()
                text = (partition_batch_text() if kind == "partition-audit"
                        else _long_text(kind))
                fake = self._fake(width=176)
                res, logs = self._send(fake, text, nudge=kind, state={}, now=NOW)
                self.assertTrue(res, logs)
                sub = self._submitted()[-1]
                self.assertTrue(sub.startswith("nudge: "), sub)
                self.assertNotIn("\n", sub)
                self.assertLessEqual(_nf().cells(sub), min(100, 176 - 6), sub)
                self.assertIn("— celý text: ~/.claude/nudges/", sub)
                # the pane only ever received the pointer, never the paragraph
                self.assertEqual(fake.literals(), [sub], fake.literals())
                files = self._files()
                self.assertEqual(len(files), 1, files)
                self.assertTrue(files[0].name.startswith(kind + "-"), files)
                self.assertTrue(sub.endswith("~/.claude/nudges/" + files[0].name))
                self.assertEqual(files[0].read_bytes(), text.encode("utf-8"))
                self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)

    def test_the_incident_batch_is_one_row_in_the_incident_width(self):
        text = partition_batch_text()
        self.assertGreater(len(text), 700)
        fake = self._fake(width=176, visible_rows=3)
        res, logs = self._send(fake, text, nudge="partition-audit", state={},
                               now=NOW)
        self.assertTrue(res, logs)
        self.assertEqual(len(fake.literals()), 1)
        self.assertTrue(any("nudge-file partition-audit ->" in ln for ln in logs),
                        logs)

    def test_narrow_pane_still_gets_one_row(self):
        for kind in POINTER_KINDS:
            with self.subTest(kind=kind):
                fake = self._fake(width=80)
                res, logs = self._send(fake, _long_text(kind), nudge=kind,
                                       state={}, now=NOW)
                self.assertTrue(res, logs)
                sub = self._submitted()[-1]
                self.assertLessEqual(_nf().cells(sub), 80 - 6, sub)
                self.assertTrue(_nf().is_pointer_line(sub, require_file=True), sub)

    def test_the_headline_is_the_first_sentence_cut_on_a_word(self):
        fake = self._fake(width=176)
        text = ("report-owed: #41 (money gate) — napíš ## ✅ Work Complete a "
                "pošli kartu. Druhá veta sem nepatrí.")
        res, logs = self._send(fake, text, nudge="card", state={}, now=NOW)
        self.assertTrue(res, logs)
        sub = self._submitted()[-1]
        self.assertTrue(sub.startswith("nudge: [card] #41 (money gate)"), sub)
        self.assertNotIn("Druhá veta", sub)
        head = sub[len("nudge: [card] "):sub.index(" — celý text:")]
        self.assertTrue(text.startswith("report-owed: " + head.rstrip("…")), head)


class ExemptTextsAreTypedUnchanged(_Base):

    def _assert_unchanged(self, text, **kw):
        fake = self._fake(width=176)
        res, logs = self._send(fake, text, state={}, now=NOW, **kw)
        self.assertEqual("".join(fake.literals()), text, logs)
        self.assertEqual(self._files(), [])
        return res, logs

    def test_goal_arm_is_typed_in_full(self):
        text = "/goal " + "all issues closed AND CI green AND PR clean " * 12
        self._assert_unchanged(text.strip(), nudge="goal-arm")

    def test_goal_disarm_and_compact_are_unchanged(self):
        self._assert_unchanged("/goal clear", nudge="goal-disarm")
        self._assert_unchanged("/compact", nudge="compact")

    def test_a_slash_command_under_a_machine_kind_is_unchanged(self):
        self._assert_unchanged("/goal ship the release train", nudge="goal-sweep")

    def test_the_owners_own_reply_is_unchanged(self):
        reply = ("Odpoveď z Discordu: " + "áno, sprav to takto a nie inak. " * 10
                 ).strip()
        self._assert_unchanged(reply, user_authored=True)
        self.assertEqual(self._submitted(), [reply])


class PointerLineUnit(_Base):

    def test_the_line_never_exceeds_the_budget_at_any_width(self):
        nf = _nf()
        shown = "~/.claude/nudges/partition-audit-260926182600-a1b2.md"
        for width in range(80, 260, 7):
            for kind in POINTER_KINDS:
                line = nf.pointer_line(kind, _long_text(kind),
                                       shown.replace("partition-audit", kind),
                                       width)
                self.assertLessEqual(nf.cells(line), min(100, width - 6),
                                     (width, line))

    def test_is_pointer_line_rejects_appended_words_and_foreign_paths(self):
        nf = _nf()
        path = nf.write("card", "obsah")
        line = nf.pointer_line("card", "obsah", nf.display_path(path), 176)
        self.assertTrue(nf.is_pointer_line(line, require_file=True))
        self.assertFalse(nf.is_pointer_line(line + " a este toto"))
        self.assertFalse(nf.is_pointer_line(
            line.replace("~/.claude/nudges/", "/tmp/")))
        self.assertFalse(nf.is_pointer_line(line.replace("[card]", "[bounce]")))
        os.unlink(path)
        self.assertFalse(nf.is_pointer_line(line, require_file=True))

    def test_old_files_are_pruned_on_write_and_foreign_files_kept(self):
        nf = _nf()
        old = nf.write("card", "stary", now=NOW - 8 * 86400)
        os.utime(old, (NOW - 8 * 86400, NOW - 8 * 86400))
        fresh = nf.write("card", "novy", now=NOW - 3600)
        os.utime(fresh, (NOW - 3600, NOW - 3600))
        foreign = self.ndir / "poznamky.md"
        foreign.write_text("moje")
        nf.write("bounce", "dalsi", now=NOW)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(foreign.exists())
        self.assertEqual(stat.S_IMODE(self.ndir.stat().st_mode) & 0o077, 0)


class RecognisersAcceptThePointer(_Base):
    """Slice 2's cleanup and the batch caller's undo still know our leftover."""

    def _box(self, line):
        return "● Hotovo.\n\n" + "─" * 176 + "\n❯\xa0" + line + "\n" + "─" * 176 + \
            "\n  ⏸ manual mode on\n"

    def _pointer(self, kind, text):
        nf = _nf()
        path = nf.write(kind, text)
        return nf.pointer_line(kind, text, nf.display_path(path), 176)

    def test_own_stuck_content_accepts_the_pointer(self):
        self.assertTrue(wd._looks_like_own_stuck_content(
            self._pointer("card", _long_text("card"))))

    def test_box_is_ours_with_the_long_original_text(self):
        # the batch caller undoes with its ORIGINAL composite text (`_bt`)
        text = partition_batch_text()
        cap = self._box(self._pointer("partition-audit", text))
        self.assertTrue(send_outcome._box_is_ours(cap, text))
        self.assertFalse(send_outcome._box_is_ours(
            self._box(self._pointer("card", "x") + " a moje slova"), text))

    def test_presend_proves_a_pointer_of_any_kind(self):
        cap = self._box(self._pointer("card", _long_text("card")))
        self.assertEqual(send_outcome._presend_own(PID, cap, {}, time.time()),
                         "shape")

    def test_a_pointer_prompt_is_never_a_human_prompt(self):
        entry = {"type": "user", "message": {
            "content": self._pointer("resume", "continue")}}
        self.assertFalse(wd._is_genuine_human_prompt(entry))

    def test_the_dead_worker_ack_reads_through_the_pointer(self):
        wid = "4f0c2d8e-1111-2222-3333-444455556666"
        text = ("stuck-check: %s (api-error v subagents/%s.jsonl) — over jeho "
                "transcript." % (tmux_io._subagent_nudge_signature(wid), wid))
        rows = [{"type": "user", "message": {
                    "content": self._pointer("subagent-stuck", text)}},
                {"type": "assistant", "message": {"content": "Pozriem worker."}}]
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertTrue(wd.supervisor_responded_to_nudge(
            str(self.tpath), tmux_io._subagent_nudge_signature(wid)))


if __name__ == "__main__":
    unittest.main()
