---
paths:
  - "cli_webterm*.py"
  - "tests/test_webterm*.py"
---

### airuleset internals — webterm dashboard + tmux client sizing

The webterm sizing area has been reopened many times (#584/#586/#613×3/#615/#648/#661/#672).
Non-obvious, hard-won invariants (deep detail lives in the code comments of the named symbols,
which auto-load when you read `cli_webterm.py`):

- **#672 REWORK (owner ruling 2026-08-25) — ONE canonical grid for EVERY tab; the per-tab
  browser stream grid is REVERSED, the crop is a TMUX-side fix, and #648 is REVERSED too.**
  The original #672 gave a foreign-stream tab a LARGER browser grid (`WEBTERM_STREAM_TERM_GRID`
  320×64) so the owner's `-f ignore-size` client was ≥ the stream window and tmux never cropped
  the footer. That was the WRONG layer: the owner's browser VIEWPORT is fixed (his lowest-res
  notebook, PWA zoom 100%), so a bigger grid → the font-fit shrinks it → MICRO FONTS on the
  m1..m6 tabs (unusable; owner: "nie je ani jeden dovod aby boli tmuxi a windows v nich
  rozdielne, vsetky musia maximalne vyhovovat mne"). REMOVED: `WEBTERM_STREAM_TERM_GRID`, the
  `_tab_sessions` `kind=="stream"` tcols/trows override, and the per-current-tab CFG getter IIFE
  — every tab now renders at the ONE owner canonical grid `_webterm_term_grid()` (176×51). The
  foreign-stream footer crop is instead solved on the TMUX side by the fleet-wide `window-size
  manual` + `default-size 176x50` pin (`apply_tmux_history_limit`, on EVERY box incl. subdev):
  `manual` pins every window to the owner size regardless of ANY client, so the owner's 176×51
  ignore-size client shows every window whole (footer included) and David/Marek get the owner's
  size (a harmless cosmetic dark border) — which the owner EXPLICITLY wants, **reversing the #648
  "never degrade David" invariant by owner decree.** Verified LIVE on dev1 (`show-options -g` →
  `window-size manual`, every window 176×50). CONSEQUENCE: the two bullets below ("crop fix is
  BROWSER-GRID-bound" and "owner box vs foreign-stream box grid") are the OLD #672 design and are
  SUPERSEDED — the geometry claim (a too-small client is cropped) still holds, but the FIX is now
  the tmux pin, never a per-tab browser grid. The subdev/dev2 cross-box tmux convergence + an
  isolated-tmux empirical pin proof are the #685 / tmux-convergence follow-up (the `window-size
  manual` conf is CONF-ONLY = takes effect at the next server start; a running subdev server that
  predates the conf still follows David's client until it restarts). See also #671 rework: the
  `#clip-hint` copy/paste footer strip was removed ENTIRELY (element + CSS + isSecureContext
  honesty JS) — owner: "potrebujem hlavne pracovnu plochu nie tvoje blbe vysvetlivky" — while
  `attachClipboard` (OSC 52 + copy-on-select) stayed, so copy/paste FUNCTIONALITY is unchanged.
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
  (`exec ttyd -i "$SOCK" …`); the gateway binds either a `--bind` TCP interface (the
  real install passes a `_tailscale_ip`-validated IP) or a `--socket` UNIX socket,
  and `main()` fail-closes to exactly one of them (#663) AND rejects a wildcard
  `--bind` outright (#681, below). IP-validation of `--bind` happens at render/
  provision time (`_tailscale_ip`); `main()` itself only rejects the wildcard forms.
- **MITIGATION IN CODE (#681).** `_reject_wildcard_bind(bind, where)` (cli_webterm.py,
  backed by `_bind_is_wildcard` — an ipaddress/inet_aton parse that catches EVERY
  interface-any spelling: `0.0.0.0`, `::`, `::0`, `0:0:0:0:0:0:0:0`, the legacy
  shorthands `0`/`0.0`/`0.0.0`, `""`, `*`) fails closed at the two TCP-render
  chokepoints (`render_webterm_launch_script` password branch +
  `_render_webterm_gateway_unit`). The gateway `main()` carries its OWN local
  `_bind_is_wildcard` (the module is standalone) and refuses a wildcard `--bind` at
  runtime — closing the #671 class where an agent HAND-RUNS `--bind 0.0.0.0
  --trust-access-header …` (a forgeable header + all-interfaces bind), which no
  render would ever emit. Locked by `tests/test_webterm_ttyd_wildcard_bind_681.py`
  (scans the rendered spawn argv/units — a hand-enumerated list, so enrol any new
  renderer there — + asserts the guard raises) and `TestWildcardBindMainGuard681`.
  The UNIX-socket path is not a TCP interface and needs no guard.
- **TEST HARNESS = loopback too, never 0.0.0.0.** To live-verify webterm in Playwright
  MCP, bind the throwaway ttyd LOOPBACK `-i 127.0.0.1` and navigate
  `http://127.0.0.1:<port>/`. Playwright MCP DOES reach 127.0.0.1 on dev1 —
  EMPIRICALLY confirmed #681 (a loopback ttyd rendered its marker in the browser)
  AND #678; this SUPERSEDES the earlier #661 harness claim ("Playwright MCP can't
  reach the host 127.0.0.1 → bind 0.0.0.0"), which a review agent literally executed
  → an unauthenticated writable terminal on the tailnet, killed by hand (#671). The
  `internals-tests.md` #661/#678 harness bullets now agree on loopback-only.
- **#681 review lessons (reusable for any bind/security guard).** (1) A wildcard-bind
  GUARD must be PARSE-based — `ipaddress.ip_address(b).is_unspecified` + `inet_aton(b)
  == 0` — NOT a frozenset of literals: `::0` / `0` / `0.0` / `0.0.0` / `0:0:0:0:0:0:0:0`
  all resolve to INADDR_ANY/in6addr_any but escape a literal set (only `""` / `*` need
  the explicit sentinel branch). (2) Guard the RUNTIME chokepoint, not only the render
  path — the #671 class is an agent HAND-RUNNING `--bind 0.0.0.0`; the gateway
  `main()` argparse rejects it now, mirroring the render guard (module stays
  standalone → a LOCAL `_bind_is_wildcard`, no cli_webterm import). (3) A regex SCAN
  for a wildcard literal must NOT carry an empty-value arm — `(?:-i|--bind)…['\"]?(?:\s|…)`
  matches EVERY `-i ` (the trailing space satisfies the empty arm); drop it (the parse
  guard covers `""`) and verify the regex against the REAL rendered artifacts for
  false-positives, never just seeds. (4) VERIFY a ticket's cited `#N` before repeating
  it — the #661/#678 harness bullet was cited "#657" (an unrelated ticket); one
  `grep -c 657 internals-tests.md` (0 hits) + `git log -S "<phrase>"` settled it. A
  citation inherited from ticket text is itself the "unverified doc claim" class this
  ticket fixes.
