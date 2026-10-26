"""#1244 — the disk-guard root leg never makes a second logrotate stanza.

logrotate (3.21 on gk) rejects a second stanza for a log it already rotates
("duplicate log entry"), drops that entry with the rest of its file and exits 1.
#841 OVERWRITES the distro ``/etc/logrotate.d/btmp|wtmp``. That avoided a
duplicate of airuleset's own making, but on gk odoo-erp's
``/etc/logrotate.d/gk-auth`` (odoo-erp issue 5872) also rotates btmp + wtmp,
so logrotate.service failed every night and the push read-back exited 4.

So the apply script asks the box first. ``DETECTOR_PY`` (ONE source string, run
there with python3 and exec'd by the tests) reads ``/etc/logrotate.conf`` and
every ``/etc/logrotate.d`` file logrotate itself reads, and parses only the PATH
LINES of each stanza. Comments, directives and ``postrotate … endscript`` bodies
are skipped, and a glob is matched per path segment as glob(3) does. It names
another file that already rotates the log. Then ``build_logrotate_step``:

* another owner, and airuleset's path holds a file whose FIRST line carries
  ``MARKER``: that file becomes a stanza-free yield note (same marker, so the
  next push still knows the path is airuleset's);
* another owner, and the file at airuleset's path has no marker: it is foreign,
  never touched, and reported;
* another owner, and airuleset's path is empty: nothing is written;
* no other owner: airuleset's stanza (written back over a yield note once the
  other owner is gone);
* a detection error: nothing is written for that log, and ``LR_FAIL=1``
  (the read-back turns it into exit 4).

The read-back (``build_logrotate_readback``) is the independent oracle, run on
logrotate's own ``--debug`` parse, in both directions:
* a missed owner (an ``include`` inside a logrotate.d file, a ``{a,b}`` glob)
  shows up as a duplicate entry;
* a yield to a stanza logrotate never reads (a ``tabooext``, a file it skips on
  a parse error) leaves a managed log without its ``considering log`` line.
Either way the read-back exits 4.

A foreign file at airuleset's own path (e.g. the pristine distro btmp beside
gk-auth) is reported, never rewritten, so that duplicate stays loud until a
human removes one of the two. A stdlib leaf with no airuleset imports;
``cli_disk_guard_root`` re-exports the paths and renderers (moved here verbatim).
"""
import shlex

MARKER = "# Managed by airuleset (#841)"
LR_CONF = "/etc/logrotate.conf"
LR_DIR = "/etc/logrotate.d"
_PY_MARK = "AIRULESET_LR_OWNER_PY"

# airuleset's own files sit at the DISTRO paths (never a second `50-airuleset-*`
# file for the same log), and each names the log it rotates.
LOGROTATE_BTMP_PATH = "/etc/logrotate.d/btmp"
LOGROTATE_WTMP_PATH = "/etc/logrotate.d/wtmp"
LOGROTATE_LOGS = {LOGROTATE_BTMP_PATH: "/var/log/btmp",
                  LOGROTATE_WTMP_PATH: "/var/log/wtmp"}
LOGROTATE_ROTATE = 4               # weekly + rotate 4 (was monthly rotate 1)


def render_logrotate_btmp():
    """btmp logrotate config OVERWRITING the distro ``/etc/logrotate.d/btmp``
    (weekly + compress + rotate 4). ``create 0660 root utmp`` preserved EXACTLY
    — btmp records failed-login usernames + mistyped passwords; a wrong mode is
    an information leak, not a cosmetic bug."""
    return (
        "# Managed by airuleset (#841) — btmp weekly + compress + rotate 4\n"
        "# (was monthly rotate 1 → up to ~450M of one file on disk at once).\n"
        "# OVERWRITES the distro file in place: a second logrotate stanza for\n"
        "# the same log errors 'duplicate log entry' and silently rotates\n"
        "# NOTHING. create mode 0660 root utmp preserved EXACTLY (btmp holds\n"
        "# failed-login usernames/mistyped passwords — a wrong mode leaks them).\n"
        "/var/log/btmp {\n"
        "    missingok\n"
        "    weekly\n"
        "    compress\n"
        "    delaycompress\n"
        "    notifempty\n"
        "    rotate %d\n"
        "    create 0660 root utmp\n"
        "}\n" % LOGROTATE_ROTATE
    )


def render_logrotate_wtmp():
    """wtmp logrotate config OVERWRITING the distro ``/etc/logrotate.d/wtmp``
    (weekly + compress + rotate 4). ``create 0664 root utmp`` preserved."""
    return (
        "# Managed by airuleset (#841) — wtmp weekly + compress + rotate 4.\n"
        "# OVERWRITES the distro file in place (duplicate-stanza fail-open).\n"
        "/var/log/wtmp {\n"
        "    missingok\n"
        "    weekly\n"
        "    compress\n"
        "    delaycompress\n"
        "    notifempty\n"
        "    rotate %d\n"
        "    create 0664 root utmp\n"
        "}\n" % LOGROTATE_ROTATE
    )

# Runs on the box as ``python3 - <conf> <dir> <log> <own conf>`` and prints the
# first OTHER config file that rotates <log> (nothing when none does). Any read
# error exits non-zero with the reason on stderr — never a silent "no owner".
DETECTOR_PY = r'''
import fnmatch
import os
import shlex
import sys

# logrotate's default taboo patterns: files it never reads from an include dir
# (measured on logrotate 3.21.0, every fleet box: `Ignoring a.bak, because of
# *.bak pattern match`; plus `*.rhn-cfg-tmp-*` below).
TABOO = (",v", ".bak", ".cfsaved", ".disabled", ".dpkg-bak", ".dpkg-del",
         ".dpkg-dist", ".dpkg-new", ".dpkg-old", ".dpkg-tmp", ".rpmnew",
         ".rpmorig", ".rpmsave", ".swp", ".ucf-dist", ".ucf-new", ".ucf-old", "~")
SCRIPTS = ("postrotate", "prerotate", "firstaction", "lastaction", "preremove")


def _words(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def log_patterns(text):
    """Every log path/glob named on a stanza's PATH LINES, in order."""
    out, in_stanza, in_script = [], False, False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if in_script:
            if line.split()[0] == "endscript":      # `endscript }` closes both
                in_script = False
                in_stanza = "}" not in line.split("#", 1)[0]
            continue
        if in_stanza:
            if line.split()[0] in SCRIPTS:
                in_script = True
            elif "}" in line:
                in_stanza = False
            continue
        head, brace, tail = line.partition("{")
        words = _words(head)
        if words and words[0].startswith("/"):
            out.extend(w for w in words if w.startswith("/"))
        if brace:
            in_stanza = "}" not in tail
    return out


def rotates(pattern, log):
    """True when glob ``pattern`` names ``log``; like glob(3), ``*`` never
    crosses a ``/``, so the match is per path segment."""
    pat, path = pattern.rstrip("/").split("/"), log.split("/")
    return len(pat) == len(path) and all(
        fnmatch.fnmatchcase(p, q) for p, q in zip(path, pat))


def logrotate_reads(st, as_root):
    """logrotate run as root ignores a config file that is group/other-writable
    or not owned by root (its own `Ignoring … because the file owner is
    wrong`); not as root it applies no such rule."""
    return not as_root or (not st.st_mode & 0o022 and st.st_uid == 0)


def config_files(conf, include_dir, as_root=None):
    """The main config plus every file logrotate reads from its include dir
    (an absent include dir adds none; an unreadable one raises)."""
    as_root = os.geteuid() == 0 if as_root is None else as_root
    names = [conf] if os.path.isfile(conf) else []
    if os.path.lexists(include_dir):
        names += [os.path.join(include_dir, n) for n in sorted(os.listdir(include_dir))
                  if not n.endswith(TABOO) and ".rhn-cfg-tmp-" not in n]
    return [p for p in names
            if os.path.isfile(p) and logrotate_reads(os.stat(p), as_root)]


def main(argv):
    conf, include_dir, log, own = argv[1:5]
    own = os.path.realpath(own)
    for path in config_files(conf, include_dir):
        if os.path.realpath(path) == own:
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if any(rotates(p, log) for p in log_patterns(text)):
            print(path)
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except OSError as e:          # one loud line, never a silent "no owner"
        sys.exit("airuleset logrotate-owner: %s" % e)
'''


def render_yield_note(log):
    """The stanza-free file airuleset leaves at its own path while another
    logrotate file rotates ``log``. Line 1 carries ``MARKER``, so the next push
    still recognises the path as airuleset's own."""
    return (
        "%s — %s YIELDED (#1244): another\n"
        "# logrotate config already rotates this log, and logrotate rejects a\n"
        "# second stanza ('duplicate log entry'). No stanza here on purpose; every\n"
        "# push re-checks and writes airuleset's stanza back once no other file\n"
        "# rotates %s.\n" % (MARKER, log, log))


def _install_heredoc(conf, mode, content, mark):
    return ("_install %s %s <<'%s'\n%s%s"
            % (shlex.quote(conf), shlex.quote(mode), mark,
               content.rstrip("\n") + "\n", mark))


def _step_for(log, conf, content, mode, mark):
    q_log, q_conf = shlex.quote(log), shlex.quote(conf)
    return (
        "if ! LR_OTHER=$(_lr_other %s %s); then\n"
        '    echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: cannot read the logrotate '
        'config to place %s — %s left as is (#1244)" >&2\n'
        "    LR_FAIL=1\n"
        'elif [ -n "$LR_OTHER" ]; then\n'
        "    if _lr_ours %s; then\n"
        "%s\n"
        '        echo "  disk-guard-root: %s yielded to $LR_OTHER (#1244)"\n'
        "    elif [ -e %s ]; then\n"
        '        echo "  ⚠ disk-guard-root: %s is rotated by $LR_OTHER AND by %s, '
        'which airuleset did not write — left alone (#1244)" >&2\n'
        "    else\n"
        '        echo "  disk-guard-root: %s is rotated by $LR_OTHER — no airuleset '
        'stanza (#1244)"\n'
        "    fi\n"
        "else\n"
        "%s\n"
        "fi"
        % (q_log, q_conf, log, conf, q_conf,
           _install_heredoc(conf, mode, render_yield_note(log), mark),
           log, q_conf, log, conf, log,
           _install_heredoc(conf, mode, content, mark)))


def build_logrotate_readback(logs, lr_conf=LR_CONF):
    """The read-back half (sets ``fail=1``): logrotate's own ``--debug`` parse
    of the live config must show (a) NO duplicate log entry — the Fable-flagged
    #841 fail-open; the grep reads all of the output, since logrotate exits 1
    on a duplicate (#1223) — and (b) a ``considering log <log>`` for EVERY
    managed log, i.e. some stanza logrotate really reads still rotates it.
    (b) is the oracle for the yield above: a yield to a stanza logrotate skips
    (a taboo name, an unsafe mode, a parse error, a ``tabooext``) fails here
    instead of leaving the log rotated by nobody. ``case``, not ``grep -q``: an
    early-exiting grep would SIGPIPE the producer under ``pipefail``."""
    checks = "".join(
        "    case \"$LR_DEBUG\"$'\\n' in *\"considering log %s\"$'\\n'*) ;; *)\n"
        '        echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: no stanza logrotate reads '
        'rotates %s (#1244)" >&2; fail=1 ;; esac\n' % (log, log) for log in logs)
    return (
        "if command -v logrotate >/dev/null 2>&1; then\n"
        "    LR_DEBUG=$(logrotate --debug %s 2>&1 || true)\n"
        "    if printf '%%s\\n' \"$LR_DEBUG\" | grep -i \"duplicate log entry\" "
        ">/dev/null; then\n"
        '        echo "  ⚠ DISK-GUARD-ROOT VERIFY FAIL: a logrotate duplicate log '
        'entry — a log (likely btmp/wtmp) would silently NOT rotate; check '
        '/etc/logrotate.d for a second stanza" >&2; fail=1\n'
        "    fi\n%sfi" % (shlex.quote(lr_conf), checks))


def build_logrotate_step(logs, lr_conf=LR_CONF, lr_dir=LR_DIR,
                         heredoc_mark="AIRULESET_DGROOT_EOF"):
    """The apply-script bash that installs each ``(log, conf, content, mode)``
    only when no other logrotate file rotates ``log`` (module docstring). It
    needs the apply script's ``_install`` and an initialised ``LR_FAIL``."""
    parts = [
        # -I: never import shlex/fnmatch from the working directory (root, sudo)
        "_lr_other() {\n"
        "    python3 -I - %s %s \"$1\" \"$2\" <<'%s'\n%s\n%s\n}"
        % (shlex.quote(lr_conf), shlex.quote(lr_dir), _PY_MARK,
           DETECTOR_PY.strip("\n"), _PY_MARK),
        "_lr_ours() {\n"
        '    local first=""\n'
        '    [ -f "$1" ] || return 1\n'
        '    IFS= read -r first < "$1" || [ -n "$first" ] || return 1\n'
        '    case "$first" in %s*) return 0 ;; esac\n'
        "    return 1\n"
        "}" % shlex.quote(MARKER),
    ]
    for log, conf, content, mode in logs:
        parts.append(_step_for(log, conf, content, mode, heredoc_mark))
    return "\n".join(parts)
