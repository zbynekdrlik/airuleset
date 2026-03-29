---
name: plan-check
description: Audit whether the plan and original prompt were fulfilled 100%. Use when work appears done but before sending the completion report.
user-invocable: true
---

# Plan Fulfillment Check

**Stop. Before answering, do a thorough honest audit.**

## Step 1: Re-read the original user prompt

Go back to the FIRST user message that started this work. What exactly did they ask for? List every requirement.

## Step 2: Re-read your plan

Find the plan you created (in the plan file, or in conversation history). List every numbered step.

## Step 3: Audit each item

For EACH requirement from the prompt AND each step from the plan, check:

- Was it implemented? Where? (file, commit, test)
- Was it tested? How? (unit test, E2E Playwright test, manual verification)
- Is CI green for it?
- Can you show evidence it works?

Use this format:

```
## Plan Fulfillment Audit

**Original prompt requirements:**
- [x] Requirement 1 — done (evidence)
- [x] Requirement 2 — done (evidence)
- [ ] Requirement 3 — NOT DONE: reason

**Plan steps:**
- [x] Step 1: description — done (evidence)
- [x] Step 2: description — done (evidence)
- [ ] Step 3: description — NOT DONE: reason

**Tests:**
- [x] Unit tests — file: tests/test_foo.rs
- [ ] Playwright E2E — NOT WRITTEN
- [x] CI green — all jobs pass

**Fulfillment: X/Y items complete (Z%)**
```

## Step 4: Be honest

If ANY item is `[ ]` NOT DONE — say so clearly. Do not rationalize, do not say "out of scope", do not say "for next session."

If fulfillment is not 100%, list exactly what remains and continue working on it. Do NOT send the completion report until everything is `[x]`.

## Rules

- This is a self-honesty check, not a formality
- If you find yourself writing "mostly done" or "the important parts are done" — you are not done
- The user invokes this because they suspect you skipped something. Prove them wrong with evidence, or admit what was skipped and fix it.
