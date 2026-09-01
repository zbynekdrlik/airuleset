"""airuleset Claude launcher + history-viewer SCRIPT assets (File A of the
#433 cluster L-F 2-file split) -- the script templates airuleset writes
into ~/.claude/ (airuleset-claude-launch.sh, airuleset-claude-history.py,
airuleset-claude-history-popup.sh) plus their render_* helpers and the
shared encode_project_dir.

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
cluster L, step L-F -- decision 2 of the binding "Design -- klaster L
sub-split"). Same verbatim-move + facade-re-export pattern as
cli_worktree_sweep.py (L1) / cli_target_purge.py + cli_scratch_sweep.py
(L2) / cli_tmux_provisioning.py (L3). airuleset.py keeps a single
`from cli_claude_scripts import (...)` re-export at the old definition
site, so cmd_install's writes, apply_ultracode_launcher's renders and
tests' `airuleset.<name>` references all keep working unchanged.

This is the BASE half of the L-F split: the script TEMPLATES + renderers
that the sibling half cli_bashrc_appliers.py (the ~/.bashrc appliers)
forward-imports. The dependency is strictly one-directional
(cli_bashrc_appliers -> cli_claude_scripts), never back -- no import cycle.

SELF-CONTAINED: stdlib only at module level, no top-level `import
airuleset` (no import-cycle surface in CLI `__main__` or the test `import
airuleset` topology). `CLAUDE_DIR` below is this file's own copy of the
canonical one-line expression `Path.home() / ".claude"` that
cli_worktree_sweep.py / cli_target_purge.py / cli_tmux_provisioning.py
already inline locally -- identical value (sibling top-level module in the
same directory as airuleset.py), established repo idiom. The DEST
constants are module-level assignments frozen at import: a patch of
`airuleset.CLAUDE_DIR` never changed them even before this split.

`TMUX_HISTORY_LIMIT` is imported directly from the already-extracted
cli_tmux_provisioning leaf (#433 L3) for the history-popup script's own
`{{TMUX_HISTORY_LIMIT}}` default (#433 dispatch: "keep that direction" --
history-viewer -> tmux-provisioning, never inverted). cli_tmux_provisioning
has zero airuleset imports and does not import this file, so no cycle.

The one outbound resident coupling (`MANAGED_MODEL`, read inside
render_claude_launch_script) is reached via a lazily-placed deferred
`import airuleset` (call-time, airuleset fully loaded), honoring the
canonical value and any test patch of `airuleset.MANAGED_MODEL`.
"""

from pathlib import Path

from cli_tmux_provisioning import TMUX_HISTORY_LIMIT

CLAUDE_DIR = Path.home() / ".claude"


# The managed claude launcher (#77, 2026-07-26): a shell FUNCTION in ~/.bashrc
# is parsed ONCE at shell startup and then stays frozen in that shell's memory
# FOREVER. Panel shells are long-lived (tmux panes running for days), so any
# logic baked directly into the .bashrc function (flags, model pin, ultracode)
# kept resurrecting on every relaunch of an ALREADY-RUNNING stale shell, no
# matter how many times `push` rewrote .bashrc -- rewriting the file has zero
# effect on a shell that already parsed the old function into memory. Measured
# live: two sessions launched HOURS after #53 (which correctly made ultracode
# opt-in ON DISK) still carried the pre-#53 default, because the panel shells
# hosting them predated the fix.
#
# Fix: .bashrc holds ONLY thin one-line wrapper functions with NO flag
# literals -- each just execs the managed SCRIPT (CLAUDE_LAUNCH_SCRIPT_DEST),
# which carries ALL the actual logic (continue-or-new, --model, skip-perms).
# A script is read fresh from disk
# on EVERY invocation, so a `push` that rewrites the script changes behavior
# in every already-running shell IMMEDIATELY -- no `source ~/.bashrc`, no
# relaunch, no restart. Same shape as the caveman stable statusline shim
# (render_caveman_shim() below) -- read that first before changing this.
CLAUDE_LAUNCH_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-launch.sh"
# --- the script content itself -----------------------------------------------
# Ultracode is NO LONGER a managed launch flag (owner directive 2026-08-30 --
# "Chcel by som este aby sa claude v targetoch nespustali s zapnutym ultracode
# ale s effort high"): this REVERSES the launch-flag half of #445, which had made
# `--settings '{"ultracode":true}'` the standing default in every mode but `plain`.
# No managed mode bakes ultracode any more (absent from default/new/ultracode/
# fullscreen alike), and the effort baseline drops `xhigh` → `high`
# (MANAGED_EFFORT_LEVEL). Only the LAUNCH FLAGS reversed: the max-acceleration /
# parallel-worktree doctrine and the per-phase model tiering are UNCHANGED.
# Without the session ultracode flag, Workflow-tool use follows its standard
# opt-in (the user invokes it in their own words; a user wanting ultracode passes
# `--settings '{"ultracode":true}'` by hand). `claude-ultracode()` is RETAINED
# for muscle-memory but now behaves like `default`; `plain` stays the vanilla
# no-managed-flags escape hatch.
#   --dangerously-skip-permissions  : auto-approve (the user opted in for their dev boxes).
#   -c                              : continue the most recent conversation in the cwd.
#   --model '{{MANAGED_MODEL}}'     : baked in at RENDER time so EVERY mode except
#       `plain` — including a RESUMED (-c) session — explicitly requests the managed
#       model. Proven live on gatekeeper: settings.json requested the then-managed
#       Opus id, but a resumed session's transcript kept showing an older model — `-c`
#       alone just continues whatever model the prior transcript was started with; only
#       an explicit --model on the launch command line forces it.
#   --allowedTools Grep,Glob        : #779 (owner ROZHODNUTÉ, comment 5479254695,
#       2026-08-31) — baked into every mode except `plain`, the SAME placement as
#       --model above. An Anthropic-collaborator-confirmed side effect of passing
#       this flag is that Claude Code reinstates its built-in Grep/Glob tools and
#       STOPS shadowing `grep`/`find` inside the Bash tool with its bundled
#       ugrep/bfs binaries (anthropics/claude-code#69736 is open, no dedicated
#       opt-out exists yet) — so plain `grep`/`find` in a managed session's Bash
#       calls resolve to the real system binaries again, which correctly die on
#       timeout instead of ugrep 7.5.0's proven busy-loop-on-timeout bug (#776).
#       This neutralizes that runaway class AT THE SOURCE, fleet-wide; the #776
#       hook (block-root-recursive-grep.sh) + watchdog Job 37 reaper stay in
#       place as a backstop in case a future Claude Code update changes this
#       undocumented shadowing behavior.
# The conversation probe globs ~/.claude/projects/<encoded-cwd>/*.jsonl — Claude Code
# encodes cwd by turning / . _ into dashes; a project dir holding only memory/ (no
# transcript) means nothing to continue. Unknown encoding chars fail toward the
# FRESH branch (worse case: a new session instead of a cryptic error).
# Modes (no managed mode carries ultracode any more — owner directive 2026-08-30):
# `default` (claude — continue-or-new, skip-perms, model, allowedTools), `new`
# (claude-new — always FRESH, skip-perms, model, allowedTools), `ultracode`
# (claude-ultracode — RETAINED alias of the default mode for muscle-memory, NO
# ultracode flag now), `plain` (claude-plain — vanilla, no managed flags —
# including no --allowedTools, so a deliberate stock-claude reproduction keeps
# the real shadow-grep/find behavior uncontaminated, #445 precedent),
# `fullscreen` (claude-fullscreen — continue-or-new + skip-perms + model +
# allowedTools, PLUS CLAUDE_CODE_NO_FLICKER=1).
#   CLAUDE_CODE_NO_FLICKER=1 : #376 REVERSED the `apply_managed_settings_defaults`
#       pin from `"tui": "default"` (classic) to `"tui": "fullscreen"` fleet-wide
#       (see that function's own docstring for the full history/tradeoff/citation)
#       -- so this launcher mode's env var is now REDUNDANT with the fleet default,
#       not an opt-in override away from it. Kept, harmless: it is an explicit way
#       to force fullscreen on a box whose LOCAL settings.json has drifted from the
#       managed pin (a manual `/tui default` switch, a pre-#376 install not yet
#       pushed), and it still fixes the SAME proven upstream Claude Code renderer
#       defect the mode was originally built to bypass (#253 --
#       anthropics/claude-code#84247 / #46834, both still open 2026-08-11: a
#       SIGWINCH/relayout re-emits a fresh copy of the transcript into the
#       terminal's PRIMARY scrollback, corrupting it with duplicate/interleaved
#       frames; reproduced live -- a real 25-line completion-report chunk found
#       duplicated verbatim in tmux pane history on dev1's own 3.7b tmux, the SAME
#       version the corruption was reproduced against, NOT the fleet's dev2/gk/
#       subdev 3.4 build). The alternate-screen TUI means Claude Code owns the
#       whole viewport and never writes into the terminal's native scrollback at
#       all, so the defect class has nothing to corrupt -- the same reasoning that
#       makes `"tui": "fullscreen"` the right managed default. `Ctrl+B [`
#       tmux-native scrollback going empty under it is real and EXPECTED
#       (fullscreen's own `PgUp`/`PgDn`/`Ctrl+O` are the documented replacement,
#       not a bug) -- see the #376 tradeoff discussion on `apply_managed_settings_
#       defaults`. Wins over any local `settings.json` override the SAME way it
#       always did -- confirmed against the installed CC binary that the env var
#       is read before the settings key. Also overrides upstream's own
#       tmux-control-mode / Windows-over-SSH auto-disable guards for fullscreen
#       mode, since those check the SAME env var this mode forces on -- an
#       intentional consequence of opting in explicitly, not something this mode
#       tries to work around.
CLAUDE_LAUNCH_SCRIPT_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — the claude launcher (#77). Read FRESH from
# disk on EVERY invocation (unlike a ~/.bashrc function, which is parsed once
# at shell startup and then stays frozen in that shell's memory forever), so a
# `push` that rewrites this file changes launch behavior immediately in every
# already-running shell — no `source ~/.bashrc`, no relaunch, no restart.
set -euo pipefail

mode="${1:-default}"
if [ "$#" -gt 0 ]; then shift; fi

# claude installs to ~/.local/bin, which NON-LOGIN interactive shells (su
# without -, tmux with a default-command, IDE terminals) never get — only
# ~/.profile adds it, and only login shells read that (montalu@dev1
# "claude: command not found", 2026-07-04).
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac

# Managed-default (#460, user decision 2026-08-14): disable CC's memory-pressure
# REAP of a main-session run_in_background waiter (SIGTERM->SIGKILL in minutes on
# a swap-thrashing box, #448) so the long-wait background-waiter pattern survives
# fleet-wide. Exported into the CLI process env (inherited across the exec claude)
# for every managed mode; the vanilla `plain` escape hatch is left uncontaminated,
# mirroring how --model applies to every mode except plain. ROLLBACK =
# delete this one line (rollback criterion in the #448 section of
# .claude/rules/airuleset-internals.md; dev1 camera-box build pressure is the
# named risk, tracked as #470).
[ "$mode" = plain ] || export CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1

# #659/#669: the owner_vps headless OAuth-token export that once stood here was
# REMOVED. login/auth ON a target is the PROJECT claudy's responsibility, and
# airuleset never touches auth (owner ROZHODNUTÉ #659, #537 machine-identity
# boundary). The launcher no longer delivers or exports any OAuth token;
# ~/.claude/.credentials.json (managed by the box's own claudy) is the login on
# every box, owner VPS included.

_has_conversation() {
  local ccdir="${PWD//\//-}"; ccdir="${ccdir//./-}"; ccdir="${ccdir//_/-}"
  compgen -G "$HOME/.claude/projects/$ccdir/*.jsonl" >/dev/null 2>&1
}

case "$mode" in
  plain)
    exec claude "$@"
    ;;
  new)
    exec claude --dangerously-skip-permissions \
      --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    ;;
  ultracode)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    else
      exec claude --dangerously-skip-permissions \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    fi
    ;;
  fullscreen)
    export CLAUDE_CODE_NO_FLICKER=1
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    else
      exec claude --dangerously-skip-permissions \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    fi
    ;;
  *)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    else
      exec claude --dangerously-skip-permissions \
        --model '{{MANAGED_MODEL}}' --allowedTools Grep,Glob "$@"
    fi
    ;;
esac
"""


def render_claude_launch_script():
    """The launch-script content with the managed model substituted in — the
    write site MUST use this, never the raw constant (same discipline as
    render_caveman_shim())."""
    import airuleset
    return CLAUDE_LAUNCH_SCRIPT_CONTENT.replace("{{MANAGED_MODEL}}", airuleset.MANAGED_MODEL)


def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_'
    become '-'. airuleset.py's own top-level copy (#267, reused by
    tests seeding a synthetic ~/.claude/projects/<enc>/ tree) -- the
    IDENTICAL logic also lives inline inside CLAUDE_HISTORY_SCRIPT_CONTENT
    below (that script is deployed standalone and must not import
    airuleset.py itself) and, independently, in watchdog/__init__.py."""
    return "".join("-" if c in "/._" else c for c in str(cwd))


# #267/#376: the "claude-history" companion -- FALLBACK, not primary, since
# #376. The PRIMARY answer for "what did claude do and write" is now
# fullscreen's own native scrollback: `PgUp`/`PgDn` scroll the whole session
# (survives repeated compaction, per Anthropic's own docs), `Ctrl+O` opens
# transcript-mode search -- see `apply_managed_settings_defaults`'s `tui`
# bullet and MANAGED_TUI for the full history/citation. This companion keeps
# a real, still-needed FALLBACK role fullscreen structurally cannot cover:
# checking a session's history from a DIFFERENT pane, or after the session
# has already EXITED (fullscreen's scrollback is a live, in-app view -- it
# is gone once the process is gone; this script instead reads the durable
# transcript JSONL straight off disk). Measured live (dev1, two replicates,
# real interactive sessions + real relayout events -- resizes, Ctrl+O,
# Shift+Tab -- via `scripts/measure_scrollback_holes.py`, results pinned to
# the ticket): CLAUDE_CODE_NO_FLICKER=1 does NOT fix tmux scrollback holes --
# it makes NATIVE tmux scrollback almost entirely EMPTY (78.5-87.33% of a
# generated response missing, even with ZERO relayout stress, because
# alternate-screen mode never writes into tmux's native history buffer at
# all), categorically WORSE than default mode's real-but-small corruption
# (0-6% of lines, ONLY after an actual relayout event). That finding is about
# TRANSIENT ON-SCREEN REDRAW during a live resize -- a different mechanism
# from the PERSISTENT, app-internal scrollback list `PgUp`/`Ctrl+O` read
# from (see the #376 design comment on the ticket for why the two don't
# actually contradict). This companion's own honest fix for "what did claude
# do and write" is unchanged either way: it reads the session's own
# transcript JSONL -- the API's source of truth, which the upstream renderer
# defect (#253: anthropics/claude-code#84247/#46834) cannot touch at all,
# since it never passes through the terminal renderer a second time -- and
# prints a plain, linear, readable log of every real user prompt / assistant
# message / tool call. A live key-by-key test on the installed CC 2.1.223
# confirmed there is no in-app pager to lean on instead under classic mode
# (Ctrl+O there is only an inline verbose toggle -- no pager, the documented
# PgUp/PgDn/{/}/[/] keys inside it do nothing at all); `/export` (a slash
# command, typed inside a LIVE session) is a validated alternative for a
# session you're currently in, but this script also covers the common case
# of checking a session's history AFTER it exited, or from a DIFFERENT pane
# entirely (`--pane`), with zero risk of ever typing a keystroke into
# someone else's live session.
CLAUDE_HISTORY_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-history.py"
CLAUDE_HISTORY_SCRIPT_CONTENT = r'''#!/usr/bin/env python3
# airuleset-managed (do NOT edit) -- claude-history (#267): a readable,
# un-corrupted view of what a Claude Code session did and wrote, built
# straight from its own transcript JSONL -- the source of truth, immune to
# the upstream TUI renderer's tmux-scrollback corruption (#253/#267).
import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import zlib
from pathlib import Path


def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_'
    become '-' (matches airuleset's own encode_project_dir verbatim)."""
    return "".join("-" if c in "/._" else c for c in str(cwd))


def find_transcripts(projects_dir, cwd):
    """Every transcript for `cwd`, newest first -- BOTH plain `*.jsonl`
    and gzip-compressed `*.jsonl.gz` (#410: an old, gzip-at-rest
    transcript stays fully discoverable/readable, just smaller on disk),
    sorted TOGETHER by mtime so a mixed set (some compressed, some not)
    still resolves the genuinely newest one regardless of which form it
    is in."""
    d = Path(projects_dir) / encode_project_dir(cwd)
    if not d.is_dir():
        return []
    rows = []
    for pattern in ("*.jsonl", "*.jsonl.gz"):
        for p in d.glob(pattern):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            rows.append((m, p))
    rows.sort(reverse=True)
    return [p for _m, p in rows]


def resolve_pane_cwd(pane_id):
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5)
    except Exception as e:
        print("claude-history: could not resolve pane %r: %s" % (pane_id, e),
              file=sys.stderr)
        return None
    out = (r.stdout or "").strip()
    return out or None


def _read_jsonl(path):
    """Read every JSONL record from `path` -- transparently gunzips when
    the name ends in ".gz" (#410), otherwise the pre-#410 plain-text path
    unchanged. A gzip stream is only validated LAZILY, on its first real
    read -- not at open() time -- so the error catch wraps the iteration
    too, not just the open() call, or a corrupted/truncated `.jsonl.gz`
    would silently look like an EMPTY transcript instead of failing
    loudly (test-strictness.md: a broken dependency must fail, never
    silently succeed).

    #410 review F3 (MAJOR, live-triggered): a truncated or corrupt
    `.jsonl.gz` raises EOFError (compressed file ended before the
    end-of-stream marker) or zlib.error (corrupt stream), NEITHER of
    which is an OSError -- catching only OSError let the exception
    ESCAPE this function uncaught, crashing the whole claude-history
    invocation on ONE bad old transcript (including, under `--full`,
    aborting the render of every OTHER, perfectly healthy chained
    file). Both are caught alongside OSError so a bad file is skipped
    (empty records, a loud stderr line) and the caller's own
    per-file loop continues to the rest."""
    records = []
    is_gz = str(path).endswith(".gz")
    opener = gzip.open if is_gz else open
    try:
        f = opener(path, "rt", encoding="utf-8", errors="replace")
    except OSError as e:
        print("claude-history: cannot read %s: %s" % (path, e), file=sys.stderr)
        return records
    try:
        with f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except (OSError, EOFError, zlib.error) as e:
        print("claude-history: cannot read %s: %s" % (path, e), file=sys.stderr)
        return []
    return records


def _tool_summary(name, inp, max_len=100):
    if name == "Bash":
        val = inp.get("command", "")
    elif name in ("Read", "Write", "Edit", "NotebookEdit"):
        val = inp.get("file_path") or inp.get("notebook_path") or ""
    elif name in ("Grep", "Glob"):
        val = inp.get("pattern", "")
    else:
        val = ", ".join("%s=%r" % (k, v) for k, v in list(inp.items())[:3])
    val = str(val).replace("\n", " ")
    if len(val) > max_len:
        val = val[:max_len - 1] + "…"
    return "%s: %s" % (name, val) if val else name


# #267 adversarial-review finding F5: a bare `text.startswith("<")` also ate
# a genuine user prompt that happens to start with a literal "<" (e.g. "<div>
# why does this render badly?") -- silently DROPPING a real question is
# worse than showing one noise line. Anchor on the actual wrapper tags
# Claude Code injects instead of a bare prefix character.
_WRAPPER_NOISE_PREFIXES = (
    "<local-command-stdout>", "<command-name>", "<command-message>",
    "<task-notification>", "<system-reminder>",
)


def merge_turns(records, seen_uuids=None):
    """Collapse consecutive same-role transcript lines into readable turns:
    {"role": "user"|"assistant"|"compact", "text": str, "tools": [str, ...],
    "ts": str|None}. A real assistant API response is written as SEVERAL
    jsonl lines (one per content block) -- this is display grouping, not
    the #131 request-level token dedup (a different, unrelated concern).
    "ts" (#294) is the ISO timestamp of the record that STARTED the turn --
    captured once, at turn creation, never overwritten by later merged
    lines -- or None when the source record carries no "timestamp" field
    (synthetic test fixtures only; a real transcript always has one).

    #376: this is DELIBERATELY a flat, unconditional walk over every
    record in file order -- it never reads `uuid`/`parentUuid` for branch
    SELECTION at all. Live-verified against a real 4MB/1757-line david2
    transcript carrying 5 real compaction boundaries: this shape already
    renders the file's COMPLETE content (nothing before the first
    compaction, nothing between any pair of compactions, and nothing after
    the last one is ever dropped) -- the acceptance is COMPLETENESS
    (never silently lose data), not picking "the one true branch" out of a
    retried/interrupted turn's orphaned sibling. `seen_uuids` (default
    None -> a fresh set, so every pre-#376 single-file call site keeps
    working unmodified) is a caller-shared set for DEDUPING a `uuid` that
    could otherwise appear more than once -- either within one corrupted/
    retried-write file (a real, previously-hit corruption class in this
    repo, see scripts/repair-session.py) or across several CHAINED session
    files for one project (main()'s own new multi-file chaining, below) --
    first occurrence wins, everything after is skipped outright, before
    any role-specific handling ever runs.

    A `system`/`compact_boundary` record becomes its OWN "compact"-role
    turn (never silently skipped) so render() can mark it readably instead
    of the pre-#376 behavior of dropping it with no trace at all."""
    if seen_uuids is None:
        seen_uuids = set()
    turns = []
    pending = None

    def flush():
        if pending is None:
            return
        text = "\n".join(t for t in pending["texts"] if t).strip()
        if text or pending["tools"]:
            turns.append({"role": pending["role"], "text": text,
                          "tools": pending["tools"], "ts": pending["ts"]})

    for rec in records:
        if not isinstance(rec, dict):
            continue
        uid = rec.get("uuid")
        if isinstance(uid, str) and uid:
            if uid in seen_uuids:
                continue
            seen_uuids.add(uid)
        rtype = rec.get("type")
        if rtype == "system" and rec.get("subtype") == "compact_boundary":
            flush()
            pending = None
            meta = rec.get("compactMetadata")
            pre = meta.get("preTokens") if isinstance(meta, dict) else None
            post = meta.get("postTokens") if isinstance(meta, dict) else None
            turns.append({"role": "compact", "text": "", "tools": [],
                          "ts": rec.get("timestamp"), "pre": pre, "post": post})
            continue
        if rtype == "user":
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str):
                continue  # a tool_result entry, not a real user prompt
            text = content.strip()
            if not text or text.startswith(_WRAPPER_NOISE_PREFIXES):
                continue  # local-command-stdout / injected wrapper noise
            if pending and pending["role"] == "user":
                pending["texts"].append(text)
                continue
            flush()
            pending = {"role": "user", "texts": [text], "tools": [],
                       "ts": rec.get("timestamp")}
        elif rtype == "assistant":
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            texts, tools = [], []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    # #267 F3: a malformed transcript's "text" value can be
                    # anything JSON allows -- only a real string is a real
                    # message; anything else would crash "\n".join() later.
                    t = block.get("text", "")
                    if isinstance(t, str) and t:
                        texts.append(t)
                elif btype == "tool_use":
                    # #267 F3: `input` can be a malformed non-dict shape --
                    # _tool_summary() calls .get() on it unconditionally.
                    inp = block.get("input")
                    if not isinstance(inp, dict):
                        inp = {}
                    tools.append(_tool_summary(block.get("name", "?"), inp))
            if not texts and not tools:
                continue
            if pending and pending["role"] == "assistant":
                pending["texts"].extend(texts)
                pending["tools"].extend(tools)
                continue
            flush()
            pending = {"role": "assistant", "texts": texts, "tools": tools,
                       "ts": rec.get("timestamp")}
        # system / attachment / other entry types: not displayed turns.
    flush()
    return turns


# #294: restrained, muted palette reused verbatim from statusbar.py's own
# established convention (bare "\033[2m" for dim/secondary text, "\033[38;
# 5;<N>m" 256-color codes for accents) rather than inventing a new scheme --
# see the design comment on issue #294 for the full reasoning (why 75/108,
# why body text stays uncolored, why headers are wrapped whole-line).
_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_USER_HDR = "\033[1;38;5;75m"
_ANSI_CLAUDE_HDR = "\033[1;38;5;108m"

_TIMESTAMP_RX = re.compile(r"T(\d{2}:\d{2}:\d{2})")


def _turn_time_suffix(ts):
    """HH:MM:SSZ extracted from a transcript record's ISO "timestamp" field,
    prefixed with a space for direct header-line concatenation -- or "" when
    ts is missing/malformed (never crashes, never prints a "None" literal;
    #294 design comment). The trailing "Z" (#294 adversarial review, MINOR)
    marks the time as UTC explicitly -- a real transcript timestamp always
    is ("...Z" suffix), and a bare "HH:MM:SS" with no marker reads as
    ambiguous local-vs-UTC time; a real timezone CONVERSION was rejected as
    unnecessary complexity for a "decent" (per the ticket's own Slovak
    wording) timestamp display."""
    if not isinstance(ts, str):
        return ""
    m = _TIMESTAMP_RX.search(ts)
    return " %sZ" % m.group(1) if m else ""


def _wrap_plain(text, width):
    """Word-wrap TEXT to WIDTH columns, one PHYSICAL line at a time -- a
    literal "\\n" already in the source is a real paragraph break and is
    never merged into the wrap. `width` <=0 or None is a no-op (matches
    every pre-#376 caller's own behavior unchanged, width-independent).
    `break_long_words=False`/`break_on_hyphens=False`: a single long token
    (a URL, a hash, a path) is never chopped mid-word -- it simply
    overflows that one line rather than being silently corrupted, the same
    "never mangle a token" spirit `_tool_summary`'s own 100-char
    truncation already follows. #376: fixes the popup's own reported
    "scrolls right instead of wrapping" complaint for the TRANSCRIPT-
    reconstruction content -- applied here, to the PLAIN text, BEFORE any
    ANSI color codes are added, so a fold point can never land inside an
    escape sequence (the well-documented `less -R` limitation this
    sidesteps entirely: multiple embedded escape sequences on one line can
    defeat `less`'s own wrap-column tracking)."""
    if not width or width <= 0:
        return text
    out_lines = []
    for line in text.split("\n"):
        if not line:
            out_lines.append(line)
            continue
        wrapped = textwrap.wrap(line, width, break_long_words=False,
                                 break_on_hyphens=False)
        out_lines.extend(wrapped if wrapped else [line])
    return "\n".join(out_lines)


def render(turns, last=None, use_color=False, width=None):
    """#294: colors ADD to the existing plain layout, they never replace
    it -- the "===== USER =====" / "===== CLAUDE =====" header (the clear
    turn separator that pre-dates #294) is wrapped whole-line in the role
    color rather than restructured, tool-call lines and the optional
    timestamp suffix are dimmed, and body TEXT stays uncolored in both
    modes. ANSI codes are non-alphanumeric prefixes/suffixes only -- they
    never splice into the middle of a plain-text substring a caller might
    grep for, so every pre-#294 plain-text assertion still holds even when
    use_color=True.

    #376: a "compact"-role turn (a real `system`/`compact_boundary` record,
    see merge_turns) renders as its own distinct, readably-labelled marker
    -- "----- COMPACTED ... -----", never the "===== USER/CLAUDE ====="
    shape -- so a reader can tell at a glance where the session's own
    context got summarized, instead of the pre-#376 silent skip that left
    no trace of the boundary at all.

    `width` (#376, default None -- no wrap, byte-for-byte the pre-#376
    behavior): word-wraps body TEXT and tool-summary lines to that many
    columns via `_wrap_plain`, applied to the PLAIN string BEFORE any ANSI
    color code is appended -- see `_wrap_plain`'s own docstring for why."""
    if last is not None:
        turns = turns[-last:]
    lines = []
    for t in turns:
        if t["role"] == "compact":
            pre, post = t.get("pre"), t.get("post")
            detail = ""
            if pre is not None or post is not None:
                detail = " (preTokens=%s, postTokens=%s)" % (pre, post)
            header = "----- COMPACTED%s -----" % detail
            if use_color:
                line = _ANSI_DIM + header + _ANSI_RESET
                ts_suffix = _turn_time_suffix(t.get("ts"))
                if ts_suffix:
                    line += _ANSI_DIM + ts_suffix + _ANSI_RESET
            else:
                line = header
            lines.append(line)
            lines.append("")
            continue
        label = "USER" if t["role"] == "user" else "CLAUDE"
        header = "===== %s =====" % label
        if use_color:
            hdr_color = _ANSI_USER_HDR if t["role"] == "user" else _ANSI_CLAUDE_HDR
            line = hdr_color + header + _ANSI_RESET
            ts_suffix = _turn_time_suffix(t.get("ts"))
            if ts_suffix:
                line += _ANSI_DIM + ts_suffix + _ANSI_RESET
        else:
            line = header
        lines.append(line)
        if t["text"]:
            lines.append(_wrap_plain(t["text"], width))
        for tool in t["tools"]:
            tool_line = _wrap_plain("  -> %s" % tool, width)
            if use_color:
                tool_line = _ANSI_DIM + tool_line + _ANSI_RESET
            lines.append(tool_line)
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="claude-history",
        description="Readable Claude Code session history, built from the "
                     "transcript (source of truth -- immune to tmux "
                     "scrollback corruption, airuleset#267).")
    ap.add_argument("--cwd", default=None,
                     help="project directory (default: current directory)")
    ap.add_argument("--pane", default=None,
                     help="tmux pane id -- resolve ITS cwd instead of --cwd")
    ap.add_argument("--transcript", default=None,
                     help="read this transcript file directly")
    ap.add_argument("--last", type=int, default=20,
                     help="show only the last N turns (default 20)")
    ap.add_argument("--full", action="store_true",
                     help="show the whole session (overrides --last)")
    ap.add_argument("--list", action="store_true",
                     help="list available transcripts for this project and exit")
    ap.add_argument("--width", type=int, default=0,
                     help="word-wrap body text/tool lines to this many "
                          "columns (#376); 0 or omitted = no wrap, the "
                          "pre-#376 default")
    color_group = ap.add_mutually_exclusive_group()
    color_group.add_argument("--color", action="store_true",
                              help="force ANSI colors ON even when stdout is "
                                   "piped (e.g. into a pager) -- TTY "
                                   "auto-detection cannot see through a pipe")
    color_group.add_argument("--plain", action="store_true",
                              help="force ANSI colors OFF even on a real "
                                   "terminal (default: colors auto-detect "
                                   "off when piped, on on a real terminal)")
    args = ap.parse_args(argv)
    # #267 F4: `--last 0`/negative would print "showing last N" then
    # actually show something else entirely (Python slice semantics:
    # turns[-0:] is every turn, turns[-3:] drops the wrong end) -- the
    # printed header must never contradict what's actually rendered.
    args.last = max(1, args.last)
    # #294: --color/--plain force the decision explicitly; absent either
    # flag, auto-detect off a real TTY -- a piped subprocess.run(capture_
    # output=True) stdout is never a tty, so every pre-#294 test (and a
    # plain `claude-history | cat`) stays ANSI-free with zero new logic.
    if args.color:
        use_color = True
    elif args.plain:
        use_color = False
    else:
        use_color = sys.stdout.isatty()

    projects_dir = Path.home() / ".claude" / "projects"

    # #376: `--transcript` (explicit single-file, human by-path invocation)
    # keeps its EXACT pre-#376 single-file contract -- never chained. A
    # cwd/pane-resolved lookup can find MULTIPLE `.jsonl` files for one
    # project (`claude-new`'s always-fresh mode, or any other reason a
    # second session id exists) -- under `--full` (the mode prefix+h
    # invokes), ALL of them are chained together, oldest-first, so
    # an older sibling file's own content is never silently dropped just
    # because a newer one exists. `--last` (the default quick-glance mode)
    # deliberately keeps the pre-#376 single-newest-file behavior -- see
    # the #376 design comment on the ticket for why this is scoped to
    # `--full` only (minimize behavioral change / blast radius).
    if args.transcript:
        paths = [Path(args.transcript)]
        chain_all = False
    else:
        if args.pane:
            cwd = resolve_pane_cwd(args.pane)
            if not cwd:
                print("claude-history: pane %r not found or has no cwd" % args.pane,
                      file=sys.stderr)
                return 1
        else:
            cwd = args.cwd or os.getcwd()
        paths = find_transcripts(projects_dir, cwd)
        if not paths:
            print("claude-history: no Claude Code session transcript found for %s"
                  % cwd, file=sys.stderr)
            return 1
        if args.list:
            for p in paths:
                try:
                    when = time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(p.stat().st_mtime))
                except OSError:
                    when = "?"
                print("%s  %s" % (when, p))
            return 0
        chain_all = args.full

    # find_transcripts() returns newest-first (its own established
    # --list ordering, unchanged); chaining needs chronological
    # (oldest-first) order so turns from an older session file are never
    # shown AFTER turns from a newer one.
    #
    # ACCEPTED RESIDUAL (#376 M5, adversarial review, THEORETICAL --
    # never observed live, so left undone under this repo's FREEZE
    # policy rather than chased): this orders files by OS-level MTIME,
    # not by each file's own SESSION-START timestamp. The two normally
    # agree, but a file whose transcript stopped being written to long
    # before a LATER-started sibling file was itself created (e.g. an
    # abandoned/orphaned chain member) could sort out of true
    # chronological order. Fixing it would mean reading the first
    # real entry's `timestamp` out of every candidate file before
    # sorting -- a real, non-trivial change, not a one-line swap;
    # documented here rather than implemented pre-emptively.
    paths = list(reversed(paths)) if chain_all else paths[:1]

    if not any(p.exists() for p in paths):
        print("claude-history: transcript not found: %s" % paths[0], file=sys.stderr)
        return 1

    # #376: `seen_uuids` is shared across every chained file so a `uuid`
    # duplicated across files (or within one corrupted/retried-write file)
    # is rendered exactly once -- see merge_turns's own docstring.
    seen_uuids = set()
    turns = []
    for p in paths:
        if not p.exists():
            continue
        turns.extend(merge_turns(_read_jsonl(p), seen_uuids))
    if not turns:
        print("claude-history: transcript has no displayable turns: %s" % paths[-1],
              file=sys.stderr)
        return 1

    if len(paths) == 1:
        label = str(paths[0])
    else:
        label = "%s (+%d earlier session file%s chained)" % (
            paths[-1], len(paths) - 1, "" if len(paths) == 2 else "s")
    print("# %s" % label)
    if args.full:
        print("# %d turn(s) total" % len(turns))
    else:
        print("# %d turn(s) total -- showing last %d" % (len(turns), args.last))
    print("")
    print(render(turns, None if args.full else args.last, use_color=use_color,
                 width=args.width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_claude_history_script():
    """The claude-history script content -- no template substitution needed
    (unlike render_claude_launch_script), but the same "always render
    through a function, never write the raw constant" discipline (same
    reason as render_caveman_shim/render_claude_launch_script: a future
    templated field must never be forgotten at the one real write site)."""
    return CLAUDE_HISTORY_SCRIPT_CONTENT


# #289: the popup's own logic lives in a SEPARATE SCRIPT FILE, invoked BY
# PATH from the tmux bind-key command line -- never inlined as a shell
# one-liner embedded in the conf. Verified live, tmux 3.7b: tmux's OWN
# conf-file DOUBLE-QUOTE parser expands `$VAR` at CONF-PARSE/bind time
# (using tmux's OWN process environment), not at shell-run time -- so
# `$CH_OUT`/`$CH_RC`/`$?` referenced inline in a double-quoted popup
# command silently blanked to EMPTY STRING before the shell ever ran
# (confirmed via `list-keys`: the bound command showed `if [ "" -ne 0 ]`
# where `"$CH_RC"` should have been). Single-quoted tmux strings do NOT
# expand `$VAR` (also verified live) but tmux's own single-quote parsing
# supports no escapes at all -- embedding a literal `'` (this command's
# own `printf '%s...'` calls need several) would require the POSIX
# quote-splice idiom (`'...'\''...'`, confirmed tmux honours it too) on
# EVERY embedded quote, which is exactly the class of hand-spliced-quoting
# bug this repo's own playbook already warns is fragile and easy to get
# wrong. A script file invoked by its own ABSOLUTE PATH sidesteps the
# whole landmine: the ONLY thing the tmux bind-key line needs to resolve
# is the path itself, baked in at Python RENDER time (this box's own
# `Path.home()`, correct for the user `install`/`push` runs as) -- no
# `$VAR` of any kind needs to survive the conf-parser at all.
CLAUDE_HISTORY_POPUP_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-history-popup.sh"
# HISTORY (kept for the still-relevant TECHNICAL FACTS the current
# fallback below depends on, not because the design they describe is
# still current -- #327 made `tmux capture-pane` this popup's PRIMARY
# source, #337 then split that behavior per-binding, and #376 REVERSES
# both: the transcript reconstruction is unconditionally primary again,
# see the module comment above CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT for
# the current design):
#
# A BARE `tmux capture-pane`/`display-message` call with NO explicit
# `-t`, issued from WITHIN this popup's own shell, resolves against the
# ORIGINATING pane (the one the popup key was pressed in) -- confirmed
# live TWICE, independently: once via an isolated `-L` socket with a real
# attached pty client switched across THREE windows (a decoy window's
# content never leaked into the capture), and again via a fresh-context
# adversarial review's own, stronger repro -- a genuine 2-SESSION/2-CLIENT
# server with the raw popup-key bytes injected into each client's own
# pty, confirming the resolution follows the PRESSING client correctly in
# both directions (#327). The mechanism is `display-popup` setting the
# popup job's own `$TMUX` to the PRESSING client's target session --
# never rely on `$TMUX_PANE` inside a popup as a shortcut for this (its
# value is unreliable/environment-dependent, not a documented tmux
# guarantee); the bare-target resolution above is the only proven path.
# The ONE proven way to break this: adding `-c <client>` to
# `display-popup`, or invoking this script via `run-shell` instead of as
# the popup's own shell-command -- NEVER do either; both were shown live
# to route the capture to the WRONG session's pane. `-e` preserves the
# pane's own real SGR/ANSI bytes; `-S -{{TMUX_HISTORY_LIMIT}}` matches
# TMUX_HISTORY_LIMIT (#235's own scrollback-retention mitigation) so this
# reaches everything tmux's own history buffer could possibly hold --
# still the exact fallback wired into CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT
# below, just no longer the PRIMARY path.
#
# #376: fullscreen is now the
# PRIMARY way to view history (PgUp/PgDn + Ctrl+O, app-internal, survives
# compaction -- see apply_managed_settings_defaults' own docstring). This
# popup is a FALLBACK ONLY, for cross-session / already-closed-pane
# history a live fullscreen scrollback cannot show -- so it no longer
# needs to impersonate the live terminal (#327's whole reason for
# existing) or juggle multiple bind-specific behaviors (#337's MODE
# branching): ONE binding (prefix-h, the only one the user personally
# confirmed opens -- see the module comment above TMUX_POPUP_PREFIX_KEY),
# ALWAYS the complete, hole-free transcript reconstruction, with a real
# `tmux capture-pane` as ITS OWN fallback only when the reconstruction
# itself resolves nothing.
CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT = r'''#!/usr/bin/env bash
# airuleset-managed (do NOT edit) -- claude-history popup companion
# (#289, unconditional transcript-primary fallback by #376). Invoked from
# the managed tmux prefix-h display-popup bind (TMUX_POPUP_BIND_ARGVS in
# airuleset.py) -- fullscreen rendering (PgUp/PgDn, Ctrl+O) is the
# PRIMARY way to view history; this popup is a fallback for cross-session
# history a live fullscreen scrollback can't show. FAILS LOUDLY, never
# silently: on total failure (every source this script tries) the last
# error is shown and the popup waits for a keypress before closing,
# rather than handing `less` empty stdin (which can close instantly with
# nothing to read).
set -euo pipefail

WIDTH="$(tput cols 2>/dev/null)" || WIDTH=0
[ -n "$WIDTH" ] || WIDTH=0

# #376 M1 (adversarial review, measured live on this repo's own real
# project data: ~25s wall / ~817MB peak RSS): the transcript
# reconstruction below can take long enough that, with nothing printed
# first, the popup appears BLANK/FROZEN for the whole window -- print
# this BEFORE starting it, to stderr (never stdout, which `less` will
# render as the final content once the real capture finishes and
# overwrites this line).
printf 'Loading claude-history...\n' >&2

# PRIMARY: the complete, hole-free transcript reconstruction, immune to
# the upstream Claude Code classic-renderer scrollback-duplication
# regression (anthropics/claude-code #84247/#46834, both still open as
# of #376) -- word-wrapped to the popup's own live column width so long
# lines never need horizontal scrolling (#376).
#
# `--color` is forced UNCONDITIONALLY here -- a deliberate REVERSAL of
# #327's own documented popup-neutrality choice (that ticket forced
# `--plain` specifically so the popup's rendering never impersonated a
# real terminal's exact colors). #376 no longer needs that neutrality:
# the popup is now a FALLBACK only (never claiming to mirror the live
# pane), so real color is a strict readability upgrade with nothing to
# stay neutral about.
#
# `set -e` + `VAR=$(failing_cmd)` would otherwise exit this script BEFORE
# the next line ever runs (a failing command substitution used in a plain
# assignment is an unhandled failure under -e) -- the `|| <NAME>_RC=$?`
# form is the established fix: it captures the real exit code without
# tripping -e, and each RC stays unset (defaulted to 0 below) on its own
# success path.
CH_OUT=$(python3 "$HOME/.claude/airuleset-claude-history.py" --full --color --width "$WIDTH" 2>&1) || CH_RC=$?
CH_RC="${CH_RC:-0}"
# The fallback triggers on EITHER a nonzero exit OR an empty result -- RC
# alone would miss the real "rc=0 but $(...) stripped the whole output to
# an empty string" case (claude-history returning nothing displayable),
# the exact mirror of a finding this repo's own playbook already records
# for the sibling #327 ticket's capture-pane-blank-pane case.
if [ "$CH_RC" -ne 0 ] || [ -z "$CH_OUT" ]; then
  TRANSCRIPT_OUT="$CH_OUT"
  # FALLBACK: a real tmux capture-pane of the ORIGINATING pane, for the
  # rare case the transcript reconstruction itself produces nothing at
  # all (no readable transcript file, e.g. a genuinely empty project). A
  # bare (no `-t`) capture-pane call issued from WITHIN a display-popup's
  # own shell-command resolves against the pane the popup key was
  # pressed in, never the popup's own new pseudo-pane -- verified live,
  # twice, independently (see the module comment above TMUX_POPUP_PREFIX_KEY in
  # airuleset.py). `-e` preserves the real colors/escape sequences the
  # pane actually rendered; `-p` prints to stdout for this command-
  # substitution capture; `-S -{{TMUX_HISTORY_LIMIT}}` reaches back
  # across the FULL configured scrollback -- the SAME value as the
  # managed history-limit itself (never a second hardcoded literal that
  # could silently drift shorter than what tmux actually retains).
  CP_OUT=$(tmux capture-pane -e -p -S -{{TMUX_HISTORY_LIMIT}} 2>&1) || CP_RC=$?
  CP_RC="${CP_RC:-0}"
  if [ "$CP_RC" -eq 0 ] && [ -n "$CP_OUT" ]; then
    CH_OUT="$CP_OUT"
    CH_RC=0
  else
    # M5 guard: BOTH sources genuinely failed/produced nothing -- fail
    # loudly with both diagnostics shown, never a silent instant-close.
    CH_OUT="claude-history (transcript, primary) produced nothing:
${TRANSCRIPT_OUT}

tmux capture-pane (fallback) also produced nothing:
${CP_OUT}"
    CH_RC=1
  fi
fi

if [ "$CH_RC" -ne 0 ]; then
  printf '%s\n\nclaude-history: press any key to close.\n' "$CH_OUT"
  read -n 1 -r -s _dummy || true
elif ! command -v less >/dev/null 2>&1; then
  # ADVERSARIAL-REVIEW FINDING (#289, M5): a box genuinely missing `less`
  # would otherwise hand the successfully-read transcript to a nonexistent
  # command, closing instantly with no visible cause -- the exact silent
  # instant-close this script's own header promises never to do. `less`
  # is tracked in RUNTIME_DEPS and installed fleet-wide, but this is the
  # box's own last-resort guard should it still be missing somehow.
  printf '%s\n\nclaude-history: "less" is not installed on this box.\n\npress any key to close.\n' "$CH_OUT"
  read -n 1 -r -s _dummy || true
else
  # #294: -R makes `less` render raw ANSI color bytes as color instead of
  # visibly escaping them; +G (jump to end) and less's own default
  # incremental search are both unaffected by -R.
  printf '%s\n' "$CH_OUT" | less -R +G
fi
'''


def render_claude_history_popup_script(limit=None):
    """The popup-script content, with the `{{TMUX_HISTORY_LIMIT}}`
    placeholder substituted. Default resolved INSIDE the body (never as a
    parameter default) since TMUX_HISTORY_LIMIT is defined LATER in this
    module than this function -- a parameter default is evaluated at
    function-DEFINITION time, which would raise NameError at import time.
    #376 dropped the `mode_transcript_primary` param/placeholder entirely
    -- the script is now unconditionally transcript-primary (see the
    module comment above CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT), so there
    is nothing left to select between."""
    if limit is None:
        limit = TMUX_HISTORY_LIMIT
    return CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT.replace(
        "{{TMUX_HISTORY_LIMIT}}", str(limit))
