"""Cross-stream backstops — the gatekeeper<->sub-dev protocol's machine-local
safety nets: job 8 (`bounce_backstop`, gatekeeper-returned `prio:bounce` work
must never rot), job 11 (`gk_request_backstop`, the mirror — a sub-dev stream's
`needs-gatekeeper`/`GATEKEEPER-ACTION:` request reaches the supervisor without
the user as middleman), and the shared per-cwd backlog-cache read
(`_cached_backlog_open`/`_cached_backlog_count`) jobs 10/20 consult.

Extracted verbatim from `watchdog/__init__.py` by #433 cluster D (modular split
of the 9738-line monolith) — the fourteen functions the ticket names:
`_repo_in_cross_stream_flow`, `_bounce_quals`, `_gh_env`, `_fetch_bounce_tickets`,
`_cache_repo_roots`, `_try_stash_nudge`, `_safe_to_bounce_nudge`, `bounce_backstop`,
`_gkreq_reping_due`, `_gkreq_supervisor_root`, `_fetch_gkreq_tickets`,
`gk_request_backstop`, `_cached_backlog_open`, `_cached_backlog_count`.
`watchdog/__init__.py` facade-re-exports all fourteen, so every existing caller
(`run_once`'s job-8/11 dispatch, airuleset.py's deferred `from watchdog import
_fetch_bounce_tickets`/`_fetch_gkreq_tickets`, and `__init__.py`'s own bare
`_gh_env`/`_try_stash_nudge`/`_safe_to_bounce_nudge`/`_cached_backlog_open` sites)
reaches them unchanged.

Like `watchdog/janitor.py` (#433 cluster C), this is a leaf with a
back-dependency into `__init__.py`: the cluster-private constants
(`BOUNCE_INTERVAL`/`BOUNCE_RENUDGE_SECONDS`/`BOUNCE_NUDGE`/`_REDUCED_STREAM_USERS`/
`_CROSS_STREAM_REPOS`/`AUTOPILOT_SKIP_EXCL`/`_FOREIGN_TMUX_USERS`/`GKREQ_INTERVAL`/
`GKREQ_CACHE_MAX_AGE_S`/`GKREQ_REPING_SCHEDULE_S`/`GKREQ_NUDGE`/
`_STALE_HANDOFF_EXCLUDE_LABELS`/`BACKLOG_CHECK_INTERVAL_S`/
`BACKLOG_CHECK_FAILURE_TTL_S`), the three private helpers still resident
(`_parse_gh_ts`/`_normalize_gkreq`/`_stale_handoff_alarm`), and the shared
pane/transcript primitives (`capture_pane`/`deliver_with_stash`/
`find_active_transcript`/`list_claude_panes`/`pane_at_idle_prompt`/`pane_in_mode`/
`send_continue`/`transcript_last_marker`/`PROJECTS_DIR`) all stay in `__init__.py`
and are reached via this file's own top-level `import watchdog` (never
`from watchdog import <name>`) + call-time `watchdog.<name>` access — the
established `watchdog/goal.py` + `watchdog/compact.py` + `watchdog/janitor.py`
idiom, circular-import-safe because `import watchdog` binds the (possibly
still-initializing) package object WITHOUT dereferencing any attribute at load
time, and every `watchdog.<name>` read happens later, at call time.

Byte-verbatim from the base block EXCEPT two declared deviations, both
reverse-diff-proven (strip the prefixes + revert the sentinels -> MD5-identical
to the base):
  * 36 `watchdog.` prefixes on the resident references above.
  * 5 `None`-sentinel conversions of default args that referenced a resident
    constant (`bounce_backstop` interval/renudge, `gk_request_backstop`
    interval/schedule, `_gkreq_reping_due` schedule). A default expression
    evaluates at def-time = THIS module's load (mid-`__init__` facade import),
    when `watchdog.<CONST>` cannot be safely dereferenced; the sentinel
    (`param=None` -> `param = watchdog.CONST if param is None else param`, the
    exact idiom `watchdog/janitor.py`'s `_janitor_watch_seen` uses) defers the
    read to call time. Behavior-identical: run_once relies on these defaults and
    None reproduces the constant exactly.

Patch-seam note (#1510): a test that patches a RESIDENT symbol by attribute
(`patch.object(watchdog, "capture_pane")`) STILL reaches these functions — they
read `watchdog.__dict__[name]` at call time, the exact dict the patch writes. The
moved functions DO call one another by bare (leaf-global) name (e.g.
`bounce_backstop` -> `_bounce_quals`/`_fetch_bounce_tickets`/`_try_stash_nudge`),
so a hypothetical `patch.object(wd, "<moved helper>")` would NOT intercept that
internal call — it writes `watchdog.__dict__`, not `cross_stream.__dict__` (the
genuine K-class break shape). What makes the seam safe is that NO test patches a
moved helper by attribute: the whole bounce/gk-request suite drives these
functions through their DI parameters (`gh_fetch=`/`cross_stream_repos=`/`run=`/
`send_fn=`/`user=`) plus stdlib-level patching (`getpass`/`subprocess`), never
`patch.object(wd, "<moved name>")`. The one moved-name patch that DOES exist
(`patch.object(wd, "bounce_backstop")`) targets run_once's OWN bare call in
`__init__.py`, resolved through `watchdog.__dict__` (the facade) — safe.
"""
import json
import os
import re
import time
from pathlib import Path

import watchdog


def _repo_in_cross_stream_flow(root, cross_stream_repos=None):
    """Does the repo at `root` actually participate in the gatekeeper<->
    sub-dev cross-stream flow? `cross_stream_repos=None` resolves to the
    real registry above (the DI convention every other bounce_backstop
    input already follows: `gh_fetch=None` -> the real fetcher,
    `projects_dir=None` -> the real PROJECTS_DIR) -- pass an explicit set
    only to override it (e.g. in a test)."""
    repos = watchdog._CROSS_STREAM_REPOS if cross_stream_repos is None else cross_stream_repos
    name = os.path.basename(str(root or "").rstrip("/"))
    return name in repos


def _bounce_quals(cwd):
    """gh search quals scoping the bounce query to the PANE's stream, derived
    from its /home/<user>/ prefix — historically because montalu's claude ran
    under newlevel's tmux (until the 2026-07-24 subdev migration), making the
    WATCHDOG user meaningless there; the prefix derivation stays regardless,
    since gh identity is the same account everywhere, so @me cannot scope.
    Reduced streams → their stream label (the
    #1599 convention: findings tickets carry stream:<name>); a full-authority
    box takes the CORE slice — sub-dev streams EXCLUDED (live dry-run finding
    2026-07-19: an unscoped dev1 query picked up david's bounces and would
    have pinged the wrong person; the sub-dev's own box nudges those). The
    GATEKEEPER is skipped ENTIRELY ([] = no query, no nudge): the bounce lane's
    direction is reviewer→sub-dev — nudging the reviewer about bounces IT filed
    is backwards (the live gatekeeper-pane spam incident, 2026-07-19)."""
    c = str(cwd or "")
    if c.startswith("/home/gatekeeper/"):
        return []
    for u in watchdog._REDUCED_STREAM_USERS:
        if c.startswith("/home/%s/" % u):
            return ["label:stream:%s" % u]
    return [" ".join("-label:stream:%s" % u for u in watchdog._REDUCED_STREAM_USERS)]


def _gh_env(home=None, base=None):
    """Env for gh subprocesses. david's box keeps GH_TOKEN only as an `export`
    in ~/.bashrc (no hosts.yml), which a systemd --user service never sources —
    parse that one line as a fallback. An already-set token is never touched;
    the value is never logged."""
    env = dict(os.environ if base is None else base)
    if env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"):
        return env
    homedir = home or os.path.expanduser("~")
    try:
        rc = Path(os.path.join(homedir, ".bashrc")
                  ).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return env                      # no .bashrc → gh runs with what it has
    m = re.search(r'^\s*export\s+(GH_TOKEN|GITHUB_TOKEN)=["\x27]?(.+)$',
                  rc, re.M)
    if not m:
        return env
    key, val = m.group(1), m.group(2).strip().strip('"\x27')
    # `export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)` — david's real
    # form (2026-07-20 401 root cause: the literal regex captured '$(cat').
    # Resolve the one safe substitution shape by reading the file ourselves;
    # any OTHER substitution is unresolvable → leave the env untouched
    # (a garbage literal would turn every gh call into a silent 401).
    cat = re.match(r"\$\(\s*cat\s+([^\s)]+)", val)
    if cat:
        p = cat.group(1)
        p = os.path.join(homedir, p[2:]) if p.startswith("~/") else p
        try:
            val = Path(p).read_text(encoding="utf-8").strip()
        except OSError:
            return env
    elif val.startswith("$("):
        return env
    else:
        val = val.split()[0] if val.split() else ""
    if val:
        env[key] = val
    return env


def _fetch_bounce_tickets(root, home=None):
    """Open prio:bounce ticket numbers for the repo at `root`, scoped to the
    root's stream. None on any error (fail-safe — an auth/network hiccup must
    never look like 'no bounces')."""
    import subprocess
    nums, env = set(), _gh_env(home)
    for qual in _bounce_quals(root):
        try:
            r = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--label",
                 "prio:bounce", "--search",
                 (watchdog.AUTOPILOT_SKIP_EXCL + " " + qual).strip(), "-L", "100",
                 "--json", "number"],
                cwd=root, env=env, capture_output=True, text=True, timeout=8)
            if r.returncode != 0:
                return None
            nums.update(x["number"] for x in json.loads(r.stdout))
        except Exception:
            return None
    return sorted(nums)


def _cache_repo_roots(home=None, max_age_s=None):
    """{root: name} from the tickets-status cache — the repos this box recently
    worked (the Discord-fallback candidate set for panes that no longer exist).
    `max_age_s` keeps only entries whose cache ts is that fresh — job 11's
    no-pane ping fired for a 16-DAY-stale checkout supervised from another box
    (live false ping 2026-07-24); 'a session was here recently and is now
    gone' is the only state that justifies a session-missing ping."""
    import statusbar
    import time as _time
    roots = {}
    try:
        files = list(statusbar.cache_dir(home).glob("*.json"))
    except OSError:
        return roots                    # unreadable cache dir → empty candidate set
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue                    # one corrupt cache entry never kills the sweep
        root, name = str(d.get("root") or ""), str(d.get("name") or "")
        if not (root and name):
            continue
        if max_age_s is not None:
            try:
                if (_time.time() - float(d.get("ts") or 0)) > max_age_s:
                    continue            # stale root — no session here lately
            except (TypeError, ValueError):
                continue
        roots[root] = name
    return roots


def _try_stash_nudge(pid, captured, text, run, dry_run, logs=None):
    """Shared bounce/gk-request helper (issue #35): attempt a stash-around
    delivery of `text` for a pane that already passed the live-work / armed
    -loop / already-nudged guards but isn't bare-idle — i.e. it holds a
    draft, not a running turn. dry_run never attempts it (keeps the
    diagnostic simulation identical to the pre-#35 behavior).

    `logs`, if a list, collects `deliver_with_stash`'s own reason strings.
    Both callers used to pass none at all, so every internal reason went
    nowhere and a failed delivery was indistinguishable from a supervisor
    who simply chose not to act — 48h of gk-request nudges left no trace of
    any kind (#193). A delivery that cannot run now always says why.

    A dry run records nothing, and its callers log nothing either — a
    SIMULATED skip is not a failed delivery, and reporting it as one would
    make `--dry-run` accuse a repo whose real sweep would have succeeded."""
    if dry_run:
        return False
    return watchdog.deliver_with_stash(pid, text, run, captured=captured, logs=logs)


def _send_bare_nudge_verified(state, pid, root, text, run, now, projects_dir,
                              sleep_fn, logs):
    """#497 — the shared BARE-box transcript-proof send for bounce/gkreq (the
    sibling of `_try_stash_nudge`, which is the DRAFT-box branch's shared
    helper). Resolves the pane's own transcript (`find_active_transcript(root)`
    — the SAME resolver `_safe_to_bounce_nudge` gates on), marks #372 janitor
    provenance BEFORE the keystroke so a stuck residue is reclaimable, then
    `send_verified`. Returns True on a transcript-VERIFIED submit (janitor
    provenance cleared); False on an unverified/swallowed one — the janitor
    mark is LEFT as the residue backstop and the caller undoes its own
    pre-send dedup so the swallowed nudge retries next sweep. Bare-box only:
    the DRAFT branch is `_try_stash_nudge` above.

    Residual (round-2 review F4): the verify transcript is resolved by CWD
    (`find_active_transcript(root)`), while the keystroke goes to a specific
    pane pid; if TWO live claude sessions share the identical cwd, the resolver
    can pick the OTHER session's newer transcript, so a genuinely-landed nudge
    reads as unverified → a spurious retry (and, after `_VERIFY_FAIL_GIVEUP`, a
    spurious give-up ping). This never produces a false 'delivered' (the exact
    nudge text is typed ONLY into the target pane, so `_submit_confirmed` can
    never false-CONFIRM off a sibling), and two sessions in one checkout is
    unusual; the cwd-keyed resolver shape predates #497."""
    tinfo = watchdog.find_active_transcript(projects_dir, root)
    tpath = tinfo[0] if tinfo else None
    watchdog._janitor_mark_watch(state, pid, now)
    if watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn, logs=logs):
        watchdog._janitor_clear_watch(state, pid)
        return True
    return False


# #497 (round-1 adversarial review F3a / playbook #442-F2) — the transcript-
# proof send RETRIES on an unverified submit (`seen.pop`), so a pane that never
# ACCEPTS the nudge (transcript unresolvable, or a persistently swallowed Enter)
# would otherwise re-log `…-nudge-failed` at the ~30-min cadence forever with no
# escalation — the bounce/gkreq work rotting with only a journal line. After
# this many CONSECUTIVE unverified submits for one target, fire a ONE-shot
# give-up ping so the user is told the session isn't accepting the nudge (the
# same direction as the no-pane fallback, one pane-state over). The counter
# lives in the store dict (`state['bounce']`/`['gkreq']`), OUTSIDE the popped
# `seen` entry, so it survives the retry pop; it is cleared on a verified send.
_VERIFY_FAIL_GIVEUP = 3


def _note_verify_fail(store, name):
    """Bump `name`'s consecutive-unverified-submit counter; return the new
    count."""
    vf = store.setdefault("vfail", {})
    vf[name] = int(vf.get(name, 0)) + 1
    return vf[name]


def _clear_verify_fail(store, name):
    """A verified send (of any kind — bare OR stash) ends the episode: forget
    the failure streak AND the give-up-pinged flag, so a fresh episode can
    re-escalate."""
    for k in ("vfail", "vpinged"):
        m = store.get(k)
        if isinstance(m, dict):
            m.pop(name, None)


def _handle_unverified_nudge(store, name, tick_str, kind, send_fn, persist,
                             dry_run, now, logs, giveup_body):
    """#497 — the ONE place a bare-box nudge's UNVERIFIED submit is handled
    (shared by bounce + gkreq). Bumps the consecutive-fail streak (persisted
    BEFORE any ping, the #193 order), logs it, and once the streak reaches
    `_VERIFY_FAIL_GIVEUP` fires the give-up ping so a pane that never ACCEPTS
    the nudge escalates to the user instead of rotting on a journal line
    (round-1 review F3a / playbook #442-F2). The caller has already `seen.pop`ed
    the dedup so the swallowed nudge retries.

    ONE ping per episode, gated on a `vpinged` flag set ONLY after a delivered
    send (result != "error") — so a transient notify failure retries next sweep
    instead of losing the escalation (round-2 review F2), and a still-failing
    streak never re-pings until a verified send resets both via
    `_clear_verify_fail`. The dedup key carries `int(now)` so it is FRESH per
    episode: a content-stable key would be swallowed by notify's own 14-day
    dedup TTL, silently killing a legitimate SECOND-episode escalation
    (round-2 review F1 / the #360/#459 dedup-TTL class)."""
    nfail = _note_verify_fail(store, name)
    persist()
    logs.append("%s-nudge-failed %s %s (submit-unverified %d/%d)"
                % (kind, name, tick_str, nfail, _VERIFY_FAIL_GIVEUP))
    pinged = (store.get("vpinged") or {}).get(name)
    if nfail >= _VERIFY_FAIL_GIVEUP and not pinged and not dry_run:
        from notify import stream_qualified
        result = send_fn(
            giveup_body, dedup_key="%s-verify-fail:%s:%d" % (kind, name, int(now)),
            dry_run=dry_run, project=stream_qualified(name))
        logs.append("%s-verify-giveup %s (send=%r)" % (kind, name, result))
        if result != "error":            # delivered (or unconfigured) → once per episode
            store.setdefault("vpinged", {})[name] = True
            persist()


def _safe_to_bounce_nudge(captured, cwd, projects_dir):
    """Is the pane a session at TRUE REST — safe to type the bounce nudge?

    The live incident (2026-07-19, gatekeeper pane): CC renders a free `❯`
    prompt while WAITING on a background Workflow, so a bare idle-prompt check
    pasted the nudge 4× into a mid-review session — the user's hardest rule
    violated ('nesmie sa pastovat do promptu pocas behu'). Refuse when the
    pane shows live-work signals (an active spinner's `esc to interrupt`, a
    background `Waiting for`, a `⏳ WORKING` tail), an ARMED WORKING /goal
    (the statusline's `◎ /goal` — the label alone queues bounce tickets
    there), or a previous nudge still on screen (belt against lost dedup
    state); and when the session transcript is readable, refuse while its
    last marker is ⏳ (mid-flight even if the prompt looks free) or ❓
    (waiting on the user's answer — never interject before it).

    DONE-PARKED override (the 2026-07-20 deadlock): a SATISFIED /goal keeps
    `◎ /goal` lit although no turn will ever fire again — david's session sat
    at ✅ DONE while a bounce rotted and the gatekeeper waited on him. A pane
    whose tail shows `✅ DONE` with no live-work signal is AT REST: the
    ◎ /goal indicator alone must not block the nudge there (the ✻/✳ glyphs
    are NOT used as signals — they appear in finished-turn summaries too)."""
    if "bounce-backstop:" in captured:
        return False
    for sig in ("esc to interrupt", "Waiting for", "⏳ WORKING"):
        if sig in captured:
            return False
    if "◎ /goal" in captured and "✅ DONE" not in captured:
        return False
    tinfo = watchdog.find_active_transcript(Path(projects_dir), cwd)
    if tinfo:
        m = watchdog.transcript_last_marker(tinfo[0]) or ""
        if "⏳" in m or "❓" in m:
            return False
    return True


def bounce_backstop(now, run, state, send_fn, home=None, dry_run=False,
                    gh_fetch=None, interval=None,
                    renudge=None, persist=None,
                    projects_dir=None, user=None, cross_stream_repos=None,
                    time_fn=None, sweep_deadline=None, sleep_fn=None):
    """Job 8 — see the section comment. Mutates state['bounce']; `persist` (the
    caller's save-state closure) is invoked BEFORE any keystroke/ping leaves
    the process — the live incident: TimeoutStartSec killed the run after the
    nudge but before run_once's save, so dedup had no memory and the same
    nudge repeated every sweep. Returns log lines. Best-effort (never raises).

    #255 Fix 1: `time_fn`/`sweep_deadline` (both optional, default None ->
    unbounded, exactly today's behavior) give this job's own per-TARGET loop
    the SAME wall-clock self-bound `run_once`'s per-transcript pane loop
    already has via #172 -- this loop previously had none at all, unlike
    that one. The check sits strictly BETWEEN targets, checked BEFORE a
    target's delivery starts, never nested inside one target's own
    `send_continue`/`_try_stash_nudge` call (each a single atomic type+
    submit pair) -- a target already being delivered always finishes; only
    a NOT-YET-STARTED target is deferred to the next sweep. A deferred
    target's dedup memory (`seen`) is never written, so it is retried next
    sweep rather than silently dropped."""
    interval = watchdog.BOUNCE_INTERVAL if interval is None else interval
    renudge = watchdog.BOUNCE_RENUDGE_SECONDS if renudge is None else renudge
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in watchdog._FOREIGN_TMUX_USERS:
        return []                          # pane lives in another user's tmux
    b = state.get("bounce") or {}
    if (now - b.get("last_check", 0)) < interval:
        return []
    b["last_check"] = int(now)
    seen = dict(b.get("seen") or {})
    b["seen"] = seen
    state["bounce"] = b
    fetch = gh_fetch or (lambda root: _fetch_bounce_tickets(root, home))
    persist = persist or (lambda: None)
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    persist()                                  # cadence stamp survives a kill
    logs = []

    panes = watchdog.list_claude_panes(run, logs=logs, dry_run=dry_run)
    # candidate repos: every live pane cwd (nudge path) + cached roots (Discord
    # fallback for repos whose session is gone)
    targets = {}                               # root -> (name, pane_id | None)
    for pid, cwd in panes:
        targets[cwd] = (os.path.basename(cwd.rstrip("/")), pid)
    pane_cwds = [c for _p, c in panes]

    def _covered_by_pane(root):
        """A cached root is covered when a live pane sits INSIDE it — or when
        the root is a WORKTREE under the repo a pane sits in (the false
        'nebeží žiadna session' ping, 2026-07-23: David's claude ran in the
        MAIN checkout while the cached bounce root was the repo's
        .claude/worktrees/<agent> path; bounce tickets are per-REPO, so a
        session anywhere in the repo tree handles them)."""
        for c in pane_cwds:
            if c == root or c.startswith(root + "/"):
                return True
            if root.startswith(c + "/.claude/worktrees/"):
                return True
        return False

    for root, name in _cache_repo_roots(home).items():
        if not _covered_by_pane(root):
            targets.setdefault(root, (name, None))

    for idx, (root, (name, pid)) in enumerate(sorted(targets.items())):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            # #255 Fix 1: never START a new target's delivery once the
            # shared sweep budget is exhausted. Nothing has been fetched or
            # typed for THIS (or any later) target yet, so deferring here
            # loses nothing -- it is retried on the next sweep exactly like
            # an untouched target always would be.
            logs.append("bounce-budget-exceeded — %d/%d targets handled "
                        "this tick, rest retried next" % (idx, len(targets)))
            break
        if not _bounce_quals(root):
            continue                           # gatekeeper: never bounce-nudged
        if not _repo_in_cross_stream_flow(root, cross_stream_repos):
            # #89: prio:bounce has no protocol meaning outside a repo that
            # actually participates in the gatekeeper<->sub-dev flow — never
            # even ask GitHub, let alone nudge (the restreamer #337 false
            # nudge: a bare label used as a generic priority marker).
            logs.append("bounce-skip-not-cross-stream %s" % name)
            continue
        tickets = fetch(root)
        if tickets is None:
            continue                           # gh error → keep prior state
        if not tickets:
            seen.pop(name, None)               # clean → forget the set
            continue
        prev = seen.get(name) or {}
        same = prev.get("tickets") == tickets
        fresh = (now - prev.get("ts", 0)) < renudge
        if same and fresh:
            continue                           # already nudged/pinged this set
        tick_str = " ".join("#%d" % n for n in tickets)
        if pid:
            captured = watchdog.capture_pane(pid, run)
            if watchdog.pane_in_mode(pid, run) \
                    or not _safe_to_bounce_nudge(captured, root, projects_dir):
                # working / armed-loop / already-nudged pane gets NOTHING —
                # the label alone is the insertion (never interrupt mid-work).
                continue
            if not watchdog.pane_at_idle_prompt(captured):
                # not bare-idle but past every live-work guard above — an
                # idle-with-a-FOREIGN-draft pane, not a running turn. Stash
                # it, deliver the nudge, let CC restore it (issue #35). A
                # verify failure still skips, but never silently (#193).
                why = []
                ok = _try_stash_nudge(pid, captured, watchdog.BOUNCE_NUDGE % (tick_str, name),
                                      run, dry_run, logs=why)
                # #271 (adversarial-review MAJOR finding): `why` also carries
                # `deliver_with_stash`'s own rescue-persist line — promote it
                # to the main journal on EITHER outcome, not just failure, or
                # a successful rescue here is as silent as the incident this
                # mechanism exists to prevent.
                logs.extend(ln for ln in why if "draft-rescue" in ln)
                if not ok:
                    if not dry_run:
                        logs.append("bounce-nudge-failed %s %s (%s)"
                                    % (name, tick_str, "; ".join(why) or "no reason"))
                    continue
                _clear_verify_fail(b, name)   # a delivered nudge ends the streak
                seen[name] = {"tickets": tickets, "ts": int(now)}
                persist()
                logs.append("bounce-nudge %s %s" % (name, tick_str))
                continue
            # #193 — persist the dedup BEFORE the keystroke so a systemd
            # TimeoutStartSec kill after a LANDED nudge but before run_once's
            # save_state cannot lose it (the 4x re-nudge incident).
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()
            if dry_run:
                logs.append("bounce-nudge %s %s (dry-run)" % (name, tick_str))
                continue
            # #497 — transcript-proof send. On an UNVERIFIED submit (swallowed
            # Enter) UNDO the pre-send dedup so the swallowed nudge retries next
            # sweep instead of dedup-ing itself out; a LANDED nudge keeps its
            # #193 dedup.
            if not _send_bare_nudge_verified(
                    state, pid, root, watchdog.BOUNCE_NUDGE % (tick_str, name),
                    run, now, projects_dir, sleep_fn, logs):
                seen.pop(name, None)
                _handle_unverified_nudge(
                    b, name, tick_str, "bounce", send_fn, persist, dry_run, now,
                    logs,
                    "⚠️ **%s: bounce nudge sa nedoručuje** (%s)\n> V `%s` beží "
                    "session, ale %dx po sebe sa nudge nepodarilo odoslať (submit "
                    "sa neuchytil). Skontroluj ju — vrátené prio:bounce tikety "
                    "inak ostanú nespracované."
                    % (name, tick_str, root, _VERIFY_FAIL_GIVEUP))
                continue
            _clear_verify_fail(b, name)
            logs.append("bounce-nudge %s %s" % (name, tick_str))
        else:
            body = ("⚠️ **%s: %d vrátené tikety čakajú**\n> Gatekeeper vrátil "
                    "prácu (%s), ale nebeží žiadna Claude session, ktorá by ju "
                    "spracovala. Spusti session v `%s` (autopilot ich zoberie "
                    "cez prio:bounce)." % (name, len(tickets), tick_str, root))
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()                          # dedup memory BEFORE the ping
            # #369: routes to the repo's own project thread — mirrors the
            # SAME stream-qualified label a run-card / idle ping for the
            # SAME repo checkout on this box would carry, so the phone can
            # tell which project (and which stream box) this bounce-backlog
            # ping is about.
            from notify import stream_qualified
            # #360 — the dedup key must be fresh per DECISION INSTANT, not per
            # content. The old `bounce:%s:%s % (name, tick_str)` embedded only
            # the ticket-set text, so notify's OWN independent 14-day marker TTL
            # (`notify._DEDUP_TTL_S`) silently swallowed every 6h re-ping of an
            # unchanged set (the `same and fresh` window above never actually
            # reached Discord past the first send). This function's own `same
            # and fresh` renudge window is now the SOLE authority on whether a
            # send is due, so the key only needs to be unique per decision —
            # `int(now)` is unique across any two real decisions (sweeps are
            # >= BOUNCE_INTERVAL apart, re-pings >= BOUNCE_RENUDGE_SECONDS apart)
            # and stays short regardless of backlog size. Exactly the shape
            # gk_request_backstop already uses (#353, see its identical fix +
            # rationale above at the `gkreq:%s:%d` send).
            result = send_fn(body, dedup_key="bounce:%s:%d" % (name, int(now)),
                             dry_run=dry_run, project=stream_qualified(name))
            logs.append("bounce-ping %s %s (send=%r)" % (name, tick_str, result))
    return logs


def _gkreq_reping_due(prev, now, schedule=None):
    """#353 — pure decision for ONE gk-request label (repo name): given the
    caller has ALREADY established the observation is NOT materially
    different from the one `prev` was recorded for (same ticket set, no
    supervisor-pane appear-then-disappear transition), should THIS sweep
    re-ping/re-nudge, or stay silent? Returns (due: bool, count: int) — the
    caller persists `count` under whatever key it uses (never mutates here;
    this function is stateless/pure, like `decide`/`decide_working`).

    Mirrors #352's staged-schedule PATTERN for job-4's `decide_working`
    (an explicit tuple of widening intervals, `min(count - 1,
    len(schedule) - 1)` indexing, holding at the final/cap stage forever)
    — deliberately NOT the same function call: `decide_working`'s own
    vocabulary (`responded`/`nudges`/`noresp`, keyed on whether a self-check
    nudge got a transcript-level ANSWER) is specific to a stalled-*turn*
    detector; a Discord ping to an absent supervisor has no "answer" to
    detect, so the semantics don't transfer. This ticket's own lane also
    forbids touching job-4/stuck-check code at all, so extracting a
    genuinely shared helper out of `decide_working` was out of scope
    regardless — this is an independent function reusing the same SHAPE,
    with zero change to job-4's file section or behavior.

    A caller with no prior record for this label (`prev` empty, or a
    material-change reset already decided) must pass `prev={}` so the first
    call always returns `(True, 1)` — ping now, count starts at 1."""
    schedule = watchdog.GKREQ_REPING_SCHEDULE_S if schedule is None else schedule
    count = int(prev.get("reping_count") or 0)
    last_ts = prev.get("ts")
    if not count or last_ts is None:
        return True, 1                      # first sighting of this state
    step = min(count - 1, len(schedule) - 1)
    if (now - last_ts) < schedule[step]:
        return False, count                 # too soon — stay silent
    return True, count + 1                  # schedule cleared — ping, escalate


def _gkreq_supervisor_root(cwd):
    """Only a FULL-authority session works gk-requests. A root under a reduced
    stream's HOME is skipped — nudging the REQUESTER about its own request is
    backwards (the inverse of `_bounce_quals`' gatekeeper skip)."""
    c = str(cwd or "")
    return not any(c.startswith("/home/%s/" % u)
                   for u in watchdog._REDUCED_STREAM_USERS)


def _fetch_gkreq_tickets(root, home=None):
    """Open stream→supervisor request tickets + hand-off staleness data for
    the repo at `root`: `{"tickets": [...], "handoffs": {num: updated_epoch}}`.

    `tickets` keeps the pre-#399 population EXACTLY: the `needs-gatekeeper`
    label query UNION the no-label-permission fallback (`GATEKEEPER-ACTION:`
    in the title — `airuleset.py gk-request`'s degradation for read-only-fork
    streams). Job 11's immediate nudge/ping flow consumes only this half.

    `handoffs` (#399) maps every open hand-off-shaped ticket — the two
    request queries PLUS `ready-for-review` — to its `updatedAt` epoch,
    minus rows carrying a `_STALE_HANDOFF_EXCLUDE_LABELS` label (checked
    client-side on the fetched `labels` field) and minus rows whose
    timestamp does not parse (unmeasurable must never alarm). A
    `ready-for-review` row deliberately NEVER joins `tickets`: a fresh
    hand-off is normal flow the gatekeeper's /process-subdev loop consumes,
    and pinging it immediately would be the banned per-phase spam shape.

    None on any error (fail-safe — an auth/network hiccup must never look
    like 'no requests')."""
    import subprocess
    nums, handoffs, env = set(), {}, _gh_env(home)
    # NB: GitHub search TOKENIZES — the in:title query ALSO returns titles
    # merely containing the words gatekeeper+action ("… gatekeeper GitHub
    # Actions runner", the live #1768 false ping, 2026-07-24) — so the
    # fallback fetches titles and keeps only the LITERAL marker client-side.
    queries = (
        (["gh", "issue", "list", "--state", "open", "--label",
          "needs-gatekeeper", "--search", watchdog.AUTOPILOT_SKIP_EXCL,
          "-L", "100", "--json", "number,updatedAt,labels"], None, True),
        (["gh", "issue", "list", "--state", "open", "--search",
          '"GATEKEEPER-ACTION:" in:title ' + watchdog.AUTOPILOT_SKIP_EXCL,
          "-L", "100", "--json", "number,title,updatedAt,labels"],
         lambda x: str(x.get("title", "")).startswith("GATEKEEPER-ACTION:"),
         True),
        # (#399) ready-for-review hand-offs feed ONLY the stale-alarm map.
        (["gh", "issue", "list", "--state", "open", "--label",
          "ready-for-review", "--search", watchdog.AUTOPILOT_SKIP_EXCL,
          "-L", "100", "--json", "number,updatedAt,labels"], None, False),
    )
    for argv, keep, is_request in queries:
        try:
            r = subprocess.run(argv, cwd=root, env=env, capture_output=True,
                               text=True, timeout=8)
            if r.returncode != 0:
                return None
            for x in json.loads(r.stdout):
                if keep is not None and not keep(x):
                    continue
                n = x["number"]
                if is_request:
                    nums.add(n)
                raw = x.get("labels")
                labels = ({str(lb.get("name", "")) for lb in raw
                           if isinstance(lb, dict)}
                          if isinstance(raw, list) else set())
                if labels & watchdog._STALE_HANDOFF_EXCLUDE_LABELS:
                    continue
                upd = watchdog._parse_gh_ts(x.get("updatedAt"))
                if upd is None:
                    continue           # unmeasurable → never in the stale map
                # keep the OLDEST stamp when a number appears in two queries
                if n not in handoffs or upd < handoffs[n]:
                    handoffs[n] = upd
        except Exception:
            return None
    return {"tickets": sorted(nums), "handoffs": handoffs}


def gk_request_backstop(now, run, state, send_fn, home=None, dry_run=False,
                        gh_fetch=None, interval=None,
                        schedule=None, persist=None,
                        projects_dir=None, user=None, sleep_fn=None):
    """Job 11 — see the section comment. Mutates state['gkreq']; `persist` is
    invoked BEFORE any keystroke/ping leaves the process (the job-8 lesson:
    a TimeoutStartSec kill after the nudge but before save left dedup with no
    memory) AND before any state-only bookkeeping write (#353's `pane_seen`
    tracking) — the persisted `state["gkreq"]["seen"]` dict IS the "marker
    file in ~/.claude" surviving a watchdog restart; no separate file is
    needed since this JSON store already reloads fresh on every process
    start. #399: the same sweep also runs `_stale_handoff_alarm` per target
    (its own `state["gkreq"]["stale_seen"]` dedup namespace) whenever the
    fetch supplies the widened handoffs map — a legacy bare-list fetch
    keeps the pre-#399 behavior byte-for-byte via `_normalize_gkreq`.
    Returns log lines. Best-effort (never raises)."""
    interval = watchdog.GKREQ_INTERVAL if interval is None else interval
    schedule = watchdog.GKREQ_REPING_SCHEDULE_S if schedule is None else schedule
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in watchdog._FOREIGN_TMUX_USERS:
        return []                          # pane lives in another user's tmux
    g = state.get("gkreq") or {}
    if (now - g.get("last_check", 0)) < interval:
        return []
    g["last_check"] = int(now)
    seen = dict(g.get("seen") or {})
    g["seen"] = seen
    state["gkreq"] = g
    fetch = gh_fetch or (lambda root: _fetch_gkreq_tickets(root, home))
    persist = persist or (lambda: None)
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    persist()                                  # cadence stamp survives a kill
    logs = []
    stale_handled = set()                      # (#399) one stale evaluation
                                               # per NAME per sweep

    panes = watchdog.list_claude_panes(run, logs=logs, dry_run=dry_run)
    # #353 round-2 review, MAJOR-3 — computed BEFORE the pane targets so a
    # pane-covered root can reuse the cache's own origin-derived name below.
    cache_roots = _cache_repo_roots(home, max_age_s=watchdog.GKREQ_CACHE_MAX_AGE_S)
    targets = {}                               # root -> (name, pane_id | None)
    for pid, cwd in panes:
        # MAJOR-3 (TRIGGERED, live on dev1: forestshop_app -> forestshop-app,
        # odoo-slovnormal -> odoo-erp -- the incident's OWN repo): a pane
        # target used to key `seen` on the directory BASENAME while a
        # cache-only (no-pane) sweep of the SAME root keys on the cache's
        # origin-derived name. Whenever those differ, the appear-then-
        # disappear reset below is structurally dead code -- the two
        # observation types write to two DIFFERENT `seen[name]` records
        # that never see each other's half of the cycle. Prefer the
        # already-known cache name for this EXACT root (no new subprocess
        # call — `cache_roots` is already being computed for the loop
        # below); fall back to the basename only for a root with no cache
        # entry at all (genuinely never seen before).
        name = cache_roots.get(cwd) or os.path.basename(cwd.rstrip("/"))
        targets[cwd] = (name, pid)
    pane_cwds = [c for _p, c in panes]

    def _covered_by_pane(root):
        for c in pane_cwds:
            if c == root or c.startswith(root + "/"):
                return True
            if root.startswith(c + "/.claude/worktrees/"):
                return True
        return False

    for root, name in cache_roots.items():
        if not _covered_by_pane(root):
            targets.setdefault(root, (name, None))

    for root, (name, pid) in sorted(targets.items()):
        if not _gkreq_supervisor_root(root):
            continue                           # requester homes never nudged
        tickets, handoffs = watchdog._normalize_gkreq(fetch(root))
        # (#399) The stale hand-off alarm MUST run before the
        # needs-gatekeeper flow's own early `continue`s below — `if not
        # tickets: continue` would otherwise skip exactly the main new
        # case: zero open requests, but a stale ready-for-review hand-off
        # rotting in the review queue. ONE evaluation per NAME per sweep
        # (review MINOR-1, the shared per-sweep handled-set shape): two
        # same-name targets (a duplicate checkout — pane target + an
        # uncovered cache root) with divergent fetches would otherwise
        # send twice under the SAME decision-instant dedup key, and
        # notify's own per-key marker would swallow the second, richer
        # alarm until the 24h stage; a genuine divergence resolves on the
        # NEXT sweep with a fresh key instead.
        if handoffs is not None and name not in stale_handled:
            stale_handled.add(name)
            logs += watchdog._stale_handoff_alarm(name, root, handoffs, g, now,
                                         send_fn, dry_run, persist, schedule)
        if tickets is None:
            continue                           # gh error → keep prior state
        if not tickets:
            seen.pop(name, None)               # clean → forget the set
            continue
        prev = seen.get(name) or {}
        # #353 — presence tracking is written EVERY sweep a label has an
        # open backlog, regardless of whether a ping fires this sweep, so a
        # later appear→disappear transition is never missed just because
        # the interim sweep stayed silent under the staged schedule.
        #
        # #353 round-2 review, MAJOR-1 (TRIGGERED live: a single transient
        # `list_claude_panes` read blip — #199's own documented "empty ≠
        # genuine negative" class — made a healthy, mid-work session read
        # as "gone" for exactly one sweep, firing an immediate false
        # "nebeží žiadna supervízorská Claude session" ping: 2 pings in 90
        # minutes where the OLD 6h-window code produced 0). A single absent
        # observation is therefore only PENDING, never confirmed — the
        # material-change reset fires only once the SAME session has been
        # observed absent on TWO CONSECUTIVE sweeps (`pane_absent_pending`),
        # never on the first.
        pane_now = bool(pid)
        had_pane = bool(prev.get("pane_seen"))
        pending_gone = bool(prev.get("pane_absent_pending"))
        confirmed_disappear = False
        if pane_now:
            new_pane_seen, new_pending = True, False
        elif had_pane:
            if pending_gone:
                new_pane_seen, new_pending = False, False
                confirmed_disappear = True
            else:
                new_pane_seen, new_pending = True, True
        else:
            new_pane_seen, new_pending = False, False
        if new_pane_seen != had_pane or new_pending != pending_gone:
            seen[name] = dict(prev, pane_seen=new_pane_seen,
                              pane_absent_pending=new_pending)
            persist()
            prev = seen[name]
        material_change = prev.get("tickets") != tickets or confirmed_disappear
        if material_change:
            due, count = True, 1
        else:
            due, count = _gkreq_reping_due(prev, now, schedule)
        if not due:
            continue                           # staged schedule not cleared yet
        tick_str = " ".join("#%d" % n for n in tickets)
        if pid:
            captured = watchdog.capture_pane(pid, run)
            if watchdog.pane_in_mode(pid, run) \
                    or not _safe_to_bounce_nudge(captured, root, projects_dir):
                # working / armed-loop pane gets NOTHING — the label alone is
                # the queue insertion (never interrupt mid-work).
                continue
            if not watchdog.pane_at_idle_prompt(captured):
                # idle-with-a-FOREIGN-draft, not a running turn — stash it,
                # deliver, let CC restore it (issue #35). A verify failure
                # still skips, but never silently (#193): this job threaded no
                # log list at all, which is why 48h of stranded nudges left
                # not one `stash-*` line in the journal.
                why = []
                ok = _try_stash_nudge(pid, captured, watchdog.GKREQ_NUDGE % (tick_str, name),
                                      run, dry_run, logs=why)
                # #271 — see bounce_backstop's identical fix above.
                logs.extend(ln for ln in why if "draft-rescue" in ln)
                if not ok:
                    if not dry_run:
                        logs.append("gkreq-nudge-failed %s %s (%s)"
                                    % (name, tick_str, "; ".join(why) or "no reason"))
                    continue
                _clear_verify_fail(g, name)   # a delivered nudge ends the streak
                seen[name] = {"tickets": tickets, "ts": int(now),
                              "reping_count": count, "pane_seen": new_pane_seen,
                              "pane_absent_pending": new_pending}
                persist()
                logs.append("gkreq-nudge %s %s" % (name, tick_str))
                continue
            # #193 — persist the dedup BEFORE the keystroke (see bounce above).
            seen[name] = {"tickets": tickets, "ts": int(now),
                          "reping_count": count, "pane_seen": new_pane_seen,
                          "pane_absent_pending": new_pending}
            persist()
            if dry_run:
                logs.append("gkreq-nudge %s %s (dry-run)" % (name, tick_str))
                continue
            # #497 — transcript-proof send; UNDO the pre-send dedup on an
            # unverified submit so a swallowed nudge retries next sweep.
            if not _send_bare_nudge_verified(
                    state, pid, root, watchdog.GKREQ_NUDGE % (tick_str, name),
                    run, now, projects_dir, sleep_fn, logs):
                seen.pop(name, None)
                _handle_unverified_nudge(
                    g, name, tick_str, "gkreq", send_fn, persist, dry_run, now,
                    logs,
                    "⚠️ **%s: gk-request nudge sa nedoručuje** (%s)\n> V `%s` beží "
                    "supervízorská session, ale %dx po sebe sa nudge nepodarilo "
                    "odoslať (submit sa neuchytil). Skontroluj ju — needs-gatekeeper "
                    "žiadosti inak ostanú nespracované."
                    % (name, tick_str, root, _VERIFY_FAIL_GIVEUP))
                continue
            _clear_verify_fail(g, name)
            logs.append("gkreq-nudge %s %s" % (name, tick_str))
        else:
            body = ("⚠️ **%s: %d needs-gatekeeper žiadostí čaká**\n> Sub-dev "
                    "stream žiada akciu supervízora (%s), ale nebeží žiadna "
                    "supervízorská Claude session. Spusti session v `%s` — "
                    "master loop si žiadosti zoberie."
                    % (name, len(tickets), tick_str, root))
            seen[name] = {"tickets": tickets, "ts": int(now),
                          "reping_count": count, "pane_seen": new_pane_seen,
                          "pane_absent_pending": new_pending}
            persist()                          # dedup memory BEFORE the ping
            # #353 round-2 review, MAJOR-2/MAJOR-A (both TRIGGERED): the
            # dedup key used to embed ONLY the ticket-set text, so notify's
            # OWN independent 14-day marker TTL (`notify._DEDUP_TTL_S`, a
            # pre-existing, unrelated mechanism) silently swallowed every
            # STAGED reping of an unchanged set (the 24h/3d/7d schedule
            # never actually reached Discord past the first send) AND
            # swallowed a genuine material-change reset whenever the ticket
            # set reverted to one seen within the last 14 days. This
            # function's own `_gkreq_reping_due`/material-change logic is
            # now the SOLE authority on whether a send is due, so the key
            # only needs to be fresh per DECISION INSTANT, not per content —
            # `int(now)` is unique across any two real decisions this
            # function ever makes (sweeps are >= GKREQ_INTERVAL apart) and,
            # unlike the old tick_str-based key, stays short regardless of
            # backlog size (MAJOR-B: a very large ticket list embedded
            # verbatim can exceed the filesystem's NAME_MAX and make
            # notify's own dedup fail OPEN — out of this lane's scope to
            # fix in notify.py itself, but this function no longer builds
            # a key that can trigger it).
            # #369: routes to the repo's own project thread — mirrors the
            # SAME stream-qualified label a run-card / idle ping for the
            # SAME repo checkout on this box would carry.
            from notify import stream_qualified
            result = send_fn(body, dedup_key="gkreq:%s:%d" % (name, int(now)),
                             dry_run=dry_run, project=stream_qualified(name))
            logs.append("gkreq-ping %s %s (send=%r)" % (name, tick_str, result))
    return logs


# --------------------------------------------------------------------------- #
# #516 — GK-SIDE SELF-SERVICE AUTO-BOUNCE (job 31). The mirror-image half of
# the #516 pair: side A (hooks/block-gk-request-without-selfservice.sh) forces
# a sub-dev stream's gk ACTION request to carry a falsifiable
# `Self-service-checked:` line at FILING time; this side is the gatekeeper's
# MECHANICAL backstop — an open needs-gatekeeper ACTION request that carries no
# such line (a request that BYPASSED the filing hook, or predates it) is
# AUTO-BOUNCED back to the owning sub-dev stream instead of the gatekeeper
# working it, so gk is never overloaded by a self-serviceable prod-STATE READ.
#
# Deliberately MECHANICAL and CONSERVATIVE, never a semantic classifier of
# prose (the exact thing #516 bans): it bounces ONLY the falsifiable "no
# Self-service-checked line" case, and only for a request positively
# attributable to a reduced sub-dev stream (a `handed-by:<stream>` label). The
# "line-is-present-but-it-is-a-pure-read" case is NOT mechanized (it would be a
# banned prose classifier) — it stays the gatekeeper's own manual triage step
# (documented in skills/process-subdev). A code-review hand-off
# (`stream:<x>`/`ready-for-review`) is NEVER bounced (rule 8).
# --------------------------------------------------------------------------- #

_SELFSERVICE_LINE_RE = re.compile(r'Self-service-checked:\s*\S', re.IGNORECASE)
_HANDED_BY_RE = re.compile(r'^handed-by:([A-Za-z0-9_-]+)$', re.I)

# #516 adversarial review F3 — per-sweep cap on the per-candidate `gh issue
# view` detail fetches (each needs `comments`, unavailable in the list query),
# so a large open-request backlog can never blow the 120s systemd sweep budget
# (the #172/#504 class). Candidates are processed oldest-first (sorted number
# ascending) and drop out as they bounce, so the backlog drains across sweeps.
_SELFSERVICE_MAX_CANDIDATES = 25

# The Slovak bounce template. `%(stream)s` is the owning reduced stream. Posted
# via subprocess argv (never a shell), so its backticks / newlines are literal.
_SELFSERVICE_BOUNCE_TEMPLATE = (
    "🔄 **Automaticky vrátené (prio:bounce) — chýba `Self-service-checked:` riadok**\n\n"
    "Táto požiadavka na gatekeepera nemá riadok `Self-service-checked:`, takže "
    "nevieme, či ide o prod-STATE READ (skupinové členstvo, počet riadkov, "
    "hodnota configu, obsah odoslaného mailu) — a ten si **rieši stream sám**, "
    "nie gatekeeper:\n\n"
    "1. **Vlastný read-only kanál** na PROD inštancii (`has_group` / `search_read`; "
    "Money RO tunel pre Money). Pri HTTP chybe čítaj TELO chyby a skús užšiu "
    "metódu — nikdy sa nevzdaj po jednom 500.\n"
    "2. **Čerstvá kópia PRODu** na vlastnom boxe — napíš `REFRESH-DEV-BOX-FROM-PROD: "
    "%(stream)s` na sledovací ticket → do ~20–40 min máš aktuálny rsync/pg_dump s "
    "plným psql prístupom (autoritatívne pre STATE otázky).\n\n"
    "Gatekeeper robí **len ŽIVÉ zásahy do PRODu** (reštart zaseknutej fronty, "
    "inštalácia balíka do RUNTIME_DEPS, migrácia…). Ak po self-service naozaj "
    "potrebuješ živý zásah, **znova to zadaj** s riadkom:\n"
    "`Self-service-checked: skúsil som <RO kanál / čerstvá PROD kópia> — <výsledok>; "
    "živý zásah, ktorý potrebujem od gk, je <…>`\n\n"
    "Podrobne: `modules/core/autonomous-verification.md` → „What's on PROD? is a "
    "SELF-SERVICE question\"."
)


def _origin_reduced_stream(labels):
    """The reduced sub-dev stream that FILED this gk request, read from a
    `handed-by:<stream>` label whose <stream> is a known reduced stream user,
    or None. `handed-by:<user>` is the origin-provenance marker cmd_gk_request
    stamps (#191 Part C) — deliberately NOT `stream:<user>` (which is the
    REVIEW-queue ownership primitive, rule 8). None when no such label is
    present (e.g. a read-only fork that could apply no label — that case is a
    documented residual the gatekeeper's manual triage handles, never
    auto-bounced, since its stream is not attributable from labels)."""
    for lb in (labels or []):
        m = _HANDED_BY_RE.match(str(lb))
        if m and m.group(1) in watchdog._REDUCED_STREAM_USERS:
            return m.group(1)
    return None


def _selfservice_bounce_decide(labels, has_line, origin_stream):
    """PURE facts-in / verdict-out decision (#486 direction — no silent
    branches; the caller LOGS the verdict for EVERY candidate). Returns
    (should_bounce: bool, reason: str). Bounce a gk ACTION request (the
    caller's candidate set is already the needs-gatekeeper ∪ GATEKEEPER-ACTION
    set) iff ALL hold:
      - it is NOT already bounced (no `prio:bounce` label),
      - it is NOT a code-review hand-off (no `ready-for-review`, no
        `stream:<x>` label — rule 8),
      - it is positively attributable to a reduced sub-dev stream
        (`origin_stream` is not None),
      - it carries NO `Self-service-checked:` line (the falsifiable, mechanical
        signal — a pure-read request that DOES carry a line is left for the
        gatekeeper's manual triage, never mechanically judged here).
    Every non-bounce returns an explicit reason, so the sweep's journal shows
    exactly why each candidate was or was not bounced."""
    label_set = {str(x) for x in (labels or [])}
    if "prio:bounce" in label_set:
        return False, "already-bounced"
    if "ready-for-review" in label_set:
        return False, "review-handoff:ready-for-review"
    if any(re.match(r'^stream:[A-Za-z0-9_-]+$', x, re.I) for x in label_set):
        return False, "review-handoff:stream-label"
    if origin_stream is None:
        return False, "origin-not-attributable-to-a-reduced-stream"
    if has_line:
        return False, "has-self-service-line"    # gk judges a pure read manually
    return True, "no-self-service-line"


def _fetch_gk_action_requests(root, home=None):
    """Open gk ACTION-request tickets for the repo at `root` — the SAME set job
    11 nudges (needs-gatekeeper label ∪ `GATEKEEPER-ACTION:` title), each with
    the facts `_selfservice_bounce_decide` needs: its labels, whether its body
    OR any comment carries a `Self-service-checked:` line, and the reduced
    stream that filed it (`handed-by:<stream>`). Returns a list of dicts, or
    None on ANY error (fail-safe — an auth/network hiccup must never look like
    'nothing to bounce'). The `GATEKEEPER-ACTION:` title fallback is filtered
    client-side (GitHub search TOKENIZES, so `in:title` also matches titles
    merely CONTAINING the words — the same #1768 guard `_fetch_gkreq_tickets`
    applies)."""
    import subprocess
    env = _gh_env(home)
    nums = set()
    queries = (
        (["gh", "issue", "list", "--state", "open", "--label",
          "needs-gatekeeper", "--search", watchdog.AUTOPILOT_SKIP_EXCL,
          "-L", "100", "--json", "number"], None),
        (["gh", "issue", "list", "--state", "open", "--search",
          '"GATEKEEPER-ACTION:" in:title ' + watchdog.AUTOPILOT_SKIP_EXCL,
          "-L", "100", "--json", "number,title"],
         lambda x: str(x.get("title", "")).startswith("GATEKEEPER-ACTION:")),
    )
    for argv, keep in queries:
        try:
            r = subprocess.run(argv, cwd=root, env=env, capture_output=True,
                               text=True, timeout=8)
            if r.returncode != 0:
                return None
            for x in json.loads(r.stdout):
                if keep is not None and not keep(x):
                    continue
                nums.add(x["number"])
        except Exception:
            return None
    out = []
    for num in sorted(nums)[:_SELFSERVICE_MAX_CANDIDATES]:
        try:
            r = subprocess.run(
                ["gh", "issue", "view", str(num), "--json",
                 "number,labels,body,comments"],
                cwd=root, env=env, capture_output=True, text=True, timeout=8)
            if r.returncode != 0:
                return None
            d = json.loads(r.stdout or "{}")
        except Exception:
            return None
        labels = [str((lb or {}).get("name", ""))
                  for lb in (d.get("labels") or []) if isinstance(lb, dict)]
        texts = [str(d.get("body") or "")]
        texts += [str((c or {}).get("body") or "")
                  for c in (d.get("comments") or []) if isinstance(c, dict)]
        has_line = any(_SELFSERVICE_LINE_RE.search(t) for t in texts)
        out.append({"number": num, "labels": labels, "has_line": has_line,
                    "origin_stream": _origin_reduced_stream(labels)})
    return out


def _apply_selfservice_bounce(root, num, stream, home=None, dry_run=False):
    """Perform the bounce for ONE ticket: post the Slovak template comment, add
    `prio:bounce`, add `stream:<stream>` (routes the ticket back into the owning
    sub-dev's OWN bounce lane — job 8's stream-scoped nudge + its footer/goal
    slice — exactly where a bounced ticket belongs, #307), and remove
    `needs-gatekeeper` (leaves the gatekeeper's workable set). Returns True iff
    the COMMENT (the durable record + guidance) landed; the label edits are
    best-effort (a stale label failure never re-bounces, since the comment
    anchors the dedup). `needs-gatekeeper` is removed LAST so a partial failure
    leaves the ticket discoverable rather than orphaned. Never raises."""
    if dry_run:
        return True
    import subprocess
    env = _gh_env(home)

    def _gh(*args):
        try:
            return subprocess.run(["gh"] + list(args), cwd=root, env=env,
                                  capture_output=True, text=True, timeout=15)
        except Exception:
            return None

    body = _SELFSERVICE_BOUNCE_TEMPLATE % {"stream": stream}
    c = _gh("issue", "comment", str(num), "--body", body)
    if c is None or c.returncode != 0:
        return False
    _gh("issue", "edit", str(num), "--add-label", "prio:bounce")
    _gh("issue", "edit", str(num), "--add-label", "stream:%s" % stream)
    _gh("issue", "edit", str(num), "--remove-label", "needs-gatekeeper")
    return True


def gk_selfservice_bounce(now, run, state, home=None, dry_run=False,
                          gh_fetch=None, bounce_apply=None, interval=None,
                          persist=None, user=None):
    """Job 31 (#516) — see the section comment. Runs ONLY on a supervisor
    (full-authority) box (`_gkreq_supervisor_root`), for cross-stream repos
    (`_repo_in_cross_stream_flow`); a reduced-stream box never bounces (it is
    the REQUESTER). Mutates state['gk_selfservice_bounce']; `persist` is invoked
    BEFORE the GitHub mutation (the job-8/11 kill-safe-dedup lesson: a
    TimeoutStartSec kill after the bounce but before save must not re-bounce).
    Best-effort (never raises). Returns log lines — EVERY candidate's verdict is
    logged (bounce or skip+reason), the #486 explicit-decision-log direction."""
    interval = watchdog.GKREQ_INTERVAL if interval is None else interval
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in watchdog._FOREIGN_TMUX_USERS:
        return []                              # pane lives in another user's tmux
    g = state.get("gk_selfservice_bounce") or {}
    if (now - g.get("last_check", 0)) < interval:
        return []
    g["last_check"] = int(now)
    seen = dict(g.get("seen") or {})
    g["seen"] = seen
    state["gk_selfservice_bounce"] = g
    fetch = gh_fetch or (lambda root: _fetch_gk_action_requests(root, home))
    apply_bounce = bounce_apply or (
        lambda root, num, stream: _apply_selfservice_bounce(
            root, num, stream, home, dry_run))
    persist = persist or (lambda: None)
    persist()                                  # cadence stamp survives a kill
    logs = []

    # Target repos: the SAME derivation job 11 uses (live pane cwds + recently-
    # cached roots), so the bounce only touches repos this box actually
    # supervises. We act on GitHub directly, so a pane id is not needed.
    panes = watchdog.list_claude_panes(run, logs=logs, dry_run=dry_run)
    roots = {c for _p, c in panes}
    roots.update(_cache_repo_roots(home, max_age_s=watchdog.GKREQ_CACHE_MAX_AGE_S))

    for root in sorted(roots):
        if not _gkreq_supervisor_root(root):
            continue                           # requester homes never bounce
        if not _repo_in_cross_stream_flow(root):
            continue                           # not a gatekeeper<->sub-dev repo
        name = os.path.basename(root.rstrip("/"))
        candidates = fetch(root)
        if candidates is None:
            continue                           # gh error → keep prior state
        for c in candidates:
            num = c.get("number")
            should, reason = _selfservice_bounce_decide(
                c.get("labels"), c.get("has_line"), c.get("origin_stream"))
            key = "%s#%s" % (name, num)
            if not should:
                logs.append("gk-selfservice-skip %s (%s)" % (key, reason))
                continue
            if key in seen:
                logs.append("gk-selfservice-already %s" % key)
                continue
            stream = c.get("origin_stream")
            # A dry-run mutates NOTHING, so it must NOT persist the one-shot
            # dedup latch — the check goes BEFORE the `seen` write (#516
            # adversarial review F1: unlike job 11's re-fireable nudge dedup,
            # this `seen` never re-evaluates, so a `watchdog --once --dry-run`
            # that latched `seen[key]` to the REAL state file would suppress the
            # ticket's genuine bounce on every later timer sweep, forever).
            if dry_run:
                logs.append("gk-selfservice-bounce %s -> stream:%s (dry-run)"
                            % (key, stream))
                continue
            # #193 kill-safe: record the dedup BEFORE the mutation; undo it only
            # if the bounce could not even post its comment (retry next sweep).
            seen[key] = int(now)
            persist()
            ok = apply_bounce(root, num, stream)
            if ok:
                logs.append("gk-selfservice-bounce %s -> stream:%s (bounced)"
                            % (key, stream))
            else:
                seen.pop(key, None)
                persist()
                logs.append("gk-selfservice-bounce-failed %s (comment did not "
                            "post; retry next sweep)" % key)
    return logs


# --------------------------------------------------------------------------- #
# #551 — ORPHANED gk HAND-OFF MARKER BACKSTOP (job 36). The miva1 incident
# (odoo-erp #3244): a stream HAND-WROTE a gk hand-off marker COMMENT in a
# MUTATED shape — `GATEKEEPER-ACTION (spresnenie …):` (a parenthetical BEFORE
# the colon) — instead of using `airuleset.py gk-request`. The odoo-erp
# auto-label workflow matches only a line-start `GATEKEEPER-ACTION:` (colon
# immediately after the token), so the mutated shape triggered no
# `needs-gatekeeper` label; and job 11's own candidate queries
# (`needs-gatekeeper` label ∪ `GATEKEEPER-ACTION:` in:title) never scan
# COMMENTS at all — so the hand-off was invisible to every existing layer and
# the stream parked on a NEVER-DELIVERED request for hours.
#
# This backstop closes that gap on the SUPERVISOR side. It is the DETECTION-
# side mirror of the prevention hook `block-raw-gatekeeper-action-comment.sh`
# (which steers a stream's raw marker comment to the reliable CLI at source).
#
# The design-critical fact (measured live on the real repo, 60 candidates): the
# token `GATEKEEPER-ACTION` is PERVASIVE in this repo's comment history (every
# gk-request leaves a `GATEKEEPER-ACTION:` marker forever; the label is removed
# after processing), so a naive "token in a comment + no label = orphan" rule
# would FALSE-ACCUSE ~44 already-processed tickets. The FALSIFIABLE orphan
# signature is narrow and ANDed from five conditions, biased hard toward
# SILENCE (never a false accusation — the ticket's own hard requirement):
#   (a) a comment carries a MUTATED marker (line-start token, interposed
#       non-colon text, then a colon — the exact shape the workflow misses),
#   (b) NO comment carries a PROPER `GATEKEEPER-ACTION:` marker (which the
#       workflow WOULD have labeled — a processed-then-cleared hand-off),
#   (c) `needs-gatekeeper` is NOT currently a label (authoritative live read),
#   (d) `needs-gatekeeper` was NEVER in the label timeline (PAGINATED — a
#       manually-labeled-then-cleared ticket is not an orphan),
#   (e) the title does not carry the `GATEKEEPER-ACTION` prefix (job 11's
#       in:title query already surfaces those).
# A "proper marker present but never labeled" case (gk may have worked it
# directly from the comment) is deliberately LEFT to manual triage — that is
# the exact "line-present-but-pure-read" judgement #516 forbids mechanizing.
# --------------------------------------------------------------------------- #

# Marker classifiers, applied PER LINE (re.match anchors each line's start).
# `_GK_ANY_MARKER_RE`: any line-start hand-off marker (proper OR mutated) —
# token, up to 80 non-colon chars, then a colon. `_GK_PROPER_MARKER_RE`: the
# workflow-matched proper shape (token, optional horizontal ws, colon) — kept
# GENEROUS (a trailing-space `GATEKEEPER-ACTION :` counts as proper) so the
# gate biases toward SILENCE. A line is MUTATED iff it is an any-marker that is
# NOT proper. `[ \t]` (never `\s`, which matches newlines and spans lines).
_GK_ANY_MARKER_RE = re.compile(r'[ \t]{0,3}GATEKEEPER-ACTION\b[^\n:]{0,80}:')
_GK_PROPER_MARKER_RE = re.compile(r'[ \t]{0,3}GATEKEEPER-ACTION[ \t]*:')

_GKORPHAN_EVIDENCE_TEMPLATE = (
    "gk hand-off backstop (airuleset#551): tento tiket nesie MUTOVANÝ "
    "`GATEKEEPER-ACTION` hand-off marker v komente, ktorý repo auto-label "
    "workflow (matchuje len line-start `GATEKEEPER-ACTION:`) nezachytil — takže "
    "`needs-gatekeeper` label nikdy nedopadol a gk fronta o tomto hand-offe "
    "nevedela. Doplnil som `needs-gatekeeper` label zo supervisor strany, aby "
    "job 11 hand-off zdvihol. Ak je to omyl (hand-off je už vybavený), label "
    "odober. Stream: nabudúce použi `airuleset.py gk-request`, nie ručný "
    "komentár — CLA cesta label doručí spoľahlivo.")

_GKORPHAN_PING_TEMPLATE = (
    "⚠️ **%(name)s: orphaned gk hand-off #%(num)d**\n> Tiket nesie mutovaný "
    "`GATEKEEPER-ACTION` komentár bez `needs-gatekeeper` labelu (workflow ho "
    "minul), ale label sa nepodarilo doplniť automaticky. Skontroluj #%(num)d "
    "v `%(root)s` — hand-off pravdepodobne nebol nikdy doručený gk fronte.")


def _gk_marker_kinds(comment_bodies):
    """PURE classifier: does ANY comment carry a MUTATED marker, and does ANY
    carry a PROPER marker? Returns (has_mutated: bool, has_proper: bool).
    Scanned PER LINE so a body with both a proper marker on one line and a
    mutated one on another is classified correctly on both axes."""
    has_mutated = has_proper = False
    for b in (comment_bodies or []):
        for line in str(b or "").split("\n"):
            if _GK_PROPER_MARKER_RE.match(line):
                has_proper = True
            elif _GK_ANY_MARKER_RE.match(line):
                has_mutated = True
    return has_mutated, has_proper


def _gk_orphan_decide(has_mutated, has_proper, currently_labeled, ga_title,
                      ever_labeled):
    """PURE facts-in / verdict-out decision (#486 — no silent branch; the
    caller LOGS the verdict for EVERY candidate). Returns (is_orphan, reason).
    A candidate is an ORPHANED hand-off iff ALL five hold; every exclusion
    returns an explicit reason, so the sweep journal shows exactly why each
    candidate was or was not reconciled. `ever_labeled is None` (timeline
    unreadable) is treated as NOT-orphan — the fail-safe direction (never a
    false accusation on an unmeasurable fact)."""
    if not has_mutated:
        return False, "no-mutated-marker"
    if has_proper:
        return False, "has-proper-marker"        # workflow would have labeled
    if ga_title:
        return False, "ga-title-already-discoverable"
    if currently_labeled:
        return False, "already-labeled"
    if ever_labeled is None:
        return False, "timeline-undeterminable"   # fail-safe: never accuse
    if ever_labeled:
        return False, "was-labeled-then-cleared"
    return True, "orphaned-mutated-marker"


def _fetch_gk_orphan_candidates(root, home=None):
    """Live candidate facts for the orphan sweep at `root`, or None on ANY
    error (fail-safe — an auth/network hiccup must never look like a real
    orphan). Returns a list of dicts, each with the facts `_gk_orphan_decide`
    needs: `{number, has_mutated, has_proper, currently_labeled, ga_title,
    ever_labeled}`.

    ONE cheap search narrows the field (the token in a comment, no label);
    then per candidate a single `gh issue view` supplies the authoritative
    labels/title/comments — the search's own `-label` filter and `in:comments`
    match both LAG the index and TOKENIZE, so nothing the search says is
    trusted, it only bounds the set. Candidates are freshest-first (updatedAt
    DESC — a live orphan is a fresh waiting hand-off) and capped at
    `_SELFSERVICE_MAX_CANDIDATES` to bound the 120s sweep budget (#172/#504).
    The PAGINATED timeline read (the `ever_labeled` fact) fires ONLY for a
    candidate already past the cheap gate (mutated ∧ ¬proper ∧ ¬labeled ∧
    ¬ga_title) — ≈0 per sweep on real data — so its cost is negligible."""
    import subprocess
    env = _gh_env(home)
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--search",
             '"GATEKEEPER-ACTION" in:comments -label:needs-gatekeeper '
             + watchdog.AUTOPILOT_SKIP_EXCL,
             "-L", "60", "--json", "number,title,updatedAt"],
            cwd=root, env=env, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        rows = json.loads(r.stdout or "[]")
    except Exception:
        return None
    # Drop title-discoverable rows cheaply; freshest first; cap.
    rows = [x for x in rows
            if not str(x.get("title", "")).startswith("GATEKEEPER-ACTION")]
    rows.sort(key=lambda x: str(x.get("updatedAt") or ""), reverse=True)
    out = []
    for x in rows[:_SELFSERVICE_MAX_CANDIDATES]:
        num = x.get("number")
        try:
            v = subprocess.run(
                ["gh", "issue", "view", str(num), "--json",
                 "number,title,labels,comments"],
                cwd=root, env=env, capture_output=True, text=True, timeout=15)
            if v.returncode != 0:
                return None
            d = json.loads(v.stdout or "{}")
        except Exception:
            return None
        labels = {str((lb or {}).get("name", ""))
                  for lb in (d.get("labels") or []) if isinstance(lb, dict)}
        title = str(d.get("title") or "")
        bodies = [str((c or {}).get("body") or "")
                  for c in (d.get("comments") or []) if isinstance(c, dict)]
        has_mutated, has_proper = _gk_marker_kinds(bodies)
        currently_labeled = "needs-gatekeeper" in labels
        ga_title = title.startswith("GATEKEEPER-ACTION")
        ever_labeled = None
        if has_mutated and not has_proper and not currently_labeled \
                and not ga_title:
            ever_labeled = _gk_ever_labeled(root, num, env)
        out.append({"number": num, "has_mutated": has_mutated,
                    "has_proper": has_proper,
                    "currently_labeled": currently_labeled,
                    "ga_title": ga_title, "ever_labeled": ever_labeled})
    return out


def _gk_ever_labeled(root, num, env):
    """Was `needs-gatekeeper` EVER applied to issue `num` (PAGINATED timeline —
    a labeled event can sit past the first page, #551 live finding)? True /
    False / None (unreadable → fail-safe not-orphan). `gh api` substitutes
    {owner}/{repo} from the checkout at `root`."""
    import subprocess
    try:
        r = subprocess.run(
            ["gh", "api", "--paginate",
             "repos/{owner}/{repo}/issues/%d/timeline" % int(num),
             "--jq", '.[]|select(.event=="labeled" and '
                     '.label.name=="needs-gatekeeper")|.label.name'],
            cwd=root, env=env, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return "needs-gatekeeper" in (r.stdout or "")
    except Exception:
        return None


def _apply_gk_orphan_reconcile(root, num, home=None, dry_run=False):
    """Reconcile ONE orphaned hand-off: post the evidence comment (durable
    record FIRST), then add `needs-gatekeeper` (queue re-insertion — job 11
    then nudges the supervisor). Returns True iff the LABEL landed; the comment
    is the durable anchor either way. Never raises. On the supervisor box the
    label add normally succeeds (full authority); a failure is transient and
    the caller undoes the dedup to retry (job 31's shape)."""
    if dry_run:
        return True
    import subprocess
    env = _gh_env(home)

    def _gh(*args):
        try:
            return subprocess.run(["gh"] + list(args), cwd=root, env=env,
                                  capture_output=True, text=True, timeout=15)
        except Exception:
            return None

    body = _GKORPHAN_EVIDENCE_TEMPLATE
    c = _gh("issue", "comment", str(num), "--body", body)
    if c is None or c.returncode != 0:
        return False
    lab = _gh("issue", "edit", str(num), "--add-label", "needs-gatekeeper")
    return lab is not None and lab.returncode == 0


def gk_orphan_marker_sweep(now, run, state, send_fn, home=None, dry_run=False,
                           gh_fetch=None, apply_fn=None, interval=None,
                           persist=None, user=None):
    """Job 36 (#551) — see the section comment. Runs ONLY on a supervisor
    (full-authority) box (`_gkreq_supervisor_root`), for cross-stream repos
    (`_repo_in_cross_stream_flow`); a reduced-stream box never reconciles (it
    is the REQUESTER). Mutates state['gkorphan']; `persist` is invoked BEFORE
    the GitHub mutation (the job-8/11/31 kill-safe-dedup lesson). The `seen`
    dedup is a ONE-SHOT latch (a reconciled ticket gets the label → the next
    sweep's decider returns `already-labeled`), so `dry_run` must NOT persist
    it (#516 F1). Every candidate's verdict is logged (#486). Best-effort
    (never raises). Returns log lines."""
    interval = watchdog.GKORPHAN_INTERVAL if interval is None else interval
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in watchdog._FOREIGN_TMUX_USERS:
        return []                              # pane lives in another user's tmux
    g = state.get("gkorphan") or {}
    if (now - g.get("last_check", 0)) < interval:
        return []
    g["last_check"] = int(now)
    seen = dict(g.get("seen") or {})
    g["seen"] = seen
    state["gkorphan"] = g
    fetch = gh_fetch or (lambda root: _fetch_gk_orphan_candidates(root, home))
    apply_reconcile = apply_fn or (
        lambda root, num: _apply_gk_orphan_reconcile(root, num, home, dry_run))
    persist = persist or (lambda: None)
    persist()                                  # cadence stamp survives a kill
    logs = []

    panes = watchdog.list_claude_panes(run, logs=logs, dry_run=dry_run)
    roots = {c for _p, c in panes}
    roots.update(_cache_repo_roots(home, max_age_s=watchdog.GKREQ_CACHE_MAX_AGE_S))

    for root in sorted(roots):
        if not _gkreq_supervisor_root(root):
            continue                           # requester homes never reconcile
        if not _repo_in_cross_stream_flow(root):
            continue                           # not a gatekeeper<->sub-dev repo
        name = os.path.basename(root.rstrip("/"))
        candidates = fetch(root)
        if candidates is None:
            continue                           # gh error → keep prior state
        for c in candidates:
            num = c.get("number")
            is_orphan, reason = _gk_orphan_decide(
                c.get("has_mutated"), c.get("has_proper"),
                c.get("currently_labeled"), c.get("ga_title"),
                c.get("ever_labeled"))
            key = "%s#%s" % (name, num)
            if not is_orphan:
                logs.append("gk-orphan-skip %s (%s)" % (key, reason))
                continue
            if key in seen:
                logs.append("gk-orphan-already %s" % key)
                continue
            # #516 F1: a one-shot latch must NOT persist under a dry run, else
            # a diagnostic --dry-run suppresses the real reconcile forever.
            if dry_run:
                logs.append("gk-orphan-reconcile %s (dry-run)" % key)
                continue
            seen[key] = int(now)               # dedup BEFORE the mutation (#193)
            persist()
            if apply_reconcile(root, num):
                logs.append("gk-orphan-reconcile %s (labeled + evidence)" % key)
            else:
                # label add failed (transient / permission): undo the dedup so
                # a later sweep retries, and surface it once via a ping.
                seen.pop(key, None)
                persist()
                send_fn(_GKORPHAN_PING_TEMPLATE
                        % {"name": name, "num": num, "root": root},
                        dedup_key="gkorphan:%s:%d" % (key, int(now)),
                        dry_run=dry_run, project=name)
                logs.append("gk-orphan-reconcile-failed %s (label add failed; "
                            "pinged, retry next sweep)" % key)
    return logs


def _cached_backlog_open(cwd, backlog_fetch, state, now, ttl=None):
    """True/False/None -- does the repo at `cwd` have an open, actionable
    (non-`autopilot-skip`) issue backlog right now? Cached per `cwd` in
    `state['backlog_cache']` for `ttl` (default `BACKLOG_CHECK_INTERVAL_S`
    seconds for a genuine True/False answer, `BACKLOG_CHECK_FAILURE_TTL_S`
    for an unmeasurable/failed one — #160-review 🔵F5).

    `backlog_fetch is None` (not wired) -> None, unconditionally, no cache
    write -- same "wired = on" convention as every other injected fetch in
    this file. None is UNMEASURABLE and every caller must treat it as
    "cannot tell, do not act" -- never as either polarity (#166's
    carried-forward fail-open requirement)."""
    if backlog_fetch is None:
        return None
    ttl = watchdog.BACKLOG_CHECK_INTERVAL_S if ttl is None else ttl
    cache = state.setdefault("backlog_cache", {})
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        # #160-review 🔵F9 -- `ts` crosses a JSON persistence boundary
        # (this repo's own established rule: never trust a `.get()` off
        # such a boundary without a type check) -- a malformed/legacy
        # entry must read as EXPIRED, never raise or silently misbehave.
        try:
            age = now - float(entry.get("ts", 0))
        except (TypeError, ValueError):
            age = None
        if age is not None:
            entry_ttl = (ttl if entry.get("open") is not None
                        else watchdog.BACKLOG_CHECK_FAILURE_TTL_S)
            if age < entry_ttl:
                return entry.get("open")
    try:
        count = backlog_fetch(cwd)
    except Exception:
        count = None
    open_ = (count > 0) if isinstance(count, int) else None
    # #365 -- also keep the RAW COUNT alongside the boolean, in the SAME
    # cache entry, so `_cached_backlog_count` below never needs a second
    # `gh` round trip: one fetch already produced both facts, this just
    # stops throwing the number away.
    cache[cwd] = {"ts": now, "open": open_, "count": count}
    return open_


def _cached_backlog_count(cwd, backlog_fetch, state, now, ttl=None):
    """The raw open-issue COUNT behind `_cached_backlog_open`'s own
    True/False/None verdict (#365) -- reuses the IDENTICAL cache entry
    (calling `_cached_backlog_open` first to guarantee it is fresh/warm),
    never a second `gh` round trip. Returns the cached int, or None when
    the fetch was never wired, never warmed, or itself returned None
    (unmeasurable), OR the cached entry predates this field being added
    (a legacy entry with no `count` key at all, until it next expires and
    refreshes) -- same fail-safe direction as its sibling: an unmeasurable
    count must never be guessed as 0."""
    _cached_backlog_open(cwd, backlog_fetch, state, now, ttl=ttl)
    cache = state.get("backlog_cache") or {}
    entry = cache.get(cwd)
    if not isinstance(entry, dict):
        return None
    count = entry.get("count")
    return count if isinstance(count, int) else None
