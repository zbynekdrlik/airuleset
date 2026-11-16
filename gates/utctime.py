"""gates.utctime -- raw UTC clock times in owner-facing prose (#1343).

Owner ruling 16.11.2026: every clock time shown to the owner is
Europe/Bratislava local time. A UTC timestamp read from `gh` or an API
(`...Z`) is converted before it is quoted, and a bare UTC hour is never given
(incident: devbridge said "hotovo okolo 21:00" from a UTC `gh` time while it
was 21:35 CET for the owner).

`hooks/stop-check-prose-violations.sh` pre-screens the message with a cheap
grep and only then runs this module ONCE (`python3 -P -m gates.utctime`),
feeding the MENTION-stripped message on stdin (fenced code, inline code and
double-quoted spans already removed, #96). Linear regexes only: the first cut
did a per-line bash strip loop that was quadratic (3000 exempt lines ~14 s
against the 15 s hook limit, review F6).

Exit codes: 0 = clean (or an honoured, logged bypass), 2 = raw UTC time found
(the offending line on stdout), anything else = infra failure (the hook fails
OPEN on it, never fabricating a block).
"""
import os
import re
import sys

# A UTC time must end here: not glued to a filename / path / offset character.
_TRAIL = r"(?=[^\w./+-]|\.(?:\W|$)|$)"
_ISO_RX = re.compile(
    r"(?<![\w./=<>+-])\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z"
    + _TRAIL)
# HH:MM[:SS] + a UTC zone. `UTC+0`/`UTC+00:00` is still UTC; `UTC+1` is a
# local offset (Bratislava in winter) and passes via _TRAIL's `+` exclusion.
# A bare `Z` must be glued to the time: `7:30 Z Košíc` is a Slovak preposition.
_HM_RX = re.compile(
    r"(?<![\w:./])[0-2]?\d:[0-5]\d(?::[0-5]\d)?"
    r"(?:\s*(?i:utc|gmt)(?:[+-]0{1,2}(?::?00)?)?|Z)" + _TRAIL)
_LOCAL_RX = re.compile(r"(?<![\w:./])[0-2]?\d:[0-5]\d")
_URL_RX = re.compile(r"https?://\S+")
_BRACKET_RX = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_MAX_BRACKET_PASSES = 32  # nesting depth bound; each pass is linear
# The bypass must carry a reason and sit in prose (a backticked MENTION of the
# marker is stripped before this module sees the text, review F4).
_BYPASS_RX = re.compile(r"airuleset:utc-time-ok[ \t]+(\S[^\n]*)")
_PLACEHOLDER_RX = re.compile(r"^<[^>]*>$")
AUDIT_LOG = "utc-time-bypasses.log"


def _has_utc(text):
    return bool(_ISO_RX.search(text) or _HM_RX.search(text))


def _strip_brackets(text):
    for _ in range(_MAX_BRACKET_PASSES):
        text, n = _BRACKET_RX.subn(" ", text)
        if not n:
            break
    return text


def find_raw_utc(text):
    """The first prose line quoting a raw UTC clock time, else None.

    Skipped: blockquoted log lines (`> ...`) and URLs. A UTC time inside
    parentheses / square brackets passes only when the SAME line also states a
    time outside them (`21:45 (20:45 UTC)`)."""
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">"):
            continue
        prose = _URL_RX.sub(" ", line)
        if not _has_utc(prose):
            continue
        outside = _strip_brackets(prose)
        if _has_utc(outside) or not _LOCAL_RX.search(outside):
            return line
    return None


def bypass_reason(text):
    m = _BYPASS_RX.search(text or "")
    if not m:
        return None
    reason = m.group(1).strip()
    # The doc's `<reason>` placeholder copied verbatim is not a reason.
    return None if _PLACEHOLDER_RX.match(reason) else reason


def main():
    text = sys.stdin.read()
    hit = find_raw_utc(text)
    if hit is None:
        return 0
    reason = bypass_reason(text)
    if reason:
        from gates import audit
        try:
            cwd = os.getcwd()
        except OSError:  # a removed worktree must not lose the audit line
            cwd = "?"
        audit.append_line(AUDIT_LOG, "%s  session=%s  cwd=%s  airuleset:utc-time-ok %s" % (
            audit.iso_now(), os.environ.get("AIRULESET_SESSION_ID") or "?",
            cwd, reason))
        print("utc-time gate: bypass honoured (%s)" % reason, file=sys.stderr)
        return 0
    print(hit[:200])
    return 2


if __name__ == "__main__":
    sys.exit(main())
