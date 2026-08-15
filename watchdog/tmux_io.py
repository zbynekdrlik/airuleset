"""tmux I/O shims + pane keystroke helpers (the only impure part of the watchdog).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 2 of the
definitive module split (issue #433). Everything here shells out to ``tmux``
(injectable as ``run``, defaulting to :func:`_default_run`) or walks ``/proc``:
the tmux socket-recovery + sudo-hosted-pane discovery (:func:`list_claude_panes`
and friends), pane capture / mode / owner reads, and the keystroke senders
(:func:`send_continue`, :func:`send_selfcheck`, :func:`send_subagent_nudge`) plus
the shared dying-subagent nudge logic. Every name here is re-exported into the
``watchdog`` namespace by the positional facade import in ``__init__.py``, so all
existing ``watchdog.<name>`` seams (goal / compact / cross_stream / janitor,
hooks, tests) keep resolving unchanged.

Direction: back-reference module. Cross-references to any name that was a
top-level ``watchdog`` name go through the package namespace call-time
(``import watchdog`` at module top; ``watchdog.<name>(...)`` in bodies), which is
what keeps ``monkeypatch``/``patch.object(watchdog, ...)`` seams effective:
- ``watchdog._default_run`` -- the step-2 C5 grep found it patched
  (``patch.object(wd, "_default_run", ...)`` in test_goal_arm), so the seven
  ``run = run or watchdog._default_run`` fallbacks go through the package
  namespace, NOT a bare module-local name (the design's "module-local" note
  assumed a def-time ``run=_default_run`` default, but the real shape is a body
  ``or`` fallback and C5 proves it a live seam).
- ``watchdog._pane_hosted_claude_pid`` / ``watchdog._hosted_claude_cwd`` --
  patched by test_sudo_hosted_pane.
- ``watchdog.capture_pane`` / ``watchdog.pane_in_mode`` / ``watchdog.send_continue``
  -- the three heaviest monkeypatch seams in the codebase (the design mandates
  the package-namespace form for these even where a single step's grep is empty).
- ``watchdog.pane_at_idle_prompt`` (pane_classify, patched), ``watchdog.decide_working``
  (still in ``__init__``), ``watchdog.transcript_last_error`` /
  ``watchdog._iter_jsonl_tail`` (transcripts.py) -- external readers reached
  call-time through the package namespace.
Intra-module calls to names the step-2 C5 grep proves UNPATCHED (``_proc_read``,
the ``_tmux_*`` socket helpers, ``_strip_selected``, ``send_subagent_nudge``,
``_subagent_transcript_unsalvageable``) stay bare (byte-verbatim, C3 exception).

``NUDGE_TEXT`` / ``WORKING_NUDGE_TEXT`` still live in ``__init__.py`` (bound above
the facade-import position, proven unpatched) and are imported here at module top
-- ``NUDGE_TEXT`` is :func:`send_continue`'s def-time default, so it MUST be a
from-import, never a below-position ``from watchdog import <function>`` shape.
"""

import os
import re

import watchdog
from watchdog import NUDGE_TEXT, WORKING_NUDGE_TEXT


def _default_run(argv, timeout=8):
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _proc_read(path):
    try:
        with open(path) as h:
            return h.read()
    except OSError:
        return ""                # process exited mid-walk — expected race


def _pane_hosted_claude_pid(pane_pid):
    """PID of a `claude` process inside the pane's process TREE, or None — a
    sudo-hosted stream session (`sudo su - montalu` → bash → claude) reports
    pane_current_command='sudo', which hid the montalu pane from every
    watchdog job (2026-07-20: /goal auto-arm structurally impossible there).
    Pure /proc walk, fail-safe None."""
    try:
        children = {}
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            stat = _proc_read("/proc/%s/stat" % p)
            if not stat:
                continue
            ppid = stat.rsplit(") ", 1)[-1].split()[1]
            children.setdefault(ppid, []).append(p)
        frontier = [str(int(pane_pid))]
        while frontier:
            cur = frontier.pop()
            for ch in children.get(cur, []):
                if "claude" in _proc_read("/proc/%s/comm" % ch):
                    return ch
                frontier.append(ch)
    except Exception:
        return None              # fail-safe: an unreadable /proc = not hosted
    return None


def _hosted_claude_cwd(claude_pid, pane_cwd):
    """The hosted claude process's REAL cwd — tmux reports the SUDO root's cwd
    (where the human ran `sudo su`, e.g. /home/newlevel/devel/odoo), which
    mis-binds every cwd-keyed lookup. Direct readlink works only same-user;
    a foreign process needs `sudo -n -u <owner>`. Falls back to the pane cwd."""
    import subprocess
    link = "/proc/%s/cwd" % claude_pid
    try:
        return os.readlink(link)
    except OSError:
        status = _proc_read("/proc/%s/status" % claude_pid)
        m2 = re.search(r"^Uid:\s+(\d+)", status, re.M)
        if m2:
            try:
                import pwd
                user = pwd.getpwuid(int(m2.group(1))).pw_name
                p = subprocess.run(["sudo", "-n", "-u", user, "readlink", link],
                                   capture_output=True, text=True, timeout=5)
                out = (p.stdout or "").strip()
                if p.returncode == 0 and out.startswith("/"):
                    return out
            except Exception:
                return pane_cwd  # no passwordless sudo → best effort
    return pane_cwd


def _tmux_default_socket_path():
    """The tmux server's DEFAULT control socket path -- `$TMUX_TMPDIR` (or
    `/tmp` when unset) + `tmux-<uid>/default` -- what every managed session
    (the one hosting a `claude` pane) uses. A project MAY run OTHER tmux
    servers on `-L`/`-S` sockets alongside it (this repo's own scripts/tests
    do, and dev2 runs a real `-L t2` server right now) -- those are simply a
    DIFFERENT server this function has no opinion about; the recovery this
    module performs only ever targets the DEFAULT socket, since that is the
    one a managed Claude Code session's tmux actually binds to."""
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(base, "tmux-%d" % os.getuid(), "default")


def _tmux_socket_missing(path=None):
    """True when the tmux control socket the server should be listening on
    is absent from disk -- the orphaned-server shape (#318): a `tmpfiles.d`
    age-based reap (or any other removal) of `/tmp/tmux-*` deletes the
    socket FILE while the server PROCESS keeps running, so every NEW client
    connection to it fails from then on, although the session itself is
    alive. `path` is overridable for tests; production always resolves the
    real default socket path."""
    return not os.path.exists(path or _tmux_default_socket_path())


def _tmux_server_pids(run=None):
    """PIDs of every live `tmux: server` process OWNED BY THIS UID, found
    via `ps` (never the tmux socket itself -- the whole point is this must
    still resolve even when the socket is unreachable). Adversarial review
    of #318 measured live: `ps -e` also lists OTHER users' tmux servers on
    a shared box (subdev's montalu/marek/david), and a box can genuinely
    run MORE THAN ONE server for the SAME uid (dev2 right now: a default
    socket AND a `-L t2` one) -- picking just the first candidate can
    signal the WRONG server (a foreign uid's SIGUSR1 attempt just EPERMs
    silently; a same-uid `-L` server's own SIGUSR1 only ever recreates ITS
    OWN socket, never the default one, confirmed live). So this returns
    EVERY same-uid candidate, in `ps`'s own order, for the caller to try
    each in turn until the DEFAULT socket actually comes back. Empty when
    no such process is running at all (the ordinary "tmux genuinely isn't
    up" case -- nothing to recover, behavior unchanged from before #318).
    Injectable via `run` like every other tmux shim in this module."""
    run = run or watchdog._default_run
    out = run(["ps", "-eo", "pid,uid,comm"])
    my_uid = os.getuid()
    pids = []
    for line in (out or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[2].strip() != "tmux: server":
            continue
        try:
            pid, uid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if uid == my_uid:
            pids.append(pid)
    return pids


def _tmux_socket_recover(pid, run=None):
    """SIGUSR1 to a tmux server whose socket was removed out from under it
    re-creates the socket at its configured path (tmux(1) SIGNALS: "If the
    socket is accidentally removed, the SIGUSR1 signal may be sent to the
    tmux server process to recreate it") -- the SAME recovery the #318
    incident applied by hand (`kill -USR1 <server-pid>`). Only ever called
    after `_tmux_socket_missing()` has already confirmed the DEFAULT socket
    is genuinely gone, so this never signals a healthy default-socket
    server -- it CAN still be sent to the wrong same-uid candidate (a `-L`
    server) when several exist, which is why `list_claude_panes` retries
    the real query after EACH candidate rather than trusting the first."""
    run = run or watchdog._default_run
    run(["kill", "-USR1", str(pid)])


def list_claude_panes(run=None, logs=None, dry_run=False):
    """[(pane_id, cwd)] for every tmux pane running `claude` — directly, or
    hosted under sudo/su (the montalu-in-newlevel-tmux stream shape) — deduped
    by pane_id (grouped sessions share the same pane_id).

    Self-heals the orphaned-tmux-server shape (#318): `tmux list-panes -a`
    returning EMPTY is structurally ambiguous on its own -- a live server
    always hosts >=1 pane, so empty means EITHER genuinely no tmux server is
    running, OR the server is alive but its socket FILE was reaped out from
    under it (the live incident: subdev's `/tmp/tmux-1000/` was recreated by
    a tmpfiles-clean-shaped age-based sweep while both the tmux server and
    david's claude session kept running -- every watchdog job funnels
    through THIS function, so recovering here fixes job 8's false "no
    session" bounce ping and every other pane-reading job at once, instead
    of teaching each one to special-case it). `logs`, if a list, gets one
    line describing the recovery attempt and its outcome -- best-effort,
    callers that don't care about it (nearly all of them) just omit it.
    `dry_run=True` (adversarial-review finding) logs what WOULD be tried
    but never sends the real SIGUSR1, so a `watchdog --once --dry-run` stays
    genuinely side-effect-free through every caller that threads it here."""
    run = run or watchdog._default_run
    query = ["tmux", "list-panes", "-a", "-F",
             "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"
             "\t#{pane_pid}"]
    out = run(query)
    if not (out or "").strip() and _tmux_socket_missing():
        # `_tmux_socket_missing()` is a cheap stat -- checked BEFORE the
        # `ps -e` process-table scan below (MINOR-1, #318 review) so a box
        # whose socket is intact never pays that cost, even when
        # list-panes came back empty for some other, unrelated reason.
        pids = _tmux_server_pids(run)
        if pids and dry_run:
            if logs is not None:
                logs.append("tmux-socket-orphaned server-pid=%d -- "
                            "would recover via SIGUSR1 (dry-run)" % pids[0])
        elif pids:
            recovered = False
            for pid in pids:
                if logs is not None:
                    logs.append("tmux-socket-orphaned server-pid=%d -- "
                                "recovering via SIGUSR1" % pid)
                _tmux_socket_recover(pid, run)
                out = run(query)
                if (out or "").strip():
                    if logs is not None:
                        logs.append("tmux-socket-recovered")
                    recovered = True
                    break
            if not recovered and logs is not None:
                logs.append("tmux-socket-recovery-failed server-pids=%s"
                            % ",".join(str(p) for p in pids))
    seen, res = set(), []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, cmd, cwd = parts[0].strip(), parts[1].strip(), parts[2].strip()
        ppid = parts[3].strip() if len(parts) > 3 else ""
        if not pid or pid in seen:
            continue
        if cmd != "claude":
            if cmd not in ("sudo", "su") or not ppid:
                continue
            cpid = watchdog._pane_hosted_claude_pid(ppid)
            if not cpid:
                continue
            cwd = watchdog._hosted_claude_cwd(cpid, cwd)
        seen.add(pid)
        res.append((pid, cwd))
    return res


def pane_in_mode(pane_id, run=None):
    """True if the pane is in tmux copy-mode / a modal (the user is scrolling, or a
    menu is open). Sending keys then would be swallowed or would corrupt the user's
    selection — so the watchdog skips such a pane this cycle (without burning a
    retry)."""
    run = run or watchdog._default_run
    out = run(["tmux", "display-message", "-p", "-t", pane_id, "#{pane_in_mode}"])
    return (out or "").strip() == "1"


def capture_pane(pane_id, run=None, lines=40):
    """Last `lines` of the pane's visible content. Used ONLY for the ping-only
    waiting-on-user detector — never for the api-error action trigger (that is
    flag-only, after the pane-text-fallback incident)."""
    run = run or watchdog._default_run
    return run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", "-%d" % lines])


def pane_owner(pane_id, run=None):
    """Lowercase tmux owner (zbynek / marek) of a SPECIFIC pane, so a ping about
    that pane @mentions the right person — the watchdog runs headless (systemd
    --user) with NO tmux context of its own, so it must resolve the owner from the
    waiting/stalled pane, not from itself. Matches notify.resolve_owner's
    normalization ('marek-12' → 'marek')."""
    run = run or watchdog._default_run
    for fmt in ("#{session_group}", "#S"):
        out = (run(["tmux", "display-message", "-p", "-t", pane_id, fmt]) or "").strip()
        if out:
            out = re.sub(r"-\d+$", "", out)
            return re.sub(r"[^a-z0-9]", "", out.lower())
    return ""


def _strip_selected(captured):
    """True if the agent-strip SELECTOR holds focus — any line renders as a
    selected strip row (`❯ ● main` / `❯ ◯ <agent>`, issue #36). While
    selected, Enter navigates ("view agent") instead of submitting the input
    box — a bare Enter typed there is silently swallowed. Scans every line
    (not just the boundary) since the selection can sit below other chrome.
    Fail-safe direction: a false positive costs one harmless extra Escape,
    never a lost draft."""
    if not captured:
        return False
    for ln in captured.splitlines():
        s = ln.strip()
        if s.startswith("❯ ●") or s.startswith("❯ ◯"):
            return True
    return False


def send_continue(pane_id, text=NUDGE_TEXT, run=None):
    """Type `text` literally into the pane, then press Enter to submit it.

    Captures the pane FIRST (issue #36): if the agent-strip selector holds
    focus (`_strip_selected`), send ONE Escape before typing — otherwise the
    submit Enter can be swallowed as "view agent" instead of submitting our
    text. Best-effort only: we do NOT re-verify the Escape actually cleared
    the selection — proceed with the type + Enter regardless (today's
    behavior), since the retry paths (job 7's verify loop, job 10's machine
    submit) already Escape-and-retry on a swallowed submit. NEVER send a
    second Escape here — a rapid double-Escape into a pane holding a draft
    PERMANENTLY DELETES it (empirically confirmed, issue #35)."""
    run = run or watchdog._default_run
    captured = watchdog.capture_pane(pane_id, run, lines=10)
    if _strip_selected(captured):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
    # #372 round-2 adversarial-review MINOR-2 -- `--` (end-of-options) is
    # required for the SAME reason `_type_literal` already carries it
    # (#322): real tmux parses a literal argument via getopt, so text
    # whose first character is `-` (an arbitrary Discord-reply prompt,
    # never /goal-prefixed, is exactly such a case) is read as an unknown
    # FLAG and the whole send silently fails -- `_default_run` swallows a
    # non-zero exit as "" with no exception and no log, leaving the box
    # bare and every caller's own post-send verify reading a FALSE
    # "delivered" (the box genuinely is empty, just never received the
    # text at all).
    run(["tmux", "send-keys", "-t", pane_id, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])


def send_selfcheck(pane_id, run=None):
    """Job 4's self-check nudge — the autonomous form of the user's manual 'stucked?'.
    Types WORKING_NUDGE_TEXT into the pane and submits it, prompting the session to
    verify the liveness of its launched work and intervene if it died silently."""
    watchdog.send_continue(pane_id, WORKING_NUDGE_TEXT, run)


def send_subagent_nudge(pane_id, worker_id, kind, run=None):
    """(issue #6) Nudge the SUPERVISOR pane about a dying BACKGROUND WORKER —
    `kind` is a short human label ('api-error' or 'text-toolcall-stall'). Types a
    stuck-check-style self-check message naming the worker's own transcript file,
    so the supervisor — the only thing that can decide resume vs re-dispatch —
    investigates. Never acts on the worker's behalf directly."""
    text = ("stuck-check: background worker %s vyzerá mŕtvy (%s v subagents/%s.jsonl) "
            "— over jeho transcript a zasiahni (dispatchni znova alebo naň nadviaž), "
            "nič nerob naslepo." % (worker_id, kind, worker_id))
    watchdog.send_continue(pane_id, text, run)


def _subagent_transcript_unsalvageable(sub_path):
    """(#287) True when a dying SUBAGENT's OWN transcript has genuinely NOTHING
    left to investigate: no `tool_use` it ever issued actually COMPLETED
    (returned a `tool_result`), and its last real entry is a bare
    `isApiErrorMessage`. Matches the reporting incident's OWN stated bar
    verbatim (odoo-erp#3036: "4 lines total... 1 tool_use — the dispatch
    itself, **0 completed tool calls**") — not "zero tool_use ever ISSUED",
    which is stricter than what the incident actually reports and would
    wrongly classify its own worker (1 issued-but-never-returned tool_use)
    as salvageable (adversarial-review finding, #287). An issued tool_use
    with no observed tool_result is exactly as un-investigable as no
    tool_use at all — the supervisor has no evidence it ever produced
    anything, so a session nudged about either shape can only ever
    re-derive the SAME "nothing to salvage" conclusion. `_nudge_dying_
    subagent` nudges such a worker AT MOST ONCE rather than the full
    nudge/nudge/nudge/escalate cycle a genuinely recoverable stall earns.

    Fails SAFE toward "salvageable" (False) on any read problem or on
    finding even ONE returned `tool_result` — under-classifying only costs
    a few extra (harmless, now BOUNDED by SUBAGENT_NUDGE_STATE_TTL_SECONDS)
    nudges, never a silently-skipped genuinely-recoverable worker. The scan
    is bounded to the last `max_lines` entries — a real completed tool_use
    sitting further back than that (an unusually long-lived worker that
    only went quiet for its own final stretch) could still be missed and
    the worker over-classified as unsalvageable; the harm stays bounded to
    one nudge instead of three, never a silently-dropped one."""
    if not watchdog.transcript_last_error(sub_path):
        return False                     # doesn't even end on an api-error
    for entry in watchdog._iter_jsonl_tail(sub_path, max_lines=500):
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        msg = entry.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False                 # a tool call actually RETURNED -> real progress
    return True


def _nudge_dying_subagent(state, logs, send_fn, pid, run, captured, project, owner,
                          now, sub_path, sub_idle, kind, dedup_prefix,
                          interval, max_nudges, dry_run):
    """(issue #6) Shared busy/idle nudge-or-ping logic for a detected dying SUBAGENT
    (jobs 1b / 4a-sub). `kind` is a human label for the nudge/ping text; `dedup_prefix`
    namespaces the state/dedup keys per detector ('apierr' / 'textcall'). Mutates
    `state` and `logs` in place. Same keystroke discipline as every other job: NEVER
    type into a copy-mode or busy (no free `❯`) pane — ping instead, mirroring job 4's
    busy-pane-wedged path — and reuse decide_working's nudge → retry → escalate
    lifecycle for the idle-pane case, so a wedged supervisor still only pings once.

    (#287) When the worker's own transcript is PROVABLY unsalvageable
    (`_subagent_transcript_unsalvageable`), `max_nudges` is capped at 1 —
    decide_working then delivers exactly ONE typed nudge (the thing that
    actually costs the session a paid turn) and escalates to a single
    passive Discord ping on its next evaluation, before going permanently
    silent — never the full multi-nudge cycle for a transcript with nothing
    left to learn from a second look."""
    wid = sub_path.stem
    if watchdog.pane_in_mode(pid, run):
        logs.append("skip in-mode (subagent-%s) %s" % (dedup_prefix, project or pid))
        return
    if not watchdog.pane_at_idle_prompt(captured):
        bkey = "subagent-busypane:%s:%s" % (dedup_prefix, wid)
        b = state.get(bkey) or {"first_seen": int(now - sub_idle), "pinged": False}
        b["last_seen"] = int(now)
        state[bkey] = b
        if not b["pinged"]:
            b["pinged"] = True
            logs.append("subagent-%s-busy %s [%s] — ping only" % (dedup_prefix, project, wid))
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s), ale hlavná session "
                    "je zaneprázdnená\n> Nezasahujem klávesami (rozbilo by to bežiacu "
                    "prácu) — over `subagents/%s.jsonl`." % (project, wid, kind, wid),
                    owner=owner, dedup_key="subagent-%s-busy:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
        else:
            logs.append("skip busy-pane (subagent-%s) %s [%s]" % (dedup_prefix, project, wid))
        return
    wkey = "subagent-%s:%s" % (dedup_prefix, wid)
    # (#287) A worker whose own transcript is provably unsalvageable earns
    # AT MOST ONE typed nudge, never the full retry cycle — see
    # _subagent_transcript_unsalvageable's own docstring.
    unsalvageable = _subagent_transcript_unsalvageable(sub_path)
    effective_max_nudges = 1 if unsalvageable else max_nudges
    action, entry = watchdog.decide_working(state, wkey, now, sub_idle,
                                   interval=interval, max_nudges=effective_max_nudges)
    state[wkey] = entry
    if action == "nudge":
        n = len(entry["nudges"])
        logs.append("subagent-%s-nudge#%d %s [%s]" % (dedup_prefix, n, project, wid))
        if not dry_run:
            send_subagent_nudge(pid, wid, kind, run)
    elif action == "escalate":
        logs.append("subagent-%s-escalate %s [%s] — gave up after %d nudges"
                    % (dedup_prefix, project, wid, effective_max_nudges))
        # (#287 adversarial-review MINOR) The unsalvageable path escalates
        # after exactly ONE nudge, often within the very next sweep — "session
        # nereaguje na nudge" (not responding) is the WRONG claim there (no
        # time to respond, and often nothing TO respond to); it stays correct
        # only for the genuine multi-nudge no-response cycle.
        if unsalvageable:
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s) transcript nemá čo "
                    "ponúknuť na skúmanie (0 dokončených tool calls pred chybou)\n> "
                    "Ďalšie skúmanie by neprinieslo nič nové — ak treba, over "
                    "`subagents/%s.jsonl` ručne." % (project, wid, kind, wid),
                    owner=owner, dedup_key="subagent-%s-giveup:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
        else:
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s) a session nereaguje na "
                    "nudge\n> Treba zásah." % (project, wid, kind),
                    owner=owner, dedup_key="subagent-%s-giveup:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
    else:
        logs.append("subagent-%s-%s %s [%s]" % (dedup_prefix, action, project, wid))
