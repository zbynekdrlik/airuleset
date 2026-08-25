---
name: autopilot
description: "Usage: /autopilot [status] [manual] [dialog]. Hands-off loop that solves the WHOLE GitHub backlog. To cut long-CI cost it BUNDLES bundle-safe small issues into ONE worker run → ONE PR closing all → ONE CI cycle (the bundling gate decides; big/schema/API/security/cross-cut issues run solo). By DEFAULT it CONTINUOUSLY dispatches SEVERAL such bundled batches as PARALLEL isolation:worktree in-session BACKGROUND autopilot-worker lanes, refilling to saturation (run_in_background — your main session stays FREE + thin, every worker stays visible in the agent strip) that can still ASK YOU the important questions directly, then the supervisor integrates each returned branch SERIALLY under an integration mutex (one merge/PR/CI/deploy at a time per repo — dispatch never blocked; falls back to one-worker-at-a-time when worktree isolation isn't available). Never pre-filters needs-input issues and never refuses to start; after each integration (incl. after merge) it refills the next lanes. status = show backlog + skipped, run nothing. manual = stop every PR at green for your merge. Merge/deploy follow pr-merge-policy.md (opt-out airuleset:merge=manual). DEFAULT (no dialog arg) = zero questions at start: preflight → banner → print the /goal line → stop, respecting existing autopilot-skip labels silently (nothing un-skipped, nothing added, nothing closed). dialog = run the interactive start-of-run flow first — reviews the skip set (asks which already-skipped issues to un-skip), lets you exclude more (autopilot-skip), and lets you interactively CLOSE obsolete issues — same flow the /autopilot-dialog alias runs. End-of-run (backlog empty) it does a reconciliation sweep over ALL remaining open issues INCLUDING skips — while context is fresh — closing/rescoping any ticket the run overcame (hard-overcome auto-closes with evidence; uncertain asks) — this sweep is UNCONDITIONAL, dialog or not. You can also close any issue anytime via 'close #N (reason)'."
argument-hint: "[status] [manual] [dialog]"
user-invocable: true
disable-model-invocation: true
---

# Autopilot — Hands-off Backlog Loop

> Solves the **ENTIRE** open backlog by CONTINUOUSLY saturating lanes — by DEFAULT a FLEET of
> PARALLEL `isolation: "worktree"` workers (#317/#456), one per solo ticket or bundle-safe batch,
> dispatched to keep every lane full and integrated SERIALLY by the supervisor as each branch
> returns (never held for a whole "round" to finish). Each batch is
> handed to an **in-session background `autopilot-worker` subagent** (`run_in_background: true`)
> — fresh context (your main session stays thin AND interactive — you can keep messaging it),
> visible in the agent strip, and **able to ask you the genuinely-important questions directly**.
> As each integration lands (merged + deployed) or a question resolves — and continuously as lanes
> free up — the loop refills the **next** lanes, including right after a merge. It **NEVER** pre-filters "needs input" issues and
> **NEVER** refuses to start. The goal is to finish everything; your only job is to answer the
> important per-issue questions when a worker raises one.

> **Usage:** `/autopilot [status] [manual] [dialog]`
> • *(no arg)* — **default: ZERO questions at start.** Preflight → banner → print the `/goal`
>   line → stop. Existing `autopilot-skip` labels are respected silently (nothing un-skipped,
>   nothing added, nothing closed) — the run just goes straight to work.
> • `status` — print the backlog + currently-skipped issues, run nothing
> • `manual` — stop every PR at green for your "merge it" this run (else default auto-merge)
> • `dialog` — run TODAY's full interactive start-of-run flow first: the skip-review +
>   add-skip picker (Step 1b) and the close-obsolete picker (Step 1c), THEN the `/goal` line.
>   Same flow as the thin `/autopilot-dialog` alias skill.

**What it removes (the old pain):** no more re-running `/issue-planner`, no manual `/compact`,
no "nothing is hands-off so I'm stopping". You answer the important questions; everything else runs.

> **On a GATEKEEPER / full-authority box prefer `/autopilot-master` (#22):** it runs this
> loop's body as ONE lane of a lane scheduler (sub-dev reviews, windowed releases, own
> backlog, user questions) under a single armed /goal that never parks. `/autopilot` alone
> stays the right command on sub-dev boxes and for a deliberate single-lane run.

**Context gate — apply all:**
- `autonomous-batch-issue-development.md` → **load the `batch-issue-development` skill at run start** (full policy lives there since 2026-07-09) — bundle bundle-safe issues into ONE PR/CI cycle (the gate + ceiling below)
- `pr-merge-policy.md` — default auto-merge; `airuleset:merge=manual` marker (or the `manual` arg) = stop at green PR
- `tdd-workflow.md` / `regression-test-first.md` — calibrated TDD per issue
- `ci-monitoring.md` — 2-branch single-CI repo: the worker monitors its OWN CI **foreground** (NEVER `run_in_background` — that ends the subagent), the main loop verifies the result; long / multi-stage pipeline (3-branch): the SUPERVISOR owns the CI waits and the worker returns per stage (Step 3 multi-stage note)
- `post-deploy-verification.md` / `version-on-dashboard.md` — deploys verified via the live DOM version
- `milestone-notifications.md` — short `❓`/`✅` idle pings only on a worker's ❓ question or the FINAL ✅ (mobile model); BUT each finished+deployed ticket ALSO sends ONE structured Discord completion card (the worker fires it directly at merge — the user's explicit per-ticket ask); every device message @mentions the tmux owner (zbynek/marek)
- `no-dropped-work.md` — workers file issues for everything identified but unfinished
- `verify-issue-still-valid.md` — the worker FIRST proves the issue still reproduces against current code + live system; obsolete/already-solved tickets get closed with evidence, never blindly implemented
- `ask-before-assuming.md` — a genuine per-issue question is a CONVERSATION with you, asked the MOMENT the ticket needs it and it ALWAYS pings; then either BLOCK (`❓ NEEDS YOU`) or ask-and-continue (`❓ ASKED` + track on the issue, work other tickets meanwhile) — never buried, never a reason to abandon the issue, never a reason to reproach you (the 00:00–06:00 — hours `00..05` — Europe/Bratislava sleep window defers a question ONLY while other work exists; a NECESSARY question — nothing else workable — pings even at night)
- `user-questions-slovak.md` — HOW to phrase it: SELF-CONTAINED (a person with ZERO terminal context understands it — which project, what happened, every cross-project/ticket link explained), plain Slovak, no jargon; delivered as the `❓` text marker (waits UNLIMITED), NEVER a 60-second `AskUserQuestion` dialog for an away user; structured template + ONE decision per ping is hook-enforced

## How it works

- **Engine = a `/goal` loop you paste once.** Each turn the main agent assembles a ROUND — by
  DEFAULT several bundle-safe BATCHES (each one bundle-safe issue, or several bundled into one PR
  — see Step 3.1) — and CONTINUOUSLY dispatches ONE in-session BACKGROUND `autopilot-worker` PER
  batch, `isolation: "worktree"`, running MANY IN PARALLEL and refilling to saturation
  (`run_in_background: true`, Step 3.2, #317/#456); every dispatch returns IMMEDIATELY so your main
  session stays FREE, and any worker finishing RE-INVOKES the loop. Each worker runs its cycle to a
  green LOCAL result on its own worktree branch; the main agent then integrates each returned branch
  SERIALLY under the integration mutex — one merge/test/push at a time — as it becomes ready and
  verifies from GitHub, WHILE new lanes keep dispatching. The loop refills continuously until the
  backlog is empty. (Worktree isolation unavailable, or a lane's candidates overlap too heavily to
  parallelize? Dispatch falls back to the documented single-worker serial shape — same mechanics,
  no `isolation:`, one batch at a time.)
- **Bundling AND fleet dispatch both cut cost — different axes.** CI is long here, so bundling
  spends ONE CI cycle on as many bundle-safe issues as the gate allows
  (`autonomous-batch-issue-development.md`) instead of one-PR-per-issue — this cuts CI cost per
  worker. Continuous fleet dispatch (many worktree-isolated worker lanes running concurrently) cuts
  WALL-CLOCK by keeping lanes saturated instead of working batches one after another. Issues that
  fail the bundling gate (large / schema / API / security / cross-cut) still run solo — as their
  own single-member batch within a round, or alone in the serial fallback.
- **Worker = in-session BACKGROUND `autopilot-worker` subagent** (`run_in_background: true`, user-
  level, installed by airuleset). Background so your MAIN session stays FREE (you can keep messaging
  it) and THIN while the worker runs — and since Claude Code's 2026-W26 change the worker's prompts
  and questions still SURFACE in your main session, so it can ask you. It stays VISIBLE in the agent
  strip (it's an in-session subagent — NOT a hidden `claude --bg` daemon). Fresh context so your main
  session never degrades; it returns only a short evidence block to the main agent.
- **Main session stays thin** — it holds only "dispatched #N → verified merged" summaries, so
  there is no `/compact` churn across a long backlog.
- **`/autopilot` itself does ONLY Step 1, then Step 2** — preflight, then, by **DEFAULT**, straight
  to printing the `/goal` line and **STOPPING** — **zero start-of-run questions**; existing
  `autopilot-skip` labels are respected silently. Only `/autopilot dialog` (or the
  `/autopilot-dialog` alias) inserts the interactive Step 1b/1c pickers between Step 1 and Step 2.
  It must **NOT** start dispatching workers on its own. The per-issue loop (Step 3) runs **only
  after YOU paste the `/goal` line** — only the user can type `/goal`, and without it nothing
  re-fires across turns (a directly-dispatched worker would do one issue and stop). So
  `/autopilot` always ends by handing you the `/goal` line to paste.

## Step 1 — Preflight

```bash
git fetch origin && git rev-parse --abbrev-ref HEAD && git status --porcelain   # dev, clean
gh auth status
python3 ~/devel/airuleset/airuleset.py core-quals --list    # full authority: the obligation set
# reduced authority: python3 ~/devel/airuleset/airuleset.py slice-quals --list
grep -n "airuleset:merge=manual" CLAUDE.md || true                              # merge mode
grep -n "airuleset:authority=" CLAUDE.md || python3 ~/devel/airuleset/airuleset.py authority   # authority profile
```

- Confirm the `autopilot-worker` subagent is available (`@agent-autopilot-worker` resolves). If
  not, run `python3 ~/devel/airuleset/airuleset.py install` once and restart the session
  (subagents load at session start).
- **Recommended:** run the session with **auto or bypass permissions** (Shift+Tab → auto) so
  routine worker tool-calls don't spam prompts. Genuine clarifying questions still reach you regardless.
- **Backlog scope = every open issue THIS box is obliged to action, and `autopilot-skip`/`ops-channel` are the
  only LABELS that FULLY exclude one.** Do **NOT** filter out `needs-design` /
  `question` / `blocked` — those stay fully workable and get worked; the worker raises the question with you. A
  backlog full of "needs input" issues is **NOT** a reason to refuse — start anyway. Only a
  genuinely empty backlog stops you. The one other carve-out is ownership, not preference: a
  ticket a SUB-DEV STREAM's own box works (`stream:<user>`) is not this box's to implement —
  **unless it also carries `needs-gatekeeper` or `ready-for-review`, which mean only THIS box
  can act on it** (cross-stream protocol rule 4/7 below), and then it is in scope as an ACTION
  (review / merge / close / unblock), never as an implementation. A BARE `prio:bounce` with
  neither of those alongside it is the sub-dev's OWN work in progress — not yet this box's to
  action (#307, 2026-08-07). On a full-authority
  box `python3 ~/devel/airuleset/airuleset.py core-quals --list` IS that set — use it wherever
  this skill lists the backlog, so SELECTION and the `/goal` stop-proof (which uses
  `core-quals --count`) can never disagree about what "done" means. **Every row carries that
  distinction in its own third column** (#181 round 4): `action-only` = a sub-dev stream owns it,
  so you action it and NEVER write its code; `implement` = ordinary work. Read the column, not
  your memory of this paragraph — the column is why it stopped being a prose promise. A ticket
  labeled `ops-channel` (a stream's own self-declared PERMANENT never-auto-close channel — a
  teardown/refresh loop, an automated alert log, e.g. odoo-erp #1861/#3037) is excluded from this
  set at the SAME tier as `autopilot-skip`, never workable regardless of age (#362; documented in
  `statusline-vocabulary.md`). A ticket parked on the OWNER — `needs-answer`/`needs-decision` (applied
  only AFTER a question was already raised) or a NOT-YET-SENT `needs-acceptance` (Claude must still get
  the owner to approve+send the client acceptance thread — a real question ON the owner) — is a
  DIFFERENT case: not fully excluded, but PARTITIONED into the user-waiting bucket. It leaves the
  workable `I N` / `core-quals --count` (the OWNER's court now, not this box's) and surfaces as `U N` /
  `core-quals --waiting` (each member tagged answer/decision/acceptance/ping); the loop PARKS on it —
  still tracked, never lost, never blocking 🏁 (#468/#512). **`U` = only what Claude ASKS / the owner
  must APPROVE** ("čo sa ťa Claude pýta / čo máš schváliť"). The standalone `Q` ❓-pings badge folds
  into `U N` too (#512: a ticketless session ping is a thing waiting on the owner; a ping that
  references a `#N` ticket is already counted via that ticket's label, never twice). **#526 acceptance
  automat:** `needs-acceptance` is a STATE MACHINE — while its client thread has NOT yet been
  approved+sent it sits in `U`/`--waiting` (above); once the stream SENDS the thread and adds
  `ops-wait` it moves to `W`/`--ops-wait` (below, tagged `acceptance` there) — now waiting on the
  CLIENT/third party, not the owner. **#507 precedence:** a `needs-acceptance` ticket that is ALSO a
  re-hand-off (`ready-for-review`/`needs-gatekeeper`) stays `gk`, or a returned bounce (`prio:bounce`)
  stays workable — never `U`. A `W`/`ops-wait` ticket (a supervisor-set advisory state — open but blocked
  on an external event/evidence with no dispatchable code lane, OR a SENT `needs-acceptance` per the
  automat above) is the SAME surface-only case for a DIFFERENT reason: it leaves `I N` / `core-quals
  --count` and surfaces as `W N` / `core-quals --ops-wait` (each member tagged `acceptance` for a sent
  acceptance vs `ops-wait` for a pure event-parked one); the loop PARKS on it identically — never
  blocking 🏁, cleared by the SUPERVISOR (not you, and no auto-labelling) when the event/evidence (or
  the client's acceptance) lands (#510/#526). **`W` = SENT, waiting on a THIRD PARTY** ("odoslané,
  čaká tretia strana"). The canonical `W` workflow: ask a THIRD PARTY (e.g. "zisti X od človeka Y cez
  Odoo discussion") → post the question in that thread, label the ticket `ops-wait` (it leaves `I N`
  into `W N`), check the thread as you work others, and CLEAR the label when they reply so the ticket
  re-enters `I N`. **#539 acceptance SIDE-BRANCHES — name them so a bare `needs-acceptance` never rots
  in `U`.** Besides that main "compose → owner approve → send → W" thread path, TWO real acceptance
  branches skip the thread and belong in `W` immediately: (1) **fix-class** — an owner-ruled NO-THREAD
  close waiting on an EXTERNAL event (e.g. a foreign-repo fix); the supervisor adds `ops-wait` WITH
  evidence and it is `W` at once (no thread will ever be sent). (2) **deferred-thread** — the
  acceptance thread is deliberately DEFERRED to a future event (go-live); park it in `W` with
  `ops-wait` naming that blocking event until it is time to send. Both are tagged `acceptance` in
  `--ops-wait`. The THIRD acceptance case is the canonical `W` workflow just above — a
  `needs-acceptance` ticket waiting on a THIRD PARTY (a client / a named person) is `ops-wait` → `W`,
  never a bare `U`. In every branch the supervisor SETS and CLEARS `ops-wait` with evidence — never
  auto-labelled. (montalu3 left 13 fix-class/deferred + 1 third-party acceptances as bare `U` = `U 15`
  with zero real questions — exactly what these branches prevent.) **THIRD branch — QUEUED = U (#622,
  owner directive 2026-08-22, REVERSES #539 chained-I).** A bare `needs-acceptance` (no `ops-wait`, no
  gk-override) is QUEUED for owner approval and ALWAYS `U` — the code is merged and its only next step
  is an owner-approved client message, never dispatchable-now `I` code work (`I` = only that; owner:
  "I29 = 29 ticketov na ktorych mozes robit … no nie na mna"). #539 had routed a bare needs-acceptance
  with NO delivered draft to `I` ("chained-I"), reading "no delivered ping" as the stream's OWN chained
  work; #606 (one-at-a-time owner-question delivery, AFTER #539) made that wrong for the common case —
  "no delivered ping" now overwhelmingly means QUEUED-behind-others = waiting on the owner. MECHANIZED:
  `_partition_workable` routes a bare `needs-acceptance` to `U` UNCONDITIONALLY (the #539
  `acceptance_present` routing param is removed — pure label partition); the delivered-vs-queued
  distinction is a DISPLAY tag only — `--waiting` tags a DELIVERED one `acceptance` (a live
  owner-approval question, ❓ ping fired) and an undelivered one `queued` (draft ready, awaiting #606
  delivery, no-question!-exempt). (Release-wait tickets — merged to develop but not yet on PROD, so the
  handover thread may announce only what lives on PROD — are the deferred-thread branch: `ops-wait`
  "waiting on release" → `W`.) **Owner-UX invariant, REVISED (#622): "otázky na mňa?" while `U > 0`** is
  answered by the #606 STEP-BY-STEP delivery — a positive `U` is real delivered questions PLUS queued
  acceptances (draft ready, delivered one at a time), never a phantom NIE. **W re-check is now MECHANICAL (#547):** the `ops-wait` re-entry is no longer
  prose-only — watchdog job 20 reads each armed loop's `--ops-wait` members and, on a ~daily cadence,
  types a `stuck-check:` re-check nudge into that session ("W #N parked — re-check the external state,
  clear `ops-wait` WITH evidence or confirm still waiting"), so a parked W ticket whose reply/release
  already landed is re-surfaced instead of leaving the loop blind (montalu5). The SUPERVISOR still stays
  the ONLY one that clears `ops-wait` with evidence — the nudge only surfaces, never auto-unlabels.
  **I→W/U freshness audit is MECHANICAL (#552):** the SAME job 20 partition-audit nudge also covers the
  OPPOSITE direction — on the ~daily cadence it reminds an armed loop to re-audit each `I` member against
  the #526/#539 shapes (a ticket already meeting a fix-class / sent-thread / deferred-thread shape →
  `ops-wait`=W, or carrying a delivered owner-question → U) instead of letting it rot in `I` until the
  owner asks (montalu3); the JUDGMENT stays in the session, only the scheduler is mechanical — the
  supervisor still sets/clears every label with evidence.
  **stale! freshness tag is MECHANICAL (#570) — W = tlač dopredu KAŽDÝ deň:** `W` is NEVER passive
  waiting — if `W N`, that is N things you must PUSH FORWARD every day so they move: at least a 1×/day
  reminder to the third party AND a re-verification that the blocker STILL holds by RE-READING the
  referenced (cross-repo) ticket, never from memory — each recorded as a COMMENT on the ticket (that
  comment IS the freshness evidence). Owner (2026-08-19, verbatim): "Ak je W 15 tak je 15 veci ktore
  mas pushovat dopredu aby sa to pohlo. Nie ze ... cakas x dni na nejake info v discussion odoo a vobec
  si tych ludi aspon raz denne nepovzbudil." Mechanized: `core-quals`/`slice-quals --ops-wait` tags
  `stale!` any W member with NO fresh (≤24h) stream-authored push comment (never a false accusation — a
  gh error / zero comments / unresolvable identity leaves it UNTAGGED), and the ~daily nudge NAMES the
  stale members with the required action (re-verify the blocker + remind the third party TODAY, with a
  ticket comment). **A daily reminder must be a SUBSTANTIVE, CONCRETE ask that MOVES the ticket,
  never a content-free ack (#568):** the mandated 1×/day third-party reminder is a real push — a
  specific question, a named blocker, the exact thing you need from them — NEVER a hollow "still
  working on it" acknowledgement; the `stale!` tag resets its 24h freshness clock on ANY
  stream-authored comment, so a content-free ack would silence the tag while the third party stays
  genuinely un-nudged (the exact "never actually nudged them" failure in tag-compliant disguise). A
  concrete ask is a real povzbudenie, not status noise.
  **Pipeline-gated tail + umbrella = W (#578)** — dva tvary ktoré #526/#539 NEpomenovali, a preto
  sedeli v bare `I` a nafukovali ho (gk `I 16`): (1) **release-gated tail** vlastnej pipeline
  (merged/queued, čaká na pomenovaný INTERNÝ release event — napr. „2.180 stage-3") je `ops-wait` → W
  s tým pomenovaným eventom (supervisor SETuje s dôkazom, CLEARuje pri landnutí stage), NIKDY bare
  `I`; (2) **umbrella/tracking ticket gated na INÝ ticket** je `ops-wait` → W s odkazom na blocker
  ticket, nikdy bare `I`. ODLÍŠ od bare `needs-acceptance`: tá je od #622 vždy `U` (queued na
  owner-schválenie klientskej správy — #539 chained-I `I`-fallback zrušený, bare acceptance už nikdy
  nesedí v `I`); pipeline-gated/umbrella čaká na RELEASE stage ALEBO na BLOCKER ticket → W.
  **Owner fyzická akcia = U, nikdy W (#601, owner ruling 2026-08-20).** Ticket, ktorý čaká na OWNEROV
  vlastný fyzický/manuálny krok (príď k rigu, sprav hardvérovú akciu, buď pri tom), NIE JE tretia
  strana — patrí do `U`, nie `W`: olabeluj `needs-owner-action` (v `--waiting` tagovaný `action`;
  doručené OZNÁMENIE = ❓ ping/komentár menujúci konkrétny krok, chýbajúce = `no-action!`).
  `needs-owner-action` + `ops-wait` → `U` (owner beats third-party framing); owner-action NIKDY nevojde
  do `W` bucketu, takže #570 `stale!` sa naň nevzťahuje; fyzický owner-krok je vždy owner-ova
  zodpovednosť → vždy `U` (rovnako ako bare `needs-acceptance`, ktorý je od #622 tiež vždy `U` — queued
  na owner-schválenie, nie odložiteľná vlastná práca streamu; #539 chained-I `I`-fallback zrušený).
  Owner nie je
  tretia strana — owner-blocked `ops-wait` je mis-shape, ktorý job-20 partition-audit nudge fleet-wide
  pomenúva, aby si bežiace slučky opravili svoje W tikety samy. Čistí ho SUPERVISOR s dôkazom, že owner
  krok spravil (paralela k `ops-wait`, nie auto Discord-answer).
- **NEVER prod/hardware-classify the backlog (the user's hardest rule — `approval-scope.md`).** When
  printing the banner / backlog / queue, do **NOT** flag, colour (🔴), tag, or bucket issues as
  "PROD / HARDWARE / live / off-air / invasive / risky / needs-the-rig / needs-you-present", do
  **NOT** recommend `autopilot-skip` for any of them, and do **NOT** warn about off-air windows, "you
  must be present / be at the rig", or "CI can't verify (manual self-hosted) so you must watch". A
  hardware / prod / streaming / OBS / HDMI / DRM issue is worked end-to-end on the rig like any other;
  the USER alone guards whether prod is live and stops you in the moment. (Same in Slovak: no
  `off-air okná`, `musíš byť pri tom`, `odporúčam autopilot-skip`, `vedene so mnou nie naslepo`.)
- **Authority profile (issue #16):** resolve it FIRST — a project CLAUDE.md marker
  `airuleset:authority=<full|branch-merge|fork-no-merge>` wins; else `python3
  ~/devel/airuleset/airuleset.py authority` (maps the linux user: david=fork-no-merge,
  marek/montalu=branch-merge, default full). The profile decides WHICH /goal template Step 2
  prints and what "done" means per ticket. **Reduced authority (branch-merge / fork-no-merge)
  additionally scopes the backlog to ISSUES ASSIGNED TO THIS STREAM** — use `python3
  ~/devel/airuleset/airuleset.py slice-quals --list` everywhere this skill lists the backlog. This
  is THE single definition of "my slice" (#181) — the SAME one the footer and the Discord card use
  — never hardcode `--assignee @me`, which silently returns 0 on a shared-gh-account stream box
  (montalu/marek/simap: `@me` there is the maintainer, matching nothing assigned). Shared trackers:
  marek + david both work odoo-erp; never grab another stream's tickets.
- **Print a one-line banner:** `autopilot · merge=auto (no manual marker) · authority=<profile> · I N · U M · W K · solving the whole backlog` — the workable count PLUS the parked `U`/`W` counts (`M`/`K` from the SAME `--count`/footer derivation), so the owner sees the split up front, not a bare `N`.
- **Surface the U/W BREAKDOWN, never just the counts (#527).** Whenever `U > 0` or `W > 0` — at the banner AND in every per-cycle `## ✅ Work Complete` report (Step 4) — print the actual parked MEMBERS, not only the numbers, so the owner SEES what waits without having to ask "máš na mňa otázku?": run `python3 ~/devel/airuleset/airuleset.py core-quals --waiting` (reduced-authority: `slice-quals --waiting`) and list each `U` member with its tag (`answer`/`decision`/`acceptance`/`ping`) + title, then `core-quals --ops-wait` (or `slice-quals --ops-wait`) for each `W` member with its tag (`acceptance`/`ops-wait`) + title. `U` = "čo sa ťa Claude pýta / čo máš schváliť" (a not-yet-sent acceptance is a real approve-the-thread question); `W` = "odoslané, čaká tretia strana".
- **Invariant — `U > 0` ⟹ every `U` member ALREADY carries a DELIVERED question (#527, owner directive "keď je U väčšie ako nula sa stále musím pýtať či má Claude na mňa otázku").** On EVERY cycle, for each `U` member from `--waiting`, confirm it has a delivered question the owner can act on: a fired ❓ ping (question map) OR a `needs-answer`/`needs-decision` comment that CARRIES the actual question (not a bare label). **This is now MECHANIZED (#539): `--waiting` itself tags any `U` member with no delivered question `no-question!` in its reason column** (a question-map `#N` reference OR an ask-flow-marked comment clears it; an unreadable map / gh error tags nothing — never a false accusation). A `U` member tagged `no-question!` (or one you spot without a question) is a defect — ASK its question now (`❓ ASKED` + a `needs-answer` comment on the issue), **ONE AT A TIME, SEQUENTIALLY** (never a pile — `user-questions-slovak.md`), so the owner never has to prompt "do you have a question for me?". (`acceptance`-tagged `U` members: the question is "approve + send the client acceptance thread"; once sent, the stream adds `ops-wait` and it moves to `W`.) The goal: the owner NEVER asks whether Claude has a question — a positive `U` always means the questions are already on their phone. **#606 (owner directive 2026-08-21): a "U N?" / "otázky na mňa?" status query is itself answered by STARTING this step-by-step delivery — the FIRST U member as its own full `**Otázka — projekt …:**` block, one at a time — NEVER by rendering the raw `--waiting` table or a summary list of every U member to the owner (that table is machine context; the owner cannot decode ticket-by-ticket asks from a compressed list).** Hook-backstopped: `stop-check-prose-violations.sh` blocks an owner-facing turn that piles 3+ `#N …?` per-ticket asks into one question turn.
- **Version-on-dashboard foundation gate** (web projects): no version label → that foundation
  issue is the FIRST work item (`version-on-dashboard.md`).

**Branch here on the invocation argument (#52, 2026-07-25):**
- **`dialog` arg present** (`/autopilot dialog`, or invoked via the `/autopilot-dialog` alias
  skill) → continue to Step 1b, then Step 1c, then Step 2.
- **No `dialog` arg (the DEFAULT)** → **skip Step 1b and Step 1c ENTIRELY** and go straight to
  Step 2. Existing `autopilot-skip` labels are respected exactly as-is — nothing is un-skipped,
  nothing is newly excluded, and **nothing is closed or asked about**. The user's directive: *"chcel
  by som aby by default autopilot rovno pracoval a daval goal a nepytal sa co skipnut co uzavriet
  etc … chcem vzdy smerovat k nula ticketov"* — the default run's only interaction is the ONE
  `/goal` paste in Step 2. (The end-of-run reconciliation sweep, Step 4a, is UNCONDITIONAL — it
  still runs when the backlog empties regardless of `dialog` or not.)

### Step 1b — Skip review + picker (DIALOG ONLY — `/autopilot dialog`; the skip set is RE-WEIGHED, not frozen)

**Runs ONLY when invoked with the `dialog` argument.** In the DEFAULT run (no `dialog`) this
step is skipped entirely per the branch above — go straight to Step 2.

Run BOTH halves every start so a skipped task is reconsidered each run. Ensure the label exists once:
`gh label create autopilot-skip --color ededed --description "Excluded from autopilot runs" 2>/dev/null || true`.

**(i) Un-skip review — reconsider what is ALREADY skipped (do this FIRST).** List the currently-skipped
issues: `gh issue list --state open --label autopilot-skip -L 100`. If ANY exist, PRINT them
(`#N <title> (Xd old)`) and ask via `AskUserQuestion` (`multiSelect: true`, one option per issue) which
to **UN-skip** this run. For each chosen: `gh issue edit <N> --remove-label autopilot-skip` → it
re-enters the backlog. **Default = keep all skipped (un-skip none).** This is how a deliberately-skipped
task gets re-weighed without silently losing the skip. (If none are skipped, say so and move on.)

**(ii) Add-skip picker — exclude anything you do NOT want touched at all this run.** The default is
*work everything*. PRINT the full open-issue list (`#N <title> (Xd old)`, one per line), then ask which
to EXCLUDE via `AskUserQuestion` with `multiSelect: true` (one option per issue). AskUserQuestion renders
~4 options per question, so split across multiple ~4-option questions, or for a large backlog show the
oldest subset and let the user add any other numbers via "Other" (comma-separated) — the printed list
backs that. Apply to each chosen issue: `gh issue edit <N> --add-label autopilot-skip`, then print
`skipping #A #B … · working N issues`. **Selecting none = work all (the normal case).** NEW issues filed
by workers never carry this label → always worked.

### Step 1c — Close obsolete issues (DIALOG ONLY — `/autopilot dialog`, interactive)

**Runs ONLY when invoked with the `dialog` argument.** In the DEFAULT run (no `dialog`) this
step is skipped entirely per the branch above — go straight to Step 2; no issue is closed here.

You often already know a task no longer makes sense but it lingers with no easy way to close it — this
is that way. From the working backlog (open issues minus `autopilot-skip`), PRINT the full list
(`#N <title> (Xd old)`) and ask via `AskUserQuestion` (`multiSelect: true`) which are **OBSOLETE and
should be CLOSED now**. Present the list NEUTRALLY: do **NOT** recommend which to close, and **NEVER**
classify / flag / colour any issue (especially not prod/hardware — `approval-scope.md`). For each chosen:
`gh issue close <N> --comment "Closed at /autopilot start — obsolete per user."`, drop it from the
backlog, and note the closures (no per-issue device ping — `milestone-notifications.md`). **Default =
close none.** Same ~4-options-per-question / "Other" handling as the picker above. (You can ALSO close any
issue at any time — in `/autopilot` or normal chat — by telling Claude `close #N (reason)`; it runs
`gh issue close <N> --comment "<reason>"` + ping. Closing an issue is non-destructive tracking and never
needs extra approval.)

## Step 2 — Start the engine (the one manual paste, auto-armed as a backstop)

The agent itself cannot type `/goal` into its own input — print the ONE line matching the resolved authority profile for the user to paste once, exactly as before. **Immediately after printing it, as your OWN last tool call of this turn, run `python3 ~/devel/airuleset/airuleset.py goal-arm --self`** (#403 — the collapsed callback model, `watchdog/goal.py`'s own module docstring is the single source of truth for how this is delivered). This call IS the arm request — it records the printed line's exact text against your own session id and attempts one immediate delivery; watchdog job 9 (`goal.goal_sweep`) then re-evaluates the SAME recorded request every sweep until it lands (pane busy/holding a draft → delivered via `deliver_with_stash`, the same primitive job 20's lane-nudge uses, never overwriting a draft; a hard age cap expires the request with one deduped "arm failed, re-run /autopilot" ping if it never gets a chance to land at all). Job 9 no longer guesses an arm intent from anything visible in the pane — it only ever acts on an EXPLICIT recorded request, so the printed line staying on screen is never itself a trigger. If `goal-arm --self` cannot resolve this session's own pane (rare — a non-tmux/non-Claude-Code environment), the printed line is still there for the user to paste by hand, exactly as documented below. Never claim the loop is armed until the `/goal` line has actually been submitted (`Goal set:` in the transcript, or `◎ /goal` in the footer) — printing the line, or merely calling `goal-arm --self`, is not by itself proof of arming.

**AUTHORITY: full** (default — merge to main + deploy):

```
/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, both checkable from the transcript: (A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU` (after I answer, Claude resolves that ticket and re-prints this /goal line if issues remain). (B) BACKLOG EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED. Every open issue THIS box is OBLIGED to action — the CORE slice (not labeled autopilot-skip, not owned by a sub-dev stream) PLUS every ticket only I can action whatever stream owns it (needs-gatekeeper, a hand-off awaiting my review/merge/close) — is resolved, and (B) holds ONLY when my final message carries the pasted OUTPUT of both proof commands: `python3 ~/devel/airuleset/airuleset.py core-quals --count` printing exactly `0` under it (it counts EXACTLY that obligation set), AND `gh run list -b main -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND then the line `🏁 BACKLOG EMPTY: 0 open, main green` directly above the terminal `✅ DONE:` marker. HOW TO TELL A REAL COMPLETION FROM A CLAIMED ONE: real = output shown; claimed = asserted. `✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a merged PR. IF I CANNOT TELL — missing, unreadable, or stale output, any doubt — (B) does NOT hold: CONTINUE. There is no third answer. TO PRODUCE THE PROOF: run both, paste each output, write the `🏁` line — no proof, no stop. A stream ticket in that set is NOT mine to implement — I ACTION it (review, merge, close, unblock) and never write its code (a bare sub-dev bounce is NOT in this set — `/process-subdev`'s loop holds it). Also stop for a genuinely-irreversible approval or a CI failure unfixable after two real attempts. While NEITHER holds, work the backlog — SATURATE, never one ticket per turn: refill PARALLEL `isolation:worktree` autopilot-worker lanes to saturation, backing off only on a resource signal; integrate returned branches SERIALLY, ONE per turn under the integration mutex. Never gate, classify, skip, or warn based on prod-usage / events / off-air / hardware — I alone guard whether prod is live. ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable. A `needs-answer`/`needs-decision`/`needs-acceptance`/`ops-wait` ticket is parked — never counted, never blocks 🏁 (paste `core-quals --waiting`/`--ops-wait`). NEVER bury a question or blame my silence. 00:00–06:00 Europe/Bratislava: defer only while other tickets are workable; a NECESSARY question is asked even at night. Bounce lane: open tickets labeled prio:bounce jump the queue — every NEW batch seeds oldest-first (never preempting a running batch); a named nudge gets a one-line ACK + prio:bounce label, taken next turn, never worked inline. Count a ticket done ONLY after verifying from primary sources — `gh pr view` (merged, closingIssuesReferences), `gh run list` (main green), `gh issue view` (closed), the deployed version on the live target — never the worker's claim alone; verify the LAST ticket as strictly as the first. After every merge, END the turn with the full `## ✅ Work Complete` report (`completion-report.md`) terminating in `✅ DONE:` — which means CONTINUE and NEVER satisfies (B) — FIRST run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (returns promptly, #402) as your own last tool call, THEN do NOT integrate a SECOND branch this turn — the compact boundary paces ONE integration per turn, NOT the parallel lanes, which keep building; the ARMED GOAL fires the NEXT TURN (lets the context compact).
```

**AUTHORITY: branch-merge** (montalu / marek shape — own PR merged into the project's INTEGRATION branch only, never staging/main, never deploy):

```
/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, both checkable from the transcript: (A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU`. (B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED. Every open issue ASSIGNED TO ME here not labeled autopilot-skip is MERGED via my own PR into the project's INTEGRATION branch (develop unless the project CLAUDE.md names another), no open prio:bounce for my stream, and (B) holds ONLY when my final message carries the pasted OUTPUT of all four proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `gh run list -b <integration> -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND `git merge-base --is-ancestor <my last integration merge> origin/main && echo RELEASED` printing exactly `RELEASED` under it, AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (a `gk N`/`U N`/`W N` is parked — gatekeeper-owned/user-parked/ops-wait, not mine to wait on, never blocks 🏁; blank = unmeasurable), AND then the line `🏁 BACKLOG EMPTY: 0 open, integration green, released` directly above the terminal `✅ DONE:` marker. HOW TO TELL A REAL COMPLETION FROM A CLAIMED ONE: real = output shown; claimed = asserted. `✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a merged PR. IF I CANNOT TELL — missing, unreadable, or stale output, any doubt — (B) does NOT hold: CONTINUE. There is no third answer. TO PRODUCE THE PROOF: run all four, paste each output, write the `🏁` line — no proof, no stop. A handed-off ticket or an empty backlog, release still pending, is NOT done — REVIEW-WATCH: stay alive, re-check hourly with a FOREGROUND sleep-poll (~1h; never a wakeup/schedule), end ⏳ WORKING; never park silently — work any new stream/bounce ticket. My authority ENDS at the integration branch: never promote to staging/main, never deploy, never touch other streams'. Also stop for a genuinely-irreversible approval or a CI failure unfixable after two real attempts. While NEITHER holds, work the assigned backlog — SATURATE, never one ticket per turn: refill PARALLEL `isolation:worktree` autopilot-worker lanes to saturation, backing off only on a resource signal; merge returned branches into the integration branch SERIALLY, ONE per turn under the integration mutex; ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable. 00:00–06:00 Europe/Bratislava: defer only while other tickets are workable; a NECESSARY question is asked even at night. Bounce lane: my prio:bounce tickets seed each NEW batch oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label next turn, never inline. Count a hand-off done ONLY after verifying it from primary sources — `gh pr view` (merged into integration), that branch's CI run, the READY-FOR-REVIEW comment posted — never the worker's claim alone; verify the LAST as strictly as the first. After every merge, END the turn with the full `## ✅ Work Complete` report (the branch-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B) — FIRST run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (#225) as my last tool call, THEN do NOT integrate a SECOND branch this turn — the compact boundary paces ONE integration per turn, NOT the parallel lanes, which keep building; the ARMED GOAL fires the NEXT TURN (lets the context compact).
```

**AUTHORITY: fork-no-merge** (David shape — fork branch + local verification + ready hand-off; NEVER open or merge a PR, never close the issue yourself):

```
/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, both checkable from the transcript: (A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU`. (B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED. Every issue ASSIGNED TO ME here not labeled autopilot-skip is HANDED OFF — closing it after is the maintainer's job, not mine to prove — and (B) holds ONLY when my final message carries the pasted OUTPUT of all three proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `git merge-base --is-ancestor <my last merged commit> origin/main && echo RELEASED` printing exactly `RELEASED` under it (release still pending is STILL review-watch, not done), AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (a `gk N`/`U N`/`W N` is parked — gatekeeper-owned/user-parked/ops-wait, not mine to wait on, never blocks 🏁; blank = unmeasurable), AND then the line `🏁 BACKLOG EMPTY: 0 open, released` directly above the terminal `✅ DONE:` marker. HOW TO TELL A REAL COMPLETION FROM A CLAIMED ONE: real = output shown; claimed = asserted. `✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a clean local verification. IF I CANNOT TELL — missing, unreadable, or stale output, any doubt — (B) does NOT hold: CONTINUE. There is no third answer. TO PRODUCE THE PROOF: run all three, paste each output, write the `🏁` line — no proof, no stop. An open ticket carrying my READY-FOR-REVIEW comment (names the fork branch + green local verification; the comment is the signal, the label best-effort) never blocks 🏁, but PREFER REVIEW-WATCH: stay alive, re-check hourly with a FOREGROUND sleep-poll (~1h; never a wakeup/schedule), end ⏳ WORKING; never park silently — work any gatekeeper bounce. My authority ENDS at the hand-off: I push MY fork branches + evidence — NEVER open/merge a PR, never push upstream, never deploy, never close the issue, never touch other streams'. Also stop for a genuinely-irreversible approval or local verification failing twice. While NEITHER holds, work the assigned backlog — SATURATE, never one ticket per turn: refill PARALLEL `isolation:worktree` autopilot-worker lanes to saturation, backing off only on a resource signal; hand off returned fork branches SERIALLY, ONE per turn; ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable. 00:00–06:00 Europe/Bratislava: defer only while other tickets are workable; a NECESSARY question is asked even at night. Bounce lane: my prio:bounce tickets seed each NEW batch oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label (best-effort), taken next turn, never worked inline. Count a hand-off done ONLY after verifying from primary sources — the `READY-FOR-REVIEW:` comment present (`gh issue view --json comments`), the fork branch pushed, local test/lint output shown — never the worker's claim alone; verify the LAST as strictly as the first. After every hand-off, END the turn with the full `## ✅ Work Complete` report (the fork-no-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B) — FIRST run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (#225) as my last tool call, THEN do NOT hand off a SECOND branch this turn — the compact boundary paces ONE hand-off per turn, NOT the parallel lanes, which keep building; the ARMED GOAL fires the NEXT TURN (lets the context compact).
```

The condition lists ONLY `autopilot-skip` as the exclusion, so `needs-design` / `needs-decision`
/ `question` issues all count toward "must be closed" — the loop works them WITH your input.

**Why the templates read the way they do** — rationale lives HERE, never inside the armed
condition, which Claude Code caps at **4000 characters** and rejects outright when exceeded
(#169: a single commit took all three past the cap and no goal could be armed anywhere until it
was cut back; `tests/test_goal_backlog_proof.py` now locks the cap):

- The **counted `--jq 'length'`** form in (B) is deliberate. A bare `gh issue list` prints
  NOTHING when the backlog is empty, so its "proof" would be a blank space — indistinguishable
  from never having run the command. The counted form prints a literal `0`, which a
  transcript-only evaluator can actually read.
- The reduced-authority proof runs `airuleset.py slice-quals --count`, never a hand-rolled
  `--assignee @me` (#181: on a shared-gh-account stream box — montalu/marek/simap — `@me` resolves
  to the maintainer, matching nothing assigned, so the loop declared the backlog empty with real
  labelled work open). `slice-quals` is THE one definition of "my slice", shared with the footer
  and the Discord card — gh's own `--search` syntax ANDs space-joined qualifiers, it cannot OR
  them, so an own-account stream's 3-way union (assigned ∪ authored ∪ stream-labelled) cannot be
  expressed as one hand-rolled `--search` fragment either; the command runs the union server-side
  and prints only the result.
- The **full-authority proof runs `airuleset.py core-quals --count`**, never a bare whole-repo
  `gh issue list` (#181 I4, round 2): a whole-repo stop-proof could never legitimately reach 0
  while any stream still had open work, and a core/gatekeeper box is forbidden from working those
  tickets. But the CORE slice alone is the wrong set the other way (#181 round 3): it is the
  FOOTER's *display* partition ("which population am I showing"), and reusing it as the
  *obligation* partition ("what must I finish before I may stop") excluded the tickets ONLY this
  box can move. Measured on `zbynekdrlik/odoo-erp` 2026-07-30 (round 4 re-measurement, stated in
  LABELLED units — the earlier figures here and in `docs/autopilot-log.md` looked contradictory
  because two of them were different QUANTITIES: `13` was obligation-minus-core, `54` was the
  obligation total): **83 open non-skip / 44 core / 56 obligation**, the 56 being the 44 unioned
  with 7 `needs-gatekeeper`, 11 `prio:bounce` and 0 `ready-for-review` — that was the round-4
  formula. **#307 (2026-08-07) corrected it: `prio:bounce` is NOT unioned in any more.** It means
  the gatekeeper returned the ticket to the SUB-DEV — the sub-dev acts next, not this box, so a
  bare open `prio:bounce` with no `needs-gatekeeper`/`ready-for-review` alongside it is the
  sub-dev's own work and does not block the obligation set (a ticket carrying BOTH still counts,
  via the `ready-for-review` qual). The 11 in the round-4 measurement above were entirely
  `stream:david`'s own work — counting them inflated a real `core-quals --count` on the same repo
  from 63 to 77 a few days later, and a full-authority `/goal` SELECTING from that inflated set
  could start IMPLEMENTING a sub-dev's bounce fix, violating the standing rule that the gatekeeper
  never patches a sub-dev's branch. #2396 and #2377 carry `stream:montalu` **and**
  `needs-gatekeeper`, so a core-only count cannot see them. So `core-quals` now counts the
  **obligation set**: the core slice UNIONED with every open ticket labelled `needs-gatekeeper` /
  `ready-for-review`, whatever stream owns it. A stream ticket the sub-dev is actively working
  (including a bare `prio:bounce`) carries none of those and still does not block this box, so
  this is not a revert to whole-repo. This is also what makes cross-stream rule 4 ("neither side
  ever finishes while the other holds its ball") MECHANICAL on the gatekeeper's side rather than a
  prose guarantee, for a HAND-OFF awaiting review — a bounce still being fixed is the sub-dev's
  ball alone, and the gatekeeper's loop may legitimately stop while it is worked, resuming once
  `ready-for-review` reappears.
- **`core-quals --count` IS the number the footer renders as `I N` on a full-authority box** —
  UPDATED by #367 (2026-08-11, third footer simplification round): the footer's own refresh now
  calls the SAME `_obligation_quals()`/`_union_open_issues()` derivation this stop-proof uses,
  never a parallel narrower one, so the two can no longer silently disagree about what "done"
  means the way #181 documents happening before this fix. (The pre-#367 divergence this bullet
  used to describe — the footer showing a narrower `core` count plus a separate `· streamy M`
  badge — is gone; both badges were dropped along with the split.)
- A hand-off in that obligation set is detected by a hand-off **LABEL** — **`ready-for-review`**,
  or **`needs-gatekeeper`** for a carve-out stream whose hand-off gate strips `ready-for-review`
  structurally (airuleset #498; `MAINTAINER_ACTION_LABELS` already unions both) — the same
  signal the footer's `gk` bucket uses, not by the `READY-FOR-REVIEW:` comment that is its primary
  signal — the only single-query comment form is `"READY-FOR-REVIEW:" in:comments`, and GitHub
  tokenizes quoted phrases (the 2026-07-24 `in:title` false match), so it over-matches, and
  over-counting the obligation set is the never-stops failure again. A hand-off whose label the
  repo's `subdev-handoff-label.yml` workflow failed to add is the known residual.
- **`✅ DONE:` never satisfies (B)** because it is the per-ticket CONTINUE terminator. camera-box
  stopped on exactly that with 129 issues open (2026-07-28), in a turn that had itself just filed
  another one.
- **Every ambiguity resolves to CONTINUE.** Non-compliance then costs turns, never a silent stop
  — which is why a text-level rule is sound here and was not in #134.
- The **release-containment proof** on the reduced variants exists because tickets were closed
  while production got nothing and both sides reported done (2026-07-20).
- The **foreground sleep-poll** in review-watch is not a preference: a wakeup/schedule mechanism
  inside an armed `/goal` fires the next turn immediately and spins tokens (the gk burn,
  2026-07-20).

**This is the LAST thing `/autopilot` does.** Present the `/goal` line prominently in a code block,
tell the user to paste it to start the loop, and **STOP** — end your message with
a conforming question block (the question-quality gate requires the briefing line):

```
**Otázka — projekt <repo> (<čo projekt robí>):** autopilot je pripravený — backlog má N otvorených ticketov.
• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám
• Nič nevkladaj — autopilot sa nespustí
❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne
```

Do **NOT** proceed to
dispatch any worker yourself — **Step 3 is the LOOP BODY that the `/goal` loop runs each turn AFTER
the user pastes the line**, not part of this initial invocation. Dispatching a worker now (without
`/goal` running) would do one issue and stop — the exact failure this avoids. If you skip printing
the `/goal` line, the loop never starts.

## Step 3 — Per-issue cycle (the loop body — run BY the `/goal` loop each turn, NOT by the initial `/autopilot` call)

> You reach this section only when a turn fires under the `/goal` loop the user pasted in Step 2.
> The plain `/autopilot` invocation STOPS at Step 2 — it never runs Step 3 itself.

Each loop turn keeps a CONTINUOUS FLEET of `isolation: "worktree"`-isolated `autopilot-worker`
lanes running in PARALLEL — one lane per solo ticket or bundle-safe batch — refilling to
saturation every turn, and integrates each returned branch SERIALLY, under an integration
mutex, as it becomes ready (never waiting for the whole fleet to finish first).
Fleet dispatch is the default dispatch shape (2026-08-08, #317): the `Agent` tool's
`isolation: "worktree"` gives each worker its OWN checkout sharing only `.git`, so the collision
risk that used to force one-worker-at-a-time dispatch (two workers editing the SAME `dev` tree) no
longer applies once each worker has its own worktree — this repo's own 2026-08-08 session already
ran #313+#315+#316 as three parallel worktree workers alongside a #311+#312 batch in the shared
tree, four concurrent workers, zero collisions. What stays STRICTLY serial is INTEGRATION: merging
N worktree branches, running the one CI/test cycle, and pushing — always ONE AT A TIME, always
supervisor-owned, never by a worker itself (Step 4 below). Bundling (packing more issues into one
worker's PR, `autonomous-batch-issue-development.md`) and fleet-parallelism (running several
bundled batches at once) are COMPLEMENTARY levers, not substitutes: bundling cuts CI cost per
worker, fleet dispatch cuts wall-clock by running several bundled batches concurrently.

**Serialize-on-overlap — no fixed lane cap (#456).** There is NO fixed number of parallel lanes:
SATURATE — dispatch a lane for every workable bundle-safe unit — bounded ONLY by the real resource
signals in the next paragraph, never by a chosen number. A worker should still prefer running a
SCOPED test subset first before the full suite where the project supports it, same discipline as
any single worker. When assembling a
candidate batch for a NEW lane (repeat the per-lane procedure below for each lane you dispatch),
SKIP — don't dispatch it — any issue whose bundling-relevant files heavily overlap a
batch ALREADY claimed by a LIVE lane (today's live example: #311/#316/#317
all edited `agents/autopilot-worker.md` and had to be sequenced, not parallelized). Two workers
independently editing the same file in two separate worktrees is a guaranteed merge conflict at
integration — worse than simply waiting until the overlapping lane has integrated. An overlapping
issue is not lost — it seeds a LATER lane, exactly like any issue that fails the bundling gate today.

**No fixed agent cap — saturate; back off ONLY on a real resource signal + stagger (#456; the
#332 numbers below are measured CONTEXT, not a cap).** There is NO fixed number bounding the
agents this loop runs concurrently: the worker lanes PLUS the read-only `ticket-validator`
dispatches Step 1b fires for EVERY batch member PLUS every LANE 1 review dispatch PLUS anything a
DIFFERENT concurrent lane or session under this account runs are all the SAME kind of Claude-API
subagent, from the SAME account, against the SAME server-side rate limit. So the bound is
ACCOUNT-WIDE (one rate limit shared by everything the account runs, workers and read-only helpers
alike), never per lane, per round, or per repo — and it is REACTIVE, not a number: SATURATE
(dispatch every workable unit in parallel) and back off ONLY when a real resource signal appears —
a server-side rate-limit error, box memory pressure, or CC's own max-concurrent-subagents
ceiling. A CI-waiting lane is NOT a local slot and never bounds new dispatch (CI runs on dynamic
autoscaled VPS runners — capacity is not local). What a rate-limit signal actually looks like,
measured (2026-08-08, this repo's own dogfooding): a burst of 4 parallel worktree workers ran with
no rate-limit kills (it did hit a benign doc-append merge conflict at integration, resolved
keep-both per `docs/autopilot-log.md` — unrelated to rate limiting); a LATER burst of 5 workers
PLUS 13 concurrent `ticket-validator` dispatches — 18 total agents fired at once — had 3 of them (a
worker and two validators) killed by a server-side rate limit ("Server is temporarily limiting
requests (not your usage limit) · Rate limited") within a few minutes. **Stagger on a signal: when
a rate-limit error, memory pressure, or the max-subagents ceiling hits (or a planned burst is
obviously huge), split the dispatch into sequential WAVES** — fire the first wave (however many
`Agent` tool_use blocks fit), wait for it to genuinely return (a real bounded pause, e.g. tens of
seconds, gives the rate limiter room to recover) before the next wave. Never fire one giant
simultaneous burst just because the harness lets a single message hold arbitrarily many `Agent`
tool_use blocks — but equally, never throttle to a fixed small number below what a real signal
actually shows.

**Serial fallback (documented, not an improvisation).** Dispatch stays the single-worker,
shared-tree, cross-session-locked shape (unchanged, described in full below) whenever: worktree
isolation is genuinely unavailable in this environment, or every remaining round candidate this
turn overlaps a batch already claimed this round (nothing left to safely parallelize). Never force
a worktree merge you can already see will conflict — serialize instead. A round of size 1 (fleet
dispatch with a single worker) and the serial fallback are behaviorally identical except for the
`isolation:` flag; the fallback exists for the environments/situations where even THAT flag is
unsafe to use.

**Repo-flow policy — which target a round's branches integrate into:**
- **Local-merge repo** (pushes straight to `main`, no PR/CI — e.g. airuleset itself): the round's
  worktree branches each merge `--no-ff` into local `main` ONE AT A TIME (Step 4), THEN one full
  test suite, THEN one `push`. Each integration cycle (one integration-mutex hold) merges
  whatever branches are READY at that moment, never waiting for stragglers; a branch that finishes
  mid-cycle joins the NEXT integration cycle.
- **`dev`→`main` PR repo** (the ordinary two-branch flow): each integration cycle merges the
  branches READY then `--no-ff` into local `dev` one at a time, THEN the existing bundling model
  applies UNCHANGED — ONE `dev`→`main` PR closing those ready members, ONE CI cycle.
  Parallel worktree DEVELOPMENT is unchanged — the integration mutex just serializes each
  merge/PR/CI cycle so two never collide across sessions.
- **Reduced-authority streams** (branch-merge / fork-no-merge) follow their own authority profile
  exactly as today — a fork-no-merge worker's worktree branch IS its own fork branch: pushed and
  handed off by the worker itself, never merged by the supervisor, never folded into a round-wide
  integration wave.

**When fleet dispatch pays off (and when it doesn't) — generalizes to every project, including
long-CI repos (#332).** Fleet parallelism is a PURE WIN on a `dev`→`main` PR repo with a genuinely
long CI, at least as much as on a fast one: each integration cycle's ONE CI (repo-flow policy
above) is paid exactly once no matter how many lanes built the branches feeding it, so
parallelizing the IMPLEMENTATION phase never costs anything on the CI side — it only saves
wall-clock, and a CI-waiting lane never blocks dispatch of the next one (dynamic autoscaled VPS
runners — CI capacity is not local). The supervisor's own CI wait during a cycle is its own
long-lived job and follows `ci-monitoring.md`'s short-wait-foreground / long-wait-background split
exactly like any other CI wait (the supervisor, never a worker, is the component a
`run_in_background` poll safely re-invokes across a long wait). Fleet dispatch genuinely is NOT
worth it — fall back to a plain solo dispatch (still `isolation: "worktree"` for consistency where
available, but no wall-clock benefit to expect) — only when there is a single workable candidate
at all this turn. The bundling gate (`autonomous-batch-issue-development.md`) plus the collision
heuristic together are the whole answer to "which issues share one lane" — this ticket found no
gap in either.

**Continuous lane refill — saturate; NEVER 1–2 lanes while a bundle-safe backlog sits (#456, supersedes the #442 fill-the-cap round mandate).** DISPATCH is CONTINUOUS, not per-round: on ANY loop turn, while unworked bundle-safe backlog remains, assemble the next bundle-safe batch (per-lane procedure below — bundling gate + collision heuristic, skipping only a batch that file-overlaps a LIVE lane) and dispatch it into a fresh `isolation: "worktree"` lane IMMEDIATELY — including WHILE other lanes still run and WHILE returned branches await integration. Refill to saturation every turn — a lane per every workable unit, bounded ONLY by real resource signals (the no-fixed-cap section above: rate-limit errors, box memory, CC max-subagents), never a number; a CI-waiting lane never holds a slot. A single lane's long CI wait then never idles the fleet, because the others were dispatched the moment they were free — never held for a "round" to finish. The ONLY thing serialized is INTEGRATION: Step 3.2's integration mutex (one merge→gates→push at a time per repo across all sessions) — DISPATCH never waits on it.

1. **Per lane SLOT — assemble one BATCH; bundle by default to spend ONE CI cycle on many issues**
   (`autonomous-batch-issue-development.md`). CI here is long, so bundling small issues into one PR
   is the main lever to cut CI cost per worker (fleet dispatch, above, is the lever that cuts
   wall-clock across workers).
   - **Seed — PRIORITY LANE first (`prio:bounce`).** Open non-skip issues labeled `prio:bounce`
     (a reviewer/gatekeeper-INJECTED priority ticket — the bounce lane from odoo-erp #1599, but the
     label is a GENERIC cross-repo convention every repo/stream honors, never an odoo-specific
     hardcode) jump the queue: seed = the **OLDEST open `prio:bounce`** ticket — full authority
     `python3 ~/devel/airuleset/airuleset.py core-quals --list --extra "label:prio:bounce"`,
     reduced authority `python3 ~/devel/airuleset/airuleset.py slice-quals --list --extra
     "label:prio:bounce"` (THE single definitions #181, already oldest-first). **Never a raw
     `gh issue list` here** (#181 round 4): the seed is the highest-priority SELECTION path, and a
     raw query is the one path with neither the false-empty guard nor the ownership column — while
     the oldest open bounce ticket on odoo-erp is #2150, `stream:david`. **A row marked
     `action-only` is NOT yours to implement** — you review / merge / close / unblock it and never
     write its code; only an `implement` row is ordinary work.
     Several open bounce tickets that pass the
     bundling gate bundle together like any other issues, bounce ones first. No bounce ticket open
     → seed = the next open non-`autopilot-skip` issue (highest priority / oldest first — the
     normal queue). **A RUNNING batch is NEVER preempted** — it finishes, the bounce ticket seeds
     the very NEXT batch, then the normal queue resumes (the user's flow: "dokonči rozrobený →
     sprav gatekeeper ticket → pokračuj v ostatných"). The worker removes the `prio:bounce` label
     at its done-point, so a resolved bounce leaves the lane automatically.
   - **Grow greedily** by adding more open backlog issues that EACH pass the **bundling gate** vs the
     seed and the batch-so-far:
       • each member ≤ ~300 LoC estimated, AND cumulative batch ≤ ~600 LoC, AND ≤ 4 issues (keep the
         PR reviewable);
       • no DB schema/migration, no public-API break (routes/exported types/CLI flags), no
         security-boundary change (auth/permissions/secrets), no cross-cutting refactor (rename >5
         files / dep major bump / framework upgrade);
       • independent — no member depends on another member's design choice.
   - An issue that FAILS the gate is NOT added — it becomes the seed of a LATER solo batch (its own PR).
     A large / schema / API / security / cross-cut seed runs SOLO; never force-bundle it.
   - **Best-effort:** if nothing else qualifies, the batch is just the seed (one issue — today's behavior).
1b. **VALIDATE EACH batch member FIRST — hard gate** (`verify-issue-still-valid.md`). Before dispatching
   the worker, dispatch the read-only **`ticket-validator`** subagent
   (`subagent_type: ticket-validator`, prompt `Validate issue #<N> in <repo>`) for EVERY member — they
   are independent, so validate them in parallel, **but they are the SAME account-wide rate-limited
   agents as the worker fleet (the no-fixed-cap section above): SATURATE, and back off ONLY on a
   real resource signal — staggering the validators PLUS workers into sequential WAVES when a
   rate-limit error, box memory pressure, or CC's max-subagents ceiling hits, never a fixed number
   (#456).** A validator KILLED by a rate limit (or any
   other fatal API error) mid-dispatch is NEVER re-dispatched and NEVER blocks the round — treat
   that ONE member exactly as if Step 1b had simply been skipped for it (the worker's own Step 0
   re-validation, `verify-issue-still-valid.md`, mechanically backstops every member regardless of
   whether Step 1b ran — #213), and dispatch its worker normally without a Step 1b verdict. Branch
   PER member (for every validator that DID return):
   - **STILL_VALID** → keep in the batch. **PARTIAL** → keep, pass its `still_to_do` as that issue's scope.
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR resolved it OR a passing repro proves it) →
     do NOT implement; **auto-close** the issue with the validator's evidence as a closing comment
     (`gh issue close <N> --comment "<validator evidence>"`) — no device ping (reopenable in one
     click) — and DROP it from the batch.
   - **OVERCOME + `overcome_confidence: soft`** → DROP from the batch and ask the user ("looks overcome by
     <evidence> — close it?") with the validator's evidence; act on their answer (close, or run it solo).
   - **UNCLEAR** → DROP from the batch and ask the user, quoting the validator's `premise_check` so nothing
     already-answered is re-asked. **One unclear/overcome member must NOT block the rest of the batch** —
     pull it out and proceed with the surviving STILL_VALID / PARTIAL members.
   - **`cross_stream` and `governing_design` are checked IN ADDITION to `verdict:`** — the validator emits
     them ALWAYS, independent of STILL_VALID/OVERCOME/etc, so a `conflict` in EITHER field DROPS the member
     even when `verdict: STILL_VALID` (a ticket valid against merged code can still collide with unmerged /
     foreign work — that is the whole point). Then:
   - **`cross_stream: conflict`** (multi-stream repo — another stream is actively working an overlapping
     domain, or a foreign in-flight branch/PR overlaps the same files) → DROP from the batch, do NOT
     dispatch: cross-link the overlapping stream/PR/branch on the ticket (`gh issue comment <N>`) and WAIT —
     it re-enters a later batch once the overlap clears. Same drop-from-batch shape as OVERCOME/UNCLEAR;
     one conflicted member never blocks the rest.
   - **`governing_design: conflict`** (the ticket contradicts a frozen governing decision) → DROP from the
     batch and ask the user — a frozen-decision conflict is a genuine design decision, never a silent pick.
     A `governing_design: follows` is NOT a drop: keep the member and carry that governing decision # into
     the Step 1c design grounding so the worker's design comment cites it instead of re-deciding.
   (Hybrid close policy: auto-close ONLY clear-cut hard-overcome; everything uncertain goes to the user.)
   After validation, the batch = the surviving STILL_VALID / PARTIAL members. This stops the recurring
   failure (working / re-asking on an already-overcome ticket).
1c. **DESIGN-TRIAGE — classify each surviving batch member BEFORE any worker sees it** (#414, SOTA
   architecture: restores the whole-repo, multi-approach design depth the owner reported degrading
   to a per-ticket, one-paragraph tunnel once autopilot took over — "nikto nedržal celok"). For
   every member that passed 1b, classify trivial vs design-heavy by REUSING `model-awareness.md`'s
   own HARD-task criteria (architectural / cross-cutting / ambiguous-design / a prior worker
   already failed on it) PLUS the ONE framework-first trigger `architecture-first.md` names (a NEW
   service, CLI, daemon, or long-lived component) — extending that single taxonomy, never
   inventing a second, parallel one. TRIVIAL members skip this sub-step entirely — no
   `Plan` dispatch, no extra cost, same one-paragraph design comment as today. For each
   DESIGN-HEAVY member: run `python3 ~/devel/airuleset/airuleset.py fable-gate` ONCE for the whole
   batch (the gate guards EVERY automatic Fable dispatch — worker-model escalation reuses the SAME
   gate result, see the Model bullet in Step 2). Gate OPEN → dispatch ONE read-only
   `subagent_type: "Plan"` agent per member at `model: "fable"`; gate CLOSED → do NOT spend a new
   gated dispatch (omitting the override would inherit the Fable main — exactly what a CLOSED gate
   says there is no headroom for): hold the design synthesis in the main session itself (the
   Fable-MAIN-at-CLOSED carve-out, `model-awareness.md`), grounding it via cheap read-only
   collection — never the banned `opus` alias, never sonnet for judgment. The (gate-OPEN) `Plan`
   dispatch asks
   for 2-3 candidate architectural approaches with trade-offs and a recommendation, grounded in a
   WHOLE-REPO view. Post the `Plan` agent's synthesis to the ticket via `gh issue comment <N>`
   IMMEDIATELY (`durable-decisions-to-tickets.md` — a design living only in this session dies at the
   next compaction), and embed a tight summary of it in that member's worker dispatch prompt as
   grounding. The worker's own CYCLE step 2 design comment (`agents/autopilot-worker.md`) still
   writes the final `Triage:` line + `Architektúra:` section + (for non-trivial) the 2-3 approaches
   itself — now grounded in the supervisor's synthesis instead of inventing the architecture solo
   mid-implementation. A genuine design FORK the synthesis cannot settle goes to the user as a
   `❓ ASKED` ask-and-continue question the moment it surfaces — never a silent pick, in either
   direction.
2. **Dispatch the ROUND — one in-session BACKGROUND `autopilot-worker` PER assembled batch, each
   `isolation: "worktree"`, all fired in the SAME message (multiple Agent tool_use blocks — this
   is what makes them run concurrently rather than one-after-another).** For each batch:
   `subagent_type: autopilot-worker`, **`run_in_background: true`**, **`isolation: "worktree"`**
   (the default; omit it only for the documented serial fallback above) — this keeps your main
   session FREE + thin while every worker runs, each worker stays VISIBLE in the agent strip, and
   (per CC's 2026-W26 change) its prompts still reach you. prompt = `Work issues #A #B #C in
   <repo> as ONE bundled PR (Closes all). You are running in an isolated git worktree — see
   agents/autopilot-worker.md's worktree-awareness section: never touch the shared tree, never
   push/install, never fire your own run-card — return your branch name + worktree path instead.`
   (or `Work issue #<N> in <repo>; isolated worktree — work ONLY in your worktree dir, never the
   shared main checkout.` for a solo batch) plus any repo-specific note. **LEAD with the worktree,
   never a bare `Repo <main path>`** — a dispatch prompt that names the main checkout path as
   "context" is exactly what made a worktree worker `cd` into it and edit the shared tree (#496,
   worker #433 step 12); `block-foreign-airuleset-write.sh` rule B now hard-blocks that write, but
   the prompt must not invite it in the first place. ONE PR per
   INTEGRATION CYCLE (not per worker), ONE CI cycle per integration — see the repo-flow policy
   above for exactly which branch each worker's worktree branch integrates into.
   - **Compute AND CREATE this batch's own scratch subdirectory BEFORE dispatching, then STATE it
     in the prompt as a fact (#435, #432's own follow-up)** — never leave the worker to compute
     its own. `mkdir -p <scratchpad>/issues-<A>[-<B>-<C>...]` (the batch's own issue numbers,
     sorted, hyphen-joined — deterministic and known BEFORE the `Agent` call ever fires, since you
     already assembled every batch's issue set back in Step 1b/1c; collision-free by construction,
     since no two batches in the SAME round share an issue), then append `Your scratchpad
     subdirectory for this dispatch (already created): <that path> — use it for every
     temp/body/commit-message file; never the scratchpad's top level.` to the prompt text
     verbatim. This reduces the sibling-collision hazard from N independent prose-followers (every
     worker computing its own path) down to ONE (the supervisor, once per round, with a safe
     worker-side fallback below) — it is still a prose-followed step, not a mechanically
     enforced one (no hook exists here, per the repo's FREEZE on new hooks/watchdog jobs) —
     `agents/autopilot-worker.md`'s WORKTREE AWARENESS section still carries the worker-side
     fallback (compute its own `agent-<worktree-id>` path) for a dispatch that doesn't state one.
   - **Include the Step 1b validator verdict in the dispatch prompt when you ran it** (`Validator:
     STILL_VALID — <one-line note>` per member) — it saves the worker re-deriving what you already
     found. This is a courtesy, not the enforcement mechanism (#213): even when Step 1b was skipped
     for this dispatch (a notification-driven mid-run nudge, a long `/goal` loop that drifted), the
     worker's OWN Step 0 now posts its own validation evidence per issue as a durable `gh issue
     comment`, mechanically checked at its SubagentStop (`design_gate.py`) — so validation coverage
     no longer depends on your Step 1b prose actually having run for this specific dispatch.
   - **Model — airuleset repo: Fable-MAJORITY; other repos: Fable for every judgment-content
     ticket; Opus 4.8 the routine fallback** (`model-awareness.md` ACTIVE policy 2026-08-25; Opus 5
     is BANNED — never any `opus`-aliased dispatch, never sonnet on anything complex). The
     `autopilot-worker` frontmatter pins `model: claude-opus-4-8` — that pin is the FALLBACK tier,
     reached by dispatching AS-IS (no `model` param). **On the airuleset repo:** run
     `python3 ~/devel/airuleset/airuleset.py fable-gate` ONCE per round;
     **gate OPEN (exit 0) → dispatch `model: fable` for EVERY worker; gate CLOSED (exit 1) →
     dispatch AS-IS (the frontmatter's `claude-opus-4-8`).** **On other repos:** apply the
     JUDGMENT-CONTENT test per ticket (non-trivial implementation, review of a non-trivial change,
     hard debug, design/synthesis — when unsure, it QUALIFIES; the tie-break is deliberately
     REVERSED from the retired 2026-08-14 HARD-only policy, whose "unsure → not hard" produced
     zero Fable dispatches ever): a qualifying ticket → gate ONCE for the ticket/batch → OPEN →
     `model: fable`; CLOSED, or a genuinely routine/mechanical-ish ticket (one obvious shape, zero
     design decisions) → AS-IS on `claude-opus-4-8`. Honest application lands near the owner's
     ≈50% Fable share of subagent tasks — a day of dispatches far below that means the test is
     being applied too strictly, never that the backlog is special.
     Never dispatch an automatic `model: fable` without the gate check. You (the main session)
     re-verify every line of the worker's evidence block regardless of its model.
   - **Authority rides the dispatch.** Include the resolved profile in every worker prompt
     (`Authority profile: <profile>` + what "done" means for it). branch-merge: the worker's PR
     targets and merges into the INTEGRATION branch (develop unless the project CLAUDE.md names
     another), THEN posts the SAME `READY-FOR-REVIEW:` hand-off comment fork-no-merge uses (never
     staging/main, never deploy, never a self-close — #349: a merge into the integration branch
     does NOT auto-close the ticket, and skipping the hand-off comment leaves it invisible to
     `/process-subdev`'s queue). fork-no-merge: the worker ends
     at fork-branch push + local verification green + the hand-off COMMENT `READY-FOR-REVIEW:
     branch <name> — <test/lint evidence>` (the PRIMARY signal — it works at read role; a
     fork-derived collaborator often CANNOT add labels, #17) + best-effort `gh issue edit <N>
     --add-label ready-for-review` (ignore a 403 — never required) — it must NEVER open
     or merge a PR and never close the issue itself; the per-ticket Discord card fires at THIS
     hand-off point (`--achieved "... pripravené na review"`) for BOTH reduced-authority profiles.
     Step 4 verification then checks the PROFILE's done-point — full: merge to main + deploy;
     branch-merge: PR merged into integration AND the `READY-FOR-REVIEW:` comment present;
     fork-no-merge: the `READY-FOR-REVIEW:` comment present — NEVER a merge to main for either
     reduced profile, and NEVER the ticket closed by the worker itself.
   - **Every dispatch RETURNS IMMEDIATELY** (background) — do NOT block waiting on any of them. End
     the turn `⏳ WORKING`; ANY worker returning RE-INVOKES this loop, and on each re-invocation you
     BOTH refill lanes to saturation (continuous refill, above) AND integrate any ready branches
     (Step 4) under the integration mutex — never waiting for the whole fleet to finish first.
   - **Integration mutex (hard) — the #8 cross-session lock now guards INTEGRATION ONLY, never
     dispatch (#456; narrowed from the old round-level lock).** DISPATCH is NEVER gated: continuous
     refill (above) fires new lanes whenever backlog remains, and N lanes running concurrently in
     THIS session is exactly the point — there is no "do nothing while a worker runs" check any
     more. The ONE thing serialized is the merge→gates→push INTEGRATION cycle: acquire the mutex
     immediately BEFORE each integration cycle (Step 4) — `python3
     ~/devel/airuleset/airuleset.py autopilot-lock acquire --repo <repo path>` (exit 0 = acquired,
     do the merge→gates→push; exit 1 = a DIFFERENT live session is integrating this repo right now
     — do NOT integrate this turn, keep DISPATCHING new lanes and re-check next turn) — and
     **release it the moment that cycle's push has landed** (`autopilot-lock release --repo <repo
     path>`), so another session's integration can proceed. This mutex is what prevents two sessions
     running a merge/push on the SAME repo at the SAME instant (the proven camera-box #495 and
     #499/#500-vs-#505 collision). In the serial fallback the SUPERVISOR still acquires the mutex
     before dispatching the single worker and releases it once that worker's own PR merge has landed
     — one worker at a time, so the broader lock span carries no decay. A crashed holder never
     wedges the mutex — a dead holder's lock is auto-stolen by the NEXT `acquire`, logged to
     `audits/autopilot-lock-steals.log`, as a backstop.
3. The worker re-validates each batched issue is still real (`verify-issue-still-valid.md` — defense
   in depth on top of 1b), then runs ONE cycle for the whole batch on one `dev` branch: version bump
   → per-issue TDD (each bug RED→GREEN, each member committed with its own `Closes #<n>`) → ONE push
   → ONE CI → ONE PR whose body `Closes` every member → merge per `pr-merge-policy.md` → deploy
   verify. It **asks you directly** on any genuine design / scope / authorization call — but FIRST it
   runs the ownership gate (`ask-before-assuming.md`): a question goes to you ONLY if it is CONCEPTUAL
   (what to build / ambiguous intent / a product decision you have a stake in / irreversible), NEVER a
   TECHNICAL detail the worker should just decide (placement of a diagnostic element, which corner, a
   size, a default, layout of a debug overlay). Asking "súhlasíš s týmto rozmiestnením QR kódov?" is a
   banned over-ask — the worker decides that and proceeds. Answer the conceptual ones; the worker
   continues. **A question is a conversation, NOT an abandoned issue.** If a member turns
   out to violate the gate mid-flight (schema/API/security/cross-cut discovered), the worker DROPS it
   from this PR (leaves its issue open) and finishes the rest — the loop re-dispatches the dropped one
   solo later.

   > **Worktree/fleet mode — the worker's cycle stops at its OWN branch; the supervisor's Step 4
   > integrates.** In `isolation: "worktree"` mode a worker does version bump → per-issue TDD
   > (RED→GREEN, each member committed with its own `Closes #<n>`) → local `/review` +
   > `/requesting-code-review` + the local test suite, ALL on its OWN worktree branch — via ONE
   > self-contained fresh-context `general-purpose` review dispatch per `agents/autopilot-worker.md`
   > CYCLE step 6, NEVER the built-in review/code-review Skill (#363) — then
   > RETURNS (branch name + worktree path in its evidence block). It does **NOT** push to the
   > INTEGRATION flow, does
   > **NOT** open or merge the PR, does **NOT** deploy, and does **NOT** fire its own run-card —
   > it cannot: it never sees the final merged/deployed state, because that only exists
   > after the supervisor integrates the worker's branch under the integration mutex (Step 4 below
   > does the merge → gates → push → deploy for each returned branch). (The worker DOES make ONE
   > push: a durability BACKUP of its branch to `refs/autopilot-wip/<branch>` after each commit,
   > #503 — CI-neutral, integrates nothing, deleted by Step 4 after the branch merges.) In the
   > documented serial
   > fallback (no `isolation:`), the
   > worker's full self-contained cycle above — push → PR → merge → deploy → its own run-card —
   > is UNCHANGED, exactly as it always was.

   > **Prefer durable-state resumption over `SendMessage` for a worker that has already ended.**
   > `SendMessage` (the subagent-continuation tool, loadable via `ToolSearch` on current builds, no
   > `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` flag required — airuleset #299) can genuinely redirect a
   > STILL-RUNNING or RESUMABLE background worker mid-task (even one that stopped on a transient API
   > error), but it goes nowhere against one that has genuinely ended and cannot resume — and you
   > cannot always tell which case you're in from the outside. When in doubt, a worker that stopped
   > invoking you is presumed dead, not paused. Do **not** narrate SendMessage mechanics
   > either way ("SendMessage isn't available here, dispatching a fresh worker", "SendMessage worked,
   > redirecting it") — just dispatch a fresh background `autopilot-worker` for the issue and let it
   > RESUME from durable state: the existing `dev` branch, the open PR, and the issue's current state.
   > It continues from there instead of redoing version-bump→RED. The per-ticket Discord card is
   > deduped on repo-name#issue, so a fresh worker re-dispatched for the same issue does NOT
   > double-post its card. A worker ending mid-issue (turn boundary, error, your answer to its
   > question) is recovered by ONE fresh dispatch with the resume context in the prompt — silently,
   > never by narrating the tooling, never by restarting from scratch. This includes a worker
   > KILLED by a server-side rate limit mid-round (#332) — no different from any other API-error
   > death: the same "presumed dead, dispatch fresh, resume from durable state" rule applies.

   > **Worktree/fleet mode: a dead worker's branch is NOT self-discovering — name it explicitly
   > (#332).** A fresh replacement worker gets its OWN new `isolation: "worktree"` checkout and has
   > no way to know a previous attempt ever existed unless you tell it — the resume text above
   > ("the existing `dev` branch") only describes the serial/shared-tree case. Before re-dispatching,
   > find the SPECIFIC dead worker's branch — `git branch --list 'worktree-agent-*'` alone is NOT
   > enough: a live repo routinely carries dozens of stray branches from past rounds with no issue
   > number in the name, so do not guess from that bare list alone — naming the WRONG branch
   > re-dispatches on top of a different, possibly still-live worker's work. Two reliable ways to
   > find the RIGHT one: (1) when no custom worktree `name` was passed, the branch is
   > DETERMINISTIC — `EnterWorktree`'s auto-generated branch is `worktree-agent-<agentId>` for
   > worktree directory `agent-<agentId>`, so the dead worker's own dispatch `agentId` (from its
   > dispatch record, or the task notification that reported its death) names its branch directly;
   > (2) otherwise, run `git log --all --oneline -20 --grep '#<N>'` for commits referencing the
   > issue, then `git branch --contains <sha>` on each hit to resolve the OWNING branch —
   > `git log` alone never prints branch names. A worktree's branch is a normal ref shared via the
   > ONE `.git`, so it survives even after `git worktree remove` cleans up the directory. If real
   > commits exist on it, name that branch explicitly in the fresh worker's dispatch prompt
   > (`Resume from existing branch <name> — it already has: <one-line summary of what's
   > committed>`) so it continues from that tip instead of starting a fresh RED→GREEN cycle from
   > scratch — nothing lost, nothing duplicated. **If even the LOCAL branch ref is gone too — a
   > `.git` loss / branch deletion / box re-clone, not just a removed worktree directory (#503 case
   > 2) — the finished commits still exist on origin at the worker's durability backup
   > `refs/autopilot-wip/<branch>`** (the worker pushed it after each commit exactly for this): run
   > `git fetch origin 'refs/autopilot-wip/<branch>:refs/heads/<branch>'` to restore the branch
   > locally, then integrate it normally. This is the recovery the origin backup exists for; without
   > it, a worker that died before the supervisor merged (and whose local ref was then lost) would
   > have left nothing to recover. If no commits exist yet (the worker died before
   > its first commit — the common shape for a rate-limit kill during Step 0/1b, before any code
   > was written), there is nothing to resume: dispatch fresh in a NEW worktree exactly as
   > documented above, no special handling needed. Either way the round is NOT blocked waiting on
   > the dead worker indefinitely — once its death is confirmed (a fatal-API-error task
   > notification, or a bounded liveness check per `verify-launched-work-liveness.md` rather than
   > an unbounded silent wait), re-dispatch that ONE issue's fresh worker and let the
   > OTHER, still-healthy workers keep going — Step 4 integration simply waits for THAT ONE
   > issue's own replacement branch, while every OTHER ready branch integrates without being
   > held for it.

   > **Multi-stage / long pipelines (e.g. a 3-branch `develop→staging→main` flow) — YOU own the CI
   > waits, not the worker.** A single worker cannot safely hold an hour-plus of successive CI waits:
   > a subagent that `run_in_background`-waits and ends its turn TERMINATES (the dominant worker
   > failure — its background task re-invokes YOU, not the dead worker), a foreground wait caps at
   > 10 min/call and bloats the worker's context, and the long lifetime is exposed to api-errors the
   > whole time. So for such repos the worker is BOUNDED PER STAGE — it does its stage's work →
   > pushes / opens the PR → reports the CI run-id + current stage in its evidence block → RETURNS.
   > YOU (the supervisor) then own the wait: poll the reported run-id with a `run_in_background`
   > bounded poll (you ARE the long-lived component — `run_in_background` re-invokes you and
   > `--resume` continues you, exactly why the wait is safe here and fatal inside a subagent), and
   > when CI is green dispatch the next short-lived worker for the next promotion (develop→staging,
   > staging→main, merge→deploy-verify). The integration mutex still serializes each promotion
   > cycle (one merge/push at a time per repo, Step 3.2), while dispatch keeps refilling lanes;
   > each worker's lifetime just shrinks. This is the SANCTIONED pattern — not an improvisation. For a
   > plain 2-branch single-CI repo it isn't needed: the worker waits FOREGROUND through the one short
   > CI and runs the whole cycle itself.
4. **As workers return with ready branches, integrate them under the integration mutex — do NOT
   wait for the whole fleet** (#456; Step 3.2's mutex serializes each merge→gates→push cycle, and
   continuous dispatch keeps refilling lanes meanwhile). For each returned worker being integrated,
   independently verify its evidence block from primary sources (never trust the claim). Read its
   `dropped:` and `obsolete_closed:` lines and compute that worker's **SURVIVING set** = its batch
   members MINUS dropped MINUS obsolete-closed —
   a dropped / obsolete member is **NOT a verify failure**. This integration cycle's surviving set
   is the union across the workers integrated in it (a straggler still running joins the NEXT cycle).

   > **Serial fallback (no `isolation:`): unchanged, per-batch, exactly as before.** The worker
   > already pushed, opened, and merged its OWN PR — verify it directly:
   > - `gh pr view <PR> --json state,mergedAt,mergeCommit,closingIssuesReferences` — confirm EVERY
   >   surviving member is in `closingIssuesReferences`
   > - `gh run list -b main -L 1 --json conclusion`
   > - deployed version read from the live target (if there is a deploy)
   > - `gh issue view <N> --json state` for EACH member: surviving → `closed`; obsolete-closed →
   >   `closed`; dropped → `open` is CORRECT
   > Confirmed → one line per surviving issue to `docs/autopilot-log.md`, then the run-card /
   > lock-release paragraphs below — the per-ticket Discord completion card is fired by the WORKER
   > itself in this mode, directly at merge (`notify --run-card`), NOT by the supervisor; just
   > confirm the worker carded each merged member.

   > **Fleet/worktree mode: INTEGRATION — serial, supervisor-owned, one merge→test→push CYCLE at a
   > time under the #8 integration mutex, repeated as branches return (never held for the whole
   > fleet — #456).** This is what replaces the worker's own push→PR→merge→deploy for each returned
   > batch. Each integration cycle acquires the mutex, integrates whatever branches are READY at
   > that moment (never waiting for stragglers), and releases it; DISPATCH keeps refilling lanes
   > independently the whole time:
   > 1. For each worker (any order), spot-check its evidence against its own worktree: `git -C
   >    <worktree-path> log --oneline` / `git -C <worktree-path> diff <base>` — confirm the
   >    claimed commits, RED/GREEN test pairs, and clean `/review` + `/requesting-code-review`
   >    results genuinely exist on that branch before trusting it enough to merge.
   > 2. Merge each READY worker's branch **`--no-ff`** into the cycle's target — local `main` for a
   >    local-merge repo, local `dev` for a `dev`→`main` PR repo (repo-flow policy above) — ONE AT
   >    A TIME, in a fixed order (e.g. lowest issue number first). Resolve any conflict yourself; a
   >    worktree's branch is a normal ref shared via the ONE `.git`, so it merges cleanly even after
   >    the worktree itself is later removed.
   > 3. Run **ONE full test/CI cycle** against the cycle's merged result — one test per integration
   >    cycle, never one per worker.
   > 4. **ONE push per integration cycle, integrating every branch merged in THIS cycle**:
   >    local-merge repo → one `push` after this cycle's merges + the one green suite; `dev`→`main`
   >    PR repo → open (or update) ONE PR whose body lists `Closes #<n>` for every member merged in
   >    this cycle, then `pr-merge-policy.md`'s normal gate→merge→deploy flow. A later cycle
   >    integrates the branches that have since returned, the same way — never held for the fleet.
   > 5. `git worktree remove` each worker's worktree once its branch is safely merged (or leave it
   >    for salvage per `salvage-before-discarding-work.md` if anything looked wrong — never delete
   >    a worktree whose branch you have not yet confirmed merged). Then delete that worker's origin
   >    durability backup ref (#503): `git push origin --delete refs/autopilot-wip/<branch>`
   >    (best-effort — a missing ref is a harmless no-op; a serial-fallback worker made none). The
   >    worker pushed this CI-neutral backup after each commit so its finished work survived a lost
   >    worktree; once the branch is merged the backup is redundant remote litter, so remove it in
   >    the SAME integration cycle that merged the branch.
   > A worker KILLED mid-round (API error / session limit) leaves a worktree + branch this step
   > never reaches at all (its branch was never merged) — that leak is now cleaned SYSTEMICALLY,
   > not by you: `sweep_stale_worktrees()` (#345) runs fleet-wide, non-fatal, on every
   > `install`/`push` (cadence-gated, at most every few hours), reclaiming only a worktree that is
   > unlocked, has ZERO commits ahead of the repo's own base, and whose tree `git worktree remove`
   > itself confirms is clean — never `--force`, never `main`/`dev`. You do not need to hunt for
   > these by hand; if you want one run immediately (e.g. to reclaim disk before dispatching a big
   > round), `airuleset.py sweep-worktrees [--dry-run]` bypasses the cadence gate on demand.
   > Then run the SAME per-member verification bullets as the serial-fallback box above, against
   > this integration cycle's PR/push instead of a per-worker one, before writing `docs/autopilot-log.md`.
   > **Read back each surviving member's OUTPUT artifact BEFORE firing its card (#450).** When
   > the ticket produced or changed a user-facing output (email content from the DB, a rendered
   > document, live UI values), open the ACTUAL artifact and record CONCRETE observed values —
   > price, currency, order number, heading — never send/delivery/liveness alone (the montalu3
   > 0 € email class: emails "verified" delivered while every price rendered 0 €). This is the
   > worktree-fleet sibling of CYCLE step 8's own read-back: on the DEFAULT dispatch YOU (the
   > supervisor) compose the cards, not the worker, so the observed values must exist HERE. They
   > feed the integration cycle report's mandatory `✅ Výstup:` line (`completion-report.md` — an explicit
   > `n/a — <prečo>` when a member truly has no user-facing output), and each card fires only
   > AFTER this read-back.
   > **Fire the per-ticket run-card yourself, for EACH surviving member, right after this ONE
   > integration lands** — `airuleset.py notify --run-card --repo <owner/name> --issue <N> --goal
   > "<plain goal>" --achieved "<plain what landed>" --version "<deployed version read from the
   > DOM>" --url "<Label=URL where the change shows>"` (never the worker's own call in this mode —
   > it never sees the final deployed state). The card header/format is unchanged (🎫/🎯/✅/📦/🔗),
   > deduped on repo-name#issue exactly as when the worker fires it itself.

   > **Release the integration mutex the instant THIS integration cycle's push has landed:**
   > `python3 ~/devel/airuleset/airuleset.py autopilot-lock release --repo <repo path>` — this frees
   > the repo for another `/autopilot` session's integration `acquire` to succeed, and lets THIS
   > session's own NEXT integration cycle re-acquire it. Hold it only around the merge→gates→push,
   > never across dispatch or the CI wait of unrelated lanes. Release even when a member was
   > partially dropped (Step 3 note) or a worker's evidence looked wrong — the mutex's job is "is an
   > integration in progress", not "did it fully succeed". If a holder never releases (crashed
   > mid-integration), do NOT hand-release from a DIFFERENT campaign — the NEXT `acquire` (this
   > session or another) auto-steals a dead holder's lock (logged to
   > `audits/autopilot-lock-steals.log`), so a stuck mutex self-heals without manual intervention.
5. **Report each COMPLETED INTEGRATION CYCLE as a REAL completion — the ARMED GOAL + any lanes
   still running continue the loop (2026-07-25 revision).** Once an integration cycle's
   verification + integration + the per-member run-cards are done and the mutex is released, end the
   turn with the FULL `## ✅ Work Complete` template (`completion-report.md`) for the members
   integrated in this cycle: audits — `✅ CI: green`,
   `✅ /plan-check: <N>/<N> fulfilled` (RELAYS each worker's own `plan:` field — a per-issue
   self-audit; you never independently re-run plan-check yourself, #215/#216), `✅ /review: clean —
   0 🔴 0 🟡 0 🔵` and `✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵` (you are RELAYING what
   each worker already confirmed locally before its branch was merged (its own PR gate in serial
   mode, its own local run in worktree mode — `agents/autopilot-worker.md`), never re-running the
   review yourself — and neither should the worker have, as a literal `Skill({skill: "review"})`/
   `code-review` invocation either, per #363), `✅ Deploy: <version>`, `✅ Výstup: <observed values>` (RELAYS each worker's Step-4 read-back of its member's real OUTPUT artifact — an explicit `n/a — <prečo>` when a member has no user-facing output; `completion-report.md` blocks a report missing this line)
   — then Goal/What changed in plain language (covering every member integrated in this cycle), the
   🌐 URL(s) from the workers' `--url`, and the PR title/link/merge SHA (this cycle's ONE PR, per the
   repo-flow policy). **On `U > 0`/`W > 0` the report ALSO prints the parked BREAKDOWN** (Step-1
   `--waiting`/`--ops-wait` members + tags, #527), never a bare `U N`.
   Terminating in the marker `message-status-marker.md` prescribes: a genuine
   `✅ DONE: <plain outcome, e.g. "#41+#43+#317 merged -> v1.2.3, CI green">` when no lane is left
   running, or `⏳ WORKING` when this turn still has dispatched lanes in flight (background work IS
   running — never claim idleness). This IS a real completion of the integrated members (merged,
   verified, carded — durable in git/GitHub), not a lie; the signal that MORE work follows is the
   **ARMED GOAL** Claude Code shows in its footer (`◎ /goal`) plus any lanes still running. This genuine `## ✅ Work Complete` boundary is also
   where the supervisor itself should call `airuleset.py compact-request --self` FIRST (before
   writing the report — see `completion-report.md`'s own "Compact at your own boundary" section),
   so the ticket-boundary `/compact` fires PER INTEGRATION CYCLE instead of once for the whole
   backlog — an integrated cycle's durable state already lives in git/GitHub, so it is a safe
   compaction boundary
   every time. (#400, 2026-08-12: `notify-compact-request.sh`, the old passive Stop-hook text-sniff
   this used to describe, is now a permanent no-op — `compact-request --self` is the only mechanism
   left for the supervisor's own turn boundary.) The idle Discord ping is separately guarded while
   the goal stays armed
   (`milestone-notifications.md`) — the run-cards already gave phone visibility for this round, so
   nothing double-pings.
   **Reduced-authority streams (branch-merge / fork-no-merge) carry the SAME Step 5 mandate — never
   silence (#58, the david #2129 incident).** There is no PR-to-main, no merge, no deploy for these
   streams — replace the PR-title/merge-SHA/`✅ Deploy:`/🌐 lines with `completion-report.md`'s
   reduced-authority variant instead: `✅ Lokálne overenie: <tests+lint result>` +
   `✅ Hand-off: READY-FOR-REVIEW komentár na #N (<topic>) + --handoff karta` for BOTH profiles (#349:
   branch-merge posts it too, right after its integration-branch merge — a merge alone does NOT
   close the ticket) + `✅ PR: #M do <integration> zmergnutý <sha>` additionally for branch-merge
   (ends there — ticket stays OPEN for the gatekeeper). The heading + audits + `---` separator +
   Goal/What changed + terminal `✅ DONE:` are IDENTICAL and NON-OPTIONAL regardless of authority —
   a bare `✅ DONE: #N hotové` prose report is still blocked by the same Stop-hook gate
   (`stop-check-prose-violations.sh`).
   This closes the exact gap #2129 hit: Step 5 previously read as merge-shaped only, so a fork-no-merge
   stream might not have recognized it applies to its hand-off turns too.
   **The `/goal` loop's NEXT fire re-enters Step 1 for the next batch** — do NOT chain into Step 1
   within this same turn anymore. Do NOT re-run `/issue-planner`; do NOT hand-type `/compact` yourself
   (the watchdog handles the timing once the pane goes idle).

### Bounce nudge-ack — an injected prompt while the loop runs (ACK it; never work it inline)

A reviewer stream (e.g. gatekeeper `/process-subdev`) may inject a SHORT prompt into the RUNNING
`/goal` session referencing a freshly-filed ticket ("bounce #N filed — tvoj autopilot ho zoberie
ďalší"). The full finding lives ON the ticket (durable — survives compaction); the nudge is only a
wake-up, never the carrier of the work. Handle it in ONE short turn:

1. **ACK in one line** (which ticket, that the lane will take it next).
2. **Ensure the label sticks** — `gh label create prio:bounce --color D93F0B --description
   "Reviewer-injected priority ticket — jumps the autopilot queue" 2>/dev/null || true`, then
   `gh issue edit <N> --add-label prio:bounce` (best-effort — a read-role stream silently accepts
   a 403; the reviewer normally labeled it already).
3. **End the turn `⏳ WORKING`** and let Step 3.1's PRIORITY LANE seed the ticket into the very
   NEXT batch.

**NEVER start working the finding inline in the main session, and NEVER derail/abort the
currently-running batch** — the batch finishes, the bounce goes next through the normal
worker/validator machinery. This is what lets a sub-dev autopilot run 24/7 CONCURRENTLY with a
gatekeeper review stream instead of serializing them.

**When NO `/goal` loop is armed** (the nudge — or a watchdog bounce-backstop prompt — arrives
AFTER a previous run ended, so no next turn will fire): a dead ACK loses the ticket. Instead run a
NOTIFICATION-DRIVEN mini-loop right from the nudge turn: validate the ticket (ticket-validator),
then **dispatch the background `autopilot-worker` for the bounce ticket** exactly as Step 3.2
(same lock, same authority profile in the prompt), end `⏳ WORKING` — the worker's completion
re-invokes you; verify per Step 4, then check for MORE open `prio:bounce` tickets and dispatch the
next, until none remain; finish by re-entering REVIEW-WATCH (reduced authority) or reporting. The
one-shot nudge substitutes for the missing `/goal` engine — never reply "the loop will take it"
when there is no loop.

## Step 4 — When to actually STOP (only these)

- **Backlog empty** (no open non-skip issues) → run the **end-of-run reconciliation sweep (Step 4a)
  FIRST**, then the final completion report (`completion-report.md`).
- **Destructive / prod action** a worker surfaced that needs your approval
  (`no-destructive-remote-actions.md`, `approval-scope.md`).
- **A gate that won't go clean / the same CI failure twice** after a real fix attempt → surface
  it, never bypass (`autonomous-quality-discipline.md`).

A per-issue **design question is NOT a reason to abandon the issue, and NOT a reason to sit
idle-blocked when there is other work — it is a reason to ASK YOU (with a phone PING) the MOMENT the
issue needs it.** The user does NOT watch the terminal 24/7; **the Discord ping is the ONLY way the
question reaches them, so it MUST fire — every time, no exception (waking hours).** A question printed
but never pinged does NOT count as asked, and you may NEVER later stop the loop blaming the user's
silence. **Deliver every question as a SELF-CONTAINED `❓` text marker (NOT a 60-second
`AskUserQuestion` dialog — from a background worker it auto-continues in ~60 s so an away user never
answers; the `❓` marker pings AND waits UNLIMITED). Write it so someone with ZERO terminal context
understands it — which project + what it does, what happened, and EVERY cross-project / cross-ticket
link explained in plain Slovak (`user-questions-slovak.md`); never assume the user read the history or
knows two projects are related.** The SHAPE is hook-enforced (`stop-check-question-quality.sh`): the block opens `**Otázka — projekt …:**` and carries exactly ONE decision per ping — a ticket with several open questions asks them ONE at a time (next one after the previous answer arrives via Discord reply), never a `(1)/(2)/(3)` pile. Handle it BY THE CLOCK:

- **Waking hours — 06:00–23:59 Europe/Bratislava (check `TZ=Europe/Bratislava date +%H` → hour
  `06..23`): ASK NOW — it PINGS — then pick the honest form by whether OTHER work is available:**
  - **Other answer-independent work exists → ASK-AND-CONTINUE (the user's requested model).** Raise
    the question so it pings (`❓ ASKED: <q>` — Slovak, the real decision), track it DURABLY on the
    issue (`gh issue comment <N>` with the question + `gh issue edit <N> --add-label needs-answer`, so
    it is never lost in the scrollback), set THAT issue aside (paused, not abandoned), and move the
    loop to the next answer-independent ticket. End the turn `⏳ WORKING: <what you continue>`. When
    the user answers (any time), resume the paused issue from its DURABLE state (the open branch / PR /
    the `needs-answer` comment) per `subagent-continuation.md`. Give the user a genuine chance (~10
    min) before bulldozing a ticket that hinges on their taste — but do NOT block the whole loop for
    an answer you don't yet need.
  - **Nothing else is workable without the answer → BLOCK.** End the turn `❓ NEEDS YOU` (Slovak, the
    real decision) — it pings, and the `/goal` loop STOPS per its stop-condition (A) (waiting on the
    user is the terminal state; endless re-pokes of a blocked session were the camera-box chat
    wall). When the user answers, resolve THAT ticket first; then, if open non-skip issues remain,
    re-print the /goal line (same block as Step 2) with the conforming start question so the user
    re-arms the loop with one paste. Use the block only when the question truly blocks all
    remaining work.
  Do **NOT** grind on WITHOUT asking (burying the question) — ask-and-continue means you ASKED (pinged
  + tracked) FIRST, then continued. Do **NOT** write `❓ NEEDS YOU` and then move to another ticket
  anyway (that pings "I'm blocked" while you moved on — use `❓ ASKED` + `⏳`).
  - **Re-poked while STILL blocked on the SAME question** (the `/goal` evaluator or a
    task-notification re-fires a turn although nothing changed and the answer hasn't come): reply
    with **EXACTLY ONE LINE — the previous `❓ NEEDS YOU: <q>` line repeated VERBATIM,
    byte-identical.** NOTHING else: no apology, no "stojím a čakám" preamble, and **no re-printed
    question block** — every re-printed wall lands in the user's chat AGAIN (the camera-box chat
    spam, 2026-07-05). The device path dedups the identical line (no re-ping) and the shape gate
    recognizes the repeat (LASTQ match), so the one-liner passes untouched. A REWORDED repeat still
    counts as a new/edited question and is banned. A re-poke is never license to bulldoze the
    pending decision. (Stop-condition (A) in the /goal line means the evaluator should STOP instead
    of re-poking at all — the one-line reply is the damage bound if it misfires anyway.)
- **Sleep window — 00:00–05:59 Europe/Bratislava (hour `00..05`): defer ONLY while other work
  exists; a NECESSARY question still pings.** Night does NOT stop the work, and it does NOT silence
  a question the loop cannot proceed without (the user: "nezakázal som v noci robiť, len obmedziť
  otázkovanie ak je čo iné robiť; ak je otázka nutná, treba ju položiť"). Two cases:
  - **Other answer-independent work exists → DEFER + keep working.** Queue the question (label
    `needs-decision`, leave the issue open), do NOT emit `❓ ASKED` (nothing pings), grind the rest,
    end `⏳ WORKING`. Raise the queued questions as ONE `❓ NEEDS YOU` once the window ends (after
    06:00) or the user is next active. Rig / prod / hardware tickets are workable at night like any
    other (`approval-scope.md` — never gate on off-air windows; night is not one).
  - **Nothing else is workable without an answer → the question is NECESSARY: ask it NOW, even at
    night.** End the turn with the full `❓ NEEDS YOU` block exactly as in waking hours — it pings,
    and the `/goal` loop STOPS per stop-condition (A). NEVER idle-park instead: a spin of
    `⏳ WORKING: sleep window — parked till 06:00` turns (no work done, no question asked) under an
    armed `/goal` re-pokes every ~40 s into the 9-consecutive-block cap and floods the chat — the
    camera-box overnight wall, 2026-07-06. Blocked = ask; asked = the loop stops cleanly.

"Nothing is hands-off" is **NOT a stop** — work the tickets; when one needs your decision, ASK it NOW
(ping) and either continue other work (ask-and-continue) or block if nothing else is workable.
Finishing a merge is **NOT a stop** — pick the next issue. An **unanswered pinged question is NEVER a
reason to stop the loop or blame the user** — it just waits (tracked on its issue, `needs-answer`)
while you work everything else; you reach the end only when the WHOLE backlog is either merged or
blocked on a pinged question, and even then you re-surface those as ONE `❓ NEEDS YOU`, never as a
reproach.

**BANNED rationalizations — both directions, kill both:**
- **Burying:** "there's other workable work so I'll move to the next ticket and ask later",
  "**pokračujem na ďalšom tickete, otázku položím neskôr**", "medzitým robím iné" — **WRONG when you
  did NOT ask+ping+track first.** Continuing is allowed ONLY after the question pinged the phone AND
  was recorded on the issue (`❓ ASKED` + `needs-answer` comment). Moving to a DIFFERENT ticket to
  AVOID asking is the banned defer — the user gets NO question and the important ticket never gets
  solved.
- **Reproach / false-stop:** "the loop is waiting on your answers so I'm stopping", "**skončil som,
  lebo tickety čakajú na tvoje odpovede**", surfacing hours-old questions the user was never pinged
  about as the reason for stopping — **WRONG.** Every question pings when raised; unanswered ones wait
  without blame while you do other work. (Post-MERGE "pick the next issue" is correct and DIFFERENT —
  that is continuing after a ticket is DONE, not skipping a ticket that needs your answer.)

## Step 4a — End-of-run reconciliation sweep (when the backlog goes empty, BEFORE the final report)

When the workable backlog empties, the run has just changed a lot of code while the context is still
fresh. Reconcile the WHOLE tracker NOW — **including the `autopilot-skip` issues** — so no ticket
lingers contradicting what the run achieved (`verify-issue-still-valid.md`, `no-dropped-work.md`).
This is a ONE-TIME sweep at completion, not a per-issue step; it runs once, then the report.

1. **List EVERY still-open issue, skips INCLUDED:** `gh issue list --state open -L 200` (do NOT filter
   out `autopilot-skip` here — the whole point is to re-examine them too). Gather what the run did from
   `docs/autopilot-log.md` (PRs merged this run + their `Closes #N`) so each validation has that context.
2. **Validate EACH remaining open issue against current reality** — dispatch the read-only
   **`ticket-validator`** (`subagent_type: ticket-validator`, prompt `Validate issue #<N> in <repo>;
   this run merged: <PR list + topics>`). They are independent → validate in parallel. Branch PER issue
   on its verdict (same hybrid close policy as Step 1b):
   - **OVERCOME + `overcome_confidence: hard`** (a concrete merged PR this run resolved it, OR a passing
     repro proves it) → **auto-close** with the validator's evidence as the closing comment
     (`gh issue close <N> --comment "<evidence — overcome by PR #M this run>"`) — reopenable in one
     click. This is the core ask: a skip / open ticket the run made moot gets closed.
   - **PARTIAL** (the run did some of it; real work remains) → do NOT close. **Rescope it non-
     destructively:** `gh issue comment <N> --body "Reconciled at /autopilot end: PR #M did <X>;
     remaining scope is <Y>."` so the ticket reflects reality. Leave it open (and, if it was an
     `autopilot-skip`, note the remaining scope on it — the user re-weighs skips at the next start).
   - **OVERCOME + `soft` / UNCLEAR** → do NOT auto-close — **ask the user** with the validator's
     evidence (`#N looks overcome by PR #M — close, rescope, or keep?`), act on the answer.
   - **STILL_VALID** → leave it as-is (a deliberately-skipped, still-relevant ticket stays skipped).
3. **NEVER prod/hardware-classify** any ticket in this sweep, and never close a skip just because it
   touches prod/hardware (`approval-scope.md`) — closure is driven ONLY by the validator's overcome
   evidence, never by a ticket's subject. Closing/commenting is non-destructive tracking → no approval
   needed for hard-overcome; everything uncertain goes to the user.
4. Then write the final completion report — listing what the sweep closed / rescoped / asked about.

## Cross-stream protocol — gatekeeper ↔ sub-dev (CANONICAL — see `references/cross-stream-protocol.md`)

Multi-stream development on one project (gatekeeper reviews + merges to prod; sub-devs deliver
slices) follows the CANONICAL protocol in `skills/autopilot/references/cross-stream-protocol.md`
(#426 — extracted verbatim; only relevant when a ticket carries `prio:bounce` /
`needs-gatekeeper` / `ready-for-review`, or a repo-local command like odoo-erp's
`/process-subdev` needs to conform to it). Read it in full the moment you hit any of those —
never define your own variant. The rule numbers referenced elsewhere in THIS file (e.g. "cross-
stream protocol rule 4/7") are that reference file's own numbered items 1–7.

## Watching & steering, Context hygiene & resume — see `references/session-mechanics.md`

FYI mechanics on HOW the loop runs day to day — why the worker is background not foreground, the
agent-strip visibility, and how GitHub-as-state + `docs/autopilot-log.md` keep the main session
thin across `--resume` — moved verbatim to `skills/autopilot/references/session-mechanics.md`
(#426). Not operative per-ticket instructions; read on demand.

## Guardrails (hard — never relax)

- **Serial INTEGRATION per repo, CONTINUOUS parallel dispatch by default (#317/#456, 2026-08-08).**
  Only the merge→gates→push INTEGRATION cycle is serialized — the integration mutex (Step 3.2)
  allows ONE integration in flight per repo at a time across ALL sessions, supervisor-owned, never
  simultaneous. DISPATCH, by contrast, is CONTINUOUS and NOT serialized: it keeps refilling a FLEET
  of `isolation: "worktree"`-isolated worker lanes running IN PARALLEL — one per solo ticket or
  bundle-safe batch, saturating to every workable unit, bounded ONLY by real resource signals
  (rate-limit errors, box memory, CC max-subagents), never a fixed cap, and never blocked by the
  integration mutex. The collision risk that used to force one-worker-at-a-time DISPATCH was two
  workers sharing the SAME `dev` tree; a worktree gives each its own checkout sharing only `.git`,
  so that risk is gone for dispatch. Falling back to the fully-serial single-worker shape (no
  `isolation:`) is still correct whenever worktree isolation is unavailable or a lane's candidates
  overlap too heavily to safely parallelize (Step 3). Two INDEPENDENT levers cut cost, and neither
  replaces the other: BUNDLING many issues into ONE worker's single PR/CI cycle (Step 3.1) cuts CI
  cost per worker; CONTINUOUS FLEET DISPATCH (Step 3.2) cuts wall-clock by keeping lanes saturated.
  (Different repos can each run their own `/autopilot`
  independently, exactly as before.)
- **Independent verification is mandatory** — a worker's "merged and deployed" counts only after
  the main loop re-reads PR/CI/version/issue state from primary sources (premature-done is the #1
  long-running-agent failure).
- **Gates are absolute** — no `--admin`, no bypass, no merge-despite (`autonomous-quality-discipline.md`).
