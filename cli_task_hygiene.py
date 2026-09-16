"""Odoo task-hygiene A/B/C computation + `task-hygiene` CLI (#1036).

The GLOBAL, invariant-based overseer of every stream's client Odoo board — the
fleet replacement for the per-box audit scripts each stream ran locally. It
computes three violation classes and NEVER trusts a time window (the concrete
defect the local scripts had):

  A — an OPEN task whose LAST `comment` is authored by someone OUTSIDE the
      stream/owner identity set (a client) with NO stream reaction/message
      after it. The client is waiting for a reply.
  B — a task in Verifikácia / Realizácia / Potrebuje ujasniť with ZERO stream
      message (the Verifikácia invariant: "awaiting client verification MUST
      carry a stream handover message").
  C — a task in Verifikácia older than `client_confirm_days` whose last message
      is a stream handover with no client confirmation yet (a reminder is due).

All Odoo access is via the injected `call(model, method, **body)` seam
(`cli_odoo_ro.OdooReadOnlyClient.call` in production, a fake in tests) — this
module makes NO network call of its own, so the whole computation is unit-tested
against a fake. Reactions are read via the GUARDED method
`message_reactions_guarded` (never the 403-by-design raw reaction model, #784);
an instance that has not released it degrades to the `reaction_ids`-presence
fallback (the ticket's else-branch), the safe direction.
"""
import datetime
import json
import os

import cli_odoo_ro as ro

# Deep-URL shape — the FUNCTIONAL project.task action URL (never the raw model
# form), per deliver-files-as-urls.md's functional-URL rule.
_TASK_URL_FMT = "%s/odoo/project/%s/tasks/%s"

_STATUS_BASENAME = "status.json"
_STATUS_DIRNAME = "task-hygiene"

# per-task comment fetch cap — a board task never realistically has more.
_MSG_LIMIT = 100
# open-task fetch cap — a stream board is dozens of open tasks, not thousands.
_TASK_LIMIT = 500
# the nudge keystroke cap (the sibling rider cap, tmux_io/nudge_gate).
_NUDGE_MAX_CHARS = 700


def status_path(home=None):
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".claude", _STATUS_DIRNAME, _STATUS_BASENAME)


def _m2o(value):
    """Odoo many2one → `(id, name)`; `(None, "")` for a False/empty value."""
    if isinstance(value, (list, tuple)) and value:
        vid = value[0] if isinstance(value[0], int) else None
        name = value[1] if len(value) > 1 and isinstance(value[1], str) else ""
        return vid, name
    if isinstance(value, int):
        return value, ""
    return None, ""


def _parse_dt(s):
    """Odoo naive-UTC datetime string → aware UTC datetime, or None."""
    if not isinstance(s, str) or not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


def _is_stream_author(author_value, own_names, stream_partner_ids):
    """True iff a message's `author_id` is the stream/owner (name in
    `own_names` OR partner id in `stream_partner_ids`)."""
    aid, name = _m2o(author_value)
    return (name in own_names) or (aid is not None and aid in stream_partner_ids)


def _has_stream_reaction(call, msg, own_names, stream_partner_ids):
    """True iff the stream/owner reacted to `msg`.

    PRIMARY: the guarded server method `message_reactions_guarded` (#784) —
    read-only, returns `[{content, partner_id/guest_id}, ...]`. A reactor whose
    partner id is in `stream_partner_ids` or whose name is in `own_names` = a
    stream ACK. FALLBACK (guarded method not released on this instance → the
    call raises `OdooError`): the ticket's else-branch — the PRESENCE of any
    `reaction_ids` on the message is treated as ACKed (never a raw
    `mail.message.reaction` read, which 403s by design)."""
    mid = msg.get("id")
    if mid is None:
        return bool(msg.get("reaction_ids"))
    try:
        reacts = call("mail.message", "message_reactions_guarded", ids=[mid])
    except ro.OdooError:
        return bool(msg.get("reaction_ids"))
    if not isinstance(reacts, list):
        return bool(msg.get("reaction_ids"))
    for rx in reacts:
        if not isinstance(rx, dict):
            continue
        pid, pname = _m2o(rx.get("partner_id"))
        if (pid is not None and pid in stream_partner_ids) or pname in own_names:
            return True
    return False


def _tracked_stage_ids(cfg):
    """The B-stage id set (Verifikácia / Realizácia / Potrebuje ujasniť)."""
    st = cfg.get("stage_ids", {})
    return {st.get("verifikacia"), st.get("realizacia"),
            st.get("potrebuje_ujasnit")} - {None}


def compute_hygiene(call, cfg, now=None):
    """Compute the A/B/C violations. `call(model, method, **body)` is the Odoo
    read seam; `cfg` is a validated config dict. Returns a dict with `A`, `B`,
    `C` (lists of `{task_id, task_name, stage, url, ...}`) and a `summary`
    line. Read-only; every failure inside the client surfaces as `OdooError`
    to the caller (the watchdog job / CLI wraps it)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    own_names = set(cfg.get("own_author_names", []))
    stream_pids = set(cfg.get("stream_partner_ids", []))
    stages = cfg.get("stage_ids", {})
    verif = stages.get("verifikacia")
    hotovo = stages.get("hotovo")
    tracked = _tracked_stage_ids(cfg)
    confirm_days = ro.client_confirm_days(cfg)
    project_ids = list(cfg.get("project_ids", []))

    domain = [["project_id", "in", project_ids]]
    if hotovo is not None:
        domain.append(["stage_id", "!=", hotovo])
    tasks = call("project.task", "search_read", domain=domain,
                 fields=["id", "name", "stage_id"], order="id", limit=_TASK_LIMIT)

    a_items, b_items, c_items = [], [], []
    for t in tasks or []:
        tid = t.get("id")
        tname = t.get("name") or ""
        stage_id, stage_name = _m2o(t.get("stage_id"))
        msgs = call(
            "mail.message", "search_read",
            domain=[["model", "=", "project.task"], ["res_id", "=", tid],
                    ["message_type", "=", "comment"]],
            fields=["id", "author_id", "date", "reaction_ids"],
            order="date desc, id desc", limit=_MSG_LIMIT)
        msgs = msgs or []
        last = msgs[0] if msgs else None

        base = {"task_id": tid, "task_name": tname, "stage": stage_name}

        # A — last comment by a client, no stream reaction after it.
        if last is not None and not _is_stream_author(
                last.get("author_id"), own_names, stream_pids):
            if not _has_stream_reaction(call, last, own_names, stream_pids):
                _, aname = _m2o(last.get("author_id"))
                dt = _parse_dt(last.get("date"))
                a_items.append(dict(base, author=aname,
                                    date=last.get("date"),
                                    ts=(dt.timestamp() if dt else None)))

        # B — tracked-stage task with NO stream message at all.
        if stage_id in tracked:
            has_stream = any(
                _is_stream_author(m.get("author_id"), own_names, stream_pids)
                for m in msgs)
            if not has_stream:
                b_items.append(dict(base))

        # C — Verifikácia, last message is a stale stream handover, no client
        # reply after it (a client reply would be an A candidate instead).
        if stage_id is not None and stage_id == verif and last is not None:
            if _is_stream_author(last.get("author_id"), own_names, stream_pids):
                dt = _parse_dt(last.get("date"))
                if dt is not None:
                    age_days = (now - dt).total_seconds() / 86400.0
                    if age_days > confirm_days:
                        c_items.append(dict(base, days=int(age_days)))

    summary = "task-hygiene: A=%d B=%d C=%d" % (
        len(a_items), len(b_items), len(c_items))
    return {"A": a_items, "B": b_items, "C": c_items, "summary": summary}


def task_url(cfg, item):
    """The functional deep-URL for a task item (`/odoo/project/<pid>/tasks/<tid>`).
    Uses the FIRST configured project id for the path segment."""
    base = str(cfg.get("instance_url", "")).rstrip("/")
    pids = cfg.get("project_ids") or [""]
    return _TASK_URL_FMT % (base, pids[0], item.get("task_id"))


def _short(name, n=48):
    name = name or ""
    return name if len(name) <= n else name[: n - 1] + "…"


def format_report(result, cfg):
    """Human-readable multi-line report with a deep-URL per flagged task and the
    one-line summary — what `airuleset.py task-hygiene` prints."""
    lines = [result["summary"]]
    labels = [
        ("A", "nezodpovedaný komentár klienta"),
        ("B", "Verifikácia/Realizácia/Potrebuje ujasniť bez správy streamu"),
        ("C", "Verifikácia > lehota bez potvrdenia klienta"),
    ]
    for key, label in labels:
        items = result.get(key, [])
        if not items:
            continue
        lines.append("%s (%s):" % (key, label))
        for it in items:
            lines.append("  #%s %s — %s" % (
                it["task_id"], _short(it.get("task_name")), task_url(cfg, it)))
    return "\n".join(lines)


def compose_nudge(result, cfg):
    """The bounded (<= 700 char) keystroke nudge for watchdog Job 49. Empty
    string when A ∪ B ∪ C is empty (nothing to nudge)."""
    a, b, c = result.get("A", []), result.get("B", []), result.get("C", [])
    if not (a or b or c):
        return ""
    head = "%s — klientske úlohy čakajú na teba." % result["summary"]

    def _ids(items, cap=6):
        return ", ".join("#%s" % it["task_id"] for it in items[:cap]) or "—"

    parts = [head]
    if a:
        parts.append("A (nezodpovedaný komentár klienta): %s" % _ids(a))
    if b:
        parts.append("B (fáza bez správy streamu): %s" % _ids(b))
    if c:
        parts.append("C (Verifikácia bez potvrdenia > lehota): %s" % _ids(c))
    parts.append("Akcia: 👷 reakcia na komentár, presun fázy, ticket + lane, "
                 "draft do U (po schválení ownerom).")
    txt = "\n".join(parts)
    if len(txt) > _NUDGE_MAX_CHARS:
        txt = txt[: _NUDGE_MAX_CHARS - 1] + "…"
    return txt


def persist_status(result, home=None, now=None):
    """Write the LAST-run A/B/C summary to `~/.claude/task-hygiene/status.json`
    for the footer, the quals `--task-hygiene` flag, and the Stop hook (never a
    live Odoo call in any of those paths). Records `a`/`b`/`c` counts, the
    oldest A comment ts (for the Stop hook's >24h gate), `b_verif` (B members in
    Verifikácia, for the "Verifikácia bez správy" Stop block), and short
    item lines."""
    now = now if now is not None else datetime.datetime.now(
        datetime.timezone.utc).timestamp()
    a = result.get("A", [])
    b = result.get("B", [])
    c = result.get("C", [])
    a_ts = [it["ts"] for it in a if isinstance(it.get("ts"), (int, float))]
    b_verif = sum(1 for it in b if _is_verif_stage_name(it.get("stage")))
    payload = {
        "ts": now,
        "a": len(a), "b": len(b), "c": len(c),
        "a_oldest_ts": (min(a_ts) if a_ts else None),
        "b_verif": b_verif,
        "a_items": ["#%s %s" % (it["task_id"], _short(it.get("task_name"), 40))
                    for it in a[:10]],
        "b_items": ["#%s %s" % (it["task_id"], _short(it.get("task_name"), 40))
                    for it in b[:10]],
    }
    path = status_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(payload, h)
    os.replace(tmp, path)
    return payload


def _is_verif_stage_name(stage_name):
    """A B-item's stage is Verifikácia (used for the Stop hook's B>0 gate).
    Matched on the human stage name captured at compute time (Verifikácia /
    Verifik…), stage-vocabulary tolerant."""
    return isinstance(stage_name, str) and stage_name.lower().startswith("verif")


def read_status(home=None):
    """The persisted status dict, or None when absent/corrupt (fail-safe)."""
    path = status_path(home)
    try:
        with open(path, encoding="utf-8") as h:
            data = json.load(h)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def a_count(home=None):
    """The persisted A count (unanswered client comments) from the last
    watchdog run, or 0 when absent/corrupt (never a live Odoo call). Used by
    `slice-quals`/`core-quals --task-hygiene`."""
    st = read_status(home)
    if not isinstance(st, dict):
        return 0
    a = st.get("a")
    return a if isinstance(a, int) and not isinstance(a, bool) else 0


# --------------------------------------------------------------------------- #
# CLI — airuleset.py task-hygiene [--init | --check | --json]
# --------------------------------------------------------------------------- #
def cmd_task_hygiene(args):
    """`--init` writes the config template (never overwrites); `--check`
    validates it (prints `not configured` when absent — NEVER an error, so the
    watchdog gate stays clean); the default reads live A/B/C and prints the
    report (`--json` for machine output), persisting status.json."""
    path = getattr(args, "path", None) or ro.default_config_path(
        getattr(args, "home", None))

    if getattr(args, "init", False):
        if os.path.exists(path):
            print("task-hygiene: config already exists at %s" % path)
            return 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as h:
            h.write(ro.config_template())
        print("task-hygiene: wrote config template to %s "
              "(fill in the per-stream values; the API key stays in ~/.secrets)"
              % path)
        return 0

    cfg = ro.load_config(path)
    if cfg is None:
        print("task-hygiene: not configured on this box")
        return 0

    if getattr(args, "check", False):
        ok, missing = ro.config_valid(cfg)
        if ok:
            print("task-hygiene: config ok (%s)" % cfg.get("instance_url"))
        else:
            print("task-hygiene: config invalid — missing: %s"
                  % ", ".join(missing))
        return 0

    # Live path — the ONLY network path (never reached by the test suite).
    ok, missing = ro.config_valid(cfg)
    if not ok:
        print("task-hygiene: config invalid — missing: %s" % ", ".join(missing))
        return 0
    try:
        client = ro.client_from_config(cfg)
        result = compute_hygiene(client.call, cfg)
    except ro.OdooError as e:
        print("task-hygiene: Odoo read failed: %s" % e)
        return 0
    persist_status(result, home=getattr(args, "home", None))
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(format_report(result, cfg))
    return 0
