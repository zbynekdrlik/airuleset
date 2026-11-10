### No Dropped Work — Every Separate Deliverable Gets Its Own Ticket

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — deferral phrases in completion reports MUST cite `#N`; this rule extends that to EVERY message, every phase
- `completion-report.md` — 🔵 review findings inside the diff = MUST FIX; out-of-diff = its own ticket (a separate defect)
- `autonomous-batch-issue-development.md` — bundling/follow-up gate decides same-PR vs new issue
- `tdd-workflow.md` / `regression-test-first.md` — a discovered bug is a missing-test report; record it AND test it
- `durable-decisions-to-tickets.md` — DECISIONS and FINDINGS get the same treatment as work items, and the deadline is THE MOMENT they land (compaction eats anything that lives only in the conversation)

**The single rule (#1297, owner 10.11.2026): any piece of work you IDENTIFY but do NOT complete in this session MUST be captured BEFORE you stop. The three fates: (1) do it now (the default); (2) a step of the SAME deliverable → a `- [ ]` line in that ticket's DESCRIPTION; (3) a separate deliverable → its OWN ticket, linked both ways. There is no fourth fate.**

**What is a separate deliverable** (owner, 10.11.2026: „nasilu sa pcha do existujucich ticketov aj veci ktore mali ist o novych a vysleodk je taky ze sa straca praca, claude si ju nevsimne"): a different request; a task another project hands over; a defect or finding outside the change in hand. The #1259 squeeze ("one request = one ticket", a 2/day cap, a net-drain brake) pushed these into existing tickets as comments and the footer `I` — which counts tickets, not the work inside them — lost them (dcontroller: three dstack tasks posted as comments, untouched for a day; dasio: 16 ignored items behind a "done").

**Linkage both ways** (owner addendum 10.11.: separate issues must not lose the connection between the pieces of work):
- the child body says `Part of #<parent>` (`Part of owner/repo#N` across repos);
- the parent DESCRIPTION gets a `- [ ] #<child>` line (`- [ ] owner/repo#M`) — GitHub renders its state; tick it `- [x]` once the child is closed.
- A task FOR ANOTHER PROJECT = `gh issue create -R <receiving repo>` whose body says `Requested by owner/repo#N`, plus a peer tell to that project's session; the requesting ticket lists `- [ ] owner/repo#M`. NEVER a comment on the receiving project's existing ticket.

**The description is the single task list.** A comment that adds work to the SAME deliverable also adds its `- [ ]` line to the body (`gh issue edit <N> --body-file`). A ticket closes only when every `- [ ]` line — child links included — is `- [x]` with its evidence: `hooks/block-close-with-open-checklist.sh` refuses the close otherwise (a `--reason "not planned"` close is exempt).

**Default fate = FIX NOW.** A worktree WORKER never files (#842): it fixes in-lane and returns `followup_candidates:`; the SUPERVISOR files each separate one as its own linked ticket. The filing gate keeps the anti-spam parts — `Dedup-checked:` + near-duplicate, no plan-step tickets (`planned-work` is retired), no follow-up-of-a-follow-up review chains — and has no daily cap, no per-parent cap and no net-drain brake.

This rule exists because the same work keeps getting lost the same three ways, forcing the user to re-explain things they already asked for. Each is a violation:

#### Failure mode 1 — Decomposition-shedding (the most common loss)

The user gives a high-level prompt. You split it into sub-parts. You implement SOME parts and silently forget the rest — they were never tracked, so they evaporate, and the user has to prompt them again.

**Mandatory mechanism:** when you decompose a request, enumerate ALL sub-parts UP FRONT (TodoWrite, or an explicit numbered list). Before you stop, walk that list. EVERY sub-part you did not complete this session → a `- [ ]` line in the ticket's description (a step of the same deliverable — never a ticket per plan step), then say `Recorded on #N: <sub-part>`. Never end a turn having done part of a request with the remainder living only in your head.

- "I focused on the auth piece; the rate-limiting and the audit-log parts can come later." — **WRONG** unless each later part is a `- [ ]` item on the ticket.
- "Implemented 2 of the 4 things you asked for." — **WRONG** unless the other 2 are `- [ ]` items on the ticket.
- Quietly shipping a subset and not mentioning the dropped parts at all — **WRONG.** That's the silent loss the user is most angry about.

#### Failure mode 2 — Review findings acknowledged but neither fixed nor recorded

A review (`/review`, `/requesting-code-review`, or any ad-hoc inspection) surfaces a problem. You note it, decide not to fix it right now, and move on without recording it. It's gone.

**Rule:** every review finding is fixed in this PR (preferred — see `completion-report.md`'s 🔵 = MUST FIX) OR, when it is outside the change in hand, filed as its OWN ticket linked to `#N` (`Part of #N` + a `- [ ] #<new>` line on `#N`). A finding you "decided to leave" with no `#N` is a dropped finding. This applies to findings you surface yourself mid-work, not just to formal review-skill output.

#### Failure mode 3 — "Pre-existing / known / unrelated" dismissal during testing

During a normal run a test fails, a warning appears, or something looks broken. You label it "pre-existing", "a known issue", "unrelated to my change", or "out of scope" — and neither fix it nor file it. Forever unsolved, rediscovered every session.

**Rule:** a problem you NOTICE for the first time is a problem you just DISCOVERED — "pre-existing" describes its age, not its tracking status. The moment you call something pre-existing / known / unrelated / out-of-scope, you MUST either fix it now or file it as its own ticket (what you observed: the failing test name, the warning text, where it happens, why it matters) and cite `#N`. "Pre-existing" without that `#N` next to it is banned.

- "That test was already failing before my change, so I'll skip it." — **WRONG.** Record it on `#N`, then skip with the reference.
- "This is a known issue in the upstream lib, nothing we can do here." — **WRONG.** Record the limitation and the upstream link on `#N`.
- "The console warning is unrelated to this PR." — **WRONG.** Unrelated to the PR ≠ unrelated to the project. Record it.

#### The mechanism

```bash
gh issue edit <N> --body-file <body + a new "- [ ] <step>" line>    # fate 2: a step of the same deliverable
gh issue create -R <repo> -F <body with "Part of #N" + Scope-gate: separate-work>   # fate 3
gh issue edit <N> --body-file <body + a new "- [ ] #<new>" line>   # fate 3: link it back
```

Then surface it: `Recorded on #N: <step>` / `Filed as #M: <title>`. Record BEFORE you stop — not "I'll write it down later" (a fourth fate does not exist). **Before the ticket closes, every `- [ ]` is `- [x]` with its evidence — closing a ticket with open boxes drops them (hook-enforced).**

**Dedup by CODE AREA before filing** (#860, montalu3 incident): before filing a feature/gap ticket, ALSO grep the relevant codebase area (models, functions, the mechanism) for existing implementations — not only issue-title search. Ticket titles name symptoms differently; the code is the ground truth. A title-only dedup misses already-merged work under a different name.

**Filing itself is now mechanically gated on naming WHY it's out of scope** (`hooks/block-ungated-issue-filing.sh`, #137 — the enforcement asymmetry this rule used to have with `complete-planned-work.md`'s Follow-up gate): the `--body` must carry a `Scope-gate: <criterion>` line (`separate-work`/`>300-loc`/`schema-migration`/`api-break`/`security-boundary`/`cross-cutting`/`needs-user-decision`/`user-request`). This does not weaken the recording rule above — a separate deliverable always has a truthful criterion to name in one line — it only stops the SILENT default of filing over fixing. **A reduced-authority stream filing a core/shared-infra ticket** (a foreign `stream:core` or `stream:<other>` label) must also carry `Stream-routing: <reason>` in the body AND `-l needs-gatekeeper` in the command to auto-route to the gatekeeper (#962); an unattended `user-request` filing is presence-exempt when the body quotes an owner message with `verbatim` and today's/yesterday's date.

#### Prepared ≠ recorded — record or file it, never ask permission to

Recording on the ticket (or filing a separate deliverable) is **non-destructive tracking**. It changes no code, touches no production, costs nothing, and is trivially editable or closeable afterward. Therefore it **NEVER requires the user's approval** — "should I create the issues?" is answered by recording / filing them NOW (see `ask-before-assuming.md`).

The failure: you decompose a request, DRAFT the issue list in chat (titles, scopes, "the 7 new issues + 4 rescopes"), and then STOP to ask permission to create them — "give the word and I'll create them", "ready to file the backlog?", "or tell me to hold". **A drafted issue that was never `gh issue create`'d is a dropped issue.** The user then has to say "yes create them", which is exactly the re-prompting this rule exists to eliminate.

- When one deliverable has several steps, write them as a `- [ ]` checklist in its ticket's description — immediately, in that same turn (never one issue per step). Separate deliverables each get their own linked ticket. Do not present a list and wait to be told.
- When the user points out missing items, RECORD / FILE them — do not re-draft them and ask again.
- If a specific issue's DESIGN is genuinely ambiguous (e.g. "reset to 0dB or last preset?"), still RECORD it now (so it's tracked), then note the open design question ON that ticket or in the same turn. Recording is never blocked on a design answer.

Banned (the Stop hook blocks these): "give the word and I'll create the issues", "ready to create the backlog?", "should I file these issues or hold?", "want me to open the issues?", "once you confirm I'll file them" — any wording that asks permission to file issues instead of filing them. The only allowed message about a backlog is one where it is ALREADY recorded: `Recorded on #N` for steps of one deliverable, `Filed as #N: <title>` for each separate one.

#### Banned phrases (intent — all rewordings and semantic equivalents apply)

Any of these, when NOT accompanied by a filed `#N` (or an in-session fix), is a dropped-work violation:

- "pre-existing" / "preexisting" / "already failing/broken before my change"
- "known issue" / "known bug" / "known limitation" / "known failure"
- "unrelated to this change/PR/fix" / "not related to my work"
- "out of scope" / "outside the scope" (used to avoid acting, with nothing filed)
- "the remaining parts / the rest / the other items can wait / will follow / later"
- "handled only part of" / "did some of what you asked"
- "I'll file it later" / "should be tracked" / "worth an issue someday" (intent without actually recording it on `#N`)
- "separate issue" / "different problem" said as a reason to skip, with no `#N`

The intent is banned: surfacing work and then letting it evaporate. The Stop hook (`stop-check-untracked-work.sh`) blocks these phrases when no `#N` / `gh issue create` / in-session fix is present, on EVERY message — not just completion reports.

#### The principle

The user runs long autonomous sessions and cannot babysit what you silently drop. A dropped sub-part, a dropped review finding, and a dropped "pre-existing" failure are the same bug: identified work with no tracking. Track everything you touch. Recording it costs 10 seconds; re-explaining a lost request costs the user a whole session. A step of the same deliverable → a `- [ ]` line; a separate deliverable → its own linked ticket (#1297). Work hidden in a comment on an unrelated ticket is work the footer never counts.
