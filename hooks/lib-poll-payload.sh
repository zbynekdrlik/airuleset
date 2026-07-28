# SOURCED, never executed — airuleset #124. (No shebang: this file is a library
# for the poll-loop detectors, which already run under `set -euo pipefail`;
# re-declaring shell options here would silently reach into every caller.)
#
# One classifier shared by every poll-loop detector (nudge-poll-loop-timeout.sh,
# block-ci-poll-repeat.sh, block-local-poll-repeat.sh): given a Bash command,
# return the part of it that is CONTROL FLOW, with provably-inert PAYLOAD blanked
# out. A private copy in any consumer is how the three would drift apart.
#
# WHY. Both shipped detectors match a normalised token stream of the WHOLE
# command (`tr -c 'A-Za-z0-9_' ' '`), which has no notion of what the shell
# executes and what it merely CARRIES. So a ticket body, playbook entry or
# completion report that QUOTES a poll loop — and this repo now writes a lot of
# those — reads as a poll loop. Measured live on the shipped code: a heredoc
# write quoting the sanctioned background waiter, with no `gh issue comment` in
# the call, is a hard `exit 2` from block-ci-poll-repeat.sh on its SECOND
# occurrence in a session (the waiter body carries `gh run view`, `cat >` is not
# mutating-exempt, the body carries `do … sleep … done`). Same failure class as
# #80 (`grep -c` read as a bulk read) and #111.
#
# WHY NOT JUST STRIP EVERY HEREDOC BODY — the fix #112 closed won't-fix, and it
# was right to. Re-measured over 8,107 transcripts / 251,551 Bash commands: of
# 147 foreground loop-matches that vanish when EVERY body is stripped, 75 are
# LIVE — 55 are `cat > x.sh <<EOF … EOF` followed by RUNNING that script in the
# same command, 20 are interpreter bodies (`python3 - <<PY`, `ssh host 'bash -s'
# <<EOF`). Blanket stripping deletes the majority. #112 also killed its own
# proposed discriminator ("strip unless the heredoc's owner is a shell or
# interpreter") on exactly that class: `cat` is not an interpreter, yet those 55
# bodies run.
#
# WHAT #112 SAID A CORRECT CLASSIFIER WOULD NEED, and dismissed as too hard:
# "follow the written PATH to a later execution". Within ONE command that is
# neither hard nor a heuristic, and it is this whole fix. A body is blanked only
# when ALL THREE hold:
#   1. its owner line names no interpreter (covers `python3 - <<PY`, `ssh host
#      'bash -s' <<EOF`, sudo/env/xargs/eval wrappers),
#   2. it is a plain redirect to a LITERAL file path (a `$VAR` target cannot be
#      resolved, so it counts as live), and
#   3. that file is not run, sourced, interpreted or `chmod +x`'d anywhere in the
#      same command's SHELL-VISIBLE text.
# Anything it cannot prove inert is left alone. Measured: 55 of 4,430 foreground
# loop-matches reclassified as payload, all 75 live classes kept.
#
# THE RESIDUAL, stated rather than hidden: 5 corpus commands write a script that
# a LATER call runs. Those stop nudging — and that is a false positive removed,
# not a detection lost: the nudge tells you to raise THIS call's `timeout`, but
# this call only writes a file in milliseconds, while the call that really runs
# long (`bash /tmp/overnight_orch.sh`) never carried the loop text and never
# nudged, before or after.
#
# QUOTED ARGUMENTS get the same treatment, but ONLY as the argument to an
# unambiguous text-payload flag. Stripping quoted spans wholesale would silence
# `bash -c 'while :; do …; sleep 60; done'` — the sanctioned waiter itself — so
# `-c` is deliberately absent from the flag list, and the message/title flags are
# honoured only inside a `git`/`gh` command line where they cannot mean anything
# executable.
#
# FAIL-OPEN: any error returns the command UNCHANGED, so a broken stripper can
# only ever cost a false nudge, never a missed real loop.

# The heredoc body must live INSIDE the `$( … )`, not after its closing paren:
# bash otherwise parses the substitution as ending on the opening line and warns
# "unterminated here-document" on stderr for every call. It still produced the
# right answer, which is exactly why it needed a test to catch it — #118's
# `assertEqual(out.stderr.strip(), "")` did.
poll_payload_strip() {
    local _out
    _out=$(python3 - "$1" 2>/dev/null <<'PYEOF'
import re
import sys

cmd = sys.argv[1]

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Anything that could EXECUTE the body. Unrecognised words mean "may be live",
# which means "do not strip" — the list only ever has to grow.
INTERP = re.compile(
    r"(?:^|[\s;&|(<'\"])"
    r"(?:bash|sh|zsh|ksh|dash|python3?|perl|ruby|node|deno|php|lua|awk|sed"
    r"|psql|mysql|sqlite3|ssh|docker|kubectl|sudo|env|xargs|eval|source)"
    r"(?:[\s;&|)'\"]|$)")

REDIR = re.compile(r"(?:^|[\s;&|)])(?:\d?>>?)\s*(['\"]?)([^\s'\">|;&]+)\1")

# A written file is inert only if EVERY later mention of it is the argument of
# an unambiguous text-FILE flag. Enumerating runners instead was too weak: the
# corpus replay caught `cat > /tmp/x.sh <<EOF … EOF; sshpass ssh host 'bash -s'
# < /tmp/x.sh`, which names no interpreter beside the path and never spells
# `bash <path>` — it feeds the file to a remote shell's STDIN. Three such
# specimens ran 119s, 135s and 183s. Reappearance-means-live needs no
# enumeration of every way a file can be executed, which is the property that
# made the runner list wrong.
TEXTSINK = re.compile(r"(?:--body-file|--notes-file|--file|-F)(?:=|\s+)\S*$")

# A heredoc read STRAIGHT by a text sink — `git commit -F -`, `gh issue comment
# -F -`, `gh pr create --body-file -`, which is gh-cli-recipes.md's own mandated
# recipe. None of these executes its stdin. Checked only AFTER the interpreter
# test, so `ssh host 'bash -s' <<EOF` and `psql -f - <<EOF` still win as live.
SINK_OWNER = re.compile(
    r"(?:^|[\s;&|(])(?:git\s+(?:commit|tag|notes)|gh\s+(?:issue|pr|release)\s+\w+)"
    r"\b[^\n;&|]*(?:-F|--body-file|--notes-file|--file)(?:=|\s+)-(?:\s|$|<)")

# Unambiguous text-payload flags: none of these ever carries executable shell.
BODYFLAG = re.compile(
    r"(?:^|\s)(?:--body|--body-file|--notes|--notes-file|-b|-F)"
    r"(?:=|\s+)(['\"])(.*?)\1", re.S)
# `-m`/`-t` mean other things to grep/ssh/docker, so these are honoured only
# inside a git/gh command line.
MSGFLAG = re.compile(
    r"(?:^|\s)(?:-m|--message|--title|-t)(?:=|\s+)(['\"])(.*?)\1", re.S)
GITGH = re.compile(r"(?:^|[\s;&|(])(?:git|gh)\s")


def heredoc_spans(text):
    """[(owner_line, body)] — a body ends at its own delimiter line."""
    out = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        owner = lines[i]
        consumed = i
        for m in HEREDOC.finditer(owner):
            delim = m.group(2)
            body = []
            j = consumed + 1
            while j < len(lines) and lines[j].strip() != delim:
                body.append(lines[j])
                j += 1
            out.append((owner, "\n".join(body)))
            consumed = j
        i = consumed + 1
    return out


def shell_visible(text):
    """The command with every heredoc body blanked — what the shell itself
    runs. Point 3 must be asked of THIS, never of the raw text: a quoted
    waiter's own body contains the word `bash`, and matching that would call
    every documentation write 'live'."""
    res = text
    for _, body in heredoc_spans(text):
        if body:
            res = res.replace(body, " ")
    return res


def consumed_only_as_text(base, remainder):
    """Every mention of `base` outside the write is a text-file argument."""
    if not base or "$" in base or "`" in base:
        return False                     # unresolvable target -> assume live
    for m in re.finditer(re.escape(base), remainder):
        if not TEXTSINK.search(remainder[max(0, m.start() - 60):m.start()]):
            return False
    return True


def is_live(owner, visible):
    """True unless this heredoc is PROVABLY inert payload."""
    if INTERP.search(owner):
        return True
    if SINK_OWNER.search(owner):
        return False                     # read straight by a text sink
    reds = REDIR.findall(owner)
    if not reds:
        return True                      # not a plain file write -> unknown
    # the write itself names the target; ask only about the REST
    remainder = visible.replace(owner, " ", 1)
    return not consumed_only_as_text(reds[-1][1].split("/")[-1], remainder)


def strip(text):
    res = text
    visible = shell_visible(text)
    for owner, body in heredoc_spans(text):
        if not body:
            continue
        if is_live(owner, visible):
            continue
        res = res.replace(body, " ")
    flags = [BODYFLAG] + ([MSGFLAG] if GITGH.search(res) else [])
    for rx in flags:
        for m in rx.finditer(res):
            if m.group(2).strip():
                res = res.replace(m.group(2), " ")
    return res


sys.stdout.write(strip(cmd))
PYEOF
) || _out=""
    if [ -n "$_out" ]; then printf '%s' "$_out"; else printf '%s' "$1"; fi
}

# Cheap pre-gate: only pay for the stripper when the command actually carries
# something payload-shaped. Every Bash tool call goes through the detectors, so
# an unconditional python3 fork would be a real cost for nothing.
poll_payload_carrier() {
    case "$1" in
        *'<<'*|*--body*|*--notes*|*--message*|*--title*) return 0 ;;
        *' -b '*|*' -F '*|*' -m '*|*' -t '*) return 0 ;;
        *) return 1 ;;
    esac
}
