#!/usr/bin/env python3
"""Soniox stt-async-v5 file transcription with NATIVE speaker diarization — the PRIMARY
transcription path for requirement-bearing Slovak meetings.

Why this exists (the "use something better than whisper" mandate, twice from the user):
local whisper-large-v3-turbo mangles Slovak business terms and needs a caption track for
"who spoke". Soniox stt-async-v5 (1) transcribes Slovak materially better, (2) does native
speaker diarization (no caption track required), and (3) is a cloud API so it runs from dev1
with NO dev2 GPU contention. This is the same vendor the voiceagent already uses in prod.

It is dependency-light (urllib only) so it runs anywhere python3 does.

IMPORTANT — do NOT reuse the voiceagent bakery Soniox context here. That context locks the
vocabulary to CENTRUM bread terms and would BIAS a montalu/ERP meeting transcript toward
bakery words. This script ships its own optional Slovak-ERP context (Money/Odoo domain).

Usage:
  SONIOX_API_KEY=... python3 transcribe_soniox.py <audio.wav> <out_dir> [lang=sk] [context=erp|none]

Writes into <out_dir>:
  transcript.txt      — human-readable, "[mm:ss] Speaker N: text" per segment
  transcript.json     — {tokens, segments, meta}
  speaker_turns.json  — [{speaker, start_s, end_s, text}]  (Phase 3 consumes this directly)
  summary.json        — {duration_s, n_tokens, n_segments, speakers, model}
  done | error        — terminal markers (same crash-aware watch pattern as transcribe.py)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SONIOX_BASE = "https://api.soniox.com/v1"
ASYNC_MODEL = "stt-async-v5"          # newest async model (verify against GET /v1/models)
POLL_INTERVAL_S = 5.0
POLL_MAX = 600                        # ~50 min ceiling for a long meeting
SEG_GAP_S = 0.9                       # a silence gap this long starts a new segment


# --- optional Slovak ERP context (Money/Odoo). NOT the bakery context. -------------------
ERP_CONTEXT: dict[str, Any] = {
    "general": [
        {"key": "domain", "value": "Odoo ERP + Money — obchodné oddelenie, slovenčina"},
    ],
    "terms": [
        "Odoo", "Money", "montalu", "ponuka", "cenová ponuka", "objednávka",
        "faktúra", "zálohová faktúra", "dobropis", "materiál", "dodávateľ",
        "sklad", "výroba", "artikel", "artikl", "stredisko", "cenník",
        "DPH", "platca DPH", "eKasa", "kalkulácia", "pergola", "žalúzia",
    ],
    "text": (
        "Pracovná porada o obchodnom oddelení firmy montalu. Hovorí sa o prenose dát "
        "z programu Money do Odoo: ponuky, objednávky, faktúry, zálohové faktúry, "
        "materiál, dodávatelia a výroba. Zachovaj slovenské odborné výrazy presne."
    ),
}


def _req(method: str, path: str, api_key: str, *, data: bytes | None = None,
         content_type: str | None = None, timeout: int = 300) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(SONIOX_BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def _upload(path: Path, api_key: str) -> str:
    boundary = "----meetinganalysissoniox"
    parts = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
         f'filename="{path.name}"\r\nContent-Type: audio/wav\r\n\r\n').encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    out = _req("POST", "/files", api_key, data=b"".join(parts),
               content_type=f"multipart/form-data; boundary={boundary}", timeout=600)
    return out["id"]


def _create(file_id: str, api_key: str, *, lang: str, use_context: bool) -> str:
    """Create the async transcription. Robust: if the API rejects the diarization flag or the
    context (400), retry without it rather than dying — a slightly poorer transcript beats none.
    """
    base = {
        "model": ASYNC_MODEL,
        "file_id": file_id,
        "language_hints": [lang],
        "enable_speaker_diarization": True,
    }
    attempts = []
    if use_context:
        attempts.append({**base, "context": ERP_CONTEXT})
    attempts.append(base)                                   # no context
    attempts.append({k: v for k, v in base.items()          # no diarization flag
                     if k != "enable_speaker_diarization"})
    last_err: Exception | None = None
    for body in attempts:
        try:
            created = _req("POST", "/transcriptions", api_key,
                           data=json.dumps(body).encode(), content_type="application/json")
            if "enable_speaker_diarization" not in body:
                print("WARN: created WITHOUT speaker diarization (API rejected the flag)",
                      flush=True)
            elif "context" not in body:
                print("WARN: created WITHOUT ERP context (API rejected it)", flush=True)
            return created["id"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            print(f"create attempt failed ({e.code}): {detail}", flush=True)
            last_err = e
    raise RuntimeError(f"soniox create failed on all attempts: {last_err}")


def _tokens_to_speaker_segments(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group tokens into segments, breaking on a speaker change OR a long silence gap.
    Token `text` carries its own leading spaces/punctuation → segment text is a plain join."""
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


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: transcribe_soniox.py <audio.wav> <out_dir> [lang] [context=erp|none]")
        return 2
    audio = Path(sys.argv[1])
    out = Path(sys.argv[2])
    lang = sys.argv[3] if len(sys.argv) > 3 else "sk"
    use_context = (sys.argv[4] if len(sys.argv) > 4 else "erp").lower() != "none"
    out.mkdir(parents=True, exist_ok=True)
    (out / "done").unlink(missing_ok=True)
    (out / "error").unlink(missing_ok=True)

    api_key = os.environ.get("SONIOX_API_KEY", "").strip()
    if not api_key:
        (out / "error").write_text("SONIOX_API_KEY not set")
        print("ERROR: SONIOX_API_KEY not set", flush=True)
        return 1
    if not audio.exists():
        (out / "error").write_text(f"audio missing: {audio}")
        print(f"ERROR: audio missing: {audio}", flush=True)
        return 1

    try:
        print(f"uploading {audio.name} ({audio.stat().st_size/1e6:.1f} MB)…", flush=True)
        file_id = _upload(audio, api_key)
        print(f"file_id={file_id}; creating transcription ({ASYNC_MODEL}, diarization on)…",
              flush=True)
        tid = _create(file_id, api_key, lang=lang, use_context=use_context)
        print(f"transcription id={tid}; polling…", flush=True)

        status: dict[str, Any] = {}
        for i in range(POLL_MAX):
            status = _req("GET", f"/transcriptions/{tid}", api_key)
            st = status.get("status")
            if st in ("completed", "error"):
                break
            if i % 6 == 0:                                  # ~ every 30 s
                print(f"…{st} ({i*POLL_INTERVAL_S:.0f}s)", flush=True)
            time.sleep(POLL_INTERVAL_S)
        if status.get("status") != "completed":
            msg = f"{status.get('status')} {status.get('error_message')}"
            (out / "error").write_text(msg)
            print(f"ERROR: transcription did not complete: {msg}", flush=True)
            return 1

        transcript = _req("GET", f"/transcriptions/{tid}/transcript", api_key)
        tokens = list(transcript.get("tokens") or [])
        # best-effort cleanup of server-side objects
        for p in (f"/transcriptions/{tid}", f"/files/{file_id}"):
            try:
                _req("DELETE", p, api_key)
            except Exception:
                pass

        segs = _tokens_to_speaker_segments(tokens)
        speakers = sorted({s["speaker"] for s in segs})
        dur = max((s["end_s"] for s in segs), default=0.0)

        (out / "transcript.txt").write_text(
            "\n".join(f"[{_mmss(s['start_s'])}] Speaker {s['speaker']}: {s['text']}"
                      for s in segs),
            encoding="utf-8")
        (out / "transcript.json").write_text(
            json.dumps({"model": ASYNC_MODEL, "language": lang,
                        "tokens": tokens, "segments": segs}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        (out / "speaker_turns.json").write_text(
            json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
        summary = {"duration_s": round(dur, 1), "n_tokens": len(tokens),
                   "n_segments": len(segs), "speakers": speakers, "model": ASYNC_MODEL}
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        (out / "done").write_text("ok")
        print("DONE:", json.dumps(summary, ensure_ascii=False), flush=True)
        return 0
    except Exception as e:                                   # noqa: BLE001 — mark + surface, never hang
        (out / "error").write_text(repr(e))
        print(f"ERROR: {e!r}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
