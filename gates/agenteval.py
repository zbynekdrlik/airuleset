r"""gates.agenteval -- #1077: a guide section is an agent-eval fixture.

Owner ruling 18.9.2026 (montalu1): „takéto veci by sa mali okrem do návodov
overovať, či ich vie správne AI pomocník riešiť a prípadne aj vykonať podľa
pokynov". A guide is not finished when the HTML reads well -- it is finished
when the tenant's AI assistant answers the same questions correctly (and, for
executable steps, performs them) on a COPY, with the client persona, BEFORE the
client sees the guide.

This leaf is the PURE predicate half of that rule; the RUNNER stays the
project's own (`services/agent/scripts/eval_navody.py` in odoo-erp -- copy-target
fail-closed, persona enrollment, effect verify+revert), never rebuilt here. This
gate only ties a guide-SOURCE change to the recorded eval: the hand-off RFR body
must carry an `AI-eval:` line.

Two consumers share this module (the same split `gates.navody` uses):
  (a) `airuleset.py handoff`'s composer pre-flight `_handoff_agent_eval_preflight`
      calls `check_handoff()` / `agent_eval()` -- a guide-source diff must carry
      `AI-eval: <fixture>-qa.json → <tally> (<report>)` (both files present) OR
      `AI-eval: n/a — <why>`; otherwise refuse, naming the sources / the missing
      path;
  (b) tests call the pure functions directly.

A guide SOURCE is any changed path matching the four globs (dispatch-authoritative):
    docs/<tenant>/navody/**        -- anything under a tenant's navody/ dir
    docs/**/build-*-guide.py       -- a guide builder
    docs/**/navody_*_sections.py   -- a guide section source
    docs/**/*-qa.json              -- a machine-readable fixture

The per-tenant fact lives in `<repo>/.claude/streams/<stream>.md` (same file +
rename-alias resolution as `navody_url`, read via `gates.navody._read_stream_file`):
    agent_eval: NONE — <why>
A NONE fact means the tenant has no agent (or its agent-on-copy is blocked): a
bare `AI-eval: n/a` is then SUFFICIENT (the fact carries the why) but the n/a
line is STILL mandatory -- a guide-source RFR always has to say SOMETHING about
the assistant.

STDLIB ONLY. Every regex is LINEAR (segment-partitioned `/`, no backtracking
`.*\S` -- the repo's #577/#1010 ReDoS discipline).
"""
import os
import re

from gates.navody import _read_stream_file, _current_stream

# --------------------------------------------------------------------------- #
# guide-SOURCE detection (the four globs). Each is `/`-partitioned so slashes
# split the input deterministically -- no catastrophic backtracking.
# --------------------------------------------------------------------------- #
_GUIDE_SOURCE_RES = (
    # docs/<tenant>/navody/** -- a tenant segment then a navody/ dir at any depth
    re.compile(r'(?:^|/)docs/[^/]+/(?:[^/]+/)*navody/', re.IGNORECASE),
    # docs/**/build-*-guide.py
    re.compile(r'(?:^|/)docs/(?:[^/]+/)*build-[^/]*-guide\.py$', re.IGNORECASE),
    # docs/**/navody_*_sections.py
    re.compile(r'(?:^|/)docs/(?:[^/]+/)*navody_[^/]*_sections\.py$',
               re.IGNORECASE),
    # docs/**/*-qa.json
    re.compile(r'(?:^|/)docs/(?:[^/]+/)*[^/]*-qa\.json$', re.IGNORECASE),
)

# an `AI-eval:` line (optionally led by a list/quote marker); the remainder is
# read from the captured group, never a trailing `.*\S` (line-bounded, no
# DOTALL -- linear).
_AI_EVAL_RE = re.compile(
    r'(?im)^[ \t]*[-*>]{0,2}[ \t]*AI-eval[ \t]{0,4}:[ \t]{0,4}(?P<rest>.*)$')
# `n/a` at the start of the remainder, with a bounded separator; the reason is
# whatever follows (`.strip()` truthy == a reason present).
_NA_RE = re.compile(r'(?i)^n[ \t]{0,2}/?[ \t]{0,2}a\b(?P<why>.*)$')
# the first `<path>-qa.json` token (a fixture path).
_FIXTURE_RE = re.compile(r'(?P<fixture>\S+-qa\.json)\b')
# the `(<report>)` path.
_REPORT_RE = re.compile(r'\((?P<report>[^)]+)\)')
# the per-tenant `agent_eval:` fact line.
_FACT_RE = re.compile(r'^\s*agent_eval\s*:\s*(.+?)\s*$',
                      re.IGNORECASE | re.MULTILINE)


def is_guide_source(path):
    """True when a changed path is a guide SOURCE (one of the four globs)."""
    p = (path or "").replace("\\", "/")
    return any(rx.search(p) for rx in _GUIDE_SOURCE_RES)


def tenant_agent_eval(cwd, stream, *, read_text=None):
    """The tenant's `agent_eval:` fact VALUE (e.g. `NONE — bez agenta`), or None
    when the line/file is absent. Reads the SAME stream file + rename alias as
    `gates.navody.tenant_guide` (via the shared `_read_stream_file`)."""
    text = _read_stream_file(cwd, stream, read_text=read_text)
    if text is None:
        return None
    m = _FACT_RE.search(text)
    if not m:
        return None
    val = (m.group(1) or "").strip()
    return val or None


def _is_none_fact(fact):
    return bool(fact) and fact.strip().upper().startswith("NONE")


def _eval_one(rest, cwd, fact):
    """Judge ONE `AI-eval:` remainder -> (ok, reason_or_None)."""
    r = (rest or "").strip()
    na = _NA_RE.match(r)
    if na:
        if (na.group("why") or "").strip() or _is_none_fact(fact):
            return True, None
        return False, (
            "handoff BLOCK: `AI-eval: n/a` musí uviesť dôvod "
            "(`AI-eval: n/a — <prečo: tenant bez agenta / agent-na-kópii "
            "blokovaný, uveď ticket>`), alebo nastav v "
            "`.claude/streams/<stream>.md` fakt `agent_eval: NONE — <prečo>` "
            "(#1077).")
    fx = _FIXTURE_RE.search(r)
    if not fx:
        return False, (
            "handoff BLOCK: `AI-eval:` má neplatný tvar — očakávam "
            "`AI-eval: <fixture>-qa.json → <rubric tally> (<report>)` alebo "
            "`AI-eval: n/a — <prečo>` (#1077).")
    fixture = fx.group("fixture")
    base = cwd or "."
    if not os.path.exists(os.path.join(base, fixture)):
        return False, (
            "handoff BLOCK: `AI-eval:` fixture `%s` v strome neexistuje — "
            "uveď skutočnú cestu k `docs/<tenant>/navody/<sekcia>-qa.json` "
            "(#1077)." % fixture)
    rep = _REPORT_RE.search(r)
    if not rep:
        return False, (
            "handoff BLOCK: `AI-eval:` musí uviesť cestu k reportu v zátvorke "
            "`(<report>)` (napr. `(docs/ai-agent/eval-navody-<sekcia>-<dátum>.md)`) "
            "(#1077).")
    report = (rep.group("report") or "").strip()
    if not os.path.exists(os.path.join(base, report)):
        return False, (
            "handoff BLOCK: `AI-eval:` report `%s` v strome neexistuje — spusti "
            "projektový runner `services/agent/scripts/eval_navody.py` na KÓPII "
            "a commitni report (#1077)." % report)
    return True, None


def agent_eval(changed_paths, body, *, cwd=None, fact=None):
    """(ok, reason_or_None) for the RFR diff.

    A guide-SOURCE diff must carry a valid `AI-eval:` line; a non-guide diff
    passes. `fact` is the tenant's `agent_eval:` value (a NONE fact makes a bare
    `n/a` sufficient); `cwd` is where the fixture/report paths are resolved."""
    paths = changed_paths or []
    touched = [p for p in paths if is_guide_source(p)]
    if not touched:
        return True, None
    rests = [m.group("rest") for m in _AI_EVAL_RE.finditer(body or "")]
    if not rests:
        return False, (
            "handoff BLOCK: RFR mení zdroj návodu (%s) bez overenia AI "
            "pomocníka (#1077). Pridaj do RFR riadok `AI-eval: "
            "<fixture>-qa.json → <rubric tally> (<report>)` — fixture "
            "spustený projektovým runnerom (`services/agent/scripts/"
            "eval_navody.py`) proti agentovi na KÓPII s klientskou personou; "
            "alebo `AI-eval: n/a — <prečo>` (tenant bez agenta / agent-na-kópii "
            "blokovaný, uveď ticket)." % ", ".join(touched[:5]))
    reasons = []
    for rest in rests:
        ok, reason = _eval_one(rest, cwd, fact)
        if ok:
            return True, None
        reasons.append(reason)
    return False, reasons[0]


def check_handoff(body, changed_paths, *, cwd, stream=None):
    """Compose: resolve the stream (the box's uid account when not given), read
    its per-tenant `agent_eval:` fact, then `agent_eval()`. The thin seam the
    composer pre-flight calls so `airuleset.py` stays minimal (logic lives here)."""
    if stream is None:
        stream = _current_stream()
    fact = tenant_agent_eval(cwd, stream)
    return agent_eval(changed_paths, body, cwd=cwd, fact=fact)
