### Commit Conventions

- Use imperative mood in commit messages (e.g., "Add feature" not "Added feature").
- Keep messages concise and descriptive — explain the "why", not just the "what".
- **Never rewrite git history.** No `git reset`, no `git rebase -i`, no `git commit --amend`, no `git push --force`. Every commit stays as-is. If you made a mistake, fix it in a new commit.
- One push should work. If CI fails, the fix should be ONE commit that addresses ALL issues, not a stream of partial fixes.
- Never push multiple "fix CI" commits in a row — think before pushing.
