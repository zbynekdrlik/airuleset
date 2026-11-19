"""Stale children of a LIVE session's scratchpad in the DAILY sweep (#1355).

Discovery (`cli_scratch_sweep._classify_scratch_session`) already reports, for
a live or undeterminable session, the top-level children of its
``scratchpad/`` that are unused past the child floor (``stale_children``,
#849/#1195). Until #1355 only the watchdog Job 40 drain consumed them, and only
at >= 80 % disk, so a session alive for weeks piled up review and worker clones
(controller 17.11.2026: 7.1 GB). `sweep_claude_scratch` now feeds the same rows
through these two helpers.

Safety is the same as the drain's: a child is a candidate only after
``SCRATCHPAD_CHILD_MIN_AGE_DAYS`` (2 d) with no use, meaning the newest mtime
AND file atime anywhere in its subtree (relatime trails a read by < 24 h, so
48 h is the shortest provable floor), and with no live process fd/cwd/exe
inside it. Both are checked again right before the delete
(`refused_at_act`); a stat error keeps the child. Stdlib only; the live-use
probe is passed in, so this leaf never imports `cli_scratch_sweep`.
"""

from cli_scratch_use import child_in_use


def expand_live_children(candidates):
    """`candidates` plus every row's ``stale_children``, each as its own row
    (kept rows included, so the log shows why they stayed). A session row
    itself is never changed: a live session stays a kept row."""
    out = list(candidates)
    for c in candidates:
        out.extend(dict(ch) for ch in (c.get("stale_children") or ()))
    return out


def refused_at_act(row, path, now, proc_dir, live_use):
    """True (KEEP) when the act-time re-check says `path` is in use.

    A child row carries ``floor_days`` (the floor it was planned under): it is
    refused when it was used within that floor since the plan or a live
    process holds it (`cli_scratch_use.child_in_use`). Any other row keeps the
    whole-entry check `live_use(path, proc_dir=...)`."""
    floor = row.get("floor_days")
    if floor is None:
        return bool(live_use(path, proc_dir=proc_dir))
    try:
        return child_in_use(path, now, floor * 86400,
                            lambda c: live_use(c, proc_dir=proc_dir))
    except OSError:
        return True
