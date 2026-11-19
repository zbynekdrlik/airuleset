### Delivering Files to the User — ALWAYS a Clickable URL, NEVER a /tmp Path → on-demand skill `deliver-files-as-urls`

Protocol: the `deliver-files-as-urls` skill — load it when producing a file the user needs to open or download. Non-negotiable that survives here: the user has NO filesystem access to the dev machines — a `/tmp/...` path is USELESS to them. Every deliverable file goes through `python3 ~/devel/airuleset/airuleset.py share <path-to-file>` — it prints ONE public HTTPS URL, or NO URL + exit 1 when the public lane is dead/missing. NEVER hand the owner a tailscale/LAN URL (owner 30.9., #1192: tailscale opens boxes that must not be reachable); `--private` exists only when he explicitly asks for one.

**A design for owner approval (#1226):** `docs/mockups/<topic>-vN.html`, sent as a `raw.githack.com` URL pinned to the SHA. Never a claude.ai URL.

**A credential is NOT a file** — deliver via `secret show` (one-shot URL, `❓`/`needs-owner-action` #879), never `share`/chat/`cat`. See `receive-files-via-upload-url.md`.

**A message that REFERENCES an openable thing carries its functional URL** (owner 2026-08-20, #595). EVERY message a reader can act on — a client handover / Odoo Discuss message, a Discord ping, a completion-report line — that names something the reader could OPEN (a feature, record, screen, report, dashboard) MUST carry the direct FUNCTIONAL URL — the deep link to that exact action/record/screen, verified live (loads) before sending — never only a prose menu path (`Predaj ▸ Objednávky ▸ …`) and never only the bare homepage. Incident: montalu Discuss msg 1723308, menu path only, rejected. This generalizes `completion-report.md`'s 🌐-line rule to client-facing messages, not just the report. Applies to all rewordings.

**Published app = box tunnel + Cloudflare Access** (`airuleset.py publish-app`, #1370): venue-LAN bypass only per app the OWNER confirms; never a router change; ask airuleset, not the owner.
