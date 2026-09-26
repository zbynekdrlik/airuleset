"""#1157 slice 3 — a MACHINE nudge is ONE short line pointing at a file.

The recurring class this removes: the watchdog typed a long, multi-line nudge
paragraph (the ~800-char partition-audit batch) into Claude Code's input box
and verified it by reading the rendered screen back. That box wraps, scrolls,
hides rows in a short pane and changes with CC versions, so every threshold
around it only moved the edge (#746, #1022, #1092, #1023/#1104, slices 1-2).

Now `send_verified` (via `send_outcome.machine_pointer`) writes the nudge's
FULL text here first, then types only
`nudge: [<kind>] <headline> — celý text: ~/.claude/nudges/<file>` — at most
`min(LINE_MAX_CELLS, pane_width - ROW_MARGIN)` cells, so it never wraps and its
read-back is one exact row. The session reads the file with its normal tools.

This module is a stdlib-only LEAF (no `import watchdog`), so any module can
import it without an import cycle. It owns:

* `write` — the atomic 0600 write (temp file in the same dir, fsync, then a
  no-overwrite `link` to the final name), pruning files older than `TTL_S` on
  every write (the `_draft_rescue_prune` housekeeping pattern: no new job, and
  the directory stays bounded because it only grows on a write);
* `pointer_line` — the one-line pointer, cut to the pane's row budget;
* `is_pointer_line` / `expand` — the recogniser the own-content checks and the
  transcript readers use to tell our pointer apart and to read back what it
  points at.

The directory is `$AIRULESET_NUDGE_FILE_DIR` (the test/push-gate seam) or
`~/.claude/nudges` (per account: the watchdog runs as each box user)."""
import logging
import os
import re
import secrets
import tempfile
import time
import unicodedata

_log = logging.getLogger(__name__)

DIR_ENV = "AIRULESET_NUDGE_FILE_DIR"
TTL_S = 7 * 86400                 # a pointer older than a week is never read again
TMP_TTL_S = 3600                  # a temp file left by a crash mid-write
LINE_MAX_CELLS = 100              # the owner's cap for a typed machine line
# CC draws the input row two columns in (`❯` + NBSP) and keeps a two-column
# right margin (measured on CC 2.1.281, tests/_cc_box_pane.py): a row holds
# `pane_width - 4` cells. Two more cells of slack absorb a CC layout change.
ROW_MARGIN = 6
FALLBACK_WIDTH = 80               # layout width when tmux gives no pane width
MIN_HEADLINE_CELLS = 8            # below this a headline says nothing: drop it
PREFIX = "nudge:"
LABEL = "— celý text:"
_TS_FMT = "%y%m%d%H%M%S"          # UTC, 12 digits: keeps the path short

NAME_RX = re.compile(r"^([a-z0-9][a-z0-9-]*)-(\d{12})-([0-9a-f]{4})\.md$")
_POINTER_RX = re.compile(r"^nudge: (?:\[([a-z0-9-]+)\] )?(?:(.+?) )?"
                         r"— celý text: (\S+)$")
# A leading machine tag of the nudge text (`stuck-check: `, `report-owed: `,
# `bounce-backstop [odoo-erp]: `): the bracket kind already says it.
_TAG_RX = re.compile(r"^[a-z][a-z-]*(?: [a-z-]+)?(?: \[[^\]]*\])?: ")
_BATCH_HEAD_RX = re.compile(r"^nudge:\s*(?:\[[a-z0-9-]+\]\s*)*")
_SENTENCE_END_RX = re.compile(r"[.!?](?=\s|$)")


def nudge_dir():
    """The per-account nudge-file directory (absolute)."""
    d = os.environ.get(DIR_ENV)
    return os.path.abspath(d) if d else os.path.join(
        os.path.expanduser("~"), ".claude", "nudges")


def cells(s):
    """Terminal cells `s` takes: a wide (W/F) char is 2, a combining char 0."""
    n = 0
    for ch in s or "":
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def row_budget(width):
    """The cell budget of ONE typed line for a pane `width` columns wide."""
    return min(LINE_MAX_CELLS, (width or FALLBACK_WIDTH) - ROW_MARGIN)


def safe_kind(kind):
    """`kind` reduced to `[a-z0-9-]` (it names the file and the bracket)."""
    k = re.sub(r"[^a-z0-9-]+", "-", str(kind or "").lower()).strip("-")
    return k or "nudge"


def display_path(path):
    """`path` as the typed line shows it: `~/…` when under the home dir."""
    home = os.path.expanduser("~").rstrip("/")
    if home and path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


def headline(text):
    """The nudge's first sentence, whitespace-normalised, with the batch head
    (`nudge: [cat] `) and one leading machine tag (`stuck-check: `) removed:
    the bracket kind already names them."""
    t = " ".join((text or "").split())
    t = _BATCH_HEAD_RX.sub("", t)
    t = _TAG_RX.sub("", t, count=1)
    m = _SENTENCE_END_RX.search(t)
    if m:
        t = t[:m.end()]
    return t.split(LABEL)[0].strip()


def _cut(head, room):
    """`head` cut on a word boundary to at most `room` cells (a cut head ends
    with `…`), or "" when not even its first word fits."""
    if cells(head) <= room:
        return head
    out = ""
    for word in head.split(" "):
        cand = (out + " " + word) if out else word
        if cells(cand) > room - 1:
            break
        out = cand
    out = out.rstrip(" ,;:—-")
    return out + "…" if out else ""


def pointer_line(kind, text, shown_path, width):
    """The ONE typed line for a machine nudge, at most `row_budget(width)`
    cells whenever any form fits. Ladder: the headline form when at least
    `MIN_HEADLINE_CELLS` are left for the headline, else the bracket form
    without a headline, else the bare form. A pane too narrow even for the
    bare form gets the bare form anyway (the caller then verifies it as a
    wrapped box and says so)."""
    budget = row_budget(width)
    k = safe_kind(kind)
    tail = "%s %s" % (LABEL, shown_path)
    bracket = "%s [%s] %s" % (PREFIX, k, tail)
    room = budget - cells(bracket) - 1
    if room >= MIN_HEADLINE_CELLS:
        head = _cut(headline(text), room)
        if head:
            return "%s [%s] %s %s" % (PREFIX, k, head, tail)
    if cells(bracket) <= budget:
        return bracket
    return "%s %s" % (PREFIX, tail)


def _pointer_target(text):
    """The absolute file a well-formed pointer line names, or None. The file
    must sit directly in `nudge_dir()`, carry our name shape, and its kind
    must equal the bracket kind when the line has one."""
    m = _POINTER_RX.match(" ".join((text or "").split()))
    if not m:
        return None
    path = os.path.expanduser(m.group(3))
    name = os.path.basename(path)
    nm = NAME_RX.match(name)
    if not nm or os.path.dirname(os.path.abspath(path)) != nudge_dir():
        return None
    if m.group(1) is not None and m.group(1) != nm.group(1):
        return None
    return os.path.join(nudge_dir(), name)


def is_pointer_line(text, require_file=False):
    """True when `text` (whitespace-normalised) is EXACTLY one of our pointer
    lines. A human never types this shape: it ends in a path to a file in our
    own nudge dir, so words appended after it break the match. With
    `require_file` the file must also exist as a regular file (not a link)."""
    path = _pointer_target(text)
    if path is None:
        return False
    return (not require_file) or (os.path.isfile(path)
                                  and not os.path.islink(path))


def expand(text):
    """The file content a pointer line names, else `text` unchanged. Transcript
    readers use it to find a nudge's words after only the pointer was typed.
    Never raises."""
    path = _pointer_target(text)
    if path is None or os.path.islink(path):
        return text
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        _log.info("nudge-file: pointer target unreadable %s (%s)", path, e)
        return text


def prune(now=None, dir_path=None, ttl_s=TTL_S):
    """Remove our nudge files older than `ttl_s` (and crash-left temp files
    older than `TMP_TTL_S`). Only our own name shapes are candidates, a
    matching-named symlink is removed on sight (we only create regular files).
    Best-effort, never raises; returns the removed names."""
    now = time.time() if now is None else now
    d = dir_path or nudge_dir()
    removed = []
    try:
        names = os.listdir(d)
    except OSError:
        return removed                        # no dir yet: nothing to prune
    for name in names:
        if NAME_RX.match(name):
            limit = ttl_s
        elif name.startswith(".tmp-") and name.endswith(".md"):
            limit = TMP_TTL_S
        else:
            continue
        p = os.path.join(d, name)
        try:
            if os.path.islink(p) or now - os.lstat(p).st_mtime > limit:
                os.unlink(p)
                removed.append(name)
        except OSError as e:
            _log.info("nudge-file: prune skipped %s (%s)", p, e)
    if removed:
        _log.info("nudge-file: pruned %d old file(s) in %s", len(removed), d)
    return removed


def write(kind, text, now=None):
    """Write `text` byte-identically (UTF-8) to a NEW 0600 file
    `<kind>-<yymmddHHMMSS UTC>-<4 hex>.md` in `nudge_dir()` (created 0700),
    atomically: a temp file in the same dir is fsynced, then hard-linked to the
    final name (a link never overwrites, so a name collision just retries with
    new random hex). Prunes old files first. Returns the absolute path; raises
    OSError when the file cannot be written (the caller then types nothing)."""
    now = time.time() if now is None else now
    d = nudge_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    prune(now, d)
    data = (text or "").encode("utf-8")
    ts = time.strftime(_TS_FMT, time.gmtime(now))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        for _ in range(16):
            path = os.path.join(d, "%s-%s-%s.md" % (
                safe_kind(kind), ts, secrets.token_hex(2)))
            try:
                os.link(tmp, path)
            except FileExistsError:
                continue                      # name taken: new random hex
            return path
        raise OSError("no free nudge file name in %s" % d)
    finally:
        try:
            os.unlink(tmp)
        except OSError as e:                  # the next prune reaps it
            _log.warning("nudge-file: temp file left %s (%s)", tmp, e)
