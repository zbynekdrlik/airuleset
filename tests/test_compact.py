"""The collapsed `/compact` callback-delivery model (#402).

Replaces `tests/test_compact_request.py` (7350 lines, testing ~46 functions
implementing a text-sniffing/heuristic-boundary era of this machinery) and
`tests/test_compact_stall.py` (job 26, the claim-file stall watch — deleted
wholesale along with the claim system it watched). See `watchdog/compact.py`'s
own module docstring for the full design; this file locks its CONTRACT:

  - Exactly two origins ever create a request (`self-callback`,
    `subagent-stop`) — nothing text-sniffed, nothing context-size-derived.
  - `deliver_compact()`'s five named conditions (pane idle/no-draft, no live
    background tasks, not on a ⏳/❓ marker or unresumed API error, a 30-min
    per-session cooldown, a hard non-refreshable age cap) are ALL
    unconditional — no time-boxed override on any of them.
  - Every decision (SEND or SKIP) is logged from the ONE call site.
  - The kill-switch (`~/.claude/watchdog-disable-compact`) is honoured.
  - `compact_sweep` re-evaluates every still-pending request through the
    SAME function, discarding only on expiry/already-handled, never
    "no infinite waiting" via refusing to re-evaluate.

`TestManagedAutoCompactWindowReverted` is carried over UNCHANGED from the old
file — a 2026-07-25 correction unrelated to #402 (compaction fires at ticket
boundaries, not off a low `autoCompactWindow`).
"""

import json
import os
import sys
import time
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import compact

# Captured BEFORE any test patches `time.sleep` -- `TestCompactRequestCli`'s
# own class-wide no-op patch (see its `setUp`) would otherwise defeat the
# ONE test that specifically needs a REAL, if short, sleep to genuinely
# prove the MAJOR-1 fix (`test_self_on_a_genuinely_idle_pane_actually_
# sends_end_to_end`).
_REAL_SLEEP = time.sleep


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


# --------------------------------------------------------------------------- #
# Shared fixtures — transcript writers, a fake tmux, isolation helper.
# --------------------------------------------------------------------------- #

def _isolate_compact_state(testcase):
    """Give this test its OWN isolated compact-requests/-delivered/-sync
    files instead of the real `~/.claude/` copies — the live systemd
    watchdog executes this repo's WORKING TREE every 60s, so a test process
    touching the real files would race a live production job."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests-test.json"
    delp = Path(d.name) / "compact-delivered-test.json"
    syncp = Path(d.name) / "compact-sync-test.log"
    for name, path in (("compact_requests_path", reqp),
                       ("compact_delivered_path", delp),
                       ("compact_sync_log_path", syncp)):
        patcher = m.patch.object(compact, name, return_value=path)
        patcher.start()
        testcase.addCleanup(patcher.stop)
    return reqp, delp, syncp


def _encode(cwd):
    return wd.encode_project_dir(cwd)


def _write_marker_transcript(base, cwd, sid, marker_text=None, error=False):
    """A minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl whose
    last real assistant message either carries `marker_text` (a plain
    string, e.g. a real `❓ NEEDS YOU: ...` line) or, when `error=True`, is
    flagged `isApiErrorMessage` (for `_compact_session_unresumed`)."""
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    if error:
        entry = {"type": "assistant", "isApiErrorMessage": True,
                 "message": {"id": "msg_1", "content": "API Error: 529"}}
    else:
        entry = {"type": "assistant", "message": {
            "id": "msg_1", "content": marker_text or ""}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_human_transcript(base, cwd, sid, ts_epoch,
                            text="text ktorý napísal používateľ"):
    """A transcript whose single top-level `user` entry is genuinely
    HUMAN-typed, at `ts_epoch` — feeds `_compact_recent_human_activity`."""
    from datetime import datetime, timezone
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    iso = datetime.fromtimestamp(ts_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    entry = {"type": "user", "timestamp": iso, "message": {"content": text}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_subagent_transcript(base, cwd, sid, mtime=None, error=False,
                               finished=False, settling=False, agent_id="x"):
    """A sibling subagent transcript proving `_session_has_live_bg_tasks`'s
    file-mtime signal — <base>/<encoded-cwd>/<sid>/subagents/agent-<id>.jsonl.
    `error=True` makes its last turn an unrecovered api-error (a `wedged`
    lane: count_live_workers drops it from its live COUNT, but compact still
    vetoes on it — #565-review). `finished=True` (#587) makes its last real
    turn a COMPLETED text reply with a TERMINAL stop_reason (end_turn — the
    ~78% common case incl. autopilot-worker final reports), a `finished` lane
    that compact must NOT veto on: a per-ticket boundary always follows a
    worker return, so the finished lane's mtime is still fresh at that moment.
    `settling=True` gives the same final text but a NON-terminal (absent)
    stop_reason — the #587 `settling` state, finished only once aged past
    FINISH_SETTLE_S."""
    d = Path(base) / _encode(cwd) / sid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("agent-" + agent_id + ".jsonl")
    if error:
        entry = {"type": "assistant", "isApiErrorMessage": True,
                 "message": {"id": "msg_1", "content": "API Error: 529"}}
    elif finished or settling:
        msg = {"role": "assistant",
               "content": [{"type": "text", "text": "issues: #587 done; merged."}]}
        if finished:
            msg["stop_reason"] = "end_turn"   # terminal → finished immediately
        entry = {"type": "assistant", "message": msg}
    else:
        entry = {"type": "assistant"}
    p.write_text(json.dumps(entry) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _write_bg_bash_transcript(base, cwd, sid, marker_text=None, live=True,
                              tool_use_id="toolu_bg1"):
    """A MAIN transcript at <base>/<encoded-cwd>/<sid>.jsonl whose tail carries
    a `run_in_background:true` Bash tool_use (a START — the shape measured live
    on cambox's transcript). `live=True` → NO completion follows, so
    `session_has_live_bg_bash` reads a LIVE bg job (a `/compact` would orphan
    it). `live=False` → a later `<task-notification>` names that tool_use id
    (COMPLETION), so the job reads NOT live. An optional trailing `marker_text`
    assistant turn lets a test combine the marker check with the bg-bash check;
    with `marker_text=None` the last real turn is the bg START itself (marker
    '' — not a `❓`, so condition (c) passes and the bg-bash veto is what fires)."""
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    lines = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_use_id, "name": "Bash",
         "input": {"command": "bash fleet-upgrade.sh", "run_in_background": True}}]}}]
    if not live:
        lines.append({"type": "user", "message": {"content": (
            "<task-notification>\n<task-id>bq1</task-id>\n"
            "<tool-use-id>%s</tool-use-id>\n</task-notification>" % tool_use_id)}})
    if marker_text is not None:
        lines.append({"type": "assistant",
                      "message": {"id": "m2", "content": marker_text}})
    p.write_text("\n".join(json.dumps(e) for e in lines) + "\n")
    return p


CB_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
CB_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
CB_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
CB_ALL_CHROME_NO_BOX_CAP = ("  ctx ███░  caveman:lite\n"
                            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
                            "● main\n")
CB_QUEUED_COMPACT_CAP = "● Hotovo.\n❯ /compact\n❯ \n  ctx ███░  caveman:lite\n"
CB_BG_AGENT_CAP = ("● Working…\nWaiting for 1 background agent to finish\n"
                   "❯ \n  ctx ███░\n")
# #565: the saturated-supervisor incident shape -- an idle-at-prompt main
# showing LIVE agent-strip rows (`◯ … local_agent · #N …`) and a bare `❯` box,
# but NO "Waiting for N background agents" row (that row is not rendered in this
# state). Signal (a) reads False here even though ~10 lanes are live -- the
# whole point of #565's structured signal (b).
CB_IDLE_STRIP_ROWS_CAP = ("● Predošlá práca hotová.\n"
                          "◯ opus-4.8 local_agent · #4023 handoff E2E · 405k\n"
                          "◯ opus-4.8 local_agent · #4026 release-PR CI · 817k\n"
                          "❯ \n  ctx ███░  caveman:lite\n")

# #425: a real supervisor's own turn -- a genuine `## ✅ Work Complete`
# heading for the ticket that just finished, trailing `⏳ WORKING` for
# UNRELATED, independent, still-running parallel workers in the SAME
# round. This is the exact live-incident shape (dev1 sid 2d02a127, and a
# sibling project box) that used to loop `SKIP not-a-boundary` until the
# 30-min request expired.
_WORK_COMPLETE_PLUS_TAIL = (
    "## ✅ Work Complete\n\n"
    "**Audits & deploy:**\n"
    "✅ CI: green\n\n"
    "---\n\n"
    "**Goal:** oprava chyby v X\n"
    "**What changed:** X je teraz opravené\n\n"
    "**[repo] PR #41: fix X**\n"
    "https://github.com/o/r/pull/41 — merged abc123\n\n"
    "⏳ WORKING: 3 more workers still dispatched in this round"
)


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


class CompactSubmitFake:
    """Stateful fake for the SUBMIT-VERIFY path (#375 part 2). Unlike
    `DeliverCompactFakeTmux` (static/scripted captures), this one MODELS the
    input box and the swallowed-Enter behaviour the real fix recovers from, so
    a mutation dropping the post-send verify / the corrective Escape+Enter /
    the undo is caught by BOTH the resulting box state AND the keystroke
    sequence — never invisible behind an unmodeled keystroke (the fixture
    mutation-invisibility trap this repo's playbook warns about).

    `swallow_enters`: how many Enter keystrokes are SWALLOWED (box unchanged)
    before one submits (clears the box) — models the agent-strip selector /
    menu overlay eating the Enter (#36). A single `Escape` NEVER clears the box
    (a single Escape does not delete a CC draft, #35) — it is a no-op on the
    box and only recorded as a keystroke. A `BSpace` batch removes that many
    trailing chars (the undo path). The pane resolves via list-panes exactly
    like `DeliverCompactFakeTmux`."""

    def __init__(self, panes, swallow_enters=0):
        self.panes = panes          # [(pane_id, cmd, cwd, pid)]
        self.box = ""
        self.swallow_enters = swallow_enters
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s\t%s" % t for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            if "-l" in argv:
                self.box += argv[-1]                     # literal type
            elif "BSpace" in argv:
                self.box = self.box[:-argv.count("BSpace")]   # "" if over-run
            elif argv[-1] == "Enter":
                if self.swallow_enters > 0:
                    self.swallow_enters -= 1             # Enter swallowed
                else:
                    self.box = ""                        # submitted
            # Escape (or anything else) is a no-op on the box (#35).
            return ""
        if "capture-pane" in j:
            return ("● Predošlá práca hotová.\n❯ %s\n"
                    "  ctx ███░  caveman:lite\n" % self.box)
        return ""

    def keys(self):
        return [a[-1] for a in self.sent]

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def bspace_batches(self):
        return [a.count("BSpace") for a in self.sent if "BSpace" in a]


# --------------------------------------------------------------------------- #
# 2. Request state — record / load / clear, non-refreshable age anchor.
# --------------------------------------------------------------------------- #

class CompactRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(compact.record_compact_request(
            "sess-1", "/home/x/proj", now=1000, path=p, origin="self-callback"))
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["ts"], 1000)
        self.assertEqual(d["sess-1"]["origin"], "self-callback")

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(compact.record_compact_request("", "/x", path=p))
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_ts_is_refreshed_across_a_re_record(self):
        # #599 SUPERSEDE (reverses #400's non-refreshable anchor): a re-record
        # from a genuine boundary REFRESHES `ts` — a recorded boundary request
        # is a STANDING claim that holds until delivered, and each new boundary
        # is a genuinely-newer claim. Now that `ts` measures "time since the
        # LAST boundary", a busy loop's request never expires (holds until it
        # delivers at a safe moment), while a gone-quiet session's ages out
        # after 30 min of no new boundary. The old #400 "possibly-inappropriate
        # late delivery" hazard is now handled by the direct delivery conditions.
        p = self._p()
        compact.record_compact_request("sess-1", "/x", now=1000, path=p,
                                       origin="subagent-stop")
        compact.record_compact_request("sess-1", "/y", now=5000, path=p,
                                       origin="self-callback")
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["ts"], 5000)      # REFRESHED (supersede)
        self.assertEqual(d["sess-1"]["cwd"], "/y")      # latest cwd wins
        self.assertEqual(d["sess-1"]["origin"], "self-callback")  # latest origin

    def test_supersede_dedup_is_one_pending_per_session(self):
        # #599 point 5c: a newer boundary record REPLACES the older (no queue
        # of duplicates) — the dict is keyed by session, so there is always
        # exactly ONE pending request per session, carrying the NEWEST anchor.
        p = self._p()
        for t in (1000, 1200, 1500, 1800):
            compact.record_compact_request("sess-1", "/x", now=t, path=p,
                                           origin="self-callback")
        d = compact.load_compact_requests(p)
        self.assertEqual(list(d.keys()), ["sess-1"])   # one pending, not four
        self.assertEqual(d["sess-1"]["ts"], 1800)      # newest anchor

    def test_origin_takes_newest_value_no_downgrade_protection(self):
        # #599 point 4: the #402-era "origin never downgrades from
        # self-callback" protection is REMOVED with the #425 `⏳` exemption it
        # guarded (that exemption is gone — no origin is second-class). origin
        # now simply takes the newest call's value, in EITHER direction. Both
        # record sites always pass a proven origin, so it stays proven; the
        # #188 unresumed-api-error gate accepts either proven origin.
        p = self._p()
        compact.record_compact_request("sess-1", "/x", now=1000, path=p,
                                       origin="self-callback")
        compact.record_compact_request("sess-1", "/x", now=1001, path=p,
                                       origin="subagent-stop")
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["origin"], "subagent-stop")  # newest wins
        # blank likewise takes the newest value (no downgrade refusal)
        compact.record_compact_request("sess-1", "/x", now=1002, path=p, origin=None)
        self.assertEqual(compact.load_compact_requests(p)["sess-1"]["origin"], "")

    def test_subagent_stop_origin_is_upgraded_by_self_callback(self):
        # The self-callback direction also just takes the newest value.
        p = self._p()
        compact.record_compact_request("sess-1", "/x", now=1000, path=p,
                                       origin="subagent-stop")
        compact.record_compact_request("sess-1", "/x", now=1001, path=p,
                                       origin="self-callback")
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["origin"], "self-callback")

    def test_ts_is_refreshed_even_when_the_prior_anchor_is_already_expired(self):
        # #599: the supersede applies unconditionally — a re-record ALWAYS
        # refreshes `ts` to `now`, even when the prior entry was already past
        # the age cap. This is exactly the desired behavior: a fresh boundary
        # is a fresh standing claim, never inheriting a dead anchor (the old
        # #400 concern about resetting-past-expiry is moot because expiry is no
        # longer the safety mechanism — the direct delivery conditions are).
        p = self._p()
        compact.record_compact_request("sess-1", "/x", now=1000, path=p,
                                       origin="self-callback")
        way_past_the_cap = 1000 + compact.COMPACT_REQUEST_MAX_AGE_S + 1
        compact.record_compact_request("sess-1", "/y", now=way_past_the_cap, path=p,
                                       origin="self-callback")
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["ts"], way_past_the_cap)   # REFRESHED

    def test_clear_removes_one_request_only(self):
        p = self._p()
        compact.record_compact_request("sess-1", "/x", path=p)
        compact.record_compact_request("sess-2", "/y", path=p)
        self.assertTrue(compact.clear_compact_request("sess-1", path=p))
        d = compact.load_compact_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(compact.clear_compact_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_compact_requests_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-x"}):
            self.assertEqual(
                compact.compact_requests_path(),
                Path("/tmp/fake-home-x") / ".claude" / "compact-requests.json")


# --------------------------------------------------------------------------- #
# 3. Delivered store / cooldown (condition d).
# --------------------------------------------------------------------------- #

class CompactCooldownState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-delivered.json")

    def test_no_prior_delivery_is_not_in_cooldown(self):
        p = self._p()
        self.assertFalse(compact.compact_delivery_in_cooldown("sess-1", 1000, path=p))

    def test_recent_delivery_is_in_cooldown(self):
        p = self._p()
        compact.mark_compact_delivery_ts("sess-1", now=1000, path=p)
        self.assertTrue(compact.compact_delivery_in_cooldown("sess-1", 1500, path=p))

    def test_delivery_past_the_interval_is_not_in_cooldown(self):
        p = self._p()
        compact.mark_compact_delivery_ts("sess-1", now=1000, path=p)
        self.assertFalse(compact.compact_delivery_in_cooldown(
            "sess-1", 1000 + compact.COMPACT_MIN_DELIVERY_INTERVAL_S + 1, path=p))

    def test_env_override_clamped_to_a_positive_floor(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S": "-5"}):
            self.assertEqual(compact._compact_min_delivery_interval(), 1)

    def test_env_override_clamped_to_a_ceiling(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S": "999999999"}):
            self.assertEqual(compact._compact_min_delivery_interval(),
                             compact.COMPACT_MIN_DELIVERY_INTERVAL_MAX_S)


# --------------------------------------------------------------------------- #
# 4. deliver_compact — the five conditions, plus the two incident-closing
#    extras, plus the kill-switch, plus logging.
# --------------------------------------------------------------------------- #

class TestDeliverCompact(unittest.TestCase):
    SID = "sess-deliver-1"
    CWD = "/home/newlevel/devel/delivertest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, origin="subagent-stop", now=None, request_ts=None,
           in_mode=False, cap_seq=(), proj=None):
        # a bare (empty-marker) transcript is REQUIRED for pane resolution:
        # `_find_pane_for_session` only matches a pane whose cwd resolves a
        # transcript file with the right stem via `find_active_transcript`.
        if proj is None:
            proj = self._dir()
            _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, in_mode=in_mode,
            cap_seq=cap_seq)
        word = compact.deliver_compact(self.SID, self.CWD, origin=origin,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=now,
                                       request_ts=request_ts)
        return word, tmux, proj

    def test_idle_bare_pane_sends(self):
        word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_exact_keystrokes(self):
        word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_no_pane_skips(self):
        proj = self._dir()      # no transcript, no matching pane
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact("no-such-sid", self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "skip:no-pane")
        self.assertEqual(tmux.sent, [])

    def test_copy_mode_skips(self):
        word, tmux, _ = self._go(CB_IDLE_CAP, in_mode=True)
        self.assertEqual(word, "skip:in-mode")
        self.assertEqual(tmux.sent, [])

    def test_open_dialog_skips(self):
        word, tmux, _ = self._go(CB_DIALOG_CAP)
        self.assertEqual(word, "skip:dialog-open")
        self.assertEqual(tmux.sent, [])

    # -- condition (a): pane idle, no draft, not busy ------------------- #

    def test_genuine_draft_skips(self):
        word, tmux, _ = self._go(CB_DRAFT_CAP)
        self.assertEqual(word, "skip:draft")
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_skips(self):
        # #333: a queued-then-drained /compact fires at whatever LATER turn
        # is first accepted -- typing into a busy pane is refused outright.
        word, tmux, _ = self._go(CB_BUSY_CAP)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_no_boundary_at_all_skips(self):
        word, tmux, _ = self._go(CB_ALL_CHROME_NO_BOX_CAP)
        self.assertEqual(word, "skip:no-input-line")
        self.assertEqual(tmux.sent, [])

    def test_already_queued_compact_is_handled_not_resent(self):
        word, tmux, _ = self._go(CB_QUEUED_COMPACT_CAP)
        self.assertEqual(word, "already-queued")
        self.assertEqual(tmux.sent, [])

    # -- condition (b): no live background tasks ------------------------- #

    def test_live_bg_task_pane_signal_skips(self):
        word, tmux, _ = self._go(CB_BG_AGENT_CAP)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_live_bg_task_transcript_signal_skips(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=time.time())
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=time.time())
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_stale_subagent_transcript_does_not_block(self):
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(
            proj, self.CWD, self.SID,
            mtime=now - compact.COMPACT_LIVE_WORKER_FRESHNESS_S - 60)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")

    def test_587_finished_worker_ghost_no_longer_blocks_compact(self):
        # #587 RED -- the headline fix. A per-ticket boundary ALWAYS follows a
        # worker return (SubagentStop / self-callback report after integration),
        # so at that moment the just-finished worker's transcript is still fresh
        # (mtime well inside the 15-min window). Pre-#587 it reads `live` ->
        # skip:live-tasks forever (the 15-min ghost); before it expires the
        # supervisor dispatches new lanes and the request lapses at the 30-min
        # cap, so the compactable window never exists. Post-#587 a finished lane
        # (last turn a completed text reply) is NOT live -> the compact SENDs.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   finished=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", "".join("".join(s) for s in tmux.sent))

    def test_587_finished_lane_beside_a_running_lane_still_vetoes(self):
        # #587 no-false-send regression: a FINISHED lane must not mask a genuine
        # RUNNING sibling. With one finished + one still-mid-work lane (the bare
        # `{"type":"assistant"}` synthetic that reads `live`), compact still
        # vetoes -- the running lane is real in-flight work compaction would
        # orphan. The finish-immediate reclassification never over-reaches.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   finished=True, agent_id="fin")
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   agent_id="run")   # bare -> reads live
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_587_finished_lane_beside_a_wedged_lane_still_vetoes(self):
        # #587 x #565: a wedged (unrecovered-api-error) lane pending job-1
        # auto-resume is recoverable in-flight work the supervisor still owns, so
        # it must keep vetoing compact even when a sibling lane is finished. The
        # #565 direction (wedged stays live for compact) is preserved.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   finished=True, agent_id="fin")
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   error=True, agent_id="wedged")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_587_fresh_settling_worker_still_vetoes_until_it_settles(self):
        # #587-review: a fresh worker whose last turn is a text reply with a
        # NON-terminal (None) stop_reason could be a text block streamed just
        # before a large tool_use (~14s gap) — it must keep vetoing compact until
        # it has settled, so a running worker mid-large-edit is never orphaned.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now,
                                   settling=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_587_settled_worker_no_longer_blocks_compact(self):
        # the same settling worker, aged past FINISH_SETTLE_S (any pending tool_use
        # would have been flushed by now) → a genuine finish → compact SENDs.
        from watchdog.transcripts import FINISH_SETTLE_S
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=now - (FINISH_SETTLE_S + 5), settling=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")

    def test_565_saturated_supervisor_live_lanes_not_compacted_on_self_callback(self):
        # #565 RED -- the reported live incident: a per-ticket
        # `compact-request --self` on a saturated supervisor with ~10 live
        # worker lanes typed /compact and killed them all. The subagent
        # transcript is >120s old (a lane inside a long CI poll), the pane
        # shows live agent-strip rows but NO "Waiting for N background agents"
        # row, the last turn carries `## ✅ Work Complete` (a per-ticket
        # boundary for ONE lane), and origin is self-callback. It MUST skip:
        # the other lanes are live, unrelated work. Pre-#565 code SENDs (the
        # 120s subagent_active window reads the 300s-old lane as dead).
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now - 300)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")],
                                      CB_IDLE_STRIP_ROWS_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=now)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    # -- #565: a `## ✅ Work Complete` heading NO LONGER exempts condition
    #    (b)'s live-task signals -- one ticket done never means the session
    #    has no live sibling lanes (the exemption stays only for condition
    #    (c)'s ⏳-marker veto) -- #

    def test_heading_plus_tail_no_longer_exempts_the_pane_text_live_task_signal(self):
        # #565 (inverts the pre-#565 exemption): the "Waiting for N background
        # agents" row is a genuine live signal; a Work Complete heading no
        # longer overrides it.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")],
                                      CB_BG_AGENT_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:live-tasks")

    def test_heading_plus_tail_no_longer_exempts_the_subagent_transcript_live_task_signal(self):
        # #565 (inverts the pre-#565 exemption): a fresh, genuinely-live
        # subagent lane is no longer discarded by a Work Complete heading.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=now)
        self.assertEqual(word, "skip:live-tasks")

    def test_heading_plus_tail_live_task_veto_at_the_raced_recheck_no_longer_exempted(self):
        # #565: the #333 fresh-recapture-before-typing re-check re-runs the
        # live-tasks veto -- which is no longer exempted by a Work Complete
        # heading either, so a lane that appears in the raced recapture still
        # blocks. (Was `..._exemption_honoured_at_the_raced_recheck`.)
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP,
                                      cap_seq=[CB_IDLE_CAP, CB_BG_AGENT_CAP])
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:live-tasks-raced")

    def test_565_genuinely_all_done_still_delivers_even_with_a_stale_lane_file(self):
        # #565 no-false-veto regression: removing the (b) exemption must NOT
        # over-block a session that genuinely HAS no live lanes. A Work
        # Complete heading + self-callback + idle strip-row pane (no "Waiting"
        # row) + a lingering STALE subagent file (>15 min) still delivers --
        # the ⏳ tail is excused by condition (c)'s #425 exemption, and
        # condition (b) reads the stale lane as not-live.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        _write_subagent_transcript(
            proj, self.CWD, self.SID,
            mtime=now - compact.COMPACT_LIVE_WORKER_FRESHNESS_S - 60)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")],
                                      CB_IDLE_STRIP_ROWS_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=now)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    # -- condition (c): not on a ⏳/❓ marker; not an unresumed API error - #

    def test_blocked_on_a_question_skips(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "❓ NEEDS YOU: schváliš reštart?")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "skip:not-a-boundary")
        self.assertEqual(tmux.sent, [])

    def test_still_working_marker_now_delivers_for_every_origin(self):
        # #599 FLIP (reverses #333's `⏳` veto): the `⏳` marker NO LONGER
        # vetoes — a recorded boundary PROVES a boundary occurred, and a 24/7
        # loop moves on to `⏳` within seconds. The idle-after-`⏳` case (pane at
        # rest, no live work) is exactly what the owner wants compacted.
        for origin in ("subagent-stop", "self-callback", None):
            proj = self._dir()
            _write_marker_transcript(proj, self.CWD, self.SID, "⏳ WORKING: next batch")
            # a FRESH delivered-path per iteration so the 30-min cooldown of an
            # earlier iteration's send does not mask a later one.
            delp = str(Path(self._dir()) / "delivered.json")
            tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
            word = compact.deliver_compact(self.SID, self.CWD, origin=origin,
                                           run=tmux, projects_dir=proj,
                                           delivered_path=delp)
            self.assertEqual(word, "sent", "origin=%r should deliver on ⏳" % origin)

    def test_done_marker_still_delivers(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "sent")

    # -- #599: the ⏳ marker no longer vetoes (dropped for ALL origins) ---- #

    def test_work_complete_heading_with_parallel_tail_delivers(self):
        # The #425 live-incident shape (a `## ✅ Work Complete` heading + a
        # trailing `⏳` for still-running parallel workers) STILL delivers —
        # now simply because the `⏳` marker no longer vetoes at all (#599), not
        # via a narrow self-callback-only heading exemption.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_plain_working_marker_with_no_heading_now_delivers(self):
        # #599 FLIP: a genuinely mid-work `⏳` with NO completion heading used
        # to defer FOREVER (the old exemption required BOTH self-callback AND a
        # heading). It now delivers — the record proved the boundary, and were
        # there genuinely live work the (b)/bg-bash conditions would catch it
        # (here the pane is idle with none).
        proj = self._dir()
        _write_marker_transcript(
            proj, self.CWD, self.SID,
            "⏳ WORKING: still figuring out the next step")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_question_marker_never_exempted_even_with_heading(self):
        # `❓` is NEVER relaxed (#333/#228), even with a completion heading
        # earlier in the same turn — the session is mid-decision waiting for
        # the user, and the pending question / in-flight ticket the user's
        # answer needs would be lost by a compaction.
        proj = self._dir()
        text = "## ✅ Work Complete\n\n...\n\n❓ NEEDS YOU: schváliš merge PR #41?"
        _write_marker_transcript(proj, self.CWD, self.SID, text)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:not-a-boundary")
        self.assertEqual(tmux.sent, [])

    def test_heading_plus_tail_delivers_for_subagent_stop_origin(self):
        # #599 point 4: a `subagent-stop` boundary is no longer second-class —
        # the `⏳` veto is gone for it too (this is the exact 7-requests-died
        # case the ticket named), so a `⏳` + heading turn delivers.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="subagent-stop",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_working_marker_delivers_for_blank_origin(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin=None,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_montalu3_class_same_ticket_background_wait_with_no_live_bg_delivers(self):
        # #599: a `## ✅ Work Complete` whose trailing `⏳` narrates a
        # same-ticket background wait STILL delivers WHEN there is no actual
        # live bg-bash job in the transcript (the record proved the boundary).
        # The dangerous sub-case — a genuinely-LIVE background wait — is now
        # caught by the point-3 bg-bash veto (see test_live_bg_bash_skips), so
        # this is no longer the "accepted trade-off" the owner rejected.
        proj = self._dir()
        text = ("## ✅ Work Complete\n\n...PR #41 merged...\n\n"
               "⏳ WORKING: waiting for CI on PR #41 to go green before "
               "declaring #41 truly done")
        _write_marker_transcript(proj, self.CWD, self.SID, text)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_unresumed_api_error_skips_for_proven_origin(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, error=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="subagent-stop",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:unresumed-session")

    def test_unresumed_api_error_does_not_apply_to_unproven_origin(self):
        # scoped to proven-boundary origins only -- an unproven origin's
        # request was justified by the supervisor's OWN ✅ turn, already
        # consumed/reported.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, error=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin=None,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    # -- #377: recent human activity ------------------------------------- #

    def test_recent_human_activity_skips(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(proj, self.CWD, self.SID, now - 5)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "skip:recent-human")

    def test_stale_human_activity_does_not_block(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(
            proj, self.CWD, self.SID,
            now - compact.COMPACT_RECENT_HUMAN_ACTIVITY_S - 60)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")

    # -- condition (e): the hard, non-refreshable age cap ----------------- #

    def test_expired_request_is_discarded(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now,
                                 request_ts=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1)
        self.assertEqual(word, "expired")
        self.assertEqual(tmux.sent, [])

    def test_expired_decision_record_names_the_origin(self):
        # #523: a lapsed request's decision record must name its ORIGIN, so a
        # by-design subagent-stop lapse (a `⏳` supervisor that never
        # self-declared a boundary — #425) is distinguishable in triage from a
        # self-callback one. The #486 "silent suppression -> explicit decision
        # log" guardrail; logging-only, no delivery-behaviour change.
        now = time.time()
        self._go(CB_IDLE_CAP, origin="subagent-stop", now=now,
                 request_ts=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1)
        log = self.syncp.read_text()
        self.assertIn("SKIP expired", log)
        self.assertIn("origin=subagent-stop", log)

    def test_request_exactly_at_the_cap_is_not_expired(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now,
                                 request_ts=now - compact.COMPACT_REQUEST_MAX_AGE_S)
        self.assertNotEqual(word, "expired")

    # -- the small #238 race floor (record-time synchronous path only) --- #

    def test_too_young_request_skips_the_live_tasks_no_signal_verdict(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now, request_ts=now - 0.1)
        self.assertEqual(word, "skip:too-young")
        self.assertEqual(tmux.sent, [])

    def test_periodic_sweep_request_ts_none_is_never_too_young(self):
        # the periodic sweep passes request_ts=None -- the ~60s cadence
        # already exceeds the floor naturally, so it's a no-op there.
        word, tmux, _ = self._go(CB_IDLE_CAP, request_ts=None)
        self.assertEqual(word, "sent")

    # -- condition (d): cooldown ------------------------------------------ #

    def test_cooldown_drops_the_request(self):
        now = time.time()
        compact.mark_compact_delivery_ts(self.SID, now=now - 60, path=self.delp)
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now)
        self.assertEqual(word, "cooldown")
        self.assertEqual(tmux.sent, [])

    # -- kill-switch -------------------------------------------------------- #

    def test_owner_disabled_skips(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(word, "skip:disabled")
        self.assertEqual(tmux.sent, [])

    # -- #333: fresh re-verify immediately before the send ------------------ #

    def test_raced_pane_since_the_sweep_is_refused(self):
        # the entry-point capture is idle; a FRESH capture right before the
        # send shows the pane has moved on since -- must refuse, not send.
        word, tmux, _ = self._go(CB_IDLE_CAP, cap_seq=[CB_IDLE_CAP, CB_DRAFT_CAP])
        self.assertEqual(word, "skip:raced")
        self.assertEqual(tmux.sent, [])

    def test_raced_live_tasks_since_the_sweep_is_refused(self):
        word, tmux, _ = self._go(CB_IDLE_CAP, cap_seq=[CB_IDLE_CAP, CB_BG_AGENT_CAP])
        self.assertEqual(word, "skip:live-tasks-raced")
        self.assertEqual(tmux.sent, [])

    # -- logging -------------------------------------------------------------- #

    def test_send_is_logged(self):
        self._go(CB_IDLE_CAP)
        log_text = self.syncp.read_text()
        self.assertIn("SEND", log_text)
        self.assertIn(self.SID, log_text)

    def test_skip_is_logged_with_a_reason(self):
        self._go(CB_DRAFT_CAP)
        log_text = self.syncp.read_text()
        self.assertIn("SKIP draft", log_text)

    def test_send_marks_the_delivery_timestamp(self):
        now = time.time()
        self._go(CB_IDLE_CAP, now=now)
        self.assertTrue(compact.compact_delivery_in_cooldown(self.SID, now, path=self.delp))

    def test_send_marks_janitor_provenance(self):
        state = {}
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, state=state,
                                       now=time.time())
        self.assertEqual(word, "sent")
        self.assertIn("%9", state.get("janitor_watch", {}))


# --------------------------------------------------------------------------- #
# 4a. #375 part 2 — job 14's `/compact` send VERIFIES the submit landed.
# --------------------------------------------------------------------------- #

class TestCompactSubmitVerify(unittest.TestCase):
    """A swallowed Enter (agent-strip selector / menu overlay, #36 class) must
    NOT be reported 'sent': `deliver_compact` verifies the submit landed, does
    ONE corrective Escape+Enter, and on a persistent swallow LEAVES the request
    pending (a `skip:` word, so the caller does not clear it), starts NO 30-min
    cooldown, and backspaces its own text off the (caller-verified-bare) box —
    the compact counterpart of the swallowed-submit recovery goal/stash already
    have (`_send_goal_verified`, `deliver_with_stash`)."""

    SID = "sess-submit-1"
    CWD = "/home/newlevel/devel/submittest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)
        # None of these tests depends on genuine elapsed wall-clock time; keep
        # the (bounded) settle-poll waits instant.
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, swallow_enters, origin="subagent-stop"):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = CompactSubmitFake([("%9", "claude", self.CWD, "111")],
                                 swallow_enters=swallow_enters)
        word = compact.deliver_compact(self.SID, self.CWD, origin=origin,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=time.time())
        return word, tmux

    def _cooldown_started(self):
        return compact.compact_delivery_in_cooldown(
            self.SID, time.time() + 1, path=self.delp)

    def test_clean_submit_is_sent_with_no_corrective_keystrokes(self):
        # The unchanged happy path: a submit that lands needs NO Escape/Enter
        # correction and NO undo — locks that verify never fires the recovery
        # on a clean send.
        word, tmux = self._go(swallow_enters=0)
        self.assertEqual(word, "sent")
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])
        self.assertEqual(tmux.bspace_batches(), [])
        self.assertEqual(tmux.box, "")
        self.assertTrue(self._cooldown_started())

    def test_swallowed_submit_leaves_request_pending_not_sent(self):
        word, _tmux = self._go(swallow_enters=9)
        self.assertEqual(word, "skip:submit-swallowed")
        self.assertNotIn(word, compact._COMPACT_TERMINAL_WORDS)
        # A failed send never starts the 30-min cooldown (which would block the
        # retry the pending request exists to enable).
        self.assertFalse(self._cooldown_started())

    def test_swallowed_submit_recovered_by_one_corrective_escape_enter(self):
        word, tmux = self._go(swallow_enters=1)
        self.assertEqual(word, "sent")
        self.assertEqual(tmux.keys(),
                         ["/compact", "Enter", "Escape", "Enter"])
        self.assertTrue(self._cooldown_started())

    def test_persistent_swallow_undoes_its_own_text_and_sends_one_escape(self):
        word, tmux = self._go(swallow_enters=9)
        self.assertEqual(word, "skip:submit-swallowed")
        self.assertEqual(tmux.box, "")                    # own text backspaced off
        self.assertEqual(tmux.bspace_batches(),
                         [len(compact.COMPACT_TEXT)])
        # Exactly ONE Escape ever — a rapid double-Escape deletes a draft (#35).
        self.assertEqual(tmux.keys().count("Escape"), 1)
        self.assertEqual(
            tmux.keys(),
            ["/compact", "Enter", "Escape", "Enter", "BSpace"])

    def test_a_draft_that_raced_in_pre_send_aborts_without_typing(self):
        # Round-1 review item 3 (keystroke safety): a draft appearing AFTER
        # deliver_compact's own fresh recapture but BEFORE the type keystroke
        # must be rescued and NEVER typed over — otherwise a later undo could
        # backspace the user's characters. Mirrors _send_goal_verified's own
        # raced-busy guard. Driven directly so the non-bare box is presented at
        # exactly the pre-type re-check (not caught earlier).
        keys = []

        def run(argv, timeout=8):
            j = " ".join(argv)
            if "send-keys" in j:
                keys.append(argv[-1])
            if "capture-pane" in j:
                return "● x\n❯ rozpisaný draft usera\n  ctx ███░\n"   # NON-bare
            return ""

        logs = []
        outcome = compact._compact_submit_verified(
            "%9", run, lambda *a, **k: None, lambda r: logs.append(r))
        self.assertEqual(outcome, "raced-busy")
        self.assertEqual(keys, [])            # nothing typed, no Enter, no BSpace
        self.assertTrue(any("raced-busy" in r for r in logs), logs)

    def test_raced_busy_forwards_draft_rescue_failure_logs(self):
        # Round-2 review MINOR-1 (observability parity, #271/#360): a draft
        # rescue that FAILS on the raced path must not be silent — its own log
        # lines are forwarded through log_fn (the sibling passes logs=logs).
        def run(argv, timeout=8):
            if "capture-pane" in " ".join(argv):
                return "● x\n❯ draft usera\n  ctx ███░\n"
            return ""

        def fake_rescue(pid, cap, logs=None, **kw):
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED sentinel")
            return None

        logs = []
        with m.patch.object(wd, "_draft_rescue_persist", fake_rescue):
            compact._compact_submit_verified(
                "%9", run, lambda *a, **k: None, lambda r: logs.append(r))
        self.assertTrue(
            any("draft-rescue: FAILED sentinel" in r for r in logs), logs)


# --------------------------------------------------------------------------- #
# 4b. #425 — the shared exemption predicate + its two consumers' origin
#     scoping, tested DIRECTLY (isolated from each other) -- the end-to-end
#     `deliver_compact` tests above conflate both checks (condition (c)
#     always runs before condition (b)), so a live-tasks-scoping
#     regression on a non-⏳-marker path would be invisible there.
# --------------------------------------------------------------------------- #

class TestCompactConditionBAndMarker(unittest.TestCase):
    """Condition (b) live-worker signal, and condition (c)'s `❓`-only marker
    veto (#599: the `⏳` veto and its `_compact_self_reported_*` /
    `_compact_transcript_completion_heading` machinery were removed — those
    unit tests were deleted with the functions)."""
    SID = "sess-exempt-1"
    CWD = "/home/newlevel/devel/exempttest"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    # -- _session_has_live_bg_tasks: structured live-worker count, NO #425
    #    exemption (#565) --------------------------------------------------- #

    def test_live_bg_tasks_true_for_a_fresh_live_lane_even_with_work_complete(self):
        # #565: a fresh subagent lane counts live REGARDLESS of a
        # `## ✅ Work Complete` heading -- the exemption is gone from (b),
        # so "one ticket done" never masks a live sibling lane.
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now)
        self.assertTrue(compact._session_has_live_bg_tasks(
            None, self.SID, self.CWD, None, projects_dir=proj, now=now))

    def test_live_bg_tasks_pane_waiting_row_alone_is_live(self):
        # signal (a): the "Waiting for N background agents" row, with no
        # subagent transcript at all, is a genuine live positive.
        proj = self._dir()
        run = DeliverCompactFakeTmux([("%1", "claude", self.CWD, "111")],
                                     CB_BG_AGENT_CAP)
        self.assertTrue(compact._session_has_live_bg_tasks(
            "%1", self.SID, self.CWD, run, projects_dir=proj))

    def test_live_bg_tasks_false_with_no_live_lane_and_no_pane_signal(self):
        # no subagent transcript + an idle strip-row pane (no "Waiting" row)
        # -> not live, even with a Work Complete heading present. Locks that
        # #565's removal of the exemption did NOT introduce a false veto.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        run = DeliverCompactFakeTmux([("%1", "claude", self.CWD, "111")],
                                     CB_IDLE_STRIP_ROWS_CAP)
        self.assertFalse(compact._session_has_live_bg_tasks(
            "%1", self.SID, self.CWD, run, projects_dir=proj))

    def test_live_bg_tasks_false_for_a_stale_lane_past_the_freshness_window(self):
        # a subagent transcript older than COMPACT_LIVE_WORKER_FRESHNESS_S is
        # stale -> not counted live (self-heals; a box that just finished a
        # ticket is active, not stuck).
        proj = self._dir()
        now = time.time()
        _write_subagent_transcript(
            proj, self.CWD, self.SID,
            mtime=now - compact.COMPACT_LIVE_WORKER_FRESHNESS_S - 60)
        self.assertFalse(compact._session_has_live_bg_tasks(
            None, self.SID, self.CWD, None, projects_dir=proj, now=now))

    def test_live_bg_tasks_true_for_a_fresh_wedged_lane_pending_auto_resume(self):
        # #565-review 🟡: count_live_workers EXCLUDES a fresh api-errored
        # ("wedged") lane from its live COUNT (correct for its lane-nudge
        # consumer) — but for compact that lane is recoverable in-flight work
        # (pending job-1 auto-resume) the supervisor still owns, and compaction
        # would orphan it. Condition (b) must veto on any NON-STALE lane, not
        # the wedged-excluding count.
        proj = self._dir()
        now = time.time()
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now, error=True)
        self.assertTrue(compact._session_has_live_bg_tasks(
            None, self.SID, self.CWD, None, projects_dir=proj, now=now))

    def test_live_bg_tasks_window_floor_strictly_exceeds_the_10min_bash_cap(self):
        # #565-review 🔵: the load-bearing correctness property is "the window
        # strictly exceeds the 10-min (600s) Bash timeout cap, so a worker
        # silent for one max-length tool call still counts live." A lane silent
        # 605s (just past the cap) MUST veto — an ABSOLUTE age, not relative to
        # the constant, so a mutant shrinking COMPACT_LIVE_WORKER_FRESHNESS_S
        # below ~600 fails HERE (the 300s fixtures would survive such a shrink).
        proj = self._dir()
        now = time.time()
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now - 605)
        self.assertTrue(compact._session_has_live_bg_tasks(
            None, self.SID, self.CWD, None, projects_dir=proj, now=now))

    def test_live_bg_tasks_reads_count_live_workers_at_the_15min_window(self):
        # #565 wiring lock: (b) reads the STRUCTURED count_live_workers at
        # COMPACT_LIVE_WORKER_FRESHNESS_S, not the pre-#565 raw 120s
        # subagent_active window. A 300s-old lane (dead under 120s, live under
        # 15 min) proves BOTH which primitive is consulted AND the window.
        proj = self._dir()
        now = time.time()
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=now - 300)
        seen = {}
        real = wd.count_live_workers

        def spy(pdir, cwd, sid, n, fresh, **kw):
            seen["freshness"] = fresh
            return real(pdir, cwd, sid, n, fresh, **kw)

        with m.patch.object(wd, "count_live_workers", spy):
            result = compact._session_has_live_bg_tasks(
                None, self.SID, self.CWD, None, projects_dir=proj, now=now)
        self.assertTrue(result)
        self.assertEqual(seen["freshness"],
                         compact.COMPACT_LIVE_WORKER_FRESHNESS_S)

    # -- _compact_not_at_boundary: #599 `❓`-only veto --------------------- #

    def test_not_at_boundary_false_for_working_marker_with_heading(self):
        # #599: `⏳` never vetoes now (regardless of heading/origin).
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, _WORK_COMPLETE_PLUS_TAIL)
        self.assertFalse(compact._compact_not_at_boundary(
            self.CWD, self.SID, projects_dir=proj, origin="self-callback"))

    def test_not_at_boundary_false_for_plain_working_marker(self):
        # #599 FLIP: a plain `⏳ WORKING` no longer vetoes (used to return True).
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "⏳ WORKING: no heading here")
        self.assertFalse(compact._compact_not_at_boundary(
            self.CWD, self.SID, projects_dir=proj, origin="self-callback"))

    def test_not_at_boundary_false_for_working_marker_subagent_stop_origin(self):
        # #599 point 4: subagent-stop is no longer second-class — `⏳` doesn't
        # veto for it either (the 7-requests-died case).
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "⏳ WORKING: next ticket")
        self.assertFalse(compact._compact_not_at_boundary(
            self.CWD, self.SID, projects_dir=proj, origin="subagent-stop"))

    def test_not_at_boundary_true_for_question_marker(self):
        # `❓` STILL vetoes (#333/#228), even with a completion heading.
        proj = self._dir()
        _write_marker_transcript(
            proj, self.CWD, self.SID,
            "## ✅ Work Complete\n\n...\n\n❓ NEEDS YOU: schváliš?")
        self.assertTrue(compact._compact_not_at_boundary(
            self.CWD, self.SID, projects_dir=proj, origin="self-callback"))

    def test_not_at_boundary_false_for_done_marker(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        self.assertFalse(compact._compact_not_at_boundary(
            self.CWD, self.SID, projects_dir=proj, origin="self-callback"))


# --------------------------------------------------------------------------- #
# 5. Pane resolution.
# --------------------------------------------------------------------------- #

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
# 6. compact_sweep — the periodic re-evaluation loop.
# --------------------------------------------------------------------------- #

class TestCompactSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/sweeptest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_sends_and_clears_a_pending_request(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-a")
        now = time.time()
        compact.record_compact_request("sess-a", self.CWD, now=now,
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = set()
        # +5s: the periodic sweep runs SOME time after the record -- a
        # same-instant evaluation would (correctly) trip the #238 too-young
        # floor, exactly like a real synchronous attempt right after record.
        logs = compact.compact_sweep(now + 5, run=tmux, projects_dir=proj,
                                     requests_path=self.reqp, delivered_path=self.delp,
                                     handled=handled)
        self.assertIn("sess-a", handled)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertNotIn("sess-a", compact.load_compact_requests(self.reqp))
        self.assertTrue(any("sent" in ln for ln in logs))

    def test_leaves_a_blocked_request_pending(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-b")
        compact.record_compact_request("sess-b", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_DRAFT_CAP)
        compact.compact_sweep(time.time(), run=tmux, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertIn("sess-b", compact.load_compact_requests(self.reqp))

    def test_expired_request_is_discarded_not_retried(self):
        proj = self._dir()
        now = time.time()
        compact.record_compact_request("sess-c", self.CWD,
                                       now=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1,
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        compact.compact_sweep(now, run=tmux, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertNotIn("sess-c", compact.load_compact_requests(self.reqp))
        self.assertEqual(tmux.sent, [])

    def test_expired_lapse_log_names_the_origin(self):
        # #523: the journal LAPSE line for a discarded request must name its
        # origin, so a lapsed subagent-stop request (the by-design #425
        # outcome on a saturated `⏳` supervisor) is a 30-second triage read
        # rather than a re-investigation. #486 explicit-decision-log guardrail.
        proj = self._dir()
        now = time.time()
        compact.record_compact_request("sess-lapse", self.CWD,
                                       now=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1,
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        logs = compact.compact_sweep(now, run=tmux, projects_dir=proj,
                                     requests_path=self.reqp, delivered_path=self.delp)
        self.assertTrue(any("LAPSE" in ln and "origin=subagent-stop" in ln
                            for ln in logs),
                        "LAPSE line must name the request origin: %r" % logs)

    def test_dry_run_sends_nothing(self):
        proj = self._dir()
        compact.record_compact_request("sess-d", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        compact.compact_sweep(time.time(), run=tmux, dry_run=True, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertEqual(tmux.sent, [])
        self.assertIn("sess-d", compact.load_compact_requests(self.reqp))

    def test_owner_disabled_skips_the_whole_sweep(self):
        proj = self._dir()
        compact.record_compact_request("sess-e", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = compact.compact_sweep(time.time(), run=tmux, projects_dir=proj,
                                         requests_path=self.reqp, delivered_path=self.delp)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("DISABLED" in ln for ln in logs))


# --------------------------------------------------------------------------- #
# 7. CLI wiring — `airuleset.py compact-request`.
# --------------------------------------------------------------------------- #

def _args(**kw):
    # `types.SimpleNamespace`, not `m.Mock(self=...)` -- Mock's own
    # `__init__` signature collides with a `self=` kwarg (the CLI's own
    # `--self` flag becomes `args.self`), raising a confusing TypeError
    # that has nothing to do with the code under test.
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


class TestCompactRequestCli(unittest.TestCase):
    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)
        # `cmd_compact_request` now goes through `_compact_sync_attempt`,
        # which sleeps a small BOUNDED margin (~2.1s) on a genuinely fresh
        # record (#402-review MAJOR-1's own fix) -- these tests don't care
        # about the wait's real wall-clock duration, only the disposition,
        # so patch it away module-wide (mirrors the established
        # `test_goal_autoarm.py`/`test_goal_rearm.py` pattern).
        sp = m.patch("time.sleep", lambda s: None)
        sp.start()
        self.addCleanup(sp.stop)

    def test_record_with_no_session_prints_skip(self):
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            airuleset.cmd_compact_request(_args(record=True, session="",
                                                cwd="/x", origin="subagent-stop"))
        self.assertEqual("".join(buf), "skip:no-session")

    def test_record_recognises_delivery_disposition(self):
        # no live pane -> deliver_compact returns skip:no-pane -> printed verbatim
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            airuleset.cmd_compact_request(_args(
                record=True, session="sess-x", cwd="/nowhere",
                origin="subagent-stop"))
        self.assertEqual("".join(buf), "skip:no-pane")
        # the request stays recorded for the next periodic sweep
        self.assertIn("sess-x", compact.load_compact_requests(self.reqp))

    def test_self_with_no_tmux_pane_exits_nonzero(self):
        with m.patch.object(compact, "resolve_self_pane", return_value=("", "", "")):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_compact_request(_args(self=True))
        self.assertNotEqual(cm.exception.code, 0)

    def test_self_records_under_self_callback_origin(self):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-y")):
            with m.patch.object(compact, "deliver_compact", return_value="skip:draft") as dc:
                buf = []
                with m.patch("sys.stdout") as out:
                    out.write = lambda s: buf.append(s)
                    airuleset.cmd_compact_request(_args(self=True))
        self.assertEqual(dc.call_args.kwargs.get("origin"), compact._COMPACT_SELF_CALLBACK_ORIGIN)
        self.assertEqual("".join(buf), "skip:draft")
        entry = compact.load_compact_requests(self.reqp).get("sess-y")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["origin"], "self-callback")

    def test_self_on_a_genuinely_idle_pane_actually_sends_end_to_end(self):
        # #402-review MAJOR-1's own live reproduction: "a perfectly idle
        # pane, zero live tasks -- word = skip:too-young, zero keystrokes."
        # `deliver_compact` is NOT mocked here -- a real call, through the
        # REAL `_compact_sync_attempt`, against a real (fake-tmux) idle
        # pane, is the only thing that actually proves the fix: a fresh
        # `--self` call must be able to send, not just always defer.
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        cwd = "/home/newlevel/devel/synctest-cli"
        sid = "sess-cli-sync"
        _write_marker_transcript(proj.name, cwd, sid)
        tmux = DeliverCompactFakeTmux([("%9", "claude", cwd, "111")], CB_IDLE_CAP)
        with m.patch("time.sleep", _REAL_SLEEP):    # a REAL, short sleep here
            with m.patch.object(compact, "resolve_self_pane",
                                return_value=("%9", cwd, sid)):
                with m.patch.object(compact.watchdog, "_default_run", tmux):
                    with m.patch.object(compact.watchdog, "PROJECTS_DIR", proj.name):
                        buf = []
                        with m.patch("sys.stdout") as out:
                            out.write = lambda s: buf.append(s)
                            airuleset.cmd_compact_request(_args(self=True))
        self.assertEqual("".join(buf), "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_terminal_word_clears_the_request(self):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-z")):
            with m.patch.object(compact, "deliver_compact", return_value="sent"):
                with m.patch("sys.stdout"):
                    airuleset.cmd_compact_request(_args(self=True))
        self.assertNotIn("sess-z", compact.load_compact_requests(self.reqp))

    def test_no_flags_prints_usage_and_exits(self):
        with m.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_compact_request(_args())
        self.assertNotEqual(cm.exception.code, 0)


# --------------------------------------------------------------------------- #
# 7b. `_compact_sync_attempt` — #402-review MAJOR-1's own fix: the ONE
#     synchronous attempt must not be structurally dead for a FRESH
#     request just because it was recorded in the same call.
# --------------------------------------------------------------------------- #

class TestCompactSyncAttempt(unittest.TestCase):
    SID = "sess-sync-1"
    CWD = "/home/newlevel/devel/synctest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)
        self.proj = TemporaryDirectory()
        self.addCleanup(self.proj.cleanup)
        _write_marker_transcript(self.proj.name, self.CWD, self.SID)

    def _tmux(self, captured=CB_IDLE_CAP):
        return DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured)

    def test_a_fresh_request_still_sends_end_to_end(self):
        # THE bug: request_ts == now (same call) used to make
        # `_compact_request_too_young` refuse EVERY fresh request
        # unconditionally. A fake `sleep_fn` that just advances a
        # deterministic fake clock (never a real sleep) proves the wait
        # genuinely clears the floor and the REAL `deliver_compact` call
        # then sends -- no mocking of `deliver_compact` itself.
        clock = [1_000_000.0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            clock[0] += s

        tmux = self._tmux(CB_IDLE_CAP)
        word = compact._compact_sync_attempt(
            self.SID, self.CWD, "self-callback", run=tmux,
            projects_dir=self.proj.name, delivered_path=self.delp,
            requests_path=self.reqp, now_fn=now_fn, sleep_fn=sleep_fn)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())
        # the sleep genuinely ran (clock advanced by roughly the floor)
        self.assertGreaterEqual(clock[0] - 1_000_000.0,
                                compact.COMPACT_MIN_REQUEST_AGE_S)
        # terminal word -> the request is cleared, not left pending
        self.assertNotIn(self.SID, compact.load_compact_requests(self.reqp))

    def test_the_sleep_is_a_single_bounded_call_never_a_loop(self):
        clock = [2_000_000.0]
        calls = []

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            calls.append(s)
            clock[0] += s

        tmux = self._tmux(CB_IDLE_CAP)
        compact._compact_sync_attempt(
            self.SID, self.CWD, "self-callback", run=tmux,
            projects_dir=self.proj.name, delivered_path=self.delp,
            requests_path=self.reqp, now_fn=now_fn, sleep_fn=sleep_fn)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(
            calls[0],
            compact.COMPACT_MIN_REQUEST_AGE_S + compact.COMPACT_SYNC_ATTEMPT_MARGIN_S,
            places=6)

    def test_sync_attempt_records_fresh_anchor_then_sleeps_the_floor_and_sends(self):
        # #599 supersede (reverses #400's non-refreshable anchor): the sync
        # attempt's OWN record always sets a FRESH `ts` (never inherits an older
        # anchor), so a fresh boundary sleeps the #238 floor once and delivers
        # when the clock has advanced past it.
        compact.record_compact_request(self.SID, self.CWD, now=3_000_000.0,
                                       path=self.reqp, origin="self-callback")
        clock = [3_000_000.0 + compact.COMPACT_MIN_REQUEST_AGE_S + 10]
        calls = []

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            calls.append(s)
            clock[0] += s          # advance the clock by the slept duration

        tmux = self._tmux(CB_IDLE_CAP)
        word = compact._compact_sync_attempt(
            self.SID, self.CWD, "self-callback", run=tmux,
            projects_dir=self.proj.name, delivered_path=self.delp,
            requests_path=self.reqp, now_fn=now_fn, sleep_fn=sleep_fn)
        self.assertEqual(word, "sent")
        self.assertEqual(len(calls), 1)       # slept the #238 floor exactly once

    def test_record_failure_reports_skip_no_session_and_never_sleeps(self):
        calls = []
        with m.patch.object(compact, "record_compact_request", return_value=False):
            word = compact._compact_sync_attempt(
                self.SID, self.CWD, "self-callback",
                requests_path=self.reqp, delivered_path=self.delp,
                now_fn=lambda: 4_000_000.0, sleep_fn=lambda s: calls.append(s))
        self.assertEqual(word, "skip:no-session")
        self.assertEqual(calls, [])

    def test_a_not_yet_safe_pane_still_leaves_the_request_pending(self):
        # A skip word must NOT clear the request -- the periodic sweep
        # still needs to see it.
        tmux = self._tmux(CB_DRAFT_CAP)
        word = compact._compact_sync_attempt(
            self.SID, self.CWD, "self-callback", run=tmux,
            projects_dir=self.proj.name, delivered_path=self.delp,
            requests_path=self.reqp, now_fn=lambda: 5_000_000.0,
            sleep_fn=lambda s: None)
        self.assertEqual(word, "skip:draft")
        self.assertIn(self.SID, compact.load_compact_requests(self.reqp))


# --------------------------------------------------------------------------- #
# 8. Kill-switch survives the collapse.
# --------------------------------------------------------------------------- #

class TestKillSwitch(unittest.TestCase):
    def test_owner_disabled_reads_the_flag_file(self):
        with m.patch("os.path.exists", return_value=True):
            with m.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
                self.assertTrue(wd._owner_disabled("compact"))

    def test_test_ignore_env_overrides_the_flag(self):
        with m.patch("os.path.exists", return_value=True):
            with m.patch.dict(os.environ, {"AIRULESET_TEST_IGNORE_DISABLE": "1"}):
                self.assertFalse(wd._owner_disabled("compact"))


# --------------------------------------------------------------------------- #
# 9. run_once wiring — job 14 dispatches into compact_sweep; job 26 is gone.
# --------------------------------------------------------------------------- #

class TestRunOnceCompactWiring(unittest.TestCase):
    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_not_wired_means_no_compact_dispatch(self):
        with m.patch.object(compact, "compact_sweep") as sw:
            wd.run_once(now=time.time(), run=DeliverCompactFakeTmux([], ""),
                       send_fn=lambda *a, **k: "sent",
                       projects_dir=Path(self._dir()) / "projects",
                       state_path=str(Path(self._dir()) / "state.json"))
        sw.assert_not_called()

    def test_wired_dispatches_compact_sweep(self):
        reqp = Path(self._dir()) / "requests.json"
        with m.patch.object(compact, "compact_sweep", return_value=["OK"]) as sw:
            wd.run_once(now=time.time(), run=DeliverCompactFakeTmux([], ""),
                       send_fn=lambda *a, **k: "sent",
                       projects_dir=Path(self._dir()) / "projects",
                       state_path=str(Path(self._dir()) / "state.json"),
                       compact_requests_path=str(reqp))
        sw.assert_called_once()

    def test_run_once_has_no_compact_stall_enabled_param(self):
        # job 26 (compact-stall watch) is REMOVED wholesale (#402) -- the
        # param it used to gate on must be gone, not merely defaulted off.
        import inspect
        params = inspect.signature(wd.run_once).parameters
        self.assertNotIn("compact_stall_enabled", params)

    def test_compact_stall_watch_function_no_longer_exists(self):
        self.assertFalse(hasattr(wd, "compact_stall_watch"))


# --------------------------------------------------------------------------- #
# #599 — the run_in_background Bash liveness detector (transcripts.py), its
# isolation from the worker-lane primitive, and the compact bg-bash veto +
# boundary standing-claim delivery.
# --------------------------------------------------------------------------- #

class TestBgBashDetector599(unittest.TestCase):
    """#599 point 3 — the STRUCTURED run_in_background Bash liveness detector,
    and its DELIBERATE isolation from the worker-lane primitive so the #571
    lane-occupancy / gk-fill consumers are structurally unaffected."""

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _bg_start(self, tid):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "Bash",
             "input": {"command": "x", "run_in_background": True}}]}}

    def _completion(self, tid):
        return {"type": "user", "message": {"content": (
            "<task-notification>\n<task-id>b</task-id>\n"
            "<tool-use-id>%s</tool-use-id>\n</task-notification>" % tid)}}

    def test_start_without_completion_is_live(self):
        self.assertTrue(wd.session_live_bg_bash([self._bg_start("toolu_a")]))

    def test_start_with_completion_is_not_live(self):
        self.assertFalse(wd.session_live_bg_bash(
            [self._bg_start("toolu_a"), self._completion("toolu_a")]))

    def test_one_open_among_several_completed_is_live(self):
        entries = [self._bg_start("t1"), self._completion("t1"),
                   self._bg_start("t2"), self._completion("t2"),
                   self._bg_start("t3")]   # t3 never completes → live
        self.assertTrue(wd.session_live_bg_bash(entries))

    def test_all_completed_is_not_live(self):
        entries = [self._bg_start("t1"), self._bg_start("t2"),
                   self._completion("t1"), self._completion("t2")]
        self.assertFalse(wd.session_live_bg_bash(entries))

    def test_completion_in_queue_operation_form_is_not_live(self):
        # #599 review 🟡: CC writes a bg completion as a `queue-operation` entry
        # with the `<task-notification>` in a TOP-LEVEL `content` string (NO
        # `message` key) — the DOMINANT form (8860 of ~12.5k on cambox). The
        # message-only reader MISSED it (over-veto); `_task_notification_ids`
        # now reads the top-level content. This test is RED against the old
        # message-only reader.
        qop = {"type": "queue-operation", "content": (
            "<task-notification>\n<task-id>bq</task-id>\n"
            "<tool-use-id>toolu_qop</tool-use-id>\n</task-notification>")}
        self.assertFalse(wd.session_live_bg_bash([self._bg_start("toolu_qop"), qop]))

    def test_completion_in_attachment_form_is_not_live(self):
        # the `attachment` form carries the notification in `attachment.prompt`
        # (measured live) — also read by `_task_notification_ids`.
        att = {"type": "attachment", "attachment": {"type": "queued_command",
               "prompt": ("<task-notification>\n<task-id>ba</task-id>\n"
                          "<tool-use-id>toolu_att</tool-use-id>\n"
                          "</task-notification>")}}
        self.assertFalse(wd.session_live_bg_bash([self._bg_start("toolu_att"), att]))

    def test_bounded_marker_finds_marker_behind_a_large_trailing_entry(self):
        # #599 review 🔵: the bounded marker read (2MB default) must still find a
        # `❓` marker turn even when a large (~1.5MB) trailing entry sits between
        # it and EOF — the drift the tiny synthetic fixtures did not cover.
        proj = self._dir()
        d = Path(proj) / _encode("/home/x/big")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "sidBig.jsonl"
        marker = json.dumps({"type": "assistant",
                             "message": {"id": "m1", "content": "❓ NEEDS YOU: rozhodni"}})
        big = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "x" * 1_500_000}]}})
        p.write_text(marker + "\n" + big + "\n")
        self.assertEqual(wd.transcript_last_marker_bounded(str(p)), "❓")
        self.assertEqual(wd.transcript_last_marker_bounded(str(p)),
                         wd.transcript_last_marker(str(p)))

    def test_a_non_bg_bash_tool_use_is_not_a_bg_job(self):
        entries = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls"}}]}}]   # no run_in_background
        self.assertFalse(wd.session_live_bg_bash(entries))

    def test_unmatched_completion_alone_is_not_live(self):
        # a completion whose START is outside the window must not read live.
        self.assertFalse(wd.session_live_bg_bash([self._completion("t9")]))

    def test_empty_and_garbage_never_raise(self):
        self.assertFalse(wd.session_live_bg_bash([]))
        self.assertFalse(wd.session_live_bg_bash([None, 3, "x", {}]))
        self.assertFalse(wd.session_live_bg_bash(   # wrong-typed text block
            [{"type": "assistant", "message": {"content": [
                {"type": "text", "text": None}]}}]))

    def test_io_wrapper_live_and_not_live(self):
        proj = self._dir()
        p = _write_bg_bash_transcript(proj, "/home/x/p", "sidL", live=True)
        self.assertTrue(wd.session_has_live_bg_bash(str(p)))
        p2 = _write_bg_bash_transcript(proj, "/home/x/q", "sidD", live=False)
        self.assertFalse(wd.session_has_live_bg_bash(str(p2)))

    def test_io_wrapper_unreadable_is_false(self):
        self.assertFalse(wd.session_has_live_bg_bash("/no/such/transcript.jsonl"))

    def test_bg_bash_is_invisible_to_count_live_workers(self):
        # CONSUMER ISOLATION (#565/#587 shared-primitive lesson): a bg-bash job
        # is NOT a worker lane. The lane-occupancy (#571) / gk-fill consumers
        # read count_live_workers / lane_has_live_evidence, whose danger is
        # UNDER-counting a live lane (a false live lane there would SUPPRESS a
        # fill nudge). A session with a LIVE bg-bash job but NO dispatched
        # subagents must read 0 live workers / no live evidence — proving the
        # bg-bash signal is held entirely separate and never leaks into them.
        proj = self._dir()
        cwd, sid = "/home/x/iso", "sidIso"
        _write_bg_bash_transcript(proj, cwd, sid, live=True)
        main = str(Path(proj) / _encode(cwd) / (sid + ".jsonl"))
        self.assertTrue(wd.session_has_live_bg_bash(main))    # bg detected...
        count, evidence = wd.count_live_workers(
            str(proj), cwd, sid, time.time(), 15 * 60)
        self.assertEqual(count, 0)                             # ...but invisible here
        self.assertEqual(evidence, [])
        self.assertFalse(wd.lane_has_live_evidence(evidence))

    def test_bounded_marker_agrees_with_the_full_reader(self):
        proj = self._dir()
        for marker in ("⏳ WORKING: x", "❓ NEEDS YOU: y", "✅ DONE: z", ""):
            p = _write_marker_transcript(proj, "/home/x/m", "sidM", marker)
            self.assertEqual(wd.transcript_last_marker_bounded(str(p)),
                             wd.transcript_last_marker(str(p)),
                             "bounded vs full disagree for %r" % marker)


class TestCompactBgBashVeto599(unittest.TestCase):
    """#599 — deliver_compact's bg-bash veto (point 5b) and the boundary
    STANDING-CLAIM delivery (point 5a)."""
    SID = "sess-bgveto-1"
    CWD = "/home/newlevel/devel/bgvetotest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_live_bg_bash_skips(self):
        # point 5b: a live run_in_background Bash job (no completion) vetoes,
        # with its OWN distinct reason, so a `/compact` never orphans it.
        proj = self._dir()
        _write_bg_bash_transcript(proj, self.CWD, self.SID, live=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:live-bg-bash")
        self.assertEqual(tmux.sent, [])

    def test_completed_bg_bash_delivers(self):
        # a bg job whose completion notification arrived is NOT live → the
        # boundary request delivers.
        proj = self._dir()
        _write_bg_bash_transcript(proj, self.CWD, self.SID, live=False)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_live_bg_bash_beats_the_dropped_working_marker(self):
        # combined: the last turn is `⏳` (no longer vetoes) BUT a live bg-bash
        # job is present → skip:live-bg-bash (NOT skip:not-a-boundary). Proves
        # the `⏳` veto is gone AND the bg-bash veto is what protects the job.
        proj = self._dir()
        _write_bg_bash_transcript(proj, self.CWD, self.SID,
                                  marker_text="⏳ WORKING: fleet upgrade", live=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="self-callback",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:live-bg-bash")

    def test_boundary_recorded_then_working_delivers_as_standing_claim(self):
        # point 5a: a boundary request recorded a while ago (ts within the cap)
        # STILL delivers once the session sits at an idle `⏳` turn with no live
        # work — the record is a STANDING claim, never gated on the last turn
        # still being a boundary.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "⏳ WORKING: next ticket")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        now = 100000.0
        recorded_ts = now - 300   # 5 min ago, well within the 30-min cap
        word = compact.deliver_compact(
            self.SID, self.CWD, origin="self-callback", run=tmux,
            projects_dir=proj, delivered_path=self.delp, now=now,
            request_ts=recorded_ts)
        self.assertEqual(word, "sent")

    def test_env_tunable_window_never_disables_the_veto(self):
        # a malformed/disabling env value falls back to the constant, never off.
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_BG_BASH_TAIL_BYTES": "0",
                           "AIRULESET_COMPACT_BG_BASH_MAX_ENTRIES": "-5"}):
            tb, me2 = compact._compact_bg_bash_window()
        self.assertEqual(tb, compact.COMPACT_BG_BASH_TAIL_BYTES)
        self.assertEqual(me2, compact.COMPACT_BG_BASH_MAX_ENTRIES)


if __name__ == "__main__":
    unittest.main()
