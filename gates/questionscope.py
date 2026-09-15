"""gates.questionscope — the #1025 question-in-U Stop gate (gate-family #1020).

A THIN ADAPTER over ``cli_quals.question_ticket_in_u``. When an assistant turn
ends with a ``❓ ASKED`` / ``❓ NEEDS YOU`` marker AND names a same-repo ``#N``,
this checks that at least one of those tickets is in THIS box's ``U`` (owner-
court) surface — the owner's ONLY question surface since the #795 re-ask
retirement. If none is (the ``needs-answer``/``needs-decision``/
``needs-owner-action`` label never landed on ``#N``, or ``#N`` is out of this
box's scope / closed), the owner sees ``U 0`` / ``U N`` without the ticket and
has nowhere to click — the exact defect airuleset #1025 (odoo-erp#6883) fixes.

FAIL-OPEN by construction: a ticketless ping, a cross-repo-only reference, an
unmeasurable U set (no fresh cache + a gh error), or any parse failure all
ALLOW. The membership decision itself is cache-first (zero gh when fresh) with a
single gh-search fallback — see ``cli_quals.question_ticket_in_u``.

Invoked by ``hooks/stop-check-question-quality.sh`` as ``python3 -m
gates.questionscope`` with the Stop payload on stdin; exit 2 (reason on stderr)
= block, exit 0 = allow. The bash hook owns the per-session retry cap.
Dry-run: echo '{"last_assistant_message":"... ❓ ASKED: #5?","cwd":"/repo"}' | python3 -m gates.questionscope
"""
import os
import re
import sys

import gates

# A genuine question marker: ❓ ASKED: / ❓ NEEDS YOU: (bold/space tolerant),
# mirroring stop-check-question-quality.sh's own ASKED_RX / NEEDS_YOU_RX.
_MARKER_RE = re.compile(r"❓\s*\**\s*(ASKED|NEEDS\s+YOU)\s*\**\s*:", re.IGNORECASE)

# A bare same-repo `#N` — NOT a cross-repo `owner/repo#N` (a preceding slug
# char blocks the match, so `odoo-erp#6883` is excluded). 1-6 digits.
_BARE_REF_RE = re.compile(r"(?<![A-Za-z0-9_./-])#(\d{1,6})\b")
# A `#N` that is really a PULL REQUEST reference ("PR #5", "pull request #5") is
# NOT a subject ticket — exclude it (it never carries an owner-court label).
_PR_PREFIX_RE = re.compile(r"(?:\bPR|pull\s+request)\s*$", re.IGNORECASE)


def _bare_refs(msg):
    """Same-repo bare `#N` refs in `msg` — cross-repo `owner/repo#N` and
    `PR #N` / `pull request #N` references excluded."""
    msg = msg or ""
    out = set()
    for m in _BARE_REF_RE.finditer(msg):
        if _PR_PREFIX_RE.search(msg[max(0, m.start() - 16):m.start()]):
            continue
        out.add(int(m.group(1)))
    return out


def decide(payload, question_fn=None, u_count_fn=None):
    """Return ``(block: bool, reason: str)``. ``block`` is True ONLY when ALL of:
    (1) the turn is a genuine ❓ question, (2) it names a same-repo ``#N``,
    (3) this box's owner-court ``U`` is EMPTY per a readable cache
    (``user_waiting == 0`` — the exact reported symptom "U je 0"; None / >0 →
    allow, so a box with any visible owner question, and a box with no cache at
    all, are never gated), and (4) a live membership check confirms NONE of the
    named tickets is actually in U (verdict ``not_in_u`` — the label genuinely
    did not land). Every other state — U>0, no cache, ``in_u`` (the label DID
    land, cache just stale), ``unmeasurable`` — allows (fail-open). The U==0
    precondition is what keeps a legitimate question that merely REFERENCES a
    closed / other / PR ``#N`` for context from being blocked whenever the owner
    already has visible questions."""
    msg = gates.field_of(payload, "last_assistant_message", "")
    if not msg or not _MARKER_RE.search(msg):
        return False, ""                     # not a question turn
    refs = _bare_refs(msg)
    if not refs:
        return False, ""                     # ticketless / cross-repo / PR-only — not gated
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    # (3) owner court EMPTY per the cache? Only then is a named #N's absence the
    # reported "❓ but U 0" defect; U>0 (owner has clickable questions) or no
    # cache (unmeasurable) → allow.
    try:
        if u_count_fn is None:
            import statusbar
            u_count = statusbar.obligation_partition(cwd)[1]  # user_waiting
        else:
            u_count = u_count_fn(cwd)
    except Exception as e:  # noqa: BLE001 — a cache read failure never blocks
        sys.stderr.write("questionscope: U-count read errored (%s) — allowing "
                         "(fail-open)\n" % e)
        return False, ""
    if u_count != 0:                         # None (no cache) or >0 → allow
        return False, ""
    if question_fn is None:
        import cli_quals
        question_fn = cli_quals.question_ticket_in_u
    try:
        verdict = question_fn(sorted(refs), cwd)
    except Exception as e:  # noqa: BLE001 — never block a question on a bug
        sys.stderr.write("questionscope: membership check errored (%s) — "
                         "allowing (fail-open)\n" % e)
        return False, ""
    if verdict == "not_in_u":
        listed = ", ".join("#%d" % n for n in sorted(refs))
        reason = (
            "Otázka končí na ❓ ASKED/NEEDS YOU a menuje %s, ale žiadny z týchto "
            "ticketov NIE JE v `U` tohto boxu — owner vidí `U N` bez neho a nemá "
            "kam kliknúť. Príčina: label `needs-answer`/`needs-decision`/"
            "`needs-owner-action` na tom tikete NEPRISTÁL, alebo ticket nie je v "
            "scope tohto boxu (napr. cudzí stream / zatvorený). Oprav to: pridaj "
            "label na SPRÁVNY ticket (`gh issue edit <N> --add-label "
            "needs-answer`) alebo presuň otázku na FLOW-scope ticket, potom over "
            "`core-quals --waiting` / `slice-quals --waiting`. (#1025)" % listed)
        return True, reason
    if verdict == "unmeasurable":
        sys.stderr.write("questionscope: U membership unmeasurable (no fresh "
                         "cache + gh error) — allowing (fail-open, #1025)\n")
    return False, ""


def main():
    payload = gates.read_payload()
    block, reason = decide(payload)
    if block:
        gates.emit_block_stderr(reason)      # exit 2, reason on stderr
    gates.allow()                            # exit 0


if __name__ == "__main__":
    main()
