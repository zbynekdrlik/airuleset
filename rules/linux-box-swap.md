---
paths:
  - "**/*provision*"
  - "**/*provision*/**"
  - "**/user-data"
  - "**/cloud-config*"
  - "**/*bootstrap*.sh"
  - "**/setup*.sh"
  - "**/*install*.sh"
  - "**/cloud-init*"
  - "**/ansible/**"
---

### Every Linux box MUST have active swap (#1235)

**Owner ruling 14.10.2026, after stream-pp had no swap and the OOM killer took down OBS's browser source, the control-room Chrome and restreamer.service:**

> „To ze tam nieje swap je hrube a opakovane porusenie pravidiel pre linux boxy … aby som to neriesil dokolecka ze niekde sa urobil novy linuxovy box a nema swap a crashuje na nedostatku pamete!!!!"

- **Every Linux box you provision or manage MUST have active swap.** Size it to RAM, clamped to 2-8 GB like airuleset's own `provision_swap` (more than 2x RAM only wastes disk), and persist it in `/etc/fstab` so it survives a reboot. A container is not a box: its host's swap counts.
- **A provisioning script or flow CREATES the swap and VERIFIES it.**
  - Typical create step: `fallocate -l <size> /swapfile` (or `dd`), `chmod 600`, `mkswap`, `swapon`, and an fstab line.
  - Verify step: `swapon --show` must be non-empty, otherwise the step FAILS.
  - A provisioning run that ends on a box without swap is not done.
- **A box found without swap is a defect.** Fix it now, or ticket it now in the project that owns the box. Never treat it as normal, and never "note it for later".
- airuleset's own fleet boxes get swap at every `install` (`cli_disk_guard_root.provision_swap`), and `airuleset.py status` prints the swap line. This rule exists for the boxes a PROJECT builds (camera-box stream boxes, customer VPS, new Linux PCs).
