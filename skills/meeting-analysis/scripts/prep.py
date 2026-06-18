"""Prep for screen+speaker fusion (stdlib + Pillow only). Run on dev1.

1. Dedup the keyframes via dhash -> keep one representative per distinct screen,
   preserving temporal order. Copies kept frames to frames_kept/ named
   scr_<seq>_t<sec>.jpg and writes frames_manifest.json. This turns the raw
   frames into a small set of DISTINCT screens you actually read. (Reference:
   the original run collapsed 413 raw frames -> 57 distinct screens at threshold 10.)
2. Parse subs.srt / .vtt (meeting captions) -> speaker_turns.json: merged
   contiguous (start,end,speaker) turns. The caption TEXT is usually low quality
   (that's why we run real ASR) — we keep ONLY speaker+timing as a free
   diarization track.

NOTE: t_sec is approximate (frame_index * STEP). It assumes ~one frame per STEP
seconds; use it for ±STEP correlation with the transcript, not exact alignment.
A drift check vs audio.wav duration warns if extraction dropped frames.

Setup on dev1 (once):  python3 -c 'import PIL' || pip install --user Pillow

Usage: python3 prep.py <work-dir> [seconds-per-frame=8] [dhash-hamming-threshold=10]
"""
import json
import re
import shutil
import sys
import wave
from pathlib import Path

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True  # tolerate a truncated final JPEG

WORK = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 8   # seconds per extracted frame
HAM_THRESHOLD = int(sys.argv[3]) if len(sys.argv) > 3 else 10  # >this => "new screen"
FRAMES = WORK / "frames"
KEPT = WORK / "frames_kept"
KEPT.mkdir(parents=True, exist_ok=True)


def dhash(path, hsz=8):
    with Image.open(path) as img:
        img = img.convert("L").resize((hsz + 1, hsz), Image.LANCZOS)
        px = list(img.getdata())
    bits = 0
    for r in range(hsz):
        base = r * (hsz + 1)
        for c in range(hsz):
            bits = (bits << 1) | (1 if px[base + c] > px[base + c + 1] else 0)
    return bits


def ham(a, b):
    return bin(a ^ b).count("1")


# --- 1. dedup frames ---
if not FRAMES.is_dir():
    sys.exit(f"ERROR: no frames dir {FRAMES} — did extract.sh run? (audio-only input has none)")
frames = sorted(FRAMES.glob("f_*.jpg"))
if not frames:
    print("no frames found (audio-only input) -> skipping screen dedup")
else:
    manifest = []
    last_kept_hash = None
    seq = 0
    for i, fp in enumerate(frames):
        t = i * STEP
        try:
            h = dhash(fp)
        except Exception as e:
            print(f"  WARN: unreadable frame {fp.name} ({e}) — kept as distinct")
            h = None
        kept = h is None or last_kept_hash is None or ham(h, last_kept_hash) > HAM_THRESHOLD
        rec = {"frame": fp.name, "t_sec": t, "hash": h, "kept": kept}
        if kept:
            dst = KEPT / f"scr_{seq:03d}_t{t:05d}.jpg"
            shutil.copy(fp, dst)
            rec["kept_name"] = dst.name
            seq += 1
            if h is not None:
                last_kept_hash = h
        manifest.append(rec)
    json.dump(manifest, open(WORK / "frames_manifest.json", "w"), indent=1)
    print(f"frames: {len(frames)} extracted -> {seq} distinct screens kept (frames_kept/)")

    # dedup-blowup warning: scrolling/video/cursor content defeats dhash
    if seq > 80 or seq > 0.30 * len(frames):
        print(f"  WARN: {seq} 'screens' is a lot — dedup likely under-collapsed "
              f"(scrolling/video/animated UI). Re-run with a higher threshold, e.g. "
              f"`prep.py {WORK} {STEP} {HAM_THRESHOLD + 6}`, before reading every frame.")

    # timestamp drift check vs real audio duration
    awav = WORK / "audio.wav"
    if awav.exists():
        try:
            with wave.open(str(awav), "rb") as w:
                real = w.getnframes() / float(w.getframerate())
            approx = len(frames) * STEP
            if abs(approx - real) > 2 * STEP:
                print(f"  WARN: frame timeline ({approx}s) drifts from audio ({real:.0f}s) "
                      f"by >{2*STEP}s — t_sec values are unreliable; treat screen↔speech "
                      f"correlation as loose.")
        except Exception:
            pass

# --- 2. parse subs -> speaker turns (srt or vtt) ---
sub_path = None
for name in ("subs.srt", "subs.vtt"):
    if (WORK / name).exists():
        sub_path = WORK / name
        break

if sub_path is None:
    print("no subs.srt/.vtt found -> skipping diarization (speaker_turns.json not written)")
    sys.exit(0)

raw_bytes = sub_path.read_bytes()
for enc in ("utf-8", "cp1250", "latin-1"):  # SK captions are often cp1250
    try:
        srt = raw_bytes.decode(enc)
        break
    except UnicodeDecodeError:
        continue
else:
    srt = raw_bytes.decode("utf-8", errors="replace")

TS = r"(?:(\d+):)?(\d\d):(\d\d)[.,](\d+)"           # srt 00:00:00,000 / vtt 00:00:00.000
TS_LINE = re.compile(rf"{TS}\s*-->\s*{TS}")
SPK_PATTERNS = [
    re.compile(r"^\((.+?)\)\s*$"),                  # (Peter)
    re.compile(r"<v\s+([^>]+)>"),                   # <v Peter>
    re.compile(r"^([A-Za-z][\w .'\-]{1,30}):\s"),   # Peter:  (filtered by frequency below)
]


def to_sec(g):
    hh, mm, ss, ms = g
    return int(hh or 0) * 3600 + int(mm) * 60 + int(ss) + int(ms) / (10 ** len(ms))


blocks = re.split(r"\n\s*\n", srt.strip())
raw = []
sample_lines = []
for b in blocks:
    lines = [x for x in b.splitlines() if x.strip()]
    if len(lines) < 2:
        continue
    tm = TS_LINE.search(b)
    if not tm:
        continue
    start, end = to_sec(tm.groups()[:4]), to_sec(tm.groups()[4:])
    # text lines = everything after the timestamp line (skip a leading numeric index)
    txt = [ln.strip() for ln in lines if not TS_LINE.search(ln) and not ln.strip().isdigit()]
    first = txt[0] if txt else ""
    if len(sample_lines) < 6 and first:
        sample_lines.append(first)
    spk = None
    for pat in SPK_PATTERNS:          # ONLY the first text line, to avoid body-text colons
        m = pat.search(first)
        if m:
            spk = m.group(1).strip()
            break
    raw.append((start, end, spk or "?"))

# frequency filter: a real speaker recurs; one-off matches ("Note:", "TODO:") are phantoms
counts = {}
for _, _, s in raw:
    counts[s] = counts.get(s, 0) + 1
valid = {s for s, c in counts.items() if c >= 2 and s != "?"}
raw = [(st, en, s if s in valid else "?") for st, en, s in raw]

# merge consecutive same-speaker blocks into turns (<=8s gap = same turn)
turns = []
for st, en, spk in raw:
    if turns and turns[-1]["speaker"] == spk and st - turns[-1]["end"] <= 8:
        turns[-1]["end"] = en
    else:
        turns.append({"start": st, "end": en, "speaker": spk})
json.dump(turns, open(WORK / "speaker_turns.json", "w"), ensure_ascii=False, indent=1)
spks = sorted(valid) or ["?"]
print(f"speaker turns: {len(turns)} merged; speakers={spks}")
if spks == ["?"]:
    print("  WARN: no speaker labels matched. First caption lines look like:")
    for ln in sample_lines:
        print(f"    | {ln[:80]}")
    print("  -> identify the label format and add a regex to SPK_PATTERNS, then re-run.")
