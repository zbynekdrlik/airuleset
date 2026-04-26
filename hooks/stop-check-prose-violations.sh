#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# WARNING-ONLY: detects when Claude asked a pre-answered question in prose
# instead of applying the fixed answer. Warns Claude for the next turn.
# Does NOT block (exit 0) — blocking Stop causes infinite loops.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# Check for subagent vs inline prose question
if echo "$MSG" | grep -qiE "subagent.?driven.*inline|two execution options|which (approach|execution)|subagent or (sequential|inline)"; then
    echo "VIOLATION: You asked 'subagent or inline' in prose. This is a pre-answered question — always use subagent-driven. Next time, just dispatch subagents without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for visual companion prose question
if echo "$MSG" | grep -qiE "want to try.*(visual|mockup|browser)|easier to explain.*browser|visual companion"; then
    echo "VIOLATION: You offered visual companion in prose. This is a pre-answered question — always yes. Next time, just use it without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for "say go / ready to proceed" prose questions
if echo "$MSG" | grep -qiE "say.?go|shall (i|we) proceed|if good.?say|ready when you are|ready for.?next|ready to execute"; then
    echo "VIOLATION: You asked the user to 'say go' or confirm proceed in prose. The plan is approved — chain directly to the next step without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for spec/plan/design review handoff prose
if echo "$MSG" | grep -qiE "review the (spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before (i|we) hand.?off|before (handing|moving).?(off|on)|hand.?off to writing.?plans|any (changes?|edits?|tweaks?) before"; then
    echo "VIOLATION: You stopped to ask the user to review the spec/plan/design before handing off. This is a pre-answered question — always proceed autonomously to the next step. The user approved the workflow when they invoked brainstorming/spec-writing. Next time, chain directly into writing-plans → executing-plans without pausing. See ask-before-assuming.md pre-answered table." >&2
fi

# Check completion report has plan-check + /review audit lines
if echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
    HAS_PLAN_CHECK=$(echo "$MSG" | grep -qiE "/plan.?check|plan-check.*(fulfilled|passed|clean|complete)|✅.*plan.?check" && echo 1 || echo 0)
    HAS_REVIEW=$(echo "$MSG" | grep -qiE "/review|review.*(clean|0 🔴|no critical|no warnings|addressed in commit)|✅.*review" && echo 1 || echo 0)
    if [ "$HAS_PLAN_CHECK" = "0" ] || [ "$HAS_REVIEW" = "0" ]; then
        echo "VIOLATION: You sent a Work Complete report without running the mandatory pre-completion gate. completion-report.md REQUIRES two audit lines BEFORE the report:" >&2
        [ "$HAS_PLAN_CHECK" = "0" ] && echo "  - MISSING: '✅ /plan-check: N/N fulfilled' — invoke the plan-check skill, fix any NOT DONE items, then add the line." >&2
        [ "$HAS_REVIEW" = "0" ] && echo "  - MISSING: '✅ /review: clean — 0 🔴 0 🟡 (or addressed in commit <sha>)' — apply /review standards (Correctness/Security/Performance/Maintainability/Style), fix every 🔴 and 🟡 finding, then add the line." >&2
        echo "Run the gate autonomously — the user shouldn't have to ask. See completion-report.md → 'Pre-completion gate (MANDATORY)'." >&2
    fi
fi

# Check for quality-bypass shortcut menus or "your call" delegation
if echo "$MSG" | grep -qiE "admin.?merge|merge --admin|--admin.*merge|bypass.*(branch.?protection|gate)|merge.*despite|merge.*broken.*(code|ci)|close.*pr.*roll.*into|roll.*into.*next.*pr|stop.*runner.*(to|so).*merge|your call|realistic options.*[12]\.|cheaper option|quicker option|easier path|you decide(.*merge)?|your decision|up to you.*merge|investigate.*(or|vs).*merge|merge.*(or|vs).*investigate|functionally ready|essentially (clean|ready|mergeable)|good enough to merge|won.?t claim.*clean|UNSTABLE.*merge|merge.*UNSTABLE|informational (check|failure).*(merge|skip|ignore)|advisory only.*(merge|skip|ignore)|project precedent.*merg|previous pr.*merged.*same"; then
    echo "VIOLATION: You offered quality-bypass shortcuts (admin-merge / close PR / 'your call' / 'merge despite' / 'you decide on merge' / 'functionally ready' / 'UNSTABLE but merge anyway' / 'informational check, merge it' / 'project precedent'). These are NEVER options. A failing gate or UNSTABLE state = fix the root cause, autonomously. Hours of overnight agentic work require autonomous decisions. The user wants the harder, correct path EVERY time — never the cheaper/quicker shortcut. See autonomous-quality-discipline.md, pr-merge-policy.md, ask-before-assuming.md." >&2
fi

# Check for PR completion message missing the PR URL
# Signal: completion language about a PR but no https://github.com/.../pull/N URL anywhere in message
if echo "$MSG" | grep -qiE "awaiting (your|merge)|pr (is )?(ready|mergeable)|mergeable[, ]+(clean|all)|all checks (are )?green|ready to merge|per pr-merge-policy|awaiting.*\"merge it\""; then
    if ! echo "$MSG" | grep -qE "https?://github\.com/[^[:space:]]+/pull/[0-9]+"; then
        echo "VIOLATION: You announced PR completion ('mergeable clean', 'awaiting merge', 'all checks green', etc.) without providing the PR URL. completion-report.md and pr-merge-policy.md MANDATE the PR URL on the completion line: '✅ PR: <https://github.com/.../pull/N> — mergeable, clean'. Always paste the full URL — the user works remotely and cannot click 'PR #11'. Use the EXACT completion-report.md template, not a prose summary." >&2
    fi
fi

# Check for deploy verification claim missing dashboard/target URL
if echo "$MSG" | grep -qiE "deploy.*(verified|complete|done|success)|verified.*deploy|deployed.*(to|successfully)" \
   && echo "$MSG" | grep -qiE "dashboard|frontend|ui|app"; then
    if ! echo "$MSG" | grep -qE "https?://[^[:space:]]+"; then
        echo "VIOLATION: You announced a verified deploy with a dashboard/UI but provided no URL. completion-report.md mandates '🌐 Dashboard: <url>' on completion. Always include the live URL the user can click. See no-localhost-urls.md — verify it returns 200 first, never paste localhost." >&2
    fi
fi

exit 0
