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

  * CREATE DETECTION is precise so an UNRELATED Odoo op is never false-blocked
    (the ticket's explicit constraint). A CHANNEL create is signalled by, in
    order: the RPC model+method ADJACENCY `"discuss.channel", "create"` (the
    exact execute_kw shape the odoo-discuss-xmlrpc recipe uses); the ORM
    `['discuss.channel'].create(`; or a `parent_channel_id` key WITH the model
    referenced (the sub-thread field). A `message_post` (posting to an EXISTING
    channel) and a `write` (a rename -- the name-correction path MUST stay
    possible) carry NONE of these signals and are silently allowed. The RPC
    adjacency (not a bare `"create"` anywhere) stops a `res.partner` create in
    the same script from being read as a CHANNEL create; a `"create_date"` /
    `create_uid` FIELD is not the quoted method string `"create"` either.

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

  * ACCEPTED RESIDUALS (documented, not chased): a create whose name lives in a
    variable is not checked; a name literal containing a quote char is truncated
    by the naive quote-span (`[^'"\n]*`, same low-cost tradeoff close_trigger.py
    takes); a contrived multi-name create where an UNRELATED name literal happens
    to end with the exact stream number within the length cap would pass (a
    false-negative -- accepted per the issue's "block iff no name compliant"
    spec). Each fails toward NOT blocking or is the incident's own literal shape.
"""
import re
import unicodedata
from collections import namedtuple

import cli_aliases

# ~30 chars INCLUDING the trailing stream number (owner directive, #597). Char
# count, never bytes. Owner's good example "Oprava filtra rozmerov 2" == 24;
# the rejected "Viditeľnosť leadov pre obchodníkov 2" == 36.
MAX_NAME_LEN = 30

BYPASS_MARKER = "airuleset:discuss-name-ok"

_MODEL_RE = re.compile(r"""['"]discuss\.channel['"]""")
# RPC execute_kw model+method adjacency: "discuss.channel" , "create"
_RPC_CREATE_RE = re.compile(r"""['"]discuss\.channel['"]\s*,\s*['"]create['"]""")
# ORM: env['discuss.channel'].create(  /  ['discuss.channel'] . create (
_ORM_CREATE_RE = re.compile(r"""['"]discuss\.channel['"]\s*\]\s*\.\s*create\s*\(""")
# sub-thread create field
_PARENT_RE = re.compile(r"\bparent_channel_id\b")
# a `name` KEY (JSON/dict/kwarg) whose value is a string literal, NOT
# partner_name / .name / display_name / create_uid (lookbehind rejects a
# preceding word char or dot).
_NAME_RE = re.compile(r"""(?<![\w.])['"]?name['"]?\s*[:=]\s*(['"])([^'"\n]*)\1""")

Violation = namedtuple("Violation", "number names suggestion")


def is_channel_create(content):
    """True iff `content` creates a `discuss.channel` (top-level or a
    `parent_channel_id` sub-thread). A `message_post` / `write` carries none of
    the create signals and returns False -- the fail-safe direction is to gate
    ONLY a create, never an unrelated Odoo op."""
    if not content:
        return False
    if _RPC_CREATE_RE.search(content):
        return True
    if _ORM_CREATE_RE.search(content):
        return True
    if _MODEL_RE.search(content) and _PARENT_RE.search(content):
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
