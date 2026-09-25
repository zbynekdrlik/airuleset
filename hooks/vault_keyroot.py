"""Rule E of the vault read guard: the plain key-file root `~/.secrets/` (#1153).

Split out of hooks/vault_read_guard.py (which imports `key_violation`,
`key_ref`, `key_audit_ref`, `is_secret_inspect`, `effective_terms`) so the
engine stays under the ~1000-line budget. Built on the SAME primitives as
the store rules (hooks/vault_guard_shell.py) — one parser for both roots.
"""
import re
import shlex

from vault_guard_shell import (ASSIGN_RE, TOKEN_RE, can_be, path_candidates,
                               split_segments)

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
# head, or a pre-child argument of the secret CLI — and since slice 2 the
# VALUE of a prose option (gh/git table), the `-f` of a pure `ssh-keygen
# -l/-y`, a literal `*.pub` path, and a one-path `secret inspect` piped only
# into text filters (since slice 3 a prose command pipes the same way, since
# slice 5 a bare `ls`/`stat`). One unaccounted reference denies the segment.
# A parse that goes wrong can therefore only fail to account for something —
# i.e. deny — never allow a stray reader.
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


# --- slice 2: per-head allowances (#1153 issuecomment-5830067896) -----------
# Each is an explicit table, never a heuristic about quoting: Approach 2 of the
# design ("any quoted argument is prose") was rejected because `python3 -c
# '…open("<root>/x")…'` is a quoted argument that DOES open the file.
#
# PROSE: a (head, option) pair whose VALUE the program uses as TEXT and never
# opens. gh (pflag) and git (parse-options) both take a required option value
# from the NEXT token even when it starts with `-`, so the value is exactly
# the token after the option. Every OTHER option must be known — one that
# takes a value is skipped with its value (a root path there stays
# unaccounted, so `-F <root>/x` / `--body-file` / `git commit -F` deny); an
# UNKNOWN option ends the scan (`git commit --fil -m <root>/x` is `--file -m`
# by abbreviation), and `--` ends it too (pathspecs follow). A positional is
# never accounted.
GH_PROSE_SUBCMDS = {("issue", "comment"), ("issue", "create"), ("issue", "edit"),
                    ("pr", "comment"), ("pr", "create"), ("pr", "edit")}
GH_TEXT_OPTS = {"--body", "-b", "--title", "-t"}
GH_ARG_OPTS = {"-F", "--body-file", "-R", "--repo", "-a", "--assignee", "-l",
               "--label", "-m", "--milestone", "-p", "--project", "-T", "--template",
               "-r", "--reviewer", "-B", "--base", "-H", "--head", "--add-label",
               "--remove-label", "--add-assignee", "--remove-assignee",
               "--add-project", "--remove-project", "--add-reviewer",
               "--remove-reviewer", "--recover"}
GH_BOOL_OPTS = {"-e", "--editor", "-w", "--web", "--edit-last", "--delete-last",
                "--create-if-none", "-d", "--draft", "-f", "--fill", "--fill-first",
                "--fill-verbose", "--no-maintainer-edit", "--dry-run",
                "--remove-milestone", "--yes"}
GIT_GLOBAL_ARG_OPTS = {"-C", "-c"}
GIT_GLOBAL_BOOL_OPTS = {"--no-pager", "-P", "--no-replace-objects", "--bare",
                        "--literal-pathspecs", "--no-optional-locks"}
GIT_TEXT_OPTS = {"-m", "--message"}
GIT_ARG_OPTS = {"-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
                "-t", "--template", "--author", "--date", "--cleanup", "--fixup",
                "--squash", "--trailer", "--pathspec-from-file"}
GIT_BOOL_OPTS = {"-a", "--all", "-s", "--signoff", "-v", "--verbose", "-q",
                 "--quiet", "-n", "--no-verify", "--amend", "--no-edit", "-e",
                 "--edit", "--allow-empty", "--allow-empty-message", "-p", "--patch",
                 "-i", "--include", "-o", "--only", "--dry-run", "--reset-author",
                 "--no-status", "--status", "--no-gpg-sign", "--no-post-rewrite"}
GIT_BOOL_SHORT = "asvqneiop"        # short no-arg flags that may cluster before -m
REDIRECT_OP_RE = re.compile(r"^\d*(?:>>?|<<?<?|>&|<&|&>>?|>\|)$")


def _skip_redirect(tk, i):
    """Index past a redirection operator token and its target, or None."""
    if REDIRECT_OP_RE.match(tk[i]):
        return i + 2
    return None


def _scan_text_options(tk, i, text_opts, arg_opts, bool_opts, short_cluster=""):
    """Indices of the VALUE tokens of `text_opts` in tk[i:] (see PROSE above)."""
    ok = set()
    while i < len(tk):
        t = tk[i]
        nxt = _skip_redirect(tk, i)
        if nxt is not None:
            i = nxt                        # its target is never accounted here
            continue
        if t == "--":
            break
        if not t.startswith("-") or t == "-":
            i += 1                         # a positional: never accounted
            continue
        key = t.partition("=")[0] if t.startswith("--") else t
        if key in text_opts and "=" in t and t.startswith("--"):
            ok.add(i)
            i += 1
        elif key in text_opts:
            if i + 1 < len(tk):
                ok.add(i + 1)
            i += 2
        elif (not t.startswith("--") and len(t) > 2 and t[:2] in text_opts):
            ok.add(i)                      # `-mTEXT` / `-bTEXT`: attached value
            i += 1
        elif (short_cluster and not t.startswith("--") and len(t) > 2
              and t[-1] == "m" and all(c in short_cluster for c in t[1:-1])):
            if i + 1 < len(tk):
                ok.add(i + 1)              # `-am TEXT`
            i += 2
        elif (short_cluster and not t.startswith("--") and len(t) > 2
              and all(c in short_cluster for c in t[1:])):
            i += 1                         # `-sq`: known no-arg flags only
        elif key in arg_opts:
            i += 1 if "=" in t else 2      # its value is NOT text
        elif key in bool_opts:
            i += 1
        else:
            break                          # unknown: stop, deny what follows
    return ok


def _prose_options_start(tk, start, head):
    """Index where a gh/git prose command's own options begin, or None when
    tk[start:] is not a command of the PROSE table (see above)."""
    if head == "gh":
        return start + 3 if tuple(tk[start + 1:start + 3]) in GH_PROSE_SUBCMDS else None
    if head != "git":
        return None
    i = start + 1
    while i < len(tk) and tk[i].startswith("-"):
        key = tk[i].partition("=")[0]
        if tk[i] in GIT_GLOBAL_ARG_OPTS:
            i += 2
        elif tk[i] in GIT_GLOBAL_BOOL_OPTS or key in ("--git-dir", "--work-tree"):
            i += 1
        else:
            return None
    return i + 1 if i < len(tk) and tk[i] == "commit" else None


def _prose_args(tk, start, head):
    """Text-option VALUE indices of a gh/git prose command (see PROSE above)."""
    i = _prose_options_start(tk, start, head)
    if i is None:
        return set()
    if head == "gh":
        return _scan_text_options(tk, i, GH_TEXT_OPTS, GH_ARG_OPTS, GH_BOOL_OPTS)
    return _scan_text_options(tk, i, GIT_TEXT_OPTS, GIT_ARG_OPTS, GIT_BOOL_OPTS,
                              GIT_BOOL_SHORT)


def is_prose_command(segment):
    """A BARE-name, assignment-free command of the PROSE table — the slice-3
    pipe source (`gh issue comment N --body … | tail -1`). A path-named
    `/tmp/x/gh` or a `PATH=/tmp/x gh` could be anything, exactly like a
    path-named text filter (see INSPECT_SINKS below)."""
    try:
        tk = shlex.split(segment)
    except ValueError:
        return False
    if not tk or _cmd_start(tk) != 0 or tk[0] not in ("gh", "git"):
        return False
    return _prose_options_start(tk, 0, tk[0]) is not None


def _prose_token_is_quoted(segment, token):
    """True when `token` holds no `<`/`>` the shell would read as a redirect.

    shlex drops quotes, so `--body 'a <root>/<n>'` and `--body a>(root)/k` both
    come back as one token carrying `>`. A punctuation-aware tokenizer splits
    an UNQUOTED `<`/`>` into its own token and keeps a quoted one inside the
    word — so the token survives verbatim there only if its brackets were
    quoted text.
    """
    if "<" not in token and ">" not in token:
        return True
    try:
        lex = shlex.shlex(segment, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return token in list(lex)
    except ValueError:
        return False


# ssh-keygen's PUBLIC views: `-l` prints a fingerprint, `-y` the public half.
# Only these flags may appear (plus `-E <hash>`, `-q`); any other mode (`-p`
# rewrites, `-e` exports, `-P` puts a passphrase in argv, `-v` debugs) or an
# operand leaves the `-f` value unaccounted.
KEYGEN_VIEW_FLAGS = set("lyq")


def _keygen_args(tk, start):
    ok, modes, i = set(), set(), start + 1
    while i < len(tk):
        t = tk[i]
        nxt = _skip_redirect(tk, i)
        if nxt is not None:
            if t.endswith(">") and nxt - 1 < len(tk) and is_pub_path(tk[nxt - 1]):
                ok.add(nxt - 1)            # `-y -f k > k.pub`: public output
            i = nxt
            continue
        if not t.startswith("-") or t in ("-", "--"):
            return set()                   # an operand: not a view call
        j, i_next = 1, i + 1
        while j < len(t):
            ch = t[j]
            if ch in KEYGEN_VIEW_FLAGS:
                modes.add(ch)
                j += 1
                continue
            if ch in ("f", "E"):
                idx = i if j + 1 < len(t) else i + 1
                if idx >= len(tk):
                    return set()
                if ch == "f":
                    ok.add(idx)
                i_next = idx + 1
                break
            return set()                   # any other option
        i = i_next
    return ok if modes & {"l", "y"} else set()


# A `.pub` file is public material — but its NAME is one `${f%.pub}` away
# from the private key beside it (review A: `for f in <root>/*.pub; do cat
# "${f%.pub}"; done` read the key while the loop header was accounted). So:
# only a LITERAL single file (no glob `*`/`?`, no `$`/`` ` `` — word
# splitting turns `<root>/$X.pub` into `<root>/k` + `.pub` — no braces,
# brackets or `..`), and only as an operand of a known CONTENT reader: a head
# that uses the file's bytes, never its name (not `for`/`select`/`set`/
# `echo`/`awk`, which can bind or rewrite the name). Piped, even a content
# reader's name headers (`head a b`) are a name source, so only `cat` keeps
# it; a `< <path>` redirect feeds content, never a name, to any head. Review
# C: the heads are READ-ONLY ones (no `cp`/`sort -o`/`uniq IN OUT`/`xxd -r`
# that can WRITE a `.pub`), an option token (`--files0-from=<p>.pub`,
# `-o<p>.pub`) is never a path, and an output-redirect target is never
# accounted — a hand-written key file is refused like any other write.
PUB_PATH_RE = re.compile(r"^[A-Za-z0-9_.~/+@%,:=][A-Za-z0-9_.~/+@%,:=-]*/"
                         r"[A-Za-z0-9_.+@%,:=-]+\.pub$")
# (wc/sha256sum are metadata heads already, with their own option checks.)
PUB_HEADS = {"cat", "head", "tail", "cut", "grep", "diff", "cmp",
             "ssh-copy-id", "nl", "tac", "fold", "base64", "od"}
PUB_FLOW_HEADS = {"cat"}
OUT_REDIRECT_RE = re.compile(r"^\d*(?:>>?|>\||&>>?)$")


def is_pub_path(tok):
    """A literal path whose final component is a `*.pub` public-key file."""
    if not PUB_PATH_RE.match(tok or ""):
        return False
    return ".." not in tok.split("/")


def _pub_args(tk, start, head, term):
    ok = {i for i in range(start + 1, len(tk))
          if tk[i - 1] in ("<", "0<") and is_pub_path(tk[i])}
    if head in (PUB_FLOW_HEADS if term == "|" else PUB_HEADS):
        ok |= {i for i in range(start + 1, len(tk))
               if is_pub_path(tk[i]) and not OUT_REDIRECT_RE.match(tk[i - 1])}
    return ok


# Piped `secret inspect` (design change 1). Inspect never prints a value, but
# its `path:` line IS a name — `inspect <root>/k | sed -n 's/^path: //p' |
# xargs cat` reads the key. So a pipeline keeps inspect's exemption only when
# EVERY other command in it is a pure text filter that cannot open a name
# from its input, invoked by bare name with no assignment in front (a
# `PATH=/x head` or `/tmp/x/head` could be anything), and nothing in the
# command substitutes or groups (`$(…)`, backticks, `<(…)`, `{ …; }`).
#
# Slice 3 (#1153 issuecomment-5831733482): a PROSE command is the SAME kind of
# source — its output (a URL, `git commit`'s subject line) can echo its text,
# a NAME, but never a file's bytes — so it takes the same path and the same
# sinks. Being a source only restores its plain term: every root reference in
# it is still accounted token by token (`--body-file`/`-F`/`<` stay denied).
INSPECT_SINKS = {"head", "tail", "grep", "egrep", "fgrep", "wc", "sort", "uniq",
                 "cut", "tr", "column", "nl", "cat", "fold"}
FD_REMNANT_RE = re.compile(r"^\s*\d*-?\s*$")
# Sink options that turn the piped NAMES into a read (`--files0-from=-`
# opens every listed file; sort prints the content) or hand the stream to a
# program. GNU getopt takes ANY unambiguous abbreviation (`sort --fil=-`),
# so a token is refused when it is a prefix of one of these (slice 5).
NAME_READING_OPTS = {"wc": ("--files0-from",),
                     "sort": ("--files0-from", "--compress-program")}


def _reads_names(head, tok):
    key = tok.partition("=")[0]
    if tok.startswith("--files0-from"):
        return True
    return len(key) > 2 and any(opt.startswith(key)
                                for opt in NAME_READING_OPTS.get(head, ()))


# A recursive grep walks the working directory, whatever it was piped (slice-5
# review: `cd ~ && ls <root> | grep -d recurse PAT` printed a private key).
# GNU grep permutes, so an option anywhere before `--` counts; a short
# option that takes a value ends its cluster (`-e -r` is a pattern).
GREP_HEADS = {"grep", "egrep", "fgrep"}
GREP_VALUE_SHORT = "efmABCdD"
GREP_WALK_LONG = ("--recursive", "--dereference-recursive")


def _recurse_value(value):
    return bool(value) and "recurse".startswith(value)


def _grep_walks(args):
    i = 0
    while i < len(args):
        t = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if t == "--":
            return False
        if t.startswith("--"):
            key, eq, val = t.partition("=")
            if len(key) > 2 and any(o.startswith(key) for o in GREP_WALK_LONG):
                return True
            if (len(key) > 3 and "--directories".startswith(key)
                    and _recurse_value(val if eq else nxt)):
                return True
        elif t.startswith("-") and len(t) > 1:
            for j, ch in enumerate(t[1:], start=1):
                if ch in "rR":
                    return True
                if ch in GREP_VALUE_SHORT:
                    value = t[j + 1:] or nxt
                    if ch == "d" and _recurse_value(value):
                        return True
                    if not t[j + 1:]:
                        i += 1             # the value is the next token
                    break
        i += 1
    return False


def _is_text_sink(seg):
    try:
        tk = shlex.split(seg)
    except ValueError:
        return False
    if not tk or _cmd_start(tk) != 0 or "/" in tk[0]:
        return False
    if tk[0] not in INSPECT_SINKS:
        return False
    if tk[0] in GREP_HEADS and _grep_walks(tk[1:]):
        return False
    return not any(_reads_names(tk[0], t) for t in tk[1:])


# Slice 5 (#1153 issuecomment-5836170974): a metadata head prints NAMES and
# metadata, never a value — the same kind of source. Only `ls` and `stat`
# (`wc -c`/`sha256sum`/`test` stay unpiped: nobody pipes them). Its output is
# a CLEAN name list, the one thing a consumer needs to read the keys, so it
# may feed ONLY text filters: no second source may sit in its pipeline (`ls
# -d <root>/* | git commit --pathspec-from-file=-` would take the names).
META_SOURCES = {"ls", "stat"}


def is_meta_source(segment):
    """A bare-name, assignment-free `ls`/`stat` at command position (a
    `/tmp/x/ls` or `PATH=/x ls` could be anything, like a path-named sink)."""
    try:
        tk = shlex.split(segment)
    except ValueError:
        return False
    return bool(tk) and _cmd_start(tk) == 0 and tk[0] in META_SOURCES


def _is_inert_source(seg):
    return is_secret_inspect(seg) or is_prose_command(seg) or is_meta_source(seg)


def pipeline_is_inert(segments):
    """True when the command pipes a one-path `secret inspect`, a prose
    command, or a bare `ls`/`stat` only into text filters (see INSPECT_SINKS
    and META_SOURCES above).

    This decides ONLY the source's term; it is not the read check. A sink
    that names the root (`… | cat <root>/k`) still passes `_is_text_sink`
    here and is denied by its own `key_violation` pass, like every segment.
    """
    sources = [seg for seg, _t in segments if _is_inert_source(seg)]
    if not sources:
        return False
    if len(sources) > 1 and any(is_meta_source(seg) for seg in sources):
        return False
    prev = None
    for seg, term in segments:
        if term in ("$(", "`", "(", ")"):
            return False
        if not seg.strip() or _is_inert_source(seg):
            prev = (seg, term)
            continue
        # `2>&1` is split at its `&`: the `1` after a `>`/`<` is not a command
        if (prev and prev[1] == "&" and prev[0].rstrip().endswith((">", "<"))
                and FD_REMNANT_RE.match(seg)):
            prev = (seg, term)
            continue
        if not _is_text_sink(seg):
            return False
        prev = (seg, term)
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

    Mirrors argparse + cli_vault._secret_apply_remainder EXACTLY: argparse
    binds the optional NAME only to the token right after the action (and
    only when it does not start with `-`); everything after that is the
    REMAINDER, where the CLI consumes its own flags from the head, stops at
    `--`, and the first token that is not its flag starts the `exec` child.
    An int flag with a non-int value is NOT ours (the CLI breaks there).
    Only the VALUE of a path flag (--persist / --persist-map / --file) and
    inspect's NAME are accounted — a root reference in any other flag value
    or in a stray positional stays unaccounted (review A finding 1).

    Slice 2: `exec --file <path>` runs a child with NO name, so a mirror that
    took the first free token anywhere as the NAME would shift the child by
    one token (`exec --file <root>/a cat ls <root>/k` read as NAME=cat,
    child `ls <root>/k` — an allowed listing — while the CLI runs `cat ls …`).
    """
    action = tk[cli] if cli < len(tk) else ""
    ok, i = set(), cli + 1
    if i < len(tk) and not tk[i].startswith("-"):     # argparse's `name`
        if action == "inspect":
            ok.add(i)
        i += 1
    while i < len(tk):
        t = tk[i]
        key, eq, inline = t.partition("=")
        if t == "--":
            return ok, i + 1
        if t in SECRET_CLI_FLAGS:
            i += 1
            continue
        if key in SECRET_CLI_ARGFLAGS:
            if not eq and i + 1 >= len(tk):
                return ok, i         # a dangling flag: the CLI leaves it
            idx, value = (i, inline) if eq else (i + 1, tk[i + 1])
            if key in ("--ttl", "--keep", "--port") and not _is_int(value):
                return ok, i         # not a flag of ours after all
            if key in SECRET_PATH_FLAGS:
                ok.add(idx)
            i = idx + 1
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
    ok = _pub_args(tk, start, head, term)
    if head == "ssh-keygen":
        return ok | _keygen_args(tk, start)
    if head in ("gh", "git"):
        # Piped, a prose command's output can echo its text (`git commit`
        # prints the subject) into a consumer — a name source like `ls`.
        # Piped only into text filters it keeps a plain term instead
        # (effective_terms / pipeline_is_inert, slice 3).
        return ok if term == "|" else ok | _prose_args(tk, start, head)
    return ok | _accounted_head(tk, start, head, term)


def _accounted_head(tk, start, head, term):
    rest = range(start + 1, len(tk))
    if head in KEY_META_HEADS:
        # Piped, a metadata head is a NAME SOURCE for whatever consumes it
        # (`ls <root>/* | xargs cat`) — the store's review-F5 rule. Piped
        # only into text filters, `ls`/`stat` keep a plain term (slice 5).
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


def _unaccounted(tk, term, cwd_hint=None, segment=None):
    # A redirection glued into one word (`ls <root>/k><root>/j`) is a WRITE
    # riding on an allowed argument (review C finding 7): a token naming the
    # root that also carries `<`/`>` is never accounted — unless it is PROSE
    # whose brackets were quoted text (`--body 'a <root>/<name>'`, slice 2).
    prose = _prose_indices(tk, term)
    ok = {i for i in _accounted(tk, term)
          if ("<" not in tk[i] and ">" not in tk[i])
          or (i in prose and segment is not None
              and _prose_token_is_quoted(segment, tk[i]))}
    return [t for i, t in enumerate(tk) if i not in ok and key_ref(t, cwd_hint)]


def _prose_indices(tk, term):
    start = _cmd_start(tk)
    if start is None or term == "|":
        return set()
    return _prose_args(tk, start, tk[start].rsplit("/", 1)[-1].lower())


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


# Redirections that cannot write anywhere that matters: an fd dup (`2>&1`,
# and the `2>` a `&`-split leaves behind) or a discard to /dev/null. Any other
# redirect target stays a token, so `inspect <p> > <file>` is not one path.
SAFE_REDIRECT_RE = re.compile(r"^\d*(?:>&\d*-?|>|>>?/dev/null)$")


def is_secret_inspect(segment):
    """`… airuleset.py secret inspect <one path>` — metadata only, any root."""
    try:
        tk = [t for t in shlex.split(segment) if not SAFE_REDIRECT_RE.match(t)]
    except ValueError:
        return False
    start = _cmd_start(tk)
    cli = None if start is None else _secret_cli_start(tk, start)
    return cli is not None and tk[cli:cli + 1] == ["inspect"] and len(tk) == cli + 2


# `(` too (slice-4 review): a segment ending at the `(` of `>(…)` writes its
# output INTO the reader inside (`stat <root>/k > >(xargs cat)`).
FLOW_TERMS = {"|", "(", ")", "`"}
# Slice 4 (#1153 issuecomment-5832682152, ROZHODNUTÉ): "piped" is judged per
# PIPELINE. These terms end one; `&` ends one only as a real background
# operator (see _ends_pipeline). Any GROUPING keeps the whole-command cut:
# a group or compound command can carry one pipeline's output into another.
PIPELINE_BREAKS = {";", "&&", "||", "\n", ""}
GROUP_TERMS = {"(", ")", "$(", "`"}
COMPOUND_WORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until",
                  "do", "done", "case", "esac", "select", "coproc", "function"}
# A command that can change what a LATER command name means (an alias, a
# hashed path, a disabled builtin, PATH or another variable, sourced/evaled
# code, shell options, fd redirection for the rest of the shell). The
# whole-command cut judged `alias gh=cat; gh … | tail -1` denied because the
# alias was not a text filter; a per-pipeline view would lose that, so such a
# command keeps the whole-command rule too.
REBIND_HEADS = {"alias", "unalias", "hash", "enable", "source", ".", "eval",
                "export", "declare", "typeset", "readonly", "local", "set",
                "shopt", "exec", "trap"}
_LEAD_WORDS = {"!", "time", "-p", "command", "builtin"}


def _whole_command_only(segments):
    """True when the command groups/compounds anything or rebinds a name —
    then it stays ONE unit for pipe promotion (see above)."""
    for seg, term in segments:
        if term in GROUP_TERMS:
            return True
        try:
            tk = shlex.split(seg)
        except ValueError:
            return True               # unparseable: keep the conservative cut
        if "{" in tk or "}" in tk:
            return True
        i = 0
        while i < len(tk) and tk[i] in _LEAD_WORDS:
            i += 1
        rest = tk[i:]
        if rest and rest[0] in COMPOUND_WORDS:
            return True
        words = [t for t in rest if not ASSIGN_RE.match(t)]
        if rest and not words:
            return True               # a bare assignment (`PATH=/x`)
        if words and words[0].rsplit("/", 1)[-1] in REBIND_HEADS:
            return True
    return False


def _ends_pipeline(segments, k):
    """Does segment k's terminator end its pipeline?

    Not when the segment is empty (`a |\\n b`, `a |& b` — the separator right
    after a `|` continues the same pipeline), nor when the separator is
    escaped (`a \\; | b` is ONE pipeline whose argument is `;`). An `&` is a
    background operator only when it is not part of a redirection: not the
    `&` of `2>&1`/`>&2` (the segment ends in `>`/`<`), not the `&` of
    `&>`/`&>>` (the next segment starts with `>`).
    """
    seg, term = segments[k]
    if not seg.strip():
        return False
    trail = len(seg) - len(seg.rstrip("\\"))
    if trail % 2:
        return False
    if term == "&":
        nxt = segments[k + 1][0] if k + 1 < len(segments) else ""
        return not (seg.rstrip().endswith((">", "<")) or nxt.startswith(">"))
    return term in PIPELINE_BREAKS


def _pipelines(segments):
    """`segments` cut into pipelines, in order (see _ends_pipeline)."""
    out, cur = [], []
    for k, item in enumerate(segments):
        cur.append(item)
        if _ends_pipeline(segments, k):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _pipeline_terms(segments):
    """The pre-slice-4 promotion, applied to one unit (a pipeline, or the
    whole command when it groups anything)."""
    piped = any(term == "|" for _seg, term in segments)
    # Slice 2/3: a one-path `secret inspect` or a prose command piped ONLY
    # into text filters is not a flow (pipeline_is_inert) — its segment keeps
    # a plain term.
    inert = piped and pipeline_is_inert(segments)
    return [(seg, "" if (inert and _is_inert_source(seg))
             else "|" if (piped or term in FLOW_TERMS) else term)
            for seg, term in segments]


def effective_terms(segments):
    """Each segment's terminator, promoted to `|` when its output can FLOW.

    Review C finding 1: `|` alone missed `cat $(ls -d <root>/*)` (the inner
    segment ends at `)`), a backtick, `<(…)`, and a GROUP that is piped
    (`{ ls <root>/*; } | xargs cat`, `for …; do ls …; done | …`). A segment
    ending in `)`/backtick is inside a substitution or subshell.

    Slice 4: without any grouping, a pipe's data cannot leave its own
    pipeline, so each pipeline is promoted on its own — `cd X && gh … --body
    "<prose>" | tail -1` keeps the prose table. With grouping the whole
    command stays one unit: when it pipes ANYWHERE, no listing in it is
    trusted (a stateless text check cannot follow a group).
    """
    if _whole_command_only(segments):
        return _pipeline_terms(segments)
    return [item for pl in _pipelines(segments) for item in _pipeline_terms(pl)]


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
    stray = _unaccounted(tk, term, cwd_hint, segment)
    if stray:
        return stray[0]
    # The segment names the root but no single argument does (a reference
    # only an expansion across tokens produces): nothing accounts for it.
    if not any(key_ref(t, cwd_hint) for t in tk):
        return ref
    return None
