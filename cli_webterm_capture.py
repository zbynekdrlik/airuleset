"""airuleset webterm: the read behind the phone view (#1287).

`build_capture_argv` / `capture_main` run in a child of the gateway and print one
tab's pane through that tab's own forced key. `PhoneReader` is the gateway side:
one spawn per tab at most every TTL, so two phones or a fast poll cost one ssh.
"""
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path
import time

WEBTERM_CAPTURE_VERB = "wt-capture"     # the one word a forced key may ask for
WEBTERM_CAPTURE_LINES = 3000            # history the phone view can scroll
WEBTERM_HISTORY_VERB = "wt-history"    # + " <byte offset>": the pane's session transcript
WEBTERM_HISTORY_FIRST_BYTES = 2000000   # a first read takes this much of the transcript tail
# The read branch of the forced attach body (cli_webterm._ATTACH_BODY), run after
# `$T` is resolved and before the mouse option, the attach and the create fallback.
# Only these exact shapes; the offset must be digits. No session: no output, rc 1.
# Claude runs fullscreen (alternate screen), so its pane has no tmux history; the
# history is the newest transcript in the pane's project dir (Claude's own key:
# every non-alphanumeric char of the cwd becomes `-`). First line: name size start.
WEBTERM_READ_SH = (
    'case "${SSH_ORIGINAL_COMMAND:-}" in '
    + WEBTERM_CAPTURE_VERB + ') [ -n "$T" ] || exit 1; '
    'exec tmux capture-pane -p -J -S -' + str(WEBTERM_CAPTURE_LINES) + ' -t "=$T:";; '
    '"' + WEBTERM_HISTORY_VERB + ' "*) o=${SSH_ORIGINAL_COMMAND#* }; '
    'case "$o" in ""|*[!0-9]*) exit 2;; esac; [ -n "$T" ] || exit 1; '
    'd=$(tmux display -p -t "=$T:" "#{pane_current_path}" | sed "s/[^A-Za-z0-9]/-/g"); '
    'f=$(ls -t "$HOME/.claude/projects/$d"/*.jsonl 2>/dev/null | head -n 1); '
    '[ -n "$f" ] || exit 1; s=$(wc -c < "$f"); '
    'if [ "$o" -eq 0 ] || [ "$o" -gt "$s" ] || [ $((s - o)) -gt ' + str(WEBTERM_HISTORY_FIRST_BYTES)
    + ' ]; then o=$((s > ' + str(WEBTERM_HISTORY_FIRST_BYTES)
    + ' ? s - ' + str(WEBTERM_HISTORY_FIRST_BYTES) + ' : 0)); fi; '
    'echo "${f##*/} $s $o"; tail -c +$((o + 1)) "$f" | head -c $((s - o)); exit 0;; '
    'esac; '
)
PHONE_READ_TTL_S = 1.2
PHONE_HISTORY_TTL_S = 5
PHONE_HISTORY_KEEP = 400                # messages a tab keeps
PHONE_HISTORY_TEXT_MAX = 20000          # chars of one message
PHONE_READ_FAIL_TTL_S = 10              # a failing tab is not retried at poll speed
PHONE_READ_TIMEOUT_S = 15
PHONE_READ_MAX_SPAWNS = 4               # across tabs, per gateway
PHONE_READ_MAX_BYTES = 512 * 1024
PHONE_READ_MAX_IDS = 256


def build_capture_argv(entry, verb=WEBTERM_CAPTURE_VERB):
    """The argv that prints this tab's pane (or, with the history verb, its session
    transcript), and its extra env. The tab's own key is enough: its forced command
    takes the verb, so no second key and no shell is involved. `-T`: no PTY."""
    import cli_webterm as w
    if entry.get("local"):
        cmd = w._remote_command(entry["preferred"],
                                start_dir_chain=entry.get("start_dir_chain"),
                                user=entry.get("user"))
        return ["sh", "-c", cmd], {"SSH_ORIGINAL_COMMAND": verb}
    prefix = [a for a in w._ssh_interactive_prefix(entry) if a != "-t"]
    return (prefix + ["-T", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5",
                      "-o", "ServerAliveCountMax=2",
                      "%s@%s" % (entry["user"], entry["host"]),
                      verb], {})


def capture_main(argv, inventory_path=None):
    """Gateway child: print one tab's pane. The id is checked against the lane's
    inventory exactly like `connect_main`; the env var is the lane boundary."""
    import cli_webterm as w
    if inventory_path is None:
        inventory_path = os.environ.get("WEBTERM_INVENTORY") or None
    verb = WEBTERM_CAPTURE_VERB
    if len(argv) == 3 and argv[1] == "history" and argv[2].isdigit() and argv[2].isascii():
        verb = "%s %d" % (WEBTERM_HISTORY_VERB, int(argv[2]))
    elif len(argv) != 1:
        sys.stderr.write("webterm: capture takes a session id [history <offset>]\n")
        return 2
    try:
        inv = w._load_inventory(inventory_path)
    except (OSError, ValueError) as e:
        sys.stderr.write("webterm: inventory unreadable: %s\n" % e)
        return 2
    entry = next((e for e in inv if e.get("id") == argv[0]), None)
    if entry is None:
        sys.stderr.write("webterm: unknown session id %r\n" % argv[0])
        return 2
    cmd, env = build_capture_argv(entry, verb)
    # exec, never a child: the gateway's timeout must kill the ssh itself, and a
    # normal python exit would run the atexit unlink of the shared host-key pin.
    os.execvpe(cmd[0], cmd, dict(os.environ, **env))


class PhoneReader:
    """The `/phone-read` answer for one lane. `capture` is injectable (tests)."""

    def __init__(self, inventory, id_re, capture=None, history=None):
        self.inventory = inventory
        self.id_re = id_re
        self.capture = capture or self._capture
        self.history_read = history or self._history_read
        self._hist = {}                     # id -> History
        self._cache = {}                    # id -> (time, bytes|None)
        self._locks = {}
        self._spawns = None                 # the semaphore needs the running loop

    async def body(self, target):
        """The JSON answer. A failure is `{"ok":false}`: the page shows it and its
        console stays clean. The id passes the charset check, then the inventory."""
        ids = urllib.parse.parse_qs(target.partition("?")[2]).get("id") or []
        sid = ids[0] if len(ids) == 1 else ""
        text = await self.text(sid) if self.id_re.fullmatch(sid) else None
        out = {"ok": text is not None}
        if text is not None:
            out["text"] = text.decode("utf-8", "replace")
        return json.dumps(out)

    async def history_body(self, target):
        """`{"ok", "first", "msgs"}`: the tab's messages from index `since` on.
        `first` is the oldest index kept; a page holding less starts over."""
        query = urllib.parse.parse_qs(target.partition("?")[2])
        ids, since = query.get("id") or [], (query.get("since") or ["0"])[0]
        sid = ids[0] if len(ids) == 1 else ""
        if not (self.id_re.fullmatch(sid) and since.isdigit()):
            return json.dumps({"ok": False})
        hist = self._hist.get(sid)
        if hist is None:
            if len(self._hist) >= PHONE_READ_MAX_IDS:
                self._hist.clear()
            hist = self._hist[sid] = History()
        async with hist.lock:
            if time.monotonic() - hist.read_at >= PHONE_HISTORY_TTL_S:
                hist.read_at = time.monotonic()
                try:
                    async with self._spawn_slot():
                        raw = await self.history_read(sid, hist.offset)
                except (OSError, asyncio.TimeoutError) as e:
                    sys.stderr.write("webterm-gateway: phone history %s failed: %r\n" % (sid, e))
                    raw = None
                hist.ok = raw is not None and hist.feed(raw)
        start = max(int(since), hist.first)
        return json.dumps({"ok": hist.ok, "first": hist.first,
                           "msgs": hist.msgs[start - hist.first:]})

    def _spawn_slot(self):
        self._spawns = self._spawns or asyncio.Semaphore(PHONE_READ_MAX_SPAWNS)
        return self._spawns

    async def text(self, sid):
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:                    # a burst for one tab waits for one read
            hit = self._cache.get(sid)
            ttl = PHONE_READ_TTL_S if hit and hit[1] is not None else PHONE_READ_FAIL_TTL_S
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            try:
                async with self._spawn_slot():
                    text = await self.capture(sid)
            except (OSError, asyncio.TimeoutError) as e:
                sys.stderr.write("webterm-gateway: phone read %s failed: %r\n" % (sid, e))
                text = None
            self._cache[sid] = (time.monotonic(), text)
            if len(self._cache) > PHONE_READ_MAX_IDS:   # ids are charset-checked only
                self._cache.clear()
                self._locks = {sid: lock}
            return text

    async def _history_read(self, sid, offset):
        return await self._capture(sid, "history", str(offset))

    async def _capture(self, sid, *extra):
        """The pane of tab `sid` (or with `extra`, its transcript bytes), or None. A
        child past the timeout is killed. The inventory reaches it by env only,
        the `webterm-connect` boundary (#612)."""
        repo = Path(__file__).resolve().parent
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(repo / "cli_webterm.py"), "webterm-capture", sid, *extra,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ, WEBTERM_INVENTORY=str(self.inventory)))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), PHONE_READ_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            sys.stderr.write("webterm-gateway: phone read %s rc=%s: %s\n"
                             % (sid, proc.returncode, err[-200:].decode("utf-8", "replace")))
            return None
        return out if extra else out[-PHONE_READ_MAX_BYTES:]


def message_of(entry):
    """`{"r": "u"|"a", "t": text}` for what the owner typed or Claude wrote, else
    None. Tool calls, results, thinking, channel and task notices are left out."""
    m = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(m, dict) or entry.get("isSidechain") or entry.get("isMeta"):
        return None
    content = m.get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(blocks, list):
        return None
    text = "\n\n".join(b.get("text") or "" for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        return None
    if entry.get("type") == "user":
        origin = entry.get("origin")
        if (origin.get("kind") if isinstance(origin, dict) else origin) not in (None, "human"):
            return None
        if text.startswith("<"):            # command and reminder wrappers
            return None
        return {"r": "u", "t": text[:PHONE_HISTORY_TEXT_MAX]}
    if entry.get("type") == "assistant":
        return {"r": "a", "t": text[:PHONE_HISTORY_TEXT_MAX]}
    return None


class History:
    """One tab's messages, read incrementally from its newest transcript."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.read_at = float("-inf")
        self.name, self.offset, self.rest = None, 0, b""
        self.first, self.msgs, self.ok = 0, [], False

    def feed(self, raw):
        """Take one `name size start` header plus bytes; False on a bad header."""
        head, _, data = raw.partition(b"\n")
        try:
            name, size, start = head.decode("utf-8").split(" ")
            size, start = int(size), int(start)
        except ValueError:
            return False
        if name != self.name or start != self.offset:   # a new session or a fresh read
            self.first += len(self.msgs)
            self.name, self.msgs, self.rest = name, [], b""
            if start > 0:                   # a tail starts inside a line
                data = data.partition(b"\n")[2]
        data = self.rest + data
        lines = data.split(b"\n")
        self.rest = lines.pop()
        for line in lines:
            try:
                msg = message_of(json.loads(line))
            except ValueError:
                continue
            if msg:
                self.msgs.append(msg)
        drop = max(0, len(self.msgs) - PHONE_HISTORY_KEEP)
        self.first, self.msgs = self.first + drop, self.msgs[drop:]
        self.offset = size
        return True
