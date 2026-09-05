### Issue / PR References Always Carry Their Title — Never a Bare #N

**The user manages many projects in parallel, does NOT keep tickets open, and cannot decode a bare `#42` by number from memory.** EVERY time you mention a GitHub issue or PR by number — in ANY message, not only completion reports: status updates, milestone pings, mid-work narration, "filed as", "closes", plan steps — you MUST include what it is about (its title or a short topic) right next to the number.

#### The rule

- Issue/PR reference = number **+** title/topic, every time: `#42 (karaoke word-timing sanitizer)`, `PR #7: Refactor driver.rs and add lyrics test`, `Closes #234 (driver.rs over the 1000-line cap)`, `Filed as #88: add version label to dashboard`.
- Copy the title from `gh issue view <N> --json title` / `gh pr view <N>` — do not guess it.
- Multiple refs in one line each get their topic: `merged #42 (NDI rebind) + #45 (sanitizer) → v1.2.3`.

#### Anti-patterns (banned — all rewordings)

- "Working on #42 now." → **WRONG.** `Working on #42 (karaoke sanitizer) now.`
- "PR #7 — mergeable, clean" → **WRONG.** `PR #7: <title> — mergeable, clean`
- "Filed as #88." / "See #91." / "Blocked by #103." with no topic → **WRONG.** Add the topic.
- A milestone ping "merged #42+#45 to main" with no titles → **WRONG.** Add a short topic per number.
- A RANGE / pile — `#684–#740`, "the 52-ticket rollout", "those skip'd tickets" — naming dozens the user can't see → **WRONG, doubly so.** List the few that matter each with a one-line plain meaning, OR describe the group in plain words ("~50 starších úloh okolo prerábky prehrávača"); never a bare range. When it's a question, see `user-questions-slovak.md` (explain each + ask in small parts).

#### One exception — commit-message syntax

Inside an actual commit message a bare `Closes #42` is correct git syntax (GitHub needs it to auto-close) and is fine. The title requirement is for USER-FACING PROSE — every reference the user reads in a chat message.

The intent: the user should never have to look up what `#N` means. Applies to all messages and all rewordings.

### Odoo Discuss Thread References Always Carry the Thread NAME + a Clickable Deep URL — Never a Bare Channel Id

**The SAME rule, for Odoo Discuss threads.** The owner manages many client Discuss threads across projects and CANNOT decode a bare internal channel id (`288`) from memory — exactly as they cannot decode a bare `#42`. So EVERY owner-facing mention of an Odoo Discuss thread/channel — in ANY message, not only approval questions: status updates, mid-work narration, completion reports, milestone pings — MUST carry BOTH the thread's human NAME **and** its clickable deep URL. Never a bare number.

Canonical form:

```
vlákno „Zakaznicky portal 3" — https://<instancia>/odoo/discuss?active_id=discuss.channel_288
```

- Thread reference = **NAME + deep URL**, every time. The deep URL opens the exact thread: `https://<client-instance>/odoo/discuss?active_id=discuss.channel_<N>` (montalu → `https://erp.montalu.cloud/…`). Open it and confirm it loads before pasting (same no-dead-links discipline as any `🌐` URL).
- **Banned — a BARE channel id in owner-facing prose (all rewordings):** `vlakno 288`, `vo vlákne 288`, `Discuss vlákno 288`, `kanál 293`, `ch275`, `channel 288`. The owner's exact complaint (montalu3, 2026-08-24): *"co ja mam akoze robit s 'vlakno 288'?!"* — a number they cannot decode.
- Origin: airuleset **#657**, extending **#650** (the `❓` client-posting approval question already hook-enforces NAMING the exact target thread — `stop-check-question-quality.sh` Check 6) and **#595** (deep-link URLs for every openable reference in a client message). This doctrine widens the requirement to EVERY owner-facing mention and adds the thread's OWN deep URL alongside the name.
- Mechanically backstopped on the wider owner-facing prose surface: `hooks/stop-check-prose-violations.sh` HARD-blocks a bare Discuss channel id (in an Odoo-context message) that carries no clickable `discuss.channel_<N>` deep URL. The hook is a narrow high-confidence backstop; THIS doctrine covers every rewording universally.

Applies to all streams (montalu*/david*/miva*/gk) and all messages and all rewordings.
