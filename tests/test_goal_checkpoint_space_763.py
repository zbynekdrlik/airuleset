"""#763 — the #746/#747 head-checkpoint verifies the chunk `text[:120]` against
the STRIPPED box read, and on ALL THREE real templates that slice ends with a
trailing space (`'...MY '`) — so `_typed_landed(chunk, stripped_box)`'s
`chunk.endswith(stripped_box)` is False for a PERFECTLY typed chunk, the
checkpoint settles CORRUPT before the (whitespace-normalised, would-pass)
head-is-prefix check runs, the undo+retype loop burns its retries, and every
long-/goal delivery ends `skip:verify-failed` → fleet-wide ZERO `SEND typed`
since the v0.1.95 deploy (live repro: dev1 `goaltest` session, 2026-08-30).

The #746/#747 fixtures dodged exactly this: they deliberately built payloads
with no trailing whitespace (the #720(3) lesson) but applied it only to the
WHOLE text — an arbitrary 120-char slice re-creates the trailing space, and
the real templates carry one. These tests drive BOTH shapes:

  * a synthetic payload whose [:120] boundary lands on a space (hermetic), and
  * every REAL rendered template (`goal_template_for_authority`) end-to-end
    through the same stateful scrolling fake — so a future template edit that
    moves the boundary can never silently re-break delivery.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog.goal as goal  # noqa: E402
from watchdog import stash  # noqa: E402
from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP  # noqa: E402

PID = "%9"
WRAP = 60
VIS = 20


def _space_boundary_goal():
    """A scroll-length, NON-periodic payload whose [:120] slice ends with a
    space — the exact real-template shape (#763). Position 119 is ' ' by
    construction (asserted in the shape test below); the whole payload still
    ends clean (no trailing whitespace — that is not the bug under test)."""
    base = ("/goal STOP CONDITIONS - the loop is DONE the moment EITHER of "
            "them both holds, each checkable from transcript by ME ONLY")
    assert len(base) >= 119
    head = base[:119] + " "
    assert len(head) == 120 and head.endswith(" "), (len(head), head[-3:])
    body = " ".join(
        "clause-%02d work the backlog one ticket at a time until every "
        "workable issue %02d is done and verified" % (i, i)
        for i in range(24))
    return (head + body).strip()


GOAL_SPACE = _space_boundary_goal()


def _tpath(testcase):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "sess.jsonl"
    p.write_text(json.dumps(
        {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
    return p


def _submitted_turns(p):
    out = []
    for ln in p.read_text().splitlines():
        e = json.loads(ln)
        if e.get("type") == "user":
            out.append(e["message"]["content"])
    return out


def _fake(testcase, p):
    return DeliverGoalFakeTmux(
        [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
        transcript_path=p, wrap_width=WRAP, visible_rows=VIS)


class ChunkBoundarySpaceShape(unittest.TestCase):
    """Pin the defect's precondition so the fixtures cannot silently drift
    into the boundary-not-on-a-space shape that dodged the bug in #746."""

    def test_synthetic_payload_chunk_ends_with_space(self):
        chunk = GOAL_SPACE[:stash.GOAL_TYPE_CHECKPOINT_CHARS]
        self.assertTrue(chunk.endswith(" "),
                        "fixture must reproduce the space-boundary shape")
        self.assertGreaterEqual(len(GOAL_SPACE),
                                stash.GOAL_TYPE_SCROLL_CHECKPOINT_THRESHOLD)
        self.assertLess(len(GOAL_SPACE), 4000)

    def test_typed_landed_is_the_failing_primitive_on_a_stripped_read(self):
        # The pure-function proof from the live diagnosis: a stripped box read
        # of a space-terminated chunk fails the suffix contract. This is a
        # CHARACTERIZATION of the primitive (its contract is unchanged by
        # #763 — the fix normalises the checkpoint's verify REFERENCE instead).
        chunk = GOAL_SPACE[:stash.GOAL_TYPE_CHECKPOINT_CHARS]
        self.assertFalse(stash._typed_landed(chunk, chunk.strip()))


class SpaceBoundaryChunkStillDelivers(unittest.TestCase):
    """The #763 regression proper: a clean type whose checkpoint chunk ends in
    a space must verify LANDED and the /goal must arm."""

    def test_type_literal_verified_passes_on_space_boundary_chunk(self):
        p = _tpath(self)
        tmux = _fake(self, p)
        ok = stash._type_literal_verified(PID, tmux, GOAL_SPACE,
                                          sleep_fn=lambda *_a: None)
        self.assertTrue(
            ok, "a clean type whose [:120] chunk ends in a space was misread "
                "CORRUPT (the #763 trailing-space checkpoint defect)")

    def test_send_goal_verified_arms_and_submits_byte_exact(self):
        p = _tpath(self)
        tmux = _fake(self, p)
        ok = goal._send_goal_verified(PID, GOAL_SPACE, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok, "space-boundary /goal must arm, not skip")
        self.assertIn(GOAL_SPACE, _submitted_turns(p))


class EveryRealTemplateDelivers(unittest.TestCase):
    """The fleet-facing lock: every REAL rendered template must pass
    `_type_literal_verified` through the scrolling fake, whatever its current
    [:120] boundary character is. This is the net that catches ANY future
    template edit whose chunk boundary re-lands on whitespace."""

    def test_all_authority_templates_type_verified(self):
        # Review-fix (#763 🔴): resolve templates from the REPO source, never
        # the installed ~/.claude copy — CI runs with a fresh HOME (no
        # installed skills), and the repo copy is the `goal-inventory --write`
        # render source the installed one is pushed from, so this locks the
        # actual fleet-facing artifact.
        skill = REPO / "skills" / "autopilot" / "SKILL.md"
        for auth in ("full", "branch-merge", "fork-no-merge"):
            text = goal.goal_template_for_authority(auth, path=skill)
            self.assertTrue(text, "template for %s must resolve" % auth)
            p = _tpath(self)
            tmux = _fake(self, p)
            ok = stash._type_literal_verified(PID, tmux, text,
                                             sleep_fn=lambda *_a: None)
            self.assertTrue(
                ok, "REAL %s template failed type-verify (chunk boundary: %r)"
                    % (auth, text[:stash.GOAL_TYPE_CHECKPOINT_CHARS][-3:]))


if __name__ == "__main__":
    unittest.main()
