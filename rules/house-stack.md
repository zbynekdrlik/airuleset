---
paths:
  - "**/*.rs"
  - "**/src/**/*.rs"
  - "**/Cargo.toml"
  - "**/Trunk.toml"
  - "**/index.html"
  - "**/crates/**"
---

### House Stack — the default for every NEW app (MANDATORY, #1333)

**A NEW standalone app or deployable (a new repo, or a new deployable inside a house project) is built on the house stack below. Read this BEFORE choosing any technology, in an empty repo too.** Owner, 21.11.2026: projects "postavane na random adhoc zvolenych technologiach ... sa potom musia prerabat". **An existing project follows its established stack:** its platform is the `stack` field of its row in airuleset's `projects-registry.json` (`house`, or a platform id such as `odoo`, `n8n`, `python-tooling`, `python-service`, `ha-addon`, `sveltekit`, `obs`, `config`, `cuda`, `mobile-rn`), set by airuleset only, never by a project session.

| Layer | Applies when | Technology (mandatory default) | Reference | Last reviewed |
|---|---|---|---|---|
| Workspace | every app | Rust Cargo workspace, crates `crates/<prefix>-*` | wall-bridge `wb-*` | 21.11.2026 |
| Service | every app | axum + tokio; `GET /api/status` (JSON incl. version); WebSocket for live state | fohmixer `fohmixer-hub`, songplayer `sp-server` | 21.11.2026: crates.io axum 0.8.9, tokio 1.54 |
| Web UI | an app with a UI | Leptos (latest 0.8.x) CSR → WASM (`wasm32-unknown-unknown`), built by Trunk, served by the axum service; PWA; version visible on the dashboard | fohmixer `crates/fohmixer-ui` | 21.11.2026: crates.io leptos 0.8.23, trunk 0.22.2 (fohmixer-ui, sp-ui still 0.7) |
| Tray | a Windows desktop app with a UI or video | service-only app: `tray-icon` in a `<prefix>-tray` crate; app with its own desktop window: the Tauri 2 tray | wall-bridge `crates/wb-tray`; Tauri 2: fohmixer, iemmixer, songplayer | 21.11.2026: crates.io tray-icon 0.26.1 (wb-tray 0.19) |
| Graphics engine | a Windows app that renders video | `windows` crate Direct3D 11; Spout / NDI in and out; frames paced on vblank | songplayer `sp-gpu` (D3D11 compositor + Spout senders), wall-bridge `wb-spout` / `wb-scanout` | 21.11.2026: crates.io windows 0.62.2 (sp-gpu, wall-bridge 0.58) |
| Windows build | a Windows app | release artefacts come from the CI Windows runner; the local check is the dev1 cross-build `cargo build --release --target x86_64-pc-windows-gnu` (target + mingw are system-wide; never install a toolchain in the account) | wall-bridge | 21.11.2026 |
| Install | a Windows desktop app with a UI or video | installer registers scheduled tasks (service, tray at the desktop user's logon, watchdog/update); logs in `C:\ProgramData\<App>\logs` | wall-bridge `installer/` | 21.11.2026 |

A Linux service is the Service + Web UI rows. A reference shows the SHAPE: take versions from the "Last reviewed" cell, **never copy a reference project's older version**. Known deviation, never a reference: wall-bridge's dashboard (a static `index.html` from `wb-service`, no WebSocket).

**Any other technology in a house project only as an owner-approved exception**, recorded in the project `CLAUDE.md` as `<!-- airuleset:stack-exception <what> — <owner ref> -->`, where `<owner ref>` is the `#N` or GitHub URL in which the owner approved it. Never add the marker on your own judgement. A project `## Stack` section may only restate house rows; other technology in it needs the marker too.

Enforcement: `onboard-project --audit` reports `stack-unpinned` (a registry row without `stack`) and, for `house` projects only, `stack-deviation` (a JS-framework or hand-written JS web UI, or non-house technology named in `## Stack`, without the marker). In a `house` project or an unregistered repo, a PreToolUse gate refuses a Write, an Edit or a package-manager / scaffolder command that brings a JS UI framework NEW to the repo (react, vue, svelte, angular, preact, solid-js, lit, alpinejs, htmx.org, astro, qwik, ember and their meta frameworks) without the marker.

#### Stack evolution: a living default, never a freeze

Owner, 21.11.2026: the stack "musi byt stale otvoreny aby sa rozvijal k lepsim verziam" — never keep a weaker choice just because it is the current row.

- **At every design step (`Architektúra:`)**, for each layer the work touches, check from READ sources (release notes, the crate's docs/source, benchmarks — never memory) whether a clearly better option exists today: a newer major version, a better crate / engine / approach for this need.
- **Found one: never silently use it, never silently stay on the weaker one.** Propose it on the design ticket as a **stack upgrade** with evidence (what, why better for this need, cost of moving, affected projects), label that ticket `needs-decision` (it reaches the owner's `U`), and add it to the ONE canonical airuleset ticket labelled `stack-proposal` (extend the open one, file it if none is open; an app-token stream files through `python3 ~/devel/airuleset/airuleset.py gk-request --repo zbynekdrlik/airuleset`). The owner decides; until then build on the current row.
- **Approved:** airuleset updates the row and its "Last reviewed" cell (the new default for every project), and each affected project gets its own upgrade ticket.
- **Rejected:** one line under **Considered** below with the reason; never re-raise it without new evidence.

**Considered (rejected, with reason):** none yet.

#### Web UI: Leptos + Axum + WASM

Every web UI of a house app MUST use Leptos compiled to WASM, served by Axum. No exception without the owner marker above, no "it would be simpler to use plain HTML/JS" rationalizations; a clearly better option goes through Stack evolution, never a silent switch.

- **CSR** (default for dashboards, PWAs, tools): `trunk build --release` → static `dist/`.
- **SSR** (cargo-leptos on Axum) only when the project explicitly requires server functions or server-side HTML, with the reason documented.
- Why WASM: compile-time-checked reactive wiring, no "undefined is not a function" class of bugs, fine-grained reactivity without virtual-DOM surprises, logic testable with `cargo test`, one language, no npm / node_modules.
- **FORBIDDEN** for a web UI: plain HTML/JS/CSS ("it's just a simple dashboard" is not a reason); React / Vue / Svelte / Angular (npm + JavaScript); Yew / Dioxus / Sycamore (Leptos is the standard; do not switch frameworks); server-rendered templates (Tera, Askama, Maud); htmx. **If you think "it would be easier without WASM" — STOP. That thought is wrong.**
- Tests, all three tiers for every UI feature: business logic in plain Rust types → `cargo test`; component behaviour → `wasm-bindgen-test`; E2E flows → Playwright against the running app, asserting zero browser-console errors.

#### Tray: every desktop web app ships one

Scope: a Rust app that serves a web UI (axum, axum-server, actix-web, warp, tiny_http, rocket, poem, salvo, tide, ntex) and runs on a Windows desktop machine. Its tray has:

- **Status:** the service state, in the tooltip or the menu.
- **Open <app>:** opens the local web UI URL in the browser.
- **Copy URL:** copies that URL to the clipboard.
- **Exit:** ends the tray only. The service keeps running.

The tray autostarts at the desktop user's logon, next to the service. Reference implementation: wall-bridge `crates/wb-tray` (`tray-icon`, a service-only app); fohmixer, iemmixer and songplayer ship the Tauri 2 tray (an app with its own desktop window). Both variants are accepted.

`onboard-project` files a foundation ticket for a Rust web app with no tray dependency (`tray-icon`, `tray-item`, `ksni`, `trayicon`, `systray`), no `tauri` dependency with the `tray-icon` or `system-tray` feature, and no `*-tray` crate; `--audit` reports it as `missing-tray` (#1198, after fohmixer went live without one). A server-only app (a VPS, no desktop) opts out with `<!-- airuleset:tray=n/a <reason> -->` in its `CLAUDE.md`.
