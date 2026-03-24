### Commit Conventions

- Use imperative mood in commit messages (e.g., "Add feature" not "Added feature").
- Keep messages concise and descriptive — explain the "why", not just the "what".
- No fixup commits in PRs — squash or amend locally before pushing.
- One push should work. If CI fails, the fix should be ONE commit that addresses ALL issues, not a stream of partial fixes.
- Never push multiple "fix CI" commits in a row — think before pushing.
