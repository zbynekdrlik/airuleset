"""gates.filing.__main__ -- orchestrator entry for the ungated-issue-filing gate
(#1020 Part 2), run by the thin bash adapter as ``python3 -m gates.filing``.

Reproduces EXACTLY what hooks/block-ungated-issue-filing.sh's bash prologue +
epilogue did around the (now-extracted) classifier: the #842 worker (subagent)
hard-block, the cheap pre-filter, the ``airuleset:scope-gate-ok`` bypass, the
presence read, the per-segment classify loop (VERBATIM from the heredoc, the ONE
change being ``resolve_body`` taking cmd/file_bodies/direct_bodies as explicit
args + the invalid-criterion reason string factored to ``_invalid_criterion_reason``
so no function exceeds the 300-line ratchet), the scope-gate.log write in the
exact field order, and the exit-2 block render. Behaviour is pinned by
tests/test_scope_gate.py + the char pins.

The #988(g) ``log_hook_block`` measurement stays in the adapter (the hook
boundary), which also wraps this module fail-CLOSED (a non-0/2 exit -> exit 2).
"""
import os
import re
import sys

from gates import audit, command_of, field_of, read_payload
from gates.filing import render
from gates.filing.parse import (
    ACK_REACTION_CITE_RE, ALLOWED, AREA_RE, CLIENT_MSG_ORIGIN_RE, CRITERION_RE,
    DEDUP_RE, EXEMPT_FROM_CAP, LOC_NUM_RE,
    extract_heredocs, flag_value, is_api_issues_post, is_issue_create,
    resolve_body, split_top_level, strip_prefix, tokens_of,
    _all_labels, _apply_cd, _chain_parent, _chain_parents, _clean_field,
    _no_field_decoy, _target_repo_for_segment,
)
from gates.filing.caps import (
    CHAIN_WIDTH_CAP, DAILY_CAP, cwd_repo_of,
    _gh_view_text, _log_pass_count, _near_duplicate, _ratchet_should_block,
    _stream_routing_block_reason, _today_str,
)
from gates.filing.presence import is_away, _dismissal_word, _has_recent_owner_quote

# #842 worker (subagent) create-shape detection -- a per-LINE match, mirroring
# the bash hook's `grep -qE` (grep scans line by line; re.MULTILINE gives `^`/`$`
# the same line-boundary meaning). Horizontal-space classes ([ \t]) match
# `[[:space:]]` within a single line (grep never sees a newline inside a line).
_WORKER_CREATE_RE = re.compile(r"gh[ \t]+issue[ \t]+create")
_WORKER_API_RE = re.compile(r"gh[ \t]+api")
_WORKER_POST_RE = re.compile(
    r"(-X[ \t]*POST|--method[ \t]+POST|-XPOST|(^|\s)-f(\s|$)|(^|\s)-F(\s|$)|"
    r"--field|--raw-field|--input)", re.MULTILINE)


def _is_worker_filing(cmd):
    """True when `cmd` is a genuine issue-CREATE shape (gh issue create / a
    `gh api …/issues` WRITE / a `gh api graphql … createIssue` mutation) --
    VERBATIM logic from the bash #842 worker block. A bare GET read has none of
    the POST signals and returns False."""
    if _WORKER_CREATE_RE.search(cmd):
        return True
    if _WORKER_API_RE.search(cmd):
        if "createIssue" in cmd:
            return True
        if "issues" in cmd and _WORKER_POST_RE.search(cmd):
            return True
    return False


def _prefilter_pass(cmd):
    """The bash pre-filter: only classify a command that could plausibly carry
    a gh issue-creation call -- `*"issue create"*` OR `*"gh api"*"issues"*`."""
    if "issue create" in cmd:
        return True
    i = cmd.find("gh api")
    if i != -1 and cmd.find("issues", i) != -1:
        return True
    return False


def _invalid_criterion_reason(body, body_err, crit):
    """The concrete, decoy-neutralised BLOCK reason for a segment whose
    Scope-gate line is missing/unresolvable/invalid -- VERBATIM from the bash
    classifier's `elif not (crit ... in ALLOWED)` branch (#483/#802: no branch
    may ever render the opaque `-> none`; attacker-derived crit / `-F` token
    neutralised). Returns the reason string; the caller appends the BLOCK."""
    # unchanged from before #329 -- missing/invalid Scope-gate blocks
    # here, BEFORE the new dedup/cap checks below (keeps every
    # pre-existing test's block reason unaffected). #483: when the body
    # was UNRESOLVED because a `-F` disk path could not be read, surface
    # that explicit, actionable reason instead of the opaque `-> none`.
    # #483-review 🔴: _clean_field is MANDATORY here -- body_err embeds
    # the attacker-controlled `-F` token / cwd, and an embedded tab or
    # newline would otherwise forge a second record (a `verdict=PASS`
    # for an arbitrary repo) in the tab-separated hand-off to bash /
    # scope-gate.log, re-opening the #329 field-injection.
    #
    # #802: EVERY branch of this block must emit a CONCRETE reason -- an
    # empty criterion string (which the print loop renders as the opaque
    # `-> none`) is itself a defect. The montalu1 incident: a body
    # carrying `Scope-gate: user-request` inside a `--body "$(printf
    # ...)"` command-substitution had no newline-anchored `Scope-gate:`
    # LINE for CRITERION_RE, so crit=None; #483 only filled the reason
    # for a `-F` disk-path failure (body_err set), leaving the two
    # crit=None holes (body resolved but no line; body unresolvable with
    # no body_err) rendering `-> none` -- an undiagnosable block. Each
    # gets a concrete reason now:
    # #802-review 🟡: the `criterion=` field on these BLOCK lines is FREE
    # TEXT (already so since #483's body_err), so the #329 log-field
    # invariant "every counting field is written BEFORE the two free-text
    # fields" no longer holds in the LETTER for BLOCK lines -- a crafted,
    # ATTACKER-influenced crit / `-F` token like `x-parents=999` (a legal
    # `\S+` token) would decoy a later `\bparents=(\S+)`/`\bsession=(\S+)`
    # first-match with a forged value. Harmless TODAY only because
    # `_log_pass_count` (the ONLY counting consumer) filters `verdict=PASS`
    # FIRST and BLOCK lines are never counted. Defence-in-depth so a future
    # counting change over non-PASS lines can never re-open #329:
    # `_no_field_decoy` neutralises `=` to `:` in the two ATTACKER-derived
    # substrings only (crit, and the `-F` token embedded in body_err),
    # never in the author-controlled literal hints below (their fixed
    # `body=` is legit gh syntax and is not a counting-field name anyway).
    if body is None:
        # body could not be resolved: an unreadable `-F` disk path gives
        # the explicit #483 reason (attacker `-F` token neutralised); any
        # other unreadable shape (no -F/--body, an unresolvable heredoc, a
        # command-substitution / $VAR body a static PreToolUse scan cannot
        # execute) gets a concrete body-unresolved reason instead of the
        # empty `-> none`. #802-review 🔵: this branch is ALSO reached by a
        # `gh api ...issues` POST with no `body=` field, where `-F body.md`
        # / `--body` are the wrong flags -- name BOTH filing recipes.
        reason = _no_field_decoy(_clean_field(body_err)) if body_err else (
            "body-unresolved -- could not read the issue body from this "
            "command (no readable body; a $(...) / $VAR body cannot be read "
            "at PreToolUse -- write it to a file via a heredoc `cat > "
            "body.md <<'EOF' ... EOF` then `-F body.md`, or pass it inline: "
            "`--body \"...\"` for `gh issue create`, `-f body=...` / `-F "
            "body=@body.md` for `gh api`)")
    elif crit is None:
        # body IS readable but carries no `Scope-gate: <criterion>` line
        # at all -- the plain missing-line case.
        reason = "no-scope-gate -- body carries no `Scope-gate: <criterion>` line"
    else:
        # crit present but not one of ALLOWED -- name the bad (attacker-
        # written) value, decoy-neutralised.
        reason = "invalid-scope-gate:%s" % _no_field_decoy(_clean_field(crit))
    # Defensive: no BLOCK may ever carry an empty reason (would print as
    # `-> none`). Every branch above yields a non-empty string, but pin
    # it so a future edit cannot silently re-open the opaque block.
    reason = _clean_field(reason) or "unspecified-block"
    return reason

def _write_log(log_path, results, has_block, sid):
    """Append every result to ~/.claude/scope-gate.log in the exact field order
    the bash epilogue used (every COUNTING field before the two free-text
    fields), best-effort. A PASS in a batch that ALSO blocked is logged
    NOTFILED so it never charges cap budget (#329 phantom-PASS)."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            for verdict, title, crit, parents_str, target_repo, dedup_claim in results:
                log_verdict = "NOTFILED" if (has_block and verdict == "PASS") else verdict
                fh.write('%s  verdict=%s  repo=%s  criterion=%s  session=%s  '
                         'parents=%s  title="%s"  dedup="%s"\n' % (
                             audit.iso_now(), log_verdict, target_repo,
                             crit or "none", sid, parents_str or "none",
                             title, dedup_claim or "none"))
    except OSError:
        pass


def classify_command(cmd, sid, cwd, repo_dir, log_path, unattended):
    """Run the per-segment classifier over `cmd` and return the list of result
    tuples (verdict, title, reason_or_crit, parents_str, target_repo, dedup).
    VERBATIM the bash classifier's heredoc/segment loop; the caller writes the
    log + renders + exits."""
    file_bodies, direct_bodies, skeleton = extract_heredocs(cmd)
    results = []  # (verdict, title, criterion_or_none, parents_str, target_repo, dedup_claim)

    # cwd-derived repo resolved BEFORE the loop as the caps' FALLBACK target.
    cwd_repo = cwd_repo_of(cwd)

    # Per-BATCH running counters (a batch can file several issues in one call
    # before anything is written to the log).
    _local_day_count = {}       # target_repo -> count
    _local_parent_count = {}    # (target_repo, parent) -> count
    _batch_titles_by_repo = {}  # target_repo -> [title, ...] already PASSed

    # #483 -- the effective cwd a relative `-F` body path resolves against,
    # folded forward by every leading `cd <dir>` segment.
    effective_cwd = cwd

    for seg in split_top_level(skeleton):
        if not seg.strip():
            continue
        tk = strip_prefix(tokens_of(seg))
        # #483 -- a `cd` segment changes where a later relative `-F` lives; fold
        # it into effective_cwd (unknowable target -> None, degrades to the
        # explicit not-readable reason) and move on -- it is never a filing.
        if tk and tk[0] == "cd":
            effective_cwd = _apply_cd(effective_cwd, tk)
            continue
        api_call = is_api_issues_post(tk)
        if not (is_issue_create(tk) or api_call):
            continue
        title = flag_value(tk, ("-t", "--title"))
        if title is None and api_call:
            for idx, t in enumerate(tk):
                if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk) \
                        and tk[idx + 1].startswith("title="):
                    title = tk[idx + 1][len("title="):]
                    break
        title = title or "(no title)"
        body, body_err = resolve_body(tk, seg, api_call, effective_cwd, file_bodies, direct_bodies, cmd)
        crit = None
        if body:
            # #1003 -- scan ALL `Scope-gate:` lines and PREFER one whose token is
            # an accepted criterion. A prose self-audit line
            # (`Scope-gate: >300 LoC? nie; schema? nie; …`) yields a non-criterion
            # first token (`>300`); a proper `Scope-gate: <criterion>` line
            # elsewhere in the body must win over it. Fall back to the FIRST token
            # (for the `invalid-scope-gate:<token>` block reason) when none of the
            # lines names an accepted criterion.
            _first = None
            for _m in CRITERION_RE.finditer(body):
                _tok = _m.group(1)
                if _first is None:
                    _first = _tok
                if _tok.lower() in ALLOWED:
                    crit = _tok
                    break
            if crit is None:
                crit = _first

        repo_flag = flag_value(tk, ("-R", "--repo"))
        target_repo = _target_repo_for_segment(tk, api_call, cwd_repo)

        # #311 -- a review-finding follow-up whose own PARENT is ITSELF such a
        # follow-up is a depth-2 review-finding chain -- a self-reinforcing
        # sequence the follow-up gate's PER-ISSUE criterion cannot see, since
        # each individual hop can honestly claim its own criterion. Cheap text
        # match first; a bounded `gh` call only fires once a candidate parent
        # is actually named -- EVERY candidate is tried (finding F7: a decoy
        # reference earlier in the text must not hide the real one), each
        # checked with the backward-reference filter (finding F2: a root
        # ticket linking its own spawned children is not itself chained), in
        # the filing's OWN explicit repo when one is given (finding F8).
        parents = _chain_parents((title or "") + "\n" + (body or ""))
        parents_str = ",".join(parents) if parents else "none"
        chain_capped = False
        for parent in parents:
            parent_text = _gh_view_text(parent, cwd, repo=repo_flag)
            if parent_text is not None and _chain_parent(parent_text, own_number=parent):
                chain_capped = True
                break

        # #311 point 3 -- a `>300-loc` claim whose own body confesses a
        # <=300 number next to "loc"/"lines" is self-contradicting; checked
        # ONLY for this one criterion, ONLY when a number is actually stated.
        # EVERY stated number must clear 300, not just the first one found
        # (finding F1, TRIGGERED live: a body honestly quoting the follow-up
        # gate's OWN threshold text before stating its real, genuinely-large
        # count -- "under ~100 LoC ... roughly 620 LoC across 5 modules" --
        # matched the FIRST number and false-blocked exactly the author being
        # most honest about clearing the gate).
        loc_mismatch = False
        if crit and crit.lower() == ">300-loc" and body:
            nums = [int(x) for x in LOC_NUM_RE.findall(body)]
            if nums and max(nums) <= 300:
                loc_mismatch = True

        # #390 -- computed unconditionally per segment, same tier as
        # chain_capped/loc_mismatch above (a routing-correctness question, not
        # a scope-gate-criterion one) -- see this file's header for the full
        # design and scoping.
        stream_reason = _stream_routing_block_reason(
            tk, api_call, body, cwd, target_repo, repo_dir)

        # #802 adversarial-review 🔵: a whitespace-only `-t` title cleans to ""
        # -- an EMPTY field, which bash's `IFS=$'\t' read` collapses (line ~1220
        # comment), shifting every field after it. `title = title or "(no
        # title)"` at line ~1028 only catches a MISSING/empty title, not a
        # whitespace-only one (truthy). Pin a non-empty clean title at the
        # source so both the tab-joined print AND the log echo stay aligned.
        clean_title = _clean_field(title) or "(no title)"

        if chain_capped:
            results.append(("BLOCK", clean_title, "chain-depth-cap", parents_str,
                             target_repo, ""))
        elif loc_mismatch:
            results.append(("BLOCK", clean_title, "loc-mismatch", parents_str,
                             target_repo, ""))
        elif stream_reason:
            results.append(("BLOCK", clean_title, stream_reason, parents_str,
                             target_repo, ""))
        elif not (crit and crit.lower() in ALLOWED):
            # missing/invalid Scope-gate -- the concrete, decoy-neutralised reason
            # (body-unresolved / no-scope-gate / invalid-scope-gate:<tok>) is built
            # by _invalid_criterion_reason; #483/#802 shapes preserved there.
            results.append(("BLOCK", clean_title,
                             _invalid_criterion_reason(body, body_err, crit),
                             parents_str, target_repo, ""))
        else:
            crit_l = crit.lower()
            # #993 -- an architecture-rework ticket is deduped BY AREA (one open
            # rework ticket per area), so its body MUST carry an `Area:` line naming
            # the reworked area. Unconditional (attended or not) -- it is this
            # criterion's structural discipline, the same tier as the >300-loc
            # self-contradiction check, and BLOCKS before the presence/cap gates.
            if crit_l == "architecture-rework" and not AREA_RE.search(body or ""):
                results.append(("BLOCK", clean_title,
                                 "architecture-rework-missing-area (body must carry "
                                 "an `Area:` line naming the reworked area -- one open "
                                 "rework ticket per area)",
                                 parents_str, target_repo, ""))
                continue
            # #993-review: an architecture-rework filing MUST also carry the
            # `-l architecture-rework` LABEL (mirrors #962's -l needs-gatekeeper
            # requirement) -- the picker (`_row_label_rank`) promotes ONLY labeled
            # rework tickets, and requiring the label as a deliberate act raises the
            # bar against relabelling an ordinary discovery as this exempt criterion.
            if crit_l == "architecture-rework" \
                    and "architecture-rework" not in _all_labels(tk, api_call):
                results.append(("BLOCK", clean_title,
                                 "architecture-rework-missing-label (add "
                                 "`-l architecture-rework` -- the picker promotes only "
                                 "labeled rework tickets)",
                                 parents_str, target_repo, ""))
                continue
            # #1027/#1033 -- a `user-request` ticket filed FROM a client Odoo
            # Discuss message (body QUOTES a mail.message / discuss.channel_<N> /
            # msg <id> origin) MUST cite the intake worker-reaction (an
            # `Ack-reaction:` line): the owner's visible 👷 "being worked on"
            # signal goes on the client message the MOMENT it is picked up, BEFORE
            # the ticket is filed (skills/odoo-client-messaging/ack-reaction.md).
            # UNCONDITIONAL (attended or not) -- the intake reaction is a fleet
            # rule, not an owner-present gate, and it BLOCKS before the presence/
            # dedup/cap gates. A PreToolUse hook has no Odoo credentials, so it
            # cannot post the reaction itself ("or the gate posts it") -- it blocks
            # and instructs the stream to react + cite instead.
            if crit_l == "user-request" and body \
                    and CLIENT_MSG_ORIGIN_RE.search(body) \
                    and not ACK_REACTION_CITE_RE.search(body):
                results.append(("BLOCK", clean_title,
                                 "intake-no-worker-reaction (this user-request quotes "
                                 "a client Odoo message -- react 👷 on that message "
                                 "FIRST via message_reaction_guarded(msg_id,\"👷\",\"add\") "
                                 "and cite it with an `Ack-reaction: msg <id> 👷` line "
                                 "in the body, or `Ack-reaction: pending — <reason>` if "
                                 "the guarded method is not yet on that instance; see "
                                 "skills/odoo-client-messaging/ack-reaction.md)",
                                 parents_str, target_repo, ""))
                continue
            # #842 -- UNATTENDED gates (an ATTENDED / owner-present filing keeps the
            # pre-#842 flow untouched, so these never touch the owner). presence-gate
            # (req 3): an unattended loop cannot claim the owner asked for a
            # user-request / planned-work ticket. dismissal-word (req 4): a NEW issue
            # body dismissing a test failure (flaky / pre-existing / intermittent /
            # out-of-scope) is the same dismissal in durable form -- fix the test /
            # root cause, never file its excuse. Both BLOCK BEFORE the dedup/cap/
            # near-dup/ratchet checks (cheapest first) and `continue` this segment.
            if unattended:
                unattended_reason = None
                # #993 -- architecture-rework is a MECHANICAL area-review verdict, not
                # an owner-asked ticket, so it is gated by NEITHER the presence gate
                # (its Area: + dedup discipline stand in for the owner-quote) NOR the
                # dismissal-word gate. It stays in EXEMPT_FROM_CAP for the cap/ratchet
                # exemption below.
                if crit_l == "architecture-rework":
                    pass
                elif crit_l in EXEMPT_FROM_CAP:
                    # #962: exempt when the body quotes an owner message with
                    # 'verbatim' + a date from the last 24h — evidence the
                    # owner WAS present and explicitly asked.
                    if not _has_recent_owner_quote(body):
                        unattended_reason = (
                            "presence-required (an unattended loop cannot claim the "
                            "owner asked -- %s is accepted only when the owner is "
                            "PRESENT, or the body quotes an owner message with "
                            "'verbatim' and today's/yesterday's date)" % crit_l)
                else:
                    _dw = _dismissal_word(body)
                    if _dw:
                        unattended_reason = (
                            "dismissal-word:%s (fix the test / root cause -- do not "
                            "file its excuse as a ticket)" % _clean_field(_dw))
                if unattended_reason:
                    results.append(("BLOCK", clean_title, unattended_reason,
                                     parents_str, target_repo, ""))
                    continue
            dedup_match = DEDUP_RE.search(body) if body else None
            if not dedup_match:
                # #329 -- structural half of the dedup gate: prove you searched.
                results.append(("BLOCK", clean_title, "no-dedup-line", parents_str,
                                 target_repo, ""))
            else:
                dedup_claim = _clean_field(dedup_match.group(1))[:80]
                today = _today_str()
                batch_titles = _batch_titles_by_repo.setdefault(target_repo, [])

                # #329 -- cheap local checks BEFORE the network near-dup call
                # (adversarial-review finding: a batch of N filings into N
                # different repos previously paid N network round-trips even
                # when the local caps alone would already refuse most of
                # them -- reordering costs nothing for the common case and
                # bounds the worst case).
                width_blocked = False
                if crit_l not in EXEMPT_FROM_CAP and parents:
                    for p in parents:
                        n = (_log_pass_count(log_path, target_repo, today, parent=p)
                             + _local_parent_count.get((target_repo, p), 0))
                        if n >= CHAIN_WIDTH_CAP:
                            width_blocked = True
                            break
                daily_blocked = False
                if not width_blocked and crit_l not in EXEMPT_FROM_CAP:
                    n = (_log_pass_count(log_path, target_repo, today)
                         + _local_day_count.get(target_repo, 0))
                    if n >= DAILY_CAP:
                        daily_blocked = True

                if width_blocked:
                    results.append(("BLOCK", clean_title, "chain-width-cap",
                                     parents_str, target_repo, dedup_claim))
                elif daily_blocked:
                    results.append(("BLOCK", clean_title, "daily-cap",
                                     parents_str, target_repo, dedup_claim))
                else:
                    near_dup = _near_duplicate(title, body, cwd, target_repo,
                                                batch_titles)
                    if near_dup:
                        if near_dup == "in-batch":
                            reason = "near-duplicate:in-this-batch"
                        else:
                            reason = "near-duplicate:#%s" % near_dup
                        results.append(("BLOCK", clean_title, reason, parents_str,
                                         target_repo, dedup_claim))
                    elif (unattended and crit_l not in EXEMPT_FROM_CAP
                          and _ratchet_should_block(target_repo, cwd)):
                        # #842 req 2 -- net-drain ratchet, checked LAST (the only gate
                        # costing a gh call, so it is never paid for a filing already
                        # blocked more cheaply). An UNATTENDED non-exempt discovery
                        # filing is allowed ONLY while the repo is strictly draining
                        # today (created_today < closed_today); otherwise BLOCK. A gh
                        # error -> BLOCK (fail-safe). user-request / planned-work are
                        # exempt (already presence-gated above).
                        results.append((
                            "BLOCK", clean_title,
                            "net-drain (created_today >= closed_today on this repo "
                            "-- fix it in-lane now, or fold it as a comment onto the "
                            "existing ticket it belongs to; this repo must drain "
                            "today before an unattended loop files more)",
                            parents_str, target_repo, dedup_claim))
                    else:
                        results.append(("PASS", clean_title, crit, parents_str,
                                         target_repo, dedup_claim))
                        batch_titles.append(title or "")
                        # #842 -- the per-repo counter bump (to close the within-TTL
                        # burst race) is DEFERRED to the print loop below, where
                        # `has_block` is known: a PASS in a batch that ALSO blocks is
                        # NOTFILED (the whole command is refused, nothing filed), so
                        # bumping it here would be a phantom +1 (#842-review 🔵,
                        # mirroring the #329 phantom-PASS / NOTFILED discipline).
                        if crit_l not in EXEMPT_FROM_CAP:
                            _local_day_count[target_repo] = \
                                _local_day_count.get(target_repo, 0) + 1
                            for p in parents:
                                key = (target_repo, p)
                                _local_parent_count[key] = \
                                    _local_parent_count.get(key, 0) + 1

    return results


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if not cmd:
        return 0
    sid = field_of(payload, "session_id", "unknown") or "unknown"
    agent_id = field_of(payload, "agent_id", "") or ""
    cwd = os.getcwd()
    repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    log_path = os.path.join(os.path.expanduser("~"), ".claude", "scope-gate.log")

    # #842 req 1 -- a worktree WORKER (subagent, payload .agent_id) may NOT file
    # a GitHub issue. BEFORE the pre-filter (so a graphql createIssue mutation is
    # still caught) and BEFORE the bypass (so a worker can never self-exempt).
    if agent_id and _is_worker_filing(cmd):
        sys.stderr.write(render.WORKER_MSG)
        return 2

    if not _prefilter_pass(cmd):
        return 0
    if "airuleset:scope-gate-ok" in cmd:
        return 0

    unattended = is_away(sid)
    results = classify_command(cmd, sid, cwd, repo_dir, log_path, unattended)

    if not results:
        return 0
    has_block = any(r[0] == "BLOCK" for r in results)
    _write_log(log_path, results, has_block, sid)
    if has_block:
        sys.stderr.write(render.render_block(results))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
