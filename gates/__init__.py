"""gates -- shared library + thin per-hook entry points for the push/commit/
filing gate family (#1020 architecture-rework).

THE PROBLEM this package fixes. The push/commit/filing gate hooks each grew as
one bash script carrying its own embedded ``python3 <<PYEOF`` classifier and a
private regex zoo, so the same concerns were re-implemented per hook: the
shell-command quote-strip/split existed in four copies, "what lines does this
push introduce to <dest>?" in three dialects, the secret classifier and the
bypass-token audit once per hook. This package holds ONE implementation of each
MIGRATED concern (``shellcmd``, ``pushscope``, ``secrets``, ``design``,
``audit``) plus thin per-hook entry modules (``secrets``/``testskips``/
``pushtest``/``commitdesign``) whose ``main()`` reads the JSON hook payload,
runs the shared logic, prints the block reason to BOTH stdout and stderr, and
exits 0/2. Each migrated bash hook is now a <=40-line stdin adapter that
resolves REPO_ROOT and ``exec env PYTHONPATH=$REPO_ROOT python3 -m gates.<gate>``.

The FILING gate (block-ungated-issue-filing.sh) is only PARTIALLY migrated in
#1020: its net-drain counter fold-in lives in the pre-existing
``ratchet_counts.py`` leaf (``net_drain_blocks_live`` + ``--explain``, no
cached-increment drift). The full filing classifier -> ``gates/filing`` package
extraction (the ~1300-line embedded Scope-gate / Dedup / stream-routing
classifier, the fleet's highest-blast-radius gate) is a deliberate followup for
its own reviewed lane.

STDLIB ONLY -- no third-party deps, and (deliberately) no ``watchdog``/``notify``
imports at package import time, so a gate stays cheap and self-contained. See the
#1020 design comment on the issue for the full architecture + rejected
alternatives.
"""
import json
import os
import sys


def read_payload():
    """The raw hook payload from STDIN (the current Claude Code PreToolUse/
    PostToolUse contract), falling back to the dead ``$TOOL_INPUT`` env var --
    the exact two-step every hook in this family used. Never raises; returns
    "" when there is nothing to read."""
    try:
        payload = sys.stdin.read()
    except Exception:
        payload = ""
    if not payload:
        payload = os.environ.get("TOOL_INPUT", "") or ""
    return payload


def command_of(payload):
    """The ``.tool_input.command`` string from a hook payload, with the
    careful raw-payload fallback block-sensitive-staging.sh established: fall
    back to the raw payload ONLY when JSON parsing produced nothing AND the
    payload is not itself tool JSON (so an empty ``command`` inside real tool
    JSON never makes a gate scan the whole JSON blob). Never raises."""
    cmd = ""
    try:
        obj = json.loads(payload)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        cmd = (obj.get("tool_input") or {}).get("command") or ""
    if cmd:
        return cmd
    if '"tool_input"' in (payload or ""):
        return ""
    return payload or ""


def field_of(payload, name, default=""):
    """A top-level payload field (e.g. ``cwd``, ``agent_id``, ``transcript_path``)
    as-is, or ``default`` when absent/unparseable. Never raises."""
    try:
        obj = json.loads(payload)
    except Exception:
        return default
    if isinstance(obj, dict):
        val = obj.get(name)
        return default if val is None else val
    return default


def emit_block(msg):
    """Print a block reason to BOTH stdout and stderr and exit 2.

    Claude Code surfaces only STDERR to the model on a PreToolUse deny, but a
    terminal run reads stdout -- so a gate that does NOT already route fd1 to
    fd2 (secrets, commit-design) prints to both (live incident 2026-07-31: an
    stderr-only reason rendered as "No stderr output"). The message text is the
    caller's own, printed verbatim."""
    sys.stdout.write(msg + "\n")
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def emit_block_stderr(msg):
    """Print a block reason to STDERR ONLY and exit 2 -- for the push gates
    (testskips/pushtest) whose adapters run the module with ``1>&2`` (the old
    hooks' ``exec 1>&2``): writing to stdout as well would then render the reason
    TWICE on the model-visible channel. stderr-only + the adapter redirect keeps
    it exactly once, matching the original hooks."""
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def allow():
    """Exit 0 -- the gate did not fire."""
    sys.exit(0)
