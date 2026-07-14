---
name: meeting-analysis
description: Analyze a meeting/call recording MULTIMODALLY — Soniox stt-async-v5 transcription with native speaker diarization + screenshare-frame reading — into a complete, structured understanding (requirements, decisions, tickets, summary). Use whenever the user hands you a recorded call/meeting (video or audio) and wants it analyzed, especially screen-shared sales/product/requirements calls. Local dev2-GPU whisper is the fallback. The method SELF-IMPROVES after every run (Phase 6).
user-invocable: true
---

# Multimodal Meeting Analysis

> Turn a recorded meeting into a complete, structured understanding. The recording has
> THREE information channels — **spoken words**, **who said them**, and **what was shown on
> screen**. Analyzing only the audio (the common failure) throws away half the meeting:
> screenshared documents, ERP screens, mockups, and numbers are where the real requirements
> live. This skill transcribes with **Soniox stt-async-v5** (materially better Slovak +
> **native speaker diarization**, so "who spoke" needs no caption track; local dev2-GPU
> whisper is the fallback), reads every shared screen itself, ends with a completeness pass
> so nothing is missed, and — Phase 6 — **improves its own method after every run**.

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
5. **Transcription = Soniox stt-async-v5 (PRIMARY); YOU pick, never ask which.** Whisper twice
   left the user unhappy — it garbles Slovak business terms and needs a caption track for "who
   spoke". For a requirement-bearing Slovak meeting, transcribe with **Soniox stt-async-v5**
   (`scripts/transcribe_soniox.py`): far better Slovak, **native speaker diarization**, and it's
   a cloud API so it runs from **dev1 with NO dev2 GPU contention**. Local whisper on dev2
   (`transcribe.py`) is the FALLBACK — use it only when the Soniox key/network is unavailable.
   When the user *lists* options (Soniox/AssemblyAI/Gemini), that is them saying what's available
   — they want YOU to pick the best (Soniox here), not to ask which. **Verify the newest async
   model** at `GET https://api.soniox.com/v1/models` (Bearer key) and track it — as of 2026-07
   it is `stt-async-v5`. **Never reuse the voiceagent BAKERY Soniox context** for a non-bakery
   meeting — it biases the transcript toward bread terms; the montalu/ERP context ships inside
   `transcribe_soniox.py`.
6. **Diarization is native, not caption-derived.** Soniox stt-async-v5 labels speakers itself
   (`speaker_turns.json` comes straight out of `transcribe_soniox.py`) — no caption/subtitle
   track required. The caption track is only a fallback speaker source for the local-whisper
   path; when you fall back to whisper, its caption speaker labels + timings are still gold.

## Setup & variables

- **Skill scripts dir** (also printed as "Base directory for this skill" when the skill loads):
  `SKILL=$HOME/devel/airuleset/skills/meeting-analysis`
- **Per-meeting work dir** — pick a concrete topic, e.g. `WORK=$HOME/uploads/acme-call/work`
- **IMPORTANT:** each Bash tool call is a FRESH shell — env vars do NOT persist between calls.
  Re-export `SKILL=...` and `WORK=...` at the top of EVERY phase's bash block (shown below), or
  use absolute paths.

Machines: **dev1** (hostname `dev1`, 100.104.8.125) = orchestration, ffmpeg, frame dedup, and where YOU
read the screens — the recording lands here. **dev2** (hostname `dev2`, 100.82.64.27, RTX 5050
8 GB) = the GPU ASR; SSH `ssh newlevel@100.82.64.27`. The GPU is SHARED with production inference
— check free memory and never kill another process's GPU memory.

## Phase 0 — Receive the recording (user is remote, no shared filesystem)

The user is on a laptop over VPN/SSH with NO filesystem access to the dev boxes, and airuleset's
filedrop is download-only. Do NOT ask them to `scp` (they've asked repeatedly not to). Stand up
the bundled push endpoint, verify it's live, give them the URL:

```bash
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=$HOME/uploads/acme-call/work          # <- pick a real topic
mkdir -p "$WORK"
python3 ~/devel/airuleset/airuleset.py upload --dir "$(dirname "$WORK")" --ttl 14400
# (the upload server is the shared filedrop/upload_server.py — CLI spawns it detached,
#  prints the live-checked URL; log = /tmp/airuleset-upload-<port>.log)
curl -s -o /dev/null -w "liveness: %{http_code}\n" "http://$IP:$PORT/$TOK/"   # must be 200
echo "GIVE THE USER:  http://$IP:$PORT/$TOK/"
```

- The advertised `IP` must be the address the remote user's VPN actually routes to dev1 (they
  may reach it on a Tailscale/other IP, not 100.104.8.125). A local 200 does NOT prove the user can
  reach it — confirm their path. If port 8799 is firewalled from their network, pick another.
- After the user drops the file, read the real saved path (the filename is sanitized — spaces /
  accents / parens become `_`, so you cannot guess it): `grep SAVED /tmp/airuleset-upload-<port>.log` →
  `$HOME/uploads/acme-call/<sanitized-name>`. Confirm the byte count matches the user's
  file size, then stop the server: `kill <PID>`.
- If the recording already lives on a dev box, skip this phase.

## Phase 1 — Extract the three channels (dev1, ffmpeg)

```bash
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=$HOME/uploads/acme-call/work
ls -la "$(dirname "$WORK")"                          # resolve the real uploaded filename
VIDEO=$HOME/uploads/acme-call/<sanitized-name>   # from the ls above / upload.log
bash "$SKILL/scripts/extract.sh" "$VIDEO" "$WORK"
```

Produces `$WORK/audio.wav` (16 kHz mono), `$WORK/frames/f_*.jpg` (1 frame / 8 s — **video
input only**), `$WORK/duration_s.txt`, and `$WORK/subs.srt` if a TEXT caption track is present
(sidecar `.srt`/`.vtt`, else a demuxed text stream; bitmap subs like PGS are reported as
unavailable). **Audio-only input is valid** — it yields 0 frames; Phases 3-frames/4 are then
skipped and analysis runs on transcript + captions. No caption track → diarization is
unavailable (request a captions export, or run a local diarizer); ASR + screen reading proceed.

## Phase 2 (PRIMARY) — Transcribe with Soniox stt-async-v5 (dev1, native diarization)

Runs from dev1, no GPU. Soniox does the Slovak transcription AND the speaker labels, so this
one step produces both `transcript.txt` and `speaker_turns.json`.

```bash
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=$HOME/uploads/acme-call/work
# key sources, first hit wins: env already set → per-user local secret (isolated boxes,
# e.g. montalu — provisioned outside git) → the maintainer box's voiceagent .env
export SONIOX_API_KEY=${SONIOX_API_KEY:-$(grep -hoE 'SONIOX_API_KEY=[^[:space:]]+' "$HOME/.claude/secrets/soniox.env" /home/newlevel/devel/voiceagent/.env 2>/dev/null | head -1 | cut -d= -f2)}
# sanity: newest async model still stt-async-v5?  (verify + track — user's standing instruction)
curl -s https://api.soniox.com/v1/models -H "Authorization: Bearer $SONIOX_API_KEY" | grep -o 'stt-async-v[0-9]*' | sort -u | tail -1
# launch detached + crash-aware watch (a ~1 h call is a few min of Soniox wall-clock)
cd "$WORK" && rm -f done error && \
  setsid nohup python3 "$SKILL/scripts/transcribe_soniox.py" audio.wav "$WORK" sk erp \
  >soniox.log 2>&1 < /dev/null & echo "launched pid $!"
```

Watch (background, crash-aware — same shape as the fallback below), then read `summary.json`:

```bash
WORK=$HOME/uploads/acme-call/work
for i in $(seq 1 60); do
  [ -f "$WORK/done" ]  && { echo DONE;  cat "$WORK/summary.json"; break; }
  [ -f "$WORK/error" ] && { echo ERROR; cat "$WORK/error"; tail -5 "$WORK/soniox.log"; break; }
  pgrep -f transcribe_soniox.py >/dev/null || { echo PROCESS_GONE; tail -8 "$WORK/soniox.log"; break; }
  sleep 30
done
```

- `DONE` → `transcript.txt`, `transcript.json`, `speaker_turns.json`, `summary.json` are in
  `$WORK`. **Skip the Phase 3 caption-diarization half** — Soniox already produced
  `speaker_turns.json`; Phase 3 then only dedups screens.
- `ERROR` → read `$WORK/error` + `soniox.log`, fix the root cause. Only if Soniox is genuinely
  unavailable (key/network/account) fall back to the dev2-GPU whisper path below.
- The script is resilient: if the API rejects the diarization flag or the ERP context it retries
  without them (logged as `WARN`) rather than hanging — a slightly poorer transcript beats none.

## Phase 2 (FALLBACK) — Transcribe on the dev2 GPU with whisper (only if Soniox is down)

Use ONLY when Soniox is unavailable. ASR takes ~15–20 min for a ~1 h call. Do NOT block the
session; launch detached and poll with a **background, crash-aware** watch. Diarization then
comes from the caption track (Phase 3), not the model.

```bash
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=$HOME/uploads/acme-call/work
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
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=$HOME/uploads/acme-call/work
python3 -c 'import PIL' 2>/dev/null || pip install --user Pillow   # dev1 prereq
# Soniox path already wrote the authoritative speaker_turns.json — preserve it across prep.py
[ -f "$WORK/speaker_turns.json" ] && cp "$WORK/speaker_turns.json" "$WORK/speaker_turns.soniox.json"
python3 "$SKILL/scripts/prep.py" "$WORK"
[ -f "$WORK/speaker_turns.soniox.json" ] && mv "$WORK/speaker_turns.soniox.json" "$WORK/speaker_turns.json"
```

**prep.py rewrites `speaker_turns.json` from the CAPTION track** — that is only the whisper-path
fallback. When you used the Soniox path, its native `speaker_turns.json` is authoritative, so the
`cp`/`mv` above backs it up and restores it after prep.py's screen dedup. On the whisper fallback
path there is no Soniox file, so prep.py's caption-derived turns stand.

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

**Every generated ticket MUST cite its evidence** — the transcript timestamp + speaker, the
screen file (`frames_kept/scr_*.jpg`), and/or the `*_verbatim.md` line it came from. A ticket
with no citation is an unverifiable hallucinated requirement; the user must be able to trace each
one back to the moment in the call it came from.

**Delivered-vs-broken cross-check (mandatory when the meeting is a complaint call).** When the
meeting exists because the user/stakeholder says previously-"delivered" things are non-functional,
unclear, or wrong (the recurring montalu/Peto pattern), the synthesis MUST explicitly reconcile
the call against what was already claimed done:

- For each complaint in the call, find the ticket/PR that claimed to deliver it
  (`gh issue list --state closed`, `gh pr list --state merged`, the project memory). File a
  **regression/bug ticket** that names the original claim, quotes the call's evidence that it is
  broken, and states the expected behavior. Do NOT re-file it as a fresh feature — link the origin.
- **VERIFY THE DEPLOYED VERSION the complainer actually ran — before calling anything a regression.**
  A merged PR is NOT the same as a deployed feature. Establish the git SHA / release the tested
  environment was running (e.g. `ssh <host> 'git -C <addon> rev-parse HEAD'` / a version label /
  container uptime) and compare it against each candidate PR's **merge date**. A "delivered but
  broken" complaint is very often just a **stale test/deploy environment** — the fix is a deploy /
  env refresh, NOT a new dev bug. Filing already-done work as a fresh regression is the exact
  wasted-cycle trap the user is burned by. Split the output into *already-fixed-pending-deploy*
  (file/flag a deploy-and-reverify item) vs *genuinely-not-done*. (Live example: half a cutover
  call's "regressions" were a 4-day-stale test env; the type-to-filter fix was merged to `develop`
  but not deployed — filing it as a new bug would have been dead work.)
- An item the stakeholder found **"unintelligible / unclear / can't tell what it does"** is a real
  finding, not noise → file a **clarity/UX ticket** (labelling, wording, findability — the montalu
  "Peter must FIND it and know the flow" acceptance bar), never silently drop it.
- Separate **NEW requirements** (never promised) from **REGRESSIONS** (promised, now broken) in the
  output — they are different work and the user reads them differently.

## Phase 6 — Self-improve the method (run EVERY time, before declaring done)

**The user's standing instruction: each analysis must be higher-quality than the last, using a
more functional approach — the skill develops ITSELF.** After delivering, run a short retrospective
on the METHOD (not the content), then bank the improvement so the next run inherits it:

1. Ask: what did THIS run reveal was weak, slow, brittle, or missing in the *method*? (ASR quality
   on this audio, diarization accuracy, screen-dedup threshold, a channel that got under-read, a
   step that hung, a manual fix-up you had to improvise, a better tool that would have helped.)
2. Turn each concrete lesson into an **edit of this SKILL.md and/or its scripts** — tighten a
   threshold, add a preflight, fix a fragile command, adjust the ASR/context choice, add an
   anti-pattern. Then **commit** it (airuleset is a git repo):
   `cd ~/devel/airuleset && git add skills/meeting-analysis && git commit -m "meeting-analysis: <lesson> (self-improve after <topic> run)"`.
   On a box whose airuleset checkout can't push (isolated sub-dev users like montalu), do NOT
   commit — put the concrete lesson into the completion report's `🔧 Self-improve:` line so the
   maintainer session applies it to the repo.
3. If the lesson is project-state (not method) — e.g. a montalu-specific gotcha — write it to
   session memory instead, and cross-link.
4. The completion report MUST end with a one-line `🔧 Self-improve:` note stating what changed in
   the method (or, rarely, "method held up — no change needed" with the reason).

Skipping Phase 6 is the banned "ran it the same flawed way again" failure — the whole point is that
the method never stops improving.

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
- Defaulting to local whisper for a Slovak requirement-bearing meeting → **WRONG.** Soniox
  stt-async-v5 is the primary (better Slovak + native diarization); whisper is the fallback.
- ASKING the user which ASR to use when they listed options → **WRONG.** They want YOU to pick
  (Soniox); asking is the banned over-ask.
- Reusing the voiceagent BAKERY Soniox context for a non-bakery meeting → **WRONG.** It biases the
  transcript toward bread terms; use the ERP context in `transcribe_soniox.py` (or none).
- Blocking the session polling ASR, or a fixed cap that calls a long run "dead" → **WRONG.**
  Background, crash-aware, duration-scaled watch with a HANG branch (Phase 2).
- On the whisper fallback: launching ASR into a near-full shared GPU, or killing another process's
  GPU memory → **WRONG.** Gate on free memory; never kill prod inference.
- Declaring complete without the Phase 5 completeness critic → **WRONG.** That's the exact
  "looked done but dropped half" failure.
- Declaring done without the Phase 6 self-improvement pass → **WRONG.** The method must improve
  every run; skipping it repeats the same flaws.

The intent: every meeting is analyzed across all three channels (words + speakers + screen) with
the BEST transcription available (Soniox stt-async-v5, native diarization), ending with a
completeness pass AND a self-improvement pass — so the user never re-explains the method, never
loses requirements that were shown but not said, and every run is better than the last.
