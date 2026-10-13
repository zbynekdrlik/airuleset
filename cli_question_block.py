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


def newest(entries, cwd=None, session=None):
    """The newest recorded question for ``cwd`` (and ``session``), or None."""
    best = None
    for rec in (entries or {}).values():
        if not isinstance(rec, dict):
            continue
        if cwd and rec.get("cwd") != cwd:
            continue
        if session and rec.get("session") != session:
            continue
        try:
            ts = float(rec.get("ts") or rec.get("asked") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if best is None or ts > best[0]:
            best = (ts, rec)
    return best[1] if best else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="airuleset question-block",
                                description=__doc__.splitlines()[0])
    p.add_argument("--cwd", default=os.getcwd(), help="project dir (default: here)")
    p.add_argument("--session", help="narrow to one session id")
    p.add_argument("--path", help=argparse.SUPPRESS)       # tests
    a = p.parse_args(argv)
    import notify
    rec = newest(notify.load_questions(a.path), a.cwd, a.session)
    if rec is None:
        print("question-block: no delivered ❓ question recorded for %s" % a.cwd,
              file=sys.stderr)
        return 1
    print(rec.get("block") or rec.get("question") or "")
    return 0


def cmd_question_block(args):
    return main(list(getattr(args, "rest", None) or []))


def register_parser(sub):
    p = sub.add_parser("question-block", add_help=False,
                       help="print the last delivered ❓ block for this cwd (#1224)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
