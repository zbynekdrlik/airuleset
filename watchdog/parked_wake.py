"""#1034 — Job 48: wake a Claude Code session PARKED on the usage-limit
auto-continue banner after a claudy ACCOUNT SWITCH.

WHY (the montalu1 incident, 2026-09-15): a session hit the 5h/usage limit on
drlik.zbynek ~06:20, claudy switched the box's on-disk account to a free one
(samuelrepka) 06:31:56, but the parked Claude Code kept WAITING for the ORIGINAL
account's printed reset (09:50) — the box sat idle ~3.5 h. Neither existing
recovery path re-fires `continue` on the account change:
  * job 1 (api-error auto-resume) is PERMANENTLY DORMANT for a usage/quota cap
    ("ping ONCE, NO `continue`") — a quota reset is not something re-nudging fixes;
  * job 6 (session-limit auto-resume) parses the reset clock ONCE and fires
    `continue` only after `now >= resets_at` — keyed on the ORIGINAL reset time,
    never on the account changing.
So the box has no watcher for "the account is already free, wake it EARLY". This
job is that watcher: keyed on the `~/.claude.json` oauthAccount.emailAddress
change while a pane is parked on the auto-continue banner.

DESIGN (Approach 3 on issue #1034): every sweep, for each live pane, detect the
parked auto-continue banner (`decide.pane_auto_continue_parked`), read the box's
current account email, remember (persisted) the email seen when the banner was
FIRST observed, and — ONLY when the email has CHANGED since then (a claudy
switch) — deliver `Escape` (cancel the auto-continue wait / the "no longer ends
this wait" state) then `continue`+Enter through the existing verified keystroke
primitive, log a token-free `wake-parked: <pane> <old> -> <new>` line, and clear
the pane's parked mark. No account change → do NOTHING (an esc before the reset
would only re-trigger the limit — the frozen job-6 rationale). Complementary to
job 6, which still wakes at the original reset when no switch happens.

Machine-channel ONLY (the #850 recovery-job class): returns journal LOG lines
and NEVER pings the owner (this module deliberately imports no `notify` send
path). Dependency-injected for a tmux/network-free unit test (the
model_float_audit_job template). The keystroke path reuses the SAME proven
primitives — `keys(kind="continue")` for the cancel-Escape and
`send_verified(nudge="wake-parked")` for the transcript-verified `continue` —
never a raw `tmux send-keys`. The nudge identity `wake-parked` is registered in
`RECOVERY_NUDGE_KINDS` (tmux_io.py + nudge_gate.py) so the per-kind staging
switch can never silence a revival, exactly like `resume`/`compact`/`goal-arm`.
"""
import os

# The always-on RECOVERY nudge IDENTITY for this job's keystrokes. MUST be a
# member of tmux_io.RECOVERY_NUDGE_KINDS / nudge_gate.RECOVERY_NUDGE_KINDS
# (both list it) so `nudges_enabled("wake-parked")` is always True.
WAKE_PARKED_NUDGE = "wake-parked"


def deliver_wake(pid, tpath, *, run, sleep_fn=None, logs=None,
                 keys_fn=None, send_verified_fn=None, dry_run=False):
    """Wake a parked pane: send ONE `Escape` (cancel the auto-continue wait /
    the "automatic-continue setting no longer ends this wait" state) then submit
    `continue`, transcript-VERIFIED. Returns True on a verified submit, False
    otherwise (the caller keeps the pane's parked mark and retries next sweep).

    Both keystrokes carry `nudge=WAKE_PARKED_NUDGE` (a RECOVERY kind → always-on)
    and go through the ONE `keys`/`send_verified` primitive — never a raw
    `tmux send-keys`. `send_verified` re-captures fresh, refuses to type over a
    DRAFT (it draft-rescues and returns False — the safe fallback: cancel the
    wait, never clobber the user's own text), and only submits into a bare box.

    Dependency-injected (`keys_fn`/`send_verified_fn`) for a tmux-free unit test;
    the defaults are the real `watchdog.keys` / `watchdog.send_verified`. `dry_run`
    sends NOTHING and returns True (so a dry sweep still logs the intended wake),
    mirroring job 6's own `ok = True` dry-run shape."""
    if dry_run:
        return True
    import watchdog
    kf = keys_fn if keys_fn is not None else watchdog.keys
    svf = send_verified_fn if send_verified_fn is not None else watchdog.send_verified
    # Cancel the parked auto-continue wait. kind="continue" is GATED but
    # nudge=WAKE_PARKED_NUDGE is RECOVERY (always-on), so it is never suppressed
    # by the #1023 staging switch. A SINGLE Escape only — a rapid double-Escape
    # into a pane holding a draft permanently deletes it (#35); send_verified's
    # own bare-check refuses to type over a draft, so no second Escape lands on
    # one.
    kf(pid, "Escape", kind="continue", nudge=WAKE_PARKED_NUDGE, run=run, logs=logs)
    # Submit `continue`, transcript-verified (a swallowed continue must NOT be
    # booked as a wake — the #497 discipline job 6's resume path uses).
    return svf(pid, watchdog.NUDGE_TEXT, run, tpath, sleep_fn=sleep_fn, logs=logs,
               nudge=WAKE_PARKED_NUDGE)


def parked_wake_job(now, state, panes, projects_dir, *,
                    account_email, find_transcript, capture,
                    is_parked, in_mode, recent_human, deliver,
                    dry_run=False):
    """Return journal log lines for the parked-wake sweep. NEVER pings the owner.

    Reuses the SAME materialized `panes` list the pane loop already built (no
    second `list_claude_panes`), the model_float_audit_job template. Per-pane
    state lives under `state["parked_wake"]` (sid -> {email, first_seen}),
    persisted by run_once's own `save_state`; dead-pane entries are pruned.

    Injected deps (production wiring in run_once passes the real primitives):
      account_email()                 -> str   current box oauthAccount.emailAddress ("" unreadable)
      find_transcript(projects_dir, cwd) -> (tpath, mtime) | None
      capture(pid)                    -> str   FRESH pane capture ("" on failure)
      is_parked(captured)             -> bool  parked auto-continue banner present
      in_mode(pid)                    -> bool  copy-mode/scroll → skip this sweep
      recent_human(sid, cwd, tpath, pid) -> bool  VETO: a human is active in this pane
      deliver(pid, tpath)            -> bool  esc+continue verified submit (handles dry_run)
    """
    out = []
    # Work on a local dict; only PERSIST it into `state` at the end when it is
    # non-empty (and drop the key when it empties) — an always-present empty
    # `parked_wake` would pollute run_once's persisted state and break the
    # exact-state assertions elsewhere in the suite.
    parked = state.get("parked_wake")
    if not isinstance(parked, dict):
        parked = {}
    email = account_email() or ""
    seen = set()
    for pid, cwd in panes:
        tinfo = find_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath = tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo
        if not tpath:
            continue
        sid = os.path.basename(str(tpath))
        if sid.endswith(".jsonl"):
            sid = sid[:-len(".jsonl")]
        seen.add(sid)

        captured = capture(pid)
        if not is_parked(captured):
            # Banner gone → the session resumed; drop the parked mark so a fresh
            # park later re-arms cleanly.
            if sid in parked:
                del parked[sid]
                out.append("wake-parked: %s cleared (banner gone)" % pid)
            continue

        rec = parked.get(sid)
        if rec is None:
            # First time we see this pane parked: record the account at park.
            # Only record a REAL email — never seed the baseline with "" (an
            # unreadable ~/.claude.json), which would later false-read as a
            # "switch" the moment the file became readable.
            if email:
                parked[sid] = {"email": email, "first_seen": int(now)}
                out.append("wake-parked: %s parked on %s (watching for switch)"
                           % (pid, email))
            continue

        old = rec.get("email", "")
        if not email or email == old:
            # No account change (or unreadable now) → do NOTHING. An esc/continue
            # before the reset would just re-hit the limit (the frozen rationale).
            continue

        # The account CHANGED while parked — claudy switched the box to a free
        # account. Wake it, subject to the same safety gates the `resume`
        # recovery kind honours.
        if in_mode(pid):
            out.append("wake-parked: %s skip in-mode (switch %s -> %s)"
                       % (pid, old, email))
            continue
        if recent_human(sid, cwd, tpath, pid):
            out.append("wake-parked: %s skip recent-human (switch %s -> %s)"
                       % (pid, old, email))
            continue

        if deliver(pid, tpath):
            out.append("wake-parked: %s %s -> %s" % (pid, old, email))
            del parked[sid]                         # woken; drop the mark
        else:
            # A swallowed/aborted submit is NOT a wake: keep the OLD-email mark so
            # the next sweep retries the switch. send_verified never types over a
            # draft, so this never keystroke-spams.
            out.append("wake-parked: %s submit-unverified (switch %s -> %s), "
                       "retry next sweep" % (pid, old, email))

    # Prune state for panes that no longer exist this sweep (bounded state).
    for dead in [s for s in parked if s not in seen]:
        del parked[dead]
    # Persist without leaving an empty key behind.
    if parked:
        state["parked_wake"] = parked
    elif "parked_wake" in state:
        del state["parked_wake"]
    return out
