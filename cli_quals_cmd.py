"""cli_quals_cmd.py — the slice/core-quals CLI + issue-row presentation.

Extracted from airuleset.py (#433 cluster I, File B of a 2-file split). The
UPPER layer over `cli_quals.py` (File A): issue-row rendering, the empty-result
trust guard, and the `slice-quals` / `core-quals` CLI subcommands, entered only
via SUBCOMMANDS/main. Depends on File A's derivation core AND on shared
airuleset.py-resident plumbing — BOTH reached lazily via a deferred
`import airuleset` + `airuleset.X` inside the function bodies (never a
module-level import; internals #1481/#1484). airuleset.py re-exports every
name here via its facade.
"""
import json
import os
import sys


def _row_action(row, own_stream=None):
    """What THIS box may do with an issue row: `action-only` or `implement`.

    A ticket owned by a stream OTHER than this box's is in the obligation set
    because only this box can REVIEW / MERGE / CLOSE / UNBLOCK it — never
    because this box should write its code. The discriminator is deliberately
    relative to this box (`own_stream`), not absolute: a reduced-authority
    stream's own `stream:<me>` tickets ARE its to implement, and an absolute
    "carries any stream label" rule would mark every row of its own slice
    untouchable.

    A row with NO `labels` key at all is UNDETERMINABLE, not "unlabelled", and
    takes the conservative side — the same `"labels" in view` discrimination
    `_notify_run_card` already makes, and for the same reason (#181 M11: a
    failed lookup read as the negative case silently restored a pre-existing
    wrong behaviour). The two errors here are not symmetric: a sub-dev's
    ticket printed `implement` invites this box to write code on a foreign
    stream's ticket — the exact harm the column exists to prevent — while a
    core ticket printed `action-only` merely stalls visibly. `labels: []` is a
    genuinely unlabelled core ticket and stays `implement`."""
    import airuleset
    if not isinstance(row, dict) or "labels" not in row:
        return airuleset.ROW_ACTION_ONLY
    labels = row.get("labels")
    if not isinstance(labels, list) or any(not isinstance(lb, dict)
                                           for lb in labels):
        # Present but not a list of dicts (bare strings, an explicit null):
        # UNREADABLE ownership, not an absence of it. `_stream_owner_of` skips
        # non-dict entries, which would silently render this as `implement` —
        # the dangerous direction again, one layer down (adversarial review,
        # round 4).
        return airuleset.ROW_ACTION_ONLY
    owner = airuleset._stream_owner_of(labels)
    if owner and owner != (own_stream or ""):
        return airuleset.ROW_ACTION_ONLY
    return airuleset.ROW_IMPLEMENT


def _print_issue_rows(rows, own_stream=None, reason_fn=None):
    """`number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first (the bounce
    lane picks the oldest — no client-side sort needed downstream).

    The action column is third, ahead of the title, so a title containing a
    tab cannot shift it. It is the #181-round-4 fix for the SELECTION source
    emitting no ownership discriminator: `action-only` = only this box can act
    on it and it must never write its code; `implement` = ordinary work.

    `reason_fn` (#512, `--waiting` only): when given, a per-member REASON column
    is inserted BEFORE the title — `number<TAB>createdAt<TAB>action<TAB>reason
    <TAB>title` — so a `--waiting` reader sees WHY each parked ticket waits
    (answer/decision/acceptance). Field 0 stays the issue number, so existing
    `split("\\t", 1)[0]` number-parsing is unaffected."""
    for n in sorted(rows, key=lambda k: rows[k].get("createdAt") or ""):
        row = rows[n]
        action = _row_action(row, own_stream)
        if reason_fn is None:
            print("%s\t%s\t%s\t%s" % (n, row.get("createdAt") or "",
                                      action, row.get("title") or ""))
        else:
            print("%s\t%s\t%s\t%s\t%s" % (n, row.get("createdAt") or "",
                                          action, reason_fn(row.get("labels")),
                                          row.get("title") or ""))


def _print_ping_rows(ping_entries):
    """Print the ticketless ❓ pings that fold into `U N` (#512), one per member,
    each tagged `ping` in the reason column — so `--waiting`'s member list
    matches the footer's `U N` count (label members + ticketless pings). A ping
    has no issue number / createdAt / action, so those columns render `-`; the
    last column is a short one-line snippet of the question text."""
    for v in ping_entries:
        snippet = " ".join(
            str(v.get("question") or v.get("block") or "").split())[:120]
        print("-\t%s\t-\tping\t%s" % (v.get("ts") or "", snippet))


def _waiting_ping_entries(cwd=None):
    """The ticketless ❓ pings scoped to the session cwd, for `--waiting`'s ping
    listing (#512) — the SAME `statusbar.ticketless_question_pings` derivation
    the footer's `U N` count uses, so the list and the count agree. `cwd`
    defaults to the process cwd (the /goal loop runs `--waiting` from the
    session's own cwd, the same one the footer keys on). Fail-safe: any error
    (statusbar unimportable, unreadable map) yields no ping rows, never a crash
    of the stop-proof list."""
    try:
        import statusbar
        return statusbar.ticketless_question_pings(cwd or os.getcwd())
    except Exception:
        return []


def _refuse_unless_empty_is_trustworthy(cmd, quals, cwd=None):
    """Refuse (stderr + non-zero exit, nothing on stdout) unless an EMPTY
    search-derived result is TRUSTWORTHY. Shared by BOTH `/goal` stop-proof
    commands, called at the identical point.

    #181 round 4, CRITICAL. `_search_index_healthy()` (round 3) is the right
    guard for this defect class, but it was installed as an extra validation
    for ONE caller's zero rather than as a precondition on trusting ANY zero
    derived from the GitHub issue SEARCH index — one call site, nested inside
    `cmd_slice_quals` behind `len(quals) == 1 and quals[0].startswith("label:")`,
    the SHARED-account shape. Two paths walked straight past it, both
    reproduced live on dev1 2026-07-30 against the shipped code:

      * `cmd_core_quals` never called it at all. In a checkout whose `origin`
        still points at the pre-rename name (GitHub's issue SEARCH index does
        not follow a repo rename; the REST/repository-listing path does):
        REST 110 open issues, every `--search` 0, `core-quals --count` -> `0`
        with rc 0. The gatekeeper pastes that 0, writes the mandated BACKLOG
        EMPTY line, and stops with the whole backlog outstanding.
      * `cmd_slice_quals` on an OWN-account stream. `_slice_quals("david")` is
        `['assignee:@me', 'author:@me', 'label:stream:david']`, so
        `len(quals) == 1` is False and the guard was skipped in the very
        command round 3 fixed: stdout `0`, no SystemExit.

    The rename is only the cheapest trigger; ANY state where search answers
    empty while REST does not reaches the same line. Rounds 1-3 each moved the
    guard one call frame outward instead of making the refusal a property of
    the RESULT — "this zero came out of the search index, and nothing has
    shown the search index is answering" — which is why the class survived
    three fixes. One helper, two callers, one contract: a third stop-proof
    command gets it by calling this.

    Runs ONLY when the union is empty; a non-empty union is itself proof the
    index answers, so the healthy path costs no extra gh call."""
    import airuleset
    if len(quals) == 1 and quals[0].startswith("label:"):
        # C2 (round 2) — a shared-account slice's ONLY signal is one label,
        # and a forgotten/never-created label makes gh search return `[]` with
        # exit 0 for a query that can never match anything.
        label_name = quals[0].split(":", 1)[1]     # "label:stream:x" -> "stream:x"
        exists = airuleset._label_exists_on_repo(label_name, cwd=cwd)
        if exists is not True:
            print(
                "%s: cannot confirm label '%s' exists on this repo (%s) — a "
                "single-label slice of 0 resting on an unconfirmed label is "
                "UNRELIABLE. Refusing rather than reporting it (#181 C2)."
                % (cmd, label_name,
                   "not found" if exists is False else "the check itself failed"),
                file=sys.stderr)
            sys.exit(1)
    healthy = airuleset._search_index_healthy(cwd=cwd)
    if healthy is False:
        print(
            "%s: gh's SEARCH path is not answering for this identity/repo (a "
            "sort-only search that must match every open issue came back "
            "empty, or the probe itself failed, while the repository listing "
            "path does show open issues) — an empty result here is NOT "
            "evidence of an empty backlog. Refusing (#181 round 4)." % cmd,
            file=sys.stderr)
        sys.exit(1)
    # healthy is True (search demonstrably works) or None (the repo has no
    # open issues at all, so an empty result is trivially correct).


HANDOFF_LABEL_WORKFLOW_HINT = "handoff"

# A COMPLETED hand-off-labeller run whose conclusion is not one of these did
# not do its job. Stated as POSITIVE evidence rather than as a list of bad
# conclusions (adversarial review, round 4): the first cut tested
# `conclusion == "failure"` literally, so `startup_failure`, `timed_out`,
# `cancelled` and `action_required` all passed as healthy — and the live
# failing labeller this guard exists for is startup-SHAPED (its failing job
# records no failing STEP), i.e. the neighbouring spelling of its own
# motivating case. `skipped` is normal and must stay here: a labeller
# legitimately skips every comment that is not a hand-off.
HANDOFF_RUN_OK_CONCLUSIONS = frozenset({"success", "skipped"})


def _handoff_label_mechanism_health(cwd=None):
    """Is the mechanism the `ready-for-review` arm rests on actually working?
    Returns `(state, detail)` with state in `ok` / `broken` / `unknown` /
    `n/a`.

    #181 round 4. The obligation set detects an outstanding sub-dev hand-off
    by the `ready-for-review` LABEL, and a read-role collaborator gets a 403
    adding that label itself — so the arm depends ENTIRELY on the repo's own
    hand-off-label workflow. Measured on zbynekdrlik/odoo-erp 2026-07-30: the
    workflow is `active` but 23 of its last 30 runs FAILED (the 5 newest all
    failed, job `label`, startup-shaped), and the repo carries 0 open
    `ready-for-review` issues. So the arm contributes a zero while the only
    thing that can produce a non-zero is failing three runs in four — this
    ticket's own failure mode by a different road. Filed as odoo-erp #2584.

    A miss is made DETECTABLE, never guessed: the alternative is a comment
    query (`"READY-FOR-REVIEW:" in:comments`), and GitHub tokenizes quoted
    phrases so that over-matches — over-counting the obligation set is the
    never-stops failure the original ticket rejected.

    `n/a` when the repo is not enrolled in the gatekeeper<->sub-dev flow, or
    when enrollment itself cannot be determined: enrollment is a static local
    fact, and if it is unknowable then nothing here depends on the workflow.
    `unknown` (which the caller treats like `broken`, exactly as C2 treats a
    failed label probe) when the repo IS enrolled but the health probe fails —
    an unreachable gh is not evidence the mechanism is fine."""
    import airuleset
    try:
        import notify
        from watchdog import _CROSS_STREAM_REPOS as enrolled
    except Exception:
        return ("n/a", "the cross-stream registry is not resolvable here")
    try:
        name = notify.repo_name_for(cwd or os.getcwd())
    except Exception:
        name = ""
    if not name:
        return ("n/a", "the repo name is not resolvable from origin")
    if name not in enrolled:
        return ("n/a", "%s is not enrolled in the cross-stream flow" % name)

    raw = airuleset._gh_out("workflow", "list", "--all", "--json", "name,state,path",
                  cwd=cwd, timeout=20)
    try:
        flows = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh workflow list` failed on %s" % name)
    if not isinstance(flows, list):
        return ("unknown", "`gh workflow list` returned no list on %s" % name)
    match = [f for f in flows if isinstance(f, dict)
             and HANDOFF_LABEL_WORKFLOW_HINT in str(f.get("path") or "").lower()]
    if not match:
        return ("broken",
                "%s is enrolled in the cross-stream flow but carries no "
                "hand-off-label workflow, so nothing can label a hand-off"
                % name)
    inactive = [f for f in match if f.get("state") != "active"]
    if inactive:
        return ("broken", "the hand-off-label workflow is %r, not active"
                % (inactive[0].get("state"),))

    path = str(match[0].get("path") or "")
    raw = airuleset._gh_out("run", "list", "-w", path, "-L", "1",
                  "--json", "conclusion,status", cwd=cwd, timeout=20)
    try:
        runs = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh run list` failed for %s" % path)
    if not isinstance(runs, list):
        return ("unknown", "`gh run list` returned no list for %s" % path)
    if not runs:
        # An enrolled repo whose labeller has never produced a run cannot have
        # labelled any hand-off — exactly as much evidence as a failing run.
        return ("unknown", "%s has never run on %s" % (path, name))
    newest = runs[0] if isinstance(runs[0], dict) else {}
    if newest.get("status") != "completed":
        # A run still in flight has a null conclusion and is not evidence of
        # breakage; refusing on it would spin the loop for the duration of
        # every labeller run.
        return ("ok", "%s (newest run still %s)" % (path, newest.get("status")))
    if newest.get("conclusion") not in HANDOFF_RUN_OK_CONCLUSIONS:
        return ("broken", "the newest %s run concluded %r"
                % (path, newest.get("conclusion")))
    return ("ok", path)


def cmd_slice_quals(args):
    """THE single definition of "my slice" (#181) — reused verbatim by the
    reduced-authority `/goal` stop-proof templates in skills/autopilot/SKILL.md
    instead of each one hand-rolling its own `--search` string.

    Before this command existed, the templates hardcoded `--assignee @me`,
    which silently resolves to `0` on a SHARED-gh-account box (montalu/marek/
    simap — see `_slice_quals`' own docstring): `@me` there is the maintainer
    account, matching nothing assigned, so the /goal loop declared the
    backlog empty with real labelled work open. `_slice_quals()` already
    fixed this for the footer/Discord-card paths via a LIST of quals unioned
    in Python — but gh's own `--search` syntax ANDs space-joined qualifiers,
    it cannot OR them, so a caller cannot just embed that list as one
    `--search` fragment (that would silently switch an own-account stream's
    3-qual union into an intersection). This command runs the SAME per-qual
    queries + Python-side union already used by `_notify_run_card`/
    `cmd_tickets_status`, and prints only the RESULT — never a raw fragment a
    template could misuse.

    C1 (round 2, live-confirmed on dev1): this command used to build quals
    unconditionally from `_current_user()`, never consulting
    `resolve_authority()` — on a FULL-authority box (no stream at all) that
    silently built `label:stream:<linux-user>`, which matches nothing, so
    `--count` printed a clean `0` with real open work sitting untouched. It
    now REFUSES outright when this box does not resolve to a
    reduced-authority profile — never a printed count.

    C2 (round 2): on a SHARED-gh-account box (montalu/marek/simap) the
    slice is `label:stream:<user>` ALONE — a forgotten/never-created label
    makes gh search return `[]` with exit 0 for a query that can never
    match anything. A ZERO result from a single-label (shared-account)
    slice is now VALIDATED before being trusted: the label must be
    confirmed to exist on the repo, AND an `involves:@me` cross-check query
    must itself succeed (proving gh search genuinely works for this
    identity/repo, not just silently returning nothing everywhere) —
    refusing rather than trusting an unconfirmed zero.

    --count: prints an integer (0 = own UNHANDLED work is empty — the /goal
             stop-proof). #391 (2026-08-11): reversed from a raw slice count
             to own UNHANDLED work (a ticket already handed off to the
             gatekeeper — `ready-for-review`/`needs-gatekeeper`, unless
             overridden by `prio:bounce` — no longer counts), via the SAME
             shared derivation (`_slice_mine_and_handed`) `cmd_tickets_
             status`'s footer uses — the #367-established consistency guard,
             so the footer's `I N` and this stop-proof cannot silently
             drift apart. Only for the PLAIN (no `--extra`) query — see
             `--extra` below.
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, one per open
             non-skip UNHANDLED issue in the slice, OLDEST first (the bounce
             lane picks the oldest — no client-side sort needed). `action` is
             relative to THIS box, so a stream's own `stream:<me>` tickets
             read `implement` (#181 round 4).
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed). The
             handed-off exclusion still applies via a cheap LABEL-only check
             (a genuine `prio:bounce` ticket is already un-handed by that
             label's own override) — the recovery/comment-fallback
             ENRICHMENT is skipped here, since its own candidate query is
             never filtered by `extra` and could otherwise leak a ticket
             into a filtered result that does not itself match `extra`.
    No flag: prints each qual defining this box's slice, one per line
             (informational).

    A gh query failure prints to stderr and exits non-zero — NEVER prints `0`
    on failure, which would be exactly the false-empty bug this exists to
    fix.

    Authority, the slice quals and every gh query resolve against the REPO
    ROOT, not the process cwd (#181 I-5) — a project CLAUDE.md marker
    `airuleset:authority=...` was invisible to this command whenever the
    session cwd was a subdirectory, while the footer saw it, so the two
    consumers of "THE one definition" could disagree about which profile the
    box was even running."""
    import airuleset
    root = airuleset._repo_root() or None
    authority = airuleset.resolve_authority(cwd=root)
    if authority == "full":
        print(
            "slice-quals: this box resolves to FULL authority — there is no "
            "stream slice to report here. Refusing rather than printing a "
            "plausible-looking 0 (this command answers 'my slice' for a "
            "reduced-authority branch-merge/fork-no-merge stream only; "
            "#181 C1).", file=sys.stderr)
        sys.exit(1)
    user = airuleset._current_user()
    try:
        quals = airuleset._slice_quals(user, cwd=root)
    except airuleset.SliceUnresolved as exc:
        print("slice-quals: %s" % exc, file=sys.stderr)
        sys.exit(1)
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    want_waiting = getattr(args, "waiting", False)
    want_ops_wait = getattr(args, "ops_wait", False)
    if not (want_count or want_list or want_waiting or want_ops_wait):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    # -L 1000 (via `_union_open_issues`, matching core-quals — #181 M-2): a
    # single population can only be UNDER-counted by a clamp, never zeroed —
    # but the documented "0 = own unhandled work is empty" contract must not
    # silently cap either. `slug` feeds the comment-fallback recovery
    # (#391) — best-effort, "" on failure just skips that enrichment.
    slug = airuleset._repo_slug(cwd=root)
    rows, handed, failed = airuleset._slice_mine_and_handed(quals, root, slug, extra=extra)
    if failed:
        print("slice-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    if not rows:
        # Round 4: the validation is no longer nested behind the SHARED-account
        # shape. An own-account stream has THREE quals, so `len(quals) == 1`
        # was False and this command skipped its own guard entirely — a false
        # SLICE EMPTY for david@subdev whenever the search index is not
        # answering. One shared helper, identical contract in both commands.
        # This checks the RAW slice (`rows`, before subtracting handed-off) —
        # a non-empty slice that is ENTIRELY handed off is a real, trusted
        # 0-unhandled result and needs no validation (the search index
        # already demonstrably answered).
        #
        # #391 adversarial review THEORETICAL-5 (accepted residual, no known
        # reproduction): "non-empty `rows`" proves the index answered
        # SOMETHING, not that it answered COMPLETELY — a partial index
        # response returning only the already-handed subset (plausible for
        # the freshest-changed ticket, e.g. one just bounced) would still
        # clear this guard and print a clean `0`. Pre-#391 the identical
        # partial answer produced a non-zero undercount instead (loop stayed
        # alive). Not chased: moving the guard to check `unhandled` directly
        # would FALSE-refuse the legitimate all-handed case this branch
        # exists to accept (verified: `test_count_excludes_a_ready_for_
        # review_ticket` fails under that change), and no real partial-
        # index-response has ever been observed (only a full-empty one, the
        # repo-rename repro this guard was built from).
        _refuse_unless_empty_is_trustworthy("slice-quals", quals, cwd=root)
    # #468: partition the tickets parked on the USER's answer
    # (`needs-answer`/`needs-decision`) out of the workable slice — they leave
    # the workable `--count` (never double-counted as handed-off) and surface via
    # `--waiting` / the footer's `U N`. DEFAULT path only, mirroring core-quals
    # and the `not extra`-scoping of `_slice_mine_and_handed`'s own enrichment.
    # #510: also partition ops-wait (external-event/evidence) tickets out of the
    # workable slice, alongside #468's user-waiting split — both leave `--count`/
    # `--list` and surface via `--waiting`/`--ops-wait`. DEFAULT path only,
    # mirroring the `not extra`-scoping of `_slice_mine_and_handed`'s enrichment.
    if extra:
        workable_rows, waiting, ops_wait = rows, {}, {}
    else:
        workable_rows, waiting, ops_wait = airuleset._partition_workable(rows)
    unhandled = {n: v for n, v in workable_rows.items() if not handed.get(n)}
    if want_ops_wait:
        # #526: tag each W member `acceptance` (client thread sent) vs `ops-wait`
        # (external event/evidence) so they are distinguishable in the listing.
        _print_issue_rows(ops_wait, own_stream=user,
                          reason_fn=airuleset._ops_wait_reason)
        return
    if want_waiting:
        # #512: each labeled member gets a reason tag (answer/decision/
        # acceptance), then the ticketless ❓ pings (reason=ping) so the list
        # matches the footer's `U N` count.
        _print_issue_rows(waiting, own_stream=user,
                          reason_fn=airuleset._user_waiting_reason)
        _print_ping_rows(_waiting_ping_entries())
        return
    if want_count:
        print(len(unhandled))
        return
    _print_issue_rows(unhandled, own_stream=user)


def cmd_core_quals(args):
    """The full-authority box's OBLIGATION set: every open, non-skip issue
    THIS box must action before its `/goal` loop may stop (#181, round 3).

    That is the CORE slice — the backlog minus every reduced-authority
    stream's own `stream:<user>` tickets, the SAME exclusion the footer
    (`cmd_tickets_status`) and the Discord run-card (`_notify_run_card`) use
    — UNIONED with every open ticket carrying a MAINTAINER_ACTION_LABELS
    label, whatever stream owns it. See `_obligation_quals()` for why the
    core partition alone is the wrong set (it excluded odoo-erp #2396/#2377,
    `stream:montalu` + `needs-gatekeeper`, which only this box can move) and
    why this is not a revert to the whole-repo count.

    NOTE: this IS the same number the footer renders as `I N` on a
    full-authority box (#367, 2026-08-11) — `cmd_tickets_status`'s
    full-authority branch calls this SAME `_obligation_quals()`/
    `_union_open_issues()` derivation, never a parallel narrower one, so the
    footer and this stop-proof cannot silently disagree about what "done"
    means. (Before #367 they deliberately differed — the footer showed only
    the narrower core partition plus a separate `· streamy M` badge for the
    hidden population; both were dropped along with the split, which removed
    the reason to keep the two numbers apart.)

    --count: prints an integer (0 = nothing left for this box to action).
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first —
             so the skill's backlog SELECTION and its stop-proof read the same
             set. The `action` column is `action-only` for a ticket a SUB-DEV
             stream owns (review / merge / close / unblock it, NEVER write its
             code) and `implement` otherwise (#181 round 4): the discriminator
             lives in the data the worker reads, not in a prose clause it may
             never have loaded.
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed) AND unions in
             the BARE `extra` query alone (#307: `prio:bounce` is no longer
             one of MAINTAINER_ACTION_LABELS, so the per-qual AND can no
             longer find a ticket carrying ONLY `extra` — the common bounce
             shape) — this no longer mirrors `slice-quals`'s pure-AND
             contract; a caller passing `--extra` gets base∧extra (every
             non-skip, non-autopilot-skip ticket matching `extra`, a
             SUPERSET of obligation∧extra), each row still marked
             `action-only`/`implement`. Without it the full-authority bounce
             seed went through a raw `gh issue list`, so the single
             highest-priority SELECTION path was the one path with neither
             this command's guard nor its ownership column — while the
             oldest open `prio:bounce` ticket on odoo-erp is #2150,
             `stream:david`.
    No flag: prints each qual whose union defines the obligation set.

    A gh query failure prints to stderr and exits non-zero — NEVER prints a
    number on failure (mirrors `slice-quals`'s own contract), and an EMPTY
    result is refused unless it is demonstrably trustworthy — see
    `_refuse_unless_empty_is_trustworthy` (#181 round 4, CRITICAL: this
    command never consulted the search-index guard at all, so a repo whose
    search index answers empty while its REST listing does not produced a
    clean stop-proof `0` with the whole backlog open)."""
    import airuleset
    root = airuleset._repo_root() or None
    authority = airuleset.resolve_authority(cwd=root)
    if authority != "full":
        # I-3: C1's fix, applied in the mirror direction. `slice-quals`
        # correctly refuses on a full box; this one answered on ANY box, so
        # run on montalu it printed a number that is neither that box's slice
        # nor a valid stop-proof for it.
        print(
            "core-quals: this box resolves to %s authority — the core/"
            "obligation slice is a FULL-authority (core / gatekeeper) "
            "question. Use `slice-quals` here. Refusing rather than printing "
            "a plausible-looking number (#181 I-3)." % authority,
            file=sys.stderr)
        sys.exit(1)

    quals = airuleset._obligation_quals()
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    want_waiting = getattr(args, "waiting", False)
    want_ops_wait = getattr(args, "ops_wait", False)
    if not (want_count or want_list or want_waiting or want_ops_wait):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    if isinstance(extra, str):
        # A whitespace-only --extra is truthy but carries no real qualifier —
        # left unstripped it still passes `if extra:` below and the new
        # bare-extra branch would union in a BARE "-label:autopilot-skip"
        # query, the exact whole-repo never-stops shape #181 rejected
        # (adversarial review of #307).
        extra = extra.strip() or None
    base = airuleset.AUTOPILOT_SKIP_EXCL + ((" " + extra) if extra else "")
    search_quals = quals
    if extra:
        # #307: `prio:bounce` is no longer one of MAINTAINER_ACTION_LABELS,
        # so a per-qual AND (base + each obligation qual) can never find a
        # ticket that carries ONLY `extra` (e.g. a bare `prio:bounce`, no
        # core membership, no `needs-gatekeeper`, no `ready-for-review`) —
        # the common real shape the bounce-lane SEED (Step 3.1) depends on.
        # The old code found it BY ACCIDENT (prio:bounce AND'd with itself
        # degenerates to itself); this restores that coverage explicitly, by
        # unioning in the BARE `extra` query (qual="" -> search=base alone)
        # alongside the per-qual AND queries. Safe only because `extra` is
        # non-empty here — with no `extra` this branch never runs, so the
        # plain obligation proof never gets the bare whole-repo query #181
        # rejected.
        search_quals = quals + [""]
    seen, failed = airuleset._union_open_issues(search_quals, base, cwd=root)
    if failed:
        print("core-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    # #468: partition the tickets parked on the USER's answer
    # (`needs-answer`/`needs-decision`) OUT of the workable obligation set — they
    # are the user's responsibility, not this box's (the loop can do nothing with
    # them until the user answers), so they never count toward the workable-0
    # stop-proof and never block 🏁; `--waiting` LISTS them so nothing is hidden.
    # ONLY on the DEFAULT path: a `--extra` (bounce-seed) query is a different
    # axis and keeps its full set, mirroring the `not extra`-scoping of the
    # empty-refusal and hand-off-health gates below. ONE partition of the SAME
    # already-fetched rows — never a second gh query that could drift (#367).
    # #510: ops-wait (external-event/evidence) tickets also leave the workable
    # obligation set, alongside #468's user-waiting split — both surface via
    # `--waiting`/`--ops-wait`, never the workable-0 stop-proof. ONE partition of
    # the SAME already-fetched rows; DEFAULT path only (a `--extra` bounce-seed
    # query is a different axis, keeping its full set, per the gates below).
    if extra:
        workable, waiting, ops_wait = seen, {}, {}
    else:
        workable, waiting, ops_wait = airuleset._partition_workable(seen)
    if not seen:
        _refuse_unless_empty_is_trustworthy("core-quals", quals, cwd=root)
    if not seen and not extra:
        # The `ready-for-review` arm of this set rests ENTIRELY on the repo's
        # own hand-off-label workflow (a read-role stream gets a 403 adding
        # the label itself). A zero that rests on a mechanism which may have
        # MISSED a hand-off is not evidence — same shape as C2's "validate
        # the evidence's own existence before trusting its absence of hits".
        #
        # `not extra` is load-bearing: with a filter (`--extra
        # "label:prio:bounce"`, the bounce seed) the question asked is "any
        # open bounce ticket?", and "none" is an ordinary answer that does not
        # depend on hand-off labels at all. Gating that would refuse a
        # legitimate result and spin the loop forever on the one repo the
        # cross-stream flow runs on — the mirror of this ticket's own bug, not
        # a safer version of it. The arm belongs to the UNFILTERED obligation
        # set, which is what the stop-proof reads. (The search-index guard
        # above stays unconditional: a dead index makes a FILTERED answer just
        # as meaningless as an unfiltered one.)
        health, detail = _handoff_label_mechanism_health(cwd=root)
        if health not in ("ok", "n/a"):
            print(
                "core-quals: the obligation set is empty, but the "
                "`ready-for-review` arm rests on this repo's hand-off-label "
                "workflow and that mechanism is %s (%s) — a hand-off can be "
                "outstanding with no label, so this 0 is NOT evidence. "
                "Refusing (#181 round 4)." % (health, detail),
                file=sys.stderr)
            sys.exit(1)
    if want_ops_wait:
        # own_stream=None: a full-authority box owns no stream, so EVERY
        # stream-labelled row is action-only. #526: tag each W member
        # `acceptance` (client thread sent) vs `ops-wait` (external event).
        _print_issue_rows(ops_wait, own_stream=None,
                          reason_fn=airuleset._ops_wait_reason)
        return
    if want_waiting:
        # own_stream=None: a full-authority box owns no stream, so EVERY
        # stream-labelled row is action-only. #512: reason tag per labeled
        # member (answer/decision/acceptance) + the ticketless ❓ pings
        # (reason=ping), matching the footer's `U N` count.
        _print_issue_rows(waiting, own_stream=None,
                          reason_fn=airuleset._user_waiting_reason)
        _print_ping_rows(_waiting_ping_entries())
        return
    if want_count:
        print(len(workable))
        return
    # own_stream=None: a full-authority box owns no stream, so EVERY
    # stream-labelled row in its obligation set is action-only.
    _print_issue_rows(workable, own_stream=None)
