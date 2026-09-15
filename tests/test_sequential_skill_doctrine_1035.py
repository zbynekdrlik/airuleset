"""#1035 — the autopilot SKILL BODY must honour a `sequential` pane's concurrency
mode, not ONLY the armed `/goal` line.

Owner directive (2026-09-15, verbatim on the issue): sequential mode is NOT a
hard subagent count (reviewers / validators / consults are fine); the ban is on
(a) uncoordinated saturation — two workers on similar things without knowing of
each other (the gk-infra failure) — and (b) ANY rule / nudge / skill text that
PUSHES the maximum lane count. A lower cadence that keeps moving forward is the
point when tokens must be saved.

Defect this locks against: `skills/autopilot/SKILL.md` was authored as
PARALLEL / continuous-refill doctrine throughout, and the pane's resolved mode
was honoured ONLY inside the `goal-arm --self` step (the armed `/goal` variant).
A session running WITHOUT an armed goal (`david3@subdev` after a restart, "po
reštarte … bežím na tvoj pokračuj") reads ONLY the body and dispatched FIVE
background lanes on a `sequential` box (2026-09-15).

The fix: a mode-aware Step 3.0 gate at the TOP of the per-cycle procedure whose
SEQUENTIAL dispatch block carries the byte-identical `goal_registry`
sequential clause (the SAME source the sequential `/goal` variant uses —
`goal-inventory --check` locks the two, so an unarmed body can never disagree
with the armed goal line), plus mode qualifiers on the parallel-push anchors and
on the always-on lane-fill module surface.

Prose grep-locks use the whitespace-collapsing `window()` technique from
`tests/test_fleet_concurrency_doctrine.py` (a markdown line-wrap must never
break an `assertIn` on a freshly-added statement — this repo's own playbook).
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry as gr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AUTOPILOT = "skills/autopilot/SKILL.md"
TOOLING = "modules/core/claude-code-tooling.md"

# The PARALLEL-PUSH tokens the owner asked never to appear UNSCOPED — the same
# family the ticket names (`refill|saturat|keep .* lanes live|sized to what the
# box`). Case-insensitive.
PUSH_RE = re.compile(
    r"refill|saturat|keep .* lanes live|sized to what the box",
    re.IGNORECASE,
)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def window(text, start, end):
    """Whitespace-collapsed slice between two anchors."""
    i = text.index(start)
    j = text.index(end, i)
    return " ".join(text[i:j].split())


class TestByteIdenticalSequentialClause(TestCase):
    """(2a) The skill body carries the canonical sequential clause VERBATIM —
    rendered from the SAME `goal_registry` source the `/goal` sequential variant
    uses, never a hand-written paraphrase."""

    def test_skill_carries_the_byte_identical_registry_clause(self):
        self.assertIn(gr._SEQUENTIAL_SATURATION, read(AUTOPILOT))

    def test_goal_registry_exposes_the_skill_sequential_lock(self):
        # The lock function `goal-inventory --check` calls (the natural home).
        self.assertTrue(hasattr(gr, "skill_sequential_drift"))
        self.assertEqual(gr.skill_sequential_drift(read(AUTOPILOT)), [])

    def test_skill_sequential_drift_flags_a_missing_clause(self):
        # A body WITHOUT the canonical clause must be flagged (not silently ok).
        self.assertNotEqual(gr.skill_sequential_drift("no clause here"), [])


class TestStep30ModeGate(TestCase):
    """The mode-aware gate sits at the TOP of the per-cycle procedure and reads
    the ONE resolver, before any parallel/refill doctrine."""

    def test_gate_precedes_the_parallel_doctrine(self):
        t = read(AUTOPILOT)
        i_step3 = t.index("## Step 3 — Per-issue cycle")
        self.assertIn("Concurrency MODE gate", t)
        i_gate = t.index("Concurrency MODE gate", i_step3)
        i_parallel = t.index("Each loop turn works the backlog", i_step3)
        self.assertLess(i_gate, i_parallel,
                        "the Step 3.0 mode gate must come BEFORE the parallel "
                        "refill doctrine")

    def test_gate_reads_the_one_resolver(self):
        w = window(read(AUTOPILOT), "Concurrency MODE gate",
                   "SEQUENTIAL dispatch block")
        self.assertIn("airuleset.py status", w)
        self.assertIn("concurrency:", w)
        self.assertIn("resolve_concurrency", w)

    def test_gate_globally_scopes_refill_to_parallel(self):
        w = window(read(AUTOPILOT), "Concurrency MODE gate",
                   "SEQUENTIAL dispatch block")
        # The single sentence that scopes EVERY refill line for a sequential pane.
        self.assertIn("IGNORE every refill", w)
        self.assertIn("PARALLEL mode only", w)

    def test_gate_names_the_unarmed_defect(self):
        w = window(read(AUTOPILOT), "Concurrency MODE gate",
                   "SEQUENTIAL dispatch block")
        self.assertIn("whether or not a `/goal` is armed", w)


class TestSequentialBlockHasNoPushDoctrine(TestCase):
    """(2b) The SEQUENTIAL dispatch block carries NONE of the parallel-push
    tokens (the canonical clause's own "no refill" is exempted — it is the
    registry source, and it says the OPPOSITE of a push)."""

    def test_block_has_no_parallel_push_words(self):
        block = window(read(AUTOPILOT), "SEQUENTIAL dispatch block",
                       "PARALLEL mode (default)")
        scan = block.replace(gr._SEQUENTIAL_SATURATION, "")
        m = PUSH_RE.search(scan)
        self.assertIsNone(
            m, "SEQUENTIAL block carries a parallel-push token: %r"
            % (m.group(0) if m else None))

    def test_block_says_more_than_one_subagent_is_fine(self):
        block = window(read(AUTOPILOT), "SEQUENTIAL dispatch block",
                       "PARALLEL mode (default)")
        self.assertIn("NOT a hard subagent count", block)
        self.assertIn("ticket-validator", block)

    def test_block_names_the_998_backstop(self):
        block = window(read(AUTOPILOT), "SEQUENTIAL dispatch block",
                       "PARALLEL mode (default)")
        self.assertIn("block-dispatch-over-wdrain.sh", block)


class TestParallelPushAnchorsAreModeScoped(TestCase):
    """(2c) Every SECTION that pushes lane saturation carries a mode qualifier,
    so no reader in any section reads the push as unconditional."""

    def _assert_mode_scoped(self, w):
        self.assertIn("PARALLEL mode", w)
        self.assertIn("sequential", w.lower())
        self.assertIn("Step 3.0", w)

    def test_header_blurb_is_mode_scoped(self):
        self._assert_mode_scoped(
            window(read(AUTOPILOT), "Solves the **ENTIRE**",
                   "Each unit is handed"))

    def test_how_it_works_engine_bullet_is_mode_scoped(self):
        self._assert_mode_scoped(
            window(read(AUTOPILOT), "**Engine = a `/goal` loop",
                   "**Bundling AND parallel"))

    def test_bundling_fleet_bullet_is_mode_scoped(self):
        # #1035 review LOW-2: the "Bundling AND parallel fleet dispatch" bullet
        # sits BEFORE Step 3.0, so the top-down global IGNORE line never reaches
        # it — it needs its OWN qualifier like its sibling engine bullet above.
        self._assert_mode_scoped(
            window(read(AUTOPILOT), "**Bundling AND parallel fleet dispatch",
                   "**Worker = in-session BACKGROUND"))

    def test_step3_parallel_doctrine_is_headed_parallel_mode(self):
        self._assert_mode_scoped(
            window(read(AUTOPILOT), "PARALLEL mode (default)",
                   "Each loop turn works the backlog"))

    def test_guardrails_summary_is_mode_scoped(self):
        self._assert_mode_scoped(
            window(read(AUTOPILOT),
                   "Serial INTEGRATION per repo, CONTINUOUS-REFILL",
                   "Independent verification is mandatory"))


class TestAlwaysOnLaneFillSurfacesModeScoped(TestCase):
    """(3) The always-on module that tells a session to fill lanes is qualified
    with mode != sequential."""

    def test_claude_code_tooling_parallel_lanes_is_mode_scoped(self):
        # Line-based (robust to a markdown wrap / a nearby word trim) — the
        # assertion is unchanged: the Parallelism lane-fill line carries the
        # mode qualifier.
        lines = [ln for ln in read(TOOLING).splitlines()
                 if "Parallelism is the working model" in ln]
        self.assertTrue(lines, "the Parallelism lane-fill line is missing")
        line = lines[0]
        self.assertIn("sequential", line.lower())
        self.assertIn("1035", line)


if __name__ == "__main__":
    main()
