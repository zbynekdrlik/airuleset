"""Chunk long audio for providers with a per-request length limit, and stitch the
per-chunk speaker labels back into one timeline (#1155).

Why: gemini-3.5-transcribe diarizes at most 30 min per request (1 h without
diarization), and it numbers its speakers PER REQUEST, so chunk 2's `spk_1` is
not necessarily chunk 1's `spk_1`.

The stitching rule (documented and tested — tests/test_asr_chunk_stitch_1155.py):
1. Consecutive chunks OVERLAP by `overlap_s` (plan_chunks).
2. For chunk k > 0, every (global label g, local label l) pair is weighted by the
   speaking time they share inside the overlap: the sum over word pairs of the
   intersection of their [start, end] intervals, where the g words are chunk
   k-1's words (already on global labels) and the l words are chunk k's words.
   Words without a speaker (`?`) are no evidence and never take part.
3. Pairs are assigned greedily by descending shared time, one-to-one, and only
   when the shared time is at least MIN_MATCH_S. Greedy is enough here: in the
   overlap a voice nearly always shares most of its time with exactly one label,
   and the provider caps speakers at 8.
4. A local label with no match becomes a NEW global label. LIMIT: a speaker who
   is silent throughout an overlap cannot be linked across it and is counted as
   a new speaker. A longer overlap lowers the chance of this; the A/B speaker
   score (ab_asr.py) measures the real effect on our meetings.
5. Words in the overlap are kept once: before the overlap's midpoint from the
   earlier chunk, from the midpoint on from the later chunk.
6. Global labels are "1", "2", ... in order of first appearance.

Stdlib only (`wave`), the same style as prep.py. Input is the 16 kHz mono PCM
wav that extract.sh writes.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

from asr_contract import relabel_by_first_appearance

MIN_MATCH_S = 0.2                     # shared speech needed to link two labels


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def slice_wav(src: Path, start_s: float, end_s: float, dst: Path) -> Path:
    """Copy [start_s, end_s) of a PCM wav into `dst` (clamped to the file)."""
    with wave.open(str(src), "rb") as r:
        rate = r.getframerate()
        total = r.getnframes()
        first = max(0, min(total, int(round(start_s * rate))))
        last = max(first, min(total, int(round(end_s * rate))))
        r.setpos(first)
        frames = r.readframes(last - first)
        params = r.getparams()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    return dst


def plan_chunks(duration_s: float, chunk_s: float, overlap_s: float) -> list[tuple[float, float]]:
    """[(start, end)] covering [0, duration]; each chunk at most chunk_s long,
    consecutive chunks overlapping by overlap_s."""
    if duration_s <= 0:
        raise ValueError(f"audio has no duration ({duration_s})")
    if not 0 <= overlap_s < chunk_s:
        raise ValueError(f"overlap {overlap_s} must be >= 0 and below the chunk {chunk_s}")
    chunks: list[tuple[float, float]] = []
    start = 0.0
    while True:
        end = min(start + chunk_s, duration_s)
        chunks.append((round(start, 3), round(end, 3)))
        if end >= duration_s:
            return chunks
        start = end - overlap_s


def _shared_time(a: list[dict[str, Any]], b: list[dict[str, Any]],
                 lo: float, hi: float) -> dict[tuple[str, str], float]:
    """Shared speaking time inside [lo, hi] per (label in a, label in b)."""
    def clip(ws):
        out = []
        for w in ws:
            s, e = max(w["start_s"], lo), min(w["end_s"], hi)
            if e > s and w["speaker"] != "?":          # no speaker = no evidence
                out.append((s, e, w["speaker"]))
        return out
    weights: dict[tuple[str, str], float] = {}
    for s1, e1, g in clip(a):
        for s2, e2, loc in clip(b):
            inter = min(e1, e2) - max(s1, s2)
            if inter > 0:
                weights[(g, loc)] = weights.get((g, loc), 0.0) + inter
    return weights


def _map_labels(weights: dict[tuple[str, str], float]) -> dict[str, str]:
    """Greedy one-to-one local -> global mapping by descending shared time."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for (g, loc), w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        if w < MIN_MATCH_S or loc in mapping or g in used:
            continue
        mapping[loc] = g
        used.add(g)
    return mapping


def stitch_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge per-chunk words into one absolute timeline with global speakers.

    `chunks`: [{"start_s", "end_s", "words": [{"text", "start_s", "end_s",
    "speaker"}]}] in chunk order, word times RELATIVE to the chunk start.
    Returns [{"text", "start_s", "end_s", "speaker"}] sorted by start_s.
    """
    out: list[dict[str, Any]] = []
    prev_words: list[dict[str, Any]] = []      # previous chunk, global labels, all words
    prev_end = 0.0
    next_label = 1
    for idx, ch in enumerate(chunks):
        offset = float(ch["start_s"])
        words = [{**w, "start_s": w["start_s"] + offset, "end_s": w["end_s"] + offset,
                  "speaker": str(w.get("speaker") or "?")}
                 for w in sorted(ch.get("words") or [], key=lambda w: w["start_s"])]
        if idx == 0:
            mapping: dict[str, str] = {}
            cut = None
        else:
            lo, hi = offset, prev_end
            mapping = _map_labels(_shared_time(prev_words, words, lo, hi)) if hi > lo else {}
            cut = (lo + hi) / 2 if hi > lo else offset
            out = [w for w in out if w["start_s"] < cut]
        labelled = []
        for w in words:
            loc = w["speaker"]
            if loc == "?":
                glob = "?"
            elif loc in mapping:
                glob = mapping[loc]
            else:
                glob = mapping[loc] = str(next_label)
                next_label += 1
            labelled.append({**w, "speaker": glob})
        out.extend(w for w in labelled if cut is None or w["start_s"] >= cut)
        prev_words, prev_end = labelled, float(ch["end_s"])
    # a label seen only in a dropped overlap half consumed a number; renumber
    # so the kept labels are contiguous in order of first appearance
    return relabel_by_first_appearance(sorted(out, key=lambda w: w["start_s"]))
