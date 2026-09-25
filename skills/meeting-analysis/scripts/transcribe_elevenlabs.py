#!/usr/bin/env python3
"""ElevenLabs Scribe v2 file transcription with speaker diarization and keyterms,
writing the SAME output contract as transcribe_soniox.py (asr_contract.py, #1155).

A candidate provider for the meeting-analysis A/B (ab_asr.py). API as documented
on 2026-09-25:
  https://elevenlabs.io/docs/api-reference/speech-to-text/convert
  POST https://api.elevenlabs.io/v1/speech-to-text, header `xi-api-key`,
  multipart form: model_id=scribe_v2, file, language_code, diarize=true,
  timestamps_granularity=word, tag_audio_events=false, keyterms (repeated field,
  <=1000 terms of <=50 chars; billed extra). The response is synchronous:
  {language_code, text, words:[{text, type, start, end, speaker_id}]} where
  `type` is word | spacing | audio_event and times are seconds.

The key comes ONLY from the environment (ELEVENLABS_API_KEY), which the
credential channel sets: `airuleset.py secret exec ELEVENLABS_API_KEY -- python3
transcribe_elevenlabs.py ...`. This script never reads a key file and never
prints the key.

Usage:
  python3 transcribe_elevenlabs.py <audio.wav> <out_dir> [lang=sk] [context=erp|none]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from asr_contract import (ERP_TERMS, fail, relabel_by_first_appearance, reset_markers,
                          write_contract)

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL_ID = "scribe_v2"
KEY_ENV = "ELEVENLABS_API_KEY"
TIMEOUT_S = 1800                      # synchronous call; a 1 h meeting takes minutes


def _multipart(fields: list[tuple[str, str]], audio: Path) -> tuple[bytes, str]:
    boundary = "----meetinganalysis" + uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                     f'\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="{audio.name}"\r\nContent-Type: audio/wav\r\n\r\n').encode())
    parts.append(audio.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post(fields: list[tuple[str, str]], audio: Path, api_key: str) -> dict[str, Any]:
    body, ctype = _multipart(fields, audio)
    req = urllib.request.Request(API_URL, data=body, method="POST",
                                 headers={"xi-api-key": api_key, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read() or b"{}")


def transcribe(audio: Path, api_key: str, *, lang: str, use_keyterms: bool) -> dict[str, Any]:
    """POST the file; if the API rejects the keyterms (400/422), retry once without
    them rather than dying (the same degrade transcribe_soniox.py does for its
    context). Any other HTTP error propagates."""
    base = [("model_id", MODEL_ID), ("language_code", lang), ("diarize", "true"),
            ("timestamps_granularity", "word"), ("tag_audio_events", "false")]
    terms = [("keyterms", t) for t in ERP_TERMS] if use_keyterms else []
    try:
        return _post(base + terms, audio, api_key)
    except urllib.error.HTTPError as e:
        if not terms or e.code not in (400, 422):
            raise
        detail = e.read().decode(errors="replace")[:300]
        print(f"WARN: keyterms rejected ({e.code}): {detail} — retrying WITHOUT keyterms",
              flush=True)
        return _post(base, audio, api_key)


def words_to_tokens(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scribe `words` -> contract tokens. Only `type == word` items are text;
    spacing items are dropped (each word token carries its own leading space)
    and audio events are not speech. Speakers are renumbered 1, 2, ... by first
    appearance, like Soniox's."""
    tokens = []
    for w in words:
        if w.get("type", "word") != "word" or not str(w.get("text", "")).strip():
            continue
        tokens.append({"text": " " + str(w["text"]).strip(),
                       "start_ms": int(round(float(w.get("start", 0)) * 1000)),
                       "end_ms": int(round(float(w.get("end", w.get("start", 0))) * 1000)),
                       "speaker": w.get("speaker_id") or "?"})
    return relabel_by_first_appearance(tokens)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print("usage: transcribe_elevenlabs.py <audio.wav> <out_dir> [lang] [context=erp|none]")
        return 2
    audio, out = Path(args[0]), Path(args[1])
    lang = args[2] if len(args) > 2 else "sk"
    use_keyterms = (args[3] if len(args) > 3 else "erp").lower() != "none"
    reset_markers(out)

    api_key = os.environ.get(KEY_ENV, "").strip()
    if not api_key:
        return fail(out, f"{KEY_ENV} not set (run it under `airuleset.py secret exec {KEY_ENV} --`)")
    if not audio.exists():
        return fail(out, f"audio missing: {audio}")
    try:
        print(f"transcribing {audio.name} ({audio.stat().st_size/1e6:.1f} MB) with {MODEL_ID}"
              f" (diarize, keyterms={'on' if use_keyterms else 'off'})…", flush=True)
        resp = transcribe(audio, api_key, lang=lang, use_keyterms=use_keyterms)
        tokens = words_to_tokens(list(resp.get("words") or []))
        print(f"provider language={resp.get('language_code')} words={len(tokens)}", flush=True)
        write_contract(out, model=MODEL_ID, language=lang, tokens=tokens)
        return 0
    except Exception as e:                                   # noqa: BLE001 — mark + surface, never hang
        return fail(out, repr(e))


if __name__ == "__main__":
    raise SystemExit(main())
