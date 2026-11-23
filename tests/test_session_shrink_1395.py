"""#1395: `session-shrink` archives a transcript's pre-compact records.

The fixtures follow the record shapes measured on a real Claude Code 2.1.330
transcript: a `compact_boundary` system record (`parentUuid: null`,
`logicalParentUuid`, `compactMetadata.preservedSegment` / `preservedMessages`)
followed by the `isCompactSummary` user record, the preserved records sitting
BEFORE the boundary, and session-metadata records (`{"type":"ai-title",…}`)
between chain records.
"""
import gzip
import json
import os
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
OLD = time.time() - 3600


def _u(n):
    return "00000000-0000-4000-8000-%012d" % n


def _msg(n, parent, role="user", text="hello"):
    rec = {"parentUuid": parent, "isSidechain": False, "type": role,
           "message": {"role": role, "content": text}, "uuid": _u(n),
           "timestamp": "2026-11-23T10:00:%02dZ" % (n % 60), "sessionId": SID}
    return json.dumps(rec, separators=(",", ":"))


def _meta(typ, **kw):
    return json.dumps(dict({"type": typ, "sessionId": SID}, **kw), separators=(",", ":"))


def _boundary(n, logical, preserved=None):
    meta = {"trigger": "manual", "preTokens": 1000, "postTokens": 100}
    if preserved:
        head, tail, anchor, uuids = preserved
        meta["preservedSegment"] = {"headUuid": head, "anchorUuid": anchor, "tailUuid": tail}
        meta["preservedMessages"] = {"anchorUuid": anchor, "uuids": uuids, "allUuids": uuids}
    rec = {"parentUuid": None, "logicalParentUuid": logical, "isSidechain": False,
           "type": "system", "subtype": "compact_boundary",
           "content": "Conversation compacted", "level": "info",
           "compactMetadata": meta, "uuid": _u(n), "sessionId": SID}
    return json.dumps(rec, separators=(",", ":"))


def _summary(n, parent):
    rec = {"parentUuid": parent, "isSidechain": False, "type": "user",
           "message": {"role": "user", "content": "This session is being continued "
                       "from a previous conversation. Summary: PELICAN-42"},
           "isCompactSummary": True, "uuid": _u(n), "sessionId": SID}
    return json.dumps(rec, separators=(",", ":"))


def two_compacts():
    """lines, index of the first kept line: 2 compacts, the second preserving
    records 20-21 that sit before it."""
    L = [_meta("ai-title", aiTitle="first title"),
         _meta("file-history-snapshot", messageId=_u(1)),
         _msg(1, None), _msg(2, _u(1), "assistant", "mentions \\\"compact_boundary\\\" text"),
         _boundary(3, _u(2)), _summary(4, _u(3)),
         _msg(5, _u(4)), _msg(6, _u(5), "assistant"),
         _meta("mode", mode="normal"),
         _msg(20, _u(6)), _msg(21, _u(20), "assistant", "kept tail"),
         _boundary(30, _u(21), (_u(20), _u(21), _u(31), [_u(20), _u(21)])),
         _summary(31, _u(30)),
         _msg(32, _u(31)), _msg(33, _u(32), "assistant", "answer"),
         _meta("mode", mode="auto"),
         _meta("last-prompt", leafUuid=_u(33))]
    return L, 9


class _Dir(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        self.home = os.path.join(d.name, "home")
        os.makedirs(os.path.join(self.home, ".claude", "sessions"))

    def write(self, lines, name=SID + ".jsonl", mode=0o600):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(p, mode)
        os.utime(p, (OLD, OLD))
        return p

    def shrink(self, p, **kw):
        return ss.shrink(p, home=self.home, **kw)


class Shrink(_Dir):
    def test_two_compacts_keep_the_last_boundary_tail_byte_identical(self):
        lines, first = two_compacts()
        p = self.write(lines)
        orig = open(p, "rb").read()
        res = self.shrink(p)
        new = open(p, "rb").read()
        tail = ("\n".join(lines[first:]) + "\n").encode()
        self.assertTrue(new.endswith(tail))
        carried = new[:-len(tail)].decode().splitlines()
        self.assertEqual([json.loads(c)["type"] for c in carried], ["ai-title"])
        with gzip.open(res["archive"], "rb") as gz:
            self.assertEqual(gz.read(), orig[:len(orig) - len(tail)])
        self.assertTrue(res["archive"].startswith(p + ".pre-"))
        self.assertTrue(res["archive"].endswith(".jsonl.gz"))

    def test_the_parent_chain_of_the_kept_records_is_intact(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        self.shrink(p)
        recs = [json.loads(x) for x in open(p)]
        chain = [r for r in recs if "parentUuid" in r]
        uuids = {r["uuid"] for r in chain}
        boundary = [r for r in chain if r.get("subtype") == "compact_boundary"]
        self.assertEqual(len(boundary), 1)
        preserved = set(boundary[0]["compactMetadata"]["preservedMessages"]["uuids"])
        self.assertLessEqual(preserved, uuids)
        for r in chain:
            if r["parentUuid"] is not None and r["uuid"] not in preserved:
                self.assertIn(r["parentUuid"], uuids, r["uuid"])
        summary = [r for r in recs if r.get("isCompactSummary")]
        self.assertEqual(summary[0]["parentUuid"], boundary[0]["uuid"])

    def test_mode_and_times_are_kept(self):
        lines, _ = two_compacts()
        p = self.write(lines, mode=0o640)
        self.shrink(p)
        st = os.stat(p)
        self.assertEqual(st.st_mode & 0o777, 0o640)
        self.assertAlmostEqual(st.st_mtime, OLD, places=3)

    def test_without_preserved_records_the_cut_is_the_boundary(self):
        lines = [_msg(1, None), _msg(2, _u(1), "assistant"), _boundary(3, _u(2)),
                 _summary(4, _u(3)), _msg(5, _u(4))]
        p = self.write(lines)
        self.shrink(p)
        self.assertEqual(open(p).read(), "\n".join(lines[2:]) + "\n")

    def test_a_session_without_a_compact_is_untouched(self):
        lines = [_meta("ai-title", aiTitle="t"), _msg(1, None), _msg(2, _u(1), "assistant")]
        p = self.write(lines)
        before = open(p, "rb").read()
        res = self.shrink(p)
        self.assertEqual(res["cut"], 0)
        self.assertEqual(open(p, "rb").read(), before)
        self.assertEqual(os.stat(p).st_mtime, OLD)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".gz")], [])

    def test_a_text_mention_of_compact_boundary_is_not_a_boundary(self):
        lines = [_msg(1, None), _msg(2, _u(1), "assistant", 'a "compact_boundary" b')]
        p = self.write(lines)
        self.assertEqual(ss.plan(p)["cut"], 0)

    def test_a_missing_preserved_record_is_refused(self):
        lines = [_msg(1, None), _boundary(3, _u(1), (_u(90), _u(91), _u(4), [_u(90), _u(91)])),
                 _summary(4, _u(3))]
        p = self.write(lines)
        before = open(p, "rb").read()
        with self.assertRaises(ss.Refused):
            self.shrink(p)
        self.assertEqual(open(p, "rb").read(), before)

    def test_a_kept_record_whose_parent_would_be_archived_is_refused(self):
        lines = [_msg(1, None), _msg(2, _u(1), "assistant"), _boundary(3, _u(2)),
                 _summary(4, _u(3)), _msg(5, _u(2))]          # points over the cut
        p = self.write(lines)
        with self.assertRaises(ss.Refused):
            self.shrink(p)

    def test_a_stale_last_prompt_is_not_carried(self):
        lines = [_msg(1, None), _meta("last-prompt", leafUuid=_u(1)),
                 _meta("queue-operation", op="x"), _boundary(3, _u(1)), _summary(4, _u(3))]
        p = self.write(lines)
        self.shrink(p)
        self.assertEqual(open(p).read(), "\n".join(lines[3:]) + "\n")


class Live(_Dir):
    def test_a_file_held_open_by_another_process_is_refused(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sys,time\nf=open(sys.argv[1])\nprint('ok',flush=True)\n"
                                   "time.sleep(60)", p], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "ok")
        before = open(p, "rb").read()
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p)
        self.assertIn("holds the file open", str(cm.exception))
        self.assertEqual(open(p, "rb").read(), before)

    def test_a_running_session_record_is_refused(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        with open(os.path.join(self.home, ".claude", "sessions", "1.json"), "w") as fh:
            json.dump({"pid": os.getpid(), "sessionId": SID}, fh)
        with self.assertRaises(ss.Refused) as cm:
            self.shrink(p)
        self.assertIn("running claude", str(cm.exception))

    def test_a_dead_session_record_is_not_live(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        with open(os.path.join(self.home, ".claude", "sessions", "1.json"), "w") as fh:
            json.dump({"pid": 2 ** 22 + 7, "sessionId": SID}, fh)
        self.assertEqual(ss.live_reason(p, home=self.home), "")

    def test_a_recently_written_file_is_refused(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        os.utime(p, None)
        with self.assertRaises(ss.Refused):
            self.shrink(p)

    def test_only_a_claude_command_line_counts(self):
        self.assertTrue(ss._is_claude(b"claude\0--resume\0" + SID.encode()))
        self.assertTrue(ss._is_claude(b"/home/x/.local/share/claude/versions/2.1.330\0-c"))
        self.assertFalse(ss._is_claude(b"bash\0-c\0grep x " + SID.encode()))


class Cli(_Dir):
    def run_cli(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run([sys.executable, str(ROOT / "airuleset.py"), "session-shrink"]
                              + list(args), capture_output=True, text=True, env=env, timeout=120)

    def test_dry_run_is_the_default_and_writes_nothing(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        before = open(p, "rb").read()
        r = self.run_cli(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would shrink", r.stdout)
        self.assertIn("cut before line 10", r.stdout)
        self.assertEqual(open(p, "rb").read(), before)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".gz")], [])

    def test_apply_writes(self):
        lines, _ = two_compacts()
        p = self.write(lines)
        r = self.run_cli(p, "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shrunk", r.stdout)
        self.assertEqual(len([n for n in os.listdir(self.dir) if n.endswith(".gz")]), 1)

    def test_another_account_is_refused(self):
        r = self.run_cli("--account", "nobody-else-1395")
        self.assertEqual(r.returncode, 2)
        self.assertIn("sudo -u nobody-else-1395", r.stderr)

    def test_over_lists_only_big_own_sessions(self):
        proj = os.path.join(self.home, ".claude", "projects", "-x")
        os.makedirs(proj)
        big = os.path.join(proj, "a.jsonl")
        with open(big, "wb") as fh:
            fh.truncate(3 * ss.MIB)
        with open(os.path.join(proj, "b.jsonl"), "w") as fh:
            fh.write("{}\n")
        self.assertEqual(ss.candidates(self.home, 2 * ss.MIB), [big])


if __name__ == "__main__":
    unittest.main()
