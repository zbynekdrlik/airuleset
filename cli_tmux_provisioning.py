"""airuleset tmux.conf / tmux-server provisioning — cluster L sub-split (#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as the earlier
H/I/J/K/L1/L2 CLI leaves and the A-F watchdog leaves). airuleset.py keeps a
single `from cli_tmux_provisioning import (...)` re-export at the old
definition site, so cmd_install's tmux-provisioning steps, cmd_push's tmux
cutover, report_stream_dev_env / ensure_stream_tmux_session, and every test's
`airuleset.apply_tmux_history_limit(...)` / `airuleset.TMUX_*` reference all
keep working unchanged.

This region provisions the managed `~/.tmux.conf` marker block (history
limit, default window size, scrollback keybinds, history-popup keybind) plus
the live self-heals that UNSET stale globals on a running server
(destroy-unattached #591, aggressive-resize #613) and the systemd
tmux-server CUTOVER that swaps a box onto the newest tmux build. The conf block
carries no destroy-unattached line (that is a live `-gu` unset, not conf policy),
but DOES carry `window-size manual` version-gated (#586, restored by #613
REOPEN-2: the owner's fixed-size invariant) + `default-size 176x50` -- both in
the conf AND, since #685, live-converged on a running >= 3.5 server via the
gated `converge_tmux_window_geometry`.

Deliberately SELF-CONTAINED: stdlib only at module level (`subprocess` is
imported locally inside the three functions that use it, verbatim), NO
top-level `import airuleset` — this leaf has ZERO airuleset.py-resident
outbound COUPLINGS. The two path constants below are this file's own
value-identical copies of the canonical one-line expressions (established
repo idiom, cf. cli_scratch_sweep.py's own CLAUDE_DIR):
  * REPO_DIR — `Path(__file__).resolve().parent`; the leaf sits in the same
    repo directory as airuleset.py, so the value is byte-identical.
  * CLAUDE_HISTORY_POPUP_SCRIPT_DEST — the tmux history-popup keybind
    (TMUX_POPUP_BIND_ARGVS, built at import by calling _tmux_popup_bind_argv)
    bakes this absolute path in. airuleset.py keeps its OWN resident copy at
    the definition site of render_claude_history_popup_script (that half is
    NOT part of this cluster and stays resident); this leaf carries its own
    identical copy so it needs no `import airuleset`. Same canonical
    derivation, identical value.
"""

import os
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_HISTORY_POPUP_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-history-popup.sh"
# #649: the #613-r2 prefix+w WINDOW-PICKER helper (airuleset-tmux-window-menu.sh)
# is REMOVED -- prefix+w now binds to the native `choose-tree -ZwG` (see the
# TMUX_CHOOSE_TREE_BIND_ARGVS module comment below). cli_bashrc_appliers cleans
# up the previously-deployed ~/.claude/airuleset-tmux-window-menu.sh file.


TMUX_CONF = Path.home() / ".tmux.conf"
TMUX_HISTORY_LIMIT = 50000
TMUX_DEFAULT_SIZE = "176x50"

# #660: audit trail for tmux stray-session hygiene on the OWNER box --
# `normalize.log` (the kill-sweep's kill/skip decisions) and, from the
# native session-created hook, `session-created.log` (creator capture).
TMUX_AUDIT_DIR = CLAUDE_DIR / "tmux-audit"

# #660: a pane whose FOREGROUND command (tmux's `#{pane_current_command}`,
# which strips the login-shell leading dash) is one of these bare shells is an
# idle pane BY FOREGROUND ONLY -- NOT by itself proof the session is absorbable
# (review 🔵3). `_maybe_absorb_idle_stray` requires TWO more conditions the
# foreground command cannot show: no child process (`_pane_has_child` -- a
# Ctrl-Z'd claude) and cwd in $HOME (a work session sits in a project dir). ANY
# other foreground command (claude, node, an editor, ssh, python, ...) means the
# session is doing work and is NEVER killed (feedback_never_touch_stopped_sessions).
_BARE_SHELL_COMMANDS = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "ash", "mksh",
})
# #613 REOPEN-2 (owner directive 2026-08-22): `window-size manual` is RESTORED
# (version-gated + conf-only, exactly #586), reversing the first reopen. The
# owner's governing agreement is a FIXED terminal size for EVERY client so no
# client ever resizes another's window ("dohodli sme sa ze budeme mat fixnu
# velkost terminalov pre vsetkych aby nevznikalo stale resizovanie"). `manual`
# pins EVERY window to `default-size` regardless of any client, so a smaller
# webterm client can never shrink the owner's Windows-Terminal view (the "dark"
# report -- his SURFACE; the browser was the CAUSE). The first reopen switched to
# `latest`, which sizes a window to whichever client last TOUCHED it, so the
# smaller browser re-pinned the owner's windows to its grid and his larger WT was
# left rendering a dark unused region -- proven live (his windows measured 160x46
# today) + on an isolated socket. The browser's OWN appearance (no dark area at
# the fixed grid) is solved on the BROWSER side (cli_webterm dashboard fit),
# never by letting the browser resize tmux. See the incident-history comment
# above render_tmux_history_block for the full trail.
# #586: the `window-size` server option value that PINS the fixed geometry -- no
# attached client (whatever its size) can ever resize a window away from
# `default-size`. EMITTED ONLY on a tmux version where it is SAFE at conf-parse
# startup (see _tmux_supports_window_size_manual / _MIN_WINDOW_SIZE_MANUAL_VERSION
# below) -- never unconditionally, because #241 crashes tmux 3.4 with it. In
# the conf, and since #685 ALSO live-set -- but ONLY via the version-gated
# `converge_tmux_window_geometry` (a 3.4/unprobeable box never gets a live
# geometry call, so the #236/#241 hazard classes stay unreachable).
TMUX_WINDOW_SIZE = "manual"
# #586: the MINIMUM (major, minor) tmux version at which `set-option -g
# window-size manual` is SAFE in the conf at server-startup. Reproduced LIVE on
# dev1: tmux 3.4 (/usr/bin/tmux, Ubuntu 24.04 noble stock) CRASHES at startup
# with the line (`server exited unexpectedly`, exit 1); tmux 3.7b
# (/usr/local/bin/tmux, the #242 cutover build) starts cleanly (re-verified live
# 2026-08-22: a `-f <conf with window-size manual>` 3.7b server starts, options
# read `manual`). The fleet ships ONLY 3.4 and 3.7b, so (3, 5) is the conservative
# boundary that excludes exactly the known-bad 3.4 and includes the verified-good
# 3.7b.
_MIN_WINDOW_SIZE_MANUAL_VERSION = (3, 5)
# #591: there is NO managed `destroy-unattached` value any more -- the global
# option is REMOVED from the conf entirely (fresh servers inherit tmux's factory
# default `off`, which never destroys a session). #254 shipped a GLOBAL
# `destroy-unattached keep-last` to sweep detached grouped-session duplicates;
# that value protects the group's LAST member, NOT the BASE session, so once a
# webterm clone (`new-session -t`, cli_webterm) forms a group, the owner
# detaching from the BASE destroys the base (unattached, grouped, not-last) --
# killing the live Claude process inside -- and the clone's later teardown
# empties the whole tmux server (the gk 2026-08-20 09:58 total-death). Verified
# live on an isolated `-L` scratch tmux 3.7b server with real pty clients.
# The webterm throwaway clone now self-cleans PER-SESSION instead (a
# `client-attached` hook arming its OWN session-scoped `destroy-unattached on`
# in cli_webterm), so ONLY the clone is ever swept and the base is untouched --
# verified live that a session-scoped set does NOT leak to other group members.
# See render_tmux_history_block/apply_tmux_history_limit below (the live-apply
# now UNSETS any stale global -- a #254-style self-heal), and
# TestTmuxDestroyUnattached in tests/test_airuleset.py for the regression locks.
TMUX_MARK_START = "# >>> airuleset tmux >>>"
TMUX_MARK_END = "# <<< airuleset tmux <<<"
# #235: tmux's own built-in default (2000-line scrollback) plus the current
# Claude Code renderer re-rendering the viewport in place and stacking
# duplicate/partial frames into pane history on re-render events made real
# scrollback holey within minutes under agentic load (measured live: active
# panes saturated at ~1937-1942/2000). Fix: raise history-limit fleet-wide.
# Same idempotent-marker-block shape as apply_ultracode_launcher (#77) above
# -- create the file if missing, rewrite ONLY the block's content if the
# markers already exist, never touch anything outside them.
#
# #236: the identical frame-stacking mechanism also fires on every ATTACH
# from a different-sized terminal -- tmux's default `window-size latest`
# auto-resizes the whole window to fit the new client, and Claude Code
# re-renders the visible screen in place on that resize. #236 originally
# tried to pin `window-size manual` (stop the auto-resize) alongside
# `default-size 176x50` (the fixed size new windows get -- the user's own
# client, 176x51, is the confirmed smallest on the fleet, so 176x50 crops
# nobody and larger clients just get an unused margin).
#
# #241: `window-size manual` was REMOVED again -- it CRASHES tmux 3.4's
# server outright at startup (`server exited unexpectedly`), confirmed
# live against the real 3.4 binary every managed box runs, the only
# version Ubuntu 24.04 noble ships. A box whose conf carried the line
# could not start tmux at all. This is a DIFFERENT failure than #236's own
# live-apply finding (flipping window-size against a RUNNING server snaps
# every window back to its stored size -- a disruptive resize, not a
# crash): there is no safe way to ship the option at all, conf-only or
# otherwise, so it is gone from the managed block entirely. Cost: without
# `manual`, tmux keeps auto-tracking the smallest attached client's size,
# so the fixed geometry #236 wanted is only PARTLY delivered by
# `default-size` alone (new windows still start at 176x50; an existing
# window's LIVE size is no longer pinned against later attach/detach
# cycles) -- see #236's own comment thread for that trade-off.
# `default-size` stays: it starts cleanly on 3.4. This ticket's own
# incident history (two live-tmux destructions on dev1, the second a
# kernel segfault in tmux 3.4's format-expansion code) settled that a
# per-window resize call is NEVER part of this feature: setting the
# surviving default-size OPTION does not disturb any attached client's
# current window size, and resizing a window in place buys nothing new
# windows don't already get from `default-size` on their own -- see
# TestTmuxWindowSizeNoResize for the structural, whole-file lock (the
# exact tmux subcommand name is deliberately not spelled out here so this
# comment can't ever collide with that lock).
#
# #586: `window-size manual` was RESTORED version-gated + conf-only (the #241
# mistake was shipping it fleet-wide), to PIN a fixed 176x50 geometry so a small
# webterm client could not shrink the owner's WT view (the #584 regression).
#
# #613 REOPEN (first attempt, REVERSED below): removed `window-size manual`
# entirely and switched the conf to tmux's native `latest`, on the theory that
# the manual pin was itself the dead border -- because its verification measured
# the BROWSER client (larger, so it saw a border over the pinned 176x50 window).
# That theory targeted the wrong client and BROKE the owner: under `latest` a
# window sizes to whichever client last TOUCHED it, so the SMALLER browser (160x46
# measured today) re-pinned the owner's real windows and his larger Windows
# Terminal was left rendering a dark unused region -- exactly the "dark" he keeps
# reporting (his SURFACE), the browser being the CAUSE.
#
# #613 REOPEN-2 (owner directive 2026-08-22, CURRENT): the fixed-size invariant
# is RESTORED -- `window-size manual` (version-gated + conf-only, #586) + the
# clone's `-f ignore-size` (cli_webterm). `manual` pins EVERY window to
# `default-size 176x50` regardless of any client, so no client resizes another's
# window; `-f ignore-size` additionally excludes the webterm from the size calc
# so a box still RUNNING the first-reopen `latest` server (before its next
# restart) is fixed immediately without a restart. Proven live (isolated tmux 3.7b
# + pty clients): a WEBTERM(160x46) attaching under `latest`+plain-clone shrinks
# the owner's window to 160x45 (owner DARK); under `manual` OR `latest`+ignore-size
# the owner's window stays 176x50 (full) at every attach + window-switch from both
# sides. `default-size 176x50` unchanged. The `set-option -gu window-size`
# live-unset the first reopen added is REMOVED (it forced a running server to
# `latest`, the opposite of the invariant).
#
# #685 (2026-08-25) then REVISED the "window-size is NEVER live-SET" half of
# that doctrine: a conf-only pin provably never reaches a server started
# BEFORE it (a tmux server reads the conf once, at start, and agentic fleet
# boxes never restart tmux -- restarting would kill live Claude sessions), so
# dev2's pre-v0.1.43 server still ran `latest`, David's 305x57 client sized
# the codex-bridge window to 305x56, and the owner's 176x51 `-f ignore-size`
# webterm client cropped the bottom rows (the invisible CC footer). The
# `ignore-size` bridge only stops the webterm from SHRINKING a window; it
# cannot fix one already LARGER than the owner's viewport. So
# `converge_tmux_window_geometry` (below) now LIVE-converges a running server
# -- set `window-size manual` + `default-size 176x50` when they differ, plus
# a per-window resize to 176x50 -- VERSION-GATED on the same >= 3.5 probe as
# the conf line (fails CLOSED: a 3.4/unprobeable box gets NO live geometry
# call, so the #241 conf-crash and the dev1 3.4 format-expansion segfault
# classes stay impossible), strictly idempotent, and proven safe live with
# the exact command sequence on montalu1-6@subdev + dev1 (supervisor) and
# dev2/fleet (this ticket) -- sessions untouched, no restarts, never a
# keystroke. The #236 "snap-resize" is no longer a hazard but the owner-ruled
# DESIRED convergence itself (#672: one fixed size for everyone).
# The browser's OWN appearance at the fixed grid (no dark area, no giant grid) is
# solved on the BROWSER side: the dashboard forces each ttyd xterm to the owner's
# fixed 176x51 client grid and font-scales it to fill the viewport (cli_webterm),
# never by letting the browser resize tmux. The `aggressive-resize` `-gwu` live
# self-heal (harmless stale-global cleanup) stays.
#
# #267: raising history-limit only fixed how much scrollback SURVIVES --
# the user's live complaint ("neviem sa v tom pretacat, kolieskom cez ssh
# sa to blbo pouziva") was that reaching it needs a MOUSE (tmux's default
# scroll-wheel-into-copy-mode binding), which is awkward over ssh, and the
# user explicitly asked for the keyboard shortcut old Linux virtual
# consoles used: Shift+PageUp/PageDown. `bind-key -n S-PageUp copy-mode
# -eu` (root table, no prefix key) enters copy-mode and scrolls up one
# page in one keystroke; `-e` auto-exits copy-mode the moment the user
# scrolls back down to the bottom, so Shift+PageDown alone (bound in BOTH
# copy-mode key tables, vi and emacs, since the managed conf pins neither
# `mode-keys` setting) returns to the live view -- matching the user's own
# "Shift+PgDn / navrat na spodok vrati live view" acceptance line.
# UNLIKE window-size/default-size above, a `bind-key` call is SAFE to
# live-apply against a running server: it only registers a key-table
# entry -- it does not touch any window's geometry, force a
# recalculate_sizes() pass, or read/write anything CC's renderer has
# already drawn, so it carries none of #236's live-apply hazard. Verified
# live (#267): bound against a real running server, then driven through a
# REAL attached pty client sending the actual xterm CSI byte sequences for
# Shift+PageUp/PageDown (`send-keys -t <pane>` alone does NOT exercise a
# key-table binding -- it writes bytes straight into the pane's pty,
# bypassing the server's key dispatch entirely; only a genuinely attached
# client's input passes through the binding tables) -- Shift+PageUp
# correctly entered copy-mode and scrolled up, Shift+PageDown correctly
# scrolled back down and auto-exited to the live view, with the pane's
# own content completely undisturbed throughout.
#
# #591: the global destroy-unattached is REMOVED (see the TMUX_DESTROY_UNATTACHED
# removal comment above for the full mechanism). #254 shipped `destroy-unattached
# keep-last` to sweep detached grouped duplicates, but that value protects the
# group's LAST member, NOT the BASE session -- so once a webterm clone
# (`new-session -t`, cli_webterm) forms a group, the owner detaching from the
# BASE destroyed it (unattached, grouped, not-last) with the live Claude process
# inside, and the clone's later teardown emptied the whole tmux server (gk
# 2026-08-20 09:58 total-death). Reproduced live on an isolated `-L` scratch
# tmux 3.7b server with real pty clients. The conf now carries NO line at all
# (fresh servers inherit tmux's default `off`, which never destroys a session),
# and the live-apply below UNSETS (`-gu`) the global -- a #254-style self-heal
# that reverts any RUNNING server still carrying the base-killing `keep-last`
# back to `off` on the next push/install (verified live: `-gu` on a keep-last
# server -> off, idempotent). The webterm throwaway clone self-cleans PER-SESSION
# instead (a `client-attached` hook arming its OWN session-scoped
# `destroy-unattached on`, cli_webterm), so ONLY the clone is ever swept -- a
# session-scoped set does NOT leak to other group members (base stays `off`,
# verified live). destroy-unattached is safe to touch on a running server for
# the #235-vs-#254 reason (it only ever evaluates ZERO-client sessions), and
# unsetting to `off` can never destroy anything.
#
# Pane addressing (verified, not assumed): every keystroke-sending job in
# watchdog/__init__.py (list_claude_panes/_reconcile_candidate_panes)
# addresses panes exclusively by tmux's stable `#{pane_id}` (`%N`,
# server-global, independent of which session name currently references
# the underlying window -- their own docstrings already say "grouped
# sessions share the same pane_id"). `_pane_location()` (which renders a
# `session:window.pane` string like the `zbynek-4:2.0` the ticket cites)
# is used PURELY as human-readable text interpolated into log lines,
# never as a `tmux -t` target -- so destroying a detached grouped
# sibling's session name can never break pane resolution.


# #267: the three Shift+PgUp/PgDn keyboard-scrollback bindings, as tmux
# argv lists -- shared verbatim between the rendered conf lines
# (render_tmux_history_block, below) and the live-apply calls
# (apply_tmux_history_limit) so the two can never drift apart. A `bind-key`
# call is a pure key-table registration -- see the incident-history comment
# above for why that makes it safe to live-apply, unlike window-size/
# default-size.
#
# #338: the user repeatedly asked whether Claude Code's OWN native
# transcript viewer (Ctrl+O, CC v2.1.226+ -- reads the session's clean
# internal history, immune to the tmux-scrollback frame-duplication defect
# #267's own bind only ever scrolls INTO; PgUp/PgDn already work natively
# once it's open, no further wiring needed) could be reached via the same
# Shift+PageUp muscle memory #267 already trained. S-PageUp is now
# CONDITIONAL, via tmux's own `if -F` (alias of `if-shell -F`): inside a
# pane whose `pane_current_command` is literally `claude` it sends `C-o`;
# everywhere else it falls through to the ORIGINAL, byte-identical
# `copy-mode -eu`. `if -F`'s format string is evaluated by tmux PER
# KEYPRESS against the CURRENT client's pane, never once at conf-parse/
# bind time -- verified LIVE (not read from docs): a real attached pty
# client fed the real xterm CSI bytes for Shift+PageUp (`\x1b[5;2~`)
# against two different panes bound to the SAME key on an isolated
# scratch server -- a pane whose `pane_current_command` was `claude` (a
# real fixture: `bash -c 'stty raw -echo; exec -a claude cat > <file>'` --
# the `stty raw -echo` is load-bearing, a canonical-mode pty buffers a
# lone control byte with no trailing newline and never delivers it)
# received exactly one byte `0x0f` (Ctrl+O); a plain `sleep` pane on the
# identical bind entered copy-mode (`#{pane_in_mode}` flipped 0->1),
# unchanged from #267's own pre-#338 behaviour. `tmux send-keys -t <pane>`
# was deliberately NEVER used to exercise the binding itself -- it
# bypasses key-table dispatch and writes straight into the pty, proving
# nothing about whether a real keypress reaches the bound command (see
# the incident-history comment above TMUX_SCROLLBACK_KEYBINDS' own #267
# entry for the same lesson). Also confirmed live: the RENDERED
# (`_tmux_conf_quote`d) conf line starts cleanly from a COLD conf file,
# and live-applies cleanly against an already-running server, on BOTH the
# fleet's real deployed `/usr/bin/tmux` (3.4) and `/usr/local/bin/tmux`
# (3.7b) -- no crash-at-parse-time hazard of the `window-size manual`
# (#241) kind. `S-NPage` (Shift+PageDown) is deliberately left untouched
# -- no existing root-table bind, and the native viewer's own PgDn already
# works once it's open.
#
# This is the FIRST entry whose argv holds multi-word NESTED-COMMAND
# tokens (`"send-keys C-o"`, `"copy-mode -eu"`, each one single tmux
# argument tmux itself re-parses as an embedded command string) -- live
# apply passes them straight through subprocess argv (no shell, no
# quoting needed), but the RENDERED conf line now needs the same
# per-token `_tmux_conf_quote` the popup binds (TMUX_POPUP_BIND_ARGVS)
# already use, or the unquoted "send-keys C-o" would parse as FOUR
# separate tmux words instead of one, silently corrupting the `if -F`
# command's own argument count (see render_tmux_history_block below).
TMUX_SCROLLBACK_KEYBINDS = [
    ["bind-key", "-n", "S-PageUp", "if", "-F",
     "#{==:#{pane_current_command},claude}",
     "send-keys C-o", "copy-mode -eu"],
    ["bind-key", "-T", "copy-mode", "S-PageDown", "send-keys", "-X", "page-down"],
    ["bind-key", "-T", "copy-mode-vi", "S-PageDown", "send-keys", "-X", "page-down"],
]


# #289: a one-keystroke POPUP over `claude-history` (#267's companion --
# reads the session TRANSCRIPT, immune to the tmux frame-stacking defect
# S-PageUp above merely scrolls INTO). Root problem this closes: #267
# shipped claude-history but gave the user no discoverable path to it from
# a running session; #289 was reopened because nobody ever typed the bare
# command.
#
# KEY CHOICE (engineer's call, ask-before-assuming.md -- an internal/
# diagnostic element's placement has no user stake): originally Shift+F1
# (`S-F1`), root table, no prefix -- REMOVED by #376 (never confirmed to
# reach the user's real terminal/ssh client; see the module comment above
# TMUX_POPUP_PREFIX_KEY). `prefix + h` (mnemonic: history) -- unbound in
# stock tmux's prefix table (verified live, `-f /dev/null` throwaway
# socket) -- is the ONE surviving binding: the only one the user
# personally confirmed opens.
#
# MECHANISM: `display-popup`'s own SHELL-COMMAND argument is NOT format-
# expanded by tmux (verified live, tmux 3.7b: a literal `#{pane_id}`
# inside the command string reaches the shell UNSUBSTITUTED). `-d`
# (start-directory) IS format-expanded (verified live the same way) --
# so `-d '#{pane_current_path}'` puts the popup's shell in the
# ORIGINATING PANE's own cwd, and claude-history's own `--cwd` default
# (`os.getcwd()`) then resolves the right project with no `--pane`
# argument needed at all. The popup invokes the POPUP SCRIPT
# (CLAUDE_HISTORY_POPUP_SCRIPT_DEST, an absolute path baked in at Python
# render time) directly -- never the `claude-history` bashrc FUNCTION,
# since `display-popup` runs its shell-command non-interactively and
# `~/.bashrc` (where the function lives) is never sourced.
#
# CAPTURE-PANE RESOLUTION (#337, used by the popup's own capture-pane
# fallback -- the ONLY consumer as of #376, since S-DC's mode-only path is
# gone -- see CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT below): a bare
# (no `-t`) `tmux capture-pane` call issued from WITHIN a display-popup
# job's own shell-command resolves against the ORIGINATING pane -- the
# one the popup key was actually pressed in -- never the popup's own
# freshly-created pseudo-pane. Verified live, twice, independently, on
# both tmux 3.4 and 3.7b: an isolated multi-window/multi-client server
# with the raw popup-key bytes injected into a real attached pty client
# confirmed the resolution correctly follows whichever client pressed
# the key, including a run where the popup job's own `$TMUX_PANE`
# carried the popup's OWN pane id (e.g. `%3`) while the bare capture
# still returned the ORIGINATING pane's content -- proof this does not
# rely on `$TMUX_PANE` at all. Two proven ways to BREAK this
# resolution, never do either: adding `-c <client>` to `display-popup`
# (routes to the wrong session's pane when the popup is opened from an
# outside command client), or invoking the script via `tmux run-shell`
# instead of directly as the popup's own shell-command.
#
# ADVERSARIAL-REVIEW-CLASS FINDING (self-caught via live verification,
# #289): the shell-command argument was ORIGINALLY inlined directly on
# this bind-key line (a `CH_OUT=$(...); CH_RC=$?; if [ "$CH_RC" -ne 0 ]
# ...` one-liner) -- and it silently produced `if [ "" -ne 0 ]` at
# runtime (confirmed via `list-keys` on the ACTUAL bound command, and via
# a real S-F1 keypress through a genuinely attached pty client reading
# the raw popup overlay bytes -- `capture-pane` does NOT see a popup's
# content at all, since it is a client-side rendering overlay, never part
# of any pane's own buffer). Root cause: tmux's OWN conf-file DOUBLE-QUOTE
# parser EXPANDS `$VAR` at CONF-PARSE/bind time (using tmux's own process
# environment), not at shell-run time -- `$CH_OUT`/`$CH_RC`/`$?` don't
# exist in THAT environment, so they were silently blanked to empty
# string before the shell that eventually ran the command ever saw them.
# Single-quoted tmux strings do NOT expand `$VAR` (also verified live),
# but tmux's own single-quote parsing supports no escapes at all, so
# embedding this command's own several `printf '%s...'` single quotes
# would need the POSIX quote-splice idiom on every one of them (tmux DOES
# honour `'...'\''...'`, verified live) -- fragile, easy to get wrong, and
# exactly the class of hand-spliced-quoting bug this repo's own playbook
# already flags. Moving the logic into its OWN script file, invoked by
# absolute path, sidesteps the whole landmine: the only thing the
# bind-key line needs to resolve is the path itself, which needs no `$VAR`
# to survive tmux's conf parser at all. See CLAUDE_HISTORY_POPUP_SCRIPT_DEST.
#
# FAIL LOUDLY, NEVER SILENTLY: claude-history exits nonzero with a clear
# stderr message when no transcript exists for the resolved cwd (#267's
# own behavior, unchanged). A bare `claude-history | less` would then
# hand `less` empty stdin, which can close instantly with nothing to
# read -- the exact "no silent instant-close" failure this ticket's own
# acceptance forbids. The popup script captures claude-history's output +
# exit code explicitly and, on failure, prints the error and waits for a
# keypress instead of piping into `less` at all.
#
# LIVE-APPLY SAFETY: a `bind-key` call is a pure key-table registration
# (see the #267 comment above `TMUX_SCROLLBACK_KEYBINDS` for the full
# argument -- no window geometry read or written, nothing already
# rendered by the CC TUI touched) -- identical safety class to the
# S-PageUp/PageDown binds, so the popup bind is live-applied the same
# way, never conf-only.
#
# #376 CLEANUP: `S-F1` (root-table, never confirmed to reach the user's
# actual terminal/ssh client -- #294's own Windows-notebook report) and
# `S-DC` (root-table, confirmed delivered but explicitly downgraded by
# the user's own binding correction: "garantovaná skratka = výhradne
# prefix trieda; žiadna nová skratka bez potvrdeného doručenia") are
# REMOVED. `prefix+h` below is the ONE surviving binding -- the only one
# the user personally confirmed opens -- and this popup is now a FALLBACK
# for cross-session/closed-pane history only, not the primary answer
# (fullscreen rendering is -- see apply_managed_settings_defaults). The
# `#294`/`#337` research that picked S-F1/S-DC (candidate keys
# considered, Windows-client encoding evidence) is preserved in this
# ticket's own history/playbook, not repeated here since neither key
# survives to be re-derived from it.
TMUX_POPUP_PREFIX_KEY = "h"


def _tmux_popup_bind_argv(key, in_prefix_table):
    """The `bind-key ... display-popup ...` argv for `key` -- `-n` (root
    table, no prefix) when `in_prefix_table` is False, omitted (default
    "prefix" table) otherwise. Shared verbatim between the live-apply
    subprocess call (a plain argv list, no shell involved -- each element
    is already exactly one tmux token) and the rendered conf line (which
    needs REAL quoting, see `_tmux_conf_quote` -- unlike
    TMUX_SCROLLBACK_KEYBINDS, none of THESE tokens contain spaces, but
    `#{pane_current_path}` contains a literal `#`, which would start a
    tmux COMMENT if left unquoted at the start of a conf line -- the
    quoting here is load-bearing for THAT character, not for whitespace).
    The invoked command is the POPUP SCRIPT's own ABSOLUTE PATH (baked in
    at Python render time -- see the module comment above
    TMUX_POPUP_PREFIX_KEY for why this, not an inline shell command, is
    the safe shape). #376 dropped the `mode` parameter this function used
    to take (#337) -- the popup script is unconditional now, so there is
    nothing left to select between and no `-e AIRULESET_POPUP_MODE=`
    flag to build."""
    argv = ["bind-key"]
    if not in_prefix_table:
        argv.append("-n")
    argv += [key, "display-popup", "-E", "-w", "96%", "-h", "96%"]
    argv += ["-d", "#{pane_current_path}", "-T", "claude-history",
             str(CLAUDE_HISTORY_POPUP_SCRIPT_DEST)]
    return argv


TMUX_POPUP_BIND_ARGVS = [
    _tmux_popup_bind_argv(TMUX_POPUP_PREFIX_KEY, in_prefix_table=True),
]


# --------------------------------------------------------------------------- #
# #649 (course change over #613 REOPEN-2 round-2): prefix+w MULTI-SESSION TREE,
# via the NATIVE tmux `choose-tree -ZwG`.
#
# ROOT CAUSE (measured, issue #649, 2026-08-24): the owner's Ctrl+B w blackout is
# UPSTREAM tmux bug #5180/#5493 -- a stale-pointer in `window_tree_build`'s
# SQUASH-GROUPS filter (which by default collapses a session group to its first
# member). It blackens `choose-tree` (the default prefix+w) for a client of an
# OLDER grouped session once a newer grouped sibling exists -- exactly the
# owner's topology (his launcher forms a newer grouped sibling on every attach,
# so his webterm on the base always loses the chooser). Fixed only on tmux master
# commit a6a06c5aa6, UNRELEASED in every 3.7.x incl. 3.7c (2026-08-17); the fleet
# runs 3.7b.
#
# FIX: bind prefix+w to `choose-tree -ZwG`. The `-G` flag DISABLES the buggy
# squash filter (renders every group member separately), sidestepping the
# blackout -- and it gives the owner (#649) the multi-session TREE the native
# chooser shows (sessions -> their windows), which the #613-r2 custom
# `display-menu` picker had LOST (it only ever listed the CURRENT session's
# windows). That custom picker (WINDOW_MENU_SCRIPT_*, a deployed helper script)
# is REMOVED (mvp-philosophy: the native chooser replaces ~90 lines of custom
# bash + its deploy). Verified live on an isolated -S tmux 3.7b socket (dead
# topology: a client on the OLDER grouped base + a newer grouped sibling + an
# independent 2nd session): default `choose-tree -Zw` = 366 chars, `sort:`
# absent (blackout); `choose-tree -ZwG` = 4916 chars, `sort:` + the 2nd session
# name present (full cross-session tree, Down+Enter navigates). `-Z` zooms the
# pane, `-w` starts in the window list; flag order is irrelevant.
#
# RE-CHECK (2026-08-24): when the fleet upgrades to a tmux carrying the upstream
# fix (master a6a06c5aa6, unreleased as of 3.7c), the blackout is gone anyway --
# but `-ZwG` STAYS desirable (it IS the multi-session view: all group members
# shown separately), so the flag can remain. This dated note is the trigger --
# no separate ticket.
#
# LIVE-APPLY SAFETY: a `bind-key` is a pure key-table registration -- none of
# window-size's live-apply hazard -- so it is live-applied the same way (a
# running tmux never re-reads the conf), grouped with the scrollback/popup binds.
TMUX_CHOOSE_TREE_BIND_ARGVS = [
    ["bind-key", "w", "choose-tree", "-ZwG"],
]


def _tmux_conf_quote(word):
    """Quote a single conf-line WORD (argv element) for tmux's OWN config
    parser. `#{...}` format expansion works the same way quoted or bare.
    ADVERSARIAL-REVIEW FINDING (#289, M1): a literal `$VAR` is EXPANDED by
    tmux's OWN conf-parser at conf-parse/bind time -- using tmux's OWN
    process environment, NOT the shell's -- both INSIDE a tmux double-
    quoted string AND when left bare/unquoted (verified live; this is the
    exact landmine the module comment above TMUX_POPUP_PREFIX_KEY documents this
    ticket self-finding and fixing by moving shell logic into its own
    script file). No quoting form in THIS function protects a literal `$`
    from that expansion, so a word containing one is REFUSED outright
    rather than silently mis-rendered -- a future conf-line author needing
    a real shell-runtime variable must move it into a separate script file
    invoked by absolute path instead (see CLAUDE_HISTORY_POPUP_SCRIPT_DEST).
    For every other case this only escapes what tmux itself needs escaped
    (`\\` and `"`); single quotes need no escaping inside a tmux double-
    quoted string, but DO need quoting when they appear in an otherwise-
    bare word (an unquoted `'` starts real single-quote mode in tmux's own
    grammar too, per M2)."""
    if "$" in word:
        raise ValueError(
            "_tmux_conf_quote: refusing to render literal '$' in %r -- "
            "tmux's own conf-parser expands $VAR at conf-parse/bind time "
            "(both quoted and unquoted, verified live) and no quoting form "
            "here protects a literal '$' from that. Move logic needing a "
            "real shell-runtime variable into its own script file, invoked "
            "by absolute path, instead." % (word,)
        )
    if word and not re.search(r'[\s"\\;#\']', word):
        return word
    escaped = word.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def render_tmux_history_block(limit=TMUX_HISTORY_LIMIT,
                               default_size=TMUX_DEFAULT_SIZE,
                               window_size_manual=False):
    # #338: per-token _tmux_conf_quote (not a bare " ".join) -- required
    # the moment the S-PageUp entry's `"send-keys C-o"`/`"copy-mode -eu"`
    # nested-command tokens (each ONE tmux argv element, containing a
    # space) need to survive as single words in the rendered conf line.
    # No-op for the two S-PageDown entries (no token there needs quoting).
    keybind_lines = "\n".join(
        " ".join(_tmux_conf_quote(tok) for tok in argv)
        for argv in TMUX_SCROLLBACK_KEYBINDS)
    popup_lines = "\n".join(
        " ".join(_tmux_conf_quote(tok) for tok in argv)
        for argv in TMUX_POPUP_BIND_ARGVS)
    # #649: bind prefix+w to the native `choose-tree -ZwG`, grouped with the
    # other binds (after the popup bind). `-ZwG` is flag-only, no `$`, so
    # `_tmux_conf_quote` renders `bind-key w choose-tree -ZwG` cleanly. See the
    # TMUX_CHOOSE_TREE_BIND_ARGVS module comment above.
    choose_tree_lines = "\n".join(
        " ".join(_tmux_conf_quote(tok) for tok in argv)
        for argv in TMUX_CHOOSE_TREE_BIND_ARGVS)
    # #586/#613 REOPEN-2: `window-size manual` pins the fixed geometry (the
    # owner's invariant -- no client resizes another's window) -- but only when
    # the caller has confirmed the reading tmux is a safe version (>= 3.5); a 3.4
    # box would crash at conf-parse startup with it (#241). Emitted right before
    # `default-size` so the two size options sit together. CONF-ONLY.
    window_size_line = (
        f"set-option -g window-size {TMUX_WINDOW_SIZE}\n" if window_size_manual else "")
    # #591: NO `destroy-unattached` line -- the global option is removed; the
    # base session must inherit tmux's default `off` (see the module comment
    # above), and the webterm clone self-cleans per-session instead.
    # #646: `mouse on` is the managed FLEET-WIDE default -- the owner wants the
    # scroll-wheel to reach tmux's copy-mode scrollback over ssh (`kolieskom cez
    # ssh sa to blbo pouziva`; #267 built the Shift+PgUp keybind for the SAME
    # complaint, this adds the wheel). The `#`-comment records the ONE caveat
    # (native text selection then needs Shift+drag) right by the line, per the
    # ticket. UNLIKE window-size this is a plain server/session option toggle --
    # it also carries a live-apply (below), safe for the same reason as
    # history-limit / the bind-keys.
    return (
        f"{TMUX_MARK_START}\n"
        f"set-option -g history-limit {limit}\n"
        f"{window_size_line}"
        f"set-option -g default-size {default_size}\n"
        "# #646: mouse on -- scroll-wheel reaches tmux's scrollback over ssh. Native\n"
        "# text selection then needs Shift+drag (a plain drag goes to tmux copy-mode).\n"
        "set-option -g mouse on\n"
        f"{keybind_lines}\n"
        f"{popup_lines}\n"
        f"{choose_tree_lines}\n"
        f"{TMUX_MARK_END}"
    )


def _clean_tmux_block_spans(existing, start=TMUX_MARK_START, end=TMUX_MARK_END):
    """[(start, end)] for every CLEAN (non-crossing) START...END marker
    pair in `existing`, left to right. "Clean" means no OTHER marker
    literal (START or END) falls strictly between a pair's own START and
    its END -- this deliberately refuses to treat an externally-corrupted
    or reordered marker set (e.g. END appearing before START) as a
    replaceable block.

    `start`/`end` default to the managed history-block markers (the #235
    caller), but are parameterized so the #554 stream-window block reuses
    the SAME proven scanner rather than a third hardcoded copy (F1
    review): passing its own `# >>> airuleset tmux stream-window >>>` /
    `<<<` markers is safe because the two marker sets are mutually
    non-substring, so each call only ever matches its own block.

    Why this matters (#235 adversarial-review finding): a naive whole-file
    `START.*?END` regex would, on a LATER run once a fresh clean block has
    been appended after a stray/orphaned marker, span from the stray
    marker all the way to the fresh block's END -- silently deleting every
    real tmux directive sitting in between. This left-to-right, position-
    tracking scan can never produce that outcome, at any point across any
    number of runs: an unpaired or crossed marker is simply skipped over
    and left as inert literal text, never merged with anything else."""
    spans = []
    pos = 0
    s_len = len(start)
    while True:
        s = existing.find(start, pos)
        if s == -1:
            break
        e = existing.find(end, s + s_len)
        if e == -1:
            pos = s + s_len  # no END anywhere after this START -- skip it
            continue
        inner = existing[s + s_len:e]
        if start in inner or end in inner:
            pos = s + s_len  # another marker crosses this pair -- not clean
            continue
        e_full = e + len(end)
        spans.append((s, e_full))
        pos = e_full
    return spans


def _default_tmux_run(argv):
    import subprocess
    return subprocess.run(argv, capture_output=True, text=True, timeout=8)


def _default_ps_run(argv):
    """#660: the real `ps` runner for the kill-sweep's no-suspended-child guard
    (review 🟡4). Separate injection point from `_default_tmux_run` so a test
    can fake tmux and ps independently; identical bounded-subprocess shape."""
    import subprocess
    return subprocess.run(argv, capture_output=True, text=True, timeout=8)


def _parse_tmux_version(text):
    """`(major, minor)` from a `tmux -V` line like `tmux 3.7b` / `tmux 3.4` /
    `tmux next-3.8`, or None if unparseable/empty. Any trailing letter (the
    `b` in `3.7b`) is ignored -- only the numeric major.minor decides the
    version-gate. #586."""
    if not text:
        return None
    m = re.search(r"(\d+)\.(\d+)", str(text))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def _tmux_supports_window_size_manual(run):
    """True iff the PATH `tmux` that will READ the managed conf at its next
    server start is a version where `set-option -g window-size manual` is SAFE
    at conf-parse time. #586: reproduced live -- tmux 3.4 CRASHES at startup
    with the line (`server exited unexpectedly`), tmux 3.7b starts cleanly.
    `run(["tmux", "-V"])` uses the SAME injectable runner the live-apply calls
    use (so tests never touch a real tmux server); the version is read from the
    result's `.stdout`. Fails CLOSED -- any exception (tmux missing), a None
    result (a `run` that never answers `-V`, e.g. the dead-socket default), or
    an unparseable line all yield False, so a box whose version cannot be read
    NEVER receives the crashing line. `run` is required (the caller passes
    `run or _default_tmux_run`)."""
    try:
        result = run(["tmux", "-V"])
    except Exception:
        return False
    ver = _parse_tmux_version(getattr(result, "stdout", None))
    return ver is not None and ver >= _MIN_WINDOW_SIZE_MANUAL_VERSION


def converge_tmux_window_geometry(run=None, default_size=TMUX_DEFAULT_SIZE,
                                   supports_manual=None):
    """#685: LIVE-converge a RUNNING tmux server to the owner's fixed-size
    invariant (#672 / #613 REOPEN-2): set `window-size manual` +
    `default-size 176x50` when they differ, and resize every window not
    already at 176x50 (dedup by window id -- grouped sessions list a shared
    window once per session). A tmux server reads the conf ONLY at start and
    agentic fleet boxes never restart tmux, so the conf-only pin never
    reached a server started before it -- live dev2 (2026-08-25):
    `window-size latest`, codex-bridge at 305x56 from David's 305x57 client,
    the owner's 176x51 `-f ignore-size` webterm client cropping the bottom
    rows (the invisible CC footer).

    SAFETY -- why this does not resurrect the #236/#241 hazards: it is
    VERSION-GATED on the same probe as the conf line
    (`_tmux_supports_window_size_manual`, >= 3.5, fails CLOSED), so the #241
    tmux-3.4 conf-parse crash and the dev1 3.4 format-expansion segfault
    classes can never be reached; the #236 "snap-resize" is no longer a
    hazard but the owner-ruled DESIRED end state, and the exact live
    sequence was proven safe on tmux 3.7b (supervisor's montalu1-6@subdev +
    dev1 applies; this ticket's dev2/fleet convergence) -- sessions
    untouched, NO server restart/kill, NEVER a keystroke into any pane.
    This function is the ONE sanctioned site for the raw per-window
    geometry subcommands (TestTmuxWindowSizeNoResize counts the literals).

    Strictly IDEMPOTENT: reads the server state first and mutates only what
    differs; a converged server gets read-only calls and an empty return; a
    dead socket / unreadable state returns [] (the conf written by
    apply_tmux_history_limit covers the next server start). Every call is
    independently guarded -- a failure is logged, never raised. Returns the
    list of MUTATING argvs actually applied (the caller's log line).

    `supports_manual=None` self-probes via `run`; `apply_tmux_history_limit`
    passes its own already-probed bool so `tmux -V` stays a single call."""
    runner = run or _default_tmux_run
    if supports_manual is None:
        supports_manual = _tmux_supports_window_size_manual(runner)
    if not supports_manual:
        return []
    applied = []

    def _read(argv):
        try:
            result = runner(argv)
        except Exception:
            return None
        if result is None or getattr(result, "returncode", 1):
            return None
        return getattr(result, "stdout", None) or ""

    def _mutate(argv):
        try:
            result = runner(argv)
        except Exception as e:
            print(f"  tmux geometry converge skipped (non-fatal): {e}",
                  file=sys.stderr)
            return
        if result is not None and getattr(result, "returncode", 0):
            stderr = (getattr(result, "stderr", "") or "").strip()
            print(f"  tmux geometry converge skipped (rc): "
                  f"{stderr or 'no server running?'}", file=sys.stderr)
            return
        applied.append(argv)

    state = _read(["tmux", "show-options", "-g", "window-size"])
    if state is None:
        return []  # no server running -- the conf covers the next start
    # (deliberately asymmetric from here on: a LATER failed read only skips
    # its own step / partially returns -- the next install/push retries)
    if state.strip() != f"window-size {TMUX_WINDOW_SIZE}":
        _mutate(["tmux", "set-option", "-g", "window-size",
                 TMUX_WINDOW_SIZE])
    cur_default = _read(["tmux", "show-options", "-g", "default-size"])
    if (cur_default is not None
            and cur_default.strip() != f"default-size {default_size}"):
        _mutate(["tmux", "set-option", "-g", "default-size", default_size])
    try:
        cols, rows = (int(v) for v in default_size.split("x"))
    except ValueError:
        return applied
    listing = _read(["tmux", "list-windows", "-a", "-F",
                     "#{window_id} #{window_width} #{window_height}"])
    if listing is None:
        return applied
    seen = set()
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        wid, w, h = parts
        if wid in seen:
            continue
        seen.add(wid)
        try:
            w, h = int(w), int(h)
        except ValueError:
            continue
        if (w, h) != (cols, rows):
            _mutate(["tmux", "resize-window", "-t", wid,
                     "-x", str(cols), "-y", str(rows)])
    return applied


def apply_tmux_history_limit(tmux_conf_path: Path = None, limit: int = TMUX_HISTORY_LIMIT,
                              default_size: str = TMUX_DEFAULT_SIZE,
                              run=None) -> bool:
    """Ensure `~/.tmux.conf` carries the managed tmux block: history-limit
    (#235), default-size (#236), `mouse on` (#646 -- the fleet-wide
    scroll-wheel-into-scrollback default, ALSO live-applied), AND `window-size
    manual` (#586, restored by
    #613 REOPEN-2 -- the owner's fixed-size invariant, version-gated so a tmux
    3.4 box that would crash at conf-parse never receives the line) -- and UNSETS
    any stale global destroy-unattached (#591 self-heal; the conf no longer
    carries a destroy-unattached line at all) AND aggressive-resize (#613 self-
    heal -- #584's connect live-set `aggressive-resize on` globally, the Ctrl+B W
    blackening; the conf never carried it) on a running server. window-size
    lands in the conf (version-gated) AND -- since #685 -- is LIVE-converged on
    a running >= 3.5 server via `converge_tmux_window_geometry`, called at the
    end with the same probed bool (so `tmux -V` stays a single call): a
    conf-only pin never reaches a server started before it, and the webterm
    clone's `-f ignore-size` bridge only stops the webterm from SHRINKING a
    window -- it cannot fix one already LARGER than the owner's viewport (dev2
    codex-bridge 305x56 -> the CC footer cropped below the owner's 176x51
    client, #685). The first reopen's live `-gu window-size` (forcing a running
    server to `latest`) stays REMOVED; a 3.4/unprobeable box gets NO live
    geometry call at all (fails closed -- the #241 crash / dev1 segfault
    classes stay impossible). The `tmux -V` version probe
    (`_tmux_supports_window_size_manual`) decides the conf content AND gates
    the live convergence. See the module-level comment above
    `render_tmux_history_block` for the full incident history.

    Idempotent marker block: create the file if absent, rewrite ONLY the
    block's CONTENT in place if a clean pair of markers already exists
    (never touches anything outside them, byte-for-byte -- see
    `_clean_tmux_block_spans`), no-op on a second run with nothing
    changed. Returns True iff the conf file's bytes changed.

    Also live-applies history-limit on any RUNNING tmux server for this
    user (`tmux set-option -g history-limit N`), exactly #235's original,
    already-shipped, already-proven-safe scope: an already-running
    session's NEW panes/windows pick it up immediately, without waiting
    for the next server start; EXISTING panes keep their creation-time
    limit (tmux has no way to grow an existing pane's history buffer in
    place). This is a server OPTION set, never a keystroke into any pane.

    #646: `mouse on` is live-applied the SAME way (`set-option -g mouse on`),
    for the SAME reason -- a running tmux never re-reads the conf, so a running
    session picks up the wheel-scroll into scrollback immediately at
    install/push without a restart. Same safety class as history-limit (a plain
    option toggle -- no geometry recalc, no screen redraw), proven live on
    dev2 by the supervisor. SET, never UNSET: the fleet default is `on`, a
    permanent latch is DESIRED (unlike #615's temporary per-session webterm
    flip), so there is no revert path.

    #591: the global destroy-unattached is UNSET (`set-option -gu`) right
    after history-limit -- not SET to any value. #254 introduced a global
    `keep-last`; that protects the group's LAST member, not the BASE, so
    once a webterm clone forms a group the owner detaching from the base
    destroyed it (with the Claude process inside) and the server later died
    (the gk 2026-08-20 total-death). The conf now carries no line at all
    (fresh servers -> tmux default `off`), and this live-apply self-heals
    any RUNNING server still carrying the stale base-killing `keep-last` by
    reverting it to `off` on the next push -- verified live: `-gu` on a
    keep-last server -> off, idempotent. It only ever evaluates ZERO-client
    sessions (the #235-vs-#254 safety) and unsetting to `off` can never
    destroy anything. The webterm clone self-cleans PER-SESSION instead
    (cli_webterm's `client-attached` hook), so the base is never swept.

    default-size is never in the STATIC `live_argvs` list below; it lands
    in the conf file above (next server/session/window), and -- since #685
    -- the gated `converge_tmux_window_geometry` step additionally live-sets
    it (idempotently, only when it differs) on a running >= 3.5 server, as
    part of the same convergence that fixes each window's live size. On a
    gate-CLOSED (3.4/unprobeable) box it stays conf-only exactly as before.
    See TestTmuxWindowSizeRemoved for the fail-closed lock, and
    TestTmuxWindowSizeNoResize for the only-sanctioned-site lock on the raw
    per-window geometry subcommands.

    #267: the three `TMUX_SCROLLBACK_KEYBINDS` (Shift+PgUp/PgDn) are ALSO
    live-applied, unlike default-size -- a `bind-key` call only registers
    a key-table entry, so it carries none of window-size's live-apply
    hazard (see the module comment above `render_tmux_history_block`).
    Each keybind is attempted independently of the others and of the
    history-limit call above: a failure/nonzero-exit on one never skips
    the rest, so a session that has already reached a running server
    picks up the keyboard scrollback shortcut immediately, with no
    restart and no keystroke sent to any pane.

    #289: the `TMUX_POPUP_BIND_ARGVS` (prefix-h, the one surviving popup
    fallback binding as of #376 -- see the module comment above
    `TMUX_POPUP_PREFIX_KEY`) is live-applied the SAME way, for the SAME
    reason -- a `bind-key` call is a pure key-table registration,
    independent of and no riskier than the scrollback keybinds it sits
    alongside.

    `run` defaults to a real `tmux` invocation and is injectable so tests
    never touch a real tmux server. A missing server / a nonzero exit
    (which `subprocess.run` does NOT raise on without `check=True` -- a
    real `tmux set-option` against a dead socket exits 1 silently) / any
    other failure is logged and ignored, never raised, never affecting the
    conf-file write result above -- mirroring the ticket's own "ignore
    failure when no server" acceptance."""
    path = tmux_conf_path or TMUX_CONF
    runner = run or _default_tmux_run
    # #586/#613 REOPEN-2/#685: probe the PATH tmux version ONCE (via the same
    # injectable runner). The result decides BOTH whether the conf may carry
    # `window-size manual` AND whether the #685 live convergence at the end may
    # run. Fails closed: an unprobeable / <3.5 box gets no window-size line
    # (never the #241 3.4 conf-parse crash) and no live geometry call.
    window_size_manual = _tmux_supports_window_size_manual(runner)
    block = render_tmux_history_block(limit, default_size,
                                       window_size_manual=window_size_manual)

    existing = path.read_text() if path.exists() else ""
    spans = _clean_tmux_block_spans(existing)
    if spans:
        out, cursor = [], 0
        for s, e in spans:
            out.append(existing[cursor:s])
            out.append(block)
            cursor = e
        out.append(existing[cursor:])
        new = "".join(out)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{block}\n"
    changed = new != existing
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)

    # `runner` was already resolved above.
    live_argvs = [
        ["tmux", "set-option", "-g", "history-limit", str(limit)],
        # #591: UNSET (not set) the global destroy-unattached on any running
        # server -- a #254-style self-heal that reverts a box still carrying the
        # base-killing `keep-last` (live-applied before this fix, or read from a
        # not-yet-rewritten conf) back to tmux's default `off`. Verified live on
        # tmux 3.7b: `-gu` on a keep-last server -> off, idempotent (a no-op when
        # already unset). Setting `off` never destroys anything, so this is safe
        # to apply against a running server, same #235-vs-#254 reason as before.
        ["tmux", "set-option", "-gu", "destroy-unattached"],
        # #613: UNSET the stale global aggressive-resize on any running server --
        # the SIBLING self-heal to destroy-unattached above. #584's connect
        # live-set `-gw aggressive-resize on` GLOBALLY (persists for the server's
        # life) and #586 added no live-revert, so a long-running owner server
        # still carries it -- a stale non-default global worth cleaning up
        # regardless. `-gwu` (window-option unset) reverts it to tmux's default
        # `off`. Verified live on tmux 3.7b: `-gwu`/`-gu` both revert on->off,
        # idempotent, server unharmed. Unlike window-size, aggressive-resize is a
        # plain window OPTION affecting only FUTURE resize computation, so it is
        # safe to fire UNCONDITIONALLY here -- while window-size touches live
        # geometry and is live-set only via the version-gated, state-read-first
        # #685 convergence after this loop (never unconditionally in this list).
        ["tmux", "set-option", "-gwu", "aggressive-resize"],
        # #646: SET the managed fleet-wide default `mouse on` on any running
        # server, so an already-running session picks up wheel-scroll into
        # tmux's scrollback immediately at install/push -- never a restart (a
        # running tmux never re-reads the conf; the conf line above only takes
        # effect at the next server start). SAFE to live-apply, same class as
        # history-limit above and the bind-keys below and UNLIKE window-size: a
        # plain server/session option toggle -- it does not touch any window's
        # geometry, force a recalculate_sizes() pass, or read/write anything CC's
        # renderer has drawn. Proven live: the supervisor ran exactly
        # `tmux set-option -g mouse on` on dev2's real server (read-back `on`)
        # with no disruption. SET (never UNSET/`off`) -- the fleet default is on;
        # a permanent latch is the DESIRED state, so no revert path (unlike
        # #615's temporary per-session webterm clone flip). It sits AFTER the two
        # self-heal UNSETs so calls[1..3] (history-limit + the two self-heals)
        # keep their positions; mouse is calls[4].
        ["tmux", "set-option", "-g", "mouse", "on"],
        # #613 REOPEN-2 / #685: window-size is never in THIS static list. The
        # first reopen live-UNSET it (`set-option -gu window-size`) to force a
        # running server to `latest`; that is the OPPOSITE of the owner's
        # restored fixed-size invariant, so it stays REMOVED. The conf line is
        # version-gated above, and #685's `converge_tmux_window_geometry` --
        # invoked AFTER this loop with the same probed bool -- is the ONE
        # sanctioned live path for window-size: it SETS `manual` (never
        # `latest`) and converges each window's live size, idempotently,
        # state-read-first, on >= 3.5 only. It lives outside this list because
        # its calls are CONDITIONAL on the server's current state, while every
        # entry here fires unconditionally.
    ]
    live_argvs += [["tmux"] + argv for argv in TMUX_SCROLLBACK_KEYBINDS]
    live_argvs += [["tmux"] + argv for argv in TMUX_POPUP_BIND_ARGVS]
    # #649: live-apply the prefix+w `choose-tree -ZwG` bind too -- a running
    # tmux never re-reads the conf, so an already-running session picks up the
    # native multi-session tree (the -G flag dodging the upstream #5180/#5493
    # grouped-session choose-tree blackout) immediately at install/push. It also
    # OVERWRITES a box still carrying the old #613-r2 run-shell menu bind on `w`
    # (bind-key replaces the existing binding). A pure key-table registration,
    # same safety class as the scrollback/popup binds above.
    live_argvs += [["tmux"] + argv for argv in TMUX_CHOOSE_TREE_BIND_ARGVS]
    # #376 CLEANUP: an ALREADY-RUNNING server that was live-bound before
    # this fix deployed still has S-F1/S-DC registered -- rewriting the
    # CONF file (above) does not retroactively unbind an already-live
    # key-table entry, and `live_argvs` above only ever ADDS bindings, it
    # never removes stale ones. `unbind-key` on a key that was never
    # bound is a documented tmux no-op (rc 0), so this is safe to run
    # unconditionally on every box, whether or not it ever had them.
    live_argvs += [["tmux", "unbind-key", "-n", "S-F1"],
                   ["tmux", "unbind-key", "-n", "S-DC"]]
    for argv in live_argvs:
        try:
            result = runner(argv)
            rc = getattr(result, "returncode", 0)
            if rc:
                stderr = (getattr(result, "stderr", "") or "").strip()
                print(f"  tmux live-apply skipped (rc={rc}): "
                      f"{stderr or 'no server running?'}", file=sys.stderr)
        except Exception as e:
            # No server running / tmux missing from PATH / timeout, etc. --
            # expected and harmless (a new server reads the conf file we
            # just wrote anyway); logged for visibility, never re-raised,
            # and never affects the conf-file write result above. Each
            # call is independently guarded so one failure never skips
            # the rest (#267).
            print(f"  tmux live-apply skipped (non-fatal): {e}", file=sys.stderr)

    # #685: LIVE-converge the running server's window geometry (version-gated,
    # idempotent, state-read-first -- see converge_tmux_window_geometry's
    # docstring). The probed bool is passed through so `tmux -V` stays a
    # single call; on a gate-CLOSED or server-less box this is a no-op.
    converged = converge_tmux_window_geometry(
        run=runner, default_size=default_size,
        supports_manual=window_size_manual)
    if converged:
        print(f"  tmux geometry converged live: {len(converged)} change(s) "
              f"(window-size {TMUX_WINDOW_SIZE} + windows -> {default_size},"
              " #685)")

    return changed


# ---------------------------------------------------------------------------
# #554/#592: the tmux WINDOW name carries the box's short TARGET ALIAS, so the
# owner -- attached to one of many fleet sessions -- can tell at a glance WHERE
# they are. Root cause (verified live on montalu@subdev, tmux 3.7b): the default
# status-left already shows the SESSION name (`[#{session_name}]`) and the owner
# STILL could not tell -- the identity has to go where the owner actually looks,
# the window-status list in the middle of the status bar, which
# `automatic-rename on` keeps filling with the running command (`bash`/`node`).
# So: name the window after the box alias and turn `automatic-rename` off so it
# STICKS.
#
# #592 widened #554 from subdev stream accounts to also cover the gk box (owner
# report 2026-08-20: gk's window showed `bash`). #593 SCOPES that back: window
# naming renders ONLY on SINGLE-SESSION-per-account boxes -- subdev streams +
# the gk `gatekeeper` account, i.e. exactly `is_single_session_box_user`, the
# SAME set the #264 ssh-auto-attach uses. It must NEVER render on an owner/
# newlevel MULTI-PROJECT box (dev1/dev2): those run many project sessions with
# per-command window names, and one fixed name + `automatic-rename off` froze
# every window to `dev1`, destroying navigation (the #592 regression, owner
# 2026-08-20). The name = the box's `cli_aliases.short_target_alias`
# (gatekeeper->gk, montaluN->mN, davidN->dN, ...) -- the SAME single source the
# webterm dashboard tabs draw on (cli_webterm._short_alias), never a parallel
# map. The "stream" in the marker/function names below is historical (#554).
# The block is stripped when a box is not single-session OR yields no SAFE
# alias; for a mis-provisioned owner box the strip also live-reverts the running
# server (`_live_revert_stream_window_name`). Same idempotent per-box
# marker-block shape as apply_stream_ssh_attach (#264).
#
# The alias is BAKED at install time from `_current_user()` + hostname -- 100%
# predictable, testable, and (unlike `#{session_name}`) constant across the ssh
# grouped-attach survivor path (`new-session -t`), so the session-created hook
# always renames the shared window to the SAME literal.
# ---------------------------------------------------------------------------

STREAM_TMUX_WINDOW_MARK_START = "# >>> airuleset tmux stream-window >>>"
STREAM_TMUX_WINDOW_MARK_END = "# <<< airuleset tmux stream-window <<<"

# The alias is interpolated as a LITERAL into a tmux `rename-window` argument --
# constrain it to a shell/tmux-safe unix-name shape so an exotic hostname/user
# can never inject tmux command syntax. Every real alias (dev1/dev2/gk/mN/dN/
# miva/siN + the marek owner account) already matches this.
_SAFE_STREAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def render_stream_tmux_window_block(name):
    """The managed ~/.tmux.conf block that names this box's tmux windows after
    `name` -- the box's short TARGET ALIAS (#592; already validated against
    `_SAFE_STREAM_NAME_RE` by the caller). #554 baked the subdev username here;
    #592 bakes the alias (e.g. `m2`/`gk`), but the render itself is
    name-agnostic -- it bakes whatever literal it is handed. #593: the CALLER
    only renders this for a SINGLE-SESSION-per-account box (gk + subdev
    streams), never an owner/newlevel multi-project box -- the render is not
    where that gate lives.

    `after-new-window` is DELIBERATELY not emitted: it fires ONLY on windows
    opened AFTER the initial one, so it never names the session's FIRST
    (claude) window -- the one the owner sees on attach -- which
    `session-created` does. (An earlier probe saw a tmux "server exited
    unexpectedly" with it, but a fresh-socket re-check showed that was a
    probe socket-reuse race, not the hook: `after-new-window` parses rc=0
    on both 3.4 and 3.7b -- it is simply the WRONG hook for the first
    window.) The `session-created` hook + `automatic-rename off` pair is
    proven clean on BOTH the fleet's tmux 3.4 and 3.7b (a scratch conf
    starts rc=0 with `#{window_name}` = the name on both).

    KNOWN LIMITATION (review F3): `session-created` names only the FIRST
    window of a NEW session. A second window opened later keeps its own name
    (with `automatic-rename off` it does not track the command either).
    Acceptable, and the live-apply below is more thorough, renaming EVERY
    existing window of the primary session on each install."""
    return (
        f"{STREAM_TMUX_WINDOW_MARK_START}\n"
        "# #554/#592: window name = this box's short target alias so the owner\n"
        "# sees WHERE they are (gk/mN/dN/...). automatic-rename off makes it\n"
        "# STICK (a command-tracking 'node'/'bash' name hides the identity).\n"
        "# #593: rendered ONLY on single-session-per-account boxes (gk + subdev\n"
        "# streams), never an owner multi-project box; the alias is the SAME\n"
        "# source the webterm tabs use (cli_aliases.short_target_alias).\n"
        "set-option -gw automatic-rename off\n"
        f'set-hook -g session-created "rename-window {name}"\n'
        f"{STREAM_TMUX_WINDOW_MARK_END}"
    )


def _live_apply_stream_window_name(new_name, run=None):
    """Best-effort live-apply on any RUNNING tmux server for this box, so an
    ALREADY-running/attached session updates on the next push WITHOUT waiting
    for a session re-create. Called ONLY for a single-session-per-account box
    (gk + subdev streams, #593). `new_name` is the box alias to rename every
    window to (#592, e.g. `m2`/`gk`). Purely configuration-path (`set-option` /
    `set-hook` / `rename-window` -- NEVER a `send-keys` keystroke into any
    pane), failure-tolerant (no server -> no-op), and it NEVER creates or
    resurrects a session (the standing 'never touch a session the user
    deliberately stopped' rule): `rename-window` only relabels a window that
    already exists.

    #592-review (B3): renames EVERY window on this user's server (`list-windows
    -a`), NOT just the `=<unix-user>` session -- on gk the owner's real session
    is `zbynek-N` (#562) while `_current_user()` is `gatekeeper`, so a
    `=<unix-user>` target matched nothing and the currently-attached window
    stayed FROZEN at its command name (`bash`, under the global
    `automatic-rename off` set just above) until the next `session-created`.
    Every session on this single-session box IS this account's, so the box alias
    is the right name for all of them; `automatic-rename off` then keeps it
    stuck. (Owner/newlevel multi-project boxes are excluded upstream by #593 --
    they must keep per-command names, so this fn is never called for them.)

    A `rename-window` is safe to live-apply for the same reason
    `destroy-unattached` is (apply_tmux_history_limit): it changes a server
    option / a window's own name label, not any window's geometry, and does
    not read or rewrite anything CC's renderer has drawn."""
    runner = run or _default_tmux_run
    for argv in (["tmux", "set-option", "-gw", "automatic-rename", "off"],
                 ["tmux", "set-hook", "-g", "session-created",
                  "rename-window %s" % new_name]):
        try:
            runner(argv)
        except Exception as e:
            print("  tmux stream-window live-apply skipped (non-fatal): %s" % e,
                  file=sys.stderr)
    # Rename EVERY window on this user's server to the alias so an attached
    # session updates immediately, whatever its name. No server (or the injected
    # test `run` returning None) makes list-windows exit non-zero -> no rename.
    try:
        result = runner(["tmux", "list-windows", "-a", "-F", "#{window_id}"])
    except Exception as e:
        print("  tmux stream-window live-apply (list) skipped (non-fatal): %s" % e,
              file=sys.stderr)
        return
    if getattr(result, "returncode", 1) != 0:
        return
    for wid in (getattr(result, "stdout", "") or "").splitlines():
        wid = wid.strip()
        if not wid:
            continue
        try:
            runner(["tmux", "rename-window", "-t", wid, new_name])
        except Exception:
            # one window's failure never skips the rest
            pass


def _live_revert_stream_window_name(alias, run=None):
    """#593: UNDO the #592 window-naming options on any RUNNING server for a
    box that must NOT carry them -- an owner/newlevel MULTI-PROJECT box (dev1/
    dev2) the pre-#593 code wrongly provisioned. Three config-path steps:

      1. `set-option -gwu automatic-rename` -- reset the GLOBAL window option
         to tmux's default `on` (new windows resume per-command tracking).
      2. `set-hook -gu session-created` -- remove the rename hook (new sessions
         no longer get the fixed name).
      3. For every EXISTING window still frozen at the `<alias>` literal,
         `set-option -wu -t <wid> automatic-rename` -- clear its PER-WINDOW
         override so the owner's already-open project windows resume tracking.

    Step 3 is load-bearing (adversarial review, live-verified on an isolated
    `-L` scratch tmux 3.7b): the bad #592 live-apply renamed every window with
    `rename-window`, which sets a PER-WINDOW `automatic-rename off` override that
    the GLOBAL reset in step 1 does NOT clear -- so WITHOUT step 3 the owner's
    open windows stay frozen at `<alias>` until they are recreated or the server
    restarts (the exact regression symptom). Scoped to windows NAMED the alias
    (the ones #592 clobbered) so a window the owner DELIBERATELY named is left
    untouched.

    Config-path ONLY (`set-option`/`set-hook`, NEVER a `send-keys` keystroke
    into any pane), failure-tolerant (no server -> no-op), and it NEVER creates
    or resurrects a session (the standing 'never touch a session the user
    deliberately stopped' rule). Mirrors `apply_tmux_history_limit`'s #591
    `set-option -gu destroy-unattached` self-heal shape -- idempotent, safe on
    a live server (the reverted defaults never break navigation the way the
    frozen `off` + fixed name did)."""
    runner = run or _default_tmux_run
    for argv in (["tmux", "set-option", "-gwu", "automatic-rename"],
                 ["tmux", "set-hook", "-gu", "session-created"]):
        try:
            runner(argv)
        except Exception as e:
            print("  tmux stream-window live-revert skipped (non-fatal): %s" % e,
                  file=sys.stderr)
    # un-freeze the owner's already-open windows still stuck at the alias literal
    try:
        result = runner(["tmux", "list-windows", "-a", "-F",
                         "#{window_id} #{window_name}"])
    except Exception as e:
        print("  tmux stream-window live-revert (list) skipped (non-fatal): %s"
              % e, file=sys.stderr)
        return
    if getattr(result, "returncode", 1) != 0:
        return
    for line in (getattr(result, "stdout", "") or "").splitlines():
        line = line.strip()
        if not line:
            continue
        wid, _, wname = line.partition(" ")
        if not wid or wname != alias:
            continue
        try:
            runner(["tmux", "set-option", "-wu", "-t", wid, "automatic-rename"])
        except Exception as e:
            print("  tmux window un-freeze skipped for %s (non-fatal): %s"
                  % (wid, e), file=sys.stderr)


def _is_bare_shell(cmd):
    """#660: True iff `cmd` (a tmux `#{pane_current_command}`) is one of the
    bare interactive shells -- an IDLE pane. tmux strips the login-shell
    leading dash from `pane_current_command`, but a defensive `lstrip('-')`
    covers any variant that carries it."""
    return (cmd or "").strip().lstrip("-") in _BARE_SHELL_COMMANDS


def _audit_normalize(audit_dir, action, session, reason):
    """#660: append ONE tab-separated line (`<iso-ts> <action> session=<name>
    <reason>`) recording a kill/skip decision to `<audit_dir>/normalize.log`.
    Best-effort: audit logging NEVER breaks normalization -- an unwritable
    dir is noted on stderr, never allowed to raise into the caller."""
    import datetime
    try:
        d = Path(audit_dir) if audit_dir else TMUX_AUDIT_DIR
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with open(d / "normalize.log", "a") as f:
            f.write("%s\t%s\tsession=%s\t%s\n" % (ts, action, session, reason))
    except Exception as e:
        print("  tmux normalize-audit write skipped (non-fatal): %s" % e,
              file=sys.stderr)


def _pane_has_child(pane_pid, ps_run):
    """#660 (review 🟡4): True iff the shell at `pane_pid` has ANY child
    process -- a SUSPENDED (Ctrl-Z'd claude) or BACKGROUNDED job. tmux's
    `#{pane_current_command}` reports only the FOREGROUND process, so a shell
    with a stopped/background child reads as a bare idle shell; killing it
    SIGHUPs that hidden child (claude/build dies). This is the guard that
    makes 'provably idle' true. FAILS SAFE -> True (skip the kill): a
    non-numeric/empty pid, a ps error, or an unreadable result all read as
    'has work'. `ps --ppid <pid>` exits 1 with empty output when there are NO
    children (the safe-to-kill case); any output means a child exists."""
    p = (pane_pid or "").strip()
    if not p.isdigit():
        return True
    runner = ps_run or _default_ps_run
    try:
        res = runner(["ps", "-o", "pid=", "--ppid", p])
    except Exception:
        return True
    # review 🔵4: a degenerate result (a runner returning None, or an object
    # with no `returncode`) must read as UNREADABLE -> fail safe, so the
    # default is OUTSIDE {0,1} (255), never 1 ("no children, safe to kill").
    rc = getattr(res, "returncode", 255)
    out = (getattr(res, "stdout", "") or "").strip()
    if rc not in (0, 1):
        # ps returns 1 == no matching child (GOOD); anything other than 0/1 is
        # unreadable -> fail safe.
        return True
    return bool(out)


def _maybe_absorb_idle_stray(stray, runner, audit_dir, ps_run=None, home=None):
    """#660: kill a standalone stray IFF it is PROVABLY idle -- unattached AND
    UNGROUPED (its own windows, never a grouped view or a grouped multi-window
    work session like `marek-3`) AND every pane a bare shell whose shell has NO
    child process (no suspended/backgrounded claude or build, review 🟡4) AND
    every pane's cwd is exactly the user's HOME. ANY other state -- attached
    (any pane), grouped, any non-shell pane, any pane with a child, any pane
    cwd'd OUTSIDE $HOME (a real work session -- the second-review residual: a
    stream name like `marek` doubles as a real work session whose stopped-claude
    bare shell must be left alone, feedback_never_touch_stopped_sessions), no
    readable pane data, or a tmux/kill error/non-zero -- is a logged SKIP, never
    a kill. Every observed stray was created by `new-session -A -s <name>` from
    an ssh login whose cwd is $HOME, so `cwd == $HOME` is the throwaway-stray
    signal that distinguishes it from a deliberately-kept work session in a
    project dir. Target `=<name>` (tmux exact-match) so a kill can never
    prefix-match a different session. Config-path ONLY (`kill-session`, never
    `send-keys`)."""
    home_real = os.path.realpath(home or os.path.expanduser("~"))
    try:
        res = runner(["tmux", "list-panes", "-s", "-t", "=" + stray, "-F",
                      "#{session_attached}\t#{session_group}\t"
                      "#{pane_current_command}\t#{pane_pid}\t"
                      "#{pane_current_path}"])
    except Exception as e:
        _audit_normalize(audit_dir, "skip", stray, "list-panes error: %s" % e)
        return
    if getattr(res, "returncode", 1) != 0:
        _audit_normalize(audit_dir, "skip", stray, "list-panes non-zero")
        return
    lines = [ln for ln in (getattr(res, "stdout", "") or "").splitlines()
             if ln.strip()]
    if not lines:
        # a real session always has >=1 pane -- empty output means we cannot
        # confirm it is idle, so we NEVER kill (conservative).
        _audit_normalize(audit_dir, "skip", stray, "no pane data")
        return
    attached_any = False
    grouped = False
    off_home = False
    panes = []  # (cmd, pane_pid)
    for ln in lines:
        parts = ln.split("\t")
        att = parts[0].strip() if len(parts) > 0 else ""
        grp = parts[1].strip() if len(parts) > 1 else ""
        cmd = parts[2].strip() if len(parts) > 2 else ""
        pid = parts[3].strip() if len(parts) > 3 else ""
        cwd = parts[4].strip() if len(parts) > 4 else ""
        if att and att != "0":   # review 🔵5: any pane's attached flag counts
            attached_any = True
        if grp:
            grouped = True
        # a missing/unreadable cwd (no 5th field) fails SAFE -> treated as
        # off-home so an ambiguous pane is never killed.
        if not cwd or os.path.realpath(cwd) != home_real:
            off_home = True
        panes.append((cmd, pid))
    if attached_any:
        _audit_normalize(audit_dir, "skip", stray, "attached")
        return
    if grouped:
        # a grouped session shares a base's windows (a webterm/ssh view) or is a
        # multi-window work session (marek-3) -- never a standalone stray.
        _audit_normalize(audit_dir, "skip", stray, "grouped (shared/work session)")
        return
    non_shell = sorted({c for c, _ in panes if not _is_bare_shell(c)})
    if non_shell:
        _audit_normalize(audit_dir, "skip", stray,
                         "non-shell pane(s): %s" % ",".join(non_shell))
        return
    if off_home:
        # a pane cwd'd outside $HOME is a real work session, not a throwaway
        # stray -- never kill it (protects a stopped-claude stream work session).
        _audit_normalize(audit_dir, "skip", stray, "pane cwd outside $HOME (work session)")
        return
    if any(_pane_has_child(pid, ps_run) for _, pid in panes):
        _audit_normalize(audit_dir, "skip", stray,
                         "pane shell has a child (suspended/background job)")
        return
    try:
        kres = runner(["tmux", "kill-session", "-t", "=" + stray])
    except Exception as e:
        _audit_normalize(audit_dir, "skip", stray, "kill-session error: %s" % e)
        return
    if getattr(kres, "returncode", 1) != 0:   # review 🟡3: never log a false kill
        _audit_normalize(audit_dir, "skip", stray, "kill-session non-zero")
        return
    _audit_normalize(audit_dir, "killed", stray,
                     "idle standalone bare-shell stray (%d pane(s))" % len(panes))


def _fleet_stream_stems():
    """#660: the family STEMS of the fleet's subdev stream accounts (marek,
    david, montalu, simap, miva, ...). On an OWNER (multi-project) box a
    session named after one of these, STANDALONE + idle, is a STRAY -- the
    streams work on the subdev box, never as a loose owner-box session (the
    dev2 `marek`/`marek-12` incident, #660 fleet-wide). Derived from the SAME
    registry the rest of the fleet uses (AUTHORITY_BY_USER + STREAM_RENAME_
    ALIASES old names), never a parallel hardcoded list; a stem strips any
    trailing digits (montalu2 -> montalu). Empty on any import failure
    (fail-safe: no widening -- the `<owner>-N` sweep still runs)."""
    import re as _re
    try:
        import airuleset
        names = set(airuleset.AUTHORITY_BY_USER)
        names |= set(getattr(airuleset, "STREAM_RENAME_ALIASES", {}).keys())
    except Exception:
        return set()
    stems = set()
    for n in names:
        m = _re.match(r"^([a-z]+?)\d*$", n)
        if m:
            stems.add(m.group(1))
    return stems


def _owner_box_stray_name_res(owner, single_session):
    """#660: the compiled name patterns the kill sweep treats as STRAY on this
    box. ALWAYS the owner's own numbered-sibling namespace `^<owner>-\\d+$`
    (the #651 accidental `tmux new -t <owner>` pile-up). On an OWNER
    (multi-project) box ADDITIONALLY every fleet stream family: `^<stem>\\d*
    (?:-\\d+)?$` for each stem != owner (so `marek`, `marek-12`, `montalu2`,
    `montalu2-5` match, but a deliberately-named `marek-notes` does NOT -- only
    a digit / `-<digits>` tail). Never the bare `<owner>` name itself (the
    canonical session), and never a session the owner deliberately named
    outside these namespaces. A single-session (subdev/gk) box gets ONLY the
    owner-N pattern -- widening there would target the account's own real
    session, so it is owner-boxes-only."""
    import re
    res = [re.compile(r"^" + re.escape(owner) + r"-\d+$")]
    if not single_session:
        for stem in sorted(_fleet_stream_stems()):
            if stem != owner:
                res.append(re.compile(r"^" + re.escape(stem) + r"\d*(?:-\d+)?$"))
    return res


def _live_normalize_owner_session(owner, run=None, audit_dir=None,
                                  stray_name_res=None, ps_run=None, home=None):
    """#651/#660: idempotent live normalization on any RUNNING server for this
    box. Two mutually-exclusive branches, keyed on whether the canonical
    `<owner>` session already exists:

    * `<owner>` ABSENT (the #651 rename path, unchanged): if EXACTLY ONE
      surviving `<owner>-N` (N all-digits -- an accidental `tmux new -t
      <owner>` grouped sibling) exists, `rename-session` it to `<owner>` so
      the native `-A -s <owner>` helpers attach to it. `rename-session` NEVER
      kills; zero or >=2 survivors is a silent no-op (never guess which
      becomes canonical).

    * `<owner>` PRESENT (#660 kill sweep): the canonical session is safe, so
      absorb (`kill-session`) every STRAY-named session that is provably an
      idle bare shell -- unattached AND ungrouped AND every pane a bare shell
      with no child process AND cwd'd in $HOME (`_maybe_absorb_idle_stray`);
      NEVER one running claude / any non-shell / any suspended-or-background job
      / any pane in a project dir (feedback_never_touch_stopped_sessions). Every
      kill/skip decision is logged to `<audit_dir>/normalize.log`. `home` is the
      injectable HOME for that cwd guard (default `~`).

    `stray_name_res` (compiled regexes) selects WHICH names are stray-candidates
    for the kill path -- default `[^<owner>-\\d+$]` (owner numbered siblings
    only), and the cmd_install caller passes the widened owner-box set (owner-N
    + fleet stream families, `_owner_box_stray_name_res`) so the dev2
    `marek`/`marek-12` fleet strays are covered too. The bare `<owner>` name is
    always excluded (the canonical session). `ps_run` is the injectable ps
    runner for the no-suspended-child guard.

    Conservative by construction throughout: only the stray namespaces are ever
    matched (never a deliberately-named session outside them); config-path ONLY
    (`rename-session` / `kill-session`, NEVER a `send-keys` keystroke);
    failure-tolerant (no reachable server / a non-zero list-sessions -> silent
    no-op). Same dependency-injectable `run` shape as
    `_live_apply_stream_window_name`."""
    import re
    runner = run or _default_tmux_run
    try:
        result = runner(["tmux", "list-sessions", "-F", "#{session_name}"])
    except Exception as e:
        print("  tmux owner-session normalize (list) skipped (non-fatal): %s"
              % e, file=sys.stderr)
        return
    if getattr(result, "returncode", 1) != 0:
        return
    names = [ln.strip()
             for ln in (getattr(result, "stdout", "") or "").splitlines()
             if ln.strip()]
    owner_pat = re.compile(r"^" + re.escape(owner) + r"-\d+$")
    if owner not in names:
        # #651 RENAME path (unchanged): promote a lone numbered survivor.
        survivors = [n for n in names if owner_pat.match(n)]
        if len(survivors) != 1:
            return  # zero or ambiguous -- never guess which becomes canonical
        try:
            runner(["tmux", "rename-session", "-t", survivors[0], owner])
        except Exception as e:
            print("  tmux owner-session normalize (rename) skipped (non-fatal):"
                  " %s" % e, file=sys.stderr)
        return
    # #660 KILL SWEEP: the canonical `<owner>` exists -- absorb each idle
    # standalone bare-shell STRAY (by `stray_name_res`), log the rest. The bare
    # `<owner>` canonical is never a candidate (it never matches `<owner>-\d+`
    # and is guarded out explicitly).
    res_list = stray_name_res if stray_name_res is not None else [owner_pat]
    for name in names:
        if name == owner:
            continue
        if any(r.match(name) for r in res_list):
            _maybe_absorb_idle_stray(name, runner, audit_dir, ps_run, home)


def apply_stream_tmux_window_name(tmux_conf_path=None, user=None, host=None,
                                   run=None):
    """Idempotently add/remove the #554/#592 window-naming marker block in
    ~/.tmux.conf. #593: rendered ONLY on a SINGLE-SESSION-per-account box
    (`is_single_session_box_user` -- subdev streams + the gk `gatekeeper`
    account, the SAME set #264's ssh-auto-attach uses), NEVER an owner/newlevel
    MULTI-PROJECT box (dev1/dev2): one fixed name + `automatic-rename off` froze
    every project window to `dev1` and destroyed navigation (the #592
    regression). The window name = the box's short TARGET ALIAS
    (`cli_aliases.short_target_alias(user, host)` -- gatekeeper->gk, montaluN->
    mN, davidN->dN, ...), the SAME single source the webterm dashboard tabs draw
    on, never a parallel map. `host` defaults to the box's hostname
    (`os.uname().nodename`). The block is stripped when a box is NOT
    single-session OR yields no SAFE alias (an alias failing
    `_SAFE_STREAM_NAME_RE`, the injection guard).

    Same overall shape as apply_stream_ssh_attach (#264): positional-span
    rewrite (the shared `_clean_tmux_block_spans`, parameterized with this
    block's own markers), create-file-if-absent, no-op on a second run.
    On an ELIGIBLE box it also live-applies the directives on any running server
    (`_live_apply_stream_window_name`); on a NON-single-session owner box with a
    safe alias (one the pre-#593 code mis-provisioned) it instead LIVE-REVERTS
    them (`_live_revert_stream_window_name`), so a running server self-heals.
    Returns True iff ~/.tmux.conf changed."""
    import airuleset
    from cli_aliases import short_target_alias
    from cli_bashrc_appliers import is_single_session_box_user
    path = tmux_conf_path or TMUX_CONF
    u = user or airuleset._current_user()
    box = host or os.uname().nodename
    alias = short_target_alias(u, box)
    safe_alias = bool(alias) and bool(_SAFE_STREAM_NAME_RE.match(alias))
    # #593: window naming is ONLY for single-session-per-account boxes (gk +
    # subdev streams) -- the SAME set the #264 ssh-auto-attach uses. An
    # owner/newlevel MULTI-PROJECT box (dev1/dev2) must NEVER get it: one fixed
    # window name + `automatic-rename off` destroys its per-project navigation
    # (the #592 regression). newlevel@dev1 yields a SAFE alias ("dev1"), so the
    # single-session predicate -- not alias safety -- is what excludes it.
    single_session = is_single_session_box_user(u)
    should_have = single_session and safe_alias
    existing = path.read_text() if path.exists() else ""
    spans = _clean_tmux_block_spans(
        existing, STREAM_TMUX_WINDOW_MARK_START, STREAM_TMUX_WINDOW_MARK_END)
    if should_have:
        block = render_stream_tmux_window_block(alias)
        if spans:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                out.append(block)
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
        else:
            sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
            new = f"{existing}{sep}\n{block}\n"
    else:
        if not spans:
            new = existing
        else:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
    changed = new != existing
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    if should_have:
        # rename EVERY window on this (single-session) box's server to the alias
        # -- the account's own session name may differ from the unix user (on gk
        # the owner session is zbynek-N, #562), so `list-windows -a` covers it.
        _live_apply_stream_window_name(alias, run)
    elif safe_alias and not single_session:
        # #593: a multi-project owner box (dev1/dev2) the pre-#593 code wrongly
        # provisioned -- self-heal any running server that still carries the bad
        # #592 options AND un-freeze its already-open windows stuck at `<alias>`
        # (so the owner's navigation is restored without a restart; the conf
        # strip above only fixes the NEXT server start).
        _live_revert_stream_window_name(alias, run)
    return changed


# ---------------------------------------------------------------------------
# #660: native `session-created` audit hook on the OWNER box.
#
# ROOT CAUSE (repro'd live on an isolated `-L` socket, tmux 3.7b): a stray
# standalone `<owner>-N` (own bash window, no group tag) is created by a path
# the #651 interactive-only 3-token `tmux()` wrapper does not cover (a 4-token
# `new-session -d -t`, a non-interactive/ssh/webterm/script context, `command
# tmux`, or an explicit `-s <owner>-N`). The stray pane's process tree ends at
# the tmux SERVER, so the creating CLIENT is unrecoverable AFTER the fact --
# meaning the creator cannot be pinned statically. This native hook is the
# DETERMINISTIC answer (#649 native-beats-custom): it logs EVERY session
# creation's client pid/tty/name + a ps chain of the creator, so the NEXT
# stray's creator is captured for certain.
#
# OWNER BOXES ONLY. A single-session box (gk + subdev streams,
# `is_single_session_box_user`) already binds `session-created` to
# `rename-window` (#593), so this must NEVER render there -- the two are
# mutually exclusive by box type, so the single global hook slot never
# collides. On an owner box (dev1/dev2), `session-created` is otherwise unset
# (apply_stream_tmux_window_name's owner-box path REVERTS/unsets it), so this
# applier must run AFTER apply_stream_tmux_window_name in the install flow
# (else that revert's live `set-hook -gu session-created` would clear this
# hook right after we set it -- the persistent conf block below survives a
# server restart regardless, but the live-apply needs the ordering).
# ---------------------------------------------------------------------------

OWNER_AUDIT_MARK_START = "# >>> airuleset tmux owner session-created audit >>>"
OWNER_AUDIT_MARK_END = "# <<< airuleset tmux owner session-created audit <<<"
OWNER_AUDIT_LOGGER_REL = ".claude/tmux-audit/log-session-created.sh"


def render_owner_audit_logger():
    """The managed logger script invoked by the `session-created` hook. Appends
    ONE line per session creation to `~/.claude/tmux-audit/session-created.log`:
    ISO timestamp, session name, creating-client pid/tty/name, a ps chain of
    the creator (walked up to 3 ancestors of the client pid), and -- for a
    DETACHED create with no client pid -- a snapshot of any live `tmux new*`
    CLI process still mid-command. Best-effort throughout (`-b` backgrounds it,
    every fallible step is guarded), so it can never block or fail a session
    creation. Bounded to the last 500 log lines."""
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "# #660: airuleset owner-box tmux session-created audit logger. Invoked\n"
        "# by the native `set-hook -g session-created` (run-shell -b) with the\n"
        "# new session name + creating-client fields. The pane process tree of a\n"
        "# stray ends at the tmux server, so the creating CLIENT is otherwise\n"
        "# unrecoverable post-hoc; this line is the deterministic capture.\n"
        "# #660 review 2: each field is sentinel-prefixed with a literal x\n"
        "# (x#{q:...}) so an empty value keeps its positional slot; strip it.\n"
        'sess="${1:-x}"; sess="${sess#x}"\n'
        'cpid="${2:-x}"; cpid="${cpid#x}"\n'
        'ctty="${3:-x}"; ctty="${ctty#x}"\n'
        'cname="${4:-x}"; cname="${cname#x}"\n'
        "# #660 review 7: guard HOME so a server spawned from a stripped env\n"
        "# does not abort under set -u before anything is logged.\n"
        '[ -n "${HOME:-}" ] || exit 0\n'
        'dir="$HOME/.claude/tmux-audit"\n'
        'log="$dir/session-created.log"\n'
        'mkdir -p "$dir" 2>/dev/null || exit 0\n'
        "ts=\"$(date -Is 2>/dev/null || date 2>/dev/null || echo '?')\"\n"
        'creator=""\n'
        'case "$cpid" in\n'
        "  ''|*[!0-9]*) : ;;\n"
        "  *)\n"
        '    p="$cpid"\n'
        "    for _ in 1 2 3; do\n"
        '      row="$(ps -o pid=,ppid=,user=,args= -p "$p" 2>/dev/null '
        "| tr -s ' ' | sed 's/^ //' || true)\"\n"
        '      [ -n "$row" ] || break\n'
        '      creator="${creator}[${row}] "\n'
        '      p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d \' \' || true)"\n'
        '      case "$p" in \'\'|0|1) break ;; esac\n'
        "    done\n"
        "    ;;\n"
        "esac\n"
        'tmuxprocs=""\n'
        'if [ -z "$cpid" ]; then\n'
        "  tmuxprocs=\"$(ps -eo pid=,ppid=,args= 2>/dev/null "
        "| grep -E 'tmux( -[^ ]+)* (new-session|new)( |$)' "
        "| grep -v ' grep' | tr '\\n' ';' || true)\"\n"
        "fi\n"
        "printf '%s\\tsession=%s\\tclient_pid=%s\\tclient_tty=%s"
        "\\tclient_name=%s\\tcreator=%s\\ttmux_new_procs=%s\\n' \\\n"
        '  "$ts" "$sess" "$cpid" "$ctty" "$cname" "$creator" "$tmuxprocs" '
        '>> "$log" 2>/dev/null || exit 0\n'
        'tail -n 500 "$log" > "$log.tmp" 2>/dev/null '
        '&& mv "$log.tmp" "$log" 2>/dev/null || true\n'
    )


def _owner_audit_hook_command(logger_path):
    """The tmux hook command string: run the logger in the BACKGROUND
    (`run-shell -b`, so session creation is never blocked), passing the native
    session + client fields as FOUR positional args.

    Quoting (live-verified on an isolated `-L` tmux 3.7b server): the four
    fields use the `#{q:...}` format modifier, which BACKSLASH-escapes sh(1)
    special characters in each value -- so a session name carrying a shell
    metacharacter (space, `;`, `$`, quote, ...) can never inject into the
    run-shell command. `q:` does NOT wrap a value in quotes, so an EMPTY field
    (a detached `new-session -d` create has no client) would otherwise VANISH
    under sh word-splitting and SHIFT the positional args (review 🟡2); each
    field is therefore SENTINEL-PREFIXED with a literal `x`, so an empty value
    still yields the non-empty word `x` that holds its position, and the logger
    strips the leading `x` back off (`${N:-x}` / `${N#x}`). The whole command is
    stored UNEXPANDED (the caller wraps it in the conf with SINGLE quotes; the
    live `set-hook` stores the argv verbatim) and tmux expands `#{...}` at FIRE
    time, per hook. (A newline can't reach a field: tmux rejects newlines in
    session names, and pid/tty/name never contain one.) `logger_path` is a
    managed absolute path with no spaces (a fleet home dir)."""
    return ('run-shell -b "%s x#{q:hook_session_name} x#{q:client_pid} '
            'x#{q:client_tty} x#{q:client_name}"' % logger_path)


def render_owner_session_audit_block(logger_path):
    """The managed ~/.tmux.conf marker block that binds `session-created` to the
    audit logger. Persistent so it survives a server restart; the applier also
    live-applies it via `set-hook`. Owner boxes only (the caller gates).

    The hook value is SINGLE-quoted (not double): a double-quoted set-hook value
    is expanded at CONF-PARSE time (baking empty fields) AND collides with the
    inner double-quotes around the run-shell argument -- both live-verified as
    broken. Single quotes keep the `#{...}` unexpanded until the hook FIRES."""
    return (
        f"{OWNER_AUDIT_MARK_START}\n"
        "# #660: capture the CREATOR of every new tmux session so a stray\n"
        "# <owner>-N / foreign owner-session can be root-caused deterministically\n"
        "# (the pane process tree ends at the server -> the creating CLIENT is\n"
        "# otherwise unrecoverable). Native set-hook (#649). Owner boxes only --\n"
        "# single-session boxes own session-created for rename-window (#593).\n"
        f"set-hook -g session-created '{_owner_audit_hook_command(logger_path)}'\n"
        f"{OWNER_AUDIT_MARK_END}"
    )


def _write_owner_audit_logger(logger_path):
    """Write the managed logger script (idempotent) and mark it executable.
    Best-effort: an unwritable path is noted on stderr, never fatal."""
    try:
        logger_path.parent.mkdir(parents=True, exist_ok=True)
        logger_path.write_text(render_owner_audit_logger())
        os.chmod(logger_path, 0o755)
    except Exception as e:
        print("  tmux owner audit-logger write skipped (non-fatal): %s" % e,
              file=sys.stderr)


def _live_apply_owner_session_audit(logger_path, run=None):
    """Best-effort live `set-hook -g session-created` on any RUNNING server so an
    already-running owner server captures creators WITHOUT waiting for a restart.
    Config-path ONLY (`set-hook`, never a `send-keys` keystroke), failure-
    tolerant (no server -> no-op). Mirrors `_live_apply_stream_window_name`."""
    runner = run or _default_tmux_run
    try:
        runner(["tmux", "set-hook", "-g", "session-created",
                _owner_audit_hook_command(logger_path)])
    except Exception as e:
        print("  tmux owner audit-hook live-apply skipped (non-fatal): %s" % e,
              file=sys.stderr)


def apply_owner_session_created_audit(tmux_conf_path=None, user=None, run=None,
                                      home=None):
    """#660: idempotently add/remove the owner-box `session-created` audit
    marker block in ~/.tmux.conf. Rendered ONLY on an OWNER (multi-project)
    box (`not is_single_session_box_user` -- dev1/dev2 newlevel); a single-
    session box (gk + subdev streams) owns `session-created` for #593
    rename-window, so the block is STRIPPED there (and never live-applied).

    On an owner box it also writes the managed logger script and live-applies
    the hook (`_live_apply_owner_session_audit`). Same positional-span rewrite
    + create-file-if-absent + no-op-on-second-run shape as
    apply_stream_tmux_window_name. Returns True iff ~/.tmux.conf changed.

    MUST be called AFTER apply_stream_tmux_window_name in the install flow: on
    an owner box that applier live-UNSETS session-created (its #593 revert), so
    this applier's live `set-hook` has to run afterwards to win."""
    import airuleset
    from cli_bashrc_appliers import is_single_session_box_user
    path = tmux_conf_path or TMUX_CONF
    u = user or airuleset._current_user()
    h = Path(home) if home else Path.home()
    owner_box = not is_single_session_box_user(u)
    logger_path = h / OWNER_AUDIT_LOGGER_REL
    existing = path.read_text() if path.exists() else ""
    spans = _clean_tmux_block_spans(
        existing, OWNER_AUDIT_MARK_START, OWNER_AUDIT_MARK_END)
    if owner_box:
        _write_owner_audit_logger(logger_path)
        block = render_owner_session_audit_block(str(logger_path))
        if spans:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                out.append(block)
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
        else:
            sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
            new = f"{existing}{sep}\n{block}\n"
    else:
        if not spans:
            new = existing
        else:
            out, cursor = [], 0
            for s, e in spans:
                out.append(existing[cursor:s])
                cursor = e
            out.append(existing[cursor:])
            new = "".join(out)
    changed = new != existing
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    if owner_box:
        _live_apply_owner_session_audit(str(logger_path), run)
    return changed


# ---------------------------------------------------------------------------
# tmux boot-time cutover (#242) -- points /usr/local/bin/tmux at the newest
# managed tmux build (tmux-3.7b) at the box's own NEXT boot, so the client
# and server binary always match. #240/#241: repointing the symlink while a
# tmux SERVER is live breaks every attach ("server exited unexpectedly"),
# and at boot no server exists yet -- the only moment a flip is provably
# safe for a box whose server is already live today (dev2/gatekeeper/
# subdev). The cutover's standing rationale is the matching client/server
# binary. (It is ALSO what makes #586's fixed-geometry `window-size manual`
# safe on the fleet -- that option crashes tmux 3.4's server at startup, #241,
# but starts cleanly on 3.7b; the conf line is version-gated so a not-yet-
# cutover 3.4 box never receives it. #613 REOPEN-2 RESTORED the manual pin as
# the owner's fixed-size invariant, reversing the first reopen's `latest`.)
#
# System-level (root-owned /etc/systemd/system + /usr/local/bin), unlike
# every OTHER airuleset-managed unit (file-drop/api-watchdog are --user).
# ---------------------------------------------------------------------------

TMUX_CUTOVER_UNIT_NAME = "airuleset-tmux-cutover.service"
TMUX_CUTOVER_SCRIPT_DEST = "/usr/local/bin/airuleset-tmux-cutover.sh"
TMUX_CUTOVER_SERVICE_DEST = "/etc/systemd/system/" + TMUX_CUTOVER_UNIT_NAME
TMUX_CUTOVER_SERVICE_TEMPLATE = REPO_DIR / "settings" / "tmux-cutover.service.template"
# tmux-3.7b is the only extra build any managed box carries today (#242); a
# future newer build is a distinct ticket with its own compatibility check
# (like #241 for 3.4), not something a silent "highest version wins" glob
# should decide -- deliberately hardcoded, not generalized.
TMUX_CUTOVER_NEWEST = "/usr/local/bin/tmux-3.7b"

# Env-var overrides (unset in production -- the defaults above always apply
# there) exist ONLY so this script's LOGIC can be exercised by a real `sh`
# subprocess against a throwaway sandbox in tests, instead of only ever
# being proven by string-matching its source.
TMUX_CUTOVER_SCRIPT_CONTENT = """#!/bin/sh
# airuleset-managed (do NOT edit) -- boot-time tmux symlink cutover (#242).
# Idempotently points /usr/local/bin/tmux at the newest managed tmux build
# present on this box. Runs once at boot, before any tmux server can exist
# -- see airuleset-tmux-cutover.service's own ordering (Before=sysinit.target
# ssh.service ssh.socket, DefaultDependencies=no) -- so it can never run
# while a server using the OLD binary is already live.
set -eu

NEWEST="${AIRULESET_TMUX_CUTOVER_NEWEST:-/usr/local/bin/tmux-3.7b}"
TARGET="${AIRULESET_TMUX_CUTOVER_TARGET:-/usr/local/bin/tmux}"

# No 3.7b build on this box (yet), or it isn't runnable (a truncated /
# interrupted copy, wrong permissions) -- leave the packaged binary alone.
# -x (not -e): a present-but-non-executable NEWEST must never become the
# boot-time target (review finding, #242).
if [ ! -x "$NEWEST" ]; then
    exit 0
fi

CURRENT=""
if [ -L "$TARGET" ]; then
    CURRENT=$(readlink "$TARGET")
fi

# Already correct -- no-op. This is what makes a re-run at any later boot,
# or a box that is already on 3.7b, safe: the symlink is only ever touched
# when it is actually stale.
if [ "$CURRENT" != "$NEWEST" ]; then
    ln -sfn "$NEWEST" "$TARGET"
fi
"""

# subdev's four stream accounts (montalu/marek/david/simap) have no sudo at
# all and share ONE box + ONE symlink -- root there is reachable ONLY from
# the gatekeeper VPS (never dev1), via this identity. Its mere PRESENCE is
# the discriminator for "am I the gatekeeper box" (mirrors #68's own
# identity-based trust in block-subdev-ssh-misuse.sh) -- never a
# hostname/whoami guess.
SUBDEV_ADMIN_IDENTITY = Path.home() / ".ssh" / "subdev_admin"


def _sudo_write_root_file(run, content, dest, mode):
    """Write `content` to root-owned `dest` LOCALLY via `sudo -n tee` +
    `sudo -n chmod` -- never an interactive password. Returns (ok, err)."""
    w = run(["sudo", "-n", "tee", dest], input=content,
            capture_output=True, text=True, timeout=15)
    if w.returncode != 0:
        return False, f"write {dest} failed: {(w.stderr or '').strip()}"
    c = run(["sudo", "-n", "chmod", mode, dest],
            capture_output=True, text=True, timeout=15)
    if c.returncode != 0:
        return False, f"chmod {dest} failed: {(c.stderr or '').strip()}"
    return True, None


def setup_tmux_cutover_provisioning(run=None):
    """Install the boot-time tmux symlink cutover unit on THIS box (#242).

    Non-interactive (`sudo -n`) throughout, matching check_runtime_deps's own
    "install what you can, skip loudly what you can't" shape: the four subdev
    stream accounts (montalu/marek/david/simap) have no sudo AT ALL -- probed
    up front and skipped with an expected, non-alarming reason, because the
    ONE shared box+symlink they sit behind is provisioned instead by
    `setup_tmux_cutover_subdev_via_gatekeeper` (the gatekeeper account's own
    `install` run performs that root hop).

    Rewrites the script + unit UNCONDITIONALLY on every call (same shape as
    apply_ultracode_launcher's own claude-launcher script -- cheap, and the
    content is a pure function of fixed constants, so a same-content rewrite
    is a true no-op on disk) and (re)enables the unit -- but NEVER starts it.
    Starting it now would flip the symlink under a POSSIBLY-LIVE tmux server;
    the actual flip only ever happens at the box's own NEXT boot, when no
    server can exist yet (see the shipped unit's own ordering). Running the
    unit/script directly at ANY time (including a manual `systemctl start`
    used to prove idempotency) is still provably safe on a box already on
    3.7b: the script's own compare-then-skip is what makes that true, not
    merely "we never invoke it".

    Returns (ok: bool, reason: str|None) -- reason is set only when this
    account genuinely cannot do it (the expected subdev-stream case) or a
    real command failed."""
    import subprocess
    run = run or subprocess.run

    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10)
        has_sudo = probe.returncode == 0
    except Exception:
        has_sudo = False
    if not has_sudo:
        return False, "no NOPASSWD sudo on this account (expected on the subdev stream accounts)"

    if not TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        return False, f"missing unit template: {TMUX_CUTOVER_SERVICE_TEMPLATE}"
    unit_content = TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()

    for content, dest, mode in (
        (TMUX_CUTOVER_SCRIPT_CONTENT, TMUX_CUTOVER_SCRIPT_DEST, "755"),
        (unit_content, TMUX_CUTOVER_SERVICE_DEST, "644"),
    ):
        ok, err = _sudo_write_root_file(run, content, dest, mode)
        if not ok:
            return False, err

    dr = run(["sudo", "-n", "systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=20)
    if dr.returncode != 0:
        return False, f"daemon-reload failed: {(dr.stderr or '').strip()}"
    en = run(["sudo", "-n", "systemctl", "enable", TMUX_CUTOVER_UNIT_NAME],
            capture_output=True, text=True, timeout=20)
    if en.returncode != 0:
        return False, f"enable failed: {(en.stderr or '').strip()}"

    return True, None


def setup_tmux_cutover_subdev_via_gatekeeper(run=None, identity_path: Path = None):
    """From the gatekeeper account ONLY, root-hop into the shared subdev VPS
    (`ssh -i ~/.ssh/subdev_admin root@subdev`) and install the SAME cutover
    unit there -- ONE root-level install covers all FOUR subdev stream
    accounts (montalu/marek/david/simap), which share one box and one
    /usr/local/bin/tmux symlink and individually have no sudo (see
    setup_tmux_cutover_provisioning's own no-op there). Root@subdev is
    reachable ONLY from gatekeeper, never from dev1 (machine-identities.md)
    -- which is why this is a distinct function rather than one more
    REMOTE_HOSTS deploy entry: root there is not one of the managed
    per-account checkouts `install` normally runs against.

    A true no-op on every box that isn't gatekeeper (dev1/dev2/the subdev
    accounts themselves never carry the identity file). Never starts the
    remote unit, for the identical live-server-safety reason as the local
    path above -- the remote's own next reboot is what actually flips it.

    Returns (ok: bool, reason: str|None)."""
    import subprocess
    run = run or subprocess.run
    identity = identity_path or SUBDEV_ADMIN_IDENTITY

    if not identity.exists():
        return False, "not the gatekeeper box (no subdev_admin identity) -- skipped"

    if not TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        return False, f"missing unit template: {TMUX_CUTOVER_SERVICE_TEMPLATE}"
    unit_content = TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()

    ssh_prefix = ["ssh", "-i", str(identity),
                  "-o", "StrictHostKeyChecking=no", "root@subdev"]

    for content, dest, mode in (
        (TMUX_CUTOVER_SCRIPT_CONTENT, TMUX_CUTOVER_SCRIPT_DEST, "755"),
        (unit_content, TMUX_CUTOVER_SERVICE_DEST, "644"),
    ):
        w = run(ssh_prefix + [f"tee {dest} >/dev/null && chmod {mode} {dest}"],
                input=content, capture_output=True, text=True, timeout=25)
        if w.returncode != 0:
            return False, f"write {dest} on subdev failed: {(w.stderr or '').strip()}"

    dr = run(ssh_prefix + ["systemctl daemon-reload"],
            capture_output=True, text=True, timeout=25)
    if dr.returncode != 0:
        return False, f"daemon-reload on subdev failed: {(dr.stderr or '').strip()}"
    en = run(ssh_prefix + [f"systemctl enable {TMUX_CUTOVER_UNIT_NAME}"],
            capture_output=True, text=True, timeout=25)
    if en.returncode != 0:
        return False, f"enable on subdev failed: {(en.stderr or '').strip()}"

    return True, None
