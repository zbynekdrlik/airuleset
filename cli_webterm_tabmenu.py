"""airuleset webterm — reorder the tabs and hide rarely used ones in a menu (#1269).

Owner (30.10.2026): "aby sa dali taby hore preusporadavat a hidnut do nejakeho
menu napravo ako posledny tab ktory sa vyroluje ako menu". The bar is a
scrolling strip of tabs (`#tabs`) plus a last item (`#tabmore`) whose drop-down
(`#tabmenu`) lists the hidden tabs:

* drag a tab (mouse at once; touch after a short hold, a plain swipe still
  scrolls the strip) to reorder it; drop it on `#tabmore`, or right-click it,
  to hide it in place;
* a menu row opens its tab (`#tabmore` then shows its name as the active item);
  the row's arrow puts the tab back into the bar at its old spot;
* Ctrl+Alt+1..9 follow the visible bar; a hidden tab's U reddens `#tabmore`.

The layout (order + hidden, by session id) is stored by the gateway in the
dashboard's own dir (`POST /tab-layout`) and inlined into the page when the
gateway serves `/` (`WT_TAB_LAYOUT`), so it is the same on every device of that
dashboard and the bar is arranged before the first paint.

PURE CONSTANT LEAF (the `cli_webterm_dash_template` / `cli_webterm_keybar`
contract): zero imports, zero defs. The template carries `@@TABMENU_CSS@@` in
its head `<style>` and `@@TABMENU_JS@@` inside its main `<script>` (the code
reads `CFG` / `current` / `activate` and is called from `activate`, `onHotkey`
and the U poll, so it must share that script), substituted in the same single
pass by `cli_webterm.render_dashboard_html`.
"""

TABMENU_CSS = """.tab[hidden] { display: none; }
.tab.dragging { opacity: 0.55; }
#tabmore { display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 9px 12px; border: 1px solid transparent; border-bottom: none;
  border-radius: 7px 7px 0 0; background: #1b1b1b; color: #CCCCCC;
  font: inherit; line-height: 1; max-width: 170px; flex: 0 0 auto; }
#tabmore:hover, #tabmore.open { background: #262626; color: #F2F2F2; }
#tabmore.active { background: #333333; color: #F2F2F2; border-color: #3f3f3f;
  box-shadow: inset 0 -2px 0 0 #3B78FF; }
#tabmore.active .al { font-weight: 700; }
#tabmore .al { overflow: hidden; text-overflow: ellipsis; }
#tabmore .al:empty { display: none; }
#tabmore .mico, #tabmenu .ico { color: #13A10E; font-size: 11px; }
#tabmore.has-u .mico, #tabmenu .mrow.has-u .ico { color: #E74856; }
#tabmore.drop { background: #262626; box-shadow: inset 0 0 0 1px #3B78FF; }
#tabmenu { position: absolute; right: 6px; top: 100%; min-width: 170px;
  max-height: 70vh; overflow-y: auto; padding: 4px 0;
  background: #1b1b1b; border: 1px solid #3f3f3f; border-radius: 0 0 7px 7px; }
#tabmenu[hidden] { display: none; }
#tabmenu .mrow { display: flex; align-items: center; padding-left: 12px; }
#tabmenu .mrow:hover { background: #262626; }
#tabmenu button { background: none; border: 0; color: #CCCCCC; font: inherit;
  cursor: pointer; padding: 8px 10px; }
#tabmenu .mopen { flex: 1 1 auto; text-align: left; }
#tabmenu .mrow.active .mopen { color: #F2F2F2; font-weight: 700; }
#tabmenu .mpin:hover, #tabmenu .mopen:hover { color: #F2F2F2; }
"""

TABMENU_JS = """// #1269 (owner 30.10.2026): reorder the tabs by dragging, hide rarely used ones
// into the drop-down of the last bar item (#tabmore). Drop a tab on #tabmore or
// right-click it to hide; a menu row opens its tab, its arrow puts the tab back
// into the bar. The layout (order + hidden, by session id) is stored by the
// gateway at /tab-layout, so it is the same on every device of this dashboard.
const tabsEl = document.getElementById('tabs');
const moreEl = document.getElementById('tabmore');
const menuEl = document.getElementById('tabmenu');
let tabLayout = { order: CFG.sessions.map((s) => s.id), hidden: [] };
let tabDrag = null;
let lastPointerType = 'mouse';
let tabSaveChain = Promise.resolve();
let tabUnknown = { order: [], hidden: [] };   // stored ids of tabs not in this page: kept on save
function tabEls() { return [...tabsEl.querySelectorAll('.tab')]; }
function visibleTabIdx() { return tabEls().filter((t) => !t.hidden).map((t) => +t.dataset.idx); }
function applyTabLayout(l) {
  const ids = CFG.sessions.map((s) => s.id);
  const known = new Set(ids);
  tabUnknown = { order: (l.order || []).filter((id) => !known.has(id)),
                 hidden: (l.hidden || []).filter((id) => !known.has(id)) };
  const order = (l.order || []).filter((id) => known.has(id));
  ids.forEach((id) => { if (!order.includes(id)) order.push(id); });   // a new tab joins the bar
  tabLayout = { order: order, hidden: (l.hidden || []).filter((id) => known.has(id)) };
  const byId = {};
  tabEls().forEach((t) => { byId[CFG.sessions[+t.dataset.idx].id] = t; });
  order.forEach((id) => {
    const t = byId[id];
    tabsEl.appendChild(t);
    t.hidden = tabLayout.hidden.includes(id);
    t.classList.toggle('active', +t.dataset.idx === current && !t.hidden);
  });
  syncTabMenu();
}
function saveTabLayout() {
  tabLayout.order = tabEls().map((t) => CFG.sessions[+t.dataset.idx].id);
  const body = JSON.stringify({ order: tabLayout.order.concat(tabUnknown.order),
                                hidden: tabLayout.hidden.concat(tabUnknown.hidden) });
  const failed = (why) => console.warn('webterm: tab layout not saved (' + why + ')');
  // serialized: the last change always lands last
  tabSaveChain = tabSaveChain.then(() => fetch('/tab-layout', { method: 'POST',
    credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body }))
    .then((r) => { if (!r.ok) failed('HTTP ' + r.status); })
    .catch((err) => failed(err && err.message));
}
function setTabHidden(idx, hide) {
  const id = CFG.sessions[idx].id;
  tabLayout.hidden = tabLayout.hidden.filter((h) => h !== id);
  if (hide) tabLayout.hidden.push(id);
  tabLayout.order = tabEls().map((t) => CFG.sessions[+t.dataset.idx].id);
  applyTabLayout(tabLayout);
  saveTabLayout();
}
function syncTabMenu() {
  if (!moreEl) return;
  const cur = CFG.sessions[current];
  const curHidden = !!cur && tabLayout.hidden.includes(cur.id);
  moreEl.classList.toggle('active', curHidden);
  moreEl.querySelector('.al').textContent = curHidden ? cur.alias : '';
  moreEl.title = curHidden ? cur.title : '';
  moreEl.classList.toggle('has-u', !!tabsEl.querySelector('.tab[hidden].has-u'));
  const hiddenIds = tabLayout.order.filter((id) => tabLayout.hidden.includes(id));
  // rebuild the rows only when the hidden set changes, so a U poll never swaps
  // a row out from under a press (the click would land on #tabmenu)
  if (menuEl.dataset.key !== hiddenIds.join(' ')) buildTabMenu(hiddenIds);
  [...menuEl.children].forEach((row) => {
    const idx = +row.dataset.idx;
    const t = tabsEl.querySelector('.tab[data-idx="' + idx + '"]');
    row.classList.toggle('active', idx === current);
    row.classList.toggle('has-u', !!(t && t.classList.contains('has-u')));
  });
  if (!menuEl.children.length) closeTabMenu();
}
function buildTabMenu(hiddenIds) {
  menuEl.textContent = '';
  menuEl.dataset.key = hiddenIds.join(' ');
  hiddenIds.forEach((id) => {
    const idx = CFG.sessions.findIndex((s) => s.id === id);
    const s = CFG.sessions[idx];
    const row = document.createElement('div');
    row.className = 'mrow';
    row.dataset.idx = idx;
    const ico = document.createElement('span');
    ico.className = 'ico'; ico.textContent = '\u25B8';
    const open = document.createElement('button');
    open.type = 'button'; open.className = 'mopen'; open.textContent = s.alias; open.title = s.title;
    open.addEventListener('click', () => { closeTabMenu(); activate(idx); });
    const pin = document.createElement('button');
    pin.type = 'button'; pin.className = 'mpin'; pin.textContent = '\u2191';
    pin.addEventListener('click', () => {
      setTabHidden(idx, false);
      if (!tabLayout.hidden.length) closeTabMenu();
    });
    row.append(ico, open, pin);
    menuEl.appendChild(row);
  });
}
// #1269: the click that ends a drag must not switch tabs -- stopped in the
// capture phase, before the tab's own click listener.
tabsEl.addEventListener('click', (e) => {
  const t = e.target.closest('.tab');
  if (t && t.__wtDragged) e.stopImmediatePropagation();
}, true);
function closeTabMenu() { menuEl.hidden = true; moreEl.classList.remove('open'); }
moreEl.addEventListener('click', () => {
  if (!menuEl.hidden || !menuEl.children.length) { closeTabMenu(); return; }
  menuEl.hidden = false; moreEl.classList.add('open');
});
document.addEventListener('pointerdown', (e) => {
  if (!menuEl.hidden && !e.target.closest('#tabmenu, #tabmore')) closeTabMenu();
});
window.addEventListener('blur', closeTabMenu);          // focus went into a terminal
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTabMenu(); });
tabsEl.addEventListener('contextmenu', (e) => {
  const t = e.target.closest('.tab');
  if (!t || lastPointerType === 'touch') return;          // a touch long-press arms a drag
  e.preventDefault();
  setTabHidden(+t.dataset.idx, true);
});
tabsEl.addEventListener('pointerdown', (e) => {
  lastPointerType = e.pointerType;
  const t = e.target.closest('.tab');
  if (!t || (e.pointerType === 'mouse' && e.button !== 0)) return;
  tabDrag = { t: t, id: e.pointerId, x: e.clientX, y: e.clientY, home: t.nextSibling,
              armed: e.pointerType !== 'touch', on: false };
  if (!tabDrag.armed) {                     // touch: hold still, then drag (a swipe scrolls)
    const d = tabDrag;
    d.timer = setTimeout(() => { d.armed = true; d.t.classList.add('dragging'); }, 350);
  }
});
// a non-passive touchmove that blocks the bar's own scroll once a drag is armed
tabsEl.addEventListener('touchmove', (e) => { if (tabDrag && tabDrag.armed) e.preventDefault(); },
  { passive: false });
function tabDragOver(x, y) {
  const el = document.elementFromPoint(x, y);
  return { more: !!(el && el.closest('#tabmore')), tab: el && el.closest('#tabs .tab'), el: el };
}
window.addEventListener('pointermove', (e) => {
  const d = tabDrag;
  if (!d || e.pointerId !== d.id) return;
  // a release outside this document (over a terminal iframe) sends no pointerup
  if (e.pointerType === 'mouse' && !(e.buttons & 1)) { endTabDrag(e, false); return; }
  const far = Math.abs(e.clientX - d.x) > 6 || Math.abs(e.clientY - d.y) > 6;
  if (!d.on) {
    if (!far) return;
    if (!d.armed) { clearTimeout(d.timer); tabDrag = null; return; }   // a touch swipe
    d.on = true;
    d.t.classList.add('dragging');
    // capture on the strip, never on the tab: moving the tab in the DOM drops
    // a capture held by the tab itself, and then the release goes to the terminal
    try { tabsEl.setPointerCapture(e.pointerId); } catch (err) { /* already gone */ }
  }
  const o = tabDragOver(e.clientX, e.clientY);
  moreEl.classList.toggle('drop', o.more);
  if (o.more) return;
  if (o.tab && o.tab !== d.t) {
    const r = o.tab.getBoundingClientRect();
    tabsEl.insertBefore(d.t, e.clientX < r.left + r.width / 2 ? o.tab : o.tab.nextSibling);
  } else if (!o.tab && o.el === tabsEl) {
    const vis = tabEls().filter((t) => !t.hidden);
    const last = vis[vis.length - 1];
    if (last && last !== d.t && e.clientX > last.getBoundingClientRect().right) tabsEl.appendChild(d.t);
  }
});
function endTabDrag(e, drop) {
  const d = tabDrag;
  if (!d || e.pointerId !== d.id) return;
  tabDrag = null;
  clearTimeout(d.timer);
  moreEl.classList.remove('drop');
  d.t.classList.remove('dragging');
  if (!d.on) return;
  d.t.__wtDragged = true;                   // swallow the click this pointerup fires
  setTimeout(() => { d.t.__wtDragged = false; }, 0);
  if (drop && tabDragOver(e.clientX, e.clientY).more) {
    tabsEl.insertBefore(d.t, d.home);       // hidden in place: back at its spot when shown again
    setTabHidden(+d.t.dataset.idx, true);
  }
  else saveTabLayout();
}
window.addEventListener('pointerup', (e) => endTabDrag(e, true));
window.addEventListener('pointercancel', (e) => endTabDrag(e, false));
"""
