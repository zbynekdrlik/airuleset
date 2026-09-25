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
