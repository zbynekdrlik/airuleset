"""gates.lanefill — the Stop-time LANE-FILL gate (#1078 item 1).

THE PROBLEM. On a PARALLEL box the autopilot `/goal` loop is supposed to keep
as many bundle-safe worker lanes live as the box + backlog bear (continuous
refill, skills/autopilot/SKILL.md Step 3.2 / #848). But the only enforcement
was the supervisor's own judgment: a session that serialised implementation
"by box" would end a `⏳ WORKING` / `✅ DONE` turn with ONE live lane while
`slice-quals --count-dispatchable` was 15+. Nothing read that gap at the Stop
boundary — the exact owner complaint repeated three times on 2026-09-18 to the
montalu1 session ("I 19 a len jeden subagent pracuje!", "stále ti to treba
opakovať?"). Prose (the skill + the owner) had failed; the ticket asked for a
mechanical check AT the turn boundary, which only a Stop hook sees.

THE GATE. Runs ONLY when the session is goal-armed AND the box/pane mode is
`parallel` (`sequential` boxes — the controller pane, gk-infra — are EXEMPT by
design, #1031/#1035) AND the turn's last line is `⏳ WORKING` / `✅ DONE`. It
BLOCKS (exit 2) iff there are MORE dispatchable tickets than live lanes AND a
free lane slot AND the final message carries no justification line
(`Dependency: #N …` / `Lane-fill: <reason>`). The block names up to five
dispatchable tickets (with titles) and the two escape lines.

FAIL-OPEN, deliberately (the OPPOSITE of the filing gate's fail-closed): this
hook adds PRESSURE, it does not guard a write. Any unreadable seam — a
transcript / quals / mode / cap read that errors — journals
`lane-fill: unreadable (<why>) — not enforced` to stderr and ALLOWS. A
gh/transcript failure must never wedge the loop.

THIN-ADAPTER shape (gate-family #1020): the bash adapter
`hooks/stop-check-lane-fill.sh` runs `python3 -P -m gates.lanefill` with the
Stop payload on stdin; exit 2 (reason on stderr) blocks, exit 0 allows. See the
#1078 design comment for the full architecture + rejected alternatives.

Dry-run: echo '{"last_assistant_message":"…\n⏳ WORKING","cwd":"/repo","session_id":"s","transcript_path":"/t.jsonl"}' | python3 -m gates.lanefill
"""
import os
import re
import sys
import time

import gates

# The mode that enforces (a sequential pane runs one lane at a time; exempt).
_MODE_PARALLEL = "parallel"

# Lane-occupancy freshness window — the SAME 15 min the compact live-worker
# reader uses (watchdog.compact.COMPACT_LIVE_WORKER_FRESHNESS_S): a live worker
# in a long foreground CI-wait writes nothing to its transcript for up to ~9 min,
# so a shorter window would misread it as dead.
_FRESHNESS_S = 15 * 60

# A justification line makes an under-filled turn INTENTIONAL — the escape the
# block message names. `Dependency: #N …` (a real blocking ref) or
# `Lane-fill: <reason>` (e.g. "all remaining tickets wait on gk review",
# "bounce lane occupies the box"). Matched anywhere in the final message.
_JUSTIF_RE = re.compile(r"(?im)^\s*(?:Dependency:\s*#\d+|Lane-fill:\s*\S)")

# Marker on the LAST non-blank line — the "turn ends ⏳/✅" signal. `✅` alone is
# not enough (a mid-message ✅ row is common in an autopilot turn); the marker
# must be on the tail line, matching stop-check-status-marker.sh's LAST_LINE.
_DONE_RE = re.compile(r"✅\s*(?:DONE|complete|work complete)", re.I)

# Test seam (documented, like ND_FAKE_PANE_CAPTURE): canned dispatchable output
# in the `number<TAB>title` shape of `slice-quals/core-quals --list-dispatchable`
# — a value (inline, "\n"-joined lines) OR "@<path>" to read a file. Lets the
# hook stdin-contract test drive the FULL module with NO gh call. Ignored in
# production (env unset).
_FAKE_QUALS_ENV = "AIRULESET_LANEFILL_FAKE_QUALS"

_MAX_NAMED = 5


class _Unreadable(Exception):
    """A seam could not be read — the caller journals + fail-opens."""


def _last_marker(msg):
    """`"working"` / `"done"` / `None` from the LAST non-blank line of `msg`."""
    lines = [ln for ln in (msg or "").splitlines() if ln.strip()]
    if not lines:
        return None
    tail = lines[-1]
    if "⏳" in tail:
        return "working"
    if _DONE_RE.search(tail):
        return "done"
    return None


def has_justification(msg):
    """True iff the message carries a `Dependency: #N` / `Lane-fill:` line."""
    return bool(_JUSTIF_RE.search(msg or ""))


def decide(dispatchable, live, cap, mode, goal_armed, last_marker,
           justification_present):
    """PURE. Return ``(block: bool, reason: str)``.

    ``reason`` is the generic escape explanation (the I/O shell prepends the
    named dispatchable-ticket list). ``block`` is True ONLY when ALL hold:
    the session is goal-armed, the pane mode is ``parallel`` (sequential is
    exempt), the turn ends ``⏳``/``✅`` (``last_marker`` in working/done),
    the message carries NO justification, and there are MORE dispatchable
    tickets than live lanes with a free slot (``dispatchable > live`` AND
    ``live < cap``). Every other state allows.
    """
    if not goal_armed:
        return False, ""
    if mode != _MODE_PARALLEL:
        return False, ""                      # sequential exempt (#1031/#1035)
    if last_marker not in ("working", "done"):
        return False, ""                      # only ⏳/✅ turns
    if justification_present:
        return False, ""                      # intentional under-fill
    if dispatchable > live and live < cap:
        return True, _REASON
    return False, ""


_REASON = (
    "Zapíš do poslednej správy dôvod, ak je to zámer:\n"
    "  • `Dependency: #N — <čo blokuje>`  (ticket čaká na iný lane / merge)\n"
    "  • `Lane-fill: <dôvod>`  (napr. \"všetky ostatné čakajú na gk review\", "
    "\"bounce lane drží box\")\n"
    "Inak dispatchni ďalšie `isolation:\"worktree\"` autopilot-worker lane "
    "(continuous refill, SKILL.md Step 3.2)."
)


def _compose(count, live, cap, tickets):
    """The full block message: the fill gap + up to five named dispatchable
    tickets + the escape explanation."""
    free = max(0, cap - live)
    head = ("LANE-FILL: %d dispatchovateľných tiketov, ale len %d živých lane a "
            "%d voľných slotov (cap %d). Paralelný box nesmie skončiť ⏳/✅ turn "
            "s voľnými slotmi a dispatchovateľnými tiketmi bez lane (owner "
            "montalu1 18.9., #1078)." % (count, live, free, cap))
    rows = []
    for num, title in tickets[:_MAX_NAMED]:
        rows.append("  #%s %s" % (num, title))
    more = count - min(count, len(tickets[:_MAX_NAMED]))
    listing = "\n".join(rows)
    if more > 0:
        listing += "\n  … a ďalších %d" % more
    return "%s\nDispatchovateľné tikety:\n%s\n\n%s" % (head, listing, _REASON)


# --------------------------------------------------------------------------- #
# I/O shell — resolve the seven decide() inputs from the Stop payload.
# Each resolver is injectable (tests) with a real default. A read error raises
# _Unreadable → the caller journals + fail-opens.
# --------------------------------------------------------------------------- #
def _goal_armed(payload):
    """True iff the newest `/goal` marker in the session transcript is `set`
    (the canonical watchdog.goal_scan reader — the SAME marker the other Stop
    hooks read). A missing / unreadable transcript → not armed → allow (never
    a false enforce). Lazy import keeps gates package import cheap + stdlib-only."""
    tp = gates.field_of(payload, "transcript_path", "")
    if not tp:
        return False
    import watchdog.goal_scan as goal_scan
    _off, mark = goal_scan.scan_goal_markers(tp)
    return bool(mark) and mark.get("state") == "set"


def _mode(cwd):
    """The pane's EFFECTIVE concurrency mode via the single resolver
    (`cli_concurrency.resolve_mode` — declared window / project lane-resources /
    default parallel; the #1031/#1035 seam)."""
    import cli_concurrency
    return cli_concurrency.resolve_mode(cwd)


def _cap(cwd):
    """The box's lane cap (`watchdog.lane_resources.lane_resource_caps` total —
    the project's `.claude/lane-resources.json` `max_lanes` or the flat default,
    forced to 1 for a sequential pane)."""
    import watchdog.lane_resources as lr
    caps, _reason = lr.lane_resource_caps(cwd)
    return int(caps.get("total", lr.GOAL_LANE_SATURATION_WORKERS))


def _live(payload, cwd):
    """This session's live worker-lane count (`watchdog.count_live_workers`,
    disk state only — no tmux, no ps)."""
    import watchdog
    sid = gates.field_of(payload, "session_id", "") or "unknown"
    count, _ev = watchdog.count_live_workers(
        watchdog.PROJECTS_DIR, cwd, sid, time.time(), _FRESHNESS_S)
    return int(count)


def _parse_quals_lines(text):
    """`(count, [(num, title), …])` from `number<TAB>title` lines. An
    `unmeasurable:` head (a failed dep-meta read) → _Unreadable (fail-open)."""
    tickets = []
    for ln in (text or "").splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        if ln.startswith("unmeasurable"):
            raise _Unreadable("quals " + ln.strip())
        num, _tab, title = ln.partition("\t")
        num = num.strip()
        if not num.isdigit():
            continue
        tickets.append((int(num), title.strip()))
    return len(tickets), tickets


def _airuleset_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "airuleset.py")


def _dispatchable(payload, cwd):
    """`(count, tickets)` of DISPATCHABLE candidates, from ONE quals call —
    `slice-quals`/`core-quals --list-dispatchable` (authority-picked, the SAME
    seam `--count-dispatchable` reuses). The count is the line count, consistent
    with `--count-dispatchable` by construction. Test seam `_FAKE_QUALS_ENV`
    supplies canned lines with NO gh. Any failure → _Unreadable → fail-open."""
    fake = os.environ.get(_FAKE_QUALS_ENV)
    if fake is not None:
        if fake.startswith("@"):
            try:
                with open(fake[1:]) as f:
                    fake = f.read()
            except OSError as e:
                raise _Unreadable("fake quals file %s" % e)
        return _parse_quals_lines(fake)
    import subprocess
    import cli_quals
    try:
        root = cli_quals._repo_root(cwd=cwd) or cwd
        authority = cli_quals.resolve_authority(cwd=root)
    except Exception as e:  # noqa: BLE001
        raise _Unreadable("authority %s" % e)
    cmd = "core-quals" if authority == "full" else "slice-quals"
    try:
        r = subprocess.run(
            [sys.executable, _airuleset_path(), cmd, "--list-dispatchable"],
            cwd=cwd, capture_output=True, text=True, timeout=45)
    except Exception as e:  # noqa: BLE001
        raise _Unreadable("quals subprocess %s" % e)
    if r.returncode != 0:
        raise _Unreadable("quals rc %d" % r.returncode)
    return _parse_quals_lines(r.stdout)


def _journal(why):
    """The fail-open journal line (the gate's stderr channel, like
    questionscope's fail-open writes)."""
    sys.stderr.write("lane-fill: %s — not enforced\n" % why)


def run(payload, *, goal_fn=None, mode_fn=None, cap_fn=None, live_fn=None,
        quals_fn=None):
    """The I/O shell: resolve inputs, call decide, emit. CHEAP checks first
    (marker / justification, no reads) so the expensive quals/transcript reads
    fire only on an armed, parallel, ⏳/✅, unjustified turn. Any read error
    journals + allows (fail-open). Never raises."""
    msg = gates.field_of(payload, "last_assistant_message", "")
    marker = _last_marker(msg)
    if marker not in ("working", "done"):
        return                                # not a ⏳/✅ turn — allow, no reads
    if has_justification(msg):
        return                                # intentional under-fill — allow
    try:
        armed = (goal_fn or _goal_armed)(payload)
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (goal-armed: %s)" % e)
        return
    if not armed:
        return
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    try:
        mode = (mode_fn or _mode)(cwd)
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (mode: %s)" % e)
        return
    if mode != _MODE_PARALLEL:
        return                                # sequential exempt
    try:
        cap = (cap_fn or _cap)(cwd)
        live = (live_fn or _live)(payload, cwd)
        count, tickets = (quals_fn or _dispatchable)(payload, cwd)
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (%s)" % e)
        return
    block, _reason = decide(count, live, cap, mode, armed, marker, False)
    if not block:
        return
    gates.emit_block_stderr(_compose(count, live, cap, tickets))


def main():
    run(gates.read_payload())
    gates.allow()


if __name__ == "__main__":
    main()
