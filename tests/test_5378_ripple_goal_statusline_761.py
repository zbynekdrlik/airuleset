"""#761 (round 2 of the odoo-erp#5378 ripple): scope-qualify the char-capped
`/goal` STOP CONDITIONS and the statusline #636 close-doctrine so the two
surfaces round 1 (#759) could not touch inline stop asserting the superseded
"the stream never closes / the gatekeeper closes it" absolute.

Round 1 landed the exception verbatim on completion-report.md /
pr-merge-policy/SKILL.md / autopilot-worker.md:
    "EXCEPT on odoo-erp, where after the gk review-verdict + every queue label
     dropped the delivering STREAM self-closes with an evidence `--comment`,
     odoo-erp#5378 / #756".

Round 2 = the char-capped `/goal` fork-no-merge profile + the always-on
statusline #636 clause. The `/goal` is char-BUDGETED (fork-no-merge headroom was
only 162, below the #621/#730 AIM of >=180), so the fix is a true-making REWORD
of the two fork-no-merge clauses (net-negative chars) + the odoo-erp HOW in the
surrounding UNCAPPED prose (the #730 escape), NOT an appended qualifier.

Actor distinction (verified): the self-close is the STREAM's (the `/goal` LOOP),
never the dispatched WORKER's — worker-prose ("never a self-close" etc.) stays
literally TRUE on every repo and is content-locked elsewhere; it is NOT touched.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import goal_registry as gr  # noqa: E402

SKILL = "skills/autopilot/SKILL.md"
STATUSLINE = "modules/core/statusline-vocabulary.md"
STATUSLINE_DEEP2 = "skills/statusline-vocabulary-deep/DEEP-2.md"
MIN_HEADROOM_AIM = 180  # #621/#730 aim (150 is the hard floor)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def fork_goal_line():
    """The rendered fork-no-merge `/goal` line as shipped in SKILL.md (one
    physical line; the file order is full, branch-merge, fork-no-merge)."""
    lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(SKILL), re.MULTILINE)
    assert len(lines) == 3, lines
    return lines[2]


class TestForkGoalNoLongerAssertsFalseAbsolute(TestCase):
    """The char-capped fork-no-merge `/goal` must stop asserting the now-FALSE
    absolutes (odoo-erp streams self-close) and instead carry literally-true
    reworded clauses. Asserted on BOTH the registry render (authoring source)
    and the shipped SKILL.md line (rendered artifact) so a drift can never hide
    the regression on either side."""

    def test_registry_render_drops_the_false_absolutes(self):
        fk = gr.render("fork-no-merge")
        self.assertNotIn("never close the issue", fk)
        self.assertNotIn("the maintainer's job", fk)

    def test_registry_render_carries_the_true_making_rewords(self):
        fk = gr.render("fork-no-merge")
        self.assertIn("a later close is not my (B) proof", fk)
        self.assertIn("close only per authority", fk)

    def test_shipped_skill_line_matches(self):
        fk = fork_goal_line()
        self.assertNotIn("never close the issue", fk)
        self.assertNotIn("the maintainer's job", fk)
        self.assertIn("a later close is not my (B) proof", fk)
        self.assertIn("close only per authority", fk)

    def test_review_watch_and_never_blocks_survive(self):
        # the #395 REVIEW-WATCH lifecycle + "never blocks" disclaimer are
        # untouched by the true-making reword (regression guard).
        fk = fork_goal_line()
        self.assertIn("REVIEW-WATCH", fk)
        self.assertIn("never blocks", fk)


class TestForkGoalHeadroomImproved(TestCase):
    """The reword is NET-NEGATIVE on chars, so fork-no-merge headroom must reach
    the >=180 AIM (it was 162) -- the safe direction for CC's ~4000 stored cap."""

    def test_fork_headroom_meets_the_aim(self):
        hr = gr.headroom("fork-no-merge")
        self.assertGreaterEqual(
            hr, MIN_HEADROOM_AIM,
            "fork-no-merge /goal headroom %d < aim %d" % (hr, MIN_HEADROOM_AIM))

    def test_no_profile_over_budget(self):
        for p in gr.PROFILES:
            self.assertFalse(gr.over_budget(p), "%s over the /goal cap" % p)


class TestUncappedProseCarriesTheOdooErpHow(TestCase):
    """The odoo-erp self-close HOW lives in the UNCAPPED prose: the
    `**AUTHORITY: fork-no-merge**` header annotation (which describes the STREAM
    loop's authority) + the statusline #636 clause -- verbatim-consistent with
    round 1 so the four surfaces cannot drift apart."""

    def test_fork_header_annotation_carries_the_exception(self):
        t = read(SKILL)
        idx = t.index("**AUTHORITY: fork-no-merge**")
        end = t.index("```", idx)  # up to the goal fence that follows
        header = t[idx:end]
        self.assertIn("odoo-erp#5378", header)
        self.assertIn("delivering STREAM", header)

    def test_statusline_636_clause_carries_the_exception(self):
        t = read(STATUSLINE_DEEP2)  # #859 batch 3: moved to companion
        idx = t.index("GK-blocked ops-wait = post-release limbo (#636")
        window = t[idx:idx + 2500]
        # the stale routing-outcome phrase stays (it is still where the ticket
        # goes) but is now scope-qualified for odoo-erp.
        self.assertIn("gatekeeper ho zavrie", window)
        self.assertIn("odoo-erp#5378", window)


if __name__ == "__main__":
    main()
