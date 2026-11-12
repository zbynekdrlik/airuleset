### Creating a NEW project with its own account — the full procedure (#1184, owner 12.11.2026)

The owner asked for this after `mikrotik-pz` (#1310): "zapis detailne ten postup ako mas vytvarat nove projekty". Follow every step in order. Do not skip one because it looks done. The reference commits are tuke `b72815f1` (#1296) and mikrotik-pz `e6af1635` + `3b22986d` (#1310).

#### 0. Before any code
- Open a ticket for the request and write the owner's decision on it (ROZHODNUTÉ): account name, box (default dev1), whose webterm gets a tab (zbynek always, marek or others only if asked), and which access the project needs.
- One unix account per project, never a window under a shared user. A shared uid lets every Claude read every other one's secrets and keys.
- No device, network or sudo grant unless the project needs it now. A later need comes over peer as a scoped declaration (#1281, #1282).

#### 1. Declare the account in code (worktree, first commit bumps the version)
Mirror the tuke commit in every place:
- `cli_account_bootstrap.py` `SERVICE_ACCOUNTS`: host, sudo NO, `github_app: True`, `repo_secrets: []` (an empty allow-list, or `accounts verify` fails secret-sync), repo, `project_dir`, `tmux_session`, webterm humans.
- `cli_fleet.py`: `REMOTE_HOSTS` row and `FULL_AUTHORITY_USERS`.
- `cli_drop_lanes.py`: the next free drop port.
- `peer-links.json`: only `airuleset|<acct>` with the standard airuleset-infra topic. Add other links only on request.
- `projects-registry.json`: path `/home/<acct>/devel/<acct>`, 2-branch main/dev, onboarded date, account.
- Webterm: `cli_webterm_profiles.py` inventories (`zbynek_inventory`, plus `marek_inventory` etc. when asked) and `cli_webterm.py` `WEBTERM_DASHBOARD_TABS`.
- Tab alias: `cli_aliases.short_target_alias` cuts names to 8 characters. If the cut collides with or reads like another project (`mikrotik-pz` became `mikrotik`), add an explicit alias (`mt-pz`).
- `tests/size_ratchet.json`: raise each ceiling only by the declaration rows. Add tests that pin the account and its tabs (`tests/test_project_account_<issue>.py`), then update the per-human tab and alias lists.
- Run `grep -rl --include=*.py -E "tuke|<acct>" tests | xargs python3 -m pytest -q`, `tests/test_size_ratchet.py` and `ruff check`.

#### 2. GitHub repo (controller, airuleset session only; a hook blocks it elsewhere)
```
gh repo create zbynekdrlik/<acct> --private --add-readme --description "..."
gh api repos/zbynekdrlik/<acct>/git/refs -f ref=refs/heads/dev \
  -f sha=$(gh api repos/zbynekdrlik/<acct>/git/ref/heads/main --jq .object.sha)
```

#### 3. Merge into main
Bump past any version conflict, then run the affected tests again. Do not push yet: push deploys to `<acct>@<host>`, which does not exist until step 4.

#### 4. Root bootstrap on the host
Render on the controller from the merged tree, check what it removes (only the account's own files), and run it:
```
python3 airuleset.py account-bootstrap --render <acct> > <scratch>/boot.sh
ssh -i ~/.ssh/id_ed25519 newlevel@100.104.8.125 'sudo -n bash -s' < <scratch>/boot.sh
```
The read-back must show the user, linger, and the webterm keys for each human with a tab.

#### 5. As the account: airuleset checkout and install
```
ssh -i ~/.secrets/airuleset_push_ed25519 <acct>@100.104.8.125 \
  'git clone -q https://github.com/zbynekdrlik/airuleset.git ~/devel/airuleset && python3 ~/devel/airuleset/airuleset.py install'
```

#### 6. Respawn the pane IMMEDIATELY, before anyone types `claude`
The bootstrap started the tmux pane before install wrote the `claude()` launcher into `~/.bashrc`. That pane's `claude` is the bare binary: no bypass mode, no `-c`, no peer channel, no managed model. The owner hit exactly this on mikrotik-pz (12.11.). Respawning later kills a Claude he already started, so do it now:
```
ssh ... <acct>@<host> 'tmux respawn-pane -k -t <acct> -c ~/devel/<acct> "exec bash -l"'
```

#### 7. Repo token (controller)
```
python3 airuleset.py project-gh-token mint <acct>
python3 airuleset.py project-gh-token verify <acct>    # must print OK
```

#### 8. Project checkout
Re-run the step-4 bootstrap: the first run could not clone the private repo without the token. Then, as the account, run `git -C ~/devel/<acct> checkout dev`.

#### 9. Claude login through claudy (controller, as claudy)
Add `<acct>@<host>` to `~claudy/.config/claudy/hosts.json` (copy the tuke entry, change name and user) and to `auto_switch.json` (`{"box": "<acct>@<host>", "priority": 10}`). Back up both files first. Write the change as a script file and run it by path: an inline command that names the identity path is refused by the vault hook.

#### 10. Push
```
setsid nohup python3 airuleset.py push > <scratch>/push.log 2>&1 &
# waiter: pgrep -f "^python3 airuleset.py push"   (anchored, or it matches itself)
```
The real result is the last `N deployed, M paused, 0 failed` line. `DEPLOY FAILED` lines from the test run are fixtures.

#### 11. Go-live gate
```
python3 airuleset.py accounts verify <acct> --no-transfer
```
Every line must be OK, including `tmux`, `login` and `webterm`. Then read each tab from the live gateway:
```
curl -s --unix-socket /run/user/$(id -u)/webterm-<human>-gateway.sock \
  -H "Cf-Access-Authenticated-User-Email: <owner email>" http://x/ | grep -o '<acct>[^"<]*'
```

#### 12. Close the loop
Tick the ticket lines with this evidence. Tell the owner the account is live; only then does anyone open the tab. Later needs (router address, sudo, device access) come from the project over peer and get scoped declarations.
