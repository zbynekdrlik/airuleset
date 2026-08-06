"""Payload-vs-control-flow classifier for the poll-loop detectors (airuleset
#124). Called by hooks/lib-poll-payload.sh with ONE argument: the path of a
file holding the Bash command. Prints the command with provably-inert payload
blanked out; the rationale lives in lib-poll-payload.sh's header.

A separate .py rather than an embedded heredoc for two reasons: the command is
read from a FILE (Linux MAX_ARG_STRLEN caps a single argv entry at ~131 kB, and
the largest documentation writes are exactly the class this exists for), and a
`python3 - <<'PYEOF'` heredoc inside a shell function cannot be edited from a
tool call that is itself a heredoc without the delimiters colliding.
"""

import re
import sys

# `(?<!<)` keeps a here-STRING (`<<<'x'`) out: it matches at offset 1 of `<<<`,
# no later line ever equals that "delimiter", and heredoc_spans would then
# swallow the whole rest of the command as an inert body.
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Anything that could EXECUTE what follows. An unrecognised word means "may be
# live", which means "do not strip" — this list only ever has to grow.
INTERP = re.compile(
    r"(?:^|[\s;&|(<'\"])"
    r"(?:bash|sh|zsh|ksh|dash|python3?|perl|ruby|node|deno|php|lua"
    r"|psql|mysql|sqlite3|ssh|sshpass|docker|kubectl|sudo|env|xargs|eval|source)"
    r"(?:[\s;&|)'\"]|$)")

REDIR = re.compile(r"(?:^|[\s;&|)])(?:\d?>>?)\s*(['\"]?)([^\s'\">|;&]+)\1")

TEXTSINK = re.compile(r"(?:--body-file|--notes-file|--file|-F)(?:=|\s+)\S*$")

# A heredoc read STRAIGHT by a text sink — `git commit -F -`, `gh issue comment
# -F -` — which is gh-cli-recipes.md's own mandated recipe. Checked only AFTER
# the interpreter test, so `ssh host 'bash -s' <<EOF` and `psql -f - <<EOF`
# still win as live.
SINK_OWNER = re.compile(
    r"(?:^|[\s;&|(])(?:git\s+(?:commit|tag|notes)|gh\s+(?:issue|pr|release)\s+\w+)"
    r"\b[^\n;&|]*(?:-F|--body-file|--notes-file|--file)(?:=|\s+)-(?:\s|$|<)")

# Text-payload flags. `-c` is absent on purpose: `bash -c 'while :; do …; sleep
# 60; done'` is the sanctioned waiter itself. Deliberately matches only up to
# the OPENING quote character (no `.*?...\1` value capture) — the naive
# quote-pair regex used to close at the FIRST quote it saw, escaped or not,
# and had no notion of the shell's `'"'"'` single-quote-splice idiom (airuleset
# #282). `_shell_word_end` below resolves the true value span instead.
BODYFLAG = re.compile(
    r"(?:^|\s)(?:--body|--body-file|--notes|--notes-file|-b|-F)"
    r"(?:=|\s+)(['\"])")
# `-m`/`-t` mean other things to ssh/docker/grep, so these are honoured only in
# a segment that names git/gh and names no interpreter.
MSGFLAG = re.compile(
    r"(?:^|\s)(?:-m|--message|--title|-t)(?:=|\s+)(['\"])")
GITGH = re.compile(r"(?:^|[\s;&|(])(?:git|gh)\s")
SEP = (";", "&&", "||", "|", "\n")


def _shell_word_end(text, i):
    """Index just past the shell WORD beginning at `i` (the position of a
    quote character) — the whole possibly-multi-fragment value a flag
    actually receives, matching real bash tokenization rather than a single
    quote-pair: adjacent quoted/unquoted fragments with NO separating
    whitespace concatenate into ONE argument. That is exactly what the
    standard `'"'"'` single-quote-splice idiom is (close-single /
    open-double-containing-a-lone-quote / close-double / reopen-single, four
    fragments forming one logical value) — a regex anchored on a single
    quote PAIR can never see past the first fragment. Backslash-aware
    inside double quotes (`\\"` does not close the span); single quotes have
    NO escaping at all, per bash semantics (airuleset #282).

    Stops at whitespace, an UNQUOTED shell metacharacter (`;`/`&`/`|`/`(`/
    `)`/`<`/`>`, real bash word terminators outside quotes too — a fix for
    a review-found regression in the fix above: the first cut stopped only
    at whitespace, so a separator glued directly to a value's closing quote
    with no intervening space was consumed INTO the blanked span, eating
    the first word of the very next command), or end-of-string.

    Known, deliberate residual (pre-existing, unaffected either way by
    this scanner — not something #282 introduces or closes): `$( … )`/
    backtick command substitution containing its OWN quote characters
    inside an outer double-quoted value is NOT parsed as nested — those
    inner quotes are read as ordinary boundary characters. A value like
    `--body "$(printf '%s' "hi") gh issue comment 99"` mis-tokenizes
    identically before and after this fix. A full shell-grammar parser
    was rejected as overkill for this narrow question (see the #282
    design comment's own rejected-alternative)."""
    METACHARS = ";&|()<>"
    n = len(text)
    while i < n and not text[i].isspace():
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            i = (j + 1) if j != -1 else n
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            i = (j + 1) if j < n else n
        elif c in METACHARS:
            break
        elif c == "\\" and i + 1 < n:
            i += 2
        else:
            i += 1
    return i


def heredoc_spans(lines):
    """[(owner_idx, body_start, body_end)] — HALF-OPEN LINE RANGES.

    Positional, never by value: two heredocs whose bodies are byte-identical
    (documenting a script, then writing and running it) must be judged
    independently, and a value-based strip blanks both from one verdict.
    """
    out = []
    i = 0
    while i < len(lines):
        consumed = i
        for m in HEREDOC.finditer(lines[i]):
            delim = m.group(2)
            j = consumed + 1
            while j < len(lines) and lines[j].strip() != delim:
                j += 1
            out.append((i, consumed + 1, j))
            consumed = j
        i = consumed + 1
    return out


def consumed_only_as_text(base, remainder):
    """Every mention of `base` outside the write is a text-file argument."""
    if not base or "$" in base or "`" in base:
        return False                     # unresolvable target -> assume live
    for m in re.finditer(re.escape(base), remainder):
        if not TEXTSINK.search(remainder[max(0, m.start() - 60):m.start()]):
            return False
    return True


def is_live(owner, remainder):
    """True unless this heredoc is PROVABLY inert payload."""
    if INTERP.search(owner):
        return True
    if SINK_OWNER.search(owner):
        return False
    reds = REDIR.findall(owner)
    if not reds:
        return True                      # not a plain file write -> unknown
    return not consumed_only_as_text(reds[-1][1].split("/")[-1], remainder)


def flag_spans(text):
    """Offsets of quoted text-payload arguments — never their VALUES.

    Scoped to the flag's OWN command segment: `ssh -t '<loop>'` executes its
    argument remotely, and a whole-command git/gh test would license stripping
    it because some unrelated `git status` appears elsewhere in the line.

    The span covers the WHOLE resolved shell word (`_shell_word_end`), quote
    characters included — never just a single quote-pair's inner text — so a
    multi-fragment splice-idiom value is blanked in one piece rather than
    leaving its later fragments as live-looking text.
    """
    out = []
    for rx, need_gitgh in ((BODYFLAG, False), (MSGFLAG, True)):
        for m in rx.finditer(text):
            head = text[:m.start()]
            cut = max([head.rfind(s) for s in SEP] + [-1])
            seg = text[cut + 1:m.start()]
            if INTERP.search(seg):
                continue
            if need_gitgh and not GITGH.search(seg):
                continue
            start = m.start(1)
            end = _shell_word_end(text, start)
            if end - start > 2:            # more than a bare '' / ""
                out.append((start, end))
    return out


def strip(text):
    lines = text.split("\n")
    spans = heredoc_spans(lines)
    # what the shell itself runs: every body blanked, BY INDEX. The inertness
    # question must be asked of THIS, never of the raw text — a quoted waiter's
    # own body contains the word `bash`, and matching that would call every
    # documentation write "live".
    vis = list(lines)
    for _, b0, b1 in spans:
        for k in range(b0, min(b1, len(vis))):
            vis[k] = " "
    inert = []
    for owner_i, b0, b1 in spans:
        if b1 <= b0:
            continue
        rest = [ln for k, ln in enumerate(vis) if k != owner_i]
        if not is_live(lines[owner_i], "\n".join(rest)):
            inert.append((b0, b1))
    kept = list(lines)
    for b0, b1 in inert:
        for k in range(b0, min(b1, len(kept))):
            kept[k] = " "
    res = "\n".join(kept)
    # splice by OFFSET, in reverse: a global replace of a short flag value
    # ("done", "do") deletes that token out of live control flow too.
    for a, b in sorted(flag_spans(res), reverse=True):
        res = res[:a] + " " + res[b:]
    return res


def main():
    with open(sys.argv[1], "r", errors="replace") as fh:
        sys.stdout.write(strip(fh.read()))


if __name__ == "__main__":
    main()
