---
name: claude-code-log
user-invocable: false
description: Browse, export, or archive Claude Code session transcripts as HTML/Markdown using claude-code-log. Load for transcript reading or token-usage review.
---

### Browsing / Exporting / Archiving Transcripts — `claude-code-log` (adopt, don't build — #420)

**To read, browse, export, or archive Claude Code session transcripts as HTML / Markdown / a TUI, use the external `claude-code-log` tool — never hand-roll an HTML exporter.** This is airuleset's ADOPTED transcript-browsing path (audit #416 verdict "ADOPT, don't build"; adopted in #420). It is a real, actively-maintained Python CLI (github.com/daaain/claude-code-log, PyPI, MIT) that turns the raw JSONL under `~/.claude/projects/` into clean, chronological, navigable pages.

#### Run it (no install needed)

- **One-shot, always latest:** `uvx claude-code-log@latest` — reads `~/.claude/projects/` and writes HTML. (Pin a specific release — `uvx claude-code-log==<version>` — if you need a reproducible run rather than the newest.)
- **Interactive TUI:** `uvx claude-code-log@latest --tui` — browse sessions in the terminal (textual-based).
- **Persistent install:** `pip install claude-code-log` (Python ≥3.10), then `claude-code-log`.
- **One project only:** point it at a single `~/.claude/projects/<encoded-cwd>/` directory.
- It produces **HTML + Markdown export, a project-hierarchy index, token-usage tracking, and cross-session navigation** — the capabilities airuleset's own `claude-history` deliberately does NOT have.

#### #410 gzip-at-rest interplay — the load-bearing caveat

**`claude-code-log` reads ONLY plain `.jsonl` files — it has NO gzip support.** It discovers and reads only plain `*.jsonl` (no gzip decoding), so a `.jsonl.gz` file is **invisible to it: silently skipped, never read — it does NOT crash.** This is the same accepted horizon as Claude Code's native `/resume` (which also lists only `*.jsonl`).

Why this is a bounded, non-blocking caveat (not a reason to avoid the tool):

- **Today nothing is compressed.** airuleset's #410 gzip-at-rest sweep is **report-only by default** (`AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1` is the opt-in, user-gated) — so on a normal box every transcript is still a plain `.jsonl` and fully readable by `claude-code-log`.
- **Even once live, only OLD transcripts compress** — the #410 sweep only touches 30-day-old, ≥100 KB MAIN transcripts, which are already beyond both the practical browse window and `/resume`'s own horizon.

**To browse an OLD, gzip-compressed session with `claude-code-log`, pick one:**

1. **Decompress a copy first:** `gunzip -k ~/.claude/projects/<dir>/<session>.jsonl.gz` — the `-k` KEEPS the `.gz`, leaving a plain `.jsonl` that `claude-code-log` then reads. (Delete the temporary `.jsonl` afterward if you want to reclaim the space.)
2. **Use airuleset's own `claude-history` instead** — it reads `.jsonl.gz` transparently (`airuleset.py`'s `find_transcripts`/`_read_jsonl`, #410), so it is the **gzip-aware fallback reader** for any compressed session.

**The two tools are complementary, not competitors:** `claude-history` = the gzip-aware terminal reader / fallback (reads plain AND compressed); `claude-code-log` = the rich HTML / TUI / token-tracking browser for the live (uncompressed) working set.

#### Why we don't lean on native retention (context)

Claude Code's native `cleanupPeriodDays` auto-delete is unreliable — `#58154` (subagent transcripts never cleaned), `#23710` (a value of 0 disables persistence entirely), `#59248` (silent deletion). airuleset therefore sets `cleanupPeriodDays=3650` fleet-wide (#376) and replaces retention with #410's gzip-at-rest (compresses, NEVER deletes — "história nesmie miznúť"). `claude-code-log` is the READ/EXPORT layer on top of that; it does not do retention.
