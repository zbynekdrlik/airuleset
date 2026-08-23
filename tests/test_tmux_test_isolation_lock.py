"""Safety lock for the #613 incident — every DESTRUCTIVE / global-mutating
tmux invocation under `tests/` or `hooks/` that ACTUALLY EXECUTES (via
`subprocess.run`/`.Popen`/`.call`/`.check_call`/`.check_output`, or a shell
script under `hooks/`) must carry an EXPLICIT socket selector (`-S <path>`
or `-L <name>`) on the SAME invocation.

Incident (2026-08-23, building tests/test_webterm_ctrlbw_darkening.py): a
test harness spun up what it believed was an "isolated" tmux server by
setting `TMUX_TMPDIR` alone. That is NOT isolation — a tmux CLIENT resolves
its socket via `$TMUX` (inherited from the very pane the test itself runs
inside, since Claude Code's own session lives in a real tmux pane) BEFORE
it ever looks at `TMUX_TMPDIR`; `TMUX_TMPDIR` only applies once `$TMUX` is
unset. So `set-option -g window-size manual` silently rewrote the OWNER's
REAL global tmux options, and the test's own teardown `kill-server`
(carrying no `-S`) killed his REAL, live tmux server outright — with his
live Claude Code session, running inside that same server, dying with it.
It happened twice before the box was rebooted.

`-S <path>` and `-L <name>` are the only two tmux socket selectors that are
resolved from the CLI ARGUMENT itself, which always overrides `$TMUX`
(tmux's own documented precedence: an explicit `-S`/`-L` flag beats `$TMUX`
beats `TMUX_TMPDIR`/the factory default) — so either one, present on the
SAME invocation, is what this lock requires; relying on the environment
(`TMUX_TMPDIR`, or nothing at all) is exactly the shape that caused the
incident and is what this lock forbids for the three subcommands capable
of doing REAL damage to a live server: `kill-server` (ends the whole
server, every session, every pane), `set-option -g` (rewrites GLOBAL
options a live session depends on), and `new-session` (session-name
collisions aside, still a mutation against whatever server the socket
resolves to).

SCOPED TO ACTUAL EXECUTION, not every mention. A first draft of this lock
naively scanned every List/Tuple literal and every non-docstring string
constant, and immediately produced ~19 false positives — this repo's own
established, SAFE test pattern of dependency-injecting a FAKE `run`
callable (`def run(argv): calls.append(argv); ...`, `sentinel_path=`,
`ensure_stream_tmux_session(..., run=run, ...)` in
tests/test_dev_env_provisioning.py / tests/test_airuleset.py) records the
argv the PRODUCTION code *would* pass to a real `subprocess.run` — it never
spawns a process at all — and `assertIn("exec tmux new-session -A -s",
text)` merely checks GENERATED bashrc/conf TEXT the code writes to disk
(text that, like cli_webterm.py's own `_ATTACH_BODY`, is legitimately
meant to run WITHOUT `-S`/`-L` once installed on a real managed box). Both
shapes contain the banned strings and neither executes anything. So this
lock looks ONLY at the first argument of a REAL `subprocess.run` /
`.Popen` / `.call` / `.check_call` / `.check_output` call (Python) or a
non-comment line of a `.sh` file under `hooks/` (a shell script IS
executable in its entirety) — never a bare list/string literal sitting in
an assertion, a recorded-calls comparison, or generated-text prose.

Covers BOTH invocation shapes at that narrowed scope:
  * Python list/tuple ARGV literals passed to a real subprocess call —
    `subprocess.run(["tmux", "-S", sock, "kill-server"])` — via an AST
    walk (`_list_violations`), so a `*args`-spliced dynamic call (this
    repo's own `self.tmux(*args)`-style wrapper, whose ONE definition site
    already carries `-S`/`-L` literally) is correctly exempt: the
    destructive subcommand string is invisible to a static reader at the
    CALL site, and the wrapper's own DEFINITION site is checked on its own
    literal list, which does carry the flag.
  * Shell COMMAND-STRING invocations passed to a real subprocess call with
    `shell=True`, or embedded in a `["sh", "-c", <str>]` argv, or a whole
    `.sh` file under `hooks/` — via a comment-stripped clause scan
    (`_clause_violations`), bounded the way a real shell would parse
    clauses (`;`, `&&`, `||`, `|`, newline), so a `-S` earlier in the SAME
    pipeline/command-list still guards a subcommand later in that clause,
    but never reaches across an unrelated clause.

Scope is deliberately `tests/` + `hooks/` ONLY — PRODUCTION code (e.g.
cli_webterm.py's own `_ATTACH_BODY`, or `STREAM_SSH_ATTACH_BLOCK`) is
SUPPOSED to target the box's real, managed tmux server with no `-S`/`-L`
at all; that is its entire purpose, never a bug this lock should flag —
and it is exactly what the narrowing above already keeps out, since that
text is asserted-about, never executed, inside `tests/`. This file's own
path is excluded from the corpus it scans — its docstring, by necessity,
spells out the banned subcommand strings in prose, which would otherwise
trip its own scan; see tests/test_no_session_kill.py's identical,
already-established self-exclusion for the same reason.
"""
import ast
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SELF = Path(__file__).resolve()

_SCAN_DIRS = ("tests", "hooks")
# Canonical destructive subcommands + the tmux-recognized SHORT ALIASES a
# first draft of this lock missed (adversarial review on #613: confirmed
# live via `tmux list-commands` -- `new-session` aliases to `new`,
# `set-option` aliases to `set`; `kill-server` has no alias at all, so it
# stays a plain literal). `new`/`set` alone are risky bare substrings (an
# unrelated word merely CONTAINING them), so every pattern here is matched
# as a whole WORD (`\b`), never plain `in` containment.
_DESTRUCTIVE_SUBCOMMAND_PATTERNS = (
    ("kill-server", re.compile(r"\bkill-server\b")),
    ("set-option -g (or its alias 'set -g')",
     re.compile(r"\bset-option\s+-g\b|\bset\s+-g\b")),
    ("new-session (or its alias 'new')",
     re.compile(r"\bnew-session\b|\bnew\b")),
)
_GUARD_FLAGS = ("-S", "-L")
_EXEC_CALL_NAMES = {"run", "Popen", "call", "check_call", "check_output"}


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-z", *_SCAN_DIRS],
                         cwd=REPO, capture_output=True, text=True, check=True)
    for rel in out.stdout.split("\0"):
        if not rel or not (rel.endswith(".py") or rel.endswith(".sh")):
            continue
        p = REPO / rel
        if p.resolve() == _SELF:
            continue
        if p.is_file():
            yield rel, p


def _clause_violations(text, label):
    """`text` (a shell-command string, or a whole comment-stripped .sh
    file) for a `tmux` token followed, within the SAME shell clause, by a
    destructive subcommand (canonical name OR tmux short alias -- see
    `_DESTRUCTIVE_SUBCOMMAND_PATTERNS`) with no `-S`/`-L` guard also in
    that clause. The 300-char lookahead window is generous against every
    real invocation in this repo's current corpus (the longest matched
    clause is well under 150 chars) -- a genuinely longer unbroken clause
    would need a wider window, but 300 already covers a `tmux -S <path>
    <subcommand> <several flags>` shape with room to spare."""
    violations = []
    for m in re.finditer(r"\btmux\b", text):
        window = text[m.start():m.start() + 300]
        clause_end = len(window)
        for sep in (";", "&&", "||", "|", "\n"):
            idx = window.find(sep)
            if idx != -1:
                clause_end = min(clause_end, idx)
        clause = window[:clause_end]
        for label_text, pattern in _DESTRUCTIVE_SUBCOMMAND_PATTERNS:
            if not pattern.search(clause):
                continue
            if any(flag in clause for flag in _GUARD_FLAGS):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append("%s:%d: unguarded 'tmux ... %s' -- clause: %r"
                              % (label, line_no, label_text, clause.strip()))
    return violations


def _is_real_exec_call(node):
    """True if `node` (an ast.Call) is a REAL process-spawning call:
    `subprocess.run/Popen/call/check_call/check_output(...)`, or the bare
    `run/Popen/...` form after `from subprocess import run`-style
    imports. Deliberately NOT any other callable named `run` (e.g. this
    repo's own dependency-injected fakes take a DIFFERENT positional shape
    -- `run(argv)`, one arg, never constructed inline as a destructive
    tmux literal at the call site since the argv is a variable -- and the
    false-positive corpus this lock's docstring documents is exactly what
    motivated requiring the `subprocess.` prefix, not the bare name, for
    the common ambiguous case)."""
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in _EXEC_CALL_NAMES:
        base = f.value
        return isinstance(base, ast.Name) and base.id == "subprocess"
    return False


def _string_elements(list_or_tuple):
    return [e.value for e in list_or_tuple.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _has_destructive_subcommand(elts):
    if any(sub in elts for sub in ("kill-server", "new-session")):
        return True
    for i, v in enumerate(elts[:-1]):
        if v == "set-option" and elts[i + 1] == "-g":
            return True
    return False


def _py_violations(py_path):
    """AST walk restricted to the first argument of a REAL
    `subprocess.run/Popen/call/check_call/check_output(...)` call — see
    `_is_real_exec_call` and the module docstring for exactly why this
    scope (not "every list/string literal") is what keeps this lock free
    of the ~19 false positives its first draft produced against this
    repo's own established mocked-`run=` test pattern."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args
                and _is_real_exec_call(node)):
            continue
        first = node.args[0]
        line_no = getattr(node, "lineno", 0)
        if isinstance(first, (ast.List, ast.Tuple)):
            elts = _string_elements(first)
            if "tmux" not in elts:
                continue
            if not _has_destructive_subcommand(elts):
                continue
            if any(flag in elts for flag in _GUARD_FLAGS):
                continue
            violations.append("%s:%d: unguarded tmux argv literal %r"
                              % (py_path.name, line_no, elts))
            # a `["sh", "-c", "<shell text>"]` argv nests a shell string as
            # its 3rd element -- scan that too (a `-S` on the OUTER "tmux"
            # element wouldn't exist here since this branch is the "sh -c"
            # wrapper shape, not a direct tmux argv; the inner string is
            # its own clause-scanned unit).
            if elts[:2] == ["sh", "-c"] and len(first.elts) >= 3:
                inner = first.elts[2]
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    violations.extend(_clause_violations(
                        inner.value, "%s (sh -c string, line %d)"
                        % (py_path.name, line_no)))
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            violations.extend(_clause_violations(
                first.value, "%s (shell string, line %d)"
                % (py_path.name, line_no)))
    return violations


class TestNoUnguardedDestructiveTmuxInvocation(unittest.TestCase):
    """Every `kill-server` / `set-option -g` / `new-session` tmux
    invocation under tests/ or hooks/ that ACTUALLY EXECUTES must carry an
    explicit -S or -L on the SAME invocation -- see module docstring
    (#613 incident)."""

    def test_every_destructive_tmux_call_is_socket_guarded(self):
        violations = []
        for rel, path in _tracked_files():
            if rel.endswith(".py"):
                try:
                    violations.extend(
                        "%s: %s" % (rel, v) for v in _py_violations(path))
                except SyntaxError as e:
                    self.fail("could not parse %s: %s" % (rel, e))
            else:  # .sh under hooks/ -- the WHOLE file is executable code
                text = path.read_text(encoding="utf-8", errors="replace")
                code = "\n".join(ln for ln in text.splitlines()
                                 if not ln.lstrip().startswith("#"))
                violations.extend(
                    "%s: %s" % (rel, v) for v in _clause_violations(code, rel))
        self.assertEqual(
            violations, [],
            "Found tmux invocation(s) of a destructive/global-mutating "
            "subcommand with NO explicit -S/-L socket selector on the same "
            "invocation -- exactly the #613 incident shape (a test harness "
            "that killed the box's REAL live tmux server via inherited "
            "$TMUX). Fix each:\n" + "\n".join(violations))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
