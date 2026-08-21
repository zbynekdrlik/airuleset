"""airuleset webterm PROFILES (#612) — doména → (session set + auth realm).

Rozširuje JEDNONÁJOMNÚ webterm mašinériu (#555/#584/#586) o per-developer
profily. Doména sa mapuje na (session set + auth realm):

  * ``owner`` — dev1, tailnet-only (zbynek.newlevel.media / dnešný prístup cez
    tailscale IP), CELÝ fleet inventár, login ``zbynek``. Byte-identické s
    pred-#612 správaním — owner sa nič nemení.
  * ``david`` — subdev, VEREJNÝ HTTPS front (david.newlevel.media, Cloudflare),
    session set = david1..4 (subdev) + codex-bridge (dev2), login ``david``.

Bezpečnostné invarianty (celé v tomto leaf + connect allowliste v cli_webterm):
  1. Davidov inventár = { david1..4, codex-bridge } IBA. Jeho ttyd sa spúšťa s
     TÝMTO inventárom (``--inventory``), takže ``connect_main`` allowlist NIKDY
     nevie resolvnúť owner-fleet id (dev1/gk/marek/montalu…) → refused. Session
     set je KONFIG brány, nie klientská voľba.
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

# --------------------------------------------------------------------------- #
# Box -> profile mapping (provisioning selects the profile by hostname).
# --------------------------------------------------------------------------- #

def profile_for_host(nodename):
    """Which webterm profile a box provisions, by its ``os.uname().nodename``:
    dev1 -> owner (the single tailnet gateway, unchanged), subdev -> david (the
    public developer gateway). Any other box -> None (webterm is not a service
    there). The hostnames are the fleet's real, stable node names
    (machine-identities: dev1/dev2/subdev == hostname == MagicDNS)."""
    if nodename == "dev1":
        return OWNER
    if nodename == "subdev":
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


def profile_inventory(profile, fleet_inventory):
    """The session set for ``profile``: the david set for ``david``, else the
    full ``fleet_inventory`` (owner — unchanged). Only the OWNER path needs the
    fleet (built by the caller via the airuleset facade); the david set is
    self-contained here so this leaf never imports airuleset."""
    if profile == DAVID:
        return david_inventory()
    return list(fleet_inventory)


def allowed_ids(profile, fleet_inventory):
    """The set of session ids reachable in ``profile`` — the connect allowlist.
    For ``david`` this is exactly {david1..4, codex-bridge}; the security test
    asserts NO owner-fleet id is ever a member."""
    return {e["id"] for e in profile_inventory(profile, fleet_inventory)}
