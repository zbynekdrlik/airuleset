"""#1138 — the owner-controlled stream-priority registry.

`stream-priority.json` (repo root) maps a stream FAMILY (`montalu` for
montalu1…N) to `high` / `normal` (default `normal`). `cli_stream_priority`
reads/writes it (`airuleset.py stream-priority --list` / `set <stream>
high|normal`), and `cli_quals_cmd._row_sort_key` puts a high-priority stream's
rows first WITHIN the same label rank (architecture-rework still leads), then
oldest-first. A missing / unparseable / wrongly-shaped file means everything is
`normal` — never a crash.

Hermetic: every test points `cli_stream_priority.STREAM_PRIORITY_PATH` at a
temp file (the seam) and pins `AUTHORITY_BY_USER` / `STREAM_RENAME_ALIASES`;
the only real-file reads are the two shipped-contract tests.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase, main, mock

import airuleset
import cli_quals_cmd
import cli_stream_priority
import cli_work_class

REPO = Path(__file__).resolve().parent.parent

_AUTH = {
    "gatekeeper": "full",
    "montalu1": "branch-merge",
    "montalu3": "branch-merge",
    "david1": "fork-no-merge",
    "miva1": "branch-merge",
    "marek": "fork-no-merge",
}
_ALIASES = {"montalu": "montalu1", "david": "david1"}


def _row(created, labels):
    return {"createdAt": created, "title": "t",
            "labels": [{"name": n} for n in labels]}


class _Hermetic(TestCase):
    """Temp registry path + pinned stream tables for every test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "stream-priority.json"
        for target, attr, value in (
                (cli_stream_priority, "STREAM_PRIORITY_PATH", self.path),
                (airuleset, "AUTHORITY_BY_USER", _AUTH),
                (airuleset, "STREAM_RENAME_ALIASES", _ALIASES)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        cli_stream_priority._cache.update(key=None, value={})

    def write(self, content):
        self.path.write_text(content if isinstance(content, str)
                             else json.dumps(content))

    def run_cmd(self, list_=False, sp_args=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_stream_priority.cmd_stream_priority(
                Namespace(list=list_, sp_args=list(sp_args)))
        return rc, out.getvalue(), err.getvalue()

    def emit(self, rows):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(rows)
        return [ln.split("\t", 1)[0] for ln in buf.getvalue().splitlines()
                if ln and not ln.startswith("#")]


class TestShippedContract(TestCase):
    """The SHIPPED file is owner data (`set` rewrites it), so these assert it
    is VALID and that `--list` prints exactly it — never pin its content, or
    the owner's own `set` would fail the next push gate (review finding)."""

    def test_shipped_file_is_valid(self):
        data, err, dropped, present = cli_stream_priority._read(
            REPO / "stream-priority.json")
        self.assertTrue(present)
        self.assertIsNone(err)
        self.assertEqual(dropped, [])
        self.assertLessEqual(set(data), cli_stream_priority.known_families())
        self.assertLessEqual(set(data.values()),
                             set(cli_stream_priority.PRIORITIES))

    def test_cli_list_prints_shipped_json(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "stream-priority",
             "--list"], capture_output=True, text=True, cwd=str(REPO),
            timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(json.loads(r.stdout), cli_stream_priority._read(
            REPO / "stream-priority.json")[0])

    def test_subcommand_registered(self):
        self.assertIs(airuleset.SUBCOMMANDS["stream-priority"],
                      cli_stream_priority.cmd_stream_priority)


class TestList(_Hermetic):
    def test_list_prints_file_contents_as_json(self):
        self.write({"montalu": "high"})
        rc, out, _ = self.run_cmd(list_=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"montalu": "high"})

    def test_list_missing_file_is_empty_json(self):
        rc, out, _ = self.run_cmd(list_=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {})

    def test_list_bad_file_is_empty_json_and_warns(self):
        self.write("{not json")
        rc, out, err = self.run_cmd(list_=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {})
        self.assertIn("stream-priority", err)

    def test_list_drops_invalid_values_with_a_warning(self):
        self.write({"montalu": "high", "david": "urgent", "miva": 3})
        rc, out, err = self.run_cmd(list_=True)
        self.assertEqual(json.loads(out), {"montalu": "high"})
        self.assertIn('david="urgent"', err)

    def test_numbered_key_folds_into_its_family(self):
        self.write({"montalu3": "high"})
        rc, out, _ = self.run_cmd(list_=True)
        self.assertEqual(json.loads(out), {"montalu": "high"})
        self.assertEqual(cli_stream_priority.row_priority_rank(
            _row("x", ["stream:montalu1"])), 0)

    def test_list_warns_on_a_family_no_stream_carries(self):
        self.write({"montlau": "high"})
        rc, out, err = self.run_cmd(list_=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"montlau": "high"})
        self.assertIn("montlau", err)

    def test_bare_command_lists(self):
        self.write({"montalu": "high"})
        rc, out, _ = self.run_cmd()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"montalu": "high"})


class TestFamilies(_Hermetic):
    def test_family_strips_trailing_digits(self):
        f = cli_stream_priority.stream_family
        self.assertEqual(f("montalu3"), "montalu")
        self.assertEqual(f("miva1"), "miva")
        self.assertEqual(f("marek"), "marek")
        self.assertEqual(f(""), "")

    def test_known_families_are_the_reduced_authority_streams(self):
        self.assertEqual(cli_stream_priority.known_families(),
                         {"montalu", "david", "miva", "marek"})


class TestSet(_Hermetic):
    def test_set_refuses_unknown_stream(self):
        self.write({"montalu": "high"})
        rc, _, err = self.run_cmd(sp_args=["set", "nosuch", "high"])
        self.assertNotEqual(rc, 0)
        self.assertIn("nosuch", err)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"montalu": "high"})

    def test_set_refuses_full_authority_account(self):
        rc, _, _ = self.run_cmd(sp_args=["set", "gatekeeper", "high"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.path.exists())

    def test_set_refuses_invalid_priority(self):
        rc, _, _ = self.run_cmd(sp_args=["set", "david", "urgent"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.path.exists())

    def test_set_writes_known_family(self):
        self.write({"montalu": "high"})
        rc, _, _ = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"montalu": "high", "david": "high"})

    def test_set_numbered_stream_writes_its_family(self):
        rc, _, _ = self.run_cmd(sp_args=["set", "miva1", "high"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.path.read_text()), {"miva": "high"})

    def test_set_normal_removes_the_entry(self):
        self.write({"montalu": "high", "david": "high"})
        rc, _, _ = self.run_cmd(sp_args=["set", "montalu", "normal"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.path.read_text()), {"david": "high"})

    def test_set_refuses_to_overwrite_a_corrupt_file(self):
        self.write("{not json")
        rc, _, _ = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.path.read_text(), "{not json")

    def test_set_refuses_to_drop_unreadable_entries(self):
        self.write({"montalu": "high", "david": "HIGH"})
        before = self.path.read_text()
        with redirect_stderr(io.StringIO()):
            rc, _, err = self.run_cmd(sp_args=["set", "miva1", "high"])
        self.assertNotEqual(rc, 0)
        self.assertIn('david="HIGH"', err)
        self.assertEqual(self.path.read_text(), before)

    def test_set_normal_on_missing_file_creates_nothing(self):
        rc, out, _ = self.run_cmd(sp_args=["set", "david", "normal"])
        self.assertEqual(rc, 0)
        self.assertIn("already normal", out)
        self.assertFalse(self.path.exists())

    def test_set_already_high_is_a_noop(self):
        self.write({"montalu": "high"})
        rc, out, _ = self.run_cmd(sp_args=["set", "montalu3", "high"])
        self.assertEqual(rc, 0)
        self.assertIn("already high", out)

    def test_write_failure_is_rc1_not_a_traceback(self):
        # Injected at the replace step, not via chmod: CI runs as root,
        # which ignores directory permissions. The mkstemp temp file must
        # be cleaned up, so the directory stays empty.
        with mock.patch("cli_playwright_mcp.os.replace",
                        side_effect=PermissionError("EACCES")):
            rc, _, err = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertEqual(rc, 1)
        self.assertIn("could not write", err)
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_high_wins_when_a_family_appears_twice(self):
        self.write({"montalu": "high", "montalu3": "normal"})
        self.assertEqual(cli_stream_priority.load_priorities(),
                         {"montalu": "high"})
        self.write({"montalu3": "normal", "montalu": "high"})
        self.assertEqual(cli_stream_priority._read(self.path)[0],
                         {"montalu": "high"})

    def test_key_with_no_family_is_dropped(self):
        self.write({"123": "high", "": "high"})
        rc, out, err = self.run_cmd(list_=True)
        self.assertEqual(json.loads(out), {})
        self.assertIn('123="high"', err)

    def test_same_size_rewrite_is_not_served_from_cache(self):
        self.write({"david": "high"})
        self.assertEqual(cli_stream_priority.load_priorities(),
                         {"david": "high"})
        st = self.path.stat()
        other = self.path.with_name("other.json")
        other.write_text(json.dumps({"marek": "high"}))   # same byte size
        os.replace(other, self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size, st.st_size)
        self.assertEqual(cli_stream_priority.load_priorities(),
                         {"marek": "high"})

    def test_set_bad_arity_is_usage_error(self):
        rc, _, _ = self.run_cmd(sp_args=["set", "david"])
        self.assertNotEqual(rc, 0)

    def test_list_and_set_together_is_usage_error(self):
        rc, _, _ = self.run_cmd(list_=True, sp_args=["set", "david", "high"])
        self.assertNotEqual(rc, 0)


class TestSetControllerOnly(_Hermetic):
    """#946 pattern: a GIT-TRACKED registry is written only on the controller,
    and the controller auto-commits so its tree is never left dirty."""

    def setUp(self):
        super().setUp()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git = lambda *a: subprocess.run(
            ["git", "-C", self._tmp.name, *a], capture_output=True,
            text=True, env=env, check=True).stdout
        self.git("init", "-q")
        self.write({"montalu": "high"})
        self.git("add", "stream-priority.json")
        self.git("commit", "-q", "-m", "init")
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def _box(self, cls):
        p = mock.patch.object(cli_stream_priority, "_box_class", lambda: cls)
        p.start()
        self.addCleanup(p.stop)

    def test_non_controller_refuses_tracked_write(self):
        self._box("workstation")
        rc, _, err = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertNotEqual(rc, 0)
        self.assertIn("controller", err)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"montalu": "high"})

    def test_unreadable_box_class_refuses(self):
        def boom():
            raise OSError("marker unreadable")
        p = mock.patch.object(cli_stream_priority, "_box_class", boom)
        p.start()
        self.addCleanup(p.stop)
        rc, _, _ = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"montalu": "high"})

    def test_controller_writes_and_commits(self):
        self._box("controller")
        rc, out, _ = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertEqual(rc, 0)
        self.assertIn("airuleset.py push", out)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"montalu": "high", "david": "high"})
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.assertIn("stream-priority", self.git("log", "-1", "--format=%s"))

    def test_controller_noop_makes_no_commit(self):
        self._box("controller")
        before = self.git("rev-list", "--count", "HEAD")
        rc, _, _ = self.run_cmd(sp_args=["set", "montalu", "high"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), before)

    def test_controller_commit_failure_is_rc1(self):
        self._box("controller")
        lock = Path(self._tmp.name) / ".git" / "index.lock"
        lock.write_text("")
        self.addCleanup(lambda: lock.exists() and lock.unlink())
        rc, _, err = self.run_cmd(sp_args=["set", "david", "high"])
        self.assertEqual(rc, 1)
        self.assertIn("NOT committed", err)


class TestRowOrdering(_Hermetic):
    def test_newer_high_stream_row_precedes_older_normal_stream_row(self):
        self.write({"montalu": "high"})
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1",
                                                "ready-for-review"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu3",
                                                "ready-for-review"]),
        }
        self.assertEqual(self.emit(rows), ["20", "10"])

    def test_high_rows_keep_age_order_among_themselves(self):
        self.write({"montalu": "high"})
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu1"]),
            "21": _row("2026-03-01T00:00:00Z", ["stream:montalu3"]),
            "30": _row("2026-02-01T00:00:00Z", []),
        }
        self.assertEqual(self.emit(rows), ["21", "20", "10", "30"])

    def test_legacy_family_label_counts_as_high(self):
        self.write({"montalu": "high"})
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu"]),
        }
        self.assertEqual(self.emit(rows), ["20", "10"])

    def test_any_high_stream_label_promotes_the_row(self):
        self.write({"montalu": "high"})
        row = _row("x", ["stream:david1", "stream:montalu1"])
        self.assertEqual(cli_stream_priority.row_priority_rank(row), 0)

    def test_architecture_rework_still_leads_high_priority(self):
        self.write({"montalu": "high"})
        rows = {
            "10": _row("2026-09-01T00:00:00Z", ["architecture-rework"]),
            "20": _row("2026-01-01T00:00:00Z", ["stream:montalu1"]),
        }
        self.assertEqual(self.emit(rows), ["10", "20"])

    def test_missing_file_is_plain_age_order(self):
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu1"]),
        }
        self.assertEqual(self.emit(rows), ["10", "20"])

    def test_bad_file_is_plain_age_order_never_a_crash(self):
        for bad in ("{not json", "[1, 2]", '"high"', '{"montalu": "urgent"}'):
            with self.subTest(bad=bad):
                self.write(bad)
                rows = {
                    "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
                    "20": _row("2026-09-01T00:00:00Z", ["stream:montalu1"]),
                }
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(self.emit(rows), ["10", "20"])

    def test_row_without_readable_labels_is_normal(self):
        self.write({"montalu": "high"})
        rank = cli_stream_priority.row_priority_rank
        self.assertEqual(rank({"createdAt": "x"}), 1)
        self.assertEqual(rank({"labels": None}), 1)
        self.assertEqual(rank({"labels": ["stream:montalu1"]}), 1)
        self.assertEqual(rank("not a row"), 1)
        self.assertEqual(rank(_row("x", ["stream:montalu1"])), 0)

    def test_sort_key_edit_is_picked_up_without_restart(self):
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu1"]),
        }
        self.write({"montalu": "high"})
        self.assertEqual(self.emit(rows), ["20", "10"])
        self.write({"david": "high", "montalu": "normal"})
        os.utime(self.path, ns=(1, 1))   # a new mtime even on a coarse clock
        self.assertEqual(self.emit(rows), ["10", "20"])

    def test_work_class_dep_wait_order_mirrors_the_sort_key(self):
        self.write({"montalu": "high"})
        rows = {
            "10": _row("2026-01-01T00:00:00Z", ["stream:david1"]),
            "20": _row("2026-09-01T00:00:00Z", ["stream:montalu1"]),
            "30": _row("2026-09-02T00:00:00Z", ["architecture-rework"]),
        }
        self.assertEqual(cli_work_class._sorted_row_keys(rows),
                         sorted(rows, key=lambda k: cli_quals_cmd._row_sort_key(
                             rows, k)))
        self.assertEqual(cli_work_class._sorted_row_keys(rows),
                         ["30", "20", "10"])


if __name__ == "__main__":
    main()
