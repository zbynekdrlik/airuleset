"""cli_concurrency — resolve a pane's declared window ROLE (#998).

Single source of truth for the role (review / infra / quality, the gk windows'
purpose), so the footer / quals / goal-renderer / status all read the SAME
resolver — the #367/#821 single-derivation rule. A role comes ONLY from a
DECLARED managed window of the box's OWN fleet entry (``cli_fleet.box_windows``),
matched by CWD first (containment, the LONGEST declared cwd the pane is in wins),
else by exact window NAME (#998 addendum: cwd wins so a mis-named window in the
infra cwd still resolves to role infra). No declaration -> no role.

#1137 (owner 25.10.2026) removed the sequential/parallel MODE half of this
module: Claude Code decides subagents, worktrees and ticket order itself.

Scoping to the box's OWN entry is what stops a montalu box at
``~/devel/odoo/odoo-erp`` being mis-classified as the gk review window: only
the box whose unix account owns the declaration resolves ``role``.

Dependency-light: ``cli_fleet`` (pure data) + stdlib only; reads NOTHING from
``watchdog`` (no import-time cycle, no heavy ``watchdog/__init__`` pull).
"""
import os

import cli_fleet


def _current_user():
    """Un-spoofable box account (pw_name), never ``getpass.getuser()`` — the
    #839 identity source. ``""`` when unresolvable (a passwd-less uid)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:  # noqa: BLE001 — any failure => no declared windows
        return ""


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
    classify it as the review lane. Matching by cwd first pins it to
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


def resolve_role(cwd, window_name=None, user=None, home=None, windows=None):
    """The pane's declared window role (``review`` / ``infra`` / ``quality``),
    or None. ``windows`` (the box's declared windows) is injected for tests;
    ``None`` -> derived via ``cli_fleet.box_windows(user or _current_user())``.
    ``home`` is the box user's home (test seam)."""
    if windows is None:
        windows = cli_fleet.box_windows(user or _current_user())
    w = _match_window(cwd, window_name, windows or [], home)
    return w.get("role") if w is not None else None


def is_exact_declared_window(cwd, windows=None, user=None, home=None):
    """#1038 — True iff `cwd` is EXACTLY a declared window's own working directory
    (realpath equality), NOT merely CONTAINED in one. `resolve_role` matches
    by containment on purpose (a pane cd'd into a subdirectory inherits the
    window's ROLE) — but Job 52's watch triggers (`watch_triggers`) ask a stricter
    question: "is this pane THE declared window itself?" A human sub-pane cd'd
    into a SUBDIRECTORY of a declared checkout (a worktree, an ad-hoc sub-session)
    never counts; only the window's own pane does (its first consumer, the #1038
    virgin arm, is retired, #1260). Fail-safe False on any resolver/expand error."""
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


def role_status_row(cwd, window_name=None, user=None, home=None,
                    windows=None):
    """The ``airuleset.py status`` role row (#998 item 4): ``role: <role>`` for a
    declared gk window (review / infra / quality, #1074), else None (no row)."""
    role = resolve_role(cwd, window_name, user, home, windows)
    return "role: %s" % role if role else None


def goal_variant_label(role):
    """#1038 -- the owner-facing name of a pane's `/goal` VARIANT: its role
    (``review``, ``infra``, ``quality``), else ``default``. ONE label for the
    ``airuleset.py status`` goal row."""
    return role or "default"


def goal_status_row(cwd, armed, pending=False, *, pane_found=True,
                    window_name=None, user=None, home=None, windows=None):
    """#1038 item (3) -- the ``airuleset.py status`` ``goal:`` row. Reads the SAME truth the arm machinery uses: ``armed`` is
    the tri-state ``watchdog.pane_goal_armed`` of the RESOLVED pane
    (True/False/None), ``pending`` is whether a durable goal-arm request is
    still in flight for that session. The variant (which /goal WOULD/DID arm)
    is always resolvable from the cwd via `resolve_role`, so the owner
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
    undeterminable`` (the #1038-review residual). Same "None is doubt, never act"
    stance as deliver_goal's tri-state armed check, so the consumers agree.
    A declared window that genuinely reads ``False`` is simply unarmed (#1260:
    no automatic arm, even after a reboot); the row then tells the owner the one word (`/autopilot`) is the
    whole procedure.

    ``pane_found`` and everything after it are KEYWORD-ONLY (#1038-review 2):
    ``pane_found`` was inserted between ``pending`` and ``window_name``, so a
    future caller passing ``window_name`` POSITIONALLY would silently misbind it
    to ``pane_found``. The ``*`` closes that trap by construction."""
    variant = goal_variant_label(
        resolve_role(cwd, window_name, user, home, windows))
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
        # Same "None is doubt, never act" stance as deliver_goal's tri-state
        # armed check, so the consumers agree.
        return ("goal: armed state undeterminable (pane busy) — variant %s; "
                "re-check when the pane is idle" % variant)
    return "goal: NOT armed — type /autopilot (variant %s)" % variant
