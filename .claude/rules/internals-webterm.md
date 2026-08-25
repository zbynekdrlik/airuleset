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

## #661 rework + #684 parity — badge reversal + lane-dashboard regeneration (2026-08-25)

- **#661 REVERSED its own #582 decision** (owner acceptance ruling): the `.ord`
  ordinal badge (the visible Ctrl+Alt+1..9 map added by #582) was owner-vetoed — "adds
  no needed info, eats space" — so it is REMOVED (the `<span class="ord">` in
  `_tab_button` + the `.tab .ord` / `.tab.active .ord` CSS). The Ctrl+Alt+1..9
  SHORTCUT itself stays fully functional (`onHotkey`, `e.key >= '1'`) — it never
  depended on the badge; only the visible digit went. The green `▸ .ico` separator
  STAYS (owner values its tab-separating role). Reusable shape: a KEEP-the-mechanism /
  DROP-the-decoration reversal — the RED test asserts the rendered dashboard contains
  NO `class="ord"` at any tab count, and a sibling test locks that `.ico` survives, so
  "requirement change, not test-weakening" is provable.
- **#684 finding: the lane-dashboard REGENERATION is ALREADY on the deploy path — do
  NOT add a redundant re-render step.** Full chain (verify empirically, never assume):
  `push` (cli_remote._deploy_to_all_remotes) deploys to `david1@subdev` AND
  `marek@subdev` (both in `cli_fleet.REMOTE_HOSTS`) → `git pull && python3 airuleset.py
  install` under each account → `cmd_install` → `maybe_setup_webterm()` → dispatch by
  profile/account → `cli_webterm_lane.setup_service()` → `write_artifacts()`
  REGENERATES `dash_index` from the LIVE `render_dashboard_html()` (human=None,
  physically-scoped inventory) → daemon-reload → restart ttyd+gateway. The gateway
  ALSO serves `dash_index` by reading the file per request
  (`cli_webterm_gateway.py:558`), so a fresh `write_artifacts` alone serves current
  HTML even before the restart. So the shared render is the parity mechanism: any owner
  render change reaches david/marek on the next push, automatically.
- **How to PROVE lane parity without touching live subdev units (worktree-safe).** (1)
  LIVE READ (one ssh, read-only, key `~/.secrets/gatekeeper_access_ed25519`, NEVER retry
  — subdev fail2ban): `grep -c 'class="ord"'` the live `~/.claude/webterm-<lane>-dash/
  index.html` — before #661 both lanes carried `.ord` (david 5, marek 1), proving the
  path already propagates owner-render changes. (2) LOCAL DRY-RUN: run the REAL
  `_write_<lane>_artifacts()` with the lane path constants patched into a tmp dir (the
  `test_webterm_david.py::_isolate` pattern) and read back the generated index.html
  (post-#661: 0× `class="ord"`, 5× `.ico`, `.tab` padding `6px 12px 6px 16px`,
  `"u_status": false`). (3) The live-service restart + unix-socket curl smoke is the
  SUPERVISOR's post-merge job, not a worktree action.
- **Parity is VISUAL/UX ONLY — the security boundary is `u_status` (#677).** A lane
  render (`human=None` / `"marek"`) has `"u_status": false` in its embedded cfg; only
  `human == WEBTERM_LOGIN_USER` ("zbynek") gets `true`. So a lane gateway NEVER polls
  `/u-status` and NEVER spawns the cross-tenant `--u-collect` ssh collector under a
  sub-dev account. Lock it in the parity test; a parity change must never flip it.
- **The regression lock (`tests/test_webterm_lane_parity_684.py`) is the deliverable,
  not new code**: (a) `write_artifacts` writes `dash_index` from the LIVE render (patch
  `render_dashboard_html` to a sentinel → written file == sentinel, so a future
  cached/hardcoded blob fails); (b) `setup_service` re-renders BEFORE it restarts the
  units (order locked via the `run`/`_run_systemctl`/`write_artifacts_fn` seams + a
  SimpleNamespace spec — setup_service only reads a handful of spec attrs on the ready
  path); (c) owner + lane render both drop `.ord` (parity, non-vacuous); (d) the
  `u_status` boundary above.
### #661 rework — extending a per-human LANE SET (Marek: montalu4 + dev1/dev2 sessions + forestshop)

Reusable shape for growing ANY lane's session set (the next "add X to <human>'s dashboard"):

- **The set lives in `cli_webterm_profiles.<human>_inventory()` — a zero-import leaf.** A
  public-DNS target's #679 host-key pin is DUPLICATED verbatim into the leaf (CODEX_HOST
  precedent) + tied to `cli_fleet` by a drift-lock test — never an import. `write_artifacts`
  json-dumps the entries, so `host_keys` rides into the inventory JSON and the connect child
  takes the strict-pin branch (#680) with no code change.
- **Every ssh entry MUST carry an explicit `identity`** — `identity=None` on a non-local entry
  makes `_ssh_interactive_prefix` take the `sshpass -p newlevel` fleet-shared-password branch
  FROM THE LANE GATEWAY (a cross-tenant leak). PROBE the lane account's existing keys first
  (read-only ssh, one attempt); no existing access ⇒ a NEW dedicated `WEBTERM_<HUMAN>_IDENTITY`
  (the david shape), never the gatekeeper key. Key+authorized_keys distribution = go-live
  owner-action; for a target that is an OWNER account (newlevel@dev1/dev2) the go-live must
  RECOMMEND a forced-command `restrict,command="tmux ..."` entry and the SECURITY NOTE must
  name the transitive reach honestly (an unrestricted key there is a full owner shell).
- **Do NOT grow the prereq gate a new key requirement for a LIVE lane** (`identity_key` stays
  None): gating would no-op `write_artifacts` re-renders until the key lands — a #684 parity
  regression. The ssh tabs degrade to a VISIBLE ssh failure instead.
- **Tab order/exclusivity for a lane = `LaneSpec.dashboard_human` → the lane render consumes
  `WEBTERM_DASHBOARD_TABS[human]`** (write_artifacts omits the `human` kwarg entirely when the
  field is None — david stays unfiltered, its scoped ids differ from the policy dict's fleet
  ids; the #684 `assertNotIn("human", kwargs)` lock stays green via getattr+conditional).
  Marek's policy ids are LANE-namespace: `dev1`/`dev2` deliberately reuse the fleet id
  spellings (load-bearing — they buy the right aliases from the single #592 source) but name
  MAREK's entries (preferred=`marek` group); the two namespaces meet only in tests.
- **"Marek's tmux sessions on box X" = an entry with `preferred="marek"`** — `_ATTACH_BODY`
  resolves exact-name → session_group → single-session → create, i.e. the SAME owner-group
  (zbynek/marek) session mechanism notify/statusbar use. Never a hardcoded session list.
- **Forestshop verdict (verified, not guessed):** the fleet's ONE forestshop box is
  `forestshop-dev.newlevel.media`; Marek's tab connects as `admin` (the principal
  forestshop-app account; notify #572 routes the whole box to marek's realm), NEVER `stepan`
  (StepanDK's own isolated personal account — a third person's account on someone's dashboard
  is the original #661 sin). Box-keyed alias `fs` (like spinbike `sb`) at `cli_aliases`.

### #686 webterm U-dot — a per-box AGGREGATION over tickets-status caches must FRESHNESS-filter, or a dead session's frozen U inflates it forever

The #677 U-dot collector (`cli_webterm.py` `_box_u_count` local + the inline
`_U_READER_SNIPPET` run over ssh on each remote box) summed `user_waiting` across
EVERY `~/.claude/tickets-status/*.json` with no freshness test. A tickets-status
cache is refreshed ONLY by the OWNER of its cwd (statusline shim on render, TTL
120s; plus the watchdog's 60s re-warm of an ACTIVE cwd, `_watchdog_backlog_fetch`
→ `_spawn_refresh`), so a DEAD session's cache FREEZES its `user_waiting` and the
sum stays inflated forever (live: gk footer truthfully U 0 while `/u-status`
summed the box's stale caches to 8 — the red dot never cleared; dev1 similarly).
FIX = a freshness precondition (`_U_FRESH_MAX_AGE_S = 30 min = 15× the 120s TTL`)
in BOTH readers: an entry counts only when `ts` is numeric and `now - ts <= M`.
Lessons reusable for any future per-box aggregation over these caches:

- **FRESHNESS is the right discriminator, NOT root-existence.** Real dev1 data
  proved root-existence INSUFFICIENT: two dead-session caches (camera-box ~10.4h,
  spinbike ~16.7h, `user_waiting=1`) had `root` EXISTING (a real repo dir, not a
  removed worktree) — root-existence keeps them and still inflates; only freshness
  drops a dead session in a real dir. Root-existence is ALSO redundant (a removed
  worktree's cache is already stale) and would need the check baked into the FIXED
  remote snippet (root-existence is only checkable on-box). Freshness alone handles
  BOTH removed-worktree and dead-session-in-real-dir, and is the smallest fix.
- **The remote read runs the snippet ON the box** (`_read_box_u` ssh-runs
  `_U_READER_SNIPPET`; the collector receives only the integer), so the snippet must
  carry the SAME freshness filter as `_box_u_count` — they are kept equal by
  `tests/test_webterm_u_status.TestUReaderSnippet`. The max-age constant is baked
  into the snippet string via `% _U_FRESH_MAX_AGE_S`.
- **Window justification:** live sessions refresh within ~1–2 min (TTL 120s) +
  watchdog 60s re-warm; every OBSERVED dead entry is ≥10h. 30 min is ~15× the TTL
  (huge margin so a live-but-idle WAITING session — the case that legitimately has
  U>0 — keeps its dot) yet ~20× below the smallest dead entry. Deliberately a bit
  more generous than the sibling `_BACKLOG_STATUS_CACHE_MAX_AGE_S` (15 min, SAME
  cache) because a false-negative here loses a navigation DOT, not just a fallback.
- **Fail-safe direction preserved** (`_read_box_u` doctrine): a stale / undatable
  (no-ts / non-numeric-ts) entry is DROPPED (dot lost, self-heals on next refresh),
  never summed as a false positive; a transiently-unreachable box still returns None
  (no dot) via the unchanged error path — the filter lives INSIDE the per-box sum.
- **Contract-change test adaptation:** the pre-existing `TestBoxUCount` /
  `TestUReaderSnippet` fixtures seeded caches with NO `ts` — after the fix a ts-less
  cache is (correctly) dropped, so those fixtures were updated to seed a fresh `ts`
  (a deliberate precondition change, not a weakening). The JS poll (`applyUStatus`,
  ~1564) is UNCHANGED — a box aggregating to 0 yields `map[id]=0` → the existing
  `u>0` toggle removes the dot.

- **#691 (2026-08-25) — two process gotchas from the active-tab CSS ticket.** (1) NEVER write
  hash-prefixed hex colours (`#333333`) in a COMMIT MESSAGE: `block-commit-without-design.sh`
  parses them as issue refs and hard-blocks the commit ("no design comment posted yet for
  #333333") — write them hash-less (`333333`) until #692 (hook hex-misparse) is fixed.
  (2) Visual-verify recipe for the dashboard: Playwright MCP blocks `file://` — render
  `render_dashboard_html(...)` to the scratchpad, serve it with
  `timeout 300 python3 -m http.server <port> --bind 127.0.0.1 -d <scratchpad-dir> &`, then
  read computed styles via `browser_evaluate` (getComputedStyle read-back beats eyeballing)
  and screenshot `#tabbar`. Beware: `browser_take_screenshot` saves relative paths into the
  MAIN checkout root (the MCP server's cwd), not your worktree — move the file out so the
  shared tree stays clean. Styling invariant worth knowing before touching tab CSS:
  `.tab:hover` and `.tab.active` have EQUAL specificity, so `.tab.active` must stay declared
  AFTER `.tab:hover` (source order is what keeps a hovered active tab from dimming).
- **#691 REWORK (owner rejected v0.1.55 active-tab, 2026-08-25) — a KEEP-the-data-channel /
  MOVE-the-render pattern + three sub-lessons.** The owner rejected the visual (top inset blue
  stripe touched the label; the separate red `.udot` was the loudest element) and set a binding
  hierarchy NAME > SELECTED > U. Fix WITHOUT touching the U data channel: the U-state moved from
  a dedicated `<span class="udot">` to a colour-swap of the EXISTING left `▸ .ico` arrow
  (`.tab.has-u .ico { color: #E74856 }`) — `applyUStatus`'s `.has-u` toggle + the whole
  `_box_u_count`/`_U_READER_SNIPPET` collector stayed byte-untouched (only the RENDER changed);
  the selected cue moved from a top `inset 0 2px` stripe to a BOTTOM `inset 0 -2px #3B78FF`
  underline (a "selected tab" convention that never touches the label) + `.tab` top/bottom
  padding 6→9px for vertical breathing. Reuse this shape for any "owner rejected the look, keep
  the behaviour": re-express the SAME state on an existing element, retire the dedicated one.
  (1) **RETIRING a UI element = grep the element's OLD NAME across the WHOLE file, JS COMMENTS
  included — not just its CSS rule + markup.** The one review 🔵 was a stale JS comment above
  `applyUStatus` still saying "the per-tab U dot" / "leaves the current dots untouched", 15
  lines from the CSS comment that correctly called `.udot` retired. `grep -rn '<old-name>'
  <file>` after any retirement; every hit is updated or a deliberate historical note. (2)
  **`.tab.has-u .ico` (0,3,0) beats `.tab .ico` (0,2,0) by specificity — state-agnostic vs
  `.active`**, so it reddens the arrow on an active+U tab regardless of source order (verified
  live: active+U → `#E74856` on the `#333333` body). `assertNotIn("inset 0 2px 0 0")` is a sound
  "top stripe retired" check precisely because the new value is `inset 0 -2px 0 0` — the `-`
  breaks substring contiguity. (3) **A prior ticket's test that hardcoded a FULL multi-value CSS
  string breaks when a later ticket legitimately changes ONE of those values** — the #661
  left-padding lock asserted the whole `padding: 6px 12px 6px 16px`; #691's vertical-padding
  change (6→9) broke it. Decouple such a lock to the asymmetry it actually owns (parse the
  4-value shorthand, assert left=16 > right=12), never the values a later ticket owns.

### #685 tmux — a CONF-ONLY geometry pin never reaches a running server; live convergence is now sanctioned, version-gated

The #672 uniform-grid fix pinned `window-size manual` + `default-size 176x50` in the
managed tmux conf — but a tmux server reads the conf ONLY at start, and agentic fleet
boxes never restart tmux (that kills live Claude sessions), so every server started
before the pin ran `window-size latest` forever. Live dev2: David's direct 305x57
client sized the codex-bridge window to 305x56; the owner's 176x51 `-f ignore-size`
webterm client showed the top-left 176x51 region → the CC footer (bottom ~5 rows)
invisible after a window switch. **`-f ignore-size` only stops the webterm from
SHRINKING a window — it cannot fix one already LARGER than the owner's viewport.**
FIX = `converge_tmux_window_geometry` (cli_tmux_provisioning.py), invoked from
`apply_tmux_history_limit` at every install/push: version-gated on the SAME >= 3.5
probe as the conf line (fails CLOSED — tmux 3.4's conf-parse crash #241 and the dev1
format-expansion segfault stay impossible; spinbike is 3.4 and is correctly refused),
idempotent (state-read-first: `show-options -g`, window listing dedup'd by window id
— grouped sessions list a shared window once per session), never kills/restarts,
never types. The old "window-size NEVER live-set / per-window resize never anywhere"
doctrine (#236/#241) was NARROWED, not deleted: `TestTmuxWindowSizeNoResize` now
locks airuleset.py literal-free + the mutating resize subcommand to the ONE helper;
the #236 "snap-resize hazard" is the owner-DESIRED convergence under #672. Live
convergence recipe (manual, same as the helper): `tmux set-option -g window-size
manual; tmux set-option -g default-size 176x50;` then per window ≠176x50
`tmux resize-window -t <window_id> -x 176 -y 50` — proven on dev2, gk, david1/2,
miva1, montalu1–8, marek@subdev, dev1: sessions untouched, attached larger clients
just gain the cosmetic dark margin the owner decreed. Acceptance read-back:
`tmux capture-pane -p -t <sess:win> | tail -6` shows the CC statusline inside the
50-row window.

- **#661 go-live (supervisor, 2026-08-25):** authorized_keys forced-command tvar je `restrict,pty,command="…"` — samotné `restrict` vypína aj PTY a interaktívny tmux attach by zlyhal; `pty` ho musí explicitne vrátiť. Overenie živosti restrict línie bez interakcie: BatchMode probe (`ssh -T … true`) — forced command IGNORUJE `true` a padne na `open terminal failed: not a terminal` = auth OK + forced command beží. Presný remote command renderuj z `cli_webterm._remote_command(preferred)` (escape `\` a `"` pre authorized_keys), nikdy ručne prepísaný.
### #700 webterm — exact viewport fill: the integer-cell residual is killed at the IFRAME boundary, never inside the xterm document

The #678 native fill (integer px/cell `letterSpacing`/`lineHeight`) quantizes the
WHOLE grid in steps of `cols`/`rows` px per axis (176 px horizontally!), so up to
~176+~102 px of centered letterbox remained BY DESIGN — the owner's #700 report:
side margins + an "empty row" under the status bar. Three reusable lessons:

- **The "dead row" was a MISDIAGNOSIS — do the screenshot row-math before touching
  geometry.** Client grid (176x51) = window (176x50, `TMUX_DEFAULT_SIZE`) + 1
  status row; the status bar OCCUPIES grid row 51 (measure: first-row top + N×cell
  height against the bar's position; mind DPR — a 2879-px-wide PWA screenshot at
  Windows 150% is a 1920-CSS-px viewport, all JS math is CSS px). Forcing all
  sources to ONE literal would crop: window 176x51 → the owner's own 176x51 WT
  client loses the last row (#613 class); browser grid 176x50 → CC footer crop
  (#672). Lock: `TestGeometryCanonDecision700`.
- **The ONE mouse-safe place for an EXACT fill is a transform on the IFRAME in the
  PARENT document** (`stretchFrameToFill`, capped `WT_FRAME_FILL_MAX_STRETCH`
  1.25/axis, `#frames{overflow:hidden}` clips the spill). A SAME-document ancestor
  transform of the xterm screen is the #678 mouse regression (getBoundingClientRect
  scales, cssCellHeight doesn't); a PARENT-document transform never enters the
  child's coordinate space — child rect AND pointer clientX/Y stay in child layout
  px (the browser inverse-maps events through ancestor transforms), and
  `win.innerWidth/innerHeight` stay layout-sized → no feedback loop, and a
  parent-side style change cannot re-fire the child ResizeObserver. Proven LIVE
  (2026-08-25, review 🟡 fix): real ttyd 1.7.4 + headless Chromium + REAL
  `page.mouse` input through a parent-scaled iframe — 9/9 drags at scale
  1.15x1.22 and at the 1.25 cap selected exactly the pointed row's text (rows
  6/26/42 of 52; the #678 proof method re-run for the cross-document variant).
  Rig gotcha: the fleet conf sets `mouse on`, under which xterm does NO local
  selection (drags go to tmux copy-mode and `term.getSelection()` stays empty
  even untransformed) — set `mouse off` on the sandbox tmux server to exercise
  xterm's own pixel→cell mapping, and drag INTERIOR cells (a rig xterm fills
  the whole iframe, so an arbitrary test scale clips the outer cell band —
  unlike the real page, whose scale is exactly the letterbox residual).
- **Harness gotcha:** extracted dashboard functions reference top-level consts —
  every shipping cap needs a matching `const` in `_FIT_HARNESS` PLUS a caps-match
  source lock (the #655 pattern), or the node run dies on ReferenceError only
  AFTER the source lands (a RED test can't see it).

### #694 webterm — the dashboard template lives in `cli_webterm_dash_template.py`, NOT inline

- **Where the HTML/CSS/JS is:** every dashboard markup/style/script edit (the #643/#661/#672/
  #677/#678/#691/#700 class of ticket) goes into `cli_webterm_dash_template.py` —
  `DASHBOARD_TEMPLATE`, a PURE CONSTANT LEAF (zero imports, zero defs; both locked by
  `tests/test_webterm_template_extraction_694.py`). `cli_webterm.py` stays the LOGIC module and
  merely aliases the constant (`… import DASHBOARD_TEMPLATE as _DASHBOARD_TEMPLATE`); the
  `@@…@@` single-pass substitution contract stays in `render_dashboard_html` THERE. Never paste
  HTML back into `cli_webterm.py` — the invariant test hard-fails on any inline `<!DOCTYPE`.
- **Sentinel set == subst keys, exactly.** Live sentinels are `@@BUTTONS@@`/`@@CFG_JSON@@`/
  `@@THEME_JSON@@`; `@@COUNT@@` was a VESTIGE (absent from the template since #671/#674,
  removed from the subst dict+regex in #694). Adding a new sentinel means BOTH sides in one
  commit — template token AND subst entry + regex alternative — or the 694 test fails
  (template-side-only would otherwise ship an unsubstituted `@@X@@` to the browser silently).
- **Ratchet after a split:** the extracted-from file's ceiling is LOWERED and the new module
  enrolled in the SAME green commit (`tests/size_ratchet.json`). A worktree lane hand-edits
  ONLY its own entries — a full `size_ratchet.py --update` also sweeps in ~25 unrelated
  main-landed enrollments/tightenings (conflict surface for sibling lanes; the supervisor's
  merge-side ratchet pass owns those).
