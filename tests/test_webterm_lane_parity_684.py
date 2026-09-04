"""#684 — webterm parity: owner-dashboard changes must reach david/marek lanes.

Owner requirement (2026-08-25, from the #661 acceptance round): "pravdaze vyvoj
co robim pre zbynek sa musi uplatnit aj na david a marek." — every owner-dashboard
improvement must land on david.newlevel.media + marek.newlevel.media too, by a
MECHANISM, not a one-off.

Empirically (STEP-0 on #684) the regeneration is ALREADY part of the deploy path:
`push` runs `install` under each lane account (david1@subdev / marek@subdev, both in
REMOTE_HOSTS) -> `maybe_setup_webterm()` -> `cli_webterm_lane.setup_service()` ->
`write_artifacts()` REGENERATES the lane `dash_index` from the LIVE
`render_dashboard_html()` + restarts the units; the gateway also reads `dash_index`
from disk per request. Live proof: the david1 lane index.html carried 5 `.ord`
badges and marek's 1 (from #582) before this PR — i.e. the shared render already
reaches both lanes by this path, so #661's `.ord` removal reaches them the same way.

These tests LOCK that invariant so a future refactor cannot silently break the
propagation (smallest honest mechanism: a regression lock, not a redundant new
re-render step). They also lock the SECURITY boundary: parity is VISUAL/UX only —
a lane render never enables the owner-only U-status poll (#677 `--u-collect`).
"""
import contextlib
import sys
import tempfile
import types
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w              # noqa: E402
import cli_webterm_lane as lane      # noqa: E402
import cli_webterm_pwa as pwa        # noqa: E402


def _inv(ids):
    """Minimal fleet-style inventory entries (the fields _tab_sessions reads)."""
    return [{"id": i, "label": i, "kind": "owner", "local": False,
             "host": "10.0.0.1", "user": "u"} for i in ids]


class TestLaneRegeneratesFromLiveRender(unittest.TestCase):
    """The load-bearing propagation fact: `write_artifacts` (the deploy-path step
    that runs under each lane account on every push) writes the lane `dash_index`
    from the LIVE `render_dashboard_html`, so any change to the shared render (e.g.
    #661's `.ord` removal) lands on the lane on the next push — never a cached/
    hardcoded HTML blob frozen at provision time."""

    def test_dash_index_is_written_from_live_render_dashboard_html(self):
        sentinel = "<!-- SENTINEL-684-LIVE-RENDER -->\n"
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
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
            # Patch the heavy neighbours so ONLY the dash_index write path runs.
            st.enter_context(m.patch.object(w, "CLAUDE_DIR", base / ".claude"))
            st.enter_context(m.patch.object(
                w, "webterm_inventory", return_value=_inv(["david1", "david2"])))
            rd = st.enter_context(m.patch.object(
                w, "render_dashboard_html", return_value=sentinel))
            st.enter_context(m.patch.object(
                w, "render_webterm_launch_script", return_value="#!/bin/sh\n"))
            st.enter_context(m.patch.object(pwa, "write_pwa_assets"))
            st.enter_context(m.patch.object(lane, "render_ttyd_unit", return_value="[unit]\n"))
            st.enter_context(m.patch.object(lane, "render_gateway_unit", return_value="[unit]\n"))

            lane.write_artifacts(spec)

            written = (base / "dash" / "index.html").read_text(encoding="utf-8")
            self.assertEqual(written, sentinel)          # regenerated from live render
            rd.assert_called_once()                       # exactly one live render call
            # A lane WITHOUT a `dashboard_human` (david — its scoped inventory
            # ids differ from the policy dict's fleet ids) renders UNFILTERED:
            # the write omits `human` ENTIRELY (default None). (#661 rework: a
            # lane that DOES declare one consumes the policy — the test below.)
            _args, kwargs = rd.call_args
            self.assertNotIn("human", kwargs)

    def test_dashboard_human_lane_renders_through_the_domain_policy(self):
        # #661 rework: marek's spec declares dashboard_human="marek", so his
        # lane dash is rendered through the owner-defined per-domain tab list
        # (order + exclusivity), while the connect allowlist (inventory JSON)
        # stays his full physically-scoped set.
        sentinel = "<!-- SENTINEL-661-HUMAN-RENDER -->\n"
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            base = Path(tmp)
            spec = types.SimpleNamespace(
                dash_dir=base / "dash",
                dash_index=base / "dash" / "index.html",
                profile="marek",
                inventory_path=base / "inv.json",
                launch_path=base / "launch.sh",
                ttyd_sock_basename="webterm-marek-ttyd.sock",
                ttyd_service_dest=base / "sys" / "ttyd.service",
                gateway_service_dest=base / "sys" / "gateway.service",
                retire_credential_path=None,
                dashboard_human="marek",
            )
            st.enter_context(m.patch.object(w, "CLAUDE_DIR", base / ".claude"))
            st.enter_context(m.patch.object(
                w, "webterm_inventory", return_value=_inv(["marek-subdev"])))
            rd = st.enter_context(m.patch.object(
                w, "render_dashboard_html", return_value=sentinel))
            st.enter_context(m.patch.object(
                w, "render_webterm_launch_script", return_value="#!/bin/sh\n"))
            st.enter_context(m.patch.object(pwa, "write_pwa_assets"))
            st.enter_context(m.patch.object(lane, "render_ttyd_unit", return_value="[unit]\n"))
            st.enter_context(m.patch.object(lane, "render_gateway_unit", return_value="[unit]\n"))

            lane.write_artifacts(spec)

            rd.assert_called_once()
            _args, kwargs = rd.call_args
            self.assertEqual(kwargs.get("human"), "marek")


class TestSetupServiceRegeneratesBeforeRestart(unittest.TestCase):
    """The deploy path re-renders (`write_artifacts`) BEFORE it restarts the units,
    so a push serves current HTML: the order, not just the presence, is locked."""

    def test_write_artifacts_runs_before_any_service_restart(self):
        events = []
        spec = types.SimpleNamespace(
            log_prefix="webterm(test)",
            ttyd_service_name="webterm-x-ttyd.service",
            gateway_service_name="webterm-x-gateway.service",
            go_live="(go-live steps)",
            gateway_sock_basename="webterm-x-gateway.sock",
            ttyd_sock_basename="webterm-x-ttyd.sock",
        )

        def _run(cmd, *a, **k):
            events.append("run:" + " ".join(cmd))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        def _systemctl(argv):
            events.append("systemctl:" + argv[0])
            return 0, "", ""

        import cli_filedrop_watchdog as fw
        import cli_binary_installers as binstall
        with m.patch.object(binstall, "ensure_ttyd_static_binary"), \
                m.patch.object(fw, "_run_systemctl", _systemctl), \
                m.patch.object(fw, "_whoami", lambda: "x"):
            ok = lane.setup_service(
                spec, run=_run,
                prereq_fn=lambda: (True, "ready"),
                write_artifacts_fn=lambda: events.append("write_artifacts"),
                tunnel_fn=lambda run=None: events.append("tunnel"))

        self.assertTrue(ok)
        self.assertIn("write_artifacts", events)
        restarts = [i for i, e in enumerate(events) if e == "systemctl:restart"]
        self.assertTrue(restarts, "the ready path must restart the lane units")
        self.assertLess(events.index("write_artifacts"), restarts[0],
                        "re-render must happen BEFORE the first service restart")


class TestOwnerAndLaneRenderParityNoOrd(unittest.TestCase):
    """The shared render is the parity mechanism: the #661 `.ord` removal is visible
    on BOTH the owner render (human=zbynek) and the lane render (human=None), while
    the green ▸ separator (shared UX) survives on both — non-vacuously (each has a
    real tab)."""

    def test_neither_owner_nor_lane_render_carries_ord_badge(self):
        owner = w.render_dashboard_html(
            _inv(["dev1"]), ttyd_base="/t", human=w.WEBTERM_LOGIN_USER)
        lane_html = w.render_dashboard_html(
            _inv(["david1", "david2"]), ttyd_base="/t", human=None)
        # non-vacuous: each render actually produced tabs
        self.assertIn('class="ico"', owner)
        self.assertIn('class="ico"', lane_html)
        # #661 parity: the badge is gone on BOTH domains
        self.assertNotIn('class="ord"', owner)
        self.assertNotIn('class="ord"', lane_html)


class TestLaneNeverEnablesOwnerUStatus(unittest.TestCase):
    """Parity is VISUAL/UX ONLY — the U-status poll (#677) is OWNER-ONLY: a lane
    render must never turn it on (that would spawn a cross-tenant ssh collector as
    the sub-dev account). Locked here so a parity change can never leak it."""

    def test_u_status_true_only_for_owner_login_user(self):
        owner = w.render_dashboard_html(
            _inv(["dev1"]), ttyd_base="/t", human=w.WEBTERM_LOGIN_USER)
        david_lane = w.render_dashboard_html(_inv(["david1"]), ttyd_base="/t", human=None)
        marek_lane = w.render_dashboard_html(_inv(["marek-subdev"]), ttyd_base="/t",
                                             human="marek")
        # #867: the dominika lane behaves exactly like david/marek — a direct render
        # (no lane_u_status) never turns on the owner U-status poll.
        dominika_lane = w.render_dashboard_html(_inv(["montalu5-subdev"]),
                                                ttyd_base="/t", human="dominika")
        self.assertIn('"u_status": true', owner)
        self.assertIn('"u_status": false', david_lane)
        self.assertIn('"u_status": false', marek_lane)
        self.assertIn('"u_status": false', dominika_lane)


if __name__ == "__main__":
    unittest.main()
