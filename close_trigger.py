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

  * GRAMMAR = the ticket's spec PLUS a leading word-boundary (`\b`) AND a
    TRAILING boundary on the number (adversarial review A-F1). The leading
    boundary is FAITHFUL to GitHub (its keywords must be whole words) and stops
    the false-positive class `hotfix:`/`prefix:`/`suffix`/`closer`/`closed-loop`
    (GitHub does NOT auto-close those). The trailing `(?![0-9A-Za-z_])` stops the
    OTHER false-positive class: GitHub does NOT autolink `#N` when the digits are
    glued to a word char (`fix #3d`, `#2fa`, `#4k`, `#12_bar`, `#404page`), but
    DOES autolink `#12` before `-`/`.`/`,`/space -- so the guard blocks the
    latter and passes the former. `\s*` (not `[ \t]*`) so a keyword ending a line
    and `#N` starting the next still matches (GitHub collapses whitespace) --
    fail-SAFE toward blocking (#514): a false block costs one reword to `(#N)`,
    a false pass costs an unwanted auto-close (the incident). The ref alternation
    also carries `GH-N` and a full issue URL (`[https://]github.com/o/r/issues/N`)
    as FAIL-SAFE additions (A-F3): whether GitHub's COMMIT-message closer honours
    those (vs only a PR body) is unverified, but blocking them costs at most a
    rare reword and is consistent with the ticket's own already-fail-safe
    inclusion of the cross-repo `owner/repo#N` form.

  * MESSAGE EXTRACTION reuses `block-ungated-issue-filing.sh`'s already-hardened
    `split_top_level`/`strip_prefix`/`_apply_cd`/heredoc-capture (read its
    source, per investigate-existing-first, rather than a custom parser).
    `split_top_level`'s in-double-quote branch is backslash-aware (review B-F4:
    an escaped `\"` inside `-m "..."` must not toggle quote state, else a
    following `&&` wrongly splits the trigger into a non-commit segment).
    Messages are pulled from the shlex TOKENS of each `git commit` segment
    (`comments=True`, so a trailing `# fixes #99` shell comment is stripped and
    never mistaken for the message -- verified), which also captures the inline
    `-m "$(cat <<'EOF' ... EOF)"` recipe. `-F`/`--file` content that lives
    OUTSIDE the segment text is resolved and scanned: a real file on disk, a
    `cat > f <<EOF` heredoc, a `-F -` direct heredoc, AND (review B-F3) a
    `printf ... > f` / `echo ... > f` redirect written in the SAME command
    (which does not exist on disk yet at PreToolUse time).

  * BYPASS MARKER (`# airuleset:close-trigger-ok`) is detected by
    `has_bypass_marker`, which greps a SKELETON with every message body removed
    (heredoc bodies blanked + a backslash-aware quote strip). This is review
    B-F1/B-F2: the earlier bash `sed` quote-strip left the marker exposed when it
    sat inside a heredoc body or behind an escaped `\"`, silently disabling the
    guard for any commit whose message merely MENTIONS the marker (e.g. a commit
    to this very hook). The marker only counts when it is real shell text
    (a comment / bare token), never message content.

  * ACCEPTED RESIDUALS (documented, not chased): `-m "$(cat file)"` /
    `-F "$(cmd)"` (a command-substitution body, not a heredoc/plain-path/
    printf-echo redirect) is not read; a same-command redirect via a NON
    printf/echo producer (`mytool > f && git commit -F f`) or via a pipe
    (`printf x | tee f`) is not captured; `-c`/`-C`/`--reuse-message` and `-t
    <template>` are ignored; `git commit` with no `-m`/`-F` (an editor session a
    non-interactive worker cannot open) has no message to scan. Each fails toward
    NOT blocking, the same offline/unmeasurable bias every regex in this fleet's
    hooks takes -- and every one is separately caught by the supervisor's
    post-hoc scan + the review that follows.
"""
import os
import re
import shlex

BYPASS_MARKER = "airuleset:close-trigger-ok"

# GitHub's real closing grammar + leading word-boundary + trailing number
# boundary. Case-insensitive.
#   keyword = close/closes/closed | fix/fixes/fixed | resolve/resolves/resolved
#   then    = optional ':' , optional whitespace (incl. newline)
#   ref     = #N | owner/repo#N | GH-N | [https://]github.com/o/r/issues/N
#             (all forbidding a trailing word char, so #12foo / GH-3x never match)
_REF = (
    r"(?:"
    r"#[0-9]+"
    r"|GH-[0-9]+"
    r"|[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[0-9]+"
    r"|(?:https?://)?github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/issues/[0-9]+"
    r")(?![0-9A-Za-z_])"
)
CLOSE_TRIGGER_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s*" + _REF,
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
# Command parsing -- shapes ported from block-ungated-issue-filing.sh (quote-
# aware top-level split, prefix strip, cd-tracking, heredoc capture).
# --------------------------------------------------------------------------- #

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r"^\s*cat\s*>>?\s*([^\s<>&;|]+)")
# A `>`/`>>` redirect target (the `cat > f <<EOF` recipe AND the review B-F3
# `printf ... > f` / `echo ... > f` shape). Deliberately simple, same "cost of a
# miss is low" tradeoff design_gate._REDIR_RX makes.
_REDIR_RX = re.compile(r"(?:^|[\s;&|(])(?:\d?>>?)\s*(['\"]?)([^\s'\">|;&]+)\1")


def split_top_level(text):
    """Split on &&/||/;/&/|/newline, QUOTE-AWARE -- a separator inside a real
    quoted message must never be treated as a command boundary. The in-double-
    quote branch is backslash-aware (review B-F4): `\\"` inside `"..."` is an
    escaped quote and must NOT close the string, or a following `&&` splits the
    value. Single quotes do not process backslashes, per POSIX shell."""
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if quote == '"' and c == "\\" and i + 1 < n:
                buf.append(c)
                buf.append(text[i + 1])
                i += 2
                continue
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


def _heredoc_skeleton(lines):
    """Return (file_bodies, direct_bodies, skeleton_lines): a `cat > FILE
    <<DELIM` heredoc body keyed by FILE; a bare `<<DELIM` body keyed by DELIM;
    and the lines with every heredoc BODY blanked (trigger/closing kept)."""
    n = len(lines)
    file_bodies, direct_bodies = {}, {}
    skeleton = list(lines)
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
        for k in range(i + 1, min(j, n)):
            skeleton[k] = ""
        i = j + 1
    return file_bodies, direct_bodies, skeleton


def _capture_heredocs(lines):
    """Back-compat wrapper: (file_bodies, direct_bodies) only."""
    fb, db, _ = _heredoc_skeleton(lines)
    return fb, db


def _strip_quoted(text):
    """Blank every quoted span (single OR double), backslash-aware for double
    quotes -- so message content can never leak into the bypass-marker
    skeleton. Unclosed final quote: blank to end (fail toward removing content,
    never toward exposing it)."""
    out, i, n, quote = [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            if quote == '"' and c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def has_bypass_marker(cmd):
    """True iff the deliberate `# airuleset:close-trigger-ok` bypass marker is
    present as REAL shell text (a comment / bare token), never inside a message
    body. Reviews B-F1/B-F2: the marker is checked on a SKELETON with heredoc
    bodies blanked AND all quoted spans removed, so a marker that merely appears
    in the committed message (`-F -` heredoc body, `cat > f <<EOF` body, or
    behind an escaped `\\"` in `-m "..."`) does NOT disable the guard."""
    _, _, skeleton_lines = _heredoc_skeleton((cmd or "").split("\n"))
    skeleton = _strip_quoted("\n".join(skeleton_lines))
    return BYPASS_MARKER in skeleton


def _is_git_commit(tk):
    """True iff `tk` is a `git ... commit ...` invocation (handles global
    options `git -C <path> commit`, `git -c k=v commit`, `git --no-pager
    commit`) OR the dashed `git-commit` form (review A-F2). A non-option token
    before `commit` means a different subcommand (`git log`, `git commit-graph`)."""
    if not tk:
        return False
    if tk[0] == "git-commit":
        return True
    if tk[0] != "git":
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


def _capture_redirect_writes(cmd):
    """{target_path: written_text} for every `printf ... > f` / `echo ... > f`
    (and `>>`) in `cmd` -- review B-F3: a message file created by a redirect in
    the SAME command does not exist on disk yet at PreToolUse time, so it must
    be read from the command text. Only printf/echo (whose literal args ARE the
    content) are captured; a general `somecmd > f` producer is a documented
    residual. First-writer wins per target is irrelevant here -- we scan every
    captured body anyway."""
    writes = {}
    for seg in split_top_level(cmd):
        redir = _REDIR_RX.search(seg)
        if not redir:
            continue
        target = redir.group(2)
        tk = strip_prefix(tokens_of(seg))
        if not tk or tk[0] not in ("printf", "echo"):
            continue
        # content = the literal args between the command and the redirect,
        # minus echo/printf flags. shlex already dropped the `> f` operator
        # tokens? No -- shlex keeps `>` as a token; collect args before it.
        args = []
        for t in tk[1:]:
            if t in (">", ">>") or t.startswith(">"):
                break
            if t.startswith("-") and t.lstrip("-") and all(
                    ch in "en" for ch in t.lstrip("-")):
                continue  # echo/printf -e/-n/-E flags carry no content
            args.append(t)
        body = " ".join(args)
        if body:
            writes.setdefault(target, body)
    return writes


def _resolve_file(val, seg_line, eff_cwd, file_bodies, direct_bodies,
                  redirect_writes):
    """The text of a `-F`/`--file` argument: a `-F -` direct heredoc body, a
    `cat > file <<EOF` heredoc body, a `printf/echo > file` redirect body
    written in the same command, or a real file read from disk (resolved against
    the cd-tracked effective cwd). "" on anything unreadable/unresolvable (fail
    toward NOT scanning -- never raises)."""
    if val == "-":
        m = HEREDOC_RE.search(seg_line.rstrip())
        if m and m.group(2) in direct_bodies:
            return direct_bodies[m.group(2)]
        return ""
    if val in file_bodies:
        return file_bodies[val]
    if val in redirect_writes:
        return redirect_writes[val]
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
    body) plus each -F/--file's resolved content (disk / cat-heredoc / -F -
    heredoc / printf-echo same-command redirect). cd-tracked. Empty texts
    dropped."""
    file_bodies, direct_bodies = _capture_heredocs(cmd.split("\n"))
    redirect_writes = _capture_redirect_writes(cmd)
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
                texts.append(_resolve_file(
                    val, seg, eff_cwd, file_bodies, direct_bodies,
                    redirect_writes))
    return [t for t in texts if t]


def scan_commit_command(cmd, cwd):
    """The first close-trigger found in any commit message a `git commit` in
    `cmd` would use, or None. The hook calls this only after `is_worker_context`
    has confirmed a worker/worktree context and `has_bypass_marker` is false."""
    for text in commit_message_texts(cmd, cwd):
        hit = find_close_trigger(text)
        if hit:
            return hit
    return None
