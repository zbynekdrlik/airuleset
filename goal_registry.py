"""goal_registry.py — the `/goal` autopilot loop condition as a COMPOSED
STRUCTURE, not three hand-maintained prose blobs (#621, owner directive
2026-08-22).

WHY: the `/goal` condition used to live as three independent prose blocks in
`skills/autopilot/SKILL.md` (one per authority profile). Nothing could say
which goal carried which clause, so the saturation directive was ABSENT from
all three for months with nothing to report it (the #621 incident). This
module makes the goal COMPOSED from named clauses: one source of truth per
shared clause, mechanical coverage, and a COMPUTED character budget (#617
burned days on a hand-estimated length).

The three `/goal ...` lines shipped in `skills/autopilot/SKILL.md` are the
RENDERED output of `render(profile)`; `watchdog/goal.py` (unchanged) reads
those lines at arm time. A drift test locks `SKILL.md == render(registry)`,
so the registry stays the single authoring source without `goal.py` needing
to import this module. Regenerate the shipped lines with
`airuleset.py goal-inventory --write` after editing a clause.

Pure data + a trivial `" ".join` renderer + `len()` budget arithmetic — no
heuristics, no silent branches (#486). The single-spaced templates make the
join byte-exact: each clause is a contiguous run of words, joined by one
space, reproducing the original line exactly.
"""

import os as _os
import re as _re

PROFILES = ("full", "branch-merge", "fork-no-merge")

# Claude Code refuses a `/goal` condition longer than this and never arms
# (watchdog/goal.py:345). MIRRORS watchdog.goal.GOAL_ARM_CHAR_CAP; the two are
# drift-locked in tests/test_goal_registry.py so they can never diverge.
GOAL_ARM_CHAR_CAP = 4000


class Clause:
    """One named piece of a `/goal` condition. `text` is a str when every
    profile that carries the clause renders it identically (ONE definition —
    e.g. `saturation-core`), or a {profile: str} dict when the profiles
    genuinely differ (e.g. `proof`, whose commands/tokens differ)."""

    __slots__ = ("id", "profiles", "text")

    def __init__(self, id, profiles, text):
        self.id = id
        self.profiles = tuple(profiles)
        self.text = text
        if isinstance(text, dict):
            assert set(text) == set(self.profiles), (id, "text keys != profiles")

    def text_for(self, profile):
        if profile not in self.profiles:
            return None
        return self.text if isinstance(self.text, str) else self.text[profile]


# The reconciliation the owner asked to be VISIBLE in the registry, not buried
# in prose (#848, 2026-09-02): `saturation-core` keeps parallel worktree lanes
# live and refills a returned slot ONLY with a DISPATCHABLE unit (dependencies
# closed, #993 r2b — the class-based infra-serial half was removed: infra
# serialisation is ROUTING via --role, not a live-lane gate; the count is sized
# to box+backlog, #991); `saturation-delivery` integrates each returned branch
# SERIALLY under the mutex as it returns; `compact-boundary` now just ENDS every
# integration cycle with the `## ✅ Work Complete` report — machine compacts are
# REMOVED (#1084, owner ROZHODNUTÉ 2026-09-19), so there is NO compact command
# and NO compact HOLD; Claude Code's native threshold autocompact is the only
# compaction left. So dispatch is continuous-refill, integration is serial, and
# nothing types `/compact`. Tests assert the compact-boundary clause carries no
# compact-request instruction and no HOLD.
SATURATION_RECONCILES_COMPACT = ("saturation-core", "saturation-delivery",
                                 "compact-boundary")

# Clause KINDS a profile MUST carry (a missing one is a red coverage test — the
# mechanical guard that prevents another silently-absent clause like the
# saturation directive was for months, #621). `REQUIRED_CLAUSES` is the shared
# base every profile carries; `REQUIRED_BY_PROFILE` adds the profile-specific
# load-bearing clauses so a full-only clause (prod-gate — the approval-scope.md
# "never gate on events/prod" hardest rule; parked) or a reduced-only clause
# (review-watch, authority-ends) can never be silently dropped either.
REQUIRED_CLAUSES = (
    "header", "stop-a", "stop-a-livelane", "stop-b-header", "obligation", "proof",
    "how-to-tell", "done-never", "cannot-tell", "produce-proof",
    "irreversible", "work-intro", "saturation-core", "saturation-delivery",
    "ask", "night", "bounce", "verify-sources", "compact-boundary",
)
REQUIRED_BY_PROFILE = {
    "full": REQUIRED_CLAUSES + ("stream-note", "prod-gate", "parked"),
    "branch-merge": REQUIRED_CLAUSES + ("review-watch", "authority-ends"),
    "fork-no-merge": REQUIRED_CLAUSES + ("review-watch", "authority-ends"),
}

# The ORDERED clause registry. render(profile) walks this list, keeps the
# clauses that profile carries, and joins their text with a single space.
CLAUSES = [
    Clause("header", PROFILES,
        "STOP CONDITIONS — the loop is DONE the moment EITHER holds, both checkable from the transcript:"),
    Clause("stop-a", PROFILES, {
        "full": "(A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU` (after I answer, Claude resolves that ticket and re-prints this /goal line if issues remain).",
        "branch-merge": "(A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU`.",
        "fork-no-merge": "(A) BLOCKED ON MY ANSWER — the latest assistant message ends with a line starting `❓ NEEDS YOU:` and there is NO user message after it; NEVER continue me past an unanswered `❓ NEEDS YOU`.",
    }),
    # #1007 (miva1 ×5, 2026-09-12): (A) blocks the loop only when GENUINELY
    # blocked — with a background agent/lane live the correct form is
    # ASK-AND-CONTINUE, never a bare `❓ NEEDS YOU:` re-poke. A SEPARATE clause
    # (not appended to stop-a's text) so the tightest-arming gk-review variant
    # can DROP it — see _REVIEW_B_BLOCK — since the review block already carries
    # the #1007 rule in its own (B) condition, and stop-a's text stays
    # byte-identical for the review-variant verbatim-present lock.
    Clause("stop-a-livelane", PROFILES,
        "(A) applies only when no background agent/lane is live; with lanes live use ASK-AND-CONTINUE and let the footer U carry the question."),
    Clause("stop-b-header", PROFILES, {
        "full": "(B) BACKLOG EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
        "branch-merge": "(B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
        "fork-no-merge": "(B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
    }),
    Clause("obligation", PROFILES, {
        "full": "Every open issue THIS box is OBLIGED to action — the CORE slice (not labeled autopilot-skip, not owned by a sub-dev stream) PLUS every ticket only I can action whatever stream owns it (needs-gatekeeper, a hand-off awaiting my review/merge/close) — is resolved,",
        "branch-merge": "Every open issue ASSIGNED TO ME here not labeled autopilot-skip is MERGED via my own PR into the project's INTEGRATION branch (develop unless the project CLAUDE.md names another), no open prio:bounce for my stream,",
        "fork-no-merge": "Every issue ASSIGNED TO ME here not labeled autopilot-skip is HANDED OFF — a later close is not my (B) proof —",
    }),
    Clause("proof", PROFILES, {
        "full": "and (B) holds ONLY when my final message carries the pasted OUTPUT of both proof commands: `python3 ~/devel/airuleset/airuleset.py core-quals --count` printing exactly `0` under it (it counts EXACTLY that obligation set), AND `gh run list -b main -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND then the line `🏁 BACKLOG EMPTY: 0 open, main green` directly above the terminal `✅ DONE:` marker.",
        "branch-merge": "and (B) holds ONLY when my final message carries the pasted OUTPUT of all four proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `gh run list -b <integration> -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND `git merge-base --is-ancestor <my last integration merge> origin/main && echo RELEASED` printing exactly `RELEASED` under it, AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (`gk N`/`U N`/`W N` = parked, never blocks 🏁; blank = unmeasurable; a `bounce K` BLOCKS 🏁 until `slice-quals --bounces --unhandled` prints nothing under it), AND then the line `🏁 BACKLOG EMPTY: 0 open, integration green, released` directly above the terminal `✅ DONE:` marker.",
        "fork-no-merge": "and (B) holds ONLY when my final message carries the pasted OUTPUT of all three proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `git merge-base --is-ancestor <my last merged commit> origin/main && echo RELEASED` printing exactly `RELEASED` under it (release still pending is STILL review-watch, not done), AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (`gk N`/`U N`/`W N` = parked, never blocks 🏁; blank = unmeasurable; a `bounce K` BLOCKS 🏁 until `slice-quals --bounces --unhandled` prints nothing under it), AND then the line `🏁 BACKLOG EMPTY: 0 open, released` directly above the terminal `✅ DONE:` marker.",
    }),
    Clause("how-to-tell", PROFILES,
        "HOW TO TELL A REAL COMPLETION FROM A CLAIMED ONE: real = output shown; claimed = asserted."),
    Clause("done-never", PROFILES, {
        "full": "`✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a merged PR.",
        "branch-merge": "`✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a merged PR.",
        "fork-no-merge": "`✅ DONE:` NEVER satisfies (B) — it is the per-ticket CONTINUE terminator, even in a turn full of `✅` rows and a clean local verification.",
    }),
    Clause("cannot-tell", PROFILES,
        "IF I CANNOT TELL — missing, unreadable, or stale output, any doubt — (B) does NOT hold: CONTINUE. There is no third answer."),
    Clause("produce-proof", PROFILES, {
        "full": "TO PRODUCE THE PROOF: run both, paste each output, write the `🏁` line — no proof, no stop.",
        "branch-merge": "TO PRODUCE THE PROOF: run all four, paste each output, write the `🏁` line — no proof, no stop.",
        "fork-no-merge": "TO PRODUCE THE PROOF: run all three, paste each output, write the `🏁` line — no proof, no stop.",
    }),
    Clause("stream-note", ("full",),
        "A stream ticket in that set is NOT mine to implement — I ACTION it (review, merge, close, unblock) and never write its code (a bare sub-dev bounce is NOT in this set — `/process-subdev`'s loop holds it)."),
    Clause("review-watch", ("branch-merge", "fork-no-merge"), {
        "branch-merge": "A handed-off ticket or an empty backlog, release still pending, is NOT done — REVIEW-WATCH: stay alive, re-check hourly with a FOREGROUND sleep-poll (~1h; never a wakeup/schedule), end ⏳ WORKING; never park silently — work any new stream/bounce ticket.",
        "fork-no-merge": "An open ticket carrying my READY-FOR-REVIEW comment (names the fork branch + green local verification; the comment is the signal, the label best-effort) never blocks 🏁, but PREFER REVIEW-WATCH: stay alive, re-check hourly with a FOREGROUND sleep-poll (~1h; never a wakeup/schedule), end ⏳ WORKING; never park silently — work any gatekeeper bounce.",
    }),
    Clause("authority-ends", ("branch-merge", "fork-no-merge"), {
        "branch-merge": "My authority ENDS at the integration branch: never promote to staging/main, never deploy, never touch other streams'.",
        "fork-no-merge": "My authority ENDS at the hand-off: I push MY fork branches + evidence — NEVER open/merge a PR, never push upstream, never deploy, close only per authority, never touch other streams'.",
    }),
    Clause("irreversible", PROFILES, {
        "full": "Also stop for a genuinely-irreversible approval or a CI failure unfixable after two real attempts.",
        "branch-merge": "Also stop for a genuinely-irreversible approval or a CI failure unfixable after two real attempts.",
        "fork-no-merge": "Also stop for a genuinely-irreversible approval or local verification failing twice.",
    }),
    Clause("work-intro", PROFILES, {
        "full": "While NEITHER holds, work the backlog —",
        "branch-merge": "While NEITHER holds, work the assigned backlog —",
        "fork-no-merge": "While NEITHER holds, work the assigned backlog —",
    }),
    Clause("saturation-core", PROFILES,
        "CONTINUOUS REFILL, never one ticket per turn: keep `isolation:worktree` autopilot-worker lanes live — refill ONLY with a DISPATCHABLE unit (dependencies closed);"),
    Clause("saturation-delivery", PROFILES, {
        "full": "integrate returned branches SERIALLY under the integration mutex as they return;",
        "branch-merge": "merge returned branches into the integration branch SERIALLY under the mutex as they return;",
        "fork-no-merge": "hand off returned fork branches SERIALLY as they return;",
    }),
    Clause("prod-gate", ("full",),
        "Never gate, classify, skip, or warn based on prod-usage / events / off-air / hardware — I alone guard whether prod is live."),
    Clause("ask", PROFILES, {
        "full": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`) ASK ONCE, no repeat; `❓ NEEDS YOU` only if nothing else is workable.",
        "branch-merge": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`) ASK ONCE, no repeat; `❓ NEEDS YOU` only if nothing else is workable.",
        "fork-no-merge": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`) ASK ONCE, no repeat; `❓ NEEDS YOU` only if nothing else is workable.",
    }),
    Clause("parked", ("full",),
        "A `needs-answer`/`needs-decision`/`needs-acceptance`/`ops-wait` ticket is parked — never counted, never blocks 🏁 (paste `core-quals --waiting`/`--ops-wait`). NEVER bury a question or blame my silence."),
    Clause("night", PROFILES,
        "No night/day difference (#791): work the backlog and ask questions 24/7 — no night-hour cutoff, no time-of-day deferral."),
    Clause("bounce", PROFILES, {
        "full": "Bounce lane: open tickets labeled prio:bounce jump the queue — the next FREE lane takes them oldest-first (never preempting a running lane); a named nudge gets a one-line ACK + prio:bounce label, taken next turn, never worked inline.",
        "branch-merge": "Bounce lane: my prio:bounce tickets fill each FREE lane oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label next turn, never inline.",
        "fork-no-merge": "Bounce lane: my prio:bounce tickets fill each FREE lane oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label (best-effort), taken next turn, never worked inline.",
    }),
    Clause("verify-sources", PROFILES, {
        "full": "Count a ticket done ONLY after verifying from primary sources — `gh pr view` (merged, closingIssuesReferences), `gh run list` (main green), `gh issue view` (closed), the deployed version on the live target — never the worker's claim alone; verify the LAST ticket as strictly as the first.",
        "branch-merge": "Count a hand-off done ONLY after verifying it from primary sources — `gh pr view` (merged into integration), that branch's CI run, the READY-FOR-REVIEW comment posted — never the worker's claim alone; verify the LAST as strictly as the first.",
        "fork-no-merge": "Count a hand-off done ONLY after verifying from primary sources — the `READY-FOR-REVIEW:` comment present (`gh issue view --json comments`), the fork branch pushed, local test/lint output shown — never the worker's claim alone; verify the LAST as strictly as the first.",
    }),
    Clause("compact-boundary", PROFILES, {
        "full": "After EVERY integration END the turn with the full `## ✅ Work Complete` report (`completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). Machine compacts are REMOVED (#1084) — native autocompact only; never a compact command, never a compact HOLD.",
        "branch-merge": "After EVERY integration END the turn with the full `## ✅ Work Complete` report (the branch-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). Machine compacts are REMOVED (#1084) — native autocompact only; never a compact command, never a compact HOLD.",
        "fork-no-merge": "After EVERY hand-off END the turn with the full `## ✅ Work Complete` report (the fork-no-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). Machine compacts are REMOVED (#1084) — native autocompact only; never a compact command, never a compact HOLD.",
    }),
]


# --------------------------------------------------------------------------- #
# #998 — per (authority, role, mode) rendering. The DEFAULT (parallel, no role)
# is byte-identical to the historical `render(profile)`; a SEQUENTIAL mode
# SUBSTITUTES the refill clause (`saturation-core`) with a one-unit-at-a-time
# clause, and an `infra` ROLE APPENDS an infra-scope clause after the delivery
# clause. NO variant ever carries a turn cap ("stop after N turns" is banned in
# operational goals, owner directive 2026-09-12, #993 comment) — the renderer
# never inserts one, and `no_turn_cap_ok` locks it.
# --------------------------------------------------------------------------- #

MODES = ("parallel", "sequential")
ROLES = (None, "review", "infra", "quality")  # #1074 — the gk-quality window

# The sequential clause that REPLACES `saturation-core` (the refill clause).
_SEQUENTIAL_SATURATION = (
    "SEQUENTIAL — ONE unit at a time: dispatch → main review → integrate → "
    "verify → next; no refill;")

# #1060 L3b — the DUAL dispatch-CHANNEL clause. This is ORTHOGONAL to the
# parallel/sequential MODE: it swaps HOW a unit is dispatched (a cross-session
# SendMessage to the persistent implementer window, not an in-session
# autopilot-worker Agent), not the loop's stop conditions or refill discipline —
# so the armed `/goal` line is UNCHANGED and `drift()` stays green. Selected by
# the model-backend marker (`~/.claude/airuleset-model-backend.json`). The SKILL
# body carries this string VERBATIM (`skill_dual_drift` check-locks it, exactly
# as `skill_sequential_drift` locks the sequential clause), so an unarmed session
# reading only the body can never diverge from this canonical dispatch contract.
_DUAL_DISPATCH = (
    "DUAL dispatch (model-backend marker present) — dispatch each unit to the "
    "IMPLEMENTER window, NEVER an in-session Agent(autopilot-worker): "
    "ListAgents → SendMessage(to the impl session) the SAME per-ticket prompt; "
    "the wait = the implementer's return message OR a ticket poll for "
    "LANE-RETURN (the Approach-3 fallback); everything after the return (Fable "
    "review, integrate, run-card) is UNCHANGED; no impl session → journal "
    "`dual: implementer session missing — dispatch left on the ticket` and "
    "continue other work — NEVER fall back to an in-session Opus autopilot-worker "
    "on a dual box (that breaks the pilot's per-alias measurement).")

# The infra-role clause, APPENDED after `saturation-delivery` when role==infra.
_INFRA_ROLE = (
    "INFRA ROLE — only tickets labelled `infra`; no stream hand-offs / release "
    "ops / PROD data; never touch the review window's checkout; coordinate with "
    "the review window via tickets only; box-maintenance steps stop with `❓ "
    "NEEDS YOU` between steps.")

# The generic (B) proof + obligation block the review ROLE REPLACES (#1000).
# Clause (h) of the review block IS the gk review window's own (B) done
# condition, so these generic-backlog clauses are superseded. Replacing (not
# appending) them is what fits the review clauses under the 4000 arm cap:
# appending (a)-(h) to the full template renders 5081 chars (> 4000, never
# arms); replacing this ~1383-char block with the ~1527-char review block
# renders ~3765 (headroom ~235, matching montalu's proven-arming 3764). Every
# OTHER clause stays byte-identical — see goal_registry drift/snapshot tests.
# stop-a-livelane (#1007) rides in this drop set too: the review block's own
# (B) condition already carries the #1007 "iba keď nebeží žiadna lane" rule, so
# the shared (A) live-lane appendix is redundant in the review variant AND would
# push it past its tight arm-cap (test_review_variant_arms_under_the_cap_with_
# headroom, ≤ GOAL_ARM_CHAR_CAP-150) — so the review role DROPS it, keeping the
# variant byte-identical. It is NOT a (B) clause; it is dropped, not replaced.
_REVIEW_B_BLOCK = ("stop-a-livelane", "stop-b-header", "obligation", "proof",
                   "how-to-tell", "done-never", "cannot-tell", "produce-proof",
                   "stream-note")

# The review-ROLE block (#1000, owner directive 2026-09-12 "subdevs never wait
# on gk" + "uz mam dost tvojich patchworkov" = ONE renderer, no second text
# source): clause (h) as the review window's (B) done condition, then the
# operating directives (a)-(g). Slovak, in the template's terse style; carries
# NO turn cap ("stop po"/"stop after" never appear — `no_turn_cap_ok` / the
# #1000 tests lock it). Inserted at the `stop-b-header` position by
# render_goal_line when role=="review".
_REVIEW_ROLE = (
    "(B) BACKLOG EMPTY — gk REVIEW okno, PROVEN IN THIS TURN, NEVER CLAIMED: "
    "HOTOVO iba keď 0 hand-offov starších než 1 h bez akcie A 0 otvorených "
    "stream PR bez skorého review ≤ 1 h A montalu/slovnormal/miva == main — inak "
    "CONTINUE bez akéhokoľvek turn limitu; hlavný ďalší stop je (A) ❓ NEEDS YOU "
    "na ownerovu odpoveď (per #1007 iba keď nebeží žiadna lane). "
    "REVIEW ROLE — "
    "(a) infra tickety (label infra) sa TU NEpracujú, iba zakladajú; "
    "(b) SUBDEVS NIKDY NEČAKAJÚ NA GK: každý OTVORENÝ stream PR→develop "
    "(montalu/*, david*/kvaskodev, miva*, simap*) dostane skoré advisory review "
    "do 1 h od posledného pushu (koment „gk skoré review (advisory, pred "
    "hand-offom)\", bez verdikt headingu) a znova po každom podstatnom pushi; "
    "(c) každý hand-off (ready-for-review / needs-gatekeeper / prio:bounce / "
    "GATEKEEPER-ACTION; stream:montalu prvé) dostane gk verdikt alebo akčný "
    "komentár do 1 h; "
    "(d) lanes plním DISPATCHOVATEĽNÝMI jednotkami vždy keď existuje workable "
    "(review / fix-forward / resync / PROD akcia) — čakanie na CI nikdy nedrží "
    "slot; "
    "(e) každá akceptovaná vetva má merged PR do develop; červený/DIRTY gk PR "
    "dostane fix lane v tom istom cykle; "
    "(f) release train nikdy nestojí (develop pred main ≥ 2 h a nič in-flight → "
    "existuje cut PR; shadow/main/deploy postup; každý STOP hlásený na "
    "odoo-erp#6883); "
    "(g) za cyklus vypíš `python3 ~/devel/airuleset/airuleset.py core-quals "
    "--role review --count`, počet otvorených stream PR bez skorého review ≤ 1 "
    "h, lanes N/cap, stav release (cut PR, shadow, main PR, posledné deploy runy "
    "s DB verziou, montalu/slovnormal/miva vs main).")

# The gk-QUALITY-ROLE block (#1074, owner directive 18.9., escalated 21.9. — a
# dedicated gk session that owns subdev delivery QUALITY end-to-end). Like the
# review block it REPLACES the generic (B) proof + obligation block (clause (h)
# below is the gk-quality window's own (B) done condition) — the SAME mechanism,
# a different charter — so it fits under the 4000 arm cap; every OTHER clause
# stays byte-identical. gk-full-only + SEQUENTIAL (one quality unit at a time).
# Carries NO turn cap. Inserted at the `stop-b-header` position by
# render_goal_line when role=="quality".
_QUALITY_ROLE = (
    "(B) QUALITY CONTRACT HELD — gk-QUALITY window, PROVEN IN THIS TURN, NEVER "
    "CLAIMED: DONE only when 0 subdev hand-offs failed the gate for a class not "
    "yet root-caused into a mechanical guard AND `audit_bounce_rule_updates.py "
    "--rounds` is trending DOWN per stream AND no gk-quality ticket is workable "
    "— else CONTINUE with NO turn limit; the only other stop is (A) `❓ NEEDS "
    "YOU` on the owner's answer, which (#1007) applies only when no background "
    "agent/lane is live; with lanes live use ASK-AND-CONTINUE and let the footer "
    "U carry the question. "
    "QUALITY ROLE — I own subdev delivery QUALITY end-to-end: "
    "(a) own the review lenses (`gk-review-lenses*.md`) + the hand-off gate "
    "`subdev_handoff_gate.py` promotions — an advisory check graduates to FAIL "
    "per the 7-day policy; "
    "(b) root-cause EVERY bounce and release-break into a guard / lane / rule so "
    "the class never recurs — `audit_bounce_rule_updates.py --rounds` trends "
    "DOWN per stream; a re-introduced solved problem is a rule defect, never a "
    "fresh bounce; "
    "(c) enforce fresh-prod-copy + E2E evidence at hand-off as gate evidence — a "
    "green suite alone is not delivery; "
    "(d) give the streams a read-only PROD fact source (live PROD version / row "
    "reads) so a hand-off is verified against prod, never guessed; "
    "(e) infra tickets (label infra) are NOT worked here, only filed; "
    "(f) per cycle print `python3 ~/devel/airuleset/airuleset.py core-quals "
    "--role quality --count`, the bounce-rounds trend per stream, and the gate "
    "promotion state.")

# A turn cap in an OPERATIONAL goal is banned (#993 comment 2026-09-12). Matches
# "stop after N turns" / "stop after 30 turns" / "…or stop after …".
_TURN_CAP_RE = _re.compile(r"stop\s+after\s+(?:\d+|N)\s+turns?", _re.IGNORECASE)


def render_goal_line(authority, mode="parallel", role=None):
    """The exact `/goal ...` line for `(authority, mode, role)` (#998).

    `mode="parallel", role=None` reproduces `render(authority)` byte-for-byte
    (so the SKILL.md drift lock is unchanged). `mode="sequential"` substitutes
    the refill clause; `role="infra"` appends the infra-scope clause;
    `role="review"` REPLACES the generic (B) proof + obligation block with the
    gk review window's own (B) done condition + operating clauses (a)-(g)
    (#1000); `role="quality"` REPLACES the same block with the gk-quality
    window's (B) done condition + the subdev-quality charter (a)-(f) (#1074).
    Never inserts a turn cap."""
    if authority not in PROFILES:
        raise ValueError("unknown authority: %r" % (authority,))
    if mode not in MODES:
        raise ValueError("unknown mode: %r" % (mode,))
    if role not in ROLES:
        raise ValueError("unknown role: %r" % (role,))
    if role in ("review", "quality") and authority != "full":
        # #1000 F5 / #1074 — the review AND quality blocks hardcode
        # full-authority gk semantics (montalu/slovnormal/miva, release train,
        # core-quals, the subdev quality contract); each is only ever paired
        # with a full-authority gk window. Refuse a nonsensical reduced-authority
        # render rather than emit gk clauses into it.
        raise ValueError(
            "%s role is gk-full-only, not %r" % (role, authority))
    parts = []
    for c in CLAUSES:
        if authority not in c.profiles:
            continue
        if role in ("review", "quality") and c.id in _REVIEW_B_BLOCK:
            # The generic (B) machinery is superseded by the review/quality
            # block's own (B) done condition; insert that block ONCE at the (B)
            # position and drop the rest of the generic block (#1000 / #1074 —
            # SAME (B)-substitution mechanism, a different charter).
            if c.id == "stop-b-header":
                parts.append(_REVIEW_ROLE if role == "review"
                             else _QUALITY_ROLE)
            continue
        text = c.text_for(authority)
        if mode == "sequential" and c.id == "saturation-core":
            text = _SEQUENTIAL_SATURATION
        if role == "infra" and authority == "full" and c.id == "proof":
            # #1066 lane B item 2 (finding a) — the gk-infra window (full
            # authority, sequential) must prove its OWN slice, not the WHOLE gk
            # obligation set it can neither action nor drive to 0. Role-scope its
            # ONE quals proof command (`core-quals --count` ->
            # `core-quals --role infra --count`, exactly what `airuleset.py
            # status` already resolves for that pane, #1065/#1074). FULL-ONLY
            # (documented in the #1066 Anchors-confirmed comment): applying it to
            # the lock-only reduced+infra defensive variants would STACK on
            # item 1's bounce clause and breach the design's explicit >=20
            # arm-cap headroom invariant; no reduced-authority infra window
            # exists, so scoping to full is behaviourally identical in
            # production. Non-infra variants are byte-identical (drift/snapshot
            # locks stay green); `_INFRA_ROLE` append below is unchanged.
            text = text.replace("-quals --count", "-quals --role infra --count")
        parts.append(text)
        if role == "infra" and c.id == "saturation-delivery":
            parts.append(_INFRA_ROLE)
    return "/goal " + " ".join(parts)


def render(profile):
    """The exact `/goal ...` line for `profile` — the DEFAULT (parallel, no
    role) variant. Kept as the SKILL.md-drift-lock anchor; delegates to
    `render_goal_line` so the two can never diverge."""
    return render_goal_line(profile, "parallel", None)


def variant_specs():
    """Every (authority, mode, role) variant `goal-inventory --check` locks: all
    authority × mode with no role, PLUS the infra-role variant per authority
    (the gk-infra window is inherently sequential-infra), PLUS the gk-full-only
    sequential QUALITY variant (#1074 — the 10th locked variant; the gk-quality
    window is inherently sequential-quality). Enumerated so a new clause that
    breaks any variant (over budget, a stray turn cap, a dropped required
    clause) is caught mechanically."""
    specs = []
    for a in PROFILES:
        for m in MODES:
            specs.append((a, m, None))
        specs.append((a, "sequential", "infra"))
    specs.append(("full", "sequential", "quality"))  # #1074 — gk-full-only
    return specs


def variant_check():
    """Return a list of error strings ([] == every variant is valid). Locks, per
    variant: renders, ≤ GOAL_ARM_CHAR_CAP, NO turn cap, carries every required
    clause, and — for sequential — the sequential phrase present + the refill
    phrase absent; for infra — the infra phrase present; for quality — the
    quality phrase present (#1074). The gk-full-only
    `review` variant (#1000) is checked SEPARATELY below for BUDGET + no-turn-cap
    only — its required-clause leg is skipped because it legitimately SUBSTITUTES
    the (B) proof/obligation clauses (clause (h) supersedes them), which the
    profile-level `clause_ids()` coverage leg cannot model."""
    errs = []
    for authority, mode, role in variant_specs():
        try:
            line = render_goal_line(authority, mode, role)
        except Exception as exc:  # noqa: BLE001
            errs.append("render(%s,%s,%s) raised: %r" % (authority, mode, role, exc))
            continue
        tag = "%s/%s/%s" % (authority, mode, role)
        if len(line) > GOAL_ARM_CHAR_CAP:
            errs.append("%s over budget: %d > %d" % (tag, len(line), GOAL_ARM_CHAR_CAP))
        if _TURN_CAP_RE.search(line):
            errs.append("%s carries a TURN CAP (banned in operational goals)" % tag)
        for cid in REQUIRED_BY_PROFILE.get(authority, REQUIRED_CLAUSES):
            if cid not in clause_ids(authority):
                errs.append("%s missing required clause %s" % (tag, cid))
        if mode == "sequential":
            if "ONE unit at a time" not in line:
                errs.append("%s sequential missing the one-unit clause" % tag)
            if "CONTINUOUS REFILL" in line:
                errs.append("%s sequential still carries the refill clause" % tag)
        if role == "infra" and "INFRA ROLE" not in line:
            errs.append("%s infra missing the infra-role clause" % tag)
        if role == "quality" and "QUALITY ROLE" not in line:  # #1074
            errs.append("%s quality missing the quality-role clause" % tag)
    # #1000 F1 — the review variant is the TIGHTEST-arming variant and is NOT in
    # variant_specs (its required-clause leg cannot model the (B) substitution),
    # so lock its BUDGET + no-turn-cap here so `goal-inventory --check` is honest
    # that it guards every ARMABLE variant. (Headroom is locked tighter by the
    # dedicated #1000 test.)
    try:
        rline = render_goal_line("full", "parallel", "review")
    except Exception as exc:  # noqa: BLE001
        errs.append("render(full,parallel,review) raised: %r" % (exc,))
    else:
        if len(rline) > GOAL_ARM_CHAR_CAP:
            errs.append("full/parallel/review over budget: %d > %d"
                        % (len(rline), GOAL_ARM_CHAR_CAP))
        if _TURN_CAP_RE.search(rline):
            errs.append("full/parallel/review carries a TURN CAP (banned)")
    return errs


def clause_ids(profile):
    """The clause ids `profile` carries, in render order."""
    return [c.id for c in CLAUSES if profile in c.profiles]


def length(profile):
    return len(render(profile))


def headroom(profile):
    """Characters remaining before Claude Code refuses to arm the goal."""
    return GOAL_ARM_CHAR_CAP - length(profile)


def over_budget(profile):
    return length(profile) > GOAL_ARM_CHAR_CAP


def missing_required(profile):
    """Required clauses this profile fails to carry (empty == fully covered) —
    the shared base plus the profile's own load-bearing clauses."""
    have = set(clause_ids(profile))
    required = REQUIRED_BY_PROFILE.get(profile, REQUIRED_CLAUSES)
    return [c for c in required if c not in have]


def inventory(profile):
    """Structured answer to "which goal solves what and what does it contain":
    the clauses carried (id + length), the rendered length, and the remaining
    budget. Consumed by `airuleset.py goal-inventory`."""
    clauses = [{"id": c.id, "len": len(c.text_for(profile))}
               for c in CLAUSES if profile in c.profiles]
    return {
        "profile": profile,
        "clauses": clauses,
        "clause_count": len(clauses),
        "length": length(profile),
        "cap": GOAL_ARM_CHAR_CAP,
        "headroom": headroom(profile),
        "over_budget": over_budget(profile),
        "missing_required": missing_required(profile),
    }


# --------------------------------------------------------------------------- #
# SKILL.md is the RENDERED runtime artifact watchdog/goal.py reads at arm time.
# These helpers let `airuleset.py goal-inventory` VERIFY (--check) that the
# shipped `/goal` lines still equal render(registry), and REGENERATE them
# (--write) after a clause edit. The registry stays the single authoring source
# without goal.py importing this module. The parse format (a `**AUTHORITY: <x>**`
# heading followed by a fenced `/goal STOP CONDITIONS ...` line) mirrors
# goal.py's own reader; the drift test locks that they agree on the shipped file.
# --------------------------------------------------------------------------- #

SKILL_REL = _os.path.join("skills", "autopilot", "SKILL.md")

_SHIPPED_RE = _re.compile(
    r"\*\*AUTHORITY:\s*(full|branch-merge|fork-no-merge)\*\*[^\n]*\n+```\n"
    r"(/goal STOP CONDITIONS[^\n]*)\n```",
    _re.S,
)


def skill_path():
    return _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), SKILL_REL)


def shipped_lines(skill_text):
    """Map each profile to the `/goal` line CURRENTLY shipped in SKILL.md."""
    return {m.group(1): m.group(2) for m in _SHIPPED_RE.finditer(skill_text)}


def drift(skill_text):
    """List of (profile, shipped_or_None, expected) where the shipped `/goal`
    line != render(profile). Empty list == SKILL.md matches the registry."""
    shipped = shipped_lines(skill_text)
    out = []
    for p in PROFILES:
        exp = render(p)
        got = shipped.get(p)
        if got != exp:
            out.append((p, got, exp))
    return out


def render_into(skill_text):
    """Return SKILL.md text with each profile's `/goal` line replaced by
    render(profile) (the --write regeneration; a no-op when already in sync)."""
    def repl(m):
        return m.group(0).replace(m.group(2), render(m.group(1)), 1)
    return _SHIPPED_RE.sub(repl, skill_text)


def skill_sequential_drift(skill_text):
    """[] when SKILL.md's BODY carries the canonical sequential clause
    (`_SEQUENTIAL_SATURATION`) verbatim; else a one-item error list (#1035).

    The skill body's SEQUENTIAL dispatch block (Step 3.0) must carry THIS clause
    VERBATIM — the SAME clause the sequential `/goal` variant uses. It is
    hand-maintained in the body, NOT auto-rendered; `goal-inventory --check`
    CHECK-LOCKS the two byte-identical via this function, so an UNARMED session
    (body only, not the goal line) can never disagree with the armed sequential
    goal on lane saturation. Called alongside `drift()`/`variant_check()`."""
    if _SEQUENTIAL_SATURATION not in (skill_text or ""):
        return ["SKILL.md body is missing the canonical sequential clause "
                "(goal_registry._SEQUENTIAL_SATURATION) — the Step 3.0 "
                "SEQUENTIAL dispatch block must carry it verbatim (#1035)"]
    return []


def skill_dual_drift(skill_text):
    """[] when SKILL.md's BODY carries the canonical dual dispatch-channel clause
    (`_DUAL_DISPATCH`) verbatim; else a one-item error list (#1060 L3b).

    The skill body's DUAL dispatch section must carry THIS clause VERBATIM — the
    dispatch-channel contract for a model-backend marker box (SendMessage to the
    implementer window, ticket-poll fallback, journal-and-continue when no impl
    session, never an in-session Opus worker). Hand-maintained in the body, NOT
    auto-rendered; `goal-inventory --check` CHECK-LOCKS it byte-identical here
    (alongside `drift()`/`variant_check()`/`skill_sequential_drift()`), so an
    UNARMED dual-box session reading only the body can never diverge from the
    canonical dispatch contract."""
    if _DUAL_DISPATCH not in (skill_text or ""):
        return ["SKILL.md body is missing the canonical dual dispatch clause "
                "(goal_registry._DUAL_DISPATCH) — the Step 3.0 DUAL dispatch "
                "block must carry it verbatim (#1060 L3b)"]
    return []

