---
name: rules-audit
description: "Merged into /mdreview v2 (#858) — invoke /mdreview instead. Structural baseline (size, dupes, orphans, contradictions) is now Step 1 of mdreview."
user-invocable: false
disable-model-invocation: true
---

# rules-audit → merged into /mdreview (#858)

This skill's content was merged VERBATIM into `/mdreview` v2 as its scripted Step 1.
Invoke `/mdreview` instead — it runs the structural baseline automatically from the
`mdreview-audit` JSON artifact, then adds the live/ecosystem delta.

See `skills/mdreview/SKILL.md` for the full v2 skill.
