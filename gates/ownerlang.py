"""gates.ownerlang -- #1379: the owner writes Slovak, so the session answers in Slovak.

THE INCIDENT (owner, 20.11.2026, verbatim): "preco sa devbridge napriek uz
mesiace pozadovanemu pravidlu komunikovat so mnou slvoensky neustale prepina do
anglictiny?!" For hours devbridge answered every Slovak prompt in English. The
only always-on language rule (``user-questions-slovak.md``) covered QUESTIONS,
everything else a session reads is English by our own rules (tickets, commits,
peer messages), and no Stop hook read the language of the final message.

THE CHECK (``hooks/stop-check-owner-language.sh``, Stop payload on stdin):
1. ``stop_hook_active`` → allow: the turn already continues after a Stop-hook
   block, so this gate blocks at most ONCE per owner prompt and never wedges.
2. The final message is judged FIRST, with no I/O: ``prose_words`` drops fenced
   code, inline code, ``> `` quotes, the title in ``#N (…)``, every token that
   carries a URL, a path or an identifier character, and ALL-CAPS acronyms /
   status keywords. Fewer than ``MIN_PROSE_WORDS`` → allow. Mainly English =
   at least ``MIN_EN`` English stopwords and ``en >= EN_RATIO * sk``.
3. Only then the transcript is read BACKWARDS in ``CHUNK`` pieces, at most
   ``MAX_SCAN_BYTES`` (the family bound ``gates.handover.TAIL_BYTES``; never the
   whole file, #1224). The newest main-thread user entry that is not a tool
   result and not an ``isMeta`` body decides: Stop-hook feedback, a
   ``<task-notification>``, a ``<channel …>`` peer message or a compact summary
   → allow (not the owner's language); anything else is the owner's prompt,
   judged by its first ``PROMPT_LEAD_WORDS`` words. A prompt with no signal
   (``1``, ``continue``, a ``<command-name>`` echo) takes the language of an
   older prompt (``owner_language``).
4. A Slovak prompt + a mainly-English message → exit 2.

The heuristic is two-language only: Slovak diacritics and Slovak stopwords
(diacritics-free forms included, the owner types "som pri terminali") against
English stopwords; words both languages use count for neither. Every regex is
linear; the token filter is plain ``str`` work. Corpus replay before shipping
(the controller's main session, last 300 MB): 429 judged stops after a Slovak
prompt, 0 would-block.

FAIL-OPEN everywhere: an unreadable payload or transcript, any exception →
journal (``owner-language-gate.log`` in the audits dir) + exit 0.
Dry-run: echo '{"last_assistant_message":"…","transcript_path":"…"}' | python3 -m gates.ownerlang
STDLIB ONLY.
"""
import json
import os
import re
import sys

from gates import audit, read_payload
from gates.handover import TAIL_BYTES, _entry_text, _is_turn_boundary

GATE_LOG = "owner-language-gate.log"
MIN_PROSE_WORDS = 12
PROMPT_LEAD_WORDS = 40
PROMPT_LOOKBACK = 5
MIN_EN = 4
EN_RATIO = 3
CHUNK = 65536
MAX_SCAN_BYTES = TAIL_BYTES

SK_CHARS = frozenset("áäčďéíĺľňóôŕšťúýž")
# Diacritics-free forms on purpose: the owner types "som pri terminali", "cize
# co dalej?", "hotovo, over". A word with a Slovak diacritic counts anyway, so
# the list carries mostly the bare forms. Left out because English uses them
# too: a, to, do, on, so, no, by, ten, tie, pod, my, stale.
SK_STOP = frozenset("""
som si sa je sme ste su bol bola bolo boli bude budem budes budu ale aj ani
alebo len iba uz nie ano ak ked kde kedy preco co cize takze ako aby ze tak teda
este teraz potom znova znovu zase opat tu tam ta ti tu mi ma mu ho ty vy ona oni
mam mas mame maju nemam nemas nemame mozem mozes treba chcem chces nechcem
potrebujem viem neviem vies vie nevie musi musis mal mala malo mali pri na v vo
z zo k ku s o u za pre pred od po nad bez cez medzi okrem podla asi lebo preto
ktory ktora ktore ktori vsetko nic nieco velmi dobre podme daj urob sprav skus
pozri pozrime skusme urobme spravme robis riesis neries ide nejde funguje
nefunguje hotovo hotove slovensky jeho jej ich mne teba tebe tiez hned dalej
pise pises otazku spravu pokracuj zopakuj spoj prepoj pridaj odstran vymaz
skontroluj over ukaz napis precitaj spusti zastav restartuj odloz odlozit nechaj
zober prosim dakujem diky vdaka
""".split())
EN_STOP = frozenset("""
the and is are was were be been being this that these those with for of it its
you your i we our they their them he she his her have has had will would can
could should not but or if then than there here what which who when where why
how from at in into as an all any some just now already still also only does
did done my me about after before because while until right sorry let i'm it's
don't you're i'll can't won't let's that's we're isn't doesn't didn't i've
there's what's please thanks yes each other more most such very
""".split())

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_REF_TITLE_RE = re.compile(r"#\d{1,6}\s*\([^()\n]{0,300}\)")
_JOINED_RE = re.compile(r"\w[.-]\w")    # x.py, 0.8.82, pre-commit
_JUNK_CHARS = frozenset("/\\_=@{}[]<>|$~")
_TOOL_RESULT_RE = re.compile(rb'"type"\s*:\s*"tool_result"')
_REMINDER_OPEN, _REMINDER_CLOSE = "<system-reminder>", "</system-reminder>"

_REASON = (
    "🚫 BLOCKED (#1379) — owner píše po slovensky, ale táto správa je po anglicky. "
    "Napíš ju znova PO SLOVENSKY: všetku prózu aj stavové riadky. Po anglicky "
    "ostáva len kód, príkazy, cesty a presné chybové hlášky; vyžiadaný anglický "
    "text (e-mail, návrh) daj do ``` bloku alebo `> ` citátu, tie sa nehodnotia.\n"
    "Owner writes Slovak: write this message in Slovak (English only in code, "
    "commands, paths, error strings).")


def _drop_reminders(text):
    """``text`` without ``<system-reminder>…</system-reminder>`` blocks (an
    unclosed one runs to the end). A find loop, linear."""
    out, pos = [], 0
    while True:
        start = text.find(_REMINDER_OPEN, pos)
        if start < 0:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:start])
        end = text.find(_REMINDER_CLOSE, start)
        if end < 0:
            return "".join(out)
        pos = end + len(_REMINDER_CLOSE)


def prose_words(text):
    """The lowercased prose words of ``text`` that the language is judged by."""
    words, fenced = [], False
    for line in (text or "").splitlines():
        lead = line.lstrip()
        if lead.startswith("```") or lead.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced or lead.startswith(">"):
            continue
        line = _REF_TITLE_RE.sub(" ", _INLINE_CODE_RE.sub(" ", line))
        for tok in line.split():
            if "://" in tok or _JUNK_CHARS.intersection(tok) or _JOINED_RE.search(tok):
                continue
            for w in _WORD_RE.findall(tok):
                if len(w) > 1 and w.isupper():
                    continue                    # PC, SNV, CI, DONE, NEEDS YOU
                words.append(w.lower())
    return words


def scores(words):
    """``(sk, en)``: Slovak signals (a stopword or a Slovak diacritic) and
    English stopwords among ``words``."""
    sk = en = 0
    for w in words:
        if w in SK_STOP or not SK_CHARS.isdisjoint(w):
            sk += 1
        elif w in EN_STOP:
            en += 1
    return sk, en


def message_is_mainly_english(text):
    """True when ``text`` has enough prose to judge and that prose is English."""
    words = prose_words(text)
    if len(words) < MIN_PROSE_WORDS:
        return False
    sk, en = scores(words)
    return en >= MIN_EN and en >= EN_RATIO * sk


def prompt_language(text):
    """``"sk"`` / ``"en"`` / ``"unknown"`` for an owner prompt, judged by its
    first ``PROMPT_LEAD_WORDS`` prose words (no length floor: the owner writes
    "slovensky!!!"). The LEAD decides because a long English instruction that
    embeds a pasted Slovak ticket is not a Slovak prompt (corpus replay, an
    eval wrapper "You are the design author …" carried sk 66 / en 32 overall)."""
    words = prose_words(_drop_reminders(text or ""))[:PROMPT_LEAD_WORDS]
    sk, en = scores(words)
    if sk > en:
        return "sk"
    if en > sk:
        return "en"
    return "unknown"


def _lines_backwards(path, max_bytes=MAX_SCAN_BYTES, chunk=CHUNK):
    """Raw transcript lines, newest first, from at most the last ``max_bytes``;
    the cut first line of the window is dropped. Raises OSError."""
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        floor = max(0, pos - max_bytes)
        carry = b""
        while pos > floor:
            step = min(chunk, pos - floor)
            pos -= step
            fh.seek(pos)
            lines = (fh.read(step) + carry).split(b"\n")
            if pos > floor:
                carry, lines = lines[0], lines[1:]
            elif floor > 0:
                lines = lines[1:]
            yield from reversed(lines)


def _user_turn(entry):
    """``(kind, text)`` for a main-thread user entry, or None to keep walking
    back (a tool result, an ``isMeta`` skill body / caveat, a reminder)."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    if not isinstance(content, (str, list)):
        return None
    text = _entry_text(content)
    lead = text.lstrip()
    if lead.startswith("Stop hook feedback"):
        return "feedback", lead
    if entry.get("isCompactSummary"):
        return "compact", ""
    if entry.get("isMeta") or lead.startswith(_REMINDER_OPEN):
        return None
    if lead.startswith("<task-notification>"):
        return "notification", lead
    if lead.startswith("<channel"):
        return "channel", lead
    if lead.startswith("<"):
        return "prompt", ""          # a harness echo (`<command-name>`, local output)
    return "prompt", text if _is_turn_boundary(entry) else ""


def user_turns(path, max_bytes=MAX_SCAN_BYTES):
    """``(kind, text)`` of each main-thread user turn in the transcript tail,
    newest first. Raises OSError (the caller fails open)."""
    lines = _lines_backwards(path, max_bytes)
    try:
        for raw in lines:
            if b'"user"' not in raw or _TOOL_RESULT_RE.search(raw):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue                              # torn line / junk
            if (not isinstance(entry, dict) or entry.get("type") != "user"
                    or entry.get("isSidechain")):
                continue
            turn = _user_turn(entry)
            if turn is not None:
                yield turn
    finally:
        lines.close()


def owner_language(path, max_bytes=MAX_SCAN_BYTES):
    """The language the owner is writing in, from the transcript tail: ``"sk"``
    / ``"en"`` / ``"unknown"``, the kind of a non-owner newest turn
    (``"feedback"`` / ``"notification"`` / ``"channel"`` / ``"compact"``), or
    None when the transcript cannot be read.

    A prompt with no language signal (the owner's ``1`` picking an option,
    ``continue``, a slash command) takes the language of the newest older prompt
    that has one, at most ``PROMPT_LOOKBACK`` prompts back and never across a
    compact summary (corpus: 44 of ~450 owner prompts were a bare number)."""
    newest, looked = True, 0
    try:
        for kind, text in user_turns(path, max_bytes):
            if newest and kind != "prompt":
                return kind
            newest = False
            if kind == "compact":
                return "unknown"
            if kind != "prompt":
                continue
            lang = prompt_language(text)
            looked += 1
            if lang != "unknown" or looked >= PROMPT_LOOKBACK:
                return lang
    except (OSError, TypeError, ValueError):
        return None
    return None if newest else "unknown"


def evaluate(payload):
    """``("block", reason)`` or ``("allow", why)`` for one Stop payload dict."""
    if not isinstance(payload, dict):
        return "allow", "payload"
    if payload.get("stop_hook_active") is True:
        return "allow", "stop-hook-continuation"
    msg = payload.get("last_assistant_message")
    if not isinstance(msg, str) or not message_is_mainly_english(msg):
        return "allow", "message"
    tp = payload.get("transcript_path")
    if not isinstance(tp, str) or not tp:
        return "allow", "no-transcript"
    lang = owner_language(tp)
    if lang != "sk":
        return "allow", "owner-language:%s" % lang
    return "block", _REASON


def run(payload_text):
    """0 = allow, 2 = block (reason on stderr). Never raises."""
    try:
        payload = json.loads(payload_text) if payload_text else None
    except ValueError:
        return 0
    cwd = payload.get("cwd", "") if isinstance(payload, dict) else ""
    try:
        verdict, reason = evaluate(payload)
    except Exception as exc:                          # fail-open, but journalled
        audit.append_line(GATE_LOG, "%s ERROR cwd=%s %s: %s" % (
            audit.iso_now(), cwd, type(exc).__name__, str(exc)[:200]))
        return 0
    if verdict != "block":
        return 0
    sk, en = scores(prose_words(payload.get("last_assistant_message", "")))
    audit.append_line(GATE_LOG, "%s BLOCK project=%s cwd=%s sk=%d en=%d" % (
        audit.iso_now(), audit.project_of(cwd or None), cwd, sk, en))
    sys.stderr.write(reason + "\n")
    return 2


def main():
    sys.exit(run(read_payload()))


if __name__ == "__main__":
    main()
