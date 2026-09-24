"""cli_ticket_state — ONE total classifier for the footer buckets (#1141).

`classify(row, facts, box) -> (bucket, reason)` puts every open ticket in this
box's scope into EXACTLY one bucket and says WHY in one line. It is the single
definition behind `_partition_workable` (I/U/W — a thin wrapper over this),
behind the M step of `_split_merged_unreleased` (`leaves_to_merged`) and
behind the sub-dev `gk` count. The `--explain` surfaces of `tickets-status`,
`core-quals` and `slice-quals` print it per ticket, so "why is I 5" is
answered by the tool, not by a session decoding labels.

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

import argparse
import os
import sys
from dataclasses import dataclass

import cli_quals

BUCKETS = ("I", "M", "U", "W", "gk")

# The owner-question labels (everything user-waiting except needs-acceptance,
# which is a client-message approval with its own #526/#622 routing).
_OWNER_QUESTION_LABELS = ("needs-answer", "needs-decision", "needs-owner-action")
_BOUNCE = cli_quals._PARTITION_BOUNCE_LABEL
_ROLES = ("review", "infra", "quality")

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
    own_stream: str = None

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
    """The pre-#1141 label partition, one branch per rule, each with its reason.

    PRECEDENCE (#526, ROZHODNUTÉ v3): a row carrying BOTH a user-waiting AND an
    ops-wait label normally goes to U (a pending owner answer is the more
    actionable of the two) — EXCEPT a `needs-acceptance`-ONLY user-waiting row,
    which goes to W: once the stream has SENT the client acceptance thread and
    added `ops-wait`, the ticket waits on a THIRD PARTY. needs-answer/
    needs-decision + ops-wait STAY in U.

    #622: a BARE `needs-acceptance` (no ops-wait, no gk-override) → U
    unconditionally — its only next step is an owner-approved client message.
    The delivered-vs-queued distinction is a `--waiting` DISPLAY tag only.

    #601: `needs-owner-action` routes to U when it is the highest-precedence
    user-waiting label (answer > decision > acceptance > action, see
    `_user_waiting_reason`); a pathological row that also carries a higher one
    follows THAT label's routing.

    #943 / #1053 / #1056 L2: a non-user-waiting ops-wait row that ALSO carries
    `prio:bounce`, a MAINTAINER_ACTION_LABELS hand-off label or a
    SUBDEV_ACTION_LABELS label (`verify-on-copy`) stays workable I — only one
    box can act on it, so it must never be hidden in W. Label-only, the safe
    over-count direction; unconditional because this is a pure label partition.

    #654: an answer/decision/action row owned by a FOREIGN stream
    (`_stream_owner_of` != own_stream) goes to I (action-only), never this box's
    U — STREAM OWNERSHIP WINS; the owning stream's box asks the owner. Checked
    FIRST in the user-waiting branch. Scoped away from needs-acceptance.
    `stream:core`/bare/unreadable → owner "" → not foreign.

    #507/#512/#1130: a needs-acceptance row that also carries a re-hand-off
    (`ready-for-review`/`needs-gatekeeper`/`gk-processing`) or `prio:bounce` is
    NOT user-waiting — it stays I so the hand-off/bounce logic sees it.

    Unreadable labels read as NOT user-waiting and NOT ops-wait → I (never hide
    own work because of a failed label read)."""
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
        ov = [lb for lb in cli_quals.NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS
              if lb in names]
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


def explain_lines(buckets, box, merged=(), handed=None, supplement=()):
    """The `--explain` text. `buckets` maps each bucket name to the rows the
    command actually COUNTED there (so the totals line equals the counts);
    the per-row reason comes from `classify()` with the same facts. A row whose
    classify() bucket differs from where it was counted gets a `mismatch:`
    line — that would be a parity break, and must never be printed.
    `supplement` = question-map U rows the slice search misses (#948)."""
    merged = {int(n) for n in (merged or ())}
    handed = handed or {}
    supplement = set(supplement or ())
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
            out.append("%s\t%s\t%s\t%s" % (number, bucket, reason, title))
            if got != bucket:
                out.append("  mismatch: classify() says %s — a parity break, "
                           "report it on #1141" % got)
            for line in conflicts(_labels_of(row)):
                out.append("  conflict: %s; counted as %s by today's "
                           "precedence" % (line, bucket))
    out.append("# explain: " + " ".join(
        "%s=%d" % (b, len(buckets.get(b) or {})) for b in BUCKETS))
    return out


def _refuse_extra(extra):
    if extra:
        print("--explain explains the footer buckets and does not combine "
              "with --extra (that query skips the partition)", file=sys.stderr)
        sys.exit(2)


def explain_core(extra, workable, merged_rows, waiting, ops_wait, merged_set):
    """`core-quals --explain`: the full-authority box (no gk bucket — it
    actions its hand-offs itself). Called with the SAME buckets `--count`
    uses, after the role filter and the M split."""
    _refuse_extra(extra)
    for line in explain_lines({"I": workable, "M": merged_rows, "U": waiting,
                               "W": ops_wait, "gk": {}}, Box(), merged_set):
        print(line)


def explain_slice(extra, rows, workable_rows, unhandled, waiting, ops_wait,
                  merged_rows, merged_set, handed, root, user):
    """`slice-quals --explain`: the reduced-authority box. I = the unhandled
    workable rows `--count` counts, gk = the handed-off workable rows, U also
    carries the question-map supplement the footer adds (#948)."""
    _refuse_extra(extra)
    import cli_quals_cmd
    extra_u = cli_quals._question_map_u_supplement(
        rows, root, cli_quals_cmd._slice_quals_runner(root))
    gk = {n: r for n, r in workable_rows.items() if handed.get(n)}
    for line in explain_lines(
            {"I": unhandled, "M": merged_rows, "U": {**waiting, **extra_u},
             "W": ops_wait, "gk": gk},
            Box(own_stream=user), merged_set, handed, supplement=extra_u):
        print(line)


def explain_footer(cwd):
    """`tickets-status --explain`: explain the footer of the session at `cwd`.
    It runs the SAME `core-quals`/`slice-quals` derivation for this box's
    authority, with the role the footer itself resolves for `cwd` (#998), so
    the printed totals are the footer's I/M/U/W/gk."""
    import airuleset
    import cli_quals_cmd
    root = airuleset._repo_root(cwd)
    if not root:
        print("# explain: no repo at %s (the footer shows no-repo)" % cwd)
        return
    try:
        import cli_concurrency
        role = cli_concurrency.resolve_role(cwd)
    except Exception as e:  # noqa: BLE001 — the footer degrades unfiltered too
        sys.stderr.write("tickets-status: role resolve skipped (%s)\n" % e)
        role = None
    role = role if role in _ROLES else None
    full = airuleset.resolve_authority(cwd=root) == "full"
    print("# tickets-status --explain: scope=%s role=%s root=%s"
          % ("core" if full else "mine", role or "-", root))
    ns = argparse.Namespace(explain=True, role=role)
    prev = os.getcwd()
    os.chdir(root)   # the quals commands resolve the repo from the process cwd
    try:
        (cli_quals_cmd.cmd_core_quals if full
         else cli_quals_cmd.cmd_slice_quals)(ns)
    finally:
        os.chdir(prev)
