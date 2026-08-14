"""watchdog/cards.py -- per-ticket completion-card reconciliation (job 25,
#134) plus the shared git-primitive trio it (and job 24) run on
(#433, module split cluster F).

WHY THIS FILE EXISTS. Extracted VERBATIM (a MOVE, not a rewrite -- same
discipline as #404's `watchdog/usage.py` and #433's own `watchdog/burn_jobs.py`)
from `watchdog/__init__.py` as part of #433's continuation of #404's
per-service module split: `card_reconcile` (job 25) plus its own
`merged_closes`/`_normalize_closed`/`_commits_in_window` helpers detect a
ticket that closed with no delivered Discord run-card, purely from local
git -- a self-contained cluster called from exactly ONE place, `run_once()`'s
own job dispatch, and never called BY any other watchdog job.

THE SHARED GIT-HELPER TRIO (`_default_git_run`/`_git_first_line`/
`_git_base_ref`) moved here TOO, even though this file's own module
docstring only needs to explain job 25: at the time this file was
extracted, all three were genuinely shared with job 24's `delivery_state`/
`_repo_label` (then still resident in `watchdog/__init__.py`, cluster E,
not yet extracted) -- `_default_git_run`'s own docstring says so explicitly
("job 24's `_git_first_line`/`delivery_state`, job 25's `merged_closes`").
That was NOT the circular-import hazard a coupled cluster like C/D would
have: the dependency direction was the SAFE one -- this file needs nothing
back from `watchdog/__init__.py` (only stdlib `re` + a locally-imported
`subprocess` + locally-imported `notify` package functions, unchanged from
before the move). Cluster E has SINCE been extracted too, into
`watchdog/repo_health.py` -- `_git_commit_ts`, `delivery_state`, and
`_repo_label` no longer live in `watchdog/__init__.py` at all;
`repo_health.py` imports `_git_first_line`/`_git_base_ref` directly from
this module (`from watchdog.cards import _git_first_line, _git_base_ref`),
a plain leaf-to-leaf forward import, exactly the shape this docstring
predicted would be safe when it was written.

MONKEYPATCH SEAM NOTE (#433 cluster-F review MINOR-2, updated after cluster
E's own extraction): before cluster F's own move, `watchdog._default_git_run
= spy` reached every caller of the trio, since they all resolved it against
the SAME module's globals. After cluster F it resolves against
`watchdog.cards.__dict__` instead -- a package-level patch of
`watchdog._default_git_run` no longer reaches `_git_first_line` (here) or
any caller now resident in `watchdog.repo_health` (`_git_commit_ts`/
`delivery_state`/`_repo_label`). No existing test does this (verified: no
test patches these names at all, in either module -- every test that needs
control injects `git_run=` instead, which is unaffected) -- cluster E's own
extraction did not add such a test either, so this remains a latent
seam-narrowing rather than a live break.

Re-exported from `watchdog/__init__.py` (`from watchdog.cards import ...`,
placed after every symbol this cluster depends on is already defined -- it
depends on none) so every existing caller (`run_once()`'s job 25 dispatch,
calling `card_reconcile` as a bare module-global name, and the test suite's
`wd.card_reconcile`/`watchdog.card_reconcile` attribute access) needs zero
changes. Same facade pattern as `watchdog/usage.py` (#404 cluster A) and
`watchdog/burn_jobs.py` (#433 cluster B): thin re-export block, `X as X`
syntax for ruff, own module docstring, zero behavior change.
"""

import re


def _default_git_run(argv, timeout=10):
    """The default `git_run` callable for every `git -C <cwd> ...` helper in
    this module (job 24's `_git_first_line`/`delivery_state`, job 25's
    `merged_closes`) -- a thin `subprocess.run` wrapper returning stdout on
    success, `None` on any failure (non-zero exit, timeout, missing `git`).
    Genuinely shared: NOT compact-owned, even though it used to sit inside
    the (now-deleted, #402) compact block purely by physical proximity to
    `compact_boundary_substantial`'s own `_git_commit_count_since` helper,
    which WAS compact-only and is gone with it."""
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _git_first_line(cwd, argv, git_run=None):
    """One `git -C <cwd> …` call, stripped. None on any failure OR empty
    output — never a partial guess."""
    out = (git_run or _default_git_run)(["git", "-C", str(cwd)] + list(argv))
    if out is None:
        return None
    return out.strip() or None


def _git_base_ref(cwd, git_run=None):
    """The repo's DELIVERY ref — the branch a merge lands on. `origin/HEAD`
    is authoritative when the checkout has it; the explicit fallbacks cover a
    clone that never resolved it. None when neither exists (then the repo is
    unmeasurable and job 24 stays silent about it)."""
    ref = _git_first_line(cwd, ["symbolic-ref", "--quiet",
                                "refs/remotes/origin/HEAD"], git_run)
    if ref and ref.startswith("refs/remotes/"):
        return ref[len("refs/remotes/"):]
    for cand in ("origin/main", "origin/master"):
        if _git_first_line(cwd, ["rev-parse", "--verify", "--quiet",
                                 cand + "^{commit}"], git_run):
            return cand
    return None


# --------------------------------------------------------------------------- #
# Job 25 — CARD RECONCILIATION (#134, marek's stream 2026-07-23 → 2026-07-28).
#
# Measured: the `claude-marek` thread received ZERO per-ticket completion
# cards for five days while tvdole closed 6 issues / merged 5 PRs and
# parovanie-produktov closed 97 / merged 80. The last dedup marker for that
# repo is `#192` at 2026-07-23 19:05; issues #193–#295 produced none, and
# tvdole has never produced one at any point. Nothing was broken — the
# mechanism is healthy and zbynek-owned repos card through today. The card is
# simply an ACTION WITH NO ARTIFACT ANYONE CHECKS, so workers drifted out of
# the habit and nothing pushed back.
#
# WHY THIS EXISTS ALONGSIDE THE SubagentStop GATE. The gate
# (`hooks/subagent-stop-check-run-card.sh`) is the in-band half and it is the
# one that restores a REAL card, with the worker's own plain-Slovak goal and
# achieved text. But it is structurally blind to two of the ways this fails:
# a worker that DIES mid-run never reaches SubagentStop at all, and a
# delivery that fails AFTER the worker returned happens when no hook is
# looking. Both collapse to one observable — an issue closed by a merge with
# no delivered card — which is what this job measures.
#
# WHAT IT READS, AND WHY NOT `gh`. Purely LOCAL git, per repo hosting a live
# pane: the merge commits that landed on the base branch inside the window,
# and the `Closes/Fixes/Resolves #N` they carry. That is the same fact a `gh`
# query would return, for free, without spending an API call per repo per
# sweep against a 5,000/h budget and without needing auth in every checkout.
#
# RELATION TO JOB 24 (#138), checked before adding rather than after. They do
# not overlap and in the common case are mutually exclusive BY CONSTRUCTION:
# job 24 fires when the base branch is FROZEN (merges stopped); job 25 fires
# when the base branch MOVED (merges happened) and the reports did not. They
# share no state key and answer different questions.
#
# Detection only, exactly like jobs 21 and 24: it never types into a pane and
# never touches a worktree, index or local branch. Deciding what to do about
# an unreported ticket is the user's call.
# --------------------------------------------------------------------------- #

CARD_WINDOW_S = 172800            # merges older than 48h are history, not a gap
CARD_GRACE_S = 1200               # a ticket younger than this hasn't had time
                                   # to be carded yet (#224) — the worker's own
                                   # post-merge sequence (deploy, verify, THEN
                                   # the card) takes minutes by design, and the
                                   # original code had no minimum age at all,
                                   # so it beat the worker's own card by
                                   # seconds on every clean run
CARD_MAX_LISTED = 8               # the phone gets numbers, not a wall

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
                        re.I)


def merged_closes(root, base, since_ts, git_run=None):
    """`{issue_num: commit_epoch_ts}` for every issue closed by a commit on
    `base` since `since_ts`.

    GitHub closes an issue from a `Closes #N` in a commit reachable from the
    default branch, so this is the delivery event itself, read locally — and
    the commit's OWN timestamp is carried forward so `card_reconcile` can
    gate a fresh merge with a per-TICKET grace period (#224), not just the
    window's edge. A record separator (`%x1e`) marks the start of each
    commit's block and a field separator (`%x1f`) splits its timestamp from
    the message, so a `Closes #N` mentioned in either the subject or the
    body is still found — the whole message stays in the same record. When
    the same issue is mentioned in more than one commit within the window
    (rare), the MOST RECENT commit's timestamp wins — the newest mention is
    the honest "when did this actually settle" answer."""
    text = (git_run or _default_git_run)(
        ["git", "-C", str(root), "log", base,
         "--since=@%d" % int(since_ts), "--format=%x1e%ct%x1f%s%n%b"])
    result = {}
    for rec in (text or "").split("\x1e"):
        ts_str, sep, rest = rec.partition("\x1f")
        if not sep:
            continue
        try:
            ts = float(ts_str)
        except (TypeError, ValueError):
            continue
        for m in _CLOSES_RE.finditer(rest):
            n = int(m.group(1))
            result[n] = max(ts, result.get(n, ts))
    return result


def _normalize_closed(value):
    """Normalize what a `closed_fetch` callback returns into the same
    `{issue_num: ts_or_None}` shape `merged_closes` produces.

    Accepts a `{n: ts}` dict, a list of `(n, ts)` pairs, or a bare list of
    ints (the pre-#224 contract some callers — and this job's own existing
    tests — still use). A bare int gets `ts=None`, and `card_reconcile`
    treats an unknown timestamp as ALREADY past grace: not knowing when a
    ticket closed must never suppress a report the pre-grace code would
    have sent instantly — "report now" is the safe default, never "wait
    forever for a timestamp that will never arrive"."""
    if isinstance(value, dict):
        return {int(k): (None if v is None else float(v))
                for k, v in value.items()}
    out = {}
    for item in value or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            n, ts = item
            out[int(n)] = None if ts is None else float(ts)
        else:
            out[int(item)] = None
    return out


def _commits_in_window(root, base, since_ts, git_run=None):
    out = _git_first_line(root, ["rev-list", "--count",
                                 "--since=@%d" % int(since_ts), base], git_run)
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def card_reconcile(now, run, state, cwd_by_sid, send_fn=None, dry_run=False,
                   git_run=None, card_probe=None, marker_ok=None,
                   owner_by_sid=None, window=None, grace=None,
                   closed_fetch=None, reopen_fetch=None):
    """Job 25 — see the section comment.

    Gated on `card_probe` (the "wired = on" convention of jobs 8/11/16/24):
    the probe carries the confirming fetch, so a base ref that had merely
    gone stale locally cannot on its own claim a ticket went unreported.

    Two independent fixes over the original #134 shape, both from live
    false positives measured on this box (#224):

      * GRACE PERIOD, per ticket. The original code had no minimum age at
        all — only `window`, a MAXIMUM age — so it beat the worker's own
        delivered card by seconds on every clean run (merge closed 3
        tickets at 11:46:21, this job pinged "unreported" at 11:46:24,
        the worker's cards were `sent` at 11:49:06-11:49:14). A ticket is
        only "pingable" once `now - <its own closing commit's timestamp>
        >= grace` — read from `merged_closes`'s per-ticket dict, never
        derived from the window's edge, so the grace genuinely applies
        per ticket rather than per repo (two tickets in the same repo, one
        old and one fresh, are judged independently in the same sweep).
      * DEDUP, per ticket, forever — not per repo per calendar day. A
        ticket whose card can never arrive (the worker died, or the issue
        closed with no PR at all) used to be re-announced once a day for
        as long as it sat inside the 48h window, and any NEWLY stuck
        ticket restarted that clock for every OTHER ticket in the same
        repo (834 consecutive sweeps of the same six camera-box tickets,
        ~14h). `state["card_unreported"][root]["pinged"]` now remembers
        every issue number this job has ever pinged for that repo — a
        ticket earns exactly one nag, and a genuinely NEW unreported
        ticket still pings immediately, even in the sweep that just
        deduped an old one. Entries are pruned once their ticket ages out
        of `closed` (the 48h window), so the memory stays bounded.

    One repo is examined once per sweep however many panes sit in it, and
    DETECTION is logged every sweep regardless of grace/dedup (issue #36's
    print-always convention — the log is a local journal line, never the
    thing that reaches the user's phone)."""
    if card_probe is None:
        return []
    window = CARD_WINDOW_S if window is None else window
    grace = CARD_GRACE_S if grace is None else grace
    if marker_ok is None:
        try:
            from notify import marker_delivered as marker_ok
        except ImportError:
            return []
    owner_by_sid = owner_by_sid or {}
    seen = dict(state.get("card_unreported") or {})
    logs, live, examined = [], set(), set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
        if not root or root in examined:
            continue
        examined.add(root)
        base = _git_base_ref(root, git_run)
        if not base:
            continue                      # unmeasurable — never a finding
        live.add(root)

        # The repo NAME comes from `origin`, never the directory basename:
        # the checkout is `parovanie_produktov` while every marker is keyed
        # `parovanie-produktov`, so a directory-derived key matches nothing
        # and would report every ticket as unreported. Resolved HERE, ahead
        # of `closed` — the #182 reopen-clear step below needs it and must
        # run independent of whether anything closed in THIS sweep's window
        # (adversarial-review finding: it used to sit AFTER `if not closed:
        # continue`, so a repo where nothing closed this sweep — the ticket's
        # OWN original close having aged out of the window, with no sibling
        # ticket closing to keep `closed` non-empty — never reached it at
        # all, and the exact bug #182 exists to fix kept reproducing).
        try:
            from notify import repo_name_for
            name = repo_name_for(root)
        except ImportError:
            name = ""

        # #182: a REOPENED ticket's existing run-card marker refers to a
        # PRIOR close and must not keep deduping the card the NEXT close
        # earns. `reopen_fetch` (wired = on, like `closed_fetch`) answers
        # "of the issue numbers that already have a marker for this repo,
        # which are OPEN again right now" — a marker for one of those is
        # cleared so the next close claims fresh. Gated separately from the
        # rest of this job (never on `card_probe`/`closed` at all) so a
        # caller that never wires it — every pre-#182 test — sees NO
        # behavior change at all. An existing marker predates THIS sweep's
        # window entirely, which is exactly why this cannot depend on
        # `closed` being non-empty.
        if name and reopen_fetch is not None:
            try:
                from notify import card_marker_numbers, forget_marker
            except ImportError:
                card_marker_numbers = forget_marker = None
            if card_marker_numbers is not None:
                candidates = card_marker_numbers(name)
                if candidates:
                    try:
                        reopened = reopen_fetch(root, candidates)
                    except Exception as e:
                        logs.append("card-reconcile reopen-fetch-failed %s: %r"
                                    % (root, e))
                        reopened = None
                    for n in sorted(reopened or ()):
                        forget_marker("%s#%d" % (name, n))
                        logs.append("card-reconcile reopen-cleared %s#%d"
                                    % (name, n))

        # CONFIRM, then announce (job 24's contract, reused verbatim): the
        # probe fetches the base ref, and the measurement happens after it.
        try:
            card_probe(root, base)
        except Exception as e:
            logs.append("card-reconcile probe-failed %s: %r" % (root, e))
        closed = merged_closes(root, base, now - window, git_run)
        # `verified_source` tracks whether `closed` ALREADY passed GitHub's
        # own CLOSED_EVENT->closer check (#230's `_watchdog_closed_fetch`),
        # as opposed to being a bare local commit-message-keyword match
        # (`merged_closes`) that has not yet been verified against anything.
        verified_source = False
        if not closed and closed_fetch is not None:
            # A repo that never writes `Closes #N` trailers is invisible to
            # the local read — its issues close from the PR body, which is a
            # GitHub-side fact. tvdole is exactly that repo, and it is one of
            # the two in #134's own evidence, so the local-only design would
            # have been blind to half the incident. The fallback is bounded
            # to precisely that signature — fresh merges on the base branch,
            # yet zero trailers — so a repo that answers locally never costs
            # an API call, and a parked repo never costs one either.
            if _commits_in_window(root, base, now - window, git_run) > 0:
                try:
                    closed = _normalize_closed(closed_fetch(root, now - window))
                    verified_source = True
                except Exception as e:
                    logs.append("card-reconcile closed-fetch-failed %s: %r"
                                % (root, e))
                    closed = {}
        if not closed:
            seen.pop(root, None)
            continue

        if not name:
            continue

        # Two ways a ticket can already have been reported: its OWN card, or
        # a catch-up DIGEST that accounted for it (#141). The digest writes
        # its own namespace, and only on a delivered POST — so a digest that
        # never reached Discord suppresses nothing here.
        #
        # Resolved SEPARATELY from `repo_name_for`, whose ImportError path
        # skips the repo entirely: the timer runs the working tree every
        # 60s, so a mid-push checkout can genuinely see a watchdog newer
        # than notify. Sharing that import would turn a transient
        # missing-symbol window into a silent, unlogged death of the whole
        # backstop — the exact shape this job exists to catch.
        try:
            from notify import backfill_marker_key
        except ImportError:
            backfill_marker_key = None
            logs.append("card-reconcile backfill-namespace-unavailable %s"
                        % name)
        missing = [n for n in sorted(closed)
                   if not marker_ok("%s#%d" % (name, n))
                   and not (backfill_marker_key is not None
                            and marker_ok(backfill_marker_key(name, n)))]
        if not missing:
            seen.pop(root, None)
            continue

        # Detection is logged unconditionally, on `missing` alone (never
        # gated on grace or on per-ticket dedup) — this is a local journal
        # line, not the phone ping, so it stays true to issue #36's
        # print-always convention regardless of what happens next.
        logs.append("card-unreported %s n=%d issues=%s"
                    % (name, len(missing),
                       ",".join(str(n) for n in missing[:CARD_MAX_LISTED])))

        prev = seen.get(root) or {}
        # Per-ticket "already pinged, ever" memory. Pruned to tickets still
        # inside `closed` — once a ticket ages out of the 48h window it can
        # never be "missing" again, so its entry is dead weight.
        pinged = {k: v for k, v in (prev.get("pinged") or {}).items()
                  if int(k) in closed}
        # #474: per-ticket "STABLY verify-rejected, ever" memory — parallel
        # to `pinged`, pruned the same way. A candidate that a SUCCESSFUL
        # `closed_fetch` verify rejected (a local `Closes #N` trailer that
        # GitHub's own CLOSED_EVENT->closer check does NOT confirm — the
        # odoo-erp non-default-branch shape) is a STABLE disagreement. Before
        # this memory it was re-verified with a fresh `gh` GraphQL call AND
        # its `verify-rejected` line re-logged on EVERY 60s sweep, forever
        # (gk: the same 9 odoo-erp tickets every sweep). Remembering it here
        # excludes it from `pingable` below, so neither the re-verify nor the
        # re-log happens again — the rejection is logged once, on the sweep
        # it first happens.
        rejected = {k: v for k, v in (prev.get("rejected") or {}).items()
                    if int(k) in closed}
        # A ticket is still inside its own grace window when its closing
        # commit's timestamp is known AND recent. An UNKNOWN timestamp
        # (the `closed_fetch` bare-int-list fallback) is never treated as
        # "inside grace" — not knowing when a ticket closed must never
        # suppress a report the pre-#224 code would have sent instantly.
        pingable = [n for n in missing
                    if not (closed.get(n) is not None
                            and now - closed[n] < grace)
                    and str(n) not in pinged
                    and str(n) not in rejected]

        # #232: `merged_closes` is a bare commit-message-keyword match — it
        # is NOT proof GitHub actually closed the ticket from that commit
        # (odoo-erp: 22 local `Fixes #N` matches on a non-default branch,
        # none of which ever triggered a GitHub auto-close). Only a
        # candidate that already came from the VERIFIED `closed_fetch`
        # fallback (`verified_source`) skips this — asking again would cost
        # a second `gh` call for an answer already known. Every OTHER
        # candidate that survived grace + dedup (i.e. would actually nag
        # this sweep) is checked against the SAME CLOSED_EVENT->closer
        # GraphQL query #230 built, and only a confirmed merged-PR closer
        # is kept. A `closed_fetch` failure (raises, times out, or itself
        # degrades to `None`) drops every currently-pingable candidate for
        # THIS SWEEP ONLY — nothing here is added to `pinged`, so a genuine
        # finding is retried next sweep rather than silently swallowed
        # forever (the direction the pre-existing fallback-failure branch
        # above already takes).
        if pingable and not verified_source and closed_fetch is not None:
            verify_ok = True
            try:
                raw = closed_fetch(root, now - window)
            except Exception as e:
                logs.append("card-reconcile verify-failed %s: %r" % (root, e))
                raw = None
                verify_ok = False
            # #474: `closed_fetch`'s OWN documented degrade sentinel is None
            # (`_watchdog_closed_fetch` returns None on any gh failure —
            # "degrade to the local-only answer rather than to silence"). A
            # None here is an UNMEASURABLE verify, NOT a stable rejection —
            # treat it exactly like a raised exception below: drop the ping
            # this sweep but persist NOTHING, so a genuine finding is retried
            # once the verifier recovers. Only a genuine MEASURED answer
            # (a dict/list, even empty) makes a "not in confirmed" stable.
            if raw is None:
                verify_ok = False
                # #474-review MINOR-1: a None here is the verifier saying "I
                # could not reach GitHub", NOT "GitHub confirmed these were
                # not merge-closed". Log it as verify-UNMEASURABLE, never
                # verify-REJECTED — a sustained graphql-bucket outage would
                # otherwise re-log a MISLEADING "rejected" line every sweep
                # for each unverified candidate (the ping decision is still
                # correct: dropped this sweep, retried next). The exception
                # branch above already logs its own `verify-failed` line, so
                # only the graceful-None path needs this.
                logs.append("card-reconcile verify-unmeasurable %s issues=%s"
                            % (name, ",".join(str(n)
                                              for n in pingable[:CARD_MAX_LISTED])))
            confirmed = _normalize_closed(raw)
            newly_rejected = [n for n in pingable if n not in confirmed]
            # verify-REJECTED only for a SUCCESSFUL verify's genuine
            # rejections (verify_ok) — the transient-failure paths (None /
            # exception) have their own honest lines above and must never
            # claim GitHub rejected a ticket it was never actually asked.
            if newly_rejected and verify_ok:
                logs.append("card-reconcile verify-rejected %s issues=%s"
                            % (name, ",".join(str(n)
                                              for n in newly_rejected[:CARD_MAX_LISTED])))
            # #474: only a SUCCESSFUL verify's rejections are STABLE — persist
            # them into `rejected` so a later sweep excludes them from
            # `pingable` above and never re-verifies or re-logs. A TRANSIENT
            # verify failure (`confirmed = {}` makes EVERYTHING look rejected)
            # must remember NOTHING, so a genuine finding is retried next
            # sweep once the verifier recovers — the same direction the
            # fallback-failure branch above already takes, and what keeps a
            # ticket rejected only by a boom-verifier still pingable later.
            if verify_ok:
                for n in newly_rejected:
                    rejected[str(n)] = now
            pingable = [n for n in pingable if n in confirmed]

        if dry_run or send_fn is None or not pingable:
            # `send_fn is None` must NOT mark anything pinged — nothing was
            # delivered, so a later sweep still owes the user the alert
            # (jobs 21/24's contract, reused verbatim). A `pingable` empty
            # only because everything is still inside grace, or already
            # pinged, is equally a no-op this sweep.
            seen[root] = {"pinged": pinged, "rejected": rejected}
            continue

        shown = ", ".join("#%d" % n for n in pingable[:CARD_MAX_LISTED])
        more = ("" if len(pingable) <= CARD_MAX_LISTED
                else " a ďalších %d" % (len(pingable) - CARD_MAX_LISTED))
        # #369 review M1 (TRIGGERED): a per-repo "finished tickets never
        # reported" nag is exactly the ticket-work-scoped traffic #369's own
        # design comment (item 7) says belongs on the PROJECT thread, not
        # the shared owner pile — the same one-liner every other wired call
        # site already uses.
        from notify import stream_qualified
        status = send_fn(
            "\U0001f4ee **%s** — %d hotových ticketov bez hlásenia\n"
            "> Tieto tickety sa dokončili a zavreli, ale na telefón o nich "
            "neprišla žiadna správa: %s%s.\n"
            "> Práca je hotová — chýba len hlásenie o nej."
            % (name, len(pingable), shown, more),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="card-unreported:%s:%s"
                      % (name, "-".join(str(n) for n in pingable)),
            dry_run=dry_run, project=stream_qualified(name))
        logs.append("card-unreported PING %s -> %s" % (name, status))
        for n in pingable:
            pinged[str(n)] = now
        seen[root] = {"pinged": pinged, "rejected": rejected}

    if not dry_run:
        state["card_unreported"] = {k: v for k, v in seen.items() if k in live}
    return logs
