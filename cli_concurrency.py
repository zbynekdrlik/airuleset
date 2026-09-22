"""cli_concurrency — resolve a pane's (mode, role) for #998.

Single source of truth for concurrency resolution, shaped like
``cli_quals._authority_decision``: a THREE-source chain returning
``(value, source)`` so ``airuleset.py status`` / ``--explain`` can NAME where
the answer came from, and the footer / quals / goal-renderer / dispatch-hook /
lane caps all read the SAME resolver — never a parallel narrower one, the
#367/#821 single-derivation rule that keeps them from silently disagreeing.

Resolution order (owner directive 2026-09-12, item 1c):

  1. a DECLARED managed window of the box's OWN fleet entry (matched by CWD
     first — by containment, the LONGEST declared cwd the pane is in wins —
     else by exact window NAME; #998 addendum: cwd wins so a mis-named window
     in the infra cwd still resolves to role infra) -> ``(window.mode or
     "parallel", window.role, "role")``
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
    """The declared window matching ``cwd`` FIRST (expanded, realpath, by
    containment — the pane cwd equals or is a subdirectory of a declared
    window's cwd; the LONGEST such declared cwd wins), else the one matching
    ``window_name`` exactly. ``None`` when neither matches.

    CWD wins over name (#998 addendum, owner 2026-09-12 "prečo mám dva gk"):
    the install's window-namer renamed EVERY window to the box alias, so the
    infra window is live-named ``gk`` too — resolving by name would then
    classify it as the parallel review lane. Matching by cwd first pins it to
    role ``infra`` regardless of the (mis-)name, and the same matcher gives the
    namer each window's DECLARED name from its cwd. Containment (not bare
    equality) keeps a pane cd'd into a subdirectory of the checkout resolving
    to its window; sibling dirs (``odoo-erp`` vs ``odoo-erp-infra``) never
    cross-match thanks to the ``os.sep`` boundary."""
    if cwd:
        try:
            cwd_real = os.path.realpath(cwd)
        except OSError:
            cwd_real = cwd
        best = None
        best_len = -1
        for w in windows:
            dreal = _expand(w.get("cwd"), home).rstrip("/")
            if cwd_real == dreal or cwd_real.startswith(dreal + os.sep):
                if len(dreal) > best_len:
                    best, best_len = w, len(dreal)
        if best is not None:
            return best
    if window_name:
        for w in windows:
            if w.get("name") == window_name:
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


def is_exact_declared_window(cwd, windows=None, user=None, home=None):
    """#1038 — True iff `cwd` is EXACTLY a declared window's own working directory
    (realpath equality), NOT merely CONTAINED in one. `resolve_concurrency` matches
    by containment on purpose (a pane cd'd into a subdirectory inherits the
    window's MODE) — but the post-reboot VIRGIN-arm scan asks a stricter question:
    "is this pane THE declared window itself?" A human sub-pane cd'd into a
    SUBDIRECTORY of a declared checkout (a worktree, an ad-hoc sub-session) must
    NEVER be given an unsolicited `/goal`; only the window's own pane is
    bootstrapped. Fail-safe False on any resolver/expand error."""
    if not cwd:
        return False
    if windows is None:
        windows = cli_fleet.box_windows(user or _current_user())
    try:
        cwd_real = os.path.realpath(cwd)
    except OSError:
        cwd_real = cwd
    for w in (windows or []):
        try:
            dreal = _expand(w.get("cwd"), home).rstrip("/")
        except Exception:  # noqa: BLE001 -- a malformed window cwd is never a match
            continue
        if cwd_real == dreal:
            return True
    return False


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
            import cli_lane_liveness
            root = repo_root or cwd
            live_count = len(cli_lane_liveness.gather_live_lanes(root, run=run))
        except Exception:  # noqa: BLE001 — cannot count => fail-safe allow
            return "allow|sequential|0"
    verdict = "block" if live_count >= 1 else "allow"
    return "%s|sequential|%d" % (verdict, live_count)


def concurrency_status_row(cwd, window_name=None, user=None, home=None,
                           windows=None):
    """The ``airuleset.py status`` row (#998 item 4):
    ``concurrency: <mode> (source: role|project|default)``. When a role is
    resolved it is named too, so the gk windows (review / infra / quality,
    #1074) read distinctly."""
    mode, role, source = resolve_concurrency(cwd, window_name, user, home,
                                             windows)
    role_sfx = " role=%s" % role if role else ""
    return "concurrency: %s (source: %s)%s" % (mode, source, role_sfx)


def goal_variant_label(mode, role):
    """#1038 -- the owner-facing name of a pane's `/goal` VARIANT: ``<role>/<mode>``
    when a role is resolved (``review/parallel``, ``infra/sequential`` -- the
    owner's own ticket examples), else just ``<mode>`` (``sequential`` for d3,
    ``parallel`` for the default). ONE label shared by the watchdog virgin-arm
    journal line AND the ``airuleset.py status`` goal row so the two never drift."""
    return "%s/%s" % (role, mode) if role else "%s" % mode


def goal_status_row(cwd, armed, pending=False, *, pane_found=True,
                    window_name=None, user=None, home=None, windows=None):
    """#1038 item (3) -- the ``airuleset.py status`` ``goal:`` row, next to
    ``concurrency:``. Reads the SAME truth the arm machinery uses: ``armed`` is
    the tri-state ``watchdog.pane_goal_armed`` of the RESOLVED pane
    (True/False/None), ``pending`` is whether a durable goal-arm request is
    still in flight for that session. The variant (which /goal WOULD/DID arm)
    is always resolvable from the cwd via `resolve_concurrency`, so the owner
    sees it in every state:
      * armed True                 -> ``goal: armed <variant>``
      * a pending request          -> ``goal: arming <variant> (request pending)``
      * armed None (pane busy)     -> ``goal: armed state undeterminable (pane busy) ...``
      * armed False (a real read)  -> ``goal: NOT armed — type /autopilot (variant <variant>)``
      * no pane resolved           -> ``goal: unmeasurable outside a pane ...``
    ``pane_found`` (#1038 follow-up) plus the tri-state ``armed`` are the
    honesty gate: a ``NOT armed`` verdict is printed ONLY after a REAL,
    DETERMINATE ``False`` read of a resolved pane. Two states are NEVER reported
    as NOT armed: no pane resolved -> ``unmeasurable outside a pane`` (the first
    #1038 lane's honesty defect: over ssh it read no pane at all yet said NOT
    armed); a pane resolved but ``pane_goal_armed`` returned ``None`` (a busy /
    scrolled / empty capture -- undeterminable, not dark) -> ``armed state
    undeterminable`` (the #1038-review residual). This matches the virgin scan's
    own "None is doubt, never act" stance so the two tri-state consumers agree.
    A declared window that genuinely reads ``False`` is the post-reboot state
    #1038 fixes; the row then tells the owner the one word (`/autopilot`) is the
    whole procedure.

    ``pane_found`` and everything after it are KEYWORD-ONLY (#1038-review 2):
    ``pane_found`` was inserted between ``pending`` and ``window_name``, so a
    future caller passing ``window_name`` POSITIONALLY would silently misbind it
    to ``pane_found``. The ``*`` closes that trap by construction."""
    mode, role, _source = resolve_concurrency(cwd, window_name, user, home,
                                              windows)
    variant = goal_variant_label(mode, role)
    if not pane_found:
        return ("goal: unmeasurable outside a pane (variant %s) — run status "
                "inside the claude pane or type /autopilot there" % variant)
    if armed is True:
        return "goal: armed %s" % variant
    if pending:
        return "goal: arming %s (request pending)" % variant
    if armed is None:
        # A pane WAS resolved but its armed state could not be READ (a busy /
        # scrolled / empty capture -> pane_goal_armed None). NOT a dark pane,
        # so NEVER a NOT-armed verdict (the #1038-review residual): report the
        # undeterminable state honestly and point the owner at an idle re-check.
        # Matches the virgin scan's own "None is doubt, never act" stance so the
        # two tri-state consumers agree.
        return ("goal: armed state undeterminable (pane busy) — variant %s; "
                "re-check when the pane is idle" % variant)
    return "goal: NOT armed — type /autopilot (variant %s)" % variant
