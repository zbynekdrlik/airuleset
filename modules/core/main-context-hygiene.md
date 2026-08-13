### Main-Context Hygiene — Delegate Heavy Reading to Subagents, Keep the Main Thread Thin

**Context gate — related rules you MUST also apply:**
- `claude-code-tooling.md` — the in-session subagent / agent-strip surfaces (foreground vs background)
- `model-awareness.md` — tier the delegated read: low/medium effort on the cheap end (Opus 4.8 low where a surface can name it; an ad-hoc `Explore`/`general-purpose` read-only sweep runs `model: "sonnet"` low — never an inherited-Fable mechanical dispatch; Haiku for the most trivial)
- `subagent-type-discipline.md` — use ONLY a listed `subagent_type` (`Explore` / `general-purpose`); never invent one
- `subagent-continuation.md` — every dispatch is one-shot; embed all context in the prompt

**The main session's context window is a SCARCE resource — every file you read into it, every log you scrape, every wide search you run YOURSELF fills it with raw detail and crowds out the user's HIGH-LEVEL goals. An overfull main thread FORGETS what the user is actually steering.** So you delegate GENUINELY LARGE reading, searching, auditing, and exploration to read-only subagents, and keep the main thread for orchestration, decisions, and the user's intent. **Delegation is for large, independent tracks — never a reflex** (recalibrated 2026-07-31: the official Opus 5 prompting doc says "delegation … multiplies cost and time when applied to small tasks. … Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work. If one subagent can complete the task, use one rather than several" — and the user reported the same week that the subagent-heavy regime coincided with a delivery collapse and INCREASED burn). Work that fits in a handful of tool calls with small outputs runs on the main thread; NEVER spawn a subagent to verify your own work.

**Delegate to a subagent (it reads the bulk, returns a TIGHT CONCLUSION — never raw dumps) whenever the work is:**
- A genuinely WIDE read — many files, a big corpus, multi-MB logs (a 2–3 file lookup that fits in a handful of calls stays on the main thread)
- A wide grep/glob sweep, a codebase map, a "where is X / what calls Y / list all uses of Z" search
- Log scraping, status polling, an audit/inventory across many files
- Cross-referencing several rules / docs / modules to synthesize ONE answer

Use `Explore` (read-only) or `general-purpose`, cheap model per `model-awareness.md`. Fan several out in PARALLEL when the areas are independent — you keep the conclusions, not the file dumps.

**Keep on the main thread (do NOT dump onto a naive subagent):**
- The actual WRITING / EDITING of code — implementation carries the full ruleset; a bare in-session `general-purpose` subagent boots with a REDUCED system prompt and NO rules. For implementation use the proper rules-carrying mechanism (`superpowers:subagent-driven-development`, or the `autopilot-worker` for issues), NEVER a context-less subagent.
- Targeted edits where you already know the exact file + lines.
- The high-level conversation, the user's goals, and the orchestration decisions themselves.

**The discipline:** before reading a pile of files into your own context, ask "do I need this verbatim, or just the conclusion?" Conclusion → dispatch a subagent and STAY THIN. Reading 10 files yourself to answer one question is the anti-pattern — it bloats the main thread and makes you forget what the user wants. Applies to all rewordings and semantic equivalents.
