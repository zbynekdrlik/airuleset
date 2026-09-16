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
                 keys_fn=None, send_verified_fn=None,
                 capture_fn=None, at_idle_fn=None, dry_run=False):
    """Wake a parked pane: send ONE `Escape` (cancel the auto-continue wait /
    the "automatic-continue setting no longer ends this wait" state) then submit
    `continue`, transcript-VERIFIED. Returns True on a verified submit, False
    otherwise (the caller keeps the pane's parked mark and retries next sweep).

    Both keystrokes carry `nudge=WAKE_PARKED_NUDGE` (a RECOVERY kind → always-on)
    and go through the ONE `keys`/`send_verified` primitive — never a raw
    `tmux send-keys`. `send_verified` re-captures fresh, refuses to type over a
    DRAFT (it draft-rescues and returns False — the safe fallback: cancel the
    wait, never clobber the user's own text), and only submits into a bare box.

    #1034 review-2 🟡-1 — the leading Escape must NOT fire on the JOB's stale
    top-of-loop capture: a human draft (or a self-resumed turn) can race into the
    composer in the ~100-300 ms since. So deliver_wake takes its OWN FRESH capture
    right before the Escape and re-checks `at_idle` (a bare `❯`), the exact
    fresh-recapture discipline `send_verified` uses before its own strip-Escape —
    if the pane is no longer bare-idle, ABORT (return False, retry next sweep),
    never Escape into a draft/running turn (#35/#233).

    Dependency-injected (`keys_fn`/`send_verified_fn`/`capture_fn`/`at_idle_fn`)
    for a tmux-free unit test; the defaults are the real `watchdog.keys` /
    `watchdog.send_verified` / `watchdog.capture_pane` / `watchdog.pane_at_idle_prompt`.
    `dry_run` sends NOTHING and returns True (so a dry sweep still logs the
    intended wake), mirroring job 6's own `ok = True` dry-run shape."""
    if dry_run:
        return True
    import watchdog
    kf = keys_fn if keys_fn is not None else watchdog.keys
    svf = send_verified_fn if send_verified_fn is not None else watchdog.send_verified
    cf = capture_fn if capture_fn is not None else (lambda: watchdog.capture_pane(pid, run))
    idle_fn = at_idle_fn if at_idle_fn is not None else watchdog.pane_at_idle_prompt
    # FRESH re-capture + bare-idle re-check IMMEDIATELY before the Escape (close
    # the JOB-gate TOCTOU): a draft racing into the box, or a turn that started
    # since the top-of-loop capture, means the pane is no longer a bare `❯` — do
    # NOT Escape it. Retry next sweep.
    if not idle_fn(cf()):
        if isinstance(logs, list):
            logs.append("wake-parked: %s abort — not bare-idle at Escape time" % pid)
        return False
    # Cancel the parked auto-continue wait. kind="continue" is GATED but
    # nudge=WAKE_PARKED_NUDGE is RECOVERY (always-on), so it is never suppressed
    # by the #1023 staging switch. A SINGLE Escape only — a rapid double-Escape
    # into a pane holding a draft permanently deletes it (#35); the fresh
    # re-check above proved the box bare, and send_verified's own bare-check is a
    # second belt, so no Escape ever lands on a draft.
    kf(pid, "Escape", kind="continue", nudge=WAKE_PARKED_NUDGE, run=run, logs=logs)
    # Submit `continue`, transcript-verified (a swallowed continue must NOT be
    # booked as a wake — the #497 discipline job 6's resume path uses).
    return svf(pid, watchdog.NUDGE_TEXT, run, tpath, sleep_fn=sleep_fn, logs=logs,
               nudge=WAKE_PARKED_NUDGE)


def parked_wake_job(now, state, panes, projects_dir, *,
                    account_email, find_transcript, capture,
                    is_parked, in_mode, at_idle, recent_human, deliver,
                    dry_run=False):
    """Return journal log lines for the parked-wake sweep. NEVER pings the owner.

    Reuses the SAME materialized `panes` list the pane loop already built (no
    second `list_claude_panes`), the model_float_audit_job template. Per-pane
    state lives under `state["parked_wake"]` (sid -> {email, first_seen}),
    persisted by run_once's own `save_state`; dead-pane entries are pruned.

    AMBIGUOUS panes are skipped (never churned): two `claude` panes in ONE repo
    cwd resolve to the SAME cwd-keyed transcript/sid (the #645 shared-cwd shape),
    so acting per-pane would (a) let a non-parked sibling delete a parked pane's
    baseline every sweep, and (b) cross-verify a `continue` typed into pane A
    against a transcript shared with pane B. The whole per-pane loop already
    SKIPS a transcript owned by >1 pane (`skip ambiguous`); this job mirrors that
    — a missed wake on a shared-cwd config is the safe direction.

    An account switch BEFORE the first post-park sweep records the ALREADY-
    switched account as the baseline, so the wake never fires for that episode
    (it degrades to the status quo — no harm — matching the montalu1 ~11-min
    switch-lag window); the fast 60 s sweep makes that window small.

    This is an ALWAYS-ON keystroke job (registry gate `lambda: True`, like Job 41
    model_float_audit), so a `run_once`-driving test that supplies REAL tmux would
    read live panes (the #1012 class). It is SAFE by construction: it only fires a
    keystroke on a REAL parked banner + a REAL account change + a bare idle prompt
    (triple-gated), and `dry_run` fires nothing — so a test must stub `run` empty
    (as the characterization suite does) or deliberately construct a
    parked+switched+idle pane (which then injects fakes) for it to ever act.

    Injected deps (production wiring in run_once passes the real primitives):
      account_email()                 -> str   current box oauthAccount.emailAddress ("" unreadable)
      find_transcript(projects_dir, cwd) -> (tpath, mtime) | None
      capture(pid)                    -> str   FRESH pane capture ("" on failure)
      is_parked(captured)             -> bool  parked auto-continue banner present
      in_mode(pid)                    -> bool  copy-mode/scroll → skip this sweep
      at_idle(captured)               -> bool  pane is at a BARE idle `❯` prompt (not a
                                               running turn, no user draft) — the #233
                                               guard the `resume` kind uses before it types
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

    # Resolve every pane to its sid FIRST, so an AMBIGUOUS sid (>1 pane sharing
    # one cwd-keyed transcript) can be detected and skipped rather than churned.
    per_sid = {}                     # sid -> [(pid, cwd, tpath)]
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
        per_sid.setdefault(sid, []).append((pid, cwd, tpath))

    seen = set(per_sid)              # every LIVE sid this sweep (incl. ambiguous)
    for sid, owners in per_sid.items():
        if len(owners) > 1:
            # Ambiguous (shared cwd) — never guess which pane the banner is on,
            # never churn the mark. The live panes keep the sid in `seen` so its
            # baseline is not pruned as dead.
            out.append("wake-parked: skip ambiguous (%d panes -> %s)"
                       % (len(owners), sid))
            continue
        pid, cwd, tpath = owners[0]

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
        if not at_idle(captured):
            # A running turn (a session that already self-resumed while its
            # banner tail lingers, or a detector false-positive) is NOT at a
            # bare `❯` — the leading Escape would INTERRUPT the live turn (#233).
            # Skip WITHOUT clearing the mark; retry once it is genuinely idle.
            out.append("wake-parked: %s skip busy-pane (switch %s -> %s)"
                       % (pid, old, email))
            continue
        if recent_human(sid, cwd, tpath, pid):
            out.append("wake-parked: %s skip recent-human (switch %s -> %s)"
                       % (pid, old, email))
            continue

        if deliver(pid, tpath):
            out.append("wake-parked: %s %s -> %s" % (pid, old, email))
            # Woken → drop the mark. Under dry_run `deliver` sends NOTHING but
            # returns True, so KEEP the mark (never let a `--dry-run` clear a
            # real parked baseline and sabotage the next real sweep's wake).
            if not dry_run:
                del parked[sid]
        else:
            # A swallowed/aborted submit is NOT a wake: keep the OLD-email mark so
            # the next sweep retries the switch. send_verified never types over a
            # draft, so this never keystroke-spams.
            out.append("wake-parked: %s submit-unverified (switch %s -> %s), "
                       "retry next sweep" % (pid, old, email))

    # Prune state for panes that no longer exist this sweep (bounded state).
    # Keyed on `seen` (transcript-resolved sids), so a one-sweep transient
    # `find_transcript`→None blip drops the baseline; if a switch coincides with
    # that single blip the next sweep re-seeds the NEW email and the switch is
    # missed — vanishingly unlikely (60 s sweeps vs an ~11-min switch lag) and it
    # degrades to the status quo, so it is accepted rather than special-cased.
    for dead in [s for s in parked if s not in seen]:
        del parked[dead]
    # Persist without leaving an empty key behind.
    if parked:
        state["parked_wake"] = parked
    elif "parked_wake" in state:
        del state["parked_wake"]
    return out
