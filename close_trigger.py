r"""GitHub close-trigger grammar detection for worker/worktree commits (#567).

THE PROBLEM. GitHub's issue-linking grammar auto-closes an issue from a commit
message reachable on the default branch whenever it contains a closing KEYWORD
(close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved) followed by an
OPTIONAL colon, optional whitespace, then `#N` (or `owner/repo#N`). A worktree /
autopilot WORKER must NEVER emit such a trigger -- the supervisor closes the
ticket with evidence AFTER review (MEMORY.md #152/#348); a worker's commit that
auto-closes bypasses that review the instant the supervisor merges to main.

Live incident (#564, 2026-08-19): the worker commit `e2933ca0` titled
`fix: #564 review -- ...` auto-closed #564 at 08:20:38Z because the grammar
accepts the OPTIONAL COLON (`fix: #564`). The documented supervisor/resume scan
required a literal space (`\s+`), so it missed the colon form entirely -- 3rd
incident of the class after #152/#348. The ban was prompt-prose + a post-hoc
scan only; nothing blocked the commit at WRITE time. This module is the
mechanical write-time detector `hooks/block-worker-close-trigger.sh` calls.

DESIGN NOTES (full rationale on issue #567's design comment):

  * GRAMMAR = the ticket's spec PLUS a leading word-boundary (`\b`). The
    boundary is FAITHFUL to GitHub (its keywords must be whole words) and stops
    the false-positive class the bare spec would create -- `hotfix: #12`,
    `prefix: #12`, `suffix #12`, `closer #12`, `closed-loop #12` do NOT
    auto-close on GitHub (keyword is not a standalone word), so blocking them
    would false-block legit commits. `\s*` (not `[ \t]*`) so a keyword ending a
    line and `#N` starting the next still matches (GitHub collapses whitespace)
    -- fail-SAFE toward blocking (#514): a false block costs one reword to
    `(#N)`, a false pass costs an unwanted auto-close (the incident).

  * MESSAGE EXTRACTION reuses `block-ungated-issue-filing.sh`'s already-hardened
    `split_top_level`/`strip_prefix`/`_apply_cd`/heredoc-capture (read its
    source, per investigate-existing-first, rather than a custom parser).
    Messages are pulled from the shlex TOKENS of each `git commit` segment
    (`comments=True`, so a trailing `# fixes #99` shell comment is stripped and
    never mistaken for the message -- verified), which also captures the inline
    `-m "$(cat <<'EOF' ... EOF)"` recipe (shlex keeps the heredoc body inside
    the double-quoted `-m` value). `-F`/`--file` content that lives OUTSIDE the
    segment text (a separate file on disk, a `cat > f <<EOF` earlier in the same
    command, or a `-F -` direct heredoc) is resolved and scanned separately.

  * ACCEPTED RESIDUALS (documented, not chased): `-m "$(cat file)"` /
    `-F "$(cmd)"` (a command-substitution message body, not a heredoc or a
    plain path) is not read; `-c`/`-C`/`--reuse-message` (reuse an existing
    commit's message -- referenced by hash, not readable text) and `-t
    <template>` are ignored; `git commit` with no `-m`/`-F` (an editor session,
    which a non-interactive worker cannot open) has no message to scan. Each
    fails toward NOT blocking, the same offline/unmeasurable bias every regex in
    this fleet's hooks already takes -- and every one of them is separately
    caught by the supervisor's post-hoc scan + the review that follows.
"""
import os
import re
import shlex

# GitHub's real closing grammar + a leading word-boundary. Case-insensitive.
#   keyword = close/closes/closed | fix/fixes/fixed | resolve/resolves/resolved
#   then    = optional ':' , optional whitespace (incl. newline)
#   ref     = #N  |  owner/repo#N   (cross-repo form)
CLOSE_TRIGGER_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
    r":?\s*"
    r"(?:#[0-9]+|[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[0-9]+)",
    re.IGNORECASE,
)


def find_close_trigger(text):
    """The first close-trigger substring in `text` (e.g. "fix: #564"), or None.
    Returned verbatim so the hook can quote exactly what it matched."""
    if not text:
        return None
    m = CLOSE_TRIGGER_RE.search(text)
    return m.group(0) if m else None


def is_worker_context(cwd, agent_type):
    """True iff this commit is a WORKER/WORKTREE commit -- the only context the
    close-trigger ban applies to. UNION of two proven signals: an
    `autopilot-worker` subagent (agent_type, the same field
    block-commit-without-design.sh reads -- catches a serial-fallback worker
    whose cwd is the main checkout) OR a session cwd inside an isolated worktree
    (the #564 vector; the same `*/.claude/worktrees/*` shape
    block-foreign-airuleset-write.sh rule B keys on -- `.cwd` is the STABLE
    session path, never a mid-Bash `cd`). A MAIN session (no agent_type, cwd NOT
    under a worktree -- the supervisor, or any ordinary project session) is
    NEVER a worker context, so its deliberate `Closes #N` stays possible."""
    if agent_type == "autopilot-worker":
        return True
    if cwd and "/.claude/worktrees/" in cwd:
        return True
    return False


# --------------------------------------------------------------------------- #
# Command parsing -- shapes ported VERBATIM from block-ungated-issue-filing.sh
# (quote-aware top-level split, prefix strip, cd-tracking, heredoc capture).
# --------------------------------------------------------------------------- #

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r"^\s*cat\s*>>?\s*([^\s<>&;|]+)")


def split_top_level(text):
    """Split on &&/||/;/&/|/newline, QUOTE-AWARE -- a separator inside a real
    quoted message must never be treated as a command boundary."""
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    idx = 0
    while idx < len(tk):
        t = tk[idx]
        if t in ("sudo", "env") or t in LOOP_BODY_KEYWORDS or ASSIGN_RE.match(t):
            idx += 1
            continue
        break
    return tk[idx:]


def _cd_target(tk):
    target = None
    for t in tk[1:]:
        if t == "--":
            continue
        if t.startswith("-"):
            continue
        target = t
        break
    if target is None:
        return None
    if any(ch in target for ch in "$~*?`"):
        return None
    return target


def _apply_cd(base, tk):
    target = _cd_target(tk)
    if target is None:
        return None
    if os.path.isabs(target):
        return os.path.normpath(target)
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, target))


def _capture_heredocs(lines):
    """(file_bodies, direct_bodies): a `cat > FILE <<DELIM` heredoc's body keyed
    by FILE; a bare `<<DELIM` (no `cat >` in front -- e.g. `git commit -F -
    <<DELIM`) keyed by DELIM. Same pass-1 shape as block-ungated-issue-filing."""
    n = len(lines)
    file_bodies, direct_bodies = {}, {}
    i = 0
    while i < n:
        line = lines[i]
        mm = HEREDOC_RE.search(line.rstrip())
        if not mm:
            i += 1
            continue
        delim = mm.group(2)
        strip_leading = "<<-" in line
        body = []
        j = i + 1
        while j < n:
            check = lines[j].lstrip("\t") if strip_leading else lines[j]
            if check == delim:
                break
            body.append(lines[j])
            j += 1
        body_text = "\n".join(body)
        fm = CATFILE_RE.match(line)
        if fm:
            file_bodies[fm.group(1)] = body_text
        else:
            direct_bodies[delim] = body_text
        i = j + 1
    return file_bodies, direct_bodies


def _is_git_commit(tk):
    """True iff `tk` is a `git ... commit ...` invocation (handles the global
    options `git -C <path> commit`, `git -c k=v commit`, `git --no-pager
    commit`). A non-option token before `commit` means a different subcommand
    (`git log`, `git commit-graph`)."""
    if not tk or tk[0] != "git":
        return False
    i = 1
    while i < len(tk):
        t = tk[i]
        if t == "commit":
            return True
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                 "--config-env", "--exec-path"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return False
    return False


def _iter_flags(tk):
    """Yield (kind, value) for every -m/--message ("m") and -F/--file ("F")
    argument in `tk`, handling separate (`-m V`), long-equals (`--message=V`),
    glued short (`-mV`), and last-in-bundle short (`-am V`). Stops at a `--`
    end-of-options marker."""
    i, n = 0, len(tk)
    while i < n:
        t = tk[i]
        if t == "--":
            break
        adv = 1
        if t in ("-m", "--message"):
            if i + 1 < n:
                yield ("m", tk[i + 1])
                adv = 2
        elif t.startswith("--message="):
            yield ("m", t[len("--message="):])
        elif t in ("-F", "--file"):
            if i + 1 < n:
                yield ("F", tk[i + 1])
                adv = 2
        elif t.startswith("--file="):
            yield ("F", t[len("--file="):])
        elif len(t) >= 2 and t[0] == "-" and t[1] != "-":
            body = t[1:]
            pm, pf = body.find("m"), body.find("F")
            cands = [(p, c) for p, c in ((pm, "m"), (pf, "F")) if p != -1]
            if cands:
                p, c = min(cands)
                rest = body[p + 1:]
                if rest:
                    yield (c, rest)
                elif i + 1 < n:
                    yield (c, tk[i + 1])
                    adv = 2
        i += adv


def _resolve_file(val, seg_line, eff_cwd, file_bodies, direct_bodies):
    """The text of a `-F`/`--file` argument: a `-F -` direct heredoc body, a
    `cat > file <<EOF` heredoc body captured earlier in the same command, or a
    real file read from disk (resolved against the cd-tracked effective cwd).
    "" on anything unreadable/unresolvable (fail toward NOT scanning -- never
    raises)."""
    if val == "-":
        m = HEREDOC_RE.search(seg_line.rstrip())
        if m and m.group(2) in direct_bodies:
            return direct_bodies[m.group(2)]
        return ""
    if val in file_bodies:
        return file_bodies[val]
    if os.path.isabs(val):
        path = val
    elif eff_cwd is not None:
        path = os.path.join(eff_cwd, val)
    else:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def commit_message_texts(cmd, cwd):
    """Every commit-message text a `git commit` in `cmd` would use: each
    -m/--message value (which also carries an inline `-m "$(cat <<EOF ...)"`
    body, since shlex keeps it inside the quoted value) plus each -F/--file's
    resolved content. cd-tracked so a `cd <dir> && git commit -F rel` reads
    `rel` from `<dir>`. Empty texts dropped."""
    file_bodies, direct_bodies = _capture_heredocs(cmd.split("\n"))
    texts = []
    eff_cwd = cwd
    for seg in split_top_level(cmd):
        if not seg.strip():
            continue
        tk = strip_prefix(tokens_of(seg))
        if tk and tk[0] == "cd":
            eff_cwd = _apply_cd(eff_cwd, tk)
            continue
        if not _is_git_commit(tk):
            continue
        for kind, val in _iter_flags(tk):
            if kind == "m":
                texts.append(val)
            else:
                texts.append(
                    _resolve_file(val, seg, eff_cwd, file_bodies, direct_bodies))
    return [t for t in texts if t]


def scan_commit_command(cmd, cwd):
    """The first close-trigger found in any commit message a `git commit` in
    `cmd` would use, or None. The whole check -- the hook calls this only after
    `is_worker_context` has already confirmed a worker/worktree context."""
    for text in commit_message_texts(cmd, cwd):
        hit = find_close_trigger(text)
        if hit:
            return hit
    return None
