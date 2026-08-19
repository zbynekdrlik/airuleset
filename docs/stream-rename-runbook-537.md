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
| 5b | **Claude Code per-project STATE migration (#561, live regression: "na montalu1 v claude nemám žiadnu históriu").** CC keys ALL per-project state by PATH, so a home rename leaves transcripts/auto-memory/prompt-history keyed under `/home/<old>/...` and the new session sees an EMPTY project. Runs AFTER the home move (step 4), BEFORE the session relaunch (step 13) — the reverse order forces the merge branch. (a) rename `/home/<new>/.claude/projects/-home-<old>-*` dirs → `-home-<new>-*` (if a target already exists from a premature launch, merge file-wise, fresh-wins); (b) rewrite `/home/<new>/.claude.json` → `projects` keys `/home/<old>/...` → `/home/<new>/...` (atomic write + `.bak`; if the new key already exists, merge fresh-wins + concatenate the history list) | newest transcript reachable under the NEW key: `ls -t /home/<new>/.claude/projects/-home-<new>-*/ \| head` shows a recent `*.jsonl`, and `~/.claude.json` has NO residual `/home/<old>/` project key |
| 6 | Confirm ssh still works from dev1 with the SAME identity as the base | montalu1: default key; david1/simap1: `-i ~/.secrets/gatekeeper_access_ed25519` — `ssh <new>@100.118.174.27 true` | exit 0 (fail2ban-exact identities per #495) |

## Repo-side flip (once the account is live + verified)

Do these in `zbynekdrlik/airuleset` (a normal PR through the two-branch flow):

| # | Step | Where | Verify |
|---|------|-------|--------|
| 7 | Remove `"pending": True` from the `<new>@subdev` `REMOTE_HOSTS` entry | `cli_fleet.py` | `python3 -c "import airuleset; print(any(h['user']=='<new>' for h in airuleset._deployable_hosts()))"` → `True` |
| 8 | Remove the `<old>@subdev` `REMOTE_HOSTS` entry AND the `<old>` `AUTHORITY_BY_USER` entry | `cli_fleet.py` | `python3 -c "import airuleset; print('<old>' in airuleset.AUTHORITY_BY_USER)"` → `False` |
| 8b | **Verify EVERY `stream:%s` construction site is alias-covered (#561) — removing the `<old>` key in step 8 is EXACTLY what triggered the gk `core-quals` 5→61 leak (the #537 staging wired the alias into only 2 of the recognition sites).** The single primitive is `_stream_rename_equivalents()` (backed by `STREAM_RENAME_ALIASES`); every site mapping a `stream:<user>` label to a known stream MUST route through it, never enumerate `AUTHORITY_BY_USER`/`_REDUCED_STREAM_USERS` keys directly. Alias-covered sites (#561 audit): `_core_search_excl`, `_stream_owner_of`, `_slice_quals`, `_ticket_is_stream_labeled` (cli_quals.py); `_bounce_quals` full-authority branch (watchdog/cross_stream.py). STILL on old names (separate subsystems, #561 follow-ups — verify/handle at rename): the `--stream-label` close-exemption (cli_quals.py `cmd_authority` + `hooks/block-fork-no-merge-issue-close.sh`, exact-match) and the gkreq `handed-by:` provenance (`_origin_reduced_stream` + `_REDUCED_STREAM_USERS`, watchdog/cross_stream.py). | `python3 -m unittest tests.test_stream_rename_aliases` → `OK` (covers `_core_search_excl`/`_stream_owner_of`/`_bounce_quals` against a post-rename-only `AUTHORITY_BY_USER`) |
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
| 12b | **gh-app-stream-tokens delivery list** (`streams.conf`, odoo-erp #3281 — deployed `/opt/gh-app-stream-tokens/scripts/gh-app/streams.conf` on gk, SYSTEM timer `gh-app-stream-tokens.timer` every 45 min; discovered live during the montalu1 rename — the timer ssh-strikes the DEAD old account (fail2ban risk) and the new account gets no token, so `gh`/slice-quals die within 60 min) | edit the deployed conf line(s) `<old>\|` → `<new>\|` on gk + run `push-stream-tokens.sh` once — this is the LIVE `/opt` half ONLY; the DURABLE repo-side align of the SAME file is the EXECUTED step **12c**, applied ATOMICALLY with this hotfix (without it the next `install-gatekeeper-timer.sh` reverts this /opt edit). montalu1 repo precedent: zbynekdrlik/odoo-erp#4475 | delivery log prints `delivered: stream=<new>`; `slice-quals --count` works on the box |
| 12c | **REPO source of the SAME `streams.conf`** — the committed `scripts/gh-app/streams.conf` in `zbynekdrlik/odoo-erp` (the DURABLE half of the 12b register; `install-gatekeeper-timer.sh` re-copies this committed file → `/opt` on every gk deploy, so 12b's /opt hotfix is only as permanent as this repo file) | Land the repo-align that mirrors 12b's live hotfix — the streams.conf DATA row(s) (`<old>\|`→`<new>\|`) PLUS the coupled test/doc + CHANGELOG — **atomically** with the 12b /opt hotfix (same rename window), coordinated on **odoo-erp#4518** (the channel the fleet committed to: announce the rename in advance + post evidence). **Load-bearing, NOT optional:** a stale committed file makes the NEXT `install-gatekeeper-timer.sh` REVERT 12b and the renamed account silently loses tokens (`gh`/slice-quals die within ~45–60 min) — the montalu failure (odoo-erp#4475/#4479, anomaly write-up #4518). ⚠️ **Ordering:** the repo-align must NOT merge BEFORE the account rename — a committed `<new>\|` row while the account is still `<old>` delivers the token to a non-existent `/home/<new>/`; apply it atomically with the rename, never ahead of it. **david1:** ready align = **odoo-erp#4535** (mirror of montalu's #4479), ops-wait/blocked-on-rename; david has **TWO** data rows + a **commented-out fork line** in gk `streams.conf` (identity key `gatekeeper_access_ed25519`) — align BOTH data rows, leave the commented fork line as-is | after landing atomically with 12b, deployed `/opt/.../streams.conf` == committed repo file (`diff` on gk empty), so a re-run of `install-gatekeeper-timer.sh` is a no-op and cannot revert; delivery log still prints `delivered: stream=<new>` |
| 13 | **Relaunch the stream's tmux session as an EXECUTED step (#537 gap 2), NOT deferred to "next SSH login" — a deferred relaunch left montalu1's session (69 open tickets) DEAD ~8h after the rename.** Ordered AFTER the CC state migration (step 5b). Start a DETACHED session named `<new>`, cwd = the stream's REAL working dir (montalu1's is `~/devel/odoo`, NOT the conventional `devel/odoo/odoo-erp` which doesn't exist there; the attach block falls back to `$HOME`, so use the ACTUAL project cwd, verified from the stream's transcript project keys in step 5b), launcher `~/.claude/airuleset-claude-launch.sh`: `sudo -u <new> tmux new-session -d -s <new> -c <real-cwd> ~/.claude/airuleset-claude-launch.sh` | `sudo -u <new> tmux list-panes -t <new> -F '#{pane_current_command}'` shows `claude` AND `pgrep -u <new> -x claude` returns a pid |

## Provisioning + final verification (per stream)

| # | Step | Command (from dev1) | Verify |
|---|------|---------------------|--------|
| 14 | Deploy + re-provision the renamed account | `python3 airuleset.py push` | push log shows `<new>@subdev` deployed, no FAILED; soniox key delivered |
| 15 | Soniox key present | `ssh <new>@100.118.174.27 'test -s ~/.soniox.env && echo OK'` | `OK` |
| 16 | Watchdog timer active | `ssh <new>@100.118.174.27 'systemctl --user is-active api-watchdog.timer'` (the unit is `api-watchdog.timer` — verified live during the simap1 rename; an earlier draft named a nonexistent `airuleset-watchdog.timer`) | `active` |
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
- **`streams.conf` lives in TWO coupled copies — align BOTH atomically at rename (steps 12b + 12c).** The deployed `/opt/gh-app-stream-tokens/scripts/gh-app/streams.conf` on gk (the LIVE token-delivery source, 12b) and the committed `scripts/gh-app/streams.conf` in `zbynekdrlik/odoo-erp` (the DURABLE source, 12c). `install-gatekeeper-timer.sh` re-deploys the committed file → /opt, so a /opt hotfix WITHOUT the repo align is reverted on the next gk deploy — the reactive montalu dorovnávanie (odoo-erp#4475/#4479/#4518). Coordinate every such rename on odoo-erp#4518; never merge the repo-align before the account rename. For david1 the repo-align is ready in odoo-erp#4535 (blocked on rename).
- **A rename touches THREE state layers the #537 staging missed (all live regressions on the montalu1 rename, fixed under #561), each now an EXECUTED step with the ordering `state migration (5b) → session relaunch (13) → verify (14–18)`:** (1) **partition/ownership recognition** — every `stream:%s` construction site must route through `_stream_rename_equivalents()`, verified at step 8b (the leak: `core-quals` jumped 5→61 the moment step 8 removed the old `AUTHORITY_BY_USER` key); (2) **CC per-project state** — transcripts/memory/prompt-history are keyed by PATH and must be migrated to the new home path at step 5b, BEFORE any session launches under the new name (else the session sees an empty project and the migration hits the merge branch); (3) **tmux session relaunch** — executed at step 13, never deferred to "next SSH login" (a deferred relaunch left the stream dead ~8h). **All three apply to `david1` at its re-entry.**
