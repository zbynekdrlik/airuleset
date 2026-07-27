### Subagent Type Discipline — Never Invent Agent Names → on-demand skill `subagent-type-discipline`

The full policy moved VERBATIM to the `subagent-type-discipline` skill — load it before dispatching any `Agent`/`Task` subagent. Non-negotiable that survives here: the `Agent` tool's `subagent_type` MUST be one of the types listed in the Agent tool's OWN description in your prompt — NEVER invent a plugin-prefixed name (`caveman:cavecrew-builder`, `superpowers:implementer`); when in doubt use `general-purpose`. Applies to all rewordings and semantic equivalents.

Second non-negotiable: dispatch with the **LEAST tool authority** the task needs — a review / audit / verify task gets read-only `Explore` (no Edit/Write, enforced by the harness), never write-capable `general-purpose`, because a prompt saying "only report" constrains nothing (#49). After ANY subagent run, `git status` before `git add -A` — it may have left scratch files.
