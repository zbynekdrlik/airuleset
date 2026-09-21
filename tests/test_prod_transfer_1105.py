"""Prod-transfer manifest for developer-supplied erp-test inputs (#1105, owner
TODO 21.9.2026).

On david1-4 the developer supplies credentials / data / config so a feature can
be verified on the stream's erp-test shadow box; at PROD deploy nobody knows what
must be carried over and how, so "it worked on erp-test, nothing worked on prod".
This locks a per-ticket `Prod-transfer:` manifest recorded at supply time, GATED
at hand-off (the composer/gk pre-flight), CHECKED OFF at deploy (a
`Prod-transfer-status:` line + a pending -> U/W label), and BLOCKING the client
acceptance message while any item is pending.

Every classifier here is a SHAPE check (bilingual token families), the same
contract as gates/spec.py / gates/navody.py -- never a proof of correctness.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gates.prod_transfer as pt  # noqa: E402


# Secret-shaped FIXTURES built at runtime so no secret-shaped literal sits in
# this file (the block-sensitive-staging entropy scanner correctly flags such a
# literal — the exact scanner this feature reuses). These are fabricated, inert.
def _fake_token():
    # A GitHub PAT-shaped prefix + >=20 alnum, assembled from <32-char pieces so
    # no secret-shaped literal exists in this source file.
    return "gh" + "p_" + "0123456789" + "abcdefghij" + "0123456789" + "abcd"


def _fake_blob():
    # A >=32-char high-entropy blob, assembled from <32-char pieces.
    return "aB3xK9pQ2vT4" + "wR8nL5jH3bF6" + "dS1aG0zCq7Y"


# --------------------------------------------------------------------------- #
# (a) SURFACE DETECTION -- the erp-test-only surfaces in a lane's diff/commands
# --------------------------------------------------------------------------- #
class TestSurfaceDetection(unittest.TestCase):
    def test_res_config_settings(self):
        s = pt.surfaces_in("+    self.env['res.config.settings'].set_values()")
        self.assertIn("res.config.settings", s)

    def test_ir_config_parameter(self):
        s = pt.surfaces_in("+ icp = env['ir.config_parameter'].sudo()")
        self.assertIn("ir.config_parameter", s)

    def test_secret_request_command(self):
        s = pt.surfaces_in("I ran: airuleset.py secret request --name stripe")
        self.assertIn("secret request", s)

    def test_set_param(self):
        s = pt.surfaces_in("+ icp.set_param('mymod.token', tok)")
        self.assertIn("set_param", s)

    def test_env_file(self):
        s = pt.surfaces_in("+API_BASE=https://x\n--- a/.env\n+++ b/.env")
        self.assertIn(".env", s)

    def test_webhook(self):
        s = pt.surfaces_in("+ webhook_url = 'https://hooks.example/x'")
        self.assertIn("webhook", s)

    def test_api_key_variants(self):
        self.assertIn("api_key", pt.surfaces_in("+ api_key field added"))
        self.assertIn("api_key", pt.surfaces_in("+ apiKey = get()"))
        self.assertIn("api_key", pt.surfaces_in("+ api-key: header"))

    def test_refresh_dev_box(self):
        s = pt.surfaces_in("REFRESH-DEV-BOX-FROM-PROD: david1")
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD", s)

    def test_no_surface_is_empty(self):
        self.assertEqual(pt.surfaces_in("+ def add(a, b):\n+     return a + b"), [])

    def test_env_does_not_match_environment(self):
        # `.environment` is not the `.env` file surface.
        self.assertNotIn(".env", pt.surfaces_in("+ self.environment = 'prod'"))

    def test_dedup_and_stable(self):
        txt = ("+ res.config.settings\n+ res.config.settings\n"
               "+ ir.config_parameter")
        s = pt.surfaces_in(txt)
        self.assertEqual(s.count("res.config.settings"), 1)
        self.assertIn("ir.config_parameter", s)

    def test_none_text_is_empty(self):
        self.assertEqual(pt.surfaces_in(None), [])

    def test_env_does_not_match_odoo_orm_env(self):
        # #1105 review F1: `.env` must NOT fire on the Odoo ORM attribute access
        # (`self.env[...]`, `request.env`, `process.env.X`) — that dominates
        # every Odoo diff and would false-flag nearly every hand-off.
        self.assertNotIn(".env", pt.surfaces_in("+ p = self.env['res.partner']"))
        self.assertNotIn(".env", pt.surfaces_in("+ u = request.env.user"))
        self.assertNotIn(".env", pt.surfaces_in("+ x = process.env.NODE"))
        self.assertNotIn(".env", pt.surfaces_in("+ self.env.cr.execute(q)"))

    def test_env_still_matches_the_dotenv_file(self):
        self.assertIn(".env", pt.surfaces_in("--- a/.env\n+++ b/.env"))
        self.assertIn(".env", pt.surfaces_in("+ cat .env >> config"))
        self.assertIn(".env", pt.surfaces_in("+ cp .env.local .env"))


# --------------------------------------------------------------------------- #
# (b) MANIFEST LINE PARSING -- Prod-transfer: <...> and the none escape
# --------------------------------------------------------------------------- #
class TestManifestParsing(unittest.TestCase):
    def test_real_manifest_line(self):
        body = ("blah\nProd-transfer: Stripe key — supplied by David 21.9.2026 "
                "— erp-test: ir.config_parameter stripe.key — prod path: "
                "vault→owner (secret show) — sensitivity: secret\nmore")
        lines = pt.manifest_lines(body)
        self.assertEqual(len(lines), 1)
        self.assertIn("Stripe key", lines[0])

    def test_none_escape_with_reason(self):
        ok, reason = pt.has_manifest_none(
            "Prod-transfer: none — test-only feature flag")
        self.assertTrue(ok)
        self.assertIn("test-only", reason)

    def test_bare_none_is_not_an_escape(self):
        ok, _ = pt.has_manifest_none("Prod-transfer: none")
        self.assertFalse(ok)

    def test_glued_hyphen_none_is_not_an_escape(self):
        # #1105 review F5: `none-critical config` must NOT read as "nothing to
        # transfer" — only an em/en-dash or a SPACED hyphen escapes.
        ok, _ = pt.has_manifest_none("Prod-transfer: none-critical config X")
        self.assertFalse(ok)
        # A real, spaced-hyphen escape still works.
        ok2, r2 = pt.has_manifest_none("Prod-transfer: none - test-only flag")
        self.assertTrue(ok2)
        self.assertIn("test-only", r2)

    def test_none_line_is_not_a_real_manifest_line(self):
        self.assertEqual(pt.manifest_lines("Prod-transfer: none — nothing"), [])

    def test_status_line_is_not_a_manifest_line(self):
        # Prod-transfer-status: must not be picked up as a Prod-transfer: line.
        self.assertEqual(
            pt.manifest_lines("Prod-transfer-status: Stripe key — transferred"), [])

    def test_multiple_manifest_lines(self):
        body = ("Prod-transfer: A — prod path: gk config step — sensitivity: config\n"
                "Prod-transfer: B — prod path: developer manual step — sensitivity: data\n")
        self.assertEqual(len(pt.manifest_lines(body)), 2)


# --------------------------------------------------------------------------- #
# (c) SECRET-VALUE GUARD -- a manifest line carrying a secret VALUE
# --------------------------------------------------------------------------- #
class TestSecretValueGuard(unittest.TestCase):
    def test_token_prefix_value_flagged(self):
        line = ("Prod-transfer: token %s — sensitivity: secret" % _fake_token())
        self.assertIsNotNone(pt.line_has_secret_value(line))

    def test_high_entropy_blob_flagged(self):
        line = ("Prod-transfer: key %s — sensitivity: secret" % _fake_blob())
        self.assertIsNotNone(pt.line_has_secret_value(line))

    def test_clean_name_and_path_not_flagged(self):
        line = ("Prod-transfer: Stripe API key — supplied by David 21.9.2026 — "
                "erp-test: ir.config_parameter stripe.key — prod path: "
                "vault→owner (secret show) — sensitivity: secret")
        self.assertIsNone(pt.line_has_secret_value(line))

    def test_placeholder_not_flagged(self):
        line = "Prod-transfer: token YOUR_TOKEN_HERE — sensitivity: secret"
        self.assertIsNone(pt.line_has_secret_value(line))


# --------------------------------------------------------------------------- #
# (d) MANIFEST PRE-FLIGHT -- the composer/gk hand-off gate
# --------------------------------------------------------------------------- #
class TestManifestPreflight(unittest.TestCase):
    RFR_NO_MANIFEST = "READY-FOR-REVIEW: branch x\nself-review table..."
    RFR_WITH_MANIFEST = (
        "READY-FOR-REVIEW: branch x\n"
        "Prod-transfer: Stripe key — supplied by David 21.9.2026 — prod path: "
        "vault→owner (secret show) — sensitivity: secret\n")
    SCAN_SURFACE = ("+ self.env['res.config.settings']\n"
                    "$ airuleset.py secret request --name stripe")

    def test_surface_without_manifest_blocks_naming_surfaces(self):
        ok, reason = pt.manifest_preflight(self.RFR_NO_MANIFEST, self.SCAN_SURFACE)
        self.assertFalse(ok)
        self.assertIn("res.config.settings", reason)
        self.assertIn("secret request", reason)
        self.assertIn("Prod-transfer", reason)

    def test_surface_with_manifest_passes(self):
        ok, reason = pt.manifest_preflight(self.RFR_WITH_MANIFEST, self.SCAN_SURFACE)
        self.assertTrue(ok)

    def test_none_escape_passes(self):
        ok, _ = pt.manifest_preflight(
            "READY-FOR-REVIEW: branch x\nProd-transfer: none — test-only feature flag",
            self.SCAN_SURFACE)
        self.assertTrue(ok)

    def test_no_surface_passes_byte_identical(self):
        ok, reason = pt.manifest_preflight(self.RFR_NO_MANIFEST,
                                           "+ def add(a, b):\n+     return a + b")
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_undeterminable_scan_fails_open(self):
        ok, reason = pt.manifest_preflight(self.RFR_NO_MANIFEST, None)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_manifest_with_secret_value_blocks(self):
        body = ("READY-FOR-REVIEW: branch x\n"
                "Prod-transfer: token %s — sensitivity: secret" % _fake_token())
        ok, reason = pt.manifest_preflight(body, self.SCAN_SURFACE)
        self.assertFalse(ok)
        self.assertIn("secret", reason.lower())


# --------------------------------------------------------------------------- #
# (e) STATUS CHECK-OFF + pending -> label (item 3)
# --------------------------------------------------------------------------- #
class TestStatusCheckoff(unittest.TestCase):
    def test_parse_status_states(self):
        body = ("Prod-transfer-status: Stripe key — owner-action pending\n"
                "Prod-transfer-status: seed data — transferred\n"
                "Prod-transfer-status: webhook — developer step pending\n"
                "Prod-transfer-status: flag — n/a\n")
        states = dict(pt.status_lines(body))
        self.assertEqual(states["Stripe key"], "owner-action pending")
        self.assertEqual(states["seed data"], "transferred")
        self.assertEqual(states["webhook"], "developer step pending")
        self.assertEqual(states["flag"], "n/a")

    def test_pending_items_only(self):
        body = ("Prod-transfer-status: Stripe key — owner-action pending\n"
                "Prod-transfer-status: seed data — transferred\n"
                "Prod-transfer-status: webhook — developer step pending\n")
        pend = pt.pending_items(body)
        whats = {w for w, _ in pend}
        self.assertEqual(whats, {"Stripe key", "webhook"})

    def test_label_for_pending(self):
        self.assertEqual(pt.label_for_pending("owner-action pending"),
                         "needs-owner-action")
        self.assertEqual(pt.label_for_pending("developer step pending"),
                         "ops-wait")
        self.assertIsNone(pt.label_for_pending("transferred"))
        self.assertIsNone(pt.label_for_pending("n/a"))


# --------------------------------------------------------------------------- #
# (f) ACCEPTANCE BLOCK (item 4)
# --------------------------------------------------------------------------- #
class TestAcceptanceBlock(unittest.TestCase):
    def test_pending_status_blocks(self):
        body = ("Prod-transfer: Stripe key — sensitivity: secret\n"
                "Prod-transfer-status: Stripe key — owner-action pending\n")
        blocked, item = pt.acceptance_block(body)
        self.assertTrue(blocked)
        self.assertIn("Stripe key", item)

    def test_all_resolved_not_blocked(self):
        body = ("Prod-transfer: Stripe key — sensitivity: secret\n"
                "Prod-transfer-status: Stripe key — transferred\n")
        blocked, _ = pt.acceptance_block(body)
        self.assertFalse(blocked)

    def test_manifest_with_no_status_is_pending(self):
        # A real manifest item never checked off = not yet transferred = pending.
        body = "Prod-transfer: Stripe key — sensitivity: secret\n"
        blocked, _ = pt.acceptance_block(body)
        self.assertTrue(blocked)

    def test_none_escape_not_blocked(self):
        body = "Prod-transfer: none — test-only feature flag\n"
        blocked, _ = pt.acceptance_block(body)
        self.assertFalse(blocked)

    def test_no_manifest_not_blocked(self):
        blocked, _ = pt.acceptance_block("just a normal ticket body")
        self.assertFalse(blocked)

    def test_duplicate_resolved_does_not_unblock_a_different_item(self):
        # #1105 review E1: two items, one checked off TWICE, the other never ->
        # count would say resolved(2) >= real(2); identity matching must BLOCK
        # on the never-checked item (the "worked on erp-test, nothing on prod"
        # hole this feature closes).
        body = ("Prod-transfer: Stripe key — sensitivity: secret\n"
                "Prod-transfer: Webhook URL — sensitivity: config\n"
                "Prod-transfer-status: Stripe key — transferred\n"
                "Prod-transfer-status: Stripe key — transferred\n")
        blocked, item = pt.acceptance_block(body)
        self.assertTrue(blocked)
        self.assertIn("Webhook URL", item)

    def test_misnamed_status_does_not_unblock(self):
        # #1105 review E2: a status naming a DIFFERENT item than the manifest
        # must not satisfy it.
        body = ("Prod-transfer: Stripe key — sensitivity: secret\n"
                "Prod-transfer-status: seed data — transferred\n")
        blocked, item = pt.acceptance_block(body)
        self.assertTrue(blocked)
        self.assertIn("Stripe key", item)

    def test_each_item_resolved_by_name_not_blocked(self):
        body = ("Prod-transfer: Stripe key — sensitivity: secret\n"
                "Prod-transfer: Webhook URL — sensitivity: config\n"
                "Prod-transfer-status: Stripe key — transferred\n"
                "Prod-transfer-status: Webhook URL — n/a\n")
        blocked, _ = pt.acceptance_block(body)
        self.assertFalse(blocked)


# --------------------------------------------------------------------------- #
# (g) lane_scan_text -- the production git-diff seam (fail-open)
# --------------------------------------------------------------------------- #
class TestLaneScanText(unittest.TestCase):
    def test_added_lines_extracted(self):
        def run(argv):
            if argv[:3] == ["git", "symbolic-ref", "--quiet"]:
                return subprocess.CompletedProcess(argv, 0, "origin/main\n", "")
            if argv[:2] == ["git", "diff"]:
                patch = ("diff --git a/x.py b/x.py\n"
                         "--- a/x.py\n+++ b/x.py\n"
                         "+ res.config.settings write\n"
                         "- removed old line\n"
                         " context line\n")
                return subprocess.CompletedProcess(argv, 0, patch, "")
            return subprocess.CompletedProcess(argv, 1, "", "")
        txt = pt.lane_scan_text(cwd="/x", run=run)
        self.assertIn("res.config.settings", txt)
        self.assertNotIn("removed old line", txt)
        self.assertNotIn("context line", txt)

    def test_undeterminable_returns_none(self):
        def run(argv):
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        self.assertIsNone(pt.lane_scan_text(cwd="/x", run=run))


# --------------------------------------------------------------------------- #
# (h) render + CLI -- prod-transfer add posts the exact line, refuses a secret
# --------------------------------------------------------------------------- #
class TestRenderAndCli(unittest.TestCase):
    def test_render_line_shape(self):
        line = pt.render_manifest_line(
            what="Stripe API key", who="David", date="21.9.2026",
            location="ir.config_parameter stripe.key",
            path="vault→owner (secret show)", sensitivity="secret")
        self.assertTrue(line.startswith("Prod-transfer: Stripe API key"))
        self.assertIn("supplied by David 21.9.2026", line)
        self.assertIn("prod path: vault→owner (secret show)", line)
        self.assertIn("sensitivity: secret", line)

    def test_cli_add_posts_line_via_fake_runner(self):
        import cli_prod_transfer as cli

        posted = {}

        def runner(argv, body=None):
            posted["argv"] = argv
            posted["body"] = body
            return 0, "https://x/comment/1", ""

        class A:
            action = "add"
            issue = 1105
            what = "Stripe API key"
            who = "David"
            date = "21.9.2026"
            location = "ir.config_parameter stripe.key"
            path = "vault→owner (secret show)"
            sensitivity = "secret"
            repo = "zbynekdrlik/airuleset"
        rc = cli.cmd_prod_transfer(A(), runner=runner)
        self.assertEqual(rc, 0)
        self.assertIn("issue", posted["argv"])
        self.assertTrue(posted["body"].startswith("Prod-transfer: Stripe API key"))

    def test_cli_add_refuses_secret_value(self):
        import cli_prod_transfer as cli

        def runner(argv, body=None):
            raise AssertionError("must not post a secret value")

        class A:
            action = "add"
            issue = 1105
            what = "token %s" % _fake_token()
            who = "David"
            date = "21.9.2026"
            location = "x"
            path = "vault→owner (secret show)"
            sensitivity = "secret"
            repo = "zbynekdrlik/airuleset"
        rc = cli.cmd_prod_transfer(A(), runner=runner)
        self.assertEqual(rc, 1)

    def test_cli_add_default_date_is_portable(self):
        # #1105 review C: the default-date branch (no --date) must post a valid
        # D.M.YYYY line without the glibc-only strftime("%-d") extension.
        import datetime
        import cli_prod_transfer as cli

        posted = {}

        def runner(argv, body=None):
            posted["body"] = body
            return 0, "ok", ""

        class A:
            action = "add"
            issue = 1105
            what = "seed data"
            who = "David"
            date = None
            location = "res.partner rows"
            path = "data migration seed.py"
            sensitivity = "data"
            repo = "zbynekdrlik/airuleset"
        rc = cli.cmd_prod_transfer(A(), runner=runner)
        self.assertEqual(rc, 0)
        d = datetime.date.today()
        self.assertIn("%d.%d.%d" % (d.day, d.month, d.year), posted["body"])
        self.assertRegex(posted["body"], r"supplied by David \d+\.\d+\.\d{4}")
        # No unexpanded strftime directive leaked in.
        self.assertNotIn("%-", posted["body"])

    def test_cli_add_rejects_unknown_sensitivity(self):
        import cli_prod_transfer as cli

        def runner(argv, body=None):
            raise AssertionError("must not post an invalid sensitivity")

        class A:
            action = "add"
            issue = 1105
            what = "x"
            who = "David"
            date = "21.9.2026"
            location = "y"
            path = "gk config step"
            sensitivity = "banana"
            repo = "zbynekdrlik/airuleset"
        rc = cli.cmd_prod_transfer(A(), runner=runner)
        self.assertEqual(rc, 1)


# --------------------------------------------------------------------------- #
# (i) airuleset composer wrapper _handoff_prod_transfer_preflight
# --------------------------------------------------------------------------- #
class TestHandoffProdTransferPreflight(unittest.TestCase):
    SCAN = ("+ self.env['res.config.settings']\n"
            "$ airuleset.py secret request --name stripe")

    def test_reduced_authority_blocks_without_manifest(self):
        import airuleset
        blk = airuleset._handoff_prod_transfer_preflight(
            "READY-FOR-REVIEW: branch x", cwd="/repo",
            scan_text=self.SCAN, authority="fork-no-merge")
        self.assertIsNotNone(blk)
        self.assertIn("res.config.settings", blk)

    def test_reduced_authority_passes_with_manifest(self):
        import airuleset
        body = ("READY-FOR-REVIEW: branch x\n"
                "Prod-transfer: Stripe key — prod path: vault→owner (secret show) "
                "— sensitivity: secret")
        blk = airuleset._handoff_prod_transfer_preflight(
            body, cwd="/repo", scan_text=self.SCAN, authority="fork-no-merge")
        self.assertIsNone(blk)

    def test_full_authority_passes(self):
        import airuleset
        blk = airuleset._handoff_prod_transfer_preflight(
            "READY-FOR-REVIEW: branch x", cwd="/repo",
            scan_text=self.SCAN, authority="full")
        self.assertIsNone(blk)

    def test_no_surface_passes(self):
        import airuleset
        blk = airuleset._handoff_prod_transfer_preflight(
            "READY-FOR-REVIEW: branch x", cwd="/repo",
            scan_text="+ def add(a, b):\n+   return a + b",
            authority="fork-no-merge")
        self.assertIsNone(blk)

    def test_fail_open_on_undeterminable_scan(self):
        import airuleset
        blk = airuleset._handoff_prod_transfer_preflight(
            "READY-FOR-REVIEW: branch x", cwd="/repo",
            scan_text=None, authority="fork-no-merge")
        self.assertIsNone(blk)


# --------------------------------------------------------------------------- #
# (j) COMPOSER WIRING -- both hand-off call sites invoke the pre-flight
# --------------------------------------------------------------------------- #
class TestComposerWiring(unittest.TestCase):
    SRC = (ROOT / "airuleset.py").read_text(encoding="utf-8")

    def test_preflight_defined(self):
        self.assertIn("def _handoff_prod_transfer_preflight", self.SRC)

    def _func_body(self, name):
        # Slice a top-level function body: from `def <name>(` to the next
        # top-level `\ndef ` (or EOF). Teeth against removing ONE call site
        # (#1105 review F4): a bare count>=2 passed even with one call removed
        # (1 def + 1 call), so assert the call is INSIDE each function.
        i = self.SRC.find("\ndef %s(" % name)
        assert i != -1, "function %s not found" % name
        j = self.SRC.find("\ndef ", i + 1)
        return self.SRC[i:j if j != -1 else len(self.SRC)]

    def test_wired_at_both_call_sites(self):
        # Both cmd_handoff (compose) and _cmd_handoff_post_body_file
        # (pass-through) must call the pre-flight, like the guide/spec siblings.
        self.assertIn("_handoff_prod_transfer_preflight(",
                      self._func_body("cmd_handoff"))
        self.assertIn("_handoff_prod_transfer_preflight(",
                      self._func_body("_cmd_handoff_post_body_file"))
        # 1 def + 2 calls: a bare count would pass with a call removed.
        self.assertGreaterEqual(
            self.SRC.count("_handoff_prod_transfer_preflight("), 3)

    def test_subcommand_wired(self):
        self.assertIn('"prod-transfer"', self.SRC)
        self.assertIn("cmd_prod_transfer", self.SRC)


# --------------------------------------------------------------------------- #
# (k) DOCTRINE CONTENT LOCKS (window teeth) -- process-subdev + handover-compose
# --------------------------------------------------------------------------- #
class TestDoctrineLocks(unittest.TestCase):
    PROCESS = (ROOT / "skills/process-subdev/SKILL.md").read_text(encoding="utf-8")
    HANDOVER = (ROOT / "skills/odoo-client-messaging/handover-compose.md").read_text(
        encoding="utf-8")

    def test_process_subdev_has_prod_transfer_review_lens(self):
        # The review frame gains a Prod-transfer lens tying erp-test parity to prod.
        self.assertIn("Prod-transfer", self.PROCESS)
        self.assertIn("erp-test", self.PROCESS)

    def test_process_subdev_has_deploy_checkoff_and_labels(self):
        self.assertIn("Prod-transfer-status:", self.PROCESS)
        self.assertIn("needs-owner-action", self.PROCESS)
        self.assertIn("ops-wait", self.PROCESS)

    def test_handover_compose_has_acceptance_block_bullet(self):
        self.assertIn("Prod-transfer", self.HANDOVER)
        # The acceptance message must not go out while an item is pending.
        self.assertRegex(self.HANDOVER, r"Prod-transfer[\s\S]{0,400}pending")


if __name__ == "__main__":
    unittest.main()
