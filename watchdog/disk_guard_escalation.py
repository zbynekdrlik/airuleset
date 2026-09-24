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
or the drain reported ``drain_exhausted`` at >= the box's OWN effective
drain-critical level (``disk_guard_worktrees.effective_critical_pct``: 85 % on
a <= 64 GB root like gk, 90 % above; coordinator ruling on #1136 — never a
fixed floor), not on a shared-stream box: each account's drain sees only its own data, and N stream
accounts would file N tickets). The dedupe and the no-Discord rule
(#693/#850) stay with the caller, ``disk_guard.file_severe_ticket``, which
also asks :func:`still_open` about the stored ticket once its 24 h window
passed. This leaf never pings anything.
"""

import json
import os
import re
from pathlib import Path

AIRULESET_REPO = "zbynekdrlik/airuleset"
INFRA_ROLE = "infra"
INFRA_LABEL = "infra"
MAX_SKIPPED_ROWS = 10       # a body line per skipped rung, capped


_ISSUE_URL_RE = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)")


def default_critical_pct():
    """The box's own effective drain-critical level, the SAME helper the
    ``drain_exhausted`` bookkeeping (``disk_guard_post._record_exhausted``)
    uses, so the ticket fires exactly where the drain reports exhaustion."""
    from watchdog import disk_guard_worktrees as _dgw
    return _dgw.effective_critical_pct()


def should_file(status, severe_pct, critical_pct, box_class=None):
    """True at >= ``severe_pct``, or when the drain reported
    ``drain_exhausted`` (strictly ``True``) at >= ``critical_pct`` on a box
    that is not ``shared-stream``."""
    worst = status.get("worst_pct", 0)
    if worst >= severe_pct:
        return True
    return (status.get("drain_exhausted") is True and worst >= critical_pct
            and box_class != "shared-stream")


def default_filer_refused():
    """True under pytest (``PYTEST_CURRENT_TEST``, set by pytest for every
    test and never in production): a test that forgot to inject ``run_fn``
    must never reach the real filer (#896-899, and #1144-#1151, where a
    raise-based guard was swallowed by the filer's own ``except``)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def stored_issue(state_path):
    """The issue reference the last filing stored (a URL, or gk-request's
    ``filed: <url>`` line), else ``None``."""
    try:
        issue = json.loads(Path(state_path).read_text()).get("issue")
    except (OSError, ValueError, AttributeError):
        return None
    return issue if isinstance(issue, str) else None


def still_open(run_fn, ref):
    """True only when ``ref`` names a GitHub issue that ``gh`` reads as
    OPEN. Anything unknown (no ref, a gh failure, an exception) is False:
    an unreadable state never suppresses the escalation."""
    m = _ISSUE_URL_RE.search(ref or "")
    if not m:
        return False
    try:
        r = run_fn(["gh", "issue", "view", m.group(2), "-R", m.group(1),
                    "--json", "state", "-q", ".state"],
                   capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 -- unknown → file (fail toward filing)
        return False
    return (getattr(r, "returncode", 1) == 0
            and (getattr(r, "stdout", "") or "").strip() == "OPEN")


def _md(s):
    """A local string made safe inside a markdown code span: no backtick can
    close the span (inside it, ``@user`` and ``#N`` never render)."""
    return str(s).replace("`", "'")


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
            lines.append("- %s `%s`: `%s`" % (
                _md(r.get("rung") or r.get("cls") or "?"),
                _md(r.get("path") or "?"), _md(r.get("reason") or "?")))
        if len(skipped) > MAX_SKIPPED_ROWS:
            lines.append("- ... %d more" % (len(skipped) - MAX_SKIPPED_ROWS))
    lines.append("")
    lines.append("Top consumers still on disk:")
    lines += ["- `%s` = %s" % (_md(p), human(b)) for p, b in (top or [])] or ["- (none)"]
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
    """Create the ticket in ``repo`` carrying ``label`` (so no reader ever
    sees it unlabelled), then VERIFY the label with its own ``gh issue edit``
    (#221: create silently drops a label it cannot apply). Returns
    ``(created, ref, errors)``: ``created`` is True once the issue exists (so
    the caller dedupes even if only the label step failed or raised), ``ref``
    the create output (the issue URL), ``errors`` the failure texts to log.
    A raise from the create itself propagates: nothing was filed."""
    r = run_fn(["gh", "issue", "create", "-R", repo, "--title", title,
                "--body", body, "--label", label],
               capture_output=True, text=True, timeout=60)
    if getattr(r, "returncode", 1) != 0:
        return False, None, ["gh issue create -R %s failed: %s" % (
            repo, (getattr(r, "stderr", "") or "").strip()[:200])]
    ref = (getattr(r, "stdout", "") or "").strip()
    num = ref.rsplit("/", 1)[-1]
    if not num.isdigit():
        return True, ref or None, ["created in %s but no issue number in %r; "
                                   "label %s not applied" % (repo, ref[:200], label)]
    try:
        e = run_fn(["gh", "issue", "edit", num, "-R", repo, "--add-label", label],
                   capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001 -- the issue exists: never re-file
        return True, ref, ["label %s not verified on %s: %r" % (label, ref, exc)]
    if getattr(e, "returncode", 1) != 0:
        return True, ref, ["label %s not applied to %s: %s" % (
            label, ref, (getattr(e, "stderr", "") or "").strip()[:200])]
    return True, ref, []
