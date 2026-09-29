"""gates.draftbundle -- ONE client draft per approval question (#1006 / #1177).

Owner rule (montalu 12.9. + montalu4 29.9.2026, „jedna správa / jedna otázka a
potom ďalšia, nie naraz"): a ❓ approval question shows exactly ONE client
message draft; the rest wait on their tickets and are shown after the answer.

The Stop hook `stop-check-question-quality.sh` extracts the delivered ❓ block
(`$BLOCK`, head..marker) and runs this module with the whole final message on
STDIN and the block in `AIRULESET_QQ_BLOCK`. Exit 2 + the split instruction on
STDOUT = a bundle; exit 0 = no bundle, or ANY error (FAIL-OPEN, the sibling
`gates.spec_question` / `gates.questionscope` direction -- a Stop gate must never
wedge a session on its own bug).

How a bundle is counted (`analyse`):
  * SCOPE -- when the block carries client-approval intent (`is_approval`), the
    whole message is read (the 29.9 stream put both drafts ABOVE the block,
    whose phone view is capped); otherwise only the block (the #1006 behaviour).
  * SIGNED drafts -- a signature-only line (`ZbynekAI`, `ZbynekAI 4`,
    `— ZbynekAI 4`, `<p>ZbynekAI 4</p>`, …) is one draft. A line that merely
    MENTIONS the signature in prose (an option bullet, a report line) is not.
    A signed draft under a header with a SENT cue („Predošlá správa (odoslaná
    včera)", „Včera odišli …") is context, not a proposal, and is skipped.
  * UNSIGNED drafts -- a `>`-quote / ``` fence run whose header (last <= 3
    lines of the paragraph above it) names a target: a `Vlákno:`/`Úloha:`/
    `Task:` line, or a thread/task URL next to a draft word („Návrh …"). A run
    headed by an INCOMING cue („Patrik napísal:") is the client's own message.
    Unsigned runs are grouped by shared target keys (thread id, task id, quoted
    thread name); a group sharing a key with a signed draft is the same
    conversation (e.g. the client's quote above our reply) and adds nothing.
  * drafts = signed + remaining unsigned groups; >= 2 is a bundle. So are >= 2
    `Text …` draft headers in scope, and >= 2 ❓ decision markers in the block.
Targets are read only from draft HEADERS, never from free briefing prose or a
draft body (a client text may link another record).
"""
import os
import re
import sys

_SIG_TOKEN = re.compile(
    r"^(?:(?i:s\s+pozdravom),?\s*)?ZbynekAI\s*(?:\d+|\(\s*[\w-]+\s*\))?\s*[.!]?$")
_HTML_SPLIT = re.compile(r"(?i)</?p\b[^>]*>|<br\s*/?>")
_HTML_TAG = re.compile(r"<[^>]+>")
_LEAD = re.compile(r"^[\s>]*(?:[-–—]\s*)?[*_\s]*")

_APPROVAL = re.compile(
    r"schv[aá][lľ](?:[ií]š|ite|i|enie|enia|ovanie)\b"
    r"|\b(?:po|odo)[sš]l(?:a[tť]|i|em|eme|ite)\b"
    r"|\bodpoveda[tť]|\bodpovedz|\bodp[ií]sa[tť]|\bnap[ií]sa[tť]"
    r"|\bpublikova[tť]|\bzverejni[tť]|\bs[úu]hlas[ií](?:š|te)\b"
    r"|\bklient|\bz[aá]kazn[ií]k|\btext|\bn[aá]vrh|\bdraft|\bspr[aá]v[auy]?\b|\bspr[aá]vami\b",
    re.I)
_SENT = re.compile(
    r"odišl|\b(?:po|odo)[sš]lal[a]?\s+som|predošl|predchádzaj|už\s+(?:bol[aoi]?\s+)?odoslan"
    r"|odoslan[áaéý]\s+(?:včera|dnes|ráno|\d)|\(odoslan[áaéý]", re.I)
_INCOMING = re.compile(
    r"\b(?:nap[ií]sal|p[ií]sal|odp[ií]sal|odpovedal|reagoval)a?\b|\bwrote\b|\bsp[ýy]tal[a]?\s+sa\b",
    re.I)
_TARGET_LINE = re.compile(r"^\s*[*_•-]*\s*(?:vl[áa]kno|[úu]loha|task)\s*[*_]*\s*:", re.I)
_DRAFT_WORD = re.compile(r"n[áa]vrh|draft|\btext|spr[áa]v|odpove[ďd]|verzi", re.I)
_CHANNEL = re.compile(r"discuss\.channel_(\d+)")
_TASK = re.compile(r"/odoo/project/\d+/tasks/(\d+)")
_QUOTED = re.compile(r"[„\"“]([^„\"“”]{1,80})[”\"“]")
_URL = re.compile(r"https?://\S+")
_TEXT_HDR = re.compile(r"^\s*\**\s*Text\s+(?:[úu]loh|pre\s|pro\s|\d)")
_DECISION = re.compile(r"❓\s*\**\s*(?:NEEDS\s+YOU|ASKED)")


def signature_count(line):
    """Number of signature-only parts in `line` (a `<p>…</p><p>ZbynekAI</p>`
    HTML line is split into parts first)."""
    n = 0
    for part in _HTML_SPLIT.split(line):
        core = _LEAD.sub("", _HTML_TAG.sub("", part)).strip().strip("*_").strip()
        if _SIG_TOKEN.match(core):
            n += 1
    return n


def is_approval(block):
    """True when the ❓ block asks to approve / send / answer a client text."""
    return bool(_APPROVAL.search(block or ""))


def _keys(lines):
    """Target keys named in header `lines`: thread ids, task ids, quoted names."""
    keys = set()
    for ln in lines:
        keys.update("ch:" + m for m in _CHANNEL.findall(ln))
        keys.update("task:" + m for m in _TASK.findall(ln))
        for name in _QUOTED.findall(ln):
            keys.add("name:" + " ".join(name.lower().split()))
        if _TARGET_LINE.match(ln) and not _QUOTED.search(ln):
            val = _URL.sub("", ln.split(":", 1)[1]).strip(" *_—–-")
            if val:
                keys.add("name:" + " ".join(val.lower().split()))
    return keys


class _Scan:
    """One pass over the scope text, collecting signed drafts and unsigned runs."""

    def __init__(self):
        self.signed = 0
        self.signed_keys = set()
        self.unsigned = []          # list of key sets, one per unsigned draft run
        self.ctx_keys = set()       # keys of the most recent targeted header
        self.run = None

    def _open(self, header):
        text = "\n".join(header)
        own = _keys(header)
        if own:
            self.ctx_keys = own
        evidence = any(_TARGET_LINE.match(h) for h in header) or (
            bool(_CHANNEL.search(text) or _TASK.search(text)) and bool(_DRAFT_WORD.search(text)))
        return {"sigs": 0, "own": own, "keys": own or set(self.ctx_keys),
                "sent": bool(_SENT.search(text)), "incoming": bool(_INCOMING.search(text)),
                "evidence": evidence}

    def _close(self, unit):
        if unit["sigs"]:
            if not unit["sent"]:
                self.signed += unit["sigs"]
                self.signed_keys |= unit["keys"]
        elif unit["evidence"] and not unit["incoming"] and not unit["sent"]:
            self.unsigned.append(set(unit["own"]))

    def feed(self, lines):
        para, prev_para, gap, fence = [], [], False, False
        for raw in lines:
            s = raw.strip()
            delim = s.startswith("```")
            body = fence or delim or s.startswith(">")
            if delim:
                fence = not fence
            if body:
                if self.run is None:
                    self.run = self._open(para[-3:])
                    para = []
                self.run["sigs"] += signature_count(raw)
                continue
            if not s:
                gap = True
                continue
            n = signature_count(raw)
            if self.run is not None:
                self.run["sigs"] += n          # a signature right after the run
                self._close(self.run)
                self.run, gap = None, False
                if n:
                    continue
                prev_para, para = para, []
            elif gap:
                prev_para, para = para, []
            gap = False
            if n:                              # a plain (unquoted) signed draft
                unit = self._open(prev_para[-3:] + para[:1])
                unit["sigs"] = n
                self._close(unit)
            para.append(raw)
        if self.run is not None:
            self._close(self.run)
            self.run = None

    def unsigned_groups(self):
        groups = []
        for keys in self.unsigned:
            merged = [g for g in groups if g & keys]
            for g in merged:
                groups.remove(g)
                keys = keys | g
            groups.append(keys)
        return [g for g in groups if not (g & self.signed_keys)]


def analyse(text):
    """Counts for `text`: signed drafts, extra unsigned drafts, total, headers."""
    lines = (text or "").split("\n")
    scan = _Scan()
    scan.feed(lines)
    extra = len(scan.unsigned_groups())
    return {"signed": scan.signed, "unsigned": extra, "drafts": scan.signed + extra,
            "texthdr": sum(1 for ln in lines if _TEXT_HDR.match(ln))}


def verdict(msg, block):
    """The split instruction when `msg`/`block` bundle client texts, else None."""
    scope = msg if is_approval(block) else block
    res = analyse(scope)
    dec = len(_DECISION.findall(block or ""))
    if res["drafts"] < 2 and res["texthdr"] < 2 and dec < 2:
        return None
    return (
        "Tvoja ❓ správa bundluje VIAC než jeden klientsky text / rozhodnutie "
        f"(podpisy ZbynekAI: {res['signed']}, „Text …\" hlavičky: {res['texthdr']}, "
        f"drafty s cieľom vlákno/úloha: {res['unsigned']}, ❓ rozhodnutia: {dec}) — počíta "
        "sa CELÁ správa, aj drafty NAD blokom. Owner pravidlo: JEDNA otázka = JEDEN "
        "klientsky text — ukáž PRVÝ draft teraz (jeden cieľ), zvyšné ZARAĎ DO FRONTY (na ich "
        "ticketoch, label needs-answer) a ďalší ukáž až po odpovedi. Alternatívnu verziu tej "
        "istej správy opíš v možnostiach, neukazuj ju ako druhý podpísaný text. Už ODOSLANÚ "
        "správu označ „odoslaná <kedy>\" v jej hlavičke. Rodinné batchovanie (#755) zoskupuje "
        "TIKETY deklaratívne, NIKDY viac klientskych textov v jednej otázke (#1006/#1177).")


def main():
    try:
        msg = sys.stdin.read()
        block = os.environ.get("AIRULESET_QQ_BLOCK", "")
        if not block.strip():
            return 0
        reason = verdict(msg, block)
    except Exception as exc:  # FAIL-OPEN: a gate bug never wedges a session
        print(f"draftbundle: not enforced ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0
    if reason:
        print(reason)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
