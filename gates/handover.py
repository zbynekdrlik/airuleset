"""gates.handover -- #1179/#1180: a client handover on a project.task is ONE step.

THE INCIDENTS (owner, montalu1 28.9. + montalu4 29.9.2026): owner-approved
handovers were posted into a task chatter / a Discuss thread and the covered
board tasks were left in their old stages. The rule was written down
(``client-board-stages.md`` rule 3 + 5) but nothing tied the POST to the MOVE,
and the #1018 note-shape check only reads the assistant's final prose.

THE CHECK. Dispatched from the #1018 block of ``hooks/stop-check-prose-
violations.sh`` with the Stop payload on stdin, it reads THIS TURN's tool calls
from ``transcript_path`` (entries after the last genuine human prompt; an
``isMeta`` entry -- Stop-hook feedback -- never starts a new turn, so a blocked
turn keeps its own evidence) and blocks when:

  O1  an ``odoo_post.py`` post targets a project.task (``--model project.task``,
      ``--task-id``, a ``/tasks/<id>`` URL) with a HANDOVER-SHAPED body (the
      rule-3 markers ``Čo skúsiť`` + ``stačí``; an unreadable body counts as
      handover-shaped) and WITHOUT ``--handover`` -- the repo poster's one-step
      mode (odoo-erp#8606: post + assignee + move + read-back). A reply to a
      client question on the task carries no rule-3 markers and passes; a
      Discuss post is out of scope (not a project.task).
  O2  a ``gh issue comment`` whose body carries ``Acceptance-thread:`` names a
      board task (``/odoo/project/<pid>/tasks/<tid>``) with no ``--handover``
      run and no verification-stage move of THAT task id in the turn. A mention
      in the comment itself is never evidence (gh / post commands and Write/Edit
      are excluded from the evidence set).

Escape: ``airuleset:handover-ok <reason>`` in the final message (the step was
completed by hand / in an earlier turn -- the reason is the durable record).
Fail-OPEN on a missing/unreadable transcript (never a fabricated block).

Dry-run:
  echo '{"last_assistant_message":"x","transcript_path":"/t.jsonl","cwd":"/r"}' \
    | python3 -m gates.handover ; echo rc=$?
"""
import json
import os
import re
import shlex

from gates import allow, emit_block_stderr, field_of, read_payload

TAIL_BYTES = 4_000_000

BYPASS_RX = re.compile(r"airuleset:handover-ok[ \t]+\S")
# an INVOCATION of the poster (never a grep/cat/sed of it): at a segment start,
# optionally behind env assignments and a python interpreter.
_POSTER_RX = re.compile(
    r"(?:^|&&|\|\||[;|(\n])\s*(?:\w+=\S*\s+)*(?:(?:uv\s+run\s+)?python3?(?:\.\d+)?"
    r"\s+(?:-\S+\s+)*)?\S*odoo_post\.py(?P<args>[^\n]*)")
_GH_COMMENT_RX = re.compile(
    r"\bgh\s+(?:issue\s+comment|api\s+\S*issues/\d+/comments)\b")
_TASK_URL_RX = re.compile(r"/odoo/project/\d+/tasks/(\d+)")
_SHAPE_RX = (re.compile(r"sk[úu]si", re.IGNORECASE),
             re.compile(r"sta[čc][íi]", re.IGNORECASE))
_MOVE_RX = re.compile(
    r"stage_id|--stage\b|\bmove\b[^\n]{0,120}(?:Verifik|Na overeni|[ČC]ak|verif)",
    re.IGNORECASE)
_TID_OPT_RX = re.compile(r"^--(?:res-ids?|tasks?|task-ids?|cover\S*)$")
_SUBST_RX = re.compile(r"\$\(\s*(?:cat\s+|<\s*)([^)\s]+)\s*\)|`\s*cat\s+([^`\s]+)\s*`")
_EVIDENCE_EXCLUDED_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


# --------------------------------------------------------------------------- #
# transcript → this turn's tool calls
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
    """[(tool_name, input_dict)] of the current turn, oldest first. None when the
    transcript cannot be read (the caller fails open)."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            raw = fh.read()
    except (OSError, TypeError, ValueError):
        return None
    calls = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue                              # the cut first line / junk
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        if _is_turn_boundary(entry):
            calls = []
            continue
        if entry.get("type") != "assistant":
            continue
        for blk in (entry.get("message") or {}).get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                inp = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                calls.append((str(blk.get("name") or ""), inp))
    return calls


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def _read_file(path, cwd):
    path = os.path.expanduser(path.strip("'\""))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(512_000)
    except OSError:
        return None


def _tokens(args):
    try:
        return shlex.split(args, comments=False)
    except ValueError:
        return args.split()


def _body_text(tokens, cwd):
    """The post body (inline, ``$(cat F)`` / ``--body-file F``), or None when it
    cannot be resolved."""
    for i, tok in enumerate(tokens):
        val = None
        if tok in ("--body", "--body-file", "--html", "--html-file") and i + 1 < len(tokens):
            val = tokens[i + 1]
        elif tok.startswith(("--body=", "--body-file=", "--html=", "--html-file=")):
            val = tok.split("=", 1)[1]
        if val is None:
            continue
        if tok.startswith(("--body-file", "--html-file")):
            return _read_file(val, cwd)
        m = _SUBST_RX.search(val)
        if m:
            return _read_file(m.group(1) or m.group(2), cwd)
        return None if "$(" in val or "`" in val else val
    return None


def _tids(tokens, text):
    out, collecting = set(), False
    for tok in tokens:
        if tok.startswith("-"):
            name, _, inline = tok.partition("=")
            collecting = bool(_TID_OPT_RX.match(name))
            if collecting and inline:
                out.update(x for x in re.split(r"[,\s]+", inline) if x.isdigit())
            continue
        if collecting:
            out.update(x for x in re.split(r"[,\s]+", tok) if x.isdigit())
    out.update(_TASK_URL_RX.findall(text))
    return out


def _poster_calls(cmd):
    """[(tokens, args_text)] for every odoo_post.py INVOCATION in a command."""
    joined = re.sub(r"\\\r?\n", " ", cmd)
    out = []
    for m in _POSTER_RX.finditer(joined):
        args = m.group("args")
        toks = _tokens(args)
        if "--help" in toks or "-h" in toks:
            continue
        out.append((toks, args))
    return out


def _targets_task(tokens, args):
    for i, tok in enumerate(tokens):
        if tok == "--model" and i + 1 < len(tokens):
            return tokens[i + 1] == "project.task"
        if tok.startswith("--model="):
            return tok.split("=", 1)[1] == "project.task"
    return any(t.startswith(("--task-id", "--task=")) or t == "--task"
               for t in tokens) or bool(_TASK_URL_RX.search(args))


def _handover_shaped(body):
    return body is None or all(rx.search(body) for rx in _SHAPE_RX)


def _gh_comment_body(cmd, cwd):
    text = cmd
    for m in re.finditer(r"(?:^|\s)(?:-F|--body-file)(?:\s+|=)(\S+)", cmd):
        if m.group(1) not in ("-", "'-'", '"-"'):
            text += "\n" + (_read_file(m.group(1), cwd) or "")
    return text


def evaluate(transcript_path, message, cwd):
    """``("block", reason)`` or ``("allow", "")`` for this turn."""
    if BYPASS_RX.search(message or ""):
        return "allow", ""
    calls = turn_tool_calls(transcript_path)
    if not calls:
        return "allow", ""
    cwd = cwd or os.getcwd()
    unsent, accepted, handed, evidence, handover_any = set(), set(), set(), [], False
    for name, inp in calls:
        cmd = str(inp.get("command") or "") if name == "Bash" else ""
        if cmd and _GH_COMMENT_RX.search(cmd):
            body = _gh_comment_body(cmd, cwd)
            if "Acceptance-thread:" in body:
                accepted.update(_TASK_URL_RX.findall(body))
            continue                              # a comment is never evidence
        posts = _poster_calls(cmd) if cmd else []
        for toks, args in posts:
            if "--handover" in toks or any(t.startswith("--handover=") for t in toks):
                ids = _tids(toks, args)
                handed.update(ids)
                handover_any = handover_any or not ids
            elif _targets_task(toks, args) and _handover_shaped(_body_text(toks, cwd)):
                unsent.update(_tids(toks, args) or {"?"})
        if posts or name in _EVIDENCE_EXCLUDED_TOOLS:
            continue
        hay = cmd if name == "Bash" else json.dumps(inp, ensure_ascii=False)
        if _MOVE_RX.search(hay):
            evidence.append(hay)

    def moved(tid):
        rx = re.compile(r"(?<!\d)%s(?!\d)" % re.escape(tid))
        return tid in handed or handover_any or any(rx.search(h) for h in evidence)

    open_accept = sorted(t for t in accepted if not moved(t))
    if not unsent and not open_accept:
        return "allow", ""
    lines = ["VIOLATION (#1179/#1180): a client handover on a project.task is ONE step "
             "-- `odoo_post.py --handover` (odoo-erp#8606) posts the note, sets the "
             "rule-5 assignee and moves every covered task to the profile's "
             "verification stage (client-board-stages.md rule 3)."]
    if unsent:
        lines.append(
            "This turn posted a handover note on task(s) %s WITHOUT --handover. The "
            "note is already out -- do NOT re-post it: move every covered task to "
            "the verification stage, set the addressee as assignee (montalu), read "
            "it back, then end with `airuleset:handover-ok <what you moved/set>`. "
            "Post the next handover with --handover."
            % ", ".join("#" + t for t in sorted(unsent)))
    if open_accept:
        lines.append(
            "This turn wrote `Acceptance-thread:` naming task(s) %s with no "
            "--handover run and no verification-stage move of them. Move them (or "
            "run the poster's --handover), or cite where it already happened: "
            "`airuleset:handover-ok <ref>`." % ", ".join("#" + t for t in open_accept))
    return "block", " ".join(lines)


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
