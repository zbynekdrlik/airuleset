#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Blocks on HARD violations (missing required completion-report fields)
# via {"decision":"block",...} JSON output, with retry limit (max 2 per session)
# to avoid runaway loops if a violation is genuinely unfixable.
# Warns on SOFT violations (banned phrases, prose questions) via stderr.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

# HARD violations collected here trigger {"decision":"block"} response.
# SOFT violations go to stderr as warnings. Both can fire in the same hook run.
HARD_VIOLATIONS=""
add_hard() { HARD_VIOLATIONS="${HARD_VIOLATIONS}- $1\n"; }

# Retry limiter: max 2 blocks per session to avoid loops.
# State stored in /tmp under per-session counter file.
RETRY_FILE="/tmp/airuleset-stop-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=2

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

# Check for spec/plan/design review handoff prose, including
# "Does this design look right? If yes, I'll commit/write/spec ..."
# AND "dispatch via subagent now, or hold for your review of the plan"
if echo "$MSG" | grep -qiE "review the (spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before (i|we) hand.?off|before (handing|moving).?(off|on)|hand.?off to writing.?plans|any (changes?|edits?|tweaks?) before|(does|is) (this|the) (design|spec|plan|approach|architecture|interface|api|schema|model|structure|layout|flow) (look|seem|sound) (right|good|ok|fine|correct|reasonable)|if (yes|good|ok|approved),? .*(write|create|commit|push|save|file|spec|generate|hand.?off|proceed)|(approve|approved|sign.?off|sign off|green.?light) (this|the) (design|spec|plan|approach|architecture)|(dispatch|kick.?off|launch|start|begin|fire|trigger).*(subagent|implement|impl|task|work|run).*(now|immediately).*(or|vs).*(hold|wait|pause|review|stop|skim|check)|(hold|wait|pause).*(for|on).*(your|user) review|(go|proceed|now).*(or|vs).*review (first|the plan)|pre.implementation.*(pause|skim|review|check)|(skim|review).*(plan|spec).*before.*(dispatch|kick.?off|launch|implement)"; then
    echo "VIOLATION: You stopped to ask 'does this design look right?' / 'if yes I'll commit' / 'dispatch now or hold for review' / 'review the spec'. These are all pre-answered — always proceed autonomously. The user approved the workflow when they invoked brainstorming/spec-writing. Next time, just commit / dispatch / chain to next step. See ask-before-assuming.md pre-answered table." >&2
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
        [ "$HAS_GOAL" = "0" ] && { echo "  - MISSING: '**Goal:** <1 sentence restating the user's ask in plain language>' — placed at the bottom, after audits." >&2; add_hard "Missing **Goal:** line"; }
        [ "$HAS_OUTCOME" = "0" ] && { echo "  - MISSING: '**What changed:** <1-2 sentences in user-visible language>' — placed at the bottom, after audits." >&2; add_hard "Missing **What changed:** line"; }
        [ "$HAS_PLAN_CHECK" = "0" ] && { echo "  - MISSING: '✅ /plan-check: N/N fulfilled' — invoke the plan-check skill, fix any NOT DONE items, then add the line." >&2; add_hard "Missing ✅ /plan-check audit line"; }
        [ "$HAS_REVIEW" = "0" ] && { echo "  - MISSING: '✅ /review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — apply /review standards (Correctness/Security/Performance/Maintainability/Style), fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. The 🔵 counter is required — '0 🔴 0 🟡' alone is incomplete (no skipping minor findings). Then add the line." >&2; add_hard "Missing ✅ /review audit line with 0 🔴 0 🟡 0 🔵"; }
        echo "See completion-report.md for the exact template." >&2
    fi

    # Bug-fix PRs MUST include a regression-test evidence line.
    # Triggered when the report mentions: Closes/Fixes/Resolves #N, or fix:/bug:/regression: in title,
    # or PR title starts with "fix" / contains "bugfix" / "hotfix" / "patch".
    IS_BUGFIX_REPORT=0
    if echo "$MSG" | grep -qiE '(closes|fixes|resolves)\s+#[0-9]+'; then IS_BUGFIX_REPORT=1; fi
    if echo "$MSG" | grep -qiE 'PR.*:.*\b(fix|bugfix|hotfix|patch|regression|repair)\b|^(fix|bugfix|hotfix|patch|regression):'; then IS_BUGFIX_REPORT=1; fi
    if echo "$MSG" | grep -qiE '\b(bug fix|bug-fix|regression fix|fixed (the )?(bug|regression|issue|defect))\b'; then IS_BUGFIX_REPORT=1; fi

    if [ "$IS_BUGFIX_REPORT" = "1" ]; then
        # Required line format examples:
        #   ✅ Regression test: tests/foo_test.rs:42 — RED on a1b2c3d, GREEN on e4f5g6h
        #   ✅ Regression test: e2e/login.spec.ts:15 — failed before fix (a1b2c3d), passes after fix (e4f5g6h)
        HAS_REGRESSION=$(echo "$MSG" | grep -qiE '✅\s*regression test:.*[a-f0-9]{7}' && echo 1 || echo 0)
        if [ "$HAS_REGRESSION" = "0" ]; then
            echo "VIOLATION: Bug-fix completion report missing the '✅ Regression test:' evidence line. Per regression-test-first.md, every bug fix needs a test commit BEFORE the fix commit, and the report must cite both SHAs:" >&2
            echo "  Required line:" >&2
            echo "    ✅ Regression test: <test_path>:<line> — RED on <test_sha>, GREEN on <fix_sha>" >&2
            echo "  Or:" >&2
            echo "    ✅ Regression test: <test_path>:<line> — failed before fix (<test_sha>), passes after fix (<fix_sha>)" >&2
            echo "  See regression-test-first.md and completion-report.md." >&2
            add_hard "Missing ✅ Regression test: <path>:<line> — RED <sha>, GREEN <sha> line on bug-fix PR"
        fi
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

# Check for follow-up issue filings in completion reports.
# Per complete-planned-work.md "Follow-up gate", same-PR small cleanups (enum migration,
# type tightening, magic-number extraction, <100 LoC same-file polish) MUST land in the
# current PR — NOT in a follow-up issue. Follow-ups are reserved for genuinely
# out-of-scope work that fails the bundling gate (>300 LoC, schema change, API break,
# security boundary, cross-cut refactor).
if echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
    if echo "$MSG" | grep -qiE "follow.?up (filed|issue|tracked|created|opened|logged)[:= ]+#[0-9]+|filed (as|under) #[0-9]+ for (next|follow.?up|separate|dedicated)|tracked (in|as) #[0-9]+ (as|for) (separate|follow.?up|next|dedicated)|(will|to) address.*(in (a )?(next|follow.?up|dedicated|separate) pr|in (the )?next session)|(opened|created) #[0-9]+ (for|to track) (the )?(follow.?up|cleanup|tidy|polish|migration|refactor|migrate)"; then
        echo "VIOLATION: You filed a follow-up issue from a completion report. Per complete-planned-work.md 'Follow-up gate', same-PR small cleanups (<100 LoC, same-file polish, enum migration, type tightening, magic-number extraction, missing test on touched path) MUST land in the CURRENT PR — not a follow-up. Follow-ups are reserved for work that FAILS the bundling gate (>300 LoC, DB schema change, API break, security boundary, cross-cut refactor). If the discovered task does NOT meet one of those criteria, close the follow-up issue and add a commit to THIS PR. See complete-planned-work.md → 'Follow-up gate' and ask-before-assuming.md pre-answered table." >&2
    fi
fi

# Check for "ghost deferral" — completion report mentions deferred work but no #N issue reference.
# Per complete-planned-work.md, ANY deferral phrase in a completion report MUST cite a filed issue
# number. Without #N, the deferred work is permanently lost.
if echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
    # Detect deferral phrases (broad — many rewordings)
    DEFER_HIT=0
    if echo "$MSG" | grep -qiE "\b(is |has been |will be |to be )?deferred\b|\bdefer(ring|ral)\b|root.?cause (fix|repair) (is )?(later|deferred|for later|not yet|in follow.?up|next pr|next session)|(actual|real) (fix|root.?cause) (is )?(later|deferred|coming|in follow.?up|for follow.?up|next pr|next session|not yet)|(will|to) be addressed (in (the )?(next pr|next session|follow.?up|dedicated pr|future))|(remains|still) (outstanding|unresolved|pending|to.?be.?done|to.?fix)|this pr (does ?n'?t|doesn'?t|will not|won.?t) (fix|address|resolve|close|complete) (the |that )?(root.?cause|actual|underlying|real)|(not yet|won.?t be) fix(ed|ing) (in|until) (this pr|next session|follow.?up)|patch(ed)? around|workaround for now|temporary (fix|patch|band.?aid)|placeholder until|stub until|leave[sd]? broken|stays broken|known (issue|broken)|moves? to a (next|future|separate|dedicated) pr|punt(ed|ing)? (to|until|for)"; then
        DEFER_HIT=1
    fi
    if [ "$DEFER_HIT" = "1" ]; then
        # Require an EXPLICIT tracking-issue reference. A bare PR title with #N does NOT count
        # (PR #195 in the title doesn't prove the deferred work was filed). Require:
        #   "Filed as #N" / "Filed: #N" / "Tracked as #N" / "Tracking issue #N" /
        #   "Issue #N" / "Tracker: #N" / "TODO #N" / "filed under #N" / etc.
        if ! echo "$MSG" | grep -qiE "\b(filed|tracked|tracking|tracker|opened|created|logged|recorded)\b[^.]{0,60}#[0-9]+|issue\s+#[0-9]+\b|todo[: ]+#[0-9]+|see\s+#[0-9]+|follow.?up\s+(in|at|as)\s+#[0-9]+|deferred[^.]{0,60}#[0-9]+|root.?cause[^.]{0,60}#[0-9]+|address(ed)?\s+(in|by|via)\s+#[0-9]+"; then
            echo "VIOLATION: Completion report contains a deferral phrase ('deferred', 'root-cause fix later', 'will be addressed in follow-up', 'remains outstanding', 'workaround for now', 'patched around', 'this PR doesn't fix...', 'punted to...') but NO EXPLICIT tracking-issue reference. The current PR's own #N in the title does NOT count — the user needs proof the DEFERRED work was filed as its own tracked issue." >&2
            echo "" >&2
            echo "  Per complete-planned-work.md, any deferred work MUST be filed as a tracked GitHub issue BEFORE sending the completion report, and the report MUST cite it explicitly:" >&2
            echo "    • 'Filed as #<N>: <title>'" >&2
            echo "    • 'Tracked as #<N>'" >&2
            echo "    • 'Root-cause fix tracked in #<N>'" >&2
            echo "    • 'Address in #<N>'" >&2
            echo "" >&2
            echo "  Without that, the deferred work is permanently lost (the ghost-deferral failure mode)." >&2
            echo "" >&2
            echo "  Fix NOW:" >&2
            echo "    1. gh issue create --title 'TODO: <description of deferred work>' --body '<context>'" >&2
            echo "    2. Add a line to the completion report: 'Filed as #<returned-N>: <title>'" >&2
            echo "" >&2
            echo "  See complete-planned-work.md → 'CRITICAL — deferral phrases MUST cite the issue number'." >&2
            add_hard "Deferral phrase in completion report without explicit 'Filed as #N' / 'Tracked as #N' reference"
        fi
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

# Detect "STOP at green PR URL" / "Awaiting your merge it" / "Phase N remains gated" prose
# These are template-bypass shorthands. If they appear, the message must use the full template.
if echo "$MSG" | grep -qiE "STOP at (the )?green pr|stop at green pr url|stop at green-pr"; then
    if ! echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete"; then
        echo "VIOLATION: 'STOP at green PR URL' is template-bypass prose. Any 'we're done, PR is ready, awaiting merge' message MUST use the full Completion Report template (## ✅ Work Complete with audits, Goal, What changed, 🌐 URLs, PR title/URL). Replace the prose with the template. See completion-report.md → 'Full template every time'." >&2
    fi
fi

# Detect "Phase N remains gated on Phase M merge" / "Phase N is gated on" / "next phase awaits"
# This is a "Remaining/Future" mention disguised as plan continuity — banned per complete-planned-work.md.
if echo "$MSG" | grep -qiE "phase [0-9]+ (remains|is) gated on|phase [0-9]+ awaits|phase [0-9]+ blocked on .*(merge|phase)|next phase (awaits|gated|blocked)|gated on phase [0-9]+ merge"; then
    echo "VIOLATION: 'Phase N remains gated on Phase M' is a 'Remaining / Future' mention disguised as plan continuity. complete-planned-work.md and completion-report.md ban these in the report. The next phase is the next session's prompt — do NOT explain gating here. Cut the line. See completion-report.md → 'Banned shortcuts'." >&2
fi

# Check for PR completion message missing the PR URL
# Signal: completion language about a PR but no https://github.com/.../pull/N URL anywhere in message
if echo "$MSG" | grep -qiE "awaiting (your|merge)|pr (is )?(ready|mergeable)|mergeable[, ]+(clean|all)|all checks (are )?green|ready to merge|per pr-merge-policy|awaiting.*\"merge it\""; then
    if ! echo "$MSG" | grep -qE "https?://github\.com/[^[:space:]]+/pull/[0-9]+"; then
        echo "VIOLATION: You announced PR completion ('mergeable clean', 'awaiting merge', 'all checks green', etc.) without providing the PR URL. completion-report.md and pr-merge-policy.md MANDATE the PR URL on the completion line: '✅ PR: <https://github.com/.../pull/N> — mergeable, clean'. Always paste the full URL — the user works remotely and cannot click 'PR #11'. Use the EXACT completion-report.md template, not a prose summary." >&2
    fi
fi

# Check deploy verification has 🌐 URL lines for USER-CLICKABLE web URLs.
# Multi-environment (dev+prod / dev+staging / prod+staging) ⇒ require ≥2 🌐 lines.
# Single UI deploy ⇒ require ≥1 🌐 line.
# 🌐 lines list USER-clickable URLs (frontend / dashboard / admin) — NEVER backend/API URLs.
# Backend URLs are agent verification evidence, not human clickables.
if echo "$MSG" | grep -qiE "✅ Deploy:|deploy.*(verified|complete|done|success|redeploy|auto.?redeploy)|verified.*deploy|deployed.*(to|successfully)"; then
    GLOBE_COUNT=$(echo "$MSG" | grep -cE "🌐.*https?://" || true)
    [ -z "$GLOBE_COUNT" ] && GLOBE_COUNT=0

    # Anti-pattern: 🌐 line listing a backend/API URL — clutters the user's clickable list.
    GLOBE_HAS_BACKEND=$(echo "$MSG" | grep -qiE "🌐.*(backend|/api/|api[: ]|:8000|:8080|:5000|api endpoint|api server)" && echo 1 || echo 0)

    HAS_DEV=$(echo "$MSG" | grep -qiE "\bdev\b|\bdevelopment\b" && echo 1 || echo 0)
    HAS_PROD=$(echo "$MSG" | grep -qiE "\bprod\b|\bproduction\b" && echo 1 || echo 0)
    HAS_STAGING=$(echo "$MSG" | grep -qiE "\bstaging\b|\bstage\b" && echo 1 || echo 0)
    HAS_UI=$(echo "$MSG" | grep -qiE "frontend|dashboard|\bui\b|web app|browser|admin panel" && echo 1 || echo 0)

    MULTI_ENV=0
    [ "$HAS_DEV" = "1" ] && [ "$HAS_PROD" = "1" ] && MULTI_ENV=1
    [ "$HAS_DEV" = "1" ] && [ "$HAS_STAGING" = "1" ] && MULTI_ENV=1
    [ "$HAS_PROD" = "1" ] && [ "$HAS_STAGING" = "1" ] && MULTI_ENV=1

    if [ "$GLOBE_HAS_BACKEND" = "1" ]; then
        echo "VIOLATION: A 🌐 URL line lists a backend/API URL (matched ':8000' / ':8080' / '/api/' / 'backend:' / 'api:'). The user reads the 🌐 list to click in a browser — backend URLs are noise there. Backend evidence belongs in '✅ Deploy:' (e.g. 'dev backend serves v1.0.97-dev.9 via /api/version'), NOT in the 🌐 list. Remove backend/API entries from 🌐. See completion-report.md → 'Dashboards & URLs'." >&2
    fi

    if [ "$MULTI_ENV" = "1" ] && [ "$GLOBE_COUNT" -lt 2 ]; then
        echo "VIOLATION: Deploy mentions multiple environments (dev/staging/prod) but the report has only $GLOBE_COUNT clickable 🌐 URL line(s). List every USER-CLICKABLE web URL on its own '🌐 <env>: <url>' line — typically one per environment. Read the project's CLAUDE.md '## Dashboards' / '## URLs' section. Do NOT list backend/API URLs — only user-facing browser URLs. URLs in prose ('curl http://...') do NOT count. See completion-report.md → 'Dashboards & URLs'." >&2
        add_hard "Multi-env deploy with <2 🌐 URL lines"
    elif [ "$HAS_UI" = "1" ] && [ "$GLOBE_COUNT" -lt 1 ]; then
        echo "VIOLATION: Deploy mentions a UI/frontend/dashboard but the report has no clickable 🌐 URL line. The user cannot click URLs buried in prose. Add at least one '🌐 <env>: <url>' line for the user-facing dashboard (NOT backend/API). See completion-report.md → 'Dashboards & URLs', and no-localhost-urls.md." >&2
        add_hard "UI deploy with no 🌐 URL line"
    fi
fi

# Final: if HARD violations found AND retry budget not exhausted, output JSON to block Stop.
# Per Claude Code hooks docs: {"decision":"block","reason":"..."} prevents Claude from stopping.
# Retry limit prevents loops if a violation is genuinely unfixable in this session.
if [ -n "$HARD_VIOLATIONS" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    REASON="Completion report has hard violations:\n${HARD_VIOLATIONS}\nFix the report and resend in this turn. See completion-report.md for the exact template."
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

# Either no hard violations, or retry budget exhausted — let Stop succeed.
# Clear the counter on clean stop so next session starts fresh.
[ -z "$HARD_VIOLATIONS" ] && rm -f "$RETRY_FILE"
exit 0
