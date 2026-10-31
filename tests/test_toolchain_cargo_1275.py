"""#1275: a project account declares a `cargo install` tool, built once for the
box into the shared cargo bin, because the #1201 hook refuses a local install."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_project_toolchain as tc  # noqa: E402


class TestCargoKind(unittest.TestCase):

    def test_parse(self):
        self.assertEqual(tc.parse_tool("cargo:trunk")[1].group("crate"), "trunk")
        self.assertEqual(tc.parse_tool("cargo:trunk@0.21.14")[1].group("ver"), "0.21.14")
        with self.assertRaises(ValueError):
            tc.parse_tool("cargo:$(id)")

    def test_plan_and_render(self):
        spec = {"tools": ["cargo:trunk@0.21.14", "cargo:wasm-pack"]}
        self.assertEqual(tc.plan(spec)["cargo"], [("trunk", "0.21.14"), ("wasm-pack", None)])
        step = tc.render_system_step(spec)
        self.assertIn("cargo +%s install --locked --version 0.21.14 trunk"
                      % tc.DEFAULT_TOOLCHAIN, step)
        self.assertIn("cargo +%s install --locked wasm-pack" % tc.DEFAULT_TOOLCHAIN, step)

    def test_built_before_the_read_only_chmod(self):
        step = tc.render_system_step({"tools": ["cargo:trunk"]})
        self.assertLess(step.index("install --locked"), step.index("chmod -R a+rX,go-w"))

    def test_an_existing_binary_is_not_rebuilt(self):
        step = tc.render_system_step({"tools": ["cargo:trunk"]})
        self.assertIn("[ -x /opt/rust/cargo/bin/trunk ] ||", step)

    def test_no_cargo_tool_renders_no_install(self):
        self.assertNotIn("install --locked", tc.render_system_step({"tools": []}))

    def test_render_is_valid_bash(self):
        step = tc.render_system_step({"tools": ["cargo:trunk"]})
        self.assertEqual(subprocess.run(["bash", "-n"], input=step, text=True).returncode, 0)

    def test_dcontroller_declares_trunk(self):
        self.assertIn("cargo:trunk", bootstrap.account_spec("dcontroller")["tools"])


if __name__ == "__main__":
    unittest.main()
