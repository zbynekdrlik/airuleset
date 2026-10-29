#!/usr/bin/env python3
"""peer channel — cross-account Claude session messaging over Claude Code Channels (#1266).

One copy runs per Claude Code session, started by the ``peer@airuleset`` plugin
(``plugins/airuleset/peer/.mcp.json``) as an MCP stdio server. It:

* declares the ``claude/channel`` capability, so an inbound peer message is pushed
  into the running session as ``<channel source="plugin:peer:peer" from="<project>">`` (a native
  channel event: never typed into the prompt, never a file the session polls);
* exposes two tools, ``peers`` and ``peer_send(to, text)``;
* listens on ``/run/claude-peers/<project>.<pid>.sock``, a root-made shared dir
  (``cli_peer_channel``) that only the accounts named in a link may traverse
  (per-uid POSIX ACL, sticky bit).

Trust model (the account is the boundary):

* the RECEIVER reads ``SO_PEERCRED`` of the connecting process; the claimed sender
  project must be a ``projects-registry.json`` project on this host whose
  ``account`` IS that uid's user name, the target must be this session's own
  project, and the pair must be a link in ``peer-links.json``;
* the SENDER reads ``SO_PEERCRED`` of the listening socket and refuses a socket
  not owned by the target project's account (a planted socket gets no text);
* a per-sender rate limit stops two sessions from ping-ponging tokens forever.

Every start / register / send / deliver / refuse is a JSON line in
``~/.claude/logs/peer-channel.log``. Stdlib only and SELF-CONTAINED: it is run with
``python3 -I`` (no sibling imports), so it imports nothing from the repo.
"""
import argparse
import json
import os
import pwd
import re
import socket
import struct
import sys
import threading
import time
from collections import deque

SOCK_DIR = "/run/claude-peers"
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_FILE = "projects-registry.json"
LINKS_FILE = "peer-links.json"
LOG_MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 4000
MAX_WIRE = 64 * 1024
IO_TIMEOUT_S = 5.0
RATE_MAX = 20
RATE_WINDOW_S = 600
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SOCK_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]{0,63})\.(\d{1,10})\.sock$")
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"
INSTRUCTIONS = ('Messages from related projects\' Claude sessions arrive as a peer '
                '<channel ... from="P">. Reply with peer_send(to=P) only when an '
                'answer is needed.')
TOOLS = [
    {"name": "peers",
     "description": "List related projects and whether a Claude session is live.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "peer_send",
     "description": "Send a message to a related project's live Claude session(s).",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string"},
                                    "text": {"type": "string"}},
                     "required": ["to", "text"]}},
]
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# --------------------------------------------------------------------------- #
# Logging (per account, JSON lines, size-capped one-step rotation)
# --------------------------------------------------------------------------- #
class Log:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def __call__(self, event, **fields):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event,
               "pid": os.getpid()}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > LOG_MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                sys.stderr.write("peer-channel: log write failed: %s\n" % e)


# --------------------------------------------------------------------------- #
# Config: registry (project -> host/account/path) + links (allowlist)
# --------------------------------------------------------------------------- #
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def host_projects(registry, host):
    """{project: entry} for the registry entries on ``host`` with a valid name
    and an explicit account."""
    out = {}
    for p in registry if isinstance(registry, list) else []:
        if not isinstance(p, dict) or p.get("host") != host:
            continue
        name, acct = p.get("name"), p.get("account")
        if isinstance(name, str) and PROJECT_RE.match(name) and isinstance(acct, str) and acct:
            out[name] = p
    return out


def host_links(links_doc, host):
    """Set of frozenset({a, b}) project pairs linked on ``host``."""
    hosts = links_doc.get("hosts") if isinstance(links_doc, dict) else None
    entry = hosts.get(host) if isinstance(hosts, dict) else None
    raw = entry.get("links") if isinstance(entry, dict) else None
    pairs = set()
    for pair in raw if isinstance(raw, list) else []:
        if (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)
                and pair[0] != pair[1]):
            pairs.add(frozenset(pair))
    return pairs


def linked_to(pairs, project):
    return sorted(next(iter(p - {project})) for p in pairs if project in p)


def resolve_project(projects, user, cwd, home):
    """The project of THIS session: the longest registry path (expanded against
    the account's own home) that equals or contains ``cwd``, among projects whose
    account is ``user``. None when the cwd is no registered project."""
    best, best_len = None, -1
    real = os.path.realpath(cwd)
    for name, p in projects.items():
        if p.get("account") != user or not isinstance(p.get("path"), str):
            continue
        path = p["path"]
        if path.startswith("~"):
            path = home + path[1:]
        path = os.path.realpath(path)
        if (real == path or real.startswith(path + os.sep)) and len(path) > best_len:
            best, best_len = name, len(path)
    return best


# --------------------------------------------------------------------------- #
# Socket helpers
# --------------------------------------------------------------------------- #
def peer_uid(sock):
    """uid of the process on the other end (Linux SO_PEERCRED: pid, uid, gid)."""
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)[1]


def read_line(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_WIRE:
            raise ValueError("message too large")
    return buf.split(b"\n", 1)[0].decode("utf-8")


def socket_name(project, pid):
    return "%s.%d.sock" % (project, pid)


def parse_socket_name(fname):
    """(project, pid) from ``<project>.<pid>.sock``, else None."""
    m = SOCK_NAME_RE.match(fname)
    return (m.group(1), int(m.group(2))) if m else None


def clean_text(text):
    return _CTRL_RE.sub("", text)


# --------------------------------------------------------------------------- #
# The peer node: config, listener, sender
# --------------------------------------------------------------------------- #
class Peer:
    def __init__(self, host, user, uid, cwd, home, project_arg=None,
                 sock_dir=SOCK_DIR, config_dir=CONFIG_DIR, log=None,
                 notify=None, clock=time.monotonic):
        self.host, self.user, self.uid = host, user, uid
        self.sock_dir, self.config_dir = sock_dir, config_dir
        self.log = log or Log(os.path.join(home, ".claude", "logs", "peer-channel.log"))
        self.notify = notify or (lambda text, meta: None)
        self.clock = clock
        self.rate = {}
        self.rate_lock = threading.Lock()
        self.listener = None
        self.sock_path = None
        self.project = None
        self.problem = None
        projects = self._projects()
        if project_arg:
            p = projects.get(project_arg)
            if p is None or p.get("account") != user:
                self.problem = ("project %r is not a %s project of account %s"
                                % (project_arg, host, user))
            else:
                self.project = project_arg
        else:
            self.project = resolve_project(projects, user, cwd, home)
            if self.project is None:
                self.problem = "this session's directory is no registered %s project" % host
        self.log("start", project=self.project, user=user, cwd=cwd, host=host,
                 problem=self.problem)

    # -- config (re-read on every use, so a push applies without a restart) --
    def _projects(self):
        try:
            return host_projects(load_json(os.path.join(self.config_dir, REGISTRY_FILE)),
                                 self.host)
        except (OSError, ValueError) as e:
            self.log("config-error", file=REGISTRY_FILE, error=str(e))
            return {}

    def _pairs(self):
        try:
            return host_links(load_json(os.path.join(self.config_dir, LINKS_FILE)), self.host)
        except (OSError, ValueError) as e:
            self.log("config-error", file=LINKS_FILE, error=str(e))
            return set()

    def _account_uid(self, project):
        p = self._projects().get(project)
        if p is None:
            return None
        try:
            return pwd.getpwnam(p["account"]).pw_uid
        except KeyError:
            return None

    # -- listener --------------------------------------------------------- #
    def start_listener(self):
        if self.project is None:
            return False
        if not os.path.isdir(self.sock_dir):
            self.problem = "shared dir %s is missing (root leg not applied)" % self.sock_dir
            self.log("listen-failed", project=self.project, reason=self.problem)
            return False
        self._sweep_own_stale()
        path = os.path.join(self.sock_dir, socket_name(self.project, os.getpid()))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            if os.path.lexists(path) and os.lstat(path).st_uid == self.uid:
                os.unlink(path)
            srv.bind(path)
            os.chmod(path, 0o666)
            srv.listen(16)
        except OSError as e:
            srv.close()
            self.problem = "cannot listen on %s: %s" % (path, e)
            self.log("listen-failed", project=self.project, reason=self.problem)
            return False
        self.listener, self.sock_path = srv, path
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.log("register", project=self.project, socket=path)
        return True

    def _sweep_own_stale(self):
        """Remove THIS account's sockets whose pid is gone (a crashed session).
        Never touches another account's file (the sticky dir forbids it anyway)."""
        try:
            names = os.listdir(self.sock_dir)
        except OSError as e:
            self.log("sweep-failed", error=str(e))
            return
        for fname in names:
            parsed = parse_socket_name(fname)
            path = os.path.join(self.sock_dir, fname)
            if parsed is None:
                continue
            try:
                if os.lstat(path).st_uid != self.uid:
                    continue
                os.kill(parsed[1], 0)
            except ProcessLookupError:
                self._unlink(path, "stale-removed")
            except OSError:
                continue

    def _unlink(self, path, event):
        try:
            os.unlink(path)
            self.log(event, socket=path)
        except OSError as e:
            self.log(event + "-failed", socket=path, error=str(e))

    def close(self):
        if self.listener is None:
            return
        listener, self.listener = self.listener, None
        listener.close()
        if self.sock_path:
            self._unlink(self.sock_path, "unregister")

    def _accept_loop(self):
        while self.listener is not None:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn):
        with conn:
            conn.settimeout(IO_TIMEOUT_S)
            try:
                uid = peer_uid(conn)
                req = json.loads(read_line(conn))
                reply = self.handle_request(req, uid)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                self.log("refuse", project=self.project, reason="bad request: %s" % e)
                reply = {"ok": False, "error": "bad request"}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError as e:
                self.log("reply-failed", project=self.project, error=str(e))

    def handle_request(self, req, uid):
        """Authenticate + authorise one inbound request from kernel-verified ``uid``.
        Returns the reply dict; delivers a ``msg`` as a channel event."""
        def refuse(reason, **extra):
            self.log("refuse", project=self.project, uid=uid, reason=reason, **extra)
            return {"ok": False, "error": reason}

        if self.project is None:
            return refuse("no project")
        if not isinstance(req, dict) or req.get("v") != 1:
            return refuse("unsupported request")
        op, sender = req.get("op"), req.get("from")
        try:
            sender_user = pwd.getpwuid(uid).pw_name
        except KeyError:
            return refuse("unknown uid")
        if not isinstance(sender, str) or not PROJECT_RE.match(sender):
            return refuse("bad sender name", sender_user=sender_user)
        p = self._projects().get(sender)
        if p is None or p.get("account") != sender_user:
            return refuse("sender %s is not a %s project of %s" % (sender, self.host, sender_user),
                          sender=sender, sender_user=sender_user)
        if req.get("to") != self.project:
            return refuse("wrong target", sender=sender, target=str(req.get("to"))[:80])
        if frozenset((sender, self.project)) not in self._pairs():
            return refuse("no link %s<->%s" % (sender, self.project), sender=sender)
        if op == "ping":
            return {"ok": True, "project": self.project}
        if op != "msg":
            return refuse("unknown op", sender=sender)
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return refuse("empty text", sender=sender)
        if len(text) > MAX_TEXT:
            return refuse("text over %d chars" % MAX_TEXT, sender=sender)
        with self.rate_lock:
            now = self.clock()
            q = self.rate.setdefault(sender, deque())
            while q and now - q[0] > RATE_WINDOW_S:
                q.popleft()
            if len(q) >= RATE_MAX:
                limited = True
            else:
                limited = False
                q.append(now)
        if limited:
            return refuse("rate limit (%d per %ds)" % (RATE_MAX, RATE_WINDOW_S), sender=sender)
        text = clean_text(text)
        self.notify(text, {"from": sender})
        self.log("deliver", project=self.project, sender=sender, sender_user=sender_user,
                 chars=len(text), preview=text[:200])
        return {"ok": True}

    # -- sender ----------------------------------------------------------- #
    def _target_sockets(self, project):
        try:
            names = sorted(os.listdir(self.sock_dir))
        except OSError as e:
            self.log("list-failed", dir=self.sock_dir, error=str(e))
            return []
        return [os.path.join(self.sock_dir, f) for f in names
                if (parse_socket_name(f) or (None,))[0] == project]

    def _call(self, path, want_uid, req):
        """One request to one listener; refuses a listener whose kernel-reported
        uid is not the target account's (anti-squatting) BEFORE sending."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(IO_TIMEOUT_S)
        try:
            s.connect(path)
            got = peer_uid(s)
            if got != want_uid:
                self.log("refuse", project=self.project, socket=path,
                         reason="listener uid %d is not the target account %d" % (got, want_uid))
                return {"ok": False, "error": "impostor socket"}
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            reply = json.loads(read_line(s) or "{}")
            return reply if isinstance(reply, dict) else {"ok": False, "error": "bad reply"}
        except (OSError, ValueError, UnicodeDecodeError) as e:
            return {"ok": False, "error": "unreachable (%s)" % e.__class__.__name__}
        finally:
            s.close()

    def _check_target(self, to):
        if self.project is None:
            return self.problem or "no project"
        if not isinstance(to, str) or not PROJECT_RE.match(to):
            return "bad project name"
        if frozenset((self.project, to)) not in self._pairs():
            return "%s is not linked with %s (peer-links.json)" % (to, self.project)
        if self._account_uid(to) is None:
            return "%s has no account on %s" % (to, self.host)
        return None

    def send(self, to, text):
        err = self._check_target(to)
        if err is None and (not isinstance(text, str) or not text.strip()):
            err = "empty text"
        if err is None and len(text) > MAX_TEXT:
            err = "text over %d chars" % MAX_TEXT
        if err:
            self.log("send-refused", project=self.project, target=str(to)[:80], reason=err)
            return False, err
        want = self._account_uid(to)
        results = [self._call(path, want, {"v": 1, "op": "msg", "from": self.project,
                                           "to": to, "text": text})
                   for path in self._target_sockets(to)]
        ok = sum(1 for r in results if r.get("ok"))
        errors = sorted({str(r.get("error", "?"))[:120] for r in results if not r.get("ok")})
        self.log("send", project=self.project, target=to, delivered=ok,
                 attempted=len(results), errors=errors, chars=len(text), preview=text[:200])
        if ok:
            return True, "delivered to %d %s session(s)" % (ok, to)
        return False, ("%s has no live session" % to if not results
                       else "not delivered: " + "; ".join(errors))

    def list_peers(self):
        if self.project is None:
            return False, self.problem or "no project"
        lines = []
        for other in linked_to(self._pairs(), self.project):
            want = self._account_uid(other)
            if want is None:
                lines.append("%s: no account on %s" % (other, self.host))
                continue
            live = sum(1 for path in self._target_sockets(other)
                       if self._call(path, want, {"v": 1, "op": "ping", "from": self.project,
                                                  "to": other}).get("ok"))
            lines.append("%s: %s" % (other, "live (%d)" % live if live else "offline"))
        self.log("peers", project=self.project, result=lines)
        return True, ("\n".join(lines) if lines
                      else "no related projects linked to %s" % self.project)


# --------------------------------------------------------------------------- #
# MCP stdio server (JSON-RPC 2.0, one message per line)
# --------------------------------------------------------------------------- #
class McpServer:
    def __init__(self, peer, out=None):
        self.peer = peer
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.ready = False
        self.pending = []
        peer.notify = self.channel_event

    def _write_locked(self, obj):
        self.out.write(json.dumps(obj) + "\n")
        self.out.flush()

    def write(self, obj):
        with self.lock:
            self._write_locked(obj)

    def channel_event(self, text, meta):
        msg = {"jsonrpc": "2.0", "method": "notifications/claude/channel",
               "params": {"content": text, "meta": meta}}
        with self.lock:
            if self.ready:
                self._write_locked(msg)
            else:
                self.pending.append(msg)

    def handle(self, msg):
        if not isinstance(msg, dict):
            return
        mid, method = msg.get("id"), msg.get("method")
        if method == "notifications/initialized":
            with self.lock:
                self.ready = True
                for m in self.pending:
                    self._write_locked(m)
                self.pending = []
            return
        if mid is None:
            return
        if method == "initialize":
            asked = (msg.get("params") or {}).get("protocolVersion")
            self.write({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": asked if asked in PROTOCOLS else DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}, "experimental": {"claude/channel": {}}},
                "serverInfo": {"name": "peer", "version": "1"},
                "instructions": INSTRUCTIONS}})
        elif method == "ping":
            self.write({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            self.write({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            args = params.get("arguments") or {}
            if params.get("name") == "peers":
                ok, text = self.peer.list_peers()
            elif params.get("name") == "peer_send" and isinstance(args, dict):
                ok, text = self.peer.send(args.get("to"), args.get("text"))
            else:
                ok, text = False, "unknown tool"
            self.write({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": not ok}})
        else:
            # server/discover (the "modern" era probe) lands here: a legacy answer
            # keeps the connection on the era that carries unsolicited notifications.
            self.write({"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": "method not found"}})

    def run(self, inp=None):
        for line in inp or sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                self.peer.log("bad-jsonrpc", chars=len(line))
                continue
            self.handle(msg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="peer channel MCP server (#1266)")
    ap.add_argument("mode", nargs="?", default="serve", choices=["serve"])
    ap.add_argument("--project", help="claim a project of this account instead of the cwd's")
    a = ap.parse_args(argv)
    pw = pwd.getpwuid(os.getuid())
    peer = Peer(socket.gethostname().split(".")[0], pw.pw_name, os.getuid(), os.getcwd(),
                pw.pw_dir, project_arg=a.project)
    server = McpServer(peer)
    peer.start_listener()
    try:
        server.run()
    finally:
        peer.close()
        peer.log("stop", project=peer.project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
