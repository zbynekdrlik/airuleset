"""#1301 / #1395 -- the `shrink <N>M` footer segment: this session's FILE is heavy.

A long-lived Claude Code session keeps its whole history in its transcript, and
the per-turn work grows with it until the Node main thread saturates: david3@subdev
pinned a core at a 68 MB transcript (10.11.2026) and again at 97 MB (13.11.,
keystrokes lagging, owner report "nedá sa písať"). #1301 said `restart`, but a
`claude -c` restart reloads the same file. #1395: `airuleset.py session-shrink`
archives every record before the last /compact, so the session keeps working on
/compact (never /clear) with a small file. Only the person at that terminal exits
the session (the shrink refuses a live one); the watchdog never kills it. So the
lever is a signal in the one place that person looks: the session's own footer.

The statusline shim calls `shrink_segments(payload)` on every render. It reads
the session's own `transcript_path` from the payload with ONE `os.stat` (never
the content: a transcript reaches hundreds of MB) and returns a red
`shrink <N>M` segment at or above `RESTART_TRANSCRIPT_BYTES`, else nothing.
`restart_segments` stays as an alias for shims installed before #1395.
Stdlib only, never raises (a footer segment must never break the render).
"""
import os

# Just under the first observed lockup (david3, 68 MB on 10.11.2026).
RESTART_TRANSCRIPT_BYTES = 64 * 1024 * 1024
_RED = "\033[38;5;196m%s\033[0m"


def transcript_bytes(payload):
    """Size of the session's own transcript from the statusline payload, or
    None when the payload names none or it cannot be stat'ed."""
    if not isinstance(payload, dict):
        return None
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.stat(path).st_size
    except (OSError, ValueError):
        return None


def shrink_segments(payload, limit=RESTART_TRANSCRIPT_BYTES):
    """`["shrink <N>M"]` (red) when this session's transcript is at least
    `limit` bytes, else `[]`. A list so the shim can `segs += ...` it."""
    try:
        size = transcript_bytes(payload)
        if size is None or size < limit:
            return []
        return [_RED % ("shrink %dM" % (size // (1024 * 1024)))]
    except Exception:  # noqa: BLE001 — a footer segment never breaks the render
        return []


restart_segments = shrink_segments   # shims installed before #1395 call this name
