"""erp-test box heartbeat — watchdog job 51 (#959).

odoo-erp#6642 made every stream's erp-test box self-expiring (an `expires_at`
label + an in-box self-destruct, 24h base TTL) and shipped the heartbeat side on
odoo-erp `main` (`scripts/dev-box-heartbeat-remote.sh <stream>` → SSH-execs
`dev-box-heartbeat.sh` ON the box → the gk destruct relay extends `expires_at`;
the per-box destruct token never leaves the box). The CALLER — the airuleset
side — was this ticket. Nothing else extends a box's TTL, so a box whose stream's
Claude is off must die on its own; a box whose Claude is alive must be kept alive.

The subdev box runs ONE `api-watchdog` per stream account (`run_once` in each
user's systemd user timer), so the natural owner of "is THIS stream's Claude
alive" is that account's own watchdog. This is Approach 1 from the design: a
gated, cadence-bound leaf job in each stream account's watchdog (the Job 47
`healthz_probe` pattern — declaration-gated, `_sweep_due` cadence, injectable
seams, journal-first, no new notify channel). NO new daemon, NO root, NO Hetzner
token on subdev.

Contract with odoo-erp's wrapper (`dev-box-heartbeat-remote.sh`, both headers):
exit **0** = heartbeat extended / box unreachable no-op / box has no config;
**2** = relay/network error (retry next tick); **3** = token/auth refusal (alarm
immediately). ONE stdout journal line (`heartbeat stream=<s> box=erp-test-<s>
relay=OK expires_at=…` or `noop reason=…`), which this job passes through 1:1 —
it parses nothing but the exit code.

`run_erp_heartbeat` (this module's SSOT):
  - **Gate:** runs only on a `shared-stream` box class (``default_box_class``)
    for a reduced-authority stream account (``_box_authority`` != ``full``); any
    other box → a silent no-op skip line, no call.
  - **Liveness:** at least one `claude` process owned by THIS uid; none → a skip
    line, no call (the box then expires on its own — the point of the design).
  - **Script resolution:** the stream's odoo-erp checkout is resolved from the
    live claude processes' cwd (`git rev-parse --show-toplevel` → origin slug
    ``zbynekdrlik/odoo-erp``), else a fallback scan of ``~/devel/odoo/*/``; not
    found → a skip line, never a guess.
  - **Call:** `bash <script> <user>` with a 60s timeout; the wrapper's one stdout
    line is journalled verbatim. rc 0 → ok; 2 → retry (no alarm); 3 → ALARM
    auth-refused (journalled every tick); other rc / timeout → error rc=N
    (retry). `state["erp_heartbeat"] = {rc, ts, line}` records the last outcome
    for the conformance/status surfaces. No owner Discord ping (analyze-not-ping
    #693/#704 — the gk-side relay owns box-level alerting; this job's evidence is
    the journal + the state record).
  - `dry_run` → journals `would call …`, calls nothing, persists nothing.

The run_once `_add` gate owns the 10-min cadence stamp
(``state["erp_heartbeat_last_ts"]`` via ``_sweep_due``) and the
``erp_heartbeat_enabled`` flag (default off; cmd_watchdog turns it on — the leaf
is the real production gate, so the flag may be passed unconditionally). Every
process/git/subprocess access is an injectable seam, so a unit test never spawns
a real ssh.
"""
import glob
import os
import subprocess

# 10-min cadence — the run_once `_add` gate stamps state["erp_heartbeat_last_ts"]
# via `_sweep_due`, so the wrapper call fires at most once per interval, never on
# the 60s poll tick (mirrors job 47).
ERP_HEARTBEAT_INTERVAL_S = 600

# Wrapper call timeout. The odoo-erp wrapper uses ssh ConnectTimeout=10, so a
# reachable box replies fast and a dead box fails ~10s (rc 255 → the wrapper's
# own exit 0 + `noop reason=box-unreachable-…`); 60s is generous headroom for a
# slow-but-alive box.
ERP_HEARTBEAT_TIMEOUT_S = 60

ODOO_ERP_SLUG = "zbynekdrlik/odoo-erp"
SCRIPT_RELPATH = "scripts/dev-box-heartbeat-remote.sh"
# Fallback checkout scan root (expanded at call time).
_ODOO_CHECKOUT_GLOB = "~/devel/odoo/*/"

SHARED_STREAM = "shared-stream"
FULL_AUTHORITY = "full"

# rc sentinel for a call that timed out (distinct from the wrapper's own 0/2/3).
_TIMEOUT_RC = -1


# --------------------------------------------------------------------------- #
# Default seams (deferred imports avoid a watchdog package-load cycle; every one
# is fail-safe — it never raises, so the gate reads a safe value on any error).
# --------------------------------------------------------------------------- #
def _default_run(argv, timeout=8):
    from watchdog.tmux_io import _default_run as _dr
    return _dr(argv, timeout=timeout)


def _default_box_class():
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except Exception:
        return None


def _default_authority():
    try:
        import watchdog
        return watchdog._box_authority()
    except Exception:
        # Unknown authority on a shared-stream box: treat as reduced (a
        # shared-stream box only ever hosts stream accounts) — the box-class
        # gate is the dominant one.
        return None


def _default_user():
    try:
        import airuleset
        return airuleset._current_user()
    except Exception:
        import getpass
        try:
            return getpass.getuser()
        except Exception:
            return "?"


def _cmdline_is_claude_cli(cmdline):
    """True when a process cmdline IS the Claude CLI, in EITHER fleet launch
    shape — the direct binary / bin-symlink (`claude`, `node .../claude`) OR the
    npm shape (`node .../claude-code/cli.js`). Mirrors
    `priority_policy._looks_like_claude_cli`'s documented two-shape recognition.
    A bare `pgrep -x claude` (comm == "claude") MISSES the npm-launched shape
    (comm == "node"), which would read a live stream as dead and let its box
    wrongly expire — the exact failure #959 exists to prevent."""
    toks = (cmdline or "").split()
    if not toks:
        return False
    if os.path.basename(toks[0]) == "claude":
        return True
    if os.path.basename(toks[0]) in ("node", "nodejs"):
        for t in toks[1:]:
            if os.path.basename(t) == "claude" or t.endswith("claude-code/cli.js") \
                    or "/claude-code/" in t:
                return True
    return False


def _default_live_claude_cwds(run):
    """cwds of live Claude CLI processes owned by THIS uid. Matches BOTH fleet
    launch shapes (`pgrep -u <uid> -f claude` → a per-pid cmdline signature
    check via `_cmdline_is_claude_cli`, so the npm `node .../claude-code/cli.js`
    shape is not missed the way a bare `-x claude` would), then
    `os.readlink(/proc/<pid>/cwd)`. Empty on none / any error (fail-safe: no
    proof of life → no heartbeat). The `-f claude` over-match (any cmdline
    mentioning "claude", e.g. an editor on a claude-named path) is filtered out
    by the signature check — only a real CLI process contributes a cwd."""
    try:
        out = run(["pgrep", "-u", str(os.getuid()), "-f", "claude"]) or ""
    except Exception:
        return []
    cwds = []
    for pid in out.split():
        pid = pid.strip()
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not _cmdline_is_claude_cli(cmdline):
            continue
        try:
            cwds.append(os.readlink("/proc/%s/cwd" % pid))
        except OSError:
            continue
    return cwds


def _slug_matches(origin, slug):
    """True when a git origin URL (ssh or https, with/without `.git`) names
    `<owner>/<repo>` == `slug`."""
    if not origin:
        return False
    o = origin.strip()
    if o.endswith(".git"):
        o = o[:-4]
    o = o.replace(":", "/")
    parts = [p for p in o.split("/") if p]
    if len(parts) < 2:
        return False
    return "%s/%s" % (parts[-2], parts[-1]) == slug


def _script_at(top, run):
    """`<top>/scripts/dev-box-heartbeat-remote.sh` when `top`'s origin is
    odoo-erp AND the file exists; else None."""
    if not top:
        return None
    origin = (run(["git", "-C", top, "remote", "get-url", "origin"]) or "").strip()
    if not _slug_matches(origin, ODOO_ERP_SLUG):
        return None
    cand = os.path.join(top, SCRIPT_RELPATH)
    return cand if os.path.isfile(cand) else None


def _default_find_script(cwds, run):
    """Resolve the wrapper path from a live-claude cwd whose git toplevel is an
    odoo-erp checkout; else a fallback scan of ``~/devel/odoo/*/``. Returns the
    exists-checked script path or None — never a guess."""
    seen = set()
    for cwd in cwds:
        if not cwd or cwd in seen:
            continue
        seen.add(cwd)
        top = (run(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
               or "").strip()
        if not top or top in seen:
            continue
        seen.add(top)
        found = _script_at(top, run)
        if found:
            return found
    for d in sorted(glob.glob(os.path.expanduser(_ODOO_CHECKOUT_GLOB))):
        d = d.rstrip("/")
        if d in seen:
            continue
        seen.add(d)
        found = _script_at(d, run)
        if found:
            return found
    return None


def _default_call(script, user, timeout):
    """`bash <script> <user>`; returns (rc, first_stdout_line). Raises
    subprocess.TimeoutExpired on timeout (the caller maps it to _TIMEOUT_RC).
    Timed + recorded into the per-sweep subprocess counter (#1055-P2
    "count EVERY runner") — this bash→ssh wrapper call is the heavy runner of
    this job, so a `max_subprocess` budget must see it. The record runs in a
    `finally` so a TimeoutExpired is still counted (mirrors tmux_io._default_run)."""
    import time as _time
    from watchdog.subprocess_budget import record_subprocess
    t0 = _time.monotonic()
    try:
        r = subprocess.run(["bash", script, user],
                           capture_output=True, text=True, timeout=timeout)
    finally:
        record_subprocess("bash", _time.monotonic() - t0)
    out = (r.stdout or "").strip()
    line = out.splitlines()[0] if out else ""
    return r.returncode, line


# --------------------------------------------------------------------------- #
# The job.
# --------------------------------------------------------------------------- #
def _record(out, state, now, rc, line, user):
    """Journal the wrapper's line 1:1, then the rc verdict, and record the last
    outcome in state (for the conformance/status surfaces)."""
    if line:
        out.append("erp-heartbeat: %s" % line)
    if rc == 0:
        out.append("erp-heartbeat: ok rc=0 for %s" % user)
    elif rc == 2:
        out.append("erp-heartbeat: retry rc=2 (relay/network) for %s" % user)
    elif rc == 3:
        out.append("erp-heartbeat: ALARM auth-refused rc=3 for %s" % user)
    elif rc == _TIMEOUT_RC:
        out.append("erp-heartbeat: error timeout for %s" % user)
    else:
        out.append("erp-heartbeat: error rc=%s for %s" % (rc, user))
    state["erp_heartbeat"] = {"rc": rc, "ts": now, "line": line}


def run_erp_heartbeat(now, state, *, run=None, dry_run=False,
                      box_class_fn=None, authority_fn=None, user_fn=None,
                      live_claude_fn=None, find_script_fn=None, call_fn=None,
                      logs=None):
    """See the module docstring (the SSOT). Returns a list of journal lines."""
    out = logs if logs is not None else []
    run = run or _default_run
    box_class_fn = box_class_fn or _default_box_class
    authority_fn = authority_fn or _default_authority
    user_fn = user_fn or _default_user
    if live_claude_fn is None:
        live_claude_fn = lambda: _default_live_claude_cwds(run)  # noqa: E731
    if find_script_fn is None:
        find_script_fn = lambda cwds: _default_find_script(cwds, run)  # noqa: E731
    call_fn = call_fn or _default_call

    # Gate 1: box class — a silent no-op off a shared-stream box.
    if box_class_fn() != SHARED_STREAM:
        out.append("erp-heartbeat: skip (not a shared-stream box)")
        return out
    # Gate 2: authority — never heartbeat from a full-authority account.
    if authority_fn() == FULL_AUTHORITY:
        out.append("erp-heartbeat: skip (full-authority box)")
        return out

    user = user_fn()
    cwds = live_claude_fn()
    if not cwds:
        out.append("erp-heartbeat: skip (no live claude for %s)" % user)
        return out

    script = find_script_fn(cwds)
    if not script:
        out.append("erp-heartbeat: skip (no odoo-erp checkout / script)")
        return out

    if dry_run:
        out.append("erp-heartbeat: would call %s %s" % (script, user))
        return out

    try:
        rc, line = call_fn(script, user, ERP_HEARTBEAT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        rc, line = _TIMEOUT_RC, ""
    _record(out, state, now, rc, line, user)
    return out
