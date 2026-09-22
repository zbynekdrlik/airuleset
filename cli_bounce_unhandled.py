"""cli_bounce_unhandled -- the refresh-time UNHANDLED-bounce derivation (#1066 lane A).

The footer's `· bounce K` (`entry["bounce"]`) and `slice-quals --bounces` are
READ-only LABEL counts: nothing distinguishes a bounce the stream already ACKed
from one whose newest gk `BOUNCE` verdict is newer than anything the stream
wrote. This leaf computes THAT unhandled subset ONCE, on the tickets-status
refresh path the footer already runs, via the composer's own trusted gk-watch
primitive (`cli_gk_watch.watch_issue`), and stores it as

    entry["bounce_unhandled"] = [{"number": N, "verdict_ts": <epoch>}, ...]

consumed by `slice-quals --bounces --unhandled` (this same derivation, #367 one-
derivation), the Stop hook `gates.bounce_unhandled` (a CACHE read, no gh on the
Stop path), and -- lane B -- the `/goal` clause + the watchdog BOUNCE nudge.
`entry["bounce"]` is UNCHANGED.

Design note (#1066): the design named `gates.ghread.read_comment_bodies` as the
per-member reader, but that returns comment BODIES only, while `watch_issue`
needs full `{id, body, login, created_at}` rows (login to split gk vs stream,
created_at to order). This leaf drives `watch_issue` through
`airuleset.gk_watch_issue`'s reader (`_infra_ticket_comments`) -- the SAME
REST-first paginated reader the composer already trusts -- so the unhandled
state is genuinely ONE derivation, REST-first, one call per bounce member, under
the shared gh-rate budget guard.

FAIL-OPEN, in two distinct shapes (#1066 review R1):
  * WHOLE-SLICE failure (rate-guard low budget / unresolvable slug / EVERY
    member read errored) -> None, so the caller leaves the cache field ABSENT
    (never a false '0 unhandled' across the board);
  * PER-MEMBER failure (one member's gh read errored while others succeeded) ->
    that member is OMITTED from the reported list (its state is unmeasurable, so
    it is never reported as unhandled — which would false-BLOCK — nor claimed
    handled) and LOGGED to stderr, and a CONFIRMED-unhandled member is NEVER
    dropped for a sibling's failure. So a per-member failure yields a
    field-PRESENT list that omits the unreadable member; it self-heals on the
    next refresh (which usually reads it). The residual gap (a PERSISTENTLY
    unreadable bounce stays invisible to the Stop gate) is surfaced by the
    stderr log, never silent.
"""
import sys


# The Stop-hook grace shared with `gates.bounce_unhandled` (30 min after the
# verdict -- a bounce younger than this is not yet blockable). One source of
# truth so the refresh derivation and the gate can never disagree.
BOUNCE_GRACE_SECONDS = 1800


def _bounce_label():
    """The returned-bounce label — the ONE source of truth `cli_quals` keys the
    footer's `bounce` count on (`_GK_HANDOFF_BOUNCE_OVERRIDE`), imported LAZILY
    (only on the refresh path, never the Stop path) so the footer's `bounce`
    count and this unhandled subset can never drift on a future rename (#1066
    review R2). A safe literal fallback keeps the leaf importable if cli_quals is
    ever unavailable — cli_quals is a core module present in every real call."""
    try:
        from cli_quals import _GK_HANDOFF_BOUNCE_OVERRIDE
        return _GK_HANDOFF_BOUNCE_OVERRIDE
    except Exception:
        return "prio:bounce"


def _bounce_numbers(*buckets):
    """The set of `prio:bounce` member numbers across the given partition
    buckets (each a `{number: {"labels": [...]}}` dict). A missing/malformed
    labels value counts as no-bounce (the safe direction, mirroring
    `cli_quals._count_bounce`)."""
    label = _bounce_label()
    nums = set()
    for bucket in buckets:
        for num, row in (bucket or {}).items():
            labels = row.get("labels") if isinstance(row, dict) else None
            names = {(lb or {}).get("name") for lb in (labels or [])
                     if isinstance(lb, dict)}
            if label in names:
                try:
                    nums.add(int(num))
                except (TypeError, ValueError):
                    continue
    return nums


def unhandled_from_fetch(numbers, *, fetch, gk_login, self_login,
                         now=None, is_own_login=None):
    """Drive `cli_gk_watch.watch_issue` per member over the injected `fetch`
    (the REST reader / test seam) and return `(entries, read_ok, unreadable)`:

      * `entries` -- `[{"number": N, "verdict_ts": <epoch>}, ...]` for every
        member whose state is `bounce-unanswered` (the newest gk BOUNCE is newer
        than the stream's last own comment/RFR), ORDERED by number;
      * `read_ok` -- True iff at least one member's read succeeded (state !=
        `unknown`). The caller returns None (field ABSENT) when NO read succeeded
        (a whole-slice gh failure), so a transient error never reads as '0
        unhandled' (fail-open);
      * `unreadable` -- the member numbers whose read errored (state `unknown`).
        A per-member error OMITS that member (its state is unmeasurable) but
        NEVER drops a CONFIRMED-unhandled sibling; the caller logs `unreadable`
        so a persistently-unreadable bounce is surfaced, not silent (#1066 R1).

    `head_ts_fn` is deliberately None: `bounce-unanswered` depends only on the
    newest-BOUNCE-vs-last-RFR timestamps, never on the PR head, so no per-member
    PR-head lookup is spent on the hot refresh path."""
    import cli_gk_watch
    entries, read_ok, unreadable = [], False, []
    for n in sorted({int(x) for x in (numbers or [])}):
        st = cli_gk_watch.watch_issue(
            n, fetch=fetch, gk_login=gk_login, self_login=self_login,
            head_ts_fn=None, now=now, is_own_login=is_own_login)
        state = st.get("state") if isinstance(st, dict) else None
        if state == "unknown" or state is None:
            unreadable.append(n)
            continue
        read_ok = True
        if state == "bounce-unanswered":
            gl = st.get("gk_latest") or {}
            entries.append({"number": n, "verdict_ts": gl.get("created_at")})
    return entries, read_ok, unreadable


def derive_numbers(numbers, *, cwd, slug, now=None, fetch=None, gk_login=None,
                   self_login=None, is_own_login=None, rate_guard=True):
    """The unhandled-bounce list for `numbers`, or None when the read could not
    run trustworthily (rate-guard low budget / unresolvable slug / EVERY member
    read failed) -> the caller leaves the cache field ABSENT (fail-open).

    Production wiring (when `fetch` is None): the REST-first paginated comment
    reader `airuleset._infra_ticket_comments` (the SAME one `gk_watch_issue`
    feeds `watch_issue`), the maintainer/stream logins, the app-aware
    `_is_own_login` matcher, all resolved from airuleset -- so nothing is added
    to airuleset.py and the derivation stays the composer's ONE trusted path.
    `rate_guard` skips the whole derivation when the shared core gh budget is
    below the poll floor (mirrors `gk_watch_issue`); an explicit CLI invocation
    passes `rate_guard=False`.

    Why this wires `watch_issue` directly rather than calling `gk_watch_issue`
    (#1066 review R2): `gk_watch_issue` does a per-member `_pr_head_commit_ts`
    (`gh pr list`) call that `bounce-unanswered` does NOT need (`head_ts_fn=None`
    here), and it re-checks the rate guard per member; this leaf skips the PR-head
    call and hoists ONE rate-guard check for the whole batch. `gk_watch_issue`
    exposes no head-skip knob, and adding one would edit airuleset.py (at its
    ratchet ceiling) beyond this lane's single call site — so the fetch reader,
    logins and matcher are RESOLVED from the same airuleset/cli_quals sources
    (never re-implemented), keeping the divergence to the head-skip + batching."""
    nums = sorted({int(x) for x in (numbers or [])})
    if not nums:
        return []                        # no bounces in the slice -> truthful 0
    if rate_guard and fetch is None:
        try:
            import cli_gh_rate
            if cli_gh_rate.backoff_seconds(
                    "core", cli_gh_rate.read_status()) > 0:
                return None               # low budget -> absent (fail-open)
        except Exception as _e:           # a guard error must never block a read
            sys.stderr.write(
                "bounce-unhandled: rate-guard skipped (%s)\n" % _e)
    import airuleset
    if fetch is None:
        if not slug:
            return None                   # unresolvable slug -> absent
        fetch = lambda iss: airuleset._infra_ticket_comments(  # noqa: E731
            iss, cwd, slug=slug)
    if gk_login is None:
        gk_login = airuleset.MAINTAINER_GH_LOGIN
    if self_login is None:
        self_login = airuleset._stream_self_login()
    if is_own_login is None:
        try:
            from cli_quals import _is_own_login
            is_own_login = _is_own_login
        except Exception as _e:           # matcher optional -> exact-match default
            sys.stderr.write(
                "bounce-unhandled: own-login matcher unavailable (%s)\n" % _e)
            is_own_login = None
    entries, read_ok, unreadable = unhandled_from_fetch(
        nums, fetch=fetch, gk_login=gk_login, self_login=self_login,
        now=now, is_own_login=is_own_login)
    if unreadable:
        # A per-member gh failure omits that member (unmeasurable state) but does
        # NOT drop the confirmed-unhandled siblings; surface it so a PERSISTENTLY
        # unreadable bounce is never silent (#1066 review R1).
        sys.stderr.write(
            "bounce-unhandled: %d member(s) unreadable this refresh (%s) — "
            "omitted (state unmeasurable), confirmed members still reported\n"
            % (len(unreadable), ",".join("#%d" % n for n in unreadable)))
    return entries if read_ok else None    # no read succeeded -> absent


def derive_at_refresh(workable, waiting, ops_wait, *, cwd, slug, **kw):
    """The unhandled-bounce list for the FULL footer partition (workable ∪
    user-waiting ∪ ops-wait) -- the SAME buckets `count_bounce_all` counts, so
    the footer's `bounce K` and its unhandled subset are ONE derivation (#367).
    Returns None on a read failure (caller leaves the field absent)."""
    return derive_numbers(_bounce_numbers(workable, waiting, ops_wait),
                          cwd=cwd, slug=slug, **kw)


def attach_unhandled(entry, workable, waiting, ops_wait, cwd, slug):
    """Set `entry["bounce_unhandled"]` from `derive_at_refresh`, but ONLY when
    the read succeeded -- a None result (gh error / low budget) leaves the field
    ABSENT so every consumer fails open (no false '0 unhandled'). The whole call
    is best-effort: any error is logged and the field is left absent, so the
    footer refresh can never crash on this addition (#133 payload-coercion)."""
    try:
        got = derive_at_refresh(workable, waiting, ops_wait, cwd=cwd, slug=slug)
    except Exception as _e:               # never crash the footer refresh
        sys.stderr.write("bounce-unhandled: derivation skipped (%s)\n" % _e)
        return
    if got is not None:
        entry["bounce_unhandled"] = got
