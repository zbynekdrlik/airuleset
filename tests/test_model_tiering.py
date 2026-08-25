"""Locks the model tiering: Opus 5 BANNED; Fable for judgment-content work.

History: 2026-07-03 middle tier (Opus 5 + Sonnet 5 default) -> 2026-08-13
(Opus 5 banned outright, gated Fable the default judgment layer) ->
2026-08-14 refinement #455 (Fable narrowed to HARD-only after the
inherited-Fable burn; Opus 4.8 default) -> **2026-08-25 revision #690**: the
HARD-only boundary produced ZERO Fable subagent dispatches in practice (the
"when unsure -> it is NOT hard" tie-break + the taxonomy's height; the owner
never once saw a Fable subagent across the subdevs' lifetime), so the
boundary MOVES. The lineup these assertions lock:

  main session managed default = Fable 5 (claude-fable-5[1m], MANAGED_MODEL)
  judgment-CONTENT tasks       = Fable 5 through the budget gate (non-trivial
                                 implementation, review of a non-trivial
                                 change, hard debug, plan/design/synthesis;
                                 tie-break REVERSED: unsure -> it QUALIFIES;
                                 ~50% of subagent tasks is the owner's
                                 CALIBRATION target, never a counter)
  airuleset repo subagents     = Fable-MAJORITY: every substantive dispatch
                                 carries model: "fable" at gate OPEN
  routine execution fallback   = Opus 4.8 via agent-definition frontmatter /
                                 Workflow opts.model full id / inheritance
                                 (also the gate-CLOSED fallback tier)
  mechanical / read-only       = sonnet low (haiku most-trivial) -- unchanged
  Opus 5 (claude-opus-5, and the bare `opus` alias that resolves to it)
                               = BANNED on every dispatch surface (grep-gated)

The budget gate (airuleset.py fable-gate) guards EVERY automatic Fable
dispatch; its default threshold is 90 (raised from 80 by #690 so the new
usage level actually passes -- an 80% gate would dead-letter the policy
mid-week); fail-safe CLOSED on missing/stale cache is UNCHANGED. CLOSED
falls back to Opus 4.8, never lower and never the banned Opus 5. The
2026-07-01/02/03 history and the 2026-07-25 Opus-5-era records stay
preserved VERBATIM in the fable-advisor skill -- locked below too.
"""

import re
from pathlib import Path
from unittest import TestCase, main

from watchdog.usage import FABLE_GATE_PCT

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
        # #690 revision (2026-08-25): Fable for judgment-content work,
        # airuleset subagents Fable-MAJORITY, Opus 4.8 the routine fallback.
        # Header asserted as two single-line fragments (the wrap-trap this
        # file documents).
        self.assertIn(
            "Model tiering — Fable 5 for judgment-content work; "
            "airuleset subagents Fable-MAJORITY; Opus 4.8 routine fallback", t)
        self.assertIn(
            "Opus 5 BANNED (ACTIVE policy, 2026-08-25 — revises 2026-08-14 "
            "HARD-only)", t)
        # the 2026-08-14 header (Fable HARD-only) is retired as the LIVE header:
        self.assertNotIn(
            "Model tiering — Opus 4.8 default; Fable 5 for HARD work only; "
            "Sonnet 5 for light work", t)
        # the 2026-08-13 header (Fable as the default judgment tier) stays retired:
        self.assertNotIn(
            "Model tiering — Fable 5 judgment + Opus 4.8 execution; "
            "Opus 5 BANNED (ACTIVE policy, 2026-08-13 — replaces 2026-07-03)", t)
        self.assertNotIn("Opus 5 + Sonnet 5 default; Fable 5 AUTO-escalates", t)

    def test_directive_recorded_verbatim(self):
        t = read(MODULE)
        # 2026-08-13 directives stay as history:
        self.assertIn("intenet je plny obrovskej nespokojnosti s opus 5", t)
        self.assertIn("hlavne nie ze pouzijes sonnet na zlozite veci", t)
        # #455 -- the two 2026-08-14 refinement directives, verbatim:
        self.assertIn(
            "len tazke ulohy isli na fable ostatok na opus4.8 a moze byt "
            "aj sonnet 5 vyuzivany", t)
        self.assertIn(
            "len ja dlhodobo pouzivam hlavny agent fable lebo potrebujem "
            "aby ten co komunikuje so mnou bol vysoko inteligentny", t)
        # #690 -- the 2026-08-25 revision directive, verbatim (single-line
        # fragments of the one physical quote line):
        self.assertIn(
            "chcem aby sa ta hranica posunula aby sa pouzival fable ovela viac", t)
        self.assertIn("na airuleset chcem aby sa majoritne pouzival fable", t)

    def test_judgment_content_criterion_is_the_selector(self):
        # #690: the fleet-wide Fable selector is the JUDGMENT-CONTENT test --
        # a mechanizable per-task criterion, never a percentage counter, with
        # the tie-break REVERSED (the old "unsure -> NOT hard" tie-break is
        # the traced root cause of zero Fable dispatches).
        t = read(MODULE)
        self.assertIn("JUDGMENT-CONTENT test", t)
        self.assertIn("Non-trivial implementation", t)
        self.assertIn("Review / verify of a non-trivial change", t)
        self.assertIn("Hard debugging", t)
        self.assertIn(
            "when unsure whether a task carries judgment content → it DOES", t)
        self.assertIn("calibration check, never a counter", t)
        self.assertIn("≈50%", t)
        # the old always-against-Fable tie-break must be gone as LIVE policy:
        self.assertNotIn(
            "When unsure whether it is design-heavy → it is NOT", t)

    def test_airuleset_repo_is_fable_majority(self):
        # #690: on the airuleset repo every substantive subagent dispatch is
        # Fable at gate OPEN; gate CLOSED falls back to the frontmatter pin.
        t = read(MODULE)
        self.assertIn("Fable-MAJORITY", t)
        self.assertIn(
            'carries an explicit `model: "fable"` when the gate is OPEN', t)
        self.assertRegex(
            t, r"[Gg]ate CLOSED[^\n]*frontmatter-pinned `claude-opus-4-8`")

    def test_gate_threshold_default_is_90(self):
        # #690: threshold raised 80 -> 90 so the new usage level actually
        # passes (at the observed baseline fable=43%/weekly=66% the new
        # dispatch load projects into the 60-90% band, which an 80% gate
        # would dead-letter mid-week). Fail-safe CLOSED semantics are locked
        # separately in test_usage_cache.py and must NOT change.
        self.assertEqual(FABLE_GATE_PCT, 90)
        self.assertIn("raised 80→90", read(MODULE))

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

    def test_gate_guards_every_automatic_fable_dispatch(self):
        # #690 (2026-08-25): the gate guards EVERY automatic Fable dispatch
        # (the judgment-content tier + the airuleset Fable-majority), no
        # longer only a HARD-only escalation. The LIVE gate-role phrase must
        # flip; older gate-role phrasings stay retired.
        t = read(MODULE)
        self.assertIn("airuleset.py fable-gate", t)
        self.assertIn("guards EVERY automatic Fable dispatch", t)
        self.assertNotIn("guards the HARD-only Fable escalation", t)
        self.assertNotIn("guards the DEFAULT judgment layer", t)
        self.assertIn("ONCE per qualifying task/batch", t)
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
        # #690: the HARD criteria no longer SELECT the model (the
        # judgment-content test does) -- they still classify design DEPTH
        # (the autopilot skill's design-triage step reuses exactly this
        # taxonomy), so the enumeration must survive, demoted explicitly.
        t = read(MODULE)
        self.assertIn("no longer the Fable selector", t)
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
        # #690 (2026-08-25): a DISPATCHED, fresh-context Fable worker is a
        # sanctioned shape now (the 2026-08-14 HARD-only sanction is
        # retired); Fable as a long-lived MAIN implementer stays banned.
        self.assertIn(
            "a DISPATCHED, fresh-context Fable worker is a sanctioned shape", t)
        self.assertNotIn("sanctioned ONLY for genuinely HARD", t)

    def test_behavior_header_is_fable_and_opus_4_8(self):
        t = read(MODULE)
        self.assertIn("Fable 5 / Opus 4.8 behavior", t)
        self.assertNotIn("Opus 5 / Fable 5 behavior (primary + escalation)", t)

    def test_main_session_clauses_survive(self):
        t = read(MODULE)
        self.assertIn(
            "MAIN interactive session runs whatever the user set via `/model`", t)
        self.assertIn("NEVER recommend switching it, in either direction", t)

    def test_sonnet_5_rehabilitated_for_light_work(self):
        # #455 (2026-08-14): Sonnet 5 is now the LIGHT / mechanical tier
        # ("moze byt aj sonnet 5 vyuzivany") -- an explicit dispatch target,
        # not merely a fallback for when 4.8 is unreachable.
        t = read(MODULE)
        self.assertIn("2026-08-14 refinement explicitly REHABILITATED", t)
        self.assertIn('`model: "sonnet"` + `low`', t)
        self.assertIn("moze byt aj sonnet 5 vyuzivany", t)
        # still never for anything complex:
        self.assertIn("Sonnet 5 is never used for anything complex", t)

    def test_hard_rule_never_model_less_dispatch(self):
        # #455 (2026-08-14): the load-bearing HARD RULE -- a model-less
        # subagent dispatch inherits the Fable main, which is the burn.
        t = read(MODULE)
        self.assertIn(
            "NEVER dispatch a subagent without an EXPLICIT model choice", t)
        self.assertIn("INHERITS the caller's model", t)
        self.assertIn("silently runs the SUBAGENT on Fable", t)
        self.assertIn("inherited Fable", t)
        # the HARD RULE bullet names its own lock (same line):
        self.assertRegex(t, r"HARD RULE[^\n]*lock-tested")


class TestWorkflowStageTiering(TestCase):
    """claude-code-tooling.md's per-stage mirror of the same policy."""

    def test_judgment_stages_gate_fable_closed_opus_4_8(self):
        t = read(TOOLING)
        self.assertIn("`opts.model: 'fable'`", t)
        self.assertIn("ONLY when the budget gate is OPEN", t)
        # #690 (2026-08-25): Fable stages are every stage with real
        # judgment/design content (the judgment-content test), no longer the
        # HARD-only subset; a genuinely mechanical stage stays cheap.
        self.assertIn("every stage with real judgment/design content", t)
        self.assertNotIn("ONLY for the HARD subset", t)
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
        # #455 (2026-08-14): `model: "opus"` may appear ONLY as a trap warning
        # (a "NEVER do this", on a BANNED line -- the gk incident), never as a
        # dispatch instruction. Mirrors the grep-gate's own ban-prose logic.
        for ln in w.splitlines():
            if 'model: "opus"' in ln:
                self.assertIn(
                    "BANNED", ln,
                    "model: opus appears outside a BANNED trap-warning line: %r"
                    % ln[:80])

    def test_autopilot_supervisor_dispatch_model_rule(self):
        # #690: airuleset repo -> Fable-MAJORITY worker dispatch; other repos
        # -> the judgment-content criterion; Opus 4.8 stays the routine
        # fallback (frontmatter pin, dispatched AS-IS).
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Fable-MAJORITY", s)
        self.assertIn("JUDGMENT-CONTENT test", s)
        self.assertIn("gate OPEN (exit 0) → dispatch `model: fable`", s)
        self.assertIn("gate CLOSED (exit 1)", s)
        self.assertIn(
            "Never dispatch an automatic `model: fable` without the gate check", s)
        self.assertNotIn("Model = Opus 4.8 by default", s)
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

    def test_autopilot_worker_review_stage_explicit_model_mandate(self):
        # #690 (2026-08-25): CYCLE step 6 review of a NON-TRIVIAL change is
        # judgment-content work -> gated `model: "fable"`; only a genuinely
        # TRIVIAL diff's review (or gate CLOSED) runs with NO override on the
        # worker's pinned claude-opus-4-8. Mechanical sub-dispatches still
        # carry an explicit sonnet/haiku, never model-less.
        w = read("agents/autopilot-worker.md")
        norm = " ".join(w.split())
        self.assertIn("MODEL for the review dispatch", norm)
        self.assertIn(
            "review of a NON-TRIVIAL change is judgment-content work", norm)
        self.assertIn("inherits YOUR pinned `claude-opus-4-8`", norm)
        # the 2026-08-14 HARD-only review rule is retired:
        self.assertNotIn("the DEFAULT is NO `model` override", norm)
        self.assertNotIn(
            "Escalate the review to Fable ONLY when the TICKET itself "
            "genuinely meets the design-heavy taxonomy", norm)
        # #455 MINOR-2 fix survives: the worker's OWN model-less dispatch is
        # safe (claude-opus-4-8-pinned, so no-override inherits 4.8); the
        # model-less-inherits-Fable hazard is a Fable-MAIN one.
        self.assertIn("Your OWN model-less dispatch is SAFE", norm)
        self.assertIn('carries an explicit `model: "sonnet"`/`"haiku"`', norm)

    def test_opus_alias_trap_warning_present(self):
        # #455 addendum -- LIVE gk incident 2026-08-14: a naive "explicit
        # model" reading pushed `model: "opus"` (the alias for the 4.8 tier),
        # which resolves to BANNED Opus 5 AND overrode the frontmatter pin. The
        # trap-warning must sit next to the explicit-model mandate on EVERY
        # surface that carries it.
        for path in (MODULE, TOOLING, "agents/autopilot-worker.md"):
            norm = " ".join(read(path).split())
            self.assertIn("live gk incident 2026-08-14", norm,
                          "%s: missing gk-incident citation" % path)
            self.assertIn('model: "opus"', norm, "%s: missing the opus trap" % path)
            self.assertIn("BANNED Opus 5", norm,
                          "%s: missing banned-Opus-5 warning" % path)
        # model-awareness spells out the exact resolution: no param for 4.8.
        m = " ".join(read(MODULE).split())
        self.assertIn('Passing `model: "opus"` is NEVER correct', m)
        self.assertIn("the Opus 4.8 tier is reached WITHOUT a param", m)


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

    def test_2026_08_25_revision_recorded(self):
        # #690: the advisor's history/tables must cite the current policy --
        # judgment-content boundary + airuleset Fable-majority + gate 80->90.
        a = read(ADVISOR)
        self.assertIn("2026-08-25", a)
        self.assertIn("majoritne pouzival fable", a)
        self.assertIn("80→90", a)


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
