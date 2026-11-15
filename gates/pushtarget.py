"""gates.pushtarget -- WHICH repos and WHICH refs a ``git push`` command sends (#1312).

A push hook runs in the SESSION's cwd, but the command can push another
checkout (``git -C <worktree> push``, ``cd <worktree> && git push``) and
other refs than that checkout's HEAD (``git push origin lane``,
``origin src:dst``). The #1059 lesson (internals-hooks.md): take the target
from the command itself, never from the cwd. ``parse`` is a pure parser;
``resolved`` adds the git reads (is the dir a work tree, does the source exist
locally) through injected functions.

``parse(cmd)`` returns one ``PushTarget(dir, srcs, skip)`` per ``git … push``
in the command (EVERY one, at any word position, so an earlier clean or delete
push never shadows a later one and ``sudo``/``time``/``env``/``{ …; }`` wrappers
are seen). Heredoc bodies are data and are blanked first. When the raw text
holds more ``git … push`` than were parsed (``bash -c '…'``, a heredoc body),
the pre-#1312 cwd/HEAD target is added: the parser never judges LESS than the
old gate did.

* ``dir`` -- the directory git runs in: a ``cd [-L|-P] <dir>`` earlier in the
  same ``&&``/``;``/newline chain, then every global ``-C <dir>`` (relative
  ones chain, as git does). ``None`` = the process cwd (no cd/-C, or a cd it
  cannot follow: ``cd $X``, ``cd -``, a cd after a pipe or a subshell, and any
  relative cd after one of those). Never a guess.
* ``srcs`` -- every pushed source: each refspec's left side (a leading ``+``
  dropped); ``("HEAD",)`` when there is no refspec, ``HEAD`` for ``HEAD``/``@``.
  A delete refspec (``:dst``) contributes nothing.
* ``skip`` -- the whole push only deletes (``--delete``/``-d``, or every
  refspec is ``:dst``): it adds no lines.

An unparseable command (unbalanced quotes) yields one ``PushTarget(None,
("HEAD",), False)``: exactly the pre-#1312 behaviour.
"""
import collections
import os
import re
import shlex

PushTarget = collections.namedtuple("PushTarget", "dir srcs skip")

_DEFAULT = [PushTarget(None, ("HEAD",), False)]
_CHAIN = {"&&", ";", "\n"}                 # a cd before these still applies
_PUNCT = "();<>|&\n"
_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_GIT_VALUE_GLOBALS = {"-c", "--git-dir", "--work-tree", "--namespace"}
_UNKNOWN = object()                        # a cd we could not follow
# Every `git [globals] push` the raw text holds, quoted or wrapped or not (the
# pushscope detector's shape). More of these than parsed pushes means one hides
# where the parser cannot see it (`sudo …`, `bash -c '…'`, a heredoc body):
# that one is judged the pre-#1312 way, in the cwd (re-review 🔴).
_ANY_PUSH_RE = re.compile(r"\bgit(?:\s+-[Cc]\s+\S+|\s+-(?![Cc]\s)\S+)*\s+push\b")


def _tokens(cmd):
    lex = shlex.shlex((cmd or "").replace("\\\n", " "), posix=True,
                      punctuation_chars=_PUNCT)
    lex.whitespace = " \t\r"               # a newline separates commands
    lex.whitespace_split = True
    return list(lex)


def _is_op(tok):
    return bool(tok) and all(ch in _PUNCT for ch in tok)


def _join(base, rel):
    rel = os.path.expanduser(rel)
    return rel if base is None else os.path.join(base, rel)


def _followable(path):
    return bool(path) and not path.startswith("-") and not any(c in path for c in "$`*?")


def _cd_target(toks, i):
    """The dir ``cd`` at ``toks[i]`` changes to, or None when not followable."""
    j = i + 1
    while j < len(toks) and toks[j] in ("-L", "-P", "-e", "-@", "--"):
        j += 1
    nxt = toks[j] if j < len(toks) else ""
    return nxt if _followable(nxt) and not _is_op(nxt) else None


def _push_args(toks, i):
    """``(dir_from_-C, push_args)`` when ``toks[i]`` starts ``git [globals]
    push``; ``(None, None)`` for an unfollowable ``-C``; else ``None``."""
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


def _refspecs(args):
    """``(srcs, skip)`` from the push's own arguments."""
    pos, k, repo_opt = [], 0, False
    while k < len(args):
        a = args[k]
        if a == "--":
            pos.extend(args[k + 1:])
            break
        if a in ("--delete", "-d"):
            return (), True
        if a.startswith("--repo"):
            repo_opt = True                # the remote is the option, not pos[0]
        if a.startswith("-"):
            k += 2 if a in _VALUE_OPTS else 1
            continue
        pos.append(a)
        k += 1
    specs = pos if repo_opt else pos[1:]
    if not specs:
        return ("HEAD",), False
    srcs = []
    for spec in specs:
        src, sep, _dst = spec.lstrip("+").partition(":")
        if sep and not src:
            continue                       # `:dst` deletes dst
        src = "HEAD" if src in ("", "HEAD", "@") else src
        if src not in srcs:
            srcs.append(src)
    return tuple(srcs), not srcs


def _target(cd_dir, c_dir, srcs, skip):
    if cd_dir is _UNKNOWN:
        if not (c_dir and os.path.isabs(os.path.expanduser(c_dir))):
            return PushTarget(None, srcs, skip)   # unknown dir: the process cwd, as before
        cd_dir = None
    return PushTarget(_join(cd_dir, c_dir) if c_dir else cd_dir, srcs, skip)


def parse(cmd):
    """Every ``PushTarget`` in ``cmd`` (see the module doc). Heredoc bodies are
    data, never commands (a `cd` line in one must not move the gate)."""
    from gates.selfservice import _capture_heredocs
    skeleton = _capture_heredocs(cmd or "")[2]
    try:
        toks = _tokens(skeleton)
    except ValueError:
        return list(_DEFAULT)
    out, cd_dir, start = [], None, True
    for i, tok in enumerate(toks):
        if _is_op(tok):
            if tok not in _CHAIN:
                cd_dir = _UNKNOWN          # `|`, `||`, `&`, a subshell
            start = True
            continue
        if start and "=" in tok and not tok.startswith("-") \
                and tok.split("=", 1)[0].isidentifier():
            continue                       # `VAR=x cmd`: the command follows
        if start and tok == "cd":
            target = _cd_target(toks, i)
            if not target or (cd_dir is _UNKNOWN and not os.path.isabs(os.path.expanduser(target))):
                cd_dir = _UNKNOWN
            else:
                cd_dir = _join(None if cd_dir is _UNKNOWN else cd_dir, target)
        start = False
        if tok.rsplit("/", 1)[-1] != "git":
            continue
        found = _push_args(toks, i)        # at ANY position: `sudo …`, `time …`
        if found is None:
            continue
        c_dir, args = found
        if args is None:                   # an unfollowable -C: the old behaviour
            out.append(_DEFAULT[0])
            continue
        out.append(_target(cd_dir, c_dir, *_refspecs(args)))
    if len(_ANY_PUSH_RE.findall(cmd or "")) > len(out) and _DEFAULT[0] not in out:
        out.append(_DEFAULT[0])
    return out or list(_DEFAULT)


def resolved(cmd, git, is_work_tree):
    """``[(dir, head, cur)]`` to gate, one per pushed source, deduplicated.

    ``git(args, cwd)`` returns a CompletedProcess or None; ``is_work_tree(cwd)``
    a bool. A dir that is not a work tree falls back to the process cwd (the
    pre-#1312 behaviour, never a silent allow); a target whose dir is still no
    work tree is dropped. ``head`` is the source when it resolves to a local
    commit, else ``HEAD``; ``cur`` its branch name (``None`` = the checkout's
    own branch). A delete-only push contributes nothing."""
    out = []
    for tgt in parse(cmd):
        if tgt.skip:
            continue
        cwd = tgt.dir if tgt.dir is None or is_work_tree(tgt.dir) else None
        if not is_work_tree(cwd):
            continue
        for head in tgt.srcs:
            if head != "HEAD":
                ok = git(["rev-parse", "-q", "--verify", head + "^{commit}"], cwd)
                head = head if ok is not None and ok.returncode == 0 else "HEAD"
            cur = head[len("refs/heads/"):] if head.startswith("refs/heads/") else head
            item = (cwd, head, None if cur == "HEAD" else cur)
            if item not in out:
                out.append(item)
    return out
