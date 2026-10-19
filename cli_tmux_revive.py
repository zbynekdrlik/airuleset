"""#1256 — revive an unreachable tmux server before anything creates a new one.

A server whose socket FILE was deleted (subdev's weekly /tmp sweep removed
`/tmp/tmux-<uid>/` while servers ran) keeps running but answers nobody. Every
creator that falls through to `tmux new-session -A` then forks a SECOND server
with a same-named session: a hidden second Claude per account.

`TMUX_REVIVE_SH` is one POSIX shell fragment shared by the webterm attach body
(`cli_webterm._ATTACH_BODY`, also baked into authorized_keys forced commands)
and the ssh auto-attach bashrc block (`cli_bashrc_appliers`). When the socket
FILE is missing (a plain `[ -S ]` test, so the healthy path costs no tmux
round-trip), it SIGUSR1s every process still LISTENING on that path:
/proc/net/unix keeps a deleted socket's bound path, and tmux(1) re-creates its
socket on SIGUSR1. It then waits (bounded) for the socket file to reappear.
Servers on other paths (`-S`/`-L`) are never signalled.
`AIRULESET_TMUX_SOCK` overrides the path for tests; a `restrict` forced command
receives no client environment. No backslashes: sshd's authorized_keys dequote
keeps `\\` doubled, so the find pattern uses `?` around the inode.
"""

TMUX_REVIVE_SH = (
    'RS="${AIRULESET_TMUX_SOCK:-${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/default}"; '
    'if [ ! -S "$RS" ]; then RK=""; '
    "for RI in $(awk -v s=\"$RS\" '$8==s && $4==\"00010000\"{print $7}' "
    '/proc/net/unix 2>/dev/null); do '
    'for RF in $(find /proc/[0-9]*/fd -lname "socket:?$RI?" 2>/dev/null); do '
    'RP=${RF#/proc/}; RP=${RP%%/*}; '
    'case "$(cat /proc/$RP/comm 2>/dev/null)" in *": server") '
    'kill -USR1 "$RP" 2>/dev/null && RK=1;; esac; done; done; '
    'if [ -n "$RK" ]; then for RW in 1 2 3 4 5 6 7 8 9 10; do '
    '[ -S "$RS" ] && break; sleep 0.1; done; fi; fi; '
)
