"""#976: App-token stream cannot close its OWN issues when the python
``authority --self-login`` / ``authority --app-bot-login`` subprocess chain
is unreachable or returns empty — the hook had no DIRECT shell-level 403
detection as a belt.

The fix adds a direct ``gh api user`` probe in the hook: when ME is empty
after ``authority --self-login``, the hook itself checks for the 403 error
and sets ME to the App bot login constant (``STREAM_APP_BOT_LOGIN``),
hardcoded in the hook.  This makes the identity resolution independent
of the python authority subprocess chain.

RED: when the python authority chain is broken (--self-login and
--app-bot-login both return empty), the current hook blocks a legitimate
App-token self-close because it has no direct 403 detection.
GREEN: the direct 403 detection sets ME and the self-close is allowed.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"

# Fake gh: returns 403 for `api user`, configurable author for `issue view`.
_FAKE_GH = """\
#!/usr/bin/env bash
case "$1 $2" in
  "api user")
    echo "gh: Resource not accessible by integration (HTTP 403)" >&2
    exit 1;;
  "issue view")
    if printf '%s ' "$@" | grep -q -- '--json labels,comments'; then
      jq -n '{labels: [], comments: []}'
    elif printf '%s ' "$@" | grep -q -- '--json labels'; then
      for lbl in ${FAKE_GH_LABELS:-}; do echo "$lbl"; done
    else
      echo "${FAKE_GH_AUTHOR:-}"
    fi;;
  *) exit 1;;
esac
"""

# A python3 wrapper that intercepts authority --self-login and
# --app-bot-login calls and returns empty, simulating a stale/broken
# deployment where the python authority subsystem cannot resolve
# App identities. Plain `authority` (profile resolution) is passed
# through to the real python3 so the hook reaches the self-close logic.
_PYTHON3_WRAPPER = """\
#!/usr/bin/env bash
# #976 test: intercept authority identity calls
if [ "${AIRULESET_976_BREAK_IDENTITY:-0}" = "1" ]; then
    for arg in "$@"; do
        if [ "$arg" = "--self-login" ] || [ "$arg" = "--app-bot-login" ]; then
            # Return empty — simulates broken identity resolution
            exit 0
        fi
    done
fi
# Pass through to real python3
exec /usr/bin/python3 "$@"
"""


def _cwd_with_authority(profile):
    d = tempfile.mkdtemp()
    (Path(d) / "CLAUDE.md").write_text(
        f"# proj\n<!-- airuleset:authority={profile} -->\n")
    return d


def _fake_gh_dir():
    d = tempfile.mkdtemp()
    gh = Path(d) / "gh"
    gh.write_text(_FAKE_GH)
    gh.chmod(0o755)
    return d


def _python3_wrapper_dir():
    d = tempfile.mkdtemp()
    p = Path(d) / "python3"
    p.write_text(_PYTHON3_WRAPPER)
    p.chmod(0o755)
    return d


def _run(cmd, cwd, author="", labels="", app_token_dir=None,
         break_identity=False):
    """Run the close-guard hook with a fake gh.

    When ``break_identity=True``, a python3 wrapper intercepts
    ``authority --self-login`` and ``authority --app-bot-login`` to return
    empty, simulating a broken python identity chain (stale deploy,
    import error, env mismatch) while keeping plain ``authority``
    (profile resolution) working.
    """
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    path_dirs = [_fake_gh_dir()]
    if break_identity:
        path_dirs.insert(0, _python3_wrapper_dir())
        env["AIRULESET_976_BREAK_IDENTITY"] = "1"
    else:
        env.pop("AIRULESET_976_BREAK_IDENTITY", None)
    env["PATH"] = os.pathsep.join(path_dirs) + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_AUTHOR"] = author
    env["FAKE_GH_LABELS"] = labels
    env["FAKE_GH_API_USER_403"] = "1"
    if app_token_dir is not None:
        env["GH_APP_TOKEN_DIR"] = app_token_dir
    else:
        env.pop("GH_APP_TOKEN_DIR", None)
    return subprocess.run(["bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, cwd=cwd, env=env)


class TestAppToken403DirectDetection(TestCase):
    """#976: direct shell-level 403 detection in the hook as a belt
    on the python authority identity chain."""

    def setUp(self):
        self.branch = _cwd_with_authority("branch-merge")
        self.fork = _cwd_with_authority("fork-no-merge")

    # --- RED: broken authority chain blocks a legitimate App-token close ---

    def test_allows_app_close_when_authority_chain_broken(self):
        """The #976 core scenario: authority --self-login AND
        --app-bot-login both return empty (broken chain), but gh api user
        returns 403. The hook's direct 403 detection should set ME to the
        App bot login and allow the self-close.

        RED on the pre-fix hook (no direct 403 detection — ME stays empty,
        #773 fallback also dead because --app-bot-login is empty);
        GREEN after the fix (direct 403 detection sets ME).
        """
        r = _run(
            "gh issue close 4986 -R zbynekdrlik/odoo-erp "
            '--comment "Acceptance-cited: msg 1742799"',
            self.branch,
            author=airuleset.STREAM_APP_BOT_LOGIN,
            break_identity=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- the same scenario on fork-no-merge authority ---

    def test_allows_app_close_fork_when_authority_broken(self):
        r = _run(
            "gh issue close 5628 --comment done",
            self.fork,
            author=airuleset.STREAM_APP_BOT_LOGIN,
            break_identity=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- blocks: maintainer-authored must still block ---

    def test_blocks_maintainer_close_when_authority_broken(self):
        """A maintainer-authored ticket on a 403 box with a broken
        authority chain stays BLOCKED — the 403 detection must NOT widen
        the identity beyond the specific App bot login."""
        r = _run(
            "gh issue close 3312 --comment done",
            self.branch,
            author=airuleset.MAINTAINER_GH_LOGIN,
            break_identity=True,
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- blocks: foreign bot must still block ---

    def test_blocks_foreign_bot_close_when_authority_broken(self):
        """A ticket authored by a DIFFERENT bot stays BLOCKED."""
        r = _run(
            "gh issue close 4100 --comment done",
            self.fork,
            author="app/dependabot",
            break_identity=True,
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- normal paths still work without the break ---

    def test_normal_403_close_still_works(self):
        """Without break_identity, the existing #773 fallback path
        continues to work (belt-and-suspenders: both paths active)."""
        r = _run(
            "gh issue close 4986 --comment done",
            self.branch,
            author=airuleset.STREAM_APP_BOT_LOGIN,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pat_self_close_unchanged(self):
        """On a PAT box (no 403), the identity path is unaffected."""
        d = tempfile.mkdtemp()
        gh = Path(d) / "gh"
        gh.write_text("""\
#!/usr/bin/env bash
case "$1 $2" in
  "api user") echo "kvaskodev";;
  "issue view")
    if printf '%s ' "$@" | grep -q -- '--json labels,comments'; then
      jq -n '{labels: [], comments: []}'
    elif printf '%s ' "$@" | grep -q -- '--json labels'; then
      echo ""
    else
      echo "kvaskodev"
    fi;;
  *) exit 1;;
esac
""")
        gh.chmod(0o755)
        payload = json.dumps({"tool_input": {"command":
            "gh issue close 100 --comment done"}})
        env = dict(os.environ)
        env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_AUTHOR"] = "kvaskodev"
        env["FAKE_GH_API_USER_403"] = "0"
        env.pop("GH_APP_TOKEN_DIR", None)
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True,
                           cwd=self.fork, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
