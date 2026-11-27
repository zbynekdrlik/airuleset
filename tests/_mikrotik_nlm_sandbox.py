"""A sandbox that runs a copy of scripts/root/mikrotik-nlm-net as the test
user (#1401). Only the named root-side constants and the pinned PATH are
swapped (each swap must hit exactly one line); `id -u` answers 0, and stub
`nmcli`, `ip`, `tcpdump`, `runuser` and netinstall record their argv."""
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "root" / "mikrotik-nlm-net"
PINNED_PATH = "export PATH=/usr/sbin:/usr/bin:/sbin:/bin\n"

# The fake NetworkManager: a JSON {uuid: name} file, every call logged.
NMCLI = r'''#!/usr/bin/python3 -I
import json, sys, uuid
DB, LOG = %(db)r, %(log)r
argv = sys.argv[1:]
with open(LOG, "a") as f:
    f.write(json.dumps(argv) + "\n")
db = json.load(open(DB))
def save():
    json.dump(db, open(DB, "w"))
def find(kind, val):
    return [u for u, n in db.items() if (n if kind == "id" else u) == val]
if argv[:2] == ["-g", "connection.uuid"] and argv[2:4] == ["connection", "show"]:
    hits = find(argv[4], argv[5])
    print("\n".join(hits))
    sys.exit(0 if hits else 10)
if argv[:2] == ["-g", "NAME"] and argv[2:4] == ["connection", "show"]:
    print("\n".join(db.values()))
    sys.exit(0)
if argv[:2] == ["-g", "GENERAL.DEVICES"]:
    print("enp39s0" if find(argv[4], argv[5]) else "")
    sys.exit(0 if find(argv[4], argv[5]) else 10)
if argv[:2] == ["connection", "add"]:
    name = argv[argv.index("con-name") + 1]
    u = str(uuid.uuid4())
    db[u] = name
    save()
    print("Connection '%%s' (%%s) successfully added." %% (name, u))
    sys.exit(0)
if argv[:2] == ["connection", "delete"]:
    for u in find(argv[2], argv[3]):
        del db[u]
    save()
    sys.exit(0)
if argv[:2] == ["connection", "modify"]:
    if not find(argv[2], argv[3]):
        sys.exit(10)
    if argv[4:5] == ["connection.id"]:
        db[find(argv[2], argv[3])[0]] = argv[5]
        save()
'''

LOGGER = '''#!/bin/bash
printf '%%s\\n' "$(basename "$0") $*" >> %(log)s
'''

RUNUSER = '''#!/bin/bash
[ "$1" = -u ] && [ "$3" = -- ] || exit 9
printf '%%s\\n' "runuser $2 ${*:4}" >> %(log)s
shift 3
exec "$@"
'''

NETINSTALL = '''#!/bin/bash
printf '%%s\\n' "netinstall $*" >> %(log)s
for a in "$@"; do
    if [ -f "$a" ]; then printf '%%s\\n' "staged $a $(cat "$a")" >> %(log)s; fi
done
'''


class Sandbox:
    """One sandboxed copy. ``extra_consts`` maps a constant name to the value
    the copy gets (a callable gets the sandbox dir); every name must be
    assigned on exactly one script line."""

    def __init__(self, testcase, **extra_consts):
        self._tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmp.cleanup)
        d = self.dir = Path(self._tmp.name)
        for sub in ("bin", "opt", "stage", "log", "state-parent"):
            (d / sub).mkdir()
        os.chmod(d, 0o755)
        self.calls_log = d / "calls.log"
        self.nm_log = d / "nmcli.jsonl"
        self.db = d / "nm.json"
        self.set_connections({"u-lan": "dev1-lan", "u-dante": "dante-vlan",
                              "u-r2": "router2-backup", "u-pz": "mikrotik-pz-setup"})
        self.log = d / "log" / "mikrotik-nlm-net.log"
        self.netinstall = d / "opt" / "netinstall-cli"
        self._stub("nmcli", NMCLI % {"db": str(self.db), "log": str(self.nm_log)})
        for name in ("ip", "tcpdump"):
            self._stub(name, LOGGER % {"log": self.calls_log})
        self._stub("runuser", RUNUSER % {"log": self.calls_log})
        self._stub("id", '#!/bin/bash\nif [ "$1" = -u ]; then echo 0; else exec /usr/bin/id "$@"; fi\n')
        self.netinstall.write_text(NETINSTALL % {"log": self.calls_log})
        os.chmod(self.netinstall, 0o755)
        os.chmod(d / "opt", 0o755)
        consts = {"LOG": str(self.log), "NETINSTALL": str(self.netinstall),
                  "STAGE_PARENT": str(d / "stage"), "ROOT_UID": str(os.getuid()),
                  "ROOT_GID": str(os.getgid())}
        consts.update({k: str(v(d) if callable(v) else v) for k, v in extra_consts.items()})
        src = SCRIPT.read_text()
        assert src.count(PINNED_PATH) == 1
        src = src.replace(PINNED_PATH, "export PATH=%s:/usr/sbin:/usr/bin:/sbin:/bin\n" % (d / "bin"))
        lines = src.split("\n")
        for name, value in consts.items():
            hits = [i for i, ln in enumerate(lines) if ln.startswith(name + "=")]
            assert len(hits) == 1, "%s= must be assigned on exactly one line, got %d" % (name, len(hits))
            lines[hits[0]] = "%s=%s" % (name, value)
        self.script = d / "mikrotik-nlm-net"
        self.script.write_text("\n".join(lines))
        os.chmod(self.script, 0o755)

    def _stub(self, name, body):
        p = self.dir / "bin" / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def set_connections(self, conns):
        self.db.write_text(json.dumps(conns))

    def connections(self):
        return json.loads(self.db.read_text())

    def run(self, *args):
        return subprocess.run([str(self.script), *args], capture_output=True, text=True,
                              cwd=str(self.dir), timeout=60)

    def nm_calls(self):
        if not self.nm_log.exists():
            return []
        return [json.loads(ln) for ln in self.nm_log.read_text().splitlines()]

    def calls(self):
        return self.calls_log.read_text().splitlines() if self.calls_log.exists() else []
