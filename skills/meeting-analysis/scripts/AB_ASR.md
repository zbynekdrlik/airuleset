# ASR provider A/B: operator guide (#1155)

This guide compares the cloud transcription providers on OUR audio: Slovak,
ERP codes, 3–5 speakers. The pipeline does not care which provider produced a
transcript, because every adapter writes the same files (`asr_contract.py`):

| Adapter | Provider / model | Key (environment variable) |
|---|---|---|
| `transcribe_soniox.py` | Soniox `stt-async-v5` (the current PRIMARY) | `SONIOX_API_KEY` |
| `transcribe_elevenlabs.py` | ElevenLabs Scribe v2 (`scribe_v2`), diarization + keyterms | `ELEVENLABS_API_KEY` |
| `transcribe_gemini.py` | Google `gemini-3.5-transcribe`, sk-SK | `GEMINI_API_KEY` |

Each adapter takes `<audio.wav> <out_dir> [lang=sk] [context=erp|none|<terms file>]`.
The context is the biasing vocabulary: `erp` is the built-in montalu/Money/Odoo
list, and a terms file (the same format as the scorer's, `#` comments and `re:`
lines skipped) lets any other client bring its own. The Gemini adapter takes a
fifth argument, `[mode=diarize|vocab]`. Each writes
`transcript.txt`, `transcript.json`, `speaker_turns.json`, `summary.json` and
`done` (or `error`).

## Two Gemini facts that shape the A/B

- **Vocabulary XOR speakers.** Gemini rejects `custom_vocabulary` together with
  diarization or word timestamps, so the A/B runs Gemini twice:
  - `gemini`: speakers, no vocabulary. The adapter prints a WARN that the ERP
    context was not sent.
  - `gemini-vocab`: the ERP vocabulary, no speakers. Its speaker agreement
    reads `n/a`.
- **30-minute limit with diarization.** Longer audio is cut into 28-minute
  chunks that overlap by 60 s, and speakers are stitched through the overlap
  (`asr_chunks.py` documents the rule). The known limit: a speaker who is
  silent throughout an overlap comes back as a new label. The 10-minute A/B
  slices never chunk, so this only matters for full meetings.

## What the report measures (`ab_asr.py score`)

- **ERP-term error rate:** (misses + insertions) / reference occurrences, over
  a term list. The terms are case-insensitive whole words; a line starting
  `re:` is a regex, for codes such as `re:ZAK[- ]?\d+`. Slovak inflects, and
  `faktúra` does not match `faktúru` or `faktúry`. So list the forms, or use
  a stem regex such as `re:faktúr\w*`. A regex that can match empty text is
  refused.
- **Speaker agreement:** the share of reference speech time attributed to the
  right speaker, under the best one-to-one label mapping.
- **Cost per audio minute:** from the price table in `ab_asr.py`, dated with
  source URLs. Re-check the prices before you quote them.

## Live run (needs the owner's keys; nothing below runs without them)

The run happens on the box that holds the meeting (montalu1 for
`mdq-bvtq-aku`). `$WORK` is that meeting's work dir, which already holds
`audio.wav` from `extract.sh` (SKILL.md Phase 1).

```bash
SKILL=$HOME/devel/airuleset/skills/meeting-analysis
WORK=<the mdq-bvtq-aku work dir with audio.wav>
AB=$WORK/ab
AR=$HOME/devel/airuleset/airuleset.py

# 0. keys: the owner types them into ONE secure page (never chat)
python3 $AR secret request ELEVENLABS_API_KEY GEMINI_API_KEY

# 1. three 10-min slices (pick starts where several people talk about ERP data)
python3 $SKILL/scripts/ab_asr.py cut --audio $WORK/audio.wav --at 600,1800,3000 --len 600 --out $AB

# 2. by listening, fill each $AB/slice-*/reference.json:
#    {"turns": [{"speaker": "<name>", "start_s": 0.0, "end_s": 12.5, "text": "<exact words>"}]}
# 3. write $AB/terms.txt: one ERP term per line (Odoo, Money, ...), stem regexes for
#    inflected words (re:faktúr\w*, re:zálohov\w+ faktúr\w*), and `re:` lines for
#    the order codes (OP / ZAK / PKO / IZOS). `score` refuses a slice whose
#    reference.json still has no turns.

# 4. run each provider under its own key
( export SONIOX_API_KEY=${SONIOX_API_KEY:-$(grep -hoE 'SONIOX_API_KEY=[^[:space:]]+' "$HOME/.soniox.env" 2>/dev/null | head -1 | cut -d= -f2)}
  python3 $SKILL/scripts/ab_asr.py run --provider soniox $AB/slice-* )
python3 $AR secret exec ELEVENLABS_API_KEY -- python3 $SKILL/scripts/ab_asr.py run --provider elevenlabs $AB/slice-*
python3 $AR secret exec GEMINI_API_KEY -- python3 $SKILL/scripts/ab_asr.py run --provider gemini $AB/slice-*
python3 $AR secret exec GEMINI_API_KEY -- python3 $SKILL/scripts/ab_asr.py run --provider gemini-vocab $AB/slice-*

# 5. score -> markdown report (exit 1 while any provider output is missing)
python3 $SKILL/scripts/ab_asr.py score --terms $AB/terms.txt --out $AB/report.md $AB/slice-*
```

Post `report.md` on the ticket. The choice of primary provider is a decision
on that ticket (#1155 part c); the fallback chain stays.

Notes:
- `secret exec` buffers the child's output until it exits, so the per-slice
  progress lines appear at the end.
- Every adapter reads only its env var. None reads a key file or prints a key.
- `run` exits 1 if any slice failed. Look in `$AB/slice-*/<provider>/error`;
  an HTTP failure there carries the provider's own error text.
- Every provider gets the same built-in `erp` vocabulary. To bias all of them
  with another list instead, add `--context <terms file>` to each `run`.
- The contract tests (`tests/test_asr_adapter_contract_1155.py`) use fixture
  responses built from the documented response shapes, not live traffic. The
  first live run is the first real check of those shapes. If a provider's
  real response differs, fix the adapter, then record a sanitized real
  response as the new fixture.
