---
paths:
  - "**/Cargo.toml"
  - "**/src/**/*.rs"
  - "**/crates/**"
---

### Rust Web App on a Desktop → Ships a Tray Icon

**Scope:** a Rust app that serves a web UI (axum, axum-server, actix-web, warp, tiny_http, rocket, poem, salvo, tide, ntex) and runs on a desktop machine.

Such an app ships a system-tray icon next to its service. The tray has:

- **Status:** the service state, shown in the tooltip or the menu.
- **Open <app>:** opens the local web UI URL in the browser.
- **Copy URL:** copies that URL to the clipboard.
- **Exit:** ends the tray only. The service keeps running.

The tray autostarts at the desktop user's logon, next to the service.

Reference implementation: iemmixer `crates/iem-tray`.

`onboard-project` files a foundation ticket for a Rust web app with no tray dependency (`tray-icon`, `tray-item`, `ksni`, `trayicon`, `systray`), no `tauri` dependency with the `tray-icon` or `system-tray` feature, and no `*-tray` crate. `--audit` reports it as `missing-tray` (#1198, owner 2026-09-30, after fohmixer went live without one).
