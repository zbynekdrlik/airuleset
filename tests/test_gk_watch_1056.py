"""cli_gk_watch -- the gk hand-off state primitive (#1056 L1 / #1057 items 1, 2).

RED-first (dispatch): these lock the pure classification (`classify_gk_comment`)
and the per-ticket state machine (`watch_issue`) with an INJECTED fetch, so no
network is touched. The states under test are exactly the dispatch's contract:
bounce-unanswered / needs-disposition / rfr-current / no-gk-comment / unknown.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_gk_watch as gw

GK = "zbynekdrlik"
STREAM = "odoo-erp-stream-tokens"

# A realistic gk BOUNCE verdict body (odoo-erp corpus shape) + finding ids.
BOUNCE_BODY = (
    "## Gatekeeper review — BOUNCE r5\n"
    "gk-state: BOUNCE @a1b2c3d4\n"
    "**Počty (otvorené @ a1b2c3d4)**: 2\n"
    "- 🔴 1 — security: token logged\n"
    "- 🟡 2 — correctness: off-by-one\n"
)
ADVISORY_BODY = (
    "## Gatekeeper delta advisory\n"
    "**Počty (otvorené @ deadbee1)**: 1\n"
    "- 🔵 3 — style nit\n"
)
ACCEPT_BODY = "## Gatekeeper review\ngk-state: ACCEPT @cafe0011\nLGTM, merged.\n"
RFR_BODY = (
    "READY-FOR-REVIEW: branch worktree-x head a1b2c3d4\n"
    "Closes-finding: 1 — fixed in def456\n"
    "Closes-finding: 2 — fixed in def456\n"
)


def _row(cid, login, body, created_at):
    return {"id": cid, "login": login, "body": body, "created_at": created_at}


class ClassifyGkComment(unittest.TestCase):
    def test_bounce_marker_and_sha_and_ids(self):
        verdict, sha, ids = gw.classify_gk_comment(BOUNCE_BODY)
        self.assertEqual(verdict, "BOUNCE")
        self.assertEqual(sha, "a1b2c3d4")
        self.assertIn("1", ids)
        self.assertIn("2", ids)

    def test_advisory_from_counts_header(self):
        verdict, sha, ids = gw.classify_gk_comment(ADVISORY_BODY)
        self.assertEqual(verdict, "ADVISORY")
        self.assertEqual(sha, "deadbee1")
        self.assertIn("3", ids)

    def test_accept_marker(self):
        verdict, sha, _ids = gw.classify_gk_comment(ACCEPT_BODY)
        self.assertEqual(verdict, "ACCEPT")
        self.assertEqual(sha, "cafe0011")

    def test_plain_comment_is_not_a_verdict(self):
        verdict, sha, ids = gw.classify_gk_comment("just a normal note, working on it")
        self.assertIsNone(verdict)
        self.assertIsNone(sha)
        self.assertEqual(ids, [])

    def test_literal_at_sha_header_is_not_a_hexsha(self):
        # `**Počty (otvorené @ sha)**` template header: "sha" is not hex.
        verdict, sha, _ = gw.classify_gk_comment("**Počty (otvorené @ sha)**: 0")
        self.assertEqual(verdict, "ADVISORY")
        self.assertIsNone(sha)


class WatchIssueStates(unittest.TestCase):
    def _watch(self, rows, now=1000.0, head_ts_fn=None):
        return gw.watch_issue(
            5, fetch=lambda _i: rows, gk_login=GK, self_login=STREAM,
            now=now, head_ts_fn=head_ts_fn)

    def test_unknown_on_fetch_failure(self):
        r = gw.watch_issue(5, fetch=lambda _i: None, gk_login=GK,
                           self_login=STREAM)
        self.assertEqual(r["state"], "unknown")

    def test_no_gk_comment(self):
        rows = [_row(1, STREAM, "hello", "2026-09-17T10:00:00Z")]
        self.assertEqual(self._watch(rows)["state"], "no-gk-comment")

    def test_bounce_unanswered_when_bounce_newer_than_rfr(self):
        rows = [
            _row(1, STREAM, RFR_BODY, "2026-09-17T10:00:00Z"),
            _row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z"),   # newer
        ]
        now = gw._parse_iso("2026-09-17T13:00:00Z")
        r = self._watch(rows, now=now)
        self.assertEqual(r["state"], "bounce-unanswered")
        self.assertGreater(r["age_seconds"], 3600)
        self.assertIn("1", r["undispositioned_ids"])
        self.assertEqual(r["gk_latest"]["verdict"], "BOUNCE")

    def test_rfr_current_when_rfr_newer_than_bounce(self):
        rows = [
            _row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z"),
            _row(3, STREAM, RFR_BODY, "2026-09-17T12:00:00Z"),  # newer, disposes 1+2
        ]
        r = self._watch(rows, now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "rfr-current")

    def test_needs_disposition_advisory_after_rfr_undispositioned(self):
        # advisory (id 3) NEWER than the last RFR, never dispositioned.
        rows = [
            _row(3, STREAM, RFR_BODY, "2026-09-17T12:00:00Z"),
            _row(4, GK, ADVISORY_BODY, "2026-09-17T12:30:00Z"),  # newer, id 3
        ]
        r = self._watch(rows, now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "needs-disposition")
        self.assertEqual(r["undispositioned_ids"], ["3"])

    def test_needs_disposition_cleared_by_later_stream_citation(self):
        rows = [
            _row(3, STREAM, RFR_BODY, "2026-09-17T12:00:00Z"),
            _row(4, GK, ADVISORY_BODY, "2026-09-17T12:30:00Z"),
            _row(5, STREAM, "Disposition: 3 — wontfix, style only",
                 "2026-09-17T12:45:00Z"),
        ]
        r = self._watch(rows, now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "rfr-current")

    def test_rfr_line_comment_is_never_a_gk_verdict_even_with_ids(self):
        # A shared-gh-identity box: the SAME login authored both. An RFR line
        # (carrying disposition ids) must read as a hand-off, not a verdict.
        rows = [
            _row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z"),
            _row(3, GK, RFR_BODY, "2026-09-17T12:00:00Z"),   # RFR, same login
        ]
        r = gw.watch_issue(5, fetch=lambda _i: rows, gk_login=GK,
                           self_login=GK,
                           now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "rfr-current")

    def test_head_ts_fn_recorded(self):
        rows = [_row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z")]
        r = self._watch(rows, now=gw._parse_iso("2026-09-17T13:00:00Z"),
                        head_ts_fn=lambda _i: 42.0)
        self.assertEqual(r["head_ts"], 42.0)


class GkWatchIssueWrapper(unittest.TestCase):
    """airuleset.gk_watch_issue wiring — an injected fetch bypasses slug
    resolution + the rate guard (the CLI dry-run seam)."""

    def test_injected_fetch_bounce_unanswered(self):
        import airuleset
        rows = [
            _row(1, STREAM, RFR_BODY, "2026-09-17T10:00:00Z"),
            _row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z"),
        ]
        r = airuleset.gk_watch_issue(
            5, gk_login=GK, self_login=STREAM,
            fetch_fn=lambda _i: rows, head_ts_fn=lambda _i: None,
            now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "bounce-unanswered")

    def test_default_gk_login_is_maintainer(self):
        import airuleset
        rows = [_row(2, airuleset.MAINTAINER_GH_LOGIN, BOUNCE_BODY,
                     "2026-09-17T11:00:00Z")]
        r = airuleset.gk_watch_issue(
            5, self_login=STREAM, fetch_fn=lambda _i: rows,
            head_ts_fn=lambda _i: None,
            now=gw._parse_iso("2026-09-17T13:00:00Z"))
        self.assertEqual(r["state"], "bounce-unanswered")


class CmdGkWatchJson(unittest.TestCase):
    def test_cli_json_dry_run_against_fake_fetch(self):
        import argparse
        import io
        import json as _json
        from contextlib import redirect_stdout
        import airuleset

        rows = {
            "5": [_row(2, GK, BOUNCE_BODY, "2026-09-17T11:00:00Z")],
            "6": [_row(9, STREAM, "hi", "2026-09-17T10:00:00Z")],
        }
        orig = airuleset.gk_watch_issue

        def fake(issue, **kw):
            return orig(issue, gk_login=GK, self_login=STREAM,
                        fetch_fn=lambda _i: rows.get(str(issue)),
                        head_ts_fn=lambda _i: None,
                        now=gw._parse_iso("2026-09-17T13:00:00Z"))

        airuleset.gk_watch_issue = fake
        try:
            args = argparse.Namespace(issues=["5", "6"], repo=None,
                                      gk_login=None, json=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = airuleset.cmd_gk_watch(args)
        finally:
            airuleset.gk_watch_issue = orig
        self.assertEqual(rc, 0)
        out = _json.loads(buf.getvalue())
        by = {r["issue"]: r["state"] for r in out}
        self.assertEqual(by[5], "bounce-unanswered")
        self.assertEqual(by[6], "no-gk-comment")


if __name__ == "__main__":
    unittest.main()
