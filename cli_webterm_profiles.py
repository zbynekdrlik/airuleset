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
    })
    return entries


# --------------------------------------------------------------------------- #
# marek profile — session set (#612 scope-add 2026-08-24).
# --------------------------------------------------------------------------- #

# marek's gateway runs AS this account on subdev, so his session set is a single
# LOCAL tmux attach — NO ssh, NO key. That is a STRICTLY SMALLER reachability than
# david's ssh-based set: marek's ttyd child has zero ssh capability, so even a
# gateway compromise reaches nothing but the local marek tmux group.
MAREK_GATEWAY_USER = "marek"

# marek's single scoped session id — matches the owner-defined #661 tab policy
# WEBTERM_DASHBOARD_TABS["marek"] = ["marek-subdev"].
MAREK_ID = "marek-subdev"


def marek_inventory():
    """marek's SCOPED session set — a SINGLE LOCAL entry: his own tmux group on
    subdev (the gateway runs as marek, so this is a local attach, never ssh).
    This — and ONLY this — is what marek's ttyd is launched against, so it is his
    full connect allowlist: no owner-fleet id and no david id can ever be present.

    A ``local`` entry (like the owner's dev1 entry) makes ``build_connect_argv``
    emit ``sh -c <tmux attach>`` with NO ssh/identity — the strongest possible
    scoping (no key to compromise, no host to reach)."""
    return [{
        "id": MAREK_ID,
        "label": "marek@subdev",
        "kind": "stream",
        "local": True,
        "host": None,
        "user": MAREK_GATEWAY_USER,
        "identity": None,
        "preferred": MAREK_GATEWAY_USER,   # the local `marek` tmux group
    }]


def profile_inventory(profile, fleet_inventory):
    """The session set for ``profile``: the david set for ``david``, the marek set
    for ``marek``, else the full ``fleet_inventory`` (owner — unchanged). Only the
    OWNER path needs the fleet (built by the caller via the airuleset facade); the
    david/marek sets are self-contained here so this leaf never imports airuleset."""
    if profile == DAVID:
        return david_inventory()
    if profile == MAREK:
        return marek_inventory()
    return list(fleet_inventory)


def allowed_ids(profile, fleet_inventory):
    """The set of session ids reachable in ``profile`` — the connect allowlist.
    For ``david`` this is exactly {david1..4, codex-bridge}; the security test
    asserts NO owner-fleet id is ever a member."""
    return {e["id"] for e in profile_inventory(profile, fleet_inventory)}
