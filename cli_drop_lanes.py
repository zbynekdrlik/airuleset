"""airuleset — the GENERATED drop-lane registry engine + push-measured filedrop
port cache + deploy-loop harvest helpers (#1115, split out of cli_drop_gateway
for the 1000-line file cap, #993 area review).

This is a SELF-CONTAINED LEAF (#433): AT MODULE LEVEL it imports ONLY
``cli_fleet`` (a pure zero-import data leaf) + stdlib — NEVER a module-level
``cli_drop_gateway`` or ``airuleset`` import (that would be the load-time cycle:
``cli_drop_gateway`` imports THIS at its top). ``delivery_channel`` (#1115 slice
C) uses a FUNCTION-LOCAL lazy ``import cli_drop_gateway`` — cycle-safe (resolved
at call time, after both modules are loaded) and injectable-seamed for tests. The
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

# #1115 slice G: the LOCAL controller account. The controller box (real nodename
# `airuleset`, per _NODENAME_OVERRIDE above) runs `push` + the owner's supervisor
# session as the unix account `airuleset` — the box's real hostname IS the local
# account name. That account is NOT a REMOTE_HOSTS deploy target (push never ssh's
# to itself), so `build_drop_lanes` never generates it from the fleet loop; it is
# derived at the END of the build from the controller box's OWN fleet entry (the
# claudy@controller service account is the only REMOTE_HOSTS row on the controller
# box and carries the controller tailscale origin), with a DISTINCT hostname so it
# never collides with claudy's drop-airuleset lane. The nodename literal comes
# from the existing override (no NEW hand literal for the identity); the hostname
# is a new lane fact.
CONTROLLER_NODENAME = _NODENAME_OVERRIDE["claudy@controller"]
CONTROLLER_LOCAL_DROP_HOST = "drop-controller.newlevel.media"

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
    # #1115 slice G: the LOCAL controller account (not a REMOTE_HOSTS target, so
    # never reached by the fleet loop — read by _add_controller_local_lane).
    ("airuleset", "airuleset"): 8891,
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

    # #1115 slice G: RESERVE the LOCAL controller account's fixed port BEFORE the
    # fleet loop so `_next_free_port` can never hand it to a future no-fixed-port
    # account and silently displace the controller-local lane (which is injected
    # AFTER the loop by `_add_controller_local_lane`). Review finding (port-steal).
    _ctrl_port = _GENERATED_DROP_PORTS.get((CONTROLLER_NODENAME, CONTROLLER_NODENAME))
    if _ctrl_port is not None:
        used_ports.add(_ctrl_port)

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

    # #1115 slice G: the LOCAL controller account is not a REMOTE_HOSTS target, so
    # the fleet loop above never generated it — derive it here from the controller
    # box's own fleet entry. NON-FATAL like the loop (logs + skips, never raises).
    _add_controller_local_lane(lanes, remote_hosts, used_ports, drop_lane_cls,
                               controller_tunnel_uuid)
    return lanes


def _controller_origin_ip(remote_hosts):
    """The controller box's tailscale origin IP for the LOCAL-account lane (#1115
    slice G), derived from the controller's OWN fleet entry — the claudy@controller
    service account is the only REMOTE_HOSTS row on the controller box, so its
    `host` is the controller tailscale IP. No separate hand literal. Returns None
    when no such (non-paused, tailscale) entry exists."""
    for entry in remote_hosts:
        if cli_fleet.is_paused(entry):
            continue
        if _nodename_for_entry(entry) == CONTROLLER_NODENAME \
                and _is_tailscale_host(entry.get("host")):
            return entry["host"]
    return None


def _add_controller_local_lane(lanes, remote_hosts, used_ports, drop_lane_cls,
                               controller_tunnel_uuid):
    """Inject the LOCAL controller account's drop lane (#1115 slice G).

    Keyed ``(CONTROLLER_NODENAME, CONTROLLER_NODENAME)`` (the box's real hostname
    IS the local account name), a DISTINCT hostname ``CONTROLLER_LOCAL_DROP_HOST``
    (never claudy's ``drop-airuleset``), controller topology riding the ONE
    controller tunnel, origin = the controller's own tailscale IP, access-gated
    (its owner-only spec is derived by ``generated_access_specs`` like every other
    generated access lane). ``filedrop_port=None`` — the push-measured value
    arrives via the ~/.claude/drop-lanes.json cache (harvested locally, see
    ``harvest_local_controller_filedrop_port``). NON-FATAL: a missing controller
    entry / missing port / port collision LOGS loudly and skips (the same
    never-raise contract as ``build_drop_lanes`` — this runs at import)."""
    key = (CONTROLLER_NODENAME, CONTROLLER_NODENAME)
    if key in lanes:
        return  # a seed already covers it (never today) — never overwrite
    origin = _controller_origin_ip(remote_hosts)
    port = _GENERATED_DROP_PORTS.get(key)
    if origin is None:
        print("#1115 slice G WARNING: no controller fleet entry with a tailscale "
              "origin — the LOCAL controller account gets NO drop lane.",
              file=sys.stderr)
        return
    if port is None:
        print("#1115 slice G WARNING: no _GENERATED_DROP_PORTS entry for the LOCAL "
              "controller account — NO drop lane.", file=sys.stderr)
        return
    # A REAL collision = some OTHER lane already holds this port. `used_ports`
    # now RESERVES this port for us (build_drop_lanes seeds it before the loop),
    # so we check the actual lane ports, never the reservation set (else we would
    # falsely skip our own reserved port). With the reservation in place a seed is
    # the only way this could ever be non-empty.
    if any(ln.port == port for ln in lanes.values()):
        print("#1115 slice G WARNING: controller-local drop port %d already held "
              "by another lane — NO drop lane (fix _GENERATED_DROP_PORTS)." % port,
              file=sys.stderr)
        return
    used_ports.add(port)
    lanes[key] = drop_lane_cls(
        host=CONTROLLER_LOCAL_DROP_HOST, port=port,
        tunnel_uuid=controller_tunnel_uuid,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account=None,
        topology="controller", origin_host=origin,
        filedrop_port=None)


def generated_access_specs(lanes, existing_specs, owner_emails,
                           *, session_duration="24h"):
    """Owner-only ``DROP_ACCESS_APPS`` specs for every GENERATED access lane
    (#1115 slice D) — ONE derivation from the generated lane list, never a spec
    hand-typed per account.

    For every ``access=True`` lane whose ``host`` is not already in
    ``existing_specs``, produce a spec whose shape matches the hand-authored gk
    spec EXACTLY — ``{hostname, name, allowed_emails, session_duration}`` — with
    ``allowed_emails`` = the owner-only include (decision 5787094428). The name is
    ``"drop — <stem>"``, the host with its ``drop-`` prefix and ``.newlevel.media``
    suffix stripped. A lane already covered by ``existing_specs`` is SKIPPED, so
    the hand-authored specs (gk, david1, david2-4, dominika) stay authoritative
    and byte-identical — the caller merges this dict UNDER them.

    Each spec owns a fresh ``allowed_emails`` list (never a shared reference).
    Pure + dependency-injected (a self-contained leaf, #433): no import of the
    gateway, no I/O.
    """
    specs = {}
    for lane in lanes.values():
        if not getattr(lane, "access", False):
            continue
        host = lane.host
        if host in existing_specs or host in specs:
            continue
        stem = host[len("drop-"):] if host.startswith("drop-") else host
        stem = stem.rsplit(".newlevel.media", 1)[0]
        specs[host] = {
            "hostname": host,
            "name": "drop — %s" % stem,
            "allowed_emails": list(owner_emails),
            "session_duration": session_duration,
        }
    return specs



def add_webterm_readers(specs, lanes, remote_hosts, readers):
    """Add every webterm reader to the drop lane of each account they can open
    (#1115 reopen, owner 2026-09-23: whoever the webterm lets open an account
    must also pass that account's drop lane — Marek, Dominika and David were
    missing). ``readers`` is ``[(emails, inventory_entries)]`` — per webterm
    human, their login emails and the ``{user, host}`` entries their dashboard
    connects to. An entry maps to its fleet account (same user + host in
    ``remote_hosts``) and then to that account's Access-gated lane; an entry with
    no fleet account or no Access lane is skipped. Mutates ``specs`` in place,
    appending each missing email once (the existing order is kept). Pure data,
    no I/O (a self-contained leaf, #433)."""
    for emails, entries in readers:
        for e in entries:
            for entry in remote_hosts:
                if entry.get("user") == e.get("user") and entry.get("host") == e.get("host"):
                    lane = lanes.get((_nodename_for_entry(entry), entry.get("user", "")))
                    spec = specs.get(lane.host) if lane is not None and lane.access else None
                    if spec is not None:
                        spec["allowed_emails"] += [m for m in emails
                                                   if m not in spec["allowed_emails"]]
                    break


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


def harvest_local_controller_filedrop_port(measure_fn=None, drop_lanes=None):
    """Measure the LOCAL controller account's persistent filedrop port and merge
    it into the SAME push-measured cache slice A uses (#1115 slice G).

    The controller-local account is not a deploy target, so the push ssh leg
    (``filedrop_port_probe_snippet``) never harvests its port — it is measured
    directly on the controller (no ssh) and cached keyed
    ``drop_lanes_cache_key(CONTROLLER_NODENAME, CONTROLLER_NODENAME)`` so
    ``drop_ingress_rules_for_controller`` renders the ``/s/`` rule for
    ``drop-controller.newlevel.media`` at the account's real filedrop port.

    ``measure_fn`` is injectable for tests; the default reads
    ``filedrop.persisted_port()`` (a function-local import — ``filedrop`` imports
    neither cli_drop nor cli_fleet, so no cycle) falling back to the #493
    uid-derived default. ``drop_lanes`` is injected (default
    ``cli_drop_gateway.DROP_LANES``) so this stays a leaf and the prune keeps the
    controller-local key. Best-effort: no controller-local lane / a falsy port /
    any error is a no-op, never raises. Returns the port cached, or None."""
    if drop_lanes is None:
        import cli_drop_gateway as dg  # function-local: cycle-safe leaf pattern
        drop_lanes = dg.DROP_LANES
    key = (CONTROLLER_NODENAME, CONTROLLER_NODENAME)
    if key not in drop_lanes:
        return None
    try:
        if measure_fn is None:
            import filedrop
            port = filedrop.persisted_port() or filedrop.default_port_for_uid()
        else:
            port = measure_fn()
        if not port:
            return None
        port = int(port)
        persist_measured_filedrop_ports(
            {drop_lanes_cache_key(*key): port}, drop_lanes)
        return port
    except Exception as e:  # noqa: BLE001 — best-effort; never fail the install
        print("#1115 slice G WARNING: local controller filedrop-port harvest "
              "failed (non-fatal): %r" % e, file=sys.stderr)
        return None


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


def _lane_go_live_eligible(lane, access_specs):
    """A lane is ELIGIBLE for go-live iff it is token-only (access=False) OR it is
    an access lane that ALREADY has a DROP_ACCESS_APPS spec (#1115 slice B review,
    both reviewers): an access lane with NO spec must NEVER have its DNS CNAME
    created — a proxied CNAME with no Access app in front is publicly routable and
    UNPROTECTED (the #983 RED-1 class). Such a lane is PENDING go-live (its spec
    is an owner-provided go-live data step), NOT a failure, and gets neither DNS
    nor a marker until the spec lands.

    #1115 slice E: this is the ONE shared eligibility predicate — the controller
    ingress renderer (below) and the go-live reconcile (`cli_drop_golive`, which
    re-exports this name) both call it, never a copy. It lives in this pure LEAF
    (only ``lane`` + a specs dict; no imports) so the leaf's ingress renderer can
    use it without importing ``cli_drop_gateway`` at module level."""
    if not lane.access:
        return True
    return access_specs.get(lane.host) is not None


def _repo_is_worktree_checkout():
    """True when THIS checkout is a git worktree (#1115 / #972).

    A LIVE Cloudflare write must never originate from a worktree copy of the
    go-live code: the 23.9. incident (comment 5787949642) created 12 proxied
    CNAMEs from a worktree lane with the controller's real tokens, before any
    Access app existed. Reuses ``airuleset._is_worktree_repo_dir`` (the #972
    install/push predicate) via a FUNCTION-LOCAL ``import airuleset`` — the
    sanctioned cycle-safe pattern (this pure leaf never module-level-imports
    airuleset). Fails CLOSED: if the checkout cannot be classified (import/attr
    error), returns True (treat as a worktree, REFUSE the live write) — a
    degraded private-only lane is safe; a stray live write is the incident.

    #1115 slice F: MOVED here from cli_drop_golive (re-exported there) so the
    three manual live-write entries (cmd_drop_gateway, cmd_webterm_access,
    ensure_managed_records) re-use ONE definition, never a copy."""
    try:
        import airuleset
        return airuleset._is_worktree_repo_dir(airuleset.REPO_DIR)
    except Exception:
        return True


def _refuse_worktree_live_write(what, out=None):
    """The LOUD refusal line for a live Cloudflare write attempted from a git
    worktree checkout (#1115 / #972), shared by every live-write entry — ONE
    definition, re-used never copied. No API call is made by the caller after
    this. ``out`` defaults to stdout."""
    print("airuleset: REFUSING a LIVE %s from a git worktree checkout — live "
          "DNS/Access writes run ONLY from the main checkout on the controller "
          "(#1115/#972). No Cloudflare API call was made." % what,
          file=out if out is not None else sys.stdout)


def drop_ingress_rules_for_controller(drop_lanes, cache=None, access_specs=None):
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

    #1115 slice E: a lane that ``_lane_go_live_eligible`` rejects (an access lane
    with NO ``DROP_ACCESS_APPS`` spec — PENDING go-live) gets NO ingress rule,
    neither ``/s/`` nor drop. Once DNS exists, the tunnel ingress is the only
    guard; routing a host whose Access app is missing serves the origin
    unprotected (the 23.9. exposure incident, #1115). A PENDING host then answers
    the tunnel's catch-all 404 even if a leftover DNS record exists. ``access_specs``
    is injectable for tests; when None it lazily reads ``cli_drop_gateway``'s
    ``DROP_ACCESS_APPS`` (a FUNCTION-LOCAL import — the cycle-safe pattern the
    leaf uses everywhere, so this module stays import-pure).
    """
    if access_specs is None:
        import cli_drop_gateway as dg  # function-local: cycle-safe leaf pattern
        access_specs = dg.DROP_ACCESS_APPS
    port_cache = read_drop_lanes_cache() if cache is None else cache
    rules = []
    seen = {}  # host -> drop service_url (dedup + conflict detection)
    for (node, user), lane in sorted(drop_lanes.items()):
        if lane.topology != "controller" or not lane.origin_host:
            continue
        if not _lane_go_live_eligible(lane, access_specs):
            continue  # #1115 slice E: PENDING lane (access, no spec) → no rule
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
# #1115 slice D: the fail-safe branch's reason family. The resolver never raises
# (a producer must still print its private URLs), but a REAL breakage must not
# hide behind a benign "no-lane" on the conformance surface (slice-C review B).
# The reason carries the exception CLASS name only — never the message, which can
# carry a path / host / credential — as "error:<ClassName>".
CHANNEL_ERROR = "error"

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
      - ``"marker-absent"`` -> ``(None, "marker-absent")``: a live-capable lane for
        which ``resolve_public_lane_full`` returned ``None`` — normally no go-live
        marker yet (the push hasn't reached it), but it ALSO covers a stale/foreign
        marker (host mismatch) and a controller lane with no ``origin_host``
        (fail-closed). All three are "not live via a marker"; the reason string
        names the common case.
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
    ``(None, "error:<ExceptionClassName>")`` — never a raise (a producer must
    still print its private URLs) and never a wrong public URL. The reason carries
    the exception CLASS name only (never its message, which can leak a
    path/host/secret); conformance collapses it to ``broken:error`` (#1115 slice
    D) so a real resolver breakage is visible instead of a benign ``no-lane``.
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
    except Exception as exc:
        # Fail-safe: never raise, never a wrong public URL. Report the exception
        # CLASS name only (never the message — it can leak a path/host/secret)
        # so a resolver breakage is visible as broken:error, not a benign no-lane.
        return None, "%s:%s" % (CHANNEL_ERROR, type(exc).__name__)


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


def _probe_public_status(url, timeout=3, user_agent="airuleset-conformance"):
    """HTTP status of a GET on ``url`` WITHOUT following redirects, or ``None`` on
    a connection error / timeout (#1115; mirrors
    ``cli_filedrop_watchdog._public_share_status`` #1114 — a GET, not a HEAD, so a
    filedrop origin that does not implement HEAD is not mis-read as 405). A NAMED
    User-Agent: Cloudflare's browser-integrity check answers 403 to urllib's
    default ``Python-urllib/3.x`` while the same URL serves 200 (david4, 22.9.).
    Kept a small stdlib probe here rather than importing the heavier
    filedrop-watchdog module into the leaf / the conformance sweep (a deliberate
    leaf-purity duplication, #433)."""
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
    - Live: probe (GET, no redirect follow) the account's public ``/s/`` path with
      a named User-Agent and read REACHABILITY from the status:
        * ``200`` / ``302`` (Access login) / ``404`` -> ``"ok"``. A bare
          token-less ``/s/`` legitimately 404s at the filedrop origin on a
          TOKEN-ONLY lane (no Access edge to 302; proven by
          ``test_share_public_lane_1114.test_s_alone_is_404``) — the origin
          ANSWERED, so the channel is reachable. Treating that 404 as ``ok``
          avoids a permanent false ``broken:404`` on every token-only box's status
          row (slice-C review, both fresh reviewers). The design named 200/302 as
          the expected codes; 404 is the third reachable-but-empty case.
        * any other HTTP code (a real 5xx origin failure, an anomalous 4xx on an
          access lane) -> ``"broken:<code>"``.
        * a connection error / timeout -> ``"broken:unreachable"``.

    ``probe(url) -> status|None`` is injectable for tests (defaults to a real
    GET). Never raises — keeping it total here means a fixture can assert every
    branch deterministically."""
    base, reason = delivery_channel(marker_path=marker_path, nodename=nodename,
                                    username=username)
    if base is None:
        # #1115 slice D: the fail-safe error family is a BREAKAGE, not a benign
        # fallback. Collapse "error:<ClassName>" to a single "broken:error" token
        # so the status surface stays one word (the class name lives in the
        # delivery_channel reason for a caller that logs it).
        if reason == CHANNEL_ERROR or reason.startswith(CHANNEL_ERROR + ":"):
            return "broken:%s" % CHANNEL_ERROR
        return "fallback:%s" % reason
    code = (probe or _probe_public_status)("%s/s/" % base)
    if code in (200, 302, 404):
        return "ok"
    if code is None:
        return "broken:unreachable"
    return "broken:%s" % code
