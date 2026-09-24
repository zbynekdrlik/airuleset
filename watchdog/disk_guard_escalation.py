"""Where and when watchdog Job 40 files its owner-actionable disk ticket (#1136 A).

The owner ruling on #1136 (issuecomment-5822220077): disk problems on gk
belong to the gk-infra window, never to the gk review/FLOW window. The box's
own fleet declaration (``cli_fleet.REMOTE_HOSTS[...]["windows"]``, #998)
already says which window is the infra lane and which repo it works. A box
whose declaration has a window with role ``infra`` and a ``repo`` checkout
files there with label ``infra``. Every other box keeps the airuleset repo
through the existing ``gk-request`` path.

The infra target does NOT go through ``gk-request``: that command always adds
``needs-gatekeeper``, and watchdog job 11 then delivers the request to the
review/FLOW supervisor, the window the ruling keeps out of disk work. It uses
the direct ``gh issue create`` that ``gk-request`` itself runs on the
owner-identity box, and adds the label with its own ``gh issue edit`` call,
never baked into create (#221: create silently drops a label it cannot apply).

The trigger is the footer's own owner-actionable state (#925): >= SEVERE_PCT,
or the drain reported ``drain_exhausted`` at >= EXHAUSTED_FILE_PCT. The
dedupe and the no-Discord rule (#693/#850) stay with the caller,
``disk_guard.file_severe_ticket``. This leaf never pings anything.
"""

AIRULESET_REPO = "zbynekdrlik/airuleset"
INFRA_ROLE = "infra"
INFRA_LABEL = "infra"
EXHAUSTED_FILE_PCT = 90     # #925: exhausted at >= 90 % is owner-actionable
MAX_SKIPPED_ROWS = 10       # a body line per skipped rung, capped


def should_file(status, severe_pct):
    """True at >= ``severe_pct``, or when the drain reported
    ``drain_exhausted`` (strictly ``True``) at >= EXHAUSTED_FILE_PCT."""
    worst = status.get("worst_pct", 0)
    if worst >= severe_pct:
        return True
    return status.get("drain_exhausted") is True and worst >= EXHAUSTED_FILE_PCT


def own_windows():
    """This box's declared windows: the same ``cli_fleet.box_windows(
    cli_concurrency._current_user())`` lookup the concurrency / goal code
    uses. Any resolver failure reads as "no windows", which routes to the
    airuleset fallback, so the ticket is never lost."""
    try:
        import cli_concurrency
        import cli_fleet
        return cli_fleet.box_windows(cli_concurrency._current_user())
    except Exception:  # noqa: BLE001 -- unresolvable → fallback target
        return []


def infra_window(windows):
    """The first declared window with role ``infra`` and a safe ``repo``,
    else ``None``."""
    try:
        from cli_fleet import _repo_ok
    except Exception:  # noqa: BLE001 -- no validator → no infra routing
        return None
    for w in windows or []:
        if (isinstance(w, dict) and w.get("role") == INFRA_ROLE
                and w.get("repo") and _repo_ok(w["repo"])):
            return w
    return None


def resolve_target(windows):
    """``(repo, label)``: the infra window's repo + ``infra``, or the
    airuleset repo + ``None`` (the unchanged gk-request path)."""
    w = infra_window(windows)
    if w is None:
        return AIRULESET_REPO, None
    return w["repo"], INFRA_LABEL


def compose(status, hostname, top, human, target_pct, window=None):
    """``(title, body)``: what the drain could not free and who owns it."""
    pct = status["worst_pct"]
    exhausted = status.get("drain_exhausted") is True
    why = "drain exhausted" if exhausted else "still >= 95% after drain"
    owner = window.get("name") if window else None
    title = "Disk pressure on %s: %d%% (%s)%s" % (
        hostname, pct, why, (" - for %s" % owner) if owner else "")
    lines = ["Auto-filed by disk-guard (watchdog Job 40) on %s: worst mount "
             "at %d%% (%s)." % (hostname, pct, status.get("dim", "bytes")), ""]
    lines.append("## What the drain could not free")
    if exhausted:
        lines.append("The drain ran and freed nothing it is allowed to delete "
                     "(`drain_exhausted`).")
    else:
        lines.append("The drain ran but could not bring the box under %d%%."
                     % target_pct)
    skipped = [r for r in status.get("drain_skipped_rungs") or []
               if isinstance(r, dict)]
    if skipped:
        lines.append("Rungs the drain had to skip:")
        for r in skipped[:MAX_SKIPPED_ROWS]:
            lines.append("- %s `%s`: %s" % (r.get("rung") or r.get("cls") or "?",
                                            r.get("path") or "?",
                                            r.get("reason") or "?"))
        if len(skipped) > MAX_SKIPPED_ROWS:
            lines.append("- ... %d more" % (len(skipped) - MAX_SKIPPED_ROWS))
    lines.append("")
    lines.append("Top consumers still on disk:")
    lines += ["- `%s` = %s" % (p, human(b)) for p, b in (top or [])] or ["- (none)"]
    lines.append("")
    if owner:
        lines.append("## Owner: the `%s` window (role infra)" % owner)
        lines.append("Owner ruling on zbynekdrlik/airuleset#1136: disk problems on this "
                     "box are infra work for `%s`, never for the review/FLOW "
                     "window. Move growing data off the root disk (the box "
                     "volume) or raise the capacity question to the owner."
                     % owner)
    else:
        lines.append("This box declares no infra window, so the escalation "
                     "stays in airuleset.")
    return title, "\n".join(lines)


def file_infra(run_fn, repo, label, title, body):
    """Create the ticket in ``repo``, then add ``label`` in its own call.
    Returns ``(created, ref, errors)``: ``created`` is True once the issue
    exists (so the caller dedupes even if only the label failed), ``ref`` the
    create output (the issue URL), ``errors`` the failure texts to log."""
    r = run_fn(["gh", "issue", "create", "-R", repo, "--title", title,
                "--body", body], capture_output=True, text=True, timeout=60)
    if getattr(r, "returncode", 1) != 0:
        return False, None, ["gh issue create -R %s failed: %s" % (
            repo, (getattr(r, "stderr", "") or "").strip()[:200])]
    ref = (getattr(r, "stdout", "") or "").strip()
    num = ref.rsplit("/", 1)[-1]
    if not num.isdigit():
        return True, ref or None, ["created in %s but no issue number in %r; "
                                   "label %s not applied" % (repo, ref[:200], label)]
    e = run_fn(["gh", "issue", "edit", num, "-R", repo, "--add-label", label],
               capture_output=True, text=True, timeout=60)
    if getattr(e, "returncode", 1) != 0:
        return True, ref, ["label %s not applied to %s: %s" % (
            label, ref, (getattr(e, "stderr", "") or "").strip()[:200])]
    return True, ref, []
