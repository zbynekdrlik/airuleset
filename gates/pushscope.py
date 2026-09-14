"""gates.pushscope -- ONE resolver for "what lines does this push introduce to
the destination?" (#1020).

Before this module, block-test-skips.sh, pre-push-test-check.sh and
block-sensitive-staging.sh each resolved the diff BASE in their own dialect. The
#847/#909/#951/#1003 lessons are all patches to those divergent copies. This
module holds the ONE BASE_REF resolver (the PR-target case-block + the #909
fallbacks) that block-test-skips and pre-push-test-check share, plus the two
push-shape helpers and the changed-files diff that were byte-identical between
them.

The ONE deliberate divergence stays a PARAMETER, not a fork: block-test-skips
(per-added-line semantics) applies an ``origin/<branch>`` RANGE override and
captures the pre-override DEST_REF for merge-in exclusion; pre-push-test-check
(PR-scoped gates) must NOT narrow the range that way (#909 F1). So
``resolve(apply_branch_override=...)`` returns BOTH the range base and the
destination ref; each caller passes the flag its doctrine requires. All git
calls run in the process cwd (the hook's inherited session cwd), exactly as the
bash originals did.
"""
import re
import subprocess

from gates.shellcmd import strip_quoted

# A real ``git push`` command (block-test-skips' precise shape): git, optional
# flags, then ``push`` at a word boundary. Applied to the QUOTE-STRIPPED command
# so "git push" inside a quoted message/echo/path does not trigger.
_PUSH_RE = re.compile(r"git(\s+-\S+)*\s+push(\s|$)")
# The looser shape pre-push-test-check historically used (raw, no quote-strip).
_PUSH_LOOSE_RE = re.compile(r"git\s+push")
# #503 -- a durability BACKUP push to refs/autopilot-wip/* triggers no CI, so
# the CI-protecting gates must skip it. Anchored on the DESTINATION refspec.
_WIP_BACKUP_RE = re.compile(
    r":refs/autopilot-wip/|(?:--delete|\s-d)\s+refs/autopilot-wip/")


def is_push_command(cmd, *, quote_strip=True):
    """True iff ``cmd`` is a real ``git push`` command. With ``quote_strip``
    (block-test-skips' shape) the check runs on the quote-stripped command with
    the precise ``git [flags] push`` pattern; without it (pre-push-test-check's
    historical shape) a raw ``git\\s+push`` substring match."""
    if quote_strip:
        return bool(_PUSH_RE.search(strip_quoted(cmd or "")))
    return bool(_PUSH_LOOSE_RE.search(cmd or ""))


def is_wip_backup_push(cmd):
    """True iff ``cmd`` pushes (or deletes) a refs/autopilot-wip/* backup ref."""
    return bool(_WIP_BACKUP_RE.search(cmd or ""))


def _git(args, cwd=None):
    try:
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def is_inside_work_tree(cwd=None):
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return bool(r) and r.returncode == 0


def _ref_exists(ref, cwd=None):
    r = _git(["rev-parse", "-q", "--verify", ref], cwd=cwd)
    return bool(r) and r.returncode == 0


def default_branch(cwd=None):
    """The remote default branch (``git symbolic-ref refs/remotes/origin/HEAD``
    stripped of its prefix), or "main" when unresolved -- matching the bash
    ``... | sed ... || echo main`` under ``pipefail``."""
    r = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=cwd)
    if r and r.returncode == 0:
        val = (r.stdout or "").strip()
        if val.startswith("refs/remotes/origin/"):
            val = val[len("refs/remotes/origin/"):]
        if val:
            return val
    return "main"


def current_branch(cwd=None):
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if r and r.returncode == 0 and (r.stdout or "").strip():
        return r.stdout.strip()
    return "HEAD"


def resolve(cwd=None, *, apply_branch_override=False):
    """Resolve the diff refs for a push, returning
    ``(base_ref, dest_ref, cur_branch, default_branch)``.

    ``base_ref`` -- the RANGE base for ``base_ref...HEAD`` diffs. ``dest_ref`` --
    the DESTINATION integration branch (the PR target), captured BEFORE the
    per-branch override so a line already on the destination (rode in via
    ``git merge origin/develop``) can be excluded. When ``apply_branch_override``
    (block-test-skips only), ``base_ref`` is narrowed to ``origin/<branch>`` for
    a re-push when that ref exists; ``dest_ref`` is never narrowed. The whole
    case-block + #909 fallbacks are the SHARED logic the two push hooks used to
    keep byte-identical."""
    default = default_branch(cwd)
    cur = current_branch(cwd)
    base_ref = "origin/%s" % default
    case_resolved = False

    if cur in ("HEAD", default, "staging"):
        pass
    elif cur == "develop":
        if _ref_exists("origin/staging", cwd):
            base_ref = "origin/staging"
            case_resolved = True
    else:
        for cand in ("develop", "dev"):
            if cand != cur:
                if _ref_exists("upstream/%s" % cand, cwd) and _ref_exists("origin/%s" % cand, cwd):
                    base_ref = "upstream/%s" % cand
                    case_resolved = True
                    break
                if _ref_exists("origin/%s" % cand, cwd):
                    base_ref = "origin/%s" % cand
                    case_resolved = True
                    break

    # #909 -- fallbacks ONLY when the case block found nothing.
    if not case_resolved:
        tr = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=cwd)
        tracking = (tr.stdout or "").strip() if (tr and tr.returncode == 0) else ""
        if tracking and _ref_exists(tracking, cwd):
            base_ref = tracking
        else:
            for local in ("develop", "dev"):
                if local != cur and _ref_exists(local, cwd):
                    base_ref = local
                    break

    dest_ref = base_ref
    if apply_branch_override and cur != "HEAD" and _ref_exists("origin/%s" % cur, cwd):
        base_ref = "origin/%s" % cur
    return base_ref, dest_ref, cur, default


def changed_files(base_ref, cwd=None):
    """The list of files changed in ``base_ref...HEAD`` (fallback ``HEAD~1``),
    matching the bash ``git diff --name-only ... || git diff --name-only HEAD~1``.
    Empty list when neither range resolves."""
    r = _git(["diff", "--name-only", "%s...HEAD" % base_ref], cwd=cwd)
    if not r or r.returncode != 0:
        r = _git(["diff", "--name-only", "HEAD~1"], cwd=cwd)
    if not r or r.returncode != 0:
        return []
    return [ln for ln in (r.stdout or "").splitlines() if ln]
