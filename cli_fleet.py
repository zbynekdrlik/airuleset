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

ZERO imports and ZERO logic by design — this is pure data (a list of host dicts,
a profiles tuple, a user->profile map). No ``import airuleset`` anywhere: this
leaf has no outbound couplings, it is a pure leaf of the dependency DAG.
"""

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
        # Isolated montalu odoo dev stream — MIGRATED 2026-07-24 from dev1 to
        # the subdev VPS (airuleset#33 + odoo-erp#1895; same box as marek and
        # david: tailscale 100.118.174.27 / MagicDNS "subdev", public
        # subdev.newlevel.media = fallback only — address by tailscale per
        # machine-identities). The old dev1 account (uid 1001) is LOCKED with
        # a ForceCommand redirect notice; /home/montalu on dev1 stays
        # untouched as the rollback backup per the #1895 contract. Unlike
        # marek/david, montalu authorizes the DEFAULT newlevel key (no
        # gatekeeper_access identity — live-verified at the swap).
        #
        # #258 (2026-08-05): this exact key (dev1's own default
        # ~/.ssh/id_ed25519, comment "david grena mac" for unrelated
        # historical reasons — NOT david@subdev's key) got stripped from
        # montalu's authorized_keys by a gatekeeper access review that
        # mistook the misleading comment for the real cross-company
        # david@subdev identity. Restored via root@subdev under a
        # corrected comment. If push to montalu@subdev ever silently
        # starts failing with "Permission denied" again, check
        # authorized_keys FIRST before assuming a code regression.
        "name": "montalu@subdev",
        "host": "100.118.174.27",
        "user": "montalu",
        "repo_path": "~/devel/airuleset",
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
        # Marek's isolated user — MIGRATED 2026-07-21/22 from the gatekeeper
        # VPS to the dedicated subdev VPS (Hetzner cx33/nbg1 "subdev", project
        # odoo-subdev, id 153587360): tailscale 100.118.174.27 / MagicDNS
        # "subdev", public 116.203.108.177 = subdev.newlevel.media (fallback
        # only — address by tailscale per machine-identities). Old marek@gk
        # account is BLOCKED (ForceCommand notice). Same gatekeeper_access key
        # (authorized_keys byte-copied in the migration). Evidence: airuleset
        # #23 + odoo-erp #1895 hand-over comments.
        "name": "marek@subdev",
        "host": "100.118.174.27",
        "user": "marek",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # David's isolated external-dev user (slovnormal odoo dev stream: no
        # sudo, no prod keys, can't read other homes) — MIGRATED 2026-07-22
        # from the gatekeeper VPS to the same subdev VPS as marek (see the
        # marek@subdev entry above for the box facts). Old david@gk account is
        # BLOCKED (ForceCommand notice). Same gatekeeper_access key.
        "name": "david@subdev",
        "host": "100.118.174.27",
        "user": "david",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # miva1 -- 5th sub-dev stream, phase-1 isolated, on the same subdev
        # VPS as marek/david/simap (airuleset#300; tracking ticket for the
        # account itself is odoo-erp#3223). Built by gatekeeper: bare linux
        # user + own SSH keypair, read-only GitHub deploy key, `develop`
        # checkout, empty tmux session -- but no airuleset config until this
        # entry lands. Registered with the SAME operator gatekeeper_access
        # identity requirement as marek/david/simap (never montalu's
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
        # david itself (odoo-erp#3282). Registered here as a data-only
        # mirror of david's own entry (host + identity requirement) -- the
        # identity ASSUMPTION is unverified for these specific accounts
        # (mirroring david's shape is the registration; it does not confirm
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
        # same "default newlevel key, no identity" shape montalu@subdev
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
    },
    {
        # montalu1/david1/simap1 (#537, 2026-08-18): the NUMBERED push targets
        # for the base-stream rename, registered ALONGSIDE the base entries
        # above so the fleet table already knows the coming names. They carry
        # `"pending": True` because the LIVE unix rename has NOT happened yet —
        # the accounts do NOT exist on subdev, so `_deployable_hosts()`
        # (cli_remote.py) filters them out of EVERY ssh path (the deploy loop
        # AND provision_subdev_soniox_key). This is fail2ban-critical:
        # montalu1 authenticates via the shared default key/sshpass path, and a
        # password attempt against a non-existent account is a fail2ban strike
        # (#341/#300/#326). The live-op rename ticket removes the `"pending"`
        # flag (and the old entry) once each account is created + verified.
        # Identity mirrors the base: montalu1 = default newlevel key (no
        # `identity`, like montalu); david1/simap1 = the operator
        # gatekeeper_access identity (like david/simap).
        "name": "montalu1@subdev",
        "host": "100.118.174.27",
        "user": "montalu1",
        "repo_path": "~/devel/airuleset",
        "pending": True,
    },
    {
        "name": "david1@subdev",
        "host": "100.118.174.27",
        "user": "david1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
        "pending": True,
    },
    {
        # simap1 — the renamed 4th sub-dev stream (was `simap`; #537 live
        # rename 2026-08-18: in-place usermod on subdev, uid 1003 kept,
        # home moved to /home/simap1, linger re-enabled; the old
        # `simap@subdev` entry + its AUTHORITY_BY_USER row are GONE — the
        # OS account no longer exists). Original build history: airuleset#143
        # (Odoo 19 demo, gatekeeper_access identity, marek's operator keys).
        "name": "simap1@subdev",
        "host": "100.118.174.27",
        "user": "simap1",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
]


# Autopilot authority profiles (issue #16, 2026-07-09). A stream's authority is a
# property of its LINUX USER (streams are separate users by construction: david /
# marek / montalu), resolved at RUNTIME — no per-box state to lose on a home-dir
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
# A project CLAUDE.md marker `airuleset:authority=<profile>` OVERRIDES the user
# default (checked by the /autopilot skill, not here). Only the user adds markers.
AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")
AUTHORITY_BY_USER = {
    "david": "fork-no-merge",
    "marek": "branch-merge",
    "montalu": "branch-merge",
    # simap (airuleset#143) was renamed to simap1 (#537, 2026-08-18) — its
    # row moved to the numbered block below; the OS account `simap` is gone.
    # montalu2/montalu3/montalu4 (airuleset#251, odoo-erp#2961): three MORE
    # full parallel montalu streams — same authority as montalu itself.
    "montalu2": "branch-merge",
    "montalu3": "branch-merge",
    "montalu4": "branch-merge",
    # miva1 (airuleset#300, 2026-08-07): phase-1 isolated stream, same shape
    # as simap -- merges nowhere, fork-no-merge is already correct.
    "miva1": "fork-no-merge",
    # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE clones of
    # the david external-developer fork stream (additional capacity for the
    # same slovnormal odoo developer) -- same authority as david itself.
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
    # montalu1/david1/simap1 (#537, 2026-08-18): the NUMBERED names for the
    # base-stream rename (owner directive on #532 — montalu->montalu1,
    # david->david1, simap->simap1; marek STAYS marek, deliberately NOT
    # renamed). Added ALONGSIDE the base entries above (which stay until each
    # live unix rename lands — removing them is the live-op ticket's job);
    # each inherits its base's authority profile so the moment a box actually
    # runs as the new name it resolves correctly. STREAM_RENAME_ALIASES
    # (below) drives the transition alias so old `stream:<base>` tickets keep
    # working during the switch, in BOTH directions.
    "montalu1": "branch-merge",
    "david1": "fork-no-merge",
    "simap1": "fork-no-merge",
}


# Base-stream rename map (#537): old base name -> new numbered name. The SINGLE
# explicit source of truth for the in-progress rename, so `cli_quals`'
# `_stream_rename_equivalents()` (the alias primitive that `_slice_quals` and
# `_ticket_is_stream_labeled` both consume) has one table to read, never a
# scattered set of literals. `marek` is deliberately ABSENT — the owner keeps
# it un-renamed. Read via `airuleset.STREAM_RENAME_ALIASES` (the facade
# re-export), NEVER `cli_fleet.STREAM_RENAME_ALIASES` directly, so a
# `patch.object(airuleset, "STREAM_RENAME_ALIASES", ...)` in a test is honoured
# (the same L-E rule AUTHORITY_BY_USER above follows).
STREAM_RENAME_ALIASES = {
    "montalu": "montalu1",
    "david": "david1",
    "simap": "simap1",
}
