"""#1195 item 4 — the worktree reclaimers never touch the REAL home under pytest.

At the #1194 split a test missed a patch seam, so a real non-dry-run
`cli_worktree_stale.sweep_stale_worktrees` ran against the controller's real
`~/.claude` (200 SKIP rows, `last_run` stamped). The disk filer already
refused its real default under pytest (#1136); the reclaimers did not.

`sweep_stale_worktrees` and `cli_lane_target_reclaim.purge_merged_lane_targets`
call `refuse_real_paths_under_pytest` right after resolving their paths, before
the cadence gate, discovery or any write. It reuses the #1136 detection
(`watchdog.disk_guard_escalation.running_under_pytest`), never a second copy.

The "real home" is the account's passwd home plus the home the `CLAUDE_DIR`
defaults were resolved from at import. A test that points $HOME at a tmp dir
and passes that effective home is not the real one.

It RAISES (never a silent return), so a direct call fails its test. An
`except Exception` wrapper such as the cmd_install step still catches it, but
by then nothing has been read, walked or written.

Its own leaf, not `cli_worktree_common`: the #1194 facade lock pins the split
leaves to exactly the pre-split names. Stdlib only at module level.
"""

import os
import sys
from pathlib import Path

from cli_worktree_common import CLAUDE_DIR


class RealPathUnderPytest(RuntimeError):
    """A reclaimer was pointed at the account's real home under pytest."""


def _real_homes():
    homes = {CLAUDE_DIR.parent}
    try:
        import pwd
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (ImportError, KeyError, OSError) as e:
        print("reclaim-guard: no passwd home (%r), using %s only" % (e, CLAUDE_DIR.parent),
              file=sys.stderr)
    return {Path(os.path.realpath(h)) for h in homes}


def refuse_real_paths_under_pytest(caller, dry_run, home=None, **paths):
    """Raise `RealPathUnderPytest` when a NON-dry-run call under pytest resolves
    to the real home: `home` equal to it or one of its ancestors (a walk from
    there reaches the real repos), or any path inside its `.claude`.
    `home=None` means the caller will not walk a home; a `None` path is
    ignored. Returns None otherwise."""
    if dry_run:
        return
    from watchdog.disk_guard_escalation import running_under_pytest
    if not running_under_pytest():
        return
    homes = _real_homes()
    hits = []
    if home is not None:
        h = Path(os.path.realpath(home))
        if any(h == r or h in r.parents for r in homes):
            hits.append("home=%s" % h)
    for name, p in paths.items():
        if p is None:
            continue
        q = Path(os.path.realpath(p))
        if any(q == r / ".claude" or (r / ".claude") in q.parents for r in homes):
            hits.append("%s=%s" % (name, q))
    if hits:
        raise RealPathUnderPytest(
            "%s: refused under pytest, a non-dry-run call resolves to the real "
            "home (%s); inject tmp home/log/state paths (#1195)"
            % (caller, ", ".join(hits)))
