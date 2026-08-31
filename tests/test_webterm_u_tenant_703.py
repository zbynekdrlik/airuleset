"""#703 — per-tenant U-dot for the david/marek lanes (owner ruling 2026-08-25).

Each lane gateway gets its OWN red U-dot data channel, scoped STRICTLY to that
lane's own tenant sessions — the #677/#684 cross-tenant boundary is untouched
(the owner-only `--u-collect` fleet collector stays owner-unit-only, and the
existing read-only gating locks in test_webterm_u_status.py /
test_webterm_lane_parity_684.py stay green unmodified).

RED-first invariants locked here:

  * the tenant COLLECTION set is the explicit `u_tenant` opt-in subset of the
    lane's own inventory (cli_webterm_profiles.u_tenant_entries) — never a
    shared/OWNER-account target (`newlevel@dev1/dev2` — its per-cwd
    tickets-status caches aggregate the OWNER's sessions, so a lane read there
    would be cross-tenant), and never an identity-less ssh entry (the sshpass
    shared-password branch must be unreachable from a lane collector);
  * the lane render polls /u-status ONLY via the new explicit `lane_u_status`
    opt-in — the default lane render keeps `"u_status": false`;
  * the lane gateway unit carries `--u-lane <profile>` and still NEVER
    `--u-collect`; the owner unit keeps `--u-collect` and never `--u-lane`;
  * the scoped collector (`webterm-u-collect --lane <profile>`) writes the
    per-lane u-status file with only tenant entries, through the SAME #686
    freshness-filtered readers, fail-closed on a bogus profile;
  * gateway wiring: --u-lane/--u-collect mutual exclusion, charset fail-close,
    scoped spawn argv, and a drift-lock between the standalone gateway's path
    formula and cli_webterm's;
  * the non-interactive U read honors a #680 `host_keys` pin (forestshop is a
    public-DNS box) instead of the TOFU `StrictHostKeyChecking=no` posture.
"""
import json
import re
import sys
import tempfile
import types
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w                  # noqa: E402
import cli_webterm_gateway as gw         # noqa: E402
import cli_webterm_lane as lane          # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
import cli_webterm_pwa as pwa            # noqa: E402
import cli_webterm_david as dv           # noqa: E402
import cli_webterm_marek as mk           # noqa: E402


def _inv(ids):
    """Minimal inventory entries (the fields _tab_sessions reads)."""
    return [{"id": i, "label": i, "kind": "owner", "local": False,
             "host": "10.0.0.1", "user": "u"} for i in ids]


class TestUTenantSets703(unittest.TestCase):
    """The per-tenant collection set is the profiles leaf's explicit opt-in —
    exactly the lane's OWN accounts, never a shared/owner-account target."""

    def test_david_set_is_his_own_accounts_only(self):
        ids = {e["id"] for e in profiles.u_tenant_entries(profiles.DAVID)}
        self.assertEqual(ids, set(profiles.DAVID_ACCOUNTS))
        # codex-bridge = newlevel@dev2 (the OWNER's account) — cross-tenant.
        self.assertNotIn(profiles.CODEX_ID, ids)

    def test_marek_set_excludes_owner_account_boxes(self):
        # #787: montalu2-subdev joined marek's u_tenant set alongside montalu4.
        ids = {e["id"] for e in profiles.u_tenant_entries(profiles.MAREK)}
        self.assertEqual(ids, {profiles.MAREK_ID, "montalu2-subdev",
                               "montalu4-subdev", profiles.MAREK_FORESTSHOP_ID})
        # dev1/dev2 = newlevel@ (the OWNER's account) — cross-tenant.
        self.assertNotIn("dev1", ids)
        self.assertNotIn("dev2", ids)

    def test_no_owner_account_entry_is_ever_u_tenant(self):
        # CROSS-TENANT REFUSAL: an inventory entry whose TARGET account is the
        # owner's (`newlevel`) must never be marked u_tenant — its
        # ~/.claude/tickets-status caches aggregate the OWNER's sessions.
        for profile in (profiles.DAVID, profiles.MAREK):
            for e in profiles.profile_inventory(profile, []):
                if e.get("user") == "newlevel":
                    self.assertIsNot(e.get("u_tenant"), True, e["id"])

    def test_fail_closed_for_owner_and_unknown_profiles(self):
        # The owner profile has NO u_tenant set (its collector is the separate
        # owner-only --u-collect path); an unknown lane collects nothing.
        self.assertEqual(profiles.u_tenant_entries(profiles.OWNER), [])
        self.assertEqual(profiles.u_tenant_entries("no-such-lane"), [])

    def test_identityless_nonlocal_entry_is_dropped_never_sshpass(self):
        # Defense in depth: even a (future, mis-edited) u_tenant entry WITHOUT
        # an explicit identity is DROPPED — the lane collector must never reach
        # _ssh_read_prefix's sshpass shared-password branch.
        bad = [{"id": "x", "local": False, "host": "127.0.0.1", "user": "x",
                "identity": None, "u_tenant": True}]
        with m.patch.object(profiles, "profile_inventory", return_value=bad):
            self.assertEqual(profiles.u_tenant_entries("david"), [])

    def test_every_collected_entry_is_local_or_explicit_identity(self):
        for profile in (profiles.DAVID, profiles.MAREK):
            entries = profiles.u_tenant_entries(profile)
            self.assertTrue(entries)                       # non-vacuous
            for e in entries:
                self.assertTrue(e.get("local") or e.get("identity"), e["id"])


class TestLaneRenderScopedPoll703(unittest.TestCase):
    """The lane dashboard polls /u-status ONLY via the explicit `lane_u_status`
    opt-in; the default lane render keeps the #677 `"u_status": false` lock."""

    def test_lane_render_with_flag_polls(self):
        html = w.render_dashboard_html(_inv(["david1"]), ttyd_base="/t",
                                       human=None, lane_u_status=True)
        self.assertIn('"u_status": true', html)

    def test_marek_policy_render_with_flag_polls(self):
        html = w.render_dashboard_html(_inv(["marek-subdev"]), ttyd_base="/t",
                                       human="marek", lane_u_status=True)
        self.assertIn('"u_status": true', html)

    def test_default_lane_render_still_off(self):
        html = w.render_dashboard_html(_inv(["david1"]), ttyd_base="/t",
                                       human=None)
        self.assertIn('"u_status": false', html)

    def test_owner_render_unchanged(self):
        html = w.render_dashboard_html(_inv(["dev1"]), ttyd_base="/t",
                                       human=w.WEBTERM_LOGIN_USER)
        self.assertIn('"u_status": true', html)


class TestLaneArtifactsScopedU703(unittest.TestCase):
    """Deploy parity (#684): both lanes get the scoped U-dot AUTOMATICALLY —
    write_artifacts enables the poll in the rendered dash, and the lane gateway
    unit carries the scoped `--u-lane` flag (never the owner `--u-collect`)."""

    def test_write_artifacts_enables_the_scoped_poll(self):
        with tempfile.TemporaryDirectory() as tmp, \
                m.patch.object(w, "CLAUDE_DIR", Path(tmp) / ".claude"), \
                m.patch.object(w, "webterm_inventory",
                               return_value=_inv(["david1"])), \
                m.patch.object(w, "render_dashboard_html",
                               return_value="<!-- x -->\n") as rd, \
                m.patch.object(w, "render_webterm_launch_script",
                               return_value="#!/bin/sh\n"), \
                m.patch.object(pwa, "write_pwa_assets"), \
                m.patch.object(lane, "render_ttyd_unit", return_value="[u]\n"), \
                m.patch.object(lane, "render_gateway_unit", return_value="[u]\n"):
            base = Path(tmp)
            spec = types.SimpleNamespace(
                dash_dir=base / "dash",
                dash_index=base / "dash" / "index.html",
                profile="david",
                inventory_path=base / "inv.json",
                launch_path=base / "launch.sh",
                ttyd_sock_basename="webterm-david-ttyd.sock",
                ttyd_service_dest=base / "sys" / "ttyd.service",
                gateway_service_dest=base / "sys" / "gateway.service",
                retire_credential_path=None,
            )
            lane.write_artifacts(spec)
            _args, kwargs = rd.call_args
            self.assertIs(kwargs.get("lane_u_status"), True)

    def test_lane_gateway_units_carry_u_lane_and_never_u_collect(self):
        d_unit = dv.render_david_gateway_unit()
        m_unit = mk.render_marek_gateway_unit()
        d_exec = next(ln for ln in d_unit.splitlines()
                      if ln.startswith("ExecStart="))
        m_exec = next(ln for ln in m_unit.splitlines()
                      if ln.startswith("ExecStart="))
        self.assertIn("--u-lane david", d_exec)
        self.assertIn("--u-lane marek", m_exec)
        # the owner-only cross-tenant flag stays OUT of every lane unit
        self.assertNotIn("--u-collect", d_unit)
        self.assertNotIn("--u-collect", m_unit)

    def test_owner_unit_still_u_collect_never_u_lane(self):
        unit = w._render_webterm_gateway_unit("127.0.0.1", access_mode=True)
        self.assertIn("--u-collect", unit)
        self.assertNotIn("--u-lane", unit)


class TestScopedCollector703(unittest.TestCase):
    """`webterm-u-collect --lane <profile>`: tenant entries only, per-lane
    output file, fail-closed on a bogus profile, owner path byte-compatible."""

    def test_scoped_collect_writes_per_lane_file_with_only_tenant_ids(self):
        with tempfile.TemporaryDirectory() as tmp, \
                m.patch.object(w, "CLAUDE_DIR", Path(tmp)), \
                m.patch.object(w, "collect_fleet_u",
                               return_value={"david1": 2}) as cf:
            rc = w.cmd_webterm_u_collect(["--lane", "david"])
            self.assertEqual(rc, 0)
            entries = cf.call_args[0][0]
            self.assertEqual(
                {e["id"] for e in entries},
                {e["id"] for e in profiles.u_tenant_entries("david")})
            data = json.loads((Path(tmp) / "webterm-david-u-status.json")
                              .read_text(encoding="utf-8"))
            self.assertEqual(data["u"], {"david1": 2})
            self.assertIn("ts", data)

    def test_scoped_collect_never_contacts_a_non_tenant_target(self):
        # End-to-end through the REAL collect_fleet_u with a recording `run`:
        # no owner-account target is ever contacted and the sshpass
        # shared-password branch never fires from the lane path.
        calls = []

        def fake_run(argv, **k):
            calls.append([str(a) for a in argv])
            return types.SimpleNamespace(returncode=0, stdout="0\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
                m.patch.object(w, "CLAUDE_DIR", Path(tmp)), \
                m.patch.object(w, "_box_u_count", return_value=0), \
                m.patch.object(w.subprocess, "run", fake_run):
            # patch _box_u_count so marek's `local` entry does NOT read the test
            # runner's real ~/.claude/tickets-status (hermetic; assertion below
            # is about ssh targets, which a local entry never contacts anyway).
            rc = w.cmd_webterm_u_collect(["--lane", "marek"])
        self.assertEqual(rc, 0)
        self.assertTrue(calls)                              # non-vacuous
        for argv in calls:
            joined = " ".join(argv)
            self.assertNotIn("newlevel@", joined)           # owner accounts
            self.assertNotIn("sshpass", joined)             # shared password

    def test_bogus_lane_profile_fails_closed_writes_nothing(self):
        # A path-traversal-shaped profile must never produce a file anywhere.
        # (collect_fleet_u + the owner output path are patched too, so even a
        # not-yet-gated implementation can never do real fleet IO from a test.)
        with tempfile.TemporaryDirectory() as tmp, \
                m.patch.object(w, "CLAUDE_DIR", Path(tmp)), \
                m.patch.object(w, "WEBTERM_U_STATUS_PATH",
                               Path(tmp) / "owner.json"), \
                m.patch.object(w, "collect_fleet_u", return_value={}):
            rc = w.cmd_webterm_u_collect(["--lane", "../evil"])
            self.assertEqual(rc, 0)
            self.assertEqual(list(Path(tmp).rglob("*")), [])

    def test_owner_collect_path_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "u.json"
            with m.patch.object(w, "WEBTERM_U_STATUS_PATH", target), \
                    m.patch.object(w, "_owner_u_entries",
                                   return_value=[]) as oe, \
                    m.patch.object(w, "collect_fleet_u", return_value={}):
                rc = w.cmd_webterm_u_collect([])
            self.assertEqual(rc, 0)
            oe.assert_called_once()
            self.assertTrue(target.exists())


class TestGatewayLaneWiring703(unittest.TestCase):
    """The standalone gateway's #703 lane mode: scoped path + scoped spawn,
    mutually exclusive with the owner `--u-collect`, charset fail-closed."""

    def test_u_status_config_lane_mode(self):
        args = types.SimpleNamespace(u_collect=False, u_lane="marek")
        path, spawn = gw._u_status_config(args)
        self.assertTrue(str(path).endswith("/.claude/webterm-marek-u-status.json"))
        self.assertIsNotNone(spawn)                 # the SCOPED spawner

    def test_u_status_config_owner_and_off_modes(self):
        on = types.SimpleNamespace(u_collect=True, u_lane=None)
        path, spawn = gw._u_status_config(on)
        self.assertTrue(str(path).endswith("/.claude/webterm-u-status.json"))
        self.assertIsNone(spawn)                    # Gateway default owner spawn
        off = types.SimpleNamespace(u_collect=False, u_lane=None)
        self.assertEqual(gw._u_status_config(off), (None, None))

    def test_lane_spawn_argv_is_the_scoped_collector(self):
        rec = {}

        def fake_popen(argv, **k):
            rec["argv"] = [str(a) for a in argv]
            return types.SimpleNamespace()

        with m.patch.object(gw.subprocess, "Popen", fake_popen):
            gw._lane_u_collect_spawn("marek")()
        self.assertEqual(rec["argv"][-3:], ["webterm-u-collect", "--lane", "marek"])
        self.assertTrue(rec["argv"][1].endswith("cli_webterm.py"))

    def test_mutual_exclusion_and_charset_fail_closed(self):
        base = ["--socket", "/tmp/x.sock", "--dash-index", "/tmp/i.html",
                "--trust-access-header", "H", "--ttyd-socket", "/tmp/t.sock"]
        with self.assertRaises(SystemExit):
            gw.main(base + ["--u-collect", "--u-lane", "marek"])
        # "_x" is a real value argparse ACCEPTS, so it hits the charset gate's
        # first-char rule inside main() (not just argparse's own "-x" rejection).
        for bad in ("../evil", "Marek", "a/b", "", "-x", "_x"):
            with self.assertRaises(SystemExit):
                gw.main(base + ["--u-lane", bad])

    def test_valid_lane_profile_reaches_serve(self):
        # Positive control (provenance): a VALID --u-lane passes ALL of main()'s
        # validation and reaches asyncio.run(_main_async(...)) -- proving the
        # fail-closed SystemExits above reject BAD values specifically, not every
        # --u-lane. Without this, an over-eager guard rejecting everything would
        # pass the fail-closed test vacuously.
        base = ["--socket", "/tmp/x.sock", "--dash-index", "/tmp/i.html",
                "--trust-access-header", "H", "--ttyd-socket", "/tmp/t.sock"]
        served = []

        def fake_run(coro):
            served.append(1)
            coro.close()                     # never awaited -> close cleanly

        with m.patch.object(gw.asyncio, "run", fake_run):
            rc = gw.main(base + ["--u-lane", "marek"])
        self.assertEqual(served, [1])        # reached serve, no SystemExit
        self.assertEqual(rc, 0)

    def test_lane_path_drift_lock_between_gateway_and_cli(self):
        # The standalone gateway duplicates the path formula (it deliberately
        # imports no cli_webterm — the _default_u_status_path precedent); this
        # lock ties the two copies together (#663 lesson: tie copies with a
        # test, never re-derive).
        # Hermetic: the gateway side reads Path.home() at CALL time while the
        # cli side bakes CLAUDE_DIR at IMPORT time -- pin the gateway's home
        # to the cli's import-time root so the lock compares the FORMULAS,
        # not whatever HOME a previous test module left behind.
        with m.patch.object(gw.Path, "home", return_value=w.CLAUDE_DIR.parent):
            g = gw._lane_u_status_path("x")
        c = w.webterm_lane_u_status_path("x")
        self.assertEqual(g, c)              # strictly stronger: ties the FULL
        #                                     path incl. the home-root derivation
        self.assertEqual(g.name, c.name)
        self.assertEqual(g.parent.name, ".claude")
        self.assertEqual(c.parent.name, ".claude")

    def test_gateway_serves_scoped_file_and_spawns_scoped_collector(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "webterm-marek-u-status.json"
            spawned = []
            g = gw.Gateway(dash_index=str(Path(tmp) / "i.html"), cred_path=None,
                           ttyd_host="127.0.0.1", ttyd_port=1, base_path="/t",
                           origins=[], trust_access_header="H",
                           u_status_path=str(p),
                           u_collect_spawn=lambda: spawned.append(1))
            body = g._u_status_body()   # absent file -> empty map + one spawn
            self.assertEqual(json.loads(body), {"u": {}, "ts": 0})
            self.assertEqual(spawned, [1])
            p.write_text(json.dumps({"u": {"montalu4-subdev": 3}, "ts": 1}),
                         encoding="utf-8")
            body = g._u_status_body()   # fresh file -> served, no second spawn
            self.assertEqual(json.loads(body)["u"], {"montalu4-subdev": 3})
            self.assertEqual(spawned, [1])


class TestReadPrefixHostKeys703(unittest.TestCase):
    """The non-interactive U read honors a #680 `host_keys` pin (forestshop is
    public-DNS): strict verification, never the TOFU =no posture there."""

    def test_pinned_entry_uses_strict_pin_never_tofu(self):
        entry = {"identity": "~/.secrets/webterm_marek_ed25519",
                 "host": profiles.MAREK_FORESTSHOP_HOST,
                 "host_keys": profiles.MAREK_FORESTSHOP_HOST_KEYS}
        joined = " ".join(w._ssh_read_prefix(entry))
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertNotIn("StrictHostKeyChecking=no", joined)
        self.assertIn("BatchMode=yes", joined)      # still the read shape
        self.assertIn("ConnectTimeout=5", joined)

    def test_unpinned_entry_prefix_unchanged(self):
        joined = " ".join(w._ssh_read_prefix({"identity": "~/.x"}))
        self.assertIn("StrictHostKeyChecking=no", joined)
        self.assertIn("UserKnownHostsFile=/dev/null", joined)

    def test_empty_pin_omits_one_box_never_kills_the_whole_map(self):
        # host_key_check_opts RAISES on a present-but-empty pin (#669
        # fail-closed); that must omit ONE box, not error the whole collect.
        entries = [
            {"id": "bad", "local": False, "host": "h1", "user": "u",
             "identity": "~/.x", "host_keys": []},
            {"id": "good", "local": False, "host": "h2", "user": "u",
             "identity": "~/.x"},
        ]

        def fake_run(argv, **k):
            return types.SimpleNamespace(returncode=0, stdout="4\n", stderr="")

        out = w.collect_fleet_u(entries, run=fake_run)
        self.assertEqual(out, {"good": 4})   # bad omitted, good still read


_RE_CFG = re.compile(r"const CFG = (\{.*?\});")


class TestScopedPollCfgShape703(unittest.TestCase):
    """The cfg flag stays a BOOLEAN (poll on/off) — scoping is enforced where
    the data is READ (collector entry set) and SERVED (per-lane path), never in
    client JS."""

    def test_lane_cfg_flag_is_a_json_boolean(self):
        html = w.render_dashboard_html(_inv(["david1"]), ttyd_base="/t",
                                       human=None, lane_u_status=True)
        cfg = json.loads(_RE_CFG.search(html).group(1))
        self.assertIs(cfg["u_status"], True)


if __name__ == "__main__":
    unittest.main()
