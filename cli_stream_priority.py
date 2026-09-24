"""cli_stream_priority.py — the owner-controlled stream-priority registry (#1138).

The owner marks a stream FAMILY `high` or `normal` (default `normal`) once;
montalu (montalu1…N) is `high`. ONE data file, `stream-priority.json` in the
airuleset repo root (`{"montalu": "high"}`), is the single source of truth for
two consumers:

- in-repo: `cli_quals_cmd._row_sort_key` (and its dep-wait mirror in
  `cli_work_class`) orders a high-priority stream's rows FIRST within the same
  label rank, then oldest-first — so gk's `core-quals --list` / `--role review`
  picks up montalu hand-offs before older low-value ones;
- project release cuts (the odoo-erp cut script) read `airuleset.py
  stream-priority --list` (JSON `{family: priority}`) through the managed
  `~/devel/airuleset` checkout and may hold a cut while a high stream's
  hand-off is in flight.

FAMILY = the `stream:` label owner (`cli_quals._stream_owner_of`, which already
folds the #537 rename aliases, so a legacy `stream:montalu` resolves too) with
its trailing digits stripped: `montalu3` → `montalu`, `miva1` → `miva`. The
known families are that same strip over the reduced-authority
`AUTHORITY_BY_USER` keys — derived, never a parallel table. A row carrying
several `stream:` labels is high when ANY of them is.

Read side NEVER crashes: a missing file is "everything normal"; an
unparseable / wrongly-shaped file is the same plus ONE stderr warning per file
state; an entry with an unknown priority value is dropped with a warning, and
a numbered key (`montalu3`) is folded into its family. Write side (`set`)
follows the #946 registry-writer pattern (`cli_onboard`): a git-TRACKED
registry is written only on the controller and auto-committed there (it goes
live on gk and the targets with the next `airuleset.py push`); an untracked
copy (a temp file) is writable anywhere; a corrupt file, or one with entries
`set` could not carry over, is never rewritten.

Stdlib-only. `airuleset` / `cli_onboard` / `watchdog` / `cli_playwright_mcp`
are imported lazily inside the functions (the leaf rule — never at module
level), so the facade patches (`airuleset.AUTHORITY_BY_USER`) are honoured.
"""
import json
import re
import sys
from pathlib import Path

PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"
PRIORITIES = (PRIORITY_HIGH, PRIORITY_NORMAL)
STREAM_PRIORITY_FILENAME = "stream-priority.json"
# The seam: tests point this at a temp file. The shipped registry lives next
# to this module in the airuleset checkout.
STREAM_PRIORITY_PATH = Path(__file__).resolve().parent / STREAM_PRIORITY_FILENAME

_USAGE = ("usage: airuleset.py stream-priority [--list] | "
          "stream-priority set <stream> high|normal")

# One slot keyed by the file's identity + state (path, inode, ctime, mtime,
# size): re-read when the file changes, so a long-lived process sees a `set`
# at once (an atomic replace always changes the inode). `_warned` holds the
# states already warned about — bounded by the number of distinct bad edits.
_cache = {"key": None, "value": {}}
_warned = set()


def stream_family(stream):
    """`montalu3` → `montalu`; a name with no trailing digits is its own
    family; "" stays ""."""
    return re.sub(r"\d+$", "", stream or "")


def known_families():
    """The families of every reduced-authority stream in AUTHORITY_BY_USER
    (the same non-`full` filter `_stream_owner_of` applies)."""
    import airuleset
    return {stream_family(user)
            for user, profile in airuleset.AUTHORITY_BY_USER.items()
            if profile != "full" and stream_family(user)}


def _parse(text):
    """`(map, error, dropped)`. `map` is `{family: priority}` — a numbered key
    folds into its family (high wins if both appear); `dropped` lists the
    entries that could not be read (bad value, key with no family); `error`
    is a reason string when the file itself is unusable."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return {}, "not valid JSON (%s)" % e, []
    if not isinstance(data, dict):
        return {}, "top level is %s, not an object" % type(data).__name__, []
    out, dropped = {}, []
    for key, value in data.items():
        family = stream_family(key)
        if not family or value not in PRIORITIES:
            dropped.append("%s=%s" % (key, json.dumps(value)))
        elif out.get(family) != PRIORITY_HIGH:
            out[family] = value
    return out, None, dropped


def _read(path):
    """`(map, error, dropped, present)` for `path`, uncached."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None, [], False
    except (OSError, ValueError) as e:
        return {}, "unreadable (%s)" % e, [], True
    data, err, dropped = _parse(text)
    return data, err, dropped, True


def load_priorities(path=None):
    """`{family: priority}` from the registry, or {} when missing / bad.
    Cached per file state; a bad file or bad entry warns on stderr once per
    state."""
    path = Path(path or STREAM_PRIORITY_PATH)
    try:
        st = path.stat()
        key = (str(path), st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), None)
    if _cache["key"] == key:
        return _cache["value"]
    data, err, dropped, _present = _read(path)
    if (err or dropped) and key not in _warned:
        _warned.add(key)
        if err:
            print("stream-priority: ignoring %s — %s; every stream is treated "
                  "as normal" % (path, err), file=sys.stderr)
        else:
            print("stream-priority: ignoring unreadable entries in %s: %s "
                  "(priority must be high|normal)" % (path, ", ".join(dropped)),
                  file=sys.stderr)
    _cache["key"], _cache["value"] = key, data
    return data


def row_priority_rank(row):
    """0 when ANY of the row's `stream:` labels belongs to a `high` family,
    else 1 — the secondary sort key after `_row_label_rank`. A row with no
    readable labels, or no stream owner, is 1 (never spuriously promoted)."""
    priorities = load_priorities()
    if PRIORITY_HIGH not in priorities.values() or not isinstance(row, dict):
        return 1
    labels = row.get("labels")
    if not isinstance(labels, list):
        return 1
    import airuleset
    for lb in labels:
        name = lb.get("name") if isinstance(lb, dict) else None
        if not (isinstance(name, str) and name.startswith("stream:")):
            continue
        family = stream_family(airuleset._stream_owner_of([lb]))
        if family and priorities.get(family) == PRIORITY_HIGH:
            return 0
    return 1


def _box_class():
    """This box's class marker (`controller` / `workstation` / …)."""
    from watchdog.reaper import default_box_class
    return default_box_class()


def _set(stream, priority):
    """Validate + write one family's priority. Returns the process rc."""
    import cli_onboard
    from cli_playwright_mcp import _atomic_write_text
    family = stream_family(stream)
    known = known_families()
    if family not in known:
        print("stream-priority: unknown stream %r — known families: %s"
              % (stream, ", ".join(sorted(known))), file=sys.stderr)
        return 2
    if priority not in PRIORITIES:
        print("stream-priority: priority must be high|normal, got %r"
              % priority, file=sys.stderr)
        return 2
    path = Path(STREAM_PRIORITY_PATH)
    data, err, dropped, _present = _read(path)
    if err or dropped:
        print("stream-priority: %s %s — refusing to rewrite it (fix or remove "
              "it first)" % (path, err or "has unreadable entries: "
                             + ", ".join(dropped)), file=sys.stderr)
        return 1
    new = dict(data)
    if priority == PRIORITY_HIGH:
        new[family] = PRIORITY_HIGH
    else:
        new.pop(family, None)      # normal is the default: keep the file minimal
    if new == data:                # a missing file reads {} -> a normal set is a no-op
        print("stream-priority: %s is already %s" % (family, priority))
        return 0
    tracked = cli_onboard._registry_is_tracked(path)
    if tracked and not cli_onboard._is_controller_box(_box_class):
        print("stream-priority: this box is NOT the controller — the tracked "
              "registry is written only on the controller (#946). Run it "
              "there, or relay via gk-request: set %s %s" % (family, priority),
              file=sys.stderr)
        return 1
    try:
        _atomic_write_text(path, json.dumps(new, indent=2, sort_keys=True) + "\n")
    except OSError as e:
        print("stream-priority: could not write %s — %s" % (path, e),
              file=sys.stderr)
        return 1
    if not tracked:
        print("stream-priority: %s = %s (%s)" % (family, priority, path))
        return 0
    warn = cli_onboard._auto_commit_registry(
        path, filename=path.name, source="stream-priority set")
    if warn:
        print("stream-priority: %s = %s written but NOT committed — %s"
              % (family, priority, warn), file=sys.stderr)
        return 1
    print("stream-priority: %s = %s — committed on the controller; live on gk "
          "and the targets after `airuleset.py push`" % (family, priority))
    return 0


def register_parser(sub):
    """The `stream-priority` argparse subparser (kept here so main() grows by
    one call line, not a parser block)."""
    p = sub.add_parser(
        "stream-priority",
        help="#1138: stream priority registry — [--list] prints the "
             "{family: high|normal} JSON; set <stream> high|normal")
    p.add_argument("sp_args", nargs="*", help="set <stream> high|normal")
    p.add_argument("--list", action="store_true",
                   help="print the JSON map (the default)")
    return p


def cmd_stream_priority(args):
    """`stream-priority [--list]` prints the JSON map; `stream-priority set
    <stream> high|normal` writes one family (controller-only when tracked)."""
    sp_args = list(getattr(args, "sp_args", None) or [])
    if not sp_args:
        priorities = load_priorities()
        unknown = sorted(set(priorities) - known_families())
        if unknown:
            print("stream-priority: no known stream belongs to %s — a typo? "
                  "known families: %s" % (", ".join(unknown),
                                          ", ".join(sorted(known_families()))),
                  file=sys.stderr)
        print(json.dumps(priorities, sort_keys=True))
        return 0
    if getattr(args, "list", False) or sp_args[0] != "set" or len(sp_args) != 3:
        print(_USAGE, file=sys.stderr)
        return 2
    return _set(sp_args[1], sp_args[2])
