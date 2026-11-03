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
PHONE_READ_TTL_S = 1.2
PHONE_READ_FAIL_TTL_S = 10              # a failing tab is not retried at poll speed
PHONE_READ_TIMEOUT_S = 15
PHONE_READ_MAX_SPAWNS = 4               # across tabs, per gateway
PHONE_READ_MAX_BYTES = 512 * 1024
PHONE_READ_MAX_IDS = 256


def build_capture_argv(entry):
    """The argv that prints this tab's pane with history, and its extra env. The
    tab's own key is enough: its forced command takes the one verb, so no second
    key and no shell is involved. `-T`: a read needs no PTY."""
    import cli_webterm as w
    if entry.get("local"):
        cmd = w._remote_command(entry["preferred"],
                                start_dir_chain=entry.get("start_dir_chain"),
                                user=entry.get("user"))
        return ["sh", "-c", cmd], {"SSH_ORIGINAL_COMMAND": WEBTERM_CAPTURE_VERB}
    prefix = [a for a in w._ssh_interactive_prefix(entry) if a != "-t"]
    return (prefix + ["-T", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5",
                      "-o", "ServerAliveCountMax=2",
                      "%s@%s" % (entry["user"], entry["host"]),
                      WEBTERM_CAPTURE_VERB], {})


def capture_main(argv, inventory_path=None):
    """Gateway child: print one tab's pane. The id is checked against the lane's
    inventory exactly like `connect_main`; the env var is the lane boundary."""
    import cli_webterm as w
    if inventory_path is None:
        inventory_path = os.environ.get("WEBTERM_INVENTORY") or None
    if len(argv) != 1:
        sys.stderr.write("webterm: capture takes one session id\n")
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
    cmd, env = build_capture_argv(entry)
    # exec, never a child: the gateway's timeout must kill the ssh itself, and a
    # normal python exit would run the atexit unlink of the shared host-key pin.
    os.execvpe(cmd[0], cmd, dict(os.environ, **env))


class PhoneReader:
    """The `/phone-read` answer for one lane. `capture` is injectable (tests)."""

    def __init__(self, inventory, id_re, capture=None):
        self.inventory = inventory
        self.id_re = id_re
        self.capture = capture or self._capture
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

    async def text(self, sid):
        lock = self._locks.setdefault(sid, asyncio.Lock())
        async with lock:                    # a burst for one tab waits for one read
            hit = self._cache.get(sid)
            ttl = PHONE_READ_TTL_S if hit and hit[1] is not None else PHONE_READ_FAIL_TTL_S
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            self._spawns = self._spawns or asyncio.Semaphore(PHONE_READ_MAX_SPAWNS)
            try:
                async with self._spawns:
                    text = await self.capture(sid)
            except (OSError, asyncio.TimeoutError) as e:
                sys.stderr.write("webterm-gateway: phone read %s failed: %r\n" % (sid, e))
                text = None
            self._cache[sid] = (time.monotonic(), text)
            if len(self._cache) > PHONE_READ_MAX_IDS:   # ids are charset-checked only
                self._cache.clear()
                self._locks = {sid: lock}
            return text

    async def _capture(self, sid):
        """The pane of tab `sid`, or None. A child past the timeout is killed. The
        inventory reaches it by env only, the `webterm-connect` boundary (#612)."""
        repo = Path(__file__).resolve().parent
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(repo / "cli_webterm.py"), "webterm-capture", sid,
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
        return out[-PHONE_READ_MAX_BYTES:]
