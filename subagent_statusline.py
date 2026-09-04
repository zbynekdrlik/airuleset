"""Subagent status-line renderer — surface each inline subagent's resolved
MODEL (+ effort) in the Claude Code agent strip (#538).

THE PROBLEM. The owner asked (2026-08-18): "bolo by mozne aby kazdy inline
subagent pisal aj aky model a verziu pouziva dole v peticke?" — the agent
strip below the prompt shows rows like `autopilot-worker  Reading … · 46m ·
↓ 541k tokens`, but never WHICH model (opus-4.6 / sonnet-5 / fable-5 /
haiku) the subagent actually runs on. CC's default row is a fixed
`name · description · token count`, no model.

THE NATIVE MECHANISM (investigate-existing-first, verified against the docs
at https://code.claude.com/docs/en/statusline — "Subagent status lines").
The `subagentStatusLine` setting (CC v2.1.205+) renders a custom row body
for each subagent in the agent panel. Its command receives, on stdin, ONE
JSON object with a `columns` field (usable row width) and a `tasks` array;
each task carries `id, name, type, status, description, label, startTime,
model, effort, contextWindowSize, tokenCount, tokenSamples, cwd`. `model`
is the RESOLVED model id (v2.1.205+, absent until resolved); `effort` is the
reasoning-effort string (v2.1.214+, absent when inherited). The command
writes one JSON line per overridden row: `{"id": "<task id>", "content":
"<row body>"}` — `content` renders as-is (ANSI + OSC 8). Omitting a task's
id keeps CC's default row for it; an empty output keeps EVERY default row.

WHY NATIVE, not a rename or a description-tag hook (rejected forks, see the
#538 design comment): renaming the 2 pinned agents to model-encoding names
is a ~90-file blast radius that re-breaks on every tiering change and cannot
touch built-in types at all; a description-tag convention duplicates what CC
now resolves natively. This module + one settings key surfaces the resolved
model for EVERY subagent (custom AND built-in) with no rename cascade.

FAIL-SAFE CONTRACT. A status line must NEVER break Claude Code. Every public
function degrades to a safe no-op on bad/partial input: `render()` returns
"" (CC keeps all default rows), and a task with no resolved `model` is
SKIPPED (CC keeps its own default row rather than a worse model-less
override). stdlib only.
"""

import json
import os
import re

# ANSI: a single distinct colour on the model badge so it stands out at the
# start of the row; nothing else is coloured, so width math (below) is done
# on the PLAIN text and the colour codes are wrapped around the (never
# truncated) badge afterwards.
_BADGE_COLOR = "\033[38;5;80m"   # teal/cyan, 256-colour (house style)
_RESET = "\033[0m"
_DIM = "\033[2m"

_SEP = " · "                 # " · " — the same middot CC's default row uses
_ELLIPSIS = "…"             # "…"

_CTX_SUFFIX_RE = re.compile(r"\[.*?\]\s*$")   # drop a "[1m]" context-window tag
# CC renders `content` as-is, so a raw newline / cursor-moving escape smuggled
# in via a `label`/`description`/`name` field would break the strip's
# one-row-per-task invariant. Collapse ALL C0/C7F control chars (incl. our own
# ESC, which never legitimately arrives in a DATA field — colour is added by us
# AFTER assembly) to a space before the content is built. Review #538 (🔵).
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(s):
    """Collapse any control char in a CC-supplied string to a space, so it can
    never break the one-row-per-task strip. Non-str → "" (caller-safe)."""
    return _CTRL_RE.sub(" ", s) if isinstance(s, str) else ""


def short_model(model_id):
    """A resolved model id → a compact human badge: family + dotted version.

    `claude-opus-4-6` → `opus-4.6`, `claude-sonnet-5` → `sonnet-5`,
    `claude-haiku-4-5` → `haiku-4.5`, `claude-fable-5[1m]` → `fable-5`. An
    unknown id degrades to a best-effort shortening (never blank, never a
    crash); a missing/blank/non-string id → "" (the caller then skips the
    row). Deliberately derived, not an exhaustive dict, so a new model id
    still renders sensibly the day it ships."""
    if not isinstance(model_id, str):
        return ""
    s = _CTX_SUFFIX_RE.sub("", model_id).strip()
    if not s:
        return ""
    if s.startswith("claude-"):
        s = s[len("claude-"):]
    parts = [p for p in s.split("-") if p != ""]
    if not parts:
        return ""
    family, ver = parts[0], parts[1:]
    return "%s-%s" % (family, ".".join(ver)) if ver else family


def _compact_tokens(n):
    """541000 → "541k", 2_300_000 → "2.3M", small/absent → "" or a bare int."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 1_000_000:
        return ("%.1fM" % (n / 1_000_000.0)).replace(".0M", "M")
    if n >= 1_000:
        return "%dk" % (n // 1_000)
    return str(n)


def _activity(task):
    """The live-activity text for a row: prefer the tool `label`, then the
    dispatch `description`, then `status`. Always a (possibly empty) str."""
    for key in ("label", "description", "status"):
        v = task.get(key)
        if isinstance(v, str) and v.strip():
            return _clean(v).strip()
    return ""


def _badge_text(task):
    """Plain badge text: `opus-4.6` or `opus-4.6·xhigh` (effort appended only
    when present). "" when the model is unresolved."""
    model = short_model(task.get("model"))
    if not model:
        return ""
    eff = task.get("effort")
    if isinstance(eff, str) and eff.strip():
        return "%s·%s" % (model, eff.strip())
    if isinstance(eff, (int, float)) and not isinstance(eff, bool):
        return "%s·%s" % (model, eff)
    return model


def render_row(task, columns):
    """Build `{"id", "content"}` for ONE task, or None to keep the default
    row (task has no `id`, or no resolved `model` to add). `content` leads
    with the coloured model badge, then name, activity and a compact token
    count, truncated to fit `columns`."""
    if not isinstance(task, dict):
        return None
    tid = task.get("id")
    if not tid:
        return None
    badge = _badge_text(task)
    if not badge:                       # nothing to add over CC's default row
        return None
    name = _clean(task.get("name") or task.get("type") or "agent") or "agent"
    tok = _compact_tokens(task.get("tokenCount"))
    activity = _activity(task)

    # Width math on PLAIN text. Badge + name are the must-keep lead; the
    # token tail is short; the activity is what gets truncated/dropped.
    lead = "%s %s" % (badge, name)
    tail_plain = (_SEP + tok) if tok else ""
    try:
        budget = int(columns)
    except (TypeError, ValueError):
        budget = 0

    act_plain = ""
    if activity:
        if budget and budget > 0:
            room = budget - len(lead) - len(tail_plain) - len(_SEP)
            if room >= 2:
                act = activity if len(activity) <= room else \
                    activity[:room - 1] + _ELLIPSIS
                act_plain = _SEP + act
            # room < 2 → drop the activity segment entirely
        else:
            act_plain = _SEP + activity

    plain = lead + act_plain + tail_plain
    if budget and budget > 0 and len(plain) > budget:
        plain = plain[:budget]          # last-resort hard clamp (keeps lead)

    # Colourise: badge in teal, token tail dimmed. Done on the assembled
    # plain string so the visible-width guarantee above is preserved.
    content = plain
    if content.startswith(badge):
        content = _BADGE_COLOR + badge + _RESET + content[len(badge):]
    if tok:
        content = content.replace(_SEP + tok, _SEP + _DIM + tok + _RESET, 1)
    return {"id": tid, "content": content}


def render(payload):
    """The command entry point: parse the `subagentStatusLine` stdin JSON and
    return one `{"id","content"}` JSON line per overridable task, joined by
    newlines. Returns "" on ANY bad/partial input (CC then keeps every
    default row) — the fail-safe contract."""
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        if isinstance(payload, str):
            payload = json.loads(payload) if payload.strip() else {}
        if not isinstance(payload, dict):
            return ""
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return ""
        columns = payload.get("columns")
        lines = []
        for task in tasks:
            row = render_row(task, columns)
            if row is not None:
                lines.append(json.dumps(row, ensure_ascii=False))
        return "\n".join(lines)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Deployment — mirrors the caveman statusline-shim topology: a managed shim
# under ~/.claude/ (REPO_DIR-substituted) imports this module from the repo
# checkout, plus a pure settings-reconcile that wires `subagentStatusLine`.
# --------------------------------------------------------------------------- #

SHIM_BASENAME = "airuleset-subagent-statusline.sh"

# The shim reads CC's JSON on stdin and pipes it through render(). Any error
# → empty stdout → CC keeps its default rows (never `set -e`: a status line
# must always exit cleanly, like the caveman shim). {{REPO_DIR}} is
# substituted at install time by render_shim().
SHIM_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — #538: prefix each subagent's agent-strip
# row with its resolved model+effort via the native subagentStatusLine hook.
REPO="{{REPO_DIR}}" exec python3 -c '
import os, sys
sys.path.insert(0, os.environ.get("REPO", ""))
try:
    import subagent_statusline as s
    sys.stdout.write(s.render(sys.stdin.read()))
except Exception:
    pass
'
"""


def render_shim(repo_dir):
    """The shim content with {{REPO_DIR}} substituted to this checkout, so its
    embedded python can `import subagent_statusline`."""
    return SHIM_CONTENT.replace("{{REPO_DIR}}", str(repo_dir))


def shim_dest(claude_dir):
    return os.path.join(str(claude_dir), SHIM_BASENAME)


def command_for(claude_dir):
    return 'bash "%s"' % shim_dest(claude_dir)


def reconcile_settings(settings, command):
    """Pure: return a NEW settings dict wiring `subagentStatusLine` → the
    managed shim command. Every other key is preserved; idempotent (same
    input → same output); never mutates the input."""
    result = dict(settings) if isinstance(settings, dict) else {}
    result["subagentStatusLine"] = {"type": "command", "command": command}
    return result


def setup(repo_dir, claude_dir, settings_path):
    """Install step (called from cmd_install): write the managed shim +
    reconcile `subagentStatusLine` into settings.json. Best-effort and
    fail-safe — an unwritable path never raises, it just returns False, so a
    broken subagent status line can never fail the whole install (mirrors
    the caveman step's non-fatal contract). Prints one status line."""
    print("  Wiring subagent status line (model+effort in agent strip)")
    ok = True
    dest = shim_dest(claude_dir)
    try:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(render_shim(repo_dir))
        os.chmod(dest, 0o755)
    except OSError as e:
        print("    could not write subagent-statusline shim (%s)" % e)
        ok = False

    try:
        raw = ""
        if os.path.isfile(settings_path):
            with open(settings_path, encoding="utf-8") as fh:
                raw = fh.read()
        settings = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError) as e:
        print("    settings.json unreadable — skipped subagentStatusLine (%s)" % e)
        return False

    new_str = json.dumps(reconcile_settings(settings, command_for(claude_dir)),
                         indent=2) + "\n"
    if new_str.strip() != raw.strip():
        try:
            if os.path.isfile(settings_path):
                import shutil
                shutil.copy2(settings_path, settings_path + ".bak")
            with open(settings_path, "w", encoding="utf-8") as fh:
                fh.write(new_str)
            print("    settings.json: subagentStatusLine -> model badge shim")
        except OSError as e:
            print("    could not write settings.json (%s)" % e)
            ok = False
    else:
        print("    settings.json: already correct")
    return ok
