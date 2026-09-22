"""#1066 lane A — an UNHANDLED bounce is derived at the tickets-status refresh,
the turn cannot end while one is un-ACKed (Stop hook), a bounce always stays
workable, and the gk INFRA window's own owner questions stop being mis-routed.

RED-first (dispatch): these lock, with INJECTED fetch/cache seams (no network):
  * cli_bounce_unhandled — the refresh-time derivation (bounce-unanswered → the
    `bounce_unhandled` cache field, fail-open ABSENT on a gh error);
  * slice-quals --bounces --unhandled — prints the SAME list;
  * gates.bounce_unhandled — the Stop runner (block only an un-ACKed unhandled
    bounce older than the 30-min grace; fail-open on no/unreadable cache);
  * cli_quals._partition_workable — the 6474 combo stays workable (re-lock);
  * gates.questionscope — the infra-LABEL verdict is scoped by the asking
    window's role (INFRA window passes; FLOW window still routes to infra);
  * skills/process-subdev/SKILL.md — the foreign-bounce re-home bullet.
"""
import json
import os
import sys
import time
import unittest
import unittest.mock as m
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_bounce_unhandled as bu       # noqa: E402
import cli_quals                        # noqa: E402
import gates.bounce_unhandled as gate   # noqa: E402
import gates.questionscope as qs        # noqa: E402

GK = "zbynekdrlik"
STREAM = "odoo-erp-stream-tokens"

BOUNCE_BODY = (
    "## Gatekeeper review — BOUNCE r5\n"
    "gk-state: BOUNCE @a1b2c3d4\n"
    "**Počty (otvorené @ a1b2c3d4)**: 2\n"
    "- 🔴 1 — security: token logged\n"
)
RFR_BODY = (
    "READY-FOR-REVIEW: branch worktree-x head deadbee1\n"
    "Closes-finding: 1 — fixed in def456\n"
)


def _row(cid, login, body, created_at):
    return {"id": cid, "login": login, "body": body, "created_at": created_at}


def _labels(*names):
    return [{"name": n} for n in names]


def _rows(*specs):
    out = {}
    for number, *names in specs:
        out[number] = {"number": number, "labels": _labels(*names)}
    return out


# --------------------------------------------------------------------------- #
# The refresh-time derivation (cli_bounce_unhandled).
# --------------------------------------------------------------------------- #
class DeriveUnhandled(unittest.TestCase):
    def test_bounce_newer_than_last_stream_comment_is_unhandled(self):
        # gk BOUNCE at t=100, no later stream RFR → bounce-unanswered → reported
        # with the BOUNCE's own epoch as verdict_ts.
        rows = {6474: [
            _row(1, STREAM, "working on it", "2026-09-16T10:00:00Z"),
            _row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z"),
        ]}
        out, read_ok, unreadable = bu.unhandled_from_fetch(
            [6474], fetch=lambda n: rows[n], gk_login=GK, self_login=STREAM)
        self.assertTrue(read_ok)
        self.assertEqual(unreadable, [])
        self.assertEqual([e["number"] for e in out], [6474])
        import cli_gk_watch
        self.assertEqual(out[0]["verdict_ts"],
                         cli_gk_watch._parse_iso("2026-09-16T14:21:00Z"))

    def test_stream_commented_after_bounce_is_not_unhandled(self):
        rows = {6474: [
            _row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z"),
            _row(3, STREAM, RFR_BODY, "2026-09-16T15:00:00Z"),
        ]}
        out, read_ok, unreadable = bu.unhandled_from_fetch(
            [6474], fetch=lambda n: rows[n], gk_login=GK, self_login=STREAM)
        self.assertTrue(read_ok)
        self.assertEqual(out, [])
        self.assertEqual(unreadable, [])

    def test_mixed_success_reports_confirmed_and_omits_unreadable(self):
        # #1066 review R1: one member unreadable (gh error → None), one member a
        # CONFIRMED unhandled bounce. The confirmed member is reported (NEVER
        # dropped for the sibling's failure); the unreadable member is omitted
        # AND surfaced as `unreadable` (never silently claimed handled).
        rows = {
            6474: [_row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z")],   # unhandled
            6413: None,                                                  # gh error
        }
        out, read_ok, unreadable = bu.unhandled_from_fetch(
            [6474, 6413], fetch=lambda n: rows[n], gk_login=GK,
            self_login=STREAM)
        self.assertTrue(read_ok)
        self.assertEqual([e["number"] for e in out], [6474])
        self.assertEqual(unreadable, [6413])

    def test_mixed_all_readable_no_unreadable(self):
        rows = {
            6474: [_row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z")],
            6413: [_row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z"),
                   _row(3, STREAM, RFR_BODY, "2026-09-16T15:00:00Z")],  # handled
        }
        out, read_ok, unreadable = bu.unhandled_from_fetch(
            [6474, 6413], fetch=lambda n: rows[n], gk_login=GK,
            self_login=STREAM)
        self.assertEqual([e["number"] for e in out], [6474])
        self.assertEqual(unreadable, [])

    def test_gh_failure_yields_absent_field(self):
        # A gh error (fetch returns None) → derive_numbers returns None so the
        # caller leaves the cache field ABSENT (fail-open, never a false '0').
        got = bu.derive_numbers([6474], cwd="/repo", slug="o/r",
                                fetch=lambda n: None)
        self.assertIsNone(got)

    def test_bounce_numbers_extracted_from_partition_buckets(self):
        workable = _rows((7431, "prio:bounce"), (10, "enhancement"))
        waiting = _rows((3, "prio:bounce", "needs-answer"))
        ops_wait = _rows((6413, "prio:bounce", "ops-wait"), (5, "ops-wait"))
        self.assertEqual(bu._bounce_numbers(workable, waiting, ops_wait),
                         {7431, 3, 6413})

    def test_no_bounce_members_is_empty_list_not_none(self):
        # Truthful 0 (not a read failure): an empty slice → [].
        self.assertEqual(bu.derive_numbers([], cwd="/repo", slug="o/r"), [])

    def test_derive_at_refresh_wires_buckets(self):
        rows = {6474: [_row(2, GK, BOUNCE_BODY, "2026-09-16T14:21:00Z")]}
        workable = _rows((6474, "prio:bounce"))
        out = bu.derive_at_refresh(
            workable, {}, {}, cwd="/repo", slug="o/r",
            fetch=lambda n: rows[n], gk_login=GK, self_login=STREAM)
        self.assertEqual([e["number"] for e in out], [6474])


class AttachUnhandled(unittest.TestCase):
    def test_sets_field_when_derivable(self):
        entry = {}
        with m.patch.object(bu, "derive_at_refresh",
                            return_value=[{"number": 6474, "verdict_ts": 1.0}]):
            bu.attach_unhandled(entry, _rows((6474, "prio:bounce")), {}, {},
                                "/repo", "o/r")
        self.assertEqual(entry["bounce_unhandled"],
                         [{"number": 6474, "verdict_ts": 1.0}])

    def test_leaves_field_absent_on_read_failure(self):
        entry = {}
        with m.patch.object(bu, "derive_at_refresh", return_value=None):
            bu.attach_unhandled(entry, _rows((6474, "prio:bounce")), {}, {},
                                "/repo", "o/r")
        self.assertNotIn("bounce_unhandled", entry)

    def test_never_crashes_the_refresh(self):
        # attach_unhandled must swallow ANY derivation error and leave the field
        # absent — the footer refresh can never crash on this addition.
        entry = {}
        with m.patch.object(bu, "derive_at_refresh",
                            side_effect=RuntimeError("boom")):
            bu.attach_unhandled(entry, _rows((6474, "prio:bounce")), {}, {},
                                "/repo", "o/r")
        self.assertNotIn("bounce_unhandled", entry)


# --------------------------------------------------------------------------- #
# The Stop runner (gates.bounce_unhandled) — cache-driven, fail-open.
# --------------------------------------------------------------------------- #
class StopRunner(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp(prefix="bu-home-")
        self.cwd = "/repo/x"
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.home, ignore_errors=True))

    def _write_cache(self, bounce_unhandled):
        import hashlib
        key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]
        d = Path(self.home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        (d / (key + ".json")).write_text(json.dumps(
            {"bounce_unhandled": bounce_unhandled}))

    def _payload(self, msg):
        return json.dumps({"last_assistant_message": msg, "cwd": self.cwd,
                           "session_id": "s-" + uuid.uuid4().hex[:6]})

    def test_unhandled_overdue_without_ack_blocks(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600}])
        block, reason = gate.decide(
            self._payload("merged five PRs. ✅ DONE"), now=now, home=self.home)
        self.assertTrue(block)
        self.assertIn("6474", reason)
        # names the required action
        self.assertRegex(reason.lower(), r"ack|relay|bounce-ack")

    def test_ack_line_allows(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600}])
        block, _ = gate.decide(
            self._payload("BOUNCE-ACK: #6474 — starting the rework lane. ⏳"),
            now=now, home=self.home)
        self.assertFalse(block)

    def test_within_grace_allows(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 60}])
        block, _ = gate.decide(self._payload("✅ DONE"), now=now, home=self.home)
        self.assertFalse(block)

    def test_grace_boundary_exact(self):
        # Contract: `(now - verdict_ts) < grace` allows, so `>= grace` blocks.
        now = time.time()
        grace = gate._grace_seconds()
        # just INSIDE the grace (age = grace-1) → allow
        self._write_cache([{"number": 6474, "verdict_ts": now - (grace - 1)}])
        block, _ = gate.decide(self._payload("✅ DONE"), now=now, home=self.home)
        self.assertFalse(block, "age just under grace is not yet blockable")
        # exactly AT / past the grace edge (age >= grace) → block
        self._write_cache([{"number": 6474, "verdict_ts": now - grace}])
        block, _ = gate.decide(self._payload("✅ DONE"), now=now, home=self.home)
        self.assertTrue(block, "age at/over grace is blockable")

    def test_ack_exact_number_no_prefix_match(self):
        # `BOUNCE-ACK: #647` must NOT clear #6474 (the exact-number contract).
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600}])
        block, reason = gate.decide(
            self._payload("BOUNCE-ACK: #647 unrelated. ⏳"),
            now=now, home=self.home)
        self.assertTrue(block, "#647 must not ACK #6474")
        self.assertIn("6474", reason)

    def test_ack_longer_number_does_not_match_shorter(self):
        # The (?![0-9]) lookahead: `BOUNCE-ACK: #6474` must NOT clear cache #647.
        now = time.time()
        self._write_cache([{"number": 647, "verdict_ts": now - 3600}])
        block, reason = gate.decide(
            self._payload("BOUNCE-ACK: #6474 done. ⏳"),
            now=now, home=self.home)
        self.assertTrue(block, "#6474 must not ACK #647")
        self.assertIn("647", reason)

    def test_ack_no_hash_still_matches(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600}])
        block, _ = gate.decide(
            self._payload("BOUNCE-ACK: 6474 (no hash). ⏳"),
            now=now, home=self.home)
        self.assertFalse(block)

    def test_unmeasurable_verdict_ts_allows(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": "not-a-number"}])
        block, _ = gate.decide(self._payload("✅ DONE"), now=now, home=self.home)
        self.assertFalse(block)

    def test_no_cache_allows(self):
        block, _ = gate.decide(self._payload("✅ DONE"), home=self.home)
        self.assertFalse(block)

    def test_unreadable_cache_allows(self):
        import hashlib
        key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]
        d = Path(self.home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        (d / (key + ".json")).write_text("{ not json")
        block, _ = gate.decide(self._payload("✅ DONE"), home=self.home)
        self.assertFalse(block)

    def test_empty_unhandled_list_allows(self):
        self._write_cache([])
        block, _ = gate.decide(self._payload("✅ DONE"), home=self.home)
        self.assertFalse(block)

    def test_partial_ack_still_blocks_on_the_unacked_member(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600},
                           {"number": 6413, "verdict_ts": now - 7200}])
        block, reason = gate.decide(
            self._payload("BOUNCE-ACK: #6474 done. ⏳"), now=now, home=self.home)
        self.assertTrue(block)
        self.assertIn("6413", reason)
        self.assertNotIn("6474", reason)

    def test_main_exits_2_on_block(self):
        now = time.time()
        self._write_cache([{"number": 6474, "verdict_ts": now - 3600}])
        env = {**os.environ, "HOME": self.home}
        import subprocess
        r = subprocess.run(
            [sys.executable, "-P", "-m", "gates.bounce_unhandled"],
            input=self._payload("merged five PRs. ✅ DONE"),
            text=True, capture_output=True, cwd=str(REPO),
            env={**env, "PYTHONPATH": str(REPO)})
        self.assertEqual(r.returncode, 2)
        self.assertIn("6474", (r.stdout + r.stderr))


# --------------------------------------------------------------------------- #
# slice-quals --bounces --unhandled CLI print.
# --------------------------------------------------------------------------- #
class CliPrintUnhandled(unittest.TestCase):
    def _run(self, bounce_rows, derive_ret):
        import contextlib
        import io
        import airuleset
        import cli_quals_cmd
        out = io.StringIO()
        with m.patch.object(airuleset, "_repo_slug", return_value="o/r"), \
             m.patch.object(airuleset, "_slice_mine_and_handed",
                            return_value=(bounce_rows, {}, False)), \
             m.patch.object(bu, "derive_numbers", return_value=derive_ret), \
             contextlib.redirect_stdout(out):
            cli_quals_cmd._print_bounce_unhandled(["q"], "/repo", "user")
        return out.getvalue()

    def test_prints_oldest_verdict_first(self):
        rows = {6474: {"number": 6474}, 6413: {"number": 6413}}
        txt = self._run(rows, [{"number": 6474, "verdict_ts": 100.0},
                               {"number": 6413, "verdict_ts": 50.0}])
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        self.assertEqual(lines[0].split("\t")[0], "6413")   # ts 50 first
        self.assertEqual(lines[1].split("\t")[0], "6474")

    def test_no_bounce_members_prints_nothing(self):
        self.assertEqual(self._run({}, []), "")

    def test_read_failure_exits_1(self):
        rows = {6474: {"number": 6474}}
        with self.assertRaises(SystemExit) as cm:
            self._run(rows, None)      # derive_numbers None = gh read failed
        self.assertEqual(cm.exception.code, 1)


# --------------------------------------------------------------------------- #
# Partition re-lock (#1066 item 3): the 6474 combo stays workable.
# --------------------------------------------------------------------------- #
class PartitionReLock(unittest.TestCase):
    def test_6474_combo_is_workable(self):
        w, uw, ow = cli_quals._partition_workable(
            _rows((6474, "stream:montalu1", "prio:bounce", "needs-acceptance",
                   "ops-wait")))
        self.assertIn(6474, w)
        self.assertNotIn(6474, ow)
        self.assertNotIn(6474, uw)


# --------------------------------------------------------------------------- #
# questionscope role scoping (#1066 finding b).
# --------------------------------------------------------------------------- #
class QuestionScopeRole(unittest.TestCase):
    MSG = ("**Otázka — projekt airuleset (fleet):** v tickete #6883 treba "
           "schváliť zmenu configu na PROD hoste.\n\n• A (odporúčam) — X\n"
           "• B — Y\n\n❓ NEEDS YOU: schváliš A alebo B pre #6883?")

    def _payload(self):
        return json.dumps({"last_assistant_message": self.MSG, "cwd": "/repo",
                           "session_id": "qs-" + uuid.uuid4().hex[:6]})

    def test_infra_window_passes(self):
        # From the INFRA window every named ticket carries `infra` by
        # construction — an ordinary owner question there must PASS.
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="infra"):
            block, _ = qs.decide(self._payload(), u_count_fn=lambda c: 0,
                                 role_fn=lambda c: "infra")
        self.assertFalse(block)

    def test_flow_window_still_routes_to_infra(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="infra"):
            block, reason = qs.decide(self._payload(), u_count_fn=lambda c: 0,
                                      role_fn=lambda c: "review")
        self.assertTrue(block)
        self.assertIn("GATEKEEPER-ACTION", reason)

    def test_no_role_defaults_to_route(self):
        # An unresolvable role (None) keeps the existing FLOW routing (the
        # conservative direction — only a CONFIRMED infra window passes).
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="infra"):
            block, _ = qs.decide(self._payload(), u_count_fn=lambda c: 0,
                                 role_fn=lambda c: None)
        self.assertTrue(block)


# --------------------------------------------------------------------------- #
# Doctrine (#1066 item 5): the foreign-bounce re-home bullet.
# --------------------------------------------------------------------------- #
class ProcessSubdevReHome(unittest.TestCase):
    def test_rehome_bullet_present(self):
        txt = (REPO / "skills" / "process-subdev" / "SKILL.md").read_text()
        low = txt.lower()
        self.assertIn("re-home", low)
        # the operative content: another repo/stream's PR + origin keeps ops-wait
        self.assertIn("ops-wait", low)
        self.assertTrue("another repo" in low or "another stream" in low
                        or "other repo" in low)


if __name__ == "__main__":
    unittest.main()
