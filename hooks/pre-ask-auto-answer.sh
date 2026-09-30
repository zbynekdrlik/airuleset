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
# "Does this design/spec/plan look right/good/ok?", "If yes, I'll commit/write/save",
# "dispatch tasks via subagent now, or hold for your review", "go vs review first"
if echo "$TOOL_INPUT" | grep -qiE "review.*the.*(spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before.*(i|we).*(hand.?off|move.?on|continue|proceed)|before.*(handing|moving).?(off|on)|hand.?off.*to.*writing.?plans|review.*before.*(implementation|implement|next)|(any|need) (changes?|edits?|tweaks?).*before|(does|is) (this|the) (design|spec|plan|approach|architecture|interface|api|schema|model|structure|layout|flow) (look|seem|sound) (right|good|ok|fine|correct|reasonable)|(does|is) (this|the).*(look|seem|sound) (right|good|ok|fine|correct|reasonable).*(specifically|specifically the)|if (yes|good|ok|approved),? .*(write|create|commit|push|save|file|spec|generate|hand.?off|proceed)|(approve|approved|sign.?off|sign off|green.?light) (this|the) (design|spec|plan|approach|architecture)|(dispatch|kick.?off|launch|start|begin|fire|trigger).*(subagent|implement|impl|task|work|run).*(now|immediately).*(or|vs).*(hold|wait|pause|review|stop|skim|check)|(hold|wait|pause).*(for|on).*(your|user) review|(go|proceed|now).*(or|vs).*review (first|the plan)|review.first.*(or|vs).*(go|proceed|dispatch)|pre.implementation.*(pause|skim|review|check)|(skim|review).*(plan|spec).*before.*(dispatch|kick.?off|launch|implement)"; then
    echo "BLOCKED: Spec/plan/design review handoffs are pre-answered — always proceed autonomously to the next step (writing-plans → executing-plans → commit → dispatch). 'Does this design look right? If yes, I'll commit' / 'dispatch via subagent now, or hold for review' / 'go vs review first' are all process pauses. The user already approved the workflow when they invoked brainstorming/spec-writing. Just commit / dispatch / move on. See ask-before-assuming.md pre-answered questions table." >&2
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

# Merge-permission questions on a green PR — pre-answered (pr-merge-policy.md default auto-merge).
# Default-auto project: merge yourself. Manual-marker project: end the message with
# '❓ NEEDS YOU: approve merge?' — never an AskUserQuestion either way.
# Context-anchored (it/this pr/now/to main...) so DESIGN questions about merging
# code/files/structs stay allowed.
if echo "$TOOL_INPUT" | grep -qiE "(should|shall|can|may) (i|we) merge (it\b|this pr|the pr|pr #?[0-9]+|now\b|dev\b|to main|into main)|want me to merge (it\b|this pr|the pr|pr #?[0-9]+|now\b|dev\b|to main|into main)|ok to merge|approve (the )?merge|ready to merge\?|merge (it |the pr )?now or (wait|hold|later)|merge.*or.*wait for your (approval|go|merge)"; then
    echo "BLOCKED: Merge-permission questions are pre-answered. Default-auto (pr-merge-policy.md): all gates green → merge + deploy + verify yourself, without asking. Manual-marker project (airuleset:merge=manual): do not ask via AskUserQuestion either — stop at the green PR and end the message with '❓ NEEDS YOU: approve merge?'. See ask-before-assuming.md." >&2
    exit 2
fi

# Commit author identity / email in a public repo's (old) history — pre-answered (#1196).
# Owner 30.9.2026: keep the old history, noreply from now on, never ask. PRECISION over
# recall: the hook greps the WHOLE tool_input JSON (question + options), so terms found
# anywhere proved useless (review rounds 1-4). The owner's identity must be LINKED to
# the commits INSIDE ONE CLAUSE (no . ? ! between; grep is per line). A missed phrasing
# only means the question gets asked; install already sets the identity. Blocks when:
#   GI_LINKED  — commit(s) … carry/authored/under/leaked/nesú … a personal/student/
#                university e-mail or name; or commit(s) … show/with/keep/majú/s … the
#                OWNER's own ("my …/môj/mojím …" e-mail/address/Gmail) or an AUTHOR
#                e-mail ("personal author e-mail", "osobný e-mail autora") — app data
#                ("previous commits show the student email") never counts;
#   or GI_AUTHID (author/committer e-mail|identity, e-mail/identita autora, git identity,
#                noreply, user.email) within one clause of "commit", plus a GI_CONTEXT
#                (old/past commits, a public repo, a rewrite/change, from now on/odteraz);
#   or GI_WHICH ("which e-mail/identity should … commit") plus from now on/odteraz;
#   and never GI_EXCLUDE (secrets, customer/client/GDPR/fixture data, bots, CI, signing,
#                external developers, his/her/their): "never rewrite" is wrong there.
# LC_ALL=C.UTF-8 so -i folds Slovak capitals (STARÉ, MÔJ) in any caller locale.
GI_ADJ='(personal|private|student|university|osobn[^ ]*|súkromn[^ ]*|študentsk[^ ]*|univerzitn[^ ]*)'
GI_POSS='(my|môj|moj[^ ]*)'
GI_OWN="$GI_POSS +($GI_ADJ +)?(e-?mail[^ ]*|gmail|address|adres[ua]|adresou|identit[^ ]*)|$GI_POSS +$GI_ADJ +(name|meno|menom)|\\bgmail|$GI_ADJ +(author|autora) +e-?mail|$GI_ADJ +e-?mail[^ ]* +autora"
GI_IDENT="$GI_OWN|$GI_ADJ +(e-?mail[^ ]*|address|adres[ua]|adresou|name|meno|menom)"
GI_LINK_AUTH='(carry|carries|carried|nesú|nesie|authored|under|pod|leak|leaks|leaked|expose|exposes|exposed)'
GI_LINK_SOFT='(show|shows|showing|majú|obsahujú|with|keep|keeps|ostať|ostanú|s)'
GI_LINKED='\bcommit[^.?!]{0,25} '"$GI_LINK_AUTH"' [^.?!]{0,30}('"$GI_IDENT"')|\bcommit[^.?!]{0,25} '"$GI_LINK_SOFT"' [^.?!]{0,30}('"$GI_OWN"')'
GI_AUTHID='\b(author|committer) +(e-?mail[^ ]*|identit[^ ]*)|\be-?mail[^ ]* +autora|\bidentit[^ ]* +autora|\bgit +identit[^ ]*|\bnoreply\b|user\.email'
GI_AUTHNEAR='\bcommit[^.?!]{0,60}('"$GI_AUTHID"')|('"$GI_AUTHID"')[^.?!]{0,60}\bcommit'
GI_CONTEXT='\b(old|older|past|previous|earlier|existing) +commits?\b|\bstar(é|ých|ych|e|ej|ú|ými) +commit|\bpublic +(git +)?(repo|github)|\bverejn[^ ]* +(repo|rep[ae]|repozit)|\brewrite\b|prepísať|prepisovať|\bchange\b|zmeniť|from now on|going forward|odteraz|force.?push'
GI_WHICH='\b(which|what|aký|akým|akú|ktorý|ktorým|ktorú) +(e-?mail|identit[^ ]*) +(should|shall|do|mám|by|will|must|to)\b[^.?!]{0,40}\bcommit'
GI_FUTURE='from now on|going forward|odteraz'
GI_EXCLUDE='secret|token|password|passwd|heslo|credential|api.?key|private key|kľúč|kluc|customer|client|zákazn|klient|gdpr|fixture|test data|testovac|\bbots?\b|\b(ci|cd)\b|github actions|pipeline|\bsign(ing|ed)?\b|gpg|external|extern|third.?party|contributor|another developer|iný vývojár|\b(his|her|their)\b'
gi_has() { LC_ALL=C.UTF-8 grep -qiE "$1" <<<"$TOOL_INPUT"; }
if ! gi_has "$GI_EXCLUDE" \
    && { gi_has "$GI_LINKED" \
         || { gi_has "$GI_AUTHNEAR" && gi_has "$GI_CONTEXT"; } \
         || { gi_has "$GI_WHICH" && gi_has "$GI_FUTURE"; }; }; then
    echo "BLOCKED: Commit author identity / e-mail in a public repo is pre-answered (#1196): keep the OLD history exactly as it is (never rewrite, never force-push) and commit under the owner's GitHub noreply identity from now on. airuleset install / onboard-project sets that local identity on every public checkout automatically. Do not ask. See ask-before-assuming-deep pre-answered table." >&2
    exit 2
fi

exit 0
