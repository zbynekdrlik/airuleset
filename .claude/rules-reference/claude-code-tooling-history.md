# Claude Code Tooling — History & Rationale (#859)

This file contains the policy evolution history and owner directives moved
VERBATIM from `modules/core/claude-code-tooling.md` during the context-diet
conversion (#859 batch 1). The effort tiers, workflow guidance, /goal rules,
and anti-patterns stay always-on in the module.

---

## Ultracode launch-flag policy evolution

Set with `/model` in CLI. **ultracode** mode = `xhigh` + permission to launch multi-agent workflows (not a separate API tier) — as of the owner directive 2026-08-30 it is NO LONGER a managed launch flag: managed sessions launch WITHOUT ultracode and at effort `high`, so ultracode is once again a per-session opt-in the user turns on by hand (see the Dynamic Workflows section below).

**Ultracode is NO LONGER a managed launch flag (owner directive 2026-08-30, verbatim):** *"Chcel by som este aby sa claude v targetoch nespustali s zapnutym ultracode ale s effort high"*. This reverses the LAUNCH-FLAG half of #445 (2026-08-13): managed launches no longer carry `--settings '{"ultracode":true}'` and the effort baseline drops `xhigh` → `high` (`airuleset.py` `MANAGED_EFFORT_LEVEL = "high"`; the launcher's `plain` mode is still the vanilla no-managed-flags escape hatch, and `claude-ultracode` is retained as a muscle-memory alias that now behaves like the default mode). **Only the launch flags reversed — the max-acceleration / parallel-worktree DOCTRINE is UNCHANGED:** the same 2026-08-13 direction still stands (*"kazdy claude spravne pouzival multiple git worktreee a mergovanie spolu a maximalizoval vyuzitie subagentov... vzdy sa islo maximalnou akceleraciou a ak to dana uloha dovoli sa pracovalo paralelne"*) — worktree fleet dispatch and subagent maximization for fan-out work remain the default approach (below). What changed is only that, WITHOUT a session ultracode flag, launching the full multi-agent **Workflow tool** follows its standard opt-in — the user invokes it in their own words — rather than being an always-standing grant; a user who wants ultracode for a session enables it by hand.

## Right-sizing fan-out — the real incident

**Right-size the fan-out, and GROUND ONCE — the dominant token sink is REDUNDANCY, not depth.** A real incident: a review Workflow fanned 6 agents that EACH re-read the same three ~1500-line CI files (≈4,500 lines × 6) plus the full design, then spawned a fresh verifier PER finding that re-received the whole design again — ~5 MB of tokens, all on Opus, for a design the user had already hand-converged. Three rules prevent it:
- **Ground ONCE, pass a digest — never N agents each re-reading the same big files.** When every fan-out agent needs the SAME large source (the CI YAMLs, the design doc, the log bundle), read it ONE time — a single cheap grounding stage (`opts.model: 'claude-opus-4-6'` at low where the surface names it; `'sonnet'` only for genuinely trivial collection — or one inline read) that returns a TIGHT digest — and pass that digest in each agent's prompt. N agents × the same 4,500-line re-read is N× the input cost for one body of context, and is the single biggest waste in practice.
- **Size the fan-out to RESIDUAL UNCERTAINTY, not to thoroughness-by-reflex.** A 6-reviewer panel + per-finding adversarial verify is for high-stakes UNKNOWNS (a security audit, an unproven design). Work the user has already vetted / hand-converged needs ONE focused pass, not a fleet. **Ultracode buys DEPTH, never REDUNDANCY** — "cost is not the constraint" does NOT license N agents re-deriving the same thing; that is waste, not rigor.
- **Per-item fan-out MULTIPLIES — bound it and don't re-ground per item.** A verify/refine step that re-sends the entire design (or the whole file) to a fresh agent PER finding is O(findings × context). Batch the findings into ONE verify call, or pass only the finding + its local slice — never the whole body again per item.
