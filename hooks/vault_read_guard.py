"""The matcher behind hooks/block-vault-store-read.sh (#153/#156, #1153).

Moved VERBATIM out of the hook's `python3 - <<'PYEOF'` heredoc (#1153) so the
second protected root (`~/.secrets/`) could be added without growing the
wrapper past the ~1000-line budget, and so the engine is a real file that ruff
lints. The WHY of every rule below is documented in the hook's own header —
that header stays the single place a reader forms a belief about what this
guard does and does not cover. Three files, one parser: this engine (payload
handling, store rules A-D, the verdict loop), vault_guard_shell.py (the
shared segment splitter + #156 expansion primitives) and vault_keyroot.py
(rule E, the plain key-file root) — both moved verbatim out of this file.

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
import hashlib
import json
import re
import shlex
import sys

# Invoked by path, so sys.path[0] is this directory: the two siblings below
# resolve from here, never from the caller's cwd. An import failure exits
# non-zero-non-2, which the wrapper turns into fail_closed.
from vault_guard_shell import (ASSIGN_RE, TOKEN_RE, can_be,
                               path_candidates,
                               split_segments)
from vault_keyroot import (effective_terms, is_pub_path, is_secret_inspect,
                           key_audit_ref, key_ref, key_violation)

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
    # #1153: a file TOOL reading the key-file root has no head to exempt it —
    # every field reference is a read (or a write) of a key. Slice 2: a
    # READ of one literal `*.pub` path is public material (Read/Grep only —
    # a Write/Edit there, or a Glob listing names, stays refused).
    keyed = [(k, key_ref(v), v) for k, v in fields if key_ref(v)
             and not (tool in ("Read", "Grep") and k in ("file_path", "path")
                      and is_pub_path(v))]
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
