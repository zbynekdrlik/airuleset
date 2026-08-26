"""#515 -- mechanical U-label lifecycle: clear a ``needs-answer`` /
``needs-decision`` label the moment its question is ANSWERED and the asking
session has moved on, instead of relying on the asking session's prose
discipline to remove it.

Root cause (traced in the code): the footer's ``U N`` bucket counts open
tickets carrying ``needs-answer`` / ``needs-decision``
(``cli_quals.USER_WAITING_LABELS`` via ``_partition_workable``). The label is
added by the asking session when it raises a ``❓`` (ask-and-continue), and its
REMOVAL after the owner answers is PURELY that session's own prose duty --
almost every sub-dev forgets it, so an answered question's label lingers and
the footer shows a phantom ``U`` (miva1 #4199/#4200, dev1 #510). The only
``--remove-label`` anywhere in the watchdog was ``needs-gatekeeper``
(``cross_stream.py``). This module adds the missing mechanical clear.

Design (capture -> verify the session moved on -> clear), implementing #515's
ROZHODNUTÉ gate "label + no pending question in the map + no ``❓`` at the
transcript tail = clear, with an explicit decision log":

- CAPTURE at the RELIABLE answer point, where the (session, cwd, #N) linkage
  is trustworthy: job 7's ``_delivered`` (a routed Discord answer -- a genuine
  owner reply). The terminal / "presence path" (an answer typed straight into
  the session) is DELIBERATELY out of scope -- see the note above
  ``_main_map_reask_pairs``: its own answer detector
  (``prune_answered_questions``) is a #449-unreliable inference, so clearing on
  it would risk a false-CLEAR (the owner-invariant violation this fix exists to
  prevent). A safe terminal-path signal is a separate follow-up.
- RECONCILE (job 32, ``reconcile_u_labels``): for each captured (session, #N),
  KEEP the label while the question is still live -- a FRESH main-map entry
  re-references it (a re-ask) OR the asking session's transcript tail is still
  a ``❓`` (delivered but not yet processed, or re-parked). Only once the
  session has demonstrably moved PAST the ``❓`` (tail != ``❓``) and no re-ask
  is pending does it remove ``needs-answer`` / ``needs-decision`` from #N,
  logging the decision.

Clears ONLY ``needs-answer`` / ``needs-decision`` -- NEVER ``needs-acceptance``
(the #526 acceptance / W lane owns that; the ROZHODNUTÉ scopes this fix to the
two owner-question labels). It acts ONLY on questions THIS box's own sessions
asked+answered (captured locally / this box's own grace store), so no gh slice
scoping is needed and it can never touch another box's ticket -- the capture
IS the ownership proof. Runs on EVERY box (full + reduced): a sub-dev clearing
its OWN answered-question labels is exactly the miva1 fix.

Why NOT a broad "any needs-answer ticket with no pending question -> clear"
sweep (rejected): a decision ticket that NEVER became a ``❓`` is still a
genuine pending decision (a question's stored text need not literally
contain its ``#N``) -- a broad clear would silently hide it. #515 clears
only DELIVERED-AND-ANSWERED questions. (The #461 daily digest that also
targeted that never-asked population is RETIRED, #707 -- only the footer
``U N`` badge surfaces it now, so the narrow scope here matters MORE.)

Topology mirrors cluster C (`watchdog/janitor.py`): ONE top-level
``import watchdog`` (never ``from watchdog import <name>``), every resident
name reached as ``watchdog.<name>`` at CALL time -- circular-import-safe
because ``import watchdog`` only binds the partially-initialised package
object already in ``sys.modules`` and dereferences no attribute at load time.
"""
import re

import watchdog

# The state key holding the Discord-path answer captures (job 7 `_delivered`
# writes; job 32 `reconcile_u_labels` reads + reaps). A dict keyed by
# "<session>:<num>", value {"num", "cwd", "session", "ts"}.
U_RECONCILE_STATE_KEY = "u_label_reconcile"

# Age-cap for a capture that never resolves (session stuck on a `❓`, or a gh
# clear that keeps failing) -- drop it so the state map can never leak
# unboundedly (the #486-G5 reaper discipline). 24h comfortably outlives a
# real "delivered -> processed" gap (minutes) and the grace window itself.
U_RECONCILE_TTL_S = 24 * 3600

# Hard cap on tracked captures, newest-by-ts kept -- a bounded belt against a
# pathological burst, mirroring the question map's own `_QUESTIONS_MAX`.
U_RECONCILE_MAX = 200

# The FIRST ticket reference in a question's stored text. Same shape as
# discord_replies._TICKET_NUM_RX; the FIRST match is the question's primary
# ticket (a passing mention of a second #M must not trigger clearing #M off
# an unrelated answer). 1-6 digits, matching the repo's convention.
_TICKET_NUM_RX = re.compile(r"#(\d{1,6})")


def _first_ticket_num(text):
    """The FIRST `#N` in `text` as an int, or None. Fail-safe on garbage."""
    m = _TICKET_NUM_RX.search(str(text or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def capture_answered_ticket(state, reply, now):
    """#515 CAPTURE (Discord path) -- record that reply `reply` (a
    `parse_discord_reply` dict: session/cwd/question) ANSWERED a question whose
    primary ticket is `#N`, so job 32 can clear its owner-question label once
    the session has moved past the `❓`. A no-op when the question text carries
    no `#N`, or the session/cwd is missing (nothing to reconcile against) --
    such a miss simply falls back to the pre-#515 prose behaviour, NEVER a
    wrong clear. Writes only `state` (persisted by run_once); NO I/O, so it is
    cheap enough for job 7's `_delivered` hot path. The CALLER guards `dry_run`
    (`_delivered` calls this inside its existing `if not dry_run:` block), so a
    dry-run simulation never mutates the persisted state.

    Cross-box safe by construction: the capture is written by THIS box's own
    job-7 delivery, so it can only ever name a question THIS box asked."""
    num = _first_ticket_num((reply or {}).get("question"))
    if num is None:
        return
    cwd = str((reply or {}).get("cwd") or "")
    session = str((reply or {}).get("session") or "")
    if not cwd or not session:
        return                                # no linkage -> nothing to reconcile
    recs = state.get(U_RECONCILE_STATE_KEY)
    recs = dict(recs) if isinstance(recs, dict) else {}
    recs["%s:%d" % (session, num)] = {
        "num": num, "cwd": cwd, "session": session, "ts": int(now)}
    # bounded, newest-by-ts kept (a pathological burst can never grow the map
    # without limit -- the reaper below handles the normal lifecycle).
    if len(recs) > U_RECONCILE_MAX:
        for key, _v in sorted(
                recs.items(),
                key=lambda kv: kv[1].get("ts") or 0 if isinstance(kv[1], dict) else 0
        )[:len(recs) - U_RECONCILE_MAX]:
            recs.pop(key, None)
    state[U_RECONCILE_STATE_KEY] = recs


# NOTE (#515, deliberate scope -- the ROZHODNUTÉ part (b), the terminal /
# "presence path" broad clear, is DEFERRED for safety, NOT forgotten): the only
# RELIABLE "this question was answered" signal is a routed Discord `_delivered`
# reply -- a genuine owner answer. The terminal path's own answer detector,
# `prune_answered_questions`, infers "answered" from "a human prompt landed in
# the asking session after the ping" -- an inference #449 documents as PROVABLY
# FALSE for an owner who types OTHER things at the terminal while answering the
# real question on the phone (which is exactly why #449 moved a pruned entry to
# a 24h GRACE store instead of deleting it). Clearing the U-label off that
# unreliable signal would REINTRODUCE #449's false-loss as a false-CLEAR: an
# ask-and-continue session's tail is `⏳ WORKING` (not `❓`) while it genuinely
# waits, so "entry left the main map + tail != ❓" would clear a still-pending
# question -- the exact owner-invariant violation (`U>0 => delivered question`)
# this ticket exists to UPHOLD. So #515 clears ONLY on the Discord `_delivered`
# capture; a safe terminal-path signal is a separate follow-up.


def _main_map_reask_pairs():
    """The set of (session, #N) pairs a CURRENT main-map entry re-references
    (`notify.load_questions`) -- a FRESH question the asking session is still
    waiting on. A captured (session, #N) present here is a live re-ask and its
    label must be KEPT. Keyed on the FIRST #N of each entry's stored text (its
    primary ticket), matching the capture side. Never raises."""
    pairs = set()
    try:
        from notify import load_questions
        qmap = load_questions()
    except Exception:
        return pairs
    if not isinstance(qmap, dict):
        return pairs
    for _mid, rec in qmap.items():
        if not isinstance(rec, dict):
            continue
        num = _first_ticket_num(rec.get("question") or rec.get("block"))
        if num is None:
            continue
        session = str(rec.get("session") or "")
        if session:
            pairs.add((session, num))
    return pairs


def _u_reconcile_decide(pending_reask, tail_marker, age):
    """#515 PURE decision (facts-in / verdict-out, the #504 pattern): given
    whether a fresh main-map entry re-references this (session, #N)
    (`pending_reask`), the asking session's LAST assistant status marker
    (`tail_marker` -- '❓' / '⏳' / '✅' / '' , '' also = no transcript / no
    marker), and the capture's `age` in seconds, return (action, reason):

      - "keep-reask"  : a live re-ask is pending           -> keep the label.
      - "keep-parked" : the session's tail is still '❓'    -> keep the label
                        (delivered-but-not-yet-processed, or re-parked).
      - "drop-stale"  : the session has been parked on '❓' PAST
                        U_RECONCILE_TTL_S -> stop tracking this capture
                        (anti-leak) WITHOUT touching gh; the label stays (the
                        question is still visibly pending), a later answer just
                        writes a fresh capture.
      - "clear"       : the question is resolved AND the session moved PAST the
                        '❓' (tail != '❓', no re-ask)       -> clear the label.

    A missing transcript / no-marker tail ('') reads as "moved on" (the session
    is not visibly parked on a question), the SAFE direction here: it only ever
    fires for a question THIS box already saw ANSWERED (captured), so clearing
    an answered-question label whose session has ended is correct, never a
    hidden pending question. The TTL reaper is scoped to the '❓'-parked case ON
    PURPOSE: a moved-on session (⏳/✅/none) is CLEARED (which pops the capture)
    regardless of age, so it never lingers -- only a capture stuck behind a
    still-pending '❓' can accumulate, and that is exactly what drop-stale reaps."""
    if pending_reask:
        return "keep-reask", "live re-ask pending in the question map"
    if tail_marker == "❓":
        if age > U_RECONCILE_TTL_S:
            return "drop-stale", "parked on a ❓ past TTL — stop tracking"
        return "keep-parked", "asking session still parked on a ❓"
    return "clear", "answered, session moved past the ❓ (tail=%s)" % (
        tail_marker or "none")


def _clear_owner_question_labels(cwd, num, home=None, run=None):
    """gh side-effect: remove ``needs-answer`` / ``needs-decision`` from open
    ticket #`num` in the repo at `cwd`. Returns the list of labels actually
    removed (possibly empty -- the ticket carried neither, e.g. already cleared
    or only ``needs-acceptance``), or None on an UNMEASURABLE error (gh failed /
    cwd is not a repo) so the caller keeps the capture and retries next sweep.
    NEVER touches ``needs-acceptance`` (the #526 W lane). Best-effort; never
    raises. `run` is injectable for hermetic tests -- (argv) -> object with
    .returncode/.stdout, defaulting to a real subprocess."""
    import subprocess
    env = watchdog._gh_env(home)

    def _default_run(argv):
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=15)

    runner = run or _default_run

    try:
        r = runner(["gh", "issue", "view", str(num), "--json", "state,labels"])
    except Exception:
        return None
    if r is None or getattr(r, "returncode", 1) != 0:
        return None
    try:
        import json
        d = json.loads(getattr(r, "stdout", "") or "{}")
    except (ValueError, TypeError):
        return None
    # A CLOSED ticket is not in the U bucket at all -- nothing to clear, and it
    # is a clean terminal state, so drop the capture (return []). Only OPEN
    # tickets need the label removed.
    if str(d.get("state") or "").upper() == "CLOSED":
        return []
    present = {str((lb or {}).get("name") or "")
               for lb in (d.get("labels") or []) if isinstance(lb, dict)}
    to_remove = [lb for lb in watchdog.OWNER_DECISION_LABELS if lb in present]
    if not to_remove:
        return []                             # nothing to clear (already gone)
    argv = ["gh", "issue", "edit", str(num)]
    for lb in to_remove:
        argv += ["--remove-label", lb]
    try:
        e = runner(argv)
    except Exception:
        return None
    if e is None or getattr(e, "returncode", 1) != 0:
        return None
    return to_remove


def reconcile_u_labels(now, state, dry_run=False, projects_dir=None,
                       clear_fn=None, persist=None):
    """Job 32 (#515) -- the mechanical U-label reconciliation sweep. For every
    captured answered question (job-7 Discord `_delivered` -> `state`), KEEP the
    ticket's owner-question label while the question is still live (a fresh
    re-ask, or the asking session's tail is still a `❓`); once the session has
    demonstrably moved past the `❓`, remove ``needs-answer``/``needs-decision``
    from the ticket. (The terminal / presence path is DELIBERATELY out of scope
    for safety -- see the module-level note above `_main_map_reask_pairs`: the
    only reliable "answered" signal is a routed Discord reply.)

    Gated on `clear_fn` being wired (None -> the whole job is a no-op, so every
    OTHER job's `run_once` test stays network-free, exactly like jobs 8/11/31).
    `clear_fn(cwd, num) -> removed_labels | None`: the gh side-effect
    (defaults, when a real fetch is wired by cmd_watchdog, to
    `_clear_owner_question_labels`); None means unmeasurable -> keep + retry.
    `persist` (the caller's save-state closure) is invoked after each mutating
    action so a mid-sweep kill can never lose a completed clear (the job-8/11/31
    kill-safe pattern). EVERY candidate's verdict is logged (clear/keep/drop +
    reason) -- the #486 explicit-decision-log direction (no silent branches).

    Runs on EVERY box (full + reduced): the captures are LOCAL, so the job only
    ever clears a label for a question THIS box's own sessions asked+answered --
    no gh slice scoping, no cross-box contamination. Never raises."""
    if clear_fn is None:
        return []
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    persist = persist or (lambda: None)
    logs = []

    recs = state.get(U_RECONCILE_STATE_KEY)
    recs = dict(recs) if isinstance(recs, dict) else {}
    # Point `state` at the working copy UP FRONT (never on a dry-run, which must
    # mutate nothing) so every in-loop `persist()` genuinely saves the current
    # popped state -- a reassignment only at the end would make each persist save
    # the PRE-pop dict, silently defeating the kill-safe claim (#516).
    if not dry_run:
        state[U_RECONCILE_STATE_KEY] = recs

    reask_pairs = _main_map_reask_pairs()

    # Normalise the Discord-`_delivered` captures into clean candidates, and
    # REAP any malformed entry as we go (a non-dict / missing-field capture can
    # never reconcile and would otherwise leak the state map forever -- the
    # #486-G5 / #524 per-sid-dict reaper discipline; a legit over-TTL entry is
    # reaped by the `drop-stale` action below).
    changed = False
    by_key = {}
    for key, rec in list(recs.items()):
        num = rec.get("num") if isinstance(rec, dict) else None
        session = str(rec.get("session") or "") if isinstance(rec, dict) else ""
        cwd = str(rec.get("cwd") or "") if isinstance(rec, dict) else ""
        if not isinstance(num, int) or not session or not cwd:
            if not dry_run:
                recs.pop(key, None)           # malformed -> reap (anti-leak)
                changed = True
            continue
        ts = rec.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            ts = now
        by_key[key] = {"num": num, "cwd": cwd, "session": session, "ts": int(ts)}

    for key, cand in sorted(by_key.items()):
        num = cand["num"]
        cwd = cand["cwd"]
        session = cand["session"]
        pending_reask = (session, num) in reask_pairs
        tail_marker = ""
        if not pending_reask:
            tpath = watchdog._transcript_for_session(projects_dir, session, cwd)
            if tpath is not None:
                tail_marker = watchdog.transcript_last_marker(tpath) or ""
        age = now - cand["ts"]
        action, reason = _u_reconcile_decide(pending_reask, tail_marker, age)
        label = watchdog.project_label(cwd)
        tag = "%s #%d" % (label, num)

        if action in ("keep-reask", "keep-parked"):
            logs.append("u-label keep %s (%s)" % (tag, reason))
            continue

        if action == "drop-stale":
            logs.append("u-label drop-stale %s (%s)" % (tag, reason))
            if not dry_run and key in recs:
                recs.pop(key, None)
                changed = True
            continue

        # action == "clear"
        if dry_run:
            logs.append("u-label clear (dry-run) %s (%s)" % (tag, reason))
            continue
        removed = clear_fn(cwd, num)
        if removed is None:
            logs.append("u-label clear-retry %s (gh unmeasurable, kept)" % tag)
            continue                          # transient -> keep, retry next sweep
        if removed:
            logs.append("u-label CLEARED %s: %s (%s)"
                        % (tag, ",".join(removed), reason))
        else:
            logs.append("u-label already-clear %s (no owner-question label)" % tag)
        if key in recs:
            recs.pop(key, None)               # mutates the persisted dict directly
            changed = True
        persist()                             # a landed clear survives a kill

    if changed:
        persist()
    return logs
