"""airuleset remote deploy / push / Soniox-provisioning — cluster L-E (#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433 cluster L,
binding sub-split decision 1 — same verbatim-move + facade-re-export pattern as
the earlier H/I/J/K/L1..L4 CLI leaves). airuleset.py keeps a single
``from cli_remote import (...)`` re-export at the old definition site, so
SUBCOMMANDS wiring, main()'s argparse, cmd_watchdog's fleet fetch, and every
test's ``airuleset.cmd_push(...)`` / ``airuleset._ssh_*`` /
``inspect.getsource(airuleset.cmd_push)`` reference all keep working unchanged.

This region PUSHES to GitHub and DEPLOYS to every managed remote box: the
fail-closed ruff+test gate, the per-remote ssh install (with retry/backoff +
ControlMaster multiplexing), the /home-audit that flags an unregistered stream
account, and the Soniox meeting-analysis key delivery to subdev stream accounts.

Deliberately SELF-CONTAINED: stdlib only at module level (``os``, ``re``,
``shutil``, ``sys``, ``Path``). ``subprocess`` / ``time`` / ``shlex`` /
``tempfile`` are imported LOCALLY inside the functions that use them, verbatim —
the same never-top-level-import-subprocess idiom airuleset.py itself follows.
``REPO_DIR`` is a 1-line dup (identical value; never test-patched — K/L-B
precedent). NO module-level ``import airuleset``: the resident/promoted
couplings are DEFERRED to call time in exactly 5 functions —
``_soniox_key_line`` (airuleset.read_file_safe), ``provision_subdev_soniox_key``
(airuleset.REMOTE_HOSTS + airuleset.AUTHORITY_BY_USER), ``_shared_remote_host_ips``
+ ``unregistered_home_accounts`` (airuleset.REMOTE_HOSTS), and ``cmd_push``
(airuleset.cmd_install + airuleset.REMOTE_HOSTS). REMOTE_HOSTS/AUTHORITY_BY_USER
are read via the ``airuleset`` facade (NOT ``cli_fleet`` directly) precisely so
the many ``patch.object(airuleset, "REMOTE_HOSTS"/"AUTHORITY_BY_USER", ...)``
tests stay live (the L2 no-op-mock lesson). In CLI ``__main__`` mode a deferred
``import airuleset`` triggers a second, bounded import of airuleset as module
``airuleset`` (the documented J/cli_burn cost); in tests it is a sys.modules
cache hit.
"""

import os
import re
import shutil
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


# Per-remote SSH deploy timeout (#263): `cmd_install()` now includes best-
# effort network-heavy provisioning that can legitimately take well over the
# old 60s bound on a first-time run — the sum of the INNER timeouts a single
# install can burn through: check_runtime_deps()'s `apt-get install` (300s
# PER missing package), ensure_claude_cli_installed()'s curl-install (180s),
# and ensure_playwright_browsers()'s npx install (300s) — up to ~780s of
# best-effort network work alone before any of the ordinary install steps.
# An adversarial review of the first version of this fix caught it set at
# 300s, LESS than that inner sum: on the exact scenario #263 exists for (a
# fresh subdev account missing both claude and the Playwright cache), the
# outer ssh call would hit ITS OWN bound first, sshd would SIGHUP the
# remote's still-running `python3 airuleset.py install` mid-run, and every
# step after the point it was killed (managed plugins, file-drop, the
# api-watchdog timer) would simply never execute on that remote — silently,
# since the timeout branch below reports it as a plain timeout, not as
# "install ran partway and was killed". A remote whose ssh call genuinely
# can't complete inside this window still gets caught below
# (subprocess.TimeoutExpired), never crashes the whole push loop.
#
# Re-sized (adversarial review, plugin-marketplace fix, 2026-08-06):
# ensure_marketplace_registered() adds up to TWO more 150s calls on a fresh
# account (caveman's marketplace + the shared, market_ok-cached
# claude-plugins-official one covering both superpowers and playwright's
# own installs). Worst-case inner sum in sequence: apt-get(300) + claude
# CLI curl(180) + caveman marketplace-add(150) + caveman install(120) +
# managed-plugins marketplace-add(150) + superpowers install(180) +
# playwright install(180) + npx playwright browsers(300) = 1560s. 1800s
# gives real headroom over that (see
# tests/test_dev_env_provisioning.py::TestPushRemoteDeployTimeoutAndStderr
# for the exact sum this must stay above).
REMOTE_DEPLOY_TIMEOUT_S = 1800


# --- #275: deliver the meeting-analysis Soniox key to every subdev stream
# account, sourced from dev1's own local voiceagent checkout ------------------
SONIOX_KEY_SOURCE = Path.home() / "devel" / "voiceagent" / ".env"


def _soniox_key_line(source: Path = None):
    """Extract ONLY the `SONIOX_API_KEY=...` line out of dev1's voiceagent
    `.env` -- that file carries MANY other unrelated secrets (Discord
    tokens, etc.), so the whole file is never read out, only this one line
    ever leaves this process. Returns None when the source is missing or
    carries no such key -- never raises, so a caller can treat "not found"
    uniformly regardless of the reason."""
    src = source if source is not None else SONIOX_KEY_SOURCE
    if not src.is_file():
        return None
    import airuleset  # #433 L-E: read_file_safe stays resident in airuleset.py
    for line in airuleset.read_file_safe(src).splitlines():
        line = line.rstrip("\r\n")
        if line.startswith("SONIOX_API_KEY="):
            return line
    return None


def provision_subdev_soniox_key(hosts=None, run=None, source: Path = None,
                                 skip_names=None, control_opts=None):
    """Deliver `~/.soniox.env` (the meeting-analysis skill's canonical,
    UN-guarded Soniox key path -- see skills/meeting-analysis/SKILL.md and
    hooks/block-vault-store-read.sh) to every subdev stream account (#275)
    PLUS any host explicitly flagged `"soniox": True` -- a managed NON-subdev
    box that still runs meeting-analysis, e.g. newlevel@dev2 (#451).

    `control_opts` (#358): the SAME `_ssh_multiplex_opts()` list
    `cmd_push()`'s own deploy loop built for this run, so an account
    contacted here for a SECOND time (already dialed once by the deploy
    loop above) reuses that account's already-authenticated ssh master
    connection instead of paying for a fresh TCP+SSH handshake. Defaults
    to `[]` -- a caller with no multiplexing set up (or a direct test
    call) gets plain, unmultiplexed ssh, byte-identical to before this
    parameter existed.

    The value is piped to each remote via `input=` on the ssh subprocess
    call -- never embedded in argv, never printed by this process -- and
    the source read is scoped to the ONE matching line (`_soniox_key_line`),
    never the whole voiceagent `.env`. Targets are filtered FIRST -- a
    subdev stream account (its `user` in `AUTHORITY_BY_USER`) OR a host
    explicitly flagged `"soniox": True` (#451) -- before the source is ever
    read, so a host list with no such target in it (e.g. gatekeeper-only)
    never touches the filesystem at all.

    A missing source on this box is a LOUD stderr failure -- every subdev
    target is reported failed, exactly like a real per-host delivery
    failure -- never a silent skip (the gatekeeper Discord `.env` lesson:
    a silent provisioning gap is a dead feature). Returns the list of
    `(remote_name, reason)` failures, mirroring `cmd_push()`'s own
    `failed` accumulator shape.

    `skip_names` (#341): a set of `remote["name"]` values ALREADY known to
    have failed ssh auth this run (`cmd_push()`'s own deploy loop passes its
    own `auth_failed` set here) -- each is skipped with a loud stderr line
    and a `failed` entry, never given a fresh ssh connection. A second
    connection attempt against an account the deploy loop already proved is
    unprovisioned/unreachable is exactly what compounded the fail2ban risk
    this parameter exists to remove; it never touches an account that has
    not already, independently, failed auth THIS run."""
    import subprocess
    import time
    run = run or subprocess.run
    control_opts = list(control_opts or [])
    import airuleset  # #433 L-E: REMOTE_HOSTS/AUTHORITY_BY_USER promoted to cli_fleet, read via facade
    targets = [h for h in (hosts if hosts is not None else airuleset.REMOTE_HOSTS)
               if h.get("user") in airuleset.AUTHORITY_BY_USER or h.get("soniox")]
    if not targets:
        return []

    failed = []
    skip = set(skip_names or ())
    if skip:
        deliverable = []
        for h in targets:
            if h["name"] in skip:
                print("  ⚠ soniox key delivery to %s SKIPPED — its deploy "
                      "leg already failed auth this run (see the FAILED "
                      "line above); not opening a second ssh connection "
                      "against a known-unprovisioned/unreachable account."
                      % h["name"], file=sys.stderr)
                failed.append((h["name"], "skipped-known-auth-failure"))
            else:
                deliverable.append(h)
        targets = deliverable
        if not targets:
            return failed

    line = _soniox_key_line(source)
    if line is None:
        src = source if source is not None else SONIOX_KEY_SOURCE
        print("  ⚠ SONIOX KEY SOURCE MISSING (%s) — skipping ~/.soniox.env "
              "delivery to %d subdev stream account(s). Run `airuleset.py "
              "push` from dev1 (the maintainer box) instead."
              % (src, len(targets)), file=sys.stderr)
        return failed + [(h["name"], "soniox-key-source-missing")
                          for h in targets]

    for remote in targets:
        identity = remote.get("identity")
        if identity:
            # #341: BatchMode=yes -- a failed pubkey attempt (an
            # unprovisioned/misconfigured account) must fail IMMEDIATELY
            # rather than falling through to an interactive password/
            # keyboard-interactive attempt, which is what turned a single
            # "Permission denied" connection into several distinct
            # auth-failure log lines against subdev.
            ssh_prefix = ["ssh", "-i", os.path.expanduser(identity),
                          "-o", "StrictHostKeyChecking=no",
                          "-o", "BatchMode=yes"]
        else:
            # #341: NumberOfPasswordPrompts=1 -- caps a wrong/unprovisioned
            # password attempt to ONE try (openssh's own default is 3,
            # and sshpass happily re-supplies the same password for every
            # re-prompt), so a single sshpass call can burn at most one
            # fail2ban-countable strike instead of up to three.
            ssh_prefix = ["sshpass", "-p", "newlevel", "ssh",
                          "-o", "StrictHostKeyChecking=no",
                          "-o", "NumberOfPasswordPrompts=1"]
        argv = ssh_prefix + control_opts + [
            f"{remote['user']}@{remote['host']}",
            "umask 077; cat > ~/.soniox.env && "
            "chmod 600 ~/.soniox.env"]
        # #358 adversarial-review F2 (MAJOR): this call used to be a
        # single, un-retried attempt -- a connection-closed/reset drop
        # here permanently lost that account's soniox key, the exact hole
        # cmd_push()'s own deploy-loop retry exists to close, one
        # function over. Same bounded target-level retry shape as that
        # loop: only a genuine ssh connection-establishment failure
        # (never an ordinary write failure) gets retried, up to
        # SSH_RETRY_MAX_ATTEMPTS total attempts, with the SAME
        # env-tunable backoff.
        r = None
        exc = None
        for attempt in range(1, SSH_RETRY_MAX_ATTEMPTS + 1):
            exc = None
            try:
                r = run(argv, input=line + "\n", capture_output=True,
                        text=True, timeout=20)
            except Exception as e:
                exc = e
                break
            if r.returncode == 0:
                break
            if (attempt >= SSH_RETRY_MAX_ATTEMPTS
                    or not _is_ssh_transient_failure(r.returncode,
                                                      r.stderr)):
                break
            backoff = _ssh_retry_backoff_s()
            print(f"  transient ssh failure delivering soniox key to "
                  f"{remote['name']} (attempt {attempt}/"
                  f"{SSH_RETRY_MAX_ATTEMPTS}) — retrying in {backoff}s: "
                  f"{r.stderr.strip()[:200]}")
            time.sleep(backoff)
        if exc is not None:
            print("  ⚠ soniox key delivery to %s failed: %s"
                  % (remote["name"], exc), file=sys.stderr)
            failed.append((remote["name"], repr(exc)))
            continue
        if r.returncode != 0:
            print("  ⚠ soniox key delivery to %s failed (rc=%d): %s"
                  % (remote["name"], r.returncode,
                     (r.stderr or "").strip()[:200]), file=sys.stderr)
            failed.append((remote["name"], "rc=%d" % r.returncode))
        else:
            print("    soniox key: delivered to %s" % remote["name"])
    return failed


# #341 adversarial-review F1 (MAJOR, TRIGGERED): a bare `"Permission denied"
# in stderr` substring check misfires on a REMOTE COMMAND's own stderr —
# `subprocess.run(..., capture_output=True)` on an ssh call captures
# whatever the remote process itself prints too (ssh forwards it), and a
# real `git pull` hitting a root-owned file under `.git`, or an
# `airuleset.py install` traceback, can both legitimately contain that
# literal substring with ssh auth completely intact. ssh's OWN client-side
# auth-exhaustion message is structurally distinct on two properties no
# remote process has any reason to reproduce: it always exits 255, and it
# is always literally prefixed "<user>@<host>: " (openssh's
# `permission_denied()`, unchanged for decades). Requiring BOTH closes the
# false-positive without weakening detection of a genuine auth failure.
_SSH_AUTH_DENIED_RX = re.compile(r"(?m)^\S+@\S+: Permission denied")


def _is_ssh_auth_failure(returncode, stderr):
    """True only for ssh's OWN auth-exhaustion failure, never a remote
    COMMAND's stderr that merely happens to contain the same words."""
    return returncode == 255 and bool(_SSH_AUTH_DENIED_RX.search(stderr or ""))


# --- #358: ssh connection reuse + target-level retry with backoff ----------
# Live incident (2026-08-10, this repo's own gatekeeper/subdev push targets):
# a burst of rapid independent ssh handshakes within one push wave (11 of
# REMOTE_HOSTS' 13 entries share the subdev VPS; provision_subdev_soniox_key
# below re-dials several of the SAME accounts a second time in the same run)
# tripped a connection drop against gk with NO retry at all -- one flaky
# target permanently lost the whole wave's deploy to it.
#
# Root cause REVISED after live diagnostics on gk itself (issue comment
# 5245989172, supervisor session via the dev2 jump host): this is NOT a
# per-source ban (fail2ban's own ban list never contained dev1's address)
# and NOT a targeted fail2ban/MaxStartups window against ONE source -- gk's
# sshd has `MaxStartups 10:30:100` with `PerSourceMaxStartups none`, and an
# ongoing internet-wide bruteforce flood (126k+ failed attempts, 60+
# concurrently in flight at diagnosis time) keeps that GLOBAL unauthenticated-
# connection pool saturated, so sshd RANDOMLY drops legitimate connections
# too (the tailscale path shares the identical pool). The observed
# "recovery after 20-40 min" was the bruteforce wave itself ebbing, not a
# ban expiring. This makes a SHORT in-wave retry the directly-correct fix,
# not merely "worth trying anyway": each retry is a genuinely independent
# roll against the SAME saturated pool, with real per-attempt odds of
# landing while the pool has briefly cleared -- never a hopeless repeat
# against a fixed-duration ban.
#
# Same rc==255 + ssh-client-only-message discriminator shape as
# `_is_ssh_auth_failure` above, on purpose: it is the same false-positive
# class (a REMOTE command's own stderr must never be misread as ssh's OWN
# connection-level failure).
#
# #358 adversarial-review F5 (live-reproduced with a network-free
# ProxyCommand trick against the real /usr/bin/ssh binary): two further
# real client-only shapes were originally MISSED --
# `ssh_dispatch_run_fatal: Connection to <host> port <port>: Broken pipe`
# (a proxy that sends a banner then drops -- `ssh_dispatch_run_fatal:` is
# the same class of internal log tag as the two `*_exchange_identification:`
# prefixes, confirmed via `strings` on the real binary) and a bare
# `Connection reset by <host> port <port>` line (confirmed via `strings`
# to be `sshpkt_vfatal`'s own ECONNRESET message, the same shape as the
# already-covered "closed by" line but for the reset case). Both are now
# covered. Honest residual: `ssh_dispatch_run_fatal:` is not exclusively a
# PRE-auth message -- unlike the two `*_exchange_identification:` prefixes
# (which only ever fire before any remote shell exists), it can in
# principle also fire on a MID-session drop. That is still safe here: the
# retried remote command (`git pull --ff-only && ... install`, or the
# soniox `cat > ~/.soniox.env`) is idempotent, so a retry after a
# mid-session drop just repeats a safe operation, never double-applies one.
_SSH_TRANSIENT_RX = re.compile(
    r"(?m)^(?:kex_exchange_identification|ssh_exchange_identification|"
    r"ssh_dispatch_run_fatal):"
    r"|^Connection (?:closed|reset) by \S+ port \d+\s*$"
)

# Bounded: at most this many total attempts (initial + retries) per target.
# 3 (2 retries) -- "a few attempts", matching the revised diagnosis: since a
# drop is a random per-connection event against a saturated pool (not a
# fixed-duration ban), a second retry has real, undiminished odds of success,
# unlike retrying against an actual ban where every attempt is equally
# futile. Kept as a plain constant, not env-tunable -- only the backoff
# between attempts needs to be (see `_ssh_retry_backoff_s`'s own docstring).
SSH_RETRY_MAX_ATTEMPTS = 3

# ControlPersist window for a target's shared ssh master connection.
#
# #358 adversarial-review F1 (MAJOR, measured against the real
# REMOTE_HOSTS list): 60s was sized as "a few seconds to low tens of
# seconds" between an account's deploy call and its LATER soniox-key call
# -- but those two calls are NOT adjacent. montalu@subdev sits at deploy-
# loop index 2 and is also soniox-phase's FIRST target, so its own real
# gap is TEN complete `git pull --ff-only && python3 airuleset.py install`
# runs for every OTHER account still queued in the deploy loop -- a
# budget this file's own REMOTE_DEPLOY_TIMEOUT_S sizes at up to 1560s
# EACH for a slow first-time install (apt-get + claude-CLI curl +
# caveman/managed-plugin marketplace installs + Playwright browsers).
# 60s expired long before that gap in the realistic slow-account case,
# making the multiplexing structurally unable to help the account it was
# sized around. 1800s matches REMOTE_DEPLOY_TIMEOUT_S itself -- a natural,
# already-justified ceiling -- and costs nothing extra: the sockets live
# under a 0700 per-run temp dir the `finally` block deletes regardless of
# how long the underlying master process itself takes to notice and exit,
# so a longer window only ever leaves an IDLE, UNREACHABLE master
# process a little longer, never anything a caller can observe.
SSH_CONTROL_PERSIST_S = 1800


def _is_ssh_transient_failure(returncode, stderr):
    """True only for ssh's OWN connection-establishment failure -- a
    MaxStartups-pool-exhaustion drop mid-handshake (a random per-connection
    event, never a per-source ban -- see the #358 root-cause comment above
    this function). Matches a line starting `kex_exchange_identification:` /
    `ssh_exchange_identification:` / `ssh_dispatch_run_fatal:` (openssh's
    own internal log tags -- a remote command's own stderr structurally
    cannot reproduce them) or a bare `Connection closed|reset by <host>
    port <port>` line with nothing else on it. Requiring rc==255 (mirrors
    `_is_ssh_auth_failure`'s own discriminator) excludes an ordinary
    failed remote command (a bad `git pull`, a crashing `install`), which
    never exits via ssh's own 255 connection-failure path.

    NOT claimed: that every matched message can only ever appear BEFORE a
    remote shell exists (`ssh_dispatch_run_fatal:` is a documented
    exception -- see the classifier regex's own comment) -- only that
    every matched message is genuinely ssh-client-internal, never text a
    remote command could produce on its own."""
    return returncode == 255 and bool(_SSH_TRANSIENT_RX.search(stderr or ""))


def _ssh_retry_backoff_s():
    """`AIRULESET_SSH_RETRY_BACKOFF_S` override for the target-level retry
    delay, clamped to [1, 300]. Unclamped, a misconfigured 0/negative
    value would defeat the whole backoff (the same class of gap #172's own
    `AIRULESET_SWEEP_BUDGET_S` fix closed elsewhere in this file), and an
    absurdly large one would block the WHOLE wave behind one flaky target
    for minutes it will never actually need.

    Default 60s, "order of seconds to ~2 min" per the REVISED diagnosis
    (issue comment 5245989172): the drop is a RANDOM per-connection event
    against gk's globally-saturated MaxStartups unauthenticated-connection
    pool (internet-wide bruteforce noise, not a per-source ban), so each
    retry is a genuinely independent roll with real odds of landing once
    the pool has briefly cleared -- unlike retrying against a real ban,
    where a longer wait would be needed and every short retry would be
    equally futile. Combined with SSH_RETRY_MAX_ATTEMPTS=3 (2 retries),
    the worst-case added time for one exhausted target is ~2 backoffs of
    the default (about 2 min) -- still short enough that one flaky target
    never meaningfully delays the whole semi-foreground wave, and a
    genuinely exhausted target is reported loudly with the exact manual
    re-run command instead of silently vanishing from it.

    Stated explicitly for the WHOLE wave, not just one target (#358
    adversarial-review F7): retries do NOT meaningfully worsen the pool
    saturation (spread >=60s apart per target, a tiny fraction of the
    126k+ ambient bruteforce attempts already hitting gk), but the wave's
    own worst-case wall clock is real and grows with REMOTE_HOSTS' size --
    at today's 13 entries, EVERY target hitting a transient failure once
    adds up to `13 * 2 * 60s` = ~26 minutes at the default backoff, or up
    to `13 * 2 * 300s` = ~130 minutes at the `[1, 300]` clamp ceiling if an
    operator raises the override. This is the honest worst case, not the
    expected one -- the revised root cause means most retries either land
    on their first extra attempt or fail fast, not spend their whole
    backoff every time."""
    raw = os.environ.get("AIRULESET_SSH_RETRY_BACKOFF_S", "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = 60
    return max(1, min(300, val))


def _ssh_control_dir_for_push():
    """One short-lived directory for this push run's ssh ControlMaster
    sockets -- multiplexes REPEATED connections to the SAME target within
    one push (the deploy call, then the LATER soniox-key call, #275) onto
    a single already-authenticated master, so the wave opens fewer
    distinct TCP+SSH handshakes against a target's sshd rather than more.
    A UNIX socket path is capped at roughly 104-108 bytes on Linux, so the
    prefix here stays short and ssh's own `%C` (a fixed-length per-target
    connection hash) supplies the per-target uniqueness -- one shared
    directory safely serves every REMOTE_HOSTS entry in the same push; a
    DIFFERENT target (different user/host/port) can never collide on, or
    reuse, another target's socket. Returns None (never raises) when the
    temp dir can't be created, so a caller degrades to plain unmultiplexed
    ssh calls rather than failing the whole push over a missing sandbox
    capability."""
    import tempfile
    try:
        return tempfile.mkdtemp(prefix="arsshcm-")
    except OSError:
        return None


def _ssh_multiplex_opts(control_dir):
    """The `-o ControlMaster=... -o ControlPersist=... -o ControlPath=...`
    triple built from a `_ssh_control_dir_for_push()` result. Returns []
    (plain, unmultiplexed ssh -- never a hard failure) when `control_dir`
    is falsy, e.g. because the temp dir itself could not be created."""
    if not control_dir:
        return []
    return [
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=%ds" % SSH_CONTROL_PERSIST_S,
        "-o", "ControlPath=%s/%%C" % control_dir,
    ]


def _redacted_ssh_cmd(cmd):
    """The same argv `ssh_cmd`/`ssh_prefix` list built for a deploy or
    soniox-key call, with any `sshpass -p <password>` password argument
    replaced -- this is printed into the push LOG as a manual single-
    target retry hint once retries are exhausted (#358), and the shared
    subdev password must never land in a log file (security-basics.md).
    Never mutates the input list."""
    out = list(cmd)
    for i, tok in enumerate(out):
        if tok == "-p" and i > 0 and out[i - 1] == "sshpass" and i + 1 < len(out):
            out[i + 1] = "<REDACTED>"
    return out


# --- #347: push-time guard against a newly-activated subdev stream account
# silently never gaining its own REMOTE_HOSTS registration -------------------
# Root cause of the incident this guards against: david2/david3/david4 were
# provisioned on the shared subdev VPS (odoo-erp#3282) days before airuleset
# gained their REMOTE_HOSTS/AUTHORITY_BY_USER/etc entries (#326) — nothing in
# `push` ever compared "which Linux accounts actually exist on the shared
# box" against "which accounts we think we manage", so the gap was invisible
# until the owner caught it live. montalu2/3/4 (#251/#263) and simap/miva1
# (#143/#300) hit variants of the identical class before that.
_HOME_AUDIT_MARKER = "===AIRULESET-HOMES==="


def _shared_remote_host_ips():
    """Host IPs backed by MORE than one REMOTE_HOSTS entry — boxes where a
    brand-new account can be hand-provisioned without ever gaining its own
    registration entry. A single-entry host (dev2, gatekeeper) has no such
    gap by construction, so auditing it would be pure overhead for zero
    possible finding."""
    import airuleset  # #433 L-E: REMOTE_HOSTS lives in cli_fleet, read via facade
    seen = {}
    for r in airuleset.REMOTE_HOSTS:
        h = r.get("host")
        seen[h] = seen.get(h, 0) + 1
    return {h for h, n in seen.items() if n > 1}


def _remote_cmd_with_home_audit(remote_cmd):
    """Append a same-connection `/home` listing to an existing remote
    install command, WITHOUT letting the trailing `ls` change the ssh
    call's own exit code — `;` sequencing after the `&&`-chained install
    steps would otherwise make the LAST command's exit code win, silently
    turning a genuine `git pull`/`install` failure into a false push
    success the moment this audit rides along on that connection. Capture
    the real exit code with `$?` immediately after the original chain, run
    the audit unconditionally, then `exit` with the ORIGINAL code."""
    return (
        remote_cmd
        + "; __ar_rc=$?; echo '%s'; ls -1 /home 2>/dev/null; exit $__ar_rc"
        % _HOME_AUDIT_MARKER
    )


def _parse_home_audit_output(stdout):
    """Extract the `/home` listing appended by `_remote_cmd_with_home_audit`
    from a completed ssh call's stdout. Returns "" when the marker never
    appears — e.g. ssh itself failed before the remote shell ever ran the
    audit at all, which is not this guard's concern (a real auth/timeout
    failure is already tracked by `cmd_push`'s own `failed` list)."""
    text = stdout or ""
    if _HOME_AUDIT_MARKER not in text:
        return ""
    return text.split(_HOME_AUDIT_MARKER, 1)[1]


def _parse_home_names(home_listing):
    """Home directory names from a raw `/home` listing, blank lines
    dropped. Shared by `unregistered_home_accounts` and
    `_home_listing_trustworthy` so both read the same real shape."""
    return {ln.strip() for ln in (home_listing or "").splitlines() if ln.strip()}


def _home_listing_trustworthy(user, home_listing):
    """#347 adversarial-review MAJOR finding (M1): `_HOME_AUDIT_MARKER`
    being present in an ssh call's stdout only proves the remote SHELL
    reached the audit — NOT that `ls -1 /home` itself actually succeeded.
    A hardened or root-owned `/home` makes `ls` fail with its stderr
    redirected to `/dev/null`, returning an EMPTY listing at rc 0 — which
    a marker-only check would read as "checked, no gap found" FOREVER,
    silently and permanently defeating the whole guard. Positive control:
    the very account THIS ssh connection authenticated AS must appear in
    any genuine listing (the same connection just `cd`'d into that
    account's own checkout, so its home directory unquestionably exists
    on this host) — if it's missing, the listing cannot be trusted at
    all, and the caller must fall through to a retry / an honest
    UNVERIFIED report rather than a false "clean"."""
    return user in _parse_home_names(home_listing)


def unregistered_home_accounts(host, home_listing):
    """Home directory names present on `host` (from a real `/home` listing)
    that have NO matching REMOTE_HOSTS entry for that EXACT host — #347's
    push-time guard against a newly-activated stream account silently never
    being registered as a push target. Read-only: the caller decides what
    to do with a non-empty result (a loud, non-blocking warning — refusing
    to push over an unrelated new account would be a worse failure mode
    than the gap this exists to report).

    "lost+found" is excluded unconditionally — a filesystem artifact on a
    `/home` that is its own mount point, never a stream account. Reporting
    it as a "gap" on every single push forever would train the reader to
    ignore the one warning that will someday be real (adversarial-review
    MINOR m2, alarm fatigue)."""
    import airuleset  # #433 L-E: REMOTE_HOSTS lives in cli_fleet, read via facade
    registered = {r.get("user") for r in airuleset.REMOTE_HOSTS if r.get("host") == host}
    names = _parse_home_names(home_listing)
    return sorted(names - registered - {"lost+found"})


def cmd_push(args):
    """Push to GitHub and deploy to all remote machines.

    Fail-closed: `ruff check .` runs FIRST, then the full test suite — a lint
    error or a single failing test aborts the push (and therefore the dev2
    deploy) so unlinted/untested code never ships. `git push` here is an
    internal subprocess call, so the PreToolUse pre-push-lint.sh hook (which
    only fires for a real Bash `git push` tool invocation) never sees this
    flow — this in-process gate is what actually protects it (issue #7)."""
    import shlex
    import subprocess
    import time
    import airuleset  # #433 L-E: cmd_install resident + REMOTE_HOSTS in cli_fleet, via facade

    # 0a. Lint the whole repo — fail-closed before any push/deploy. Unlike the
    # PreToolUse hook (which lints only the files a real `git push` command
    # changed), this runs from inside the process itself, so a whole-repo
    # check is the only way to guarantee it; keep it fast by keeping the repo
    # clean (see the ruff cleanup commit for #7 — this is cheap post-cleanup).
    print("Running ruff check (fail-closed before push)...")
    try:
        ruff_result = subprocess.run(
            ["ruff", "check", "."],
            cwd=str(REPO_DIR),
        )
    except FileNotFoundError:
        print("  RUFF NOT INSTALLED — refusing to push unlinted code.", file=sys.stderr)
        print("  Install ruff (e.g. `pip install ruff` / `pipx install ruff`) and retry.",
              file=sys.stderr)
        sys.exit(1)
    if ruff_result.returncode != 0:
        print("  RUFF FAILED — refusing to push unlinted code.", file=sys.stderr)
        sys.exit(1)
    print("  Ruff clean.")

    # 0b. Run the full test suite — fail-closed before any push/deploy.
    print("Running test suite (fail-closed before push)...")
    import tempfile
    # #271: `deliver_with_stash`/`_send_goal_verified` persist non-empty
    # input-box content to `watchdog.draft_rescue_dir()` (default
    # `~/.claude/draft-rescue/`) BEFORE any keystroke — and the live
    # systemd api-watchdog timer executes THIS repo's working tree every
    # 60s on this box, so a test process writing into the REAL directory
    # would be indistinguishable from production activity. Rather than
    # isolate every one of the ~19 test files whose fixtures transitively
    # reach these two functions via `run_once`, point the WHOLE test-suite
    # subprocess at one throwaway directory here — `AIRULESET_DRAFT_RESCUE_DIR`
    # is `draft_rescue_dir()`'s own env-override, so this is the single
    # place that has to know it exists.
    with tempfile.TemporaryDirectory() as _rescue_tmp, \
            tempfile.TemporaryDirectory() as _lock_tmp:
        test_env = dict(os.environ)
        test_env["AIRULESET_DRAFT_RESCUE_DIR"] = str(
            Path(_rescue_tmp) / "draft-rescue")
        # #400 owner kill-switch: the pre-push suite must stay green on a
        # box whose owner has genuinely disabled the compact/goal jobs --
        # the flag files are production state, not test state.
        test_env["AIRULESET_TEST_IGNORE_DISABLE"] = "1"
        # #385: `tests/test_autopilot_lock.py` spawns a REAL `autopilot-lock`
        # CLI subprocess on every test, against a fresh never-reused repo
        # path — without this, every push leaves a permanent orphaned lock
        # (plus `.mutex`/symlink/directory artifacts) in the REAL system
        # `/tmp`. `tests/conftest.py`'s `_isolate_autopilot_lock_dir` covers
        # a `pytest`-direct run; `conftest.py` is NOT read by `unittest
        # discover` at all, so this is the single place that has to know
        # `AIRULESET_AUTOPILOT_LOCK_DIR` exists for THIS subprocess.
        test_env["AIRULESET_AUTOPILOT_LOCK_DIR"] = str(
            Path(_lock_tmp) / "autopilot-lock")
        # #486 G1: the session-heartbeat producer (watchdog/session_status.py),
        # now invoked by the notify-discord-pending / notify-compact-subagent-
        # boundary / session-start-fetch hooks, writes
        # ~/.claude/session-status/<sid>.json via AIRULESET_SESSION_STATUS_DIR.
        # Existing TestCase tests that spawn those hooks would otherwise litter
        # the REAL home under `unittest discover` (which does not read
        # conftest.py's own isolation fixture) — same single-place-that-knows
        # rule as the two env-overrides above.
        test_env["AIRULESET_SESSION_STATUS_DIR"] = str(
            Path(_lock_tmp) / "session-status")
        test_result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(REPO_DIR), env=test_env,
        )
    if test_result.returncode != 0:
        print("  TESTS FAILED — refusing to push untested code.", file=sys.stderr)
        sys.exit(1)
    print("  Tests passed.")

    # 1. Push to GitHub
    print("\nPushing to GitHub...")
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED: {result.stderr.strip()}")
        sys.exit(1)
    print(f"  {result.stdout.strip() or result.stderr.strip()}")

    # #263: `failed` accumulates every remote (and now the local install
    # itself) that did NOT deploy cleanly. An adversarial review of the
    # first version of this timeout fix caught a real regression it
    # introduced: BEFORE, an uncaught TimeoutExpired propagated out of
    # cmd_push() with a loud traceback and a non-zero exit — impossible to
    # miss. The `continue` below (needed so one slow remote can't abort
    # deployment to every REMAINING host) turned that into a single line
    # among hundreds, with the run still ending "All deployments complete."
    # at exit 0 — a SILENT partial deploy. Tracking every failure and
    # exiting non-zero if any occurred restores the "impossible to miss"
    # property without reintroducing the abort-the-whole-loop defect.
    failed = []
    # #341: host NAMES whose deploy leg failed with a genuine ssh AUTH
    # failure this run ("Permission denied" — never a timeout, never a
    # plain remote-command failure, both of which mean auth actually
    # succeeded). Passed to provision_subdev_soniox_key() below so it never
    # opens a SECOND connection against an account already proven
    # unprovisioned/unreachable this run — contacting the same known-bad
    # account twice (once here, once again independently in the soniox
    # phase) is what compounded the fail2ban risk this set exists to close.
    auth_failed = set()

    # 2. Install locally
    # Adversarial-review CRITICAL finding (plugin-marketplace fix,
    # 2026-08-06): cmd_install() can now sys.exit(1) on a still-failing
    # managed-plugin install (script-failure-policy). Left uncaught here,
    # that SystemExit propagated straight out of cmd_push() BEFORE the
    # remote-deploy loop below ever ran — git had ALREADY pushed to
    # GitHub by this point, so main would advance and ZERO of the 9 remote
    # hosts (including montalu2/montalu3/montalu4, the very accounts this
    # fix exists for) would ever deploy it. Give the local step the exact
    # same "track it, keep going" treatment the remote loop already gets.
    print("\nInstalling locally...")
    try:
        airuleset.cmd_install(args)
    except SystemExit as e:
        if e.code:
            print(f"  FAILED: local install exited {e.code} — continuing to remotes")
            failed.append(("local(dev1)", "install rc=%s" % e.code))

    # 3. Deploy to each remote
    # #358: one per-run ssh ControlMaster socket directory, shared by the
    # deploy loop below AND the soniox-key phase (3b) -- multiplexes a
    # REPEATED connection to the SAME target within this one push onto a
    # single already-authenticated master. Degrades to `control_opts = []`
    # (plain, unmultiplexed ssh) if the temp dir can't be created; the
    # `finally` at the end of this block removes it regardless of how the
    # deploy phase finishes (success, a failed target, or a raised
    # SystemExit from the failure-summary branch below).
    control_dir = _ssh_control_dir_for_push()
    control_opts = _ssh_multiplex_opts(control_dir)
    try:
        # #347: audit ANY shared VPS host's /home listing for an account with
        # no REMOTE_HOSTS entry, exactly ONCE per host, riding the FIRST
        # already-happening connection to that host — never a dedicated extra
        # ssh call (#341: never re-probe the same host for a second, unrelated
        # purpose — that is exactly what compounded the fail2ban risk there).
        shared_hosts = _shared_remote_host_ips()
        audited_hosts = set()
        for remote in airuleset.REMOTE_HOSTS:
            print(f"\n{'=' * 50}")
            print(f"Deploying to {remote['name']} ({remote['host']})...")
            remote_cmd = f"cd {remote['repo_path']} && git pull --ff-only && python3 airuleset.py install"
            # #347 adversarial-review CRITICAL finding: `audited_hosts` must
            # NOT be marked here (before the ssh call even runs) — a first
            # entry that fails (timeout/auth) before the remote shell ever
            # reaches the appended `ls` would then PERMANENTLY skip the audit
            # for the rest of this push, with the failure looking IDENTICAL to
            # "checked, no gap found" (unregistered_home_accounts() on an
            # empty/no-marker listing silently returns []). `audited_hosts` is
            # only marked below, once the marker is CONFIRMED present in the
            # real stdout — so a failed first connection lets the NEXT entry
            # sharing this host retry the audit instead of silently giving up.
            audit_this_call = (remote["host"] in shared_hosts
                                and remote["host"] not in audited_hosts)
            if audit_this_call:
                remote_cmd = _remote_cmd_with_home_audit(remote_cmd)
            identity = remote.get("identity")
            if identity:
                # key-based SSH (e.g. the gatekeeper — prod-critical, no shared
                # password). #341: BatchMode=yes -- a failed pubkey attempt
                # against an unprovisioned/misconfigured account must fail
                # IMMEDIATELY instead of falling through to an interactive
                # password/keyboard-interactive attempt (the "Permission denied
                # (publickey,password)" shape), which is what let a single
                # connection generate several distinct auth-failure log lines.
                ssh_cmd = [
                    "ssh", "-i", os.path.expanduser(identity),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "BatchMode=yes",
                ] + control_opts + [
                    f"{remote['user']}@{remote['host']}",
                    remote_cmd,
                ]
            else:
                # #341: NumberOfPasswordPrompts=1 -- caps a wrong/unprovisioned
                # password attempt to ONE try (openssh's own default is 3, and
                # sshpass happily re-supplies the same password on every
                # re-prompt), so one sshpass call burns at most one
                # fail2ban-countable strike instead of up to three.
                ssh_cmd = [
                    "sshpass", "-p", "newlevel",
                    "ssh", "-o", "StrictHostKeyChecking=no",
                    "-o", "NumberOfPasswordPrompts=1",
                ] + control_opts + [
                    f"{remote['user']}@{remote['host']}",
                    remote_cmd,
                ]
            # #263: never let ONE slow/hanging remote (a first-time claude-CLI
            # curl install, ensure_playwright_browsers()'s npx install) abort
            # deployment to every REMAINING host — the loop used to have no
            # try/except around this call at all, so a TimeoutExpired here
            # propagated straight out of cmd_push() and silently skipped every
            # host still queued after this one.
            #
            # #358: retry ONLY a genuine ssh connection-establishment failure
            # (a MaxStartups-pool-exhaustion drop mid-handshake -- a random
            # per-connection event, never a per-source ban) -- bounded to
            # SSH_RETRY_MAX_ATTEMPTS total attempts, with a short backoff
            # between them. This is a TARGET-LEVEL retry only: it never
            # re-runs step 0a/0b/1 (ruff/tests/git push), which already ran
            # exactly once above, before this loop even started -- a retry
            # here can only ever repeat the SAME ssh_cmd for THIS ONE target.
            timed_out = False
            ssh_result = None
            for attempt in range(1, SSH_RETRY_MAX_ATTEMPTS + 1):
                try:
                    ssh_result = subprocess.run(
                        ssh_cmd,
                        capture_output=True,
                        text=True,
                        timeout=REMOTE_DEPLOY_TIMEOUT_S,
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    break
                if ssh_result.returncode == 0:
                    break
                if (attempt >= SSH_RETRY_MAX_ATTEMPTS
                        or not _is_ssh_transient_failure(
                            ssh_result.returncode, ssh_result.stderr)):
                    break
                backoff = _ssh_retry_backoff_s()
                print(f"  transient ssh failure on {remote['name']} "
                      f"(attempt {attempt}/{SSH_RETRY_MAX_ATTEMPTS}) — "
                      f"retrying in {backoff}s: "
                      f"{ssh_result.stderr.strip()[:200]}")
                time.sleep(backoff)
            if timed_out:
                print(f"  FAILED: timed out after {REMOTE_DEPLOY_TIMEOUT_S}s "
                      f"— continuing to the next host")
                failed.append((remote["name"], "timeout"))
                continue
            if ssh_result.returncode != 0:
                print(f"  FAILED: {ssh_result.stderr.strip()}")
                failed.append((remote["name"], "rc=%d" % ssh_result.returncode))
                # #341: a genuine ssh AUTH failure (never a remote-command
                # failure with auth intact, e.g. a bad `git pull`) marks this
                # host so the soniox phase below skips it instead of opening a
                # second connection against an already-known-bad account.
                if _is_ssh_auth_failure(ssh_result.returncode, ssh_result.stderr):
                    auth_failed.add(remote["name"])
                # #358: an exhausted transient (connection-closed/reset)
                # failure names the exact single-target command to retry
                # manually once the target is reachable again -- never the
                # whole push (which would re-run the full-suite gate for
                # nothing), and never the shared subdev PASSWORD (a
                # redacted placeholder stands in for it, security-basics.md).
                if _is_ssh_transient_failure(ssh_result.returncode,
                                              ssh_result.stderr):
                    hint = " ".join(shlex.quote(c)
                                     for c in _redacted_ssh_cmd(ssh_cmd))
                    print(f"  Manual retry once reachable: {hint}")
            else:
                print(f"  {ssh_result.stdout.strip()}")
                # #263: a successful remote install's STDERR was being silently
                # discarded here — this repo's own ensure_playwright_browsers()
                # warning writes to stderr (RUNTIME_DEPS' and this diff's own
                # report_stream_dev_env() gap warnings write to STDOUT and were
                # already reaching the console via the branch above), so on the
                # common success path that warning never reached the push
                # console. Surface it too, so a gap really is reported loudly
                # rather than swallowed by push's own success branch.
                stderr_out = ssh_result.stderr.strip()
                if stderr_out:
                    print(f"  [stderr] {stderr_out}")

            # #347: report (never block) any home directory on this shared
            # host with no matching REMOTE_HOSTS entry — the systemic guard
            # against a newly-activated stream account silently never being
            # registered as a push target. Advisory only: one unrelated stray
            # /home entry (a not-yet-onboarded or test account) must never
            # abort deployment to every OTHER, already-registered account.
            # `audited_hosts` is marked HERE, only once the marker is
            # positively confirmed present AND the listing itself passes the
            # `_home_listing_trustworthy` positive control (adversarial-review
            # MAJOR M1: the marker alone only proves the remote shell reached
            # the audit, never that `ls -1 /home` actually succeeded — a
            # hardened/root-owned /home fails silently at rc 0 with an EMPTY
            # listing, which would otherwise read as "checked, clean" forever)
            # — so a never-executed OR untrustworthy audit is never mistaken
            # for "ran and found nothing".
            if audit_this_call and _HOME_AUDIT_MARKER in (ssh_result.stdout or ""):
                home_listing = _parse_home_audit_output(ssh_result.stdout)
                if _home_listing_trustworthy(remote["user"], home_listing):
                    audited_hosts.add(remote["host"])
                    gap = unregistered_home_accounts(remote["host"], home_listing)
                    if gap:
                        print(f"\n⚠ REGISTRATION GAP on {remote['host']}: /home has "
                              f"account(s) with NO REMOTE_HOSTS entry: "
                              f"{', '.join(gap)} — a stream account was activated "
                              f"but never registered as a push target. Register "
                              f"it: REMOTE_HOSTS + AUTHORITY_BY_USER + the "
                              f"block-subdev-ssh-misuse.sh allow-list + watchdog's "
                              f"_REDUCED_STREAM_USERS + notify's STREAM_NOTIFY_OWNER "
                              f"(see #251/#263/#300/#326/#347's own onboarding "
                              f"checklist).", file=sys.stderr)

        # #347: any shared host that never got a TRUSTWORTHY audit this run
        # (every connection failed before the appended `ls` ever ran, or every
        # listing that did run failed the positive control) must be reported
        # as UNVERIFIED, not silently read as "no gap found" — the exact
        # false-negative the adversarial review flagged (both the original
        # CRITICAL and the follow-up MAJOR M1). Every shared host gets
        # `audit_this_call = True` on its FIRST-seen entry (audited_hosts
        # starts empty), so any host still missing from `audited_hosts` here
        # was genuinely never confirmed this run.
        unverified_shared = shared_hosts - audited_hosts
        if unverified_shared:
            print(f"\n⚠ REGISTRATION AUDIT NOT VERIFIED this run for: "
                  f"{', '.join(sorted(unverified_shared))} — no connection ever "
                  f"returned a trustworthy /home listing for the shared host "
                  f"(either every attempt failed before reaching it, or the "
                  f"listing itself could not be trusted), so a registration gap "
                  f"there could exist and go unreported until a later push "
                  f"confirms it.", file=sys.stderr)

        # 3b. Deliver the meeting-analysis Soniox key to every subdev stream
        # account (#275) -- a true no-op when REMOTE_HOSTS has no such account
        # (dev2/gatekeeper-only pushes never reach the source read at all).
        # #358: shares this run's OWN `control_opts` with the deploy loop
        # above, so an account contacted here a second time reuses that
        # account's already-authenticated master connection.
        print(f"\n{'=' * 50}")
        print("Delivering Soniox key to subdev stream accounts...")
        failed.extend(provision_subdev_soniox_key(skip_names=auth_failed,
                                                    control_opts=control_opts))

        if failed:
            # #341 adversarial-review F3 (MINOR, TRIGGERED): an auth-failed
            # stream host now yields TWO `failed` entries by design (its own
            # deploy `rc=...` PLUS the soniox `skipped-known-auth-failure`), so
            # len(failed) double-counts -- report the DISTINCT host count, the
            # full list still shows each reason.
            distinct_failed = {name for name, _reason in failed}
            print(f"\n⚠ {len(distinct_failed)} of {len(airuleset.REMOTE_HOSTS)} remote(s) "
                  f"FAILED: {failed}", file=sys.stderr)
            sys.exit(1)
        print("\nAll deployments complete.")
    finally:
        # #358: the ControlMaster socket directory is bounded on its own
        # (ControlPersist expires any leftover master promptly), but remove
        # it here too so a repeated push run never accumulates stale
        # per-run directories -- runs regardless of how the try block above
        # exits (success, a failed target, or `sys.exit(1)` above).
        if control_dir:
            shutil.rmtree(control_dir, ignore_errors=True)
