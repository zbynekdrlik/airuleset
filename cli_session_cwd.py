"""#1202: ONE source for a single-session account's tmux start directory.

Regression of #1088/#961. After the 2026-09-30 subdev reboot, montalu1's tmux
session came up in the bare parent ``~/devel/odoo``. The creator was the
controller-lane FORCED COMMAND in montalu1's ``authorized_keys``: it was baked by
hand on 2026-09-09 (#961 follow-on) from the chain of that day,
``("devel/odoo/odoo-erp", "devel/odoo")``, with a plain ``[ -d ]`` predicate.
``claude -c`` there then resumed a stale copy of the owner's session, which
lives under a DIFFERENT project key.

Three creators render ``tmux new-session … -c <dir>``: the ssh auto-attach
block (``cli_bashrc_appliers``), the webterm command (``cli_webterm``, client
side AND baked into ``authorized_keys``), and the #263 Python bootstrap
(``airuleset.ensure_stream_tmux_session``). Each used to resolve its dir from a
chain. The fix makes the DECLARED managed window (``cli_fleet.box_windows``,
#998/#1031) the single source whenever the account declares one. The chain is
only the fallback for undeclared accounts, and a chain entry counts only when it
is a git checkout (``<dir>/.git``), never a bare parent. This module holds that
single source, plus the two #1202 read sides built on it:

* ``pane_cwd_status`` — the ``airuleset.py status`` check that a stream's first
  tmux pane sits in its declared cwd (status only, never the footer — owner
  rule);
* ``launch_guard_cwds`` — the declared cwds baked into the claude launcher so
  ``claude`` / ``claude -c`` refuses to run from any other directory.

Leaf module: imports only ``cli_fleet`` (a pure table), so both
``cli_bashrc_appliers`` and ``cli_webterm`` may import it at top level without a
cycle.
"""
import os
import re
import shlex
from pathlib import Path

import cli_fleet

# A declared window cwd is baked into shell (the attach block, a forced
# command, the launcher). ``cli_fleet.validate_windows`` already forbids an
# absolute cwd and ``..``; this is the extra token-shape guard for the bake —
# a real path always passes, anything carrying a shell metachar is dropped
# (fail-closed toward "no declared cwd", i.e. the chain).
_SAFE_REL_RE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")


def _home_rel(cwd):
    """``~/devel/x`` or ``devel/x`` -> ``devel/x``; None for an empty/unsafe
    value (never an absolute path, never ``..``)."""
    if not isinstance(cwd, str):
        return None
    cwd = cwd.strip()
    if cwd.startswith("~/"):
        cwd = cwd[2:]
    cwd = cwd.strip("/")
    if not cwd or ".." in cwd.split("/") or not _SAFE_REL_RE.fullmatch(cwd):
        return None
    return cwd


def _local_windows(user, hostname=None):
    """The declared windows of the box THIS process runs on: the host-scoped
    ``cli_fleet._box_self_entry`` (``newlevel`` is shared by dev1/dev2/spinbike,
    so a user-only lookup could hand one box another's windows). Used by the
    launcher guard and the status check, which run ON the box."""
    entry = cli_fleet._box_self_entry(user, hostname) if user else None
    return cli_fleet.managed_windows(entry) if entry else []


def declared_window_rels(user, local=False, hostname=None):
    """Every declared window cwd of ``user`` as a HOME-relative path, in
    declaration order (``()`` for an account that declares none — every box but
    gk and the sequential subdev streams). gk declares three windows
    (gk/gk-infra/gk-quality), so the launcher guard accepts ANY of them.
    ``local=True`` scopes the lookup to this box (``_local_windows``)."""
    rels = []
    wins = (_local_windows(user, hostname) if local
            else cli_fleet.box_windows(user))
    for w in wins:
        rel = _home_rel(w.get("cwd"))
        if rel and rel not in rels:
            rels.append(rel)
    return tuple(rels)


def primary_declared_rel(user):
    """The PRIMARY declared window's cwd (``windows[0]``, the window a new
    session opens with), HOME-relative, or None when ``user`` declares none."""
    if not user:
        return None
    wins = cli_fleet.box_windows(user)
    if not wins:
        return None
    return _home_rel(wins[0].get("cwd"))


def session_chain_for(user, default_chain):
    """``(chain, fallback_rel)`` for ``user``'s session start directory — the
    ONE decision every creator makes.

    Declared window -> ``((rel,), rel)``: the declared cwd, accepted as a
    checkout and, if it is not one (yet), still used because the declaration is
    authoritative. No declared window -> ``(default_chain, None)``: the first
    chain entry that is a git checkout, else ``$HOME`` — never a bare parent."""
    rel = primary_declared_rel(user)
    if rel:
        return (rel,), rel
    return tuple(default_chain), None


# --- #263/#264/#563/#1088: subdev stream account dev-env convention ---------
# The convention working directory for a subdev/gatekeeper account's tmux
# session. NOT every account checks out at the same path (#563), and worse, a
# checkout may be NAMED DIFFERENTLY: montalu1 (renamed from montalu, #537)
# checks out ~/devel/odoo/odoo-slovnormal, other accounts check out
# ~/devel/odoo/odoo-erp, and ~/devel/odoo itself is a PLAIN folder (screenshots
# + the checkout). #563's chain accepted the first EXISTING dir with NO git
# check, so on montalu1 (no odoo-erp subdir) it picked the non-repo parent
# ~/devel/odoo -- a claude launched there wrote under the wrong project key (no
# history/memory), the footer rendered nothing, and lane-fill saw no dispatchable
# tickets (the owner's complaint, #1088).
#
# #1088: the chain is now REPO-AWARE -- a candidate is accepted ONLY if it is a
# git WORK TREE (`<dir>/.git` present, work tree or gitfile). The checkout names
# come first, then the bare `devel/odoo` (accepted as a repo only if IT carries
# .git). STREAM_DEV_CWD_REL stays the primary (chain[0]). ONE chain + ONE
# predicate, shared by #263's tmux bootstrap (airuleset._stream_session_cwd via
# resolve_stream_cwd), #264's ssh auto-attach block
# (render_stream_cwd_chain_shell, below) and cli_webterm's #961 command
# (render_webterm_cwd_shell, the one-line twin).
#
# #1202 (regression of #1088/#961): a DECLARED managed window
# (cli_fleet.box_windows, #998/#1031) is now the SINGLE source of the start dir
# for every creator (session_chain_for); this chain applies only
# to an account that declares none. And when no chain candidate is a work tree
# the last resort is $HOME -- NEVER the bare parent `devel/odoo` (#1088 fell back
# to it; resuming there picked a stale transcript copy under the parent's
# project key). ONE loud line is still printed so a missing checkout is visible.
STREAM_DEV_CWD_REL = "devel/odoo/odoo-erp"
STREAM_DEV_CWD_CHAIN = (STREAM_DEV_CWD_REL, "devel/odoo/odoo-slovnormal",
                        "devel/odoo")
# The one loud line printed (attach block: to stderr; bootstrap: via Python) when
# the last resort is used. `%s` = the chosen dir. Shared literal so the bash and
# Python renderings can't drift; a test asserts both carry it.
STREAM_DEV_CWD_NO_REPO_MSG = (
    "stream cwd: no git checkout found -- session starts in %s; "
    "tickets footer will show no-repo")


def resolve_stream_cwd(home, cwd_chain=STREAM_DEV_CWD_CHAIN, fallback_rel=None,
                       user=None):
    """#1088/#1202: resolve the session start directory under `home`, mirroring
    render_stream_cwd_chain_shell's predicate in Python.

    `user` given and declaring a managed window -> that window's cwd is the ONE
    source (session_chain_for; `cwd_chain`/`fallback_rel` are
    then ignored). Returns `(chosen: Path, no_repo: bool)`: the first chain
    candidate that is a git WORK TREE (`<dir>/.git` exists -- work tree OR
    gitfile) wins with no_repo=False; when none is, the last resort is
    `home/<fallback_rel>` if given and a dir (only a DECLARED cwd is passed
    here), else `home` -- never a bare parent -- with no_repo=True (the caller
    prints ONE loud line and the footer shows a `no-repo` marker)."""
    home = Path(home)
    if user:
        declared_chain, declared_fb = session_chain_for(user, cwd_chain)
        if declared_fb:
            cwd_chain, fallback_rel = declared_chain, declared_fb
    for rel in cwd_chain:
        if (home / rel / ".git").exists():
            return home / rel, False
    if fallback_rel and (home / fallback_rel).is_dir():
        return home / fallback_rel, True
    return home, True


def render_stream_cwd_chain_shell(cwd_chain, var="__airuleset_cwd",
                                  loopvar="__airuleset_rel",
                                  fallback_rel=None,
                                  indent="  "):
    """#1088: render the bash that resolves the stream start dir into `$<var>`,
    the SAME predicate resolve_stream_cwd applies in Python.

    The first `cwd_chain` candidate that is a git WORK TREE
    (`[ -e "$HOME/$<loopvar>/.git" ]`, work tree or gitfile) wins; when none is,
    the LOUD last resort is `$HOME/<fallback_rel>` when one is given (only a
    DECLARED window cwd, #1202) and that dir exists, else `$HOME` -- never a
    bare parent -- echoing ONE line to stderr (STREAM_DEV_CWD_NO_REPO_MSG) so a
    missing checkout is visible at login. Used by the cli_bashrc_appliers ssh
    attach block, incl. the #985 controller override's `("devel/airuleset",)`
    chain and a declared window's single-entry chain (#1202)."""
    i = indent
    # The loud line is echoed inside DOUBLE quotes so `$<var>` expands; the
    # message has no `"`/backtick/backslash, and `~` stays literal in "" (we want
    # the literal `~/devel/odoo`). Shares STREAM_DEV_CWD_NO_REPO_MSG with Python.
    msg = STREAM_DEV_CWD_NO_REPO_MSG % ("$%s" % var)
    if fallback_rel:
        last = (f'{i}  if [ -d "$HOME/{fallback_rel}" ]; then '
                f'{var}="$HOME/{fallback_rel}"; else {var}="$HOME"; fi\n')
    else:
        last = f'{i}  {var}="$HOME"\n'
    return (
        f'{i}{var}=""\n'
        f'{i}for {loopvar} in {" ".join(cwd_chain)}; do\n'
        f'{i}  if [ -e "$HOME/${loopvar}/.git" ]; then\n'
        f'{i}    {var}="$HOME/${loopvar}"; break\n'
        f'{i}  fi\n'
        f'{i}done\n'
        f'{i}if [ -z "${var}" ]; then\n'
        + last +
        f'{i}  echo "{msg}" >&2\n'
        f'{i}fi\n'
    )


def render_webterm_cwd_shell(chain, fallback_rel=None, var="C",
                             loopvar="__r"):
    """The ONE-LINE POSIX-sh resolver the webterm command uses (it is baked into
    ``authorized_keys`` as ``command="…"``, so it may not contain a newline).

    Same predicate as ``render_stream_cwd_chain_shell`` (above):
    the first ``chain`` entry with ``$HOME/<entry>/.git`` (work tree or gitfile)
    wins; else ``$HOME/<fallback_rel>`` when given and a dir; else ``$HOME``."""
    rels = " ".join(shlex.quote(r) for r in chain)
    fb = ""
    if fallback_rel:
        fb = ('if [ -d "$HOME/%s" ]; then %s="$HOME/%s"; else %s="$HOME"; fi; '
              % (fallback_rel, var, fallback_rel, var))
    else:
        fb = '%s="$HOME"; ' % var
    return (
        '%s=""; ' % var
        + "for %s in %s; do " % (loopvar, rels)
        + 'if [ -e "$HOME/$%s/.git" ]; then %s="$HOME/$%s"; break; fi; '
        % (loopvar, var, loopvar)
        + "done; "
        + 'if [ -z "$%s" ]; then %sfi; ' % (var, fb)
    )


def launch_guard_cwds(user, hostname=None):
    """The declared cwds baked into the claude launcher's resume guard, as ONE
    shell word list (each shell-quoted), or ``""`` when ``user`` declares none
    (the guard is then a no-op — dev1/dev2/controller stay byte-identical in
    behaviour)."""
    return " ".join(shlex.quote(r) for r in
                    declared_window_rels(user, local=True, hostname=hostname))


# The claude launcher's resume guard (#1202), spliced into
# cli_claude_scripts.CLAUDE_LAUNCH_SCRIPT_CONTENT at `{{CWD_GUARD}}`.
LAUNCH_CWD_GUARD_TEMPLATE = r"""
# #1202: resume guard. On an account that DECLARES managed windows (rendered at
# install from cli_fleet.box_windows), every continue-capable mode refuses to
# start outside a declared window's git checkout: `claude -c` from any other dir
# resumes whatever transcript lives under THAT dir's project key — after the
# 2026-09-30 subdev reboot montalu1's session came up in ~/devel/odoo and `claude`
# silently continued a stale copy of the owner's session. An account with no
# declared window gets no guard at all. `claude-new`
# (always fresh) and `claude-plain` (vanilla escape hatch) are never guarded,
# nor are non-session subcommands (--version/update/mcp/doctor/...);
# AIRULESET_CWD_GUARD=off bypasses it for one call. A SUBDIRECTORY of the
# checkout is refused on purpose: its project key differs from the checkout's.
_declared_cwds=({{DECLARED_CWDS}})
_cwd_guard() {
  [ "${#_declared_cwds[@]}" -gt 0 ] || return 0
  [ "${AIRULESET_CWD_GUARD:-on}" = off ] && return 0
  local here rel d
  here="$(pwd -P)"
  for rel in "${_declared_cwds[@]}"; do
    d="$HOME/$rel"
    if [ -d "$d" ] && [ "$(cd "$d" && pwd -P)" = "$here" ]; then
      [ -e "$d/.git" ] && return 0
      echo "claude: refusing to start — ~/$rel is this account's declared session dir but not a git checkout (clone it first)." >&2
      exit 1
    fi
  done
  echo "claude: refusing to start in $PWD — this account's declared session dir is ~/${_declared_cwds[0]}; a claude -c here would resume a DIFFERENT project's transcript (#1202)." >&2
  echo "  run:  cd ~/${_declared_cwds[0]} && claude -c" >&2
  echo "  (a fresh session anywhere: claude-new; one-off bypass: AIRULESET_CWD_GUARD=off claude)" >&2
  exit 1
}
case "$mode" in plain|new) ;; *)
  case "${1:-}" in
    -v|--version|-h|--help|update|mcp|doctor|config|install|plugin|setup-token|migrate-installer) ;;
    *) _cwd_guard ;;
  esac ;;
esac
"""


def render_launch_cwd_guard(declared_cwds=""):
    """The launcher guard with ``declared_cwds`` (``launch_guard_cwds``) baked
    in, or ``""`` when there are none — the launcher of an account with no
    declared window (dev1/dev2/controller) stays byte-identical to pre-#1202."""
    if not declared_cwds.strip():
        return ""
    return LAUNCH_CWD_GUARD_TEMPLATE.replace("{{DECLARED_CWDS}}", declared_cwds)


def cwd_within(actual, expected):
    """True when ``actual`` is ``expected`` or a subdirectory of it, both
    realpath-resolved (tmux reports ``/proc/<pid>/cwd``; ``$HOME`` may be a
    symlink) — the #308 review-MAJOR containment rule, shared by the #308
    install warning and the #1202 status check."""
    try:
        exp_real = os.path.realpath(str(expected))
        act_real = os.path.realpath(str(actual))
    except (OSError, ValueError):
        exp_real, act_real = str(expected), str(actual)
    exp_real = exp_real.rstrip("/") or "/"
    return act_real == exp_real or act_real.startswith(exp_real + os.sep)


def pane_cwd_status(user, home, pane_cwd, session=None, hostname=None):
    """The ``airuleset.py status`` session-cwd check (#1202).

    Returns None when ``user`` declares no window (nothing to check), else
    ``(ok, line)``: ``ok`` is True when the first pane of session
    ``session`` (default ``user``) sits in the declared primary cwd or a
    subdirectory of it (a pane ``cd``'d into the checkout is healthy — the #308
    review-MAJOR precedent), False on a mismatch (e.g. the bare parent
    ``~/devel/odoo``), and None when the pane cwd is unknown (no session /
    tmux unreachable — inconclusive, never a false alarm). Both sides are
    realpath-resolved (tmux reports ``/proc/<pid>/cwd``; ``$HOME`` may be a
    symlink)."""
    rels = declared_window_rels(user, local=True, hostname=hostname)
    if not rels:
        return None
    rel = rels[0]
    sess = session or user
    expected = os.path.join(str(home), rel)
    if not pane_cwd:
        return (None, "session cwd: no pane of session '%s' to check "
                      "(inconclusive) — declared ~/%s" % (sess, rel))
    if cwd_within(pane_cwd, expected):
        return (True, "session cwd: OK — session '%s' first pane in ~/%s"
                      % (sess, rel))
    return (False, "session cwd: MISMATCH — session '%s' first pane is in %s, "
                   "declared window cwd is ~/%s (#1202: a session created in the "
                   "wrong dir resumes the wrong transcript; recreate it from ~/%s)"
                   % (sess, pane_cwd, rel, rel))


def stale_forced_commands(user, ak_text):
    """The controller-lane keys in ``ak_text`` (an ``authorized_keys`` body)
    whose baked forced-command options differ from what the code renders NOW
    for ``user`` (``cli_webterm_only._controller_lane_key_line``) — as a list of
    ``"<human> (<key comment>)"``. The #1202 creator was exactly such a line:
    baked by hand on 2026-09-09, never re-rendered by ``push``. Only the
    ``WEBTERM_CONTROLLER_LANE_PUBKEYS`` blobs are compared; every other key
    (owner, fleet push, foreign) is ignored."""
    from cli_webterm_only import (WEBTERM_CONTROLLER_LANE_PUBKEYS,
                                  _controller_lane_key_line,
                                  parse_authorized_key)
    by_blob = {parse_authorized_key(pub).blob: (human, pub)
               for human, pub in WEBTERM_CONTROLLER_LANE_PUBKEYS.items()}
    stale = []
    for line in ak_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ak = parse_authorized_key(line)
        if ak.blob not in by_blob:
            continue
        human, pub = by_blob[ak.blob]
        want = parse_authorized_key(_controller_lane_key_line(user, pub)).options
        if ak.options != want:
            stale.append("%s (%s)" % (human, ak.comment or "no comment"))
    return stale


def print_status(user, home, pane_cwd_reader, hostname=None):
    """Print the #1202 lines for ``airuleset.py status`` (never the footer —
    owner rule), and never raise out of ``cmd_status``:

    * ``session cwd:`` — the first pane vs the declared window cwd;
      ``pane_cwd_reader(session)`` (``airuleset._tmux_session_pane_cwd``) is
      called ONLY when this box's account declares a window;
    * ``forced command: STALE`` — on a stream account, a baked controller-lane
      line in ``~/.ssh/authorized_keys`` that the current code would render
      differently (push never re-bakes it)."""
    try:
        if declared_window_rels(user, local=True, hostname=hostname):
            print("\n" + pane_cwd_status(user, home, pane_cwd_reader(user),
                                         hostname=hostname)[1])
    except Exception as e:  # status must never die on one probe; say so
        print("\nsession cwd: check failed — %s: %s" % (type(e).__name__, e))
    if user not in cli_fleet.AUTHORITY_BY_USER:
        return
    ak_path = Path(home) / ".ssh" / "authorized_keys"
    try:
        ak_text = ak_path.read_text() if ak_path.is_file() else ""
        stale = stale_forced_commands(user, ak_text)
    except Exception as e:
        print("forced command: check failed — %s: %s" % (type(e).__name__, e))
        return
    for item in stale:
        print("forced command: STALE for %s in %s — re-bake it from the "
              "controller with cli_webterm_only.append_controller_lane_pubkey_"
              "command (push never re-renders it, #1202)" % (item, ak_path))
