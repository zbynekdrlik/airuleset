"""Job 49 companions for #1180: the reaction-403 degrade and class H.

Split out of ``cli_task_hygiene`` (which sits at its size-ratchet ceiling) and
called from ``compute_hygiene``. Both pieces use the same injected read-only
``call(model, method, **body)`` seam, so they are unit-tested against a fake,
never a live Odoo.

REACTION 403 (odoo-erp#8507, montalu1 29.9.2026): the handover key may not read
``mail.message.reaction_ids``, and the batched comment read used to request that
field, so ONE HTTP 403 failed the whole A/B/C run. ``read_messages`` retries
that read ONCE without ``reaction_ids`` when, and only when, the first attempt
was an HTTP 403 AND the field was requested. Any other failure, including a 403
that persists without the field, still raises, so a real ACL/network fault is
never hidden. With the field gone, A/C run without the reaction signal: a 👍 is
not auto-seen through ``reaction_ids``, but the guarded
``message_reactions_guarded`` path still answers where the key may use it. The
job logs ``reactions unavailable (403)`` once per day (``watchdog/task_hygiene``).

CLASS H: a STREAM-authored HANDOVER message on an open task that is NOT in the
verification stage ``H_GRACE_S`` (10 min) after it was posted, with no stage move
since. That is the post-without-move the owner escalated twice (montalu1 28.9.,
montalu4 29.9.).
- Handover shape: the rule-3 ``Čo skúsiť`` heading + the literal ``stačí 👍``
  (client-board-stages.md rule 3; ``is_handover_body``, shared with
  ``gates.handover``). The approval ref is a poster-side argument, not part of
  the posted body. The server filter is the loose ``👍`` alone; Python decides.
- Degraded A: with ``reaction_ids`` 403'd AND the guarded method unavailable, A
  items carry ``reaction_unknown`` (listed + counted, never Stop-gated — a 👷 ack
  cannot be seen, so the Stop gate must not demand one).
- A stage move AFTER the handover (``date_last_stage_update`` newer than the
  message) is a deliberate later decision, e.g. a client rejection back to
  Realizácia, so it is not H.
- Residual: a task that is only NAMED in a handover posted elsewhere (another
  task's chatter, a Discuss thread) has no message of its own. The
  ``gates.handover`` Stop check covers it through ``Acceptance-thread:``.
"""
import datetime
import re

import cli_odoo_ro as ro
from cli_odoo_board import m2o, parse_dt

H_GRACE_S = 600
REACTIONS_403_LOG = ("reactions unavailable (403) — A/C run without the "
                     "reaction_ids signal (guarded reactions still read)")
_H_LIMIT = 1000
# The rule-3 handover SHAPE, shared with gates.handover (ONE definition): the
# "Čo skúsiť" section heading AND the literal closing "stačí 👍" — two loose words
# ("Skúsili ste…? Stačí napísať") are a client question, never a handover.
_GAP = r"(?:\s|&nbsp;|&#160;|<[^>]{0,40}>)*"
_SHAPE_RX = (re.compile(r"[ČčCc]o" + _GAP + r"sk[úu]si", re.IGNORECASE),
             re.compile(r"sta[čc][íi]" + _GAP + r"(?:👍|&#128077;|:\+1:|:thumbsup:)",
                        re.IGNORECASE))
_TASK_BASE_FIELDS = ("id", "name", "stage_id", "date_last_stage_update")


def task_fields(extra=()):
    """The project.task read fields: the base set (incl. the stage-move stamp
    class H compares against) plus the auto-close extras, de-duplicated."""
    return list(dict.fromkeys(list(_TASK_BASE_FIELDS) + list(extra)))


def read_messages(call, **body):
    """The batched ``mail.message`` search_read → ``(rows, reactions_ok)``.

    Degrades ONLY a 403 that the ``reaction_ids`` field caused; see the module
    docstring."""
    fields = list(body.get("fields") or [])
    try:
        return (call("mail.message", "search_read", **body) or []), True
    except ro.OdooError as e:
        if "reaction_ids" not in fields or " HTTP 403" not in str(e):
            raise
    slim = dict(body, fields=[f for f in fields if f != "reaction_ids"])
    return (call("mail.message", "search_read", **slim) or []), False


def is_handover_body(body):
    """True iff ``body`` carries the rule-3 handover shape (see ``_SHAPE_RX``)."""
    return isinstance(body, str) and all(rx.search(body) for rx in _SHAPE_RX)


def guarded_reactions_ok(call, msgs):
    """True iff the guarded reaction method answers on this instance (probed
    with ONE message id). With ``reaction_ids`` 403'd AND this False, a client
    comment the stream already 👷-acked is indistinguishable from an unanswered
    one, so its A item is marked ``reaction_unknown`` (listed, never Stop-gated)."""
    mid = next((m.get("id") for m in msgs if isinstance(m.get("id"), int)), None)
    if mid is None:
        return False
    try:
        call("mail.message", "message_reactions_guarded", ids=[mid])
    except ro.OdooError:
        return False
    return True


def daily_notes(result, state, now):
    """Job-49 log lines that must appear ONCE per UTC day, never per sweep: the
    reaction 403 degrade and a failed/truncated class-H read."""
    if not isinstance(state, dict):
        return []
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()
    out = []
    for key, text in (("task_hygiene_r403_day",
                       REACTIONS_403_LOG if result.get("reactions_unavailable") else ""),
                      ("task_hygiene_herr_day", result.get("h_error") or "")):
        if text and state.get(key) != day:
            state[key] = day
            out.append("task-hygiene: " + text)
    return out


def compute_h(call, tasks, cfg, is_stream, now):
    """``(items, error)`` — class H members, or an error string when the read
    failed (H then reports nothing; A/B/C are unaffected)."""
    verif = (cfg.get("stage_ids") or {}).get("verifikacia")
    cand = {t.get("id"): t for t in tasks
            if m2o(t.get("stage_id"))[0] != verif and t.get("id") is not None}
    if not cand:
        return [], None
    try:
        rows = call("mail.message", "search_read",
                    domain=[["model", "=", "project.task"],
                            ["res_id", "in", sorted(cand)],
                            ["message_type", "=", "comment"],
                            ["body", "ilike", "👍"]],   # loose; _SHAPE_RX decides
                    fields=["id", "author_id", "date", "res_id", "body"],
                    order="res_id, date desc, id desc", limit=_H_LIMIT) or []
    except ro.OdooError as e:
        return [], "class H read failed: %s" % e
    err = ("class H read truncated at %d rows (raise _H_LIMIT)" % _H_LIMIT
           if len(rows) >= _H_LIMIT else None)
    newest = {}
    for m in rows:
        rid = m.get("res_id")
        rid = rid if isinstance(rid, int) else m2o(rid)[0]
        dt = parse_dt(m.get("date"))
        if (rid not in cand or dt is None or not is_stream(m.get("author_id"))
                or not is_handover_body(m.get("body"))):
            continue
        if rid not in newest or dt > newest[rid]:
            newest[rid] = dt
    items = []
    for rid in sorted(newest):
        posted, t = newest[rid], cand[rid]
        age_s = (now - posted).total_seconds()
        moved = parse_dt(t.get("date_last_stage_update"))
        if age_s < H_GRACE_S or (moved is not None and moved > posted):
            continue
        items.append({"task_id": rid, "task_name": t.get("name") or "",
                      "stage": m2o(t.get("stage_id"))[1],
                      "minutes": int(age_s // 60)})
    return items, err
