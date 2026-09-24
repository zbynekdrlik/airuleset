"""cli_ticket_state — ONE total classifier for the footer buckets (#1141).

`classify(row, facts, box) -> (bucket, reason)` puts every open ticket in this
box's scope into EXACTLY one bucket and says WHY in one line. It is the single
definition behind `_partition_workable` (I/U/W — a thin wrapper over this),
behind the M step of `_split_merged_unreleased` (`leaves_to_merged`) and
behind the sub-dev `gk` count. The `--explain` surfaces of `tickets-status`,
`core-quals` and `slice-quals` (CLI wiring: `cli_ticket_explain`) print it per
ticket, so "why is I 5" is answered by the tool, not by a session decoding
labels. This module stays pure: no I/O, no process state.

Slice 1 (this module's first version) is a ZERO-behaviour-change refactor: the
precedence below reproduces the pre-#1141 label partition exactly (locked by
`tests/test_ticket_state_classify_1141.py`, a frozen-oracle parity test over
every label combination). A contradictory label set is only REPORTED
(`conflicts()`); it does not change the bucket. The precedence fixes, the P/C
buckets and the machine facts are later slices of #1141.

Buckets (`BUCKETS`): `I` workable, `U` the owner's court, `W` a third party,
`M` merged to the integration branch but not yet on main, `gk` handed off to
the gatekeeper (a reduced-authority box only).
"""

from dataclasses import dataclass
from typing import Optional

# The label predicates still live in cli_quals (re-exported by the airuleset
# facade and used across the repo). cli_quals imports THIS module lazily inside
# `_partition_workable`/`_split_merged_unreleased`, so there is no import-time
# cycle; moving the predicates here is a later #1141 slice.
import cli_quals

BUCKETS = ("I", "M", "U", "W", "gk")

# The owner-question labels (everything user-waiting except needs-acceptance,
# which is a client-message approval with its own #526/#622 routing).
_OWNER_QUESTION_LABELS = ("needs-answer", "needs-decision", "needs-owner-action")
_BOUNCE = cli_quals._PARTITION_BOUNCE_LABEL

_U_REASON = {
    "answer": "needs-answer: waiting for the owner's answer (#468)",
    "decision": "needs-decision: waiting for the owner's decision (#468)",
    "acceptance": ("needs-acceptance: the client message waits for the "
                   "owner's approval (#622)"),
    "action": "needs-owner-action: waiting for the owner's manual step (#601)",
}
_U_LABEL = {"answer": "needs-answer", "decision": "needs-decision",
            "acceptance": "needs-acceptance", "action": "needs-owner-action"}


@dataclass(frozen=True)
class Box:
    """Which box is counting. `own_stream` is the box's OWN reduced-authority
    stream (its canonical AUTHORITY_BY_USER key), or None for a full-authority
    (core / gatekeeper) box — the same value `_partition_workable` takes."""
    own_stream: Optional[str] = None

    @property
    def kind(self):
        return "slice" if self.own_stream else "core"


@dataclass(frozen=True)
class Facts:
    """Non-label facts about one ticket. `merged`: its fix is merged into the
    integration branch but not yet on main (#1083, git-derived by
    `cli_release_state`). `handed`: a reduced-authority box already handed it
    to the gatekeeper (`_slice_mine_and_handed`, #391)."""
    merged: bool = False
    handed: bool = False


def _names(labels):
    return {(lb or {}).get("name") for lb in (labels or [])
            if isinstance(lb, dict)}


def _labels_of(row):
    return row.get("labels") if isinstance(row, dict) else None


def leaves_to_merged(labels):
    """#1083: may a merged-unreleased ticket leave I/W for M? Not when it
    carries a U-class label (the owner's court beats "waiting for the cut") or
    `prio:bounce` (gk returned it for rework, it stays in I). The ONE
    definition `_split_merged_unreleased` and `classify` share."""
    return (not cli_quals._row_is_user_waiting(labels)
            and _BOUNCE not in _names(labels))


def _partition(labels, box):
    """The pre-#1141 label partition, one branch per rule, each with its
    reason. Its precedence documentation below is the former
    `cli_quals._partition_workable` docstring, moved VERBATIM (#1141) —
    that function is now a thin wrapper over `classify()`.

    --- moved verbatim from `_partition_workable` ---

    Split a `_union_open_issues`/`_slice_mine_and_handed` rows dict
    (`{number: {"number","title","createdAt","labels"}}`) THREE ways:
    `(workable, user_waiting, ops_wait)`. Both the user-waiting (#468) and the
    ops-wait (#510) buckets LEAVE `workable` — they are parked (on the user's
    answer / on an external event) and surface as the footer's `U N` / `W N`
    buckets and `--waiting` / `--ops-wait`, never in the workable count.

    ONE derivation, never independent queries: all three halves come from the
    SAME already-fetched rows, so the footer's `I N`/`U N`/`W N`, the /goal
    stop-proof's workable count, and the lane guard (which runs `core-quals`/
    `slice-quals --count`) cannot silently drift (#367/#468 lesson — the exact
    reason a search-exclusion + separate positive query was rejected). Extends
    the repo's own established client-side-partition pattern — no new mechanism.

    PRECEDENCE (#526, ROZHODNUTÉ v3): a row carrying BOTH a user-waiting AND an
    ops-wait label normally goes to `user_waiting` (a pending owner answer is the
    more actionable of the two) — EXCEPT a `needs-acceptance`-ONLY user-waiting
    row (reason == "acceptance": no needs-answer/needs-decision), which routes to
    `ops_wait` (W) instead. Once the stream has SENT the client acceptance thread
    and added `ops-wait`, the ticket is waiting on a THIRD PARTY, not a question
    for the owner — U is "čo sa ťa Claude pýta / čo máš schváliť", W is
    "odoslané, čaká tretia strana". needs-answer/needs-decision + ops-wait STAY
    in U (a pending owner answer beats a sent thread), so the override is
    acceptance-scoped. Both buckets leave `workable`, so the COUNT is identical
    either way; the precedence only decides which DISPLAY bucket (U vs W) the row
    lands in.

    #622 (owner directive 2026-08-22): a BARE `needs-acceptance` (no `ops-wait`,
    no gk-override) → U UNCONDITIONALLY. The code is merged and its only next step
    is an owner-approved client message, so it is never dispatchable-now code work
    (I = only that). This REVERSED #539's chained-I branch, which routed a bare
    needs-acceptance with no DELIVERED draft to `workable` (I) using "no delivered
    ping" as a proxy for "the stream's own chained work". #606 (2026-08-21) made
    that proxy wrong for the common case: with owner-questions delivered ONE AT A
    TIME, "no delivered ping" overwhelmingly means QUEUED-behind-others = waiting
    on the owner (→ U). The genuinely-chained case collapses into "queued in U"
    honestly (its dispatchable sibling work is its OWN ticket, still in I, so the
    loop never falsely disarms). The delivered-vs-queued distinction is now a
    DISPLAY tag only, computed on the on-demand `--waiting` path from
    `_acceptance_present_set` (delivered → `acceptance`, undelivered → `queued`) —
    it no longer routes, so this function is a PURE label partition again (no
    question-map read on the hot footer/count path).

    `needs-owner-action` (#601, the owner's own physical/manual step) routes to U
    via the `else` branch below WHEN it is the HIGHEST-precedence user-waiting
    label on the row — i.e. `_user_waiting_reason` reads `action` (no co-present
    needs-answer/needs-decision/needs-acceptance, all of which outrank it). In
    that normal case: (1) an owner-action + `ops-wait` row still lands in U
    (owner beats third-party framing — the owner is not a third party); and (2)
    it never enters the `ops_wait` bucket, so the #570 stale! W-freshness path can
    never touch it. (Since #622 a bare needs-acceptance is ALSO always U, so
    owner-action no longer differs from it on the I-vs-U axis — both are the
    owner's court.)

    Because `action` is the LOWEST precedence (deliberately, so needs-answer/
    needs-decision/needs-acceptance stay byte-exact per #507/#526), a
    PATHOLOGICAL row that ALSO carries a higher-precedence user-waiting label
    follows THAT label's routing, not action's: e.g. `needs-acceptance` +
    `needs-owner-action` + `ops-wait` reads reason `acceptance` and routes to W
    by the acceptance-scoped override (and the #507 gk-override a co-present
    `needs-acceptance` triggers applies too). Such a contradictory combo does not
    occur in practice — the byte-exact preservation of the co-present label's
    established semantics is the intended design, and a genuine owner-only-blocked
    ticket never carries a competing acceptance/answer label. The
    labelled-but-not-yet-announced defect is surfaced by the `no-action!` display
    flag (`_no_question_flagged` + `_print_issue_rows`), not by a routing gate.

    #943 (owner escalation 2026-09-08, odoo-erp #6294 APK): a NON-user-waiting
    row carrying BOTH ops-wait AND a MAINTAINER_ACTION_LABELS label
    (`needs-gatekeeper` / `ready-for-review`) routes to `workable` (action-only
    I), NOT `ops_wait` (W). Only the full-authority box can action a hand-off,
    so the hand-off label keeps the row visible in I — the #589/#636
    over-count-safe direction. The `_gk_handoff_ops_wait_flagged` function (#636)
    already DETECTED this contradictory shape and tagged it `gk_handoff!` in the
    nudge text, but the partition itself routed it to W, making it invisible to
    the gatekeeper's I count for 2 days. The override is unconditional (not
    authority-gated) because this function is a pure label partition (#622),
    and on a reduced-authority box these rows are structurally absent.

    `own_stream` (#654): the box's OWN reduced-authority stream (its canonical
    AUTHORITY_BY_USER key, `_current_user()`), or None for a full-authority box.
    An ANSWER/DECISION/ACTION row owned by a FOREIGN stream (`_stream_owner_of`
    != own_stream) is routed to `workable` (action-only), NOT `user_waiting` —
    STREAM OWNERSHIP WINS for U routing (the ROZHODNUTÉ decision): a full-authority
    (gk) box never fields another stream's owner-question, its owning box does.
    Checked FIRST, so it beats the acceptance→W / U splits. A full box
    (own_stream=None) drops every such foreign row into I; a slice box keeps its
    OWN stream rows in its own U (owner == own_stream). SCOPED to answer/decision/
    action (the enumerated ROZHODNUTÉ reasons): needs-acceptance keeps its own
    #526/#622 routing (bare → U, sent-thread+ops-wait → W) — a foreign acceptance
    is search-excluded from the obligation set anyway, so it never reaches this
    branch on the gk box (the real leak path is answer/decision/action carrying a
    gk queue label, which have no gk-override). `stream:core`/bare/unreadable →
    `_stream_owner_of` == "" → not foreign → stays U (the box's own court)."""
    if cli_quals._row_is_user_waiting(labels):
        kind = cli_quals._user_waiting_reason(labels)
        owner = cli_quals._stream_owner_of(labels)
        if kind != "acceptance" and owner and owner != (box.own_stream or ""):
            return ("I", "%s belongs to stream %s, whose own box asks the owner; "
                         "here it is action-only work (#654)"
                    % (_U_LABEL[kind], owner))
        if kind == "acceptance" and cli_quals._row_is_ops_wait(labels):
            return ("W", "needs-acceptance + ops-wait: the client thread was "
                         "sent; waiting on the client (#526)")
        return "U", _U_REASON[kind]
    names = _names(labels)
    handoff = [lb for lb in cli_quals.MAINTAINER_ACTION_LABELS if lb in names]
    verify = [lb for lb in cli_quals.SUBDEV_ACTION_LABELS if lb in names]
    if cli_quals._row_is_ops_wait(labels):
        if _BOUNCE in names:
            return ("I", "prio:bounce beats ops-wait: a returned bounce is the "
                         "stream's own rework (#1056)")
        if handoff:
            return ("I", "%s beats ops-wait: only the gatekeeper box can act "
                         "on a hand-off (#943)" % handoff[0])
        if verify:
            return ("I", "%s beats ops-wait: only the owning stream can verify "
                         "the deployed change (#1053)" % verify[0])
        return "W", "ops-wait: parked on an external event or evidence (#510)"
    if not isinstance(labels, (list, tuple)):
        return "I", "labels unreadable: kept workable (the safe side)"
    if "needs-acceptance" in names:
        # Not user-waiting ⇒ an override label is present today; the fallback
        # keeps classify() total even if that predicate ever changes.
        ov = [lb for lb in cli_quals.NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS
              if lb in names] or ["an override label"]
        return ("I", "needs-acceptance is overridden by %s: back in the "
                     "hand-off/bounce flow (#507/#1130)" % ov[0])
    if _BOUNCE in names:
        return "I", "prio:bounce: returned by the gatekeeper for rework (#313)"
    if handoff:
        return ("I", "%s: a hand-off the gatekeeper box acts on (#181/#1053)"
                % handoff[0])
    if verify:
        return ("I", "%s: verify the deployed change on a fresh PROD copy "
                     "(#1053)" % verify[0])
    return "I", "no parking label: workable"


def classify(row, facts=None, box=None):
    """Return `(bucket, reason)` for ONE ticket row (a gh `--json` dict with a
    `labels` list; any other shape is handled on the safe side). Total: every
    input gets exactly one bucket from BUCKETS and a one-line reason.

    Order (first match wins): the label partition (`_partition`, I/U/W) →
    `facts.merged` moves an I/W row that `leaves_to_merged` to M (#1083) →
    on a reduced-authority box `facts.handed` moves a remaining I row to gk
    (#391; a handed row parked in U/W stays there, as the footer counts it).
    `facts=None` stops after the label partition — the `_partition_workable`
    contract."""
    box = box or Box()
    labels = _labels_of(row)
    bucket, reason = _partition(labels, box)
    if facts is None:
        return bucket, reason
    if facts.merged and bucket in ("I", "W"):
        if leaves_to_merged(labels):
            return ("M", "fix merged to the integration branch, not yet on "
                         "main: waits for the release cut (#1083)")
        reason += "; merged, but kept here by its owner/bounce label (#1083)"
    if facts.handed and bucket == "I" and box.kind == "slice":
        return "gk", "handed off to the gatekeeper: waiting on its review (#391)"
    return bucket, reason


def conflicts(labels):
    """The contradictory label combinations on one ticket, one line each —
    REPORTED by `--explain`, never used to re-classify (slice 1). These are
    the four families the #1141 analysis measured as routinely open for hours:
    an owner question + a hand-off, ops-wait + a hand-off, verify-on-copy + a
    hand-off, and ops-wait + an owner question."""
    names = _names(labels)
    question = [lb for lb in _OWNER_QUESTION_LABELS if lb in names]
    handoff = [lb for lb in cli_quals.MAINTAINER_ACTION_LABELS if lb in names]
    ops = [lb for lb in cli_quals.OPS_WAIT_LABELS if lb in names]
    verify = [lb for lb in cli_quals.SUBDEV_ACTION_LABELS if lb in names]
    out = []
    for left, right, what in (
            (question, handoff, "an owner question AND a gatekeeper hand-off"),
            (ops, handoff, "waiting on a third party AND handed to the "
                           "gatekeeper"),
            (verify, handoff, "returned to the stream to verify AND handed to "
                              "the gatekeeper"),
            (ops, question, "waiting on a third party AND on the owner")):
        if left and right:
            out.append("%s: %s" % (" + ".join(left + right), what))
    return out


def _cell(text):
    """One TSV cell: a tab or newline in a title would split the row."""
    return " ".join(str(text or "").split())


def explain_lines(buckets, box, merged=(), handed=None, supplement=(),
                  extras=()):
    """The `--explain` text (pure — the CLI wiring is `cli_ticket_explain`).

    `buckets` maps each bucket name to the rows the command actually COUNTED
    there, so the totals line equals the counts; the per-row reason comes from
    `classify()` with the same facts (`merged` numbers, `handed` map). A row
    whose classify() bucket differs from where it was counted gets a
    `mismatch:` line — a parity break that must never be printed.
    `supplement` = question-map U rows the slice search misses (#948).
    `extras` = `(bucket, weight, reason, text)` footer contributions that are
    not ticket rows (ticketless ❓ pings in U, the task-hygiene A count in I);
    each prints as a `-` row and adds `weight` to its bucket's total."""
    merged = {int(n) for n in (merged or ())}
    handed = handed or {}
    supplement = set(supplement or ())
    totals = {b: len(buckets.get(b) or {}) for b in BUCKETS}
    out = []
    for bucket in BUCKETS:
        rows = buckets.get(bucket) or {}
        for number in sorted(rows, key=int):
            row = rows[number]
            title = row.get("title", "") if isinstance(row, dict) else ""
            if number in supplement:
                got, reason = bucket, ("a pending question ping names this "
                                       "ticket, which the slice search "
                                       "misses (#948)")
            else:
                got, reason = classify(
                    row, Facts(merged=int(number) in merged,
                               handed=bool(handed.get(number))), box)
            out.append("%s\t%s\t%s\t%s" % (number, bucket, reason,
                                           _cell(title)))
            if got != bucket:
                out.append("  mismatch: classify() says %s — a parity break, "
                           "report it on #1141" % got)
            for line in conflicts(_labels_of(row)):
                out.append("  conflict: %s; counted as %s by today's "
                           "precedence" % (line, bucket))
    for bucket, weight, reason, text in extras:
        out.append("-\t%s\t%s\t%s" % (bucket, reason, _cell(text)))
        totals[bucket] += weight
    out.append("# explain: " + " ".join(
        "%s=%d" % (b, totals[b]) for b in BUCKETS))
    return out
