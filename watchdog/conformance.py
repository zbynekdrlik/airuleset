"""Periodic per-box cross-target CONFORMANCE check (#535) — watchdog job 34.

Uniformity of the generalized ``~/.claude/CLAUDE.md`` + the airuleset repo across
the fleet is guaranteed TODAY only by push-time deploy (``cmd_install`` regenerates
CLAUDE.md; ``cmd_push`` deploys to every ``REMOTE_HOSTS`` target) plus a prose/hook
ban on hand-edits. Nothing READS the post-deploy state, so drift after a deploy —
a box that never received the push, a hand-edited ``~/.claude/CLAUDE.md``, a repo
left behind ``origin/main``, or a dead ``api-watchdog.timer`` — stays invisible
until someone notices by accident.

This job is the PER-BOX SELF-CHECK (design fork variant (a), #535): each box, in
its OWN watchdog, on a DAILY cadence, compares four structured dimensions against
their expected state and LOUD-pings the owner via the existing ``send_fn`` notify
path on divergence, deduped. No ssh, no central fan-out — it works even when dev1
is asleep. The one gap this variant cannot cover (a DEAD box's self-check sends
nothing) is DEFERRED to a filed central "heartbeat-missing" follow-up, not
silently dropped (see the #535 design comment).

Idiom (cluster C, #433): ONE top-level ``import watchdog``; every reused package
name (``_sweep_due``) is read at CALL time as ``watchdog.<name>``. Circular-import-
safe: ``__init__.py``'s facade re-export loads this module mid-init, but this
module dereferences NO ``watchdog`` attribute at load time — only inside function
bodies, long after the package finishes initializing.

DESIGN (#486 direction): tens of lines of STRUCTURED comparisons, no new
thousand-line heuristic. Each dimension is a PURE decision function
``classify_<dim>(facts) -> (dim, ok, detail)`` where ``ok`` is:

  * ``True``  — CONFORMANT (matches the fleet / expected state);
  * ``False`` — genuine DRIFT (alarm);
  * ``None``  — UNDETERMINED this sweep (a git error, a fetch failure, a missing
                baseline, an obscured local-dev state) → logged, NEVER an alarm.

``run_conformance_check`` owns ALL I/O (the injectable ``git_run`` primitive, the
``timer_check`` callable, ``hashlib`` md5, the baseline JSON read) and calls the
pure deciders, so the safety-critical "never a false drift alarm" invariant lives
in trivially-auditable pure functions. Anything uncertain returns ``None``.
"""
import hashlib
import json
import os

import watchdog

CONFORMANCE_BASELINE_NAME = ".airuleset-conformance-baseline.json"
CONFORMANCE_CHECK_INTERVAL_S = 24 * 3600      # env AIRULESET_CONFORMANCE_CHECK_S —
                                               # daily; the bounded ``git fetch`` for
                                               # the HEAD dimension rides this cadence,
                                               # so at most 1 fetch/day
CONFORMANCE_CHECK_MIN_S = 3600                 # floor for the env override (#504/#172
                                               # pattern): a sub-hour value is a units
                                               # error that would `git fetch` every
                                               # 60s sweep — clamp UP, never honor 0
CONFORMANCE_REPING_S = 3 * 24 * 3600          # env AIRULESET_CONFORMANCE_REPING_S —
                                               # re-remind cadence for an UNCHANGED
                                               # divergence: >1 day (never a daily
                                               # re-spam), but finite (never
                                               # permanently silent — the #134 class)
CONFORMANCE_REPING_MIN_S = 24 * 3600          # floor: a sub-daily re-remind would
                                               # re-spam every daily check — clamp UP
WATCHDOG_TIMER_UNIT = "api-watchdog.timer"
CONF_GIT_TIMEOUT_S = 15                         # network op (fetch) bound (#172 class)


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def default_baseline_path():
    """The install-recorded baseline JSON path. cmd_install WRITES it (via
    ``record_conformance_baseline``); this job READS it. Both derive it from
    ``~/.claude`` so they agree by construction."""
    return os.path.join(os.path.expanduser("~"), ".claude", CONFORMANCE_BASELINE_NAME)


def _conf_git(args, cwd, timeout=CONF_GIT_TIMEOUT_S):
    """``git -C <cwd> <args>`` -> ``(returncode, stdout)``; ``(None, "")`` on any
    subprocess-level failure (spawn error, timeout). Never raises — the sweep must
    not crash a ``run_once`` poll. The ``(None, "")`` sentinel IS the signal, not a
    silent swallow (mirrors ``wip_ref_sweep._wip_git``); ``merge-base
    --is-ancestor`` is exit-code-only, so the return code is kept, never conflated
    with empty stdout."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return (None, "")     # sentinel IS the signal — never a silent swallow
    return (r.returncode, r.stdout)


def _timer_status(unit=WATCHDOG_TIMER_UNIT):
    """``systemctl --user is-active <unit>`` -> its verbatim word (``active`` /
    ``inactive`` / ``failed`` / ``activating`` ...), or ``None`` when systemctl is
    absent / not a systemd-user box / the call errors — an UNDETERMINED read, never
    a drift. ``is-active`` exits non-zero for a non-active unit but still prints the
    state word on stdout, so the STDOUT word is authoritative (rc alone would read a
    genuinely-inactive timer identically to a spawn failure)."""
    import subprocess
    try:
        # #826: mirror cli_filedrop_watchdog._xdg_runtime_env — a non-login ssh
        # `watchdog --once` needs BOTH XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS
        # or `systemctl --user` fails 'No medium found'. DBUS is derived from the
        # EFFECTIVE XDG value (never re-derived from the uid) so an ambient
        # non-default XDG stays coherent. (Self-contained to keep this a watchdog
        # leaf; the push/install call sites route through the shared helper.)
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=%s/bus" % env["XDG_RUNTIME_DIR"])
        r = subprocess.run(["systemctl", "--user", "is-active", unit],
                           capture_output=True, text=True, timeout=10, env=env)
    except Exception:
        return None           # UNDETERMINED (no systemctl / not a systemd box)
    word = (r.stdout or "").strip()
    return word or None


def _md5_hex(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _md5_file(path):
    """md5 hex of a file's bytes, or ``None`` if unreadable (an UNDETERMINED read,
    never a drift)."""
    try:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except Exception:
        return None           # unreadable -> UNDETERMINED, never a drift


def _read_baseline(path):
    """The install-recorded ``{claude_md_md5, head_sha, recorded_at}`` dict, or
    ``{}`` when the file is absent / unreadable / malformed (a pre-conformance box,
    handled as UNDETERMINED by ``classify_md5``)."""
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}             # absent/malformed -> pre-conformance box, UNDETERMINED


def record_conformance_baseline(claude_md_content, repo_root, dest_path,
                                git_run=None):
    """Record ``{claude_md_md5, head_sha, recorded_at}`` for ``claude_md_content``
    (the exact bytes cmd_install wrote to ``~/.claude/CLAUDE.md``) + the repo's
    current HEAD. cmd_install calls this AFTER writing CLAUDE.md, so the md5 and the
    file on disk agree atomically — and the HEAD lets ``classify_md5`` skip the md5
    dimension on a mid-push box (repo advanced but install not yet re-run), which is
    what makes the md5 dimension structurally immune to a mid-push false alarm
    (#535 design). Returns the recorded dict; best-effort (a write failure is
    logged into the returned dict, never raised — a missing baseline degrades the
    md5 dimension to UNDETERMINED, never a crash of install)."""
    git_run = git_run or _conf_git
    rc, out = git_run(["rev-parse", "HEAD"], repo_root)
    head = (out or "").strip() if rc == 0 else None
    rec = {"claude_md_md5": _md5_hex(claude_md_content),
           "head_sha": head,
           "recorded_at": None}
    try:
        import time
        rec["recorded_at"] = int(time.time())
        tmp = str(dest_path) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(rec, fh)
        os.replace(tmp, dest_path)
    except Exception as e:
        # best-effort: a write failure must not crash install — a missing baseline
        # simply degrades the md5 dimension to UNDETERMINED (never a false alarm).
        rec["write_error"] = repr(e)
    return rec


# --- PURE DECIDERS ---------------------------------------------------------
# facts in -> (dim, ok, detail); ok True=conformant / False=drift / None=unknown.

def classify_head(local_sha, origin_sha, behind):
    """HEAD vs origin/main. Drift ONLY when strictly BEHIND (the box never received
    a deploy). Ahead/diverged = local dev / mid-integration on dev1 = UNDETERMINED
    (no alarm). Unreadable shas = UNDETERMINED."""
    if not local_sha or not origin_sha:
        return ("head", None, "HEAD/origin nečitateľné — preskočené")
    if local_sha == origin_sha:
        return ("head", True, "HEAD == origin/main (%s)" % local_sha[:8])
    if behind:
        return ("head", False,
                "repo POZADU: HEAD %s je pozadu za origin/main %s — deploy "
                "neprišiel/zlyhal" % (local_sha[:8], origin_sha[:8]))
    return ("head", None,
            "HEAD %s napred/divergovaný vs origin/main %s — lokálny vývoj, žiaden "
            "alarm" % (local_sha[:8], origin_sha[:8]))


def classify_dirty(porcelain, error, clean_expected):
    """A clean working tree is only EXPECTED — and a dirty one only DRIFT — on a
    box that (a) is a fleet DEPLOY TARGET (receives read-only ``git pull --ff-only``
    deploys and never develops airuleset) AND (b) is AT the fleet baseline
    (HEAD == origin/main). ``clean_expected`` False → UNDETERMINED, never a drift:

      * the DEPLOY SOURCE (dev1, the airuleset dev checkout, NOT in REMOTE_HOSTS)
        is legitimately dirty during any airuleset session — even at HEAD==origin,
        with uncommitted edits before the next commit (#535 review MAJOR-A);
      * a box off baseline (HEAD ahead/diverged = local dev, behind = a stale box
        the HEAD dimension already owns, or origin unreadable) — the same local-dev
        exemption ``classify_head`` gives an ahead HEAD (#535 review MAJOR-1).

    On a deploy target at baseline: empty = conformant; non-empty = DRIFT (a
    hand-edited deployed box); a git error = UNDETERMINED."""
    if not clean_expected:
        return ("dirty", None,
                "clean tree sa neočakáva (deploy-source box alebo mimo fleet "
                "baseline) — dirty check preskočený (lokálny vývoj/stale)")
    if error:
        return ("dirty", None, "git status zlyhal — preskočené")
    n = len([ln for ln in (porcelain or "").splitlines() if ln.strip()])
    if n == 0:
        return ("dirty", True, "pracovný strom čistý")
    return ("dirty", False,
            "pracovný strom má %d neuložených zmien — možný ručný edit repa" % n)


def classify_md5(on_disk_md5, recorded_md5, recorded_head, current_head):
    """``~/.claude/CLAUDE.md`` md5 vs the install-recorded baseline. UNDETERMINED
    (never a drift) when: no baseline (pre-conformance box), the baseline's HEAD !=
    the current HEAD (install pending after a repo move — mid-push, the HEAD
    dimension owns that signal), or the on-disk file is unreadable. Otherwise
    match=conformant / mismatch=DRIFT (a hand-edit)."""
    if not recorded_md5:
        return ("claude_md", None,
                "žiaden install baseline — preskočené (pre-conformance box)")
    if recorded_head and current_head and recorded_head != current_head:
        return ("claude_md", None,
                "install ešte nebežal po posune repa (baseline HEAD %s != HEAD %s) "
                "— preskočené" % (recorded_head[:8], current_head[:8]))
    if not on_disk_md5:
        return ("claude_md", None, "~/.claude/CLAUDE.md nečitateľné — preskočené")
    if on_disk_md5 == recorded_md5:
        return ("claude_md", True, "~/.claude/CLAUDE.md md5 sedí s installom")
    return ("claude_md", False,
            "~/.claude/CLAUDE.md sa líši od installu (md5 %s vs %s) — možný ručný "
            "edit" % (on_disk_md5[:8], recorded_md5[:8]))


_TIMER_DRIFT_STATES = ("inactive", "failed")


def classify_timer(status):
    """``api-watchdog.timer`` active = conformant; ONLY the genuinely-stopped states
    in ``_TIMER_DRIFT_STATES`` (``inactive``/``failed``) = DRIFT (the watchdog is not
    firing on schedule). Every OTHER non-active word — a TRANSIENT state
    (``activating``/``reloading``/``deactivating`` — a sweep landing during a
    ``systemctl --user daemon-reload``/restart) or any unknown future state — falls to
    the catch-all → UNDETERMINED, never a spurious ping (#535 review NIT-1); an
    unreadable status = UNDETERMINED. Drift is an ALLOWLIST of known-bad states, so a
    novel state is fail-safe (no alarm), never a guess."""
    if status is None:
        return ("timer", None, "systemctl nedostupné — preskočené")
    if status == "active":
        return ("timer", True, "api-watchdog.timer active")
    if status in _TIMER_DRIFT_STATES:
        return ("timer", False,
                "api-watchdog.timer je '%s' — watchdog nebeží pravidelne" % status)
    return ("timer", None,
            "api-watchdog.timer stav '%s' (prechodný/neznámy) — preskočené" % status)


# --- ORCHESTRATOR ----------------------------------------------------------

def _sig_for(dim, facts):
    """Compact dedup signature per dimension from its raw facts — a CHANGED sig
    re-pings immediately (the drift is materially different); an unchanged sig is
    re-pinged only after ``reping`` elapses."""
    if dim == "head":
        return "head:%s:%s" % ((facts.get("local") or "")[:8],
                               (facts.get("origin") or "")[:8])
    if dim == "dirty":
        return "dirty:%s" % _md5_hex(facts.get("porcelain") or "")[:12]
    if dim == "claude_md":
        return "md5:%s" % (facts.get("on_disk") or "")
    if dim == "timer":
        return "timer:%s" % (facts.get("status") or "")
    return dim


def run_conformance_check(now, state, send_fn=None, dry_run=False,
                          repo_root=None, claude_md_path=None, baseline_path=None,
                          git_run=None, timer_check=None, is_target_check=None,
                          interval=None, reping=None, persist=None):
    """Job 34: the daily per-box conformance sweep. Cadence-gated on its OWN state
    key ``conformance_last_check`` (``_sweep_due``); the cadence marker is stamped +
    persisted BEFORE any network op (#172 kill-safe). Best-effort — every dimension
    fails safe to UNDETERMINED, never a raise, never a false alarm. Returns a
    decision log line per dimension (#486). ``dry_run`` mutates no persistent state
    and sends nothing (peek pattern)."""
    git_run = git_run or _conf_git
    timer_check = timer_check or _timer_status
    persist = persist or (lambda: None)
    if repo_root is None:
        return []
    if claude_md_path is None:
        claude_md_path = os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")
    if baseline_path is None:
        baseline_path = default_baseline_path()
    if interval is None:
        interval = max(_env_int("AIRULESET_CONFORMANCE_CHECK_S",
                                CONFORMANCE_CHECK_INTERVAL_S), CONFORMANCE_CHECK_MIN_S)
    if reping is None:
        reping = max(_env_int("AIRULESET_CONFORMANCE_REPING_S",
                              CONFORMANCE_REPING_S), CONFORMANCE_REPING_MIN_S)

    logs = []
    if not watchdog._sweep_due(state, "conformance_last_check", now, interval):
        return logs
    if not dry_run:
        # #172: stamp + persist the cadence marker BEFORE the git fetch leaves this
        # process — a systemd TimeoutStartSec kill mid-fetch must never re-run the
        # identical daily sweep forever.
        state["conformance_last_check"] = now
        persist()

    import socket
    try:
        host = socket.gethostname()
    except Exception:
        host = "?"           # host label only — a "?" is cosmetic, never a drift

    # --- gather facts (all I/O here; the deciders below are pure) ---
    # HEAD is network-free — read it always (also the md5 dimension's HEAD guard).
    rc_local, out_local = git_run(["rev-parse", "HEAD"], repo_root)
    local = (out_local or "").strip() if rc_local == 0 else None
    # bounded daily fetch; on failure SKIP the origin comparison (never measure the
    # HEAD dimension on stale refs — #172-F5), the head decider handles origin=None.
    # Not sweep_deadline-gated (#535 review MINOR-B, accepted residual): it is ONE
    # fetch at most once per DAY (the check is cadence-gated), the cadence marker is
    # already persisted above so a systemd TimeoutStartSec kill mid-fetch can never
    # re-run it (no loop), and CONF_GIT_TIMEOUT_S=15 bounds a single hung fetch —
    # so at worst it defers other jobs to the next sweep, never a livelock.
    rc_fetch, _ = git_run(["fetch", "--quiet", "--no-tags", "origin", "main"], repo_root)
    if rc_fetch == 0:
        rc_o, out_o = git_run(["rev-parse", "origin/main"], repo_root)
        origin = (out_o or "").strip() if rc_o == 0 else None
    else:
        origin = None
        logs.append("conformance %s fetch zlyhal (rc=%r) — HEAD dimenzia preskočená"
                    % (host, rc_fetch))
    if local and origin:
        rc_anc, _ = git_run(["merge-base", "--is-ancestor", local, origin], repo_root)
        behind = (rc_anc == 0)
    else:
        behind = False
    head_facts = {"local": local, "origin": origin}
    # A clean working tree is EXPECTED only where it is the invariant: a DEPLOY
    # TARGET (receives read-only `git pull --ff-only`, never develops airuleset —
    # `is_target_check`, POSITIVELY confirmed via the tailscale-IP∈REMOTE_HOSTS
    # membership cmd_watchdog wires) AND AT the fleet baseline (HEAD == origin/main).
    # The DEPLOY SOURCE (dev1) is legitimately dirty even at HEAD==origin (#535
    # review MAJOR-A), and any off-baseline box is local dev / stale (#535 review
    # MAJOR-1) — both silence the dirty dimension. Fail-safe: an unconfirmed target
    # (no tailscale / error) → is_target False → dirty SKIPPED, never a false alarm.
    # (The md5 dimension needs no such gate — its own `recorded_head != HEAD` guard
    # already exempts the source's uncommitted-but-not-installed edits.)
    is_target = bool(is_target_check()) if is_target_check else False
    clean_expected = bool(local and origin and local == origin) and is_target

    rc_st, out_st = git_run(["status", "--porcelain"], repo_root)
    dirty_error = (rc_st != 0)
    porcelain = out_st or ""
    dirty_facts = {"porcelain": porcelain}

    on_disk_md5 = _md5_file(claude_md_path)
    baseline = _read_baseline(baseline_path)
    recorded_md5 = baseline.get("claude_md_md5")
    recorded_head = baseline.get("head_sha")
    md5_facts = {"on_disk": on_disk_md5}

    status = timer_check()
    timer_facts = {"status": status}

    # --- decide (pure) ---
    decisions = [
        (classify_head(local, origin, behind), head_facts),
        (classify_dirty(porcelain, dirty_error, clean_expected), dirty_facts),
        (classify_md5(on_disk_md5, recorded_md5, recorded_head, local), md5_facts),
        (classify_timer(status), timer_facts),
    ]

    seen = dict(state.get("conformance") or {})
    if not dry_run:
        state["conformance"] = seen      # same dict from here on (#172-F3)

    for (dim, ok, detail), facts in decisions:
        logs.append("conformance %s [%s] %s -- %s"
                    % (host, dim, {True: "OK", False: "DRIFT", None: "unknown"}[ok],
                       detail))
        if ok is True:
            # resolved — a re-divergence re-pings immediately
            if dim in seen and not dry_run:
                seen.pop(dim, None)
            continue
        if ok is None:
            # UNDETERMINED — a confidence gap must NOT drop a prior episode
            # (#486-G5): neither ping nor clear the dedup entry.
            continue
        # ok is False -> genuine drift
        sig = _sig_for(dim, facts)
        prev = seen.get(dim) or {}
        same = (prev.get("sig") == sig)
        pinged = prev.get("pinged_ts")
        if send_fn is None or dry_run or (
                same and pinged is not None and (now - float(pinged)) < reping):
            if dry_run:
                logs.append("conformance %s [%s] WOULD-PING -- %s" % (host, dim, detail))
            continue
        seen[dim] = {"sig": sig, "pinged_ts": now}
        if not dry_run:
            persist()      # dedup memory BEFORE the ping (#172-F3)
        status_word = send_fn(
            "\U0001f527 **conformance drift** na boxe `%s` — airuleset sa rozišiel "
            "s fleetom:\n> %s\n> Skontroluj deploy / hand-edit; na dev1 spusti "
            "`python3 airuleset.py push` alebo over tento box." % (host, detail),
            # FRESH per real decision INSTANT, never a `now // reping` BUCKET
            # (#535 review MAJOR-2): the per-dimension `seen` dedup above is the
            # authoritative gate — it already skips a same-sig ping within `reping`
            # and allows a changed-sig / re-divergence ping immediately. A bucketed,
            # sig-independent dedup_key would SWALLOW exactly those allowed re-pings
            # inside notify's own (longer) dedup TTL. `int(now)` is unique per
            # genuine decision (daily cadence + per-dim `seen` gate = never two in
            # one second), so it only ever dedups an exact re-fire of the same
            # decision (the gk_request_backstop "fresh per decision instant" lesson).
            dedup_key="conformance:%s:%s:%d" % (host, dim, int(now)),
            dry_run=dry_run)
        logs.append("conformance %s [%s] PING -> %s" % (host, dim, status_word))
    return logs
