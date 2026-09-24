"""cli_ticket_explain — the `--explain` CLI wiring for the footer buckets (#1141).

The pure classifier and the text renderer live in `cli_ticket_state`
(`classify`, `conflicts`, `explain_lines`, `conflict_lines`). This module is
the I/O shell around them; `--conflicts` prints only the conflict lines. It receives the buckets `core-quals`/`slice-quals` already COUNTED (so
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
- The footer reads the P/C facts fresh; the quals commands read the facts
  cache the footer refresher wrote (at most 10 min old, else unknown: #1141).
`tickets-status --explain` prints the footer's cached numbers next to the
totals, so any such divergence is visible.
"""

import argparse
import os
import sys
import time

import cli_quals
import cli_ticket_route
import cli_ticket_state as ts

_ROLES = cli_ticket_route.ROLES

# The session cwd `tickets-status --explain` explains. It is set only for the
# duration of `explain_footer`, so the ping extras key on the RAW cwd string the
# footer keys on (`statusbar.ticketless_question_pings(cwd)` compares strings
# without realpath). `os.getcwd()` after the chdir would return the resolved
# path. None = the process cwd (a direct `core-quals`/`slice-quals --explain`,
# the same cwd `--waiting` uses).
_footer_cwd = None


def _refuse_extra(extra, flag="--explain"):
    if extra:
        print("%s explains the footer buckets and does not combine "
              "with --extra (that query skips the partition)" % flag,
              file=sys.stderr)
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


def _emit(buckets, box, facts=None, supplement=(), only_conflicts=False):
    """Print the `--explain` text, or with `only_conflicts` (`--conflicts`,
    #1141 slice 4) just its `conflict:` lines, for the gk review loop."""
    if only_conflicts:
        lines = ts.conflict_lines(buckets, box, facts or None, supplement)
    else:
        lines = ts.explain_lines(buckets, box, facts or None, supplement,
                                 extras=_footer_extras(_footer_cwd))
    for line in lines:
        print(line)


def _flag(only_conflicts):
    return "--conflicts" if only_conflicts else "--explain"


def explain_core(extra, buckets, facts, only_conflicts=False):
    """`core-quals --explain`: the full-authority box (no gk bucket: it
    actions its own hand-offs). `buckets`/`facts` are the ONE route's result
    (`cli_ticket_route.quals`), the SAME `--count` uses — including the HIDDEN
    rows (a foreign stream's owner question, #1141 slice 2), listed with their
    reason and counted in no bucket. `only_conflicts`: `--conflicts`."""
    _refuse_extra(extra, _flag(only_conflicts))
    _emit(buckets, ts.Box(), facts, only_conflicts=only_conflicts)


def explain_slice(extra, root, rows, buckets, facts, box=None,
                  only_conflicts=False):
    """`slice-quals --explain`: the reduced-authority box `box` (default: this
    account's stream, as `slice-quals` resolves it). `buckets`/`facts` are the
    ONE route's result (I = the unhandled rows `--count` counts, gk = the
    handed-off ones; every bucket role-filtered like the footer, since the
    role filter is a pre-filter, #1141 slice 3). U also carries the #948
    question-map supplement the footer adds. `only_conflicts`: `--conflicts`."""
    _refuse_extra(extra, _flag(only_conflicts))
    import airuleset
    import cli_quals_cmd
    box = box or ts.Box(own_stream=airuleset._current_user())
    extra_u = cli_quals._question_map_u_supplement(
        rows, root, cli_quals_cmd._slice_quals_runner(root))
    _emit({**buckets, "U": {**buckets["U"], **extra_u}}, box, facts,
          supplement=extra_u, only_conflicts=only_conflicts)


def _footer_cache_line(cwd):
    """The footer's own cached numbers for `cwd`, printed next to the explain
    totals so a divergence is visible. The raw cache fields: the footer then
    adds the A count to I and the ticketless pings to U."""
    import statusbar
    entry = statusbar._load(statusbar.cache_dir()
                            / (statusbar.cwd_key(cwd) + ".json"))
    if not isinstance(entry, dict) or "open" not in entry:
        return "# footer cache: none for this cwd"
    stamp = entry.get("ts")
    age = ("%ds" % (int(time.time()) - int(stamp))
           if isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
           else "?")
    fields = ("open", "pipeline", "merged_unreleased", "done", "user_waiting",
              "ops_wait", "gk")
    return "# footer cache (age %s): %s" % (age, " ".join(
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
    global _footer_cwd
    prev = os.getcwd()
    os.chdir(cwd)   # the quals commands resolve the repo from the process cwd
    _footer_cwd = cwd
    try:
        (cli_quals_cmd.cmd_core_quals if full else cli_quals_cmd.cmd_slice_quals)(
            argparse.Namespace(explain=True, role=role))
    finally:
        _footer_cwd = None
        os.chdir(prev)
