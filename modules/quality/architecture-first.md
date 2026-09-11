### Architecture First

Before writing code, think about the design:

- **Follow existing patterns** in the codebase. Consistency is more important than cleverness.
- **No patchwork — area-reviewed at integration (#993).** Fix a wrong architecture; never stack workarounds on it. Every integrated change gets a main-session (Fable) review of the whole AREA it touches: verdict `OK` or `REWORK`; `REWORK` FILES an `architecture-rework` ticket — mechanical, not a question.
- **Critical self-review:** Be skeptical of your own conclusions. Before assuming something "doesn't work":
  1. Search documentation and GitHub issues for evidence
  2. Verify from multiple independent sources
  3. Never make assumptions about API behavior without documentation
  4. If debugging, confirm the actual cause before implementing workarounds
- **Study open source code:** When using libraries, read the actual source code to understand internal behavior. Do not rely solely on documentation.
- **No circular development:** Never cycle between approaches (try A, fail, try B, fail, try A again). If an approach should theoretically work, investigate WHY it does not instead of reverting.
- **Production-by-default (#414).** Code that runs UNATTENDED (timer / service / hook / cron), touches prod data or a managed box, or is a DEPENDENCY of another component IS production software from line 1 — structure, error paths, and tests from the first commit. "It's just a script / just an MVP" as an implicit quality classification is BANNED: MVP is a decision about SCOPE (fewer features), never about QUALITY (no structure / no framework / no tests) — see `mvp-philosophy.md`. The moment a "script" turns out to meet any criterion above, STOP and redesign the architecture — never "continue the original design" on a foundation that was never designed (the reported failure: 5 days spent debugging why a "simple" thing is broken, when a framework would have given it structure from day one).
- **Framework-first for new components (#414).** Before ANY new service / CLI / daemon / long-lived component, `investigate-existing-first` is MANDATORY with NAMED candidates (libraries, frameworks, machinery the repo already has), and the ticket's design comment MUST carry an `Architektúra:` section — structure/topology + the framework chosen, OR an evidenced why-none-fits from an actually-READ source, never a general impression.
