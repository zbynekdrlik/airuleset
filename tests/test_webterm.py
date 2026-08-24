"""Tests for the web terminal gateway (#555, cli_webterm.py).

Covers: inventory generation from the fleet table (never a hand list), the
per-host ssh identity rule matching the deploy loop, the connect-argv shapes
(local sh -c / identity ssh / sshpass ssh), the connect allowlist (an unknown
id is REFUSED, never execed — the security-critical path), dashboard/launcher/
unit rendering, and the dev1-only provisioning gate.
"""
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402


def _extract_js_function(html, name):
    """The source of the top-level `function <name>(...) { ... }` from the
    rendered dashboard's <script>, brace-matched so nested `{}` are handled.
    NB: the naive `{`/`}` counter does not skip braces inside string literals --
    it works because the extracted functions' string literals (the injected CSS)
    are net brace-balanced; a future unbalanced string brace would mis-extract,
    which the node behavioral test (`test_fit_behaviour_...`) catches as a
    SyntaxError. If that ever bites, make this string-aware."""
    start = html.index("function %s(" % name)
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError("unbalanced braces extracting %s" % name)


# A tiny node harness: runs the REAL extracted fitFixedGrid against a fake window
# whose fake xterm reports a cell size that scales with term.options.fontSize (so
# the font-fit loop converges), a viewport LARGER than the fixed grid at the base
# font, and a term.resize we can watch. Proves: the grid is FORCED to CFG's fixed
# (cols,rows), a later ttyd-style resize is CLAMPED, and the font is raised to fill.
_FIT_HARNESS = r"""
const CFG = { term_cols: 176, term_rows: 51 };
// #655: the fill caps are top-level consts in the real dashboard script (outside
// fitFixedGrid), so the harness must define them for the extracted function to
// reference. Kept in sync with cli_webterm.py by test_fit_fill_caps_match_source.
const WT_FILL_MAX_CELL_STRETCH = 1.5;
const WT_FILL_MAX_LINE_STRETCH = 1.8;
const CW = 0.6, CH = 1.2;                 // fake NATURAL monospace cell = 0.6*fs x 1.2*fs
// #678: cell dims track term.options — fontSize AND (native fill) lineHeight /
// letterSpacing. A real xterm bakes lineHeight into cell HEIGHT and letterSpacing
// into cell WIDTH (verified live: lineHeight 1.6 -> cellH 15->24; letterSpacing 3
// -> cellW 7->10), so the fill via those options grows the REAL cell — which is
// why xterm's own mouse hit-testing stays correct (it divides by that same cell).
function natural(term){
  const lh = term.options.lineHeight || 1, ls = term.options.letterSpacing || 0;
  return { width:  term.cols * (CW*term.options.fontSize + ls),
           height: term.rows * (CH*term.options.fontSize * lh) };
}
const term = {
  cols: 80, rows: 24,
  options: { fontSize: 13, lineHeight: 1, letterSpacing: 0, theme: { background: '#0d1117' } },
  resize(c, r){ this.cols = c; this.rows = r; },
};
const styleStore = {};
// #655: the fake grid element carries a `style` whose `transform` the FILL pass
// sets; getBoundingClientRect REFLECTS that scale (as real DOM does), so the
// harness proves the CSS-scale fill genuinely FILLS the viewport. querySelector
// returns this same element for both `.xterm` (the scale target) and
// `.xterm-screen` (the measured grid), matching the real same-element wiring.
const screenEl = {
  style: {},
  getBoundingClientRect(){
    const m = /scale\(\s*([\d.]+)\s*,\s*([\d.]+)\s*\)/.exec(this.style.transform || '');
    const sx = m ? parseFloat(m[1]) : 1, sy = m ? parseFloat(m[2]) : 1;
    const n = natural(term);
    return { width: n.width*sx, height: n.height*sy };
  },
};
const doc = {
  head: { appendChild(){} },
  getElementById(id){ return styleStore[id] || null; },
  createElement(){ const e = { set id(v){ this._id=v; styleStore[v]=e; }, get id(){ return this._id; } }; return e; },
  querySelector(){ return screenEl; },
};
const VW = (typeof HARNESS_VW !== 'undefined') ? HARNESS_VW : 1600;
const VH = (typeof HARNESS_VH !== 'undefined') ? HARNESS_VH : 1000;
const win = { term, document: doc, innerWidth: VW, innerHeight: VH };
%(fit)s
%(fill)s
const baseFont = 13;
// #655: fitFixedGrid clamps+resets+min-fits the font; fillFixedGrid is the
// DEFERRED pass that stretches the grid to fill. In the browser they run one
// async tick apart (so the natural-grid measure is settled); the harness is
// synchronous, so calling them back-to-back is the same contract.
const ok = fitFixedGrid(win);
const filled = fillFixedGrid(win);
term.resize(300, 80);                     // simulate ttyd's own FitAddon firing
const r = screenEl.getBoundingClientRect();   // FINAL size (reflects lineHeight/letterSpacing fill + any transform)
const sm = /scale\(\s*([\d.]+)\s*,\s*([\d.]+)\s*\)/.exec(screenEl.style.transform || '');
process.stdout.write(JSON.stringify({
  ok, filled, cols: term.cols, rows: term.rows, clampedTo: [term.cols, term.rows],
  fontSize: term.options.fontSize, baseFont,
  scaleX: sm ? parseFloat(sm[1]) : 1,
  scaleY: sm ? parseFloat(sm[2]) : 1,
  lineHeight: term.options.lineHeight, letterSpacing: term.options.letterSpacing,
  gridW: r.width, gridH: r.height, availW: win.innerWidth, availH: win.innerHeight,
}) + "\n");
"""


def _run_fit_harness(html_or_fit, vw=1600, vh=1000, fill_js=None):
    """Run the extracted fitFixedGrid + fillFixedGrid in node against a fake
    window of size (vw, vh); return the parsed JSON result dict. `html_or_fit`
    is either the fitFixedGrid source (with `fill_js` given) or the rendered
    dashboard HTML (both functions extracted from it). Skips if node absent."""
    import subprocess
    if fill_js is None:                 # `html_or_fit` is the rendered HTML
        fit_js = _extract_js_function(html_or_fit, "fitFixedGrid")
        fill_js = _extract_js_function(html_or_fit, "fillFixedGrid")
    else:
        fit_js = html_or_fit
    harness = ("const HARNESS_VW=%d, HARNESS_VH=%d;\n" % (vw, vh)) + (
        _FIT_HARNESS % {"fit": fit_js, "fill": fill_js})
    d = tempfile.mkdtemp()
    hp = Path(d) / "fitharness.js"
    hp.write_text(harness, encoding="utf-8")
    r = subprocess.run(["node", str(hp)], capture_output=True, text=True,
                       timeout=30)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
    return json.loads(r.stdout.strip().splitlines()[-1])


# A small, controlled fleet: one owner box (identity), one owner box (no
# identity/sshpass), one stream (identity), one stream (no identity), and one
# PENDING host that must be filtered out.
_FAKE_HOSTS = [
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "gatekeeper", "host": "10.0.0.9", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "david@subdev", "host": "10.0.0.5", "user": "david",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "montalu@subdev", "host": "10.0.0.5", "user": "montalu"},
    {"name": "ghost@subdev", "host": "10.0.0.5", "user": "ghost", "pending": True},
]
_FAKE_AUTHORITY = {"david": "fork-no-merge", "montalu": "branch-merge"}


def _fake_inventory():
    import airuleset
    with m.patch.object(airuleset, "REMOTE_HOSTS", _FAKE_HOSTS), \
            m.patch.object(airuleset, "AUTHORITY_BY_USER", _FAKE_AUTHORITY):
        return w.webterm_inventory()


class TestInventory(unittest.TestCase):
    def test_generated_from_fleet_plus_dev1_never_hand_list(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        # dev1 (localhost) is always first; the pending ghost host is filtered.
        self.assertEqual(ids[0], "dev1")
        self.assertIn("dev2", ids)
        self.assertIn("gatekeeper", ids)
        self.assertIn("david-subdev", ids)     # `@` sanitized to `-`
        self.assertIn("montalu-subdev", ids)
        self.assertNotIn("ghost-subdev", ids)   # pending -> excluded
        self.assertEqual(len(inv), 5)           # dev1 + 4 live hosts

    def test_ids_unique_and_url_safe(self):
        inv = _fake_inventory()
        ids = [e["id"] for e in inv]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r"^[a-z0-9-]+$")  # no @, no ., URL-safe

    def test_dev1_is_local(self):
        dev1 = next(e for e in _fake_inventory() if e["id"] == "dev1")
        self.assertTrue(dev1["local"])
        self.assertEqual(dev1["preferred"], "zbynek")
        self.assertEqual(dev1["kind"], "owner")

    def test_preferred_group_stream_is_user_owner_is_zbynek(self):
        inv = {e["id"]: e for e in _fake_inventory()}
        # stream accounts -> their unix user (the #264 whoami convention)
        self.assertEqual(inv["david-subdev"]["preferred"], "david")
        self.assertEqual(inv["david-subdev"]["kind"], "stream")
        self.assertEqual(inv["montalu-subdev"]["preferred"], "montalu")
        # owner boxes -> the owner group
        self.assertEqual(inv["dev2"]["preferred"], "zbynek")
        self.assertEqual(inv["dev2"]["kind"], "owner")
        self.assertEqual(inv["gatekeeper"]["preferred"], "zbynek")

    def test_identity_decision_matches_deploy_loop(self):
        # The web terminal's identity-vs-sshpass DECISION must not drift from the
        # deploy loop's: an entry has an identity iff its fleet row does.
        inv = {e["id"]: e for e in _fake_inventory()}
        self.assertEqual(inv["david-subdev"]["identity"],
                         "~/.secrets/gatekeeper_access_ed25519")
        self.assertIsNone(inv["montalu-subdev"]["identity"])
        self.assertIsNone(inv["dev2"]["identity"])


class TestConnectArgv(unittest.TestCase):
    def test_local_is_sh_c_with_attach_snippet(self):
        e = {"id": "dev1", "local": True, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sh")
        self.assertEqual(argv[1], "-c")
        self.assertIn("P=zbynek", argv[2])
        self.assertIn("new-session -A -s", argv[2])
        # No ssh for a local target.
        self.assertNotIn("ssh", argv)

    def test_identity_host_uses_ssh_i_with_pty(self):
        e = {"local": False, "user": "david", "host": "10.0.0.5",
             "identity": "~/.secrets/gatekeeper_access_ed25519", "preferred": "david"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser("~/.secrets/gatekeeper_access_ed25519"), argv)
        self.assertIn("-t", argv)                       # force a PTY
        self.assertIn("david@10.0.0.5", argv)
        self.assertNotIn("sshpass", argv)

    def test_no_identity_host_uses_sshpass(self):
        e = {"local": False, "user": "newlevel", "host": "10.0.0.2",
             "identity": None, "preferred": "zbynek"}
        argv = w.build_connect_argv(e)
        self.assertEqual(argv[0], "sshpass")
        self.assertIn("ssh", argv)
        self.assertIn("-t", argv)
        self.assertIn("newlevel@10.0.0.2", argv)

    def test_preferred_is_shell_quoted(self):
        # A pathological preferred value must be shell-quoted, never injected.
        e = {"id": "x", "local": True, "preferred": "a; rm -rf /"}
        argv = w.build_connect_argv(e)
        self.assertIn("'a; rm -rf /'", argv[2])


class TestConnectAllowlist(unittest.TestCase):
    """The security-critical path: connect_main validates the id against the
    generated inventory, never interpolates an unknown value into a shell."""

    def _inv_file(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "inv.json"
        p.write_text(json.dumps([
            {"id": "dev1", "local": True, "preferred": "zbynek"},
        ]), encoding="utf-8")
        return p

    def test_unknown_id_is_refused_and_never_execs(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")) as ex:
            rc = w.connect_main(["totally-unknown"], inventory_path=p)
        self.assertEqual(rc, 2)
        ex.assert_not_called()

    def test_no_id_is_refused(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main([], inventory_path=p)
        self.assertEqual(rc, 2)

    def test_known_id_execs_the_built_argv(self):
        p = self._inv_file()
        with m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["dev1"], inventory_path=p)
        ex.assert_called_once()
        cmd = ex.call_args[0][1]
        self.assertEqual(cmd[0], "sh")
        self.assertIn("P=zbynek", cmd[2])

    def test_unreadable_inventory_is_refused(self):
        with m.patch.object(w.os, "execvp",
                            side_effect=AssertionError("must not exec")):
            rc = w.connect_main(["dev1"], inventory_path="/no/such/inventory.json")
        self.assertEqual(rc, 2)


class TestRendering(unittest.TestCase):
    def test_dashboard_is_tabbed_ui_with_iframe_wiring(self):
        # #579: the dashboard is a single-page Windows-Terminal-style tabbed UI
        # (one iframe per session at the IP-first ttyd URL), NOT a landing page
        # of anchor cards.
        inv = [
            {"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
             "local": True, "host": None, "user": None},
            {"id": "david-subdev", "label": "david@subdev", "kind": "stream",
             "local": False, "host": "10.0.0.5", "user": "david"},
        ]
        html = w.render_dashboard_html(inv, ttyd_base="/t")
        # #584: same-origin RELATIVE ttyd base (`/t`); the JS builds `?arg=<id>`
        # from it. No cross-origin `<ip>:7682` any more.
        self.assertIn('"ttyd_base": "/t"', html)
        self.assertNotIn(":7682", html)
        self.assertIn("?arg=", html)              # JS constructs the ttyd URL
        # A top tab bar + lazy iframe container — the SPA, not a card grid.
        self.assertIn('id="tabbar"', html)
        self.assertIn('class="tab"', html)
        self.assertIn("iframe", html)
        self.assertIn("Ctrl+Alt", html)           # keyboard-switch hint
        # NOT the old landing-page card anchors.
        self.assertNotIn('class="card"', html)
        # session ids are in the embedded session list the JS iterates.
        self.assertIn('"dev1"', html)
        self.assertIn('"david-subdev"', html)

    def test_dashboard_escapes_label_and_alias(self):
        # A malicious label must never render as raw HTML in the tab / JSON.
        # (The page legitimately contains its OWN <script> for the tab logic,
        # so we assert on the injected payload specifically, not "<script>".)
        inv = [{"id": "x", "label": "<b>PWN</b>", "kind": "owner",
                "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("<b>PWN</b>", html)      # never unescaped
        self.assertIn("PWN", html)                # but the text survives escaped

    def test_dashboard_label_cannot_close_the_script_element(self):
        # A `</script>` in a label must not break out of the embedded config JS.
        inv = [{"id": "x", "label": "</script><script>alert(1)</script>",
                "kind": "owner", "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("</script><script>", html)   # neutralized in the JSON
        self.assertIn("\\u003c/script\\u003e", html)   # rendered as escaped codepoints

    def test_dashboard_sentinel_label_does_not_splice_config(self):
        # A label equal to a template sentinel must NOT splice the config JSON
        # into the tab markup (single-pass substitution guards this).
        inv = [{"id": "x", "label": "@@CFG_JSON@@", "kind": "owner",
                "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://100.64.0.9:7682")
        # The sentinel-shaped label survives as literal text in the tab title,
        # and the config JSON appears exactly ONCE (in the <script>, not spliced
        # into a title attribute).
        self.assertIn("@@CFG_JSON@@", html)
        self.assertEqual(html.count('"ttyd_base"'), 1)

    def test_launch_script_binds_loopback_behind_gateway(self):
        # #584: ttyd binds LOOPBACK only, behind a `-b /t` base path — the
        # gateway is the sole tailnet entry + authenticator. NO basic-auth
        # (`-c`), NO `-O` (the gateway does the Origin check; `-O` would break
        # the proxied WS), NO tailscale-IP bind.
        s = w.render_webterm_launch_script()
        self.assertIn("exec ttyd", s)
        self.assertIn("-p 7682", s)
        self.assertIn("-i 127.0.0.1", s)          # loopback bind
        self.assertIn("-b /t", s)                 # base path for the gateway proxy
        self.assertNotIn("-i 100.", s)            # never a tailscale/public IP
        self.assertNotIn(" -O", s)                # gateway does the Origin check
        self.assertNotIn(" -c ", s)               # no basic-auth (Bitwarden couldn't fill it)
        self.assertIn("-a", s)                    # url-arg
        self.assertIn("-W", s)                    # writable
        self.assertIn("cli_webterm.py webterm-connect", s)
        self.assertNotIn(str(w.WEBTERM_CRED_PATH), s)  # ttyd never reads the credential now

    def test_unit_substitutes_launch_script_placeholder(self):
        unit = w._render_webterm_unit()
        self.assertNotIn("{{LAUNCH_SCRIPT}}", unit)
        self.assertIn(str(w.WEBTERM_LAUNCH_PATH), unit)
        self.assertIn("WantedBy=default.target", unit)


_FAKE_TMUX = r"""#!/usr/bin/env bash
# Fake tmux: LOGS every invocation's argv to $FAKETMUX_LOG (one line each) and
# answers has-session/list-sessions from FAKETMUX_SESSIONS (newline-separated
# `group::name`, group may be empty). Proves the REAL _ATTACH_BODY builds the
# right direct-attach multi-attach commands (#613 REOPEN-3) and NEVER
# detaches another client.
echo "$*" >> "$FAKETMUX_LOG"
cmd="$1"; shift
case "$cmd" in
  has-session)
    name="${2#=}"
    while IFS= read -r line; do
      [ -n "$line" ] && [ "${line#*::}" = "$name" ] && exit 0
    done <<< "$FAKETMUX_SESSIONS"
    exit 1 ;;
  list-sessions)
    if [ "$2" = "#{session_group}::#{session_name}" ]; then
      printf '%s\n' "$FAKETMUX_SESSIONS"
    else
      while IFS= read -r line; do
        [ -n "$line" ] && printf '%s\n' "${line#*::}"
      done <<< "$FAKETMUX_SESSIONS"
    fi
    exit 0 ;;
esac
exit 0
"""


class TestAttachSnippetBehavior(unittest.TestCase):
    """#584: standard tmux multi-attach that NEVER disturbs an existing client.
    Run the REAL _ATTACH_BODY against a logging fake tmux and prove: an existing
    session is JOINED, and `attach -d` (the only verb that detaches other
    clients) is NEVER used. #586: the webterm client no longer forces ANY
    window-size policy on the target -- it drops the #584 `-gw window-size
    latest` + `aggressive-resize on` overrides (that was the ROOT regression:
    `latest` shrinks the shared window to whoever is active, so a small webterm
    client blackens the owner's WT view). #613 REOPEN-2 (owner directive
    2026-08-22): the `-f ignore-size` flag #586 added is RESTORED (the first
    reopen removed it, mis-targeting the browser client). It EXCLUDES the
    webterm client from tmux's window-size calc, so a SMALLER browser client can
    never shrink the owner's Windows-Terminal window (which then rendered a dark
    unused region -- his SURFACE; the browser the CAUSE). This is belt-and-
    suspenders under the restored conf `window-size manual` and fixes a box still
    running the first-reopen `latest` server immediately. The browser's OWN
    appearance at the fixed grid is solved on the browser side (the dashboard
    fit JS).

    #613 REOPEN-3 (supervisor finding, 2026-08-23, issue #613 comment
    5387073996): the THROWAWAY GROUPED CLONE this class used to prove (a
    second, same-group session created per webterm connect, cleaned up on
    disconnect) is REMOVED -- it silently blackened `Ctrl+B w` (tmux's own
    window-chooser) for the owner's OTHER attached client the instant the
    webterm browser also joined. An existing base is now joined by attaching
    to it DIRECTLY (`tmux attach-session -t "$T" -f ignore-size`) -- no
    clone, no `$$` session naming, no disconnect trap, no per-session
    `destroy-unattached` hook. See tests/test_webterm_ctrlbw_darkening.py
    for the live pty-driven regression proof of the actual chooser bug this
    fixes. #615's `mouse on` is RETARGETED, not dropped: it is now set on
    the shared base session itself (`-t "$T"`), since there is no more an
    independent clone session to scope it to. Post-#646 that session-scoped
    set is REDUNDANT with the fleet `-g mouse on` (kept so a box without the
    #646 conf still gets browser wheel->scrollback); and #648 (FIX LANDED)
    switched the disconnect trap to UNSET the session-local override
    (`set-option -u`) instead of forcing `mouse off`, so it can no longer
    override the fleet global. See the `_ATTACH_BODY` header comment in
    cli_webterm.py for the record."""

    def setUp(self):
        import subprocess
        self.subprocess = subprocess
        self.d = tempfile.mkdtemp()
        faketmux = Path(self.d) / "tmux"
        faketmux.write_text(_FAKE_TMUX, encoding="utf-8")
        os.chmod(faketmux, 0o755)
        self.log = Path(self.d) / "log"

    def _run(self, preferred, sessions):
        env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"],
                   FAKETMUX_SESSIONS=sessions, FAKETMUX_LOG=str(self.log))
        cmd = w._remote_command(preferred)
        self.subprocess.run(["sh", "-c", cmd], capture_output=True,
                            text=True, env=env, timeout=10)
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_grouped_owner_joins_survivor_directly(self):
        # dev1: zbynek + marek co-tenant. P=zbynek joins zbynek-4 (group
        # survivor) via a DIRECT attach, never marek, never a clone. #613
        # REOPEN-3: no more `new-session -d -t ... -s ...-web-<pid>` clone
        # creation anywhere on this path.
        log = self._run("zbynek", "zbynek::zbynek-4\nmarek::marek-12")
        self.assertRegex(log, r"\battach-session -t zbynek-4 -f ignore-size\b")
        self.assertNotIn("attach -d", log)
        self.assertNotRegex(log, r"new-session -d -t \S+ -s \S+-web-")

    def test_standalone_stream_joins_exact_directly(self):
        log = self._run("david", "::david\n::montalu")
        self.assertRegex(log, r"\battach-session -t david -f ignore-size\b")
        self.assertNotRegex(log, r"new-session -d -t \S+ -s \S+-web-")

    def test_single_session_joins_via_direct_attach(self):
        # gk: one session "0". This IS now a shared/direct attach (the fix)
        # -- the independent grouped-clone view #584/#613-REOPEN-2 used to
        # build is exactly what #613 REOPEN-3 removed (it broke Ctrl+B w).
        log = self._run("zbynek", "::0")
        self.assertRegex(log, r"\battach-session -t 0 -f ignore-size\b")
        self.assertNotRegex(log, r"new-session -d -t \S+ -s \S+-web-")

    def test_no_clone_lifecycle_machinery_anywhere(self):
        # #613 REOPEN-3 negative lock: the whole clone-cleanup apparatus
        # (disconnect trap that KILLS a session, per-session
        # destroy-unattached arming, kill of a throwaway `-web-<pid>`
        # session) must never reappear on the join path -- there is no
        # clone left to clean up. This is the "test that fails if the
        # grouped-clone shape is reintroduced" the ticket asked for, at the
        # shell-snippet level (see tests/test_webterm_ctrlbw_darkening.py
        # for the LIVE behavioral lock proving WHY it must never come
        # back). A disconnect TRAP itself is NOT banned any more -- the
        # #615 mouse-revert trap (test_disconnect_trap_unsets_mouse_
        # restoring_inheritance below) legitimately adds one, but (post-#648)
        # it only ever runs a `set-option -u ... mouse` (UNSET the
        # session-local override, restoring inheritance -- never forcing a
        # value), and NEVER a `kill-session`/`new-session` (checked
        # explicitly here, not just "no trap at all").
        cases = (
            ("zbynek", "zbynek::zbynek-4\nmarek::marek-12"),
            ("zbynek", "::0"),
            ("david", "::david"),
        )
        for preferred, sess in cases:
            log = self._run(preferred, sess)
            self.assertNotIn("kill-session", log)
            self.assertNotRegex(log, r"new-session -d -t")
            self.assertNotIn("client-attached", log)
            self.assertNotIn("destroy-unattached", log)
        cmd = w._remote_command("zbynek")
        self.assertNotIn("$$", cmd)
        self.assertNotIn("-web-", cmd)
        # The one trap that DOES exist is mouse-revert only -- never a
        # session-killing one. Post-#648 it UNSETS the session-local mouse
        # override (`set-option -u ... mouse`), restoring inheritance --
        # never forcing `mouse off` (which would override the #646 fleet
        # `-g mouse on`).
        trap_match = re.search(r"trap '([^']*)'", cmd)
        self.assertIsNotNone(trap_match, "expected exactly one trap (mouse revert)")
        self.assertIn("set-option -u", trap_match.group(1))
        self.assertIn("mouse", trap_match.group(1))
        self.assertNotIn("mouse off", trap_match.group(1))
        self.assertNotIn("kill-session", trap_match.group(1))
        self.assertNotIn("kill-server", trap_match.group(1))

    def test_disconnect_trap_unsets_mouse_restoring_inheritance(self):
        # #648 (FIX LANDED, Option 2): #613 REOPEN-3 armed a disconnect trap
        # that reverted the connect-set `mouse on` by FORCING `mouse off`.
        # #646 then made `-g mouse on` the fleet default, so a forced
        # session-LOCAL `mouse off` OVERRODE the global and left the owner's
        # own ssh session mouse-off after every webterm connect+disconnect.
        # The fix: the trap now UNSETS the session-local override
        # (`set-option -u -t "$T" mouse`) instead of forcing a value, so the
        # effective value falls back to inheritance (`-g mouse on` where
        # #646 is provisioned, factory default elsewhere) -- never a forced
        # `mouse off`. What this still locks mechanically: `$T` is
        # deferred-expanded at trap-FIRE time (single-quoted at trap-SET
        # time -- the same pattern the removed clone's own `$C` kill-session
        # trap used), so it always targets the session actually joined, not
        # whatever `$T` was later.
        log = self._run("zbynek", "zbynek::zbynek-4")
        self.assertRegex(log, r"set-option -u -t zbynek-4 mouse\b")
        # the OLD forced-off shape must be gone from the fired trap.
        self.assertNotRegex(log, r"set-option -t zbynek-4 mouse off")
        cmd = w._remote_command("zbynek")
        self.assertIn(
            'trap \'tmux set-option -u -t "$T" mouse 2>/dev/null || true\' '
            'EXIT HUP INT TERM', cmd)
        # the connect-side `mouse on` STAYS (covers a box without the #646
        # global); only the trap changed -- so the command must NOT force
        # `mouse off` anywhere.
        self.assertNotIn("mouse off", cmd)
        # armed BEFORE the attach (so it is live for the WHOLE connection,
        # not just after it), and the join path is no longer `exec`ed (an
        # exec'd process replaces the shell outright, which would prevent
        # the trap from ever running once the client detaches).
        mouse_on_pos = cmd.index("mouse on")
        trap_pos = cmd.index("trap '")
        attach_pos = cmd.index("attach-session")
        self.assertLess(mouse_on_pos, trap_pos)
        self.assertLess(trap_pos, attach_pos)
        self.assertNotRegex(cmd, r"exec tmux attach-session")

    def test_does_not_force_any_GLOBAL_window_size_policy_on_the_target(self):
        # #586: the ROOT regression was `-gw window-size latest` (+ aggressive-
        # resize) — that shrinks the shared window to the active client, so a
        # small webterm client blackens the owner's WT choose-tree. The connect
        # script must set NEITHER: GLOBAL window-size policy is
        # cli_tmux_provisioning's concern (the conf pins `window-size manual`
        # version-gated, #613 REOPEN-2), never the connect script's. The direct
        # attach's `-f ignore-size` (#613 REOPEN-3) is a per-ATTACH client flag
        # (checked below), NOT a global `set-option -g window-size`.
        log = self._run("zbynek", "zbynek::zbynek-4")
        self.assertNotIn("window-size latest", log)
        self.assertNotIn("aggressive-resize", log)
        self.assertNotIn("set-option -g window-size", log)

    def test_direct_attach_uses_ignore_size_so_it_never_shrinks_owner_window(self):
        # #613 REOPEN-2 (owner directive 2026-08-22): `-f ignore-size` on the
        # webterm attach is RESTORED (the first reopen removed it, mis-targeting
        # the browser client). #613 REOPEN-3: the attach target is now the base
        # session DIRECTLY (no more clone), but the flag itself is unchanged --
        # it EXCLUDES the webterm attach from tmux's window-size calc, so a
        # SMALLER browser client can never shrink the owner's Windows-Terminal
        # window (which then rendered a dark unused region — the owner's
        # SURFACE; the browser the CAUSE). Proven live (isolated tmux 3.7b + pty
        # clients, issue #613 comment 5387073996): with `-f ignore-size` the
        # owner's window stays 176x50 (full) at every attach + window-switch
        # from both sides -- AND (the actual #613 REOPEN-3 finding) the direct
        # attach is what keeps `Ctrl+B w` alive, unlike the removed clone shape
        # (see tests/test_webterm_ctrlbw_darkening.py). Never the fresh-base
        # fallback (see test_fresh_base_session_is_never_ignore_size).
        log = self._run("zbynek", "zbynek::zbynek-4")
        self.assertRegex(log, r"\battach-session -t zbynek-4 -f ignore-size\b")
        self.assertIn("ignore-size", log)

    def test_mouse_is_restored_on_the_shared_base_session_only(self):
        # #613 REOPEN-3: #615's `mouse on` is RETARGETED, not dropped. On the
        # (now-removed) clone it was scoped to the browser's own independent
        # session; with a direct attach there is only ONE session, so it is
        # now set on the BASE itself (`-t "$T"`). Post-#646 this session-
        # scoped set is REDUNDANT with the fleet `-g mouse on` but KEPT so a
        # box without the #646 conf still gets browser wheel->scrollback;
        # #648 (FIX LANDED) then made the disconnect trap UNSET this override
        # rather than force `mouse off`. This test still locks the RIGHT
        # thing: this join path never emits a global `-g mouse` (that is
        # cli_tmux_provisioning's job, #646) -- hence the
        # `assertNotIn("set-option -g mouse", log)` below stays valid.
        log = self._run("zbynek", "zbynek::zbynek-4")
        self.assertRegex(log, r"set-option -t zbynek-4 mouse on")
        self.assertNotIn("set-option -g mouse", log)
        cmd = w._remote_command("zbynek")
        self.assertIn('tmux set-option -t "$T" mouse on', cmd)

    def test_mouse_is_never_set_on_the_fresh_base_fallback(self):
        # No existing session -> nothing to share mouse mode with yet (no
        # browser has ever joined this session). The fresh-base path stays
        # exactly as before #615: no mouse option touched at all.
        log = self._run("zbynek", "")
        self.assertNotIn("mouse", log)

    def test_connect_never_sets_a_global_destroy_unattached(self):
        # #591/#613 REOPEN-3: no destroy-unattached policy, session-scoped OR
        # global, is ever set by this connect script any more -- a global
        # `destroy-unattached` (any value/unset) is exactly the base-killer
        # #591 removed, and global tmux policy is cli_tmux_provisioning's
        # concern, never the connect script's; the per-session variant #591
        # added for the (now-removed) clone has nothing left to scope to.
        for sess in ("zbynek::zbynek-4", "::0", ""):
            log = self._run("zbynek", sess)
            self.assertNotIn("destroy-unattached", log)

    def test_fresh_base_session_is_never_ignore_size(self):
        # No existing session -> the owner's own base is created; it is the real
        # viewing client, so it must NOT be ignore-size. The DIRECT attach to an
        # existing base IS `-f ignore-size` (#613 REOPEN-3,
        # test_direct_attach_uses_ignore_size…), so this fresh-base path is the
        # SOLE resolution path that is deliberately NOT ignore-size — the
        # owner's own base must size its own windows.
        log = self._run("zbynek", "")
        self.assertNotIn("ignore-size", log)

    def test_no_sessions_creates_preferred_and_does_not_kill_it(self):
        # No existing session -> create the owner's own base, which must PERSIST
        # (never trap-killed — it is not a throwaway clone).
        log = self._run("zbynek", "")
        self.assertRegex(log, r"new-session -A -s zbynek")
        self.assertNotIn("kill-session", log)

    def test_two_sessions_no_match_creates_preferred(self):
        log = self._run("gatekeeper", "zbynek::zbynek-4\nmarek::marek-12")
        self.assertRegex(log, r"new-session -A -s gatekeeper")

    def test_never_uses_detach_flag_anywhere(self):
        # `attach -d` is the ONLY tmux verb that detaches OTHER clients; it must
        # never appear in any resolution path (the owner's WT stays attached).
        for sess in ("zbynek::zbynek-4\nmarek::marek-12", "::0", ""):
            log = self._run("zbynek", sess)
            self.assertNotIn("attach -d", log)


class TestProvisioningGate(unittest.TestCase):
    def test_setup_is_dev1_only(self):
        fake = os.uname_result(("Linux", "dev2", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertFalse(w.is_webterm_gateway())
            self.assertFalse(w.setup_webterm_service())

    def test_gateway_true_on_dev1(self):
        fake = os.uname_result(("Linux", "dev1", "x", "x", "x"))
        with m.patch.object(w.os, "uname", return_value=fake):
            self.assertTrue(w.is_webterm_gateway())


def _run_returning(stdout="", rc=0):
    """A fake subprocess.run that ignores the argv and returns a fixed result."""
    def _run(cmd, **kw):
        return _R(rc, stdout, "")
    return _run


class _R:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class TestTailscaleIP(unittest.TestCase):
    """#579: the bind IP is dev1's DYNAMIC tailscale IP, validated to the CGNAT
    range (100.64.0.0/10) so a public/garbage value can NEVER become a bind."""

    def test_accepts_cgnat_ip(self):
        self.assertEqual(
            w._tailscale_ip(run=_run_returning("100.104.8.125\n")), "100.104.8.125")

    def test_rejects_public_ip(self):
        # A non-tailnet address must NEVER be returned (would risk a public bind).
        self.assertIsNone(w._tailscale_ip(run=_run_returning("1.2.3.4\n")))

    def test_rejects_empty(self):
        self.assertIsNone(w._tailscale_ip(run=_run_returning("")))

    def test_rejects_nonzero_rc(self):
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.104.8.125\n", rc=1)))

    def test_rejects_out_of_cgnat_100_range(self):
        # 100.200.x is NOT in 100.64.0.0/10 (second octet must be 64..127).
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.200.0.1\n")))

    def test_rejects_malformed_octets(self):
        # A malformed value in the CGNAT band (octet > 255) is still rejected.
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.64.999.1\n")))
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.64.1\n")))

    def test_boundary_ips(self):
        self.assertEqual(w._tailscale_ip(run=_run_returning("100.64.0.0\n")),
                         "100.64.0.0")
        self.assertEqual(w._tailscale_ip(run=_run_returning("100.127.255.255\n")),
                         "100.127.255.255")
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.63.0.1\n")))
        self.assertIsNone(w._tailscale_ip(run=_run_returning("100.128.0.1\n")))


class TestGatewayUnit(unittest.TestCase):
    def test_gateway_unit_binds_tailscale_ip_and_wires_ttyd_loopback(self):
        # #584: the gateway unit runs cli_webterm_gateway.py bound to the
        # tailscale IP on 8080, proxying to the loopback ttyd.
        unit = w._render_webterm_gateway_unit("100.104.8.125")
        self.assertNotIn("{{", unit)                       # every placeholder filled
        self.assertIn("cli_webterm_gateway.py", unit)
        self.assertIn("--bind 100.104.8.125", unit)        # tailnet-only bind
        self.assertIn("--port %d" % w.WEBTERM_GATEWAY_PORT, unit)   # 8080
        self.assertIn("--ttyd-host 127.0.0.1", unit)       # proxies loopback ttyd
        self.assertIn("--ttyd-port %d" % w.WEBTERM_TTYD_PORT, unit)
        self.assertIn("--cred %s" % str(w.WEBTERM_CRED_PATH), unit)
        self.assertIn("--dash-index %s" % str(w.WEBTERM_DASH_INDEX), unit)
        self.assertIn("--base-path /t", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn("--bind 0.0.0.0", unit)           # never public/wildcard
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        self.assertIn("--bind 100.104.8.125", exec_line)
        self.assertNotIn("http.server", exec_line)         # not the old static server


class TestOwnerAccessModeUnit(unittest.TestCase):
    """#635: the owner gateway can render in Cloudflare-Access mode (loopback bind
    + trust-header, password retired) — mirroring David's lane — while the default
    (password/tailnet) render stays byte-identical."""

    def test_access_mode_unit_uses_trust_header_and_loopback_no_cred(self):
        import cli_webterm_access as acc
        unit = w._render_webterm_gateway_unit("100.104.8.125", access_mode=True)
        self.assertNotIn("{{", unit)                       # every placeholder filled
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        # Cloudflare Access replaces the password: trust the injected identity
        # header, and NEVER pass --cred on this path.
        self.assertIn("--trust-access-header %s" % acc.WEBTERM_ACCESS_TRUST_HEADER,
                      exec_line)
        self.assertNotIn("--cred ", exec_line)
        # Loopback bind — cloudflared fronts it; no direct tailnet exposure.
        self.assertIn("--bind 127.0.0.1", exec_line)
        self.assertNotIn("--bind 0.0.0.0", exec_line)

    def test_access_mode_unit_prepends_honesty_correction_note(self):
        # #635 review 🟡: the installed Access-mode unit must NOT be left asserting
        # the shared template's now-false tailnet/password wording — a correction
        # note (like David's _DAVID_UNIT_NOTE) is prepended BEFORE the [Unit]
        # section, naming Cloudflare Access + loopback and marking the template's
        # tailnet-only claims FALSE.
        unit = w._render_webterm_gateway_unit("100.104.8.125", access_mode=True)
        self.assertIn("CLOUDFLARE-ACCESS mode", unit)
        self.assertIn("FALSE here", unit)
        self.assertLess(unit.index("#635"), unit.index("[Unit]"))
        # the default (password) render carries NO such note
        self.assertNotIn("CLOUDFLARE-ACCESS mode",
                         w._render_webterm_gateway_unit("100.104.8.125"))

    def test_default_mode_render_is_unchanged_password_tailnet(self):
        # Regression guard: with access_mode off (the default) the owner unit keeps
        # the password/tailnet ExecStart, matches an explicit access_mode=False
        # call, and — crucially — DIFFERS from the access-mode render (proving the
        # flag actually changes the emitted unit, not a tautology).
        default = w._render_webterm_gateway_unit("100.104.8.125")
        explicit_off = w._render_webterm_gateway_unit("100.104.8.125", access_mode=False)
        access_on = w._render_webterm_gateway_unit("100.104.8.125", access_mode=True)
        self.assertEqual(default, explicit_off)
        self.assertNotEqual(default, access_on)            # the flag has real teeth
        self.assertIn("--cred %s" % str(w.WEBTERM_CRED_PATH), default)
        self.assertIn("--bind 100.104.8.125", default)
        self.assertNotIn("--trust-access-header", default)


class TestShortAlias(unittest.TestCase):
    def _e(self, **kw):
        base = {"id": kw.get("id"), "label": kw.get("label", kw.get("id")),
                "local": False, "user": kw.get("user")}
        base.update(kw)
        return base

    def test_owner_and_family_aliases(self):
        self.assertEqual(w._short_alias(self._e(id="dev1", local=True, user=None)), "dev1")
        self.assertEqual(w._short_alias(self._e(id="dev2", user="newlevel")), "dev2")
        self.assertEqual(w._short_alias(self._e(id="gatekeeper", user="gatekeeper")), "gk")
        self.assertEqual(w._short_alias(self._e(id="montalu3-subdev", user="montalu3")), "m3")
        self.assertEqual(w._short_alias(self._e(id="montalu8-subdev", user="montalu8")), "m8")
        self.assertEqual(w._short_alias(self._e(id="miva1-subdev", user="miva1")), "miva")
        self.assertEqual(w._short_alias(self._e(id="david-subdev", user="david")), "d1")
        self.assertEqual(w._short_alias(self._e(id="david4-subdev", user="david4")), "d4")

    def test_unknown_gets_sensible_short_form(self):
        # An unrecognized session still gets a short, non-empty alias.
        a = w._short_alias(self._e(id="admin-forestshop-dev", user="admin"))
        self.assertTrue(0 < len(a) <= 8)
        self.assertNotIn("@", a)

    def test_tab_order_stable(self):
        # dev1, dev2, gk, m1.., miva, d*, then the rest alphabetically.
        aliases = ["d2", "m3", "gk", "dev2", "miva", "dev1", "m1", "zzz", "d1", "aaa"]
        ordered = sorted(aliases, key=w._tab_order_key)
        self.assertEqual(
            ordered, ["dev1", "dev2", "gk", "m1", "m3", "miva", "d1", "d2", "aaa", "zzz"])

    def test_tab_sessions_end_to_end_order(self):
        # The FULL pipeline (alias derivation -> sort) over a realistic
        # inventory produces the owner's stable Windows-Terminal tab order.
        inv = [
            {"id": "montalu3-subdev", "label": "montalu3@subdev", "user": "montalu3"},
            {"id": "dev2", "label": "dev2", "user": "newlevel"},
            {"id": "david-subdev", "label": "david@subdev", "user": "david"},
            {"id": "dev1", "label": "dev1 (localhost)", "user": None, "local": True},
            {"id": "gatekeeper", "label": "gatekeeper", "user": "gatekeeper"},
            {"id": "montalu1-subdev", "label": "montalu1@subdev", "user": "montalu1"},
            {"id": "miva1-subdev", "label": "miva1@subdev", "user": "miva1"},
            {"id": "admin-forestshop-dev", "label": "admin@forestshop-dev", "user": "admin"},
        ]
        aliases = [t["alias"] for t in w._tab_sessions(inv)]
        self.assertEqual(
            aliases, ["dev1", "dev2", "gk", "m1", "m3", "miva", "d1", "admin"])


class TestSetupWiring(unittest.TestCase):
    """Drives setup_webterm_service() with every path constant redirected into a
    temp dir + systemctl/whoami mocked, proving the install path (a) never runs
    `tailscale serve --bg`, (b) resets any leftover serve config, (c) writes an
    IP-first tabbed dashboard + dash unit, (d) refuses (writes nothing) with no
    tailscale IP, and (e) is idempotent over a re-run (converges, never dups)."""

    def _isolate(self, stack, tmp):
        p = Path(tmp)
        (p / ".claude").mkdir(parents=True, exist_ok=True)
        (p / ".secrets").mkdir(parents=True, exist_ok=True)
        (p / ".cloudflared").mkdir(parents=True, exist_ok=True)
        (p / ".config" / "systemd" / "user").mkdir(parents=True, exist_ok=True)
        patches = {
            "CLAUDE_DIR": p / ".claude",
            "SECRETS_DIR": p / ".secrets",
            "WEBTERM_INVENTORY_PATH": p / ".claude" / "webterm-inventory.json",
            "WEBTERM_DASH_DIR": p / ".claude" / "webterm-dash",
            "WEBTERM_DASH_INDEX": p / ".claude" / "webterm-dash" / "index.html",
            "WEBTERM_LAUNCH_PATH": p / ".claude" / "airuleset-webterm-ttyd.sh",
            "WEBTERM_CRED_PATH": p / ".secrets" / "webterm_credential",
            "WEBTERM_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-ttyd.service",
            "WEBTERM_GATEWAY_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-gateway.service",
            "WEBTERM_OLD_DASH_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-dash.service",
        }
        for name, val in patches.items():
            stack.enter_context(m.patch.object(w, name, val))
        # #635: the go-live DEFAULT is Access mode, whose success path calls into the
        # cli_webterm_tunnel leaf. Redirect ITS owner-tunnel path constants into tmp —
        # on dev1 the REAL creds JSON exists, so an unpatched run would write real
        # ~/.cloudflared / systemd files. No creds JSON is created in tmp, so
        # setup_webterm_owner_tunnel stays a safe no-op in these tests.
        import cli_webterm_tunnel as tun
        (p / ".cloudflared").mkdir(parents=True, exist_ok=True)
        for name, val in {
            "WEBTERM_CLOUDFLARED_DIR": p / ".cloudflared",
            "WEBTERM_OWNER_TUNNEL_CREDS": p / ".cloudflared" / (tun.WEBTERM_OWNER_TUNNEL_UUID + ".json"),
            "WEBTERM_OWNER_TUNNEL_CONFIG": p / ".cloudflared" / "webterm-owner.yml",
            "WEBTERM_OWNER_TUNNEL_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-owner-tunnel.service",
        }.items():
            stack.enter_context(m.patch.object(tun, name, val))
        fake = os.uname_result(("Linux", "dev1", "x", "x", "x"))
        stack.enter_context(m.patch.object(w.os, "uname", return_value=fake))
        stack.enter_context(m.patch.object(w.shutil, "which", return_value="/usr/bin/ttyd"))
        stack.enter_context(m.patch.object(
            w, "webterm_inventory",
            return_value=[{"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
                           "local": True, "host": None, "user": None, "preferred": "zbynek"}]))
        import cli_filedrop_watchdog as fw
        stack.enter_context(m.patch.object(fw, "_run_systemctl", lambda args: (0, "", "")))
        stack.enter_context(m.patch.object(fw, "_whoami", lambda: "zbynek"))
        return patches

    class _RunRec:
        def __init__(self, ip="100.104.8.125"):
            self.calls, self.ip = [], ip

        def __call__(self, cmd, **kw):
            self.calls.append(list(cmd))
            if list(cmd[:3]) == ["tailscale", "ip", "-4"]:
                return _R(0, (self.ip + "\n") if self.ip else "", "")
            return _R(0, "", "")

    def test_install_path_has_no_serve_but_resets_and_writes_same_origin(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            # #635: the go-live DEFAULT is now Access mode; this test exercises the
            # still-present PASSWORD/tailnet code path, so pin the flag OFF.
            st.enter_context(m.patch.object(w, "OWNER_GATEWAY_ACCESS_MODE", False))
            rec = self._RunRec()
            ok = w.setup_webterm_service(run=rec)
            self.assertTrue(ok)
            # (a) NEVER runs `tailscale serve --bg` (the 404-causing layer).
            self.assertFalse(any(c[:2] == ["tailscale", "serve"] and "--bg" in c
                                 for c in rec.calls), rec.calls)
            # (b) resets any leftover serve config.
            self.assertTrue(any(list(c[:3]) == ["tailscale", "serve", "reset"]
                                for c in rec.calls), rec.calls)
            # (c) #584: SAME-ORIGIN tabbed dashboard (relative /t, no :7682) +
            # the GATEWAY unit bound to the tailscale IP + the loopback ttyd launch.
            html = pt["WEBTERM_DASH_INDEX"].read_text()
            self.assertIn('"ttyd_base": "/t"', html)
            self.assertNotIn(":7682", html)
            self.assertIn('id="tabbar"', html)
            self.assertNotIn('class="card"', html)
            unit = pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text()
            self.assertIn("--bind 100.104.8.125", unit)
            self.assertIn("cli_webterm_gateway.py", unit)
            launch = pt["WEBTERM_LAUNCH_PATH"].read_text()
            self.assertIn("-i 127.0.0.1", launch)     # ttyd is loopback now
            self.assertIn("-b /t", launch)
            self.assertNotIn("-i 100.", launch)

    def test_no_tailscale_ip_refuses_and_writes_no_unit(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            # #635: the "refuse with no tailscale IP" guard is the PASSWORD-mode
            # invariant (Access mode binds loopback + needs no tailnet IP), so pin
            # the flag OFF to exercise it.
            st.enter_context(m.patch.object(w, "OWNER_GATEWAY_ACCESS_MODE", False))
            rec = self._RunRec(ip="")          # tailscale ip -4 returns nothing
            ok = w.setup_webterm_service(run=rec)
            self.assertFalse(ok)               # LOUD refusal
            # NEVER writes a unit with a possibly-public bind.
            self.assertFalse(pt["WEBTERM_GATEWAY_SERVICE_DEST"].exists())
            self.assertFalse(pt["WEBTERM_SERVICE_DEST"].exists())

    def test_reinstall_is_idempotent(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            self.assertTrue(w.setup_webterm_service(run=self._RunRec()))
            first = pt["WEBTERM_DASH_INDEX"].read_text()
            first_unit = pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text()
            self.assertTrue(w.setup_webterm_service(run=self._RunRec()))
            self.assertEqual(first, pt["WEBTERM_DASH_INDEX"].read_text())
            self.assertEqual(first_unit, pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text())

    def test_access_mode_binds_loopback_retires_cred_needs_no_tailscale_ip(self):
        # #635: with the go-live flag ON, the owner gateway provisions in
        # Cloudflare-Access mode — loopback bind + trust-header, the password
        # credential RETIRED, and NO tailscale IP required (cloudflared fronts it).
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._isolate(st, tmp)
            st.enter_context(m.patch.object(w, "OWNER_GATEWAY_ACCESS_MODE", True))
            # a stale password credential must be retired by the Access-mode path
            pt["WEBTERM_CRED_PATH"].write_text("zbynek:deadbeef\n")
            rec = self._RunRec(ip="")            # tailscale ip -4 returns nothing
            ok = w.setup_webterm_service(run=rec)
            self.assertTrue(ok)                  # Access mode does NOT need a tailnet IP
            unit = pt["WEBTERM_GATEWAY_SERVICE_DEST"].read_text()
            import cli_webterm_access as acc
            self.assertIn("--trust-access-header %s"
                          % acc.WEBTERM_ACCESS_TRUST_HEADER, unit)
            self.assertNotIn("--cred ", unit)
            self.assertIn("--bind 127.0.0.1", unit)
            # the password credential is retired (no password path any more)
            self.assertFalse(pt["WEBTERM_CRED_PATH"].exists())

    def test_access_mode_provisions_the_managed_owner_tunnel(self):
        # #635: the go-live DEFAULT (Access mode) must CALL the managed-tunnel
        # provisioner — the public front is a reconciled cloudflared unit, not a
        # hand-made one. Behavioral (a spy), not a brittle getsource text check.
        import contextlib
        import cli_webterm_tunnel as tun
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            self._isolate(st, tmp)
            spy = []
            st.enter_context(m.patch.object(
                tun, "setup_webterm_owner_tunnel", lambda run=None: spy.append(True)))
            # Access mode needs no tailscale IP; the default flag is now True.
            self.assertTrue(w.setup_webterm_service(run=self._RunRec(ip="")))
            self.assertEqual(spy, [True])       # tunnel provisioned in access mode

    def test_password_mode_does_not_provision_owner_tunnel(self):
        import contextlib
        import cli_webterm_tunnel as tun
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            self._isolate(st, tmp)
            st.enter_context(m.patch.object(w, "OWNER_GATEWAY_ACCESS_MODE", False))
            spy = []
            st.enter_context(m.patch.object(
                tun, "setup_webterm_owner_tunnel", lambda run=None: spy.append(True)))
            self.assertTrue(w.setup_webterm_service(run=self._RunRec()))
            self.assertEqual(spy, [])           # password mode never touches the tunnel


class TestTabSwitchingUX(unittest.TestCase):
    """#582: Ctrl+Alt+N can't switch tabs while focus is inside a cross-origin
    ttyd iframe (a web-platform limitation — the parent :8080 page never
    receives keydowns dispatched inside the :7682 iframe). The proportionate
    fix keeps reliable CLICK switching and adds discoverable UX: ordinal
    badges (the visible Ctrl+Alt+N map), Prev/Next cycle controls that step
    through ALL sessions, Ctrl+Alt+arrow cycling, and an honest hint."""

    def _inv(self, n):
        return [{"id": "s%02d" % i, "label": "sess %02d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "usr%02d" % i}
                for i in range(1, n + 1)]

    def test_ordinal_badges_on_first_nine_tabs_only(self):
        # 11 sessions -> exactly 9 ordinal badges (the Ctrl+Alt+1..9 range),
        # numbered 1..9; the 10th/11th tabs carry no badge.
        html = w.render_dashboard_html(self._inv(11), ttyd_base="http://b:7682")
        self.assertEqual(html.count('class="ord"'), 9)
        self.assertIn('<span class="ord">1</span>', html)
        self.assertIn('<span class="ord">9</span>', html)
        self.assertNotIn('<span class="ord">10</span>', html)
        self.assertNotIn('<span class="ord">11</span>', html)

    def test_ordinal_badge_count_matches_small_inventory(self):
        html = w.render_dashboard_html(self._inv(3), ttyd_base="http://b:7682")
        self.assertEqual(html.count('class="ord"'), 3)
        self.assertIn('<span class="ord">1</span>', html)
        self.assertIn('<span class="ord">3</span>', html)

    def test_ordinal_badges_are_only_digits_never_a_label(self):
        # Even a hostile label must never leak into the ordinal badge — the
        # badge is a fixed position digit, not user data.
        inv = [{"id": "x", "label": "<b>PWN</b>", "kind": "owner",
                "local": True, "host": None, "user": None}]
        # A single-tab inventory still gets one badge — assert it EXISTS so the
        # digits-only guarantee can never be met vacuously (0 badges).
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        import re as _re
        matches = _re.findall(r'<span class="ord">([^<]*)</span>', html)
        self.assertTrue(matches)                      # a badge is actually present
        for content in matches:
            self.assertRegex(content, r"^[0-9]+$")

    def test_keydown_is_direct_jump_only_no_arrow_binding(self):
        # Ctrl+Alt+1..9 direct-jumps when the tab bar has focus — gate the ACTUAL
        # handler code, not the decorative "Ctrl+Alt" hint text. (#674 removed the
        # prev/next arrows + the cycle() helper; direct-jump switching stays and is
        # the only cycling path now, alongside tab clicks.)
        html = w.render_dashboard_html(self._inv(4), ttyd_base="http://b:7682")
        self.assertIn("e.key >= '1'", html)          # the digit-jump handler
        # No Ctrl+Alt+arrow binding: Ctrl+Alt+Left/Right is the Linux desktop
        # workspace-switch shortcut, grabbed by the compositor before the page
        # sees it — advertising it would over-promise (both #582 reviewers).
        self.assertNotIn("ArrowLeft", html)
        self.assertNotIn("ArrowRight", html)

    def test_ux_additions_do_not_break_escaping_or_single_pass(self):
        # The security invariants from #579 must survive the UX additions.
        inv = [{"id": "x", "label": "</script><script>alert(1)</script>",
                "kind": "owner", "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="http://b:7682")
        self.assertNotIn("</script><script>", html)
        self.assertEqual(html.count('"ttyd_base"'), 1)


class TestSameOriginKeyboard(unittest.TestCase):
    """#584 supersedes #582's residual: the gateway makes the terminal iframes
    SAME-ORIGIN, so Ctrl+Alt+1..9 now works even while focus is IN a terminal —
    a per-iframe capture-phase keydown forwarder (only possible same-origin)
    reaches the parent's switch logic before xterm consumes the keystroke."""

    def _inv(self, n=3):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_dashboard_uses_same_origin_ttyd_base(self):
        # The caller now passes a RELATIVE base (`/t`) so each iframe is
        # same-origin with the dashboard — never an absolute `<ip>:7682` origin.
        html = w.render_dashboard_html(self._inv(2), ttyd_base="/t")
        self.assertIn('"ttyd_base": "/t"', html)
        self.assertIn("?arg=", html)              # iframe src still built from it
        self.assertNotIn(":7682", html)           # no cross-origin ttyd port

    def test_per_iframe_keydown_forwarder_is_wired_on_load(self):
        # The fix: attach a keydown listener to each terminal iframe's OWN window
        # when it loads (same-origin makes `contentWindow` reachable), in the
        # CAPTURE phase so it fires before xterm swallows the key.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("contentWindow", html)
        self.assertIn("addEventListener('load'", html)
        # capture-phase keydown forwarder (the `true` 3rd arg is load-bearing)
        self.assertRegex(html, r"addEventListener\('keydown',\s*\w+,\s*true\)")

    def test_hotkey_handler_shared_and_stops_propagation(self):
        # ONE shared handler used by BOTH the parent bar AND the per-iframe
        # forwarder; it stops propagation so xterm never also processes the key.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("e.key >= '1'", html)        # the digit-jump logic
        self.assertIn("stopPropagation", html)     # xterm must not also get it

    def test_no_stale_shortcut_origin_limitation_copy(self):
        # The honest #582 residual ("during typing it's a different origin — use
        # click") must never reappear anywhere on the page. (#674 removed the whole
        # #hint help overlay, so this is now a whole-page invariant.)
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn("iný origin", html)


class TestAllTabsPreloaded(unittest.TestCase):
    """#586: SUPERSEDES #585's disconnect-on-hide. #585 disconnected every hidden
    tab (about:blank) so it could not shrink the shared window under `window-size
    latest` — but that made switching slow (reconnect on every switch, a refresh
    feel) AND caused a "Leave site?" dialog on every tab click (navigating the
    iframe to about:blank unloads ttyd's page, firing ttyd's OWN beforeunload).
    #586 made every tab PRELOAD + stay connected instead of disconnecting hidden
    ones. #613 REOPEN-2: the tmux window is FIXED (`window-size manual` +
    `default-size 176x50`) and every webterm attach is `-f ignore-size` (#613
    REOPEN-3: a direct attach now, no clone), so NO tab — hidden or active —
    can ever resize a window; keeping every tab connected is
    unconditionally safe. All tabs PRELOAD at login and stay connected; switching
    is pure show/hide (instant, no reconnect, no iframe navigation, so no
    beforeunload dialog on a tab click). Each ttyd xterm is force-fit to the fixed
    grid on the browser side (see TestBrowserFixedGridFit)."""

    def _inv(self, n=3):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_no_disconnect_on_hide_suspend_or_about_blank(self):
        # The #585 disconnect mechanism is GONE — no suspend(), no about:blank
        # navigation (that navigation was the ROOT of both the slow-switch feel
        # and the Leave-site dialog on tab click).
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn("function suspend(", html)
        self.assertNotIn("about:blank", html)

    def test_all_tabs_preloaded_and_connected_at_login(self):
        # Every tab's iframe is created AND connected up front (keepalive), not
        # lazily on first activation — so a switch never has to (re)connect.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("function preloadAll(", html)   # the preload helper
        self.assertIn("preloadAll()", html)           # it is actually CALLED
        self.assertIn("ttydSrc(", html)               # each frame gets a real src

    def test_switch_is_pure_show_hide_no_src_change_inside_activate(self):
        # activate() must ONLY toggle display (block/none) — never reassign an
        # iframe's src (that would be a reconnect/navigation, and a navigation is
        # exactly what fired the Leave-site dialog in #585). Isolate the
        # activate() body and prove: it toggles display on the +k===idx test and
        # never touches `.src`.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        m = re.search(r"function activate\(idx\)\s*\{(.*?)\n\}", html, re.S)
        self.assertIsNotNone(m, "activate() body not found")
        body = m.group(1)
        self.assertIn("+k === idx", body)     # the show/hide branch key
        self.assertIn("'block'", body)        # active tab shown
        self.assertIn("'none'", body)         # others hidden
        # #586: switching must NOT (re)connect or suspend any iframe — those were
        # the #585 navigations that made switching slow AND fired the Leave-site
        # dialog. Pure display toggle only.
        self.assertNotIn("suspend(", body)
        self.assertNotIn("connect(", body)
        self.assertNotIn(".src", body,
                         "activate() must not navigate any iframe (pure show/hide)")

    def test_exactly_one_iframe_src_assignment_in_the_whole_script(self):
        # #586 review 🔵: the activate-body check catches a regression that
        # navigates INSIDE activate, but a differently-named nav helper (e.g.
        # reconnect(f,s){ f.src=... } called from activate) would evade it.
        # Close it whole-script: the ONLY place an iframe src is (re)assigned is
        # makeFrame's initial connect — exactly ONE `.src =` in the script.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        self.assertEqual(script.count(".src ="), 1)

    def test_no_stale_disconnect_language_on_page(self):
        # The #585 "odpojí/obnoví" disconnect copy must never reappear (tabs stay
        # preloaded + connected — behaviour locked by the other tests in this
        # class). #674 removed the #hint overlay that used to carry this copy, so
        # this is now a whole-page invariant.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn("odpojí", html)              # no disconnect language anywhere


class TestBrowserFixedGridFit(unittest.TestCase):
    """#613 REOPEN-2 (owner directive 2026-08-22): the owner's fixed terminal
    size is the invariant (`window-size manual`), so the tmux window never
    resizes to any client. The BROWSER adapts to that fixed grid instead of the
    other way round: each ttyd xterm is forced to the owner's fixed CLIENT grid
    (`_webterm_term_grid()` = TMUX_DEFAULT_SIZE 176x50 window + 1 status row =
    176x51) and its font is scaled to fill the viewport, so the fixed window
    fills the browser with no dark unused region AND without the browser ever
    influencing tmux sizing (every webterm attach is `-f ignore-size` --
    #613 REOPEN-3: a direct attach now, no clone). ttyd 1.7.4 exposes
    the xterm Terminal as `window.term` in each same-origin iframe; the dashboard
    clamps `term.resize` so ttyd's own FitAddon can never change the grid, then
    scales `term.options.fontSize` (crisp re-render, not a blurry CSS scale)."""

    def _inv(self):
        return [{"id": "s1", "label": "sess 1", "kind": "owner",
                 "local": False, "host": "10.0.0.1", "user": "u1"}]

    def test_grid_is_derived_from_default_size_plus_status_row(self):
        # The fixed CLIENT grid = the tmux WINDOW size (TMUX_DEFAULT_SIZE) +
        # WEBTERM_STATUS_ROWS — DERIVED, never a duplicated literal, so it can
        # never drift from the conf's default-size (a parity lock).
        import cli_tmux_provisioning as prov
        cols, rows = w._webterm_term_grid()
        dw, dh = (int(x) for x in prov.TMUX_DEFAULT_SIZE.lower().split("x"))
        self.assertEqual(cols, dw)
        self.assertEqual(rows, dh + w.WEBTERM_STATUS_ROWS)
        # today's concrete value: the owner's live Windows-Terminal client.
        self.assertEqual((cols, rows), (176, 51))

    def test_cfg_carries_the_fixed_grid(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        m = re.search(r"const CFG = (\{.*?\});", html)
        self.assertIsNotNone(m, "CFG literal not found")
        cfg = json.loads(m.group(1))
        self.assertEqual(cfg["term_cols"], 176)
        self.assertEqual(cfg["term_rows"], 51)

    def test_activate_fits_the_now_visible_tab(self):
        # the fit runs when a tab becomes VISIBLE (a hidden iframe has a 0-size
        # viewport), so activate() must call applyFixedGrid on the shown frame.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        m = re.search(r"function activate\(idx\)\s*\{(.*?)\n\}", html, re.S)
        self.assertIn("applyFixedGrid(", m.group(1))

    def test_fit_clamps_resize_and_scales_fontsize(self):
        # source-lock the two load-bearing mechanics: (1) term.resize is
        # OVERRIDDEN to clamp to the fixed grid (defeats ttyd's FitAddon), and
        # (2) term.options.fontSize is set (crisp font scaling for the PRIMARY
        # scale). fitFixedGrid must never SCALE via a CSS transform (its
        # transform:'none' only defensively CLEARS a pre-#678 leftover); the
        # residual fill lives in fillFixedGrid and is now NATIVE cell sizing
        # (lineHeight/letterSpacing), never a CSS transform (#678).
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        fn = _extract_js_function(html, "fitFixedGrid")
        self.assertIn("term.resize =", fn)             # clamp installed
        self.assertIn("real(cols, rows)", fn)          # clamped to the fixed grid
        self.assertIn("term.options.fontSize", fn)     # font scaling (crisp)
        self.assertNotIn("scale(", fn)                 # never a CSS scale in the PRIMARY fit
        apply = _extract_js_function(html, "applyFixedGrid")
        self.assertIn("win.term", _extract_js_function(html, "fitFixedGrid"))
        self.assertIn("setTimeout", apply)             # polls for async ttyd term

    def test_fit_behaviour_forces_grid_and_scales_font_with_a_fake_term(self):
        # BEHAVIOURAL proof (node, no jsdom needed): run the REAL extracted
        # fitFixedGrid against a fake window whose xterm reports a viewport
        # LARGER than the fixed grid at the base font -> the function must clamp
        # term.resize to the fixed (176,51), keep cols/rows there even when ttyd
        # later "fits" to a big size, and RAISE the font so the grid fills the
        # viewport. (The live real-ttyd proof is the worker's browser run.)
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        out = _run_fit_harness(html)                    # runs fit + deferred fill
        self.assertTrue(out["ok"])                      # fit applied
        self.assertEqual(out["cols"], 176)              # grid FORCED to the fixed cols
        self.assertEqual(out["rows"], 51)               # ... and rows
        self.assertEqual(out["clampedTo"], [176, 51])   # a later ttyd fit is clamped
        self.assertGreater(out["fontSize"], out["baseFont"])  # font raised to fill
        self.assertLessEqual(out["gridW"], out["availW"] + 1)  # never overflows width
        self.assertLessEqual(out["gridH"], out["availH"] + 1)  # ... nor height

    def test_fit_fills_the_viewport_no_letterbox_via_bounded_stretch(self):
        # #655 RED->GREEN, mechanism updated by #678: the min-fit font alone
        # LETTERBOXES whenever the viewport aspect != the fixed 176x51 grid aspect
        # (the owner's "okno v strede"). #655 filled the residual with a CSS
        # transform scale; #678 proved that BREAKS xterm's mouse hit-test, so the
        # fill now grows the REAL cell via lineHeight (vertical) + letterSpacing
        # (horizontal) instead -- correct mouse (see
        # test_fill_does_not_offset_mouse_selection). lineHeight is a fine float
        # multiplier so the VERTICAL loose dim fills near-exactly; letterSpacing is
        # integer px/cell so the HORIZONTAL fill is COARSER (a small residual
        # letterbox may remain -- #678: a working mouse outranks a pixel-exact
        # fill). Either way it never OVERFLOWS and never CSS-scales the terminal.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        for vw, vh, tag in ((1920, 1011, "laptop wide (horizontal margin)"),
                            (1400, 1000, "tall-ish window (vertical margin)")):
            out = _run_fit_harness(html, vw, vh)
            fillW = out["gridW"] / out["availW"]
            fillH = out["gridH"] / out["availH"]
            # vertical loose dim fills near-exactly via lineHeight; horizontal via
            # coarse letterSpacing fills much better than the ~88% fontSize-only
            # letterbox but not pixel-exact (RED #655: current code letterboxed the
            # loose dim by 10%+). NEVER overflows, NEVER a CSS transform (#678).
            self.assertGreaterEqual(
                fillW, 0.95,
                "%s: grid must FILL the width via letterSpacing (coarse ok, no big "
                "letterbox); filled %.1f%% (ls=%s)" % (tag, fillW * 100, out["letterSpacing"]))
            self.assertGreaterEqual(
                fillH, 0.98,
                "%s: grid must FILL the height via lineHeight; filled %.1f%% "
                "(lh=%s)" % (tag, fillH * 100, out["lineHeight"]))
            self.assertLessEqual(out["gridW"], out["availW"] + 1, "%s: no overflow W" % tag)
            self.assertLessEqual(out["gridH"], out["availH"] + 1, "%s: no overflow H" % tag)
            self.assertEqual(out["scaleX"], 1, "%s: no CSS transform (breaks mouse)" % tag)
            self.assertEqual(out["scaleY"], 1, "%s: no CSS transform (breaks mouse)" % tag)

    def test_fit_fill_caps_match_source(self):
        # #655: the node harness hardcodes the fill caps (they are top-level
        # consts in the dashboard script, outside fitFixedGrid). Lock that the
        # harness values equal the source values so the behavioural tests can
        # never silently drift from what ships.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertRegex(html, r"WT_FILL_MAX_CELL_STRETCH\s*=\s*1\.5\b")
        self.assertRegex(html, r"WT_FILL_MAX_LINE_STRETCH\s*=\s*1\.8\b")
        self.assertIn("const WT_FILL_MAX_CELL_STRETCH = 1.5;", _FIT_HARNESS)
        self.assertIn("const WT_FILL_MAX_LINE_STRETCH = 1.8;", _FIT_HARNESS)

    def test_fit_stretch_is_bounded_so_extreme_viewport_never_distorts(self):
        # #655/#678: an EXTREME viewport must NOT stretch a cell without bound --
        # the native fill (lineHeight/letterSpacing, #678) caps the stretch and
        # letterboxes the remainder, so text never becomes grotesque, AND it never
        # CSS-scales the terminal (scaleX==scaleY==1). A very TALL viewport caps the
        # VERTICAL lineHeight stretch; a very WIDE one caps the HORIZONTAL
        # letterSpacing widening. Neither overflows.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        for vw, vh, tag in ((6000, 400, "absurdly wide-and-short"),
                            (700, 4000, "absurdly tall-and-narrow")):
            out = _run_fit_harness(html, vw, vh)
            # NEVER a CSS transform (the #678 mouse-breaking mechanism)
            self.assertEqual(out["scaleX"], 1, "%s: no CSS scaleX" % tag)
            self.assertEqual(out["scaleY"], 1, "%s: no CSS scaleY" % tag)
            # lineHeight stretch capped at WT_FILL_MAX_LINE_STRETCH (never grotesque)
            self.assertLessEqual(out["lineHeight"], 1.8 + 1e-6,
                                 "%s: lineHeight must be capped, got %s"
                                 % (tag, out["lineHeight"]))
            self.assertGreaterEqual(out["lineHeight"], 1)   # a stretch, never a shrink
            self.assertGreaterEqual(out["letterSpacing"], 0)  # never negative
            # and the grid never overflows the viewport even at the cap
            self.assertLessEqual(out["gridH"], out["availH"] + 1, "%s: no overflow H" % tag)

    def test_fill_does_not_offset_mouse_selection(self):
        # #678 REGRESSION (RED->GREEN): the #655 fill scaled `.xterm` with a CSS
        # `transform: scale(sx, sy)`. xterm maps a mouse event to a cell via
        # `row = ceil((clientY - rect.top) / cssCellHeight)` where `rect` is
        # getBoundingClientRect() (which REFLECTS the transform) but cssCellHeight
        # is UNSCALED -- so `sy>1` reports a row BELOW the one the user points at,
        # worsening with depth (owner: "selectujem kde je kurzor ale vybera sa mi
        # ovela nizsie"). Proven empirically at the code level (the served ttyd
        # 1.7.4 bundle module 9806 getCoords) and live (real ttyd + real xterm:
        # a fixed pixel pointing at ROW44 hit-tested to line 45/48/54/57 as sy
        # grew 1.06->1.8). The FIX fills via NATIVE cell sizing (lineHeight /
        # letterSpacing grow the REAL cell), never a CSS scale, so scaleX==scaleY==1
        # and the mapping is exact.
        #
        # For a click at the VISUAL centre of grid cell N, xterm reports (0-based)
        # `ceil((N+0.5)*scale) - 1` (transform-origin-center is self-cancelling in
        # rect.top, so only the scale factor matters). It equals N iff scale==1.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")

        def reported(pointed, scale):
            return math.ceil((pointed + 0.5) * scale) - 1

        # realistic dashboard viewports incl. the owner's wide window and a taller
        # one; the fill must keep mouse mapping exact at EVERY row/col, deepest
        # included (where a multiplicative offset is largest).
        for vw, vh, tag in ((1920, 1006, "owner wide window (v0.1.23 + footer)"),
                            (1400, 1000, "tall-ish window (vertical fill)"),
                            (1536, 790, "laptop 125% + footer")):
            out = _run_fit_harness(html, vw, vh)
            rows, cols = out["rows"], out["cols"]
            for n in (0, 5, 15, 30, rows - 1):
                self.assertEqual(
                    reported(n, out["scaleY"]), n,
                    "%s: drag over row %d must select row %d, not %d "
                    "(fill applied vertical scale %.4f -> broken mouse hit-test)"
                    % (tag, n, n, reported(n, out["scaleY"]), out["scaleY"]))
            for c in (0, 40, 100, cols - 1):
                self.assertEqual(
                    reported(c, out["scaleX"]), c,
                    "%s: drag over col %d must select col %d, not %d "
                    "(fill applied horizontal scale %.4f -> broken mouse hit-test)"
                    % (tag, c, c, reported(c, out["scaleX"]), out["scaleX"]))
            # the fill must STILL fill the viewport -- but via native cell sizing
            # (lineHeight/letterSpacing grow the real cell), never a CSS transform.
            self.assertEqual(out["scaleX"], 1,
                             "%s: fill must not CSS-scale the terminal (breaks mouse); "
                             "fill horizontally via letterSpacing" % tag)
            self.assertEqual(out["scaleY"], 1,
                             "%s: fill must not CSS-scale the terminal (breaks mouse); "
                             "fill vertically via lineHeight" % tag)


class TestFullDisplayAndDomains655(unittest.TestCase):
    """#655: (1) the persistent 11px `#hint` micro-bar at the bottom (the owner's
    "nezrozumiteľný mikro text dole") is replaced by a `?`-toggled READABLE help
    panel, hidden by default, freeing the terminal's vertical space; (2) the
    hardcoded stale `work.newlevel.media` domain in the dashboard `<title>` is
    replaced by a dynamic title from `location.hostname` (the domain is
    NXDOMAIN; the live domains are zbynek/david.newlevel.media)."""

    def _inv(self, n=3):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_no_hardcoded_stale_domain_anywhere_in_rendered_dashboard(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn("work.newlevel.media", html,
                         "the stale NXDOMAIN work.newlevel.media must not appear "
                         "in the rendered dashboard (title is dynamic now)")

    def test_dashboard_title_is_set_from_location_hostname(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        # the document title reflects the ACTUAL serving host, client-side, so it
        # is correct on zbynek/david.newlevel.media (and any future domain)
        # without a hardcoded literal baked into the static file.
        self.assertIn("location.hostname", html)
        self.assertRegex(html, r"document\.title\s*=")

    def test_footer_hint_is_readable_not_micro_text(self):
        # #674 removed the ? button and the #hint help OVERLAY (#655). The only
        # persistent hint element is now the single #clip-hint footer line, which
        # must stay READABLE (>= 12px — the #655 owner complaint was the old 11px
        # "nezrozumiteľný mikro text dole") and muted, matching the dashboard chrome.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        m = re.search(r"#clip-hint\s*\{[^}]*\}", html)
        self.assertIsNotNone(m, "#clip-hint CSS rule not found")
        rule = m.group(0)
        self.assertIn("font-size: 12px", rule)     # readable, not the old 11px micro-text
        self.assertIn("#767676", rule)             # muted Campbell grey
        # the old hidden-overlay #hint rule + ? toggle are gone
        self.assertNotIn("#hint", html)


class TestCtrlWProtection(unittest.TestCase):
    """#585 (b): Ctrl+W is readline delete-word in the terminal but the browser
    consumes it as close-tab (a reserved shortcut the page cannot preventDefault
    in a normal window). Three layers: (1) a beforeunload confirm armed WHILE a
    terminal is connected (no silent tab loss); (2) a Fullscreen button that
    requests fullscreen + navigator.keyboard.lock so Chrome delivers Ctrl+W to
    the terminal (feature-detected, honest title when unsupported). (#674 removed
    the #hint help overlay that also documented a PWA fallback; the beforeunload
    confirm + the fullscreen button's own title still cover Ctrl+W.)"""

    def _inv(self, n=3):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_beforeunload_confirm_registered_and_gated(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("'beforeunload'", html)         # the handler is registered
        self.assertIn("function hasLiveTerminal(", html)  # the gate
        self.assertIn("hasLiveTerminal()", html)      # gate CONSULTED in beforeunload
        self.assertIn("preventDefault()", html)       # standard confirm shape
        self.assertIn("returnValue", html)

    def test_fullscreen_button_requests_lock_with_feature_detect(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn('id="fs"', html)                # a Fullscreen control exists
        self.assertIn("requestFullscreen", html)
        self.assertIn("navigator.keyboard", html)     # Keyboard Lock API
        self.assertIn(".lock(", html)
        self.assertIn("KeyW", html)                   # Ctrl+W is the locked key
        self.assertIn("function keyboardLockSupported(", html)  # feature-detect
        # honest fallback: the disabled assignment is inside the ELSE of the
        # feature-detect (a swap that disables when SUPPORTED would fail this).
        self.assertRegex(html, r"if\s*\(\s*keyboardLockSupported\(\)\s*\)")
        self.assertRegex(html, r"\}\s*else\s*\{[^}]*?fsBtn\.disabled = true")
        # secure-context honesty (#585 reviewer 🔵): Keyboard Lock needs HTTPS/
        # localhost, so the gate consults isSecureContext AND the disabled title
        # names HTTPS as the real reason on the plain-HTTP tailnet (not a false
        # "browser unsupported").
        self.assertIn("isSecureContext", html)
        self.assertIn("HTTPS", html)

    def test_ctrlw_additions_preserve_escaping_and_single_pass(self):
        # The #579 injection invariants must survive the Ctrl+W / disconnect JS.
        inv = [{"id": "x", "label": "</script><script>alert(1)</script>",
                "kind": "owner", "local": True, "host": None, "user": None}]
        html = w.render_dashboard_html(inv, ttyd_base="/t")
        self.assertNotIn("</script><script>", html)
        self.assertEqual(html.count('"ttyd_base"'), 1)

    def test_beforeunload_fires_only_on_real_close_never_on_tab_switch(self):
        # #586: with the #585 iframe-navigation (about:blank suspend) gone, a tab
        # switch never unloads any ttyd frame, so ttyd's own beforeunload never
        # fires on a tab click (the "Leave site?" dialog the owner reported). The
        # PAGE's OWN beforeunload is still armed — gated on a live terminal — so a
        # real window/tab close with a live session still confirms. Structural
        # proof here (behavioural proof is the live jsdom run): exactly ONE
        # beforeunload handler, gated on hasLiveTerminal, and activate() performs
        # no navigation (locked by TestAllTabsPreloaded).
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertEqual(html.count("'beforeunload'"), 1)  # exactly one, the page's
        # the single beforeunload consults the live-terminal gate
        self.assertRegex(html, r"addEventListener\('beforeunload',[^)]*\)")
        self.assertIn("if (!hasLiveTerminal()) return;", html)


class TestClipboardBridge(unittest.TestCase):
    """#671: mouse select/copy in the webterm. ttyd 1.7.4's bundled xterm
    frontend registers NO OSC 52 handler (empirically verified: a grep of the
    served bundle finds registerOscHandler for 0/1/2/4/8/10/11/12/104/110/111/
    112/1337 but NOT 52), so a tmux copy-mode mouse drag (which under
    `set-clipboard external` emits OSC 52) never reaches the browser clipboard --
    the owner's reported symptom. The gateway-injected JS registers an OSC 52
    handler on xterm's OWN parser API -> navigator.clipboard.writeText via the
    same-origin window.term bridge, PLUS a copy-on-select mirror for native
    xterm selections. Runtime behaviour was verified with Playwright against a
    real ttyd replica AND a same-origin iframe harness; these tests lock the
    injected-JS SHAPE (the repo has no browser test runner)."""

    def _inv(self, n=2):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_osc52_handler_registered_and_writes_clipboard(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("function attachClipboard(", html)
        # OSC 52 handler on xterm's OWN parser API (not a bundled addon we lack)
        self.assertIn("registerOscHandler(52", html)
        # decodes base64 and mirrors the payload to the browser clipboard
        self.assertIn("navigator.clipboard", html)
        self.assertIn("writeText", html)
        self.assertIn("atob(", html)

    def test_copy_on_select_mirrors_native_selection(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        # native xterm selection (Shift+drag) is mirrored too, daemon-agnostic
        # (independent of ttyd's own deprecated execCommand copy-on-select).
        self.assertIn("onSelectionChange(", html)
        self.assertIn("getSelection()", html)

    def test_clipboard_bridge_is_guarded_and_idempotent(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        # idempotent per terminal (never double-registers)
        self.assertIn("__wtClip", html)
        m2 = re.search(r"function attachClipboard\([^)]*\)\s*\{.*?\n\}", html, re.DOTALL)
        self.assertIsNotNone(m2, "attachClipboard() not found")
        body = m2.group(0)
        self.assertIn("navigator", body)         # clipboard availability consulted
        self.assertIn("try", body)               # guarded against a throw
        # an OSC 52 read-request (52;c;?) must NOT attempt a decode/write
        self.assertIn("'?'", body)

    def test_clipboard_bridge_wired_into_grid_poll(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        # attached where the term first exists (same place as themeTerminal)
        self.assertIn("attachClipboard(win)", html)

    def test_footer_hint_states_paste_combo(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        # #671 + #674: the combos live in an unobtrusive footer line, NOT a ?
        # button (the ? button is removed by #674).
        self.assertIn('id="clip-hint"', html)
        foot = next(ln for ln in html.splitlines() if 'id="clip-hint"' in ln)
        self.assertIn("Ctrl+Shift+V", foot)          # the working paste combo
        self.assertIn("Ctrl+V", foot)                # names the NON-working one ("nie Ctrl+V")
        self.assertIn("myš", foot.lower())           # mouse-select copies

    def test_footer_hint_honest_over_http_feature_detect(self):
        # #671 review: navigator.clipboard needs a secure context; over the
        # plain-HTTP tailnet the copy bridge is inert, so the footer is
        # feature-detected and rewritten to an honest message when clipboard is
        # unavailable (mirrors the #585 Ctrl+W isSecureContext honesty) — never a
        # false "mouse copies" promise. Paste (Ctrl+Shift+V) still works there.
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("getElementById('clip-hint')", html)      # footer is feature-detected
        self.assertIn("window.isSecureContext", html)           # secure-context gate
        # the honest fallback names the real reason, not a false browser-unsupported
        self.assertIn("kopírovanie myšou vyžaduje HTTPS", html)


class TestTopBarOnlyFullscreen(unittest.TestCase):
    """#674 (owner directive 2026-08-24, verbatim "nechaj tam len fullscreen"):
    the top-left controls are reduced to ONLY the fullscreen button -- the
    prev/next arrows and the ? help button are removed, along with the
    now-unreachable #hint help overlay and the dead cycle() helper. Tab
    switching still works via tab clicks + Ctrl+Alt+1..9 (onHotkey)."""

    def _inv(self, n=4):
        return [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
                 "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
                for i in range(1, n + 1)]

    def test_nav_keeps_only_fullscreen(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        nav = next(ln for ln in html.splitlines() if 'id="nav"' in ln)
        self.assertIn('id="fs"', nav)                 # fullscreen stays
        self.assertNotIn("data-cyc", nav)             # prev/next arrows removed
        self.assertNotIn('id="help"', nav)            # ? button removed
        self.assertNotIn("&#9664;", nav)              # left-arrow glyph gone
        self.assertNotIn("&#9654;", nav)              # right-arrow glyph gone

    def test_cycle_helper_and_wiring_removed(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn("function cycle(", html)     # dead helper removed
        self.assertNotIn(".cyc[data-cyc]", html)      # its click-wiring removed

    def test_help_overlay_and_toggle_removed(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertNotIn('id="hint"', html)           # the ?-opened overlay is gone
        self.assertNotIn('id="help"', html)
        self.assertNotIn("getElementById('help')", html)

    def test_tab_switching_still_works(self):
        # removing the arrows must NOT break switching: tab clicks + Ctrl+Alt+1..9
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn("function activate(", html)
        self.assertIn("() => activate(+t.dataset.idx)", html)   # tab click still wired
        self.assertIn("e.key >= '1'", html)                     # Ctrl+Alt+1..9 handler stays

    def test_fullscreen_control_preserved(self):
        html = w.render_dashboard_html(self._inv(), ttyd_base="/t")
        self.assertIn('id="fs"', html)
        self.assertIn("requestFullscreen", html)


if __name__ == "__main__":
    unittest.main()
