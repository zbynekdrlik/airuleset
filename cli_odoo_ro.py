"""Read-only Odoo JSON-2 client + per-box task-hygiene config (#1036).

The FIRST Odoo read client in airuleset (`discuss_thread_guard.py` is a hook
text classifier, not a client). Production software from line 1
(`architecture-first` production-by-default): a watchdog job depends on it.

DESIGN (framework-first, #414): stdlib `urllib` only — airuleset ships no
third-party deps, and Odoo's `/json/2` endpoint is a plain HTTP+JSON API
(`POST {base}/json/2/{model}/{method}` with `Authorization: Bearer <key>`) that
needs no ORM / XML-RPC library. The reference implementation the ticket cites
(montalu1 `audit_client_comments.py`) is itself a ~12-line urllib JSON-2 client.

HARD invariants:
  * READ-ONLY. `_call` refuses any method outside `READ_METHODS`
    (`search_read` / `search_count` / `read`) BEFORE any request is built, so a
    mutating call can never be dispatched through this client.
  * The API KEY never leaves the auth header. It is read from the stream's
    `~/.secrets/<file>` env file (config carries the PATH only), lives in one
    private attribute, and appears in NO repr / str / error message / log line.
  * Every failure is a structured `OdooError` (never a silent None from a
    network/HTTP/JSON fault) — the config LOADER is the one documented
    fail-safe (absent/corrupt config → None), mirroring the repo's other
    config loaders.

The transport is dependency-injected (`transport=`) so every unit test runs
against a FAKE — this module never makes a real network call in the test suite.
"""
import json
import os

DEFAULT_TIMEOUT_S = 20.0

# The ONLY methods this client will dispatch. A write/unlink/create is refused
# in `_call` before any request is built — read-only by construction.
#
# GUARDED_READ_METHODS: custom server methods that are READ-ONLY by nature
# (they RETURN data, mutate nothing) but need a dedicated ACL. The reactions
# rule (#784 / odoo-erp #5577): the handover account gets a 403 on the RAW
# `mail.message.reaction` model BY DESIGN, so reactions are read through the
# guarded method `message_reactions_guarded` (montalu alias
# `montalu_message_reactions`) on `mail.message`, never the raw model.
GUARDED_READ_METHODS = frozenset({
    "message_reactions_guarded", "montalu_message_reactions",
})
READ_METHODS = frozenset({"search_read", "search_count", "read"}) | GUARDED_READ_METHODS

# The per-box config contract file.
CONFIG_BASENAME = "odoo-task-tracking.json"

# Top-level required keys, plus the stage sub-keys the A/B/C computation needs.
REQUIRED_TOP_KEYS = (
    "instance_url", "api_key_env_file", "project_ids", "stage_ids",
    "stream_partner_ids", "own_author_names",
)
# `hotovo` is REQUIRED (#1036 review 🟡): it defines the closed-stage exclusion
# in `_closed_stage_ids`; a config omitting it would count Done tasks as "open"
# and false-flag them (footer inflation / false Stop-block).
REQUIRED_STAGE_KEYS = ("verifikacia", "realizacia", "potrebuje_ujasnit", "hotovo")

DEFAULT_CLIENT_CONFIRM_DAYS = 3
DEFAULT_API_KEY_VAR = "ODOO_API_KEY"


class OdooError(Exception):
    """Any Odoo read failure — refused method, network/HTTP fault, bad JSON, or
    an unreadable/missing API key. Its message NEVER carries the key value."""


def _urllib_transport(url, data, headers, timeout):
    """Default POST transport: returns `(status, body_bytes)`. An HTTP error
    response (non-2xx) is returned as `(code, body)` so `_call` raises a
    structured error; a genuine network fault (URLError/timeout/socket) raises
    `OdooError` directly. The key is inside `headers` and is never logged."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        # An HTTP error IS a response — hand back its code + body (never the key).
        try:
            body = e.read()
        except (OSError, ValueError):
            body = b""
        return e.code, body
    except (urllib.error.URLError, OSError, ValueError) as e:
        # Network-level fault — no response at all. Surface WITHOUT the URL's
        # auth header (only the reason), so nothing leaks.
        raise OdooError("Odoo request failed (network): %s" % e.reason
                        if hasattr(e, "reason") else "Odoo request failed (network)")


class OdooReadOnlyClient:
    """A minimal read-only JSON-2 Odoo client.

    `transport(url, data_bytes, headers, timeout) -> (status, body_bytes)` is
    injectable; the default is `_urllib_transport`. The key is stored privately
    and never rendered."""

    def __init__(self, instance_url, api_key, timeout=DEFAULT_TIMEOUT_S,
                 transport=None):
        if not instance_url:
            raise OdooError("instance_url is required")
        self._base = str(instance_url).rstrip("/")
        self._key = api_key or ""
        self._timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self._transport = transport or _urllib_transport

    def __repr__(self):
        return "<OdooReadOnlyClient base=%s key=***>" % self._base

    __str__ = __repr__

    def _call(self, model, method, **body):
        if method not in READ_METHODS:
            raise OdooError(
                "refused non-read method %r (read-only client allows only %s)"
                % (method, ", ".join(sorted(READ_METHODS))))
        url = "%s/json/2/%s/%s" % (self._base, model, method)
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": "Bearer %s" % self._key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        status, raw = self._transport(url, data, headers, self._timeout)
        if status != 200:
            snippet = ""
            try:
                snippet = (raw or b"").decode("utf-8", "replace")[:200]
            except (UnicodeError, AttributeError):
                snippet = ""
            raise OdooError("Odoo %s/%s HTTP %s: %s"
                            % (model, method, status, snippet))
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as e:
            raise OdooError("Odoo %s/%s returned non-JSON: %s"
                            % (model, method, e))

    def call(self, model, method, **body):
        """Public dispatch for the A/B/C computation's injected `call` seam —
        delegates to `_call` (same read-only allowlist enforcement). Used for
        the guarded reaction read (`message_reactions_guarded`)."""
        return self._call(model, method, **body)

    def search_read(self, model, domain, fields=None, limit=None, order=None):
        body = {"domain": domain}
        if fields is not None:
            body["fields"] = fields
        if limit is not None:
            body["limit"] = limit
        if order is not None:
            body["order"] = order
        return self._call(model, "search_read", **body)

    def search_count(self, model, domain):
        return self._call(model, "search_count", domain=domain)

    def read(self, model, ids, fields=None):
        body = {"ids": ids}
        if fields is not None:
            body["fields"] = fields
        return self._call(model, "read", **body)


# --------------------------------------------------------------------------- #
# Config contract — ~/.claude/odoo-task-tracking.json
# --------------------------------------------------------------------------- #
def default_config_path(home=None):
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".claude", CONFIG_BASENAME)


def _strip_hash_comments(text):
    """Drop only WHOLE-LINE `#` comments (a line whose first non-blank char is
    `#`). Never strips mid-line — an `https://` value carries `//`, never a
    leading `#`, so a value is never corrupted (the double-slash-URL guard)."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def load_config(path=None, home=None):
    """The parsed config dict, or None when absent / unreadable / not valid JSON
    (the documented fail-safe — the watchdog job renders `not configured`, never
    an error). `#`-line comments in the file are stripped before parsing."""
    p = path if path is not None else default_config_path(home)
    try:
        with open(p, encoding="utf-8") as h:
            raw = h.read()
    except OSError:
        return None
    try:
        data = json.loads(_strip_hash_comments(raw))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def config_valid(cfg):
    """`(ok, missing)` — `missing` lists each absent required top-level key and
    each absent required stage sub-key (`stage_ids.<name>`). A non-dict config
    is entirely missing."""
    if not isinstance(cfg, dict):
        return False, list(REQUIRED_TOP_KEYS)
    missing = [k for k in REQUIRED_TOP_KEYS if not cfg.get(k)]
    stages = cfg.get("stage_ids")
    if isinstance(stages, dict):
        for sk in REQUIRED_STAGE_KEYS:
            if stages.get(sk) is None:
                missing.append("stage_ids.%s" % sk)
    elif "stage_ids" not in missing:
        # stage_ids present but not a dict → its sub-keys are all missing
        missing.extend("stage_ids.%s" % sk for sk in REQUIRED_STAGE_KEYS)
    return (not missing), missing


def client_confirm_days(cfg):
    """The Verifikácia client-confirmation window in days (default 3)."""
    try:
        v = (cfg or {}).get("client_confirm_days", DEFAULT_CLIENT_CONFIRM_DAYS)
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_CLIENT_CONFIRM_DAYS


def read_api_key(env_file, var=DEFAULT_API_KEY_VAR):
    """Read `var`'s value from a `KEY=value` env file (path expanded). Tolerates
    a leading `export `, surrounding quotes, and `#` comment lines. Raises
    `OdooError` when the file is unreadable or `var` is absent. NEVER logs the
    value."""
    path = os.path.expanduser(str(env_file or ""))
    try:
        with open(path, encoding="utf-8") as h:
            lines = h.readlines()
    except OSError:
        raise OdooError("cannot read API-key env file (path from config)")
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].strip()
        if "=" not in s:
            continue
        name, _, val = s.partition("=")
        if name.strip() != var:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        return val
    raise OdooError("API-key variable %r not found in env file" % var)


def client_from_config(cfg, transport=None):
    """Build a read-only client from `cfg`, reading the key from the env file
    named by config (`api_key_env_file` + optional `api_key_var`)."""
    var = (cfg or {}).get("api_key_var") or DEFAULT_API_KEY_VAR
    key = read_api_key(cfg["api_key_env_file"], var)
    return OdooReadOnlyClient(cfg["instance_url"], key, transport=transport)


def config_template():
    """A `#`-commented template with the montalu values from the ticket as the
    EXAMPLE (erp.montalu.cloud, project 1, partner 17244, stages
    2880/2879/3350/2881). The API key itself stays in `~/.secrets/<file>` — this
    file carries only the PATH."""
    return (
        "# airuleset Odoo task-hygiene config (#1036). Fill in per stream.\n"
        "# Configure this ONLY on a dedicated Odoo STREAM box: the footer I,\n"
        "# the nudge, and the Stop gate are BOX-WIDE (they fire on every\n"
        "# session on the box), so a box that also does non-Odoo work would\n"
        "# see unrelated turns blocked/nudged. The API KEY itself lives in the\n"
        "# ~/.secrets/<file> named by api_key_env_file below — NEVER put the\n"
        "# key value in this file. The values below are the montalu EXAMPLE.\n"
        "{\n"
        '  "instance_url": "https://erp.montalu.cloud",\n'
        '  "api_key_env_file": "~/.secrets/odoo-montalu.env",\n'
        '  "api_key_var": "ODOO_API_KEY",\n'
        '  "project_ids": [1],\n'
        '  "stage_ids": {\n'
        '    "verifikacia": 2880,\n'
        '    "realizacia": 2879,\n'
        '    "potrebuje_ujasnit": 3350,\n'
        '    "hotovo": 2881\n'
        "  },\n"
        '  "stream_partner_ids": [17244],\n'
        '  "own_author_names": ["ZbynekAI", "Zbynek Drlik", "Marek Greňa"],\n'
        '  "client_confirm_days": 3\n'
        "}\n"
    )
