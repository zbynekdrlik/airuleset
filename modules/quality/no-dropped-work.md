### No Dropped Work — Everything You Identify Is Either Done Now or Filed as an Issue

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — deferral phrases in completion reports MUST cite `#N`; this rule extends that to EVERY message, every phase
- `completion-report.md` — 🔵 review findings inside the diff = MUST FIX; out-of-diff = file an issue
- `autonomous-batch-issue-development.md` — bundling/follow-up gate decides same-PR vs new issue
- `tdd-workflow.md` / `regression-test-first.md` — a discovered bug is a missing-test report; file it AND test it

**The single rule: any piece of work you IDENTIFY but do NOT complete in this session MUST be captured as a tracked GitHub issue BEFORE you stop. Work is never silently dropped. The only three fates for an identified task are: (1) do it now, (2) `gh issue create` and cite the returned `#N`, (3) it was already a tracked issue you can point at. There is no fourth fate.**

This rule exists because the same work keeps getting lost the same three ways, forcing the user to re-explain things they already asked for. Each is a violation:

#### Failure mode 1 — Decomposition-shedding (the most common loss)

The user gives a high-level prompt. You split it into sub-parts. You implement SOME parts and silently forget the rest — they were never tracked, so they evaporate, and the user has to prompt them again.

**Mandatory mechanism:** when you decompose a request, enumerate ALL sub-parts UP FRONT (TodoWrite, or an explicit numbered list). Before you stop, walk that list. EVERY sub-part you did not complete this session → `gh issue create` with the context from the original prompt, then list `Filed as #N: <title>`. Never end a turn having done part of a request with the remainder living only in your head.

- "I focused on the auth piece; the rate-limiting and the audit-log parts can come later." — **WRONG** unless each later part has a filed `#N`.
- "Implemented 2 of the 4 things you asked for." — **WRONG** unless the other 2 are filed `#N`.
- Quietly shipping a subset and not mentioning the dropped parts at all — **WRONG.** That's the silent loss the user is most angry about.

#### Failure mode 2 — Review findings acknowledged but neither fixed nor filed

A review (`/review`, `/requesting-code-review`, or any ad-hoc inspection) surfaces a problem. You note it, decide not to fix it right now, and move on without filing it. It's gone.

**Rule:** every review finding is fixed in this PR (preferred — see `completion-report.md`'s 🔵 = MUST FIX) OR filed as a `#N` issue with a title. A finding you "decided to leave" with no `#N` is a dropped finding. This applies to findings you surface yourself mid-work, not just to formal review-skill output.

#### Failure mode 3 — "Pre-existing / known / unrelated" dismissal during testing

During a normal run a test fails, a warning appears, or something looks broken. You label it "pre-existing", "a known issue", "unrelated to my change", or "out of scope" — and neither fix it nor file it. Forever unsolved, rediscovered every session.

**Rule:** a problem you NOTICE for the first time is a problem you just DISCOVERED — "pre-existing" describes its age, not its tracking status. The moment you call something pre-existing / known / unrelated / out-of-scope, you MUST either fix it now or `gh issue create` describing what you observed (the failing test name, the warning text, where it happens, why it matters) and cite `#N`. "Pre-existing" without a filed `#N` next to it is banned.

- "That test was already failing before my change, so I'll skip it." — **WRONG.** File it (`#N`), then skip with the reference.
- "This is a known issue in the upstream lib, nothing we can do here." — **WRONG.** File it documenting the limitation and the upstream link.
- "The console warning is unrelated to this PR." — **WRONG.** Unrelated to the PR ≠ unrelated to the project. File it.

#### The mechanism (every fate-2 case)

```bash
gh issue create --title "<concise problem statement>" \
  --body "<what you observed (exact error/test name/warning), where it happens, why it matters, original-request context if it's a shed sub-part>"
```

Then surface it: `Filed as #N: <title>`. File BEFORE you stop — not "I'll file it later" (that's a fourth fate, and it doesn't exist). The bundling/follow-up gate (`autonomous-batch-issue-development.md`, `complete-planned-work.md`) decides whether a small cleanup lands in THIS PR instead of a new issue — but the choice is do-now-in-PR vs file-issue, NEVER drop.

#### Prepared ≠ filed — RUN `gh issue create`, never ask permission to

Filing a GitHub issue is **non-destructive tracking**. It changes no code, touches no production, costs nothing, and is trivially editable or closeable afterward. Therefore it **NEVER requires the user's approval** — and "should I create the issues?" is a pre-answered YES (see `ask-before-assuming.md`).

The failure: you decompose a request, DRAFT the issue list in chat (titles, scopes, "the 7 new issues + 4 rescopes"), and then STOP to ask permission to create them — "give the word and I'll create them", "ready to file the backlog?", "or tell me to hold". **A drafted issue that was never `gh issue create`'d is a dropped issue.** The user then has to say "yes create them", which is exactly the re-prompting this rule exists to eliminate.

- When you identify a backlog at the INITIAL high-level prompt, FILE it as part of doing the work — immediately, in that same turn. Do not present a list and wait to be told.
- When the user points out missing issues, CREATE them — do not re-draft them and ask again.
- If a specific issue's DESIGN is genuinely ambiguous (e.g. "reset to 0dB or last preset?"), still FILE the issue now (so it's tracked), then note the open design question ON that issue or in the same turn. Filing is never blocked on a design answer.

Banned (the Stop hook blocks these): "give the word and I'll create the issues", "ready to create the backlog?", "should I file these issues or hold?", "want me to open the issues?", "once you confirm I'll file them" — any wording that asks permission to file issues instead of filing them. The only allowed message about a backlog is one where the issues are ALREADY created (`gh issue create` ran → cite `Filed as #N: <title>` for each).

#### Banned phrases (intent — all rewordings and semantic equivalents apply)

Any of these, when NOT accompanied by a filed `#N` (or an in-session fix), is a dropped-work violation:

- "pre-existing" / "preexisting" / "already failing/broken before my change"
- "known issue" / "known bug" / "known limitation" / "known failure"
- "unrelated to this change/PR/fix" / "not related to my work"
- "out of scope" / "outside the scope" (used to avoid acting, with nothing filed)
- "the remaining parts / the rest / the other items can wait / will follow / later"
- "handled only part of" / "did some of what you asked"
- "I'll file it later" / "should be tracked" / "worth an issue someday" (intent without the actual `gh issue create`)
- "separate issue" / "different problem" said as a reason to skip, with no `#N`

The intent is banned: surfacing work and then letting it evaporate. The Stop hook (`stop-check-untracked-work.sh`) blocks these phrases when no `#N` / `gh issue create` / in-session fix is present, on EVERY message — not just completion reports.

#### The principle

The user runs long autonomous sessions and cannot babysit what you silently drop. A dropped sub-part, a dropped review finding, and a dropped "pre-existing" failure are the same bug: identified work with no tracking. Track everything you touch. Filing an issue costs 10 seconds; re-explaining a lost request costs the user a whole session. When in doubt, file the issue.
