"""airuleset webterm — the touch-only on-screen key bar (#1159).

A phone keyboard has no Ctrl chord, no arrows and no Esc, so on a phone the
owner could not send `Ctrl+B w/s` (tmux window/session switch) or `Up` (prompt
history). This bar supplies those keys. It is shown ONLY on a coarse pointer
with no hover anywhere (phones, tablets; #1164: `KEYBAR_MEDIA`); on a desktop
or a touchscreen laptop it stays `display: none`, inert.

Each button feeds its exact byte sequence into the ACTIVE tab's xterm through
the same-origin `window.term` bridge (#613/#643/#671), exactly as if typed:
`term.input(data, true)` where the bundled xterm has it (ttyd 1.7.7), else the
core `triggerDataEvent(data, true)` (ttyd 1.7.4's xterm has no public
`input`). Never `term.paste()`: bracketed paste would wrap the bytes and tmux
would never see its prefix key. Arrows follow xterm's application-cursor mode
(`ESC O A` vs `ESC [ A`). After each press focus returns to the terminal via
the dashboard's own `focusTerminal`, so the phone keyboard stays up; while it
is up, `keybarFitViewport` sizes the page to the visual viewport so the
keyboard never covers the bar (the viewport meta stays untouched: iOS ignores
`interactive-widget` and WebKit logs it as a console error).

PURE CONSTANT LEAF (same contract as `cli_webterm_dash_template`, locked by
`tests/test_webterm_keybar_1159.py`): zero imports, zero defs. The dashboard
template carries two sentinels for it — `@@KEYBAR_CSS@@` inside its head
`<style>` and `@@KEYBAR_HTML@@` after its main `<script>` (the bar's script
reads the main script's `made` / `current` / `focusTerminal`) — substituted in
the same single pass by `cli_webterm.render_dashboard_html`.

#1287 round 3: the same script carries `keybarAttachSwipe`, which the main
script's `applyFixedGrid` attaches to every terminal (a guarded forward
reference): on a touch-only device a vertical swipe becomes mouse-wheel events,
so Claude Code's fullscreen view scrolls as under the PC wheel. The tmux
copy-mode `scroll` key was removed: a fullscreen Claude pane has no tmux history.
"""

# #1164: the ONE touch-only gate (CSS + keybarFitViewport). A coarse primary
# pointer alone is not enough: the owner's desktop Chrome (a touchscreen laptop)
# matched the old gate, i.e. it reported a coarse, non-hovering primary pointer,
# so no available pointer may hover either. NOT `(any-hover: none)`: Chromium matches
# that as soon as ANY pointer (the touchscreen) cannot hover (measured). Level-4
# `not` (Chrome 104+, Safari 16.4+); an older browser just never shows the bar.
KEYBAR_MEDIA = "(pointer: coarse) and (hover: none) and (not (any-hover: hover))"

KEYBAR_CSS = """/* #1159: the touch-only key bar. Hidden (inert) unless NO pointer can
   hover and the primary one is coarse, i.e. a phone or tablet (#1164: never a
   touchscreen laptop with a mouse); the desktop layout is unchanged. One
   bottom row, horizontally scrollable, 44px touch targets, no hint text (the
   #671 ruling). */
#keybar { display: none; }
@media """ + KEYBAR_MEDIA + """ {
  #keybar { display: flex; flex: 0 0 auto; gap: 4px; padding: 4px 6px;
    overflow-x: auto; overflow-y: hidden; white-space: nowrap;
    background: #0C0C0C; border-top: 1px solid #2b2b2b; }
  #keybar button { flex: 0 0 auto; min-width: 44px; height: 44px; padding: 0 10px;
    border: 1px solid #3f3f3f; border-radius: 6px; background: #1b1b1b;
    color: #F2F2F2; font: inherit; font-size: 15px;
    touch-action: manipulation; user-select: none; -webkit-user-select: none; }
  #keybar button:active { background: #333333; }
  #keybar .kb-gap { flex: 0 0 8px; }
}
"""

# Raw string: the `\xNN` escapes below must reach the browser as JS escapes.
KEYBAR_HTML = r"""<div id="keybar">
<button type="button" data-k="esc">Esc</button>
<button type="button" data-k="tab">Tab</button>
<button type="button" data-k="up" aria-label="up">&#8593;</button>
<button type="button" data-k="down" aria-label="down">&#8595;</button>
<button type="button" data-k="left" aria-label="left">&#8592;</button>
<button type="button" data-k="right" aria-label="right">&#8594;</button>
<button type="button" data-k="ctrlc">^C</button>
<span class="kb-gap"></span>
<button type="button" data-k="sessions">sessions</button>
<button type="button" data-k="windows">windows</button>
<button type="button" data-k="prevwin" aria-label="previous window">&#9664; win</button>
<button type="button" data-k="nextwin" aria-label="next window">win &#9654;</button>
</div>
<script>
const KEYBAR_MEDIA = '""" + KEYBAR_MEDIA + r"""';
// #1159: the touch key bar (see cli_webterm_keybar.py). Fixed byte sequences;
// the tmux group is the prefix Ctrl+B (0x02) + the command key.
const KEYBAR_KEYS = {
  esc: '\x1b', tab: '\x09', ctrlc: '\x03',
  sessions: '\x02s', windows: '\x02w', prevwin: '\x02p', nextwin: '\x02n',
};
// Arrows: the final byte only; the introducer depends on the cursor mode.
const KEYBAR_ARROWS = { up: 'A', down: 'B', right: 'C', left: 'D' };
function keybarAppCursor(term) {      // DECCKM: application cursor keys on?
  try {
    if (term.modes) return !!term.modes.applicationCursorKeysMode;
    return !!term._core.coreService.decPrivateModes.applicationCursorKeys;
  } catch (e) { return false; }
}
function keybarBytes(k, appCursor) {
  if (Object.prototype.hasOwnProperty.call(KEYBAR_ARROWS, k)) {
    return '\x1b' + (appCursor ? 'O' : '[') + KEYBAR_ARROWS[k];
  }
  return Object.prototype.hasOwnProperty.call(KEYBAR_KEYS, k) ? KEYBAR_KEYS[k] : '';
}
function keybarSend(term, data) {     // as TYPED input (onData -> ttyd), never paste
  if (typeof term.input === 'function') { term.input(data, true); return; }
  const cs = term._core && term._core.coreService;
  if (cs && typeof cs.triggerDataEvent === 'function') cs.triggerDataEvent(data, true);
}
function keybarPress(k) {            // the ACTIVE tab only; hidden tabs never receive
  const f = made[current];
  try {
    const term = f && f.contentWindow && f.contentWindow.term;
    const data = term && keybarBytes(k, keybarAppCursor(term));
    if (data) keybarSend(term, data);
  } catch (e) { /* iframe realm gone / term not ready: drop the key */ }
  focusTerminal(f, current);          // keep the terminal (and phone keyboard) focused
}
// A phone keyboard OVERLAYS the page (Android Chrome's default, iOS Safari), so
// it would cover a bottom bar. While the visual viewport is shorter than the
// layout viewport (keyboard up), size the page to the visual viewport so the
// bar sits just above the keyboard. Touch-only, and never under a pinch-zoom
// (scale != 1 shrinks the visual viewport too, but that is not a keyboard).
function keybarFitViewport() {
  const vv = window.visualViewport;
  if (!vv) return;
  const touch = !!(window.matchMedia && window.matchMedia(KEYBAR_MEDIA).matches);
  const h = Math.round(vv.height);
  const keyboard = touch && Math.abs(vv.scale - 1) < 0.01 && h < window.innerHeight - 1;
  document.body.style.height = keyboard ? h + 'px' : '';
}
// #1287 round 3: a one-finger vertical swipe over a terminal scrolls like the PC
// mouse wheel. Claude runs fullscreen (alternate screen, no tmux history) and
// turns on SGR mouse tracking; a wheel over the xterm becomes `ESC[<64/65;x;yM`,
// tmux `mouse on` hands it to Claude and Claude scrolls its own view (measured).
// So each row of finger travel dispatches one WheelEvent at the finger's point
// and xterm encodes it exactly as for a real wheel (its own arrow-key fallback
// too, for an app without mouse mode). Finger down = older content = wheel up.
// A tap, a long press (selection), a sideways move and two fingers are left alone.
const KEYBAR_SWIPE_SLOP = 10;        // px of vertical travel before it is a swipe
const KEYBAR_SWIPE_HOLD_MS = 400;    // a longer hold before moving is a long press
const KEYBAR_SWIPE_MAX = 30;         // wheel ticks per move event at most
function keybarRowHeight(win) {      // child-document px; the parent transform never enters
  const sc = win.document.querySelector('.xterm-screen');
  const h = sc && win.term.rows ? sc.getBoundingClientRect().height / win.term.rows : 0;
  return h > 0 ? h : 16;
}
function keybarWheel(win, x, y, up) {
  const WE = win.WheelEvent || WheelEvent;    // the iframe's own realm
  const t = win.document.elementFromPoint(x, y) || win.term.element;
  t.dispatchEvent(new WE('wheel', { bubbles: true, cancelable: true, view: win,
    clientX: x, clientY: y, deltaMode: 1, deltaY: up ? -1 : 1 }));
}
function keybarAttachSwipe(win) {    // idempotent: once per terminal, touch-only
  const term = win && win.term, el = term && term.element;
  if (!el || !el.addEventListener || term.__wtSwipe) return;
  if (!(window.matchMedia && window.matchMedia(KEYBAR_MEDIA).matches)) return;
  term.__wtSwipe = true;
  let s = null;
  el.addEventListener('touchstart', (e) => {
    const p = e.touches.length === 1 ? e.touches[0] : null;
    s = p ? { x: p.clientX, y: p.clientY, at: Date.now(), on: false } : null;
  }, { capture: true, passive: true });
  el.addEventListener('touchmove', (e) => {
    if (!s || e.touches.length !== 1) { s = null; return; }
    const p = e.touches[0];
    if (!s.on) {
      const dx = Math.abs(p.clientX - s.x), dy = Math.abs(p.clientY - s.y);
      if (dx >= KEYBAR_SWIPE_SLOP && dx >= dy) { s = null; return; }   // sideways: not ours
      if (dy < KEYBAR_SWIPE_SLOP) return;
      if (Date.now() - s.at > KEYBAR_SWIPE_HOLD_MS) { s = null; return; }
      s.on = true;
    }
    e.preventDefault();               // no page pan, no xterm touch scroll
    e.stopPropagation();
    try {
      const h = keybarRowHeight(win), n = Math.trunc((p.clientY - s.y) / h);
      if (!n) return;
      s.y += n * h;
      for (let i = 0; i < Math.min(Math.abs(n), KEYBAR_SWIPE_MAX); i++) keybarWheel(win, p.clientX, p.clientY, n > 0);
    } catch (err) { /* iframe realm gone: drop the swipe */ }
  }, { capture: true, passive: false });
  const end = () => { s = null; };
  el.addEventListener('touchend', end, { capture: true, passive: true });
  el.addEventListener('touchcancel', end, { capture: true, passive: true });
}
(function () {
  const bar = document.getElementById('keybar');
  if (!bar) return;
  // A tap must not move focus onto the button: that would blur the terminal
  // and drop the phone keyboard for a moment.
  bar.addEventListener('mousedown', (e) => e.preventDefault());
  bar.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('button[data-k]') : null;
    if (b) keybarPress(b.dataset.k);
  });
  if (window.visualViewport) window.visualViewport.addEventListener('resize', keybarFitViewport);
})();
</script>"""
