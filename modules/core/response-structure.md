### Response Structure — Concise ≠ Compact

**Reducing word count is good. Cramming the result into a dense block is bad.** A scannable response is shorter to read than a compressed one, even if both have the same character count. The user reads the terminal at a glance — blank lines, headers, and short bullets are what make a response useful.

#### When to add structure

- **Single-topic short response** (1-3 sentences) → write it as flat prose. No headers, no bullets, no blank-line padding.
- **Multi-topic response** (status across Main/Dev, Frontend/Backend, multiple subsystems) → use bold headers + bullets + blank lines between sections. Always.
- **Long explanation** (>5 sentences on one topic) → break into paragraphs separated by blank lines.

If the response touches more than one distinct subject, structure it. If you're writing 5+ bullets in a row, group them under headers.

#### Mandatory whitespace

- Blank line between paragraphs.
- Blank line before AND after every header, code block, and table.
- Blank line between groups of bullets that cover different topics.
- Never let two distinct sections butt up against each other with no separator.

#### Use bold headers for topic shifts (not inline bold inside bullets)

When reporting on multiple subsystems, lead each section with a bold header on its own line, then bullets underneath. Inline bold inside a long bullet hides the section boundary.

**WRONG — dense, no breathing room, headers buried inline:**

```
- **Main:** PR #62 merged at 88e2586. Main CI deployed v0.27.0 to win-resolume; wall live, OBS connected, sp-fast playing.
- **Race resolution:** Both main's deploy and dev's deploy ran on win-resolume sequentially within 4 seconds. Main ran 2s after dev, so main's v0.27.0 binary is the final state on disk — that's the correct outcome (release version on the wall).
- **Dev:** synced with main, bumped to 0.28.0-dev.1 (commit c44dc73), CI green.
- **Main E2E:** cancellation requested (E2E currently mid-test on Resolume title step, ~2 min in). GitHub's cancel is propagating slowly; the deploy job was already ✅ before cancel so the rule's intent is satisfied.
```

**RIGHT — headers on their own lines, blank lines between sections, short bullets:**

```
**Main:**

- PR #62 merged at 88e2586
- CI deployed v0.27.0 to win-resolume
- Wall live, OBS connected, sp-fast playing

**Race resolution:**

- Main's and dev's deploys ran on win-resolume 4s apart, sequentially
- Main ran 2s after dev → main's v0.27.0 binary is the final state on disk
- Correct outcome (release version on the wall)

**Dev:**

- Synced with main
- Bumped to 0.28.0-dev.1 (commit c44dc73)
- CI green

**Main E2E:**

- Cancellation requested mid-test (Resolume title step, ~2 min in)
- GitHub's cancel propagating slowly
- Deploy job was already ✅ before cancel — rule's intent satisfied
```

Same content, half the cognitive load to parse.

#### Long bullets get broken up

If a bullet wraps to 2+ terminal lines (~100+ characters), it's too dense. Either split into multiple bullets, or promote it into its own section with sub-bullets.

WRONG: `- Race resolution: Both main's deploy and dev's deploy ran on win-resolume sequentially within 4 seconds. Main ran 2s after dev, so main's v0.27.0 binary is the final state on disk — that's the correct outcome (release version on the wall).`

RIGHT (split into 3 short bullets, each a single fact).

#### Scannability beats brevity

Goal: the user can extract the answer in 2 seconds of scanning. One paragraph of dense prose is usually slower to parse than the same content broken into a header + 3 short bullets, even if the bullet form is slightly longer in characters.

If you have to choose between "8 lines of bullets" and "5 lines of dense prose with the same info" — pick the bullets.

#### Anti-patterns

- Walls of bullets with no blank lines between groups → **WRONG.** Group by topic, blank line between groups.
- Inline bold (`**Main:** ...`) instead of header (`**Main:**\n\n - ...`) → **WRONG** when there are multiple sections. Inline bold disappears in the wall.
- One bullet that wraps to 3+ terminal lines → **WRONG.** Break it.
- Removing blank lines to "save space" → **WRONG.** Whitespace is information — it tells the user where one topic ends and the next begins.
- Treating "be concise" as "be compact" → **WRONG.** Shorter words, fewer redundant facts — yes. Removing structural whitespace — no.

#### Match the structure to the task

A simple acknowledgement ("Done. Pushed `abc1234`.") needs no structure. A status update across 4 subsystems needs headers + blank lines + short bullets. The completion report has its own EXACT template (see `completion-report.md`) — use that template, not freeform prose.
