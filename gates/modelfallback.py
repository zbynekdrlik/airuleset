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
    on another model, e.g. scripts/rules_ab_experiment.py) -- logged;
  * a headless run (`CLAUDE_CODE_ENTRYPOINT` other than `cli`, i.e. `claude -p`)
    whose model merely differs: it chose that model. A PROVEN fallback (a
    Claude Code marker in the tail) still blocks it -- logged.
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
LOG_MAX_BYTES = 64 * 1024          # a /goal loop bouncing off the block logs per call


def _log(kind, extra=""):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", LOG_NAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")          # one rotated generation
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), kind, extra))
    except OSError:
        return


def message(v):
    """The block reason (Slovak first, then English) for verdict `v`. Only a
    PROVEN fallback (a Claude Code marker in the tail) claims that Claude Code
    switched the model; a plain mismatch says only what is observed."""
    if v.get("proven"):
        why_sk = ("Claude Code prepol model sám (safeguard fallback) a práca na "
                  "náhradnom modeli nesmie pokračovať.")
        why_en = "Claude Code switched models on its own (a fallback marker is in the transcript)."
    else:
        why_sk = ("Session nebeží na spravovanom modeli (starší model alebo fallback, "
                  "ktorého značka už nie je v konci transkriptu).")
        why_en = "The session is not on the managed model (an older model, or an earlier fallback)."
    return (
        "BLOCKED (#1203): model -- táto hlavná session beží na {short} (`{model}`), "
        "nie na spravovanom {mshort} (`{managed}`). {why_sk}\n"
        "  Čo teraz: ukonči tento turn HNEĎ, bez ďalších tool callov. Watchdog "
        "(kind `model-restore`) napíše `/model {managed}` do nečinného panelu a "
        "overí, že ďalšia odpoveď je na {mshort}; ručne: napíš `/model {managed}`.\n"
        "EN: this MAIN session is running on `{model}`, not the managed "
        "`{managed}`. {why_en} Every tool call is blocked until the model is "
        "restored: end the turn now; the watchdog types `/model {managed}` into the "
        "idle pane (or type it yourself)."
    ).format(short=v["short"], model=v["model"], mshort=v["managed_short"],
             managed=v["managed"], why_sk=why_sk, why_en=why_en)


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
    if not v["proven"] and env.get("CLAUDE_CODE_ENTRYPOINT", "cli") != "cli":
        # a headless `claude -p --model <x>` run (entrypoint sdk-cli) chose its
        # model; only a proven Claude Code fallback stops it
        return None, "headless-allow", "%s %s" % (tpath, v["model"])
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
