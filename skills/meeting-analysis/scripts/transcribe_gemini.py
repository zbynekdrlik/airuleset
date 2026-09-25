#!/usr/bin/env python3
"""Gemini `gemini-3.5-transcribe` file transcription (sk-SK), writing the SAME output
contract as transcribe_soniox.py (asr_contract.py, #1155).

A candidate provider for the meeting-analysis A/B (ab_asr.py). API as documented
on 2026-09-25:
  https://ai.google.dev/gemini-api/docs/transcribe
  https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe
  https://ai.google.dev/gemini-api/docs/files
  audio is uploaded with the Files API (resumable upload), then
  POST https://generativelanguage.googleapis.com/v1beta/interactions
  {model, input:[{type:audio, uri, mime_type}],
   generation_config:{transcription_config:{language_codes, mode | custom_vocabulary}}}
  header `x-goog-api-key`. With diarization the response carries
  steps[].content[].annotations[] of type word_info {text, speaker spk_N,
  start_offset "0.100s", end_offset}.

Two documented limits shape this adapter:
- A request may be 1 h long, but only 30 min with diarization or word
  timestamps. Longer audio is cut into overlapping chunks (asr_chunks.py) and
  the per-request speaker labels are stitched into one timeline (the rule and
  its limit are documented there).
- `custom_vocabulary` CANNOT be combined with diarization or word timestamps.
  So there are two modes:
    diarize (default)  speakers + word timestamps, no vocabulary. The ERP
                       context is NOT sent; a WARN says so.
    vocab              the ERP vocabulary, no speakers: speaker_turns.json
                       carries one `?` turn per chunk with the chunk's times.
  The A/B scores both (providers `gemini` and `gemini-vocab`).

The key comes ONLY from the environment (GEMINI_API_KEY), which the credential
channel sets: `airuleset.py secret exec GEMINI_API_KEY -- python3
transcribe_gemini.py ...`. This script never reads a key file and never prints
the key.

Usage:
  python3 transcribe_gemini.py <audio.wav> <out_dir> [lang=sk] [context=erp|none] [mode=diarize|vocab]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any

from asr_chunks import plan_chunks, slice_wav, stitch_chunks, wav_duration
from asr_contract import ERP_TERMS, fail, reset_markers, write_contract

BASE = "https://generativelanguage.googleapis.com"
MODEL = "gemini-3.5-transcribe"
KEY_ENV = "GEMINI_API_KEY"
CHUNK_S = 28 * 60.0                   # under the 30 min diarization limit
OVERLAP_S = 60.0                      # shared audio for speaker stitching
VOCAB_CHUNK_S = 55 * 60.0             # under the 1 h limit; no stitching needed
POLL_INTERVAL_S = 5.0
POLL_MAX = 360                        # 30 min per chunk
TIMEOUT_S = 900
LOCALES = {"sk": "sk-SK", "cs": "cs-CZ", "en": "en-US", "de": "de-DE", "hu": "hu-HU"}


def bcp47(lang: str) -> str:
    """`sk` -> `sk-SK`; an explicit locale passes through."""
    return lang if "-" in lang else LOCALES.get(lang.lower(), f"{lang.lower()}-{lang.upper()}")


def _call(method: str, url: str, api_key: str, *, data: bytes | None = None,
          headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"x-goog-api-key": api_key, **(headers or {})})
    resp = urllib.request.urlopen(req, timeout=TIMEOUT_S)
    with resp:
        body = resp.read()
        return (json.loads(body) if body else {}), resp.headers


def _upload(path: Path, api_key: str) -> dict[str, Any]:
    """Files API resumable upload; returns the `file` object (name, uri, state)."""
    size = path.stat().st_size
    _, hdrs = _call("POST", f"{BASE}/upload/v1beta/files", api_key,
                    data=json.dumps({"file": {"display_name": path.name}}).encode(),
                    headers={"X-Goog-Upload-Protocol": "resumable",
                             "X-Goog-Upload-Command": "start",
                             "X-Goog-Upload-Header-Content-Length": str(size),
                             "X-Goog-Upload-Header-Content-Type": "audio/wav",
                             "Content-Type": "application/json"})
    upload_url = hdrs.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Files API start returned no x-goog-upload-url header")
    body, _ = _call("POST", upload_url, api_key, data=path.read_bytes(),
                    headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                             "X-Goog-Upload-Command": "upload, finalize"})
    f = body.get("file") or {}
    for _ in range(POLL_MAX):
        if f.get("state", "ACTIVE") != "PROCESSING":
            break
        time.sleep(POLL_INTERVAL_S)
        f, _ = _call("GET", f"{BASE}/v1beta/{f['name']}", api_key)
    if f.get("state", "ACTIVE") != "ACTIVE" or not f.get("uri"):
        raise RuntimeError(f"uploaded file not usable: state={f.get('state')}")
    return f


def _interaction(uri: str, api_key: str, config: dict[str, Any]) -> dict[str, Any]:
    body = {"model": MODEL,
            "input": [{"type": "audio", "uri": uri, "mime_type": "audio/wav"}],
            "generation_config": {"transcription_config": config}}
    resp, _ = _call("POST", f"{BASE}/v1beta/interactions", api_key,
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"})
    for _ in range(POLL_MAX):
        if resp.get("status") not in ("in_progress", "queued"):
            break
        time.sleep(POLL_INTERVAL_S)
        iid = str(resp.get("id", ""))
        path = iid if iid.startswith("interactions/") else f"interactions/{iid}"
        resp, _ = _call("GET", f"{BASE}/v1beta/{path}", api_key)
    if resp.get("status") != "completed":
        raise RuntimeError(f"interaction ended {resp.get('status')}: {resp.get('error')}")
    return resp


def _seconds(offset: Any) -> float:
    return float(str(offset or "0").rstrip("s") or 0)


def parse_words(resp: dict[str, Any]) -> list[dict[str, Any]]:
    """word_info annotations -> [{text, start_s, end_s, speaker}] (chunk-local)."""
    words = []
    for step in resp.get("steps") or []:
        for block in step.get("content") or []:
            for a in block.get("annotations") or []:
                if a.get("type") != "word_info" or not str(a.get("text", "")).strip():
                    continue
                words.append({"text": str(a["text"]).strip(),
                              "start_s": _seconds(a.get("start_offset")),
                              "end_s": _seconds(a.get("end_offset")),
                              "speaker": a.get("speaker") or "?"})
    return words


def parse_text(resp: dict[str, Any]) -> str:
    if resp.get("output_text"):
        return str(resp["output_text"]).strip()
    return " ".join(str(b.get("text", "")).strip() for s in resp.get("steps") or []
                    for b in s.get("content") or [] if b.get("type") == "text").strip()


def _config(lang: str, mode: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {"language_codes": [bcp47(lang)]}
    if mode == "vocab":
        cfg["custom_vocabulary"] = list(ERP_TERMS)
    else:
        cfg["mode"] = {"type": "verbatim", "diarization_mode": "speaker",
                       "timestamp_granularities": ["word"]}
    return cfg


def transcribe(audio: Path, api_key: str, *, lang: str, mode: str) -> list[dict[str, Any]]:
    """Chunk, upload, transcribe, delete; return contract tokens."""
    dur = wav_duration(audio)
    plan = (plan_chunks(dur, VOCAB_CHUNK_S, 0.0) if mode == "vocab"
            else plan_chunks(dur, CHUNK_S, OVERLAP_S))
    print(f"{dur:.0f}s of audio -> {len(plan)} request(s), mode={mode}", flush=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="gemini-chunks-") as tmp:
        for i, (start, end) in enumerate(plan):
            part = slice_wav(audio, start, end, Path(tmp) / f"chunk{i:03d}.wav")
            f = _upload(part, api_key)
            try:
                resp = _interaction(f["uri"], api_key, _config(lang, mode))
            finally:
                try:                                       # best-effort server cleanup
                    _call("DELETE", f"{BASE}/v1beta/{f['name']}", api_key)
                except Exception as e:                     # noqa: BLE001 — never fail the run on cleanup
                    print(f"WARN: could not delete {f.get('name')}: {e!r}", flush=True)
            print(f"chunk {i + 1}/{len(plan)} [{start:.0f}-{end:.0f}s] done", flush=True)
            results.append((start, end, resp))
    if mode == "vocab":
        return [{"text": " " + parse_text(r), "start_ms": int(s * 1000),
                 "end_ms": int(e * 1000), "speaker": "?"}
                for s, e, r in results if parse_text(r)]
    words = stitch_chunks([{"start_s": s, "end_s": e, "words": parse_words(r)}
                           for s, e, r in results])
    return [{"text": " " + w["text"], "start_ms": int(round(w["start_s"] * 1000)),
             "end_ms": int(round(w["end_s"] * 1000)), "speaker": w["speaker"]} for w in words]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print("usage: transcribe_gemini.py <audio.wav> <out_dir> [lang] [context=erp|none]"
              " [mode=diarize|vocab]")
        return 2
    audio, out = Path(args[0]), Path(args[1])
    lang = args[2] if len(args) > 2 else "sk"
    use_context = (args[3] if len(args) > 3 else "erp").lower() != "none"
    mode = (args[4] if len(args) > 4 else "diarize").lower()
    reset_markers(out)

    if mode not in ("diarize", "vocab"):
        return fail(out, f"unknown mode {mode!r} (diarize|vocab)")
    api_key = os.environ.get(KEY_ENV, "").strip()
    if not api_key:
        return fail(out, f"{KEY_ENV} not set (run it under `airuleset.py secret exec {KEY_ENV} --`)")
    if not audio.exists():
        return fail(out, f"audio missing: {audio}")
    try:
        with wave.open(str(audio), "rb"):
            pass
    except (wave.Error, EOFError) as e:
        return fail(out, f"not a PCM wav (run extract.sh first): {audio}: {e}")
    if mode == "vocab" and not use_context:
        return fail(out, "mode=vocab needs context=erp (the vocabulary is the point)")
    if mode == "diarize" and use_context:
        print("WARN: the ERP vocabulary is NOT sent — Gemini rejects custom_vocabulary"
              " together with diarization (use mode=vocab to test the vocabulary)", flush=True)
    try:
        tokens = transcribe(audio, api_key, lang=lang, mode=mode)
        write_contract(out, model=MODEL, language=lang, tokens=tokens)
        return 0
    except Exception as e:                                   # noqa: BLE001 — mark + surface, never hang
        return fail(out, repr(e))


if __name__ == "__main__":
    raise SystemExit(main())
