"""airuleset ~/.bashrc drift scan (#1015).

A stray `export CLAUDE_CODE_*` line in a login shell's ~/.bashrc / ~/.profile /
~/.bash_profile / ~/.bash_login — OUTSIDE the managed airuleset marker blocks —
silently overrides the fleet's Claude Code environment (dev2 exported
`CLAUDE_CODE_DISABLE_MOUSE=1` for weeks, taking native mouse scrolling away, and
nothing reported it). The managed launcher now OWNS the mouse/alternate-screen
toggles (it `unset`s them before exec, cli_claude_scripts.py), so the launch env
is fixed regardless; this module is the "make the gap LOUD" backstop the incident
asked for: ONE scanner read by two consumers —

  - `airuleset.py status` / install (`bashrc_drift_status_row` /
    `bashrc_drift_install_warning`, a live scan naming file:line),
  - the daily conformance sweep Job 34 (`count_bashrc_drift`, a report-only
    `bashrc_drift` fact — watchdog/conformance.py, the #1047 pattern).

The scan NEVER edits ~/.bashrc — the unmanaged region is the owner's; airuleset
reports it (the 22.9. dev2 removal was an explicit owner-requested one-off).

Known limitation (report-only, deliberately not line-parsing shell): an
`export CLAUDE_CODE_*` line that sits inside a heredoc / quoted string is
line-matched; contrived for a real login file and harmless for an advisory row.
"""
import re
from pathlib import Path

# The managed marker blocks (cli_bashrc_appliers.py) all use the sentinel
# convention `# >>> airuleset: <name> >>>` ... `# <<< airuleset: <name> <<<`.
# Match the PREFIX generically (colon OPTIONAL — a future no-colon block, like the
# `# >>> airuleset tmux` shape, is then covered too) so a new managed block never
# needs a change here.
_BLOCK_START_RE = re.compile(r"^\s*#\s*>>>\s*airuleset\b")
_BLOCK_END_RE = re.compile(r"^\s*#\s*<<<\s*airuleset\b")

# A managed `export`/`declare -x` line that names any CLAUDE_CODE_* VARIABLE —
# the exported var can be the first token, a later token in a multi-var export
# (`export FOO=1 CLAUDE_CODE_X=1`), or have no `=` (`export CLAUDE_CODE_X`); all
# of these contaminate the child env. `CLAUDE_CODE_` must sit at a var-name
# position (right after the keyword, or after a whole prior var token), so a
# value string like `export MYVAR=CLAUDE_CODE_HOME` is NOT flagged. A commented
# line never matches — the keyword is not first.
_EXPORT_RE = re.compile(
    r"^\s*(?:export|declare\s+-x)\s+(?:\S+\s+)*CLAUDE_CODE_\w+")


def default_bashrc_paths():
    """The login-shell files a stray export can hide in. Bash reads the FIRST of
    ~/.bash_profile / ~/.bash_login / ~/.profile for a login shell and ~/.bashrc
    for an interactive non-login shell (the launcher's `claude()` wrapper), so all
    four are scanned — checking an unused file is harmless, missing a used one is
    the bug."""
    home = Path.home()
    return [home / ".bashrc", home / ".bash_profile",
            home / ".bash_login", home / ".profile"]


def _managed_line_indices(lines):
    """The set of 0-based indices INSIDE a BALANCED managed marker block. Clean
    left-to-right pairing (the `cli_bashrc_appliers._stream_marker_block_spans`
    approach): each START pairs with the NEXT END; an UNCLOSED start opens no
    block, so it can never SWALLOW the rest of the file (which would hide every
    later stray export — the opposite of this module's job)."""
    managed = set()
    i, n = 0, len(lines)
    while i < n:
        if _BLOCK_START_RE.match(lines[i]):
            j = i + 1
            while j < n and not _BLOCK_END_RE.match(lines[j]):
                j += 1
            if j < n:                       # balanced block: mark i..j inclusive
                managed.update(range(i, j + 1))
                i = j + 1
                continue
            # orphan START (no matching END) — do NOT swallow; skip only this line
        i += 1
    return managed


def scan_bashrc_drift(paths=None):
    """Return `[(path_str, lineno, stripped_line), ...]` for every stray managed
    CLAUDE_CODE_* export found OUTSIDE a balanced airuleset marker block, across
    `paths` (default: the login-shell files above). A missing / unreadable file is
    a legitimate state and is skipped, never an error. Symlinked-together files
    (a common `~/.profile -> ~/.bashrc`) are scanned once. Lines numbered from 1."""
    if paths is None:
        paths = default_bashrc_paths()
    hits = []
    seen_real = set()
    for p in paths:
        path = Path(p)
        try:
            real = path.resolve()
        except OSError:
            real = path
        if real in seen_real:
            continue
        seen_real.add(real)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # absent / unreadable file — a valid state; nothing to report here.
            continue
        lines = text.splitlines()
        managed = _managed_line_indices(lines)
        for idx, raw in enumerate(lines):
            if idx in managed:
                continue
            if _EXPORT_RE.match(raw):
                hits.append((str(path), idx + 1, raw.strip()))
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


def bashrc_drift_install_warning(paths=None):
    """The install-step WARNING string (or None when clean) — the "make the gap
    LOUD at install" surface (the gk Discord `.env` lesson). Composed here so the
    install call site stays a one-liner (keeps `cmd_install` off its size cap)."""
    row = bashrc_drift_status_row(paths)
    if not row:
        return None
    return "  WARNING: %s (stray CLAUDE_CODE_* export — remove it)" % row
