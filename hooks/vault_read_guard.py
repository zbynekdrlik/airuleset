"""The matcher behind hooks/block-vault-store-read.sh (#153/#156, #1153).

Moved VERBATIM out of the hook's `python3 - <<'PYEOF'` heredoc (#1153) so the
second protected root (`~/.secrets/`) could be added without growing the
wrapper past the ~1000-line budget, and so the engine is a real file that ruff
lints. The WHY of every rule below is documented in the hook's own header —
that header stays the single place a reader forms a belief about what this
guard does and does not cover.

Contract with the wrapper (unchanged by the move):
  argv[1] = the raw PreToolUse payload (argv, never stdin — see the wrapper);
  exit 0  = allow;
  exit 2  = a real hit (stdout: the refusal lines, then ONE `#AUDIT# ` line);
  exit 3  = the payload could not be understood (the wrapper fails closed);
  anything else (an import/syntax error, a crash) also fails closed there.

Invoked by PATH (`python3 <this file>`), never `python3 -` / `-c`: a script
path puts THIS directory on sys.path[0], not the caller's cwd, so a stray
`json.py`/`re.py` in whatever directory the agent happens to be in cannot
shadow a stdlib module this guard imports (the #1046 class).
"""
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
#
# The boundary after `.secret` accepts anything that is NOT an identifier
# continuation (alnum/_/-) — including a bare `.` — so `<stem>.secret` blocks
# every copy/archive/backup of a real value file (`.bak`, `.gz`, a tilde
# backup, …) by DEFAULT, with no enumeration needed on that side. The one
# carve-out is an EXPLICIT allow-list of common config-file extensions
# (#165): `config.secret.json` is a config whose name merely carries
# `.secret` as an infix — a real, ordinary local-config convention — not
# this vault's own `<NAME>.secret` value file (the vault's real files match
# the TERMINAL case above, never an infix), and was falsely refused. This is
# an enumeration on the ALLOW side only; the guard's usual "no enumeration"
# stance (stated throughout this file) is about the BLOCK side, where an
# unlisted reader/head/extension would walk through SILENTLY — here an
# extension that isn't on the list stays BLOCKED, so a missing entry is a
# LOUD false positive: the file can be renamed to a listed extension, or the
# user can grant the (out-of-session-only) env bypass — never a silent leak.
#
# The allow-list match must be the WHOLE, FINAL extension, never merely
# present somewhere before a further suffix (adversarial review finding F1,
# #165): a first draft's inner boundary accepted a `.` after the listed
# extension as "terminal", which let `DB_PASS.secret.json.gz` — an archive
# of a real value file wearing a config-shaped disguise — through
# unblocked. The inner boundary below is the OUTER one PLUS `.` added to the
# excluded class, so a listed extension followed by ANYTHING (including
# another `.`) is never mistaken for the end of the name. The honest cost:
# a real config's own future backup, `config.secret.json.bak`, is now
# blocked too (a KNOWN GAPS bullet documents this trade explicitly).
#
# The stem's quantifier is BOUNDED (#162), not `*` (unbounded). `.secret`
# starts with characters the stem class ([A-Za-z0-9_.-]) itself accepts, so
# an unbounded stem forces a full greedy-then-backtrack sweep at every start
# offset whenever a segment has a long run of stem-legal characters with no
# `.secret` anywhere -- O(n^2), measured at 10.5s for a 50KB ordinary
# argument (a base64 blob, an embedded file body -- no glob, no exploit
# shape) against this hook's own 5s harness timeout. `{0,253}` bounds the
# stem to at most 254 characters immediately before the literal `.secret`
# (1 required leading char + 253 more) -- close to, but deliberately more
# generous than, Linux's own NAME_MAX (255 bytes/component): a real
# terminal `<NAME>.secret` FILE's full name, suffix included, tops out at
# 255 bytes, so its stem alone can be at most 248 -- the extra headroom up
# to 254 only ever admits a HANDFUL of stem lengths (249-254) that could
# never belong to a real file on disk, and erring generous there means
# erring toward BLOCKING, never toward a gap.
#
# The one genuine behavioural difference from the unbounded original,
# found by an adversarial review of this fix (#162 round 2) and reproduced
# against the real hook: a stem whose final 254 characters before `.secret`
# contain NO alnum/underscore character at all (e.g. 300 dashes) has
# nowhere for the required leading `[A-Za-z0-9_]` to anchor within the
# bounded window, so the match does NOT fire -- unlike the unbounded
# original, which always found an earlier alnum character no matter how
# far back it sat. This is real and NARROW (a KNOWN GAPS bullet documents
# it), not the "match still fires regardless of stem length" claim an
# earlier draft of this comment made. Its security impact is negligible:
# no stem shaped this way can ever name a real value file either (it
# already exceeds NAME_MAX), and the bounded window still catches every
# alnum-leading run genuinely within reach, so no real `<NAME>.secret`
# reference goes unblocked by this.
_VALUE_FILE_CONFIG_EXT = r"(?:json|yaml|yml|env|toml|ini)"
VALUE_FILE_RE = re.compile(
    r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,253}|[*?\]}])\.secret"
    r"(?!\." + _VALUE_FILE_CONFIG_EXT + r"(?![A-Za-z0-9_.-]))"
    r"(?![A-Za-z0-9_-])")
# C. the store's PARENT swept recursively or archived (review F2). Anchored on
# `.claude` NOT followed by a deeper path component, so `~/.claude/projects`
# — the transcript greps this repo's own work runs constantly — is untouched.
CLAUDE_ROOT_RE = re.compile(r"\.claude/?(?![A-Za-z0-9_./-])")
# The two `[A-Za-z]*` runs are BOUNDED to `{0,254}` each (#162 round 2) —
# the same overlapping-class-vs-required-char shape as VALUE_FILE_RE and
# BRACE_RE above: `[A-Za-z]*` accepts the `r`/`R` the pattern must find
# next, so a long run of only letters with no r/R anywhere forces the same
# greedy-then-backtrack sweep. Reachability here is LOWER than BRACE_RE's —
# `sweeps_the_parent` only reaches `RECURSIVE_RE.search` after the segment
# has already matched `CLAUDE_ROOT_RE`/`globbed_parent_ref` (a bare
# `.claude`/glob-of-it must already be present) — but the defect is
# identical and was found by the same adversarial review. 254 keeps every
# real short-flag cluster (`-r`, `-avz`, `--recursive`) matching unchanged;
# no legitimate flag cluster is remotely that long.
RECURSIVE_RE = re.compile(
    r"(?:^|\s)(?:-[A-Za-z]{0,254}[rR][A-Za-z]{0,254}|--recursive)(?=\s|$)")
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


# The two `[^{}]*` groups are BOUNDED to `{0,254}` each (#162 round 2), for
# the identical reason VALUE_FILE_RE's stem quantifier was bounded above:
# `[^{}]*` accepts the literal `,` it must find next, so an unbounded pair
# forces a full greedy-then-backtrack sweep whenever a `{` opens with no
# matching `,...}` anywhere in reach. This was found MORE reachable than
# VALUE_FILE_RE's own gap, not less: `expand_braces` runs on every token of
# every command via `path_candidates`/`globbed_store_ref` (not gated behind
# an earlier miss), and its own recursion (below) can re-run the search on
# the SAME pathological content across multiple `_depth` levels. Measured
# before the bound: a single 4KB unclosed brace already exceeded the 5s
# harness budget end to end through the real hook — an order of magnitude
# smaller than the 50KB that triggered VALUE_FILE_RE's own gap. 254 per
# side keeps every realistic brace alternative (a path segment, a filename)
# matching exactly as before — no real alternative inside `~/.claude/
# {secrets,x}/*`-shaped globbing is remotely that long — while capping the
# worst adversarial construction (many repeated near-miss segments, sized
# to this hook's own ~128KB argv ceiling) at ~1.3s: bounded and comfortably
# inside the 5s harness budget, but NOT sub-second — see the KNOWN GAPS
# timeout bullet above for the honest measured numbers across all three
# regexes this round bounded.
BRACE_RE = re.compile(r"\{([^{}]{0,254},[^{}]{0,254})\}")
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
        if two == "\\\n":
            # A line continuation is whitespace to the shell, never a command
            # separator (#1153: a wrapped `ssh … \<nl> -i <key> host` was
            # split into a second segment headed by `-i`).
            buf.append(" ")
            i += 2
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


# --- E. the plain key-file root, `~/.secrets/` (#1153) ----------------------
# Legacy/durable credentials live as PLAIN files there (a bare value, or
# `NAME=value` lines; the #529 `--persist` copies of vault values too), and
# the odoo-erp 8236 lane printed one twice in an hour with an inline
# `cat`/`python3 -c 'print(open(...))'` "format check". Same engine, same
# deny-by-default stance, ONE difference in what counts as safe: this root
# also holds the fleet's SSH keys, so a head that takes the path as a KEY
# ARGUMENT and never prints it (`ssh -i`, `rsync -e 'ssh -i …'`, the secret
# CLI) is allowed alongside the metadata heads.
#
# THE ACCOUNTING RULE. The store's check is per SEGMENT (an allowlisted head
# exempts the whole segment). That is too coarse here: `ssh -i <root>/k host
# 'cat <root>/x'` has an allowed head and a read in the SAME segment. So each
# segment is tokenized (shlex, quotes removed — which also defeats a spliced
# `.secret"s"`) and EVERY token naming the root must be accounted for: an
# identity-file argument of a key consumer, an operand of an unpiped metadata
# head, or a pre-child argument of the secret CLI. One unaccounted reference
# denies the segment. A parse that goes wrong can therefore only fail to
# account for something — i.e. deny — never allow a stray reader.
KEY_DIR = ".secrets"
KEY_DIR_RE = re.compile(r"(?<![A-Za-z0-9_.-])\.secrets(?![A-Za-z0-9_-])")
# Unpiped metadata heads. The per-head option checks exist because `wc
# --files0-from=F` and `sha256sum -c F` ingest F as a LIST and echo its lines
# back in their own error text — the #153 `file -f` lesson again.
KEY_META_HEADS = {"ls", "stat", "test", "[", "wc", "sha256sum"}
KEY_CONSUMERS = {  # head -> its getopt short options that take an argument
    "ssh": "BbcDEeFIiJLlmOoPpQRSWw",
    "scp": "cDFiJlMoPSX",
    "sftp": "BbcDFiJlmoPRSsX",
}
RSYNC_ARGOPTS = "eBfMT@"
IDENTITY_OPT_RE = re.compile(r"(?i)^\s*identityfile\s*[=\s]")
PROXY_OPT_RE = re.compile(r"(?i)^\s*proxycommand\s*[=\s]\s*(.+)$", re.S)
DECLARE_HEADS = {"export", "local", "declare", "readonly", "typeset"}
SHELL_WORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{",
               "time"}
SECRET_CLI_FLAGS = {"--stdin", "--replace", "--allow-plain", "--public"}
SECRET_CLI_ARGFLAGS = {"--ttl", "--keep", "--port", "--env", "--persist",
                       "--file", "--persist-map"}
SECRET_PATH_FLAGS = {"--persist", "--file", "--persist-map"}


def _dequoted(text):
    """`text` as the shell will see it once quoting is removed."""
    try:
        return " ".join(shlex.split(text))
    except ValueError:
        return re.sub(r"[\"'\\]", "", text)


def key_ref(text, cwd_hint=None):
    """The first way `text` can name the key-file root, or None.

    Literal (`~/`, `$HOME/`, `/home/<u>/`, bare after a `cd ~`), quote-spliced
    (`.secret"s"`), and every #156 expansion layer via `path_candidates`
    (globs anchored on a 3-char literal prefix, braces, `.`/`..` noise). Any
    `.secrets` path COMPONENT counts, not only the one under $HOME: a relative
    read after an earlier-call `cd ~` is invisible to a stateless hook
    otherwise, and a `.secrets` directory anywhere is a credential dir.
    """
    for variant in (text, _dequoted(text)):
        if KEY_DIR_RE.search(variant):
            toks = [t for t in TOKEN_RE.findall(variant) if KEY_DIR_RE.search(t)]
            return toks[0] if toks else KEY_DIR
        for t, comps in path_candidates(variant, cwd_hint):
            if any(can_be(c, KEY_DIR) for c in comps):
                return t
    return None


def _cmd_start(tk):
    """Index of the real command word, past wrappers/keywords/assignments."""
    i, n = 0, len(tk)
    while i < n:
        t = tk[i]
        if t in SHELL_WORDS or ASSIGN_RE.match(t) or t in (
                "env", "time", "command", "builtin", "nohup"):
            i += 1
        elif t == "timeout":
            i += 1
            while i < n and tk[i].startswith("-"):
                i += 2 if tk[i] in ("-k", "-s", "--kill-after", "--signal") else 1
            i += 1                                # the DURATION
        elif t in ("sudo", "nice", "ionice", "exec"):
            i += 1
            while i < n and tk[i].startswith("-"):
                i += 2 if tk[i] in ("-u", "-g", "-n", "-c", "-C", "-D", "-p",
                                    "-r", "-t", "-U", "-h", "-a") else 1
        else:
            return i
    return None


def _getopt(tk, i, argopts):
    """Parse the option token tk[i] -> (next index, [(opt, value, idx)]).

    `idx` is the token index holding the value (the same token for an
    inline `-ifile` / `-oX=y`, the next one otherwise), so the caller can mark
    exactly that token as accounted for.
    """
    tok, found = tk[i], []
    for j, ch in enumerate(tok[1:], start=1):
        if ch in argopts:
            if tok[j + 1:]:
                return i + 1, found + [(ch, tok[j + 1:], i)]
            if i + 1 < len(tk):
                return i + 2, found + [(ch, tk[i + 1], i + 1)]
            return i + 1, found
    return i + 1, found


def _identity_args(tk, start, head):
    """Token indices that are identity-file arguments of an ssh/scp/sftp call.

    ssh re-enters option parsing after the destination (OpenSSH `goto again`),
    so options are read until the SECOND bare word — where the remote command
    starts and nothing is an option any more (`ssh h cat -i <root>/x` is a
    remote read, not an identity). scp/sftp permute (glibc getopt): every
    option token is read, and every operand stays unaccounted.
    """
    argopts, ok, seen_host = KEY_CONSUMERS[head], set(), False
    i = start + 1
    while i < len(tk):
        t = tk[i]
        if t == "--":
            break
        if t.startswith("-") and len(t) > 1:
            i, found = _getopt(tk, i, argopts)
            for opt, val, idx in found:
                if opt == "i" or (opt == "o" and IDENTITY_OPT_RE.match(val)):
                    ok.add(idx)
                elif opt == "o":
                    # A ProxyCommand's stdout feeds the connection, but ssh
                    # echoes a bad banner line back in its error — so it is
                    # accounted only when it is itself an identity-only ssh.
                    proxy = PROXY_OPT_RE.match(val)
                    if proxy and _rsh_is_identity_only(proxy.group(1)):
                        ok.add(idx)
            continue
        if head == "ssh":
            if seen_host:
                # The REMOTE command: ssh joins it with spaces and hands it to
                # the remote shell, so it is judged as shell text with the
                # SAME rules — `h 'cat <root>/x'` is a remote read (its output
                # comes back here), `h 'ssh -i <root>/k h2 uptime'` is the
                # remote box using its own key. Accounted all-or-nothing.
                if text_is_clean(" ".join(tk[i:])):
                    ok.update(range(i, len(tk)))
                break
            seen_host = True
        i += 1
    return ok


def _rsync_rsh_args(tk, start):
    """Token indices of an rsync `-e`/`--rsh` value that is an ssh identity
    call — and nothing else: `--password-file=<root>/x` or a key-file operand
    stays unaccounted."""
    ok = set()
    i = start + 1
    while i < len(tk):
        t, found = tk[i], []
        if t.startswith("--rsh="):
            found, i = [("e", t[len("--rsh="):], i)], i + 1
        elif t == "--rsh" and i + 1 < len(tk):
            found, i = [("e", tk[i + 1], i + 1)], i + 2
        elif t.startswith("-") and not t.startswith("--") and len(t) > 1:
            i, found = _getopt(tk, i, RSYNC_ARGOPTS)
        else:
            i += 1
        for opt, val, idx in found:
            if opt == "e" and _rsh_is_identity_only(val):
                ok.add(idx)
    return ok


def _rsh_is_identity_only(val):
    try:
        sub = shlex.split(val)
    except ValueError:
        return False
    s = _cmd_start(sub)
    if s is None or sub[s].rsplit("/", 1)[-1] != "ssh":
        return False
    return not _unaccounted(sub, "")


def _meta_options_safe(head, args):
    if head == "wc":
        return not any(a.startswith("--files0-from") for a in args)
    if head == "sha256sum":
        return not any(a == "--check" or (a.startswith("-") and not a.startswith("--")
                                          and "c" in a[1:]) for a in args)
    return True


def _secret_cli_start(tk, start):
    """Index just past `… airuleset.py secret`, or None if this is not it."""
    head = tk[start].rsplit("/", 1)[-1]
    i = start + 1
    if re.fullmatch(r"python3?(\.\d+)?", head):
        if i >= len(tk) or tk[i].rsplit("/", 1)[-1] != "airuleset.py":
            return None
        i += 1
    elif head != "airuleset.py":
        return None
    return i + 1 if i < len(tk) and tk[i] == "secret" else None


def _is_int(value):
    """The CLI's own test (`int(value)` in _secret_apply_remainder)."""
    try:
        int(value)
    except ValueError:
        return False
    return True


def _secret_cli_args(tk, cli):
    """(accounted token indices, child start) for `… airuleset.py secret <action>`.

    Mirrors cli_vault._secret_apply_remainder: our flags, the NAME, our flags
    again, an optional `--`; an int flag with a non-int value is NOT ours
    (the CLI breaks there, and the rest becomes the `exec` child). Only the
    VALUE of a path flag (--persist / --persist-map / --file) and inspect's
    one positional are accounted — a root reference in any other flag value
    or in a stray positional stays unaccounted (review A finding 1: a
    blanket pre-child exemption let `exec N --ttl <root>/k cmd` through).
    """
    action = tk[cli] if cli < len(tk) else ""
    ok, seen_name, i = set(), False, cli + 1
    while i < len(tk):
        t = tk[i]
        key, eq, inline = t.partition("=")
        if t == "--":
            return ok, i + 1
        if t in SECRET_CLI_FLAGS:
            i += 1
            continue
        if key in SECRET_CLI_ARGFLAGS:
            idx, value = (i, inline) if eq else (i + 1, tk[i + 1] if i + 1 < len(tk) else "")
            if key in ("--ttl", "--keep", "--port") and not _is_int(value):
                return ok, i         # not a flag of ours after all
            if key in SECRET_PATH_FLAGS:
                ok.add(idx)
            i = idx + 1
            continue
        if not seen_name:
            if action == "inspect":
                ok.add(i)
            seen_name, i = True, i + 1
            continue
        if action == "exec":
            return ok, i          # the REMAINDER child, without a `--`
        i += 1                    # `request A B …`: more names, never accounted
    return ok, len(tk)


def _assignment_is_identity_only(tok):
    """`SSH="ssh -i <key> …"` / `O="-i <key> -o …"` holds a CALL, not a path.

    A bare `K=<root>/k` is never accounted: a later `cat "$K"` names nothing
    this hook could see. A value that is an identity-only ssh-family call (or
    an ssh option list, checked as `ssh <value>`) cannot be turned into a read
    by reflex — `cat $O` is `cat -i …`, an option error.
    """
    value = tok.split("=", 1)[1]
    if value.lstrip().startswith("-"):
        value = "ssh " + value
    return _rsh_is_identity_only(value)


def _accounted(tk, term):
    """Indices of tokens whose root reference is a sanctioned, non-printing use."""
    start = _cmd_start(tk)
    assigns = {i for i in range(len(tk) if start is None else start)
               if ASSIGN_RE.match(tk[i]) and _assignment_is_identity_only(tk[i])}
    if start is None:
        return assigns
    head = tk[start].rsplit("/", 1)[-1].lower()
    if head in DECLARE_HEADS:
        return assigns | {i for i in range(start + 1, len(tk))
                          if ASSIGN_RE.match(tk[i]) and _assignment_is_identity_only(tk[i])}
    return assigns | _accounted_command(tk, start, head, term)


def _accounted_command(tk, start, head, term):
    rest = range(start + 1, len(tk))
    if head in KEY_META_HEADS:
        # Piped, a metadata head is a NAME SOURCE for whatever consumes it
        # (`ls <root>/* | xargs cat`) — the store's review-F5 rule.
        if term == "|" or not _meta_options_safe(head, tk[start + 1:]):
            return set()
        return set(rest)
    if head in KEY_CONSUMERS:
        return _identity_args(tk, start, head)
    if head == "rsync":
        return _rsync_rsh_args(tk, start)
    cli = _secret_cli_start(tk, start)
    if cli is None:
        return set()
    ok, child = _secret_cli_args(tk, cli)
    if term == "|" and tk[cli:cli + 1] == ["inspect"]:
        return set()             # its `path:` line is a name source when piped
    if tk[cli:cli + 1] == ["exec"]:
        # The child runs with fd 1/2 filtered for the VAULT value only — a
        # plain key file it reads would print unfiltered, so the child is
        # accounted exactly like a command of its own.
        return ok | {child + j for j in _accounted(tk[child:], term)}
    return ok


def _unaccounted(tk, term, cwd_hint=None):
    # A redirection glued into one word (`ls <root>/k><root>/j`) is a WRITE
    # riding on an allowed argument (review C finding 7): a token naming the
    # root that also carries `<`/`>` is never accounted.
    ok = {i for i in _accounted(tk, term) if "<" not in tk[i] and ">" not in tk[i]}
    return [t for i, t in enumerate(tk) if i not in ok and key_ref(t, cwd_hint)]


KEY_AUDIT_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])\.secrets(?:/[A-Za-z0-9_.*?-]{1,64})?")


def key_audit_ref(ref):
    """The subset of a key-file reference safe to WRITE DOWN (#157 for rule E).

    A stray is a whole shell token, and a value can sit in the same token as
    the path (`printf <value>><root>/k`, `open("<root>/k","w").write(<value>)`
    — review B finding 2). Only `.secrets/<one file component>` is kept; a
    glob-only reference (no literal name) is logged as the fixed marker.
    """
    m = KEY_AUDIT_RE.search(ref or "")
    return m.group(0) if m else KEY_DIR + "(glob)"


def is_secret_inspect(segment):
    """`… airuleset.py secret inspect <one path>` — metadata only, any root."""
    try:
        tk = shlex.split(segment)
    except ValueError:
        return False
    start = _cmd_start(tk)
    cli = None if start is None else _secret_cli_start(tk, start)
    return cli is not None and tk[cli:cli + 1] == ["inspect"] and len(tk) == cli + 2


FLOW_TERMS = {"|", ")", "`"}


def effective_terms(segments):
    """Each segment's terminator, promoted to `|` when its output can FLOW.

    Review C finding 1: `|` alone missed `cat $(ls -d <root>/*)` (the inner
    segment ends at `)`), a backtick, `<(…)`, and a GROUP that is piped
    (`{ ls <root>/*; } | xargs cat`, `for …; do ls …; done | …`). A segment
    ending in `)`/backtick is inside a substitution or subshell; and when the
    command pipes ANYWHERE, no metadata listing in it is trusted — the
    conservative cut, since a stateless text check cannot follow a group.
    """
    piped = any(term == "|" for _seg, term in segments)
    return [(seg, "|" if (piped or term in FLOW_TERMS) else term)
            for seg, term in segments]


def text_is_clean(text):
    """No segment of shell `text` uses the key-file root unsafely."""
    return not any(key_violation(seg, term)
                   for seg, term in effective_terms(split_segments(text)))


def key_violation(segment, term, cwd_hint=None):
    """None when `segment` is clean or only uses the root safely, else the ref."""
    ref = key_ref(segment, cwd_hint)
    if not ref:
        return None
    try:
        tk = shlex.split(segment)
    except ValueError:
        return ref                # unbalanced quoting around a reference
    stray = _unaccounted(tk, term, cwd_hint)
    if stray:
        return stray[0]
    # The segment names the root but no single argument does (a reference
    # only an expansion across tokens produces): nothing accounts for it.
    if not any(key_ref(t, cwd_hint) for t in tk):
        return ref
    return None


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
    # #1153: a file TOOL reading the key-file root has no head to exempt it —
    # every field reference is a read (or a write) of a key.
    keyed = [(k, key_ref(v), v) for k, v in fields if key_ref(v)]
    if bad or keyed:
        print("\n".join("  %s %s -> %s" % (tool or "tool", k, excerpt(v))
                        for k, _refs, v in bad + keyed))
        print("#ROOTS# %s" % ",".join(
            r for r, hit in (("store", bad), ("keyfile", keyed)) if hit))
        # `fields` holds (key, value) PAIRS while `bad` holds triples — the
        # two are not interchangeable, and unpacking one as the other threw
        # inside this branch. It still exited 2 because fail_closed does too,
        # so the store stayed shut and every block test passed while the real
        # refusal, the audit line and the user's env bypass were all gone.
        audit(tool, [r for k, _rs, v in bad
                     for r in audit_refs(v)] + [key_audit_ref(r) for _k, r, _v in keyed],
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
roots = set()
for seg, term in effective_terms(split_segments(cmd)):
    head = head_of(seg)
    sweep = sweeps_the_parent(seg, head)
    if sweep:
        hits.append("%s  ->  %s (%s)" % (head or "?", excerpt(seg), sweep))
        refs.append(globbed_parent_ref(seg) or ".claude")
        roots.add("store")
        continue
    stray = key_violation(seg, term, cwd_hint)
    if stray:
        hits.append("%s  ->  %s (key file %s)" % (
            head or "(redirection/substitution)", excerpt(seg), excerpt(stray)))
        refs.append(key_audit_ref(stray))
        roots.add("keyfile")
        # A segment can name BOTH roots; the store check below still runs so
        # its audit refs and guidance are not lost.
    seg_refs = store_refs(seg, cwd_hint)
    if not seg_refs:
        cwd_hint = cd_target(seg, head) or cwd_hint
        continue
    if (head in ALLOW_HEADS or is_secret_inspect(seg)) and term != "|":
        # Piped, an allowlisted head is just a name source for whatever
        # consumes it — `ls <store>/* | xargs cat` (review F5).
        continue
    hits.append("%s  ->  %s" % (head or "(redirection/substitution)",
                                excerpt(seg)))
    refs.extend(audit_refs(seg, cwd_hint))
    roots.add("store")

if hits:
    print("\n".join("  " + h for h in dict.fromkeys(hits)))
    print("#ROOTS# %s" % ",".join(sorted(roots)))
    audit(tool, refs, cmd)
    sys.exit(2)
sys.exit(0)
