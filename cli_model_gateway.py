"""#1062 Lane L1 — a managed LiteLLM model gateway on the controller box.

The pilot (owner decision 2026-09-17, issue 1062) keeps Claude Code as the agent
CLI but points ONE box's backend at an Anthropic-compatible gateway instead of
Anthropic, so the model can be switched per test (e.g. DeepSeek 4.1 via
OpenRouter). This leaf is the GATEWAY half (Lane L1) — a LiteLLM proxy service on
the controller that maps tier ALIASES (`pilot-main`/`pilot-sub`/`pilot-fast`) to
any provider model, so the owner flips the model by editing one central alias map
(or `airuleset.py model-gateway set pilot-main openrouter/deepseek/...`) with no
box visit. The per-box backend switch (Lane L2) is a later lane.

Design: issue 1062, `Design-by: main claude-fable-5-1` comment (items 1–4).
Anchors + LiteLLM doc URLs: the `Anchors-confirmed` comment on the same issue.

Stdlib only (a clean leaf — no top-level `import airuleset`, so no import-cycle
surface). Secrets are NEVER written into the rendered yaml: LiteLLM reads each
provider key by `os.environ/<VAR>` indirection, and the values reach the proxy
process ONLY through the systemd unit's `EnvironmentFile`, rendered at install
time from the fleet-standard durable key files under `~/.secrets/*.key`. The
renderers here NEVER read `~/.secrets`; an injected `read_secret` callable does
(the install step passes the real reader, tests pass a fake).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"

# --- Pinned LiteLLM release. Verified latest stable on PyPI 2026-09-17
# (https://pypi.org/pypi/litellm/json -> 1.101.0). The proxy server needs the
# `[proxy]` extras, so the install pins `litellm[proxy]==<LITELLM_PIN>`. ---
LITELLM_PIN = "1.101.0"

# --- Paths (all under the invoking user's home) ---
ALIAS_MAP_PATH = CLAUDE_DIR / "airuleset-model-gateway.json"
CONFIG_DIR = Path.home() / ".config" / "airuleset"
YAML_PATH = CONFIG_DIR / "model-gateway.yaml"
ENV_PATH = CONFIG_DIR / "model-gateway.env"
SPEND_LOG_PATH = CONFIG_DIR / "model-gateway-spend.jsonl"
SPEND_LOGGER_DEST = CONFIG_DIR / "model_gateway_spend_logger.py"
VENV_DIR = Path.home() / ".local" / "share" / "airuleset" / "model-gateway-venv"
MASTER_KEY_FILE = Path.home() / ".secrets" / "model-gateway.master"
SERVICE_TEMPLATE = REPO_DIR / "settings" / "model-gateway.service.template"
SPEND_LOGGER_TEMPLATE = REPO_DIR / "settings" / "model_gateway_spend_logger.py"
SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "model-gateway.service"
GATEWAY_PORT = 4000
SERVICE_NAME = "model-gateway.service"

# The LiteLLM success callback that appends the JSONL spend records `model-gateway
# spend` reads. Placed in CONFIG_DIR at install; the unit sets PYTHONPATH to that
# dir so litellm can import it (`litellm_settings.callbacks: <module>.<attr>`).
CALLBACK_REF = "model_gateway_spend_logger.spend_logger_instance"

# The single unit-template placeholder: the tailscale bind address baked in at
# install (the gateway must bind ONLY the tailscale interface, never LAN/public).
HOST_IP_PLACEHOLDER = "{{HOST_IP}}"


class ModelGatewayError(Exception):
    """A user-facing model-gateway error (bad args, unreadable map)."""


# --------------------------------------------------------------------------- #
# Pure helpers (fully unit-testable, no I/O beyond what is injected)
# --------------------------------------------------------------------------- #
def _expand(p):
    return Path(os.path.expanduser(str(p)))


def _coerce(value, cast, default):
    """Cast `value` with `cast`, returning `default` on a malformed field.
    Returns a value (never a bare `pass`) so a bad spend-log field degrades to
    the default instead of crashing the aggregate."""
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _provider_of(target):
    """The provider = the FIRST path segment of an alias target
    `provider/model[/...]` (e.g. `openrouter/deepseek/deepseek-v4.1-flash` ->
    `openrouter`)."""
    return (target or "").split("/", 1)[0]


def _provider_env_var(provider):
    """Deterministic env-var name for a provider's key: `openrouter` ->
    `OPENROUTER_KEY`, `vertex-ai` -> `VERTEX_AI_KEY`. Used identically by the
    yaml renderer (`os.environ/<VAR>`) and the env-file renderer (`<VAR>=...`),
    so the two always agree by construction."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", provider or "").strip("_").upper()
    return f"{slug}_KEY" if slug else "PROVIDER_KEY"


def _conventional_key_file(provider):
    """Where a provider's key lives by convention: `~/.secrets/<provider>.key`."""
    return f"~/.secrets/{provider}.key"


def _yaml_str(s):
    """Double-quote + escape a scalar so a value with a slash/dot/colon renders
    as a safe YAML string."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def default_alias_map():
    """The design's default (issue 1062): three pilot tiers on OpenRouter's
    DeepSeek 4.1 Flash, one configured provider. `set` seeds from this when no
    map exists yet, so a fresh controller gets a complete 3-tier map."""
    target = "openrouter/deepseek/deepseek-v4.1-flash"
    return {
        "aliases": {
            "pilot-main": target,
            "pilot-sub": target,
            "pilot-fast": target,
        },
        "providers": {
            "openrouter": {"key_file": _conventional_key_file("openrouter")},
        },
    }


def _normalize_map(data):
    """Fill in the two required top-level keys without discarding extras."""
    if not isinstance(data, dict):
        raise ModelGatewayError("alias map must be a JSON object")
    data.setdefault("aliases", {})
    data.setdefault("providers", {})
    if not isinstance(data["aliases"], dict) or not isinstance(data["providers"], dict):
        raise ModelGatewayError("alias map `aliases`/`providers` must be objects")
    return data


def _referenced_providers(config):
    """The set of providers actually used by an alias — the ONLY providers the
    env file and yaml reference, so a stale/unused provider entry never forces a
    key that is not present."""
    return {_provider_of(t) for t in config.get("aliases", {}).values() if t}


def _key_file_for(config, provider):
    entry = config.get("providers", {}).get(provider) or {}
    return entry.get("key_file") or _conventional_key_file(provider)


def render_gateway_yaml(config):
    """Render the LiteLLM proxy config.yaml from the alias map. NO secret ever
    appears here — each model's key is `api_key: os.environ/<VAR>` indirection.

    Keys (docs.litellm.ai/docs/tutorials/claude_non_anthropic_models):
    `model_list[].model_name` = the alias, `litellm_params.model` =
    `provider/model`, `general_settings.master_key` = os.environ indirection,
    `litellm_settings.drop_params: true` (docs/completion/drop_params),
    `litellm_settings.callbacks` = the JSONL spend logger."""
    config = _normalize_map(dict(config))
    aliases = config["aliases"]
    lines = [
        "# Managed by airuleset (model-gateway, issue 1062). Do NOT edit by hand.",
        "# Regenerate: `airuleset.py model-gateway set <alias> <provider/model>`",
        "# or `airuleset.py install` (controller). Keys live in ~/.secrets/*.key",
        "# and reach the proxy via the systemd EnvironmentFile — never in this file.",
        "model_list:",
    ]
    for alias in sorted(aliases):
        target = aliases[alias]
        var = _provider_env_var(_provider_of(target))
        lines.append(f"  - model_name: {_yaml_str(alias)}")
        lines.append("    litellm_params:")
        lines.append(f"      model: {_yaml_str(target)}")
        lines.append(f"      api_key: {_yaml_str('os.environ/' + var)}")
    lines.append("general_settings:")
    lines.append(f"  master_key: {_yaml_str('os.environ/LITELLM_MASTER_KEY')}")
    lines.append("litellm_settings:")
    lines.append("  drop_params: true")
    lines.append(f"  callbacks: {_yaml_str(CALLBACK_REF)}")
    return "\n".join(lines) + "\n"


def render_gateway_env(config, read_secret, master_key_file=None):
    """Render the systemd EnvironmentFile the proxy loads. `read_secret(path)`
    returns the (stripped) value of a key file — the ONLY thing that touches
    `~/.secrets`. In production the install step passes a reader over the real
    key files; tests pass a fake. The var names match `render_gateway_yaml`'s
    `os.environ/<VAR>` exactly."""
    config = _normalize_map(dict(config))
    mk = _expand(master_key_file or MASTER_KEY_FILE)
    lines = [
        "# Managed by airuleset (model-gateway, issue 1062). systemd EnvironmentFile.",
        "# Rendered from ~/.secrets/*.key at install; regenerated on every install.",
        f"LITELLM_MASTER_KEY={read_secret(str(mk))}",
    ]
    for provider in sorted(_referenced_providers(config)):
        var = _provider_env_var(provider)
        kf = _expand(_key_file_for(config, provider))
        lines.append(f"{var}={read_secret(str(kf))}")
    return "\n".join(lines) + "\n"


def render_gateway_unit(host_ip, template_path=None):
    """Render the systemd --user unit, substituting the tailscale bind IP."""
    tmpl = _expand(template_path or SERVICE_TEMPLATE)
    if not tmpl.exists():
        raise ModelGatewayError(f"missing service template: {tmpl}")
    if not host_ip:
        raise ModelGatewayError("refusing to render the unit with an empty bind "
                                "IP — the gateway must bind the tailscale address")
    return tmpl.read_text(encoding="utf-8").replace(HOST_IP_PLACEHOLDER, str(host_ip))


# --------------------------------------------------------------------------- #
# Alias-map mutation (`set`) — no `~/.secrets` access, testable on a temp home
# --------------------------------------------------------------------------- #
def load_alias_map(path=None):
    """The parsed alias map, or None when the file is absent."""
    p = _expand(path or ALIAS_MAP_PATH)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModelGatewayError(f"cannot read alias map {p}: {e}")
    return _normalize_map(data)


def _write_alias_map(config, path):
    p = _expand(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_alias(alias, target, path=None, yaml_path=None, reload_fn=None):
    """Point `alias` at `target` (`provider/model`): rewrite the map + the yaml,
    then reload the service. Seeds the map from `default_alias_map()` when it is
    absent (so a fresh controller gets a complete 3-tier map). Ensures the
    target's provider has a `providers` entry (conventional key file if new).
    Returns (config, added_provider_or_None). Never reads `~/.secrets`."""
    if not alias:
        raise ModelGatewayError("usage: model-gateway set <alias> <provider/model>")
    if "/" not in (target or ""):
        raise ModelGatewayError(
            f"target must be `provider/model` (e.g. openrouter/deepseek/"
            f"deepseek-v4.1-flash), got {target!r}")
    p = _expand(path or ALIAS_MAP_PATH)
    config = load_alias_map(p)
    if config is None:
        config = default_alias_map()
    provider = _provider_of(target)
    added_provider = None
    if provider not in config["providers"]:
        config["providers"][provider] = {"key_file": _conventional_key_file(provider)}
        added_provider = provider
    config["aliases"][alias] = target
    _write_alias_map(config, p)
    yp = _expand(yaml_path or YAML_PATH)
    yp.parent.mkdir(parents=True, exist_ok=True)
    yp.write_text(render_gateway_yaml(config), encoding="utf-8")
    (reload_fn or _reload_service)()
    return config, added_provider


# --------------------------------------------------------------------------- #
# Spend log — a DB-free JSONL written by the LiteLLM CustomLogger callback
# (docs.litellm.ai/docs/observability/custom_callback), parsed here.
# Record shape (must match settings/model_gateway_spend_logger.py):
#   {"ts": "<ISO8601>", "alias": "<model_name>", "cost_usd": <float>,
#    "prompt_tokens": <int>, "completion_tokens": <int>, "total_tokens": <int>}
# --------------------------------------------------------------------------- #
def load_spend_records(path=None):
    """Read the JSONL spend log into a list of dicts, skipping blank/malformed
    lines. Returns [] when the log does not exist yet."""
    p = _expand(path or SPEND_LOG_PATH)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def aggregate_spend(records, since=None):
    """Aggregate spend by (day, alias). `since` is an ISO lower bound compared
    lexicographically against each record's `ts` (ISO8601 sorts chronologically).
    Returns a list of dicts sorted by (day, alias)."""
    buckets = {}
    for rec in records:
        ts = str(rec.get("ts") or "")
        if not ts:
            continue
        if since and ts < str(since):
            continue
        day = ts[:10]
        alias = str(rec.get("alias") or "?")
        b = buckets.setdefault((day, alias), {
            "day": day, "alias": alias, "requests": 0,
            "cost_usd": 0.0, "total_tokens": 0,
        })
        b["requests"] += 1
        b["cost_usd"] += _coerce(rec.get("cost_usd"), float, 0.0)
        b["total_tokens"] += _coerce(rec.get("total_tokens"), int, 0)
    return [buckets[k] for k in sorted(buckets)]


def format_spend_table(rows):
    """A plain fixed-width table of the aggregated spend."""
    if not rows:
        return "no spend recorded yet"
    out = [f"{'DAY':<12} {'ALIAS':<20} {'REQS':>6} {'TOKENS':>10} {'COST_USD':>10}"]
    total = 0.0
    for r in rows:
        out.append(f"{r['day']:<12} {r['alias']:<20} {r['requests']:>6} "
                   f"{r['total_tokens']:>10} {r['cost_usd']:>10.4f}")
        total += r["cost_usd"]
    out.append(f"{'TOTAL':<12} {'':<20} {'':>6} {'':>10} {total:>10.4f}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# systemd helpers (reuse the file-drop leaf's XDG-aware runner)
# --------------------------------------------------------------------------- #
def _run_systemctl(args):
    try:
        from cli_filedrop_watchdog import _run_systemctl as _rs
        return _rs(args)
    except Exception as e:  # pragma: no cover - defensive: leaf import failure
        return 1, "", str(e)


def _reload_service():
    """Best-effort `reload-or-restart` so a new alias target takes effect.
    Never raises — a controller without the service yet just prints a note."""
    rc, _o, err = _run_systemctl(["reload-or-restart", SERVICE_NAME])
    if rc != 0:
        print(f"  model-gateway: reload-or-restart {SERVICE_NAME} skipped "
              f"(rc={rc}: {err.strip()}) — run `airuleset.py install` on the "
              f"controller to (re)install the service", file=sys.stderr)
    return rc == 0


def _service_state():
    rc, out, _err = _run_systemctl(["is-active", SERVICE_NAME])
    return (out or "").strip() or "unknown"


# --------------------------------------------------------------------------- #
# Install step (controller-only). Written here; RUN live only by the
# supervisor's `airuleset.py install` — never by the authoring lane (which may
# not create the venv / pip-install / enable a unit / read ~/.secrets).
# --------------------------------------------------------------------------- #
def _current_box_class():
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except Exception as e:  # pragma: no cover - defensive: reaper import failure
        print(f"  model-gateway: box-class read failed ({e})", file=sys.stderr)
        return None


def model_gateway_should_install(box_class, alias_map_path=None):
    """The install gate (pure): act ONLY on box-class `controller` AND only when
    an alias map already exists. A no-op everywhere else."""
    if box_class != "controller":
        return False
    return _expand(alias_map_path or ALIAS_MAP_PATH).exists()


def maybe_setup_model_gateway(box_class=None, alias_map_path=None, setup_fn=None):
    """cmd_install entry point. Prints a LOUD no-op line off the controller or
    when no alias map exists yet; otherwise installs the gateway service."""
    if box_class is None:
        box_class = _current_box_class()
    amp = _expand(alias_map_path or ALIAS_MAP_PATH)
    if box_class != "controller":
        print("  model-gateway: skipped (box class is not `controller`)")
        return False
    if not amp.exists():
        print(f"  model-gateway: no alias map at {amp} — nothing to install "
              f"(create one with `airuleset.py model-gateway set "
              f"<alias> <provider/model>`)")
        return False
    (setup_fn or setup_model_gateway_service)(alias_map_path=amp)
    return True


def _read_secret_file(path):
    """Read a durable `~/.secrets/*` key file and return its stripped value.
    ONLY the install step calls this (the authoring lane never runs it)."""
    with open(os.path.expanduser(str(path)), "r", encoding="utf-8") as fh:
        return fh.read().strip()


def _tailscale_ip():
    """The box's tailscale IPv4 (`tailscale ip -4`, first line) — the ONLY
    address the gateway is allowed to bind. Raises loudly when unavailable, so
    the install never binds a LAN/public interface by mistake."""
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                           text=True, timeout=15)
    except Exception as e:
        raise ModelGatewayError(f"cannot read tailscale ip: {e}")
    ip = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not ip:
        raise ModelGatewayError(
            f"tailscale ip -4 returned nothing (rc={r.returncode}); refusing to "
            f"bind a non-tailscale interface")
    return ip[0].strip()


def _generate_master_key():
    import secrets as _secrets
    return "sk-" + _secrets.token_urlsafe(32)


def setup_model_gateway_service(alias_map_path=None):
    """Install + start the model-gateway systemd --user service on the
    controller. LOUD on every failure with a manual-command fallback; never
    claims success it did not observe. UNVERIFIED by the authoring lane — the
    supervisor runs this via `airuleset.py install` after the key is pasted."""
    print("  Installing model-gateway (LiteLLM) systemd --user service")
    amp = _expand(alias_map_path or ALIAS_MAP_PATH)
    config = load_alias_map(amp)
    if config is None:
        print(f"  model-gateway: alias map vanished ({amp}) — aborting install",
              file=sys.stderr)
        return False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. master key (generated once, 0600, in the durable secret tree).
    mk = _expand(MASTER_KEY_FILE)
    if not mk.exists():
        try:
            mk.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(mk), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_generate_master_key() + "\n")
            print(f"  Generated master key: {mk}")
        except OSError as e:
            print(f"  model-gateway: could NOT create master key {mk}: {e}",
                  file=sys.stderr)
            return False

    # 2. render config.yaml (no secrets) + the EnvironmentFile (real reader).
    YAML_PATH.write_text(render_gateway_yaml(config), encoding="utf-8")
    try:
        env_text = render_gateway_env(config, _read_secret_file, master_key_file=mk)
    except OSError as e:
        print(f"  model-gateway: a provider key file is missing/unreadable "
              f"({e}); paste it with `airuleset.py secret request` then re-run "
              f"install", file=sys.stderr)
        return False
    fd = os.open(str(ENV_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(env_text)
    os.chmod(str(ENV_PATH), 0o600)
    print(f"  Wrote {YAML_PATH} + {ENV_PATH} (0600)")

    # 3. spend-logger callback module (importable via the unit's PYTHONPATH).
    if SPEND_LOGGER_TEMPLATE.exists():
        SPEND_LOGGER_DEST.write_text(
            SPEND_LOGGER_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        print(f"  model-gateway: spend-logger template missing "
              f"({SPEND_LOGGER_TEMPLATE}) — spend tracking will be inert",
              file=sys.stderr)

    # 4. venv + pinned litellm[proxy].
    if not _ensure_venv():
        return False

    # 5. render + write the unit, enable, health-check.
    try:
        host_ip = _tailscale_ip()
    except ModelGatewayError as e:
        print(f"  model-gateway: {e}", file=sys.stderr)
        return False
    SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_DEST.write_text(render_gateway_unit(host_ip), encoding="utf-8")
    print(f"  Wrote unit: {SERVICE_DEST} (bind {host_ip}:{GATEWAY_PORT})")
    if not _enable_service():
        return False
    return _health_check(host_ip)


def _ensure_venv():
    """Create the venv + `pip install litellm[proxy]==<pin>`. LOUD on failure."""
    litellm_bin = VENV_DIR / "bin" / "litellm"
    try:
        if not litellm_bin.exists():
            VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            print(f"  Creating venv {VENV_DIR} + installing "
                  f"litellm[proxy]=={LITELLM_PIN} (one-time, slow)")
            r = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)],
                               capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                print(f"  model-gateway: venv create FAILED: {r.stderr.strip()}",
                      file=sys.stderr)
                return False
        pip = VENV_DIR / "bin" / "pip"
        r = subprocess.run([str(pip), "install", "--no-input",
                           f"litellm[proxy]=={LITELLM_PIN}"],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            print(f"  model-gateway: pip install litellm FAILED: "
                  f"{r.stderr.strip()[-500:]}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  model-gateway: venv/pip step FAILED: {e}", file=sys.stderr)
        return False
    return True


def _enable_service():
    manual = (f"    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user "
              f"daemon-reload\n    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl "
              f"--user enable --now {SERVICE_NAME}")
    try:
        subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)
    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  model-gateway: daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    rc, _o, err = _run_systemctl(["enable", "--now", SERVICE_NAME])
    if rc != 0:
        print(f"  model-gateway: enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    _run_systemctl(["restart", SERVICE_NAME])
    return True


def _health_check(host_ip):
    """Liveliness probe (GET /health/liveliness) + one /v1/messages echo through
    `pilot-fast`. LOUD on failure. docs.litellm.ai/docs/proxy/health +
    /docs/anthropic_unified."""
    import time
    import urllib.request
    base = f"http://{host_ip}:{GATEWAY_PORT}"
    live = f"{base}/health/liveliness"
    ok = False
    last_err = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(live, timeout=3) as resp:
                if resp.status == 200:
                    ok = True
                    break
        except Exception as e:
            last_err = e
        time.sleep(1)
    if not ok:
        print(f"  model-gateway: {live} did NOT answer 200 (last error: "
              f"{last_err}) — check `systemctl --user status {SERVICE_NAME}`",
              file=sys.stderr)
        return False
    print(f"  model-gateway: live at {base} (GET /health/liveliness 200)")
    if not _probe_messages(base):
        print("  model-gateway: the /v1/messages echo through pilot-fast did "
              "NOT succeed — the model is up but a request failed; check the "
              "key + the alias target", file=sys.stderr)
        return False
    print("  model-gateway: /v1/messages echo through pilot-fast OK")
    return True


def _probe_messages(base):
    import urllib.request
    try:
        master = _read_secret_file(MASTER_KEY_FILE)
    except OSError as e:
        print(f"  model-gateway: cannot read master key for the probe: {e}",
              file=sys.stderr)
        return False
    body = json.dumps({
        "model": "pilot-fast",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "reply OK"}],
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", master)
    req.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  model-gateway: /v1/messages probe error: {e}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_status(args):
    config = load_alias_map(getattr(args, "map_path", None))
    if config is None:
        print("model-gateway: not configured (no alias map at "
              f"{_expand(ALIAS_MAP_PATH)})")
        print("  configure with: airuleset.py model-gateway set "
              "<alias> <provider/model>")
        return 0
    print(f"model-gateway: LiteLLM pin litellm[proxy]=={LITELLM_PIN}, "
          f"service {SERVICE_NAME} = {_service_state()}")
    print("  aliases:")
    for alias in sorted(config["aliases"]):
        print(f"    {alias:<14} -> {config['aliases'][alias]}")
    print("  providers:")
    for provider in sorted(config["providers"]):
        print(f"    {provider:<14} key_file {_key_file_for(config, provider)}")
    yp = _expand(YAML_PATH)
    print(f"  config yaml: {yp} "
          f"({'present' if yp.exists() else 'ABSENT — run install'})")
    print(f"  spend log:   {_expand(SPEND_LOG_PATH)} "
          f"({len(load_spend_records())} records)")
    return 0


def _cmd_set(args):
    parts = list(getattr(args, "mg_args", []) or [])
    if len(parts) != 2:
        raise ModelGatewayError("usage: model-gateway set <alias> <provider/model>")
    alias, target = parts
    _config, added = set_alias(alias, target, path=getattr(args, "map_path", None))
    print(f"model-gateway: {alias} -> {target}")
    if added:
        print(f"  NOTE: new provider `{added}` added with key file "
              f"{_conventional_key_file(added)}. Paste the key with "
              f"`airuleset.py secret request` and run `airuleset.py install` on "
              f"the controller so the gateway can use it.")
    return 0


def _cmd_spend(args):
    records = load_spend_records()
    rows = aggregate_spend(records, since=getattr(args, "since", None))
    if not rows:
        print(f"model-gateway: no spend recorded yet (log "
              f"{_expand(SPEND_LOG_PATH)})")
        return 0
    print(format_spend_table(rows))
    return 0


def cmd_model_gateway(args):
    """`model-gateway {status|set <alias> <provider/model>|spend [--since ISO]}`."""
    action = getattr(args, "mg_action", "status") or "status"
    try:
        if action == "status":
            return _cmd_status(args)
        if action == "set":
            return _cmd_set(args)
        if action == "spend":
            return _cmd_spend(args)
    except ModelGatewayError as e:
        print(f"model-gateway: {e}", file=sys.stderr)
        return 2
    print(f"model-gateway: unknown action {action!r}", file=sys.stderr)
    return 2
