"""Last-use age of a scratchpad child (#1195 item 5).

The disk-guard scratch rung (#849 ask 2) reclaims individual top-level
children of a LIVE session's ``scratchpad/``. It used to age a child by its
newest MTIME only, so a helper script the session executes (reads) many times
a day but never rewrites looked days old and was deleted from under the
running session (controller, 2026-09-30 04:24Z: ``merge-one.sh`` and
``ratchet-fix.py`` among 1876 deleted children). A child is now aged by its
last USE: the newest ``max(st_mtime, st_atime)`` over its regular files.

Why atime is a sound signal here:

- The managed mounts are ``relatime``. A read updates atime when the old atime
  is older than mtime or older than 24 h, so atime trails the real last read by
  at most one day. That is well inside the 2-day child floor
  (``SCRATCHPAD_CHILD_MIN_AGE_DAYS``), so a child read at least daily is kept.
- On a ``noatime`` mount atime never advances past creation. ``max`` then
  returns the mtime, which is exactly the previous rule, so there is no new risk.

Two invariants, both locked by ``tests/test_scratch_atime_1195.py``:

- **Only FILE atimes count.** ``os.walk`` lists every directory it enters, and
  that listing bumps the DIRECTORY's own atime under relatime. If a dir atime
  counted, the sweep would mark every dir as used by walking it. An empty
  directory therefore falls back to its own MTIME, never its atime.
- **Stat only, never open.** Every entry is ``os.lstat``-ed, and file content is
  never read, so the sweep cannot refresh the atime it measures.

``cli_target_purge._dir_stats`` (shared by the target, worktree and disk-guard
size rungs) deliberately stays mtime-only. Stdlib only, no airuleset import.
"""

import os


def scratch_use_stat(path):
    """``(size_bytes, newest_mtime, last_use)`` for one scratchpad child in a
    single walk.

    ``last_use`` is ``max(newest file mtime, newest file atime)``. For a regular
    file (or any non-directory), it is its own ``lstat``. A directory walks the
    same way ``_dir_stats`` does: ``os.walk`` without following links, plus an
    ``os.lstat`` per non-directory entry. An empty tree reports the directory's
    own mtime for both values. Raises ``OSError`` when the child itself cannot be
    stat-ed; the caller turns that into a KEEP row."""
    top = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path):
        return top.st_size, top.st_mtime, max(top.st_mtime, top.st_atime)
    total = 0
    newest_mtime = None
    last_use = None
    for dirpath, _dirnames, filenames in os.walk(path, topdown=True,
                                                 onerror=lambda e: None):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += st.st_size
            if newest_mtime is None or st.st_mtime > newest_mtime:
                newest_mtime = st.st_mtime
            use = max(st.st_mtime, st.st_atime)
            if last_use is None or use > last_use:
                last_use = use
    if newest_mtime is None:
        # #355 finding 1 (empty tree): the dir's own MTIME, never its atime,
        # because the walk just listed it and so refreshed that atime itself.
        return total, top.st_mtime, top.st_mtime
    return total, newest_mtime, last_use
