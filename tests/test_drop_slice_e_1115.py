"""#1115 slice E — security hardening after the 23.9. exposure incident
(comment 5787949642): a worktree checkout can NEVER perform a live Cloudflare
write, and the controller ingress routes ONLY lanes that are eligible to go live.

ABSOLUTE RULE (the incident this slice fixes): NO real Cloudflare API call, NO
reconcile_* with dry_run=False against real clients, NO ~/.secrets read. Every
test here uses injected FAKE clients / an injected is_worktree_fn seam.

Two guards (Approach 1 of the design comment 5787954399):
  (a) reconcile_drop_lanes(dry_run=False) and the reachable Access apply helper
      reconcile_access_for_lane refuse when the checkout is a git worktree —
      a loud line naming #1115/#972, ZERO API calls, a falsy/non-zero result.
  (b) drop_ingress_rules_for_controller emits NO rule (neither /s/ nor drop) for
      a lane that _lane_go_live_eligible rejects (an access lane with no spec).
      A lock asserts every ingress hostname is go-live eligible.
"""
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_drop_golive as gl            # noqa: E402
import cli_drop_lanes as dl             # noqa: E402
import cli_drop_gateway as dg           # noqa: E402
import cli_webterm_access as acc        # noqa: E402

# Reuse the exact fake transports + lane/spec helpers slice B already proved.
from test_drop_golive_1115 import (      # noqa: E402
    FakeDnsTransport, FakeAccessTransport, _lane, _access_spec)


def _dns(records=None):
    import cli_cloudflare_dns as dns
    t = FakeDnsTransport(records=records)
    return dns.DnsClient(token="dns-tok", transport=t), t


def _acc(apps=None):
    t = FakeAccessTransport(apps=apps)
    return acc.AccessClient("acct", token="acc-tok", transport=t), t


# --------------------------------------------------------------------------- #
# (a) LIVE-write refusal from a git worktree checkout
# --------------------------------------------------------------------------- #

class TestWorktreeLiveWriteRefusal(unittest.TestCase):
    def setUp(self):
        self.lane = _lane("drop-a.newlevel.media", 8902, access=True)
        self.specs = {self.lane.host: _access_spec(self.lane.host, ["a@x.sk"])}
        self.lanes = {("boxa", "u1"): self.lane}

    def test_reconcile_drop_lanes_refuses_in_worktree_zero_api_calls(self):
        dns_c, dns_t = _dns(records=[])
        acc_c, acc_t = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c,
            is_worktree_fn=lambda: True, out=out)
        # falsy / non-zero result, no lane touched
        self.assertFalse(all_ok)
        self.assertEqual(results, [])
        self.assertEqual(live, {})
        # ZERO Cloudflare API calls made
        self.assertEqual(dns_t.calls, [])
        self.assertEqual(acc_t.calls, [])
        # loud line naming the incident + the reused #972 predicate
        text = out.getvalue()
        self.assertIn("REFUSING", text)
        self.assertIn("worktree", text)
        self.assertIn("#1115", text)
        self.assertIn("#972", text)

    def test_reconcile_drop_lanes_proceeds_when_not_worktree(self):
        dns_c, dns_t = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c,
            is_worktree_fn=lambda: False, out=out)
        self.assertTrue(all_ok)
        self.assertEqual(live, {self.lane.host: self.lane.port})
        self.assertIn("POST", dns_t.methods())          # the CNAME was created
        self.assertNotIn("REFUSING", out.getvalue())

    def test_reconcile_drop_lanes_dry_run_never_refuses(self):
        # a dry-run makes no live write, so the worktree guard must NOT fire even
        # in a worktree — the CI/test harness runs dry-runs from any checkout.
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, _live = gl.reconcile_drop_lanes(
            dry_run=True, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c,
            is_worktree_fn=lambda: True, out=out)
        self.assertNotIn("REFUSING", out.getvalue())
        self.assertEqual({r["host"] for r in results}, {self.lane.host})

    def test_access_apply_helper_refuses_real_client_in_worktree(self):
        # reconcile_access_for_lane is a live path reachable WITHOUT
        # reconcile_drop_lanes: with access_client=None it would build a REAL
        # AccessClient from ~/.secrets and apply_profile live. In a worktree it
        # must refuse BEFORE reading any token — patch _load_token to blow up so
        # any read is a test failure.
        with mock.patch.object(acc, "_load_token",
                               side_effect=AssertionError("token was read")):
            ok, action, msg = gl.reconcile_access_for_lane(
                self.lane, dry_run=False, access_client=None,
                access_specs=self.specs, is_worktree_fn=lambda: True)
        self.assertFalse(ok)
        self.assertEqual(action, "worktree-refused")
        self.assertIn("#1115", msg)
        self.assertIn("#972", msg)

    def test_reconcile_drop_lanes_refuses_before_any_secret_read(self):
        # review R2: with NO injected clients the guard must still refuse BEFORE
        # building a real client / reading ~/.secrets. Patch both token loaders to
        # blow up — any read is a test failure.
        import cli_cloudflare_dns as dns
        with mock.patch.object(dns, "_load_token",
                               side_effect=AssertionError("DNS token read")), \
             mock.patch.object(acc, "_load_token",
                               side_effect=AssertionError("Access token read")):
            all_ok, results, live = gl.reconcile_drop_lanes(
                dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
                dns_client=None, access_client=None,
                is_worktree_fn=lambda: True, out=io.StringIO())
        self.assertFalse(all_ok)
        self.assertEqual(results, [])
        self.assertEqual(live, {})

    def test_access_apply_helper_injected_client_not_guarded(self):
        # an INJECTED (fake) client makes no live write, so the worktree guard
        # is scoped to the real-client build and must NOT fire — the seam guards
        # who WRITES live, not every call.
        acc_c, acc_t = _acc(apps=[])
        ok, action, _msg = gl.reconcile_access_for_lane(
            self.lane, dry_run=False, access_client=acc_c,
            access_specs=self.specs, is_worktree_fn=lambda: True)
        self.assertTrue(ok)
        self.assertEqual(action, "ok")
        self.assertTrue(acc_t.calls)                    # the fake WAS exercised


# --------------------------------------------------------------------------- #
# (b) controller ingress routes ONLY go-live-eligible lanes
# --------------------------------------------------------------------------- #

class TestControllerIngressEligibility(unittest.TestCase):
    def setUp(self):
        # eligible access lane (has spec), token-only lane (always eligible),
        # PENDING access lane (access=True, NO spec) -> must emit no rule.
        self.elig = _lane("drop-e.newlevel.media", 8902, access=True,
                          filedrop_port=8801)
        self.tok = _lane("drop-t.newlevel.media", 8903, access=False,
                         filedrop_port=8802)
        self.pending = _lane("drop-p.newlevel.media", 8904, access=True,
                             filedrop_port=8803)
        self.lanes = {
            ("boxe", "u1"): self.elig,
            ("boxt", "u2"): self.tok,
            ("boxp", "u3"): self.pending,
        }
        self.specs = {self.elig.host: _access_spec(self.elig.host, ["a@x.sk"])}

    def test_pending_access_lane_gets_no_ingress_rule(self):
        rules = dl.drop_ingress_rules_for_controller(
            self.lanes, cache={}, access_specs=self.specs)
        hosts = [r[0] for r in rules]
        # NEITHER a /s/ rule NOR a drop rule for the PENDING host
        self.assertNotIn(self.pending.host, hosts)
        # eligible + token-only hosts ARE routed
        self.assertIn(self.elig.host, hosts)
        self.assertIn(self.tok.host, hosts)

    def test_eligible_lanes_byte_identical_regardless_of_pending(self):
        with_pending = dl.drop_ingress_rules_for_controller(
            self.lanes, cache={}, access_specs=self.specs)
        without_pending = dl.drop_ingress_rules_for_controller(
            {("boxe", "u1"): self.elig, ("boxt", "u2"): self.tok},
            cache={}, access_specs=self.specs)
        # dropping the pending lane leaves every other rule byte-identical
        self.assertEqual(with_pending, without_pending)

    def test_pending_host_absent_from_every_rule_element(self):
        rules = dl.drop_ingress_rules_for_controller(
            self.lanes, cache={}, access_specs=self.specs)
        for rule in rules:
            self.assertNotIn(self.pending.host, rule)


class TestIngressGoLiveEligibilityLock(unittest.TestCase):
    """The lock: EVERY hostname the REAL controller ingress emits must be
    eligible to go live — otherwise a routed host could serve the origin with no
    Access app in front (the incident class)."""

    def test_every_real_ingress_hostname_is_go_live_eligible(self):
        # Real-data INVARIANT (snapshot): every host the live registry routes is
        # eligible today. NOTE: this alone would hold even if the filter were
        # removed (slice D fills every real spec, so there are 0 pending lanes) —
        # the BEHAVIOURAL teeth are in
        # test_ingress_omits_an_ineligible_lane_injected_into_the_real_registry.
        rules = dg.drop_ingress_rules_for_controller(cache={})
        lane_by_host = {lane.host: lane
                        for (_n, _u), lane in dg.DROP_LANES.items()
                        if lane.topology == "controller" and lane.origin_host}
        self.assertTrue(rules, "expected at least one controller ingress rule")
        for rule in rules:
            host = rule[0]
            lane = lane_by_host.get(host)
            self.assertIsNotNone(lane, "ingress host %s has no lane" % host)
            self.assertTrue(
                dl._lane_go_live_eligible(lane, dg.DROP_ACCESS_APPS),
                "ingress routes %s but it is NOT go-live eligible" % host)

    def test_ingress_omits_an_ineligible_lane_injected_into_the_real_registry(self):
        # BEHAVIOURAL lock (review R1 FINDING 2 — the invariant above is a
        # tautology on today's all-eligible data): inject a synthetic
        # access-with-NO-spec lane into a COPY of the REAL DROP_LANES and render
        # via the REAL leaf with the REAL specs. It is controller-topology with an
        # origin, so WITHOUT the eligibility filter it WOULD be routed — the filter
        # must OMIT it. Removing the filter makes this test fail.
        lanes = dict(dg.DROP_LANES)
        pend = _lane("drop-pending-injected.newlevel.media", 8909, access=True)
        lanes[("injected-box", "injected-user")] = pend
        rules = dl.drop_ingress_rules_for_controller(
            lanes, cache={}, access_specs=dg.DROP_ACCESS_APPS)
        hosts = [r[0] for r in rules]
        self.assertNotIn(pend.host, hosts)
        # the omission is due to ELIGIBILITY, not topology:
        self.assertEqual(pend.topology, "controller")
        self.assertTrue(pend.origin_host)
        self.assertFalse(dl._lane_go_live_eligible(pend, dg.DROP_ACCESS_APPS))


class TestEligibilityPredicateShared(unittest.TestCase):
    def test_predicate_is_shared_not_copied(self):
        # ONE eligibility predicate: cli_drop_golive re-exports the leaf's, never
        # a copy (the design's "import it, never copy").
        self.assertIs(gl._lane_go_live_eligible, dl._lane_go_live_eligible)

    def test_predicate_semantics(self):
        tok = _lane("drop-x.newlevel.media", 8905, access=False)
        acc_no_spec = _lane("drop-y.newlevel.media", 8906, access=True)
        acc_spec = _lane("drop-z.newlevel.media", 8907, access=True)
        specs = {acc_spec.host: _access_spec(acc_spec.host, ["a@x.sk"])}
        self.assertTrue(dl._lane_go_live_eligible(tok, specs))
        self.assertFalse(dl._lane_go_live_eligible(acc_no_spec, specs))
        self.assertTrue(dl._lane_go_live_eligible(acc_spec, specs))


class TestGoliveLeafImportBothOrders(unittest.TestCase):
    """#1115 slice E (review R2 FINDING 1): the module-level re-export
    `from cli_drop_lanes import _lane_go_live_eligible` in cli_drop_golive must
    not create an import cycle — importing EITHER module first must succeed, and
    the predicate identity holds. A fresh subprocess per order (a stale
    sys.modules would mask a cycle)."""

    def _fresh(self, first):
        import subprocess
        code = ("import %s; "
                "import cli_drop_golive as g; import cli_drop_lanes as l; "
                "assert g._lane_go_live_eligible is l._lane_go_live_eligible, "
                "'predicate not shared'; print('OK')" % first)
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "import-%s-first failed: %s" % (first, r.stderr))
        self.assertIn("OK", r.stdout)

    def test_golive_first(self):
        self._fresh("cli_drop_golive")

    def test_leaf_first(self):
        self._fresh("cli_drop_lanes")

    def test_gateway_first(self):
        self._fresh("cli_drop_gateway")


if __name__ == "__main__":
    unittest.main()
