---
paths:
  - "**/*dante*"
  - "**/*Dante*"
---

### Dante Names and Patching Belong to dcontroller (#1402, owner 27.11.2026)

**dcontroller owns every Dante device and channel name, their grouping, the fleet name/connection map, renames and subscriptions (patching).** Its policy lives in `zbynekdrlik/dcontroller#164`. Owner, 27.11.2026: "nech sa nedeje že každý subprojekt claude si v týchto veciach robí čo chce a čo ho napadne".

Every other project (dasio, iemmixer, fohmixer, camera-box, dstack, restreamer, songplayer, …):

- **Never invent or change** a Dante device name, a channel label (`free 01`, bare numbers, ad-hoc words), a grouping, or another device's subscription on its own.
- **Ask dcontroller**: a ticket in `zbynekdrlik/dcontroller` (`Requested by owner/repo#N`), plus `peer_send` to `dcontroller` when its session is live. It is owner-started, so a ticket alone is the record when it is not running.
- **Apply only your own device's layout** exactly as dcontroller gives it. Your device config keeps the names dcontroller set.
- **A needed deviation is a dcontroller ticket**, never a local choice. Reading names or subscriptions, and debugging your own device's audio path, needs no ticket.
