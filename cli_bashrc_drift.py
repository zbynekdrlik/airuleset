"""airuleset ~/.bashrc drift scan (#1015).

A stray `export CLAUDE_CODE_*` line in a login shell's ~/.bashrc / ~/.profile —
OUTSIDE the managed airuleset marker blocks — silently overrides the fleet's
Claude Code environment (dev2 exported `CLAUDE_CODE_DISABLE_MOUSE=1` for weeks,
taking native mouse scrolling away, and nothing reported it). The managed
launcher now OWNS the mouse/alternate-screen toggles (it `unset`s them before
exec, cli_claude_scripts.py), so the launch env is fixed regardless; this module
is the "make the gap LOUD" backstop the incident asked for: ONE scanner read by
two consumers —

  - `airuleset.py status` (`bashrc_drift_status_row`, a live scan naming file:line),
  - the daily conformance sweep Job 34 (`count_bashrc_drift`, a report-only
    `bashrc_drift` fact — watchdog/conformance.py, the #1047 pattern).

The scan NEVER edits ~/.bashrc — the unmanaged region is the owner's; airuleset
reports it (the 22.9. dev2 removal was an explicit owner-requested one-off).
"""
import re
from pathlib import Path

# The managed marker blocks (cli_bashrc_appliers.py) all use this sentinel
# convention: `# >>> airuleset: <name> >>>` ... `# <<< airuleset: <name> <<<`.
# Matching the PREFIX generically (rather than an explicit list of the 5 current
# block names) means a NEW managed block never needs a change here.
_BLOCK_START_PREFIX = "# >>> airuleset:"
_BLOCK_END_PREFIX = "# <<< airuleset:"

# A managed `export CLAUDE_CODE_*` line (leading whitespace tolerated); a
# commented line (starting `#`) never matches because `export` is not first.
_EXPORT_RE = re.compile(r"^\s*export\s+CLAUDE_CODE_\w+=")


def default_bashrc_paths():
    """The login-shell files a stray export can hide in."""
    home = Path.home()
    return [home / ".bashrc", home / ".profile"]


def scan_bashrc_drift(paths=None):
    """Return `[(path_str, lineno, stripped_line), ...]` for every
    `export CLAUDE_CODE_*` line found OUTSIDE a managed airuleset marker block,
    across `paths` (default: ~/.bashrc, ~/.profile). A missing / unreadable file
    is a legitimate state (a box may have no ~/.profile) and is skipped, never an
    error. Lines are numbered from 1."""
    if paths is None:
        paths = default_bashrc_paths()
    hits = []
    for p in paths:
        path = Path(p)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # absent / unreadable file — a valid state; nothing to report here.
            continue
        inside_block = False
        for i, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if stripped.startswith(_BLOCK_START_PREFIX):
                inside_block = True
                continue
            if stripped.startswith(_BLOCK_END_PREFIX):
                inside_block = False
                continue
            if inside_block:
                continue
            if _EXPORT_RE.match(raw):
                hits.append((str(path), i, stripped))
    return hits


def count_bashrc_drift(paths=None):
    """The number of drift lines (report-only fact for the conformance sweep)."""
    return len(scan_bashrc_drift(paths))


def bashrc_drift_status_row(paths=None):
    """A single `airuleset.py status` row, or None when there is no drift (the
    caller prints nothing on a clean box). On drift:
    `bashrc-drift: <file>:<line> <export line>; ...`."""
    hits = scan_bashrc_drift(paths)
    if not hits:
        return None
    parts = ["%s:%d %s" % (path, lineno, line) for path, lineno, line in hits]
    return "bashrc-drift: " + "; ".join(parts)
