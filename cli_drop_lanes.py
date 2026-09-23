"""airuleset — the GENERATED drop-lane registry engine + push-measured filedrop
port cache + deploy-loop harvest helpers (#1115, split out of cli_drop_gateway
for the 1000-line file cap, #993 area review).

This is a SELF-CONTAINED LEAF (#433): it imports ONLY ``cli_fleet`` (a pure
zero-import data leaf) + stdlib — NEVER ``cli_drop_gateway`` or ``airuleset``. The
lane-shape primitives it cannot own without a cycle (the ``DropLane`` class, the
hand-authored ``_SEED_DROP_LANES``, the controller tunnel UUID, the port range)
are INJECTED into ``build_drop_lanes`` by its one caller in ``cli_drop_gateway``.
So there is no cross-import cycle to reason about (`cli_drop_gateway` imports THIS
at its top; this imports nothing back) — the both-orders import test proves it.

``cli_drop_gateway`` re-exports every public name here so existing callers keep
using ``cli_drop_gateway.DROP_LANES`` / ``.build_drop_lanes`` / ``.read_drop_lanes_cache``
/ ``.filedrop_port_probe_snippet`` unchanged.
"""
import sys
from pathlib import Path

# #1115: the drop-lane registry is GENERATED from the fleet registry. cli_fleet
# is a pure DATA leaf with ZERO top-level imports (it never imports this module),
# so importing it here creates no cycle.
import cli_fleet


# #1115: a box `name` in cli_fleet.REMOTE_HOSTS is a LABEL, not the box's
# os.uname().nodename — three labels are irregular: the `gatekeeper` box is
# nodename `odoo-gatekeeper`, `spinbike-vps` is `spinbike`, and the `controller`
# box's real hostname is `airuleset` (the `@controller` label is the human name,
# `uname -n` == `airuleset`). The KEY must equal the box's real nodename so
# `drop_lane_for_account(os.uname().nodename, user)` resolves AND the push
# port-harvest key matches (both keyed the same way). Every other label maps
# cleanly: `<user>@<box>` → box, a bare `<box>` → itself.
_NODENAME_OVERRIDE = {"gatekeeper": "odoo-gatekeeper", "spinbike-vps": "spinbike",
                      "claudy@controller": "airuleset"}

# #1115: the FIXED per-account drop-port allocation for the GENERATED lanes
# (the 14 fleet accounts with no seed lane). Keyed by the REAL (nodename,
# username) — controller is keyed `airuleset` per _NODENAME_OVERRIDE. Packed
# above the 7 hand-authored ports (8870-8876) inside the 8870-8909 range. A lock
# test asserts fleet-wide uniqueness; a future account with no entry here (and no
# `drop` block) auto-allocates the next free in-range port (still deterministic).
_GENERATED_DROP_PORTS = {
    ("airuleset", "claudy"): 8877,
    ("dev1", "newlevel"): 8878,
    ("dev2", "newlevel"): 8879,
    ("forestshop-dev", "admin"): 8880,
    ("forestshop-dev", "stepan"): 8881,
    ("subdev", "miva1"): 8882,
    ("subdev", "montalu1"): 8883,
    ("subdev", "montalu2"): 8884,
    ("subdev", "montalu3"): 8885,
    ("subdev", "montalu4"): 8886,
    ("subdev", "montalu5"): 8887,
    ("subdev", "montalu6"): 8888,
    ("subdev", "montalu7"): 8889,
    ("subdev", "montalu8"): 8890,
}


def _nodename_for_entry(entry):
    """The box os.uname().nodename for a REMOTE_HOSTS entry (#1115). `<user>@<box>`
    → box, a bare `<box>` → itself, with the three irregular boxes overridden."""
    name = entry.get("name", "")
    if name in _NODENAME_OVERRIDE:
        return _NODENAME_OVERRIDE[name]
    return name.split("@", 1)[1] if "@" in name else name


def _generated_drop_host(nodename, username, shared):
    """The deterministic public hostname for a generated lane (#1115):
    `drop-<box>-<user>.newlevel.media` on a SHARED box (>1 account on the
    nodename), `drop-<box>.newlevel.media` on a single-account box. Single-level
    under newlevel.media (dashes only, no dots) — LOAD-BEARING for the
    `*.newlevel.media` one-level Universal SSL cert."""
    stem = "drop-%s-%s" % (nodename, username) if shared else "drop-%s" % nodename
    return "%s.newlevel.media" % stem


def _is_tailscale_host(host):
    """True iff `host` is a tailscale CGNAT IPv4 (100.64.0.0/10 → 100.64-127.x.x).

    A generated controller-topology lane's origin MUST be a tailscale IP: the
    controller's cloudflared proxies the drop hostname to `http://<origin>:<port>`
    over the tailnet. A non-tailscale `host` (a public IP or a public FQDN like
    forestshop-dev.newlevel.media) would route the drop hop over the PUBLIC
    internet in cleartext to a box whose filedrop server binds only tailscale +
    loopback — a 502 + a plaintext-over-public leak (review finding). Such a box
    gets a LOCAL-topology placeholder lane instead (its own tunnel is a Slice-B
    go-live decision), which is never rendered into the controller ingress."""
    if not isinstance(host, str):
        return False
    parts = host.split(".")
    if len(parts) != 4:
        return False                    # a FQDN or non-dotted-quad → not tailscale
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 0 or o > 255 for o in octets):
        return False
    return octets[0] == 100 and 64 <= octets[1] <= 127


def build_drop_lanes(remote_hosts, *, seed, drop_lane_cls,
                     controller_tunnel_uuid, port_base, port_max):
    """The drop-lane registry GENERATED from the fleet (#1115).

    Returns a fresh `(nodename, username) -> DropLane` dict: every hand-authored
    `seed` entry preserved BYTE-FOR-BYTE, plus one generated lane for every
    non-paused `remote_hosts` account that has no seed. A generated lane whose box
    has a TAILSCALE `host` rides the ONE controller multi-ingress tunnel
    (topology="controller", origin = that tailscale IP); a NON-tailscale box (a
    public-only box like forestshop) gets a LOCAL-topology PLACEHOLDER
    (origin_host=None, tunnel TBD at Slice-B go-live) that is never rendered into
    the controller ingress — never a public/plaintext origin (review finding).
    Access-gated by default (deny-by-default intent; the per-account Access app +
    include list is provisioned at Slice-B go-live, which is HARD-gated on a
    DROP_ACCESS_APPS spec, so an unspecced lane can never serve unprotected).
    `filedrop_port=None` (the push-measured value arrives via the
    ~/.claude/drop-lanes.json cache). A per-account host/port/access override may
    be supplied by an optional `drop` block on the entry.

    Dependency-injected (this is a self-contained leaf, #433): `seed` (the
    hand-authored lanes), `drop_lane_cls` (the DropLane class),
    `controller_tunnel_uuid`, `port_base`/`port_max` — all owned by
    cli_drop_gateway, passed in by its bound `build_drop_lanes(remote_hosts)`
    wrapper so this module never imports cli_drop_gateway (no cycle).

    Paused accounts (simap1) are excluded. Port precedence: the entry's `drop`
    block, else the fixed `_GENERATED_DROP_PORTS` table, else the next free
    in-range port (deterministic — accounts scanned in sorted key order).

    NON-FATAL by construction: the caller runs this at IMPORT (DROP_LANES =
    build_drop_lanes(...)), and cli_drop_gateway is imported by airuleset.py, so a
    raise here would crash the WHOLE CLI + statusline + watchdog (review finding).
    A port collision / range exhaustion therefore LOGS loudly to stderr and SKIPS
    that one account (the box simply has no drop lane until fixed) rather than
    raising.
    """
    lanes = dict(seed)
    used_ports = {lane.port for lane in lanes.values()}

    # Deterministic order: sort the not-yet-covered accounts by their key so the
    # next-free-port fallback is stable regardless of REMOTE_HOSTS ordering.
    pending = []
    for entry in remote_hosts:
        if cli_fleet.is_paused(entry):
            continue
        key = (_nodename_for_entry(entry), entry.get("user", ""))
        if key in lanes:
            continue
        pending.append((key, entry))
    pending.sort(key=lambda ke: ke[0])

    # Which nodenames host >1 non-paused account (→ shared-box hostnames).
    node_counts = {}
    for entry in remote_hosts:
        if cli_fleet.is_paused(entry):
            continue
        node_counts[_nodename_for_entry(entry)] = \
            node_counts.get(_nodename_for_entry(entry), 0) + 1

    def _next_free_port():
        for cand in range(port_base, port_max + 1):
            if cand not in used_ports:
                return cand
        return None                     # exhausted — caller logs + skips

    for key, entry in pending:
        nodename, username = key
        drop = entry.get("drop") or {}
        port = drop.get("port") or _GENERATED_DROP_PORTS.get(key) or _next_free_port()
        if port is None:
            print("#1115 WARNING: drop-port range %d-%d exhausted — %s@%s gets NO "
                  "drop lane this build (widen DROP_PORT_MAX)."
                  % (port_base, port_max, username, nodename), file=sys.stderr)
            continue
        if port in used_ports:
            print("#1115 WARNING: duplicate drop port %d for %s@%s — SKIPPING this "
                  "lane (fix _GENERATED_DROP_PORTS / the drop block)."
                  % (port, username, nodename), file=sys.stderr)
            continue
        used_ports.add(port)
        host = drop.get("host") or _generated_drop_host(
            nodename, username, node_counts.get(nodename, 0) > 1)
        access = drop.get("access")
        access = True if access is None else bool(access)
        # Tailscale box → controller-tunnel origin; public-only box → a
        # local-topology placeholder (never a public/plaintext controller origin).
        if _is_tailscale_host(entry.get("host")):
            lanes[key] = drop_lane_cls(
                host=host, port=port,
                tunnel_uuid=controller_tunnel_uuid,
                tunnel_config=None, tunnel_service=None,
                tunnel_system_unit=False, access=access,
                gateway_account=None,
                topology="controller", origin_host=entry.get("host"),
                filedrop_port=None)
        else:
            lanes[key] = drop_lane_cls(
                host=host, port=port,
                tunnel_uuid=None,        # its own tunnel is a Slice-B decision
                tunnel_config=None, tunnel_service=None,
                tunnel_system_unit=False, access=access,
                gateway_account=None,
                topology="local", origin_host=None,
                filedrop_port=None)
    return lanes


# #1115: the controller-side cache of each target's PUSH-MEASURED persistent
# filedrop port, keyed "<nodename>/<username>". `push` reads each target's port
# (~/.claude/filedrop.port, else the #493 uid-derived default) over the deploy
# ssh session it already opens and writes this file on the controller;
# `drop_ingress_rules_for_controller()` PREFERS it, so the measured
# `DropLane.filedrop_port` literal in code is now only the fallback. A missing /
# unreadable / malformed cache degrades silently to that fallback.
# The effective READ binds THIS name (cli_drop_gateway re-exports a SEPARATE
# binding), so to override the cache path in a test, patch
# cli_drop_lanes.DROP_LANES_CACHE, not the gateway copy (review MINOR-2).
DROP_LANES_CACHE = Path.home() / ".claude" / "drop-lanes.json"


def drop_lanes_cache_key(nodename, username):
    """The controller-cache key for an account (#1115): ``"<nodename>/<username>"``."""
    return "%s/%s" % (nodename, username)


def read_drop_lanes_cache(path=None):
    """The push-measured filedrop-port cache as ``{"<node>/<user>": port}`` (#1115).

    Robust: a missing / unreadable / malformed file, or a non-int port value,
    yields ``{}`` (the caller then falls back to the in-code ``filedrop_port``).
    Never raises."""
    import json
    p = Path(path) if path is not None else DROP_LANES_CACHE
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def write_drop_lanes_cache(measured, path=None):
    """Write the controller-side filedrop-port cache atomically (#1115).

    ``measured`` is ``{"<node>/<user>": port}`` (already keyed via
    ``drop_lanes_cache_key``). Returns the path written. Creates the parent dir
    if needed; a non-int port is skipped (never poisons the file)."""
    import json
    p = Path(path) if path is not None else DROP_LANES_CACHE
    clean = {}
    for k, v in (measured or {}).items():
        try:
            clean[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")
    tmp.replace(p)
    return p


def persist_measured_filedrop_ports(measured, drop_lanes):
    """#1115: merge the push-measured filedrop ports into the controller cache
    (~/.claude/drop-lanes.json) so `drop_ingress_rules_for_controller()` renders
    each lane's `/s/` rule at the target's REAL port (the in-code literal becomes
    the fallback). Merged into any existing cache so a partial push (some targets
    unreachable this run) never drops a previously-measured port; then PRUNED to
    ``drop_lanes``'s keys so a removed/renamed account leaves no stale entry that
    could later mis-target a reused hostname (review finding). ``drop_lanes`` is
    injected (the caller passes cli_drop_gateway.DROP_LANES) so this stays a leaf.
    Best-effort: any write failure is loud but never fails the push."""
    if not measured:
        return
    try:
        merged = read_drop_lanes_cache()
        merged.update(measured)
        valid = {drop_lanes_cache_key(n, u) for (n, u) in drop_lanes}
        pruned = {k: v for k, v in merged.items() if k in valid}
        write_drop_lanes_cache(pruned)
        print("  drop-lanes cache: wrote %d filedrop port(s) to %s"
              % (len(measured), DROP_LANES_CACHE))
    except Exception as e:  # noqa: BLE001 — best-effort; never fail the push
        print("  ⚠ drop-lanes cache write failed (non-fatal): %r" % e,
              file=sys.stderr)


# #1115: the marker line a target prints so `push` can harvest its persisted
# filedrop port over the deploy ssh session it already opens.
_FILEDROP_PORT_MARKER = "AIRULESET-FILEDROP-PORT"


def filedrop_port_probe_snippet():
    """A pure-shell fragment chained into the deploy ssh command with ``&&``
    RIGHT AFTER ``airuleset.py install`` (#1115). It prints one line
    ``AIRULESET-FILEDROP-PORT <port>`` where <port> is the target's persisted
    ~/.claude/filedrop.port, else the #493 uid-derived default (8788 + uid%1000).

    It prints ONLY the port — NOT the node/user — deliberately: the deploy loop
    already knows WHICH fleet entry it is contacting, so it builds the cache key
    from `_nodename_for_entry(entry)` + the entry's user (the SAME derivation the
    ingress consumer uses), never from the target's `uname -n`. That closes the
    label-vs-`uname` mismatch (the controller box's `uname -n` is `airuleset`, not
    its `@controller` label — review finding): the harvest key can never disagree
    with the DROP_LANES key.

    It is a ``{ … }`` group whose LAST command is ``echo`` (so it ALWAYS exits 0)
    and contains NO ``exit`` (so the ``&& …`` chain to the later post-checks
    continues) — the gh/playwright post-checks `exit` the remote shell, which is
    why this must precede them, never follow. Arithmetic ``%`` binds tighter than
    ``+`` (POSIX): the derived port is ``8788 + (uid % 1000)`` exactly like
    ``filedrop.default_port_for_uid``."""
    return (
        '{ port=$(cat "$HOME/.claude/filedrop.port" 2>/dev/null || true); '
        '[ -n "$port" ] || port=$((8788 + $(id -u) %% 1000)); '
        'echo "%s $port"; }' % _FILEDROP_PORT_MARKER
    )


def parse_filedrop_port(text):
    """The port from a target's ``AIRULESET-FILEDROP-PORT <port>`` marker line in
    a deploy ssh stdout blob, or None (#1115). Ignores noise / malformed lines;
    returns the LAST valid marker if several appear. Never raises."""
    port = None
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == _FILEDROP_PORT_MARKER:
            try:
                port = int(parts[1])
            except ValueError:
                continue
    return port


def drop_ingress_rules_for_controller(drop_lanes, cache=None):
    """Ingress rules for controller-topology drop lanes (#931).

    Returns ``[(hostname, service_url), ...]`` for the controller tunnel's
    multi-ingress config. Called (via cli_drop_gateway's thin wrapper) from
    ``_setup_controller_webterm`` in ``cli_webterm.py`` at install time — the drop
    ingress entries ride the SAME controller tunnel that fronts the webterm
    hostnames. ``drop_lanes`` is injected (the caller passes
    cli_drop_gateway.DROP_LANES) so this stays a self-contained leaf.

    The ``service_url`` uses the lane's ``origin_host`` (the box's tailscale
    IP) + ``port`` — cloudflared on the controller proxies to
    ``http://<tailscale>:<port>`` where the drop server listens.

    #1114: each lane with a known ``filedrop_port`` ALSO gets a ``/s/`` path rule
    ``(host, "^/s/", http://origin_host:filedrop_port)`` emitted BEFORE its drop
    rule, so ``share`` deliveries reach the persistent filedrop service while the
    ephemeral upload endpoint keeps the same host's catch-all. Order matters:
    cloudflared matches ingress top-to-bottom, so the path rule MUST precede the
    no-path drop rule. A lane with ``filedrop_port=None`` emits no ``/s/`` rule
    (``share`` on it falls back to the private URLs).

    #1115: the effective filedrop port PREFERS the push-measured cache
    (``~/.claude/drop-lanes.json`` keyed ``<node>/<user>``); the in-code
    ``DropLane.filedrop_port`` literal is the fallback only. ``cache`` is
    injectable for tests (``{}``/``None`` → read the default path).
    """
    port_cache = read_drop_lanes_cache() if cache is None else cache
    rules = []
    seen = {}  # host -> drop service_url (dedup + conflict detection)
    for (node, user), lane in sorted(drop_lanes.items()):
        if lane.topology != "controller" or not lane.origin_host:
            continue
        svc = "http://%s:%d" % (lane.origin_host, lane.port)
        prev = seen.get(lane.host)
        if prev is not None:
            if prev != svc:
                raise ValueError(
                    "conflicting controller ingress for %s: %s vs %s"
                    % (lane.host, prev, svc))
            continue  # same host + same service — dedup
        seen[lane.host] = svc
        fd_port = port_cache.get(drop_lanes_cache_key(node, user))
        if fd_port is None:
            fd_port = lane.filedrop_port
        if fd_port is not None:                 # #1114: /s/ rule FIRST
            rules.append((lane.host, "^/s/",
                          "http://%s:%d" % (lane.origin_host, fd_port)))
        rules.append((lane.host, svc))
    return rules


# ---------------------------------------------------------------------------
# #1115 Slice C: ONE delivery-channel resolver for every user-facing URL
# producer (share / upload / secret request / secret show), the single labelled
# fallback line they all print, and the report-only conformance fact.
# ---------------------------------------------------------------------------

# The five channel states every producer + the conformance fact share by name
# (so no producer invents its own string and the fixtures assert one vocabulary).
CHANNEL_LIVE = "live"
CHANNEL_NO_LANE = "no-lane"
CHANNEL_PENDING = "pending"
CHANNEL_MARKER_ABSENT = "marker-absent"
CHANNEL_UNREACHABLE = "unreachable"

_CHANNEL_FALLBACK_PHRASE = {
    CHANNEL_NO_LANE: "no lane for this account",
    CHANNEL_PENDING: "lane pending — no Access spec, see #1115 slice B",
    CHANNEL_MARKER_ABSENT: "go-live marker absent",
    CHANNEL_UNREACHABLE: "public host unreachable",
}


def delivery_channel(*, marker_path=None, nodename=None, username=None,
                     probe=None, lane_lookup=None, resolve=None,
                     access_specs=None):
    """``(public_url_base | None, reason)`` — the ONE resolver every user-facing
    URL producer asks before printing a link (#1115 Slice C).

    Reasons (all module constants ``CHANNEL_*``):
      - ``"live"``          -> ``("https://<host>", "live")``: a registered lane
        with a live matching go-live marker (and, when ``probe`` is supplied, the
        public host answered).
      - ``"no-lane"``       -> ``(None, "no-lane")``: no drop lane for this account.
      - ``"pending"``       -> ``(None, "pending")``: a registered ACCESS lane with
        no ``DROP_ACCESS_APPS`` spec — Slice-B go-live is HARD-gated on that spec
        (fail-closed), so the lane can never serve until the spec is added; naming
        it PENDING points the operator at the spec, not a missing marker.
      - ``"marker-absent"`` -> ``(None, "marker-absent")``: a live-capable lane but
        no matching go-live marker on this box yet (the push hasn't reached it).
      - ``"unreachable"``   -> ``(None, "unreachable")``: a live lane whose public
        host did not answer (``probe(host)`` returned falsy).

    Builds on ``cli_drop_gateway.resolve_public_lane_full`` (lazy-imported so this
    stays a self-contained leaf, #433 — no module-level gateway import / cycle).
    Every gateway seam is dependency-injectable for tests: ``lane_lookup``
    (``drop_lane_for_account``), ``resolve`` (``resolve_public_lane_full``),
    ``access_specs`` (``DROP_ACCESS_APPS``). ``probe(host) -> bool`` is OPTIONAL —
    ``share`` passes its origin+HEAD reachability check; ``upload``/``secret`` omit
    it and trust the go-live marker (their pre-#1115 behaviour).

    Fail-safe: any unexpected error resolving the gateway seams degrades to
    ``(None, "no-lane")`` — never a raise (a producer must still print its private
    URLs) and never a wrong public URL.
    """
    try:
        if lane_lookup is None or resolve is None or access_specs is None:
            import cli_drop_gateway as _dg
            if lane_lookup is None:
                lane_lookup = _dg.drop_lane_for_account
            if resolve is None:
                resolve = _dg.resolve_public_lane_full
            if access_specs is None:
                access_specs = _dg.DROP_ACCESS_APPS
        lane = lane_lookup(nodename, username)
        if lane is None:
            return None, CHANNEL_NO_LANE
        if getattr(lane, "access", False) and access_specs.get(lane.host) is None:
            return None, CHANNEL_PENDING
        full = resolve(marker_path=marker_path, nodename=nodename,
                       username=username)
        if full is None:
            return None, CHANNEL_MARKER_ABSENT
        host = full[0]
        base = "https://%s" % host
        if probe is not None and not probe(host):
            return None, CHANNEL_UNREACHABLE
        return base, CHANNEL_LIVE
    except Exception:
        return None, CHANNEL_NO_LANE


def channel_fallback_line(reason, prog="share", detail=None):
    """The single labelled line a producer prints (to stderr, above its private
    URLs on stdout) when it falls back off the public channel (#1115 Slice C).

    Matches what ``share`` printed before this slice — English, a ``<prog>:``
    prefix, the ``see #1115`` pointer — so ``share`` / ``upload`` / ``secret``
    all speak with one voice. ``detail`` overrides the phrase for the unreachable
    case (share passes the concrete `origin down` / `<http code>` / `timeout`)."""
    if reason == CHANNEL_UNREACHABLE:
        why = detail if detail is not None else _CHANNEL_FALLBACK_PHRASE[reason]
        return ("%s: public lane unreachable (%s) — private URLs only, see #1115"
                % (prog, why))
    phrase = _CHANNEL_FALLBACK_PHRASE.get(reason, reason)
    return ("%s: no public lane on this box (%s) — private URLs only, see #1115"
            % (prog, phrase))


def _probe_public_head(url, timeout=3, user_agent="airuleset-conformance"):
    """HTTP status of GET ``url`` WITHOUT following redirects, or ``None`` on a
    connection error / timeout (#1115; mirrors
    ``cli_filedrop_watchdog._public_share_status`` #1114). A NAMED User-Agent:
    Cloudflare's browser-integrity check answers 403 to urllib's default
    ``Python-urllib/3.x`` while the same URL serves 200 (david4, 22.9.). Kept a
    small stdlib probe here rather than importing the heavier filedrop-watchdog
    module into the leaf / the conformance sweep."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None                          # surface the 3xx code, don't follow

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        return opener.open(req, timeout=timeout).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def public_url_channel_fact(*, marker_path=None, nodename=None, username=None,
                            probe=None):
    """The report-only conformance fact for THIS account's public URL channel
    (#1115 Slice C): ``"ok" | "fallback:<reason>" | "broken:<code>"``.

    - Resolve the channel state (no reachability probe — the config state):
      not live -> ``"fallback:<reason>"`` (no-lane / pending / marker-absent).
    - Live: HEAD the account's public ``/s/`` probe path with a named User-Agent:
      200 / 302 -> ``"ok"``; another HTTP code -> ``"broken:<code>"``;
      a connection error / timeout -> ``"broken:unreachable"``.

    ``probe(url) -> status|None`` is injectable for tests (defaults to a real
    HEAD). Never raises — keeping it total here means a fixture can assert every
    branch deterministically."""
    base, reason = delivery_channel(marker_path=marker_path, nodename=nodename,
                                    username=username)
    if base is None:
        return "fallback:%s" % reason
    code = (probe or _probe_public_head)("%s/s/" % base)
    if code in (200, 302):
        return "ok"
    if code is None:
        return "broken:unreachable"
    return "broken:%s" % code
