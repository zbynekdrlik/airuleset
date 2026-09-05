# Completion Report — History & Rationale (#859)

This file contains the evolution history and revision mechanics moved
VERBATIM from `modules/core/completion-report.md` during the context-diet
conversion (#859 batch 1). The template, hard rules, gates, and enforcement
stay always-on in the module.

---

## Compact-at-boundary mechanics

**#822 (2026-09-01), REVISED by #855 (2026-09-02) — under an ARMED `/goal` the drained-boundary `/compact` needs a BOUNDARY-HOLD turn, not a bare `⏳`; and the compact is TYPED at the next IDLE poll, never queued behind a running turn.** The goal Stop hook blocks every `✅` boundary, so a `/compact` typed into a running turn would only QUEUE — and #855 proved CC's type-ahead queue drain is NOT idempotent for a slash command (one queued `/compact` → two submits, the owner's double-compact). So the watchdog now types `/compact` ONLY into a genuinely idle pane; a running turn is refused (`skip:turn-running`). When `compact-request --self` prints its boundary-hold command (because the boundary compact could not run yet — `skip:turn-running`/`already-queued`), launch it as a tracked background Bash task — `sleep 45 && echo boundary-hold` via `run_in_background: true` — and end the turn `⏳ WORKING: boundary hold`. The accepted Stop leaves the pane idle for the whole 45 s sleep, so the next ~60 s watchdog sweep finds it idle and the `/compact` is typed into that idle prompt where it executes immediately, exactly once (`compact-request --status` then goes `PENDING` → `NONE`). Full mechanism + the live-verify-and-escalate caveat: `skills/autopilot/SKILL.md` Step 5. A served, non-`/goal` session (no armed goal) needs none of this — a bare `--self` at its report boundary is enough.

## Compact-request backstop history

**#411 (2026-08-13): a SECOND, MECHANICAL backstop now exists too — a genuine SAFETY NET, never a reason to skip calling `--self` yourself.** `hooks/stop-check-prose-violations.sh` (the SAME Stop hook that already validates the report's own structure) fires the identical `compact-request --record --session <sid> --cwd <cwd> --origin self-callback` call itself, best-effort and silent, the moment a turn's `## ✅ Work Complete` report clears every hard-violation check — closing the gap #400 reopened (a smaller/sonnet-tier stream session that reliably skips the prose-taught `--self` call now still gets recorded, PROVIDED the report itself is well-formed enough to pass the gate). This is strictly a backstop: it fires LATER than your own proactive call (only after the WHOLE Stop-hook evaluation runs, not the instant your turn ends) and a malformed report that gets BLOCKED never reaches it at all — so `--self` FIRST is still the primary mechanism, not an optional courtesy.

## Passive fallback retirement

**#400 (2026-08-12): there is no passive fallback any more if this call is skipped or fails.** `notify-compact-request.sh` (the Stop hook this used to describe as a fallback) is now a PERMANENT NO-OP — its text-sniffing `## ✅ Work Complete`/`✅ DONE:` channel is removed entirely, because repeated re-firing on every ordinary turn is what let a stale compact request keep looking "fresh" for 11+ hours in a live incident. Make the `--self` call proactively, don't rely on anything else to catch it — Claude Code's own native auto-compact still handles a genuinely full context, but the deliberate per-report boundary is only guaranteed by calling this yourself.

## Compact-at-boundary two NEVERs

**Two more NEVERs (2026-08-06 decision, `#228`):**
- **NEVER right after just answering a question.** The user replying to your `❓ NEEDS YOU` is a RESUMPTION point, not a completion boundary — that reply is what lets you continue the work you paused for, and compacting right then would discard the very question/answer context you just needed. Call `compact-request --self` only once the RESULTING work is itself reported done with its own genuine `## ✅ Work Complete` heading — never immediately after the answer arrives.
- **NEVER mid-work, and never when the only record of what you did is this conversation.** A clean report (no `❓`/`⏳`) proves the TURN is finished, not that the WORK is durable. If the plan, decision, or result a compaction would discard lives ONLY in this chat — not yet a commit, a merged PR, a ticket comment, or a file on disk — it is not a safe boundary; write it down first (`durable-decisions-to-tickets.md`), then compact.
