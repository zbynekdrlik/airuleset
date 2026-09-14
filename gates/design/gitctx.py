"""gates.design.gitctx -- the git-context helpers for the design gate (#1020):
merge-commit detection, the #310 stale-msgfile quarantine, reject-reason I/O, and
the #206 gh-issue-state / required_refs narrowing.

Split out of the old top-level design_gate.py with ZERO behaviour change. The
quote-stripper is now the shared gates.shellcmd.strip_quoted (re-exported here as
the historical private name `_strip_quoted` so `design_gate._strip_quoted` keeps
resolving), replacing the former local copy -- one of the four duplicated
shell-quote strippers this rework removes.
"""
import os
import re
import subprocess
import time

from gates.shellcmd import strip_quoted as _strip_quoted
from gates.design.markers import _claude_dir, marker_key

# #1003 -- merge-commit awareness. A resync `git merge origin/develop` makes a
# commit whose auto-generated message can carry a `#N` from the integration
# branch's history (`Merge branch 'develop' into feat-3 (#6981)`), but a merge
# introduces NO new design -- so the gate must exempt it. PRIMARY, un-forgeable:
# MERGE_HEAD exists (a real merge is in progress). BELTS (for the single-call
# `git merge ... && git commit -m "Merge ..."` shape, MERGE_HEAD not yet set at
# PreToolUse): a REAL `git merge` command, or a canonical `-m "Merge <kind> '..."
# message. MERGE_HEAD is load-bearing; the belts are a trusted-worker
# convenience, not an adversarial boundary.
#
# Review F2 (both belts hardened against ordinary fix commits):
#  - the git-merge belt runs against the QUOTE-STRIPPED command, so a `git merge`
#    that appears only INSIDE a quoted `-m` message ("handle 'a && git merge b'")
#    is NOT a command and does not exempt;
#  - the message belt is anchored to `-m "Merge <kind> '<ref>` (right after the
#    -m flag, canonical auto-merge message start), so an ordinary fix message
#    that merely MENTIONS "merge branch"/"merge origin/x" mid-sentence does not
#    match (the replay-corpus / F2 false positives).

# A `git merge` command at a statement boundary (checked on the quote-stripped
# command, so an in-message occurrence never counts). Review-2 F1: `merge`
# must be TERMINAL — `(?![-\w])` excludes the read-only plumbing subcommands
# `git merge-base`/`merge-file`/`merge-tree`/`merge-index` (a `git merge-base
# --is-ancestor` "am I behind?" idiom is not a merge), and the trailing
# lookahead excludes the merge CANCELS `--abort`/`--quit` (they end a merge,
# they do not make one). `git merge origin/x` / `git merge --no-ff` /
# `git merge --continue` still match (a `--continue` completes a real merge,
# and MERGE_HEAD is present for it anyway).
_GIT_MERGE_CMD_RE = re.compile(
    r"(?:^|[;&|]|&&)\s*(?:sudo\s+|env\s+)?git\s+merge(?![-\w])"
    r"(?!\s+--(?:abort|quit)\b)")
# A canonical auto-merge message immediately after the -m/--message flag:
# `-m "Merge branch '…"` / `-m 'Merge commit "…'`. The kind + a following quote
# (the ref) is required, and it must sit right after the flag's opening quote,
# so a fix message like `-m "fix: resolve merge branch 'x' note"` never matches.
_MERGE_MSG_RE = re.compile(
    r"(?:-m|--message)\s*=?\s*(['\"])\s*"
    r"Merge\s+(?:branch|remote-tracking\s+branch|commit|tag)\s+['\"]",
    re.IGNORECASE)


def _merge_head_present(cwd):
    """True iff a merge is in progress in `cwd` (`.git/MERGE_HEAD` exists).
    Never raises; False on any failure (git missing, not a repo, no cwd)."""
    if not cwd:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return False
    return r.returncode == 0


def is_merge_commit_context(cmd, cwd):
    """Heuristic: is this `git commit` completing a MERGE (which introduces no
    new design)? Returns `(ok: bool, reason: str)`. PRIMARY = MERGE_HEAD in
    `cwd`; BELTS = a real `git merge` command (quote-stripped) or a canonical
    `-m "Merge <kind> '..."` message. A SHAPE/state check (same documented
    limitation as the rest of this module) -- MERGE_HEAD is the robust primary,
    the belts are hardened per review F2 against ordinary fix commits."""
    text = cmd or ""
    if _merge_head_present(cwd):
        return True, "MERGE_HEAD present (merge in progress)"
    if _GIT_MERGE_CMD_RE.search(_strip_quoted(text)):
        return True, "git merge command in the compound"
    if _MERGE_MSG_RE.search(text):
        return True, "canonical -m merge-message"
    return False, "not a merge commit"


# --------------------------------------------------------------------------- #
# #206 -- an already-CLOSED issue reference no longer requires a design
# marker. The same syntactic shapes (`#N`, `(#N)`, comma/slash-separated
# lists) are used BOTH for "the ticket this commit is for" AND for a
# historical/context reference (the reported false-block: a commit prose
# citing "(owner decisions #1734/#1766)", both long-closed tickets) -- no
# purely positional/syntactic rule can tell the two apart (this repo's own
# "(#N)" convention for the commit's own ticket is syntactically identical
# to the false-positive shape). A CLOSED issue, by definition, is
# overwhelmingly unlikely to be "the ticket I'm designing for right now",
# so GitHub's own issue state is the discriminator.
# --------------------------------------------------------------------------- #

def _gh_issue_state(n, cwd, timeout=8):
    """GitHub's own state ("OPEN"/"CLOSED") for issue #`n`, resolved via
    `gh` run with `cwd` as the working directory so it auto-detects the
    repo -- the same pattern `hooks/block-fork-no-merge-issue-close.sh`
    already uses for its own live `gh` call. Returns None on ANY failure
    (gh missing, no network, auth issue, timeout, unexpected output) --
    unmeasurable, never guessed, and NEVER raises."""
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(n), "--json", "state", "--jq", ".state"],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    state = (out.stdout or "").strip().upper()
    return state if state in ("OPEN", "CLOSED") else None


# --------------------------------------------------------------------------- #
# #310 -- stale scratch-msgfile quarantine. A worker composing
# `cat > path <<EOF ... EOF && git commit -F path` in ONE Bash call has the
# WHOLE compound denied atomically by block-commit-without-design.sh when the
# gate fires -- nothing in it executes, so the intended `cat >` write never
# lands. A file already sitting at `path` from an unrelated earlier attempt
# survives untouched, and a LATER bare `git commit -F path` retry carries no
# issue-number TEXT at all (the reference lives only inside the file's own
# content, invisible to ISSUE_REF_RE), so the gate never blocks IT either --
# the stale content commits silently. See the #310 design comment on the
# issue for the full incident and the two rejected alternatives (re-printing
# the file's content, and a stateful "provably predates the command"
# freshness marker).
# --------------------------------------------------------------------------- #

# Any `>`/`>>` redirect target -- the standard `cat > path <<EOF` recipe.
# Deliberately simple (documented limitation, same "cost of a miss is low"
# tradeoff ISSUE_REF_RE already makes elsewhere in this module): no
# shell-quote/heredoc-body awareness.
_REDIR_RX = re.compile(
    r"(?:^|[\s;&|(])(?:\d?>>?)\s*(['\"]?)([^\s'\">|;&]+)\1")
# `git commit` specifically -- the whitespace/end-of-string lookahead (not a
# bare `\b`) is what keeps a DIFFERENT subcommand sharing the same prefix
# (`git commit-graph write --file X`) from being mistaken for `git commit`.
_COMMIT_FILE_RX = re.compile(
    r"git\s+commit(?=\s|$)[^\n;&|]*?(?:-F|--file)(?:=|\s+)"
    r"(['\"]?)([^\s'\"><|;&]+)\1")


def stale_msgfile_candidates(cmd):
    """Paths this SAME command text tries to (over)WRITE (a `>`/`>>`
    redirect target) AND ALSO passes to `git commit -F`/`--file` -- the
    write-then-consume SIGNATURE that leaves a stale file behind whenever
    the whole compound gets blocked before any of it executes. First-seen
    order (sorted); empty list when there is nothing to quarantine.

    Known, deliberate residual (adversarial-review finding #1, #310):
    NEITHER regex requires its match to be REAL shell syntax outside
    quotes, and the two are scanned INDEPENDENTLY -- so a `-m "..."`
    message merely PROSE-DESCRIBING the pattern (e.g. documenting this
    very bug: "cat > README.md ... git commit -F README.md") satisfies
    both just as well as genuine shell syntax, with no requirement that
    the two matches even belong to the same statement. This function
    stays a pure text-signature detector on purpose (no filesystem/git
    access, same offline-testable shape every other regex in this module
    has) -- the caller is what closes the DANGEROUS half of this false-
    positive class: `is_git_tracked()` refuses to let anything git
    already tracks be quarantined, so the worst a decoy match can do is
    move aside a path that was already UNTRACKED -- never a real project
    file. See hooks/block-commit-without-design.sh for the wiring."""
    text = cmd or ""
    written = {os.path.normpath(m.group(2)) for m in _REDIR_RX.finditer(text)}
    committed = {os.path.normpath(m.group(2)) for m in _COMMIT_FILE_RX.finditer(text)}
    return sorted(written & committed)


def is_git_tracked(path, cwd):
    """True iff `path` is a file SOME repo already tracks -- a genuine
    scratch msgfile is NEVER tracked, so this is the discriminator
    stale_msgfile_candidates' own docstring promises: refuse to quarantine
    anything that could plausibly be a real project file. Fails toward
    TRACKED (never quarantine) on ANY unmeasurable result -- git missing,
    not a repo, timeout, unexpected output -- the same "never guess toward
    the more consequential action" direction `required_refs` already uses
    one function up, just applied to a destructive action instead of a
    block decision. `git ls-files --error-unmatch` has FOUR outcomes, not
    two: rc=0 (tracked BY cwd's repo), rc=1 (git ran fine and specifically
    confirmed the path is untracked -- "did not match any file(s) known to
    git", which can only happen for a path genuinely INSIDE cwd's own
    working tree), rc=128 with "is outside repository" (the queried path is
    not even INSIDE `cwd`'s working tree at all -- every real worker
    scratchpad file, under /tmp, hits this case), and anything else (128
    for "not a git repository" -- `cwd` itself isn't a repo, genuinely
    unmeasurable -- or any other failure/timeout).

    #431-review F1 (live-triggered, empirically confirmed): "outside cwd's
    repo" does NOT mean "untracked anywhere" -- a worktree's own `cwd`
    makes its sibling MAIN checkout (they share one `.git`, per two-branch-
    workflow.md/#317, but each worktree has its OWN separate working tree)
    read as "is outside repository" too, and so does any file genuinely
    tracked by a COMPLETELY DIFFERENT repo. Naively treating rc=128
    "outside repository" as a second confident "not tracked" signal would
    let `quarantine_stale_msgfile` rename away a real, valuable,
    git-tracked project file the instant it merely lives outside THIS
    worktree's own directory -- exactly the "moved a real project file"
    disaster `stale_msgfile_candidates`' own docstring promises can never
    happen. Fix: on rc=128 "outside repository", ask whichever repo (if
    any) actually OWNS `path`'s containing directory -- via
    `git -C <dirname(path)> rev-parse --show-toplevel` -- rather than
    assuming "outside cwd's repo" means "untracked by every repo". If NO
    repo owns that directory at all (rev-parse itself fails: rc!=0 or
    empty stdout), `path` is genuinely outside version control anywhere --
    the real /tmp-scratchpad case #431 exists to fix -- confidently
    untracked. If a repo DOES own it, re-query `--error-unmatch` against
    THAT repo and fail toward TRACKED on anything but a confirmed rc==1
    (mirrors the original outer check's own fail-safe direction one level
    down, rather than inventing a new one)."""
    # #431-review F2: "is outside repository" is one of git's own
    # gettext-translated messages -- on a box whose git has NLS catalogs
    # installed AND runs under a non-English locale, the substring match
    # below would silently miss and this whole rc=128 branch would revert
    # to the pre-#431 "assume tracked, never quarantine" behaviour with NO
    # signal that the feature had disappeared. Force the untranslated
    # (POSIX "C") locale on the ONE call whose stderr text this function
    # actually parses -- the two calls below only ever read a returncode
    # or a raw path from stdout, never a translated message.
    git_env = dict(os.environ)
    git_env["LC_ALL"] = "C"
    git_env["LANGUAGE"] = ""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=cwd, capture_output=True, text=True, timeout=8, env=git_env,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode == 1:
        return False
    if out.returncode == 128 and "is outside repository" in (out.stderr or ""):
        try:
            owner_dir = os.path.dirname(path) or "."
            top = subprocess.run(
                ["git", "-C", owner_dir, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        owner = top.stdout.strip()
        if top.returncode != 0 or not owner:
            return False
        try:
            owned = subprocess.run(
                ["git", "-C", owner, "ls-files", "--error-unmatch", "--", path],
                capture_output=True, text=True, timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return owned.returncode != 1
    return True


def quarantine_stale_msgfile(path, ts=None):
    """Best-effort: rename `path` aside (a `.stale-<epoch-ms>` sibling,
    never deleted -- the content is kept for inspection, only the ORIGINAL
    path is made to disappear) so a LATER bare `git commit -F path` retry
    fails LOUD (file not found) instead of silently reading content this
    SAME blocked command never got the chance to (re)write. A destination
    collision (adversarial-review finding #2: two candidates quarantined
    in the same millisecond) is never clobbered -- a numeric suffix is
    appended until the destination is free. Returns the quarantine path on
    success, else None (nothing existed there, it is a directory, or the
    rename failed) -- NEVER raises. The CALLER is responsible for checking
    `is_git_tracked()` first; this function only ever moves a file, it has
    no opinion on whether that was the right file."""
    try:
        if not os.path.isfile(path):
            return None
        stamp = int((ts if ts is not None else time.time()) * 1000)
        dest = "%s.stale-%d" % (path, stamp)
        n = 0
        while os.path.lexists(dest):
            n += 1
            dest = "%s.stale-%d-%d" % (path, stamp, n)
        os.rename(path, dest)
        return dest
    except OSError:
        return None


def ensure_stale_pattern_excluded(cwd):
    """Best-effort, idempotent: appends a `*.stale-*` line to the repo's
    LOCAL (never committed, never git-tracked) `.git/info/exclude`, so a
    quarantined msgfile never gets swept into `git add -A` / shown as an
    untracked file waiting to be staged (adversarial-review finding #3,
    #310). Resolved via `git rev-parse --git-common-dir` -- NOT a naive
    `cwd/.git` join -- so this lands in the SHARED exclude file even when
    `cwd` is a linked WORKTREE checkout (`.git` there is a FILE pointing
    elsewhere, exactly this repo's own autopilot-worker dispatch shape).
    Pure hygiene, never load-bearing for the actual quarantine: a missing/
    unwritable/non-repo `cwd` is silently skipped, never raises."""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                              cwd=cwd, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    git_common_dir = (out.stdout or "").strip()
    if not git_common_dir:
        return
    if not os.path.isabs(git_common_dir):
        git_common_dir = os.path.join(cwd, git_common_dir)
    pattern = "*.stale-*"
    try:
        info_dir = os.path.join(git_common_dir, "info")
        os.makedirs(info_dir, exist_ok=True)
        exclude_path = os.path.join(info_dir, "exclude")
        existing_lines = []
        if os.path.isfile(exclude_path):
            with open(exclude_path, encoding="utf-8", errors="replace") as fh:
                existing_lines = fh.read().splitlines()
        if pattern not in existing_lines:
            with open(exclude_path, "a", encoding="utf-8") as fh:
                fh.write(pattern + "\n")
    except OSError:
        return


# --------------------------------------------------------------------------- #
# #414 -- reject-reason I/O: purely diagnostic, NEVER gates anything by
# itself. Lets `hooks/block-commit-without-design.sh` tell a blocked worker
# WHAT is still missing from its last posted comment, instead of the bare
# "no design comment posted yet". A SIBLING directory to `design-posted/`
# (never mixed in) so a reject can never be mistaken for a delivered
# marker, and `marker_exists`/`read_marker` never need to learn about it.
# --------------------------------------------------------------------------- #

_REJECT_DIRNAME = "design-rejected"


def reject_dir():
    return os.path.join(_claude_dir(), _REJECT_DIRNAME)


def reject_path(repo_key, issue, kind="design"):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", kind + ":" + marker_key(repo_key, issue))
    return os.path.join(reject_dir(), safe)


def write_reject_reason(repo_key, issue, reason, kind="design"):
    """Best-effort record of the MOST RECENT classification failure for
    `<repo_key>#<issue>` / `kind` -- overwritten on every failed attempt
    (only the latest matters). Never speculative about anything a caller
    hasn't just observed; never raises; an unwritable ~/.claude just means
    the diagnostic stays unavailable, the gate itself is unaffected."""
    if not repo_key or issue in (None, ""):
        return False
    d = reject_dir()
    try:
        os.makedirs(d, exist_ok=True)
        with open(reject_path(repo_key, issue, kind), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\n" % (time.time(), reason or ""))
        return True
    except OSError:
        return False


def read_reject_reason(repo_key, issue, kind="design"):
    """The most recently recorded reject reason for `<repo_key>#<issue>` /
    `kind`, or None. Tolerates a malformed/empty file rather than raising."""
    try:
        with open(reject_path(repo_key, issue, kind), encoding="utf-8") as fh:
            body = fh.read(2000).rstrip("\n")
    except OSError:
        return None
    if "\t" not in body:
        return None
    _, _, reason = body.partition("\t")
    return reason if reason else None


def required_refs(refs, cwd, state_of=None):
    """Filter `refs` (issue numbers with no marker yet) down to the ones
    that STILL require a design-comment marker: drop any that are already
    CLOSED on GitHub at commit time. Fails toward STILL REQUIRED (never
    drops a ref whose state can't be determined) -- gh missing, no
    network, timeout, unexpected output all keep the ref in the required
    set, which is also exactly this gate's pre-#206 unconditional
    behaviour, so this is a pure narrowing of when the gate fires, never a
    widening. `state_of` defaults to `_gh_issue_state`; tests inject a
    stub so this stays pure/offline."""
    state_of = state_of or _gh_issue_state
    return [n for n in refs if state_of(n, cwd) != "CLOSED"]
