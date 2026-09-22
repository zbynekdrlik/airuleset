"""airuleset ~/.bashrc drift scan (#1015) + settings.json/tmux env legs (#1116).

A stray `export CLAUDE_CODE_*` line in a login shell's ~/.bashrc / ~/.profile /
~/.bash_profile / ~/.bash_login — OUTSIDE the managed airuleset marker blocks —
silently overrides the fleet's Claude Code environment (dev2 exported
`CLAUDE_CODE_DISABLE_MOUSE=1` for weeks, taking native mouse scrolling away, and
nothing reported it). The managed launcher now OWNS the mouse/alternate-screen
toggles (it `unset`s them before exec, cli_claude_scripts.py), so the launch env
is fixed regardless; this module is the "make the gap LOUD" backstop the incident
asked for: ONE scanner read by two consumers —

  - `airuleset.py status` / install (`bashrc_drift_status_row` /
    `bashrc_drift_install_warning`, a live scan naming the source),
  - the daily conformance sweep Job 34 (`count_bashrc_drift`, a report-only
    `bashrc_drift` fact — watchdog/conformance.py, the #1047 pattern).

#1116: the mouse/alternate-screen toggle can ALSO hide in two places the #1015
bashrc scan never looked — the merged `~/.claude/settings.json` `env` (applied
INSIDE the Claude Code process, so it defeats the launcher `unset`) and the tmux
server's global environment (`tmux show-environment -g`, inherited by every pane
the server spawns). Both dev2's first restart still carried the toggle from these
sources. So the scan gained two legs behind `scan_env_drift()`: a `settings.json:
env` leg and a `tmux:global-env` leg. The settings merge (cli_config) now DROPS
the two keys at install (reporting each), so the settings leg is a report-only
belt-and-suspenders; the tmux leg names the source with a `tmux set-environment
-gu <k>` fix hint (airuleset never mutates the tmux server env either). The two
owned keys live in ONE tuple, MANAGED_ENV_DROP_KEYS, shared with the launcher's
`unset` line (cli_claude_scripts) and the merge's drop list (cli_config).

The scan NEVER edits ~/.bashrc / settings.json / the tmux env — the unmanaged
region is the owner's; airuleset reports it (settings.json is the one exception:
the merge drops the two owned keys at install, an explicit #1116 decision).

Known limitation (report-only, deliberately not line-parsing shell): an
`export CLAUDE_CODE_*` line that sits inside a heredoc / quoted string is
line-matched; contrived for a real login file and harmless for an advisory row.
"""
import json
import re
import subprocess
from collections import namedtuple
from pathlib import Path

# The two Claude Code toggles the managed launcher OWNS (#1015/#1116): the fleet
# default IS Claude Code's native mouse + alternate screen. This ONE tuple is the
# single source of truth — the launcher `unset` line (cli_claude_scripts) is built
# from it, the settings merge (cli_config) drops exactly these keys, and the two
# new scan legs below look for exactly these keys. Order matters: the launcher's
# `unset` line joins them in this order (frozen by test_launcher_mouse_env_1015).
MANAGED_ENV_DROP_KEYS = (
    "CLAUDE_CODE_DISABLE_MOUSE",
    "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN",
)

# A single env-drift finding across any leg. `source` names WHERE it was found
# (`bashrc:<file>:<line>`, `settings.json:env`, `tmux:global-env`), `detail` the
# offending content (a bashrc export line, or `<k>=<v>`), `fix` an optional
# remediation hint (the tmux leg carries `tmux set-environment -gu <k>`).
EnvDriftHit = namedtuple("EnvDriftHit", ["source", "detail", "fix"])

# Sentinel for "use the real box source"; None means "skip this leg".
_DEFAULT = object()

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
    (a common `~/.profile -> ~/.bashrc`) are scanned once. Lines numbered from 1.

    This is the bashrc LEG only, and its 3-tuple shape is frozen by
    test_launcher_mouse_env_1015 — the #1116 settings.json/tmux legs live in
    `scan_env_drift`, which wraps these into EnvDriftHit(source=...) records."""
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


def _default_tmux_env_reader():
    """Run `tmux show-environment -g` and return its stdout, or "" on ANY failure
    (no server running, tmux not installed, a timeout) — best-effort, never
    raises. The 3 s timeout keeps `status` / conformance responsive on a wedged
    tmux."""
    try:
        proc = subprocess.run(
            ["tmux", "show-environment", "-g"],
            capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _scan_settings_env_drift(settings_path):
    """Report each MANAGED_ENV_DROP_KEYS key present in a settings.json `env`
    object. Best-effort: a missing / unreadable / malformed file, or an `env`
    that is not an object, yields no hit and never raises (the cli_config merge
    removes these keys at the next install; this leg only NAMES the source so the
    owner / next supervisor can see where a toggle lived before then)."""
    try:
        text = Path(settings_path).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    env = data.get("env")
    if not isinstance(env, dict):
        return []
    hits = []
    for key in MANAGED_ENV_DROP_KEYS:
        if key in env:
            hits.append(EnvDriftHit(
                source="settings.json:env",
                detail="%s=%s" % (key, env[key]),
                fix=""))
    return hits


def _scan_tmux_env_drift(tmux_env_reader):
    """Report each MANAGED_ENV_DROP_KEYS key SET in the tmux server's global
    environment. `tmux_env_reader` returns the raw `tmux show-environment -g`
    text. Best-effort: a reader that returns "" (no server) or raises yields no
    hit and never propagates. A `-VAR` line (a var REMOVED from the global env)
    is the CLEAN state, never drift. Each hit carries the
    `tmux set-environment -gu <k>` fix hint — airuleset reports the tmux env, it
    never mutates it."""
    try:
        out = tmux_env_reader() or ""
    except Exception:  # noqa: BLE001 — best-effort backstop, never raises
        return []
    env = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("-") or "=" not in line:
            continue                        # `-VAR` = removed (clean), no `=` = skip
        name, value = line.split("=", 1)
        env[name.strip()] = value
    hits = []
    for key in MANAGED_ENV_DROP_KEYS:
        if key in env:
            hits.append(EnvDriftHit(
                source="tmux:global-env",
                detail="%s=%s" % (key, env[key]),
                fix="tmux set-environment -gu %s" % key))
    return hits


def scan_env_drift(paths=None, *, settings_path=_DEFAULT, tmux_env_reader=_DEFAULT):
    """The unified box-environment drift scan (#1116): the bashrc leg
    (`scan_bashrc_drift`) PLUS a `settings.json:env` leg and a `tmux:global-env`
    leg, every hit an EnvDriftHit(source, detail, fix). A leg is SKIPPED when its
    source argument is None (targeted bashrc-only mode); `_DEFAULT` uses the real
    box source (`~/.claude/settings.json`, `tmux show-environment -g`). A callable
    `tmux_env_reader` (or a `settings_path` string) is injected by the tests."""
    hits = [EnvDriftHit(source="bashrc:%s:%d" % (path, lineno),
                        detail=line, fix="")
            for (path, lineno, line) in scan_bashrc_drift(paths)]
    if settings_path is not None:
        sp = (Path.home() / ".claude" / "settings.json") \
            if settings_path is _DEFAULT else settings_path
        hits += _scan_settings_env_drift(sp)
    if tmux_env_reader is not None:
        reader = _default_tmux_env_reader \
            if tmux_env_reader is _DEFAULT else tmux_env_reader
        hits += _scan_tmux_env_drift(reader)
    return hits


def _box_scan_or_targeted(paths, settings_path, tmux_env_reader):
    """The scope rule the three consumers share: a DEFAULT box scan (paths is
    None — what `status` / install / conformance call) runs all three legs against
    the real box sources; a TARGETED call (explicit `paths`, as the #1015 tests
    use) is bashrc-only UNLESS the caller explicitly injects a settings_path /
    tmux_env_reader (the #1116 tests do)."""
    if paths is None:
        return scan_env_drift(None, settings_path=settings_path,
                              tmux_env_reader=tmux_env_reader)
    sp = None if settings_path is _DEFAULT else settings_path
    tr = None if tmux_env_reader is _DEFAULT else tmux_env_reader
    return scan_env_drift(paths, settings_path=sp, tmux_env_reader=tr)


def _render_hit(hit):
    """`<source> <detail>`, with a ` (fix: <hint>)` suffix when the hit carries
    one (the tmux leg's `set-environment -gu` remediation)."""
    text = "%s %s" % (hit.source, hit.detail)
    if hit.fix:
        text += " (fix: %s)" % hit.fix
    return text


def count_bashrc_drift(paths=None, *, settings_path=_DEFAULT,
                       tmux_env_reader=_DEFAULT):
    """The number of env-drift lines across all legs (report-only fact for the
    conformance sweep — the fact stays one integer, `bashrc_drift`). All-legs on
    the default box scan; bashrc-only for a targeted explicit-paths call unless a
    source is injected."""
    return len(_box_scan_or_targeted(paths, settings_path, tmux_env_reader))


def bashrc_drift_status_row(paths=None, *, settings_path=_DEFAULT,
                            tmux_env_reader=_DEFAULT):
    """A single `airuleset.py status` row, or None when there is no drift (the
    caller prints nothing on a clean box). On drift:
    `bashrc-drift: <source> <detail>[ (fix: ...)]; ...` — the source names each
    of bashrc / settings.json:env / tmux:global-env."""
    hits = _box_scan_or_targeted(paths, settings_path, tmux_env_reader)
    if not hits:
        return None
    return "bashrc-drift: " + "; ".join(_render_hit(h) for h in hits)


def bashrc_drift_install_warning(paths=None, *, settings_path=_DEFAULT,
                                 tmux_env_reader=_DEFAULT):
    """The install-step WARNING string (or None when clean) — the "make the gap
    LOUD at install" surface (the gk Discord `.env` lesson). Composed here so the
    install call site stays a one-liner (keeps `cmd_install` off its size cap)."""
    row = bashrc_drift_status_row(paths, settings_path=settings_path,
                                  tmux_env_reader=tmux_env_reader)
    if not row:
        return None
    return "  WARNING: %s (stray CLAUDE_CODE_* export/env — remove it)" % row
