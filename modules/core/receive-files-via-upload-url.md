### Receiving Files FROM the User — ALWAYS a Web Upload URL, NEVER scp/sftp

**The user works over SSH and has NO local filesystem access to any managed box — in EITHER direction.** `deliver-files-as-urls.md` covers files TO the user (share URL); this is the INPUT counterpart (incident: david@gk asked the user to scp a file up, 2026-07-10 — "babrať sa cez scp" is the exact banned outcome). When you need a file FROM the user (a recording, an export, a photo, a config, anything):

1. Run `python3 ~/devel/airuleset/airuleset.py upload` (options: `--dir`, `--ttl`, `--port`). It stands up a drag-drop endpoint bound to EVERY private interface (tailscale + LAN, never the box's public IP — it is a WRITE endpoint), 200-checks each, and prints **one URL per interface**. Hand the user ALL of them — he opens whichever his current network (tailscale or LAN) reaches.
2. Hand the user THAT URL — they open it in their own Chrome and drop the file. Default destination `~/uploads/`; confirm receipt via `grep SAVED /tmp/airuleset-upload-<port>.log` and the file size before proceeding.

**BANNED (all rewordings and semantic equivalents):** asking the user to `scp` / `sftp` / `rsync` a file to the box, offering them scp command lines, asking for their SSH key so THEY can push a file, "pošli mi to cez scp / nahraj to na server cez terminál". The user provides files through a browser URL — never through a terminal transfer they must compose themselves.
