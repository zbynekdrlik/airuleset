"""cli_gh_rate_graphql — the GraphQL ``rateLimit`` OBJECT reading for the
fleet gh rate-guard (#1052).

Origin: controller 2026-09-16 20:26–20:30. The REST ``gh api rate_limit``
endpoint reported ``graphql remaining 5000/5000`` while the GraphQL
``rateLimit`` object reported ``remaining 0`` — the truth (every GraphQL-backed
``gh`` call failed until exactly the object's ``resetAt``). #1040's guard read
ONLY the REST bucket, so a GraphQL exhaustion passed unseen. This module is the
self-contained I/O + parse half of the fix — query the GraphQL ``rateLimit``
object and convert its ISO-8601 ``resetAt`` to an epoch. The COMPOSE decision
(take the LOWER of {REST, object} for the graphql resource) stays in
``cli_gh_rate.read_status``, which owns the resources shape.

Split out of ``cli_gh_rate.py`` to keep that module under the 1000-line
architecture cap (``architecture-first.md``). stdlib only (repo policy) and it
imports NOTHING from ``cli_gh_rate`` — the caller passes the timeout, the
internal-env marker, and a ``diag`` callback — so there is no import cycle and
no token can leak (it never logs env/argv/stdout).
"""
import datetime
import json
import os

# The 0-point GraphQL query for the rateLimit object: it answers even at
# remaining 0, so it is the authoritative reading during a GraphQL exhaustion
# (the REST rate_limit bucket lags/mis-reports then). It costs nothing against
# the GraphQL budget.
GRAPHQL_RATE_QUERY = "{ rateLimit { limit remaining resetAt used } }"


def iso_to_epoch(s):
    """Convert an ISO-8601 UTC timestamp (e.g. '2026-09-16T18:30:01Z') to an
    epoch int, or 0 on any parse error (fail-open — a 0 reset renders '?').
    stdlib only (repo policy)."""
    if not isinstance(s, str) or not s.strip():
        return 0
    text = s.strip()
    if text.endswith("Z"):                       # fromisoformat < 3.11 rejects 'Z'
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return 0


def fetch_graphql_object(run, real_gh, *, timeout, internal_env, diag):
    """Query the GraphQL ``rateLimit`` OBJECT and return
    ``{"remaining", "limit", "reset"}`` or None on ANY error (fail-open — the
    caller then keeps the REST reading). Runs with ``internal_env`` set so the
    gh shim never throttles it, with the same ``timeout`` as the REST fetch, and
    never logs env/argv/stdout (no token can leak). ``diag`` is the caller's
    best-effort diagnostic sink (``cli_gh_rate._diag``)."""
    if not real_gh:
        return None
    env = {**os.environ, internal_env: "1"}
    try:
        r = run([real_gh, "api", "graphql", "-f", "query=" + GRAPHQL_RATE_QUERY],
                capture_output=True, text=True, timeout=timeout, env=env)
    except Exception as e:
        diag("fetch-graphql-object", e)           # never logs stdout/stderr
        return None
    if getattr(r, "returncode", 1) != 0:
        diag("fetch-graphql-object",
             RuntimeError("rc=%s" % getattr(r, "returncode", "?")))
        return None
    try:
        data = json.loads(r.stdout or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    rl = (data.get("data") or {}).get("rateLimit") \
        if isinstance(data.get("data"), dict) else None
    if not isinstance(rl, dict) or "remaining" not in rl or "limit" not in rl:
        return None
    try:
        return {
            "remaining": int(rl["remaining"]),
            "limit": int(rl["limit"]),
            "reset": iso_to_epoch(rl.get("resetAt")),
        }
    except (ValueError, TypeError):
        return None
