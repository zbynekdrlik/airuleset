"""cli_ticket_explain — the `--explain` CLI wiring for the footer buckets (#1141).

The pure classifier and the text renderer live in `cli_ticket_state`
(`classify`, `conflicts`, `explain_lines`). This module is the I/O shell around
them. It receives the buckets `core-quals`/`slice-quals` already COUNTED (so
`--explain` can never re-derive a number), adds the footer-only contributions
(ticketless ❓ pings in U, the task-hygiene A count in I), and prints.
`tickets-status --explain` runs the same quals derivation from the session's
cwd with the role the footer resolves for it.

Known differences from the footer, each surfaced rather than hidden:
- On an empty slug the quals role filter fails CLOSED (exit 1), while the
  footer degrades to unfiltered numbers.
- An untrustworthy empty obligation set is REFUSED here, while the footer
  records 0.
- The footer's M set is keyed on the `gh repo view` slug, the quals one on the
  local remote's slug (#1083; they agree unless a fork clone differs).
`tickets-status --explain` prints the footer's cached numbers next to the
totals, so any such divergence is visible.
"""

import argparse
import os
import sys
import time

import cli_quals
import cli_ticket_state as ts

_ROLES = ("review", "infra", "quality")


def _refuse_extra(extra):
    if extra:
        print("--explain explains the footer buckets and does not combine "
              "with --extra (that query skips the partition)", file=sys.stderr)
        sys.exit(2)


def _footer_extras(cwd=None):
    """The footer contributions that are not ticket rows: each ticketless ❓
    ping adds 1 to U (#512, `statusbar.ticketless_question_pings`, the SAME
    source as the footer and `--waiting`), and the task-hygiene A count is
    added to I (#1036, `statusbar.task_hygiene_a_count`; 0 when unconfigured).
    `--count` never includes either — they are footer display terms."""
    import cli_quals_cmd
    import statusbar
    extras = [("U", 1, "ticketless question ping, no ticket to label (#512)",
               v.get("question") or v.get("block") or "")
              for v in cli_quals_cmd._waiting_ping_entries(cwd)]
    try:
        a_count = statusbar.task_hygiene_a_count()
    except Exception:  # noqa: BLE001 — a display term, never a crash
        a_count = 0
    if a_count:
        extras.append(("I", a_count, "%d unanswered client comment(s), the "
                       "task-hygiene A count (#1036)" % a_count, "-"))
    return extras


def _emit(buckets, box, merged_set, handed=None, supplement=()):
    for line in ts.explain_lines(buckets, box, merged_set, handed, supplement,
                                 extras=_footer_extras()):
        print(line)


def explain_core(extra, *, workable, merged_rows, waiting, ops_wait,
                 merged_set):
    """`core-quals --explain`: the full-authority box. It has no gk bucket,
    because it actions its own hand-offs. Receives the SAME buckets `--count`
    uses, after the role filter and the M split."""
    _refuse_extra(extra)
    _emit({"I": workable, "M": merged_rows, "U": waiting, "W": ops_wait,
           "gk": {}}, ts.Box(), merged_set)


def explain_slice(extra, root, user, role, slug, *, rows, handed, workable,
                  unhandled, waiting, ops_wait, merged_rows, merged_set):
    """`slice-quals --explain`: the reduced-authority box.
    - I = the unhandled rows `--count` counts.
    - gk = the handed-off workable rows.
    - U also carries the #948 question-map supplement the footer adds.
    The footer role-filters BEFORE it splits M and counts gk, so on a role
    window gk and M are role-filtered here too. `slice-quals` itself filters
    only I/U/W; that difference exists before #1141."""
    _refuse_extra(extra)
    import cli_quals_cmd
    extra_u = cli_quals._question_map_u_supplement(
        rows, root, cli_quals_cmd._slice_quals_runner(root))
    gk = {n: r for n, r in workable.items() if handed.get(n)}
    if role in _ROLES:
        gk = cli_quals_cmd._apply_role_filter(gk, root, role, slug=slug)
        merged_rows = cli_quals_cmd._apply_role_filter(
            merged_rows, root, role, slug=slug)
    _emit({"I": unhandled, "M": merged_rows, "U": {**waiting, **extra_u},
           "W": ops_wait, "gk": gk},
          ts.Box(own_stream=user), merged_set, handed, supplement=extra_u)


def _footer_cache_line(cwd):
    """The footer's own cached numbers for `cwd`, printed next to the explain
    totals so a divergence is visible. The raw cache fields: the footer then
    adds the A count to I and the ticketless pings to U."""
    import statusbar
    entry = statusbar._load(statusbar.cache_dir()
                            / (statusbar.cwd_key(cwd) + ".json"))
    if not isinstance(entry, dict) or "open" not in entry:
        return "# footer cache: none for this cwd"
    age = int(time.time()) - int(entry.get("ts") or 0)
    fields = ("open", "merged_unreleased", "user_waiting", "ops_wait", "gk")
    return "# footer cache (age %ds): %s" % (age, " ".join(
        "%s=%s" % (f, "-" if entry.get(f) is None else entry.get(f))
        for f in fields))


def explain_footer(cwd):
    """`tickets-status --explain`: explain the footer of the session at `cwd`.
    Runs the `core-quals`/`slice-quals` derivation for this box's authority
    FROM `cwd` (so the ping extras key on the same cwd as the footer), with the
    role the footer resolves for `cwd` (#998)."""
    import airuleset
    import cli_quals_cmd
    root = airuleset._repo_root(cwd)
    if not root:
        print("# explain: no repo at %s (the footer shows no-repo)" % cwd)
        return
    try:
        import cli_concurrency
        role = cli_concurrency.resolve_role(cwd)
    except Exception as e:  # noqa: BLE001 — the footer degrades unfiltered too
        sys.stderr.write("tickets-status: role resolve skipped (%s)\n" % e)
        role = None
    role = role if role in _ROLES else None
    full = airuleset.resolve_authority(cwd=root) == "full"
    print("# tickets-status --explain: scope=%s role=%s root=%s"
          % ("core" if full else "mine", role or "-", root))
    print(_footer_cache_line(cwd))
    prev = os.getcwd()
    os.chdir(cwd)   # the quals commands resolve the repo from the process cwd
    try:
        (cli_quals_cmd.cmd_core_quals if full else cli_quals_cmd.cmd_slice_quals)(
            argparse.Namespace(explain=True, role=role))
    finally:
        os.chdir(prev)
