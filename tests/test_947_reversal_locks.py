"""Tests locking the #947 reversal — session health is OBSERVATION-ONLY.

RED-first: these tests FAIL against the pre-reversal code (which has /exit,
execute_exit, execute_relaunch, drop-in provisioning, session_restart_enabled
param), then GREEN after the reversal removes the action path.

Owner directive 2026-09-10: "regresia je ze nieco nam pcha exit do vsetkych
targetov a ked prideme rano je vsetko seknute … Ja som vobec taketo random
existovanie target claude urcite nikdy neschvalil a som proti tomu aby sa
nieco take dialo!!!"
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestNoExitStringInHealthModule:
    """The health observation module must not contain '/exit' as executable code."""

    def test_no_exit_command_string(self):
        """The string '/exit' must not appear in the module as executable code."""
        mod_path = REPO / "watchdog" / "session_health_observe.py"
        if not mod_path.exists():
            mod_path = REPO / "watchdog" / "session_restart.py"
        src = mod_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstring_nodes.add(id(body[0].value))
        exit_strings = [
            (n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstring_nodes
            and "/exit" in n.value
        ]
        assert exit_strings == [], (
            "the health module still contains '/exit' as executable code "
            "(#947 reversal): %s" % exit_strings
        )


class TestNoSendKeysInHealthModule:
    """The health module must not contain any tmux send-keys capability."""

    def test_no_send_keys_in_code(self):
        """send-keys must not appear as executable code (docstrings OK)."""
        mod_path = REPO / "watchdog" / "session_health_observe.py"
        if not mod_path.exists():
            mod_path = REPO / "watchdog" / "session_restart.py"
        src = mod_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstring_nodes.add(id(body[0].value))
        sendkeys_strings = [
            (n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstring_nodes
            and ("send-keys" in n.value or "send_keys" in n.value)
        ]
        assert sendkeys_strings == [], (
            "the health module references send-keys as executable code "
            "(#947 reversal): %s" % sendkeys_strings
        )

    def test_no_execute_exit_function(self):
        mod_path = REPO / "watchdog" / "session_health_observe.py"
        if not mod_path.exists():
            mod_path = REPO / "watchdog" / "session_restart.py"
        src = mod_path.read_text(encoding="utf-8")
        assert "def execute_exit" not in src, (
            "execute_exit still exists (#947 reversal)"
        )

    def test_no_execute_relaunch_function(self):
        mod_path = REPO / "watchdog" / "session_health_observe.py"
        if not mod_path.exists():
            mod_path = REPO / "watchdog" / "session_restart.py"
        src = mod_path.read_text(encoding="utf-8")
        assert "def execute_relaunch" not in src, (
            "execute_relaunch still exists (#947 reversal)"
        )


class TestNoDropinProvisioningInInstall:
    """cmd_install must not render a session-restart drop-in."""

    def test_no_render_session_restart_dropin(self):
        src = (REPO / "cli_filedrop_watchdog.py").read_text(encoding="utf-8")
        assert "def render_session_restart_dropin" not in src, (
            "render_session_restart_dropin still exists (#947 reversal)"
        )

    def test_no_setup_session_restart_dropin(self):
        src = (REPO / "cli_filedrop_watchdog.py").read_text(encoding="utf-8")
        assert "def setup_session_restart_dropin" not in src, (
            "setup_session_restart_dropin still exists (#947 reversal)"
        )


class TestHealthObservationJobIsPassive:
    """The run_once wiring for job 46 must be purely observational."""

    def test_no_session_restart_enabled_param(self):
        src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        assert "session_restart_enabled" not in src, (
            "session_restart_enabled param still exists (#947 reversal)"
        )

    def test_job_46_label_not_session_restart(self):
        src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        assert '_add("session_restart"' not in src, (
            'job 46 still wired as "session_restart" (#947 reversal)'
        )
