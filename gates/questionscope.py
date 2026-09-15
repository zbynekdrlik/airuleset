"""gates.questionscope — the question-in-U + infra-routing + no-interim-
workaround Stop gate (#1025 / #1026 / #1027, gate-family #1020).

A THIN ADAPTER over ``cli_quals.question_ticket_in_u`` (plus a small local
lane-state check for #1027). When an assistant turn ends with a ``❓ ASKED`` /
``❓ NEEDS YOU`` marker it enforces THREE things:

#1027 — NO INTERIM WORKAROUND (owner directive, miva1 2026-09-14): a ❓ proposing
a client reply whose inline text carries workaround phrasing (``zatiaľ`` /
``medzitým`` / ``dovtedy`` / ``obísť`` / ``ručne`` / ``workaround``) while a
referenced same-repo ``#N`` still has an open implementation lane (the ticket is
OPEN — the lane closes it when the fix is on PROD) is BLOCKED: the stream replies
ONCE, after the fix is on PROD and verified. Bypass ``airuleset:client-reply-ok``
for the sanctioned exception (a yes/no question the client explicitly asked that
the fix does not answer). When workaround phrasing is present this branch owns
the one-gh-call budget and the #1025 U-membership check is not additionally run.

#1025 — when the turn names a same-repo ``#N``, at least one of those tickets
must be in THIS box's ``U`` (owner-court) surface — the owner's ONLY question
surface since the #795 re-ask retirement. If none is (the ``needs-answer``/
``needs-decision``/``needs-owner-action`` label never landed on ``#N``, or
``#N`` is out of this box's scope / closed), the owner sees ``U 0`` / ``U N``
without the ticket and has nowhere to click — the exact defect airuleset #1025
(odoo-erp#6883) fixes.

#1026 — an INFRA-caused release block is NEVER an owner question. Two triggers,
both blocking with the infra-routing reason (open/update the infra ticket + tag
``GATEKEEPER-ACTION (INFRA)`` on the hub; the #1029 rider wakes the INFRA
session; the owner is only INFORMED): (a) a zero-gh, U-INDEPENDENT TEXT-shape
trigger — the question text names a STRONG release-block token (``deploy-prod``
/ ``startup_failure`` / ``hotfix-main`` / ``release-fasttrack-exception``); bare
``fast-track`` prose is deliberately NOT a trigger (Item 1 — a non-infra
fast-track stays the owner's; see ``_is_release_block_shape``); (b) a named
same-repo ticket carrying the ``infra`` label (the ``infra`` verdict, folded
into the #1025 single gh call — the ROBUST path for an infra fast-track).
Owner ruling 14.9.2026 (odoo-erp gk FLOW, release 2.288): infra-caused blocks
are resolved WITH the infra session, not the owner.

FAIL-OPEN by construction: a ticketless ping, a cross-repo-only reference, an
unmeasurable U set (no fresh cache + a gh error), or any parse failure all
ALLOW. The membership decision itself is cache-first (zero gh when fresh) with a
single gh-search fallback — see ``cli_quals.question_ticket_in_u``.

Invoked by ``hooks/stop-check-question-quality.sh`` as ``python3 -m
gates.questionscope`` with the Stop payload on stdin; exit 2 (reason on stderr)
= block, exit 0 = allow. The bash hook owns the per-session retry cap.
Dry-run: echo '{"last_assistant_message":"... ❓ ASKED: #5?","cwd":"/repo"}' | python3 -m gates.questionscope
"""
import json
import os
import re
import subprocess
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

# #1026 — a release-block SHAPE in the question TEXT: an INFRA-caused release
# block is NEVER an owner question. A zero-gh, U-independent trigger that catches
# the actual incident (release 2.288, deploy-prod.yml startup_failure) with no gh
# call. These are the STRONG tokens — ones that only ever name an infra/CI/
# workflow-caused block: a `deploy-prod` dispatch failing on workflow/pool/gate
# breakage, a startup failure, a hotfix-main dispatch, or the fast-track MARKER
# artifact `release-fasttrack-exception`. Any one fires the infra route on its
# own. `\b` anchors keep `deploy-production` / `breakfast track…` from matching
# (#1026 review 🔵2).
#
# BARE `fast-track`/`fasttrack` PROSE is DELIBERATELY NOT a trigger (#1026 review
# 🟡1, BOTH reviewers): a non-infra fast-track decision reads identically to an
# infra one and Item 1 rules it stays the OWNER's, and a message-wide `infra`
# co-signal false-blocks even an explicit DENIAL of an infra cause ("žiadny infra
# problém … mám fast-track-núť?"). So a genuine infra-caused fast-track is caught
# by the STRONG marker/CI tokens above OR — the ROBUST path — the `infra` LABEL
# on the named ticket (the `infra` verdict below). The text heuristic is
# best-effort by design; a ticketless infra block with no strong token reaches
# the owner (the SAFE direction — asking is never harmful, mis-routing a real
# owner decision to infra is).
_RELEASE_BLOCK_STRONG_RE = re.compile(
    r"\bdeploy-prod\b|\bstartup_failure\b|\bhotfix-main\b|"
    r"\brelease-fasttrack-exception\b",
    re.IGNORECASE,
)


def _is_release_block_shape(msg):
    """True when the question text names an INFRA-caused release block via a
    STRONG infra/CI/marker token. Bare `fast-track` prose is intentionally NOT a
    trigger — see the block comment above (#1026 review 🟡1/🔵2, Item 1)."""
    return bool(_RELEASE_BLOCK_STRONG_RE.search(msg))

# The shared block reason for BOTH #1026 triggers (text-shape + infra label).
# Owner ruling 14.9.2026: infra-caused blocks are resolved WITH the infra
# session, not the owner — the owner is only INFORMED (✅/⏳), never ASKED.
_INFRA_REASON = (
    "Infra-vyvolaný release blok NIE JE otázka na ownera — otvor/aktualizuj "
    "infra tiket a tagni `GATEKEEPER-ACTION (INFRA)` na hube (#1029 rider zobudí "
    "INFRA session, ktorá vydá marker alebo opraví príčinu); ownera len INFORMUJ "
    "(✅/⏳), nepýtaj sa ho. Ak je blok naozaj NON-infra fast-track, marker ostáva "
    "ownerov — preformuluj otázku bez release-block shape / infra tiketu. (#1026)"
)


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


# #1027 — NO INTERIM WORKAROUND while a fix is in flight (owner directive, miva1
# 2026-09-14). The six phrasing tokens the owner named: a client reply that
# explains a manual/interim workaround. `\b` word anchors (Py3 `re` is unicode by
# default, so ľ/ť/í/ý are word chars) so the tokens match as words, case-
# insensitive.
_WORKAROUND_RE = re.compile(
    r"\b(zatiaľ|medzitým|dovtedy|obísť|ručne|workaround)\b", re.IGNORECASE)
# Bypass — the ONE sanctioned exception the owner allowed: a yes/no question the
# client EXPLICITLY asked that the fix does not answer. Mirrors the repo's
# `airuleset:<x>-ok` bypass convention; the block reason names it.
_WORKAROUND_BYPASS = "airuleset:client-reply-ok"
_WORKAROUND_REASON = (
    "❓ navrhuje klientovi INTERIM workaround (%(hit)s) kým je oprava %(refs)s "
    "ešte v behu — ticket je OTVORENÝ, teda fix ešte NIE je na PROD. Owner "
    "14.9.2026: kým beží fix lane, stream NEPOSIELA dočasnú náhradu ani návod "
    "ako to obísť — odpovie klientovi RAZ, až keď je oprava na PROD a overená "
    "(vtedy sa ticket zavrie). Nerob klientovi ručnú prácu, kým prichádza reálna "
    "oprava. VÝNIMKA: ak sa klient EXPLICITNE spýtal áno/nie a oprava to nerieši, "
    "pridaj `%(bypass)s` do správy. (#1027)"
)


def _default_lane_cache(cwd):
    """The box's cached open owner-court (`U`) issue numbers — a ref present here
    is provably OPEN, so a lane could be in flight (zero gh). Returns a set or
    None (no readable cache). Reads only, never spawns a refresh."""
    try:
        import statusbar
        nums, _ts = statusbar.user_waiting_numbers(cwd)
        return nums
    except Exception:  # noqa: BLE001 — a cache read failure just skips the fast path
        return None


def _default_lane_runner(argv, cwd):
    """Run ONE gh command in `cwd`; stdout on success, None on any failure/
    timeout — the fail-open signal `_client_report_lane_in_flight` needs (None =
    could not measure, "" / "[]" = measured empty)."""
    import airuleset
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=20, env=airuleset._gh_env())
        return r.stdout if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — any failure is "unmeasurable", never a block
        return None


def _client_report_lane_in_flight(numbers, cwd, *, runner=None, cache_fn=None):
    """#1027 — is a fix/feature lane for the client's report still IN FLIGHT?
    Operationalized as "a referenced same-repo `#N` is an OPEN issue": the lane
    closes the ticket when the fix is on PROD and verified, so an OPEN ticket ==
    the fix is not yet delivered == an interim workaround reply is banned, and a
    CLOSED ticket == the fix shipped == the how-to reply is now allowed. A `wip:`
    PR is a stronger form of the same in-flight state and is subsumed (it exists
    only while the ticket is open). Returns:

      "in_flight"    — at least one ref is an OPEN issue.
      "shipped"      — the state is determinable and NO ref is open (all closed).
      "unmeasurable" — a gh failure (FAIL-OPEN at the caller).

    COST (cache-first, ONE gh call at most): a ref in the box's cached `U` set is
    provably open (in_flight, ZERO gh); otherwise a SINGLE `gh issue list
    --state open` call, None → unmeasurable. No per-issue open-state cache exists,
    so the single gh call is the general measurement (mirrors
    `cli_quals.question_ticket_in_u`'s one-gh fallback)."""
    nums = set()
    for n in (numbers or []):
        try:
            nums.add(int(str(n).strip().lstrip("#")))
        except (TypeError, ValueError):
            continue
    if not nums:
        return "shipped"                     # nothing to check against
    cache_fn = cache_fn or _default_lane_cache
    try:
        cached = cache_fn(cwd)
    except Exception:  # noqa: BLE001 — a cache miss just falls through to gh
        cached = None
    if cached and (nums & set(cached)):
        return "in_flight"                   # a ref in U is provably open (zero gh)
    run = runner or _default_lane_runner
    out = run(["gh", "issue", "list", "--state", "open", "--json", "number",
               "-L", "500"], cwd)
    if out is None:
        return "unmeasurable"
    try:
        data = json.loads(out or "[]")
        open_nums = {int(x["number"]) for x in data
                     if isinstance(x, dict) and "number" in x}
    except (ValueError, TypeError, KeyError):
        return "unmeasurable"
    return "in_flight" if (nums & open_nums) else "shipped"


def decide(payload, question_fn=None, u_count_fn=None, lane_fn=None):
    """Return ``(block: bool, reason: str)``.

    #1027 (no interim workaround) is checked FIRST after ref-extraction, on its
    own gh budget (``lane_fn`` overridable for tests): when the turn carries
    workaround phrasing and a referenced open-lane ``#N`` is in flight it blocks;
    the #1025 path below is then not run. Otherwise the #1025 U-membership rule:
    ``block`` is True ONLY when ALL of:
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
    # (#1026) TEXT-shape trigger: an infra-caused release block named in the
    # question text is NEVER an owner question — block regardless of U, ZERO gh.
    # Runs BEFORE the ref/U checks so it fires even on a ticketless / cross-repo
    # release-block question (the FLOW session's own release-2.288 case).
    if _is_release_block_shape(msg):
        return True, _INFRA_REASON
    refs = _bare_refs(msg)
    if not refs:
        return False, ""                     # ticketless / cross-repo / PR-only — not gated
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    # (#1027) NO INTERIM WORKAROUND: a ❓ proposing a client reply that explains a
    # manual/interim workaround while a fix for a referenced open-lane #N is still
    # in flight is blocked (the stream replies ONCE, after the fix is on PROD).
    # Runs on its OWN gh budget: when workaround phrasing is present this branch
    # OWNS the one-gh-call ceiling and returns — the #1025 U-membership gh call is
    # NOT additionally made (skipping it here fails toward allow, the safe
    # direction). Independent of U (fires regardless of the owner-court count).
    if _WORKAROUND_RE.search(msg):
        if _WORKAROUND_BYPASS in msg:
            return False, ""                 # sanctioned client yes/no exception
        lane = (lane_fn or _client_report_lane_in_flight)(sorted(refs), cwd)
        if lane == "in_flight":
            hit_m = _WORKAROUND_RE.search(msg)
            hit = hit_m.group(1) if hit_m else "workaround"
            listed = ", ".join("#%d" % n for n in sorted(refs))
            return True, _WORKAROUND_REASON % {
                "hit": hit, "refs": listed, "bypass": _WORKAROUND_BYPASS}
        if lane == "unmeasurable":
            sys.stderr.write("questionscope: workaround lane-state unmeasurable "
                             "(gh error) — allowing (fail-open, #1027)\n")
        return False, ""                     # shipped / unmeasurable → allow
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
    if verdict == "infra":                   # #1026 — infra lane, not owner court
        return True, _INFRA_REASON
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
