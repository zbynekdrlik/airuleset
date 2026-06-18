---
name: meeting-analysis
description: Analyze a meeting/call recording MULTIMODALLY — local GPU transcription + screenshare-frame reading + caption-track diarization — into a complete, structured understanding (requirements, decisions, tickets, summary). Use whenever the user hands you a recorded call/meeting (video or audio) and wants it analyzed, especially screen-shared sales/product/requirements calls. No paid API keys needed; runs on the dev2 GPU.
user-invocable: true
---

# Multimodal Meeting Analysis

> Turn a recorded meeting into a complete, structured understanding. The recording has
> THREE information channels — **spoken words**, **who said them**, and **what was shown on
> screen**. Analyzing only the audio (the common failure) throws away half the meeting:
> screenshared documents, ERP screens, mockups, and numbers are where the real requirements
> live. This skill processes all three channels locally on the dev2 GPU (no API keys) and
> ends with a completeness pass so nothing is missed.

## The hard rules (the failures this skill exists to prevent)

1. **NEVER audio-only.** If the meeting had screensharing, you MUST read the screens. A
   transcript without the shown documents/screens is an incomplete analysis. This is the #1
   past failure ("you just listened to the audio and ignored what we showed you").
2. **YOU read every distinct screen yourself** (you have vision via the Read tool). Do NOT
   ask the user to describe what was on screen. Do NOT skip frames.
3. **A shown document is a PRIMARY source — transcribe it VERBATIM.** When a requirements
   doc, spec, quote, invoice, or any text document is shown on screen, transcribe its exact
   text into a `*_verbatim.md`. In the original failure the shown requirements doc WAS the
   meeting's substance; never reduce a shown spec to a one-line summary. Same weight as rule 1.
4. **Completeness is priority #1.** End with an adversarial completeness critic (Phase 5).
   When unsure whether something is a requirement, capture it — over-capture, never drop.
5. **Local + key-free.** Transcription runs on the dev2 GPU. Do NOT reach for a paid API
   (Soniox/AssemblyAI/Gemini) when the local pipeline works. If the user *lists* paid options,
   that is them informing you what's available — they want YOU to pick, not to ask which.
6. **Diarization (who spoke) is free** — from the meeting tool's caption/subtitle track, not a
   paid diarizer. The caption *text* is low quality (that's why we run real ASR), but its
   speaker labels + timings are gold.

## Setup & variables

- **Skill scripts dir** (also printed as "Base directory for this skill" when the skill loads):
  `SKILL=/home/newlevel/devel/airuleset/skills/meeting-analysis`
- **Per-meeting work dir** — pick a concrete topic, e.g. `WORK=/home/newlevel/uploads/acme-call/work`
- **IMPORTANT:** each Bash tool call is a FRESH shell — env vars do NOT persist between calls.
  Re-export `SKILL=...` and `WORK=...` at the top of EVERY phase's bash block (shown below), or
  use absolute paths.

Machines: **dev1** (`develbox`, 100.104.8.125) = orchestration, ffmpeg, frame dedup, and where YOU
read the screens — the recording lands here. **dev2** (`baking-ai-5060`, 100.82.64.27, RTX 5050
8 GB) = the GPU ASR; SSH `ssh newlevel@100.82.64.27`. The GPU is SHARED with production inference
— check free memory and never kill another process's GPU memory.

## Phase 0 — Receive the recording (user is remote, no shared filesystem)

The user is on a laptop over VPN/SSH with NO filesystem access to the dev boxes, and airuleset's
filedrop is download-only. Do NOT ask them to `scp` (they've asked repeatedly not to). Stand up
the bundled push endpoint, verify it's live, give them the URL:

```bash
SKILL=/home/newlevel/devel/airuleset/skills/meeting-analysis
WORK=/home/newlevel/uploads/acme-call/work          # <- pick a real topic
TOK=$(openssl rand -hex 8); PORT=8799; IP=100.104.8.125 # IP = the address the user reaches dev1 on
mkdir -p "$WORK"
setsid nohup python3 "$SKILL/scripts/upload_server.py" "$TOK" "$PORT" "$IP" \
       "$(dirname "$WORK")" >/tmp/upload.log 2>&1 < /dev/null &
echo "server PID $!"
sleep 1
curl -s -o /dev/null -w "liveness: %{http_code}\n" "http://$IP:$PORT/$TOK/"   # must be 200
echo "GIVE THE USER:  http://$IP:$PORT/$TOK/"
```

- The advertised `IP` must be the address the remote user's VPN actually routes to dev1 (they
  may reach it on a Tailscale/other IP, not 100.104.8.125). A local 200 does NOT prove the user can
  reach it — confirm their path. If port 8799 is firewalled from their network, pick another.
- After the user drops the file, read the real saved path (the filename is sanitized — spaces /
  accents / parens become `_`, so you cannot guess it): `grep SAVED /tmp/upload.log` →
  `/home/newlevel/uploads/acme-call/<sanitized-name>`. Confirm the byte count matches the user's
  file size, then stop the server: `kill <PID>`.
- If the recording already lives on a dev box, skip this phase.

## Phase 1 — Extract the three channels (dev1, ffmpeg)

```bash
SKILL=/home/newlevel/devel/airuleset/skills/meeting-analysis
WORK=/home/newlevel/uploads/acme-call/work
ls -la "$(dirname "$WORK")"                          # resolve the real uploaded filename
VIDEO=/home/newlevel/uploads/acme-call/<sanitized-name>   # from the ls above / upload.log
bash "$SKILL/scripts/extract.sh" "$VIDEO" "$WORK"
```

Produces `$WORK/audio.wav` (16 kHz mono), `$WORK/frames/f_*.jpg` (1 frame / 8 s — **video
input only**), `$WORK/duration_s.txt`, and `$WORK/subs.srt` if a TEXT caption track is present
(sidecar `.srt`/`.vtt`, else a demuxed text stream; bitmap subs like PGS are reported as
unavailable). **Audio-only input is valid** — it yields 0 frames; Phases 3-frames/4 are then
skipped and analysis runs on transcript + captions. No caption track → diarization is
unavailable (request a captions export, or run a local diarizer); ASR + screen reading proceed.

## Phase 2 — Transcribe on the dev2 GPU (preflight, detached, non-blocking watch)

ASR takes ~15–20 min for a ~1 h call. Do NOT block the session; launch detached and poll with a
**background, crash-aware** watch.

```bash
SKILL=/home/newlevel/devel/airuleset/skills/meeting-analysis
WORK=/home/newlevel/uploads/acme-call/work
# 1. preflight deps on dev2 (one-time; missing deps otherwise crash the run)
ssh newlevel@100.82.64.27 'python3 -c "import torch,transformers,accelerate,soundfile" 2>/dev/null \
  || pip install --user "transformers>=4.40" accelerate soundfile'
# 2. free-GPU gate — turbo+batch1+fp16 needs ~4 GB free; do NOT launch into a full card
ssh newlevel@100.82.64.27 'nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader'
# (read memory.free; if < ~4000 MiB, STOP — the shared card is busy, report contention, do not launch)
# 3. ship audio + script, launch detached
ssh newlevel@100.82.64.27 'mkdir -p ~/asr'
rsync -a "$WORK/audio.wav" "$SKILL/scripts/transcribe.py" newlevel@100.82.64.27:~/asr/
ssh newlevel@100.82.64.27 'cd ~/asr && rm -f done error && \
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  setsid nohup python3 transcribe.py audio.wav . openai/whisper-large-v3-turbo sk \
  >asr.log 2>&1 < /dev/null & echo launched'
```

- **Pass the language** (`sk` above) when known — auto-detect can mis-read Slovak as Czech/Polish
  and drift mid-call. Drop the 4th arg only when the language is genuinely unknown.
- transcribe.py enforces the hard-won lessons in code: missing deps / no-GPU / a non-turbo large
  model on this 8 GB card all write the `error` marker and exit (never a silent hang); it asserts
  fp16 actually loaded (else it would OOM).

**Watch — non-blocking, scaled to the recording length, with a HANG branch.** Use the Bash tool
with `run_in_background: true` (per ci-monitoring), polling until a terminal marker; size the cap
to ~2× expected runtime (≈ minutes ≈ audio-minutes), not a fixed 40:

```bash
ssh newlevel@100.82.64.27 'for i in $(seq 1 90); do
   [ -f ~/asr/done ]  && { echo DONE;  cat ~/asr/summary.json 2>/dev/null; tail -1 ~/asr/asr.log; exit 0; }
   [ -f ~/asr/error ] && { echo ERROR; tail -8 ~/asr/asr.log; exit 0; }
   pgrep -f transcribe.py >/dev/null || { echo PROCESS_GONE; tail -8 ~/asr/asr.log; exit 0; }
   sleep 60
 done; echo "STILL_RUNNING_AFTER_90MIN"; nvidia-smi --query-gpu=memory.used --format=csv,noheader'
```

Branch on the result: `DONE` → pull results (`rsync -a newlevel@100.82.64.27:~/asr/transcript.json
newlevel@100.82.64.27:~/asr/transcript.txt newlevel@100.82.64.27:~/asr/summary.json "$WORK/"`).
`ERROR` → read asr.log, fix the root cause (do NOT just retry). `PROCESS_GONE` with no marker →
a dependency/import died — check asr.log. `STILL_RUNNING_AFTER_90MIN` while `pgrep` still finds it
→ it is STILL RUNNING (long recording or GPU contention), NOT dead — re-enter the watch; if the
process is alive but the log is frozen and the shared GPU is starved, that's a hang → investigate
contention, don't assume success.

## Phase 3 — Fuse: dedup screens + speaker turns (dev1)

```bash
SKILL=/home/newlevel/devel/airuleset/skills/meeting-analysis
WORK=/home/newlevel/uploads/acme-call/work
python3 -c 'import PIL' 2>/dev/null || pip install --user Pillow   # dev1 prereq
python3 "$SKILL/scripts/prep.py" "$WORK"
```

Produces `$WORK/frames_kept/scr_NNN_tSSSSS.jpg` (one per DISTINCT screen) and
`$WORK/speaker_turns.json`. **Calibration (proven run): 413 raw frames → 57 distinct screens at
threshold 10.** If prep.py warns that it kept dozens-to-hundreds of "screens", dedup
under-collapsed (scrolling/video/cursor/animated UI) — re-run with a higher threshold
(`prep.py "$WORK" 8 16`) before reading, so the target stays "dozens", not "hundreds". If
speakers come back `["?"]`, prep.py prints the first caption lines — identify that tool's
speaker-label format, add a regex to `SPK_PATTERNS`, re-run. `t_sec` is approximate (±8 s); use
it for loose screen↔speech correlation, not exact alignment (prep.py warns on frame drift).

## Phase 4 — READ every distinct screen yourself (the channel audio misses)

For EACH file in `frames_kept/`, use the Read tool to look at it and record what's on it —
**field by field** for ERP/app screens, **verbatim** (Hard Rule 3) for shown documents:

- Read every `frames_kept/scr_*.jpg` (Read renders the image). Don't sample — dedup already cut
  it to a readable set (dozens). If it's hundreds, the dedup threshold needs raising (Phase 3),
  not sampling.
- Write a `screen_inventory.md`: per screen — what system/screen it is, every field / column /
  value / status / menu / button visible, and the capability it implies.
- A shown document/spec/quote → transcribe its **exact text** into `*_verbatim.md` (Hard Rule 3).
- Correlate with `transcript.txt` + `speaker_turns.json`: when a screen is on (~its t_sec ±8 s),
  what is the speaker saying about it? That pairing is where requirements crystallize.

## Phase 5 — Synthesize, then adversarially critique for completeness

Combine all artifacts — `transcript.txt`, `speaker_turns.json`, `screen_inventory.md`, any
`*_verbatim.md` — into the deliverable the user asked for (tickets, spec, decision log, summary).
Then run a **completeness critic** before declaring done:

- Large/important synthesis (e.g. "turn this into tickets") + ultracode on / user asks → use the
  **Workflow** tool: fan out parallel readers over transcript segments + screen groups, then a
  critic agent whose only job is "what requirement, screen, number, or shown document is NOT
  represented in the output?". Loop until the critic comes back dry.
- Smaller synthesis → inline critic pass: re-read each screen + speaker turn and tick off where
  it landed in the output. Anything unticked is a gap — fix it.
- Capture EVERYTHING identified-but-not-done as tracked items (e.g. GitHub issues) — never drop a
  requirement silently (`no-dropped-work.md`).

## Deliver files back as clickable LAN URLs

Any artifact the user should open goes back as a clickable link, never a `/tmp` path
(`deliver-files-as-urls.md`): `python3 ~/devel/airuleset/airuleset.py share "$WORK/screen_inventory.md"`.

## Anti-patterns (all rewordings apply)

- Transcribing the audio and calling it analyzed while screensharing happened → **WRONG.** Read
  the screens (Phase 4).
- Summarizing a shown requirements doc into a line instead of transcribing it verbatim → **WRONG**
  (Hard Rule 3).
- Asking the user "what was on the screen / describe the document you shared?" → **WRONG.** You
  have vision; read the frames.
- Reaching for a paid ASR/diarization API because the user mentioned one → **WRONG.** Local dev2
  pipeline is the default.
- Blocking the session polling the GPU, or a fixed 40-min cap that calls a long run "dead" →
  **WRONG.** Background, crash-aware, duration-scaled watch with a HANG branch (Phase 2).
- Launching ASR into a near-full shared GPU, or killing another process's GPU memory → **WRONG.**
  Gate on free memory; never kill prod inference.
- Declaring complete without the Phase 5 completeness critic → **WRONG.** That's the exact
  "looked done but dropped half" failure.

The intent: every meeting is analyzed across all three channels (words + speakers + screen),
locally and key-free, ending with a completeness pass — so the user never re-explains the method
and never loses requirements that were shown but not said.
