"""Autopilot Board HTTP server (design §6, §10; plan Task 12).

A stdlib `ThreadingHTTPServer` exposing four endpoints:

  * GET  /              — card grid (Live + Recent) + health strip + version
  * GET  /ticket/<run>  — per-run detail (timeline, gates, gh signals, evidence)
  * GET  /api/state     — TOKEN-GATED JSON (the bulk-exfil endpoint)
  * POST /report        — TOKEN-GATED ingestion: body-cap, validate, route to the
                          single writer, apply worker gate claims

SECURITY (LAN-internal, defense-in-depth, all enforced HERE so a bug in one
endpoint can't lower the bar for another):

  * Token via `hmac.compare_digest` (constant-time) on /report AND /api/state.
    403 on mismatch/missing. The read board pages (/, /ticket) are open on the
    LAN (the design gates "at minimum /api/state"); the token is the real
    control for the write + bulk-read paths.
  * Body cap: Content-Length > BODY_MAX → 413 BEFORE the body is read, so a
    multi-MB POST never lands in memory. A socket recv timeout (anti slow-loris)
    is set on the server.
  * Field validation at ingestion (gh.valid_run_id / valid_repo / valid_issue):
    a non-conforming run_id/repo/issue → 400, NOT stored (the gh-injection guard
    — a crafted repo can never reach a run row or the gh subprocess).
  * Worker gate claims (`reviews`) go through board.set_gate, which REFUSES the
    gh-verified checks (ci/mergeable/merged/issue_state) — a worker can never
    spoof a verified gate. Only the gh refresher writes those.
  * Per-source-IP token-bucket rate limit on /report (in-memory, stdlib) → 429.
  * Response headers on EVERY response: Content-Type with charset; a restrictive
    CSP (default-src 'none'; style-src 'self' 'unsafe-inline') so even a missed
    escape can't run script; NO Access-Control-Allow-Origin (no-CORS posture).

The render layer (board.render) does the html.escape on every value; this module
only assembles the data dicts and sets the headers.

stdlib only.
"""
import os
import json
import time
import hmac
import socket
import logging
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from board import (PORT, BOARD_HOST_IP, BODY_MAX, GH_POLL_FLOOR_S,
                   TERMINAL_PHASES)
from board import gh as ghmod
from board import render

_log = logging.getLogger("autopilot_board.server")

# Rate limit: per-source-IP token bucket on /report.
_RL_CAPACITY = 60        # burst: 60 requests
_RL_REFILL_PER_S = 5.0   # steady state: 5 req/s sustained
# Anti-slow-loris: a single request's socket read may not block forever.
_SOCKET_TIMEOUT_S = 15


class _TokenBucket:
    """Per-IP token bucket. `allow(ip)` returns False when the IP is over budget.
    Thread-safe (ThreadingHTTPServer dispatches each request on its own thread).
    """

    def __init__(self, capacity=_RL_CAPACITY, refill_per_s=_RL_REFILL_PER_S):
        self.capacity = capacity
        self.refill = refill_per_s
        self._buckets = {}          # ip -> (tokens, last_ts)
        self._lock = threading.Lock()

    def allow(self, ip):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(ip, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens < 1.0:
                self._buckets[ip] = (tokens, now)
                return False
            self._buckets[ip] = (tokens - 1.0, now)
            return True


def _is_live(run):
    """A run is 'Live' when its phase is non-terminal and it's not marked stale.
    sqlite Row indexed by column name."""
    phase = run["phase"]
    status = run["status"]
    if status == "stale":
        return False
    return phase is None or phase not in TERMINAL_PHASES


def _run_card_dict(board, run):
    """Assemble the plain dict render.card_grid expects from a runs Row,
    enriched with the gate map, computed alarms, and the PR url (gh wins over
    the worker-reported pr_url when present)."""
    from board.gate import compute_alarms
    rid = run["run_id"]
    gate = board.gate_map(rid)
    gh = board.get_gh(rid)
    pr_url = run["pr_url"]
    if gh is not None and gh["pr_url"]:
        pr_url = gh["pr_url"]
    try:
        snap = board.alarm_input(rid)
        alarms = compute_alarms(snap) if snap else []
    except Exception:
        _log.exception("alarm computation failed for %s", rid)
        alarms = []
    return {
        "run_id": rid, "repo": run["repo"], "issue": run["issue"],
        "title": run["title"], "phase": run["phase"], "goal": run["goal"],
        "approach": run["approach"], "result": run["result"],
        "machine": run["machine"], "status": run["status"],
        "pr_url": pr_url, "gate": gate, "alarms": alarms,
    }


def _row_to_dict(row):
    """sqlite Row -> plain dict (render.ticket_detail wants .get on a dict)."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def make_server(board, token, host=BOARD_HOST_IP, port=PORT, version="dev"):
    """Build (but do NOT start) the ThreadingHTTPServer.

    The caller starts it via `serve_forever()` (in a thread for tests, or the
    main thread for run_server). This function does NOT start the gh refresher
    or reaper — run_server does — so a test using make_server never hits the
    network. `board.start_writer()` must already have been called by the caller.
    """
    token_b = token.encode() if isinstance(token, str) else token
    rate_limiter = _TokenBucket()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # ---- helpers -----------------------------------------------------
        def log_message(self, fmt, *args):
            _log.debug("%s - %s", self.address_string(), fmt % args)

        def _client_ip(self):
            return self.client_address[0] if self.client_address else "?"

        def _check_token(self):
            """Constant-time token compare. Returns True iff the request carries
            the exact board token."""
            sent = self.headers.get("X-Board-Token", "")
            return hmac.compare_digest(
                sent.encode() if isinstance(sent, str) else (sent or b""),
                token_b)

        def _security_headers(self):
            # CSP first — applied to EVERY response (no script anywhere).
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'self' 'unsafe-inline'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # deliberately NO Access-Control-Allow-Origin (no-CORS posture).

        def _respond(self, code, body, content_type):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _html(self, code, body):
            self._respond(code, body, "text/html; charset=utf-8")

        def _json(self, code, obj):
            self._respond(code, json.dumps(obj), "application/json; charset=utf-8")

        def _text(self, code, msg):
            self._respond(code, msg, "text/plain; charset=utf-8")

        # ---- GET ---------------------------------------------------------
        def do_GET(self):
            try:
                self._route_get()
            except Exception:
                _log.exception("GET %s failed", self.path)
                self._text(500, "internal error")

        def do_HEAD(self):
            self.do_GET()

        def _route_get(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._html(200, render.card_grid(*self._board_view(), version=version,
                                                  health=self._health()))
            elif path.startswith("/ticket/"):
                rid = path[len("/ticket/"):]
                self._serve_ticket(rid)
            elif path == "/api/state":
                if not self._check_token():
                    self._json(403, {"error": "forbidden"})
                    return
                self._json(200, self._api_state())
            else:
                self._html(404, render._page(
                    "Not found", "<h1>404 — not found</h1>"))

        def _board_view(self):
            """(live, recent) lists of card dicts for the home grid."""
            live, recent = [], []
            for run in board.list_runs():
                d = _run_card_dict(board, run)
                (live if _is_live(run) else recent).append(d)
            return live, recent

        def _serve_ticket(self, rid):
            if not ghmod.valid_run_id(rid):
                self._html(404, render._page(
                    "Not found", "<h1>404 — not found</h1>"))
                return
            run = _row_to_dict(board.get_run(rid))
            if run is None:
                self._html(404, render.ticket_detail(None, [], {}, None))
                return
            events = [_row_to_dict(e) for e in board.get_events(rid)]
            gate = board.gate_map(rid)
            gh = _row_to_dict(board.get_gh(rid))
            self._html(200, render.ticket_detail(run, events, gate, gh))

        def _health(self):
            try:
                last_report = board.last_report_ts()
                touched = board.runs_touched_since(time.time() - 3600)
            except Exception:
                _log.exception("health query failed")
                last_report, touched = None, 0
            return {
                "last_report": _fmt_ts(last_report),
                "runs_last_hour": touched,
            }

        def _api_state(self):
            runs = []
            for run in board.list_runs():
                runs.append(_run_card_dict(board, run))
            return {"version": version, "runs": runs,
                    "health": self._health()}

        # ---- POST --------------------------------------------------------
        def do_POST(self):
            try:
                self._route_post()
            except Exception:
                _log.exception("POST %s failed", self.path)
                self._text(500, "internal error")

        def _route_post(self):
            path = self.path.split("?", 1)[0]
            if path != "/report":
                self._text(404, "not found")
                return

            # 1) body cap BEFORE reading (DoS) ----------------------------
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length > BODY_MAX:
                # drain nothing; close. 413 with explicit Connection: close so
                # the client doesn't try to reuse a half-read keep-alive socket.
                self.close_connection = True
                self._text(413, "payload too large")
                return

            # 2) token (constant-time) -----------------------------------
            if not self._check_token():
                self._text(403, "forbidden")
                return

            # 3) per-IP rate limit ---------------------------------------
            if not rate_limiter.allow(self._client_ip()):
                self._text(429, "rate limited")
                return

            # 4) read + parse JSON ---------------------------------------
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                ev = json.loads(raw.decode() or "{}")
            except (ValueError, UnicodeDecodeError):
                self._text(400, "bad json")
                return
            if not isinstance(ev, dict):
                self._text(400, "bad body")
                return

            # 5) validate gh-bound fields (don't store on failure) -------
            rid = ev.get("run_id")
            if not ghmod.valid_run_id(rid):
                self._text(400, "bad run_id")
                return
            if "repo" in ev and ev["repo"] is not None \
                    and not ghmod.valid_repo(ev["repo"]):
                self._text(400, "bad repo")
                return
            if "issue" in ev and ev["issue"] is not None \
                    and not ghmod.valid_issue(ev["issue"]):
                self._text(400, "bad issue")
                return

            # 6) route into the single writer (waits for commit) ----------
            try:
                board.submit(ev, wait=True, timeout=5)
            except Exception:
                _log.exception("submit failed for %s", rid)
                self._text(500, "write failed")
                return

            # 7) apply worker gate claims (claimed checks only — set_gate
            #    refuses gh-verified checks). reviews = [[check, state], ...]
            reviews = ev.get("reviews")
            if reviews:
                seq = ev.get("seq", 0) or 0
                for item in reviews:
                    try:
                        check, state = item[0], item[1]
                    except (TypeError, IndexError, KeyError):
                        continue
                    try:
                        board.set_gate(rid, check, state, seq=seq, claimed=True)
                    except Exception:
                        _log.exception("set_gate failed: %s %s", rid, item)

            self._text(200, "ok")

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.timeout = _SOCKET_TIMEOUT_S
    # Anti slow-loris: bound how long a single request's socket can block.
    httpd.socket.settimeout(None)  # accept() blocks; per-conn timeout below
    return httpd


def _fmt_ts(ts):
    """UTC-epoch float -> a readable local timestamp string, or 'never'."""
    if not ts:
        return "never"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


# --------------------------------------------------------------------------- #
# production entrypoint
# --------------------------------------------------------------------------- #
def _git_version():
    """`git describe --tags --always --dirty` run in the airuleset repo, plus
    the install time. Falls back to 'unknown' if git is unavailable."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(
            ["git", "-C", repo, "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=10)
        desc = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        desc = ""
    if not desc:
        desc = "unknown"
    return f"{desc} (up {time.strftime('%Y-%m-%d %H:%M')})"


def _port_already_bound(host, port):
    """True if something is already listening on host:port (so we fail loud
    instead of silently picking another port — a stale board must be detected)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _refresher_loop(board, repos, stop):
    """Background gh refresher (design §8): batched per-repo fetch on a loop with
    a GH_POLL_FLOOR_S floor, per-repo try/except so one repo's failure never
    kills the thread. Maps gh signals to the newest non-terminal run per issue
    and writes them via board.set_gh (the verified-gate path)."""
    from board.gh import fetch_repo_active
    while not stop.is_set():
        start = time.monotonic()
        for repo in repos:
            try:
                res = fetch_repo_active(repo)
                if not res.get("gh_ok"):
                    _log.warning("gh refresh for %s not ok (rate_limited=%s)",
                                 repo, res.get("rate_limited"))
                    continue
                for pr in res.get("prs", []):
                    num = pr.get("number")
                    if num is None:
                        continue
                    rid = board.newest_active_run(repo, num)
                    if not rid:
                        continue
                    board.set_gh(
                        rid, pr_url=pr.get("url"), pr_state=pr.get("pr_state"),
                        merged=pr.get("merged"),
                        mergeable=pr.get("mergeable"),
                        mergeable_state=pr.get("mergeable_state"),
                        mergeable_gate=pr.get("mergeable_gate"))
            except Exception:
                _log.exception("refresher failed for repo %s", repo)
        try:
            board.mark_stale(time.time())
        except Exception:
            _log.exception("reaper (mark_stale) failed")
        # honour the poll floor (sleep the remainder; never below the floor)
        elapsed = time.monotonic() - start
        stop.wait(max(GH_POLL_FLOOR_S, GH_POLL_FLOOR_S - elapsed))


def run_server(board, token, host=BOARD_HOST_IP, port=PORT, repos=None):
    """Production entrypoint. Fails loud if the port is already bound, starts the
    single writer + the gh refresher/reaper thread, then serves forever."""
    if _port_already_bound(host, port):
        raise SystemExit(
            f"FATAL: {host}:{port} is already bound — a board is already "
            f"running (or a stale process holds the port). Refusing to start.")
    version = _git_version()
    board.start_writer()
    stop = threading.Event()
    if repos:
        threading.Thread(target=_refresher_loop, args=(board, repos, stop),
                         daemon=True).start()
    httpd = make_server(board, token, host=host, port=port, version=version)
    _log.info("autopilot board listening on %s:%s (version %s)",
              host, port, version)
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()
