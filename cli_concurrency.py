"""cli_concurrency — resolve a pane's (mode, role) for #998.

Single source of truth for concurrency resolution, shaped like
``cli_quals._authority_decision``: a THREE-source chain returning
``(value, source)`` so ``airuleset.py status`` / ``--explain`` can NAME where
the answer came from, and the footer / quals / goal-renderer / dispatch-hook /
lane caps all read the SAME resolver — never a parallel narrower one, the
#367/#821 single-derivation rule that keeps them from silently disagreeing.

Resolution order (owner directive 2026-09-12, item 1c):

  1. a DECLARED managed window of the box's OWN fleet entry (matched by window
     NAME when given, else by CWD) -> ``(window.mode or "parallel",
     window.role, "role")``
  2. the project's ``.claude/lane-resources.json`` ``mode`` ->
     ``(mode, None, "project")``
  3. default -> ``("parallel", None, "default")``

Scoping to the box's OWN entry (via ``cli_fleet.box_windows(user)``) is what
stops a montalu box at ``~/devel/odoo/odoo-erp`` being mis-classified as the
gk review window: only the box whose unix account owns the declaration
resolves ``role``.

Dependency-light: ``cli_fleet`` (pure data) + stdlib only. ``lane_resources``
reads THIS (deferred) for the sequential -> caps=1 rule; this reads NOTHING
from ``watchdog`` (no import-time cycle, no heavy ``watchdog/__init__`` pull).
"""
import json
import os

import cli_fleet

DEFAULT_MODE = "parallel"
LANE_RESOURCE_FILE = os.path.join(".claude", "lane-resources.json")


def _current_user():
    """Un-spoofable box account (pw_name), never ``getpass.getuser()`` — the
    #839 identity source. ``""`` when unresolvable (a passwd-less uid)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:  # noqa: BLE001 — any failure => no declared windows
        return ""


def read_project_mode(cwd):
    """The ``mode`` declared in ``<cwd>/.claude/lane-resources.json``, or
    ``None``. Only a valid ``cli_fleet.WINDOW_MODES`` value counts; an absent
    file, malformed JSON, a non-dict, or an unknown value -> ``None`` (fall
    through to the default). Fail-safe toward the default, never a crash."""
    if not cwd:
        return None
    p = os.path.join(cwd, LANE_RESOURCE_FILE)
    try:
        with open(p) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    return mode if mode in cli_fleet.WINDOW_MODES else None


def _expand(cwd_spec, home):
    """Expand a declared window cwd (``~/devel/...``) against ``home`` (the
    box user's home; ``None`` -> the running user's own)."""
    s = cwd_spec or ""
    if s.startswith("~"):
        base = home if home is not None else os.path.expanduser("~")
        s = base + s[1:]
    try:
        return os.path.realpath(s)
    except OSError:
        return s


def _match_window(cwd, window_name, windows, home):
    """The declared window matching ``window_name`` (exact) or ``cwd``
    (expanded, realpath-compared), else ``None``. Name wins over cwd."""
    if window_name:
        for w in windows:
            if w.get("name") == window_name:
                return w
    if cwd:
        try:
            cwd_real = os.path.realpath(cwd)
        except OSError:
            cwd_real = cwd
        for w in windows:
            if _expand(w.get("cwd"), home) == cwd_real:
                return w
    return None


def resolve_concurrency(cwd, window_name=None, user=None, home=None,
                        windows=None):
    """Resolve ``(mode, role, source)`` for a pane. See the module docstring
    for the source chain. ``windows`` (the box's declared windows) is injected
    for tests; ``None`` -> derived via ``cli_fleet.box_windows(user or
    _current_user())``. ``home`` is the box user's home (test seam)."""
    if windows is None:
        windows = cli_fleet.box_windows(user or _current_user())
    w = _match_window(cwd, window_name, windows or [], home)
    if w is not None:
        return (w.get("mode") or DEFAULT_MODE, w.get("role"), "role")
    pm = read_project_mode(cwd)
    if pm is not None:
        return (pm, None, "project")
    return (DEFAULT_MODE, None, "default")


def resolve_mode(cwd, window_name=None, user=None, home=None, windows=None):
    """Convenience: just the effective mode (the caps=1 consumer's question)."""
    return resolve_concurrency(cwd, window_name, user, home, windows)[0]


def resolve_role(cwd, window_name=None, user=None, home=None, windows=None):
    """Convenience: just the effective role (the footer/quals slice question)."""
    return resolve_concurrency(cwd, window_name, user, home, windows)[1]


def dispatch_gate_line(cwd, repo_root=None, run=None, live_count=None):
    """One-line verdict for ``block-dispatch-over-wdrain.sh``'s #998
    sequential gate: ``"<verdict>|<mode>|<live>"`` (verdict ∈ allow/block).

    ``block`` ONLY when the pane's mode is ``sequential`` AND at least one live
    worktree lane already exists for the repo containing ``cwd`` (the total cap
    is 1, so the 2nd concurrent ``autopilot-worker`` is refused). ``parallel``
    (and any resolver error) always allows — fail-safe toward today's
    behaviour, never a false block. ``live_count``/``run`` are test seams."""
    try:
        mode = resolve_mode(cwd)
    except Exception:  # noqa: BLE001
        return "allow|unknown|0"
    if mode != "sequential":
        return "allow|%s|0" % mode
    if live_count is None:
        try:
            import cli_lane_overlap
            root = repo_root or cwd
            live_count = len(cli_lane_overlap.gather_live_lanes(root, run=run))
        except Exception:  # noqa: BLE001 — cannot count => fail-safe allow
            return "allow|sequential|0"
    verdict = "block" if live_count >= 1 else "allow"
    return "%s|sequential|%d" % (verdict, live_count)


def concurrency_status_row(cwd, window_name=None, user=None, home=None,
                           windows=None):
    """The ``airuleset.py status`` row (#998 item 4):
    ``concurrency: <mode> (source: role|project|default)``. When a role is
    resolved it is named too, so the two gk windows read distinctly."""
    mode, role, source = resolve_concurrency(cwd, window_name, user, home,
                                             windows)
    role_sfx = " role=%s" % role if role else ""
    return "concurrency: %s (source: %s)%s" % (mode, source, role_sfx)
