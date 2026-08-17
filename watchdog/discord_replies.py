"""Discord-reply DELIVERY cluster (issue #433 item G, step 9) — "the beast".

Extracted VERBATIM from ``watchdog/__init__.py``: job 7's owner-reply router
(``deliver_discord_replies``, the 459-line core, moved WHOLE — never split
internally, per the binding design), its compose/record/verify helpers, the
#449 never-silent orphan floor + grace/channel-memory merge, the ticket-fallback
gh-comment lane (``_gh_comment``), and ALL eight ``dreply`` constants.

Direction: BACK-REFERENCE. Every cross-cluster call to a name that lives outside
this module — whether still in ``__init__`` (the step-7 ``_orphan_*`` question
helpers, the step-8 card/flag cluster, ``_gh_call``/``_gh_env`` until those steps
land) or already relocated + re-exported by an earlier step (``discord_api``,
``stash``, ``tmux_io``, ``pane_text``, ``pane_classify``, ``transcripts``,
``cross_stream``) — is reached at CALL TIME through the package namespace as
``watchdog.<name>`` (design C3, the #1 monkeypatch-decoupling hazard). This is
correct no matter which module physically owns the name today, so step 9 is safe
to land before steps 7/8 (the DI defaults are all call-time ``None`` + in-body
resolution, so no def-time binding on those modules exists). Co-moved names that
NO test patches at the package path stay bare (module-local); the four PATCHED
names keep the ``watchdog.`` form wherever referenced — ``_gh_comment`` in-body
here, the three ``clean_reply_text`` regex/cap constants only via their
``discord_api.py`` consumer (here merely defined + re-exported). Every moved name is re-exported into the
``watchdog`` namespace by ``__init__`` so ``watchdog.<name>`` /
``monkeypatch.setattr(watchdog, ...)`` seams stay live unchanged.

The one def-time default (``deliver_discord_replies``' ``projects_dir=PROJECTS_DIR``)
uses ``from watchdog import PROJECTS_DIR`` (bound in ``__init__`` above this
module's re-import position — legal, cycle-free). ``_DREPLY_TYPED_TTL_S`` is NOT
in the design's eight-constant move list, so it deliberately stays in ``__init__``
and is reached as ``watchdog._DREPLY_TYPED_TTL_S``.
"""
import re
import time

import watchdog
from watchdog import PROJECTS_DIR

DISCORD_REPLY_MAX_CHARS = 1500       # cap a typed answer (Discord msgs ≤ 2000)
_DREPLY_DONE_CAP = 200               # bounded dedup set of delivered reply ids
_MENTION_TOKEN_RX = re.compile(r"<@[!&]?\d+>")

_CONTROL_CHAR_RX = re.compile(r"[\x00-\x1f\x7f\x80-\x9f]")

DREPLY_TICKET_FALLBACK_S = 180      # reply blocked this long → deliver via the ticket
# A box with NO pane for the asking session may not be the pane's HOST — a
# hosted stream's session (montalu claude inside newlevel's tmux) is delivered
# by the HOST watchdog's keystrokes. The no-pane fallback therefore waits
# longer, so the host wins the race and a double gh comment cannot happen; for
# a genuinely dead session it still fires (later), never silently.
DREPLY_NOPANE_FALLBACK_S = 900

_TICKET_NUM_RX = re.compile(r"#(\d{1,6})")

# --- #449: the NEVER-SILENT floor for owner answer attempts ---------------- #
# How long job 7 keeps fetching a QUESTION channel after its last live/grace
# entry vanished (state["dreply_channels"], {channel: {"ts", "q"}}). This is
# what lets the orphan floor below see a reply that lands AFTER the grace
# window (or against an empty map — the david 2026-08-13 incident state):
# without a tracked entry the channel set used to be empty and job 7 returned
# before fetching anything at all, so the loss left zero journal lines.
DREPLY_CHANNEL_MEMORY_S = 7 * 24 * 3600


def _merge_grace_questions(qmap, now, dry_run, logs):
    """#449: merge the GRACE store into `qmap` for reply matching, expiring
    entries past QUESTION_GRACE_S as we go (this is the store's ONLY
    consumer, so it owns the lifecycle — the prune deliberately never
    expires, so a bare run_once test without a Discord fetcher never
    touches the store). A dry-run drops nothing (#304 discipline) but still
    merges the same view. Returns the set of ids merged FROM grace so
    `_delivered` drops them from the right store. Residual, documented: a
    HOSTED foreign user's own grace store is not merged (historical shape,
    no live hosted stream today — the never-silent floor still covers it
    via the channel memory)."""
    from notify import (QUESTION_GRACE_S, drop_grace_question,
                        load_grace_questions)
    grace_ids = set()
    for gid, grec in list(load_grace_questions().items()):
        if not isinstance(grec, dict):
            if not dry_run:
                drop_grace_question(gid)
            continue
        gts = grec.get("pruned")
        if isinstance(gts, bool) or not isinstance(gts, (int, float)):
            gts = grec.get("ts")
            if isinstance(gts, bool) or not isinstance(gts, (int, float)):
                gts = 0
        if now - gts > QUESTION_GRACE_S:
            if not dry_run:
                drop_grace_question(gid)
            logs.append("question grace expired — dropped %s [%s]"
                        % (str(gid)[-6:],
                           watchdog.project_label(str(grec.get("cwd") or ""))))
            continue
        if gid not in qmap:
            qmap[gid] = grec
            grace_ids.add(gid)
    return grace_ids

def _refresh_channel_memory(chan_seen, channels, q_channels, now):
    """#449: expire + refresh the question-channel memory (the shape stored
    in state["dreply_channels"]: {channel: {"ts", "q"}}). Every channel
    currently carrying a tracked entry is re-stamped `now`; a remembered
    channel survives DREPLY_CHANNEL_MEMORY_S past its last sighting, so the
    orphan floor can still SEE an answer that arrives after every entry
    expired. Returns the fresh dict — the CALLER decides whether to persist
    it (never on dry-run, #304) and widens its own fetch/question sets."""
    kept = {}
    for chx, ent in chan_seen.items():
        if not isinstance(ent, dict):
            continue
        cts = ent.get("ts")
        if isinstance(cts, bool) or not isinstance(cts, (int, float)):
            continue
        if now - cts <= DREPLY_CHANNEL_MEMORY_S:
            kept[str(chx)] = {"ts": cts, "q": bool(ent.get("q"))}
    for chx in channels:
        prev_q = bool((kept.get(chx) or {}).get("q"))
        kept[chx] = {"ts": now, "q": prev_q or (chx in q_channels)}
    kept.pop("", None)
    return kept

def _orphan_floor(msg, ch, allowed, qmap, cardmap, q_channels, now, env,
                  dry_run, skip_ids, orphan_done, orphan_done_set,
                  state, persist, logs):
    """#449 NEVER-SILENT floor: an owner's answer attempt that cannot be
    routed (a reply to a message no longer tracked — pruned/superseded past
    grace — or a plain non-reply message in a questions thread the security
    gate can never match) must leave a journal line AND ping the owner,
    never vanish. The journal line is unconditional (the floor even when
    Discord itself is down); the ping is deduped per message id —
    state-marked only on a CONFIRMED send ("sent"/"dedup"), so a transient
    send error retries next sweep, and a dry-run marks nothing (#304)."""
    mid_o = str(msg.get("id") or "").strip() if isinstance(msg, dict) else ""
    if not mid_o or mid_o in skip_ids or mid_o in orphan_done_set:
        return
    reason = watchdog._orphan_answer_reason(msg, allowed, qmap, cardmap,
                                   q_channels, ch, now)
    if not reason:
        return
    logs.append("reply orphaned (%s) %s [%s]" % (reason, mid_o[-8:], ch))
    from notify import forget_marker, marker_delivered, send as _send
    dkey = "dorphan:%s" % mid_o
    st_o = _send(watchdog._orphan_ping_text(msg, reason), env=env,
                 dedup_key=dkey, dry_run=dry_run, kind="questions")
    # #449-review F2: "dedup" alone is NOT confirmation — notify.send's
    # dedup CLAIM marker is written BEFORE the POST, so a retry after one
    # transient POST failure short-circuits to "dedup" with zero further
    # POSTs. Only marker_delivered (the status recorded AFTER the POST,
    # #135) is the honest read — the #134 marker-presence-vs-delivery
    # trap, avoided at this call site. An undelivered "dedup" claim is
    # additionally RELEASED so the NEXT sweep's send genuinely re-POSTs
    # instead of short-circuiting at the stale claim forever (worst case
    # of the release: one duplicate ⚠️ ping under a rare cross-process
    # race — strictly better than a permanently silent one).
    if not dry_run and (st_o == "sent"
                        or (st_o == "dedup" and marker_delivered(dkey))):
        orphan_done_set.add(mid_o)
        orphan_done.append(mid_o)
        state["dorphan_done"] = orphan_done[-_DREPLY_DONE_CAP:]
        persist()
    elif not dry_run and st_o == "dedup":
        forget_marker(dkey)

def _ticket_fallback_text(r):
    q = " ".join(str(r.get("question") or "").split())[:400]
    return ("**ODPOVEĎ UŽÍVATEĽA Z DISCORDU** (doručená na ticket — session input "
            "bol obsadený/wedged, watchdog ticket-fallback): «%s»\n\n"
            "Otázka: %s\n\n"
            "Zariaď sa podľa odpovede (číslo = poradie ponúknutej možnosti)."
            % (" ".join(str(r.get("text") or "").split()), q))

def _gh_comment(cwd, num, text, user=None):
    """Post `text` as a comment on issue #num of the repo at `cwd` (the DURABLE
    delivery lane of the ticket-fallback). `user`: a FOREIGN question's repo —
    run via `sudo -n -u <user>` with THEIR gh auth and a login-shell cd (their
    HOME isn't even cd-able for this process). Best-effort: False on any
    failure — the reply then stays pending on the keystroke path."""
    import shlex
    import subprocess
    try:
        if user:
            p = subprocess.run(
                ["sudo", "-n", "-u", user, "bash", "-lc",
                 "cd %s && exec gh issue comment %d -F -"
                 % (shlex.quote(str(cwd)), int(num))],
                input=text, capture_output=True, text=True, timeout=30)
        else:
            p = subprocess.run(["gh", "issue", "comment", str(num), "-F", "-"],
                               cwd=cwd or None, env=watchdog._gh_env(), input=text,
                               capture_output=True, text=True, timeout=25)
        return p.returncode == 0
    except Exception:
        return False

def compose_reply_prompt(r):
    """The ONE-LINE prompt typed into the asking session for a Discord reply.

    A reply may land hours/days after the ❓ was asked — a bare '1' is
    meaningless once the session's context no longer holds the question (user
    ask, 2026-07-17), so the prompt carries WHEN + WHAT was asked + the answer.
    One line only: send_continue types the text literally then presses Enter, a
    newline would submit early. A legacy map entry without stored question text
    falls back to the raw reply (the pre-2026-07-17 behavior)."""
    # The re-arm tail closes the ping-pong: a /goal loop correctly ENDS on a
    # blocked ❓ (stop condition A) — after the answer resolves the ticket the
    # session must re-print the continuation /goal + arm question so the
    # watchdog auto-arm re-starts the loop (montalu break, 2026-07-20: the
    # answer landed, nothing re-armed, bounce tickets rotted).
    rearm = (" Ak predtým bežal /goal loop (ukončil sa touto otázkou), po "
             "vyriešení vytlač continuation /goal + arm otázku s prázdnym "
             "inputom — auto-arm ho nalepí sám.")
    q = str(r.get("question") or "").strip()
    if not q:
        return r["text"] + rearm
    ts = r.get("asked_ts") or 0
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts
            else "nedávno")
    return ("Odpoveď z Discordu: %s ti bola cez Discord položená táto otázka: "
            "«%s» Užívateľ na ňu teraz odpovedal: «%s» — zariaď sa podľa tejto "
            "odpovede (číslo = poradie ponúknutej možnosti).%s"
            % (when, q, " ".join(str(r.get("text") or "").split()), rearm))

def _record_dreply_typed(state, pid, prompt, now):
    """Remember the TAIL of text job 7 just typed into `pid`, so job 10 can
    recognize the SAME text sitting stuck as MACHINE (issue #35) instead of
    pinging the user about their own delivery's swallowed submit. Pruned to
    the last 48h so a dead pane's entry doesn't linger forever."""
    typed = state.get("dreply_typed")
    typed = ({k: v for k, v in typed.items()
             if isinstance(v, dict) and now - (v.get("ts") or 0) < watchdog._DREPLY_TYPED_TTL_S}
            if isinstance(typed, dict) else {})
    typed[pid] = {"head": prompt[:160], "tail": prompt[-160:], "ts": now}
    state["dreply_typed"] = typed

def _is_dreply_machine_text(state, pid, head_txt, txt):
    """True if the pane's input box holds text job 7 typed into `pid` — so job
    10 auto-submits our OWN swallowed delivery instead of pinging the user
    about it. `head_txt` is the box's HEAD row, `txt` its tail.

    BOTH ENDS must agree (#193). The recorded value is a 160-char tail and a
    WRAPPED box's boundary row can be a single character, so the original bare
    `txt in tail` substring test was satisfied by very nearly any draft the
    moment wrapped boxes became readable — which would have let job 10
    classify a genuine USER draft as MACHINE, skip its at-rest guards, and
    auto-submit it. That is the one thing job 10's own docstring forbids.

    An entry recorded before this change carries no `head` and therefore never
    matches: job 10 falls back to PINGING about it rather than typing, which
    is the safe direction, and the 48h TTL retires such entries anyway."""
    rec = (state.get("dreply_typed") or {}).get(pid)
    if not isinstance(rec, dict):
        return False
    tail = str(rec.get("tail") or "")
    head = str(rec.get("head") or "")
    if not tail or not head or not txt or not head_txt:
        return False
    if not (tail.endswith(txt) or txt in tail):
        return False
    return head.startswith(head_txt) or head_txt.startswith(head)

def _box_holds_our_own_text(captured, text):
    """True if the pane's input box holds OUR OWN `text` — a prior delivery
    whose submit was swallowed — and not merely something that happens to END
    the way `text` does (#193).

    Job 7 used `text.endswith(_input_line_text(captured))` alone. That was
    unreachable for a wrapped box (which read as None) and became lethal the
    moment wrapped boxes were readable: the boundary row of a wrapped draft
    can be a SINGLE character, and every reply prompt ends with the same fixed
    sentence, so a FOREIGN draft would satisfy it — authorising a bare `Enter`
    that submits the user's unsent text AND marking the Discord answer
    delivered although it was never typed.

    The box's HEAD row is the discriminator: our own stuck text starts where
    `text` starts. Both ends must agree."""
    box = watchdog._find_input_box(captured)
    if box is None:
        return False
    head, tail, wrapped = box
    if not wrapped:
        content = head[1:].strip()
        return bool(content) and text.endswith(content)
    head_txt = head[1:].strip()
    return bool(head_txt) and text.startswith(head_txt) and text.endswith(tail)

def deliver_discord_replies(now, run, state, panes_by_sid, dry_run=False,
                            discord_fetch=None, env=None, gh_comment=None,
                            hosted_users=None, foreign_questions=None,
                            foreign_drop=None, cwd_by_sid=None,
                            projects_dir=PROJECTS_DIR, reaction_fetch=None,
                            card_gh_fn=None, persist=None, sleep_fn=None):
    """Route owner Discord replies into the sessions that asked (job 7) — AND
    (#297/#298) a ❓/❔ REACTION on a tracked bot message, and a REPLY on a
    tracked completion CARD.

    `panes_by_sid`: {session_id: (pane_id, captured_pane)} for the live `claude`
    panes this cycle (collected in run_once). `cwd_by_sid`: {session_id: cwd} —
    needed to resolve "the nearest live session of repo X" for #297's fallback
    and #298's post-reopen nudge (`{}` when omitted — both features then simply
    find nothing live, same as an empty box). `discord_fetch(channel, token)`:
    returns recent messages (injectable for tests). Delivers each matching reply
    ONCE (dedup on reply id + the question is dropped on delivery), only into an
    IDLE-input pane, and reacts ✅ on success. Returns log lines. Never raises.

    2026-07-20 (#1832 incident) hardening:
    - VERIFY after typing: a swallowed Enter (queued-prompt wedge, airuleset#20)
      leaves the text sitting at `❯` — up to 2 corrective Enters; still stuck →
      NOT delivered (the fallback clock keeps ticking), and the next cycle
      recognizes OUR OWN stuck text (prompt tail at `❯`) and presses Enter only,
      never retypes (no doubled text).
    - TICKET-FALLBACK: a reply blocked > DREPLY_TICKET_FALLBACK_S (busy pane
      with a foreign draft, dead session, persistent wedge) is delivered as a
      gh comment on the #N parsed from the stored question text — the DURABLE
      lane (the loop's question tracking lives on the ticket anyway); then
      ✅-reacted + dropped like a normal delivery. No #N / gh failure → the
      keystroke path keeps retrying as before.

    `card_gh_fn` (#298, deliberately a DIFFERENT name from the ticket-fallback
    `gh_comment`/its local `gh_fn` below — the two are unrelated gh call sites
    and sharing a name would silently shadow one of them): injectable
    `(argv, input_text=None) -> (ok, stdout)` for the card-reopen flow's `gh`
    calls; defaults to a real subprocess (`_gh_call`).

    `persist` (#297/#298 review MAJOR-4): the caller's save-state closure,
    called immediately after EACH successful card-reopen or flag-react
    dedup-marks — mirrors jobs 8/11's "dedup memory BEFORE the next item"
    shape, so a mid-sweep kill between two fetched messages never loses the
    marker for a mutation that already landed (a real `gh` reopen/comment or
    a real keystroke delivery). Defaults to a no-op; `run_once`'s own trailing
    `save_state()` still covers the ordinary case."""
    from notify import (bot_token, drop_grace_question, drop_question,
                        forget_marker, known_owner_ids, load_cards,
                        load_questions, _read_env)
    logs = []
    env = _read_env() if env is None else env
    qmap = load_questions()
    cardmap = load_cards()
    cwd_by_sid = cwd_by_sid or {}
    # Merge HOSTED users' question maps (2026-07-21: montalu's claude used to
    # run in THIS tmux, its ❓ map living under /home/montalu — the session was
    # invisible to both watchdogs; historical since the 2026-07-24 subdev
    # migration gave montalu its own tmux, #33/#34 — kept generic for the next
    # shared-tmux stream). `q_owner` remembers which foreign user owns
    # a merged question so delivery drops it from THEIR map.
    hosted_users = hosted_users or {}
    f_load = foreign_questions or watchdog._foreign_questions
    f_drop = foreign_drop or watchdog._foreign_drop_question
    q_owner = {}                        # question id -> foreign unix user
    for fu in sorted(set(hosted_users.values())):
        for qid, rec in (f_load(fu) or {}).items():
            if qid not in qmap and isinstance(rec, dict):
                qmap[qid] = rec
                q_owner[qid] = fu
    # (#449) Merge the GRACE store — entries the prune/supersede moved out
    # of the main map. A reply to a graced entry still routes NORMALLY for
    # QUESTION_GRACE_S (see _merge_grace_questions). And the QUESTION-
    # CHANNEL MEMORY: with an EMPTY map this function used to return before
    # fetching anything, which is exactly how david's answer vanished with
    # zero journal lines (2026-08-13) — remembered channels keep the fetch
    # alive for the orphan floor below.
    grace_ids = _merge_grace_questions(qmap, now, dry_run, logs)
    chan_seen = state.get("dreply_channels")
    chan_seen = dict(chan_seen) if isinstance(chan_seen, dict) else {}
    if (not qmap and not cardmap and not state.get("dreply_pointer")
            and not chan_seen):
        return logs
    token = bot_token(env)
    allowed = known_owner_ids(env)
    if not token or not allowed:
        return logs
    fetch = discord_fetch or watchdog.fetch_channel_messages
    gh_fn = gh_comment or watchdog._gh_comment
    react_fetch = reaction_fetch or watchdog.fetch_reaction_users
    card_gh = card_gh_fn or watchdog._gh_call
    persist = persist or (lambda: None)

    done = state.get("dreply_done")
    done = list(done) if isinstance(done, list) else []
    done_set = set(done)
    blocked = state.get("dreply_blocked")
    blocked = dict(blocked) if isinstance(blocked, dict) else {}
    acked = state.get("dreply_acked")
    acked = list(acked) if isinstance(acked, list) else []
    acked_set = set(acked)
    card_done = state.get("dcard_done")
    card_done = list(card_done) if isinstance(card_done, list) else []
    card_done_set = set(card_done)
    react_done = state.get("dreact_done")
    react_done = list(react_done) if isinstance(react_done, list) else []
    react_done_set = set(react_done)
    # MINOR-7 (#297/#298 review): a malformed/legacy qmap/cardmap entry (not
    # a dict) must not crash the channel-set build — skip it, never guess.
    q_channels = {str(v.get("channel") or "") for v in qmap.values()
                  if isinstance(v, dict)}
    q_channels.discard("")
    channels = set(q_channels)
    channels |= {str(v.get("channel") or "") for v in cardmap.values()
                if isinstance(v, dict)}
    channels.discard("")
    # (#449) update + expire the channel memory (persist only on a real
    # sweep — #304), widen the fetch set with it, and remember which
    # remembered channels were QUESTION channels (the orphan floor's scope).
    kept_chans = _refresh_channel_memory(chan_seen, channels, q_channels, now)
    if not dry_run:
        state["dreply_channels"] = kept_chans
    channels |= set(kept_chans)
    q_channels |= {c for c, e in kept_chans.items() if e.get("q")}
    orphan_done = state.get("dorphan_done")
    orphan_done = list(orphan_done) if isinstance(orphan_done, list) else []
    orphan_done_set = set(orphan_done)

    def _ack(r):
        # ✅ = RECEIPT, fired the moment the reply is MATCHED (even while the
        # delivery pends) — a green check minutes late reads as "answer lost"
        # (3rd user report, 2026-07-20). Once per reply.
        if r["reply_id"] in acked_set:
            return
        if not dry_run:
            watchdog._react_ok(r["channel"], r["reply_id"], token)
            # #304: a --dry-run sweep must NEVER mark the dedup set — this
            # `acked`/`acked_set` pair is persisted into
            # state["dreply_acked"] unconditionally by run_once, so a
            # dry-run mark here would poison the REAL next sweep's dedup
            # state exactly like the done_set/done leak below.
            acked_set.add(r["reply_id"])
            acked.append(r["reply_id"])

    def _delivered(r, via_ticket=None):
        # dry-run simulates delivery — it must NEVER mutate the real on-disk
        # map (a dropped live question loses the answer), and it must NEVER
        # mark the reply done either (#304): run_once persists `done`/
        # `done_set` into state["dreply_done"] unconditionally, so a
        # dry-run mark here would make the FOLLOWING real sweep believe the
        # reply was already delivered and silently skip it.
        if not dry_run:
            done_set.add(r["reply_id"])
            done.append(r["reply_id"])
            fu = q_owner.get(r["referenced"])
            if fu:
                f_drop(fu, r["referenced"])
            elif r["referenced"] in grace_ids:
                drop_grace_question(r["referenced"])   # #449: graced entry
            else:
                drop_question(r["referenced"])
            # #304 review MINOR-5: popping the reply's "first blocked at"
            # timestamp is itself a real mutation of persisted state (the
            # fallback-deadline clock) -- a dry-run simulating delivery via
            # the idle-pane fast path must not silently wipe a REAL clock a
            # concurrent/earlier real sweep already started.
            blocked.pop(r["reply_id"], None)
            # #515: this delivered answer is the RELIABLE "the owner answered"
            # signal -- capture (session, cwd, #N) so job 32 can clear the
            # ticket's needs-answer/needs-decision label once the asking session
            # moves past the `❓`. Cheap (no I/O); a question with no #N is a
            # no-op. Inside `not dry_run` so a dry-run never mutates state.
            watchdog.capture_answered_ticket(state, r, now)
        qmap.pop(r["referenced"], None)     # same-batch 2nd reply won't re-fire
        if via_ticket:
            logs.append("reply→ticket #%s [%s]" % (via_ticket, r["session"][:12]))
        else:
            logs.append("reply→%s [%s]" % (watchdog.project_label(r["cwd"]), r["session"][:12]))

    def _pending(r, why):
        # #304 review MINOR-5: creating the "first blocked at" timestamp is
        # itself real persisted state (the fallback-deadline clock) — a
        # dry-run must not create one that didn't already exist.
        if not dry_run:
            blocked.setdefault(r["reply_id"], now)
        # The durable lane once the keystroke path has been blocked long
        # enough. NO pane = we may not be the pane's HOST (a hosted stream's
        # session lives in another user's tmux) — defer longer so the host
        # delivers by keystroke first; a busy/wedged pane WE own keeps the
        # tight deadline. #304 review MAJOR: this whole branch is a REAL
        # mutation (a gh comment, plus a durable state["dreply_pointer"]
        # entry that a LATER real sweep types into a live pane as an
        # instruction to go read it) — a --dry-run diagnostic must never
        # fake success here. The original code's `if dry_run: ok = True`
        # did exactly that: a real keystroke telling a live session to read
        # a ticket comment that was never actually posted.
        if not dry_run:
            deadline = (DREPLY_NOPANE_FALLBACK_S if why == "no pane"
                        else DREPLY_TICKET_FALLBACK_S)
            if now - blocked[r["reply_id"]] >= deadline:
                m = _TICKET_NUM_RX.search(r.get("question") or "")
                if m:
                    fu = q_owner.get(r["referenced"])
                    if fu:
                        ok = gh_fn(r["cwd"], m.group(1), _ticket_fallback_text(r),
                                   user=fu)
                    else:
                        ok = gh_fn(r["cwd"], m.group(1), _ticket_fallback_text(r))
                    if ok:
                        ptr = state.get("dreply_pointer")
                        ptr = dict(ptr) if isinstance(ptr, dict) else {}
                        ptr[r["session"]] = {"num": m.group(1), "ts": now}
                        state["dreply_pointer"] = ptr
                        _delivered(r, via_ticket=m.group(1))
                        return
        logs.append("reply pending (%s) %s" % (why, r["session"][:12]))

    for ch in sorted(channels):
        for msg in fetch(ch, token):
            # (#297) ❓/❔ REACTION on a TRACKED bot message -> ask the
            # sending stream about it. Independent of the reply-shaped
            # checks below — a message can be a reply AND separately
            # flagged. Cheap in the common (untracked) case: the reaction
            # COUNT is already on `msg` from the channel fetch, so
            # `_flag_target` (a dict lookup, no network) runs BEFORE the
            # one extra call (`react_fetch`) that verifies WHO reacted.
            mid = str(msg.get("id") or "").strip()
            emoji = watchdog._flagged_emoji(msg)
            if mid and emoji and mid not in react_done_set:
                target = watchdog._flag_target(mid, qmap, cardmap)
                if target is not None:
                    users = react_fetch(ch, mid, emoji, token)
                    if watchdog._reacted_by_owner(users, allowed):
                        deliver = watchdog._flag_delivery_target(
                            target, panes_by_sid, cwd_by_sid, run=run)
                        if deliver is not None:
                            d_sid, d_pid, d_cwd, d_exact = deliver
                            prompt = watchdog.compose_flag_prompt(msg.get("content"))
                            # #505: verify the flag-prompt delivery against the
                            # TARGET pane's OWN transcript (keyed on the exact
                            # sid — precise, never a cwd-collision sibling), so a
                            # swallowed Enter is retried next sweep, not booked.
                            d_tpath = watchdog._transcript_for_session(
                                projects_dir, d_sid, d_cwd)
                            # MAJOR-1 (adversarial review): the EXACT
                            # asking session's own ❓ marker must never
                            # block delivery of the flag ABOUT that very
                            # question — job 7's own idle/draft gate, not
                            # job 8's at-rest discipline, which refuses
                            # any ❓-marker session outright.
                            if d_exact:
                                ok = watchdog._deliver_flag_prompt_to_exact_session(
                                    d_pid, run, prompt, dry_run, logs=logs,
                                    tpath=d_tpath, state=state, now=now,
                                    sleep_fn=sleep_fn)
                            else:
                                ok = watchdog._nudge_repo_pane(d_pid, d_cwd, run, prompt,
                                                      dry_run, projects_dir,
                                                      logs=logs, tpath=d_tpath,
                                                      state=state, now=now,
                                                      sleep_fn=sleep_fn)
                            if ok and not dry_run:
                                react_done_set.add(mid)
                                react_done.append(mid)
                                state["dreact_done"] = react_done[-_DREPLY_DONE_CAP:]
                                persist()
                                logs.append("flag-react→%s [%s]"
                                            % (watchdog.project_label(d_cwd),
                                               d_sid[:12] if d_sid else "-"))
                            elif ok:
                                logs.append("flag-react (dry-run)→%s [%s]"
                                            % (watchdog.project_label(d_cwd),
                                               d_sid[:12] if d_sid else "-"))
                            else:
                                logs.append("flag-react pending (busy) %s"
                                            % mid[-8:])
                        else:
                            logs.append("flag-react pending (no live "
                                        "session) %s" % mid[-8:])

            # (#298) REPLY on a tracked completion CARD -> reopen the
            # ticket with the remark. `parse_discord_card_reply` returns
            # None for a reply whose referenced id is NOT a card (incl.
            # every ❓-ping reply the block below already owns), so this
            # never competes with the existing reply flow.
            cr = watchdog.parse_discord_card_reply(msg, allowed, cardmap)
            if cr and cr["reply_id"] not in card_done_set:
                if dry_run:
                    reopened = True
                else:
                    reopened = watchdog._card_reopen_flow(cr["repo"], cr["issue"],
                                                 cr["text"], gh_fn=card_gh)
                if reopened and not dry_run:
                    card_done_set.add(cr["reply_id"])
                    card_done.append(cr["reply_id"])
                    state["dcard_done"] = card_done[-_DREPLY_DONE_CAP:]
                    persist()
                    logs.append("card-reopen #%s (%s)"
                                % (cr["issue"], cr["repo"]))
                    # MINOR-9: prefer the FETCHING channel `ch` (this is the
                    # channel the reply was actually read from) — `cr["channel"]`
                    # is only a best-effort fallback for a card record from
                    # before that field existed.
                    watchdog._react_ok(ch or cr["channel"], cr["reply_id"], token)
                    bare = cr["repo"].rstrip("/").split("/")[-1]
                    # MAJOR-5: the ticket's own run-card dedup marker (written
                    # at card-send time) would otherwise block a FRESH card
                    # once the worker re-fixes and re-closes this issue —
                    # release it now that the user has flagged the old one
                    # as needing another look.
                    forget_marker("%s#%s" % (bare, cr["issue"]))
                    ctarget = watchdog._repo_live_pane(bare, cwd_by_sid, panes_by_sid,
                                              run=run)
                    if ctarget is not None:
                        c_sid, c_pid, c_cwd = ctarget
                        # #505: transcript-proof the reopen nudge against the
                        # found pane's OWN sid-resolved transcript.
                        c_tpath = watchdog._transcript_for_session(
                            projects_dir, c_sid, c_cwd)
                        watchdog._nudge_repo_pane(
                            c_pid, c_cwd, run,
                            watchdog.compose_card_reopen_nudge(cr["issue"]),
                            dry_run, projects_dir, logs=logs, tpath=c_tpath,
                            state=state, now=now, sleep_fn=sleep_fn)
                elif reopened:
                    logs.append("card-reopen (dry-run) #%s (%s)"
                                % (cr["issue"], cr["repo"]))
                else:
                    logs.append("card-reopen failed #%s (%s)"
                                % (cr["issue"], cr["repo"]))

            r = watchdog.parse_discord_reply(msg, allowed, qmap, bot_id="")
            if not r:
                # (#449) NEVER-SILENT floor — see _orphan_floor: an owner's
                # unroutable answer attempt journals + pings, never vanishes.
                _orphan_floor(msg, ch, allowed, qmap, cardmap, q_channels,
                              now, env, dry_run,
                              done_set | card_done_set,
                              orphan_done, orphan_done_set,
                              state, persist, logs)
                continue
            if r["reply_id"] in done_set:
                continue
            _ack(r)
            pane = panes_by_sid.get(r["session"])
            if not pane:
                # the asking session isn't a live pane right now — retry next
                # cycle (the question stays in the map; TTL prunes if it's gone).
                _pending(r, "no pane")
                continue
            pid, captured = pane
            prompt = compose_reply_prompt(r)
            # a PRIOR wedged delivery of THIS reply left our own text at `❯`.
            # Both ENDS of the box must agree that it is ours — a tail-only
            # suffix test submits a foreign draft (#193).
            own_stuck = _box_holds_our_own_text(captured, prompt)
            if not (watchdog.pane_at_idle_prompt(captured) or own_stuck):
                # A genuinely busy mid-turn pane / open dialog we must never
                # type over (#233) has no free `❯` at all — but an IDLE pane
                # holding a FOREIGN draft (also lands here, since its box
                # isn't bare) is no longer a dead end (issue #35): stash the
                # draft, deliver, let CC auto-restore it. Any ambiguity
                # (busy pane, copy-mode, a verify failure) falls straight
                # through to the pre-#35 pending/fallback path, unchanged.
                if not dry_run and not watchdog.pane_in_mode(pid, run):
                    _record_dreply_typed(state, pid, prompt, now)
                    if watchdog.deliver_with_stash(pid, prompt, run, captured=captured,
                                          logs=logs):
                        idead = state.get("inputdead")
                        if isinstance(idead, dict):
                            idead.pop(r["session"], None)
                            state["inputdead"] = idead
                        _delivered(r)
                        continue
                _pending(r, "busy")
                continue
            if watchdog.pane_in_mode(pid, run):
                continue
            if not dry_run:
                if own_stuck:
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                else:
                    _record_dreply_typed(state, pid, prompt, now)
                    watchdog.send_continue(pid, prompt, run)
                # verify the input box emptied — a swallowed Enter (#20) leaves
                # the text at `❯`; up to 2 corrective Escape+Enter retries, then
                # give up. Escape FIRST (issue #36) — a swallow while the
                # agent-strip selector holds focus makes a bare Enter navigate
                # ("view agent") instead of submitting; ONE Escape clears that.
                # Never two Escapes in a row — that permanently deletes a draft.
                #
                # #372 (4th incident, forensically confirmed) — a FALSE
                # "delivered" confirmation is a trust-breaking defect: the
                # sender legitimately believes their reply reached the
                # session while it never did. `_input_line_text` returns
                # `None` (undeterminable — a dialog, a spinner, a genuinely
                # unreadable capture) as a value DISTINCT from `""`
                # (genuinely bare, confirmed empty) — the ORIGINAL `while t2`
                # / `if t2:` checks treated `None` identically to `""` (both
                # falsy), so an unreadable post-send capture was silently
                # accepted as proof of delivery. Require the EXPLICIT `""`
                # confirmation — never a merely-falsy one.
                t2 = watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30))
                tries = 0
                while t2 != "" and tries < 2:
                    run(["tmux", "send-keys", "-t", pid, "Escape"])
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                    t2 = watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30))
                    tries += 1
                if t2 != "":
                    blocked.setdefault(r["reply_id"], now)
                    logs.append(
                        "reply wedged (enter swallowed) %s" % r["session"][:12]
                        if t2 is not None else
                        "reply wedged (verify unreadable) %s"
                        % r["session"][:12])
                    # An ACTIVE session with a DEAD input box (the 4th #20
                    # recurrence) is invisible to job 10 — count our own
                    # verify failures; >= 3 cycles → ONE deduped ping (the
                    # armed /goal survives a kill + resume, so the restart
                    # is safe to ask for).
                    idead = state.get("inputdead")
                    idead = dict(idead) if isinstance(idead, dict) else {}
                    idead[r["session"]] = int(idead.get(r["session"]) or 0) + 1
                    state["inputdead"] = idead
                    if idead[r["session"]] == 3:   # exactly once per episode
                        from notify import send as _send
                        _send("⚠️ **%s** — session BEŽÍ, ale jej VSTUP je "
                              "mŕtvy (Enter sa opakovane nechytá — wedge). "
                              "Nedá sa jej nič doručiť. Reštart: v okne kill "
                              "claude procesu + `claude` (resume) — armovaný "
                              "/goal reštart prežije."
                              % watchdog.project_label(r["cwd"]),
                              env=env,
                              dedup_key="inputdead:%s" % r["session"][:12],
                              dry_run=dry_run)
                    continue
            # #304 review MINOR-5: clearing the wedge-episode counter is a
            # real mutation of persisted alerting state — a dry-run
            # simulating delivery via this idle-pane fast path must not
            # silently reset a REAL wedge counter (delaying/hiding the
            # 3-cycle alert threshold above).
            if not dry_run:
                idead = state.get("inputdead")
                if isinstance(idead, dict):
                    idead.pop(r["session"], None)
                    state["inputdead"] = idead
            _delivered(r)

    # A ticket-fallback delivery is durable but INVISIBLE in the terminal —
    # the user watching the window assumes the answer vanished (2026-07-20).
    # Type a short visible pointer into the asking pane as soon as it is
    # typable; once per fallback, expired after a day.
    ptr = state.get("dreply_pointer")
    ptr = dict(ptr) if isinstance(ptr, dict) else {}
    for sid in list(ptr):
        ent = ptr[sid] if isinstance(ptr[sid], dict) else {}
        if now - (ent.get("ts") or 0) > 86400:
            ptr.pop(sid)
            continue
        pane = panes_by_sid.get(sid)
        if not pane:
            continue
        pid, captured = pane
        if not watchdog.pane_at_idle_prompt(captured) or watchdog.pane_in_mode(pid, run):
            continue
        ptr_text = ("Odpoveď užívateľa na tvoju otázku je na tickete #%s "
                    "(komentár ODPOVEĎ UŽÍVATEĽA Z DISCORDU) — prečítaj ho a "
                    "zariaď sa podľa neho." % ent.get("num"))
        if dry_run:
            ptr.pop(sid)
            logs.append("reply pointer→#%s [%s]" % (ent.get("num"), sid[:12]))
            continue
        # #497 — transcript-proof send: the one-shot `ptr.pop(sid)` consumes the
        # pointer, so consume it ONLY on a VERIFIED submit. A swallowed one
        # (agent-strip #36) must leave the pointer in place so the next sweep
        # retries (until its own 1-day expiry above) instead of silently losing
        # the visible answer-hint — strictly safer than the old unconditional pop.
        tpath = watchdog._transcript_for_session(
            projects_dir, sid, (cwd_by_sid or {}).get(sid))
        if watchdog.send_verified(pid, ptr_text, run, tpath,
                                  sleep_fn=sleep_fn, logs=logs):
            ptr.pop(sid)
            logs.append("reply pointer→#%s [%s]" % (ent.get("num"), sid[:12]))
        else:
            logs.append("reply pointer unverified→#%s [%s], retry next sweep"
                        % (ent.get("num"), sid[:12]))
    state["dreply_pointer"] = ptr

    state["dreply_done"] = done[-_DREPLY_DONE_CAP:]
    state["dreply_acked"] = acked[-_DREPLY_DONE_CAP:]
    state["dreply_blocked"] = {k: v for k, v in blocked.items()
                               if now - v < 86400}
    state["dcard_done"] = card_done[-_DREPLY_DONE_CAP:]
    state["dreact_done"] = react_done[-_DREPLY_DONE_CAP:]
    return logs
