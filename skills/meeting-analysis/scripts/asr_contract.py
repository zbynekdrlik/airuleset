"""The ONE output contract every meeting-analysis cloud ASR adapter writes (#1155).

`transcribe_soniox.py` defined it first; `transcribe_elevenlabs.py` and
`transcribe_gemini.py` write the same files through these helpers, so the rest
of the pipeline (Phase 3 speaker turns, the synthesis) never knows which
provider ran, and a later switch of the primary provider is a one-line change.

Into <out_dir>:
  transcript.txt      "[mm:ss] Speaker N: text", one line per segment
  transcript.json     {model, language, tokens, segments}
  speaker_turns.json  [{speaker, start_s, end_s, text}]
  summary.json        {duration_s, n_tokens, n_segments, speakers, model}
  done | error        terminal markers (the crash-aware watch in SKILL.md)

A token is {text, start_ms, end_ms, speaker, ...}: `text` carries its own
leading space (Soniox's native shape), so a segment's text is a plain join.
Speaker labels are short strings; the same label means the same voice within
one output. `?` means the provider gave no speaker for that token.

Stdlib only, like the adapters.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

SEG_GAP_S = 0.9                       # a silence gap this long starts a new segment

# Slovak ERP vocabulary (Money/Odoo, montalu) shared by every adapter's biasing
# feature: Soniox `context.terms`, ElevenLabs `keyterms`, Gemini
# `custom_vocabulary`. NOT the voiceagent bakery context (see SKILL.md).
ERP_TERMS: list[str] = [
    "Odoo", "Money", "montalu", "ponuka", "cenová ponuka", "objednávka",
    "faktúra", "zálohová faktúra", "dobropis", "materiál", "dodávateľ",
    "sklad", "výroba", "artikel", "artikl", "stredisko", "cenník",
    "DPH", "platca DPH", "eKasa", "kalkulácia", "pergola", "žalúzia",
]


def context_terms(arg: str) -> list[str] | None:
    """The biasing vocabulary an adapter sends, from its `context` argument:
    `erp` -> the built-in ERP_TERMS, `none` -> None (no biasing), anything else
    -> a term-list FILE (the same format as `ab_asr.py --terms`; its `#`
    comments and `re:` scorer patterns are skipped, providers take plain
    terms). Raises ValueError, naming the context, when the file is unusable."""
    if arg.lower() == "none":
        return None
    if arg.lower() == "erp":
        return list(ERP_TERMS)
    try:
        text = Path(arg).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"context file unreadable: {arg}: {e}") from None
    terms = [s for s in (ln.strip() for ln in text.splitlines())
             if s and not s.startswith(("#", "re:"))]
    if not terms:
        raise ValueError(f"context file has no plain terms: {arg}")
    return terms


def describe_error(e: BaseException) -> str:
    """Error-marker text. An HTTP error carries its status and the first 300
    chars of the provider's response body (the one clue on a request-shape
    mismatch); never the request headers, so never the key."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:                                    # noqa: BLE001 — body is a best-effort clue
            body = ""
        return f"HTTP {e.code} {e.reason}: {body}".rstrip(": ")
    return repr(e)


def reset_markers(out: Path) -> None:
    """Create <out> and clear a previous run's terminal markers."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "done").unlink(missing_ok=True)
    (out / "error").unlink(missing_ok=True)


def fail(out: Path, msg: str) -> int:
    """Write the `error` marker, print, return the exit code 1. The message
    must never contain a credential; callers pass error text, not headers."""
    (out / "error").write_text(msg)
    print(f"ERROR: {msg}", flush=True)
    return 1


def tokens_to_segments(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group tokens into segments, breaking on a speaker change OR a long
    silence gap. Token `text` carries its own leading spaces/punctuation, so
    the segment text is a plain join."""
    segs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for t in tokens:
        spk = t.get("speaker")
        spk = str(spk) if spk is not None else "?"
        start_s = float(t.get("start_ms", 0)) / 1000.0
        end_s = float(t.get("end_ms", t.get("start_ms", 0))) / 1000.0
        text = t.get("text", "")
        if cur is None:
            cur = {"speaker": spk, "start_s": start_s, "end_s": end_s, "text": text}
        elif spk != cur["speaker"] or start_s - cur["end_s"] > SEG_GAP_S:
            segs.append(cur)
            cur = {"speaker": spk, "start_s": start_s, "end_s": end_s, "text": text}
        else:
            cur["text"] += text
            cur["end_s"] = end_s
    if cur is not None:
        segs.append(cur)
    return [{**s, "text": s["text"].strip()} for s in segs if s["text"].strip()]


def relabel_by_first_appearance(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rename provider speaker ids (`speaker_0`, `spk_2`, ...) to "1", "2", ...
    in order of first appearance, matching Soniox's own numbering. `?` (no
    speaker) stays `?`."""
    names: dict[str, str] = {}
    out = []
    for t in tokens:
        spk = t.get("speaker")
        if spk is None or spk == "?":
            out.append({**t, "speaker": "?"})
            continue
        spk = str(spk)
        if spk not in names:
            names[spk] = str(len(names) + 1)
        out.append({**t, "speaker": names[spk]})
    return out


def mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def write_contract(out: Path, *, model: str, language: str,
                   tokens: list[dict[str, Any]],
                   segments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Write the four contract files and the `done` marker; return the summary.
    `segments` defaults to `tokens_to_segments(tokens)`."""
    segs = tokens_to_segments(tokens) if segments is None else segments
    speakers = sorted({s["speaker"] for s in segs})
    dur = max((s["end_s"] for s in segs), default=0.0)
    (out / "transcript.txt").write_text(
        "\n".join(f"[{mmss(s['start_s'])}] Speaker {s['speaker']}: {s['text']}"
                  for s in segs),
        encoding="utf-8")
    (out / "transcript.json").write_text(
        json.dumps({"model": model, "language": language,
                    "tokens": tokens, "segments": segs}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (out / "speaker_turns.json").write_text(
        json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"duration_s": round(dur, 1), "n_tokens": len(tokens),
               "n_segments": len(segs), "speakers": speakers, "model": model}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False),
                                      encoding="utf-8")
    (out / "done").write_text("ok")
    print("DONE:", json.dumps(summary, ensure_ascii=False), flush=True)
    return summary
