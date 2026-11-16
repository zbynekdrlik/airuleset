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

Round 2 (owner rejected the look, 16.11.2026): the capture keeps its SGR escapes
(`-e`) and `phoneAnsiLines`/`phoneRender` draw them as spans in the xterm palette;
transcript messages use the same font with `●`, a small markdown subset and the
`›` prompt bar (`phoneMsg`); the mode is remembered per device, default terminal.

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
  color: #CCCCCC; background: #0C0C0C; white-space: pre-wrap; overflow-wrap: anywhere;
  -webkit-overflow-scrolling: touch; user-select: text; -webkit-user-select: text; }
/* Round 2: history in the screen's own font, size and palette, as one terminal.
   `●` before Claude's text, the `›` prompt bar for the owner's (colours as Claude
   Code draws them: white bullet, 256-colour 153 code, 246 dim, 237 bar). */
#phonelog .m { position: relative; margin: 0 0 1.42em; padding-left: 2ch; }
#phonelog .m .mk { position: absolute; left: 0; top: 0; }
#phonelog .m.a .mk { color: #FFFFFF; }
#phonelog .m.u { background: #3A3A3A; color: #F2F2F2; }
#phonelog .mc { color: #AFD7FF; }
#phonelog .mcode { color: #949494; }
#phonescreen { margin: 0; padding: 0; border: 0; font: inherit;
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
const PHONE_MODE_KEY = 'wt-phone-mode';            // per device: 'text' | 'terminal'
// ---- A: `capture-pane -e` -> DOM. Escape-only: the text never becomes markup,
// colours come from the palette or from clamped numbers, never from the pane.
// A line is a list of runs {t, s}: s = null (plain) or a copy of the SGR state.
// tmux carries the state across lines, so the whole capture is one stream.
const PHONE_ANSI_KEYS = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
  'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta',
  'brightCyan', 'brightWhite'];
const PHONE_PLAIN = { b: false, d: false, i: false, u: false, v: false, fg: null, bg: null };
// CSI (SGR when it ends in m), OSC (to BEL / ST / the end), any other ESC sequence
// (intermediates + one final byte, e.g. `ESC ( B`). An unfinished one at the end is dropped.
const PHONE_ESC_RE = /\x1b\[([0-?]*)([ -\/]*)([@-~]|$)|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b[ -\/]*[0-~]?/g;
const PHONE_C0_RE = /[\x00-\x08\x0b-\x1f\x7f]/g;
function phoneRgb(r, g, b) { return 'rgb(' + r + ',' + g + ',' + b + ')'; }
function phoneByte(x) { const n = /^\d{1,3}$/.test(x || '') ? +x : -1; return n <= 255 ? n : -1; }
function phoneColor256(n) {
  if (!Number.isInteger(n) || n < 0 || n > 255) return null;
  if (n < 16) return CAMPBELL_THEME[PHONE_ANSI_KEYS[n]];
  if (n < 232) {
    const v = [0, 95, 135, 175, 215, 255], k = n - 16;
    return phoneRgb(v[Math.floor(k / 36)], v[Math.floor(k / 6) % 6], v[k % 6]);
  }
  const g = 8 + (n - 232) * 10;
  return phoneRgb(g, g, g);
}
function phoneTrue(c) { return c.some((x) => x < 0) ? null : phoneRgb(c[0], c[1], c[2]); }
// 38/48 at p[i]: [colour or null, params used]. `38;5;n`, `38;2;r;g;b`, colon forms.
function phoneExtColor(p, i) {
  const sub = p[i].split(':');
  if (sub.length > 1) {
    if (sub[1] === '5') return [phoneColor256(phoneByte(sub[2])), 1];
    if (sub[1] === '2') return [phoneTrue((sub.length >= 6 ? sub.slice(3, 6) : sub.slice(2, 5)).map(phoneByte)), 1];
    return [null, 1];
  }
  if (p[i + 1] === '5') return [phoneColor256(phoneByte(p[i + 2])), 3];
  if (p[i + 1] === '2') return [phoneTrue([p[i + 2], p[i + 3], p[i + 4]].map(phoneByte)), 5];
  return [null, p.length - i];
}
function phoneSgr(st, params) {
  const p = params.split(';');
  for (let i = 0; i < p.length;) {
    const head = p[i].split(':')[0], c = head === '' ? 0 : +head;
    let used = 1;
    if (c === 0) Object.assign(st, PHONE_PLAIN);
    else if (c === 1) st.b = true;
    else if (c === 2) st.d = true;
    else if (c === 3) st.i = true;
    else if (c === 4) st.u = p[i] !== '4:0';
    else if (c === 7) st.v = true;
    else if (c === 22) { st.b = false; st.d = false; }
    else if (c === 23) st.i = false;
    else if (c === 24) st.u = false;
    else if (c === 27) st.v = false;
    else if (c >= 30 && c <= 37) st.fg = phoneColor256(c - 30);
    else if (c >= 90 && c <= 97) st.fg = phoneColor256(c - 82);
    else if (c >= 40 && c <= 47) st.bg = phoneColor256(c - 40);
    else if (c >= 100 && c <= 107) st.bg = phoneColor256(c - 92);
    else if (c === 39) st.fg = null;
    else if (c === 49) st.bg = null;
    else if (c === 38 || c === 48) {
      const r = phoneExtColor(p, i);
      used = r[1];
      if (c === 38) st.fg = r[0]; else st.bg = r[0];
    }
    i += used;
  }
}
function phoneAnsiLines(raw) {
  const st = Object.assign({}, PHONE_PLAIN), lines = [[]];
  const plain = () => !(st.b || st.d || st.i || st.u || st.v || st.fg || st.bg);
  const text = (t) => {
    t.replace(PHONE_C0_RE, '').split('\n').forEach((part, k) => {
      if (k) lines.push([]);
      if (part) lines[lines.length - 1].push({ t: part, s: plain() ? null : Object.assign({}, st) });
    });
  };
  let at = 0, m;
  PHONE_ESC_RE.lastIndex = 0;
  while ((m = PHONE_ESC_RE.exec(raw)) !== null) {
    text(raw.slice(at, m.index));
    if (m[3] === 'm' && !m[2] && /^[\d;:]*$/.test(m[1])) phoneSgr(st, m[1]);
    at = PHONE_ESC_RE.lastIndex;
    if (m[0] === '') PHONE_ESC_RE.lastIndex++;
  }
  text(raw.slice(at));
  return lines;
}
function phoneLineText(l) { return l.map((r) => r.t).join(''); }
function phoneTrimEnd(l) {
  while (l.length) {
    const r = l[l.length - 1], t = r.t.replace(/\s+$/, '');
    if (t) { r.t = t; return; }
    l.pop();
  }
}
function phoneTrimStart(l) {
  while (l.length) {
    const t = l[0].t.replace(/^\s+/, '');
    if (t) { l[0].t = t; return; }
    l.shift();
  }
}
// The pane is drawn for 176 columns. Rejoin what Claude broke at that width, and
// cut full-width rules, which would wrap into several rows. Works on the runs'
// visible text, so an escape is never split or counted.
function phoneTidy(lines) {
  const out = [];
  let open = false;
  lines.forEach((line) => {
    phoneTrimEnd(line);
    const vis = phoneLineText(line);
    const rule = /^[\s─━═╌-]{24,}$/.test(vis);
    let cur = line;
    if (rule) {
      const first = line.find((r) => r.t.trim());
      cur = [{ t: vis.trim().slice(0, 24), s: first ? first.s : null }];
    }
    if (open && !rule && vis.trim()) {
      phoneTrimStart(cur);
      out[out.length - 1].push({ t: ' ', s: null }, ...cur);
    } else out.push(cur);
    open = phoneLineText(cur).length >= PHONE_GRID_NEAR_FULL;
  });
  while (out.length && !phoneLineText(out[out.length - 1]).trim()) out.pop();
  return out;
}
function phoneStyled(s, t) {
  const sp = document.createElement('span');
  sp.textContent = t;
  let fg = s.fg, bg = s.bg;
  if (s.v) { const f = fg || CAMPBELL_THEME.foreground; fg = bg || CAMPBELL_THEME.background; bg = f; }
  if (fg) sp.style.color = fg;
  if (bg) sp.style.backgroundColor = bg;
  if (s.b) sp.style.fontWeight = 'bold';
  if (s.d) sp.style.opacity = '0.6';
  if (s.i) sp.style.fontStyle = 'italic';
  if (s.u) sp.style.textDecoration = 'underline';
  return sp;
}
function phoneRender(lines, parent) {
  const frag = document.createDocumentFragment();
  lines.forEach((l, k) => {
    if (k) frag.appendChild(document.createTextNode('\n'));
    l.forEach((r) => frag.appendChild(r.s ? phoneStyled(r.s, r.t) : document.createTextNode(r.t)));
  });
  parent.textContent = '';
  parent.appendChild(frag);
}
// ---- B: a transcript message drawn like Claude's terminal does it ----
function phoneEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function phoneInline(text, parent) {     // **bold** and `code`; odd parts are the matches
  text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/).forEach((part, k) => {
    if (!part) return;
    if (k % 2 === 0) parent.appendChild(document.createTextNode(part));
    else if (part[0] === '*') parent.appendChild(phoneEl('b', '', part.slice(2, -2)));
    else parent.appendChild(phoneEl('span', 'mc', part.slice(1, -1)));
  });
}
function phoneMd(text, parent) {
  let code = null;
  text.split('\n').forEach((line) => {
    if (/^\s*```/.test(line)) {
      if (code) code = null;
      else { code = phoneEl('div', 'mcode', ''); parent.appendChild(code); }
      return;
    }
    if (code) { code.textContent += (code.textContent ? '\n' : '') + line; return; }
    const row = phoneEl('div', 'ml');
    const head = /^#{1,6}\s+(.*)$/.exec(line), item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (head) { const b = phoneEl('b'); phoneInline(head[1], b); row.appendChild(b); }
    else if (item) {
      const indent = item[1].length + item[2].length + 1;
      row.style.paddingLeft = indent + 'ch';
      row.style.textIndent = '-' + (item[2].length + 1) + 'ch';
      row.appendChild(document.createTextNode(item[2] + ' '));
      phoneInline(item[3], row);
    } else phoneInline(line || ' ', row);
    parent.appendChild(row);
  });
}
function phoneMsg(m) {
  const user = m.r === 'u', div = phoneEl('div', 'm ' + (user ? 'u' : 'a'));
  div.appendChild(phoneEl('span', 'mk', user ? '› ' : '● '));
  if (user) div.appendChild(document.createTextNode(m.t));
  else phoneMd(m.t, div);
  return div;
}
// ---- C: the owner's choice, per device; storage may throw (private mode) ----
function phoneModeLoad() {
  try { return window.localStorage.getItem(PHONE_MODE_KEY) === 'text'; } catch (e) { return false; }
}
function phoneModeSave(on) {
  try { window.localStorage.setItem(PHONE_MODE_KEY, on ? 'text' : 'terminal'); } catch (e) { /* not kept */ }
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
    d.msgs.forEach((m) => hist.appendChild(phoneMsg(m)));
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
    if (d.text === shownText) return;
    phoneRender(phoneTidy(phoneAnsiLines(d.text)), screen);
    shownText = d.text;
    if (following) phoneToEnd(log);
    down.classList.toggle('on', !phoneAtEnd(log));
  }
  function setOn(on, atLoad) {
    window.wtPhoneOn = on;
    el.classList.toggle('on', on);
    toggle.textContent = on ? 'Terminál' : 'Text';
    if (on) { shownIdx = -1; following = true; refresh(); } else if (!atLoad) focusTerminal(made[current], current);
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
  toggle.addEventListener('click', () => { setOn(!window.wtPhoneOn); phoneModeSave(window.wtPhoneOn); });
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
  setOn(phoneModeLoad(), true);         // the terminal unless this device chose Text
})();
</script>"""
