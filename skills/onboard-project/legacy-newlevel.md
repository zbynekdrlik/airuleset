### This is a legacy newlevel project — migrate it to its own account as part of this work (#1184)

You are editing a project that lives in the shared `newlevel` account (weak shared password, NOPASSWD sudo, every fleet key in its home). The owner froze these projects on 2026-09-29: every project gets its own unix account, with sudo and reach declared explicitly. A legacy project moves when it is worked on (migrate-on-touch).

As part of THIS ticket, plan the migration unless the ticket says otherwise:

1. Declare the account in `cli_account_bootstrap.SERVICE_ACCOUNTS` in airuleset: host; sudo (default NO); reach and secrets (default none); webterm humans; repo, project_dir and tmux_session. File it as an airuleset ticket from a project session.
2. Root runs `airuleset.py account-bootstrap --render <acct>` on the host. It creates the user and applies the declared sudo, a uid-keyed ssh egress rule and a su/polkit lockout (so the account can never become `newlevel`). Then it clones the repo and starts the tmux session.
3. Push `dev` first, then move the work to the new account:
   - add its `<acct>@<box>` `REMOTE_HOSTS` entry;
   - flip the registry row's `account`;
   - lower `cli_accounts.LEGACY_CEILING` by one.

Never add a new project, a new credential or new access to `newlevel`. `airuleset.py accounts status` lists what is still legacy.
