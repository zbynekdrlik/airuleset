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
# in prose (#723 BATCH mode, deliberately reversing #456's continuous refill FOR
# autopilot): `saturation-core` dispatches a BOUNDED PARALLEL BATCH (up to 5
# worktree lanes, NO refill while a batch runs); `saturation-delivery` integrates
# each returned branch SERIALLY under the mutex as it returns; `compact-boundary`
# fires the compact ONLY at the DRAINED batch boundary (whole batch returned +
# integrated = ZERO live tasks) then dispatches the NEXT batch — NEVER mid-fleet
# (that breaks task handles/goal, CC #29193 unfixed as of CC 2.1.246). So dispatch
# is a BOUNDED parallel batch, integration is serial, and compact is at the clean
# boundary; the clauses cannot contradict. Tests assert compact-boundary fires at
# the DRAINED batch boundary (zero live tasks → next batch) and that the old
# continuous "compact boundary paces ONE integration per turn / parallel lanes
# keep building" framing is GONE.
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
    "header", "stop-a", "stop-b-header", "obligation", "proof",
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
    Clause("stop-b-header", PROFILES, {
        "full": "(B) BACKLOG EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
        "branch-merge": "(B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
        "fork-no-merge": "(B) SLICE EMPTY — PROVEN IN THIS TURN, NEVER CLAIMED.",
    }),
    Clause("obligation", PROFILES, {
        "full": "Every open issue THIS box is OBLIGED to action — the CORE slice (not labeled autopilot-skip, not owned by a sub-dev stream) PLUS every ticket only I can action whatever stream owns it (needs-gatekeeper, a hand-off awaiting my review/merge/close) — is resolved,",
        "branch-merge": "Every open issue ASSIGNED TO ME here not labeled autopilot-skip is MERGED via my own PR into the project's INTEGRATION branch (develop unless the project CLAUDE.md names another), no open prio:bounce for my stream,",
        "fork-no-merge": "Every issue ASSIGNED TO ME here not labeled autopilot-skip is HANDED OFF — closing it after is the maintainer's job, not mine to prove —",
    }),
    Clause("proof", PROFILES, {
        "full": "and (B) holds ONLY when my final message carries the pasted OUTPUT of both proof commands: `python3 ~/devel/airuleset/airuleset.py core-quals --count` printing exactly `0` under it (it counts EXACTLY that obligation set), AND `gh run list -b main -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND then the line `🏁 BACKLOG EMPTY: 0 open, main green` directly above the terminal `✅ DONE:` marker.",
        "branch-merge": "and (B) holds ONLY when my final message carries the pasted OUTPUT of all four proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `gh run list -b <integration> -L 1 --json conclusion --jq '.[0].conclusion'` printing exactly `success` under it, AND `git merge-base --is-ancestor <my last integration merge> origin/main && echo RELEASED` printing exactly `RELEASED` under it, AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (a `gk N`/`U N`/`W N` is parked — gatekeeper-owned/user-parked/ops-wait, not mine to wait on, never blocks 🏁; blank = unmeasurable), AND then the line `🏁 BACKLOG EMPTY: 0 open, integration green, released` directly above the terminal `✅ DONE:` marker.",
        "fork-no-merge": "and (B) holds ONLY when my final message carries the pasted OUTPUT of all three proof commands: `python3 ~/devel/airuleset/airuleset.py slice-quals --count` printing exactly `0` under it, AND `git merge-base --is-ancestor <my last merged commit> origin/main && echo RELEASED` printing exactly `RELEASED` under it (release still pending is STILL review-watch, not done), AND `python3 ~/devel/airuleset/airuleset.py tickets-status --refresh >/dev/null; python3 ~/devel/airuleset/airuleset.py tickets-status` pasted under it (a `gk N`/`U N`/`W N` is parked — gatekeeper-owned/user-parked/ops-wait, not mine to wait on, never blocks 🏁; blank = unmeasurable), AND then the line `🏁 BACKLOG EMPTY: 0 open, released` directly above the terminal `✅ DONE:` marker.",
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
        "fork-no-merge": "My authority ENDS at the hand-off: I push MY fork branches + evidence — NEVER open/merge a PR, never push upstream, never deploy, never close the issue, never touch other streams'.",
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
        "BATCH MODE, never one ticket per turn: dispatch a BATCH of up to 5 PARALLEL `isolation:worktree` autopilot-worker lanes, NO refill while a batch runs;"),
    Clause("saturation-delivery", PROFILES, {
        "full": "integrate returned branches SERIALLY under the integration mutex as they return;",
        "branch-merge": "merge returned branches into the integration branch SERIALLY under the mutex as they return;",
        "fork-no-merge": "hand off returned fork branches SERIALLY as they return;",
    }),
    Clause("prod-gate", ("full",),
        "Never gate, classify, skip, or warn based on prod-usage / events / off-air / hardware — I alone guard whether prod is live."),
    Clause("ask", PROFILES, {
        "full": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable.",
        "branch-merge": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable.",
        "fork-no-merge": "ASK the moment input is needed (it ALWAYS pings) — prefer ASK-AND-CONTINUE (`❓ ASKED` + `needs-answer` comment, end `⏳ WORKING`); `❓ NEEDS YOU` only if nothing else is workable.",
    }),
    Clause("parked", ("full",),
        "A `needs-answer`/`needs-decision`/`needs-acceptance`/`ops-wait` ticket is parked — never counted, never blocks 🏁 (paste `core-quals --waiting`/`--ops-wait`). NEVER bury a question or blame my silence."),
    Clause("night", PROFILES,
        "00:00–06:00 Europe/Bratislava: defer only while other tickets are workable; a NECESSARY question is asked even at night."),
    Clause("bounce", PROFILES, {
        "full": "Bounce lane: open tickets labeled prio:bounce jump the queue — every NEW batch seeds oldest-first (never preempting a running batch); a named nudge gets a one-line ACK + prio:bounce label, taken next turn, never worked inline.",
        "branch-merge": "Bounce lane: my prio:bounce tickets seed each NEW batch oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label next turn, never inline.",
        "fork-no-merge": "Bounce lane: my prio:bounce tickets seed each NEW batch oldest-first (never preempting a running one); a named nudge gets a one-line ACK + label (best-effort), taken next turn, never worked inline.",
    }),
    Clause("verify-sources", PROFILES, {
        "full": "Count a ticket done ONLY after verifying from primary sources — `gh pr view` (merged, closingIssuesReferences), `gh run list` (main green), `gh issue view` (closed), the deployed version on the live target — never the worker's claim alone; verify the LAST ticket as strictly as the first.",
        "branch-merge": "Count a hand-off done ONLY after verifying it from primary sources — `gh pr view` (merged into integration), that branch's CI run, the READY-FOR-REVIEW comment posted — never the worker's claim alone; verify the LAST as strictly as the first.",
        "fork-no-merge": "Count a hand-off done ONLY after verifying from primary sources — the `READY-FOR-REVIEW:` comment present (`gh issue view --json comments`), the fork branch pushed, local test/lint output shown — never the worker's claim alone; verify the LAST as strictly as the first.",
    }),
    Clause("compact-boundary", PROFILES, {
        "full": "After each integration END the turn with the full `## ✅ Work Complete` report (`completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). ONLY when the WHOLE batch has returned + integrated (ZERO live tasks — waiver #730) run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (#402) as your last tool call — the ARMED GOAL fires the NEXT TURN, compacting then dispatching the next batch; NEVER compact while lanes live (breaks tasks/goal, CC #29193 unfixed).",
        "branch-merge": "After each integration END the turn with the full `## ✅ Work Complete` report (the branch-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). ONLY when the WHOLE batch has returned + merged (ZERO live tasks — waiver #730) run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (#225) as my last tool call — the ARMED GOAL fires the NEXT TURN, compacting then dispatching the next batch; NEVER compact while lanes live (breaks tasks/goal, CC #29193 unfixed).",
        "fork-no-merge": "After each hand-off END the turn with the full `## ✅ Work Complete` report (the fork-no-merge variant, `completion-report.md`) terminating in `✅ DONE:` — CONTINUE, NEVER satisfies (B). ONLY when the WHOLE batch has returned + handed off (ZERO live tasks — waiver #730) run `python3 ~/devel/airuleset/airuleset.py compact-request --self` (#225) as my last tool call — the ARMED GOAL fires the NEXT TURN, compacting then dispatching the next batch; NEVER compact while lanes live (breaks tasks/goal, CC #29193 unfixed).",
    }),
]


def render(profile):
    """The exact `/goal ...` line for `profile`, composed from the registry."""
    if profile not in PROFILES:
        raise ValueError("unknown profile: %r" % (profile,))
    parts = [c.text_for(profile) for c in CLAUSES if profile in c.profiles]
    return "/goal " + " ".join(parts)


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

