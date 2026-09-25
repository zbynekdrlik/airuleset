"""Per-host detail for the hourly fleet burn row (#1154).

Two additive fields claudy reads to do exact per-group accounting on shared
machines:

- `sessions` — the previous full hour's spend split per Claude Code session
  (`SessionAgg`, fed by `burn.scan()`'s ONE per-line pass, never a second
  parser; `snapshot_sessions()` picks the hour).
- `weekly_window` — every weekly window the Anthropic usage endpoint last
  reported, each with its scope and a freshness verdict
  (`weekly_windows()`, read from the usage cache `watchdog.usage.
  write_usage_cache()` writes: `{ts, account_email, windows: [{kind, group,
  percent, model, resets_at, is_active}]}`).

A sibling module of `burn/__init__.py` (at its size-ratchet ceiling), so it
must never import `burn` itself — `burn/__init__.py` imports FROM here.
Pure, stdlib-only, never raises on malformed input.
"""
import os
from collections import defaultdict

# #286-review — how STALE a usage-cache reading may be before it is refused as
# current. Mirrors `watchdog.usage.FABLE_GATE_MAX_AGE`'s own 6h staleness bound
# for this EXACT same cache file (`~/.claude/airuleset-usage-cache.json`) — a
# deliberate MIRROR, never a shared import: `burn` must never import
# `watchdog`, which already imports `burn`. Shared by
# `burn.group_fleet_by_account()`'s cross-host candidates and #1154's
# `weekly_window` `stale` flag, so both freshness verdicts always agree.
FLEET_WEEKLY_CANDIDATE_MAX_AGE = 6 * 3600

ALL_MODELS_SCOPE = "all-models"
_WORKTREE_MARK = "/.claude/worktrees/"


def _weekly_candidate_is_fresh(ts, now_epoch):
    """True iff `ts` (a host's own usage-cache WRITE time, unix epoch
    seconds — see `_fleet_remote_row`'s `weekly_ts`) is within
    `FLEET_WEEKLY_CANDIDATE_MAX_AGE` of `now_epoch`. Clock-skew-safe
    staleness check for the usage cache file: age outside `[0, MAX]`
    — including a FUTURE `ts`
    (clock skew, a cache synced off another box), which a plain
    `age > MAX` check would wrongly call "fresh" forever — is unknown,
    never trusted. A missing/non-numeric `ts` (a legacy pre-#286 row that
    never carried one) is never fresh by omission — excluded from the
    candidate list, never guessed."""
    try:
        age = float(now_epoch) - float(ts)
    except (TypeError, ValueError):
        return False
    return 0 <= age <= FLEET_WEEKLY_CANDIDATE_MAX_AGE


def session_id_of(path, root, slug):
    """The Claude Code session a transcript belongs to: the FIRST path
    component under the project dir `root/slug`. A main transcript
    `<sid>.jsonl` (or its gzipped `<sid>.jsonl.gz`, #1117) yields `<sid>`; a
    subagent transcript `<sid>/subagents/agent-*.jsonl` (any depth) folds into
    its parent `<sid>`."""
    first = os.path.relpath(path, os.path.join(root, slug)).split(os.sep)[0]
    for suffix in (".jsonl.gz", ".jsonl"):
        if first.endswith(suffix):
            return first[:-len(suffix)]
    return first


class SessionAgg:
    """Per-(hour, session) spend accumulator, fed request-by-request by
    `burn.scan()` in the same pass that fills `by_hour`, so a session's usd
    is priced and windowed exactly like the host total it sums to.

    `project` is the session's REAL cwd, from the transcript lines' own `cwd`
    field — the `~/.claude/projects/<slug>` name is lossy (every `/` and `.`
    became `-`). The main transcript's FIRST cwd (its launch dir) wins over a
    subagent's (a worktree lane runs under `.claude/worktrees/…`); a
    subagent's first cwd is used only when the main transcript carries none
    (e.g. the main file aged out of the scan window), with a trailing
    `/.claude/worktrees/<lane>` stripped back to the project it belongs to;
    the slug is the last resort. Session ids are Claude Code UUIDs, unique
    per box, so rows are keyed by the id alone (the design's `hour|sid`)."""

    def __init__(self):
        self._rows = defaultdict(lambda: [0.0, 0, defaultdict(float)])
        self._cwd = {}     # sid -> (rank, cwd); rank 0 = main, 1 = subagent
        self._slug = {}

    def note_cwd(self, sid, cwd, is_main):
        if not isinstance(cwd, str) or not cwd:
            return
        rank = 0 if is_main else 1
        cur = self._cwd.get(sid)
        if cur is None or rank < cur[0]:
            self._cwd[sid] = (rank, cwd)

    def add(self, hour, sid, slug, model, usd):
        self._slug.setdefault(sid, slug)
        row = self._rows[(hour, sid)]
        row[0] += usd
        row[1] += 1
        row[2][model] += usd

    def project_of(self, sid):
        cwd = self._cwd.get(sid)
        if not cwd:
            return self._slug.get(sid, "?")
        rank, path = cwd
        if rank and _WORKTREE_MARK in path:
            path = path.split(_WORKTREE_MARK, 1)[0] or path
        return path

    def dump(self):
        """`{"<hour>|<sid>": {project, usd, msgs, by_model}}`, sorted by key."""
        return {hour + "|" + sid: {
                    "project": self.project_of(sid), "usd": round(v[0], 4),
                    "msgs": v[1],
                    "by_model": {k: round(u, 4) for k, u in sorted(v[2].items())}}
                for (hour, sid), v in sorted(self._rows.items())}


def snapshot_sessions(by_hour_session, hour_key):
    """The `sessions` list of one hourly snapshot row: every session with
    spend in `hour_key`, as `{project, session_id, usd, msgs, by_model}`,
    sorted by usd descending (ties by session id, for a stable order). `[]`
    for an hour with no spend. Each session `usd` is 4-dp; the host `usd` is
    2-dp precise (`scan()`'s `_dump`), so the session sum matches the host
    figure within ±0.005 plus 0.00005 per session, never exactly."""
    prefix = hour_key + "|"
    out = [{"project": v.get("project"), "session_id": k[len(prefix):],
            "usd": v.get("usd", 0.0), "msgs": v.get("msgs", 0),
            "by_model": v.get("by_model") or {}}
           for k, v in (by_hour_session or {}).items() if k.startswith(prefix)]
    out.sort(key=lambda s: (-s["usd"], s["session_id"]))
    return out


def weekly_windows(cache, now_epoch):
    """Every WEEKLY window in the usage `cache`, as `{scope, kind, pct,
    resets_at, is_active, read_ts, stale}` — `scope` is `"all-models"` for
    the account-wide window (falsy `model`, the SAME rule
    `burn.shared_weekly_window()` uses), else the endpoint's model display
    name (e.g. the Fable-scoped weekly window); a truthy non-string `model`
    is malformed and skipped, never mislabelled account-wide. All-models
    first, then by scope. This list is the CANONICAL weekly reading of a
    snapshot row; the legacy `weekly_pct`/`resets_at` a fleet collector adds
    come from a later cache read and may differ by one refresh.

    `read_ts` is the cache's own write time (when the endpoint last reported
    these values); `stale` is True whenever that reading is not provably
    within `FLEET_WEEKLY_CANDIDATE_MAX_AGE` of `now_epoch` (the snapshot
    row's own write time, so the verdict is "as of the row" and immune to a
    collector's clock skew) — including a missing/non-numeric write time —
    so an old reading is never presented as current. `[]` on a missing/malformed cache or one with no weekly window;
    a malformed entry (non-dict, non-numeric/bool percent) is skipped."""
    if not isinstance(cache, dict):
        return []
    ts = cache.get("ts")
    read_ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
    stale = not _weekly_candidate_is_fresh(read_ts, now_epoch)
    out = []
    windows = cache.get("windows")
    for w in windows if isinstance(windows, list) else []:
        if not isinstance(w, dict) or w.get("group") != "weekly":
            continue
        pct = w.get("percent")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        model = w.get("model")
        if model and not isinstance(model, str):
            continue
        out.append({"scope": model or ALL_MODELS_SCOPE,
                    "kind": w.get("kind"), "pct": pct,
                    "resets_at": w.get("resets_at"),
                    "is_active": bool(w.get("is_active")),
                    "read_ts": read_ts, "stale": stale})
    out.sort(key=lambda e: (e["scope"] != ALL_MODELS_SCOPE, e["scope"]))
    return out
