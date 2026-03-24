### PR Merge Policy

- **NEVER merge a PR yourself.** Only the user may merge pull requests.
- Create the PR, ensure all CI checks are green, and provide the URL.
- Before sharing any PR URL, verify it is mergeable:
  ```bash
  gh api repos/OWNER/REPO/pulls/NUMBER --jq '{mergeable: .mergeable, mergeable_state: .mergeable_state}'
  ```
- The PR is ONLY ready when: `mergeable: true` AND `mergeable_state: "clean"`.
- If `mergeable_state` is "behind", sync branches first. If "blocked" or "dirty", fix the issues.
- Never provide a PR URL that has failing checks or merge conflicts.
