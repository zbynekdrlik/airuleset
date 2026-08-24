"""tmux I/O shims + pane keystroke helpers (the only impure part of the watchdog).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 2 of the
definitive module split (issue #433). Everything here shells out to ``tmux``
(injectable as ``run``, defaulting to :func:`_default_run`) or walks ``/proc``:
the tmux socket-recovery + sudo-hosted-pane discovery (:func:`list_claude_panes`
and friends), pane capture / mode / owner reads, and the keystroke senders
(:func:`send_continue`, :func:`send_subagent_nudge`) plus
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
import time

import watchdog
# WORKING_NUDGE_TEXT is no longer USED here (its `send_selfcheck` caller was
# removed when job 4 adopted send_verified, #497 batch 3) but is deliberately
# RE-EXPORTED — the module-split invariant test locks tmux_io.WORKING_NUDGE_TEXT
# is watchdog.WORKING_NUDGE_TEXT (it stays resident in __init__.py). The `as`
# alias marks it an intentional re-export so ruff does not flag F401.
from watchdog import NUDGE_TEXT, WORKING_NUDGE_TEXT as WORKING_NUDGE_TEXT

# #490 — transcript-proof submit verification (`send_verified`, below). CC
# writes the accepted `user` turn near-instantly, so most confirms land on the
# first poll; the window only ever runs to the end for a genuinely swallowed
# submit. Worst case a lost keystroke runs BOTH windows (~2 × POLLS × S ≈ 20s
# of real sleep) inside ONE pane's iteration; `goal_lane_sweep`'s wall-clock
# deadline is checked only BETWEEN panes, so several wedged panes in one sweep
# trend toward the #172 timeout class (acceptable: a lost keystroke is rare and
# the happy path returns on the first poll).
SEND_VERIFY_POLLS = 10
SEND_VERIFY_S = 1
# Pre-Enter type-settle (borrowed from goal._await_typed's GOAL_TYPE_SETTLE_*):
# CC needs a moment to INGEST a multi-KB chunked paste before it renders it.
SEND_TYPE_SETTLE_POLLS = 8
SEND_TYPE_SETTLE_S = 1


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


def _proc_start_epoch(pid):
    """Epoch seconds when process `pid` STARTED — `/proc/<pid>/stat` field 22
    (`starttime`, clock ticks since boot) + `/proc/stat` `btime`. None on any
    read/parse failure. This is the per-PANE session discriminator (#645): the
    claude process's start aligns with a RESUME BOUNDARY in the session's
    transcript, the only signal that holds (fd/env/cmdline carry no sid)."""
    stat = _proc_read("/proc/%s/stat" % pid)
    if not stat:
        return None
    # comm can contain spaces/parens, so split AFTER the last ") " — field 3
    # (state) is then index 0, field 22 (starttime) index 19.
    try:
        after = stat.rsplit(") ", 1)[-1].split()
        ticks = int(after[19])
    except (IndexError, ValueError):
        return None
    btime = None
    for line in _proc_read("/proc/stat").splitlines():
        if line.startswith("btime "):
            try:
                btime = int(line.split()[1])
            except (IndexError, ValueError):
                return None
            break
    if btime is None:
        return None
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None
    if not hz:
        return None
    return btime + ticks / hz


def _pane_claude_pid(pane_pid):
    """The `claude` PID for a pane: `pane_pid` itself when it IS claude (an
    `exec claude` launch), else the claude descendant in its process tree (the
    normal shell-forks-claude launch — reuses `_pane_hosted_claude_pid`). None if
    neither. Fail-safe None on a bad pane_pid."""
    p = str(pane_pid).strip()
    if not p.isdigit():
        return None
    if "claude" in _proc_read("/proc/%s/comm" % p):
        return p
    return watchdog._pane_hosted_claude_pid(p)


def _pane_claude_start_epoch(pane_id, run=None):
    """Start-epoch of the claude process hosting tmux pane `pane_id`:
    `#{pane_pid}` → `_pane_claude_pid` → `_proc_start_epoch`. None when
    unresolved (fail-safe: the #645 disambiguation then safe-skips this pane)."""
    run = run or watchdog._default_run
    ppid = (run(["tmux", "display-message", "-p", "-t", pane_id,
                 "#{pane_pid}"]) or "").strip()
    if not ppid.isdigit():
        return None
    cpid = watchdog._pane_claude_pid(ppid)
    if not cpid:
        return None
    return watchdog._proc_start_epoch(cpid)


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


def _subagent_nudge_signature(worker_id):
    """(#491) The stable, per-worker substring that identifies our dead-worker
    stuck-check nudge in a supervisor transcript `user` turn. Built once, used
    BOTH to compose the nudge (`send_subagent_nudge`) and to recognize it as a
    LANDED nudge (`supervisor_responded_to_nudge`), so the two can never drift
    on what text to match. `wid` is the dispatched agent's own transcript stem
    — unique per dispatch — so the signature never collides across workers."""
    return "background worker %s vyzerá mŕtvy" % worker_id


def send_subagent_nudge(pane_id, worker_id, kind, run=None, tpath=None,
                        sleep_fn=None, logs=None):
    """(issue #6) Nudge the SUPERVISOR pane about a dying BACKGROUND WORKER —
    `kind` is a short human label ('api-error' or 'text-toolcall-stall'). Types a
    stuck-check-style self-check message naming the worker's own transcript file,
    so the supervisor — the only thing that can decide resume vs re-dispatch —
    investigates. Never acts on the worker's behalf directly. Returns True on a
    delivered/verified submit, False on a swallowed one.

    #497 batch 3: the text embeds the worker-id (a 36-char UUID on real boxes)
    TWICE, so it runs 238-248c → CHUNK-typed. When a `tpath` is supplied it is
    the SUPERVISOR's transcript — the pane typed into, NEVER the dying worker's
    `sub_path` — so route through the transcript-proof `send_verified` and verify
    the submit landed. The `_nudge_dying_subagent` caller marks/clears the #372
    janitor provenance around this call, so a swallowed chunk-typed residue is
    reclaimable via the shared `"stuck-check: "` own-payload prefix. Without a
    tpath, fall back to the raw `send_continue` (backward-compatible; a caller
    that cannot resolve the supervisor transcript still delivers, unverified)."""
    text = ("stuck-check: %s (%s v subagents/%s.jsonl) "
            "— over jeho transcript a zasiahni (dispatchni znova alebo naň nadviaž), "
            "nič nerob naslepo." % (_subagent_nudge_signature(worker_id), kind, worker_id))
    if tpath is not None:
        return watchdog.send_verified(pane_id, text, run, tpath,
                                      sleep_fn=sleep_fn, logs=logs)
    watchdog.send_continue(pane_id, text, run)
    return True


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
                          interval, max_nudges, dry_run, tpath=None, sleep_fn=None):
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
    # (#491) ACKNOWLEDGEMENT RESOLUTION — checked BEFORE the busy/idle branches
    # so a resolved worker is PERMANENTLY silent on BOTH paths (no keystroke,
    # no busy-pane ping). A dead worker never recovers, so the only useful
    # thing a nudge does is tell the SUPERVISOR its worker died. Once the
    # supervisor has demonstrably ACKNOWLEDGED a nudge about THIS worker — its
    # own transcript shows our nudge as a LANDED `user` turn FOLLOWED BY a
    # genuine assistant response (`supervisor_responded_to_nudge`) — it has
    # SEEN the death and owns the resume-vs-redispatch call. Re-nudging from
    # here delivers zero new information and costs a full paid turn each time
    # (the reporting incident's 244k-token resurrect-to-silence workaround,
    # #491, which the salvageable full-nudge cycle earns even AFTER #287
    # bounded the never-recover case). Mark the worker RESOLVED and go silent;
    # the state's own SUBAGENT_NUDGE_STATE_TTL_SECONDS cleanup drops it once
    # the file ages past the trigger window. Purely durable-state driven (the
    # supervisor's own transcript) — no manual ack, no supervisor-transcript
    # edit. Retry-if-unseen is preserved: a swallowed nudge writes no `user`
    # turn → no causal ack → the decide_working cycle below still fires
    # nudge#2/#3 then escalates to the human. The causal (landed-`user`-turn)
    # signal — not a mere timestamp-after — is what makes that hold in the
    # incident's own `/goal` loop, where the supervisor emits genuine
    # post-nudge turns independently of ever seeing the nudge (adversarial
    # review #491: a timestamp-only signal falsely resolved a swallowed nudge
    # on the loop's next re-fire).
    wkey = "subagent-%s:%s" % (dedup_prefix, wid)
    prev = state.get(wkey) or {}
    resolved = bool(prev.get("resolved"))
    if not resolved and (prev.get("nudges") or []):
        sup_tpath = watchdog.supervisor_transcript_for_subagent(sub_path)
        if sup_tpath is not None:
            resolved = watchdog.supervisor_responded_to_nudge(
                sup_tpath, _subagent_nudge_signature(wid))
    if resolved:
        prev["resolved"] = True
        prev["last_seen"] = int(now)       # keep sticky across the TTL window
        state[wkey] = prev
        logs.append("subagent-%s-resolved %s [%s] — supervisor acknowledged"
                    % (dedup_prefix, project, wid))
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
    # (#287) A worker whose own transcript is provably unsalvageable earns
    # AT MOST ONE typed nudge, never the full retry cycle — see
    # _subagent_transcript_unsalvageable's own docstring. (`wkey` and the
    # resolution short-circuit are handled at the top, before the busy/idle
    # branch, so a resolved worker never reaches here.)
    unsalvageable = _subagent_transcript_unsalvageable(sub_path)
    effective_max_nudges = 1 if unsalvageable else max_nudges
    action, entry = watchdog.decide_working(state, wkey, now, sub_idle,
                                   interval=interval, max_nudges=effective_max_nudges)
    state[wkey] = entry
    if action == "nudge":
        n = len(entry["nudges"])
        # #497 batch 3 — transcript-proof send (238-248c CHUNK) against the
        # SUPERVISOR's own `tpath` (the pane typed into — threaded from the
        # run_once caller, NEVER `sub_path`, the dying worker's transcript), plus
        # the #372 janitor mark BEFORE the keystroke so a swallowed chunk-typed
        # residue is reclaimable via the shared "stuck-check: " own-payload
        # prefix; cleared on a verified submit. decide_working's own cadence
        # retries a swallowed nudge, so no persist reorder — log honestly.
        ok = True
        if not dry_run:
            watchdog._janitor_mark_watch(state, pid, now)
            ok = send_subagent_nudge(pid, wid, kind, run, tpath, sleep_fn, logs)
            if ok:
                watchdog._janitor_clear_watch(state, pid)
        logs.append("subagent-%s-nudge#%d %s [%s]%s"
                    % (dedup_prefix, n, project, wid,
                       "" if ok else " (submit-unverified)"))
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


def _await_submit_confirmed(tpath, baseline, text, sleep_fn):
    """Poll (bounded, ~SEND_VERIFY_POLLS × SEND_VERIFY_S) for the accepted
    `user` turn — returns the instant `_submit_confirmed` sees it, or False
    after the window. A structured-settle poll, never a blind timeout."""
    sleep_fn = sleep_fn or time.sleep
    for i in range(SEND_VERIFY_POLLS):
        if watchdog._submit_confirmed(tpath, baseline, text):
            return True
        if i < SEND_VERIFY_POLLS - 1:
            sleep_fn(SEND_VERIFY_S)
    return False


def _await_typed_landed(pane_id, text, run, sleep_fn, want=True):
    """Poll (bounded) until the input box shows evidence of `text`
    (`want=True`) or has stopped showing it (`want=False`) — the pre-Enter
    TYPE verify. Mirrors `goal._await_typed`, reproduced here to keep tmux_io's
    `import watchdog`-only module boundary (#433, the same idiom
    `compact._compact_still_in_box` repeats). Returns the final verdict; a type
    that never renders is refused (`not want`)."""
    sleep_fn = sleep_fn or time.sleep
    for i in range(SEND_TYPE_SETTLE_POLLS):
        landed = watchdog._typed_landed(text, watchdog._input_line_text(
            watchdog.capture_pane(pane_id, run, lines=40)))
        if landed is want:
            return landed
        if i < SEND_TYPE_SETTLE_POLLS - 1:
            sleep_fn(SEND_TYPE_SETTLE_S)
    return not want


def send_verified(pane_id, text, run=None, tpath=None, sleep_fn=None, logs=None,
                  out=None):
    """Type `text` + Enter into a BARE input box and VERIFY the submit landed
    via the TRANSCRIPT (the #486 delivery bullet's structured proof), not the
    pane render: after the send, the session jsonl at `tpath` must gain a new
    `user` turn carrying `text` within a bounded window. This is the missing
    transcript-proof member of the delivery family (`deliver_with_stash` /
    `_send_goal_verified` / the compact submit-verify) — the piece a raw
    `send_continue` (type + Enter, no post-send read) never had, so a swallowed
    Enter (agent-strip selector #36, or a turn started under the send) used to
    be booked "sent" with the text left hanging in the user's input box (#490).

    The type half mirrors `_send_goal_verified`: a fresh bare re-check right
    before typing, a strip-selector Escape (#36), `_type_literal` CHUNK-typing
    (never a single multi-KB `-l` burst that CC would collapse into a paste,
    #322 — the lane nudge texts run ~550–720 chars, well past the 200-char
    threshold), and a pre-Enter TYPE verify that never submits a collapsed or
    unrendered paste. Only the SUBMIT verify differs: the transcript, not the
    pane render.

    Returns True ONLY on a transcript-CONFIRMED submit. On failure:
      - our text still PROVABLY stuck in the box -> ONE corrective Escape+Enter
        (#36; never a second Escape #35, never a bare second Enter), re-verify;
        if still stuck, `_undo_and_release_slot` backspaces exactly our own
        text off the box (verified bare before we typed, so every char is ours);
      - box bare but unconfirmed -> return False with NO corrective Escape (a
        bare box after a submit may mean a turn genuinely STARTED — an Escape
        there would interrupt it, the #233 harm) and nothing undone;
      - box holds UNRECOGNIZED content (a truncated type, a collapsed hint) ->
        withhold keystrokes (a blind backspace could eat a real draft, #193),
        log the residue HONESTLY (never claim "bare" unread, #134/#360), and
        let the caller's #372 janitor mark backstop it.
    False means "not delivered, retryable next sweep" — the caller leaves its
    own budget unconsumed.

    `out` (#594, optional): when a dict is passed, `send_verified` records the
    ONE outcome a flat bool cannot express — a submit that was DELIVERED but the
    transcript confirmation RACED. It sets `out["delivered_unconfirmed"] = True`
    in the "box bare after our Enter, submit not proven" branch: the Enter
    CLEARED the box (CC accepted/queued the submit), only the `user` turn was
    not written inside the window (the normal case when injecting into an
    actively-cycling armed `/goal` loop). The genuine-swallow path (text left
    STUCK) returns ABOVE via `_undo_and_release_slot` — text backed out, never
    accepted — so it never reaches this branch. TWO live paths DO reach it, both
    delivery: the first-Enter path (box cleared straight away) and the corrective
    Escape+Enter path (lines below) that ends bare; the latter's delivery-ness
    rests on this module's #36 premise (the corrective Escape only DESELECTS the
    agent strip, it never clears the composer), so a bare box after it means the
    Enter, not the Escape, emptied it. A caller that must not re-deliver (job
    20's re-check nudge, #594) reads `ok OR out.get("delivered_unconfirmed")` as
    "delivered", while still retrying a genuine swallow (neither True nor the flag
    set). Even in the theoretical over-claim (an Escape that DID clear a real
    composer), the only cost is the caller advancing its cadence by one period
    (job 20: ≥6h, ~daily) while the ticket stays OPEN + surfaced and job 20 is
    itself the re-check backstop — bounded and self-healing, never a permanent
    silence. Default None -> byte-identical for every existing caller.

    `tpath` is REQUIRED (the transcript is the whole proof); a falsy or
    unreadable `tpath` refuses to send rather than typing blind or reading from
    byte 0. Bare-box ONLY: a pane holding a DRAFT is delivered via
    `deliver_with_stash`; a draft that RACED into the box since the caller's own
    check is rescued and the send aborted."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    if not tpath:
        _log("send-verified abort: no transcript path")
        return False
    # A FRESH capture right before typing (the sibling helpers' own race
    # guard): the caller proved the box bare a moment ago, but round-trips pass
    # before the real keystroke lands.
    cap = watchdog.capture_pane(pane_id, run, lines=40)
    if watchdog._input_line_text(cap) != "":
        watchdog._draft_rescue_persist(pane_id, cap, logs=logs)
        _log("send-verified abort: box not bare pre-send")
        return False
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
    # Re-verify bare AFTER the strip-Escape and immediately before the type
    # keystroke — a draft racing into that gap would otherwise be typed over
    # (the same second bare-check `_send_goal_verified` does, #176-F3).
    fresh = watchdog.capture_pane(pane_id, run, lines=40)
    if watchdog._input_line_text(fresh) != "":
        watchdog._draft_rescue_persist(pane_id, fresh, logs=logs)
        _log("send-verified abort: box raced busy pre-send")
        return False
    try:
        baseline = os.path.getsize(tpath)
    except OSError:
        # An unreadable transcript cannot verify a submit; refuse rather than
        # read from byte 0 (a prior identical nudge would false-confirm).
        _log("send-verified abort: transcript unreadable pre-send")
        return False
    # #670 -- HEAD-INCLUSIVE verified type + bounded settle/undo/retry, replacing
    # the old `_type_literal` + `_await_typed_landed(want=True)` pair that
    # verified only the TAIL (`_typed_landed`'s endswith) and was head-blind: a
    # swallowed FIRST char (`ane-check...` for `lane-check...`, the send-keys
    # first-byte race) IS a suffix of the intended text, so it passed and Enter
    # submitted the corrupted prompt. `_type_literal_verified` reads the box HEAD
    # back too (it retains the same bounded render-settle tolerance, #670-review
    # R1), re-types on a genuine swallow, and on a HOLD (unreadable / collapsed)
    # box withholds every keystroke (#670-review R2) -- so a head-corrupted
    # prompt is NEVER submitted, and no keystroke is fired into a box we cannot
    # safely backspace.
    if not watchdog._type_literal_verified(pane_id, run, text, sleep_fn):
        if watchdog._pane_shows_collapsed_paste(watchdog._input_line_text(
                watchdog.capture_pane(pane_id, run, lines=40))):
            _log("send-verified abort: collapsed-paste, not submitted")
        else:
            _log("send-verified abort: type not head+tail-verified, not submitted")
        return False
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])
    if _await_submit_confirmed(tpath, baseline, text, sleep_fn):
        return True
    # Unconfirmed. Only act further when our text is PROVABLY still in the box.
    if watchdog._typed_landed(text, watchdog._input_line_text(
            watchdog.capture_pane(pane_id, run, lines=40))):
        # A swallowed Enter (#36 class) — ONE corrective Escape+Enter.
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
        run(["tmux", "send-keys", "-t", pane_id, "Enter"])
        if _await_submit_confirmed(tpath, baseline, text, sleep_fn):
            return True
        if watchdog._typed_landed(text, watchdog._input_line_text(
                watchdog.capture_pane(pane_id, run, lines=40))):
            # Genuinely stuck — back our own text off the bare-verified box so
            # the next sweep retries from a clean prompt. parked=False: bare
            # box, no stash to pop.
            watchdog._undo_and_release_slot(pane_id, run, text, False, _log,
                                            "send-verified swallowed",
                                            sleep_fn=sleep_fn)
            return False
    # Unconfirmed and NOT provably stuck. Read the box ONCE and log honestly —
    # never claim a state we did not read (#134/#360). Withhold keystrokes on
    # every branch (Escape could interrupt a turn that started #233; a blind
    # backspace could eat a real draft #193). A caller that marked janitor
    # provenance AND whose payload starts with a recognized OWN prefix
    # (`_JANITOR_OWN_PREFIXES`, e.g. the lane-check nudge) has the #372 janitor
    # reclaim any residue before the next sweep re-reads the pane.
    itext = watchdog._input_line_text(watchdog.capture_pane(pane_id, run, lines=40))
    if itext is None:
        _log("send-verified unconfirmed: box unreadable, submit not proven")
    elif itext == "":
        _log("send-verified unconfirmed: box bare, submit not proven")
        # #594: the Enter CLEARED the box (CC accepted/queued the submit) — this
        # is a DELIVERY the transcript confirmation merely raced (a cycling armed
        # loop). NOT a swallow (that path returned above with the text UNDONE).
        # Surface it so a caller that must not re-deliver treats it as delivered.
        if isinstance(out, dict):
            out["delivered_unconfirmed"] = True
    else:
        _log("send-verified unconfirmed: box holds unrecognized content, "
             "left in place (retryable)")
    return False


def submit_own_draft_verified(pane_id, draft, run=None, tpath=None,
                              sleep_fn=None, logs=None):
    """#501 — SUBMIT an EXISTING recognized-own nudge draft already sitting in
    the input box, transcript-verified — WITHOUT typing anything. The missing
    "submit an already-composed OWN draft" member of the delivery family
    (`send_verified` TYPES then submits; `deliver_with_stash` parks a FOREIGN
    draft and types around it): a pane holding OUR OWN previously-swallowed
    nudge (a pre-#490 blind Enter stranded it) must be FINISHED by submitting
    the draft in place, never stashed-around and retyped — that retype aborts
    forever against the persistent swallow that stranded it (the live cam-box
    zbynek-4:0.0 incident: `stash-abort 1/5 -> backoff -> give-up`, the nudge
    never delivered).

    HARD foreign-draft gate (never weakened — HARD CONSTRAINT a): `draft` MUST
    start with one of the UNAMBIGUOUS machine-diagnostic nudge prefixes
    (`_own_nudge_submit_prefix`: `lane-check: `/`bounce-backstop: `/`gk-request
    backstop: `, texts a human PROVABLY never types). The human-typeable
    `/goal `/`/compact` prefixes are refused here (content is not proof of
    ownership for them — the #372 janitor recovers those only WITH provenance).
    Any unrecognized/foreign draft is refused with ZERO keystrokes: NEVER a
    blind Enter on a user's parked draft.

    Recognition + verification read the box HEAD row (`_input_box_head_text`),
    NEVER the tail (`_input_line_text`): every real own nudge is 289-720 chars
    and WRAPS, so its prefix sits on the head and is absent from the tail (#501
    -- reading the tail made this path dead against exactly the wrapped drafts
    the incident is about).

    Transcript-proof (HARD CONSTRAINT b): after the Enter, the session jsonl at
    `tpath` must gain a NEW top-level `user` turn carrying the HEAD-ROW TEXT
    (the draft's own leading substring -- wrap-safe AND far more specific than
    the bare prefix, so a foreign turn merely containing `lane-check: ` cannot
    false-confirm) within the bounded window (`_await_submit_confirmed`). A
    swallowed Enter (#36) earns ONE corrective Escape+Enter (never a second
    Escape #35), re-verified. Never booked delivered on a pane render alone.

    No keystroke ever RE-TYPES or BACKSPACES the draft: the box already holds
    our own text, and we neither typed it nor can prove its exact length (a
    wrapped multi-row draft makes a byte-exact undo unprovable), so on a
    genuinely-stuck submit we leave it EXACTLY as it is — a legit pending own
    nudge — and return False; the caller's give-up ping escalates (#193: never
    destroy an unproven buffer).

    Returns True ONLY on a transcript-CONFIRMED submit; False = not delivered,
    retryable next sweep (the caller leaves its own budget unconsumed). A
    falsy/unreadable `tpath` refuses (the transcript is the whole proof)."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    prefix = watchdog._own_nudge_submit_prefix(draft)
    if not prefix:
        _log("submit-own abort: draft is not an unambiguous own nudge")
        return False
    if not tpath:
        _log("submit-own abort: no transcript path")
        return False
    # A FRESH capture right before the Enter (the sibling helpers' own race
    # guard): the box must STILL hold the same OWN draft. Recognition reads the
    # box HEAD row (`_input_box_head_text`), NOT `_input_line_text` (the TAIL):
    # every real own nudge is 289-720 chars and WRAPS at a live pane width, so
    # its prefix sits on the head row and is NEVER on the tail (#501 -- keying
    # on the tail made this whole path DEAD against exactly the wrapped drafts
    # the incident is about). A draft that raced OUT (bare / submitted) or a
    # FOREIGN draft that raced IN must NEVER be Entered.
    cap = watchdog.capture_pane(pane_id, run, lines=40)
    head = watchdog._input_box_head_text(cap)
    if not head or not head.startswith(prefix):
        _log("submit-own abort: box no longer holds the recognized own draft")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("submit-own abort: live turn")
        return False
    # A SELECTED agent-strip row (#36) steals the Enter — ONE Escape returns
    # focus to the input box (the draft survives ONE Escape; two would delete
    # it, #35), then re-confirm the draft is still there before submitting.
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
        cap = watchdog.capture_pane(pane_id, run, lines=40)
        head = watchdog._input_box_head_text(cap)
        if not head or not head.startswith(prefix):
            _log("submit-own abort: own draft gone after strip Escape")
            return False
    try:
        baseline = os.path.getsize(tpath)
    except OSError:
        _log("submit-own abort: transcript unreadable pre-send")
        return False
    # The head row IS the draft's leading substring (the first ~pane-width
    # chars), so it appears verbatim in the transcript's `user` turn AND is far
    # more specific than the bare 12-char prefix — a wrap-safe, low-false-
    # confirm verification token (a foreign turn would have to carry this whole
    # ~170-char line, not just `lane-check: `). Its only failure mode is a
    # benign non-confirm -> retry, never a false positive nor a destroyed draft.
    token = head
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])
    if _await_submit_confirmed(tpath, baseline, token, sleep_fn):
        _log("submit-own delivered")
        return True
    # Unconfirmed. Only send a corrective Escape+Enter when our OWN draft is
    # PROVABLY still in the box (a swallowed Enter, #36) — never a second
    # Escape (#35), never an Escape into a box that already went bare (a turn
    # may have started, #233).
    still = watchdog._input_box_head_text(watchdog.capture_pane(pane_id, run, lines=40))
    if still and still.startswith(prefix):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
        run(["tmux", "send-keys", "-t", pane_id, "Enter"])
        if _await_submit_confirmed(tpath, baseline, token, sleep_fn):
            _log("submit-own delivered (after corrective Escape+Enter)")
            return True
    # Genuinely unconfirmed. Leave the box EXACTLY as-is — never backspace our
    # own draft (we did not type it, cannot prove its length, and it is a legit
    # pending own nudge). Read the box ONCE and log honestly (#134/#360), never
    # claim a state we did not read; the caller's give-up escalation fires.
    final = watchdog._input_box_head_text(watchdog.capture_pane(pane_id, run, lines=40))
    if final is None:
        _log("submit-own unconfirmed: box unreadable, submit not proven")
    elif final.startswith(prefix):
        _log("submit-own unconfirmed: own draft still in box, left in place "
             "(retryable)")
    elif final == "":
        _log("submit-own unconfirmed: box bare, submit not proven")
    else:
        _log("submit-own unconfirmed: box changed, left in place (retryable)")
    return False


def submit_own_goal_verified(pane_id, text, run=None, sleep_fn=None, logs=None):
    """#566 -- SUBMIT an EXISTING, COMPLETE, own `/goal <...>` payload already
    sitting swallowed-unsubmitted in the input box, PANE-verified, WITHOUT
    re-typing or backspacing it. The `/goal`-specific sibling of
    `submit_own_draft_verified` (#501): that primitive REFUSES the human-typeable
    `/goal ` prefix because CONTENT is not proof of ownership for it -- here the
    CALLER (the #566 goal recovery in `deliver_goal`) has ALREADY proven
    ownership (the #372 janitor watch/park provenance + the recent-human gate)
    AND passes the EXACT expected payload, so ownership is proven by an EXACT
    head+tail match against `text` rather than an unambiguous-nudge prefix.

    COMPLETENESS is mandatory before ANY keystroke (HARD CONSTRAINT: a truncated
    slash command is NEVER submitted -- the #36 disaster): the box HEAD row must
    be a leading substring of `text` AND the box TAIL row must be a trailing
    substring of `text` AND `text` must start with `/goal `. Both ends matching
    the literal expected payload prove the box holds the WHOLE rendered
    `/goal <...>`; a partial/truncated type (tail does not match `text`'s tail)
    is refused. Recognition reads the HEAD row (`_input_box_head_text`) + the
    TAIL row (`_input_line_text`), never guessing. A collapsed-paste placeholder
    (`[Pasted text #N]`) never `startswith("/goal ")`, so it is refused too.

    Confirmation is PANE-based, NOT transcript-based (#566-review F1 -- the
    #501 sibling's transcript token works for a PLAIN-TEXT nudge, but a SLASH
    COMMAND like `/goal` is written to the transcript as a `<command-name>
    /goal</command-name> ... <command-args>...` COMPOSITE, so the raw `/goal
    <...>` text is NEVER a contiguous substring of the accepted `user` turn and a
    transcript match can never succeed -- `watchdog/decide.py`'s own #336-F1
    finding). So this mirrors the PROVEN production `/goal` typing path
    `goal._send_goal_verified` exactly: press Enter, and the submit is confirmed
    the instant the box no longer holds our `/goal` (`_await_typed_landed(...,
    want=False)`); a swallowed Enter (#36 agent-strip) earns ONE corrective
    Escape+Enter (never a second Escape #35), re-verified. On a genuinely
    unconfirmed submit the box is left EXACTLY as-is (never backspace our own
    complete payload; it is a legit pending arm), logged honestly, return False;
    the caller's escalation fires."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    if not text or not text.startswith("/goal "):
        _log("submit-own-goal abort: payload is not a /goal command")
        return False

    def _complete_own_goal(cap):
        # The box holds the COMPLETE literal `/goal <text>`: head row is its
        # leading substring AND tail row is its trailing substring. A truncated
        # type matches the head but NOT the tail -> refused.
        head = watchdog._input_box_head_text(cap)
        tail = watchdog._input_line_text(cap)
        return (bool(head) and head.startswith("/goal ")
                and text.startswith(head)
                and bool(tail) and text.endswith(tail))

    cap = watchdog.capture_pane(pane_id, run, lines=40)
    if not _complete_own_goal(cap):
        _log("submit-own-goal abort: box no longer holds the complete own /goal")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("submit-own-goal abort: live turn")
        return False
    # A SELECTED agent-strip row (#36) steals the Enter -- ONE Escape returns
    # focus (the draft survives ONE Escape; two would delete it, #35), then
    # re-confirm the complete own /goal is still there before submitting.
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
        cap = watchdog.capture_pane(pane_id, run, lines=40)
        if not _complete_own_goal(cap):
            _log("submit-own-goal abort: own /goal gone after strip Escape")
            return False
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])
    # PANE proof: the box no longer holds our `/goal` (`want=False`) => submitted.
    if not _await_typed_landed(pane_id, text, run, sleep_fn, want=False):
        _log("submit-own-goal delivered")
        return True
    # STILL in the box -- a swallowed Enter (#36). ONE corrective Escape+Enter.
    run(["tmux", "send-keys", "-t", pane_id, "Escape"])
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])
    if not _await_typed_landed(pane_id, text, run, sleep_fn, want=False):
        _log("submit-own-goal delivered (after corrective Escape+Enter)")
        return True
    # Genuinely unconfirmed -- our own complete /goal is still in the box. Leave
    # it EXACTLY as-is (never backspace our own payload; it is a legit pending
    # arm), logged honestly (#134/#360); the caller's escalation fires.
    _log("submit-own-goal unconfirmed: own /goal still in box, left in place "
         "(retryable)")
    return False
