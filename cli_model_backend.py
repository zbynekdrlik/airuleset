"""cli_model_backend — Lane L2 of #1062: the per-box MODEL BACKEND switch.

A single per-box marker (``~/.claude/airuleset-model-backend.json``) flips ONE
box's Claude Code onto the controller's LiteLLM model gateway (Lane L1,
``cli_model_gateway``) with tier ALIASES + an ``apiKeyHelper`` token. The marker
is the ONLY per-box state; without it a box is byte-identical to today. The
marker is written from the controller through the normal push/install path out
of a controller-side REGISTRY (``~/.claude/airuleset-model-backends.json``,
``{"<user>@<host>": {...}}``) maintained by ``airuleset.py model-backend
set|clear <target> …``; the per-target deploy step (cli_remote) ships the marker
plus the gateway master key (copied 0600 into the target's
``~/.secrets/model-gateway.key``).

This leaf is STDLIB ONLY and carries NO module-level ``import airuleset`` (same
leaf discipline as cli_config / cli_model_gateway — a module-level import would
crash CLI mode, #433 L-E). Consumed by:

  * ``cli_config.apply_managed_settings_defaults`` — settings.json env + model +
    apiKeyHelper on a marker box; the marker-derived env is popped on a
    no-marker box (self-heal after ``clear``).
  * ``cli_claude_scripts.render_claude_launch_script`` — the launcher ``--model``
    on a marker box = the marker's ``main`` alias.
  * ``gates.designdispatch`` — the box's configured ``main`` alias is accepted as
    the design model WHEN the marker exists (every other box still requires the
    Fable id).
  * ``watchdog.usage.check_usage`` — a marker box skips the OAuth usage read and
    records ``backend=gateway`` instead of logging an error.
  * ``statusbar.account_email_segment`` — a marker box shows ``gw:<main>``.
  * ``cli_remote.provision_model_backend_markers`` — the per-target deploy shipper.

#1062 Lane L2. Design comment: airuleset#1062 (the Fable main).
"""

import json
import os
import re
import shlex
from pathlib import Path


class ModelBackendError(Exception):
    """A user-facing model-backend CLI/registry error (never a stack trace)."""


# --- Paths (all under ~/.claude, the same marker family as airuleset-box-class /
#     airuleset-playwright-optout) ------------------------------------------- #
CLAUDE_DIR = Path.home() / ".claude"
# The per-box marker — the ONLY per-box state (design item 5).
MARKER_PATH = CLAUDE_DIR / "airuleset-model-backend.json"
# The controller-side registry: {"<user>@<host>": {base_url, key_file, main, sub, fast}}.
REGISTRY_PATH = CLAUDE_DIR / "airuleset-model-backends.json"
# The managed apiKeyHelper script: prints the marker's key_file to stdout so the
# token itself never lands in settings.json (design item 6).
APIKEY_HELPER_PATH = CLAUDE_DIR / "airuleset-model-gateway-apikey.sh"

# The token file ON the target box (the gateway master key, shipped 0600). The
# marker's default `key_file`; the apiKeyHelper script cats it.
TARGET_KEY_FILE = "~/.secrets/model-gateway.key"

# Default tier aliases — MUST agree with cli_model_gateway.default_alias_map().
DEFAULT_MAIN = "pilot-main"
DEFAULT_SUB = "pilot-sub"
DEFAULT_FAST = "pilot-fast"

# The controller's tailscale IPv4 (machine-identities: ar.newlevel.media /
# MagicDNS `airuleset`); the gateway binds it (cli_model_gateway._tailscale_ip).
CONTROLLER_TAILSCALE_IP = "100.101.214.103"

# API_TIMEOUT_MS on a gateway box (design item 6): 900_000 (Claude Code default
# is 600_000) — a cheap third-party model routed through the proxy can be slower
# to first byte than Anthropic first-party.
API_TIMEOUT_MS = "900000"

# The env keys the marker OWNS: present when the marker exists, popped when it is
# absent (self-heal of a cleared box; a never-markered box never had them, so the
# pop is a no-op and the no-marker path stays byte-identical to today). NB
# CLAUDE_CODE_SUBAGENT_MODEL is set fleet-wide today (opus tier) and only
# OVERRIDDEN (never removed) by a marker, so it is deliberately NOT in this set.
BACKEND_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
    "API_TIMEOUT_MS",
)

# The five string fields a valid marker MUST carry.
_MARKER_FIELDS = ("base_url", "key_file", "main", "sub", "fast")

# #1062 L2 review A-M1 (security hardening): a marker field flows into a shell
# context — the `main` alias into the launcher's `--model '{{MANAGED_MODEL}}'`
# (single-quoted), the key_file/base_url into settings.json + the apiKeyHelper
# script — so a quote/backtick/whitespace/metachar in ANY field could break out
# of the quoting or inject a command. Reject those chars in every field. This is
# deliberately permissive for real values (a base_url has `:/.`, a key_file has
# `/~.-`, an alias/model id has `/._:@-`); it bans only the shell-dangerous set.
_MARKER_UNSAFE_RE = re.compile(r"""['"`\s;|&$\\<>(){}]""")


def _expand(p):
    return Path(os.path.expanduser(str(p)))


def default_base_url():
    """The controller gateway's base URL (`http://<tailscale-ip>:<GATEWAY_PORT>`).
    Reads L1's `GATEWAY_PORT` AND — since `set` runs ON the controller (where the
    L1 gateway binds) — L1's live `_tailscale_ip()` so the marker points at the
    address the gateway is ACTUALLY bound to, never a hard-coded IP that could
    drift (review B-MINOR). Falls back to the pinned CONTROLLER_TAILSCALE_IP on
    any failure (off-controller, no tailscale, an unexpected error)."""
    try:
        from cli_model_gateway import GATEWAY_PORT
        port = int(GATEWAY_PORT)
    except Exception:
        port = 4000
    try:
        from cli_model_gateway import _tailscale_ip
        live = (_tailscale_ip() or "").strip()
        ip = live or CONTROLLER_TAILSCALE_IP
    except Exception:
        # off-controller / no tailscale / error → the pinned fallback (the
        # expected path anywhere but the controller; not a real failure).
        ip = CONTROLLER_TAILSCALE_IP
    return "http://%s:%d" % (ip, port)


# --------------------------------------------------------------------------- #
# The per-box marker
# --------------------------------------------------------------------------- #
def _valid_marker(d):
    """True iff `d` is a complete marker — every field a non-empty string with no
    shell-dangerous character (review A-M1). An INCOMPLETE or unsafe marker is
    treated as absent (fail to today's behaviour) rather than half-flipping the
    box or injecting into the launcher/apiKeyHelper/settings."""
    if not isinstance(d, dict):
        return False
    for k in _MARKER_FIELDS:
        v = d.get(k)
        if not (isinstance(v, str) and v) or _MARKER_UNSAFE_RE.search(v):
            return False
    return True


def marker_path(home=None):
    if home is not None:
        return Path(home) / ".claude" / "airuleset-model-backend.json"
    return _expand(MARKER_PATH)


def load_marker(path=None, home=None):
    """The per-box model-backend marker dict, or None when it is
    absent/unreadable/malformed/incomplete. NEVER raises — a read failure is a
    no-backend classification (the byte-identical-to-today direction). `home`
    roots the default path for tests; `path` overrides it outright."""
    p = _expand(path) if path is not None else marker_path(home)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if _valid_marker(d) else None


def backend_env(marker):
    """The env vars a marker box adds to settings.json (PURE). Keys are exactly
    BACKEND_ENV_KEYS; every value is a string. CLAUDE_CODE_SUBAGENT_MODEL and
    apiKeyHelper are applied by the caller (they override/are top-level keys)."""
    return {
        "ANTHROPIC_BASE_URL": marker["base_url"],
        "ANTHROPIC_MODEL": marker["main"],
        "ANTHROPIC_DEFAULT_OPUS_MODEL": marker["main"],
        "ANTHROPIC_DEFAULT_SONNET_MODEL": marker["sub"],
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": marker["fast"],
        "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
        "API_TIMEOUT_MS": API_TIMEOUT_MS,
    }


def launcher_model(default, home=None):
    """The launcher `--model` for THIS box: the marker's `main` alias when a
    marker exists, else `default` (the caller passes airuleset.MANAGED_MODEL)."""
    mb = load_marker(home=home)
    return mb["main"] if mb else default


# --------------------------------------------------------------------------- #
# The apiKeyHelper managed script (prints the token; the token never lands in
# settings.json — design item 6)
# --------------------------------------------------------------------------- #
def apikey_helper_path(home=None):
    if home is not None:
        return Path(home) / ".claude" / "airuleset-model-gateway-apikey.sh"
    return _expand(APIKEY_HELPER_PATH)


def apikey_helper_script(key_file):
    """The managed apiKeyHelper script body (PURE). Prints the gateway key file
    to stdout so Claude Code authenticates to the controller LiteLLM gateway
    with a Bearer token instead of the Max OAuth login. The key-file's ABSOLUTE
    path is baked in at render time (Claude Code execs the helper via a shell
    that may not expand `~`), shell-quoted; the token itself is never stored in
    settings.json."""
    kf = shlex.quote(os.path.expanduser(str(key_file)))
    return (
        "#!/bin/sh\n"
        "# Managed by airuleset (cli_model_backend, #1062 L2). Prints the\n"
        "# model-gateway master key so Claude Code authenticates to the\n"
        "# controller LiteLLM gateway (ANTHROPIC_BASE_URL). The key file is\n"
        "# 0600; the token is never stored in settings.json. Do not edit — a\n"
        "# push regenerates this file.\n"
        "umask 077\n"
        "exec cat -- %s\n" % kf
    )


def maybe_setup_model_backend(home=None, marker=None):
    """cmd_install entry point (runs on EVERY box's local install). When the
    per-box marker exists → materialize the managed apiKeyHelper script (0755)
    that prints the marker's `key_file`. When it does NOT exist → remove a stale
    managed script (self-heal of a cleared box). Returns a short status line.
    Never raises (the caller wraps it non-fatally). Writes NO marker and NO
    secret — only the apiKeyHelper script (which contains a `cat <path>`, never a
    token value)."""
    mb = marker if marker is not None else load_marker(home=home)
    dest = apikey_helper_path(home=home)
    if not mb:
        try:
            if dest.exists():
                dest.unlink()
                return ("  model-backend: no marker — removed stale apiKeyHelper "
                        "script %s" % dest)
        except OSError as e:
            return "  model-backend: could not remove stale apiKeyHelper (%s)" % e
        return "  model-backend: no marker (no-op)"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 0755: the helper must be executable; it contains no secret.
        fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(apikey_helper_script(mb["key_file"]))
        os.chmod(str(dest), 0o755)
    except OSError as e:
        return "  model-backend: could NOT write apiKeyHelper %s: %s" % (dest, e)
    return "  model-backend: apiKeyHelper script installed (%s), main=%s" % (
        dest, mb["main"])


# --------------------------------------------------------------------------- #
# The controller-side registry: {"<user>@<host>": {base_url, key_file, main,
# sub, fast}}. `set`/`clear` maintain it; the deploy step ships each entry as
# the target's marker (cli_remote.provision_model_backend_markers).
# --------------------------------------------------------------------------- #
def registry_path(home=None):
    if home is not None:
        return Path(home) / ".claude" / "airuleset-model-backends.json"
    return _expand(REGISTRY_PATH)


def load_registry(path=None, home=None):
    """The registry dict, or {} when absent/malformed. Never raises."""
    p = _expand(path) if path is not None else registry_path(home)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_registry(reg, path=None, home=None):
    p = _expand(path) if path is not None else registry_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(str(tmp), str(p))


def marker_from_entry(entry):
    """The per-box marker dict shipped to a target, from its registry entry.
    Validates the entry has the five fields (an incomplete entry raises)."""
    if not _valid_marker(entry):
        raise ModelBackendError(
            "registry entry is incomplete — needs %s" % ", ".join(_MARKER_FIELDS))
    return {k: entry[k] for k in _MARKER_FIELDS}


def set_target(target, base_url=None, main=None, sub=None, fast=None,
               key_file=None, path=None, home=None):
    """Add/replace `target`'s registry entry. Defaults: base_url →
    default_base_url(); main/sub/fast → the pilot aliases; key_file →
    TARGET_KEY_FILE. Returns the written entry. Does NOT ship anything — the
    next push's deploy step ships the marker (design item 5)."""
    if not target or "@" not in target:
        raise ModelBackendError(
            "target must be `<user>@<host>` (e.g. miva1@subdev), got %r" % target)
    reg = load_registry(path=path, home=home)
    reg[target] = {
        "base_url": base_url or default_base_url(),
        "key_file": key_file or TARGET_KEY_FILE,
        "main": main or DEFAULT_MAIN,
        "sub": sub or DEFAULT_SUB,
        "fast": fast or DEFAULT_FAST,
    }
    # Fail fast on a malformed hand-built entry before it is persisted.
    marker_from_entry(reg[target])
    _write_registry(reg, path=path, home=home)
    return reg[target]


def clear_target(target, path=None, home=None):
    """Drop `target` from the registry. Returns True iff it was present. The
    marker + key + marker-derived env on the target itself are removed by the
    deploy step's removal leg / the target's next install (no-marker path)."""
    reg = load_registry(path=path, home=home)
    existed = target in reg
    reg.pop(target, None)
    _write_registry(reg, path=path, home=home)
    return existed


# --------------------------------------------------------------------------- #
# The gateway master key (shipped to the target as ~/.secrets/model-gateway.key).
# Read ONLY at deploy time on the controller — the authoring lane never runs it.
# --------------------------------------------------------------------------- #
def read_gateway_master_key(source=None):
    """Read the L1 gateway master key (cli_model_gateway.MASTER_KEY_FILE) and
    return its stripped value, or None when the file is absent/unreadable. ONLY
    the per-target deploy step calls this (on the controller); tests inject a
    tmp `source`."""
    if source is None:
        try:
            from cli_model_gateway import MASTER_KEY_FILE
            source = MASTER_KEY_FILE
        except Exception:
            return None
    try:
        with open(os.path.expanduser(str(source)), encoding="utf-8") as fh:
            v = fh.read().strip()
        return v or None
    except OSError:
        return None


def marker_json(entry):
    """The exact JSON text written to a target's marker file (deploy stdin). No
    trailing newline — the deploy helper appends one (`value + "\\n"`), so this
    avoids a double newline in the shipped file (review A-M4)."""
    return json.dumps(marker_from_entry(entry), indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# CLI: airuleset.py model-backend {status | set <target> ... | clear <target>}
# --------------------------------------------------------------------------- #
def _cmd_status(args):
    reg = load_registry()
    if not reg:
        print("model-backend: no registry — no box is flipped to the gateway")
        print("  (flip one with: airuleset.py model-backend set <user@host> "
              "[--main pilot-main --sub pilot-sub --fast pilot-fast])")
        # Also report THIS box's own marker state.
        mb = load_marker()
        print("  this box: %s" % ("marker present (main=%s, base_url=%s)"
                                  % (mb["main"], mb["base_url"]) if mb
                                  else "no marker (Anthropic backend)"))
        return 0
    print("model-backend registry (%d target(s)):" % len(reg))
    for tgt in sorted(reg):
        e = reg[tgt]
        print("  %-20s main=%s sub=%s fast=%s base_url=%s key_file=%s"
              % (tgt, e.get("main"), e.get("sub"), e.get("fast"),
                 e.get("base_url"), e.get("key_file")))
    mb = load_marker()
    print("  this box: %s" % ("marker present (main=%s)" % mb["main"] if mb
                              else "no marker (Anthropic backend)"))
    return 0


def _cmd_set(args):
    a = list(getattr(args, "mb_args", []) or [])
    if not a:
        raise ModelBackendError(
            "usage: model-backend set <user@host> [--main A --sub B --fast C "
            "--base-url URL --key-file PATH]")
    target = a[0]
    entry = set_target(
        target,
        base_url=getattr(args, "base_url", None),
        main=getattr(args, "main", None),
        sub=getattr(args, "sub", None),
        fast=getattr(args, "fast", None),
        key_file=getattr(args, "key_file", None))
    print("model-backend: %s → main=%s sub=%s fast=%s base_url=%s"
          % (target, entry["main"], entry["sub"], entry["fast"], entry["base_url"]))
    print("  Written to the registry. The NEXT `airuleset.py push` ships the "
          "marker + the gateway master key (0600) to the target BEFORE its "
          "install runs, so that install flips the box onto the gateway in the "
          "same push (a best-effort install re-run also fires right after "
          "delivery). Verify with `claude -p 'reply OK'` on the target.")
    return 0


def _cmd_clear(args):
    a = list(getattr(args, "mb_args", []) or [])
    if not a:
        raise ModelBackendError("usage: model-backend clear <user@host>")
    target = a[0]
    existed = clear_target(target)
    if not existed:
        print("model-backend: %s was not in the registry (no-op)." % target)
        return 0
    print("model-backend: %s dropped from the registry." % target)
    if getattr(args, "registry_only", False):
        # HONEST (review B-MAJOR3): the push shipper only ADDS to registered
        # targets — it does NOT reconcile removals — so the marker on the target
        # is NOT removed by a later push. --registry-only is for an UNREACHABLE
        # target: the registry is cleaned, but the marker must be removed by a
        # reachable `clear` (no --registry-only) or by hand.
        print("  --registry-only: the registry entry is dropped, but the marker "
              "+ key on %s are NOT removed (a push never reconciles a removal). "
              "Run `airuleset.py model-backend clear %s` (reachable) to remove "
              "them, or remove them on the target by hand." % (target, target))
        return 0
    # Ship the on-target removal (rm marker + key + apiKeyHelper script) now —
    # "clear removes both". Deferred imports keep this leaf standalone-importable.
    try:
        import cli_remote  # noqa: E402 — CLI runtime only
        entries = [h for h in cli_remote._deployable_hosts()
                   if h.get("name") == target]
    except Exception as e:
        print("  model-backend: could not resolve %s for removal (%s); drop "
              "recorded — run `airuleset.py push` to reconcile the target."
              % (target, e), file=__import__("sys").stderr)
        return 0
    if not entries:
        print("  model-backend: %s is not a known deployable host; registry "
              "drop recorded (nothing to ssh)." % target)
        return 0
    fails = cli_remote.remove_model_backend_markers(entries)
    if fails:
        print("  model-backend: removal FAILED on %s (%s) — re-run "
              "`airuleset.py push` to reconcile."
              % (target, ", ".join("%s:%s" % f for f in fails)),
              file=__import__("sys").stderr)
        return 2
    print("  model-backend: marker + key + apiKeyHelper removed on %s. Its "
          "settings.json still points at the gateway until the target's NEXT "
          "install reverts it (no marker → the backend env + apiKeyHelper are "
          "popped) — run `airuleset.py push` to revert it now." % target)
    return 0


def cmd_model_backend(args):
    """`model-backend {status | set <target> [...] | clear <target>}`."""
    action = getattr(args, "mb_action", "status") or "status"
    try:
        if action == "status":
            return _cmd_status(args)
        if action == "set":
            return _cmd_set(args)
        if action == "clear":
            return _cmd_clear(args)
    except ModelBackendError as e:
        print("model-backend: %s" % e, file=__import__("sys").stderr)
        return 2
    print("model-backend: unknown action %r" % action, file=__import__("sys").stderr)
    return 2
