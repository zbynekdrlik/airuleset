"""ack_reaction — the fleet ack-reaction emoji, the single config key
`ack_reaction_emoji` (#1027 / #1033).

Owner directives (miva1 + montalu sessions 2026-09-14): the fleet ack reaction
is the WORKER 👷 (U+1F477), the owner's standing instruction for HIS client
channels. #978 had fixed the fleet emoji to 👀 (eyes); the owner escalated very
angrily that eyes overrode his explicit worker instruction
("Preco davas oci ked som sa velakrat vyjadril ze treba dat pracovnika!!!") and
that it must be an airuleset rule, not something re-taught per sub-dev
("to ma byt airuleset zasada a nie jak blby to musim riesit s kazdym subdevom").

So the fleet DEFAULT is now the worker 👷, with a per-owner / per-tenant
override map (empty by default — every owner gets the default) so an owner who
prefers a different emoji for HIS channel can select it centrally instead of the
fleet default silently overriding an explicit instruction. The legacy 👀 stays
selectable via that override.

STDLIB ONLY, no side effects at import — safe for the watchdog and the tests to
import at module load. The per-owner mechanism mirrors `notify.resolve_owner()`
(the same owner strings: 'zbynek' / 'david' / …), reusing the fleet's existing
owner-resolution framework rather than a second identity map.

RULE: an explicit owner instruction about HIS client channel overrides the fleet
default, and a stream that hits a conflict ESCALATES it to airuleset (a config
override here) instead of following the default — never a per-stream memory
stopgap (the exact per-stream re-teaching loop #1027/#1033 close).
"""

# Fleet default — the owner's standing instruction (#1027 / #1033).
ACK_REACTION_EMOJI_DEFAULT = "\U0001F477"   # 👷 worker (pracovnik)

# The legacy #978 fleet default, still selectable via an override below.
ACK_REACTION_EMOJI_EYES = "\U0001F440"      # 👀 eyes

# The set the enforcement (the `stop-check-prose-violations.sh` Ack-reaction
# evidence check) accepts as a valid ack emoji. The worker default plus the
# legacy eyes — both remain valid so a stream on an override never trips the
# gate.
SELECTABLE_ACK_EMOJIS = (ACK_REACTION_EMOJI_DEFAULT, ACK_REACTION_EMOJI_EYES)

# Per-owner override: owner (a lowercase `notify.resolve_owner()` value) ->
# emoji. EMPTY by default — every owner gets the fleet default 👷. Populate to
# give a specific owner a different ack emoji for HIS channels, e.g.
# {"someowner": ACK_REACTION_EMOJI_EYES}.
ACK_REACTION_EMOJI_BY_OWNER = {}

# Per-tenant override: tenant / client-instance key -> emoji. HIGHEST
# precedence (a specific client instance can pin its own emoji). Empty by
# default.
ACK_REACTION_EMOJI_BY_TENANT = {}


def ack_reaction_emoji(owner=None, tenant=None):
    """Resolve the ack-reaction emoji for `owner` / `tenant`.

    Precedence: per-tenant override > per-owner override > fleet default 👷.
    Never raises; an unknown / unusable owner or tenant falls back to the
    fleet default (the fail-safe direction — a client always gets the standing
    worker ack rather than an exception)."""
    try:
        if tenant:
            key = str(tenant).strip().lower()
            if key in ACK_REACTION_EMOJI_BY_TENANT:
                return ACK_REACTION_EMOJI_BY_TENANT[key]
        if owner:
            key = str(owner).strip().lower()
            if key in ACK_REACTION_EMOJI_BY_OWNER:
                return ACK_REACTION_EMOJI_BY_OWNER[key]
    except Exception:  # noqa: BLE001 — resolution never breaks a caller
        return ACK_REACTION_EMOJI_DEFAULT
    return ACK_REACTION_EMOJI_DEFAULT
