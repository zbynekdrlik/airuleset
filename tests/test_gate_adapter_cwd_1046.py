"""#1046 DYNAMIC lock — a gate-adapter hook imports the airuleset repo package,
never a package that happens to sit in the caller's cwd.

A hook that runs ``python3 -m gates.<module>`` or ``python3 -c 'import
cli_concurrency…'`` puts the CALLER's cwd on ``sys.path`` unless the interpreter
is started with ``-P`` (PYTHONSAFEPATH, Python 3.11+): for ``-m`` the cwd is
``sys.path[0]``, for ``-c`` likewise. Inside a lane worktree whose base predates
the module (or carries an older copy), the stale package shadows the installed
one → ``ModuleNotFoundError`` → the hook FAILS CLOSED and every Bash call in
that worktree is blocked, including the ``git merge --no-ff main`` that would
bring the module in (the #1048-lane wedge). Every ``-m gates.`` adapter already
runs ``python3 -P -m gates.<module>``; the residual was
``hooks/block-dispatch-over-wdrain.sh``'s ``python3 -c`` without ``-P``.

This lock runs every adapter from a HOSTILE cwd that carries a stale
``gates/__init__.py`` and a stale ``cli_concurrency.py`` (both write a marker on
import; the ``cli_concurrency`` stale also ``raise``s), under a hermetic HOME,
and asserts the adapter (1) exits cleanly (0 or a real gate BLOCK 2, never a
crash), (2) leaves no ``Traceback`` / ``ModuleNotFoundError`` / ``stale`` on
stderr, and (3) never imports either stale module (no marker). The marker is the
teeth that survive ``block-dispatch-over-wdrain``'s ``2>/dev/null`` — the stale
import is invisible on stderr there, but the marker file proves it happened.
Targets are discovered by scan (``grep -l -- '-m gates.' hooks/*.sh`` plus
``block-dispatch-over-wdrain.sh``), never a hand-kept list.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
WDRAIN = "block-dispatch-over-wdrain.sh"

_STALE_GATES = "import os\nopen(os.environ['A1046_MARK_G'], 'w').write('g')\n"
_STALE_CC = ("import os\nopen(os.environ['A1046_MARK_CC'], 'w').write('c')\n"
             "raise ImportError('stale')\n")

# Payloads: the Agent/autopilot-worker shape drives block-dispatch-over-wdrain
# all the way to its sequential-mode `-c` adapter (line ~139); the Bash shape
# exercises the read-only gate adapters. Both are sent to every hook — a hook
# that fails-open early on one simply passes it.
_PAYLOADS = {
    "agent": {"tool_name": "Agent",
              "tool_input": {"subagent_type": "autopilot-worker",
                             "prompt": "do the benign thing now"},
              "cwd": "/tmp/a1046-benign-cwd"},
    "bash": {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "cwd": "/tmp/a1046-benign-cwd", "session_id": "a1046",
             "prompt": "benign", "last_assistant_message": "benign"},
}


# Repo top-level modules (plus the package dirs) — a hook that imports one of
# these from a `python3 -c`/`-` is cwd-shadowable and needs `-P` too, not only
# the `-m gates.` adapters (review #1046: block-main-implementation.sh imports
# gates.shellcmd via `-c`, block-dispatch-over-wdrain.sh imports cli_concurrency).
_REPO_MODS = {p.stem for p in ROOT.glob("*.py")} | {"gates", "watchdog", "notify"}
_PY_DASH_RE = re.compile(r"python3\s+(?:-P\s+)?(?:-c\b|-\s|-$|-\")")


def _imports_repo_module(text):
    for name in re.findall(r"\b(?:import|from)\s+([A-Za-z_]\w*)", text):
        if name in _REPO_MODS:
            return True
    return False


def _adapter_hooks():
    """Every gate-adapter hook that resolves a REPO module from a subprocess
    interpreter — discovered by scan, never a hand-kept list: the `-m gates.`
    adapters, plus any hook running `python3 -c`/`-` (stdin) that imports a repo
    module (block-main-implementation.sh, the stdin heredocs), plus wdrain."""
    out = []
    for p in sorted(HOOKS.glob("*.sh")):
        text = p.read_text(encoding="utf-8")
        if re.search(r"-m gates\.", text) or (
                _PY_DASH_RE.search(text) and _imports_repo_module(text)):
            out.append(p.name)
    if WDRAIN not in out:
        out.append(WDRAIN)
    return out


class TestGateAdapterIgnoresStaleCwd(TestCase):
    def _hostile_cwd(self):
        d = tempfile.mkdtemp(prefix="a1046-hostile-")
        self.addCleanup(shutil.rmtree, d, True)
        (Path(d) / "gates").mkdir()
        (Path(d) / "gates" / "__init__.py").write_text(_STALE_GATES)
        (Path(d) / "cli_concurrency.py").write_text(_STALE_CC)
        return d

    def test_discovery_covers_wdrain_and_the_m_gates_adapters(self):
        hooks = _adapter_hooks()
        self.assertIn(WDRAIN, hooks)
        self.assertGreaterEqual(
            len(hooks), 10,
            "expected the -m gates. adapter family plus the -c/- repo importers")

    def test_adapters_never_resolve_a_repo_module_from_the_caller_cwd(self):
        offenders = []
        for hook in _adapter_hooks():
            for pname, payload in _PAYLOADS.items():
                d = self._hostile_cwd()
                mg = os.path.join(d, "MARK_G")
                mc = os.path.join(d, "MARK_CC")
                env = hermetic_hook_env(self, A1046_MARK_G=mg, A1046_MARK_CC=mc)
                r = subprocess.run(
                    ["bash", str(HOOKS / hook)], input=json.dumps(payload),
                    text=True, capture_output=True, cwd=d, env=env, timeout=60)
                where = "%s [%s]" % (hook, pname)
                if r.returncode not in (0, 2):
                    offenders.append("%s: exit %d (crash, not a clean verdict); "
                                     "stderr=%r" % (where, r.returncode,
                                                    r.stderr[:200]))
                if re.search(r"Traceback|ModuleNotFoundError|stale", r.stderr):
                    offenders.append("%s: error text on stderr: %r"
                                     % (where, r.stderr[:200]))
                if os.path.exists(mg) or os.path.exists(mc):
                    which = "gates" if os.path.exists(mg) else "cli_concurrency"
                    offenders.append(
                        "%s: imported the STALE %s from the caller cwd — the "
                        "adapter needs `python3 -P` so cwd is off sys.path"
                        % (where, which))
        self.assertEqual(offenders, [], "\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    main()
