"""Which accounts have a client Odoo board (#1258).

The slovnormal client does not use Odoo projects or tasks (owner 24.10.2026), so
its streams have NO client board. `airuleset.py install` writes BOARD_MARKER =
"off" for these accounts; the question gate (Check 9 in
`hooks/stop-check-question-quality.sh`) and `hooks/inject-situational-rule.sh`
read the marker, because a bash hook cannot query this table.

The montalu board profile, its Verifikácia auto-close wait (#1167) and the
Job 49 task-hygiene overseer were removed with the montalu client (#1306).
"""
import os

NO_BOARD_USERS = frozenset({"david1", "david2", "david3", "david4"})
BOARD_MARKER = "airuleset-client-board"


def board_enabled(user):
    """False for an account whose client has no Odoo board (#1258)."""
    return user not in NO_BOARD_USERS


def write_board_marker(claude_dir, user):
    """install step (#1258): write the marker for a no-board account, remove a
    stale one elsewhere. Returns a one-line status for the install output."""
    path = os.path.join(str(claude_dir), BOARD_MARKER)
    if board_enabled(user):
        if not os.path.exists(path):
            return "client board: on"
        os.remove(path)
        return "client board: on (stale marker removed)"
    with open(path, "w", encoding="utf-8") as h:
        h.write("off\n")
    return "client board: off (%s)" % path
