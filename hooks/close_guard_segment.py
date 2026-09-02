#!/usr/bin/env python3
r"""Quote/backslash-aware segmenter for block-fork-no-merge-issue-close.sh (#837).

Reads the raw Bash command on STDIN and emits KEY=VALUE derived signals the shell
hook consumes. This REPLACES the hook's sed/grep EXTRACTION + COUNTING + DETECTION
layer, whose text-scan could not model bash's quote/backslash/operator grammar — six
hardening rounds (#533 -> #540 -> #756 -> #807 -> #816 -> #824) each patched one
tokenization divergence and opened another. Four residual CLASSES were structural
(a regex fundamentally cannot reach them) and are the charter of this module:

  A-4  a `-c`/`--comment` INSIDE a quoted argument makes the sed value-strip span a
       REAL top-level `gh issue close`, erasing it -> the close count under-counts.
  REPO_ARG-in-quoted-arg  the whole-command REPO_ARG grep reads `-R x/y` from inside
       a balanced-quote argument (`gh issue close 100 && echo 'foo -R x/y'`) ->
       poisons the author read against the wrong repo.
  N-6  a STANDALONE quoted/backslashed/aliased command word (`"gh" issue close`,
       `g\h`, `gh issue clo\se`, `gh issue "close"`, `/usr/bin/gh`) bypasses the
       front gate entirely — bash de-quotes the command word, grep cannot.
  N-7  a non-shell interpreter (`python3 -c '…os.system("gh issue close")'`) hides a
       nested close the shell-only HAS_INTERP enumeration misses.

APPROACH (Fable design consult, #837 design comment): a HYBRID of two proven tools —
(1) `close_trigger.split_top_level` for TOP-LEVEL segment boundaries (quote-aware +
backslash-aware, already used by block-ungated-issue-filing.sh); (2) stdlib
`shlex.split(posix=True, comments=True)` to TOKENIZE each segment. shlex de-quotes
`"gh"`/`'gh'`/`g\h`/`gh issue clo\se`/`/usr/bin/gh` to the clean `gh` command word for
free (kills N-6); flag/number/repo extraction reads ONLY the close segment's own
tokens, never a whole-command scan (kills A-4 + the poison); interpreter payloads are
recognised structurally (kills N-7).

THE LOAD-BEARING INVARIANT: every command word shlex cannot statically resolve FAILS
CLOSED. A segment whose command word carries an expansion/glob/redirect char
(`$(:)gh`, `${x}gh`, `$'gh'`->`$gh`, `{g,}h`, `gh</dev/null`), a `$'…'` ANSI-C
subcommand, or that raises a shlex ValueError, is treated as a suspicious close-carrier
(-> HAS_INTERP -> the single-action guard blanks ISSUE_NUM -> BLOCK) rather than being
decoded per-case. shlex cannot emulate bash EXPANSION (`$VAR`, arithmetic, globs), so
the only sound use is this invariant, not a growing set of decoders.

FAIL DIRECTION is uniform with the hook's #349/#463 discipline: any ambiguity -> BLOCK.
On ANY internal error this module prints NOTHING; the shell driver then fails CLOSED.

OUTPUT PROTOCOL (stdout, one per line; the leading `OK` proves a clean analysis):
    OK
    IS_CLOSE=<0|1>            any close action anywhere (front gate)
    N_CLOSE=<int>            count of CLEAN top-level `gh issue close` actions
    ISSUE_NUM=<digits|>      the first clean close's target number
    REPO_ARG=<owner/repo|>   the close segment's -R/--repo value, CLEAN (empty if glued)
    REPO_FLAG_PRESENT=<0|1>  a -R/--repo flag token is present in the close segment
    HAS_INTERP=<0|1>         a nested/hidden/suspicious close-carrier is present
    HAS_PATCH_CLOSE=<0|1>    a `gh api ... PATCH` close form is present
    D_REPO_ARG=<owner/repo|> the first close segment's -R value, GLUED-TOLERANT (Discuss)
    D_NUMS=<space-sep|>      every clean top-level close number (Discuss gate)
"""

import os
import re
import shlex
import sys

# Reuse the repo's proven quote/backslash-aware top-level splitter (splits on
# && || ; & | newline). Importing keeps ONE grammar for both this hook and
# block-ungated-issue-filing.sh / block-worker-close-trigger.sh.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import close_trigger  # noqa: E402  (fail-closed if this import raises)

# A command word shlex de-quoted is UNRESOLVABLE (cannot be trusted as the literal
# program name) if it still carries any shell expansion / glob / redirect / subshell
# metacharacter. `$(:)gh`, `${x}gh`, `$gh` (from `$'gh'`), `{g,}h`, `gh</dev/null`.
_UNRESOLVABLE_CHARS = set("$`{}<>*?()[]")

# Wrapper commands whose FIRST real argument is the actual program. A wrapper's own
# leading `-flags`, numeric values (`timeout 300`, `nice -n 19`) and env-assignments
# (`env X=1`) are skipped so the effective command word is reached.
_WRAPPERS = {
    "sudo", "env", "command", "nohup", "exec", "setsid", "doas",
    "time", "timeout", "nice", "ionice", "stdbuf", "chrt",
}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")

# Interpreters that can carry a nested close in an argument.
_SHELL_INTERP = {"bash", "sh", "dash", "zsh", "ksh", "ash", "mksh"}
_GENERIC_INTERP = {"python", "python2", "python3", "perl", "ruby", "node", "nodejs", "php"}
_UNCOND_INTERP = {"eval", "xargs"}
_INTERP_C_FLAGS = {"-c", "-e", "-E"}

# `gh issue close` phrase inside a de-quoted interpreter payload / hidden string.
# Allows an absolute path to gh; anchored so `github` / `weight` never match.
_CLOSE_PHRASE_RE = re.compile(r"(?:^|[^\w/])(?:\S*/)?gh\s+issue\s+close\b", re.I)

# A CLEAN, LITERAL `gh issue close` phrase in the RAW segment (a real command
# boundary, an optional `/`-path prefix, and the keywords unbroken by any quote or
# backslash). A segment that shlex recognises as `gh issue close` ONLY after removing
# a quote/backslash from the keywords (`\gh`, `"gh"`, `g\h`, `gh issue clo\se`,
# `gh issue "close"`) does NOT match this → it is treated as SUSPICIOUS (fail closed),
# preserving the pre-#837 over-block of a backslash/quote-obfuscated close, while a
# plain absolute path `/usr/bin/gh issue close` DOES match → a legit clean close.
_CLEAN_LITERAL_CLOSE_RE = re.compile(
    r"(?:^|[;&|\s(])(?:/\S+/)?gh\s+issue\s+close(?:\s|$)")

# Nested-close smuggle via command substitution / funsub / backtick, scanned on the
# RAW (de-backslashed) command — mirrors the current bash `$(`/`${ }`/backtick guards
# (a `$(` or `${[ |]` carrying `gh<ws>`, or ANY backtick carrying `gh<ws>`). `[^)]*`/
# `[^}]*` span newlines (no `.`), matching the bash `grep -z` behaviour.
# ACCEPTED RESIDUAL (Fable review #837, IDENTICAL to the pre-#837 `\$\([^)]*gh` guard —
# NOT a regression): a `)` before `gh` INSIDE a nested substitution
# (`--comment "$( ( : ) && gh issue close 999 )"`) ends `[^)]*` early → the nested
# close is missed. Modelling nested parens is the exact "regex can't parse bash" trap
# this module bounds elsewhere via fail-closed; a confusion guard (not adversarial-
# complete) accepts it, as the old hook did.
_SUBST_CLOSE_RE = re.compile(r"\$\([^)]*gh\s", re.I)
_FUNSUB_CLOSE_RE = re.compile(r"\$\{[\s|][^}]*gh\s", re.I)
_BACKTICK_CLOSE_RE = re.compile(r"`[^`]*gh\s", re.I)

# `gh api ... PATCH` close forms (faithful port of _is_patch_close_cmd). The state may
# be VISIBLE (bare or value-quoted) or HIDDEN in the request body (--input / -F ...=@).
_PATCH_ISSUES_RE = re.compile(r"gh\s+api[^|]*issues/[0-9]+")
_PATCH_METHOD_RE = re.compile(r"(?:-X|--method)[\s=]*PATCH", re.I)
_PATCH_STATE_VISIBLE_RE = re.compile(r"""state=["']?closed""")
_PATCH_INPUT_RE = re.compile(r"(?:^|\s)--input(?:[\s=]|$)")
_PATCH_FIELD_FILE_RE = re.compile(r"(?:-F|--field)[\s=]+[^\s]*=@")


def _has_close_phrase(text):
    """True iff `text`, de-backslashed and de-quoted, contains a `gh issue close`
    phrase. Used on interpreter payloads and any de-quoted hidden string."""
    t = (text or "").replace("\\", "")
    t = t.replace('"', "").replace("'", "")
    return bool(_CLOSE_PHRASE_RE.search(t))


def _unresolvable(word):
    return any(c in _UNRESOLVABLE_CHARS for c in word)


def _effective_word_index(tokens):
    """Index of the effective command word (skip env-assignments + wrappers and a
    wrapper's own leading -flags / numeric values / assignments)."""
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if _ASSIGN_RE.match(t):
            i += 1
            continue
        if t in _WRAPPERS:
            i += 1
            while i < n and (tokens[i].startswith("-") or tokens[i].isdigit()
                             or _ASSIGN_RE.match(tokens[i])):
                i += 1
            continue
        break
    return i


def _is_patch_close(cmd):
    if not _PATCH_ISSUES_RE.search(cmd):
        return False
    if not _PATCH_METHOD_RE.search(cmd):
        return False
    return bool(_PATCH_STATE_VISIBLE_RE.search(cmd)
                or _PATCH_INPUT_RE.search(cmd)
                or _PATCH_FIELD_FILE_RE.search(cmd))


def _parse_gh_args(args):
    """Parse gh's arguments (everything after the `gh` command word) into positional
    tokens + the first -R/--repo. `-R`/`--repo` may sit ANYWHERE (a global flag before
    `issue`, or a close flag after). Value-flags consume their following value so it is
    never mistaken for the positional number. Returns:
        (positional, repo_clean, repo_glued, repo_flag_present)
    repo_clean is empty for a GLUED `-Rvalue` (present-but-unparseable fail-safe the
    143 tests lock); repo_glued reads the glued value (the Discuss gate is glued-tolerant).
    """
    positional = []
    repo_clean = ""
    repo_glued = ""
    repo_flag = False
    i, n = 0, len(args)
    while i < n:
        t = args[i]
        if t in ("-R", "--repo"):
            if not repo_flag:
                repo_flag = True
                if i + 1 < n:
                    repo_clean = args[i + 1]
                    repo_glued = args[i + 1]
            i += 2
            continue
        if t.startswith("-R=") and not repo_flag:
            repo_flag = True
            repo_clean = repo_glued = t[3:]
            i += 1
            continue
        if t.startswith("--repo=") and not repo_flag:
            repo_flag = True
            repo_clean = repo_glued = t[7:]
            i += 1
            continue
        if t.startswith("-R") and len(t) > 2 and not repo_flag:
            repo_flag = True
            repo_clean = ""          # glued -Rvalue -> unparseable (fail-safe)
            repo_glued = t[2:]
            i += 1
            continue
        if t.startswith("--repo") and len(t) > 6 and not repo_flag:
            repo_flag = True
            repo_clean = ""
            repo_glued = t[6:]
            i += 1
            continue
        if t in ("--comment", "-c", "--reason", "-r", "-b", "--body"):
            i += 2                   # value-flag: skip its value
            continue
        if t.startswith("-"):
            i += 1                   # other flag (boolean or glued value)
            continue
        positional.append(t)
        i += 1
    return positional, repo_clean, repo_glued, repo_flag


def analyze(cmd):
    """Return the derived-signal dict, or None on any internal failure (fail closed)."""
    # Strip backslash-newline line continuations before segmentation (bash removes
    # them; a segmenter must not split there and shlex must not read `\<nl>` as an
    # escaped newline). Advisor pitfall #2.
    cmd = cmd.replace("\\\n", "")

    segments = close_trigger.split_top_level(cmd)

    n_close = 0
    issue_num = ""
    repo_arg = ""
    repo_flag_present = False
    d_repo_arg = ""
    d_nums = []
    has_interp = False
    suspicious = False
    first_close_seen = False

    for seg in segments:
        if not seg.strip():
            continue
        raw = seg.replace("\\", "")   # de-backslashed raw for the substitution scans

        # Nested-close smuggle in a value / substitution (quoted or not).
        if (_SUBST_CLOSE_RE.search(raw) or _FUNSUB_CLOSE_RE.search(raw)
                or _BACKTICK_CLOSE_RE.search(raw)):
            has_interp = True

        try:
            tokens = shlex.split(seg, posix=True, comments=True)
        except ValueError:
            # Unbalanced quote / dangling escape: shlex cannot see inside it.
            suspicious = True
            continue
        if not tokens:
            continue

        wi = _effective_word_index(tokens)
        if wi >= len(tokens):
            continue
        word = tokens[wi]
        sub = tokens[wi:]
        args = sub[1:]
        positional, r_clean, r_glued, r_flag = _parse_gh_args(args)
        is_close_shape = (len(positional) >= 2
                          and positional[0] == "issue" and positional[1] == "close")

        if not _unresolvable(word) and os.path.basename(word) == "gh":
            if is_close_shape and _CLEAN_LITERAL_CLOSE_RE.search(seg):
                # A CLEAN, LITERAL top-level `gh issue close` (the keywords are not
                # quote/backslash-obfuscated in the raw segment).
                n_close += 1
                num = ""
                if len(positional) >= 3:
                    m = re.match(r"^#?([0-9]+)$", positional[2])
                    if m:
                        num = m.group(1)
                if num:
                    d_nums.append(num)
                if not first_close_seen:
                    first_close_seen = True
                    issue_num = num
                    repo_arg = r_clean
                    repo_flag_present = r_flag
                    d_repo_arg = r_glued
            elif is_close_shape:
                # shlex recognised `gh issue close` only after removing a quote/
                # backslash from the keywords (`\gh`, `"gh"`, `gh issue "close"`) ->
                # fail closed (the pre-#837 over-block posture).
                suspicious = True
            elif (len(positional) >= 2 and positional[0] == "issue"
                  and (_unresolvable(positional[1]) or "$'" in raw)):
                # `gh issue $'close'`-style obfuscated subcommand -> fail closed.
                suspicious = True
            continue

        if _unresolvable(word):
            # `$(:)gh`/`${x}gh`/`$gh`/`{g,}h`/`gh</dev/null` carrying `issue close`.
            if is_close_shape:
                suspicious = True
            continue

        # A resolvable NON-gh command word: is it an interpreter carrying a close?
        wb = os.path.basename(word)
        payload = " ".join(args)
        if wb in _UNCOND_INTERP:
            if _has_close_phrase(payload):
                has_interp = True
        elif wb in _SHELL_INTERP and "-c" in args:
            if _has_close_phrase(payload):
                has_interp = True
        elif wb in _GENERIC_INTERP and any(f in _INTERP_C_FLAGS for f in args):
            if _has_close_phrase(payload):
                has_interp = True

    has_patch = _is_patch_close(cmd)
    if suspicious:
        has_interp = True

    is_close = has_patch or n_close >= 1 or has_interp
    return {
        "IS_CLOSE": "1" if is_close else "0",
        "N_CLOSE": str(n_close),
        "ISSUE_NUM": issue_num,
        "REPO_ARG": repo_arg,
        "REPO_FLAG_PRESENT": "1" if repo_flag_present else "0",
        "HAS_INTERP": "1" if has_interp else "0",
        "HAS_PATCH_CLOSE": "1" if has_patch else "0",
        "D_REPO_ARG": d_repo_arg,
        "D_NUMS": " ".join(d_nums),
    }


def main():
    try:
        cmd = sys.stdin.read()
    except Exception:
        return  # print nothing -> shell fails closed
    try:
        result = analyze(cmd)
    except Exception:
        return  # print nothing -> shell fails closed
    if result is None:
        return
    out = ["OK"]
    for key in ("IS_CLOSE", "N_CLOSE", "ISSUE_NUM", "REPO_ARG", "REPO_FLAG_PRESENT",
                "HAS_INTERP", "HAS_PATCH_CLOSE", "D_REPO_ARG", "D_NUMS"):
        out.append("%s=%s" % (key, result[key]))
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
