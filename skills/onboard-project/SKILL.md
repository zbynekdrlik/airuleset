---
name: onboard-project
description: Onboard a project under airuleset management the ONE maintained way — idempotent CLI that ensures git repo + GitHub remote + two/three-branch convention + .gitignore hygiene + CLAUDE.md/Playbook-router + foundation & notification tickets + machine-readable registry entry. Also the read-only --audit drift check. Load when the user says "onboard <project>", "add <project> under management / as a target", "put X under airuleset", or "which managed projects have drifted / audit the registry".
user-invocable: true
---

# Onboard a project under airuleset management

**All logic lives in the CLI (`cli_onboard.py`); this skill is a thin wrapper — invoke the command and report. Never re-implement any step here.** The command is IDEMPOTENT: a second run on an already-onboarded project reports every step "satisfied" and changes nothing, and it NEVER touches a project's dirty (in-progress) worktree files.

## Onboard one project

```bash
python3 ~/devel/airuleset/airuleset.py onboard-project <path> [--host dev1|dev2] [--name <repo-name>] [--override 3-branch] [--override merge=manual]
```

- `<path>` — the project directory. Repo name is derived deterministically from the path (leaf dir, `_`→`-`; a nested path under a client-cluster dir like `montalu/` gets the `montalu-<leaf>` prefix). Pass `--name` for an exception.
- `--host` — where the project lives (`dev1` = local default; a `REMOTE_HOSTS` name runs the steps over ssh).
- `--override` — a convention tag, repeatable: `3-branch` (odoo develop/staging/main), `merge=manual`, `local-builds=allowed`.
- `--dry-run` — report would-apply for every step, change nothing.

What it ensures (each step: detect → act only if absent → report `satisfied`/`applied`/`skipped`):

1. git repo (`git init` if missing)
2. `.gitignore` hygiene — append-only, never overwrites; untracks already-tracked build artifacts
3. `CLAUDE.md` skeleton + `## Playbook router` — ONLY if missing (existing file never overwritten)
4. GitHub remote (`gh repo create zbynekdrlik/<name> --private --source . --push` if none)
5. two/three-branch work branch per existing convention — NEVER changes the existing default branch
6. foundation-gap tickets — no CI, or web-without-version-label → files a tracked ticket (Scope-gate line; never auto-generates CI)
7. onboarding notification ticket in the project repo (`onboarding: projekt pod správou airuleset`)
8. registry entry in `projects-registry.json`

Then report the printed step table to the user (plain Slovak: čo bolo doplnené, čo už bolo v poriadku).

## Audit drift (read-only)

```bash
python3 ~/devel/airuleset/airuleset.py onboard-project --audit          # whole registry
python3 ~/devel/airuleset/airuleset.py onboard-project <path> --audit   # one project
```

`--audit` (alias `--check`) reports drift from the checklist — missing remote, tracked build artifacts, missing Playbook router, branch-model mismatch, missing registry entry — and changes NOTHING. The fix for drift is an explicit re-run of `onboard-project` on that project, never an auto-fix.

## Rules

- Never re-implement onboarding steps in this skill body — the CLI owns all logic.
- Never overwrite an existing file, never auto-generate CI, never mass-fix in audit mode.
- Deploying the tooling: `python3 airuleset.py push` (never bare `git push`).
