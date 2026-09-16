# airuleset:html-ok  -- this is the gate's OWN source module: it names
# `message_post` and example markup (`<body>`, `<N>`, `<p>`) purely as
# documentation / regex, it never posts to Odoo, so the #915 body_is_html hook
# must not fire on writing or editing this file.
"""gates.clientbody -- client-board doctrine enforcement at write time
(#1014 / #1018 / #1024). ONE module, TWO disjoint checks; the thin hook
``hooks/block-client-board-doctrine.sh`` runs both on Bash|Write|Edit.

CHECK A -- client-facing BODY jargon gate (``classify_client_body``). The owner
directive (#1018): a client-facing message body -- a ``project.task`` chatter
note, a Discuss message, or a rewrite of the stream's OWN message -- must carry
ZERO developer jargon: no ``github.com`` links, no bare ``#NNNN`` issue refs, no
PR / commit / branch / RFR / gk / hand-off / CI / merge / worktree tokens ("preco
do commentarov do odoo taskov vypisujes technicke veci o githube!!"). The ONE
sanctioned exception is the ``GitHub ticket: #N`` linking marker (and the
``(GitHub #N)`` / ``(#N)`` description trailer) the odoo-task-sync tooling
depends on -- allowlisted.

Design that makes it SAFE (never a false block of a legitimate client message,
the #1018 review target): the gate scans ONLY the EXTRACTED message body, never
the surrounding posting code -- so ``env.cr.commit()`` (posting CODE) is never
read as the jargon token "commit". When it cannot extract a body it FAILS OPEN
(allows) -- a gate that cannot classify never blocks a client message. Bypass:
``airuleset:client-body-ok`` (logged by the hook).

CHECK B -- per-stream client-board memory-write guard (``classify_memory_write``).
An owner correction about board chatter / stages / addressing / "Done" must
change the RULE (``client-board-tasks.md``), never land in a per-stream
``~/.claude/projects/*/memory/*.md`` where the other streams never see it (the
"ako keby som uz jedneho neinstruoval" drift, #1014/#1028). A NEW memory whose
SUBJECT (frontmatter ``description:`` + first heading + filename -- never the
body, the #1028 fix-forward-2 lesson) is client-board doctrine is refused with a
pointer to the rule + the ``gk-request`` relay. Bypass:
``airuleset:client-board-memory-ok`` (a genuinely unrelated memory).

STDLIB ONLY. Every classifier is a pure function returning ``(blocked, reason)``;
``main()`` reads the hook payload and exits 0/2. Fail-open is delegated to the
thin bash adapter (exit != 2 -> allow), so no exception is silently swallowed
here (script-failure-policy.md).
"""

import json
import re

# --------------------------------------------------------------------------- #
# CHECK A -- body jargon gate
# --------------------------------------------------------------------------- #

# Posting-family prefilter: only a payload that plausibly POSTS a client message
# is scanned. A read (search_read on mail.message) matches the prefilter but
# carries no body -> no extraction -> fail-open allow.
_POSTING_FAMILY_RE = re.compile(
    r"message_post|odoo_post|post-message|mail\.message", re.IGNORECASE)

_BYPASS_BODY = "airuleset:client-body-ok"
_BYPASS_MEMORY = "airuleset:client-board-memory-ok"

# Sanctioned GitHub reference forms (odoo-task-sync depends on them) -- stripped
# from a body BEFORE the github / bare-issue-ref checks so the linking marker +
# the description trailer are never mistaken for jargon. `[\s-]*` (not `?`) so a
# double-space `GitHub  ticket:` is still recognised (#1018 review-1 F8).
_ALLOWLIST_REF_RE = re.compile(
    r"GitHub[\s-]*ticket:\s*#\d+"
    r"|Discuss[\s-]*ticket:\s*#\d+"
    r"|\(\s*GitHub\s+#\d+\s*\)"
    r"|\(\s*#\d+\s*\)",
    re.IGNORECASE,
)

# A GitHub MENTION (the URL, or the bare word „GitHubu"/"GitHub #N") — checked
# AFTER the allowlist strip, so the sanctioned `GitHub ticket: #N` marker never
# trips it. This is the owner's actual complaint („odpoved sem alebo do
# GitHubu", #1018) — broader than a bare github.com URL.
_GITHUB_WORD_RE = re.compile(r"github", re.IGNORECASE)

# A bare `#NNNN` is BLOCKED ONLY when corroborated by a github SIGNAL in the SAME
# body (#1018 review-1 F1, the headline false block): in the gate's own Odoo
# domain `#1058` far more often means an ORDER / INVOICE / product / SO record
# (or an all-digit hex colour `#003366`, a quantity `#500`) than a GitHub issue
# — blocking every `#NNNN` false-blocks legitimate client messages, the owner's
# WORST outcome. A github-word body is already blocked above; this arm adds the
# `issue #7110` / `pull request #7110` shape that carries no literal „github".
# `ticket` is deliberately NOT a signal (#1018 review-2 F-3): „Ticket" is a
# first-class Odoo Helpdesk object, so „váš helpdesk ticket #1058" is a
# legitimate client message. `issue` stays (Slovak client bodies say „problém",
# not „issue", so it is safe today; a future English board's collision is an
# accepted residual the github-word check + doctrine still cover). The literal
# #1018(a) „bare #NNNN" ask is deliberately narrowed to honour the
# higher-priority no-false-block directive (recorded in the review + LANE-RETURN).
_BARE_ISSUE_RE = re.compile(r"#\d{3,}")
_GITHUB_SIGNAL_RE = re.compile(r"\bissues?\b|pull\s+request", re.IGNORECASE)

# UNAMBIGUOUS developer tokens only. Dropped from the standalone list: PR (public
# relations / „PR oddelenie"), CI (corporate identity), merge + branch (Odoo's
# own „Merge duplicates" button; „branch" = pobočka) — #1018 review-1 F2/F3; and
# `gk` — #1018 review-2 F-2: a 2-letter token that false-blocks a MANDATED
# handover URL whose host contains it (`https://gk-erp.klient.sk/...`, rule 3).
# The full jargon set stays DISCOURAGED in the doctrine prose (rule 7); the
# mechanical gate only blocks what is unambiguous, and github.com + a
# github-context #NNNN already catch the real „developer pasted a GitHub ref".
_JARGON_ACRONYM_RE = re.compile(r"\bRFR\b")  # case-SENSITIVE (rare, unambiguous)
_JARGON_WORD_RE = re.compile(
    r"\b(?:commit|worktree|hand-?off|hand off)\b", re.IGNORECASE)

# Body extraction. Triple-quoted first (so the inner single/double form does not
# truncate it), then key form (body=, 'body':, "body":), the --body flag, and
# the odoo-task-sync post-message positional. Non-greedy + DOTALL: an HTML body
# spans newlines; the common case (delimiter quote not repeated raw inside) is
# what streams actually emit.
#
# The `body` KEY carries a non-word LOOKBEHIND `(?<![A-Za-z0-9_])` (#1018 review-2
# F-1, the headline over-extraction): without it `somebody=` / `nobody=` /
# `log_body=` / `email_body=` / `message_body=` all matched, so a posting script
# whose INTERNAL variable (e.g. `log_body = "...github.com..."`) carried jargon
# false-blocked the clean client body next to it — violating this module's own
# „scan only the extracted client body, never the surrounding code" invariant.
_BODY_PATTERNS = [
    re.compile(r"""(?<![A-Za-z0-9_])["']?body["']?\s*[:=]\s*(?P<q>\"\"\"|''')(?P<v>.*?)(?P=q)""", re.DOTALL),
    re.compile(r"""(?<![A-Za-z0-9_])["']?body["']?\s*[:=]\s*(?P<q>["'])(?P<v>.*?)(?P=q)""", re.DOTALL),
    re.compile(r"""--body(?:\s+|=)(?P<q>["'])(?P<v>.*?)(?P=q)""", re.DOTALL),
    re.compile(r"""post-message\s+\S+\s+(?P<q>["'])(?P<v>.*?)(?P=q)""", re.DOTALL),
]


def extract_bodies(content):
    """Every client-message body string the payload plausibly posts. Empty list
    when none is extractable -- the caller then FAILS OPEN (never a false block)."""
    bodies = []
    for pat in _BODY_PATTERNS:
        for m in pat.finditer(content or ""):
            v = m.group("v")
            if v and v not in bodies:
                bodies.append(v)
    return bodies


def _scan_body(body):
    """The first banned token found in a single body, or None. The sanctioned
    GitHub reference forms are stripped FIRST, so every subsequent check runs
    against a body with the linking marker / trailer removed."""
    stripped = _ALLOWLIST_REF_RE.sub(" ", body)
    if _GITHUB_WORD_RE.search(stripped):
        return "zmienka o GitHube / github.com odkaz"
    if _BARE_ISSUE_RE.search(stripped) and _GITHUB_SIGNAL_RE.search(stripped):
        return "cislo GitHub tiketu (issue/ticket #NNNN)"
    m = _JARGON_ACRONYM_RE.search(stripped)
    if m:
        return "vyvojarsky zargon (%s)" % m.group(0)
    m = _JARGON_WORD_RE.search(stripped)
    if m:
        return "vyvojarsky zargon (%s)" % m.group(0)
    return None


def classify_client_body(content):
    """Verdict for a client-facing message body: ``(blocked, reason)``. A SHAPE
    check -- it verifies the body carries no jargon token, never the message's
    business correctness. Fails open (allow) on an unextractable body."""
    text = content or ""
    if _BYPASS_BODY in text:
        return False, ""
    if not _POSTING_FAMILY_RE.search(text):
        return False, ""
    bodies = extract_bodies(text)
    if not bodies:
        return False, ""  # fail-open: cannot classify -> never block
    for body in bodies:
        hit = _scan_body(body)
        if hit:
            reason = (
                "\nBLOKOVANE: klientska sprava obsahuje odkaz na GitHub / "
                "vyvojarsky zargon (airuleset #1018).\n\n"
                "Najdene v tele spravy: %s.\n\n"
                "Klientovi (chatter na project.task alebo Discuss) sa pise iba "
                "obycajnou biznis slovencinou o tom, co sa pre NEHO zmenilo -- "
                "NIKDY zmienka o GitHube / github.com, cislo GitHub tiketu "
                "(`issue #NNNN` / `ticket #NNNN`), ani commit / worktree / "
                "hand-off / RFR / gk. Klient odpoveda iba v Odoo ulohe alebo "
                "majitelovi v chate; GitHub needs-answer tiket je len zrkadlo "
                "(client-board-tasks.md, pravidla 7 + 8). Cislo bez GitHub "
                "kontextu (napr. objednavka #1058, farba #003366) sa NEblokuje.\n\n"
                "Vynimka: sankcionovany linkovaci marker `GitHub ticket: #N` "
                "(a popisovy chvost `(GitHub #N)`), na ktorom stoji odoo-task-sync, "
                "je povoleny. Pre skutocny okrajovy pripad pouzi (loguje sa): "
                "`# airuleset:client-body-ok DOVOD`." % hit
            )
            return True, reason
    return False, ""


# --------------------------------------------------------------------------- #
# CHECK B -- per-stream client-board memory-write guard
# --------------------------------------------------------------------------- #

_MEMORY_PATH_RE = re.compile(r"/\.claude/projects/[^/]+/memory/.+\.md$")

# Client-board doctrine keywords, matched against the memory's SUBJECT only
# (#1028 fix-forward-2: filename / frontmatter description / first heading,
# never the body -- so an unrelated memory that merely mentions "Odoo" deep in
# its body is not caught). `board` is word-boundaried so keyboard/dashboard/
# onboarding never match. A memory must hit at least TWO DISTINCT keywords to be
# guarded (#1018 review-1 F5): a SINGLE generic word over-blocks unrelated
# memories a stream legitimately keeps -- "Odoo /json/2 transport", an "Odoo
# deploy box", an internal Trello "board", a "# Hotovo ked CI zelene" checklist
# all carry exactly one keyword and are NOT client-board doctrine.
_MEMORY_KEYWORD_RES = [
    re.compile(r"\bOdoo\b", re.IGNORECASE),
    re.compile(r"chatter", re.IGNORECASE),
    re.compile(r"koment[aá]r", re.IGNORECASE),
    re.compile(r"\bboard\b", re.IGNORECASE),
    re.compile(r"\bHotovo\b", re.IGNORECASE),
    re.compile(r"client task", re.IGNORECASE),
]
_MEMORY_MIN_KEYWORDS = 2

_FM_DESC_RE = re.compile(r"(?m)^\s*description\s*:\s*(?P<v>.+?)\s*$", re.IGNORECASE)
_FIRST_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*(?P<v>.+?)\s*$")


def _memory_subject(file_path, content):
    """The memory's SUBJECT for keyword matching: frontmatter ``description:`` +
    first markdown heading + the filename basename (never the body)."""
    parts = [file_path.rsplit("/", 1)[-1]]
    m = _FM_DESC_RE.search(content or "")
    if m:
        parts.append(m.group("v"))
    m = _FIRST_HEADING_RE.search(content or "")
    if m:
        parts.append(m.group("v"))
    return " ".join(parts)


def classify_memory_write(file_path, content):
    """Verdict for a Write/Edit to a per-stream memory file: ``(blocked,
    reason)``. Blocks a NEW client-board-doctrine memory, pointing to the rule +
    the gk-request relay. Only the SUBJECT is matched (not the body)."""
    path = file_path or ""
    text = content or ""
    if _BYPASS_MEMORY in text:
        return False, ""
    if not _MEMORY_PATH_RE.search(path):
        return False, ""
    subject = _memory_subject(path, text)
    hits = sum(1 for rx in _MEMORY_KEYWORD_RES if rx.search(subject))
    if hits < _MEMORY_MIN_KEYWORDS:
        return False, ""
    reason = (
        "\nBLOKOVANE: nova per-stream memory o Odoo klientskom boarde "
        "(airuleset #1014/#1028).\n\n"
        "Ownerova korekcia o chatteri / stlpcoch / oslovovani / Hotovo na "
        "klientskom boarde NEPATRI do per-stream memory (ostatne streamy ju "
        "nikdy neuvidia -- drift 'ako keby som uz jedneho neinstruoval'). Patri "
        "do JEDNEHO miesta pre vsetky streamy: "
        "`skills/odoo-client-messaging/client-board-tasks.md`.\n\n"
        "Navrhni upravu pravidla cez relay:\n"
        "  python3 ~/devel/airuleset/airuleset.py gk-request "
        "--repo zbynekdrlik/airuleset --title \"...\"\n\n"
        "Pre skutocne nesuvisiacu memory (loguje sa): "
        "`# airuleset:client-board-memory-ok DOVOD`."
    )
    return True, reason


# --------------------------------------------------------------------------- #
# Hook entry point
# --------------------------------------------------------------------------- #

def _payload_fields(payload):
    """``(content, file_path)`` from a hook payload: content is the Bash command
    / Write content / Edit new_string; file_path is the Write/Edit target. Never
    raises (a parse failure yields empty strings -> allow)."""
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    ti = obj.get("tool_input") or {}
    if not isinstance(ti, dict):
        return "", ""
    content = ti.get("command") or ti.get("content") or ti.get("new_string") or ""
    file_path = ti.get("file_path") or ""
    return content, file_path


def main():
    from gates import allow, emit_block, read_payload
    payload = read_payload()
    content, file_path = _payload_fields(payload)
    if content or file_path:
        blocked, reason = classify_client_body(content)
        if blocked:
            emit_block(reason)
        blocked, reason = classify_memory_write(file_path, content)
        if blocked:
            emit_block(reason)
    allow()


if __name__ == "__main__":
    main()
