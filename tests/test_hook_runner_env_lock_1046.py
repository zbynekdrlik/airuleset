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
# hermetic seam or builds its own env with a HOME override — a "HOME" dict key
# (``env["HOME"] = …`` / ``{"HOME": …}``) OR a ``HOME=`` keyword
# (``dict(os.environ, HOME=tmp)``). Both are legitimate own-HOME patterns.
_HOME_KEY_RE = re.compile(r"""(?:["']HOME["']|\bHOME\s*=)""")


def _test_files():
    return sorted(TESTS_DIR.glob("test_*.py"))


# Deferred wrappers that run their arg later (teardown / interpreter exit): the
# exec function is arg 0, the argv is arg 1 (the #734 blind spot the tmux
# isolation lock documents).
_DEFERRED_WRAPPERS = {"addCleanup", "register"}


def _bare_exec_names(tree):
    """subprocess exec names brought into the module namespace by
    ``from subprocess import run[, Popen, ...]`` — so a bare ``run(...)`` call is
    a real spawn, not this repo's dependency-injected fake. Empty unless the file
    actually does such an import (keeps the tmux-lock false-positive corpus out
    of files that never import bare)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                if a.name in _EXEC_FUNCS:
                    names.add(a.asname or a.name)
    return names


def _is_subprocess_exec(node, bare_names=frozenset()):
    """True iff ``node`` (an ast.Call) is a real subprocess spawn:
    ``subprocess.run/Popen/...`` (the ``subprocess.`` prefix), or a bare
    ``run``/``Popen`` ONLY when the file imported it from subprocess."""
    f = node.func
    if (isinstance(f, ast.Attribute) and f.attr in _EXEC_FUNCS
            and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
        return True
    return isinstance(f, ast.Name) and f.id in bare_names


def _is_exec_ref(node, bare_names=frozenset()):
    """True iff ``node`` is a bare REFERENCE (not a call) to a subprocess exec —
    ``subprocess.run`` as a callable, or an imported bare name."""
    if (isinstance(node, ast.Attribute) and node.attr in _EXEC_FUNCS
            and isinstance(node.value, ast.Name) and node.value.id == "subprocess"):
        return True
    return isinstance(node, ast.Name) and node.id in bare_names


def _exec_sites(tree, bare_names):
    """Yield ``(call_for_env, argv_node)`` for every real subprocess exec:
      * DIRECT   ``subprocess.run(ARGV, …)``                    -> (call, args[0])
      * DEFERRED ``addCleanup(subprocess.run, ARGV, …)`` /
                 ``register(subprocess.run, ARGV, …)``          -> (call, args[1])
    ``call_for_env`` is the node whose ``env=`` keyword governs the spawn (the
    exec call itself for direct, the wrapper for deferred)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_subprocess_exec(node, bare_names):
            yield node, _argv_node(node)
            continue
        f = node.func
        wname = (f.attr if isinstance(f, ast.Attribute)
                 else f.id if isinstance(f, ast.Name) else None)
        if (wname in _DEFERRED_WRAPPERS and len(node.args) >= 2
                and _is_exec_ref(node.args[0], bare_names)):
            yield node, node.args[1]


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
    that resolves to a hook path — ``HOOK = ROOT / "hooks" / "x.sh"``,
    ``HOOKS = ROOT / "hooks"``, ``NUDGE = HOOKS / "x.sh"`` (derived from another
    hook-path var), ``self.HOOK = …``. A subprocess argv that references one of
    these drives a hook even though the literal path is not at the call site.

    Case-insensitive on ``hooks`` (a `HOOKS = REPO / "hooks"` parent counts),
    matches a bare ``*.sh`` filename constant, and is TRANSITIVE (a var built
    from an already-recorded hook-path var, computed to a fixpoint) — the
    NUDGE/CIBLOCK derived-var gap review #1046 exposed."""
    assigns = []       # (targets, value_src, referenced_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        try:
            src = ast.unparse(value)
        except Exception:
            continue
        assigns.append((targets, src, _names_and_attrs(value)))

    def _target_names(targets):
        for t in targets:
            if isinstance(t, ast.Name):
                yield t.id
            elif isinstance(t, ast.Attribute):
                yield t.attr

    names = set()
    for targets, src, _refs in assigns:
        # A `hooks` path segment (case-insensitive, so a `HOOKS = REPO/"hooks"`
        # parent counts) or a literal `hooks/x.sh`. A bare `*.sh` filename is
        # NOT enough on its own — that over-matched a rendered `script.sh` a test
        # runs `bash -n` on (test_volume_provision_999) — derived hook-path vars
        # (`NUDGE = HOOKS / "x.sh"`) are caught by the transitive closure below.
        if "hooks" in src.lower() or _HOOK_SH_RE.search(src):
            names.update(_target_names(targets))
    # transitive closure: a var built from a known hook-path var is one too
    changed = True
    while changed:
        changed = False
        for targets, _src, refs in assigns:
            if refs & names:
                for n in _target_names(targets):
                    if n not in names:
                        names.add(n)
                        changed = True
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
    # `[sys.executable, "-P", "-m", "gates.designdispatch"]` shape), OR a bare
    # `"hooks"` path segment (covers the inline-join basename shape
    # `["bash", str(REPO_DIR / "hooks" / self.HOOK)]` where HOOK is a bare
    # basename and no file-wide hooks-path var records it — review #1046).
    for sub in ast.walk(argv):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if (sub.value == "hooks" or sub.value.startswith("gates.")
                    or _HOOK_SH_RE.search(sub.value)):
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
        bare_names = _bare_exec_names(tree)
        controls_home = _file_controls_home(source)
        for call, argv in _exec_sites(tree, bare_names):
            if not _argv_drives_hook(argv, hook_vars):
                continue
            rel = path.name
            if not _call_has_env(call):
                out.append("%s:%d: subprocess exec drives a hook without an "
                           "explicit env= (needs hermetic_hook_env(self))"
                           % (rel, call.lineno))
            elif not controls_home:
                out.append("%s:%d: subprocess exec passes env= but the file "
                           "never controls HOME (no hermetic_hook_env, no "
                           '"HOME" key)' % (rel, call.lineno))
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
            bare_names = _bare_exec_names(tree)
            for _call, argv in _exec_sites(tree, bare_names):
                if _argv_drives_hook(argv, hook_vars):
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
