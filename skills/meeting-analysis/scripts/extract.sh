#!/usr/bin/env bash
# Extract the three analysis inputs from a meeting recording:
#   1. audio.wav  — 16 kHz mono PCM (what the ASR model wants)        [always]
#   2. frames/    — one keyframe every 8 s (the screenshare timeline) [video only]
#   3. subs.srt   — embedded/sidecar caption track, if text-format    [free diarization]
#
# Audio-only input is fully supported: it produces audio.wav, 0 frames, and (if
# present) subs — the caller then skips the screen-reading phase.
#
# Usage: extract.sh <input-video> <work-dir>
# Requires: ffmpeg + ffprobe on PATH.
set -euo pipefail

VIDEO="${1:?usage: extract.sh <input-video> <work-dir>}"
WORK="${2:?usage: extract.sh <input-video> <work-dir>}"
mkdir -p "$WORK/frames"

[ -f "$VIDEO" ] || { echo "ERROR: no such file: $VIDEO" >&2; exit 1; }
command -v ffmpeg  >/dev/null || { echo "ERROR: ffmpeg not on PATH"  >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "ERROR: ffprobe not on PATH" >&2; exit 1; }

echo "== 1/3 audio -> 16k mono =="
ffmpeg -nostdin -loglevel error -y -i "$VIDEO" -vn -ac 1 -ar 16000 "$WORK/audio.wav"
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/audio.wav" 2>/dev/null || echo "?")
echo "   audio.wav ok (${DUR}s)"
echo "$DUR" > "$WORK/duration_s.txt"   # so the watcher can size its wait

echo "== 2/3 frames -> fps=1/8 =="
# `|| true` so set -e doesn't abort if there is no video stream
HAS_VIDEO=0
ffprobe -v error -select_streams v -show_entries stream=index -of csv=p=0 "$VIDEO" | grep -q . && HAS_VIDEO=1 || true
if [ "$HAS_VIDEO" = 1 ]; then
  ffmpeg -nostdin -loglevel error -y -i "$VIDEO" -vf "fps=1/8" -q:v 3 "$WORK/frames/f_%05d.jpg"
  NF=$(find "$WORK/frames" -name 'f_*.jpg' | wc -l)
  echo "   $NF frames extracted"
else
  echo "   audio-only input — no video stream, 0 frames (screen-reading phase skipped)"
fi

echo "== 3/3 subtitle/caption track =="
# Prefer a sidecar .srt/.vtt next to the video; else demux a TEXT embedded stream.
SIDE=""
for ext in srt vtt; do
  cand="${VIDEO%.*}.$ext"
  [ -f "$cand" ] && SIDE="$cand" && break
done
if [ -n "$SIDE" ]; then
  cp "$SIDE" "$WORK/subs.${SIDE##*.}"
  echo "   sidecar subtitle copied: $SIDE"
else
  # only TEXT subtitle codecs can become SRT; bitmap (pgs/dvb/vobsub) cannot
  SCODEC=$(ffprobe -v error -select_streams s:0 -show_entries stream=codec_name -of csv=p=0 "$VIDEO" 2>/dev/null || echo "")
  if [ -z "$SCODEC" ]; then
    echo "   NO subtitle track found — caption diarization unavailable."
    echo "   (Request a captions export from the meeting tool, or run a local"
    echo "    diarizer e.g. pyannote on dev2. ASR + screen reading still proceed.)"
  elif echo "$SCODEC" | grep -qiE 'subrip|srt|ass|ssa|webvtt|mov_text|text'; then
    if ffmpeg -nostdin -loglevel error -y -i "$VIDEO" -map 0:s:0 -c:s srt "$WORK/subs.srt" && [ -s "$WORK/subs.srt" ]; then
      echo "   embedded text subtitle ($SCODEC) extracted -> subs.srt"
    else
      rm -f "$WORK/subs.srt"
      echo "   WARN: subtitle stream ($SCODEC) present but produced empty/failed srt — captions unavailable"
    fi
  else
    echo "   WARN: subtitle stream is bitmap codec ($SCODEC) — cannot convert to text; captions unavailable"
  fi
fi

echo "DONE -> $WORK"
