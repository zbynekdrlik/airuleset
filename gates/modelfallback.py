"""gates.modelfallback -- stop work in a MAIN session that silently fell back
off the managed model (#1203). Entry for hooks/block-model-fallback.sh
(PreToolUse, every tool).

The incident: an Opus 5.5 safeguard stop switched an iemmixer session onto
`claude-opus-4-8`, and it kept working for two days (78 commits, 4 PRs) with
nobody noticing. The owner's ruling: work must NOT continue on a fallback
model. So when the transcript tail (`model_fallback.tail_state`, a bounded
read) shows that the MAIN session's last assistant entry is a fallback marker,
or that its model is not `model_lineup.MANAGED_MODEL`, every tool call is
blocked with a Slovak + English reason naming both models. The watchdog kind
`model-restore` types `/model <managed>` into the idle pane; a `/model
<managed>` typed after the last reply already reads as restored.

Exempt (ALLOW):
  * a subagent (the payload carries `agent_id`) -- it legitimately runs a
    per-dispatch model, or inherits the main;
  * the #1060 implementer window (`AIRULESET_ROLE=implementer`), which pilots
    a gateway model on purpose;
  * `AIRULESET_MODEL_GUARD=off` in the environment (a deliberate headless run
    on another model, e.g. scripts/rules_ab_experiment.py) -- logged.
FAIL-OPEN on everything else that is not a proven fallback: no transcript path,
an unreadable transcript (logged), no main assistant entry in the tail, an
unresolvable managed model. A hook bug must never wedge every session.

STDLIB ONLY; imports `model_fallback` + `model_lineup` (both import-cheap), never
airuleset.
"""
import json
import os
import time

from gates import read_payload, emit_block_stderr, allow

LOG_NAME = "model-fallback-gate.log"


def _log(kind, extra=""):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", LOG_NAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), kind, extra))
    except OSError:
        return


def message(v):
    """The block reason (Slovak first, then English) for verdict `v`."""
    return (
        "BLOCKED (#1203): model fallback -- táto hlavná session beží na {short} "
        "(`{model}`), nie na spravovanom {mshort} (`{managed}`). Claude Code prepol "
        "model sám (safeguard fallback) a práca na náhradnom modeli nesmie "
        "pokračovať.\n"
        "  Čo teraz: ukonči tento turn HNEĎ, bez ďalších tool callov. Watchdog "
        "(kind `model-restore`) napíše `/model {managed}` do nečinného panelu a "
        "overí, že ďalšia odpoveď je na {mshort}; ručne: napíš `/model {managed}`.\n"
        "EN: this MAIN session is running on `{model}`, not the managed "
        "`{managed}` -- Claude Code switched models on its own. Every tool call is "
        "blocked until the model is restored: end the turn now; the watchdog types "
        "`/model {managed}` into the idle pane (or type it yourself)."
    ).format(short=v["short"], model=v["model"], mshort=v["managed_short"],
             managed=v["managed"])


def decide(payload_text, env=None):
    """`(block_message | None, log_kind | None, log_extra)` for one payload."""
    env = os.environ if env is None else env
    try:
        obj = json.loads(payload_text or "")
    except ValueError:
        return None, None, ""
    if not isinstance(obj, dict):
        return None, None, ""
    if obj.get("agent_id"):
        return None, None, ""                      # a subagent: exempt
    if env.get("AIRULESET_ROLE") == "implementer":
        return None, None, ""                      # the #1060 implementer window
    tpath = obj.get("transcript_path")
    if not tpath or not isinstance(tpath, str):
        return None, None, ""
    import model_fallback
    try:
        st = model_fallback.tail_state(tpath)
    except OSError as exc:
        return None, "read-error", "%s %r" % (tpath, exc)
    try:
        import model_lineup
        managed = model_lineup.MANAGED_MODEL
    except Exception as exc:  # noqa: BLE001 -- an unresolvable lineup fails open
        return None, "lineup-error", repr(exc)
    v = model_fallback.verdict(st, managed)
    if v is None:
        return None, None, ""
    if (env.get("AIRULESET_MODEL_GUARD") or "").strip().lower() == "off":
        return None, "bypass", "%s %s" % (tpath, v["model"])
    return message(v), "block", "%s %s %s" % (v["kind"], v["model"], tpath)


def main():
    msg, kind, extra = decide(read_payload())
    if kind:
        _log(kind, extra)
    if msg:
        emit_block_stderr(msg)
    allow()


if __name__ == "__main__":
    main()
