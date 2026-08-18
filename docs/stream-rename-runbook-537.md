# Base-stream rename runbook (#537)

**Owner directive (2026-08-18, on #532):** the unnumbered base streams violate
the `<name><N>` convention — rename `montalu → montalu1`, `david → david1`,
`simap → simap1`. **`marek` STAYS `marek`** (NOT renamed; possible future
removal is a separate owner decision, out of scope).

This is the LIVE-OP checklist the **supervisor** executes, **one stream at a
time, in that stream's own quiet window** (running sessions must be ended
cleanly, never killed blind). The repo-side prep (alias layer + registers +
pending fleet targets) already landed via #537's PR — this runbook is what turns
each `pending` entry into a live account and retires the old name.

## What the repo already prepared (do NOT redo)

- `STREAM_RENAME_ALIASES` (`cli_fleet.py`) = `{montalu: montalu1, david: david1,
  simap: simap1}` — the single source of truth for the rename.
- `AUTHORITY_BY_USER` carries `montalu1`/`david1`/`simap1` **alongside** the base
  names (same profile each), so the moment a box runs as the new name it
  resolves correctly.
- `_slice_quals()` / `_ticket_is_stream_labeled()` accept **both** old and new
  `stream:<name>` labels (symmetric alias) — old tickets keep working during the
  transition with **zero GitHub relabel**.
- `REMOTE_HOSTS` carries `montalu1@subdev`/`david1@subdev`/`simap1@subdev`
  flagged `"pending": True`; `_deployable_hosts()` filters them out of **every**
  ssh path (deploy loop + soniox), so push never strikes a not-yet-existent
  account (fail2ban).

## Rename mechanic — Approach 1 (in-place `usermod`), per stream

Recommended (design comment on #537): rename in place on the SAME box (subdev,
tailscale `100.118.174.27`). Preserves uid, `~/.ssh/authorized_keys`, the whole
`~/.claude` managed tree, per-user secrets (`~/.claude/secrets/*.env`,
`~/.soniox.env`) and gh auth (`~/.config/gh` + `~/.config/gh-app-tokens`) — all
move with the home dir under `-m`. Fallback if in-place fails: Approach 2
(new-account + rsync + ForceCommand redirect, the #33 / odoo-erp#1895 pattern).

For each stream, substitute `<old>`/`<new>` (e.g. `montalu`/`montalu1`):

| # | Step | Command (run as root on subdev) | Verify |
|---|------|----------------------------------|--------|
| 1 | Confirm quiet window — no live `<old>` claude/tmux session doing real work; end it cleanly with the owner | `sudo -u <old> tmux ls; ps -u <old> -o pid,etimes,cmd` | operator confirms no in-flight work |
| 2 | Ensure no `<old>` processes hold the account (usermod refuses otherwise) | `pgrep -u <old>` | empty output |
| 3 | Rename the login | `usermod -l <new> <old>` | `id <new>` resolves; `id <old>` fails |
| 4 | Move + rename the home dir | `usermod -d /home/<new> -m <new>` | `ls -ld /home/<new>`; `ls -ld /home/<old>` absent |
| 5 | (optional) rename the primary group for tidiness | `groupmod -n <new> <old>` | `id <new>` shows `<new>` group |
| 6 | Confirm ssh still works from dev1 with the SAME identity as the base | montalu1: default key; david1/simap1: `-i ~/.secrets/gatekeeper_access_ed25519` — `ssh <new>@100.118.174.27 true` | exit 0 (fail2ban-exact identities per #495) |

## Repo-side flip (once the account is live + verified)

Do these in `zbynekdrlik/airuleset` (a normal PR through the two-branch flow):

| # | Step | Where | Verify |
|---|------|-------|--------|
| 7 | Remove `"pending": True` from the `<new>@subdev` `REMOTE_HOSTS` entry | `cli_fleet.py` | `python3 -c "import airuleset; print(any(h['user']=='<new>' for h in airuleset._deployable_hosts()))"` → `True` |
| 8 | Remove the `<old>@subdev` `REMOTE_HOSTS` entry AND the `<old>` `AUTHORITY_BY_USER` entry | `cli_fleet.py` | `python3 -c "import airuleset; print('<old>' in airuleset.AUTHORITY_BY_USER)"` → `False` |
| 9 | Keep `STREAM_RENAME_ALIASES` for now (old tickets still carry `stream:<old>`) — the alias keeps recognising them after step 8; drop the alias entry only when no open `stream:<old>` ticket remains | `cli_fleet.py` | `_ticket_is_stream_labeled([{ "name": "stream:<old>" }])` still `True` |

## Non-repo registers that key on the NAME (live-op, each with its verify)

The onboarding checklist (`cli_remote.py` registration-gap message) names every
register a stream account touches. Repo-side #537 did REMOTE_HOSTS +
AUTHORITY_BY_USER + the alias. The rest are live-op:

| # | Register | Action | Verify |
|---|----------|--------|--------|
| 10 | `notify.STREAM_NOTIFY_OWNER` (`notify/__init__.py`) | add `"<new>": "<owner>"` (montalu1→zbynek, david1→david, simap1→zbynek — mirror the base's routing); drop `<old>` | `python3 -c "import notify,unittest.mock as m,os;\n… resolve_owner() for <new>"` → expected owner; a REAL `notify-delivery.log` ping confirms (new-subdev §6a) |
| 11 | `watchdog._REDUCED_STREAM_USERS` + any `_CROSS_STREAM` / bounce scoping (`watchdog/`) | add `<new>`; drop `<old>` — **coordinate with the watchdog owner (#535 conformance lane); `_bounce_quals` scopes by the `/home/<name>` path, so it follows the account automatically once the home is `/home/<new>`** | `python3 -c "import watchdog as w; print(w._bounce_quals('/home/<new>/devel/odoo-erp'))"` → `['label:stream:<new>']` |
| 12 | `hooks/block-subdev-ssh-misuse.sh` allow-list | add `<new>`; drop `<old>` | grep the hook for `<new>` |
| 13 | tmux session name / `apply_stream_ssh_attach` | the account's next SSH login re-creates its session under `<new>` (linger) | `sudo -u <new> tmux ls` shows the `<new>` session |

## Provisioning + final verification (per stream)

| # | Step | Command (from dev1) | Verify |
|---|------|---------------------|--------|
| 14 | Deploy + re-provision the renamed account | `python3 airuleset.py push` | push log shows `<new>@subdev` deployed, no FAILED; soniox key delivered |
| 15 | Soniox key present | `ssh <new>@100.118.174.27 'test -s ~/.soniox.env && echo OK'` | `OK` |
| 16 | Watchdog timer active | `ssh <new>@100.118.174.27 'systemctl --user is-active airuleset-watchdog.timer'` | `active` |
| 17 | `slice-quals` works on the new name (the base activity gate) | `ssh <new>@100.118.174.27 'cd ~/devel/odoo-erp && python3 ~/devel/airuleset/airuleset.py slice-quals --count'` | an integer, no refusal — proves the alias slice resolves both `stream:<old>` and `stream:<new>` |
| 18 | Statusline footer scopes correctly on `<new>` | open a `<new>` session, check the `I N` count | counts the stream's own slice |

## Notes

- **gh identity is unchanged by the unix rename.** `STREAM_APP_BOT_LOGIN =
  "app/odoo-erp-stream-tokens"` is a fixed App-installation identity, independent
  of `$USER`; the token files move with the home dir (step 4). No gh re-auth
  needed. Verify: `ssh <new>@… 'gh api user 2>&1 | head -1'` (App-token box 403s
  structurally — that is the expected, unchanged signal, not a regression).
- **`stream:<name>` label migration = alias, NOT bulk relabel.** No historical
  odoo-erp ticket is relabelled; the alias layer makes `stream:<old>` keep
  working. An optional lazy relabel is possible later but is not part of #537.
- **`marek` is never in any step here.** It stays `marek`.
