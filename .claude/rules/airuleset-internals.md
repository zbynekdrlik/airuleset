---
paths:
  - "airuleset.py"
  - "statusbar.py"
  - "watchdog/**"
  - "hooks/**"
  - "notify/**"
  - "filedrop/**"
  - "burn/**"
  - "tests/**"
  - "settings/**"
  - "scripts/**"
  - "modules/**"
  - "skills/**"
  - "agents/**"
  - "profiles/**"
  - "rules/**"
---


### airuleset Internals — Development Rules

Hard-won gotchas for airuleset's own code. Claude Code loads this automatically
when a matching file is read, so it costs nothing in a session that never
touches the internals. Moved here VERBATIM from the project CLAUDE.md (#92);
nothing was summarised or dropped.


## Kde čo žije (router)

Pôvodný 973 KB monolit bol rozbitý (#482) — dotyk súboru už neinjektuje ~240k tokenov. Lekcie oblasti nájdeš v jej path-scoped súbore, hlbší archív v on-demand referencii:

- `watchdog/**` → `.claude/rules/internals-watchdog.md`
- `hooks/**` → `.claude/rules/internals-hooks.md`
- `notify/**` → `.claude/rules/internals-notify.md`
- `filedrop/**` → `.claude/rules/internals-filedrop.md`
- `burn/**` → `.claude/rules/internals-burn.md`
- `tests/**` → `.claude/rules/internals-tests.md`
- `scripts/**` → `.claude/rules/internals-scripts.md`
- `statusbar.py` → `.claude/rules/internals-statusbar.md`
- `airuleset.py` (install/push/plugins) → `.claude/rules/internals-cli.md`
- `skills/** agents/** profiles/** modules/** rules/**` → `.claude/rules/internals-skills-modules.md`
- `.github/** scripts/ci_*.py tests/test_ci_*.py` → `.claude/rules/internals-ci.md`
- **hlbší archív / staré lekcie (on-demand, grep):** `.claude/rules-reference/internals-archive.md`

**Playbook (nová lekcia po tickete):** pridaj ju do príslušného `internals-<area>.md` (nie do archívu, nie sem). Keď ten súbor prekročí ~50 KB ratchet strop, presuň jeho najstaršie lekcie do archívu a nechaj inline len tie aktuálne.
