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
before a body edit) is a restatement, not a new dependency.

Settled by a DELIVERED question after the write -- an `❓ ASKED:` line or a
last-line `❓ NEEDS YOU:`, outside code (the notify hook's own delivery rule), in
the final message or in a text that ENDED an earlier stop of the same turn (it
is followed by Stop feedback / a notification, never by a tool call) -- or by a
successful add of a U label (`needs-answer` / `needs-decision` /
`needs-owner-action`) on that ticket in the turn (any U label settles an
unresolvable `#?` comment edit). Escape: a line `airuleset:owner-dep-ok
<reason>` (outside code fences) in the final message or in such an earlier
stop's text. Bypasses and blocks are logged to `audits/owner-dep-gate.log`.
Fail-OPEN on an unreadable transcript.

Known residual: a ticket already in U whose dependency is restated without a
read in the same turn blocks once (the bypass names it); `$VAR` bodies and
nested `bash -c` are not resolved; the regex is deliberately narrow (needed /
future verb forms only).

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
_GH_RX = re.compile(r"\bgh\s")
_READ_RX = re.compile(r"\bgh\s+(?:issue\s+view|api)\b(?![^\n]*(?:\bbody=|--input|\bgraphql\b|"
                      r"(?:-X|--method)\s*(?:POST|PATCH|PUT)))")
_ISSUE_URL_RX = re.compile(r"/issues/(\d+)")
_API_ISSUE_RX = re.compile(r"(?:^|/)issues/(?:(\d+)(?:/comments)?|comments/\d+)/?(?:\?|$)")
_API_LABELS_RX = re.compile(r"(?:^|/)issues/(\d+)/labels/?(?:\?|$)")
_CODE_OR_QUOTE_RX = re.compile(r"`[^`\n]*`|(?<!\w)\"[^\"\n]{1,120}\"(?!\w)|„[^“\n]*“|“[^”\n]*”")
_SKIP_LINE_RX = re.compile(r"^\s*>|^\W*ROZHODNUT[ÉE]|^\s*[-*]\s+\[[xX]\]")
_CLAUSE_CUT_RX = re.compile(r"[;:.,!?()—–]|\s(?:and|but|so|now|then|because|that|which|a|ale|aj|"
                            r"preto|takže|lebo|teraz)\s")
_NEGATIONS = {"not", "never", "nie", "ani", "netreba", "nikdy", "nemusí", "nemusi"}
_SETTLED = {"was", "were", "had", "previously", "bolo", "bol", "bola"}

# FUTURE / NEEDED forms only; a past or settled form ("owner-approved", "Owner
# approved", "approved by the owner", ROZHODNUTÉ) has none of these verbs.
_OWN = r"(?:(?:the|an?)\s+)?(?:owner|zby(?:ně|ň|nek(?!drlik))\w*)(?:['’]s|s['’]?)?"
_NOUN = (r"(?:approval|consent|decision|answer|ok|okay|go[- ]ahead|permission|sign[- ]off|"
         r"confirmation|input|hands|action|reply|response|choice|review)\b")
_SK_OWN = r"(?:tvoj\w*|ownerov\w*|owner\w*|zby(?:ně|ň|nek(?!drlik))\w*|vlastn[íi]k\w*|teba|tebe)"
_SK_OWNER_GEN = r"(?:od\s+)?(?:ownera|teba|zby(?:ně|ň|nek(?!drlik))\w*|vlastn[íi]ka)\b"
_SK_NOUN = r"(?:s[úu]hlas\w*|schv[áa]len\w*|rozhodnut\w*|odpove\w*|povolen\w*|ok)\b"
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
)), re.IGNORECASE)


def _cancelled(line, start):
    """A negation ("does not need", "no longer needs", "netreba čakať na") or a
    settled marker ("was blocked on", "previously parked on") in the same
    clause just before the match."""
    prefix = _CLAUSE_CUT_RX.split(line[max(0, start - 80):start])[-1]
    words = re.findall(r"[\w'’]+", prefix.lower())[-4:]
    return "no" in words[-2:] or any(
        w in _NEGATIONS or w in _SETTLED or w.endswith(("n't", "n’t")) for w in words)


def owner_dependency(text):
    """The first line of `text` that states a future/needed owner dependency, or
    "". Skipped: fenced blocks, inline code and quoted spans, `>` quotes,
    `ROZHODNUTÉ` records and ticked `- [x]` checklist lines."""
    for raw in _FENCE_RE.sub("", text or "").splitlines():
        if _SKIP_LINE_RX.search(raw):
            continue
        line = _CODE_OR_QUOTE_RX.sub("", raw)
        for m in _DEP_RX.finditer(line):
            if m.group(0)[:5].lower() == "until" or not _cancelled(line, m.start()):
                return raw.strip()[:200]
    return ""


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

    def read(self, path, cwd):
        full = os.path.normpath(os.path.join(cwd or "/", os.path.expanduser(str(path))))
        if full in self.files:
            return self.files[full]
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read(512_000)
        except OSError:
            return ""

    def wrote(self, ticket, body):
        line = owner_dependency(body)
        if line and not any(line in prior for prior in self.seen):
            self.deps.append([ticket, line, False])

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
        sub = _SUBST_FILE_RX.search(inline or "")
        if bf and bf != "-" and bf not in file_bodies:
            body = self.read(bf, cwd)
        elif sub:
            body = self.read(sub.group(1) or sub.group(2), cwd)
        else:
            body = _comment_body(tk, seg, cwd, file_bodies, direct_bodies)
        self.wrote(ticket, body or "")

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
                        body = self.read(val[1:], cwd) if val.startswith("@") else val
                        self.wrote(im.group(1) or "?", body)

    def scan_bash(self, cmd, ok, result):
        if "<<" not in cmd and not _GH_RX.search(cmd):
            return                                # cheap: nothing to record
        file_bodies, direct_bodies, skeleton = _capture_heredocs(re.sub(r"\\\r?\n", " ", cmd))
        cwd = self.cwd
        for path, text in file_bodies.items():
            self.files[os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))] = text
        if not _GH_RX.search(skeleton):
            return
        for seg in split_top_level(skeleton):
            if not (_GH_RX.search(seg) or seg.lstrip().startswith("cd")):
                continue
            tk = _unwrap(_tokens_of(seg))
            if not tk:
                continue
            if tk[0] == "cd":
                cwd = _apply_cd(cwd, tk) or cwd
            elif _is_gh_issue_cmd(tk):
                self.gh_issue(tk, seg, cwd, (file_bodies, direct_bodies), ok, result or "")
            elif tk[:2] == ["gh", "api"]:
                self.gh_api(tk, cwd, ok, result or "")

    def asked(self, text):
        if asked_in(text) or bypass_reason(text):
            for dep in self.deps:
                dep[2] = True


def bypass_reason(message):
    m = _BYPASS_RX.search(_FENCE_RE.sub("", message or ""))
    return m.group(1).strip() if m else None


def evaluate(transcript_path, message, cwd):
    """`("block", reason)` or `("allow", "")` for this turn."""
    if bypass_reason(message) or asked_in(message):
        return "allow", ""
    calls = turn_tool_calls(transcript_path, texts=True)
    if not calls:
        return "allow", ""
    turn, last_text = _Turn(cwd), None
    for name, inp, ok, result in calls:
        if name == "Bash":
            turn.scan_bash(str(inp.get("command") or ""), ok, result)
        elif name == "Write" and isinstance(inp.get("file_path"), str):
            path = os.path.normpath(os.path.join(turn.cwd, inp["file_path"]))
            turn.files[path] = str(inp.get("content") or "")
        if name == "_text":
            last_text = inp.get("text")
        elif name == "_resume":           # the text before it ended a stop: delivered
            turn.asked(last_text)
        else:
            last_text = None
        if name == "Bash" and _READ_RX.search(str(inp.get("command") or "")):
            turn.seen.append(result)
    open_deps = [(t, line) for t, line, asked in turn.deps if not asked
                 and t not in turn.labelled and not (t == "?" and turn.labelled)]
    if not open_deps:
        return "allow", ""
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
