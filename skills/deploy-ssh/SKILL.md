---
name: deploy-ssh
description: Deploy binaries or scripts to remote machines via SSH. Covers the full stop-copy-start-verify cycle.
user-invocable: true
---

# SSH Deployment

## Read Credentials

Check `TARGETS.md` in the project root for host, user, and authentication details.
TARGETS.md must be in .gitignore — never commit credentials.

## Deploy Steps

1. **Stop service:**
   - Linux: `ssh USER@HOST "systemctl stop SERVICE"`
   - Windows: `ssh USER@HOST "taskkill /F /IM app.exe"`

2. **Copy binary:**
   - `scp binary USER@HOST:/path/to/install/dir/`

3. **Start service:**
   - Linux: `ssh USER@HOST "systemctl start SERVICE"`
   - Windows GUI: Use `schtasks` with `/it` flag (see windows-remote-gui skill)
   - Windows service: `ssh USER@HOST "sc start SERVICE"`

4. **Verify deployment:**
   - Health check: `curl -sf http://HOST:PORT/health`
   - Process check: `ssh USER@HOST "tasklist | findstr app"` or `pgrep`
   - Never assume deploy succeeded without verification

## Windows GUI Apps

GUI apps cannot be started via SSH directly (Session 0 limitation).
Use the `windows-remote-gui` skill for `schtasks`-based launching with `/it` flag.

## Troubleshooting

- If service fails to start, check logs: `ssh USER@HOST "journalctl -u SERVICE -n 50"` (Linux)
- If binary not found after copy, verify the path and permissions
- If health check fails, give the app time to initialize (up to 30 seconds), then investigate


---

# Deploy from a clean, committed tree (moved verbatim from modules/deploy/deploy-from-clean-tree.md, mdreview 2026-07-09)

### Deploy From a Clean, Committed Tree — Never rsync/scp a Dirty Working Directory

**Context gate — related rules you MUST also apply:**
- `post-deploy-verification.md` — after deploy, verify liveness + version + function on the live target
- `no-destructive-remote-actions.md` — overwriting remote files is destructive; the wrong bytes = data loss
- `git-fetch-first.md` — fetch before comparing/deploying so refs aren't stale
- `comprehensive-logging.md` — record the deployed commit SHA so drift is detectable later

**A deploy copies bytes to a live target. If those bytes come from a dirty working tree, an uncommitted edit — even an accidental one — ships straight to production with no review, no test, no record. This rule exists because exactly that happened: a stray file-write reverted a tracked source file mid-session, and an `rsync` of the working directory pushed the revert to a live production service. The git history was clean; the deployed bytes were not.**

#### The hard gate — `git status` MUST be empty before any deploy

Before ANY command that copies local files to a remote/production target — `rsync … host:`, `scp … host:`, `sftp`, `sshpass … scp`, `docker build` of the working dir, or any sync of the project directory:

1. **`git status --porcelain` MUST be empty** for tracked files. A dirty tree = STOP. Commit it, revert it, or stash it FIRST — never deploy over it.
2. **Deploy from a committed, pushed ref** — ideally the merged commit on the deploy branch. The working directory is for editing, NOT the deploy source of truth. Prefer `git archive <ref> | tar -x -C <staging>` or a clean checkout of the ref, then sync the staging dir. For built artifacts (binaries), build from a clean tree and record the SHA the binary was built at.
3. **Post-deploy diff-verify against HEAD** — after copying, hash/byte-compare each deployed file against the committed HEAD (`git hash-object` vs the remote file's hash, or `rsync --dry-run --checksum` showing zero diffs). Liveness ("process is up") does NOT prove the right bytes landed. The deploy is verified only when the remote matches the committed ref byte-for-byte.
4. **Record what was deployed on the target** — write the deployed commit SHA to the target (a `DEPLOYED_SHA` file, a version label, or the app's `/api/version`). A deploy with no recorded SHA is a deploy you cannot audit.

The completion-report `✅ Deploy:` line MUST state the deployed commit SHA and that the remote matches HEAD, e.g. `✅ Deploy: synced 9a048c5 (HEAD=origin/main) to prod, diff-verify clean — all 5 files byte-match HEAD`.

#### Enforcement vs. discipline — the hook covers a subset

A `pre-deploy-clean-tree.sh` PreToolUse hook is the automated backstop. It is **conservative / fail-closed**: any `rsync`/`scp`/`sftp`/`sshpass` (or `rsync://`) command naming a remote endpoint is BLOCKED while the tree is dirty — it does NOT try to prove the transfer is a push, because parsing shell direction is fragile and every miss fails open. A wrongly-blocked pull or remote-to-remote copy is one bypass token of friction; a missed dirty push is the incident. `--dry-run`/`-n` is allowed (transfers nothing).

The hook is a backstop, not the whole rule. **You are still responsible for the clean-tree gate on the vectors the hook does NOT watch:** `docker build` of the working dir, streaming pushes (`tar c … | ssh host "tar x"`, `cat f | ssh host "cat > …"`), and sftp batch/bare-host uploads (`sftp -b batch host`) that carry no `host:path` token. The rule applies to every way bytes leave the working tree for a live target; the hook enforces the argv-detectable subset that caused the incident.

#### Bypass (rare, explicit)

A genuine non-repo deploy (syncing a directory that is not a git checkout, or intentionally shipping local-only files) bypasses the hook with `AIRULESET_ALLOW_DIRTY_DEPLOY=1` or an inline `# airuleset:deploy-dirty-ok` marker in the command. Using the bypass to ship an uncommitted code change is the exact failure this rule prevents — NEVER use it to skip committing real source edits.

#### Anti-patterns (intent — all rewordings apply)

- `rsync -a ./ user@host:/srv/app/` from the project dir without checking `git status` first → **WRONG.** Dirty tree ships unreviewed.
- "I'll commit after I confirm the deploy works" → **WRONG.** Commit FIRST, deploy the commit. Deploy-then-commit means production ran code that was never in git.
- Deploying the working tree, then committing a *different* state than what shipped → **WRONG.** The remote and HEAD now silently diverge.
- "Liveness passed, deploy verified" → **WRONG.** Liveness proves the app started. Diff-verify against HEAD proves the *correct code* started.
- Trusting that the working tree equals HEAD because "I didn't run any git command" → **WRONG.** File-writes (stray edits, multi-agent work, reverts) change the tree without a git operation and leave no reflog entry. The only proof is `git status`.

Applies to all rewordings and semantic equivalents — the intent: the deploy source is always a clean, committed ref, verified byte-for-byte on the target.
