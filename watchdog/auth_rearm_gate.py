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

Review round 2: a process STARTED after the newest 401 (job 1's relaunch, or the
owner's `claude -c`) read the credentials at launch, so the old 401 is history
(the job 1 `authdead_is_historical` rule) and the re-arm proceeds. The hold is
journalled once per episode (B9 `first_hold` shape), not every sweep. Any
trailing api-error counts, not only the revoked 401: a "Please run /login"
clear leaves the process just as dead, and a long retry run of another error
must not push the 401 out of the reader's 200-line tail. By design an owner
`/login` inside the same process writes only user/system entries, so the hold
lasts until that session's next real turn (job 1's `continue` or the owner's).
"""
from watchdog.decide import authdead_is_historical
from watchdog.transcripts import transcript_first_error_ts, transcript_last_error_ts

_HOLD_KEY = "auth_rearm_hold"   # state: {sid: True} while a hold was journalled


def _proc_start(pid, run):
    from watchdog.tmux_io import _pane_claude_start_epoch
    return _pane_claude_start_epoch(pid, run)


def still_auth_dead(tpath, pid=None, run=None):
    """True while the transcript's trailing run is an api-error stall (any
    error: a revoked 401, a "Please run /login", retries) that the running
    process received (no real reply since, process not restarted after it). Unreadable -> False: the caller's other gates decide, as before
    #1303."""
    if not tpath:
        return False
    if transcript_first_error_ts(tpath) is None:
        return False
    last_err = transcript_last_error_ts(tpath)
    if pid and authdead_is_historical(last_err, _proc_start(pid, run)):
        return False
    return True


def hold_line(loc, sid, tpath, pid, run, armed, state):
    """The decision-log line that HOLDS the auth re-arm, "" for a hold already
    journalled this episode, or None to proceed. Only an idle dark pane
    (`armed is False`) is asked: anything else never re-arms anyway."""
    holds = state.setdefault(_HOLD_KEY, {}) if isinstance(state, dict) else {}
    if armed is not False or not still_auth_dead(tpath, pid, run):
        holds.pop(sid, None)
        return None
    if holds.get(sid):
        return ""
    holds[sid] = True
    return ("auth-rearm %s sid=%s -> cleared-by-auth but the session still "
            "ends in an api-error stall, e.g. a revoked-token 401 (no real reply yet) -- hold" % (loc, sid))
