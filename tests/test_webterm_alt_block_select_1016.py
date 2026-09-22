"""#1016 -- Alt+drag in the webterm makes a Windows-Terminal-style BLOCK
(column) selection.

Owner report (13.9.2026): selecting an INDENTED block (a `/goal` line offset two
spaces) with Alt+drag -- the Windows Terminal habit -- does not work in the
webterm. Root cause (design comment, main): the fleet tmux runs `mouse on`
(#646), so xterm.js only FORCES its own selection under mouse-tracking when
`shiftKey` is set (`shouldForceSelection`) and only makes it a COLUMN block when
`altKey` is set (`_shouldColumnSelect`); so a plain Alt+drag goes to tmux (line
selection) and the real block gesture is Shift+Alt+drag (which on Windows is the
input-language toggle).

Fix (Approach 1): the dash template attaches ONE capture-phase `mousedown`
listener per same-origin xterm iframe (the #613/#886 `window.term` integration
point) that translates a plain Alt+left-mousedown into a Shift+Alt one -- xterm
then forces its own COLUMN selection -- and sets `altClickMovesCursor = false`.

Two tiers of coverage:

* STRUCTURAL locks (always run) -- the translator ships, is wired into the
  per-iframe poll beside attachClipboard/themeTerminal, runs in the CAPTURE
  phase, guards on `altKey && !shiftKey && button === 0`, re-dispatches with
  `shiftKey: true` + `altKey: true`, is idempotent, and disables
  altClickMovesCursor.
* A BEHAVIOURAL node harness (runs when `node` is present) that drives the REAL
  extracted `attachBlockSelect` against a stub `term.element` (an EventTarget)
  and a stub `win.MouseEvent`, recording what a stand-in xterm listener actually
  receives. It proves: an Alt+drag reaches xterm as a Shift+Alt (column) event
  with the original suppressed; a plain drag is untouched; an already-Shift+Alt
  drag is not double-processed; `altClickMovesCursor` is false after attach; the
  attach is idempotent; and NO console output is emitted (console stays clean).
  This ALWAYS-RUN tier uses the design's sanctioned stub-`term.element` harness
  (not a real xterm) for a concrete reason: no `playwright` DRIVER is installed on
  this box (python import fails; not in node_modules), so a real-browser pytest
  — like the sibling `test_webterm_short_viewport_798.py` real-browser tier —
  would only SKIP here, giving no regression net. The xterm.js JS SOURCE is also
  not a file on disk (it is compiled into the ttyd binary), so it cannot be
  `<script>`-loaded into a node harness either. A real xterm IS reachable at
  DEPLOY time via a loopback ttyd + browser (the #678/#700 recipe), and the
  end-to-end column-selection OUTCOME (a Shift+Alt drag under tmux `mouse on`
  yields a block with no leading indent) is the ticket's UNVERIFIED item, confirmed
  in the owner's browser after deploy per the #1015 live-verification rule — this
  stub tier proves the TRANSLATION (the JS reshapes the event correctly), which is
  the part that lives in this repo.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
from test_webterm import _extract_js_function  # noqa: E402


def _inv():
    return [{"id": "s1", "label": "dev1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


# ---------------------------------------------------------------------------
# Node harness: run the REAL extracted attachBlockSelect against a stub term
# whose `element` is a node EventTarget and whose `win.MouseEvent` is a shim.
# A stand-in xterm listener (registered AFTER attachBlockSelect) records exactly
# what xterm would receive: node's flat EventTarget fires listeners in
# REGISTRATION order and honours stopImmediatePropagation, so the translator
# (registered first) suppresses the original at the recorder (registered second)
# and only the re-dispatched clone reaches it. NB this models REGISTRATION-order
# suppression, NOT a real DOM tree's capture-before-descendant phase — the actual
# capture flag is a FLAT-model no-op here, so the `, true` capture requirement is
# locked STRUCTURALLY by test_capture_phase_listener, not behaviourally (a real
# tree-capture test needs a browser, which is the deploy-time #678/#700 recipe).
# ---------------------------------------------------------------------------
_BLOCK_HARNESS = r"""
%(attach)s
// --- stub realm ------------------------------------------------------------
const consoleHits = [];
for (const m of ['error', 'warn', 'log', 'info', 'debug']) {
  console[m] = (...a) => { consoleHits.push(m + ':' + a.join(' ')); };
}
class MouseEvt extends Event {
  constructor(type, init) {
    init = init || {};
    super(type, { bubbles: !!init.bubbles, cancelable: !!init.cancelable });
    const num = ['button', 'buttons', 'clientX', 'clientY', 'screenX', 'screenY'];
    const flag = ['altKey', 'shiftKey', 'ctrlKey', 'metaKey'];
    for (const k of num) this[k] = (k in init) ? init[k] : 0;
    for (const k of flag) this[k] = (k in init) ? init[k] : false;
    this.view = ('view' in init) ? init.view : null;
  }
}
const el = new EventTarget();
const term = { element: el, options: { altClickMovesCursor: true, macOptionClickForcesSelection: false } };
const win = { MouseEvent: MouseEvt, term: term };

let attachError = null;
try {
  attachBlockSelect(win);
  attachBlockSelect(win);   // idempotent: second call must be a no-op
} catch (e) { attachError = String(e && e.message || e); }

// stand-in for xterm's OWN selection listener, registered AFTER the translator
// (models capture-phase-before-xterm: the translator's stopImmediatePropagation
// then hides the original from this listener, leaving only the re-dispatch).
const recorded = [];
el.addEventListener('mousedown', (ev) => {
  recorded.push({ altKey: ev.altKey, shiftKey: ev.shiftKey, button: ev.button });
});

function fire(init) {
  recorded.length = 0;
  el.dispatchEvent(new MouseEvt('mousedown',
    Object.assign({ bubbles: true, cancelable: true, button: 0, buttons: 1 }, init)));
  return recorded.slice();
}

const out = {
  attachError: attachError,
  altClickMovesCursor: term.options.altClickMovesCursor,
  macOptionClickForcesSelection: term.options.macOptionClickForcesSelection,
  altDrag: fire({ altKey: true, shiftKey: false }),         // plain Alt+left
  plainDrag: fire({ altKey: false, shiftKey: false }),      // ordinary drag
  shiftAltDrag: fire({ altKey: true, shiftKey: true }),     // already the block gesture
  altRight: fire({ altKey: true, shiftKey: false, button: 2 }),  // Alt + right button
  consoleHits: consoleHits,
};
process.stdout.write(JSON.stringify(out));
"""


def _run_block_harness(html):
    attach = _extract_js_function(html, "attachBlockSelect")
    src = _BLOCK_HARNESS % {"attach": attach}
    d = tempfile.mkdtemp()
    hp = Path(d) / "blockharness.js"
    hp.write_text(src, encoding="utf-8")
    r = subprocess.run(["node", str(hp)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
    return json.loads(r.stdout.strip().splitlines()[-1])


class TestAltBlockSelectStructure1016(unittest.TestCase):
    """The translator ships, is wired into the per-iframe poll, and carries the
    exact gesture/guard/capture semantics the design specifies."""

    def setUp(self):
        self.html = w.render_dashboard_html(_inv(), ttyd_base="/t")

    def test_translator_function_ships(self):
        self.assertIn("function attachBlockSelect(", self.html)

    def test_wired_into_the_per_iframe_poll(self):
        # attached in applyFixedGrid's poll, beside attachClipboard -- the same
        # same-origin window.term hook #643/#671/#886 use.
        poll = _extract_js_function(self.html, "applyFixedGrid")
        self.assertIn("attachBlockSelect(win)", poll)
        self.assertIn("attachClipboard(win)", poll)

    def test_disables_alt_click_moves_cursor(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("altClickMovesCursor", fn)
        self.assertRegex(fn, r"altClickMovesCursor\s*=\s*false")

    def test_forces_selection_on_macos(self):
        # on macOS xterm's shouldForceSelection keys on altKey &&
        # macOptionClickForcesSelection, NOT shiftKey -- so this must be enabled or
        # the synthetic Shift+Alt forces nothing on a Mac (the template is shared
        # across every lane, not only the Windows owner).
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertRegex(fn, r"macOptionClickForcesSelection\s*=\s*true")

    def test_only_mousedown_is_hooked(self):
        # mousemove/mouseup must be left to xterm once the selection has started;
        # a lock so a future edit can't silently hook them.
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertNotIn("'mousemove'", fn)
        self.assertNotIn("'mouseup'", fn)

    def test_capture_phase_listener(self):
        # MUST be capture -- xterm's own selection listener lives on a descendant
        # and would consume the plain Alt+drag first in the bubble phase.
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("'mousedown'", fn)
        self.assertRegex(fn, r"addEventListener\(\s*'mousedown'[\s\S]*?,\s*true\s*\)")

    def test_gesture_guard_plain_alt_left_only(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("altKey", fn)
        self.assertIn("shiftKey", fn)
        self.assertIn("button", fn)

    def test_redispatch_forces_shift_and_alt(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertRegex(fn, r"shiftKey\s*:\s*true")
        self.assertRegex(fn, r"altKey\s*:\s*true")
        # the original is suppressed so xterm never sees the plain Alt+drag.
        self.assertIn("preventDefault", fn)
        self.assertRegex(fn, r"stop(Immediate)?Propagation")

    def test_idempotent_guard_flag(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("__wtBlockSel", fn)


class TestAltBlockSelectBehaviour1016(unittest.TestCase):
    """Drive the REAL extracted translator in node against a stub term.element."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node not available")
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        cls.out = _run_block_harness(html)

    def test_attach_never_raises(self):
        self.assertIsNone(self.out["attachError"])

    def test_alt_click_moves_cursor_false_after_attach(self):
        self.assertIs(self.out["altClickMovesCursor"], False)

    def test_mac_option_click_forces_selection_true_after_attach(self):
        self.assertIs(self.out["macOptionClickForcesSelection"], True)

    def test_alt_drag_becomes_shift_alt_block_original_suppressed(self):
        # xterm receives exactly ONE event: the translated Shift+Alt (column
        # block). The original plain Alt+drag is stopped, so it never reaches
        # xterm -> tmux (which would make an ordinary line selection).
        self.assertEqual(self.out["altDrag"],
                         [{"altKey": True, "shiftKey": True, "button": 0}])

    def test_plain_drag_untouched(self):
        # no altKey -> today's behaviour, exactly one unchanged event.
        self.assertEqual(self.out["plainDrag"],
                         [{"altKey": False, "shiftKey": False, "button": 0}])

    def test_shift_alt_drag_not_double_processed(self):
        # already the block gesture -> pass through once, never re-dispatched.
        self.assertEqual(self.out["shiftAltDrag"],
                         [{"altKey": True, "shiftKey": True, "button": 0}])

    def test_alt_right_button_untouched(self):
        # only the left button (0) is a selection drag; right-button Alt is not.
        self.assertEqual(self.out["altRight"],
                         [{"altKey": True, "shiftKey": False, "button": 2}])

    def test_console_stays_empty(self):
        # the page must stay console-clean (browser-console-zero-errors).
        self.assertEqual(self.out["consoleHits"], [])


if __name__ == "__main__":
    unittest.main()
