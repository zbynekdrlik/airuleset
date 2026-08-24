"""#677: red dot on a dashboard tab whose target box has U > 0 (a question/approval
waiting on the OWNER — the statusline U bucket). Ground truth = each box's
tickets-status cache `user_waiting`; the gateway aggregates it (low-frequency ssh
pull) into ~/.claude/webterm-u-status.json, the dashboard polls /u-status and
toggles a per-tab dot. These tests lock the pure reader, the fleet collector, the
collect command, and the dashboard render (dot present + toggled on U>0 only).
The gateway /u-status route is tested in test_webterm_gateway.py.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock as m

import cli_webterm as w


class _Res:
    """A fake subprocess.run result."""
    def __init__(self, stdout="", rc=0, stderr=""):
        self.stdout = stdout
        self.returncode = rc
        self.stderr = stderr


class TestBoxUCount(unittest.TestCase):
    """_box_u_count sums the U bucket across a box's tickets-status caches — pure,
    no gh, no network. This is the value read locally on dev1 and, via an inline
    python mirror, on each remote box over ssh."""

    def _home(self, caches):
        d = tempfile.mkdtemp()
        ts = Path(d) / ".claude" / "tickets-status"
        ts.mkdir(parents=True)
        for i, c in enumerate(caches):
            (ts / ("%d.json" % i)).write_text(json.dumps(c), encoding="utf-8")
        return d

    def test_sums_user_waiting_across_caches(self):
        home = self._home([
            {"open": 5, "user_waiting": 2},
            {"open": 1, "user_waiting": 3},
            {"open": 0, "user_waiting": 0},
        ])
        self.assertEqual(w._box_u_count(home), 5)

    def test_missing_or_none_field_counts_zero(self):
        home = self._home([
            {"open": 5},                      # pre-#468 cache: no field at all
            {"user_waiting": None},           # explicit None
            {"user_waiting": 4},
        ])
        self.assertEqual(w._box_u_count(home), 4)

    def test_bad_json_is_skipped_not_fatal(self):
        home = self._home([{"user_waiting": 2}])
        (Path(home) / ".claude" / "tickets-status" / "bad.json").write_text(
            "{not json", encoding="utf-8")
        self.assertEqual(w._box_u_count(home), 2)

    def test_no_cache_dir_is_zero(self):
        self.assertEqual(w._box_u_count(tempfile.mkdtemp()), 0)


class TestCollectFleetU(unittest.TestCase):
    """collect_fleet_u reads U per box: local dev1 directly, each remote via ssh
    running the inline python reader. A box that errors / times out / returns a
    non-int is OMITTED (unknown != zero), never a false 0."""

    def _entries(self):
        return [
            {"id": "dev1", "local": True, "preferred": "zbynek"},
            {"id": "dev2", "local": False, "host": "10.0.0.2", "user": "newlevel",
             "identity": None},
            {"id": "gatekeeper", "local": False, "host": "10.0.0.9", "user": "gatekeeper",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]

    def test_local_direct_and_remote_ssh_reads(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            joined = " ".join(argv)
            uid = "7" if "10.0.0.2" in joined else "0"
            return _Res(stdout=uid + "\n", rc=0)

        with m.patch.object(w, "_box_u_count", return_value=3):
            u = w.collect_fleet_u(self._entries(), run=fake_run, max_workers=1)
        self.assertEqual(u["dev1"], 3)          # local reader (patched)
        self.assertEqual(u["dev2"], 7)          # remote ssh
        self.assertEqual(u["gatekeeper"], 0)    # remote ssh, zero U
        # the remote reads went through ssh, running the inline python reader.
        self.assertTrue(any(a and a[0] in ("ssh", "sshpass") for a in calls))
        self.assertTrue(any("python3 -c" in " ".join(a) for a in calls))

    def test_identity_entry_uses_ssh_i_no_identity_uses_sshpass(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(" ".join(argv))
            return _Res("0\n", 0)

        with m.patch.object(w, "_box_u_count", return_value=0):
            w.collect_fleet_u(self._entries(), run=fake_run, max_workers=1)
        gk = [s for s in seen if "10.0.0.9" in s][0]
        self.assertIn("-i", gk)
        self.assertIn("gatekeeper_access_ed25519", gk)
        dev2 = [s for s in seen if "10.0.0.2" in s][0]
        self.assertIn("sshpass", dev2)          # no identity -> sshpass path
        self.assertNotIn("gatekeeper_access", dev2)

    def test_read_is_non_interactive_with_a_short_timeout(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append((argv, kw))
            return _Res("0\n", 0)

        with m.patch.object(w, "_box_u_count", return_value=0):
            w.collect_fleet_u(self._entries(), run=fake_run, timeout_s=8, max_workers=1)
        remote = [(a, k) for a, k in seen if "10.0.0.2" in " ".join(a)][0]
        argv, kw = remote
        self.assertNotIn("-t", argv)            # NOT a PTY (non-interactive read)
        self.assertIn("BatchMode=yes", " ".join(argv))   # never prompt/hang
        self.assertEqual(kw.get("timeout"), 8)  # per-box timeout so a dead box can't stall

    def test_failed_or_nonint_box_is_omitted_never_false_zero(self):
        def fake_run(argv, **kw):
            if "10.0.0.2" in " ".join(argv):
                raise subprocess.TimeoutExpired("ssh", 8)
            return _Res("not-an-int\n", 0)

        with m.patch.object(w, "_box_u_count", return_value=1):
            u = w.collect_fleet_u(self._entries(), run=fake_run, max_workers=1)
        self.assertEqual(u["dev1"], 1)
        self.assertNotIn("dev2", u)             # timed out -> omitted (no dot), never 0
        self.assertNotIn("gatekeeper", u)       # non-int stdout -> omitted


class TestUReaderSnippet(unittest.TestCase):
    """The inline python reader the collector runs over ssh must compute the SAME
    sum as _box_u_count against a real tickets-status dir (no drift)."""

    def test_snippet_matches_box_u_count(self):
        d = tempfile.mkdtemp()
        ts = Path(d) / ".claude" / "tickets-status"
        ts.mkdir(parents=True)
        (ts / "a.json").write_text(json.dumps({"user_waiting": 2}), encoding="utf-8")
        (ts / "b.json").write_text(json.dumps({"user_waiting": 5}), encoding="utf-8")
        (ts / "c.json").write_text("{broken", encoding="utf-8")
        # run the real snippet with HOME pointed at the temp dir
        env = {"HOME": d, "PATH": __import__("os").environ.get("PATH", "")}
        r = subprocess.run(["python3", "-c", w._U_READER_SNIPPET],
                           capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(int(r.stdout.strip()), w._box_u_count(d))
        self.assertEqual(int(r.stdout.strip()), 7)


class TestCmdUCollect(unittest.TestCase):
    def test_writes_u_status_json_atomically(self):
        out = Path(tempfile.mkdtemp()) / "webterm-u-status.json"
        with m.patch.object(w, "WEBTERM_U_STATUS_PATH", out), \
             m.patch.object(w, "_owner_u_entries",
                            return_value=[{"id": "dev1", "local": True}]), \
             m.patch.object(w, "collect_fleet_u", return_value={"dev1": 4}):
            rc = w.cmd_webterm_u_collect([])
        self.assertEqual(rc, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["u"], {"dev1": 4})
        self.assertIsInstance(data["ts"], int)

    def test_main_dispatches_u_collect(self):
        with m.patch.object(w, "cmd_webterm_u_collect", return_value=0) as cm:
            rc = w.main(["webterm-u-collect"])
        self.assertEqual(rc, 0)
        cm.assert_called_once()


class TestDashboardDot(unittest.TestCase):
    def _inv(self):
        return [{"id": "s1", "label": "sess 1", "kind": "owner",
                 "local": False, "host": "10.0.0.1", "user": "u1"},
                {"id": "s2", "label": "sess 2", "kind": "owner",
                 "local": False, "host": "10.0.0.2", "user": "u2"}]

    def test_each_tab_carries_a_udot_element(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertEqual(html.count('class="udot"'), 2)   # one per tab

    def test_css_dot_shown_only_on_has_u_and_is_restrained(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn(".udot", html)
        self.assertIn(".tab.has-u .udot", html)           # dot shown only at U>0
        self.assertIn("border-radius: 50%", html)         # a small round badge

    def test_poll_fetches_u_status_and_toggles_on_u_gt_0(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("'/u-status'", html)                # polls the gateway route
        self.assertIn("function applyUStatus(", html)
        self.assertIn("function pollUStatus(", html)
        apply = re.search(r"function applyUStatus\(.*?\n\}", html, re.S).group(0)
        self.assertIn("has-u", apply)
        self.assertIn("> 0", apply)                       # dot iff U > 0
        # a poll is scheduled (burst + steady), not a one-shot
        self.assertIn("setInterval(pollUStatus", html)

    def test_dot_maps_tab_to_inventory_id(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        apply = re.search(r"function applyUStatus\(.*?\n\}", html, re.S).group(0)
        # maps the tab's session id to the u-status map (per-box keying)
        self.assertIn("CFG.sessions", apply)
        self.assertIn(".id", apply)


class TestUCollectOwnerGating(unittest.TestCase):
    """#677 review 🟡: the U-dot poll + collection are OWNER-ONLY. The david/marek
    gateways render the SAME dashboard (human=None / "marek") and reuse the SAME
    gateway module; without gating, a sub-dev gateway would spawn an owner-fleet
    collector AS the sub-dev account (a cross-tenant ssh read via the shared
    password). Gated two ways: the dashboard poll ACTIVATION on CFG.u_status
    (owner-render only), and the gateway feature off unless --u-collect (which only
    the owner gateway unit injects)."""

    def _dev1_inv(self):
        return [{"id": "dev1", "label": "dev1", "kind": "owner", "local": True,
                 "host": None, "user": None, "identity": None, "preferred": "zbynek"}]

    def _cfg(self, html):
        return json.loads(re.search(r"const CFG = (\{.*?\});", html).group(1))

    def test_owner_render_activates_poll_non_owner_does_not(self):
        owner = w.render_dashboard_html(self._dev1_inv(), ttyd_base="/t",
                                        human=w.WEBTERM_LOGIN_USER)
        nonowner = w.render_dashboard_html(self._dev1_inv(), ttyd_base="/t",
                                           human=None)              # david/marek path
        self.assertTrue(self._cfg(owner)["u_status"])               # owner: poll on
        self.assertFalse(self._cfg(nonowner)["u_status"])           # non-owner: poll off
        # the activation is GATED on the flag (never an unconditional pollUStatus()).
        self.assertIn("if (CFG.u_status)", owner)
        self.assertIn("if (CFG.u_status)", nonowner)

    def test_owner_gateway_unit_enables_u_collect_david_marek_do_not(self):
        import cli_webterm_david as dv
        import cli_webterm_marek as mk
        owner_unit = w._render_webterm_gateway_unit("127.0.0.1", access_mode=True)
        self.assertIn("--u-collect", owner_unit)                    # owner gateway: on
        self.assertNotIn("--u-collect", dv.render_david_gateway_unit())   # david: off
        self.assertNotIn("--u-collect", mk.render_marek_gateway_unit())   # marek: off

    def test_ssh_read_identity_decision_matches_interactive(self):
        # #677 review 🔵3: _ssh_read_prefix is a non-interactive variant, but its
        # identity-vs-sshpass DECISION must not drift from _ssh_interactive_prefix
        # (which the deploy-loop drift guard already covers).
        e_id = {"identity": "~/.secrets/gatekeeper_access_ed25519"}
        e_no = {"identity": None}
        self.assertIn("-i", w._ssh_read_prefix(e_id))
        self.assertEqual(w._ssh_read_prefix(e_id)[0], "ssh")
        self.assertEqual(w._ssh_read_prefix(e_no)[0], "sshpass")
        # same identity DECISION as the interactive prefix
        self.assertEqual("-i" in w._ssh_read_prefix(e_id),
                         "-i" in w._ssh_interactive_prefix(e_id))
        self.assertEqual("-i" in w._ssh_read_prefix(e_no),
                         "-i" in w._ssh_interactive_prefix(e_no))
        # ... but NON-interactive: no PTY, BatchMode on (fail fast, never prompt)
        self.assertNotIn("-t", w._ssh_read_prefix(e_no))
        self.assertIn("BatchMode=yes", w._ssh_read_prefix(e_no))


_USTATUS_APPLY_HARNESS = r"""
const CFG = { sessions: [{id:'dev1'},{id:'dev2'}] };
function fakeTab(idx){ const cls=new Set(); return {
  dataset:{idx:String(idx)},
  classList:{ toggle(n,on){ if(on)cls.add(n); else cls.delete(n); }, contains(n){return cls.has(n);} } }; }
const tabs=[fakeTab(0),fakeTab(1)];
const document={ querySelectorAll(){ return tabs; } };
%(apply)s
const out={};
applyUStatus({dev2:3});  out.gt0=[tabs[0].classList.contains('has-u'), tabs[1].classList.contains('has-u')];
applyUStatus({dev2:0});  out.zero=tabs[1].classList.contains('has-u');
applyUStatus({});        out.omit=tabs[1].classList.contains('has-u');
applyUStatus({dev2:5});  out.back=tabs[1].classList.contains('has-u');
process.stdout.write(JSON.stringify(out));
"""


class TestApplyUStatusBehaviour(unittest.TestCase):
    """#677 review 🔵4: EXECUTE applyUStatus (not just assert its source) to prove
    the dot toggles BOTH directions: shown at U>0, cleared at U=0 AND on an omitted
    box, and shown again when U returns."""

    def test_dot_toggles_both_directions(self):
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(
            [{"id": "dev2", "label": "dev2", "kind": "owner", "local": False,
              "host": "1", "user": "u"}], ttyd_base="/t")
        apply = re.search(r"function applyUStatus\(.*?\n\}", html, re.S).group(0)
        d = tempfile.mkdtemp()
        hp = Path(d) / "applyharness.js"
        hp.write_text(_USTATUS_APPLY_HARNESS % {"apply": apply}, encoding="utf-8")
        r = subprocess.run(["node", str(hp)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip())
        self.assertEqual(out["gt0"], [False, True])   # dev1 absent -> no dot; dev2 U>0 -> dot
        self.assertFalse(out["zero"])                 # U=0 -> cleared
        self.assertFalse(out["omit"])                 # omitted box -> cleared
        self.assertTrue(out["back"])                  # U>0 again -> re-shown


if __name__ == "__main__":
    unittest.main()
