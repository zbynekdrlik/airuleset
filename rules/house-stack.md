---
paths:
  - "**/Cargo.toml"
  - "**/Trunk.toml"
---

### House Stack — the default for every NEW app (MANDATORY, #1333)

**A NEW standalone app or deployable (a new repo, or a new deployable inside a house project) is built on the house stack below. Read this BEFORE choosing any technology, in an empty repo too.** Owner, 21.11.2026: projects "postavane na random adhoc zvolenych technologiach ... sa potom musia prerabat". **An existing project follows its established stack:** its platform is the `stack` field of its row in airuleset's `projects-registry.json` (`house` or a platform id: `odoo`, `n8n`, `python-tooling`, … — `cli_stack_scope.STACK_IDS`), set by airuleset only, never by a project session. A new repo whose kind matches ANY existing platform id follows that platform, and airuleset records its `stack` right after onboarding. Until a deviating project's migration ticket lands, keep maintaining the deviating component; new features go on the house stack or need the marker.

| Layer | Applies when | Technology (mandatory default) | Reference | Last reviewed |
|---|---|---|---|---|
| Workspace | every app | Rust Cargo workspace, crates `crates/<prefix>-*` | wall-bridge `wb-*` | 21.11.2026 |
| Service | an app that runs as a service or serves a UI / API (not a CLI or library) | axum + tokio; `GET /api/status` (JSON incl. version); WebSocket for live state | songplayer `sp-server` (`/api/v1/status` with the version, WebSocket); fohmixer `fohmixer-hub` (`/api/version`, WebSocket) | 21.11.2026: crates.io axum 0.8.9, tokio 1.54 |
| Web UI | an app with a UI | Leptos (latest 0.8.x) CSR → WASM (`wasm32-unknown-unknown`), built by Trunk, served by the axum service; PWA; version visible on the dashboard; where the UI shows controls / panels the user arranges, a customizable UI (ARRANGE: reorder + hide/show, saved server-side) | fohmixer `crates/fohmixer-ui` (ARRANGE: `crates/fohmixer-ui/src/behave/arrange_drag.rs` + `behave/arrange_edit.rs`) | 21.11.2026: crates.io leptos 0.8.23, trunk 0.22.2 (fohmixer-ui, sp-ui still 0.7) |
| Tray | a desktop app (Windows or Linux) | `tray-icon` in a `<prefix>-tray` crate, or the Tauri 2 tray (no window needed) | wall-bridge `crates/wb-tray` (tray-icon, Windows); intercom `tray` (tray-icon, Linux / gtk); fohmixer `crates/fohmixer-tray` (Tauri 2 tray, no window) | 21.11.2026: crates.io tray-icon 0.26.1 (wb-tray 0.19, intercom 0.21.2) |
| Graphics engine | a Windows app that renders video | `windows` crate Direct3D 11; Spout / NDI in and out; frames paced on vblank | songplayer `sp-gpu` (D3D11 compositor + Spout senders), wall-bridge `wb-spout` / `wb-scanout` | 21.11.2026: crates.io windows 0.62.2 (sp-gpu, wall-bridge 0.58) |
| Windows build | a Windows app | release artefacts come from the CI Windows runner; the local check is the dev1 cross-build `cargo build --release --target x86_64-pc-windows-gnu` (target + mingw are system-wide; never install a toolchain in the account) | wall-bridge | 21.11.2026 |
| Install | a Windows desktop app with a UI or video | installer registers scheduled tasks (service, tray at the desktop user's logon, watchdog/update); logs in `C:\ProgramData\<App>\logs` | wall-bridge `installer/` | 21.11.2026 |

A headless Linux service is the Workspace + Service + Web UI rows and opts out of the tray with `<!-- airuleset:tray=n/a <reason> -->`. A reference shows the SHAPE: take versions from the "Last reviewed" cell, **never copy a reference project's older version**. Known deviation, never a reference: wall-bridge's dashboard (a static `index.html` from `wb-service`, no WebSocket).

**Any other technology in a house project only as an owner-approved exception**, recorded in the project `CLAUDE.md` as `<!-- airuleset:stack-exception <what> — <owner ref> -->`, where `<owner ref>` is the `#N` or GitHub URL in which the owner approved it. Never add the marker on your own judgement. **One marker per technology:** `<what>` names it (React, askama, plain JavaScript, …), and a marker covers only the technology it names. A project `## Stack` section may only restate house rows; other technology in its Technology column needs the marker too.

Enforcement, in a `house` project or an unregistered repo: a PreToolUse gate refuses a `package.json` Write or Edit that brings a JS UI framework family NEW to the repo (react incl. next, vue incl. nuxt, svelte, angular, … — `cli_onboard_stack.FRAMEWORK_FAMILY`); after a package-manager Bash call (success or failure) a PostToolUse check compares the repo's `package.json` files with the default branch (the merge-base with `origin/HEAD`: committing does not make a framework allowed). Both honour only the repo-root `CLAUDE.md` marker. `onboard-project --audit` reports `stack-unpinned` (a registry row without a known `stack`) and, for `house` projects, one `stack-deviation` per technology without its marker: a JS-framework `package.json`; a template / UI crate (askama, tera, maud, yew, dioxus, sycamore, actix-web, warp, rocket, egui, iced, slint); hand-written JS (incl. inline `<script>`; vendored files and a framework app's own sources excluded) outside a `Trunk.toml` dir or a leptos / wasm-bindgen crate, minus known non-UI files (wasm-bindgen / Trunk output, generated reports, tooling dirs, gitignored paths, non-JS `type=` or commented-out inline blocks); other technology in the `## Stack` Technology column. It lists every marker and the gate's `ALLOW-MARKER` lines.

#### Stack evolution: a living default, never a freeze

Owner, 21.11.2026: the stack "musi byt stale otvoreny aby sa rozvijal k lepsim verziam".

- **At every design step (`Architektúra:`)**, for each layer the work touches, check from READ sources (release notes, the crate's docs/source, benchmarks — never memory) whether a clearly better option exists today: a newer major version, a better crate / engine / approach for this need.
- **Found one: never silently use it, never silently stay on the weaker one.** Propose it as a **stack upgrade** with evidence (what, why better for this need, cost of moving, affected projects) on the ONE canonical airuleset ticket labelled `stack-proposal` + `needs-decision` (it reaches the owner's `U`). Extend the open one; if none is open, file it: `gh issue create -R zbynekdrlik/airuleset -l stack-proposal -l needs-decision` with `Scope-gate: needs-user-decision`, `Dedup-checked: label stack-proposal` and `Requested by <owner>/<repo>#N` lines, then `peer_send` it to airuleset; an app-token stream files through `python3 ~/devel/airuleset/airuleset.py gk-request --repo zbynekdrlik/airuleset`. The design ticket links it and keeps working on the current row; the owner decides.
- **Approved:** airuleset updates the row and its "Last reviewed" cell (the new default for every project), and each affected project gets its own upgrade ticket.
- **Rejected:** one line under **Considered** below with the reason; never re-raise it without new evidence.

**Considered (rejected, with reason):** none yet.

#### Web UI: Leptos + Axum + WASM

Every web UI of a house app MUST use Leptos compiled to WASM, served by Axum. No exception without the owner marker above, no "it would be simpler to use plain HTML/JS" rationalizations; a clearly better option goes through Stack evolution, never a silent switch.

- **CSR** (default for dashboards, PWAs, tools): `trunk build --release` → static `dist/`.
- **SSR** (cargo-leptos on Axum) only when the project explicitly requires server functions or server-side HTML, with the reason documented.
- Why WASM: compile-time-checked wiring, one language, logic testable with `cargo test`, no npm.
- **FORBIDDEN** for a web UI: plain HTML/JS/CSS ("it's just a simple dashboard" is not a reason); React / Vue / Svelte / Angular (npm + JavaScript); Yew / Dioxus / Sycamore (Leptos is the standard; do not switch frameworks); server-rendered templates (Tera, Askama, Maud); htmx. **Simplicity alone is no reason to leave WASM; a genuinely better option goes through Stack evolution.**
- Tests, all three tiers for every UI feature: business logic in plain Rust types → `cargo test`; component behaviour → `wasm-bindgen-test`; E2E flows → Playwright against the running app, asserting zero browser-console errors.

#### Tray: every desktop web app ships one

Scope: a Rust app that serves a web UI (any HTTP server crate) and runs on a desktop machine (Windows or Linux). Its tray has:

- **Status:** the service state, in the tooltip or the menu.
- **Open <app>:** opens the local web UI URL in the browser.
- **Copy URL:** copies that URL to the clipboard.
- **Exit:** ends the tray only. The service keeps running.

The tray autostarts at the desktop user's logon, next to the service. Reference implementations: the Tray row. Both variants are accepted.

`onboard-project` files a foundation ticket for a Rust web app with no tray dependency (`tray-icon`, `tray-item`, `ksni`, `trayicon`, `systray`), no `tauri` dependency with the `tray-icon` or `system-tray` feature, and no `*-tray` crate; `--audit` reports it as `missing-tray` (#1198, after fohmixer went live without one). A server-only app (a VPS, no desktop) opts out with `<!-- airuleset:tray=n/a <reason> -->` in its `CLAUDE.md`.
