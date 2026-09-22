"""#1077 — a guide section is an agent-eval fixture: the rule lands on the
guide-source surface, the hand-off composer refuses a guide-source RFR without
an `AI-eval:` line, and the runner stays the PROJECT's (`eval_navody.py`, already
built in odoo-erp — never rebuilt here).

Decided design (Fable main, comment 5773235391), Approach 1 items 1–3:

1. RULE `rules/guide-agent-eval.md` — path-scoped (`paths:` globs over the guide
   sources), listed in `profiles/universal.profile` like the 5 existing
   `rules/*.md` (symlinked to `~/.claude/rules/`, so ZERO always-on bytes), plus
   ONE `hooks/situational-triggers.conf` row so it also lands at the
   `airuleset.py handoff` action.
2. GATE LEAF `gates/agenteval.py` (stdlib; imports `gates.navody._read_stream_file`
   for the per-tenant fact) — `agent_eval(changed_paths, body, *, cwd, fact) ->
   (ok, reason)`: a guide-SOURCE diff (the four globs) demands an
   `AI-eval: <fixture>-qa.json → <tally> (<report>)` line whose fixture AND report
   exist under `cwd`, OR `AI-eval: n/a — <why>`. A per-tenant
   `agent_eval: NONE — <why>` fact makes a bare `n/a` sufficient but STILL
   mandatory. COMPOSER pre-flight `_handoff_agent_eval_preflight` in `airuleset.py`,
   called right after `_handoff_guide_preflight` in `cmd_handoff`; FAIL-OPEN when
   the diff is undeterminable.
3. DOCTRINE — one bullet in `.claude/rules/internals-gates.md`.

RED-before-GREEN: against the merged main `gates.agenteval` does not exist (import
error), `airuleset._handoff_agent_eval_preflight` is absent, the rule file / the
profile line / the trigger row / the doctrine bullet are missing.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import agenteval as ae  # noqa: E402  (import error = RED against base)


PROFILE = REPO / "profiles" / "universal.profile"
CONF = REPO / "hooks" / "situational-triggers.conf"
RULE = REPO / "rules" / "guide-agent-eval.md"
INTERNALS_GATES = REPO / ".claude" / "rules" / "internals-gates.md"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _glob_to_re(glob):
    """Translate a Claude Code `paths:` frontmatter glob into a regex, matching
    CC's nested-memory semantics: `**` crosses `/`, `*` stays within one path
    segment. Used ONLY to lock the rule's frontmatter globs (production code
    never re-implements this — CC's native matcher injects the rule)."""
    out = []
    i = 0
    while i < len(glob):
        if glob[i:i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif glob[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _frontmatter_paths(rule_path):
    text = rule_path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("---"), "rule has no frontmatter"
    parts = text.lstrip().split("---", 2)
    front = parts[1]
    globs = []
    in_paths = False
    for ln in front.splitlines():
        s = ln.strip()
        if s.startswith("paths:"):
            in_paths = True
            continue
        if in_paths:
            m = re.match(r'-\s*"?([^"]+?)"?\s*$', s)
            if m:
                globs.append(m.group(1))
            elif s and not s.startswith("-"):
                break
    return globs


def _conf_rows():
    rows = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p != ""]
        rows.append(parts)
    return rows


# --------------------------------------------------------------------------- #
# 1. guide-source detection (the four globs — dispatch-authoritative)
# --------------------------------------------------------------------------- #
class TestGuideSourceDetection(unittest.TestCase):
    def test_qa_json_under_navody_dir(self):
        self.assertTrue(ae.is_guide_source("docs/montalu/navody/sklad-qa.json"))

    def test_qa_json_anywhere_under_docs(self):
        self.assertTrue(ae.is_guide_source("docs/miva/x/y-qa.json"))

    def test_build_guide_py(self):
        self.assertTrue(ae.is_guide_source("docs/montalu/build-vyroba-guide.py"))

    def test_navody_sections_py(self):
        self.assertTrue(
            ae.is_guide_source("docs/montalu/navody_sklad_sections.py"))

    def test_any_file_under_a_navody_dir(self):
        self.assertTrue(
            ae.is_guide_source("docs/slovnormal/navody/prehlad.html"))

    def test_docs_anchor_matches_mid_path(self):
        self.assertTrue(
            ae.is_guide_source("src/docs/montalu/navody/sklad-qa.json"))

    def test_non_guide_model_file(self):
        self.assertFalse(ae.is_guide_source("addons/x/models/y.py"))

    def test_report_md_is_not_a_source(self):
        # the eval REPORT lives under docs/ai-agent/ — editing it must not itself
        # demand another eval.
        self.assertFalse(
            ae.is_guide_source("docs/ai-agent/eval-navody-sklad-2026-09-18.md"))

    def test_bare_docs_navody_no_tenant_still_not_a_source_file(self):
        # a plain code file with no docs/ ancestor
        self.assertFalse(ae.is_guide_source("navody_sklad_sections.py"))


# --------------------------------------------------------------------------- #
# 2. pure gate: agent_eval(changed_paths, body, *, cwd, fact)
# --------------------------------------------------------------------------- #
class TestAgentEvalGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "docs", "x", "navody"))
        os.makedirs(os.path.join(self.d, "docs", "ai-agent"))
        with open(os.path.join(self.d, "docs", "x", "navody", "a-qa.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(self.d, "docs", "ai-agent", "r.md"), "w") as f:
            f.write("# report")

    def test_non_guide_diff_passes(self):
        ok, reason = ae.agent_eval(["addons/x/models/y.py"], "RFR done.",
                                   cwd=self.d, fact=None)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_guide_source_without_line_refuses_naming_the_line(self):
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], "RFR done.",
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)
        self.assertIn("handoff BLOCK", reason)
        self.assertIn("AI-eval", reason)

    def test_valid_fixture_and_report_present_passes(self):
        body = ("READY-FOR-REVIEW: done.\n"
                "AI-eval: docs/x/navody/a-qa.json → 16/18 (docs/ai-agent/r.md)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_fixture_path_missing_refuses_naming_the_path(self):
        body = ("AI-eval: docs/x/navody/MISSING-qa.json → 16/18 "
                "(docs/ai-agent/r.md)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)
        self.assertIn("docs/x/navody/MISSING-qa.json", reason)

    def test_report_path_missing_refuses_naming_the_path(self):
        body = ("AI-eval: docs/x/navody/a-qa.json → 16/18 "
                "(docs/ai-agent/NOPE.md)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)
        self.assertIn("docs/ai-agent/NOPE.md", reason)

    def test_na_with_reason_passes(self):
        body = "AI-eval: n/a — montalu nemá agenta, blokované #7601"
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_na_bare_without_reason_and_no_fact_refuses(self):
        body = "AI-eval: n/a"
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)
        self.assertIn("AI-eval", reason)

    def test_none_fact_plus_no_line_still_refuses(self):
        # the n/a form is STILL required — the fact only makes it sufficient.
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], "RFR done.",
                                   cwd=self.d, fact="NONE — bez agenta")
        self.assertFalse(ok)
        self.assertIn("AI-eval", reason)

    def test_none_fact_makes_bare_na_sufficient(self):
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], "AI-eval: n/a",
                                   cwd=self.d, fact="NONE — bez agenta")
        self.assertTrue(ok, reason)
        self.assertIsNone(reason)

    def test_malformed_line_refuses(self):
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"],
                                   "AI-eval: pending", cwd=self.d, fact=None)
        self.assertFalse(ok)
        self.assertIn("AI-eval", reason)

    def test_empty_diff_passes(self):
        ok, reason = ae.agent_eval([], "RFR", cwd=self.d, fact=None)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_none_changed_paths_passes(self):
        ok, reason = ae.agent_eval(None, "RFR", cwd=self.d, fact=None)
        self.assertTrue(ok)

    def test_ai_eval_line_with_leading_bullet_recognised(self):
        body = "- AI-eval: docs/x/navody/a-qa.json → 18/18 (docs/ai-agent/r.md)"
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertTrue(ok, reason)

    def test_tally_parenthetical_does_not_steal_the_report(self):
        # review F2: `16/18 (2 skipped)` is a natural tally; the REPORT is the
        # TRAILING (…) group — a leading parenthetical must not false-block.
        body = ("AI-eval: docs/x/navody/a-qa.json → 16/18 (2 skipped) "
                "(docs/ai-agent/r.md)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertTrue(ok, reason)

    def test_uppercase_fixture_suffix_recognised(self):
        # review #6: is_guide_source matches -qa.json case-insensitively, so the
        # AI-eval fixture token must be recognised case-insensitively too.
        os.makedirs(os.path.join(self.d, "docs", "y", "navody"))
        with open(os.path.join(self.d, "docs", "y", "navody", "B-QA.JSON"),
                  "w") as f:
            f.write("{}")
        body = "AI-eval: docs/y/navody/B-QA.JSON → 18/18 (docs/ai-agent/r.md)"
        ok, reason = ae.agent_eval(["docs/y/navody/B-QA.JSON"], body,
                                   cwd=self.d, fact=None)
        self.assertTrue(ok, reason)

    def test_traversal_report_refused(self):
        # review F5: a `../` report escapes cwd — the doctrine says "under cwd",
        # so it must be refused even if the traversal target exists.
        body = ("AI-eval: docs/x/navody/a-qa.json → 16/18 "
                "(../../../etc/hosts)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)

    def test_absolute_report_refused(self):
        body = "AI-eval: docs/x/navody/a-qa.json → 16/18 (/etc/hosts)"
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)

    def test_traversal_fixture_refused(self):
        body = ("AI-eval: ../../secret-qa.json → 16/18 (docs/ai-agent/r.md)")
        ok, reason = ae.agent_eval(["docs/x/navody/a-qa.json"], body,
                                   cwd=self.d, fact=None)
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
# 3. per-tenant fact reader (uses gates.navody._read_stream_file)
# --------------------------------------------------------------------------- #
class TestFactReader(unittest.TestCase):
    def _stream_dir(self, stream, body):
        d = tempfile.mkdtemp()
        sd = os.path.join(d, ".claude", "streams")
        os.makedirs(sd)
        with open(os.path.join(sd, "%s.md" % stream), "w", encoding="utf-8") as fh:
            fh.write(body)
        return d

    def test_none_fact_read(self):
        d = self._stream_dir("montalu1",
                             "navody_url: https://x/navody-a.html\n"
                             "agent_eval: NONE — bez agenta\n")
        fact = ae.tenant_agent_eval(d, "montalu1")
        self.assertIsNotNone(fact)
        self.assertTrue(fact.upper().startswith("NONE"))

    def test_missing_fact_is_none(self):
        d = self._stream_dir("montalu1", "navody_url: https://x/navody-a.html\n")
        self.assertIsNone(ae.tenant_agent_eval(d, "montalu1"))

    def test_missing_file_is_none(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(ae.tenant_agent_eval(d, "montalu1"))

    def test_alias_resolution_via_read_stream_file(self):
        # montalu1 -> montalu.md (the fleet rename alias gates.navody resolves)
        d = tempfile.mkdtemp()
        sd = os.path.join(d, ".claude", "streams")
        os.makedirs(sd)
        with open(os.path.join(sd, "montalu.md"), "w", encoding="utf-8") as fh:
            fh.write("agent_eval: NONE — bez agenta\n")
        self.assertIsNotNone(ae.tenant_agent_eval(d, "montalu1"))


# --------------------------------------------------------------------------- #
# 4. composer pre-flight — airuleset._handoff_agent_eval_preflight
# --------------------------------------------------------------------------- #
class TestComposerPreflight(unittest.TestCase):
    def setUp(self):
        import airuleset
        self.air = airuleset
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "docs", "x", "navody"))
        os.makedirs(os.path.join(self.d, "docs", "ai-agent"))
        with open(os.path.join(self.d, "docs", "x", "navody", "a-qa.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(self.d, "docs", "ai-agent", "r.md"), "w") as f:
            f.write("# report")

    def test_guide_source_without_line_blocks(self):
        blk = self.air._handoff_agent_eval_preflight(
            "READY-FOR-REVIEW: done.",
            changed_paths=["docs/x/navody/a-qa.json"],
            cwd=self.d, stream="montalu1")
        self.assertIsNotNone(blk)
        self.assertIn("handoff BLOCK", blk)

    def test_guide_source_with_valid_line_passes(self):
        blk = self.air._handoff_agent_eval_preflight(
            "AI-eval: docs/x/navody/a-qa.json → 16/18 (docs/ai-agent/r.md)",
            changed_paths=["docs/x/navody/a-qa.json"],
            cwd=self.d, stream="montalu1")
        self.assertIsNone(blk)

    def test_non_guide_change_passes(self):
        blk = self.air._handoff_agent_eval_preflight(
            "RFR", changed_paths=["addons/x/models/y.py"],
            cwd=self.d, stream="montalu1")
        self.assertIsNone(blk)

    def test_undeterminable_diff_fails_open(self):
        blk = self.air._handoff_agent_eval_preflight(
            "RFR", changed_paths=None, cwd="/nonexistent-xyz-1077",
            stream="montalu1")
        self.assertIsNone(blk)

    def test_none_fact_from_stream_file_makes_bare_na_pass(self):
        sd = os.path.join(self.d, ".claude", "streams")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "montalu1.md"), "w", encoding="utf-8") as fh:
            fh.write("agent_eval: NONE — bez agenta\n")
        blk = self.air._handoff_agent_eval_preflight(
            "AI-eval: n/a",
            changed_paths=["docs/x/navody/a-qa.json"],
            cwd=self.d, stream="montalu1")
        self.assertIsNone(blk)

    def test_none_fact_from_stream_file_still_needs_the_line(self):
        sd = os.path.join(self.d, ".claude", "streams")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "montalu1.md"), "w", encoding="utf-8") as fh:
            fh.write("agent_eval: NONE — bez agenta\n")
        blk = self.air._handoff_agent_eval_preflight(
            "READY-FOR-REVIEW: done.",
            changed_paths=["docs/x/navody/a-qa.json"],
            cwd=self.d, stream="montalu1")
        self.assertIsNotNone(blk)
        self.assertIn("handoff BLOCK", blk)


# --------------------------------------------------------------------------- #
# 4b. WIRING — the gate must fire on BOTH hand-off paths (review 🔴)
# --------------------------------------------------------------------------- #
class TestComposerWiring(unittest.TestCase):
    """The gate is the SOLE enforcement of #1077 (no Stop-hook backstop), so it
    must be called in BOTH the compose path (`cmd_handoff`) AND the `--body-file`
    pass-through (`_cmd_handoff_post_body_file`) — the path the odoo-erp guide
    streams actually hand off through. #1106 established the both-paths
    convention; a single call site (compose-only) leaves the gate bypassable via
    the primary path."""
    def setUp(self):
        import airuleset
        import inspect
        self.air = airuleset
        self.inspect = inspect

    def test_wired_into_compose_path(self):
        src = self.inspect.getsource(self.air.cmd_handoff)
        self.assertIn("_handoff_agent_eval_preflight(", src)

    def test_wired_into_body_file_pass_through_path(self):
        src = self.inspect.getsource(self.air._cmd_handoff_post_body_file)
        self.assertIn("_handoff_agent_eval_preflight(", src)


# --------------------------------------------------------------------------- #
# 5. rule file — frontmatter globs + rule text
# --------------------------------------------------------------------------- #
class TestRuleFile(unittest.TestCase):
    def test_rule_exists(self):
        self.assertTrue(RULE.exists(), "rules/guide-agent-eval.md missing")

    def test_frontmatter_matches_guide_sources(self):
        globs = _frontmatter_paths(RULE)
        self.assertTrue(globs, "no paths: globs in frontmatter")
        pats = [_glob_to_re(g) for g in globs]

        def matches(path):
            return any(p.match(path) for p in pats)

        self.assertTrue(matches("docs/montalu/navody/sklad-qa.json"))
        self.assertTrue(matches("docs/montalu/build-vyroba-guide.py"))
        self.assertTrue(matches("docs/montalu/navody_sklad_sections.py"))
        # review F3/#7: a non-`navody`-prefixed file INSIDE a navody/ dir is a
        # guide source per the gate, so the frontmatter must inject on it too.
        self.assertTrue(matches("docs/slovnormal/navody/prehlad.html"))
        self.assertTrue(matches("docs/x/navody/img/diagram.svg"))
        self.assertFalse(matches("addons/x/models/y.py"))

    def test_frontmatter_superset_of_gate_sources(self):
        # the injected `paths:` set must be a SUPERSET of the gate's source set,
        # or an operator edits a guide source without ever seeing the advisory
        # rule on Read (#1073/#1099 dual-definition drift). Check the gate's own
        # positive cases all match a frontmatter glob.
        pats = [_glob_to_re(g) for g in _frontmatter_paths(RULE)]
        for p in ("docs/montalu/navody/sklad-qa.json",
                  "docs/slovnormal/navody/prehlad.html",
                  "docs/x/navody/img/diagram.svg",
                  "docs/montalu/build-vyroba-guide.py",
                  "docs/montalu/navody_sklad_sections.py"):
            self.assertTrue(ae.is_guide_source(p), p)
            self.assertTrue(any(rx.match(p) for rx in pats),
                            "frontmatter misses guide source %s" % p)

    def test_rule_text_names_the_ai_eval_line(self):
        text = RULE.read_text(encoding="utf-8")
        self.assertIn("AI-eval:", text)

    def test_rule_text_references_project_runner_not_rebuilt(self):
        text = RULE.read_text(encoding="utf-8")
        self.assertIn("eval_navody.py", text)

    def test_rule_text_names_the_fixture_schema(self):
        text = RULE.read_text(encoding="utf-8")
        self.assertIn("-qa.json", text)

    def test_rule_text_names_the_na_escape(self):
        text = RULE.read_text(encoding="utf-8")
        self.assertIn("n/a", text)

    def test_rule_text_wrong_answer_is_a_fix_not_ignored(self):
        text = RULE.read_text(encoding="utf-8").lower()
        # a wrong answer = a guide fix OR an agent prompt/tool ticket, never ignored
        self.assertTrue("copy" in text or "kópi" in text or "kopi" in text)


# --------------------------------------------------------------------------- #
# 6. profile lists the rule (symlinked, not @import — zero always-on bytes)
# --------------------------------------------------------------------------- #
class TestProfile(unittest.TestCase):
    def test_profile_lists_the_rule(self):
        entries = [ln.strip() for ln in PROFILE.read_text().splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
        self.assertIn("rules/guide-agent-eval.md", entries)

    def test_rule_is_categorised_as_a_rule_not_a_module(self):
        # cli_config.categorize_entries routes `rules/` to symlinks, so no
        # always-on CLAUDE.md bytes are added.
        sys.path.insert(0, str(REPO))
        from cli_config import parse_profile, categorize_entries
        modules, rules = categorize_entries(parse_profile(PROFILE))
        self.assertIn("rules/guide-agent-eval.md", rules)
        self.assertNotIn("rules/guide-agent-eval.md", modules)


# --------------------------------------------------------------------------- #
# 7. situational-triggers row (loads the rule at the handoff action)
# --------------------------------------------------------------------------- #
class TestTriggerRow(unittest.TestCase):
    def _row(self):
        for parts in _conf_rows():
            if parts and parts[0] == "guide-agent-eval":
                return parts
        return None

    def test_row_present(self):
        self.assertIsNotNone(self._row(), "guide-agent-eval trigger row missing")

    def test_row_parses_four_columns(self):
        row = self._row()
        self.assertIn(len(row), (4, 5))

    def test_row_tool_and_body(self):
        topic, tool, pattern, body = self._row()[:4]
        self.assertTrue(re.fullmatch(tool, "Bash"))
        self.assertEqual(body, "rules/guide-agent-eval.md")

    def test_row_pattern_matches_handoff_command(self):
        _topic, _tool, pattern, _body = self._row()[:4]
        cmd = "python3 ~/devel/airuleset/airuleset.py handoff --repo x/y --issue 1"
        self.assertTrue(re.search(pattern, cmd))

    def test_row_pattern_does_not_match_unrelated_command(self):
        _topic, _tool, pattern, _body = self._row()[:4]
        self.assertIsNone(re.search(pattern, "git status"))

    def test_row_body_file_exists(self):
        self.assertTrue(RULE.exists())


# --------------------------------------------------------------------------- #
# 8. doctrine bullet
# --------------------------------------------------------------------------- #
class TestDoctrine(unittest.TestCase):
    def test_internals_gates_has_1077_bullet(self):
        text = INTERNALS_GATES.read_text(encoding="utf-8")
        self.assertIn("#1077", text)
        self.assertIn("AI-eval", text)


# --------------------------------------------------------------------------- #
# 9. ReDoS — every matcher LINEAR (repo #577/#1010 discipline)
# --------------------------------------------------------------------------- #
class TestReDoS(unittest.TestCase):
    def test_guide_source_linear_on_pathological_nesting(self):
        import time
        path = "docs/t/" + "seg/" * 20000 + "x" * 20000 + "Y"
        t0 = time.monotonic()
        ae.is_guide_source(path)
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_ai_eval_line_linear_on_pathological_whitespace(self):
        import time
        body = "AI-eval: n" + " " * 40000 + "X"
        t0 = time.monotonic()
        ae.agent_eval(["docs/x/navody/a-qa.json"], body, cwd="/tmp", fact=None)
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_fixture_extraction_linear_on_long_nonwhitespace_token(self):
        # review F1/#2: the fixture extraction must be LINEAR on a long
        # NON-whitespace token with no `-qa.json` (the case a `\S+-qa\.json`
        # regex made O(n²)). A whitespace-broken input (above) never exercised it.
        import time
        body = "AI-eval: " + "a" * 200000
        t0 = time.monotonic()
        ae.agent_eval(["docs/x/navody/a-qa.json"], body, cwd="/tmp", fact=None)
        self.assertLess(time.monotonic() - t0, 0.5)


if __name__ == "__main__":
    unittest.main()
