"""Shell-text primitives shared by the vault read guard's two roots (#1153).

Moved VERBATIM out of hooks/vault_read_guard.py when rule E (the plain
key-file root) pushed that file past the ~1000-line budget: the segment
splitter, the token regex, and the #156 expansion layers (globs anchored on
a literal prefix, braces, `.`/`..` noise). Pure functions, no I/O, no
module state — imported by vault_read_guard.py (store rules A-D) and
vault_keyroot.py (rule E), so the two roots can never drift onto two
parsers. The WHY of each lives in hooks/block-vault-store-read.sh's header.
"""
import fnmatch
import re

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
