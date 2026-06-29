### Main-Context Hygiene — Delegate Heavy Reading to Subagents, Keep the Main Thread Thin

**Context gate — related rules you MUST also apply:**
- `claude-code-tooling.md` — the in-session subagent / agent-strip surfaces (foreground vs background)
- `model-awareness.md` — tier the delegated read: cheap model (Sonnet/Haiku) + low/medium effort for read-only sweeps
- `subagent-type-discipline.md` — use ONLY a listed `subagent_type` (`Explore` / `general-purpose`); never invent one
- `subagent-continuation.md` — every dispatch is one-shot; embed all context in the prompt

**The main session's context window is a SCARCE resource — every file you read into it, every log you scrape, every wide search you run YOURSELF fills it with raw detail and crowds out the user's HIGH-LEVEL goals. An overfull main thread FORGETS what the user is actually steering.** So by DEFAULT you aggressively delegate heavy/bulk READING, SEARCHING, AUDITING, and EXPLORATION to read-only subagents, and keep the main thread for orchestration, decisions, and the user's intent. This is reflexive, not a last resort.

**Delegate to a subagent (it reads the bulk, returns a TIGHT CONCLUSION — never raw dumps) whenever the work is:**
- Reading/scanning more than ~2–3 files to answer a question, or any whole-file read you don't need verbatim
- A wide grep/glob sweep, a codebase map, a "where is X / what calls Y / list all uses of Z" search
- Log scraping, status polling, an audit/inventory across many files
- Cross-referencing several rules / docs / modules to synthesize ONE answer

Use `Explore` (read-only) or `general-purpose`, cheap model per `model-awareness.md`. Fan several out in PARALLEL when the areas are independent — you keep the conclusions, not the file dumps.

**Keep on the main thread (do NOT dump onto a naive subagent):**
- The actual WRITING / EDITING of code — implementation carries the full ruleset; a bare in-session `general-purpose` subagent boots with a REDUCED system prompt and NO rules. For implementation use the proper rules-carrying mechanism (`superpowers:subagent-driven-development`, or the `autopilot-worker` for issues), NEVER a context-less subagent.
- Targeted edits where you already know the exact file + lines.
- The high-level conversation, the user's goals, and the orchestration decisions themselves.

**The discipline:** before reading a pile of files into your own context, ask "do I need this verbatim, or just the conclusion?" Conclusion → dispatch a subagent and STAY THIN. Reading 10 files yourself to answer one question is the anti-pattern — it bloats the main thread and makes you forget what the user wants. Applies to all rewordings and semantic equivalents.
