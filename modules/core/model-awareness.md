### Model Awareness (2026)

**Fable 5.1 (`claude-fable-5-1`) @ effort `medium` is the fleet Fable tier (owner directive 2026-09-05, #894, revises #871). Fable 5.0 (`claude-fable-5`) is retired from the lineup (not banned like Opus 5 — just no longer in `MODEL_TIERS`).**

**The fleet lineup is an ALLOWLIST of EXACT ids (`airuleset.MODEL_TIERS`), never a set of aliases:**

| Tier | Exact id | Role |
|---|---|---|
| Fable | `claude-fable-5-1` (main launch `claude-fable-5-1[1m]`) @ effort `medium` | main default; the DESIGN + REVIEW phases (gated) |
| Opus | `claude-opus-4-6` @ effort `high`/`xhigh` | implementation ESCALATION tier; gate-CLOSED fallback |
| Sonnet | `claude-sonnet-5` | settled-design implementation default; mechanical/read-only |
| Haiku | `claude-haiku-4-5` | most-trivial reads |

**A dispatch NEVER carries a `model` param — the pinned AGENT TYPE is the model choice.** Enforced: `hooks/block-unpinned-model-dispatch.sh` rejects ANY `model` param on the `Agent` tool and any non-allowlisted `opts.model` value on a `Workflow` script. **Opus 5** (`claude-opus-5` — including the bare `opus` alias) **is BANNED everywhere**. Fable 5.0 (`claude-fable-5`) is retired — only Fable 5.1 (`claude-fable-5-1`, @ `medium`). The main model is the user's call — NEVER recommend switching it. Rules must work for Fable AND the Opus 4.6/Sonnet/Haiku subagents.

The full per-phase tiering policy (design + review = gated Fable; implementation = Sonnet 5 default / Opus 4.6 on complexity; FLEET-WIDE), the JUDGMENT-CONTENT phase selector, the DESIGN-HEAVY taxonomy, the IMPLEMENTATION tier escalation criteria, the ADVISOR digest shape, the Fable/Opus behavior notes, and the subagent behavior + rule-writing guidance are in the situational companion `skills/model-awareness-deep/DEEP.md` — loaded automatically on Agent/Workflow/fable-gate actions. History + rationale: `.claude/rules-reference/model-awareness-history.md` (#859).
