#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (AskUserQuestion)
# Auto-blocks questions that have pre-defined answers.
# Exit 2 = block the tool call. Claude sees stderr as the reason.

command -v jq &>/dev/null || exit 0

INPUT=$(cat)
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty' 2>/dev/null || echo "")
[ -z "$TOOL_INPUT" ] && exit 0

# Visual companion question (all phrasings)
if echo "$TOOL_INPUT" | grep -qiE "visual.?companion|mockup.*browser|show.*it.*in.*a.*web.*browser|want to try it|visual.*option|browser.*preview"; then
    echo "BLOCKED: Visual companion is always enabled. Do not ask — just use it. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# Subagent vs inline/sequential question (all phrasings)
# Catches: "subagent or sequential", "Subagent-Driven ... Inline Execution",
# "which execution approach", "Two execution options", "Which approach?"
if echo "$TOOL_INPUT" | grep -qiE "subagent.?driven|subagent.*(or|vs).*(sequential|inline)|agent.?driven.*(or|vs)|which.*execution.*approach|two.*execution.*option|execution.*option.*subagent|inline.*execution.*subagent|subagent.*inline.*execution"; then
    echo "BLOCKED: Always use subagent-driven execution. Do not ask. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# "Ready to proceed / say go / which approach" style questions — process only.
# IMPORTANT: keep these patterns narrow — UX/copy/wording/design preference questions
# (e.g. "which wording do you prefer") are LEGITIMATE ambiguous-scope questions and
# MUST NOT be blocked. Match only process/workflow phrasings.
if echo "$TOOL_INPUT" | grep -qiE "say.*go|shall.*(i|we).*proceed|ready.*to.*(execute|start|proceed|continue|move on)|ready.*when.*you.*are|if.*good.*say|if.*(looks|seems).*good|want.*me.*to.*proceed|proceed.*to.*next.*step|ready.*for.*next.*step|invoke.*superpowers:writing-plans|invoke.*superpowers:executing-plans|which (approach|execution|strategy|workflow|method|path forward)\??|how.*would.*you.*like.*to.*proceed|which (approach|execution|strategy|workflow|method).*do you (prefer|want)"; then
    echo "BLOCKED: This is a process / chain-stop question — pre-answered. Chain directly to the next step. If the user approved the design/plan, proceed autonomously. See ask-before-assuming.md pre-answered questions table. (NOTE: UX/copy/wording/design preference questions are legitimate — ask those freely.)" >&2
    exit 2
fi

# Spec / plan / design review handoff — always proceed autonomously
# Catches: "review the spec/plan/design and let me know", "before I hand off to writing-plans",
# "any changes before I proceed", "before moving on to implementation",
# "Does this design/spec/plan look right/good/ok?", "If yes, I'll commit/write/save"
if echo "$TOOL_INPUT" | grep -qiE "review.*the.*(spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before.*(i|we).*(hand.?off|move.?on|continue|proceed)|before.*(handing|moving).?(off|on)|hand.?off.*to.*writing.?plans|review.*before.*(implementation|implement|next)|(any|need) (changes?|edits?|tweaks?).*before|(does|is) (this|the) (design|spec|plan|approach|architecture|interface|api|schema|model|structure|layout|flow) (look|seem|sound) (right|good|ok|fine|correct|reasonable)|(does|is) (this|the).*(look|seem|sound) (right|good|ok|fine|correct|reasonable).*(specifically|specifically the)|if (yes|good|ok|approved),? .*(write|create|commit|push|save|file|spec|generate|hand.?off|proceed)|(approve|approved|sign.?off|sign off|green.?light) (this|the) (design|spec|plan|approach|architecture)"; then
    echo "BLOCKED: Spec/plan/design review handoffs are pre-answered — always proceed autonomously to the next step (writing-plans → executing-plans → commit). 'Does this design look right? If yes, I'll commit' is a process pause; the user already approved the workflow when they invoked brainstorming/spec-writing. Just commit and move on. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# Quality-bypass shortcut menus — NEVER offer these as options
# Catches: "admin-merge", "your call", "realistic options" with bypass options,
# "merge despite (anything)", "close and roll into next PR", "stop the runner to merge",
# "you decide on merge", "investigate ... or merge", "functionally ready" minimizers,
# UNSTABLE-but-merge-anyway prompts, "informational check" dismissals
if echo "$TOOL_INPUT" | grep -qiE "admin.?merge|merge --admin|--admin.*merge|bypass.*(branch.?protection|gate|check)|merge.*despite|merge.*broken.*(code|ci)|skip.*(failing|broken).*(test|check|gate)|disable.*(failing|broken).*check|close.*pr.*roll.*into|roll.*into.*next.*pr|your.?call|how.*should.*(we|i).*handle.*(failing|gated|broken|stuck)|stop.*runner.*(to|so).*merge|you decide(.*merge)?|your decision|up to you|investigate.*(or|vs).*merge|merge.*(or|vs).*investigate|functionally ready|essentially (clean|ready|mergeable)|good enough to merge|won.?t claim.*clean|UNSTABLE.*merge|merge.*UNSTABLE|informational (check|failure).*(merge|skip|ignore|bypass)|advisory only.*(merge|skip|ignore|bypass)|project precedent.*merg|previous pr.*merged.*same"; then
    echo "BLOCKED: Quality-bypass shortcuts are NEVER options. Failing CI / UNSTABLE state = fix the root cause autonomously. Branch protection cannot be bypassed. 'Investigate or merge despite' is a false binary — investigation is the only path. UNSTABLE ≠ clean. 'Informational check' failures are still failures. Past sloppy merges do not authorize new sloppy merges. NEVER propose admin-merge, 'close and roll into next PR', 'merge despite X', 'you decide on merge', 'functionally ready', or any other shortcut menu. The agent makes the quality call autonomously: investigate, fix, push, monitor until truly clean+green. See autonomous-quality-discipline.md, pr-merge-policy.md, ask-before-assuming.md." >&2
    exit 2
fi

exit 0
