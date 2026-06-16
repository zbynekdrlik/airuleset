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
                   QUEUE_ITEM_TTL_S, TERMINAL_PHASES)
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


def _dedupe_newest_per_issue(runs):
    """Keep only the newest run per (repo, issue) from a list ALREADY ordered
    newest-first (list_runs is updated_at DESC). Runs with a NULL repo or issue
    have no dedupe key and are all kept. Collapses the duplicate cards a
    re-dispatched autopilot worker creates (a fresh run per cold-start)."""
    seen = set()
    out = []
    for run in runs:
        repo, issue = run["repo"], run["issue"]
        if repo and issue is not None:
            key = (repo, issue)
            if key in seen:
                continue
            seen.add(key)
        out.append(run)
    return out


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
        "updated_str": _fmt_ts(run["updated_at"]),
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
        # Anti slow-loris: BaseHTTPRequestHandler applies this as the request
        # socket's timeout, so a client that opens a connection and dribbles
        # bytes (or never sends the body) is dropped after _SOCKET_TIMEOUT_S
        # rather than tying up a worker thread forever.
        timeout = _SOCKET_TIMEOUT_S

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
                live, recent = self._board_view()
                try:
                    queue = board.get_queue()
                    # convert sqlite Rows to plain dicts for the renderer
                    queue = [{"repo": r["repo"], "issue": r["issue"],
                               "title": r["title"], "position": r["position"]}
                              for r in queue]
                except Exception:
                    _log.exception("get_queue failed")
                    queue = []
                self._html(200, render.card_grid(live, recent, version=version,
                                                  health=self._health(),
                                                  queue=queue))
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
            """(live, recent) lists of card dicts for the home grid, collapsed to
            the NEWEST run per (repo, issue). A re-dispatched autopilot worker
            mints a fresh run (SendMessage continuation is unavailable without
            the agent-teams flag, so the supervisor cold-starts a new worker),
            which otherwise showed 2-3 duplicate cards for one issue. Older
            same-issue runs stay in the DB / ticket history; only the grid
            collapses them."""
            live, recent = [], []
            for run in _dedupe_newest_per_issue(board.list_runs()):
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
            h = {
                "last_report": _fmt_ts(last_report),
                "runs_last_hour": touched,
            }
            # gh refresher degraded (rate limit / outage)? surface it so the
            # header shows the STALE banner render already supports, instead of
            # the board silently serving gh signals that stopped updating.
            since = getattr(board, "_gh_degraded_since", None)
            if since:
                h["gh_stale"] = True
                h["gh_stale_since"] = _fmt_ts(since)
            return h

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

            # 5a) planned-queue body: kind="queue" routes to set_queue ------
            if ev.get("kind") == "queue":
                return self._handle_queue(ev)

            # 5b) validate gh-bound fields (don't store on failure) -------
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
                ok = board.submit(ev, wait=True, timeout=5)
            except Exception:
                _log.exception("submit failed for %s", rid)
                self._json(500, {"error": "write failed"})
                return
            if not ok:
                _log.error("board write failed or timed out for %s", rid)
                self._json(500, {"error": "write failed"})
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

        def _handle_queue(self, ev):
            """Handle POST /report with kind="queue". Validates repo + each
            item's issue number, scrubs/caps titles, then calls board.set_queue
            directly (same pattern as set_gate/set_gh — WAL lets multiple
            readers and one writer coexist; set_queue opens its own connection).
            Token + body-cap are already enforced by the outer _route_post."""
            from board.reporter import scrub as _scrub
            repo = ev.get("repo")
            if not ghmod.valid_repo(repo):
                self._text(400, "bad repo")
                return
            raw_items = ev.get("items")
            if not isinstance(raw_items, list):
                self._text(400, "items must be a list")
                return
            items = []
            for entry in raw_items:
                try:
                    issue = entry[0]
                    title = entry[1] if len(entry) > 1 else ""
                except (TypeError, IndexError, KeyError):
                    self._text(400, "bad item format")
                    return
                if not ghmod.valid_issue(issue):
                    self._text(400, f"bad issue: {issue}")
                    return
                # scrub titles the same way the reporter does for event fields
                safe_title = _scrub(str(title) if title is not None else "")
                items.append((int(issue), safe_title))

            try:
                board.set_queue(repo, items)
            except Exception:
                _log.exception("set_queue failed for repo %s", repo)
                self._json(500, {"error": "write failed"})
                return
            self._text(200, "ok")

    httpd = ThreadingHTTPServer((host, port), Handler)
    # daemon_threads so per-request threads don't block shutdown/server_close.
    httpd.daemon_threads = True
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


def _maybe_prune(board, repo, open_issue_numbers):
    """Prune closed issues from the queue for `repo` — but ONLY when the
    open-issues set is non-empty.

    An empty set means the gh fetch returned nothing (transient rate-limit,
    all-closed repo, or a parse hiccup).  Pruning against an empty set would
    delete every queue row, wiping the user's planned-work view.  The truthiness
    guard (`if open_issue_numbers:`) skips the prune when the set is empty so the
    queue survives transient gh outages intact.  An explicit supervisor
    `queue_report(repo, [])` is the correct way to clear the queue intentionally."""
    if not open_issue_numbers:
        return
    try:
        board.prune_queue(repo, open_issue_numbers)
    except Exception:
        _log.exception("prune_queue failed for repo %s", repo)


def _apply_repo_refresh(board, repo, res):
    """Map ONE `fetch_repo_active` result onto the board — the testable unit of
    the refresher's per-repo work. Caller guarantees res['gh_ok'] is True.

      * prune the queue to the open-issue set,
      * fan each PR's gh signals to the RUN of the issue(s) it CLOSES — keyed on
        the closed issue number, NEVER the PR number (PR# != issue#, the bug
        that left gh_state empty so nothing was ever reconciled),
      * record OPEN/CLOSED issue_state per matched run,
      * finalise as done every run whose issue is no longer open (the main
        false-STALE_ABANDONED fix) — SKIPPED when the open list was capped, since
        'not open' is then unreliable (merged-PR reconcile still covers those).
    """
    open_issues = res.get("open_issues", set()) or set()
    capped = res.get("issues_capped", False)
    _maybe_prune(board, repo, open_issues)
    for pr in res.get("prs", []):
        for issue_num in pr.get("closes", []):
            rid = board.newest_active_run(repo, issue_num)
            if not rid:
                continue
            board.set_gh(
                rid, pr_url=pr.get("url"), pr_state=pr.get("pr_state"),
                merged=pr.get("merged"), mergeable=pr.get("mergeable"),
                mergeable_state=pr.get("mergeable_state"),
                mergeable_gate=pr.get("mergeable_gate"),
                issue_state=("CLOSED" if issue_num not in open_issues else "OPEN"))
    if not capped:
        board.reconcile_closed(repo, open_issues, time.time())


def _refresher_loop(board, static_repos, stop):
    """Background gh refresher (design §8): batched per-repo fetch on a loop with
    a GH_POLL_FLOOR_S floor, per-repo try/except so one repo's failure never
    kills the thread. Maps gh signals to the newest non-terminal run per issue
    and writes them via board.set_gh (the verified-gate path).

    The repo set is recomputed at the top of EACH cycle as:
      static_repos (BOARD_REPOS env) UNION board.distinct_repos()
    so a newly-reporting repo is picked up without a restart — no BOARD_REPOS
    config needed once any worker starts reporting."""
    from board.gh import fetch_repo_active, valid_repo
    while not stop.is_set():
        start = time.monotonic()
        # Recompute each cycle so newly-reporting repos are picked up immediately.
        try:
            db_repos = board.distinct_repos()
        except Exception:
            _log.exception("distinct_repos() failed; using static_repos only")
            db_repos = []
        seen = set()
        repos = []
        for r in list(static_repos) + db_repos:
            if r and r not in seen and valid_repo(r):
                seen.add(r)
                repos.append(r)
        cycle_failed = False
        for repo in repos:
            try:
                res = fetch_repo_active(repo)
                if not res.get("gh_ok"):
                    cycle_failed = True
                    _log.warning("gh refresh for %s not ok (rate_limited=%s)",
                                 repo, res.get("rate_limited"))
                    continue
                _apply_repo_refresh(board, repo, res)
            except Exception:
                cycle_failed = True
                _log.exception("refresher failed for repo %s", repo)
        # Track gh refresher health for the header banner: first failure stamps
        # _gh_degraded_since; a fully-clean cycle clears it.
        if cycle_failed:
            if getattr(board, "_gh_degraded_since", None) is None:
                board._gh_degraded_since = time.time()
        else:
            board._gh_degraded_since = None
        try:
            now = time.time()
            board.mark_stale(now)
            board.prune_queue_expired(now, QUEUE_ITEM_TTL_S)
        except Exception:
            _log.exception("reaper (mark_stale/prune) failed")
        # honour the poll floor: sleep only the REMAINDER to reach the floor
        # (max(0, …)); the old max(FLOOR, …) always slept a FULL floor on top of
        # the cycle, halving the refresh rate.
        elapsed = time.monotonic() - start
        stop.wait(max(0.0, GH_POLL_FLOOR_S - elapsed))


def run_server(board, token, host=BOARD_HOST_IP, port=PORT, repos=None):
    """Production entrypoint. Fails loud if the port is already bound, starts the
    single writer + the gh refresher/reaper thread, then serves forever.

    The refresher always starts (even when `repos` is empty) so it can discover
    repos from board.distinct_repos() as soon as workers begin reporting — no
    BOARD_REPOS env config required for the alarm/gate-verification to be live."""
    if _port_already_bound(host, port):
        raise SystemExit(
            f"FATAL: {host}:{port} is already bound — a board is already "
            f"running (or a stale process holds the port). Refusing to start.")
    version = _git_version()
    board.start_writer()
    board._gh_degraded_since = None   # gh-refresher health for the header banner
    stop = threading.Event()
    # Always start the refresher: it merges static_repos (BOARD_REPOS env) with
    # board.distinct_repos() each cycle, so it activates the moment any worker
    # reports — no BOARD_REPOS config needed.
    threading.Thread(target=_refresher_loop, args=(board, repos or [], stop),
                     daemon=True).start()
    httpd = make_server(board, token, host=host, port=port, version=version)
    _log.info("autopilot board listening on %s:%s (version %s)",
              host, port, version)
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()
