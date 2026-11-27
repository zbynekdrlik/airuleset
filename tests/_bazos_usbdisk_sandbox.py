"""A sandbox that runs a copy of scripts/root/bazos-usbdisk as the test user
(#1394). Only the pinned PATH and LOG= are swapped (each must hit exactly one
line); `id -u` answers 0, and stub lsblk/findmnt/blkid/smartctl/dd/blockdev/
journalctl answer from a JSON state file and record their argv."""
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "root" / "bazos-usbdisk"
PINNED_PATH = "export PATH=/usr/sbin:/usr/bin:/sbin:/bin\n"

# One python program behind every stub name; argv[0] picks the tool.
TOOL = r'''#!/usr/bin/python3 -I
import json, os, sys
STATE, LOG = %(state)r, %(log)r
tool = os.path.basename(sys.argv[0])
a = sys.argv[1:]
with open(LOG, "a") as f:
    f.write(json.dumps([tool] + a) + "\n")
st = json.load(open(STATE))
disks = st["disks"]
def names(dev):
    for d in disks:
        if "/dev/" + d["name"] == dev:
            return [(d["name"], "disk")] + [(p, "part") for p in d.get("parts", [])]
    return []
if tool == "lsblk":
    if a[:2] == ["-sno", "NAME,TYPE"]:
        print("nvme0n1p2 part\n`-nvme0n1 disk")
    elif a[:3] == ["-dnr", "-o", "NAME,TRAN,TYPE,SERIAL"]:
        for d in disks:
            print(d["name"], d["tran"], "disk", d["serial"])
    elif a[:3] == ["-dnr", "-o", "NAME,TRAN,TYPE"]:
        for d in disks:
            print(d["name"], d["tran"], "disk")
    elif a[:3] == ["-nr", "-o", "NAME,TYPE"]:
        for n, t in names(a[3]):
            print(n, t)
    elif a[:3] == ["-nr", "-o", "MOUNTPOINT"]:
        for n, _ in names(a[3]):
            if n in st.get("mounts", {}):
                print("/mnt/x")
    elif a[:1] == ["-o"]:
        for n, t in names(a[2]):
            print("TREE", n, t)
    else:
        sys.exit("lsblk stub: unknown argv %%r" %% a)
elif tool == "findmnt":
    if a == ["-no", "SOURCE", "/"]:
        print("/dev/nvme0n1p2")
    elif a[:2] == ["-rn", "-S"] and a[3:] == ["-o", "OPTIONS"]:
        opts = st.get("mounts", {}).get(a[2][5:])
        if opts is None:
            sys.exit(1)
        print(opts)
    else:
        sys.exit(1)
elif tool == "blkid":
    print('%%s: PARTLABEL="LDM metadata partition" TYPE="BitLocker"' %% a[-1])
elif tool == "smartctl":
    dtype = a[a.index("-d") + 1] if "-d" in a else "auto"
    rc = st.get("smart_rc", {}).get(dtype, 0)
    print("SMART via %%s rc=%%d" %% (dtype, rc))
    sys.exit(rc)
elif tool == "blockdev":
    print(st["size"])
elif tool == "journalctl":
    print(st.get("kernel", ""), end="")
elif tool == "dd":
    for _ in range(st.get("dd_errors", 0)):
        sys.stderr.write("dd: error reading '/dev/sda': Input/output error\n")
    n = st.get("dd_bytes", st["size"])
    sys.stderr.write("%%d bytes copied\r%%d bytes (17 MB, 16 MiB) copied, 0.2 s, 84 MB/s\n" %% (n // 2, n))
    sys.stderr.write("%%d bytes (17 MB, 16 MiB) copied, 0.2 s, 84 MB/s\n" %% n)
    sys.exit(st.get("dd_rc", 0))
'''

STUBS = ("lsblk", "findmnt", "blkid", "smartctl", "blockdev", "journalctl", "dd")


class Sandbox:

    def __init__(self, testcase):
        self._tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmp.cleanup)
        d = self.dir = Path(self._tmp.name)
        (d / "bin").mkdir()
        self.state_path = d / "state.json"
        self.calls_log = d / "calls.jsonl"
        self.log = d / "bazos-usbdisk.log"
        self.state = {"size": 16777216, "disks": [
            {"name": "nvme0n1", "tran": "nvme", "serial": "ROOTSER1", "parts": ["nvme0n1p2"]},
            {"name": "sda", "tran": "usb", "serial": "S1234", "parts": ["sda1", "sda2"]}]}
        self.save()
        tool = d / "bin" / "_tool"
        tool.write_text(TOOL % {"state": str(self.state_path), "log": str(self.calls_log)})
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        for name in STUBS:
            os.symlink(tool, d / "bin" / name)
        idp = d / "bin" / "id"
        idp.write_text('#!/bin/bash\nif [ "$1" = -u ]; then echo 0; else exec /usr/bin/id "$@"; fi\n')
        idp.chmod(0o755)
        src = SCRIPT.read_text()
        assert src.count(PINNED_PATH) == 1
        src = src.replace(PINNED_PATH, "export PATH=%s:/usr/sbin:/usr/bin:/sbin:/bin\n" % (d / "bin"))
        lines = src.split("\n")
        hits = [i for i, ln in enumerate(lines) if ln.startswith("LOG=")]
        assert len(hits) == 1
        lines[hits[0]] = "LOG=%s" % self.log
        self.script = d / "bazos-usbdisk"
        self.script.write_text("\n".join(lines))
        self.script.chmod(0o755)

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def set(self, **kw):
        self.state.update(kw)
        self.save()

    def run(self, *args):
        return subprocess.run([str(self.script), *args], capture_output=True, text=True,
                              cwd=str(self.dir), timeout=60, stdin=subprocess.DEVNULL)

    def calls(self, tool=None):
        if not self.calls_log.exists():
            return []
        out = [json.loads(ln) for ln in self.calls_log.read_text().splitlines()]
        return [c[1:] for c in out if tool is None or c[0] == tool]

    def log_text(self):
        return self.log.read_text() if self.log.exists() else ""
