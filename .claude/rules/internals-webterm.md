---
paths:
  - "cli_webterm*.py"
  - "tests/test_webterm*.py"
---

### airuleset internals — webterm dashboard + tmux client sizing

The webterm sizing area has been reopened many times (#584/#586/#613×3/#615/#648/#661/#672).
Non-obvious, hard-won invariants (deep detail lives in the code comments of the named symbols,
which auto-load when you read `cli_webterm.py`):

- **A too-small tmux CLIENT is CROPPED, never scaled (#672).** A client attached `-f ignore-size`
  that is SMALLER than the window gets a cursor-following CROP of the window — everything below
  the cursor (the CC statusline footer + agent strip) is clipped. `capture-pane` shows the footer
  IS in the pane; only the client's render clips it. This is the whole #672 bug.
- **A webterm crop fix is BROWSER-GRID-bound, NOT tmux-side (#672).** `-f ignore-size` is REQUIRED
  to keep the target's OWN client un-resized (removing it lets the owner's client resize the
  window = degradation; proven by a control). With ignore-size, the client viewport == its ttyd
  pty == the browser xterm grid (`fitFixedGrid` clamps `term.resize`). So the ONLY non-degrading
  way to stop the crop is to make the browser grid ≥ the target window. No `window-size` mode
  helps (smallest degrades the owner; largest/latest keep their size and the small client still
  crops). Do NOT chase a tmux-side fix — the geometry forbids it.
- **Owner box vs foreign-stream box grid (#672).** An OWNER box pins `window-size manual` +
  `default-size 176x50` (cli_tmux_provisioning), so the owner grid `_webterm_term_grid()` = 176x51
  matches the window → no crop, no dark border. A FOREIGN-STREAM box does NOT pin — its window
  follows the stream dev's own client (David 305x57). So foreign-stream tabs get their own larger
  `WEBTERM_STREAM_TERM_GRID` (per-tab `tcols`/`trows` in `_tab_sessions`, kind=="stream" only);
  owner tabs keep 176x51 or a dark border returns. A grid too LARGE for a small-terminal stream is
  a harmless dark border (everything visible), a grid too SMALL crops — prefer generous.
- **Keep the churned `fitFixedGrid`/`fillFixedGrid` FILL region UNTOUCHED (#672).** Per-tab grid is
  delivered via a getter over `CFG.term_cols`/`CFG.term_rows` placed right after `const CFG` (an
  uncontended spot), so the fill algorithm reads them unchanged.
- **A `current`-keyed getter + the resize-CLAMP is a RACE trap (#672).** `fitFixedGrid`'s clamp
  (`term.resize = () => real(...)`) captured the grid at INSTALL time, and a preloaded tab's ttyd
  can connect LATE while `current` has moved to a different-grid tab → a STICKY clamp to the wrong
  grid that silently re-crops. FIX: the clamp reads `CFG.term_cols`/`CFG.term_rows` LIVE on every
  resize (activate() shows only the current tab, so a clamped resize of a visible terminal always
  sees its own grid). Any future per-tab-grid work must keep this live-read property.
- **Prod owner-dashboard render path is `human="zbynek"`, not `human=None`** — `entries_for_tab_list`
  + `preserve_order=True`, which preserves `kind`. Lock BOTH paths in a test (the crop regression
  test renders `human=None`; add a `human="zbynek"` case too).
- **Empirical proof method (#672, mirrors #613 ctrlbw harness).** Reproduce a sizing/crop bug on an
  ISOLATED tmux server: reuse `_IsolatedTmuxServer` (own `-S` socket, `TMUX`/`TMUX_PANE` stripped,
  `pty.openpty()`+`TIOCSWINSZ` pinned pty clients, wall-clock `_drain`). Attach one normal client +
  one `-f ignore-size` smaller client; assert the footer marker is absent from the small client's
  RENDERED screen (crop) and present at the fixed grid. ALWAYS include a no-degradation CONTROL
  (the window size must be unchanged after the ignore-size attach). NEVER touch a live session.

## Supervisor smoke po #663 (unix sockety)

- Access-mode gateway/ttyd NEPOČÚVAJÚ na TCP — starý header-inject relay
  (127.0.0.1:8199 → :8080) je mŕtvy. Supervízny smoke ide priamo cez socket:
  `curl --unix-socket /run/user/<uid>/webterm-gateway.sock -H
  "Cf-Access-Authenticated-User-Email: <owner e-mail>" http://localhost/`
  (dev1 owner lane; subdev lane sockety: `webterm-<lane>-gateway.sock`).
- Cross-account izolácia sa overuje NEGATÍVNE: cudzí účet dostane
  "Couldn't connect" + `ls /run/user/<uid>` Permission denied (0700 runtime dir).

## ttyd/gateway bind invariant — loopback or UNIX socket, NEVER 0.0.0.0 (#681)

- **THE INVARIANT.** Every webterm ttyd (owner replica AND each lane) and the
  same-origin gateway binds LOOPBACK `127.0.0.1` (password mode) or a mode-0700
  UNIX-domain socket in the account's `/run/user/<uid>` runtime dir (Access mode,
  #663) — NEVER a wildcard / interface-any bind (`0.0.0.0`, `::`). A wildcard bind
  exposes an unauthenticated, WRITABLE terminal on every interface incl. the
  tailnet. The real spawn sites: `WEBTERM_TTYD_BIND = "127.0.0.1"` (cli_webterm.py),
  `_LAUNCH_TEMPLATE` (`exec ttyd -p … -i 127.0.0.1 …`) and `_LAUNCH_TEMPLATE_SOCKET`
  (`exec ttyd -i "$SOCK" …`); the gateway `--bind` argparse help says "never 0.0.0.0"
  and `main()` fail-closes to exactly one of `--bind` (validated tailscale IP) / `--socket`.
- **MITIGATION IN CODE (#681).** `_reject_wildcard_bind(bind, where)` (cli_webterm.py)
  fails closed — a wildcard/empty bind at the two TCP-render chokepoints
  (`render_webterm_launch_script` password branch + `_render_webterm_gateway_unit`)
  raises `ValueError` instead of rendering a live exposure. Locked by
  `tests/test_webterm_ttyd_wildcard_bind_681.py` (scans every rendered spawn
  argv/unit for a wildcard + asserts the guard raises). The UNIX-socket path is not
  a TCP interface and needs no guard.
- **TEST HARNESS = loopback too, never 0.0.0.0.** To live-verify webterm in Playwright
  MCP, bind the throwaway ttyd LOOPBACK `-i 127.0.0.1` and navigate
  `http://127.0.0.1:<port>/`. Playwright MCP DOES reach 127.0.0.1 on dev1 —
  EMPIRICALLY confirmed #681 (a loopback ttyd rendered its marker in the browser)
  AND #657; this SUPERSEDES the earlier #661 harness claim ("Playwright MCP can't
  reach the host 127.0.0.1 → bind 0.0.0.0"), which a review agent literally executed
  → an unauthenticated writable terminal on the tailnet, killed by hand (#671). The
  `internals-tests.md` #661/#657 harness bullets now agree on loopback-only.
