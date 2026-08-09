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
