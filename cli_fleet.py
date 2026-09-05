"""airuleset fleet constants — cluster L-E (#433).

Constants-only leaf promoted VERBATIM out of airuleset.py (#404 point 3 module
split; #433 cluster L, binding sub-split decision 1). Holds the two large,
widely-shared deploy/authority DATA registries — with their infra-rationale
doc-comments intact — so a self-contained CLI leaf can own them and airuleset.py
keeps a facade re-export at each original definition site. Existing readers do
NOT change: resident airuleset.py functions and shipped leaves alike keep
reading ``airuleset.REMOTE_HOSTS`` / ``airuleset.AUTHORITY_BY_USER`` (both stay
test-patchable on airuleset via that facade re-export); a genuinely NEW leaf may
import ``cli_fleet`` directly.

ZERO imports by design — this is pure data (a list of host dicts, a profiles
tuple, a user->profile map) plus a couple of trivial dict-accessor helpers
(``is_paused``/``paused_reason``, #851) kept next to the table they read. No
``import airuleset`` anywhere: this leaf has no outbound couplings, it is a
pure leaf of the dependency DAG.
"""

# #870 F3: controller cutover flag. When False (the dev1-safe default), all
# cutover code is DORMANT — zero runtime change. Commit B (the first push
# FROM the controller box) flips this to True.
CONTROLLER_CUTOVER_DONE = False

# Remote machines that should receive airuleset updates.
# host = the TAILSCALE IP (stable across LAN switches; see #1). Was 10.77.8.134.
REMOTE_HOSTS = [
    {
        "name": "dev2",
        "host": "100.82.64.27",
        "user": "newlevel",
        "repo_path": "~/devel/airuleset",
        # #451: dev2 is a managed deploy target whose `newlevel` user is NOT
        # a sub-dev stream account (not in AUTHORITY_BY_USER), so it was
        # filtered OUT of the meeting-analysis Soniox key delivery -- yet a
        # meeting-analysis session can run here (a montalu session on dev2,
        # 2026-08-13) and the skill greps ~/.soniox.env FIRST (SKILL.md:123).
        # This flag admits dev2 into provision_subdev_soniox_key's target set
        # WITHOUT touching the merge-authority map (AUTHORITY_BY_USER is about
        # merge rights, not meeting-analysis usage). Any future non-subdev
        # meeting-analysis box gets the key the same way -- one line, no
        # parallel mechanism.
        "soniox": True,
    },
    {
        # odoo-gatekeeper VPS (prod merge/deploy + hotfix box). Key-based SSH,
        # NOT the shared "newlevel" password — it is a prod-critical host.
        # Migrated 2026-07-07 to Hetzner cx23 "gk.newlevel.media": tailscale
        # IP 100.90.94.41 (node "gatekeeper-cx23", public 88.99.170.148 =
        # gk.newlevel.media). Do NOT use the MagicDNS name "odoo-gatekeeper"
        # — it resolves to a RETIRED node; the previous HostKey box
        # (100.77.52.43 / 202.148.55.31) is retired too.
        "name": "gatekeeper",
        "host": "100.90.94.41",
        "user": "gatekeeper",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # montalu2/montalu3/montalu4 — three MORE full parallel montalu
        # streams (airuleset#251, odoo-erp#2961: "zhodné s dnešným
        # montalu" — same subdev box, same default-key shape, same
        # branch-merge authority). Accounts created by GATEKEEPER (Phase 1
        # of #2961 — SSH access/Hetzner ownership stays with gatekeeper per
        # the user's 2026-08-05 ownership split; airuleset only wires the
        # ALREADY-EXISTING accounts into its own push/authority registries).
        # Live-verified 2026-08-05: all three accounts' default-key push
        # access had to be restored via root@subdev (see the montalu
        # entry's own comment above — same #258 access-cleanup mistake hit
        # montalu2/3/4 too, since they were provisioned from the same
        # authorized_keys template the cleanup rewrote).
        "name": "montalu2@subdev",
        "host": "100.118.174.27",
        "user": "montalu2",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu3@subdev",
        "host": "100.118.174.27",
        "user": "montalu3",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu4@subdev",
        "host": "100.118.174.27",
        "user": "montalu4",
        "repo_path": "~/devel/airuleset",
    },
    {
        # marek@subdev — webterm OBSERVER lane account (#882 scope correction,
        # 2026-09-05: DEV STREAM cancelled but webterm dashboard survives per
        # owner ruling "potrebujem aby marek mal prístup aj k m1"). NOT a dev
        # stream: no stream:marek slice, no tmux stream session, no soniox;
        # registered here so `push` reaches the account and runs
        # `maybe_setup_webterm`. Shell restored from nologin for the lane.
        "name": "marek@subdev",
        "host": "100.118.174.27",
        "user": "marek",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # dominika -- webterm OBSERVER account (airuleset#867, 2026-09-04, owner
        # request). NOT a dev stream: she runs ONLY the dominika webterm gateway
        # (cli_webterm_dominika) and watches two OTHER streams (montalu5 + miva1)
        # over loopback ssh; she works no tickets, has no Discord persona, no
        # notify routing. Registered here so `push` reaches her account and runs
        # `maybe_setup_webterm` (same subdev VPS, same operator gatekeeper_access
        # identity — byte-copied authorized_keys is the go-live owner step).
        # Classified reduced `fork-no-merge` (the LEAST-privilege profile) in
        # AUTHORITY_BY_USER below, NEVER full: an observe-only account must never
        # merge/deploy/close (see that row).
        "name": "dominika@subdev",
        "host": "100.118.174.27",
        "user": "dominika",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # miva1 -- 5th sub-dev stream, phase-1 isolated, on the same subdev
        # VPS as david/simap (airuleset#300; tracking ticket for the
        # account itself is odoo-erp#3223). Built by gatekeeper: bare linux
        # user + own SSH keypair, read-only GitHub deploy key, `develop`
        # checkout, empty tmux session -- but no airuleset config until this
        # entry lands. Registered with the SAME operator gatekeeper_access
        # identity requirement as david/simap (never montalu's
        # default-key path), matching this ticket's own "same phase-1
        # isolated shape as simap" framing.
        "name": "miva1@subdev",
        "host": "100.118.174.27",
        "user": "miva1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # david2 -- 6th/7th/8th sub-dev streams (airuleset#326, 2026-08-08):
        # THREE MORE parallel david streams, additional capacity for the
        # SAME external slovnormal odoo developer (fork-based, no sudo, no
        # prod keys), provisioned by gatekeeper on the SAME subdev VPS as
        # david1 itself (the renamed base david, #537; odoo-erp#3282).
        # Registered here as a data-only mirror of david1's own entry (host +
        # identity requirement) -- the identity ASSUMPTION is unverified for
        # these specific accounts (mirroring david1's shape is the
        # registration; it does not confirm
        # THIS account's authorized_keys accepts the same operator key --
        # #300's own precedent for this exact caveat). No ssh was attempted
        # from this worktree to verify it (fail2ban risk, #300).
        "name": "david2@subdev",
        "host": "100.118.174.27",
        "user": "david2",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        "name": "david3@subdev",
        "host": "100.118.174.27",
        "user": "david3",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        "name": "david4@subdev",
        "host": "100.118.174.27",
        "user": "david4",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # montalu5/montalu6/montalu7/montalu8 (airuleset#378,
        # odoo-erp#3642): FOUR MORE full parallel montalu streams, same
        # shape as montalu2/3/4 (airuleset#251) -- same subdev box, same
        # default-key shape (no `identity` entry — the montalu family
        # authenticates via dev1's own default newlevel key, never
        # gatekeeper_access_ed25519), same branch-merge authority. Accounts
        # created by GATEKEEPER, repo side wired per odoo-erp#3642.
        "name": "montalu5@subdev",
        "host": "100.118.174.27",
        "user": "montalu5",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu6@subdev",
        "host": "100.118.174.27",
        "user": "montalu6",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu7@subdev",
        "host": "100.118.174.27",
        "user": "montalu7",
        "repo_path": "~/devel/airuleset",
    },
    {
        "name": "montalu8@subdev",
        "host": "100.118.174.27",
        "user": "montalu8",
        "repo_path": "~/devel/airuleset",
    },
    {
        # forestshop-dev (airuleset#406, 2026-08-12): owner-dedicated Hetzner
        # Nbg1 cx23 box for zbynekdrlik/forestshop-app. No tailscale -- the
        # owner explicitly declined it -- so it is addressed by its own
        # public DNS name (the ticket's own literal ssh address for BOTH
        # accounts), not the raw IP 178.105.89.168; the DNS name is already
        # the address used everywhere (the ticket text, and
        # forestshop_app's own .claude/rules/deploy.md, which shows
        # `ssh admin@forestshop-dev.newlevel.media` in routine use). No
        # `identity` pinned: that same deploy.md file shows the ssh command
        # already working with NO -i flag from dev1 -- i.e. dev1's own
        # default key (~/.ssh/id_ed25519) is already authorized there, the
        # same "default newlevel key, no identity" shape montalu1@subdev
        # already uses. Deliberately NOT the ~/.ssh/forestshop_dev_backup_pull
        # key found on dev1's disk -- its own comment
        # (forestshop-dev-backup-pull@dev1) marks it single-purpose for a
        # backup-pull flow, never general shell access. Full authority
        # (not registered in AUTHORITY_BY_USER below) per the ticket's own
        # explicit ask -- this is the owner's own trusted box, not an
        # external sub-dev stream.
        "name": "admin@forestshop-dev",
        "host": "forestshop-dev.newlevel.media",
        "user": "admin",
        "repo_path": "~/devel/airuleset",
        # #679: pinned PUBLIC ssh host keys for this public-DNS target. Same
        # public-internet threat class as spinbike-vps (#669) and WORSE: this
        # entry carries no `identity`, so the deploy loop takes the no-identity
        # `sshpass -p newlevel` branch -- under StrictHostKeyChecking=no a MITM
        # on the path to forestshop-dev.newlevel.media (or a DNS hijack) would
        # get ANY host key accepted AND receive the fleet-shared password. The
        # pin verifies the host key STRICTLY (cli_remote.host_key_check_opts),
        # so a MITM's key fails BEFORE any auth -- the password can never be
        # handed over. PUBLIC key material -- safe to commit, exactly like
        # cli_owner_keys.OWNER_PUBKEYS. Each line is `<type> <base64>` WITHOUT
        # the address; cli_remote materializes them into a known_hosts file
        # keyed to `host` (the DNS name ssh connects by -- CheckHostIP defaults
        # to no, so no HostKeyAlias is needed). Captured on dev1 (the maintainer
        # box) with auth-less `ssh-keyscan`, cross-verified byte-for-byte
        # against dev1's existing authenticated-deploy known_hosts entries (the
        # DNS-name line + the raw-IP 178.105.89.168 lines) AND one authenticated
        # `ssh admin@forestshop-dev.newlevel.media` connection over the
        # documented default-key path (rc 0, hostname forestshop-dev, repo
        # present); ed25519 fingerprint
        # SHA256:sP+uKY/5B+85xQoNUs+RfJ5SwMozoQWUWNi/EouGZI8. Both this and the
        # stepan@ entry below are the SAME physical box, so they carry the SAME
        # pin. A genuine host-key rotation will hard-fail the deploy LOUDLY
        # until these are re-captured + re-committed -- the intended fail-safe
        # direction. (An `identity` to leave the shared-password branch is a
        # separate decision the ticket explicitly defers -- pinning alone fully
        # closes the exposure this ticket names.)
        "host_keys": [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF0hQYw2+OticG0PVhzzDeJzghERkK7g+WkqpDihlbiI",
            "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBHpFPlgqeS8+KP2L9KrlVSKqezEK19l8IgdDCubJPxISCF8L4X7TO/TkOkBXoYVKPgaLyEV2rva6zlihdef4h9o=",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCM20xevli5Jj4pdx3m0lQs7m81ZMY6+b20kIwrtM1hLjbEV9JOW7G2P15zcCEeHwtkqn36BSERbkKVX9tf8aXy7TD+Wh80o70cUhh77r2janngtCGkHNWbag/Q9mvOrIos6f1BQjkMlH77g6O5Fav5ZaOADzKPlyP9EqYc++ZIrGkaoqeJUirFGVVY7OhdF5Zx2g4UUfEv92SxvAB9W6mWVabotoFdEh2qlY0iX8o7uL0vTim63E82E1dxU2QkYH6mtMimn8rU1oNfg3IM5N2ZzIar3U6XwlcQmNkNm7Xjj2Fl95F1r6s4V363b3UrnDeK+qf1EtJMv9bOILDIJgqGDU+OQBEfvA/Y9jfeaC4LxO4JeniRcgJVIH8gyPhmOUJ7/RSp/R+7394KXo1ueKv1DVZKN0V99GLmUUwHjT8Eh6tg+Ma5tOoj81jHrRyJ07qYOC34ERvfTeH6bctKmCA73wsTXTbOVq6lPL9X6w/Disnfu6EBLPFLSiwFEHbNnLk=",
        ],
    },
    {
        # stepan@forestshop-dev -- StepanDK's own isolated dev account on
        # the SAME box (see the admin@forestshop-dev entry above for the
        # host/identity rationale -- same shape). No independent evidence
        # was found confirming this SPECIFIC account's default-key auth
        # (the deploy.md evidence only ever shows the admin@ account) -- the
        # supervisor's first live push is the first real proof this
        # account's default-key auth actually works; a failure here is a
        # one-line fix (add "identity": "<path>") once the correct key is
        # known, not a redesign.
        "name": "stepan@forestshop-dev",
        "host": "forestshop-dev.newlevel.media",
        "user": "stepan",
        "repo_path": "~/devel/airuleset",
        # #679: same physical box as admin@forestshop-dev above -> the SAME
        # committed PUBLIC host-key pin (see that entry for the full provenance
        # + rationale). Both accounts connect by the same DNS name, so
        # cli_remote materializes ONE shared known_hosts pin for both.
        "host_keys": [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF0hQYw2+OticG0PVhzzDeJzghERkK7g+WkqpDihlbiI",
            "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBHpFPlgqeS8+KP2L9KrlVSKqezEK19l8IgdDCubJPxISCF8L4X7TO/TkOkBXoYVKPgaLyEV2rva6zlihdef4h9o=",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCM20xevli5Jj4pdx3m0lQs7m81ZMY6+b20kIwrtM1hLjbEV9JOW7G2P15zcCEeHwtkqn36BSERbkKVX9tf8aXy7TD+Wh80o70cUhh77r2janngtCGkHNWbag/Q9mvOrIos6f1BQjkMlH77g6O5Fav5ZaOADzKPlyP9EqYc++ZIrGkaoqeJUirFGVVY7OhdF5Zx2g4UUfEv92SxvAB9W6mWVabotoFdEh2qlY0iX8o7uL0vTim63E82E1dxU2QkYH6mtMimn8rU1oNfg3IM5N2ZzIar3U6XwlcQmNkNm7Xjj2Fl95F1r6s4V363b3UrnDeK+qf1EtJMv9bOILDIJgqGDU+OQBEfvA/Y9jfeaC4LxO4JeniRcgJVIH8gyPhmOUJ7/RSp/R+7394KXo1ueKv1DVZKN0V99GLmUUwHjT8Eh6tg+Ma5tOoj81jHrRyJ07qYOC34ERvfTeH6bctKmCA73wsTXTbOVq6lPL9X6w/Disnfu6EBLPFLSiwFEHbNnLk=",
        ],
    },
    {
        # SpinBike Hetzner VPS (airuleset#408, 2026-08-12): no tailscale --
        # the owner explicitly declined it (spinbike#350) -- so this is
        # addressed by its raw public IPv4 with an explicit pinned
        # identity, the first managed target that cannot use a MagicDNS
        # name. Shape given verbatim by the maintainer's own comment on the
        # ticket (issuecomment-5268350062): REMOTE_HOSTS already supports a
        # keyed public-IP target (see the gatekeeper entry above), no push
        # code change needed. `identity` is REQUIRED here (unlike
        # forestshop-dev) because there is no derivable evidence of a
        # working default-key path, and the ticket explicitly states this
        # key was freshly generated "for this box only, not shared" --
        # falling back to no-identity here would silently attempt the
        # shared dev1/dev2 sshpass password against an account that is NOT
        # part of that shared-password convention. Full authority (not
        # registered in AUTHORITY_BY_USER below), same reasoning as
        # forestshop-dev.
        "name": "spinbike-vps",
        "host": "167.233.245.147",
        "user": "newlevel",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.ssh/spinbike_vps",
        # #669: pinned PUBLIC ssh host keys for this raw-public-IP target.
        # Every OTHER managed host is a private tailscale/subdev address where a
        # StrictHostKeyChecking=no (TOFU) posture is acceptable; spinbike-vps is
        # the FIRST target reached over the public internet by raw IP, so its
        # push-path ssh legs must verify the host key STRICTLY (see
        # cli_remote.host_key_check_opts). PUBLIC key material -- safe to commit,
        # exactly like cli_owner_keys.OWNER_PUBKEYS. Each line is `<type>
        # <base64>` WITHOUT the address; cli_remote materializes them into a
        # known_hosts file keyed to `host` for `-o UserKnownHostsFile`.
        # Captured on dev1 (the maintainer box) with auth-less `ssh-keyscan`,
        # cross-verified byte-for-byte against dev1's existing authenticated-
        # deploy known_hosts entries AND an authenticated `ssh -i
        # ~/.ssh/spinbike_vps` connection (ed25519 fingerprint
        # SHA256:biFKgHhb7NP//kfDbvUzEe1R/ZDJkKhRe7hTy7CmS6c). A genuine host-key
        # rotation on spinbike will hard-fail the deploy LOUDLY until these are
        # re-captured + re-committed -- the intended fail-safe direction.
        "host_keys": [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ4gdjBncONNRHmRw+W8hNFBDkkvEORFWLBxXUWS2r7g",
            "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBOGOPIXqySpMtYUHf3LOdpOWUwhUqxQb6tPwohllTPO0jtjF7YgTw7BKT+NQlFL2QapbGET925FaO/ZIPamFFm8=",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDJdt/emE+jSbKDUgS2WBAPicPNJaVGFSPZ2svAtkfzDWxTS3duvDNR/i9S1D9Rv1VWbAzBrBFyXDZG/UmblLatzozd+DJjl7lf2/5opuW4qrtyqwNqr6rpyClo2U5xH3ftR6atZu+T4uJnAWBjasH9WLD7TvV/IU0m/627tEkwOJollYKdEz1bEVcYW697CHFROAmEehgThm8Ikio0vBPhUHG7POigquZS/ZDgJNqcaBPpeWgni0NRRcn/pmoKwEywUJx4DgKw6Okulan27Scx3K2E7luRa6xZsEbtQWTNiNOoBQM2+MyFwLxZwi3P+CiINcrYeactngnmSrwtH6tcNlGUmqHp8zF7rEZESpAlvwoErK7XjAO8ML76JuwwDmAcOXDwfUKJzWO6tNYjMeaEQOdEVNodpyesRFM2qvBAzn8FQWeoGRoBEPDAVNTpRzv6jmMgkXeB0Lu3TwqlZ3bhSn1vXdxbTXMinTNXp0lcsmLGz9g78VXCvybUj52LFYE=",
        ],
        # #659: a VPS-class OWNER target (the owner's own box, full authority).
        # This flag gates the owner-VPS-only provisioning: the deploy loop sets
        # AIRULESET_OWNER_VPS=1 for this host so cmd_install's
        # provision_owner_sudo() installs NOPASSWD sudo for the owner user.
        # (#669: the #659 headless CLAUDE_CODE_OAUTH_TOKEN delivery was REMOVED
        # -- login/auth on a target is the PROJECT claudy's job, airuleset never
        # touches auth; owner ROZHODNUTÉ #659.) Sub-dev stream accounts carry NO
        # such flag (they stay sudo-less), so this is per-target, never a
        # blanket rule.
        "owner_vps": True,
    },
    {
        # david1 (#537): the renamed base david stream (was `david`; #537 live
        # rename 2026-08-21: in-place usermod on subdev, uid 1000 kept, home
        # moved to /home/david1, primary group renamed, linger re-enabled,
        # CC per-project state migrated, stale /home/david symlinks repointed,
        # session relaunched, token delivery `delivered: stream=david1`
        # verified). Identity mirrors the base: the operator gatekeeper_access
        # identity. David's isolated external-dev user (slovnormal odoo dev
        # stream: no sudo, no prod keys, can't read other homes).
        "name": "david1@subdev",
        "host": "100.118.174.27",
        "user": "david1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # montalu1 — the renamed base montalu stream (was `montalu`; #537 live
        # rename 2026-08-19: in-place usermod on subdev, uid 1002 kept, home
        # moved to /home/montalu1, primary group renamed, linger re-enabled,
        # ssh from dev1 with the montalu-family DEFAULT key verified; the old
        # `montalu@subdev` entry + its AUTHORITY_BY_USER row are GONE — the OS
        # account no longer exists). Unlike david1/simap1 it authenticates
        # with the DEFAULT newlevel key (no `identity`, like montalu2..8) — the
        # montalu family's shared default-key/sshpass path, never
        # gatekeeper_access. Original history: airuleset#33 + odoo-erp#1895
        # (migrated 2026-07-24 from dev1 to the subdev VPS).
        "name": "montalu1@subdev",
        "host": "100.118.174.27",
        "user": "montalu1",
        "repo_path": "~/devel/airuleset",
    },
    {
        # simap1 — the renamed 4th sub-dev stream (was `simap`; #537 live
        # rename 2026-08-18: in-place usermod on subdev, uid 1003 kept,
        # home moved to /home/simap1, linger re-enabled; the old
        # `simap@subdev` entry + its AUTHORITY_BY_USER row are GONE — the
        # OS account no longer exists). Original build history: airuleset#143
        # (Odoo 19 demo, gatekeeper_access identity, operator keys).
        "name": "simap1@subdev",
        "host": "100.118.174.27",
        "user": "simap1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
        # #851 owner directive 2026-09-02: simap is a PAUSED stream until the
        # customer decides — never re-heal, never deploy, never contact it.
        # Resume = delete this key (the ONLY resume mechanism, #851 design
        # point 5) — no separate flag, no `--skip` invocation option.
        "paused": ("owner 2026-09-02: simap pozastavený, kým sa zákazník "
                    "nevyjadrí (odoo-erp stream simap1); gk mu obmedzil "
                    "prístup — NEliečiť, NEdeployovať"),
    },
]


def _append_dev1_if_cutover():
    """Conditionally add dev1 as a deploy TARGET when the cutover is done.

    Called at module-load time AND available for tests to re-invoke after
    patching CONTROLLER_CUTOVER_DONE. When False, a no-op."""
    if CONTROLLER_CUTOVER_DONE and not any(
            h.get("name") == "dev1" for h in REMOTE_HOSTS):
        REMOTE_HOSTS.append({
            "name": "dev1",
            "host": "100.104.8.125",
            "user": "newlevel",
            "repo_path": "~/devel/airuleset",
            "identity": "~/.secrets/airuleset_push_ed25519",
            "owner_vps": True,
            "soniox": True,
        })


_append_dev1_if_cutover()


def is_paused(remote):
    """True if a REMOTE_HOSTS entry carries a `"paused": "<why + date>"`
    marker (#851) -- an account the owner has frozen (a customer/access
    dispute) that must NEVER be auto-contacted (no deploy, no re-heal, no
    ssh of any kind) until the owner deletes the flag. Zero imports, zero
    outbound coupling -- a one-line accessor kept next to the table it
    reads, consistent with this leaf's own "pure data, no logic" design."""
    return bool(remote.get("paused"))


def paused_reason(remote):
    """The `paused` string itself (why + date), or `""` when the entry is
    not paused -- never `None`, so a caller can always format it safely
    (e.g. an f-string SKIPPED line) without an extra None-check."""
    return remote.get("paused") or ""


# Autopilot authority profiles (issue #16, 2026-07-09). A stream's authority is a
# property of its LINUX USER (streams are separate users by construction: david1 /
# montalu1 / miva1), resolved at RUNTIME — no per-box state to lose on a home-dir
# migration (the AIRULESET_NOTIFY_OWNER loss pattern), and every push carries the
# map to every managed target. Profiles:
#   full          — merge PR to main + main green + deploy verified (default)
#   branch-merge  — own PR merged into the project INTEGRATION branch (develop),
#                   THEN the same ready-for-review hand-off comment fork-no-merge
#                   uses (#349: a merge alone does NOT close the ticket, and
#                   skipping the comment leaves it invisible to the gatekeeper's
#                   review queue); never staging/main promotion, never deploy,
#                   never closes the issue itself
#   fork-no-merge — fork branch pushed + local verification green + ready-for-review
#                   hand-off on the issue; never opens/merges a PR, never closes
#                   the issue itself (the maintainer does, at merge)
# A project CLAUDE.md marker `airuleset:authority=<profile>` can only LOWER the
# per-user default (a CAP, never a raise — airuleset#828, `_authority_decision`);
# `full` is granted ONLY via the registries below. Only the user adds markers.
AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")
AUTHORITY_BY_USER = {
    # marek — DEV STREAM cancelled (#882, odoo-erp#6257) but webterm OBSERVER
    # lane survives (owner scope correction 2026-09-05). Least-privilege
    # fork-no-merge (dominika model #867): she is not a real hand-off stream,
    # so WEBTERM_OBSERVER_USERS excludes her from stream provisioning consumers.
    "marek": "fork-no-merge",
    # david (airuleset#23) was renamed to david1 (#537, 2026-08-21) — its row
    # moved to the numbered block below; the OS account `david` is gone.
    # montalu (airuleset#33) was renamed to montalu1 (#537, 2026-08-19) — its
    # row moved to the numbered block below; the OS account `montalu` is gone.
    # simap (airuleset#143) was renamed to simap1 (#537, 2026-08-18) — its
    # row moved to the numbered block below; the OS account `simap` is gone.
    # montalu2/montalu3/montalu4 (airuleset#251, odoo-erp#2961): three MORE
    # full parallel montalu streams — same authority as montalu itself.
    "montalu2": "branch-merge",
    "montalu3": "branch-merge",
    "montalu4": "branch-merge",
    # miva1 (airuleset#300, 2026-08-07): activated phase-1 isolated (fork-no-merge).
    # PROMOTED to branch-merge (airuleset#821, 2026-09-01): odoo-erp CLAUDE.md
    # phase-2 (#3244, 2026-08-14) made miva1 a full write stream "in the montalu
    # mould" -- it pushes miva1/<topic> directly, opens+merges its own PR into
    # develop, then the ready-for-review hand-off ("branch-merge authority (like
    # montalu)", odoo-erp CLAUDE.md). odoo-erp states this in PROSE, not the
    # <!-- airuleset:authority=... --> HTML-comment marker (and MUST stay
    # marker-free: it is a MULTI-STREAM repo where a project marker would beat the
    # table for EVERY user and DOWNGRADE the gatekeeper's `full`), so this per-user
    # table row is the effective source. The stale fork-no-merge value armed the
    # wrong (never-open/merge-a-PR) /goal template on 2026-09-01. miva1 is an
    # odoo-erp-only single-client (MIVA) stream, so a per-USER branch-merge default
    # is correct even though the table is per-user, not per-repo.
    "miva1": "branch-merge",
    # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE clones of
    # the david external-developer fork stream (additional capacity for the
    # same slovnormal odoo developer) -- same authority as david1 (the renamed
    # base david, #537).
    "david2": "fork-no-merge",
    "david3": "fork-no-merge",
    "david4": "fork-no-merge",
    # montalu5/montalu6/montalu7/montalu8 (airuleset#378, odoo-erp#3642):
    # four MORE full parallel montalu streams -- same authority as
    # montalu/montalu2/montalu3/montalu4.
    "montalu5": "branch-merge",
    "montalu6": "branch-merge",
    "montalu7": "branch-merge",
    "montalu8": "branch-merge",
    # montalu1/david1/simap1 (#537): the NUMBERED names for the base-stream
    # rename (owner directive on #532 — montalu->montalu1, david->david1,
    # simap->simap1; marek was un-renamed but is now decommissioned #882). All three
    # renames are now LIVE (montalu1 2026-08-19, simap1 2026-08-18, david1
    # 2026-08-21) — each runs as the new OS account, so every base row above is
    # gone. Each keeps its base's authority profile. STREAM_RENAME_ALIASES
    # (below) is KEPT so old `stream:<base>` tickets still resolve via
    # `_stream_rename_equivalents`, in BOTH directions, until no open
    # `stream:<base>` ticket remains.
    "montalu1": "branch-merge",
    "david1": "fork-no-merge",
    "simap1": "fork-no-merge",
    # dominika (airuleset#867, 2026-09-04): a webterm OBSERVER, NOT a dev stream —
    # she works no tickets, merges/deploys/closes nothing. But
    # `test_every_remote_hosts_user_is_classified` requires every REMOTE_HOSTS user
    # to sit in EXACTLY ONE registry: FULL_AUTHORITY_USERS (merge-to-main + deploy +
    # close) is WRONG for an observer, so she goes here with the LEAST-privilege
    # reduced profile `fork-no-merge` (the safe fail-direction — never full). She is
    # not a real hand-off stream, so the `_own_handoff_label`/`_ticket_is_stream_
    # labeled` "AUTHORITY_BY_USER == is-a-stream" consumers may see her as a stream
    # — harmless, because she never authors/works a ticket, so those paths never
    # fire for her; and `fork-no-merge` (not `full`) keeps this row consistent with
    # the "no `full` value in AUTHORITY_BY_USER" invariant
    # (test_authority_profiles.py). She gets NO full-authority/maintainer skills
    # (not in FULL_AUTHORITY_USERS/MAINTAINER_USERS) and NO dev-stream extras (not
    # in SKILLS_EXTRA_BY_USER) — skill_names_for_user gates all of those.
    "dominika": "fork-no-merge",
}


# Explicit FULL-authority account allow-list (airuleset#827, 2026-09-02). The
# unix accounts that legitimately resolve `full` (merge to main + deploy + close
# issues) and are DELIBERATELY NOT in AUTHORITY_BY_USER above — that table is the
# REDUCED-authority sub-dev stream registry, and two consumers key on membership
# as "is a sub-dev stream" WITHOUT a profile filter (`_own_handoff_label`,
# `_ticket_is_stream_labeled`), so a `full` account there would misclassify the
# maintainer/gatekeeper boxes as streams everywhere downstream. This mirrors the
# `SSH_ATTACH_EXTRA_USERS` idiom (#562/#563) for exactly that non-stream-account
# class. Members (every REMOTE_HOSTS `user` not in AUTHORITY_BY_USER, all
# documented/intended full): `newlevel` = dev1/dev2 maintainer + spinbike-vps;
# `gatekeeper` = gk box; `admin` + `stepan` = the owner's own trusted forestshop-dev
# box (cli_fleet REMOTE_HOSTS: "the owner's own trusted box, not an external
# sub-dev stream"). Before #827 these relied on the fail-OPEN `full` default in
# `_authority_decision`; that default now fails SAFE to `fork-no-merge`, so the
# legitimate full accounts MUST be enumerated here or they regress.
#
# HAND-MAINTAINED, never derived (e.g. "REMOTE_HOSTS users minus
# AUTHORITY_BY_USER") — a derived full-set would re-open the fail-open bug: a
# future REDUCED stream added to REMOTE_HOSTS but forgotten in AUTHORITY_BY_USER
# would auto-classify `full`. Kept DISJOINT from AUTHORITY_BY_USER
# (`test_full_authority_users_disjoint_from_stream_table`); a genuinely-unknown
# user (in neither registry) fails SAFE to `fork-no-merge`. Every REMOTE_HOSTS
# user must appear in one registry or the other
# (`test_every_remote_hosts_user_is_classified`) — a new provisioned box that is
# neither is a RED test, forcing an explicit decision, never a silent grant.
# ONE unmapped->full path survives, narrow and deliberate: the GITHUB-HOSTED CI
# runner (`_is_github_ci_runner`: unix `runner` OR uid 0 — a container job — AND
# GITHUB_ACTIONS AND RUNNER_ENVIRONMENT=github-hosted, airuleset#839) — a
# legitimate full context for THIS repo's own CI, un-spoofable by a stream
# (uid-derived pw_name; a stream can never be uid 0; no fleet box has a `runner`
# account) and gated off a self-hosted runner. A project CLAUDE.md
# `authority=full` marker does NOT elevate an unmapped user: airuleset#828 (owner
# decision A) made the marker a CAP that can only LOWER, never raise. Everything
# ELSE in neither registry still fails SAFE to `fork-no-merge`.
FULL_AUTHORITY_USERS = frozenset({"newlevel", "gatekeeper", "admin", "stepan"})


# Webterm OBSERVER accounts (airuleset#867). An account that exists ONLY to run a
# webterm gateway (maybe_setup_webterm) and VIEW other streams, never to run a
# Claude stream of its own. It MUST still be classified
# (test_every_remote_hosts_user_is_classified), and full is wrong for an observer,
# so it sits in AUTHORITY_BY_USER at fork-no-merge — but AUTHORITY_BY_USER
# membership ALSO means "is a reduced sub-dev stream" to install/push-time
# provisioning consumers: ensure_stream_tmux_session (types `claude` into an
# auto-created tmux session — an unwanted Claude session burning tokens on a viewer
# account), report_stream_dev_env (dev-env/TODO-PROVISIONING gap noise),
# provision_subdev_soniox_key (writes ~/.soniox.env — a needless credential
# footprint), is_single_session_box_user (ssh-auto-attach + per-window naming), and
# _REDUCED_STREAM_USERS (bounce/gkreq cross-stream sweeps). An observer wants NONE
# of those — only the webterm gateway. This HAND-MAINTAINED set (never derived) is
# the ONE exclusion those consumers honour. Kept a strict SUBSET of
# AUTHORITY_BY_USER (test-locked): an observer is a classified reduced account
# MINUS stream provisioning, not a third authority tier — resolve_authority still
# returns its fork-no-merge (harmless: an observer authors/works no ticket).
WEBTERM_OBSERVER_USERS = frozenset({"dominika", "marek"})


# Webterm-ONLY accounts (#869, owner directive 2026-09-04): these users access
# their streams EXCLUSIVELY via the webterm gateway (david.newlevel.media,
# dominika.newlevel.media) — no personal SSH key, no password login.  Direct
# SSH is a fallback for the owner (zbynek) ONLY (#882: marek decommissioned).
#
# DISTINCT from WEBTERM_OBSERVER_USERS: observer = provisioning PROFILE
# (gateway-only, no Claude stream — dominika); webterm-only = SSH ACCESS POLICY
# (no personal key / password — david1-4 are full streams yet webterm-only;
# dominika is in both).
#
# Managed by cli_webterm_only.py: authorized_keys rendered exactly (foreign
# keys quarantined), sshd Match drop-in disables password auth.
# Test-locked: subset of subdev REMOTE_HOSTS users.
WEBTERM_ONLY_USERS = frozenset({
    "david1", "david2", "david3", "david4", "dominika", "marek",
})


def is_webterm_observer(user):
    """True iff `user` is a webterm OBSERVER account (#867) — provisioned with a
    webterm gateway only, excluded from every stream-provisioning side effect of
    AUTHORITY_BY_USER membership. See WEBTERM_OBSERVER_USERS for the full rationale."""
    return user in WEBTERM_OBSERVER_USERS


def _github_ci_runner_source(user):
    """The `--explain` `source` string IF this process is the GITHUB-HOSTED CI
    runner for airuleset's own CI, else None (airuleset#839). Named DISTINCTLY
    per arm so the printed decision log stays consistent with the resolved
    profile: `ci-runner (GitHub-hosted, container)` when the uid-0 CONTAINER arm
    matched, `ci-runner (GitHub-hosted)` for the `runner` arm.

    The runner is in neither authority registry, so #827's fail-safe resolves it
    `fork-no-merge`, which broke ~33 tests that shell out to the FULL-authority-
    gated `core-quals` / `tickets-status --refresh` / run-card backlog count (all
    of which silently assumed the box was full). The hosted runner IS a
    legitimate full-authority context for THIS repo's OWN CI.

    Recognition needs BOTH hosted-CI env conjuncts (`GITHUB_ACTIONS=true` AND
    `RUNNER_ENVIRONMENT=github-hosted`) PLUS an UNSPOOFABLE identity signal —
    either the unix account `runner` OR uid 0 (`root`). Two identity arms because
    the `gate` job runs its pytest step INSIDE a `container: python:3.12`, where
    the process is uid 0 / `pw_name = root`, NEVER `runner` (attempt 1's
    `pw_name == "runner"`-only recognition never fired on CI). Both are
    un-spoofable by a stream: a stream can never be uid 0 (that needs root), and
    no fleet box has a `runner` unix user (creating one needs root) — a stream
    controls its env and its repo files, NEVER its uid. Root on a fleet box is
    already all-powerful, so recognising it under the hosted-CI env grants
    nothing new; and a plain root shell WITHOUT the env stays `fork-no-merge`
    (root is in NO registry).

    The conjunction is load-bearing and un-spoofable in EVERY direction: an
    identity signal WITHOUT `GITHUB_ACTIONS`/`RUNNER_ENVIRONMENT` stays reduced (a
    real `runner`/`root` box — #827 preserved, constraint 4); the env WITHOUT
    `runner`/uid-0 elevates nobody (a stream setting the env vars can never make
    its uid 0 or its pw_name `runner`); and `RUNNER_ENVIRONMENT == "github-hosted"`
    distinguishes the GitHub-HOSTED runner from a SELF-HOSTED actions runner (the
    runner app sets it `github-hosted`/`self-hosted`), so a self-hosted runner
    provisioned under a `runner` unix account — the one non-stream actor that
    could carry that pw_name (owner misconfig, needs root) — is NOT elevated.
    Fail-safe: were GitHub ever to drop `RUNNER_ENVIRONMENT`, airuleset's own CI
    would go visibly RED (a stream can never reach full by it), never
    silently-full. `user` is the already-resolved `_current_user()` pw_name — an
    env-spoofable `getpass.getuser()` would defeat the whole point, so this MUST
    be fed the hardened identity.

    Fork-PR CI resolving `full` is harmless: the profile only unblocks CLI
    *behavior*; all real power lives in credentials the hosted runner does not
    hold.

    INVARIANT (airuleset#839): neither `runner` nor `root` may ever appear in
    `AUTHORITY_BY_USER` or `FULL_AUTHORITY_USERS` (lock-tested), so this predicate
    is the ONLY path that recognises them — a self-hosted `runner`, or a plain
    root shell, stays reduced by the `github-hosted` / env terms above.
    """
    import os
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        return None
    # uid-0 CONTAINER arm checked FIRST so a container job (the real CI shape)
    # is named as such; a hosted runner outside a container (uid != 0) is named
    # by the `runner` arm. The container arm requires BOTH the real uid to be 0
    # AND the RESOLVED identity to be `root` (`user`, the hardened
    # `_current_user()` pw_name). In the real container both hold together
    # (`pwd.getpwuid(0).pw_name == "root"`), so it fires; and keying on the
    # resolved identity — exactly as the `runner` arm does — means a test (or
    # any process) whose `_current_user()` is patched/derived to a NON-root
    # value never trips this arm merely because it happens to run as uid 0
    # (e.g. the CI container itself running the hermetic suite). Without the
    # `user == "root"` half, the ambient uid-0 would elevate EVERY
    # authority-resolving test on CI to `full`, breaking the ones that assert a
    # reduced result while patching only `_current_user` (found by running the
    # hermetic subset in the real `python:3.12` container as root, airuleset#839
    # attempt 2). Still un-spoofable: a stream can never be uid 0 AND can never
    # carry the `root` pw_name.
    if os.getuid() == 0 and user == "root":
        return "ci-runner (GitHub-hosted, container)"
    if user == "runner":
        return "ci-runner (GitHub-hosted)"
    return None


def _is_github_ci_runner(user) -> bool:
    """True when THIS process is the GITHUB-HOSTED CI runner for airuleset's own
    CI (airuleset#839). Thin bool over `_github_ci_runner_source` — the ONE
    definition point of the recognition — so `_authority_decision`'s hot-path
    check and its `--explain` `map_val` annotation can never disagree with the
    named source.
    """
    return _github_ci_runner_source(user) is not None


# Base-stream rename map (#537): old base name -> new numbered name. The SINGLE
# explicit source of truth for the in-progress rename, so `cli_quals`'
# `_stream_rename_equivalents()` (the alias primitive that `_slice_quals` and
# `_ticket_is_stream_labeled` both consume) has one table to read, never a
# scattered set of literals. `marek` was deliberately absent (un-renamed) and
# is now decommissioned (#882). Read via `airuleset.STREAM_RENAME_ALIASES` (the facade
# re-export), NEVER `cli_fleet.STREAM_RENAME_ALIASES` directly, so a
# `patch.object(airuleset, "STREAM_RENAME_ALIASES", ...)` in a test is honoured
# (the same L-E rule AUTHORITY_BY_USER above follows).
STREAM_RENAME_ALIASES = {
    "montalu": "montalu1",
    "david": "david1",
    "simap": "simap1",
}


# --- #775: shared-stream resource-guard apply targets ----------------------
# The box(es) that run N isolated reduced-authority Claude stream users
# (`AUTHORITY_BY_USER`) and therefore need per-user systemd cgroup ceilings so a
# single stream can never OOM-collapse the whole box (subdev incident
# 2026-08-31). DATA-ONLY, like REMOTE_HOSTS above.
#
# `admin_user`/`identity` = the ROOT apply path: stream accounts are sudo-less,
# so the guardrail drop-ins (which live in /etc/systemd/system) can only be
# installed over `ssh root@<host>` — a DIFFERENT connection from the per-user
# deploy loop. The identity is the SAME pinned operator key the gatekeeper VPS
# uses; the dev1→root@subdev authorization for it is a one-time GATEKEEPER-ACTION
# bootstrap (until it lands, `provision_shared_stream_guards` is a fail-loud
# no-op and the `watchdog.resource_guard` verify job is the backstop).
#
# `host` is subdev's TAILSCALE IP (the same 100.118.174.27 the montalu*/david*/
# simap1/miva1 REMOTE_HOSTS entries above all share) — stable across LAN
# switches (#1). Every reduced-authority stream in AUTHORITY_BY_USER lives on
# THIS box, so this single entry covers the whole shared-stream fleet;
# `tests/test_resource_guards.py` drift-locks that (a new shared-stream host
# cannot be added to REMOTE_HOSTS without a matching guard entry here).
SHARED_STREAM_GUARD_HOSTS = [
    {
        "name": "subdev",
        "host": "100.118.174.27",
        "admin_user": "root",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
]


# --- #841: disk-guard ROOT/system-level apply targets ----------------------
# The public multi-user VPS boxes whose root-owned classes (btmp/wtmp/auth.log,
# system journal, apt cache, docker images, gh-runner _work, other users'
# /tmp) no per-USER watchdog can reach (#834 root cause) — subdev (sudo-less
# streams) AND gk (the box that hit 91 %). A DELIBERATELY SEPARATE list from
# SHARED_STREAM_GUARD_HOSTS above: reusing that list would silently apply the
# #775 cgroup drop-ins to gk (scope creep on a shipped guard) and still omit
# the box that actually filled. Same `{name,host,admin_user,identity}` schema.
#
# `admin_user:root` = the uniform root-ssh apply path (mirroring #775): stream
# accounts are sudo-less, so the drop-ins in /etc + the system timer can only
# be installed over `ssh root@<host>`. gk's dev1→root operator key is a
# one-time GATEKEEPER-ACTION bootstrap; until it lands `provision_disk_guard_
# root` is a fail-loud no-op for gk (exactly how #775 shipped), and the daily
# report timer + the machine-channel escalation are the standing backstops.
# `host` is each box's TAILSCALE IP (stable across LAN switches, #1).
DISK_GUARD_ROOT_HOSTS = [
    {
        "name": "subdev",
        "host": "100.118.174.27",
        "admin_user": "root",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        "name": "gatekeeper",
        "host": "100.90.94.41",
        "admin_user": "root",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
]
