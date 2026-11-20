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
2. The final message is judged FIRST, with no I/O. ``prose_words`` keeps only
   prose: fenced code (``` and ~~~, each closed by its own fence), ``> `` quotes
   and inline code are dropped. The text English is ALLOWED in (a ticket title
   ``#N (…)`` / ``#N — …`` / ``#N: …``, a table cell, a quoted string up to
   ``QUOTE_MAX`` chars, a timestamped log line) is dropped only when it carries
   no Slovak signal, so Slovak status written there still counts. A token
   carrying a URL, a path, an identifier character or a digit is skipped;
   ALL-CAPS acronyms / keywords and capitalised names in mid-sentence (Kežmarok,
   Spišská Nová Ves) count for neither language. Fewer than ``MIN_PROSE_WORDS``
   → allow. Mainly English = at least ``MIN_EN`` English stopwords and
   ``en >= EN_RATIO * sk``.
3. Only then the transcript is read BACKWARDS in ``CHUNK`` pieces, at most
   ``MAX_SCAN_BYTES`` and ``SCAN_BUDGET_S`` (never the whole file, #1224; Claude
   Code 2.1.3xx adds ~2 MiB of api-request records a minute, so the window is
   wide and a line is decoded only when a cheap byte test says it is a user
   entry or a queued command). The newest owner-side turn decides
   (``owner_language``): Stop-hook feedback (``isMeta``), a ``<task-notification>``,
   a ``<channel …>`` peer message or a compact summary → allow; a message the
   owner typed mid-turn (a queued command) counts as a prompt, a queued wake
   or machine text does not end the owner's turn. A prompt is judged by its
   first ``PROMPT_LEAD_WORDS`` words. A prompt with no language signal (``1``, an image,
   ``continue``, a machine nudge from ``machine_prompts``, a command echo, the
   Esc marker) takes the language of an older prompt.
4. A Slovak prompt + a mainly-English message → exit 2. The marker
   ``/tmp/airuleset-owner-language-block-<sid>`` (written as soon as the message
   reads English, removed on every allowed stop) tells
   ``notify-discord-pending.sh`` not to ping the rejected draft and never the
   accepted rewrite.

The heuristic is two-language only: Slovak diacritics and Slovak stopwords
(diacritics-free forms included, the owner types "som pri terminali") against
English stopwords; words both languages use count for neither. Every regex is
linear or bounded, quotes are cut with a find loop, the transcript reader is
linear in line length. Corpus replay before
shipping (the controller's main session, last 300 MB): 433 judged stops after a
Slovak prompt, 0 would-block. Recall on the real devbridge transcript is
unmeasured (another account). Accepted residuals: an UNFENCED English paste
around a Slovak question reads English (never judged); an English reply made only
of English titles, table cells, short quotes or log lines passes.

FAIL-OPEN: an unreadable payload or transcript, a prompt outside the window or
the time budget, any exception → journal (``owner-language-gate.log`` in the
audits dir) + exit 0.
Dry-run: echo '{"last_assistant_message":"…","transcript_path":"…"}' | python3 -m gates.ownerlang
STDLIB ONLY; never imports ``watchdog``.
"""
import json
import os
import re
import sys
import time

from gates import audit, read_payload
from gates.handover import entry_text
from machine_prompts import (DISCORD_ANSWER_PREFIXES, INTERRUPT_PREFIX,
                             MACHINE_PROMPT_EXACT, MACHINE_PROMPT_PREFIXES)

GATE_LOG = "owner-language-gate.log"
MARKER_DIR = "/tmp"                 # the literal dir notify-discord-pending.sh reads
MIN_PROSE_WORDS = 12
PROMPT_LEAD_WORDS = 40
QUOTE_MAX = 200
PROMPT_LOOKBACK = 5
MIN_EN = 4
EN_RATIO = 3
CHUNK = 65536
MAX_SCAN_BYTES = 32 * 1024 * 1024
SCAN_BUDGET_S = 2.0

SK_CHARS = frozenset("áäčďéíĺľňóôŕšťúýž")
# Diacritics-free forms on purpose: the owner types "som pri terminali", "cize
# co dalej?". A word with a Slovak diacritic counts anyway, so the list carries
# mostly the bare forms. Left out because English uses them too: a, to, do, on,
# so, no, by, ten, tie, pod, my, over, stale.
SK_STOP = frozenset("""
som si sa je sme ste su bol bola bolo boli bude budem budes budu ale aj ani
alebo len iba uz nie ano ak ked kde kedy preco co cize takze ako aby ze tak teda
este teraz potom znova znovu zase opat tu tam ta ti mi ma mu ho ty vy ona oni
toto tento tieto tohto mam mas mame maju nemam nemas nemame mozem mozes treba
chcem chces nechcem potrebujem viem neviem vies vie nevie musi musis mal mala
malo mali pri na v vo z zo k ku s o u za pre pred od po nad bez cez medzi okrem
podla asi lebo preto ktory ktora ktore ktori vsetko nic nieco velmi dobre podme
daj urob sprav skus pozri pozrime skusme urobme spravme robis riesis neries ide
nejde funguje nefunguje hotovo hotove slovensky jeho jej ich mne teba tebe tiez
hned dalej pise pises otazku spravu pokracuj zopakuj spoj prepoj pridaj odstran
vymaz skontroluj ukaz napis precitaj spusti zastav restartuj odloz odlozit
nechaj zober prosim dakujem diky vdaka
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

_WORD_RE = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)?")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_QUOTE_OPEN_RE = re.compile('["\u201c\u201e\u00ab]')
_QUOTE_CLOSE = {'"': '"', "\u201c": "\u201d", "\u201e": "\u201c\u201d", "\u00ab": "\u00bb"}
_REF_RE = re.compile(r"#\d{1,6}")
_JOINED_RE = re.compile(r"\w[.-]\w")    # x.py, pre-commit, e-mail
_JUNK_CHARS = frozenset("/\\_=@{}[]<>|$~")
_LOG_LINE_RE = re.compile(
    r"\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\[?\d{2}:\d{2}:\d{2}|Traceback \(")
_SENTENCE_END = frozenset(".!?:\u2026")
_TOKEN_TAIL = "\"')]}*»“”„_~`"
_USER_LINE_RE = re.compile(rb'"type"\s*:\s*"user"')
_TOOL_RESULT_RE = re.compile(rb'"type"\s*:\s*"tool_result"')
_REMINDER_OPEN, _REMINDER_CLOSE = "<system-reminder>", "</system-reminder>"
# owner text that a machine-prefix test would misread: a Discord-relayed answer,
# and Stop-hook output the owner pasted (real feedback is an isMeta entry)
_OWNER_TEXT_PREFIXES = DISCORD_ANSWER_PREFIXES + ("Stop hook feedback",)
_HUMAN_ORIGINS = frozenset(("user", "human", "prompt", "keyboard"))
_JOURNALLED = frozenset(("unreadable", "window", "budget"))

_REASON = (
    "🚫 BLOCKED (#1379) — owner píše po slovensky, ale táto správa je po anglicky. "
    "Napíš ju znova PO SLOVENSKY: všetku prózu aj stavové riadky. Po anglicky "
    "ostáva len kód, príkazy, cesty, presné chybové hlášky (v `…` alebo \"…\") a "
    "kľúčové slová značiek (`❓ NEEDS YOU`, `❓ ASKED`, `⏳ WORKING`, `✅ DONE`, "
    "`## ✅ Work Complete`); vyžiadaný anglický text (e-mail, návrh) daj do ``` "
    "bloku alebo `> ` citátu, tie sa nehodnotia.\n"
    "Owner writes Slovak: write this message in Slovak (English only in code, "
    "commands, paths, error strings and the status-marker keywords).")


class _OverBudget(Exception):
    """The backward scan ran past ``SCAN_BUDGET_S`` (the caller fails open)."""


def _note(text):
    """A diagnostic on stderr (a Stop hook's stderr reaches the transcript log,
    never the model, when it exits 0)."""
    try:
        sys.stderr.write("stop-check-owner-language: %s\n" % text)
    except (OSError, ValueError) as exc:     # a closed stderr: nothing left to tell
        del exc


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


def _has_sk(text):
    """True when ``text`` carries a Slovak word of two letters or more: a lone
    ``u`` / ``v`` / ``s`` is no evidence a cut-able segment is Slovak (``the U
    bucket``, a shell flag)."""
    return scores([w for w in _line_words(text) if len(w) > 1])[0] > 0


def _cut_refs(line):
    """``line`` without English ticket titles: ``#N (…)`` (balanced
    parentheses, at most 400 chars), ``#N — …`` / ``#N: …`` (to the end of the
    line or the next table cell). A title that carries Slovak stays (it is the
    owner-facing status, not a title written for other readers)."""
    out, pos = [], 0
    for m in _REF_RE.finditer(line):
        if m.start() < pos:
            continue
        k = m.end()
        while k < len(line) and line[k] == " ":
            k += 1
        nxt, end = line[k:k + 1], m.end()
        if nxt == "(":
            depth, limit = 0, min(len(line), k + 400)
            end = limit
            for j in range(k, limit):
                depth += {"(": 1, ")": -1}.get(line[j], 0)
                if depth == 0:
                    end = j + 1
                    break
        elif nxt and nxt in ":—–-" and line[k + 1:k + 2] in (" ", ""):
            cell = line.find(" | ", k)
            end = len(line) if cell < 0 else cell
        if end > m.end() and not _has_sk(line[k:end]):
            out.append(line[pos:m.start()])
            pos = end
    out.append(line[pos:])
    return "".join(out)


def _cut_quotes(line):
    """``line`` without English quoted strings (``"…"``, ``“…”``, ``„…“``,
    ``«…»`` up to ``QUOTE_MAX`` chars): an exact error string stays English. A
    longer quote, or one carrying Slovak, is prose and stays. A find loop, so
    an unpaired opener costs one bounded ``str.find``, never a regex backtrack."""
    out, pos = [], 0
    for m in _QUOTE_OPEN_RE.finditer(line):
        i = m.start()
        if i < pos:
            continue
        limit = min(len(line), i + 1 + QUOTE_MAX)
        ends = [j for j in (line.find(c, i + 1, limit) for c in _QUOTE_CLOSE[m.group()])
                if j >= 0]
        if not ends:
            continue
        end = min(ends)
        if not _has_sk(line[i + 1:end]):
            out.append(line[pos:i])
            out.append(" ")
            pos = end + 1
    out.append(line[pos:])
    return "".join(out)


def _line_words(line):
    """The lowercased prose words of one already-cut line."""
    words, initial = [], True
    for tok in line.split():
        found = _WORD_RE.findall(tok)
        core = tok.rstrip(_TOKEN_TAIL)
        ends = bool(core) and core[-1] in _SENTENCE_END
        skip = (_JUNK_CHARS.intersection(tok) or _JOINED_RE.search(tok)
                or any(c.isdigit() for c in tok))
        for i, w in enumerate(found if not skip else ()):
            low = w.replace("\u2019", "'").lower()
            if len(w) > 1 and w.isupper():
                continue                    # PC, SNV, CI, DONE, NEEDS YOU
            if (len(w) > 1 and w[0].isupper() and not (initial and i == 0)
                    and low not in SK_STOP and low not in EN_STOP):
                continue                    # a name in mid-sentence
            words.append(low)
        initial = ends if found else (initial or ends)
    return words


def _prose_lines(text):
    """The prose words of every judged line of ``text`` (a list per line)."""
    fence = None
    for line in (text or "").splitlines():
        lead = line.lstrip()
        if fence:
            if lead.startswith(fence):
                fence = None
            continue
        if lead.startswith("```") or lead.startswith("~~~"):
            fence = lead[:3]
            continue
        if lead.startswith(">"):
            continue
        line = _INLINE_CODE_RE.sub(" ", line)
        if lead.startswith("|"):
            line = " ".join(cell for cell in line.split("|") if _has_sk(cell))
        elif _LOG_LINE_RE.match(lead) and not _has_sk(line):
            continue
        yield _line_words(_cut_refs(_cut_quotes(line)))


def prose_words(text):
    """The lowercased prose words of ``text`` that the language is judged by."""
    return [w for line in _prose_lines(text) for w in line]


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
    """``"sk"`` / ``"en"`` / ``"unknown"`` for an owner prompt (no length floor:
    the owner writes "slovensky!!!"): the FIRST prose line with a decisive
    signal, within the first ``PROMPT_LEAD_WORDS`` words. The lead decides
    because an English instruction that carries pasted Slovak material is no
    Slovak prompt (corpus: an eval wrapper "You are the design author …" carried
    sk 66 / en 32 overall; "Please summarise the client's message …" + the
    Slovak message). A fenced or quoted paste leaves only the owner's own words;
    a "short Slovak last line" rule was dropped in review round 2 (it read
    English prompts ending in a shell line or a checklist item as Slovak)."""
    seen = 0
    for words in _prose_lines(_drop_reminders(text or "")):
        words = words[:PROMPT_LEAD_WORDS - seen]
        sk, en = scores(words)
        if sk != en:
            return "sk" if sk > en else "en"
        seen += len(words)
        if seen >= PROMPT_LEAD_WORDS:
            break
    return "unknown"


def _lines_backwards(path, max_bytes, chunk=CHUNK, deadline=None):
    """Raw transcript lines, newest first, from at most the last ``max_bytes``;
    the cut first line of the window is dropped. Raises OSError, or
    ``_OverBudget`` past ``deadline`` (a ``time.monotonic`` value)."""
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        floor = max(0, pos - max_bytes)
        tail = []          # chunks of the line read so far, newest chunk FIRST
        while pos > floor:
            if deadline is not None and time.monotonic() > deadline:
                raise _OverBudget()
            step = min(chunk, pos - floor)
            pos -= step
            fh.seek(pos)
            parts = fh.read(step).split(b"\n")
            if len(parts) == 1:          # no newline: still inside one long line
                tail.append(parts[0])
                continue
            tail.append(parts[-1])
            yield b"".join(reversed(tail))
            yield from reversed(parts[1:-1])
            tail = [parts[0]]
        if floor == 0 and tail:          # the file's first line (a cut one is dropped)
            yield b"".join(reversed(tail))


def _is_machine(lead):
    """Text that machinery, not the owner, typed (a nudge, a command echo, the
    Esc marker, a reminder)."""
    if lead.startswith(_OWNER_TEXT_PREFIXES):
        return False
    return (lead in MACHINE_PROMPT_EXACT or lead.startswith(MACHINE_PROMPT_PREFIXES)
            or lead.startswith(INTERRUPT_PREFIX) or lead.startswith("<"))


def _queued_turn(entry):
    """A message queued mid-turn (``attachment`` ``queued_command``): the owner's
    text is a prompt; a machine continuation or an absorbed notification is not
    a turn at all (None)."""
    att = entry.get("attachment")
    if not isinstance(att, dict) or att.get("type") != "queued_command":
        return None
    text = att.get("prompt")
    origin = att.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if not isinstance(text, str) or (kind and kind not in _HUMAN_ORIGINS):
        return None
    text = _drop_reminders(text)
    lead = text.lstrip()
    if not lead or _is_machine(lead):
        return None
    return "prompt", text


def _user_turn(entry):
    """``(kind, text)`` for a main-thread user entry, or None to keep walking
    back (a tool result, an ``isMeta`` skill body / caveat, a reminder only)."""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    if not isinstance(content, (str, list)):
        return None
    raw = entry_text(content)
    text = _drop_reminders(raw)
    lead = text.lstrip()
    if entry.get("isCompactSummary"):
        return "compact", ""
    if entry.get("isMeta"):
        return ("feedback", lead) if lead.startswith("Stop hook feedback") else None
    if not lead:
        return None if raw.strip() else ("prompt", "")    # reminder only / an image
    if lead.startswith("<task-notification>"):
        return "notification", lead
    if lead.startswith("<channel"):
        return "channel", lead
    return "prompt", "" if _is_machine(lead) else text


def user_turns(path, max_bytes=None, deadline=None):
    """``(kind, text)`` of each main-thread owner-side turn in the transcript
    tail, newest first. Raises OSError / ``_OverBudget`` (the caller fails open)."""
    lines = _lines_backwards(path, MAX_SCAN_BYTES if max_bytes is None else max_bytes,
                             deadline=deadline)
    try:
        for raw in lines:
            queued = b'"queued_command"' in raw
            if not queued and (not _USER_LINE_RE.search(raw) or _TOOL_RESULT_RE.search(raw)):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue                              # torn line / junk
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            kind = entry.get("type")
            turn = (_user_turn(entry) if kind == "user"
                    else _queued_turn(entry) if kind == "attachment" else None)
            if turn is not None:
                yield turn
    finally:
        lines.close()


def owner_language(path, max_bytes=None):
    """The language the owner is writing in, from the transcript tail: ``"sk"``
    / ``"en"`` / ``"unknown"``; the kind of a non-owner newest turn
    (``"feedback"`` / ``"notification"`` / ``"channel"`` / ``"compact"``); or a
    fail-open reason (``"unreadable"``, ``"window"``: no turn in the window,
    ``"budget"``: the scan ran out of time).

    A prompt with no language signal takes the language of the newest older
    prompt that has one, at most ``PROMPT_LOOKBACK`` prompts back and never
    across a compact summary (corpus: 44 of ~450 owner prompts were a bare
    number)."""
    newest, looked = True, 0
    deadline = time.monotonic() + SCAN_BUDGET_S
    try:
        for kind, text in user_turns(path, max_bytes, deadline):
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
    except _OverBudget:
        return "budget"
    except (OSError, TypeError, ValueError):
        return "unreadable"
    return "window" if newest else "unknown"


def evaluate(payload, on_english=None):
    """``("block", reason)`` or ``("allow", why)`` for one Stop payload dict.
    ``on_english`` runs once the message reads English, before the transcript
    is read (``run`` writes the block marker there, ahead of the notify hook's
    settle)."""
    if not isinstance(payload, dict):
        return "allow", "payload"
    if payload.get("stop_hook_active") is True:
        return "allow", "stop-hook-continuation"
    msg = payload.get("last_assistant_message")
    if not isinstance(msg, str) or not message_is_mainly_english(msg):
        return "allow", "message"
    if on_english is not None:
        on_english()
    tp = payload.get("transcript_path")
    if not isinstance(tp, str) or not tp:
        return "allow", "no-transcript"
    lang = owner_language(tp)
    if lang != "sk":
        return "allow", "owner-language:%s" % lang
    return "block", _REASON


def _journal(event, cwd, detail=""):
    """One audit line; never raises (a lost line never loses the verdict)."""
    project = os.path.basename(cwd.rstrip("/")) or "unknown"
    try:
        audit.append_line(GATE_LOG, "%s %s project=%s cwd=%s %s" % (
            audit.iso_now(), event, project, cwd, detail))
    except Exception as exc:                    # e.g. a read-only audits dir
        _note("journal write failed (%s): %s" % (event, exc))


def _marker_path(session_id):
    """The block marker for a session id, sanitised like the bash hooks'
    ``tr -cd 'A-Za-z0-9._-'``."""
    sid = re.sub(r"[^A-Za-z0-9._-]", "", session_id if isinstance(session_id, str) else "")
    return os.path.join(MARKER_DIR, "airuleset-owner-language-block-" + (sid or "unknown"))


def _clear_block(session_id):
    """Remove the marker on an allowed stop, so the accepted rewrite pings."""
    path = _marker_path(session_id)
    if os.path.lexists(path):
        try:
            os.unlink(path)
        except OSError as exc:
            _note("block marker not removed: %s" % exc)


def _mark_block(session_id):
    """The marker ``notify-discord-pending.sh`` reads to drop the ping of a
    rejected draft; a symlink planted in /tmp is never followed."""
    path = _marker_path(session_id)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        _note("block marker not written: %s" % exc)
        return
    try:
        os.write(fd, b"1")
    except OSError as exc:
        _note("block marker write failed: %s" % exc)
    finally:
        os.close(fd)


def run(payload_text):
    """0 = allow, 2 = block (reason on stderr). Never raises."""
    try:
        payload = json.loads(payload_text) if payload_text else None
    except ValueError:
        _journal("BADPAYLOAD", "", "%d bytes" % len(payload_text))
        return 0
    if not isinstance(payload, dict):
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    sid = payload.get("session_id")
    try:
        verdict, why = evaluate(payload, on_english=lambda: _mark_block(sid))
    except Exception as exc:                          # fail-open, but journalled
        _clear_block(sid)
        _journal("ERROR", cwd, "%s: %s" % (type(exc).__name__, str(exc)[:200]))
        return 0
    if verdict != "block":
        _clear_block(sid)
        reason = why.partition(":")[2]
        if reason in _JOURNALLED:
            _journal("UNREADABLE" if reason == "unreadable" else "WINDOW", cwd, why)
        return 0
    sys.stderr.write(why + "\n")
    sk, en = scores(prose_words(payload.get("last_assistant_message")))
    _journal("BLOCK", cwd, "sk=%d en=%d" % (sk, en))
    return 2


def main():
    sys.exit(run(read_payload()))


if __name__ == "__main__":
    main()
