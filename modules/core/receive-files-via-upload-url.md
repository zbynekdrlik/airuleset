### Receiving Files FROM the User — ALWAYS a Web Upload URL, NEVER scp/sftp

**The user works over SSH and has NO local filesystem access to any managed box — in EITHER direction.** `deliver-files-as-urls.md` covers files TO the user (share URL); this is the INPUT counterpart (incident: david@gk asked the user to scp a file up, 2026-07-10 — "babrať sa cez scp" is the exact banned outcome). When you need a file FROM the user (a recording, an export, a photo, a config, anything):

1. Run `python3 ~/devel/airuleset/airuleset.py upload` (options: `--dir`, `--ttl`, `--port`). It stands up a drag-drop endpoint bound to EVERY private interface (tailscale + LAN, never the box's public IP — it is a WRITE endpoint), 200-checks each, and prints **one URL per interface**. Hand the user ALL of them — he opens whichever his current network (tailscale or LAN) reaches.
2. Hand the user THAT URL — they open it in their own Chrome and drop the file. Default destination `~/uploads/`; confirm receipt via `grep SAVED ~/.claude/upload-logs/upload-<port>.log` (the CLI prints the exact path — it is per-user, since a shared `/tmp` name collided across the box's users, #115) and the file size before proceeding.

**BANNED (all rewordings and semantic equivalents):** asking the user to `scp` / `sftp` / `rsync` a file to the box, offering them scp command lines, asking for their SSH key so THEY can push a file, "pošli mi to cez scp / nahraj to na server cez terminál". The user provides files through a browser URL — never through a terminal transfer they must compose themselves. **A credential is NOT a file** — see below, `upload` is the wrong tool for it.

#### Receiving CREDENTIALS FROM the User — `secret request`/`secret exec`, NEVER `upload` or chat

**A password, API key, token, PAT, or connection string is a CREDENTIAL, not a file — `upload` above is the wrong channel for it, and pasting it into chat is worse.** When the user needs to give you a credential:

1. `python3 ~/devel/airuleset/airuleset.py secret request <NAME>` — prints a one-shot URL on PRIVATE interfaces only, defaulting to ENCRYPTED (tailscale) ones — a plaintext LAN URL is opt-in only, via `--allow-plain`. Hand the user THAT URL; they paste the value from their own browser. The session learns only that `<NAME>` is ready — never the value.
2. `python3 ~/devel/airuleset/airuleset.py secret exec <NAME> -- <cmd>` — runs `<cmd>` with the value handed to it (an env var named `<NAME>`, or `--stdin`); the command's own captured stdout/stderr are redacted before you see them, but a child that WRITES the value to a FILE is outside that filter — treat what the child does with the value as your own responsibility, same as any other command you run. `secret list` / `secret status <NAME>` read metadata only (never the value); `secret forget <NAME>` drops it early.

**When to reach for which:** the user is sending you BYTES you will read as-is (a recording, an export, a config, a screenshot) → `upload`. The user is sending you a SECRET you will only ever hand to another process, never read → `secret request` + `secret exec`.

**BANNED (all rewordings and semantic equivalents):** asking the user to paste a password/key/token/PAT/connection-string into chat ("send me the API key here", "paste the token", "čo je to heslo?"), and using `upload`/`share` for a credential. Both permanently write the value into the session transcript, where it survives compaction and cannot be revoked — the exact outcome `secret request`/`secret exec` exists to prevent. If a credential ever lands in chat by accident (the user pasted it unprompted), do not echo it back — have them run `secret request <NAME>` to resubmit it through the real channel (and rotate it if they can), then use `secret exec` from then on.
