"""Locks the model tiering: Opus 5 BANNED; Fable 5 judgment + Opus 4.8 execution.

History: the 2026-07-03 middle tier (Opus 5 + Sonnet 5 default, Fable only on
gated HARD escalations) was the ACTIVE policy until 2026-08-13, when the user
banned Opus 5 outright (directive, verbatim: "intenet je plny obrovskej
nespokojnosti s opus 5, chcem prerobit pravidla opus 5 sa nesmie pouzivat...")
and refined the mapping the same day ("hlavne nie ze pouzijes sonnet na zlozite
veci, radsej by som mal ze kde bol opus 5 bude fable a kde bol sonnet bude opus
4.8"). The lineup these assertions lock:

  main session managed default = Fable 5 (claude-fable-5[1m], MANAGED_MODEL)
  judgment dispatches          = Fable 5 through the budget gate;
                                 gate CLOSED -> Opus 4.8 (claude-opus-4-8)
  execution dispatches         = Opus 4.8 via agent-definition frontmatter /
                                 Workflow opts.model full id
  mechanical / read-only       = Opus 4.8 low where nameable; sonnet ONLY for
                                 genuinely trivial lookups; haiku most-trivial
  Opus 5 (claude-opus-5, and the bare `opus` alias that resolves to it)
                               = BANNED on every dispatch surface (grep-gated)

The budget gate (airuleset.py fable-gate) now guards the DEFAULT judgment
layer, not an exceptional escalation; CLOSED falls back to Opus 4.8, never
lower and never the banned Opus 5. The 2026-07-01/02/03 history and the
2026-07-25 Opus-5-era records stay preserved VERBATIM in the fable-advisor
skill (dated history, not current policy) -- locked below too.
"""

import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


ADVISOR = "skills/fable-advisor/SKILL.md"
MODULE = "modules/core/model-awareness.md"
TOOLING = "modules/core/claude-code-tooling.md"


class TestOpus5BanLineup(TestCase):
    """The 2026-08-13 directive bans Opus 5 everywhere and rewrites the
    lineup -- these lock the NEW ACTIVE policy in the always-on module."""

    def test_model_awareness_active_policy_header(self):
        t = read(MODULE)
        self.assertIn(
            "Model tiering — Fable 5 judgment + Opus 4.8 execution; "
            "Opus 5 BANNED (ACTIVE policy, 2026-08-13 — replaces 2026-07-03)", t)
        self.assertNotIn("Opus 5 + Sonnet 5 default; Fable 5 AUTO-escalates", t)

    def test_directive_recorded_verbatim(self):
        t = read(MODULE)
        self.assertIn("intenet je plny obrovskej nespokojnosti s opus 5", t)
        self.assertIn("hlavne nie ze pouzijes sonnet na zlozite veci", t)

    def test_opus_5_is_banned_and_alias_named(self):
        t = read(MODULE)
        self.assertIn("BANNED", t)
        self.assertIn("`claude-opus-5`", t)
        # the alias must be named as resolving to the banned model, so no
        # rule ever reaches for the bare `opus` alias again:
        self.assertIn("`opus` alias", t)

    def test_execution_tier_is_opus_4_8(self):
        t = read(MODULE)
        self.assertIn(
            "EXECUTION of settled, scoped code = Opus 4.8 (`claude-opus-4-8`)", t)
        self.assertNotIn("EXECUTION of settled, scoped code = Sonnet 5", t)

    def test_sonnet_never_complex(self):
        t = read(MODULE)
        self.assertIn("Sonnet 5 is never used for anything complex", t)
        self.assertIn("when in doubt, Opus 4.8", t)

    def test_gate_guards_the_default_judgment_layer(self):
        t = read(MODULE)
        self.assertIn("airuleset.py fable-gate", t)
        self.assertIn("guards the DEFAULT judgment layer", t)
        self.assertIn("ONCE per judgment task/batch", t)
        self.assertIn("missing/stale cache = CLOSED", t)
        self.assertIn("Never skip the gate", t)
        # CLOSED falls back to Opus 4.8, never the banned alias:
        self.assertRegex(t, r"CLOSED[^\n]*claude-opus-4-8")

    def test_opus_4_8_reach_mechanics_documented(self):
        # The per-dispatch `model` param takes only aliases (live-verified,
        # ROZHODNUTE mapping mechanics), so the module must document the
        # REAL mechanisms: agent-definition frontmatter full id, Workflow
        # opts.model full name, or inheritance -- never the `opus` alias.
        t = read(MODULE)
        self.assertIn("model: claude-opus-4-8", t)
        self.assertIn("opts.model: 'claude-opus-4-8'", t)
        self.assertIn("never the `opus` alias", t)

    def test_design_heavy_taxonomy_survives(self):
        # The HARD criteria no longer pick the judgment model (all judgment
        # is gated Fable) -- they still classify design DEPTH (the autopilot
        # skill's design-triage step reuses exactly this taxonomy), so the
        # enumeration must survive.
        t = read(MODULE)
        self.assertIn(
            "Architecture / design / synthesis of a genuinely COMPLEX or cross-cutting", t)
        self.assertIn('"Multi-file" alone is NOT the bar', t)
        self.assertIn("Hard debugging", t)
        self.assertIn("Adversarial final review / verify of a safety-critical", t)
        self.assertIn("autopilot ticket that is architectural / cross-cutting", t)
        self.assertRegex(t, r"(?i)sub-dev[^\n]*(hand-off|review|submission)")

    def test_circling_valve_survives_without_opus_5(self):
        t = read(MODULE)
        self.assertIn("CIRCLING", t)
        self.assertIn("OBSERVED circling", t)

    def test_fable_consult_shape_survives(self):
        t = read(MODULE)
        self.assertIn("digest in, decision out", t)
        self.assertIn("re-reads the full conversation context every turn", t)
        # what changed: a gated Fable judgment DISPATCH is the sanctioned
        # norm now, not an exception
        self.assertIn("sanctioned", t)

    def test_behavior_header_is_fable_and_opus_4_8(self):
        t = read(MODULE)
        self.assertIn("Fable 5 / Opus 4.8 behavior", t)
        self.assertNotIn("Opus 5 / Fable 5 behavior (primary + escalation)", t)

    def test_main_session_clauses_survive(self):
        t = read(MODULE)
        self.assertIn(
            "MAIN interactive session runs whatever the user set via `/model`", t)
        self.assertIn("NEVER recommend switching it, in either direction", t)


class TestWorkflowStageTiering(TestCase):
    """claude-code-tooling.md's per-stage mirror of the same policy."""

    def test_judgment_stages_gate_fable_closed_opus_4_8(self):
        t = read(TOOLING)
        self.assertIn("`opts.model: 'fable'`", t)
        self.assertIn("ONLY when the budget gate is OPEN", t)
        self.assertIn("BEFORE authoring the script", t)
        self.assertIn("never bake in an ungated Fable stage", t)
        self.assertRegex(t, r"CLOSED[^\n]*claude-opus-4-8")

    def test_execution_stages_are_opus_4_8(self):
        t = read(TOOLING)
        self.assertIn("EXECUTION stages", t)
        self.assertIn("opts.model: 'claude-opus-4-8'", t)
        self.assertNotIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)

    def test_no_opus_alias_stage(self):
        self.assertNotIn("opts.model: 'opus'", read(TOOLING))

    def test_advisor_digest_shape_survives(self):
        t = read(TOOLING)
        self.assertIn("ADVISOR call: digest in, decision out", t)
        self.assertIn("grounds itself by re-reading the sources", t)


class TestDispatchSurfacesRewritten(TestCase):
    """Every dispatch-instructing surface names the new lineup."""

    def test_autopilot_worker_pinned_to_opus_4_8(self):
        w = read("agents/autopilot-worker.md")
        fm = w.split("---")[1]
        self.assertIn("model: claude-opus-4-8", fm)
        self.assertNotIn("model: sonnet", fm)
        self.assertIn("You run on Opus 4.8", w)

    def test_ticket_validator_pinned_to_opus_4_8(self):
        v = read("agents/ticket-validator.md")
        fm = v.split("---")[1]
        self.assertIn("model: claude-opus-4-8", fm)
        self.assertNotIn("model: sonnet", fm)

    def test_worker_hard_wall_ladder_is_gated_fable(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("fable-gate", w)
        self.assertIn("HARD wall mid-ticket", w)
        self.assertNotIn('FIRST at `model: "opus"`', w)
        self.assertNotIn('`model: "opus"`', w)

    def test_autopilot_supervisor_dispatches_opus_4_8_default(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Opus 4.8 by default", s)
        self.assertIn("gate OPEN (exit 0) → dispatch `model: fable`", s)
        self.assertIn("gate CLOSED (exit 1)", s)
        self.assertIn(
            "Never dispatch an automatic `model: fable` without the gate check", s)
        self.assertNotIn("Model = Sonnet 5 by default", s)
        self.assertNotIn('`model: "opus"`', s)
        self.assertNotIn("`model: opus`", s)

    def test_fable_advisor_gate_closed_falls_back_to_opus_4_8(self):
        a = read(ADVISOR)
        self.assertRegex(a, r"CLOSED[^\n]*(Opus 4\.8|claude-opus-4-8)")
        self.assertNotIn("runs on `opus` instead", a)
        self.assertNotIn("(or `opus`", a)
        # execution hand-off is Opus 4.8 now, not sonnet:
        self.assertNotIn("dispatches execution to `model: sonnet`", a)

    def test_process_subdev_review_closed_tier_is_opus_4_8(self):
        txt = read("skills/process-subdev/SKILL.md")
        self.assertIn("fable-gate", txt)
        self.assertIn("model: fable", txt)
        self.assertIn("xhigh", txt)
        self.assertIn("never degrades", txt)
        self.assertRegex(txt, r"CLOSED[^\n]*claude-opus-4-8")

    def test_cross_stream_protocol_closed_tier_is_opus_4_8(self):
        txt = read("skills/autopilot/references/cross-stream-protocol.md")
        self.assertRegex(txt, r"CLOSED[^\n]*claude-opus-4-8")


class TestAdvisorHistoryPreserved(TestCase):
    """The dated policy history stays VERBATIM in the fable-advisor skill --
    the ban rewrites the ACTIVE lineup, never the record of how we got
    here (the 2026-07-01 burn, the 2026-07-25 Opus-5-era rationale)."""

    def test_burn_and_revert_history_survive(self):
        a = read(ADVISOR)
        self.assertIn("reverted the 2026-07-01", a)
        self.assertIn("2026-07-01 Fable-everywhere mode burned tokens brutally", a)
        self.assertIn(
            "Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend", a)

    def test_dormant_max_performance_record_survives(self):
        a = read(ADVISOR)
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", a)
        self.assertIn("re-activate ONLY on the user's explicit say-so", a)

    def test_opus_5_era_rationale_survives_as_history(self):
        a = read(ADVISOR)
        self.assertIn("Opus 5 retires that workaround", a)
        self.assertIn("Sonnet 5 could not reliably carry the coordinator role", a)
        self.assertIn("within 0.5% of Fable 5 on CursorBench 3.2", a)
        self.assertIn("https://www.anthropic.com/news/claude-opus-5", a)
        self.assertIn("community reports of Opus 4.8 degradation", a)
        self.assertIn("no 4.8 model regression so far", a)

    def test_opus_5_era_section_is_marked_superseded(self):
        a = read(ADVISOR)
        self.assertIn("superseded 2026-08-13", a)

    def test_pricing_table_survives_dated(self):
        a = read(ADVISOR)
        self.assertIn("Fable 5 $10/$50", a)
        self.assertIn("Opus 5 $5/$25", a)
        self.assertIn("Sonnet 5 $2/$10", a)
        self.assertIn("Haiku 4.5 $1/$5", a)


class TestOpus5GrepGate(TestCase):
    """The banned model id (and the `opus` alias as a dispatch VALUE) must
    not appear on ANY dispatch surface. Allowlist: the ban prose itself
    (lines carrying BANNED/banned/zakazan), and the dated benchmark URL in
    the advisor's history section. Historical narrative homes (docs/,
    .claude/, watchdog/'s REMOVED-jobs incident notes) and tests/ (this
    gate's own home + fixtures) are outside the scanned dispatch set.

    Honestly-stated residuals (adversarial review, #440): (a) the
    ban-prose exemption is LINE-scoped — a code line whose trailing
    comment merely mentions "banned" would pass; accepted, the gate locks
    against DRIFT, not adversaries. (b) `.claude/` is excluded as a
    historical-narrative home, but `.claude/rules/` also holds REACHABLE
    path-scoped rules — those were swept and fixed manually in #440's
    review pass; a future reintroduction there is not mechanically
    caught. (c) the CLOSED-fallback patterns cover the lowercase alias
    and the capitalized bare "Opus" (not followed by "4"); an exotic
    rendering (e.g. "Opus five") is out of scope."""

    SCAN = ("airuleset.py", "statusbar.py", "burn", "notify", "filedrop",
            "agents", "skills", "modules", "hooks", "rules", "profiles",
            "settings", "scripts", "CLAUDE.md")
    EXTS = {".py", ".md", ".sh", ".json", ".profile", ".conf"}

    def _files(self):
        for top in self.SCAN:
            p = ROOT / top
            if p.is_file():
                yield p
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and (f.suffix in self.EXTS or not f.suffix):
                        yield f

    @staticmethod
    def _line_is_ban_prose(line):
        return ("BANNED" in line or "banned" in line
                or "zakázan" in line or "zakazan" in line
                or "anthropic.com/news/claude-opus-5" in line)

    def test_claude_opus_5_absent_from_dispatch_surfaces(self):
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "claude-opus-5" not in line:
                    continue
                if self._line_is_ban_prose(line):
                    continue
                violations.append(
                    "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        self.assertEqual(violations, [])

    def test_opus_alias_never_a_dispatch_value(self):
        # `model: "opus"` / `model: 'opus'` / frontmatter `model: opus` /
        # `opts.model: 'opus'` all resolve to the banned Opus 5 on the
        # Anthropic API. A gate-CLOSED fallback naming the bare alias
        # (`CLOSED ... opus`) is the other real shape the ban must catch;
        # `claude-opus-4-8` never matches either pattern (the `-` fails
        # the boundary), and ban prose avoids the literal shapes.
        alias = re.compile(r"""model:\s*["']?opus["']?(?![\w-])""")
        closed_fallback = re.compile(r"CLOSED[^\n]{0,60}(?<![\w-])`?opus`?(?![\w-])")
        # capitalized bare-"Opus" fallback ("CLOSED → Opus") — the exact OLD
        # model-awareness wording; "Opus 4.8"/"Opus 4" stays legal via the
        # negative lookahead, and `claude-opus-4-8` never matches \bOpus\b.
        closed_capital = re.compile(r"CLOSED[^\n]{0,60}\bOpus\b(?!\s*4)")
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if (alias.search(line) or closed_fallback.search(line)
                        or closed_capital.search(line)):
                    if self._line_is_ban_prose(line):
                        continue
                    violations.append(
                        "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        self.assertEqual(violations, [])


class TestUltracodeStandingDefault(TestCase):
    """#445: user directive 2026-08-13 ("chcel by som aby by default vzdy bol
    ultracode... maximalna akceleracia... paralelne, ak to uloha dovoli") —
    ultracode is a STANDING opt-in on every managed session, and maximum
    acceleration (worktree fleet, disjoint lanes, serial-only integration)
    is the default doctrine. The settings/launcher halves are locked in
    tests/test_airuleset.py; these lock the policy PROSE."""

    def test_tooling_records_the_directive_verbatim(self):
        t = read(TOOLING)
        self.assertIn("chcel by som aby by default vzdy bol ultracode", t)
        self.assertIn("STANDING DEFAULT", t)

    def test_stop_and_ask_step_is_retired(self):
        t = read(TOOLING)
        self.assertNotIn("STOP and ASK for ultracode", t)
        self.assertNotIn("The agent CANNOT enable ultracode itself", t)

    def test_max_acceleration_doctrine_is_explicit(self):
        t = read(TOOLING)
        self.assertIn("disjoint lanes", t)
        self.assertIn("integration strictly serial", t)
        self.assertIn("single-worker only when the task genuinely cannot parallelize", t)

    def test_module_effort_baseline_is_xhigh_plus_ultracode(self):
        m = read(MODULE)
        self.assertIn("managed MAIN-session baseline is `xhigh` + standing ultracode", m)
        self.assertNotIn("managed MAIN-session baseline is `high`", m)


if __name__ == "__main__":
    main()
