"""#993/#992 — the `architecture-rework` Scope-gate criterion in
hooks/block-ungated-issue-filing.sh.

A REWORK verdict from the integration-time area-review gate (#993) files an
`architecture-rework` ticket AUTONOMOUSLY (the supervisor, in the same turn).
So the criterion must:

  1. be a VALID Scope-gate criterion (accepted, not `invalid-scope-gate`);
  2. REQUIRE an `Area:` line in the body (one open rework ticket PER AREA — the
     dedup-by-area discipline the ticket mandates), attended or not;
  3. be EXEMPT from the net-drain ratchet + daily/chain caps, like `user-request`
     (a genuine rework verdict must always be able to file), AND — unlike
     user-request/planned-work — NOT require an owner-present quote on the
     unattended path (it is a mechanical verdict, not an owner-asked ticket).

Driven through the REAL shipped hook on stdin JSON, reusing the test harness in
test_scope_gate.py.
"""

import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

from test_scope_gate import run, body_cmd, _fake_gh_netdrain


def _away_sid(tc):
    """A session id whose presence marker is stale (>900s) → UNATTENDED."""
    sid = "t-ar-" + uuid.uuid4().hex[:10]
    mark = Path("/tmp/claude-user-active-%s" % sid)
    mark.write_text("")
    old = time.time() - 1000
    os.utime(mark, (old, old))
    tc.addCleanup(lambda: mark.unlink(missing_ok=True))
    return sid


class TestArchitectureReworkCriterion(TestCase):
    def test_valid_criterion_with_area_passes(self):
        body = ("Area: autopilot orchestration (skills/autopilot/SKILL.md)\n"
                "Patchwork signs: duplicated sources of truth.\n"
                "Target concept: one doctrine home.")
        r = run(body_cmd("rework autopilot orchestration area", body,
                         scope_gate="architecture-rework"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_area_line_blocks(self):
        body = ("Patchwork signs: duplicated sources of truth.\n"
                "Target concept: one doctrine home.")
        r = run(body_cmd("rework some area", body,
                         scope_gate="architecture-rework"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("area", r.stderr.lower())

    def test_criterion_is_recognised_not_invalid(self):
        # Even when blocked for a DIFFERENT reason, it must never be rejected as
        # an unknown criterion.
        body = "no area here"
        r = run(body_cmd("rework x", body, scope_gate="architecture-rework"))
        self.assertNotIn("invalid-scope-gate", r.stderr)

    def test_exempt_from_net_drain_and_presence_when_unattended(self):
        # Repo NOT draining (created >= closed) AND unattended: a non-exempt
        # discovery would be net-drain BLOCKED and a user-request would be
        # presence-BLOCKED. architecture-rework passes on both counts.
        tmp = tempfile.mkdtemp(prefix="ar-netdrain-")
        home = tempfile.mkdtemp(prefix="ar-home-")
        gh = _fake_gh_netdrain(tmp, created=9, closed=1, open_issues=())
        body = ("Area: watchdog (watchdog/__init__.py + jobs)\n"
                "Patchwork signs: incident-driven exceptions stacked.\n"
                "Target concept: structured state.")
        r = run(body_cmd("rework watchdog area", body,
                         scope_gate="architecture-rework"),
                home=home, gh_bin=gh, session_id=_away_sid(self))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_exempt_discovery_still_net_drain_blocks_unattended(self):
        # Control: a NON-exempt criterion on the same non-draining repo IS
        # blocked — proves the harness actually exercises the ratchet.
        tmp = tempfile.mkdtemp(prefix="ar-netdrain2-")
        home = tempfile.mkdtemp(prefix="ar-home2-")
        gh = _fake_gh_netdrain(tmp, created=9, closed=1, open_issues=())
        r = run(body_cmd("a security finding", "genuinely a boundary issue",
                         scope_gate="security-boundary"),
                home=home, gh_bin=gh, session_id=_away_sid(self))
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    main()
