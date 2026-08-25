"""#681 — lock the webterm ttyd/gateway spawn SECURITY invariant.

A replica/lane ttyd, and the same-origin gateway, binds LOOPBACK (127.0.0.1) or a
mode-0700 UNIX-domain socket (#663) — NEVER a wildcard / interface-any bind
(`0.0.0.0`, `::`). A wildcard bind exposes an unauthenticated, writable terminal on
every interface, including the tailnet: the #661 harness lesson wrongly told a review
agent to run `ttyd -i 0.0.0.0 + navigate the tailscale IP` (claiming Playwright MCP
could not reach 127.0.0.1), and the agent literally executed it (#671; the orphan was
reaped by #672). Worker EMPIRICAL finding (#681 VALIDATED comment, dev1): Playwright
MCP DOES reach a loopback ttyd on dev1 (`browser_navigate http://127.0.0.1:<port>/`
rendered the terminal), so the "needs 0.0.0.0" rationale is false; and every rendered
spawn argv already binds 127.0.0.1 or `$SOCK`. This file makes the regression
un-shippable and adds a fail-closed guard at the render chokepoint.

Nothing here binds a live socket — the assertions are over the RENDERED argv / systemd
units built by the REAL helpers, so a test never actually binds 0.0.0.0.
"""
import os

# Hygiene (contract): never inherit an outer tmux server/pane. These render helpers
# do not touch tmux, but keep the suite deterministic across environments.
os.environ.pop("TMUX", None)
os.environ.pop("TMUX_PANE", None)

import re  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_david as dvd  # noqa: E402
import cli_webterm_marek as mrk  # noqa: E402


# An interface-any bind, in either the `ttyd -i` or the gateway `--bind` argv form.
# `0.0.0.0` is checked as a bare token (a dotted-quad guard avoids matching e.g.
# `10.0.0.0/8` were it ever to appear); `::` only in an explicit `-i`/`--bind` slot
# so a legitimate `%t/...` path or a `::`-in-prose never false-positives.
_WILDCARD_RE = re.compile(
    r"(?<![\d.])0\.0\.0\.0(?![\d.])"
    r"|-i\s+::(?:\s|\"|$)"
    r"|--bind\s+::(?:\s|\"|$)"
)


def _has_wildcard_bind(text):
    return bool(_WILDCARD_RE.search(text))


def _all_rendered_spawn_artifacts():
    """Every ttyd/gateway spawn argv + systemd unit this repo can emit, built via the
    REAL render helpers (not re-derived — a tautology-free scan of the actual output).
    Returns a list of (label, text) pairs covering all three lanes and both modes."""
    return [
        ("owner password launch",
         w.render_webterm_launch_script()),
        ("owner Access socket launch",
         w.render_webterm_launch_script(ttyd_socket_basename=w.WEBTERM_TTYD_SOCK_BASENAME)),
        ("lane port-override launch",
         w.render_webterm_launch_script(inventory_path="/x/inv.json",
                                        ttyd_port=w.WEBTERM_TTYD_PORT)),
        ("owner password gateway unit",
         w._render_webterm_gateway_unit(w.WEBTERM_TTYD_BIND, access_mode=False)),
        ("owner Access gateway unit",
         w._render_webterm_gateway_unit(w.WEBTERM_TTYD_BIND, access_mode=True)),
        ("david gateway unit", dvd.render_david_gateway_unit()),
        ("marek gateway unit", mrk.render_marek_gateway_unit()),
        ("david ttyd unit", dvd.render_david_ttyd_unit()),
        ("marek ttyd unit", mrk.render_marek_ttyd_unit()),
    ]


class TestNoWildcardBind681(unittest.TestCase):
    """The regression LOCK: no ttyd/gateway spawn path ever emits a wildcard bind."""

    def test_no_spawn_path_emits_a_wildcard_bind(self):
        for label, text in _all_rendered_spawn_artifacts():
            self.assertFalse(
                _has_wildcard_bind(text),
                "%s must bind loopback (127.0.0.1) or a UNIX socket, never a "
                "wildcard/interface-any bind (0.0.0.0 / ::) — #681" % label)

    def test_scan_catches_a_seeded_wildcard(self):
        # Positive control: the scan MUST flag a deliberately-wrong argv, so the
        # lock above is proven able to fail (not a tautology that always passes).
        self.assertTrue(_has_wildcard_bind('exec ttyd -p 7682 -i 0.0.0.0 -b /t -a -W foo'))
        self.assertTrue(_has_wildcard_bind('cli_webterm_gateway.py --bind 0.0.0.0 --port 8080'))
        self.assertTrue(_has_wildcard_bind('exec ttyd -i :: -b /t -a -W foo'))
        # ...and NOT flag the safe binds.
        self.assertFalse(_has_wildcard_bind('exec ttyd -p 7682 -i 127.0.0.1 -b /t -a -W foo'))
        self.assertFalse(_has_wildcard_bind('exec ttyd -i "$SOCK" -b /t -a -W foo'))

    def test_password_launch_binds_loopback(self):
        s = w.render_webterm_launch_script()
        self.assertIn("-i 127.0.0.1", s)

    def test_access_launch_binds_unix_socket_not_tcp(self):
        s = w.render_webterm_launch_script(ttyd_socket_basename=w.WEBTERM_TTYD_SOCK_BASENAME)
        self.assertIn('-i "$SOCK"', s)
        self.assertNotIn("-i 127.0.0.1", s)


class TestWildcardBindGuard681(unittest.TestCase):
    """The fail-closed GUARD: rendering a ttyd/gateway with a wildcard bind must
    RAISE, so a future edit that sets `WEBTERM_TTYD_BIND = "0.0.0.0"` or passes an
    interface-any `bind_ip` can never silently ship a live exposure. RED before the
    guard (`_reject_wildcard_bind`) exists; GREEN once it is wired at the two TCP
    render chokepoints."""

    def test_guard_rejects_wildcard_and_accepts_loopback(self):
        self.assertEqual(w._reject_wildcard_bind("127.0.0.1", "x"), "127.0.0.1")
        for bad in ("0.0.0.0", "::", "", "*", None):
            with self.assertRaises(ValueError):
                w._reject_wildcard_bind(bad, "x")

    def test_render_launch_script_refuses_wildcard_ttyd_bind(self):
        orig = w.WEBTERM_TTYD_BIND
        try:
            w.WEBTERM_TTYD_BIND = "0.0.0.0"
            with self.assertRaises(ValueError):
                w.render_webterm_launch_script()
        finally:
            w.WEBTERM_TTYD_BIND = orig

    def test_render_gateway_unit_refuses_wildcard_bind(self):
        with self.assertRaises(ValueError):
            w._render_webterm_gateway_unit("0.0.0.0", access_mode=False)


if __name__ == "__main__":
    unittest.main()
