### Migrating a legacy newlevel project to its own account (#1184)

A legacy project lives in the shared `newlevel` account (weak shared password, NOPASSWD sudo, every fleet key in its home). The owner froze these projects on 2026-09-29: every project gets its own unix account, with sudo declared explicitly (no network reach rule, #1232). The owner decides when a project moves (#1137: no session starts or files a migration on its own); `airuleset.py accounts status` lists what is still legacy. The recipe:

1. Declare the account in `cli_account_bootstrap.SERVICE_ACCOUNTS` in airuleset: host; sudo (default NO); secrets (default none); webterm humans; repo, project_dir and tmux_session.
2. Root runs `airuleset.py account-bootstrap --render <acct>` on the host. It creates the user and applies the declared sudo and a su/polkit lockout (so the account can never become `newlevel` through su/pkexec). Then it clones the repo and starts the tmux session.
3. Push `dev` first, then move the work to the new account:
   - add its `<acct>@<box>` `REMOTE_HOSTS` entry;
   - flip the registry row's `account`;
   - lower `cli_accounts.LEGACY_CEILING` by one.
4. Carry the Claude conversation over (#1190). After no claude runs in the old checkout any more, run `airuleset.py accounts transfer-session <acct> --from-dir <old checkout>` on the host. The dry run lists what moves. Then run it with `--render | sudo bash`. Resume with the printed `claude --resume <uuid>` in the account's tab.

Never add a new project, a new credential or new access to `newlevel`.
