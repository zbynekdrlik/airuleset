---
paths:
  - "cli_resource_guards*.py"
  - "tests/test_quota*.py"
  - "cli_session_scope.py"
  - "tests/test_session_scope*.py"
  - "gates/owntranscript.py"
  - "hooks/block-own-transcript-read.sh"
  - "cli_question_block.py"
---

### airuleset internals — per-user quota on shared-stream boxes (#950 / #1140)

- **Three root writers share ONE limits renderer and ONE lock.** `_render_quota_limits_block()` is the only copy of the ceiling math. The push apply (`_render_quota_apply_block`), the daily `airuleset-quota-refresh` (recount + limits) and the hourly `airuleset-quota-ceiling` (limits ONLY, #1140 D) all embed it, and all take `QUOTA_LOCK_PATH` on fd 9 (busy → exit 4, touch nothing). The two scripts share `_render_quota_script_preamble()`. A second copy of the math is a test failure (`test_limits_math_exists_exactly_once_in_source`).
- **Ceiling formula (#1140 D):** `hard = min(max(10G, used*1.2), used + 50% of df avail on /)`, `soft = min(max(8G, used*1.1), hard)`. The fs cap is floored at 1 KiB because setquota reads 0 as NO limit. An unreadable df is LOUD, sets `qfail` and still applies the unbounded formula, because a stale lower ceiling is the EDQUOT incident itself.
- **Bash on external numbers under `set -euo pipefail`:** parse df STDOUT only (a stderr warning merged with `2>&1` can void the value), force base 10 with `$(( 10#$x ))` (a leading `0` is octal: `08` errors), and guard non-numeric repquota fields with `${x//[0-9]/}` before any arithmetic. Otherwise one bad row aborts the whole user loop, and the read-back then "verifies" the OLD limits.
- **Execution tests must stub `df`.** The limits block reads the real fs size, so a test without a `df` stub depends on the box (a 16 GB-free box caps a 10G floor down to 9G). `test_quota_fix_forward_950._make_stubs` defaults one (1 TiB free). For a realistic read-back, use STATEFUL stubs (setquota writes, repquota reads back), as in `test_quota_ceiling_hourly_1140.py`.
- **Timer collisions:** `hourly` and `daily` both elapse at 00:00. The ceiling service is `After=airuleset-quota-refresh.service`, so systemd queues it behind the refresh instead of racing the lock.
- **Per-SESSION memory scope on workstations (#1224, `cli_session_scope`).** Both launchers exec claude through `systemd-run --user --scope` on the `SCOPE_HOSTS`. Five facts, each measured live on dev1:
  - **`OOMPolicy=continue` is mandatory.** The default `stop` ends the WHOLE scope, claude included, when the kernel memcg OOM kills one tool. A throwaway 300 MB scope showed `Terminated`; with `continue` the tool got rc -9 and the parent lived.
  - **The probe must carry the same `-p` set as the exec.** A refused property after a green bare probe would kill the pane.
  - **Every tool must be the OOM victim first.** `BASH_ENV` is exported and each non-interactive `/bin/bash -c "source <snapshot>…"` (how Claude Code runs a Bash tool) raises its own `oom_score_adj` to 800. An unprivileged process may lower the value only down to `oom_score_adj_min` (0), so claude cannot shield itself; raising the tools is the lever.
    - Group the write as `{ echo 800 > /proc/self/oom_score_adj; } 2>/dev/null`: a redirect error is printed BEFORE a trailing `2>/dev/null` applies, and every hook would carry it.
    - The real claude is a native ELF on every account (checked dev1/dev2), so it never sources `BASH_ENV`. A bash-script `claude` shim would.
  - **`--scope` keeps the pid but RESOLVES argv[0]** (`/home/<u>/.local/bin/claude …`). A cmdline matcher needs `(^|/)claude`; `comm` and `pane_current_command` stay `claude`.
  - **Under `set -euo pipefail`, `_kb=$(awk …)` on a missing file aborts the launch.** Every fail-open read needs `|| _kb=""`.
  - Everything a session starts, `setsid nohup` jobs included, shares its cap.
- **A guard keyed on a session id must key on the FILE, never the bare id (#1224 slice 2).** The id is also in the session's scratchpad (`/tmp/claude-<uid>/<proj>/<id>/scratchpad`), task-output and `tool-results` paths. A bare-id match would have blocked every ordinary scratch read, including the session that shipped it. `gates/owntranscript.py` triggers on `<id>.jsonl` or `.claude/projects`. It refuses any reader of a `*.jsonl` there, by path or glob, and any recursive read (`-r`, `rg`). It scans every `$(…)`/backtick body as its own command, but never splits on a bare paren, so `python -c 'open("x.jsonl")'` stays one command. Two more rules:
  - **"Bounded" head/tail means one numeric `-n <= 50` or `-c <= 64 KiB`.** `tail -n +1` and `tail -f` read everything.
  - **A hook test's PATH shim must use builtins only.** A `touch` missing from the isolated PATH silently recorded nothing. The root cause is fixed by `airuleset.py question-block`, which prints the last ❓ block from `discord-questions.json`. Declare a leaf subcommand's options on its subparser: `argparse.REMAINDER` drops a leading `--opt`.
  - **Only a content READER reads (live false positive, 0.1.538).** The first deploy blocked the ticket's own evidence comment. The shell text `cat > c.md <<EOF` held markdown that quoted a `grep … .jsonl` in backticks, and a `gh --body`, `git commit -m` or `echo` naming such a path was blocked too. The guard now looks only at a `READERS` head. It blanks heredoc bodies with `gates.selfservice._capture_heredocs`, except when an interpreter runs them (`python3 - <<EOF`, the songplayer shape). A guard that inspects shell TEXT must test documentation-shaped commands that MENTION its target, not only commands that use it. Also run `test_hook_runner_env_lock_1046` for any new hook test, which needs `hermetic_hook_env(self)`: Pass A refused the first 0.1.538 push on it.
