"""#1256 — which tmux servers are bound to a socket path, and which hosts Claude.

A server whose socket FILE is deleted keeps running; /proc/net/unix still lists
its LISTENING socket with the bound path. When something then starts a second
server on the same path, two servers share one path and only the newest answers.
Subdev 2026-10-19: david1's working Claude sat in such an orphan while the
socket belonged to a bash-only server, and #318's SIGUSR1 recovery picked
servers in `ps` (pid) order, so the oldest bash-only server won.

Pure reads of a proc root (injectable for tests); the only tmux call is one
`list-sessions -F '#{pid}'` on the explicit path, made only when >= 2 servers
share it. Never kills a server: the only action is SIGUSR1 (tmux re-creates
its socket at its own path).
"""
import os
import signal

LISTENING = "00010000"   # __SO_ACCEPTCON in /proc/net/unix "Flags"


def default_socket_path(env=None):
    env = os.environ if env is None else env
    return os.path.join(env.get("TMUX_TMPDIR") or "/tmp",
                        "tmux-%d" % os.getuid(), "default")


def listening_inodes(path, proc_root="/proc"):
    found = set()
    try:
        with open(os.path.join(proc_root, "net", "unix")) as f:
            for line in f:
                p = line.split()
                if len(p) >= 8 and p[7] == path and p[3] == LISTENING:
                    found.add(p[6])
    except OSError:
        return set()   # no proc view: nothing provable, nothing to act on
    return found


def _pids(proc_root):
    try:
        return sorted(int(d) for d in os.listdir(proc_root) if d.isdigit())
    except OSError:
        return []


def servers_bound_to(path, proc_root="/proc"):
    """Pids holding a LISTENING unix socket bound to `path` (own-uid fds only:
    another uid's fd dir is unreadable and skipped)."""
    inodes = listening_inodes(path, proc_root)
    if not inodes:
        return []
    out = []
    for pid in _pids(proc_root):
        fd_dir = os.path.join(proc_root, str(pid), "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in inodes:
                out.append(pid)
                break
    return out


def _parent_and_comm(proc_root):
    """{pid: (ppid, comm)} from <pid>/stat; comm sits in parentheses and may
    itself contain spaces or parentheses, so split on the LAST ')'."""
    table = {}
    for pid in _pids(proc_root):
        try:
            with open(os.path.join(proc_root, str(pid), "stat")) as f:
                raw = f.read()
        except OSError:
            continue
        head, _, tail = raw.rpartition(")")
        fields = tail.split()
        if len(fields) >= 2 and "(" in head:
            table[pid] = (int(fields[1]), head.split("(", 1)[1])
    return table


def claude_hosts(server_pids, proc_root="/proc"):
    """The servers among `server_pids` that are an ancestor of a `claude`."""
    servers = set(server_pids)
    if not servers:
        return set()
    table = _parent_and_comm(proc_root)
    hosts = set()
    for pid, (_ppid, comm) in table.items():
        if comm != "claude":
            continue
        cur, seen = pid, set()
        while cur in table and cur not in seen and cur > 1:
            seen.add(cur)
            if cur in servers:
                hosts.add(cur)
                break
            cur = table[cur][0]
    return hosts


def recovery_order(pids, proc_root="/proc"):
    """#318 recovery candidates, the server hosting a Claude first (stable)."""
    hosts = claude_hosts(pids, proc_root)
    return [p for p in pids if p in hosts] + [p for p in pids if p not in hosts]


def _already_logged(memo_path, signature):
    """True when `memo_path` holds exactly `signature` (a missing or unreadable
    memo means 'not logged yet', so the line is emitted)."""
    if not memo_path or not os.path.exists(memo_path):
        return False
    try:
        with open(memo_path) as f:
            return f.read() == signature
    except OSError:
        return False


def _log_once(logs, line, signature, memo_path):
    """Append `line` unless the same state signature was already logged."""
    if _already_logged(memo_path, signature):
        return
    if memo_path:
        try:
            with open(memo_path, "w") as f:
                f.write(signature)
        except OSError as e:
            line += " (memo unwritable: %s)" % e
    if logs is not None:
        logs.append(line)


def duplicate_check(run, logs=None, dry_run=False, path=None, proc_root="/proc",
                    kill=os.kill, memo_path=None):
    """None when <= 1 server is bound to the path. Otherwise one of:
    'repointed' — the socket owner hosts no Claude and exactly one other
    server does: SIGUSR1 it so new connections reach the working session;
    'ok' — the owner hosts the Claude (an idle orphan lingers, logged);
    'ambiguous' — Claudes in several servers: logged, never touched;
    'no-claude' — no server hosts a Claude: logged, never touched."""
    path = path or default_socket_path()
    servers = servers_bound_to(path, proc_root)
    if len(servers) < 2:
        return None
    hosts = claude_hosts(servers, proc_root)
    out = run(["tmux", "-S", path, "list-sessions", "-F", "#{pid}"]) or ""
    owner = next((int(x) for x in out.split() if x.isdigit()), None)
    head = "tmux-duplicate-server path=%s servers=%s owner=%s claude-in=%s" % (
        path, ",".join(map(str, servers)), owner,
        ",".join(map(str, sorted(hosts))) or "none")
    if len(hosts) >= 2:
        verdict = "ambiguous"
    elif not hosts:
        verdict = "no-claude"
    elif owner in hosts:
        verdict = "ok"
    else:
        target = sorted(hosts)[0]
        if dry_run:
            if logs is not None:
                logs.append(head + " -- would repoint to %d (dry-run)" % target)
            return "repointed"
        kill(target, signal.SIGUSR1)
        if logs is not None:
            logs.append(head + " -- repointed to %d (SIGUSR1)" % target)
        return "repointed"
    _log_once(logs, head + " -- " + verdict, head + verdict, memo_path)
    return verdict


def status_line(path=None, proc_root="/proc"):
    """One `airuleset.py status` row when >= 2 servers share the socket path;
    None otherwise (the common, healthy case prints nothing)."""
    path = path or default_socket_path()
    servers = servers_bound_to(path, proc_root)
    if len(servers) < 2:
        return None
    hosts = sorted(claude_hosts(servers, proc_root))
    return ("tmux servers: %d servers share %s (pids %s; claude in %s) -- "
            "duplicate, see journal 'tmux-duplicate-server'" % (
                len(servers), path, ",".join(map(str, servers)),
                ",".join(map(str, hosts)) or "none"))
