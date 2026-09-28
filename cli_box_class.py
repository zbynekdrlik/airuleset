"""The ONE box-class classifier of a UNIX account (#778 shared-stream, #870 F3
controller, #998 gk) — shared by the `~/.claude/airuleset-box-class` marker
writer (`airuleset._write_box_class_marker`) and the #1174 nudge-profile
resolver (`cli_nudge_profiles.resolve_profile`), so the two can never drift.

Zero imports: the caller passes the stream-account registry
(`AUTHORITY_BY_USER`) so a test's `patch.object(airuleset, "AUTHORITY_BY_USER",
...)` on the facade keeps working for the marker writer.
"""

BOX_CLASSES = ("controller", "gk", "shared-stream", "workstation")


def box_class_for_user(user, stream_users):
    """`controller` for the control-plane account `airuleset`, `gk` for
    `gatekeeper`, `shared-stream` for a reduced-authority stream account (a key
    of `stream_users`), else `workstation` (dev1/dev2/spinbike/forestshop/…)."""
    if user == "airuleset":
        return "controller"
    if user == "gatekeeper":
        return "gk"
    if user in stream_users:
        return "shared-stream"
    return "workstation"
