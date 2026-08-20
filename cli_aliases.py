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
    cli_webterm._short_alias for every input that function accepted."""
    box_name = box_name or ""
    # dev1 short-circuits first (mirrors the old `local or id=="dev1"` check),
    # regardless of user.
    if box_name == "dev1":
        return "dev1"
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
