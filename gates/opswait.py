"""gates.opswait -- the W-entry rule: `ops-wait` needs a concrete third-party wait (#1298).

Owner, 10.11.2026: sessions park work in W (`ops-wait`) that waits on nobody or
on the OWNER, and it rots there for weeks (camera-box #1496/#1483/#1441 needed
owner actions = U; dstack had five workable tickets in W; airuleset #1141 sat
in W on an "observation window"). The label was hand-set and nothing checked it.

ONE line shape is the evidence, both at label time and at read time:

    Waiting-on: <third party> — <the concrete event> — <how it is checked>

The party is never the owner (an owner wait is U: `needs-owner-action` /
`needs-answer`), never us / a session of ours, never "nobody". The word lists
are explicit (`OWNER_PARTY_*`, `SELF_PARTY_*`, `*_WHOLE_WORDS`) and
content-locked by tests.

Two halves share the parse:
- LABEL TIME (this module's `main`, thin adapter
  `hooks/block-ops-wait-without-waiting-on.sh`): `gh issue edit <N>
  --add-label ops-wait` (comma lists too) and `gh issue create -l/--label
  ops-wait` are refused unless a valid line is in the SAME command (a `gh
  issue comment` / edit / create body in the chain) or in a comment on that
  ticket from the last `FRESH_S` (30 min). Shell parsing is REUSED from
  `gates.selfservice` / `gates.labeledit` (#1020: one implementation). A `gh
  api` label POST is not parsed (the sibling labeledit gate does not either).
  FAIL-OPEN on a gh read error or an unresolvable ticket/slug (logged), the
  #539 never-false-accuse direction. Every box is gated (the owner's complaint
  spans full and reduced authority). Log ~/.claude/opswait-gate.log; bypass
  `# airuleset:opswait-ok <reason>` (logged).
- READ TIME (`unbacked_reason`): an ops-wait row whose newest valid line is
  missing, or older than `STALE_S` (7 days) with no newer cited own push,
  counts in I, not W (`cli_ticket_state.classify`, fact `wait_unbacked`, read
  by `cli_ticket_facts` from the W comment prefetch).

STDLIB only; no airuleset import at module import time.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

from gates import command_of, field_of, read_payload
from gates.labeledit import _ISSUE_URL_RE
from gates.selfservice import (
    _all_flag_values, _apply_cd, _capture_heredocs, _flag_value,
    _is_gh_issue_cmd, _resolve_body, _strip_prefix, _tokens_of,
)
from gates.shellcmd import split_top_level

OPS_WAIT_LABEL = "ops-wait"
BYPASS_MARKER = "airuleset:opswait-ok"
FRESH_S = 30 * 60               # a ticket comment counts this long at label time
STALE_S = 7 * 24 * 3600         # read time: an older line needs a newer cited push

# The line: start of a line (indent allowed, never a `>` quote), the key, a colon.
_LINE_RE = re.compile(r"(?im)^[ \t]*Waiting-on[ \t]*:[ \t]*(?P<rest>[^\n]*)$")
# Separators between party / event / check: an em or en dash, or a spaced `--`.
_SEP_RE = re.compile(r"[ \t]*[—–][ \t]*|[ \t]+--[ \t]+")
# A copied template field (`<third party>`) is no party.
_PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")

# Tokens are compared lower-cased with diacritics stripped. Two kinds of list:
# - ANYWHERE: one such token anywhere in the party disqualifies it. `*_STEMS`
#   are prefixes, so a Slovak case ending ("vlastníka", "ownera", "Zbyňkovi",
#   "session-y") is caught too.
# - WHOLE: pronouns that disqualify only when the WHOLE party is made of them
#   (plus fillers), so a possessive like "our client" / "tvoj klient" stays a
#   third party while a bare "us" / "ty" / "Claude" does not.
# The owner (Marek counts as the owner's side, owner wording 10.11.2026):
OWNER_PARTY_STEMS = ("zbyn", "drlik", "owner", "vlastnik", "majitel")
OWNER_PARTY_WORDS = ("marek", "marka", "markovi", "marekovi")
OWNER_WHOLE_WORDS = ("ty", "teba", "tebe", "you", "your", "user", "users",
                     "pouzivatel", "pouzivatela", "pouzivatelovi")
# Us: this session, another session / lane / worker of ours, the tooling.
SELF_PARTY_STEMS = ("session", "autopilot", "worker", "supervisor", "subagent",
                    "airuleset")
SELF_PARTY_WORDS = ("lane", "lanes")
SELF_WHOLE_WORDS = ("us", "we", "me", "my", "i", "ja", "nas", "nami", "self",
                    "myself", "ourselves", "claude", "agent", "agents")
# Nobody / unknown (WHOLE party only).
NOBODY_WHOLE_WORDS = ("nobody", "nikto", "nikoho", "nikomu", "none", "nothing",
                      "nic", "unknown", "tbd", "na", "n")
_FILLER_WORDS = ("this", "the", "a", "an", "other", "another", "tento", "tato",
                 "toto", "iny", "ina", "ine", "nejaky", "nejaka")


def _norm_tokens(text):
    """Lower-case word tokens of `text` with diacritics stripped."""
    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", plain.lower())


def party_problem(party):
    """Why `party` is not a third party ("owner" / "self" / "nobody"), or ""."""
    tokens = _norm_tokens(party)
    for tok in tokens:
        if tok in OWNER_PARTY_WORDS or tok.startswith(OWNER_PARTY_STEMS):
            return "owner"
    for tok in tokens:
        if tok in SELF_PARTY_WORDS or tok.startswith(SELF_PARTY_STEMS):
            return "self"
    core = [tok for tok in tokens if tok not in _FILLER_WORDS]
    if not core:
        return "nobody"
    whole = OWNER_WHOLE_WORDS + SELF_WHOLE_WORDS + NOBODY_WHOLE_WORDS
    if all(tok in whole for tok in core):
        if any(tok in OWNER_WHOLE_WORDS for tok in core):
            return "owner"
        if any(tok in SELF_WHOLE_WORDS for tok in core):
            return "self"
        return "nobody"
    return ""


def line_problem(rest):
    """Why one `Waiting-on:` line's text after the colon is invalid, or ""."""
    parts = [p.strip() for p in _SEP_RE.split((rest or "").strip(), maxsplit=2)]
    if len(parts) < 3 or not all(parts):
        return "shape"
    if any(_PLACEHOLDER_RE.match(p) for p in parts):
        return "placeholder"
    return party_problem(parts[0])


def waiting_on_lines(text):
    """`[(rest, problem)]` for every `Waiting-on:` line in `text`."""
    if not isinstance(text, str):
        return []
    return [(m.group("rest").strip(), line_problem(m.group("rest")))
            for m in _LINE_RE.finditer(text)]


def has_valid_waiting_on(text):
    """True when `text` carries at least one valid `Waiting-on:` line."""
    return any(not problem for _rest, problem in waiting_on_lines(text))


def unbacked_reason(ages, now, max_age=STALE_S):
    """The READ-TIME verdict for one plain ops-wait ticket from its comment
    ages (`cli_quals._ages_from_comments`: `waiting_on` = newest comment with a
    valid line, `own_cited` = newest cited own push). None = unknown (the
    comments were not read: the row keeps W), "" = backed (stays W), else the
    one-line reason it counts in I."""
    if not isinstance(ages, dict):
        return None
    line_ts = ages.get("waiting_on")
    if not isinstance(line_ts, (int, float)) or isinstance(line_ts, bool):
        return "no valid `Waiting-on: <third party> — <event> — <check>` line"
    anchor = line_ts
    cited = ages.get("own_cited")
    if isinstance(cited, (int, float)) and not isinstance(cited, bool) \
            and cited > anchor:
        anchor = cited
    if now - anchor <= max_age:
        return ""
    return ("the newest `Waiting-on:` line is %d days old and no cited push "
            "is newer than %d days" % ((now - line_ts) // 86400,
                                       max_age // 86400))


# --------------------------------------------------------------------------- #
# Label time: command parsing.
# --------------------------------------------------------------------------- #
_EDIT_VALUE_FLAGS = ("--add-label", "--remove-label", "--add-assignee",
                     "--remove-assignee", "--add-project", "--remove-project",
                     "--milestone", "-m", "--title", "-t", "--body", "-b",
                     "--body-file", "-F", "-R", "--repo")


def _ticket_keys(tk):
    """The ticket positionals of `gh issue edit|comment …` (numbers, `…/issues/N`
    URLs as N, or a raw token such as `$N` kept verbatim as the chain key)."""
    keys, skip = [], False
    for tok in tk[3:]:
        if skip:
            skip = False
            continue
        if tok.startswith("-"):
            skip = "=" not in tok and tok in _EDIT_VALUE_FLAGS
            continue
        m = _ISSUE_URL_RE.search(tok)
        keys.append(m.group(1) if m else tok)
    return keys


def _repo_of(tk):
    val = _flag_value(tk, ("-R", "--repo"))
    if not val:
        return None
    parts = [p for p in val.rstrip("/").split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def _body_of(tk, seg, cwd, file_bodies, direct_bodies):
    body = _resolve_body(tk, seg, cwd, file_bodies, direct_bodies)
    if body is None:
        body = _flag_value(tk, ("-b",))
    return body


def parse_command(cmd, cwd=None):
    """`(adds, bodies)`: `adds` = `[{"key", "kind", "body", "repo", "cwd"}]`
    for every ops-wait ADD (kind `edit` keyed by ticket, kind `create` with its
    own body), `bodies` = `{ticket key: [comment/edit bodies in the chain]}`."""
    file_bodies, direct_bodies, skeleton = _capture_heredocs(cmd)
    eff = cwd or os.getcwd()
    adds, bodies = [], {}
    for seg in split_top_level(skeleton):
        if not seg.strip():
            continue
        tk = _strip_prefix(_tokens_of(seg))
        if not tk:
            continue
        if tk[0] == "cd":
            eff = _apply_cd(eff, tk)
            continue
        if not _is_gh_issue_cmd(tk):
            continue
        verb = tk[2]
        body = _body_of(tk, seg, eff, file_bodies, direct_bodies)
        if verb == "create":
            if OPS_WAIT_LABEL in _all_flag_values(tk, ("-l", "--label")):
                adds.append({"key": None, "kind": "create", "body": body,
                             "repo": _repo_of(tk), "cwd": eff})
            continue
        keys = _ticket_keys(tk)
        if body is not None:
            for key in keys:
                bodies.setdefault(key, []).append(body)
        if verb == "edit" and OPS_WAIT_LABEL in _all_flag_values(
                tk, ("--add-label",)):
            for key in keys:
                adds.append({"key": key, "kind": "edit", "body": None,
                             "repo": _repo_of(tk), "cwd": eff})
    return adds, bodies


def _verdict_from_texts(texts):
    """("PASS", "same-command") / ("BLOCK", <problem>) / (None, None)."""
    problems = [p for text in texts for _r, p in waiting_on_lines(text)]
    if not problems:
        return None, None
    if "" in problems:
        return "PASS", "valid-line"
    return "BLOCK", problems[-1]


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _default_comments(ticket, repo, cwd, now):
    """The ticket's comments created or edited in the last FRESH_S, as
    `[{"body", "ts"}]`, or None on any read problem (→ the gate fails OPEN).
    Test seam: `AIRULESET_OPSWAIT_COMMENTS_FIXTURE` names a JSON file mapping
    str(ticket) → that list (or null = unreadable)."""
    fixture = os.environ.get("AIRULESET_OPSWAIT_COMMENTS_FIXTURE")
    if fixture:
        try:
            with open(fixture, encoding="utf-8") as fh:
                return json.load(fh).get(str(ticket))
        except (OSError, ValueError, AttributeError):
            return None
    from gates import ghread
    slug = repo or ghread.canonical_slug(cwd)
    if not slug:
        return None
    since = _iso(now - FRESH_S)
    rc, out, _err = ghread._run(
        ["gh", "api", "repos/%s/issues/%s/comments?since=%s&per_page=100"
         % (slug, ticket, since)], cwd, 10, ghread._gh_env(), None)
    if rc != 0:
        return None
    try:
        rows = json.loads(out)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("updated_at") or row.get("created_at"))
        out_rows.append({"body": row.get("body") or "", "ts": ts})
    return out_rows


def classify_command(cmd, cwd=None, comments_fn=None, now=None):
    """`[{"verdict", "reason", "ticket"}]` for every ops-wait add in `cmd`
    (empty = no ops-wait add). `comments_fn(ticket, repo, cwd, now)` is the
    injectable read of the ticket's recent comments (default: gh)."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    read = comments_fn or _default_comments
    adds, bodies = parse_command(cmd, cwd)
    results = []
    for add in adds:
        key = add["key"]
        if add["kind"] == "create":
            verdict, reason = _verdict_from_texts([add["body"] or ""])
            results.append({"verdict": verdict or "BLOCK",
                            "reason": reason or "missing", "ticket": "new"})
            continue
        verdict, reason = _verdict_from_texts(bodies.get(key, []))
        if verdict:
            results.append({"verdict": verdict, "reason": "same-command:" + reason
                            if verdict == "PASS" else reason, "ticket": key})
            continue
        if not (key and key.isdigit()):
            results.append({"verdict": "PASS", "reason": "unresolvable-ticket",
                            "ticket": key})
            continue
        try:
            rows = read(key, add["repo"], add["cwd"], now)
        except Exception:  # noqa: BLE001 — a read error fails OPEN (logged)
            rows = None
        if rows is None:
            results.append({"verdict": "PASS", "reason": "gh-unreadable",
                            "ticket": key})
            continue
        recent = [r.get("body") or "" for r in rows if isinstance(r, dict)
                  and isinstance(r.get("ts"), (int, float))
                  and now - r["ts"] <= FRESH_S]
        verdict, reason = _verdict_from_texts(recent)
        results.append({"verdict": verdict or "BLOCK",
                        "reason": ("recent-comment" if verdict == "PASS"
                                   else reason or "missing"), "ticket": key})
    return results


# --------------------------------------------------------------------------- #
# Log + message + main.
# --------------------------------------------------------------------------- #
def _log_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "opswait-gate.log")


def _write_log(verdict, reason, ticket):
    """Append one `<ISO> <verdict> <reason> <ticket>` line, best-effort: an
    unwritable ~/.claude only loses the audit entry, never the decision."""
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s %s %s\n" % (stamp, verdict, reason, ticket or "-"))
    except OSError:
        return                     # audit write is non-fatal to the gate decision


_WHY = {
    "missing": "no `Waiting-on:` line in this command or in a comment from "
               "the last 30 min",
    "owner": "the `Waiting-on:` party is the OWNER — an owner wait is U, not W",
    "self": "the `Waiting-on:` party is us / a session of ours — that is "
            "workable work (I), not a wait",
    "nobody": "the `Waiting-on:` party is nobody / unknown",
    "shape": "the `Waiting-on:` line needs three parts: party — event — check",
    "placeholder": "the `Waiting-on:` line still has a `<…>` template field",
}


def block_message(blocks):
    lines = ["🚫 BLOCKED — `ops-wait` (W) needs a concrete THIRD-PARTY wait "
             "(#1298):"]
    for b in blocks:
        lines.append("  #%s: %s" % (b["ticket"], _WHY.get(b["reason"], b["reason"])))
    lines += [
        "",
        "W = waiting on a third party for a concrete event. Put this line in "
        "the same command (a `gh issue comment <N> --body-file …` before the "
        "edit) or comment it on the ticket first (it counts for 30 min):",
        "  Waiting-on: <third party> — <the concrete event> — <how you check it>",
        "Waiting on the OWNER is U: `--add-label needs-owner-action` (his manual "
        "step) or `needs-answer` (his answer). Work you can do yourself stays in "
        "I: no parking label.",
        "Bypass (rare, logged): `# %s <reason>`." % BYPASS_MARKER,
    ]
    return "\n".join(lines)


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if not cmd or OPS_WAIT_LABEL not in cmd or "issue" not in cmd:
        sys.exit(0)
    cwd = field_of(payload, "cwd", "") or os.getcwd()
    results = classify_command(cmd, cwd)
    if not results:
        sys.exit(0)
    if BYPASS_MARKER in cmd:
        for r in results:
            _write_log("BYPASS", r["reason"], r["ticket"])
        sys.exit(0)
    for r in results:
        _write_log(r["verdict"], r["reason"], r["ticket"])
    blocks = [r for r in results if r["verdict"] == "BLOCK"]
    if blocks:
        sys.stderr.write(block_message(blocks) + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
