"""#1062 Lane L2 — the per-box MODEL BACKEND switch (cli_model_backend.py + the
five surface wire-ins + the per-target deploy shipper).

Every test is hermetic: markers/registries live under a tmp home or an injected
path; the apiKeyHelper script writes into a tmp dir; the deploy shipper is driven
with an INJECTED `run` + an INJECTED master-key source + an INJECTED registry
path, so NO real box, NO real `~/.secrets`, and NO real `~/.claude` marker is
ever written or read (the live miva1 cutover is the supervisor's — UNVERIFIED by
this lane). The no-marker byte-identity of the settings renderer is locked here.
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_model_backend as mb  # noqa: E402

MARKER = {
    "base_url": "http://100.101.214.103:4000",
    "key_file": "~/.secrets/model-gateway.key",
    "main": "pilot-main",
    "sub": "pilot-sub",
    "fast": "pilot-fast",
}


class TestLeafDiscipline(unittest.TestCase):
    def test_leaf_imports_without_pulling_in_airuleset(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; import cli_model_backend; "
             "assert 'airuleset' not in sys.modules, 'leaf pulled in airuleset'; "
             "print('ok')"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)

    def test_no_module_level_import_airuleset(self):
        for line in (REPO / "cli_model_backend.py").read_text().splitlines():
            self.assertNotEqual(line.strip(), "import airuleset",
                                "leaf has a MODULE-LEVEL `import airuleset`")

    def test_airuleset_reexports_the_facade(self):
        import airuleset
        self.assertIs(airuleset.cmd_model_backend, mb.cmd_model_backend)
        self.assertIs(airuleset.maybe_setup_model_backend,
                      mb.maybe_setup_model_backend)


class TestMarker(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertTrue(mb._valid_marker(MARKER))
        self.assertFalse(mb._valid_marker({"base_url": "x"}))
        self.assertFalse(mb._valid_marker({**MARKER, "main": ""}))
        self.assertFalse(mb._valid_marker({**MARKER, "sub": 3}))
        self.assertFalse(mb._valid_marker("not a dict"))

    def test_load_absent_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mb.load_marker(home=d))

    def test_load_roundtrip_via_home(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".claude" / "airuleset-model-backend.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(MARKER))
            self.assertEqual(mb.load_marker(home=d), MARKER)

    def test_load_incomplete_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".claude" / "airuleset-model-backend.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"base_url": "x", "main": "y"}))
            self.assertIsNone(mb.load_marker(home=d),
                              "an incomplete marker must fail to no-backend")

    def test_load_malformed_json_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".claude" / "airuleset-model-backend.json"
            p.parent.mkdir(parents=True)
            p.write_text("{not json")
            self.assertIsNone(mb.load_marker(home=d))


class TestBackendEnv(unittest.TestCase):
    def test_env_shape(self):
        env = mb.backend_env(MARKER)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], MARKER["base_url"])
        self.assertEqual(env["ANTHROPIC_MODEL"], "pilot-main")
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "pilot-main")
        self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL"], "pilot-sub")
        self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "pilot-fast")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"], "1")
        self.assertEqual(env["API_TIMEOUT_MS"], "900000")
        # every value is a string (settings.json env must be str→str)
        self.assertTrue(all(isinstance(v, str) for v in env.values()))

    def test_env_keys_match_the_owned_set(self):
        # BACKEND_ENV_KEYS must be exactly the keys backend_env emits (so the
        # no-marker pop removes precisely what a marker adds). SUBAGENT_MODEL is
        # deliberately excluded (fleet-wide default; overridden not owned).
        self.assertEqual(set(mb.backend_env(MARKER)), set(mb.BACKEND_ENV_KEYS))
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", mb.BACKEND_ENV_KEYS)

    def test_launcher_model_marker_vs_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mb.launcher_model("claude-fable-5-1[1m]", home=d),
                             "claude-fable-5-1[1m]")
            p = Path(d) / ".claude" / "airuleset-model-backend.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(MARKER))
            self.assertEqual(mb.launcher_model("claude-fable-5-1[1m]", home=d),
                             "pilot-main")


class TestApiKeyHelper(unittest.TestCase):
    def test_script_content(self):
        s = mb.apikey_helper_script("~/.secrets/model-gateway.key")
        self.assertTrue(s.startswith("#!/bin/sh\n"))
        self.assertIn("exec cat -- ", s)
        self.assertIn("umask 077", s)
        # absolute, ~-expanded, shell-quoted path — never a literal ~ Claude
        # Code's shell might not expand.
        self.assertNotIn("~/.secrets", s)
        self.assertIn(os.path.expanduser("~/.secrets/model-gateway.key"), s)
        # NEVER a token literal — only a `cat <path>`.
        self.assertNotIn("sk-", s)

    def test_script_quotes_a_spaced_path(self):
        s = mb.apikey_helper_script("/tmp/a b/key")
        self.assertIn("'/tmp/a b/key'", s)

    def test_maybe_setup_writes_executable_script(self):
        with tempfile.TemporaryDirectory() as d:
            out = mb.maybe_setup_model_backend(home=d, marker=MARKER)
            dest = Path(d) / ".claude" / "airuleset-model-gateway-apikey.sh"
            self.assertTrue(dest.exists())
            mode = stat.S_IMODE(dest.stat().st_mode)
            self.assertTrue(mode & stat.S_IXUSR, "helper must be executable")
            self.assertIn("apiKeyHelper script installed", out)
            self.assertIn(os.path.expanduser(MARKER["key_file"]),
                          dest.read_text())

    def test_maybe_setup_removes_stale_on_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / ".claude" / "airuleset-model-gateway-apikey.sh"
            dest.parent.mkdir(parents=True)
            dest.write_text("#!/bin/sh\nexec cat -- /old\n")
            out = mb.maybe_setup_model_backend(home=d, marker=None)
            self.assertFalse(dest.exists(), "stale helper must be removed")
            self.assertIn("removed stale", out)

    def test_maybe_setup_no_marker_no_script_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            out = mb.maybe_setup_model_backend(home=d, marker=None)
            self.assertIn("no marker", out)
            self.assertFalse(
                (Path(d) / ".claude" / "airuleset-model-gateway-apikey.sh").exists())


class TestRegistry(unittest.TestCase):
    def test_set_defaults_and_clear_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "reg.json"
            entry = mb.set_target("miva1@subdev", path=str(reg))
            self.assertEqual(entry["main"], "pilot-main")
            self.assertEqual(entry["sub"], "pilot-sub")
            self.assertEqual(entry["fast"], "pilot-fast")
            self.assertEqual(entry["key_file"], mb.TARGET_KEY_FILE)
            self.assertTrue(entry["base_url"].endswith(":4000"))
            self.assertIn("miva1@subdev", mb.load_registry(path=str(reg)))
            self.assertTrue(mb.clear_target("miva1@subdev", path=str(reg)))
            self.assertNotIn("miva1@subdev", mb.load_registry(path=str(reg)))
            self.assertFalse(mb.clear_target("miva1@subdev", path=str(reg)))

    def test_set_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "reg.json"
            entry = mb.set_target("miva1@subdev", base_url="http://x:4000",
                                  main="deepseek-main", sub="deepseek-sub",
                                  fast="deepseek-fast", key_file="~/.k",
                                  path=str(reg))
            self.assertEqual(entry["main"], "deepseek-main")
            self.assertEqual(entry["base_url"], "http://x:4000")
            self.assertEqual(entry["key_file"], "~/.k")

    def test_set_rejects_bad_target(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mb.ModelBackendError):
                mb.set_target("miva1", path=str(Path(d) / "reg.json"))

    def test_marker_from_entry_and_json(self):
        j = mb.marker_json(MARKER)
        self.assertEqual(json.loads(j), MARKER)
        with self.assertRaises(mb.ModelBackendError):
            mb.marker_from_entry({"base_url": "x"})

    def test_load_registry_absent_or_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mb.load_registry(path=str(Path(d) / "none.json")), {})
            bad = Path(d) / "bad.json"
            bad.write_text("[]")   # a list, not a dict
            self.assertEqual(mb.load_registry(path=str(bad)), {})


class TestApplyManagedSettings(unittest.TestCase):
    """apply_managed_settings_defaults marker-conditional env + byte-identity."""

    def _apply(self, settings):
        import airuleset
        return airuleset.apply_managed_settings_defaults(settings)

    def test_no_marker_byte_identity(self):
        import airuleset
        with m.patch("cli_model_backend.load_marker", return_value=None), \
                m.patch("cli_model_backend.apikey_helper_path",
                        return_value=Path("/home/x/.claude/airuleset-model-gateway-apikey.sh")):
            out = self._apply({})
        # none of the marker-derived keys leak in on a no-marker box
        for k in mb.BACKEND_ENV_KEYS:
            self.assertNotIn(k, out["env"], "%s leaked on a no-marker box" % k)
        self.assertNotIn("apiKeyHelper", out)
        # the fleet defaults are untouched (byte-identical to today)
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)
        self.assertEqual(out["env"]["CLAUDE_CODE_SUBAGENT_MODEL"],
                         airuleset.MODEL_TIERS["opus"])

    def test_no_marker_preserves_foreign_apikeyhelper(self):
        with m.patch("cli_model_backend.load_marker", return_value=None), \
                m.patch("cli_model_backend.apikey_helper_path",
                        return_value=Path("/managed/helper.sh")):
            out = self._apply({"apiKeyHelper": "/usr/bin/user-own"})
        self.assertEqual(out["apiKeyHelper"], "/usr/bin/user-own",
                         "a user's own apiKeyHelper must be preserved")

    def test_no_marker_pops_our_stale_apikeyhelper(self):
        with m.patch("cli_model_backend.load_marker", return_value=None), \
                m.patch("cli_model_backend.apikey_helper_path",
                        return_value=Path("/managed/helper.sh")):
            out = self._apply({"apiKeyHelper": "/managed/helper.sh"})
        self.assertNotIn("apiKeyHelper", out,
                         "our stale managed apiKeyHelper must self-heal away")

    def test_marker_flips_env_model_and_helper(self):
        with m.patch("cli_model_backend.load_marker", return_value=MARKER), \
                m.patch("cli_model_backend.apikey_helper_path",
                        return_value=Path("/managed/helper.sh")):
            out = self._apply({})
        self.assertEqual(out["env"]["ANTHROPIC_BASE_URL"], MARKER["base_url"])
        self.assertEqual(out["env"]["ANTHROPIC_MODEL"], "pilot-main")
        self.assertEqual(out["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "pilot-sub")
        self.assertEqual(out["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "pilot-fast")
        self.assertEqual(out["env"]["API_TIMEOUT_MS"], "900000")
        self.assertEqual(out["env"]["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"], "1")
        # subagent tier overridden to <sub> on this box; main model → <main>
        self.assertEqual(out["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "pilot-sub")
        self.assertEqual(out["model"], "pilot-main")
        self.assertEqual(out["apiKeyHelper"], "/managed/helper.sh")


class TestLauncherRender(unittest.TestCase):
    def test_render_uses_main_alias_with_marker(self):
        import cli_claude_scripts as cs
        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            s = cs.render_claude_launch_script()
        self.assertIn("--model 'pilot-main'", s)
        self.assertNotIn("{{MANAGED_MODEL}}", s)

    def test_render_uses_managed_model_without_marker(self):
        import airuleset
        import cli_claude_scripts as cs
        with m.patch("cli_model_backend.load_marker", return_value=None):
            s = cs.render_claude_launch_script()
        self.assertIn("--model '%s'" % airuleset.MANAGED_MODEL, s)


class TestDesignGate(unittest.TestCase):
    """gates/designdispatch accepts the pilot main alias only when the marker
    (allowed_alias) is present; every other box still requires the Fable id."""

    def _payload(self, prompt="Work issue #1062"):
        return json.dumps({
            "tool_name": "Agent",
            "cwd": "/repo",
            "tool_input": {"subagent_type": "autopilot-worker", "prompt": prompt},
        })

    def test_check_issue_accepts_fable_id(self):
        from gates import designdispatch as dd
        ok, _ = dd.check_issue(
            1, "o/r", "/repo",
            fetch=lambda s, n, c: ["Design-by: main claude-fable-5-1"],
            fable_id="claude-fable-5-1")
        self.assertTrue(ok)

    def test_check_issue_accepts_pilot_alias_when_allowed(self):
        from gates import designdispatch as dd
        ok, _ = dd.check_issue(
            1, "o/r", "/repo",
            fetch=lambda s, n, c: ["Design-by: main pilot-main"],
            fable_id="claude-fable-5-1", allowed_alias="pilot-main")
        self.assertTrue(ok)

    def test_check_issue_rejects_alias_without_marker(self):
        from gates import designdispatch as dd
        ok, reason = dd.check_issue(
            1, "o/r", "/repo",
            fetch=lambda s, n, c: ["Design-by: main pilot-main"],
            fable_id="claude-fable-5-1", allowed_alias=None)
        self.assertFalse(ok)
        self.assertIn("pilot-main", reason)

    def test_evaluate_threads_pilot_main(self):
        from gates import designdispatch as dd
        verdict, _ = dd.evaluate(
            self._payload(), fetch=lambda s, n, c: ["Design-by: main pilot-main"],
            resolve_slug=lambda cwd: "o/r", fable_id="claude-fable-5-1",
            pilot_main="pilot-main")
        self.assertEqual(verdict, "allow")

    def test_evaluate_rejects_alias_when_no_pilot_main(self):
        from gates import designdispatch as dd
        verdict, _ = dd.evaluate(
            self._payload(), fetch=lambda s, n, c: ["Design-by: main pilot-main"],
            resolve_slug=lambda cwd: "o/r", fable_id="claude-fable-5-1",
            pilot_main=None)
        self.assertEqual(verdict, "block")


class TestUsageSkip(unittest.TestCase):
    def test_marker_box_skips_oauth_and_records_backend(self):
        from watchdog import usage
        calls = {"n": 0}

        def _fetch():
            calls["n"] += 1
            return {"limits": []}

        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            state = {}
            line = usage.check_usage(1000, state, send_fn=lambda *a, **k: None,
                                     fetch=_fetch, interval=0)
        self.assertEqual(line, "backend=gateway")
        self.assertEqual(calls["n"], 0, "OAuth fetch must be SKIPPED on a marker box")

    def test_no_marker_box_polls_oauth(self):
        from watchdog import usage
        calls = {"n": 0}

        def _fetch():
            calls["n"] += 1
            return None  # 429/empty → returns ""

        with m.patch("cli_model_backend.load_marker", return_value=None):
            state = {}
            line = usage.check_usage(1000, state, send_fn=lambda *a, **k: None,
                                     fetch=_fetch, interval=0)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(line, "")


class TestStatusbar(unittest.TestCase):
    def test_gw_render_with_marker(self):
        import statusbar
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir(parents=True)
            (claude / "airuleset-model-backend.json").write_text(json.dumps(MARKER))
            (Path(d) / ".claude.json").write_text(json.dumps(
                {"oauthAccount": {"emailAddress": "x@y.z"}}))
            seg = statusbar.account_email_segment(home=d)
        self.assertIn("gw:pilot-main", seg)
        self.assertNotIn("x@y.z", seg)

    def test_email_render_without_marker(self):
        import statusbar
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".claude.json").write_text(json.dumps(
                {"oauthAccount": {"emailAddress": "x@y.z"}}))
            seg = statusbar.account_email_segment(home=d)
        self.assertIn("x@y.z", seg)
        self.assertNotIn("gw:", seg)


class TestBannedModelAliasesPass(unittest.TestCase):
    """block-banned-model.sh must let the pilot aliases through untouched."""

    HOOK = REPO / "hooks" / "block-banned-model.sh"

    def _run(self, model):
        payload = json.dumps({"tool_name": "Agent",
                              "tool_input": {"model": model}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True)

    @unittest.skipUnless(
        subprocess.run(["bash", "-c", "command -v jq"],
                       capture_output=True).returncode == 0,
        "jq not available")
    def test_pilot_aliases_pass(self):
        for alias in ("pilot-main", "pilot-sub", "pilot-fast"):
            r = self._run(alias)
            self.assertEqual(r.returncode, 0,
                             "alias %s must pass the banned-model hook (%s)"
                             % (alias, r.stderr))

    @unittest.skipUnless(
        subprocess.run(["bash", "-c", "command -v jq"],
                       capture_output=True).returncode == 0,
        "jq not available")
    def test_banned_still_blocked(self):
        r = self._run("claude-opus-5")
        self.assertEqual(r.returncode, 2, "opus-5 must still be blocked")


class _FakeRun:
    """A subprocess.run stand-in recording every call; returns rc=0 by default.
    `fail_on` is a set of hostnames whose ssh returns rc=255 (auth failure)."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = set(fail_on or ())

    def __call__(self, argv, input=None, capture_output=None, text=None,
                 timeout=None):
        self.calls.append({"argv": argv, "input": input})
        host = argv[-2] if len(argv) >= 2 else ""
        rc = 255 if any(h in host for h in self.fail_on) else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")


class TestDeployShipping(unittest.TestCase):
    def _reg_with_miva1(self, d):
        reg = Path(d) / "reg.json"
        mb.set_target("miva1@subdev", path=str(reg))
        return str(reg)

    def _master_key(self, d):
        k = Path(d) / "master.key"
        k.write_text("sk-test-master-key\n")
        return str(k)

    def test_empty_registry_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "none.json"
            fr = _FakeRun()
            out = mb  # keep import used
            from cli_remote import provision_model_backend_markers as prov
            fails = prov(run=fr, registry_path=str(reg),
                         master_key_source=self._master_key(d))
            self.assertEqual(fails, [])
            self.assertEqual(fr.calls, [], "empty registry must ship nothing")
            del out

    def test_ships_key_then_marker_to_member(self):
        from cli_remote import provision_model_backend_markers as prov
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg_with_miva1(d)
            fr = _FakeRun()
            with m.patch("sys.stdout", io.StringIO()), \
                    m.patch("sys.stderr", io.StringIO()):
                fails = prov(run=fr, registry_path=reg,
                             master_key_source=self._master_key(d))
            self.assertEqual(fails, [], "clean shipping must report no failures")
            cmds = [c["argv"][-1] for c in fr.calls]
            self.assertEqual(len(fr.calls), 2, "one key leg + one marker leg")
            self.assertTrue(any("model-gateway.key" in c for c in cmds))
            self.assertTrue(any("airuleset-model-backend.json" in c for c in cmds))
            # the key value + the marker JSON are piped via stdin, never argv
            inputs = "".join((c["input"] or "") for c in fr.calls)
            self.assertIn("sk-test-master-key", inputs)
            self.assertIn('"main": "pilot-main"', inputs)
            for c in fr.calls:
                self.assertNotIn("sk-test-master-key", " ".join(c["argv"]))

    def test_missing_master_key_is_loud_failure(self):
        from cli_remote import provision_model_backend_markers as prov
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg_with_miva1(d)
            fr = _FakeRun()
            with m.patch("sys.stdout", io.StringIO()), \
                    m.patch("sys.stderr", io.StringIO()):
                fails = prov(run=fr, registry_path=reg,
                             master_key_source=str(Path(d) / "absent.key"))
            self.assertTrue(fails, "a missing master key must report failures")
            self.assertTrue(any("master-key-missing" in r for _n, r in fails))
            self.assertEqual(fr.calls, [], "nothing shipped without the key")

    def test_removal_ships_rm(self):
        from cli_remote import remove_model_backend_markers as rm_fn
        import airuleset
        entry = [h for h in airuleset.REMOTE_HOSTS if h.get("name") == "miva1@subdev"]
        self.assertTrue(entry, "miva1@subdev must be a known host")
        fr = _FakeRun()
        with m.patch("sys.stdout", io.StringIO()), \
                m.patch("sys.stderr", io.StringIO()):
            fails = rm_fn(entry, run=fr)
        self.assertEqual(fails, [])
        self.assertEqual(len(fr.calls), 1)
        cmd = fr.calls[0]["argv"][-1]
        self.assertIn("rm -f", cmd)
        self.assertIn("airuleset-model-backend.json", cmd)
        self.assertIn("model-gateway.key", cmd)


class TestCliDispatch(unittest.TestCase):
    class _Args:
        def __init__(self, **kw):
            self.mb_action = "status"
            self.mb_args = []
            self.main = self.sub = self.fast = None
            self.base_url = self.key_file = None
            self.registry_only = False
            for k, v in kw.items():
                setattr(self, k, v)

    def test_status_empty(self):
        with tempfile.TemporaryDirectory() as d, \
                m.patch("cli_model_backend.REGISTRY_PATH",
                        Path(d) / "reg.json"), \
                m.patch("cli_model_backend.MARKER_PATH",
                        Path(d) / "marker.json"), \
                m.patch("sys.stdout", io.StringIO()) as out:
            rc = mb.cmd_model_backend(self._Args(mb_action="status"))
        self.assertEqual(rc, 0)
        self.assertIn("no registry", out.getvalue())

    def test_set_then_status(self):
        with tempfile.TemporaryDirectory() as d, \
                m.patch("cli_model_backend.REGISTRY_PATH",
                        Path(d) / "reg.json"), \
                m.patch("cli_model_backend.MARKER_PATH",
                        Path(d) / "marker.json"), \
                m.patch("sys.stdout", io.StringIO()) as out:
            rc = mb.cmd_model_backend(
                self._Args(mb_action="set", mb_args=["miva1@subdev"]))
            self.assertEqual(rc, 0)
            rc2 = mb.cmd_model_backend(self._Args(mb_action="status"))
            self.assertEqual(rc2, 0)
        self.assertIn("miva1@subdev", out.getvalue())

    def test_set_requires_target(self):
        with m.patch("sys.stderr", io.StringIO()):
            rc = mb.cmd_model_backend(self._Args(mb_action="set", mb_args=[]))
        self.assertEqual(rc, 2)

    def test_clear_registry_only(self):
        with tempfile.TemporaryDirectory() as d, \
                m.patch("cli_model_backend.REGISTRY_PATH",
                        Path(d) / "reg.json"), \
                m.patch("sys.stdout", io.StringIO()):
            mb.set_target("miva1@subdev", path=str(Path(d) / "reg.json"))
            rc = mb.cmd_model_backend(
                self._Args(mb_action="clear", mb_args=["miva1@subdev"],
                           registry_only=True))
        self.assertEqual(rc, 0)
        self.assertEqual(mb.load_registry(path=str(Path(d) / "reg.json")), {})

    def test_unknown_action(self):
        with m.patch("sys.stderr", io.StringIO()):
            rc = mb.cmd_model_backend(self._Args(mb_action="bogus"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
