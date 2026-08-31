"""watchdog.reaper — Job 37: runaway shadow-ugrep OS-process reaper (#776).

The FIRST OS-process reaper in the watchdog. Every cycle it finds processes
that are a genuine runaway shadow-ugrep — the exact `ugrep -G --ignore-files`
signature (anchored at argv[0]), running > 30 minutes, AND burning CPU the
whole time (a busy-loop) — SIGKILLs them, and logs the kill reason + cmdline +
age.

WHY (root cause, traced in the issue #776): Claude Code injects a shadow
`grep()` function into every Bash-tool shell (`~/.claude/shell-snapshots/
snapshot-bash-*.sh`) that rewrites EVERY `grep` to
`ugrep -G --ignore-files --hidden -I --exclude-dir=.git ...`. The bundled
ugrep 7.5.0 has an OPEN upstream bug (anthropics/claude-code#81916):
`--ignore-files` against a directory busy-loops at 100% CPU forever, and its
orphaned child survives the session (a 15-day 295%-CPU orphan on subdev,
#774). `hooks/block-root-recursive-grep.sh` (Layer 1) stops NEW ones from
spawning; this reaper (Layer 2) is the backstop that cleans up anything that
is already running or was orphaned before Layer 1 landed. `#775` (resource
caps) is Layer 3.

FAIL-SAFE by construction — this is code that SIGKILLs processes on prod:
  * SIGNATURE anchored, not a substring — argv[0] basename must be `ugrep`
    and its first two flags must be `-G --ignore-files`. A process merely
    QUOTING the signature (`watch "pgrep -af 'ugrep -G --ignore-files'"`)
    never matches (its argv[0] is `watch`).
  * NEVER a young process — the age gate (`etimes > 30 min`) is necessary.
  * NEVER a low-CPU long-runner — the CPU gate (`cputimes >= 0.5 * etimes`)
    is what tells a 100%-CPU BUSY-LOOP apart from a legitimate long-running
    grep blocked on a pipe (`tail -f log | grep pat`, a sanctioned fleet
    waiter, burns ~0 CPU-seconds however long it runs, so it is NEVER
    killed). Age alone was not enough — this closes that hole.
  * TOCTOU — the pid's `/proc/<pid>/cmdline` is re-verified against the
    anchored signature immediately BEFORE the kill, so a pid reused by an
    unrelated process between the ps read and the kill is never killed.
  * ANY ps/parse error kills NOTHING (`ps_fetch()` raising, or returning
    None, is treated as "could not read → do nothing this cycle").
  * A malformed process row is skipped, never guessed at.
  * `kill_fn` UNWIRED (None) kills nothing — a mis-wired run_once seam logs
    "would kill" and moves on, never a real SIGKILL.
  * `dry_run` logs what it WOULD kill and kills nothing.
  * ps is scoped to this box-user's OWN processes (`-u <uid>`) — you can only
    SIGKILL your own anyway, so scoping avoids a permanent per-cycle
    permission-error log line for another user's runaway on a shared box.

Log-only: a reaped runaway is a SELF-HEAL (like the api-error `continue`
auto-resume), so it goes to the watchdog journal, never a Discord ping (#546
alert-suppression direction).
"""

import os
import signal
import subprocess

# 30 minutes. No legitimate shadowed grep survives this AND stays CPU-busy;
# the busy-loop orphan runs for days at 100% CPU.
REAPER_MIN_AGE_S = 1800

# A busy-loop burns ~1 CPU-second per wall-second (100% of a core), so its
# cumulative CPU time (`cputimes`) is >= its elapsed wall time; a pipe-blocked
# grep burns ~0. 0.5 is a wide, safe separator between the two.
REAPER_MIN_CPU_RATIO = 0.5

# The EXACT shadow-ugrep flag signature the CC shell-snapshot injects. argv[0]
# is `ugrep` (exec -a ugrep / ARGV0=ugrep) and the first two injected flags are
# `-G --ignore-files`. Anchored at argv[0] — NOT a substring match — so a
# process merely mentioning the signature in its arguments never matches.
SHADOW_UGREP_SIGNATURE = "ugrep -G --ignore-files"
_SIG_TOKENS = SHADOW_UGREP_SIGNATURE.split()   # ["ugrep", "-G", "--ignore-files"]


def _matches_signature(args):
    """True iff `args` (a process cmdline string) is the anchored shadow-ugrep
    signature: argv[0] basename == `ugrep` and its first two flags are
    `-G --ignore-files`."""
    toks = (args or "").split()
    if len(toks) < len(_SIG_TOKENS):
        return False
    if os.path.basename(toks[0]) != _SIG_TOKENS[0]:
        return False
    return toks[1:len(_SIG_TOKENS)] == _SIG_TOKENS[1:]


def _is_shadow_ugrep_runaway(args, etimes, cputimes, min_age_s=REAPER_MIN_AGE_S,
                             min_cpu_ratio=REAPER_MIN_CPU_RATIO):
    """True ONLY for a process carrying the anchored shadow-ugrep signature,
    running longer than `min_age_s`, AND burning CPU the whole time (a
    busy-loop). All three are required; any one alone is a legitimate
    transient / long-runner."""
    try:
        age = int(etimes)
        cpu = int(cputimes)
    except (TypeError, ValueError):
        return False
    if age <= min_age_s:
        return False
    if cpu < age * min_cpu_ratio:
        return False
    return _matches_signature(args)


def default_ps_fetch():
    """Read this user's OWN processes as (pid:int, etimes:int, cputimes:int,
    args:str). Returns None on ANY error (→ the reaper kills nothing this
    cycle). `ps -o etimes=`/`cputimes=` give whole SECONDS directly, so no
    clock arithmetic is needed. Scoped to `-u <uid>` — you can only SIGKILL
    your own processes, and scoping avoids a permanent permission-error log for
    another user's runaway on a shared box."""
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,etimes=,cputimes=,args=", "-u", str(os.getuid())],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "  1234   5678   5670 ugrep -G --ignore-files ..." — pid, etimes,
        # cputimes, then the whole argv (which itself contains spaces).
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, etimes_s, cputimes_s, args = parts
        try:
            pid = int(pid_s)
            etimes = int(etimes_s)
            cputimes = int(cputimes_s)
        except ValueError:
            continue
        rows.append((pid, etimes, cputimes, args))
    return rows


def default_proc_cmdline(pid):
    """Read /proc/<pid>/cmdline (NUL-separated argv) as a space-joined string,
    or None if it cannot be read (process gone). Used for the pre-kill TOCTOU
    re-verify."""
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def default_kill_fn(pid):
    """SIGKILL a pid. Raises on failure (the reaper catches + logs)."""
    os.kill(int(pid), signal.SIGKILL)


def shadow_ugrep_reaper(ps_fetch=None, kill_fn=None, verify_fn=None,
                        dry_run=False, min_age_s=REAPER_MIN_AGE_S,
                        min_cpu_ratio=REAPER_MIN_CPU_RATIO):
    """Find + SIGKILL runaway shadow-ugrep processes (anchored signature, age >
    `min_age_s`, CPU-busy).

    `ps_fetch` returns an iterable of (pid, etimes, cputimes, args); returning
    None (or raising) means "could not read → kill nothing". `verify_fn(pid)`
    returns the pid's live cmdline for the pre-kill TOCTOU re-verify (default
    reads /proc). `kill_fn(pid)` sends the SIGKILL — when it is None (an
    unwired run_once seam) the reaper kills NOTHING and logs "would kill".
    `ps_fetch` defaults to the real process read; tests inject fakes. Returns
    the journal log lines (possibly empty). NEVER pings Discord."""
    if ps_fetch is None:
        ps_fetch = default_ps_fetch
    if verify_fn is None:
        verify_fn = default_proc_cmdline

    logs = []
    try:
        procs = ps_fetch()
    except Exception as e:
        return ["shadow-ugrep-reaper: ps error, killed nothing: %r" % (e,)]
    if procs is None:
        # Could not read the process table — fail safe, kill nothing.
        return logs

    for entry in procs:
        try:
            pid, etimes, cputimes, args = entry
        except Exception:
            # malformed row — skip, never guess
            continue
        if not _is_shadow_ugrep_runaway(args, etimes, cputimes, min_age_s,
                                        min_cpu_ratio):
            continue
        if dry_run:
            logs.append(
                "shadow-ugrep-reaper: DRY-RUN would SIGKILL pid=%s age=%ss "
                "cpu=%ss cmd=%s" % (pid, etimes, cputimes, args))
            continue
        if kill_fn is None:
            logs.append(
                "shadow-ugrep-reaper: kill_fn not wired — would SIGKILL pid=%s "
                "age=%ss cpu=%ss cmd=%s (skipped)"
                % (pid, etimes, cputimes, args))
            continue
        # TOCTOU: re-verify the pid still IS the runaway right before killing,
        # so a pid reused by an unrelated process is never SIGKILLed.
        try:
            live = verify_fn(pid)
        except Exception:
            live = None
        if live is None:
            logs.append(
                "shadow-ugrep-reaper: pid=%s vanished before kill, skipped "
                "(cmd was %s)" % (pid, args))
            continue
        if not _matches_signature(live):
            logs.append(
                "shadow-ugrep-reaper: pid=%s no longer the runaway (reused?), "
                "skipped (now %r)" % (pid, live))
            continue
        try:
            kill_fn(pid)
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s age=%ss cpu=%ss runaway "
                "shadow-ugrep (issue 776, upstream cc#81916) cmd=%s"
                % (pid, etimes, cputimes, args))
        except Exception as e:
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s FAILED: %r "
                "(age=%ss cpu=%ss cmd=%s)" % (pid, e, etimes, cputimes, args))
    return logs


# --------------------------------------------------------------------------- #
# #778 — Job 38: heavy-build-toolchain OS-process reaper, SHARED-STREAM BOX
# ONLY. A SIBLING of the #776 reaper above, NOT an extension of it — the two
# have OPPOSITE gating semantics, so folding them into one function would be a
# patchwork (`architecture-first`):
#   * shadow_ugrep_reaper: EVERY box, age + CPU gate (a runaway is only a
#     runaway once it has run long AND stayed CPU-busy).
#   * heavy_build_reaper:  SHARED-STREAM box only, KILL ON SIGHT (no age, no
#     CPU gate) — a JVM/Android build daemon is BANNED OUTRIGHT there, so a
#     young, idle one is killed exactly like an old busy one.
#
# WHY (root cause, #778): the subdev VPS runs N isolated reduced-authority
# Claude stream users; it exists ONLY to run those Claude sessions + git +
# light scripts/tests + the watchdog. Streams `david1`/`david2` self-installed
# a JDK + Android toolchain there and ran Gradle/Kotlin daemons (`-Xmx3072m` ×
# 2 = 13.3 GB of 15.6 GB RAM), collapsing the box (#774). The owner's standing,
# repeated rule: Android/JVM/RN builds run on dev2 (the build+emulator lane),
# NEVER on a shared-stream box. `hooks/block-heavy-build-toolchain.sh` (Layer 1)
# stops a NEW launch on a shared-stream box; this reaper (Layer 2) is the
# backstop that kills any of these BANNED DAEMONS already running or orphaned.
# Same #776 pattern.
#
# The kill set is DELIBERATELY the persistent daemons / VM backends (the memory
# hogs #774 named), NOT every build JVM: a transient Gradle WORKER JVM
# (`org.gradle.process.internal.worker.GradleWorkerMain`) or an ad-hoc
# `javac`/`java` compile is NOT reaped — those are short-lived and blocking
# their LAUNCH is Layer 1's job, so anchoring the reaper on the long-lived
# daemon main-classes keeps it fail-safe (never a false kill) without guessing
# at every build-JVM shape.
# --------------------------------------------------------------------------- #

# The box-class marker a `push`/`install` writes on every target (a shared box
# → `shared-stream`, a workstation dev1/dev2/gk → `workstation`). Read from
# BOTH the bash hook and this python reaper, so the durable marker file is the
# right seam (not an at-runtime AUTHORITY_BY_USER lookup a bash hook can't do).
BOX_CLASS_PATH = "~/.claude/airuleset-box-class"
SHARED_STREAM = "shared-stream"

# argv[0]-anchored heavy build/VM daemon signatures. Anchored EXACTLY like the
# #776 SHADOW_UGREP_SIGNATURE (argv[0] basename), so a process merely QUOTING a
# class name (a `watch`/`pgrep`/`grep`/`git commit` whose argv[0] is not the
# tool) never matches. The two java daemons are identified by their main-class
# token (a plain `java -jar app.jar` is NOT a build daemon and is left alone).
GRADLE_DAEMON_CLASS = "org.gradle.launcher.daemon.bootstrap.GradleDaemon"
KOTLIN_DAEMON_CLASS = "org.jetbrains.kotlin.daemon.KotlinCompileDaemon"


def default_box_class(path=None):
    """The box-class marker's stripped FIRST line (`shared-stream`/
    `workstation`/…), or None when the marker is missing/unreadable/empty.
    Reads the first line (whitespace-stripped) so it agrees byte-for-byte with
    `block-heavy-build-toolchain.sh`'s `cat | head -1 | tr -d '[:space:]'` on
    any content, not only the single clean line the writer emits. Fail-open: a
    read error — OSError OR a non-UTF8/binary marker (UnicodeDecodeError, a
    ValueError) — is never a shared-stream classification."""
    p = os.path.expanduser(path or BOX_CLASS_PATH)
    try:
        with open(p, "r") as fh:
            return fh.readline().strip() or None
    except (OSError, ValueError):
        return None


def is_shared_stream_box(box_class_fn=None):
    """True ONLY when the box-class marker reads EXACTLY `shared-stream`. Any
    other value, a missing marker, or a read error → False. This is the whole
    fail-open discriminator: off a shared-stream box (or when the class cannot
    be read) the heavy-build reaper kills NOTHING."""
    if box_class_fn is None:
        box_class_fn = default_box_class
    try:
        return box_class_fn() == SHARED_STREAM
    except Exception:
        return False


def _heavy_build_kind(args):
    """A short label of the heavy build / VM daemon that `args` (a process
    cmdline string) IS — `gradle-daemon` / `kotlin-daemon` / `aapt2` /
    `qemu/emulator` — or None for anything else. argv[0]-ANCHORED: a process
    merely mentioning a signature in its arguments (argv[0] = watch/pgrep/grep/
    git) never matches. NODE is DELIBERATELY never matched — node runs Claude
    Code, MCP servers and the webterm, so a kill-on-sight node reaper would be
    catastrophic collateral; the hook can discourage a node bundler, the reaper
    never SIGKILLs one."""
    toks = (args or "").split()
    if not toks:
        return None
    base = os.path.basename(toks[0])
    rest = toks[1:]
    if base == "java":
        if GRADLE_DAEMON_CLASS in rest:
            return "gradle-daemon"
        if KOTLIN_DAEMON_CLASS in rest:
            return "kotlin-daemon"
        return None
    if base == "aapt2":
        return "aapt2"
    if base.startswith("qemu-system"):
        return "qemu/emulator"
    return None


def heavy_build_reaper(ps_fetch=None, kill_fn=None, verify_fn=None,
                       dry_run=False, box_class_fn=None):
    """Find + SIGKILL heavy build-toolchain / VM daemons (Gradle/Kotlin/aapt2/
    qemu) — KILL ON SIGHT, no age/CPU gate — but ONLY on a shared-stream box.

    Off a shared-stream box (or when the box-class cannot be read) this kills
    NOTHING and returns []. On a shared-stream box the fail-safe construction
    mirrors shadow_ugrep_reaper (#776) exactly: `ps_fetch` returning None (or
    raising) means "could not read → kill nothing"; a malformed row is skipped;
    `kill_fn=None` (an unwired seam) logs "would kill" and kills nothing;
    `dry_run` logs and kills nothing; and a pre-kill TOCTOU re-verify of the
    pid's live cmdline (`verify_fn`, default reads /proc) means a pid reused by
    an unrelated process between the ps read and the kill is never SIGKILLed.
    `ps_fetch` reuses the Job-37 read shape (pid, etimes, cputimes, args) — the
    heavy reaper reads only pid + args (a build daemon is banned at ANY age, so
    etimes/cputimes are ignored). Returns the journal log lines; NEVER pings."""
    # The box-class gate is FIRST — a non-shared-stream box (dev1/dev2/gk) never
    # even reads its process table here.
    if not is_shared_stream_box(box_class_fn):
        return []
    if ps_fetch is None:
        ps_fetch = default_ps_fetch
    if verify_fn is None:
        verify_fn = default_proc_cmdline

    logs = []
    try:
        procs = ps_fetch()
    except Exception as e:
        return ["heavy-build-reaper: ps error, killed nothing: %r" % (e,)]
    if procs is None:
        return logs

    for entry in procs:
        try:
            pid, etimes, cputimes, args = entry
        except Exception:
            # malformed row — skip, never guess
            continue
        kind = _heavy_build_kind(args)
        if kind is None:
            continue
        if dry_run:
            logs.append(
                "heavy-build-reaper: DRY-RUN would SIGKILL pid=%s kind=%s "
                "cmd=%s" % (pid, kind, args))
            continue
        if kill_fn is None:
            logs.append(
                "heavy-build-reaper: kill_fn not wired — would SIGKILL pid=%s "
                "kind=%s cmd=%s (skipped)" % (pid, kind, args))
            continue
        # TOCTOU: re-verify the pid still IS a heavy build daemon right before
        # killing, so a pid reused by an unrelated process is never SIGKILLed.
        try:
            live = verify_fn(pid)
        except Exception:
            live = None
        if live is None:
            logs.append(
                "heavy-build-reaper: pid=%s vanished before kill, skipped "
                "(cmd was %s)" % (pid, args))
            continue
        if _heavy_build_kind(live) is None:
            logs.append(
                "heavy-build-reaper: pid=%s no longer a build daemon (reused?),"
                " skipped (now %r)" % (pid, live))
            continue
        try:
            kill_fn(pid)
            logs.append(
                "heavy-build-reaper: SIGKILL pid=%s kind=%s BANNED heavy build "
                "toolchain on a shared-stream box (issue 778 — builds run on "
                "dev2) cmd=%s" % (pid, kind, args))
        except Exception as e:
            logs.append(
                "heavy-build-reaper: SIGKILL pid=%s FAILED: %r (kind=%s cmd=%s)"
                % (pid, e, kind, args))
    return logs
