"""gates.pushtarget -- WHICH repo and WHICH ref a ``git push`` command sends (#1312).

A push hook runs in the SESSION's cwd, but the command can push another
checkout (``git -C <worktree> push``, ``cd <worktree> && git push``) and
another ref than that checkout's HEAD (``git push origin lane``,
``origin src:dst``). The #1059 lesson (internals-hooks.md): take the target
from the command itself, never from the cwd. ``parse`` is a pure parser;
``resolved`` adds the one git read (does the source exist locally?) through
an injected runner.

``parse(cmd)`` reads the FIRST ``git … push`` segment and returns
``PushTarget(dir, src, skip)``:

* ``dir`` -- the directory git runs in: a ``cd <dir>`` earlier in the same
  ``&&``/``;`` chain, then every global ``-C <dir>`` (relative ones chain, as
  git does). ``None`` = the process cwd (no cd/-C, or one it cannot follow:
  ``cd $X``, ``cd -``, a subshell). Never a guess.
* ``src`` -- the pushed source: the refspec's left side (a leading ``+``
  dropped), ``HEAD`` when there is no refspec or it is ``HEAD``/``@``.
* ``skip`` -- a delete push (``:dst``, ``--delete``/``-d``) adds no lines.

An unparseable command (unbalanced quotes) yields ``PushTarget(None, "HEAD",
False)``: exactly the pre-#1312 behaviour.
"""
import collections
import os
import shlex

PushTarget = collections.namedtuple("PushTarget", "dir src skip")

_DEFAULT = PushTarget(None, "HEAD", False)
_CHAIN = {"&&", ";"}                       # a cd before these still applies
_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_GIT_VALUE_GLOBALS = {"-c", "--git-dir", "--work-tree", "--namespace"}


def _tokens(cmd):
    lex = shlex.shlex(cmd or "", posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _is_op(tok):
    return bool(tok) and all(ch in "();<>|&\n" for ch in tok)


def _join(base, rel):
    if rel is None:
        return None
    rel = os.path.expanduser(rel)
    return rel if base is None else os.path.join(base, rel)


def _followable(path):
    return bool(path) and path != "-" and not any(c in path for c in "$`*?")


def _push_args(toks, i):
    """``(dir_from_-C, push_args)`` when ``toks[i]`` starts ``git [globals]
    push``, else ``None``."""
    j, d = i + 1, None
    while j < len(toks) and toks[j].startswith("-"):
        opt = toks[j]
        if opt == "-C" and j + 1 < len(toks):
            if not _followable(toks[j + 1]):
                return None, None
            d = toks[j + 1] if d is None else os.path.join(d, toks[j + 1])
            j += 2
        elif opt in _GIT_VALUE_GLOBALS and j + 1 < len(toks):
            j += 2
        else:
            j += 1
    if j < len(toks) and toks[j] == "push":
        end = j + 1
        while end < len(toks) and not _is_op(toks[end]):
            end += 1
        return d, toks[j + 1:end]
    return None


def _refspec(args):
    """``(src, skip)`` from the push's own arguments."""
    pos, k = [], 0
    while k < len(args):
        a = args[k]
        if a == "--":
            pos.extend(args[k + 1:])
            break
        if a in ("--delete", "-d"):
            return "HEAD", True
        if a.startswith("-"):
            k += 2 if a in _VALUE_OPTS else 1
            continue
        pos.append(a)
        k += 1
    if len(pos) < 2:
        return "HEAD", False
    spec = pos[1].lstrip("+")
    src, sep, _dst = spec.partition(":")
    if sep and not src:
        return "HEAD", True                # `:dst` deletes dst
    return ("HEAD" if src in ("", "HEAD", "@") else src), False


def parse(cmd):
    """The ``PushTarget`` of the first ``git … push`` in ``cmd`` (see module doc)."""
    try:
        toks = _tokens(cmd)
    except ValueError:
        return _DEFAULT
    cd_dir, start = None, True
    for i, tok in enumerate(toks):
        if _is_op(tok):
            if tok not in _CHAIN:
                cd_dir = None              # `|`, `||`, `&`, a subshell: unknown
            start = True
            continue
        if not start:
            continue
        start = False
        if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].isidentifier():
            start = True                   # `VAR=x cmd`: the command follows
            continue
        if tok == "cd":
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            cd_dir = _join(cd_dir, nxt) if _followable(nxt) and not _is_op(nxt) else None
            continue
        if tok.rsplit("/", 1)[-1] != "git":
            continue
        found = _push_args(toks, i)
        if found is None:
            continue
        c_dir, args = found
        if args is None:                    # an unfollowable -C
            return _DEFAULT
        src, skip = _refspec(args)
        return PushTarget(_join(cd_dir, c_dir) if c_dir else cd_dir, src, skip)
    return _DEFAULT


def resolved(cmd, git):
    """``(dir, head, cur, skip)`` for a push hook. ``git(args, cwd)`` is the
    caller's runner (a CompletedProcess or None). ``head`` is the pushed
    source when it resolves to a local commit, else ``HEAD`` (the pre-#1312
    range); ``cur`` is its branch name for ``pushscope.resolve`` (``None`` =
    the checkout's own branch)."""
    tgt = parse(cmd)
    head = tgt.src
    if head != "HEAD":
        ok = git(["rev-parse", "-q", "--verify", head + "^{commit}"], tgt.dir)
        head = head if ok is not None and ok.returncode == 0 else "HEAD"
    cur = head[len("refs/heads/"):] if head.startswith("refs/heads/") else head
    return tgt.dir, head, (None if cur == "HEAD" else cur), tgt.skip
