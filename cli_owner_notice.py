"""#1300 — an unattended tool's owner notice reaches the footer `U`.

`airuleset.py notify --body <text> --owner-notice <slug> [--cwd <project>]`
sends as before; when the owner policy (`notify.OWNER_PING_ALLOW`, #1137)
SUPPRESSES the send (zbynek gets only alarms on Discord and reads the footer
`U`), the notice is recorded through the map's single writer
`notify.record_question(..., suppressed=True)` — the #716 Discord-less entry,
key `suppressed:notice:<slug>`, `channel=""`, scoped to the project's git root.
That entry is counted by the footer `U N` fold (`statusbar.ticketless_question_pings`)
and listed by `core-quals --waiting`; every other map consumer already ignores
it (job 7 drops channel "", the answered-question prune finds no transcript for
a `notice:` session, `update_question` skips a suppressed entry).

The same slug again overwrites the item (one U line per condition, newest
text); `notify --clear-owner-notice <slug> [--cwd]` drops it once the condition
is gone. A send that IS delivered (an owner not on the policy) and a dry run
record nothing.

The cwd must resolve to a project (a git root that is neither `/` nor the
home dir): the U fold scopes entries by either-direction cwd containment, so a
notice recorded at `$HOME` would land in EVERY project footer of the account.
"""
import os
import re
import sys

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
SESSION_PREFIX = "notice:"


def add_notify_args(p_notify):
    """The two `notify` options (one call from airuleset.py's parser)."""
    p_notify.add_argument(
        "--owner-notice", dest="owner_notice", metavar="SLUG",
        help="with --body: when the owner policy suppresses the send, put the "
             "notice into the footer U of the --cwd project (#1300); the same "
             "SLUG again replaces it")
    p_notify.add_argument(
        "--clear-owner-notice", dest="clear_owner_notice", metavar="SLUG",
        help="drop the --owner-notice SLUG of the --cwd project (#1300)")


def valid_slug(slug):
    return bool(_SLUG_RE.fullmatch(str(slug or "")))


def project_root(cwd, home=None):
    """The nearest ancestor of `cwd` (inclusive) holding `.git`, or None when
    there is none or it is `/` / the home dir."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    path = os.path.realpath(cwd or os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return None if path in ("/", home) else path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def key(slug):
    return "suppressed:" + SESSION_PREFIX + slug


def record(slug, cwd, body, path=None):
    from notify import record_question
    return bool(record_question("", "", SESSION_PREFIX + slug, cwd,
                                path=path, question=body, suppressed=True))


def clear(slug, cwd, path=None):
    """Drop the notice `slug` only when it belongs to the project `cwd`."""
    from notify import drop_question, load_questions
    entry = load_questions(path).get(key(slug))
    if not isinstance(entry, dict) or str(entry.get("cwd") or "") != cwd:
        return False
    return bool(drop_question(key(slug), path))


def _refuse(msg):
    print("notify: %s (#1300)" % msg, file=sys.stderr)
    sys.exit(1)


def preflight(args):
    """Validate the owner-notice options BEFORE anything is sent. Returns the
    project root, or None when neither option is set. Exits 1 on misuse."""
    slug = getattr(args, "owner_notice", None)
    clear_slug = getattr(args, "clear_owner_notice", None)
    if slug is None and clear_slug is None:
        return None
    for s in (slug, clear_slug):
        if s is not None and not valid_slug(s):
            _refuse("owner-notice slug %r must match [a-z0-9][a-z0-9._-]{0,63}" % s)
    if slug is not None and getattr(args, "body", None) is None:
        _refuse("--owner-notice needs --body (the notice text)")
    root = project_root(getattr(args, "cwd", None))
    if root is None:
        _refuse("--owner-notice/--clear-owner-notice need --cwd inside a "
                "project git repo (not $HOME): the notice shows in that "
                "project's footer U")
    return root


def after_send(args, status, root):
    """Record (or clear) the notice for `root` after the send returned
    `status`. Never raises; a failed record is reported on stderr."""
    slug = getattr(args, "owner_notice", None)
    clear_slug = getattr(args, "clear_owner_notice", None)
    if root is None:
        return
    if clear_slug is not None:
        done = clear(clear_slug, root)
        print("owner-notice: %s %s for %s" % (
            clear_slug, "cleared" if done else "not present", root), file=sys.stderr)
    if slug is None or getattr(args, "dry_run", False) or status != "suppressed":
        return
    try:
        ok = record(slug, root, args.body)
    except Exception as exc:  # noqa: BLE001 - reported, the send already ran
        ok = False
        print("notify: owner notice %s not recorded: %s" % (slug, type(exc).__name__),
              file=sys.stderr)
    if ok:
        print("owner-notice: %s -> U of %s" % (slug, root), file=sys.stderr)
    else:
        print("notify: owner notice %s NOT recorded in U (#1300)" % slug,
              file=sys.stderr)
