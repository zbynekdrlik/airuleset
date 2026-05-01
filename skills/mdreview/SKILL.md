---
name: mdreview
description: Web-research-driven audit of CLAUDE.md, memory, hooks, settings, skills across dev1+dev2. Fetches latest best practices from Anthropic, Karpathy, Claude Code community in last 7-14 days, then cross-references against current rules to find model-evolution drift, caveman-incompatible rules, broken Discord notifier, dead bash backgrounds, and bloat. Run weekly or after major model release.
user-invocable: true
allowed-tools: Bash, Read, Edit, Write, WebSearch, WebFetch, Grep, Glob, AskUserQuestion, Skill
---

# /mdreview — Live Web-Research Rule & Config Audit

Periodic audit of EVERY rule artifact across dev1 + dev2 against the latest published best practices for Claude Code agentic development. Differs from `rules-audit` by pulling current week's state-of-the-art from the web BEFORE judging current rules.

## Why this skill exists

Rules optimized for older models (Opus 4.6, Sonnet 4.5) can degrade newer-model behavior (Opus 4.7, future). Rules that compensated for known-old-model bugs become unnecessary once those bugs are fixed natively. Hooks/skills/notifications break silently as the platform evolves. The user runs this weekly to keep the rule set evergreen.

## Goals

- Pick up new SOTA techniques for CLAUDE.md / agent autonomy in last 7-14 days
- Detect rules that worked for older models but degrade newer-model behavior
- Detect rules that became unnecessary because the model now does the right thing natively
- Detect rule/hook output suppressed or distorted by caveman compression mode
- Detect background-process / Discord-notifier issues (long-running bash → no notification)
- Detect duplicate / contradictory / orphan rules across dev1 + dev2
- Detect drift between dev1 and dev2 (airuleset push should keep them in sync)

## Step 1: Web research — fetch current week (MANDATORY)

Use WebSearch then WebFetch to gather published material from the last 7-14 days. Record sources + URLs + summaries.

### Search queries (use ALL)

- `"CLAUDE.md" best practices 2026`
- `Claude Code agent autonomy rules`
- `Anthropic Opus 4.7 prompt engineering`
- `Anthropic Opus 4.7 release notes` and `4.6 vs 4.7 differences`
- `agentic development best practices 2026`
- `Claude Code skills hooks SOTA`
- `Karpathy Claude prompt engineering` (X/Twitter primary source)
- `site:anthropic.com claude.md` (authoritative Anthropic guidance)
- `site:x.com (Anthropic OR @AnthropicAI OR @karpathy) claude code` (last 7d)
- `Claude subagent driven development`
- `Claude Code hooks Stop PreToolUse 2026`

### What to capture per source

For each top result, WebFetch and capture:
- New techniques (structured output via tools, plan-edit cycles, verification gates, memory patterns)
- Anti-patterns the community now considers harmful
- Model-specific tuning advice (4.7 differences from 4.6)
- Hook / skill / slash command innovations
- Caveman / token-budget interactions if mentioned

Write findings to `~/devel/airuleset/audits/mdreview-<YYYY-MM-DD>-research.md` with:
- Source URL
- Publication date
- Author
- 3-5 bullet summary
- Tag: `[autonomy] [model:4.7] [hooks] [memory] [caveman] [discord]`

If a search returns nothing fresh in 14d, note the gap — don't fabricate research.

## Step 2: Inventory current state

### dev1 (local)

```bash
# Global config
ls -la ~/.claude/CLAUDE.md ~/.claude/settings.json
wc -l ~/.claude/CLAUDE.md
# Resolved size including @imports
python3 -c "
import re, pathlib
p = pathlib.Path.home() / '.claude' / 'CLAUDE.md'
text = p.read_text()
total = 0
for line in text.splitlines():
    m = re.match(r'@(\S+)', line.strip())
    if m:
        target = pathlib.Path(m.group(1).replace('~', str(pathlib.Path.home())))
        if target.exists():
            total += len(target.read_text().splitlines())
print(f'Resolved CLAUDE.md ≈ {total} lines')
"

# Auto-memory across all projects
find ~/.claude/projects -name 'MEMORY.md' -exec wc -l {} +

# Project-local CLAUDE.md
ls ~/devel/*/CLAUDE.md ~/devel/*/.claude/CLAUDE.md 2>/dev/null

# airuleset state
ls ~/devel/airuleset/modules/**/*.md
ls ~/devel/airuleset/hooks/*.sh
ls ~/devel/airuleset/skills/*/SKILL.md
cat ~/devel/airuleset/profiles/universal.profile
```

### dev2 (remote, 10.77.8.134)

```bash
ssh newlevel@10.77.8.134 "ls -la ~/.claude/CLAUDE.md ~/.claude/settings.json && wc -l ~/.claude/CLAUDE.md"
ssh newlevel@10.77.8.134 "ls ~/devel/*/CLAUDE.md 2>/dev/null"
ssh newlevel@10.77.8.134 "find ~/.claude/projects -name 'MEMORY.md' -exec wc -l {} + 2>/dev/null"
```

Compare dev1 vs dev2. Drift = airuleset push failure → flag for fix.

## Step 3: Cross-reference research vs current rules

For each finding from Step 1, audit current rules:

### a. Model-evolution check

Grep airuleset modules + project CLAUDE.md for model-version-specific phrases:

```bash
grep -rEn "Opus 4\.[0-6]|Sonnet 4\.[0-5]|Haiku 3|before 4\.7|in older models|known bug in" ~/devel/airuleset/modules/ ~/devel/*/CLAUDE.md
```

For each hit: check if Opus 4.7 release notes / community evidence shows the behavior is now native. If yes → propose removal (rule is now noise). If unclear → note for manual review.

### b. Caveman compatibility check

Caveman mode strips articles, filler, hedging — could break rules that expect specific output strings. Grep for rules that mandate specific text:

```bash
grep -rEn 'always (say|write|output|print)|MUST contain.*"[^"]+"|exact string|verbatim' ~/devel/airuleset/modules/ ~/devel/airuleset/hooks/
```

For each: verify caveman wouldn't strip the mandated text. If at risk:
- Rewrite rule to depend on STRUCTURE (emoji like ✅ ❌ 🌐, code blocks, headers) which caveman preserves
- Or mark rule as "exempt from caveman" in the rule body
- Or flag hook for caveman-aware update

### c. Hook / background-process / Discord check

```bash
# Find hooks that depend on background bash completion
grep -lE "run_in_background|nohup|disown|&\s*$" ~/devel/airuleset/hooks/

# Discord notifier wiring
cat ~/.claude/settings.json | python3 -c "import sys, json; cfg=json.load(sys.stdin); print(json.dumps(cfg.get('hooks', {}).get('Notification', []), indent=2))"

# Recent transcripts — were Discord notifications fired when bash bg completed?
grep -rE "Bash.*run_in_background.*true|notify-discord" ~/.claude/projects/ 2>/dev/null | head -20
```

Discord notifier currently misses notifications when bash bg jobs complete after agent stops. Audit:
- Are background bash completions wired to fire notification?
- Does notify-discord.sh check for pending bg jobs and fire after they finish?
- Should there be a PostToolUse hook on Bash bg-job completion?

If broken → propose `notify-discord.sh` patch + `settings.json` hook entry.

### d. Bloat / duplicate / orphan check

Delegate to existing `rules-audit` skill output, then merge findings.

```bash
# Orphan modules — listed in profile but not imported, or imported but not in profile
diff <(grep -oE 'modules/[^[:space:]]+\.md' ~/devel/airuleset/profiles/universal.profile | sort -u) \
     <(grep -oE 'modules/[^[:space:]]+\.md' ~/.claude/CLAUDE.md | sort -u)

# Hooks not wired in settings.json
ls ~/devel/airuleset/hooks/*.sh | xargs -n1 basename | while read h; do
  grep -q "$h" ~/.claude/settings.json || echo "ORPHAN HOOK: $h"
done

# Memory entries pointing to nonexistent files
for mem in $(find ~/.claude/projects -name 'MEMORY.md'); do
  awk -F'[][)(]' '/\[.*\]\(.*\.md\)/{print $4}' "$mem" | while read ref; do
    [ -f "$(dirname "$mem")/$ref" ] || echo "ORPHAN MEMORY REF: $mem -> $ref"
  done
done
```

### e. dev1/dev2 drift

If git status on `~/devel/airuleset` differs between dev1 and dev2, or if global CLAUDE.md size differs by >5 lines, propose an `airuleset push` to resync.

## Step 4: Score and prioritize findings

Each finding gets:
- **Impact** H/M/L — how much does this affect autonomy/correctness?
- **Confidence** H/M/L — how strong is the research evidence?
- **Effort** H/M/L — how big is the proposed edit?

Sort by `Impact × Confidence ÷ Effort`. Present top 10 as a table with: ID, file, current behavior, proposed change, source-URL, score.

## Step 5: AskUserQuestion — apply changes

For each top-priority finding, AskUserQuestion with options:

- **Apply** — make the edit now
- **Defer** — file as GitHub issue in `zbynekdrlik/airuleset` with research source linked
- **Reject** — record rejection reason in audit log

Apply approved changes:
1. Edit module / hook / settings.json
2. `python3 airuleset.py validate`
3. `python3 airuleset.py push`
4. Confirm both dev1 + dev2 updated

## Step 6: Save audit log

Write summary to `~/devel/airuleset/audits/mdreview-<YYYY-MM-DD>.md`:

```
# /mdreview audit — <date>

## Research sources fetched
- <url> — <date> — <author> — <tags>
- ...

## Findings (N total, M applied, K deferred, L rejected)

### Applied
- [ID] file — change — source

### Deferred (issues filed)
- [ID] file — issue #N — rationale

### Rejected
- [ID] file — proposed change — reject reason

## Files changed
<git diff --stat>

## dev1/dev2 drift
<resolved or none>
```

Commit to airuleset with: `audit: mdreview <date> — N applied, M deferred`.

## Step 7: Schedule next run

After completion, offer:

> Schedule next /mdreview in 7 days?

Use the `schedule` skill if user agrees.

## Rules

- **Always do Step 1 first.** No skipping research because "I remember last week's findings" — model behavior + community wisdom moves fast.
- **Cite every proposed change** — link to the research source URL. No source = no change.
- **Never apply changes silently** — every change goes through AskUserQuestion.
- **Always validate before push** — `python3 airuleset.py validate` must pass.
- **Always sync dev1 + dev2** — `airuleset push` after every applied change.
- **Caveman mode caveat** — when proposing rule wording changes, verify the new text survives caveman compression. Test by mentally stripping articles + filler from the rule output.
- **Discord notifier caveat** — if proposing a notification-triggering hook, manually test it fires (run a bg bash, wait for completion, check Discord).
