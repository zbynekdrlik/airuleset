"""#1062 Lane L1 — the managed LiteLLM model gateway leaf (cli_model_gateway.py).

Every test is hermetic: the renderers take an INJECTED `read_secret` (never the
real `~/.secrets`), `set` writes into a temp home, the install-step gate is driven
with an injected box class + alias-map path + a fake `setup_fn`, so the venv /
pip-install / systemctl / `~/.secrets` real-install path is NEVER exercised here
(that is the supervisor's live run — UNVERIFIED by this lane).
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_model_gateway as mg  # noqa: E402

TARGET = "openrouter/deepseek/deepseek-v4.1-flash"

BASE_CONFIG = {
    "aliases": {"pilot-main": TARGET, "pilot-sub": TARGET, "pilot-fast": TARGET},
    "providers": {"openrouter": {"key_file": "~/.secrets/openrouter.key"}},
}


class TestProviderEnvVar(unittest.TestCase):
    def test_deterministic_names(self):
        self.assertEqual(mg._provider_env_var("openrouter"), "OPENROUTER_KEY")
        self.assertEqual(mg._provider_env_var("deepseek"), "DEEPSEEK_KEY")
        # hyphen/dot collapse to a single underscore, uppercased
        self.assertEqual(mg._provider_env_var("vertex-ai"), "VERTEX_AI_KEY")
        self.assertEqual(mg._provider_env_var("openai"), "OPENAI_KEY")

    def test_provider_of(self):
        self.assertEqual(mg._provider_of(TARGET), "openrouter")
        self.assertEqual(mg._provider_of("deepseek/deepseek-chat"), "deepseek")


class TestRenderYaml(unittest.TestCase):
    def test_shape_and_keys(self):
        y = mg.render_gateway_yaml(BASE_CONFIG)
        # LiteLLM proxy config keys (docs.litellm.ai)
        self.assertIn("model_list:", y)
        self.assertIn('model_name: "pilot-main"', y)
        self.assertIn(f'model: "{TARGET}"', y)
        self.assertIn('api_key: "os.environ/OPENROUTER_KEY"', y)
        self.assertIn('master_key: "os.environ/LITELLM_MASTER_KEY"', y)
        self.assertIn("drop_params: true", y)
        self.assertIn(f'callbacks: "{mg.CALLBACK_REF}"', y)

    def test_no_secret_literal_ever_in_yaml(self):
        # A config whose provider key_file name looks secret-ish must still not
        # leak any VALUE — the yaml only ever carries os.environ/ indirection.
        cfg = json.loads(json.dumps(BASE_CONFIG))
        y = mg.render_gateway_yaml(cfg)
        self.assertNotIn("sk-", y)
        self.assertNotIn(".secrets/openrouter.key", y)  # the file path never leaks

    def test_api_key_var_matches_target_provider(self):
        cfg = {"aliases": {"pilot-fast": "deepseek/deepseek-chat"},
               "providers": {"deepseek": {"key_file": "~/.secrets/deepseek.key"}}}
        y = mg.render_gateway_yaml(cfg)
        self.assertIn('model: "deepseek/deepseek-chat"', y)
        self.assertIn('api_key: "os.environ/DEEPSEEK_KEY"', y)


class TestRenderEnv(unittest.TestCase):
    def test_key_indirection_uses_injected_reader(self):
        seen = {}

        def fake_read(path):
            seen[path] = True
            return {"MASTER": "sk-master-xyz"}.get(
                "MASTER" if path.endswith("model-gateway.master") else path,
                "VALUE-" + os.path.basename(path))

        env = mg.render_gateway_env(BASE_CONFIG, fake_read,
                                    master_key_file="~/.secrets/model-gateway.master")
        self.assertIn("LITELLM_MASTER_KEY=sk-master-xyz", env)
        self.assertIn("OPENROUTER_KEY=VALUE-openrouter.key", env)
        # the reader was handed the EXPANDED key-file path (never a raw ~ path)
        called = list(seen)
        self.assertTrue(any(p.endswith("/.secrets/openrouter.key") for p in called),
                        called)
        self.assertTrue(all("~" not in p for p in called), called)

    def test_env_var_names_agree_with_yaml(self):
        # The os.environ/<VAR> names in the yaml MUST equal the <VAR>= names in
        # the env file, or LiteLLM reads an unset var.
        y = mg.render_gateway_yaml(BASE_CONFIG)
        env = mg.render_gateway_env(BASE_CONFIG, lambda p: "x")
        yaml_vars = set(re.findall(r"os\.environ/([A-Z0-9_]+)", y))
        env_vars = {ln.split("=", 1)[0] for ln in env.splitlines()
                    if "=" in ln and not ln.startswith("#")}
        # every provider var referenced in the yaml is set in the env file
        self.assertTrue(yaml_vars.issubset(env_vars), (yaml_vars, env_vars))
        self.assertIn("LITELLM_MASTER_KEY", env_vars)

    def test_only_referenced_providers_emitted(self):
        # An unused provider entry must NOT force its key into the env file.
        cfg = {"aliases": {"pilot-main": "deepseek/deepseek-chat"},
               "providers": {"openrouter": {"key_file": "~/.secrets/openrouter.key"},
                             "deepseek": {"key_file": "~/.secrets/deepseek.key"}}}
        env = mg.render_gateway_env(cfg, lambda p: "v")
        self.assertIn("DEEPSEEK_KEY=", env)
        self.assertNotIn("OPENROUTER_KEY=", env)

    def test_multi_provider_no_duplicate_var_lines(self):
        # Two DISTINCT providers -> two distinct VAR= lines, no duplicates.
        cfg = {"aliases": {"a": "openrouter/x", "b": "deepseek/y"},
               "providers": {}}
        env = mg.render_gateway_env(cfg, lambda p: "v")
        var_lines = [ln for ln in env.splitlines()
                     if "=" in ln and not ln.startswith("#")]
        names = [ln.split("=", 1)[0] for ln in var_lines]
        self.assertEqual(len(names), len(set(names)), names)
        self.assertIn("OPENROUTER_KEY", names)
        self.assertIn("DEEPSEEK_KEY", names)

    def test_colliding_provider_var_names_refused(self):
        # A2: two providers slugifying to the SAME var must raise, not silently
        # emit a duplicate line (systemd keeps the last -> wrong key).
        cfg = {"aliases": {"a": "vertex-ai/m", "b": "vertex.ai/n"},
               "providers": {}}
        with self.assertRaises(mg.ModelGatewayError):
            mg.render_gateway_env(cfg, lambda p: "v")


class TestRenderUnit(unittest.TestCase):
    def test_template_fields_present(self):
        tmpl = mg.SERVICE_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=%h/.config/airuleset/model-gateway.env", tmpl)
        self.assertIn("litellm --config", tmpl)
        self.assertIn("--host {{HOST_IP}}", tmpl)
        self.assertIn("--port 4000", tmpl)
        self.assertIn("Restart=on-failure", tmpl)
        self.assertIn("[Install]", tmpl)
        # bind IP is a placeholder — NEVER a hardcoded address
        self.assertNotRegex(tmpl, r"--host\s+\d+\.\d+\.\d+\.\d+")

    def test_render_substitutes_host_ip(self):
        out = mg.render_gateway_unit("100.101.214.103")
        self.assertIn("--host 100.101.214.103", out)
        self.assertNotIn("{{HOST_IP}}", out)

    def test_empty_ip_refused(self):
        with self.assertRaises(mg.ModelGatewayError):
            mg.render_gateway_unit("")


class TestSetAlias(unittest.TestCase):
    def _paths(self, d):
        return (str(Path(d) / "map.json"), str(Path(d) / "gw.yaml"))

    def test_seed_from_default_then_apply(self):
        with tempfile.TemporaryDirectory() as d:
            mp, yp = self._paths(d)
            calls = []
            cfg, added = mg.set_alias("pilot-fast", "deepseek/deepseek-chat",
                                      path=mp, yaml_path=yp,
                                      reload_fn=lambda: calls.append(1))
            # seeded default's three tiers survive; the set one is overridden
            self.assertEqual(cfg["aliases"]["pilot-main"], TARGET)
            self.assertEqual(cfg["aliases"]["pilot-fast"], "deepseek/deepseek-chat")
            self.assertEqual(added, "deepseek")  # new provider registered
            self.assertIn("deepseek", cfg["providers"])
            # a NEW provider is NOT reloaded (its key isn't in the env yet, B5)
            self.assertEqual(calls, [])
            # map + yaml really written
            written = json.loads(Path(mp).read_text())
            self.assertEqual(written["aliases"]["pilot-fast"], "deepseek/deepseek-chat")
            self.assertIn('model: "deepseek/deepseek-chat"', Path(yp).read_text())

    def test_roundtrip_updates_existing(self):
        with tempfile.TemporaryDirectory() as d:
            mp, yp = self._paths(d)
            mg.set_alias("pilot-main", TARGET, path=mp, yaml_path=yp,
                         reload_fn=lambda: None)
            new = "openrouter/qwen/qwen-3"
            cfg, added = mg.set_alias("pilot-main", new, path=mp, yaml_path=yp,
                                      reload_fn=lambda: None)
            self.assertIsNone(added)  # openrouter already present
            self.assertEqual(cfg["aliases"]["pilot-main"], new)
            self.assertEqual(json.loads(Path(mp).read_text())["aliases"]["pilot-main"],
                             new)

    def test_bad_target_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            mp, yp = self._paths(d)
            for bad in ("no-slash-here", "provider/", "/model", "prov/ mod",
                        "openrouter/", "/"):
                with self.assertRaises(mg.ModelGatewayError, msg=bad):
                    mg.set_alias("pilot-main", bad, path=mp, yaml_path=yp,
                                 reload_fn=lambda: None)
            with self.assertRaises(mg.ModelGatewayError):
                mg.set_alias("", TARGET, path=mp, yaml_path=yp, reload_fn=lambda: None)

    def test_new_provider_skips_reload(self):
        # B5: adding a NEW provider must NOT restart the live service (its key is
        # not in the env file yet). Repointing an EXISTING provider DOES reload.
        with tempfile.TemporaryDirectory() as d:
            mp, yp = self._paths(d)
            reloads = []
            _cfg, added = mg.set_alias("pilot-fast", "newprov/model",
                                       path=mp, yaml_path=yp,
                                       reload_fn=lambda: reloads.append(1))
            self.assertEqual(added, "newprov")
            self.assertEqual(reloads, [])  # NOT reloaded
            # repoint an existing provider (openrouter seeded by default) -> reload
            _cfg, added2 = mg.set_alias("pilot-main", "openrouter/qwen/qwen-3",
                                        path=mp, yaml_path=yp,
                                        reload_fn=lambda: reloads.append(1))
            self.assertIsNone(added2)
            self.assertEqual(reloads, [1])


class TestWriteIfChanged(unittest.TestCase):
    def test_writes_then_noops_and_reasserts_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "f.txt"
            self.assertTrue(mg._write_if_changed(str(p), "hello\n", mode=0o600))
            self.assertEqual(p.read_text(), "hello\n")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            # identical content -> no write
            self.assertFalse(mg._write_if_changed(str(p), "hello\n", mode=0o600))
            # a drifted mode is re-asserted even on a no-op
            import os as _os
            _os.chmod(str(p), 0o644)
            self.assertFalse(mg._write_if_changed(str(p), "hello\n", mode=0o600))
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            # changed content -> write
            self.assertTrue(mg._write_if_changed(str(p), "world\n"))
            self.assertEqual(p.read_text(), "world\n")

    def test_venv_version_none_when_absent(self):
        # _venv_litellm_version returns None when the venv python is absent
        # (drives the _ensure_venv skip decision) — no real venv touched.
        saved = mg.VENV_DIR
        try:
            with tempfile.TemporaryDirectory() as d:
                mg.VENV_DIR = Path(d) / "no-venv"
                self.assertIsNone(mg._venv_litellm_version())
        finally:
            mg.VENV_DIR = saved


class TestSpend(unittest.TestCase):
    RECORDS = [
        {"ts": "2026-09-16T10:00:00+00:00", "alias": "pilot-fast",
         "cost_usd": 0.001, "total_tokens": 100},
        {"ts": "2026-09-16T12:00:00+00:00", "alias": "pilot-fast",
         "cost_usd": 0.002, "total_tokens": 200},
        {"ts": "2026-09-17T09:00:00+00:00", "alias": "pilot-main",
         "cost_usd": 0.010, "total_tokens": 500},
    ]

    def test_aggregate_by_day_and_alias(self):
        rows = mg.aggregate_spend(self.RECORDS)
        self.assertEqual(len(rows), 2)
        fast = next(r for r in rows if r["alias"] == "pilot-fast")
        self.assertEqual(fast["day"], "2026-09-16")
        self.assertEqual(fast["requests"], 2)
        self.assertAlmostEqual(fast["cost_usd"], 0.003)
        self.assertEqual(fast["total_tokens"], 300)

    def test_since_filter(self):
        rows = mg.aggregate_spend(self.RECORDS, since="2026-09-17")
        self.assertEqual([r["alias"] for r in rows], ["pilot-main"])

    def test_malformed_field_degrades_not_crashes(self):
        rows = mg.aggregate_spend([
            {"ts": "2026-09-17T09:00:00+00:00", "alias": "x",
             "cost_usd": "not-a-number", "total_tokens": None},
        ])
        self.assertEqual(rows[0]["cost_usd"], 0.0)
        self.assertEqual(rows[0]["total_tokens"], 0)

    def test_format_table_totals(self):
        table = mg.format_spend_table(mg.aggregate_spend(self.RECORDS))
        self.assertIn("TOTAL", table)
        self.assertIn("0.0130", table)  # 0.003 + 0.010

    def test_load_records_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spend.jsonl"
            p.write_text('{"ts":"2026-09-17T00:00:00Z","alias":"a","cost_usd":0.5}\n'
                         "not json\n\n"
                         '{"ts":"2026-09-17T01:00:00Z","alias":"b","cost_usd":0.25}\n',
                         encoding="utf-8")
            recs = mg.load_spend_records(str(p))
            self.assertEqual([r["alias"] for r in recs], ["a", "b"])

    def test_load_records_absent_is_empty(self):
        self.assertEqual(mg.load_spend_records(str(Path("/nope/nowhere.jsonl"))), [])


class TestInstallGate(unittest.TestCase):
    def test_should_install_only_controller_with_map(self):
        with tempfile.TemporaryDirectory() as d:
            present = Path(d) / "map.json"
            present.write_text("{}", encoding="utf-8")
            absent = str(Path(d) / "missing.json")
            self.assertFalse(mg.model_gateway_should_install("workstation",
                                                             str(present)))
            self.assertFalse(mg.model_gateway_should_install("shared-stream",
                                                             str(present)))
            self.assertFalse(mg.model_gateway_should_install("controller", absent))
            self.assertTrue(mg.model_gateway_should_install("controller",
                                                            str(present)))

    def test_maybe_setup_noop_off_controller(self):
        called = []
        with tempfile.TemporaryDirectory() as d:
            present = Path(d) / "map.json"
            present.write_text("{}", encoding="utf-8")
            r = mg.maybe_setup_model_gateway(
                box_class="workstation", alias_map_path=str(present),
                setup_fn=lambda **k: called.append(k))
        self.assertFalse(r)
        self.assertEqual(called, [])  # the venv/systemctl path never runs

    def test_maybe_setup_noop_no_map(self):
        called = []
        with tempfile.TemporaryDirectory() as d:
            absent = str(Path(d) / "missing.json")
            r = mg.maybe_setup_model_gateway(
                box_class="controller", alias_map_path=absent,
                setup_fn=lambda **k: called.append(k))
        self.assertFalse(r)
        self.assertEqual(called, [])

    def test_maybe_setup_runs_on_controller_with_map(self):
        called = []

        def fake_setup(**k):
            called.append(k)
            return True  # a real setup returns its observed success

        with tempfile.TemporaryDirectory() as d:
            present = Path(d) / "map.json"
            present.write_text("{}", encoding="utf-8")
            r = mg.maybe_setup_model_gateway(
                box_class="controller", alias_map_path=str(present),
                setup_fn=fake_setup)
        self.assertTrue(r)
        self.assertEqual(len(called), 1)  # exactly the injected setup, once

    def test_maybe_setup_returns_false_when_setup_fails(self):
        # B4: the honest boolean — a failed setup must NOT report success.
        with tempfile.TemporaryDirectory() as d:
            present = Path(d) / "map.json"
            present.write_text("{}", encoding="utf-8")
            r = mg.maybe_setup_model_gateway(
                box_class="controller", alias_map_path=str(present),
                setup_fn=lambda **k: False)
        self.assertFalse(r)


class TestCliDispatch(unittest.TestCase):
    class _Args:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def test_status_not_configured(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            absent = str(Path(d) / "missing.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = mg.cmd_model_gateway(self._Args(mg_action="status",
                                                     map_path=absent))
            self.assertEqual(rc, 0)
            self.assertIn("not configured", buf.getvalue())

    def test_set_via_cli_requires_two_args(self):
        rc = mg.cmd_model_gateway(self._Args(mg_action="set", mg_args=["only-one"]))
        self.assertEqual(rc, 2)  # ModelGatewayError -> exit 2

    def test_unknown_action(self):
        rc = mg.cmd_model_gateway(self._Args(mg_action="frobnicate"))
        self.assertEqual(rc, 2)


class TestSpendLoggerConsistency(unittest.TestCase):
    """The callback WRITER (settings/model_gateway_spend_logger.py, runs in the
    litellm venv) and the READER here must agree on the record shape + the
    callback reference the yaml points at — verified by source inspection so no
    litellm import is needed."""

    def test_callback_ref_matches_writer_module_and_instance(self):
        src = mg.SPEND_LOGGER_TEMPLATE.read_text(encoding="utf-8")
        module, attr = mg.CALLBACK_REF.split(".")
        self.assertEqual(module, mg.SPEND_LOGGER_TEMPLATE.stem)
        self.assertIn(f"{attr} = ", src)

    def test_record_fields_match_parser(self):
        src = mg.SPEND_LOGGER_TEMPLATE.read_text(encoding="utf-8")
        for field in ("ts", "alias", "cost_usd", "prompt_tokens",
                      "completion_tokens", "total_tokens"):
            self.assertIn(f'"{field}"', src)
        # writer + reader agree on the log path basename
        self.assertIn("model-gateway-spend.jsonl", src)
        self.assertEqual(mg.SPEND_LOG_PATH.name, "model-gateway-spend.jsonl")


if __name__ == "__main__":
    unittest.main()
