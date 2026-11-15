"""gates.loopquestion -- `❓ NEEDS YOU` inside an ARMED loop with work left
(#1329).

`❓ NEEDS YOU` means "I stopped, nothing else is workable", and the /goal
evaluator ends the loop on it (stop condition A). On david3 (15.11.2026) a
stream ended on it with 28 dispatchable tickets and the evaluator accepted.
This gate blocks that turn and forces ASK-AND-CONTINUE (`❓ ASKED` + `⏳
WORKING`, the question carried by `needs-answer` + the footer U).

It blocks only when ALL hold: the last non-blank line is a `❓ NEEDS YOU:`
marker; a /goal loop is armed for this pane (`◎ /goal` in the pane capture --
the signal `notify-discord-pending.sh goal_armed` and the watchdog read;
`ND_FAKE_PANE_CAPTURE` is the shared test seam); the per-cwd footer cache
(`~/.claude/tickets-status/<cwd-key>.json`, the one the footer renders) is
fresh and its `open` (the workable `I`) is > 0.

Honest limit: the Stop path has no dispatchable count (`slice-quals
--count-dispatchable` takes ~20 s against the hook's 5 s budget), so `I` --
a superset that also holds dep-wait rows -- stands in. Over-blocking there is
benign: the forced form (ASK-AND-CONTINUE) is still correct.

FAIL-OPEN: no pane / no tmux, a missing, unreadable or stale cache, a null
`open` (a failed refresh) -> allow, logged to `~/.claude/loop-question-gate.log`
(never a block on an unreadable count). The hook owns the retry cap.

Invoked as `python3 -P -m gates.loopquestion` with the Stop payload on stdin;
exit 2 (reason on STDERR) = block, exit 0 = allow.
"""
import hashlib
import json
import os
import re
import subprocess
import time

import gates

CACHE_MAX_AGE_S = 15 * 60
_NEEDS_YOU_RE = re.compile(r"❓\s*\**\s*NEEDS\s+YOU\s*\**\s*:", re.IGNORECASE)


def _log(home, msg):
    path = os.path.join(home, ".claude", "loop-question-gate.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except OSError:
        pass  # airuleset:script-ok a diagnostic log must never fail the Stop


def pane_capture():
    """This pane's text (`$TMUX_PANE`), the seam value, or "" (not armed)."""
    if "ND_FAKE_PANE_CAPTURE" in os.environ:
        return os.environ["ND_FAKE_PANE_CAPTURE"]
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return ""
    try:
        return subprocess.run(["tmux", "capture-pane", "-p", "-t", pane],
                              capture_output=True, text=True,
                              timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _workable(cwd, home, now):
    """(count, why): the cached `open` when fresh and int, else (None, why)."""
    key = hashlib.sha1(str(cwd).encode()).hexdigest()[:12]
    path = os.path.join(home, ".claude", "tickets-status", key + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        return None, "no footer cache"
    if not isinstance(cache, dict):
        return None, "malformed footer cache"
    ts, n = cache.get("ts"), cache.get("open")
    if not isinstance(ts, (int, float)) or not 0 <= now - ts <= CACHE_MAX_AGE_S:
        return None, "stale footer cache"
    if type(n) is not int:
        return None, "unmeasurable I (open=%r)" % (n,)
    return n, ""


def decide(payload, capture=None, home=None, now=None):
    """Return `(block, reason)` for the Stop payload (JSON text)."""
    msg = gates.field_of(payload, "last_assistant_message", "") or ""
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    if not lines or not _NEEDS_YOU_RE.search(lines[-1]):
        return False, ""
    if "◎ /goal" not in (pane_capture() if capture is None else capture):
        return False, ""
    home = home or os.path.expanduser("~")
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    n, why = _workable(cwd, home, time.time() if now is None else now)
    if n is None:
        _log(home, "allow %s: %s" % (cwd, why))
        return False, ""
    if n <= 0:
        return False, ""
    return True, (
        "Tvoj ťah končí na `❓ NEEDS YOU:` v zapnutej /goal slučke, ale footer "
        "hlási I %d (workable tickety). `❓ NEEDS YOU` znamená „nič iné sa nedá "
        "robiť\" a ukončí slučku. Poradie priorít určuje PORADIE, nikdy ROZSAH: "
        "keď je hlava poradia zablokovaná, začni ďalší workable ticket v tomto "
        "ťahu. Otázku polož ako ASK-AND-CONTINUE: `❓ ASKED: <otázka>` v tele "
        "(celý `**Otázka — projekt …:**` blok), `needs-answer` + komentár na "
        "tikete, a ťah ukonči `⏳ WORKING: <čo robíš>`. (#1329)" % n)


def main():
    payload = gates.read_payload()
    block, reason = decide(payload)
    if block:
        gates.emit_block_stderr(reason)
    gates.allow()


if __name__ == "__main__":
    main()
