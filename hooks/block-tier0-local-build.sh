#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a heavy LOCAL build in a Tier-0 project.
#
# no-local-builds.md: Tier 0 (the default) bans heavy local builds — `cargo build`
# / `cargo build --release` / `cargo test` (runs tests) / `cargo tauri build` /
# `trunk build` / `wasm-pack build` — locally; only the cheap compile-checks
# (`cargo check`, `cargo clippy`, and a SCOPED `cargo test --no-run -p <crate>`/
# `--lib`/`--test <name>`) are allowed, and full / release builds — INCLUDING a
# FULL-WORKSPACE `cargo test --no-run` that compiles the whole test suite (#544)
# — run in CI. Tier 1 (`airuleset:local-builds=allowed`) and Tier 2
# (`airuleset:local-builds=fast-iterate`) projects, declared by a marker in their
# CLAUDE.md, are EXEMPT.
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

# #471 (F2): segment-scoped `--no-run` exemption for the `--no-run`-gated cargo
# shapes (`test`/`bench`). The old whole-command `--no-run` grep wrongly exempted
# a genuine RUN sitting in a DIFFERENT top-level segment (`cargo test --no-run &&
# cargo bench`, `cargo bench --no-run && cargo bench`) and matched `--no-run` as a
# SUBSTRING of an unrelated harness arg (`cargo bench -- --no-run-decoy`). This
# reuses the repo's quote-aware `split_top_level` splitter (from
# hooks/block-ungated-issue-filing.sh, itself from block-gh-invalid-json-flag.sh
# -- copied inline per this repo's per-hook-inline-Python convention; a bash hook
# has no shared library to import from) to scope the exemption to the segment the
# invocation actually sits in: HEAVY iff some top-level segment invokes
# `cargo <sub>` in command position WITHOUT a WHOLE-flag `--no-run` in THAT same
# segment. It runs ONLY when the caller's outer per-shape grep already matched AND
# a whole-flag `--no-run` is actually present, so the hot path (every Bash command)
# pays nothing extra.
#
# Accepted residual (#471-review, THEORETICAL/MINOR): a `--no-run` that follows a
# harness `--` separator (`cargo test -- --no-run>log`) is regex-indistinguishable
# from the real cargo flag and now exempts. It is a pre-existing decoy class (the
# space/EOL form `cargo test -- --no-run` already exempted), unrunnable (libtest
# rejects `--no-run` as a harness arg), and closing it needs `--`-position parsing
# -- out of scope for this symmetric-widening fix.
_gated_shape_is_heavy() {
    # $1 = command (quotes already stripped by the caller); $2 = subcommand
    # (`test` / `bench`). python exit 0 = heavy. Any python failure returns
    # non-zero -- the caller then treats this exactly like the pre-#471 exempt
    # path (allowed), never a fail-toward-block, so a broken python3 can never
    # false-block a real `cargo test --no-run`.
    python3 - "$1" "$2" 2>/dev/null <<'PYEOF'
import re
import sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
sub = sys.argv[2] if len(sys.argv) > 2 else ""


def split_top_level(text):
    # QUOTE-AWARE top-level split on &&/||/;/&/|/newline, reused verbatim from
    # hooks/block-ungated-issue-filing.sh's own split_top_level. The input is
    # already quote-stripped by the caller, so the quote-awareness is
    # belt-and-suspenders; reusing the same splitter also inherits its
    # backslash-escape handling.
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
        if c in (";", "&", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


anchor = re.compile(r"(^|[;&|(\s])cargo\s+" + re.escape(sub) + r"(\s|$|[;&|)(<>])")
# #471-review: the --no-run boundary is widened to the SAME metachar class as
# the heavy-token anchors, so a `--no-run` glued to a trailing `;`/`)`/`|`/... is
# still recognized as the compile-only flag (a `--no-run-decoy` harness arg,
# followed by `-`, still does NOT match and stays heavy).
norun = re.compile(r"(^|\s)--no-run(\s|$|[;&|)(<>])")
# #544: a `cargo test/bench --no-run` is the allowed Tier-0 cheap check ONLY when
# it is SCOPED -- a WHOLE-WORKSPACE compile-only build compiles every test binary
# in the workspace (dantesync 6.9 GB) and is heavy in spirit ("heavy compiles run
# in CI"), so it blocks -> CI just like `cargo build`. A segment carrying
# `--no-run` is HEAVY iff ANY of:
#   - an explicit whole-workspace flag `--workspace`/`--all` (overrides any
#     narrowing flag -- `--workspace --lib` compiles every member's lib tests);
#   - a BROAD PLURAL target selector `--bins`/`--tests`/`--examples` with NO
#     `-p`/`--package` scope (all-of-kind across the whole workspace); a
#     PACKAGE-scoped plural `-p x --tests` stays allowed -- same weight as the
#     already-allowed `-p x`, so we never block one while allowing the other;
#   - NO narrowing flag at all (the bare/default whole-workspace compile).
# Narrowing flags, word-boundary EXACT (same trailing metachar class as the
# anchors above, so `--lib;`/`-p x|tee` stay scoped): -p / --package / --lib /
# --bin / --test / --example / --bench / --doc. A positional test-NAME filter
# narrows which tests RUN, never which COMPILE, so it is correctly NOT narrowing.
# #544-review: the scan stops at the first standalone `--` (harness separator),
# so a libtest arg after `--` (`-- --lib`, `-- --workspace`) is NEVER read as a
# cargo target/scope flag -- this both blocks `cargo test --no-run -- --lib` (a
# real full compile) and avoids false-BLOCKING `-p x -- --workspace` (a scoped
# build whose harness arg merely spells a broadening flag). Accepted residuals
# (fail-SAFE over-block or monotonic vs pre-#544, all rare): a narrowing token
# as the unquoted VALUE of a non-narrowing flag (`--config --lib`) false-allows
# (needs flag-value parsing); a glued `-pmycrate` / a quoted bare `"--lib"`
# over-block (safe; the common `-p x`/`-p=x`/`-p "my-crate"` all allow).
narrowing = re.compile(
    r"(^|\s)(--package|--lib|--bin|--test|--example|--bench|--doc|-p)(=|\s|$|[;&|)(<>])")
explicit_broad = re.compile(r"(^|\s)(--workspace|--all)(=|\s|$|[;&|)(<>])")
broad_plural = re.compile(r"(^|\s)(--bins|--tests|--examples)(=|\s|$|[;&|)(<>])")
pkg_scope = re.compile(r"(^|\s)(--package|-p)(=|\s|$|[;&|)(<>])")
double_dash = re.compile(r"(^|\s)--(\s|$)")
for seg in split_top_level(cmd):
    if not anchor.search(seg):
        continue
    if not norun.search(seg):
        sys.exit(0)   # heavy: a real run with no --no-run in its own segment
    # only cargo flags BEFORE a harness `--` separator count as scope/breadth
    dd = double_dash.search(seg)
    pre = seg[:dd.start()] if dd else seg
    if explicit_broad.search(pre):
        sys.exit(0)   # heavy: explicit whole-workspace compile (#544-review)
    if broad_plural.search(pre) and not pkg_scope.search(pre):
        sys.exit(0)   # heavy: all-of-kind across the whole workspace, no -p scope
    if not narrowing.search(pre):
        sys.exit(0)   # heavy: unscoped -> whole-workspace default compile (#544)
sys.exit(1)
PYEOF
}

# Is it a HEAVY build? (cargo check / clippy / cargo test --no-run are NOT)
is_heavy() {
    local c="$1"
    # #471 (F1): every closing anchor widened `([[:space:]]|$)` ->
    # `([[:space:]]|$|[;&|)(<>])` so a shell metachar glued directly to the
    # subcommand token (`cargo run;`, `(cargo run)`, `cargo run|tee`, `cargo run&`)
    # no longer escapes, while a longer-word decoy (`cargo runner`, `cargo
    # run-script`) still does not match (the char after `run` there is a letter/
    # `-`, neither whitespace/EOL nor a metachar). The tauri/trunk/wasm-pack shapes
    # below have NO closing anchor -- they match a prefix regardless of the
    # trailing char -- so F1 does not apply to them.
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+build([[:space:]]|$|[;&|)(<>])' && return 0
    printf '%s' "$c" | grep -qE 'cargo[- ]tauri[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])trunk[[:space:]]+build' && return 0
    printf '%s' "$c" | grep -qE 'wasm-pack[[:space:]]+build' && return 0
    # `cargo test` that RUNS tests (NOT the compile-only `--no-run`). #471 (F2):
    # no WHOLE-flag `--no-run` anywhere -> heavy on the fast path (no python); a
    # whole-flag `--no-run` present -> the segment helper decides (a genuine run
    # in a different segment, or a `--no-run-decoy` harness arg, still blocks).
    if printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+test([[:space:]]|$|[;&|)(<>])'; then
        if printf '%s' "$c" | grep -qE '(^|[[:space:]])--no-run([[:space:]]|$|[;&|)(<>])'; then
            _gated_shape_is_heavy "$c" 'test' && return 0
        else
            return 0
        fi
    fi
    # #470: heavy compile/run cargo subcommands that escaped the build/test
    # regexes and ran SILENTLY (no block, no audit line) -- camera-box provably
    # runs all of these locally (STEP 0 evidence + the #470 inventory). Same
    # command-position anchor as above so a quoted mention or an argument is
    # never matched; the SANCTIONED Tier-0 workarounds (a `--no-run` compile, a
    # direct `target/.../deps/<bin>` execution, a `gcc`/`cc` harness compile)
    # stay untouched -- none of them contains one of these command shapes.
    #
    #   cargo run     -- compiles + runs the binary (no cheap subcommand)
    #   cargo mutants -- compiles + runs the whole mutation set
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+run([[:space:]]|$|[;&|)(<>])' && return 0
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+mutants([[:space:]]|$|[;&|)(<>])' && return 0
    # `cargo nextest run` -- the real CI test runner (compiles + runs); the
    # compile-only `cargo nextest list` is a cheap check and is NOT matched.
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+nextest[[:space:]]+run([[:space:]]|$|[;&|)(<>])' && return 0
    # `cmake --build <dir>` -- the vendored-libobs C build documented for local
    # dev1 use (vendor/BUILD.md). The `cmake -S . -B` configure step is light
    # and is NOT matched -- only `--build` is heavy.
    printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cmake[[:space:]]+--build([[:space:]]|$|[;&|)(<>])' && return 0
    # `cargo bench` that RUNS benches (NOT the compile-only `--no-run`, same #471
    # (F2) segment-scoped treatment as the `cargo test` carve-out above).
    if printf '%s' "$c" | grep -qE '(^|[;&|([:space:]])cargo[[:space:]]+bench([[:space:]]|$|[;&|)(<>])'; then
        if printf '%s' "$c" | grep -qE '(^|[[:space:]])--no-run([[:space:]]|$|[;&|)(<>])'; then
            _gated_shape_is_heavy "$c" 'bench' && return 0
        else
            return 0
        fi
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
    echo "BLOCKED: heavy local build/test in the camera-box repo (no-local-builds.md, airuleset #477). The '# airuleset:build-ok' marker and AIRULESET_ALLOW_LOCAL_BUILD are DISABLED for camera-box — heavy compilation and tests run in CI ONLY. Locally, run only a cheap check: cargo check / cargo clippy / a SCOPED compile-only build (cargo test --no-run -p <crate> / --lib / --test <name>). A FULL-workspace 'cargo test --no-run' is heavy (#544) — scope it or let CI run it." >&2
elif [ -n "$HEAVY_SCRIPT" ]; then
    echo "BLOCKED: heavy local build hidden inside invoked script '$HEAVY_SCRIPT' in a Tier-0 project (no-local-builds.md). Compilation + release builds run in CI — locally run only a cheap check: cargo check / cargo clippy / a SCOPED compile-only build (cargo test --no-run -p <crate> / --lib). To build locally on purpose: make the project Tier 1 ('<!-- airuleset:local-builds=allowed -->' in its CLAUDE.md) or Tier 2 ('/fast-iterate on'), or append '# airuleset:build-ok' to this one command." >&2
else
    echo "BLOCKED: heavy local build in a Tier-0 project (no-local-builds.md). Compilation + release builds run in CI — locally run only a cheap check: cargo check / cargo clippy / a SCOPED compile-only build (cargo test --no-run -p <crate> / --lib / --test <name>). A FULL-workspace 'cargo test --no-run' compiles the whole test suite and is heavy (#544) — scope it with -p/--lib or let CI run it. To build locally on purpose: make the project Tier 1 ('<!-- airuleset:local-builds=allowed -->' in its CLAUDE.md) or Tier 2 ('/fast-iterate on'), or append '# airuleset:build-ok' to this one command." >&2
fi
exit 2
