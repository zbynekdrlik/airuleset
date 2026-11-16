"""#1300 — an unattended tool's owner notice reaches the footer `U`.

`airuleset.py notify --body <text> --owner-notice <slug> [--cwd <project>]`
sends as before; when the owner policy (`notify.OWNER_PING_ALLOW`, #1137)
SUPPRESSES the send (zbynek gets only alarms on Discord and reads the footer
`U`), the notice is recorded through the map's single writer
`notify.record_question(..., suppressed=True)` — the #716 Discord-less entry,
session `notice:<project id>:<slug>`, `channel=""`, scoped to the project's
git root. `question_attribution` reads a `notice:` entry as TICKETLESS (a `#N`
in it is context) and `question-block` never returns one.
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
import hashlib
import os
import re
import sys

from question_attribution import NOTICE_SESSION_PREFIX as SESSION_PREFIX

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def add_notify_args(p_notify):
    """The two `notify` options (one call from airuleset.py's parser)."""
    p_notify.add_argument(
        "--owner-notice", dest="owner_notice", metavar="SLUG",
        help="with --body: when the owner policy suppresses the send, put the "
             "notice into the footer U of the --cwd project (#1300); the same "
             "SLUG again replaces it; it stays until --clear-owner-notice (or "
             "the question map's 200-entry cap evicts it as the oldest)")
    p_notify.add_argument(
        "--clear-owner-notice", dest="clear_owner_notice", metavar="SLUG",
        help="drop the --owner-notice SLUG of the --cwd project (#1300)")


def requested(args):
    """True when either owner-notice option is set (the cmd_notify gate)."""
    return any(_opt(args, n) is not None for n in ("owner_notice", "clear_owner_notice"))


def _opt(args, name):
    """A string option or None (a Mock/other value never counts as set)."""
    value = getattr(args, name, None)
    return value if isinstance(value, str) else None


def valid_slug(slug):
    return bool(_SLUG_RE.fullmatch(str(slug or "")))


def project_root(cwd, home=None):
    """The nearest ancestor of `cwd` (inclusive) holding `.git`, in the
    caller's own spelling (abspath, symlinks kept: the footer compares the
    session's raw cwd), or None when there is none or it is `/` / the home
    dir (compared by realpath)."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    path = os.path.abspath(cwd or os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return None if os.path.realpath(path) in ("/", home) else path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def session(slug, root):
    """`notice:<project id>:<slug>` — one item per project AND slug. The id
    hashes the REALPATH, so a symlinked, real or relative spelling of the same
    project records and clears the same item."""
    pid = hashlib.sha1(os.path.realpath(root).encode("utf-8")).hexdigest()[:10]
    return "%s%s:%s" % (SESSION_PREFIX, pid, slug)


def key(slug, root):
    return "suppressed:" + session(slug, root)


def _log(status, slug, root, reason=""):
    from notify import log_delivery
    log_delivery(status, kind="owner-notice", key=session(slug, root),
                 reason=reason or "%s %s" % (os.path.basename(root), root))


def record(slug, root, body, path=None):
    from notify import record_question
    return bool(record_question("", "", session(slug, root), root,
                                path=path, question=body, suppressed=True))


def clear(slug, root, path=None):
    """Drop the notice `slug` of the project `root`; True when it existed."""
    from notify import drop_question
    return bool(drop_question(key(slug, root), path))


def _refuse(msg):
    print("notify: %s (#1300)" % msg, file=sys.stderr)
    sys.exit(1)


def preflight(args):
    """Validate the owner-notice options BEFORE anything is sent. Returns the
    project root, or None when neither option is set. Exits 1 on misuse."""
    slug, clear_slug = _opt(args, "owner_notice"), _opt(args, "clear_owner_notice")
    if slug is None and clear_slug is None:
        return None
    for s in (slug, clear_slug):
        if s is not None and not valid_slug(s):
            _refuse("owner-notice slug %r must match [a-z0-9][a-z0-9._-]{0,63}" % s)
    if slug is not None and _opt(args, "body") is None:
        _refuse("--owner-notice needs --body (the notice text)")
    root = project_root(_opt(args, "cwd"))
    if root is None:
        _refuse("--owner-notice/--clear-owner-notice need --cwd inside a "
                "project git repo (not $HOME): the notice shows in that "
                "project's footer U")
    return root


def after_send(args, status, root):
    """Record (or clear) the notice for `root` after the send returned
    `status`. A dry run touches nothing. A record that fails exits 1 (the
    caller is unattended: a notice missing from U must not read as done)."""
    slug, clear_slug = _opt(args, "owner_notice"), _opt(args, "clear_owner_notice")
    if root is None:
        return
    if getattr(args, "dry_run", False) is True:
        print("owner-notice: dry run, nothing changed in U", file=sys.stderr)
        return
    if clear_slug is not None:
        done = clear(clear_slug, root)
        _log("owner-notice-cleared" if done else "owner-notice-absent", clear_slug, root)
        print("owner-notice: %s %s for %s" % (
            clear_slug, "cleared" if done else "not present", root), file=sys.stderr)
    if slug is None or status != "suppressed":
        return
    try:
        ok = record(slug, root, args.body)
    except Exception as exc:  # noqa: BLE001 - reported + exit 1 below
        ok = False
        print("notify: owner notice %s: %s" % (slug, type(exc).__name__), file=sys.stderr)
    _log("owner-notice-recorded" if ok else "owner-notice-failed", slug, root)
    if not ok:
        _refuse("owner notice %s NOT recorded in U" % slug)
    print("owner-notice: %s -> U of %s" % (slug, root), file=sys.stderr)
