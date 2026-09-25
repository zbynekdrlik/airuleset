#!/usr/bin/env python3
"""A/B of the meeting-analysis ASR providers on OUR audio (#1155).

Every adapter writes the same contract (asr_contract.py), so the providers are
compared on the same slices of a real Slovak meeting with three measures:

- ERP-term error rate. A term list file holds one term per line (`#` comments;
  `re:<regex>` for a pattern such as an order code). Per term, occurrences are
  counted in the reference text and in the provider text (case-insensitive,
  whole words, Unicode NFC, any whitespace inside a multi-word term):
  misses = ref - min(ref, hyp), insertions = hyp - min(ref, hyp),
  rate = (misses + insertions) / reference occurrences. It is a bag-of-terms
  count per slice, not an alignment: a term said at the wrong moment still
  counts as found. That is enough to rank providers on vocabulary.
- Speaker-attribution agreement. reference.json holds the slice's turns
  {speaker, start_s, end_s, text} written by someone who listened. Agreement
  is the reference speech time whose provider speaker matches under the BEST
  one-to-one mapping of labels (exact, by dynamic programming), divided by the
  reference speech time. Uncovered time counts against. A provider without
  speakers (all `?`, e.g. gemini-vocab) gets n/a.
- Cost per minute, from the price table below (per audio minute, USD).

Subcommands (see AB_ASR.md for the full live procedure):
  cut    --audio audio.wav --at 600,1800,3000 --len 600 --out SLICES
  run    --provider NAME [--lang sk] [--context erp|<terms file>] SLICE_DIR...
                                                   (under `secret exec` for its key)
  score  --terms terms.txt --out report.md [--providers a,b] SLICE_DIR...

A slice dir holds audio.wav, reference.json and one output dir per provider.
This script never reads or passes a key: `run` starts the adapter with the
environment it was given.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asr_chunks import slice_wav, wav_duration

SCRIPTS = Path(__file__).resolve().parent
PRICES_AS_OF = "2026-09-25"
# provider -> (adapter script, extra argv after `<lang> erp`, USD per audio minute, source)
PROVIDERS: dict[str, tuple[str, list[str], float, str]] = {
    "soniox": ("transcribe_soniox.py", [], 0.10 / 60,
               "https://soniox.com/pricing (async ~$0.10/h)"),
    "elevenlabs": ("transcribe_elevenlabs.py", [], (0.22 + 0.05) / 60,
                   "https://elevenlabs.io/pricing/api ($0.22/h + keyterms $0.05/h)"),
    "gemini": ("transcribe_gemini.py", ["diarize"], 0.005,
               "https://ai.google.dev/gemini-api/docs/pricing (~$0.005/min blended)"),
    "gemini-vocab": ("transcribe_gemini.py", ["vocab"], 0.005,
                     "https://ai.google.dev/gemini-api/docs/pricing (~$0.005/min blended)"),
}
MAX_EXACT_LABELS = 16                 # exact mapping up to this many provider labels


def cost_per_min(provider: str) -> float:
    return PROVIDERS[provider][2]


def adapter_cmd(provider: str, slice_dir: Path, lang: str, context: str = "erp") -> list[str]:
    script, extra, _, _ = PROVIDERS[provider]
    return [sys.executable, str(SCRIPTS / script), str(slice_dir / "audio.wav"),
            str(slice_dir / provider), lang, context, *extra]


# --- ERP-term error rate ---------------------------------------------------------
@dataclass(frozen=True)
class Term:
    label: str
    pattern: re.Pattern


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def load_terms(path: Path) -> list[Term]:
    """Raises ValueError naming the line for a broken regex or one that can
    match empty text (it would count phantom hits everywhere)."""
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _nfc(raw.strip())
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            pat = line[3:]
        else:
            pat = r"(?<!\w)" + r"\s+".join(map(re.escape, line.split())) + r"(?!\w)"
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"bad term line '{line}': {e}") from None
        if rx.search("") is not None:
            raise ValueError(f"bad term line '{line}': it can match empty text")
        terms.append(Term(line, rx))
    return terms


def term_errors(ref_text: str, hyp_text: str, terms: list[Term]) -> dict[str, Any]:
    ref_text, hyp_text = _nfc(ref_text), _nfc(hyp_text)
    per_term, ref, hits, misses, ins = {}, 0, 0, 0, 0
    for t in terms:
        r = sum(1 for _ in t.pattern.finditer(ref_text))
        h = sum(1 for _ in t.pattern.finditer(hyp_text))
        both = min(r, h)
        per_term[t.label] = {"ref": r, "hyp": h}
        ref, hits, misses, ins = ref + r, hits + both, misses + r - both, ins + h - both
    return {"ref": ref, "hits": hits, "misses": misses, "insertions": ins,
            "rate": (misses + ins) / ref if ref else None, "per_term": per_term}


# --- speaker-attribution agreement ---------------------------------------------------
def _intervals(turns: list[dict[str, Any]], *, drop_unknown: bool) -> list[tuple[float, float, str]]:
    """Per-label UNION of the turns, so one label's overlapping turns never
    count their shared time twice."""
    by_label: dict[str, list[tuple[float, float]]] = {}
    for t in turns:
        s, e, spk = float(t["start_s"]), float(t["end_s"]), str(t.get("speaker", "?"))
        if e > s and not (drop_unknown and spk == "?"):
            by_label.setdefault(spk, []).append((s, e))
    out = []
    for spk, spans in by_label.items():
        cur_s, cur_e = None, None
        for s, e in sorted(spans):
            if cur_e is not None and s <= cur_e:
                cur_e = max(cur_e, e)
                continue
            if cur_e is not None:
                out.append((cur_s, cur_e, spk))
            cur_s, cur_e = s, e
        out.append((cur_s, cur_e, spk))
    return out


def _best_mapping(weights: dict[str, dict[str, float]], cols: list[str]) -> dict[str, str]:
    """One-to-one row->col mapping maximising the summed weight. Exact dynamic
    programming over column subsets; greedy past MAX_EXACT_LABELS columns."""
    rows = list(weights)
    if len(cols) > MAX_EXACT_LABELS:
        mapping, used = {}, set()
        pairs = sorted(((w, r, c) for r in rows for c, w in weights[r].items()), reverse=True)
        for w, r, c in pairs:
            if w > 0 and r not in mapping and c not in used:
                mapping[r] = c
                used.add(c)
        return mapping
    best: dict[int, tuple[float, tuple]] = {0: (0.0, ())}
    for r in rows:
        nxt = dict(best)
        for mask, (val, pairs) in best.items():
            for j, c in enumerate(cols):
                w = weights[r].get(c, 0.0)
                if w <= 0 or mask & (1 << j):
                    continue
                key, cand = mask | (1 << j), (val + w, pairs + ((r, c),))
                if key not in nxt or cand[0] > nxt[key][0]:
                    nxt[key] = cand
        best = nxt
    return dict(max(best.values(), key=lambda v: v[0])[1])


def speaker_agreement(ref_turns: list[dict[str, Any]],
                      hyp_turns: list[dict[str, Any]]) -> dict[str, Any]:
    ref = _intervals(ref_turns, drop_unknown=False)
    hyp = _intervals(hyp_turns, drop_unknown=True)
    ref_speech = sum(e - s for s, e, _ in ref)
    ref_labels = sorted({spk for _, _, spk in ref})
    hyp_labels = sorted({spk for _, _, spk in hyp})
    weights: dict[str, dict[str, float]] = {r: {} for r in ref_labels}
    for s1, e1, r in ref:
        for s2, e2, h in hyp:
            inter = min(e1, e2) - max(s1, s2)
            if inter > 0:
                weights[r][h] = weights[r].get(h, 0.0) + inter
    mapping = _best_mapping(weights, hyp_labels) if hyp_labels else {}
    matched = sum(weights[r][h] for r, h in mapping.items())
    agreement = matched / ref_speech if (hyp_labels and ref_speech) else None
    return {"agreement": agreement, "matched_s": matched, "ref_speech_s": ref_speech,
            "mapping": mapping, "n_ref": len(ref_labels), "n_hyp": len(hyp_labels)}


# --- scoring + report --------------------------------------------------------------
def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def score_slice(slice_dir: Path, provider: str, terms: list[Term]) -> dict[str, Any]:
    out = slice_dir / provider
    row: dict[str, Any] = {"slice": slice_dir.name, "provider": provider}
    if not (out / "done").exists():
        why = (out / "error").read_text()[:200] if (out / "error").exists() else "no output"
        return {**row, "missing": why}
    ref_turns = _read_json(slice_dir / "reference.json").get("turns") or []
    if not ref_turns:
        return {**row, "missing": "reference.json has no turns: fill it by listening first"}
    hyp_turns = _read_json(out / "speaker_turns.json")
    minutes = wav_duration(slice_dir / "audio.wav") / 60.0
    return {**row, "minutes": minutes, "cost": minutes * cost_per_min(provider),
            "terms": term_errors(" ".join(t.get("text", "") for t in ref_turns),
                                 " ".join(t.get("text", "") for t in hyp_turns), terms),
            "speakers": speaker_agreement(ref_turns, hyp_turns)}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool COUNTS over slices (never average per-slice rates)."""
    ref = sum(r["terms"]["ref"] for r in rows)
    errs = sum(r["terms"]["misses"] + r["terms"]["insertions"] for r in rows)
    spk = [r["speakers"] for r in rows if r["speakers"].get("agreement", 0) is not None]
    speech = sum(s["ref_speech_s"] for s in spk)
    return {"term_rate": errs / ref if ref else None,
            "misses": sum(r["terms"]["misses"] for r in rows),
            "insertions": sum(r["terms"]["insertions"] for r in rows),
            "agreement": sum(s["matched_s"] for s in spk) / speech if speech else None,
            "minutes": sum(r["minutes"] for r in rows)}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


def render_report(rows: list[dict[str, Any]], providers: list[str], terms_path: Path,
                  n_terms: int) -> str:
    slices = sorted({r["slice"] for r in rows})
    done = [r for r in rows if "missing" not in r]
    minutes = max((aggregate([r for r in done if r["provider"] == p])["minutes"]
                   for p in providers if any(r["provider"] == p for r in done)), default=0.0)
    lines = [f"# ASR A/B — {len(slices)} slice(s), {minutes:.1f} min of audio", "",
             f"Terms: `{terms_path}` ({n_terms} terms). Reference: `reference.json` per slice. "
             f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.", "",
             "| Provider | ERP-term error rate | misses | insertions | speaker agreement "
             "| cost / min | cost total |", "|---|---|---|---|---|---|---|"]
    for p in providers:
        mine = [r for r in done if r["provider"] == p]
        if len(mine) < sum(1 for r in rows if r["provider"] == p) or not mine:
            lines.append(f"| {p} | missing | | | | ${cost_per_min(p):.4f} | |")
            continue
        a = aggregate(mine)
        lines.append(f"| {p} | {_pct(a['term_rate'])} | {a['misses']} | {a['insertions']} | "
                     f"{_pct(a['agreement'])} | ${cost_per_min(p):.4f} | "
                     f"${sum(r['cost'] for r in mine):.4f} |")
    lines += ["", "## Per slice", "",
              "| Slice | Provider | term errors / ref | term error rate | speaker agreement "
              "| speakers ref/hyp |", "|---|---|---|---|---|---|"]
    for r in sorted(done, key=lambda r: (r["slice"], r["provider"])):
        t, s = r["terms"], r["speakers"]
        lines.append(f"| {r['slice']} | {r['provider']} | {t['misses'] + t['insertions']}/"
                     f"{t['ref']} | {_pct(t['rate'])} | {_pct(s['agreement'])} | "
                     f"{s['n_ref']}/{s['n_hyp']} |")
    missing = [r for r in rows if "missing" in r]
    if missing:
        lines += ["", "## Missing outputs", ""]
        lines += [f"- {r['slice']} / {r['provider']}: missing — {r['missing']}" for r in missing]
    lines += ["", "## Method", "",
              "- Term error rate = (misses + insertions) / reference occurrences, counts "
              "pooled over slices; per-slice bag-of-terms counts, case-insensitive whole "
              "words, Unicode NFC.",
              "- Speaker agreement = reference speech time whose provider speaker matches "
              "under the best one-to-one label mapping / reference speech time.",
              f"- Cost per audio minute as of {PRICES_AS_OF}:"]
    lines += [f"  - {p}: {PROVIDERS[p][3]}" for p in providers]
    return "\n".join(lines) + "\n"


# --- CLI ---------------------------------------------------------------------------
REFERENCE_TEMPLATE = {
    "_how": "Listen to audio.wav and add one turn per speaker change: "
            "{\"speaker\": \"<name>\", \"start_s\": 0.0, \"end_s\": 12.5, \"text\": \"<exact words>\"}",
    "turns": [],
}


def cmd_cut(a: argparse.Namespace) -> int:
    audio, out = Path(a.audio), Path(a.out)
    total = wav_duration(audio)
    starts = [float(x) for x in a.at.split(",") if x.strip()]
    names = [f"slice-{int(at):04d}s" for at in starts]
    if len(set(names)) != len(names):
        print(f"ERROR: two slice starts share a whole second ({a.at}); pick distinct seconds")
        return 2
    late = [at for at in starts if at >= total]
    if late:
        print(f"ERROR: slice start(s) {late} past the end ({total:.0f}s)")
        return 2
    for at, name in zip(starts, names):
        d = out / name
        slice_wav(audio, at, min(at + a.len, total), d / "audio.wav")
        ref = d / "reference.json"
        if not ref.exists():
            ref.write_text(json.dumps(REFERENCE_TEMPLATE, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
        print(f"{d}: {wav_duration(d / 'audio.wav'):.0f}s")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    failed = 0
    for d in map(Path, a.slices):
        if not (d / "audio.wav").exists():
            print(f"{d}: FAILED — no audio.wav")
            failed += 1
            continue
        rc = subprocess.run(adapter_cmd(a.provider, d, a.lang, a.context),
                            check=False).returncode
        print(f"{d} / {a.provider}: {'ok' if rc == 0 else f'FAILED rc={rc}'}")
        failed += rc != 0
    return 1 if failed else 0


def cmd_score(a: argparse.Namespace) -> int:
    providers = [p.strip() for p in a.providers.split(",") if p.strip()]
    unknown = [p for p in providers if p not in PROVIDERS]
    if unknown:
        print(f"ERROR: unknown provider(s) {unknown}; known: {sorted(PROVIDERS)}")
        return 2
    try:
        terms = load_terms(Path(a.terms))
    except ValueError as e:
        print(f"ERROR: {a.terms}: {e}")
        return 2
    rows = [score_slice(Path(d), p, terms) for d in a.slices for p in providers]
    Path(a.out).write_text(render_report(rows, providers, Path(a.terms), len(terms)),
                           encoding="utf-8")
    missing = [r for r in rows if "missing" in r]
    print(f"report: {a.out}" + (f" ({len(missing)} output(s) missing)" if missing else ""))
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ab_asr.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut", help="cut slices out of a meeting audio.wav")
    c.add_argument("--audio", required=True)
    c.add_argument("--at", required=True, help="comma-separated start seconds")
    c.add_argument("--len", type=float, default=600.0)
    c.add_argument("--out", required=True)
    r = sub.add_parser("run", help="run one provider's adapter over the slices")
    r.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    r.add_argument("--lang", default="sk")
    r.add_argument("--context", default="erp",
                   help="biasing vocabulary: erp (built-in) or a terms file, same for every provider")
    r.add_argument("slices", nargs="+")
    s = sub.add_parser("score", help="score the provider outputs into a markdown report")
    s.add_argument("--terms", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--providers", default=",".join(PROVIDERS))
    s.add_argument("slices", nargs="+")
    a = ap.parse_args(argv)
    return {"cut": cmd_cut, "run": cmd_run, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
