"""#1399 — the ``odoo_json2`` PROD version source of ``watchdog.deploy_state``.

company_base ``latest_version`` over ``/json/2/ir.module.module/search_read``
with the stream's own handover bearer key (odoo-erp ``scripts/prod_facts.py``).
A box without the key file does not judge that instance (``source_configured``).
Stdlib only; never prints or logs the key.
"""
import json
import logging
import os
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


def _key_path(version_source):
    """The expanded ``key_file`` of an ``odoo_json2`` source ("" when none)."""
    return os.path.expanduser(str(version_source.get("key_file") or ""))


def source_configured(version_source):
    """False when this box cannot read the source at all: an ``odoo_json2``
    source whose bearer key file is not on this box (#1399: a stream holds the
    key of its own instance only). Such an instance is not judged here."""
    if isinstance(version_source, dict) and \
            version_source.get("type") == "odoo_json2":
        path = _key_path(version_source)
        return bool(path) and os.path.isfile(path)
    return True


def _fetch_odoo_json2(base_url, key_path, timeout=15, opener=None,
                      strip=lambda v: v):
    """company_base ``latest_version`` over ``/json/2`` with a bearer key; None
    on a missing key, any HTTP/JSON error or an empty result (never a guess)."""
    try:
        key = Path(key_path).read_text().strip()
    except OSError:
        return None
    if not key:
        return None
    req = urllib.request.Request(
        "%s/json/2/ir.module.module/search_read" % base_url.rstrip("/"),
        data=json.dumps({"domain": [["name", "=", "company_base"]],
                         "fields": ["latest_version"]}).encode("utf-8"),
        method="POST",
        headers={"Authorization": "bearer %s" % key,
                 "Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "airuleset-deploy-watch/1.0"})
    try:
        with (opener or _no_redirect_open)(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read(65536).decode("utf-8", errors="replace"))
    except Exception:
        log.debug("deploy_state: /json/2 read failed for %s", base_url,
                  exc_info=True)
        return None
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        v = data[0].get("latest_version")
        if isinstance(v, str) and v.strip():
            return strip(v.strip())
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would copy the bearer header to the new
    host (review #1399). A redirect then reads as a failed read (None)."""

    def redirect_request(self, *args, **kwargs):
        return None


def _no_redirect_open(req, timeout=15):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
