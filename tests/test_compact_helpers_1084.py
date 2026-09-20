"""#1084 L2 — the SURVIVING `watchdog/compact.py` helpers (re-homed from the
former `tests/test_compact.py`).

Machine-triggered `/compact` is REMOVED for good (owner ROZHODNUTE 2026-09-19);
L2 deleted the whole callback delivery + request store + `compact-request` CLI
(their locks now live in `tests/test_compact_removed_1084.py`). What `compact.py`
still exports — and what THIS file covers, VERBATIM from the old delivery
suite — are the helpers other jobs read:

  - `_find_pane_for_session` + its #645 ambiguous-pane resolution (via the
    claude PROCESS start time -> a transcript resume boundary), used by
    `watchdog/goal.py`; and the `watchdog` helpers it leans on
    (`_transcript_resume_boundary_at`, `_pane_claude_start_epoch`,
    `_proc_start_epoch`).
  - `resolve_self_pane` — the `$TMUX_PANE` self-resolver used by
    `airuleset.py goal-arm --self` and the `status` goal-row.
  - `TestManagedAutoCompactWindowReverted` — a 2026-07-25 correction unrelated to
    the callback machinery (native autocompact fires at ticket boundaries, not
    off a low `autoCompactWindow`); carried over UNCHANGED.
"""

import json
import os
import sys
import time
import unittest
import unittest.mock as m
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import compact


# --------------------------------------------------------------------------- #
# 1. MANAGED_AUTOCOMPACT_WINDOW — unrelated to #402, carried over verbatim.
# --------------------------------------------------------------------------- #

class TestManagedAutoCompactWindowReverted(unittest.TestCase):
    """MANAGED_AUTOCOMPACT_WINDOW is REVERTED (2026-07-25 correction batch): a
    low auto-compact threshold cuts big tasks off MID-WORK, defeating the
    point of the 1M context window. Context is bounded at TICKET BOUNDARIES
    instead — never by an artificial window."""

    def test_constant_no_longer_exists(self):
        self.assertFalse(hasattr(airuleset, "MANAGED_AUTOCOMPACT_WINDOW"))

    def test_key_absent_on_a_fresh_settings_dict(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertNotIn("autoCompactWindow", out)

    def test_key_actively_stripped_from_an_already_deployed_settings_file(self):
        out = airuleset.apply_managed_settings_defaults(
            {"autoCompactWindow": 300000})
        self.assertNotIn("autoCompactWindow", out)

    def test_preserves_other_keys(self):
        # `model` is itself a managed key (unconditionally overwritten to
        # MANAGED_MODEL) — assert against the constant, not a literal, so
        # this test stops breaking on every managed-model policy change
        # (it broke on the 2026-08-13 Opus 5 ban with a stale opus-5 id).
        out = airuleset.apply_managed_settings_defaults(
            {"hooks": {"Stop": []}, "model": airuleset.MANAGED_MODEL,
             "autoCompactWindow": 155000})
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)
        self.assertNotIn("autoCompactWindow", out)

def _encode(cwd):
    return wd.encode_project_dir(cwd)

CB_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"

class DeliverCompactFakeTmux:
    """Fake `run` for the compact module — resolves panes via list-panes
    (matching by transcript stem, mirroring real `list_claude_panes`), and
    serves capture-pane from either a static value or a scripted sequence
    (each real capture-pane call consumes the next entry, falling back to
    the static value once exhausted — the SAME `cap_seq` idiom this repo's
    other watchdog test files already use)."""

    def __init__(self, panes, captured, in_mode=False, cap_seq=()):
        self.panes = panes          # [(pane_id, cmd, cwd, pid)]
        self.captured = captured
        self.in_mode = in_mode
        self.cap_seq = list(cap_seq)
        self._cap_calls = 0
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s\t%s" % t for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            if not self.cap_seq:
                return self.captured
            idx = min(self._cap_calls, len(self.cap_seq) - 1)
            self._cap_calls += 1
            return self.cap_seq[idx]
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]

class TestFindPaneForSession(unittest.TestCase):
    SID = "sess-find-1"
    CWD = "/home/newlevel/devel/findme"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _write(self, base):
        d = Path(base) / _encode(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / (self.SID + ".jsonl")).write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")

    def test_single_matching_pane_resolves(self):
        proj = self._dir()
        self._write(proj)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertEqual(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj),
            "%9")

    def test_no_matching_pane_returns_none(self):
        proj = self._dir()
        d = Path(proj) / _encode(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / "some-other-sid.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertIsNone(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj))

    def test_ambiguous_two_panes_same_transcript_returns_none(self):
        proj = self._dir()
        self._write(proj)
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111"), ("%10", "claude", self.CWD, "222")],
            CB_IDLE_CAP)
        self.assertIsNone(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj))

class TestResumeBoundaryReader(unittest.TestCase):
    """`_transcript_resume_boundary_at` (pure jsonl binary-search reader)."""

    def _write(self, epochs):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "t.jsonl"
        lines = []
        for e in epochs:
            if e is None:
                lines.append(json.dumps({"type": "summary", "leafUuid": "x"}))
            else:
                iso = _dt.fromtimestamp(e, _tz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                lines.append(json.dumps({"type": "user", "timestamp": iso}))
        p.write_text("\n".join(lines) + "\n")
        return p

    S = 1_700_000_000.0

    def test_gap_then_burst_is_a_boundary(self):
        # entries up to ~1.7h before S, quiet, then a burst 50s after S
        p = self._write([self.S - 6000, self.S - 5500, self.S - 5000,
                         self.S + 50, self.S + 60, self.S + 120])
        self.assertTrue(wd._transcript_resume_boundary_at(p, self.S))

    def test_fresh_onset_no_entries_before_is_a_boundary(self):
        # the transcript is BORN at S (no entry before) -> boundary
        p = self._write([self.S + 3, self.S + 10, self.S + 30])
        self.assertTrue(wd._transcript_resume_boundary_at(p, self.S))

    def test_continuous_activity_is_not_a_boundary(self):
        # an entry within GAP_BEFORE (120s) before S -> NOT a resume (a co-active
        # sibling pane is excluded here, so the real owner is the unique match)
        p = self._write([self.S - 40, self.S - 20, self.S - 5,
                         self.S + 10, self.S + 30])
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

    def test_no_burst_after_is_not_a_boundary(self):
        # a gap before S but the next entry is far past BURST_AFTER (300s)
        p = self._write([self.S - 6000, self.S - 5000, self.S + 5000])
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

    def test_skips_entries_without_a_timestamp(self):
        p = self._write([None, self.S - 6000, None, self.S + 40, None])
        self.assertTrue(wd._transcript_resume_boundary_at(p, self.S))

    def test_missing_file_is_safe_false(self):
        self.assertFalse(wd._transcript_resume_boundary_at(
            Path("/no/such/transcript.jsonl"), 1.0))

    def test_binary_search_finds_boundary_deep_in_a_large_transcript(self):
        # ~40k entries; the resume boundary sits mid-file (the `-c` long-session
        # shape). A whole-file scan would be O(n); the reader must still find it.
        S = self.S
        epochs = [S - 200000 + i for i in range(0, 20000, 5)]   # dense, long ago
        epochs += [S + 40 + i for i in range(0, 20000, 5)]      # burst + on
        # the gap is between the two blocks (last-before = S-160005, first-after S+40)
        p = self._write(epochs)
        self.assertTrue(wd._transcript_resume_boundary_at(p, S))

    def test_single_entry_just_before_is_not_a_boundary(self):
        # #645 review 🔴: ONE entry 30s before the pivot must NOT read as a fresh
        # onset — the pre-window seek clamps to 0 and MUST NOT discard that first
        # line (else gap_ok is wrongly True -> a co-active sibling false-matches).
        p = self._write([self.S - 30, self.S + 40])
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

    def test_giant_before_entry_beyond_the_pre_window_refuses_boundary(self):
        # A real before-entry that sits >256KB back (one giant entry) is not
        # found by the bounded pre-window scan -> `before is None` but `lo > 0`
        # -> REFUSE (never mistake an unseen before-entry for a fresh onset).
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "t.jsonl"
        big = "x" * 300000
        lines = [
            json.dumps({"type": "user", "pad": big,
                        "timestamp": _dt.fromtimestamp(self.S - 30, _tz.utc)
                        .strftime("%Y-%m-%dT%H:%M:%S.000Z")}),
            json.dumps({"type": "user",
                        "timestamp": _dt.fromtimestamp(self.S + 40, _tz.utc)
                        .strftime("%Y-%m-%dT%H:%M:%S.000Z")}),
        ]
        p.write_text("\n".join(lines) + "\n")
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

    def test_gap_exactly_at_threshold_is_a_boundary(self):
        # gap == RESUME_GAP_BEFORE_S (120s) satisfies `>=` (locks >= vs >)
        p = self._write([self.S - wd.RESUME_GAP_BEFORE_S, self.S + 50])
        self.assertTrue(wd._transcript_resume_boundary_at(p, self.S))

    def test_gap_one_second_under_threshold_is_not_a_boundary(self):
        p = self._write([self.S - (wd.RESUME_GAP_BEFORE_S - 1), self.S + 50])
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

    def test_burst_exactly_at_threshold_is_a_boundary(self):
        # burst == RESUME_BURST_AFTER_S (300s) satisfies `<=` (locks <= vs <)
        p = self._write([self.S - 6000, self.S + wd.RESUME_BURST_AFTER_S])
        self.assertTrue(wd._transcript_resume_boundary_at(p, self.S))

    def test_burst_one_second_over_threshold_is_not_a_boundary(self):
        p = self._write([self.S - 6000, self.S + (wd.RESUME_BURST_AFTER_S + 1)])
        self.assertFalse(wd._transcript_resume_boundary_at(p, self.S))

class TestProcStartEpoch(unittest.TestCase):
    """`_proc_start_epoch` parses /proc/<pid>/stat field 22 + /proc/stat btime."""

    def test_real_spawned_process_start_is_near_now(self):
        import subprocess
        before = time.time()
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.kill)
        st = wd._proc_start_epoch(proc.pid)
        after = time.time()
        self.assertIsNotNone(st)
        self.assertGreaterEqual(st, before - 5)
        self.assertLessEqual(st, after + 5)

    def test_dead_pid_returns_none(self):
        self.assertIsNone(wd._proc_start_epoch(2_147_483_646))

class TestPaneClaudeStartEpoch(unittest.TestCase):
    @staticmethod
    def _run_pid(_argv, timeout=8):
        return "4242"           # `#{pane_pid}` -> 4242

    def test_resolves_via_pane_pid_then_claude_pid_then_start(self):
        run = self._run_pid
        with m.patch.object(wd, "_pane_claude_pid", return_value="99") as pcp, \
             m.patch.object(wd, "_proc_start_epoch", return_value=123.0) as pse:
            self.assertEqual(wd._pane_claude_start_epoch("%1", run=run), 123.0)
            pcp.assert_called_once_with("4242")
            pse.assert_called_once_with("99")

    def test_non_numeric_pane_pid_is_none(self):
        def run(_argv, timeout=8):
            return "not-a-pid"
        self.assertIsNone(wd._pane_claude_start_epoch("%1", run=run))

    def test_no_claude_pid_is_none(self):
        with m.patch.object(wd, "_pane_claude_pid", return_value=None):
            self.assertIsNone(wd._pane_claude_start_epoch("%1", run=self._run_pid))

class TestFindPaneAmbiguousResolution(unittest.TestCase):
    SID = "d306e5ce-live-sess"
    CWD = "/home/newlevel/devel/presenter/presenter-dev2"

    def _proj_sid_newest(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        base = Path(d.name)
        pdir = base / _encode(self.CWD)
        pdir.mkdir(parents=True)
        old = pdir / "old-dead-sess.jsonl"
        old.write_text("{}\n")
        os.utime(old, (1000, 1000))
        live = pdir / (self.SID + ".jsonl")
        live.write_text("{}\n")
        os.utime(live, (time.time(), time.time()))
        return base

    def _tmux(self, rows):
        return DeliverCompactFakeTmux(rows, CB_IDLE_CAP)

    def test_a_grouped_dup_pane_still_delivers(self):
        # (a) `list_claude_panes` returning ONE physical pane 3× (a grouped
        # session, if its own dedup ever regressed) must still deliver via the
        # DEFENSIVE dedup in `_find_pane_for_session`. Patch list_claude_panes to
        # emit raw duplicates so the compact-side dedup is genuinely under test
        # (without it: 3 cwd_matches -> ambiguous -> boundary branch -> None).
        proj = self._proj_sid_newest()
        tmux = self._tmux([("%16", "claude", self.CWD, "111")])
        with m.patch.object(wd, "list_claude_panes",
                            return_value=[("%16", self.CWD)] * 3):
            self.assertEqual(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj), "%16")

    def test_f_unresolvable_start_pane_skipped_not_crashing(self):
        # a candidate whose claude process start can't be read (None) is simply
        # excluded — the OTHER candidate's genuine boundary still resolves it,
        # and None never reaches the reader (which would TypeError on it).
        proj = self._proj_sid_newest()
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222")])
        starts = {"%15": None, "%16": 200.0}
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: starts.get(pid)), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=lambda path, st, *a, **k: st == 200.0):
            self.assertEqual(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj), "%16")

    def test_b_two_panes_one_cwd_unique_boundary_delivers(self):
        # (b) two distinct panes share the cwd; only %16's process has the
        # resume boundary in sid's transcript -> deliver to %16
        proj = self._proj_sid_newest()
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222")])
        starts = {"%15": 100.0, "%16": 200.0}
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: starts.get(pid)), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=lambda path, st, *a, **k: st == 200.0):
            self.assertEqual(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj), "%16")

    def test_c_no_boundary_safe_skips(self):
        proj = self._proj_sid_newest()
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222")])
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: 100.0), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=lambda *a, **k: False):
            self.assertIsNone(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj))

    def test_c_two_boundaries_ambiguous_safe_skips(self):
        proj = self._proj_sid_newest()
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222")])
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None:
                            {"%15": 100.0, "%16": 200.0}[pid]), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=lambda *a, **k: True):
            self.assertIsNone(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj))

    def test_d_older_session_sharing_cwd_is_resolved(self):
        # sid is NOT the cwd's newest (an OLDER session sharing the cwd); the
        # cheap cwd pass sees 0 candidates, the boundary branch still finds it.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        base = Path(d.name)
        pdir = base / _encode(self.CWD)
        pdir.mkdir(parents=True)
        older = pdir / (self.SID + ".jsonl")
        older.write_text("{}\n")
        os.utime(older, (1000, 1000))
        newest = pdir / "zbynek-newer.jsonl"
        newest.write_text("{}\n")
        os.utime(newest, (time.time(), time.time()))
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222")])
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None:
                            {"%15": 100.0, "%16": 200.0}[pid]), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=lambda path, st, *a, **k: st == 100.0):
            self.assertEqual(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=base), "%15")

    def test_e_different_cwd_pane_never_boundary_checked(self):
        # Ambiguous cwd (two same-cwd panes) forces the boundary branch; a THIRD
        # pane in a DIFFERENT cwd must NEVER be boundary-checked (its process
        # cannot own sid's transcript) even though it would "match".
        proj = self._proj_sid_newest()
        other = "/home/newlevel/devel/montalu/report_tabulka"
        tmux = self._tmux([("%15", "claude", self.CWD, "111"),
                           ("%16", "claude", self.CWD, "222"),
                           ("%28", "claude", other, "333")])
        seen = []

        def _boundary(path, st, *a, **k):
            seen.append(st)
            return st == 200.0   # only %16's start is a genuine boundary
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None:
                            {"%15": 100.0, "%16": 200.0, "%28": 999.0}[pid]), \
             m.patch.object(wd, "_transcript_resume_boundary_at",
                            side_effect=_boundary):
            self.assertEqual(
                compact._find_pane_for_session(self.SID, self.CWD,
                                               run=tmux, projects_dir=proj), "%16")
        self.assertNotIn(999.0, seen)   # the other-cwd pane was never checked
        self.assertEqual(sorted(seen), [100.0, 200.0])

class TestResolveSelfPane(unittest.TestCase):
    def test_no_tmux_pane_returns_all_blank(self):
        tmux = DeliverCompactFakeTmux([], "")
        self.assertEqual(compact.resolve_self_pane(run=tmux, pane_env=""),
                         ("", "", ""))

    def test_resolves_pane_cwd_and_sid(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cwd = "/home/newlevel/devel/selfpane"
        pd = Path(d.name) / _encode(cwd)
        pd.mkdir(parents=True, exist_ok=True)
        (pd / "sess-9.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")
        tmux = DeliverCompactFakeTmux([("%3", "claude", cwd, "111")], CB_IDLE_CAP)
        pid, got_cwd, sid = compact.resolve_self_pane(run=tmux, pane_env="%3",
                                                       projects_dir=Path(d.name))
        self.assertEqual((pid, got_cwd, sid), ("%3", cwd, "sess-9"))

    def test_unresolvable_pane_id_returns_blank_cwd_and_sid(self):
        tmux = DeliverCompactFakeTmux([("%9", "claude", "/somewhere", "111")], CB_IDLE_CAP)
        pid, cwd, sid = compact.resolve_self_pane(run=tmux, pane_env="%404")
        self.assertEqual(pid, "%404")
        self.assertEqual(cwd, "")
        self.assertEqual(sid, "")


# --------------------------------------------------------------------------- #
# 5. The recent-human helpers cards.py (job 25) reads — #1084 L2 restores their
#    focused coverage (they were only exercised through the deleted
#    deliver_compact before). `_compact_recent_human_activity` is imported by
#    `watchdog.cards.report_boundary_after`'s recent-human gate.
# --------------------------------------------------------------------------- #
class TestRecentHumanWindowClamp(unittest.TestCase):
    def test_explicit_window_is_returned_verbatim(self):
        self.assertEqual(compact._compact_recent_human_window(999), 999)

    def test_default_is_the_constant(self):
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_COMPACT_RECENT_HUMAN_S", None)
            self.assertEqual(compact._compact_recent_human_window(),
                             compact.COMPACT_RECENT_HUMAN_ACTIVITY_S)

    def test_zero_or_negative_env_clamps_up_to_one(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_RECENT_HUMAN_S": "0"}):
            self.assertEqual(compact._compact_recent_human_window(), 1)

    def test_over_cap_env_clamps_below_the_request_max_age(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_RECENT_HUMAN_S": "999999"}):
            self.assertEqual(compact._compact_recent_human_window(),
                             compact.COMPACT_REQUEST_MAX_AGE_S - 1)


class TestRecentHumanActivity(unittest.TestCase):
    def test_delegates_with_discord_prefixes_and_returns_the_bool(self):
        seen = {}

        def _spy(sid, tpath, now, window_s=None, extra_human_prefixes=None):
            seen["sid"] = sid
            seen["window_s"] = window_s
            seen["prefixes"] = extra_human_prefixes
            return True, "ok"

        with m.patch.object(wd, "_transcript_for_session",
                            return_value="/fake/t.jsonl"), \
                m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                               side_effect=_spy):
            out = compact._compact_recent_human_activity(
                "/cwd", "sid-x", 1000.0, projects_dir="/pd", window_s=42)
        self.assertTrue(out)
        self.assertEqual(seen["sid"], "sid-x")
        self.assertEqual(seen["window_s"], 42)
        # the Discord-relay prefixes must be forwarded so a relayed answer counts
        self.assertEqual(seen["prefixes"], compact._COMPACT_DISCORD_ANSWER_PREFIXES)

    def test_false_when_the_primitive_says_not_recent(self):
        with m.patch.object(wd, "_transcript_for_session",
                            return_value="/fake/t.jsonl"), \
                m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                               return_value=(False, "stale")):
            self.assertFalse(compact._compact_recent_human_activity(
                "/cwd", "sid-x", 1000.0))


if __name__ == "__main__":
    unittest.main()
