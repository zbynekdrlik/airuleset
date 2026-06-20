---
name: mdreview
description: Live web-research + ecosystem audit of the ruleset across dev1+dev2. Invokes rules-audit for the offline structural baseline, then adds what needs the network — current Anthropic best practices (with a cited length budget), a "rule now redundant" delete pass, and a plugins/MCP/tooling surface audit. Run manually when you want the ruleset re-checked against the latest published guidance.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, WebSearch, WebFetch, Grep, Glob, AskUserQuestion, Skill
---

# /mdreview — Live Web + Ecosystem Rule Audit

`rules-audit` is the fast OFFLINE structural auditor (size, dupes, orphans, contradictions, model-version strings, override-reconcile, mutation-budget). This skill is the LIVE layer: it runs `rules-audit` for the structural baseline, then adds only what needs the network — current published best practices, a redundancy-delete pass, and a plugins/MCP/tooling surface audit. Every change it proposes carries a source URL.

## Spine

1. Read live model from Environment → 2. `Skill(rules-audit)` for structural baseline → 3. Web best-practice research (Step A) + cited budget → 4. Ecosystem audits (Steps B/C/D) → 5. Score → 6. AskUserQuestion apply-loop → 7. audit log + validate + push.

## Step 0 — Read the live model (NEVER hardcode a generation)

Read THIS session's `## Environment` block. Note the live primary model (e.g. `Opus 4.8`, ID `claude-opus-4-8`), newest family, and subagent models. ALL search queries below are built from this value at runtime. A hardcoded model version anywhere in this skill body is itself a finding — fix it.

## Step 1 — Structural baseline via rules-audit (do NOT re-grep)

```
Skill(skill: "rules-audit")
```

Consume its punch-list verbatim (size budget, per-module line ranking, duplicates, orphans for modules/hooks/memory-refs, contradictions, context-gate coverage, §0 override-reconcile, §0b CI mutation-budget, memory hygiene, model-version-string bumping). This skill does NOT re-implement any of those greps — that is the deduplication. Everything below is the LIVE/ECOSYSTEM delta only.

## Step 1b — Official Anthropic CLAUDE.md framework (cited backbone)

```
Skill(skill: "claude-md-management:claude-md-improver")   # Anthropic's own quality criteria + length budget
Skill(skill: "claude-md-management:revise-claude-md")     # merge learnings without bloat
```

Capture the length budget, scope rules, and Include/Exclude table they apply. Tag findings `[anthropic-official]` — they outrank community blogs on conflict.

## Step A — Modern best-practice checklist (live web)

Run AFTER Step 1. Every change carries a source URL; no URL = no change. Official Anthropic docs (`code.claude.com`, `platform.claude.com`, `anthropic.com/engineering`) outrank community blogs.

WebSearch then WebFetch, queries built from the live model:
`"Claude <live-model> prompt engineering best practices"`, `"Claude Code <current-year> hooks skills features"`, `"CLAUDE.md best practices length budget"`, `"Anthropic agent skills spec"`. Record each source URL + date + author + a one-line summary to a research note.

**A. Skills-over-rules triage (primary de-bloat lever).** Anthropic: CLAUDE.md loads every session, so only broadly-applicable rules belong inline; sometimes-relevant workflows belong in skills (loaded on demand). Per always-loaded module: applies to >50% of turns regardless of task? → keep inline. Only during a specific task (deploy/migration/windows/mutation/hardware)? → flag MOVE TO SKILL. Cost: inline = full text every turn; skill = ~100-token description + body only when triggered. Report projected line savings.
Sources: code.claude.com/docs/en/best-practices · platform.claude.com/docs/en/agents-and-tools/agent-skills/overview

**B. Prose → hook conversion (advisory → deterministic).** Anthropic: hooks guarantee the action, CLAUDE.md is only advisory. `grep -rnE "MUST|NEVER|always run|banned|forbidden" modules/`; list hook keys in `settings/`. A mechanically-checkable rule with NO hook → flag HOOKABLE (PreToolUse exit-2 blocks, Stop exit-2 forces continue) + trim prose to a one-line pointer. Cross-check existing hooks — never double-enforce.
Sources: code.claude.com/docs/en/best-practices · code.claude.com/docs/en/hooks-guide

**C. Cited budget target + per-line cut test.** Fetch Anthropic's CURRENT published length budget in Step 1/1b and cite the URL. rules-audit reported the resolved size (N lines). State: `resolved = N lines = M× over the published budget. Target for THIS run = <number>; the punch list MUST move toward it.` Per line/module, apply the test: *would removing this cause Claude to make a mistake?* If not, cut it. Grade against the official Include/Exclude table (keep: non-guessable commands, non-default style, repo etiquette, env quirks, architecture; cut: anything inferable from code, standard conventions, linkable API docs, tutorials).
Sources: code.claude.com/docs/en/best-practices · alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/

**D. "Rule now redundant?" delete pass (de-bloat, not just bump).** For each module, ask two questions the offline auditor can't: (1) does the *current* model do this natively now (read the live model's prompting doc)? (2) does a HOOK now enforce it? If either → propose DELETE, not a version bump. This is the cut that rules-audit's version-string grep misses.

**E. Model currency (read the live doc).** WebFetch the current generation's prompting doc; propagate to `model-awareness.md`. For the live Opus generation flag: pure-negative rules with no positive exemplar (positive examples beat bans); rules using "only-high-severity / only-important" language (the harness now honors it → suppresses findings → prefer report-everything-then-filter); rules that rely on the model silently generalizing one example to others (state scope + "applies to all rewordings").
Source: platform.claude.com/docs/en/build-with-claude/prompt-engineering (substitute the live model's prompting page)

## Step B — Installed skills & plugins: health + upgrade

Two SEPARATE worlds; commands do NOT overlap.

| World | What | "Upgrade" mechanism |
|---|---|---|
| A. Marketplace plugins | `claude plugin` installs (superpowers, discord, caveman, claude-mem, context7, playwright, frontend-design, rust-analyzer-lsp, claude-md-management) | `claude plugin marketplace update <mkt>` → `claude plugin update <name>` → restart to apply |
| B. airuleset skills | the managed set in `SKILL_NAMES` (ci-monitor, autopilot, mdreview, …), symlinked, NO version | `git pull` in `~/devel/airuleset` → `python3 airuleset.py push` |

Inventory + outdated detection (no native "what's outdated" command):
```bash
claude plugin list                                   # name, version, enabled/disabled, scope
claude plugin details superpowers                    # component inventory + projected token cost (spot bloat)
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')));[print(k,v[0].get('version'),v[0].get('gitCommitSha','-')[:12]) for k,v in d['plugins'].items()]"
python3 ~/devel/airuleset/airuleset.py validate      # airuleset skills — validate checks ALL managed skills (NOT `status`, it truncates)
# git-backed marketplace (caveman): commits-behind
git -C ~/.claude/plugins/marketplaces/caveman fetch -q && echo "behind: $(git -C ~/.claude/plugins/marketplaces/caveman rev-list --count HEAD..origin/HEAD 2>/dev/null)"
# orphan / broken skills
find ~/.claude/skills/ -maxdepth 1 -xtype l          # dangling symlinks
ls ~/.claude/skills/*.md 2>/dev/null                 # flat .md = legacy; a .md duplicating a dir = shadowing
```

Decision rules:
- **Upgrade gotcha:** `claude plugin update` can compare a STALE local clone and report "already latest" → ALWAYS `claude plugin marketplace update <mkt>` FIRST, then `claude plugin update <name>`, then restart Claude Code to apply.
- Official plugins auto-update at startup; 3rd-party (caveman) + disabled (claude-mem) do NOT — upgrade by hand.
- `version: "unknown"` (context7/playwright/frontend-design) is NORMAL — they declare no semver; track by Last-updated date / gitCommitSha.
- **claude-mem is DISABLED and several majors behind, and overlaps airuleset's file-memory** → keep DISABLED or `claude plugin uninstall claude-mem@thedotmack`; never silently re-enable a badly-behind plugin or run two memory systems.
- airuleset skill stale → `git pull` + `python3 airuleset.py push`. Symlink dangling → `python3 airuleset.py install`.
- Foreign-owned orphan skills (`win-mcp.md`, `test-contact-form.md`) → file an issue, do NOT auto-delete (see project skill-ownership rule).
Sources: code.claude.com/docs/en/discover-plugins · `claude plugin --help`

## Step C — MCP / connector audit (read-only)

```bash
claude mcp list                                      # health + needs-auth + DUPLICATES
```
Cross-reference `~/.claude.json` (`mcpServers` vs project-scoped). Checks:
- **Redundancy:** different-endpoint servers BOTH load full tool schemas (only identical endpoints dedupe). e.g. n8n wired TWICE (a claude.ai n8n connector + self-hosted `n8n-mcp` HTTP, same tools) → keep one, drop the other.
- **Version drift:** `npx ... @latest` servers (playwright, context7) are non-reproducible and can fail concurrent sessions → pin each to an exact npm version.
- **Context cost:** each connected MCP injects its full tool schema every turn. Flag every needs-auth-but-unused connector (odoo, montalu, github, Google, Canva) for removal — an unused connector is pure context cost.
- **Secret hygiene:** never print bearer tokens; mask.
Verdicts: keep-auth / pin-version / remove-redundant / disable-unused.
Source: code.claude.com/docs/en/mcp

## Step D — New-tooling deep-research (INSTALL vs SKIP)

Principle (verified): LSP plugins and on-demand Skills REDUCE context; always-on MCP servers INCREASE it. "Install more to reduce chaos" is usually wrong. WebSearch `"best Claude Code plugins <year>"`, `"Claude Code LSP token reduction"`, `"MCP context overhead"`; browse `anthropics/claude-plugins-official` marketplace (it ships per-language LSP plugins — clangd/csharp/go/java/kotlin/php/ruby/rust/swift/ts/pyright).

Rubric: `score = (dev_velocity × context_reduction) / setup_cost`, each 1-5. INSTALL only if ≥3 AND no context regression. `context_reduction`: 5 = LSP/Skill that removes reads; 3 = on-demand; 1 = always-on MCP injecting schema every turn (auto-disqualifies any "reduce chaos" justification).

Decision rules:
- INSTALL an LSP only for an in-use language with no LSP: `pyright-lsp@claude-plugins-official` (Python — airuleset/voiceagent/presenter), `typescript-lsp@claude-plugins-official` (n8n/web) — rust-analyzer-lsp already installed. Grep find-usages ≈10k+ tokens vs LSP ≈500/call.
- NEVER add an always-on MCP to "reduce rules chaos" — add one only for a concrete recurring workflow.
- Memory is SOLVED (built-in MEMORY.md + airuleset file-memory) — keep claude-mem DISABLED; never add a silent second memory system.
- Biggest de-bloat is NOT an install: `claude-md-improver` + moving procedure-heavy modules to on-demand Skills + binding domain rules with `paths:` frontmatter.
- PRUNE before you add: drop the duplicate n8n entry; drop unused needs-auth connectors.
- SKIP (overlaps local skills): pr-review-toolkit, code-review, commit-commands, frontend-design.
Output a table (candidate | what | why-this-stack | install cmd | context effect | score | verdict), SHORTLIST first, then a DO-NOT-BOTHER list.

## Step E — caveman-compatibility

caveman lite is active (strips articles/filler). Rules must lean on STRUCTURAL markers (✅ ❌ 🌐, headers, code blocks), NOT exact prose strings. Flag any rule/hook whose enforcement depends on a literal phrase a compressor could mangle.

## Step F — Score, apply, log

1. **Score** each proposed change `Impact × Confidence ÷ Effort`; sort high→low.
2. **AskUserQuestion** per change (or grouped): Apply now / Defer-to-issue (`gh issue create`) / Reject. Never apply silently.
3. **Apply** accepted edits to `modules/` / skills / `settings/`. Heavy situational modules → MOVE TO SKILL per Step A.
4. **Validate + deploy:** `python3 airuleset.py validate` MUST pass, then `python3 airuleset.py push` (deploys dev1 + dev2 — never bare `git push`).
5. **Log** to `audits/mdreview-<date>.md`: every finding, its score, source URL, verdict (applied/deferred-#N/rejected), and the resolved-size before/after vs the cited budget.

## Rules

- Every proposed change cites a source URL captured THIS run. No URL → no change.
- Model generation is read from Environment ONCE (Step 0) — never hardcoded in a query.
- Structural checks are owned by `rules-audit` (Step 1) — do NOT re-grep them here.
- Never apply silently; always validate before push; always sync dev1 + dev2.
- This skill is manually invoked — no auto-schedule, no cron (competes with active dev runners).
