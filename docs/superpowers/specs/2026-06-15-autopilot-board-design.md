# Autopilot Board — Design Spec

**Date:** 2026-06-15
**Status:** Approved design (post adversarial review — 6 critics, 55 findings, 38 must-fix folded in)
**Repo:** airuleset (Python stdlib only)

## 1. Purpose & motivation

The user runs `/autopilot` loops that dispatch a foreground `autopilot-worker` subagent per GitHub
issue. Today, tracking is **ephemeral** (agent strip), **scattered** (Discord pings, per-repo
`docs/autopilot-log.md`), with **no central live view** and **no review-gate audit**. The user loses
oversight of *what* each autopilot is doing, *how* it solved each ticket, and **cannot catch work
that was done/solved WRONG**.

**Goal:** one simple web board where every autopilot reports, per phase, concisely: which ticket,
its **goal**, the **approach** (how it plans to solve), the **result** (how it solved, when done),
the current **phase**, and whether **all issue-planner-mandated reviews actually ran** before merge
— with a loud alarm when something merged without the full green gate. The board's deepest job is
**catching wrong work**, so its integrity (can't be faked, can't silently lie) is the top priority.

## 2. Locked decisions (chosen by the user)

1. **Topology:** ONE always-on board on **dev1 (10.77.9.21)**; workers on dev1 AND dev2 report over the LAN.
2. **Home:** lives in the **airuleset repo**, **Python stdlib only** (`http.server`, `sqlite3`, `json`, `urllib`, `threading`, `queue`, `fcntl`, `hmac`, `secrets`, `subprocess` for `gh`, `socket`, `html`, `uuid`).
3. **Store/transport:** SQLite on dev1 = source of truth; workers POST JSON via a reporter; best-effort local queue + flush.
4. **Trust model:** the board **verifies GitHub itself** (objective signals) so workers can't fake `merged`/green; worker claims are shown as claims and cross-checked.
5. **Layout:** **card grid** — rich card per active ticket; browsable history; stale flag; "merged-with-incomplete-gate" alarm.

## 3. Architecture

```
worker / supervisor / manual session
        │  airuleset.py report ...  (fire-and-forget, exits 0, <=2s)
        ▼
  reporter (board/reporter.py)  ──POST /report (token)──►  board server (dev1:8787)
        │  on fail → local queue (flock, idempotent flush)        │
        │                                                         ├─ single writer thread → SQLite (WAL)
        ▼                                                         ├─ gh refresher thread → objective signals
  ~/.claude/autopilot-board-queue.jsonl                          └─ reaper/reconcile thread
                                                                          │
browser (user laptop, LAN) ◄── GET / (card grid) / /ticket/<run> / /api/state (token)
```

**Components (all in `board/`):**
- `board/reporter.py` — the client. Mints/threads run_id + seq, POSTs, manages the queue & circuit breaker. Standalone, absolute paths, no `gh`, no cwd dependence.
- `board/server.py` — `http.server` daemon: HTTP endpoints, single SQLite writer thread, gh refresher thread, reaper/reconcile thread, schema init + migrations.
- `airuleset.py` — gains `report` and `board` subcommands (thin wrappers over `board/`), `BOARD_HOST_IP` + `is_board_host()`, install branching, validate wiring, push-runs-tests.
- `hooks/autopilot-report.sh` — Stop-event hook emitting a skeleton heartbeat+phase so prose-forgetting still yields liveness.

## 4. Identity, sequencing & run lifecycle

### run_id (collision-proof, opaque)
- Format: `<repo>-<issue>-<epoch_ms>-<rand4>` (`rand4` = 4 hex chars; `uuid`/`secrets` stdlib).
- **Minted ONCE** by `report --start`, echoed to stdout, persisted to `~/.claude/autopilot-board-run` keyed by `repo+issue`. A `--resume` of the **same** in-flight attempt reuses it (read the marker); a genuinely new attempt gets a fresh suffix.
- The board treats run_id as **opaque** — never parses epoch out of it for ordering/identity.
- **Originator:** whoever first reports an event for `(repo,issue,attempt)` mints it — the worker (first report), the supervisor (a ticket-validator OVERCOME auto-close, no worker runs), or a manual session. First-writer-wins upsert is safe.

### seq (monotonic, per run)
- Client-side counter persisted next to the run marker (`~/.claude/autopilot-board-seq`), incremented per report, included in every event and queue line.
- Board applies a report to `runs`/`gate`/phase only if `seq >= stored seq` (monotonic guard) — **stale queue-flush replays never move state backwards**. `events` still inserts every event (for the timeline) but deduped by `event_id`.

### event_id (idempotency)
- `uuid4` minted when the reporter builds the JSON, persisted in the queue line so a retry reuses it. `events` has `UNIQUE(event_id)` + `INSERT OR IGNORE` → safe at-least-once.

### Timestamps (two clocks, explicit roles) — UTC epoch everywhere
- `event_ts` = **worker** clock at report (display + intra-run timeline tiebreak only).
- `recv_ts` = **board** clock at commit — the single authoritative clock for ALL cross-run/cross-machine logic: staleness, ordering across machines, alarm timing, "N min ago".
- `started_at` = the run's first `recv_ts`. Never compare a worker-stamped time against the board's `now()` (dev1/dev2 clock skew).

### Phase lifecycle & terminal states
- Phases: `validating, version-bump, implementing, RED, GREEN, CI, review, merge, deploy, done, asking-user, stopped, obsolete-closed`.
- **Terminal set:** `{done, stopped, obsolete-closed}`. Phase transitions are **rank-monotonic** server-side; never regress out of terminal except an explicit reopen. Drop queued events older than TTL (6h) on flush.

### Multiple attempts on one issue
- Distinct run_ids per attempt. The **Live grid shows only the newest non-terminal run per `(repo,issue)`**; older attempts auto-demote to `stale` and collapse under the active card's detail as "previous attempts". gh is polled **once per `(repo,issue)`**, result fanned to all its runs. Newest `started_at` owns the issue.

### Reaper & reconcile (board background thread)
- Any run with heartbeat older than its (phase-aware) threshold AND phase not in `{terminal, asking-user}` → `status=stale`, moved out of Live into Recent with a **"WORKER LOST (no terminal report)"** badge.
- Reconcile vs gh: if gh shows the issue closed + PR merged for a non-terminal run → auto-finalize as `done` ("completed per gh, no final worker report").
- Startup sweep reconciles stale Live cards after a reboot.

## 5. Reporting protocol (the reporter)

### CLI surface (documented = `airuleset.py report`, implemented in `board/reporter.py`)
- `report --start --repo <r> --issue <N> --title "..." [--is-bug-fix] [--has-deploy] [--merge-mode auto|manual]` → mints+echoes run_id, seeds the applicable gate rows at `pending`. Returns the id for `RUN=$(...)`.
- `report --run $RUN --phase <p> [--goal ..] [--approach ..] [--result ..] [--review <name>=ok|fail|pending] [--pr <url>] [--note ..]`
- `report --run $RUN --heartbeat` (no phase change, bumps liveness).
- `report --selftest` (POST a synthetic ping; print whether the board accepted it — run on dev1 AND dev2 to prove the LAN path after install).
- Fallback: if `--run` omitted, reporter reads the last run for `repo+issue` from the marker (defends against the worker forgetting to thread the id).

### Hard reliability contract
- **Fire-and-forget, exits 0 ALWAYS** — never blocks, never a reason to pause or interrupt the real work or asking the user.
- **Timeout:** connect+read = **2s**. **Circuit breaker:** a `~/.claude/autopilot-board-down` stamp (timestamp) → reporter skips the network for ~60s and only queues, so a down board costs one 2s probe per minute, not 2s per phase.
- **Queue** (`~/.claude/autopilot-board-queue.jsonl`):
  - `fcntl.flock(LOCK_EX|LOCK_NB)` around the whole read-flush-rewrite; if held, SKIP flush and just append (next reporter flushes).
  - Flush POSTs line N; remove THAT line only on HTTP 2xx (rewrite minus delivered, or `.tmp` rename) — never POST-all-then-truncate. On any failure mid-flush, stop, leave remainder, append current, exit 0.
  - **Flush cap K=200 events/invocation** (oldest first) so a long outage never turns one call into a 50×2s stall.
  - **Queue cap 5 MB / 5000 lines**, drop oldest, increment a dropped-counter surfaced on next successful POST. Drop queued events older than **6h** TTL on flush. Poison-line: skip+log a malformed line, never crash future reporters.
- Free-text fields (`goal/approach/result/note`): single-line (newlines stripped defensively), capped 2 KB, **secret-scrubbed** before send (strip `ghp_`, `github_pat_`, `AKIA`, `xox`, `-----BEGIN`, `Bearer `). The worker reports SHORT human summaries — **never** the raw `gh issue view` body/comments dump.

## 6. Board server

### Concurrency & storage integrity
- **Fixed port 8787** (module constant). Bind **`BOARD_HOST_IP` (10.77.9.21)** (scoped interface; the token gate is the real access control). If the port is already bound → **fail loud** (a stale board must be detected, not worked around).
- SQLite: `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`.
- **Single writer:** ALL writes (POST handlers AND gh refresher AND reaper) funnel through one **writer thread** fed by a `queue.Queue`. `ThreadingHTTPServer` for read concurrency; each thread opens its **own** short-lived connection (`check_same_thread=True`); never share a Connection across threads.
- **Atomic UPSERT** with `COALESCE(excluded.x, runs.x)` (never NULL-clobber a populated goal/approach/result) + the `seq` guard + phase-rank monotonic, in a **single statement** (no read-modify-write window).
- ACK only **after commit**; the reporter re-queues on **any non-2xx OR transport error** (a transient BUSY/500 is retried, never silently dropped).
- Schema init `CREATE TABLE IF NOT EXISTS` at startup. **Migration runner:** `schema_version` table + idempotent `ALTER TABLE ADD COLUMN` steps at startup (never edit an existing migration). Test schema-upgrade-on-existing-DB (open v1 DB, run startup, assert new columns + old rows survive).

### Endpoints
- `GET /` — card grid (Live + Recent/Done) + **health strip** + **version footer** + empty state. HTML meta auto-refresh ~10s.
- `GET /ticket/<run>` — detail: event timeline (ordered by `seq`, then `event_ts`), gate rows, gh signals, previous attempts.
- `GET /api/state` — JSON (**token-gated** — this is the bulk-exfil endpoint).
- `POST /report` — **token-gated**, validated, body ≤ **64 KB** (else 413), socket recv timeout (anti slow-loris), per-source-IP token-bucket **rate limit**.
- **Empty state:** "No autopilot runs yet — start one with /autopilot" + version label + self-check line ("listening on :8787, last report received: never").
- **Health strip:** board start time, last-successful-gh-refresh, count of runs touched in last hour, last-report-received ts; if gh failing → visible "gh signals STALE since <ts> (auth/rate-limit?)" banner.
- **Version label:** `git describe --tags --always --dirty` (run in airuleset repo) + install timestamp, in the footer on EVERY route and in `/api/state`. **Seed a git tag** so describe yields semver (accept `<sha> (<date>)` as documented fallback). (Satisfies `version-on-dashboard.md` for the board itself.)

## 7. Data model (SQLite)

```
runs(
  run_id PK, repo, issue, title, goal, approach, result,
  phase, status, machine, worker, seq,
  is_bug_fix BOOL, has_deploy BOOL, merge_mode TEXT,        -- gate applicability
  validated_evidence, merge_sha, main_ci_run,
  regression_red_test, regression_green_test, unverified, filed_issues,
  started_at, updated_at, pr_url
)
events(id PK, run_id, event_id UNIQUE, seq, phase, message, event_ts, recv_ts)
gate(run_id, check, state, source, detail, seq, recv_ts, UNIQUE(run_id, check))
gh_state(run_id PK, pr_url, pr_state, merged, ci_conclusion,
         mergeable, mergeable_state, issue_state, deploy_version, refreshed_at, gh_ok)
schema_version(version)
```
- `runs.result` keeps the human "how solved" summary; the structured evidence columns capture the worker's evidence block so `/ticket` shows the full audit trail (catches weak-evidence obsolete-closes, non-empty `unverified` at merge, etc.).
- `gate.source` is set **by the board from a fixed map**, never from the report payload (a worker can't upgrade its own claim to `verified`).

## 8. GitHub refresher (objective signals)

- Background thread, **batched**: one `gh pr list` / `gh issue list` per repo covering all that repo's active tickets; **interval floor ≥ 30s**.
- **argv only, never `shell=True`/`os.system`** (like `cmd_push`). **Allowlisted read-only subcommands**: `pr view/list`, `run list`, `issue view` — never mutating gh.
- **Backoff** on HTTP 403 / rate-limit (read `X-RateLimit-Reset`, pause). `try/except` per ticket so one failure never kills the thread.
- On gh non-zero / auth failure → set `gh_ok=false` sentinel + the visible STALE banner; never freeze silently. **Verify `gh auth status` at service start**, fail loud to the install step if the daemon can't auth (systemd env may differ from interactive).
- Map gh → the **newest non-terminal run** for `(repo,issue)`.
- **Untracked-merge discovery:** poll recent merged PRs per repo; a merge with no matching run → synthetic `untracked-merge` run + the same gate alarm (covers **manual / non-autopilot** merges — the riskiest path).
- **Reconcile** (§4): gh merged+closed & run non-terminal → promote to Done.

## 9. Review-gate & alarm logic

### Canonical required-gate enum (seeded `pending` at run start)
`ci, mergeable, plan_check, review, requesting_code_review, regression, deploy_verified, ticket_validated` (+ `supervisor_verify`).

- **Source fixed per check:** gh-derived (`ci, mergeable, merged, issue_state`) = `verified`, written only by the refresher; everything else = `claimed`, written only from reports.
- **`mergeable` gate ok** iff `mergeable==true AND mergeable_state=='CLEAN'`; UNSTABLE/BLOCKED/BEHIND/DIRTY → fail; `mergeable==null` → pending (GitHub still computing — re-poll).
- **`deploy_verified` is CLAIMED** (worker's Playwright DOM read; gh can't read a DOM). N/A when no deploy pipeline.
- **Applicability:** `regression` required iff `is_bug_fix`; `deploy_verified` required iff `has_deploy`. `merge_mode=manual` → a green-but-unmerged PR is a valid `done` (not alarmed). "No row"/pending/fail is **never** treated as pass.

### Alarms
1. **MERGED WITH INCOMPLETE GATE** — `gh.merged==true AND any APPLICABLE required gate not ok`. Computed from objective + claims; **a spoofed claim can NEVER clear it** (the alarm never depends on a claim being green to stay silent). Grace: while a gate is `pending` AND last report < N min → show **"verifying"**, not the red alarm; **event-driven re-eval** when late gate reports arrive (so a delayed-but-correct gate clears a transient alarm). Map gh to the newest non-terminal run to avoid attributing a merge to the wrong attempt.
2. **STALE / ABANDONED MID-GATE** — past the phase-aware heartbeat threshold, phase not terminal/asking-user (the *other* wrong-work mode: abandoned, not mis-merged).
3. **UNVERIFIED CLAIM AT MERGE** — reached merge/done with a required item still `source=claimed` (never `supervisor_verify`-confirmed).

### Heartbeat & staleness (phase-aware)
- Thresholds: `{CI, deploy}` generous (**30 min**), active `{implementing, RED, GREEN, review}` tight (**8 min**).
- **Dedicated heartbeat ≠ phase report.** Two tiers:
  - **Skeleton (guaranteed):** the `autopilot-report.sh` Stop hook + the gh poll keep a run alive/phased even if the worker reports nothing. The **supervisor** (`/goal` loop, outlives a hung worker) emits a heartbeat for the current run each turn.
  - **Enrichment (best-effort):** worker `report` calls add goal/approach/result/gate words.
- The worker's per-300s CI sleep-wake also emits `report --heartbeat`.

### Supervisor as reporter
- `/goal` loop reports the ticket-validator OVERCOME auto-close (`phase=obsolete-closed` + validator evidence) and, after its independent re-read of PR/CI/version, `report --review supervisor-verify=ok|fail --source verified` — making CLAIM-vs-VERIFIED visible on the card.

## 10. Security (LAN-internal, defense-in-depth, stdlib only)

- **Shared-secret token:** generated at first install (`secrets.token_urlsafe`, `0600` at `~/.claude/autopilot-board.token`, gitignored), deployed to dev2 by push. Reporter sends `X-Board-Token`; board 403s on mismatch (`hmac.compare_digest`). Gate **both** `POST /report` and the read endpoints (`/api/state`, `/ticket`, `/`) — at minimum `/api/state`.
- **Claims can never clear an alarm** (§9) — the structural safety net even if the token leaks.
- **gh injection:** validate at POST ingestion — `repo` matches `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`, `issue` positive int, `run_id` `^[A-Za-z0-9._-]+$`; reject non-conforming with 400, don't store. argv-only gh, allowlisted subcommands.
- **XSS:** `html.escape()` on EVERY interpolated value in every HTML response; PR URLs validated `https://github.com/...` before `href`. `Content-Type: text/html; charset=utf-8`; **CSP** `default-src 'none'; style-src 'self' 'unsafe-inline'` (no inline/external script). **No `Access-Control-Allow-Origin`** (no-CORS posture).
- **Secret leakage:** no-raw-capture policy + secret scrub (§5); token file + queue file gitignored.
- **DoS:** 64 KB body cap, socket recv timeout, per-IP rate limit, per-run event cap (keep latest 500, prune older), queue cap (§5).
- **systemd `--user` hardening** (dev1): `NoNewPrivileges=yes`, `ProtectSystem=strict` + `ReadWritePaths=` board data dir only, `ProtectHome=read-only` (data dir RW), `PrivateTmp=yes`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `MemoryMax=`/`TasksMax=` caps, `Restart=on-failure` + `RestartSec`/`StartLimitBurst` (crash-loop backoff). gh read-only (allowlist).

## 11. UI — card grid (chosen layout)

- **Live section:** one card per active `(repo,issue)` (newest attempt): `repo #issue` + phase badge, title, goal, approach, result-when-done, **gate chip row** (✓/✗/pending, claimed vs verified styling), machine (dev1/dev2), updated/heartbeat. Alarms render as a prominent red banner on the card.
- **Recent/Done section:** full history (kept forever), collapsible; previous attempts nest under their issue. Click → `/ticket/<run>` detail (timeline + gate + gh + evidence).
- **Health strip** + **version footer** + **empty state** as in §6.
- Timestamps displayed localized to the user's tz; stored/compared as UTC epoch.

## 12. Governance integration (airuleset)

- **`agents/autopilot-worker.md`:** ONE compact REPORTING block near the top (~6 lines): *"REPORTING (fire-and-forget, never blocks, never a reason to pause): Step 0a START THE RUN — `RUN=$(airuleset.py report --start --repo <r> --issue <N> --title "..." [--is-bug-fix] [--has-deploy] [--merge-mode ..])`; after each phase transition run one `airuleset.py report --run $RUN --phase <p> [..]` line; it always exits 0 — if it fails, IGNORE and continue."* Do **not** interleave a report call into all 9 steps (keeps the load-bearing ASK-THE-USER section prominent).
- **`skills/autopilot/SKILL.md`:** one bullet under *How it works* (board URL + workers self-report); Step 1b supervisor reports the obsolete-close; Step 4 supervisor-verify gate + per-turn heartbeat; Step 1 preflight + the milestone ping print the board URL.
- **`hooks/autopilot-report.sh`** (Stop event, added to `settings/hooks.json`): scrapes `AUTOPILOT_RUN`/`AUTOPILOT_PHASE` env (set once by the worker) → POSTs a skeleton heartbeat+phase so prose-forgetting still yields liveness.
- **`airuleset.py`:** `BOARD_HOST_IP='10.77.9.21'` (+ `BOARD_HOST` env override) and `is_board_host()` (compare against `socket.gethostbyname(socket.gethostname())` / first `hostname -I` IP). `report` + `board` subcommands (board has `--url`/`status`/run-foreground). `cmd_install` branches: **board host** → `setup_board_service()` (write `~/.config/systemd/user/autopilot-board.service`, `loginctl enable-linger`, `systemctl --user daemon-reload && enable --now` with explicit `XDG_RUNTIME_DIR`, check rc, on failure print exact manual command; verify liveness via `curl 127.0.0.1:8787` then print the **10.77.9.21** LAN URL); **other hosts** → ensure reporter callable + queue dir, print "board: skipped (reports go to http://10.77.9.21:8787)". `cmd_validate` asserts board files import cleanly (`importlib`). `cmd_push` runs `python3 -m unittest discover tests` **before** pushing (fail-closed) — closes the current ships-untested-code gap.
- **`CLAUDE.md` `## Dashboards`:** the board URL.

## 13. Constants

`PORT=8787`, `BOARD_HOST_IP=10.77.9.21`, `REPORT_TIMEOUT=2s`, `CIRCUIT_BREAKER=60s`, `FLUSH_CAP=200`,
`QUEUE_CAP=5MB/5000`, `QUEUE_TTL=6h`, `BODY_MAX=64KB`, `EVENT_CAP_PER_RUN=500`, `STALE_ACTIVE=8min`,
`STALE_WAIT=30min`, `GH_POLL_FLOOR=30s`, `AUTO_REFRESH=10s`.

## 14. Testing (stdlib `unittest`, in `tests/test_airuleset.py`)

- `TestBoardSchema` — cold-DB init + migration-on-existing-DB (v1→v2 keeps rows, adds columns).
- `TestReporterQueueFlush` — flock skip, per-line removal on 2xx, idempotent re-flush (event_id), circuit breaker, flush cap, queue cap + TTL, poison-line skip.
- `TestUpsertMonotonic` — seq guard (stale replay ignored), COALESCE never NULL-clobbers, phase-rank never regresses, terminal not resurrected.
- `TestGateAlarm` — applicability (feature has no regression row required; backend no deploy; manual-mode unmerged = ok), UNSTABLE-merged fires alarm, claims-can't-clear, grace "verifying" window, untracked-merge synthetic run.
- `TestGhParse` — mock gh JSON → mergeable mapping (true+CLEAN ok, UNSTABLE fail, null pending), gh-fail sentinel + banner.
- `TestConcurrentPosts` — N threads, distinct AND identical run_ids → no lost rows, no exceptions.
- `TestSecurity` — token 403 without/with token, worker can't set source=verified, `<script>` payload rendered escaped, body>64KB → 413, gh-bound field validation rejects `--flag`/`a;b` repo.
- `TestIsBoardHost` — install on non-board hostname does NOT create the service unit; board host does.
- `TestVersionLabel` — footer + `/api/state` carry the version string format.

## 15. Out of scope (YAGNI)

- Auth beyond the shared LAN token (no multi-user/SSO — internal LAN).
- Non-GitHub forges. Metrics/graphs/charts. Editing tickets from the board (read-only audit surface; gh is never mutated).

## 16. Open questions

None blocking — defaults chosen above (port 8787, thresholds, keep-all-runs with per-run event cap) are sensible and user-overridable. The user can adjust any constant in §13.
