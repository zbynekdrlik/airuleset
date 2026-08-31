#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a heavy build-toolchain / VM launch, but ONLY
# on a box whose class marker reads `shared-stream`. #778 — Layer 1 (stop a NEW
# heavy build before it spawns); watchdog Job 38 (`heavy_build_reaper`) is the
# Layer 2 backstop for anything already running.
#
# WHY: the subdev VPS runs N isolated reduced-authority Claude stream users and
# exists ONLY to run those Claude sessions + git + light scripts/tests + the
# watchdog. Streams self-installed a JDK + Android toolchain there and ran
# Gradle/Kotlin daemons (`-Xmx3072m` × 2 = 13.3 GB of 15.6 GB RAM), collapsing
# the box (#774). The owner's standing, repeated rule: Android/JVM/RN builds run
# on dev2 (the build+emulator lane), NEVER on a shared-stream box.
#
# BOX-CLASS GATE (first): this hook is a TOTAL NO-OP unless
# `~/.claude/airuleset-box-class` reads exactly `shared-stream`. On a
# workstation (dev1/dev2/gk) or a box with no marker it exits 0 immediately —
# the heavy build is the WORK there, never blocked.
#
# Reads `.tool_input.command` on STDIN (the SAME contract every sibling
# Bash-payload hook uses). Exit 2 = block (reason on STDERR — stdout is
# invisible to the model); exit 0 = allow. ANY classifier malfunction FAILS
# OPEN. Parser shape is the ESTABLISHED one (block-root-recursive-grep.sh):
# heredoc-body strip -> QUOTE-AWARE per-segment split -> shlex -> `bash -c`
# recursion -> strip_prefix. ONE parser shape, never a second invented one.
#
# DELIBERATELY NARROW (fail toward ALLOW): only KNOWN heavy build/VM launchers
# are blocked —
#   * a launcher basename: gradle, gradlew, kotlinc, kotlinc-jvm, aapt2, aapt,
#     sdkmanager, avdmanager, emulator, or a `qemu-system*` VM;
#   * `java` ONLY in a build-daemon shape (a `org.gradle.`/
#     `org.jetbrains.kotlin.daemon` main-class token, or `-jar <…gradle…>`) —
#     a plain `java -version` / `java -jar app.jar` passes.
# NODE is DELIBERATELY not blocked — it runs Claude Code / MCP servers / the
# webterm. An unknown heavy toolchain that slips past is caught by Job 38.
#
# Bypass (rare, reviewed — NOT auto-logged, same honest convention as the
# sibling hooks): append `# airuleset:heavy-build-ok <reason>` to the OFFENDING
# command as a trailing COMMENT (honored only AFTER a `#`, so a pattern merely
# QUOTING the marker text never disarms a real launch).

# --- BOX-CLASS GATE: no-op off a shared-stream box -------------------------
BOX_CLASS_FILE="${HOME:-/nonexistent}/.claude/airuleset-box-class"
CLASS="$(cat "$BOX_CLASS_FILE" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
[ "$CLASS" = "shared-stream" ] || exit 0

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import os
import re
import shlex
import sys

text = sys.argv[1]

BYPASS = "airuleset:heavy-build-ok"

# Launcher basenames that are ALWAYS a heavy build / VM launch on a Claude-only
# box (an exact basename match). `gradlew`/`./gradlew` -> basename `gradlew`.
BLOCKED_BASENAMES = {
    "gradle", "gradlew", "kotlinc", "kotlinc-jvm", "aapt2", "aapt",
    "sdkmanager", "avdmanager", "emulator",
}
# A basename with THIS prefix is a qemu VM / Android emulator backend.
QEMU_PREFIX = "qemu-system"
# The java build-daemon main-class tokens (a plain java is NOT blocked).
JAVA_BUILD_TOKENS = ("org.gradle.", "org.jetbrains.kotlin.daemon")

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_KEYWORDS = ("do", "then", "else", "elif")
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash")
WRAP_NOARG = {"sudo", "env", "nohup", "command", "builtin", "time"}
WRAP_OPTS = {"timeout", "nice", "ionice", "stdbuf"}
WRAP_VALUE_FLAGS = {"-k", "--kill-after", "-s", "--signal", "-i", "-o", "-e"}


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=False)
    except ValueError:
        return segment.split()


def _split_segments(s):
    """Split a shell script into command segments on unquoted &&/||/;/|/&/
    newline. QUOTE-AWARE: an operator inside a quoted span does NOT split, so a
    quoted pattern (`"a;b"`) stays inside its own command."""
    segs = []
    cur = []
    i, n = 0, len(s)
    q = None
    while i < n:
        c = s[i]
        if q is not None:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(s[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            cur.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(c)
            cur.append(s[i + 1])
            i += 2
            continue
        if c in ";\n":
            segs.append("".join(cur))
            cur = []
            i += 1
            continue
        if c == "&":
            segs.append("".join(cur))
            cur = []
            i += 2 if (i + 1 < n and s[i + 1] == "&") else 1
            continue
        if c == "|":
            segs.append("".join(cur))
            cur = []
            i += 2 if (i + 1 < n and s[i + 1] == "|") else 1
            continue
        cur.append(c)
        i += 1
    if cur:
        segs.append("".join(cur))
    return segs


def strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if ASSIGN_RE.match(t) or t in WRAP_NOARG or t in LOOP_KEYWORDS:
            i += 1
            continue
        if t in WRAP_OPTS:
            i += 1
            while i < len(tk) and tk[i].startswith("-") and tk[i] != "-":
                takes_value = tk[i] in WRAP_VALUE_FLAGS
                i += 1
                if takes_value and i < len(tk):
                    i += 1
            if t in ("timeout", "nice", "ionice") and i < len(tk) \
                    and re.match(r'^-?\d', tk[i]):
                i += 1
            continue
        break
    return tk[i:]


def shell_dash_c_script(tk):
    if not tk or tk[0] not in SHELL_WRAPPERS:
        return None
    for j in range(1, len(tk)):
        if tk[j] == "-c" or DASH_C_RE.match(tk[j]):
            return tk[j + 1] if j + 1 < len(tk) else None
    return None


def _java_is_build_daemon(tk):
    """A `java` invocation is blocked ONLY when it launches a gradle/kotlin
    build daemon: a main-class token starting with one of JAVA_BUILD_TOKENS, OR
    `-jar <path containing 'gradle'>`. A plain `java -version` / `java -jar
    app.jar` passes."""
    n = len(tk)
    for i in range(1, n):
        t = tk[i]
        for tok in JAVA_BUILD_TOKENS:
            if t.startswith(tok):
                return True
        if t == "-jar" and i + 1 < n and "gradle" in os.path.basename(tk[i + 1]).lower():
            return True
    return False


def cmd_is_heavy_build(tk):
    """tk is one command's tokens (prefix already stripped). Return True if it
    launches a blocked heavy build/VM toolchain."""
    if not tk:
        return False
    base = os.path.basename(tk[0])
    if base in BLOCKED_BASENAMES:
        return True
    if base.startswith(QEMU_PREFIX):
        return True
    if base == "java":
        return _java_is_build_daemon(tk)
    return False


def _bypassed(seg):
    """The bypass marker disarms a segment ONLY when it appears after a `#`
    (a real comment), never as a quoted argument."""
    if "#" not in seg:
        return False
    return BYPASS in seg.split("#", 1)[1]


def classify(script):
    """True iff any command segment launches a blocked heavy build toolchain."""
    for seg in _split_segments(script):
        if _bypassed(seg):
            continue
        tk = strip_prefix(tokens_of(seg))
        inner = shell_dash_c_script(tk)
        if inner is not None:
            if classify(inner):
                return True
            continue
        if cmd_is_heavy_build(tk):
            return True
    return False


# strip heredoc BODIES (documentation payload — a ticket comment / commit body
# quoting the banned shape), never command tokens. SAME shape as the siblings.
lines = text.split("\n")
heredoc_re = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
out = []
i, nlines = 0, len(lines)
while i < nlines:
    line = lines[i]
    mm = heredoc_re.search(line)
    out.append(line)
    i += 1
    if not mm:
        continue
    delim = mm.group(2)
    strip_leading = "<<-" in line
    while i < nlines:
        body_line = lines[i]
        check = body_line.lstrip("\t") if strip_leading else body_line
        i += 1
        if check == delim:
            break
cmd = "\n".join(out)

sys.exit(2 if classify(cmd) else 0)
PYEOF

[ "$RC" -eq 2 ] || exit 0

cat >&2 <<'MSG'
BLOCKED: a heavy build-toolchain / VM launch (gradle / gradlew / kotlinc /
aapt2 / sdkmanager / avdmanager / emulator / qemu-system*, or a java gradle/
kotlin build daemon) on a SHARED-STREAM box. #778 — this box (subdev) exists
ONLY to run Claude sessions + git + light scripts; a JVM/Android build daemon
here collapses the box (#774).

The owner's standing rule: Android / JVM / React-Native builds run on dev2.

Do this instead:

  • Run the build on dev2 (the build + emulator lane):
      ssh newlevel@dev2 'cd <repo> && ./gradlew assembleRelease'
  • Or wire it as a CI job (GitHub Actions / EAS) — dev2 is the owner-designated
    place, CI is the complement.

A heavy build toolchain is NEVER installed on a shared-stream box either — it
goes to the dev2 build lane.

Genuine one-off exception: append `# airuleset:heavy-build-ok <reason>` to the
offending command as a trailing COMMENT.
MSG
exit 2
