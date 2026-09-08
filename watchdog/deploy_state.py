"""Deploy-state PRODUCER for W/ops-wait deploy-parked tickets (#944 part 2).

Reads the ``deploy_state`` declaration from ``projects-registry.json`` for
the repo at ``cwd``, fetches main version + per-instance PROD version +
deploy-window state, and returns the result for ``_deploy_watch_classify``
in ``ops_wait_recheck.py``.

This module is the PRODUCER that wires the part-1 seam.  It performs
read-only I/O (git, HTTP, subprocess) but all I/O is bounded (timeout)
and fail-safe (any error -> None, never a false GATEKEEPER-ACTION).

Per-version dedup (F5): a state file ``~/.claude/deploy-watch/<slug>.json``
tracks ``{ticket: {version: str, ts: float}}`` so one (ticket, version)
pair fires at most ONE GATEKEEPER-ACTION.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from watchdog import release_watch

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry reader
# ---------------------------------------------------------------------------

_REGISTRY_FILENAME = "projects-registry.json"


def _load_registry(registry_path):
    """Load and return the projects-registry list, or [] on any error."""
    try:
        with open(registry_path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        log.debug("deploy_state: cannot load registry %s", registry_path,
                  exc_info=True)
        return []


def _repo_slug_from_cwd(cwd):
    """Derive ``owner/name`` from ``git remote get-url origin`` at ``cwd``.

    Returns e.g. ``"zbynekdrlik/odoo-erp"`` or None on any error.
    """
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd), capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        url = out.stdout.strip()
        # git@github.com:owner/repo.git  or  https://github.com/owner/repo.git
        m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def _find_project(registry, cwd):
    """Find the registry entry matching ``cwd``.

    Tries path match first (exact); falls back to matching ``github_repo``
    against the git remote origin slug.  Returns the dict entry or None.
    """
    cwd_resolved = os.path.realpath(os.path.expanduser(str(cwd)))
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        p = entry.get("path")
        if not p:
            continue
        entry_resolved = os.path.realpath(os.path.expanduser(str(p)))
        if entry_resolved == cwd_resolved:
            return entry
    # Fallback: match by github_repo slug
    slug = _repo_slug_from_cwd(cwd)
    if slug:
        slug_lower = slug.lower()
        for entry in registry:
            if not isinstance(entry, dict):
                continue
            gh = entry.get("github_repo", "")
            if isinstance(gh, str) and gh.lower() == slug_lower:
                return entry
    return None


# ---------------------------------------------------------------------------
# Main-version reader
# ---------------------------------------------------------------------------

_MANIFEST_VERSION_RX = re.compile(
    r"""['"]version['"]\s*:\s*['"]([^'"]+)['"]""")


def _strip_odoo_prefix(raw):
    """Strip the Odoo ``19.0.`` prefix from a version like ``19.0.2.266.0``.

    Returns the stripped version or the original if no prefix detected.
    """
    parts = raw.split(".")
    if len(parts) >= 4 and parts[0].isdigit() and parts[1] == "0":
        return ".".join(parts[2:])
    return raw


def read_main_version(cwd, version_file):
    """Read the version string from ``origin/main:<version_file>`` via git.

    Falls back to reading the working-tree file if git show fails.
    Returns None on any error (fail-safe).
    """
    if not version_file:
        return None
    try:
        out = subprocess.run(
            ["git", "show", "origin/main:%s" % version_file],
            cwd=str(cwd), capture_output=True, text=True, timeout=10)
        content = out.stdout if out.returncode == 0 else None
    except Exception:
        log.debug("deploy_state: git show failed for %s", version_file,
                  exc_info=True)
        content = None
    if content is None:
        # Fallback: read from working tree
        try:
            fp = Path(str(cwd)) / version_file
            content = fp.read_text()
        except Exception:
            log.debug("deploy_state: file read failed for %s", version_file,
                      exc_info=True)
            return None
    # Parse: try manifest-style first, then plain version string
    m = _MANIFEST_VERSION_RX.search(content)
    if m:
        return _strip_odoo_prefix(m.group(1))
    # Try as a plain version string (first line, stripped)
    for line in content.splitlines():
        s = line.strip()
        if s and release_watch.parse_version_tuple(s) is not None:
            return s
    return None


# ---------------------------------------------------------------------------
# PROD-version reader
# ---------------------------------------------------------------------------

def read_prod_version(version_source, timeout=15):
    """Read the PROD version from a declared ``version_source``.

    ``version_source`` is a dict with:
      - ``type``: ``"manifest_url"`` -- HTTP GET, parse for version
      - ``url``: the URL to fetch

    Or a simple string URL (shorthand for manifest_url).

    Returns the version string, or None on any error (fail-safe).
    """
    if isinstance(version_source, str):
        version_source = {"type": "manifest_url", "url": version_source}
    if not isinstance(version_source, dict):
        return None

    src_type = version_source.get("type", "manifest_url")
    url = version_source.get("url")

    if src_type == "manifest_url" and url:
        return _fetch_version_from_url(url, timeout=timeout)

    return None


def _fetch_version_from_url(url, timeout=15):
    """HTTP GET ``url``, parse the response for a version string.

    Tries JSON first (looks for ``version`` / ``installed_version`` keys),
    then falls back to regex on the raw body.
    Returns None on any error.
    """
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "airuleset-deploy-watch/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        log.debug("deploy_state: HTTP fetch failed for %s", url,
                  exc_info=True)
        return None

    # Try JSON
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            for key in ("version", "installed_version", "server_version"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return _strip_odoo_prefix(v.strip())
    except (json.JSONDecodeError, TypeError):
        log.debug("deploy_state: JSON parse failed for %s, trying regex",
                  url)

    # Fallback: regex on body
    m = _MANIFEST_VERSION_RX.search(body)
    if m:
        return _strip_odoo_prefix(m.group(1))

    return None


# ---------------------------------------------------------------------------
# Deploy-window reader
# ---------------------------------------------------------------------------

def evaluate_deploy_window(window_spec, now_dt=None):
    """Evaluate a deploy-window spec for the current time.

    ``window_spec`` is a dict:
      - ``open_start``: "HH:MM" (inclusive)
      - ``open_end``:   "HH:MM" (exclusive; wraps past midnight)
      - ``timezone``:   e.g. "Europe/Bratislava"
      - ``everyday``:   bool -- if True, window applies every day
                         (default False = weeknight only, weekend fully open)

    Returns ``(window_open, window_passed)`` booleans.
    ``window_passed`` is True when the MOST RECENT window has closed
    and a new one hasn't opened yet.
    Errors -> ``(False, False)`` (fail-safe: no action).
    """
    if not isinstance(window_spec, dict):
        return False, False
    try:
        import datetime
        import zoneinfo
        tz_name = window_spec.get("timezone", "Europe/Bratislava")
        tz = zoneinfo.ZoneInfo(tz_name)
        if now_dt is None:
            now_dt = datetime.datetime.now(tz)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=tz)
        else:
            now_dt = now_dt.astimezone(tz)

        start_s = window_spec.get("open_start", "")
        end_s = window_spec.get("open_end", "")
        if not start_s or not end_s:
            return False, False

        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        now_min = now_dt.hour * 60 + now_dt.minute
        everyday = window_spec.get("everyday", False)
        is_weekend = now_dt.weekday() >= 5  # Sat=5, Sun=6

        # Weekend-open for weeknight-only windows
        if not everyday and is_weekend:
            return True, False  # whole weekend is open

        # Wraparound window (e.g. 23:00-05:00)
        if start_min > end_min:
            window_open = now_min >= start_min or now_min < end_min
        else:
            window_open = start_min <= now_min < end_min

        # window_passed = we are PAST the end of the window and BEFORE
        # the start of the next one
        window_passed = not window_open
        return window_open, window_passed

    except Exception:
        log.debug("deploy_state: window evaluation failed", exc_info=True)
        return False, False


# ---------------------------------------------------------------------------
# Per-version dedup (F5)
# ---------------------------------------------------------------------------

_DEDUP_DIR_NAME = "deploy-watch"


def _dedup_path(home=None):
    """Return the dedup state directory path."""
    h = home or os.path.expanduser("~")
    return os.path.join(h, ".claude", _DEDUP_DIR_NAME)


def _dedup_file(repo_slug, home=None):
    """Return the dedup state file for a repo."""
    d = _dedup_path(home)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", repo_slug)
    return os.path.join(d, "%s.json" % safe)


def _load_dedup(repo_slug, home=None):
    """Load dedup state: {ticket_number_str: {version, ts}}."""
    fp = _dedup_file(repo_slug, home)
    try:
        with open(fp) as f:
            return json.load(f)
    except Exception:
        log.debug("deploy_state: dedup load failed for %s", repo_slug)
        return {}


def _save_dedup(repo_slug, state, home=None):
    """Save dedup state."""
    fp = _dedup_file(repo_slug, home)
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        log.warning("deploy_state: dedup save failed for %s", repo_slug,
                    exc_info=True)


def should_fire(repo_slug, ticket_number, main_version, home=None):
    """Return True if this (ticket, version) should fire a GATEKEEPER-ACTION.

    Returns False (and does NOT update state) if this exact pair already
    fired. Updates the dedup state on True.
    """
    ds = _load_dedup(repo_slug, home)
    key = str(ticket_number)
    entry = ds.get(key)
    if isinstance(entry, dict) and entry.get("version") == main_version:
        return False  # already fired for this version
    # Fire and record
    ds[key] = {"version": main_version, "ts": time.time()}
    _save_dedup(repo_slug, ds, home)
    return True


def clear_stale_dedup(repo_slug, main_version, home=None):
    """Clear dedup entries for versions BEHIND ``main_version``."""
    ds = _load_dedup(repo_slug, home)
    if not ds:
        return
    changed = False
    for key in list(ds):
        entry = ds[key]
        if not isinstance(entry, dict):
            del ds[key]
            changed = True
            continue
        old_v = entry.get("version")
        ahead = release_watch.main_ahead_of_prod(main_version, old_v)
        if ahead:
            del ds[key]
            changed = True
    if changed:
        _save_dedup(repo_slug, ds, home)


# ---------------------------------------------------------------------------
# PRODUCER -- the public API
# ---------------------------------------------------------------------------

def fetch_deploy_state(cwd, registry_path=None, home=None, now_dt=None):
    """The deploy-state producer: reads the registry, fetches versions and
    windows, returns a list of per-instance dicts for ``_deploy_watch_classify``.

    Returns ``None`` when the project has no ``deploy_state`` declaration
    (undeclared -> decision-log + no action).
    Returns ``[]`` when declared but all instances fail (fail-safe).

    Each returned dict:
      ``{instance, main_version, prod_version, window_open, window_passed}``
    """
    if registry_path is None:
        # Default: the registry in the airuleset repo
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            _REGISTRY_FILENAME)

    registry = _load_registry(registry_path)
    project = _find_project(registry, cwd)
    if project is None:
        return None

    ds_decl = project.get("deploy_state")
    if not isinstance(ds_decl, dict):
        return None

    instances = ds_decl.get("instances")
    if not isinstance(instances, list) or not instances:
        return None

    version_file = ds_decl.get("main_version_file")
    main_ver = read_main_version(cwd, version_file)

    results = []
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "unknown")
        vs = inst.get("version_source")
        prod_ver = read_prod_version(vs) if vs else None
        window_spec = inst.get("deploy_window")
        w_open, w_passed = evaluate_deploy_window(
            window_spec, now_dt=now_dt)
        results.append({
            "instance": name,
            "main_version": main_ver,
            "prod_version": prod_ver,
            "window_open": w_open,
            "window_passed": w_passed,
        })

    return results if results else None


def make_deploy_state_fetch(registry_path=None, home=None, now_dt=None):
    """Return a ``deploy_state_fetch(cwd)`` callable for the watchdog seam.

    This is the ADAPTER between the per-project multi-instance producer
    and the ops_wait_recheck seam contract (which expects a single dict
    or a list).

    The returned callable:
      - Reads the registry for ``cwd``
      - Returns a list of per-instance dicts (each with
        ``main_version, prod_version, window_open, window_passed``)
      - Returns None on undeclared / all-fail (the seam's fail-safe)
    """
    def _fetch(cwd):
        return fetch_deploy_state(
            cwd, registry_path=registry_path, home=home, now_dt=now_dt)
    return _fetch
