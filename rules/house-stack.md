---
paths:
  - "**/*.rs"
  - "**/src/**/*.rs"
  - "**/Cargo.toml"
  - "**/Trunk.toml"
  - "**/index.html"
  - "**/crates/**"
---

### House Stack — Every App Is Built On It (MANDATORY, #1333)

**Every new app or component (service, web UI, desktop tool, graphics/video engine) is built on the house stack below — the same shape as our other Rust projects. Read this BEFORE choosing any technology, in an empty repo too.** Owner, 21.11.2026: projects "postavane na random adhoc zvolenych technologiach ... sa potom musia prerabat". Code inside an existing project follows the stack its `CLAUDE.md` `## Stack` section pins.

| Layer | Technology (non-negotiable) | Reference |
|---|---|---|
| Workspace | Rust Cargo workspace, crates `crates/<prefix>-*` | wall-bridge `wb-*` |
| Service | axum + tokio; `GET /api/status` (JSON incl. version); WebSocket for live state | wall-bridge `wb-service` |
| Web UI | Leptos (latest 0.8.x) CSR → WASM (`wasm32-unknown-unknown`), built by Trunk, served by the axum service; PWA; version visible on the dashboard | fohmixer `crates/fohmixer-ui` |
| Tray | `tray-icon` in a `<prefix>-tray` crate | wall-bridge `crates/wb-tray` |
| Graphics engine | `windows` crate Direct3D 11; Spout / NDI in and out; frames paced on vblank | songplayer `sp-gpu` (D3D11 compositor + Spout senders), wall-bridge `wb-spout` / `wb-scanout` |
| Windows build | cross-built on dev1: `cargo build --release --target x86_64-pc-windows-gnu` (target + mingw are system-wide; never install a toolchain in the account) | wall-bridge |
| Install | installer registers scheduled tasks (service, tray at the desktop user's logon, watchdog/update); logs in `C:\ProgramData\<App>\logs` | wall-bridge `installer/` |

**Any other technology only as an owner-approved exception**, recorded in the project `CLAUDE.md` as `<!-- airuleset:stack-exception <what> — <owner ref> -->` (`<owner ref>` = the ticket/comment where the owner approved it). Never add the marker on your own judgement. `onboard-project --audit` reports `stack-unpinned` (no `## Stack` section, no marker) and `stack-deviation` (a JS-framework or hand-written JS web UI without the marker); a PreToolUse gate refuses a new `package.json` that depends on react / vue / svelte / angular / preact / solid-js without the marker.

#### Web UI: Leptos + Axum + WASM

Every web UI MUST use Leptos compiled to WASM, served by Axum. No exceptions, no "it would be simpler to use plain HTML/JS" rationalizations.

- **CSR** (default for dashboards, PWAs, tools): `trunk build --release` → static `dist/`.
- **SSR** (cargo-leptos on Axum) only when the project explicitly requires server functions or server-side HTML, with the reason documented.
- Why WASM: compile-time-checked reactive wiring, no "undefined is not a function" class of bugs, fine-grained reactivity without virtual-DOM surprises, logic testable with `cargo test`, one language, no npm / node_modules.
- **FORBIDDEN** for a web UI: plain HTML/JS/CSS ("it's just a simple dashboard" is not a reason); React / Vue / Svelte / Angular (npm + JavaScript); Yew / Dioxus / Sycamore (Leptos is the standard; do not switch frameworks); server-rendered templates (Tera, Askama, Maud); htmx. **If you think "it would be easier without WASM" — STOP. That thought is wrong.**
- Tests, all three tiers for every UI feature: business logic in plain Rust types → `cargo test`; component behaviour → `wasm-bindgen-test`; E2E flows → Playwright against the running app, asserting zero browser-console errors.

#### Tray: every desktop web app ships one

Scope: a Rust app that serves a web UI (axum, axum-server, actix-web, warp, tiny_http, rocket, poem, salvo, tide, ntex) and runs on a desktop machine. Its tray has:

- **Status:** the service state, in the tooltip or the menu.
- **Open <app>:** opens the local web UI URL in the browser.
- **Copy URL:** copies that URL to the clipboard.
- **Exit:** ends the tray only. The service keeps running.

The tray autostarts at the desktop user's logon, next to the service. Reference implementation: wall-bridge `crates/wb-tray`.

`onboard-project` files a foundation ticket for a Rust web app with no tray dependency (`tray-icon`, `tray-item`, `ksni`, `trayicon`, `systray`), no `tauri` dependency with the `tray-icon` or `system-tray` feature, and no `*-tray` crate; `--audit` reports it as `missing-tray` (#1198, after fohmixer went live without one). A server-only app (a VPS, no desktop) opts out with `<!-- airuleset:tray=n/a <reason> -->` in its `CLAUDE.md`.
