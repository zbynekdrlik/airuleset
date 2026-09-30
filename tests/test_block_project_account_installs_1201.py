"""#1201: hooks/block-project-account-local-installs.sh blocks a per-account
developer-tool install, but ONLY on a declared project account.

The invoking user is controlled per test by a PATH ``id`` stub (the hook reads
``id -un``): ``fohmixer`` is a ``SERVICE_ACCOUNTS`` member, ``newlevel`` is not.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-project-account-local-installs.sh"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

BLOCKED = [
    "rustup toolchain install 1.98.1",
    "rustup install stable",
    "rustup update",
    "rustup default nightly",
    "rustup component add rustfmt",
    "rustup target add wasm32-unknown-unknown",
    "rustup self update",
    "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
    "wget -qO- https://sh.rustup.rs | sh",
    "./rustup-init -y",
    "pipx install ruff",
    "pipx install ruff==0.16.2",
    "pipx inject ruff black",
    "uv tool install ruff",
    "pip install --user ruff",
    "pip3 install ruff --break-system-packages",
    "python3 -m pip install --user websockets",
    "npx playwright install chromium",
    "npx -y playwright@1.58.2 install --with-deps chromium webkit",
    "npx --yes @playwright/test install",
    "cd e2e && npx playwright install",
    "npm exec playwright install",
    "pnpm dlx playwright install",
    "python3 -m playwright install chromium",
    "playwright install webkit",
    "npm i -g typescript",
    "npm install --global @anthropic-ai/claude-code",
    "npm install --location=global x",
    "yarn global add x",
    "pnpm add -g x",
    "cargo install cargo-mutants",
    "cargo +nightly install x",
    "cargo binstall x",
    "bash -c 'cargo install x'",
    "sudo -E pipx install ruff",
    "FOO=1 timeout 60 npx playwright install",
    "echo $(pipx install ruff)",
]

ALLOWED = [
    "rustup show",
    "rustup which rustfmt",
    "rustup default",
    "rustup component list --installed",
    "cargo fmt --all",
    "cargo update --workspace",
    "cargo metadata --format-version 1",
    "pipx install --global ruff",
    "pipx list",
    "python3 -m venv .venv",
    ".venv/bin/pip install -r requirements-dev.txt",
    "pip install -e .",
    "npm install",
    "npm ci",
    "cd e2e && npx playwright test --list",
    "npx playwright test",
    "ruff check .",
    "git commit -m 'block cargo install and npx playwright install'",
    "gh issue comment 1 --body-file - <<'EOF'\ncargo install x\npipx install y\nEOF",
    "grep -rn 'pip install --user' docs/",
]


def run_hook(cmd, user="fohmixer", payload=None):
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "id"
        stub.write_text("#!/usr/bin/env bash\necho %s\n" % user)
        stub.chmod(0o755)
        body = payload if payload is not None else json.dumps(
            {"tool_input": {"command": cmd}})
        return subprocess.run(["bash", str(HOOK)], input=body, text=True,
                              capture_output=True,
                              env={"PATH": "%s:/usr/bin:/bin" % tmp,
                                   "HOME": tmp})


class TestProjectAccountBlocks(unittest.TestCase):

    def test_blocks_every_install_shape_on_a_project_account(self):
        for cmd in BLOCKED:
            r = run_hook(cmd)
            self.assertEqual(r.returncode, 2, "expected BLOCK: %r\n%s" % (cmd, r.stderr))
            self.assertIn("gh issue create -R zbynekdrlik/airuleset", r.stderr)
            self.assertIn("nástroj pridá airuleset", r.stderr)

    def test_allows_the_non_install_shapes_and_repo_venvs(self):
        for cmd in ALLOWED:
            r = run_hook(cmd)
            self.assertEqual(r.returncode, 0, "expected ALLOW: %r\n%s" % (cmd, r.stderr))

    def test_every_other_account_is_a_no_op(self):
        for user in ("newlevel", "airuleset", "montalu3"):
            for cmd in BLOCKED[:6] + ["cargo install x", "npx playwright install"]:
                r = run_hook(cmd, user=user)
                self.assertEqual(r.returncode, 0, (user, cmd, r.stderr))

    def test_the_block_names_the_account_and_the_shape(self):
        r = run_hook("cargo install cargo-mutants")
        self.assertIn("cargo install", r.stderr)
        self.assertIn('SERVICE_ACCOUNTS["fohmixer"]["tools"]', r.stderr)

    def test_malformed_payload_fails_open(self):
        r = run_hook("", payload="{not json pipx install ruff")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_declared_account_is_guarded(self):
        import cli_account_bootstrap as bootstrap
        for account in bootstrap.SERVICE_ACCOUNTS:
            self.assertEqual(run_hook("cargo install x", user=account).returncode,
                             2, account)


class TestRegistration(unittest.TestCase):

    def test_registered_as_a_bash_pretooluse_hook(self):
        data = json.loads((ROOT / "settings" / "hooks.json").read_text())
        cmds = [h["command"] for grp in data["hooks"]["PreToolUse"]
                if grp["matcher"] == "Bash" for h in grp["hooks"]]
        self.assertIn(
            "bash ~/devel/airuleset/hooks/block-project-account-local-installs.sh",
            cmds)

    def test_hook_is_executable(self):
        self.assertTrue(HOOK.stat().st_mode & 0o111)


class TestClassifier(unittest.TestCase):

    def test_heredoc_bodies_are_documentation(self):
        import project_account_install_guard as g
        self.assertEqual(g.classify("cat > x <<'EOF'\nrustup install stable\nEOF"), "")
        self.assertEqual(g.classify("cat > x <<'EOF'\nhi\nEOF\ncargo install x"),
                         "cargo install")

    def test_nested_shells_are_classified(self):
        import project_account_install_guard as g
        self.assertEqual(g.classify("sh -c \"bash -c 'pipx install ruff'\""),
                         "pipx install")


if __name__ == "__main__":
    unittest.main()
