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

# Retry limiter: max 5 blocks per session to avoid runaway loops.
# Was 2; bumped to 5 because completion-report violations are deterministically
# fixable and agent needs more room to iterate before the hook gives up.
# State stored in /tmp under per-session counter file.
RETRY_FILE="/tmp/airuleset-stop-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=5

# Check for subagent vs inline prose question (HARD block — repeat offender pattern).
if echo "$MSG" | grep -qiE "subagent.?driven.*inline|two execution options|which (approach|execution)|subagent or (sequential|inline)|inline execution.*subagent|subagent.*inline execution|dispatch now or skim|dispatch now or hold|dispatch now or pause|dispatch.*subagents?.*or (hold|skim|pause|wait|review)"; then
    echo "VIOLATION: You asked 'subagent or inline' / 'two execution options' / 'dispatch now or skim' in prose at the end of your message. This is a pre-answered question — always use subagent-driven, dispatch immediately. The pre-ask-auto-answer hook blocks the structured AskUserQuestion form; writing the same question in prose is the same violation. Rewrite this message: cut the question entirely, and proceed with subagent-driven dispatch. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered prose question: subagent-vs-inline / two execution options / dispatch-now-or-skim"
fi

# Check for visual companion prose question
if echo "$MSG" | grep -qiE "want to try.*(visual|mockup|browser)|easier to explain.*browser|visual companion"; then
    echo "VIOLATION: You offered visual companion in prose. This is a pre-answered question — always yes. Next time, just use it without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Detect ASCII-art / box-drawing UI layout mockup paired with layout/position keywords.
# When agent draws UI layout in terminal text, visual companion MUST be used instead.
# The brainstorming skill's "use terminal for conceptual questions" escape DOES NOT apply
# to layout/position/component-placement questions — those are always visual.
HAS_BOXDRAW=$(echo "$MSG" | grep -qE "[┌┐└┘─│├┤┬┴┼╔╗╚╝═║█▓▒░▀▄■□]{3,}" && echo 1 || echo 0)
HAS_LAYOUT_KW=$(echo "$MSG" | grep -qiE "\b(header|footer|navbar|sidebar|toolbar|titlebar|status.?bar|menu.?bar|top border|bottom border|top.right|top.left|bottom.right|bottom.left|version label|version display|logo placement|page header|page footer|presenter (panel|view|placement)|top of (the )?(page|screen|window|view|border)|bottom of (the )?(page|screen|window|view|border)|above (the )?(header|footer|button|panel)|below (the )?(header|footer|button|panel)|position (of|the)|place (the )?[a-z]+ (on|in|at)|move (the )?[a-z]+ to|layout option|wizard step|dashboard layout|side.by.side layout|column layout|grid layout|component placement|fixed (top|bottom|header|footer)|sticky (top|header|footer))\b" && echo 1 || echo 0)
HAS_COMPANION_URL=$(echo "$MSG" | grep -qE "http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+|visual companion (live|running|started|at)|start-server\.sh" && echo 1 || echo 0)
if [ "$HAS_LAYOUT_KW" = "1" ] && [ "$HAS_BOXDRAW" = "1" ] && [ "$HAS_COMPANION_URL" = "0" ]; then
    echo "VIOLATION: You drew a UI layout in ASCII / box-drawing text-art for a LAYOUT/POSITION question. The user has explicitly stated terminal ASCII art is UNREADABLE for visual design decisions, causing repeated wrong iterations. Visual companion is MANDATORY for layout/position/UI-design questions — not optional." >&2
    echo "" >&2
    echo "  Start it NOW:" >&2
    echo "    bash ~/.claude/plugins/cache/claude-plugins-official/superpowers/*/skills/brainstorming/scripts/start-server.sh --project-dir <project-root>" >&2
    echo "  Then render mockups via the visual companion API and post the http://<ip>:<port> URL for the user." >&2
    echo "" >&2
    echo "  Banned: ASCII art layouts (┌─┐│└┘ etc.), text-mockup grids, '+--+' boxes for ANY layout/position question." >&2
    echo "  Allowed terminal output: prose descriptions, code snippets, data tables (without layout keywords)." >&2
    echo "" >&2
    echo "  The brainstorming skill's 'decide per question — terminal for conceptual, browser for visual' escape DOES NOT apply to layout/position/component-placement questions. Those are ALWAYS visual." >&2
    echo "  See ask-before-assuming.md pre-answered table (visual companion row)." >&2
    add_hard "ASCII-art / box-drawing UI layout mockup for layout question — start visual companion, render mockups in browser"
fi

# Check for tester-handoff prose (HARD block per autonomous-verification.md).
# The user is NEVER the agent's tester. Hand-off phrases shift verification from agent's
# tools (Playwright / curl / SSH / MCP) to the user's eyes/clicks — banned.
# Escape: if the message contains "UNVERIFIED:" explicitly stating WHAT cannot be tested
# and WHY (true user-only access), allow it — that is the documented exception.
if echo "$MSG" | grep -qiE "(can|could|would) you (please )?(test|verify|confirm|try|click|reproduce|reload|refresh)( it| this| that| the| in| on)|please (test|verify|confirm|reproduce|try it|try this|click it|click this|reload it|reload this|refresh it|refresh this)|let me know (if|when|whether)[^.]{0,80}(works|breaks|fails|shows|renders|appears|crashes|errors|loads|is correct|is right|you see)|(tell|show) me what you see|ping me (when|if|once|after)|report back (when|if|what|with|after)|\bnext user test\b|us(ed|ing) you as( a| the| my)? tester|act(ing|s)? as( a| the| my)? tester|(test|verify|try|run|click|check|reproduce|exercise) (it|this|the [a-z]+) on your end|on your end[,. ]+(test|verify|check|click|try|run|please|and let me know)|on your end and let me know|in your (browser|terminal|environment|local|machine)[, ]+(test|verify|check|click|try|run)|you'?ll need to (test|verify|click|try|reproduce)|\bbefore (the |any )?next user test\b|stop using you as tester|going to simulate.*myself|fix locally before (next |the )?(user )?test"; then
    # Allow if explicitly marked UNVERIFIED with a reason (the documented exception)
    if ! echo "$MSG" | grep -qE "UNVERIFIED:"; then
        echo "VIOLATION: You handed verification to the user ('please test', 'let me know if it works', 'ping me when', 'tell me what you see', 'on your end', 'next user test', 'using you as tester', etc.). The user is NEVER your tester. You have Playwright, curl, SSH, MCP tools, your own test harness — use them. A blocker (MCP auth failure, timeout, 500 error, opaque reference ID) is YOUR work to debug, not a hand-off trigger." >&2
        echo "" >&2
        echo "  Decision tree:" >&2
        echo "    1. Can you debug with existing tools? → DEBUG IT YOURSELF, do not mention the blocker to user" >&2
        echo "       (read full error, search root cause, build local repro, fix locally, verify locally)" >&2
        echo "    2. Do you LACK a tool/access/credential to verify? → ASK FOR THE TOOL, not the test:" >&2
        echo "       • 'Install Playwright MCP / Chrome DevTools MCP so I can drive the browser myself'" >&2
        echo "       • 'Restart MCP server <name> on host <X>, or share new host/port'" >&2
        echo "       • 'Share session cookie / bearer token / webhook URL so I can call <service> myself'" >&2
        echo "       • 'Open SSH tunnel / set <ENV_VAR> so I can reach <resource>'" >&2
        echo "       • 'Install BrowserStack/Sauce MCP for iOS Safari rendering'" >&2
        echo "       • 'Set up win-mcp on the Windows host so I can drive the desktop session'" >&2
        echo "       The user provides the TOOL — YOU run the test." >&2
        echo "    3. Is the blocker GENUINELY user-only (their personal account, their physical hardware)?" >&2
        echo "       → ONLY AFTER asking for tool and user confirms impossible:" >&2
        echo "         UNVERIFIED: <what cannot be tested> — <why no tool exists>" >&2
        echo "         (state user-only reason + that tool-request was attempted)" >&2
        echo "" >&2
        echo "  Banned shapes (still — even when blocker is real):" >&2
        echo "    • 'I can't reach X. Could you test it?' — WRONG. Ask for AUTH/MCP, not test." >&2
        echo "    • 'MCP is down. Want to verify manually?' — WRONG. Ask for MCP restart, not manual verify." >&2
        echo "    • 'Playwright not installed. Could you click through?' — WRONG. Ask for install." >&2
        echo "" >&2
        echo "  See autonomous-verification.md → 'Before giving up — ASK FOR THE TOOL, not the test'." >&2
        add_hard "Tester-handoff phrase (user-as-tester) — ask for the TOOL/ACCESS/MCP, not for the test. Or write 'UNVERIFIED:' after attempting tool-request."
    fi
fi

# Check for "say go / ready to proceed" prose questions
if echo "$MSG" | grep -qiE "say.?go|shall (i|we) proceed|if good.?say|ready when you are|ready for.?next|ready to execute"; then
    echo "VIOLATION: You asked the user to 'say go' or confirm proceed in prose. The plan is approved — chain directly to the next step without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for spec/plan/design review handoff prose, including
# "Does this design look right? If yes, I'll commit/write/spec ..."
# AND "dispatch via subagent now, or hold for your review of the plan"
if echo "$MSG" | grep -qiE "review the (spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before (i|we) hand.?off|before (handing|moving).?(off|on)|hand.?off to writing.?plans|any (changes?|edits?|tweaks?) before|(does|is) (this|the) (design|spec|plan|approach|architecture|interface|api|schema|model|structure|layout|flow) (look|seem|sound) (right|good|ok|fine|correct|reasonable)|if (yes|good|ok|approved),? .*(write|create|commit|push|save|file|spec|generate|hand.?off|proceed)|(approve|approved|sign.?off|sign off|green.?light) (this|the) (design|spec|plan|approach|architecture)|(dispatch|kick.?off|launch|start|begin|fire|trigger).*(subagent|implement|impl|task|work|run).*(now|immediately).*(or|vs).*(hold|wait|pause|review|stop|skim|check)|(hold|wait|pause).*(for|on).*(your|user) review|(go|proceed|now).*(or|vs).*review (first|the plan)|pre.implementation.*(pause|skim|review|check)|(skim|review).*(plan|spec).*before.*(dispatch|kick.?off|launch|implement)"; then
    echo "VIOLATION: You stopped to ask 'does this design look right?' / 'if yes I'll commit' / 'dispatch now or hold for review' / 'review the spec' / 'dispatch now or skim plan first'. These are all pre-answered — always proceed autonomously. The user approved the workflow when they invoked brainstorming/spec-writing. Rewrite this message: cut the question, commit / dispatch / chain to next step directly. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered prose question: spec/plan/design review handoff or pre-implementation pause"
fi

# === Unified completion-report detection ===
# Agents sometimes write prose completion reports without the canonical heading,
# silently bypassing every audit check below (slovnormal-mcp session shipped
# a report with NO heading → all audits skipped → user saw missing /requesting-code-review,
# missing 🌐, missing /plan-check). Fix: detect completion-report INTENT via signals
# even when the heading is absent, then force the agent to use the full template.
#
# Signal-based detection (any-of, combined with PR URL present):
#   - "awaiting merge" / "awaiting your merge" / "awaiting merge it"
#   - "mergeable, clean" / "mergeable=MERGEABLE" / "mergeStateStatus=CLEAN"
#   - "all N/N checks green" / "all checks green"
#   - "ready to merge"
#   - "Plan steps (N/N done)"
#   - "Work Complete" anywhere in message (catches lowercase / no heading prefix)
#   - Both **Goal:** AND **What changed:** present (clear completion-report markers in prose form)
IS_COMPLETION_HEADING=$(echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete" && echo 1 || echo 0)
HAS_PR_URL=$(echo "$MSG" | grep -qE "https?://github\.com/[^[:space:]]+/pull/[0-9]+" && echo 1 || echo 0)
HAS_COMPLETION_PHRASE=$(echo "$MSG" | grep -qiE "awaiting[^.]{0,40}(merge|your merge|merge it)|mergeable[, ]+clean|all [0-9]+/[0-9]+ checks (are )?(green|passing)|all checks (are )?green|mergeStateStatus=CLEAN|mergeable=MERGEABLE|ready to merge|Plan steps \([0-9]+/[0-9]+ done\)|✅ Work Complete|work complete[: ]|per pr-merge-policy|merged (to|into) (main|master)|auto-?merged" && echo 1 || echo 0)
HAS_GOAL_AND_OUTCOME=0
if echo "$MSG" | grep -qiE "\*\*Goal:?\*\*|^Goal:" && echo "$MSG" | grep -qiE "\*\*What changed:?\*\*|\*\*Outcome:?\*\*|^What changed:|^Outcome:"; then
    HAS_GOAL_AND_OUTCOME=1
fi

IS_COMPLETION_SIGNAL=0
if [ "$HAS_PR_URL" = "1" ] && { [ "$HAS_COMPLETION_PHRASE" = "1" ] || [ "$HAS_GOAL_AND_OUTCOME" = "1" ]; }; then
    IS_COMPLETION_SIGNAL=1
fi

# PR-LESS ticket completion (the david@gk blind spot, 2026-07-11): a fork-no-merge /
# hand-off stream NEVER produces a PR URL, so the PR-anchored route above never fired
# there and bare '✅ DONE: #1400 a #1408 hotové' one-liners sailed through — the user
# never saw a proper Work Complete report on that box. A ✅ DONE marker line that
# names ticket(s) #N together with done-vocab (SK/EN), or is paired with a
# READY-FOR-REVIEW hand-off, IS a ticket completion — same template obligations.
# (A conversational '✅ DONE: odpovedané na otázku o #123' has no done-vocab → clean.)
DONE_LINE=$(echo "$MSG" | grep -E "^✅ DONE:" | tail -1 || echo "")
if [ "$IS_COMPLETION_SIGNAL" = "0" ] && [ -n "$DONE_LINE" ]; then
    if printf '%s' "$DONE_LINE" | grep -qE "#[0-9]+" \
       && printf '%s' "$DONE_LINE" | grep -qiE "hotov|opraven|zavret|uzavret|vyrie[sš]en|dokon[cč]en|implementovan|nasaden|zmerg|zl[uú][cč]en|odovzdan|merged|deployed|fixed|closed|resolved|implemented|shipped|handed.?off"; then
        IS_COMPLETION_SIGNAL=1
    elif echo "$MSG" | grep -qiE "READY-FOR-REVIEW|odovzdan[eéáý]?[^.]{0,40}review|ready for (the )?(gatekeeper|maintainer) review"; then
        IS_COMPLETION_SIGNAL=1
    fi
fi

# A message that ends still-working (⏳ marker) is not a completion report — the signal
# route must not force the template mid-loop (e.g. fleet merged #N, dispatching the next).
if [ "$IS_COMPLETION_SIGNAL" = "1" ] && echo "$MSG" | grep -q "⏳"; then
    IS_COMPLETION_SIGNAL=0
fi

IS_COMPLETION=0
if [ "$IS_COMPLETION_HEADING" = "1" ] || [ "$IS_COMPLETION_SIGNAL" = "1" ]; then
    IS_COMPLETION=1
fi

# HARD: completion-report intent detected but canonical heading missing.
# This is the slovnormal-mcp failure mode — prose report bypassed all audits.
if [ "$IS_COMPLETION_SIGNAL" = "1" ] && [ "$IS_COMPLETION_HEADING" = "0" ]; then
    echo "VIOLATION: Your message is a completion report (PR URL + completion-signal phrase or Goal/What changed prose) but does NOT start with the canonical heading '## ✅ Work Complete'. completion-report.md MANDATES the FULL template every time — heading + audits block + --- separator + Goal + What changed + 🌐 URLs + PR title/URL. Prose substitutes ('PR clean. 8/8 checks green. mergeable=MERGEABLE...') are BANNED — they bypass the audit gates the user relies on (per slovnormal-mcp PR #9 incident: missing /requesting-code-review, missing 🌐 URLs, missing /plan-check all slipped through because no heading was present)." >&2
    echo "" >&2
    echo "  Rewrite the message NOW using the EXACT template:" >&2
    echo "" >&2
    echo "    ## ✅ Work Complete" >&2
    echo "" >&2
    echo "    **Audits & deploy:**" >&2
    echo "    ✅ CI: green" >&2
    echo "    ✅ /plan-check: N/N fulfilled" >&2
    echo "    ✅ /review: clean — 0 🔴 0 🟡 0 🔵" >&2
    echo "    ✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵" >&2
    echo "    ✅ Deploy: <verified behavior on live target>   (omit if no deploy)" >&2
    echo "    ✅ Regression test: <path>:<line> — RED <sha>, GREEN <sha>   (bug-fix only)" >&2
    echo "" >&2
    echo "    ---" >&2
    echo "" >&2
    echo "    **Goal:** <user's ask in plain language>" >&2
    echo "    **What changed:** <user-visible outcome, 1-2 sentences>" >&2
    echo "" >&2
    echo "    🌐 Dev:  <url>" >&2
    echo "    🌐 Prod: <url>" >&2
    echo "" >&2
    echo "    **[<project>] PR #N: <full title>**" >&2
    echo "    <full PR URL> — merged <sha> (default-auto) / mergeable, clean (manual-marker)" >&2
    echo "" >&2
    echo "  FORK-NO-MERGE / hand-off stream (no PR/merge/deploy exists): keep the heading +" >&2
    echo "  audits + Goal/What changed, and replace the Deploy/🌐/PR lines with the hand-off:" >&2
    echo "    ✅ Lokálne overenie: <tests+lint result on the fork branch>" >&2
    echo "    ✅ Hand-off: READY-FOR-REVIEW komentár na #N (<topic>) + --handoff karta" >&2
    echo "" >&2
    echo "  See completion-report.md → 'MANDATORY structure (use this EXACT template)'." >&2
    add_hard "Prose completion report missing canonical '## ✅ Work Complete' heading — use the full template, not a prose summary"
fi

# Check completion report has Goal + What changed + plan-check + /review lines
if [ "$IS_COMPLETION" = "1" ]; then
    HAS_GOAL=$(echo "$MSG" | grep -qiE "\*\*Goal:?\*\*|^Goal:" && echo 1 || echo 0)
    HAS_OUTCOME=$(echo "$MSG" | grep -qiE "\*\*What changed:?\*\*|\*\*Outcome:?\*\*|^What changed:|^Outcome:" && echo 1 || echo 0)
    HAS_PLAN_CHECK=$(echo "$MSG" | grep -qiE "/plan.?check|plan-check.*(fulfilled|passed|clean|complete)|✅.*plan.?check" && echo 1 || echo 0)
    # /review audit must include all THREE counters (🔴 🟡 🔵) — no skipping minor findings.
    # Accept either explicit "0 🔴 0 🟡 0 🔵" or "all findings addressed" with 🔵 mentioned.
    # MUST disambiguate from /requesting-code-review which contains "/review" as substring.
    # Use perl negative lookbehind: /review preceded by NOT "code-" (rules out requesting-code-review).
    HAS_REVIEW=$(echo "$MSG" | grep -qP '(?<!code-)/review[: ].*0 🔴.*0 🟡.*0 🔵|(?<!code-)/review[: ].*all (findings|issues|items).*addressed|(?<!code-)/review[: ].*addressed in commit' && echo 1 || echo 0)
    # requesting-code-review (superpowers skill, deep pass) — must also pass clean.
    # Distinguish from /review by requiring the literal token "requesting-code-review" or "request.*code.?review" or "superpowers:requesting".
    HAS_RCR=$(echo "$MSG" | grep -qiE "requesting.?code.?review.*0 🔴.*0 🟡.*0 🔵|requesting.?code.?review.*all (findings|issues|items).*addressed|requesting.?code.?review.*addressed in commit|✅.*requesting.?code.?review.*0 🔴.*0 🟡.*0 🔵|✅.*superpowers:requesting.*0 🔴.*0 🟡.*0 🔵|✅.*request.?code.?review.*0 🔴.*0 🟡.*0 🔵|✅.*code.?review.*\(deep\).*0 🔴.*0 🟡.*0 🔵" && echo 1 || echo 0)
    if [ "$HAS_GOAL" = "0" ] || [ "$HAS_OUTCOME" = "0" ] || [ "$HAS_PLAN_CHECK" = "0" ] || [ "$HAS_REVIEW" = "0" ] || [ "$HAS_RCR" = "0" ]; then
        echo "VIOLATION: Work Complete report is missing required lines. completion-report.md MANDATES this structure (audits at TOP, Goal/What changed/PR URL at BOTTOM — terminal scrolls, last lines are what the user sees):" >&2
        [ "$HAS_GOAL" = "0" ] && { echo "  - MISSING: '**Goal:** <1 sentence restating the user's ask in plain language>' — placed at the bottom, after audits." >&2; add_hard "Missing **Goal:** line"; }
        [ "$HAS_OUTCOME" = "0" ] && { echo "  - MISSING: '**What changed:** <1-2 sentences in user-visible language>' — placed at the bottom, after audits." >&2; add_hard "Missing **What changed:** line"; }
        [ "$HAS_PLAN_CHECK" = "0" ] && { echo "  - MISSING: '✅ /plan-check: N/N fulfilled' — invoke the plan-check skill, fix any NOT DONE items, then add the line." >&2; add_hard "Missing ✅ /plan-check audit line"; }
        [ "$HAS_REVIEW" = "0" ] && { echo "  - MISSING: '✅ /review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — apply /review standards (Correctness/Security/Performance/Maintainability/Style), fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. The 🔵 counter is required — '0 🔴 0 🟡' alone is incomplete (no skipping minor findings). Then add the line." >&2; add_hard "Missing ✅ /review audit line with 0 🔴 0 🟡 0 🔵"; }
        [ "$HAS_RCR" = "0" ] && { echo "  - MISSING: '✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — invoke the superpowers:requesting-code-review skill (the DEEP pass), fix every 🔴/🟡/🔵 it surfaces, then add the line. The user ALWAYS runs this after the completion report and it catches issues that /review misses — skipping = guaranteed rework. Both /review AND /requesting-code-review are required." >&2; add_hard "Missing ✅ /requesting-code-review audit line with 0 🔴 0 🟡 0 🔵"; }
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
    GOAL_LINE=$(echo "$MSG" | grep -nE "\*\*Goal:?\*\*" 2>/dev/null | head -1 | cut -d: -f1 || echo "")
    AUDIT_LINE=$(echo "$MSG" | grep -nE "✅.*(/plan.?check|review.*clean|review.*0 🔴)" 2>/dev/null | head -1 | cut -d: -f1 || echo "")
    if [ -n "$GOAL_LINE" ] && [ -n "$AUDIT_LINE" ] && [ "$GOAL_LINE" -lt "$AUDIT_LINE" ]; then
        echo "VIOLATION: 'Goal' line appears BEFORE the audit lines. Wrong order. The terminal scrolls — the user only sees the LAST visible passage without scrolling back. Put audits/CI/plan-check/review at the TOP, then a '---' separator, then Goal + What changed + PR URL + ❓Question at the BOTTOM. See completion-report.md → 'Why this order'." >&2
    fi

    # Check trailing question is clearly marked with ❓
    LAST_CHAR=$(echo "$MSG" | tr -d '[:space:]' | tail -c 1)
    if [ "$LAST_CHAR" = "?" ] && ! echo "$MSG" | grep -qE "❓"; then
        echo "VIOLATION: Your message ends with '?' but no ❓ marker is present. Questions must be clearly marked so the user spots them in the terminal scroll — they can't tell a question from a status line at a glance. Use '❓ **Question:** <concise 1-2 sentence question>' as the very last line. If it isn't actually a question for the user, rephrase as a statement. See completion-report.md → 'Pending question'." >&2
    fi
fi

# Check bare PR/issue numbers without titles — ALL messages (not just completion reports).
# Per issue-reference-context.md: the user manages many projects, does NOT keep tickets
# open, and cannot decode a bare '#N' by number. EVERY reference the user reads — status
# updates, milestone pings, mid-work narration, "filed as", "closes", plan steps,
# completion reports — must carry the title/topic next to the number.
# Soft warning (stderr, not a hard block): the rule does the enforcing; this catches slips
# without trapping the agent on edge cases (e.g. a 'Closes #N' inside a commit-message block,
# which is exempt git syntax).
# Right: 'PR #54: <title>' / '#42 (karaoke sanitizer)' / 'Closes #234 (driver.rs cap)'.
BARE_REF=0
# "issue|PR|pull request|pull #N" NOT immediately followed by ':' or ' (' (a title/topic).
if echo "$MSG" | grep -qPi "\b(issue|PR|pull request|pull) #[0-9]+(?! *[:(])(?![0-9])" 2>/dev/null; then BARE_REF=1; fi
# action-verb "#N" (closes/fixes/resolves/filed/tracked/see/blocked by/depends on/addressed in)
# NOT followed by ' (' (a parenthetical topic).
if echo "$MSG" | grep -qPi "\b(closes|fixes|resolves|fixed|filed as|filed:|tracked as|tracking|see|blocked by|depends on|address(ed)? in) #[0-9]+(?! *\()(?![0-9])" 2>/dev/null; then BARE_REF=1; fi
if [ "$BARE_REF" = "1" ]; then
    echo "VIOLATION (soft): Bare issue/PR number without its title/topic. The user does NOT keep tickets open and cannot decode '#N' by number — this applies to EVERY message, not just completion reports." >&2
    echo "  - WRONG: 'PR #54 — mergeable, clean' / 'Fixes #234' / 'Working on #42' / 'See #91'" >&2
    echo "  - RIGHT: 'PR #54: Refactor driver.rs and add lyrics test' / 'Fixes #234 (driver.rs over 1000-line cap)' / 'Working on #42 (karaoke sanitizer)' / 'See #91 (NDI rebind)'" >&2
    echo "Add the title/topic next to the number — copy it from 'gh issue view N' / 'gh pr view N'. Commit-message 'Closes #N' is exempt (git syntax). See issue-reference-context.md." >&2
fi

# Check for follow-up issue filings in completion reports.
# Per complete-planned-work.md "Follow-up gate", same-PR small cleanups (enum migration,
# type tightening, magic-number extraction, <100 LoC same-file polish) MUST land in the
# current PR — NOT in a follow-up issue. Follow-ups are reserved for genuinely
# out-of-scope work that fails the bundling gate (>300 LoC, schema change, API break,
# security boundary, cross-cut refactor).
if [ "$IS_COMPLETION" = "1" ]; then
    if echo "$MSG" | grep -qiE "follow.?up (filed|issue|tracked|created|opened|logged)[:= ]+#[0-9]+|filed (as|under) #[0-9]+ for (next|follow.?up|separate|dedicated)|tracked (in|as) #[0-9]+ (as|for) (separate|follow.?up|next|dedicated)|(will|to) address.*(in (a )?(next|follow.?up|dedicated|separate) pr|in (the )?next session)|(opened|created) #[0-9]+ (for|to track) (the )?(follow.?up|cleanup|tidy|polish|migration|refactor|migrate)"; then
        echo "VIOLATION: You filed a follow-up issue from a completion report. Per complete-planned-work.md 'Follow-up gate', same-PR small cleanups (<100 LoC, same-file polish, enum migration, type tightening, magic-number extraction, missing test on touched path) MUST land in the CURRENT PR — not a follow-up. Follow-ups are reserved for work that FAILS the bundling gate (>300 LoC, DB schema change, API break, security boundary, cross-cut refactor). If the discovered task does NOT meet one of those criteria, close the follow-up issue and add a commit to THIS PR. See complete-planned-work.md → 'Follow-up gate' and ask-before-assuming.md pre-answered table." >&2
    fi
fi

# Check for "ghost deferral" — completion report mentions deferred work but no #N issue reference.
# Per complete-planned-work.md, ANY deferral phrase in a completion report MUST cite a filed issue
# number. Without #N, the deferred work is permanently lost.
if [ "$IS_COMPLETION" = "1" ]; then
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
    if [ "$IS_COMPLETION_HEADING" = "0" ]; then
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
# Gate: only fire on COMPLETION REPORTS or messages with explicit `✅ Deploy:` line.
# Casual "deployed to dev1+dev2" mentions (admin chitchat) must NOT trigger this rule.
HAS_DEPLOY_LINE=$(echo "$MSG" | grep -qE "✅ Deploy:" && echo 1 || echo 0)
if { [ "$IS_COMPLETION" = "1" ] || [ "$HAS_DEPLOY_LINE" = "1" ]; } && echo "$MSG" | grep -qiE "✅ Deploy:|deploy.*(verified|complete|done|success|redeploy|auto.?redeploy)|verified.*deploy|deployed.*(to|successfully)"; then
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

# Check for a localhost/127.0.0.1/0.0.0.0 URL on a 🌐 line — issue #13 sub-item 3.
# Scoped ONLY to lines carrying the 🌐 marker (never the whole message) — that
# marker is used EXCLUSIVELY for "USER-CLICKABLE URL being presented right now"
# per completion-report.md, so this has near-zero FP risk: a code block or
# prose paragraph discussing "the dev server runs on localhost:5173" is never
# touched, only an actual 🌐-prefixed URL line. Not gated on IS_COMPLETION —
# a mid-work "here's the preview: 🌐 http://localhost:3000" is exactly the
# no-localhost-urls.md violation ("the user works remotely and cannot open
# localhost on their own machine"), completion report or not. HARD block:
# no-localhost-urls.md documents no legitimate exception for presenting one.
GLOBE_LOCALHOST=$(echo "$MSG" | grep -E "🌐" | grep -iE "localhost|127\.0\.0\.1|0\.0\.0\.0" || true)
if [ -n "$GLOBE_LOCALHOST" ]; then
    echo "VIOLATION: A 🌐 URL line points at localhost/127.0.0.1/0.0.0.0. The user works remotely and cannot open a localhost URL on their own machine. Use the machine's real LAN/tailscale IP instead (\`hostname -I\`), and verify it returns 200 before presenting it. See no-localhost-urls.md." >&2
    echo "  Offending line(s):" >&2
    echo "$GLOBE_LOCALHOST" | sed 's/^/    /' >&2
    add_hard "🌐 URL line points at localhost/127.0.0.1/0.0.0.0 — use the real LAN IP"
fi

# Final: if HARD violations found AND retry budget not exhausted, output JSON to block Stop.
# Per Claude Code hooks docs: {"decision":"block","reason":"..."} prevents Claude from stopping.
# Retry limit prevents loops if a violation is genuinely unfixable in this session.
if [ -n "$HARD_VIOLATIONS" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    REASON="Hard violations detected in your message:\n${HARD_VIOLATIONS}\nFix the message (rewrite or trim the offending content) and resend in this turn. See ask-before-assuming.md (pre-answered questions) and completion-report.md (report template) for details."
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

# Either no hard violations, or retry budget exhausted — let Stop succeed.
# Clear the counter on clean stop so next session starts fresh.
[ -z "$HARD_VIOLATIONS" ] && rm -f "$RETRY_FILE"
exit 0
