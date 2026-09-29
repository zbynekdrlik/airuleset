---
paths:
  - "cli_account_bootstrap.py"
  - "cli_account_policy.py"
  - "cli_account_hardening.py"
  - "cli_accounts.py"
  - "tests/test_project_account*.py"
---

### airuleset internals — project accounts (#1184 / #1186)

- **Layout.** `cli_account_bootstrap` holds THE declaration (`SERVICE_ACCOUNTS`) and the root-script renderer. `cli_account_policy` is the sudo + reach validator leaf. `cli_account_hardening` holds the pure bash/nft/sudoers renderers. The bootstrap file sits at its size-ratchet ceiling, so new validation goes to the policy leaf, never back into the declaration file.
- **Golden locks.** `tests/fixtures/account_render_1186/` holds the fohmixer and claudy sudo+reach sections (`# 3a.` … `# 3c.`), byte-captured from main. Any change to the hardening renderers must keep them byte-identical, or re-capture them in the same commit with a stated reason. Put a new rule behind its feature (`lan_rules` non-empty), so accounts without that feature render unchanged.
- **Test root-only bash without root.** `nft -c` needs CAP_NET_ADMIN and `unshare -rn` is blocked on the controller. `visudo -cf <file>` works unprivileged, so use it for sudoers syntax. For the path/ancestor checks, stub `stat` and `runuser` as bash functions keyed on `${@: -1}`, then run the rendered snippet under `set -euo pipefail`. That covers the ancestor walk and the writable branch, which a real non-root run can never reach, because `runuser` refuses there and the check fails closed.
- **Reach entries are single hosts.** A LAN entry is ONE canonical private `/32`. The venue LAN also carries the password-shared dev boxes, which the fleet table knows only by tailscale IP. The uid reject covers tcp/22 only, so other ports are declared, not restricted. A declared LAN login is a possible `ssh -J` hop to the host's own `newlevel`. That makes it a go-live gate of the live migration, never something the render can close.
- **RED proof for review-fix tests.** After the fix is already in the working tree, run `git archive HEAD | tar -x -C <scratch>`, overlay the new test file, and run pytest there. That proves RED against the last commit without stashing or checking out. Mutation-verify the same way, by editing the export and never the worktree.
