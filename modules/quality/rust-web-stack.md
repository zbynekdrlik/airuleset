### Rust Web Stack: Leptos + Axum + WASM (MANDATORY)

**Every web UI in a Rust project MUST use Leptos compiled to WASM, served by Axum. No exceptions. No alternatives. No "it would be simpler to use plain HTML/JS" rationalizations.**

#### The Stack

| Layer              | Technology                    | Non-negotiable |
| ------------------ | ----------------------------- | :------------: |
| Frontend framework | Leptos (latest 0.8.x)         |      YES       |
| Compilation target | WASM (wasm32-unknown-unknown) |      YES       |
| Backend/API server | Axum                          |      YES       |
| Build tool (CSR)   | Trunk                         |      YES       |
| Build tool (SSR)   | cargo-leptos                  |      YES       |

#### Mode Selection

- **CSR (Client-Side Rendering)** — default for internal dashboards, PWAs, and tools. Simpler, no server rendering needed. Use `trunk build --release` → static `dist/` folder.
- **SSR (Server-Side Rendering on Axum)** — only when the project explicitly requires server functions or server-side HTML generation. Use `cargo-leptos`.

Most apps should use **CSR** unless there is a specific, documented reason for SSR.

#### Why WASM is mandatory

1. **Compile-time correctness** — Rust's type system catches bugs that JavaScript discovers at runtime. If it compiles, the reactive wiring is correct.
2. **No "undefined is not a function"** — entire class of JS runtime errors eliminated.
3. **Deterministic rendering** — fine-grained reactivity with no virtual DOM diffing surprises.
4. **Testable** — business logic tested with `cargo test` (no browser needed), UI tested with `wasm-bindgen-test`.
5. **Single language** — frontend and backend in Rust. No context switching, no npm, no node_modules.

#### FORBIDDEN alternatives

Do NOT use any of these for web UIs in Rust projects, regardless of perceived simplicity:

- **Plain HTML/JS/CSS** — "it's just a simple dashboard" is not a valid reason. Use Leptos.
- **React/Vue/Svelte** — introduces npm, node_modules, and JavaScript. Use Leptos.
- **Yew/Dioxus/Sycamore** — Leptos is the standard for this codebase. Do not switch frameworks.
- **Server-rendered HTML templates** (Tera, Askama, Maud) — produces HTML without WASM. Use Leptos CSR instead.
- **htmx** — server-driven HTML without WASM. Use Leptos.

**If you find yourself thinking "it would be easier without WASM" — STOP. That thought is wrong. The compile-time guarantees and testability of WASM are the entire point. Short-term convenience leads to long-term instability.**

#### Testing Requirements

- Extract business logic into plain Rust types → test with `cargo test`
- Component behavior → test with `wasm-bindgen-test`
- E2E flows → Playwright against the running app
- All three tiers must exist for any web UI feature
