"""Tests for the webterm Cloudflare Access email-OTP gate (#612, cli_webterm_access.py).

All network is faked via an injectable transport (AccessClient(transport=...)),
so nothing here ever hits the real Cloudflare API — the apply logic (create-vs-
update decision, deny-by-default empty-allowlist refusal, the one-line "add a
person" property, dry-run makes no writes) is exercised purely offline.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm_access as acc  # noqa: E402


class _FakeTransport:
    """Records (method, path) + bodies and returns canned responses. The Allow
    policy is now INLINE in the app POST/PUT, so there is no separate policy
    endpoint. `apps` seeds the GET /apps?... result. `fail_write` makes any
    POST/PUT to /apps return a 4xx (to exercise the loud-failure path)."""

    def __init__(self, apps=None, fail_write=False):
        self.calls = []
        self.bodies = []
        self._apps = apps or []
        self._fail_write = fail_write

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        self.bodies.append(body)
        base = path.split("?", 1)[0]
        if method == "GET" and base.endswith("/apps"):
            return 200, {"success": True, "result": self._apps}
        if method == "POST" and base.endswith("/apps"):
            if self._fail_write:
                return 422, {"success": False,
                             "errors": [{"message": "invalid app payload"}]}
            return 201, {"success": True, "result": {"id": "new-app-id",
                                                     "domain": (body or {}).get("domain")}}
        if method == "PUT" and "/apps/" in base:
            if self._fail_write:
                return 400, {"success": False,
                             "errors": [{"message": "invalid update"}]}
            return 200, {"success": True, "result": {"id": base.rsplit("/", 1)[-1]}}
        return 200, {"success": True, "result": {}}

    def methods(self):
        return [m for (m, _p) in self.calls]

    def body_for(self, method, path_contains):
        for (m, p), b in zip(self.calls, self.bodies):
            if m == method and path_contains in p:
                return b
        return None


DAVID_SPEC = {
    "hostname": "david.newlevel.media",
    "name": "webterm — david",
    "allowed_emails": ["david@example.com"],
    "session_duration": "24h",
}


class TestPayloadBuilders(unittest.TestCase):
    def test_app_payload_is_self_hosted_with_otp_login_page(self):
        p = acc.build_app_payload(DAVID_SPEC)
        self.assertEqual(p["domain"], "david.newlevel.media")
        self.assertEqual(p["type"], "self_hosted")
        # allowed_idps=[] + auto_redirect_to_identity=False => the Access login
        # PAGE (email One-Time PIN input) is shown, not an IdP jump.
        self.assertEqual(p["allowed_idps"], [])
        self.assertIs(p["auto_redirect_to_identity"], False)
        # The Allow policy is INLINE (atomic app+policy, no deny-all window).
        self.assertEqual(len(p["policies"]), 1)
        self.assertEqual(p["policies"][0]["decision"], "allow")
        self.assertEqual(p["policies"][0]["include"],
                         [{"email": {"email": "david@example.com"}}])

    def test_policy_includes_exactly_the_declared_emails(self):
        p = acc.build_policy_payload(DAVID_SPEC)
        self.assertEqual(p["decision"], "allow")
        self.assertEqual(p["include"], [{"email": {"email": "david@example.com"}}])

    def test_adding_a_person_is_one_more_include_entry(self):
        # The owner's explicit requirement: adding marek is a ONE-LINE change to
        # allowed_emails. Prove the policy include grows by exactly that entry.
        one = acc.build_policy_payload(DAVID_SPEC)
        two_spec = dict(DAVID_SPEC,
                        allowed_emails=["david@example.com", "marek@example.com"])
        two = acc.build_policy_payload(two_spec)
        self.assertEqual(len(two["include"]) - len(one["include"]), 1)
        self.assertIn({"email": {"email": "marek@example.com"}}, two["include"])


class TestApplyIdempotent(unittest.TestCase):
    def _client(self, apps=None, fail_write=False):
        t = _FakeTransport(apps=apps, fail_write=fail_write)
        return acc.AccessClient("acct", token="tok", transport=t), t

    def test_creates_app_when_absent(self):
        client, t = self._client(apps=[])       # no app for the domain yet
        res = acc.apply_profile(client, DAVID_SPEC, dry_run=False)
        self.assertTrue(res["ok"], res["error"])
        # A create (POST /apps), never an update.
        self.assertIn("POST", t.methods())
        self.assertTrue(any("create app" in a for a in res["actions"]))

    def test_updates_app_when_present_never_duplicates(self):
        existing = [{"id": "existing-id", "domain": "david.newlevel.media"}]
        client, t = self._client(apps=existing)
        res = acc.apply_profile(client, DAVID_SPEC, dry_run=False)
        self.assertTrue(res["ok"], res["error"])
        # Idempotent: an app for this domain already exists => PUT, not a second POST.
        self.assertIn(("PUT", "/accounts/acct/access/apps/existing-id"), t.calls)
        self.assertNotIn(("POST", "/accounts/acct/access/apps"), t.calls)

    def test_dry_run_makes_no_writes(self):
        client, t = self._client(apps=[])
        res = acc.apply_profile(client, DAVID_SPEC, dry_run=True)
        self.assertTrue(res["ok"])
        # Only the read (find app) — never a POST/PUT.
        self.assertEqual(set(t.methods()), {"GET"})

    def test_empty_allowlist_is_refused_no_open_door(self):
        # An Access app with an empty include is an open door — apply must REFUSE
        # and issue NO writes (fail-closed).
        client, t = self._client(apps=[])
        spec = dict(DAVID_SPEC, allowed_emails=[])
        res = acc.apply_profile(client, spec, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIn("empty", res["error"])
        self.assertEqual(t.calls, [])            # not a single API call issued

    def test_update_carries_the_policy_inline_no_policy_endpoint(self):
        existing_app = [{"id": "app1", "domain": "david.newlevel.media"}]
        client, t = self._client(apps=existing_app)
        res = acc.apply_profile(client, DAVID_SPEC, dry_run=False)
        self.assertTrue(res["ok"], res["error"])
        # A single PUT to the app carries the policy inline; NO separate
        # (legacy) app-scoped /policies endpoint is ever called.
        self.assertIn(("PUT", "/accounts/acct/access/apps/app1"), t.calls)
        self.assertFalse(any("/policies" in p for (_m, p) in t.calls))
        put_body = t.body_for("PUT", "/apps/app1")
        self.assertEqual(put_body["policies"][0]["include"],
                         [{"email": {"email": "david@example.com"}}])

    def test_write_failure_surfaces_loud(self):
        # 🔵-4: a non-2xx on the app write must fail LOUD (ok=False + error),
        # never a silent partial apply.
        client, t = self._client(apps=[], fail_write=True)
        res = acc.apply_profile(client, DAVID_SPEC, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIsNotNone(res["error"])
        self.assertIn("create app failed", res["error"])


class TestConfigAndTrustHeader(unittest.TestCase):
    def test_owner_side_is_now_a_declared_access_app(self):
        # #635 scope change (owner ROZHODNUTÉ 2026-08-22): the owner chose to move
        # zbynek.newlevel.media behind Cloudflare Access like David's side — so it
        # is NOW a declared Access app (reverses the pre-#635 "grey/tailnet-only,
        # not declared" state that this test used to assert).
        self.assertIn("david", acc.WEBTERM_ACCESS_APPS)
        self.assertIn("owner", acc.WEBTERM_ACCESS_APPS)
        owner = acc.WEBTERM_ACCESS_APPS["owner"]
        self.assertEqual(owner["hostname"], "zbynek.newlevel.media")
        # The single owner-provided allow-list entry (coordinator 2026-08-22).
        self.assertIn("drlik.zbynek@gmail.com", owner["allowed_emails"])

    def test_trust_header_is_the_cloudflare_identity_header(self):
        self.assertEqual(acc.WEBTERM_ACCESS_TRUST_HEADER,
                         "Cf-Access-Authenticated-User-Email")


class TestOwnerAccessApp(unittest.TestCase):
    """#635: the owner (zbynek.newlevel.media) Access app mirrors David's lane —
    a self-hosted email-OTP app whose allow-list holds exactly the owner, is
    extensible by one line (marek next), and refuses an empty list (open door)."""

    def _owner(self):
        return acc.WEBTERM_ACCESS_APPS["owner"]

    def test_owner_app_payload_is_self_hosted_email_otp_with_owner(self):
        p = acc.build_app_payload(self._owner())
        self.assertEqual(p["domain"], "zbynek.newlevel.media")
        self.assertEqual(p["type"], "self_hosted")
        self.assertEqual(p["allowed_idps"], [])            # email OTP login page
        self.assertIs(p["auto_redirect_to_identity"], False)
        self.assertEqual(len(p["policies"]), 1)
        self.assertEqual(p["policies"][0]["decision"], "allow")
        self.assertIn({"email": {"email": "drlik.zbynek@gmail.com"}},
                      p["policies"][0]["include"])

    def test_owner_allowlist_extensible_by_one_line(self):
        # Adding marek must be ONE more include entry, no redesign (#612 property
        # carried to the owner app per the coordinator).
        one = acc.build_policy_payload(self._owner())
        two_spec = dict(self._owner(),
                        allowed_emails=list(self._owner()["allowed_emails"])
                        + ["marek@example.com"])
        two = acc.build_policy_payload(two_spec)
        self.assertEqual(len(two["include"]) - len(one["include"]), 1)
        self.assertIn({"email": {"email": "marek@example.com"}}, two["include"])

    def test_owner_empty_allowlist_is_refused_no_open_door(self):
        t = _FakeTransport(apps=[])
        client = acc.AccessClient("acct", token="tok", transport=t)
        spec = dict(self._owner(), allowed_emails=[])
        res = acc.apply_profile(client, spec, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIn("empty", res["error"])
        self.assertEqual(t.calls, [])                       # fail-closed, no writes

    def test_owner_apply_creates_the_app_when_absent(self):
        t = _FakeTransport(apps=[])
        client = acc.AccessClient("acct", token="tok", transport=t)
        res = acc.apply_profile(client, self._owner(), dry_run=False)
        self.assertTrue(res["ok"], res["error"])
        self.assertIn("POST", t.methods())


class TestReviewFixes(unittest.TestCase):
    """#612 review round 1 fixes: normalized domain match (🔵#3), --dry-run wins
    over --apply (🔵#2)."""

    def test_norm_domain_casefold_and_trailing_slash(self):
        self.assertEqual(acc._norm_domain("David.Newlevel.Media/"),
                         "david.newlevel.media")
        self.assertEqual(acc._norm_domain(None), "")

    def test_find_app_matches_a_normalized_domain_no_duplicate(self):
        # Cloudflare echoing the domain in a different case / with a trailing
        # slash must still MATCH (update), never miss and POST a duplicate app.
        t = _FakeTransport(apps=[{"id": "a1", "domain": "David.Newlevel.Media/"}])
        client = acc.AccessClient("acct", token="tok", transport=t)
        app, _meta = client.find_app_by_domain("david.newlevel.media")
        self.assertIsNotNone(app)
        self.assertEqual(app["id"], "a1")

    def test_dry_run_flag_forces_dry_run_even_with_apply(self):
        import argparse
        import unittest.mock as m
        calls = []

        def rec(self, method, path, body):
            calls.append((method, path))
            if path.endswith("/apps"):
                return 200, {"success": True, "result": []}
            return 200, {"success": True, "result": {}}

        args = argparse.Namespace(apply=True, dry_run=True, profile="david")
        with m.patch.object(acc, "_load_token", return_value="tok"), \
                m.patch.dict(acc.WEBTERM_ACCESS_APPS["david"],
                             {"allowed_emails": ["x@y.z"]}), \
                m.patch.object(acc.AccessClient, "_http", rec):
            acc.cmd_webterm_access(args)
        # --dry-run wins: only GET(s), never a POST/PUT, despite --apply.
        self.assertTrue(calls)
        self.assertEqual({mth for (mth, _p) in calls}, {"GET"})


if __name__ == "__main__":
    unittest.main()
