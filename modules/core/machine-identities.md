### Machine Identities — dev1 & dev2 (don't probe, just know)

Two dev machines, both user `newlevel`. Do NOT waste turns figuring out "which computer is this / where does X run" — the mapping is fixed:

| Name | Hostname | Tailscale (use this) | Role |
|---|---|---|---|
| **dev1** | `dev1` | `dev1` / `100.104.8.125` | Primary workstation. Most projects live here. airuleset's source of truth; deploys to dev2. Hosts the file-drop. |
| **dev2** | `dev2` | `dev2` / `100.82.64.27` | Secondary. GPU/CUDA workloads + a few projects (e.g. `bakerion-ai`, `presenter`, `codex-bridge`). |

**The system hostnames are now `dev1` / `dev2` themselves** — they were renamed (from the old `develbox` / `baking-ai-5060`) to match the tailscale / MagicDNS names, so hostname == name == MagicDNS for both. The tailscale IPs (`100.104.8.125` / `100.82.64.27`) are unchanged.

**Address by TAILSCALE, not the LAN IP.** The user switches the underlying LAN to a fallback network when equipment goes to external events, so the `10.77.x` DHCP IPs drift (e.g. dev1 currently `10.77.9.165`, dev2 `10.77.8.134` — these change). Tailscale IPs (`100.64.0.0/10`) and MagicDNS names (`dev1`/`dev2`) are assigned per-node and stay stable across those switches — always use them.

- **Which am I on?** `hostname` → `dev1` = dev1, `dev2` = dev2 (or `tailscale ip -4`: `100.104.8.125` = dev1, `100.82.64.27` = dev2).
- **SSH:** dev1→dev2 `ssh newlevel@dev2` (or `…@100.82.64.27`); dev2→dev1 `ssh newlevel@dev1` (or `…@100.104.8.125`). MagicDNS is enabled, so the bare names resolve from any tailnet node. (One quirk: a node's OWN name resolves to `127.0.1.1` locally via `/etc/hosts` — fine for local use; remote nodes get the tailscale IP.)
- **airuleset:** `python3 airuleset.py push` runs on dev1 and deploys to ALL managed targets automatically: dev2 (`newlevel`), the odoo-gatekeeper VPS (`gatekeeper` + `marek` users, tailscale `100.77.52.43` / public `202.148.55.31` — NEW box since 2026-07-05; the MagicDNS name `odoo-gatekeeper` still resolves to the OLD box `168.119.99.160` (kept as fallback), so never address it by name), and the isolated `montalu` user on dev1 itself (odoo montalu dev stream — own Linux user, NO sudo, NO prod keys, repo-scoped PAT; see odoo-erp #1322). Do not hand-edit those users' `~/.claude` — push manages them.

When the user says "deploy to both" / "on dev2" / "the other machine", resolve it from this table — never ask which IP or re-discover the hosts.
