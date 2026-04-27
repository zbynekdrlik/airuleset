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

# Check completion report has Goal + What changed + plan-check + /review lines
if echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
    HAS_GOAL=$(echo "$MSG" | grep -qiE "\*\*Goal:?\*\*|^Goal:" && echo 1 || echo 0)
    HAS_OUTCOME=$(echo "$MSG" | grep -qiE "\*\*What changed:?\*\*|\*\*Outcome:?\*\*|^What changed:|^Outcome:" && echo 1 || echo 0)
    HAS_PLAN_CHECK=$(echo "$MSG" | grep -qiE "/plan.?check|plan-check.*(fulfilled|passed|clean|complete)|✅.*plan.?check" && echo 1 || echo 0)
    # /review audit must include all THREE counters (🔴 🟡 🔵) — no skipping minor findings.
    # Accept either explicit "0 🔴 0 🟡 0 🔵" or "all findings addressed" with 🔵 mentioned.
    HAS_REVIEW=$(echo "$MSG" | grep -qE "/review.*0 🔴.*0 🟡.*0 🔵|/review.*all (findings|issues|items).*addressed|review.*0 🔴.*0 🟡.*0 🔵.*addressed in commit|✅.*review.*0 🔴.*0 🟡.*0 🔵" && echo 1 || echo 0)
    if [ "$HAS_GOAL" = "0" ] || [ "$HAS_OUTCOME" = "0" ] || [ "$HAS_PLAN_CHECK" = "0" ] || [ "$HAS_REVIEW" = "0" ]; then
        echo "VIOLATION: Work Complete report is missing required lines. completion-report.md MANDATES this structure (audits at TOP, Goal/What changed/PR URL at BOTTOM — terminal scrolls, last lines are what the user sees):" >&2
        [ "$HAS_GOAL" = "0" ] && echo "  - MISSING: '**Goal:** <1 sentence restating the user's ask in plain language>' — placed at the bottom, after audits." >&2
        [ "$HAS_OUTCOME" = "0" ] && echo "  - MISSING: '**What changed:** <1-2 sentences in user-visible language>' — placed at the bottom, after audits." >&2
        [ "$HAS_PLAN_CHECK" = "0" ] && echo "  - MISSING: '✅ /plan-check: N/N fulfilled' — invoke the plan-check skill, fix any NOT DONE items, then add the line." >&2
        [ "$HAS_REVIEW" = "0" ] && echo "  - MISSING: '✅ /review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — apply /review standards (Correctness/Security/Performance/Maintainability/Style), fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. The 🔵 counter is required — '0 🔴 0 🟡' alone is incomplete (no skipping minor findings). Then add the line." >&2
        echo "See completion-report.md for the exact template." >&2
    fi

    # Check ORDER: Goal/What changed must appear AFTER audit lines (audits at top, Goal at bottom)
    GOAL_LINE=$(echo "$MSG" | grep -nE "\*\*Goal:?\*\*" | head -1 | cut -d: -f1)
    AUDIT_LINE=$(echo "$MSG" | grep -nE "✅.*(/plan.?check|review.*clean|review.*0 🔴)" | head -1 | cut -d: -f1)
    if [ -n "$GOAL_LINE" ] && [ -n "$AUDIT_LINE" ] && [ "$GOAL_LINE" -lt "$AUDIT_LINE" ]; then
        echo "VIOLATION: 'Goal' line appears BEFORE the audit lines. Wrong order. The terminal scrolls — the user only sees the LAST visible passage without scrolling back. Put audits/CI/plan-check/review at the TOP, then a '---' separator, then Goal + What changed + PR URL + ❓Question at the BOTTOM. See completion-report.md → 'Why this order'." >&2
    fi

    # Check trailing question is clearly marked with ❓
    LAST_CHAR=$(echo "$MSG" | tr -d '[:space:]' | tail -c 1)
    if [ "$LAST_CHAR" = "?" ] && ! echo "$MSG" | grep -qE "❓"; then
        echo "VIOLATION: Your message ends with '?' but no ❓ marker is present. Questions must be clearly marked so the user spots them in the terminal scroll — they can't tell a question from a status line at a glance. Use '❓ **Question:** <concise 1-2 sentence question>' as the very last line. If it isn't actually a question for the user, rephrase as a statement. See completion-report.md → 'Pending question'." >&2
    fi
fi

# Check completion report uses bare PR/issue numbers without titles.
# Wrong: 'PR #54 — mergeable, clean'  (em-dash status, no title between # and dash)
# Right: 'PR #54: Refactor driver.rs and add lyrics test'  (colon + title)
# Also detects: 'Fixes #234' / 'Closes #99' / 'Resolves #N' not followed by a parenthetical title.
if echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
    BARE_PR=0
    BARE_ISSUE=0
    echo "$MSG" | grep -qE "(PR|pull|Pull Request) #[0-9]+ *(—|--|-)" && BARE_PR=1
    # Use grep -P for negative lookahead — match "fixes #N" NOT followed by " (" (a parenthetical title)
    if echo "$MSG" | grep -qPi "(fixes|closes|resolves) #[0-9]+(?! *\()" 2>/dev/null; then
        BARE_ISSUE=1
    fi
    if [ "$BARE_PR" = "1" ] || [ "$BARE_ISSUE" = "1" ]; then
        echo "VIOLATION: Bare issue/PR number without a title. The user manages many projects in parallel and cannot decode #N references in their head. completion-report.md MANDATES titles on every reference:" >&2
        echo "  - WRONG: 'PR #54 — mergeable, clean' / 'Fixes #234'" >&2
        echo "  - RIGHT: 'PR #54: Refactor driver.rs and add lyrics error-path test' / 'Fixes #234 (driver.rs over 1000-line cap)'" >&2
        echo "Add the title — copy it from 'gh pr view' or 'gh issue view'. See completion-report.md → 'Issue / PR references'." >&2
    fi
fi

# Check for "skip 🔵 review findings" / "🔵 deferred / out of scope / minor" patterns.
# The user wants every review finding fixed inside the diff — no skipping minor issues.
if echo "$MSG" | grep -qiE "🔵.*(defer|skip|out of scope|not address|leave (it|them|for|to)|next (session|pr|commit)|not blocking|low.priority|nice.?to.?have|stylistic|cosmetic|address later|address next)|(defer|skip|leave|ignore).*🔵|out of scope.*(suggestion|🔵|stylistic|nit|nice.?to.?have|minor finding)|(suggestions?|minor findings?|🔵 findings?).*(defer|skip|out of scope|leave|next session|next pr|won.?t address|will not address|not addressing|can wait|low.priority|address later|address next)|(won.?t|will not|not) address(ing)?.*(suggestion|🔵|minor finding)"; then
    echo "VIOLATION: You're skipping or deferring 🔵 (suggestion) review findings. The user wants the highest-quality code possible — fix EVERY review finding inside this PR's diff, including 🔵. Phrases like '🔵 deferred', '🔵 out of scope', '🔵 minor — leaving them', '🔵 stylistic — skip', '🔵 nice-to-have — defer', or 'won't address the suggestions' are banned. The ONLY allowed exception is a 🔵 finding that points at code OUTSIDE the diff — for that, file a GitHub issue with a title and reference it. NEVER silently skip a 🔵 inside the diff. See completion-report.md → 'Pre-completion gate'." >&2
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
