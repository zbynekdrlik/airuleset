"""airuleset File-Drop + api-watchdog systemd-leg install/CLI (#433 cluster L-B).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation -- same verbatim-move + facade-re-export pattern as cli_vault.py (H)
/ cli_burn.py (J) / cli_autopilot_lock.py (K) / cli_worktree_sweep.py (L1) /
cli_target_purge.py + cli_scratch_sweep.py (L2)). Binding cluster-L design
decision 5: the File-Drop and api-watchdog systemd LEGS ship together in ONE leaf
so their shared ``_run_systemctl``/``_whoami`` helpers live INSIDE it (no
cross-leaf duplication). airuleset.py keeps a single
``from cli_filedrop_watchdog import (...)`` re-export at the old filedrop-block
site, so cmd_install's setup steps, SUBCOMMANDS["share"]/["filedrop"],
_validate_filedrop's FILEDROP_SERVICE_TEMPLATE and every test's
``airuleset.<name>`` reference keep resolving unchanged.

Deliberately SELF-CONTAINED: stdlib + the ``filedrop`` package only at module
level -- no top-level ``import airuleset``, so there is no import-cycle surface
in either the CLI (``python3 airuleset.py``, airuleset running as ``__main__``)
or the test (``import airuleset``) topology (internals note 1483), even though
the airuleset.py facade imports this module DURING its own initialization. The
filedrop names come straight ``from filedrop import (...)`` -- the same shared
package airuleset.py itself imported, never a re-implementation (contrast J's
REMOTE_HOSTS, which had to stay resident). ``REPO_DIR``/``CLAUDE_DIR`` below are
this file's own copies of the canonical one-line expressions that
cli_autopilot_lock.py / watchdog/goal.py / watchdog/compact.py already inline
today -- identical values, established repo idiom.
"""

import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"


# --- File-Drop integration -- serve user files as clickable LAN URLs --------
try:
    from filedrop import (PORT as FILEDROP_PORT, DEFAULT_PORT as FILEDROP_DEFAULT_PORT,
                          PORT_FILE as FILEDROP_PORT_FILE, persisted_port as filedrop_persisted_port,
                          default_port_for_uid as filedrop_default_port_for_uid,
                          host_ip as filedrop_host_ip, bind_ips as filedrop_bind_ips,
                          filedrop_url, FILEDROP_DIR)
except Exception:  # pragma: no cover — filedrop package should always import
    FILEDROP_PORT = int(os.environ.get("FILEDROP_PORT", "8788"))
    FILEDROP_DEFAULT_PORT = 8788
    FILEDROP_PORT_FILE = CLAUDE_DIR / "filedrop.port"
    FILEDROP_DIR = CLAUDE_DIR / "filedrop"

    def filedrop_persisted_port():
        return None

    def filedrop_default_port_for_uid(uid=None):
        if uid is None:
            uid = os.getuid() if hasattr(os, "getuid") else 0
        return FILEDROP_DEFAULT_PORT + (uid % 1000)

    def filedrop_host_ip():
        return os.environ.get("FILEDROP_HOST", "127.0.0.1")

    def filedrop_bind_ips():
        return [filedrop_host_ip()]

    def filedrop_url():
        return f"http://{filedrop_host_ip()}:{FILEDROP_PORT}/"

FILEDROP_SERVICE_TEMPLATE = REPO_DIR / "settings" / "filedrop.service.template"
FILEDROP_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "filedrop.service"


# ---------------------------------------------------------------------------
# systemd --user helpers (shared by the File-Drop service install)
# ---------------------------------------------------------------------------


def _xdg_runtime_env():
    """A copy of os.environ with XDG_RUNTIME_DIR set explicitly.

    `systemctl --user` needs XDG_RUNTIME_DIR to find the user bus; when install
    runs over SSH (no login session) it is often unset. We set it deterministically
    to /run/user/<uid>."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run_systemctl(args):
    """Run `systemctl --user <args>` with the explicit XDG env. Returns
    (returncode, stdout, stderr). Never raises."""
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=30, env=_xdg_runtime_env())
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def _whoami():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")


# ---------------------------------------------------------------------------
# File-Drop systemd service + share/serve subcommands
# ---------------------------------------------------------------------------


def _render_filedrop_unit(port=None):
    """Read the file-drop unit template and substitute the per-machine placeholders.

    {{REPO_DIR}} -> this checkout's path (ExecStart). {{HOST_IP}} -> the primary
    (tailscale-first) IP, for status/URL. {{HOST_IPS}} -> the comma list of ALL
    private IPs to bind (tailscale + LAN — filedrop.bind_ips()), so the server
    answers on every interface the user might be on. Both are computed HERE
    (unsandboxed, so `hostname -I` / `tailscale ip` work) and baked into the
    Environment so the sandboxed server never needs AF_NETLINK to discover its own
    address. {{PORT}} -> the per-user port chosen by _choose_filedrop_port (a
    second airuleset user on the same host cannot reuse the first user's :8788)."""
    return (FILEDROP_SERVICE_TEMPLATE.read_text()
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{HOST_IPS}}", ",".join(filedrop_bind_ips()))
            .replace("{{HOST_IP}}", filedrop_host_ip())
            .replace("{{PORT}}", str(port if port is not None else FILEDROP_PORT)))


def _filedrop_port_bindable(bind_ip, port):
    """True iff (bind_ip, port) can be bound right now.

    SO_REUSEADDR mirrors the server's own HTTPServer.allow_reuse_address, so a
    port merely in TIME_WAIT from OUR OWN just-restarted server reads as
    reclaimable (not stolen); a live FOREIGN LISTEN still yields EADDRINUSE even
    with the flag, so it correctly reads as taken (#115). Any bind failure = not
    usable here — we only ever accept a port we could actually bind."""
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind_ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _choose_filedrop_port(bind_ip):
    """The port this user's file-drop should serve on — DETERMINISTIC per uid.

    N airuleset accounts on ONE host (the ~12 subdev streams) used to race for a
    single shared :8788: the old first-free probe reflected a transient instant,
    so under a fleet push every account bound :8788 momentarily, only one server
    survived, and every loser's `share` URL still advertised :8788 → 404 off a
    stranger's store (#493). The fix anchors each account on its OWN deterministic
    per-uid port (filedrop.default_port_for_uid), which is distinct per uid, so no
    two accounts ever contend and no probe/race is possible in the normal case.
    Precedence:
      1. FILEDROP_PORT env — explicit override, never second-guessed.
      2. A previously PERSISTED port (~/.claude/filedrop.port) — only ever exists
         after a past collision forced a non-deterministic fallback, or a
         ~/.claude migration carried another box's port (#33). Honored while it
         is still ours to use (our own live service serves it, OR it bind-tests
         free); a value a DIFFERENT instance holds here is dropped + re-picked.
      3. The DETERMINISTIC per-uid port `want`: kept when our own service is live
         (it holds its port; a bind-test can't tell "mine" from "another's"), or
         when it bind-tests free (share re-derives it from our uid — no persist).
      4. Only if `want` is genuinely held by ANOTHER instance (a rare uid-mod
         collision, or an unrelated service): probe upward and PERSIST the pick
         so the serve unit, the share CLI, and `filedrop status` all agree.
    Fail-open to `want` when nothing binds (the service then fails loudly,
    exactly as before)."""
    env = os.environ.get("FILEDROP_PORT")
    if env:
        return int(env)
    want = filedrop_default_port_for_uid()
    persisted = filedrop_persisted_port()
    rc, out, _err = _run_systemctl(["is-active", "filedrop.service"])
    our_active = rc == 0 and out.strip() == "active"
    if persisted:
        if our_active:
            return persisted        # our own live instance serves it
        if _filedrop_port_bindable(bind_ip, persisted):
            return persisted        # a past fallback, still free on THIS host
        # a ~/.claude migrated from another box carries THAT box's port — here it
        # can be a DIFFERENT user's live file-drop (montalu@subdev inherited
        # dev1's 8789 == marek's subdev port; #33, 2026-07-24). Stale → drop the
        # file and fall through to the deterministic port / probe re-pick.
        print(f"  persisted file-drop port {persisted} is held by another "
              f"instance on this host (not ours) — dropping "
              f"{FILEDROP_PORT_FILE} and re-picking")
        FILEDROP_PORT_FILE.unlink(missing_ok=True)
    if our_active:
        return want              # our own live instance owns its (deterministic) port
    if _filedrop_port_bindable(bind_ip, want):
        return want              # ours + free — share re-derives it from our uid
    # `want` genuinely held by ANOTHER instance — probe upward + persist.
    for cand in range(want + 1, want + 11):
        if _filedrop_port_bindable(bind_ip, cand):
            try:
                FILEDROP_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
                FILEDROP_PORT_FILE.write_text(f"{cand}\n")
                print(f"  file-drop port {want} taken by another instance on "
                      f"this host — using {cand} (persisted to {FILEDROP_PORT_FILE})")
            except OSError as e:
                print(f"  could not persist file-drop port choice ({e})",
                      file=sys.stderr)
            return cand
    return want


def _filedrop_is_live(url, timeout=2):
    """True iff GET <url> returns an HTTP response (root returns 404 by design,
    which still proves the server is up). Any completed request = live."""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # 404 at root is expected — the server answered
    except Exception:
        return False


def _wait_filedrop_live(url, attempts=5, delay=1.0):
    import time
    for _ in range(attempts):
        if _filedrop_is_live(url):
            return True
        time.sleep(delay)
    return False


def _restart_filedrop_service():
    rc, _o, err = _run_systemctl(["restart", "filedrop.service"])
    if rc != 0:
        print(f"  filedrop service restart failed (rc={rc}): {err.strip()}",
              file=sys.stderr)
    return rc == 0


def setup_filedrop_service():
    """Install + start the file-drop systemd --user service on THIS machine.

    Runs on every host (no board-style gating). Creates the served dir first
    (the read-only server never writes it), writes the unit, enables linger, and
    enable --now. On any failure it prints the manual command rather than claiming
    success."""
    import subprocess
    print("  Installing file-drop systemd --user service")

    # 1. served dir (0700) — the read-only server depends on it existing.
    try:
        FILEDROP_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(FILEDROP_DIR), 0o700)
    except OSError as e:
        print(f"  could not create {FILEDROP_DIR} ({e})", file=sys.stderr)

    # 2. write the unit — with the per-user port (a second airuleset user on the
    # same host must not restart-loop on the first user's :8788).
    if not FILEDROP_SERVICE_TEMPLATE.exists():
        print(f"  ERROR: file-drop service template missing: "
              f"{FILEDROP_SERVICE_TEMPLATE}", file=sys.stderr)
        return False
    port = _choose_filedrop_port(filedrop_host_ip())
    FILEDROP_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    FILEDROP_SERVICE_DEST.write_text(_render_filedrop_unit(port))
    print(f"  Wrote unit: {FILEDROP_SERVICE_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "filedrop.service")

    # 3. linger (best-effort)
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    # 4. daemon-reload + enable --now
    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    rc, _o, err = _run_systemctl(["enable", "--now", "filedrop.service"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False

    # 4b. restart to apply the freshly-written unit + latest filedrop code.
    # `enable --now` is a no-op for an already-running service, so a re-install
    # with a changed unit (e.g. a new bind IP) or new code needs an explicit
    # restart. Stateless file server — the brief blip is harmless.
    _run_systemctl(["restart", "filedrop.service"])

    # 5. liveness check on the LAN URL (server binds the LAN IP, not loopback).
    # Built from the port chosen ABOVE — the module-level PORT was resolved at
    # import time, i.e. before a fresh port choice was persisted this run.
    url = f"http://{filedrop_host_ip()}:{port}/"
    if _wait_filedrop_live(url):
        print(f"  File-drop is live. LAN base URL: {url}")
        return True
    print(f"  File-drop service started but did NOT answer on {url}. Check "
          f"`systemctl --user status filedrop.service`.", file=sys.stderr)
    return False


def maybe_setup_filedrop():
    """Install the file-drop service on this machine (every host runs one)."""
    setup_filedrop_service()


def _filedrop_serve():
    """Run the file-drop HTTP server in the FOREGROUND (systemd ExecStart target)."""
    from filedrop.server import run_server
    hosts_env = os.environ.get("FILEDROP_HOSTS", "").strip()
    hosts = [h for h in hosts_env.split(",") if h] or None
    run_server(host=filedrop_host_ip(), port=FILEDROP_PORT, hosts=hosts)


def cmd_share(args):
    """Copy a file into the file-drop server and print its clickable LAN URL.

    Prints ONLY the URL on stdout (easy to copy); diagnostics go to stderr. Per
    no-localhost-urls.md, the URL is live-checked before printing — if the server
    is down it tries one restart, and refuses to print a dead URL."""
    from urllib.parse import urlsplit

    from filedrop import advertise_urls
    from filedrop.share import ShareError, share
    try:
        url, dest = share(args.path)
    except ShareError as e:
        print(f"share: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"share: unexpected error ({e})", file=sys.stderr)
        sys.exit(1)

    if not _filedrop_is_live(url):
        # Down — try a single restart, then re-check the primary before printing.
        print("share: file-drop not responding — attempting service restart...",
              file=sys.stderr)
        _restart_filedrop_service()
        if not _wait_filedrop_live(url):
            print(f"share: file copied to {dest} but the file-drop server is DOWN at "
                  f"{filedrop_url()} — start it with "
                  f"`systemctl --user start filedrop.service`.", file=sys.stderr)
            sys.exit(1)

    # Primary is live — print ONE URL per private interface (tailscale + LAN) that
    # actually answers, so the user has a working link whichever network they are on.
    sp = urlsplit(url)
    reachable = [u for u in advertise_urls(port=sp.port, path=sp.path)
                 if _filedrop_is_live(u)]
    for u in (reachable or [url]):
        print(u)


def _filedrop_status():
    url = filedrop_url()
    live = _filedrop_is_live(url)
    print(f"file-drop: {url}")
    print(f"  this machine: serves {FILEDROP_DIR}")
    print(f"  liveness:     {'UP' if live else 'DOWN / unreachable'}")


def cmd_filedrop(args):
    """File-drop control: --serve (daemon), --url (live-check + print), status."""
    if getattr(args, "serve", False):
        _filedrop_serve()
        return
    if getattr(args, "url", False):
        url = filedrop_url()
        if _filedrop_is_live(url):
            print(url)
        else:
            print(f"file-drop: DOWN — {url} unreachable", file=sys.stderr)
            sys.exit(1)
        return
    _filedrop_status()


# --- api-watchdog systemd unit paths ---------------------------------------
WATCHDOG_SERVICE_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.service.template"
WATCHDOG_TIMER_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.timer.template"
WATCHDOG_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.service"
WATCHDOG_TIMER_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.timer"


def watchdog_disable_marker():
    """`~/.claude/api-watchdog.disabled` — the opt-out that makes a deliberate
    `systemctl --user stop api-watchdog.timer` SURVIVE a deploy (#132).

    Resolved at CALL time, never frozen at import (same reasoning as
    `watchdog.compact.compact_requests_path()`).

    Why this exists: on 2026-07-28 the watchdog typed `/exit` into a live
    session, the timer was stopped fleet-wide as the mitigation — and was found
    running again on all 6 boxes the next morning, because `install` ends with
    an unconditional `enable --now` and every `airuleset.py push` runs
    `install`. A mitigation a routine deploy silently undoes is not a
    mitigation. Touch this file to keep the timer off across pushes; delete it
    to hand control back to `install`."""
    return Path.home() / ".claude" / "api-watchdog.disabled"


def setup_watchdog_service():
    """Install + start the api-watchdog systemd --user timer on THIS machine
    (every host — autopilot runs on dev1 and dev2). Mirrors the file-drop setup:
    write the .service + .timer units, daemon-reload, enable --now the timer —
    unless `watchdog_disable_marker()` exists, in which case the units are still
    refreshed but the timer is left exactly as the operator set it (#132)."""
    import subprocess
    print("  Installing api-watchdog systemd --user timer")
    for tmpl in (WATCHDOG_SERVICE_TEMPLATE, WATCHDOG_TIMER_TEMPLATE):
        if not tmpl.exists():
            print(f"  ERROR: watchdog unit template missing: {tmpl}", file=sys.stderr)
            return False
    WATCHDOG_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_SERVICE_DEST.write_text(
        WATCHDOG_SERVICE_TEMPLATE.read_text().replace("{{REPO_DIR}}", str(REPO_DIR)))
    WATCHDOG_TIMER_DEST.write_text(WATCHDOG_TIMER_TEMPLATE.read_text())
    print(f"  Wrote unit: {WATCHDOG_TIMER_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "api-watchdog.timer")
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    if watchdog_disable_marker().exists():
        # ENFORCE the stop, don't merely decline to start it. A timer that is
        # stopped but still ENABLED comes back at the next boot or linger
        # restart, so "skip enable --now" alone would let the mitigation expire
        # on its own (#132). Both calls are idempotent, so a box that is
        # already stopped+disabled just no-ops.
        _run_systemctl(["stop", "api-watchdog.timer"])
        _run_systemctl(["disable", "api-watchdog.timer"])
        print(f"  api-watchdog timer STOPPED + DISABLED — disable marker "
              f"present ({watchdog_disable_marker()}).\n"
              f"  Units refreshed. To re-arm: delete the marker and run "
              f"`systemctl --user enable --now api-watchdog.timer`.")
        return True
    rc, _o, err = _run_systemctl(["enable", "--now", "api-watchdog.timer"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    print("  api-watchdog timer active (polls every 60s).")
    return True


def maybe_setup_watchdog():
    setup_watchdog_service()
