---
name: mdreview
description: Live web-research + ecosystem audit of the ruleset across dev1+dev2. Invokes rules-audit for the offline structural baseline, then adds what needs the network — current Anthropic best practices, a keep-or-cut review that DEFAULTS to keeping rules that still earn their place, and a plugins/MCP/tooling surface audit. Goal = a regularly-reviewed, EFFECTIVE ruleset, not a smaller one. Run manually when you want the ruleset re-checked against the latest published guidance.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, WebSearch, WebFetch, Grep, Glob, AskUserQuestion, Skill
---

# /mdreview — Live Web + Ecosystem Rule Audit

`rules-audit` is the fast OFFLINE structural auditor (size, dupes, orphans, contradictions, model-version strings, override-reconcile, mutation-budget). This skill is the LIVE layer: it runs `rules-audit` for the structural baseline, then adds only what needs the network — current published best practices, a keep-or-cut review, and a plugins/MCP/tooling surface audit. Every change it proposes carries a source URL.

## Goal — effectiveness over line count (READ FIRST)

**The goal is a regularly-REVIEWED, EFFECTIVE ruleset — NOT a small one.** Line count is a trigger to review, NEVER a target to minimize. Do NOT cut a rule just because the config is "over budget".

- KEEP every rule that still helps functionality or task completion — even if long. A rule that solved a real recurring problem stays.
- REMOVE a rule ONLY with proof it is now obsolete: (a) the live model does it natively now, (b) a hook deterministically enforces it, or (c) it is a true duplicate of another rule. Evidence required — same bar as any other change (source URL / hook key / duplicate file:line).
- Deleting a working rule to save lines is a REGRESSION — the worst outcome, worse than being over any budget. The user has lived this: rules they relied on were cut for size and the solved problems came back.
- Current models (live Opus generation) do NOT drop instructions due to length the way older generations did — so "bloat causes ignored instructions" is NOT a reason to cut here. Cut only proven-obsolete rules.
- A long-but-effective module is fine. The review's job is to confirm each rule still earns its place and to fix what's genuinely stale/duplicate — not to chase a number.

## Spine

1. Read live model from Environment → 2. `Skill(rules-audit)` for structural baseline → 3. Web best-practice research (Step A) → 4. Ecosystem audits — plugins/skills health, MCP, and the MANDATORY per-project tooling pass (Steps B/C/D) → 5. Score → 6. AskUserQuestion review-loop (incl. all tooling findings) → 7. audit log + validate + push.

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

**A. Skills-over-rules triage (an OPTION, not a mandate).** Anthropic: CLAUDE.md loads every session, so broadly-applicable rules belong inline; sometimes-relevant workflows CAN live in skills (loaded on demand). Per always-loaded module: only during a specific task (deploy/migration/windows/mutation/hardware)? → it is a CANDIDATE to move to an on-demand skill / `paths:`-scoped rule. But propose the move ONLY when on-demand loading does NOT weaken enforcement of something that matters — a rule that must fire every relevant turn stays inline (or becomes a hook). Moving a rule off always-on is a real enforcement change → propose with the tradeoff, never as a line-cutting reflex.
Sources: code.claude.com/docs/en/best-practices · platform.claude.com/docs/en/agents-and-tools/agent-skills/overview

**B. Prose → hook conversion (advisory → deterministic).** Anthropic: hooks guarantee the action, CLAUDE.md is only advisory. `grep -rnE "MUST|NEVER|always run|banned|forbidden" modules/`; list hook keys in `settings/`. A mechanically-checkable rule with NO hook → flag HOOKABLE (PreToolUse exit-2 blocks, Stop exit-2 forces continue) + trim prose to a one-line pointer. Cross-check existing hooks — never double-enforce.
Sources: code.claude.com/docs/en/best-practices · code.claude.com/docs/en/hooks-guide

**C. Size as a review trigger (NOT a target).** rules-audit reports the resolved size (N lines). Use it ONLY to decide WHETHER a periodic review is due and WHICH modules to look at first (largest → review first) — never as a number to hit. Do NOT state a target line count, and do NOT require the punch list to "move toward" any size. Per rule the test is: *does this still help functionality or task completion?* → keep. *Is it provably obsolete (native-now / hook-enforced / duplicate)?* → cut with evidence. Anthropic's Include/Exclude table applies to genuinely low-value lines (restating standard conventions, tutorials, linkable API docs) — NOT to rules that earn their place. When unsure whether a rule still matters: KEEP it.
Sources: code.claude.com/docs/en/best-practices · platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

**D. "Rule now redundant?" keep-or-cut pass — default KEEP.** For each module, ask two questions the offline auditor can't: (1) does the *current* model do this natively now (read the live model's prompting doc)? (2) does a HOOK now enforce it deterministically? If you can PROVE either (cite the doc / the hook key), propose DELETE. If you cannot prove it → KEEP (uncertainty defaults to keeping). This finds the few genuinely-obsolete rules; it is NOT a license to thin the ruleset.

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
Cross-reference `~/.claude.json` (global `mcpServers` vs project-scoped). Checks:
- **Prefer PROJECT-SCOPED over global (the direction).** A server in global `mcpServers` (or an account-level claude.ai connector) is visible to ALL projects — including ones that should never touch it (e.g. n8n tools loaded into projects with nothing to do with n8n). Flag global/account MCP that belongs to ONE project → recommend moving it to that project's `.mcp.json`. Goal: each project sees only the MCP it actually uses.
- **Redundancy:** the same service wired twice (e.g. a claude.ai n8n connector AND a self-hosted `n8n-mcp`) loads its tool schema twice. Flag it — but VERIFY which one a project actually uses before recommending removal; never assume "unused" and drop. Resolve via the OWNING project, not a blind global delete.
- **Health:** a server showing `Failed to connect` is broken (fix the endpoint) or dead (owning project removes it) — surface it, don't silently ignore.
- **Connected = real cost; needs-auth = cheap.** A CONNECTED MCP injects its full tool schema every turn (codex-bridge, n8n, playwright = dozens of tools each). A `needs-auth` connector exposes only ~2 auth stubs until authed, so its context cost is LOW — do NOT over-weight unused needs-auth connectors. The real saving is consolidating duplicate CONNECTED servers and scoping globals to their project.
- **Secret hygiene:** never print bearer tokens; mask.
Verdicts (always for the OWNING project to action, never a blind global edit): move-to-project-scope / consolidate-duplicate / fix-or-remove-broken / keep.
Source: code.claude.com/docs/en/mcp

## Step D — New-tooling research: ecosystem + MANDATORY per-project pass

Principle (verified): LSP plugins and on-demand Skills REDUCE context; always-on MCP servers INCREASE it. "Install more to reduce chaos" is usually wrong. Rubric: `score = (dev_velocity × context_reduction) / setup_cost`, each 1-5. INSTALL only if ≥3 AND no context regression. `context_reduction`: 5 = LSP/Skill that removes reads; 3 = on-demand; 1 = always-on MCP injecting schema every turn (auto-disqualifies any "reduce chaos" justification).

**D1 — Ecosystem layer (global).** WebSearch `"best Claude Code plugins <year>"`, `"Claude Code LSP token reduction"`, `"MCP context overhead"`. Extract the FULL local marketplace catalog — do NOT guess plugin names:
```bash
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json')));[print(p.get('name'),'|',(p.get('description') or '')[:80]) for p in d.get('plugins',[])]"
```
Cross-check `claude plugin list` so you never re-recommend an installed plugin.

**D2 — PER-PROJECT pass (MANDATORY — this IS the tooling research, not optional).** A generic "install pyright" answer is a FAILURE of this step. Go project-by-project across the user's actual repos and find what helps EACH one:
1. Enumerate every active project: `~/devel/*/` that has a `CLAUDE.md` (the managed/active set). Detect each one's REAL stack (Cargo.toml / package.json / pyproject / __manifest__.py / go.mod / C/C++ sources) and what it integrates (read its CLAUDE.md/README — external services, hardware, APIs, DBs).
2. Match each project against the full D1 catalog. Per project decide: a per-language LSP for an in-use language with NO LSP yet (e.g. `clangd-lsp` for real C/C++); a PROJECT-SCOPED domain MCP ONLY if that project automates that exact service as a recurring workflow; an on-demand skill for a recurring task; or — a valid, common answer — NOTHING NEW (covered by installed LSPs + context7).
3. **Fan out** — dozens of projects → use a Workflow / parallel agents clustered by stack; each agent reads a cluster's CLAUDE.md files and returns structured per-project verdicts. Cover EVERY active project, not a sample (log any skipped + why).
4. **Cross-cut:** report LSP coverage across ALL projects — which in-use languages still lack an LSP (recurring gap = C/C++ → `clangd-lsp` for the OBS/JUCE projects). pyright/typescript/rust-analyzer already installed.
5. Be HONEST about fit: the user's stack is mostly self-hosted media/AV/network — the SaaS-heavy catalog matches few projects. Say so; never manufacture a match.

Decision rules:
- NEVER add an always-on MCP to "reduce chaos" — only for a concrete recurring workflow, and PROJECT-SCOPED (`.mcp.json` in that project), never a global connector visible to all projects.
- Memory is SOLVED (built-in MEMORY.md + airuleset file-memory) — keep claude-mem DISABLED.
- SKIP (overlaps local skills): pr-review-toolkit, code-review, commit-commands, frontend-design.

**D3 — Hand off, don't reach in.** A project-scoped MCP / skill / config belongs to the OWNING project (stay-in-lane). For each such recommendation, FORMULATE a ready-to-paste handoff prompt for that project's Claude (what to install, how to scope it, what to verify). The user pastes it into that project's session — you NEVER edit another project's code/config.

Output: a per-project table (project | real stack | tool | type | scope | why | score | verdict), the cross-cutting LSP-coverage line, the project-scoped-MCP handoff prompts, and an honest "these need nothing new" list — ALL surfaced to the user in Step F, never auto-applied.

## Step E — caveman-compatibility

caveman lite is active (strips articles/filler). Rules must lean on STRUCTURAL markers (✅ ❌ 🌐, headers, code blocks), NOT exact prose strings. Flag any rule/hook whose enforcement depends on a literal phrase a compressor could mangle.

## Step F — Score, apply, log

1. **Score** each proposed change `Impact × Confidence ÷ Effort`; sort high→low.
2. **AskUserQuestion — EVERYTHING goes to the user's review.** Per change (or grouped): Apply now / Defer-to-issue (`gh issue create`) / Reject. Never apply silently. This INCLUDES every tooling finding (plugin installs, per-project recommendations, MCP changes): present each with a "what next?" choice — install now / hand off a project-scoped prompt to the owning project's Claude / skip. A plugin install changes the user's environment → it is ALWAYS user-reviewed, never auto-run on the agent's own judgement. A found-but-unpresented recommendation is a dropped finding.
3. **Apply** accepted edits to `modules/` / skills / `settings/`. Heavy situational modules → MOVE TO SKILL per Step A.
4. **Validate + deploy:** `python3 airuleset.py validate` MUST pass, then `python3 airuleset.py push` (deploys dev1 + dev2 — never bare `git push`).
5. **Log** to `audits/mdreview-<date>.md`: every finding, its score, source URL, verdict (applied / deferred-#N / rejected / KEPT-still-effective). Record the resolved size as a tracked metric over time — NOT as a pass/fail vs a budget. A run whose verdict is "reviewed, all rules still earn their place, nothing cut" is a SUCCESSFUL run.

## Rules

- **Effectiveness over line count.** Keep every rule that still helps; cut ONLY proven-obsolete (native-now / hook-enforced / duplicate). A small ruleset is NOT the goal — an effective, reviewed one is. Deleting a working rule to save size is a regression.
- Every proposed change cites a source URL (or hook key / duplicate file:line) captured THIS run. No evidence → no change. Uncertainty → keep.
- Model generation is read from Environment ONCE (Step 0) — never hardcoded in a query.
- Structural checks are owned by `rules-audit` (Step 1) — do NOT re-grep them here.
- MCP/connector changes are for the OWNING project to apply (prefer project-scoped); never blind-edit global MCP config.
- Never apply silently; always validate before push; always sync dev1 + dev2.
- This skill is manually invoked — no auto-schedule, no cron (competes with active dev runners).
