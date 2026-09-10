"""hooks/block-prod-write-without-notification-budget.sh — issue #979.

Owner hard rule (2026-09-10): PROD Odoo data writes are blocked unless a
notification-budget file exists for the target host and today's date. The
budget file carries expected deltas for mail_mail, mail_notification,
mail_message, mail_activity plus a copy-run reference.

Every test shells out to the REAL shipped hook (never mocked).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "block-prod-write-without-notification-budget.sh"


def payload(command):
    return json.dumps({"tool_input": {"command": command}})


class _Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _make_budget(self, host, keys=None, copy_run="copy-2026-09-10-abc",
                     extra_keys=None, raw_content=None):
        """Create a budget file for host and today in the fake HOME."""
        today = date.today().strftime('%Y%m%d')
        budget_dir = self.home / ".claude" / "prod-write-budget"
        budget_dir.mkdir(parents=True, exist_ok=True)
        path = budget_dir / f"{host}-{today}.json"
        if raw_content is not None:
            path.write_text(raw_content)
            return path
        if keys is None:
            keys = {
                "mail_mail": 0,
                "mail_notification": 121,
                "mail_message": 103,
                "mail_activity": 0,
            }
        data = dict(keys)
        data["copy_run"] = copy_run
        if extra_keys:
            data.update(extra_keys)
        path.write_text(json.dumps(data))
        return path

    def run_hook(self, command, env_extra=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(HOOK)], input=payload(command),
            text=True, capture_output=True, timeout=30, env=env)


# ===========================================================================
# Shape 1: ssh to *-prod — data write blocked without budget
# ===========================================================================

class TestSshProdWriteWithoutBudget(_Runner):
    """The #6084 import shape: ssh to a *-prod host with a data-mutating
    command, no budget file → BLOCKED."""

    def test_docker_compose_exec_odoo_shell_blocked(self):
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_docker_compose_exec_odoo_python_blocked(self):
        r = self.run_hook(
            'ssh user@miva-prod "docker compose exec web odoo python3 import_script.py"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_psql_insert_over_ssh_blocked(self):
        r = self.run_hook(
            'ssh user@montalu-prod "psql -d odoo -c \'INSERT INTO hr_leave VALUES ...\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_python_execute_kw_create_blocked(self):
        r = self.run_hook(
            'ssh user@montalu-prod "python3 -c \'models.execute_kw(db, uid, pwd, '
            '\"hr.leave\", \"create\", [vals])\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_import_script_blocked(self):
        r = self.run_hook(
            'ssh user@montalu-prod "python3 import_leaves.py"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    # --- MAJOR 1 fix: prefix/chain must not bypass ---

    def test_cd_then_ssh_blocked(self):
        """cd ~/x && ssh ... must be blocked (MAJOR 1)."""
        r = self.run_hook(
            'cd ~/x && ssh user@montalu-prod "docker compose exec web odoo shell -c 1"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_timeout_prefix_blocked(self):
        """timeout 60 ssh ... must be blocked (MAJOR 1)."""
        r = self.run_hook(
            'timeout 60 ssh user@montalu-prod "python3 import_leaves.py"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_env_var_prefix_blocked(self):
        """FOO=1 ssh ... must be blocked (MAJOR 1)."""
        r = self.run_hook(
            'FOO=1 ssh user@montalu-prod "python3 import_leaves.py"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)


# ===========================================================================
# Shape 1 with valid budget → passes
# ===========================================================================

class TestSshProdWriteWithBudget(_Runner):
    """Same shapes as above but WITH a valid budget file → allowed."""

    def test_docker_compose_exec_odoo_shell_allowed(self):
        self._make_budget("montalu-prod")
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_psql_insert_over_ssh_allowed(self):
        self._make_budget("montalu-prod")
        r = self.run_hook(
            'ssh user@montalu-prod "psql -d odoo -c \'INSERT INTO hr_leave VALUES ...\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ===========================================================================
# Malformed budget → blocked
# ===========================================================================

class TestMalformedBudget(_Runner):
    """A budget file that is missing a required key or has an empty copy_run
    must still block."""

    def test_missing_mail_activity_key_blocked(self):
        self._make_budget("montalu-prod", keys={
            "mail_mail": 0,
            "mail_notification": 121,
            "mail_message": 103,
            # mail_activity is missing
        })
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)
        self.assertIn("missing keys", r.stderr.lower())

    def test_empty_copy_run_blocked(self):
        self._make_budget("montalu-prod", copy_run="")
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)
        self.assertIn("copy_run", r.stderr.lower())

    def test_invalid_json_blocked(self):
        self._make_budget("montalu-prod", raw_content="not json at all")
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)


# ===========================================================================
# Reads always pass (untouched)
# ===========================================================================

class TestReadWriteComboBlocked(_Runner):
    """MAJOR 2 fix: a script that does BOTH search_read AND write is a write."""

    def test_search_read_then_write_blocked(self):
        r = self.run_hook(
            'ssh user@montalu-prod "python3 -c \'ids = models.execute_kw(db, uid, pwd, '
            '\"hr.leave\", \"search_read\", [[]]); '
            'models.execute_kw(db, uid, pwd, \"hr.leave\", \"write\", [ids, vals])\'"'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)


class TestReadsAlwaysPass(_Runner):
    """Read-only operations must NEVER be blocked, even without a budget."""

    def test_search_read_over_ssh_allowed(self):
        r = self.run_hook(
            'ssh user@montalu-prod "python3 -c \'models.execute_kw(db, uid, pwd, '
            '\"hr.leave\", \"search_read\", [[]])\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_psql_select_over_ssh_allowed(self):
        r = self.run_hook(
            'ssh user@montalu-prod "psql -d odoo -c \'SELECT count(*) FROM hr_leave\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fields_get_over_ssh_allowed(self):
        r = self.run_hook(
            'ssh user@montalu-prod "python3 -c \'models.execute_kw(db, uid, pwd, '
            '\"hr.leave\", \"fields_get\", [])\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_python_import_sys_not_blocked(self):
        """MINOR 4 fix: python3 -c 'import sys' must not be blocked."""
        r = self.run_hook(
            'ssh user@montalu-prod "python3 -c \'import sys; print(sys.version)\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ===========================================================================
# Shape 2: curl to *-prod /json/2/<model>/create|write
# ===========================================================================

class TestCurlProdWrite(_Runner):
    def test_curl_json_create_blocked(self):
        r = self.run_hook(
            'curl -X POST https://montalu-prod.example.com/json/2/hr.leave/create '
            '-H "Content-Type: application/json" -d \'{"vals": {}}\''
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_curl_json_write_blocked(self):
        r = self.run_hook(
            'curl https://miva-prod.internal/json/2/res.partner/write '
            '-d \'{"ids": [1], "vals": {}}\''
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_curl_json_create_with_budget_passes(self):
        self._make_budget("montalu-prod.example.com")
        r = self.run_hook(
            'curl -X POST https://montalu-prod.example.com/json/2/hr.leave/create '
            '-H "Content-Type: application/json" -d \'{"vals": {}}\''
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ===========================================================================
# Shape 3: psql INSERT/UPDATE with -h *-prod
# ===========================================================================

class TestPsqlDirectProd(_Runner):
    def test_psql_insert_direct_blocked(self):
        r = self.run_hook(
            "psql -h montalu-prod -d odoo -c 'INSERT INTO hr_leave (name) VALUES (1)'"
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_psql_update_direct_blocked(self):
        r = self.run_hook(
            "psql --host=miva-prod -d odoo -c 'UPDATE hr_leave SET state=\\'done\\''"
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_psql_select_direct_allowed(self):
        r = self.run_hook(
            "psql -h montalu-prod -d odoo -c 'SELECT * FROM hr_leave'"
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ===========================================================================
# Non-prod hosts are untouched
# ===========================================================================

class TestNonProdHostsUntouched(_Runner):
    def test_ssh_to_dev_host_allowed(self):
        r = self.run_hook(
            'ssh user@montalu-dev "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ssh_to_staging_allowed(self):
        r = self.run_hook(
            'ssh user@montalu-staging "docker compose exec web odoo python3 import.py"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_psql_to_non_prod_host_allowed(self):
        r = self.run_hook(
            "psql -h montalu-copy -d odoo -c 'INSERT INTO hr_leave (name) VALUES (1)'"
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_prod_copy_host_not_blocked(self):
        """montalu-prod-copy is a COPY host, not prod."""
        r = self.run_hook(
            'ssh user@montalu-prod-copy "docker compose exec web odoo shell -c 1"'
        )
        self.assertEqual(r.returncode, 0, r.stderr)


# ===========================================================================
# Bypass marker
# ===========================================================================

class TestBypassMarker(_Runner):
    def test_bypass_without_path_not_accepted(self):
        """A bare '# airuleset:prod-write-ok' without a path argument
        must NOT bypass the hook."""
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"  # airuleset:prod-write-ok'
        )
        # Without a path argument, the bypass should not fire
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_bypass_with_valid_path_passes_and_logs(self):
        """Bypass with a path pointing to a VALID budget file passes."""
        budget_path = self._make_budget("montalu-prod")
        audit_dir = self.home / "devel" / "airuleset" / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"  '
            '# airuleset:prod-write-ok ' + str(budget_path)
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        log_path = audit_dir / "prod-write-budget-bypasses.log"
        self.assertTrue(log_path.exists(), "bypass must be logged")
        log_content = log_path.read_text()
        self.assertIn("prod-write-ok", log_content)

    def test_bypass_with_nonexistent_path_blocked(self):
        """MAJOR 3 fix: bypass with a nonexistent path must NOT bypass."""
        r = self.run_hook(
            'ssh user@montalu-prod "docker compose exec -T web odoo shell -c '
            '\'self.env[\"hr.leave\"].create(vals)\'"  '
            '# airuleset:prod-write-ok /nonexistent/budget.json'
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED", r.stderr)


# ===========================================================================
# Wiring
# ===========================================================================

class TestWiring(_Runner):
    def test_hook_exists(self):
        self.assertTrue(HOOK.exists(),
                        "hooks/block-prod-write-without-notification-budget.sh missing")

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "")
                for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash"
                for h in blk.get("hooks", [])]
        self.assertTrue(
            any("block-prod-write-without-notification-budget.sh" in c for c in cmds),
            "hook not registered under PreToolUse/Bash in settings/hooks.json")

    def test_hook_is_executable(self):
        self.assertTrue(os.access(HOOK, os.X_OK),
                        "hook must be executable")


# ===========================================================================
# Unrelated commands are untouched
# ===========================================================================

class TestUnrelatedCommandsPass(_Runner):
    def test_git_push(self):
        r = self.run_hook("git push origin main")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_local_python(self):
        r = self.run_hook("python3 manage.py runserver")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_scp_to_prod(self):
        r = self.run_hook("scp file.py user@montalu-prod:/tmp/")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
