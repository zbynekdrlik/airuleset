### Comprehensive Logging — More Logs, Never Fewer

**These projects are MVPs and bug-prone. Treat every feature as if it WILL fail in production and you will need to debug it from logs alone — without a code change, without a redeploy.** Default to logging more than feels necessary. Adding a log line costs nothing. Missing one costs a debug cycle.

#### The rule

When in doubt, log it. When not in doubt, log it anyway. Disk space and log volume are cheaper than:
- A user reporting a bug you can't reproduce
- A code edit + push + CI run + deploy + retry, just to add a `println!` that should already exist
- "It worked on my machine" debugging where the prod path took a branch you can't see

If a future debugging session might benefit from knowing a value was X at time T, log it now. **Stripping logs "to reduce noise" is forbidden unless you have proof the noise is hiding signal — not just because it feels verbose.**

#### What to log (mandatory, every feature)

1. **Every external boundary** — incoming requests (method, path, params, user, body size), outgoing API calls (URL, payload, status, latency), DB queries (statement, params, row count, duration), MCP tool calls, SSH commands, file I/O.
2. **Every state transition** — "user X moved from state A to state B because of event E". Include the trigger and the values that decided the branch.
3. **Every decision branch** — `if`/`match`/`switch` arms relevant to feature behavior. Log which arm was taken AND the values that decided it. Not just "auth failed" — `auth failed: token=<truncated>, exp=<ts>, now=<ts>, reason=expired_30s_ago`.
4. **Every error path** — full context, NOT just the error string. Caller's args, current state, stack trace, related IDs. `Err(e)` with no surrounding context is a half-log.
5. **Every retry / fallback / timeout** — what failed, what's being retried, how many attempts left, how long until timeout.
6. **Every config / env load** — what was read, from where, what the resolved value is (mask secrets). At startup, log every config source and final merged config (with secrets masked).
7. **Every background job / scheduled task / event handler** — start time, trigger, parameters, end time, outcome.
8. **Every user action with side effects** — who, what, against which resource, with what payload, result.

#### Log content rules

- **Structured logging** — JSON or key=value when possible (`tracing` in Rust, `structlog`/JSON in Python, `pino`/`winston` in Node). Free-form strings are second-best; useful for humans, terrible for grepping at 3am.
- **Include identifiers** — request_id, user_id, session_id, transaction_id, correlation_id. A log line without an ID is hard to chain to other lines.
- **Include values, not just labels** — `processed item` is useless; `processed item id=42 size=1024 elapsed_ms=12 user=alice` is debuggable.
- **Include timing** — start/end timestamps or elapsed_ms for any operation that could be slow.
- **Mask secrets** — never log full tokens, passwords, API keys. Truncate to first/last 4 chars or hash.
- **No `format!` of an `Err` without `{:?}`** — printing `Display` on errors loses the debug detail. Use `{:?}` or full `tracing::error!(?err, ...)`.

#### Persist to DB when transient logs aren't enough

For data flowing through a debugged feature, **prefer a DB table over text logs**:

- **Audit tables** — every state-changing user action gets a row: `who, when, action, resource_id, before, after, request_id`.
- **Request log tables** — for low-traffic services, persist every API request + response. SQLite or a single Postgres table is enough. Cheap. Searchable.
- **State snapshot tables** — for state machines and complex workflows, write a row at every transition: `entity_id, from_state, to_state, trigger, payload, ts`.
- **Event sourcing for high-value flows** — payments, sync jobs, deploy pipelines. Replay the events to debug the failure.

**Default decision tree:**
- Is this data needed if a bug is reported 3 days from now? → DB table.
- Is this data only needed within the next ~24h while we tail logs? → log line is enough.
- Is the operation rare and high-value (deploy, payment, sync)? → DB table, no question.
- Is the operation high-volume and ephemeral (per-request middleware)? → structured log line; consider sampling at 100% in dev, lower in prod.

When in doubt: **DB row**. Disk is cheap. Investigation time is not.

#### Log levels (use them)

- `TRACE` — verbose internal detail. Default OFF in prod, ON in dev when reproducing a bug.
- `DEBUG` — values, decision points, state transitions. Default ON in dev, ON in prod for MVP-stage projects.
- `INFO` — meaningful operational events: server start, deploy, user action with side effect.
- `WARN` — recoverable problem: retry triggered, fallback used, deprecated config.
- `ERROR` — operation failed, request didn't complete, data is inconsistent.

For MVP / pre-stable projects: **default level = DEBUG, in prod**. Storage is cheaper than 3am SSH-and-redeploy. Once a project is stable for 6+ months with no recurring bugs, you can drop to INFO.

#### Anti-patterns (all banned)

- "I'll add logs if it breaks" — **WRONG.** Add them now. The break is when you can't add them.
- "This log is too verbose" with no proof noise > signal — **WRONG.** Verbosity is the feature.
- `catch (_) {}` / `Result.ok()` discarding errors silently — **WRONG.** Every catch logs the error with context, even when handled.
- `eprintln!("error")` with no payload — **WRONG.** Log the error value, the inputs, the surrounding state.
- Logging only on the failure path — **WRONG.** Log success too. Otherwise "no logs = it didn't run" is indistinguishable from "no logs = it ran fine".
- Stripping logs in a "cleanup" PR — **WRONG.** Removing logs requires the same justification as adding new code: a clear reason. "Looked noisy" isn't one.
- `println!` / `console.log` in production code — **WRONG.** Use the project's logger so level filtering, structured fields, and routing work.
- Saving disk by NOT writing audit rows — **WRONG.** A 100MB audit table beats 4 hours of SSH log spelunking.

#### Project setup expectations

When you start a new project or touch a logging-poor codebase:

1. Pick a structured logger early — `tracing` (Rust), `structlog`/`logging` with JSON formatter (Python), `pino` (Node), `slog` (Go).
2. Add request-ID middleware on every web service.
3. Add an `audit_log` table to the DB schema for any project with users or state changes.
4. Wire log output to a queryable destination (file + journald, Loki, CloudWatch, even a `tail -F` tmux pane in MVP).
5. Document the log destinations in the project's CLAUDE.md so future debugging doesn't start with "where are the logs?".

#### The principle

**You will be debugging this code at 3am with no ability to redeploy.** Write logs as if that's the only tool you'll have. Storage is cheap. Investigation time is the bottleneck. When you trim logs, you are betting against your future self — and your future self always wins.
