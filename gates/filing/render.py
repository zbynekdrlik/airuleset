"""gates.filing.render -- the block-message text + summary formatting for the
ungated-issue-filing gate (#1020 Part 2).

WORKER_MSG and BLOCK_MSG are VERBATIM the two `cat >&2 <<'MSG' ... MSG` walls the
bash hook printed (byte-for-byte, including the em-dashes and the exact
accepted-criteria list). `render_block` reproduces the hook's exact stderr layout
on a classifier block: the `🚫 BLOCKED — per-item reason:` header, one
`  - "<title>" -> <reason>` line per blocked item, a trailing blank line (the
printf's own `\n` after the summary), then BLOCK_MSG.
"""

SUMMARY_HEADER = "\U0001f6ab BLOCKED \u2014 per-item reason:"

WORKER_MSG = 'BLOCKED: you are a worktree WORKER (subagent) — you may NOT `gh issue create`\n(nor a `gh api …/issues` POST, nor a `gh api graphql … createIssue`) a new\nGitHub issue. A worker FIXES what it finds in-lane, in THIS branch — a small\nadjacent problem, a flaky test, a review finding all land here.\n\nA genuinely separate deliverable you found (a different request, a task for\nanother project, a defect outside this change) goes in your RETURN block as a\n`followup_candidates:` line (title + why it is separate + est. LoC). The\nSUPERVISOR files it as its OWN ticket (#1297), never the worker (#842). A return\ncontaining a `filed:` line is REJECTED at integration and the lane is sent back.\n'

BLOCK_MSG = 'A `Scope-gate:` line must be EXACTLY `Scope-gate: <criterion>` where <criterion>\nis ONE of: separate-work | >300-loc | schema-migration | api-break |\nsecurity-boundary | cross-cutting | needs-user-decision | user-request.\n`separate-work` (#1297) = a separate deliverable: a different request, a task\nanother project hands over, or a defect outside the change in hand. A step of\nthe SAME deliverable is no filing reason (`planned-work` is retired): it is a\n`- [ ]` line in that ticket\'s description. A PROSE self-audit line (e.g.\n`Scope-gate: >300 LoC? nie; …`) is NOT the gate line; put the single accepted\ncriterion on its own line (a proper gate line elsewhere in the body wins).\n\nBlocked because: (a) no valid `Scope-gate:` line, (b) this issue\'s own PARENT\n(the "#N follow-up" it names) is ITSELF a review-finding follow-up — a depth-2\nchain (#311: follow-ups of follow-ups, unbounded), (c) the body claims\n`Scope-gate: >300-loc` but states a line count of 300 or fewer (#311),\n(d) no `Dedup-checked: <what you searched>` line (#329), (e) an existing OPEN\nissue (or an earlier sibling in THIS batch) has a near-duplicate title and this\none does not reference it by `#N` (#329), (h) this repo is stream-aware and\nthis filing (from a sub-dev stream account) carries no `stream:<x>` label\n(#390), (i) it carries a `stream:<x>` label of a DIFFERENT stream with no\n`Stream-routing: <reason>` line (#390), or (i2) it HAS a `Stream-routing:`\nline but lacks `-l needs-gatekeeper` (#962).\n\nUNATTENDED sessions only (#842): (j) `presence-required` — `user-request` is\naccepted only while the OWNER is PRESENT (or the body quotes an owner message\nwith `verbatim` and today\'s/yesterday\'s date — #962). (k) `dismissal-word` —\nthe body dismisses a test failure (flaky / pre-existing / intermittent / out of\nscope); FIX the test/root cause, do not file its excuse.\n\nThere is NO daily cap, no per-parent cap and no net-drain brake (#1297): every\nseparate deliverable gets its OWN ticket. Link it both ways — the new body says\n`Part of #<parent>` (`Requested by owner/repo#N` across repos) and the parent\'s\ndescription gets a `- [ ] #<new>` line.\n\nFix NOW — one of:\n  1. A small fix inside the change you are making: just fix it, OR\n  2. Add a `Dedup-checked: <what you searched for>` line if it is missing, OR\n  3. Reference the existing near-duplicate issue by `#N` instead of filing\n     a new one, OR\n  4. Add the accepted criterion on its own line:\n       Scope-gate: <criterion>\n     where <criterion> is one of:\n       separate-work | >300-loc | schema-migration | api-break |\n       security-boundary | cross-cutting | needs-user-decision | user-request\n     OR\n  5. Add an explicit `-l stream:<your-own-stream>` label matching YOUR OWN\n     stream (#390) -- or, when this ticket genuinely belongs to a\n     DIFFERENT stream (or is a core/shared-infra ticket), keep that\n     stream\'s label, add a `Stream-routing: <reason>` line to the body,\n     AND add `-l needs-gatekeeper` to auto-route it to the gatekeeper\n     for triage (#962).\n\nThis is a LOGGED, falsifiable claim (~/.claude/scope-gate.log) — it does not\nverify the criterion is true, only that you affirmatively claimed one instead\nof silently filing. See modules/quality/no-dropped-work.md. Genuine bypass:\nappend `# airuleset:scope-gate-ok <reason>` to the command.\n'

def render_block(results):
    """Full stderr text for a classifier block, byte-identical to the bash
    hook: summary header + one line per BLOCK result + a trailing blank line,
    then the BLOCK_MSG wall. `results` is the list of
    (verdict, title, reason, parents_str, target_repo, dedup) tuples."""
    parts = [SUMMARY_HEADER]
    for verdict, title, reason, _parents, _repo, _dedup in results:
        if verdict == "BLOCK":
            parts.append('  - "%s" -> %s' % (title, reason or "none"))
    summary_part = "\n".join(parts) + "\n\n"
    return summary_part + BLOCK_MSG
