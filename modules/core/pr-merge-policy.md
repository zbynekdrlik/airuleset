### PR Merge Policy

**NEVER merge a PR unless the user explicitly tells you to.** Phrases like "merge it", "approved", "go ahead" are the only valid triggers. A green CI is NOT permission to merge. Completed work is NOT permission to merge. Only an explicit user instruction is.

Your responsibility:

1. Create the PR from `dev` to `main`
2. Ensure all CI checks are green
3. Verify the PR is mergeable:
   ```bash
   gh api repos/OWNER/REPO/pulls/NUMBER --jq '{mergeable: .mergeable, mergeable_state: .mergeable_state}'
   ```
4. The PR is ready when: `mergeable: true` AND `mergeable_state: "clean"`
5. If "behind", sync branches first. If "blocked" or "dirty", fix the issues.
6. Provide the green PR URL and WAIT for the user's explicit merge instruction.

**Never provide a PR URL that has failing checks or merge conflicts.**
