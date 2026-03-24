### Git Fetch First

Before any branch comparison, merge, or rebase operation, ALWAYS run:

```bash
git fetch origin
```

Before making ANY code changes on a branch, sync with remote:

```bash
git fetch origin && git merge origin/main
```

Never compare local branches without fetching first — local refs may be stale. A stale comparison leads to merge conflicts and wasted CI cycles.
