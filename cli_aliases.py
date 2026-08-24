"""airuleset — shared fleet TARGET-ALIAS derivation (#592).

The SINGLE source for a box's short target alias (dev1, dev2, gk, mN, dN,
miva, siN, ...). Drawn on by BOTH:
  * cli_webterm._short_alias  — the webterm dashboard tab names (#579), and
  * cli_tmux_provisioning     — the per-box tmux WINDOW name (#592),
so a webterm tab and a tmux window can NEVER drift apart (never a second
parallel map). Extracted VERBATIM from cli_webterm._short_alias's own body
(the #579 mapping), parameterized on (user, box_name) so the tmux-provisioning
caller — which has the unix user + hostname but no webterm inventory entry —
can reuse the identical logic.

Deliberately stdlib-only (`re`), zero `import airuleset` / no other airuleset
module import, so it stays cheap on cli_webterm's import-light connect path.
"""
import re


def short_target_alias(user, box_name):
    """A Windows-Terminal-style SHORT target alias from a box's unix `user` and
    `box_name` (a hostname for tmux provisioning, or the webterm inventory id).

    dev1/dev2 share the `newlevel` unix user, so the BOX NAME disambiguates
    those (and any other owner box on the shared `newlevel` account); every
    stream family (montalu/david/miva/simap) and the gk account key off the
    USER. An unrecognized box gets a sensible short form (never empty for a
    non-empty input). Behaviour is byte-identical to the pre-#592
    cli_webterm._short_alias for every input the webterm inventory produces
    (every entry carries an `id`). The one divergence is unreachable: the old
    code short-circuited on `id == "dev1"` alone, this on `box_name == "dev1"`
    where the caller passes `id or label`, so an entry with a falsy id AND
    `label == "dev1"` would differ -- `_tab_sessions` always sets `id`, so no
    real entry hits it."""
    box_name = box_name or ""
    # dev1 short-circuits first (mirrors the old `local or id=="dev1"` check),
    # regardless of user. #592-review (B5): this is keyed on the BARE hostname
    # `dev1`/`dev2` the fleet actually uses (machine-identities); a stream account
    # would only collapse to `dev1` if literally provisioned on a host named
    # `dev1` (streams live on `subdev`). If a box's `os.uname().nodename` were ever
    # an FQDN/uppercase (`dev2.example.com`), the `newlevel` branch's `[:8]` could
    # yield an alias failing `_SAFE_STREAM_NAME_RE` -> the caller STRIPS the block
    # (fail-safe, non-corrupting) rather than shipping broken tmux syntax.
    if box_name == "dev1":
        return "dev1"
    # #661: spinbike-vps -> "sb", the owner's canonical short alias (owner
    # ROZHODNUTÉ 2026-08-24). A recognized owner box disambiguated by box name
    # like dev1 -- kept at the SINGLE alias source (#592) so the webterm dashboard
    # tab and the tmux WINDOW name agree, regardless of the box's unix user.
    if box_name.split("-")[0] == "spinbike":
        return "sb"
    user = (user or "").strip()
    if user == "gatekeeper":
        return "gk"
    mo = re.match(r"^montalu(\d+)$", user)
    if mo:
        return "m" + mo.group(1)
    mo = re.match(r"^david(\d*)$", user)
    if mo:
        return "d" + (mo.group(1) or "1")   # base `david` == d1
    mo = re.match(r"^miva(\d+)$", user)
    if mo:
        return "miva" if mo.group(1) == "1" else "mv" + mo.group(1)
    mo = re.match(r"^simap(\d+)$", user)
    if mo:
        return "si" + mo.group(1)
    if user == "newlevel":
        # an owner box (dev2 / spinbike-vps) shares the `newlevel` unix user --
        # key on the box NAME, not the user.
        return (box_name.split("-")[0] or box_name)[:8]
    if user:
        return user[:8]
    return (box_name.split("-")[0] or box_name)[:8]


# The subdev STREAM FAMILIES that own client Odoo work and therefore number
# (#532/#537 thread-name suffix, #598 signature) and suffix (#596/#597 Discuss
# thread name) their client threads. `marek` (no client handovers), `gatekeeper`
# and the owner account (`newlevel`) are deliberately ABSENT -- they are not
# numbered client-handover streams. Same family stems as `short_target_alias`
# above (montalu/david/simap/miva); a `\d*` (not `\d+`) so a base stream matches.
_STREAM_FAMILY_RE = re.compile(r"^(?:montalu|david|simap|miva)(\d*)$")


def stream_number(user):
    """The canonical subdev STREAM NUMBER for a unix `user`, as a string, or
    None. THE single source (never a second map) for the #532 thread-name
    suffix, the #598 message signature, and the #596/#597 Discuss-thread-name
    guard -- materializing into one testable function the derivation that until
    now lived only as prose in `handover-compose.md`.

    A NUMBERED stream user carries its number as trailing digits: montalu2 ->
    "2", david4 -> "4", miva2 -> "2". By construction this equals the trailing
    digits of `short_target_alias(user, "")` for every numbered stream (the two
    reuse the same family stems and cannot drift). An UNNUMBERED base stream
    (montalu / david / simap / miva) maps to "1" -- the #532/#537 convention's
    own mapping (base streams are a mistake being renamed to <name>1), which is
    NOT derivable from the `\\d+` family regex (short_target_alias drops the
    digit for miva -> "miva"), so it is supplied here explicitly. Any non-stream
    user (owner / gatekeeper / marek / unknown / empty) returns None, so a caller
    (the #596 guard) stays SILENT for them -- user-only (never the box short-
    circuit `short_target_alias` carries, which is wrong for a stream number)."""
    m = _STREAM_FAMILY_RE.match((user or "").strip())
    if not m:
        return None
    return m.group(1) or "1"
