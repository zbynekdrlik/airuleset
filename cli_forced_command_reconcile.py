"""#1256 — re-render every BAKED webterm forced command at install.

The webterm attach body (`cli_webterm._remote_command`) is baked into each
target account's `~/.ssh/authorized_keys` as `restrict,pty,command="..."`, and
nothing re-baked those lines (the #1202 class: montalu1's 2026-09-09 hand-bake
outlived its renderer). A change to the body therefore reached only the
webterm-only accounts (`cli_webterm_only` rewrites their whole desired set).

`install` runs as every target account on every push, so it re-renders each
line whose command is OUR attach body here: the session name (`P=`) and the
start-dir chain (`for __r in ...`) are read back from the line, the body comes
from the current renderer (`session_chain_for` applies the #1202 single cwd
source), and the options before `command=` plus the key and comment stay byte
for byte. Every other line (plain keys, foreign forced commands, comments) is
left alone. A changed file is backed up 0600 and replaced atomically.

Encoding: the renderer escapes `\\` and `"`; sshd's dequote only unescapes
`\\"`. Lines are compared in their ENCODED form, so the round trip never
depends on that asymmetry (the attach body carries no backslash anyway).
"""
import os
import re
import shlex
import time

SIGNATURE = 'exec tmux new-session -A -s "$P"'
_CHAIN_RE = re.compile(r"for __r in ([^;]*); do")
_MARK = 'command="'


def encode(cmd):
    """The authorized_keys form of a forced command (cli_webterm_only's)."""
    return cmd.replace("\\", "\\\\").replace('"', '\\"')


def decode(encoded):
    """sshd's opt_dequote: only `\\"` is unescaped."""
    return encoded.replace('\\"', '"')


def split_command(line):
    """(head, encoded_command, tail) for a line carrying `command="..."`:
    head ends with `command="`, tail starts AFTER the closing quote;
    None when the line has no (well-formed) forced command."""
    i = line.find(_MARK)
    if i < 0:
        return None
    k = i + len(_MARK)
    while k < len(line):
        if line[k] == "\\" and line[k + 1:k + 2] == '"':
            k += 2
            continue
        if line[k] == '"':
            return line[:i + len(_MARK)], line[i + len(_MARK):k], line[k + 1:]
        k += 1
    return None


def desired_command(cmd, user):
    """The current rendering of OUR attach body `cmd` for `user`, or None
    when `cmd` is not ours (no `P=` head or no attach signature)."""
    if not cmd.startswith("P=") or SIGNATURE not in cmd:
        return None
    try:
        preferred = shlex.split(cmd[2:].split(";", 1)[0])[0]
    except (ValueError, IndexError):
        return None
    m = _CHAIN_RE.search(cmd)
    chain = m.group(1).split() if m else None
    import cli_webterm
    return cli_webterm._remote_command(preferred, start_dir_chain=chain, user=user)


def plan(ak_text, user):
    """(new_text, changed_line_count) for an authorized_keys body."""
    out, changed = [], 0
    for line in ak_text.splitlines(keepends=True):
        body = line.rstrip("\n")
        parts = split_command(body) if not body.lstrip().startswith("#") else None
        if parts:
            head, enc, tail = parts
            want = desired_command(decode(enc), user)
            if want is not None and encode(want) != enc:
                line = head + encode(want) + '"' + tail + line[len(body):]
                changed += 1
        out.append(line)
    return "".join(out), changed


def reconcile(home, user):
    """Re-render the baked forced commands in `<home>/.ssh/authorized_keys`.
    Returns {"action": skipped|unchanged|updated|unwritable, "changed": n,
    "path": str, "error": str}; never raises for an I/O problem."""
    path = os.path.join(str(home), ".ssh", "authorized_keys")
    res = {"action": "skipped", "changed": 0, "path": path, "error": ""}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return res
    except OSError as e:
        res.update(action="unwritable", error=str(e))
        return res
    new_text, changed = plan(text, user)
    res["changed"] = changed
    if not changed:
        res["action"] = "unchanged"
        return res
    ssh_dir = os.path.dirname(path)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = os.path.join(ssh_dir, "authorized_keys.airuleset-prev-" + stamp)
    tmp = path + ".airuleset-tmp"
    try:
        for target, content in ((backup, text), (tmp, new_text)):
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
        os.replace(tmp, path)
    except OSError as e:
        res.update(action="unwritable", error=str(e))
        return res
    res["action"] = "updated"
    return res


def install_step(home, user):
    """The `airuleset.py install` step: reconcile, print what changed or what
    could not be written, never raise (install is non-fatal per step)."""
    import sys
    try:
        res = reconcile(home, user)
    except Exception as e:   # install must go on; the error is printed
        print("  webterm forced commands error (non-fatal): %s" % e, file=sys.stderr)
        return None
    if res["action"] in ("updated", "unwritable"):
        print("  webterm forced commands: %s (%d line(s)) %s"
              % (res["action"], res["changed"], res["error"]))
    return res
