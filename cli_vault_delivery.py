"""airuleset — which URL a one-shot drop endpoint hands out (#1189, #1192).

#1192 (owner ROZHODNUTÉ 30.9.2026): owner-facing links are PUBLIC ONLY. „vadi
mi ze stale vsade pchas tailscale ktore je bezpecnostne riziko“ — so the #1189
always-printed tailscale fallback line is gone, back to the #1115 ruling (ONE
public TLS channel). `secret show`, `secret request` and `upload` go through
`emit_urls`; `share` uses `select_lane` + `refusal_line` for the same contract:

- the public lane answers (its token-free `/healthz`, probed with the no-redirect
  GET of `cli_drop_lanes._probe_public_status`, gets 200/204) -> ONE https line;
- 302 -> Cloudflare Access answered at the EDGE (its `/healthz` bypass app is
  not applied yet), so the tunnel is not verified: the https line + a note;
- no lane, 530 (error 1033), a 5xx, a timeout, or the tunnel origin not
  answering locally -> NO URL, a loud Slovak+English line naming `--private`,
  exit non-zero (the caller stops its endpoint);
- `--private` -> the pre-existing tailscale/LAN path, and only then.

The #1189 incident (a dead tunnel, a dead link handed out unprobed) stays fixed
by the probe; a dead link is never handed out, and neither is tailscale by
default. A stdlib leaf; the probe is injectable so tests never touch the network.
"""
from __future__ import annotations

import sys

PUBLIC_REACHED_CODES = (200, 204)     # the request reached the endpoint itself
PUBLIC_ACCESS_CODE = 302              # Access login redirect — edge only


def public_lane(args=None):
    """(public_host, port, bind_ip) for the public-TLS drop lane, or
    (None, None, None). Re-exported by cli_vault as `_secret_public_lane`.

    #889: public HTTPS is the DEFAULT for every account. Delegates to
    `cli_drop_gateway.resolve_public_lane_full` which returns the lane whenever
    a registered lane + live marker exist. #931: bind_ip is "127.0.0.1" for
    local-topology lanes (tunnel on this box) or the tailscale IP for
    controller-topology lanes (tunnel on the controller).
    """
    import cli_drop_gateway as _dg
    lane = _dg.resolve_public_lane_full()
    if lane:
        return lane[0], lane[1], lane[2]
    return None, None, None


def public_url_line(host, token):
    """The advertised public HTTPS URL line (delegates to cli_drop_gateway)."""
    import cli_drop_gateway as _dg
    return _dg.public_url_line(host, token)


def public_bind_ips(bind_ip, encrypted_private, lane_lookup=None):
    """The addresses a PUBLIC-lane endpoint binds: the tunnel origin `bind_ip`
    plus the encrypted private IPs (the #1189 bind set, left untouched by #1192
    — only what is PRINTED changed; a tailscale URL is printed only on the
    separate `--private` path).

    Exception: a LOCAL-topology (loopback origin) lane behind Cloudflare Access.
    There loopback is the endpoint's whole exposure and Access its outer gate;
    a tailnet bind would add a path that skips Access (review finding), so it
    stays loopback-only. A controller-topology origin is already a tailscale IP,
    so the fallback adds no new exposure there. Unknown lane -> loopback-only
    (the fail-closed direction)."""
    extra = [ip for ip in encrypted_private if ip != bind_ip]
    if not str(bind_ip).startswith("127."):
        return [bind_ip] + extra
    try:
        if lane_lookup is None:
            import cli_drop_gateway as _dg
            lane_lookup = _dg.drop_lane_for_account
        lane = lane_lookup()
    except Exception as e:           # never lose the endpoint over a lookup error
        print("secret: drop-lane lookup failed (%s) — loopback-only bind"
              % type(e).__name__, file=sys.stderr)
        return [bind_ip]
    if lane is None or getattr(lane, "access", True):
        return [bind_ip]
    return [bind_ip] + extra


def public_probe_url(public_host: str) -> str:
    return "https://%s/healthz" % public_host


def dead_detail(code) -> str:
    if code is None:
        return "no answer (connection error / timeout)"
    if code == 530:
        return "HTTP 530 — Cloudflare 1033, tunnel not connected"
    return "HTTP %s" % code


def refusal_line(prog, why):
    """The ONE loud line a producer prints when it hands out NO URL (#1192)."""
    return ("%s: !!! ŽIADNA URL — verejný odkaz nefunguje (%s). Tailscale/LAN "
            "odkaz sa sám nevypisuje (bezpečnostné riziko); ak ho naozaj treba, "
            "spusti znova s --private. / NO URL printed — the public lane is "
            "unusable (%s); tailscale/LAN URLs are opt-in only: re-run with "
            "--private (#1192)." % (prog, why, why))


def select_lane(prog, lane, private):
    """The lane a producer delivers on (#1192). `--private` -> no public lane
    (the pre-existing tailscale/LAN path, opt-in only). No lane and no
    `--private` -> the loud refusal and exit 1 BEFORE any endpoint starts."""
    if private:
        return None, None, None
    if lane and lane[0]:
        return lane
    import cli_drop_lanes as _dl
    _, reason = _dl.delivery_channel()
    phrase = _dl._CHANNEL_FALLBACK_PHRASE.get(reason, reason)
    print(refusal_line(prog, "no public lane on this box: %s" % phrase),
          file=sys.stderr)
    raise SystemExit(1)


def emit_urls(prog, public_host, token, ips, private_line, is_live, *,
              private=False, origin_ip=None, log=None, probe=None,
              out=None, err=None):
    """Print the URL(s) for a live endpoint and return the channel used:
    `"public"` | `"public-unverified"` | `"private"` | `"refused"`.

    #1192: ONE public URL, or nothing. `private=True` (`--private`) prints the
    live private URLs (`private_line(ip)`, health-checked by `is_live(ip)`) and
    never probes. Otherwise the public lane must be alive: `origin_ip` (the
    tunnel-origin bind) answers locally AND the `/healthz` probe gets 200/204
    (302 = Access answered at the edge: printed with a note). A missing or dead
    lane prints NO URL and returns `"refused"` — the caller stops its endpoint
    and exits non-zero. `log(event)` records the dead lane (a bare event word)."""
    out = out or sys.stdout
    err = err or sys.stderr
    if private:
        lines = [private_line(ip) for ip in ips if is_live(ip)]
        for line in lines:
            print(line, file=out)
        if lines:
            return "private"
        print(refusal_line(prog, "no private address answers"), file=err)
        return "refused"
    if not public_host:
        print(refusal_line(prog, "no public lane on this box"), file=err)
        return "refused"
    origin_ok = origin_ip is None or is_live(origin_ip)
    code = (probe or _probe)(public_probe_url(public_host)) if origin_ok else None
    if origin_ok and (code in PUBLIC_REACHED_CODES or code == PUBLIC_ACCESS_CODE):
        print(public_url_line(public_host, token), file=out)
        if code in PUBLIC_REACHED_CODES:
            return "public"
        print("%s: public lane is behind Cloudflare Access and its /healthz is "
              "not bypassed yet — the tunnel is NOT verified from here. If the "
              "link shows error 1033, re-run with --private (#1189/#1192)."
              % prog, file=err)
        return "public-unverified"
    detail = dead_detail(code) if origin_ok else "tunnel origin %s down" % origin_ip
    print(refusal_line(prog, "public URL https://%s/ is DEAD — %s"
                       % (public_host, detail)), file=err)
    if log is not None:
        log("public-lane-dead")
    return "refused"


def _probe(url):
    import cli_drop_lanes as _dl
    return _dl._probe_public_status(url, user_agent="airuleset-secret")
