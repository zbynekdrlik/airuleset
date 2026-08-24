# drop-gateway go-live runbook (#664)

The public-TLS drop lane lets `secret request` / `secret show` / `upload` print
ONE simple public HTTPS URL on a box with no tailscale (spinbike-vps) or for a
no-tailscale client (David's laptop → david1/2@subdev) — replacing all `ssh -L`
gymnastics. The CODE ships ready; this is the ONE-TIME per-box go-live.

The CLI's public lane is available IFF `~/.cloudflared/airuleset-drop.conf`
(the marker) exists on the box, which `drop-gateway --apply` writes only after
the tunnel ingress is in place. So nothing prints a dead public URL before
go-live.

## Prerequisites (already true today, verify)

- The box already runs a cloudflared tunnel fronting `newlevel.media` subdomains:
  - **spinbike** — `spinbike-tunnel.service` (SYSTEM unit), tunnel UUID
    `4093c494-b31d-4eb7-8fcb-6c5948f5d4b2`, config `~/.cloudflared/config.yml`.
  - **subdev** (david) — `webterm-david-tunnel.service` (`--user` unit), tunnel
    UUID `1564fe31-a95f-4053-93d4-baff2b8a6e97`, config `~/.cloudflared/config.yml`.
- The drop hostnames are **flat single-level** subdomains, so the
  `*.newlevel.media` Universal SSL cert covers them (a 2-level host would have no
  valid edge cert):
  - spinbike → `drop-spinbike.newlevel.media` (token-only TLS, owner box).
  - subdev  → `drop-david.newlevel.media` (Cloudflare Access + token).

## Steps (run ON the box, as the tunnel's own account)

1. **DNS** — add a proxied CNAME (this is a manual Cloudflare step; there is no
   DNS helper in the repo, same as #635's DNS cutover):
   - `drop-spinbike.newlevel.media  CNAME  4093c494-b31d-4eb7-8fcb-6c5948f5d4b2.cfargotunnel.com` (proxied / orange)
   - `drop-david.newlevel.media     CNAME  1564fe31-a95f-4053-93d4-baff2b8a6e97.cfargotunnel.com` (proxied / orange)
   Use the `~/.secrets/cloudflare-newlevel*` token (capability-probe `GET /zones`,
   never `/user/tokens/verify` for an account-owned token — see the
   `cloudflare-api-tokens` skill).

2. **Access (subdev/david only)** — the drop-david host needs its own Access app
   (deny-by-default email OTP). It reuses `cli_webterm_access.apply_profile` and
   the `~/.secrets/cloudflare-newlevel-access` token; `drop-gateway --apply`
   reconciles it automatically for the access-gated lane. spinbike has no Access
   (token-only TLS).

3. **Ingress + tunnel restart + marker** — the automated part:
   ```
   airuleset.py drop-gateway            # DRY-RUN: prints the plan, changes nothing
   airuleset.py drop-gateway --apply    # augment config (idempotent, preserves every
                                        # existing ingress) + Access + restart + marker
   ```
   The augmentation inserts the drop ingress BEFORE the config's catch-all `404`,
   preserving every existing entry (spinbike's config also serves the live
   `spinbike.sk` website — it is never clobbered). On spinbike the restart is
   `sudo -n systemctl restart spinbike-tunnel.service` (SYSTEM unit); on subdev
   it is `systemctl --user restart webterm-david-tunnel.service`.

4. **Sibling accounts (subdev only)** — the marker (`~/.cloudflared/airuleset-drop.conf`)
   is per-unix-account, but the loopback origin `127.0.0.1:8828` is box-wide. So a
   SIBLING account that should also use the lane (e.g. `david2` alongside `david1`)
   needs its OWN marker. `drop-gateway --apply` in that account fails (no tunnel
   config there), so seed the marker directly:
   ```
   # as david2 on subdev, after david1's go-live above:
   printf 'host=drop-david.newlevel.media\nport=8828\n' > ~/.cloudflared/airuleset-drop.conf
   chmod 600 ~/.cloudflared/airuleset-drop.conf
   ```
   (spinbike is single-account, so this step does not apply there.)

5. **Verify**:
   ```
   curl -sI https://drop-<box>.newlevel.media/         # 302 -> Access login (david) / reachable
   airuleset.py upload --public                          # prints https://drop-<box>.newlevel.media/<token>/
   ```
   On a no-tailscale box `--public` is the default anyway; on a tailscale box it
   forces the public lane.

**Survives re-installs (subdev):** the subdev `webterm-david-tunnel` provisioner
rewrites `config.yml` from scratch on every install, which would delete the drop
ingress. `cmd_install` now re-asserts the ingress (idempotently, restarting the
tunnel) whenever the marker is present, so a `push`/`install` after go-live keeps
the lane working — no manual re-apply needed.

**If `--apply` fails:** a non-zero exit is now surfaced (the process exits
non-zero), and the LIVE marker is written ONLY after a successful tunnel restart
AND (for david) a successful Access reconcile. Fix the reported cause (NOPASSWD
sudo for spinbike's system unit, the `cloudflare-newlevel-access` token for
david's Access app) and re-run `--apply`.

## What stayed UNVERIFIED in the #664 worktree (proven at deploy)

The worktree branch verified everything locally testable: the CLI channel
(loopback bind + public-URL print via a live loopback round-trip), the
config-ingress augmentation (existing entries preserved, drop ingress before the
404, idempotent), the Access payload, the marker round-trip. NOT provable from
the worktree, hence this runbook: the live tunnel restart picking up the new
ingress, the live DNS CNAMEs, and the live Access app.
