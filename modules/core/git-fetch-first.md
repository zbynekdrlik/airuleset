### Git Fetch First

Before any branch comparison, merge, rebase, or starting code changes on a branch: `git fetch origin` (then `git merge origin/main` before edits). Stale local refs → wrong comparisons, conflicts, wasted CI cycles.

Largely hook-covered: `session-start-fetch.sh` (SessionStart) fetches automatically at session start and, when it is PROVABLY safe (clean tree, no in-progress git operation, a real fast-forward), also fast-forwards the local branch to match — so a long-lived, mostly-passive session's on-disk files (project CLAUDE.md included) never sit stale behind origin (#314); `pre-push-base-sync.sh` blocks a push that would create a conflicting PR. The un-hooked slice is YOURS: mid-session, fetch again before any branch comparison or merge decision — a session-start fetch goes stale over a long session.
