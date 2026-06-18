### Machine Identities — dev1 & dev2 (don't probe, just know)

Two dev machines, both user `newlevel`. Do NOT waste turns figuring out "which computer is this / where does X run" — the mapping is fixed:

| Name | Hostname | Tailscale (use this) | Role |
|---|---|---|---|
| **dev1** | `develbox` | `dev1` / `100.104.8.125` | Primary workstation. Most projects live here. airuleset's source of truth; deploys to dev2. Hosts the autopilot board + file-drop. |
| **dev2** | `baking-ai-5060` | `dev2` / `100.82.64.27` | Secondary. GPU/CUDA workloads + a few projects (e.g. `bakerion-ai`, `presenter`, `codex-bridge`). |

**Address by TAILSCALE, not the LAN IP.** The user switches the underlying LAN to a fallback network when equipment goes to external events, so the old `10.77.x` DHCP IPs change (dev1 was `10.77.9.21`, dev2 `10.77.8.134`). Tailscale IPs (`100.64.0.0/10`) and MagicDNS names (`dev1`/`dev2`) are assigned per-node and stay stable across those switches — always use them.

- **Which am I on?** `hostname` → `develbox` = dev1, `baking-ai-5060` = dev2 (or `tailscale ip -4`).
- **SSH:** dev1→dev2 `ssh newlevel@dev2` (or `…@100.82.64.27`); dev2→dev1 `ssh newlevel@dev1` (or `…@100.104.8.125`). MagicDNS is enabled, so the bare names resolve from any tailnet node. (One quirk: a node's OWN name resolves to `127.0.1.1` locally via `/etc/hosts` — fine for local use; remote nodes get the tailscale IP.)
- **airuleset:** `python3 airuleset.py push` runs on dev1 and deploys to dev2 automatically (over tailscale).

When the user says "deploy to both" / "on dev2" / "the other machine", resolve it from this table — never ask which IP or re-discover the hosts.
