### Receiving Files FROM the User — ALWAYS a Web Upload URL, NEVER scp/sftp

**The user works over SSH and has NO local filesystem access to any managed box — in EITHER direction.** `deliver-files-as-urls.md` covers files TO the user (share URL); this is the INPUT counterpart (incident: david@gk asked the user to scp a file up, 2026-07-10 — "babrať sa cez scp" is the exact banned outcome). When you need a file FROM the user (a recording, an export, a photo, a config, anything):

1. Run `python3 ~/devel/airuleset/airuleset.py upload` (options: `--dir`, `--ttl`). It stands up a drag-drop endpoint behind the box's public TLS drop lane and prints ONE public URL `https://drop-<box>.newlevel.media/<token>/` after checking it is alive — or NO URL + exit 1 when the lane is dead/missing (fix the lane, never fall back to tailscale/LAN: owner 30.9., #1192). `--private` (tailscale/LAN) only on the owner's explicit request.
2. Hand the user THAT URL — they open it in their own Chrome and drop the file. Default destination `~/uploads/`; confirm receipt via `grep SAVED ~/.claude/upload-logs/upload-<port>.log` (the CLI prints the exact path — it is per-user, since a shared `/tmp` name collided across the box's users, #115) and the file size before proceeding. **If data under `~/uploads` must live permanently** (a serve root, a persistent dataset), place a `.airuleset-keep` marker file in that subtree — the disk-guard uploads sweep (#861) will skip the entire subtree.

**BANNED (all rewordings and semantic equivalents):** asking the user to `scp` / `sftp` / `rsync` a file to the box, offering them scp command lines, asking for their SSH key so THEY can push a file, "pošli mi to cez scp / nahraj to na server cez terminál". The user provides files through a browser URL — never through a terminal transfer they must compose themselves. **A credential is NOT a file** — see below, `upload` is the wrong tool for it.

**NEVER hand-write ssh -L / PowerShell tunnel instructions (#664).** Go-live per box/account is a one-time `airuleset.py drop-gateway --apply` (+ a DNS CNAME); `secret request`/`secret show` follow the same public-only rule.

#### Receiving CREDENTIALS FROM the User — `secret request`/`secret exec`, NEVER `upload` or chat

A credential is NOT a file — `secret request` receives it safely, `secret exec` uses it without exposing it, `secret show` delivers one to the owner. **`secret show` URL = OWNER ACTION (#879):** deliver via `❓` block + `needs-owner-action` (→ `U`), never a report line; use `--ttl 3600`; hook-backstopped. BANNED: pasting a password/key/token into chat (permanently in the transcript). Full credential/vault mechanics (persistence, delivery, `--persist`, one-shot render URLs): companion `skills/receive-files-credentials/DEEP.md` (#859).
