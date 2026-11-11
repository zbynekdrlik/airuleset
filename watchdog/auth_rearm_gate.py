"""#1303 review -- an auth re-arm waits until the session works again.

Since #1303 an `auth-rearm` really types (it rides the always-on `goal-arm`
kind). The process that received the rotation 401 may still hold the revoked
token: a `/goal` typed into it earns a fresh 401, CC clears the goal again, and
dark-watch would record the next re-arm at once -- a loop the #890 rate limit
never sees, since it counts only `sent` deliveries. It also gives job 1 a newer
401 than the credentials file, so job 1 reads "stale" and holds its recovery.

So the re-arm is recorded only once the transcript's trailing run is no longer
a revoked-token stall: job 1's resume (or the owner) got a real reply out of
the session, which proves the running process holds a working token. CC's
`Goal cleared …` notice is a `system` entry, which the reader skips, so the
cleared session reads "still dead" until that real reply lands.
"""
from watchdog.decide import is_oauth_revoked
from watchdog.transcripts import transcript_first_error_ts


def still_auth_dead(tpath):
    """True while the transcript's trailing run holds a revoked-token 401 (the
    session has produced no real reply since). None/unreadable -> False: the
    caller's other gates decide, as before #1303."""
    if not tpath:
        return False
    try:
        ts = transcript_first_error_ts(tpath, match=is_oauth_revoked)
    except OSError:
        return False
    return ts is not None


def hold_line(loc, sid, tpath):
    """The decision-log line that HOLDS the auth re-arm, or None to proceed."""
    if not still_auth_dead(tpath):
        return None
    return ("auth-rearm %s sid=%s -> cleared-by-auth but the session still "
            "ends in a revoked-token 401 (no real reply yet) -- hold" % (loc, sid))
