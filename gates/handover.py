"""gates.handover -- #1179/#1180: a client handover on a project.task is ONE step.

THE INCIDENTS (owner, montalu1 28.9. + montalu4 29.9.2026): owner-approved
handovers were posted into a task chatter / a Discuss thread and the covered
board tasks were left in their old stages. The rule was written down
(``client-board-stages.md`` rule 3 + 5) but nothing tied the POST to the MOVE,
and the #1018 note-shape check only reads the assistant's final prose.

THE CHECK. Dispatched from the #1018 block of ``hooks/stop-check-prose-
violations.sh`` with the Stop payload on stdin, it reads THIS TURN's tool calls
(+ their ``is_error``) from ``transcript_path`` -- entries after the last genuine
human prompt; an ``isMeta`` entry (Stop-hook feedback) never starts a new turn, so
a blocked turn keeps its own evidence. Every Bash command is cut into top-level
segments by the shared quote-aware ``gates.shellcmd.split_top_level`` (heredoc
bodies re-attached to the segment that opened them), so a chained command is
judged per segment. A task is OWED a move when this turn, successfully:

  O1  posted a HANDOVER-SHAPED note (``cli_handover_hygiene.is_handover_body``:
      the rule-3 "Čo skúsiť" heading + the literal "stačí 👍") through
      ``odoo_post.py`` WITHOUT ``--handover`` -- owed: the ``--res-id`` task of a
      project.task post, ``--also-tasks``, and every ``/tasks/<id>`` URL in the
      body (so a Discuss handover naming tasks, the montalu4 shape, is caught). A
      reply / question (no rule-3 shape), an unreadable body, and an explicit
      ``--no-stage-move`` post are not handovers.
  O2  wrote an ``Acceptance-thread:`` issue comment (``gh issue comment|close``,
      ``gh pr comment``, ``gh api …/issues/<N>/comments``; inline, heredoc or a
      body file) -- owed: every ``/tasks/<id>`` URL in it.

A debt is settled by a successful ``odoo_post.py --handover`` run naming the task
(odoo-erp#8606: post + assignee + move + read-back), or by a successful
verification-stage WRITE naming it -- a ``write``/``--stage``/``move`` shape plus
the task id plus a verification target (a Verifik/Na overenie/Čaká/verif word or
the configured ``stage_ids.verifikacia`` id). A mention (grep, echo, the comment
itself, a ``stage_id`` READ) is never a move. Until the poster ships
``--handover`` the by-hand move is the way to settle O1.

Escape: a line starting ``airuleset:handover-ok <reason>`` in the final message.
Fail-OPEN on a missing/unreadable transcript (never a fabricated block).

Dry-run:
  echo '{"last_assistant_message":"x","transcript_path":"/t.jsonl","cwd":"/r"}' \
    | python3 -m gates.handover ; echo rc=$?
"""
import json
import os
import re
import shlex

import cli_odoo_ro
from cli_handover_hygiene import is_handover_body
from gates import allow, emit_block_stderr, field_of, read_payload
from gates.shellcmd import split_top_level

TAIL_BYTES = 4_000_000

BYPASS_RX = re.compile(r"^[ \t>*-]*airuleset:handover-ok[ \t]+\S[^\n]{8,}", re.MULTILINE)
_TASK_URL_RX = re.compile(r"/odoo/project/\d+/tasks/(\d+)")
_HEREDOC_RX = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
_WRAPPERS = {"env", "nice", "nohup", "stdbuf", "time", "command", "exec"}
_TID_OPT_RX = re.compile(r"^--(?:res-ids?|tasks?|task-ids?|also-tasks?|cover\S*)$")
_WRITE_RX = re.compile(r"""\bwrite\b|['"]write['"]|--stage\b|\bmove\b|/json/2/project\.task/write""",
                       re.IGNORECASE)
_VERIF_WORD_RX = re.compile(r"verif|na\s+overeni|[čc]ak[áa]", re.IGNORECASE)
_EVIDENCE_EXCLUDED_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Read"}


# --------------------------------------------------------------------------- #
# transcript → this turn's successful tool calls
# --------------------------------------------------------------------------- #
def _is_turn_boundary(entry):
    """A genuine human/loop prompt -- never Stop-hook feedback (``isMeta``), a
    compact summary, or a tool_result carrier."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


def turn_tool_calls(transcript_path, tail_bytes=TAIL_BYTES):
    """[(tool_name, input_dict)] of the current turn's tool calls whose result is
    not an error (a call with no recorded result counts), oldest first. None
    when the transcript cannot be read (the caller fails open)."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail_bytes))
            raw = fh.read()
    except (OSError, TypeError, ValueError):
        return None
    calls, failed = [], set()
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue                              # the cut first line / junk
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        if _is_turn_boundary(entry):
            calls, failed = [], set()
            continue
        for blk in (entry.get("message") or {}).get("content") or []:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_result" and blk.get("is_error"):
                failed.add(blk.get("tool_use_id"))
            elif blk.get("type") == "tool_use" and entry.get("type") == "assistant":
                inp = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                calls.append((blk.get("id"), str(blk.get("name") or ""), inp))
    return [(name, inp) for tid, name, inp in calls if tid is None or tid not in failed]


# --------------------------------------------------------------------------- #
# command → segments
# --------------------------------------------------------------------------- #
def _lift_heredocs(cmd):
    """``(command_without_heredoc_bodies, [body, …])`` -- a quote-aware scan: a
    ``<<WORD`` OUTSIDE quotes queues WORD, and the lines after the next unquoted
    newline up to WORD are lifted out (in order), so a heredoc body is never
    split into "commands" while a multi-line QUOTED argument stays intact."""
    out, bodies, pending, quote, i, n = [], [], [], None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "\\" and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        elif cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = _HEREDOC_RX.match(cmd, i)
            if m:
                pending.append(m.group(1))
                out.append(m.group(0))
                i = m.end()
                continue
        elif c == "\n" and pending:
            out.append(c)
            i += 1
            for word in pending:
                body = []
                while i < n:
                    j = cmd.find("\n", i)
                    j = n if j == -1 else j
                    line, i = cmd[i:j], j + 1
                    if line.strip() == word:
                        break
                    body.append(line)
                bodies.append("\n".join(body))
            pending = []
            continue
        out.append(c)
        i += 1
    return "".join(out), bodies


def _segments(cmd):
    """[(segment, heredoc_text)] -- the command's top-level segments (the shared
    quote-aware ``split_top_level``, line continuations joined), each carrying
    the heredoc bodies it opened."""
    clean, bodies = _lift_heredocs(re.sub(r"\\\r?\n", " ", cmd))
    out = []
    for seg in split_top_level(clean):
        if not seg.strip():
            continue
        k = len(_HEREDOC_RX.findall(seg))     # markers were lifted from unquoted text
        out.append((seg, "\n".join(bodies[:k])))
        bodies = bodies[k:]
    return out


def _tokens(seg):
    try:
        return shlex.split(seg, comments=True)
    except ValueError:
        return seg.split()


def _strip_prefix(toks):
    """Drop leading assignments and wrapper commands (env, timeout N, nice …)."""
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.match(r"^[A-Za-z_]\w*=", t):
            i += 1
        elif t in _WRAPPERS:
            i += 1
            while i < len(toks) and toks[i].startswith("-"):
                i += 1
        elif t == "timeout":
            i += 1
            while i < len(toks) and (toks[i].startswith("-") or re.match(r"^\d", toks[i])):
                i += 1
        else:
            break
    return toks[i:]


def _poster_args(toks):
    """The argument tokens of an ``odoo_post.py`` INVOCATION, or None. Only a
    command-position call counts (``python3 [opts] …/odoo_post.py``, ``python3
    -m …odoo_post``, a direct ``…/odoo_post.py``) -- never ``grep``/``cat`` of it."""
    toks = _strip_prefix(toks)
    if toks[:2] == ["uv", "run"]:
        toks = toks[2:]
    if toks and re.match(r"^(?:\S*/)?python[\d.]*$", toks[0]):
        toks = toks[1:]
        while toks and toks[0].startswith("-") and toks[0] != "-m":
            toks = toks[1:]
        if toks[:1] == ["-m"] and len(toks) > 1 and toks[1].split(".")[-1] == "odoo_post":
            return toks[2:]
    if toks and toks[0].endswith("odoo_post.py"):
        return toks[1:]
    return None


def _inner_script(toks):
    """``bash -c "<script>"`` / ``sh -c`` → the script string, else None."""
    toks = _strip_prefix(toks)
    if toks and os.path.basename(toks[0]) in ("bash", "sh", "zsh") and "-c" in toks:
        j = toks.index("-c")
        return toks[j + 1] if j + 1 < len(toks) else None
    return None


def _is_gh_comment(toks):
    toks = _strip_prefix(toks)
    if not toks or toks[0] != "gh":
        return False
    words = [t for t in toks[1:4] if not t.startswith("-")]
    return (words[:2] in (["issue", "comment"], ["issue", "close"], ["pr", "comment"])
            or (words[:1] == ["api"] and any(
                re.search(r"issues/\d+/comments", t) for t in toks)))


def _read_file(path, cwd):
    path = os.path.expanduser(path.strip("'\"").lstrip("@"))
    if path in ("", "-"):
        return ""
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(512_000)
    except OSError:
        return ""


def _files_text(toks, cwd):
    """Contents of every body file a command names: ``--body-file F``/``-F F``,
    ``body=@F`` field values, and ``$(cat F)`` / ``$(< F)`` substitutions."""
    out = []
    for i, t in enumerate(toks):
        if t in ("-F", "--body-file", "--field", "-f") and i + 1 < len(toks):
            val = toks[i + 1]
            out.append(_read_file(val.split("=", 1)[1] if "=@" in val else val, cwd)
                       if ("=@" in val or t != "-f") else "")
        elif t.startswith("--body-file="):
            out.append(_read_file(t.split("=", 1)[1], cwd))
        for m in re.finditer(r"\$\(\s*(?:cat\s+|<\s*)([^)\s<]+)\s*\)", t):
            out.append(_read_file(m.group(1), cwd))
    return "\n".join(out)


def _tids(args, text, res_id=True):
    """Task ids a poster call names: its task-id options (``--res-id`` only when
    ``res_id`` -- on a Discuss post it is a CHANNEL id) + ``/tasks/<id>`` URLs."""
    out, collecting = set(), False
    for tok in args:
        if tok.startswith("-"):
            name, _, inline = tok.partition("=")
            collecting = bool(_TID_OPT_RX.match(name)) and (
                res_id or not name.startswith("--res-id"))
            if collecting and inline:
                out.update(x for x in re.split(r"[,\s]+", inline) if x.isdigit())
            continue
        if collecting:
            out.update(x for x in re.split(r"[,\s]+", tok) if x.isdigit())
    out.update(_TASK_URL_RX.findall(text))
    return out


def _model(args):
    for i, tok in enumerate(args):
        if tok == "--model" and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith("--model="):
            return tok.split("=", 1)[1]
    return ""


class _Turn:
    """The turn's debts (task id → why) and settlements."""

    def __init__(self, cwd, verif_id):
        self.cwd, self.verif_id = cwd, verif_id
        self.owed, self.settled = {}, set()
        self.moves = []

    def post(self, args, seg, doc):
        flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
        if flags & {"--help", "-h", "--dry-run"}:
            return                                # nothing was posted or moved
        body = " ".join(args) + "\n" + doc + "\n" + _files_text(args, self.cwd)
        on_task = _model(args) == "project.task"
        if "--handover" in flags:
            self.settled |= _tids(args, body, res_id=on_task)
            return
        if "--no-stage-move" in flags or not is_handover_body(body):
            return
        ids = _tids(args, body, res_id=on_task)
        if on_task and not _arg(args, "--res-id"):
            ids.add("?")
        for t in ids:
            self.owed.setdefault(t, "handover note posted without --handover")

    def comment(self, text):
        if "Acceptance-thread:" in text:
            for t in _TASK_URL_RX.findall(text):
                self.owed.setdefault(t, "Acceptance-thread: names it")

    def evidence(self, text):
        if _WRITE_RX.search(text) and (
                _VERIF_WORD_RX.search(text) or (
                    self.verif_id is not None
                    and re.search(r"(?<!\d)%d(?!\d)" % self.verif_id, text))):
            self.moves.append(text)

    def moved(self, tid):
        rx = re.compile(r"(?<!\d)%s(?!\d)" % re.escape(tid))
        return tid in self.settled or any(rx.search(m) for m in self.moves)


def _arg(args, name):
    for i, tok in enumerate(args):
        if tok == name and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith(name + "="):
            return tok.split("=", 1)[1]
    return ""


def _scan_command(turn, cmd, depth=0):
    for seg, doc in _segments(cmd):
        toks = _tokens(seg)
        inner = _inner_script(toks)
        if inner is not None and depth < 2:
            _scan_command(turn, inner, depth + 1)
            continue
        args = _poster_args(toks)
        if args is not None:
            turn.post(args, seg, doc)
            continue                              # a post is judged by post() only
        elif _is_gh_comment(toks):
            turn.comment(seg + "\n" + doc + "\n" + _files_text(toks, turn.cwd))
            continue                              # a comment is never a move
        turn.evidence(seg + "\n" + doc)


def _verif_stage_id():
    cfg = cli_odoo_ro.load_config()
    vid = ((cfg or {}).get("stage_ids") or {}).get("verifikacia")
    return vid if isinstance(vid, int) and not isinstance(vid, bool) else None


def evaluate(transcript_path, message, cwd, verif_id=None):
    """``("block", reason)`` or ``("allow", "")`` for this turn."""
    if BYPASS_RX.search(message or ""):
        return "allow", ""
    calls = turn_tool_calls(transcript_path)
    if not calls:
        return "allow", ""
    turn = _Turn(cwd or os.getcwd(), verif_id if verif_id is not None else _verif_stage_id())
    for name, inp in calls:
        if name == "Bash":
            _scan_command(turn, str(inp.get("command") or ""))
        elif name not in _EVIDENCE_EXCLUDED_TOOLS:
            turn.evidence(json.dumps(inp, ensure_ascii=False))   # an MCP write
    open_debts = sorted((t, why) for t, why in turn.owed.items() if not turn.moved(t))
    if not open_debts:
        return "allow", ""
    listing = "; ".join("#%s (%s)" % (t, why) for t, why in open_debts)
    return "block", (
        "VIOLATION (#1179/#1180): a client handover is ONE step -- the covered "
        "project.task(s) move to the profile's verification stage with the rule-5 "
        "assignee in the SAME turn (client-board-stages.md rule 3). Not moved this "
        "turn: %s. The note is already out -- do NOT re-post it. Post handovers with "
        "`odoo_post.py --handover` (odoo-erp#8606: post + assignee + move + "
        "read-back); where the poster has no --handover yet, move each task to the "
        "verification stage and set the assignee by hand now, then stop again. If "
        "the move genuinely happened earlier / is not wanted, end with a line "
        "`airuleset:handover-ok <where/why>`." % listing)


def run(payload):
    verdict, reason = evaluate(field_of(payload, "transcript_path", ""),
                               field_of(payload, "last_assistant_message", ""),
                               field_of(payload, "cwd", ""))
    if verdict == "block":
        emit_block_stderr(reason)


def main():
    run(read_payload())
    allow()


if __name__ == "__main__":
    main()
