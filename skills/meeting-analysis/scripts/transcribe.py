"""Local multilingual ASR on GPU (no API keys). Run on dev2 (GPU box).

whisper-large-v3-turbo via transformers pipeline. Tuned to fit an 8 GB GPU
(RTX 5050): batch_size=1, fp16, chunked long-form, expandable_segments to dodge
fragmentation OOM. ~4x faster than large-v3 — and large-v3 itself OOMs at 8 GB,
so this script REFUSES a non-turbo large model on a <12 GB card (the hard-won
lesson, enforced in code, not just prose).

Writes transcript.json (full, chunk timestamps), transcript.txt (flat),
summary.json (duration / chunks / language / chars), and a `done`/`error`
marker so a watcher can tell completion from a crash. ALL failures — including
missing dependencies and no-GPU — write the `error` marker and exit non-zero,
so the watcher never silently hangs.

Usage:
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
      python3 transcribe.py <audio.wav> <out-dir> [model] [language] [--allow-cpu]

  model      default openai/whisper-large-v3-turbo
             (openai/whisper-large-v3 is REFUSED on <12 GB GPUs — use turbo)
  language   default auto-detect; pass e.g. "sk" to force Slovak (recommended
             when the language is known — auto-detect can mis-detect Slovak as
             Czech/Polish and drift mid-call)
  --allow-cpu  opt in to CPU (HOURS slower); without it, no-GPU is a hard error

Setup on dev2 (once):
    pip install --user 'transformers>=4.40' accelerate soundfile
    (torch with CUDA is already present: 2.10.0+cu128)
"""
import os
import sys
import time
import traceback

# must be set BEFORE torch is imported
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

ARGV = [a for a in sys.argv[1:] if a != "--allow-cpu"]
ALLOW_CPU = "--allow-cpu" in sys.argv
AUDIO = ARGV[0] if len(ARGV) > 0 else "audio.wav"
OUT = ARGV[1] if len(ARGV) > 1 else "."
MODEL = ARGV[2] if len(ARGV) > 2 else "openai/whisper-large-v3-turbo"
LANG = ARGV[3] if len(ARGV) > 3 else None


def main():
    t0 = time.time()
    # heavy imports INSIDE main() so a missing dependency writes the error marker
    import json

    import soundfile as sf
    import torch
    from transformers import pipeline

    use_cuda = torch.cuda.is_available()
    if not use_cuda and not ALLOW_CPU:
        raise SystemExit(
            "no CUDA GPU visible. Run on dev2 (10.77.8.134). CPU is HOURS slower; "
            "pass --allow-cpu only if you really mean it."
        )

    # enforce the OOM lesson: refuse a non-turbo large model on a small card
    if use_cuda:
        total_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        big = ("large" in MODEL) and ("turbo" not in MODEL)
        if big and total_gb < 12:
            raise SystemExit(
                f"{MODEL} needs >=12 GB; this GPU has {total_gb:.1f} GB and will OOM. "
                "Use openai/whisper-large-v3-turbo."
            )

    audio, sr = sf.read(AUDIO)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype("float32")
    dur = len(audio) / sr
    print(f"audio loaded: {dur:.1f}s sr={sr}", flush=True)

    asr = pipeline(
        "automatic-speech-recognition",
        model=MODEL,
        torch_dtype=torch.float16 if use_cuda else torch.float32,
        device="cuda" if use_cuda else "cpu",
        chunk_length_s=30,
        stride_length_s=5,
        return_timestamps=True,
        batch_size=1,
    )
    # fail fast if fp16 did not actually apply (old transformers ignoring the kwarg)
    if use_cuda:
        loaded = next(asr.model.parameters()).dtype
        if loaded != torch.float16:
            raise SystemExit(
                f"model loaded as {loaded}, not fp16 — it will OOM on 8 GB. "
                "Upgrade transformers (>=4.40) so the dtype kwarg is honored."
            )
    print(f"model {MODEL} loaded (lang={LANG or 'auto'}), transcribing...", flush=True)

    gen = {"task": "transcribe"}
    if LANG:
        gen["language"] = LANG
    res = asr({"raw": audio, "sampling_rate": sr}, generate_kwargs=gen)

    # transcript.json first — it's the irreplaceable artifact
    json.dump(res, open(os.path.join(OUT, "transcript.json"), "w"),
              ensure_ascii=False, indent=1)

    # flat txt + summary in their OWN try: a formatting slip must NOT discard a
    # successful 15-20 min transcription.
    chunks = res.get("chunks", [])
    try:
        with open(os.path.join(OUT, "transcript.txt"), "w") as f:
            for c in chunks:
                ts = c.get("timestamp") or (None, None)
                f.write(f"[{ts[0]}-{ts[1]}] {c.get('text', '').strip()}\n")
        chars = len(res.get("text", "") or "".join(c.get("text", "") for c in chunks))
        json.dump(
            {"duration_s": round(dur, 1), "chunks": len(chunks),
             "language": LANG or "auto", "chars": chars, "model": MODEL},
            open(os.path.join(OUT, "summary.json"), "w"), ensure_ascii=False, indent=1)
        if len(chunks) == 0 or chars < 50:
            print("WARN: transcript is empty/near-empty — check audio + language",
                  flush=True)
    except Exception:
        traceback.print_exc()  # logged, but transcript.json stands

    print(f"DONE in {time.time()-t0:.0f}s, chunks={len(chunks)}", flush=True)
    open(os.path.join(OUT, "done"), "w").write("ok")


try:
    main()
except Exception as e:
    traceback.print_exc()
    try:
        open(os.path.join(OUT, "error"), "w").write(repr(e))
    except Exception:
        pass
    raise
