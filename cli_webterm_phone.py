"""airuleset webterm: the phone text view (#1287, approved mockup v1).

Every tab draws the fixed 176x51 grid, about 2 CSS px per column on a phone, so
the owner had to pinch-zoom, and the zoom pushed the tabs and the key bar off
the screen. History lives in tmux, out of reach of a finger or a selection.

On a touch-only device this view covers the terminal area with the pane as plain
wrapped text: `/phone-read` returns `capture-pane -J` with history (read only,
tmux size untouched, the PC view unchanged). Scroll, selection and copy are the
browser's. The box below sends through the tab's own xterm, so no write path is
added: `term.paste` for the text, the typed path of the key bar for Enter. The
`Terminál` button in the key bar shows the real grid for menus.

Text is set through `textContent`, never markup: the pane is untrusted output.

PURE CONSTANT LEAF, the `cli_webterm_keybar` contract: no imports, no defs. The
template carries `@@PHONE_CSS@@` and `@@PHONE_HTML@@` (after the key bar script,
whose `KEYBAR_MEDIA` and `keybarSend` it reads). Live only with `CFG.phone_read`.
"""

PHONE_CSS = """/* #1287: the phone text view, over the terminal area. Off until the script
   adds .on (touch-only device on a gateway with /phone-read). */
#phone { display: none; }
#phone.on { display: flex; flex-direction: column; position: absolute; inset: 0;
  z-index: 1; background: #0C0C0C; }
#phonelog { flex: 1 1 auto; min-height: 0; margin: 0; padding: 8px 10px; overflow-y: auto;
  font: 13.5px/1.42 "Cascadia Mono", Consolas, "DejaVu Sans Mono", Menlo, monospace;
  color: #CCCCCC; white-space: pre-wrap; overflow-wrap: anywhere;
  -webkit-overflow-scrolling: touch; user-select: text; -webkit-user-select: text; }
#phonelog .m { margin: 0 0 10px; font-family: system-ui, sans-serif; font-size: 15px; line-height: 1.4; }
#phonelog .m.u { padding: 6px 8px; border-radius: 6px; background: #262626; color: #F2F2F2; }
#phonescreen { margin: 0; padding-top: 8px; border-top: 1px solid #3f3f3f; font: inherit;
  white-space: pre-wrap; overflow-wrap: anywhere; }
#phonestate { display: none; flex: 0 0 auto; padding: 4px 10px; color: #E74856; font-size: 13px; }
#phonestate.on { display: block; }
#phonedown { display: none; position: absolute; right: 12px; bottom: 64px; height: 40px;
  padding: 0 14px; border: 1px solid #3f3f3f; border-radius: 20px; background: #333333;
  color: #F2F2F2; font-size: 14px; }
#phonedown.on { display: block; }
#phonebar { display: flex; flex: 0 0 auto; gap: 6px; padding: 6px; border-top: 1px solid #2b2b2b; }
#phonebox { flex: 1 1 auto; min-width: 0; height: 44px; padding: 10px; resize: none;
  border: 1px solid #3f3f3f; border-radius: 8px; background: #1b1b1b; color: #F2F2F2;
  font: 16px/1.3 system-ui, sans-serif; }
#phonebar button { flex: 0 0 auto; height: 44px; padding: 0 10px; border: 1px solid #3f3f3f;
  border-radius: 8px; background: #1b1b1b; color: #F2F2F2; font-size: 15px; }
#phonebar #phonesend { border-color: #3B78FF; background: #3B78FF; color: #fff; }
body.wt-phone { touch-action: pan-x pan-y; }
"""

# Raw string: the `\s`/`\n` escapes below must reach the browser as JS escapes.
PHONE_HTML = r"""<div id="phone">
<div id="phonelog" tabindex="0"><div id="phonehist"></div><pre id="phonescreen"></pre></div>
<div id="phonestate">Text tohto tabu sa nedá načítať. Tlačidlo Terminál otvorí plný terminál.</div>
<button id="phonedown" type="button">&#8595; nadol</button>
<div id="phonebar">
<textarea id="phonebox" rows="1" placeholder="Napíš alebo vlož text…" aria-label="Text pre terminál"></textarea>
<button id="phonepaste" type="button">Vložiť</button>
<button id="phonesend" type="button">Poslať</button>
</div>
</div>
<script>
// #1287: see cli_webterm_phone.py.
const PHONE_POLL_MS = 1500;
const PHONE_HISTORY_MS = 5000;      // the gateway reads a transcript at most this often
const PHONE_GRID_NEAR_FULL = CFG.term_cols - 6;    // a line this long continues below
// The pane is drawn for 176 columns. Rejoin what Claude broke at that width, and
// cut full-width rules, which would wrap into several rows.
function phoneTidy(raw) {
  const out = [];
  let open = false;
  raw.split('\n').forEach((line0) => {
    let line = line0.replace(/\s+$/, '');
    const rule = /^[\s─━═╌-]{24,}$/.test(line);
    if (rule) line = line.trim().slice(0, 24);
    if (open && !rule && line.trim()) out[out.length - 1] += ' ' + line.trim();
    else out.push(line);
    open = line.length >= PHONE_GRID_NEAR_FULL;
  });
  while (out.length && !out[out.length - 1].trim()) out.pop();
  return out.join('\n');
}
function phoneAtEnd(log) { return log.scrollHeight - log.scrollTop - log.clientHeight < 40; }
function phoneToEnd(log) { log.scrollTop = log.scrollHeight; }
function phoneSelecting() {           // a redraw would drop the owner's selection
  const s = window.getSelection && window.getSelection();
  return !!(s && !s.isCollapsed);
}
(function () {
  const el = document.getElementById('phone');
  if (!CFG.phone_read || !el) return;
  if (!(window.matchMedia && window.matchMedia(KEYBAR_MEDIA).matches)) return;
  const log = document.getElementById('phonelog');
  const state = document.getElementById('phonestate');
  const down = document.getElementById('phonedown');
  const box = document.getElementById('phonebox');
  const hist = document.getElementById('phonehist');
  const screen = document.getElementById('phonescreen');
  let shownIdx = -1, shownText = null, seq = 0, following = true, histNext = 0, histAt = 0, gen = 0;

  function newTab(idx) {                       // never another tab's text under this box
    seq++;
    shownIdx = idx; shownText = null; following = true;
    gen++; hist.textContent = ''; screen.textContent = ''; histNext = 0; histAt = 0;
    state.classList.remove('on');
    if (window.getSelection) window.getSelection().removeAllRanges();
  }
  // Claude runs fullscreen, so its pane holds one screen: older messages come
  // from the session transcript, appended in order, never re-rendered.
  async function refreshHistory(s) {
    const mine = gen;
    if (Date.now() - histAt < PHONE_HISTORY_MS) return;
    histAt = Date.now();
    let d = { ok: false };
    try {
      const r = await fetch('/phone-history?id=' + encodeURIComponent(s.id) + '&since=' + histNext,
                            { cache: 'no-store', signal: AbortSignal.timeout(20000) });
      if (r.ok) d = await r.json();
    } catch (e) { /* the next history tick retries */ }
    if (mine !== gen || !d.ok) return;
    if (d.first > histNext) { hist.textContent = ''; histNext = d.first; }
    d.msgs.forEach((m) => {
      const div = document.createElement('div');
      div.className = 'm ' + (m.r === 'u' ? 'u' : 'a');
      div.textContent = m.t;
      hist.appendChild(div);
    });
    histNext += d.msgs.length;
    if (d.msgs.length && following) phoneToEnd(log);
  }
  async function refresh() {
    const idx = current, s = CFG.sessions[idx];
    if (!s || !window.wtPhoneOn || document.hidden) return;
    if (idx !== shownIdx) newTab(idx);
    const mine = ++seq;
    refreshHistory(s);
    let d = { ok: false };
    try {
      const r = await fetch('/phone-read?id=' + encodeURIComponent(s.id),
                            { cache: 'no-store', signal: AbortSignal.timeout(20000) });
      if (r.ok) d = await r.json();
    } catch (e) { /* offline or timed out: shown below, the next tick retries */ }
    if (mine !== seq) return;                  // a newer read or a tab change won
    state.classList.toggle('on', !d.ok);
    if (!d.ok || phoneSelecting()) return;
    const text = phoneTidy(d.text);
    if (text === shownText) return;
    screen.textContent = text;
    shownText = text;
    if (following) phoneToEnd(log);
    down.classList.toggle('on', !phoneAtEnd(log));
  }
  function setOn(on) {
    window.wtPhoneOn = on;
    el.classList.toggle('on', on);
    toggle.textContent = on ? 'Terminál' : 'Text';
    if (on) { shownIdx = -1; following = true; refresh(); } else focusTerminal(made[current], current);
  }
  function send() {
    const f = made[current];
    const term = f && f.contentWindow && f.contentWindow.term;
    if (!box.value.trim()) return;
    // ttyd drops input on a closed socket, so the text stays until the tab is live.
    const tab = document.querySelector('.tab[data-idx="' + current + '"]');
    if (!term || (tab && tab.classList.contains('wt-offline'))) {
      state.classList.add('on');
      reviveTerminal(f, current);
      return;
    }
    term.paste(box.value);
    keybarSend(term, '\r');
    box.value = '';
    following = true;
    histAt = 0;
    setTimeout(refresh, 300);
  }
  // Terminal text is not page text: a pinch would only bring the old problem back.
  const meta = document.querySelector('meta[name="viewport"]');
  if (meta) meta.content += ', maximum-scale=1, user-scalable=no';
  document.body.classList.add('wt-phone');
  document.getElementById('frames').appendChild(el);
  const bar = document.getElementById('keybar');
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'phonetoggle';
  bar.insertBefore(toggle, bar.firstChild);
  toggle.addEventListener('click', () => setOn(!window.wtPhoneOn));
  // Only a scroll moves `following`: the keyboard shrinks the log without one.
  log.addEventListener('scroll', () => {
    following = phoneAtEnd(log);
    down.classList.toggle('on', !following);
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => { if (following) phoneToEnd(log); });
  }
  down.addEventListener('click', () => { following = true; phoneToEnd(log); });
  document.getElementById('phonesend').addEventListener('click', send);
  document.getElementById('phonepaste').addEventListener('click', async () => {
    try { box.value += await navigator.clipboard.readText(); } catch (e) { box.focus(); }
  });
  document.getElementById('tabbar').addEventListener('click', () => setTimeout(refresh, 0));
  document.addEventListener('visibilitychange', refresh);
  setInterval(refresh, PHONE_POLL_MS);
  setOn(true);
})();
</script>"""
