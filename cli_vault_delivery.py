"""airuleset vault — which URL(s) a one-shot `secret show` endpoint hands out (#1189).

Incident (#1189, 29.9.2026): `secret show` on dev2 printed ONLY the public
`https://drop-dev2.newlevel.media/<token>/` URL, never probed. The controller's
shared tunnel was mid-restart, Cloudflare answered 1033 (tunnel not connected),
and the owner had nothing else to open — the tailscale URL that would have
worked was never printed (public-by-default since #889 dropped it).

`emit_show_urls` closes that: AFTER the endpoint is up it probes the public
lane's token-free `/healthz` (the show server's one unauthenticated route — a
probe never touches the one-shot) with the SAME no-redirect GET `share`/
conformance use (`cli_drop_lanes._probe_public_status`):

- alive (200/204 from the origin, or the Access 302) -> the public URL FIRST,
  then the private (tailscale / encrypted-tunnel) URLs as a LABELLED fallback —
  the #1114 `share` shape;
- dead (a Cloudflare 5xx such as 530 = error 1033, a 502 origin failure, a
  connection error) -> the private URLs ONLY, plus a LOUD degradation line on
  stderr and the shared #1115 `channel_fallback_line`.

The token is the SAME on every line — one endpoint, one value, served once.
A stdlib leaf; the probe is injectable so tests never touch the network.
"""
from __future__ import annotations

import sys

# 200/204 = the tunnel reached the show server (token-only lane: /healthz is a
# 204); 302 = Cloudflare Access redirected to its login — the edge AND the tunnel
# are up (the go-live property `share`/`upload` already rely on).
PUBLIC_ALIVE_CODES = (200, 204, 302)
FALLBACK_LABEL = "   ← záloha, ak verejná URL nejde"


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


def public_probe_url(public_host: str) -> str:
    return "https://%s/healthz" % public_host


def _dead_detail(code) -> str:
    if code is None:
        return "no answer (connection error / timeout)"
    if code == 530:
        return "HTTP 530 — Cloudflare 1033, tunnel not connected"
    return "HTTP %s" % code


def emit_show_urls(public_host, public_line, private_lines, *, prog="secret show",
                   probe=None, out=None, err=None) -> str:
    """Print the URLs for a live endpoint. `public_line` is the labelled public
    URL line (None when there is no public lane — the caller then prints its own
    #1115 fallback line). `private_lines` are the labelled private URL lines that
    answer (loopback never belongs here — the owner cannot reach it). Returns
    `"public"` | `"degraded"` | `"private"` for the caller's log."""
    import cli_drop_lanes as _dl
    out = out or sys.stdout
    err = err or sys.stderr
    if not public_host:
        for line in private_lines:
            print(line, file=out)
        return "private"
    code = (probe or _probe)(public_probe_url(public_host))
    if code in PUBLIC_ALIVE_CODES:
        print(public_line, file=out)
        for line in private_lines:
            print(line + FALLBACK_LABEL, file=out)
        return "public"
    for line in private_lines:
        print(line, file=out)
    print("%s: !!! DEGRADED — public URL https://%s/ is DEAD (%s); NOT printed. "
          "Use the private URL%s instead (#1189)."
          % (prog, public_host, _dead_detail(code),
             "" if private_lines else " — NONE available on this box"), file=err)
    print(_dl.channel_fallback_line(_dl.CHANNEL_UNREACHABLE, prog=prog,
                                    detail=_dead_detail(code)), file=err)
    return "degraded"


def _probe(url):
    import cli_drop_lanes as _dl
    return _dl._probe_public_status(url, user_agent="airuleset-secret")
