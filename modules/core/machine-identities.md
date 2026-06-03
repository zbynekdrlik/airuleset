### Machine Identities — dev1 & dev2 (don't probe, just know)

Two dev machines on the LAN, both user `newlevel`. Do NOT waste turns figuring out "which computer is this / where does X run" — the mapping is fixed:

| Name | Hostname | IP | Role |
|---|---|---|---|
| **dev1** | `develbox` | `10.77.9.21` | Primary workstation. Most projects live here. airuleset's source of truth; deploys to dev2. |
| **dev2** | `baking-ai-5060` | `10.77.8.134` | Secondary. GPU/CUDA workloads + a few projects (e.g. `bakerion-ai`, `presenter`, `codex-bridge`). |

- **Which am I on?** `hostname` → `develbox` = dev1, `baking-ai-5060` = dev2 (or `hostname -I` → first IP).
- **SSH:** dev1→dev2 `ssh newlevel@10.77.8.134`; dev2→dev1 `ssh newlevel@10.77.9.21`.
- **airuleset:** `python3 airuleset.py push` runs on dev1 and deploys to dev2 automatically.

When the user says "deploy to both" / "on dev2" / "the other machine", resolve it from this table — never ask which IP or re-discover the hosts.
