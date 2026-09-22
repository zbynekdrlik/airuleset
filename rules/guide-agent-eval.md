---
paths:
  - "**/docs/**/navody*"
  - "**/docs/**/navody/**"
  - "**/docs/**/build-*-guide.py"
  - "**/docs/**/navody_*_sections.py"
  - "**/docs/**/*-qa.json"
---

### A guide section is an agent-eval fixture (#1077)

**Owner ruling 18.9.2026 (montalu1): „takéto veci by sa mali okrem do návodov
overovať, či ich vie správne AI pomocník riešiť a prípadne aj vykonať podľa
pokynov".** A client guide (Návody) is NOT finished when the HTML reads well —
it is finished when the tenant's AI assistant answers the same questions
correctly, and (for executable steps) performs them, on a COPY with the client
persona, BEFORE the client sees the guide. The guide text and the assistant must
never silently disagree.

**Every new or changed guide section, for a tenant that HAS an AI assistant,
carries a machine-readable fixture entry** in the project's
`docs/<tenant>/navody/<sekcia>-qa.json` (the odoo-erp schema: `guide`, `section`,
`entries[{id, question, expected_answer, anchor}]`, plus optional `instruction`
+ `expected_effect` for what the agent must EXECUTE on the copy). `question` +
`expected_answer` paraphrase the section — same content, machine-checkable.

**Before the hand-off, run the fixture against the assistant on a COPY** with the
client persona, using the PROJECT's own runner
`services/agent/scripts/eval_navody.py` (copy-target fail-closed, persona
enrollment, effect verify+revert — already built in odoo-erp, #7597/#7599).
airuleset defines and reviews this RULE; it never rebuilds the runner.

**The RFR (READY-FOR-REVIEW) then carries an `AI-eval:` line:**

```
AI-eval: docs/<tenant>/navody/<sekcia>-qa.json → <rubric tally> (docs/ai-agent/eval-navody-<sekcia>-<dátum>.md)
```

— the fixture path and the report path must both exist in the tree. **A wrong
answer is NOT ignored:** it is either a GUIDE fix (the section text was unclear
/ wrong) OR an agent prompt/tool TICKET (the assistant needs a better prompt or
a new tool) — a tracked fix, never a silent mismatch between the guide and the
assistant. The hand-off composer pre-flight (`gates.agenteval` via
`airuleset.py handoff`) refuses a guide-SOURCE RFR that carries no `AI-eval:`
line.

**A tenant with NO agent, or whose agent-on-copy is blocked, writes**

```
AI-eval: n/a — <prečo> (napr. montalu nemá agenta, blokované #<ticket>)
```

— the `n/a` form names the blocker ticket. A per-tenant fact
`agent_eval: NONE — <prečo>` in `.claude/streams/<stream>.md` (the same file that
holds `navody_url:`) records that the tenant has no agent: it makes a bare
`AI-eval: n/a` sufficient, but the `n/a` line is STILL mandatory — a guide-source
RFR always states something about the assistant.

**Shared-benefit:** every guide tenant (montalu, slovnormal, miva) gets the same
"answer + execution checked on a copy before the client sees the guide" bar with
no per-stream runner to write.
