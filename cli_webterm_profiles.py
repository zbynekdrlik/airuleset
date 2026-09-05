"""airuleset webterm PROFILES (#612) — doména → (session set + auth realm).

Rozširuje JEDNONÁJOMNÚ webterm mašinériu (#555/#584/#586) o per-developer
profily. Doména sa mapuje na (session set + auth realm):

  * ``owner`` — dev1, zbynek.newlevel.media, CELÝ fleet inventár, login
    ``zbynek``. Session set je byte-identický s pred-#612 správaním. EXPONOVANIE
    brány sa mení (#635, owner ROZHODNUTÉ 2026-08-22): owner prechádza z
    tailnet-only na Cloudflare Access (email OTP, ako David), gated cez
    ``OWNER_GATEWAY_ACCESS_MODE`` v cli_webterm.py — session set + login sa
    nemenia, mení sa len front (cloudflared tunel + Access namiesto tailnet+hesla).
  * ``david`` — subdev, VEREJNÝ HTTPS front (david.newlevel.media, Cloudflare),
    session set = david1..4 (subdev) + codex-bridge (dev2), login ``david``.
  * ``marek`` — subdev (marek účet), VEREJNÝ HTTPS front (marek.newlevel.media,
    Cloudflare). Session set (#661 rework, owner ruling 2026-08-25; #787
    doplnenie 2026-08-31; owner request 2026-09-03) = marek lokálny attach +
    montalu2 + miva1 + montalu4 (loopback ssh) + jeho `marek` tmux sessions na
    dev1/dev2 + gatekeeper (gk, OBSERVE tab) + jeho forestshop VPS
    (admin@forestshop-dev) — ssh entries VŽDY cez dedikovaný
    ``WEBTERM_MAREK_IDENTITY`` kľúč, nikdy gatekeeper kľúč, nikdy sshpass vetva.

Bezpečnostné invarianty (celé v tomto leaf + connect allowliste v cli_webterm):
  1. Davidov inventár = { david1..4, codex-bridge } IBA. Jeho ttyd child dostane
     TENTO inventár cez ``WEBTERM_INVENTORY`` env premennú, ktorú launcher
     EXPORTUJE — NIE cez klientsky argv flag (ttyd ``-a`` pridáva klientom
     kontrolované ``?arg=`` hodnoty do argv, takže argv ``--inventory`` by bol
     injectovateľný; env sa cez ttyd url-args injectovať nedá — #612 review).
     Preto ``connect_main`` allowlist NIKDY nevie resolvnúť owner-fleet id
     (dev1/gk/marek/montalu…) → refused. Session set je KONFIG brány, nie
     klientská voľba.
  2. david1..4 sa dosahujú DEDIKOVANÝM kľúčom (``WEBTERM_DAVID_IDENTITY``)
     authorized VÝHRADNE na david1-4 — NIKDY fleet gatekeeper kľúč (ten dosiahne
     marek/montalu/simap/miva). Server-side reprezentácia Davidovho vlastného
     4-účtového setu; nerozširuje jeho dosah (na svoje 4 účty sa dostane už dnes).
  3. codex-bridge tab ZRKADLÍ Davidov EXISTUJÚCI dev2 prístup (owner ruling
     2026-08-21): presne ``newlevel@dev2`` cez ``~/.ssh/id_ed25519`` (david1-ov
     vlastný kľúč, frontline #4253), existujúci tmux group ``david``. ŽIADNY
     dedikovaný účet, ŽIADNY nový kľúč, ŽIADNA forced-command — trvalá izolácia
     (dedikovaný obmedzený dev2 účet) je owner-DEFERRED (viď telo #612), aplikuje
     sa keď owner dočasnú akceptáciu ukončí.
  4. Brána beží ako ``david1`` na subdev, takže ``~/.ssh/id_ed25519`` JE reálny
     david1 dev2 kľúč — mirror, nie eskalácia.

Deliberately stdlib-only (``re``/``os``), zero ``import airuleset`` / žiadny iný
airuleset modul — leaf ako ``cli_aliases.py``, drží connect cestu ľahkú.
"""

OWNER = "owner"
DAVID = "david"
MAREK = "marek"
DOMINIKA = "dominika"

# --------------------------------------------------------------------------- #
# Box -> profile mapping (provisioning selects the profile by hostname + the
# install ACCOUNT). subdev hosts MORE THAN ONE per-developer gateway (david as
# david1, marek as marek), so the account disambiguates which one this install
# provisions.
# --------------------------------------------------------------------------- #

def profile_for_host(nodename, account=None):
    """Which webterm profile a box provisions, by its ``os.uname().nodename`` and
    the install ``account`` (``_whoami()``): dev1 -> owner (the single gateway,
    unchanged, account-independent); subdev -> marek when the install runs as the
    ``marek`` account, else david (its own account david1, and the safe default —
    each provisioner ALSO prereq-gates on its own account, so a non-matching
    account is a no-op there anyway). Any other box -> None (webterm is not a
    service there). ``account`` defaults to None so the pre-#612-scope-add call
    ``profile_for_host(nodename)`` is byte-identical (subdev -> david).

    subdev is a MULTI-developer box (#612 marek scope-add 2026-08-24): david and
    marek each run their OWN gateway as their OWN unix account, so the security
    boundary is per-account (a separate scoped inventory + Access realm + tunnel
    each). The hostnames are the fleet's real, stable node names
    (machine-identities: dev1/dev2/subdev == hostname == MagicDNS)."""
    if nodename == "dev1":
        return OWNER
    if nodename == "subdev":
        if account == MAREK_GATEWAY_USER:
            return MAREK
        if account == DOMINIKA_GATEWAY_USER:
            return DOMINIKA
        return DAVID
    return None


# --------------------------------------------------------------------------- #
# david profile — session set + connect shapes.
# --------------------------------------------------------------------------- #

# The david gateway runs as this account on subdev, so ``~/.ssh/id_ed25519``
# resolves to david1's REAL dev2 key — the codex-bridge tab then mirrors his
# existing access with zero new authorization (owner ruling 2026-08-21).
DAVID_GATEWAY_USER = "david1"

# Dedicated key for the david1-4 tabs, authorized ONLY on david1-4. NEVER the
# fleet gatekeeper key (`~/.secrets/gatekeeper_access_ed25519`), which reaches
# marek/montalu/simap/miva — that would be a cross-stream escalation. Its pubkey
# distribution to david1-4 authorized_keys is needs-owner-action.
WEBTERM_DAVID_IDENTITY = "~/.secrets/webterm_david_ed25519"

# david1-4 are local unix accounts on the subdev gateway box; the gateway ssh's
# them over loopback with the dedicated key (uniform for all four, so the
# gateway user is irrelevant to correctness — it only needs the private key).
SUBDEV_LOCAL = "127.0.0.1"
DAVID_ACCOUNTS = ("david1", "david2", "david3", "david4")

# codex-bridge tab — MIRROR of David's existing dev2 ssh (owner ruling
# 2026-08-21). Same user/key/host as david1's `~/.ssh/config` `Host dev2` today:
# newlevel@<dev2 tailscale IP> via ~/.ssh/id_ed25519, the existing `david` tmux
# group. Not a dedicated restricted account (that permanent isolation is
# owner-deferred). Subdev IS on the tailnet, so it reaches dev2's tailscale IP
# even though David himself is not on the tailnet.
CODEX_ID = "codex-bridge"
CODEX_USER = "newlevel"
CODEX_HOST = "100.82.64.27"           # dev2 tailscale IP
CODEX_IDENTITY = "~/.ssh/id_ed25519"  # david1's own dev2 key (mirror, not new trust)
CODEX_PREFERRED = "david"             # the existing dev2 tmux group


def david_inventory():
    """David's SCOPED session set as webterm inventory entries (the exact shape
    ``cli_webterm.build_connect_argv`` consumes): david1..4 over loopback with
    the dedicated identity, then codex-bridge mirroring the existing dev2
    access. This — and ONLY this — is what david's ttyd is launched against, so
    it is his full connect allowlist."""
    entries = []
    for user in DAVID_ACCOUNTS:
        entries.append({
            "id": user,
            "label": user,
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": user,
            "identity": WEBTERM_DAVID_IDENTITY,
            "preferred": user,
            # #703: David's OWN account — its tickets-status caches are his
            # tenant's, so his lane gateway's scoped collector may read them
            # (u_tenant_entries below).
            "u_tenant": True,
        })
    entries.append({
        "id": CODEX_ID,
        "label": "codex-bridge (dev2)",
        "kind": "stream",
        "local": False,
        "host": CODEX_HOST,
        "user": CODEX_USER,
        "identity": CODEX_IDENTITY,
        "preferred": CODEX_PREFERRED,
        # #703: NO u_tenant — the target ACCOUNT is the OWNER's (newlevel@dev2);
        # its per-cwd tickets-status caches aggregate the OWNER's sessions, so a
        # lane U read there would be cross-tenant (#677/#684 boundary).
    })
    return entries


# --------------------------------------------------------------------------- #
# marek profile — session set (#612 scope-add 2026-08-24; #661 rework
# 2026-08-25: the owner REJECTED the single-member set as incomplete).
# --------------------------------------------------------------------------- #

# marek's gateway runs AS this account on subdev; his own primary session is a
# LOCAL tmux attach (no ssh, no key). #661 rework: the owner explicitly granted
# marek FOUR MORE tabs (montalu4, his dev1/dev2 tmux sessions, his forestshop
# VPS); #787 added a FIFTH (montalu2, mirroring montalu4); the owner request
# 2026-09-03 added two OBSERVE tabs (miva1 loopback + gatekeeper tailscale) — so
# the lane is no longer "zero ssh capability", its ssh reach is exactly the
# SEVEN ssh entries below, always via the DEDICATED marek key.
MAREK_GATEWAY_USER = "marek"

# marek's own scoped session id — first member of the owner-defined #661 tab
# policy WEBTERM_DASHBOARD_TABS["marek"] (cli_webterm.py), which the marek lane
# render consumes (LaneSpec.dashboard_human="marek").
MAREK_ID = "marek-subdev"

# Dedicated key for the marek lane's ssh tabs — the WEBTERM_DAVID_IDENTITY
# shape: authorized ONLY on the targets below (montalu2@subdev + miva1@subdev +
# montalu4@subdev over loopback, newlevel@dev1, newlevel@dev2, gatekeeper@gk over
# tailscale, admin@forestshop-dev — #787 added montalu2, the owner request
# 2026-09-03 added miva1 + gatekeeper), NEVER the fleet gatekeeper key
# (`~/.secrets/gatekeeper_access_ed25519`, which reaches every stream — a
# cross-stream escalation). A live #661 probe showed marek@subdev holds NO key
# for any of these targets, so a codex-bridge-style "mirror existing access" is
# impossible — the key + its authorized_keys distribution is a provisioning
# step (owner-action, see _MAREK_GO_LIVE in cli_webterm_marek.py); until it
# lands the ssh tabs fail VISIBLY while the local marek-subdev tab keeps
# working.
WEBTERM_MAREK_IDENTITY = "~/.secrets/webterm_marek_ed25519"

# marek's dev-box tabs ssh the boxes' tailscale IPs (subdev is on the tailnet;
# machine-identities: address by tailscale, never the drifting LAN IPs) as the
# `newlevel` account and attach the `marek` tmux session group — the SAME
# owner-group session mechanism (notify/statusbar grouping: zbynek/marek) the
# owner's own tabs use with `zbynek`, never a hardcoded session list.
MAREK_DEV1_HOST = "100.104.8.125"   # dev1 tailscale IP
MAREK_DEV2_HOST = CODEX_HOST        # dev2 tailscale IP — same box as codex-bridge

# marek's gk OBSERVE tab (owner request 2026-09-03 — "aby videl ... gk"). The
# gatekeeper box's tailscale IP, DUPLICATED verbatim from cli_fleet.py's
# `gatekeeper` REMOTE_HOSTS entry (this leaf imports no airuleset module — the
# CODEX_HOST/MAREK_DEV1_HOST precedent); a drift-lock test
# (test_webterm_marek.test_gatekeeper_host_matches_the_fleet_host) ties the copy
# to the ONE fleet source. Like dev1/dev2 it is a tailscale IP with NO #680
# host-key pin (subdev/tailnet hosts stay =no). The tab attaches the OWNER's gk
# session group (preferred=OWNER_GROUP "zbynek"), so marek OBSERVES the gk work,
# never a `marek` group on gk (gatekeeper is an owner-realm, non-stream account).
MAREK_GK_HOST = "100.90.94.41"      # gatekeeper (gk.newlevel.media) tailscale IP

# marek's forestshop VPS tab (#661 DOPLNENIE — handled like the owner's
# spinbike `sb` tab). The fleet's ONE forestshop box (cli_fleet.py): the tab
# connects as `admin`, the box's PRINCIPAL account (the forestshop-app deploy
# account; notify #572 routes the whole box to marek's realm) — NEVER `stepan`,
# StepanDK's own isolated personal account (a third person's account on
# Marek's dashboard would repeat the original #661 sin).
MAREK_FORESTSHOP_ID = "forestshop"
MAREK_FORESTSHOP_HOST = "forestshop-dev.newlevel.media"
MAREK_FORESTSHOP_USER = "admin"
# #679/#680: the box is public-DNS (no tailscale), so the connect child must
# verify its host key STRICTLY against the committed pin. Duplicated VERBATIM
# from cli_fleet.py's admin@forestshop-dev entry (this leaf imports no other
# airuleset module — the CODEX_HOST precedent); a drift-lock test
# (test_webterm_marek.test_forestshop_host_keys_match_the_fleet_pin) ties the
# copies together. PUBLIC key material, safe to commit.
MAREK_FORESTSHOP_HOST_KEYS = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF0hQYw2+OticG0PVhzzDeJzghERkK7g+WkqpDihlbiI",
    "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBHpFPlgqeS8+KP2L9KrlVSKqezEK19l8IgdDCubJPxISCF8L4X7TO/TkOkBXoYVKPgaLyEV2rva6zlihdef4h9o=",
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCM20xevli5Jj4pdx3m0lQs7m81ZMY6+b20kIwrtM1hLjbEV9JOW7G2P15zcCEeHwtkqn36BSERbkKVX9tf8aXy7TD+Wh80o70cUhh77r2janngtCGkHNWbag/Q9mvOrIos6f1BQjkMlH77g6O5Fav5ZaOADzKPlyP9EqYc++ZIrGkaoqeJUirFGVVY7OhdF5Zx2g4UUfEv92SxvAB9W6mWVabotoFdEh2qlY0iX8o7uL0vTim63E82E1dxU2QkYH6mtMimn8rU1oNfg3IM5N2ZzIar3U6XwlcQmNkNm7Xjj2Fl95F1r6s4V363b3UrnDeK+qf1EtJMv9bOILDIJgqGDU+OQBEfvA/Y9jfeaC4LxO4JeniRcgJVIH8gyPhmOUJ7/RSp/R+7394KXo1ueKv1DVZKN0V99GLmUUwHjT8Eh6tg+Ma5tOoj81jHrRyJ07qYOC34ERvfTeH6bctKmCA73wsTXTbOVq6lPL9X6w/Disnfu6EBLPFLSiwFEHbNnLk=",
]


def marek_inventory():
    """marek's SCOPED session set (#661 rework, owner ruling 2026-08-25; #787
    doplnenie 2026-08-31 added montalu2; owner request 2026-09-03 added
    miva1 + gatekeeper) — EIGHT entries, in the owner-defined tab order
    (WEBTERM_DASHBOARD_TABS["marek"]):

      1. marek-subdev — his own tmux group, a LOCAL attach (the gateway runs as
         marek; no ssh, no key — unchanged from #612);
      2. montalu2-subdev — his second montalu stream (#787), ssh over loopback
         with the dedicated key — mirrors montalu4-subdev exactly except for
         the account name;
      3. miva1-subdev — an OBSERVE tab (owner request 2026-09-03): the miva1
         subdev stream, ssh over loopback with the dedicated key. Mirrors the
         montalu2/4 loopback shape but is CROSS-TENANT (NO u_tenant): miva1 is
         a SEPARATE external sub-dev stream (notify routes it to the OWNER,
         not marek's realm), so marek observes it, never a within-tenant read;
      4. montalu4-subdev — his montalu stream, ssh over loopback with the
         dedicated key (the david1..4 shape);
      5./6. dev1/dev2 — his `marek` tmux session group on the owner dev boxes,
         ssh newlevel@<tailscale IP> with the dedicated key (codex-bridge is
         the cross-box precedent);
      7. gatekeeper — an OBSERVE tab (owner request 2026-09-03): the gk box,
         ssh gatekeeper@<gk tailscale IP> with the dedicated key, attaching the
         OWNER's gk session group (preferred="zbynek"). NO u_tenant (owner-realm
         account, same as dev1/dev2, #703); no #680 host-key pin (tailscale,
         like dev1/dev2);
      8. forestshop — his VPS's principal account admin@forestshop-dev with the
         dedicated key + the #679 strict host-key pin (the owner `sb` shape).

    This — and ONLY this — is what marek's ttyd is launched against, so it is
    his full connect allowlist: no other stream's id, no david id, and no other
    person's account can ever be present. Every ssh entry carries
    WEBTERM_MAREK_IDENTITY explicitly, so the connect child never takes the
    sshpass shared-password branch and never touches the gatekeeper key."""
    return [
        {
            "id": MAREK_ID,
            "label": "marek@subdev",
            "kind": "stream",
            "local": True,
            "host": None,
            "user": MAREK_GATEWAY_USER,
            "identity": None,
            "preferred": MAREK_GATEWAY_USER,   # the local `marek` tmux group
            # #703: marek's OWN gateway account — a LOCAL within-tenant read.
            "u_tenant": True,
        },
        {
            "id": "montalu2-subdev",
            "label": "montalu2@subdev",
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": "montalu2",
            "identity": WEBTERM_MAREK_IDENTITY,
            "preferred": "montalu2",
            # #787: mirrors montalu4-subdev — marek's own montalu stream
            # account, within-tenant.
            "u_tenant": True,
        },
        {
            "id": "miva1-subdev",
            "label": "miva1@subdev",
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": "miva1",
            "identity": WEBTERM_MAREK_IDENTITY,
            "preferred": "miva1",
            # owner request 2026-09-03 ("aby videl subdev miva"): an OBSERVE tab.
            # NO u_tenant — miva1 is a SEPARATE external sub-dev stream (cli_fleet
            # "5th sub-dev stream", peer to david/simap), notify-routed to the
            # OWNER `zbynek` (notify/__init__.py "miva1": "zbynek"), NOT marek's
            # realm; reading its tickets-status would be a CROSS-TENANT read, the
            # same boundary dev1/dev2 keep by omitting the field.
        },
        {
            "id": "montalu4-subdev",
            "label": "montalu4@subdev",
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": "montalu4",
            "identity": WEBTERM_MAREK_IDENTITY,
            "preferred": "montalu4",
            # #703: marek's own montalu stream account — within-tenant.
            "u_tenant": True,
        },
        {
            "id": "dev1",
            "label": "dev1 (marek sessions)",
            "kind": "stream",
            "local": False,
            "host": MAREK_DEV1_HOST,
            "user": "newlevel",
            "identity": WEBTERM_MAREK_IDENTITY,
            "preferred": MAREK_GATEWAY_USER,   # his session group on dev1
            # #703: NO u_tenant — newlevel@dev1 is the OWNER's account; its
            # per-cwd tickets-status caches aggregate the OWNER's sessions
            # (cross-tenant). The forced-command key couldn't run the reader
            # anyway, but the boundary is the OPT-IN, not that accident.
        },
        {
            "id": "dev2",
            "label": "dev2 (marek sessions)",
            "kind": "stream",
            "local": False,
            "host": MAREK_DEV2_HOST,
            "user": "newlevel",
            "identity": WEBTERM_MAREK_IDENTITY,
            "preferred": MAREK_GATEWAY_USER,   # his session group on dev2
            # #703: NO u_tenant — owner account, same as dev1 above.
        },
        {
            "id": "gatekeeper",
            "label": "gk (gatekeeper@gk)",
            "kind": "stream",
            "local": False,
            "host": MAREK_GK_HOST,
            "user": "gatekeeper",
            "identity": WEBTERM_MAREK_IDENTITY,
            # owner request 2026-09-03 ("aby videl ... gk"): an OBSERVE tab.
            # preferred = the OWNER fleet inventory's gatekeeper group
            # (cli_webterm.OWNER_GROUP == "zbynek"; gatekeeper is NOT a stream in
            # AUTHORITY_BY_USER, so the owner inventory renders it with
            # OWNER_GROUP) — so marek attaches the OWNER's gk session group, not
            # a new `marek` group on gk. NO u_tenant — the gatekeeper account is
            # owner-realm (same cross-tenant reasoning as dev1/dev2, #703); no
            # #680 host-key pin (tailscale IP, like dev1/dev2).
            "preferred": "zbynek",
        },
        {
            "id": MAREK_FORESTSHOP_ID,
            "label": "forestshop (admin@forestshop-dev)",
            "kind": "stream",
            "local": False,
            "host": MAREK_FORESTSHOP_HOST,
            "user": MAREK_FORESTSHOP_USER,
            "identity": WEBTERM_MAREK_IDENTITY,
            "host_keys": MAREK_FORESTSHOP_HOST_KEYS,   # #680 strict pin
            "preferred": MAREK_GATEWAY_USER,
            # #703: marek's realm box (notify #572 routes it to his realm),
            # principal account — within-tenant; the U read honors the #680
            # host-key pin (cli_webterm._ssh_read_prefix).
            "u_tenant": True,
        },
    ]


# --------------------------------------------------------------------------- #
# dominika profile — session set (#867 scope-add 2026-09-04, owner request:
# "pridat noveho webterm uzivatela dominika … aby mala pristup k m5, miva").
# --------------------------------------------------------------------------- #

# dominika's gateway runs AS this account on subdev. Unlike marek she has NO
# local attach — she is a PURE OBSERVER of two OTHER streams, so BOTH her tabs
# are loopback ssh (montalu5@subdev + miva1@subdev), always via the dedicated
# dominika key.
DOMINIKA_GATEWAY_USER = "dominika"

# Dedicated key for the dominika lane's ssh tabs — the WEBTERM_DAVID_IDENTITY /
# WEBTERM_MAREK_IDENTITY shape: authorized ONLY on the two targets below
# (montalu5@subdev + miva1@subdev over loopback), NEVER the fleet gatekeeper key
# (`~/.secrets/gatekeeper_access_ed25519`, which reaches every stream — a
# cross-stream escalation). The key + its authorized_keys distribution is a
# provisioning step (owner-action, see _DOMINIKA_GO_LIVE in
# cli_webterm_dominika.py); until it lands BOTH ssh tabs fail VISIBLY (dominika
# has NO keyless local tab to fall back to, unlike marek's own subdev attach).
WEBTERM_DOMINIKA_IDENTITY = "~/.secrets/webterm_dominika_ed25519"


def dominika_inventory():
    """dominika's SCOPED session set (#867, owner request 2026-09-04) — TWO
    entries, in the owner-defined tab order (WEBTERM_DASHBOARD_TABS["dominika"]):

      1. montalu5-subdev — an OBSERVE tab: marek's own montalu-family stream,
         ssh over loopback with the dedicated key. CROSS-TENANT (NO u_tenant):
         montalu5 is not dominika's own account (she operates nothing), so she
         merely watches it — reading its tickets-status would be a cross-tenant
         read, the same boundary marek's observe tabs keep by omitting the field;
      2. miva1-subdev — an OBSERVE tab: a SEPARATE external sub-dev stream
         (cli_fleet "5th sub-dev stream", notify-routed to the OWNER), ssh over
         loopback with the dedicated key. Also CROSS-TENANT (NO u_tenant).

    This — and ONLY this — is what dominika's ttyd is launched against, so it is
    her full connect allowlist: no other stream's id, no david/marek id, no
    owner-realm box, and no other person's account can ever be present. Every ssh
    entry carries WEBTERM_DOMINIKA_IDENTITY explicitly, so the connect child never
    takes the sshpass shared-password branch and never touches the gatekeeper key.
    Neither entry is `u_tenant` (both cross-tenant OBSERVE), so
    ``u_tenant_entries("dominika")`` is empty — her lane collector reads nothing."""
    return [
        {
            "id": "montalu5-subdev",
            "label": "montalu5@subdev",
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": "montalu5",
            "identity": WEBTERM_DOMINIKA_IDENTITY,
            "preferred": "montalu5",
            # #867: OBSERVE-only, CROSS-TENANT — NO u_tenant (montalu5 is not
            # dominika's own account; she watches it, never a within-tenant read).
        },
        {
            "id": "miva1-subdev",
            "label": "miva1@subdev",
            "kind": "stream",
            "local": False,
            "host": SUBDEV_LOCAL,
            "user": "miva1",
            "identity": WEBTERM_DOMINIKA_IDENTITY,
            "preferred": "miva1",
            # #867: OBSERVE-only, CROSS-TENANT — NO u_tenant (miva1 is a separate
            # external stream notify-routed to the OWNER, never dominika's tenant).
        },
    ]


def profile_inventory(profile, fleet_inventory):
    """The session set for ``profile``: the david set for ``david``, the marek set
    for ``marek``, the dominika set for ``dominika``, else the full
    ``fleet_inventory`` (owner — unchanged). Only the OWNER path needs the fleet
    (built by the caller via the airuleset facade); the david/marek/dominika sets
    are self-contained here so this leaf never imports airuleset."""
    if profile == DAVID:
        return david_inventory()
    if profile == MAREK:
        return marek_inventory()
    if profile == DOMINIKA:
        return dominika_inventory()
    return list(fleet_inventory)


def allowed_ids(profile, fleet_inventory):
    """The set of session ids reachable in ``profile`` — the connect allowlist.
    For ``david`` this is exactly {david1..4, codex-bridge}; the security test
    asserts NO owner-fleet id is ever a member."""
    return {e["id"] for e in profile_inventory(profile, fleet_inventory)}


def u_tenant_entries(profile):
    """#703: the per-tenant U-collection set for a LANE gateway — exactly the
    lane's own inventory entries explicitly marked ``u_tenant: True`` (an
    OPT-IN meaning: the target ACCOUNT belongs to this lane's tenant, so
    reading its ``~/.claude/tickets-status`` caches is a within-tenant read).

    Fail-closed on every axis:
      * an entry WITHOUT the field is never collected — adding a new tab does
        not silently add U collection;
      * a shared/OWNER-account target (newlevel@dev1/dev2 — marek's dev
        tabs, david's codex-bridge) never carries the field: those per-cwd
        caches aggregate the OWNER's sessions, so a lane read there would be
        cross-tenant (the #677/#684 boundary this ticket keeps intact);
      * an ssh entry WITHOUT an explicit identity is DROPPED here even if
        mis-marked, so a lane collector can never reach the
        ``_ssh_read_prefix`` sshpass shared-password branch;
      * the owner / an unknown profile yields ``[]`` (the owner's
        cross-tenant collector is the separate #677 ``--u-collect`` path,
        untouched by #703).

    The result is therefore always a SUBSET of the lane's existing connect
    allowlist (same entries, same dedicated identities, minus the
    owner-account ones) — per-tenant U collection grants a lane account ZERO
    new reach. ("Zero new reach" is w.r.t. the lane's EXISTING connect
    capability: the U read uses the same identity-vs-sshpass decision as
    ``_ssh_interactive_prefix`` — with no ``IdentitiesOnly=yes`` it may also
    offer the account's own default/agent keys, exactly as the connect path
    already does, so a lane account still reaches only what it already could.)"""
    return [e for e in profile_inventory(profile, [])
            if e.get("u_tenant") is True
            and (e.get("local") or e.get("identity"))]
