"""airuleset `secret` subcommand — the credential-vault CLI channel (#433 cluster H, module named cli_vault.py — "secret" in a filename is refused by hooks/block-sensitive-staging.sh).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as
watchdog/usage.py / burn_jobs.py / cards.py / repo_health.py).
airuleset.py keeps `from cli_vault import (...)` re-exports at the old
definition site, so `airuleset.cmd_secret`, `airuleset._secret_*`,
`SUBCOMMANDS["secret"]` and `main()`'s `SECRET_ACTIONS` argparse wiring
all keep working unchanged, and `cmd_upload`'s bare-name `_pick_free_port`
call resolves through airuleset's own globals after the facade import
(late binding — the proven cards.py `_git_*` shape).

This module is deliberately SELF-CONTAINED: stdlib + local (function-body)
`filedrop` imports only — no reference back into airuleset.py, so there is
no import-cycle surface in either the CLI (`python3 airuleset.py`,
airuleset running as `__main__`) or the test (`import airuleset`) topology.
`REPO_DIR` below is this file's own copy of the canonical expression —
identical value, this file sits in the same directory as airuleset.py.
"""

import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def _pick_free_port(ips, ports):
    """The first port in `ports` that `ips` can actually BIND — None if none can.

    The pre-#115 scan probed `connect_ex(("127.0.0.1", cand))`, but
    `filedrop._is_private` EXCLUDES loopback and `upload_server.py` binds exactly
    `bind_ips()`, deliberately never 0.0.0.0 (a WRITE endpoint on a box that may
    have a public IP). The probe's address and the server's addresses were
    therefore disjoint BY CONSTRUCTION: a live endpoint answers on none of the
    addresses the scan asked about, so the scan handed out an occupied port
    (observed on dev1: five listeners on :8799, scan picked 8799, and the second
    endpoint then failed to bind anything).

    Binding the very addresses the server is about to bind asks the server's own
    question. Only EADDRINUSE rejects a candidate — any other error
    (EADDRNOTAVAIL from a stale or departed interface) is tolerated, because
    upload_server.py SKIPS such an address rather than dying on it and needs only
    one success; treating that as "occupied" would let one stale IP reject every
    candidate on a box that serves fine. SO_REUSEADDR mirrors
    HTTPServer.allow_reuse_address, so the probe's verdict is the server's."""
    import errno
    import socket

    for port in ports:
        for ip in ips:
            s = socket.socket()
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((ip, port))
            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    break
            finally:
                s.close()
        else:
            return port
    return None


SECRET_ACTIONS = ("request", "status", "list", "exec", "forget", "purge", "show")
# Both lifetimes are CLAMPED, not merely defaulted. `int(args.ttl or DEFAULT)`
# let a negative value through (0 is falsy and fell back; -1 is truthy), and the
# server armed its shutdown timer only for a positive TTL — so `--ttl -1` gave a
# credential-receiving endpoint with no timer at all, alive until reboot, while
# its store record was already expired and `status` reported `absent`.
SECRET_MIN_TTL_S, SECRET_MAX_TTL_S = 30, 3600
SECRET_MIN_KEEP_S, SECRET_MAX_KEEP_S = 60, 24 * 3600


def _secret_clamp_ttl(value):
    return max(SECRET_MIN_TTL_S, min(int(value), SECRET_MAX_TTL_S))


def _secret_clamp_keep(value):
    return max(SECRET_MIN_KEEP_S, min(int(value), SECRET_MAX_KEEP_S))
# A distinct range from `upload`'s 8799-8819, so the two endpoint kinds can
# never be confused for one another by a port alone.
SECRET_PORTS = range(8830, 8850)
# A THIRD distinct range for the `secret show` render endpoint (#580), so the
# receive (request), send (show) and file (upload) endpoints never share a port.
SHOW_PORTS = range(8850, 8870)


def _secret_bindable(ip):
    """May a credential endpoint listen here?

    The CLI-side half of the two independent checks (the other is
    `filedrop/vault_server.py:is_private`, which re-validates its own argv). A
    public address is refused outright — the token in the path is the endpoint's
    only auth, and on a box with a public IP (the gatekeeper VPS) an open
    credential endpoint on the internet is not a recoverable mistake. Loopback
    is allowed here even though `filedrop._is_private` drops it for the file
    endpoints: it cannot leave the box, so it is strictly more private than
    tailscale — it is simply not reachable BY the user, which the URL print
    makes obvious.
    """
    from filedrop import _is_private
    return bool(_is_private(ip) or (isinstance(ip, str) and ip.startswith("127.")))


# Interfaces whose traffic is encrypted BEFORE it leaves the box, so a plain
# HTTP endpoint on them is not actually in the clear. Deliberately does NOT
# include `tun` — `tunl0` is IPIP, a tunnel with no encryption at all, and the
# only safe direction for this label is to under-claim.
_SECRET_ENCRYPTED_IFACE = ("tailscale", "wg", "wireguard", "zt")


def _secret_iface_for(ip):
    """The interface `ip` is configured on, or None."""
    from filedrop import _iface_ips
    for cand, ifname in _iface_ips():
        if cand == ip:
            return ifname
    return None


def _secret_opener():
    """A urllib opener with proxies DISABLED.

    The default opener reads $http_proxy/$https_proxy, so the liveness probe
    was sending its URL to a proxy host — and a proxy error page returning 200
    made a dead endpoint look live. This probe only ever targets an address on
    this very machine; a proxy is never right for it.
    """
    import urllib.request as _u
    return _u.build_opener(_u.ProxyHandler({}))


def _secret_health_url(ip, port):
    """The liveness probe's URL — deliberately token-free.

    The probe used to GET the token URL, which hands the endpoint's only
    authentication to whatever is listening on that port. If another local
    user's process won the pick-a-port race, that is their listener.
    """
    return "http://%s:%d/healthz" % (ip, port)


def _secret_probe_urls(ips, port):
    """Every address's liveness URL — the list the readiness loop consumes.

    A named helper rather than an inline comprehension because the inline one
    is exactly what silently kept probing the TOKEN urls after the health route
    was added: nothing could assert on it.
    """
    return [_secret_health_url(ip, port) for ip in ips]


def _secret_is_encrypted(ip, iface=None):
    """True when traffic to `ip` is encrypted before it leaves the box.

    Tailscale by CIDR, loopback (it never leaves at all), and any interface
    whose name starts with a known encrypted-overlay prefix. Everything else is
    genuine cleartext, whatever its address range says — the ranges do not
    carry the answer, since wg0 and a zerotier both look like plain LAN.
    """
    from filedrop import _is_tailscale
    if _is_tailscale(ip) or str(ip).startswith("127."):
        return True
    if iface is None:
        iface = _secret_iface_for(ip)
    return bool(iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE))


def _secret_partition_ips(ips):
    """(encrypted, cleartext) — the split `request` advertises from."""
    enc, plain = [], []
    for ip in ips:
        (enc if _secret_is_encrypted(ip) else plain).append(ip)
    return enc, plain


def _secret_select_ips(ips, allow_plain=False):
    """(chosen, dropped) addresses for a credential endpoint.

    Cleartext is OPT-IN. A LAN URL carries the token in the request line and
    the credential in the POST body with nothing around them, and offering it
    beside the encrypted one means some of the time it gets picked. A box with
    only cleartext returns an EMPTY chosen list rather than falling back — the
    caller then tells the user to re-run with --allow-plain, which is a
    decision, not a default.
    """
    enc, plain = _secret_partition_ips(ips)
    if allow_plain:
        return enc + plain, []
    return enc, plain


def _secret_public_lane(args):
    """(public_host, port) for the public-TLS drop lane, or (None, None) (#664).

    Delegates the decision to `cli_drop_gateway.resolve_public_lane`: the lane is
    used only when this box has a LIVE drop marker AND (`--public` was passed OR
    the invoking unix account has a no-tailscale CONSUMER — `consumer_forces_public`,
    the #786 default for david1/david2 whose consumer is David's tailscale-less
    laptop — OR the box has no tailscale, the no-tailscale auto-fallback). "Has
    tailscale" is the user-reachable encrypted transport; loopback/LAN do not count
    (loopback is never reachable BY the user, LAN is unencrypted), so the check
    keys on a real tailscale interface. A box with tailscale, no `--public`, and a
    non-consumer account returns (None, None) — today's behaviour untouched.
    """
    from filedrop import _is_tailscale
    from filedrop import bind_ips
    import cli_drop_gateway as _dg
    have_tailscale = any(_is_tailscale(ip) for ip in bind_ips())
    lane = _dg.resolve_public_lane(getattr(args, "public", False), have_tailscale)
    if lane:
        return lane[0], lane[1]
    return None, None


def _secret_public_url_line(host, token):
    """The advertised public HTTPS URL line (delegates to cli_drop_gateway)."""
    import cli_drop_gateway as _dg
    return _dg.public_url_line(host, token)


def _secret_url_line(ip, port, token, iface=None):
    """One advertised URL plus its TRANSPORT, spelled out.

    The ticket's own requirement: when several URLs are offered the user must be
    able to SEE which one is encrypted before deciding where to type a password.

    The label keys on the INTERFACE, not on the address range, because the
    ranges do not carry the answer: `bind_ips()` legitimately advertises real
    overlays, and on this box wg0 (10.88.*), wg-money (192.168.10.*) and a
    zerotier (10.243.*) all look exactly like plain LAN addresses. Calling an
    encrypted tunnel "NEŠIFROVANÉ" steers the user to the worse option on the
    one page where it matters most.
    """
    from filedrop import _is_tailscale
    url = "http://%s:%d/%s/" % (ip, port, token)
    if _is_tailscale(ip):
        return "%s   [tailscale — šifrované (WireGuard), odporúčané]" % url
    if str(ip).startswith("127."):
        return "%s   [loopback — len z tohto stroja]" % url
    if iface is None:
        iface = _secret_iface_for(ip)
    if iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE):
        return "%s   [%s — šifrovaný tunel]" % (url, iface)
    return "%s   [LAN — NEŠIFROVANÉ (plain HTTP), použi radšej tailscale]" % url


def _secret_redact(blob, value, marker=b"<<REDACTED>>"):
    """`blob` with every anticipated rendering of `value` replaced.

    A child of `secret exec` must not be able to put the credential on the
    CLI's stdout/stderr, because those are the agent's transcript — the one
    place this whole channel exists to keep the value out of. The child's argv
    is chosen by the agent, so `secret exec DB_PASS -- env` was a one-command
    leak and any verbose or failing child (`curl -v`, `bash -x`, a tool that
    echoes its config on error) was an accidental one.

    Fragments shorter than 4 bytes are NOT redacted: at that length the value
    matches ordinary text everywhere and the filter would destroy the child's
    output instead of protecting anything.

    TWO KINDS OF RENDERING, and the second was missing (#153 finding 2). An
    ENCODING re-encodes the value whole (b64, hex, percent) — those were
    covered from the start. ESCAPING leaves the value's own bytes in place and
    rewrites only its metacharacters, so a search for the raw value misses it
    completely: for a value containing a quote, a backslash or a newline,
    `json.dumps({"pw": v})` and `repr(v)` both passed straight through. That is
    the ACCIDENTAL class this filter exists for — a child dumping its config as
    JSON, a traceback printing a dict — not a deliberate transformation, so the
    gap was real and the old docstring's disclaimer did not cover it. The
    escaped forms a stdlib dump actually produces are now in the set: JSON,
    repr() of a str and of bytes, unicode_escape, HTML/XML escaping,
    shell-quoting, and configparser's %-doubling.

    HONEST LIMIT, stated rather than implied: this stops the value appearing
    VERBATIM, in an obvious encoding, or in an ordinary escaped rendering. A
    child that deliberately transforms it (encrypts it, reverses it, prints it
    a character per line, base64s it twice) still defeats the filter — nothing
    at this layer can prevent that, because the session genuinely has to be
    able to USE the credential. The containment that would close it —
    resolving the command from a user-written template instead of agent
    argv — was filed as #154 and is now IMPLEMENTED for a TEMPLATED name:
    `cmd_secret`'s `exec` action refuses agent-supplied `-- CMD` argv outright
    once `filedrop.vault.has_template(name)` is true, and runs only
    `read_template(name)`'s result instead — the child is whatever the
    operator wrote, never whatever the agent chose, so this whole class of
    deliberate transformation no longer applies to it. Templating is OPT-IN
    per name: an UNTEMPLATED name keeps the full residual above, unchanged.

    THE OTHER RESIDUAL, which redaction cannot touch at all: this filters the
    child's captured fd 1/2 ONLY. Nothing constrains where the child WRITES —
    `secret exec DB -- sh -c 'echo "$DB" > config.ini'` puts the value in a
    git-tracked file, and no output filter can see that happen.
    """
    import base64
    import html
    import json
    import shlex
    import urllib.parse

    if not value:
        return blob
    # The floor is on the VALUE, not on each derived form. Escaping EXPANDS:
    # `"` renders as `&quot;` and `<` as `&lt;`, both of which clear a
    # per-form floor — so a per-form test silently broke this docstring's own
    # promise and turned every `&quot;` in a child's HTML output into the
    # marker. Base64 had the same shape long before the escaped forms existed
    # (one byte encodes to four characters).
    if len(value.strip()) < 4:
        return blob
    forms = {value, value.strip()}
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        forms.add(urllib.parse.quote(text).encode())
        forms.add(urllib.parse.quote_plus(text).encode())
        # Escaped renderings. Each is sliced to the BODY the dump would embed,
        # without the quotes the dumper adds around it, so the form matches
        # wherever it is nested (a dict, a config line, a traceback).
        forms.add(json.dumps(text)[1:-1].encode())
        # ensure_ascii=False is an ordinary dump option and renders a
        # non-ASCII value completely differently from the default.
        forms.add(json.dumps(text, ensure_ascii=False)[1:-1].encode())
        forms.add(repr(text)[1:-1].encode())
        forms.add(repr(value)[2:-1].encode())
        forms.add(text.encode("unicode_escape"))
        forms.add(html.escape(text).encode())
        forms.add(html.escape(text, quote=False).encode())
        forms.add(shlex.quote(text).encode())
        forms.add(text.replace("%", "%%").encode())
    forms.add(base64.b64encode(value))
    forms.add(base64.b64encode(value).rstrip(b"="))
    forms.add(base64.urlsafe_b64encode(value))
    forms.add(base64.urlsafe_b64encode(value).rstrip(b"="))
    forms.add(value.hex().encode())
    forms.add(value.hex().upper().encode())      # plenty of tools print caps
    out = blob
    # Longest first, so a form that contains another does not leave a tail.
    for form in sorted((f for f in forms if len(f) >= 4), key=len, reverse=True):
        out = out.replace(form, marker)
    return out


def _secret_apply_remainder(args):
    """Move the flags argparse's REMAINDER swallowed back onto `args`.

    `cmd` is an `argparse.REMAINDER` (needed so `exec NAME -- CMD ...` can carry
    arbitrary child arguments), and REMAINDER stops parsing at the first token
    after the positional NAME — so `secret request DB_PASS --ttl 900` puts
    `--ttl 900` in the remainder and the flag is silently ignored. Found live:
    a request made with `--ttl 900 --keep 900` reported `endpoint-ttl=600s
    keep=28800s` and stored the value with the 8-hour default.

    Consume only OUR flags, only from the head, and stop dead at `--` so a flag
    meant for the child is never eaten (`exec DB -- psql --ttl 1` keeps its own
    `--ttl`). An explicitly parsed value wins — the remainder only ever fills a
    field argparse left unset.
    """
    ints = {"--ttl": "ttl", "--keep": "keep", "--port": "port"}
    strs = {"--env": "env", "--persist": "persist", "--file": "file",
            "--persist-map": "persist_map"}
    rest = list(getattr(args, "cmd", None) or [])
    while rest:
        tok = rest[0]
        if tok == "--":
            rest.pop(0)
            break
        if tok in ("--stdin", "--replace", "--allow-plain", "--public"):
            setattr(args, tok[2:].replace("-", "_"), True)
            rest.pop(0)
            continue
        key, eq, inline = tok.partition("=")
        if key not in ints and key not in strs:
            break                           # not ours — it belongs to the child
        if eq:
            value, width = inline, 1
        elif len(rest) > 1:
            value, width = rest[1], 2
        else:
            break                           # a dangling flag: leave it visible
        dest = ints.get(key) or strs[key]
        if key in ints:
            try:
                value = int(value)
            except ValueError:
                break                       # not a flag of ours after all
        if getattr(args, dest, None) is None:
            setattr(args, dest, value)      # an explicit value always wins
        del rest[:width]
    args.cmd = rest


def _secret_resolve_persist(args):
    """Validate `--persist PATH` (#529) and return the RESOLVED absolute string,
    or None when not given. Fail-fast: a git-tracked or symlinked target is an
    operator error, so exit 2 before standing up an endpoint (request) or
    touching the value (exec). Shared by both branches so the check is
    identical."""
    from filedrop import vault as st
    persist = getattr(args, "persist", None)
    if not persist:
        return None
    try:
        return str(st.validate_durable_path(persist))
    except st.SecretError as e:
        print("secret: --persist %s" % e, file=sys.stderr)
        sys.exit(2)


def _secret_exec_self_heal(nm, value, persist):
    """Write the durable ~/.secrets/<name> copy from the vault value if it is
    ABSENT (never overwrite a copy that is the source of truth), record the
    target, and report (#529). LOUD but NEVER fatal on a write failure: the
    child still gets the value, and the periodic backstop + `secret status`
    surface a missing file. `persist` is already resolved by
    `_secret_resolve_persist`; no credential value ever reaches these prints."""
    from filedrop import vault as st
    try:
        wrote = st.persist_durable(persist, value, overwrite=False)
        st.set_durable_target(nm, persist)
        if wrote:
            print("secret exec: persisted %s -> %s (durable)" % (nm, persist),
                  file=sys.stderr)
    except st.SecretError as e:
        print("secret exec: durable persist to %s failed: %s" % (persist, e),
              file=sys.stderr)


def _secret_show_source(args, st):
    """Resolve `secret show`'s value source WITHOUT reading the value (#580).

    Returns (kind, locator, label): a vault NAME that is `ready`, or a validated
    `--file` path. The CLI parent must never read the value — it only checks
    metadata (`state`) for a name, or the file's LOCATION + mode
    (`validate_show_file`) for a file, and hands the name/path to the server
    child, which reads the value at GET. Fail-fast: exit 2 on a missing source
    or a bad --file (an operator error), exit 1 on a name that is not stored.
    """
    file_arg = getattr(args, "file", None)
    name = getattr(args, "name", None)
    if file_arg:
        if name:
            print("secret show: give a NAME or --file, not both", file=sys.stderr)
            sys.exit(2)
        try:
            real = st.validate_show_file(file_arg)
        except st.SecretError as e:
            print("secret show: --file %s" % e, file=sys.stderr)
            sys.exit(2)
        return "file", str(real), st.show_log_label("file", str(real))
    if not name:
        print("secret show: needs a NAME or --file <path>", file=sys.stderr)
        sys.exit(2)
    try:
        st.check_name(name)
    except st.SecretError as e:
        print("secret: %s" % e, file=sys.stderr)
        sys.exit(2)
    state = st.state(name)
    if state != "ready":
        print("%s is not stored (state=%s) — nothing to show" % (name, state),
              file=sys.stderr)
        sys.exit(1)
    return "name", name, st.show_log_label("name", name)


def _secret_show(args):
    """Stand up a one-shot RENDER endpoint (filedrop/show_server.py) that shows
    a credential the box holds to the OWNER's browser ONCE, then tears down —
    the OUTPUT direction of the vault (#580).

    Reuses `secret request`'s bind policy (`_secret_bindable`/`_secret_select_ips`,
    encrypted-default + `--allow-plain`), port pick, health probe and URL
    labelling. The value never reaches THIS process: the server child reads it
    only at GET (`read_value`/`read_show_file`), and the token goes through the
    env (0400), never argv. The NAME/PATH passed in argv is not the value. No
    vault entry is created or consumed — `show` neither stores nor forgets; the
    endpoint self-terminates on first view or TTL.
    """
    # Recover a NAME that argparse's REMAINDER swallowed after a LEADING flag
    # (`secret show --public NAME` parses to name=None, cmd=['NAME'] once
    # `_secret_apply_remainder` stripped the flag), mirroring what
    # `_secret_request_names` does for the request path (#664 review). The trailing
    # form `secret show NAME --public` already sets args.name directly.
    if getattr(args, "name", None) is None and not getattr(args, "file", None):
        leftover = [t for t in (getattr(args, "cmd", None) or [])
                    if not t.startswith("-")]
        if leftover:
            args.name = leftover[0]

    import secrets as _secrets
    import subprocess
    import time

    from filedrop import bind_ips
    from filedrop import vault as st

    kind, locator, label = _secret_show_source(args, st)
    ttl = _secret_clamp_ttl(getattr(args, "ttl", None) or st.DEFAULT_ENDPOINT_TTL_S)

    private = [ip for ip in bind_ips() if _secret_bindable(ip)]
    if not private:
        print("secret show: no private interface to bind (refusing a public bind)",
              file=sys.stderr)
        sys.exit(1)
    ips, dropped = _secret_select_ips(private,
                                      allow_plain=getattr(args, "allow_plain", False))

    # Public-TLS drop lane (#664): same tailscale -> public fallback as request.
    public_host, port = _secret_public_lane(args)
    if public_host:
        if getattr(args, "port", None) or getattr(args, "allow_plain", False):
            print("secret show: public drop lane — ignoring --port/--allow-plain "
                  "(fixed loopback port %d, TLS via the tunnel)" % port,
                  file=sys.stderr)
        ips, dropped = ["127.0.0.1"], []
        if _pick_free_port(ips, [port]) is None:
            print("secret show: public drop port %d is busy — another drop "
                  "endpoint (secret/upload) holds it; wait for it to close" % port,
                  file=sys.stderr)
            sys.exit(1)
    else:
        if not ips:
            print("secret show: only unencrypted interfaces are available (%s). A "
                  "credential would cross the LAN in cleartext — re-run with "
                  "--allow-plain if that is acceptable here."
                  % ", ".join(dropped), file=sys.stderr)
            sys.exit(1)
        port = int(getattr(args, "port", None) or 0) or _pick_free_port(ips, SHOW_PORTS)
        if port is None:
            print("secret show: no free port in %d-%d" % (SHOW_PORTS[0], SHOW_PORTS[-1]),
                  file=sys.stderr)
            sys.exit(1)

    token = _secrets.token_urlsafe(24)      # 24 bytes = 192 bits (>= 128)
    st.log_event("show", label, ttl=ttl)
    endpoint_log = st.log_path().parent / ("show-endpoint-%d.log" % port)
    endpoint_log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(endpoint_log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # The token goes through the ENVIRONMENT, never argv: /proc/<pid>/cmdline is
    # world-readable (0444), /proc/<pid>/environ is owner-only (0400).
    child_env = dict(os.environ)
    child_env["AIRULESET_VAULT_TOKEN"] = token
    with os.fdopen(fd, "ab") as lf:
        child = subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "show_server.py"),
             str(port), ",".join(ips), kind, locator, str(ttl)],
            stdout=subprocess.DEVNULL, stderr=lf, stdin=subprocess.DEVNULL,
            env=child_env, start_new_session=True)

    probes = _secret_probe_urls(ips, port)
    opener = _secret_opener()

    def _live(u):
        try:
            return opener.open(u, timeout=2).status in (200, 204)
        except OSError:
            return False

    for _ in range(20):
        if child.poll() is not None:
            print("secret show: endpoint exited early — see %s" % endpoint_log,
                  file=sys.stderr)
            sys.exit(1)
        if any(_live(u) for u in probes):
            break
        time.sleep(0.25)
    else:
        print("secret show: endpoint failed to come up — see %s" % endpoint_log,
              file=sys.stderr)
        sys.exit(1)

    for ip in ips:
        if _live(_secret_health_url(ip, port)):
            print(_secret_public_url_line(public_host, token) if public_host
                  else _secret_url_line(ip, port, token))
    if dropped:
        print("(skipped %s — cleartext; --allow-plain offers them too)"
              % ", ".join(dropped))
    print("name=%s  endpoint-ttl=%ds  (jednorazové zobrazenie)" % (label, ttl))
    print("Otvor URL v prehliadači — hodnota sa zobrazí RAZ a potom sa adresa "
          "zavrie. Do chatu ju NEPÍŠ.")


def _secret_request_names(args):
    """Every NAME to request in ONE endpoint (#603): `args.name` plus the extra
    positional names argparse's REMAINDER swallowed (`name` is `nargs="?"`, so
    `secret request A B C` parses as name="A", cmd=["B","C"]), with our own
    flags — which may appear ANYWHERE among the names — pulled back onto `args`.

    A SINGLE-name request yields a one-element list, and the downstream path
    treats that byte-identically to today (same argv, same single-field page,
    same raw-body POST). `_secret_apply_remainder` has already extracted the
    LEADING flags before this runs; this walk re-derives the names AND catches
    any flag that trails a name (`request A B --ttl 900`), which the head-only
    remainder pass stops before. Idempotent: a flag already set on `args` is
    never overwritten.
    """
    ints = {"--ttl": "ttl", "--keep": "keep", "--port": "port"}
    strs = {"--persist": "persist", "--persist-map": "persist_map"}
    bools = {"--allow-plain": "allow_plain", "--replace": "replace",
             "--public": "public"}
    toks = ([args.name] if getattr(args, "name", None) else []) \
        + list(getattr(args, "cmd", None) or [])
    names, i = [], 0
    while i < len(toks):
        tok = toks[i]
        if tok == "--":
            i += 1                              # tolerate a stray -- (no child)
            continue
        key, eq, inline = tok.partition("=")
        if key in bools:
            setattr(args, bools[key], True)
            i += 1
            continue
        if key in ints or key in strs:
            dest = ints.get(key) or strs[key]
            if eq:
                val, step = inline, 1
            elif i + 1 < len(toks):
                val, step = toks[i + 1], 2
            else:
                i += 1                          # dangling flag: drop it
                continue
            if key in ints:
                try:
                    val = int(val)
                except ValueError:
                    print("secret request: %s expects a number, got %r"
                          % (key, val), file=sys.stderr)
                    sys.exit(2)
            if getattr(args, dest, None) is None:
                setattr(args, dest, val)        # an explicit value always wins
            i += step
            continue
        names.append(tok)
        i += 1
    args.cmd = []
    return names


def _secret_parse_persist_map(args, names):
    """{name: resolved durable path} for this request (#603).

    A single name may still use `--persist PATH` (unchanged, #529). ANY request
    may use `--persist-map NAME=path,NAME2=path2`. The two are mutually
    exclusive, and `--persist` with more than one name is refused (one path
    cannot serve several names — use --persist-map). Every path is validated
    (`validate_durable_path`: no symlink, no git-repo ancestor — the on-disk
    filename keeps its natural hyphens, so the operator gives an EXPLICIT path,
    never one derived from the underscore-only vault NAME). A map key naming a
    credential NOT in this request is a typo and is refused before an endpoint
    stands up. Fail-fast: exits 2 on any operator error.
    """
    from filedrop import vault as st
    persist = getattr(args, "persist", None)
    raw_map = getattr(args, "persist_map", None)
    if persist and raw_map:
        print("secret request: use EITHER --persist (a single name) OR "
              "--persist-map, not both", file=sys.stderr)
        sys.exit(2)
    if persist:
        if len(names) != 1:
            print("secret request: --persist takes ONE path — for several names "
                  "use --persist-map NAME=path,NAME2=path2", file=sys.stderr)
            sys.exit(2)
        return {names[0]: _secret_resolve_persist(args)}
    if not raw_map:
        return {}
    nameset = set(names)
    out = {}
    for pair in raw_map.split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, path = pair.partition("=")
        key, path = key.strip(), path.strip()
        if not sep or not key or not path:
            print("secret request: --persist-map entry %r must be NAME=path"
                  % pair, file=sys.stderr)
            sys.exit(2)
        if key not in nameset:
            print("secret request: --persist-map names %r, which is not being "
                  "requested" % key, file=sys.stderr)
            sys.exit(2)
        try:
            out[key] = str(st.validate_durable_path(path))
        except st.SecretError as e:
            print("secret request: --persist-map %s: %s" % (key, e),
                  file=sys.stderr)
            sys.exit(2)
    return out


def _secret_request(args):
    """Stand up ONE intake endpoint for one OR several named credentials (#603).

    Extracted from `cmd_secret`'s request fall-through so the multi-field path
    has room without growing `cmd_secret`. A single name reproduces today's flow
    exactly (argv carries one NAME, the nonce goes through `AIRULESET_VAULT_NONCE`,
    the server serves the single-field page and stores the raw body); several
    names share ONE URL, ONE token and ONE atomic submit (nonces map through
    `AIRULESET_VAULT_NONCES`, the server serves a field-per-name page and stores
    the JSON body all-or-nothing via `vault.store_values`).
    """
    import json
    import secrets as _secrets
    import subprocess
    import time

    from filedrop import bind_ips
    from filedrop import vault as st

    names = _secret_request_names(args)
    seen = []
    for n in names:
        try:
            st.check_name(n)
        except st.SecretError as e:
            print("secret: %s" % e, file=sys.stderr)
            sys.exit(2)
        if n not in seen:
            seen.append(n)                      # dedup, order-preserving
    names = seen
    if not names:
        print("secret request: needs at least one NAME", file=sys.stderr)
        sys.exit(2)

    # Validate durable targets NOW (fail fast, before any endpoint).
    persist_map = _secret_parse_persist_map(args, names)

    ready = [n for n in names if st.state(n) == "ready"]
    if ready:
        print("already stored — `secret forget` first: %s" % ", ".join(ready),
              file=sys.stderr)
        sys.exit(1)
    pending = [n for n in names if st.state(n) == "pending"]
    if pending and not getattr(args, "replace", False):
        print("already has a pending request — finish it, or re-run with "
              "--replace: %s" % ", ".join(pending), file=sys.stderr)
        sys.exit(1)
    for n in pending:
        print("cancelling the previous request for %s: %s"
              % (n, st.stop_endpoint(n)))
        st.forget(n)

    ttl = _secret_clamp_ttl(getattr(args, "ttl", None) or st.DEFAULT_ENDPOINT_TTL_S)
    keep = _secret_clamp_keep(getattr(args, "keep", None) or st.DEFAULT_KEEP_S)

    private = [ip for ip in bind_ips() if _secret_bindable(ip)]
    if not private:
        print("secret: no private interface to bind (refusing a public bind)",
              file=sys.stderr)
        sys.exit(1)
    ips, dropped = _secret_select_ips(private,
                                      allow_plain=getattr(args, "allow_plain", False))

    # Public-TLS drop lane (#664): channel order is tailscale -> public. When
    # this box has a LIVE drop lane AND (--public OR no tailscale), bind loopback
    # on the fixed drop port that a managed cloudflared tunnel fronts and
    # advertise ONE public HTTPS URL — never an ssh -L instruction.
    public_host, port = _secret_public_lane(args)
    if public_host:
        if getattr(args, "port", None) or getattr(args, "allow_plain", False):
            print("secret: public drop lane — ignoring --port/--allow-plain "
                  "(fixed loopback port %d, TLS via the tunnel)" % port,
                  file=sys.stderr)
        ips, dropped = ["127.0.0.1"], []
        if _pick_free_port(ips, [port]) is None:
            print("secret: public drop port %d is busy — another drop endpoint "
                  "(secret/upload) holds it; wait for it to close" % port,
                  file=sys.stderr)
            sys.exit(1)
    else:
        if not ips:
            print("secret: only unencrypted interfaces are available (%s). A "
                  "credential would cross the LAN in cleartext — re-run with "
                  "--allow-plain if that is acceptable here."
                  % ", ".join(dropped), file=sys.stderr)
            sys.exit(1)
        port = int(getattr(args, "port", None) or 0) or _pick_free_port(ips, SECRET_PORTS)
        if port is None:
            print("secret: no free port in %d-%d" % (SECRET_PORTS[0], SECRET_PORTS[-1]),
                  file=sys.stderr)
            sys.exit(1)

    token = _secrets.token_urlsafe(24)          # 24 bytes = 192 bits (>= 128)
    nonces = {n: st.register_request(n, endpoint_ttl_s=ttl, keep_s=keep,
                                     durable_path=persist_map.get(n))
              for n in names}
    endpoint_log = st.log_path().parent / ("endpoint-%d.log" % port)
    endpoint_log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(endpoint_log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # The token goes through the ENVIRONMENT, never argv (/proc/<pid>/cmdline is
    # 0444; /proc/<pid>/environ is 0400). A single name keeps the exact env var
    # it always used; several names carry a {name: nonce} JSON map instead.
    child_env = dict(os.environ)
    child_env["AIRULESET_VAULT_TOKEN"] = token
    if len(names) == 1:
        child_env["AIRULESET_VAULT_NONCE"] = nonces[names[0]]
    else:
        child_env["AIRULESET_VAULT_NONCES"] = json.dumps(nonces)
    with os.fdopen(fd, "ab") as lf:
        child = subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "vault_server.py"),
             str(port), ",".join(ips), ",".join(names), str(ttl), str(keep)],
            stdout=subprocess.DEVNULL, stderr=lf, stdin=subprocess.DEVNULL,
            env=child_env, start_new_session=True)
    # One shared endpoint serves the whole page: record its pid under EVERY name
    # so `forget`/the TTL sweep can stop it (cancelling any one name tears down
    # the shared page, which is the safe direction for a credential request).
    for n in names:
        st.record_endpoint(n, child.pid)

    probes = _secret_probe_urls(ips, port)
    opener = _secret_opener()

    def _live(u):
        try:
            return opener.open(u, timeout=2).status in (200, 204)
        except OSError:
            return False

    for _ in range(20):
        if any(_live(u) for u in probes):
            break
        time.sleep(0.25)
    else:
        print("secret: endpoint failed to come up — see %s" % endpoint_log,
              file=sys.stderr)
        sys.exit(1)

    for ip in ips:
        if _live(_secret_health_url(ip, port)):
            print(_secret_public_url_line(public_host, token) if public_host
                  else _secret_url_line(ip, port, token))
    if dropped:
        print("(skipped %s — cleartext; --allow-plain offers them too)"
              % ", ".join(dropped))
    print("name=%s  endpoint-ttl=%ds  keep=%ds"
          % (",".join(names), ttl, keep))
    for n in names:
        if n in persist_map:
            print("durable[%s]=%s  (persisted 0600 at paste — survives the "
                  "vault TTL)" % (n, persist_map[n]))
    if len(names) == 1:
        print("Otvor URL v prehliadači a vlož hodnotu — do chatu ju NEPÍŠ. "
              "Stav: `airuleset.py secret status %s`." % names[0])
    else:
        print("Otvor URL v prehliadači, vlož VŠETKY hodnoty a odošli RAZ — do "
              "chatu ich NEPÍŠ. Stav: `airuleset.py secret status <NAME>` "
              "(mená: %s)." % ", ".join(names))


def cmd_secret(args):
    """Receive a CREDENTIAL from the user through a URL — never through chat.

    A password / SSH key / PAT / token typed into the chat is written
    permanently into the session transcript (`~/.claude/projects/**/*.jsonl`),
    survives compaction, and cannot be revoked. This command is the alternative:
    `request` prints a one-shot URL, the user posts the value from their own
    browser, and the session learns only that the NAME is ready. The value is
    stored 0600 under `~/.claude/secrets/` and is handed to a child process by
    `exec` — no action here prints it, and there is deliberately no action that
    could.

    WHAT THAT DOES NOT COVER — the residuals, stated here because this is where
    a reader forms their belief about the channel (#153 finding 3):

      * The store is readable by the AGENT'S OWN UID. "No action here prints
        it" is a claim about this command, not about the box: `cat` is not one
        of this command's actions. That path is now closed by an artifact,
        `hooks/block-vault-store-read.sh` — but it is a GUARDRAIL, not a
        boundary, because the agent's uid holds NOPASSWD sudo on these boxes,
        so no store location is out of its reach. It guarantees the unsafe path
        is refused by default and that circumventing it leaves an artifact.
      * `exec`'s CHILD is unconstrained on disk FOR AN UNTEMPLATED NAME.
        Output redaction covers the captured fd 1/2 only; nothing stops the
        child WRITING the value anywhere, including a git-tracked file —
        `secret exec DB -- sh -c 'echo "$DB" > config.ini'` is not something
        any output filter can see. #154 closes this for a TEMPLATED name:
        `secret exec NAME` for a name locked to a user-written command
        template (`~/.claude/secrets/NAME.template`, see filedrop/vault.py's
        `has_template`/`read_template`) runs ONLY that command —
        agent-supplied `-- CMD` argv is refused outright — so the child is
        whatever the operator wrote, never whatever the agent chose. An
        UNTEMPLATED name keeps the residual above unchanged: templating is
        opt-in per name, never a blanket requirement. Templating has its OWN
        honest limit, stated where the file lives (filedrop/vault.py's own
        module docstring) rather than gestured at here: this repo ships no
        function that WRITES a template, on purpose — any such function
        would be reachable by `python3 -c "..."`, a route no text-matching
        hook can see, so the only safe design is to not have one. A template
        is authored by placing the file directly, by a means outside
        anything this repo ships.
      * `exec` BUFFERS all child output until the child exits
        (`capture_output=True`, required in order to filter it). So there is no
        streaming for a long-running or interactive child, and if the CLI is
        killed mid-run the child's output is lost entirely.
      * `request` PRINTS a capability URL into the transcript BY DESIGN — that
        is how the user receives it. The token in it is live for the endpoint
        TTL (default 600s, capped at 3600s), so for that window anyone who can
        read the transcript AND reach a private interface can POST a SUBSTITUTE
        credential before the user does. The nonce binds the endpoint to the
        request, not to whoever posts. Keep the TTL short; `secret forget`
        cancels a pending endpoint.
      * TTL is swept HOURLY (watchdog job 29), so a value stored with the 60s
        minimum `keep` can survive up to ~1h past its expiry.
    """
    import shlex
    import subprocess

    from filedrop import vault as st

    _secret_apply_remainder(args)
    action = args.action
    name = getattr(args, "name", None)

    # Opportunistic TTL sweep on EVERY invocation — the guarantee that a value
    # cannot lie on disk indefinitely must not depend on anyone remembering to
    # run `purge` (the same shape filedrop.share.prune has).
    expired = st.purge()

    def _need_name():
        if not name:
            print("secret %s: needs a NAME" % action, file=sys.stderr)
            sys.exit(2)
        try:
            st.check_name(name)
        except st.SecretError as e:
            print("secret: %s" % e, file=sys.stderr)
            sys.exit(2)
        return name

    if action == "show":
        return _secret_show(args)

    if action == "purge":
        print("purged: %s" % (", ".join(expired) if expired else "nothing"))
        return

    if action == "list":
        # Keyed by name so a #154 template-only entry (no `.secret`/`.meta`
        # at all) can be unioned in without touching `list_entries()`/
        # `_entry_names()` — those drive `purge()`'s sweep, and a template
        # has no expiry to purge (see `template_names()`'s own docstring).
        rows = {r[0]: r for r in st.list_entries()}
        templated = set(st.template_names())
        names = sorted(set(rows) | templated)
        if not names:
            print("no secrets stored")
            return
        print("%-24s %-8s %-9s %-26s %s"
              % ("NAME", "STATE", "TEMPLATE", "RECEIVED", "EXPIRES"))
        for nm in names:
            if nm in rows:
                _nm, row_state, _req, recv, exp = rows[nm]
            else:
                row_state, recv, exp = st.state(nm), "-", "-"
            print("%-24s %-8s %-9s %-26s %s"
                  % (nm, row_state, "yes" if nm in templated else "no", recv, exp))
        return

    if action == "status":
        nm = _need_name()
        state = st.state(nm)
        meta = st.read_meta(nm)
        extra = ""
        if state == "ready" and isinstance(meta.get("expires_at"), (int, float)):
            extra = "  expires=%s" % st._iso(meta["expires_at"])
        # The COMMAND, not the value — non-sensitive, and showing it is what
        # "does it know whether a name is templated" (#154) asked for.
        try:
            if st.has_template(nm):
                extra += "  templated=%s" % shlex.join(st.read_template(nm))
        except st.SecretError as e:
            extra += "  templated=<error: %s>" % e
        # #529: surface the durable target + whether its file actually landed,
        # so a "delivery without persistence" is visible from the CLI (the
        # session-visible half of the periodic watchdog backstop).
        dpath = st.durable_target(nm)
        if dpath:
            try:
                present = Path(dpath).expanduser().exists()
            except OSError:
                present = False
            extra += "  durable=%s (%s)" % (dpath, "present" if present else "MISSING")
        print("%s %s%s" % (nm, state, extra))
        return

    if action == "forget":
        nm = _need_name()
        try:
            done = st.forget(nm)
        except st.SecretError as e:
            print("secret forget: %s" % e, file=sys.stderr)
            sys.exit(1)
        print("%s %s" % (nm, "forgotten" if done else "was not stored"))
        return

    if action == "exec":
        nm = _need_name()
        cmd = list(getattr(args, "cmd", None) or [])
        # #154: a TEMPLATED name is LOCKED — agent-supplied `-- CMD` is
        # refused outright, and the template's own argv is used instead.
        # Resolved BEFORE `read_value` so a locked name with a bad CMD
        # refuses without ever touching the stored value.
        try:
            templated = st.has_template(nm)
        except st.SecretError as e:
            print("secret exec: %s" % e, file=sys.stderr)
            sys.exit(1)
        if templated:
            if cmd:
                print("secret exec: %s is locked to a command template — "
                      "the CMD after `--` is refused (omit it; the "
                      "templated command always runs)" % nm, file=sys.stderr)
                sys.exit(2)
            try:
                cmd = st.read_template(nm)
            except st.SecretError as e:
                print("secret exec: %s" % e, file=sys.stderr)
                sys.exit(1)
        if not cmd:
            print("secret exec: needs a command after `--`", file=sys.stderr)
            sys.exit(2)
        # #529: --persist PATH self-heals the durable ~/.secrets/<name> copy
        # from the vault value BEFORE running the child, so a durable-first
        # consumer that read a MISS falls back to the vault ONCE and is
        # vault-independent thereafter. Validate the path FIRST (fail-fast on a
        # bad path, before touching the value); self-heal AFTER read_value.
        persist = _secret_resolve_persist(args)
        try:
            value = st.read_value(nm)
        except st.SecretError as e:
            print("secret exec: %s" % e, file=sys.stderr)
            sys.exit(1)
        st.log_event("used", nm)
        if persist:
            _secret_exec_self_heal(nm, value, persist)
        # NEVER let the child inherit fd 1/2: those are the agent's transcript.
        # Capture, filter, then re-emit — so a child that echoes its own
        # environment or config cannot write the credential into a file nobody
        # can revoke (adversarial review, finding 1).
        if getattr(args, "stdin", False):
            # stdin, so the value is not in the child's environment at all
            # (/proc/<pid>/environ is owner-only, but a child that dumps its own
            # env into a log is a real shape).
            res = subprocess.run(cmd, input=value, capture_output=True)
        else:
            env = dict(os.environ)
            given = getattr(args, "env", None)
            # `is not None`, not `or`: an explicitly EMPTY --env is a caller
            # error, not a request for the default.
            key = nm if given is None else given
            try:
                # The same grammar as a secret name, and for the same reason:
                # an unchecked key is an injection point (`BASH_FUNC_x%%` makes
                # a bash child EXECUTE the value; `A=B` splits into two).
                st.check_name(key)
            except st.SecretError as e:
                print("secret exec: bad --env key: %s" % e, file=sys.stderr)
                sys.exit(2)
            try:
                env[key] = value.decode("utf-8")
            except UnicodeDecodeError:
                print("secret exec: %s is not UTF-8 — use --stdin" % nm,
                      file=sys.stderr)
                sys.exit(1)
            res = subprocess.run(cmd, env=env, capture_output=True)
        for stream, data in ((sys.stdout, res.stdout), (sys.stderr, res.stderr)):
            if data:
                stream.buffer.write(_secret_redact(data, value))
                stream.flush()
        sys.exit(res.returncode)

    # --- request -----------------------------------------------------------
    # ONE endpoint for one OR several named credentials (#603). The whole flow
    # lives in `_secret_request` so the multi-field path has room without
    # growing this dispatcher; a single-name request there is byte-identical to
    # the pre-#603 inline flow.
    return _secret_request(args)
