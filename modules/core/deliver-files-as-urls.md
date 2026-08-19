### Delivering Files to the User — ALWAYS a Clickable LAN URL, NEVER a /tmp Path → on-demand skill `deliver-files-as-urls`

The full protocol moved VERBATIM to the `deliver-files-as-urls` skill — load it whenever producing a file the user needs to open or download. Non-negotiable that survives here: the user has NO filesystem access to the dev machines — a `/tmp/...` path is USELESS to them. Every deliverable file goes through `python3 ~/devel/airuleset/airuleset.py share <path-to-file>` (never a bare path, never "can't be linked"). See the input counterpart `receive-files-via-upload-url.md` for the opposite direction.

**A credential is NOT a file** — deliver it TO the owner with `secret show` (a one-shot render URL that shows the value once, then tears down), never `share`, a `/tmp` path or chat, and never "run `cat` yourself". A credential has its own channel in BOTH directions (`secret request` in, `secret show` out) — see `receive-files-via-upload-url.md`.
