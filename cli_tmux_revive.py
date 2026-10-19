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
socket on SIGUSR1. Exactly ONE server is signalled, because each one re-binds
the path and the last would win: the one hosting a `claude`, else the lowest
pid. The socket DIRECTORY is re-created first (the sweep `rm -rf`s it and tmux
re-binds but never mkdirs). It then waits (bounded) for the socket file and
leaves a `logger -t airuleset-tmux-revive` journal line. Servers on other paths
(`-S`/`-L`) are never signalled.
`AIRULESET_TMUX_SOCK` overrides the path for tests; a `restrict` forced command
receives no client environment. No backslashes: sshd's authorized_keys dequote
keeps `\\` doubled, so the find pattern uses `?` around the inode.
"""

TMUX_REVIVE_SH = (
    'RS="${AIRULESET_TMUX_SOCK:-${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/default}"; '
    'if [ ! -S "$RS" ]; then RC=""; '
    "for RI in $(awk -v s=\"$RS\" '$8==s && $4==\"00010000\"{print $7}' "
    '/proc/net/unix 2>/dev/null); do '
    'for RF in $(find /proc/[0-9]*/fd -lname "socket:?$RI?" 2>/dev/null); do '
    'RP=${RF#/proc/}; RP=${RP%%/*}; '
    'case "$(cat /proc/$RP/comm 2>/dev/null)" in *": server") RC="$RC $RP";; esac; '
    'done; done; '
    'if [ -n "$RC" ]; then '
    "RP=$(ps -eo pid=,ppid=,comm= 2>/dev/null | awk -v c=\"$RC\" '"
    'BEGIN{n=split(c,a," ");for(i=1;i<=n;i++)s[a[i]]=1} {p[$1]=$2} '
    '$3=="claude"{k[$1]=1} '
    'END{for(x in k){y=x;while(y>1&&!(y in s))y=p[y];if(y in s){print y;exit}} '
    'm="";for(i=1;i<=n;i++)if(m==""||a[i]+0<m+0)m=a[i];print m}'
    "'); "
    'mkdir -m 700 -p "${RS%/*}" 2>/dev/null; '
    'kill -USR1 "$RP" 2>/dev/null && '
    'logger -t airuleset-tmux-revive "SIGUSR1 tmux server $RP to re-create $RS" 2>/dev/null; '
    'for RW in 1 2 3 4 5 6 7 8 9 10; do [ -S "$RS" ] && break; sleep 0.1; done; '
    'fi; fi; '
)
