r"""Client Odoo Discuss thread-NAME compliance detection for sub-dev streams
(#596 + #597). The importable core `hooks/block-discuss-thread-name.sh` calls.

THE PROBLEM. A sub-dev stream (montaluN / davidN / simapN / mivaN) that creates
a client Odoo `discuss.channel` (a top-level channel or a `parent_channel_id`
sub-thread) MUST name it so the name:
  (#596) ENDS with the stream's number as a trailing standalone token ("... N"),
         so the owner sees at a glance which subdev owns the thread; and
  (#597) is <= MAX_NAME_LEN CHARACTERS including that number, so the name does
         not get truncated behind the Odoo Discuss sidebar's first page (which
         hides the very number #596 exists to show).

Live incident (montalu2, 2026-08-20, PROD): created `discuss.channel 274`
"Viditeľnosť leadov pre obchodníkov" with no " 2" -- the SECOND such slip (the
owner had to hand-rename "Oprava filtra rozmerov" -> "... 2" on 15.8.). The prose
rule (`handover-compose.md` #532/#537/#598) failed twice; the owner escalated to
a hook. Rule-intake gate: mechanically checkable -> hook.

DESIGN NOTES (full rationale on issue #596's design comment):

  * CREATE DETECTION keys on the `create` METHOD applied to discuss.channel, so
    an UNRELATED Odoo op is never false-blocked (the ticket's explicit
    constraint). A CHANNEL create is signalled by any of: the RPC model+method
    ADJACENCY `"discuss.channel", "create"` (the exact execute_kw shape the
    odoo-discuss-xmlrpc recipe uses); the ORM `['discuss.channel']<.chain()>
    .create(` (a bounded run of `.sudo()`/`.with_context(...)` etc. allowed
    before `.create(`); or a JSON-RPC / call_kw body carrying BOTH
    `"model":"discuss.channel"` and `"method":"create"`. A `message_post`
    (posting to an EXISTING channel) and a `write` (a rename -- the name-
    correction path MUST stay possible, INCLUDING a re-parent write that sets
    `parent_channel_id`) invoke a DIFFERENT method and are silently allowed. A
    sub-thread create is still a `create`, so it is caught by its METHOD, not by
    the `parent_channel_id` field (keying on that field alone false-blocked a
    re-parent+rename write -- review MAJOR-B). The RPC adjacency (not a bare
    `"create"` anywhere) stops a `res.partner` create in the same script from
    being read as a CHANNEL create; a `"create_date"` / `create_uid` FIELD is
    not the quoted method string `"create"` either.

  * NAME EXTRACTION pulls every `"name"`/`'name'`/`name=` string literal
    (`_NAME_RE`), with a `(?<![\w.])` lookbehind so `partner_name` / `.name` /
    `display_name` / `create_uid` are NOT mistaken for the channel name.

  * COMPLIANCE (`is_compliant`): the name, NFC-normalized and stripped, must end
    with the stream number as a trailing standalone token (space-or-start before
    the number, so "... 22" for stream 2 is NOT compliant, nor a glued "X2") AND
    be <= MAX_NAME_LEN CHARACTERS (`len()` on NFC counts code points, so Slovak
    diacritics count as one char each -- never bytes).

  * DECISION (`evaluate`): a detected create is BLOCKED iff NO extracted name
    literal is compliant (the issue's literal spec). For the overwhelmingly
    common single-name create this is exactly "the channel name must comply". A
    create with NO literal name (a dynamically-computed name) is ALLOWED -- it
    cannot be checked, the fleet's standard unmeasurable->allow bias; the
    incident always used a literal name, so this residual never affects it.

  * NUMBER DERIVATION reuses `cli_aliases.stream_number` (never a second map);
    a non-stream user -> None -> the guard stays silent for them.

  * ACCEPTED RESIDUALS (documented, not chased): a create whose model OR name
    lives in a VARIABLE (`m="discuss.channel"; execute_kw(...,m,"create",...)`)
    is not detected. A name literal containing a quote char is truncated by the
    naive quote-span (`[^'"\n]*`) -- which can BOTH miss a bad name AND wrongly
    truncate a legit apostrophe name (`'John\\'s 2'` -> `John\\` -> blocked); the
    same low-cost tradeoff close_trigger.py takes. Escaped-quote JSON inside a
    shell string (`curl -d "{\\"model\\":\\"discuss.channel\\"...}"`) breaks the
    quote-anchored detectors (fail toward NOT blocking). A multi-name create
    where an UNRELATED name literal (a comment, a foreign-model create in the
    same script) is compliant MASKS a non-compliant channel name -- a false-
    negative accepted per the issue's literal "block iff no name compliant" spec
    (`any`, pinned by a test); the realistic single-name thread-create script the
    recipe produces is unaffected, and the alternative `all` would false-block a
    legit channel create merely because an unrelated name is non-compliant
    (the exact "must not false-block unrelated Odoo work" the ticket forbids).
    Content >= 128 KB (Linux MAX_ARG_STRLEN) makes the `python3 ... "$CONTENT"`
    argv exec E2BIG -> the hook fails OPEN (a bad create in a huge Write slips);
    the fleet's standard unmeasurable->allow bias. Each fails toward NOT blocking
    or is the incident's own literal shape.
"""
import re
import unicodedata
from collections import namedtuple

import cli_aliases

# #641 -- resolve a stream's OWNER (for the identity-aware signature word below)
# through notify.STREAM_NOTIFY_OWNER, the SAME single source that routes the
# stream's Discord notifications, reused via the already-exposed
# `notify.stream_redirect` (NEVER a second stream->owner map). Fail-safe: if
# notify is ever unimportable the owner resolution degrades to the user string
# itself, which falls through to the DEFAULT signature word -- the fleet's
# standard unmeasurable->allow/default bias, never a crash.
try:
    from notify import stream_redirect as _stream_redirect
except Exception:  # pragma: no cover - notify unimportable (partial checkout etc.)
    _stream_redirect = None

# ~30 chars INCLUDING the trailing stream number (owner directive, #597). Char
# count, never bytes. Owner's good example "Oprava filtra rozmerov 2" == 24;
# the rejected "Viditeľnosť leadov pre obchodníkov 2" == 36.
MAX_NAME_LEN = 30

BYPASS_MARKER = "airuleset:discuss-name-ok"

# RPC execute_kw model+method adjacency: "discuss.channel" , "create"
_RPC_CREATE_RE = re.compile(r"""['"]discuss\.channel['"]\s*,\s*['"]create['"]""")
# ORM: env['discuss.channel']<.chain()...>.create( -- a bounded run of
# `.method(...)` calls (`.sudo()`, `.with_context(...)`, `.with_user(...)`) is
# allowed between the `]` and `.create(`, since that chain is the near-universal
# odoo-shell create idiom (review MAJOR-A: the un-chained form missed `.sudo()`).
# The `[^()]*` inside each `.method(...)` is bounded by `)`, so it stays linear.
_ORM_CREATE_RE = re.compile(
    r"""['"]discuss\.channel['"]\s*\]\s*(?:\.\s*\w+\s*\([^()]*\)\s*)*\.\s*create\s*\(""")
# JSON-RPC / call_kw: "model": "discuss.channel" AND "method": "create" both
# present (review MAJOR-C: the header claims JSON-RPC coverage; a call_kw body
# separates model and method by a `"method":`, so the RPC adjacency misses it).
# A write/message_post carries "method":"write"/"message_post", never "create",
# so this stays write-safe. Co-occurrence (not same-object) is the accepted
# residual (a batch mixing a channel op + a foreign create is rare for a stream).
_JSONRPC_MODEL_RE = re.compile(r"""['"]model['"]\s*:\s*['"]discuss\.channel['"]""")
_JSONRPC_CREATE_RE = re.compile(r"""['"]method['"]\s*:\s*['"]create['"]""")
# a `name` KEY (JSON/dict/kwarg) whose value is a string literal, NOT
# partner_name / .name / display_name / create_uid (lookbehind rejects a
# preceding word char or dot).
_NAME_RE = re.compile(r"""(?<![\w.])['"]?name['"]?\s*[:=]\s*(['"])([^'"\n]*)\1""")

Violation = namedtuple("Violation", "number names suggestion")


def is_channel_create(content):
    """True iff `content` invokes the `create` METHOD on `discuss.channel`
    (top-level channel OR a `parent_channel_id` sub-thread -- a sub-thread is
    still a `create`, so it is caught by its method, not by the field). A
    `message_post` / a `write` (a rename, INCLUDING a re-parent write that sets
    `parent_channel_id`) invokes a DIFFERENT method and returns False -- the
    fail-safe direction is to gate ONLY a create, never an unrelated Odoo op
    (review MAJOR-B: keying on the `parent_channel_id` FIELD alone false-blocked
    a re-parent+rename write; the create is keyed on its METHOD instead)."""
    if not content:
        return False
    if _RPC_CREATE_RE.search(content):
        return True
    if _ORM_CREATE_RE.search(content):
        return True
    if _JSONRPC_MODEL_RE.search(content) and _JSONRPC_CREATE_RE.search(content):
        return True
    return False


def channel_names(content):
    """Every `name` string literal in `content` (channel-name candidates)."""
    return [m.group(2) for m in _NAME_RE.finditer(content or "")]


def is_compliant(name, number, max_len=MAX_NAME_LEN):
    """True iff `name` ends with `number` as a trailing standalone token AND is
    <= `max_len` CHARACTERS (NFC code points, never bytes)."""
    nm = unicodedata.normalize("NFC", name or "").strip()
    if len(nm) > max_len:
        return False
    return bool(re.search(r"(?:^|\s)" + re.escape(number) + r"$", nm))


def suggest_name(name, number, max_len=MAX_NAME_LEN):
    """A compliant suggestion built from `name`: drop any existing trailing
    number, truncate the base to fit, re-append ` <number>`."""
    nm = unicodedata.normalize("NFC", name or "").strip()
    base = re.sub(r"\s*\d+\s*$", "", nm).rstrip()
    suffix = " " + number
    budget = max(0, max_len - len(suffix))
    if len(base) > budget:
        base = base[:budget].rstrip()
    return (base + suffix).strip()


def has_bypass_marker(content):
    """True iff the deliberate `airuleset:discuss-name-ok` bypass marker appears
    in `content` (rare, logged by the hook)."""
    return BYPASS_MARKER in (content or "")


def evaluate(content, user):
    """A `Violation` (number, offending names, suggestion) iff `content` is a
    discuss.channel create by a stream `user` (cli_aliases.stream_number) whose
    channel name(s) are ALL non-compliant; None (silent) otherwise -- a non-
    stream user, a non-create op, a create with no literal name, or a create
    with at least one compliant name."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_create(content):
        return None
    names = channel_names(content)
    if not names:
        return None
    if any(is_compliant(nm, number) for nm in names):
        return None
    offending = [nm for nm in names if not is_compliant(nm, number)]
    worst = max(offending, key=len)
    return Violation(number=number, names=offending,
                     suggestion=suggest_name(worst, number))


# --------------------------------------------------------------------------- #
# #609 -- message_post STREAM-SIGNATURE gate.
#
# THE PROBLEM. The #598 rule "every sub-dev Odoo Discuss message ENDS with a
# `ZbynekAI <N>` signature line" landed only as PROSE in a situational-injected
# skill companion (handover-compose.md) -- undiscoverable from the surface a
# stream actually loads (montalu6 posted an UNSIGNED message working from
# odoo-erp's OWN `discuss-client-posting` skill, which neither carries nor
# points to the rule). Nothing MECHANICAL enforced it: the #596/#597 create
# guard EXEMPTS message_post entirely. This gate closes that hole fleet-wide by
# scanning the SAME Bash/Write/Edit content the create guard already sees, so it
# fires regardless of which skill/prose the stream read (rule-intake gate step 1:
# mechanically checkable -> hook).
#
# DETECTION keys on the message_post METHOD on discuss.channel (the #596 MAJOR-B
# lesson: method, never a field), mirroring the create detectors. A create /
# write / rename is a DIFFERENT method and is never touched here.
#
# COMPLIANCE. The mandatory signature is `<WORD> <N>` where N is the stream
# number and <WORD> is the stream's identity token -- IDENTITY-AWARE since #641:
# `MarekAI` for a marek-owned stream (montalu4, posting via Marek's own handover
# account "Marek AI - odovzdavky" -> compact "MarekAI", odoo-erp #3864), the
# default `ZbynekAI` for every other owner (zbynek, david, ...). The word is
# derived from the posting stream's OWNER (notify.STREAM_NOTIFY_OWNER, reused via
# notify.stream_redirect -- NEVER a second stream->owner map). Before #641 the
# word was hardcoded ZbynekAI, which BLOCKED a truthful `MarekAI <N>` post AND
# ACCEPTED the wrong-identity `ZbynekAI <N>` one (live PROD contradiction,
# montalu thread 291 msg 1731669); the identity-aware word keeps the enforcement
# intact in BOTH directions (the wrong identity blocks for a marek stream AND a
# zbynek stream). The message body over RPC is frequently a VARIABLE (`body=body_html`),
# so "the body ENDS with the signature" is not reliably provable from the tool-
# call content; the reliable, low-false-positive check is that a COMPLIANT
# `ZbynekAI <N>` token appears ANYWHERE in the posting content (whether in the
# body-building assignment or the call). This catches the incident (no signature
# at all) AND a wrong number; the exact last-line placement stays the prose
# rule's + review's job. Accepted residuals (documented, not chased, per #319):
#   * the body assembled in a SEPARATE statement from the model literal (a two-
#     statement ORM `chan = env['discuss.channel'].browse(cid); chan.message_post(`)
#     is not detected -- the same unmeasurable->allow bias the create ORM detector
#     takes; the RPC `execute_kw(..., "discuss.channel", "message_post", ...)`
#     form the recipe + incident use IS detected.
#   * a .md doc/template that mentions message_post + discuss.channel with no
#     `ZbynekAI <N>` would block for a stream user -- but a doc teaching the rule
#     carries the signature example, so it passes; a stream rarely edits such a
#     doc, and the fleet's unmeasurable->over-block direction is the safe one for
#     a quality gate.
#   * a RUNTIME-built signature (`f"ZbynekAI {n}"`, `"ZbynekAI "+str(n)`, a `%d`)
#     is OVER-blocked -- the signature literal is not visible in the tool-call
#     content. This is the fail-safe (over-block) direction, off the recipe (which
#     mandates a literal `ZbynekAI <N>` last line), and carries the
#     `airuleset:discuss-sig-ok` bypass.
#   * because the check is "a compliant `ZbynekAI <N>` appears ANYWHERE in the
#     content", a MULTI-OP single tool-call can mask an unsigned post: two
#     message_posts where only the first is signed, or a signed create/other op
#     bundled with an unsigned post, both slip. The realistic single-post shape
#     the recipe + incident produce is unaffected; this is the same
#     unmeasurable->allow bias the create gate's `any`-semantics residual takes.
# --------------------------------------------------------------------------- #

SIGNATURE_WORD = "ZbynekAI"

# #641 -- owner -> client-facing signature IDENTITY word. The body-signature
# token is the COMPACT form of the handover account's client-visible display
# name ("Marek AI - odovzdavky" -> "MarekAI", odoo-erp #3864; the shared
# "Zbynek AI - odovzdavky" account -> "ZbynekAI"), mirroring the existing
# ZbynekAI compaction, never invented. Only marek needs an own word today; every
# other owner (zbynek, david, ...) uses the DEFAULT SIGNATURE_WORD. This is NOT a
# second stream->owner map -- the stream->owner resolution is reused wholesale
# from notify.STREAM_NOTIFY_OWNER (via `_stream_owner`); this table only supplies
# the display token, a genuinely NEW datum that lives nowhere else.
_OWNER_SIGNATURE_WORD = {
    "marek": "MarekAI",
}


def _stream_owner(user):
    """The stream's OWNER (zbynek / marek / david / ...) for a unix `user`,
    resolved through notify.STREAM_NOTIFY_OWNER -- the SAME single source that
    routes the stream's Discord notifications (reused via notify.stream_redirect,
    never a second stream->owner map). Fail-safe: if notify is unimportable, or
    stream_redirect raises, the owner is the (stripped) user string itself, which
    falls through to the DEFAULT signature word in `signature_word` -- the
    fleet's standard unmeasurable->default bias, never a crash."""
    u = (user or "").strip()
    if _stream_redirect is None:
        return u
    try:
        return _stream_redirect(u)
    except Exception:  # pragma: no cover - stream_redirect is itself fail-safe
        return u


def signature_word(user):
    """The client-facing signature WORD for a stream `user`, derived from the
    stream's OWNER (`_stream_owner`, reusing notify.STREAM_NOTIFY_OWNER): a
    marek-owned stream (montalu4) signs "MarekAI"; every other owner
    (zbynek, david, ...) and any non-stream / unknown / empty user signs the
    default "ZbynekAI". The guard is silent for a non-stream user anyway
    (cli_aliases.stream_number is None), so the default there is harmless."""
    return _OWNER_SIGNATURE_WORD.get(_stream_owner(user), SIGNATURE_WORD)


SIG_BYPASS_MARKER = "airuleset:discuss-sig-ok"

# message_post on discuss.channel, mirroring the create detectors' three shapes.
# RPC execute_kw model+method adjacency: "discuss.channel" , "message_post"
_RPC_MSGPOST_RE = re.compile(
    r"""['"]discuss\.channel['"]\s*,\s*['"]message_post['"]""")
# ORM: <...>['discuss.channel']<.chain()...>.message_post( -- same bounded
# `.method(...)` chain the create detector allows (`.browse()`/`.sudo()`/...).
_ORM_MSGPOST_RE = re.compile(
    r"""['"]discuss\.channel['"]\s*\]\s*(?:\.\s*\w+\s*\([^()]*\)\s*)*\.\s*message_post\s*\(""")
# JSON-RPC / call_kw: "model":"discuss.channel" (reuses _JSONRPC_MODEL_RE) AND
# "method":"message_post" both present.
_JSONRPC_MSGPOST_RE = re.compile(r"""['"]method['"]\s*:\s*['"]message_post['"]""")

MessagePostViolation = namedtuple("MessagePostViolation", "number expected")


def is_channel_message_post(content):
    """True iff `content` invokes the `message_post` METHOD on `discuss.channel`
    (RPC adjacency, an ORM `['discuss.channel']<.chain()>.message_post(`, or a
    JSON-RPC model+method co-occurrence). A create / write / rename invokes a
    DIFFERENT method and returns False -- this gate touches ONLY a message_post,
    exactly as the create gate touches only a create."""
    if not content:
        return False
    if _RPC_MSGPOST_RE.search(content):
        return True
    if _ORM_MSGPOST_RE.search(content):
        return True
    if _JSONRPC_MODEL_RE.search(content) and _JSONRPC_MSGPOST_RE.search(content):
        return True
    return False


def signature_present(content, number, word=SIGNATURE_WORD):
    """True iff `content` carries the mandatory `<word> <number>` stream
    signature: the word (case-insensitive) then whitespace then the EXACT stream
    number as a standalone token (a trailing non-digit boundary, so `ZbynekAI 66`
    is NOT number 6 and the unsubstituted `<N>` placeholder is NOT a signature).
    `word` is the stream's IDENTITY token (#641; `signature_word(user)` derives it
    -- "MarekAI" for a marek stream, default "ZbynekAI"); it defaults to
    SIGNATURE_WORD so the 2-arg form is unchanged. `number` is a digit string from
    cli_aliases.stream_number, and `word` is one of a fixed set of literals, so
    re.escape keeps the regex linear regardless of input size."""
    if not content or not number:
        return False
    pat = re.compile(r"(?i)" + re.escape(word) + r"\s+"
                     + re.escape(number) + r"(?![0-9])")
    return bool(pat.search(content))


def has_sig_bypass_marker(content):
    """True iff the deliberate `airuleset:discuss-sig-ok` bypass marker appears
    in `content` (rare, logged by the hook) -- for a genuine internal / legacy
    channel post the owner accepts without the client signature."""
    return SIG_BYPASS_MARKER in (content or "")


def evaluate_message_post(content, user):
    """A `MessagePostViolation` (number, expected signature) iff `content` is a
    discuss.channel message_post by a stream `user` (cli_aliases.stream_number)
    whose body carries NO compliant `<word> <N>` signature; None (silent)
    otherwise -- a non-stream user, a non-message_post op, or a post that already
    carries the signature. #641: `<word>` is IDENTITY-AWARE, derived from the
    stream's OWNER via `signature_word(user)` -- "MarekAI <N>" for a marek stream
    (montalu4), default "ZbynekAI <N>" otherwise. The wrong identity therefore
    BLOCKS in both directions (a marek stream signing ZbynekAI, or a zbynek stream
    signing MarekAI, each fails the accept-regex for its own demanded word)."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_message_post(content):
        return None
    word = signature_word(user)
    if signature_present(content, number, word):
        return None
    return MessagePostViolation(number=number,
                                expected=word + " " + number)


# --------------------------------------------------------------------------- #
# #628 -- message_post OWNER-APPROVAL gate.
#
# THE PROBLEM (root cause, two halves). A stream posted a message into a LIVE
# client Discuss thread on PROD WITHOUT the owner approving the text (montalu6,
# 2026-08-22, thread 283); it was deleted within a minute but the bus push +
# notification had already reached the client -- irreversible. (1) The prose
# approval doctrine (handover-compose.md) was framed around HANDOVER threads and
# their creation, so a stream read it as not applying to a "work question in an
# existing thread". (2) Nothing MECHANICAL stopped the post: the #596/#597 create
# guard exempts message_post, and the #609 gate only checks the SIGNATURE -- so a
# SIGNED but UNAPPROVED body posts freely. The owner's ruling: approval applies to
# EVERY client-facing message, without exception. This gate closes hole (2),
# fleet-wide, on the SAME message_post content the #609 signature gate already
# scans (rule-intake gate step 1: mechanically checkable -> hook); the doctrine
# in handover-compose.md closes hole (1).
#
# THE EVIDENCE is a LOGGED, FALSIFIABLE CLAIM, never a bare assertion "it's
# approved" -- the exact model of Discuss-closed: / Self-service-checked:. The
# marker `airuleset:owner-approved <ref>` must carry a NON-EMPTY reference (the
# `\s+\S` tail): a bare `airuleset:owner-approved` with no reference is NOT
# accepted. The reference points at HOW/WHEN the owner approved this exact text;
# a reviewer/owner can falsify it (did the owner really approve?) the same way a
# faked Discuss-closed: message-id is falsifiable. Detection reuses
# `is_channel_message_post` (the 3 RPC/ORM/JSON-RPC shapes) + `stream_number`
# (non-stream user -> None -> silent) -- never a second detection or derivation.
# Fail-safe direction = OVER-block (a safety/quality gate), like the signature
# gate. The genuine internal / non-client post carries the
# `airuleset:discuss-approval-ok` bypass (rare, logged) -- the sibling of
# `airuleset:discuss-sig-ok`. Accepted residuals mirror the #609 gate: the marker
# built at RUNTIME (a variable) is over-blocked (fail-safe, bypassable); a
# multi-op tool-call could mask an unapproved post via another op's marker (the
# same unmeasurable->allow bias the create/signature gates take). A name/sig
# bypass does NOT waive approval -- the hook runs this check independently.
# --------------------------------------------------------------------------- #

APPROVAL_MARKER_WORD = "airuleset:owner-approved"

APPROVAL_BYPASS_MARKER = "airuleset:discuss-approval-ok"

# `airuleset:owner-approved <ref>`: the marker word then SAME-LINE horizontal
# whitespace then at least one non-whitespace char -- a real, NON-EMPTY reference
# on the marker's OWN line. `[^\S\r\n]` is horizontal whitespace only (space/tab,
# never \r/\n), so the reference cannot be satisfied by a LATER line's content:
# a bare marker on its own line (no reference) is NOT a match, even in the common
# multi-line script where the message_post call follows on a later line (#628
# review MAJOR: a `\s+\S` that spanned the newline let a bare, reference-less
# marker pass in exactly that shape, defeating the falsifiable-claim requirement
# -- never a bare assertion). `re.escape` keeps the pattern linear regardless of
# input size.
_APPROVAL_RE = re.compile(re.escape(APPROVAL_MARKER_WORD) + r"[^\S\r\n]+\S")

ApprovalViolation = namedtuple("ApprovalViolation", "number")


def approval_present(content):
    """True iff `content` carries the `airuleset:owner-approved <ref>` evidence
    marker WITH a non-empty reference after it. A bare `airuleset:owner-approved`
    (no reference) is NOT accepted -- the falsifiable-claim requirement."""
    if not content:
        return False
    return bool(_APPROVAL_RE.search(content))


def has_approval_bypass_marker(content):
    """True iff the deliberate `airuleset:discuss-approval-ok` bypass marker
    appears in `content` (rare, logged by the hook) -- for a genuine internal /
    non-client channel post that needs no owner approval."""
    return APPROVAL_BYPASS_MARKER in (content or "")


def evaluate_message_post_approval(content, user):
    """An `ApprovalViolation` (number) iff `content` is a discuss.channel
    message_post by a stream `user` (cli_aliases.stream_number) that carries NO
    `airuleset:owner-approved <ref>` evidence marker; None (silent) otherwise --
    a non-stream user, a non-message_post op, or a post that already carries the
    approval evidence. The `airuleset:discuss-approval-ok` bypass is handled by
    the hook (like the signature bypass), not here."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_message_post(content):
        return None
    if approval_present(content):
        return None
    return ApprovalViolation(number=number)
