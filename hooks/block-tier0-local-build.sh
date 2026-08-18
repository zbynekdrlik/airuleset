#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a heavy LOCAL build in a Tier-0 project.
#
# no-local-builds.md: Tier 0 (the default) bans ALL local cargo COMPILATION —
# #557 (owner directive 2026-08-18, "reported ~100x"): "Tier-0 = ŽIADNA lokálna
# cargo kompilácia". EVERY compiling cargo shape blocks -> CI: build/test/bench/
# run/check/clippy/doc/rustc/install/... , narrow AND whole-workspace, `--no-run`
# or not. Only a curated NON-compiling class stays local (fmt, clean, metadata,
# tree, search, update, add, version, help, config, ...). Detection is an
# ALLOWLIST inversion (`_cargo_compiles`, deny-by-default): an unknown / third-
# party cargo subcommand (tarpaulin / miri / llvm-cov / nextest / ...) fails SAFE
# to blocked, so the blocklist coverage gap that let camera-box compile locally
# even AFTER #544 -- scoped `cargo test --no-run --test <name>` / `--lib`, plus
# `cargo clippy` / `cargo check` / `cargo doc` which were never matched at all
# (forensics on issue #557) -- cannot re-open. Non-cargo heavy builds
# (`cargo tauri build` / `trunk build` / `wasm-pack build` / `cmake --build`)
# still block too. Tier 1 (`airuleset:local-builds=allowed`) and Tier 2
# (`airuleset:local-builds=fast-iterate`) projects, declared by a marker in their
# CLAUDE.md, are EXEMPT -- and their behaviour is UNCHANGED by #557: a marker
# exempts every heavy command AFTER heaviness is decided, and tier-1/2 already
# blocked nothing, so widening what counts as heavy cannot change what they allow.
#
# This ENFORCES the ban (the rule alone let presenter's `target/` balloon to 97 GB
# on dev2). Reads the tool payload on STDIN (`.tool_input.command` + `.cwd`).
# Exit 2 = block the tool call (stderr shown to the agent); exit 0 = allow.
#
# #381 (~25GB regrowth on dev1 despite this hook existing since b25a893) added
# THREE things, none of them a change to the direct cargo-build/test matching
# below (that part already worked correctly -- STEP 0's own investigation,
# issue #381 comment, ruled out a regex bug):
#
#   Shape A -- the `# airuleset:build-ok <reason>` one-off escape hatch is
#     sanctioned and STAYS sanctioned (agents genuinely need it sometimes --
#     e.g. a Playwright E2E that needs a running server binary), but it was
#     completely unaccountable: nothing ever logged who used it, when, or
#     why. Every BLOCK and every BYPASS of a genuinely heavy command is now
#     appended to a durable audit log (mirrors block-destructive-remote.sh's
#     own AUDIT_LOG convention). A marker/env bypass on a command that was
#     never heavy in the first place is NOT logged -- that's not the gap.
#
#   Shape B -- a heavy build hidden INSIDE an invoked wrapper/deploy script
#     (dantesync/install.sh:72's unconditional `cargo build --release`) was
#     invisible: `is_heavy()` only ever pattern-matched the literal
#     `tool_input.command` text, and `bash install.sh` / `sudo ./install.sh`
#     never contains the substring "cargo build" itself. When the command
#     text mentions a local `.sh` path (cheap grep pre-filter -- the python3
#     extraction below only runs when that pre-filter hits, so the common
#     case pays nothing extra), any `bash`/`sh`/`source`/`.`/direct-exec
#     invocation of a LOCAL script is resolved (relative to the tool's own
#     cwd) and, if readable, ITS content is scanned with the same
#     `is_heavy()` check -- ONE level deep only (a script invoking a further
#     script is not followed; same "heuristic, not a full shell parser"
#     rigor as block-destructive-remote.sh's own documented KNOWN GAPS). A
#     script that doesn't exist or can't be read is skipped, never guessed.
#
#   Shape C -- found while designing the Shape-A fix: the marker check
#     itself was quote-UNAWARE (`case "$CMD" in *"airuleset:build-ok"*)`),
#     so the marker text merely being MENTIONED inside an unrelated quoted
#     string (a commit message, an echo) incorrectly bypassed a REAL,
#     unrelated heavy command chained on the same command line -- the exact
#     class of bug block-destructive-remote.sh already documents fixing for
#     its own `# airuleset:destructive-ok` marker. The marker is now matched
#     against the quote-stripped command, same as the build-detection regex
#     already was.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

AUDIT_LOG="${AIRULESET_TIER0_AUDIT_LOG:-$HOME/devel/airuleset/audits/tier0-build-bypasses.log}"

_log_tier0_event() {
    # $1 = event tag ("blocked" / "inline-bypass" / "env-bypass"), only
    # ever called once heaviness (direct or via a scanned script) is
    # already established -- never for a non-heavy command, so the log
    # stays a signal of actual bypass/block activity, not marker noise.
    #
    # #381-review CRITICAL-1: this function is BEST-EFFORT ONLY and must
    # NEVER be able to change the hook's own exit code. Under
    # `set -euo pipefail`, an unguarded `mkdir`/`echo >>` failure (an
    # unwritable audit dir, ENOSPC -- the exact disk-full scenario this
    # ticket exists to fix, or a single self-triggerable `mkdir -p
    # <the-log-path>` making it a directory) would abort the shell BEFORE
    # the caller's own `exit 2`/`exit 0` runs; Claude Code treats a hook
    # exiting anything other than 0/2 as a HOOK ERROR and runs the
    # blocked command anyway (#118/#196) -- reopening the exact silent
    # target/ growth #381 exists to close. Every internal step is
    # therefore `2>/dev/null || true`, and every CALL SITE below ALSO
    # wraps the call itself in `|| true` as a second, independent guard
    # (so a future edit that drops one of the internal guards still
    # cannot re-open this hole).
    local tag="$1"
    local base="${CWD:-$PWD}"
    local project
    project=$(basename "$(cd "$base" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "$base")" 2>/dev/null) || project="unknown"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    # #381-review MAJOR-3: collapse to ONE physical line -- a newline in
    # $CMD (a heredoc, a deliberately crafted multi-line payload) must
    # never be able to split/forge the record into extra, attacker-
    # shaped log lines (the same audit-channel-injection class
    # block-vault-store-read.sh already documents fixing).
    local cmd_flat
    cmd_flat=$(printf '%s' "$CMD" | tr '\n\r\t' '   ')
    { echo "$(date -Iseconds)  project=$project  $tag  cmd=${cmd_flat}" >> "$AUDIT_LOG"; } 2>/dev/null || true
}

# #477: heavy tests/builds in the camera-box repo go through CI ONLY -- the
# ad-hoc `# airuleset:build-ok` marker AND the AIRULESET_ALLOW_LOCAL_BUILD env
# var are DISABLED there (workers ran local `cargo test` 247x/2 days through the
# marker, overloading the shared dev1 box; user decision 2026-08-14). The
# DELIBERATE project-level Tier-1/2 opt-in (walked further down) is UNAFFECTED --
# only the two ad-hoc per-command bypasses are removed, and only for camera-box.
#
# Detection is by the repo's AUTHORITATIVE identity: `git remote get-url origin`
# basename == camera-box, matched case-INSENSITIVELY (git resolves repo names
# case-insensitively) -- the same repo-name convention notify.repo_name_for
# uses (a repo's identity is its remote, never its directory basename). A
# resolved remote is decisive in BOTH directions: a DIFFERENT name wins over
# any `camera-box` path component (so a non-camera-box repo is never over-
# blocked), and a camera-box remote is caught even inside a renamed checkout OR
# a git worktree (a worktree shares its main checkout's origin, so it resolves
# via the remote path, not the fallback). ONLY when no remote resolves at all
# (a detached / no-origin checkout) does a `camera-box` path component decide.
# Two accepted residuals, both with no shape on the fleet: only `origin` is
# consulted (a camera-box checkout on a non-origin remote name falls to the
# path check -- notify.repo_name_for is origin-only too), and a genuinely
# no-origin repo that merely lives under a `camera-box/` ancestor is treated as
# camera-box. Fails toward NOT-camera-box on any other ambiguity.
_is_camera_box_repo() {
    local base="$1" url name
    [ -z "$base" ] && return 1
    url=$(cd "$base" 2>/dev/null && git remote get-url origin 2>/dev/null) || url=""
    if [ -n "$url" ]; then
        # strip a trailing slash, then the `.git` suffix, then any remaining
        # trailing slash, then reduce to the last path (`/`) or scp-host (`:`)
        # segment -- handles `git@h:o/n.git`, `https://h/o/n.git`, `…/n.git/`.
        name="${url%/}"; name="${name%.git}"; name="${name%/}"
        name="${name##*/}"; name="${name##*:}"
        [ "${name,,}" = "camera-box" ] && return 0
        return 1   # remote resolved to a DIFFERENT repo -> authoritatively not camera-box
    fi
    case "/$base/" in
        */camera-box/*) return 0 ;;
    esac
    return 1
}

# #557: ALLOWLIST inversion -- on a Tier-0 repo EVERY compiling `cargo` shape is
# heavy; only a curated NON-compiling class stays local. This REPLACES the old
# per-verb blocklist (`cargo build`/`test`/`run`/`bench`/`mutants`/`nextest run`)
# and its scoped-`--no-run` carve-out (`_gated_shape_is_heavy`, #471/#544, now
# deleted): forensics on issue #557 proved the blocklist kept leaking exactly the
# narrow/unlisted shapes -- scoped `cargo test --no-run --test <name>` / `--lib`
# (explicitly exempted by #544), and `cargo clippy` / `cargo check` / `cargo doc`
# (never matched at all) -- each of which still compiles the whole dep tree into a
# 1+ GB/lane `target/`. Deny-by-default fails SAFE: any subcommand NOT in the
# non-compiling allowlist (an unknown / third-party compiling plugin like
# `tarpaulin` / `miri` / `llvm-cov` / `nextest`, or a future one) blocks -> CI.
#
# python exit 0 = a compiling cargo subcommand is present in command position;
# non-zero = none. Any python failure returns non-zero -- the caller treats that
# exactly like the pre-#557 not-heavy path (the OTHER heavy checks -- tauri /
# trunk / wasm-pack / cmake -- are still plain bash grep, so cargo detection
# degrading toward allow on a broken python3 is the same fail-direction the
# deleted `_gated_shape_is_heavy` used, never a false block).
#
# Accepted residuals (documented per the #319 convention; all rare, off the
# well-meaning-agent threat model, and either fail-SAFE over-block or a
# pre-#557-INHERITED under-block -- never a NEW leak of a natural cargo compile):
#   - a wrapper's NON-numeric VALUE flag (`sudo -u builder cargo build`,
#     `xargs -I {} cargo build`) -- the wrapper-value skip only consumes flags +
#     NUMERIC values, so the value token is mistaken for the command word and the
#     compile is missed. The COMMON forms (`sudo cargo`, `xargs cargo build`,
#     `timeout 300 cargo`, `nice -n 19 cargo`) all block.
#   - a cargo compile hidden inside a nested interpreter (`bash -c "cargo build"`,
#     `eval "cargo build"`) -- `strip_quotes` (the CALLER) removes the quoted
#     invocation before this ever runs, so it is invisible. PRE-EXISTING (the old
#     blocklist hook missed it identically); closing it needs interpreter-body
#     parsing (cf. block-fork-no-merge-issue-close.sh #540), out of scope here.
#   - the `cargo-<sub>` HYPHEN standalone-binary form (`cargo-nextest run`) is
#     only caught for the space form (`cargo nextest run`) -- same as the
#     pre-#557 nextest grep; `cargo(?=\s|$)` deliberately does not match a hyphen.
#   - `cargo build --help` / `cargo test --help` OVER-block (they print help, no
#     compile) -- INFO-flag detection only fires when the info flag PRECEDES the
#     subcommand; fail-SAFE and pre-#557 (the old hook blocked these too).
_cargo_compiles() {
    python3 - "$1" 2>/dev/null <<'PYEOF'
import re
import sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

# The ONLY cargo subcommands that do NOT compile the crate/dep tree. Everything
# else (build/b/test/t/bench/run/r/check/c/clippy/doc/rustc/rustdoc/install/
# publish/package/fix/expand/tarpaulin/miri/llvm-cov/nextest/mutants/... AND any
# unknown subcommand) is compiling -> heavy on Tier-0.
NONCOMPILING = {
    "fmt", "clean", "metadata", "tree", "search", "update", "add", "remove",
    "rm", "generate-lockfile", "locate-project", "pkgid", "verify-project",
    "read-manifest", "login", "logout", "owner", "yank", "version", "help",
    "config", "report", "new", "init", "vendor", "fetch", "uninstall",
}
# `cargo --version`/`-V`/`--help`/`-h`/`--list`/`--explain <code>` print info and
# do not compile -> treated as non-compiling.
INFO_FLAGS = {"--version", "-V", "--help", "-h", "--list", "--explain"}
# a couple of global flags that take a VALUE -- skip the value so it is not
# mis-read as the subcommand.
VALUED_FLAGS = {"--color", "--config"}
# wrapper commands that may PRECEDE `cargo` in command position; a segment whose
# real command word is NONE of these AND not cargo is some OTHER command
# (`grep cargo file`, `man cargo build`, `which cargo`) -> NOT a cargo compile.
PREFIX_CMDS = {"sudo", "env", "time", "nice", "timeout", "nohup", "stdbuf",
               "setsid", "ionice", "chrt", "command", "exec", "doas", "xargs"}
# shell keywords / group openers that precede a command in a segment
# (`do cargo run`, `then cargo build`, `{ cargo test`, `! cargo run`).
SHELL_KW = {"do", "then", "else", "elif", "{", "!"}
NUMVAL = re.compile(r"^\d+[smhd]?$")   # a timeout/nice numeric value (300, 5m)
ASSIGN = re.compile(r"^\w+=")          # an env-assignment prefix (RUSTFLAGS=x)
META = re.compile(r"[;&|()<>]")


def split_top_level(text):
    # QUOTE-AWARE top-level split on &&/||/;/&/|/newline (from
    # block-ungated-issue-filing.sh; the input is already quote-stripped so the
    # quote-awareness is belt-and-suspenders, but it also inherits the backslash
    # handling). Scopes command-word detection to the actual command segment.
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|", "(", ")", "\n"):   # incl. subshell / cmd-subst boundaries
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def cargo_sub(seg):
    # Return the effective cargo subcommand of `seg` IFF cargo is the command
    # word (after env-assignments + wrapper prefixes), else None. "__INFO__" for a
    # non-compiling info flag (`--version`/`--help`/`--list`/`--explain`).
    toks = seg.split()
    n = len(toks)
    i = 0
    while i < n:                       # walk the command prefix to the real cmd
        t = toks[i]
        if ASSIGN.match(t):            # RUSTFLAGS=x cargo ...
            i += 1
            continue
        if t in SHELL_KW:              # do/then/else/{/! cargo ...
            i += 1
            continue
        if t in PREFIX_CMDS:           # sudo/env/timeout/nice/... cargo ...
            i += 1
            while i < n and (toks[i].startswith("-") or NUMVAL.match(toks[i])):
                i += 1                 # consume the wrapper's flags + numeric vals
            continue
        break
    if i >= n or toks[i] != "cargo":
        return None                    # some OTHER command, or no command word
    k = i + 1
    while k < n:                       # parse the cargo subcommand
        t = toks[k]
        if t.startswith("+"):          # +toolchain override
            k += 1
            continue
        if t in INFO_FLAGS:
            return "__INFO__"
        if t in VALUED_FLAGS:          # skip the flag AND its value
            k += 2
            continue
        if t.startswith("-"):          # any other leading global flag
            k += 1
            continue
        return META.split(t, 1)[0]     # first non-flag token = subcommand
    return None                        # bare `cargo` -> prints help, non-compiling


for seg in split_top_level(cmd):
    sub = cargo_sub(seg)
    if not sub or sub == "__INFO__":
        continue
    if sub in NONCOMPILING:
        continue
    sys.exit(0)   # heavy: a compiling cargo subcommand in command position
sys.exit(1)
PYEOF
}

# Is it a HEAVY build?  Non-cargo heavy builds stay plain bash greps; ALL cargo
# COMPILATION (#557) is delegated to the allowlist `_cargo_compiles`.
is_heavy() {
    local c="$1"
    # Non-cargo heavy builds. The tauri/trunk/wasm-pack shapes have NO closing
    # anchor -- they match a prefix regardless of the trailing char. `cargo tauri
    # build` (space form) ALSO trips `_cargo_compiles` below (sub `tauri` is not
    # in the allowlist), so this grep is what additionally catches the standalone
    # `cargo-tauri build` (hyphen) binary that `_cargo_compiles` cannot see.
    printf '%s' "$c" | grep -qE 'cargo[- ]tauri[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])trunk[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE 'wasm-pack[[:space:]]+build' && return 0
    # `cmake --build <dir>` -- the vendored-libobs C build documented for local
    # dev1 use (vendor/BUILD.md). The `cmake -S . -B` configure step is light
    # and is NOT matched -- only `--build` is heavy.
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cmake[[:space:]]+--build([[:space:]]|$|[;&|)(<>])' && return 0
    # #557: ANY compiling `cargo` subcommand -> heavy (allowlist inversion). Cheap
    # pre-filter: only spawn python for a command that actually mentions `cargo`
    # as a word, so the hot path (every Bash command WITHOUT cargo) pays nothing
    # beyond this one grep and never forks python.
    if printf '%s' "$c" | grep -qwE 'cargo'; then
        _cargo_compiles "$c" && return 0
    fi
    return 1
}

# Strip quoted substrings, so a build command (or the bypass marker) MENTIONED
# inside a string (a git commit message, an echo) is NOT matched — only a
# real command position.
strip_quotes() {
    printf '%s' "$1" | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g"
}

STRIPPED=$(strip_quotes "$CMD")

CMD_IS_HEAVY=1
is_heavy "$STRIPPED" || CMD_IS_HEAVY=0

# Shape B: only pay for script discovery when the command text mentions a
# local .sh path at all — the overwhelmingly common case (no .sh reference)
# costs nothing beyond this one grep.
HEAVY_SCRIPT=""
if [ "$CMD_IS_HEAVY" = 0 ] && printf '%s' "$STRIPPED" | grep -qE '\.sh\b'; then
    SCRIPT_PATHS=$(python3 - "$STRIPPED" <<'PYEOF' 2>/dev/null || true
import re, sys
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
paths = set()
for m in re.finditer(r'(?:^|[;&|(]|\s)(?:sudo\s+)?(?:bash|sh|source|\.)\s+(\S+\.sh)\b', cmd):
    paths.add(m.group(1))
# #381-review MAJOR-2: a direct-exec `./x.sh`/`/abs/x.sh` path must sit at
# a genuine COMMAND POSITION -- the old boundary accepted bare whitespace,
# which also matches a script merely being an ARGUMENT to an unrelated
# command (`cat ./install.sh`, `grep cargo ./install.sh`) and wrongly
# blocked reading/inspecting a script that was never actually invoked.
# Require an explicit separator (`;`/`&`/`|`/`(`/newline) or start-of-
# string, optionally followed by `sudo `/`time `/`env VAR=val `.
for m in re.finditer(
    r'(?:^|[;&|(\n])\s*(?:sudo\s+|time\s+|env\s+\S+=\S+\s+)*(\.{0,2}/\S+\.sh)\b',
    cmd):
    paths.add(m.group(1))
for p in sorted(paths):
    print(p)
PYEOF
    )
    if [ -n "$SCRIPT_PATHS" ]; then
        BASE_DIR="${CWD:-$PWD}"
        while IFS= read -r sp; do
            [ -z "$sp" ] && continue
            case "$sp" in
                "~"*) sp="${HOME}${sp#\~}" ;;
            esac
            case "$sp" in
                /*) resolved="$sp" ;;
                *)  resolved="$BASE_DIR/$sp" ;;
            esac
            if [ -f "$resolved" ] && [ -r "$resolved" ]; then
                SCRIPT_STRIPPED=$(strip_quotes "$(cat "$resolved" 2>/dev/null || true)")
                if is_heavy "$SCRIPT_STRIPPED"; then
                    HEAVY_SCRIPT="$resolved"
                    break
                fi
            fi
        done <<EOF_SCRIPTS
$SCRIPT_PATHS
EOF_SCRIPTS
    fi
    [ -n "$HEAVY_SCRIPT" ] && CMD_IS_HEAVY=1
fi

[ "$CMD_IS_HEAVY" = 0 ] && exit 0

# #477: is this the camera-box repo? If so, the two ad-hoc bypasses below are
# NOT honoured -- the command falls through to the Tier-0 walk-and-block, which
# points at CI. (The deliberate project Tier-1/2 opt-in still exempts.)
CAMERA_BOX=0
if _is_camera_box_repo "${CWD:-$PWD}"; then CAMERA_BOX=1; fi

# Deliberate one-off bypass (a real reason to build locally just this once) —
# still sanctioned, now ACCOUNTABLE: every use on a genuinely heavy command
# is appended to the audit log instead of vanishing silently. EXCEPT for
# camera-box (#477), where neither ad-hoc bypass is honoured at all.
if [ "$CAMERA_BOX" != 1 ]; then
    case "$STRIPPED" in
        *"airuleset:build-ok"*)
            _log_tier0_event "inline-bypass" || true
            exit 0
            ;;
    esac
    if [ "${AIRULESET_ALLOW_LOCAL_BUILD:-0}" = "1" ]; then
        _log_tier0_event "env-bypass" || true
        exit 0
    fi
fi

# Heavy build (directly, or via an invoked script). Walk cwd → / for the
# project's CLAUDE.md. A Tier-1/2 allow marker → EXEMPT (deliberate, visible
# project-level config — not the accountability gap, not logged). A
# CLAUDE.md with NO marker → Tier 0 → block. No CLAUDE.md anywhere → not a
# managed project → don't enforce.
dir="${CWD:-$PWD}"
found=0
while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/CLAUDE.md" ]; then
        found=1
        grep -qE 'airuleset:local-builds=(allowed|fast-iterate)' "$dir/CLAUDE.md" 2>/dev/null && exit 0
        break
    fi
    dir=$(dirname "$dir")
done
[ "$found" = 0 ] && exit 0

_log_tier0_event "blocked" || true

if [ "$CAMERA_BOX" = 1 ]; then
    echo "BLOCKED: local cargo COMPILATION in the camera-box repo (no-local-builds.md, airuleset #477/#557). Tier-0 = ZERO local cargo compilation — EVERY compiling cargo shape (build/test/bench/run/check/clippy/doc/… , scoped or whole-workspace, --no-run or not) runs in CI ONLY. The '# airuleset:build-ok' marker and AIRULESET_ALLOW_LOCAL_BUILD are DISABLED for camera-box. Locally you may run only NON-compiling cargo (cargo fmt / metadata / tree / clean / update); let CI compile + test." >&2
elif [ -n "$HEAVY_SCRIPT" ]; then
    echo "BLOCKED: local cargo COMPILATION hidden inside invoked script '$HEAVY_SCRIPT' in a Tier-0 project (no-local-builds.md, #557). Tier-0 = ZERO local cargo compilation — every compiling cargo shape (build/test/bench/run/check/clippy/doc/…) runs in CI. Locally run only NON-compiling cargo (fmt / metadata / tree / clean). To build locally on purpose: make the project Tier 1 ('<!-- airuleset:local-builds=allowed -->' in its CLAUDE.md) or Tier 2 ('/fast-iterate on'), or append '# airuleset:build-ok' to this one command." >&2
else
    echo "BLOCKED: local cargo COMPILATION in a Tier-0 project (no-local-builds.md, #557). Tier-0 = ZERO local cargo compilation — EVERY compiling cargo shape (build/test/bench/run/check/clippy/doc/rustc/install/… , narrow AND whole-workspace, --no-run or not) runs in CI. Locally run only NON-compiling cargo (cargo fmt / metadata / tree / clean / update). To build locally on purpose: make the project Tier 1 ('<!-- airuleset:local-builds=allowed -->' in its CLAUDE.md) or Tier 2 ('/fast-iterate on'), or append '# airuleset:build-ok' to this one command." >&2
fi
exit 2
