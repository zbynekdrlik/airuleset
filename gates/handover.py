"""gates.handover -- #1179/#1180: a client handover on a project.task is ONE step.

THE INCIDENTS (owner, montalu1 28.9. + montalu4 29.9.2026): owner-approved
handovers were posted into a task chatter / a Discuss thread and the covered
board tasks were left in their old stages. The rule was written down
(``client-board-stages.md`` rule 3 + 5) but nothing tied the POST to the MOVE,
and the #1018 note-shape check only reads the assistant's final prose. Since
odoo-erp#8606 (develop, 29.9.) the repo poster itself refuses a project.task post
without ``--handover`` / ``--no-stage-move``; this gate is the session-side
backstop for what the poster cannot see (a Discuss handover naming tasks, an
``Acceptance-thread:`` record, an older checkout, the Python API).

THE CHECK. Dispatched from the #1018 block of ``hooks/stop-check-prose-
violations.sh`` with the Stop payload on stdin, it reads THIS TURN's tool calls
and their results from ``transcript_path`` (entries after the last genuine human
prompt; an ``isMeta`` Stop-hook feedback entry or a ``<task-notification>`` never
starts a new turn). Bash commands are parsed with the gate family's shared shell
helpers (``gates.shellcmd.split_top_level`` + ``gates.selfservice``'s heredoc
capture, prefix strip, tokenizer, ``cd`` tracking and gh body resolver -- the
same primitives ``gates.labeledit`` reuses), per top-level segment.

A task is OWED a move when this turn:
  O1  posted a HANDOVER-SHAPED note (``cli_handover_hygiene.is_handover_body``:
      the rule-3 "Čo skúsiť" heading + the literal "stačí 👍") WITHOUT the
      poster's handover mode -- through ``odoo_post.py`` (any wrapper, ``-m``,
      ``bash -c``), the ``odoo_post.post()`` Python API, or ``odoo-task-sync.py
      post-message``. Owed: the ``--res-id`` task of a project.task post,
      ``--also-tasks``, and every ``/tasks/<id>`` URL in the body (a Discuss
      handover naming tasks is the montalu4 shape). A reply / question (no
      rule-3 shape), an unresolvable body and an explicit ``--no-stage-move`` post
      are not handovers.
  O2  wrote an ``Acceptance-thread:`` LINE (line start) in an issue comment
      (``gh issue comment|close``, ``gh pr comment``, ``gh api …/comments``;
      inline, heredoc or body file; never on the airuleset repo). Owed: the
      ``/tasks/<id>`` URLs on those lines.
A post / comment inside a FAILED Bash call still counts when its own success
output is in the result (``OK: mail.message N posted``, an ``issuecomment-`` URL).

A debt is settled by a successful ``--handover`` run naming the task (or the
poster's ``OK: handover task N`` / ``OK: also-task N`` read-back), or by a
successful verification-stage WRITE naming it: a task-CLI ``move``/``--stage``
segment, a script (python/curl heredoc or ``-c``) that writes ``stage_id`` to
the verification stage, or an ``mcp__*`` tool call doing the same -- each with
the task id and a verification target (a Verifik/Na overenie/Čaká/verif word, or
the configured ``stage_ids.verifikacia`` id). A mention (echo, grep, a comment,
an Agent prompt, a TodoWrite, a ``stage_id`` READ) is never a move.

Escape: a line ``airuleset:handover-ok <reason>`` (outside code fences) in the
final message; logged to ``audits/handover-bypasses.log``. Fail-OPEN on a
missing/unreadable transcript (never a fabricated block).

Dry-run:
  echo '{"last_assistant_message":"x","transcript_path":"/t.jsonl","cwd":"/r"}' \
    | python3 -m gates.handover ; echo rc=$?
"""
import json
import os
import re

import cli_odoo_ro
from cli_handover_hygiene import is_handover_body
from gates import allow, audit, emit_block_stderr, field_of, read_payload
from gates.selfservice import (
    _apply_cd, _capture_heredocs, _flag_value, _resolve_body, _strip_prefix, _tokens_of,
)
from gates.shellcmd import split_top_level

TAIL_BYTES = 4_000_000
AUDIT_LOG = "handover-bypasses.log"

_FENCE_RX = re.compile(r"```.*?```", re.DOTALL)
BYPASS_RX = re.compile(r"^[ \t*-]*airuleset:handover-ok[ \t]+(\S[^\n]{8,})", re.MULTILINE)
_TASK_URL_RX = re.compile(r"/odoo/project/\d+/tasks/(\d+)")
_ACC_LINE_RX = re.compile(r"(?m)^[ \t]*[-*]?[ \t]*Acceptance-thread:(.*)$")
_HEREDOC_DELIM_RX = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
_ASSIGN_SEG_RX = re.compile(r"^\s*([A-Za-z_]\w*)=(\"?\$\(.*\)\"?|`.*`)\s*$", re.DOTALL)
_SUBST_FILE_RX = re.compile(r"\$\(\s*(?:cat\s+|<\s*)([^)\s<]+)\s*\)|`\s*cat\s+([^`\s]+)\s*`")
_VAR_RX = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
_OPEN_RX = re.compile(r"open\(\s*['\"]([^'\"]+)['\"]")
_PY_POST_RX = re.compile(
    r"\bpost\(\s*(?:model\s*=\s*)?['\"]([\w.]+)['\"]\s*,\s*(?:res_id\s*=\s*)?(\d+)")
_TID_OPT_RX = re.compile(r"^--(?:res-ids?|tasks?|task-ids?|also-tasks?)$")
_WRAPPERS = {"timeout", "nice", "nohup", "stdbuf", "time", "command", "exec", "env"}
_PY_RX = re.compile(r"^python[\d.]*$")
_MOVE_SUBCMDS = {"move", "move-stage", "set-stage", "stage"}
_STAGE_WRITE_RX = re.compile(r"write|update", re.IGNORECASE)
_VERIF_WORD = r"(?:[\w.]*verif\w*|Verifik\w*|Na\s+overeni\w*|[ČC]ak[áa])"
_POST_OK_RX = re.compile(r"OK: (?:mail\.message \d+ posted|resumed)")
_GH_OK_RX = re.compile(r"issuecomment-\d+")
_NOT_DONE = {"--help", "-h", "--dry-run"}


# --------------------------------------------------------------------------- #
# transcript → this turn's tool calls
# --------------------------------------------------------------------------- #
def _is_turn_boundary(entry):
    """A genuine human/loop prompt -- never Stop-hook feedback (``isMeta``), a
    compact summary, a task notification or a tool_result carrier."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        if "tool_result" in kinds or "text" not in kinds:
            return False
        content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return (isinstance(content, str) and bool(content.strip())
            and not content.lstrip().startswith("<task-notification>"))


def _result_text(blk):
    c = blk.get("content")
    if isinstance(c, list):
        return "\n".join(str(b.get("text") or "") for b in c if isinstance(b, dict))
    return str(c or "")


def turn_tool_calls(transcript_path, tail_bytes=TAIL_BYTES):
    """[(tool_name, input, ok, result_text)] of the current turn, oldest first
    (a call with no recorded result counts as ok). None when the transcript
    cannot be read (the caller fails open)."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail_bytes))
            raw = fh.read()
    except (OSError, TypeError, ValueError):
        return None
    calls, results = [], {}
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue                              # the cut first line / junk
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        if _is_turn_boundary(entry):
            calls, results = [], {}
            continue
        for blk in (entry.get("message") or {}).get("content") or []:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_result":
                results[blk.get("tool_use_id")] = (not blk.get("is_error"), _result_text(blk))
            elif blk.get("type") == "tool_use" and entry.get("type") == "assistant":
                inp = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                calls.append((blk.get("id"), str(blk.get("name") or ""), inp))
    return [(name, inp) + results.get(tid, (True, "")) for tid, name, inp in calls]


# --------------------------------------------------------------------------- #
# command parsing helpers (on top of the shared gates.selfservice primitives)
# --------------------------------------------------------------------------- #
def _unwrap(tk):
    """The shared ``_strip_prefix`` (sudo/env/assignments) + the wrappers a
    poster call is launched through (timeout N, nice, nohup, /usr/bin/env …)."""
    tk = _strip_prefix(tk)
    while tk and os.path.basename(tk[0]) in _WRAPPERS:
        tk = tk[1:]
        while tk and (tk[0].startswith("-") or re.match(r"^\d", tk[0])
                      or re.match(r"^[A-Za-z_]\w*=", tk[0])):
            tk = tk[1:]
    return tk


def _script_argv(tk):
    """``[script, args…]`` behind an interpreter (``python3 [opts] x.py``, ``uv run
    python x.py``, ``python -m pkg.mod`` → ``[mod, …]``) or a direct executable."""
    tk = _unwrap(tk)
    if tk[:2] == ["uv", "run"]:
        tk = tk[2:]
    if tk and _PY_RX.match(os.path.basename(tk[0])):
        tk = tk[1:]
        while tk and tk[0].startswith("-") and tk[0] not in ("-m", "-c", "-"):
            tk = tk[1:]
        if tk[:1] == ["-m"] and len(tk) > 1:
            return [tk[1].split(".")[-1]] + tk[2:]
    return tk


def _gh_comment_argv(tk):
    """``gh`` argv minus ``-R/--repo`` globals when it writes an issue/PR comment,
    else None. A comment on the airuleset repo is never a stream record."""
    tk = _unwrap(tk)
    if not tk or tk[0] != "gh":
        return None
    rest, repo, i = [], "", 1
    while i < len(tk):
        if tk[i] in ("-R", "--repo") and i + 1 < len(tk):
            repo, i = tk[i + 1], i + 2
            continue
        if tk[i].startswith("--repo="):
            repo = tk[i].split("=", 1)[1]
        else:
            rest.append(tk[i])
        i += 1
    if "airuleset" in repo:
        return None
    words = [t for t in rest[:3] if not t.startswith("-")]
    if (words[:2] in (["issue", "comment"], ["issue", "close"], ["pr", "comment"])
            or (words[:1] == ["api"] and any(re.search(r"issues/\d+/comments", t) for t in rest))):
        return ["gh"] + rest
    return None


def _flags(args):
    return {a.split("=", 1)[0] for a in args if a.startswith("-")}


def _all_values(tk, names):
    out = []
    for i, t in enumerate(tk):
        for name in names:
            if t == name and i + 1 < len(tk):
                out.append(tk[i + 1])
            elif t.startswith(name + "="):
                out.append(t[len(name) + 1:])
    return out


def _tids(args, text, res_id=True):
    """Task ids a poster call names: its task options (``--res-id`` only when
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


class _Turn:
    """The turn's debts (task id → why), settlements and verification writes."""

    def __init__(self, cwd, verif_id):
        self.cwd = cwd
        self.owed, self.settled, self.moves, self.files = {}, set(), [], {}
        target = _VERIF_WORD + (r"|%d\b" % verif_id if verif_id is not None else "")
        self.verif_rx = re.compile(r"stage_id['\"]?\s*[:=,]\s*['\"]?(?:%s)" % target,
                                   re.IGNORECASE)
        self.verif_tok = re.compile(r"^(?:%s)$" % target, re.IGNORECASE)

    def read(self, path, cwd):
        """A body file's text: written earlier this turn (Write / ``cat > f``
        heredoc -- it may be gone by Stop) or read from disk; "" when absent."""
        path = os.path.expanduser(str(path).strip("'\"").lstrip("@"))
        if not path or path == "-":
            return ""
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))
        for key in (path, full):
            if key in self.files:
                return self.files[key]
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read(512_000)
        except OSError:
            return ""

    def value(self, val, doc, cwd, env):
        """A shell word's text: ``$(cat F)`` / ``$(< F)`` → F, a heredoc
        substitution → its body, ``$VAR`` → a same-command assignment."""
        m = _SUBST_FILE_RX.search(val)
        if m:
            return self.read(m.group(1) or m.group(2), cwd)
        if "<<" in val:
            return doc
        return _VAR_RX.sub(lambda v: env.get(v.group(1), v.group(0)), val)

    def owe(self, ids, why):
        for t in ids:
            self.owed.setdefault(t, why)

    def poster(self, args, doc, cwd, env, posted, ok):
        flags = _flags(args)
        if flags & _NOT_DONE:
            return                                # nothing was posted or moved
        body = " ".join(self.value(a, doc, cwd, env) for a in args)
        if "-" in args:                           # --body - : stdin
            body += "\n" + doc + "".join(self.read(args[i + 1], cwd) for i, a in
                                         enumerate(args[:-1]) if a == "<")
        on_task = _flag_value(args, ("--model",)) == "project.task"
        if "--handover" in flags:
            if ok:
                self.settled |= _tids(args, body, res_id=on_task)
        elif posted and "--no-stage-move" not in flags and is_handover_body(body):
            ids = _tids(args, body, res_id=on_task)
            self.owe(ids or ({"?"} if on_task else set()),
                     "handover note posted without --handover")

    def py_post(self, code, cwd, posted, ok):
        """The ``odoo_post.post()`` Python API inside a heredoc / ``-c`` script."""
        body = code + "\n".join(self.read(p, cwd) for p in _OPEN_RX.findall(code))
        for model, res_id in _PY_POST_RX.findall(code):
            ids = set(_TASK_URL_RX.findall(body)) | ({res_id} if model == "project.task" else set())
            if "plan_handover" in code or "HandoverPlan" in code:
                if ok:
                    self.settled |= ids
            elif posted and "no-stage-move" not in code and is_handover_body(body):
                self.owe(ids, "handover posted via odoo_post.post() without a HandoverPlan")

    def comment(self, text):
        for line in _ACC_LINE_RX.findall(text):
            self.owe(_TASK_URL_RX.findall(line), "Acceptance-thread: names it")

    def cli_move(self, argv):
        """A task CLI stage move: ``move``/``--stage`` + task id + verif target."""
        toks = [t.split("=", 1)[-1] for t in argv[1:]]
        if not (set(toks) & _MOVE_SUBCMDS or _flags(argv) & {"--stage", "--stage-id"}):
            return
        if not _flags(argv) & _NOT_DONE and any(self.verif_tok.match(t) for t in toks):
            self.moves.append({t for t in toks if t.isdigit()})

    def script_write(self, code):
        code = re.sub(r"(?m)#[^\n'\"]*$", "", code)      # drop trailing comments
        if _STAGE_WRITE_RX.search(code) and self.verif_rx.search(code):
            self.moves.append(set(re.findall(r"(?<![\w.])(\d+)(?![\w.])", code)))

    def moved(self, tid):
        return tid in self.settled or any(tid in m for m in self.moves)


def _scan_bash(turn, cmd, ok, result, depth=0):
    """Classify every top-level segment of one Bash call into the turn."""
    file_bodies, direct_bodies, skeleton = _capture_heredocs(re.sub(r"\\\r?\n", " ", cmd))
    cwd, env = turn.cwd, {}
    for path, text in file_bodies.items():
        turn.files[path] = text
        turn.files[os.path.normpath(os.path.join(cwd, path))] = text
    posted = ok or bool(_POST_OK_RX.search(result))
    commented = ok or bool(_GH_OK_RX.search(result))
    for seg in split_top_level(skeleton):
        tk = _tokens_of(seg)
        if not tk:
            continue
        doc = "\n".join(direct_bodies.get(d, "") for d in _HEREDOC_DELIM_RX.findall(seg))
        am = _ASSIGN_SEG_RX.match(seg)
        if am:                                    # BODY=$(cat note.html)
            env[am.group(1)] = turn.value(am.group(2), doc, cwd, env)
            continue
        base = _unwrap(tk)
        if not base:
            continue
        if base[0] == "cd":
            cwd = _apply_cd(cwd, base) or cwd
            continue
        if os.path.basename(base[0]) in ("bash", "sh", "zsh") and "-c" in base[:-1]:
            if depth < 2:
                _scan_bash(turn, base[base.index("-c") + 1], ok, result, depth + 1)
            continue
        gh = _gh_comment_argv(tk)
        if gh is not None:
            body = _resolve_body(gh, seg, cwd, file_bodies, direct_bodies) or ""
            for v in _all_values(gh, ("-f", "-F", "--field", "--raw-field")):
                name, _, val = v.partition("=")
                if name == "body":
                    body += "\n" + (turn.read(val, cwd) if val.startswith("@") else val)
            if commented:
                turn.comment(body)
            continue
        argv = _script_argv(tk)
        script = os.path.basename(argv[0]) if argv else ""
        if script in ("odoo_post.py", "odoo_post"):
            turn.poster(argv[1:], doc, cwd, env, posted, ok)
            continue
        if script.endswith(".py") and "post-message" in argv[1:3]:
            args = argv[argv.index("post-message") + 1:]
            body = " ".join(turn.value(a, doc, cwd, env) for a in args)
            if posted and not _flags(args) & _NOT_DONE and is_handover_body(body):
                turn.owe({a for a in args[:1] if a.isdigit()} | set(_TASK_URL_RX.findall(body)),
                         "handover posted via odoo-task-sync.py post-message")
            continue
        interp = _PY_RX.match(os.path.basename(base[0])) or base[0] in ("uv", "curl")
        code = "\n".join([doc] + ([argv[argv.index("-c") + 1]] if "-c" in argv[:-1] else []))
        if interp and "odoo_post" in code:
            turn.py_post(code, cwd, posted, ok)
        if not ok:
            continue                              # a failed call never settles a move
        if script.endswith(".py") and not code.strip():
            turn.cli_move(argv)
        elif interp and (code.strip() or base[0] == "curl"):
            turn.script_write(code + "\n" + " ".join(argv))


def _verif_stage_id():
    cfg = cli_odoo_ro.load_config()
    vid = ((cfg or {}).get("stage_ids") or {}).get("verifikacia")
    return vid if isinstance(vid, int) and not isinstance(vid, bool) else None


def bypass_reason(message):
    """The ``airuleset:handover-ok`` reason on its own line outside code fences."""
    m = BYPASS_RX.search(_FENCE_RX.sub("", message or ""))
    return m.group(1).strip() if m else None


def evaluate(transcript_path, message, cwd, verif_id=None):
    """``("block", reason)`` or ``("allow", "")`` for this turn."""
    if bypass_reason(message):
        return "allow", ""
    calls = turn_tool_calls(transcript_path)
    if not calls:
        return "allow", ""
    turn = _Turn(cwd or os.getcwd(), verif_id if verif_id is not None else _verif_stage_id())
    for name, inp, ok, result in calls:
        if name == "Bash":
            _scan_bash(turn, str(inp.get("command") or ""), ok, result)
        elif name == "Write" and isinstance(inp.get("file_path"), str):
            turn.files[os.path.normpath(inp["file_path"])] = str(inp.get("content") or "")
        elif name.startswith("mcp__") and ok:
            turn.script_write(name + " " + json.dumps(inp, ensure_ascii=False))
        turn.settled.update(re.findall(r"OK: (?:handover|also-)task (\d+)", result))
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
        "read-back); where the checkout's poster has no --handover yet, move each "
        "task to the verification stage and set the assignee by hand now, then stop "
        "again. If the move genuinely happened in an earlier turn or is not wanted, "
        "end with its own line `airuleset:handover-ok <where/why>`." % listing)


def run(payload):
    cwd = field_of(payload, "cwd", "")
    message = field_of(payload, "last_assistant_message", "")
    bypass = bypass_reason(message)
    if bypass:
        audit.append_line(AUDIT_LOG, "%s  project=%s  cwd=%s  airuleset:handover-ok %s" % (
            audit.iso_now(), audit.project_of(cwd or None), cwd, bypass[:200]))
        return
    verdict, reason = evaluate(field_of(payload, "transcript_path", ""), message, cwd)
    if verdict == "block":
        emit_block_stderr(reason)


def main():
    run(read_payload())
    allow()


if __name__ == "__main__":
    main()
