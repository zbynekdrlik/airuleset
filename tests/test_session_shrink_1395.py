"""#1395: `session-shrink` drives Claude Code's OWN on-disk transcript compactor.

Claude Code 2.1.330 rewrites a transcript itself (`performCompactTranscript`)
when `CLAUDE_CODE_TRANSCRIPT_LOCAL_GC` is on in a non-interactive session and a
`/compact` runs (`requestCompact`). Proven live on a scratch session: 7 890 993
-> 135 681 bytes, the resume answer unchanged. `session-shrink` is a thin wrapper:
refuse a live or foreign session, back the file up (gzip, name NOT `*.jsonl.gz`,
0600 at create, mtime <= the original), run an ISOLATED
`claude --resume <id> -p /compact` (no hooks, no MCP, no tools, a fixed model,
no CLAUDE* env) in the session's recorded cwd as the file's owner, then verify
a NEW boundary, a smaller file and every kept-forever metadata type (round 3).

The `claude` binary is a fake here (`--claude-bin`): it records its argv, env
and cwd, and rewrites the file the way the native compactor does.
"""
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cli_session_shrink as ss  # noqa: E402

SID = "73cdd596-56bb-4f55-a02a-7d87ec16ef8b"
OTHER = "11111111-1111-4111-8111-111111111111"
OLD_NS = (int(time.time()) - 3600) * 10 ** 9 + 123456789

FAKE = r'''#!/usr/bin/env python3
"""fake claude: log the call, then compact like the native GC does."""
import json, os, sys
log, mode = os.environ["FAKE_LOG"], os.environ.get("FAKE_MODE", "gc")
with open(log, "w") as fh:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(),
               "env": {k: v for k, v in os.environ.items() if k.startswith("CLAUDE")}}, fh)
path = os.environ["FAKE_TRANSCRIPT"]
if mode == "fail":
    sys.exit(3)
if mode == "noop":
    sys.exit(0)
with open(path) as fh:
    lines = fh.readlines()
last = max(i for i, ln in enumerate(lines) if '"compact_boundary"' in ln)
meta = {}
for ln in lines[:last]:
    t = json.loads(ln).get("type")
    if ln.startswith('{"type":') and not t.startswith("api-request"):
        meta[t] = ln
if mode == "drop":
    meta.pop("content-replacement", None)
tail = lines[last:]
if mode != "oldboundary":
    tail = tail + ['{"parentUuid":null,"type":"system","subtype":"compact_boundary",'
                   '"uuid":"b-new","sessionId":"x"}\n']
if mode == "bigger":
    tail = lines + tail
with open(path, "w") as fh:
    fh.writelines(list(meta.values()) + tail)
'''


def sanitize(p):
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def _meta(typ, **kw):
    return json.dumps(dict({"type": typ, "sessionId": SID}, **kw), separators=(",", ":"))


def _rec(n, parent, cwd, **kw):
    rec = dict({"parentUuid": parent, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "x" * 200},
                "uuid": "u%d" % n, "cwd": cwd, "sessionId": SID}, **kw)
    return json.dumps(rec, separators=(",", ":"))


def _session(cwd, pad=50, last_cwd=None):
    lines = [_meta("ai-title", aiTitle="t1"), _meta("content-replacement", ids=["a"]),
             _meta("data-flow-records", n=1), _meta("fork-context-ref", ref="r"),
             _meta("frame-link", url="u"), _meta("memory-mode", on=True),
             _meta("api-request", body="b" * 100)]
    lines += [_rec(i, "u%d" % (i - 1) if i else None, cwd) for i in range(pad)]
    lines += [json.dumps({"parentUuid": None, "logicalParentUuid": "u%d" % (pad - 1),
                          "type": "system", "subtype": "compact_boundary",
                          "uuid": "b1", "cwd": cwd, "sessionId": SID},
                         separators=(",", ":")),
              _rec(900, "b1", last_cwd or cwd, isCompactSummary=True),
              _meta("ai-title", aiTitle="t2")]
    return lines


class _Box(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = d.name
        self.home = os.path.join(d.name, "home")
        os.makedirs(os.path.join(self.home, ".claude", "sessions"))
        self.cwd = os.path.join(d.name, "work")
        os.makedirs(self.cwd)
        self.proj = os.path.join(self.home, ".claude", "projects", sanitize(self.cwd))
        os.makedirs(self.proj)
        self.bin = os.path.join(d.name, "claude")
        with open(self.bin, "w") as fh:
            fh.write(FAKE)
        os.chmod(self.bin, 0o755)
        self.log = os.path.join(d.name, "fake.json")

    def write(self, lines, mode=0o600, name=SID):
        p = os.path.join(self.proj, name + ".jsonl")
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(p, mode)
        os.utime(p, ns=(OLD_NS, OLD_NS))
        return p

    def shrink(self, p, mode="gc", **kw):
        env = dict(os.environ, FAKE_LOG=self.log, FAKE_TRANSCRIPT=p, FAKE_MODE=mode,
                   CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli", CLAUDE_PROJECT_DIR="/x")
        kw.setdefault("min_bytes", 0)
        return ss.shrink(p, home=self.home, claude_bin=self.bin, env=env, **kw)

    def backups(self, name=SID):
        return sorted(n for n in os.listdir(self.proj)
                      if n.startswith(name + ".jsonl.shrink-"))


class Native(_Box):
    def test_runs_an_isolated_native_compact_in_the_sessions_cwd(self):
        p = self.write(_session(self.cwd))
        res = self.shrink(p)
        call = json.load(open(self.log))
        argv = call["argv"]
        self.assertEqual(argv[:4], ["--resume", SID, "-p", "/compact"])
        i = argv.index("--settings")
        self.assertEqual(json.loads(argv[i + 1]), {"disableAllHooks": True})
        self.assertIn("--strict-mcp-config", argv)
        cfg = argv[argv.index("--mcp-config") + 1]
        self.assertEqual(json.loads(cfg), {"mcpServers": {}})
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(call["env"], {"CLAUDE_CODE_TRANSCRIPT_LOCAL_GC": "1"})
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.cwd))
        self.assertLess(res["after"], res["before"])

    def test_the_backup_is_a_full_gzip_copy_not_a_jsonl_gz(self):
        p = self.write(_session(self.cwd))
        orig = open(p, "rb").read()
        res = self.shrink(p)
        b = res["backup"]
        self.assertFalse(b.endswith(".jsonl.gz"))
        self.assertTrue(b.startswith(p + ".shrink-") and b.endswith(".gz"))
        with gzip.open(b, "rb") as gz:
            self.assertEqual(gz.read(), orig)
        st = os.stat(b)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertLessEqual(st.st_mtime_ns, OLD_NS)

    def test_only_the_newest_backup_of_this_session_is_kept(self):
        p = self.write(_session(self.cwd))
        old = p + ".shrink-20200101T000000Z.gz"
        foreign = os.path.join(self.proj, OTHER + ".jsonl.shrink-20200101T000000Z.gz")
        for f in (old, foreign):
            with open(f, "wb") as fh:
                fh.write(b"x")
        res = self.shrink(p)
        self.assertEqual(self.backups(), [os.path.basename(res["backup"])])
        self.assertTrue(os.path.exists(foreign))

    def test_a_leftover_native_temp_is_cleaned(self):
        p = self.write(_session(self.cwd))
        mine = p + ".compact.tmp.deadbeef"
        theirs = os.path.join(self.proj, OTHER + ".jsonl.compact.tmp.deadbeef")
        for f in (mine, theirs):
            with open(f, "w") as fh:
                fh.write("x\n")
        self.shrink(p)
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.exists(theirs))

    def test_a_no_op_compact_is_refused_and_the_backup_kept(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p, mode="noop")
        self.assertIn("no new compact boundary", str(cm.exception))
        self.assertEqual(len(self.backups()), 1)

    def test_an_old_boundary_alone_is_not_enough(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused):
            self.shrink(p, mode="oldboundary")

    def test_a_file_that_did_not_shrink_is_refused(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p, mode="bigger")
        self.assertIn("did not shrink", str(cm.exception))

    def test_every_kept_forever_type_must_survive(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p, mode="drop")
        self.assertIn("content-replacement", str(cm.exception))
        self.assertIn(".shrink-", str(cm.exception))
        self.assertEqual(len(self.backups()), 1)

    def test_a_failed_native_run_keeps_the_backup_and_says_so(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p, mode="fail")
        self.assertIn("exit 3", str(cm.exception))
        self.assertEqual(len(self.backups()), 1)

    def test_no_temp_file_is_left_behind(self):
        p = self.write(_session(self.cwd))
        self.shrink(p)
        self.assertEqual([n for n in os.listdir(self.proj) if n.startswith(".")], [])

    def test_a_small_file_is_left_alone(self):
        p = self.write(_session(self.cwd))
        res = self.shrink(p, min_bytes=10 ** 9)
        self.assertEqual(res["action"], "skip")
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.backups(), [])


class Cwd(_Box):
    def test_the_cwd_must_match_the_projects_dir_name(self):
        other = os.path.join(self.root, "elsewhere")
        os.makedirs(other)
        p = self.write(_session(other))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p)
        self.assertIn("projects dir", str(cm.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_the_matching_cwd_wins_over_a_later_foreign_one(self):
        other = os.path.join(self.root, "elsewhere")
        os.makedirs(other)
        p = self.write(_session(self.cwd, last_cwd=other))
        self.shrink(p)
        call = json.load(open(self.log))
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.cwd))

    def test_a_vanished_cwd_is_refused(self):
        gone = os.path.join(self.root, "gone")
        self.proj = os.path.join(self.home, ".claude", "projects", sanitize(gone))
        os.makedirs(self.proj)
        p = self.write(_session(gone))
        with self.assertRaises(ss.Refused):
            self.shrink(p)
        self.assertFalse(os.path.exists(self.log))


class Guards(_Box):
    def test_a_foreign_owned_file_is_refused(self):
        p = self.write(_session(self.cwd))
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p, euid=os.geteuid() + 1)
        self.assertIn("owner", str(cm.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_a_symlink_is_refused(self):
        p = self.write(_session(self.cwd))
        link = os.path.join(self.proj, OTHER + ".jsonl")
        os.symlink(p, link)
        with self.assertRaises(ss.Refused):
            self.shrink(link)

    def test_a_file_held_open_by_another_process_is_refused(self):
        p = self.write(_session(self.cwd))
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sys,time\nf=open(sys.argv[1])\nprint('ok',flush=True)\n"
                                   "time.sleep(60)", p], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "ok")
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p)
        self.assertIn("holds the file open", str(cm.exception))
        self.assertFalse(os.path.exists(self.log))

    def test_liveness_is_checked_again_after_the_backup(self):
        p = self.write(_session(self.cwd))
        calls = []
        real = ss.live_reason

        def flip(*a, **k):
            calls.append(1)
            return "" if len(calls) == 1 else "pid 7 holds the file open"
        ss.live_reason = flip
        self.addCleanup(setattr, ss, "live_reason", real)
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p)
        self.assertIn("after the backup", str(cm.exception))
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(len(calls), 2)

    def test_a_running_session_record_is_refused(self):
        p = self.write(_session(self.cwd))
        with open(os.path.join(self.home, ".claude", "sessions", "1.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "sessionId": SID}, fh)
        with self.assertRaises(ss.Refused):
            self.shrink(p)

    def test_eperm_counts_as_alive(self):
        def kill(pid, sig):
            raise PermissionError(1, "Operation not permitted")
        self.assertTrue(ss._pid_alive(1, kill=kill))

        def gone(pid, sig):
            raise ProcessLookupError(3, "No such process")
        self.assertFalse(ss._pid_alive(1, kill=gone))

    def test_a_recently_written_file_is_refused(self):
        p = self.write(_session(self.cwd))
        os.utime(p, None)
        with self.assertRaises(ss.Refused):
            self.shrink(p)

    def test_claude_command_lines_native_and_npm(self):
        self.assertTrue(ss._is_claude(b"claude\0--resume\0" + SID.encode()))
        self.assertTrue(ss._is_claude(b"/home/x/.local/share/claude/versions/2.1.330\0-c"))
        self.assertTrue(ss._is_claude(
            b"node\0/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js\0-c"))
        self.assertTrue(ss._is_claude(b"/usr/bin/node\0/home/x/.npm-global/bin/claude\0-c"))
        self.assertFalse(ss._is_claude(b"bash\0-c\0grep x " + SID.encode()))
        self.assertFalse(ss._is_claude(b"node\0/srv/app/server.js"))


class Cli(_Box):
    def run_cli(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run([sys.executable, str(ROOT / "airuleset.py"), "session-shrink"]
                              + list(args), capture_output=True, text=True, env=env, timeout=120)

    def test_dry_run_is_the_default_and_runs_nothing(self):
        p = self.write(_session(self.cwd))
        before = open(p, "rb").read()
        r = self.run_cli(p, "--claude-bin", self.bin, "--min-mib", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would run", r.stdout)
        self.assertIn("/compact", r.stdout)
        self.assertEqual(open(p, "rb").read(), before)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.backups(), [])

    def test_another_account_is_refused(self):
        r = self.run_cli("--account", "nobody-else-1395")
        self.assertEqual(r.returncode, 2)
        self.assertIn("sudo -u nobody-else-1395", r.stderr)

    def test_over_lists_only_big_own_sessions(self):
        big = os.path.join(self.proj, "a.jsonl")
        with open(big, "wb") as fh:
            fh.truncate(3 * ss.MIB)
        with open(os.path.join(self.proj, "b.jsonl"), "w") as fh:
            fh.write("{}\n")
        self.assertEqual(ss.candidates(self.home, 2 * ss.MIB), [big])


if __name__ == "__main__":
    unittest.main()
