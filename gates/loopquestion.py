"""gates.loopquestion -- `❓ NEEDS YOU` inside an ARMED loop with work left
(#1329).

`❓ NEEDS YOU` means "I stopped, nothing else is workable", and the /goal
evaluator ends the loop on it (stop condition A). On david3 (15.11.2026) a
stream ended on it with 28 dispatchable tickets and the evaluator accepted.
This gate blocks that turn and forces ASK-AND-CONTINUE (`❓ ASKED` + `⏳
WORKING`, the question carried by `needs-answer` + the footer U).

It blocks only when ALL hold: the last non-blank line is a `❓ NEEDS YOU:`
marker; a /goal loop is armed for this pane (`◎ /goal` in the pane's bottom
rows, where the footer renders it -- the signal `notify-discord-pending.sh
goal_armed` and the watchdog read; `ND_FAKE_PANE_CAPTURE` is the shared test
seam); the per-cwd footer cache (`statusbar.cwd_key`, the file the footer
renders) is fresh, not carried forward over a failed refresh (`stale_since`),
and its owed count `open + done` (I + C, `statusbar.obligation_count`), minus
the asked ticket while it is not yet in U (#1342, `_asked_in_i`), is > 0.

Honest limit: the Stop path has no dispatchable count (`slice-quals
--count-dispatchable` takes ~20 s against the hook's 5 s budget), so `I` -- a
superset that also holds dep-wait rows -- stands in. When every I row is
dep-wait the session declares it with a `Dispatchable: 0 — <why>` line (the
pasted `--count-dispatchable` result), visible to the /goal evaluator, and the
gate allows; without it a dep-wait-only box would be pushed to a `⏳ WORKING`
that the liveness hook refuses.

FAIL-OPEN: no pane / no tmux, a missing, unreadable, stale or carried-forward
cache, a null `open` (a failed refresh) -> allow, logged to
`~/.claude/loop-question-gate.log` (never a block on an unreadable count). The
hook owns the shared 3-retry cap, so the 4th identical stop still passes: the
gate is a strong nudge, never a wedge.

Invoked as `python3 -P -m gates.loopquestion` with the Stop payload on stdin;
exit 2 (reason on STDERR) = block, exit 0 = allow.
"""
import os
import re
import subprocess
import time

import gates

CACHE_MAX_AGE_S = 15 * 60
_NEEDS_YOU_RE = re.compile(r"❓\s*\**\s*NEEDS\s+YOU\s*\**\s*:", re.IGNORECASE)
_DISPATCHABLE_0_RE = re.compile(r"(?mi)^[ \t*>-]*Dispatchable:\s*0\b")
_FOOTER_ROWS = 6   # the footer/statusline rows at the bottom of the pane
_QUESTION_HEAD_RE = re.compile(r"Ot[áa]zka\s*[—–-]\s*projekt", re.IGNORECASE)
# a same-repo `#N`: not glued to a repo slug (`owner/repo#N`) or a word.
_BARE_REF_RE = re.compile(r"(?<![\w/.-])#(\d{1,6})\b")


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
    """(count, why): the cached owed count `open + done` when fresh, not
    carried forward and readable, else (None, why)."""
    import statusbar
    cache = statusbar._load(statusbar.cache_dir(home) /
                            (statusbar.cwd_key(cwd) + ".json"))
    if not isinstance(cache, dict):
        return None, "no footer cache"
    if cache.get("stale_since"):
        return None, "footer cache carried forward over a failed refresh"
    n, ts = statusbar.obligation_count(cwd, home)
    if n is None:
        return None, "unmeasurable I (open=%r)" % (cache.get("open"),)
    if not isinstance(ts, (int, float)) or not 0 <= now - ts <= CACHE_MAX_AGE_S:
        return None, "stale footer cache"
    return n, ""


def _asked_in_i(msg, cwd, home):
    """#1342: 1 when the asked ticket is still counted in I, else 0. The asked
    refs are the bare same-repo `#N` of the question block (from the last
    `**Otázka — projekt` line, else the final line); one counts when the cache
    lists it as a counted row (`question_cover_rows`) not yet in U
    (`question_cover_numbers` / `user_waiting_numbers`). At most one: one
    question, one decision. A cache without those fields subtracts nothing."""
    import statusbar
    cache = statusbar._load(statusbar.cache_dir(home) /
                            (statusbar.cwd_key(cwd) + ".json"))
    rows = cache.get("question_cover_rows") if isinstance(cache, dict) else None
    if not isinstance(rows, list):
        return 0
    in_u = set()
    for key in ("question_cover_numbers", "user_waiting_numbers"):
        if isinstance(cache.get(key), list):
            in_u.update(cache[key])
    lines = msg.splitlines()
    heads = [i for i, ln in enumerate(lines) if _QUESTION_HEAD_RE.search(ln)]
    block = "\n".join(lines[heads[-1]:]) if heads else lines[-1]
    refs = {int(n) for n in _BARE_REF_RE.findall(block)}
    return 1 if any(n in rows and n not in in_u for n in refs) else 0


def decide(payload, capture=None, home=None, now=None):
    """Return `(block, reason)` for the Stop payload (JSON text)."""
    msg = gates.field_of(payload, "last_assistant_message", "") or ""
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    if not lines or not _NEEDS_YOU_RE.search(lines[-1]):
        return False, ""
    home = home or os.path.expanduser("~")
    if _DISPATCHABLE_0_RE.search(msg):
        return False, ""
    pane = pane_capture() if capture is None else capture
    if "◎ /goal" not in "\n".join(
            [ln for ln in pane.splitlines() if ln.strip()][-_FOOTER_ROWS:]):
        if not pane:
            _log(home, "allow: no pane capture (no tmux)")
        return False, ""
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    n, why = _workable(cwd, home, time.time() if now is None else now)
    if n is None:
        _log(home, "allow %s: %s" % (cwd, why))
        return False, ""
    n -= _asked_in_i(msg, cwd, home)
    if n <= 0:
        return False, ""
    return True, (
        "Tvoj ťah končí na `❓ NEEDS YOU:` v zapnutej /goal slučke, ale footer "
        "hlási I %d (workable tickety okrem toho, na ktorý sa pýtaš). "
        "`❓ NEEDS YOU` znamená „nič iné sa nedá "
        "robiť\" a ukončí slučku. Poradie priorít určuje PORADIE, nikdy ROZSAH: "
        "keď je hlava poradia zablokovaná, začni ďalší workable ticket v tomto "
        "ťahu. Otázku polož ako ASK-AND-CONTINUE: `❓ ASKED: <otázka>` v tele "
        "(celý `**Otázka — projekt …:**` blok), `needs-answer` + komentár na "
        "tikete, a ťah ukonči `⏳ WORKING: <čo robíš>` (spusti ďalší ticket — "
        "⏳ potrebuje živú prácu). Ak je naozaj každý I ticket dep-wait, napíš "
        "riadok `Dispatchable: 0 — <prečo>` (výstup `--count-dispatchable`). "
        "(#1329)" % n)


def main():
    payload = gates.read_payload()
    block, reason = decide(payload)
    if block:
        gates.emit_block_stderr(reason)
    gates.allow()


if __name__ == "__main__":
    main()
