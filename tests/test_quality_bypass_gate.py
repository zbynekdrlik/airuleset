"""The quality-bypass family is HARD-blocked, not merely warned (#92 item 4).

#92 item 4 mandated a PER-PHRASE check of which banned phrases the Stop hook
actually matches before trimming any module to a pointer (the repo's #14
lesson: hooks parse the assistant's OUTPUT, not module text). Running that
check surfaced a real enforcement gap instead:

`autonomous-quality-discipline.md` claimed its "your call" / merge-bypass /
"functionally ready" / "merge despite" / "informational check" family was
"HARD-blocked at Stop by stop-check-prose-violations.sh". It was not. The
block printed `VIOLATION:` to stderr and never called `add_hard`, so Stop was
never prevented — and a non-blocking Stop hook's stderr is not fed back to the
model, so the message stood uncorrected. Sibling families on the SAME hook
(tester-handoff, completion-report structure) have always been hard.

The fix splits the family by AMBIGUITY, which is why it was left soft in the
first place:

  * UNAMBIGUOUS bypass shapes -> HARD. `admin-merge`, `bypass branch
    protection`, `merge despite <x>`, `close the PR and roll into the next
    one`, `good enough to merge`, `functionally ready`, `won't claim it's
    clean`, `UNSTABLE ... merge`, `project precedent ... merged`,
    `investigate ... or merge despite`. Each names a merge/gate bypass
    explicitly; there is no innocent reading.
  * AMBIGUOUS delegation phrases stay SOFT. Bare `your call`, `you decide`,
    `your decision`, `cheaper option`, `quicker option`, `easier path` carry
    real non-bypass uses ("the cheaper option is a smaller VM"), so hard-
    blocking them would gate honest messages. They keep the stderr warning.

Both halves keep firing; only the consequence differs. The module text is
corrected to state exactly which half is hard, so the claim matches reality.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"
MODULE = ROOT / "modules" / "core" / "autonomous-quality-discipline.md"


def run(msg):
    sid = f"qbypass-{uuid.uuid4().hex[:8]}"
    retry = f"/tmp/airuleset-stop-block-{sid}"
    if os.path.exists(retry):
        os.remove(retry)
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    r = subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                       capture_output=True, timeout=30)
    if os.path.exists(retry):
        os.remove(retry)
    return r


def blocked(r):
    return '"decision": "block"' in r.stdout or '"decision":"block"' in r.stdout


def warned(r):
    return "VIOLATION" in r.stderr


class TestUnambiguousBypassIsHardBlocked(TestCase):
    CASES = [
        "All gates green except codecov. I'll admin-merge it.",
        "Simplest path here is to bypass branch protection for this one.",
        "The PR is ready — I'll merge despite the failing advisory check.",
        "Let's close the PR and roll the fix into the next PR instead.",
        "Coverage is a bit under but it's good enough to merge.",
        "The PR is functionally ready, so I'm proceeding.",
        "It's mergeable but I won't claim it's clean.",
        "mergeStateStatus is UNSTABLE — I'll merge it anyway.",
        "Project precedent: PR #41 was merged with the same failing check.",
        "Want me to investigate the codecov drop, or merge despite it?",
    ]

    def test_each_unambiguous_shape_blocks_stop(self):
        for msg in self.CASES:
            with self.subTest(msg=msg):
                r = run(msg)
                self.assertTrue(blocked(r),
                                f"must HARD-block, only warned: {msg!r}\n{r.stdout}")


class TestAmbiguousDelegationStaysSoft(TestCase):
    """These carry innocent readings, so they warn but must never block."""

    CASES = [
        "Both designs work equally well — your call.",
        "You decide which of the two label wordings reads better.",
        "The cheaper option is a smaller VM; both meet the latency target.",
        "The easier path is the built-in helper, and it is also the correct one.",
    ]

    def test_each_ambiguous_phrase_warns_but_does_not_block(self):
        for msg in self.CASES:
            with self.subTest(msg=msg):
                r = run(msg)
                self.assertFalse(blocked(r), f"must NOT block: {msg!r}")
                self.assertTrue(warned(r), f"must still warn: {msg!r}")


class TestCleanMessagesArePassedThrough(TestCase):
    def test_normal_engineering_message_is_untouched(self):
        r = run("Codecov flagged a 0.3% drop. Root cause was an untested "
                "branch in parse_row; added a case, gate is green, merged.")
        self.assertFalse(blocked(r))
        self.assertFalse(warned(r))


class TestModuleClaimMatchesReality(TestCase):
    def test_module_no_longer_claims_the_whole_family_is_hard(self):
        t = MODULE.read_text(encoding="utf-8")
        self.assertNotIn(
            'This "your call" / merge-bypass / "functionally ready" / "merge despite" / '
            '"informational check" family is HARD-blocked at Stop', t,
            "the module must not restate the false blanket claim")

    def test_module_states_which_half_is_hard(self):
        t = MODULE.read_text(encoding="utf-8")
        self.assertIn("stop-check-prose-violations.sh", t)
        # the split must be visible to the reader, not just to the hook
        self.assertRegex(t, r"(?i)unambiguous[^\n]*(hard|block)")
        self.assertRegex(t, r"(?i)(warn|soft)[^\n]*(never block|not blocked)")


if __name__ == "__main__":
    main()
