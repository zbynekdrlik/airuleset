### Salvage Before Discarding Expensive Work — Harvest, Then Stop; Never Kill-and-Throw-Away

**Context gate — related rules you MUST also apply:**
- `no-dropped-work.md` — the IDENTIFIED-work analog; this is its IN-FLIGHT-COMPUTE form (don't discard tokens already spent)
- `claude-code-tooling.md` — Workflow resume (`Workflow({scriptPath, resumeFromRunId})`) reuses the cached prefix; a stopped Workflow is RESUMABLE, not lost — and the right-size-the-fan-out rule stops the over-scope BEFORE it runs
- `main-context-hygiene.md` — the partial outputs already live in the transcript / `agent-<id>.jsonl` journal; READ them, don't re-run

**When you stop expensive in-flight work — a Workflow, a long subagent, a big run, a fleet you over-scoped — you MUST HARVEST what it already produced BEFORE you kill it, and you NEVER kill-and-discard.** Tokens already spent are unrecoverable; the partial work sitting in the transcript / journal is FREE to read. Killing a run that already burned (e.g.) 5 MB of tokens and taking ZERO from it is the WORST outcome — strictly worse than letting it finish AND worse than salvaging — because you pay the full cost and keep nothing. The user's exact words for this failure: *"este horsie!!!"* — the waste isn't that the work was over-scoped, it's that you PAID for it and kept NOTHING.

When you realize in-flight work is wasteful / over-scoped / wrong:

1. **Stop adding NEW cost** — yes: `TaskStop` the workflow, stop dispatching more agents. That part is correct.
2. **READ the partial output FIRST.** The completed agents' results are in the transcript and in `agent-<id>.jsonl`. Extract every finding / digest / decision they already produced — that is the salvage.
3. **PREFER RESUME over RESTART.** A stopped Workflow resumes from its cached prefix (`resumeFromRunId`); only edited / new stages re-run. Re-deriving inline from scratch what the run already computed pays for it TWICE.
4. **Only then** continue, using the salvaged results.

**Don't reflexively kill when called out — THINK first.** Being told "this is burning tokens" is a signal to (a) stop new cost, (b) salvage the spent cost, (c) right-size the next step — NOT to panic-discard the whole run. A reflexive kill that throws away the spent work is the second failure stacked on the first.

#### Banned (intent — all rewordings and semantic equivalents)

- "Stopping it — my mistake" followed by killing the run and re-doing the work inline from scratch → **WRONG.** Harvest the partial output first.
- "I threw away the over-scoped workflow" with nothing harvested → **WRONG.** Those tokens are now pure loss.
- Discarding a multi-MB run and then re-reading the same files / re-deriving the same findings yourself → **WRONG.** Read the journal; resume the prefix.

The intent is banned: paying for expensive compute and then keeping none of it. Applies to all rewordings and semantic equivalents.
