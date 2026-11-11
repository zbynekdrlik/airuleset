"""gates.ownerdep -- #1307: an owner dependency written to a ticket is ASKED in the same turn.

THE INCIDENT (owner, 11.11.2026: "stale problem s otazkami ktore mi nie su
polozene!!!"). On 10.11. and 11.11. the dstack session recorded on dstack 141
that its next zvukar test "needs the owner's approval" / "needs the owner's OK"
/ "needs the owner's hands", never asked him, and set no U label -- the footer
counts only U labels and session `❓` pings, so the question was invisible.

THE CHECK. Dispatched from `hooks/stop-check-question-quality.sh` at its "not a
question turn" branch (a turn whose final message carries `❓ NEEDS YOU:` /
`❓ ASKED:` asked already), with the Stop payload on stdin. It reads THIS
TURN's tool calls (`gates.handover.turn_tool_calls`: after the last genuine
prompt; Stop-hook feedback never resets it) and parses each Bash segment with
the gate family's shell primitives (`gates.shellcmd` + `gates.selfservice` +
`gates.opswait._comment_body`). A turn OWES a question when a ticket write --
`gh issue comment|edit|create` (`--body`, `-b`, `--body-file`/`-F` file or
heredoc, `$(cat <<EOF)`; a body file the Write tool made earlier this turn is
read from the turn) or `gh api …/issues/N[/comments]` / `…/issues/comments/N`
with a `body=` field (`@file` too) -- states a FUTURE or NEEDED owner
dependency (`owner_dependency`). A failed call counts only when its result
shows the posted URL.

Settled by a `❓ NEEDS YOU:` / `❓ ASKED:` line in the final message, or a
successful add of a U label (`needs-answer` / `needs-decision` /
`needs-owner-action`) in the turn. Escape: a line `airuleset:owner-dep-ok
<reason>` (outside code fences) in the final message. Bypasses and blocks are
logged to `audits/owner-dep-gate.log`. Fail-OPEN on an unreadable transcript.

Known residual: a ticket already in U whose dependency is restated blocks once
(the bypass names it); `$VAR` bodies and nested `bash -c` are not resolved.

Dry-run:
  echo '{"last_assistant_message":"x","transcript_path":"/t.jsonl","cwd":"/r"}' \
    | python3 -m gates.ownerdep ; echo rc=$?
"""
import os
import re
import sys

from gates import audit, field_of, read_payload
from gates.handover import _all_values, _unwrap, turn_tool_calls
from gates.opswait import _FENCE_RE, _comment_body, _ticket_keys
from gates.selfservice import (
    _all_flag_values, _apply_cd, _capture_heredocs, _flag_value, _is_gh_issue_cmd, _tokens_of,
)
from gates.shellcmd import split_top_level

AUDIT_LOG = "owner-dep-gate.log"
U_LABELS = {"needs-answer", "needs-decision", "needs-owner-action"}

_BYPASS_RX = re.compile(r"^[ \t*-]*airuleset:owner-dep-ok[ \t]+(\S[^\n]{3,})", re.MULTILINE)
_ASKED_RX = re.compile(r"❓\s*\**\s*(?:NEEDS\s+YOU|ASKED)\s*\**\s*:", re.IGNORECASE)
_POSTED_RX = re.compile(r"issuecomment-\d+|/issues/\d+")
_API_ISSUE_RX = re.compile(r"(?:^|/)issues/(?:(\d+)(?:/comments)?|comments/\d+)/?(?:\?|$)")
_API_LABELS_RX = re.compile(r"(?:^|/)issues/(\d+)/labels/?(?:\?|$)")
_INLINE_CODE_RX = re.compile(r"`[^`\n]*`")
_SETTLED_RX = re.compile(r"ROZHODNUT[ÉE]")
_CLAUSE_CUT_RX = re.compile(r"[;:.,!?()—–]")
_NEGATIONS = {"not", "no", "never", "without", "longer", "nie", "ani", "netreba",
              "nikdy", "bez", "nemusí", "nemusi"}

# FUTURE / NEEDED forms only; a past or settled form ("owner-approved", "Owner
# approved", "approved by the owner", ROZHODNUTÉ) has none of these verbs.
_OWN = r"(?:(?:the|an?)\s+)?(?:owner|zbyn[eě]k)(?:['’]s|s['’]?)?"
_NOUN = (r"(?:approval|consent|decision|answer|ok|okay|go[- ]ahead|permission|"
         r"sign[- ]off|confirmation|input|hands|action|reply|response|choice)\b")
_SK_OWN = r"(?:tvoj\w*|ownerov\w*|owner\w*|zbyn\w*|vlastn[íi]k\w*|teba|tebe)"
_SK_NOUN = r"(?:s[úu]hlas\w*|schv[áa]len\w*|rozhodnut\w*|odpove\w*|povolen\w*|ok)\b"
_DEP_RX = re.compile("|".join((
    r"\b(?:needs?|needed|requires?|required|awaiting|awaits|pending)\s+%s\s+%s" % (_OWN, _NOUN),
    r"\b(?:wait(?:s|ing)?|blocked|parked|stuck)\s+(?:for|on)\s+%s\b" % _OWN,
    r"\b%s\s+(?:must|has\s+to|needs?\s+to|should|will\s+have\s+to)\s+(?:approve|decide|"
    r"answer|confirm|consent|sign\s+off|choose|pick|ok|okay)\b" % _OWN,
    r"\b%s\s+%s\s+(?:is\s+|still\s+)?(?:needed|required|pending|outstanding)\b" % (_OWN, _NOUN),
    r"\bpotrebuj(?:em|eme)\s+(?:%s\s+)?%s" % (_SK_OWN, _SK_NOUN),
    r"\bpotrebuj\w*\s+%s\s+%s" % (_SK_OWN, _SK_NOUN),
    r"\bpotrebuj\w*\s+%s\s+(?:od\s+)?(?:ownera|teba|zbyn\w*|vlastn[íi]ka)\b" % _SK_NOUN,
    r"\b[čc]ak(?:á|a|ám|am|áme|ame|ajú|aju|ať|at)\s+na\s+%s" % _SK_OWN,
    r"\b(?:owner|vlastn[íi]k|zbyn\w*)\s+mus[íi]\s+(?:schv|rozhod|ods[úu]hlas|potvrd|odpoved)\w*",
)), re.IGNORECASE)


def _negated(line, start):
    """A negation in the same clause just before the match ("does not need",
    "no longer needs", "netreba čakať na")."""
    prefix = _CLAUSE_CUT_RX.split(line[max(0, start - 60):start])[-1]
    words = re.findall(r"[\w'’]+", prefix.lower())[-4:]
    return any(w in _NEGATIONS or w.endswith(("n't", "n’t")) for w in words)


def owner_dependency(text):
    """The first line of `text` that states a future/needed owner dependency
    (fenced blocks, inline code, `>` quotes and ROZHODNUTÉ lines skipped), or ""."""
    for raw in _FENCE_RE.sub("", text or "").splitlines():
        line = _INLINE_CODE_RX.sub("", raw)
        if line.lstrip().startswith(">") or _SETTLED_RX.search(line):
            continue
        for m in _DEP_RX.finditer(line):
            if not _negated(line, m.start()):
                return line.strip()[:200]
    return ""


class _Turn:
    """Owner-dependency writes (ticket, line) and U-label adds seen this turn."""

    def __init__(self, cwd):
        self.cwd = cwd or os.getcwd()
        self.files, self.deps, self.labelled = {}, [], False

    def read(self, path, cwd):
        path = os.path.expanduser(str(path))
        full = os.path.normpath(os.path.join(cwd or "/", path))
        if full in self.files:
            return self.files[full]
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read(512_000)
        except OSError:
            return ""

    def wrote(self, ticket, body):
        line = owner_dependency(body)
        if line:
            self.deps.append((ticket, line))

    def gh_issue(self, tk, seg, cwd, file_bodies, direct_bodies, wrote, ok):
        verb = tk[2]
        labels = _all_flag_values(tk, ("--add-label",) if verb == "edit" else ("-l", "--label"))
        if ok and U_LABELS & {v.strip().lower() for v in labels}:
            self.labelled = True
        if not wrote:
            return
        bf = _flag_value(tk, ("-F", "--body-file"))
        body = None
        if bf and bf != "-" and bf not in file_bodies:
            body = self.read(bf, cwd)
        if not body:
            body = _comment_body(tk, seg, cwd, file_bodies, direct_bodies)
        keys = [] if verb == "create" else _ticket_keys(tk)
        self.wrote("#%s" % (keys[0] if keys else "new"), body or "")

    def gh_api(self, tk, cwd, wrote, ok):
        fields = _all_values(tk, ("-f", "-F", "--field", "--raw-field"))
        for tok in tk[2:]:
            lm = _API_LABELS_RX.search(tok)
            if lm and ok and any(f.partition("=")[0] in ("labels[]", "labels")
                                 and f.partition("=")[2].strip().lower() in U_LABELS
                                 for f in fields):
                self.labelled = True
            im = _API_ISSUE_RX.search(tok)
            if im and wrote:
                for f in fields:
                    name, _, val = f.partition("=")
                    if name == "body":
                        body = self.read(val[1:], cwd) if val.startswith("@") else val
                        self.wrote("#%s" % (im.group(1) or "?"), body)

    def scan_bash(self, cmd, ok, result):
        file_bodies, direct_bodies, skeleton = _capture_heredocs(re.sub(r"\\\r?\n", " ", cmd))
        cwd = self.cwd
        for path, text in file_bodies.items():
            self.files[os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))] = text
        wrote = ok or bool(_POSTED_RX.search(result or ""))
        for seg in split_top_level(skeleton):
            tk = _unwrap(_tokens_of(seg))
            if not tk:
                continue
            if tk[0] == "cd":
                cwd = _apply_cd(cwd, tk) or cwd
            elif _is_gh_issue_cmd(tk):
                self.gh_issue(tk, seg, cwd, file_bodies, direct_bodies, wrote, ok)
            elif tk[:2] == ["gh", "api"]:
                self.gh_api(tk, cwd, wrote, ok)


def bypass_reason(message):
    m = _BYPASS_RX.search(_FENCE_RE.sub("", message or ""))
    return m.group(1).strip() if m else None


def evaluate(transcript_path, message, cwd):
    """`("block", reason)` or `("allow", "")` for this turn."""
    if bypass_reason(message) or _ASKED_RX.search(message or ""):
        return "allow", ""
    calls = turn_tool_calls(transcript_path)
    if not calls:
        return "allow", ""
    turn = _Turn(cwd)
    for name, inp, ok, result in calls:
        if name == "Bash":
            turn.scan_bash(str(inp.get("command") or ""), ok, result)
        elif name == "Write" and isinstance(inp.get("file_path"), str):
            path = os.path.normpath(os.path.join(turn.cwd, inp["file_path"]))
            turn.files[path] = str(inp.get("content") or "")
    if not turn.deps or turn.labelled:
        return "allow", ""
    listing = "; ".join("%s: „%s“" % dep for dep in turn.deps[:3])
    return "block", (
        "🚫 BLOCKED (#1307) — tento ťah zapísal do tiketu závislosť na ownerovi, "
        "ale ownerovi si ju nepoložil: %s. Otázka zapísaná len na tikete sa nikam "
        "nedostane — footer U ju nevidí a na telefón nepríde. Sprav jedno z: "
        "(1) polož ju TERAZ celým blokom `**Otázka — projekt …:**` + možnosti + "
        "`❓ NEEDS YOU: <rozhodnutie>` (alebo `❓ ASKED:` + `⏳ WORKING`, ak pracuješ "
        "ďalej); (2) daj tiketu label `needs-answer` / `needs-decision` / "
        "`needs-owner-action` (`gh issue edit <N> --add-label …`); (3) ak súhlas "
        "podľa approval-scope.md netreba (testovanie rigu/zariadenia je samotná "
        "práca, owner si živý event stráži sám), závislosť z tiketu odstráň. "
        "Výnimka (zriedka, logované): vlastný riadok "
        "`airuleset:owner-dep-ok <dôvod>` v záverečnej správe." % listing)


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
