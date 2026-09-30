---
name: onboard-project
description: Onboard a project under airuleset — git repo + GitHub remote + branch convention + CLAUDE.md + foundation tickets + registry. Use when the user says "onboard <project>" or "audit the registry".
user-invocable: true
---

# Onboard a project under airuleset management

**All logic lives in the CLI (`cli_onboard.py`); this skill is a thin wrapper — invoke the command and report. Never re-implement any step here.** The command is IDEMPOTENT: a second run on an already-onboarded project reports every step "satisfied" and changes nothing, and it NEVER touches a project's dirty (in-progress) worktree files.

## Onboard one project

```bash
python3 ~/devel/airuleset/airuleset.py onboard-project <path> [--host dev1|dev2] [--name <repo-name>] [--override 3-branch] [--override merge=manual]
```

- `<path>` — the project directory. Repo name is derived deterministically from the path (leaf dir, `_`→`-`; a nested path under a client-cluster dir like `montalu/` gets the `montalu-<leaf>` prefix). Pass `--name` for an exception.
- `--host` — where the project lives (`dev1` = local default; a `REMOTE_HOSTS` name runs EVERY step — detect AND act — over ssh on that box, expanding `~` against the REMOTE home; nothing local is created). Fail-safe: an unreachable host or a missing remote directory REFUSES the run (real and dry-run) rather than proceeding on local false-negatives (#583).
- `--override` — a convention tag, repeatable: `3-branch` (odoo develop/staging/main), `merge=manual`, `local-builds=allowed`.
- `--dry-run` — report would-apply for every step, change nothing.

What it ensures (each step: detect → act only if absent → report `satisfied`/`applied`/`skipped`):

1. git repo (`git init` if missing)
2. `.gitignore` hygiene — append-only, never overwrites; untracks already-tracked build artifacts
3. `CLAUDE.md` skeleton + `## Playbook router` — ONLY if missing (existing file never overwritten)
4. GitHub remote (`gh repo create zbynekdrlik/<name> --private --source . --push` if none)
5. two/three-branch work branch per existing convention — NEVER changes the existing default branch
6. foundation-gap tickets — no CI, web-without-version-label, or a Rust web app without a tray icon (`rules/rust-web-tray.md`, #1198) → files a tracked ticket (Scope-gate line; never auto-generates CI)
7. onboarding notification ticket in the project repo (`onboarding: projekt pod správou airuleset`)
8. registry entry in `projects-registry.json`

Then report the printed step table to the user (plain Slovak: čo bolo doplnené, čo už bolo v poriadku).

## Audit drift (read-only)

```bash
python3 ~/devel/airuleset/airuleset.py onboard-project --audit          # whole registry
python3 ~/devel/airuleset/airuleset.py onboard-project <path> --audit   # one project
```

`--audit` (alias `--check`) reports drift from the checklist — missing remote, tracked build artifacts, missing Playbook router, branch-model mismatch, missing registry entry, a Rust web app without a tray (`missing-tray`; a server-only app opts out with `<!-- airuleset:tray=n/a <reason> -->` in its CLAUDE.md) — and changes NOTHING. A cross-host entry on a known `REMOTE_HOSTS` box is audited OVER SSH (a healthy remote project reads clean); a host that is neither this box nor a known remote target reads `unreachable`, never a false drift (#583). The fix for drift is an explicit re-run of `onboard-project` on that project, never an auto-fix.

## Rules

- **Own account per project (#1184, owner 2026-09-29).** The CLI onboards ONLY into a DECLARED project account on its declared host, and only AS that account. It refuses `newlevel`, the control accounts and any undeclared account. `--legacy-ok <ticket>` is accepted only as bookkeeping for a project that is already registered. To add a project:
  1. Declare the account in `cli_account_bootstrap.SERVICE_ACCOUNTS`: host; sudo (default NO); reach and secrets (default none); webterm humans.
  2. Have root run `airuleset.py account-bootstrap --render <acct>` on that host.
  3. Add its `<acct>@<box>` `REMOTE_HOSTS` entry (the claudy precedent).
  4. Run `onboard-project <path> --host <acct>@<box>`.

  `airuleset.py accounts status` lists the frozen legacy inventory (a count that only goes down).
- Never re-implement onboarding steps in this skill body — the CLI owns all logic.
- Never overwrite an existing file, never auto-generate CI, never mass-fix in audit mode.
- Deploying the tooling: `python3 airuleset.py push` (never bare `git push`).
