"""model_fallback -- read which model a Claude Code session is REALLY running on,
and whether it silently fell back off the managed model (#1203).

The incident (iemmixer on dev1, 28.9.2026): an Opus 5.5 safeguard stop made
Claude Code append an assistant entry whose content is
`{"type":"fallback","from":{"model":"claude-opus-5-5"},"to":{"model":"claude-opus-4-8"}}`
and then continue the session on `claude-opus-4-8` for two days. The footer
showed only the model FAMILY (`opus`), and nothing compared the session's
current model with `MANAGED_MODEL`, so the switch was invisible.

This module is the ONE shared reader three consumers use:
  * the statusline model token (`statusbar.model_segment`),
  * the PreToolUse gate `gates.modelfallback` (hooks/block-model-fallback.sh),
  * the watchdog recovery kind `model-restore` (`watchdog/model_restore.py`).

Shape of the evidence (verified on live transcripts, 2026-09-30):
  * the marker is a CONTENT BLOCK of an ASSISTANT entry, and that entry's
    `message.model` already names the fallback model;
  * every later assistant entry carries `message.model` = the fallback model;
  * a typed `/model <id>` is written as a `user` entry whose content is the
    `<command-name>/model</command-name> ... <command-args><id></command-args>`
    composite;
  * Claude Code's own synthetic assistant entries carry `"model":"<synthetic>"`
    (an API error, an interrupt) and are not a model signal.

The transcript read is BOUNDED (the last `TAIL_BYTES`): a session transcript can
be hundreds of MB, and the gate runs on every tool call. STDLIB ONLY, and no
import of airuleset (the gate must stay cheap): the managed model comes from
`model_lineup`.
"""
import json
import os
import re

TAIL_BYTES = 256 * 1024
DEEP_BYTES = 64 * 1024 * 1024      # find_marker: the watchdog's once-per-episode scan
_CHUNK = 1024 * 1024
FAMILIES = ("opus", "sonnet", "haiku", "fable")
SYNTHETIC_MODEL = "<synthetic>"
_MARKER_NEEDLE = b'"fallback"'    # a cheap pre-filter; the JSON parse decides

_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")
# The ONE provider-prefix / served-date pair: airuleset.py's audit predicates
# import these (#1203 review: two copies had already drifted).
PROVIDER_PREFIX_RE = re.compile(r"^(?:(?:us|eu|apac|global)\.)?anthropic\.")
SERVED_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
# claude-opus-5-5, claude-fable-5-1, claude-sonnet-5, claude-haiku-4-5 (after the
# `claude-` prefix is stripped): family, major, optional 1-2 digit minor.
_NEW_RE = re.compile(r"^(%s)-(\d{1,2})(?:-(\d{1,2}))?(?:-v\d+(?::\d+)?)?$" % "|".join(FAMILIES))
# claude-3-5-sonnet (the pre-2025 naming): major, optional minor, family.
_OLD_RE = re.compile(r"^(\d{1,2})(?:-(\d{1,2}))?-(%s)$" % "|".join(FAMILIES))
# a display name: "Opus 4.8", "Fable 5.1", "Sonnet 5".
_DISPLAY_RE = re.compile(r"^(%s)\s*(\d{1,2})(?:\.(\d{1,2}))?" % "|".join(FAMILIES))
_MODEL_CMD_RE = re.compile(
    r"<command-name>\s*/model\s*</command-name>.*?<command-args>(.*?)</command-args>",
    re.S)


def norm_model_id(model):
    """Lower-case id without the `[1m]` context tag, a provider prefix
    (`us.anthropic.`) or a served `-YYYYMMDD` date. Any non-string -> ""."""
    if not isinstance(model, str):
        return ""
    m = model.strip().lower()
    m = _SUFFIX_RE.sub("", m)
    m = PROVIDER_PREFIX_RE.sub("", m)
    return SERVED_DATE_SUFFIX_RE.sub("", m)


def short_name(model):
    """The versioned short name the footer shows: `claude-opus-5-5[1m]` ->
    `opus5.5`, `claude-opus-4-8` -> `opus4.8`, `claude-fable-5-1` -> `fable5.1`,
    `claude-sonnet-5` -> `sonnet5`, `claude-3-5-sonnet-20241022` ->
    `sonnet3.5`, a display name `Opus 4.8` -> `opus4.8`, a bare family word
    (`Fable`, `opus`) -> the family. "" for anything else (a foreign model, a
    non-string)."""
    m = norm_model_id(model)
    if not m:
        return ""
    core = m[len("claude-"):] if m.startswith("claude-") else m
    for rx, order in ((_NEW_RE, (0, 1, 2)), (_OLD_RE, (2, 0, 1)), (_DISPLAY_RE, (0, 1, 2))):
        hit = rx.match(core)
        if hit:
            fam, major, minor = (hit.group(i + 1) for i in order)
            return fam + major + ("." + minor if minor else "")
    return core if core in FAMILIES else ""


def same_model(a, b):
    """True iff two ids name the same model: equal short names when both are
    recognised, else equal normalised ids."""
    sa, sb = short_name(a), short_name(b)
    if sa and sb:
        return sa == sb
    na, nb = norm_model_id(a), norm_model_id(b)
    return bool(na) and na == nb


def _read_tail(path, max_bytes):
    """The last `max_bytes` of `path`, decoded, minus a partial first line.
    Raises OSError (the caller decides whether that fails open)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()
        return fh.read().decode("utf-8", "replace")


def _fallback_block(content):
    for block in content if isinstance(content, list) else ():
        if isinstance(block, dict) and block.get("type") == "fallback":
            frm = (block.get("from") or {}).get("model") if isinstance(block.get("from"), dict) else None
            to = (block.get("to") or {}).get("model") if isinstance(block.get("to"), dict) else None
            return {"from": frm if isinstance(frm, str) else "",
                    "to": to if isinstance(to, str) else ""}
    return None


def _marker_of(line):
    """The fallback marker dict of one raw transcript line, or None."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if not isinstance(e, dict) or e.get("type") != "assistant" or e.get("isSidechain"):
        return None
    msg = e.get("message") if isinstance(e.get("message"), dict) else {}
    fb = _fallback_block(msg.get("content"))
    return None if fb is None else dict(fb, uuid=e.get("uuid"), timestamp=e.get("timestamp"))


def find_marker(path, max_bytes=DEEP_BYTES, stop_model=None):
    """The LAST main-session fallback marker within the last `max_bytes` of the
    transcript (`{"from","to","uuid","timestamp"}`), or None. Reads backwards
    in 1 MiB chunks and parses only lines carrying the marker text, so the
    watchdog can date a fallback whose marker scrolled out of `TAIL_BYTES`.
    With `stop_model`, a main reply on that model met FIRST (i.e. later in the
    file) means any older marker was already undone: None. Raises OSError."""
    stop = norm_model_id(stop_model).encode() if stop_model else b""
    size = os.path.getsize(path)
    end, carry = size, b""
    with open(path, "rb") as fh:
        while end > 0 and size - end < max_bytes:
            start = max(0, end - _CHUNK)
            fh.seek(start)
            buf = fh.read(end - start) + carry
            end = start
            lines = buf.split(b"\n")
            carry = lines.pop(0) if start > 0 else b""
            for line in reversed(lines):
                if _MARKER_NEEDLE in line:
                    marker = _marker_of(line)
                    if marker is not None:
                        return marker
                if stop and stop in line and _main_reply_model(line, stop_model):
                    return None
    return None


def _main_reply_model(line, model):
    """True iff raw `line` is a main-session reply on `model`."""
    try:
        e = json.loads(line)
    except ValueError:
        return False
    if not isinstance(e, dict) or e.get("type") != "assistant" or e.get("isSidechain"):
        return False
    msg = e.get("message") if isinstance(e.get("message"), dict) else {}
    return same_model(msg.get("model"), model)


def _model_cmd_args(content):
    """The args of a typed `/model <args>` local command, or None."""
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and isinstance(b.get("text"), str))
    if not isinstance(content, str) or "/model" not in content:
        return None
    hit = _MODEL_CMD_RE.search(content)
    return hit.group(1).strip() if hit else None


def tail_state(path, max_bytes=TAIL_BYTES):
    """What the transcript tail says about the MAIN session's model, or None
    when the tail holds no main assistant entry. Raises OSError on a read
    failure (so the gate can log it and fail open).

    Returns a dict:
      model      -- `message.model` of the LAST main assistant entry;
      fallback   -- `{"from","to"}` when that last entry IS a fallback marker;
      marker     -- the LAST fallback marker in the tail that is still in
                    force (no later reply on another model), else None;
      timestamp / uuid -- of the last main assistant entry;
      model_cmd  -- the args of a `/model` command typed AFTER that entry
                    (the model was switched but has not answered yet), else None.
    Sidechain entries (an old-style subagent inside the main transcript) and
    `<synthetic>` entries are skipped."""
    text = _read_tail(path, max_bytes)
    last = None
    marker = None
    model_cmd = None
    for line in text.splitlines():
        if '"assistant"' not in line and "/model" not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("isSidechain"):
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        if e.get("type") == "user":
            args = _model_cmd_args(msg.get("content"))
            if args is not None:
                model_cmd = args
            continue
        if e.get("type") != "assistant":
            continue
        model = msg.get("model")
        if not isinstance(model, str) or not model or model == SYNTHETIC_MODEL:
            continue
        fb = _fallback_block(msg.get("content"))
        if fb is not None:
            marker = dict(fb, uuid=e.get("uuid"), timestamp=e.get("timestamp"))
        elif marker is not None and not same_model(model, marker["to"]):
            marker = None          # a later reply left the fallback model: undone
        last = {"model": model, "fallback": fb, "uuid": e.get("uuid"),
                "timestamp": e.get("timestamp")}
        model_cmd = None
    if last is None:
        return None
    last["marker"] = marker
    last["model_cmd"] = model_cmd
    return last


def transcript_model(path, max_bytes=TAIL_BYTES):
    """The model the transcript tail says the MAIN session runs on, or None (no
    path, unreadable, no main reply): a `/model` typed after the last reply
    wins, then the fallback marker's target, then the last reply's model."""
    if not path or not isinstance(path, str):
        return None
    try:
        st = tail_state(path, max_bytes)
    except OSError:
        return None
    if not st:
        return None
    return st.get("model_cmd") or (st.get("fallback") or {}).get("to") or st.get("model")


def verdict(state, managed):
    """None when the session runs the managed model (or the comparison cannot
    be made), else a dict `{kind, model, short, managed, managed_short}` where
    `kind` is `marker` (the last entry IS the fallback marker) or `model` (the
    last entry's model is not the managed one).

    A `/model <managed>` typed after the last assistant entry reads as
    restored (None): the switch is done and the next reply will carry it."""
    if not isinstance(state, dict) or not managed:
        return None
    if not (short_name(managed) or norm_model_id(managed)):
        return None
    cmd = state.get("model_cmd")
    if cmd and same_model(cmd, managed):
        return None
    model = state.get("model") or ""
    if state.get("fallback"):
        kind = "marker"
    elif not same_model(model, managed):
        kind = "model"
    else:
        return None
    # `proven`: a Claude Code fallback marker is in the tail -- the switch is
    # Claude Code's own, not a session that simply runs another model.
    return {"kind": kind, "model": model, "short": short_name(model) or model,
            "managed": managed, "managed_short": short_name(managed) or managed,
            "proven": state.get("marker") is not None}
