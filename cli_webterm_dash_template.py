"""airuleset webterm — the dashboard HTML/CSS/JS template (#694).

Extracted VERBATIM from ``cli_webterm.py`` (where it lived as the inline
``_DASHBOARD_TEMPLATE``, lines 995-1636 pre-extraction) so the LOGIC module
stops growing on pure asset churn: every dashboard CSS/JS ticket
(#643/#661/#672/#677/#678/#691/#700) edits THIS string, and the size ratchet
now governs the asset separately from the inventory/provisioning/connect
logic.

PURE CONSTANT LEAF by design (locked by
``tests/test_webterm_template_extraction_694.py``): zero imports, zero
functions — logic never creeps in here. The ``@@BUTTONS@@`` / ``@@CFG_JSON@@``
/ ``@@THEME_JSON@@`` sentinels are substituted by
``cli_webterm.render_dashboard_html`` in a SINGLE pass; the render/substitution
contract lives THERE — this module carries only the bytes. A new sentinel needs
BOTH sides in one commit (token here + subst/regex there), or the #694 test fails.
"""

# NOTE: sentinel substitution, not `%`-formatting — the CSS/JS body is full
# of `{}`, `%`, and `:` that would otherwise need escaping.
DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fleet terminal</title><!-- #655: real domain set client-side from location.hostname (below) -->
<!-- #644: installable PWA — standalone window, no browser chrome. The manifest
     (per-domain name), icons and service worker are served by the gateway from
     the dash dir (behind Cloudflare Access). theme-color matches #643 Campbell. -->
<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">
<meta name="theme-color" content="#0C0C0C">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
/* #643: Campbell-consistent dark chrome (pure black + neutral near-black
   shades), so the whole surface matches the vivid Campbell terminals inside
   the iframes instead of the old grey GitHub-dark theme. */
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; background: #0C0C0C; color: #CCCCCC;
  font: 13px/1.3 "Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", Menlo, Monaco, "Liberation Mono", monospace;
  overflow: hidden; }
#tabbar { display: flex; align-items: stretch; gap: 2px; padding: 4px 6px 0;
  background: #0C0C0C; border-bottom: 1px solid #2b2b2b; overflow-x: auto;
  flex: 0 0 auto; white-space: nowrap; }
.tab { display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 9px 12px 9px 16px; border: 1px solid transparent; border-bottom: none; /* #661: left 12->16px; #691 rework: top/bottom 6->9px = real vertical breathing so nothing touches the top edge (owner: "zhora a zdola skoro vobec"; taller tab explicitly OK) */
  border-radius: 7px 7px 0 0; background: #1b1b1b; color: #CCCCCC;
  font: inherit; line-height: 1; max-width: 170px; flex: 0 0 auto; }
/* #691 rework: the separate absolutely-positioned corner .udot (the #677 dot)
   is RETIRED — the owner rejected it as the LOUDEST element ("mala byt doplnok,
   teraz je najvyraznejsi prvok"). The U state now recolours the left arrow (see
   .tab.has-u .ico below), so .tab needs no positioning anchor any more. */
/* #661: unselected tab text lightened from #9a9a9a to the Campbell foreground
   #CCCCCC (owner: hard to read); hover brightens to #F2F2F2; the ACTIVE tab stays
   the lightest (#F2F2F2). Restrained Campbell greys, never garish. */
.tab:hover { background: #262626; color: #F2F2F2; }
/* #691 REWORK (owner rejected v0.1.55): the top inset accent bar touched the
   label ("text tabu sa dotyka modreho pruzku") — solve the selected state
   differently, in the hierarchy NAME > SELECTED > U. The active tab is the
   LIGHTEST shade on the ramp (inactive #1b1b1b < hover #262626 < active #333333
   — hover can never masquerade as active), a rim lightened to stay visible on
   the lighter body, a bold label (the NAME, rank 1), and a 2px Campbell-
   brightBlue accent on the BOTTOM edge (inset -2px = a familiar "selected tab"
   underline just above the tabbar seam; never touches the label, zero layout
   shift). Declared AFTER .tab:hover on purpose: equal specificity, so source
   order keeps a hovered ACTIVE tab from dimming to the hover shade. */
.tab.active { background: #333333; color: #F2F2F2; border-color: #3f3f3f;
  box-shadow: inset 0 -2px 0 0 #3B78FF; }
.tab.active .al { font-weight: 700; }
.tab .ico { color: #13A10E; font-size: 11px; }
/* #691 rework: the U indicator is the LEAST-prominent accessory (rank 3) — the
   existing green ▸ arrow simply turns Campbell brightRed when the tab's box has
   U > 0. Specificity (0,3,0) beats .tab .ico (0,2,0) and is state-agnostic vs
   .active, so an active+U tab shows a red arrow on the #333333 body (legible
   contrast). Replaces the retired .udot badge; applyUStatus's .has-u toggle and
   the whole U-collector plumbing are UNCHANGED. */
.tab.has-u .ico { color: #E74856; }
.tab .al { overflow: hidden; text-overflow: ellipsis; }
#nav { position: sticky; left: 0; z-index: 1; display: inline-flex; gap: 2px;
  padding-right: 4px; margin-right: 2px; background: #0C0C0C; flex: 0 0 auto; }
.cyc { cursor: pointer; border: 1px solid #2b2b2b; border-radius: 6px;
  background: #1b1b1b; color: #9a9a9a; font: inherit; line-height: 1;
  padding: 6px 9px; }
.cyc:hover { background: #262626; color: #CCCCCC; }
#frames { position: relative; flex: 1 1 auto; overflow: hidden; /* #700: clips the stretch spill */ }
#frames iframe.term { position: absolute; inset: 0; width: 100%; height: 100%;
  border: 0; background: #0C0C0C; }
/* #671 REWORK (owner ruling 2026-08-25, verbatim "znova si napchal text na copy
   paste ktory zerie spodnu cast !!!!! nic take som nechcel, potrebujem hlavne
   pracovnu plochu nie tvoje blbe vysvetlivky"): the copy/paste footer hint strip
   is removed entirely so the terminal reclaims the height. The select/copy/paste
   FUNCTIONALITY (attachClipboard) stays; only the visible strip is gone. */
</style>
</head>
<body>
<div id="tabbar">
<span id="nav"><button class="cyc" id="fs" title="Fullscreen — Ctrl+W pôjde do terminálu (Keyboard Lock)">&#9974;</button></span>
@@BUTTONS@@
</div>
<div id="frames"></div>
<script>
const CFG = @@CFG_JSON@@;
// #672 REWORK (owner ruling 2026-08-25): ONE canonical grid for every tab, so
// CFG.term_cols/term_rows are a plain constant (no per-tab defineProperty
// getter). fitFixedGrid/fillFixedGrid read them unchanged; the foreign-stream
// footer crop is solved on the tmux side (window-size manual pin), not here.
// #643: the Campbell palette (single source of truth) + a Cascadia-ish system
// monospace stack (no external font fetch — CSP/Cloudflare-Access safe). Applied
// to each terminal via `term.options.theme` on the same-origin `window.term`
// (the #613 integration point), so it is DAEMON-AGNOSTIC (ttyd AND a future
// GoTTY both expose `window.term`) — never baked into a ttyd -t theme= flag.
const CAMPBELL_THEME = @@THEME_JSON@@;
const TERM_FONT_STACK = '"Cascadia Mono", "Cascadia Code", Consolas, "DejaVu Sans Mono", Menlo, Monaco, "Liberation Mono", monospace';
// #655: dynamic document title from the ACTUAL serving host. The old hardcoded
// legacy domain was NXDOMAIN; the live hosts are zbynek/david.newlevel.media, and
// this static file is served across BOTH. location.hostname is the honest
// per-viewer value, so the PWA/browser window title always names the real domain.
try { document.title = location.hostname + ' — fleet terminal'; } catch (e) {}
// #678: caps that BOUND the residual NATIVE-cell fill in fillFixedGrid. The fixed
// 176x51 grid letterboxes on any viewport whose aspect != the grid's; fillFixedGrid
// fills the residual by growing the REAL xterm cell -- letterSpacing (width) +
// lineHeight (height), NEVER a CSS transform (which would scale getBoundingClientRect
// but not xterm's cssCellHeight and break mouse hit-testing, the #678 regression).
// The fontSize min-fit does the crisp bulk. These cap the per-axis cell stretch so an
// extreme viewport (a phone) degrades to a residual letterbox instead of a grotesque
// stretch.
const WT_FILL_MAX_CELL_STRETCH = 1.5;   // cell WIDTH (letterSpacing) may grow up to 1.5x
const WT_FILL_MAX_LINE_STRETCH = 1.8;   // cell HEIGHT (lineHeight) may grow up to 1.8x
// #700: cap for the third, RESIDUAL fill layer (stretchFrameToFill) -- the
// parent-side iframe stretch that removes the integer-cell letterbox the native
// fill cannot (its quantum is cols/rows x 1px per axis, ~176px horizontally).
const WT_FRAME_FILL_MAX_STRETCH = 1.25; // parent-frame residual scale, per axis
// #798: floor for the parent DOWN-scale in reconcileFrameFit (below). When the fixed
// 176x51 grid OVERFLOWS a viewport too SHORT to fit it even at fitFixedGrid's 6px font
// floor (owner's compact window: natural grid ~357px vs a ~312px slot), the iframe is
// grown to the grid's own size -- so the child paints ALL 51 rows unclipped -- then
// scaled DOWN into the slot. The floor bounds that shrink for readability: within the
// floor the WHOLE grid fits; only for a PATHOLOGICALLY short viewport (needing a scale
// below the floor) does the scaled box still exceed the slot -- and because the top is
// pinned (origin y=0), what #frames then clips is the BOTTOM, so row 0 (the ticket's
// casualty) always survives. A deliberate wholeness-vs-readability trade at the extreme.
const WT_FRAME_FILL_MIN_SHRINK = 0.5;   // parent-frame down-scale floor (over-fit only)
function themeTerminal(term) {           // idempotent: applied once per terminal
  if (!term || term.__wtThemed) return;
  term.options.theme = CAMPBELL_THEME;
  term.options.fontFamily = TERM_FONT_STACK;
  term.__wtThemed = true;
}
const frames = document.getElementById('frames');
const made = {};
let current = 0;                          // the active tab index (tab click / Ctrl+Alt+1..9)
function ttydSrc(s) { return CFG.ttyd_base + '/?arg=' + encodeURIComponent(s.id); }
function makeFrame(idx, s) {                // #586: create + CONNECT one iframe ONCE, hidden.
  if (made[idx]) return made[idx];         // idempotent — an iframe is never re-created/reloaded,
  const f = document.createElement('iframe');   // so it is never navigated after its first load
  f.className = 'term';
  f.addEventListener('load', () => attachForwarder(f));  // #584 same-origin keydown
  f.style.display = 'none';
  f.src = ttydSrc(s);                      // connected at creation (preloaded, keepalive)
  f.dataset.live = '1';
  frames.appendChild(f);
  made[idx] = f;
  return f;
}
function preloadAll() {                     // #586: connect EVERY tab at login. Supersedes #585's
  CFG.sessions.forEach((s, i) => makeFrame(i, s));   // disconnect-on-hide, which made switching
}                                           // slow (a reconnect each time) AND fired ttyd's own
                                            // beforeunload ("Leave site?") on every tab click.
                                            // #613 REOPEN-2: the tmux window is FIXED (window-size
                                            // manual + default-size 176x50) and every webterm attach
                                            // is -f ignore-size (#613 REOPEN-3: a direct attach now,
                                            // no clone), so NO tab — hidden or active — can ever
                                            // resize a window; keeping every tab connected is
                                            // unconditionally safe. Each ttyd xterm is force-fit to the
                                            // fixed grid on the browser side (applyFixedGrid).
function hasLiveTerminal() {                // gate for the beforeunload close-confirm
  for (const k in made) if (made[k].dataset.live === '1') return true;
  return false;
}
function activate(idx) {
  const s = CFG.sessions[idx];
  if (!s) return;
  makeFrame(idx, s);                        // already preloaded — idempotent no-op
  // #586: PURE show/hide. Every tab stays CONNECTED (preloaded), so switching is
  // instant with no reconnect AND no iframe navigation — which is exactly why a
  // tab click never fires ttyd's own beforeunload. Only `display` toggles here.
  for (const k in made) {
    made[k].style.display = (+k === idx) ? 'block' : 'none';
  }
  document.querySelectorAll('.tab').forEach((t) => {
    const on = +t.dataset.idx === idx;
    t.classList.toggle('active', on);
    // #582: keep the active tab visible even when the bar has scrolled past it
    // (e.g. a Ctrl+Alt+9 jump to a tab scrolled off-screen). #674 removed the
    // ◀ ▶ cycle buttons, so tab clicks + Ctrl+Alt+1..9 are the switch paths now.
    // Optional call: a browser without scrollIntoView must never break switching.
    if (on) t.scrollIntoView?.({ inline: 'nearest', block: 'nearest' });
  });
  current = idx;
  applyFixedGrid(made[idx]);                 // #613 REOPEN-2: fit the now-VISIBLE tab
  focusTerminal(made[idx], idx);             // #661: type immediately after a switch
  reviveTerminal(made[idx], idx);            // #673: auto-reconnect a slept tab, no manual Enter
}
// #584: ONE keydown handler shared by the parent tab bar AND every terminal
// iframe. Ctrl+Alt+1..9 jumps to a tab. Because the gateway now serves the
// dashboard AND ttyd on the SAME origin, each ttyd iframe is same-origin, so we
// can attach this listener INSIDE each terminal's own window (capture phase,
// before xterm consumes the key) — which is what makes the shortcut work even
// while focus is IN a terminal, the #582 residual this supersedes.
// stopPropagation keeps xterm from also acting on the (unused) Ctrl+Alt+digit
// chord. (Still no Ctrl+Alt+arrow binding: Ctrl+Alt+Left/Right is the Linux
// desktop workspace-switch shortcut, grabbed by the compositor before the page
// sees it — advertising it would over-promise; a tab click covers arbitrary jumps.)
function onHotkey(e) {
  if (!(e.ctrlKey && e.altKey)) return;
  if (e.key >= '1' && e.key <= '9') {
    const idx = parseInt(e.key, 10) - 1;
    if (idx < CFG.sessions.length) {
      e.preventDefault(); e.stopPropagation(); activate(idx);
    }
  }
}
function attachForwarder(f) {
  // Reach into the terminal iframe's own window (same-origin) and intercept the
  // hotkey in the CAPTURE phase, before xterm. A cross-origin frame would throw
  // here — impossible under the gateway, but caught so one frame can never break
  // the page or the rest of switching.
  if (f.dataset.live !== '1') return;      // defensive: only forward for a live frame
  try {
    const w = f.contentWindow;
    if (w) w.addEventListener('keydown', onHotkey, true);
  } catch (err) { /* never same-origin under the gateway; switching stays alive */ }
}
// #661: move keyboard focus INTO the shown terminal after a tab switch (a tab
// click AND Ctrl+Alt+N both route through activate()), so the owner can type
// immediately with no extra click into the prompt. Same-origin under
// the gateway, so we reach the iframe's own xterm: `window.term.focus()` (which
// focuses xterm's helper textarea). ttyd connects async, so the term/textarea
// may not exist for the first activate(0) at load — retry briefly, best-effort.
// A cross-origin frame would throw (impossible under the gateway) — caught so a
// focus attempt can never break switching.
// GENERATION GUARD (#661 review): `idx` is the tab this chain focuses. ttyd may
// still be connecting, so a chain started for tab A can outlive a fast switch to
// tab B; without the guard, A's late-connecting term would steal focus back to
// the now-HIDDEN A. Each retry bails the moment `current` has moved on, so
// last-activate always wins and a superseded chain becomes a no-op.
function focusTerminal(f, idx) {
  if (!f) return;
  let tries = 0;
  const tryFocus = () => {
    if (idx !== current) return;            // superseded by a newer switch -> stop
    try {
      const w = f.contentWindow;
      if (w && w.term && typeof w.term.focus === 'function') { w.term.focus(); return; }
      const ta = w && w.document &&
        w.document.querySelector('.xterm-helper-textarea, textarea');
      if (ta) { ta.focus(); return; }
    } catch (err) { return; }               // never same-origin-throws under gateway
    if (++tries < 30) setTimeout(tryFocus, 100);   // ttyd/xterm still connecting
  };
  tryFocus();
}
// #673: detect ttyd 1.7.4's PERSISTENT reconnect-wait overlay. Empirically (real
// ttyd + Playwright): a child that merely EXITS closes with WS code 1006 and ttyd
// AUTO-reconnects; but a failed reconnect ATTEMPT fires ttyd's WS `error` handler
// (doReconnect=false) and the next close parks on "Press ⏎ to Reconnect" -- an
// OverlayAddon div (position:absolute, fontSize xx-large, appended to term.element,
// NO auto-hide) that then waits for a manual Enter. Return that overlay node iff a
// reconnect-wait prompt is CURRENTLY showing, else null. The discriminator is the
// TEXT: "Reconnecting..." is ttyd self-recovering (leave it); ttyd's resize overlay
// is xx-large too but its text is grid dimensions, so we key on the reconnect text.
function ttydReconnectOverlay(win) {
  try {
    const t = win && win.term;
    if (!t || !t.element) return null;
    const nodes = t.element.querySelectorAll('div');
    for (const n of nodes) {
      if (n.style && n.style.fontSize === 'xx-large' && n.parentNode && n.style.opacity !== '0') {
        const txt = n.textContent || '';
        if (/Reconnect/.test(txt) && !/Reconnecting/.test(txt)) return n;   // "Press ⏎ to Reconnect"
        if (/Connection Closed/.test(txt)) return n;                        // transient stuck close
      }
    }
  } catch (e) { /* cross-origin (impossible under the gateway) -> treat as connected */ }
  return null;
}
// #673: if the activated tab is stuck on ttyd's reconnect prompt, press Enter FOR
// the owner -- a synthetic keydown on xterm's helper textarea is EXACTLY what
// ttyd's own onKey reconnect trigger listens for, so ttyd reconnects IN PLACE
// (tmux restores the full scrollback server-side) with no click, no Enter, and no
// iframe reload (so the whole-script single-src-assignment invariant + the
// #661/#671 beforeunload behaviour are untouched). A cooldown stops a tight loop
// if a backend is genuinely offline (a re-switch just tries again). Verified live
// against real ttyd 1.7.4: the synthetic Enter cleared the overlay and the fresh
// backend banner appeared in the buffer, reconnected with ZERO user input.
function reviveTerminal(f, idx) {
  try {
    const win = f && f.contentWindow;
    if (!win) return;
    if (!ttydReconnectOverlay(win)) return;         // healthy/self-recovering -> instant switch
    const now = Date.now();
    if (f.__wtReviveAt && now - f.__wtReviveAt < 3000) return;   // cooldown vs a dead-backend loop
    f.__wtReviveAt = now;
    const ta = win.document.querySelector('.xterm-helper-textarea, textarea');
    if (ta) {
      try { ta.focus(); } catch (e) {}
      const ev = new win.KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true});
      ta.dispatchEvent(ev);                          // ttyd's onKey Enter trigger -> reconnect
    }
  } catch (e) { /* iframe realm gone / cross-origin -> never break switching */ }
}
// #613 REOPEN-2: force each ttyd xterm to the owner's FIXED client grid
// (CFG.term_cols x CFG.term_rows = the fixed 176x50 tmux window + 1 status row)
// and scale the font so that grid FILLS the iframe viewport, centred. The tmux
// window is a FIXED size (window-size manual) and every webterm attach is
// -f ignore-size (#613 REOPEN-3: a direct attach now, no clone), so the
// browser NEVER resizes tmux; this only makes the browser SHOW the fixed
// window filling its viewport instead of a dark unused region. ttyd 1.7.4
// exposes the xterm Terminal as `window.term` in each same-origin iframe; we
// clamp term.resize so ttyd's own FitAddon can never change the grid, then
// scale term.options.fontSize (crisp re-render, unlike a blurry CSS transform).
// Verified live against real ttyd + headless Chrome: grid forced 176x51, no
// dead dotted region, status bar full width.
// #798: the TRUE available slot = the parent #frames content box, read from the PARENT
// document -- transform- AND box-independent (the iframe's own scale/explicit size never
// change #frames). fitFixedGrid fits the font to THIS (never the possibly-grown box), so
// a reconcileFrameFit box-grow can never inflate the font -> the feedback loop's runaway
// direction is closed at the source. Fallback to the iframe's own innerWidth/innerHeight
// where there is no frameElement/parentElement (the node fit-harness), so that path is
// behaviourally unchanged.
function slotOf(win) {
  const fr = win && win.frameElement;
  const p = fr && fr.parentElement;              // #frames -- the real slot
  if (p && p.clientWidth && p.clientHeight) return { w: p.clientWidth, h: p.clientHeight };
  return { w: win.innerWidth, h: win.innerHeight };
}
function fitFixedGrid(win) {
  const term = win && win.term, cols = CFG.term_cols, rows = CFG.term_rows;
  if (!term || !cols || !rows) return false;   // ttyd not connected yet -> retry
  const doc = win.document;
  if (!term.__wtClamped) {                      // clamp resize -> defeat ttyd's FitAddon
    const real = term.resize.bind(term);
    // #672 REWORK: one canonical grid for every tab, so the clamp re-pins to the
    // fixed (cols, rows) captured at fitFixedGrid entry (the single owner grid) --
    // no per-current-tab getter, no per-tab race to close.
    term.resize = () => real(cols, rows);
    term.__wtClamped = true;
  }
  try { term.resize(cols, rows); } catch (e) { return false; }
  const bg = (term.options.theme && term.options.theme.background) || '#0C0C0C';
  if (!doc.getElementById('wt-fit-style')) {    // centre + letterbox = terminal bg
    const st = doc.createElement('style');
    st.id = 'wt-fit-style';
    st.textContent =
      'html,body{width:100%;height:100%;margin:0;overflow:hidden;background:' + bg + ';}' +
      '#terminal-container{position:absolute!important;inset:0!important;display:flex!important;' +
      'align-items:center!important;justify-content:center!important;background:' + bg + ';}' +
      '#terminal-container .xterm{position:static!important;}';
    doc.head.appendChild(st);
  }
  const screenEl = () => doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
  const el = screenEl();
  if (!el) return false;                        // xterm not painted yet -> retry
  // #678: clear any prior FILL so the natural-cell measurement below (and the
  // font min-fit) is honest -- a re-fit must recompute from the natural grid,
  // not last run's stretched one. The fill is now NATIVE (lineHeight/letterSpacing
  // grow the REAL cell, keeping xterm's mouse hit-test correct); the CSS transform
  // clear stays only defensively (a pre-#678 deploy may have left one on .xterm).
  const fillTarget = () => doc.querySelector('.xterm') || el;
  fillTarget().style.transform = 'none';
  term.options.lineHeight = 1;
  term.options.letterSpacing = 0;
  // reads the element size right after resize/fontSize assuming xterm updates
  // the DOM synchronously (verified live: real ttyd + headless Chrome); the
  // bounded shrink loop below is the safety net if it ever lags by a frame.
  const r = el.getBoundingClientRect();
  const _slot = slotOf(win);                     // #798: fit to the SLOT, not a grown box
  const availW = _slot.w, availH = _slot.h;
  if (!r.width || !r.height || !availW || !availH) return false;  // hidden/0 -> retry
  const F0 = term.options.fontSize || 13;
  let F = Math.max(6, Math.min(40, Math.floor(F0 * Math.min(availW / r.width, availH / r.height))));
  term.options.fontSize = F;
  for (let i = 0; i < 8 && F > 6; i++) {        // bounded shrink so the grid never overflows
    const rr = (screenEl() || el).getBoundingClientRect();
    if (rr.width <= availW + 1 && rr.height <= availH + 1) break;
    term.options.fontSize = --F;
  }
  // #655/#678: the FILL (stretch the fixed grid to the viewport, killing the "okno
  // v strede" letterbox) is a SEPARATE deferred pass -- fillFixedGrid(win) below.
  // It must run AFTER this font change has settled. fitFixedGrid does the CRISP
  // bulk scaling (fontSize min-fit) and RESETS the native fill (lineHeight 1 /
  // letterSpacing 0, above) so its measurement is honest; fillFixedGrid measures
  // the settled natural grid and applies the residual fill via lineHeight/
  // letterSpacing (native cell growth -> correct mouse hit-test, #678), never a
  // CSS transform.
  return true;
}
// #678 FILL (deferred pass): the crisp fontSize min-fit in fitFixedGrid fills the
// TIGHT viewport dimension and letterboxes the LOOSE one (the owner's "okno v
// strede"). This pass fills that residual via NATIVE xterm cell geometry --
// lineHeight (taller cells, vertical) + letterSpacing (wider cells, horizontal) --
// NEVER a CSS transform. WHY (the #678 regression): a CSS `transform: scale()`
// scales getBoundingClientRect but NOT xterm's cssCellHeight, so xterm's mouse
// hit-test (row = ceil((clientY - rect.top) / cssCellHeight)) reports a cell BELOW
// the one the user points at, worse with depth (owner: "selectujem kde je kurzor
// ale vybera sa mi ovela nizsie"). Growing the REAL cell keeps render and hit-test
// consistent -- verified live (native fill: a click at every row's visual centre
// hit-tests to that row). TRADE-OFF: xterm quantizes BOTH the cell WIDTH
// (letterSpacing) and the cell HEIGHT (lineHeight) to INTEGER px/cell, so BOTH axes
// fill COARSELY -- each floors to the largest integer cell that fits, leaving a
// small residual letterbox (up to ~one cell per axis, <~5-9%) rather than
// overflowing/clipping -- #700's stretchFrameToFill (below) then removes that
// residual at the IFRAME boundary; per #678 a WORKING MOUSE outranks a
// same-document pixel-exact fill (owner:
// "funkčný select má prednosť"). #655 chose a CSS transform for exact fill precisely
// because letterSpacing/lineHeight quantize -- #678 reverses that trade for mouse
// correctness. Bounded (WT_FILL_MAX_*) so an extreme viewport letterboxes the
// remainder rather than distorting text. fitFixedGrid resets lineHeight/
// letterSpacing before its own measurement, so this pass measures the natural grid;
// xterm reflects an option change synchronously (the same path the fontSize min-fit
// relies on) and the scheduleFill re-runs re-converge after any late layout settle.
function fillFixedGrid(win) {
  const cols = CFG.term_cols, rows = CFG.term_rows;
  if (!win || !win.term || !cols || !rows) return false;
  const term = win.term;
  const el = win.document.querySelector('.xterm-screen') || win.document.querySelector('.xterm');
  if (!el) return false;
  // reset the native fill so the measurement is the NATURAL grid (self-contained +
  // idempotent -- a re-run recomputes from natural, never last run's stretched grid)
  term.options.lineHeight = 1;
  term.options.letterSpacing = 0;
  const g = el.getBoundingClientRect();            // NATURAL grid (fill just reset)
  const _slot = slotOf(win), availW = _slot.w, availH = _slot.h;  // #798: the SLOT, not a grown box
  if (!g.width || !g.height || !availW || !availH) return false;
  // vertical: taller cells via lineHeight. Target an INTEGER cell height that never
  // overflows -- xterm rounds cell HEIGHT to integer px (same as letterSpacing), so a
  // raw lineHeight = availH/g.height would make rows*round(cellH) EXCEED availH (up to
  // ~rows/2 px) and CLIP the bottom row / status bar under the container's
  // overflow:hidden. floor(availH/rows) is the largest integer cell height that fits,
  // so rows*cellH <= availH always (a small residual letterbox, exactly like the
  // horizontal axis). Bounded to WT_FILL_MAX_LINE_STRETCH of the natural cell; never
  // < 1 = a shrink, not a fill (fitFixedGrid already fit the grid within availH, so
  // floor(availH/rows) >= the natural integer cell height).
  const nCellH = g.height / rows;
  const tCellH = Math.min(Math.floor(availH / rows),
                          Math.floor(nCellH * WT_FILL_MAX_LINE_STRETCH));
  let lh = Math.max(1, tCellH / nCellH);
  // horizontal: wider cells via letterSpacing (INTEGER px/cell -> coarse). FLOOR,
  // never round: round can push cols*cellW PAST availW and CLIP the grid (worse
  // than a letterbox); floor is the largest integer px/cell that never overflows,
  // leaving a small residual letterbox. Bounded to WT_FILL_MAX_CELL_STRETCH of the
  // natural cell width, never below 0.
  const naturalCellW = g.width / cols;
  const ls = Math.floor(Math.max(0, Math.min(naturalCellW * (WT_FILL_MAX_CELL_STRETCH - 1),
                                             (availW - g.width) / cols)));
  term.options.lineHeight = +lh.toFixed(4);
  term.options.letterSpacing = ls;
  // CORRECTIVE (vertical): the rendered cell height is round(charHeight*lineHeight),
  // and a FRACTIONAL charHeight can round the floor target UP by 1px -> a 1-cell
  // overflow that CLIPS the bottom row. getBoundingClientRect reflects the option
  // SYNCHRONOUSLY (the same reflow the fontSize min-fit relies on), so step lineHeight
  // down one integer cell until the grid fits -- a bounded safety net mirroring
  // fitFixedGrid's font-shrink loop; letterSpacing needs none (its floor is exact).
  for (let i = 0; i < 4 && lh > 1; i++) {
    if (el.getBoundingClientRect().height <= availH + 1) break;
    lh = Math.max(1, lh - 1 / nCellH);
    term.options.lineHeight = +lh.toFixed(4);
  }
  return true;
}
// #700 EXACT FILL (third layer): fitFixedGrid (integer fontSize) and
// fillFixedGrid (integer px/cell letterSpacing/lineHeight) BOTH quantize, so a
// residual letterbox of up to cols/rows x 1px per axis remains -- the owner's
// ~78 CSS px side margins + the "empty row" under the status bar (#700; NOT a
// grid row: the status bar occupies grid row 51, geometry is correct). This
// pass removes it EXACTLY by scaling the tab's IFRAME from the PARENT document
// by the sub-cell residual (typically <1.1x -- layers 1+2 do the crisp bulk).
// WHY THIS IS MOUSE-SAFE where the #655 same-document transform was not (#678):
// the transform lives in the PARENT document, so the child xterm document's
// coordinate space is untouched -- its getBoundingClientRect AND pointer
// clientX/Y are both in the child's own layout space (the browser inverse-maps
// pointer events through ancestor-document transforms), so xterm's
// (clientY - rect.top)/cssCellHeight mapping stays exact at every row.
// win.innerWidth/innerHeight stay the LAYOUT size and a parent-side style
// change can never re-fire the child's ResizeObserver -> no feedback loop. The
// grid is flex-CENTERED in the child, so scaling about the frame center lands
// the grid edges exactly on the frame edges; the scaled letterbox spills
// OUTSIDE the frame box and #frames{overflow:hidden} clips it. Capped
// (WT_FRAME_FILL_MAX_STRETCH) so a pathological viewport keeps a bounded
// letterbox instead of distorting; an already-exact grid gets 'none' (no
// pointless compositing layer).
function stretchFrameToFill(win) {
  const fr = win && win.frameElement;       // same-origin under the gateway
  if (!fr || !win.term) return false;
  const doc = win.document;
  const el = doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
  if (!el) return false;
  const g = el.getBoundingClientRect();     // CHILD layout coords (settled fill)
  const _slot = slotOf(win), availW = _slot.w, availH = _slot.h;  // #798: the SLOT, not a grown box
  if (!g.width || !g.height || !availW || !availH) return false;
  const sx = Math.min(WT_FRAME_FILL_MAX_STRETCH, Math.max(1, availW / g.width));
  const sy = Math.min(WT_FRAME_FILL_MAX_STRETCH, Math.max(1, availH / g.height));
  fr.style.transformOrigin = '50% 50%';
  fr.style.transform = (sx <= 1.0005 && sy <= 1.0005)
    ? 'none' : 'scale(' + sx.toFixed(4) + ', ' + sy.toFixed(4) + ')';
  return true;
}
// #798 OVER-FIT (fourth layer): fitFixedGrid floors the font at 6px, so on a viewport
// too SHORT to fit the fixed 176x51 grid even at that floor (owner's compact window:
// natural grid ~357px tall vs a ~312px slot) the grid OVERFLOWS and the child's own
// html,body{overflow:hidden} CLIPS it -- the owner's row-0 shell prompt (or, per the
// flex resolution, the tmux status bar) vanishes. stretchFrameToFill CANNOT fix this:
// a PARENT transform rescales the child's ALREADY-COMPOSITED bitmap, so it cannot
// reveal a row the child never painted (the #798 review 🔴, proven live: 0 marker
// pixels before AND after a shrink transform on the clipped child). This layer instead
// GROWS the iframe's real CSS layout box (which the child sees as win.innerHeight) to
// COVER the grid's own extent -- so the child paints ALL 51 rows with NO internal clip
// -- then applies a parent UNIFORM down-scale (origin '50% 0', top-centre) to fit that
// fully-painted box back into the slot: row 0 pinned at the slot top, the whole grid
// visible. Proven live (real ttyd 1.7.4 + tmux + headless Chromium at 723x312): box
// grown 312->366, scale 0.8525, the whole grid maps to parent y 41.3..345.6 inside the
// 37..349 slot (RED: grid clipped past 349). Runs LAST in scheduleFill's pass, so it
// OVERRIDES stretchFrameToFill's grow-only transform in the over-fit case and clears
// its own box + defers to #700 in the under-fit case. Feedback-loop guard (a naive box
// grow re-fires the child 'resize' -> fitFixedGrid -> bigger font -> bigger grid ->
// runaway): (1) fitFixedGrid fits the font to slotOf (the slot), NEVER the grown box,
// so a grow can't inflate the font; (2) the box is grown only to cover the (font-fixed)
// grid, so it never runs away; (3) the box is mutated only when it DIFFERS from the
// target -> idempotent, so a settled pass makes no change and the resize stops firing;
// (4) the child 'resize' our own grow fires is IGNORED via win.__wtSetBox (see
// applyFixedGrid), while a GENUINE slot change is driven by the parent #frames
// ResizeObserver (our box mutations never resize #frames, so it never self-triggers).
function reconcileFrameFit(win) {
  const fr = win && win.frameElement;       // same-origin under the gateway
  if (!fr || !win.term) return false;
  const doc = win.document;
  const el = doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
  if (!el) return false;
  const slot = slotOf(win);                 // the real #frames slot (parent-read)
  const g = el.getBoundingClientRect();      // CHILD coords
  if (!slot.w || !slot.h || !g.width || !g.height) return false;
  // The over-fit check + box size use the grid's OWN dimensions (g.width/g.height),
  // which are font-determined and PLACEMENT-independent -- so the box target is a fixed
  // function of the (stable) font, never of the child's centring offset. (Using the
  // extent g.right/g.bottom instead would count the post-grow centring offset and make
  // the box creep a few px per pass before settling.) The +16 slack absorbs the child's
  // own top/left padding (ttyd's ~5px), so the grid fits inside the grown box wherever
  // the child places it (top-aligned OR centred), regardless of clip direction.
  const natW = g.width, natH = g.height;
  const over = (natW > slot.w + 1) || (natH > slot.h + 1);
  if (!over) {                               // fits crisply -> box = slot, leave #700's transform
    if (fr.style.width || fr.style.height) { fr.style.width = ''; fr.style.height = ''; }
    win.__wtSetBox = null;                   // slot-sized: no self-induced-resize marker
    return true;
  }
  // OVER-FIT: grow the box to cover the grid (+ slack for the child offset), uniform down-scale.
  const boxW = Math.max(slot.w, Math.ceil(natW) + 16);
  const boxH = Math.max(slot.h, Math.ceil(natH) + 16);
  const wpx = boxW + 'px', hpx = boxH + 'px';
  if (fr.style.width !== wpx) fr.style.width = wpx;     // mutate only on change -> idempotent
  if (fr.style.height !== hpx) fr.style.height = hpx;
  win.__wtSetBox = { w: boxW, h: boxH };     // the resize this fires is OUR OWN -> guarded
  const s = Math.max(WT_FRAME_FILL_MIN_SHRINK, Math.min(slot.w / boxW, slot.h / boxH));
  // Pin the TOP at the slot top (row 0 always survives) and CENTRE horizontally in the
  // SLOT. transformOrigin '0 0' + an explicit translateX is exact for BOTH axes: a plain
  // 'scale() origin 50% 0' would centre about the GROWN BOX's centre, which only equals
  // the slot centre when boxW == slot.w -- so a WIDTH over-fit (a viewport narrower than
  // the 704px grid) would shift the grid right and clip it. translateX = half the slack
  // between the scaled box and the slot (0 when the width axis is the limiting one -> the
  // grid fills the width; positive when height-limited -> centred letterbox).
  const tx = (slot.w - s * boxW) / 2;
  fr.style.transformOrigin = '0 0';
  const t = 'translate(' + tx.toFixed(1) + 'px, 0px) scale(' + s.toFixed(4) + ')';
  if (fr.style.transform !== t) fr.style.transform = t;
  return true;
}
// #655/#678: the FILL must re-run whenever the NATURAL grid size settles/changes.
// xterm's grid layout can settle noticeably AFTER first paint (font metrics, the
// multi-tab layout). The AUTHORITATIVE driver is a ResizeObserver on .xterm-screen,
// which fires on the REAL layout size both on observe AND on every late settle, so
// the fill always tracks the true natural grid and converges. NO ping-pong: each
// fillFixedGrid run RESETS to the natural cell, measures, and re-applies the SAME
// deterministic integer-cell target, so a settled layout produces no net size change
// and the RO stops (a mid-settle change re-fires it, and it converges to that new
// natural size). The immediate call + the timed passes are only a best-effort first
// paint and a fallback for a browser without ResizeObserver: they may run on a
// still-settling (stale-small) grid, but fillFixedGrid is self-contained + idempotent
// (resets the native fill, re-measures, re-applies), and the floor targets never
// overflow, so the RO corrects any transient the instant the layout settles -- no
// persistent clip.
function scheduleFill(win) {
  // #700: every fill pass ends with the residual frame stretch (third layer).
  // #798: reconcileFrameFit runs LAST so it OVERRIDES stretchFrameToFill's grow-only
  // transform in the over-fit case (and clears its box + defers to #700 when it fits).
  const pass = () => { try { fillFixedGrid(win); stretchFrameToFill(win); reconcileFrameFit(win); } catch (e) {} };
  pass();
  try {
    const doc = win.document;
    const el = doc.querySelector('.xterm-screen') || doc.querySelector('.xterm');
    if (el && win.ResizeObserver && !win.__wtFillRO) {
      win.__wtFillRO = new win.ResizeObserver(pass);
      win.__wtFillRO.observe(el);
    }
  } catch (e) {}
  [200, 800, 2000].forEach((ms) => { setTimeout(pass, ms); });   // guard inside `pass`
}
// #671: mouse select/copy -> browser clipboard. Empirically verified against a
// real ttyd 1.7.4 replica (Playwright + tmux capture-pane): ttyd's bundled xterm
// frontend registers NO OSC 52 handler, so a tmux copy-mode mouse drag (which,
// under `set-clipboard external`, emits OSC 52) never reaches the browser
// clipboard -- the owner's reported symptom. We register an OSC 52 handler on
// xterm's OWN parser API and mirror the decoded payload to navigator.clipboard,
// PLUS a copy-on-select mirror for native xterm selections (Shift+drag). Both are
// wired through the same same-origin `window.term` bridge as #613/#643/#661, use
// the IFRAME's own (focused) navigator.clipboard, and are fully guarded so a
// missing/denied clipboard never throws. Paste needs no code: Ctrl+Shift+V pastes
// natively (browser paste event -> xterm), plain Ctrl+V is ^V and is NOT rebound
// (a legit readline key). (#671 rework removed the on-screen copy/paste hint strip.)
function attachClipboard(win) {                  // idempotent: attach once per terminal
  const term = win && win.term;
  if (!term || term.__wtClip) return;
  term.__wtClip = true;
  const clip = (win.navigator && win.navigator.clipboard) || null;   // iframe realm = focused doc
  const write = (text) => {
    try { if (clip && clip.writeText && text) clip.writeText(text).catch(function () {}); }
    catch (e) { /* clipboard unavailable/denied -> silently skip */ }
  };
  try {
    if (term.parser && term.parser.registerOscHandler) {
      term.parser.registerOscHandler(52, (data) => {
        try {
          const parts = String(data).split(';');           // "<targets>;<base64>" (targets may be empty)
          const b64 = parts.length > 1 ? parts[parts.length - 1] : parts[0];
          if (b64 && b64 !== '?') write(decodeURIComponent(escape(win.atob(b64))));  // '?' = read-request
        } catch (e) { /* invalid base64 / read-request -> ignore */ }
        return true;                                        // we own OSC 52
      });
    }
  } catch (e) { /* older xterm without registerOscHandler -> no OSC 52 bridge */ }
  try {
    if (typeof term.onSelectionChange === 'function') {
      term.onSelectionChange(() => {
        try { const s = term.getSelection(); if (s) write(s); } catch (e) {}
      });
    }
  } catch (e) {}
}
function applyFixedGrid(f) {                     // poll for window.term, fit, then watch resize
  if (!f) return;
  const win = f.contentWindow;
  if (!win) return;
  let tries = 0;
  const poll = () => {
    themeTerminal(win.term);                     // #643: Campbell palette + font, once term exists
    attachClipboard(win);                        // #671: OSC 52 + copy-on-select -> browser clipboard
    if (fitFixedGrid(win)) {
      scheduleFill(win);                           // deferred FILL passes (below)
      if (!win.__wtResize) {                       // re-fit + re-fill on window resize
        win.__wtResize = true;
        try {
          win.addEventListener('resize', () => {
            // #798 feedback-loop guard: IGNORE the resize our own reconcileFrameFit
            // box-grow fired (its new inner size == the box we deliberately set) so it
            // can never re-enter the fit loop; a GENUINE viewport change never matches
            // __wtSetBox and still re-fits (a slot change while the box is explicitly
            // grown is driven by the parent #frames ResizeObserver instead).
            const b = win.__wtSetBox;
            if (b && Math.abs(win.innerWidth - b.w) <= 1 && Math.abs(win.innerHeight - b.h) <= 1) return;
            fitFixedGrid(win); scheduleFill(win);
          });
        } catch (e) {}
      }
      return;
    }
    if (++tries < 100) setTimeout(poll, 100);    // ttyd connects async after iframe load
  };
  poll();
}
document.querySelectorAll('.tab').forEach((t) =>
  t.addEventListener('click', () => activate(+t.dataset.idx)));
// #674: the prev/next cycle arrows and the ? help toggle are removed (owner: keep
// only fullscreen); tab switching stays via tab clicks + Ctrl+Alt+1..9 (onHotkey).
// #671 REWORK (owner ruling 2026-08-25): the copy/paste footer hint strip + its
// isSecureContext honesty-rewrite are removed entirely (owner: "potrebujem hlavne
// pracovnu plochu nie tvoje blbe vysvetlivky"). The copy bridge (attachClipboard)
// stays; only the visible hint is gone.
// #585(b): Ctrl+W is readline delete-word in the terminal but the browser
// consumes it as close-tab (a reserved shortcut a normal window cannot
// preventDefault). Layer 1 — a beforeunload confirm armed WHILE a terminal is
// connected, so a stray Ctrl+W (or any close) shows Chrome's confirm instead of
// a silent tab loss. Gated on hasLiveTerminal() so nothing warns before a
// terminal is open.
window.addEventListener('beforeunload', (e) => {
  if (!hasLiveTerminal()) return;
  e.preventDefault();
  e.returnValue = '';                     // Chrome's standard leave-page confirm
});
// Layer 2 — a Fullscreen button that requests fullscreen + Keyboard Lock, so
// Chrome delivers Ctrl+W (and Ctrl+T/N) to the PAGE => the terminal as
// delete-word, not the browser. Feature-detected + gated on a SECURE CONTEXT:
// the Keyboard Lock API is only exposed on HTTPS/localhost, so over the plain-HTTP
// tailnet `navigator.keyboard` is undefined and the button is disabled — with a
// title that names the REAL reason (needs HTTPS) rather than a false "browser
// unsupported". Layer 1 (the close-confirm) still protects Ctrl+W there.
const KB_LOCK_KEYS = ['KeyW', 'KeyT', 'KeyN'];
function keyboardLockSupported() {
  return !!(document.documentElement.requestFullscreen
            && window.isSecureContext
            && navigator.keyboard && navigator.keyboard.lock);
}
async function goFullscreen() {
  try {
    await document.documentElement.requestFullscreen();
    if (navigator.keyboard && navigator.keyboard.lock) {
      await navigator.keyboard.lock(KB_LOCK_KEYS);
    }
  } catch (err) { /* denied/unsupported — the hint documents the fallbacks */ }
}
const fsBtn = document.getElementById('fs');
if (fsBtn) {
  if (keyboardLockSupported()) {
    fsBtn.addEventListener('click', goFullscreen);
  } else {
    fsBtn.disabled = true;
    fsBtn.title = !window.isSecureContext
      ? 'Keyboard Lock vyžaduje HTTPS/localhost — cez HTTP tailnet Ctrl+W chráni potvrdenie pri zatváraní'
      : 'Fullscreen + Keyboard Lock nie je v tomto prehliadači podporený';
  }
}
window.addEventListener('keydown', onHotkey);   // Ctrl+Alt+1..9 when the bar is focused
preloadAll();                           // #586: connect every tab up front (instant switching)
if (CFG.sessions.length) activate(0);   // land in the first terminal, not a landing page
// #798: drive a re-fit of the ACTIVE tab from a PARENT-side ResizeObserver on #frames
// (the real slot). An over-fit tab grows its iframe to an EXPLICIT box, which then goes
// deaf to a genuine viewport change via the child's own 'resize' (the box no longer
// tracks #frames); this observer catches the slot change regardless. Our own iframe box
// mutations never resize #frames, so it never self-triggers -- only a genuine viewport /
// tab-bar height change fires it.
try {
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      const f = made[current], win = f && f.contentWindow;
      if (win && win.term) { fitFixedGrid(win); scheduleFill(win); }
    }).observe(frames);
  }
} catch (e) {}
// #644: register the minimal NETWORK-ONLY service worker (Chromium
// installability). Best-effort — a registration failure never breaks the page.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(function () {});
}
// #677 + #691 rework: the per-tab U indicator. Poll the gateway's /u-status
// (the aggregated per-box U map) and toggle a tab's .has-u iff its target box
// currently has U > 0 -- a navigation hint that a question/approval is waiting
// on the owner there. .has-u recolours the tab's left ▸ arrow red (see
// .tab.has-u .ico; the #677 corner dot was retired in #691). Read LIVE (never a
// build-time value); a fetch failure leaves the current arrow colours untouched
// (graceful). The gateway fire-and-forgets a fresh collect on a stale read, so a
// short burst after load catches it, then a steady minutes cadence.
function applyUStatus(map) {
  document.querySelectorAll('.tab').forEach((t) => {
    const s = CFG.sessions[+t.dataset.idx];
    const u = s && map ? map[s.id] : undefined;
    t.classList.toggle('has-u', typeof u === 'number' && u > 0);
  });
}
function pollUStatus() {
  fetch('/u-status', { credentials: 'same-origin', cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && d.u) applyUStatus(d.u); })
    .catch(() => {});                     // absent/failed -> leave the dots as-is
}
if (CFG.u_status) {     // #677 owner; #703 lane (per-tenant scoped gateway map)
  pollUStatus();
  [4000, 12000, 30000].forEach((ms) => setTimeout(pollUStatus, ms));   // burst after a fresh collect
  setInterval(pollUStatus, 120000);                                     // then minutes-fresh
}
</script>
</body>
</html>
"""
