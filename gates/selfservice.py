"""gates.selfservice -- the self-service prod-read gate (entry for
hooks/block-gk-request-without-selfservice.sh, #516 + #1049).

#516 (unchanged): a gk ACTION request (`airuleset.py gk-request`, a raw `gh`
adding `needs-gatekeeper`, or a `GATEKEEPER-ACTION:` body) from a REDUCED-
authority sub-dev stream must carry a falsifiable `Self-service-checked:` line;
a review hand-off (ready-for-review / stream:<x> / READY-FOR-REVIEW:) is never
gated (rule 8); a full-authority / unresolvable-authority box is never gated
(degrade-to-allow, #390's bias); the body is resolved from --body/--comment/
--body-file (heredoc + cd-relative disk, #483/#963); bypass
`# airuleset:selfservice-ok <reason>`.

#1049 (new teeth): the presence of the line is no longer enough for a request
that reads as a SELF-SERVICEABLE PROD READ. The owner had to repeat three times
that the stream has a fresh PROD copy (odoo-erp #1967, "Preco ti to musim
nonstop opakovat, ze mas kopiu produ?!"); each time the stream escalated a pure
read with a rationalised line ("the copy predates the event") that the
presence-only gate accepted. Now such a request must cite
`refresh <run-id|comment-id> at <ISO-UTC>` NEWER than the newest event
timestamp in the request, else BLOCK (`missing-refresh` / `copy-predates-
event`). The gate FAILS OPEN: it engages only for a request positively
classified as a self-serviceable pure read -- read-verb + PROD, NOT a live
intervention, NOT a whitelisted gk-only surface the refresh rsync EXCLUDES
(odoo-erp prod-ro-clone-accounts.md: /var/lib/odoo/sessions, container/nginx
logs, ~/.secrets, docker/nginx runtime). Every verdict is logged to
~/.claude/selfservice-gate.log with a machine reason-code + stream + ticket so
gates.audit.count_selfservice_blocks can trend recurrences per stream.

The migration follows the #1020 thin-adapter pattern (see gates.testskips): the
bash hook is now a <=40-line stdin adapter that runs `python3 -m gates.
selfservice 1>&2`. STDLIB only.
"""
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone

from gates import command_of, field_of, read_payload
from gates.shellcmd import split_top_level

# --------------------------------------------------------------------------- #
# #1049 classifier regexes.
# --------------------------------------------------------------------------- #
# The falsifiable claim marker (unchanged, #516). Matched ANYWHERE (inline
# --comment or own-line --body-file), non-empty after the colon.
SELFSERVICE_RE = re.compile(r'Self-service-checked:\s*\S', re.IGNORECASE)

# PROD token -- the owner's uppercase convention (PROD / PRODe / PRODu ...) or a
# lowercase standalone word, but NEVER product/production/produce/produkt.
PROD_TOKEN_RE = re.compile(r'\bPROD(?:e|u|om|y)?\b|\bprod\b')

# A prod-READ verb (the dispatch's regex family). Case-insensitive.
PROD_READ_VERB_RE = re.compile(
    r'\b(reads?|reading|lists?|grep|select|logs?|device_log|sessions?|'
    r'config_parameter|counts?|checks?|checking)\b', re.IGNORECASE)

# A LIVE intervention (EN + SK) -- the request is not a pure read, so it fails
# OPEN (a genuine gk action). #1049-review MAJOR-1: the list must be BROAD so a
# genuine intervention request that ALSO reads state is never false-blocked as a
# pure read (the owner's friction tolerance is low, #957/#963); the cost is a
# pure read that coincidentally contains one of these words failing OPEN, which
# is the DOCUMENTED safe direction for this falsifiable-claim gate.
LIVE_INTERVENTION_RE = re.compile(
    r'\b(restarts?|restarting|reboot\w*|re[šs]tart\w*|reloads?|reloading|'
    r'installs?|installing|in[šs]tal\w*|nain[šs]tal\w*|doin[šs]tal\w*|'
    r'deploy\w*|nasad\w*|resets?|resetting|kills?|killing|zabi\w*|grants?|'
    r'granting|udel\w*|revoke\w*|revoking|purg\w*|flush\w*|rotate\w*|'
    r'rotating|requeue\w*|re-?send\w*|resend\w*|preposl\w*|reprocess\w*|'
    r'migrat\w*|clears?|clearing|deletes?|deleting|truncat\w*|drops?|'
    r'dropping|enables?|enabling|disables?|disabling|prun\w*|rebuild\w*|'
    r'starts?|starting|stops?|stopping|spusti\w*|zastav\w*|vypni\w*|'
    r'zapni\w*|zma[žz]\w*|vyma[žz]\w*)\b', re.IGNORECASE)

# The gk-ONLY PROD surfaces the refresh rsync deliberately EXCLUDES (odoo-erp
# .claude/rules/prod-ro-clone-accounts.md). ONE constant, lock-tested. A request
# naming any of these is genuinely NOT self-serviceable from the refresh copy,
# so it passes on its own words (no refresh citation required).
#   #1049-review MINOR: bare "odoo" is dropped from container-logs (the DB
#   ir.logging table IS in the refresh copy, so "read the odoo logs" is
#   self-serviceable — only the CONTAINER stdout logs are gk-only); a genuine
#   gk-only request still matches via "container logs". Slovak surface terms
#   (logy/kontajner) are included so a Slovak stream is not false-blocked.
GK_ONLY_SURFACES = (
    ("session-store", r'/var/lib/odoo/sessions|\bsessions?/|session\s+(?:store|file|id|hijack|cookie)|rel[áa]ci[ae]\s+(?:store|s[úu]bor)'),
    ("container-logs", r'\b(?:container|docker|nginx|kontajner\w*)\s+(?:logs?|logy|logov)\b|\b(?:logs?|logy|logov)\s+(?:of|from|in|z|zo)\s+(?:the\s+)?(?:container|docker|nginx|kontajner\w*)'),
    ("root-secrets", r'(?:~|/root)/\.secrets|\.secrets\b'),
    ("runtime-state", r'\b(?:docker|nginx)\s+(?:runtime|state|config|container)\b'),
)
_GK_ONLY_SURFACE_RES = tuple(re.compile(p, re.IGNORECASE) for _lbl, p in GK_ONLY_SURFACES)

# A refresh citation on the self-service line: `refresh <id> at <ISO-UTC>`.
REFRESH_CITATION_RE = re.compile(
    r'refresh\s+\S+\s+at\s+'
    r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\s*(?:Z|[+-]\d{2}:?\d{2})?)',
    re.IGNORECASE)

# Timestamp shapes for EVENT extraction.
_ISO_DT_RE = re.compile(
    r'\b(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?')
_BARE_DATE_RE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_BARE_TIME_RE = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')
# A bare HH:MM counts as an event only when a today-cue is adjacent (#1049
# review MAJOR-2). "daily" deliberately does NOT match \btoday\b.
_TODAY_CUE_RE = re.compile(r'\btoday\b|\bdnes\w*', re.IGNORECASE)


# --------------------------------------------------------------------------- #
# #1049 pure classifier helpers (unit-tested directly).
# --------------------------------------------------------------------------- #
def references_gk_only_surface(text):
    """True when `text` names a genuinely gk-only PROD surface the refresh
    rsync excludes (session store, container/nginx logs, ~/.secrets, docker/
    nginx runtime) -- so it passes on its own words, no refresh needed."""
    t = text or ""
    return any(rx.search(t) for rx in _GK_ONLY_SURFACE_RES)


def is_self_serviceable_prod_read(text):
    """True when `text` reads as a PROD READ answerable from the stream's own
    fresh refresh copy: a read-verb AND a PROD token, AND NOT a live
    intervention, AND NOT a whitelisted gk-only surface. Fail-open by design --
    anything not positively a self-serviceable pure read returns False."""
    t = text or ""
    if not (PROD_TOKEN_RE.search(t) and PROD_READ_VERB_RE.search(t)):
        return False
    if LIVE_INTERVENTION_RE.search(t):
        return False
    if references_gk_only_surface(t):
        return False
    return True


def _to_utc(y, mo, d, h, mi, s, tz):
    """Build a tz-aware UTC datetime; `tz` is None/'Z'/±HH[:]MM."""
    sec = int(s) if s else 0
    if not tz or tz == "Z":
        return datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc)
    sign = 1 if tz[0] == "+" else -1
    digits = tz[1:].replace(":", "")
    oh = int(digits[:2])
    om = int(digits[2:4]) if len(digits) >= 4 else 0
    off = timezone(sign * timedelta(hours=oh, minutes=om))
    return datetime(y, mo, d, h, mi, sec, tzinfo=off).astimezone(timezone.utc)


def _parse_iso(s):
    """Parse one ISO datetime string to tz-aware UTC, or None on malformed
    components (e.g. an impossible month) -- a skip, not a crash."""
    m = _ISO_DT_RE.search(s or "")
    if not m:
        return None
    y, mo, d, h, mi, sec, tz = m.groups()
    try:
        return _to_utc(int(y), int(mo), int(d), int(h), int(mi), sec, tz)
    except (ValueError, IndexError):
        return None


def refresh_timestamp(text):
    """The tz-aware UTC datetime cited by a `refresh <id> at <ISO-UTC>` line in
    `text`, or None when no citation is present / it does not parse. When more
    than one is cited, the NEWEST wins (a request may reference several)."""
    best = None
    for m in REFRESH_CITATION_RE.finditer(text or ""):
        dt = _parse_iso(m.group(1))
        if dt and (best is None or dt > best):
            best = dt
    return best


def newest_event_timestamp(text, now=None):
    """The NEWEST event timestamp mentioned in `text`, tz-aware UTC, or None.

    The refresh citation span is stripped FIRST so a cited refresh time is never
    itself counted as an event. Recognises full ISO datetimes, bare dates
    (YYYY-MM-DD at 00:00 UTC) and bare HH:MM (interpreted as TODAY, UTC). A
    version number (`0.1.319`, `v1.2.3`) has neither the YYYY-MM-DD dash shape
    nor a colon-minute, so it can never be read as a date/time."""
    t = REFRESH_CITATION_RE.sub(" ", text or "")
    stamps = []

    # 1. full ISO datetimes -- record then blank so their date/time parts are
    # not re-counted by the bare-date / bare-time passes.
    def _iso_sub(m):
        y, mo, d, h, mi, sec, tz = m.groups()
        try:
            stamps.append(_to_utc(int(y), int(mo), int(d), int(h), int(mi), sec, tz))
        except (ValueError, IndexError):
            # impossible component (month 13 etc.) -- skip, still blank the span
            return " "
        return " "
    t = _ISO_DT_RE.sub(_iso_sub, t)

    # 2. bare dates -> 00:00 UTC.
    def _date_sub(m):
        y, mo, d = m.groups()
        try:
            stamps.append(datetime(int(y), int(mo), int(d), 0, 0, tzinfo=timezone.utc))
        except ValueError:
            # impossible date -- skip, still blank the span
            return " "
        return " "
    t = _BARE_DATE_RE.sub(_date_sub, t)

    # 3. bare HH:MM -> today (UTC), but ONLY when "today"/"dnes" sits adjacent to
    # it (#1049-review MAJOR-2: an unqualified bare HH:MM in ordinary prose — a
    # cron schedule "runs daily at 15:00", "as of 11:00", "since 08:30" — was
    # wrongly read as a today-event and false-blocked a compliant refresh. The
    # dispatch itself scoped this to "a bare HH:MM TODAY in the text", so the
    # today-cue is the faithful, low-false-positive interpretation).
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    for m in _BARE_TIME_RE.finditer(t):
        window = t[max(0, m.start() - 18):m.end() + 18]
        if not _TODAY_CUE_RE.search(window):
            continue
        h, mi = int(m.group(1)), int(m.group(2))
        stamps.append(datetime(today.year, today.month, today.day, h, mi,
                               tzinfo=timezone.utc))
    return max(stamps) if stamps else None


def refresh_is_newer(refresh_dt, event_dt):
    """True when `refresh_dt` is strictly NEWER than `event_dt` (both tz-aware).
    A None event means there is nothing to predate -> treated as newer."""
    if event_dt is None:
        return True
    if refresh_dt is None:
        return False
    return refresh_dt > event_dt


# --------------------------------------------------------------------------- #
# Prevencia log (~/.claude/selfservice-gate.log).
# --------------------------------------------------------------------------- #
def _log_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "selfservice-gate.log")


def _current_stream():
    """The box's own account (= the stream), for the log's stream= field."""
    try:
        import airuleset
        return airuleset._current_user()
    except Exception:
        return "unknown"


def _write_log(verdict, kind, reason, ticket, sid):
    """Append one Prevencia line, best-effort. An unwritable ~/.claude only
    loses the audit entry; it MUST never fail the gate (return, do not raise)."""
    path = _log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s  verdict=%s  kind=%s  reason=%s  stream=%s  ticket=%s  "
                     "session=%s\n" % (stamp, verdict, kind, reason,
                                       _current_stream(), ticket or "-", sid))
    except OSError:
        # audit write is non-fatal to the gate decision
        return


# --------------------------------------------------------------------------- #
# Command parsing (ported verbatim from the #516 embedded classifier, split
# into helpers; the ONE quote-aware splitter is now gates.shellcmd).
# --------------------------------------------------------------------------- #
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
_CATFILE_RE = re.compile(r'^\s*cat\s*>>?\s*([^\s<>&;|]+)')
_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_GK_ACTION_RE = re.compile(r'(?m)^\s*GATEKEEPER-ACTION:')
_READY_REVIEW_RE = re.compile(r'(?m)^\s*READY-FOR-REVIEW:')
_STREAM_LABEL_RE = re.compile(r'^stream:[A-Za-z0-9_-]+$', re.I)
_LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")


def _capture_heredocs(cmd):
    """Return (file_bodies, direct_bodies, skeleton) -- heredoc bodies keyed by
    target file / delimiter, and the command with body spans blanked."""
    lines = cmd.split("\n")
    n = len(lines)
    file_bodies, direct_bodies = {}, {}
    skeleton = list(lines)
    i = 0
    while i < n:
        mm = _HEREDOC_RE.search(lines[i].rstrip())
        if not mm:
            i += 1
            continue
        delim = mm.group(2)
        strip_leading = "<<-" in lines[i]
        body, j = [], i + 1
        while j < n:
            check = lines[j].lstrip("\t") if strip_leading else lines[j]
            if check == delim:
                break
            body.append(lines[j])
            j += 1
        body_text = "\n".join(body)
        fm = _CATFILE_RE.match(lines[i])
        if fm:
            file_bodies[fm.group(1)] = body_text
        else:
            direct_bodies[delim] = body_text
        for k in range(i + 1, min(j + 1, n)):
            skeleton[k] = ""
        i = j + 1
    return file_bodies, direct_bodies, "\n".join(skeleton)


def _tokens_of(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _strip_prefix(tk):
    idx = 0
    while idx < len(tk):
        t = tk[idx]
        if t in ("sudo", "env") or t in _LOOP_BODY_KEYWORDS or _ASSIGN_RE.match(t):
            idx += 1
            continue
        break
    return tk[idx:]


def _flag_value(tk, names):
    for idx, t in enumerate(tk):
        for name in names:
            if t == name and idx + 1 < len(tk):
                return tk[idx + 1]
            if t.startswith(name + "="):
                return t[len(name) + 1:]
    return None


def _all_flag_values(tk, names):
    out = []
    for idx, t in enumerate(tk):
        val = None
        for name in names:
            if t == name and idx + 1 < len(tk):
                val = tk[idx + 1]
            elif t.startswith(name + "="):
                val = t[len(name) + 1:]
        if val is None:
            continue
        for piece in val.split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def _cd_target(tk):
    target = None
    for t in tk[1:]:
        if t == "--" or t.startswith("-"):
            continue
        target = t
        break
    if target is None or any(ch in target for ch in "$~*?`"):
        return None
    return target


def _apply_cd(base, tk):
    target = _cd_target(tk)
    if target is None:
        return None
    if os.path.isabs(target):
        return os.path.normpath(target)
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, target))


def _resolve_body(tk, seg_line, eff_cwd, file_bodies, direct_bodies):
    bf = _flag_value(tk, ("-F", "--body-file"))
    if bf is not None:
        if bf == "-":
            m = _HEREDOC_RE.search(seg_line.rstrip())
            if m and m.group(2) in direct_bodies:
                return direct_bodies[m.group(2)]
            return None
        if bf in file_bodies:
            return file_bodies[bf]
        if os.path.isabs(bf):
            path = bf
        elif eff_cwd is not None:
            path = os.path.join(eff_cwd, bf)
        else:
            path = None
        if path is not None:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except OSError:
                # unreadable body path -> treat as unresolved (block-conservative)
                return None
        return None
    inline = _flag_value(tk, ("--body", "--comment"))
    return inline if inline is not None else None


def _is_gk_request(tk):
    for i, t in enumerate(tk):
        if t == "airuleset" or t.endswith("airuleset.py"):
            if i + 1 < len(tk) and tk[i + 1] == "gk-request":
                if "-h" in tk or "--help" in tk:
                    return False
                return True
    return False


def _is_gh_issue_cmd(tk):
    return len(tk) >= 3 and tk[0] == "gh" and tk[1] == "issue" \
        and tk[2] in ("create", "edit", "comment")


def _ticket_of(tk, body):
    """Best-effort ticket number: --issue N, a gh issue <verb> N positional, or
    a #N in the body. '-' when none is resolvable."""
    val = _flag_value(tk, ("--issue", "-i"))
    if val and val.lstrip("#").isdigit():
        return val.lstrip("#")
    if _is_gh_issue_cmd(tk) and len(tk) >= 4 and tk[3].isdigit():
        return tk[3]
    m = re.search(r'#(\d+)\b', body or "")
    return m.group(1) if m else "-"


# --------------------------------------------------------------------------- #
# Classification.
# --------------------------------------------------------------------------- #
def _classify_prod_read(body):
    """Return (verdict, reason) for a line-present gk action request. Only the
    #1049 refresh check; the caller handles the no-line case."""
    if not is_self_serviceable_prod_read(body):
        return "PASS", "self-service-line-present"
    rf = refresh_timestamp(body)
    if rf is None:
        return "BLOCK", "missing-refresh"
    ev = newest_event_timestamp(body)
    if not refresh_is_newer(rf, ev):
        return "BLOCK", "copy-predates-event"
    return "PASS", ("refresh-newer-than-event" if ev is not None
                    else "refresh-cited-no-event")


def classify_command(cmd, cwd=None):
    """Classify a whole command into a list of (verdict, kind, reason, ticket).
    Empty list = not a gated escalation at all. `cwd` is the command's base cwd
    for relative `-F` body resolution (#1049-review NIT: the SAME cwd the
    authority gate resolves against, so the two never diverge); defaults to the
    process cwd, matching the #516 hook's `$(pwd)`."""
    base_cwd = cwd or os.getcwd()
    file_bodies, direct_bodies, skeleton = _capture_heredocs(cmd)
    results = []
    effective_cwd = base_cwd
    for seg in split_top_level(skeleton):
        if not seg.strip():
            continue
        tk = _strip_prefix(_tokens_of(seg))
        if not tk:
            continue
        if tk[0] == "cd":
            effective_cwd = _apply_cd(effective_cwd, tk)
            continue
        gkreq = _is_gk_request(tk)
        gh_issue = _is_gh_issue_cmd(tk)
        if not (gkreq or gh_issue):
            continue
        body = _resolve_body(tk, seg, effective_cwd, file_bodies, direct_bodies)
        labels = set(_all_flag_values(tk, ("-l", "--label", "--add-label")))
        ticket = _ticket_of(tk, body or "")
        if gkreq:
            kind, review_handoff = "gk-request", False
        else:
            body_gk_action = bool(body and _GK_ACTION_RE.search(body))
            action_request = "needs-gatekeeper" in labels or body_gk_action
            if not action_request:
                continue
            review_handoff = ("ready-for-review" in labels
                              or any(_STREAM_LABEL_RE.match(x) for x in labels)
                              or bool(body and _READY_REVIEW_RE.search(body)))
            kind = "gh-needs-gatekeeper"
        if review_handoff:
            continue
        if body is None:
            results.append(("BLOCK", kind, "no-body", ticket))
        elif not SELFSERVICE_RE.search(body):
            results.append(("BLOCK", kind, "missing-self-service-line", ticket))
        else:
            verdict, reason = _classify_prod_read(body)
            results.append((verdict, kind, reason, ticket))
    return results


# --------------------------------------------------------------------------- #
# Block messages.
# --------------------------------------------------------------------------- #
_MSG_LINE = """\
A prod-STATE READ (a group membership, a row count, a config value, sent-mail
content) is a SELF-SERVICE question — NOT a gatekeeper action. Before asking the
gatekeeper to act, you MUST first try the self-service prod-read paths yourself,
then state on the request what you tried and what LIVE PROD intervention (if any)
genuinely remains for gk:

  Self-service-checked: tried <RO handover channel has_group/search_read | a
    fresh `REFRESH-DEV-BOX-FROM-PROD: <stream>` copy with full psql> — <result>;
    the LIVE PROD intervention I still need from gk is <restart the stuck
    outgoing queue | install <pkg> in RUNTIME_DEPS | ...>.

If it turns out you need NOTHING live from gk (a pure read), do NOT file it —
read it yourself from your fresh PROD copy. See modules/core/autonomous-
verification.md's "What's on PROD? is a SELF-SERVICE question" doctrine.

This is a LOGGED, falsifiable claim (~/.claude/selfservice-gate.log). Genuine
bypass: append `# airuleset:selfservice-ok <reason>` to the command."""

_MSG_REFRESH = """\
The Self-service-checked line does not cite a fresh REFRESH copy newer than the
event — the copy predates the event, which is a reason to REFRESH, never a
reason to escalate. Post `REFRESH-DEV-BOX-FROM-PROD: <stream>` first, read the
fresh copy yourself, then ask gk ONLY for what the rsync EXCLUDES (the session
store /var/lib/odoo/sessions, container/nginx logs, ~/.secrets, docker/nginx
runtime state).

  Self-service-checked: refresh <run-id|comment-id> at <ISO-UTC newer than the
    event> — read <X> from it; the LIVE PROD intervention I still need is <...>.

Kópia staršia ako udalosť je dôvod ZREFRESHOVAŤ, nikdy nie dôvod eskalovať —
najprv si posti `REFRESH-DEV-BOX-FROM-PROD: <stream>`, prečítaj to sám, a
gatekeepera pýtaj len na to, čo rsync vynecháva. Genuine bypass:
`# airuleset:selfservice-ok <reason>`."""


def _block_message(block_reasons):
    parts = []
    if any(r in ("no-body", "missing-self-service-line") for r in block_reasons):
        parts.append(_MSG_LINE)
    if any(r in ("missing-refresh", "copy-predates-event") for r in block_reasons):
        parts.append(_MSG_REFRESH)
    return "\n\n".join(parts) if parts else _MSG_LINE


# --------------------------------------------------------------------------- #
# Authority gate + main.
# --------------------------------------------------------------------------- #
def _reduced_authority(cwd):
    """True only for a resolvable REDUCED sub-dev stream; None/False otherwise
    (degrade-to-allow -- a box whose authority we cannot resolve is never
    gated)."""
    try:
        import airuleset
        profile = airuleset.resolve_authority(cwd)
        return profile is not None and profile != "full"
    except Exception:
        return None


def main():
    payload = read_payload()
    cmd = command_of(payload)
    sid = field_of(payload, "session_id", "unknown") or "unknown"
    if not cmd:
        sys.exit(0)

    # Cheap pre-filter (mirrors the bash `case`): only plausibly-escalation cmds.
    if not any(tok in cmd for tok in ("gk-request", "needs-gatekeeper",
                                      "GATEKEEPER-ACTION")):
        sys.exit(0)
    if "airuleset:selfservice-ok" in cmd:
        sys.exit(0)

    cwd = field_of(payload, "cwd", "") or os.getcwd()
    reduced = _reduced_authority(cwd)
    if not reduced:
        sys.exit(0)

    results = classify_command(cmd, cwd)
    if not results:
        sys.exit(0)

    has_block = any(v == "BLOCK" for v, _k, _r, _t in results)
    for verdict, kind, reason, ticket in results:
        log_verdict = "NOTFILED" if (has_block and verdict == "PASS") else verdict
        _write_log(log_verdict, kind, reason, ticket, sid)

    if has_block:
        block_reasons = [r for v, _k, r, _t in results if v == "BLOCK"]
        summary = "".join("  - %s: %s\n" % (k, r)
                          for v, k, r, _t in results if v == "BLOCK")
        sys.stderr.write("🚫 BLOCKED — gk action request:\n%s\n" % summary)
        sys.stderr.write(_block_message(block_reasons) + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
