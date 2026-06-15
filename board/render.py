"""Pure HTML rendering for the Autopilot Board.

These are PURE string functions — no server, no DB, no I/O — so they're unit
testable in isolation and the server can't accidentally bypass the escaping.

DESIGN: "Status-first dark cards" — every run is a card led by a bold,
phase-COLOURED header bar + a lifecycle stepper, a readable goal/approach/result
block, and an 8-cell REVIEW-GATE scorecard (the whole point: a human sees at a
glance whether every mandated review passed before merge). Alarms render as a red
banner and make the card glow. asking-user runs (blocked on a human) sort first.

SECURITY (the board renders attacker-influenced text — titles/goals/results come
from worker reports on dev1 AND dev2):
  * EVERY interpolated value goes through `_e()` (html.escape, quote=True). The
    single chokepoint is `_e`; the templates below NEVER drop a raw value into
    markup. Phase is mapped to a FIXED css class (never interpolated into one).
  * PR URLs reach an href ONLY after `_gh_href` validates the github https prefix.
  * The page ships under a strict CSP (default-src 'none'; style-src 'self'
    'unsafe-inline'): no inline/external script can run. We keep the markup
    script-free and use a single inline <style> block.

stdlib only.
"""
import html

from board import AUTO_REFRESH_S, ALL_PHASES


# --------------------------------------------------------------------------- #
# escaping chokepoint
# --------------------------------------------------------------------------- #
def _e(v):
    """Escape ANY value for safe HTML text/attribute interpolation.

    quote=True so a value inside a double-quoted attribute can't break out.
    None -> "" (a missing field renders blank, never the string 'None')."""
    if v is None:
        return ""
    return html.escape(str(v), quote=True)


def _gh_href(url):
    """Return an escaped href ONLY for a GitHub https URL, else None."""
    if isinstance(url, str) and url.startswith("https://github.com/"):
        return _e(url)
    return None


# --------------------------------------------------------------------------- #
# phase / gate metadata (FIXED maps — never derived from user input)
# --------------------------------------------------------------------------- #
# The linear lifecycle shown in the stepper. asking-user / stopped /
# obsolete-closed are off-track (handled by the phasebar, not the stepper).
LIFECYCLE = ("validating", "version-bump", "implementing", "RED", "GREEN",
             "CI", "review", "merge", "deploy", "done")
_LC_RANK = {p: i for i, p in enumerate(LIFECYCLE)}

_STEP_LABEL = {
    "validating": "validate", "version-bump": "v-bump",
    "implementing": "implement", "RED": "RED", "GREEN": "GREEN", "CI": "CI",
    "review": "review", "merge": "merge", "deploy": "deploy", "done": "done",
}

_PHASE_GLYPH = {
    "validating": "◌", "version-bump": "⬆", "implementing": "●", "RED": "●",
    "GREEN": "●", "CI": "◍", "review": "◐", "merge": "◆", "deploy": "▲",
    "done": "✓", "asking-user": "?", "stopped": "■", "obsolete-closed": "⊘",
}

# A lifecycle step is marked FAIL when its mapped gate check failed.
_STEP_GATE = {"validating": "ticket_validated", "CI": "ci", "review": "review",
              "merge": "mergeable", "deploy": "deploy_verified"}

# Human-readable gate labels (the board's REQUIRED_GATES order is authoritative).
_GATE_LABEL = {
    "ticket_validated": "ticket valid", "ci": "CI", "mergeable": "mergeable",
    "plan_check": "plan-check", "review": "review",
    "requesting_code_review": "req-code-review", "regression": "regression",
    "deploy_verified": "deploy verified",
}

_STATE_GLYPH = {"ok": "✓", "fail": "✗"}   # default ◐ for pending/unknown


def _phase_class(phase):
    """Map a phase to its FIXED css class. Unknown/None -> validating default,
    so an attacker-supplied phase can NEVER inject a class name."""
    return "ph-" + phase if phase in ALL_PHASES else "ph-validating"


# --------------------------------------------------------------------------- #
# page chrome + style
# --------------------------------------------------------------------------- #
_STYLE = """
  :root{
    --bg:#0a0e1a; --bg2:#0e1422; --panel:#121a2c; --panel2:#161f33;
    --ink:#eef3ff; --ink-dim:#aab6d4; --ink-faint:#7886a8;
    --line:#222d47; --line-soft:#1b2538;
    --shadow:0 1px 0 rgba(255,255,255,.03), 0 14px 34px -16px rgba(0,0,0,.85);
    --slate:#7c8aa8;  --slate-bg:#2a3450; --blue:#3f86f5;   --blue-bg:#16294d;
    --red:#ff5564;    --red-bg:#3a1620;   --green:#27d07a;  --green-bg:#0e3326;
    --purple:#a877ff; --purple-bg:#281a47;--cyan:#2dd4ee;   --cyan-bg:#0c333f;
    --violet:#8b5cf6; --violet-bg:#23184a;--teal:#22c7b8;   --teal-bg:#0b3433;
    --emerald:#2ee68f;--emerald-bg:#0d3a29;--amber:#ffb020; --amber-bg:#3d2a08;
    --grey:#69748f;   --grey-bg:#222a3c;
    --ok:#27d07a; --fail:#ff5564; --pending:#ffb020;
  }
  *{box-sizing:border-box} html,body{margin:0}
  body{
    background:
      radial-gradient(1200px 600px at 80% -10%, #14203a 0%, transparent 60%),
      radial-gradient(900px 500px at 0% 0%, #161033 0%, transparent 55%),
      var(--bg);
    color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    font-size:15px; line-height:1.45; -webkit-font-smoothing:antialiased;
    padding:26px 26px 60px;
  }
  a{color:inherit;text-decoration:none}
  .wrap{max-width:1480px;margin:0 auto}

  .topbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;
    padding:18px 22px;border-radius:18px;
    background:linear-gradient(180deg,var(--panel2),var(--panel));
    border:1px solid var(--line);box-shadow:var(--shadow);margin-bottom:22px}
  .brand{display:flex;align-items:center;gap:14px;margin-right:auto}
  .brand .dot{width:14px;height:14px;border-radius:50%;background:var(--emerald);
    box-shadow:0 0 0 4px rgba(46,230,143,.18),0 0 18px rgba(46,230,143,.55)}
  .brand h1{font-size:24px;font-weight:800;letter-spacing:-.4px;margin:0;
    background:linear-gradient(90deg,#fff,#9fc1ff);
    -webkit-background-clip:text;background-clip:text;color:transparent}
  .brand .sub{font-size:12.5px;color:var(--ink-faint);font-weight:600;letter-spacing:.3px}
  .health{display:flex;gap:10px;flex-wrap:wrap}
  .hpill{display:flex;flex-direction:column;gap:1px;padding:9px 15px;
    border-radius:12px;background:var(--bg2);border:1px solid var(--line-soft);min-width:120px}
  .hpill .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--ink-faint);font-weight:700}
  .hpill .v{font-size:16px;font-weight:800;color:var(--ink)}
  .hpill .v .accent{color:var(--emerald)}
  .hpill.live .v .accent{color:var(--blue)}
  .hpill.stale{border-color:#7a2531;background:var(--red-bg)}
  .hpill.stale .k{color:#ffbcc2} .hpill.stale .v{color:#ffbcc2}

  .upnext{border-radius:16px;border:1px solid var(--line);
    background:linear-gradient(180deg,var(--panel),var(--bg2));
    box-shadow:var(--shadow);padding:16px 20px 18px;margin-bottom:26px}
  .upnext-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
  .upnext-head .ic{font-size:15px;color:var(--amber)}
  .upnext-head h2{font-size:14px;text-transform:uppercase;letter-spacing:1.4px;font-weight:800;margin:0;color:var(--ink-dim)}
  .upnext-head .count{font-size:11px;font-weight:800;color:var(--amber);
    background:var(--amber-bg);border:1px solid #5a410f;padding:2px 9px;border-radius:999px}
  .queue-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
  .qrepo{background:var(--bg2);border:1px solid var(--line-soft);border-radius:13px;padding:12px 14px}
  .qrepo-head{display:flex;align-items:center;gap:8px;margin-bottom:9px}
  .qrepo-head .owner{color:var(--ink-faint);font-weight:600;font-size:12.5px}
  .qrepo-head .name{color:#bcd2ff;font-weight:800;font-size:13.5px}
  .qrepo-head .pinico{color:var(--ink-faint);font-size:12px}
  .qitem{display:flex;gap:9px;align-items:baseline;padding:6px 8px;border-radius:8px}
  .qitem+.qitem{margin-top:2px}
  .qitem .num{font-variant-numeric:tabular-nums;font-weight:800;font-size:13px;color:var(--amber);min-width:46px}
  .qitem .txt{color:var(--ink-dim);font-size:13.5px}

  .sec-head{display:flex;align-items:center;gap:12px;margin:6px 0 16px}
  .sec-head h2{font-size:16px;font-weight:800;letter-spacing:.2px;margin:0}
  .sec-head .rail{flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
  .sec-head .badge{font-size:11px;font-weight:800;letter-spacing:.5px;padding:3px 11px;border-radius:999px}
  .sec-head .badge.live{color:#bfe0ff;background:var(--blue-bg);border:1px solid #214172}
  .sec-head .badge.done{color:#b6f0d4;background:var(--green-bg);border:1px solid #1c5840}

  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:20px;margin-bottom:34px}
  .card{position:relative;border-radius:16px;overflow:hidden;background:var(--panel);
    border:1px solid var(--line);box-shadow:var(--shadow);display:flex;flex-direction:column}
  .card.alarm{border-color:#5e1d27;
    box-shadow:0 0 0 1px rgba(255,85,100,.35),0 0 40px -8px rgba(255,85,100,.4),0 18px 40px -18px rgba(0,0,0,.9)}

  .alarmbar{display:flex;align-items:center;gap:10px;padding:11px 16px;
    background:linear-gradient(90deg,#ff3b4e,#c41d2f);color:#fff;font-weight:800;
    font-size:13px;letter-spacing:.2px;border-bottom:1px solid rgba(0,0,0,.3)}
  .alarmbar+.alarmbar{border-top:1px solid rgba(0,0,0,.25)}
  .alarmbar .warn{font-size:16px;filter:drop-shadow(0 0 3px rgba(0,0,0,.5))}
  .alarmbar .label{background:rgba(0,0,0,.28);padding:2px 8px;border-radius:6px;font-size:11px;letter-spacing:1px}

  .phasebar{display:flex;align-items:center;gap:12px;padding:14px 18px;border-bottom:1px solid rgba(0,0,0,.35)}
  .phasebar .glyph{width:34px;height:34px;border-radius:9px;display:flex;align-items:center;
    justify-content:center;font-size:17px;font-weight:900;background:rgba(0,0,0,.22)}
  .phasebar .ptext{display:flex;flex-direction:column;line-height:1.15}
  .phasebar .plabel{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;font-weight:800;opacity:.72}
  .phasebar .pname{font-size:21px;font-weight:900;letter-spacing:-.3px;text-transform:uppercase}
  .phasebar .machine{margin-left:auto;text-align:right;font-size:11px;font-weight:700}
  .phasebar .machine .mk{display:block;font-size:9.5px;letter-spacing:1px;text-transform:uppercase;opacity:.65;font-weight:800}
  .phasebar .machine .mv{font-size:13px;color:#fff;font-variant-numeric:tabular-nums}
  .needs-human{margin-left:10px;align-self:center;font-size:10px;font-weight:900;letter-spacing:1px;
    background:#ffb020;color:#2a1c00;padding:3px 9px;border-radius:6px;box-shadow:0 0 16px -2px rgba(255,176,32,.7)}

  .ph-validating .phasebar,.ph-version-bump .phasebar{background:linear-gradient(100deg,var(--slate-bg),#1c2540);color:#cfd8ec}
  .ph-validating .phasebar .glyph,.ph-version-bump .phasebar .glyph{color:var(--slate)}
  .ph-implementing .phasebar{background:linear-gradient(100deg,var(--blue-bg),#102036);color:#cfe2ff}
  .ph-implementing .phasebar .glyph{color:var(--blue)}
  .ph-RED .phasebar{background:linear-gradient(100deg,var(--red-bg),#2a0f16);color:#ffd2d7}
  .ph-RED .phasebar .glyph{color:var(--red)}
  .ph-GREEN .phasebar{background:linear-gradient(100deg,var(--green-bg),#082019);color:#c5f5dd}
  .ph-GREEN .phasebar .glyph{color:var(--green)}
  .ph-CI .phasebar{background:linear-gradient(100deg,var(--purple-bg),#160f2b);color:#e0d2ff}
  .ph-CI .phasebar .glyph{color:var(--purple)}
  .ph-review .phasebar{background:linear-gradient(100deg,var(--cyan-bg),#08222a);color:#c5f3ff}
  .ph-review .phasebar .glyph{color:var(--cyan)}
  .ph-merge .phasebar{background:linear-gradient(100deg,var(--violet-bg),#150e2e);color:#dccffe}
  .ph-merge .phasebar .glyph{color:var(--violet)}
  .ph-deploy .phasebar{background:linear-gradient(100deg,var(--teal-bg),#072221);color:#bff3ee}
  .ph-deploy .phasebar .glyph{color:var(--teal)}
  .ph-done .phasebar{background:linear-gradient(100deg,var(--emerald-bg),#082319);color:#c2f7dc}
  .ph-done .phasebar .glyph{color:var(--emerald)}
  .ph-asking-user .phasebar{background:linear-gradient(100deg,#4a3208,#2e2206);color:#ffe6b3;
    box-shadow:inset 0 0 0 1px rgba(255,176,32,.4),0 0 26px -4px rgba(255,176,32,.45)}
  .ph-asking-user .phasebar .glyph{color:var(--amber);box-shadow:0 0 0 2px rgba(255,176,32,.35)}
  .ph-stopped .phasebar,.ph-obsolete-closed .phasebar{background:linear-gradient(100deg,var(--grey-bg),#171d2a);color:#aab4c8}
  .ph-stopped .phasebar .glyph,.ph-obsolete-closed .phasebar .glyph{color:var(--grey)}

  .stepper{display:flex;align-items:center;padding:14px 16px 12px;background:var(--bg2);
    border-bottom:1px solid var(--line-soft);overflow-x:auto}
  .step{display:flex;flex-direction:column;align-items:center;gap:5px;flex:1 0 auto;min-width:0;position:relative}
  .step .node{width:18px;height:18px;border-radius:50%;background:var(--line);border:2px solid var(--line);
    display:flex;align-items:center;justify-content:center;font-size:9px;color:transparent;font-weight:900;z-index:2}
  .step .lbl{font-size:8.5px;letter-spacing:.3px;text-transform:uppercase;font-weight:700;color:var(--ink-faint);white-space:nowrap}
  .step::before{content:"";position:absolute;top:9px;left:-50%;width:100%;height:2px;background:var(--line);z-index:1}
  .step:first-child::before{display:none}
  .step.done .node{background:var(--emerald);border-color:var(--emerald);color:#06281a}
  .step.done .node::after{content:"\\2713"}
  .step.done::before{background:var(--emerald)}
  .step.done .lbl{color:var(--ink-dim)}
  .step.current .node{background:#fff;border-color:#fff;box-shadow:0 0 0 4px rgba(255,255,255,.14),0 0 14px rgba(255,255,255,.5)}
  .step.current .lbl{color:#fff;font-weight:900}
  .step.fail .node{background:var(--fail);border-color:var(--fail);color:#2a0a0d}
  .step.fail .node::after{content:"\\2717"}
  .step.fail::before{background:var(--fail)}
  .step.fail .lbl{color:#ffb3ba}

  .body{padding:16px 18px 6px;flex:1}
  .repoline{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
  .repoline .repo{font-size:13px;font-weight:700;color:var(--ink-faint);background:var(--bg2);
    border:1px solid var(--line-soft);padding:3px 9px;border-radius:7px}
  .repoline .repo b{color:#bcd2ff;font-weight:800}
  .repoline .issue{font-size:13px;font-weight:900;color:var(--amber);font-variant-numeric:tabular-nums}
  .repoline .bugtag{font-size:9.5px;font-weight:900;letter-spacing:.6px;text-transform:uppercase;
    color:#ffd2d7;background:var(--red-bg);border:1px solid #5e1d27;padding:2px 7px;border-radius:6px}
  .title{font-size:17px;font-weight:800;letter-spacing:-.2px;margin:2px 0 12px;color:var(--ink)}
  .meta{display:grid;gap:9px;margin-bottom:14px}
  .field{display:grid;grid-template-columns:74px 1fr;gap:10px;align-items:start}
  .field .fk{font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:800;color:var(--ink-faint);padding-top:2px}
  .field .fv{font-size:13.5px;color:var(--ink-dim);line-height:1.5;overflow-wrap:anywhere}
  .field.result .fv{color:var(--ink)}
  .field.danger .fv{color:#ffbcc2}

  .gate{margin:4px 0 16px;border-top:1px solid var(--line-soft);padding-top:13px}
  .gate-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .gate-head .gt{font-size:10.5px;text-transform:uppercase;letter-spacing:1.4px;font-weight:800;color:var(--ink-dim)}
  .gate-summary{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;font-weight:800}
  .gate-summary .frac{font-variant-numeric:tabular-nums;font-size:14px}
  .gate-summary .bar{width:74px;height:7px;border-radius:99px;background:var(--line);overflow:hidden;display:flex}
  .gate-summary .bar i{display:block;height:100%}
  .gate-summary .bar i.ok{background:var(--ok)}
  .gate-summary .bar i.fail{background:var(--fail)}
  .gate-summary .bar i.pend{background:var(--pending);opacity:.45}
  .scorecard{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  .cell{display:flex;flex-direction:column;gap:4px;align-items:flex-start;padding:9px 10px;border-radius:10px;
    border:1px solid var(--line-soft);background:var(--bg2);position:relative;min-height:54px}
  .cell .cstate{display:flex;align-items:center;gap:6px;font-size:14px;font-weight:900}
  .cell .clbl{font-size:10px;letter-spacing:.2px;font-weight:700;color:var(--ink-faint);line-height:1.2}
  .cell.ok{background:linear-gradient(180deg,rgba(39,208,122,.14),rgba(39,208,122,.04));border-color:#1c5e42}
  .cell.ok .cstate{color:var(--ok)} .cell.ok .clbl{color:#a7e9c8}
  .cell.fail{background:linear-gradient(180deg,rgba(255,85,100,.2),rgba(255,85,100,.06));border-color:#7a2531;
    box-shadow:0 0 0 1px rgba(255,85,100,.25),0 0 20px -6px rgba(255,85,100,.6)}
  .cell.fail .cstate{color:var(--fail)} .cell.fail .clbl{color:#ffbcc2}
  .cell.pending{background:linear-gradient(180deg,rgba(255,176,32,.1),rgba(255,176,32,.02));border-color:#574012}
  .cell.pending .cstate{color:var(--pending)} .cell.pending .clbl{color:#e9cd96}

  .cardfoot{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 18px;
    border-top:1px solid var(--line-soft);background:var(--bg2)}
  .status-chip{font-size:11px;font-weight:800;letter-spacing:.3px;padding:4px 10px;border-radius:7px;
    border:1px solid var(--line);color:var(--ink-dim);background:var(--panel)}
  .status-chip.bad{color:#ffbcc2;background:var(--red-bg);border-color:#7a2531}
  .status-chip.good{color:#b6f0d4;background:var(--green-bg);border-color:#1c5840}
  .prlink{margin-left:auto;display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:800;
    color:#bcd2ff;background:var(--panel);border:1px solid var(--line);padding:6px 12px;border-radius:9px}
  .prlink .arrow{opacity:.7}
  .prlink.danger{color:#ffbcc2;border-color:#7a2531;background:var(--red-bg)}

  .empty{margin:40px auto;max-width:520px;text-align:center;color:var(--ink-faint);
    background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:40px 24px;font-size:15px}
  .empty .big{font-size:30px;display:block;margin-bottom:10px;opacity:.5}

  .pagefoot{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:14px;padding-top:18px;
    border-top:1px solid var(--line-soft);color:var(--ink-faint);font-size:12px}
  .pagefoot .ver{font-weight:800;color:var(--ink-dim);font-variant-numeric:tabular-nums;
    background:var(--bg2);border:1px solid var(--line-soft);padding:3px 10px;border-radius:7px}
  .pagefoot .legend{display:flex;gap:14px;flex-wrap:wrap;margin-left:auto}
  .pagefoot .legend span{display:inline-flex;align-items:center;gap:6px;font-weight:600}
  .pagefoot .legend .sw{width:11px;height:11px;border-radius:3px;display:inline-block}

  /* ticket detail */
  .detail{background:var(--panel);border:1px solid var(--line);border-radius:16px;
    box-shadow:var(--shadow);padding:6px 0 0;margin-bottom:22px;overflow:hidden}
  .detail .body{padding:18px 20px}
  table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0 16px}
  th,td{border:1px solid var(--line-soft);padding:7px 10px;text-align:left;vertical-align:top}
  th{background:var(--bg2);color:var(--ink-dim);text-transform:uppercase;font-size:10.5px;letter-spacing:.8px}
  td{color:var(--ink-dim)}
  h2.dh{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--ink-dim);margin:18px 0 8px}
  .back{display:inline-block;margin:8px 0 6px;color:#bcd2ff;font-weight:700}
"""


def _page(title, body):
    """Wrap a body fragment in the full HTML document. <meta refresh> auto-
    refreshes every AUTO_REFRESH_S. No <script> anywhere (CSP forbids it)."""
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta http-equiv=\"refresh\" content=\"{int(AUTO_REFRESH_S)}\">"
        f"<title>{_e(title)}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body><div class=\"wrap\">"
        + body +
        "</div></body></html>"
    )


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #
def _topbar(health):
    health = health or {}
    pills = []
    up = health.get("started_at_str")
    if up:
        pills.append(f"<div class=\"hpill\"><span class=\"k\">Up since</span>"
                     f"<span class=\"v\"><span class=\"accent\">●</span> {_e(up)}</span></div>")
    last = health.get("last_report", health.get("last_report_str")) or "never"
    pills.append(f"<div class=\"hpill\"><span class=\"k\">Last report</span>"
                 f"<span class=\"v\">{_e(last)}</span></div>")
    if "runs_last_hour" in health:
        pills.append(f"<div class=\"hpill live\"><span class=\"k\">Runs · last hr</span>"
                     f"<span class=\"v\"><span class=\"accent\">{_e(health['runs_last_hour'])}</span></span></div>")
    if health.get("gh_stale"):
        since = health.get("gh_stale_since", "")
        pills.append(f"<div class=\"hpill stale\"><span class=\"k\">gh signals</span>"
                     f"<span class=\"v\">STALE{(' · ' + _e(since)) if since else ''}</span></div>")
    return (
        "<header class=\"topbar\">"
        "<div class=\"brand\"><span class=\"dot\"></span><div>"
        "<h1>Autopilot Board</h1>"
        "<div class=\"sub\">autonomous coding agents · live review-gate audit</div>"
        "</div></div>"
        f"<div class=\"health\">{''.join(pills)}</div>"
        "</header>"
    )


def _queue_section(queue):
    """'Up next — N queued' section, grouped by repo in pick-order."""
    if not queue:
        return ""
    n = len(queue)
    groups = {}
    for item in queue:
        repo = item["repo"] if "repo" in item else None
        groups.setdefault(repo, []).append(item)
    blocks = []
    for repo, items in groups.items():
        owner, _, name = (repo or "").partition("/")
        if name:
            head = f"<span class=\"owner\">{_e(owner)}/</span><span class=\"name\">{_e(name)}</span>"
        else:
            head = f"<span class=\"name\">{_e(repo or 'unknown')}</span>"
        rows = [f"<div class=\"qrepo-head\"><span class=\"pinico\">◆</span>{head}</div>"]
        for item in items:
            rows.append(
                f"<div class=\"qitem\"><span class=\"num\">#{_e(item['issue'])}</span>"
                f"<span class=\"txt\">{_e(item['title'])}</span></div>")
        blocks.append(f"<div class=\"qrepo\">{''.join(rows)}</div>")
    return (
        "<section class=\"upnext\"><div class=\"upnext-head\">"
        "<span class=\"ic\">▸</span><h2>Up next</h2>"
        f"<span class=\"count\">{_e(n)} queued</span></div>"
        f"<div class=\"queue-grid\">{''.join(blocks)}</div></section>"
    )


def _alarmbar(alarms):
    if not alarms:
        return ""
    out = []
    for a in alarms:
        a = str(a)
        # split a leading CODE_TOKEN — rest into a label chip + description
        label, sep, rest = a.partition(" — ")
        if sep:
            out.append(f"<div class=\"alarmbar\"><span class=\"warn\">⚠</span>"
                       f"<span class=\"label\">{_e(label)}</span><span>{_e(rest)}</span></div>")
        else:
            out.append(f"<div class=\"alarmbar\"><span class=\"warn\">⚠</span><span>{_e(a)}</span></div>")
    return "".join(out)


def _phasebar(phase, machine):
    glyph = _PHASE_GLYPH.get(phase, "●")
    needs = ("<span class=\"needs-human\">NEEDS HUMAN</span>"
             if phase == "asking-user" else "")
    mach = (f"<span class=\"machine\"><span class=\"mk\">Machine</span>"
            f"<span class=\"mv\">{_e(machine)}</span></span>") if machine else ""
    return (
        "<div class=\"phasebar\">"
        f"<span class=\"glyph\">{_e(glyph)}</span>"
        "<span class=\"ptext\"><span class=\"plabel\">Phase</span>"
        f"<span class=\"pname\">{_e(str(phase).upper()) if phase else '—'}</span></span>"
        f"{needs}{mach}</div>"
    )


def _stepper(phase, gate):
    gate = gate or {}
    cur = _LC_RANK.get(phase)
    steps = []
    for i, ph in enumerate(LIFECYCLE):
        cls = []
        gcheck = _STEP_GATE.get(ph)
        if gcheck and gate.get(gcheck) == "fail":
            cls.append("fail")
        elif cur is None:
            pass                       # off-track phase → neutral steps
        elif i < cur:
            cls.append("done")
        elif i == cur:
            if phase == "done":
                cls.append("done")
            cls.append("current")
        steps.append(
            f"<div class=\"step {' '.join(cls)}\"><span class=\"node\"></span>"
            f"<span class=\"lbl\">{_e(_STEP_LABEL[ph])}</span></div>")
    return "<div class=\"stepper\">" + "".join(steps) + "</div>"


def _gate_scorecard(gate):
    """8-cell review-gate scorecard + a N/total summary bar. `gate` is
    {check: state}. When seeded it holds the applicable checks; when empty we
    show all REQUIRED_GATES as pending so a scorecard always appears."""
    from board.gate import REQUIRED_GATES
    gate = gate or {}
    if gate:
        checks = [c for c in REQUIRED_GATES if c in gate]
        checks += sorted(c for c in gate if c not in REQUIRED_GATES)
    else:
        checks = list(REQUIRED_GATES)
    if not checks:
        return ""
    cells = []
    n_ok = n_fail = 0
    for c in checks:
        state = gate.get(c) or "pending"
        cls = state if state in ("ok", "fail", "pending") else "pending"
        if cls == "ok":
            n_ok += 1
        elif cls == "fail":
            n_fail += 1
        glyph = _STATE_GLYPH.get(cls, "◐")
        label = _GATE_LABEL.get(c, c.replace("_", " "))
        cells.append(
            f"<div class=\"cell {cls}\"><span class=\"cstate\">{glyph}</span>"
            f"<span class=\"clbl\">{_e(label)}</span></div>")
    total = len(checks)
    ok_pct = 100.0 * n_ok / total
    fail_pct = 100.0 * n_fail / total
    pend_pct = max(0.0, 100.0 - ok_pct - fail_pct)
    bar = (f"<span class=\"bar\"><i class=\"ok\" style=\"width:{ok_pct:.4g}%\"></i>"
           f"<i class=\"fail\" style=\"width:{fail_pct:.4g}%\"></i>"
           f"<i class=\"pend\" style=\"width:{pend_pct:.4g}%\"></i></span>")
    if n_fail:
        frac = f"<span class=\"frac\" style=\"color:var(--fail)\">{n_ok} / {total} ✗</span>"
    elif n_ok == total:
        frac = f"<span class=\"frac\" style=\"color:var(--ok)\">{n_ok} / {total} ✓</span>"
    else:
        frac = f"<span class=\"frac\">{n_ok} / {total}</span>"
    return (
        "<div class=\"gate\"><div class=\"gate-head\">"
        "<span class=\"gt\">Review gate</span>"
        f"<span class=\"gate-summary\">{bar}{frac}</span></div>"
        f"<div class=\"scorecard\">{''.join(cells)}</div></div>"
    )


def _repoline(run):
    repo = run.get("repo")
    issue = run.get("issue")
    if repo:
        owner, _, name = repo.partition("/")
        repo_html = (f"<span class=\"repo\">{_e(owner)}/<b>{_e(name)}</b></span>"
                     if name else f"<span class=\"repo\"><b>{_e(repo)}</b></span>")
    else:
        repo_html = "<span class=\"repo\"><b>—</b></span>"
    issue_html = f"<span class=\"issue\">#{_e(issue)}</span>" if issue not in (None, "") else ""
    bug = "<span class=\"bugtag\">bug-fix</span>" if run.get("is_bug_fix") else ""
    return f"<div class=\"repoline\">{repo_html}{issue_html}{bug}</div>"


def _field(label, value, cls=""):
    if value is None or value == "":
        return ""
    klass = ("field " + cls).strip()
    return (f"<div class=\"{klass}\"><span class=\"fk\">{_e(label)}</span>"
            f"<span class=\"fv\">{_e(value)}</span></div>")


def _cardfoot(run):
    alarms = run.get("alarms") or []
    phase = run.get("phase")
    status = run.get("status")
    if alarms:
        cls, txt = "bad", status or "gate violated · merged early"
    elif phase == "done":
        cls, txt = "good", status or "merged · deployed · verified"
    else:
        cls, txt = "", status or "in progress"
    chip = f"<span class=\"status-chip {cls}\">{_e(txt)}</span>"
    href = _gh_href(run.get("pr_url"))
    pr = ""
    if href:
        danger = " danger" if alarms else ""
        pr = (f"<a class=\"prlink{danger}\" href=\"{href}\">PR "
              f"<span class=\"arrow\">→</span></a>")
    return f"<div class=\"cardfoot\">{chip}{pr}</div>"


def _card(run):
    """One Live/Recent card. `run` is a plain dict assembled by the server.
    EVERY value flows through _e / _gh_href."""
    rid = run.get("run_id")
    phase = run.get("phase")
    gate = run.get("gate") or {}
    alarms = run.get("alarms") or []
    body = (
        "<div class=\"body\">"
        + _repoline(run)
        + (f"<a class=\"title\" href=\"/ticket/{_e(rid)}\">{_e(run.get('title') or rid)}</a>"
           if rid else f"<div class=\"title\">{_e(run.get('title'))}</div>")
        + "<div class=\"meta\">"
        + _field("Goal", run.get("goal"))
        + _field("Approach", run.get("approach"))
        + _field("Result", run.get("result"),
                 cls="result danger" if alarms else "result")
        + "</div>"
        + _gate_scorecard(gate)
        + "</div>"
    )
    return (
        f"<article class=\"card {_phase_class(phase)}{' alarm' if alarms else ''}\">"
        + _alarmbar(alarms)
        + _phasebar(phase, run.get("machine"))
        + _stepper(phase, gate)
        + body
        + _cardfoot(run)
        + "</article>"
    )


def _pagefoot(version):
    return (
        "<footer class=\"pagefoot\">"
        f"<span class=\"ver\">{_e(version)}</span>"
        "<span>Autopilot Board · auto-refresh "
        f"{int(AUTO_REFRESH_S)}s · governance audit live</span>"
        "<span class=\"legend\">"
        "<span><span class=\"sw\" style=\"background:var(--ok)\"></span>passed</span>"
        "<span><span class=\"sw\" style=\"background:var(--pending)\"></span>pending</span>"
        "<span><span class=\"sw\" style=\"background:var(--fail)\"></span>failed</span>"
        "<span><span class=\"sw\" style=\"background:var(--amber)\"></span>needs human</span>"
        "</span></footer>"
    )


# --------------------------------------------------------------------------- #
# public render functions
# --------------------------------------------------------------------------- #
def _sort_live(live):
    """asking-user runs (blocked on a human) sort FIRST so they're never missed,
    then by lifecycle rank descending (closest to merge first)."""
    def key(r):
        ph = r.get("phase")
        needs_human = 0 if ph == "asking-user" else 1
        return (needs_human, -_LC_RANK.get(ph, -1))
    return sorted(live, key=key)


def card_grid(live, recent, version, health, queue=None):
    """Render the board home page (topbar, Up-next, Live, Recent/Done, footer).

    `live`/`recent` are lists of run dicts (keys: run_id, repo, issue, title,
    phase, goal, approach, result, machine, status, pr_url, is_bug_fix,
    gate{check:state}, alarms[list]). `version` is the footer label; `health` is
    the health-strip dict; `queue` (optional) is the planned backlog. All escaped."""
    live = _sort_live(live or [])
    recent = recent or []
    head = _topbar(health) + _queue_section(queue or [])

    if not live and not recent:
        body = (
            head
            + "<div class=\"empty\"><span class=\"big\">◇</span>"
              "No autopilot runs yet — start one with <b>/autopilot</b></div>"
            + _pagefoot(version)
        )
        return _page("Autopilot Board", body)

    parts = [head]
    if live:
        parts.append(
            "<div class=\"sec-head\"><h2>Live</h2>"
            f"<span class=\"badge live\">{_e(len(live))} ACTIVE</span>"
            "<span class=\"rail\"></span></div>"
            "<section class=\"cards\">")
        parts.extend(_card(r) for r in live)
        parts.append("</section>")
    if recent:
        parts.append(
            "<div class=\"sec-head\"><h2>Recent / Done</h2>"
            f"<span class=\"badge done\">{_e(len(recent))} SHIPPED</span>"
            "<span class=\"rail\"></span></div>"
            "<section class=\"cards\">")
        parts.extend(_card(r) for r in recent)
        parts.append("</section>")
    parts.append(_pagefoot(version))
    return _page("Autopilot Board", "".join(parts))


def ticket_detail(run, events, gate, gh):
    """Render the /ticket/<run> detail page: header card, gate table, gh signals,
    evidence, and the event timeline. EVERY value escaped; PR link gh-validated."""
    if run is None:
        return _page("Ticket not found",
                     "<header class=\"topbar\"><div class=\"brand\">"
                     "<span class=\"dot\"></span><div><h1>Ticket not found</h1></div>"
                     "</div></header><p class=\"back\"><a href=\"/\">← back to board</a></p>")

    rid = run.get("run_id")
    repo = run.get("repo")
    issue = run.get("issue")
    phase = run.get("phase")

    detail = (
        f"<article class=\"detail {_phase_class(phase)}\">"
        + _alarmbar(run.get("alarms") or [])
        + _phasebar(phase, run.get("machine"))
        + _stepper(phase, gate)
        + "<div class=\"body\">"
        + _repoline(run)
        + f"<div class=\"title\">{_e(run.get('title') or rid)}</div>"
        + "<div class=\"meta\">"
        + _field("Goal", run.get("goal"))
        + _field("Approach", run.get("approach"))
        + _field("Result", run.get("result"), cls="result")
        + _field("Status", run.get("status"))
        + _field("Unverified", run.get("unverified"))
        + _field("Filed", run.get("filed_issues"))
        + _field("Merge SHA", run.get("merge_sha"))
        + _field("Evidence", run.get("validated_evidence"))
        + _field("RED test", run.get("regression_red_test"))
        + _field("GREEN test", run.get("regression_green_test"))
        + "</div>"
        + _gate_scorecard(gate)
        + _cardfoot(run)
        + "</div></article>"
    )

    # gate source table (claimed vs verified) — detail-only extra
    gate = gate or {}
    gate_tbl = ""
    if gate:
        from board.gate import source_of, REQUIRED_GATES
        order = {g: i for i, g in enumerate(REQUIRED_GATES)}
        rows = []
        for check in sorted(gate, key=lambda c: (order.get(c, len(order)), c)):
            state = gate.get(check) or "pending"
            rows.append(f"<tr><td>{_e(_GATE_LABEL.get(check, check))}</td>"
                        f"<td>{_e(state)}</td><td>{_e(source_of(check))}</td></tr>")
        gate_tbl = ("<h2 class=\"dh\">Gate detail</h2><table>"
                    "<tr><th>check</th><th>state</th><th>source</th></tr>"
                    + "".join(rows) + "</table>")

    gh_tbl = ""
    if gh:
        gh_get = gh.get if hasattr(gh, "get") else (
            lambda k: gh[k] if k in gh.keys() else None)
        rows = [("merged", gh_get("merged")), ("pr state", gh_get("pr_state")),
                ("ci", gh_get("ci_conclusion")), ("mergeable", gh_get("mergeable")),
                ("mergeable state", gh_get("mergeable_state")),
                ("issue state", gh_get("issue_state")),
                ("deploy version", gh_get("deploy_version"))]
        body_rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>"
                            for k, v in rows if v is not None)
        if body_rows:
            gh_tbl = "<h2 class=\"dh\">GitHub signals (verified)</h2><table>" + body_rows + "</table>"

    events = events or []
    if events:
        ev_rows = []
        for ev in events:
            ev_get = ev.get if hasattr(ev, "get") else (
                lambda k, e=ev: e[k] if k in e.keys() else None)
            ev_rows.append(f"<tr><td>{_e(ev_get('seq'))}</td><td>{_e(ev_get('phase'))}</td>"
                           f"<td>{_e(ev_get('message'))}</td><td>{_e(ev_get('event_ts'))}</td></tr>")
        timeline = ("<h2 class=\"dh\">Timeline</h2><table>"
                    "<tr><th>seq</th><th>phase</th><th>message</th><th>ts</th></tr>"
                    + "".join(ev_rows) + "</table>")
    else:
        timeline = "<h2 class=\"dh\">Timeline</h2><div class=\"field\"><span class=\"fv\">no events</span></div>"

    body = (detail + gate_tbl + gh_tbl + timeline
            + "<p class=\"back\"><a href=\"/\">← back to board</a></p>")
    return _page(f"{repo} #{issue}", body)


# expose phase order for any caller that wants display ordering
PHASE_ORDER = ALL_PHASES
