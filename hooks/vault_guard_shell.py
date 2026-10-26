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
import shlex

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


PLAIN_TERMS = ("\n", ";", "&&", "||", "")
# Words that open / close a compound command or a brace group: a heredoc fed
# to a `cat` inside one can have its output piped on by the closing word
# (`for …; do cat <<X … done | bash`), so its body is never data.
GROUP_OPEN = {"{", "if", "while", "until", "for", "case", "select"}
GROUP_CLOSE = {"}", "fi", "done", "esac"}
# Heads that run a FILE (or their stdin) as code — the review-A3 set.
EXEC_HEADS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "fish", "busybox",
              "source", ".", "eval", "exec", "xargs", "perl", "ruby", "node",
              "nodejs", "php", "lua", "Rscript", "tclsh", "awk", "gawk", "mawk",
              "nawk", "sed", "make", "pypy", "pypy3"}
PY_HEAD_RE = re.compile(r"python[0-9.]*$")
_PREFIX_WORDS = {"sudo", "env", "time", "nice", "ionice", "nohup", "setsid",
                 "command", "builtin", "stdbuf", "timeout", "!"}
_REDIRECT_TARGET_RE = re.compile(r"(?<![<>&])\d*>>?\|?\s*([^\s;&|<>()]+)")


def _words(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _head_index(tk):
    """Index of the command word past wrappers and assignments, or None."""
    i = 0
    while i < len(tk) and (tk[i] in _PREFIX_WORDS or ASSIGN_RE.match(tk[i])
                           or (i and tk[i - 1] in _PREFIX_WORDS and tk[i].startswith("-"))
                           or (i and tk[i - 1] == "timeout" and tk[i][:1].isdigit())):
        i += 1
    return i if i < len(tk) else None


def _is_exec_head(word):
    base = word.rsplit("/", 1)[-1]
    return base in EXEC_HEADS or bool(PY_HEAD_RE.match(base))


def _write_targets(seg):
    """The files a `cat`/`tee` heredoc segment writes (redirects, tee operands)."""
    out = [m.group(1).strip("'\"") for m in _REDIRECT_TARGET_RE.finditer(seg[0])]
    tk = _words(seg[0])
    if tk and tk[0] == "tee":
        out += [t for t in tk[1:] if not t.startswith(("-", "<", ">"))]
    return [t for t in out if t.rsplit("/", 1)[-1] and t != "/dev/null"]


def _runs_target(targets, later):
    """Does a LATER segment run one of `targets` — by an interpreter, by path,
    or by piping/substituting it into one (review A3)?"""
    names = {t.rsplit("/", 1)[-1] for t in targets}

    def names_it(tok):
        return tok.rsplit("/", 1)[-1] in names

    flows = False
    for seg, term in later:
        tk = _words(seg)
        h = _head_index(tk)
        if h is None:
            continue
        if flows and _is_exec_head(tk[h]):
            return True
        if "/" in tk[h] and names_it(tk[h]):
            return True                       # `./s.sh`, `/tmp/s.sh`
        hit = any(names_it(t) for t in tk[h + 1:])
        if hit and _is_exec_head(tk[h]):
            return True
        if hit and term in ("|", ")", "`"):
            flows = True
    return False


GIT_GLOBAL = {"-C", "-c", "--git-dir", "--work-tree"}
GH_PROSE = {("issue", "comment"), ("issue", "create"), ("issue", "edit"),
            ("pr", "comment"), ("pr", "create"), ("pr", "edit")}
_STDIN_FLAGS = {"-F", "--file", "--body-file"}


def _prose_from_stdin(seg):
    """`git commit -F -` / `gh issue|pr comment|create|edit -F -` (or
    `--body-file -`): the heredoc is the message TEXT, never run (review B2).
    A bare `git`/`gh` with nothing in front — a path or assignment could be
    anything."""
    tk = _words(seg[0])
    if not tk or tk[0] not in ("git", "gh"):
        return False
    if tk[0] == "git":
        i = 1
        while i < len(tk) and tk[i].startswith("-"):
            i += 2 if tk[i] in GIT_GLOBAL else 1
        if i >= len(tk) or tk[i] != "commit":
            return False
    elif tuple(tk[1:3]) not in GH_PROSE:
        return False
    for j, t in enumerate(tk):
        if t in ("-F-", "--file=-", "--body-file=-"):
            return True
        if t in _STDIN_FLAGS and tk[j + 1:j + 2] == ["-"]:
            return True
    return False


def _inert_body(seg, quoted, grouped, later):
    """A QUOTED heredoc body is data only when nothing can run it: fed to a
    plain `cat`/`tee` (or a prose `-F -` consumer) that ends at a plain
    separator outside any group, and no later segment runs the file it wrote."""
    if not (quoted and seg and not grouped and seg[1] in PLAIN_TERMS):
        return False
    if _prose_from_stdin(seg):
        return True
    words = seg[0].split()
    if not words or words[0] not in ("cat", "tee"):
        return False
    targets = _write_targets(seg)
    return not (targets and _runs_target(targets, later()))


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
    depth = [0, 0]                        # open `(`, open compound/brace group

    def flush(term):
        seg = ("".join(buf), term)
        for hd in pending:
            hd[3] = hd[3] or seg          # the command the heredoc feeds
        first = seg[0].split()[:1]
        if first and first[0] in GROUP_OPEN:
            depth[1] += 1
        elif first and first[0] in GROUP_CLOSE:
            depth[1] = max(0, depth[1] - 1)
        if term == "(":
            depth[0] += 1
        elif term == ")":
            depth[0] = max(0, depth[0] - 1)
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
                pending.append([delim, quoted, strip, None, any(depth)])
                buf.append(text[i:end])
                i = end
                continue
        if c == "\n" and pending:
            flush(c)
            i += 1
            bodies = []
            for hd in pending:
                body, i = _heredoc_body(text, i, hd[0], hd[2])
                bodies.append(body)
            rest = i

            def later(hd=None):
                k = next(j for j in range(len(segs) - 1, -1, -1) if segs[j] is hd[3])
                return segs[k + 1:] + split_segments(text[rest:], _depth + 1)
            for hd, body in zip(pending, bodies):
                if not _inert_body(hd[3], hd[1], hd[4], lambda hd=hd: later(hd)):
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
