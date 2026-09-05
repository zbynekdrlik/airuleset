### GitHub CLI (`gh`) — Canonical Recipes, Never Trial-and-Error Flags

**A `gh` flag you GUESSED and that just failed will fail again — do NOT retry the same invented flag.** The `block-gh-invalid-json-flag.sh` hook hard-blocks the invalid flag and prints the recipes.

**Body with backticks / `$` / `%` → write a FILE and pass `-F`/`--body-file`. NEVER an inline `--body`.** For `gh api`: lowercase `-f` is a literal string, uppercase `-F` reads a file (`@filename`). The full recipe set (create+capture, read fields, labels, PR title/body edit via API, auto-close negation awareness) is in the situational companion `skills/gh-cli-recipes-deep/DEEP.md` — loaded automatically on `gh` commands.
