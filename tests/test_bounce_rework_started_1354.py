"""#1354 (part 2): a stream comment opening the rework lane AFTER the BOUNCE
(`Anchors-confirmed:` / `LANE-*`) marks the bounce handled for the Stop gate.
david3 17.11.2026: Anchors-confirmed at 16:22Z and LANE-STATUS at 18:05Z on
odoo-erp 10274 after the 15:42Z BOUNCE, yet the gate kept nagging, because only
a later READY-FOR-REVIEW counted -- while the gate's own message tells the
session that such a lane comment is the way out."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import cli_bounce_unhandled as bu  # noqa: E402

GK = "zbynekdrlik"
STREAM = "odoo-erp-stream-tokens"
BOUNCE = "## Gatekeeper review verdict: BOUNCE\ngk-state: BOUNCE @a1b2c3d4\n- 🔴 1 — x\n"


def _row(cid, login, body, at):
    return {"id": cid, "login": login, "body": body, "created_at": at}


def _run(rows):
    calls = []

    def fetch(n):
        calls.append(n)
        return rows[n]
    out, ok, bad = bu.unhandled_from_fetch(
        list(rows), fetch=fetch, gk_login=GK, self_login=STREAM)
    return [e["number"] for e in out], calls


class TestReworkStarted(unittest.TestCase):
    def test_anchors_confirmed_after_the_bounce_handles_it(self):
        got, _ = _run({10274: [
            _row(1, GK, BOUNCE, "2026-11-17T15:42:00Z"),
            _row(2, STREAM, "Anchors-confirmed: (gk r1 verdict) checked each", "2026-11-17T16:22:00Z")]})
        self.assertEqual(got, [])

    def test_lane_status_after_the_bounce_handles_it(self):
        got, _ = _run({10274: [
            _row(1, GK, BOUNCE, "2026-11-17T15:42:00Z"),
            _row(2, STREAM, "LANE-STATUS r2 (david3): rework committed", "2026-11-17T18:05:00Z")]})
        self.assertEqual(got, [])

    def test_a_bare_ack_comment_does_not_handle_it(self):
        got, _ = _run({10274: [
            _row(1, GK, BOUNCE, "2026-11-17T15:42:00Z"),
            _row(2, STREAM, "BOUNCE-ACK: #10274 r1", "2026-11-17T16:21:00Z")]})
        self.assertEqual(got, [10274])

    def test_a_lane_comment_before_the_bounce_does_not_handle_it(self):
        got, _ = _run({10274: [
            _row(1, STREAM, "LANE-STATUS r1: done", "2026-11-17T13:30:00Z"),
            _row(2, GK, BOUNCE, "2026-11-17T15:42:00Z")]})
        self.assertEqual(got, [10274])

    def test_a_gk_comment_quoting_the_token_does_not_handle_it(self):
        got, _ = _run({10274: [
            _row(1, GK, BOUNCE, "2026-11-17T15:42:00Z"),
            _row(2, GK, "Anchors-confirmed: missing on this ticket", "2026-11-17T16:00:00Z")]})
        self.assertEqual(got, [10274])

    def test_one_read_per_member(self):
        _, calls = _run({10274: [
            _row(1, GK, BOUNCE, "2026-11-17T15:42:00Z"),
            _row(2, STREAM, "LANE-STATUS r2", "2026-11-17T18:05:00Z")]})
        self.assertEqual(calls, [10274])


if __name__ == "__main__":
    unittest.main()
