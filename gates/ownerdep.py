"""gates.ownerdep -- #1307: an owner dependency written to a ticket is ASKED in the same turn.

THE INCIDENT (owner, 11.11.2026: "stale problem s otazkami ktore mi nie su
polozene!!!"). On 10.11. and 11.11. the dstack session recorded on dstack 141
that its next zvukar test "needs the owner's approval" / "needs the owner's OK"
/ "needs the owner's hands", never asked him, and set no U label -- the footer
counts only U labels and session `❓` pings, so the question was invisible.

THE CHECK. Dispatched from `hooks/stop-check-question-quality.sh` at its "not a
question turn" branch, with the Stop payload on stdin. It reads THIS TURN's tool
calls and assistant texts (`gates.handover.turn_tool_calls(texts=True)`: after
the last genuine prompt; Stop-hook feedback, task notifications and peer
messages never reset it) and parses each Bash segment that names `gh` with the
gate family's shell primitives (`gates.shellcmd` + `gates.selfservice` +
`gates.opswait._comment_body`). A turn OWES a question when a ticket write --
`gh issue comment|edit|create` (`--body`, `-b`, `$(cat F)`, `$(cat <<EOF)`,
`--body-file`/`-F` file or heredoc; a body file the Write tool or a `cat > F
<<EOF` made earlier this turn is read from the turn) or `gh api …/issues/N
[/comments]` / `…/issues/comments/N` with a `body=` field (`@file` too) --
states a FUTURE or NEEDED owner dependency (`owner_dependency`). A failed call
counts only when its result shows the posted comment/issue URL. A dependency
line already present in an earlier tool result of the turn (a `gh issue view`
before a body edit) is a restatement, not a new dependency. A body EDIT from a
file that a read of the same ticket filled this turn (`gh issue view N … > F`,
also piped through sed/jq, copied by cp/mv; then sed / Edit, then `--body-file
F`, `--body "$(cat F)"` or `body=@F`) counts only a dependency phrase the turn
itself authored (Bash text, Write content, Edit's new lines): the rest is the
prior body (#1307 false positive, 11.11.). Any other write to F (`>>`, tee, a
script, a subagent, MultiEdit) drops that provenance, so F counts in full.

Settled by a DELIVERED question after the write -- an `❓ ASKED:` line or a
last-line `❓ NEEDS YOU:`, outside code (the notify hook's own delivery rule), in
the final message or in a text that ENDED an earlier stop of the same turn (it
is followed by Stop feedback / a notification, never by a tool call), by an idle
wake (a `<task-notification>` / peer `<channel>` right after such a text: every
gate passed that stop, so what came before is judged) -- or by a
successful add of a U label (`needs-answer` / `needs-decision` /
`needs-owner-action`) on that ticket in the turn (any U label settles an
unresolvable `#?` comment edit). Escape: a line `airuleset:owner-dep-ok
<reason>` (outside code fences) in the final message or in such an earlier
stop's text. Bypasses and blocks are logged to `audits/owner-dep-gate.log`.
Fail-OPEN on an unreadable transcript.

Known residual: a ticket already in U whose dependency is restated without a
read in the same turn blocks once (the bypass names it); `$VAR` bodies and
nested `bash -c` are not resolved; the regex is deliberately narrow (needed /
future verb forms only); a whole-body Write over a read-filled file, a sed
PATTERN or grep quoting an old dependency, or an Edit keeping it on a changed
line counts as authored (blocks once); only the first dependency line of a
body is judged.

Dry-run:
  echo '{"last_assistant_message":"x","transcript_path":"/t.jsonl","cwd":"/r"}' \
    | python3 -m gates.ownerdep ; echo rc=$?
"""
import json
import os
import re
import sys

from gates import audit, field_of, read_payload
from gates.handover import _SUBST_FILE_RX, _all_values, _unwrap, turn_tool_calls
from gates.opswait import _FENCE_RE, _comment_body, _ticket_keys
from gates.selfservice import (
    _all_flag_values, _apply_cd, _capture_heredocs, _flag_value, _is_gh_issue_cmd, _tokens_of,
)
from gates.shellcmd import split_top_level

AUDIT_LOG = "owner-dep-gate.log"
U_LABELS = {"needs-answer", "needs-decision", "needs-owner-action"}

_BYPASS_RX = re.compile(r"^[ \t*-]*airuleset:owner-dep-ok[ \t]+(\S[^\n]{3,})", re.MULTILINE)
_ASKED_LINE_RX = re.compile(r"^[ \t*_>~-]*❓\s*\**\s*ASKED\s*\**\s*:", re.IGNORECASE | re.MULTILINE)
_NEEDS_YOU_RX = re.compile(r"^[ \t*_>~-]*❓\s*\**\s*NEEDS\s+YOU\s*\**\s*:", re.IGNORECASE)
_COMMENT_URL_RX = re.compile(r"issuecomment-\d+")
_WAKE_RX = re.compile(r"\s*<(?:task-notification|channel)\b")
_REDIRECT_RX = re.compile(r"^(?:\d|&)?(>>?)\|?(.*)$")
_API_BODY_RX = re.compile(r"(?:^|/)issues/(\d+)/?(?:\?|$)")
_WRITE_API_RX = re.compile(r"\bbody=|--input|(?:-X|--method)\s*(?:POST|PATCH|PUT)")
_KEEPS_BODY = {"gh", "sed", "cat", "grep", "egrep", "head", "tail", "wc", "diff", "less", "ls"}
_PIPE_KEEPS = {"sed", "jq", "tr"}             # a transform of the piped read, nothing mixed in
_SED_FILE_CMD_RX = re.compile(r"(?:^|[;{}\s\d$/])[rRwWe]\s+\S")
_GH_RX = re.compile(r"\bgh\s")
_READ_RX = re.compile(r"\bgh\s+(?:issue\s+view|api)\b(?![^\n]*(?:\bbody=|--input|\bgraphql\b|"
                      r"(?:-X|--method)\s*(?:POST|PATCH|PUT)))")
_ISSUE_URL_RX = re.compile(r"/issues/(\d+)")
_API_ISSUE_RX = re.compile(r"(?:^|/)issues/(?:(\d+)(?:/comments)?|comments/\d+)/?(?:\?|$)")
_API_LABELS_RX = re.compile(r"(?:^|/)issues/(\d+)/labels/?(?:\?|$)")
_CODE_OR_QUOTE_RX = re.compile(r"`[^`\n]*`|(?<!\w)\"[^\"\n]{1,300}\"(?!\w)|„[^“\n]*“|“[^”\n]*”|"
                               r"(?<!\w)'[^'\n]{1,300}'(?!\w)|‚[^‘\n]*‘|«[^»\n]*»")
_SKIP_LINE_RX = re.compile(r"^\s*>|^\W*ROZHODNUT[ÉE]|^\s*[-*]\s+\[[xX]\]")
_CLAUSE_CUT_RX = re.compile(r"[;:.,!?()—–]|\s(?:and|but|so|now|then|because|that|which|a|ale|aj|"
                            r"preto|takže|lebo|teraz)\s")
_NEGATIONS = {"not", "never", "nie", "ani", "netreba", "nikdy", "nemusí", "nemusi", "nothing", "nič"}
_SETTLED = {"was", "were", "had", "previously", "bolo", "bol", "bola", "písal", "písala",
            "písalo", "napísal", "napísala", "stálo"}

# FUTURE / NEEDED forms only; a past or settled form ("owner-approved", "Owner
# approved", "approved by the owner", ROZHODNUTÉ) has none of these verbs.
_OWN = r"(?:(?:the|an?)\s+)?(?:owner|zby(?:ně|ň|nek(?!drlik))\w*)(?:['’]s|s['’]?)?"
_NOUN = (r"(?:approval|consent|decision|answer|ok|okay|go[- ]ahead|permission|sign[- ]off|"
         r"confirmation|input|hands|action|reply|response|choice|review)\b")
_SK_OWN = r"(?:tvoj\w*|ownerov\w*|owner\w*|zby(?:ně|ň|nek(?!drlik))\w*|vlastn[íi]k\w*|teba|tebe)"
_SK_OWNER_GEN = r"(?:od\s+)?(?:ownera|teba|zby(?:ně|ň|nek(?!drlik))\w*|vlastn[íi]ka)\b"
_SK_NOUN = (r"(?:s[úu]hlas\w*|schv[áa]len\w*|rozhodnut\w*|odpove\w*|povolen\w*|ok|"
            r"re[šs]tart\w*|z[áa]sah\w*|prihl[áa]sen\w*|pokyn\w*|[áa]no|vo[ľl]b\w*)\b")
# #1335: a step that waits on a PERSON AT A MACHINE or on the owner's own hands
# ("keď bude niekto pri PC", "vie len niekto pri počítači", "treba fyzicky
# skontrolovať", "musíš to spraviť ty", "once someone is at the box"). Measured
# on 14 days of dev1 final messages + ticket comments (17 accounts, #1335): a bare
# "ručne" / "fyzický" / "physical" is mostly description, so each form needs a
# NEED word (treba / musí / len / needs / must) or a future presence clause.
_WHO = r"(?:niekto|technik|ty|owner\w*|zby(?:ně|ň|nek(?!drlik))\w*)"
_ACT = (r"(?:skontrol|prever|pozr|zapoj|odpoj|pripoj|prepoj|vymen|re[šs]tart|zapn|vypn|stla[čc]|"
        r"vytiahn|zasun|nain[šs]tal|spust|zisti)\w*")    # a hands-on act, never "existovať"
_PRESENCE = (
    r"\b(?:ke[ďd]|k[ýy]m|a[žz])\s+(?:tam\s+|tu\s+)?(?:%s\s+)?(?:ne)?bud(?:e|e[šs])\s+(?:%s\s+)?"
    r"(?:[^\s.,;:!?]+\s+)?(?:fyzicky\s+)?(?:pri|na\s+mieste)\b" % (_WHO, _WHO),
    r"\b(?:ke[ďd]|a[žz])\s+(?:sa\s+)?(?:niekto|technik)\s+(?:pr[íi]de|dostane)\s+k\b",
    r"\bbude\s+niekto\s+na\s+mieste\b",
    r"\b(?:len|iba)\s+niekto\s+(?:pri|na\s+mieste)\b",
    r"\bmus[íi]\s+(?:[^\s.,;:!?]+\s+){0,2}niekto\s+(?:pri|na\s+mieste)\b",
    r"\bniekto\s+(?:pri\s+\S+|na\s+mieste)\s+(?:\S+\s+){0,2}(?:mus[íi]|treba|by\s+mal)\b",
    r"\btreba\s*,?\s*aby\s+(?:niekto|ty)\b",
    r"\btreba\s*,?\s*aby\s+si\s+(?:to\s+|ho\s+|ju\s+|ich\s+)?\w+l[aio]?\b",
    r"\b(?:treba|mus[íi]\w*|potrebuj\w*)\s+(?:[^\s.,;:!?]+\s+){0,3}fyzicky\s+"
    r"(?:[^\s.,;:!?]+\s+)?%s" % _ACT,
    r"\bfyzicky\s+(?:[^\s.,;:!?]+\s+){0,2}(?:treba|mus[íi]\w*)\s+(?:[^\s.,;:!?]+\s+)?%s" % _ACT,
    r"\bmus[íi][šs]\s+(?:[^\s.,;:!?]+\s+){0,3}ty\b",
    r"\b(?:stoj[íi]|je|ost[áa]va|zost[áa]va)\s+(?:to\s+|u[žz]\s+)?na\s+tebe\b",
    r"\b[čc]ak\w*\s+(?:to\s+|e[šs]te\s+|u[žz]\s+)na\s+(?:teba|tebe|ownera)\b",
    r"\bpotrebuj\w*(?:\s+od\s+teba|\s*,?\s*aby\s+si)\b",
    r"\b(?:when|once|until)\s+(?:someone|somebody|a\s+person|the\s+owner|you)\s+(?:is|are|gets?)"
    r"\s+(?:next\s+|back\s+)?(?:at|to|in\s+front\s+of|on[- ]site|physically)\b",
    r"\b(?:when|once|until)\s+you['’]re\s+(?:next\s+|back\s+)?(?:at|in\s+front\s+of|on[- ]site)\b",
    r"\b(?:someone|somebody|a\s+person)\s+(?:needs?\s+to|has\s+to|must)\s+(?:be\s+)?"
    r"(?:physically|on[- ]site|at\s+the|power|plug|unplug|replug|press|reboot|restart|"
    r"replace|swap|connect)\b",
    r"\b(?:someone|somebody|a\s+person)\s+(?:at|on)\s+(?:the\s+)?[\w-]+\s+(?:has\s+to|needs?\s+to|"
    r"must)\b",
    r"\b(?:needs?|requires?)\s+(?:someone|somebody|a\s+(?:person|human))\s+(?:at|on[- ]site|"
    r"physically|in\s+front)\b",
    r"\b(?:needs?|requires?|awaiting|pending)\s+(?:a\s+|an\s+)?(?:physical|manual|on[- ]site|"
    r"hands[- ]on|in[- ]person)\s+(?:step|action|check|visit|access|presence|intervention|"
    r"restart|reboot|power[- ]cycle|fix)\b",
    r"\b%s\s+(?:must|needs?\s+to|has\s+to|will\s+have\s+to)\s+(?:physically|manually|plug|"
    r"unplug|power|reboot|restart|press|replace|connect|install|log\s+in|flip|swap)\b" % _OWN,
    r"\b(?:owner|vlastn[íi]k|zby(?:ně|ň|nek(?!drlik))\w*)\s+mus[íi]\s+(?:[^\s.,;:!?]+\s+)?"
    r"(?:re[šs]tart|zapoj|odpoj|zapn|vypn|vymen|prihl[áa]s|spust|nain[šs]tal)\w*",
    r"\bonly\s+(?:someone|somebody|a\s+person|you)\s+(?:at|on[- ]site|physically|can)\b",
    r"\b(?:treba|mus[íi])\s+(?:to\s+)?(?:sprav|urob)\w*\s+(?:niekto\s+)?(?:fyzicky|na\s+mieste|"
    r"pri\s+\S+)",
    r"\bniekto\s+(?:to\s+)?mus[íi]\s+(?:[^\s.,;:!?]+\s+){0,2}(?:fyzicky|na\s+mieste|pri\s+\S+)",
    r"\bvy[žz]aduj\w*\s+fyzick\w+\s+(?:pr[íi]stup|z[áa]sah|pr[íi]tomnos)\w*",
    r"\bke[ďd]\s+pr[íi]de[šs]\s+k\b",
    r"\bnext\s+time\s+you['’]re\s+(?:at|in\s+front\s+of)\b",
    r"\b(?:waiting|blocked)\s+(?:on|for)\s+you\b",
    # #1404 (dasio 25.-28.11.): an on-site slot / a person at a machine, never a free queue slot
    r"\b(?:wait(?:s|ing)?|blocked|parked)\s+(?:for|on|until)\s+(?:the\s+|a\s+)?next\s+"
    r"(?:[\w-]+\s+){0,2}(?:slot|window|visit)\b",
    r"\b[čc]ak\w*\s+na\s+[ďd]alš\w*\s+(?:[\w-]+\s+){0,2}(?:slot|okn|term[íi]n|n[áa]v[šs]tev)\w*",
    r"\b(?:wait(?:s|ing)?|blocked)\s+(?:for|on)\s+(?:someone|somebody|a\s+person)\s+(?:at|on[- ]site)\b",
    r"\b(?:only|then(?:\s+[\w-]+)?)\s+with\s+(?:someone|somebody|a\s+person)\s+(?:on[- ]site|physically|"
    r"at\s+the\s+(?:[\w-]+\s+){0,2}"
    r"(?:pc|box|rig|machine|computer|desk|console|mixer)\b)",
    r"\bI\s+need\s+you\s+to\b",
    r"\byou\s+(?:need|have)\s+to\s+(?:physically|power|plug|replug|unplug|reboot|restart|press|"
    r"log\s+in|swap|replace)\b",
)
_PRESENCE_RX = re.compile("|".join(_PRESENCE), re.IGNORECASE)
_DEP_RX = re.compile("|".join((
    r"\b(?:needs?|requires?|awaiting|awaits|pending)\s+(?:%s|your)\s+%s" % (_OWN, _NOUN),
    r"\b(?:needs?|requires?|awaiting|awaits|pending)\s+%s\s+(?:from|of|by)\s+(?:the\s+)?owner\b"
    % _NOUN,
    r"\b(?:wait(?:s|ing)?|blocked|parked)\s+(?:for|on)\s+%s\b" % _OWN,
    r"\buntil\s+%s\s+(?:approves|decides|answers|confirms|replies|says)\b" % _OWN,
    r"\b%s\s+(?:must|has\s+to|needs?\s+to|should|will\s+have\s+to)\s+(?:approve|decide|"
    r"answer|confirm|consent|sign\s+off|choose|pick|ok|okay)\b" % _OWN,
    r"\b%s\s+%s\s+(?:is\s+|still\s+)?(?:needed|required|pending|outstanding)\b" % (_OWN, _NOUN),
    r"\bpotrebuj(?:em|eme)\s+(?:s[úu]hlas|schv[áa]lenie)\b(?!\s+(?:od|z|zo|klient\w*|z[áa]kazn\w*)\b)",
    r"\bpotrebuj\w*\s+%s\s+%s" % (_SK_OWN, _SK_NOUN),
    r"\b(?:potrebuj\w*|treba|potrebn\w*|vy[žz]aduj\w*|[čc]ak\w*(?:\s+sa)?\s+na)\s+%s\s+%s"
    % (_SK_NOUN, _SK_OWNER_GEN),
    r"\b[čc]ak(?:á|a|ám|am|áme|ame|ajú|aju|ať|at)\s+na\s+%s" % _SK_OWN,
    r"\b(?:owner|vlastn[íi]k|zby(?:ně|ň|nek(?!drlik))\w*)\s+mus[íi]\s+(?:schv|rozhod|ods[úu]hlas|potvrd|odpoved)\w*",
    r"\bmus[íi]\s+(?:to\s+)?(?:schv|rozhod|ods[úu]hlas|potvrd)\w*\s+(?:owner|vlastn[íi]k|zby(?:ně|ň|nek(?!drlik))\w*)\b",
) + _PRESENCE), re.IGNORECASE)


def _cancelled(line, start, end=None):
    """A negation ("does not need", "no longer needs", "netreba čakať na") or a
    settled marker ("was blocked on", "previously parked on") in the same
    clause just before the match; #1335: an audit REPORTING a ticket line, a
    generic rule ("každé … potrebuje tvoj súhlas"), or a rest of the sentence
    that says the step is done / needs nothing ("— nič netreba", "I did")."""
    window = line[max(0, start - 120):start]
    sentence = re.split(r"[.!?;]\s", window)[-1]
    if _reported(sentence) or _HYPOTHETICAL_RX.search(sentence):
        return True
    after = re.sub(r"\(\s*\)", "", line[end:end + 160]) if end is not None else ""
    if after and _DONE_AFTER_RX.search(_AFTER_CUT_RX.split(after)[0]):
        return True
    prefix = _CLAUSE_CUT_RX.split(re.sub(r",\s*no\s", ", ", window[-80:]))[-1]
    clause = re.findall(r"[\w'’-]+", prefix.lower())
    words = clause[-4:]
    generic = any(w in _GENERIC for w in clause) and not any(w.startswith(_STILL) for w in clause)
    return generic or "no" in words[-2:] or bool(_NO_SUBJECT_RX.search(prefix)) or any(
        w in _NEGATIONS or w in _SETTLED or w.endswith(("n't", "n’t")) for w in words)


def _reported(sentence):
    """An audit quoting a ticket line, never the session's own report: no `som`
    / `I` within 20 chars before the reporting verb."""
    m = _REPORTED_RX.search(sentence)
    if not m or re.match(r"z[áa]pis|ne", m.group(0), re.IGNORECASE):
        return bool(m)
    return not re.search(r"\b(?:som|I)\b", sentence[max(0, m.start() - 20):m.end()])


# #1335 measured FP: an audit that QUOTES what a ticket line said ("riadok, ktorý
# písal, že … potrebuje tvoj súhlas", "zápis, že práca čaká na …", "či nestojí,
# že"). Never the session's own first-person report ("som napísal, že").
_REPORTED_RX = re.compile(
    r"\b(?:na)?p[íi]sal[ao]?\b(?!\s+som)\s*,?\s+[žz]e\b|\bz[áa]pis\w*\s*,?\s+[žz]e\b|"
    r"\bne(?:stoj[íi]|st[áa]lo)\s*,?\s+[žz]e\b", re.IGNORECASE)
# The rest of the step's OWN clause (cut at ; — – ( and the sentence end) says it
# is done or needs nothing; a bare "automaticky" or a later clause never counts.
_AFTER_CUT_RX = re.compile(r"[;—–(]|[.!?](?:\s|$)")
_DONE_AFTER_RX = re.compile(
    r"\bni[čc]\s+(?:ne)?treba\b|\bnetreba\s+ni[čc]\b|\bnothing\s+(?:is\s+)?(?:needed|to\s+do)\b|"
    r"\bI\s+(?:did|ran|have\s+done)\b(?!\s*(?:n['’]t|not)\b)|\b(?:spravil|urobil)\s+som\b|"
    r"\bsa\s+(?:to\s+)?(?:\w+\s+)?(?:automaticky|samo|sám)\b|^\W*:?\**\s*(?:none|nothing|žiadn[eyaá]|"
    r"ni[čc])\W*$", re.IGNORECASE)
_NO_SUBJECT_RX = re.compile(r"\bno\s+(?:need|one|ticket|tickets|step|steps|question|questions|"
                            r"issue|issues|item|items)\b", re.IGNORECASE)
_STILL = ("remaining", "ďalš", "zvyšn", "zostávajúc", "ostávajúc", "next")
# a hypothetical, not a pending step: "Ak by na ňom trval, rozhodnutie je na tebe."
_HYPOTHETICAL_RX = re.compile(r"\b(?:ak\s+by|keby|in\s+case|ke[ďd]\s+naraz[íi]m)\b",
                              re.IGNORECASE)
_GENERIC = {"each", "every", "any", "always", "každé", "každý", "každá", "každú", "vždy"}


def _norm(text):
    return re.sub(r"\\x27|[\s\\'\"’`]+", "", text).lower()


def owner_dependency_lines(text, authored=None):
    """Every FULL line of `text` that states a future/needed owner dependency (or,
    #1335, a step waiting on a person at a machine). Skipped: fenced blocks, inline
    code and quoted spans, `>` quotes, `ROZHODNUTÉ` records and ticked `- [x]`
    lines. With `authored` (a `_norm`ed blob), only a phrase the turn itself wrote
    counts."""
    for raw in _FENCE_RE.sub("", text or "").splitlines():
        if _SKIP_LINE_RX.search(raw):
            continue
        line = _CODE_OR_QUOTE_RX.sub("", raw)
        for m in _DEP_RX.finditer(line):
            owner_until = m.group(0)[:5].lower() == "until" and not _PRESENCE_RX.match(m.group(0))
            if (owner_until or not _cancelled(line, m.start(), m.end())) \
                    and (authored is None or _norm(m.group(0)) in authored):
                yield raw.strip()
                break


def owner_dependency(text, authored=None):
    """The first `owner_dependency_lines` line of `text` (cut to 200), or ""."""
    return next(owner_dependency_lines(text, authored), "")[:200]


# #1335: a line that names another project's ticket or another session's question
# is THAT project's U (`owner/repo#N`, `repo#N`, `camera-box issue 1482`, "v relácii
# mikrotik", "sa ťa pýta vo svojom okne"); only this repo's tickets are judged.
# Bounded and anchored after a separator: a run of `-`/`.` stays linear (review).
_REF_RX = re.compile(r"(?:(?<=[\s(\[,;:*_\"'„“/])|^)([\w.-]{1,100}(?:/[\w.-]{1,100})?)?#(\d{1,6})\b")
_PR_BEFORE_RX = re.compile(r"(?:\bPRs?|\bpull\s+requests?|\bkrok|\bstep)\s*$", re.IGNORECASE)
_URL_REF_RX = re.compile(r"github\.com/[\w.-]{1,100}/([\w.-]{1,100})/issues/(\d{1,6})\b")
_NAMED_ISSUE_RX = re.compile(r"\b(?:issue|tiket(?:e|u|om|y)?|ticket(?:u)?)\s+#?(\d{1,6})\b",
                             re.IGNORECASE)
_NAME_BEFORE_RX = re.compile(r"([\w-]{1,60})\s+$")
_NOT_A_PROJECT_SFX = ("-side", "-level", "-related", "-specific", "-up", "-wide")
_ELSEWHERE_RX = re.compile(
    r"\bvo\s+svoj(?:om|ej)\s+(?:okne|rel[áa]cii|session)\b|"
    r"\b(?:sa\s+ťa|ťa\s+sa)\s+(?:na\s+to\s+)?p[ýy]ta\b|\bp[ýy]ta\s+sa\s+ťa\b|"
    r"\bin\s+(?:its|their)\s+(?:own\s+)?session\b|"
    r"\b(?:odpove\w*|s[úu]hlas\w*|schv[áa]len\w*|rozhodnut\w*|ot[áa]zk\w*|[áa]no)\W{1,3}"
    r"ktor[ýáéúí]\w*\s+(?:sa\s+ťa\s+)?(?:p[ýy]ta(?:l[aio]?)?|zbiera(?:l[aio]?)?|"
    r"zis[ťt]uje|zis[ťt]oval[aio]?)\s+[\w-]+",       # a 3rd-person asker, never "zisťujem"
    re.IGNORECASE)
# Another session by its (lowercase) project slug: case-SENSITIVE, so "Windows asks
# you", "BIOS will ask you", "predloží ti to BIOS" never read as a session.
_SESSION_RX = re.compile(
    r"(?:\b|^)([a-z][a-z0-9-]{1,40})\s+(?:is\s+asking|will\s+ask|asks)\s+(?:him|you|the\s+owner)\b|"
    r"\b[Pp]redlož(?:í|ia)\s+ti\s+(?:ich\s+|ho\s+|ju\s+|to\s+)?([a-z][a-z0-9-]{1,40})\b|"
    r"\b(?:ti|ťa)\s+(?:to\s+)?(?:predlož(?:í|ia)|polož(?:í|ia))\s+([a-z][a-z0-9-]{1,40})\b|"
    r"\bv\s+rel[áa]cii\s+(?:projektu\s+)?([a-z][a-z0-9-]{1,40})\b")
_NOT_A_SESSION = {"dnes", "ráno", "včera", "teraz", "tejto", "mojej", "našej", "tu", "pred", "od",
                  "do", "so", "pri", "minulý", "minulej", "minulú", "neskôr", "projektu", "it",
                  "he", "she", "they", "this", "that", "which", "script", "skript", "service",
                  "system", "installer", "dialog", "dialóg", "prompt", "app", "aplikácia", "the"}


def line_refs(line, repo):
    """`(ours, foreign)`: this repo's issue numbers on `line` (bare `#N`,
    `<repo>#N`, `<repo> issue N`, a bare `issue N`, a GitHub issue URL) and
    whether it names another project's ticket. `PR #N` / `krok #N` is no ticket."""
    ours, foreign = set(), False
    for m in _REF_RX.finditer(line):
        qual, num = m.group(1), int(m.group(2))
        if not qual and _PR_BEFORE_RX.search(line[max(0, m.start() - 20):m.start()]):
            continue
        if not qual or qual.rsplit("/", 1)[-1].lower() == repo:
            ours.add(num)
        else:
            foreign = True
    for name, num in _URL_REF_RX.findall(line):
        if name.lower() == repo:
            ours.add(int(num))
        else:
            foreign = True
    for m in _NAMED_ISSUE_RX.finditer(line):
        name = (_NAME_BEFORE_RX.search(line[max(0, m.start() - 61):m.start()]) or [""])[0]
        name = name.strip().lower()
        if name != repo and "-" in name and not name.endswith(_NOT_A_PROJECT_SFX):
            foreign = True             # a project slug: camera-box issue 1482
        else:
            ours.add(int(m.group(1)))  # "issue 106", "tracking issue 106", "dasio issue 106"
    return ours, foreign


def owner_court(cwd, home=None):
    """`statusbar.owner_court` for `cwd`, or for the nearest parent that is a git
    checkout (the footer cache is keyed by the session's start dir). None when
    unknown (the caller fails OPEN); never raises."""
    try:
        import statusbar
        path = os.path.abspath(cwd or "/")
        chain = [path]                     # cwd up to its git checkout root, never past it
        while not os.path.exists(os.path.join(chain[-1], ".git")):
            parent = os.path.dirname(chain[-1])
            if parent == chain[-1]:
                chain = [path]             # no checkout: never borrow a parent's cache
                break
            chain.append(parent)
        for p in chain:
            court = statusbar.owner_court(p, home=home)
            if court is not None:
                return court
        return None
    except Exception:  # noqa: BLE001 — an unreadable cache never blocks
        return None


def _elsewhere(line, repo):
    if _ELSEWHERE_RX.search(line):
        return True
    return any(name and name not in _NOT_A_SESSION and name != repo
               for groups in _SESSION_RX.findall(line) for name in groups)


def held_in_u(line, court, labelled=frozenset(), any_label=False):
    """True when the owner's court already holds the step on `line` (its own
    ticket in U or labelled this turn; U > 0 or a label this turn for a line with
    no ticket of ours), or the line belongs to another project / session.
    `court=None` (unknown U) holds all: fail open."""
    if court is None:
        return True
    nums, total, repo = court
    if _elsewhere(line, repo):
        return True
    ours, foreign = line_refs(line, repo)
    if ours:
        return bool(ours & (nums | labelled))
    return foreign or total > 0 or any_label


def asked_in(text):
    """True when `text` carries a question the notify hook delivers: an
    `❓ ASKED:` line, or a `❓ NEEDS YOU:` last non-blank line (outside code)."""
    plain = _CODE_OR_QUOTE_RX.sub("", _FENCE_RE.sub("", text or ""))
    lines = [ln for ln in plain.splitlines() if ln.strip()]
    return bool(_ASKED_LINE_RX.search(plain) or (lines and _NEEDS_YOU_RX.match(lines[-1])))


class _Turn:
    """The turn's owner-dependency writes and U-label adds, in order."""

    def __init__(self, cwd):
        self.cwd = cwd or os.getcwd()
        self.files, self.seen = {}, []
        self.deps, self.labelled = [], set()       # deps: [ticket, line, asked]
        self.prior = {}                            # body file -> ticket whose body a read put there
        self._authored, self._norm, self._done = [], [], 0
        self._touch_key, self._touch_rx = (), None

    def author(self, *texts):
        self._authored += [t for t in texts if t]

    def authored(self):
        """The turn's own text, `_norm`ed lazily, each entry once (review: O(edits x turn))."""
        self._norm += [_norm(t) for t in self._authored[self._done:]]
        self._done = len(self._authored)
        return "\n".join(self._norm)

    @staticmethod
    def full(path, cwd):
        return os.path.normpath(os.path.join(cwd or "/", os.path.expanduser(str(path))))

    def read(self, path, cwd):
        full = self.full(path, cwd)
        if full in self.files:
            return self.files[full]
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read(512_000)
        except OSError:
            return ""

    def wrote(self, ticket, body, path=None, cwd=None):
        """A body file filled by a read of this ticket (`snapshot`) holds its
        prior body: only a dependency phrase the turn authored is new."""
        line = owner_dependency(body)
        if line and path and self.prior.get(self.full(path, cwd)) == ticket:
            line = owner_dependency(body, self.authored())
        if line and not any(line in seen for seen in self.seen):
            self.deps.append([ticket, line, False])

    @staticmethod
    def body_read(tk):
        """N for a read of ticket N's body (`gh issue view N|URL`, `gh api …/issues/N`)."""
        if tk[:3] == ["gh", "issue", "view"] and not {"--comments", "-c"} & set(tk) \
                and "body" in (_flag_value(tk, ("--json",)) or "body"):
            nums = [m.group(1) if m else t.lstrip("#") for t, m in
                    ((t, _ISSUE_URL_RX.search(t)) for t in tk[3:]) if m or t.lstrip("#").isdigit()]
        elif tk[:2] == ["gh", "api"] and not _WRITE_API_RX.search(" ".join(tk)):
            nums = [m.group(1) for m in map(_API_BODY_RX.search, tk[2:]) if m]
        else:
            return None
        return nums[0] if nums else None

    def foreign(self, args, cwd, keep):
        """A command that may mix other text into a file: a head outside `keep`,
        sed with a file command (r/R/w/e), or (piped) an existing file argument."""
        if args[0] not in keep:
            return True
        if args[0] == "sed" and any(_SED_FILE_CMD_RX.search(t) for t in args[1:] if t[:1] != "-"):
            return True
        return keep is _PIPE_KEEPS and any(
            t[:1] != "-" and os.path.isfile(self.full(t, cwd)) for t in args[1:])

    def track(self, tk, cwd, piped):
        """Body-file provenance: `> F` after a read of N (or a pipe from one through
        sed/jq/tr) makes F hold N's prior body; any other write to F (`>>`, tee, a
        script, sed r/w, cp/mv onto it) drops it. Readers and the gh edit keep it."""
        ops, args, skip = [], [], False
        for i, tok in enumerate(tk):
            m = None if skip else _REDIRECT_RX.match(tok)
            if m:
                path = m.group(2) or (tk[i + 1] if i + 1 < len(tk) else "")
                ops.append((m.group(1), self.full(path, cwd)))
                skip = not m.group(2)             # the target of a split redirect
            elif not skip:
                args.append(tok)
            else:
                skip = False
        if not args:
            return None
        piped = piped if piped and not self.foreign(args, cwd, _PIPE_KEEPS) else None
        ticket = self.body_read(args) or piped
        for op, full in ops:
            if op == ">" and ticket:
                self.prior[full] = ticket
            else:
                self.prior.pop(full, None)
        mentioned = [f for f in map(lambda t: self.full(t, cwd), args) if f in self.prior]
        if args[0] in ("cp", "mv") and mentioned and self.full(args[-1], cwd) not in mentioned:
            self.prior[self.full(args[-1], cwd)] = self.prior[mentioned[0]]
            if args[0] == "mv":
                self.prior.pop(mentioned[0], None)
        elif mentioned and self.foreign(args, cwd, _KEEPS_BODY):
            for full in mentioned:
                self.prior.pop(full, None)
        return ticket

    def label(self, ticket, values, ok):
        if ok and U_LABELS & {v.strip().lower() for v in values}:
            self.labelled.add(ticket)

    def gh_issue(self, tk, seg, cwd, heredocs, ok, result):
        file_bodies, direct_bodies = heredocs
        verb = tk[2]
        created = _ISSUE_URL_RX.search(result) if verb == "create" else None
        keys = [created.group(1) if created else "new"] if verb == "create" else _ticket_keys(tk)
        ticket = keys[0] if keys else "?"
        for key in keys:
            self.label(key, _all_flag_values(
                tk, ("--add-label",) if verb == "edit" else ("-l", "--label")), ok)
        url = _ISSUE_URL_RX if verb == "create" else _COMMENT_URL_RX
        if not (ok or url.search(result)):
            return
        bf, inline = _flag_value(tk, ("-F", "--body-file")), _flag_value(tk, ("--body", "-b"))
        sub, path = _SUBST_FILE_RX.search(inline or ""), None
        if bf and bf != "-" and bf not in file_bodies:
            body, path = self.read(bf, cwd), bf if verb == "edit" else None
        elif sub:
            sub = sub.group(1) or sub.group(2)
            body, path = self.read(sub, cwd), sub if verb == "edit" else None
        else:
            body = _comment_body(tk, seg, cwd, file_bodies, direct_bodies)
        self.wrote(ticket, body or "", path, cwd)

    def gh_api(self, tk, cwd, ok, result):
        fields = [f.partition("=") for f in _all_values(tk, ("-f", "-F", "--field", "--raw-field"))]
        for path in _all_values(tk, ("--input",)):
            try:
                fields.append(("body", "", str(json.loads(self.read(path, cwd)).get("body") or "")))
            except (ValueError, AttributeError):
                continue
        for tok in tk[2:]:
            lm = _API_LABELS_RX.search(tok)
            if lm:
                self.label(lm.group(1), [v for n, _, v in fields if n in ("labels[]", "labels")], ok)
            im = _API_ISSUE_RX.search(tok)
            if im and (ok or _COMMENT_URL_RX.search(result)):
                for name, _, val in fields:
                    if name == "body":
                        path = val[1:] if val.startswith("@") else None
                        self.wrote(im.group(1) or "?", self.read(path, cwd) if path else val,
                                   None if "/comments" in tok else path, cwd)

    def touches(self, text):
        """A bounded match of any read-filled file's name (one regex, review perf)."""
        if not self.prior:
            return False
        if self._touch_key != tuple(self.prior):
            self._touch_key = tuple(self.prior)
            self._touch_rx = re.compile(r"(?<![\w.-])(?:%s)(?![\w.-])" % "|".join(
                re.escape(os.path.basename(f)) for f in self.prior))
        return bool(self._touch_rx.search(text))

    def scan_bash(self, cmd, ok, result):
        touches = self.touches(cmd)
        if "<<" not in cmd and not _GH_RX.search(cmd) and not touches:
            return                                # cheap: nothing to record
        file_bodies, direct_bodies, skeleton = _capture_heredocs(re.sub(r"\\\r?\n", " ", cmd))
        cwd, piped, pos = self.cwd, None, 0
        for path, text in file_bodies.items():
            self.files[self.full(path, cwd)] = text
            self.prior.pop(self.full(path, cwd), None)
        if not (_GH_RX.search(skeleton) or touches):
            return
        for seg in split_top_level(skeleton):
            pos = skeleton.find(seg, pos) + len(seg)
            pipe = re.match(r"\|(?!\|)", skeleton[pos:])
            tk = _unwrap(_tokens_of(seg)) if (_GH_RX.search(seg) or piped or self.touches(seg)
                                              or seg.lstrip().startswith("cd")) else []
            if not tk:
                piped = None
                continue
            read = self.track(tk, cwd, piped)
            piped = read if pipe else None
            if tk[0] == "cd":
                cwd = _apply_cd(cwd, tk) or cwd
            elif _is_gh_issue_cmd(tk):
                self.gh_issue(tk, seg, cwd, (file_bodies, direct_bodies), ok, result or "")
            elif tk[:2] == ["gh", "api"]:
                self.gh_api(tk, cwd, ok, result or "")
        if any(self.touches(text) for text in direct_bodies.values()):
            self.prior.clear()                    # a heredoc script naming F may write it

    def asked(self, text):
        if asked_in(text) or bypass_reason(text):
            self.settle()

    def settle(self):
        for dep in self.deps:
            dep[2] = True


def bypass_reason(message):
    m = _BYPASS_RX.search(_FENCE_RE.sub("", message or ""))
    return m.group(1).strip() if m else None


def _scan_turn(calls, cwd):
    """The `_Turn` of this turn's tool calls and texts, in order."""
    turn, last_text = _Turn(cwd), None
    for name, inp, ok, result in calls:
        if name == "Bash":
            turn.author(str(inp.get("command") or ""))
            turn.scan_bash(str(inp.get("command") or ""), ok, result)
        elif name == "Write" and isinstance(inp.get("file_path"), str):
            turn.author(str(inp.get("content") or ""))
            turn.files[turn.full(inp["file_path"], turn.cwd)] = str(inp.get("content") or "")
        elif name == "Edit":
            old = set(str(inp.get("old_string") or "").splitlines())
            turn.author(*(ln for ln in str(inp.get("new_string") or "").splitlines()
                          if ln not in old))
        elif name in ("Agent", "Task", "Workflow", "MultiEdit", "NotebookEdit"):
            turn.prior.clear()            # a writer the gate cannot see into
        if name == "_text":
            last_text = inp.get("text")
        elif name == "_resume" and last_text is not None and _WAKE_RX.match(inp.get("text") or ""):
            turn.settle()                 # an idle wake: every gate passed the stop before it
        elif name == "_resume":           # the text before it ended a stop: delivered
            turn.asked(last_text)
        if name != "_text":               # a wake counts only right after a text
            last_text = None
        if name == "Bash" and _READ_RX.search(str(inp.get("command") or "")):
            turn.seen.append(result)
    return turn


def evaluate(transcript_path, message, cwd, home=None):
    """`("block", reason)` or `("allow", "")` for this turn. #1307 judges the
    ticket writes, #1335 also the final message's owner-step lines; a step the
    owner's court already holds (footer cache, `owner_court`) passes."""
    if bypass_reason(message) or asked_in(message):
        return "allow", ""
    calls = turn_tool_calls(transcript_path, texts=True) if transcript_path else []
    if calls is None:
        return "allow", ""                # unreadable transcript: fail OPEN
    turn = _scan_turn(calls, cwd)
    open_deps = [(t, line) for t, line, asked in turn.deps if not asked
                 and t not in turn.labelled and not (t == "?" and turn.labelled)]
    steps = list(owner_dependency_lines(message))
    if not (open_deps or steps):
        return "allow", ""
    court = owner_court(cwd, home)
    if court is not None:
        open_deps = [d for d in open_deps if not (d[0].isdigit() and int(d[0]) in court[0])]
    labelled = {int(t) for t in turn.labelled if t.isdigit()}
    steps = [line for line in steps
             if not held_in_u(line, court, labelled, any_label=bool(turn.labelled))]
    if open_deps:
        listing = "; ".join("#%s: „%s“" % dep for dep in open_deps[:3])
        return "block", (
            "🚫 BLOCKED (#1307) — tento ťah zapísal do tiketu závislosť na ownerovi, "
            "ale ownerovi si ju nepoložil: %s. Otázka zapísaná len na tikete sa nikam "
            "nedostane — footer U ju nevidí a na telefón nepríde. Sprav jedno z: "
            "(1) polož ju TERAZ celým blokom `**Otázka — projekt …:**` + možnosti + "
            "`❓ NEEDS YOU: <rozhodnutie>` (alebo `❓ ASKED:` + `⏳ WORKING`, ak pracuješ "
            "ďalej); (2) daj tomu tiketu label `needs-answer` / `needs-decision` / "
            "`needs-owner-action` (`gh issue edit <N> --add-label …`, aj keď ho už má); "
            "(3) ak súhlas podľa approval-scope.md netreba (testovanie rigu/zariadenia "
            "je samotná práca, owner si živý event stráži sám), závislosť z tiketu "
            "odstráň A ukonči správu vlastným riadkom `airuleset:owner-dep-ok "
            "<dôvod>` (tá istá výnimka platí zriedka aj inak; je logovaná)." % listing)
    if not steps:
        return "allow", ""
    return "block", (
        "🚫 BLOCKED (#1335) — správa opisuje krok, ktorý čaká na teba / na ownera "
        "alebo na človeka pri stroji, ale v U nie je: %s. Owner číta len footer U a "
        "telefón; krok spomenutý len v správe sa stratí. Sprav jedno z: (1) krok má "
        "tiket (#N): daj mu label `needs-owner-action` (fyzický / ručný krok) alebo "
        "`needs-answer` (otázka) a do komentára konkrétnu požiadavku — `gh issue edit "
        "<N> --add-label needs-owner-action`; až potom prípadný `❓` (otázka na tiket "
        "mimo U sa blokuje); (2) bez tiketu: polož otázku celým blokom `**Otázka — "
        "projekt …:**` (briefing, možnosti) a ukonči `❓ NEEDS YOU: <čo presne má owner "
        "urobiť>`, alebo `❓ ASKED: …` + `⏳ WORKING` len ak ešte niečo beží; (3) ak "
        "krok vieš spraviť sám (SSH/MCP, approval-scope.md), sprav ho a ukonči správu "
        "riadkom `airuleset:owner-dep-ok <dôvod>` (logované)." % "; ".join(
            "„%s“" % ln[:200] for ln in steps[:3]))


def run(payload):
    """0 = allow, 2 = block (reason on stderr)."""
    cwd = field_of(payload, "cwd", "")
    message = field_of(payload, "last_assistant_message", "")
    bypass = bypass_reason(message)
    if bypass:
        audit.append_line(AUDIT_LOG, "%s BYPASS project=%s cwd=%s reason=%s" % (
            audit.iso_now(), audit.project_of(cwd or None), cwd, bypass[:200]))
        return 0
    verdict, reason = evaluate(field_of(payload, "transcript_path", ""), message, cwd)
    if verdict != "block":
        return 0
    audit.append_line(AUDIT_LOG, "%s BLOCK project=%s cwd=%s" % (
        audit.iso_now(), audit.project_of(cwd or None), cwd))
    sys.stderr.write(reason + "\n")
    return 2


def main():
    sys.exit(run(read_payload()))


if __name__ == "__main__":
    main()
