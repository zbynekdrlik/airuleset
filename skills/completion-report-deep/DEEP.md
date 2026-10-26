### Completion Report — template + hard rules

**Context gate:** `complete-planned-work.md` (finish before reporting) · `autonomous-verification.md` (✅ = functional, not liveness) · `pr-merge-policy.md` (auto-merge default: report AFTER merged + deployed + verified; manual-marker projects stop at the green PR with ❓).

The owner reads only what the task was and how it was solved (issue 1233, folded into #1137). The FORMAT is short; every gate behind it stays.

#### Template

```
## ✅ Work Complete
**Úloha:** <čo sa chcelo — slovami používateľa>
**Riešenie:** <ako sa to vyriešilo, 1-2 vety>  🌐 <url, ak má práca UI>
✅ Overené: plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 · arch <oblasť> OK · Výstup: <hodnoty> · playbook: <lekcia | none>
✅ DONE: <jeden riadok>
```

**Reduced-authority streams (fork-no-merge AND branch-merge) — SAME four lines.** Riešenie names the hand-off instead of merge/deploy: `READY-FOR-REVIEW komentár na #N (<topic>) + --handoff karta`, posted once the profile's own end-point is reached (fork-no-merge: the fork push; branch-merge: the integration-branch merge, `PR #M do <integration> zmergnutý <sha>`). The ticket stays OPEN for the gatekeeper — NEVER a self-close (odoo-erp: the stream self-closes only after the gk verdict, #756). Výstup reads back from the local/integration environment. A bare `✅ DONE: #N hotové` is blocked.

#### Hard rules (hook-enforced by `stop-check-prose-violations.sh` unless noted)

- **The `✅ Overené:` line** — the LAST one in the message counts (a `- `, `> ` or `**` prefix is fine); tokens are separated by ` · `, and a token on it is never re-read from the pre-#1137 lines.
- **plan N/N** — the `plan-check` skill ran and every item is done (N equals M).
- **review / deep are honest post-fix counters** — exactly `0 🔴 0 🟡 0 🔵` after the token (`/review`, `/deep` also accepted). `deep` = `superpowers:requesting-code-review`, which MUST also pass clean; `review` = /review standards self-applied or ONE fresh-context `general-purpose` reviewer, never the built-in `review`/`code-review` skill (#363). Every 🔴/🟡/🔵 inside the diff is FIXED before the report; a finding outside the diff is fixed in the same branch unless it clears a follow-up criterion (`complete-planned-work.md`). `0` over an unfixed finding is a false report.
- **arch** — the main-session review of the whole AREA the change lands in: `OK` (one concept, one source of truth), `REWORK fixed` (patchwork fixed in this change) or `REWORK #N` (ONE `architecture-rework` ticket for a larger defect). Area = the playbook `.claude/rules/<area>.md` split, else the top-level directory. Infra units cite the next-cycle flow metric, not the merge (#992).
- **Výstup** — concrete values READ BACK from the real artifact the work produced or changed (the sent email from the DB, the rendered page, the live screen, the deployed version label): `Výstup: email obj. 2041 — cena 12,50 €`. Send/delivery/"funguje" is liveness, not content (montalu3 0 € emails, #446). No user-facing output → `Výstup: n/a — <prečo>`; `n/a` next to a 🌐/📱 URL is a contradiction. The value (a digit or a quoted span) sits on the Overené line itself and ends at the next ` · `.
- **playbook** — the `playbook-review` skill ran: `playbook: <what was captured>` or `playbook: none` (a `📔 Playbook:` line is also accepted).
- **🌐 / 📱** — every user-facing artifact this work produced or affects: user-clickable URLs only (backend/API URLs belong in Výstup), verified live before pasting, never localhost/127.0.0.1/0.0.0.0. A client-app project carries BOTH `🌐 Demo:` and `📱 <platform>:` (the APK/IPA/signed binary) on every ticket that touched the app; 📱 = the installable-build DOWNLOAD URL only, never a decorative mobile note. Never make the owner search the transcript for an artifact URL — REPEAT it in the report. Hook-checked: a localhost URL, and a UI deploy (frontend / dashboard / web app, `deployed` / `nasadené`) with no 🌐 URL at all (owner rule #595).
- **Úloha / Riešenie** — bold labels at line start (`**Úloha:**`), plain language a non-engineer understands; every `#N` carries its title.
- No "Remaining / Future / TODO" section; deferred work cites the ticket it is recorded on.
- ⏳ or ❌ anywhere = not done, do not send. A question ends with the structured `❓` block (`user-questions-slovak.md`).
- Bug fixes: the RED→GREEN commit order is enforced at push time (`regression-test-first`), not in the report.

#### Before writing it

1. `plan-check` skill → fix every NOT DONE item.
2. Apply `/review` standards (never invoke the built-in `review`/`code-review` skill, #363) → fix every finding.
3. `superpowers:requesting-code-review` → fix every finding.
4. Open the real output artifact and note the values you SEE.
5. `playbook-review` skill.

The pre-#1137 line forms (`✅ /plan-check:` audit line, `✅ Výstup:`, `🏛 Architektúra:`, `📔 Playbook:`, `**Goal:**`/`**What changed:**`) are still accepted by the hook. Machine compacts are REMOVED (#1084): a report is just a natural autocompact point. History + the pre-#1137 template text: `.claude/rules-reference/completion-report-history.md`.
