"""gates.filing.presence -- the UNATTENDED/away read + the #842 dismissal-word
and #962 owner-quote gates for the ungated-issue-filing gate (#1020 Part 2).

`is_away` reimplements hooks/lib-presence.sh's `airuleset_presence_is_away`
(the 900s `/tmp/claude-user-active-<sid>` marker read) so the whole gate runs in
Python -- fail-OPEN (PRESENT) on any unmeasurable presence state, exactly like
the bash helper. The dismissal-word + owner-quote helpers are VERBATIM from the
embedded classifier.
"""
import os
import re
import time
from datetime import date, timedelta


def is_away(session_id):
    """True when the session is UNATTENDED: the presence marker
    /tmp/claude-user-active-<session_id> is at least
    AIRULESET_MAIN_GUARD_AWAY_S seconds old (default 900; 0 disables -> always
    PRESENT; a garbage value falls back to 900). Fail-OPEN (returns False =
    PRESENT) when the marker is absent/unreadable or session_id is empty --
    never manufacture an unattended verdict from an unmeasurable state
    (hooks/lib-presence.sh's documented bias)."""
    raw = os.environ.get("AIRULESET_MAIN_GUARD_AWAY_S", "900")
    try:
        away_s = int(raw)
    except (TypeError, ValueError):
        away_s = 900
    if away_s <= 0 or not session_id:
        return False
    mark = "/tmp/claude-user-active-%s" % session_id
    try:
        active_at = os.stat(mark).st_mtime
    except OSError:
        return False
    if active_at <= 0:
        return False
    return (time.time() - active_at) >= away_s


# #842 req 4 -- dismissal words in a NEW issue body from an UNATTENDED session.
# `test-strictness.md` + `no-dropped-work.md` already ban these as dismissals of
# a test failure; a ticket that merely SAYS "the test is flaky" / "pre-existing
# failure" is the same dismissal in durable form -- the loop must FIX the test,
# not file its excuse. Word-boundary-ish, case-insensitive. `out of scope` is
# the weakest signal (it is also a legitimate scope-gate justification), so a
# discovery filing that uses the literal phrase in prose is OVER-blocked here --
# an accepted false-block bias (the remedy: name the SPECIFIC criterion instead
# of the vague phrase), consistent with this hook's documented "get it wrong
# toward strict, a false block costs one line" stance, and bounded to the
# unattended path only (an attended owner filing is never dismissal-blocked).
DISMISSAL_WORD_RE = re.compile(
    r"\bflak(?:e|es|y|iness)\b|\bpre-?existing\b|\bintermittent(?:ly)?\b|"
    r"\bout\s+of\s+scope\b",
    re.IGNORECASE)


def _dismissal_word(body):
    """The first dismissal word/phrase found in `body`, or None."""
    if not body:
        return None
    m = DISMISSAL_WORD_RE.search(body)
    return m.group(0).strip() if m else None


# #962 -- owner-quote detection for the presence-required exemption. A body
# quoting an owner message with `verbatim` and a date from today or yesterday
# (D.M.YYYY format) is evidence the owner WAS present and the user-request
# is legitimate even in an unattended session.
_VERBATIM_RE = re.compile(r'\bverbatim\b', re.I)


_EU_DATE_RE = re.compile(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b')


def _has_recent_owner_quote(body):
    """True when the body contains 'verbatim' AND a D.M.YYYY date that is
    today or yesterday (a calendar-day window covering the last ~48h).
    Returns False on ANY parse failure -- fail toward blocking, matching
    this hook's own stated bias throughout."""
    if not body or not _VERBATIM_RE.search(body):
        return False
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)
        for m in _EU_DATE_RE.finditer(body):
            day_n, month_n, year_n = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                d = date(year_n, month_n, day_n)
            except ValueError:
                continue
            if d == today or d == yesterday:
                return True
    except Exception:
        return False
    return False
