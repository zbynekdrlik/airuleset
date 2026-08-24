"""airuleset ~/.bashrc marker-block appliers (File B of the #433 cluster
L-F 2-file split) -- the two idempotent ~/.bashrc appliers that install
the managed claude launcher wrappers + companion scripts
(`apply_ultracode_launcher`, #77) and the subdev ssh-auto-attach block
(`apply_stream_ssh_attach`, #264), plus the ULTRACODE_BASHRC_BLOCK /
STREAM_SSH_ATTACH_* constants and the `_stream_marker_block_spans`
positional-span helper.

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
cluster L, step L-F -- decision 2 of the binding "Design -- klaster L
sub-split"). Same verbatim-move + facade-re-export pattern as L1/L2/L3.
airuleset.py keeps a single `from cli_bashrc_appliers import (...)`
re-export at the old definition site, so cmd_install's
`apply_ultracode_launcher()` / `apply_stream_ssh_attach()` calls (source
text unchanged) and tests' `airuleset.<name>` references keep working.

This is the SIBLING half of the L-F split: it forward-imports the script
TEMPLATES + renderers from the BASE half cli_claude_scripts.py. The
dependency is strictly one-directional (this file -> cli_claude_scripts),
never back -- no import cycle (mirror of L2's cli_scratch_sweep ->
cli_target_purge).

L2 SEAM LESSON -- the render_* / CLAUDE_*_SCRIPT_DEST names below are
forward-imported into THIS module's globals, so a test that patches
`airuleset.render_claude_launch_script` /
`airuleset.CLAUDE_LAUNCH_SCRIPT_DEST` and drives apply_ultracode_launcher
would NOT reach this module's bound copy (silent no-op) -- patch
`cli_bashrc_appliers.<name>`, or pass the explicit *_path= arguments
(which every current TestUltracodeLauncher test does). The objects are
identical (the same File A functions), so no live test is affected today.

SELF-CONTAINED at module level: stdlib only, no top-level `import
airuleset`. `ULTRACODE_MARK_START`/`END` below are this file's own copies
of the two canonical marker-sentinel string literals that stay resident in
airuleset.py (`airuleset.ULTRACODE_MARK_*`, referenced by tests) -- fixed
literals, no drift; ULTRACODE_BASHRC_BLOCK needs them at import time, and a
module-level `from airuleset import` would cycle-fail (the leaf must not
import airuleset at module level). Dup-while-resident has precedent
(CLAUDE_DIR is duped in cli_tmux_provisioning while resident in airuleset.py).

Three outbound resident couplings read inside the appliers at CALL time
are reached via a lazily-placed deferred `import airuleset`: `BASHRC` (the
~/.bashrc path default), `AUTHORITY_BY_USER` (the subdev-stream registry --
test-patched on airuleset, so it MUST read `airuleset.AUTHORITY_BY_USER`)
and `_current_user`. All three are defined in airuleset.py AFTER this
facade site, so only a call-time deferred import (airuleset fully loaded)
resolves them without an import cycle.
"""

import os
import sys
from pathlib import Path

from cli_claude_scripts import (
    CLAUDE_LAUNCH_SCRIPT_DEST,
    CLAUDE_HISTORY_SCRIPT_DEST,
    CLAUDE_HISTORY_POPUP_SCRIPT_DEST,
    render_claude_launch_script,
    render_claude_history_script,
    render_claude_history_popup_script,
)
# #649: the #613-r2 prefix+w window-menu helper is REMOVED (prefix+w now binds
# to the native `choose-tree -ZwG`, cli_tmux_provisioning). Its previously-
# deployed file is cleaned up below; the constant lives here so the cleanup and
# a test can share one source.
_LEGACY_WINDOW_MENU_SCRIPT_NAME = "airuleset-tmux-window-menu.sh"

# Canonical dup of the two ultracode marker sentinels -- byte-identical to
# airuleset.ULTRACODE_MARK_* (which stay resident for tests). Needed at
# import time by ULTRACODE_BASHRC_BLOCK below; see the module docstring.
ULTRACODE_MARK_START = "# >>> airuleset: ultracode default >>>"
ULTRACODE_MARK_END = "# <<< airuleset: ultracode default <<<"


# .bashrc holds ONLY thin one-line functions -- no flag literal survives here,
# so nothing flag-shaped can ever be frozen in a shell's memory again.
ULTRACODE_BASHRC_BLOCK = (
    f"{ULTRACODE_MARK_START}\n"
    f'claude() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" default "$@"; }}\n'
    f'claude-new() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" new "$@"; }}\n'
    f'claude-ultracode() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" ultracode "$@"; }}\n'
    f'claude-plain() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" plain "$@"; }}\n'
    f'claude-fullscreen() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" fullscreen "$@"; }}\n'
    f'claude-history() {{ python3 "$HOME/.claude/{CLAUDE_HISTORY_SCRIPT_DEST.name}" "$@"; }}\n'
    f"{ULTRACODE_MARK_END}"
)


def apply_ultracode_launcher(bashrc_path: Path = None, script_path: Path = None,
                              history_script_path: Path = None,
                              popup_script_path: Path = None) -> bool:
    """Install/refresh the managed claude launcher (#77) AND the
    claude-history companion (#267 -- same mechanism, same self-heal
    discipline, deliberately extended in place rather than given its own
    parallel marker-block machinery) AND the claude-history POPUP
    companion script (#289 -- see the module comment above
    CLAUDE_HISTORY_POPUP_SCRIPT_DEST for why this is its OWN script file
    rather than an inline shell command in the tmux bind-key line).

    #649: the #613-r2 tmux prefix+w WINDOW-MENU helper is no longer written --
    prefix+w now binds to the native `choose-tree -ZwG` (cli_tmux_provisioning),
    so no helper script exists; this function CLEANS UP a previously-deployed
    ~/.claude/airuleset-tmux-window-menu.sh (co-located with the popup script)
    so an upgraded box does not keep the dead file.

    The SCRIPT (script_path, default CLAUDE_LAUNCH_SCRIPT_DEST) is written and
    chmod +x UNCONDITIONALLY on every call — like the caveman shim, it must
    self-heal any tampering/rollback, and a missing script after write is a
    loud RuntimeError, never a silent loss of `claude`. It carries ALL the
    actual logic, so a `push` changes launch behavior in every already-running
    shell immediately, with no `source ~/.bashrc` and no restart. The
    claude-history script (history_script_path, default
    CLAUDE_HISTORY_SCRIPT_DEST) and the claude-history POPUP script
    (popup_script_path, default CLAUDE_HISTORY_POPUP_SCRIPT_DEST) both get the
    IDENTICAL unconditional write + chmod +x + missing-after-write RuntimeError
    treatment.

    The ~/.bashrc block is idempotent (replaces the marked block if present,
    else appends it) and holds ONLY thin wrapper functions with no flag
    literals. Returns True iff the ~/.bashrc file changed."""
    import re
    import airuleset
    bpath = bashrc_path or airuleset.BASHRC
    spath = script_path or CLAUDE_LAUNCH_SCRIPT_DEST
    hpath = history_script_path or CLAUDE_HISTORY_SCRIPT_DEST
    ppath = popup_script_path or CLAUDE_HISTORY_POPUP_SCRIPT_DEST

    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(render_claude_launch_script())
    os.chmod(str(spath), 0o755)
    if not spath.exists():
        raise RuntimeError(f"claude launcher script missing right after write: {spath}")

    hpath.parent.mkdir(parents=True, exist_ok=True)
    hpath.write_text(render_claude_history_script())
    os.chmod(str(hpath), 0o755)
    if not hpath.exists():
        raise RuntimeError(f"claude-history script missing right after write: {hpath}")

    ppath.parent.mkdir(parents=True, exist_ok=True)
    ppath.write_text(render_claude_history_popup_script())
    os.chmod(str(ppath), 0o755)
    if not ppath.exists():
        raise RuntimeError(f"claude-history popup script missing right after write: {ppath}")

    # #649: the #613-r2 prefix+w window-menu helper is REMOVED (prefix+w now
    # binds to the native `choose-tree -ZwG`). CLEAN UP a previously-deployed
    # helper co-located with the popup script (in production `~/.claude`), so an
    # upgraded box does not keep the dead file. `missing_ok=True` makes this a
    # true no-op on a fresh box or a repeated push; unlink is best-effort (a
    # stray permission error must never break the launcher install).
    legacy_menu = ppath.parent / _LEGACY_WINDOW_MENU_SCRIPT_NAME
    try:
        legacy_menu.unlink(missing_ok=True)
    except OSError as e:
        print(f"  #649 cleanup: could not remove dead window-menu helper "
              f"{legacy_menu}: {e}", file=sys.stderr)

    existing = bpath.read_text() if bpath.exists() else ""
    if ULTRACODE_MARK_START in existing and ULTRACODE_MARK_END in existing:
        pattern = re.compile(
            re.escape(ULTRACODE_MARK_START) + r".*?" + re.escape(ULTRACODE_MARK_END),
            re.S)
        new = pattern.sub(lambda _m: ULTRACODE_BASHRC_BLOCK, existing)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{ULTRACODE_BASHRC_BLOCK}\n"
    if new != existing:
        bpath.write_text(new)
        return True
    return False


# --- #263/#264/#563: subdev stream account dev-env convention --------------
# The convention working directory for a subdev/gatekeeper account's tmux
# session. NOT every account checks out at the same path (#563): montalu1
# (renamed from montalu, #537) has its project at ~/devel/odoo, other
# accounts check out ~/devel/odoo/odoo-erp, and gatekeeper has no odoo
# checkout at all. So the cwd is a FALLBACK CHAIN: the first EXISTING dir of
# STREAM_DEV_CWD_CHAIN wins, else $HOME. A binary "odoo-erp or $HOME"
# fallback dropped montalu1 into $HOME, where a claude wrote under the wrong
# project key (no history/memory -- the owner's complaint). STREAM_DEV_CWD_REL
# stays the primary (chain[0]). Used by BOTH #263's tmux bootstrap
# (_stream_session_cwd, below AUTHORITY_BY_USER) and #264's ssh auto-attach
# block (right below) -- ONE shared chain, not two independently-maintained
# copies.
STREAM_DEV_CWD_REL = "devel/odoo/odoo-erp"
STREAM_DEV_CWD_CHAIN = (STREAM_DEV_CWD_REL, "devel/odoo")

# --- #264: subdev stream ssh auto-attach ------------------------------------
# One subdev stream account = one tmux session; an interactive ssh login
# should attach straight into it instead of the user attaching by hand.
STREAM_SSH_ATTACH_MARK_START = "# >>> airuleset: subdev ssh auto-attach >>>"
STREAM_SSH_ATTACH_MARK_END = "# <<< airuleset: subdev ssh auto-attach <<<"
STREAM_SSH_ATTACH_BLOCK = (
    f"{STREAM_SSH_ATTACH_MARK_START}\n"
    "# #264: one subdev stream account = one tmux session -- an interactive\n"
    "# ssh login attaches straight into it (create-or-attach, `-A`). NEVER\n"
    "# fires for a NON-interactive ssh run (push's `git pull && python3\n"
    "# airuleset.py install`, scp/rsync, watchdog/gatekeeper automation) --\n"
    "# those pass a COMMAND to ssh, which bash executes with `$-` carrying no\n"
    "# 'i' and no PTY at all, so this whole block is a no-op for them; guarded\n"
    "# on all three explicitly anyway (interactive shell, a real ssh TTY, not\n"
    "# already inside tmux) so nothing here can ever race a live session.\n"
    # command -v tmux: if tmux is ever missing/broken on a stream account,
    # `exec tmux ...` would fail AFTER the shell has already been replaced
    # -- closing the ssh session outright instead of leaving a working
    # interactive shell behind (an adversarial review's finding — this
    # guard keeps that failure mode from ever being reachable).
    'if [[ $- == *i* ]] && [ -n "${SSH_TTY:-}" ] && [ -z "${TMUX:-}" ] '
    '&& command -v tmux >/dev/null 2>&1; then\n'
    # #563: cwd FALLBACK CHAIN -- first EXISTING dir of STREAM_DEV_CWD_CHAIN
    # wins, else $HOME. A binary "odoo-erp or $HOME" fallback dropped montalu1
    # (project dir ~/devel/odoo, no odoo-erp subdir) into $HOME, so a claude
    # launched there wrote under the wrong project key (no history/memory).
    '  __airuleset_cwd="$HOME"\n'
    f'  for __airuleset_rel in {" ".join(STREAM_DEV_CWD_CHAIN)}; do\n'
    '    if [ -d "$HOME/$__airuleset_rel" ]; then\n'
    '      __airuleset_cwd="$HOME/$__airuleset_rel"; break\n'
    '    fi\n'
    '  done\n'
    '  __airuleset_me="$(whoami)"\n'
    "  # #284/#593: search for a live group survivor whose NAME may differ\n"
    "  # from this exact username before the plain -A reattach. This once\n"
    "  # guarded against a tmux destroy-unattached sweep (#254) reducing a\n"
    "  # multi-member session GROUP down to one iteration-order-arbitrary\n"
    "  # survivor -- but that sweep no longer happens globally after #591\n"
    "  # (which REMOVED the global `destroy-unattached keep-last`; #591\n"
    "  # scopes cleanup to a per-session `client-attached` hook per clone\n"
    "  # instead -- cli_webterm's webterm clone and the survivor-join\n"
    "  # below). The survivor search is KEPT as harmless defense-in-depth:\n"
    "  # if a differently-named survivor ever exists (an older server's\n"
    "  # leftover, a manual sweep), the plain -A path would silently\n"
    "  # create a fresh EMPTY session while the real, populated one sits\n"
    "  # orphaned in its own group. `=`-anchored EXACT match (#263's own\n"
    "  # established fix: a bare target does PREFIX matching and would\n"
    "  # wrongly match e.g. zbynek-4 for zbynek); if found, join it as a\n"
    "  # new independent VIEW onto the SAME windows (grouped session),\n"
    "  # which the survivor-join below now arms with its OWN per-session\n"
    "  # destroy-unattached hook (#593) so its detached duplicates\n"
    "  # self-clean. The survivor's own name is captured into a variable\n"
    "  # and the `exec` happens AFTER the `while ... done < <(...)` loop\n"
    "  # closes, never inside it -- an adversarial review proved live that\n"
    "  # an `exec` sitting INSIDE the process-substitution loop inherits\n"
    "  # that pipe as its own stdin, so a real tmux client refuses to\n"
    "  # attach (`open terminal failed: not a terminal`) and the ssh login\n"
    "  # dies right there, since `exec` already replaced the shell. Falls\n"
    "  # through to the plain exact-name path below when no survivor is\n"
    "  # found, or tmux itself is unreachable.\n"
    '  if ! tmux has-session -t "=$__airuleset_me" 2>/dev/null; then\n'
    '    __airuleset_survivor=""\n'
    '    while read -r __airuleset_g __airuleset_n; do\n'
    '      if [ -n "$__airuleset_n" ] '
    '&& [ "$__airuleset_g" = "$__airuleset_me" ]; then\n'
    '        __airuleset_survivor="$__airuleset_n"\n'
    "        break\n"
    "      fi\n"
    "    done < <(tmux list-sessions "
    "-F '#{session_group} #{session_name}' 2>/dev/null)\n"
    '    if [ -n "$__airuleset_survivor" ]; then\n'
    # #593: the survivor-join clone is ALSO a grouped-session creator
    # (`new-session -t`), so give it the SAME per-session `client-attached
    # destroy-unattached on` hook cli_webterm's #591 clone got -- otherwise,
    # now that #591 removed the GLOBAL keep-last sweep, its detached
    # duplicates orphan forever (the #254 pile-up, returning for the ssh
    # path). Named explicitly (`-s`) so the hook can target it; created
    # DETACHED (`-d`) then the hook armed then attached, because setting
    # `destroy-unattached on` on a zero-client session destroys it
    # IMMEDIATELY -- so the hook DEFERS the `on` to client-attached time
    # (both live-verified tmux constraints carried verbatim from #591).
    # `set-hook -t` does NOT take tmux's `=` exact-match anchor (only
    # has-session/kill-session do), so the just-created clone is targeted by
    # its bare, unambiguous name. The survivor is a DURABLE group member
    # holding the group's windows (normally the `-A -s` base -- a standalone
    # base is ungrouped, so the scan is only reached once a `-t` clone has
    # grouped it), so the join safely self-destructs on the user's detach while
    # that member keeps the windows alive -- no trap needed (unlike webterm's
    # throwaway view): the per-session `on` IS the cleanup. If the only survivor
    # is itself a transient view, the group's windows live only while some
    # member remains -- the benign ownerless-clone residual cli_webterm
    # documents. The detached-create is success-guarded so a failed create
    # (name clash, tmux briefly unreachable) FALLS THROUGH to the plain `-A -s`
    # base path below, never a dead ssh session OUTSIDE the documented
    # transition residual. TRANSITION RESIDUAL (same class as
    # #591-review B1): on a box NOT yet re-installed after #591 whose running
    # server still carries the old GLOBAL keep-last, the detached clone is
    # swept at creation and this connect fails -- self-heals on that box's
    # next install, which unsets the global (cli_tmux_provisioning).
    '      __airuleset_join="${__airuleset_me}-join-$$"\n'
    '      if tmux new-session -d -t "$__airuleset_survivor" '
    '-s "$__airuleset_join" 2>/dev/null; then\n'
    '        tmux set-hook -t "$__airuleset_join" client-attached '
    '"set-option destroy-unattached on" 2>/dev/null\n'
    '        exec tmux attach-session -t "$__airuleset_join"\n'
    "      fi\n"
    "    fi\n"
    "  fi\n"
    '  exec tmux new-session -A -s "$__airuleset_me" -c "$__airuleset_cwd"\n'
    "fi\n"
    f"{STREAM_SSH_ATTACH_MARK_END}"
)

# --- #562: gk box ssh auto-attach -------------------------------------------
# The gk box `gatekeeper` account is NOT a subdev stream account (not in
# AUTHORITY_BY_USER -- that map is about MERGE authority + stream scoping,
# neither of which gatekeeper is part of), but the owner wants the SAME #264
# ssh auto-attach behaviour there: an interactive `ssh gatekeeper@gk` should
# land straight in tmux instead of a bare shell. So the eligibility gate is
# widened by this explicit extra-user set, keeping the change strictly in the
# ssh-attach lane -- adding "gatekeeper" to AUTHORITY_BY_USER would misclassify
# it as a subdev stream everywhere downstream (statusline slice, skill scoping,
# notify routing). Owner ask (2026-08-19): "uz ma skor vsade po ssh pekne joine
# do tmux okrem ked sa ssh do gk, tam musim vsetko sam".
SSH_ATTACH_EXTRA_USERS = frozenset({"gatekeeper"})


def is_single_session_box_user(user: str = None) -> bool:
    """True iff `user` runs the fleet's ONE-tmux-session-per-account model
    (#264): a subdev stream account (AUTHORITY_BY_USER) or the gk box
    `gatekeeper` account (SSH_ATTACH_EXTRA_USERS, #562). The owner's `newlevel`
    boxes (dev1/dev2) run MANY project sessions and are NOT in this set.

    This is the ONE source of truth for that distinction. The #264 ssh
    auto-attach (`apply_stream_ssh_attach`) creates exactly one `-A -s "$me"`
    session per such account, and the #554/#592 per-target WINDOW-name block
    (`apply_stream_tmux_window_name`) names that single window -- so BOTH gate
    on this predicate. A multi-project box must NEVER get either: naming every
    window the same literal + `automatic-rename off` destroys the owner's
    per-project navigation (#593, the #592 regression on dev1/dev2)."""
    import airuleset
    u = user or airuleset._current_user()
    return u in airuleset.AUTHORITY_BY_USER or u in SSH_ATTACH_EXTRA_USERS


def _stream_marker_block_spans(existing, start=STREAM_SSH_ATTACH_MARK_START,
                                end=STREAM_SSH_ATTACH_MARK_END):
    """Left-to-right positional scan for CLEAN (start, end) marker pairs --
    NEVER a lazy regex `.*?` search, which silently deletes real content
    sitting between a stray leftover START and a LATER, genuine block's END
    on a second run against a conf/rc file externally corrupted with a
    marker literal (#235's own documented failure of that exact shape,
    `_clean_tmux_block_spans`). A pair CROSSED by another marker literal is
    skipped and left as inert text, never merged into a neighbouring block
    -- that specific corruption class is closed.

    Known residual (live-verified by an adversarial review, kept honest
    here rather than overclaimed): a single ISOLATED stray START/END pair
    with no OTHER marker literal between them (e.g. a truncated copy-paste
    accident) still reads as one clean span and its own real content IS
    still lost on rewrite -- no purely positional scan can distinguish that
    shape from "this is genuinely our own block". Also: an unpaired,
    never-matched marker line is never removed by this function on its
    own -- it stays as inert text forever (harmless, since it's a bash
    comment, but neither `apply_stream_ssh_attach` add nor remove path
    "self-heals" it)."""
    spans = []
    pos = 0
    s_len = len(start)
    while True:
        s = existing.find(start, pos)
        if s == -1:
            break
        e = existing.find(end, s + s_len)
        if e == -1:
            pos = s + s_len
            continue
        inner = existing[s + s_len:e]
        if start in inner or end in inner:
            pos = s + s_len
            continue
        e_full = e + len(end)
        spans.append((s, e_full))
        pos = e_full
    return spans


def apply_stream_ssh_attach(bashrc_path: Path = None, user: str = None) -> bool:
    """Idempotently add/remove the #264 ssh-auto-attach marker block in
    ~/.bashrc, scoped to the ssh-attach eligibility set: subdev stream
    accounts (AUTHORITY_BY_USER's keys -- the registry #263's tmux bootstrap
    also keys off) UNION SSH_ATTACH_EXTRA_USERS (the gk box `gatekeeper`
    account, #562 -- eligible for the block but deliberately NOT in
    AUTHORITY_BY_USER, since that map is the stream registry and gatekeeper is
    not a stream). Every account OUTSIDE that union (dev1/dev2 = `newlevel`,
    any other): the marker is actively REMOVED there if ever present, so a
    future eligibility edit can never leave a stale attach block on the wrong
    account.

    Same overall idempotent-marker-block shape as apply_ultracode_launcher
    (#77) -- create/update if this account should have it, strip if not --
    but the presence check + rewrite use a positional span scan
    (`_stream_marker_block_spans`), not a lazy regex search, which closes
    the WORST failure of that class (a stray leftover START silently
    eating real content up to a LATER genuine block's END) without
    claiming to close every possible corruption shape -- see that
    function's own docstring for the residual it honestly leaves open.
    Returns True iff ~/.bashrc changed."""
    import airuleset
    bpath = bashrc_path or airuleset.BASHRC
    u = user or airuleset._current_user()
    # The ssh-auto-attach eligibility set IS the single-session-per-account set
    # (#593): subdev streams (AUTHORITY_BY_USER) + the gk `gatekeeper` account
    # (SSH_ATTACH_EXTRA_USERS, #562). Shared with the #592 window-name block via
    # ONE predicate so the two can never drift on "which boxes are single-session".
    should_have = is_single_session_box_user(u)
    existing = bpath.read_text() if bpath.exists() else ""
    spans = _stream_marker_block_spans(existing)
    if should_have:
        if spans:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                out.append(STREAM_SSH_ATTACH_BLOCK)
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
        else:
            sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
            new = f"{existing}{sep}\n{STREAM_SSH_ATTACH_BLOCK}\n"
    else:
        if not spans:
            return False
        out, cursor = [], 0
        for s, e in spans:
            out.append(existing[cursor:s])
            cursor = e
        out.append(existing[cursor:])
        new = "".join(out)
    if new != existing:
        # Atomic write: a plain write_text() truncates-then-writes, a real
        # (if narrow) window for a killed process (e.g. this account's own
        # `push` install, now running longer network steps that can time
        # out) to leave ~/.bashrc half-written. tmp-write + os.replace
        # makes the swap atomic on the same filesystem.
        tmp = bpath.with_suffix(bpath.suffix + ".airuleset-tmp")
        tmp.write_text(new)
        os.replace(str(tmp), str(bpath))
        return True
    return False
