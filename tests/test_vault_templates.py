"""Locks the OPT-IN command-template lock for `secret exec` (#154).

The decided design (issue #154, comment 5227490255): a name may be LOCKED to
a user-written command template. `secret exec NAME` for a locked name refuses
agent-supplied `-- CMD` argv outright and runs only the template's own
command; an UNTEMPLATED name behaves exactly as it did before this ticket —
`secret exec` for it is completely unaffected by anything in this file.

Named `vault*`, never `secret*`: hooks/block-sensitive-staging.sh refuses to
`git add` any basename containing "secret"/"credential" — the same reason
every other file in this channel is named this way.

No test here writes a template through any importable function. There is
deliberately none — see filedrop/vault.py's own module docstring and
`TestNoWritePrimitiveExists` below. Every fixture below writes the file
DIRECTLY (`Path.write_text`), the same way a human placing it outside any
code this repo ships would.

No test here uses a real credential value.
"""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
from filedrop import vault as st                         # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VAL = "hunter2-fixture-value-154"


class _StoreCase(unittest.TestCase):
    """Every test runs against its OWN tmp store dir — never the real
    `~/.claude/secrets/` (identical fixture to test_vault_channel.py's)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._env = {
            st.SECRETS_DIR_ENV: str(root / "secrets"),
            st.SECRET_LOG_DIR_ENV: str(root / "secret-logs"),
        }
        for k, v in self._env.items():
            os.environ[k] = v
        self.addCleanup(self._restore, dict(os.environ))
        self.assertTrue(str(st.secrets_dir()).startswith(self.tmp.name))

    def _restore(self, _snapshot):
        for k in self._env:
            os.environ.pop(k, None)

    def _write_template(self, name, line):
        """Test-only fixture — writes the file directly. `template_path()`
        already calls `ensure_dir()`, so the store dir need not be created
        separately."""
        p = st.template_path(name)
        p.write_text(line, encoding="utf-8")
        return p

    def _templated_python_line(self, py_code):
        """A template file's ONE line: the current interpreter, `-c`, then
        `py_code` — round-tripped through `shlex.quote` so the file's own
        quoting is correct regardless of what `py_code` contains."""
        return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(py_code))


class TestTemplatePath(_StoreCase):
    def test_lives_next_to_the_value_and_meta_files(self):
        p = st.template_path("DB_PASS")
        self.assertEqual(p.name, "DB_PASS.template")
        self.assertEqual(p.parent, st.value_path("DB_PASS").parent)

    def test_an_invalid_name_is_refused(self):
        with self.assertRaises(st.SecretError):
            st.template_path("../etc/passwd")


class TestHasTemplate(_StoreCase):
    def test_false_when_nothing_is_written(self):
        self.assertFalse(st.has_template("DB_PASS"))

    def test_true_once_the_file_exists(self):
        self._write_template("DB_PASS", "psql -h host -U user")
        self.assertTrue(st.has_template("DB_PASS"))

    def test_an_untemplated_name_stays_false_even_with_a_value_stored(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self.assertFalse(st.has_template("DB_PASS"))


class TestReadTemplate(_StoreCase):
    def test_a_simple_command_splits_into_argv(self):
        self._write_template("DB_PASS", "psql -h host -U user")
        self.assertEqual(st.read_template("DB_PASS"),
                         ["psql", "-h", "host", "-U", "user"])

    def test_quoted_arguments_are_respected(self):
        self._write_template("DB_PASS", 'psql -c "select 1" -h host')
        self.assertEqual(st.read_template("DB_PASS"),
                         ["psql", "-c", "select 1", "-h", "host"])

    def test_a_leading_comment_line_is_ignored(self):
        self._write_template("DB_PASS", "# the prod login\npsql -h host")
        self.assertEqual(st.read_template("DB_PASS"), ["psql", "-h", "host"])

    def test_a_trailing_inline_comment_is_ignored(self):
        self._write_template("DB_PASS", "psql -h host   # prod")
        self.assertEqual(st.read_template("DB_PASS"), ["psql", "-h", "host"])

    def test_an_absent_template_raises_rather_than_returning_none(self):
        with self.assertRaises(st.SecretError):
            st.read_template("NOPE")

    def test_an_empty_template_refuses_rather_than_falling_back(self):
        self._write_template("DB_PASS", "   \n  ")
        with self.assertRaises(st.SecretError):
            st.read_template("DB_PASS")

    def test_a_malformed_template_refuses_rather_than_falling_back(self):
        self._write_template("DB_PASS", 'psql -c "unterminated')
        with self.assertRaises(st.SecretError):
            st.read_template("DB_PASS")

    def test_the_value_is_never_embedded_in_the_template_text(self):
        # Documented invariant, not an emergent property of this test — this
        # is what makes it safe for the value to keep travelling via env/
        # --stdin: asserted here so a future change that starts substituting
        # a placeholder into argv is caught immediately.
        self._write_template("DB_PASS", "psql -h host -U user")
        argv = st.read_template("DB_PASS")
        self.assertNotIn(VAL, argv)
        self.assertNotIn(VAL, " ".join(argv))

    def test_a_non_utf8_template_fails_loud_as_secreterror_not_uncaught(self):
        # Adversarial-review MINOR-1 (#154, second review pass): the
        # docstring promises "NEVER returns None ... must fail LOUD [as
        # SecretError]" for ANY unreadable/malformed template — but
        # `Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError` on
        # invalid bytes, a `ValueError` subclass, NOT an `OSError`. The old
        # `except OSError` therefore missed it, and the exception escaped
        # uncaught into both `secret exec` and `secret status`, which only
        # catch `st.SecretError`. It still failed CLOSED (no argv fallback,
        # since `cmd` was never assigned) but with an ugly traceback instead
        # of the documented refusal — this asserts the documented contract.
        p = st.template_path("DB_PASS")
        p.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        with self.assertRaises(st.SecretError):
            st.read_template("DB_PASS")


class TestTemplateNames(_StoreCase):
    def test_empty_when_nothing_is_templated(self):
        self.assertEqual(st.template_names(), [])

    def test_lists_every_templated_name_sorted(self):
        self._write_template("ZEBRA", "echo z")
        self._write_template("ALPHA", "echo a")
        self.assertEqual(st.template_names(), ["ALPHA", "ZEBRA"])

    def test_a_name_with_only_a_template_and_no_value_still_appears(self):
        self._write_template("DB_PASS", "psql -h host")
        self.assertEqual(st.template_names(), ["DB_PASS"])
        self.assertEqual(st.state("DB_PASS"), "absent")


class TestNoWritePrimitiveExists(unittest.TestCase):
    """The design's own central claim (#154): this module ships no function
    that WRITES a template, on purpose — see filedrop/vault.py's module
    docstring for why. Locked structurally, not just by convention: any NEW
    function whose name suggests writing one is a regression, because it is
    one `python3 -c "from filedrop import vault; vault.<it>(...)"` away from
    the very agent the whole feature exists to constrain — a route no
    text-matching hook (this one or any future one) can ever see."""

    def test_vault_has_no_write_or_set_function_for_templates(self):
        names = [n for n in dir(st) if not n.startswith("_")]
        banned = [n for n in names
                 if "template" in n.lower()
                 and any(w in n.lower()
                        for w in ("write", "set", "store", "save", "create"))]
        self.assertEqual(banned, [],
                         "a write-capable template function exists: %s" % banned)

    def test_cmd_secret_has_no_template_set_action(self):
        self.assertNotIn("template", airuleset.SECRET_ACTIONS)


class TestExecUsesTheTemplateWhenLocked(_StoreCase):
    """CLI-level: `secret exec` for a templated name, as a real process."""

    def _cli(self, *argv, **kw):
        env = dict(os.environ)
        env.update(self._env)
        return subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "secret", *argv],
            capture_output=True, text=True, timeout=90, env=env, **kw)

    def test_the_templated_command_runs_instead_of_agent_argv(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", self._templated_python_line(
            "import os,sys;sys.stdout.write('LEN %d' % len(os.environ['DB_PASS']))"))
        out = self._cli("exec", "DB_PASS")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("LEN %d" % len(VAL), out.stdout)
        self.assertNotIn(VAL, out.stdout + out.stderr)

    def test_agent_supplied_cmd_is_refused_for_a_templated_name(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", self._templated_python_line("pass"))
        out = self._cli("exec", "DB_PASS", "--",
                        sys.executable, "-c",
                        "import os;print(os.environ['DB_PASS'])")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("template", (out.stdout + out.stderr).lower())
        # the refusal must not have run the AGENT's command at all
        self.assertNotIn(VAL, out.stdout + out.stderr)

    def test_an_untemplated_name_is_unaffected(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("exec", "DB_PASS", "--",
                        sys.executable, "-c",
                        "import os;print('LEN', len(os.environ['DB_PASS']))")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("LEN %d" % len(VAL), out.stdout)

    def test_a_malformed_template_refuses_the_whole_exec_loudly(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", "")
        out = self._cli("exec", "DB_PASS")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("DB_PASS", out.stdout + out.stderr)
        self.assertNotIn(VAL, out.stdout + out.stderr)

    def test_env_and_stdin_flags_still_work_for_a_templated_name(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", self._templated_python_line(
            "import sys;sys.stdout.write('GOT %d' % len(sys.stdin.read()))"))
        out = self._cli("exec", "DB_PASS", "--stdin")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("GOT %d" % len(VAL), out.stdout)

    def test_a_templated_name_with_no_value_stored_fails_on_the_value_not_the_lock(self):
        # The template lock resolves BEFORE the value read — the error must
        # be the ordinary "not stored" one, not a template-shaped one, once
        # the lock itself is satisfied.
        self._write_template("DB_PASS", self._templated_python_line("pass"))
        out = self._cli("exec", "DB_PASS")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("DB_PASS", out.stdout + out.stderr)


class TestStatusAndListShowTemplateFlag(_StoreCase):
    def _cli(self, *argv):
        env = dict(os.environ)
        env.update(self._env)
        return subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "secret", *argv],
            capture_output=True, text=True, timeout=90, env=env)

    def test_status_shows_templated_and_its_command(self):
        self._write_template("DB_PASS", "psql -h host -U user")
        out = self._cli("status", "DB_PASS")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("templated", out.stdout.lower())
        self.assertIn("psql", out.stdout)

    def test_status_of_an_untemplated_name_says_nothing_about_it(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        out = self._cli("status", "DB_PASS")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("templated", out.stdout.lower())

    def test_list_shows_a_template_column(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", "psql -h host")
        out = self._cli("list")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("DB_PASS", out.stdout)
        self.assertIn("yes", out.stdout.lower())

    def test_list_shows_a_template_only_name_with_no_value_yet(self):
        self._write_template("PENDING_TEMPLATE", "echo hi")
        out = self._cli("list")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("PENDING_TEMPLATE", out.stdout)
        self.assertIn("absent", out.stdout)

    def test_the_value_never_prints_anywhere(self):
        st.store_value("DB_PASS", VAL.encode(), keep_s=600)
        self._write_template("DB_PASS", "psql -h host")
        for argv in (("status", "DB_PASS"), ("list",)):
            out = self._cli(*argv)
            self.assertNotIn(VAL, out.stdout + out.stderr)


class TemplateWriteLoopholeIsDisclosed(unittest.TestCase):
    """#154's own instruction: the loophole must be honestly addressed in
    what SHIPS, not only in the design comment."""

    def test_the_module_docstring_states_the_write_primitive_absence(self):
        doc = st.__doc__ or ""
        self.assertIn("154", doc)
        low = doc.lower()
        self.assertTrue(
            "write_template" in low or "no write" in low
            or "ships no" in low or "python3 -c" in low,
            "vault.py must state why it ships no template-write function")

    def test_the_cli_docstring_points_at_the_lock(self):
        doc = airuleset.cmd_secret.__doc__ or ""
        self.assertIn("154", doc)
        self.assertIn("template", doc.lower())


if __name__ == "__main__":
    unittest.main()
