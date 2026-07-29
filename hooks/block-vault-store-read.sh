#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash, Read, Grep, Glob matchers) — issue #153 finding 1.
#
# The credential store (`~/.claude/secrets/<NAME>.secret`, the `airuleset.py
# secret` channel from #144) is not read by hand. `secret exec` hands the value
# to a child process with fd 1/2 captured and filtered; every other way of
# getting at the file puts the value into the session transcript
# (`~/.claude/projects/**/*.jsonl`), where it survives compaction and cannot be
# revoked — the one outcome the whole channel exists to prevent.
#
# Before this hook that guarantee was VOLUNTARY: the store is 0600 owned by the
# very uid the agent's Bash runs as, and nothing gated it, so it held only for
# as long as the agent chose `secret exec` over `cat`. A guarantee that defers
# to an unenforced action is a silence generator (the run-card lesson, #134).
# This is the artifact it rests on instead.
#
# WHAT IS MATCHED — the RAW command text, not parsed argv. Every interesting
# evasion hides the path inside a quoted string where token parsing cannot see
# it: `python3 -c 'open("…/DB.secret").read()'`, `< …/DB.secret`,
# `$(<…/DB.secret)`. Two patterns:
#   A. a store-dir reference — `.claude/secrets` however it is spelled
#      (`~/`, `$HOME/`, an absolute path, or bare);
#   B. any `<stem>.secret` filename — this channel's own extension, so a
#      relative read after a `cd` into the store is still caught.
#   D. either of those AFTER THE SHELL'S OWN RESOLUTION (#156). A and B match
#      literal characters, but the shell expands `~/.claude/secr*/DB_PASS.sec*`
#      to the store before either of them sees a real path — so the guard was
#      deny-by-default on the HEAD and a blocklist on the SPELLING, and only
#      the first half was ever disclosed. D decides per path COMPONENT what the
#      shell can resolve it INTO, across every layer that sits between the text
#      and the open():
#        - GLOBBING: `.cl*`, `.claud?`, `.claud[e]` can all be `.claude`, and
#          `sec*`/`secret[s]` can be `secrets`;
#        - BRACE EXPANSION: `{secrets,x}`, `{s,y}ecrets`, `{.claude,x}`;
#        - PATH NOISE: `.claude/./secrets` and `.claude/x/../secrets` spell
#          both names LITERALLY and defeated even A's adjacency;
#        - a `cd` earlier in the SAME command, against which a later relative
#          token is resolved.
#      Each of these was measured reading a credential, not theorised.
# The command is then split into segments (quote-aware, and command
# substitutions `$(...)` / backticks become their OWN segments so a read
# nested inside an allowlisted head — `ls "$(cat …/DB.secret)"` — is still
# seen). Any segment referencing the store is DENIED unless its head command is
# provably metadata-only.
#
# DENY-BY-DEFAULT, deliberately. The alternative — blocklisting reader commands
# (cat/less/head/xxd/base64/…) — is an enumeration of the vocabulary, and one
# unlisted reader (`bat`, `nl`, `tac`, a future pager) walks straight through
# with no signal. Here an unanticipated reader fails CLOSED.
#
# WRITES are blocked too: hand-writing a value into the store means typing the
# credential into a shell command, which is the same leak from the other side.
# Use `secret request` — the user posts it from their own browser.
#
# NOT ONLY BASH. An agent asked what is in the store reaches for the `Read`
# TOOL long before it reaches for `cat`, so Read/Grep/Glob are matched too (by
# EXACT tool name, one settings entry each — an alternation matcher has been
# observed in this repo to silently never match, and a guard that never runs is
# worse than none because it reads as coverage). For those the inspected fields
# are file_path / notebook_path / path / glob, plus `pattern` for Glob ONLY:
# Grep's pattern is a regex to search FOR, and treating it as a path would
# block searching this repo for the guard's own subject matter.
#
# ALLOWED heads (provably content-free AND non-mutating): ls, stat, test, [.
# Plus the whole sanctioned CLI surface (`airuleset.py secret
# request|status|list|exec|forget|purge`), which never names a store path on
# the command line at all. An allowlisted head loses its exemption when the
# segment is PIPED — `ls <store>/* | xargs cat` makes `ls` a name source, not a
# listing. Heads deliberately NOT allowlisted, each because it defeated an
# earlier version: `file -f` and `du --files0-from` ingest a file as a NAME
# LIST and echo its contents back in their own error text; `cd`/`pushd` let the
# store be entered and then read by a bare glob; chmod/chown/rm/shred/touch are
# mutations (the first two hand a 0600 credential to another uid on a box that
# hosts foreign uids by design, and `secret forget` is the honest deletion
# path). `ls -l` and `stat` answer every legitimate metadata question.
#
# BYPASS — env only, and always logged:
#   AIRULESET_ALLOW_VAULT_READ=1   -> audits/vault-store-reads.log
# There is DELIBERATELY no inline `# airuleset:*-ok <reason>` marker, unlike
# every other hook in this repo. A marker the agent can append to its own
# command is exactly the voluntary compliance this hook exists to remove; the
# env bypass is for the USER. No legitimate need is lost: `secret exec` covers
# use, `secret list`/`status` metadata, `secret forget` deletion.
#
# KNOWN GAPS — stated because the point of this ticket is not to imply a
# stronger guarantee than is delivered:
#   - This is a GUARDRAIL, not a security boundary. The agent's uid holds
#     `NOPASSWD: ALL` sudo on these boxes (verified on dev1), so no store
#     location is unreadable to it and no `sudo`-based separation is
#     achievable. What this delivers is: the unsafe path is refused by
#     default, so the leak cannot happen by reflex, and any deliberate
#     circumvention leaves a greppable artifact (an audit line, or the
#     removal itself in git).
#   - A hook is configuration. Anything that edits settings.json, unregisters
#     this hook, or reaches the file through a tool with no matcher here is
#     outside its reach by construction.
#   - A GLOB THAT DOES NOT BEGIN WITH THE NAME IT STANDS FOR is not matched by
#     rule D. D anchors on a component's literal PREFIX, so `secr*` is caught
#     and `*ecrets`, `[s]ecrets`, `??????s` and a bare `*` are NOT: `cat
#     ~/.claude/*ecrets/*` and `cat ~/.claude/*/*` still read the store. This
#     is a deliberate trade, measured rather than assumed. Anchoring on any
#     literal run instead of the prefix matches grep REGEXES and `find -name`
#     PATTERNS (`secret.*=`, `*.claude*`, `^[[:space:]]*//.*[Cc]laude`), and
#     exempting wildcards under `~` refuses `du -sh ~/.claude/*`, which occurs
#     repeatedly in this fleet's real command history and reports sizes, never
#     content. Both were replayed over 212,557 real commands; the shipped rule
#     newly matches ZERO of them. What is bought is the spelling a person or a
#     tab-completion actually produces; what is left open is a spelling nobody
#     types by accident — which is the exact boundary of this hook's claim,
#     that the leak cannot happen by REFLEX.
#   - D enumerates the shell layers it knows about (globbing, braces, path
#     noise, an in-command `cd`). Any OTHER resolution step is by construction
#     outside it — command/parameter/tilde-user substitution that produces a
#     path component, and any expansion whose result depends on state this
#     process cannot see. That is the same class as the computed-path gap
#     below, and it is enumeration, so it is a floor and never a proof.
#   - A path computed at runtime rather than written literally
#     (`python3 -c "import pathlib; open(pathlib.Path.home()/'.claude'/'secrets'/n)"`,
#     a variable assembled from parts, a path read out of another file) does
#     not match either pattern — text matching cannot see it.
#   - AUTHORING THEN RUNNING is out of scope, decided rather than overlooked
#     (#156). `Write` a script that reads the store, then `bash` it: both halves
#     are allowed, and no `Write` matcher is registered. Adding one was
#     considered and REJECTED for two reasons. It would block editing this hook
#     and its own tests, which necessarily contain the store path, so the guard
#     would prevent its own maintenance — and it would destroy the documented
#     remedy for the accepted false positive below, which is precisely "write
#     the body to a file with the Write tool". Against that it buys little: a
#     script whose path is assembled at runtime defeats a content match anyway,
#     the same limit already stated for computed paths. The honest scope is
#     that this hook gates the ACT of reading, not the authoring of something
#     that will later read.
#   - Not a shell parser: `xargs` fed from a file LIST, and a wrapper script
#     that does the read internally, are invisible. The measured shape of this
#     is `find <parent> -type f | xargs cat`, which reads every credential and
#     is NOT blocked: the consumer sits in a separate segment, and deciding it
#     would mean enumerating reader commands — the enumeration this guard
#     rejects on principle, since one unlisted reader then walks through with
#     no signal. The `-exec` form of the same walk IS blocked, because the
#     action is part of the segment. Closing the piped form by treating every
#     `find` over the parent as a sweep was measured at 104 further real
#     commands that only ever listed names, against 5 for the action form.
#     (Process substitution is
#     NOT in this list — `(` is a separator outside quotes, so `cat <(cat
#     …/DB.secret)` really is blocked. An earlier version of this header
#     claimed otherwise and was wrong.)
#   - The Bash tool's working directory PERSISTS between calls, and this hook
#     is stateless. `cd` into the store is refused for exactly that reason —
#     otherwise an allowed `cd` in one call makes `cat *` in the next
#     unreachable to any single-command check. Refusing the STORE does not
#     close this, and the earlier wording implied it did: `cd ~/.claude` is
#     ALLOWED (a legitimate place to work), and `cat secrets/*` in the NEXT
#     call is then a bare relative read this process cannot resolve. Within
#     ONE command rule D does resolve it; across two calls nothing here can,
#     short of refusing `cd` into the parent, which is ordinary work.
#   - FALSE POSITIVE, accepted deliberately: a command whose TEXT merely NAMES
#     the store is blocked even when it reads nothing — `grep -rn
#     '\.claude/secrets' hooks/`, or a commit/issue body naming the path. The
#     env bypass cannot help mid-session (an inline `VAR=1 cmd` sets the
#     variable for the COMMAND, not for this hook process), so the workaround
#     is to describe the path in prose, or write the body to a file with the
#     Write tool and `cat` it — the same self-tripping shape, and the same
#     remedy, as block-sensitive-staging.sh. Stripping heredoc bodies to fix
#     it was considered and REJECTED: a heredoc body is not inert here
#     (`python3 <<EOF` genuinely executes), so that exemption would re-open a
#     real read path to buy comfort.
#   - Fail-closed is bounded by the harness: a hook that TIMES OUT (5s) is
#     treated as an error and does not block, so a pathologically slow python3
#     start fails open. Not agent-controllable, but real. Measured on dev1 the
#     whole hook runs in a median 33ms (min 29, max 41) against that 5000ms
#     budget, and the gap is not closeable from inside a hook — it is tracked
#     as its own ticket (#162) rather than as a bullet in a decision ticket.
#   - Fail-closed covers a payload that is PRESENT and unparseable, never an
#     EMPTY one: no payload at all still exits 0. See the comment at the read
#     loop for why that specific row was left open rather than closed.
#   - The bypass audit line is a FINGERPRINT, not the command (#157): tool,
#     the matched store REFERENCES, a SHA-256 of the command and its length.
#     It used to record the full text, which meant a bypassed WRITE — whose
#     value is in its own text — put that value into a plaintext file, making
#     the guard a second place the credential came to rest. What remains, and
#     is stated rather than implied: the log is gitignored (`audits/*.log`),
#     created 0600, and the digest is over the whole command, so against
#     someone who can already READ that 0600 file it is only as strong as the
#     value's own entropy. That is the same someone who can read the store
#     itself, so the digest widens nothing — but the file is not a secret
#     store and must not be treated as one. The recorded REFERENCES are
#     restricted to matches sitting in a path context, because "a path
#     fragment by construction" was too strong: a value shaped like a store
#     filename matches the same pattern, and did reach the log before that
#     restriction.
#
# Exit code 2 = block the tool call.

# Read the payload with a SHELL BUILTIN, not `cat`: the fail-closed branch
# below has to work even when PATH is broken, and reading stdin through an
# external binary would make a missing PATH look like "no payload" (allow)
# instead of "cannot check" (block).
PAYLOAD=""
line=""
while IFS= read -r line || [ -n "$line" ]; do
    PAYLOAD+="$line"$'\n'
    line=""
done
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"

# EMPTY stdin exits 0, deliberately, and this is the one row of #156 hole 2
# left open rather than closed. "I was handed nothing" is a different state
# from "I was handed something I cannot understand" (which now fails closed
# below): the payload envelope is built by the harness, not by the agent, so
# an empty one is not agent-reachable — while failing closed here would turn
# any harness change that stops supplying a payload into a fleet-wide denial
# of every Bash, Read, Grep and Glob call on every managed box.
[ -z "${PAYLOAD//[$'\n\t ']/}" ] && exit 0

fail_closed() {
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): block-vault-store-read.sh could not run its check." >&2
    echo "  $1" >&2
    echo "" >&2
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation — but a" >&2
    echo "  guard that cannot run must not silently open the credential store." >&2
    echo "  Investigate and fix the hook (or install python3) before retrying." >&2
    echo "" >&2
    exit 2
}

command -v python3 >/dev/null 2>&1 || fail_closed "python3 is not available."

# The payload travels in ARGV, never on stdin: the heredoc below IS this
# process's stdin (it carries the script), so a piped payload would arrive
# empty and every check would silently pass.
VIOLATION=$(python3 - "$PAYLOAD" <<'PYEOF'
import fnmatch
import hashlib
import json
import re
import shlex
import sys

raw = sys.argv[1]

# EXIT 3 = "I was handed something and could not understand it", which the
# bash wrapper turns into fail_closed. The predecessor caught the parse error
# and assigned `payload = {}` — a dict — and then tried to detect the failure
# by asking whether `payload` was a dict, a test that is statically always
# False on that very path (#156 hole 2). Nothing may re-derive "did the parse
# fail?" from the type of a variable the failure handler itself assigned; the
# failure is reported where it happens.
try:
    payload = json.loads(raw)
except Exception as exc:
    print("  the payload is not JSON (%s)" % exc.__class__.__name__)
    sys.exit(3)
if not isinstance(payload, dict):
    print("  the payload parsed to %s, not an object" % type(payload).__name__)
    sys.exit(3)

def strings_in(obj, _depth=0):
    """Every string anywhere in a payload value, bounded."""
    if _depth > 6:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings_in(v, _depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings_in(v, _depth + 1)


tool = payload.get("tool_name") or ""
tin = payload.get("tool_input") or {}
cmd = ""
if isinstance(tin, dict):
    cmd = tin.get("command") or ""
elif isinstance(tin, str):
    # Understandable input in an UNEXPECTED SHAPE — not the same thing as
    # unparseable input, so it is inspected rather than failed closed. Failing
    # closed on an unknown-but-valid tool would deny every call to it; letting
    # it through unscanned is what the ticket measured as a hole.
    cmd, tin = tin, {}
else:
    # A LIST (or anything else) is the same shape as the string above and gets
    # the same treatment. Treating it as `{}` exited 0 having inspected
    # nothing — an inconsistency with no argument behind it.
    cmd, tin = " ".join(strings_in(tin)), {}

# A. the store directory, however it is spelled.
STORE_DIR_RE = re.compile(r"\.claude/+secrets(?![A-Za-z0-9_-])")
# B. a value file by name. The stem is an alnum/underscore OR a glob
# metacharacter: `find ~/.claude -name '*.secret'` names no directory and no
# literal stem, and was the review's F3 bypass. A regex or source fragment
# (`"\.secret\b"`, `(".secret",`) still does not match, which is the point of
# requiring SOME stem character rather than none.
VALUE_FILE_RE = re.compile(
    r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|[*?\]}])\.secret(?![A-Za-z0-9_-])")
# C. the store's PARENT swept recursively or archived (review F2). Anchored on
# `.claude` NOT followed by a deeper path component, so `~/.claude/projects`
# — the transcript greps this repo's own work runs constantly — is untouched.
CLAUDE_ROOT_RE = re.compile(r"\.claude/?(?![A-Za-z0-9_./-])")
RECURSIVE_RE = re.compile(r"(?:^|\s)(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?=\s|$)")
BULK_HEADS = {"tar", "zip", "rsync", "cpio", "pax", "7z", "scp"}
# Heads that walk a tree BY CONSTRUCTION, so there is no `-r` flag to detect —
# but ONLY when they also carry an action that reads or mutates what they find.
# `find <parent> -exec cat {} +` prints every credential; `find <parent> -name
# x` prints NAMES, exactly like the `ls -R` the allowlist already permits.
# Measured over the real corpus before choosing: requiring the action costs 5
# commands (3 of them this session's own probes), while treating any `find`
# over the parent as a sweep costs 104 more that only ever listed names.
# `du` is deliberately absent for the same reason it was refused earlier —
# `du -sh ~/.claude/*` reports sizes, never content.
TREE_WALK_HEADS = {"find", "fd"}
TREE_WALK_ACTION_RE = re.compile(r"(?:^|\s)-(?:exec|execdir|ok|okdir|delete)\b")

# D. the same three references SPELLED WITH A GLOB (#156 hole 1). A/B/C above
# match literal characters, but the shell expands `~/.claude/secr*/*` to the
# store before any of them ever sees the real path — so `cat $HOME/.cl*/sec*/*`
# read a credential and was ALLOWED. These decide on what a component CAN
# EXPAND INTO rather than on how it is typed.
CLAUDE_DIR = ".claude"
STORE_DIR = "secrets"
VALUE_EXT = "secret"
GLOB_META = set("*?[")
# A token is a maximal run of non-separator, non-quote characters. Quotes are
# separators so a path inside `open("…")` is still tokenized as a path.
TOKEN_RE = re.compile(r"[^\s'\"|;&()<>]+")
LITERAL_HEAD_RE = re.compile(r"^[^*?\[\]]*")
# How many literal characters a glob component must anchor on. 3 is what
# separates `.cl*`/`sec*` (a real truncation of the store's own path) from a
# grep REGEX that merely happens to look like one — see ANCHOR below.
GLOB_ANCHOR = 3


def can_be(component, target):
    """Could this path component, after SHELL GLOBBING, be exactly `target`?

    The anchor is the component's literal PREFIX — the characters before its
    first metacharacter — and it must itself be a prefix of `target`. Two
    weaker rules were measured against 212,557 real commands and rejected:
    no anchor at all reads every `dir/*/*` in every tree as the store, and an
    anchor allowed to float anywhere in the component (`*claude*`,
    `.*[Cc]laude`, `secret.*=`) matches grep REGEXES and `find -name` PATTERNS,
    which are not paths — the same mention-vs-use distinction this hook already
    makes deliberately for Grep's own `pattern` field.
    """
    component = component.strip()
    if component == target:
        return True
    if not (set(component) & GLOB_META):
        return False       # a literal that is not the target cannot become it
    if not fnmatch.fnmatchcase(target, component):
        return False
    lit = LITERAL_HEAD_RE.match(component).group(0)
    return len(lit) >= GLOB_ANCHOR and target.startswith(lit)


def ext_can_be_value(component):
    """A final component whose EXTENSION is a glob truncation of `.secret`."""
    if "." not in component:
        return False
    stem, ext = component.rsplit(".", 1)
    if not stem:
        # A bare `.secret` with no stem is a source fragment (`(".secret",`),
        # not a filename — pattern B declines it for the same reason.
        return False
    return ext != VALUE_EXT and can_be(ext, VALUE_EXT)


def cd_target(segment, head):
    """Where an allowed `cd` in an EARLIER segment leaves the later ones."""
    if head not in ("cd", "pushd"):
        return None
    try:
        tk = shlex.split(segment)
    except ValueError:
        tk = segment.split()
    for t in tk[1:]:
        if not t.startswith("-"):
            return t.rstrip("/")
    return None


BRACE_RE = re.compile(r"\{([^{}]*,[^{}]*)\}")
BRACE_CAP = 64


def expand_braces(token, _depth=0):
    """Brace expansion — a SECOND expansion layer with globbing's shape.

    `~/.claude/{secrets,x}/*` and `{s,y}ecrets` resolve to the store before any
    text pattern sees a real path, exactly as a glob does. Bounded: a token
    whose expansion exceeds BRACE_CAP keeps the alternatives found so far, and
    the literal token is always among the candidates, so a pathological brace
    can waste nothing but its own alternatives.
    """
    out = [token]
    m = BRACE_RE.search(token)
    if m and _depth < 6:
        out = []
        for alt in m.group(1).split(","):
            grown = token[:m.start()] + alt + token[m.end():]
            out.extend(expand_braces(grown, _depth + 1))
            if len(out) >= BRACE_CAP:
                break
    return out[:BRACE_CAP]


def normalize(comps):
    """Drop `.` components and resolve `..` — path noise is not a barrier.

    `~/.claude/./secrets/*` and `~/.claude/x/../secrets/*` spell BOTH names
    literally and still reached the store, because the adjacency test wants the
    two components next to each other. The shell does not care what sits
    between them, so neither can this.
    """
    out = []
    for c in comps:
        if c == ".":
            continue
        if c == ".." and out and out[-1] not in ("..", "~"):
            out.pop()
            continue
        out.append(c)
    return out


def path_candidates(segment, cwd_hint=None):
    """Every path a token could resolve to, across both expansion layers."""
    for tok in TOKEN_RE.findall(segment):
        roots = [tok]
        if cwd_hint and not tok.startswith(("/", "~", "$", "-")):
            roots.append(cwd_hint + "/" + tok)
        for root in roots:
            for t in expand_braces(root):
                yield t, normalize([c for c in t.split("/") if c])


def globbed_store_ref(segment, cwd_hint=None):
    """A store reference whose literals are elided by an expansion layer."""
    for t, comps in path_candidates(segment, cwd_hint):
        for a, b in zip(comps, comps[1:]):
            if can_be(a, CLAUDE_DIR) and can_be(b, STORE_DIR):
                return t
        if comps and ext_can_be_value(comps[-1]):
            return t
    return None


# `find`'s expression takes PATTERNS, not paths — `-name "*.claude*"` searches
# for claude-related files somewhere else entirely and reaches no store. The
# mention-vs-use distinction again, and it only became load-bearing once `find`
# started triggering the parent-sweep rule.
PATTERN_FLAGS = {"-name", "-iname", "-path", "-ipath", "-wholename",
                 "-iwholename", "-regex", "-iregex", "-lname", "-ilname"}


def without_pattern_args(segment):
    """`segment` with every `<pattern-flag> <value>` pair removed."""
    try:
        tk = shlex.split(segment)
    except ValueError:
        tk = segment.split()
    out, skip = [], False
    for t in tk:
        if skip:
            skip = False
            continue
        if t in PATTERN_FLAGS:
            skip = True
            continue
        out.append(t)
    return " ".join(out)


def globbed_parent_ref(segment):
    """Rule C's own glob/brace spelling — `tar czf x.tgz ~/.cl*`."""
    for t, comps in path_candidates(segment):
        if comps and can_be(comps[-1], CLAUDE_DIR):
            return t
    return None

# Heads that are PROVABLY content-free AND non-mutating. Everything the
# adversarial review broke is gone: `file -f` and `du --files0-from` read a
# file as a NAME LIST and echo it back in their error text; `cd` let the store
# be entered and then read by a bare glob (and the Bash tool's cwd persists
# ACROSS calls, so an allowed `cd` makes the NEXT call's `cat *` invisible to a
# stateless hook); chmod/chown/rm/shred/touch are mutations, and the first two
# hand a 0600 credential to another uid on a box that hosts foreign uids by
# design. `ls -l` and `stat` already answer every legitimate metadata question.
ALLOW_HEADS = {"ls", "stat", "test", "["}
PREFIXES = {"sudo", "env", "time", "nice", "ionice", "command", "builtin", "exec"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_segments(text):
    """Quote-aware split on shell separators -> [(segment, terminator), ...].

    Command substitutions become their OWN segments — `$(` and backticks are
    separators even inside double quotes, where the shell really does expand
    them — so a read nested inside an allowlisted head is not laundered by it.
    Inside SINGLE quotes nothing is a separator, which keeps a `python3 -c
    '...'` body intact as one segment headed by python3.

    The TERMINATOR is returned because it changes what an allowlisted head
    means: piped, `ls` is not a listing, it is a name source for whatever
    consumes it (review F5).
    """
    segs, buf = [], []
    i, n = 0, len(text)
    in_sq = in_dq = False
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if in_sq:
            if c == "'":
                in_sq = False
            buf.append(c)
            i += 1
            continue
        if two == "$(":
            segs.append(("".join(buf), "$("))
            buf = []
            i += 2
            continue
        if c == "`":
            segs.append(("".join(buf), "`"))
            buf = []
            i += 1
            continue
        if in_dq:
            if c == "\\" and i + 1 < n:
                buf.append(c)
                buf.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_dq = False
            buf.append(c)
            i += 1
            continue
        if c == "'":
            in_sq = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_dq = True
            buf.append(c)
            i += 1
            continue
        if two in ("&&", "||"):
            segs.append(("".join(buf), two))
            buf = []
            i += 2
            continue
        if c in ";|&\n()":
            segs.append(("".join(buf), c))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append(("".join(buf), ""))
    return segs


def head_of(segment):
    try:
        tk = shlex.split(segment)
    except ValueError:
        tk = segment.split()
    i = 0
    while i < len(tk) and (tk[i] in PREFIXES or ASSIGN_RE.match(tk[i])):
        i += 1
    tk = tk[i:]
    if not tk:
        return None
    return tk[0].rsplit("/", 1)[-1].lower()


def store_refs(segment, cwd_hint=None):
    """EVERY store reference in the segment, for the audit trail.

    Both patterns are collected, not just the first to match: the dir tells you
    the store was touched, the `<stem>.secret` tells you WHICH item, and an
    audit line that dropped the item name would not answer the one question it
    exists for (#157).
    """
    found = [m.group(0) for m in (STORE_DIR_RE.search(segment),
                                  VALUE_FILE_RE.search(segment)) if m]
    if found:
        return found
    # Rule D is ADDITIVE, never a replacement: the regexes above still catch
    # shapes the tokenizer cannot see, and D catches the spellings they cannot.
    globbed = globbed_store_ref(segment, cwd_hint)
    return [globbed] if globbed else []


def references_store(segment, cwd_hint=None):
    found = store_refs(segment, cwd_hint)
    return found[0] if found else None


def audit_refs(segment, cwd_hint=None):
    """The subset of `store_refs` safe to WRITE DOWN.

    A reference is not "a path fragment by construction" — it is whatever the
    pattern matched, and a VALUE that happens to look like a store filename
    matches too: `echo 'topsecret.secret' > <store>/X.secret` recorded the
    value. Only a match sitting in a PATH CONTEXT is logged — inside a token
    carrying a separator, or one resolved against a `cd` — which keeps the
    item name that makes the trail useful and drops the bare argument.
    """
    out = []
    m = STORE_DIR_RE.search(segment)
    if m:
        out.append(m.group(0))          # a fixed path fragment, never a value
    # EVERY value-file match, not just the first: a value shaped like one can
    # precede the real path in the same segment, and taking the first match
    # would then both log the value AND lose the item name.
    toks = TOKEN_RE.findall(segment)
    for m in VALUE_FILE_RE.finditer(segment):
        ref = m.group(0)
        if any(ref in tok and "/" in tok for tok in toks):
            out.append(ref)
    if not out:
        globbed = globbed_store_ref(segment, cwd_hint)
        if globbed:
            out.append(globbed)
    return out


def sweeps_the_parent(segment, head):
    """The store's PARENT read wholesale, without ever naming the store.

    `grep -r password ~/.claude` and `tar czf /tmp/c.tgz ~/.claude` print or
    package every credential inline and match neither path pattern (review
    F2). Anchored on `.claude` with NO deeper component, so `~/.claude/projects`
    — the transcript sweeps this repo's own work depends on — is untouched.
    """
    if head in ALLOW_HEADS:
        return None          # `ls -R ~/.claude` lists names, never content
    operands = without_pattern_args(segment)
    if not (CLAUDE_ROOT_RE.search(operands) or globbed_parent_ref(operands)):
        return None
    if (head in BULK_HEADS or RECURSIVE_RE.search(segment)
            or (head in TREE_WALK_HEADS
                and TREE_WALK_ACTION_RE.search(segment))):
        return "recursive read/archive of the store's parent dir"
    return None


def excerpt(segment):
    """One line, always — the excerpt is untrusted text on a shared channel.

    A newline is a segment separator OUTSIDE quotes, but inside SINGLE quotes
    it is buffered into the SAME segment, so a quoted excerpt could span lines
    and a crafted second line could begin with the audit marker below. That
    forged an entry in the very artifact this hook's honest-limit claim rests
    on ("circumventing it leaves an artifact"). Collapsing whitespace closes
    the channel at the source and fixes the refusal message's layout too.
    """
    return re.sub(r"\s+", " ", segment.strip())[:120]


def audit(tool_name, refs, subject):
    """Emit the bypass AUDIT line — a fingerprint, never the raw text (#157).

    The bypass log is a durable file OUTSIDE the transcript, so whatever goes
    into it is a second place a credential can come to rest; and a command that
    carries its value in its own text is the ordinary case for an allowed
    WRITE. What survives is what the trail is actually for: which tool was
    bypassed, WHICH STORE ITEM it named, and a digest that lets two entries be
    compared. The refs are safe by construction — they are what the path
    predicate matched, which is always a path fragment, never the argument
    carrying a value.
    """
    digest = hashlib.sha256(subject.encode("utf-8", "replace")).hexdigest()
    safe = ",".join(sorted({re.sub(r"[^\w./~*?\[\]-]", "", r)[:60]
                            for r in refs if r})) or "-"
    print("#AUDIT# tool=%s refs=%s sha256=%s"
          % (tool_name or "Bash", safe, digest))


# --- a file-reading TOOL rather than a shell command (review F1) ------------
# Bash was never the most reflexive route to the store: an agent asked what is
# in it reaches for `Read` long before `cat`, and a prompt-injected one has a
# route no Bash-matched hook can see.
if not cmd:
    fields = []
    for key in ("file_path", "notebook_path", "path", "glob"):
        val = " ".join(strings_in(tin.get(key)))
        if val:
            fields.append((key, val))
    # For Glob the `pattern` IS a path pattern. For Grep it is a regex to
    # search FOR — treating that as a path would block searching this repo for
    # the guard's own subject matter, which is a false positive with no
    # security value.
    if tool == "Glob":
        val = " ".join(strings_in(tin.get("pattern")))
        if val:
            fields.append(("pattern", val))
    bad = [(k, store_refs(v), v) for k, v in fields if store_refs(v)]
    if bad:
        print("\n".join("  %s %s -> %s" % (tool or "tool", k, excerpt(v))
                        for k, _refs, v in bad))
        # `fields` holds (key, value) PAIRS while `bad` holds triples — the
        # two are not interchangeable, and unpacking one as the other threw
        # inside this branch. It still exited 2 because fail_closed does too,
        # so the store stayed shut and every block test passed while the real
        # refusal, the audit line and the user's env bypass were all gone.
        audit(tool, [r for k, _rs, v in bad
                     for r in audit_refs(v)],
              " ".join(v for _k, v in fields))
        sys.exit(2)
    sys.exit(0)

hits = []
refs = []
# WITHIN one command the hook can see a `cd` and what follows it, so a later
# relative token is resolved against the cd target — `cd ~/.claude && cat
# sec*/*` names the store in neither half on its own. ACROSS calls it still
# cannot (the Bash tool's cwd persists and this hook is stateless), which is
# why `cd` INTO the store is refused outright and stays a stated gap.
cwd_hint = None
for seg, term in split_segments(cmd):
    head = head_of(seg)
    sweep = sweeps_the_parent(seg, head)
    if sweep:
        hits.append("%s  ->  %s (%s)" % (head or "?", excerpt(seg), sweep))
        refs.append(globbed_parent_ref(seg) or ".claude")
        continue
    seg_refs = store_refs(seg, cwd_hint)
    if not seg_refs:
        cwd_hint = cd_target(seg, head) or cwd_hint
        continue
    if head in ALLOW_HEADS and term != "|":
        # Piped, an allowlisted head is just a name source for whatever
        # consumes it — `ls <store>/* | xargs cat` (review F5).
        continue
    hits.append("%s  ->  %s" % (head or "(redirection/substitution)",
                                excerpt(seg)))
    refs.extend(audit_refs(seg, cwd_hint))

if hits:
    print("\n".join("  " + h for h in dict.fromkeys(hits)))
    audit(tool, refs, cmd)
    sys.exit(2)
sys.exit(0)
PYEOF
) && RC=0 || RC=$?

if [ "$RC" -eq 0 ]; then
    exit 0
fi

if [ "$RC" -ne 2 ]; then
    fail_closed "python3 exited $RC instead of running the check. $VIOLATION"
fi

# --- a real hit ------------------------------------------------------------
# The matcher emits the audit FINGERPRINT as a marked line (#157). It is split
# off here rather than re-derived: re-parsing the payload a second time is what
# used to put the whole command — and therefore any value it carries — into the
# log. The rest of the matcher's output is the message shown to the caller.
# `tail -1` is belt and braces: the matcher sanitizes every excerpt it prints,
# so nothing else can carry the marker, and `audit()` always prints LAST — so
# even if a future edit reopened that channel the real fingerprint still wins.
AUDIT_FIELDS=$(printf '%s\n' "$VIOLATION" | grep '^#AUDIT# ' | tail -1 || true)
AUDIT_FIELDS=${AUDIT_FIELDS#\#AUDIT\# }
VIOLATION=$(printf '%s\n' "$VIOLATION" | grep -v '^#AUDIT# ' || true)

if [ "${AIRULESET_ALLOW_VAULT_READ:-}" = "1" ]; then
    AUDIT_LOG="${AIRULESET_VAULT_READ_AUDIT:-$HOME/devel/airuleset/audits/vault-store-reads.log}"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    # A file recording that a credential was touched must not inherit the
    # ambient umask — these boxes host foreign uids by design.
    if [ ! -e "$AUDIT_LOG" ]; then
        (umask 077; : >> "$AUDIT_LOG") 2>/dev/null || true
    fi
    chmod 600 "$AUDIT_LOG" 2>/dev/null || true
    {
        echo "$(date -Iseconds 2>/dev/null || echo unknown)  env-bypass  ${AUDIT_FIELDS}"
    } >> "$AUDIT_LOG" 2>/dev/null || true
    exit 0
fi

echo "" >&2
echo "🚫 BLOCKED: the credential store is not read (or written) by hand." >&2
echo "" >&2
echo "$VIOLATION" >&2
echo "" >&2
echo "  A value read this way lands in the session transcript, survives" >&2
echo "  compaction, and cannot be revoked — the exact leak the credential" >&2
echo "  channel exists to prevent." >&2
echo "" >&2
echo "  Use the value WITHOUT seeing it:" >&2
echo "    python3 ~/devel/airuleset/airuleset.py secret exec <NAME> -- <cmd>" >&2
echo "  It hands the value to the child through the environment (or --stdin)," >&2
echo "  captures fd 1/2 and filters the value out of them." >&2
echo "" >&2
echo "  Metadata, without the value:  secret list  /  secret status <NAME>" >&2
echo "  Remove it:                    secret forget <NAME>" >&2
echo "  Get a NEW value from the user: secret request <NAME>  (never ask in chat)" >&2
echo "" >&2
echo "  HONEST LIMIT: this is a GUARDRAIL, not a security boundary. The agent's" >&2
echo "  uid holds NOPASSWD sudo on these boxes, so no store location is beyond" >&2
echo "  its reach; what this guarantees is that the unsafe path is refused by" >&2
echo "  default and that circumventing it leaves an artifact." >&2
echo "" >&2
echo "  Bypass (user-instructed only, logged): AIRULESET_ALLOW_VAULT_READ=1." >&2
echo "  There is deliberately no inline marker — see the hook's header." >&2
echo "" >&2
exit 2
