#!/usr/bin/env python3
"""Close-time Odoo Discuss closing-note gate — the PURE decision (#627,
owner directive 2026-08-22).

THE RULE. A ticket that binds an Odoo Discuss thread — recognised by a
line-anchored `Discuss-thread: <channel-id>` marker on the ticket (its body
OR any comment) — may be CLOSED only once it ALSO carries a disposition line:

  * `Discuss-closed: <message-id>`  — the closing note ("všetko vyriešené,
    tému uzatvárame") was posted into the thread; this is the LAST ticket
    bound to that thread, so the LAST message in the thread is now the
    sub-dev's; OR
  * `Discuss-defer: <reason — siblings #A #B still open>` — the closing note
    is DEFERRED to the LAST ticket because sibling tickets bound to the same
    thread are still open (the note goes once, at the last close, never N
    times).

Target property (owner): the LAST message in the thread is ALWAYS from the
sub-dev, written when the LAST ticket bound to the thread closes. WHO writes
it falls out of ownership at that moment — the obligation FOLLOWS THE TICKET
to its CURRENT owner / the closing hand, NEVER the author of the thread or
the ticket (the 2026-08-22 owner correction on #627: tickets move between
streams by topic; binding the obligation to the author would park it on a
stream that no longer owns the topic). This module never looks at authorship
— it reads only the ticket's own text at close time, so a ticket that moved
between streams is handled natively (the new owner's close faces the same
gate; the `Discuss-thread:` binding is a durable comment, orthogonal to the
mutable `stream:` label, so it survives the move — airuleset #627 STEP-0).

WHY a structured marker and not the free-text real convention ("vlákno 257 /
msg 1731437", airuleset #627 STEP-0): a free-text scan would be GUESSING
(false positives/negatives), which the ticket forbids ("bez hádania"). The
structured `Discuss-thread:` marker is the deterministic recognition, and it
is a COMMENT (not a label): a comment works at read/collaborator role where a
fork-derived stream cannot add a label (403 — airuleset #589), and it is
untouched by a cross-stream move.

WHAT THE GATE ACTUALLY KEYS ON (precise — the channel id is NOT consumed by
the code). This decision is PER TICKET: `is_thread_bound(text) and not
has_disposition(text)`. The gate reads only whether the `Discuss-thread:`
marker is PRESENT (a binding exists) and whether a disposition is present — it
never parses, groups, or compares the channel-id VALUE. The channel id inside
the marker is the HUMAN-READABLE correlation key: it lets a reviewer / the
owner see at a glance which tickets belong to the same thread and validate a
`Discuss-defer:`'s named siblings — it is not a machine group key. So the two
load-bearing properties rest on per-ticket text + a human-declared claim, NOT
on any code-level grouping: (a) cross-stream-move survival works because the
binding is a durable comment that no `stream:` re-label touches and the gate is
authorship-blind; (b) note-once (one thread, many tickets) works because the
CLOSER self-declares last-vs-non-last via `Discuss-closed:` (I am last, note
posted) vs `Discuss-defer:` (siblings remain), a falsifiable claim a human
reviews (the #516 model) — the gate never queries GitHub for sibling states
(that would be the forbidden heuristic layer).

FAIL DIRECTION. This gate's DEFAULT is ALLOW (most closes are not
thread-bound). So anything UNVERIFIABLE — malformed JSON, a non-object
payload, a missing field — ALLOWS, never blocks (`evaluate_close` returns
None). Recognition itself over-fires SAFE: a bound ticket with no disposition
BLOCKS (fail toward more scrutiny, airuleset #514). This is symmetric to the
sibling authority guard whose default is BLOCK: each keeps its own safe
default when it cannot verify. The `.sh` half applies the same direction to a
gh-fetch error and an unextractable issue number (fall through = allow).

INTERFACE. `evaluate_close(issue_json_text) -> None | str` — None ALLOWS the
close, a short reason string BLOCKS it. `issue_json_text` is the raw output
of `gh issue view <N> --json body,comments`. The CLI (`python3
discuss_close_guard.py`, stdin → stdout) prints "OK" or "BLOCK", which the
hook branches on. Stdlib only (this repo's convention).
"""

import json
import re
import sys

# Line-anchored markers. The leading class `[ \t>*#-]*` mirrors design_gate's
# own header regexes: it tolerates a marker indented under a bullet / block
# quote (`  - Discuss-thread: 257`) while a MID-SENTENCE mention ("see the
# Discuss-thread: convention") never matches (the sentence's leading words are
# not in the class, so `^…` fails). The trailing `[ \t]*\S` REQUIRES a real,
# non-empty value after the colon, so a bare `Discuss-thread:` (or one with
# only trailing spaces) is NOT a binding / NOT a disposition. Case-insensitive
# so a lower-cased marker still counts (recognition fails toward MORE scrutiny;
# a lower-cased disposition still satisfies the gate — the goal is to force the
# claim to be MADE, its truth is a review/falsifiability matter, #516). The
# `[ \t*]*` before the colon tolerates the common markdown bold-label form
# `**Discuss-thread**: 257` (colon OUTSIDE the emphasis), which the open-class
# alone would miss — again the over-recognition (safe) direction.
_MARK_OPEN = r"(?im)^[ \t>*#-]*"
_MARK_TAIL = r"[ \t*]*:[ \t]*\S"

_THREAD_RE = re.compile(_MARK_OPEN + r"Discuss-thread" + _MARK_TAIL)
_CLOSED_RE = re.compile(_MARK_OPEN + r"Discuss-closed" + _MARK_TAIL)
_DEFER_RE = re.compile(_MARK_OPEN + r"Discuss-defer" + _MARK_TAIL)


def collect_text(data):
    """Concatenate the issue body + every comment body into one newline-joined
    blob. Line-anchored markers are preserved because the join keeps line
    boundaries. Defensive against a non-dict comment / non-str body."""
    parts = []
    body = data.get("body")
    if isinstance(body, str):
        parts.append(body)
    comments = data.get("comments")
    if isinstance(comments, list):
        for c in comments:
            if isinstance(c, dict):
                cb = c.get("body")
                if isinstance(cb, str):
                    parts.append(cb)
    return "\n".join(parts)


def is_thread_bound(text):
    """True iff the ticket carries a `Discuss-thread: <value>` binding."""
    return bool(_THREAD_RE.search(text))


def has_disposition(text):
    """True iff the ticket carries a `Discuss-closed:` OR `Discuss-defer:`
    disposition line with a real value."""
    return bool(_CLOSED_RE.search(text) or _DEFER_RE.search(text))


def evaluate_close(issue_json_text):
    """Return None to ALLOW the close, or a short reason string to BLOCK it.

    Blocks IFF the ticket is thread-bound AND carries no disposition. Every
    unverifiable input (bad JSON, non-object payload) returns None (ALLOW) —
    the gate's safe default."""
    try:
        data = json.loads(issue_json_text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    text = collect_text(data)
    if is_thread_bound(text) and not has_disposition(text):
        return "thread-bound-no-closing-note"
    return None


def main():
    try:
        raw = sys.stdin.read()
        reason = evaluate_close(raw)
    except Exception:
        reason = None
    print("BLOCK" if reason else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
