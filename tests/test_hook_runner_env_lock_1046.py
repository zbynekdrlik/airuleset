"""#1046 STATIC lock — every hook-level test runs against a HOME it owns.

A hook-level test that spawns a real Stop/PreToolUse hook (``bash
hooks/<x>.sh``) or a gate adapter (``python3 -m gates.<module>``) with the
BOX's real ``HOME`` reads the box's live state: ``gates/questionscope.py``
reads ``~/.claude/tickets-status/<cwd-key>.json`` and BLOCKS a fixture
question that merely names a same-repo ``#N`` whenever this box's owner-court
``U`` is 0 with a fresh cache (the nine controller reds of 2026-09-16),
``gates/designdispatch.py`` / ``gates/designbypost.py`` APPEND fixture lines
to the real ``~/.claude/design-by-gate.log`` (the #1061 lane's
``BYPASS /repo …`` / ``BLOCK #1061 …`` lines), ``notify-discord-*.sh`` read
the real question map / delivery log. The verdict then depends on the box,
never on the code under test, and the push gate's clean-HOME Pass A hides it
until Pass B fails on whatever box lines up.

The shared seam ``tests/_hook_state_cleanup.py::hermetic_hook_env(testcase)``
(the #1028 integration) hands such a runner a fresh empty ``HOME`` so every
box-state-reading branch takes its documented fail-OPEN path. This lock keeps
it that way: an ``ast`` walk over ``tests/test_*.py`` finds every
``subprocess.run/Popen/check_output/call`` whose argv drives a hook or a gate
adapter and requires (1) an explicit ``env=`` keyword on the call AND (2) that
the enclosing file controls ``HOME`` — it references ``hermetic_hook_env`` or
assigns a ``"HOME"`` key. There is NO allowlist: a real-HOME hook read is the
defect, never an exception. Modelled on ``tests/test_hook_deny_stderr.py``'s
scan-not-list discovery, so the class cannot return with a new runner.
"""
import ast
import re
from pathlib import Path
from unittest import TestCase, main

TESTS_DIR = Path(__file__).resolve().parent

# subprocess.<X>(...) exec forms this lock cares about.
_EXEC_FUNCS = {"run", "Popen", "check_output", "call", "check_call"}

# A hook shell script referenced anywhere in an argv literal.
_HOOK_SH_RE = re.compile(r"hooks/[\w.\-]+\.sh")
# A `-m gates.<module>` adapter invocation in an argv literal.
_GATES_MOD_RE = re.compile(r"\bgates\.\w")
# A file that controls HOME for its hook subprocess: it either uses the shared
# hermetic seam or builds its own env with a "HOME" key.
_HOME_KEY_RE = re.compile(r"""["']HOME["']""")


def _test_files():
    return sorted(TESTS_DIR.glob("test_*.py"))


def _is_subprocess_exec(node):
    """True iff ``node`` (an ast.Call) is ``subprocess.run/Popen/...``. Requires
    the ``subprocess.`` prefix — a bare ``run``/``Popen`` is this repo's own
    dependency-injected fake (the false-positive corpus the tmux isolation lock
    documents), never a real spawn."""
    f = node.func
    return (isinstance(f, ast.Attribute)
            and f.attr in _EXEC_FUNCS
            and isinstance(f.value, ast.Name)
            and f.value.id == "subprocess")


def _argv_node(node):
    """The argv AST node of a subprocess exec call: the first positional arg,
    else the ``args=`` keyword."""
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == "args":
            return kw.value
    return None


def _names_and_attrs(node):
    """Every ``Name.id`` and ``Attribute.attr`` reachable under ``node``."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _hook_path_vars(tree):
    """Variable / attribute names bound anywhere in the module to an expression
    whose source names a ``hooks`` path (``HOOK = ROOT / "hooks" / "x.sh"``,
    ``self.HOOK = …``, ``HOOKS = ROOT / "hooks"``). A subprocess argv that
    references one of these drives a hook even though the literal path is not at
    the call site."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        try:
            src = ast.unparse(value)
        except Exception:
            continue
        if "hooks" not in src and not _HOOK_SH_RE.search(src):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, ast.Attribute):
                names.add(t.attr)
    return names


def _argv_drives_hook(argv, hook_vars):
    """True iff the argv node drives a hook script or a gate adapter."""
    if argv is None:
        return False
    try:
        src = ast.unparse(argv)
    except Exception:
        return False
    if _HOOK_SH_RE.search(src):
        return True
    if "-m" in src and _GATES_MOD_RE.search(src):
        return True
    # A gates.<mod> string constant anywhere in the argv (covers the
    # `[sys.executable, "-P", "-m", "gates.designdispatch"]` shape).
    for sub in ast.walk(argv):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value.startswith("gates.") or _HOOK_SH_RE.search(sub.value):
                return True
    if _names_and_attrs(argv) & hook_vars:
        return True
    return False


def _call_has_env(node):
    return any(kw.arg == "env" for kw in node.keywords)


def _file_controls_home(source):
    return ("hermetic_hook_env" in source) or bool(_HOME_KEY_RE.search(source))


def _offenders():
    """List of ``file:line: reason`` for every hook-driving subprocess exec that
    is not run against a HOME the test owns."""
    out = []
    for path in _test_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        hook_vars = _hook_path_vars(tree)
        controls_home = _file_controls_home(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_subprocess_exec(node):
                continue
            argv = _argv_node(node)
            if not _argv_drives_hook(argv, hook_vars):
                continue
            rel = path.name
            if not _call_has_env(node):
                out.append("%s:%d: subprocess exec drives a hook without an "
                           "explicit env= (needs hermetic_hook_env(self))"
                           % (rel, node.lineno))
            elif not controls_home:
                out.append("%s:%d: subprocess exec passes env= but the file "
                           "never controls HOME (no hermetic_hook_env, no "
                           '"HOME" key)' % (rel, node.lineno))
    return sorted(out)


class TestHookRunnerEnvLock(TestCase):
    def test_discovery_is_non_trivial(self):
        """Sanity: the scan actually sees the hook-level suite (never a silently
        empty walk that passes vacuously)."""
        drivers = 0
        for path in _test_files():
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            hook_vars = _hook_path_vars(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and _is_subprocess_exec(node)
                        and _argv_drives_hook(_argv_node(node), hook_vars)):
                    drivers += 1
        self.assertGreater(drivers, 40,
                           "expected many hook-driving subprocess execs; the "
                           "discovery is broken")

    def test_every_hook_runner_owns_its_home(self):
        """Every hook-driving subprocess exec runs against a HOME the test owns
        (an explicit env= AND the file controls HOME). No allowlist."""
        offenders = _offenders()
        self.assertEqual(
            offenders, [],
            "hook-level runners read the box's real HOME (a box-state-"
            "dependent verdict + real ~/.claude writes). Switch each to "
            "hermetic_hook_env(self) (or keep an own tmp-HOME env with a "
            "one-line comment):\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
