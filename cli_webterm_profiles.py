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
  * ``marek`` — REMOVED (#882, 2026-09-05: marek subdev stream decommissioned,
    odoo-erp issue 6257). The profile, constants, and ``marek_inventory()`` are
    removed; historical references kept in past tense.

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
# MAREK profile REMOVED (#882, 2026-09-05: stream decommissioned, odoo-erp#6257).
DOMINIKA = "dominika"

# --------------------------------------------------------------------------- #
# Box -> profile mapping (provisioning selects the profile by hostname + the
# install ACCOUNT). subdev hosts per-developer gateways (david as david1,
# dominika as dominika), so the account disambiguates which one this install
# provisions. marek was decommissioned (#882, 2026-09-05).
# --------------------------------------------------------------------------- #

def profile_for_host(nodename, account=None):
    """Which webterm profile a box provisions, by its ``os.uname().nodename`` and
    the install ``account`` (``_whoami()``): dev1 -> owner (the single gateway,
    unchanged, account-independent); subdev -> dominika when the install runs as
    the ``dominika`` account, else david (its own account david1, and the safe
    default — each provisioner ALSO prereq-gates on its own account, so a
    non-matching account is a no-op there anyway). Any other box -> None
    (webterm is not a service there). ``account`` defaults to None so the
    pre-#612-scope-add call ``profile_for_host(nodename)`` is byte-identical
    (subdev -> david). marek was decommissioned (#882, 2026-09-05)."""
    if nodename == "dev1":
        return OWNER
    if nodename == "subdev":
        # marek profile REMOVED (#882)
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
# marek profile REMOVED (#882, 2026-09-05: marek subdev stream decommissioned,
# odoo-erp issue 6257). Constants, marek_inventory(), and MAREK_GATEWAY_USER
# were here; the live lane no longer exists.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# dominika profile — session set (#867 scope-add 2026-09-04, owner request:
# "pridat noveho webterm uzivatela dominika … aby mala pristup k m5, miva").
# --------------------------------------------------------------------------- #

# dominika's gateway runs AS this account on subdev. She has NO local attach —
# she is a PURE OBSERVER of two OTHER streams, so BOTH her tabs are loopback
# ssh (montalu5@subdev + miva1@subdev), always via the dedicated dominika key.
DOMINIKA_GATEWAY_USER = "dominika"

# Dedicated key for the dominika lane's ssh tabs — the WEBTERM_DAVID_IDENTITY
# shape: authorized ONLY on the two targets below (montalu5@subdev +
# miva1@subdev over loopback), NEVER the fleet gatekeeper key
# (`~/.secrets/gatekeeper_access_ed25519`, which reaches every stream — a
# cross-stream escalation). The key + its authorized_keys distribution is a
# provisioning step (owner-action, see _DOMINIKA_GO_LIVE in
# cli_webterm_dominika.py); until it lands BOTH ssh tabs fail VISIBLY.
WEBTERM_DOMINIKA_IDENTITY = "~/.secrets/webterm_dominika_ed25519"


def dominika_inventory():
    """dominika's SCOPED session set (#867, owner request 2026-09-04) — TWO
    entries, in the owner-defined tab order (WEBTERM_DASHBOARD_TABS["dominika"]):

      1. montalu5-subdev — an OBSERVE tab: a montalu-family stream, ssh over
         loopback with the dedicated key. CROSS-TENANT (NO u_tenant): montalu5
         is not dominika's own account (she operates nothing), so she merely
         watches it — reading its tickets-status would be a cross-tenant read;
      2. miva1-subdev — an OBSERVE tab: a SEPARATE external sub-dev stream
         (cli_fleet "5th sub-dev stream", notify-routed to the OWNER), ssh over
         loopback with the dedicated key. Also CROSS-TENANT (NO u_tenant).

    This — and ONLY this — is what dominika's ttyd is launched against, so it is
    her full connect allowlist: no other stream's id, no david id, no
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
    """The session set for ``profile``: the david set for ``david``, the
    dominika set for ``dominika``, else the full ``fleet_inventory`` (owner —
    unchanged). Only the OWNER path needs the fleet (built by the caller via the
    airuleset facade); the david/dominika sets are self-contained here so this
    leaf never imports airuleset. marek was removed (#882, 2026-09-05)."""
    if profile == DAVID:
        return david_inventory()
    # marek profile REMOVED (#882)
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
      * a shared/OWNER-account target (newlevel@dev1/dev2 — david's
        codex-bridge) never carries the field: those per-cwd
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
