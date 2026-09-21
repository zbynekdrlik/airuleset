"""gates.prod_transfer -- the per-ticket Prod-transfer manifest gate (#1105,
owner TODO 21.9.2026).

THE PROBLEM. A sub-dev stream (david1-4 first) receives from the developer,
during a ticket, everything a feature needs to be verified on its erp-test
shadow box: API keys/logins via `secret request`, config records
(`ir.config_parameter` / `res.config.settings`), seed data, `.env` values,
webhooks. The feature is verified there and handed off; the gatekeeper merges
and deploys; on PROD the same inputs are absent -- "it worked on erp-test,
nothing worked on prod". Nothing today records what erp-test-only inputs a
ticket depends on, nor asks how each reaches prod.

THIS module is the hermetic classifier core the design's Approach 1 needs, shared
by four consumers (the same hermetic shape as gates/spec.py + gates/navody.py):

  1. `airuleset.py prod-transfer add` (cli_prod_transfer) renders one manifest
     line in the exact shape and refuses a secret VALUE (`line_has_secret_value`,
     reusing the gates.secrets entropy scanner) -- item 1.
  2. `airuleset.py handoff`'s composer/gk pre-flight
     (`airuleset._handoff_prod_transfer_preflight`) scans the lane diff/commands
     (`lane_scan_text` -> `surfaces_in`); an erp-test-only surface with no
     `Prod-transfer:` manifest (or `Prod-transfer: none -- <why>`) is refused
     (`manifest_preflight`) -- item 2.
  3. the deploy check-off reads a `Prod-transfer-status:` line per item
     (`status_lines` / `pending_items`) and a pending item maps to an owner-facing
     label (`label_for_pending`: needs-owner-action -> U, ops-wait -> W) -- item 3.
  4. the client-acceptance guard refuses to send while any item is pending
     (`acceptance_block`) -- item 4.

STDLIB ONLY, and no airuleset/watchdog/notify import at module load (a classifier
stays cheap and self-contained). Every function here is a SHAPE check, never a
proof -- the fail-open direction is the sibling pre-flights' never-false-accuse
convention (an undeterminable diff must never fabricate a block).
"""
import re

# --------------------------------------------------------------------------- #
# (a) erp-test-only surfaces -- what a lane's diff/commands touched (item 2)
# --------------------------------------------------------------------------- #
# Each entry maps a CANONICAL surface name (printed in the block reason) to the
# pattern that detects it in a diff/command text blob. Odoo config identifiers
# are dotted; `.env`/`webhook`/`api_key` are generic (segment-bounded so `.env`
# does not match `.environment` and `api_key` catches api_key/apiKey/api-key).
_SURFACES = (
    ("res.config.settings", re.compile(r"res\.config\.settings", re.I)),
    ("ir.config_parameter", re.compile(r"ir\.config_parameter", re.I)),
    ("set_param", re.compile(r"\bset_param\b", re.I)),
    ("secret request", re.compile(r"\bsecret[ \t]+request\b", re.I)),
    ("REFRESH-DEV-BOX-FROM-PROD", re.compile(r"REFRESH-DEV-BOX-FROM-PROD")),
    (".env", re.compile(r"\.env\b", re.I)),
    # No trailing \b: `webhook` must still match `webhook_url` / `webhooks`.
    ("webhook", re.compile(r"\bwebhook", re.I)),
    ("api_key", re.compile(r"\bapi[_-]?key\b", re.I)),
)


def surfaces_in(text):
    """The erp-test-only surfaces present in `text` (a diff patch's added lines
    and/or session commands), de-duplicated in the declared stable order. Empty
    list for None/no-surface."""
    if not text:
        return []
    out = []
    for name, pat in _SURFACES:
        if pat.search(text) and name not in out:
            out.append(name)
    return out


# --------------------------------------------------------------------------- #
# (b) manifest lines -- `Prod-transfer:` and the `none -- <why>` escape (item 1)
# --------------------------------------------------------------------------- #
# `Prod-transfer:` (never `Prod-transfer-status:`, which has `-status` before the
# colon so `transfer:` never appears there).
_MANIFEST_RE = re.compile(r"(?im)^[ \t]*Prod-transfer:[ \t]*(.+?)[ \t]*$")
_NONE_RE = re.compile(r"^none\b[ \t]*[—–-][ \t]*(\S.*)$", re.I)


def _is_none_escape(value):
    """(True, reason) iff `value` is `none -- <why>` (a reason after `none` is
    REQUIRED -- a bare `none` does not escape); else (False, "")."""
    m = _NONE_RE.match((value or "").strip())
    if m and (m.group(1) or "").strip():
        return True, m.group(1).strip()
    return False, ""


def _manifest_values_all(body):
    """Every `Prod-transfer:` line value (including any `none -- <why>` escape)."""
    return [m.group(1).strip() for m in _MANIFEST_RE.finditer(body or "")]


def manifest_lines(body):
    """The REAL `Prod-transfer:` manifest line values in `body` -- the `none --
    <why>` escape and every `Prod-transfer-status:` line excluded."""
    out = []
    for val in _manifest_values_all(body):
        ok, _ = _is_none_escape(val)
        if not ok:
            out.append(val)
    return out


def has_manifest_none(body):
    """(True, reason) iff `body` carries a `Prod-transfer: none -- <why>` escape
    (nothing needs to reach prod); else (False, "")."""
    for val in _manifest_values_all(body):
        ok, reason = _is_none_escape(val)
        if ok:
            return True, reason
    return False, ""


# --------------------------------------------------------------------------- #
# (c) secret-value guard -- a manifest line may name a secret, never carry it
# --------------------------------------------------------------------------- #
def line_has_secret_value(line):
    """A short violation description iff `line` carries what looks like a secret
    VALUE (reuses gates.secrets.scan_line -- the SAME entropy scanner
    block-sensitive-staging.sh uses), else None. A secret belongs in the
    manifest by NAME + vault path only; the value is routed via `secret show`."""
    try:
        from gates import secrets as _secrets
    except Exception:
        return None
    return _secrets.scan_line(line or "")


# --------------------------------------------------------------------------- #
# (d) render one manifest line in the exact shape (item 1, the CLI)
# --------------------------------------------------------------------------- #
SENSITIVITIES = ("secret", "data", "config")


def render_manifest_line(*, what, who, date, location, path, sensitivity):
    """The canonical `Prod-transfer:` line -- the shape the design fixes and the
    composer pre-flight/deploy check-off read."""
    return ("Prod-transfer: %s — supplied by %s %s — erp-test: %s "
            "— prod path: %s — sensitivity: %s"
            % (what, who, date, location, path, sensitivity))


# --------------------------------------------------------------------------- #
# (e) the composer/gk hand-off pre-flight (item 2)
# --------------------------------------------------------------------------- #
_SURFACE_BLOCK = (
    "handoff BLOCK: this lane touched erp-test-only surface(s) (%s) but the RFR "
    "carries no `Prod-transfer:` manifest (#1105). Record how each "
    "developer-supplied input reaches prod — append a `Prod-transfer: "
    "<what> — supplied by <who> <D.M.YYYY> — erp-test: <location> "
    "— prod path: vault→owner (secret show) | gk config step | data "
    "migration <script> | developer manual step | n/a test-only — "
    "sensitivity: secret | data | config` line (helper: `airuleset.py "
    "prod-transfer add`), or `Prod-transfer: none — <why>` if nothing "
    "needs to reach prod.")

_SECRET_BLOCK = (
    "handoff BLOCK: a `Prod-transfer:` line carries what looks like a secret "
    "VALUE (%s) (#1105). List a secret by NAME + vault path only, never the "
    "value; route the value to the owner via `secret show` (#879).")


def manifest_preflight(body, scan_text):
    """The hand-off gate. `(ok, reason_or_None)`:

      * `scan_text` is None (diff undeterminable) -> (True, None) -- FAIL-OPEN,
        byte-identical to today (never fabricate a block from an unreadable diff);
      * a `Prod-transfer:` line carries a secret VALUE -> BLOCK;
      * the lane touched an erp-test-only surface and `body` carries neither a
        real `Prod-transfer:` line nor a `Prod-transfer: none -- <why>` escape ->
        BLOCK, naming the surfaces + the fix;
      * otherwise -> (True, None).
    """
    if scan_text is None:
        return True, None
    for val in _manifest_values_all(body):
        hit = line_has_secret_value(val)
        if hit:
            return False, _SECRET_BLOCK % hit
    surfaces = surfaces_in(scan_text)
    if not surfaces:
        return True, None
    if manifest_lines(body) or has_manifest_none(body)[0]:
        return True, None
    return False, _SURFACE_BLOCK % ", ".join(surfaces)


# --------------------------------------------------------------------------- #
# (f) deploy check-off -- `Prod-transfer-status:` states + pending -> label (item 3)
# --------------------------------------------------------------------------- #
_STATUS_RE = re.compile(r"(?im)^[ \t]*Prod-transfer-status:[ \t]*(.+?)[ \t]*$")
# The doctrine separator is an em-/en-dash (or a SPACED ascii hyphen) -- NEVER a
# bare `-` (so the hyphen inside `owner-action` never splits the state).
_SEP_RE = re.compile(r"[ \t]+[—–][ \t]+|[ \t]+-[ \t]+")
# States in priority order (a pending kind is matched before its bare word).
_STATE_FAMILIES = (
    "owner-action pending",
    "developer step pending",
    "transferred",
    "n/a",
)
_PENDING_STATES = ("owner-action pending", "developer step pending")
_RESOLVED_STATES = ("transferred", "n/a")

_PENDING_LABEL = {
    "owner-action pending": "needs-owner-action",
    "developer step pending": "ops-wait",
}


def _classify_state(rest):
    low = (rest or "").lower()
    for fam in _STATE_FAMILIES:
        if fam in low:
            return fam
    return (rest or "").strip()


def status_lines(body):
    """Every `Prod-transfer-status:` line as `(what, canonical_state)`. The state
    is normalised to one of the four families (a trailing detail such as
    `owner-action pending (secret show sent)` still classifies as `owner-action
    pending`); an unrecognised state is returned verbatim."""
    out = []
    for m in _STATUS_RE.finditer(body or ""):
        val = m.group(1).strip()
        parts = _SEP_RE.split(val)
        if len(parts) < 2:
            out.append((val, ""))
            continue
        what = "—".join(p.strip() for p in parts[:-1]).strip()
        out.append((what, _classify_state(parts[-1])))
    return out


def pending_items(body):
    """The `(what, state)` status lines whose state is a PENDING kind."""
    return [(w, s) for w, s in status_lines(body) if s in _PENDING_STATES]


def label_for_pending(state):
    """The owner-facing label a pending state maps to: `owner-action pending` ->
    `needs-owner-action` (U, #879); `developer step pending` -> `ops-wait` (W);
    a resolved/unknown state -> None."""
    return _PENDING_LABEL.get(state)


# --------------------------------------------------------------------------- #
# (g) the client-acceptance guard (item 4)
# --------------------------------------------------------------------------- #
def acceptance_block(body):
    """`(blocked, reason_or_None)` -- the client-acceptance/handover composer must
    NOT send while any Prod-transfer item is pending. An item is pending when a
    status line marks it so, OR when a real manifest item has no resolving
    (`transferred`/`n/a`) status line yet (never checked off = not transferred).
    A `Prod-transfer: none -- <why>` escape (nothing to transfer) never blocks."""
    pend = pending_items(body)
    if pend:
        return True, pend[0][0]
    real = manifest_lines(body)
    resolved = [(w, s) for w, s in status_lines(body) if s in _RESOLVED_STATES]
    if real and len(resolved) < len(real):
        return True, ("%d of %d Prod-transfer item(s) not yet checked off"
                      % (len(resolved), len(real)))
    return False, None


# --------------------------------------------------------------------------- #
# (h) production seam -- the lane's diff added-line content for the pre-flight
# --------------------------------------------------------------------------- #
def lane_scan_text(cwd=None, run=None):
    """The lane's `git diff <base>...HEAD` ADDED-line content (a text blob for
    `surfaces_in`), mirroring `airuleset._handoff_changed_paths`' base resolution
    (`origin/HEAD` default, else develop/main/master). None when undeterminable
    (no base / git error) so the caller FAILS OPEN. `run(argv)->CompletedProcess`
    is injected in tests; production shells `git` in `cwd`."""
    import subprocess as _sp
    if run is None:
        def run(argv):
            try:
                return _sp.run(argv, capture_output=True, text=True,
                               timeout=15, cwd=cwd or None)
            except Exception as e:
                return _sp.CompletedProcess(argv, 1, "", str(e))
    bases = []
    hr = run(["git", "symbolic-ref", "--quiet", "--short",
              "refs/remotes/origin/HEAD"])
    if getattr(hr, "returncode", 1) == 0 and (hr.stdout or "").strip():
        bases.append((hr.stdout or "").strip())
    bases += ["origin/develop", "origin/main", "origin/master"]
    seen = set()
    for base in bases:
        if not base or base in seen:
            continue
        seen.add(base)
        dr = run(["git", "diff", "%s...HEAD" % base])
        if getattr(dr, "returncode", 1) == 0:
            added = [ln[1:] for ln in (dr.stdout or "").splitlines()
                     if ln.startswith("+") and not ln.startswith("+++")]
            return "\n".join(added)
    return None
