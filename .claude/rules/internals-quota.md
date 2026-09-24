---
paths:
  - "cli_resource_guards*.py"
  - "tests/test_quota*.py"
---

### airuleset internals — per-user quota on shared-stream boxes (#950 / #1140)

- **Three root writers share ONE limits renderer and ONE lock.** `_render_quota_limits_block()` is the only copy of the ceiling math. The push apply (`_render_quota_apply_block`), the daily `airuleset-quota-refresh` (recount + limits) and the hourly `airuleset-quota-ceiling` (limits ONLY, #1140 D) all embed it, and all take `QUOTA_LOCK_PATH` on fd 9 (busy → exit 4, touch nothing). The two scripts share `_render_quota_script_preamble()`. A second copy of the math is a test failure (`test_limits_math_exists_exactly_once_in_source`).
- **Ceiling formula (#1140 D):** `hard = min(max(10G, used*1.2), used + 50% of df avail on /)`, `soft = min(max(8G, used*1.1), hard)`. The fs cap is floored at 1 KiB because setquota reads 0 as NO limit. An unreadable df is LOUD, sets `qfail` and still applies the unbounded formula, because a stale lower ceiling is the EDQUOT incident itself.
- **Bash on external numbers under `set -euo pipefail`:** parse df STDOUT only (a stderr warning merged with `2>&1` can void the value), force base 10 with `$(( 10#$x ))` (a leading `0` is octal: `08` errors), and guard non-numeric repquota fields with `${x//[0-9]/}` before any arithmetic. Otherwise one bad row aborts the whole user loop, and the read-back then "verifies" the OLD limits.
- **Execution tests must stub `df`.** The limits block reads the real fs size, so a test without a `df` stub depends on the box (a 16 GB-free box caps a 10G floor down to 9G). `test_quota_fix_forward_950._make_stubs` defaults one (1 TiB free). For a realistic read-back, use STATEFUL stubs (setquota writes, repquota reads back), as in `test_quota_ceiling_hourly_1140.py`.
- **Timer collisions:** `hourly` and `daily` both elapse at 00:00. The ceiling service is `After=airuleset-quota-refresh.service`, so systemd queues it behind the refresh instead of racing the lock.
