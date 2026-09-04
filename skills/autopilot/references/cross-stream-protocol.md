# Autopilot — Cross-stream protocol reference

> Extracted verbatim from `skills/autopilot/SKILL.md` (#426, the file's own
> #414 ~1000-line/file budget) — read this when a ticket in your backlog
> actually involves gatekeeper ↔ sub-dev multi-stream dynamics (a
> `prio:bounce` / `needs-gatekeeper` / `ready-for-review` label, or a
> repo-local command like odoo-erp's `/process-subdev`). Not every repo or
> every ticket needs this section; `SKILL.md` Step 1/Step 3 point here by
> rule number when it applies. Content below is UNCHANGED from its prior
> in-file form — only its location moved.

## Cross-stream protocol — gatekeeper ↔ sub-dev (CANONICAL — airuleset owns this)

Multi-stream development on one project (gatekeeper reviews + merges to prod; sub-devs deliver
slices) follows THIS protocol. Repo-local commands (e.g. odoo-erp `/process-subdev`) MUST conform
to it — they never define their own variant. Every sub-dev hand-off review verdict runs FABLE through the budget gate
(CLOSED → claude-opus-4-6, never lower and never the banned `opus` alias; grounding on cheap
stages; the tier never degrades across re-reviews) — canonical tiering in the
`process-subdev` skill, step 3.1b.
Origin: odoo-erp #1599 bounce lane + the 2026-07-19
stall incident (both sides' loops ended mid-ping-pong; 4 re-handed-off tickets sat with no
re-review and no pickup).

1. **All work travels as TICKETS, never as prompts.** Findings, bounces, re-handoffs — full
   content ON the ticket (durable, survives compaction, readable by a fresh worker). A tmux
   message is at most a 1–2 line NUDGE naming the ticket — **NEVER a payload prompt into a working session** (an interrupt mid-task derails the loop; the user's standing rule). A BUSY pane gets
   NOTHING — a running loop re-queries the backlog each turn, so the `prio:bounce` label alone IS
   the insertion.
2. **Priority = labels, picked up between tasks.** `prio:bounce` (+ `stream:<name>`) jumps the
   queue at the NEXT batch seed (Step 3.1) — never preempts a running batch. High-priority work is
   inserted by labeling, never by interrupting. **`prio:bounce` is a PROTOCOL label, never a generic
   "priority" marker** — it means exactly one thing: the gatekeeper returned this ticket to the
   **sub-dev** with findings that need a fix. The sub-dev fixes it and re-hands-off via
   `ready-for-review`; **while THAT hand-off is open the full-authority loop HOLDS** in
   review-watch (stay alive, re-check hourly, never end the loop), so `core-quals --count`
   legitimately never reaching 0 in that state is CORRECT and is **not the never-stops failure**
   #181 rejected. A BARE open `prio:bounce` with no hand-off alongside it is the sub-dev's own work
   in progress and does NOT hold the gatekeeper's loop (#307, 2026-08-07: `core-quals` no longer
   unions the bare label in — it belongs to the SUB-DEV, not the gatekeeper). Never add it to a
   ticket outside this flow to mean "do this first" (the restreamer #337 incident, 2026-07-26: the
   label alone on an unrelated repo's own ticket fired a confusing cross-project nudge — the
   api-watchdog's bounce backstop (job 8) now also scopes to repos that actually participate in
   this flow, but the label itself must still never be repurposed).
3. **Label lifecycle — who removes `prio:bounce`:** the sub-dev's worker clears it at its
   done-point (merge / re-ready hand-off comment), best-effort — but david's **read-only role cannot remove labels**, so the REPO automation (the `subdev-handoff-label.yml` workflow) must
   auto-remove `prio:bounce` when the re-ready comment lands, and the gatekeeper clears any
   leftover at re-review. A stale `prio:bounce` after a re-ready comment is a repo-automation gap,
   not a sub-dev failure. **Until the re-ready `ready-for-review` HAND-OFF is cleared, the
   full-authority loop HOLDS** — review-watch, never end the loop — which is why `core-quals`
   counts `ready-for-review` and why a count that cannot reach 0 while a re-handed-off sub-dev
   ticket is open is CORRECT, **not the never-stops failure** (#181 round 4 reconciled this with
   rule 2 and with airuleset.py's own `MAINTAINER_ACTION_LABELS` comment, which had described the
   re-review as the gatekeeper's ball to pick up NOW). `prio:bounce` ITSELF is no longer part of
   that count (#307, 2026-08-07) — while the sub-dev is still fixing it (no `ready-for-review` yet),
   that is the SUB-DEV's ball alone and the gatekeeper's loop may correctly stop.
4. **The ping-pong ends only when the ticket is CLOSED (fork streams) / the slice RELEASED to
   main (branch-merge streams).** TWO gatekeeper-side mechanisms cover it, deliberately different
   in scope (#307, 2026-08-07 reconciled this — see rules 2/3): **`/process-subdev`'s own
   per-stream `/goal`** (its own DONE line: "Also not-done while any open `prio:bounce` ticket of
   this stream awaits a sub-dev fix or a re-handoff awaits my re-review") holds through the
   **WHOLE** bounce lifecycle — bare bounce being fixed, then re-hand-off, then re-review — exactly
   as before, UNCHANGED by this ticket; that is what "resumes" review the instant
   `ready-for-review` reappears, and a `/process-subdev` run that ends with ANYTHING outstanding
   for its stream must arm its own review-watch continuation, not terminate. **The FULL
   `/autopilot` loop's own stop-proof (`core-quals`)** is a SEPARATE, broader mechanism (the
   gatekeeper's whole-repo obligation set, not one stream's queue) — it holds ONLY while a
   `ready-for-review`/`needs-gatekeeper` HAND-OFF is open, because that is the only state where
   the full loop itself has an action pending; while a sub-dev is still fixing a bare
   `prio:bounce` (no hand-off yet), the FULL loop's own obligation set is correctly empty of it
   and that loop may stop — the ticket is never actually unwatched, because `/process-subdev`'s
   loop (above) already holds it. **On the gatekeeper's side the FULL-loop half is now MECHANICAL,
   not a prose guarantee** (#181 round 3): `airuleset.py core-quals` — the FULL `/goal` template's
   own stop-proof and backlog listing — counts every open `needs-gatekeeper` / `ready-for-review`
   ticket regardless of its `stream:<user>` label, so (B) cannot reach 0 while any of them is
   open. The pre-round-2 whole-repo proof upheld this only as a side effect of being whole-repo;
   round 2's core-only proof dropped it, and a gatekeeper could have stopped with a re-handed-off
   ticket's ball in its own court.
5. **Machine-local backstop:** the api-watchdog (job 8) independently sweeps every ~30 min — an
   idle claude pane in a repo with open `prio:bounce` gets a nudge (the nudge-ack step — part of
   SKILL.md's own Step 3, not this reference file — handles it, loop or no loop); a repo with NO
   live session pings the owner's Discord once. The
   gatekeeper's own ssh/tmux nudge is best-effort delivery, never the guarantee.
6. **Nudge text shape (canonical, MANDATORY):** an injected cross-stream nudge MUST start with the
   exact prefix `Priorita: prio:bounce` — the api-watchdog auto-submits a frozen input-box draft
   matching this prefix (a swallowed Enter left gk→montalu nudges sitting unsubmitted for hours,
   3× on 2026-07-20/21), so never reword the prefix and NEVER use it for human-authored text.
   After `send-keys`, capture-pane VERIFY the input box emptied; if the text stuck, leave it (the
   watchdog submits it within ~2 min) — the `prio:bounce` label remains the delivery guarantee.
7. **Stream→supervisor ACTION requests — `needs-gatekeeper` (airuleset #30, the MIRROR direction).**
   A stream needing an action only the gatekeeper/supervisor can perform (box access, workflow
   re-dispatch, infra) files it as a TICKET — **never through the user, never by ssh to a foreign
   box**: run `python3 ~/devel/airuleset/airuleset.py gk-request --title "..." [--body-file f]`
   (or `--issue N --comment "..."` for an existing ticket). The helper labels it
   `needs-gatekeeper`; a stream whose PAT cannot label degrades AUTOMATICALLY to the
   `GATEKEEPER-ACTION:` title/comment prefix (the read-only-fork path) — both forms are matched.
   **Delivery is the api-watchdog (job 11, mirror of job 8, ~30 min):** an IDLE supervisor pane
   gets the `gk-request backstop:` machine nudge; a BUSY supervisor loop needs nothing (the label
   alone queues it — the master loop's lane scheduler picks it next turn); no live supervisor
   session → ONE deduped Discord ping. The `gk-req N` statusline badge shows the open-request
   count on full-authority boxes. **Supervisor pickup protocol:** FIRST the SELF-SERVICE TRIAGE
   (rule 9 below) — a prod-STATE READ the stream can answer itself is BOUNCED, never worked; then
   ACK-comment the ticket (add the label if only the GATEKEEPER-ACTION: title carries it), perform
   the LIVE intervention, comment the result, remove the label or close, then nudge the requesting
   stream's pane (rule 6 mechanics) so it resumes without polling. Session-local pollers for this
   lane are FORBIDDEN — the watchdog owns the cadence (the odoo-erp master-loop interim poller is
   superseded).
9. **SELF-SERVICE TRIAGE — bounce a prod-STATE READ the stream could do itself (airuleset #516).**
   The recurring overload: a sub-dev files gk an action request for a prod-STATE READ (a group
   membership, a row count, a config value, sent-mail content) it could read itself from its own
   fresh PROD copy — `autonomous-verification.md`'s "What's on PROD? is a SELF-SERVICE question".
   Both sides are now MECHANICALLY gated. **At FILING** (side A): `hooks/block-gk-request-without-
   selfservice.sh` BLOCKS a reduced-stream gk action request that carries no
   `Self-service-checked: <what I tried — RO channel / fresh prod copy — and what LIVE PROD
   intervention I need from gk>` line. **At RECEIPT** (side B): the api-watchdog job 31
   (`gk_selfservice_bounce`) AUTO-BOUNCES any needs-gatekeeper action request that has NO such
   line and is attributable to a reduced stream (`handed-by:<stream>`) — it removes
   `needs-gatekeeper`, adds `prio:bounce` + `stream:<stream>` (routing it back into the stream's
   own bounce lane, rule 4), and posts the template comment; every candidate's verdict is logged.
   **Your MANUAL triage step (the semantic case the watchdog cannot mechanize):** when a request
   DOES carry a `Self-service-checked:` line but that line names only a READ with NO live PROD
   intervention (a pure read dressed up), BOUNCE it yourself with the SAME template — remove
   `needs-gatekeeper`, add `prio:bounce` + `stream:<stream>`, and comment (Slovak):
   > 🔄 Automaticky/ručne vrátené (prio:bounce) — toto je prod-STATE READ, ktorý si vieš spraviť
   > sám z čerstvej PROD kópie (`REFRESH-DEV-BOX-FROM-PROD: <stream>`) alebo cez vlastný read-only
   > kanál (`has_group`/`search_read`). Gatekeeper robí len ŽIVÉ zásahy do PRODu. Ak naozaj
   > potrebuješ živý zásah, znova to zadaj s riadkom `Self-service-checked:` menujúcim ten zásah.

   Only a request naming a genuine LIVE intervention (a stuck queue restart, a `RUNTIME_DEPS`
   install, a migration) is WORKED. Never mechanize the pure-read judgement in a hook/watchdog —
   that would be a banned prose classifier; the watchdog only ever bounces the falsifiable
   "no line at all" case.
8. **Carve-out HAND-OFF vs. ACTION request — the SAME `needs-gatekeeper` label, told apart by
   `stream:<user>` (airuleset #498, live incident odoo-erp #3244).** `needs-gatekeeper` is
   OVERLOADED. A stream carved OUT of the hand-off gate (a phase-1 stream with no shadow box,
   whose validation hand-off gate fails STRUCTURALLY) has its `ready-for-review` label stripped at
   every hand-off; the repo-side gate applies `needs-gatekeeper` INSTEAD of silently stripping, so
   that stream's code HAND-OFFS arrive under `needs-gatekeeper` + `stream:<user>` and belong in the
   gatekeeper's REVIEW queue. The gatekeeper's review queue is therefore
   `ready-for-review` ∪ `needs-gatekeeper`, scoped to `stream:<stream>` — NEVER `ready-for-review`
   alone (the miva incident: an rfr-only queue never surfaced the carve-out hand-off, so it rotted
   with both sides claiming done). The ACTION request of rule 7 also carries `needs-gatekeeper` but
   NEVER `stream:<user>` (`handed-by:<user>` instead, #191 C1), so the `stream:<stream>` scope is
   exactly what keeps the two apart: a carve-out hand-off enters review, a bare action-request does
   NOT. **Queue membership is carried by LABELS — the `GATEKEEPER-ACTION:` / `READY-FOR-REVIEW:`
   comment TEXT is never a QUEUE-membership signal, labels carry queue state** (a repo-wide
   `in:comments` phrase query over-matches and would over-count the queue — the same reason
   `cli_quals.py`'s obligation SET uses the LABEL, not the comment). The one narrow exception is
   NOT a queue definition: the statusline `gk`-bucket's BOUNDED per-ticket `READY-FOR-REVIEW:`-
   comment recovery (`_is_readiness_comment`, #313 pt 2) is a DISPLAY-count fallback for a
   broken-label residual, never the review queue itself. This is the CANONICAL queue definition;
   `/process-subdev` step 1 and `/autopilot-master` LANE 1 both conform to it.

