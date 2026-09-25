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
# TOOL long before it reaches for `cat`, so Read/Grep/Glob/Write/Edit are all
# matched too (by EXACT tool name, one settings entry each — an alternation
# matcher has been observed in this repo to silently never match, and a guard
# that never runs is worse than none because it reads as coverage). For those
# the inspected fields are file_path / notebook_path / path / glob, plus
# `pattern` for Glob ONLY: Grep's pattern is a regex to search FOR, and
# treating it as a path would block searching this repo for the guard's own
# subject matter. Write and Edit joined this list at #154: a `<name>.template`
# command-lock file (filedrop/vault.py) lives in this SAME directory, and an
# agent's reflexive Write/Edit against it needs the identical refusal a value
# file already had — no Python change was needed here, only the
# settings/hooks.json wiring, since Write/Edit's own `tool_input` shape
# (`{"file_path": ..., ...}`) already matches the fields this branch scans.
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
# SECOND ROOT — plain key files under `~/.secrets/` (#1153, rule E in the
# matcher). odoo-erp 8236: a stream lane printed a PROD API key into its
# transcript twice in one hour with an inline "format check" (`cat`, `head`,
# `python3 -c 'print(open(...))'`) of a legacy plain key file — a root this
# hook did not know. Same engine, same deny-by-default, same #156 resolution
# (globs anchored on a 3-char literal prefix, braces, `.`/`..` noise, an
# in-command `cd`), plus a quote-removed pass so `.secret"s"` is seen. Any
# `.secrets` path COMPONENT counts, wherever it sits. The one difference: this
# root also holds the fleet's SSH keys (2,279 of 2,532 real commands naming it
# on the controller were `ssh -i`), so the accounting is per ARGUMENT, not per
# segment: every token naming the root must be (a) the identity-file argument
# of ssh/scp/sftp (`-i`, `-oIdentityFile=`), or an rsync `-e`/`--rsh` value
# that is itself such an ssh call; (b) an operand of an UNPIPED metadata head
# (ls, stat, test, [, wc without --files0-from, sha256sum without -c); or (c)
# a pre-child argument of `airuleset.py secret` (inspect/exec --persist/
# request --persist/show --file) — a `secret exec` CHILD is accounted like a
# command of its own, since the CLI filters only the vault value it injects.
# One unaccounted reference denies (`ssh -i <root>/k h 'cat <root>/x'`). A
# script invoked by path is allowed because its text never names the file —
# the sanctioned way stream code consumes a key. Measured side effects of
# deny-by-default here, on purpose: `for f in <root>/*`, `export K=$(tr … <
# <root>/k)` and `mkdir`/`chmod` on the root are refused like any other
# unlisted head. The refusal names `secret inspect <path>` (format,
# hooks-free) and `secret exec [--file <path>]`/a script (use).
# SLICE 2 (issuecomment-5830067896) — five more accounted shapes, each an
# explicit table with a DENY twin in tests/test_vault_guard_slice2_1153.py:
# (d) a one-path `secret inspect` piped ONLY into pure text filters
# (head/tail/grep/wc/sort/uniq/cut/tr/column/nl/cat/fold, by bare name, no
# `--files0-from`, no substitution/group anywhere) — its `path:` line is a
# name, so `| xargs cat` / `| sh` / `| awk` stay refused; (e) PROSE: the
# VALUE of `gh issue|pr comment|create|edit --body/-b/--title/-t` and of
# `git commit -m/--message`, unpiped, found by an option scan that knows
# every other option of those commands (an unknown option or `--` ends it;
# `--body-file`, `-F`, `gh api -F body=@`, `git commit -F`, a redirect and a
# substitution stay refused; brackets count as text only when quoted); (f)
# the `-f` value of `ssh-keygen` in a pure `-l`/`-y` (+`-E`, `-q`) call —
# fingerprint and public half only; (g) a LITERAL single `*.pub` path (no
# glob, `$`, braces, brackets or `..`) as the operand of a known CONTENT
# reader (cat/head/tail/cut/grep/wc/diff/cp/ssh-copy-id/…) or a `<` redirect
# target — never of a head that binds or rewrites the NAME, which is one
# `${f%.pub}` from the private key (review A: `for f in <root>/*.pub; do cat
# "${f%.pub}"; done`); piped, only as a `cat` operand; and
# (h) `secret exec --file <path>`, whose CLI mirror now follows argparse
# exactly (NAME only right after the action, so the child is never shifted).
# Rule E's own gaps are the store's: a glob not anchored on 3 literal chars
# (`~/.s*/k`), a computed path, a home-wide sweep (`grep -r x ~`) that never
# names the root, a symlink to a key under another name, a `*.pub` that is
# itself a symlink to the private key (the `.pub` test is textual), and a
# listing or commit subject written to a FILE and read back by a later
# segment (only pipes/substitutions are followed). Its accepted FALSE
# POSITIVE is wider than the store's, stated rather than implied: `.secrets`
# is ordinary prose, so `echo "my .secrets"`, a heredoc body or any text
# option outside the (e) table naming the dir is refused (remedy: a body
# file outside the root, `-F <file>`). The
# recursive checks (ssh remote command, ProxyCommand, rsync -e, `secret exec`
# child) have no explicit depth cap; a pathological nesting ends in a
# RecursionError, i.e. rc 1, i.e. fail_closed — a refusal, never an allow.
# Each segment is now scanned twice (raw + quote-removed); measured worst
# case at the ~128 KB argv ceiling ~2 s, inside the 5 s budget.
# A metadata head's output is a NAME SOURCE whenever it can flow into
# another command — a pipe anywhere in the command, `$(…)`, backticks,
# `<(…)`, a subshell — so for BOTH roots the metadata exemption holds only
# when nothing can consume its output (review C, `cat $(ls -d <root>/*)`).
# The secret-CLI allowance trusts any file NAMED `airuleset.py`: planting a
# lookalike is the same deliberate, pre-authored step as a reader script run
# by path, and stays outside this hook's reflex claim.
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
#   - AUTHORING THEN RUNNING is STILL out of scope for CONTENT, even though a
#     `Write` matcher IS now registered (#154, see NOT ONLY BASH above). This
#     bullet used to say "no `Write` matcher is registered" — no longer true —
#     but the REASON that decision was written down still holds for what
#     actually matters: the Write/Edit branch inspects the WRITE TARGET
#     (`file_path`) only, never `content`. `Write` a NEW file at an ordinary
#     path (`/tmp/reader.py`) whose CONTENT reads the store, then `bash` it:
#     both halves are still allowed, because neither the file's own path nor
#     the bash command that runs it names the store — a script whose path is
#     assembled at runtime defeats a content match anyway, the same limit
#     already stated for computed paths. This is NOT the concern #156
#     rejected a Write matcher over: that concern was about a
#     CONTENT-SCANNING design, which would have blocked editing this hook and
#     its own tests (their SOURCE TEXT necessarily contains the store path as
#     a string literal) and would have destroyed the documented remedy for
#     the accepted false positive below ("write the body to a file with the
#     Write tool"). The #154 design scans `file_path` only — the SAME field
#     the Read/Grep/Glob branch already scanned — so editing this hook, its
#     tests, or any ordinary file is unaffected regardless of what text they
#     contain. The honest scope, unchanged: this hook gates WHICH PATH is
#     read or written, never WHAT CONTENT passes through an unrelated path.
#   - TEMPLATE FILES (#154, `<name>.template`, filedrop/vault.py) get the
#     SAME protection as a value file for free — they live in this same
#     directory, so rule A already matches them regardless of extension
#     (VALUE_FILE_RE, rule B, was never extended to `.template` — it did not
#     need to be). The WRITE side of that lock is deliberately NOT this
#     hook's job: filedrop/vault.py ships no write_template()/
#     set_template() function at all, on purpose (a computed
#     module.function() call names no literal path this hook could ever
#     see) — the only route this hook needs to close is the reflexive one
#     (cat, Write, Edit against the literal path), which it now does.
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
#   - VALUE_FILE_RE's config-extension allow-list (`.json`/`.yaml`/`.yml`/
#     `.env`/`.toml`/`.ini`, #165) accepts `.env` DELIBERATELY, even though a
#     `.env` file conventionally carries raw secrets itself. Rule B is a
#     supplementary net for THIS vault's own value-file extension (the real
#     store files are `~/.claude/secrets/<NAME>.secret`, matched by the
#     TERMINAL case, never by an infix) — a file whose real extension is
#     `.env` was never protected by rule B before this ticket either (only
#     the accidental `.secret` infix ever made it match), so nothing here
#     removes an existing protection for `.env` files in general; a real
#     `.env` secret this hook never covered before still isn't covered now
#     (verified live: `cat ~/.claude/channels/discord/.env`, which really
#     holds a bot token on this box, was rc 0 both before and after). An
#     adversarial review (agent a9a5c0191c3ffbc07) rated this
#     RISKY-BUT-ACCEPTABLE rather than a fix-required finding, on that exact
#     reasoning, and separately noted `.env` was the ticket's OWN requested
#     entry, not the implementer's addition.
#     What IS conceded, precisely (the review's real findings, F1/F2/F4/F7):
#     naming a copy of an actual value file with a listed extension AS THE
#     FINAL component (`cp DB_PASS.secret DB_PASS.secret.env`) slips this
#     by-name net — the same class of deliberate-circumvention gap already
#     stated above for the store directory (rule A still catches it while
#     the copy sits inside the store); a SYMLINK to the real store file
#     under an allow-listed name (`ln -s <store>/DB_PASS.secret ./x.secret.
#     env`) is the cheaper version of the same gap, and needs no data
#     duplication at all; a config-shaped name with an extra dot-segment
#     before the extension (`config.secret.local.json`) is a genuine,
#     plausible false positive that STAYS blocked, because the allow-list
#     match requires the extension immediately after `.secret`; the match
#     is case-SENSITIVE (`config.secret.JSON` stays blocked); and a
#     non-ASCII byte immediately after a listed extension (`config.secret.
#     jsonä`) is read as a genuine boundary, so that specific composition
#     is allowed — a deliberately crafted filename, not a shape ordinary
#     tooling produces. None of these are silent leaks: every one requires
#     either an already-blocked authoring step (the copy/symlink/`echo`)
#     or a hand-crafted filename nobody types by reflex — the same REFLEX
#     boundary this hook's whole design already stakes its claim on.
#   - Fail-closed is bounded by the harness: a hook that TIMES OUT (5s) is
#     treated as an error and does not block, so a pathologically slow python3
#     start fails open. This gap is TRACKED as its own ticket (#162), across
#     two rounds. Round 1 found and closed the then-DOMINANT reachable
#     trigger: an O(n^2) regex sweep (VALUE_FILE_RE, see its own comment
#     above) that a 50KB ordinary long argument — no glob, no exploit shape
#     — drove past the 5s budget on its own. A fresh-context adversarial
#     review of round 1 then found the IDENTICAL overlapping-class-vs-
#     literal-suffix shape, unbounded, in two more of this file's own
#     regexes — BRACE_RE (`expand_braces` below) and RECURSIVE_RE
#     (`sweeps_the_parent` below) — and proved BRACE_RE was reachable at a
#     MUCH smaller size (a 4KB unclosed brace already blew the budget end
#     to end, since `expand_braces` runs on every token of every command,
#     not only the ones VALUE_FILE_RE/CLAUDE_ROOT_RE miss) and amplified by
#     the function's own recursion. Round 2 bounded both the same way (see
#     their own comments). Measured on dev1 post-round-2: the whole hook
#     runs in single-digit-to-low-double-digit ms for an ordinary command.
#     The worst adversarial constructions found — many repeated near-miss
#     segments, sized to the ~128KB argv ceiling this hook's own E2BIG
#     fail-closed path already caps commands at — are NOT sub-second (an
#     earlier draft of this comment claimed "well under a second" for
#     VALUE_FILE_RE alone and an adversarial review measured that false at
#     100-120KB): BRACE_RE's and RECURSIVE_RE's own worst cases at the true
#     ~128KB ceiling measured ~1.0-1.3s each, and VALUE_FILE_RE's own
#     worst case (a single stem-legal run with no `.secret` in reach) up to
#     ~2.3s — all comfortably, but not overwhelmingly, under the 5000ms
#     budget (2-5x margin, not the 100x+ margin an ordinary command gets).
#     What remains, and genuinely is not
#     agent-controllable: a pathologically slow python3 interpreter START
#     under extreme system load (swap, CPU starvation). That residual is
#     real — but this file's own round-1-to-round-2 history is precisely
#     why "now the WHOLE of the exposure" is a claim that needs re-earning
#     by a fresh review each time, not asserting from inside the diff that
#     makes it: every regex in this file has now been checked once for this
#     specific shape, and the two found were fixed, but that is a snapshot
#     of what is KNOWN, not a proof that no reachable trigger remains.
#   - VALUE_FILE_RE's stem bound ({0,253}, #162 round 2) has ONE narrow false
#     negative, found by adversarial review and reproduced against the real
#     hook: a `<stem>.secret` reference whose stem's final 254 characters
#     before `.secret` contain NO alnum/underscore character at all (e.g.
#     300 dashes then `.secret`) is NOT matched, because the required
#     leading `[A-Za-z0-9_]` has nowhere to anchor within the bounded
#     window — the unbounded original always found an earlier alnum char no
#     matter how far back. Accepted rather than fixed further: no stem
#     shaped this way can name a real value file anyway (it already exceeds
#     Linux's own NAME_MAX), so nothing a real file on disk could be called
#     goes unblocked by it. See VALUE_FILE_RE's own comment for the exact
#     bound and reasoning.
#   - RECURSIVE_RE's two `{0,254}` bounds (#162 round 2) have the SAME class
#     of narrow residual, found while writing this round's own regression
#     tests: a homogeneous run of `r`/`R` characters immediately followed by
#     whitespace matches only up to 509 total characters after the leading
#     `-` (254 + the required `[rR]` + 254) — the unbounded original had no
#     such ceiling. Past 509, `sweeps_the_parent`'s recursive-flag detector
#     stops firing for a head outside BULK_HEADS/TREE_WALK_HEADS, so a
#     deliberately-obfuscated flag cluster of 510+ repeated `r` characters
#     evades the recursive-sweep check. Accepted rather than widened: no
#     real flag cluster typed by reflex — or even deliberately, short of
#     this exact obfuscation — is remotely that long. See RECURSIVE_RE's
#     own comment for the exact bound.
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

# The payload travels in ARGV, never on stdin. It did so first because the
# matcher used to be a heredoc that WAS python's stdin; it stays so because
# this wrapper has already drained stdin above, and because an argv too big
# to exec (E2BIG) makes python3 fail to start — rc != 0/2 — which lands in
# fail_closed below rather than in a silent allow.
# The matcher lives in a sibling file (#1153 — moved verbatim out of a
# heredoc). Resolved with builtins only, so a broken PATH still reaches the
# fail-closed branch below instead of silently running nothing. A MISSING
# matcher must fail closed here: `python3 <absent file>` exits 2, which
# the code below would otherwise read as a real hit and let the env bypass
# turn into an allow.
HOOK_SRC="${BASH_SOURCE[0]}"
case "$HOOK_SRC" in
    */*) MATCHER="${HOOK_SRC%/*}/vault_read_guard.py" ;;
    *)   MATCHER="./vault_read_guard.py" ;;
esac
[ -f "$MATCHER" ] && [ -r "$MATCHER" ] \
    || fail_closed "the matcher $MATCHER is missing or unreadable."

VIOLATION=$(python3 "$MATCHER" "$PAYLOAD") && RC=0 || RC=$?

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
ROOTS=$(printf '%s\n' "$VIOLATION" | grep '^#ROOTS# ' | tail -1 || true)
ROOTS=${ROOTS#\#ROOTS\# }
VIOLATION=$(printf '%s\n' "$VIOLATION" | grep -v '^#AUDIT# \|^#ROOTS# ' || true)

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

# Which root(s) the matcher hit decides the guidance (#1153): the store is
# used through `secret exec <NAME>`, a plain key file through `secret inspect`
# (format) and a key argument or a script (use). A missing #ROOTS# line reads
# as the store, the only root a pre-#1153 matcher knew.
case ",$ROOTS," in *,keyfile,*) KEYFILE=1 ;; *) KEYFILE=0 ;; esac
case ",$ROOTS," in *,store,*|,,) STORE=1 ;; *) STORE=0 ;; esac

echo "" >&2
if [ "$KEYFILE" = 1 ] && [ "$STORE" = 0 ]; then
    echo "🚫 BLOCKED: a plain key file under ~/.secrets/ is not read (or written) by hand." >&2
else
    echo "🚫 BLOCKED: the credential store is not read (or written) by hand." >&2
fi
echo "" >&2
echo "$VIOLATION" >&2
echo "" >&2
echo "  A value read this way lands in the session transcript, survives" >&2
echo "  compaction, and cannot be revoked — the exact leak the credential" >&2
echo "  channel exists to prevent." >&2
echo "" >&2
if [ "$STORE" = 1 ]; then
    echo "  Use a STORED value WITHOUT seeing it:" >&2
    echo "    python3 ~/devel/airuleset/airuleset.py secret exec <NAME> -- <cmd>" >&2
    echo "  It hands the value to the child through the environment (or --stdin)," >&2
    echo "  captures fd 1/2 and filters the value out of them." >&2
    echo "" >&2
    echo "  Metadata, without the value:  secret list  /  secret status <NAME>  /  secret inspect <path>" >&2
    echo "  Remove it:                    secret forget <NAME>" >&2
    echo "  Get a NEW value from the user: secret request <NAME>  (never ask in chat)" >&2
    echo "" >&2
fi
if [ "$KEYFILE" = 1 ]; then
    echo "  A plain key file (~/.secrets/<name>):" >&2
    echo "    check its FORMAT (bytes, lines, trailing newline, NAME= names, hash," >&2
    echo "    owner, mode) without the value:" >&2
    echo "      python3 ~/devel/airuleset/airuleset.py secret inspect <path>" >&2
    echo "    (piped only into a text filter: ... 2>&1 | head)" >&2
    echo "    USE it without printing it: as a key argument (ssh/scp/sftp -i <path>," >&2
    echo "    rsync -e 'ssh -i <path>'), from a script invoked by path, or inline:" >&2
    echo "      python3 ~/devel/airuleset/airuleset.py secret exec --file <path> --env KEY -- <cmd>" >&2
    echo "    (the value reaches the child as \$KEY, or on stdin with --stdin; fd 1/2" >&2
    echo "    are filtered). Public material: ssh-keygen -l|-y -f <key>, a *.pub file." >&2
    echo "    Metadata heads, unpiped: ls / stat / test / wc -c / sha256sum." >&2
    echo "    Naming a path in PROSE: gh issue|pr … --body/--title, git commit -m." >&2
    echo "" >&2
fi
echo "  HONEST LIMIT: this is a GUARDRAIL, not a security boundary. The agent's" >&2
echo "  uid holds NOPASSWD sudo on these boxes, so no store location is beyond" >&2
echo "  its reach; what this guarantees is that the unsafe path is refused by" >&2
echo "  default and that circumventing it leaves an artifact." >&2
echo "" >&2
echo "  Bypass (user-instructed only, logged): AIRULESET_ALLOW_VAULT_READ=1." >&2
echo "  There is deliberately no inline marker — see the hook's header." >&2
echo "" >&2
exit 2
