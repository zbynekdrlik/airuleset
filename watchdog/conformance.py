"""Periodic per-box cross-target CONFORMANCE check (#535) — watchdog job 34.

Uniformity of the generalized ``~/.claude/CLAUDE.md`` + the airuleset repo across
the fleet is guaranteed TODAY only by push-time deploy (``cmd_install`` regenerates
CLAUDE.md; ``cmd_push`` deploys to every ``REMOTE_HOSTS`` target) plus a prose/hook
ban on hand-edits. Nothing READS the post-deploy state, so drift after a deploy —
a box that never received the push, a hand-edited ``~/.claude/CLAUDE.md``, a repo
left behind ``origin/main``, or a dead ``api-watchdog.timer`` — stays invisible
until someone notices by accident.

This job is the PER-BOX SELF-CHECK (design fork variant (a), #535): each box, in
its OWN watchdog, on a DAILY cadence, compares structured dimensions against their
expected state and surfaces divergence, deduped. #1032: divergence is a FLEET-OPS
signal — it goes to the JOURNAL (a decision line per dimension + a ``SURFACED``
escalation) and to the persisted ``state["conformance"]`` snapshot the SUPERVISOR
reads via ``airuleset.py status`` (``conformance_status_row``); it is NEVER a
Discord owner ping (the daily paused-box owner spam that removal fixes). No ssh, no
central fan-out. The one gap this variant cannot cover (a DEAD box's self-check
runs nothing) is covered by the central "heartbeat-missing" detector (job 35), not
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
    the catch-all → UNDETERMINED, never a spurious drift (#535 review NIT-1); an
    unreadable status = UNDETERMINED. Drift is an ALLOWLIST of known-bad states, so a
    novel state is fail-safe (no drift), never a guess."""
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

def classify_symlinks(drift_entries):
    """#972 REOPEN: managed agent/skill symlinks under ~/.claude that are
    dangling or point into a worktree — the CONSUMER of `cmd_status`'s MISMATCH
    detection (which had no consumer, so the 2026-09-11 drift sat unacted 19 h).
    ``drift_entries`` is a list of ``(name, reason, target)`` from the injected
    scan, or ``None`` when the scan errored. Contract mirrors the other pure
    deciders: True conformant / False drift / None undetermined (never alarmed).
    """
    if drift_entries is None:
        return ("symlinks", None, "symlink scan zlyhal — preskočené")
    if not drift_entries:
        return ("symlinks", True, "managed agent/skill symlinky OK")
    n = len(drift_entries)
    sample = ", ".join("%s (%s)" % (name, reason)
                       for name, reason, _t in drift_entries[:4])
    return ("symlinks", False,
            "%d managed symlink(ov) dangling/worktree-target: %s — spusti "
            "`python3 airuleset.py install` z HLAVNÉHO checkoutu" % (n, sample))


def classify_doctrine_drift(counts):
    """#1028: per-stream restatements of GRADUATED fleet doctrine (a rule that
    became an airuleset module/skill but whose local memory/rule copy was never
    retired). ``counts`` is ``{"high": N, "medium": M}`` from the injected
    ``cli_doctrine_audit`` scan (HIGH = an auto-fixable rewrite still present —
    normally 0 after ``cmd_install``'s fix step, > 0 means the fix FAILED;
    MEDIUM = a human-review listing), or ``None`` when the scan errored. Same
    pure contract as the other deciders: True conformant / False drift / None
    undetermined (never alarmed). #1032: report-only — the caller SURFACES to the
    journal + snapshot, never an owner ping."""
    if counts is None:
        return ("doctrine-drift", None, "doctrine audit preskočený (scan zlyhal)")
    high = int(counts.get("high", 0) or 0)
    med = int(counts.get("medium", 0) or 0)
    if high == 0 and med == 0:
        return ("doctrine-drift", True, "žiadne graduated-rule lokálne kópie")
    parts = []
    if high:
        parts.append("%d HIGH (install auto-fix zlyhal)" % high)
    if med:
        parts.append("%d MEDIUM (human review)" % med)
    return ("doctrine-drift", False,
            "graduated-rule lokálne zvyšky: %s — pozri `python3 airuleset.py "
            "doctrine-audit`" % ", ".join(parts))


def _sig_for(dim, facts):
    """Compact dedup signature per dimension from its raw facts — a CHANGED sig
    re-surfaces immediately (the drift is materially different); an unchanged sig
    is re-surfaced only after ``reping`` elapses (#1032: journal SURFACED lines,
    never an owner ping)."""
    if dim == "head":
        return "head:%s:%s" % ((facts.get("local") or "")[:8],
                               (facts.get("origin") or "")[:8])
    if dim == "dirty":
        return "dirty:%s" % _md5_hex(facts.get("porcelain") or "")[:12]
    if dim == "claude_md":
        return "md5:%s" % (facts.get("on_disk") or "")
    if dim == "timer":
        return "timer:%s" % (facts.get("status") or "")
    if dim == "symlinks":
        entries = facts.get("drift") or []
        return "symlinks:%s" % _md5_hex(
            "|".join(sorted("%s:%s" % (n, r) for n, r, _t in entries)))[:12]
    if dim == "doctrine-drift":
        c = facts.get("counts") or {}
        return "doctrine:%d:%d" % (int(c.get("high", 0) or 0),
                                   int(c.get("medium", 0) or 0))
    return dim


def run_conformance_check(now, state, dry_run=False,
                          repo_root=None, claude_md_path=None, baseline_path=None,
                          git_run=None, timer_check=None, is_target_check=None,
                          interval=None, reping=None, persist=None,
                          symlink_scan=None, doctrine_scan=None,
                          root_guard_provisioned_fn=None,
                          bashrc_drift_fn=None,
                          public_url_channel_fn=None):
    """Job 34: the daily per-box conformance sweep. Cadence-gated on its OWN state
    key ``conformance_last_check`` (``_sweep_due``); the cadence marker is stamped +
    persisted BEFORE any network op (#172 kill-safe). Best-effort — every dimension
    fails safe to UNDETERMINED, never a raise, never a false alarm. Returns a
    decision log line per dimension (#486). ``dry_run`` mutates no persistent state
    (peek pattern).

    #1032: this job NEVER pings the owner. Drift is a FLEET-OPS signal the
    SUPERVISOR reads via ``airuleset.py status`` (``conformance_status_row``, from
    the persisted ``state["conformance"]`` snapshot below) + the journal decision
    lines — NOT a Discord owner alert (the daily paused-``simap1`` spam this
    removes). The per-dimension dedup/re-surface bookkeeping is KEPT, repurposed to
    gate the JOURNAL ``SURFACED`` escalation line only (never a send)."""
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

    # #972 REOPEN: the CONSUMER of cmd_status's symlink MISMATCH detection. The
    # scan seam mirrors git_run/timer_check — tests inject a fake; the real
    # watchdog uses airuleset's own scanner (call-time import, the established
    # watchdog-leaf idiom: cards/cross_stream/goal/conformance_heartbeat all do
    # it, and airuleset.py's top level is side-effect-free behind __main__).
    if symlink_scan is None:
        import airuleset
        symlink_scan = airuleset._scan_managed_symlink_drift
    try:
        drift_entries = symlink_scan()
    except Exception:
        drift_entries = None      # scan failure → UNDETERMINED, never an alarm
    symlink_facts = {"drift": drift_entries}

    # #1028 doctrine-drift: per-stream restatements of graduated fleet doctrine.
    # Default scan reuses cli_doctrine_audit (same watchdog-leaf import idiom the
    # symlink scan above uses); tests inject a controlled counts dict.
    if doctrine_scan is None:
        def doctrine_scan():
            import cli_doctrine_audit as _da
            return _da.doctrine_counts(
                _da.scan_home(os.path.expanduser("~"), repo_dir=repo_root))
    try:
        doctrine_counts = doctrine_scan()
    except Exception:
        doctrine_counts = None    # scan failure → UNDETERMINED, never an alarm
    doctrine_facts = {"counts": doctrine_counts}

    # --- decide (pure) ---
    decisions = [
        (classify_head(local, origin, behind), head_facts),
        (classify_dirty(porcelain, dirty_error, clean_expected), dirty_facts),
        (classify_md5(on_disk_md5, recorded_md5, recorded_head, local), md5_facts),
        (classify_timer(status), timer_facts),
        (classify_symlinks(drift_entries), symlink_facts),
        (classify_doctrine_drift(doctrine_counts), doctrine_facts),
    ]

    seen = dict(state.get("conformance") or {})
    if not dry_run:
        state["conformance"] = seen      # same dict from here on (#172-F3)

    # #1047: a REPORT-ONLY per-box fact (NOT a drift dimension — a never-
    # provisioned owner workstation is a legitimate state, never an alarm): is
    # the root disk-guard provisioned on this box? Read from the SAME predicate
    # the disk-guard escalate discriminator uses (imported, never a second timer
    # check). Stored BESIDE `state["conformance"]` (a top-level key, so the
    # drift-episode loop and `conformance_status_row` never mistake it for a
    # DRIFT row); the SUPERVISOR reads it per box for the fleet provisioned view.
    try:
        # review 🟡: the IMPORT is inside the try too — an import failure (a rename
        # / a broken disk_guard) must degrade this fact to `unknown`, never raise
        # here and abort the drift loop below (the module's "never raises" invariant).
        if root_guard_provisioned_fn is None:
            from watchdog.disk_guard import _root_guard_provisioned as root_guard_provisioned_fn
        rgp = bool(root_guard_provisioned_fn())
    except Exception as e:
        rgp = None
        logs.append("conformance %s [root-guard] unknown -- %r" % (host, e))
    if rgp is not None:
        logs.append("conformance %s [root-guard] %s"
                    % (host, "provisioned" if rgp else "not provisioned"))
        if not dry_run:
            state["root_guard_provisioned"] = rgp

    # #1015: a REPORT-ONLY per-box fact (NOT a drift dimension — mirrors the
    # #1047 root-guard fact above): the count of stray `export CLAUDE_CODE_*`
    # lines OUTSIDE the managed ~/.bashrc / ~/.profile marker blocks. Injectable
    # for tests; the import is INSIDE the try so a broken leaf degrades to
    # `unknown`, never raises here and aborts the drift loop below.
    try:
        if bashrc_drift_fn is None:
            from cli_bashrc_drift import count_bashrc_drift as bashrc_drift_fn
        bdrift = int(bashrc_drift_fn())
    except Exception as e:
        bdrift = None
        logs.append("conformance %s [bashrc-drift] unknown -- %r" % (host, e))
    if bdrift is not None:
        logs.append("conformance %s [bashrc-drift] %d" % (host, bdrift))
        if not dry_run:
            state["bashrc_drift"] = bdrift

    # #1115 Slice C: a REPORT-ONLY per-account fact (NOT a drift dimension —
    # mirrors the #1047 root-guard + #1015 bashrc facts above): can THIS account
    # deliver a user-facing URL over the public Cloudflare channel? Resolves the
    # ONE `delivery_channel()` and, when live, probes the account's public `/s/`
    # probe path with a named User-Agent. `ok` / `fallback:<reason>` /
    # `broken:<code>`. Injectable for tests; the import is INSIDE the try so a
    # broken leaf degrades to `unknown`, never raises here.
    try:
        if public_url_channel_fn is None:
            import cli_drop_lanes
            public_url_channel_fn = cli_drop_lanes.public_url_channel_fact
        puc = public_url_channel_fn()
    except Exception as e:
        puc = None
        logs.append("conformance %s [public-url-channel] unknown -- %r" % (host, e))
    if puc is not None:
        logs.append("conformance %s [public-url-channel] %s" % (host, puc))
        if not dry_run:
            state["public_url_channel"] = puc

    for (dim, ok, detail), facts in decisions:
        logs.append("conformance %s [%s] %s -- %s"
                    % (host, dim, {True: "OK", False: "DRIFT", None: "unknown"}[ok],
                       detail))
        if ok is True:
            # resolved — drop the persisted episode so `status` shows OK again;
            # a re-divergence re-surfaces immediately.
            if dim in seen and not dry_run:
                seen.pop(dim, None)
            continue
        if ok is None:
            # UNDETERMINED — a confidence gap must NOT drop a prior episode
            # (#486-G5): neither re-surface nor clear the persisted entry.
            continue
        # ok is False -> genuine drift. #1032: NEVER ping the owner. Persist the
        # episode (so `conformance_status_row` shows the DRIFT row) + emit a
        # JOURNAL `SURFACED` escalation, deduped exactly as the old owner ping was
        # (new / changed-sig / past-reping surfaces; an unchanged drift within
        # `reping` keeps the episode with a refreshed detail, no new line).
        sig = _sig_for(dim, facts)
        prev = seen.get(dim) or {}
        same = (prev.get("sig") == sig)
        surfaced = prev.get("surfaced_ts")
        if dry_run:
            logs.append("conformance %s [%s] WOULD-SURFACE -- %s" % (host, dim, detail))
            continue
        if same and surfaced is not None and (now - float(surfaced)) < reping:
            # unchanged drift within the re-surface window: keep the episode (so
            # `status` still shows the DRIFT row) with a refreshed detail; no new
            # journal escalation line.
            seen[dim] = {"sig": sig, "surfaced_ts": surfaced, "detail": detail}
            persist()
            continue
        seen[dim] = {"sig": sig, "surfaced_ts": now, "detail": detail}
        persist()      # persist the episode BEFORE the journal line (#172-F3 order)
        logs.append("conformance %s [%s] SURFACED -- %s" % (host, dim, detail))
    return logs


def conformance_status_row(state):
    """A single ``cmd_status`` row summarising this box's last conformance sweep,
    read from the persisted ``state["conformance"]`` snapshot the daily job keeps
    (#1032). The SUPERVISOR-facing surface that replaces the removed owner Discord
    ping: any drifting dimension → ``conformance: DRIFT — <dim>: <detail>; ...``;
    a completed sweep with no drift → ``conformance: OK``; a box whose sweep never
    ran → ``conformance: (not yet checked)``. Pure — no I/O, safe on any dict."""
    if not isinstance(state, dict):
        state = {}
    # #1047: the report-only root-guard fact rides the row as a suffix (present
    # only once a sweep has recorded it); it is a per-box FACT, never a DRIFT.
    # review 🔵: a DISTINCT ` · ` delimiter (not the `; ` the DRIFT dims use) so
    # this aside never reads as a drift item on the DRIFT branch.
    rg = state.get("root_guard_provisioned")
    suffix = ("" if rg is None else
              " · root disk-guard: %s" % ("provisioned" if rg else "not provisioned"))
    # #1015: the report-only bashrc-drift count rides the row as a ` · ` suffix,
    # but ONLY when there IS drift (N>0). Unlike the root-guard fact (whose two
    # states are both informative), a count of 0 is the healthy norm and would be
    # pure noise on every clean box's row.
    bd = state.get("bashrc_drift")
    if isinstance(bd, int) and bd > 0:
        suffix += " · bashrc-drift: %d" % bd
    # #1115 Slice C: the report-only public-URL-channel fact rides the row as a
    # ` · ` suffix, but ONLY when it is NOT `ok` (a healthy public channel is the
    # norm and would be pure noise on every clean box's row — same rationale as
    # the bashrc count above).
    puc = state.get("public_url_channel")
    if isinstance(puc, str) and puc != "ok":
        suffix += " · public-url-channel: %s" % puc
    episodes = state.get("conformance") or {}
    if episodes:
        parts = []
        for dim in sorted(episodes):
            ep = episodes.get(dim) or {}
            detail = ep.get("detail") if isinstance(ep, dict) else None
            parts.append("%s: %s" % (dim, detail or "drift"))
        return "conformance: DRIFT — " + "; ".join(parts) + suffix
    if state.get("conformance_last_check"):
        return "conformance: OK" + suffix
    return "conformance: (not yet checked)" + suffix
