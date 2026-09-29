"""airuleset vault — which URL(s) a one-shot vault endpoint hands out (#1189).

Incident (#1189, 29.9.2026): `secret show` on dev2 printed ONLY the public
`https://drop-dev2.newlevel.media/<token>/` URL, never probed. The controller's
shared tunnel was mid-restart, Cloudflare answered 1033 (tunnel not connected),
and the owner had nothing else to open — the tailscale URL that would have
worked was never printed (public-by-default since #889 dropped it).

Both vault endpoints (`secret show` and `secret request`, same defect, same
shape) now go through `emit_urls`. AFTER the endpoint is up it probes the
public lane's token-free `/healthz` (the endpoint's one unauthenticated route —
a probe never touches the one-shot) with the SAME no-redirect GET `share`/
conformance use (`cli_drop_lanes._probe_public_status`):

- 200/204 -> the tunnel reached the endpoint: public URL FIRST, then the private
  (tailscale / encrypted-tunnel) URLs as a LABELLED fallback — the #1114 shape;
- 302 -> Cloudflare Access answered at the EDGE, before the tunnel, so a dead
  tunnel still looks like this (review finding): public first + fallback, AND a
  stderr note that the tunnel is NOT verified from here;
- anything else (530 = error 1033, a 502 origin failure, a connection error),
  or the tunnel-origin address itself not answering locally -> the private URLs
  ONLY, a LOUD degradation line, the shared #1115 line. With NO private URL the
  public one is still printed as the last resort (a one-shot 3 s probe can be a
  false negative; zero URLs is never better).

The token is the SAME on every line — one endpoint, one value, served once.
A stdlib leaf; the probe is injectable so tests never touch the network.
"""
from __future__ import annotations

import sys

PUBLIC_REACHED_CODES = (200, 204)     # the request reached the endpoint itself
PUBLIC_ACCESS_CODE = 302              # Access login redirect — edge only
FALLBACK_LABEL = "   ← záloha, ak verejná URL nejde"
LAST_RESORT_LABEL = "   [POSLEDNÁ MOŽNOSŤ — tunel neodpovedá]"


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
    plus the encrypted private IPs, so the tailscale URL works as the fallback.

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


def _dead_detail(code) -> str:
    if code is None:
        return "no answer (connection error / timeout)"
    if code == 530:
        return "HTTP 530 — Cloudflare 1033, tunnel not connected"
    return "HTTP %s" % code


def emit_urls(prog, public_host, token, ips, private_line, is_live, *,
              origin_ip=None, fallback_reason=None, log=None, probe=None,
              out=None, err=None):
    """Print the URLs for a live endpoint and return the channel used:
    `"public"` | `"public-unverified"` | `"degraded"` | `"private"`.

    `private_line(ip)` labels one private URL, `is_live(ip)` health-checks it
    (loopback is never offered — the owner cannot reach it); `origin_ip` is the
    tunnel-origin bind, which must answer before the public URL is offered. No public lane ->
    the private URLs plus the #1115 reason line (`fallback_reason`). `log(event)`
    records a degradation in the vault log (a bare event word, never a value)."""
    import cli_drop_lanes as _dl
    out = out or sys.stdout
    err = err or sys.stderr
    private = [private_line(ip) for ip in ips
               if not str(ip).startswith("127.") and is_live(ip)]
    if not public_host:
        for line in private:
            print(line, file=out)
        print(_dl.channel_fallback_line(fallback_reason or _dl.CHANNEL_NO_LANE,
                                        prog=prog), file=err)
        return "private"
    origin_ok = origin_ip is None or is_live(origin_ip)
    code = (probe or _probe)(public_probe_url(public_host)) if origin_ok else None
    if origin_ok and (code in PUBLIC_REACHED_CODES or code == PUBLIC_ACCESS_CODE):
        print(public_url_line(public_host, token), file=out)
        for line in private:
            print(line + FALLBACK_LABEL, file=out)
        if code in PUBLIC_REACHED_CODES:
            return "public"
        print("%s: public lane is behind Cloudflare Access — its tunnel is NOT "
              "verified from here (Access answers before the tunnel). If it "
              "shows error 1033: %s (#1189)."
              % (prog, "use the tailscale URL" if private else
                 "NO private fallback exists on this box"), file=err)
        return "public-unverified"
    detail = _dead_detail(code) if origin_ok else "tunnel origin %s down" % origin_ip
    for line in private:
        print(line, file=out)
    if not private:                      # never zero URLs: the last resort
        print(public_url_line(public_host, token) + LAST_RESORT_LABEL, file=out)
    print("%s: !!! DEGRADED — public URL https://%s/ is DEAD (%s); %s (#1189)."
          % (prog, public_host, detail, "use the private URL instead" if private
             else "NO private URL on this box, public printed as a last resort"),
          file=err)
    if private:                          # the #1115 line says "private URLs only"
        print(_dl.channel_fallback_line(_dl.CHANNEL_UNREACHABLE, prog=prog,
                                        detail=detail), file=err)
    if log is not None:
        log("public-lane-dead")
    return "degraded"


def _probe(url):
    import cli_drop_lanes as _dl
    return _dl._probe_public_status(url, user_agent="airuleset-secret")
