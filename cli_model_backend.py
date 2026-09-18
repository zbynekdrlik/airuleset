"""cli_model_backend — the IMPLEMENTER-window model backend registry + marker.

#1060 Lane L3a re-scopes #1062 Lane L2. A single per-box marker
(``~/.claude/airuleset-model-backend.json``) names the controller LiteLLM model
gateway (Lane L1, ``cli_model_gateway``) + the tier ALIASES (``impl-main`` /
``impl-sub`` / ``impl-fast``) + the key file for ONE box's IMPLEMENTER window.
The marker is the ONLY per-box state; without it a box is byte-identical to
today. It is written from the controller through the normal push/install path
out of a controller-side REGISTRY (``~/.claude/airuleset-model-backends.json``,
``{"<user>@<host>": {...}}``) maintained by ``airuleset.py model-backend
set|clear <target> …``; the per-target deploy step (cli_remote) ships the marker
plus the gateway master key (copied 0600 into the target's
``~/.secrets/model-gateway.key``).

The 2026-09-18 miva1 incident (#1062 correction): the marker used to feed
``settings.json`` env (shared by EVERY Claude the user runs), so it flipped the
stream's MAIN Claude onto the gateway/DeepSeek — violating the "design by Fable"
rule (#1061). L3a removes the settings.json wiring entirely; the marker is now
consumed by ONLY TWO surfaces, both scoped to the implementer WINDOW:

  * ``cli_claude_scripts`` renders the ``claude-impl`` launcher, which reads the
    marker at SHELL time and exports the gateway backend env
    (``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` read from ``key_file`` /
    ``ANTHROPIC_MODEL`` etc + ``AIRULESET_ROLE=implementer``) for THAT process
    only — the main window keeps ``MANAGED_MODEL`` + the OAuth login untouched.
  * ``statusbar.account_email_segment`` shows ``impl:<main>`` in the implementer
    window (gated on ``AIRULESET_ROLE``).

The controller-side deploy shipper (``cli_remote.provision_model_backend_markers``
/ ``remove_model_backend_markers``) + ``model-backend set|clear|status`` are
unchanged; the design gate (``gates.designdispatch``) + ``airuleset.py handoff``
are Fable-only again (the main authors the design + hands off).

This leaf is STDLIB ONLY and carries NO module-level ``import airuleset`` (same
leaf discipline as cli_config / cli_model_gateway — a module-level import would
crash CLI mode, #433 L-E).

#1060 Lane L3a (re-scopes #1062 L2). Design comment: airuleset#1060 (the Fable main).
"""

import json
import os
import re
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
# The token file ON the target box (the gateway master key, shipped 0600). The
# marker's default `key_file`; the claude-impl launcher cats it at shell time.
TARGET_KEY_FILE = "~/.secrets/model-gateway.key"

# Default tier aliases — MUST agree with cli_model_gateway.default_alias_map().
DEFAULT_MAIN = "impl-main"
DEFAULT_SUB = "impl-sub"
DEFAULT_FAST = "impl-fast"

# The controller's tailscale IPv4 (machine-identities: ar.newlevel.media /
# MagicDNS `airuleset`); the gateway binds it (cli_model_gateway._tailscale_ip).
CONTROLLER_TAILSCALE_IP = "100.101.214.103"

# The five string fields a valid marker MUST carry.
_MARKER_FIELDS = ("base_url", "key_file", "main", "sub", "fast")

# #1062 L2 review A-M1 (security hardening, kept for #1060 L3a): a marker field
# flows into a shell context — the claude-impl launcher exports each field into
# the child env (`export ANTHROPIC_BASE_URL="$_mb_base_url"` etc, read at shell
# time), and the key_file path is cat'd — so a quote/backtick/whitespace/metachar
# in ANY field could break out of the quoting or inject a command. Reject those
# chars in every field. This is deliberately permissive for real values (a
# base_url has `:/.`, a key_file has `/~.-`, an alias/model id has `/._:@-`); it
# bans only the shell-dangerous set. The launcher ADDITIONALLY parses the marker
# with python3 + shlex.quote (defence in depth), but this write-time gate keeps a
# malformed hand-edit out of the marker in the first place.
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
    box or injecting into the claude-impl launcher's shell exports."""
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
    default_base_url(); main/sub/fast → the impl(ementer) tier aliases; key_file →
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
              "[--main impl-main --sub impl-sub --fast impl-fast])")
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
    # Ship the on-target removal (rm marker + key + any stale apiKeyHelper script
    # from the old L2) now — "clear removes both". Deferred imports keep this leaf
    # standalone-importable.
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
    print("  model-backend: marker + key + stale apiKeyHelper removed on %s. If "
          "the box was flipped under the old L2, its shared settings.json still "
          "carries the L2-era ANTHROPIC_* env until the target's NEXT install "
          "self-heals it (apply_managed_settings_defaults pops the L2-era env + "
          "the managed apiKeyHelper UNCONDITIONALLY, #1060 L3a) — run "
          "`airuleset.py push` to revert it now." % target)
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
