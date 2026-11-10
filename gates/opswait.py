"""gates.opswait -- the W-entry rule: `ops-wait` needs a concrete third-party wait (#1298).

Owner, 10.11.2026: sessions park work in W (`ops-wait`) that waits on nobody or
on the OWNER, and it rots there for weeks (camera-box tickets that needed owner
actions = U; dstack had five workable tickets in W; airuleset #1141 sat in W on
an "observation window"). The label was hand-set and nothing checked it.

ONE piece of evidence, read the same way at label time and at read time: a
TICKET COMMENT carrying the line

    Waiting-on: <third party> — <the concrete event> — <how it is checked>

(markdown `- ` / `**…**` wrappers and `--` / ` - ` separators accepted, a `>`
quote or a fenced code block never). The party is never the owner (an owner
wait is U: `needs-owner-action` / `needs-answer`), never us / a session of
ours, never the gatekeeper (a hand-off, #636), never "nobody" / "time". The
word lists are explicit and content-locked by tests. An issue BODY never
counts: the read side reads comments only, so the gate does too.

Two halves share the parse:
- LABEL TIME (`main`, thin adapter `hooks/block-ops-wait-without-waiting-on.sh`):
  `gh issue edit <N…> --add-label ops-wait` (comma lists, `=`, URL tickets,
  any case) is refused unless a valid line is in a `gh issue comment` on that
  ticket in the SAME command (inline, `-F file`, heredoc, `$(cat <<EOF)`) or
  in a comment CREATED on that ticket in the last `FRESH_S` (30 min). `gh
  issue create --label ops-wait` is always refused (no comment can exist yet:
  create, comment, then label). Shell parsing is REUSED from
  `gates.selfservice` / `gates.labeledit` (#1020). A `gh api` label POST is
  not parsed (the sibling labeledit gate does not either); any label-time miss
  is caught at read time. FAIL-OPEN on a gh read error or an unresolvable
  ticket/slug (logged), the #539 never-false-accuse direction. Every box is
  gated. Log ~/.claude/opswait-gate.log; bypass `# airuleset:opswait-ok
  <reason>` (the reason is logged).
- READ TIME (`unbacked_reason`): a plain ops-wait row whose newest valid line
  is missing, or older than `STALE_S` (7 days) with no newer cited own push,
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

# The line: start of a line (indent, a `- ` bullet and `**` bold allowed, never
# a `>` quote), the key (`Waiting-on` / `Waiting on`), a colon.
_LINE_RE = re.compile(r"(?im)^[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?Waiting[- ]on"
                      r"(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(?P<rest>[^\n]*)$")
# A fenced code block (an example, never a statement) is dropped first.
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$")
# Separators between party / event / check: an em or en dash, or a spaced
# `--` / `-`.
_SEP_RE = re.compile(r"[ \t]*[—–][ \t]*|[ \t]+--?[ \t]+")
# A copied template field (`<third party>`) is no party.
_PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")
_BYPASS_RE = re.compile(r"airuleset:opswait-ok[ \t]*(?P<reason>[^\n]*)",
                        re.IGNORECASE)
_CAT_HEREDOC_RE = re.compile(r"^\$\(\s*cat\s+<<-?\s*(['\"]?)(\w+)\1")

# Tokens are compared lower-cased with diacritics stripped. Two kinds of list:
# - ANYWHERE: one such token anywhere in the party disqualifies it (`*_STEMS`
#   are prefixes, so a Slovak case ending — "Zbyňkovi", "session-y" — counts).
#   Kept to words that can only mean the owner / our own machinery.
# - WHOLE: words that disqualify only when the WHOLE party (minus fillers) is
#   made of them, so "repo owner", "vlastník domény", "Marek Novák (klient)",
#   "Cloudflare Workers", "our client", "tvoj klient" stay third parties while
#   a bare "owner" / "Marek" / "ty" / "worker" / "us" does not.
OWNER_NAME_STEMS = ("zbyn", "drlik")
OWNER_WHOLE_WORDS = ("owner", "ownera", "ownerovi", "vlastnik", "vlastnika",
                     "vlastnikovi", "majitel", "majitela", "majitelovi",
                     "marek", "marka", "markovi", "marekovi", "ty", "teba",
                     "tebe", "you", "your", "user", "users", "pouzivatel",
                     "pouzivatela", "pouzivatelovi")
SELF_PARTY_STEMS = ("session", "autopilot", "subagent")
SELF_PARTY_WORDS = ("lane", "lanes")
SELF_WHOLE_WORDS = ("us", "we", "me", "my", "i", "ja", "mna", "mne", "sebe",
                    "nas", "nami", "self", "myself", "ourselves", "claude",
                    "agent", "agents", "worker", "workers", "supervisor",
                    "stream", "streams")
# The gatekeeper is a fleet actor with its own hand-off lane, never W (#636).
GATEKEEPER_STEMS = ("gatekeeper",)
GATEKEEPER_WORDS = ("gk",)
NOBODY_WHOLE_WORDS = ("nobody", "nikto", "nikoho", "nikomu", "none", "nothing",
                      "nic", "unknown", "tbd", "na", "n", "someone", "somebody",
                      "anyone", "anybody", "niekto", "niekoho", "time", "cas",
                      "window", "okno", "observation", "pozorovanie",
                      "external", "third", "party", "3rd", "tretia", "tretej",
                      "strana", "strany", "wait", "waiting")
_FILLER_WORDS = ("this", "the", "a", "an", "other", "another", "tento", "tato",
                 "toto", "iny", "ina", "ine", "nejaky", "nejaka", "of", "for",
                 "and", "or", "na", "do")


def _norm_tokens(text):
    """Lower-case word tokens of `text` with diacritics stripped."""
    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", plain.lower())


def party_problem(party):
    """Why `party` is not a third party ("owner" / "self" / "gatekeeper" /
    "nobody"), or ""."""
    tokens = _norm_tokens(party)
    if any(tok.startswith(OWNER_NAME_STEMS) for tok in tokens):
        return "owner"
    if any(tok in GATEKEEPER_WORDS or tok.startswith(GATEKEEPER_STEMS)
           for tok in tokens):
        return "gatekeeper"
    if any(tok in SELF_PARTY_WORDS or tok.startswith(SELF_PARTY_STEMS)
           for tok in tokens):
        return "self"
    core = [tok for tok in tokens if tok not in _FILLER_WORDS] or tokens
    whole = OWNER_WHOLE_WORDS + SELF_WHOLE_WORDS + NOBODY_WHOLE_WORDS
    if not core or not all(tok in whole for tok in core):
        return "" if core else "nobody"
    if any(tok in OWNER_WHOLE_WORDS for tok in core):
        return "owner"
    if any(tok in SELF_WHOLE_WORDS for tok in core):
        return "self"
    return "nobody"


def line_problem(rest):
    """Why one `Waiting-on:` line's text after the colon is invalid, or ""."""
    parts = [p.strip() for p in _SEP_RE.split((rest or "").strip(), maxsplit=2)]
    if len(parts) < 3 or not all(parts):
        return "shape"
    if any(_PLACEHOLDER_RE.match(p) for p in parts):
        return "placeholder"
    return party_problem(parts[0])


def waiting_on_lines(text):
    """`[(rest, problem)]` for every `Waiting-on:` line in `text` (fenced code
    blocks skipped)."""
    if not isinstance(text, str):
        return []
    text = _FENCE_RE.sub("", text)
    return [(m.group("rest").strip(), line_problem(m.group("rest")))
            for m in _LINE_RE.finditer(text)]


def has_valid_waiting_on(text):
    """True when `text` carries at least one valid `Waiting-on:` line (several
    waits may be listed; one valid third-party wait is enough)."""
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
    URLs as N, or a raw token such as `$N` kept verbatim as the chain key),
    flags and their values skipped wherever they sit."""
    keys, skip = [], False
    for tok in tk[3:]:
        if skip:
            skip = False
            continue
        if tok.startswith("-"):
            skip = "=" not in tok and tok in _EDIT_VALUE_FLAGS
            continue
        m = _ISSUE_URL_RE.search(tok)
        keys.append(m.group(1) if m else tok.lstrip("#"))
    return keys


def _repo_of(tk):
    val = _flag_value(tk, ("-R", "--repo"))
    if not val:
        return None
    parts = [p for p in val.rstrip("/").split("/") if p]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else None


def _comment_body(tk, seg, cwd, file_bodies, direct_bodies):
    """The body of a `gh issue comment`: `--body`/`-b` inline (a `$(cat <<EOF
    …)` value resolved from its heredoc), `--body-file`/`-F` file or heredoc."""
    body = _resolve_body(tk, seg, cwd, file_bodies, direct_bodies)
    if body is None:
        body = _flag_value(tk, ("-b",))
    if body is not None:
        m = _CAT_HEREDOC_RE.match(body.strip())
        if m and m.group(2) in direct_bodies:
            return direct_bodies[m.group(2)]
    return body


def _adds_ops_wait(tk, flags):
    return any(v.strip().lower() == OPS_WAIT_LABEL
               for v in _all_flag_values(tk, flags))


def parse_command(cmd, cwd=None):
    """`(adds, bodies)`: `adds` = `[{"key", "kind", "repo", "cwd"}]` for every
    ops-wait ADD (kind `edit` per ticket, kind `create`), `bodies` =
    `{(repo, ticket): [same-command comment bodies]}`."""
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
        verb, repo = tk[2], _repo_of(tk)
        if verb == "create":
            if _adds_ops_wait(tk, ("-l", "--label")):
                adds.append({"key": None, "kind": "create", "repo": repo,
                             "cwd": eff})
            continue
        keys = _ticket_keys(tk)
        if verb == "comment":
            body = _comment_body(tk, seg, eff, file_bodies, direct_bodies)
            for key in keys:
                bodies.setdefault((repo, key), []).append(body or "")
        elif _adds_ops_wait(tk, ("--add-label",)):
            for key in keys:
                adds.append({"key": key, "kind": "edit", "repo": repo,
                             "cwd": eff})
    return adds, bodies


def _verdict_from_texts(texts):
    """("PASS", "valid-line") / ("BLOCK", <problem>) / (None, None) when no
    `Waiting-on:` line exists at all."""
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


def _default_comments(ticket, repo, cwd, now, runner=None):
    """The ticket's comments CREATED in the last FRESH_S, as `[{"body",
    "ts"}]` (ts = created_at, the timestamp the read side ages), or None on
    any read problem (→ the gate fails OPEN). `?since=` filters by
    updated_at, a superset. Seams: `runner(argv) -> (rc, out, err)`, and the
    env `AIRULESET_OPSWAIT_COMMENTS_FIXTURE` naming a JSON file that maps
    str(ticket) → that list (or null = unreadable) for the hook test."""
    fixture = os.environ.get("AIRULESET_OPSWAIT_COMMENTS_FIXTURE")
    if fixture and runner is None:
        try:
            with open(fixture, encoding="utf-8") as fh:
                return json.load(fh).get(str(ticket))
        except (OSError, ValueError, AttributeError):
            return None
    from gates import ghread
    slug = repo or ghread.canonical_slug(cwd)
    if not slug:
        return None
    rc, out, _err = ghread._run(
        ["gh", "api", "repos/%s/issues/%s/comments?since=%s&per_page=100"
         % (slug, ticket, _iso(now - FRESH_S))], cwd, 10,
        None if runner else ghread._gh_env(), runner)
    if rc != 0:
        return None
    try:
        rows = json.loads(out)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return [{"body": row.get("body") or "", "ts": _parse_ts(row.get("created_at"))}
            for row in rows if isinstance(row, dict)]


def _chain_bodies(bodies, add):
    """The same-command comment bodies for `add`'s ticket: same key, and the
    same `-R` repo (a missing `-R` on either side means the cwd repo)."""
    return [body for (repo, key), texts in bodies.items() for body in texts
            if key == add["key"]
            and (repo == add["repo"] or repo is None or add["repo"] is None)]


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
            results.append({"verdict": "BLOCK", "reason": "create",
                            "ticket": "new"})
            continue
        verdict, reason = _verdict_from_texts(_chain_bodies(bodies, add))
        if verdict:
            results.append({"verdict": verdict, "ticket": key, "reason":
                            "same-command" if verdict == "PASS" else reason})
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
        results.append({"verdict": verdict or "BLOCK", "ticket": key, "reason":
                        "recent-comment" if verdict == "PASS"
                        else reason or "missing"})
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
    "missing": "no `Waiting-on:` line in a comment from this command or from "
               "the last 30 min",
    "owner": "the `Waiting-on:` party is the OWNER — an owner wait is U, not W",
    "self": "the `Waiting-on:` party is us / a session of ours — that is "
            "workable work (I), not a wait",
    "gatekeeper": "the `Waiting-on:` party is the gatekeeper — a hand-off "
                  "(`GATEKEEPER-ACTION:` → needs-gatekeeper, #636), never W",
    "nobody": "the `Waiting-on:` party is nobody / time / unknown — name who "
              "you wait on",
    "shape": "the `Waiting-on:` line needs three parts separated by `—`, `–`, "
             "` -- ` or ` - `: party — event — check",
    "placeholder": "the `Waiting-on:` line still has a `<…>` template field",
    "create": "`gh issue create` with `ops-wait`: create the ticket, comment the "
              "`Waiting-on:` line on it, then add `ops-wait`",
}


def block_message(blocks):
    lines = ["🚫 BLOCKED — `ops-wait` (W) needs a concrete THIRD-PARTY wait "
             "(#1298):"]
    for b in blocks:
        lines.append("  #%s: %s" % (b["ticket"], _WHY.get(b["reason"], b["reason"])))
    lines += [
        "",
        "W = waiting on a third party for a concrete event. Comment this line "
        "on the ticket in the same command (`gh issue comment <N> --body-file "
        "…` before the edit) or first (a comment counts for 30 min):",
        "  Waiting-on: <third party> — <the concrete event> — <how you check it>",
        "Waiting on the OWNER is U: `--add-label needs-owner-action` (his manual "
        "step) or `needs-answer` (his answer). Work you can do yourself stays in "
        "I: no parking label. The line is re-read later: none or >7 d old "
        "counts in I.",
        "Bypass (rare, logged): `# %s <reason>`." % BYPASS_MARKER,
    ]
    return "\n".join(lines)


def main():
    payload = read_payload()
    cmd = command_of(payload)
    low = (cmd or "").lower()
    if OPS_WAIT_LABEL not in low or "issue" not in low:
        sys.exit(0)
    cwd = field_of(payload, "cwd", "") or os.getcwd()
    bypass = _BYPASS_RE.search(cmd)
    if bypass:                     # no gh read for a bypassed command
        adds, _bodies = parse_command(cmd, cwd)
        why = bypass.group("reason").strip() or "(no reason)"
        for add in adds:
            _write_log("BYPASS", why.replace(" ", "_")[:120], add["key"] or "new")
        sys.exit(0)
    results = classify_command(cmd, cwd)
    blocks = [r for r in results if r["verdict"] == "BLOCK"]
    for r in results:              # a PASS next to a block labelled nothing
        verdict = "NOTLABELLED" if blocks and r["verdict"] == "PASS" else r["verdict"]
        _write_log(verdict, r["reason"], r["ticket"])
    if blocks:
        sys.stderr.write(block_message(blocks) + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
