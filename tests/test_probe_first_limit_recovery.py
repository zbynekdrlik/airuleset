"""#520 — probe-first recovery on a 429 limit-death, never idle-park on the
printed reset time.

Root cause (from the ticket, re-verified against current HEAD before this fix):
on 2026-08-16 sessions across THREE unrelated projects each independently read
Claude Code's `... limit · resets HH:MM` banner as an authoritative "do nothing
until then" fact, set a timer, and idle-parked — one even staying parked AFTER
the limit had already released. No rule text anywhere told them to probe-first;
the existing "never idle-park" texts (`message-status-marker.md` sleep-window,
`skills/autopilot/SKILL.md` goal-loop spin) cover DIFFERENT pathologies.

This file content-locks the fix across the surfaces that carry it:
  - `skills/verify-launched-work-liveness/SKILL.md` — the probe-first recovery
    section + the two limit CLASSES (session-limit vs monthly-spend-cap).
  - `modules/quality/verify-launched-work-liveness.md` — the module-stub pointer.
  - `modules/core/subagent-continuation.md` — the pointer for a worker killed by
    a 429 limit.
  - `watchdog/__init__.py` — the job-4 stuck-check nudge text carries a
    probe-first instruction; the job-6 docstring reconciles wait-for-reset with
    probe-first.
  - `watchdog/decide.py` — `is_account_dispatch_block`'s docstring reconciles the
    two classes with the probe-first doctrine (SINGLE source of truth, the skill
    reconciles WITH it).

Locks follow this repo's proven content-lock shape (#307/#498/#500): a SHORT
single-line anchor + a normalize-then-check window, with `assertTrue(needle in
text, msg)` (never `assertIn`, which would dump the whole file on failure).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKILL_MD = ROOT / "skills" / "verify-launched-work-liveness" / "SKILL.md"
MODULE_MD = ROOT / "modules" / "quality" / "verify-launched-work-liveness.md"
SUBAGENT_MD = ROOT / "modules" / "core" / "subagent-continuation.md"
WATCHDOG_INIT = ROOT / "watchdog" / "__init__.py"
DECIDE_PY = ROOT / "watchdog" / "decide.py"


def _norm(text):
    """Collapse markdown line-wrapping/indentation so a phrase spanning a wrap
    still matches — the established technique this repo's prose locks use."""
    return " ".join(text.split())


def _window(text, anchor, span=1400):
    """A normalized window starting at a SHORT single-line anchor (verified to
    sit on one physical line). Raises if the anchor is absent so a missing
    section fails loudly, not silently."""
    idx = text.index(anchor)
    return _norm(text[idx:idx + span])


class TestSkillProbeFirstSection(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")

    def test_section_header_present(self):
        self.assertTrue(
            "A LIMIT-death is NOT a reason to idle-park" in self.text,
            "the probe-first recovery section header must exist in the skill")

    def test_own_working_turn_is_proof_of_capacity(self):
        w = _window(self.text, "A LIMIT-death is NOT a reason to idle-park")
        self.assertTrue(
            "own working turn is direct proof capacity exists RIGHT NOW" in w,
            "the core insight — your own working turn proves capacity now — "
            "must be stated (#520)")

    def test_printed_reset_is_a_hint_not_a_wall(self):
        w = _window(self.text, "A LIMIT-death is NOT a reason to idle-park")
        self.assertTrue("printed reset time is a HINT" in w,
                        "the printed reset must be framed as a hint, not a wall")

    def test_bounded_periodic_reprobe_not_a_hammer(self):
        # The governing constraint reconcile: bounded ~10-15 min re-probe, never
        # a tight continue-hammer (the job-6 frozen rationale).
        w = _window(self.text, "If the probe ITSELF returns 429", span=700)
        self.assertTrue("bounded PERIODIC re-probes" in w,
                        "must mandate a bounded periodic re-probe")
        self.assertTrue("min cadence" in w and "10" in w and "15" in w,
                        "must name the ~10-15 min cadence")
        self.assertTrue("continue`-hammer" in w,
                        "must forbid a tight continue-hammer (the frozen "
                        "rationale reconcile)")

    def test_wont_work_until_reset_is_banned(self):
        w = _window(self.text, "BANNED: declaring", span=500)
        self.assertTrue("won't work until the reset" in w
                        or "robiť nebudem" in w,
                        "declaring 'won't work until the reset' must be banned")
        self.assertTrue("staying parked AFTER the limit has released" in w,
                        "the worst form — parked after release — must be banned")

    def test_two_classes_distinguished(self):
        # Session limit vs monthly spend cap, handled differently.
        idx = self.text.index("Two limit CLASSES")
        w = _norm(self.text[idx:idx + 1600])
        self.assertTrue("Session limit" in w, "session-limit class named")
        self.assertTrue("Monthly spend cap" in w, "monthly-spend class named")
        self.assertTrue("raise it at claude.ai/settings/usage" in w,
                        "the monthly-spend banner (no reset clock) named")
        self.assertTrue("STOP dispatching" in w and "ONE" in w,
                        "monthly-spend behaviour: stop dispatch + one owner ping")

    def test_reconciles_with_watchdog_classifiers_not_a_parallel_one(self):
        w = _window(self.text, "Two limit CLASSES", span=900)
        self.assertTrue("is_usage_cap" in w and "is_account_dispatch_block" in w,
                        "the skill must point at the watchdog's existing "
                        "classifiers, never a parallel classification")

    def test_anti_patterns_cover_the_idle_park(self):
        w = _window(self.text, "Reading a 429 limit banner", span=500)
        self.assertTrue("idle-park" in w and "#520" in w,
                        "the anti-pattern list must forbid idle-parking a "
                        "limit-death on the printed clock")


class TestModuleStubPointer(unittest.TestCase):
    def test_stub_carries_probe_first_pointer(self):
        w = _norm(MODULE_MD.read_text(encoding="utf-8"))
        self.assertTrue("429 LIMIT-death" in w and "#520" in w,
                        "the module stub must carry the probe-first pointer")
        self.assertTrue("your own working turn proves capacity exists NOW" in w,
                        "the stub must state the own-turn-is-capacity insight")
        self.assertTrue("never a blind wait to the clock" in w,
                        "the stub must forbid a blind wait to the printed clock")


class TestSubagentContinuationPointer(unittest.TestCase):
    def test_pointer_for_a_worker_killed_by_a_limit(self):
        w = _norm(SUBAGENT_MD.read_text(encoding="utf-8"))
        self.assertTrue("killed by a 429 LIMIT" in w and "#520" in w,
                        "subagent-continuation must point at probe-first for a "
                        "limit-killed worker")
        self.assertTrue("PROBE / re-dispatch IMMEDIATELY" in w,
                        "the pointer must mandate an immediate probe/re-dispatch")
        self.assertTrue("verify-launched-work-liveness" in w,
                        "the pointer must reference the full-doctrine skill")


class TestWatchdogNudgeTextCarriesProbeFirst(unittest.TestCase):
    def setUp(self):
        import watchdog
        self.nudge = watchdog.WORKING_NUDGE_TEXT

    def test_prefix_preserved(self):
        # The janitor/own-payload recognition keys on this prefix (#497/#501).
        self.assertTrue(self.nudge.startswith("stuck-check: "),
                        "the stuck-check prefix must survive the probe-first "
                        "addition (own-payload recognition depends on it)")

    def test_still_chunk_typed_length(self):
        # >200c keeps it in the chunk-typed / janitor-reclaim class (#497).
        self.assertGreater(len(self.nudge), 200)

    def test_probe_first_clause_present(self):
        n = _norm(self.nudge)
        self.assertTrue("429 limit" in n and "#520" in n,
                        "the stuck-check nudge must carry a probe-first "
                        "instruction for the limit-park case")
        self.assertTrue("vlastný turn je dôkaz" in n,
                        "the nudge must state the own-turn-is-capacity insight")
        self.assertTrue("re-proby" in n,
                        "the nudge must mandate bounded re-probes, not a blind "
                        "wait")


class TestJob6DocstringReconcile(unittest.TestCase):
    def test_job6_docstring_reconciles_with_probe_first(self):
        w = _norm(WATCHDOG_INIT.read_text(encoding="utf-8"))
        self.assertTrue("Probe-first reconcile (#520)" in w,
                        "the job-6 docstring must carry the probe-first "
                        "reconcile note")
        self.assertTrue("post-reset retries are bounded" in w
                        and "not a tight hammer" in w,
                        "the reconcile must state the SESSLIMIT_RETRY_S "
                        "post-reset retries are bounded, not a tight hammer")
        self.assertTrue("reset-time-AGNOSTIC job-4 stuck-check nudge" in w,
                        "the reconcile must point at the job-4 stuck-check as "
                        "the covering mechanism, not a new job-6 behaviour")


class TestDecideClassReconcile(unittest.TestCase):
    def test_is_account_dispatch_block_doc_reconciles_two_classes(self):
        import watchdog  # noqa: F401
        d = sys.modules["watchdog.decide"]
        doc = _norm(d.is_account_dispatch_block.__doc__ or "")
        self.assertTrue("SESSION-guidance reconcile (#520)" in doc,
                        "the classifier docstring must carry the class reconcile")
        self.assertTrue("SINGLE source of truth" in doc,
                        "the classifier must be named the single source of truth "
                        "the skill reconciles WITH, never a parallel one")
        self.assertTrue("never a permanent" in doc and "won't work" in doc,
                        "the monthly-spend class must ban a permanent 'won't "
                        "work' after the cap is raised")

    def test_classifier_behaviour_unchanged(self):
        # The reconcile is DOCSTRING-only — the classifiers themselves are
        # untouched (no behavioural change, no RED->GREEN decision test needed).
        import watchdog
        self.assertTrue(watchdog.is_usage_cap(
            "You've hit your session limit · resets 6pm"))
        self.assertTrue(watchdog.is_account_dispatch_block(
            "You've hit your monthly spend limit · raise it at claude.ai"))
        self.assertFalse(watchdog.is_account_dispatch_block(
            "rate limited, try again"))


if __name__ == "__main__":
    unittest.main()
