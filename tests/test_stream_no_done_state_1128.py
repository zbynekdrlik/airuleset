"""#1128 — a sub-dev STREAM `/goal` loop has NO backlog-empty done-state.

Regression (owner report 2026-09-23, david1-4 idle overnight): the reduced
`fork-no-merge` / `branch-merge` templates carried a (B) SLICE EMPTY stop that
held on `slice-quals --count` == 0 with `gk N`/`U N`/`W N` declared "parked,
never blocks 🏁". A stream whose own `I` was 0 while the gatekeeper held its
hand-off therefore ACHIEVED, the /goal evaluator ended the loop, and a later gk
bounce / client reply / new stream ticket waited for the owner.

Owner ruling: a stream loop never ends on an empty slice. The reduced profiles
LOSE (B) and gain ONE idle clause: keep a single background
`airuleset.py stream-wait` (wakes on any change to the stream's slice) and end
⏳ WORKING. The full-authority profiles are byte-unchanged.
"""

import hashlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry as gr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REDUCED = ("branch-merge", "fork-no-merge")
IDLE = ("keep exactly ONE background "
        "`python3 ~/devel/airuleset/airuleset.py stream-wait` live")

# sha256[:24] of every FULL-authority variant as rendered BEFORE #1128 (the
# design's acceptance: "the full profiles are byte-unchanged").
FULL_GOLDEN = {
    ("full", "parallel", None): "2d9a5539f472a22cda96177c",
    ("full", "sequential", None): "6920ca09926869d6679eb0cc",
    ("full", "sequential", "infra"): "e3836d97e467eafe428a92ec",
    ("full", "sequential", "quality"): "77c5cb76bf02783ad940d6bb",
    ("full", "parallel", "review"): "a3b487a9e9cf0c9fd5ddd8a8",
}


def reduced_variants():
    for a in REDUCED:
        for m, r in (("parallel", None), ("sequential", None),
                     ("sequential", "infra")):
            yield (a, m, r), gr.render_goal_line(a, m, r)


class TestStreamLoopHasNoDoneState(unittest.TestCase):
    def test_no_reduced_variant_carries_a_backlog_empty_stop(self):
        for key, line in reduced_variants():
            for tok in ("(B)", "🏁", "BACKLOG EMPTY —", "SLICE EMPTY",
                        "PROVEN IN THIS TURN", "never blocks 🏁"):
                self.assertNotIn(tok, line, "%s still carries %r" % (key, tok))

    def test_every_reduced_variant_idles_on_one_stream_wait(self):
        for key, line in reduced_variants():
            self.assertIn("NO BACKLOG-EMPTY END", line, key)
            self.assertIn(IDLE, line, key)
            self.assertIn("relaunch it if gone", line, key)
            self.assertIn("end ⏳ WORKING", line, key)
            self.assertIn("Only I end this loop (`/goal clear`)", line, key)

    def test_the_idle_gate_is_the_slice_and_its_unhandled_bounces(self):
        for key, line in reduced_variants():
            i_count = line.index("slice-quals --count` prints 0")
            i_bounce = line.index("slice-quals --bounces --unhandled` prints nothing")
            i_wait = line.index(IDLE)
            self.assertLess(i_count, i_wait, key)
            self.assertLess(i_bounce, i_wait, key)

    def test_the_foreground_review_watch_is_replaced(self):
        for key, line in reduced_variants():
            self.assertNotIn("REVIEW-WATCH", line, key)
            self.assertNotIn("FOREGROUND sleep-poll", line, key)

    def test_the_autopilot_signature_header_is_kept(self):
        # watchdog/goal.py's ours-vs-foreign classifier (#623/#878/#1113) keys
        # on this header — a stream loop must stay recognisably OURS.
        for key, line in reduced_variants():
            self.assertTrue(line.startswith(
                "/goal STOP CONDITIONS — the loop is DONE the moment EITHER "
                "holds"), key)

    def test_the_stream_stops_left_are_a_and_the_irreversible_clause(self):
        for key, line in reduced_variants():
            self.assertIn("(A) BLOCKED ON MY ANSWER", line, key)
            self.assertIn("Also stop for a genuinely-irreversible approval", line, key)


class TestFullProfilesByteUnchanged(unittest.TestCase):
    def test_full_variants_match_the_pre_1128_golden(self):
        for (a, m, r), want in FULL_GOLDEN.items():
            got = hashlib.sha256(
                gr.render_goal_line(a, m, r).encode()).hexdigest()[:24]
            self.assertEqual(got, want, "%s/%s/%s changed" % (a, m, r))

    def test_full_keeps_its_b_proof(self):
        line = gr.render("full")
        self.assertIn("🏁 BACKLOG EMPTY: 0 open, main green", line)
        self.assertIn("core-quals --count", line)


class TestRegistryLocksTheRuling(unittest.TestCase):
    def test_reduced_profiles_require_stream_idle_and_no_b_clause(self):
        for p in REDUCED:
            req = gr.REQUIRED_BY_PROFILE[p]
            self.assertIn("stream-idle", req)
            for b in ("stop-b-header", "obligation", "proof", "produce-proof",
                      "done-never", "how-to-tell", "cannot-tell"):
                self.assertNotIn(b, req, (p, b))
                self.assertNotIn(b, gr.clause_ids(p), (p, b))
            self.assertEqual(gr.missing_required(p), [])

    def test_variant_check_is_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_variant_check_catches_a_reintroduced_done_state(self):
        # mutation: smuggle a 🏁 back into a reduced clause -> RED check.
        saved = list(gr.CLAUSES)
        try:
            idx = next(i for i, c in enumerate(gr.CLAUSES)
                       if c.id == "stream-idle")
            c = gr.CLAUSES[idx]
            gr.CLAUSES[idx] = gr.Clause(c.id, c.profiles,
                                        c.text + " then write 🏁 and stop.")
            errs = gr.variant_check()
        finally:
            gr.CLAUSES[:] = saved
        self.assertTrue(any("🏁" in e for e in errs), errs)


class TestShippedSkillCarriesTheStreamTemplates(unittest.TestCase):
    def test_shipped_reduced_lines_match_the_registry(self):
        text = (ROOT / "skills" / "autopilot" / "SKILL.md").read_text(
            encoding="utf-8")
        shipped = gr.shipped_lines(text)
        for p in REDUCED:
            self.assertEqual(shipped.get(p), gr.render(p), p)
            self.assertIn(IDLE, shipped[p])

    def test_skill_body_no_longer_sends_a_stream_back_to_review_watch(self):
        text = (ROOT / "skills" / "autopilot" / "SKILL.md").read_text(
            encoding="utf-8")
        flat = " ".join(text.split())
        self.assertFalse("re-entering REVIEW-WATCH (reduced authority)" in flat,
                         "SKILL.md still routes a stream back to REVIEW-WATCH")
        self.assertTrue("stream-wait" in flat, "SKILL.md never names stream-wait")
        self.assertTrue(re.search(r"#1128", flat), "SKILL.md never cites #1128")


if __name__ == "__main__":
    unittest.main()
