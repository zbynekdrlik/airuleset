"""#216 -- a GLOBAL reachability guarantee, baked into the fail-closed push
suite: an obligation only holds when (a) it lives on a surface that
actually loads in the executing agent's context AND (b) a mechanical gate
checks a produced artifact. Two concrete regressions proved this class:
`e9d1022` (2026-07-09) moved the design-before-code step into an on-demand
skill body -- unreachable by a dispatched worker for 18 days, nothing
detected it; #215's `agents/autopilot-worker.md:267` claimed
playbook-review is "enforced by the Stop gate `stop-check-playbook-review.sh`"
while that hook is registered on `Stop` only, which never fires for a
subagent.

Three checks, all offline (no network, no live session needed -- this runs
in `airuleset.py push`'s fail-closed suite):

1. Every "enforced by `<hook>`" claim inside `agents/*.md` (unambiguously
   SUBAGENT-context prose -- an agent file IS the subagent's system prompt)
   names a hook registered under an event that genuinely fires for a
   subagent (`SubagentStop`, or `PreToolUse` matching the tool). A bare
   `Stop`-only registration is the exact #215 false-claim shape.
2. Every REQUIRED line of the autopilot-worker evidence-block templates is
   a key in an explicit, hand-maintained COVERAGE_TABLE (hook-backed /
   supervisor-reverified / self-audit-with-no-hook) -- so a newly added
   field can never ship with an undecided coverage story. The test fails
   the moment the template and the table drift apart, in EITHER direction.
3. A curated list of worker-path STUB-module obligations (a module that
   points at an on-demand skill for its content) each still carry a real
   restatement keyword inside `agents/autopilot-worker.md` -- the e9d1022
   regression shape, mechanically re-checked.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"
HOOKS_JSON = ROOT / "settings" / "hooks.json"
AGENTS_DIR = ROOT / "agents"


def _hook_registrations():
    """`{hook_filename: {(event, matcher), ...}}` from settings/hooks.json."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    out = {}
    for event, entries in data.items():
        for entry in entries:
            matcher = entry.get("matcher", "")
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                m = re.search(r"hooks/([\w.-]+\.sh)", cmd)
                if not m:
                    continue
                out.setdefault(m.group(1), set()).add((event, matcher))
    return out


# Events that genuinely fire FOR A SUBAGENT (agents/*.md is always
# subagent-context -- it IS the dispatched worker's system prompt).
# `PostToolUse` fires per-tool-call same as `PreToolUse`, just after
# instead of before -- confirmed by `post-record-subagent-bg-launch.sh`
# (PostToolUse on Bash|Monitor|Agent), whose own docstring documents it
# firing "from SUBAGENT context (payload carries agent_id)". `Stop` is
# deliberately EXCLUDED: it fires for the SUPERVISOR/main session only.
SUBAGENT_REACHABLE_EVENTS = {"SubagentStop", "PreToolUse", "PostToolUse"}

# Any backtick-quoted `<hook>.sh` mention (with or without a `hooks/`
# prefix). Deliberately NOT anchored to one exact enforcement phrase --
# an earlier version anchored on "enforced by ... `hook.sh`" and went
# VACUOUS the moment this diff's own prose stopped using that exact
# wording (adversarial-review finding, #216 hardening): 0 real matches,
# only the hardcoded self-check sample ever exercised the regex. Instead,
# find every hook MENTION, then separately judge whether the surrounding
# text actually CLAIMS enforcement.
_HOOK_MENTION_RE = re.compile(r"`(?:hooks/)?([\w.-]+\.sh)`")

# An enforcement CLAIM near a hook mention -- deliberately narrower than a
# bare "check"/"checks" (which also fires on an HONEST non-claim like "the
# check fires at the SUPERVISOR's stop... `X.sh` actually checks" --
# confirmed live: a naive "checks?" keyword flagged the #215 CORRECTED,
# honest text as a false claim, which would be exactly the kind of
# self-tripping bug this repo's own playbook warns about repeatedly).
_ENFORCEMENT_CLAIM_RE = re.compile(
    r"enforc|hook-enforced|block(?:s|ed)?\s+your\s+stop|"
    r"block(?:s|ed)?\s+(?:the\s+)?commit|checked\s+by",
    re.IGNORECASE,
)
_CLAIM_WINDOW = 150


def _hook_claims(text):
    """[(hook_filename, window_text), ...] for every hook mention in
    `text` whose surrounding window contains an enforcement CLAIM (not
    just an incidental mention)."""
    out = []
    for m in _HOOK_MENTION_RE.finditer(text):
        window = text[max(0, m.start() - _CLAIM_WINDOW):m.end() + _CLAIM_WINDOW]
        if _ENFORCEMENT_CLAIM_RE.search(window):
            out.append((m.group(1), window))
    return out


class TestEnforcedByClaimsInAgentsAreReachable(unittest.TestCase):
    """#216 item 1 -- the #215 false-claim shape, generalized."""

    def test_every_enforcement_claim_in_agents_names_a_subagent_reachable_hook(self):
        registrations = _hook_registrations()
        offenders = []
        for path in sorted(AGENTS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for hook, _window in _hook_claims(text):
                events = {ev for ev, _ in registrations.get(hook, set())}
                if not events:
                    offenders.append(
                        "%s claims `%s` enforces something, but that hook "
                        "is not registered in settings/hooks.json at all"
                        % (path.name, hook))
                    continue
                if not (events & SUBAGENT_REACHABLE_EVENTS):
                    offenders.append(
                        "%s claims `%s` enforces something for the WORKER, "
                        "but it is only registered on %s -- none of which "
                        "fire for a dispatched subagent (need SubagentStop "
                        "or PreToolUse)" % (path.name, hook, sorted(events)))
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_detector_is_not_vacuous_against_current_content(self):
        # The exact regression the adversarial review caught: a detector
        # that finds ZERO real claims in the current file gives false
        # confidence -- only its own hardcoded self-check sample would be
        # exercising it. There must be real, non-hardcoded claims found.
        text = WORKER_MD.read_text(encoding="utf-8")
        claims = _hook_claims(text)
        self.assertGreater(len(claims), 0,
                           "the detector found ZERO real enforcement claims "
                           "in agents/autopilot-worker.md -- it is vacuous "
                           "against current content")

    def test_an_honest_non_claim_is_not_flagged(self):
        # The #215 CORRECTED text explicitly says a hook does NOT fire for
        # the worker -- "the check fires at the SUPERVISOR's stop... it is
        # THAT report `stop-check-playbook-review.sh` actually checks". A
        # naive "checks?" keyword would misread this honest disclaimer as
        # a claim (confirmed while building this detector) and flag
        # correct, honest prose as a violation.
        sample = (
            "the check fires at the SUPERVISOR's stop, once the worker's "
            "evidence is relayed into the report, and it is THAT report "
            "`stop-check-playbook-review.sh` actually checks -- never your "
            "own SubagentStop turn."
        )
        self.assertEqual(_hook_claims(sample), [])

    def test_the_detector_catches_the_215_false_claim_shape(self):
        # Self-check: the detector must actually MATCH the historical false
        # claim's exact wording, or item 1 above is silently vacuous.
        sample = ("MUST carry the line (enforced by the Stop gate "
                  "`stop-check-playbook-review.sh`).")
        claims = _hook_claims(sample)
        self.assertEqual([c[0] for c in claims], ["stop-check-playbook-review.sh"],
                         "detector does not match the #215 false-claim "
                         "shape it exists to catch")


# --------------------------------------------------------------------------- #
# #216 item 2 -- coverage table for the evidence-block obligations
# --------------------------------------------------------------------------- #

# mechanism in {"hook", "supervisor-reverify", "self-audit"}
#   "hook"               -> `hooks` lists filenames that must be registered
#                            under SubagentStop (or PreToolUse) and must
#                            genuinely exist.
#   "supervisor-reverify" -> checked by the SUPERVISOR re-deriving from
#                            primary sources (gh/CI/DOM) at Step 4 of
#                            skills/autopilot/SKILL.md -- no hook needed,
#                            the supervisor IS the mechanical check.
#   "self-audit"          -> plain prose the worker produces; no mechanical
#                            check exists, and none is claimed. Declaring
#                            this explicitly is the whole point of #216 --
#                            an undecided field is the bug, not an
#                            unenforced one.
COVERAGE_TABLE = {
    "issues": ("supervisor-reverify", ()),
    "plan": ("self-audit", ()),
    "validated": ("hook", ("subagent-stop-check-design.sh",)),
    "approach": ("hook", ("subagent-stop-check-design.sh",
                          "block-commit-without-design.sh")),
    "review": ("hook", ("subagent-stop-check-design.sh",)),
    "achieved": ("self-audit", ()),
    "pr": ("supervisor-reverify", ()),
    "merge_sha": ("hook", ("subagent-stop-check-design.sh",
                           "subagent-stop-check-run-card.sh")),
    "main_ci": ("supervisor-reverify", ()),
    "integration_ci": ("supervisor-reverify", ()),
    "deployed_version": ("supervisor-reverify", ()),
    "cards_fired": ("hook", ("subagent-stop-check-run-card.sh",)),
    "issue_state": ("hook", ("subagent-stop-check-design.sh",
                             "subagent-stop-check-run-card.sh")),
    "dropped": ("supervisor-reverify", ()),
    "obsolete_closed": ("supervisor-reverify", ()),
    "unverified": ("self-audit", ()),
    "filed": ("self-audit", ()),
    "branch": ("supervisor-reverify", ()),
    "worktree": ("supervisor-reverify", ()),
    "local_verify": ("self-audit", ()),
    "ready_for_review": ("supervisor-reverify", ()),
    "obsolete_handed_off": ("supervisor-reverify", ()),
    "lane_return": ("hook", ("subagent-stop-check-lane-return.sh",)),
}


def _template_fields(fence_text):
    return re.findall(r"(?m)^([A-Za-z_]+):", fence_text)


def _evidence_templates():
    text = WORKER_MD.read_text(encoding="utf-8")
    idx = text.find("## FINAL MESSAGE")
    self_check = text[idx:] if idx != -1 else text
    fences = re.findall(r"```\n(.*?)\n```", self_check, re.S)
    return fences


class TestEvidenceBlockCoverageTable(unittest.TestCase):

    def test_both_templates_exist(self):
        fences = _evidence_templates()
        self.assertGreaterEqual(len(fences), 2,
                                "expected the full-authority and "
                                "fork-no-merge evidence-block templates")

    def test_every_field_in_every_template_has_a_coverage_decision(self):
        fences = _evidence_templates()
        undecided = []
        for fence in fences:
            for field in _template_fields(fence):
                if field not in COVERAGE_TABLE:
                    undecided.append(field)
        self.assertEqual(sorted(set(undecided)), [],
                         "evidence-block field(s) with NO coverage decision "
                         "in COVERAGE_TABLE -- update the table (this is "
                         "the #216 guarantee: a new field must never ship "
                         "with an undecided coverage story): %s"
                         % sorted(set(undecided)))

    def test_the_table_names_no_field_the_templates_no_longer_have(self):
        fences = _evidence_templates()
        all_fields = set()
        for fence in fences:
            all_fields.update(_template_fields(fence))
        stale = set(COVERAGE_TABLE) - all_fields
        self.assertEqual(stale, set(),
                         "COVERAGE_TABLE names field(s) no template has any "
                         "more -- the table has drifted from the template, "
                         "update one or the other: %s" % sorted(stale))

    def test_every_hook_backed_field_names_a_real_subagent_reachable_hook(self):
        registrations = _hook_registrations()
        offenders = []
        for field, (mechanism, hooks) in COVERAGE_TABLE.items():
            if mechanism != "hook":
                continue
            if not hooks:
                offenders.append("%s: mechanism=hook but names no hooks" % field)
                continue
            for hook in hooks:
                path = ROOT / "hooks" / hook
                if not path.is_file():
                    offenders.append("%s: hook file missing: %s" % (field, hook))
                    continue
                events = {ev for ev, _ in registrations.get(hook, set())}
                if not (events & SUBAGENT_REACHABLE_EVENTS):
                    offenders.append(
                        "%s: %s is not registered on a subagent-reachable "
                        "event (got %s)" % (field, hook, sorted(events)))
        self.assertEqual(offenders, [], "\n".join(offenders))


# --------------------------------------------------------------------------- #
# #216 item 3 -- stub-module obligations still restated worker-side
# --------------------------------------------------------------------------- #

# {module filename under modules/: keyword(s) that must appear in
# agents/autopilot-worker.md, proving the obligation was RESTATED there and
# not left to reach the worker only via the (unreachable-to-a-subagent)
# skill body the module stub points at.}
STUB_MODULE_RESTATEMENTS = {
    "modules/quality/verify-issue-still-valid.md": ("VALIDATE",),
    "modules/quality/regression-test-first.md": ("RED", "GREEN"),
    "modules/core/pr-merge-policy.md": ("auto-merge",),
    "modules/core/ci-push-discipline.md": ("push",),
    "modules/deploy/post-deploy-verification.md": ("post-deploy verification",),
}


class TestStubModuleObligationsAreRestatedWorkerSide(unittest.TestCase):
    """#216 item 3 -- the e9d1022 regression shape, mechanically re-checked.
    A module that is a STUB (points at an on-demand skill for its real
    content) must have its obligation RESTATED in agents/autopilot-worker.md
    -- the one surface that genuinely reaches a dispatched worker -- or the
    obligation is only as reachable as the skill body it stubs to, which is
    to say: not reachable at all."""

    def setUp(self):
        self.worker_text = WORKER_MD.read_text(encoding="utf-8")

    def test_each_curated_module_is_still_a_stub(self):
        for rel in STUB_MODULE_RESTATEMENTS:
            path = ROOT / rel
            self.assertTrue(path.is_file(), "module vanished: %s" % rel)
            text = path.read_text(encoding="utf-8")
            self.assertIn("on-demand skill", text,
                         "%s is no longer a stub pointing at an on-demand "
                         "skill -- either it now carries full content "
                         "inline (fine, but then remove it from this "
                         "curated list) or something else changed; "
                         "re-verify before trusting this entry" % rel)

    def test_each_stub_obligation_is_restated_in_the_worker_prompt(self):
        missing = []
        for rel, keywords in STUB_MODULE_RESTATEMENTS.items():
            if not any(kw in self.worker_text for kw in keywords):
                missing.append("%s: none of %r found in "
                               "agents/autopilot-worker.md" % (rel, keywords))
        self.assertEqual(missing, [], "\n".join(missing))


class TestWorkerDocDescribesContinuousIntegration(unittest.TestCase):
    """(#462) agents/autopilot-worker.md must describe the supervisor
    integrating ready branches CONTINUOUSLY under the (#8) integration mutex --
    one integration cycle (merge->gates->push) at a time as each branch
    returns -- NOT the pre-(#456) "ONCE for the whole round after every worker
    returned" model. The worker still stops at green-local (step 4); only the
    description of WHEN the supervisor integrates changed. This is a doc lock:
    a reduced-context session reading the stale round-wording is misled about
    when its returned branch is picked up."""

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")

    def test_stale_once_per_round_integration_timing_is_gone(self):
        self.assertNotIn(
            "ONCE for the whole round", self.text,
            "pre-(#456) once-per-round integration timing must be reworded")
        # normalise the hyphen so 'round-integration' and 'round integration'
        # are both caught; the genuinely-correct DISPATCH-round uses (sibling
        # workers dispatched in the SAME round, per-round scratchpad namespacing)
        # never spell the two words adjacent, so this only catches the stale
        # integration-timing phrasing.
        flat = self.text.replace("-", " ")
        self.assertNotIn(
            "round integration", flat,
            "pre-(#456) 'round-integration' timing phrasing must be reworded")
        # (#462 review) the (#8) lock is an INTEGRATION mutex, never a dispatch
        # lock -- no surviving claim may frame it as serializing DISPATCH or as
        # acquired before dispatch (the exact pre-(#456) mental model killed).
        self.assertNotIn(
            "cross-session dispatch lock", self.text,
            "pre-(#456) 'dispatch lock' framing of the (#8) lock must be reworded")
        self.assertNotIn(
            "serializes dispatch", self.text,
            "the (#8) lock serializes INTEGRATION, never dispatch (#456)")
        # per-integration-cycle run-card, never a single round-wide one.
        self.assertNotIn(
            "round's run-card", self.text,
            "run-card is per-ticket at its integration cycle, not round-wide")

    def test_continuous_integration_phrasing_is_present(self):
        self.assertIn(
            "integration mutex", self.text,
            "must name the (#456) integration mutex the supervisor holds "
            "per integration cycle")
        self.assertIn(
            "one integration cycle at a time", self.text,
            "must describe continuous, one-cycle-at-a-time integration as "
            "branches return")


if __name__ == "__main__":
    unittest.main()
