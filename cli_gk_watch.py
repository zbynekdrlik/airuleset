"""cli_gk_watch -- the gk hand-off state primitive (#1056 L1, #1057 items 1/2).

Pure classification of a ticket's gatekeeper hand-off state from its comments,
shared by the footer (indirectly -- the footer bounce COUNT is a label count off
the same partition rows, never a comment fetch), Job 8 (`bounce_backstop`), the
blind-label-flip gate (`gates.labeledit`), and the `airuleset.py gk-watch` CLI.

ONE fetch path -- `airuleset._infra_ticket_comments` (extended additively with
`user.login` + `created_at`) -- is INJECTED into `watch_issue`, so THIS module
stays a pure, network-free leaf a test can drive with fixture comment rows (the
#1040 "never a second poller" mandate). `_parse_gk_findings` (airuleset.py:4319,
previously uncalled) gets its first caller here.

Root cause it serves: nothing compared the newest gk verdict/advisory against
the newest stream READY-FOR-REVIEW; streams hand-rolled `since=` bash watchers
that missed BOUNCE verdicts for 9-19 h (odoo-erp #5613/#6890, 16.-17.9.2026).

STDLIB only.
"""
import re

# The gk verdict machine marker the design (comment 5707242312) recommends for
# the gk verdict template (`gk-state: BOUNCE @<sha>` / `ACCEPT @<sha>` /
# `ADVISORY @<sha> open=N`). Parsed AUTHORITATIVELY when present; a comment-time
# heuristic (below) is the fallback until the gk template stamps it.
_GK_STATE_MARKER_RE = re.compile(
    r"gk-state:\s*(BOUNCE|ACCEPT|ADVISORY)\b", re.IGNORECASE)

# A git sha attached with `@` (7-40 hex chars), allowing the optional space the
# gk `**Počty (otvorené @ <sha>)**` header uses as well as the marker's
# space-less `@<sha>`. The literal `@ sha` placeholder in an unfilled template
# header is the WORD "sha", not hex, so it never matches -- exactly why the
# header is safe to also use as an ADVISORY signal below.
_SHA_RE = re.compile(r"@\s*([0-9a-f]{7,40})\b")

# The advisory count block header the gk verdict/advisory template uses.
_ADVISORY_COUNTS_RE = re.compile(r"Po[čc]ty\s*\(otvoren", re.IGNORECASE)

# Heuristic BOUNCE / ACCEPT verdict words (author-gated to the gk login, so a
# stream comment quoting "BOUNCE" is never classified as a verdict).
_BOUNCE_RE = re.compile(r"\bBOUNCE\b")
_ACCEPT_RE = re.compile(r"\bACCEPT\b")

# A stream hand-off comment -- the same line-anchored contract
# `airuleset._READINESS_LINE_RE` / `subdev-handoff-match.sh` enforce (optional
# markdown emphasis/header/list prefix, then READY-FOR-REVIEW at line start).
_RFR_RE = re.compile(r"^\s*([#*_-]+\s*)?READY-FOR-REVIEW", re.MULTILINE)


def _parse_iso(s):
    """Epoch seconds for an ISO-8601 timestamp (REST renders `...Z`), or None on
    any unparsable/absent value -- self-contained so the pure path needs no
    airuleset import. Mirrors `cli_quals._parse_iso_ts`."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _findings(body):
    """Finding ids in a gk comment body, via `airuleset._parse_gk_findings`
    (its first caller). Lazy import keeps this module import-cycle-free; on any
    failure returns [] (never raises)."""
    try:
        import airuleset
        return airuleset._parse_gk_findings(body or "")
    except Exception:
        return []


def classify_gk_comment(body):
    """Return `(verdict, sha, ids)` for a gk comment body.

    `verdict` is one of ``"BOUNCE"``/``"ACCEPT"``/``"ADVISORY"``/``None``;
    `sha` is the ``@<hexsha>`` the gk stamped (marker first, else the first
    ``@<hexsha>`` in the body) or ``None``; `ids` are the finding ids
    (`_parse_gk_findings`). ``verdict is None`` means the body is not a gk
    verdict/advisory comment at all.

    Precedence: the machine marker wins; else BOUNCE > ACCEPT > ADVISORY, where
    ADVISORY is signalled by the `Počty (otvorené ...)` header OR the presence
    of any finding id."""
    text = body or ""
    ids = _findings(text)
    mm = _GK_STATE_MARKER_RE.search(text)
    if mm:
        verdict = mm.group(1).upper()
    elif _BOUNCE_RE.search(text):
        verdict = "BOUNCE"
    elif _ACCEPT_RE.search(text):
        verdict = "ACCEPT"
    elif _ADVISORY_COUNTS_RE.search(text) or ids:
        verdict = "ADVISORY"
    else:
        verdict = None
    sm = _SHA_RE.search(text)
    sha = sm.group(1) if sm else None
    return verdict, sha, ids


def _cites_id(body, fid):
    """True if `body` cites finding id `fid` as a STANDALONE token (so id "1"
    never matches "10"/"11"). Exact-id match, the dispatch's contract."""
    if not body or not fid:
        return False
    return re.search(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(fid),
                     body) is not None


def _exact_match(a, b):
    """Default login matcher (exact). Callers inject the app-aware
    `airuleset._is_own_login` for App-token boxes where the ISSUE vs COMMENT
    author.login `app/` prefix differs."""
    return bool(a) and a == b


def watch_issue(issue, *, fetch, gk_login, self_login,
                head_ts_fn=None, now=None, is_own_login=None):
    """Classify ticket `issue`'s hand-off state from its comments.

    `fetch(issue)` returns the comment rows -- a list of
    ``{"id", "body", "login", "created_at"}`` dicts -- or ``None`` on ANY gh
    error (→ state ``"unknown"``, never a wrong state). `gk_login` is the
    gatekeeper's login, `self_login` the stream's own. `head_ts_fn(issue)`
    (optional) → the epoch of the newest commit on the PR branch, or ``None``.
    `now` epoch defaults to `time.time()`. `is_own_login(author, login)`
    (optional) → the app-aware matcher, defaulting to exact match.

    Returns a dict:
      ``{issue, state, gk_login, gk_latest, rfr, head_ts, age_seconds,
         undispositioned_ids}``
    with ``state`` one of:
      - ``bounce-unanswered`` -- the newest gk comment is a BOUNCE newer than
        the newest stream RFR (or there is no RFR);
      - ``needs-disposition`` -- finding ids in gk comments newer than the last
        RFR that no LATER stream comment dispositions by exact id;
      - ``rfr-current`` -- the stream has responded (RFR newer than the newest
        gk comment, no undispositioned ids);
      - ``no-gk-comment`` -- no gk verdict/advisory comment at all;
      - ``unknown`` -- the gh fetch failed.

    A comment carrying a READY-FOR-REVIEW line is treated as a stream hand-off,
    NEVER a gk verdict, even when it also carries finding-disposition ids and
    even on a shared-gh-identity box where `gk_login == self_login` -- the one
    robust discriminator when the two logins collide."""
    import time as _time
    if now is None:
        now = _time.time()
    match = is_own_login or _exact_match

    base = {"issue": issue, "gk_login": gk_login, "gk_latest": None,
            "rfr": None, "head_ts": None, "age_seconds": None,
            "undispositioned_ids": []}
    rows = fetch(issue)
    if rows is None:
        base["state"] = "unknown"
        return base

    gk_comments = []          # {ts, id, verdict, sha, ids}
    stream_comments = []      # {ts, body}
    rfr_ts = None
    rfr_id = None
    for c in rows:
        if not isinstance(c, dict):
            continue
        ts = _parse_iso(c.get("created_at"))
        login = c.get("login") or ""
        body = c.get("body") or ""
        cid = c.get("id")
        is_rfr_line = bool(_RFR_RE.search(body))
        if match(login, gk_login) and not is_rfr_line:
            verdict, sha, ids = classify_gk_comment(body)
            if verdict is not None and ts is not None:
                gk_comments.append({"ts": ts, "id": cid, "verdict": verdict,
                                    "sha": sha, "ids": ids})
        if match(login, self_login):
            stream_comments.append({"ts": ts, "body": body})
            if is_rfr_line and ts is not None and (rfr_ts is None or ts > rfr_ts):
                rfr_ts, rfr_id = ts, cid

    if rfr_ts is not None:
        base["rfr"] = {"created_at": rfr_ts, "id": rfr_id}

    if not gk_comments:
        base["state"] = "no-gk-comment"
        return base

    gk_latest = max(gk_comments, key=lambda g: g["ts"])
    base["gk_latest"] = {"id": gk_latest["id"], "verdict": gk_latest["verdict"],
                         "created_at": gk_latest["ts"], "sha": gk_latest["sha"],
                         "ids": list(gk_latest["ids"])}

    # Resolve the PR branch head commit time (best-effort; None → the gate
    # treats it as "no commit since the verdict", the safe block direction).
    if head_ts_fn is not None:
        try:
            base["head_ts"] = head_ts_fn(issue)
        except Exception:
            base["head_ts"] = None

    rfr_eff = rfr_ts if rfr_ts is not None else 0.0

    if gk_latest["verdict"] == "BOUNCE" and gk_latest["ts"] > rfr_eff:
        base["state"] = "bounce-unanswered"
        base["age_seconds"] = max(0.0, now - gk_latest["ts"])
        base["undispositioned_ids"] = list(gk_latest["ids"])
        return base

    # needs-disposition: finding ids raised by gk comments NEWER than the last
    # RFR that no LATER stream comment cites by exact id.
    newer_gk = [g for g in gk_comments if g["ts"] > rfr_eff]
    raised = {}                                    # id -> earliest raising gk ts
    for g in newer_gk:
        for fid in g["ids"]:
            if fid not in raised or g["ts"] < raised[fid]:
                raised[fid] = g["ts"]
    undis = []
    for fid, raised_ts in raised.items():
        dispositioned = any(
            sc["ts"] is not None and sc["ts"] > raised_ts
            and _cites_id(sc["body"], fid)
            for sc in stream_comments)
        if not dispositioned:
            undis.append(fid)
    if undis:
        base["state"] = "needs-disposition"
        base["undispositioned_ids"] = sorted(undis)
        base["age_seconds"] = max(0.0, now - max(g["ts"] for g in newer_gk))
        return base

    base["state"] = "rfr-current"
    return base
