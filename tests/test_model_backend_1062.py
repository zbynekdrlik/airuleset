"""#1060 Lane L3a — the IMPLEMENTER window (box side), re-scoping #1062 Lane L2.

The 2026-09-18 miva1 incident: the L2 marker flipped the WHOLE box's settings.json
env (shared by every Claude the user runs), so the stream's MAIN Claude came up as
DeepSeek — violating the "design by Fable" rule (#1061). L3a moves the gateway
backend switch OUT of settings.json and INTO a separate implementer LAUNCHER + a
second tmux window `impl`; the main window stays on the Anthropic OAuth login +
Fable. This suite locks the L2-branch DELETIONS (settings byte-identity with the
marker present, main launcher always MANAGED_MODEL, design gate + handoff
Fable-only again, usage polls OAuth even on a marker box, statusline `impl:` role
render) and the L3a ADDITIONS (the `claude-impl` launcher, the impl tmux window,
the live-process report after a set/clear).

Every test is hermetic: markers/registries/key files live under a tmp home or an
injected path; the impl launcher is exec'd against a fake `claude` on PATH; the
deploy shipper is driven with an INJECTED `run` + INJECTED master-key source +
INJECTED registry path. NO real box, NO real `~/.secrets`, NO real `~/.claude`
marker is ever written or read (the live miva1 cutover is the supervisor's —
UNVERIFIED by this lane).
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

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

MARKER = {
    "base_url": "http://100.101.214.103:4000",
    "key_file": "~/.secrets/model-gateway.key",
    "main": "impl-main",
    "sub": "impl-sub",
    "fast": "impl-fast",
    "cwd": "/home/miva1/devel/odoo/odoo-erp",
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
        src = (REPO / "cli_model_backend.py").read_text()
        self.assertNotIn("\nimport airuleset", src)
        self.assertNotIn("\nfrom airuleset", src)

    def test_airuleset_reexports_the_facade(self):
        import airuleset
        self.assertTrue(hasattr(airuleset, "cmd_model_backend"))


class TestMarker(unittest.TestCase):
    def test_valid_and_invalid(self):
        self.assertTrue(mb._valid_marker(MARKER))
        self.assertFalse(mb._valid_marker({}))
        self.assertFalse(mb._valid_marker({"base_url": "x"}))
        self.assertFalse(mb._valid_marker("nope"))

    def test_load_absent_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mb.load_marker(path=str(Path(d) / "none.json")))

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
            self.assertIsNone(mb.load_marker(home=d))

    def test_load_malformed_json_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".claude" / "airuleset-model-backend.json"
            p.parent.mkdir(parents=True)
            p.write_text("{not json")
            self.assertIsNone(mb.load_marker(home=d))


class TestDeadL2SurfaceRemoved(unittest.TestCase):
    """#1060 L3a: the settings.json-feeding Python surface is DELETED (the marker
    is consumed only by the impl launcher + the statusline now)."""

    def test_backend_env_surface_gone(self):
        self.assertFalse(hasattr(mb, "backend_env"))
        self.assertFalse(hasattr(mb, "BACKEND_ENV_KEYS"))

    def test_launcher_model_gone(self):
        self.assertFalse(hasattr(mb, "launcher_model"))

    def test_apikey_helper_surface_gone(self):
        self.assertFalse(hasattr(mb, "maybe_setup_model_backend"))
        self.assertFalse(hasattr(mb, "apikey_helper_script"))
        self.assertFalse(hasattr(mb, "apikey_helper_path"))


class TestRegistry(unittest.TestCase):
    def test_set_defaults_and_clear_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "reg.json"
            entry = mb.set_target("miva1@subdev", cwd="/home/miva1/devel/odoo",
                                  path=str(reg))
            self.assertEqual(entry["main"], "impl-main")
            self.assertEqual(entry["sub"], "impl-sub")
            self.assertEqual(entry["fast"], "impl-fast")
            self.assertEqual(entry["cwd"], "/home/miva1/devel/odoo")
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
                                  cwd="/proj", path=str(reg))
            self.assertEqual(entry["main"], "deepseek-main")
            self.assertEqual(entry["base_url"], "http://x:4000")
            self.assertEqual(entry["key_file"], "~/.k")

    def test_set_rejects_bad_target(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mb.ModelBackendError):
                mb.set_target("miva1", cwd="/proj",
                              path=str(Path(d) / "reg.json"))

    def test_set_requires_cwd(self):
        # #1060 L3a review: --cwd is REQUIRED (the implementer's project dir).
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mb.ModelBackendError):
                mb.set_target("miva1@subdev", path=str(Path(d) / "reg.json"))

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
    """#1060 L3a: apply_managed_settings_defaults NEVER touches settings.json from
    the marker (the L2 branch is deleted) — settings.json is byte-identical with
    and without the marker, so the MAIN session always stays MANAGED_MODEL/OAuth.
    crossSessionInbound=accept is added unconditionally (both sessions)."""

    def _apply(self, settings):
        import airuleset
        return airuleset.apply_managed_settings_defaults(settings)

    def test_marker_present_byte_identical_to_no_marker(self):
        # The RED lock for the deletion: with a marker present, settings.json must
        # be byte-identical to the no-marker box (the L2 branch used to differ).
        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            with_marker = self._apply({})
        with m.patch("cli_model_backend.load_marker", return_value=None):
            without = self._apply({})
        self.assertEqual(json.dumps(with_marker, sort_keys=True),
                         json.dumps(without, sort_keys=True),
                         "a marker must NOT change settings.json (L3a)")

    def test_no_gateway_keys_or_helper_leak(self):
        import airuleset
        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            out = self._apply({})
        for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                  "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                  "ANTHROPIC_DEFAULT_HAIKU_MODEL", "API_TIMEOUT_MS",
                  "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"):
            self.assertNotIn(k, out["env"], "%s must never leak into settings" % k)
        self.assertNotIn("apiKeyHelper", out)
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)
        self.assertEqual(out["env"]["CLAUDE_CODE_SUBAGENT_MODEL"],
                         airuleset.MODEL_TIERS["opus"])

    def test_cross_session_inbound_accept(self):
        # #1060 L3a item 2: both sessions accept cross-session SendMessage (the
        # L3b main->impl dispatch wake-up). Added unconditionally, so it does not
        # break the marker byte-identity above.
        out = self._apply({})
        self.assertEqual(out.get("crossSessionInbound"), "accept")

    def test_preserves_foreign_apikeyhelper(self):
        # A user's OWN apiKeyHelper (not the managed L2 path) is preserved — the
        # self-heal pops ONLY the managed script path.
        out = self._apply({"apiKeyHelper": "/usr/bin/user-own"})
        self.assertEqual(out["apiKeyHelper"], "/usr/bin/user-own")

    def test_self_heals_l2_contaminated_settings(self):
        # #1060 L3a review B 🔴: a box flipped under #1062 L2 still carries the
        # gateway env + managed apiKeyHelper in its SHARED settings.json; the
        # renderer MERGES (result = dict(settings) preserves existing keys), so
        # without an unconditional pop the MAIN window would keep authenticating
        # to the gateway (ANTHROPIC_MODEL env overriding the healed model:). Feed a
        # pre-contaminated settings dict → assert the L2 keys + the managed
        # apiKeyHelper are healed away and the main is MANAGED_MODEL/opus again,
        # while a user's own env key survives.
        import airuleset
        import cli_config
        contaminated = {
            "env": {
                "ANTHROPIC_BASE_URL": "http://100.101.214.103:4000",
                "ANTHROPIC_MODEL": "impl-main",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "impl-main",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "impl-sub",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "impl-fast",
                "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
                "API_TIMEOUT_MS": "900000",
                "CLAUDE_CODE_SUBAGENT_MODEL": "impl-sub",
                "USER_OWN_KEY": "keep-me",
            },
            "apiKeyHelper": cli_config._L2_MANAGED_APIKEY_HELPER,
        }
        out = self._apply(contaminated)
        for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                  "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                  "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "API_TIMEOUT_MS"):
            self.assertNotIn(k, out["env"], "%s must be self-healed away" % k)
        self.assertNotIn("apiKeyHelper", out,
                         "the managed L2 apiKeyHelper must be healed away")
        self.assertEqual(out["model"], airuleset.MANAGED_MODEL)
        self.assertEqual(out["env"]["CLAUDE_CODE_SUBAGENT_MODEL"],
                         airuleset.MODEL_TIERS["opus"])
        self.assertEqual(out["env"].get("USER_OWN_KEY"), "keep-me",
                         "a user's own env key must survive the self-heal")


# --------------------------------------------------------------------------- #
# The claude-impl launcher (design item 2) — string locks + FUNCTIONAL exec.
# --------------------------------------------------------------------------- #
_IMPL_ENV_KEYS = (
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "API_TIMEOUT_MS",
)


def _enc_project(path):
    """Claude Code's transcript-dir name for a cwd (encode_project_dir)."""
    return "".join("-" if c in "/._" else c for c in str(path))


def _run_impl_launcher(home, with_marker=True, key_content="tok-XYZ-123",
                       with_prompt=False, cwd_exists=True, session_id=None,
                       transcript_exists=False, extra_project_transcript=None):
    """Exec the rendered claude-impl launcher against a fake `claude` that dumps
    the backend env, PWD and ARGS. Returns (rc, stdout, stderr). Fully hermetic.
    The marker's `cwd` = <home>/project (created iff cwd_exists). session_id →
    pre-write the impl session-id file; transcript_exists → pre-create that id's
    transcript under the project dir; extra_project_transcript → pre-create a
    FOREIGN (e.g. main-window) transcript in the project dir (must never be
    picked)."""
    import cli_claude_scripts as cs
    home = Path(home)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    secrets = home / ".secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    project = home / "project"
    if cwd_exists:
        project.mkdir(parents=True, exist_ok=True)
    if with_marker:
        marker = dict(MARKER)
        marker["key_file"] = str(secrets / "model-gateway.key")
        marker["cwd"] = str(project)
        (home / ".claude" / "airuleset-model-backend.json").write_text(
            json.dumps(marker))
        (secrets / "model-gateway.key").write_text(key_content)
    if with_prompt:
        (home / ".claude" / "airuleset-implementer.md").write_text("# impl\n")
    if session_id is not None:
        (home / ".claude" / "airuleset-implementer-session").write_text(
            session_id + "\n")
    pdir = home / ".claude" / "projects" / _enc_project(project)
    if transcript_exists and session_id is not None:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / (session_id + ".jsonl")).write_text("{}\n")
    if extra_project_transcript is not None:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / (extra_project_transcript + ".jsonl")).write_text("{}\n")
    script = home / ".claude" / "airuleset-claude-impl.sh"
    script.write_text(cs.render_claude_impl_launch_script())
    os.chmod(str(script), 0o755)
    # A fake `claude` that prints the backend env, PWD and ARGS.
    binp = home / "bin"
    binp.mkdir()
    fake = binp / "claude"
    fake.write_text("#!/usr/bin/env bash\n"
                    "for v in " + " ".join(_IMPL_ENV_KEYS) + " AIRULESET_ROLE; do\n"
                    '  printf "%s=%s\\n" "$v" "${!v:-}"\n'
                    "done\n"
                    'printf "PWD=%s\\n" "$PWD"\n'
                    'printf "ARGS=%s\\n" "$*"\n')
    os.chmod(str(fake), 0o755)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(binp) + os.pathsep + env.get("PATH", "")
    # Run from a NEUTRAL cwd (home) so the launcher's OWN cd is what sets PWD.
    r = subprocess.run(["bash", str(script)], env=env, cwd=str(home),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, r.stderr


class TestImplLauncher(unittest.TestCase):
    def test_render_string_locks(self):
        import cli_claude_scripts as cs
        s = cs.render_claude_impl_launch_script()
        self.assertIn("airuleset-model-backend.json", s)
        self.assertIn("refusing to start", s)
        self.assertIn("exit 1", s)
        for key in _IMPL_ENV_KEYS:
            self.assertIn("export %s=" % key, s, "missing export %s" % key)
        self.assertIn("AIRULESET_ROLE=implementer", s)
        self.assertIn("--append-system-prompt-file", s)
        self.assertIn("airuleset-implementer.md", s)
        # the token is READ from the key file, never a literal in the script
        self.assertNotIn("sk-", s)
        # #1060 L3a review: OWN session identity — the exec NEVER uses
        # `-c`/--continue (which resumes the MAIN window's transcript); it uses
        # `-r <id>` or `--session-id <id>`. Scope the -c/--continue ban to the
        # actual exec lines (the explanatory comment legitimately names them).
        exec_lines = [ln for ln in s.splitlines() if "exec claude" in ln]
        self.assertTrue(exec_lines)
        for ln in exec_lines:
            self.assertNotIn(" -c ", ln)
            self.assertNotIn("--continue", ln)
        self.assertTrue(any("--session-id " in ln for ln in exec_lines))
        self.assertTrue(any('-r "$_sid"' in ln for ln in exec_lines))
        self.assertIn("airuleset-implementer-session", s)
        # #1060 L3a review: cd into the marker's PROJECT dir before running.
        self.assertIn('cd "$_mb_cwd"', s)
        self.assertIn("does not exist", s)  # the cwd refusal

    def test_refuses_without_marker(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d, with_marker=False)
        self.assertEqual(rc, 1, "must refuse (exit 1) with no marker")
        self.assertIn("no model-backend marker", err)
        self.assertNotIn("AIRULESET_ROLE=implementer", out)

    def test_refuses_without_readable_key(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude").mkdir(parents=True)
            project = home / "project"
            project.mkdir()
            marker = dict(MARKER)
            marker["key_file"] = str(home / ".secrets" / "absent.key")
            marker["cwd"] = str(project)  # a valid cwd so the KEY check is reached
            (home / ".claude" / "airuleset-model-backend.json").write_text(
                json.dumps(marker))
            import cli_claude_scripts as cs
            script = home / ".claude" / "airuleset-claude-impl.sh"
            script.write_text(cs.render_claude_impl_launch_script())
            os.chmod(str(script), 0o755)
            env = dict(os.environ)
            env["HOME"] = str(home)
            r = subprocess.run(["bash", str(script)], env=env,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("key file", r.stderr)

    def test_refuses_without_cwd_dir(self):
        # #1060 L3a review: the marker cwd must exist — refuse LOUDLY otherwise
        # (a misprovisioned project dir must not silently open in the wrong place).
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d, cwd_exists=False)
        self.assertEqual(rc, 1, "must refuse (exit 1) when the marker cwd is gone")
        self.assertIn("does not exist", err)
        self.assertNotIn("AIRULESET_ROLE=implementer", out)

    def test_exports_backend_env(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d, key_content="tok-XYZ-123",
                                              with_prompt=True)
        self.assertEqual(rc, 0, err)
        kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        self.assertEqual(kv["ANTHROPIC_BASE_URL"], MARKER["base_url"])
        self.assertEqual(kv["ANTHROPIC_MODEL"], "impl-main")
        self.assertEqual(kv["ANTHROPIC_DEFAULT_OPUS_MODEL"], "impl-main")
        self.assertEqual(kv["ANTHROPIC_DEFAULT_SONNET_MODEL"], "impl-sub")
        self.assertEqual(kv["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "impl-fast")
        self.assertEqual(kv["CLAUDE_CODE_SUBAGENT_MODEL"], "impl-sub")
        self.assertEqual(kv["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"], "1")
        self.assertEqual(kv["API_TIMEOUT_MS"], "900000")
        self.assertEqual(kv["ANTHROPIC_AUTH_TOKEN"], "tok-XYZ-123")
        self.assertEqual(kv["AIRULESET_ROLE"], "implementer")

    def test_warns_but_starts_without_prompt_file(self):
        # L3b ships airuleset-implementer.md; the launcher tolerates its absence
        # with a LOUD warning but still starts.
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d, with_prompt=False)
        self.assertEqual(rc, 0, err)
        self.assertIn("implementer system prompt", err)
        kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        self.assertEqual(kv["AIRULESET_ROLE"], "implementer")

    def test_runs_in_marker_cwd(self):
        # #1060 L3a review: the impl session runs in the marker's PROJECT dir, not
        # the shell's inherited cwd — so its transcript lands under the right dir.
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d)
        self.assertEqual(rc, 0, err)
        kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        self.assertEqual(kv["PWD"], str(Path(d) / "project"))

    def test_starts_fresh_session_id_never_continue(self):
        # #1060 L3a MAJOR: no impl session id yet → a NEW uuid via --session-id
        # (NEVER -c, which would resume the main window's transcript); the id file
        # is written 0600.
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d)
            self.assertEqual(rc, 0, err)
            args = [ln for ln in out.splitlines() if ln.startswith("ARGS=")][0]
            self.assertIn("--session-id", args)
            self.assertNotIn(" -c ", " " + args + " ")
            self.assertNotIn(" -r ", " " + args + " ")
            # the id file checks MUST run inside the with (the tmp dir is deleted
            # on exit).
            sid_file = Path(d) / ".claude" / "airuleset-implementer-session"
            self.assertTrue(sid_file.exists(),
                            "the impl session id file must be written")
            self.assertEqual(stat.S_IMODE(sid_file.stat().st_mode), 0o600)
            # the id in ARGS matches the persisted id
            sid = sid_file.read_text().strip()
            self.assertIn(sid, args)

    def test_resumes_own_session_id(self):
        # An impl id whose transcript ALREADY exists under the project dir → -r.
        sid = "12345678-1234-1234-1234-123456789abc"
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(d, session_id=sid,
                                              transcript_exists=True)
        self.assertEqual(rc, 0, err)
        args = [ln for ln in out.splitlines() if ln.startswith("ARGS=")][0]
        self.assertIn("-r " + sid, args)
        self.assertNotIn("--session-id", args)
        self.assertNotIn(" -c ", " " + args + " ")

    def test_never_picks_the_main_transcript(self):
        # A FOREIGN (main-window) transcript sits in the project dir but the impl
        # id's does NOT → fresh --session-id, NEVER -c (which would resume it).
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_impl_launcher(
                d, extra_project_transcript="ffffffff-0000-0000-0000-000000000000")
        self.assertEqual(rc, 0, err)
        args = [ln for ln in out.splitlines() if ln.startswith("ARGS=")][0]
        self.assertIn("--session-id", args)
        self.assertNotIn(" -c ", " " + args + " ")
        self.assertNotIn("ffffffff-0000", args)


class TestMainLauncherAlwaysManagedModel(unittest.TestCase):
    """#1060 L3a item 1: the MAIN launcher `--model` is ALWAYS MANAGED_MODEL, even
    on a marker box (the L2 alias flip is deleted — the main stays Fable)."""

    def test_render_uses_managed_model_even_with_marker(self):
        import airuleset
        import cli_claude_scripts as cs
        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            s = cs.render_claude_launch_script()
        self.assertIn("--model '%s'" % airuleset.MANAGED_MODEL, s)
        self.assertNotIn("impl-main", s)
        self.assertNotIn("{{MANAGED_MODEL}}", s)


# --------------------------------------------------------------------------- #
# The impl tmux window (design item 3) — one rendered block serves every box;
# the marker + has-session checks happen at SHELL time.
# --------------------------------------------------------------------------- #
_FAKE_TMUX_ALWAYS0 = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$REC"\n'
# has-session returns rc=1 (session absent); everything else records + rc=0.
_FAKE_TMUX_NO_SESSION = (
    '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$REC"\n'
    'case "$1" in has-session) exit 1 ;; esac\nexit 0\n')
# has-session returns rc=0 (session present).
_FAKE_TMUX_HAS_SESSION = (
    '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$REC"\n'
    'case "$1" in has-session) exit 0 ;; esac\nexit 0\n')


def _run_tmux_block(block, cmdline, home, fake_tmux):
    """Source `block` in an interactive bash with HOME + a fake `command tmux`
    recorder. Returns the recorded tmux argv lines."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fake = tdp / "tmux"
        fake.write_text(fake_tmux)
        os.chmod(str(fake), 0o755)
        blockfile = tdp / "block.sh"
        blockfile.write_text(block + "\n")
        rec = tdp / "rec"
        rec.write_text("")
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["PATH"] = str(tdp) + os.pathsep + env.get("PATH", "")
        env["REC"] = str(rec)
        env["HOME"] = str(home)
        script = 'source "%s"; %s' % (blockfile, cmdline)
        subprocess.run(["bash", "--norc", "--noprofile", "-ic", script],
                       env=env, capture_output=True, text=True, timeout=20)
        return [ln for ln in rec.read_text().splitlines() if ln.strip()]


class TestTmuxImplWindow(unittest.TestCase):
    def setUp(self):
        import cli_bashrc_appliers as appliers
        self.block = appliers.render_tmux_attach_block("zbynek")

    def _home_no_marker(self, td):
        (Path(td) / ".claude").mkdir(parents=True, exist_ok=True)
        return td

    def _home_with_marker(self, td):
        c = Path(td) / ".claude"
        c.mkdir(parents=True, exist_ok=True)
        project = Path(td) / "project"
        project.mkdir(parents=True, exist_ok=True)  # a real cwd so `-c` fires
        marker = dict(MARKER)
        marker["cwd"] = str(project)
        (c / "airuleset-model-backend.json").write_text(json.dumps(marker))
        return td

    def test_block_string_lock_carries_c_flag(self):
        # #1060 L3a review: the rendered attach block passes the project cwd to
        # the impl window via `-c "$_impl_cwd"`.
        self.assertIn('-n impl -c "$_impl_cwd"', self.block)

    def test_no_marker_single_window(self):
        with tempfile.TemporaryDirectory() as td:
            home = self._home_no_marker(td)
            rec = _run_tmux_block(self.block, "t proj", home,
                                  _FAKE_TMUX_NO_SESSION)
        self.assertEqual(rec, ["new-session -A -s proj"],
                         "a non-marker box gets exactly one window (no impl)")

    def test_marker_new_session_adds_impl_window(self):
        with tempfile.TemporaryDirectory() as td:
            home = self._home_with_marker(td)
            rec = _run_tmux_block(self.block, "t proj", home,
                                  _FAKE_TMUX_NO_SESSION)
        joined = "\n".join(rec)
        self.assertIn("new-session -A -s proj", joined)
        impl_lines = [r for r in rec if r.startswith("new-window")]
        self.assertEqual(len(impl_lines), 1, "exactly ONE impl window: %r" % rec)
        self.assertIn("-n impl", impl_lines[0])
        self.assertIn("airuleset-claude-impl.sh", impl_lines[0])
        # the project cwd is passed to the impl window (a real dir → the -c branch)
        self.assertIn("-c ", impl_lines[0])
        self.assertIn(str(Path(td) / "project"), impl_lines[0])

    def test_marker_existing_session_no_new_window(self):
        # Attaching to an EXISTING session adds nothing (never keystrokes, never
        # a duplicate impl window).
        with tempfile.TemporaryDirectory() as td:
            home = self._home_with_marker(td)
            rec = _run_tmux_block(self.block, "t proj", home,
                                  _FAKE_TMUX_HAS_SESSION)
        self.assertFalse(any(r.startswith("new-window") for r in rec),
                         "no impl window on attach to an existing session: %r"
                         % rec)
        self.assertIn("new-session -A -s proj", "\n".join(rec))

    def test_existing_tmux_rewrite_unaffected_no_marker(self):
        # The #651 `tmux new -t X` rewrite still yields exactly the attach call
        # on a non-marker box (regression guard for the existing behaviour).
        with tempfile.TemporaryDirectory() as td:
            home = self._home_no_marker(td)
            rec = _run_tmux_block(self.block, "tmux new -t proj", home,
                                  _FAKE_TMUX_NO_SESSION)
        self.assertEqual(rec, ["new-session -A -s proj"])


class TestDesignGateFableOnly(unittest.TestCase):
    """#1060 L3a item 1: the design gate accepts ONLY the Fable id again — the
    pilot-alias acceptance (allowed_alias / _pilot_main_alias) is deleted."""

    def _payload(self, prompt="Work issue #1060"):
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

    def test_check_issue_rejects_alias(self):
        from gates import designdispatch as dd
        ok, reason = dd.check_issue(
            1, "o/r", "/repo",
            fetch=lambda s, n, c: ["Design-by: main impl-main"],
            fable_id="claude-fable-5-1")
        self.assertFalse(ok)
        self.assertIn("impl-main", reason)

    def test_evaluate_rejects_alias_even_with_marker(self):
        # RED lock: on a marker box the L2 gate ALLOWED the alias; L3a BLOCKS it
        # (the main authors the design on Fable, always).
        from gates import designdispatch as dd
        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            verdict, _ = dd.evaluate(
                self._payload(),
                fetch=lambda s, n, c: ["Design-by: main impl-main"],
                resolve_slug=lambda cwd: "o/r", fable_id="claude-fable-5-1")
        self.assertEqual(verdict, "block")

    def test_evaluate_allows_fable(self):
        from gates import designdispatch as dd
        verdict, _ = dd.evaluate(
            self._payload(),
            fetch=lambda s, n, c: ["Design-by: main claude-fable-5-1"],
            resolve_slug=lambda cwd: "o/r", fable_id="claude-fable-5-1")
        self.assertEqual(verdict, "allow")

    def test_pilot_alias_helpers_gone(self):
        from gates import designdispatch as dd
        self.assertFalse(hasattr(dd, "_pilot_main_alias"))
        import inspect
        self.assertNotIn("allowed_alias",
                         inspect.signature(dd.check_issue).parameters)
        self.assertNotIn("pilot_main",
                         inspect.signature(dd.evaluate).parameters)


class TestUsagePollsOnMarkerBox(unittest.TestCase):
    def test_marker_box_still_polls_oauth(self):
        # RED lock: L2 SKIPPED the OAuth poll on a marker box; L3a keeps the MAIN
        # on OAuth even on a marker box, so the poll must run.
        from watchdog import usage
        calls = {"n": 0}

        def _fetch():
            calls["n"] += 1
            return None

        with m.patch("cli_model_backend.load_marker", return_value=MARKER):
            line = usage.check_usage(1000, {}, send_fn=lambda *a, **k: None,
                                     fetch=_fetch, interval=0)
        self.assertEqual(calls["n"], 1, "the OAuth poll must run on a marker box")
        self.assertEqual(line, "")
        self.assertNotEqual(line, "backend=gateway")

    def test_no_marker_box_polls_oauth(self):
        from watchdog import usage
        calls = {"n": 0}

        def _fetch():
            calls["n"] += 1
            return None

        with m.patch("cli_model_backend.load_marker", return_value=None):
            line = usage.check_usage(1000, {}, send_fn=lambda *a, **k: None,
                                     fetch=_fetch, interval=0)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(line, "")

    def test_model_backend_active_removed(self):
        from watchdog import usage
        self.assertFalse(hasattr(usage, "_model_backend_active"))


class TestStatusbarRole(unittest.TestCase):
    def test_impl_role_renders_alias(self):
        import statusbar
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / ".claude"
            c.mkdir(parents=True)
            (c / "airuleset-model-backend.json").write_text(json.dumps(MARKER))
            (Path(d) / ".claude.json").write_text(json.dumps(
                {"oauthAccount": {"emailAddress": "x@y.z"}}))
            with m.patch.dict(os.environ, {"AIRULESET_ROLE": "implementer"}):
                seg = statusbar.account_email_segment(home=d)
        self.assertIn("impl:impl-main", seg)
        self.assertNotIn("x@y.z", seg)

    def test_no_role_renders_email_even_with_marker(self):
        # The MAIN window (no AIRULESET_ROLE) shows the OAuth email even on a
        # marker box — the old `gw:` marker render is GONE.
        import statusbar
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / ".claude"
            c.mkdir(parents=True)
            (c / "airuleset-model-backend.json").write_text(json.dumps(MARKER))
            (Path(d) / ".claude.json").write_text(json.dumps(
                {"oauthAccount": {"emailAddress": "x@y.z"}}))
            env = {k: v for k, v in os.environ.items() if k != "AIRULESET_ROLE"}
            with m.patch.dict(os.environ, env, clear=True):
                seg = statusbar.account_email_segment(home=d)
        self.assertIn("x@y.z", seg)
        self.assertNotIn("gw:", seg)
        self.assertNotIn("impl:", seg)

    def test_gw_render_gone_from_source(self):
        src = (REPO / "statusbar.py").read_text()
        self.assertNotIn('"gw:%s"', src)
        self.assertNotIn("gw:%s", src)


class TestBannedModelAliasesPass(unittest.TestCase):
    """block-banned-model.sh must let the impl aliases through untouched."""

    HOOK = REPO / "hooks" / "block-banned-model.sh"

    def _run(self, model):
        payload = json.dumps({"tool_name": "Agent",
                              "tool_input": {"model": model}})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True, env=hermetic_hook_env(self))

    @unittest.skipUnless(
        subprocess.run(["bash", "-c", "command -v jq"],
                       capture_output=True).returncode == 0,
        "jq not available")
    def test_impl_aliases_pass(self):
        for alias in ("impl-main", "impl-sub", "impl-fast"):
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
    `pgrep_stdout` is returned as stdout for a pgrep call (the live-process
    report); everything else returns empty stdout."""

    def __init__(self, fail_on=None, pgrep_stdout=""):
        self.calls = []
        self.fail_on = set(fail_on or ())
        self.pgrep_stdout = pgrep_stdout

    def __call__(self, argv, input=None, capture_output=None, text=None,
                 timeout=None):
        self.calls.append({"argv": argv, "input": input})
        host = argv[-2] if len(argv) >= 2 else ""
        rc = 255 if any(h in host for h in self.fail_on) else 0
        cmd = argv[-1] if argv else ""
        out = self.pgrep_stdout if "pgrep" in cmd else ""
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")


class TestDeployShipping(unittest.TestCase):
    def _reg_with_miva1(self, d):
        reg = Path(d) / "reg.json"
        mb.set_target("miva1@subdev", cwd="/home/miva1/devel/odoo",
                      path=str(reg))
        return str(reg)

    def _master_key(self, d):
        k = Path(d) / "master.key"
        k.write_text("sk-test-master-key\n")
        return str(k)

    def test_empty_registry_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "none.json"
            fr = _FakeRun()
            from cli_remote import provision_model_backend_markers as prov
            fails = prov(run=fr, registry_path=str(reg),
                         master_key_source=self._master_key(d))
            self.assertEqual(fails, [])
            self.assertEqual(fr.calls, [], "empty registry must ship nothing")

    def test_ships_key_then_marker_then_flip_then_pgrep(self):
        from cli_remote import provision_model_backend_markers as prov
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg_with_miva1(d)
            fr = _FakeRun(pgrep_stdout="")
            with m.patch("sys.stdout", io.StringIO()), \
                    m.patch("sys.stderr", io.StringIO()):
                fails = prov(run=fr, registry_path=reg,
                             master_key_source=self._master_key(d))
            self.assertEqual(fails, [], "clean shipping must report no failures")
            cmds = [c["argv"][-1] for c in fr.calls]
            # key leg + marker leg + install-flip + the live-process pgrep check
            self.assertEqual(len(fr.calls), 4,
                             "key + marker + install-flip + pgrep: %r" % cmds)
            self.assertTrue(any("model-gateway.key" in c for c in cmds))
            self.assertTrue(any("airuleset-model-backend.json" in c for c in cmds))
            self.assertTrue(any("airuleset.py install" in c for c in cmds))
            self.assertTrue(any("pgrep" in c and "claude" in c for c in cmds),
                            "a live-process pgrep check must run after delivery")
            inputs = "".join((c["input"] or "") for c in fr.calls)
            self.assertIn("sk-test-master-key", inputs)
            self.assertIn('"main": "impl-main"', inputs)
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

    def test_removal_ships_rm_then_pgrep(self):
        from cli_remote import remove_model_backend_markers as rm_fn
        import airuleset
        entry = [h for h in airuleset.REMOTE_HOSTS if h.get("name") == "miva1@subdev"]
        self.assertTrue(entry, "miva1@subdev must be a known host")
        fr = _FakeRun(pgrep_stdout="")
        with m.patch("sys.stdout", io.StringIO()), \
                m.patch("sys.stderr", io.StringIO()):
            fails = rm_fn(entry, run=fr)
        self.assertEqual(fails, [])
        cmds = [c["argv"][-1] for c in fr.calls]
        self.assertTrue(any("rm -f" in c and "airuleset-model-backend.json" in c
                            for c in cmds))
        self.assertTrue(any("pgrep" in c and "claude" in c for c in cmds),
                        "a live-process pgrep check must run after removal")


class TestLiveProcessReport(unittest.TestCase):
    """#1060 L3a supervisor addendum: after a set/clear, REPORT (read-only, never
    kill) the live claude processes on the target that predate the change."""

    def _host(self):
        return [{"name": "miva1@subdev", "user": "miva1", "host": "subdev",
                 "identity": "~/.ssh/id_ed25519"}]

    def test_reports_when_processes_live(self):
        from cli_remote import report_live_claude_processes as rep
        fr = _FakeRun(pgrep_stdout="4242 claude --model impl-main -c\n")
        out = io.StringIO()
        with m.patch("sys.stdout", out):
            results = rep(self._host(), run=fr)
        text = out.getvalue()
        self.assertIn("miva1@subdev has 1 live claude process", text)
        self.assertIn("4242", text)
        self.assertIn("until restarted", text)
        # a pgrep argv against the right target
        self.assertTrue(any("pgrep" in c["argv"][-1] for c in fr.calls))
        self.assertEqual(results, [("miva1@subdev", 1)])
        # NEVER a kill
        for c in fr.calls:
            self.assertNotIn("kill", " ".join(c["argv"]).lower())

    def test_no_line_when_none(self):
        from cli_remote import report_live_claude_processes as rep
        fr = _FakeRun(pgrep_stdout="")
        out = io.StringIO()
        with m.patch("sys.stdout", out):
            results = rep(self._host(), run=fr)
        self.assertNotIn("live claude process", out.getvalue())
        self.assertEqual(results, [("miva1@subdev", 0)])

    def test_skips_target_without_identity(self):
        from cli_remote import report_live_claude_processes as rep
        fr = _FakeRun(pgrep_stdout="1 claude\n")
        out = io.StringIO()
        with m.patch("sys.stdout", out), m.patch("sys.stderr", io.StringIO()):
            results = rep([{"name": "x@y", "user": "x", "host": "y"}], run=fr)
        # no pinned identity for an owner-secret box → skipped quietly, no ssh
        self.assertEqual(fr.calls, [])
        self.assertEqual(results, [])


class TestCliDispatch(unittest.TestCase):
    class _Args:
        def __init__(self, **kw):
            self.mb_action = "status"
            self.mb_args = []
            self.main = self.sub = self.fast = None
            self.base_url = self.key_file = None
            self.cwd = "/home/miva1/devel/odoo/odoo-erp"
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
            mb.set_target("miva1@subdev", cwd="/proj",
                          path=str(Path(d) / "reg.json"))
            rc = mb.cmd_model_backend(
                self._Args(mb_action="clear", mb_args=["miva1@subdev"],
                           registry_only=True))
        self.assertEqual(rc, 0)
        self.assertEqual(mb.load_registry(path=str(Path(d) / "reg.json")), {})

    def test_unknown_action(self):
        with m.patch("sys.stderr", io.StringIO()):
            rc = mb.cmd_model_backend(self._Args(mb_action="bogus"))
        self.assertEqual(rc, 2)


class TestReviewFixes1062(unittest.TestCase):
    """Kept locks from the L2 adversarial reviews (marker safety, base_url,
    marker_json) + the L3a handoff-Fable-only deletion lock."""

    def test_valid_marker_rejects_injection_alias(self):
        bad = {**MARKER, "main": "impl' ; rm -rf /"}
        self.assertFalse(mb._valid_marker(bad))
        for ch in ("a b", "a`b", 'a"b', "a;b", "a|b", "a$b", "a\\b", "a\nb"):
            self.assertFalse(mb._valid_marker({**MARKER, "sub": ch}),
                             "unsafe char %r must be rejected" % ch)

    def test_valid_marker_accepts_real_values(self):
        self.assertTrue(mb._valid_marker({
            "base_url": "http://100.101.214.103:4000",
            "key_file": "~/.secrets/model-gateway.key",
            "main": "openrouter/deepseek/deepseek-v4.1-flash",
            "sub": "impl-sub", "fast": "impl-fast",
            "cwd": "/home/miva1/devel/odoo/odoo-erp"}))

    def test_set_target_refuses_unsafe_alias(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mb.ModelBackendError):
                mb.set_target("miva1@subdev", main="x'; rm -rf /",
                              cwd="/proj", path=str(Path(d) / "reg.json"))

    def test_default_base_url_uses_live_tailscale_ip(self):
        with m.patch("cli_model_gateway._tailscale_ip", return_value="100.9.9.9"):
            self.assertEqual(mb.default_base_url(), "http://100.9.9.9:4000")

    def test_default_base_url_falls_back_on_failure(self):
        with m.patch("cli_model_gateway._tailscale_ip",
                     side_effect=RuntimeError("no tailscale")):
            self.assertEqual(mb.default_base_url(),
                             "http://%s:4000" % mb.CONTROLLER_TAILSCALE_IP)

    def test_marker_json_no_trailing_newline(self):
        self.assertFalse(mb.marker_json(MARKER).endswith("\n"))
        self.assertEqual(json.loads(mb.marker_json(MARKER)), MARKER)

    def test_handoff_pilot_alias_helper_gone(self):
        # #1060 L3a: handoff is Fable-only again — the pilot-alias self-review
        # acceptance is deleted; only a MODEL_TIERS id canonicalizes.
        import airuleset
        self.assertFalse(hasattr(airuleset, "_pilot_alias_self_review_model"))
        self.assertIsNone(airuleset._canonical_self_review_model("impl-main"))
        self.assertEqual(
            airuleset._canonical_self_review_model("claude-fable-5-1"),
            "claude-fable-5-1")

    def test_install_flip_failure_is_warn_not_delivery_failure(self):
        from cli_remote import provision_model_backend_markers as prov
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "reg.json"
            mb.set_target("miva1@subdev", cwd="/home/miva1/devel/odoo",
                          path=str(reg))
            k = Path(d) / "master.key"
            k.write_text("sk-x\n")

            class _FlipFails:
                def __init__(self):
                    self.calls = []

                def __call__(self, argv, input=None, capture_output=None,
                             text=None, timeout=None):
                    self.calls.append(argv[-1])
                    rc = 255 if "airuleset.py install" in argv[-1] else 0
                    return subprocess.CompletedProcess(argv, rc, stdout="",
                                                       stderr="boom")
            fr = _FlipFails()
            with m.patch("sys.stdout", io.StringIO()), \
                    m.patch("sys.stderr", io.StringIO()):
                fails = prov(run=fr, registry_path=str(reg),
                             master_key_source=str(k))
            self.assertEqual(fails, [], "a failed install-flip is a WARN, not a "
                             "delivery failure — the marker IS delivered")
            self.assertTrue(any("airuleset.py install" in c for c in fr.calls))


if __name__ == "__main__":
    unittest.main()
