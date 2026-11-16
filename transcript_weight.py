"""#1301 -- the `restart <N>M` footer segment: this session has grown too heavy.

A long-lived Claude Code session keeps its whole history in its process, and the
per-turn work grows with it until the Node main thread saturates: david3@subdev
pinned a core at a 68 MB transcript (10.11.2026) and again at 97 MB (13.11.,
keystrokes lagging, owner report "nedá sa písať"). Only a FRESH session fixes it,
and only the owner (or the person at that terminal) restarts a session -- the
watchdog never kills or restarts one. So the lever is a signal in the one place
that person looks: the session's own footer.

The statusline shim calls `restart_segments(payload)` on every render. It reads
the session's own `transcript_path` from the payload with ONE `os.stat` (never
the content: a transcript reaches hundreds of MB) and returns a red
`restart <N>M` segment at or above `RESTART_TRANSCRIPT_BYTES`, else nothing. A
new session starts a new transcript, so the segment disappears by itself.
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


def restart_segments(payload, limit=RESTART_TRANSCRIPT_BYTES):
    """`["restart <N>M"]` (red) when this session's transcript is at least
    `limit` bytes, else `[]`. A list so the shim can `segs += ...` it."""
    try:
        size = transcript_bytes(payload)
        if size is None or size < limit:
            return []
        return [_RED % ("restart %dM" % (size // (1024 * 1024)))]
    except Exception:  # noqa: BLE001 — a footer segment never breaks the render
        return []
