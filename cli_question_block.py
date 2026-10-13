"""#1224 ask 2: `airuleset.py question-block` prints the last delivered ❓ block.

A session that must re-ask a question "NANOVO A CELÁ", or repeat its marker
line verbatim, needs the block it asked before. Its only known copy used to be
its own transcript, and loading that 598 MB jsonl froze dev1. The notify path
already records every delivered question in ``~/.claude/discord-questions.json``
(``notify.record_question``: the one-line ``question`` plus the ``block`` with
its newlines), so this command just prints the newest one for a cwd, optionally
narrowed to a session.
"""
import argparse
import os
import sys


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _same_dir(a, b):
    return os.path.realpath(a or "") == os.path.realpath(b or "")


def newest(entries, cwd=None, session=None):
    """The most recently ASKED question for ``cwd`` (and ``session``), or None.
    ``asked`` is the ask time (``record_question`` keeps it for a re-tracked
    question); ``ts`` (the record time) only breaks ties. Paths compare by
    realpath, so a trailing slash or a symlinked cwd still matches."""
    best = None
    for rec in (entries or {}).values():
        if not isinstance(rec, dict):
            continue
        if cwd and not _same_dir(rec.get("cwd"), cwd):
            continue
        if session and rec.get("session") != session:
            continue
        key = (_num(rec.get("asked") or rec.get("ts")), _num(rec.get("ts")))
        if best is None or key > best[0]:
            best = (key, rec)
    return best[1] if best else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="airuleset question-block",
                                description=__doc__.splitlines()[0])
    _add_options(p)
    a = p.parse_args(argv)
    import notify
    rec = newest(notify.load_questions(a.path), a.cwd, a.session)
    if rec is None:
        print("question-block: no delivered ❓ question recorded for %s" % a.cwd,
              file=sys.stderr)
        return 1
    print("question-block: session %s" % (rec.get("session") or "?"), file=sys.stderr)
    print(rec.get("block") or rec.get("question") or "")
    return 0


def _add_options(p):
    p.add_argument("--cwd", default=os.getcwd(), help="project dir (default: here)")
    p.add_argument("--session", help="narrow to one session id")
    p.add_argument("--path", help=argparse.SUPPRESS)       # tests


def cmd_question_block(args):
    argv = ["--cwd", args.cwd] + (["--session", args.session] if args.session else [])
    return main(argv + (["--path", args.path] if args.path else []))


def register_parser(sub):
    _add_options(sub.add_parser(
        "question-block", help="print the last delivered ❓ block for this cwd (#1224)"))
