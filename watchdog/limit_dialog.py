"""#1086 — Job 6 branch: dismiss Claude Code's INTERACTIVE usage-limit DIALOG
and resume the armed /goal once the box provably has capacity again.

WHY (gk incident 2026-09-19, owner directive verbatim: „nech airuleset lepsie
zachyti situaciu ked tam vyskoci okno ci pokracovat aby taku situaciu odblokoval
… gk/gkinfra su high priority targety ktore musia bezat bez komplikacii"): a hard
weekly 429 on the gatekeeper's then-current account rendered the INTERACTIVE
dialog

    ● Goal paused · usage limit reached · send a message after it resets to continue
       What do you want to do?
       ❯ 1. Stop and wait for limit to reset
         2. Wait here, then continue automatically at <date>
         3. Switch to usage credits
       Enter to confirm · Esc to cancel

claudy switched the box's credentials to a fresh account ~10 s later (the usage
cache already showed the new account at 5 %), but the pane sat blocked 1.5 h+.
The existing recovery paths cannot help: job 1 stays dormant for a usage cap, and
job 6's session-limit branch keys on the plain BANNER (`pane_session_limited`,
which this modal does NOT match) and waits for the parsed reset epoch — the wrong
signal once capacity is already back. The dialog is MODAL, so the ordinary resume
nudge (typed text) would land in the dialog, not the prompt.

DESIGN (Approach 1 on issue #1086). This is a BRANCH of job 6 (the usage-limit
job), invoked from run_once's per-session pane loop ONLY when
`decide.pane_limit_dialog(captured)` is True (the caller gates + `continue`s, so
the modal is never misread as a wedged prompt by job 10 nor seeds a session-limit
ping by job 1). Per sweep for such a pane:
  * FIRST sighting → record the episode (`limit-dialog:<sid>` with first_seen +
    the account seen at park), no keystroke.
  * `decide.capacity_recovered(episode, usage_cache)` still False → journal
    `limit-dialog: … waiting …`, do NOTHING (the dialog's own option 2 wait still
    applies). The usage cache is the box's OWN feed (the same file the footer
    renders) — no reset-clock guess.
  * capacity back + the recent-human veto (`goal._recovery_recent_human`) clear +
    not in copy-mode → `deliver_dismiss`: send ONE `Escape` (cancel the modal),
    re-capture and confirm the dialog is GONE and the prompt is a bare `❯` BEFORE
    typing, then submit the resume text (`continue`) transcript-verified. journal
    `limit-dialog: … dismissed (account X→Y, usage N%)`. ONE dismiss per episode
    (a latch on a VERIFIED submit); a swallowed submit retries, BOUNDED by
    `LIMIT_DIALOG_MAX_TRIES` so it is never an unbounded retry loop and never
    strands. A dialog that reappears after the resume starts a NEW episode only
    after the old one ages out of the state (`wait_clear`) and a fresh cap.

Machine-channel ONLY (the #850 recovery-job class): returns journal LOG lines and
NEVER pings the owner (this module imports no `notify` send path — the footer's
account/usage row is the truth the owner reads). Both keystrokes reuse the SAME
proven primitives — `keys` for the cancel-Escape and `send_verified` for the
transcript-verified `continue` — never a raw `tmux send-keys`, carrying the
`resume` RECOVERY nudge identity (`RECOVERY_NUDGE_KINDS`), so the #1023 per-kind
staging switch can never silence a revival, exactly like `wake-parked`.
Dependency-injected for a tmux/network-free unit test (the parked_wake template).
"""
import json

from watchdog.usage import _USAGE_CACHE_PATH  # usage.py stays READ-ONLY: import
#   the cache PATH constant, never add a reader there. `capacity_recovered` is a
#   pure predicate on the already-parsed dict; the file read lives HERE.

# The always-on RECOVERY nudge IDENTITY for this branch's keystrokes. MUST be a
# member of tmux_io.RECOVERY_NUDGE_KINDS / nudge_gate.RECOVERY_NUDGE_KINDS (both
# list `resume`) so `nudges_enabled("resume")` is always True — a nudges-OFF box
# must still recover a dialog-blocked session (the #520 harm).
RESUME_NUDGE = "resume"

# Bounded dismiss attempts per episode: the latch below stops re-firing after a
# VERIFIED dismiss; this cap stops a SWALLOWED dismiss (Escape did not clear, or
# the continue was swallowed) from retrying forever ("never a retry loop"). No
# owner ping on give-up (analyze-not-ping, #693/#704) — the journal carries it.
LIMIT_DIALOG_MAX_TRIES = 4

# The fleet-declared gatekeeper high-priority checkout basenames (cli_fleet.py
# `gk` / `gk-infra` / `gk-quality` windows). Data-driven, NOT a per-box branch:
# a box without these cwds is unaffected (the sort is stable). Order = the sweep
# priority the owner asked for.
_GK_PRIORITY_BASENAMES = ("odoo-erp", "odoo-erp-infra", "odoo-erp-quality")


def prioritize_panes(panes):
    """Return `panes` reordered so the gatekeeper's high-priority checkouts
    (gk / gk-infra / gk-quality — matched by cwd BASENAME against the
    fleet-declared list) come FIRST in the sweep, then every other pane in its
    ORIGINAL relative order (a stable sort). This honours the owner's "gk/gkinfra
    su high priority targety" directive at the ONE place the pane loop is
    materialized, with no per-box code in the branch itself — a box without those
    cwds is byte-identical (identity). Never raises."""
    try:
        rank = {name: i for i, name in enumerate(_GK_PRIORITY_BASENAMES)}
        default = len(rank)

        def key(item):
            cwd = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else ""
            base = str(cwd).rstrip("/").rsplit("/", 1)[-1]
            return rank.get(base, default)

        return sorted(panes, key=key)      # sorted() is stable → ties keep order
    except Exception:
        return list(panes)


def read_usage_cache(path=None):
    """Best-effort read of the box's usage cache ({ts, account_email, windows} —
    the same file `watchdog/usage.py` writes and the statusline renders). Returns
    the parsed dict, or None on any missing/unreadable/unparseable file. Never
    raises. `path` defaults to the module global (resolved at CALL time so tests
    can patch it), mirroring `usage.write_usage_cache`'s own path convention."""
    try:
        with open(path or _USAGE_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cache_email(usage_cache):
    if isinstance(usage_cache, dict):
        e = usage_cache.get("account_email")
        if isinstance(e, str):
            return e
    return ""


def _cache_max_pct(usage_cache):
    """The highest window percent in the cache (for the dismissed journal line),
    or "?" when none is readable. Never raises."""
    best = None
    if isinstance(usage_cache, dict):
        for w in usage_cache.get("windows") or []:
            if isinstance(w, dict):
                p = w.get("percent")
                if isinstance(p, (int, float)) and not isinstance(p, bool):
                    best = p if best is None else max(best, p)
    return "?" if best is None else int(best)


def _cache_age_str(now, usage_cache):
    if isinstance(usage_cache, dict):
        ts = usage_cache.get("ts")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            return "age %ds" % max(0, int(now - ts))
    return "no cache"


def deliver_dismiss(pid, tpath, *, run, sleep_fn=None, logs=None,
                    keys_fn=None, send_verified_fn=None, capture_fn=None,
                    dialog_fn=None, at_idle_fn=None, dry_run=False):
    """Dismiss the modal and resume: send ONE `Escape` (cancel the dialog),
    RE-CAPTURE and confirm the dialog is GONE and the prompt is a bare `❯`
    BEFORE typing, then submit the resume text (`continue`) transcript-verified.
    Returns True ONLY on a verified submit; False otherwise (the caller keeps the
    episode un-latched and retries next sweep, bounded).

    The dialog-gone + bare-idle confirmation BEFORE the resume text is
    load-bearing: the resume text must never land INSIDE a still-open modal
    (it would be typed into option-2's own composer, not the prompt). A single
    Escape only (a rapid double-Escape into a pane holding a draft can delete it,
    #35); `send_verified`'s own bare-check is a second belt and it draft-rescues
    rather than clobber real text.

    Both keystrokes carry `nudge=RESUME_NUDGE` (a RECOVERY kind → always-on) and
    go through the ONE `keys`/`send_verified` primitive — never a raw
    `tmux send-keys`. The Escape uses `kind="continue"` (a GATED delivery kind
    gated AS A UNIT, but always-on because its `nudge` is the recovery identity —
    the parked_wake `deliver_wake` precedent). Dependency-injected
    (`keys_fn`/`send_verified_fn`/`capture_fn`/`dialog_fn`/`at_idle_fn`) for a
    tmux-free unit test; the defaults are the real primitives. `dry_run` sends
    NOTHING and returns True (so a dry sweep still logs the intended dismiss),
    mirroring job 6's own dry-run shape."""
    if dry_run:
        return True
    import watchdog
    kf = keys_fn if keys_fn is not None else watchdog.keys
    svf = send_verified_fn if send_verified_fn is not None else watchdog.send_verified
    cf = capture_fn if capture_fn is not None else (lambda: watchdog.capture_pane(pid, run))
    dfn = dialog_fn if dialog_fn is not None else watchdog.pane_limit_dialog
    idle_fn = at_idle_fn if at_idle_fn is not None else watchdog.pane_at_idle_prompt

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    # Cancel the modal. kind="continue" is GATED but nudge=RESUME_NUDGE is
    # RECOVERY (always-on), so the #1023 staging switch never suppresses it.
    kf(pid, "Escape", kind="continue", nudge=RESUME_NUDGE, run=run, logs=logs)
    # Confirm the dialog is GONE and the box is a bare `❯` BEFORE typing — never
    # type the resume text into a still-open modal.
    cap = cf()
    if dfn(cap):
        _log("limit-dialog: %s Escape did not clear the dialog, retry next sweep" % pid)
        return False
    if not idle_fn(cap):
        _log("limit-dialog: %s not bare-idle after Escape, retry next sweep" % pid)
        return False
    # Resume the armed /goal, transcript-verified (a swallowed continue is NOT a
    # dismiss — the #497 discipline job 6's own resume path uses).
    return svf(pid, watchdog.NUDGE_TEXT, run, tpath, sleep_fn=sleep_fn, logs=logs,
               nudge=RESUME_NUDGE)


def handle_limit_dialog(now, state, *, pid, cwd, tpath, sid, project, captured,
                        usage_cache, in_mode, at_idle=None, recent_human,
                        deliver, dry_run=False):
    """The per-pane limit-dialog branch body. Called ONLY when
    `decide.pane_limit_dialog(captured)` is True (the run_once caller gates).
    Returns journal log lines; NEVER pings the owner (machine-channel only).

    Episode state lives under `state["limit-dialog:<sid>"]`
    ({first_seen, account, dismissed, attempts, last_seen}), persisted by
    run_once's own `save_state` and aged out by its cleanup pass once the dialog
    has been absent for `wait_clear` (the `sesslimit:`/`apierr-authdead:` shape).

    Injected deps (production wiring reads them from the live pane):
      usage_cache            -> dict|None  the box usage cache {ts, account_email, windows}
      in_mode()              -> bool  copy-mode/scroll → skip this sweep
      recent_human()         -> bool  VETO: a human just touched this pane
      deliver()              -> bool  Escape + dialog-gone confirm + continue verified
    `at_idle` is accepted for call-site symmetry with the sibling recovery jobs
    but the post-Escape bare-idle check lives inside `deliver` (`deliver_dismiss`)
    — before the Escape the pane shows the MODAL, never a bare `❯`.

    dry-run contract (#1075 shape): a `--dry-run` sweep persists NOTHING — every
    mutation lands on a local dict copy stored back into `state` ONLY when
    `not dry_run`, so a dry sweep around a live dialog can never suppress the real
    dismiss."""
    from watchdog.decide import capacity_recovered
    out = []
    ekey = "limit-dialog:" + sid
    ep_src = state.get(ekey)
    ep = dict(ep_src) if isinstance(ep_src, dict) else None

    def _persist(e):
        if not dry_run:
            state[ekey] = e

    if ep is None:
        # First sighting: record the episode (account at park, from the cache).
        ep = {"first_seen": int(now), "account": _cache_email(usage_cache),
              "dismissed": False, "attempts": 0, "last_seen": int(now)}
        _persist(ep)
        out.append("limit-dialog: %s seen, watching for capacity (account %s) [%s]"
                   % (pid, ep["account"] or "?", project or sid))
        return out

    ep["last_seen"] = int(now)

    if ep.get("dismissed"):
        # ONE dismiss per episode — never re-fire on a lingering dialog render.
        _persist(ep)
        out.append("limit-dialog: %s already dismissed this episode [%s]"
                   % (pid, project or sid))
        return out

    if not capacity_recovered(ep, usage_cache):
        # No capacity yet → wait (the dialog's own option-2 wait still applies).
        _persist(ep)
        out.append("limit-dialog: %s waiting (account %s, cache %s) [%s]"
                   % (pid, ep.get("account") or "?", _cache_age_str(now, usage_cache),
                      project or sid))
        return out

    # Capacity is back. Gate the keystrokes exactly like the resume recovery kind.
    if in_mode():
        _persist(ep)
        out.append("limit-dialog: %s skip in-mode [%s]" % (pid, project or sid))
        return out
    if recent_human():
        _persist(ep)
        out.append("limit-dialog: %s skip recent-human [%s]" % (pid, project or sid))
        return out
    if ep.get("attempts", 0) >= LIMIT_DIALOG_MAX_TRIES:
        # Bounded — never an unbounded retry loop; no owner ping (analyze-not-ping).
        _persist(ep)
        out.append("limit-dialog: %s gave up after %d attempts (dialog not "
                   "dismissable) [%s]"
                   % (pid, ep.get("attempts", 0), project or sid))
        return out

    if deliver():
        ep["dismissed"] = True
        _persist(ep)
        out.append("limit-dialog: %s dismissed (account %s -> %s, usage %s%%) [%s]"
                   % (pid, ep.get("account") or "?", _cache_email(usage_cache) or "?",
                      _cache_max_pct(usage_cache), project or sid))
    else:
        ep["attempts"] = ep.get("attempts", 0) + 1
        _persist(ep)
        out.append("limit-dialog: %s dismiss unverified (attempt %d/%d), retry "
                   "next sweep [%s]"
                   % (pid, ep["attempts"], LIMIT_DIALOG_MAX_TRIES, project or sid))
    return out
