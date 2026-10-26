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

# A command substitution's result becomes an ARGUMENT of the OUTER command, so
# the outer text is kept WHOLE with the whole `$(…)` / `…` replaced by this
# placeholder (#1168). It is a bare `*`: unquoted or alone it is an unanchored
# glob (literal prefix length 0), so `can_be` never lets it stand for a store
# component on its own — `echo "$(date)"` stays allowed. But when the
# substitution is SPLICED INTO a path word that also names a root
# (`cat ~/.secrets$(x)/k` -> `cat ~/.secrets*/k`), the `*` keeps the real
# `.secrets` boundary VISIBLE to the glob layer, which still resolves it to the
# store and BLOCKS — an inert word there (`~/.secrets__x__/k`) would have
# masked the boundary and leaked the read (review, #1168). A separator-free
# token cannot become a redirection or a second word.
SUBST_PLACEHOLDER = "*"
_SUBST_MAX_DEPTH = 8


def _match_cmdsubst(text, k):
    """Index of the `)` closing the `$(` at text[k:k+2], or -1 if unmatched.

    A context STACK, not a paren counter: inside a `$(…)` a `"…"` string makes
    `)` literal, but a `$(` nested INSIDE that string reopens a command
    context whose own `)` must be matched. `'…'`, backticks and `\\` escapes
    are honoured the same way the main splitter honours them, so a `)` sitting
    in a quoted body is never taken for the close.
    """
    n, i = len(text), k + 2
    stack = ["("]                       # the initial `$(`
    while i < n and stack:
        c, two = text[i], text[i:i + 2]
        top = stack[-1]
        if top == "'":
            if c == "'":
                stack.pop()
            i += 1
        elif top == "`":
            if c == "\\":
                i += 2
            elif c == "`":
                stack.pop()
                i += 1
            else:
                i += 1
        elif top == '"':
            if c == "\\":
                i += 2
            elif two == "$(":
                stack.append("(")
                i += 2
            elif c == "`":
                stack.append("`")
                i += 1
            elif c == '"':
                stack.pop()
                i += 1
            else:
                i += 1
        else:                            # a command context
            if c == "\\":
                i += 2
            elif two == "$(":
                stack.append("(")
                i += 2
            elif c == "'":
                stack.append("'")
                i += 1
            elif c == '"':
                stack.append('"')
                i += 1
            elif c == "`":
                stack.append("`")
                i += 1
            elif c == "(":
                stack.append("(")
                i += 1
            elif c == ")":
                stack.pop()
                if not stack:
                    return i
                i += 1
            else:
                i += 1
    return -1


def _match_backtick(text, k):
    """Index of the backtick closing the one at text[k], or -1 if unmatched."""
    n, i = len(text), k + 1
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i
        i += 1
    return -1


def _subst_body(body, closer, depth):
    """A substitution BODY as its own segment(s), with its LAST one marked flow.

    The body is a real command judged by its OWN operands (a key/store read
    there stays blocked, a `cat /tmp/x` does not), but its OUTPUT flows into
    the outer command as the substitution's value — exactly like a listing
    piped into a consumer (review C: a metadata head's output is a NAME SOURCE
    whenever it can flow into another command, `$(…)`/backticks included). So
    the LAST body segment carries the `)` / backtick terminator, which
    `effective_terms` promotes to a flow (`|`) — restoring the pre-#1168
    treatment of an UNQUOTED `cat $(ls <store>)` and extending it to the
    quoted form the old splitter left inconsistent. An empty body contributes
    nothing.
    """
    sub = split_segments(body, depth + 1)
    if sub:
        sub[-1] = (sub[-1][0], closer)
    return sub


def _heredoc_open(text, i):
    """`(end, delimiter, quoted, strip_tabs)` for the `<<` at text[i], or None.

    Bash's own rules: an optional `-` (strip leading tabs), optional blanks,
    then one word whose quotes/backslashes are removed; any quoting makes the
    body literal (no expansion)."""
    j, n = i + 2, len(text)
    strip = j < n and text[j] == "-"
    j += strip
    while j < n and text[j] in " \t":
        j += 1
    word, quoted = [], False
    while j < n and text[j] not in " \t\n;&|()<>":
        if text[j] in "'\"":
            close = text.find(text[j], j + 1)
            if close < 0:
                return None
            word.append(text[j + 1:close])
            quoted, j = True, close + 1
        elif text[j] == "\\" and j + 1 < n:
            word.append(text[j + 1])
            quoted, j = True, j + 2
        else:
            word.append(text[j])
            j += 1
    return (j, "".join(word), quoted, strip) if word else None


def _heredoc_body(text, i, delim, strip):
    """The body starting at text[i] and the index just past its delimiter line
    (the rest of the text when the delimiter never comes, as in bash)."""
    lines, n = [], len(text)
    while i < n:
        nl = text.find("\n", i)
        nl = n if nl < 0 else nl
        line = text[i:nl]
        i = nl + 1
        if (line.lstrip("\t") if strip else line) == delim:
            return "\n".join(lines), i
        lines.append(line)
    return "\n".join(lines), n


def _inert_sink(seg):
    """A plain `cat`/`tee` that writes its literal stdin out, ending at a plain
    command separator — never a pipe, `>(…)`, a subshell or `&`."""
    words = seg[0].split() if seg else []
    return (bool(words) and words[0] in ("cat", "tee")
            and seg[1] in ("\n", ";", "&&", "||", ""))


def split_segments(text, _depth=0, heredocs=False):
    """Quote-aware split on shell separators -> [(segment, terminator), ...].

    A command substitution's BODY is emitted as its own segment(s) — headed by
    the real command inside it, so a read there is judged by its OWN operands
    (a key/store read stays blocked, a `cat /tmp/x` does not) — while the OUTER
    command stays ONE balanced segment with the whole `$(…)` / `…` replaced by
    SUBST_PLACEHOLDER (#1168). Before #1168 the splitter cut the outer command
    AT the `$(`, leaving the consumer as the unbalanced fragment
    `python3 … --key-file <k> --body "` that shlex could not read, so the
    documented Odoo posting form fell into fail-closed. An UNMATCHED
    substitution keeps that old cut (still fail-closed). Inside SINGLE quotes
    nothing is a separator, which keeps a `python3 -c '...'` body intact as one
    segment headed by python3.

    The TERMINATOR is returned because it changes what an allowlisted head
    means: piped, `ls` is not a listing, it is a name source for whatever
    consumes it (review F5).

    `heredocs=True` (issue 1137 B1, the read guard's own call): a heredoc
    body is cut out of the command and judged on its own, so an apostrophe
    in it can no longer open a quote that swallows the next command; a
    QUOTED body (`<<'EOF'`) fed to a plain `cat`/`tee` that is not piped is
    literal data written out, never a read, and is dropped. Any other body
    (unquoted, so expanded; or fed to an interpreter) is still judged.
    """
    segs, buf, pending = [], [], []
    i, n = 0, len(text)
    in_sq = in_dq = False

    def flush(term):
        seg = ("".join(buf), term)
        for hd in pending:
            hd[3] = hd[3] or seg          # the command the heredoc feeds
        segs.append(seg)
        buf.clear()

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
            close = -1 if _depth >= _SUBST_MAX_DEPTH else _match_cmdsubst(text, i)
            if close < 0:
                flush("$(")                         # unmatched: the old cut
                i += 2
                continue
            segs.extend(_subst_body(text[i + 2:close], ")", _depth))
            buf.append(SUBST_PLACEHOLDER)
            i = close + 1
            continue
        if c == "`":
            close = -1 if _depth >= _SUBST_MAX_DEPTH else _match_backtick(text, i)
            if close < 0:
                flush("`")                          # unmatched: the old cut
                i += 1
                continue
            segs.extend(_subst_body(text[i + 1:close], "`", _depth))
            buf.append(SUBST_PLACEHOLDER)
            i = close + 1
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
        if heredocs and c == "\\" and i + 1 < n:
            buf.append(two)       # `\"` / `\'` / `\;` are literal, as in bash
            i += 2
            continue
        if heredocs and two == "<<" and text[i + 2:i + 3] != "<":
            opened = _heredoc_open(text, i)
            if opened:
                end, delim, quoted, strip = opened
                pending.append([delim, quoted, strip, None])
                buf.append(text[i:end])
                i = end
                continue
        if c == "\n" and pending:
            flush(c)
            i += 1
            for delim, quoted, strip, seg in pending:
                body, i = _heredoc_body(text, i, delim, strip)
                if not (quoted and _inert_sink(seg)):
                    segs.extend(split_segments(
                        body, _depth + 1, _depth < _SUBST_MAX_DEPTH))
            pending = []
            continue
        if two in ("&&", "||"):
            flush(two)
            i += 2
            continue
        if c in ";|&\n()":
            flush(c)
            i += 1
            continue
        buf.append(c)
        i += 1
    flush("")
    return segs
