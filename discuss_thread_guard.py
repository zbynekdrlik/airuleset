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
# `<word> <N>` token appears ANYWHERE in the posting content (whether in the
# body-building assignment or the call), where `<word>` is the stream's IDENTITY
# word (#641: `MarekAI` for a marek stream, default `ZbynekAI`; the `ZbynekAI`
# examples in the residuals below are the DEFAULT case). This catches the incident
# (no signature at all), a wrong number, AND the wrong identity; the exact
# last-line placement stays the prose rule's + review's job. Accepted residuals
# (documented, not chased, per #319):
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
#   * a RUNTIME-built signature (`f"ZbynekAI {n}"` / `f"MarekAI {n}"`, a `%d`)
#     is OVER-blocked -- the signature literal is not visible in the tool-call
#     content. This is the fail-safe (over-block) direction, off the recipe (which
#     mandates a literal `<word> <N>` last line), and carries the
#     `airuleset:discuss-sig-ok` bypass.
#   * IDENTITY DEGRADATION (#641): if `notify` is unimportable / the owner is
#     unresolvable, `signature_word` degrades to the DEFAULT `ZbynekAI`, so a
#     marek stream (montalu4) is then OVER-blocked on its truthful `MarekAI <N>`
#     post (the exact pre-#641 contradiction, but only in that rare fail-safe) --
#     the same unmeasurable->default bias, bypassable via `airuleset:discuss-sig-ok`.
#     The signature REQUIREMENT itself is never waived (the gate keys on
#     stream_number, evaluated before + independently of owner resolution).
#   * because the check is "a compliant `<word> <N>` appears ANYWHERE in the
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
# ZbynekAI compaction, never invented. Only marek needs an own word today: marek
# is the ONLY stream with its OWN client-visible handover account (odoo-erp #3864).
# EVERY other stream (zbynek's montaluN, david, simap, miva) posts under the SHARED
# "Zbynek AI - odovzdavky" account (odoo-erp #3176/#3527), so the DEFAULT ZbynekAI
# is the CORRECT identity for them, not a latent wrong-identity bug -- confirmed by
# an odoo-erp search: no "DavidAI"/own-account ticket exists for any other stream.
# WHEN a future stream gets its OWN handover account (the #3864 pattern repeated),
# ADD its owner here. This is NOT a second stream->owner map -- the stream->owner
# resolution is reused wholesale from notify.STREAM_NOTIFY_OWNER (via
# `_stream_owner`); this table only supplies the display token, a genuinely NEW
# datum that lives nowhere else. NB: STREAM_NOTIFY_OWNER also routes non-stream
# accounts to marek (admin/stepan, forestshop-dev), so `_stream_owner("admin")`
# -> "marek" -> "MarekAI" -- but that is UNREACHABLE: this table is only ever
# consulted via `signature_word`, which is only ever called for a user that
# already has a `cli_aliases.stream_number` (the gate is silent otherwise), and
# admin/stepan/marek all resolve stream_number None. So the conflation never bites.
# marek entry removed (#882, 2026-09-05: stream decommissioned). The table
# held {"marek": "MarekAI"} for montalu4's own handover account (odoo-erp
# issue 3864). With marek gone, all remaining streams use the default ZbynekAI.
_OWNER_SIGNATURE_WORD = {}


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
# `[ \t]` (not the earlier `[^\S\r\n]`): an exotic Unicode line separator
# (U+2028, NEL, VT) must not satisfy the same-line claim -- tightened in one
# sweep with the #696 `_ARTIFACT_RE` sibling (over-block direction).
#
# #799: the first non-whitespace TOKEN after the marker is captured so the
# template-grant branch below can inspect it. The trigger condition is
# UNCHANGED from the old `[ \t]+\S` (>= 1 non-whitespace char on the marker's
# own line), so every free-form per-message approval keeps its exact prior
# verdict; only a `template:<type>` colon-form token is additionally narrowed.
_APPROVAL_RE = re.compile(re.escape(APPROVAL_MARKER_WORD) + r"[ \t]+(\S+)")

# #799 STANDING TEMPLATE GRANT. The two MECHANICAL client-message types (the
# final reminder + the closing note) do not queue for per-message owner
# approval: the owner approves each stream's TEMPLATE once, and those messages
# cite `airuleset:owner-approved template:<TYPE>` with TYPE one of these two
# sanctioned tokens (a trailing free-form ref is OPTIONAL -- the TYPE token IS
# the falsifiable reference to the standing template, per
# `test_sanctioned_type_alone_is_the_reference`). An UNsanctioned
# `template:<other>` does NOT grant approval -- it still BLOCKS (fail-safe
# over-block) -- so the standing grant can never be widened to an arbitrary
# client message. The `template:` prefix + TYPE are matched CASE-INSENSITIVELY,
# so a casing typo (`Template:closing-note`) is still routed through the
# sanctioned-type check rather than slipping past as a free-form ref. Owner
# directive 2026-09-01 (montalu1); doctrine in handover-compose.md (#799).
_TEMPLATE_PREFIX = "template:"
SANCTIONED_TEMPLATE_TYPES = ("final-reminder", "closing-note")

ApprovalViolation = namedtuple("ApprovalViolation", "number")


def approval_present(content):
    """True iff `content` carries a valid owner-approval marker.

    TWO accepted forms of `airuleset:owner-approved <first-token> ...`:
      * a free-form per-message reference (any non-empty first token that is
        NOT a `template:` colon form) -- unchanged #628 behaviour; a bare
        `airuleset:owner-approved` with no reference is still NOT accepted.
      * a STANDING template grant `template:<TYPE>` where <TYPE> is one of
        `SANCTIONED_TEMPLATE_TYPES` (#799) -- the two mechanical message types.

    A `template:<UNsanctioned-type>` token does NOT grant (fail-safe over-block);
    if it is the only marker present the post BLOCKS. Any single valid marker
    anywhere in `content` grants (multiple markers are OR-ed)."""
    if not content:
        return False
    for m in _APPROVAL_RE.finditer(content):
        token = m.group(1)
        if token.lower().startswith(_TEMPLATE_PREFIX):
            # a template-grant colon form (case-insensitive): valid ONLY for a
            # sanctioned type -- a casing typo is routed here, never slipped
            # through as a free-form ref.
            ttype = token[len(_TEMPLATE_PREFIX):].lower()
            if ttype in SANCTIONED_TEMPLATE_TYPES:
                return True
            # unsanctioned type -> this occurrence grants nothing; keep scanning
            # for another (free-form or sanctioned) marker.
            continue
        # a free-form per-message reference (the #628 shape) -- grants.
        return True
    return False


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


# --------------------------------------------------------------------------- #
# #695 -- message_post TICKET-BINDING gate.
#
# THE PROBLEM. The #627 close gate recognises a thread-bound ticket by the
# opt-in `Discuss-thread:` mark -- and the exact stream that forgot the #627
# doctrine forgets the mark too, so four odoo-erp tickets closed silently
# while their client threads rotted for days (montalu5, 2026-08-25). The
# binding must be CREATED where the thread relationship is born: at the
# stream's message_post into the thread, never from the stream's memory at
# close time. So EVERY discuss.channel message_post by a stream must carry a
# `Discuss-ticket: #N` marker naming the bound ticket -- a falsifiable claim
# (the #628 owner-approved model: presence is mechanical, truth is a review
# matter), and the block message teaches recording the mirror line
# (`Discuss-thread: <channel-id>`) on the ticket itself, which is what the
# close gate reads.
#
# DELIBERATE ADAPTATION from the ticket's "at the stream's FIRST message_post"
# wording: first-post detection needs durable per-stream-per-channel state,
# but this hook is stateless and /tmp state is swept -- a lost state file
# would read "not first" and SILENTLY skip the requirement (fail-UNSAFE).
# Every-post is stateless, fails safe, and follows the exact #609/#628 model
# (one extra comment token per posting script). Detection reuses
# `is_channel_message_post` + `stream_number` -- never a second derivation.
# Accepted residuals mirror #609/#628: a runtime-built marker over-blocks
# (fail-safe, bypassable); a multi-op tool-call can mask an unbound post via
# another op's marker; the marker cannot be verified to name a REAL ticket
# (falsifiability + review, #516). Bypass for a genuine ticketless internal
# post: `airuleset:discuss-bind-ok` (rare, logged).
# --------------------------------------------------------------------------- #

BINDING_MARKER_WORD = "Discuss-ticket"

BIND_BYPASS_MARKER = "airuleset:discuss-bind-ok"

# `Discuss-ticket: #N` -- the marker word, an optional-whitespace colon, then a
# REAL `#`-prefixed issue number (the exact ref form the block message teaches;
# a bare number is not accepted -- too easy to satisfy accidentally). Matched
# anywhere in the content (the posting script carries it as a comment line),
# case-insensitive like the close guard's own markers.
_BINDING_RE = re.compile(r"(?i)Discuss-ticket[ \t]*:[ \t]*#[0-9]+")

BindingViolation = namedtuple("BindingViolation", "number")


def binding_present(content):
    """True iff `content` carries a `Discuss-ticket: #N` binding marker with a
    real `#`-prefixed ticket number. A bare `Discuss-ticket:` (or a number
    without `#`) is NOT a binding -- the falsifiable-claim requirement."""
    if not content:
        return False
    return bool(_BINDING_RE.search(content))


def has_bind_bypass_marker(content):
    """True iff the deliberate `airuleset:discuss-bind-ok` bypass marker
    appears in `content` (rare, logged by the hook) -- for a genuine internal
    post into a channel bound to NO ticket."""
    return BIND_BYPASS_MARKER in (content or "")


def evaluate_message_post_binding(content, user):
    """A `BindingViolation` (number) iff `content` is a discuss.channel
    message_post by a stream `user` (cli_aliases.stream_number) that carries
    NO `Discuss-ticket: #N` binding marker; None (silent) otherwise -- a
    non-stream user, a non-message_post op, or a post already carrying the
    marker. The `airuleset:discuss-bind-ok` bypass is handled by the hook
    (like the signature/approval bypasses), not here."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_message_post(content):
        return None
    if binding_present(content):
        return None
    return BindingViolation(number=number)


# --------------------------------------------------------------------------- #
# #696 -- message_post FUTURE-PROMISE gate.
#
# OWNER RULING (2026-08-25, montalu5, verbatim): "Preco mu chces pisat o
# zajtrajsom emaile, vzdy sa treba odvolavat na to co sa udialo nie na to co
# sa udeje. Bud mu iniciuj email report teraz a posli ked si si ze mu odisiel
# a obsahuje co si mu slubil alebo cakaj do zajtra!!!" -- a stream proposed a
# client handover (thread 263) promising "od zajtrajsieho ranneho e-mailu..."
# while the promised artifact (the digest e-mail) did not yet exist. A client
# message may reference ONLY events that already happened AND were verified.
#
# DETECTION: the ticket's OWN Slovak pattern list, word-bounded and
# case-insensitive, scanned over the whole message_post content (the #609
# whole-content model -- the body is often a variable, so scoping to "the
# body" is not reliably provable). The ONLY escape is the falsifiable
# `airuleset:artifact-verified <ref>` evidence marker with a SAME-LINE
# non-empty reference (the #628 `_APPROVAL_RE` shape) -- the ticket's
# explicit "bypass len s falsifikovatelnou znackou"; there is deliberately NO
# separate convenience bypass. Accepted residuals: an UNLISTED future
# phrasing slips (fail toward not blocking on an un-named pattern -- the
# doctrine in handover-compose.md covers the rest); a promise word outside
# the client body but inside the same tool-call over-blocks (the same
# whole-content residual as #609/#628, fail-safe direction); a genuinely
# non-client post carrying a promise word needs an honest artifact-verified
# ref naming why (rare; the marker stays falsifiable either way).
# --------------------------------------------------------------------------- #

ARTIFACT_MARKER_WORD = "airuleset:artifact-verified"

# `airuleset:artifact-verified <ref>`: the marker word then SAME-LINE
# horizontal whitespace then at least one non-whitespace char -- a real,
# non-empty reference on the marker's OWN line (the #628 review-MAJOR lesson:
# a `\s+\S` spanning the newline would let a bare reference-less marker pass
# whenever the call follows on the next line). `[ \t]` -- not `[^\S\r\n]` --
# so an exotic Unicode line separator (U+2028, NEL, VT) can never satisfy the
# same-line claim either (#696 review 🔵; over-block direction: a marker
# separated from its ref by an nbsp fails and gets retyped).
_ARTIFACT_RE = re.compile(re.escape(ARTIFACT_MARKER_WORD) + r"[ \t]+\S")

# The ticket's Slovak future-promise patterns. Word-bounded (`\b` is
# unicode-aware in py3, so Slovak diacritics form real boundaries):
#   od zajtra          -- also NOT inside "hod zajtra" (\b before `od`)
#   zajtraj[šs]        -- stem covers zajtrajší/zajtrajšieho/... declensions
#   bude (pri|v|obsahova[ťt]) -- the trailing \b keeps "bude viac" out (`v`
#                            must end at a boundary)
#   v [ďd]al[šs]om (e-maile|reporte)  -- `e-?maile` covers the unhyphenated form
#   od bud[úu]c        -- stem covers budúceho/budúcej/budúcich
#   [čc]oskoro, pripravujeme
# `\s+` between words tolerates a hard-wrapped body. The diacritic letters are
# TWO-CHAR CLASSES (š|s, ť|t, ď|d, ú|u, č|c) because this fleet demonstrably
# writes ASCII-transliterated Slovak too (the owner's own verbatim #696 ruling
# is diacritic-less) -- a transliteration is the SAME listed phrase, not a
# rephrasing, and over-fire is the documented safe direction (#514; both #696
# adversarial reviewers flagged the diacritic-only stems as the asymmetric
# gap while `(?i)` case-folding was handled).
_PROMISE_RE = re.compile(
    r"(?i)\b(?:od\s+zajtra\b|zajtraj[šs]|bude\s+(?:pri|v|obsahova[ťt])\b|"
    r"v\s+[ďd]al[šs]om\s+(?:e-?maile|reporte)\b|od\s+bud[úu]c|[čc]oskoro\b|"
    r"pripravujeme\b)")

PromiseViolation = namedtuple("PromiseViolation", "number matched")


def promise_phrases(content):
    """Every future-promise phrase matched in `content` (possibly empty)."""
    return [m.group(0) for m in _PROMISE_RE.finditer(content or "")]


def artifact_verified_present(content):
    """True iff `content` carries the `airuleset:artifact-verified <ref>`
    evidence marker WITH a non-empty same-line reference. A bare marker (no
    reference, or the reference only on a later line) is NOT accepted -- the
    falsifiable-claim requirement."""
    if not content:
        return False
    return bool(_ARTIFACT_RE.search(content))


def evaluate_message_post_promise(content, user):
    """A `PromiseViolation` (number, matched phrases) iff `content` is a
    discuss.channel message_post by a stream `user` (cli_aliases.
    stream_number) whose content carries a Slovak FUTURE-PROMISE pattern and
    NO `airuleset:artifact-verified <ref>` evidence marker; None (silent)
    otherwise -- a non-stream user, a non-message_post op, past-tense-only
    content, or a post carrying the artifact evidence. Unlike the sibling
    gates there is NO separate convenience bypass -- the evidence marker IS
    the only escape (#696, the ticket's explicit shape)."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_message_post(content):
        return None
    matched = promise_phrases(content)
    if not matched:
        return None
    if artifact_verified_present(content):
        return None
    return PromiseViolation(number=number, matched=matched)


# --------------------------------------------------------------------------- #
# #702 -- message_post MENTION-ANCHOR gate.
#
# OWNER RULING (2026-08-25, montalu2, verbatim): "extremne mi vadi ze posles
# spravu a peta neoznacis takze ak ma notify na mention tak mu to vobec
# nepipne!!! ... toto musi byt tvrde pravidlo ludia do discussion oddo musia
# byt realne oznaceny". Odoo Discuss delivery has TWO INDEPENDENT halves:
# `partner_ids` on message_post drives DELIVERY (inbox/e-mail + the owner
# control ping), while the MENTION notification -- the one that pings a
# client whose notifications are set to "mentions only" -- fires only from a
# mention ANCHOR embedded in the HTML body (the composer-emitted
# `<a class="o_mail_redirect" data-oe-model="res.partner" data-oe-id=...>`).
# Live incident: three approved client messages (montalu PROD threads
# 262/287, 24.-25.8.2026) went out with partner_ids ONLY -- the client got
# NO ping; fixed by unlink+repost with anchors (msgs 1742837/1742838).
#
# DETECTION reuses `is_channel_message_post` + `cli_aliases.stream_number`
# (never a second detection/derivation). The gate fires iff the content ALSO
# names a `partner_ids` KEY (the falsifiable signal that the post claims
# addressees) and carries NO mention-anchor token ANYWHERE (the #609
# whole-content model -- the body is often a variable). Client-vs-internal
# is NOT heuristically distinguished -- the family (#609/#628/#695) never
# does; a genuine internal post carries the logged
# `airuleset:discuss-mention-ok` bypass instead.
#
# Accepted residuals (documented, not chased, per #319):
#   * a body built in a PREVIOUS tool call (only `body=body_html` + a literal
#     partner_ids visible here) OVER-blocks -- the anchor is not visible in
#     this content. Fail-safe (over-block) direction, off the recipe (which
#     builds the body in the posting script), bypassable.
#   * PER-ADDRESSEE binding (every pid in partner_ids has ITS OWN anchor) is
#     not provable from the payload -- partner_ids is routinely a variable /
#     unpacking (`[owner_pid, *recipient_pids]`, the recipe's own shape), and
#     a literal-id match would false-block the owner control-ping pid (the
#     owner is not an addressee of the client message). Presence-only here;
#     the per-addressee arm rides doctrine (handover-compose.md) + review,
#     exactly like #609 cedes exact signature placement.
#   * a `partner_ids` token in a non-key position that still matches the
#     key-ish regex (`partner_ids =` inside a comparison) OVER-detects ->
#     over-block, safe direction, bypassable.
#   * a message_post with NO visible partner_ids fails OPEN (addressees
#     unmeasurable; the delivery-half mandate stays the doctrine's job) --
#     the same unmeasurable->allow bias as the create gate's no-literal-name
#     case; the incident always carried a literal partner_ids.
#   * a multi-op tool-call can mask an unanchored post via ANOTHER op's
#     anchor token -- the same unmeasurable->allow bias the sibling gates
#     document for their own markers.
# --------------------------------------------------------------------------- #

MENTION_BYPASS_MARKER = "airuleset:discuss-mention-ok"

# `partner_ids` used as a KEY: JSON `"partner_ids":`, dict `'partner_ids':`,
# or a python kwarg/assignment `partner_ids=`. The `(?<![\w.])` lookbehind
# (the _NAME_RE precedent) keeps a DIFFERENT field carrying the substring --
# `channel_partner_ids` (a real discuss.channel field), `x_partner_ids` -- and
# an ORM attribute write (`.partner_ids =`) from being read as this post's
# addressee claim.
_PARTNER_IDS_RE = re.compile(r"""(?<![\w.])['"]?partner_ids['"]?\s*[:=]""")

# A REAL mention anchor token, anywhere in the content: the composer-emitted
# `class="o_mail_redirect"` OR `data-oe-model="res.partner"` (single/double/
# backslash-escaped quotes -- the attribute routinely rides inside a
# double-quoted JSON body string as `\"res.partner\"`). Presence-level ONLY:
# the exact 19.0 attribute set is version-dependent (SKILL.md caveat), so the
# gate requires the anchor's presence, never a full attribute match. A record
# link to another model (`data-oe-model="product.product"`) is NOT a mention.
_MENTION_ANCHOR_RE = re.compile(
    r"""(?i)o_mail_redirect|data-oe-model\s*=\s*\\?['"]res\.partner\\?['"]""")

MentionViolation = namedtuple("MentionViolation", "number")


def partner_ids_present(content):
    """True iff `content` names a `partner_ids` KEY (JSON / dict / kwarg) --
    the falsifiable signal that the post claims addressees. A bare prose
    mention of the word without `:`/`=` is not an addressee claim."""
    if not content:
        return False
    return bool(_PARTNER_IDS_RE.search(content))


def mention_anchor_present(content):
    """True iff `content` carries a mention-anchor token anywhere -- the
    `o_mail_redirect` class or a `data-oe-model` pointing at `res.partner`
    (any quote style, incl. backslash-escaped). Plain-text `@Meno` is NOT an
    anchor (the incident shape: text pings nobody)."""
    if not content:
        return False
    return bool(_MENTION_ANCHOR_RE.search(content))


def has_mention_bypass_marker(content):
    """True iff the deliberate `airuleset:discuss-mention-ok` bypass marker
    appears in `content` (rare, logged by the hook) -- for a genuine internal
    post where no addressee needs a mention."""
    return MENTION_BYPASS_MARKER in (content or "")


def evaluate_message_post_mention(content, user):
    """A `MentionViolation` (number) iff `content` is a discuss.channel
    message_post by a stream `user` (cli_aliases.stream_number) that names a
    `partner_ids` key but carries NO mention-anchor token; None (silent)
    otherwise -- a non-stream user, a non-message_post op, a post with no
    visible partner_ids (addressees unmeasurable -> fail open), or a post
    already carrying an anchor. The `airuleset:discuss-mention-ok` bypass is
    handled by the hook (like the sibling bypasses), not here."""
    number = cli_aliases.stream_number(user)
    if number is None:
        return None
    if not is_channel_message_post(content):
        return None
    if not partner_ids_present(content):
        return None
    if mention_anchor_present(content):
        return None
    return MentionViolation(number=number)
