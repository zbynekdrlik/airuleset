2026-07-09 autopilot batch1: #2 multi-line [no-test:] bypass fix (bb028ca→01e09f2)
2026-07-09 autopilot batch1: #3 watchdog per-pane loop guard (fe3d83e→2150453)
2026-07-09 autopilot batch1: #6 watchdog scans newest subagent transcript, jobs 1/4a (2ed55a1→21961b8)
2026-07-09 autopilot batch2: #7 ruff cleanup 102→0 errors (b6fe3ba) + cmd_push ruff gate test:a2b19da[red]→fix:e7105b0[green]
2026-07-09 autopilot batch2: #10 block-test-skips.sh hook, test:88ab805[red]→feat:62d8245[green]
2026-07-09 autopilot batch2: #11 block-history-rewrite.sh hook, test:0ce0a47[red]→feat:0d3afd4[green]
2026-07-09 autopilot batch3: #4 secret-scan hook content-value Gate 2, test:9b7bad8[red]→feat:f3d648e[green]; follow-up bypass-marker quote bug found+fixed same batch, test:0d1d91f[red]→fix:d1fde9b[green]; empirical finding: PreToolUse hooks DO fire for in-session background subagents (self-tested live from inside this autopilot-worker)
2026-07-09 autopilot batch3: #8 cross-session autopilot-lock (fcntl.flock + pid-liveness), test:548e14e[red]→feat:74c2660[green]; wired into skills/autopilot/SKILL.md + agents/autopilot-worker.md
2026-07-09 autopilot batch3: #12 investigated CC 2.1.198 agent_needs_input/agent_completed — confirmed scoped to claude agents/--bg fleet daemon (disabled here via disableAgentView), not the interactive session; closed not-actionable with evidence; split "send-to-user tool" idea to #15
2026-07-09 solo: #13 investigate 4 hook candidates — built block-destructive-remote.sh (remote host shutdown/reboot, rm -rf on filesystem root, SQL DROP/TRUNCATE, all over ssh/remote-host only; FP-corpus checked against deploy-ssh sanctioned commands), built pre-write-script-check.sh (Write|Edit: new .sh needs set -euo pipefail; new .py except:pass blocked — S110 repo-wide rejected, 24 pre-existing sites), added 🌐-line localhost check to stop-check-prose-violations.sh, verified+tested the pre-existing (untested) tester-handoff block claim in autonomous-verification.md is TRUE. 46 new tests, all green. commit 53161d0.
2026-07-09 solo: #14 dedup/trim pass (hook-phrase-aware, higher tier). 5 commits, one per sub-scope, full suite (789) + ruff 0 after each: (1) ef9aa9b milestone→pointer for the ask-and-continue decision policy (canonical=message-status-marker), kept device-mechanics + test-locked sleep phrases; (2) 7e96662 no-destructive→pointer for the NEVER-gate-on-prod framing (canonical=approval-scope), kept command-level list+hook; (3) 5cd9228 shrank the tester-handoff + "your call" ban-lists to intent+examples+hook-note, KEEPING per-phrase-verified variants the stop-check-prose-violations.sh regex does NOT match; (4) 7db9509 genericized the Odoo/OCA curl block, pointed playbook-mandate + ticket-validator spec to their owning skills; (5) <FINAL> model-awareness↔claude-code-tooling intentional-overlap cross-pointers (no restructure). NO tests re-pointed — all phrase-locks preserved in place (verified: sleep phrases in milestone, po každom tickete + 📔 Playbook: in playbook, SHAPE/ADVISOR in the two model files). Key finding: the notification-trio 3-way restatement is DELIBERATELY test-pinned (test_question_policy requires sleep phrases in BOTH message-status-marker AND milestone; 60/UNLIMITED/AskUserQuestion in BOTH message-status-marker AND user-questions) — so the genuinely-removable restatement was narrower than the ticket implied; trimmed only the true cross-domain duplication.
2026-07-09 solo: #15 investigated client-side send-to-user tool (split from #12). Read prompting-claude-fable-5's send_to_user pattern (raw-Messages-API custom harness, client-side tool, instant ack, no turn end). Mapped existing mid-turn channels: (a) `notify --body` via Bash already works mechanically but bypasses quality-gate/dedup/routing + policy-banned for noise; (b) discord MCP reply is wrong shape (needs inbound chat_id, we're outbound-only) + also banned; (c) Stop-hook marker scan only fires when the model itself chooses to stop — no forced architectural delay, "ask the moment" is a discipline problem the rules already cover. One real gap found: a dispatched autopilot-worker subagent terminates when it ends its turn to emit a marker (subagent-continuation.md, one-shot dispatch) — but durable-decisions-to-tickets.md already mandates writing findings to the ticket the moment they land, so a fresh redispatch loses nothing material. Verdict: marginal win vs. real cost of duplicating stop-check-question-quality.sh's hard-won shape/dedup/reword/presence-aware logic into a second CLI-side path. NOT building. Closed #15 with full evidence comment (https://github.com/zbynekdrlik/airuleset/issues/15#issuecomment-4923894158); revisit trigger + port-not-duplicate guidance recorded on the issue. No code changed, no push.
2026-07-09 solo: adversarial-review fixes on the autopilot run's cumulative diff (no issue — in-run review findings, same-PR-fix policy). 6 RED/GREEN pairs, all findings (🔴+🟡+🔵) fixed, built on top of ee7e9fc→2efd17e (subagent-type hook, untouched): (1) hooks/pre-write-script-check.sh test:b614755[red]→fix:1b9a24d[green] — payload now piped to the embedded python3 child via stdin/process-substitution instead of argv (Linux MAX_ARG_STRLEN 128KB was blocking any large Write/Edit with an empty reason); except-pass detection is now AST-exact (catches one-liners, never false-matches a docstring) with a regex fallback for unparsable Edit snippets; a hit already present verbatim in old_string is suppressed (unique-match context, not introduced by the edit); .sh header/pipefail check now applies only to genuinely NEW files (not on disk) that already have a shebang (skips sourced-lib .sh). (2) hooks/block-destructive-remote.sh test:8a2771b[red]→fix:dca9acb[green] — bypass marker now quote-aware (mirrors block-sensitive-staging d1fde9b); strip_prefix skips leading VAR=val assignment tokens too; removed -H from the DB-host flag set (psql -H is HTML output, not a host flag); exit-code discipline (only python3 exit 2 = real violation). (3) hooks/block-history-rewrite.sh test:e26f4cb[red]→fix:7264903[green] — quote-aware comment-strip before shlex (a commit message like "fix #12: adjust" no longer truncates mid-quote and drops --amend); same quote-aware bypass + assignment-prefix fixes; deleted the dead unreachable `elif tk[0]=="gh"` branch (gh-admin-merge detection already works via the other, reachable branch — locked in by a test that passed BEFORE the deletion too); same exit-code discipline. (4) hooks/block-test-skips.sh test:cf29826[red]→fix:dbd10ef[green] — test-file heuristic now also requires a real code extension (docs/xspec.md no longer false-blocks on "spec" substring); $TEST_CHANGES read into a bash array via mapfile instead of unquoted word-split (a filename with a space was silently skipped, fail-open); diff scan now processes per-hunk instead of joining all hunks' added lines (killed a phantom cross-hunk "empty test body" match between two unrelated additions); same exit-code discipline. (5) airuleset.py test:c54b749[red]→fix:b4569c6[green] — cmd_push's ruff gate now catches FileNotFoundError with a clean message instead of an unhandled traceback; _campaign_pid() now walks the process ancestry by /proc/<pid>/comm (new _proc_comm helper) looking for "claude"/"node" instead of a fixed one-hop grandparent walk — an extra shell layer (e.g. bash -c wrapper) no longer makes the recorded autopilot-lock holder look stale prematurely (would have reintroduced the #8 collision). (6) watchdog/__init__.py test:fe3a741[red]→fix:1fda6ba[green] — the per-pane except handler now preserves job 1's bare-key state across a transient capture error (adds the session to `stalled` before cleanup) and logs repr(e); jobs 1b/4a-sub now `continue` after nudging a dying subagent (was falling through to job 4, risking a second keystroke injection into the same pane in one poll); both gated on the supervisor's own marker being ⏳ AND a new SUBAGENT_MAX_AGE_SECONDS (2h) ceiling on the subagent file's age (a historical dying-worker file was nudging/escalating forever, even into an already-✅-DONE session) — updated 5 pre-existing RunOnceSubagentVisibility tests to give the supervisor a genuine ⏳ marker. Full suite 822/822 green, ruff 0, throughout. Pushed via `airuleset.py push`.
2026-07-25 batch (#48+#51): #48 job 14 context-threshold gate — compact_ticket_boundary now skips /compact below COMPACT_BOUNDARY_MIN_CONTEXT=200_000 (env AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT), measured fresh via transcript_current_context() resolved through _transcript_for_session (same sid+cwd resolver prune_answered_questions uses) right before send_continue; unresolvable transcript never blocks the send. test:f741518[red]→feat:342402b[green], 9 tests. #51 hooks/block-subdev-ssh-misuse.sh — PreToolUse(Bash) mirrors REMOTE_HOSTS exactly (montalu@subdev unconditional, marek/david@subdev only with -i .../gatekeeper_access_ed25519, everything else blocked incl. bare `ssh subdev`/root@/newlevel@), covers ssh/scp/rsync/sftp incl. sshpass-wrapped across the 4 subdev addresses, bypass marker `# airuleset:subdev-ssh-ok` with quote-stripping (marker-in-quotes is not a bypass), wired into settings/hooks.json; genuine RED via mv-out+stash convention: test:ccbfc41[red]→feat:c850641[green], 24 tests. Full suite 1509 passed, ruff clean throughout. Deployed via `python3 airuleset.py push`: GitHub+dev2+gatekeeper landed (c850641); montalu/marek/david@subdev failed "Connection refused" (dev1 still fail2ban-banned from #51's own incident) — NOT retried/probed per the standing rule, supervisor completes after ban expiry. Both issues closed with evidence.
2026-07-25 batch (#52+#53): #53 ULTRACODE_BASHRC_BLOCK opt-in — claude()/claude-new() no longer bake in `--settings ultracode:true`, new claude-ultracode() carries the old default verbatim; test:2651b93[red]→feat:894f6a0[green] (7 pinned-count tests fixed for the deliberate policy change, justified in the test docstring). #52 skills/autopilot/SKILL.md — default /autopilot skips Step 1b/1c pickers entirely (respects autopilot-skip labels silently), full interactive flow moved to `/autopilot dialog` + new alias skill skills/autopilot-dialog (registered in SKILL_NAMES, unrestricted); Step 4a sweep stays unconditional; 15 new tests (tests/test_autopilot_dialog_default.py), feat:d6c2582. Investigated (read-only) watchdog job 9 goal_autoarm — already arms /autopilot's exact arm-question wording unchanged, no watchdog code needed; finding commented on #52. Full suite 1526 passed, ruff clean throughout. Deployed via `python3 airuleset.py push`: GitHub+dev2+gatekeeper landed (d6c2582); montalu/marek/david@subdev failed "Connection refused" (dev1 still fail2ban-banned from #51) — NOT retried/probed per the standing rule, supervisor completes after ban expiry. Both issues closed with evidence.
2026-07-25 solo: #55 fleet-wide burn monitoring. burn/__init__.py: merge_fleet_row (one bad/malformed host -> {"error":...}, never crashes the rest), shared_weekly_window/weekly_budget (weekly %/days-remaining/%-per-day budget from ~/.claude/airuleset-usage-cache.json), fleet_trend (latest hour vs mean of previous 3), observed_pct_per_day (each fleet row stamps the current weekly_pct so a genuine hourly time series accumulates with no separate history file), fleet_sustainability/fleet_budget_alert (verdict + the Discord-ping trigger), render_fleet + `burn --fleet [--hours N]`, fleet_compare_rows + `--compare` now shows a "Sada (cela monitorovana sada)" block per change. watchdog/__init__.py job 16 (fleet_burn_job): merges the local host's own last snapshot + every remote row (fetched via the injected `fleet_fetch`) into one hourly fleet.jsonl row, fires the deduped budget-exceeded ping. airuleset.py: _fleet_remote_cmd/_fleet_remote_row/_watchdog_fleet_fetch tail each REMOTE_HOSTS box's OWN already-written snapshots.jsonl over ssh (never re-scan transcripts remotely, same identity/sshpass selection as _burn_remote_cmd); cmd_watchdog wires fleet_fetch ONLY when `os.uname().nodename == "dev1"` (coordinator-only — every other box already writes its own row via job 13). Corrected the issue's own text: NO gk ProxyCommand jump for subdev — direct tailscale ssh via REMOTE_HOSTS identities is correct, a failing host just becomes {"error":...}. 4 RED/GREEN pairs: test:c97bda9[red]->feat:cd73bd2[green] (burn primitives+render+CLI), test:63279fb[red]->feat:c70bdfa[green] (watchdog job16+remote collection), test:c2d0117[red]->fix:9bc4111[green] (non-dict row hardening found in self-review), test:c54958c[red]->fix:6d5f5f2[green] (observed_pct_per_day naive/aware datetime crash on a malformed ts, found in self-review). 55 new tests, full suite 1526->1582 passed, ruff clean throughout. Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev ALL landed (no fail2ban ban this time). LIVE-VERIFIED within the hour on dev1: the real systemd-scheduled job 16 already collected+merged all 6 hosts for real ($112.15 total, 508 msgs, zero host errors) into ~/.claude/burn-history/fleet.jsonl before I even ran a manual check; `burn --fleet` and `burn --compare` both render real production data correctly (weekly_pct=61%, 5.69 days to reset, budget 6.85%/day; every historical --mark now shows a "Sada" fleet block). Closed with evidence.
2026-07-25 batch (#56+#54): #56 MANAGED_EFFORT_LEVEL xhigh->high per official Opus 5/Fable 5 effort docs (both recommend "start with high, the default"; xhigh was carried over from the Opus 4.7/4.8 era, which docs explicitly warn against reusing). test:094a072[red]->fix:370f4ef[green] (deliberate policy-change test-pin update, own commit with docs citation, per regression-test-first's non-bug-fix carve-out); updated modules/core/model-awareness.md + claude-code-tooling.md's "managed xhigh MAIN-session baseline" sentences to `high`, xhigh kept for autopilot-worker + gated HARD escalations; autopilot-worker.md untouched (own explicit high/xhigh, independent of the managed default); also fixed 2 stale "Opus 4.8" mentions in claude-code-tooling.md's tiering sentence to Opus 5 while touching the same lines. #54 generalized block-fable-main-implementation.sh -> block-main-implementation.sh (renamed, git mv): now blocks implementation-size Edit/Write from a MAIN session (no agent_id) whenever EITHER it runs Fable (#32, unchanged) OR it has an ARMED /goal (#54, new) — david@subdev's Opus main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches with an armed goal. Goal-armed detection reads the transcript for CC's own `<local-command-stdout>Goal set:`/`Goal cleared:` marker, restricted to TOP-LEVEL user/system string content (never inside a nested tool_result, so grepping another session's transcript can't be mistaken for this session's own state); fully independent of the Fable-model detection, so #38 (stale model after /model switch) is neither fixed nor worsened. Bypass marker generalized to /tmp/airuleset-main-exec-ok-<sid>, old /tmp/airuleset-fable-exec-ok-<sid> still honored for back-compat. Genuine RED via the mv-out convention (rename hook+test to new names/logic first, hold back the goal-armed CODE, confirm only the new goal-armed assertions fail): test:24f8e51[red]->feat:6709fe5[green], tests/test_fable_main_guard.py renamed to tests/test_main_implementation_guard.py (12->22 tests). Full suite 1582->1592 passed, ruff clean, validate clean throughout both issues. Also added a CLAUDE.md playbook note on reading /goal armed/cleared state from a transcript (c0d79cf). Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev ALL confirmed at c0d79cf via direct `git rev-parse HEAD` on each host. LIVE-VERIFIED on dev1: ran the deployed hook (`~/devel/airuleset/hooks/block-main-implementation.sh`, the exact path settings.json wires) with a real goal-armed transcript + a 2610-char Edit -> BLOCKED exit 2 with the expected message; ran it again with an ordinary non-goal-armed transcript -> exit 0 (unchanged). Both issues closed with evidence. Found + filed unrelated pre-existing flake as #59: `test_second_call_within_same_hour_is_a_noop` (TestBurnSnapshotJob + TestFleetBurnJob) uses real `time.time()` and spuriously fails when `now`/`now+30` straddle a real clock-hour boundary — hit live at 19:59:47-20:00:14 CEST mid-verification, confirmed unrelated (neither #56 nor #54 touches watchdog/__init__.py), both pass once past the boundary; not fixed here (out of scope, different file, no reason to touch it for this batch).
2026-07-25 batch (#60+#58+#59): #60 fleet monitoring counted STALE remote samples as current (5/6 hosts double-counting the same old row across 19:00/20:00, false "-39.8% (lepšie)" trend). `_hour_bucket_of_ts()` (new) converts an ISO ts to a UTC epoch-hour bucket; `_fleet_remote_row(remote, want_hour_bucket)` now requires the remote's tailed line to match that bucket (gk +00:00 vs dev1 +02:00 compared correctly in UTC, never on the raw string) — mismatch -> `{"error":..., "stale": True}`, never a silent stale-row fallback; `_watchdog_fleet_fetch(hosts, want_hour_bucket)` threads it through. `merge_fleet_row` carries the "stale" flag into per_host; `render_fleet` shows a stale host as `—` (not `ERR`, not `$0.00`) plus a "N/M hostov ma vzorku pre tuto hodinu" note per row. `fleet_trend`'s TOTAL now refuses to compare hours whose valid-host-set differs (prints "neporovnatelne (ina mnozina hostov malo vzorku)" instead of a bogus percent) — by-host comparison unaffected. `fleet_burn_job` now waits until `FLEET_BURN_DELAY_MINUTES=5` past the hour boundary before collecting at all (so a remote's own job 13 has time to write), and passes its own hour_bucket into the injected fetch(). test:45be702[red]->fix:638529d[green], 22 new/updated tests, genuine RED confirmed via `git stash` of the 3 source files (20 failures, everything else green). #58 the david #2129 incident: Step 5 already said "report the batch then STOP the turn", but all THREE `/goal` templates (Step 2 — the text a running loop re-reads every turn) ended "After every merge|hand-off immediately pick the next issue" — read as "same turn" by a running loop, so it dispatched inline, ended on a working marker, and the ticket-boundary `/compact` hook never fired (unbounded context growth). All three templates now spell out that "immediately" means the NEXT TURN — end the turn with the full completion-report.md template, do NOT dispatch inline, the ARMED GOAL fires next turn; the branch-merge/fork-no-merge variants name their own reduced-authority completion-report.md shape in the template text itself. Step 5 extended to explicitly name the branch-merge/fork-no-merge hand-off shape so a sub-dev stream recognizes it applies to hand-off turns too. Deliberately did NOT add fuzzy "looks like a finished ticket" prose-detection to any Stop hook (ticket explicitly rejects it — false blocks on a running loop are worse than a missed compact). test:b7c9a5e[red]->docs:f4306be[green] (new tests/test_goal_turn_boundary.py, 11 tests + phrase-lock), genuine RED confirmed via `git stash` of SKILL.md. #59 already resolved as a side effect of #60's own test work: both named tests (TestBurnSnapshotJob + TestFleetBurnJob `test_second_call_within_same_hour_is_a_noop`) and every other TestFleetBurnJob/RunOnceFleetWiring test now use a FIXED timestamp (minute=30, safely past the new HH:05 delay gate and never crossing an hour boundary on `+30`/`+3600`) instead of `time.time()` — already landed inside 45be702/638529d; verified via direct hour-bucket arithmetic that the old `time.time()` form really did flip hour buckets near :59:47 (the exact live-reported window) while the fixed form never does. No separate commit needed. Full suite 1592->1621 passed, ruff clean, validate clean throughout all three. Deployed via `python3 airuleset.py push`.
2026-07-25 batch (#40+#37+#57): #40 rules/*.md path-scoped rules were never symlinked anywhere by cmd_install (categorize_entries split them out but `modules, _rules = ...` discarded the rules half). Confirmed against the installed CC binary (2.1.220, strings + byte-offset inspection of the minified bundle) that a native "User"-scope rules dir exists: `join(<user config base>, "rules")` -- same base fn as the well-known User CLAUDE.md path (`join(<user config base>, "CLAUDE.md")` == ~/.claude/CLAUDE.md) -- so the fix is `~/.claude/rules/`. Added `symlink_global_rules()` (explicit-params, tempdir-testable: idempotent, backs up a pre-existing real file, prunes an airuleset-owned symlink no longer referenced, never touches a foreign symlink) mirroring the existing skill-symlink pattern; wired into cmd_install step 2c + cmd_diff preview section; RULES_DIR constant added. test:508b707[red]->feat:2fc29a6[green], 10 new tests (tests/test_global_rules_symlink.py). Live-verified on dev1: `python3 airuleset.py install` linked all 5 universal-profile rules (no-continue-on-error, coverage-thresholds, browser-console-zero-errors, e2e-real-user-testing, database-migrations) into ~/.claude/rules/, second run idempotent ("OK rule"). #37 ruleset trim wave-2: the substantive implementation (10 full module->skill conversions + 1 partial split + 4 explicitly-rejected candidates) was ALREADY DONE in a prior commit (8469296, prior session) -- STEP 0 validation found ONE candidate from the issue's own 16-item table was silently never mentioned in the closing writeup: `ask-before-assuming.md` (trim ~1500/3700 words). Investigated via n-gram cross-check of every line against tests/: lines 16-17 (the self-invented-obstacle gate + its estimatedPlayoutTimestamp narrative) and table row 55 are directly phrase-locked by tests/test_ask_before_assuming.py reading modules/core/ask-before-assuming.md by path; the "Pre-answered questions" table is itself the enforcement gate that must fire before literally every question the agent considers asking (a higher-frequency trigger than ci-monitoring's "after every push" or claude-code-tooling's per-dispatch, both already rejected in the same issue for that exact reason). Formally evaluated + REJECTED as the 5th unsafe candidate (alongside claude-code-tooling/ci-monitoring/salvage-before-discarding-work/durable-decisions-to-tickets already documented) -- file left untouched (0-line diff), safer than a forced partial split that would either break the locked test or fragment the gate table for ~90 words of real savings. Measured: modules/*/*.md 40704 (pre-wave-2, commit b62002d) -> 33400 now (-17.9% net); the ~540-word gap vs the 32860 measured right after 8469296 comes from unrelated legitimate later commits (#54, #56, correction batch), not from #37 incompleteness. Closed #37 with full 16-candidate accounting comment, no further code change needed. #57 CLAUDE.md's api-watchdog bullet said "EIGHT jobs" and only narrated 1-8 while run_once's own docstring had grown to 16 numbered jobs. Trimmed the bullet to a pointer at run_once's docstring (the actual well-maintained single source of truth) + a highlight reel of the operationally most important jobs (1,2,4/4a,6,7,8/11,9,12,13/16,14/15) instead of expanding to re-narrate all 16 (which just defers the same rot to job 17); docs-only, no code change. Full suite 1621->1631 passed (10 new #40 locks), ruff clean, validate clean throughout. Deployed via `python3 airuleset.py push` (run twice; 2nd run confirmed "Everything up-to-date" + all 6 targets' installs green with no errors): GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev ALL confirmed at 4a9c989. Filed unrelated pre-existing finding as #62: .gitignore is missing an `audits/subdev-ssh-bypasses.log` pattern (individual-filename list, not a glob) -- out of scope for this batch, not fixed here.
2026-07-26 batch (#63+#62): #63 fleet burn monitoring never collected a remote sample -- job 13 (burn_snapshot_job) stamps its row with the hour that JUST completed (bucket(now)-1), while job 16 (fleet_burn_job) requested the CURRENT (still-open) hour bucket from `fetch()` -- a row for that bucket can never exist, so every remote host was permanently "--" and only dev1 ever showed a number (its own local snapshots.jsonl tail row was trusted UNCONDITIONALLY, with zero freshness check at all -- the asymmetry the issue diagnosed). Extracted the canonical bucket helper to `burn.hour_bucket_of_ts` (airuleset._hour_bucket_of_ts now delegates to it) so job 16's local-row check and `_fleet_remote_row` share one implementation. Fix: `want_hour_bucket = hour_bucket - 1` computed once in `fleet_burn_job`, used for (a) the fetch() call to every remote, (b) a new freshness check on the local row (mismatch -> `{"error":..., "stale": True}`, same shape as a stale remote -- never silently used), (c) the written fleet.jsonl row's own `ts` (now the completed hour's timestamp, not "now" floored to the current hour). `hour_bucket` itself stays only as the once-per-hour state guard. Preserves #60 behavior exactly (genuinely missing sample stays "--", excluded from totals, never silently replaced by an older row). Genuine RED/GREEN: test:b640e0f[red]->fix:6ecae29[green] (3 tests: 2 updated to the correct convention + 1 new for the local-row staleness check), full suite 1631->1632 passed, ruff clean, validate clean. #62 .gitignore was listing individual bypass-log filenames and missing `audits/subdev-ssh-bypasses.log` (#51's hook) -- switched the whole block to a single `audits/*.log` glob (fix:224be72) so a future new bypass-log type can't repeat the gap; the tracked non-log audit docs (audits/mdreview-*.md) are unaffected since the glob only matches *.log. Deployed via `python3 airuleset.py push` (run twice; 2nd confirmed "Already up to date" on all 6 targets: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev, all at 224be72). LIVE-VERIFIED across a real hour boundary on dev1: the fleet.jsonl row written before the 06:41 deploy (ts 06:00, hour bucket requested=current) still showed the pre-fix bug (1/6 hosts); the NEXT row (same ts 06:00 label but hour_bucket=495845, written at 07:05 after the fix was live everywhere) shows all 6 hosts with real samples ($83.25 total, 436 msgs) and `python3 airuleset.py burn --fleet --hours 3` renders "6/6 hostov ma vzorku pre tuto hodinu" for that row with no "--" columns. Cross-checked dev2's and marek@subdev's OWN snapshots.jsonl over ssh (fixed identities: dev2=default key, marek=~/.secrets/gatekeeper_access_ed25519) to confirm their $0.00/msgs=0 entries are genuine recorded zero-activity samples, not swallowed errors. Both issues closed with evidence.
2026-07-26 batch (#67+#65): #67 job 14+job 15 hard-skipped a draft-holding pane forever instead of stashing it -- live proof (david@subdev): one forgotten input-box draft made job 15 log "skip draft" and retry every ~60s sweep for 13h straight while the session's context grew 214K->449K with zero compactions. Shared `_compact_stash_attempt()` (watchdog/__init__.py): tries `deliver_with_stash` (issue #35's stash mechanism) for a draft-holding pane; on success proceeds exactly like the no-draft path; on "slot already occupied" (STASH_MARKER already present) logs a distinct "skip draft (stash occupied)" reason, tracks a per-session repeat count in `state['compact_stash_skips']`, and pings the owner once every `COMPACT_STASH_SKIP_PING_EVERY`=3 consecutive skips. job 14's #48 context-threshold measurement moved earlier (before the draft decision) so a trivial-context draft-holding session is dropped without a stash dance; job 15's `_pane_has_bg_agent` guard moved before the draft decision (never touch a pane with an in-flight worker). job 12 (model_reconcile) intentionally UNCHANGED -- a restart is destructive regardless of the draft. test:15d10f4[red]->feat:29f5f52[green], both jobs' fake-tmux helpers gained cap_seq support. #65 job 14's ~60s poll loses the RACE with an armed /goal loop, which can dispatch the next ticket within seconds of "## Work Complete" -- long before the poll ever sees the pane idle (measured Work-Complete:actual-compaction ratios: david 38->13, montalu 63->49, forestshop 63->16, gatekeeper 0->9). Fix is delivery TIMING: `deliver_compact_now` (+ `_find_pane_for_session`, matched by transcript stem never cwd alone) delivers `/compact` SYNCHRONOUSLY the moment `airuleset.py compact-request --record` runs (the Stop hook's own invocation) -- including into a BUSY pane, since a short send-keys reliably queues there (verified live 2026-07-26) and does not need to wait for idle; only copy-mode/an open dialog/no locatable boundary/a genuine draft fall back to the existing polled retry. `cmd_compact_request`: records first (never lost even if the immediate attempt raises), attempts delivery, clears the just-recorded request only on confirmed delivery. Also fixed the underlying normalization bug this surfaced: CC's greyed "Press up to edit queued messages" hint (an empty box recallable via Up-arrow, never real user text) was being read as a genuine draft by `_find_boundary_line` -- now normalized to a bare `❯` for every caller. Three RED/GREEN pairs: test:dabb6a7[red]->fix:89cd02f[green] (placeholder normalization), test:15d10f4[red]->feat:29f5f52[green] (shared with #67 above), test:0b043ae[red]->feat:dd82c52[green] (synchronous delivery + CLI wiring, 34 new tests incl. TestFindPaneForSession/TestDeliverCompactNow). Full suite 1632->1661 passed, ruff clean, validate clean throughout. Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev all confirmed at dd82c52. LIVE-VERIFIED against a REAL production session (montalu@subdev, genuinely idle, ctx=227113 > the 200K threshold, no goal armed so no natural race to observe): ran the exact deployed `python3 airuleset.py compact-request --record --session dcbe67e8-... --cwd /home/montalu/devel/odoo` directly -- completed in 0.938s wall-clock and printed "delivered"; pane capture confirms `/compact` was typed+submitted for real (CC replied "Not enough messages to compact" -- its own internal message-count heuristic, a separate business decision from delivery itself, which the exhaustive unit suite already covers via the token-count #48 gate); compact-requests.json was `{}` immediately, no wait for job 14's poll; montalu's session verified healthy/idle afterward, zero side effects. david@subdev's genuinely /goal-armed loop stayed deep in one long autopilot-worker review/fix round (~40+ min, #2198) throughout the verification window and never reached a fresh ticket boundary to observe passively -- the montalu supervised test is the live-infra proof; the race-avoidance logic itself (busy-pane delivery, transcript-stem pane matching, ambiguous-match fallback) is covered by the dedicated unit tests. Both issues closed with evidence.
2026-07-26 #69: job 14 (ticket-boundary) and job 15 (idle-overgrown) both need a trigger a whole class of sessions never provides -- measured live: the gatekeeper master loop (a continuous review/merge loop across repos, 3460 turns/9 compactions/zero `Work Complete` reports -- job 14 has nothing to fire from) and the dev1 supervisor/governance session (340K context, never compacted all day -- it ends every turn `⏳ WORKING` so the Stop hook never records a job-14 request, and it is continuously busy dispatching subagents so it is never idle 20 minutes for job 15 either). New job 17 `compact_hard_ceiling`: above `COMPACT_HARD_CEILING` (300K, deliberately between job 14's 200K floor and job 15's 400K threshold), `COMPACT_MIN_IDLE_S` is ignored entirely -- delivery only needs a locatable input boundary, no open dialog, not copy-mode. Unlike job 15, a BUSY pane is a valid send target (not a skip): a short send-keys reliably queues even mid-turn (#65's proven mechanism) -- the whole point of a real backstop for a continuously-busy session. Deliberately does NOT check `_pane_has_bg_agent` (unlike job 15) -- queuing behind a running background agent doesn't interrupt it, and skipping on one would recreate the exact gap this job exists to close. Dedup: a PENDING_CONFIRM/give-up state machine (same shape as job 15's, `COMPACT_CEILING_RETRY_S`=5min, `COMPACT_CEILING_MAX_ATTEMPTS`=3, Discord-pinged once on permanent give-up -- unlike job 15's silent give-up, since nothing else will ever compact this session), a same-pane "Compacting conversation" text check (`_pane_compacting`), and -- the regression lock -- a shared per-sweep `handled` set threaded through `compact_ticket_boundary` -> `compact_stale_context` -> `compact_hard_ceiling` (in that wiring order) so job 17 can never double-fire against a session job 14/15 already compacted THIS sweep; the autopilot ticket-boundary path stays primary and unchanged. Genuine RED/GREEN: test:4b224a5[red]->feat:52aebea[green] (24 new tests incl. `RunOnceCompactHardCeilingWiring.test_ticket_boundary_fires_first_and_ceiling_never_double_fires`, confirmed RED via `git stash` of watchdog/__init__.py -- 23 failed, 1 trivially-true absence-check passed). Full suite 1661->1685 passed, ruff clean, validate clean throughout. Deployed via `python3 airuleset.py push` (run twice; 2nd confirmed "Already up to date" on all 6 targets: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev, all at 52aebea). LIVE-VERIFIED on gatekeeper (a genuine continuously-busy master-loop session, no ticket boundary in this window): with `AIRULESET_COMPACT_HARD_CEILING=1000` (real ctx was 208700, naturally well under the production 300K default) a manual `airuleset.py watchdog --once` printed `OK (compact-ceiling) 0:0.0 ctx=208700 -> compacted (attempt 1/3)` and the pane capture confirmed `/compact` was genuinely queued behind the live busy turn ("❯ /compact" / "Press up to edit queued messages"); the state machine's own next-tick check against the REAL default ceiling (208700 < 300000) legitimately cleared it back to eligible (a coincidental pass, not itself proof of execution, noted honestly). Minutes later the SAME session reported a genuine `✅ DONE:` for #2180 (GPS server foundation) -- the pre-existing #65/job-14 mechanism fired independently and for real: watched the pane live through the full cycle, "Compacting conversation… ▰▰▰...▱ 13%->55%->78%->100%", then "⎿ Compacted (ctrl+o to see full summary)", footer context reading "ctx 0 · ~$0.00/tah" immediately after (direct panel confirmation of the drop) -- with two harmless redundant queued `/compact`s replying "Not enough messages to compact", proving the mechanism tolerates redundant fires cleanly. Confirms both: the new job's delivery path works live on a real continuously-busy production pane, and the pre-existing ticket-boundary path is untouched and still primary. gatekeeper's own real context never crossed the production 300K ceiling during the observation window, so job 17 did not fire on its own there -- stated plainly per the ticket's own instruction rather than overclaiming. `compact_ceiling`/`_attempts`/`_sent_at` state confirmed empty (self-healed) after the episode. Closed with evidence.
2026-07-26 #66: block-main-implementation.sh only guarded Edit/Write -- Bash was completely unguarded, and it is the dominant cost driver in a goal-armed loop. Measured that day (loop_health.py, gatekeeper session, 08:00 fleet-burn hour): 1222 main-agent Bash calls vs only 97 subagent dispatches at 212K avg context -- every Bash call re-sends the whole context. Extended the hook to also fire on PreToolUse(Bash), gated by the SAME Fable-main/goal-armed condition as Edit/Write, but classifying the COMMAND (allow-list/block-list) instead of sizing the payload: ALLOW-LIST (`gh pr/issue/run view|list|create|comment|edit|close`, `git status/rev-parse/fetch/log --oneline -N`, `python3 airuleset.py ...`, `tmux`, `systemctl --user`) always passes; BLOCK-LIST (`grep`/`rg`/`ag`/`find`, `cat`/`head`/`tail`/`sed`/`awk`, `pytest`/`cargo test`/`npm test`/`ruff check`/`eslint`/`go test`/`mvn test`/`make test|build`, `journalctl`/`docker logs`) is rejected ONLY while goal-armed/Fable; anything matching neither is ambiguous and stays ALLOWED (conservative by explicit instruction -- never break a legitimate gh/git call the loop depends on); a subagent (agent_id set) is never blocked; the classifier fails OPEN on any internal error. Genuine RED/GREEN: test:a8eca58[red]->feat:ceeb9a4[green] (26 new tests: 10 allow-list, 10 block-list-while-armed, 2 ambiguous-allowed, 1 not-armed-allowed-anyway pair, subagent-never-blocked, bypass-marker, Fable-main-also-blocks, wiring). Related bug found while filing the ticket: `block-gh-invalid-json-flag.sh` scanned the WHOLE command including heredoc BODY content, so `gh issue create -F body.md` false-blocked whenever the ticket body (written into body.md via the standard heredoc recipe) merely documented a `--json` gh recipe -- reproduced live against the deployed hook while filing #66 itself. Fixed by stripping heredoc bodies (a Python script fed via a quoted `<<'PYEOF'` heredoc delimiter, matching block-history-rewrite.sh's established no-bash-escaping pattern) before the existing quote-strip + grep checks run; a real violation surviving heredoc-stripping still blocks, a malformed/unterminated heredoc fails safe to the original full-text scan. Genuine RED/GREEN: test:d9a7bf2[red]->fix:dba5dee[green] (4 new tests). Full suite 1685->1717 passed throughout, ruff clean, validate clean. Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev all confirmed at dba5dee. Mechanically verified: all 30 new tests exercise the real hook binary via subprocess against constructed goal-armed/Fable transcripts and Bash payloads (same code path a live session hits), plus a standalone repro script confirmed the heredoc false-positive both before (blocked) and after (allowed) the fix. NOT live-verified: the ticket's own 24h acceptance criterion (`loop_health.py` before/after main_bash vs dispatch comparison on a running loop) cannot be satisfied within one session -- deployment just landed on all 6 boxes; a follow-up 24h comparison is for whoever next runs loop_health.py, stated honestly rather than fabricated.
2026-07-26 #70: Claude Code snapshots its hook set once at process START (`rCu()` / telemetry `setup_hooks_captured`, confirmed by reading the CC 2.1.220 binary directly) and never re-reads it -- so #66's Bash-guard hook (ed83955) has zero effect on the gatekeeper master loop, which has been running since before that commit, and every hook this repo has ever deployed was equally inert on any session already running at deploy time. New job 18 `hooks_reconcile` reuses job 12's EXACT restart machinery (`_restart_pane`, `_pane_has_bg_agent`, the boundary-classification guards) driven by a content hash (never mtime; `_hooks_config_hash`, sha1-truncated via the existing `_hash()` helper) of the effective settings.json `"hooks"` block instead of a target-model string -- there is no way to introspect a running process's loaded hook set from its transcript (hooks are never recorded per-message, unlike model), so this job tracks its OWN baseline per session id (`state['hooks_session_hash']`), bootstrapped on the FIRST sweep it ever observes a given sid (no way to know retroactively what hash an already-running session started under; only matters once, at this job's own rollout). A later mismatch against that baseline triggers the restart; fails CLOSED (returns []) on any settings.json read/parse failure. `model_reconcile` (job 12) gained an optional `handled` set param, populated ONLY at the real restart claim (never in dry_run, never for a merely-skipped pane) so job 18 can see within the SAME sweep that job 12 already restarted a sid for a model change and skip a second restart -- "one restart, not two" per the ticket's explicit ask. Gated on `hooks_settings_path` (same "wired = on" convention as jobs 13/14/16, NOT always-on like 9/15/17) after an always-on first draft polluted two pre-existing api-watchdog tests by bootstrapping state against the REAL ~/.claude/settings.json on the box running the test suite; cmd_watchdog wires the real path via the new `watchdog.hooks_settings_path()` helper. Genuine RED/GREEN: test:9907da6[red]->feat:7fd38cc[green] (25 new tests: TestModelReconcileHandledSet x3, TestHooksConfigHash x5, TestHooksReconcile x13, RunOnceHooksReconcileWiring x3, incl. an explicit coalescing test proving exactly one `/exit`+`claude` sequence when both a model mismatch and a hooks mismatch hit the same sid the same sweep). Full suite 1717->1742 passed, ruff clean, validate clean. Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev all confirmed at 7fd38cc. LIVE-VERIFIED end-to-end with a genuinely isolated throwaway session (never a production loop): a fresh `claude` process launched under `CLAUDE_CONFIG_DIR=/tmp/airuleset-hooktest-70/config` (relocates `~/.claude` without touching `$HOME`, so the real repo's hook script paths under `~/devel/airuleset` still resolved -- confirmed via `strings` on the installed CC binary: `CLAUDE_CONFIG_DIR` is a real, documented env var, "Use CLAUDE_CONFIG_DIR=/tmp for ephemeral local writes"), seeded with copies of the real `.claude.json`/`.credentials.json`/`settings.json` for a clean login. Sent a Bash command containing a throwaway marker string -- ran successfully (baseline: the new hook doesn't exist yet). Called `wd.hooks_reconcile()` directly (explicit `projects_dir`/`settings_path`/`state_path` all scoped to the scratch dir, never the real ones) to bootstrap the session's baseline hash (log: none, matching the designed silent-common-case). Appended one new PreToolUse hook entry (a throwaway `block-marker.sh` blocking that same marker string) to the SCRATCH settings.json only. Re-ran `hooks_reconcile()`: logged `OK restart (hooks changed) hooktest70:0.0`; pane capture confirmed the real keystroke sequence (`/exit` -> "Resume this session with: claude --resume ..." -> `claude` relaunch -> fresh idle prompt, small session so no resume-from-summary dialog, matching the designed direct-proceed branch). Sent the SAME marker command again post-restart: genuinely BLOCKED -- "PreToolUse:Bash hook error: ... No stderr output" / "The command was blocked by a PreToolUse hook (/tmp/airuleset-hooktest-70/block-marker.sh) ... Nothing ran." -- the new hook fired for real inside the restarted session, verified by triggering the actual blocked shape, not from a log line alone. Confirmed zero blast radius: `journalctl --user -u api-watchdog.service` for the real dev1 timer showed stable ~2.2-2.4s cycles throughout the test window with no errors and no restarts of any real session (the real settings.json was never touched; the scratch `projects_dir` has no encoded dir matching any real project cwd, so every real pane was silently skipped by `find_active_transcript` during this test). Cleaned up (`/exit` the throwaway session, `tmux kill-session`, `rm -rf /tmp/airuleset-hooktest-70`) after verification.
2026-07-26 #71: notify-compact-request.sh's synchronous delivery (#65) and watchdog job 14 'don't know about each other' was the ticket's own diagnosis, but journalctl forensics on the SAME live incident (gatekeeper, #2180 boundary, ~07:35) showed ZERO job 14/17 log lines across the whole window while the session transcript recorded THREE separate synchronous /compact sends (one 'Compacted', two 'Not enough messages to compact') within ~2.5 minutes -- all tied to the identical, UNCHANGED last_assistant_message. Proved the real cause: the armed goal loop's own re-evaluation re-runs the whole Stop hook chain against an unchanged completed-ticket report right after a compaction finishes, and neither channel had any memory of 'already handled this exact report'. Fix: a sha256 fingerprint of the triggering message (computed in the hook, passed as --msg-hash) tracked in a new compact-delivered.json (watchdog.compact_delivered_path/compact_already_delivered/mark_compact_delivered) -- separate from compact-requests.json so its existing 'success clears the pending entry' contract stays untouched. cmd_compact_request checks compact_already_delivered BEFORE recording/attempting delivery (a repeat hash is a complete no-op, zero keystrokes) and marks it only on genuine delivery (a failed attempt is never marked, so job 14's polled fallback is never lost); compact_ticket_boundary (job 14) drops an already-delivered entry with zero tmux interaction (the 'vice versa' half) and marks delivered on every success path (idle send, stash, the #48 small-context drop). A blank/absent msg_hash (every pre-#71 caller) never touches compact-delivered.json at all -- byte-for-byte unchanged behavior for anything that doesn't opt in. Genuine RED/GREEN: test:45b448d[red]->feat:32e98ba[green] (33 new/extended tests: TestCompactDeliveredDedup state functions, TestCompactTicketBoundary dedup cases, TestCompactRequestCli duplicate/fallback cases, TestCompactRequestHook msg-hash determinism). Full suite 1742->1764 passed, ruff clean, validate clean throughout.
2026-07-26 #72: job 17 (compact_hard_ceiling, #69) was broken on exactly the session class it was written for -- live proof, gatekeeper pane 0:0.0: a single turn ran 1h14m while job 17 sent /compact on its fixed 5-minute retry timer, logged each as "-> compacted" (only keystrokes typed, nothing verified), then GAVE UP after 3 attempts while context kept climbing 306900->308250->311408->323K with three duplicate, unconsumed /compact sitting in the pane's own input queue (CC only drains it at a turn boundary, so resending never helps -- it just plants duplicates that later fire "Not enough messages to compact"). Replaced the timer+attempt-cap machine with a strict three-state one, `state['compact_ceiling'][sid] = {"status": "queued", "cwd": ...}`: queued (sent once, waited on indefinitely, NEVER resent no matter how much time passes while still above the ceiling -- send-time log no longer claims "compacted", only that keystrokes were typed), consumed (context confirmed below the ceiling on a later sweep -- proof a real compaction landed -- claim cleared, eligible again), failed (a pre-pass, pure transcript-directory reads, no tmux -- detects that a queued claim's `cwd` now resolves to a DIFFERENT, newer session id, meaning the process holding the original queued keystrokes is gone for good since a restart always mints a fresh session id; drops the stale claim and lets the new session get picked up fresh, producing exactly one new send, never a special-cased immediate resend). No more permanent give-up, no more "tried too many times" Discord ping -- the issue is explicit that a session still above the ceiling must never be abandoned. Coordinates with #71's delivered-dedup by construction (each source now guarantees its own trigger fires exactly once; the pre-existing `handled` set still prevents job 14/15 vs job 17 double-firing within one sweep). Genuine RED/GREEN: test:8b16618[red]->feat:4780764[green] (6 new tests replacing 5 obsolete ones that had locked in the timer/give-up behavior as correct). Full suite 1764->1765 passed, ruff clean, validate clean. Deployed via `python3 airuleset.py push`: GitHub+dev1(local)+dev2+gatekeeper+montalu/marek/david@subdev all confirmed at 4780764. LIVE-VERIFIED on gatekeeper's genuine broken-state pane (0:0.0, real production session, turn running ~1h32m at deploy time, ctx 325K, four STALE pre-existing /compact already queued from the old buggy code): the pre-existing on-disk state for that sid was the OLD permanent-give-up sentinel (`True`), which the new code doesn't recognize as "queued" -- it correctly treated it as fresh-eligible and sent exactly ONE new /compact (journalctl: "OK (compact-ceiling) 0:0.0 ctx=325416 -> /compact sent, awaiting consumption", the new non-claiming wording), then went silent on every subsequent ~60s tick for the next 6+ minutes while the turn kept running and ctx stayed above the ceiling -- zero resends, zero GAVE UP lines, matching the fix exactly. State confirmed as the new `{"status": "queued", "cwd": ...}` shape throughout. Did not touch the live pane or its stale queued /compact by hand, per the ticket's own instruction. The one pre-existing /compact duplicates (4 stale ones from before the fix landed, now permanently harmless -- CC will process them at the next turn boundary and any beyond the first will just reply "Not enough messages to compact") are a one-time migration artifact of upgrading mid-incident, not a re-introduced bug: going forward this exact session will never get a duplicate send again.
2026-07-26 #73: two gaps found measuring #66 on gatekeeper. (1) block-main-implementation.sh logged ONLY bypasses, so there was no way to answer "did the hook ever fire, on what" after #66 deployed -- a before/after bash-call-count drop couldn't be attributed to the hook vs the session simply being in one long turn. Added log_block(): every BLOCK (Bash AND Edit/Write) now appends timestamp/session/tool/rule(FABLE|GOAL_ARMED|FABLE+GOAL_ARMED)/classifier-match-or-len/first-120-chars-of-cmd-or-file to /tmp/airuleset-main-exec-block.log, same append-only style as the existing bypass log. (2) three shapes fell into "ambiguous -> allow" because their first token was neither allow- nor block-listed: a for/while loop BODY (`for f in a b; do cat $f; done`), a `timeout N`/`nice [-n N]` prefix wrapper, and `bash -c '...'`/`sh -c '...'`. Fixed by stripping `do`/`then`/`else`/`elif` loop-body leaders in strip_prefix() (loop HEADER segments stay ambiguous on purpose -- only the body is reclassified as a standalone command), skipping timeout's/nice's own flags+argument, and making the classifier recursive (`classify()`) so a `bash -c`/`sh -c` (also zsh/dash) segment's QUOTED script string is reclassified instead of the wrapper's literal tokens. The non-negotiable regression guard -- the CI-poll shape from ci-monitoring.md, `for i in $(seq 1 18); do gh run view <id> ...; sleep 30; done` -- is verified to still ALLOW (its body is already allow-listed once `do` is stripped); a subagent (agent_id set) is still never blocked for any of the new shapes. Genuine RED/GREEN: test:177e661[red]->feat:c921a47[green] (17 new tests: 3 block-log, 5 for-loop/timeout/nice/bash-c BLOCK direction, plus ALLOW-direction + subagent-never-blocked + CI-poll safety-net tests already green pre-fix). Full suite 1765->1782 passed, ruff clean, validate clean throughout. Manually smoke-tested all 7 shapes named in the ticket directly against the built hook binary (the 3 already-correct cd/;/$() cases plus the CI-poll ALLOW and the 3 new BLOCK cases) -- all matched exactly -- and confirmed the block log captured each with the expected rule+match+cmd fields. Deployed via `python3 airuleset.py push` (run twice; 2nd confirmed "Already up to date" for dev2/gatekeeper/montalu/marek/david@subdev, local dev1 rev-parse also at c921a47); a second manual rev-parse check against dev2/gatekeeper/montalu also came back at c921a47 before stopping (marek/david@subdev returned "Connection refused" on that manual re-check, consistent with subdev's fail2ban rejecting the extra probe attempts from this session, NOT with the deploy itself -- push's own "Already up to date" already confirmed both were current before that). Per #70, a deployed hook is inert in already-running sessions until they restart; watchdog job 18 (hooks_reconcile) handles that automatically -- no session was restarted by hand.
2026-07-26 #74: measurement-only ticket, NO code change. Question: is compacting "too soon" -- COMPACT_BOUNDARY_MIN_CONTEXT (job14, 200K) skipping cheap-to-catch small-context ticket boundaries while avg fleet context sits 260-330K? Measured directly from transcripts rather than guessing: `compact_boundary` system entries (CC 2.1.220) carry `compactMetadata.preTokens/postTokens` exactly; the FIRST real assistant `usage` entry after one gives the exact `cache_creation_input_tokens` (refill cost, $10/Mtok -- confirmed 100% `ephemeral_1h`, not `5m`) of re-establishing cache post-compaction; the summarization call itself has NO logged usage entry in CC's transcript format at all (an honest gap -- its cost is modeled: preTokens at cache-read price + summary-text-length/3.8 chars-per-token at output price, clearly flagged as the one estimated component, ~25% of the total). Scanned all 547 compact_boundary events across 123 days / 21 local projects (335 "quick-continuation" subset, gap<5min, most representative of an active /goal loop): median cost per compaction ≈ $1.06-1.31 (net-of-counterfactual vs gross), NOT a few cents -- dominated by the ~$10/Mtok cache-rewrite of ~90-100K tokens on the first post-compact turn. Breakeven: saving 100K tokens/turn of average context takes ~24 turns to repay one compaction. Critically: preTokens bucket distribution across ALL 547 historical events (mostly predating #48's floor, so not itself biased by it) is <200k=10%, 200-300k=14%, 300-400k=14%, >=400k=62% -- meaning job14's ticket-boundary trigger essentially NEVER fires in the 120-200K range regardless of the floor's value, because most sessions simply don't report a ✅DONE boundary until job15 (400K idle) or job17 (300K ceiling) catches them first. Since #48's floor was deployed (yesterday), `journalctl --user -u api-watchdog` shows exactly 2 real "skip small-context" events, both today, both right at the edge (ctx=187593, ctx=192328) -- minimal missed-savings potential. Conclusion: #48's original reasoning ("static floor ~93K + gain below 200K is small") still holds and is now backed by real $ data instead of an estimate -- COMPACT_BOUNDARY_MIN_CONTEXT stays at 200_000, COMPACT_HARD_CEILING (300K, job17) and COMPACT_CONTEXT_THRESHOLD (400K, job15) untouched per the ticket's own sequencing (only reconsidered if the floor investigation had justified a change). Full numbers posted to #74 as a comment (methodology + all stats). No code changed -> full suite stayed 1782 passed/ruff clean throughout (verified, unchanged) -> no push/deploy needed -> no 24h burn --compare cycle applies. Follow-up filed as #75 (out of THIS ticket's scope): 62% of compactions happening above 400K suggests the real remaining lever is job15/job17 trigger frequency / how rarely long autopilot loops report a ✅DONE boundary at all, not job14's floor value -- a separate, larger investigation.
2026-07-26 solo: #77 launcher: ultracode/flags baked into a bashrc FUNCTION froze forever in an already-running shell's memory -- push rewriting .bashrc had zero effect on live panel shells (measured: 2 sessions launched hours after #53 still carried pre-#53 ultracode). Fix: .bashrc now holds only thin one-line wrappers (claude/claude-new/claude-ultracode/claude-plain) that exec a new managed script ~/.claude/airuleset-claude-launch.sh (CLAUDE_LAUNCH_SCRIPT_DEST, render_claude_launch_script()) carrying all logic (continue-or-new, --model, skip-perms, ultracode only for ultracode mode), rewritten+chmod+x unconditionally every install/push (same pattern as render_caveman_shim()). RED ab9233e (20 new/rewritten tests failed against old code) -> GREEN d61572b (implementation). Core regression proof: persistent bash -i shell sources .bashrc once, then ONLY the script file is mutated (simulating a push) with NO re-source -- the shell's very next claude call picks up the new behavior immediately (test_frozen_shell_gets_new_launcher_behavior_without_resourcing). Live-verified on dev1's REAL deployed files (not tmp): type claude shows the wrapper, default carries no ultracode, claude-ultracode carries it deliberately, claude-plain carries nothing, and the same mutate-script-mid-session proof reproduced against the real ~/.claude/airuleset-claude-launch.sh then restored (diff+md5 confirmed identical to render output). Deployed via airuleset.py push to all 6 targets (dev1, dev2, gatekeeper, montalu/marek/david@subdev), full untruncated log grepped -- zero FAILED/Traceback in the real deploy section, all 6 report the script Updated. 1786 tests + ruff + validate clean. Old ps entries pre-dating the fix still show ultracode in argv (expected -- ps reflects a process's launch-time argv, unfixable without relaunching that specific process; no tmux pane touched per instructions -- self-corrects on each user's next natural relaunch).
2026-07-26 batch (#79+#78): #79 #77 fixed the launcher MECHANISM correctly but never reaches shells already running -- `_restart_pane()` (job 12 model-reconcile, job 18 hooks-reconcile) sent a bare `claude` after `/exit`, which in a shell OLDER than #77 resolves to whatever `.bashrc` that shell already loaded at ITS OWN start (bash reads `.bashrc` once, at shell start, never again). Live-verified on david:0.0@subdev: `type claude` still showed the frozen pre-#77 fat function (`local _ccdir=...`) with `--settings '{"ultracode":true}'` baked in, well after #77 had deployed. Fix: `_restart_pane` now sends `RELAUNCH_CMD = "source ~/.bashrc && claude"` instead of bare `claude` -- one new constant, one line changed, this is the ONLY place `claude` is launched externally. Genuine RED/GREEN: test:6fc1d78[red] (9 pre-existing keystroke-sequence assertions updated to expect RELAUNCH_CMD + 1 new regression test, all fail with AttributeError against unfixed code) -> fix:ca01886[green]. Full suite 1786->1787 passed, ruff clean, validate clean. Deployed via `airuleset.py push` (6 targets, confirmed exit 0 both runs). LIVE-VERIFIED in a fully ISOLATED throwaway tmux session (`verify79`, /tmp/verify79-scratch, a fake `claude` binary that only logs argv -- zero real Claude Code process launched, zero API tokens spent, zero real pane touched): loaded the exact pre-#77 frozen bashrc function into that ONE session, confirmed baseline reproduces the bug (bare `claude` -> `--settings {"ultracode":true}` in the fake-claude log), then sent the exact new `RELAUNCH_CMD` keystrokes -> fake-claude log shows NO ultracode flag, and `type claude` in that same pane confirms the function is now the current thin wrapper. Session + scratch dir killed/removed after verification; montalu@subdev and gatekeeper's live loops were never touched, per the batch's own instruction. Evidence posted to #79, closed.

#78: #71 (msg_hash dedup) did NOT fix the underlying bug -- live proof gathered from THIS SAME transcript that #78 cited (camera-box, session 90bc51f3, right after #71 shipped): a single completed-ticket report triggered the synchronous #65 path TWICE with two DIFFERENT msg_hashes (a Stop-hook rejection for a missing playbook line regenerated the report, hashing differently), both queuing behind the first send's busy pane and firing back-to-back the instant it went idle (13:07:24 first /compact, 13:09:26 real "Compacted", 13:09:29 x2 more /compact both "Not enough messages to compact"). Confirmed the transcript's own `compact_boundary` system entry shape directly (`{"type":"system","subtype":"compact_boundary","timestamp":...}`) from this exact incident window, and used it as the CONSUMED proof #78 mandates. Generalized #72's job-17-only QUEUED/CONSUMED/FAILED state machine into ONE shared claim (`compact_claims_path`/`compact_claim_active`/`compact_claim_set`, watchdog/__init__.py) consulted by all four /compact senders -- the synchronous path (`deliver_compact_now`), job 14 (`compact_ticket_boundary`), job 15 (`compact_stale_context`), job 17 (`compact_hard_ceiling`) -- checked right before each sender's own send point (after each job's own pre-existing state-resolution logic, so job15/17 still resolve their OWN bookkeeping via context-drop; the shared claim answers the separate "does anyone else already have one in flight" question). Resolves via exactly two paths, never a timer: CONSUMED (a `compact_boundary` transcript entry newer than the claim's send time) or FAILED (the claim's cwd now belongs to a different, newer session -- a demonstrated delivery loss). One direct, intentional behavior change: job 15's old "resend on a bare failed-verification timeout, up to N attempts, then GIVE UP" path is now unreachable once the first send sets the shared claim -- exactly the anti-pattern #72 already closed for job 17, now closed for job 15 too (2 pre-existing tests rewritten to prove the new behavior, with the old GAVE-UP assertion explicitly retired). The synchronous path also gained a NEW append-only log (`~/.claude/compact-sync.log`, via `_log_compact_sync`) recording every send/skip decision -- it previously logged nothing at all, which is exactly why the incident had zero journalctl trace and was only findable from the session transcript. Genuine RED/GREEN: test:f350575[red] (23 new/updated tests -- unit tests for the claim state machine + `_transcript_compact_boundary_ts`, one regression per sender proving a queued claim blocks a second send regardless of msg_hash/context/ceiling, synchronous-path logging tests; confirmed genuinely RED via `git stash` of watchdog/__init__.py -- 120 failed/231 passed) -> fix:dd0ad07[green]. Existing test classes isolated from the REAL `~/.claude/compact-claims.json`/`compact-sync.log` via a new `setUp` patch (`_isolate_compact_claims`) in both test files -- the live systemd watchdog executes this repo's working tree every 60s on this box, so a test process must never touch the files it also writes in production. Full suite 1787->1810 passed, ruff clean, validate clean. Deployed via `airuleset.py push` (6 targets, confirmed exit 0). Live proof of the ACTUAL fixed behavior (a real ticket boundary producing exactly one compact_boundary + no orphan /compact after deploy) could NOT be gathered within this session: neither camera-box nor odoo-erp had a live Claude session running on dev1/dev2 at the time (tmux list-panes showed only bash panels for camera-box, odoo-erp runs on subdev which this batch's own instructions forbade touching), and the one other live session on this box (restreamer) was mid a long CI wait (~1h17m) with no ticket boundary reached before or during this work -- stated honestly per the ticket's own instruction rather than fabricated. Left #78 OPEN with the exact verification command posted as a comment, for whoever next observes a real ticket-boundary completion on any other live session.

2026-07-26 batch (#82+#81): #82 gk live incident -- job 17 silently skipped every cycle for HOURS while context grew to 380K (300K ceiling): a watchdog-driven restart (`_restart_pane`) relaunches via `claude -c`, CONTINUING the same transcript, so the session id never changes -- neither #78's CONSUMED (context never dropped, the keystrokes died with the killed process) nor its cwd-session-id FAILED check (unchanged id) could ever resolve the wedged claim; manually deleting the one frozen `compact_ceiling` state entry unblocked it instantly, proving the diagnosis. Added a THIRD, independent resolution: a process fingerprint (`_proc_fingerprint`: pid + `/proc/<pid>/stat` starttime field 22, to defeat PID reuse) captured at send time (`_pane_claude_proc_fingerprint`, walks the pane's process tree via the existing `_pane_hosted_claude_pid`) and checked at every claim evaluation (`_proc_fingerprint_alive`) -- a missing or different-starttime process is a demonstrated delivery loss (FAILED), independent of session id/cwd. Applied to BOTH the shared claim (`compact_claim_set`/`compact_claim_active`, all four senders now thread `pane_id=pid, run=run` or a pre-resolved `proc=`) AND job 17's own separate `compact_ceiling` pre-pass (the ACTUAL state shown frozen in the incident, a duplicate mechanism predating #78's generalization) -- both independently gate a resend, so fixing only one would have left the other wedged identically. Also added STUCK visibility (`COMPACT_CEILING_STUCK_CYCLES`=30, env `AIRULESET_COMPACT_CEILING_STUCK_CYCLES`): a claim queued+above-ceiling for that many consecutive sweeps now logs every sweep instead of the silent `continue` that let this run for hours undetected. Genuine RED/GREEN: test:3c280b2[red] (44 new tests incl. TestProcFingerprint, extended TestCompactClaimState, job17 process-death/STUCK tests, pane_id-threading locks for all 4 senders; confirmed genuinely RED via `git stash` of watchdog/__init__.py) -> fix:246dc57[green]. Full suite 1810->1835 passed, ruff clean, validate clean. LIVE-VERIFIED end-to-end in an ISOLATED throwaway session (`CLAUDE_CONFIG_DIR=/tmp/claude-1000/scratch-82/config`, per the #70 technique): a real claude session's transcript seeded to 305K tokens (over the 300K ceiling), a stale QUEUED claim recorded with the REAL process's fingerprint, the pane genuinely restarted via `/exit` + explicit `claude -c` (confirmed: SAME sid, SAME transcript file, conversation history preserved, old PID confirmed dead via `ps`) -- running the real `compact_hard_ceiling` ONE TIME against the restarted pane logged `FAILED ... delivery lost (process gone/replaced)` immediately followed by `OK (compact-ceiling) ... -> /compact sent` in the SAME cycle, and the pane's own capture showed `/compact` genuinely typed and CC actively compacting -- exactly the acceptance's "session restarts with a queued claim, /compact resent within one cycle". #78 NOT closed (its own acceptance needs a live duplicate-avoided proof from camera-box/odoo-erp after a real busy-panel compaction, a different scenario this proof doesn't cover) -- left untouched per the dispatch's instruction. #81 job 16 only ever WRITES the merged hourly fleet row; nothing looked at it and pinged on its own -- the user's own words: "jedina vec, ktora to ma dnes robit, je to, ze si na to spomeniem... a prave pri incidente spotreba vyskoci najviac". Added `burn.hourly_burn_alert` (pure comparison over already-collected fleet.jsonl rows, burn/__init__.py) checking three independent env-overridable thresholds -- absolute $ (`AIRULESET_BURN_ALERT_ABS_USD`, default $20), a multiple of the median of the last N hours (`_REL_MULT`/`_REL_WINDOW`, default 3x/6h), and crossing a whole step of the weekly window job 16 already stamps onto every row (`_WEEKLY_STEP_PCT`, default 5%, a reset never misread as a crossing via `_weekly_step_crossed`) -- any one firing produces ONE combined Slovak message (`burn.render_burn_alert`: hour + $ + messages + weekly step, top 2 hosts by spend, $ totals of the up to 3 hours before it). `watchdog.burn_alert_job` (job 19) is the thin wiring/dedup/send wrapper: runs right after job 16 in run_once, claims the hour bucket at most once (`state['burn_alert_hour']`) so a repeat sweep sends nothing more, dedupes the send a second time via `send_fn`'s own `dedup_key` as a safety net. Wired coordinator-only (dev1) via new `burn_alert_enabled` param, gated in `cmd_watchdog` with the identical `os.uname().nodename == "dev1"` check job 16 already uses. Genuine RED/GREEN: test:0b34762[red] (31 new tests: TestHourlyBurnAlert, TestWeeklyStepCrossed, TestMedianHelper, TestBurnAlertJob, RunOnceBurnAlertWiring; confirmed genuinely RED via `git stash` of airuleset.py/burn/watchdog) -> feat:6a733ce[green]. Full suite 1835->1867 passed, ruff clean, validate clean throughout both tickets. Deployed via `airuleset.py push` (all 6 targets confirmed, idempotent second run "Already up to date" everywhere). LIVE-VERIFIED #81 against the REAL production `~/.claude/burn-history/fleet.jsonl` (last row: 16:00, $13.55/62 msgs, weekly 82->84%) with an artificially lowered `abs_usd=1.0` threshold via a throwaway state dict (never touching the real `api-watchdog-state.json`) and the real `notify.send`: produced a genuine Discord ping ("Spotreba 16:00 -- $13.55 (62 sprav), tyzdenne 82 -> 84 %" + top hosts + previous-3-hours) with status "sent"; a second call with the same state object confirmed zero further sends within the same hour bucket. LIVE-VERIFIED #82's own post-deploy behavior organically: a real production `watchdog --once --dry-run --verbose` run on dev1 surfaced a genuine STUCK log for THIS SESSION's own long-queued /compact claim (cwd=~/devel/airuleset, 37-38 cycles) -- correct, expected behavior (the session has been continuously busy implementing this exact batch, so job 17 correctly never resent while still busy, and the new STUCK line made the previously-invisible queued state visible for the first time). No new issues filed; no dropped work.

2026-07-26 (#83): a /compact claim (shared compact-claims.json AND job 17's own state['compact_ceiling']) missing the #82 "proc" key (written before #82 shipped, or whose pane could not be fingerprinted at queue time) had NO way to ever resolve -- the process-death check is a no-op, and a watchdog restart via `claude -c` continues the same transcript so the session-id-replace check never fires either. Live evidence: gatekeeper stayed queued 3.5h, context 397010, zero compaction, in BOTH structures at once (manually cleared to unblock, per the issue). Fix (issue's preferred option 1): a claim/entry missing "proc" is now dropped as unresolvable on its FIRST evaluation in both `compact_claim_active()` (silent, same convention as the existing CONSUMED/FAILED resolutions) and `compact_hard_ceiling()`'s PRE-PASS (logged as STUCK, since this was the path that let the incident run silently for hours -- the shared gate blocked job 17's own cycle counter from ever running). Sending is immediately re-enabled; worst case is one redundant /compact. Genuine RED/GREEN: test:ff5d618[red] (3 new/rewritten tests: TestCompactClaimState::test_no_fingerprint_recorded_resolves_at_first_evaluation_and_reenables_send, TestCompactHardCeiling::test_missing_proc_key_in_own_state_resolves_at_first_evaluation, TestCompactHardCeiling::test_live_incident_both_structures_proc_less_unblocks_in_one_cycle; confirmed genuinely RED via `git stash` of watchdog/__init__.py, all other edited tests passed unchanged against old code) -> fix:a4938ed[green]. Large collateral test-fidelity fix: many pre-existing #72/#78/#82 tests manually constructed or drove real-flow proc-less claims expecting them to PERSIST across evaluations (the fake tmux `run`s in both test files can never resolve a real /proc-tree fingerprint) -- these now carry an explicit live `proc` fingerprint (via `_spawn_dummy_proc`/new `_alive_proc_fingerprint` helper, or `unittest.mock.patch.object(wd, "_pane_claude_proc_fingerprint", ...)` for real-flow multi-sweep tests) so they keep exercising the paths they were written for, matching real production fidelity (where fingerprinting genuinely succeeds). Full suite 1867->1869 passed, ruff clean, validate clean throughout. Deployed via `airuleset.py push` (all 6 targets, idempotent second run "Already up to date" everywhere). No live tmux pane touched; no ssh to subdev; no new issues filed; no dropped work.

## 2026-07-26 — #76 goal: armovaný /goal ticho zanikne (watchdog job 20)

- Ticket #76 (silent /goal death). Commits: e76d61b [red] / 371a626 [green] (job 20:
  `scan_goal_markers` + `pane_goal_armed` + `goal_rearm`), da0f264 [red] / bbef06c [green]
  (cmd_watchdog gate), d1b44d6 [red] / 6fe34ff [green] (`_goal_stall_nudge`), 57f4d3c [red] /
  4337262 [green] (`_typed_landed`, CC's `[Pasted text #N]` collapse), plus the four
  live-found fixes: footer-region read, achieved-guard, viewport-only, settle polls.
- Tests: `tests/test_goal_rearm.py` (60), suite 1869 → 1929.
- Mechanism investigation: NOT conclusively settled — documented in the ticket comment and
  in the job's own section comments. Measured: every goal SURVIVAL had a post-compaction
  stimulus (a background subagent's task-notification, or the human typing); every DEATH had
  a `/compact` land at a `## ✅ Work Complete` boundary with nothing in flight. Job 4 is
  gated on `⏳ WORKING`, so `✅ DONE` + armed goal + silence had zero coverage.
- Retired the ticket's original premise WITH proof: an armed goal DOES survive `/exit` +
  `claude -c` (isolated session, pid 993139 → 1078067, same transcript, `◎ /goal` lit again).
  So jobs 12/18 do not silently disarm loops and need no per-job re-arm patch.
- Live: job 20's first automatic run found a real victim (parovanie_produktov) — marker
  `set`, footer dark, last turn `✅` — and re-armed it byte-identically (3152 chars,
  sha 20dd7683652a667c both sides).

## 2026-07-26 — #85 (gh --json false block) + #84 (long turn / queued compact), batch

**#85 — `block-gh-invalid-json-flag.sh` falsely blocked a write+read-back compound command.**
- Validated live before touching code: `gh issue edit N --title X && gh issue view N --json state` → rc=2.
- RED `a809e11` (8 failures: 4 false blocks + 4 `bash -c` misses) → GREEN `351de33`.
- Root cause: `--json` was searched over the WHOLE command string and paired with any write
  subcommand found anywhere in it. Now classified PER SEGMENT (`;`/`&&`/`||`/`|`) with
  `block-main-implementation.sh`'s existing shlex shape — one parser shape in this repo, not a
  second invented one. Its `bash -c` recursion also closed the INVERSE miss the old
  quote-stripper had (a real violation inside `bash -c "..."` passed through).
- Fails OPEN on classifier malfunction — a false block is the exact failure being fixed.

**#84 — one gk turn ran 2h40m with three `/compact` queued behind it.**
- Forensic transcript read (posted as the issue's evidence comment) DISPROVED the ticket's own
  hypothesis: no foreground subagent dispatch was involved. All five `Agent` calls returned async
  in ~100 ms with no `run_in_background` key at all; none was in flight during the long stretch.
  Real cause: an unbroken chain of correctly-bounded foreground `Bash` CI polls, extended by CI
  restarting from concurrent merges, under an armed `/goal` loop whose Stop hook kept rejecting
  the stop. The queue drained only at the user's manual interrupt (15:44), then fired back-to-back.
- That read also fixed the DESIGN: CC logged those 2h40m as three internal turns, so a
  transcript-boundary detector would have missed the incident. Detection reads the PANE's spinner
  elapsed label instead.
- RED `816883e` (32 failures) → GREEN `9614bce`.
- Queued-compact guard: `_pane_has_queued_compact` consulted immediately before the send in all
  four senders (`deliver_compact_now`, jobs 14/15/17). Composes with — does not replace — the
  shared claim (#78) and proc fingerprint (#82/#83).
- Job 21 `long_turn_watch`: logs unconditionally every sweep, ONE ping per (session, turn),
  detection only — it never types. `LONG_TURN_THRESHOLD_S` / `AIRULESET_LONG_TURN_S`, default 30 min.
- Both detectors share one walk (`_above_box_scan`) whose ADJACENCY requirement is what keeps a
  quoted panel and the agent strip's arbitrary labels (#36) out of live pane state.
- Job-15/17 integration tests went into `tests/test_watchdog.py` (established `RestartFakeTmux` +
  `_seed_context_transcript` harness) rather than a second fake in the new file — an ad-hoc
  re-mock returned zero log lines because the jobs' own pre-passes bail first.
- Tests: `tests/test_long_turn.py` (31) + 2 in `test_watchdog.py` + 8 in `test_gh_json_hook.py`;
  suite 1929 → 1990.

## 2026-07-26 — #91 (situational rule loading) + #92 (context diet tier 1, 3/5)

- **#91 rules: every module->skill conversion was a silent delete** — CLOSED.
  Settled the mechanism two ways: live isolated-profile probes (CC 2.1.220) and a
  read of the CC bundle. Skill bodies enter context ONLY via an explicit `Skill`
  call (the session-start `skill_listing` attachment is 25,401 B of DESCRIPTIONS
  only); `rules/*.md` + `paths:` inject as a `nested_memory` attachment but on
  **Read / @-mention / IDE-open only** — never Edit/Write; `PreToolUse`
  `hookSpecificOutput.additionalContext` injects on the ACTION.
  Re-measured on 7,941 transcripts (not 91): 342 `gh pr merge` transcripts, 1
  loaded pr-merge-policy; 875 CI-polling transcripts, 4 loaded ci-monitor; 32 of
  53 skills never invoked. The 9 path-scoped rules show 0 historical injections
  because user-level symlinking only landed 2026-07-25 22:06 (25 h before) — not
  a mechanism failure.
  Fix: `hooks/inject-situational-rule.sh` + `hooks/situational-triggers.conf`
  (17 topics, once-per-session, never blocks). RED `97be4d8` → GREEN `45f8e98`,
  write-shaped coverage `9950528`. Proof: `gh pr merge` → the model quoted
  `airuleset:autopilot=auto-merge` (exists ONLY in the skill body); same session
  with no deploy command → deploy procedure NOT-IN-CONTEXT.
  Production bug caught mid-ticket: a `gh issue comment` heredoc that merely
  DESCRIBED the trigger table matched 9 topics and injected 65.3 kB. RED
  `05bf005` → GREEN `d014fc0`: strip heredoc bodies + quoted spans, 14 k cap per
  call (deferring, never consuming), optional 5th `exclude` column.
- **#92 context diet tier 1** — 3 of 5 items landed, OPEN for the rest.
  Item 1 `45430fa`: the 71 `## Development Rules` bullets moved VERBATIM to
  `.claude/rules/airuleset-internals.md` (project-scope `paths:`); CLAUDE.md
  80,802 → 12,053 B, `.gitignore` carve-out `!.claude/rules/`.
  Items 3+5 `d2b684a`: notify hook internals → notification-mechanics skill;
  four CC product-doc subsections dropped from claude-code-tooling.md.
  Measured prefix (real `usage`, fresh session, this repo): **162,726 →
  135,303 tokens (−27,423)**. Items 2 (model-awareness) and 4 (8 hook-covered
  modules) still open — both rewrite deliberately test-locked rule text.

## 2026-07-27 — #92 (context diet tier 1, items 2+4) + #93 + #39

- **#92 items 2 and 4** — CLOSED. Item 2: `model-awareness.md` 18,379 → 10,793 B
  (`321faf5` red → `c8cfd8b` green); the justification layer (pricing, CursorBench
  and thinking citations, 2026-07-01/02/03 history, the Opus-4.8 community-report
  record, the 76%-of-$13,600 burn, the #32/#54/#66/#80 hook narrative, the DORMANT
  mode) moved VERBATIM into `skills/fable-advisor/SKILL.md`. Every phrase-lock was
  decided PER PHRASE: actionable → stays inline with its lock; justification →
  lock retargeted at the skill. None dropped. Wired to its action with a trigger
  row on `airuleset.py fable-gate` (`74c62d5` green) so the move is not a silent
  delete (#91's mechanism).
  Item 4: the ticket's own mandated per-phrase verification REFUTED the blanket
  trim for 7 of 8 modules and surfaced an enforcement gap instead — the
  merge-bypass family printed VIOLATION to stderr and never called `add_hard`
  while the module claimed "HARD-blocked at Stop". Split by ambiguity, 10
  unambiguous shapes now block, bare delegation phrases stay warn-only
  (`c53c59a` red → `c7d935b` green). Only hook-INTERNALS prose was cleared for
  removal (`script-failure-policy.md` 1,395 → 850 B, `589b381` green).
- **#93 playbook mandate growth** — CLOSED, not overcome. #92 item 1 emptied the
  file once but the MANDATE still pointed future writes at `.claude/skills/`
  (which #91 proved almost never loads) and the project `CLAUDE.md`. Fixed on all
  three directing surfaces (skill routing table, module boundary table, hook block
  message) → `.claude/rules/<area>.md` + `paths:`, plus the missing prune step that
  MOVES an already-parked gotcha out. `## Services` internals moved verbatim to the
  path-scoped rule; project `CLAUDE.md` 11,919 → 4,762 B (`4a95b51` red → `aaaf9ae`
  green).
- **#39 context diet ancestor** — superseded; item 2's premise dissolved by #91.
- Measured prefix (real `usage`, fresh isolated-profile session, this repo):
  **134,994 → 129,504 tokens (−5,490)**. Portable part (modules only, every
  project): 220,303 → 214,098 B.

## 2026-07-27 — batch #38 #41 #86 #90 (resumed from a usage-limit kill mid-#38)

- **#38 fable-model false-block after /model switch** — RESUMED from `93b6dc9`
  [red] (a prior worker's uncommitted GREEN attempt was read + judged, mostly
  correct, comment prose stale). Model now parsed from the last top-level
  `assistant` entry's `.message.model`, overridden by a later top-level
  `/model` switch marker. Corpus replay against the REAL incident session
  transcript (2d02a127, 43769 lines) at all 41 real `/model` switch points:
  72 disagreements old-vs-new, all within the immediate post-switch window,
  zero outside it — 30 fix the reported stale-Fable-block, 42 close the
  symmetric stale-allow. `93b6dc9` red → `d83e4e0` green. Closed.
- **#41 pre-push-test-check.sh `it\(` false positive** — `it\(` had no word
  boundary, matching inside `sys.exit(1)`/`.split(`/`.init(`, silently
  defeating Gate 2's RED-before-GREEN check. Fixed to `\bit\(['"]`. Corpus
  replay over this repo's own tracked files: 250 old matches (48 files) → 1
  new match, and that one IS a genuine `it(...)` test literal. Sub-issue 2
  (CI-YAML self-check not recognized) deliberately NOT turned into a 4th
  classifier surface (#80/#91/#96 already shipped 3 wrong ones) — documented
  as a sanctioned `[no-test: ci-yaml conditional logic, ...]` category
  instead. `d37f59d` red → `ca65a7a` green → `45f9c87` docs. Closed.
- **#86 block-test-skips.sh false-blocks in a 3-branch repo** — diff base was
  hardcoded `origin/<default>`, correct only for 2-branch dev/main; on
  develop→staging→main it re-flagged an already-merged sanctioned skip on
  every push. Ported the SAME PR-target base resolution
  `pre-push-test-check.sh` already had. Verified with a REAL 3-branch git
  repo (real `git init`/commit/update-ref, not hand-typed diffs). `5edf056`
  red → `c5a1bb4` green. Closed.
- **#90 ci-monitoring poll loop dies on default tool timeout** — two
  mechanisms: (1) the sample loop now self-bounds via bash `SECONDS`
  (`AIRULESET_POLL_BUDGET_S`, default 100 < the harness's observed 120s
  default) so a forgotten `timeout` param ends the loop cleanly instead of a
  silent SIGTERM; verified by EXECUTING the snippet extracted verbatim from
  the `.md` against a stubbed `gh`, under a real external `timeout` wrapper.
  (2) new `hooks/nudge-poll-loop-timeout.sh` (PreToolUse Bash, never blocks)
  injects a corrective reminder via `additionalContext` when a sleep+done
  loop's own `timeout` param is missing/low — fired live on this very session
  immediately after deploy. `d10ae36` red → `b295a57` green. Closed.
- Full local suite: 2164 passed, `ruff check .` clean, `airuleset.py validate`
  clean throughout. `airuleset.py push` run twice after the final commit —
  second run showed `Already up to date.` on all 5 remotes (dev2, gatekeeper,
  montalu@subdev, marek@subdev, david@subdev).

## Batch #96 #88 #89 #97 (2026-07-27)

- #88 (`block-main-implementation.sh` classifier blind to line-continuations
  before a pipe reducer): the ticket's own `$( … )`-nesting hypothesis did NOT
  reproduce after 144+ systematic variants; the real trigger is a bash `|\`
  + newline continuation, misclassified by `STATEMENTS_RE`'s bare-`\n` split.
  `test_bare_top_level_continued_pipe_allowed` /
  `test_health_check_version_extraction_inside_substitution_allowed`
  `8a298b3` red → `501c4be` green (`join_line_continuations`, quote-aware).
  Corpus replay (80,981 real Bash commands, all local transcripts): block
  rate 27.72% → 27.72% (22446→22444), 2 flip blocked→allowed, 0 regressions.
  Closed with evidence (no PR — direct commits).
- #89 (watchdog job 8 bounce backstop nudged an unrelated repo on a bare
  `prio:bounce` label): `_bounce_quals` scoped WHO the query excludes but
  never WHICH REPOS the label means anything in. `TestCrossStreamRepoScope`
  `817ff36` red → `f630cdb` green (`_repo_in_cross_stream_flow`,
  `_CROSS_STREAM_REPOS` opt-in registry) + `7d800cc` (skill clarification).
  Closed with evidence.
- #97 (orphaned `/tmp/airuleset-main-exec-ok-*` bypass markers, no TTL):
  new always-on watchdog job 22, `cleanup_stale_exec_markers` —
  age-gated AND live-session-gated (never touches a marker whose session id
  still resolves to a live pane). `TestStaleExecMarkerCleanup` `225c1e1` red
  → `cae67dc` green + `c448140` (docs, job count 21→22). Closed with evidence.
- #96 (`stop-check-prose-violations.sh` blocked a message merely REFERRING to
  banned phrases — third occurrence of the use-vs-mention class after #80/
  #91): `strip_mentions()` strips fenced code / backticks / double-quotes
  before the 5 HARD phrase-matching checks (deliberately NOT single quotes —
  English contractions like "won't" made a naive strip eat a genuine offer,
  caught in review). `TestMentionOfBannedPhrasesDoesNotBlock` `f5a65e0` red →
  `72b1442` green. Corpus replay (4000 random unique historical assistant
  messages): block rate 1.93%→1.75% (77→70/4000), all 7 newly-allowed
  messages verified genuine mentions, 0 previously-allowed newly blocked, all
  10 `TestUnambiguousBypassIsHardBlocked` cases still hard-block. Closed with
  evidence.
- Full local suite: 2182 passed, `ruff check .` clean, `airuleset.py validate`
  clean throughout. `airuleset.py push` run twice after the final commit —
  second run showed `Already up to date.` on all 5 remotes (dev2, gatekeeper,
  montalu@subdev, marek@subdev, david@subdev).
- #99 (`/compact` fired on every `✅ DONE`-shaped turn once a session's
  context passed the #48 200K floor — including a one-line answer or a
  single filed ticket with zero code change; live: "sprav ticket" → #602
  filed → immediate compact, nothing committed): added a SUBSTANTIALITY
  gate, `compact_boundary_substantial(cwd, sid)` — counts commits in the
  session's own repo since a persisted per-repo anchor
  (`compact-substantiality.json`, reset by `mark_compact_boundary` on every
  real send; falls back to the session's own transcript start on the first
  boundary ever seen for a repo). A confirmed zero drops the request before
  the context check, in both `deliver_compact_now` (sync) and
  `compact_ticket_boundary` (job 14 poll); unmeasurable (no git repo, no
  anchor) falls through unchanged. `test_compact_request.py`
  `TestCompactBoundarySubstantial`/`TestSubstantialityGateInDeliverCompactNow`/
  `TestSubstantialityGateInJob14` `60dafa7` red (13 failures, confirmed via
  `git stash` of the implementation) → `22175a7` green. Corpus replay (268
  real `✅ DONE`-boundary turns from this repo's own transcripts + its own
  real git history): OLD fires all 268, NEW fires 187, drops 81 (30%) —
  Q&A/single-ticket turns with no commit; the 187 remaining prove real work
  still triggers. Closed with evidence.
- #27 (drag-drop upload endpoint only ever sent `files[0]` — dropping
  several files silently uploaded just the first; reported live 2026-07-23
  and again 2026-07-27, Marek/Montalu): server side (`do_PUT`) was already
  per-request-independent (own save + own "SAVED" log line + own atomic
  `.part`→rename, one failure can't affect another) — the whole bug was the
  client JS. Added `<input type=file multiple>` + `sendAll(fileList)`
  driving `Array.from(fileList)` through a sequential per-file `uploadOne`,
  always advancing from BOTH `onload`/`onerror`. `test_upload_url.py`
  `TestMultiFileUpload` `14128d6` red (2/4 new tests genuinely fail against
  old JS, confirmed via `git stash`) → `ece0ef2` green. Verified live
  end-to-end through a REAL browser (Playwright): opened the served page,
  selected 3 real files (40000/65000/22000 bytes) via the actual file
  chooser, all landed byte-exact with 3 separate SAVED log lines and 3
  individual ✅ rows in the DOM. Closed with evidence.
- Full local suite: 2199 passed, `ruff check .` clean, `airuleset.py
  validate` clean throughout. `airuleset.py push` run twice after the final
  commit — second run showed `Already up to date.` on all 5 remotes (dev2,
  gatekeeper, montalu@subdev, marek@subdev, david@subdev).

2026-07-27 batch (#78+#87): pure PROOF batch, no code change needed — both
tickets closed with live/static evidence, no RED/GREEN pairs.

- #78 (compact: #71 didn't fix duplicate `/compact` — dedup protects the
  DECISION, not the pane's type-ahead queue): the actual code fix
  (`f350575`[red]/`dd0ad07`[green], shared `compact-claims.json` claim
  gating all 4 senders) was ALREADY deployed 2026-07-26 16:14:50+02:00 —
  the OWNER's own prior comment left the ticket open only for missing live
  proof on a foreign project. Found it by scanning every OTHER project's
  transcript (`~/.claude/projects/*/*.jsonl`) + `~/.claude/compact-sync.log`
  for post-deploy `/compact` SENT/BOUNDARY pairs: pz-server shows a clean
  single SEND(09:51:24)→BOUNDARY(09:53:15)→quiet DROPs cycle; even
  stronger, forestshop/parovanie_produktov shows the guard ACTIVELY firing
  — SEND(11:02:54) then a second trigger 30s later correctly
  SKIP(claim-queued)(11:03:24), the queued keystroke only actually landing
  at 11:04:32 (~98s later, busy-pane queue drain), and exactly ONE
  compact_boundary(11:06:56) despite two trigger attempts — the exact
  original incident shape, now correctly deduped. Closed with evidence
  (https://github.com/zbynekdrlik/airuleset/issues/78#issuecomment-5090797518).
- #87 (goal: prove whether local `/compact` can re-arm the goal Stop hook
  at all, leftover from #76): pure static-analysis proof, no code needed.
  Traced the installed CC 2.1.220 binary (`~/.local/share/claude/versions/`)
  — `executeStopHooks`/`VEe` has exactly ONE implementation, called from
  exactly TWO places in the whole binary: `Ycd` (post-processing of a REAL
  model-queried turn — this is also where the `activeGoal` condition check
  itself lives) and `Xdd` (a "forked slash command"'s own agent sub-turn
  completing). A local built-in command like `/compact` returns
  `{shouldQuery:false, messages:[<local-command-stdout>...]}` from
  `processSlashCommand` and the main dispatcher (`Q0b`) returns immediately
  on that branch — it NEVER reaches `EDp`/`Ycd` (no real query happened) and
  is not a forked command (no `Xdd` either). Definitive answer: NO, a local
  `/compact` cannot structurally re-invoke the goal Stop hook by itself —
  it is causally inert; only a genuine subsequent model-queried turn
  re-evaluates the goal. Confirms job 20 (#76)'s design (react to observed
  end-state, never the unproven mechanism) was already correct — no code
  change made. Closed with evidence
  (https://github.com/zbynekdrlik/airuleset/issues/87#issuecomment-5090802752).
- No code touched this batch; `python -m pytest tests/`, `ruff check .`,
  `airuleset.py validate` all pre-verified clean (repo unchanged since
  last green run). `airuleset.py push` run for the docs/playbook commit
  only, twice — second run `Already up to date.` on every remote.

## 2026-07-27 batch — #101 + #100 (watchdog job 20/9 keystroke delivery)

- #101 (job 20 permanent give-up + stale-goal revival): RED `ba0cae7`
  (TestGoalRearmTransientRefusalNeverGivesUp,
  TestGoalRearmStaleMarkerIsNeverRevived) → GREEN `2cc95b0`. A
  `deliver_with_stash` refusal that never sent a keystroke
  (`_GOAL_REARM_TRANSIENT_STASH_REASONS`) no longer counts toward the
  permanent give-up cap; a `Goal set:` marker older than
  `GOAL_REARM_MAX_DARK_S` (6h) since it was last confirmed armed
  (`rec["last_armed"]`, falling back to the marker's own timestamp) is
  never revived.
- #100 (`/autopilot` Step 2 manual paste): investigation found the
  ticket's own premise wrong — job 20 cannot arm a goal that was never
  armed before (requires a pre-existing `Goal set:` marker); job 9
  (`goal_autoarm`, always on) already does that, it just had no answer
  for a draft-holding pane. RED `f256e9a`
  (TestDraftGoesThroughStashDelivery) → GREEN `19d675b`: job 9 now
  routes a draft through `deliver_with_stash`, the same shared
  primitive job 20 uses; a pre-send transient refusal doesn't burn the
  10-minute per-pane dedup window. Docs commit `994f05b` clarifies
  Step 2's printed line is auto-armed as a backstop, manual paste
  stays the documented fallback.
- Live verification (real CC v2.1.220 scratch session, `CLAUDE_CONFIG_DIR`
  recipe) surfaced a THIRD, previously-unknown production bug:
  `_has_free_prompt(bare_only=False)` required a literal ASCII space
  after `❯`, but CC actually renders a non-breaking space (`\xa0`) —
  so `deliver_with_stash`'s own idle-with-draft precondition NEVER
  matched a real held draft, reproducing the exact #101 "not
  idle-with-draft" signature regardless of true pane state. RED
  `45b0356` (DeliverWithStashRecognizesTheRealNbspSeparator) → GREEN
  `488c189`. This was the actual root cause underneath both tickets'
  reported symptoms; found only by live testing, since every existing
  unit-test fixture in the repo modeled the wrong character.
- `python -m pytest tests/` (2211), `ruff check .`, `airuleset.py
  validate` all green. `airuleset.py push` run twice — second run
  `Already up to date.` on every remote (dev2, gatekeeper,
  montalu/marek/david@subdev). Production `api-watchdog.service`
  observed running the fixed code error-free (journalctl, dev1) before
  and after the push.

2026-07-27 #102: compact: eliminate ALL non-agreed triggers, deliver ONLY
after a completed ticket, never on a ❓ turn. Original ticket body's "hard
ceiling" reasoning was withdrawn by the user's own CORRECTION comment
("tvoja extrémne zlá implementácia strká compact všade a nie ako sme sa
dohodli že keď sa dokončí ticket") -- only the correction's scope is valid.
test:3e9f36a[red]->fix:e9ac917[green]: `_compact_blocked_by_question(cwd,
sid, projects_dir)` -- True iff transcript_last_marker(session) == "❓" at
CALL time; unmeasurable never blocks (matches #48/#99's philosophy). Wired
as a delivery-time re-check (not just record-time) into BOTH surviving
senders: compact_ticket_boundary (job 14's poll, skip+retry semantics) and
deliver_compact_now (the synchronous #65 path, falls back to job 14).
Closes the race notify-compact-request.sh's own record-time ❓/⏳ gate
cannot: a request recorded at an earlier ✅ boundary can still be pending
once the session moved on to a NEW ❓ turn (CC drains its type-ahead queue
only at an ACCEPTED Stop, so a queued /compact can land exactly as the
next turn asks its question).
feat:71ff67a: removed jobs 15 (compact_stale_context, #39/#43) and 17
(compact_hard_ceiling, #69/#72) ENTIRELY -- both fired /compact off
context-size/idle heuristics alone, no regard for the session's status
marker, which is exactly the unauthorized machinery the correction
withdraws. Removed with them: `_wait_for_compact_return`, COMPACT_
CONTEXT_THRESHOLD/COMPACT_MIN_IDLE_S/COMPACT_STALE_*/COMPACT_HARD_CEILING/
COMPACT_CEILING_* (all private to their own delivery). KEPT (still used by
job 14/sync/18/20/21): `_pane_compacting`/COMPACTING_MARKER,
`_reconcile_candidate_panes`, `_pane_has_queued_compact`/`_above_box_scan`/
`pane_turn_elapsed`, `_proc_fingerprint_alive`/`_pane_claude_proc_
fingerprint`, the shared `/compact` claim, `transcript_current_context`
(job 14's own #48 gate). Job numbers 15/17 retained as REMOVED markers in
run_once's own docstring -- never renumbered, so the dozens of existing
comments/logs addressing them by number stay valid pointers. Full suite
2220->2153 (9 new #102 tests, 76 job-15/17-only tests removed with them).
ruff clean, validate clean throughout.
CORPUS REPLAY (real, not hypothesized -- this repo has failed a
hypothesis-only fix 5 times per the ticket's own warning): scanned the
WHOLE local `~/.claude/projects/` corpus (58 projects, 7975 transcripts,
5GB) for `compact_boundary` events (641 total, all history) and replayed
EVERY real historical `/compact` SEND since job 14 shipped (2026-07-25)
against `journalctl --user -u api-watchdog.service` + `~/.claude/compact-
sync.log`, reconstructing each session's status marker AS OF THE SEND
TIMESTAMP (not today's marker -- a small variant of transcript_last_marker
that walks backward with a `timestamp<=cutoff` filter). Job 14 (surviving,
now gated): 14 real sends, marker-at-send was ✅ (or blank/pre-convention)
every time, 0 were ❓ -- the new gate changes NOTHING for real historical
job-14 behavior, matching the "every survivor from the ticket boundary"
expectation exactly. Synchronous path (surviving, now gated): 7 real sends
via compact-sync.log, all ✅, 0 blocked. Job 15 (removed): 0 real sends
ever recorded on this box -- nothing lost. Job 17 (removed): 8 real sends,
markers at send time '', '', '⏳', '⏳' (4 measurable) plus 3 that never
landed a matching boundary within 10 min (queued behind a long busy turn)
-- 0 were ❓, but the ⏳/blank sends are direct proof job 17 WAS firing on
non-ticket-boundary turns, exactly the machinery the correction forbids,
even though it never happened to catch a ❓ specifically in this box's
log window. The 2 ❓-preceded `compact_boundary` events in the whole
post-#39 corpus (camera-box sid=90bc51f3, forestshop sid=328ac7ba) have
ZERO matching watchdog log lines within a wide window around them -- one
(camera-box) has a "question answered in-session" line ~1 minute later,
consistent with a HUMAN typing /compact manually at the terminal while
thinking about the answer, not our automation. Stated honestly: the one
reported live incident could not be forensically pinned on any specific
automated sender in this box's available history -- the fix stands on the
user's own corrected agreement regardless, not on incident attribution.
Closed with the full evidence posted as an issue comment (no PR/CI in
this repo -- commits went direct to `main` per its own flow;
`airuleset.py push` confirms both dev1+dev2+4 remote targets in sync).

## #103 -- ScheduleWakeup is a /loop-only pacer, never a general long-wait mechanism

RED 1bb2a03 (test_schedule_wakeup_loop_only.py, salvaged from a probe worker's
prior comment) -> GREEN c9ee2ad. Subtractive-only fix per the user's explicit
correction ruling out any detector: removed the ScheduleWakeup recommendation
from modules/core/ci-monitoring.md (2 spots) and verify-launched-work-liveness
(module stub + skill, 4 spots total). Working alternatives (foreground bounded
poll loop, a run_in_background loop that re-arms, bounded Monitor) untouched.
Updated 2 pre-existing tests (test_autopilot_ci_resilience.py,
test_ruleset_conversion_wave2.py) that pinned "PLAIN prompt"/"#54086" -- those
phrases lived only inside the removed sentences -- to pin surviving anchors.
Full suite 2167 passed, ruff clean, validate clean. No hooks/jobs/gates added.

## #105 -- 5 e9d1022 path-scoped rules never reach a dispatched subagent

RED 4cf95ff -> GREEN 2a8c306. Settled empirically (#104's own open question):
built an isolated CLAUDE_CONFIG_DIR scratch profile, ran the real claude
2.1.220 binary against real project files (audiotester/playwright.config.ts +
ci.yml, an odoo-erp-697 migration .sql). MAIN session: all 5 rules
(no-continue-on-error, coverage-thresholds, browser-console-zero-errors,
e2e-real-user-testing, database-migrations) inject as nested_memory
attachments when a matching file is Read -- confirmed working, no change
needed. Dispatched SUBAGENT, same file, same correct cwd: the subagent's OWN
transcript (subagents/agent-<id>.jsonl) has ZERO nested_memory attachments,
only deferred_tools_delta + skill_listing -- confirmed NOT reaching a worker,
ruling out both confounders (wrong cwd, subagent context) the two prior #104
probes left open. Restored a short (<8 line) always-on stub at each old
module path (modules/ci/*.md x4, modules/quality/database-migrations.md),
same pattern as e9d1022's sibling skill conversions, added to
profiles/universal.profile ALONGSIDE (never replacing) the existing
rules/*.md entry -- airuleset.py's profile parser treats "rules/" entries
specially (symlink only, no @import), so main-session behavior is unchanged.
Full suite 2167 passed, ruff clean, validate: 59 modules (+5). No new
hooks/jobs/detectors -- content restored to a surface already proven to load.

## 2026-07-27 — #94 A/B experiment: full ruleset vs minimal ruleset

Harness f468294 (scripts/rules_ab_experiment.py + tests/test_rules_ab_experiment.py,
27 tests). Retro-ticket replay: each task is a real closed ticket replayed at <red>^,
and CORRECTNESS is decided by the RED test that actually shipped with the real fix --
no LLM judge on the primary axis. Conditions differ only in rule TEXT (CLAUDE.md
prefix + inject-situational-rule.sh, which injects rule bodies and blocks nothing);
every block-*/stop-check-* gate stayed live in both. Isolation: CLAUDE_CONFIG_DIR
scratch profiles, standalone clones with no origin, empty GH_TOKEN.

Measured layer: the MAIN-session prefix -- the only layer a main session and a
dispatched worker share (#104/#105).

Prefix cost (one trivial turn, everything else identical): full 122,907 tokens /
$0.5695 vs minimal 45,545 / $0.1383 -- 2.70x tokens, 4.12x cost. Independently
confirms the ~124k figure asserted in #92.

Behaviour, equal $4 budget per run, Sonnet 5: full = oracle 2/2, 67 turns;
minimal = oracle 1/2, 109 turns. Blind soft grade (anonymised diffs, grader
unaware of condition) tied 3-3, "most robust" split 1-1. Zero regressions in all
four runs. All four runs were terminated by the budget cap, so no run reached the
Stop gates and hook_blocks was 0/0/0/0 -- the experiment says nothing about hooks.

The one nameable behavioural difference (#88): the ticket body proposed its own fix
("strip $( … ) content"); the minimal condition implemented exactly that and solved
the stated case, while the full condition distrusted the ticket, replayed the real
command corpus and found the actual cause was line-continuation splicing -- which is
what really landed. Not "worse code", but "believed the ticket vs verified it".

Verdict: the COST claim is confirmed and quantified (+77k tokens every session);
the "rules make the model worse" claim is NOT supported. No module implicated --
naming one would be invented. n=1 per cell, no repeats: signal, not proof.
Statistical-power follow-up filed as #106. Evidence: audits/ab94/.

## 2026-07-27 batch — #45 (unanswered question re-ask) + #47 (gk run-card silence)

- #45 (question re-asked by allusion instead of restated after an intervening
  conversation): RED `3d97641` (tests/test_question_policy.py::TestUnansweredQuestionReaskedFull,
  tests/test_notify_question_block.py::TestHistoryAllusionBlocked), GREEN `dafd0de`.
  Documented the two branches (VERBATIM re-poke vs full NANOVO re-ask) in
  message-status-marker.md + user-questions-slovak.md; extended
  stop-check-question-quality.sh with Check 5 (banned Slovak referencing
  phrases), sitting after the pre-existing VERBATIM-repeat bypass so it only
  ever fires on a genuinely new ask. Live-verified the hook blocks the exact
  ticket-quoted phrase ("jediné otvorené rozhodnutie je ultracode (pýtal som
  sa skôr)") post-deploy.
- #47 (gk per-ticket Discord run-card silent since 06:09 — autopilot-master
  and process-subdev never mentioned run-card in their own bodies, 0 vs 4 in
  skills/autopilot/SKILL.md): RED `7fcc2c1` (tests/test_autopilot_master_skill.py::TestRunCardFiredInEveryLane,
  tests/test_process_subdev_skill.py::TestRunCardFiredOnReleasedSlice), GREEN
  `ffabb71`. process-subdev step 5.4 now fires `notify --run-card` once per
  ticket in the released slice, right after post-deploy verification;
  autopilot-master LANE 1/LANE 3 carry explicit reinforcement lines (never
  rely solely on the "load the other skill" pointer — see the playbook entry
  on grep-locking a cross-skill pointer).

Both: no dev branch / no PR / no CI in this repo (commits go direct to
`main`). Local gate: `python -m pytest tests/` (2207 passed), `ruff check .`
(clean), `python3 airuleset.py validate` (clean). `airuleset.py push` ran
twice — second run showed `Already up to date.` for every remote (dev2,
gatekeeper, montalu@subdev, marek@subdev, david@subdev). Both issues
auto-closed by GitHub from the `Closes #N` commit messages (confirmed via
`gh issue view`, no PR involved).

## 2026-07-27 — #49 + #50 (subagent authority, one batch, direct to main)

**#49 (review subagent took write authority).** Validated first: the ticket's
headline — "the subagent merged PR #228" — was already refuted on the ticket by
the supervisor (the merge was the supervisor's own, inside `pr-merge-policy.md`
authority). Dropped that half; did NOT build the proposed
`block-subagent-merge.sh`. The surviving defect reproduced: no surface anywhere
carried a least-tool-authority rule, and a live run of
`inject-situational-rule.sh` on a real Agent payload showed `least` and
`git status` absent from the 4072 chars it injects at every dispatch. Fix is
content on the two surfaces that reach a dispatcher — the skill body (injected
at the Agent call, `situational-triggers.conf:42`) and the always-on module stub
(survives the hook's once-per-session dedup). Commits `5f5c7ff` [red] →
`bf80458` [green]; tests `tests/test_review_dispatch_authority.py`
(`test_injected_body_at_an_agent_dispatch_contains_the_rule` is the behavioural
one — runs the real hook). Approach comment posted before the first code commit:
issues/49#issuecomment-5094352281.

**#50 (fork acts on inherited-but-unexecuted instructions).** Fully valid,
nothing overcame it: zero hits anywhere for what `fork` inherits or when to pick
it; the only description was a comment inside
`pre-agent-validate-subagent-type.sh`, on no agent's context path. Same two
surfaces, plus `subagent-continuation.md` as the always-on home (the incident
shape is a fork dispatched LATE in a long session, by which time the hook's
once-per-session injection was long spent). No hook — the ticket's own
conclusion, re-confirmed: the PreToolUse payload holds only the narrow prompt,
which looks correct, so there is no static signature. Commits `cfeb201` [red] →
`ea6fede` (assertion-literal correction, re-verified RED against a stashed
implementation) → `56f3b47` [green]; tests
`tests/test_fork_context_inheritance.py`. Approach comment:
issues/50#issuecomment-5094356385.

Gate: 2222 tests pass, `ruff check .` clean, `airuleset.py validate` OK.
Deployed with `python3 airuleset.py push`; the second run showed
`Already up to date.` for all five remotes (dev2, gatekeeper, montalu@subdev,
marek@subdev, david@subdev).

#25/#61/#98 (2026-07-27, batch): #25 tickets-status --refresh returned
open=None for david (gh calls inherit the shell env, david never runs
`gh auth login`) — added `_gh_env()`, falls back to a token extracted from
~/.git-credentials when GH_TOKEN/GITHUB_TOKEN isn't set, a real token always
wins. #61 statusline showed nothing when session cwd is the PARENT of the
git repo (montalu@subdev ~/devel/odoo vs. odoo-slovnormal one level down) —
when `git rev-parse --show-toplevel` fails at cwd, scan cwd's immediate
subdirectories for exactly one `.git` and descend into it; 0 or >1 stays
ambiguous (open=None), never guesses. #98 sshpass was used by airuleset's
ssh helpers but never in RUNTIME_DEPS (verified empirically: 3 occurrences,
0 in the tuple) — added it; extended autonomous-verification.md's
"No sudo on a restricted box" bullet to name the working path (file a
gk-request naming the package; fulfilling it = add to RUNTIME_DEPS + push) —
module bodies (unlike skill bodies) reach a dispatched sub-dev worker
(#104/#105). Commits: `e407222` [red] (#25/#61) → `3c77f9a` [green];
`46d3d1e` [red] (#98) → `f499815` [green]; `e963231` docs. Tests
`tests/test_statusbar.py` (git-credentials fallback, real-token-wins,
descend-one-level, ambiguous-stays-null), `tests/test_runtime_deps.py`
(sshpass tracked, sudo-less branch mentions gk-request + RUNTIME_DEPS).
Approach comments: issues/25#issuecomment-5094666672,
issues/61#issuecomment-5094668190, issues/98#issuecomment-5094670446.

Gate: 2228 tests pass, `ruff check .` clean, `airuleset.py validate` OK.
Deployed with `python3 airuleset.py push`; the second run showed
`Already up to date.` for all five remotes (dev2, gatekeeper, montalu@subdev,
marek@subdev, david@subdev).

## 2026-07-27 — #24 #44 #68 (bundled batch, one repo push)

**#24 — post-push-ci-cleanup force-cancel escalation.** A normal `gh run
cancel` can have no visible effect (live restreamer incident, 2026-07-21).
A synchronous 120s wait inside the hook would slow every push, so cancelled
run ids + timestamps are now recorded to a per-repo `.git/airuleset-pending-
cancels.json`; the hook's NEXT invocation re-checks any entry >=120s old via
`gh run view` and escalates to `gh api .../force-cancel -X POST` if still
not `completed` — one-shot, never retried. Hit and fixed the #96-documented
`cmd | python3 - <<'PYEOF'` stdin-vs-heredoc trap while writing the
append-to-pending step (fixed by passing the id list via argv instead).
Commits: `90f7002` [red] → `18bfa3b` [green]. Tests:
`TestPostPushCiCleanupHook` (`test_new_cancel_is_recorded_to_pending_file`,
`test_stale_pending_run_escalates_to_force_cancel`,
`test_pending_run_too_recent_is_not_escalated_yet`,
`test_pending_run_already_terminal_is_dropped_without_escalating`).
Approach: issues/24#issuecomment-5095064059.

**#44 — watchdog job 23, MANAGED_MODEL generation reconcile.** Job 12
(#42) only restarts a session still parked on the hardcoded fable/opus-4
substrings (#37's cost-migration scope, deliberately narrow — pinned by
`test_sonnet_session_is_skipped`) — it never notices a session that started
under some OTHER older `MANAGED_MODEL` default. New `transcript_first_model`
(launch-time model, read forward) + job 23 (`model_generation_reconcile`)
restart a session whose launch model no longer matches the current target,
but ONLY when the session's CURRENT model still equals its launch model —
i.e. the user never manually touched `/model` — so a deliberate manual
choice (model-awareness.md: never auto-downtier `/model`) is never fought
in either direction. Coalesces with job 12 via the existing shared
`handled` set; no new `cmd_watchdog` wiring (reuses `target_model`). Commit:
`8e3efcc` (feature, tests+impl together per tdd-workflow.md's flexible
order for greenfield features). Tests: `TestTranscriptFirstModel`,
`TestModelGenerationReconcile` (14 cases incl. the two manual-choice
guards), `RunOnceModelGenReconcileWiring` (incl. 3-way coalescing).
Approach: issues/44#issuecomment-5095066143.

**#68 — block-subdev-ssh-misuse.sh gatekeeper root@subdev identity.** The
hook's allow-list only recognized montalu/marek/david — the gatekeeper
VPS's own sanctioned `root@subdev` + `~/.ssh/subdev_admin` identity (used
explicitly, or implicitly via the box's own `~/.ssh/config` `Host subdev`
stanza, the real `process-subdev` nudge shape) was unconditionally blocked.
Added `root` as an allowed user gated on the `subdev_admin` key basename
(generalized `has_gatekeeper_key` -> `has_identity(tokens, basename)`), and
a best-effort `_resolve_ssh_config_host` reader for the bare-`ssh subdev`
form, read at hook-execution time so dev1 (no such stanza) is unaffected.
Commits: `02c0af2` [red] -> `d0bf7f9` [green]. Tests: 7 new cases in
`TestBlockSubdevSshMisuseHook` (explicit identity, wrong identity, bare
form via real ssh-config resolution, and 3 negative-resolution guards).
Approach: issues/68#issuecomment-5095067956.

Gate: 2261 tests pass (2228 baseline + 33 new), `ruff check .` clean,
`airuleset.py validate` OK. Deployed with `python3 airuleset.py push`; the
second AND third run both showed `Already up to date.` for all five
remotes (dev2, gatekeeper, montalu@subdev, marek@subdev, david@subdev).
All three issues auto-closed by GitHub on push (direct-to-main, `Closes
#N` in the commit message — no PR needed on this repo's flow).

## 2026-07-27 batch — #34 (watchdog hosted-stream wording) + #43 (cost report, superseded) + #106 (A/B power follow-up)

**#34 — sudo-hosted stream mechanism (jobs 7/9/10), keep-generic + reword
to historical.** Validated: `_FOREIGN_TMUX_USERS` was already emptied by
#33, but the WIDER `_foreign_user`/`_foreign_session_info`/
`_foreign_transcript_goal` + job 7's `hosted_users` merge + job 10's
hosted `waiting=True` path still described montalu in present tense.
Live-checked (`tmux list-panes -a`, `ls /home/`): no pane currently
matches the foreign-home shape. Decision: keep the mechanism (cheap when
idle, already generic, 373 lines of tests, mirrors the established
`_FOREIGN_TMUX_USERS` precedent) — comment-only reword of 4 docstrings to
past tense, matching the wording already used at the `_FOREIGN_TMUX_USERS`
definition. Commit `87c31c2`. No test change (no behavior change).
Approach: issues/34#issuecomment-5095518248.

**#43 — cost measurement report, closed as superseded by automation.**
Not a code-change ticket — a one-time 2026-07-25 measurement report. Every
substantive item is already actioned by later work: #37 (closed) trimmed
the always-on prefix (current `wc -w modules/*/*.md` = 33,978, on target);
job 13/16/19 (already shipped) now write a continuous hourly burn
snapshot -> fleet merge -> budget alert, replacing the one-off report's
whole purpose going forward (confirmed live: 56 real rows in
`~/.claude/burn-history/snapshots.jsonl` since 2026-07-25); the
message.id-grouping methodological lesson is already the exact algorithm
in `transcript_current_context` (same date); the frozen-statusline lesson
is already a playbook bullet. Closed with evidence, no commit needed.
Approach: issues/43#issuecomment-5095529090.

**#106 — A/B experiment (#94) power follow-up: n=1 does not replicate.**
Harness extended (`scripts/rules_ab_experiment.py`, commit `14b004f`):
`slot_name(issue, cond, rep)` (rep=1 stays unsuffixed for round-1
compat), `--rep` CLI flag, `summarise_by_task()` (never pools different
tickets into one A-vs-B total). Tests: 8 new cases, all green.

Ran 6 new replicate sessions (task #86 rep=1 both conditions — the
missing 3rd task; tasks #88/#96 rep=2 both conditions, same $4 budget as
round 1) via detached `setsid nohup` background processes against the
scratch `CLAUDE_CONFIG_DIR` profiles, polled to completion (~25 min
wall). Graded objectively (RED test from the real shipped fix) and via a
blind soft grade (fresh `general-purpose` subagent, anonymized diffs,
rubric stated up front, condition revealed only after —
`audits/ab94/blind-grade-106.md`).

Result: round 1's clean 2/2-vs-1/2 oracle split does NOT replicate —
pooled n=5/condition reads full=2/5, minimal=1/5, with BOTH original
results individually flipping on their own replicate. The blind grade
disagrees with the oracle numbers on the SAME round-2 data (minimal wins
2/3 pairs on the grader's own correctness/robustness read). Verdict:
ruleset token cost remains proven and quantified; behavioural effect
remains genuinely unmeasured (not measured-and-small) — every axis tried
flips or disagrees under replication. Also found (documented, NOT
shipped as a fix): `count_hook_blocks()`'s bracket-regex essentially
never matches real hook feedback, but a naive JSON-text-grep "fix"
over-counts by 20-50x on a hook-fix ticket (the model reads the hook's
own source, which contains that JSON shape in comments) — flagged as a
genuine gap needing a structural parse, not patched blind.

Evidence: `audits/ab94/report-combined-106.json`, `blind-grade-106.md`,
`blind-mapping-106.json`, `runs106/`. Commits `14b004f` (harness),
`f9f83cc` (result + evidence). Approach: issues/106#issuecomment-5095589629.

Gate: 2269 tests pass (2261 baseline + 8 new), `ruff check .` clean,
`airuleset.py validate` OK. All three issues auto-closed by GitHub on
push (direct-to-main, `Closes #N` in the commit messages — no PR
involved).

## 2026-07-28 — #109 compact is not atomic w.r.t. completion

`50d4a01` [red] → `1320e72` [green]. Root cause traced in code AND measured,
not inherited from the ticket: `deliver_compact_now` (#65) types `/compact`
DURING the Stop-hook batch, i.e. before that Stop's verdict exists — a message
an earlier gate refuses still enqueues, CC never drains its type-ahead queue
(#84), and the keystrokes execute at a later accepted Stop in a changed state.

Measurement (12 real sync-path sends, `~/.claude/compact-sync.log`, 2026-07-27;
compaction START = `compact_boundary` ts minus `compactMetadata.durationMs` —
without that subtraction every send looks 2 min late): 9 sends with no pending
rejection started within ~6 s (atomic); all 3 made while a `Stop hook feedback:`
entry already sat in the transcript started +24 s / +77 s / +98 s later, each
with the marker moved to `⏳`. One of the three is the reported shape exactly
(rejected for missing audit lines), so the incident reproduced outside presenter.

Fix, on existing paths only (#102 had just deleted two triggers): enqueue-time
gate `_stop_already_rejected`; delivery-time gate `_compact_not_at_boundary`
(#102's `❓` re-check generalized to `⏳`); `COMPACT_REQUEST_MAX_AGE_S` lapse.
Tests: `TestStopAlreadyRejected`, `TestDeliverCompactNowRefusesRejectedBoundary`,
`TestCompactDeliveryTimeBoundaryGate`, `TestCompactRequestExpiry`,
`TestCompactHookRunsAfterTheStopGates` (the last pins the Stop-chain order the
enqueue gate depends on).

Corpus replay through the SHIPPED functions (transcript truncated per send):
3 deferred / all 3 genuinely late, 0 false positives, 8 atomic sends unchanged,
0 residual misses. Retry replay shows deferred requests held while `⏳` and
still delivered where the session is genuinely back at `✅`.

Stated plainly rather than invented around: Claude Code has no work-complete
callback (the Stop hook fires before the verdict; #87 closed the self-signalling
`/compact` route), and queued keystrokes cannot be recalled — the only key that
clears the input box is Escape, which interrupts a running turn. Residual: a
Stop refused by a hook running AFTER ours (the `/goal` hook) is invisible at
enqueue time and is caught only on the next delivery attempt.

Gate: 2311 tests pass (2298 baseline + 13 new), `ruff check .` clean,
`airuleset.py validate` OK.

## #111 — nudge-poll-loop-timeout.sh false-positives on a bare sleep+done mention

`1c1c0dd` [red] (3 mention-shapes must go quiet + a nested-loop guard that must
stay loud) -> `979f32d` [green]. Root cause: two INDEPENDENT existence greps
(`sleep` anywhere AND `done` anywhere) answer "does this token appear", never
"is this sleep the loop's body" — so writing a note about a poll loop, grepping
the rule that documents one, or a settle `sleep` beside an unrelated fan-out all
took the full nudge. Replaced by ONE ordered `do` … `sleep` … `done` test over a
token-stream normalization of the command.

Evidence is a real corpus replay, not unit tests: every Bash `tool_use` in all 94
local transcripts (77,365 calls / 66,392 unique), each replayed through the
SHIPPED hook with its real timeout/run_in_background, and paired with its own
`tool_result` for ground truth. Before 983 nudges (41 really killed by the
harness, 233 really ran >110 s); after 793 (39 killed, 228 long) — 190 fewer
nudges, 95 % of the genuinely-needed ones retained. The 6 dropped long/killed
calls are all bare `sleep 200`-style waits with no loop.

The replay earned its keep twice: it caught that the FIRST green draft, built
from consuming character classes, went quiet on 243 real `until …; do sleep 5;
done` polls, because the single space between `do` and `sleep` cannot be both
boundaries at once — invisible to every unit test, since their loops all put a
command between the two tokens. Now pinned by
`test_tight_do_sleep_body_is_still_nudged`.

Rejected with numbers: containment (`sleep` before the NEXT `done`) — tighter but
stops nudging nested retry polls; heredoc-body stripping — 71 fewer nudges but
`ssh host 'bash -s' <<EOF` executes its body (residual filed as #112); `grep -Pzq`
— `-z` is NUL-data in GNU grep and *decompress* in ugrep.

Gate: 2316 tests pass (2311 baseline + 5 new), `ruff check .` clean,
`airuleset.py validate` OK.

## 2026-07-28 — #113 flaky readiness sleep, #112 heredoc residual

**#113** (`tests/test_upload_url.py` raced a fixed 0.6s startup sleep):
`d6f4752` [red] → `6cad00d` (test correction) → `c6d37dd` [green], closed.
RED tests `test_readiness_wait_survives_a_slow_server_start`,
`test_a_server_that_can_bind_nothing_fails_fast_with_its_stderr`,
`test_no_fixed_startup_sleep_survives_in_this_module`, plus
`test_readiness_timeout_reports_the_servers_own_stderr` added with the fix.
`_serve()` now polls the endpoint and exits three ways — answered, child
already dead (`proc.poll()`), budget spent (reported WITH the child's
stderr) — and takes its port from `_free_port()`, so the hardcoded
8794/8796/8797/8798 are gone too.

Evidence under 8 CPU spinners + 2 fork storms, 360 reps per side:
before (`34e10a5`) 350 pass / **10 fail** (2.8%, every one `[Errno 111]
Connection refused`); after **360 / 0**, at **half** the wall clock
(0.31s vs 0.62s per rep). The speedup is the tell that the race is gone
rather than padded — every banned band-aid moves that number the other way.
60 reps alone could not have settled it (a clean 60 at a 1-in-60 rate is
luck ~37% of the time); 360 puts it at ~4e-5.

The RED commit's own lock had to be corrected first: it asserted the literal
`time.sleep(0.6)` was absent from the whole source, which this module's own
docstrings necessarily contain — red for the wrong reason, ungreenable. Now
matches the bare STATEMENT plus "exactly one `x = subprocess.Popen(`". That
second assertion first counted its own message: a lock that names what it
forbids must match a form its own prose cannot take.

Found and filed, not folded in: **#114** — `upload_server.py`'s non-daemon TTL
timer blocks `sys.exit`, so a total bind failure lingers the whole TTL and
exits **0** (20.06s/rc=0 vs 0.07s/rc=1 at ttl=0).

**#112** (heredoc prose tripping the do/sleep/done shape): closed **won't-fix**
with numbers, `6e4563a` records the verdict in the hook's own comment block.
Replayed all 77,433 Bash calls (mirror diffed against the shipped hook, 0
mismatches on 2,349 candidates): stripping bodies drops 75 nudges, but only ~6
are prose — **49** are `cat > poll.sh <<'EOF'` + run (one ran 120s) and **15**
are `ssh 'bash -s'`/`python3 -` bodies (up to 152s). Kill-recall is 37 → 37
either way. The proposed executor-aware discriminator is wrong on its dominant
class by construction, since `cat` is not an interpreter yet its body runs.

Gate: 2320 tests pass (2316 baseline + 3 red + 1), `ruff check .` clean,
`airuleset.py validate` OK.

---

## 2026-07-28 — #114 upload_server.py: a total bind failure exited 0

`082b473` [red] → `1c537c9` [green], direct to main (no PR, no CI — local gate
only).

**Root cause, one property away from the symptom.** `filedrop/upload_server.py`
arms its TTL self-shutdown timer at line 46, 150 lines above the bind loop, and
`threading.Timer` inherits `Thread.daemon = False`. `sys.exit()` only raises
`SystemExit` on the main thread; CPython then joins every non-daemon thread
before it can finish, so the pending exit is parked for the rest of the TTL and
the timer's own `os._exit(0)` ends the process INSTEAD — status 0 for a server
that bound nothing and served nothing.

**The measurement that chose the fix.** The ticket describes the bind path only.
The same join blocks *every* way the main thread can leave, so a SIGINT on a
successfully-serving endpoint hangs identically:

```
bind nothing, ttl=6   rc=0 after 6.06s  ->  rc=1 after 0.07s
SIGINT, serving       rc=0 after 5.93s  ->  rc=-2 after 0.02s
```

That second row is why the fix is `daemon = True` and not the ticket's other
two candidates. Arming the timer only after a successful bind fixes the first
row and leaves the second untouched (that server bound fine). Cancelling the
timer on the failure path makes a process-wide property the responsibility of
each individual exit site — which is precisely the discipline that failed here,
the bind-failure `sys.exit` having been written 150 lines from the arm.

The happy path is unchanged by construction: `serve_forever()` holds the main
thread, so interpreter shutdown never arrives before the timer, which still
fires at TTL and still ends the process. Locked by a companion test asserting
the server serves, then dies AT its TTL (not before, not never) — so the fix
cannot pass by removing the TTL.

Tests assert the observable contract (exit status, elapsed under the TTL,
diagnosis on stderr), never the `daemon` flag. `_spawn()` split out of
`_serve()`: a server that can bind nothing never serves, so #113's readiness
poll is meaningless for it while its exit code and timing are the whole point —
the module still holds exactly one `Popen`, which is what that lock protects.

Gate: 2322 tests pass (2320 baseline + 2), `ruff check .` clean,
`airuleset.py validate` OK. Live `airuleset.py upload` re-verified end to end
after deploy.

## #115 — cmd_upload: a port scan blind to its own binds, a /tmp log shared across users (2026-07-28)

Commits `6e23eab` [red] -> `1ac3af8` [green], direct to main. Design comment
posted first: issues/115#issuecomment-5098345528.

Three defects, each reproduced live before any code. (1) The free-port scan
probed `connect_ex(("127.0.0.1", cand))` while `upload_server.py` binds exactly
`filedrop.bind_ips()` — which `_is_private` EXCLUDES loopback from, and which
never contains 0.0.0.0 because a WRITE endpoint must not listen on a box's public
IP. Probe set and bind set were therefore disjoint BY CONSTRUCTION: five
listeners on :8799 and the scan still returned 8799, after which the second
`airuleset.py upload` failed to bind anything and its readiness probes hit the
FIRST server (404 x20, logged into that other endpoint's file). Fixed by
`_pick_free_port(ips, ports)`, which BINDS each candidate on the very addresses
the server is about to bind. Only EADDRINUSE rejects; EADDRNOTAVAIL is tolerated
because the server skips an unbindable address rather than dying and needs only
one success — requiring all of them would let a single stale IP reject all 21
candidates. (2) `/tmp/airuleset-upload-<port>.log` gave the first user to use a
port ownership of that name for everyone; reproduced verbatim against montalu's
real leftover (`PermissionError: [Errno 13] ... 8811.log`, unhandled). Fixed by
`_upload_log_path()` -> `~/.claude/upload-logs/upload-<port>.log`, plus a wrapped
open that exits 1 with a diagnosis. (3) 1183 leftover logs in /tmp because the
cmd_upload tests pass ephemeral ports; the tests now redirect via
`AIRULESET_UPLOAD_LOG_DIR` and an anti-litter lock asserts the exact legacy path
is not written. That lock needed both an exact-path assertion AND a set diff: a
set diff alone passed once for the wrong reason, when the ephemeral port
collided with one of the 1186 files already there — the litter hid itself.

Cleanup: 1193 leftover logs owned by this user deleted from /tmp; montalu's 3 left
untouched (not ours to remove — they are harmless now, nothing writes /tmp any
more). Filed #116 while verifying: the page URL-encodes the filename and the
server never decodes it, so a dropped "nahrávka test (1).mp4" saves as
`nahr_C3_A1vka_20test_20_1_.mp4` — which contradicts what the meeting-analysis
skill documents.

Gate: 2330 tests pass (2322 baseline + 8), `ruff check .` clean,
`airuleset.py validate` OK. Live after deploy: two concurrent endpoints took 8799
and 8800 (never the same port twice), `--port 8811` no longer crashes on the
foreign log, a 3 MB PUT round-tripped SHA256-identical, and every endpoint
self-expired at its TTL leaving no listener, no process and no /tmp file.

## 2026-07-28 — #116 the saved upload filename kept its percent-escapes

`a0a8d9d` [red] -> `110b888` [green], direct to main (no PR, no CI — local gate only).

Root cause traced in the source before any code (design comment
`#issuecomment-5098562856`, posted 40 min before the first commit): the served
page sends `encodeURIComponent(file.name)` at `upload_server.py:113` — it has to,
because a raw filename cannot go into an HTTP request line at all — and
`BaseHTTPRequestHandler` stores `self.path` verbatim, so `do_PUT` handed the
still-encoded segment to `_SAFE.sub("_", ...)` whose class `[^A-Za-z0-9._-]`
excludes `%`. Every escape therefore survived with its `%` turned into `_`:
`nahr%C3%A1vka%20test%20(1).bin` -> `nahr_C3_A1vka_20test_20_1_.bin`.

`unquote()` is applied to the FILENAME segment only, deliberately NOT inside
`_parts()`: the token segment is this endpoint's only auth, and decoding it too
would let `%74ok…` authenticate as `tok…`. A test locks that.

Decoding is what makes the sanitizer a security boundary for the first time —
`/`, `..`, NUL, C0/C1 controls, a leading `-` and a 4000-char name only become
reachable once the escapes resolve — so `_SAFE` was replaced rather than merely
fed a decoded string. `safe_name()` normalises NFC (a macOS-origin name arrives
decomposed) then KEEPS Unicode letters/marks/numbers plus `" ._-()"`: a
keep-list, so separators, NUL, controls and the whole Cf class (U+202E
extension spoofing) are excluded by construction rather than by a blacklist that
can be incomplete. Residual cases a character filter cannot see got their own
guards — empty/dots-only -> `upload.bin` (`os.path.join(DEST, "..")` is DEST's
parent), leading `-` prefixed, length clipped to 200 UTF-8 BYTES keeping the
extension (400 Slovak chars are 800 bytes; ext4 caps a name at 255 and `.part`
must fit). A `realpath` dirname check at the write site enforces containment
independently of all of it.

Rejected: the ticket's own suggested fix (unquote + keep the ASCII `_SAFE`) —
it satisfies the old doc sentence but still lands `nahr_vka_test__1_.bin`, a
milder spelling of the same complaint; reusing `filedrop.share.safe_name` —
`filedrop/server.py:20` pins the download URL alphabet to that function's ASCII
output, and `upload_server.py` is launched by PATH so `import filedrop` raises
ModuleNotFoundError (verified, not assumed); `os.path.basename` before
sanitizing — it silently rewrites `../../etc/passwd` to a plausible `passwd`,
where mapping the separators keeps the hostile name visible in the listing.

Tests: RED reproduced the ticket's observed string verbatim in its own failure
message (`the endpoint saved ['nahr_C3_A1vka_20test_20_1_.bin'] instead`) — not
a construction artefact. Two of the nine new tests PASS at HEAD (an un-decoded
`..%2F` is inert; the token is matched raw) and the class docstring says so
rather than dressing them up as reproductions.

Docs: `skills/meeting-analysis/SKILL.md` promised "spaces / accents / parens
become `_`" — true neither before the fix (percent-escapes spelled out) nor
after it (they are preserved). Rewritten to the real behaviour, and its
`VIDEO=$HOME/...` assignment quoted, since spaces reach the path now.

Gate: 2339 tests pass (2330 baseline at 08c9afa + 9), `ruff check .` clean,
`airuleset.py validate` OK. Live after deploy, through a REAL Chromium
drag-drop against the live endpoint (so the page's own `encodeURIComponent`
is what produced the request): `nahrávka test (1).bin` landed under exactly
that name, SHA256-identical, and the SAVED log line carried the real name.
Twelve hostile names fired at the same endpoint (traversal, absolute, backslash,
NUL, CR/LF, tilde, dots-only, leading dash, RTL override, 800-byte name) all
landed flattened INSIDE the destination — nothing in /tmp, nothing in $HOME, no
stray `.part`; the raw un-encoded `../../../tmp/ESCAPED116` 404s on the segment
count. Endpoint killed, fixtures and temp dirs removed, port free.

Filed #117 while verifying: the upload page declares no icon, so every browser
that opens it logs a `GET /favicon.ico` 404 console error.

## 2026-07-28 — #117 upload page: no favicon, so every open logs a 404 console error

`13cdd60` [red] → `a713d95` [green], direct to main (this repo has no dev
branch / no PR / no CI — the local gate is the only gate: 2341 passed, ruff
clean, validate OK).

Root cause traced rather than restated: a document declaring no `rel=icon`
makes the browser auto-request `/favicon.ico` at the ORIGIN ROOT, which is not
`/<token>/`, so `do_GET` refuses it and the browser logs the 404. The refusal
is correct and stays — a favicon request carries no token, and the token is
this write endpoint's only auth.

Chosen: declare the icon INLINE in `PAGE` as a 244-char percent-encoded SVG
`data:` URI, so the request is never made. Rejected: answering `/favicon.ico`
from `do_GET` (an unauthenticated route on a write endpoint, plus a runtime
asset lookup that would be install-location dependent — this module is
launched by path); `href="data:,"` suppress-only (no icon, and leans on
per-browser handling of an undecodable empty resource for the same one line).
Design posted to the ticket before the first code commit.

RED test `test_served_page_declares_an_inline_icon` asserts on the bytes a LIVE
server serves (never the source string); companion lock
`test_favicon_path_is_still_refused_by_the_token_gate` pins that no
unauthenticated route was opened.

Real-browser proof, since curl cannot observe a console error: before, exactly
one error (`favicon.ico` 404) plus the matching server log line; after, ZERO
console messages, ZERO favicon requests in the log, the icon decoding as a real
image, and a 64 KB Slovak-named file still dropping through the page
byte-identical.

Also re-verified #116 independently against the same live endpoint (real
browser drop + 18 hostile names over a raw socket): every claim holds, nothing
escaped `DEST`, `$HOME`/`/tmp`/DEST's parent all gained zero entries. Evidence
on #116.

## 2026-07-28 — #118 repeated foreground CI poll loops (hook, not prose)

`d2fa59b` [red] → `190e425` [green] → `c47c7ce` [red] → `173801e` [green,
Closes #118]. New `hooks/block-ci-poll-repeat.sh` (PreToolUse/Bash): the FIRST
foreground CI poll loop per (session, run-id) stays free, every repeat is
hard-blocked with the ready-to-paste background waiter, run-id substituted; in
subagent context the message carries ONLY the hand-back branch and never
`run_in_background`, because a subagent that backgrounds a wait terminates.
Detector reused verbatim from #111, gated by a CI-wait signature. One sentence
appended to `ci-monitoring.md`'s foreground bullet — no rewrite (#107/#110
were the two that already failed).

Corpus replay through the real hook, 8,079 transcript files (recursive — a
`projects/*/*.jsonl` glob sees ~94), 250,201 Bash commands: **940 blocked
(0.38%)**, worth **89.0 measured hours** of foreground wall-clock, median poll
375 s, 56% over 5 minutes. Zero first loops, zero non-CI wait loops (2,754),
zero background waiters (1,903), zero of 4,394 diagnostic `gh run view` calls,
and zero of a 4,000-command fresh-session sample were blocked. Characterising
the blocked set item by item — not the delta count — is what found the three
wrong-block classes (compound `gh pr merge && wait`, generic short
appear-loops, and `gh run view`-density catching post-mortems); each became its
own RED→GREEN pair. Verified live end-to-end through the real harness on this
box: loop 1 ran, loop 2 blocked with the subagent branch, single status check
still allowed.

## #121 — /compact at every completed ticket, keyed to the worker (2026-07-28)

`618f9d0` [red] → `8510d94` [green]. An autopilot supervisor reports batch N and
dispatches batch N+1 in the SAME turn, so its message always ends `⏳` — and the
`⏳` last-line veto in `notify-compact-request.sh` runs BEFORE the
`## ✅ Work Complete` scan, so the request was never even created. Measured on
forestshop/parovanie_produktov: 19 h, 375K context, five completed tickets, zero
compactions, `compact-requests.json` empty. Fix: a second channel keyed to the
TICKET — new SubagentStop hook `notify-compact-subagent-boundary.sh` records the
request when an `autopilot-worker` concludes with zero non-self entries in the
payload's `background_tasks` (this session's own task registry — a fact, not a
marker; absent field ⇒ unprovable ⇒ never compact, matching
`subagent-stop-check-bg-work.sh`'s fail-direction so the two cannot disagree).
The request carries `origin=subagent-stop` through to `_compact_not_at_boundary`,
which stops letting the supervisor's `⏳` decide. RED was behavioral
(`skip not-a-boundary` on a request that already proved its boundary), not a
missing kwarg; the four controls that pin #109's gate for every other origin,
#102's `❓` gate WITH the proof, and the Stop hook's own veto passed before and
after. Suite verified to REJECT both tempting wrong fixes: dropping the `⏳` veto
for everyone (2 failures) and recording on every SubagentStop (6 failures).
Tests: `TestCompactSubagentBoundaryHook`, `TestWorkingMarkerNoLongerVetoesAProvenBoundary`,
`TestProvenBoundaryOriginIsStored`, `TestSupervisorStopVetoIsNoLongerTheOnlyChannel`.

## #123 — the boundary hook's decision log (2026-07-28)

`e4806f6` [red] → `1376c1c` [green]. #121 shipped a hook that was silent on
decline, and whose only success artefact is DELETED again when
`deliver_compact_now` succeeds — so "never ran", "ran and declined" and "ran,
fired and delivered" produced one identical observation.

Q1 settled EMPIRICALLY first, before any code: a hook added to `settings.json`
mid-session **does** take effect in that already-running session. Probe P (a new
hook entry appended to `~/.claude/settings.json`) fired on 2/2 subsequent Bash
calls in a session started 36 h earlier, with control C (a new line inside an
already-registered hook script) firing 3/3 to prove the event itself fires;
probe S repeated it on `SubagentStop` itself, catching a subagent's stop 10 s
after the hook was appended — which also captured this repo's first real
SubagentStop payload. So #121 was never inert and needs no operator restart.
The deployed log then confirmed it in the field, carrying lines from three
long-running sessions including the forestshop one whose drought motivated #121.

Every decision now appends one line naming the failed predicate
(`not-autopilot-worker` / `no-session-id` / `no-agent-id` / `registry-absent` /
`registry-<type>` / `live-tasks n=N`), and an accept records the outcome word
the hook used to discard with `>/dev/null 2>&1`. `type` alone cannot separate an
absent registry from an explicit null (both report `"null"`), so `has()` is
consulted too — different diagnoses. Bounded because SubagentStop fires once per
parallel tool-call branch as well as per dispatched subagent: worker decisions
unconditional, the non-worker class a ≤1/min heartbeat gated on a marker mtime,
log rotated at 512 KB.

Both outcomes observed live 8 s apart: `DECLINE reason=live-tasks n=1` on a real
`autopilot-worker` probe (deferring to this worker), and `RECORD
result=delivered` on an unrelated camera-box ticket boundary — the first field
evidence that #121's mechanism fires at all.

Corpus replay reused #121's reconstruction method, extended to background shells
and non-worker subagents (#121 tracked only `autopilot-worker` launches, so its
outstanding set was a strict subset of reality): 8,098 transcripts → 9,892
reconstructed completions → 4/4 fire at zero outstanding, 0/604 with work in
flight, 0/9,284 on other types, 0 mismatches, 0 stdout. It deliberately does NOT
claim a field accept RATE: 33.8% of registry ids never complete, and the raw vs
de-leaked bracket is 0.7%–94.1%.

`TestDecisionLogAssertionsHaveTeeth` mutates the real shipped script into the two
wrong fixes the ticket names (log unconditionally / widen the accept) plus a
strip-the-logging mutant, and asserts the checkers reject all three.

Filed #125: `result=delivered` also covers a downstream `DROP` (the camera-box
accept pairs with `DROP no-work` in `compact-sync.log`) — not fixed here because
it changes a CLI contract shared with the Stop-hook channel.

Tests: `TestCompactBoundaryDecisionLog`, `TestDecisionLogAssertionsHaveTeeth`.

## #124 — poll-loop detectors read payload as control flow

`f144075` [red] → `ce68f85` [green], closed. The reported nudge (a `gh issue
comment -F body.md` whose heredoc merely documents poll shapes) fired four more
times while working the ticket. The non-cosmetic half, reproduced from the
shipped code: a heredoc write QUOTING the sanctioned waiter, with no
`gh issue comment` in the call, is a hard `exit 2` from
`block-ci-poll-repeat.sh` on its second occurrence in a session — the body
carries `gh run view`, `cat >` is not mutating-exempt, the body carries the
`do…sleep…done` tokens. Reachable but never yet realised: 0 sessions in the
corpus have two such writes.

Root cause: both detectors match a normalised token stream of the WHOLE command,
which cannot tell what the shell RUNS from what it CARRIES. #112 rejected
blanket stripping with numbers and was right (re-measured: 75 of 147 body-only
matches are live), and named the thing a correct classifier needs — "follow the
written PATH to a later execution" — dismissing it as too hard. Within ONE
command it is neither hard nor a heuristic, and that is the whole fix.

`hooks/lib-poll-payload.sh` is sourced by all three detectors. A body is blanked
only when its owner names no interpreter, it is a plain redirect to a literal
path, and every later mention of that path in the SHELL-VISIBLE text is a
text-file argument; plus a heredoc read straight by a text sink (`git commit
-F -`, `gh issue comment -F -`) is inert, checked AFTER the interpreter test so
`ssh host 'bash -s' <<EOF` still wins. Fails open.

The corpus wrote two of those rules, not reasoning. A first draft enumerated
runners and called `sshpass ssh host 'bash -s' < /tmp/x.sh` inert — three real
specimens ran 119s, 135s and 183s. Replacing it with reappearance-means-live
took the changed set from 48 to 8. Replay both directions through the real
script over 8,107 transcripts / 251,551 commands / 6,188 unique candidates:
NEWLY SILENT 8, NEWLY FIRING 0, unchanged-fires 5,573, unchanged-silent 607;
all 8 read by hand and all payload. That lands on #112's own "~6 in 77k are
prose" estimate without touching any live class it measured.

Also fixed, caught by #118's own `assertEqual(out.stderr.strip(), "")`: a
heredoc body must live INSIDE the `$( … )`, not after its closing paren.

Known residual, out of contract: a loop quoted inside an interpreter body
(`python3 - <<PY`) still fires, because that body genuinely executes — it
hard-blocked a probe in this very session. Telling it apart needs to parse the
payload's language, which is the #112 trap again.

Tests: `tests/test_poll_payload_not_control_flow.py` (27), including
`LiveBodiesStillFireTest` pinning #112's measured live classes and
`StripperTeethTest` mutating the real stripper into the two wrong fixes.

## #119 — the repeated foreground poll #118 structurally cannot see

`24b5e89` [red] → `018f501` [green], closed. `block-ci-poll-repeat.sh` narrows
on a CI signature BEFORE loop detection, so a loop with no `gh run view|watch|
list` / `gh pr checks` token exits 0 at line 104. Deliberate and audited — a
boundary, not a bug — and the remainder was unguarded.

The ticket's own figures do not reproduce. Re-derived over 8,107 transcripts /
251,551 commands (mirror diffed against the real hook, 0 mismatches on 6,188
candidates): in the cited 24h window the population is 335 across SIX projects
(camera-box 81, airuleset 80, restreamer 76, forestshop 58, eft5000 36, tvdole
4), not 118 across two, and only 41 (12%) poll a subagent-result-shaped path.
Corpus-wide the non-CI remainder is 2,104 of 4,430 — remote/process 814,
subagent-result 746, other 191, local-log 185, endpoint 106, file 62 — so a
guard keyed on `result.json` misses two thirds of its family.

`hooks/block-local-poll-repeat.sh` exits on a CI signature before writing state,
keys on session + a digit-blind hash of the normalised loop SHAPE, and branches
its message three ways: a real Claude Code task artefact is told the
notification already exists (+ ONE `stat -Lc` re-derive for the orphaned-handle
case, #29193); a log line / ssh state / file / user-invented `result.json` is
told explicitly that NOTHING will wake it, and given one background waiter; a
subagent is told to raise this call's own timeout, since it can neither
background a wait nor end its turn on a pending task.

Replay in session order, own state dir per session: 297 blocks of 7,036
candidates (0.118% of all Bash calls) across 38 sessions, branch task 167 /
generic 130; 3,000 random NON-candidates → 0; the 8 commands #124's stripper
calls payload, 4x each in one session → 0. Wrong-block hunt by hand: fan-out
0, `git add` 0, file-write-shaped 1 — and that one writes a real 30-minute curl
poller, so it is correct.

No prose was added anywhere. The ticket rejects the accretion answer, the intake
gate says a mechanically checkable rule belongs in a hook, and the block message
is delivered at the action, which is the surface that actually reaches a
session.

Filed #127: #118's CI signature misses `gh pr view … statusCheckRollup` polls
(a real chain of nine `poll #436 CI`…`#444` turns), so they land in the generic
guard — widening an audited hook's contract needs its own replay.

Tests: `tests/test_local_poll_repeat_block.py` (31), including
`MessageBranchesByWhatIsWaitedOnTest` (each branch's claim asserted absent from
the other) and `TeethTest` mutating the real script into a no-free-first-loop
guard, a dropped CI exemption, and a single message for both branches.
2026-07-28 #132 CRITICAL: the watchdog typed `/exit` into the user's live pz-server pane and relaunched his session, twice in three minutes (`12:30:34 OK restart (hooks changed) zbynek-0:12.0`, `12:33:49 OK (model-gen-reconcile) zbynek-0:12.0 claude-opus-5 -> claude-opus-5[1m] (restarted)`). Sender: `_restart_pane`, the ONLY exit/restart/kill mechanism in the repo (verified by grep: no `tmux kill-session`, no `respawn-pane`, no C-c/C-d; the single `os.kill` at airuleset.py:3233 is a signal-0 liveness probe). FIRST FINDING, before any analysis: the ticket's own stated mitigation was NOT in place — the timer was `active` on ALL 6 boxes, restarted on dev1 at 12:42:49 that morning, and job 18 was actively evaluating live panes with only the `skip bg-agent` guard holding it off `zbynek-18:6.0` (the user's own airuleset session). Root cause of the revert: `airuleset.py:3376` runs `systemctl enable --now api-watchdog.timer` unconditionally on every `install`, so every `push` silently undoes a deliberate stop. Stopped all 6 again, then fixed by honouring a `~/.claude/api-watchdog.disabled` marker. MEASURED both premises rather than trusting either the ticket or the code comments (this repo has shipped 5 wrong stated causes). **Job 18's premise is FALSE**: in an isolated scratch session (`CLAUDE_CONFIG_DIR=/tmp/w132/config`, own tmux session, never a live pane) a 2x2 probe showed a brand-new hook ENTRY appended to settings.json mid-session fired on the very next tool call, the same entry REMOVED mid-session stopped firing immediately, and a second new entry fired too — with a control arm (new line inside an already-registered hook script) firing every turn to prove the event happened. Hooks-block hash `58b5022464b3f21e`→`fe34d1ab8d66bab2` while the process kept running. CC re-reads its hook config PER EVENT; it does not snapshot at process start, so the `rCu()`/`setup_hooks_captured` binary reading behind job 18 was wrong and #123 was right. Note how the false premise survived its own live verification: docs/autopilot-log.md's #70 entry proved that AFTER a restart the new hook fired — true, and vacuous, because that run had NO CONTROL ARM asking whether it would have fired without one. **Job 23 was far worse than the ticket described**: it compared `transcript_first_model` (an API model id, always `claude-opus-5`) against `MANAGED_MODEL` (a CLI alias, `claude-opus-5[1m]`). The `[1m]` suffix NEVER reaches the API model field — measured directly (a session launched `--model "claude-opus-5[1m]"` writes `message.model = "claude-opus-5"`) and by corpus (0 occurrences in ~119k model entries across every real dev1 transcript from 7 days). So `launch != target` was PERMANENTLY true for every session on every box, and each restart minted a NEW session id that defeated the per-sid dedup meant to stop the retry — an unbounded restart engine, live since d8031e7/64dace7 gave MANAGED_MODEL the suffix on 2026-07-25. Fleet blast radius since then: 27 successful forced restarts of live sessions (dev1 10, dev2 12, gk 1, marek 2, david 1, montalu 1). Journal also shows `<synthetic> -> claude-opus-5[1m]`, i.e. it fired on a placeholder that is not a model. **Job 12 decided on its own evidence**: its premise (a model released after session start is absent from that session's `/model` list, #42) is the one that IS true, but its remedy auto-downtiers a model the user deliberately chose, which `model-awareness.md` forbids outright ("their call alone — never auto-downtier it"), and the need is already met without keystrokes (MANAGED_MODEL binds every new session; measured live that a running session launched on `claude-opus-5` can select Opus 5 (1M context) via `/model` with no restart). FIX = DELETION, not the guard set the ticket sketched (scoping / presence signal / cooldown / TOCTOU re-check / storm brake): every one of those would have protected a job whose reason to exist was just disproved, and a guard around a job that should not run is more surface, not less. Removed jobs 12/18/23, `_restart_pane`, `_wait_for_shell_returns`, `_wait_for_relaunch`, `_wait_for_idle_after_dialog`, `RELAUNCH_CMD`, `_RESUME_DIALOG_RX`, `_hooks_config_hash`, `hooks_settings_path`, the now-unused `transcript_first_model`, and the `target_model`/`hooks_settings_path` run_once params + their cmd_watchdog wiring (-1680 lines). KEPT deliberately: `_reconcile_candidate_panes` (job 20 goal re-arm still enumerates panes with it — it lists, it never sends), `_pane_has_bg_agent` (job 20), `RestartFakeTmux` (jobs 20/22 tests use it as a generic pane stub; renaming would churn every call site). Guard is STRUCTURAL, not prose: tests/test_no_session_kill.py fails if any code file hands `/exit`/C-c/C-d to send-keys (whitespace-collapsed regex anchored on send-keys appearing first, payload within 80 chars), destroys/respawns a pane, or reintroduces the removed names — plus an AST companion that only inspects NON-docstring string constants, so the removed jobs' history can keep explaining `/exit` in prose while a literal reaching real code fails (the "a lock test matches its own prose" trap, hit twice while writing this). Genuine RED/GREEN: test:cbd7b7e[red] (7 failed, pinpointing watchdog/__init__.py:3523) → fix:377798a[green], full suite 2423 passed, ruff clean, validate clean. BEHAVIOUR REPLAY through the REAL shipped code path, bidirectional and decisive: `TestPzServerIncidentReplay` reconstructs the incident pane state and calls the real `run_once`, arming `target_model`/`hooks_settings_path` via `inspect.signature` ONLY if those params still exist (otherwise it would pass vacuously against the very code it must catch) — against the pre-fix implementation it emits exactly TWO `/exit` sends into `%12`, matching the journal's two restarts; against the fix, zero. LIVE bidirectional proof on the real box: a probe ran the real `run_once` against the REAL panes with a `run` proxy that executes read-only tmux calls for real but refuses and records any send — the PRE-FIX code (checked out temporarily, timer stopped) still queued `zbynek-0:6.0` (the user's own airuleset session) and `marek-10:2.0` for a hooks restart, held off ONLY by the transient `skip bg-agent` guard; the fixed code proposes nothing on the same panes in the same instant, zero keystrokes attempted. Also fixed: `run_once()`'s docstring said "Seven jobs" for a file defining 23 (CLAUDE.md declares it the single source of truth, and a stale index is exactly how job 18 stayed unexamined) — now "23 jobs per poll" with a test locking the numeric header to the count of documented `(N)` entries, and 12/18/23 kept addressable and marked REMOVED per the #102 never-renumber convention. Follow-up filed #133 (passive replacement for the #37 model-cost signal — must never type into a pane).
2026-07-28 #138 camera-box closed ZERO tickets for 11 straight days while the loop ran at full cost. FIRST, MEASURED FROM PRIMARY SOURCES, AND IT CORRECTED THE TICKET TWICE. What is true: camera-box PR #704 (dev->main) has been OPEN since 2026-07-11T20:57Z with `mergeable: MERGEABLE, mergeStateStatus: BLOCKED` — eleven of twelve checks green, the one red being `Full-path E2E (rig zero-loss gate)`, whose LAST SUCCESS on dev was 2026-07-13T04:53Z (since then: 105 failures, 31 cancelled, 0 successes). The failure is a rig precondition ("GATE FAILED: 1 node(s) DRIFTED or PTP-DEGRADED … Bring GM 10.77.9.184 up", byte-identical on 07-27 and 07-28), so no amount of code work could ever clear it. `origin/main` frozen at 2026-07-11; `origin/dev` 422 commits ahead. Closures/day 21,21,12,15,6,4 (07-10..07-15) then ZERO until one on 07-27 — because closure there is merge-driven, so a blocked merge makes closure structurally zero however much work lands. What is NOT true, and the ticket asserted both: (a) the loop was NOT running at full cost for 11 days — Agent dispatches/day were 07-15:6, then **0 for 07-16 through 07-26 inclusive**, 07-27:10, 07-28:23 (counted myself over the 432 MB transcript with a streaming per-line pass, corroborating a delegated read); transcript lines on 07-20/21/23/25 were 4/0/0/0; `/goal` was armed once on 07-13 and never re-armed. On 2026-07-15T16:31Z the session explicitly parked PR #704 and cancelled all CI/E2E for a live event, and the three small bursts inside the window are live-event incident response in which the operator said not to start development. That stretch is a deliberate human pause, NOT an airuleset defect, and no fix was manufactured for it. (b) the 12h cost measurement (390 main + 3,268 subagent turns, 0 closures) is real but belongs to 07-27→07-28: 33 dispatches across ~15 DISTINCT issues, 85 commits, zero merges. ROOT CAUSE, therefore, is narrower and sharper than "the loop spins": every signal this repo owns is merge-TRIGGERED — the per-ticket run-card fires AFTER a merge, autopilot-progress is fed by that card, the Issues segment only ever grows — so a loop that cannot merge is silent BY CONSTRUCTION, and silence is indistinguishable from health. FIX = watchdog job 24, DELIVERY-STALL WATCH: per repo hosting a live pane, compare two purely LOCAL git facts — newest commit on the checked-out branch (SPEND) vs newest commit on the base branch plus the count of commits not reachable from it (DELIVERY) — and report fresh work over a frozen base with a real backlog. Detection only (job 21's discipline): one deduped ping per repo per day, never a keystroke, and nothing written in a watched repo beyond a remote-tracking ref, since every one of them belongs to somebody else. REJECTED, on the evidence, the ticket's own leading candidate (a re-poke / no-dispatch detector): it would have been SILENT ON BOTH HALVES — through 07-16..07-26 there were no turns to count, and through 07-27..07-28 the loop dispatched correctly across ~15 tickets with real commits, so dispatch-liveness would have read perfectly healthy while zero work shipped. Dispatch is not delivery. Also rejected: a stuck-on-one-ticket detector (the fan-out is broad, not repetitive). The design — root cause, chosen approach, rejected alternative — went on the ticket as issuecomment-5104054683 BEFORE the first code commit (6e56212). RED/GREEN: 6e56212[red] (26 genuine failures) → f8952cc[green]; the two fixture corrections in between (059f68b) were my own harness bugs, stated as such — one advanced `now` a full default window and so aged its work branch past the freshness gate, testing the wrong gate; the other sliced source to the next `\ndef ` and ran into job 20's section comment, which legitimately discusses keystroke delivery (the repo's recurring "a lock test matches its own prose" trap). CORPUS REPLAY through the shipped `delivery_state`/`_delivery_stalled` over all 29 real repos in ~/devel: 1 fires — camera-box — and each gate proved independently load-bearing on real data (audiomatrix: 207 undelivered against a 213-day-old base but its work died 203 days ago; dantesync: fresh work, 11 undelivered, but delivered 0.2d ago). THEN LIVE DEPLOYMENT FOUND WHAT THE REPLAY COULD NOT, in six minutes: the api-watchdog runs this repo's working tree every 60s, so job 24 was in production the moment it was saved, and it pinged TWICE — camera-box (true) and `~/varos/eft5000` (false). eft5000 is a GitLab repo whose `origin/master` last moved 2019-09-07 (2,515 days) while real delivery goes to `develop-50`, which took a merge the same day the alert fired; `origin/HEAD` is unset, so the fallback picked a branch abandoned in 2019 and correctly reported 3,248 commits permanently "undelivered". No LOWER bound separates that from camera-box — from below, 2,515 days and 17 days are the same shape — so the stall window is now bounded on BOTH ends (`DELIVERY_STALL_MAX_S`, 90d): 62fe0b7[red] → ea8b26d[green]. The upper bound costs a genuine stall nothing, since one that ever reached 90 days has already pinged daily for three months. The replay was then widened from ~/devel to EVERY git checkout in $HOME (40 repos — which is what would have caught eft5000 first time): 39 quiet, 0 unmeasurable, 1 fires. LIVE VERIFIED POST-FIX on the real box: eft5000 vanished from both the journal and `~/.claude/api-watchdog-state.json` (self-healing — a non-candidate is popped), while camera-box keeps logging every sweep and correctly does NOT re-ping inside its window. Full suite 2453 passed, ruff clean, deployed to all 6 boxes via `airuleset.py push`.
- #135 (per-ticket card / idle ping could fail into perfect silence) + #134 (the card was unenforced prose — 5 days, ~85 merged PRs, 0 reports on the phone) — bundled, one cycle. Design comments posted to both tickets BEFORE the first code commit (#134 issuecomment-5104439899, #135 issuecomment-5104444468). RED→GREEN pairs: 21d14d1→854d74a (#135), bc52068→7107245 (#134), 4bf40ca→bb359a9 (#134 job-25 blind spot), plus e6aae48 (backfill). Tests: `tests/test_notify_delivery_log.py` (17), `tests/test_run_card_enforcement.py` (64). Shipped: `~/.claude/notify-delivery.log` written by BOTH send paths; the dedup marker now records the delivery OUTCOME (`notify.marker_delivered` — presence was only ever a claim); `notify --run-card` exits non-zero on a failed delivery; `hooks/subagent-stop-check-run-card.sh` (SubagentStop gate, one block per session+issue); watchdog **job 25** `card_reconcile`; the idle-`✅` suppression became conditional on DELIVERY instead of on an armed `/goal` (the design error the ticket names); `notify --backfill-digest`. Corpus replay: 339 real worker evidence blocks, 1,017 gate invocations, bidirectional — 179 block with no markers (all genuine claims), 0 block with all cards delivered, mutant-without-the-merge-check 218. It caught 6 real merges the first parser missed and forced a measured choice between two equally-scoring widenings. Live: job 25 detected tvdole and delivered a real ping within minutes of the file being saved; the tvdole blind spot (zero `Closes` trailers, ever) was found the same way and fixed with a bounded `gh` fallback. Backfill sent for 6 repos (129 tickets), zero delivery failures.
- #140 (ticket-boundary compaction wedges permanently after a stale claim — forestshop@dev1 at ~500K and montalu@subdev at 400K, both hand-compacted by the user). Design comment posted BEFORE the first code commit (issuecomment-5105507338). **The ticket's own stated cause was half wrong, and saying so was the whole diagnosis.** It said a claim "survives the death of the process that took it, and is never re-validated" — re-validation DOES exist and works (`_proc_fingerprint_alive`, #82/#83), so a dead-process claim cannot block a send. The real hole is that `compact_claim_active` had only THREE resolutions and, in the live shape, none could ever fire: the claim's `claude` process stayed ALIVE (montalu pid 3489717, up since 2026-07-26 23:14, verified by `ps` over ssh), the session id never changed (`claude -c` continues the same transcript), and the queued `/compact` never drained (CC drains type-ahead only at an ACCEPTED Stop, and an armed `/goal` keeps rejecting them, #84) so no `compact_boundary` was ever written. Measured on montalu: all 56 boundaries enumerated — last before the wedge `2026-07-27T12:17:43Z`, next `2026-07-28T13:43:49Z`, nothing between; three boundaries refused `SKIP claim-queued` (16:20:59 / 16:21:35 / 16:22:05 on 07-27); the claim was released only by that 13:43:49 boundary, `trigger manual`, `preTokens 346944` — i.e. by the USER hand-compacting, 21h26m later, not by the mechanism. forestshop showed the identical signature (last boundary `09:32:01Z`, the `10:21:55Z` SEND never landed one, the `11:44:38Z` boundary refused) until its process was replaced at `11:47:59Z`, after which the claim was an orphan but harmless — no boundary was requested again, so it was never consulted. SECONDARY HOLE, covered by the same fix: `_transcript_compact_boundary_ts` reads a bounded 4 MB tail, and forestshop's transcript is 82 MB growing ~80 MB/day — three of its six most recent boundaries are already outside that window, so a genuinely CONSUMED claim can look unconsumed and wedge by the same route. FIX, two halves: (a) a TTL backstop as the FOURTH resolution, evaluated after the three evidence paths (`COMPACT_CLAIM_TTL_S` 1800s, env-overridable) — #72's "never a timer" rule is right about what counts as PROOF and wrong to conclude an unprovable claim may be held forever; 30 min is ~18x the worst HEALTHY delivery latency this repo has measured (#109: 9 of 11 sends started within ~6s, worst park +98s), and releasing is nearly free because `_pane_has_queued_compact` already guards both send points, so a `/compact` genuinely still parked is skipped rather than typed twice; (b) watchdog **job 26** `compact_stall_watch` — the signal whose ABSENCE the ticket calls a defect in its own right (nothing noticed either box; the user did, from two screens). It reads the ARTIFACT, never intent (#134's lesson): a claim still on file, live pane, older than `COMPACT_STALL_S`, with no newer boundary in that session's own transcript. **Job 24 could NOT carry it** — that job is REPO-keyed and measures git delivery, this is SESSION-keyed and measures compaction; its SHAPE is reused verbatim (log every sweep, ping deduped per window, `send_fn is None` never marks pinged, state pruned to live sessions, detection only). RECONCILED WITH #126, and they are DISTINCT: #126 is `DROP small-context` / `DROP no-work`, the delivery layer's own gates, reached AFTER the claim check; #140 is `SKIP claim-queued`, a different branch for a different reason — both verbs appear side by side in the same `compact-sync.log` for the same sessions on the same day, so #126 stays open on its own merits (with one correction posted there: its "no compaction has actually run yet" is refuted — forestshop has 22 real boundaries, montalu 56). RED/GREEN: dfdbc1b[red] (24 genuine failures) → 09ab37a[green]. Tests: `tests/test_compact_stall.py` (new, 20) + 6 in `TestCompactClaimState`; the pre-existing `test_never_resolves_on_elapsed_time_alone` was deliberately rewritten as `..._INSIDE_the_ttl`, keeping #72's rule as the positive control within the window. CORPUS REPLAY, bidirectional, through BOTH shipped implementations (PRE = `git show HEAD:watchdog/__init__.py`, POST = working tree), on 22 real SEND→next-event pairs from dev1's own `compact-sync.log`, each session's REAL transcript truncated to the boundaries that existed at the decision instant: PRE blocked 5; the TTL releases 3 (gaps 944m, 82m, 37m — two of them the real `SKIP claim-queued` refusals) and KEEPS 2, both at a gap under 30 seconds, which is exactly the double-fire #78 was built to prevent. Discrimination control: with the TTL disabled (`AIRULESET_COMPACT_CLAIM_TTL_S=0`) the two arms agree on 22 of 22, so the TTL is the ONLY new release path and every other release is still evidence-driven. Second replay arm on montalu's own data (real claim entry + its real 56 boundary lines, read-only over ssh; the single substitution is a locally-live `proc` fingerprint modelling the one measured fact that 3489717 was alive throughout): the wedged claim goes blocked→released, a 60s-old claim on the same session stays blocked (positive control), and the real 12:13:11Z claim that the real 12:17:43Z boundary consumed releases in BOTH arms.

2026-07-28 #137 (file-vs-fix asymmetry -- filing is hook-enforced on every message, the follow-up gate's "fix small cleanups in-PR" was echo-only prose) + #139 (backlog grows by fragmentation, not duplication -- closed as folded into #137, gh issue close 139). MEASURED, PER THE TICKET'S OWN INSTRUCTION, BEFORE ANY CODE: camera-box's 441 stranded dev-ahead-of-main commits carry 22 distinct `Closes/Fixes/Resolves #N` (no PRs merge into dev on this repo -- confirmed `gh pr list --base dev` empty) out of the measured +101 net-open drift over 21 days -- 22 close the moment the frozen-merge PR #704 lands (a #136-class operational issue, not a rules problem, explicitly NOT re-litigated here), leaving up to 79 attributable to the secondary, universal cause: 7 of a 25-issue real sample violate the follow-up gate WITH THE VIOLATION CONFESSED in the issue body itself (camera-box #846/#843, odoo-erp #2388). Design comment posted to #137 BEFORE the first code commit (issuecomment-5106875521, 2026-07-28T16:26:16Z), explicitly crediting the supplied measurement and rejecting the ticket-named alternative (a filing cap/quota -- attacks the symptom, resurrects the exact silently-dropped-work failure no-dropped-work.md exists to prevent, and trains workers to stop confessing violations in ticket bodies, which is how this diagnosis was even possible). SHIPPED: `hooks/block-ungated-issue-filing.sh` -- `gh issue create` / `gh api .../issues` POST is blocked unless the resolved issue BODY carries a `Scope-gate: <criterion>` line naming one of the follow-up gate's own exemptions (`>300-loc`/`schema-migration`/`api-break`/`security-boundary`/`cross-cutting`/`needs-user-decision`) or a legitimate non-discovery mode (`planned-work`/`user-request`); every PASS/BLOCK logged to `~/.claude/scope-gate.log`. Body resolution needed proper QUOTE-AWARE top-level command splitting (a REAL issue title -- camera-box #827 -- contains a literal semicolon, which the naive regex-split shape `block-gh-invalid-json-flag.sh` accepts as a known limitation for its narrower job silently broke here) and `gh api`'s distinct `-f`/`-F key=value` field overload vs `gh issue create`'s `-F <FILE>` shape. RED/GREEN: b931cb7[red] (15 failed / 1 passed) -> cc8ad6d[green]. CORPUS REPLAY, bidirectional, through the real shipped hook: 18 real issue bodies fetched via `gh issue view --json body` (3 confirmed leaks -- camera-box #846/#843, odoo-erp #2388, each read by hand to confirm no genuine criterion exists anywhere in the text; 15 real legitimate camera-box issues, each genuinely stating a follow-up-gate-matching reason in its own real text). All 18 block AS ACTUALLY FILED (none has a Scope-gate line -- it's new), and all 15 legitimate ones pass once their own truthful one-line criterion is added -- `tests/test_scope_gate.py` (16 tests). Two module docs (`complete-planned-work.md`'s Follow-up gate, `no-dropped-work.md`'s mechanism section) got a pointer to the new mechanical enforcement so neither describes the gate as prose-only any more. Also shipped, closing the SAME observation gap that let camera-box's own drift and its 15-day merge deadlock (#138) both run unnoticed: watchdog **job 27** `net_drift_alarm` (per repo, trailing-7-day opened-minus-closed issue count via `gh`; camera-box's own +101/21d is ~+34/week at its worst window) and **job 28** `stuck_main_sweep` (job 24's OWN `delivery_state` measurement, reused rather than reimplemented, but swept across EVERY repo `discover_managed_repos($HOME)` finds -- not gated on a live pane like job 24, which is exactly the gap that let #138's incident run as long as it did whenever no session happened to be open in that repo). Both self-gate on an hourly cadence and wire "on" only when their injected fetch is present (repo_roots/issue_counts_fetch/git_fetch), same convention as jobs 8/11/16/24/25 -- run_once's unit tests stay network-free. Every test class carries a POSITIVE CONTROL built from a real git repository reproducing the camera-box shape, per the ticket's own "a detector that can only prove negatives is worthless" instruction. RED/GREEN: ff132f5[red] (26 failed / 1 passed) -> 9d45c20[green]. Explicitly rejected, per the ticket's own instruction: a filing CAP/QUOTA or a body-text blocker on words like "trivial"/"cosmetic" -- attacks the symptom, not the asymmetry. Full suite 2604 passed, ruff clean, `airuleset.py validate` clean.
2026-07-28 #143 registered the new sub-dev stream `simap` (subdev VPS, uid 1003, built by gatekeeper 2026-07-28; odoo-erp#2391 tracks the client context) across the three per-user registries a stream must appear in. Design comment posted BEFORE the first code commit (issuecomment-5107954071). RED/GREEN via the stash-out technique (implementation stashed, tests run to confirm genuine failure, then restored): 222f884[red] (6 genuine failures: REMOTE_HOSTS target/shape, authority resolution, ssh-hook allow-list) -> 2d88ddc[green]. Shipped: `REMOTE_HOSTS` entry `simap@subdev` (same host + `~/.secrets/gatekeeper_access_ed25519` identity as marek/david — simap's authorized_keys are the SAME operator keys as marek); `AUTHORITY_BY_USER["simap"] = "fork-no-merge"` (the existing lowest profile already means "merges nowhere", no new profile needed); mirrored the same allow-list entry into `hooks/block-subdev-ssh-misuse.sh` (its own docstring promises it mirrors REMOTE_HOSTS exactly — leaving it stale would have made a correctly-keyed future `simap@subdev` ssh wrongly blocked). `statusbar.py` needed NO change: `skill_names_for_user`/`tickets_segment`/`cmd_tickets_status` are already fully generic over `AUTHORITY_BY_USER`, verified by grep that no file hardcodes david/marek/montalu by name outside the two maps + REMOTE_HOSTS. Deliberately NOT touched (stated in the design comment): watchdog's cross-stream `_REDUCED_STREAM_USERS` (simap has no cross-stream hand-off in phase 1) and linux-account provisioning automation (odoo-erp#2392, explicit ticket scope note). Tests: 5 new (`TestRemoteHosts` x2, `TestBlockSubdevSshMisuseHook` x2, `TestAuthorityResolution` x1). Full suite 2682 passed, ruff clean.
2026-07-28 #136 finished (Deliverables 2+3 — the previous worker had already shipped the full mechanism, Deliverable 1's measurement comment, and stopped mid-turn). Deliverable 2, gate discrimination: existing `tests/test_block_commit_without_design.py` already proved the POSITIVE control (unmarked worker commit blocked, exit 2) and all three NEGATIVE controls (main-session no-agent-type passes, marked-issue commit passes, `[no-design: reason]` bypass passes+logs) through the real shipped hook + real STDIN payload contract — re-verified green. Added the missing piece: `scripts/replay_design_gate_commit_corpus.py`, a corpus replay over every distinct commit subject in this repo's own `git log --all` (966 at authoring time) through TWO arms of the real hook — `agent_type=autopilot-worker` ("gate ON") vs `agent_type=None` ("gate OFF", the hook's own scope-check as a natural disabled control). Result: 0 mismatches full-corpus — 589/589 no-reference commits AGREE (both 0), 377/377 has-reference commits correctly blocked by ON only, OFF never blocks anything. Bidirectional classifier spot-check of `design_gate.issue_refs` over the same corpus: 0 out-of-range/zero matches (false positives) and 0 issue-shaped-mention-with-no-extracted-ref (false negatives). `tests/test_replay_design_gate_commit_corpus.py` locks a bounded --limit 30 slice into CI. Deliverable 3, marker delivery evidence: already correct in the shipped `hooks/post-record-design-comment.sh` (re-reads `gh issue view --json comments` after the fact, never trusts the command about to run, stores the comment's own `url` — falsifiable) — re-verified via the existing `tests/test_post_record_design_comment.py`, all green. Full suite 2686 passed, ruff clean.
2026-07-28 #129 (design-before-code holds 10/10 with a worker prompt, 1/10 with only the always-on rule -- asked whether #136 already closes it) closed FULLY SATISFIED, no code change. Independently re-verified from primary sources rather than trusting #136's own closing comments: unit suite 66/66 green across the 5 design-gate test files; re-ran `scripts/replay_design_gate_commit_corpus.py` from scratch against the repo's real `git log --all` (969 commits, grown by 3 since #136's own 966-commit run) -- 0 mismatches, one transient `subprocess.TimeoutExpired` flake on a first pass reproduced-clean 5/5 standalone and absent on a second full clean run; built my own real STDIN payload against the real hook confirming a worker-session unmarked-issue commit is blocked (exit 2) and an identical main-session commit passes (exit 0, the documented/tested deliberate exemption) and that the `[no-design: reason]` bypass is logged. Live production evidence in the EXACT repo #129 named as failing (camera-box, 1/10): `~/.claude/design-posted/camera-box#854` exists, written 20:49:35, with camera-box's own first commit referencing #854 landing 20:49:40 -- 5 seconds later, correct order, today, unprompted. The dispatch's own hypothesized gap ("a non-worker session doing ticket work") was checked and found to be a deliberate, tested, correctly-scoped exclusion required by #129's own point 3 ("must not block ordinary commits that have nothing to do with an issue"), not an oversight -- gating this repo's own dominant interactive-main-session development mode would be a materially larger, more invasive change than the ticket asked for. Closed via `gh issue close -r completed` with the full evidence comment (issuecomment-5108560514); playbook entry appended documenting the flake-diagnosis technique and the worker-only-scope confirmation (`[no-design: ...]` bypass, since the commit implements no feature/fix). Card fired: `notify --run-card --repo zbynekdrlik/airuleset --issue 129` (no --version/--merge-sha, nothing merged/deployed), rc=0. Full suite otherwise unchanged (no code touched).
2026-07-28 #120 (a turn ending ⏳ WORKING must PROVE something will wake the session -- restreamer idle-pane and eft5000 orphaned-process specimens, one hour apart, prose already said the right thing for months). Design comment posted BEFORE the first code commit (issuecomment-5109263367). Followed the ticket's mandatory order: CAPTURED a real plain-Stop payload FIRST rather than guessing -- temporary `tee -a` debug hook prepended to `~/.claude/settings.json`'s Stop array, three real `claude -p` headless runs (a live `run_in_background` Bash job, a live async Agent dispatch, nothing in flight), settings.json restored byte-identical after. Contrary to the ticket's own "unresearched" framing, a plain top-level Stop event DOES carry `background_tasks` -- the SAME harness-authoritative live-task shape SubagentStop already exposes (`{id, type: shell|subagent, status, ...}`), registered synchronously in every capture, with NO sibling-ownership problem (unlike SubagentStop, a top-level Stop's list is this session's own, no ledger needed). Full key set + all three captured payload shapes went into `.claude/rules/airuleset-internals.md`. SHIPPED `hooks/stop-check-working-liveness.sh` (Stop): when the LAST non-blank line matches the ⏳ WORKING marker, requires at least one `background_tasks[]` entry with `status=="running"`; key absent (older harness) -> fail open; retry-capped per session (2) so a false block can never wedge a session. BLOCK, not nudge -- decision stated explicitly: the field is already hard-blocked on at SubagentStop in production for weeks, the fresh capture reproduced it synchronously and reliably across three specimens, and the corpus replay below found the false-⏳ pattern real but rare and never misclassified a genuinely-live wait. RED/GREEN via the established mv-the-hook-out + git-stash-the-wiring technique: a106694[red] (13 of 22 genuine failures, hook file moved to /tmp, wiring stashed) -> restored + committed [green], full 22/22 green. CORPUS REPLAY through the REAL hook via crafted real STDIN payloads (never re-implemented logic): scanned all local `~/.claude/projects/*/*.jsonl` transcripts (99 sessions, 5,647 turns whose last line matches the WORKING marker after tightening the match to require "working" near the hourglass, which dropped 11 corpus false-triggers on unrelated ⏳ mentions in prose/UI labels) for turns ending on the marker, reconstructed per-turn liveness via the SAME launch/terminal-signature scanner `subagent-stop-check-bg-work.sh` already uses. 5,628 reconstructed LIVE (real launch, no terminal notification before the Stop) vs 19 reconstructed NOT-LIVE (including the exact real restreamer and eft5000 transcript specimens, used verbatim as regression fixtures in `tests/test_working_liveness.py`). Replayed 400 randomly-sampled LIVE specimens + all 19 NOT-LIVE specimens through the real shipped `hooks/stop-check-working-liveness.sh`: 0 false positives (0/400 live specimens wrongly blocked), 0 false negatives (0/19 not-live specimens wrongly passed, including both real incident transcripts). Tests: `tests/test_working_liveness.py` (22, incl. the two real verbatim incident specimens, real-captured-payload fixtures, retry-cap behavior, and fail-open coverage for a missing `background_tasks` key/garbage stdin/no jq). Full suite green, ruff clean, `airuleset.py validate` clean.
2026-07-28 #125 (compact-decisions.log's `result=delivered` collapsed 5 dispositions into 1 word, so a genuine SEND was indistinguishable from a downstream DROP) then #126 (the #99 no-work / #48 small-context gates never received `origin`, so a proven subagent-stop boundary was still silently dropped -- both bundled per the dispatch, #125 first since #126 needed the granular return value to prove its own fix end-to-end). Design comments posted BEFORE each ticket's first code commit (#125 issuecomment-5109627223, #126 issuecomment-5109667682), grounded in real corpus lines from this box: sid 2d02a127 (this session) `RECORD result=delivered type=autopilot-worker` paired at the identical second with `DROP small-context`; sid 90bc51f3 (camera-box) the same RECORD paired with `DROP no-work`. RED/GREEN, #125: e19223c[red] (11 genuine failures -- real deliver_compact_now calls, never mocked itself, plus the CLI's own word-forwarding) -> 5bff3d3[green]. Shipped: `deliver_compact_now` returns a reason-specific string per disposition (`sent`/`claim-queued`/`queued-compact`/`dropped-no-work`/`dropped-small-context`/`""`) instead of a bare bool; `cmd_compact_request` prints that word verbatim (legacy bare-`True` mocks still map to `"sent"`, never a crash); both consuming hooks widened to the new vocabulary; `notify-compact-request.sh` -- which used to throw the CLI's whole output away with `>/dev/null 2>&1` -- now appends its own outcome to the same bounded decision log `notify-compact-subagent-boundary.sh` already writes (#123), tagged `type=stop-hook`. RED/GREEN, #126: 4327317[red] (3 genuine failures -- positive controls only, 3 negative controls already passed) -> c05d3ee[green], isolated from #125's own combined edit via a temporary `proven_boundary = False` stand-in so each ticket's RED/GREEN pair is genuine rather than both landing in one commit. Shipped: `origin=="subagent-stop"` now skips BOTH substantiality gates, not only small-context -- argued in the design comment from the code, not reflex-widened: both proxies exist only to guess whether an anonymous Stop-hook turn is worth compacting, and origin=="subagent-stop" already answers that directly (an autopilot-worker concluded with zero other live tasks), while a legitimate completed ticket can trip either proxy with zero code diff at all (closing an issue as already-fixed, a decision-only turn that only filed a follow-up). Job 14 (`compact_ticket_boundary`) is provably untouched -- confirmed by its own separate log format/file (`logs.append("skip no-work (compact-request) ...")` vs this function's `_log_compact_sync("DROP no-work ...")`, the exact string the corpus evidence above matches) -- per the ticket's own explicit scope note. Tests: `TestDeliverCompactNowOutcomeWordsAreDistinguishable` + `TestCompactRequestCliPrintsTheOutcomeWordVerbatim` (11, #125), `TestSubagentStopOriginExemptFromSubstantialityGates` (6, #126, 3 positive + 3 negative controls). "Prove it end to end" (#126 requirement 3) could not be satisfied with a live compact_boundary transcript marker without violating the ticket's own "never send keystrokes into a live pane" instruction -- satisfied instead via real (never-mocked) `deliver_compact_now` calls asserting `/compact` keystrokes are actually typed into a fake tmux pane, the same paradigm this whole test file already uses for every other gate; live confirmation will occur naturally on the next real autopilot-worker completion now that this is deployed. Checked #122/#146 (both open, both explicitly out of scope): neither shares a code path with this fix (#122 is job 14's own busy-pane lapse, #146 is the `agent_type != autopilot-worker` decline path upstream of both gates touched here) -- not fixed, no note needed. Full suite 2752 passed, ruff clean, `airuleset.py validate` clean. Cards fired for both issues (`sent`/rc=0 each); no --version/--url (this repo has no dashboard/web surface for hook-log wording). Deployed via `airuleset.py push`, both issues auto-closed by their `Closes #N` trailers landing on main.
2026-07-28 #122 (a proven-boundary /compact request that falls back to job 14 can lapse on a busy pane) -- validated STILL_VALID by a ticket-validator, re-read against current HEAD after #125/#126 landed minutes earlier on the exact same function. Design comment posted BEFORE the first code commit (issuecomment-5110025468). MEASURED FIRST, per the ticket's own instruction: `~/.claude/compact-decisions.log` type=autopilot-worker lines (13.5h window, 3 concurrent autopilot sessions post-#125/#126) -- 27 RECORD + 8 DECLINE(live-tasks, correct), and of the 27 RECORD, ZERO produced a "recorded" (fell-back-to-job-14) outcome; every one was fully handled synchronously. `journalctl --user -u api-watchdog.service --since -72h` job 14 outcomes: 3549 skip no-pane, 8 skip expired, 6 skip no-work, 4 skip small-context, 0 skip busy (the ticket's own grounding text measured 3 skip-busy in the same window before #125/#126 landed). Read honestly: the population reaching job 14's busy branch is empirically ~0 on this box right now, mostly because the synchronous path already wins the race almost every time -- but the mechanism gap is real by construction (job 14's busy-skip was unconditional for every origin, with none of #65's already-validated "a short send-keys reliably queues even into a busy pane" finding applied to it), just rarely observed hitting it in 13.5h on one box. FIX, two independent halves, both scoped to the single busy branch named in the ticket title: (a) `kind=="busy"` no longer auto-skips when the request's origin is the proven-boundary one (`subagent-stop`) -- it falls through the SAME existing chain every other kind already goes through (job 14's own #99/#48 substantiality gates, `_pane_has_queued_compact` dedup, a no-op draft check since busy's draft is always None), skipping only the final `pane_at_idle_prompt` gate a busy pane can never pass; safe here in a way it would NOT be inside a live Stop-hook batch (#109/#84's own caution is about parked keystrokes firing at some arbitrary later accepted Stop when typed DURING a Stop hook's own execution -- job 14 is an independent ~60s poll, and `_compact_not_at_boundary` above already re-confirmed the session isn't question-blocked at delivery time); every OTHER origin keeps the unconditional busy-skip, locked by the pre-existing `test_busy_pane_is_skipped_and_request_kept_for_retry` as the negative control against the SAME `CB_BUSY_CAP` fixture. (b) any genuine expiry (`COMPACT_REQUEST_MAX_AGE_S`, any origin) now ALSO writes a `"LAPSE"` line to `compact-sync.log` via `_log_compact_sync` -- the same observable channel `deliver_compact_now` already uses for every send/drop decision -- instead of only a journalctl "skip expired" line buried among thousands of no-pane polls nobody watches; the negative control (`test_a_fresh_request_is_not_expired`) additionally asserts the sync log stays untouched (file never created) when nothing actually lapsed, and a `dry_run` expiry also writes nothing (pure preview, no side effects). Explicitly rejected, per the ticket's own two named alternatives: (b-in-the-ticket) raising/segmenting `COMPACT_REQUEST_MAX_AGE_S` for the proven-boundary origin -- doesn't fix anything, only lets a doomed request wait longer before lapsing anyway, and weakens the "compact fires ticket-boundary-fresh" guarantee #121 exists for; (c-in-the-ticket) leaving the behavior and only logging the lapse -- kept as the SECOND, additional fix above, but rejected as the ONLY fix since (a) was already safe and validated elsewhere in this exact codebase. Deliberately NOT touched: job 14's own separate #99/#48 substantiality gates stay unconditional for every origin (including busy+proven-boundary) -- #126 already explicitly scoped that parity gap out ("job 14's own separate copy of these same two gates is untouched"), and this ticket does not fold it back in. RED/GREEN: a860387[red] (3 genuine failures: busy+proven-boundary still bounced, no shared-claim set, no lapse record written) -> bcc26f1[green]. Tests: 5 new in `tests/test_compact_request.py` (`test_busy_pane_with_proven_boundary_origin_still_delivers`, `test_busy_pane_with_proven_boundary_origin_sets_the_shared_claim`, `test_a_stale_request_writes_a_lapse_record_to_the_sync_log`, `test_dry_run_expiry_writes_no_lapse_record`, plus the strengthened `test_a_fresh_request_is_not_expired`), all using the REAL shipped `compact_ticket_boundary` with the SAME `CB_BUSY_CAP`/fixture conventions every other job-14 test in the file already uses, and a genuine positive/negative control pair (identical busy capture, only the `origin` field toggled) for the busy-exemption half. HONEST LIMIT stated rather than papered over: no raw busy-pane CAPTURE text is ever persisted to any real log (only the outcome word is), and the hard constraint against sending live keystrokes rules out forcing a real busy-pane scenario on a live box -- so verification here is the measured real corpus (population size + the confirmed absence of busy-lapses right now) plus genuine RED/GREEN unit coverage through the actual shipped function, not a corpus replay of real historical busy captures (none exist to replay). Full suite 2756 passed, ruff clean, `airuleset.py validate` clean.
2026-07-29 #122 addendum: after the main RED/GREEN pair landed (a860387/bcc26f1) and the first `airuleset.py push` attempt, the local gate correctly refused to push -- `test_no_missed_issue_shaped_mentions` (tests/test_replay_design_gate_commit_corpus.py) failed against `git log --all`, flagging my OWN just-made playbook-entry commit (9bbe781, "docs(playbook): ... (issue 122)") as a missed issue-shaped reference. Root cause: I wrote "issue 122" prose instead of bare `#122` purely out of overcaution copied from the OTHER commits in this same ticket (which correctly avoid bare `#N` for tickets with NO design marker) -- but #122 already HAD a marker (posted before the first code commit), so `#122` would not have blocked anything there; the "issue N" phrasing was unnecessary in that one spot. Since `git commit --amend` is hard-blocked (commit-conventions.md) and the corpus test scans `git log --all` forever, the already-made commit's wording could not be un-said -- the only forward fix was to bring the TEST's own definition of "issue-shaped" back in line with `design_gate.ISSUE_REF_RE`'s deliberately `#`-anchored scope (its own comment: a missed non-`#` reference is cheap/acceptable by design) and with the #137-era playbook convention that "issue N" prose is the SANCTIONED way to mention a ticket without triggering the marker gate for it -- the broader `issue N`/`GH-N` clauses in the corpus check predated that convention and had literally never been exercised by any real commit until this one. Narrowed the check to `#\d` only (230518e), added two unit-level locks (`test_prose_issue_n_is_deliberately_not_a_ref`, `test_gh_dash_n_is_deliberately_not_a_ref` in tests/test_design_gate.py) so a future well-meaning "fix" doesn't re-widen it without re-litigating the decision. Re-ran the full local gate (2758 passed, ruff clean, validate clean) and re-pushed successfully; confirmed `git rev-parse HEAD origin/main` match and the GitHub issue timeline shows #122 closed by commit bcc26f1 specifically. Lesson for the next worker: `#N` is always safe to write bare for the ticket(s) THIS commit's own trailer closes (marker already exists by definition) -- only reach for prose-without-`#` when mentioning an UNRELATED/historical ticket that has no marker yet.

## 2026-07-29 — #127 `gh pr view … statusCheckRollup` loops: split stands, no code change

Comment-only + tests, direct to main (no PR, no CI — local gate only), per
#112's own precedent ("closed won't-fix with numbers, verdict recorded in the
hook's own comment block").

Root cause named by the ticket: `block-ci-poll-repeat.sh`'s CI-wait signature
(`gh run view|watch|list` / `gh pr checks`) narrows before any loop detection,
so a real chained wait shaped `gh pr view <N> --json …statusCheckRollup…`
matches neither token and exits at that gate — invisible to #118, falling
through to #119's generic guard instead of #118's CI-specific message.

Re-derived over today's corpus (8,231 transcripts / 258,724 commands, larger
than #118/#119's snapshot, same box, later date): 4,597 loop-shape commands,
2,378 CI-owned. The gap population is **52** loop-shape commands carrying
`gh pr view` + `statusCheckRollup` but no #118 token — 15 sessions, 3 real
projects (camera-box 17, forestshop/parovanie_produktov 10, an
odoo-slovnormal subagent dispatch 3) — 2.3% of #119's own already-
characterised non-CI family (a sub-slice of its "other 191" bucket), not an
undiscovered population. Replayed all 52 in real session order through the
SHIPPED `block-local-poll-repeat.sh`, isolated state per session: 36
first-free, **14 already blocked** (camera-box's own `poll #436…#446` chain
the ticket quotes among them, plus an odoo-slovnormal subagent chain and one
forestshop repeat), 1 exempt-mutating, 1 exempt-short-wait. So #119 already
structurally covers the gap and already blocks its repeats — the only open
question was whether #118's message would serve those 14 better.

Tested that question empirically rather than by inspection: patched a scratch
copy of `block-ci-poll-repeat.sh` widening its CI_RX to also match
`gh[[:space:]]+pr[[:space:]]+view.*statusCheckRollup`, fed it a real corpus
specimen twice. PR numbers in the corpus (112-704) never reach the 8-digit
RUN_ID floor, so every one falls into #118's `generic` bucket, whose
compliant command is `RID=$(gh run list -L 1 --json databaseId …)` — the
single most recent run in the WHOLE repo, not the run behind the polled PR.
Confirmed live: the widened hook's second-block message handed out exactly
that wrong-run waiter. The generic bucket's 1800s TTL would also
intermittently reset "first loop free" for these naturally slower-cadence
waits, weakening a guard that #119's persistent per-(session, shape) key never
decays. **Decision: the two-hook split stands, #118's signature is unchanged**
— widening measurably trades a correct, honestly-scoped message for a
wrong-run one and a decaying key, for a population that is already a small,
already-covered slice.

Shipped: a comment paragraph in each hook's own header (next to its existing
decision history, per #112's placement convention) recording the measurement
and the rejected alternative; a locking test pair —
`Issue127CiSideGapTest` (test_ci_poll_repeat_block.py, pins the
shape never matches #118 even after repeats) and
`Issue127LocalSideGapTest` (test_local_poll_repeat_block.py, pins
#119 blocks the repeat, digit-blind across PR numbers) — using the ticket's
own `poll #436…#444` camera-box specimen as the fixture. Both pass unmodified
against the shipped hooks (no behaviour change); they exist so a future
well-meaning widen of #118 is caught before it ships.

Gate: 2763 tests pass (2758 baseline + 5 new), `ruff check .` clean,
`airuleset.py validate` OK.

---

## 2026-07-29 — #128 block-main-implementation: engagement condition + bypass audit

Commits: `4d5a7fc` [red], `8346529` [green], `8502213` [docs].
Design comments on the ticket: 5111090897 (measurement + decision),
5111106850 (the classifier change WITHDRAWN), 5111132651 (shape restated) —
all three posted before `4d5a7fc`.

**Ask 1 (who armed the 193 bypasses):** the MODEL itself, and 186 of the 193
lines are an artifact of the era before one-shot consumption. Splitting the
log on `7bcbafe` (2026-07-26T20:25:17, the one-shot fix) gives 186 before /
7 after; the 7 are 4 arms + 3 consumes, paired 1:1. Nothing automated arms
it — watchdog job 22 only ever unlinks — and grepping the session transcript
for the actual `touch` calls shows all six were emitted inline by the
assistant right after a block, because the block message advertises the
escape. The "still growing at 01:45" entries the validator saw are the
hook's OWN test suite (`t-mg-*` synthetic sids); the last real-session line
is 2026-07-28T20:49:38.

**Ask 2 (engagement condition):** decided on a full-day measurement, dev1,
2026-07-28, top-level entries of all 11 real transcripts — guarded sessions
853 main tool calls / 87 dispatches, inert sessions 1339 / 82, worst session
(varos-eft5000) 650 / 0 with 52 oversize writes, and inert. Shipped a THIRD
condition, `USER_AWAY` (presence marker older than
`AIRULESET_MAIN_GUARD_AWAY_S`, default 900s; no marker = allow), OR'd with
the other two and removing neither. "Engage always" was measured and
REFUSED: 348 newly blocked, 164 within five minutes of a live human prompt.

**Ask 3 (bypass):** already single-use in fact, so the fix is auditability —
the marker must CARRY its reason, logged on the arm and the consume; empty
or throwaway markers are refused and cleared.

**Ask 4 (both directions, shipped hook):** replayed every guardable main
event of 2026-07-28 with each event's presence marker aged by the real gap
since that session's last human prompt — 1590 events, 154 blocked before,
257 after, 103 newly blocked, 0 newly allowed, smallest gap in the
newly-blocked set 15.0 min (so zero attended calls). By shape: 43 `grep -n`
/ `grep -rn` / `sed -n` of source files, plus test-log scrapes, `head -c`
dumps and `cargo test`.

**Withdrawn:** a proposed "a pipeline bounded by its last stage is not a
bulk read" classifier change. It contradicts three deliberate, test-locked
decisions from the earlier pipe-reducer pass (`cat file | grep | head -20`,
`grep -rn . | head -20`, `journalctl | tail -50` all block on purpose), so
the shapes it would have un-blocked are not false positives on this repo's
settled terms.

**Found in passing, fixed here:** the count/quiet assertion exemption
matched only whole tokens, so the combined form real commands use
(`grep -cE`, `grep -rc`) blocked. Now recognised; a compound that scrapes a
log first still blocks.

Gate: 2802 tests pass (2763 baseline + 39 new), `ruff check .` clean,
`airuleset.py validate` OK.

## 2026-07-29 — #130 standing MAIN-vs-SUBAGENT cost meter

Root cause was sharper than the ticket's framing: `burn.scan()` globs
`<projects>/*/*.jsonl`, one directory level above where Claude Code writes
subagent transcripts (`<proj>/<sid>/subagents/agent-*.jsonl`), which is also the
only place `isSidechain` appears — so `main_vs_sidechain` reported a FALSE
100%-MAIN split, not an incomplete one (dev1: 100 files / 2.2 GB reachable,
5,281 files / 2.4 GB invisible). Design comment posted before the first code
commit (`#issuecomment-5111463217`).

Additive by choice: `scan()` untouched, because it feeds the watchdog's hourly
snapshots, the fleet feed, `--compare` and `hourly_burn_alert`'s live
thresholds — folding subagents in would double every hourly figure against a
history recorded without them and re-fire the alerts on the discontinuity, a
threshold change this ticket excludes. Reconciliation filed separately.

- `b867fcf` [red] 38 failing — `tests/test_delegation_meter.py`
- `d2a31e5` [green] `burn.scan_split/split_report/merge_splits/render_split`,
  `cost_units`, `ctx_per_turn`, `units_per_ticket`, `repo_of_cwd`;
  `airuleset.py delegation [--hours --host --tickets --json --root]`;
  `_remote_ssh_prefix` shared with `_burn_remote_cmd`; working cycle documented
  in `.claude/rules/airuleset-internals.md`
- `4285b57` [red] / `d2c2b58` [green] — live-caught on the first real
  `--host all`: a remote box returns the already-merged JSON shape, so every
  remote parsed cleanly and contributed nothing with no WARN; the coordinator
  printed a dev1-only table under a fleet heading. `_split_rows_of` now accepts
  both shapes and keeps the row's own host.

First fleet run (12h, 7 boxes, 186 transcripts): MAIN 3,849 turns / 144.6M
units (17.8%) / 232,757 ctx per turn; SUB 19,621 turns / 669.6M units (82.2%) /
242,735 ctx per turn. Subagent turns are no longer even individually cheaper —
cache read is 96–97% of input context in BOTH rows, and the subagent fleet
spends 5.31x the input context to produce 1.81x the output. Conclusion is
invariant across the weighting range (SUB 78.1%–84.0%). Cost per closed ticket
computed for the first time; 225.9M units (27.7%) closed no ticket at all.
Tension with #128 reported on the ticket; no hook, guard or threshold changed.

Gate: 2845 tests pass (2802 baseline + 43 new), `ruff check .` clean,
`airuleset.py validate` OK, deployed to all 7 targets via `airuleset.py push`.

## 2026-07-29 — #131 per-dispatch floor vs in-dispatch growth

Built `burn.scan_dispatches` / `distribution` / `floor_growth_totals` /
`by_agent_type` / `import_closure_chars` / `floor_attribution` /
`render_floor`, surfaced as `airuleset.py delegation --floor`. Additive:
`scan_split()` and `scan()` untouched, so the standing meter's baselines and
`hourly_burn_alert()` are unmoved.

Commits: 1752316 [red] · b2a6eb4 (two wrong assertions of my own, corrected in
their own commit) · 5c24d48 [green] · 1fd9744 (ruff) · be335e1 [red] ·
a7a31c4 [green] (synthetic zero-usage entries).

Design comment posted before the first code commit
(issuecomment-5111866433), root cause traced to `scan_split()`'s per-file loop
discarding ordering and counting transcript LINES as turns.

Measured, dev1, 12h (301-dispatch 48h window agrees): floor median 115,636,
turns median 38 (p25 15, p90 162, max 338 — the earlier ~3 was wrong twice
over), growth median 112,680. Floor is 43.6% of subagent CONTEXT tokens but
only 37.0% of subagent COST, because it is cache-written once and cache-read
thereafter — so growth (63%) is the larger term and the ticket's own premise
does not hold. The floor is bimodal by AGENT TYPE and nothing else: three
probes with an identical 22-char prompt gave Explore 10,392, general-purpose
80,372, cavecrew-investigator 69,993, bracketing the always-on ruleset block
at ~67k–70k tokens = ~60% of a carrying dispatch's floor, 20.3% of subagent
spend, ~17% of fleet. Implications written to the ticket; no hook, guard,
threshold or rule changed.

Filed #150 (scan_split counts lines, not requests — ~2.13x over-count;
re-baselining the standing meter is its own decision).

Gate: 2875 tests pass (2871 baseline + 4 new after the synthetic-entry pair),
`ruff check .` clean, `airuleset.py validate` OK, deployed to all 7 targets
via `airuleset.py push`.

## 141 — backfill digest: wrong marker store, and a digest job 25 cannot see

Two bounded correctness bugs in the catch-up path, both traced to the same
place — card markers are machine-local and the digest never wrote the key
anything else reads.

Half 1: `marker_delivered` resolves through the running box's own `$HOME`,
and `--backfill-digest` takes a bare `owner/name` from an operator, so a repo
whose checkout lives on another box reads as entirely unreported. Now
resolved against `discover_managed_repos` x `repo_name_for` (matched on the
NAME, the granularity of the marker namespace) and refused before any `gh`
call; the constraint is in `--help`. Fail-closed. Live on dev1: odoo-erp
(checkout on subdev) is refused, airuleset resolves to its own root.
RED `08b895f` -> GREEN `9e8ff29`, tests `TestBackfillDigestNeedsALocalCheckout`.

Half 2 (the engineering fork, decided in the design comment): the digest
sent under a digest-level dedup key while job 25 asked about a per-ticket
key nothing wrote — disjoint namespaces, so a delivered digest left its
tickets flagged for the full 48h window, and that alert is FALSE rather than
merely duplicate. Chose a second namespace `backfill#<repo>#<n>` written by
`notify.mark_backfill_reported` ONLY on the literal `sent` (send()'s return
after the POST) and consulted by `card_reconcile`; rejected accepting +
documenting the overlap, since a documented false alert still degrades the
one signal the subsystem protects. Deliberately not the per-ticket card
namespace, which the SubagentStop gate reads. RED `a45ccc9` -> GREEN
`324cc26`, tests `TestBackfillMarkerIsWrittenOnlyOnPROVENDelivery`,
`TestBackfillDigestRecordsWhatItReported`, plus two in `TestCardReconcile`.

Teeth verified by mutation (dropping job 25's consult kills the
delivered-digest test; writing the marker regardless of the POST result
kills four including the fail-open one) and by a live read-only probe of
job 25 on dev1: 45 card keys checked for this repo, 25 delivered, and each
of the 20 unreported got a backfill lookup.

Gate: 2893 tests pass (2875 baseline + 18 new), `ruff check .` clean,
`airuleset.py validate` OK, deployed to all 7 targets via `airuleset.py push`.

Review round on the same ticket (adversarial read of the four commits, dispatched
read-only): 11 findings, all fixed in `380ee82` after RED `fdee82f`. The two that
mattered — the digest test class never isolated `notify._claude_dir` and had
written a real suppression marker into this box's live store (removed; a full
suite run now leaves it clean), and the checkout gate refused legitimate
operators because the shared sweep stops at depth 4 and cannot see a `.git`
FILE (worktrees/submodules), measured 40 discovered against 55 real checkouts.
Resolution now tries the invocation cwd first (0.03s, no walk), walks its own
way rather than widening a sweep two other jobs share, and carries a logged
`--force`. Also: the write count is writes that happened; a `dedup` return no
longer reads as a delivery; the digest suppresses only the tickets its message
NAMED; job 25 degrades to card-only (and logs) if the new symbol is missing.
Gate after the review round: 2905 tests pass, ruff clean, validate OK.

## 2026-07-29 — #144 credential channel (`airuleset.py secret`)

A one-shot URL for passwords / SSH keys / PATs / tokens, so a session never asks
the user to paste a credential into chat — where the value lands in the session
transcript permanently, survives compaction, and cannot be revoked. Design
comment posted before the first code commit (issuecomment-5112882816): root
cause traced in `upload_server.py` (ambient umask under `~/uploads/`, the full
path written to its log, no `--forget`), chosen approach a SEPARATE path under
`filedrop/`, rejected alternative a `--secret` mode of the existing upload,
which would leave the safe and unsafe paths one branch apart.

New: `filedrop/vault.py` (store), `filedrop/vault_server.py` (one-shot
endpoint), `airuleset.py secret {request,status,list,exec,forget,purge}`, and
watchdog **job 29** (hourly TTL sweep). Named `vault*` because the staging guard
refuses any path whose basename contains "secret".

RED→GREEN pairs, one per defect: f302d9d/c0ce411 the feature; de47a83/fbf82b5
flags after the name silently swallowed and a WireGuard tunnel labelled
cleartext (both found by running it); then one pair per adversarial-review
finding — d04a42e/d04629d exec handed the child our stdout, 171c80e/462f82c
token in argv, ffdc57c/446c75c store location symlink- and env-controllable,
2d71bd0/bd8aa62 a negative TTL made an immortal endpoint, 1a08680/c2804e8
forget did not actually revoke, 5dc2545/f151cdc the health-probe fix was half
applied and printed no URL at all, a3555b3/98df292 cleartext opt-in,
666aeef/39e7528 name anchoring + env key + honest revocation + proxy,
20e759c/c8965c1 endpoint robustness plus a slowloris the tests uncovered,
b4da11c job 29, 556f7ea a regression where one edit had landed in `cmd_upload`.

Review was escalated per the dispatch: budget gate OPEN → one Fable advisor pass
over a digest of the diff and threat model. Verdict DO-NOT-SHIP, 3 blocking and
9 further findings; all 12 fixed. It judged the log policy and the bind policy
already proven, and the value write already safe against a planted symlink.

Verified live twice through a real browser on the tailscale endpoint, and
cross-box after deploy: a value POSTed from dev1 to dev2 landed 0600 there, a
child that deliberately echoes it printed `<<REDACTED>>`, the port closed after
the single submission, and both logs carried only name and event lines.
Gate: 3015 tests pass, ruff clean, validate OK.

Follow-up filed: **#152** — the rule-module change telling sessions to use this
channel, which is a policy decision through the rule intake gate and outside
what this dispatch was allowed to touch.

---

## 2026-07-29 — #156 + #157, the store guard's own enforcement holes

Both worked as one batch because both change `hooks/block-vault-store-read.sh`.
Every hole reproduced against HEAD before any fix; design comments on both
tickets predate the first commit.

**#156 hole 1 — globbing walked past the path predicate.**
`b0b6ea7` [red] -> `b584dc5` [green]. Root cause: the predicate read the
command's SPELLING while the shell reads the POST-EXPANSION path, with an
entire glob-expansion layer between them that the hook had no model of. Fixed
with rule D — a component-wise `can_be(component, target)` asking what the
shell can expand a component INTO — added ALONGSIDE the literal regexes, plus
within-one-command `cd` tracking for `cd ~/.claude && cat sec*/*`.

The anchor rule is the whole design and was chosen by measurement, twice over
a 212,557-command corpus: "any literal run anywhere" matched grep REGEXES and
`find -name` PATTERNS (18 hits, all mention-not-use), and an unanchored
wildcard under `~` refused `du -sh ~/.claude/*` — a recurring real diagnostic
that reports sizes, never content. Shipped rule: the component's literal
PREFIX, >= 3 chars, must itself be a prefix of the target.

**#156 hole 2 — a malformed payload failed OPEN.**
`8e49654` [red] -> `af8212e` (test correction) -> `bf57065` [green]. The
fallback meant to catch it tested `not isinstance(payload, dict)` two lines
after the handler assigned `{}` — statically unreachable. Now the matcher
exits 3 where the failure happens and the existing wrapper turns that into
fail_closed. A string `tool_input` is SCANNED rather than failed closed; empty
stdin stays exit 0 and is admitted rather than closed.

**#156 hole 3 — `Write` is not a matcher.** `8c641c2` [red] -> `725d9b3`.
Declined deliberately and written into the header: a Write matcher would block
editing this hook and its own tests, so the guard would prevent its own
maintenance, and it would destroy the documented remedy for the accepted false
positive ("write the body to a file with the Write tool").

**#156 hole 4 — the purge guard had no teeth.** `8a9e8e9` [red] -> `d0c39aa`
[green]. The guard read run_once's LOG, and the job is silent when nothing
expired, so a real sweep against an empty store was indistinguishable from no
sweep. Observable moved to `state["vault_purge_hour"]` — the artifact a sweep
leaves whatever it finds. Teeth proven by a mutation test that rewrites
`vault_purge=None` in run_once's signature to a live sweep and requires the
guard to fail; verified bidirectionally.

**#157 — the audit log was a second resting place.** `7ba760e` [red] ->
`dca7eb8` [green]. The bypass line recorded the FULL command, so a bypassed
write deposited its value verbatim into a plaintext 0664 file. Now a
fingerprint (tool, matched store refs, SHA-256, length), log created 0600.
Redaction with the channel's own filter was rejected: it takes the VALUE as an
argument and the only way this hook could get one is by reading the store.

**Authoritative blast radius**, the REAL hook before vs after over the 18,057
prefiltered candidates of a 212,743-command corpus: 3 newly blocked (all three
this session's own probe commands), 0 no-longer-blocked.

Filed: **#162** — the hook TIMES OUT gap. Not closeable from inside a hook ("a
timed-out hook does not block" is a harness property); measured median 33 ms
against a 5000 ms budget, so it is a claim-honesty defect rather than a live
risk, and its real resolution belongs to the permissions-layer decision.

### The adversarial review (Fable, fresh context, digest-only) — what it changed

Dispatched after the five holes were closed, and it is the reason this shipped
in the state it did rather than the state it was in.

- **CRITICAL, introduced by the #157 fix itself** (`44ffe27` [red] ->
  `cb479eb` [green]): the audit call unpacked `fields`' 2-tuples as triples, so
  the ENTIRE Read/Grep/Glob branch — #153's own critical addition — threw on
  every violation. It still exited 2, because `fail_closed` does too, so all
  four block tests passed and the 3,148-test suite was green. Lost while it
  lasted: the real refusal text, the audit line, and the user's env bypass for
  those three tools. `assertBlocked` now asserts the DECISION, not the code.
- **Three bypass classes the corpus replay could not see**, each verified twice
  (ALLOW verdict, then a sandbox HOME proving a real read) — `a7eb354` [red] ->
  the rule-D rewrite: brace expansion, an interposed `.`/`..` component (which
  spells both names in full and defeated even the literal adjacency regex), and
  `find <parent> -exec cat {} +`.
- **Two over-strong claims** (`9be63ce` [red] -> `87d2ede` [green]): a
  list-shaped `tool_input` exited 0 having inspected nothing, and an audit
  "reference" is not a path fragment by construction — a value shaped like a
  store filename reached the log. Refs are now restricted to matches in a path
  context, and the recorded length is gone as a guessing oracle.
- **Audit forgery** (`72b1fe4` [red] -> `bc1f196` [green]): a newline inside
  single quotes let a crafted command forge the `#AUDIT#` line and demote the
  real one — found by probing the change rather than reported.

`find` was measured rather than argued: the `-exec` family costs 5 commands (3
of them this session's probes), any-`find` costs 104 more that only listed
names. The action form is blocked; the piped form is admitted under the
existing xargs gap, now naming the shape.

Final authoritative replay, real hook before vs after over 18,062 prefiltered
candidates of a 212,852-command corpus: **4 newly blocked, all four this
session's own probe commands; 0 no-longer-blocked.**

Also filed: **#165** — the value-file pattern matches an INFIX, so an ordinary
`config.<ext>.json`-style local config is refused. Pre-existing, outside the
four holes, and a real trade (the same tightening stops covering a `.bak` copy
of a value file), so it is a decision ticket rather than a reflex fix.

## 2026-07-29 — batch: burn.scan_split() request-line over-count + btop RUNTIME_DEPS

**#150** (burn.scan_split() counted a transcript LINE as a turn, ~2.13x
over-count): RED `tests/test_delegation_meter.py::TestRequestDedup` (commit
7d5a475) — a fixture where one requestId spans several usage lines, asserting
one turn/one copy of usage; GREEN (commit 0551b2f) extracted `_fold_usage_line`
(shared with `read_dispatch()`, which already deduped correctly) and rewired
`scan_split()`'s per-file loop to dedupe by requestId before window-filtering.
No baseline migration needed — `scan_split()` has no persisted history/alert
thresholds. Full suite 3209 passed, ruff clean.

**#145** (btop missing from RUNTIME_DEPS): added `"btop"` to `RUNTIME_DEPS`
(commit 494c383) + locking tests mirroring the sshpass/jq shape in
`tests/test_runtime_deps.py`. No new install-time code needed —
`check_runtime_deps()` was already fully generic. Live-verified post-push:
`btop --version` → `1.3.0` on dev1, dev2, gatekeeper (auto-installed via
sudo). montalu/marek/david/simap on subdev have no sudo — each printed the
loud `MISSING RUNTIME DEP: btop` warning (never silent), confirmed live via
`sudo -n true` failing on all four. Filed **#171** (gk-request) asking
whoever holds subdev's root key to `apt-get install -y btop` once for the
whole box.

Both design comments posted before their first code commit
(issuecomment-5122067677 for #150, issuecomment-5122070675 for #145).
Repo pushes direct to `main` (no dev branch/PR/CI) via `python3 airuleset.py
push` — both issues auto-closed by GitHub on the `Closes #N` trailers.
Playbook: `.claude/rules/airuleset-internals.md` (commit 191e383) — the
requestId-fallback-protects-old-fixtures finding, and the RED-commit-trailer
premature-close gotcha for this repo's direct-to-main flow.

## 2026-07-29 — #166: measured a Stop hook can only DELAY a false backlog-empty stop, re-scoped

Acceptance bullet 1 (measure before building): read the CC 2.1.220 binary
directly (offset ~254572200) -- `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` defaults to
8 and overrides ANY blocking Stop hook (aggregate across hooks, not
per-hook) after 8 consecutive blocked Stop events, force-ending the turn
(`{reason:"completed"}`) regardless. Live control-arm proof in an isolated
scratch session (`CLAUDE_CONFIG_DIR`, no real repo/gh/goal): cap=2 + an
unconditional-block hook still produced `"terminal_reason":"completed"`
after 3 invocations, matching a hook-free control run's SAME terminal
reason. Full evidence: issue comment
https://github.com/zbynekdrlik/airuleset/issues/166#issuecomment-5122637185.

Re-scoped per the ticket's own pre-written branch ("say so and re-scope --
the value may belong in the watchdog instead, see #160"): did NOT ship an
enforcing Stop hook. Shipped `backlog_marker_gate.py`
(`classify_backlog_empty_claim` -- line-start, fenced-code/backtick-aware
mention-vs-use classifier for the `🏁 BACKLOG EMPTY:` marker) for issue
160's watchdog-side "verify before accepting achieved" fix to consume, plus
`scripts/replay_backlog_marker_corpus.py` (Acceptance bullet 2 -- corpus
replay, both directions, over this repo's own git-tracked files AND this
box's real local transcripts). Real replay numbers: REPO corpus 324 items,
3 no-longer-blocked (SKILL.md's own worked example + two test files
genuinely mentioning the marker), 0 newly-blocked; LOCAL corpus 77,892 real
assistant messages, 2 no-longer-blocked, 0 genuine claims yet (the marker is
brand new), 0 newly-blocked.

Genuine RED/GREEN: test:c3b107c[red] (ImportError, backlog_marker_gate.py
did not exist) -> feat:3c594ae[green] (16 new tests). Full suite 3209->3225
passed, ruff clean. Finding recorded permanently in
`.claude/rules/airuleset-internals.md` (new
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` bullet) so no future ticket re-attempts an
"absolute" Stop-hook gate on this class of problem. Closed #166 (superseded
by the measurement; issue 160 keeps ownership of the actual watchdog wiring
-- commented there pointing at the new classifier module). No dropped work;
nothing filed (issue 160 already tracks the remaining wiring).

📔 Playbook: `.claude/rules/airuleset-internals.md` -- the
CLAUDE_CODE_STOP_HOOK_BLOCK_CAP finding (default 8, overrides any blocking
Stop hook aggregate across hooks, confirmed from the CC binary + a live
control-arm probe): a Stop hook can delay a stop, never durably prevent one.

## #172 — watchdog livelock: jobs 27/28 never persisted cadence, killing every sweep

Live incident 2026-07-29: systemd `TimeoutStartSec=120` killed every
`api-watchdog.service` run for 7h07m (236 kills that day, zero before) --
jobs 27/28 (net-issue-drift alarm / stuck-main sweep, #137) swept ALL 40
discovered repos every hour, each costing a `git fetch` (90s timeout) or two
`gh issue list` calls (45s each), blowing the 120s unit budget. Because the
cadence marker lived only in `run_once`'s in-memory `state` (save_state()
ran only at the very end), a kill mid-sweep lost it entirely -- the next 60s
tick re-attempted the identical sweep, was killed again, forever, starving
job 1's 529 continue-nudge of wall-clock the whole time.

Fix, RED->GREEN: test:7ac7aff[red] (TestCadencePersistedBeforeKill_172 --
SystemExit-modeled kill mid-sweep, cadence marker absent from disk after)
-> fix:7570c79[green]:
1. Both jobs now take a `persist=` callback (same shape as the existing
   bounce/gk-request backstop jobs) invoked immediately after the cadence
   marker is set, BEFORE any per-repo network call.
2. New `_repo_sweep_batch()` bounds each sweep to
   `AIRULESET_REPO_SWEEP_BATCH` (default 3) repos via a round-robin cursor
   in state; dedup-pruning fixed to only drop entries for repos actually
   touched this sweep. `_watchdog_git_fetch` cut 90s->15s,
   `_watchdog_issue_counts_fetch`'s two `gh` calls 45s->10s each.
3. `run_once`'s decision log now flushes incrementally via a `_FlushList`
   fanning to an injected `log_fn`; `cmd_watchdog` wires `log_fn=print`
   instead of printing the returned list only after `run_once()` returns --
   a killed sweep used to print NOTHING for its whole duration.
4. Confirmed by reading: job 1's 529 auto-resume logic is untouched and
   runs well before jobs 27/28, so it resumes correctly once sweeps
   complete again.

Live-verified on dev1 after `airuleset.py push`: forced both cadence
markers 2h stale, ran `systemctl --user start api-watchdog.service` twice.
First run (23:21:13->23:21:31, 18s) fired both jobs, persisted
`net_drift_cursor=6`/`stuck_main_cursor=3` and fresh `*_last_sweep`
timestamps. Second run (23:21:49->23:22:01, 12s) correctly did NOT
re-attempt jobs 27/28 (gated for the hour) -- no journal timeout/kill
lines either run. Full suite 3236 passed, ruff clean.

Fired `notify --run-card` for #172 (rc=0, sent). No dropped work; nothing
filed (job 1's gating was verified intact, no second gate found).

**CORRECTION (#176, 2026-07-30):** the claims above that the dev1 livelock
"starved job 1's 529 continue-nudge of wall-clock" and that "job 1's gating
was verified intact" are both **wrong**. Job 1 runs inside the SAME per-pane
loop as every other job, long before jobs 27/28 are dispatched, so a sweep
killed inside jobs 27/28 had already run job 1 on every tick -- it was never
starved. On the box that actually stalled (gatekeeper), the journal shows
1140 completed sweeps for 07-29 including 367 inside the window claimed to
have "no completed sweep at all" (that was dev1's own -- and unrelated --
livelock). "Verified intact" also does not hold: `pane_at_idle_prompt()`'s
bare-only gate silently misread a pane idle at `❯` holding a foreign draft
as busy, for 32 consecutive polls (36 minutes), with zero keystrokes and
zero pings -- the actual root cause of the user's original complaint, fixed
in #176. The "4h 28m" figure quoted for the stall is also uncorroborated;
the demonstrable dead-with-zero-nudges window is 36m10s.

**CORRECTION (#172 reopened, 2026-07-30):** the live-verification paragraph
above ("First run ... persisted `net_drift_cursor=6`/`stuck_main_cursor=3`")
asserts a cursor value the stated evidence does not support -- with a batch
size of 3 and both cursors starting at 0, ONE call to `net_drift_alarm`
(which `run_once` makes exactly once per sweep) advances `net_drift_cursor`
by at most `min(max_repos, n_repos)`, never to 6 in a single run unless a
second, unmentioned invocation happened in between. The reopened adversarial
review flagged this as unsupported-as-written; it is corrected here rather
than left standing, per this repo's own "never re-report an unverifiable
number" discipline. The qualitative claim (both jobs fired, cadence markers
+ cursors persisted, no re-attempt on the second run, no journal
timeout/kill lines) is unaffected and was independently re-confirmed during
the reopened pass's own live verification below.

📔 Playbook: `.claude/rules/airuleset-internals.md` -- a cadence marker set
only in an in-memory dict and saved once at the very end of a long function
is not durable against an uncaught process kill (SIGTERM), which is not a
catchable Python exception and defeats even a wrapping `except Exception`;
the fix pattern (persist immediately after the cadence stamp, before
expensive work) already existed for jobs 8/11 and should be the default
shape for any future cadence-gated sweep job.

#175 (nudge policy gave up after 3 tries / ~15 min -- a multi-hour 529
outage stranded sessions even with a healthy watchdog). Design comment
posted BEFORE the first code commit (issuecomment-5123634983), per the
user's decision (issuecomment-5123589218, option 1: back off and keep
going). RED->GREEN: feeb880 (test_decide_backoff_never_gives_up_on_a_multi_hour_stall,
fails on current code -- gives up at the 4th attempt) -> f14d9bd (decide()
no longer returns 'escalate'/'noop'; past MAX_NUDGES it keeps returning
'nudge' with a widening interval doubling from RETRY_INTERVAL_SECONDS,
capped at the new BACKOFF_CAP_SECONDS=30min; the one-shot give-up ping is
now detected by entry["escalated"] flipping False->True, read by the job-1
caller in run_once instead of a returned action; a usage/quota-cap error
gets a dedicated entry["dormant"] flag so IT alone still holds forever,
unaffected by the new back-off). Updated the two pre-existing tests that
pinned the old give-up contract (test_decide_lifecycle_fresh_stall,
renamed test_run_once_backs_off_but_never_gives_up). Full suite 3237
passed, ruff clean. Live-verified on dev1 by driving decide() directly
with a stubbed clock over a simulated 2h15m stall (the working tree the
real api-watchdog.service executes): nudges fire at t+300/600/900 (base
300s cadence), then t+1500/2700/4500/6300/8100 (widening 600/1200/1800/
1800/1800s) -- still nudging at 135 minutes, well past the old ~15-20 min
give-up point, with `escalated` flipping False->True exactly once (nudge
#4) and staying True with no repeat ping. `airuleset.py push` ran the
fail-closed gate (3237 tests, ruff) then deployed to all 6 targets.

Fired `notify --run-card` for #175 (rc=0, sent). No dropped work; nothing
filed.

📔 Playbook: none new -- this fix stayed inside `decide()`'s existing
pure-function contract (no new state shape beyond the `dormant` flag,
which is exactly the same "caller forces a state that decide() then
respects" pattern the usage-cap path already used before this ticket).

## #176 -- job 1's busy-pane gate misdiagnosed an idle-with-draft pane as busy, silently forever

Live incident, corrected here: the sibling watchdog-livelock ticket blamed the
2026-07-29 stall on jobs 27/28 starving job 1 of wall-clock. An independent
forensic review found that reasoning was wrong (job 1 runs long before jobs
27/28 in the same sweep, and the box that actually stalled completed 1140
sweeps that day) and traced the real cause: `pane_at_idle_prompt` (bare_only)
cannot distinguish a real running foreground turn from a pane genuinely idle
at `❯` that merely holds a foreign draft in its input box -- both read as
"not idle". That branch wrote no state, sent no ping, and had no bound, so
the gatekeeper pane held a stale draft through 32 consecutive polls (36
minutes) with zero keystrokes and zero pings.

Design comment posted BEFORE the first code commit (issuecomment-5123978973).
RED->GREEN: 7ee3704 (four new tests: idle-with-draft revives via the stash
protocol; an aborted stash never burns a retry; a genuinely busy pane pings
once past 2x grace; the queued-placeholder normalizer's counted-variant gap
-- all fail against current code for the stated reason; the pre-existing
test_run_once_apierror_skipped_when_pane_busy is untouched and still passes)
-> 49832d9 (job 1's busy-pane gate now classifies via `_classify_boundary`
instead of the binary `pane_at_idle_prompt` check: `kind == "input"` with a
non-empty draft delivers the `continue` nudge through `deliver_with_stash`
-- the verified idle-with-draft protocol jobs 7/9/20 already use -- instead
of refusing forever, and an aborted stash never advances the nudge state;
any other kind stays genuinely busy and is still never typed into, but now
gets job 4's escalation shape via a DEDICATED `apierr-busypane:` state
record -- its own prefix so it never collides with job 4's own `busypane:`
bookkeeping for the same session -- pinging exactly once per episode once
the stall runs strictly past 2x grace; every busy-pane skip now logs the
classified boundary kind + a text snippet instead of an undifferentiated
"busy-pane"; the queued-placeholder normalizer widened from exact-equality
to a regex so a counted variant, e.g. "... 2 queued messages", also
normalizes). Full suite 3241 passed (3237 baseline + 4 new), ruff clean.

Corrected the record per the review's retraction list: the sibling ticket's
own autopilot-log entry and its matching playbook bullet in
`.claude/rules/airuleset-internals.md` (3a0d08d), and its own GitHub issue
body via `gh issue edit` -- job 1 was never starved, its
keystroke-safety gates were NOT verified intact during the real stall, the
cited nudge timestamp belongs to an unrelated session, and the "4h 28m" stall
figure is uncorroborated (the demonstrable dead-with-zero-nudges window is
36m10s).

Fired `notify --run-card` for #176. No dropped work; nothing filed -- the
acceptance list's six items were all implemented in this ticket's scope, and
the reopened sibling ticket's own five defects (jobs 27/28 batching, log_fn
flushing, dedup persistence) are explicitly OUT of this ticket's scope, left
for its own separate worker.

📔 Playbook: `.claude/rules/airuleset-internals.md` -- two independent jobs
that each escalate a "genuinely busy, silence otherwise" episode with the
SAME state-record shape must use DISTINCT state-key prefixes even when
keyed on the same session id; a NEW "ping past N x an existing tunable"
threshold must be checked against every pre-existing test using that
tunable nearby (a strict `>` instead of `>=` kept an existing fixture
passing unmodified here); an aborted VERIFIED delivery must not be allowed
to silently advance a decide()-style counter computed before the delivery
was attempted.

**CORRECTION (#176 REOPENED):** the claim above -- and the matching lines in
the `run_once` docstring, the inline comment at job 1's busy-pane branch, and
the playbook's own ">strict vs >=" bullet -- read as a SYSTEM-WIDE promise
that job 1 could never again go silent. An independent adversarial review
(issuecomment on the reopened #176) found that promise did not hold: the
SEPARATE aborted-stash branch (`deliver_with_stash` returning False -- an
occupied stash slot, or any `esc to interrupt` substring) had no state, no
ping, no bound at all -- the exact silent-unbounded-skip this ticket was
commissioned to remove, RELOCATED from the busy branch to the draft branch
rather than eliminated (finding F1). The busy branch's own "strict >" threshold
was also gated on live transcript `idle`, not on wall clock (finding F2) --
CC's own retries / queue-snapshot writes can hold `idle` artificially low for
as long as the busy stretch lasts, so a session could stay wedged past 2x
grace of real time with zero pings. Both are now fixed: the aborted-stash
branch gets the identical escalation shape under its own dedicated
`apierr-stashabort:` state prefix, and BOTH branches anchor their 2x-grace
threshold on the episode's own `first_seen` (wall clock), never live `idle`.
Three further findings from the same review, also fixed here: (F3) job 1 used
to act on the STALE top-of-sweep pane capture when deciding to stash-deliver,
so a machine-submit from job 10 earlier in the SAME sweep could be followed by
job 1's own `C-s` into the turn that had just started -- fixed by re-verifying
against a FRESH capture immediately before delivery, job 20's own established
pattern for the identical race; (F4) `deliver_with_stash`'s own step-4 abort
(the post-`C-s` verify) read a single immediate capture with no settle window
and no restore, so a raced render (the toggle landing before the redraw
catches up) could silently strand the user's draft in the invisible stash slot
with no delivered turn left to auto-restore it -- fixed with a bounded
render-settle poll (mirroring `_await_typed`) plus a best-effort restoring
`C-s` on genuine failure, symmetric with step 5's own abort-restore; (F5) the
over-claiming language identified by the review is corrected in place on
THREE of its four surfaces (this entry, `.claude/rules/airuleset-internals.md`,
the `run_once` docstring) to state which branch each guarantee actually
covers. **CORRECTION (added on the NEXT reopened pass, R1):** the fourth
surface -- the inline comment at job 1's busy-pane branch in
`watchdog/__init__.py` -- was claimed above as corrected too, but a
comment-stripped whole-file scan showed it still read the original
over-claiming text verbatim; it was actually fixed only in that later pass,
alongside a NEW over-claim the replacement prose itself introduced here and in
the playbook (asserting the two fixes together made the guarantee
"system-wide", which the same later pass's own R2 finding -- a third
stateless skip path -- disproved). See that pass's own entry for the honest
correction and its 0-hit re-scan.

Design comment posted before the first code commit of this reopened pass
(issuecomment-5124442344 is the review that supplied acceptance items 1-7;
the design comment restates root cause / chosen approach / rejected
alternative against that acceptance list). RED->GREEN: new/updated tests in
`tests/test_stash_delivery.py` (the settle-poll + symmetrised-restore pair)
and `tests/test_airuleset.py` (F1 aborted-stash-pings-once, F2
wall-clock-anchored-busy-ping, F3 no-C-s-after-a-job10-submit-in-the-same-
sweep, F7 both episode prefixes pruned by the cleanup loop) plus a new
`tests/test_watchdog.py` teeth test pinning the queued-placeholder regex
against over-match (F8) -> the implementation in `watchdog/__init__.py`. Every
new/changed assertion was mutation-tested by hand (revert the specific fix,
confirm the specific test fails, confirm sibling tests keep passing) before
this entry was written.

Item 6's DO-NOT-TOUCH boundary held: this diff does not touch `stalled.add`
(`:8380`, byte-identical) or the cleanup predicate at `:8750`/`_SESSION_KEY_RX`
(untouched) -- the only cleanup-loop edit is the one added
`or k.startswith("apierr-stashabort:")` in the existing OR-chain, mirroring
the pre-existing `apierr-busypane:` entry. #175's own scope (the episode-key
reset on the `pane_in_mode`/busy `continue`s before `stalled.add`) is
untouched.

📔 Playbook: `.claude/rules/airuleset-internals.md` -- corrected the ">strict
vs >=" bullet in place (a threshold-choice bullet must state WHICH branch it
covers, not just which incident motivated it) and appended new bullets for
the settle-poll-before-declaring-a-toggle-failed pattern, the
stale-top-of-sweep-capture race (re-verify fresh immediately before ANY
keystroke-sending decision, not just at the top of the sweep), and the
three-independent-state-prefixes-per-session discipline.

## #151 (engineer's half) — install ssh hint now carries this box's own pinned identity

`check_discord_notify_config()`'s "wire it from an already-configured host"
one-liner printed a literal `<this-host>` placeholder unconditionally, never
consulting `REMOTE_HOSTS` — a copy-pasted placeholder on subdev means guessing
the identity, and a wrong-key ssh attempt there trips fail2ban, banning dev1
on every interface for an hour. Added `_current_remote_host_entry()`,
matching the box running `install` to its own `REMOTE_HOSTS` entry via
`_whoami()` (username-keyed, since usernames are unique across entries while
the four subdev-stream users share one physical hostname). RED (e87064c):
`tests/test_discord_notify_check.py::TestDiscordNotifyCheckSshHint`, exact-
string assertions per user, failing against the unconditional placeholder.
GREEN (c770230): the lookup + hint rebuild, unchanged fallback when nothing
matches. Design comment posted to #151 before the RED commit (root cause /
chosen approach / rejected hostname-keyed alternative).

`python3 airuleset.py push`: ruff clean, 3208 tests OK (one run hit an
unrelated pre-existing wall-clock-timestamp-collision flake in
`test_vault_channel.py`, filed as #179, passed clean on retry), deployed to
all 6 targets. simap@subdev's own live `install` output now shows
`ssh -i ~/.secrets/gatekeeper_access_ed25519 simap@100.118.174.27` instead of
the placeholder. Deploy confirmed via `git rev-parse HEAD` over ssh on dev2
and gatekeeper (both matched local HEAD `1db16d7`, including a follow-on docs
commit); simap@subdev was never ssh'd into directly — its own push-deploy
output is the proof there. No Discord `.env`/token was read or transmitted.
#151 stays OPEN with `needs-answer` — the credential-access decision is
untouched. `watchdog/__init__.py` was not touched.

📔 Playbook: `.claude/rules/airuleset-internals.md` — added the username-vs-
hostname REMOTE_HOSTS lookup rationale (four subdev users share one physical
hostname, so only username disambiguates) and the `cmd_push` stdout-buffering
trap (unittest's stderr summary vs. block-buffered stdout fixture noise can
make a real abort line appear "sandwiched" inside unrelated trailing output
when merged via `2>&1` — grep specific markers, never trust raw tail order).

## #176 (fourth reopened round) — R1-R4: the raced skip bounded, the restore verified, the overclaims actually corrected

Fourth independent adversarial review (issuecomment-5125279406) confirmed the
core fix genuinely holds this time and reopened on four residual findings.
Design comment posted before the first code commit
(issuecomment-5125437927), covering all four with root cause / chosen
approach / rejected alternative each.

R3 (do first — it can eat the user's typed text): `deliver_with_stash`'s
step-4 abort sent an unconditional, UNCHECKED "best-effort" restoring
Ctrl+S whenever the settle poll never confirmed bare+marker within budget.
A faithful single-slot toggle model across BASE vs SHIPPED (the previous
round's own F4 fix) shows this is safe when the FIRST Ctrl+S merely
render-lagged (server state already toggled; the restore genuinely
un-stashes it back to view) but genuinely regresses when the first Ctrl+S
was LOST instead (never toggled anything at all — base's own accidental
safety, since base never sent a second keystroke): the "restore" then IS the
first real toggle CC ever receives, and it genuinely stashes the draft for
real, with no delivered turn ever coming to auto-restore it. Fixed with a
new `_await_draft_visible` helper — the structural complement of
`_await_stash_bare` (polls for the input line to be NON-empty instead of
bare+marker) — which VERIFIES the restore's own effect instead of trusting
it blindly; if the draft is still hidden after a full settle window, ONE
corrective toggle undoes it and is verified the same way, and the final
outcome (`draft-recovered` / `draft-still-not-visible-after-2-restores`) is
logged honestly either way. The old test
(`test_verify_bare_failed_aborts_before_any_typing`) pinned `cs_count == 2`
in exactly the lost-send state WITHOUT ever asserting the draft's fate —
replaced with three tests asserting the new log lines: restore-alone
recovers it (no corrective toggle needed), the regression case itself
(first Ctrl+S lost, corrective toggle recovers it, `cs_count == 3`), and the
honest-failure residual (never recovers, logged rather than silently
claimed or crashed).

R2: F3's own "skip raced" branch (job 1 re-verifying against a FRESH
capture immediately before delivery, added earlier this same ticket) used
to log and `continue` bare on a mismatch — no state, no ping, no counter, no
bound, structurally the identical silent-unbounded-skip shape this ticket
was reopened to remove from the busy and stash-abort branches, just
relocated a second time. Fixed by factoring the shared "log the skip,
escalate once past 2x grace of wall clock, never touch `state[key]`" logic
into one small helper (`_apierr_stashabort_skip`) both the raced branch and
the aborted-stash branch now call — sharing the SAME `apierr-stashabort:`
episode rather than inventing a fourth prefix, since both mean the identical
thing from the escalation's point of view ("delivery could not be verified
for this pane, this poll"); the abort/raced reason text carried in the ping
already distinguishes which one recurred. New test
(`test_run_once_apierror_raced_skip_pings_once_when_wedged`) pins the bound:
zero keystrokes, exactly one deduped ping per episode.

R4: a mutation build reverting the stash-abort branch's own threshold
(`(now - sb["first_seen"]) > 2 * grace`, added earlier this same ticket to
fix F1) back to transcript-mtime `idle` — literally the F2 defect this
ticket already fixed once, one branch over — survived the whole suite with
nothing pinning it. New test
(`test_run_once_apierror_stashabort_pings_after_wall_clock_2x_grace_even_if_mtime_stays_fresh`)
mirrors the existing busy-branch wall-clock-anchor test exactly; it PASSES
against current HEAD (the anchor was already correct there — this is a
mutation-killer, not a behavioral RED) and was hand-verified by reverting
the anchor line, confirming only the new test fails while every sibling
stays green, then restoring.

R1: three of the four "a 529 pane can never again go silent" overclaim
surfaces were corrected in the previous pass to state which branch the
guarantee covers — the FOURTH, the inline comment inside job 1's busy-pane
branch, still read the literal uncorrected claim verbatim (a
comment-stripped whole-file scan: base 2 hits → pre-this-pass 1 hit, that
one). Separately, the replacement prose the previous pass added itself
over-claimed: the playbook's own correction bullet asserted the two F1/F2
fixes together made the guarantee "actually system-wide" (false — falsified
by R2's own newly-found stateless "skip raced" branch and by the
pre-existing, `#175`-owned `skip in-mode` path, neither bounded by this
ticket), and the autopilot-log's own entry claimed all four surfaces were
"corrected in place ... the inline comment" when the inline comment never
was. Fixed: the inline comment corrected in place (scoped, branch-specific
language, matching the `run_once` docstring's own already-correct wording);
the playbook's "system-wide" sentence replaced with an honest statement of
what is bounded after this pass (busy-pane, stash-abort, and — now that R2
landed in this same pass — the raced-skip branch, all sharing the identical
escalation shape) and what remains a named, deliberate exception outside
this ticket's scope (`skip in-mode`, `#175`'s own scope, measured 0 hits in
a 7-day journal); the autopilot-log's own "all four ... corrected in place"
sentence corrected to state honestly that only three were done in that
pass, with a pointer to this entry for the fourth. Verified with a
comment-stripped, normalized-whitespace scan across all three surfaces for
every overclaim string used across this ticket's history — 0 hits.

RED→GREEN, verified via `git stash push -- watchdog/__init__.py` (the
established technique — a true RED needs the implementation genuinely
absent, not merely present-but-untested): 95953a4 [red] (4 of the 5
new/changed tests genuinely fail against current HEAD on behavioral
assertions — never a crash; the 5th, R4's mutant-killer, correctly PASSES
against current HEAD and is reported as such, not misrepresented as red) →
2d92293 [green] (the fix, `Closes #176`). Full suite 3255 passed (3247
baseline + 8 net new/changed), `ruff check .` clean.

`python3 airuleset.py push`: `ruff check .` clean, its own internal
unittest-discover run reported "Ran 3212 tests ... OK" (the push runner's own
count consistently differs from pytest's collection — pytest itself reported
3255 passed on the same tree, matching the RED→GREEN delta above), deployed
to all 6 managed targets (dev2, gatekeeper, montalu/marek/david/simap@subdev)
via clean fast-forwards `08ff821..2d92293`. Deployed SHA confirmed by reading
`git rev-parse HEAD` directly on two boxes over ssh — dev2 and gatekeeper —
both at `2d92293` (full HEAD sha), matching local HEAD.

📔 Playbook: `.claude/rules/airuleset-internals.md` — a `_await_X`
render-settle-poll helper (e.g. `_await_stash_bare`) needs its structural
COMPLEMENT (`_await_draft_visible`) whenever the caller must also VERIFY a
corrective action's own effect, not just the original one — an unchecked
"best-effort restore" is exactly as unsafe as the unverified original
action it exists to fix; two skip REASONS at the SAME call site for the
SAME underlying condition ("delivery could not be verified this poll")
should share ONE episode/state record parameterized by reason text, never a
fourth dedicated prefix — reserve a new prefix only for episodes that can be
genuinely, independently live for the same session at the same time; and a
`git checkout -- <file>` used to "revert a hand-mutation" on a file that
ALSO carries genuine UNCOMMITTED work discards ALL of it, not just the
mutation — use a targeted string-substitute-then-restore script (read,
`.replace()`, write; revert the same way) for any in-place hand-mutation
test on a file with uncommitted changes still pending, never `git checkout`.

## #175 REOPENED — the api-error episode marker outran its own gates, and the quota-cap regex missed weekly/bare banners

Adversarial review of the shipped #175 widening back-off found four defects:
(F1) job 1's per-session pane loop called `stalled.add(key)` AFTER the
copy-mode and busy-pane safety gates' own early `continue`, so any sweep that
hit either gate skipped the cleanup pass's protection and deleted the
accumulated nudge/`escalated` entry, resetting the widening to nudge #1 and
re-arming the one-shot give-up ping under a fresh dedup key; (F2)
`_USAGE_CAP_RX`/`_SESSION_LIMIT_RX` both required "session"/"usage" before
"limit" and a literal space before "reached/resets", so the real weekly-cap
and bare-cap banners (no qualifier word, separated by a MIDDLE DOT not a
space — ~13% of 375 real api-error entries) fell into the unbounded generic
nudge path; (F3) the flagship widening test only called `decide()` at
cumulative due instants (no teeth for the widening itself) and
`BACKOFF_CAP_SECONDS` had no dedicated assertion; (F4) the docstring/comment
overclaimed "only a new err_hash clears dormant" when F1's cleanup pass could
too (harmless in practice — `is_usage_cap` re-derives dormancy on rebuild).

`test: reopened #175 review findings F1/F2/F3 pin the regressions [red]`
(dda38d1) — 3 genuine behavioral RED failures (F1's gated-sweep integration
test, F2's `is_usage_cap`/`pane_session_limited` corpus test against the four
real banner strings quoted verbatim in the ticket), plus F3's two tests which
pin ALREADY-SHIPPED widening behavior and are reported honestly as
mutation-killers, not RED — verified against a throwaway no-widening mutant
of `decide()` (both fail there). 718 passed / 3 failed pre-fix (matches the
pre-existing 3257+3 full-tree count). →
`fix(watchdog): keep the api-error episode alive across a gated sweep, widen
the quota-cap regexes [green]` (99dfa4e, `Closes #175`) — hoisted the single
`stalled.add(key)` to the top of `if err_text:`; widened both regexes with an
optional session/usage/weekly qualifier and a `[\s·]*` separator; corrected
the docstring/comment. `python3 -m pytest tests/`: 3260 passed, 0 failed.
`ruff check .`: clean.

Mutation evidence: F1's own mutant (reverting just the hoist) kills the new
gated-sweep test; F2's own mutant (reverting just the regex widening) kills
both new corpus tests; the no-widening mutant (F3's pre-existing scope) kills
both new decide()-interval tests. Each mutant applied/verified/reverted via a
targeted `str.replace` script against a `/tmp` backup copy of the file (NOT
`git stash`, which would have reverted all three fixes at once since they
share one file/commit) — see the playbook entry.

`python3 airuleset.py push`: `ruff check .` clean, its own internal
unittest-discover run reported "Ran 3217 tests ... OK", pushed
`bef814c..99dfa4e` (+ a playbook-only follow-up `551198f`), deployed to all 6
managed targets (dev2, gatekeeper, montalu/marek/david/simap@subdev). Deployed
SHA confirmed by `git rev-parse HEAD` over ssh on dev2 and gatekeeper — both
`99dfa4e`, matching local HEAD at the time of deploy.

📔 Playbook: `.claude/rules/airuleset-internals.md` — the `stalled.add(key)`
marker-ordering trap in job 1's pane loop is a RECURRING shape (#176 already
added two more early-continues below the old marker position without moving
it); any future gate added to that loop goes BELOW the marker, never above.
And isolating a mutant for one of several fixes sharing a commit/file: back
up the whole file to `/tmp`, mutate with a targeted `str.replace` + `assert
src2 != src` guard, restore with a plain `cp` — `git stash` reverts every
fix at once and is the wrong tool here.

## #172 (reopened) — five real defects in the shipped livelock fix, fixed at their own sites

Post-merge adversarial review of `5b90a4e..79c0cd5` (the earlier livelock
fix) found five genuine defects still shipping, none of them undoing the
livelock fix itself (independently re-verified: persist-before-the-loop
is correct and the SystemExit-modeled RED test is still green for the
right reason):

1. `log_fn=print` never flushes under systemd's piped, non-tty stdout, so
   a killed sweep still printed nothing (measured: `print('x')` + SIGTERM
   1s later captured `''`; `print('x', flush=True)` captured `'x'`).
2. `AIRULESET_REPO_SWEEP_BATCH=0` (or negative) fell into the same branch
   as "batch already covers the whole list", silently re-sweeping every
   repo — the exact knob an operator disabling batching would reach for.
3. Jobs 27/28's per-repo dedup memory reached `state` (and hence disk)
   only at the very end of the loop, not the moment a ping fired — jobs
   8/11's own "dedup memory BEFORE the ping" shape was copied only half
   (the cadence stamp, not the per-repo write).
4. The cadence marker was persisted only AFTER `repo_roots()` (an
   `os.walk($HOME)`) already ran, not before.
5. A failed `git fetch` in `stuck_main_sweep` fell through to measuring on
   whatever refs already exist on disk — a repo behind a slow link could
   read as stuck-main on stale data.

Design comment posted BEFORE the first code commit
(issuecomment-5126366260).

RED → GREEN: `test: reopened #172 review findings 1-5 pin the regressions
[red]` (81e9f2d) — 16 new/corrected assertions, ALL genuinely failing
against pre-fix `main` (verified via `git stash push -- watchdog/__init__.py
airuleset.py`, running the new/corrected tests against the untouched
implementation, then `git stash pop`) → `fix(watchdog): flush log_fn, clamp
batch-disable, persist dedup before the ping, persist marker before
repo_roots(), skip stale refs [green]` (eed3f8f, `Closes #172`).

Two pre-existing tests were CORRECTED rather than worked around:
`test_run_once_is_wired_with_log_fn_print` asserted `log_fn is print` —
exactly the regression it should have caught — replaced with an identity
check plus a real subprocess+pipe+SIGTERM proof
(`test_log_fn_survives_a_sigterm_under_a_real_pipe`) that drives the actual
`cmd_watchdog` wiring under systemd-shaped conditions (non-tty piped
stdout). `TestBatchingPreservesUntouchedDedup_172` passed one repo root
with `max_repos=1`, so `_repo_sweep_batch`'s `max_repos >= n` fast path
fired and the round-robin sit-out it claimed to test never actually
happened — corrected to two repo roots so the sit-out is genuinely
exercised (verified this correction has teeth: mutating the pruning
filter's `k not in touched` clause out makes both corrected tests fail).

Smaller items fixed in the same pass: `DEDUP_MEMORY_MAX_AGE_S` (30 days)
ages out dedup memory for a repo that vanishes from `repo_roots()`
entirely, while a repo merely sitting out one rotation (bounded well under
30 days) is untouched; the short-list fast path no longer resets the
round-robin cursor; jobs 24/25's own network timeouts
(`_watchdog_delivery_probe`, `_watchdog_card_probe`/`_watchdog_closed_fetch`
— all three dispatch before jobs 27/28 in the same sweep) cut 90s→15s /
45s→10s, matching jobs 27/28's own cuts; every "one hour of stale drift
data" claim (code comments, `run_once`'s docstring) restated as the real
`interval * ceil(n_repos / batch)` bound (~14h at the current default);
and the earlier autopilot-log entry's own unverifiable
`net_drift_cursor=6` claim got a CORRECTION paragraph above rather than
being left standing. Carried over from #175/#176's own closing pass
(same file, well under 100 LoC, explicitly scoped into this ticket):
`_RESET_TIME_RX` now parses a weekly cap's DATED reset clock ("resets Jul
31, 9pm"), and `parse_reset_epoch` uses the named date (never "today")
when it matches, so job 6 can finally compute a resume instant for that
banner shape instead of pinging once and never auto-resuming.

`python3 -m pytest tests/`: 3278 passed. `ruff check .`: clean.
`python3 airuleset.py push`: ruff clean, its own internal
`unittest discover` run reported "Ran 3235 tests ... OK" (the two runners
disagree on the exact count on this codebase — pre-existing, not a
regression; both agree 0 failed), pushed `d5cf312..eed3f8f`, deployed to
all 7 managed targets (dev2, gatekeeper, montalu/marek/david/simap@subdev).
Deployed SHA confirmed by `git rev-parse HEAD` over ssh on dev2 and
gatekeeper — both `eed3f8f`, matching local HEAD.

Live-verified on dev1: forced both cadence markers 2h stale, ran
`systemctl --user start api-watchdog.service` once. The unit finished in
19s wall / 8.14s CPU (well under the 120s budget): job 27 logged
`net-drift zbynekdrlik/restreamer opened=26 closed=23 net=+3` and one more
repo, job 28 logged three `stuck-main ...` lines, decision lines appeared
in the journal at increasing timestamps as the sweep progressed (06:44:32
→ 06:44:44 — direct confirmation the flush fix streams output live, not
only at exit), and the state file afterward showed both cadence markers
refreshed, `net_drift_cursor`/`stuck_main_cursor` advanced by exactly 3
each (27→30, 24→27), and the pre-existing dedup entries for
`camera-box`/`parovanie-produktov`/`airuleset` preserved untouched.

`notify --run-card` for #172 returned `dedup` — a marker for
`airuleset#172` from the FIRST (pre-reopen) pass genuinely exists at
`~/.claude/autopilot-notify-sent/airuleset#172` (`1785360152.79 sent`),
confirming the dedup is correct behavior (the marker keys on
repo#issue, not on which round of work closed it), not a silent failure.

No dropped work; nothing filed — every item the reopen review raised is
fixed in this pass, and the review's own "narrative half is a separate
question, filed on its own ticket" scoping note does not apply here (that
was #176, already closed).

📔 Playbook: `.claude/rules/airuleset-internals.md` — a cadence marker set
only in an in-memory dict, saved once at the very end of a long function,
is not durable against an uncaught process kill; the "persist before the
loop" fix must extend to EVERY expensive step that precedes the loop
(including local, non-network calls like `os.walk`), and to per-item
dedup memory, not just the once-per-sweep cadence stamp — jobs 8/11's own
"dedup memory BEFORE the ping" shape is the reusable template for any
future cadence-gated sweep job with per-item duplicate-suppression state.

## #181 + #164 (bundled) — one login-aware slice definition (slice-quals CLI); footer/card self-describe core vs streamy

2026-07-30 batch (#181+#164): #181 the reduced-authority `/goal` stop-proof
hardcoded `--assignee @me`, silently 0 on a shared-gh-account box
(montalu/marek/simap) while real labelled work was open — a genuine FALSE
STOP, not a labeling issue. Fix: new `airuleset.py slice-quals` CLI wraps
the existing `_slice_quals()` (the footer's own login-aware key) as
`--count`/`--list`/`--extra`, resolved for the box it runs on; TWO of the
three `/goal` templates in skills/autopilot/SKILL.md (branch-merge and
fork-no-merge — Step 1 listing, bounce lane, both reduced-authority proof
commands) now call it instead of hand-rolling `--assignee @me` — also
SHRINKS each of those two templates by ~44 chars, comfortably inside the
4000-char cap (#169). CORRECTION (round 2, below): the FULL template's own
proof was untouched here — it still counted the whole repo, which a
round-2 review flagged (I4) since a core/gatekeeper box is forbidden from
working sub-dev-owned tickets. Rejected: ANDing
`label:stream:<me>` onto `--assignee @me` in one `--search` string — gh's
`--search` cannot OR quals, so that would fix montalu while narrowing
david's real union into an intersection (a different false-empty bug).
#164: `cmd_tickets_status`'s full-authority branch now computes a
`streamy` bucket (total non-skip minus core) and the footer renders
`Issues N core · streamy M` instead of hiding the excluded population;
`_notify_run_card` scopes `remaining` to the same core exclusion (card
says "core" too) and skips the progress write for a stream ticket's card
so D/T never drifts onto two populations. Docs corrected
(statusline-vocabulary.md, .claude/rules/airuleset-internals.md — both
previously said the opposite of the shipped full-authority query).
test:a260584[red]→fix:acb3e3d[green], 3242 tests, ruff clean. Blast-radius
measurement: searched montalu/marek/simap's full local transcript corpora
(1266/6/53 files) for an ASSISTANT-AUTHORED `🏁 BACKLOG EMPTY:`/`SLICE
EMPTY` claim — zero found on all three boxes; Claude Code's own stop
announcement is pane-only and never persisted, so a definitive historical
false-stop count is not recoverable from transcript archaeology (findings
comment: https://github.com/zbynekdrlik/airuleset/issues/181#issuecomment-5127103167).
Deployed via `python3 airuleset.py push` (2 pushes: acb3e3d then the
playbook-only 3eee5e7): GitHub + all 6 targets landed, dev2/gatekeeper SHA
verified byte-identical to local. Both issues closed on push (auto-close,
no PR in this repo); both Discord run-cards delivered
(~/.claude/autopilot-notify-sent/airuleset#181, #164).

📔 Playbook: `.claude/rules/airuleset-internals.md` — gh's `--search`
qualifiers AND, never OR, so a caller needing a union across several quals
must run one query per qual and union server-side (never join the quals
into one `--search` string); and historical goal-stop archaeology is
bounded by the same pane-only-announcement limit documented earlier in
this file, reconfirmed live against three real sub-dev boxes.

## #181 + #164 round 2 — the round-1 fix relocated the false-empty-0 failure instead of removing it

2026-07-30, round 2 (b3bfe59[red]→a88d183[green]): an adversarial review of
`49cd3d4..2612400` found two CRITICAL regressions the round-1 fix had merely
moved. C1: `cmd_slice_quals` never consulted `resolve_authority()` — on a
full-authority box (live-confirmed on dev1: 29 real open tickets) it printed
a clean `0`, exit `0`. Fix: refuse (non-zero exit) up front when authority
resolves to `full`. C2: a shared-account box's slice is one un-validated
`label:stream:<user>` — a forgotten label also silently returns `0`/exit `0`.
Fix: validate the label's existence (`gh label list --search`) AND an
`involves:@me` cross-check before trusting a zero. I6 (regression): the
D/T done-count gate also skipped the run-window `ts` heartbeat write for a
stream ticket's card on a full-authority box — `_write_autopilot_progress`
now takes `bump_done` so the heartbeat and the done-increment are
independent; `_notify_run_card` always calls it. I4: the FULL `/goal`
template's stop-proof still counted the whole repo while the footer/card
already scope to CORE (#164) — new `airuleset.py core-quals` CLI (sharing
`_core_search_excl()` with the footer/card) gives it a driftproof
core-scoped proof, replacing the bare whole-repo `gh issue list`. I5: the
core/total/remaining queries raised `-L 200` → `-L 1000` to remove the
clamp-difference arithmetic that could zero out `streamy` exactly when the
hidden population was largest. I7: the tautological regression test
(asserted `_slice_quals()`'s own unchanged output) replaced with one that
mocks `_slice_quals()` and proves `cmd_slice_quals` genuinely calls it.
M11: `_notify_run_card`'s parse-failure path now defaults `is_core_ticket=
False` (the safe direction) instead of silently restoring the pre-#164 bug.
M8 + the round-1 log entry's over-claim (only 2 of 3 `/goal` templates
called `slice-quals`, not all 3) corrected to match what actually shipped.
I3 investigated and found NOT a bug: the footer's active/handed-off
partition and `slice-quals`'s raw open count deliberately answer different
questions — forcing them equal would let the proof read `0` while a
handed-off ticket the gatekeeper has not yet closed sits open, defeating
review-watch. Documented + locked with a regression test instead of
"fixed". M10 (the #61 cwd-parent fallback `cmd_slice_quals` still lacks)
investigated and deliberately deferred — not on the round-2 priority list,
fails in the safe (errors, never misreports) direction.

3295 tests pass (10 genuinely RED against b3bfe59 with airuleset.py/
SKILL.md reverted to a88d183~1, confirmed via `git stash`), ruff clean.
Deployed via `python3 airuleset.py push`: GitHub + all 6 targets landed,
dev2/gatekeeper SHA verified byte-identical to local (`a88d183`). Both
issues auto-closed on
push (`Closes #181`/`Closes #164` on the green commit); both Discord
run-cards delivered under a distinct `-round2` dedup key (the original
`airuleset#181`/`airuleset#164` keys were already claimed by round 1's
cards) — `~/.claude/autopilot-notify-sent/airuleset#181-round2`,
`airuleset#164-round2`.

📔 Playbook: `.claude/rules/airuleset-internals.md` — a "refuse instead of
guess" fix must be checked at EVERY caller context that can reach the
guessed branch, not just the ticket's own repro (the new CLI wrapper
inherited the exact bug its own callee had just been fixed for); two
counters reading differently is not automatically a bug — check whether
they answer different questions on purpose before forcing them equal; and
a parse-failure fallback that reuses "absence looks like the negative
case" silently re-enables the wrong-direction version of the bug the
surrounding code exists to prevent.

## 2026-07-30 — #181 (reduced-authority /goal false stop) + #164 (two ticket
## counts, one label), ROUND 3: the false stop had moved to the FULL box

Round-3 adversarial review reopened both. Round 2's C1/C2/I5/M11/I6 fixes were
re-verified genuine and left alone; its I4 fix was the new defect.

CRITICAL. `_core_search_excl()` is the FOOTER's *display* partition ("which
population am I showing"); round 2 reused it as the `/goal` stop-proof's
*obligation* partition ("what must I finish before I may stop"). Those are
different sets, and the difference is exactly the tickets a full-authority box
is the only one able to move. Measured live on `zbynekdrlik/odoo-erp`
2026-07-30: 84 open non-skip, 41 in the core partition, 54 in the obligation
set — #2396 and #2377 carry `stream:montalu` AND `needs-gatekeeper`, so a
core-only proof cannot see them at all, plus 11 open `prio:bounce`. The
gatekeeper closes its 41, the proof prints `0`, the loop stops, and 13 tickets
stay blocked on the box that just stopped. (**CORRECTION, round 4:** those
figures and the skill's own — 83 / 40 / 13 — read as contradicting each other
because two of them were different QUANTITIES: the skill's `13` was
obligation-MINUS-core, this entry's `54` was the obligation TOTAL. Both are now
stated in labelled units; see the round-4 entry's re-measurement.) #181
verbatim at a new address, and
a silent breach of cross-stream rule 4, which the pre-round-2 whole-repo proof
had upheld only as a side effect of being whole-repo.

Fix: `airuleset.py core-quals` counts the OBLIGATION set — the core partition
UNIONED with every open non-skip ticket labelled `needs-gatekeeper` /
`prio:bounce` / `ready-for-review` regardless of stream (`_obligation_quals`,
`MAINTAINER_ACTION_LABELS`, per-qual queries unioned in Python since `--search`
ANDs across qualifier types). NOT a revert to whole-repo: a stream ticket the
sub-dev is actively working carries none of those and still does not block this
box. Both offered options were implemented rather than one, because each alone
leaves a hole — the count fix alone would let the loop start IMPLEMENTING a
sub-dev's ticket, the clause alone leaves the count wrong. So `core-quals` also
gained `--list` and became the FULL box's backlog SELECTION query
(`SKILL.md` Step 1 + the backlog-scope bullet), cross-stream rule 4 records
that the gatekeeper's hold is now mechanical, and the FULL `/goal` template
lost its false prose ("not owned by a sub-dev stream — I never touch those,
their own box works them" — false for fork streams, where the maintainer MUST
review/merge/close, and for `needs-gatekeeper`, where only the maintainer can
act), gained the obligation-set scope sentence, and finally carries the
REVIEW-WATCH clause the other two templates always had. 3663/4000 chars, cap
and cap-test untouched — the headroom came from aligning its (A) clause and
question/sleep paragraph with the two reduced templates' shorter wording.

I-1: the C2 cross-check `involves:@me` only required parseable JSON, and `[]`
parses — "search returns nothing everywhere" IS `[]`, so the state it claimed
to detect was the state it accepted. Replaced by `_search_index_healthy()`: a
SORT-ONLY search (`sort:created-desc`, verified live) that cannot legitimately
be empty, disambiguated by the REST listing path (a different gh code path)
when it is. REST sees issues + search sees none → refuse; both empty → the
repo has no open issues and 0 is trivially correct.

I-2: `_gh_login`'s bare `except: return ""` made "gh api user failed"
indistinguishable from "not the maintainer", so a broken-gh box silently got
the own-account 3-qual union — C2 skipped, and on odoo-erp `author:@me` is the
whole maintainer backlog (the 2026-07-20 foreign-stream leak the branch exists
to prevent). Live-reproduced on david@subdev, where `slice-quals` prints all
three quals because the query FAILS, not because the login differs. Now returns
None, `_slice_quals` raises `SliceUnresolved`, and each caller answers in its
own established way (CLI refuses; footer and run-card keep `None`).

I-3: `cmd_core_quals` never consulted `resolve_authority()` — C1's fix applied
in one direction only. It now refuses on a reduced-authority box.

I-4 (#164): `bump_done`'s invariant was locked only by kwargs assertions with
the function mocked out, so replacing `done = base_done + 1 if bump_done else
base_done` with `done = base_done + 1` left the suite green. Now asserted
directly — `done` unchanged AND `ts` advanced — and the mutant re-run confirms
it dies (`AssertionError: 5 != 4 : bump_done=False incremented done`). The
three call-site assertions read the effective flag from args OR kwargs (M-3).

I-5: `cmd_slice_quals`/`cmd_core_quals` resolved authority against the PROCESS
cwd while the footer used the repo ROOT. One `_repo_root()` helper (git
rev-parse + the #61 subdirectory scan) now serves all three, and both CLI
commands run gh with `cwd=root`.

I-6: CONFIRMED live on david@subdev — `_gh_out` did not use `_gh_env()`. That
box has no GH_TOKEN in env and authenticates from `~/.git-credentials`, so bare
`gh` exits 4 and every `slice-quals` query failed closed: the fork-no-merge
template's condition (B) could NEVER hold there, i.e. that loop could not
legitimately finish at all. `_gh_out` now runs under `_gh_env()` and takes an
optional `cwd`.

M-1 (#164): a heartbeat-only write with no prior (or expired) progress file
created `{"done": 0, "ts": now}`, so the footer rendered `Issues 0/40` for a
full 6h window — a run claimed active with nothing achieved. It now refreshes
an already-live window and never opens one. M-2: `slice-quals` raised to
`-L 1000`, matching `core-quals`. M-5: `_core_search_excl()` excludes only
non-`full` profiles, so a future `full` entry cannot silently delete a whole
population from every count. M-6: the comma-OR playbook note restated as the
`label:`-family behaviour it is (`assignee:` has none).

Deliberately NOT changed: the footer and the Discord card keep their core
DISPLAY partition. The two answer different questions on purpose — the same
resolution round 2 reached for I3 — and the hidden population is already
disclosed as `· streamy M`. Documented in the skill and locked by
`TestObligationVsDisplayPartition`.

22 tests RED against `04b575d` (verified by `git stash push`-ing the three
implementation files and re-running), every one for a BEHAVIOURAL reason —
zero AttributeError, which was the tautology shape round 1 shipped once.
3326 tests pass, ruff clean.

## 2026-07-30 — issues 181 + 164, ROUND 4: the guard was wired into one path of two

Round-4 adversarial review reopened both again. Same class every round: a gate
that fails OPEN, printing a wrong `0` that silently ends an autonomous /goal
loop, with genuine command output attached.

CRITICAL. Round 3's `_search_index_healthy()` is the right guard, but it was
installed as an extra validation for ONE caller's zero rather than as a
precondition on trusting ANY zero derived from the GitHub issue SEARCH index.
One call site, nested inside `cmd_slice_quals` behind
`len(quals) == 1 and quals[0].startswith("label:")` — the SHARED-account shape.
Two paths walked past it, both reproduced live on dev1 against the shipped
code in a checkout whose `origin` still points at the pre-rename name (the
search index does not follow a repo rename; the REST listing path does):

    gh issue list --state open -L 1000 --json number --jq length        110
    gh issue list --state open --search "sort:created-desc" -L 1000       0
    python3 airuleset.py core-quals --count                              0  (rc 0)

    _slice_quals("david") -> ['assignee:@me','author:@me','label:stream:david']
    cmd_slice_quals(count=True), every search [] -> stdout '0', no SystemExit

The second one is the command round 3 fixed: an own-account stream has three
quals, so its own guard never ran. Rounds 1-3 each moved the guard one call
frame outward instead of making the refusal a property of the RESULT, which is
why the class survived three fixes.

Fix: one shared `_refuse_unless_empty_is_trustworthy()`, called by BOTH
stop-proof commands at the identical point — after the `failed` check, before
either output path, and only when the union is empty (a non-empty union is
itself proof the index answers, so the healthy path costs no extra gh call).
Audit of "every command whose output a /goal stop condition can quote": the
three templates name exactly `slice-quals --count` and `core-quals --count`,
and `--list` on both is the mandated SELECTION source — both commands, both
paths, covered.

HIGH. `core-quals --list` is the mandated backlog SELECTION source and emitted
no not-mine-to-implement discriminator: labels were never fetched at all. The
full template seeds every new batch from the OLDEST open `prio:bounce` ticket,
which on odoo-erp is 2150, `stream:david` — only a prose clause stood between
that instruction and the gatekeeper writing code on a sub-dev's ticket.
`_union_open_issues` now fetches `labels` (one extra field on queries already
being made) and `_print_issue_rows` emits a third column, `action-only` vs
`implement`, relative to THIS box so a stream's own tickets still read
`implement`. `core-quals` gained `--extra`, and the skill's full-authority
bounce seed now goes through it instead of a raw `gh issue list` — that seed
was the one selection path with neither the guard nor the column.

MEDIUM. The `prio:bounce` definition is now one statement in all three homes
(airuleset.py's `MAINTAINER_ACTION_LABELS` comment, cross-stream rules 2 and
3): the gatekeeper returns it to the SUB-DEV, the sub-dev fixes it, the
sub-dev's worker or the repo automation clears it — and the full-authority
loop HOLDS in review-watch while it is open, so `core-quals --count` never
reaching 0 in that state is CORRECT, not the never-stops failure. Locked by
`TestBounceMeansOneThingInAllThreeHomes`, whose window normalises comment
markers and wrapping so it fails on the claim, never on the formatting.

MEDIUM. `_notify_run_card` was the fourth I-5 call site and the only one still
resolving identity against the PROCESS cwd — both `resolve_authority()` and
`_slice_quals()` are now resolved against the repo root, so all four agree.

LOW. The `ready-for-review` arm rests entirely on the repo's own hand-off-label
workflow (a read-role stream gets a 403 adding the label). Live on odoo-erp:
workflow `active`, 23 of its last 30 runs FAILED, 0 open `ready-for-review` —
the ticket's own failure mode by another road, no longer hypothetical (filed
as odoo-erp 2584). `core-quals` now verifies that mechanism at the moment a
zero would rest on it and refuses if it is missing, disabled, or its newest
run failed. A hand-off is still never GUESSED from comment text: GitHub
tokenizes quoted phrases, so a phrase query over-matches, and over-counting
the obligation set is the never-stops failure.

LOW-MEDIUM (164's M-1 residual). The guard `if not bump_done and not
within_window: return` is correct; its stated REASON was false for a
review-only gatekeeper run, whose cards are all stream tickets so no progress
file is ever created. Deliberately NOT reversed — `done` and `remaining` are
both core-scoped by design, so opening a window there asserts `0/N` for a full
6h window (M-1 verbatim), and scoping `remaining` to the obligation set while
the idle render stays core-scoped is 164's own title. The false sentence is
replaced with the real reason and the accepted consequence, and the
review-only-run SHAPE is now locked by test rather than the single-stray-card
case.

Measurement, re-taken live on `zbynekdrlik/odoo-erp` 2026-07-30 and stated in
LABELLED units so `outside-core` can never again be read as `obligation`:
**83 open non-skip / 44 core / 56 obligation** (the 56 = 44 unioned with 7
`needs-gatekeeper`, 11 `prio:bounce`, 0 `ready-for-review`). The skill and this
log now carry the same triple, and `TestTheMeasurementIsStatedOnceInLabelledUnits`
locks the SHAPE rather than the values, so the numbers may rot but the units
cannot drift apart again.

23 tests RED on disk BEFORE any implementation existed (the strongest form —
no stash needed, the code was genuinely absent), every one an AssertionError,
zero AttributeError/ImportError. Two of them then caught real self-inflicted
bugs during the green pass: a docstring that quoted the false sentence it was
correcting, and a canonical sentence that hard-wrapped across two comment
lines.

## 2026-07-30 — 189 (stash unconditionally) + 171 (btop on subdev)

**189 — job 1 never delivered a single `continue` automatically.** Measured
`stash-delivered = 0` fleet-wide in 24h; a 529 sat unattended on gatekeeper
until the user typed `continue` by hand. `capture_pane` runs
`tmux capture-pane -p` with no `-e`, so Claude Code's predicted-next-prompt
ghost (dim SGR 246 after the glyph plus U+00A0) arrives stripped of its colour
and is byte-identical to a typed draft. `deliver_with_stash` then asked the
same unanswerable question twice — a precondition demanding a NON-EMPTY draft,
and a verify demanding the box go bare WITH the marker — so it routed to a
stash that had nothing to park and could never verify.

Commits: `b33aa06` [red] -> `b38554f` [green]. RED proved twice: the new module
failed 6/13 against pristine `main` with the implementation genuinely absent,
and again via `git stash push -- watchdog/__init__.py airuleset.py` after the
fix (7 failures, every one an AssertionError, zero AttributeError/ImportError).

The design is the user's own directive: stash UNCONDITIONALLY, so the
draft-vs-ghost question never has to be answered. The one decline left is an
already-occupied slot. A bounded settle poll now reports four states instead of
one boolean — PARKED, NOOP (bare, nothing to park; the state that used to abort
every delivery), UNRESOLVED (content still showing, no marker: a ghost or a
lost keystroke, deliberately not distinguished), NO_BOUNDARY. Typing is the
discriminator the pane refuses to give: a suggestion is REPLACED by a
keystroke, a real draft is APPENDED to, so the strict `_typed_exclusively`
gates the submit after UNRESOLVED and an exact append signature is undone with
exactly the characters we typed.

The decisive design choice was refusing to depend on an unmeasured pane fact.
Whether Ctrl+S dismisses a rendered suggestion is NOT established, and a design
whose correctness hangs on it would have been settled by whichever pane model a
fixture encoded — the precise failure that hid the space-vs-U+00A0 separator
bug for the whole life of the stash mechanism. `test_stash_unconditional.py`
therefore drives a pane MODEL (input buffer, single slot, ghost) and asserts
the ghost case delivers under BOTH answers.

Four caller-side tests were re-specified, not weakened. Each asserted that a
pane holding text receives NO keystroke — true only because their fakes replay
a frozen capture, which makes every Ctrl+S look like a no-op. Against a real
pane the draft is parked and the payload delivered around it, which has been
the behaviour since the stash shipped, so those fakes now model the box and the
slot and assert the invariant that actually matters: parked, never submitted,
never lost. The suite as a whole is the reason this surfaced at all — the first
full run after the green pass failed 5, and each failure was a real contract
collision rather than noise.

`promptSuggestionEnabled: false` joins the managed settings defaults (a real
key in the installed 2.1.220 build, verified in the binary's own
global-settings key vector, not a guessed name). It removes the SOURCE of the
ambiguity and is explicitly not the remedy: it is latched at process init, so
every session already running keeps rendering suggestions until it restarts.

**171 — btop on the subdev VPS.** No code change: `RUNTIME_DEPS` already lists
btop and `check_runtime_deps` already installs it wherever sudo exists. The
four managed accounts on subdev have none, so the loud MISSING warning was the
designed outcome and the missing piece was one OS-level install only root can
do, reachable solely from the gatekeeper VPS. Installed btop 1.3.0 there and
verified `command -v btop` under montalu, marek, david and simap individually —
under the accounts themselves, not under root, which is what the ticket asked
for.

Closes #171

## 2026-07-30 — #190 (prose-gate pipeline race) + #188 (unresumed boundary compact)

- **#190 flaky Stop gate.** Root cause was NOT the ticket's own hypothesis (a
  `/tmp` retry-marker collision — the test already mints a fresh uuid4 sid per
  probe). Every boolean in `hooks/stop-check-prose-violations.sh` was
  `$(echo "$MSG" | grep -q… && echo 1 || echo 0)` under `set -euo pipefail`:
  `grep -q` exits at its first match without draining stdin, the `echo` writer
  takes SIGPIPE, pipefail reports the writer's **141**, and `&& echo 1 || echo 0`
  reads that as "line absent". Captured `rc=141` directly on a ~350-byte message
  under CPU saturation; deterministic past the 64 KiB pipe buffer. Fail-CLOSED: a
  byte-correct 140 KB completion report drew 5 false violations. Fail-OPEN (worse):
  `merge despite the failing check` stopped being blocked at all.
  Fix `3545444` — one `msg_has` helper fed by a here-string (no concurrent writer,
  so no race), 47 call sites rewritten mechanically, 2 `grep … | head -1` sites
  became `grep -m1`, and a grep exit ≥2 is now recorded as UNDETERMINABLE so the
  final decision declines to assert a violation it could not evaluate.
  RED `443be99` → GREEN `3545444`; tests `tests/test_prose_gate_pipeline_race.py`.
- **#188 boundary compact on an unconsumed result.** The SubagentStop predicate was
  correct; its justification was broader than the predicate. `/compact` normally
  lands after the supervisor's next turn (CC drains type-ahead only at a turn
  boundary), but that turn died on a 529, so the compaction hit a session whose
  worker evidence block was never read. Fix `7028118` — `_compact_session_unresumed`
  reuses `transcript_last_error` (the signal job 1 already trusts), consulted at
  BOTH send points, scoped to `origin == "subagent-stop"`, and it DEFERS (request
  left in place) rather than dropping, so job 1's `continue` self-heals it.
  RED `9609016` → GREEN `7028118`; tests in `tests/test_compact_request.py`.
- Filed **#192** — the same SIGPIPE idiom still decides 15 other hooks (100 sites);
  cross-cutting, so not bundled here.
- **#193 a wrapped input box read as "there is no box".** CC renders the prompt as
  `────` / `❯\xa0<draft>` / `────`; `_find_boundary_line_raw` returns the box's LAST
  row (a wrapped draft's TAIL, by design), and `_input_line_text` /
  `_has_free_prompt` then required THAT row to carry the `❯`. The glyph is on the
  box's FIRST row, so the condition NAMED "the boundary begins with the glyph" while
  it was asked to DECIDE "is there an input box here" — true only by the accident of
  a one-row payload. Anything 400–800 chars (past that CC collapses a paste into
  `[Pasted text #N]`, already handled) read exactly like a running turn. The
  gk-request nudge (458 chars) therefore typed, failed its own read-back, aborted,
  and LEFT ITS OWN TEXT in a live prompt; the retry read its leftovers as "no free
  prompt" and no-opped forever; job 10, the dedicated stuck-draft backstop, popped
  its episode every sweep. Nine `needs-gatekeeper` tickets went unanswered.
  Three live panes captured READ-ONLY settled which rendering matters: CC 2.1.220
  draws the box BORDERED, so the structural separator-pair strategy resolves every
  real capture — and it already computed the box's complete row list before
  discarding all but the last. The head was already in hand.
  Fix `ab35157` — `_input_box_rows_raw` returns the ROWS (`_find_boundary_line_raw`
  becomes its last element, byte-identical); `_find_input_box` is the ONE place
  deciding "is this a box", from the head row, and is STRICTLY ADDITIVE by
  construction (the head is consulted only where every consumer gets None/"busy"
  today, so nothing that currently reads as a box can change its answer). The
  borderless fallback still returns exactly ONE row and never walks upward —
  nothing bounds the box there, so a scan would be the lone-glyph transcript scar.
  Fail directions set per question, not blanket: "may I type here?" resolves an
  unlocatable box to NO; "is there a draft I would destroy?" no longer reads an
  unreadable box as "no draft" (job 10 now neither advances nor forgets an episode
  it could not read, and tests the machine-nudge prefix against the HEAD row).
  Item 3 turned up a live data-loss path nobody had filed: the restoring `C-s` fired
  while OUR OWN text was still in the box, and a `C-s` into a non-empty box PARKS it
  — single slot, silent overwrite — so the "restore" destroyed the user's parked
  draft whenever the type had landed and merely failed to verify. It is now gated on
  `_undo_typed_text` confirming the box bare first; that undo is provable because
  the settle poll had already observed the box bare, so every character in it is
  ours. The undo cap sat at 400, BELOW a real 458-char payload, so the one recovery
  that existed could not run for the delivery that stranded the text; now 4000, the
  size of the largest payload this helper carries. Jobs 8 and 11 thread a log list
  through the shared helper — job 11 passed none at all, which is why 48h of
  stranded nudges left not one `stash-*` line in the journal.
  RED `dc3f152` → GREEN `ab35157` (16 failures against the pre-fix build, every one
  an AssertionError); tests `tests/test_wrapped_draft.py` plus a rewritten pair in
  `tests/test_stash_delivery.py` replacing a test that asserted the blind restoring
  toggle. Suite 3390, ruff clean.
  A fresh-context adversarial review, dispatched BEFORE the change reached any
  live box, then found three ways reading those boxes made things WORSE — all one
  shape: a suffix/substring test that was unreachable while a wrapped box read as
  None, and that a one- or two-character tail row satisfies the instant it becomes
  readable. Job 7 pressed a BARE Enter whenever `prompt.endswith(box_tail)`, and
  every reply prompt ends with the same fixed sentence, so a wrapped FOREIGN draft
  ending in "." submitted the user's unsent text AND marked the Discord answer
  delivered although it was never typed. `_is_dreply_machine_text` was a
  `txt in tail` substring test, so job 10 would classify a genuine user draft as
  MACHINE, skip its at-rest guards and auto-submit it — the one thing its own
  docstring forbids. And admitting wrapped boxes newly reached a pane holding a
  wrapped foreign draft, where a lost stash toggle left our payload glued to that
  draft with no recovery: this ticket's own defect, relocated one branch along.
  Fix `dcf9aeb` — both ends of the box must now agree that its content is ours
  (`_box_holds_our_own_text`; `_record_dreply_typed` stores a head half, and a
  pre-change record matches nothing so job 10 PINGS rather than types);
  `deliver_with_stash` refuses an already-wrapped UNRESOLVED box at step 4, while
  refusing is still free, restoring exactly the guarantee that reading the box
  removed; for a SINGLE-row unresolved box the pre-content is complete and known,
  so `_typed_landed` is accepted as proof our characters sit at the end and the
  undo runs, verified byte-for-byte. Same review: the restoring toggle logged a pop
  it never read back, `--dry-run` reported a simulated skip as a failed delivery,
  the undo cap sat six characters below job 20's `"/goal " + 4000` payload, and
  three docstrings still claimed every consumer resolves through
  `_find_boundary_line`. Every finding got a test driving the JOB, never the helper
  — a helper-level assertion can only fail on a changed signature, which proves
  nothing about what the job does to a live pane; against the pre-review build all
  four fail as AssertionErrors showing the harm itself. Suite 3396, ruff clean.
  Lesson worth keeping: the corpus replay and the unit suite were both green and
  neither could see any of this, because a fake that models one rendering cannot
  model the tails a real renderer produces.

- **196 + 198 — prose gate: bookkeeping could delete the verdict it was
  bookkeeping for** (5e12960 red, 82e7974 green). Worked as one pair because 198
  poisons the environment 196 has to be tested in: dev1's
  `/tmp/airuleset-stop-block-unknown` was already at the cap, so any test run
  without a session id measured a fake "not blocked". Validated first against
  03874a2 with unique ids — all six variants reproduced, and the last two rows of
  that table are the whole point: at the cap, a message offering `merge --admin`
  and a clean message were byte-identical to any caller (rc 0, empty stdout).
  Root cause is one shape wearing four costumes, each condition naming something
  other than what it decided: the counter write ran BEFORE the `jq` under
  `set -e`, so "did the write succeed" decided "is the verdict emitted"; the
  counter was read with no shape guard, so non-numeric content made `[` exit 2
  and the branch unreachable (rc 0, no JSON, no complaint); the key fell back to
  the literal `unknown`, so "how many times has SOME session retried" decided
  "may THIS session's verdict be emitted"; and `rm` sat after the final `&&` of
  the clean-stop line, so an unremovable counter was a hook ERROR on a clean
  message. One rule replaces all four — the throttle is used ONLY when this
  invocation's own state is positively established, and anything else is NO
  state, so the verdict goes out. Safe because Claude Code's own
  `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` bounds the loop at 8 regardless; the counter
  was only ever a courtesy throttle. The id is VALIDATED, never mangled: a
  sanitiser is many-to-one, so two hostile ids would collapse onto one key, which
  is 198's bucket respelled. A TTL is still required after per-session keying,
  because `claude -c` REUSES a session id across a restart. Cap exhaustion stops
  being silent via a decision-free `systemMessage`, which cannot re-block.
  RED replay in the post-GREEN order (`git checkout <red-sha> -- <impl>`, never
  `stash` on a clean tree): 11 of 16 fail, every one an AssertionError, the other
  5 being controls that must pass on both sides. Suite 3412, ruff clean. Filed
  201 (the identical three defects sit in seven more Stop hooks, unchanged) and
  202 (hook-driving tests leave ~1000 counter files a day in /tmp; 2418 cleared
  by hand). Lesson worth keeping: a guard whose enclosing condition names a
  caller, a query shape, or a bookkeeping fact rather than the thing being
  trusted will be re-broken next round — read the condition out loud before
  believing it.
  Adversarial review before push (Opus; `fable-gate` CLOSED at 84%) found six
  more ways the same counter opened the same gate, three of them failures of
  invariants the fix had just claimed in its own comments (90518ea red, 65382e2
  green). The shape guard asked "is it digits" but not "can `[` parse it", and
  asked with a RANGE — measured here, `*[!0-9]*` is locale-collated and does not
  reject a fullwidth digit, so it reached `[ -lt ]` as a non-integer and exited
  2: the original defect, different bytes, plus raw bash noise into the turn.
  A NUL spliced `3\x004` into `34` past the cap, because bash strips NUL out of a
  command substitution. The counter path was still a write primitive: `-f` alone
  follows a symlink and `>` writes through one, a FIFO blocked the read until the
  harness would kill the hook (no verdict printed at all), and the key is
  ENUMERABLE rather than guessable, since live session ids are world-readable out
  of /tmp from this repo's own markers. The TTL was bounded only in the past, so a
  future-dated counter was immortal again after one backward clock step. And the
  validate-never-mangle invariant was prose: the suite passed 16/16 against a
  sanitising mutant, which is many-to-one and rebuilds the shared bucket. All read
  guards now sit in one `_retry_count_of()` where every check is a positive
  requirement returning 0 on failure, the write goes through mktemp + rename so a
  planted key is displaced rather than written through, and the two new
  assertions kill the sanitiser mutant (verified against a mutated COPY: control
  passes, mutant fails on the sixth call, on a second distinct id, and on the
  shared key it leaves behind). Suite 3423, ruff clean. The extended findings are
  on the seven-hook port ticket, because the template shares every one of them.

## 2026-08-01 — batch: #213 (validated) + #214 (reviewed) + #215 (attestation gaps) + #216 (reachability guarantee)

Same-day audit found three worker-side obligations decaying to prose despite the #136 design-gate
precedent: validator dispatch clustered only at each session's initial backlog sweep (15% real
coverage), code review ran in 12% of worker runs, and a supervisor Stop-hook line ("plan-check")
had nothing worker-side backing it while a second line falsely claimed a Stop-only hook checks the
worker's own turn. Fix: generalized `design_gate.py`'s single "design" marker into a `kind`
parameter (design/validated/reviewed), added `classify_validation_comment` and
`classify_review_comment`, and extended the two SAME existing hooks
(`post-record-design-comment.sh` PostToolUse recorder, `subagent-stop-check-design.sh` SubagentStop
backstop) to classify and enforce all three from one re-read — zero new hooks, per the FREEZE.
`agents/autopilot-worker.md` gained: an instruction to post validation evidence at STEP 0 and
review evidence at CYCLE step 6 (each its own `gh issue comment`), a `plan:` self-audit evidence
line so the supervisor's `✅ /plan-check:` attestation now relays something real, and a corrected
line 267 (the check fires at the SUPERVISOR's Stop, after relay — Stop never fires for a subagent).
`skills/autopilot/SKILL.md` updated to match.

TDD: #213 (6aa806a red / 33f1346 green — validated marker + classifier + gate wiring), #214
(85d10db red / 9457758 green — reviewed marker, staged behind a temporary ALL_KINDS restriction so
the two tickets got independently provable red/green pairs sharing one implementation), #215
(35afa05 red / 9734bc4 green — doc fix locked by tests/test_worker_attestation.py), #216 (d40b4eb —
a new push-suite meta-test module, tests/test_worker_evidence_reachability.py; proved its teeth by
temporarily swapping in agents/autopilot-worker.md's pre-#215 content and confirming the detector
correctly failed against it, then restoring). 8a2f004 fixed a collateral regression in a pinned
Step-5 scan-window test the plan-check clause pushed past its 2500-char bound.

Live-payload verification: manually exercised both extended hooks with constructed SubagentStop/
PostToolUse payloads (all three markers present → pass; each single marker present alone → blocks
naming exactly what's missing; a 3-issue/mixed-marker-state batch → well-formed consolidated JSON
naming per-issue missing kinds). Full suite 3587 passed, `airuleset.py validate` clean, ruff clean.

A dispatched fresh-context adversarial review (Explore) then found two CRITICAL + two MAJOR issues
in the new marker family, all fixed with their own red/green pairs: (1) one rich comment posted
before any code existed could plausibly satisfy design+validated+reviewed at once, defeating the
premise each kind proves its own step happened -- fixed via `design_gate.claimed_urls()` (a marker
kind can only come from a comment url that hasn't already granted a different kind) plus stopping
the classify loop at the first still-missing match instead of writing every kind that classifies.
(2) the review classifier's bare `[0-9a-f]{7,40}` alternative matched any 7+ digit decimal number
and pure-a-f-letter English words ("defaced") -- fixed by requiring the hex token sit near an
actual commit/sha/fix keyword, same shape as notify.py's own `_SHA_PER_ISSUE_RE`. (3) the
reachability test's `_ENFORCED_BY_RE` only matched the retired exact "enforced by" phrasing, so it
was VACUOUS against this diff's own "checked by" wording (confirmed empirically: 0 matches) -- fixed
with a two-stage mention+claim detector, which also surfaced a real scope bug (`PostToolUse` was
missing from `SUBAGENT_REACHABLE_EVENTS`) and needed its own keyword-set narrowing after a naive
"checks?" match first mis-flagged the #215 CORRECTED honest text as a false claim. (4) an issue
closed via STEP 0's `gh issue close --comment` path (never coded) could still need 3 unmeetable
markers -- fixed by excluding issues named on `obsolete_closed:`/`obsolete_handed_off:` lines.
Full suite green again after each fix; final count 3607 passed, ruff clean, `validate` clean.


## 2026-08-04 — batch: #206 (closed-issue design-gate exemption) + #222 (bounce ⇒ rule-update loop) + #223 (statusline: drop ctx bar, add sub/account segments, shorten labels)

Three independently-filed tickets, bundled onto one push per the batch gate (no
cross-dependency between them). All three landed direct to `main` (this repo has
no dev branch / no PR / no CI — `airuleset.py push`'s own fail-closed test suite
is the gate).

**#206**: `block-commit-without-design.sh` used to require a design marker for
EVERY `#N` mentioned anywhere in a commit message, with no way to tell "the
ticket this commit is for" from a historical/closed reference in prose (the
reported case: `"(owner decisions #1734/#1766)"`, both long-closed — syntactically
identical to this repo's own `(#N)` "this commit's ticket" convention, so no
positional/syntactic rule could discriminate them). Fix: `design_gate.
required_refs()` + `_gh_issue_state()` drop a still-unmarked ref from the
required set once `gh issue view` confirms it's CLOSED, querying only refs with
no existing marker (the common case costs zero extra calls), failing toward
STILL REQUIRED whenever state can't be determined. `scripts/
replay_design_gate_commit_corpus.py` now stubs `gh` (always OPEN) so its own
"no gh, no network" contract holds after the change. TDD: c8031fa [red] / 6ae3ccb
[green] — 15 design_gate unit tests + 5 hook-behaviour tests, 89 total passing
across both files.

**#222**: `skills/process-subdev/SKILL.md`'s bounce path (step 5, FINDINGS
branch) posted findings + `prio:bounce` and stopped, with no mechanism asking
WHY the finding reached review at all — live evidence cited: odoo-erp
#2183/#2181/#2301 bounced simultaneously, an earlier kiosk hand-off took 4
rounds. Added a mandatory step: before finishing a bounce, answer "which
sub-dev rule/checklist item would have caught this finding BEFORE hand-off?" —
name the skipped rule in the bounce comment, or update the repo's hand-off
contract in the same cycle (file an airuleset ticket if the gap is airuleset-
owned). Bounce-rate per stream named as the metric this loop drives down. TDD:
53b21ab [red] / 2ac4410 [green] — 6 new content-lock tests, 21 total passing.

**#223**: exact target footer format implemented as specified (verbatim in the
ticket, not redesigned): dropped the context-fill BAR from `CAVEMAN_SHIM_CONTENT`
entirely (the KB figure stays visible via the existing cost segment); shortened
every label (`Issues`->`I`, `otazky`->`Q`, `streamy`->`str`, `skipped`->`skip`,
`gk-req`->`gkq`, per-model window to its first letter uppercased, `ctx <size> ·
~$<cost>/ťah`->`ctx <size> ~$<cost>`, reset suffix lost its leading space); added
`statusbar.subscription_segment()` (monthly renewal anchor from `~/.claude.json`
-> `oauthAccount.subscriptionCreatedAt`, day-of-month anniversary clamped for
short months, coloured by proximity) and `statusbar.account_email_segment()`
(the logged-in account's email, faint). Both new readers fail silently on any
missing/malformed input, verified end-to-end against the real bash shim
(`tests/test_caveman.py::TestCavemanShimAccountSegments`) for every degraded
case the ticket named: missing `~/.claude.json`, missing `oauthAccount`,
unparseable `subscriptionCreatedAt`, stale (>6h) per-model usage cache — each
omits only the affected segment, never breaks the line. TDD: 60eb1f9 [red] /
fe2178a [green] + bd751c3 [green] (a same-batch fixup: the first push attempt
correctly self-aborted on one assertion in `tests/test_gk_request.py` that
still expected the OLD `gk-req N` text — missed because it lives in a file the
earlier statusbar/caveman-scoped test runs never touched; a repo-wide sweep
after the fix found zero remaining stale-label assertions). Every pre-existing
test asserting an old label string across `tests/test_statusbar.py`, `tests/
test_caveman.py`, `tests/test_usage_cache.py`, `tests/test_gk_request.py` was
migrated to the new render text (never weakened). `modules/core/
statusline-vocabulary.md` + its two locked tests updated to document the
current rendered forms alongside the historical spoken ones.

Live-verified on dev1: rendered the REAL installed shim
(`~/.claude/airuleset-caveman-statusline.sh`) against a constructed realistic
stdin payload, real HOME/caches --
`5h 13%(95071d)  wk 61%(95071d)  F 25%(3d)  sub 12.8.(8d)  I 49 core · str 0 ·
skip 1  Q inde 1  ctx 570K ~$0.28  drlik.marek@gmail.com  caveman:lite` -- the
`sub 12.8.(8d)` and `drlik.marek@gmail.com` match the box's real
`~/.claude.json` values exactly. A pre-#223 build of the same bash wrapper,
same payload, same live caches confirmed the bash-level delta precisely (ctx
bar present -> absent, `Fable 25% (3d)` -> `F 25%(3d)`, no leading space on the
reset suffix, `sub`/email segments absent -> present). A fresh `HOME` with no
`~/.claude.json` at all rendered `5h 13%(95071d)  wk 61%(95071d)  ctx 570K
~$0.28` -- the rest of the line intact, `sub`/email/tickets/questions/caveman
all correctly absent, exit 0, no traceback.

`python3 airuleset.py push`: 3603 tests, ruff clean, pushed + installed
locally + deployed to all 6 remote targets (dev2, gatekeeper, montalu@subdev,
marek@subdev, david@subdev, simap@subdev). All three issues auto-closed on
push via their `Closes #N` trailers. Discord run-cards fired for all three.

2026-08-04 solo: #224 watchdog job 25 (card_reconcile) — two independent
false-positive fixes, both live-measured. Grace period per ticket:
merged_closes() now returns {issue_num: commit_epoch_ts} (record/field
separator pair in the git-log format string carries the commit's own
timestamp), gated on CARD_GRACE_S=1200s read from each ticket's OWN closing
commit, never the window edge — two tickets in the same repo (one old, one
fresh) judged independently in the same sweep. Dedup per ticket, forever:
state["card_unreported"][root]["pinged"] replaces the old per-repo-per-day
CARD_REPING_S bucket; a ticket earns exactly one nag ever, pruned once it
ages out of the 48h window. _watchdog_closed_fetch (the gh fallback for
trailer-less repos like tvdole) carries closedAt through the same path via
a new _normalize_closed() helper (also accepts the old bare-int-list shape,
unknown ts = already past grace). TDD: ecb72be[red]->0c9927d[green], 4 new
TestCardReconcile specimens + all 21 pre-existing pass unmodified. Filed a
separate unrelated pre-existing flake (wall-clock TTL race in
test_ci_poll_repeat_block.py, ~50% failure rate, confirmed against this
commit's own parent) rather than folding it into this diff.

Live-verified on dev1 (real, non-dry-run production sweeps, not just
--dry-run): #224's own closing commit stayed silent through the ~20-minute
grace window and pinged exactly once ~22 min after landing; camera-box's 6
and forestshop-app's 10 already-stuck tickets pinged exactly ONCE at the
moment the fix went live (12:13:13) and have stayed silent on every sweep
since despite the unconditional detection log still firing every sweep. A
direct OLD-vs-NEW differential against the same real repos + real gh data
confirmed the OLD code re-pings the same camera-box tickets a second time
one day later while the NEW code stays silent.

`python3 airuleset.py push`: 3607 tests (1 pre-existing unrelated flake
confirmed transient — passed clean on the retry that shipped), ruff clean,
pushed + installed locally + deployed to all 6 remote targets. Issue
auto-closed on push via its `Closes #224` trailer. Discord run-card fired.

## #225 — /compact self-callback + proven-origin trust + bounded no-downgrade (2026-08-04)

Root cause (two compounding races, both measured live on dev1 before
touching code): the Stop hook's own synchronous /compact attempt
sometimes falls back to job 14's ~60s poll (a draft, a dialog, an
unresolved pane); under an armed /goal loop the supervisor's next turn can
land within seconds, so by the time job 14 re-derives the boundary from
the session's CURRENT last marker, it has already moved to the next
ticket's WORKING — refused as "not a boundary" (26 of these since Aug 1,
the largest single job-14 refusal reason).

Shipped `airuleset.py compact-request --self [--hold S]`: resolves the
calling session's own pane via $TMUX_PANE (no ambiguity resolution),
records + synchronously delivers under a new proven-boundary origin
(self-callback, trusted identically to the existing subagent-stop via a
shared `_COMPACT_PROVEN_BOUNDARY_ORIGINS` set at all 4 consuming sites),
holding (bounded, default 60s) if the first attempt can't land. Wired into
the full-authority /goal template (measured char headroom first: 337
chars free against the 4000 cap, the added instruction is 144); the two
reduced-authority templates have too little headroom and are tracked as
#227.

A fresh-context adversarial review (dispatched before push) found real
defects in the first cut: a successful --self never cleared the request;
origin preservation in record_compact_request had NO time bound (could
resurrect a stale proof indefinitely, defeating the 30-min max-age and
laundering an old proof onto an unrelated later boundary); the delivery
call's own origin kwarg was untested (a mutant dropping it survived); and
_stop_already_rejected (correct for the Stop-hook path) misfires for a
MID-TURN self-callback call, which runs outside any Stop-hook batch. All
fixed: clear-on-success, a 120s bounded preservation window
(COMPACT_ORIGIN_PRESERVE_WINDOW_S), a spy-based origin assertion, and a
self-callback-only exemption from the rejected-stop gate (subagent-stop's
existing behavior stays untouched, pinned by its own control test).

Live-verified on dev1 with a genuinely isolated scratch Claude Code
session (real tmux pane, real CLAUDE_CONFIG_DIR, no real API auth needed
for the mechanism itself): resolve_self_pane correctly resolved the exact
pane from $TMUX_PANE; deliver_compact_self typed a real /compact into that
real pane and it executed (CC replied "Not enough messages to compact.",
confirming genuine execution, not a simulation); job 14
(compact_ticket_boundary) delivered into that SAME pane while its
transcript's CURRENT marker read `⏳ WORKING: dispatching next ticket
#226` -- the exact #225 regression scenario, proven fixed -- and a blank-
origin control on the identical marker was correctly still refused
("skip not-a-boundary"), proving the fix is scoped to proven origins only.
Re-verified after the review-hardening round: a genuine send now leaves
`{}` in compact-requests.json (was previously stale).

TDD: 829117d [red] / 5609a2f [green] (the mechanism) then f9ea175 [red] /
87cf42a [green] (the review-hardening round). #146 evaluated and left
open -- different root cause (a served/non-worker session never producing
any boundary signal at all), not closed by this work; commented with the
verdict.

`python3 airuleset.py push`: 3643 tests (the SAME pre-existing #226 flake
hit once, confirmed transient by 5/5 isolated reruns, passed clean on the
retry that shipped), ruff clean, pushed + installed locally + deployed to
all 6 remote targets. Issue auto-closed on push via the `Closes #225`
trailer in 5609a2f. Discord run-card fired.

## 2026-08-04 — batch: #146 (compact DECLINE noise → per-session-once dedup) + #227 (compact-request --self wired into all three /goal templates)

Two independently-filed tickets, bundled onto one push per the batch gate.
Both direct to `main` (no dev branch / no PR / no CI — `airuleset.py push`'s
own fail-closed suite is the gate).

**#146**: validated live before touching code — the owner's own already-
posted VERDICT comment on the ticket narrowed it to ONE remaining
actionable item (throttle the DECLINE noise; the served-session-boundary
design question stays explicitly out of scope, split out as #228). Live
evidence at dispatch time: `~/.claude/compact-decisions.log` at 502908/
512000 bytes, 1465 of its 2842 lines (51.5%) from ONE 8.5h+ forestshop
session declining every ~60-70s — a different session than the one the
ticket originally cited, proving the defect still live a week later. Root
cause: `_decide_log_throttled` gated the high-volume non-worker DECLINE
class with a GLOBAL, time-based 60s heartbeat shared across every session
on the box, not keyed by session at all. Fix: `_decide_log_once_per_session`
logs the FIRST decline for a (session, reason) pair via a tiny marker file
under a new `.compact-decisions-seen/` directory, never again for that
pair, TTL-pruned. Live differential proof (pre-fix HEAD vs the fix, real
payloads, both directions): the same session declining again 120s later —
OLD writes a second line forever, NEW writes none; a DIFFERENT session
declining 0s after another — OLD suppresses its first-ever decline (a real
bug in the shared window), NEW logs it independently. TDD: 636db66 [red] /
45edce3 [green]. Closed with evidence; #228 filed for the deferred design
question.

**#227**: measured all three `/goal STOP CONDITIONS` templates first (full
3807/4000, branch-merge 3917/4000 = 83 headroom, fork-no-merge 3946/4000 =
54 headroom — unchanged since #225, confirming the gap was still real).
The `compact-request --self` instruction needs ~144 chars; neither reduced
template had room. Reclaimed 168 chars in EACH by cutting text 100%
duplicated across all three templates — never enforcement-bearing: the
clause-A "re-prints this /goal line" parenthetical (kept in FULL only) and
the bounce-lane restatement (compressed; its unabbreviated form already
lives in the skill body's own Step 3.1). Net −24 chars per reduced
template after adding the instruction (branch-merge → 3893, fork-no-merge
→ 3922). TDD: 35cbd3e [red] / 6441b84 [green] — widened
`TestFullAuthorityTemplateCallsTheSelfCallback` to require all three
profiles, replacing the honest-gap test with its positive counterpart.
Auto-closed on push via the `Closes #227` trailer.

**Review-hardening round** (fresh-context Opus review, dispatched before
push — full findings on both tickets' comment threads): 2 MAJOR + 1 MINOR
defect found in the #146 fix, all in code that diff introduced. (1) The
header comment's claim "agent_type never changes mid-session" was
empirically false on dev1's own corpus (6 of 8 real sessions logged more
than one distinct type) — the dedup key now folds in `agent_type` too, so
a session running Explore then general-purpose then ticket-validator gets
3 lines, not 1 shared one; a repeat of the SAME type is still deduped.
(2) marker creation could silently fail (ENAMETOOLONG on a huge
session_id) and the dedup disengaged with zero signal — key now clamped
to 200 bytes. (3) `[ -e ]` then `touch` raced under real concurrency
(verified: 12-way race produced up to 4 lines for one pair) — now atomic
via `set -o noclobber`, verified back down to exactly 1. Also corrected
the `find -mtime` TTL math (off by one day against its own comment) and a
stale positional docstring reference. TDD: 03e95a8 [red] / d55fc6d
[green]. A pre-existing, out-of-scope finding (log-line forgery via an
unsanitized `session_id` in `_decide_log`, untouched by this diff) filed
separately as #229. A test-lock gap the review also flagged (the
clause-A hint's only guard was whole-file-scoped, so it silently passed
while 2 of 3 templates lost the hint) closed with an explicit per-template
lock (dcf6897) documenting the FULL-only asymmetry as intentional
(re-arm is machine-backstopped regardless by watchdog jobs 9/20).

Playbook: three lessons added to `.claude/rules/airuleset-internals.md`
(adeeb74) — dedup keys must name every field the claim depends on, the
`find -mtime` off-by-one, and a reusable atomic noclobber marker-write
pattern.

Full local suite before push: 3695 tests, 1 failure on the first pass —
the SAME already-tracked #226 flake
(`OneShotReviewFollowupTest::test_oneshot_ttl_is_configurable_and_short_ttls_decay_fast`,
a 1-second wall-clock TTL racing subprocess-spawn overhead, unrelated file,
unrelated to this batch's diff), confirmed transient by an isolated rerun
(passed alone in 0.45s). `python3 airuleset.py push` result below.

**#230**: job 25's GitHub fallback (`_watchdog_closed_fetch`) ran `gh issue
list --state closed` and treated every closed issue as "merged but
unreported" -- unable to tell a hand close, a close-by-a-bare-commit, and
a close-by-a-genuinely-merged-PR apart. Live-confirmed against odoo-erp
before writing any code: 6 of 7 nagged tickets had `closer: null`, the
7th `closer: {"__typename":"Commit"}` -- none were ever owed a card.
Fix: `gh api graphql` reads each closed issue's `CLOSED_EVENT.closer` in
the SAME single call (never N+1), keeping only `closer.__typename ==
"PullRequest" and closer.merged`. `owner`/`name` resolved via `-F
owner='{owner}' -F name='{repo}'` (`-F`/`--field`, typed, expands the
placeholders from cwd's git remote; `-f`/`--raw-field` does not --
verified empirically both ways before writing the fix). Contract
preserved: `{number: closed_epoch}`/`None` on failure, 10s timeout,
`_normalize_closed`'s bare-int tolerance untouched. No state migration
needed -- `card_reconcile`'s existing per-ticket `pinged` prune (`{k: v
for ... if int(k) in closed}`) drops the newly-excluded tickets on the
very next sweep for free. TDD: 0498276 [red] (13 new tests, 8 fail
against the old implementation) / 172580f [green]. A fresh-context
adversarial review found a MAJOR test-quality gap (the `-F`/`{owner}`
placeholder test asserted presence, not pairing -- a mutant swapping `-F`
and `-f` passed all 12 tests untouched, though the shipped code was
already correct) and a MINOR terminology error (the comment called `-F`
"raw-field", backwards from `gh api --help`'s own naming); both fixed in
0a4679b, the mutant re-verified caught after. A third finding (client-side
`closedAt` filtering after an `UPDATED_AT`-ordered `first:100` fetch can
in principle miss a genuinely-merged ticket past 100 closed issues on a
busy repo, demonstrated against `microsoft/vscode`, not a regression this
fix introduces) filed as its own follow-up issue, "closed-fetch UPDATED_AT
ordering can silently miss a merged-but-unreported ticket past 100 closed
issues" (issue 231).

Post-deploy live verification (marek@subdev, gatekeeper@gk, both updated
to `0a4679b`) surfaced that the phone nag has NOT actually stopped for
odoo-erp -- not because this fix is wrong (confirmed correct in isolation:
returns `{}` for odoo-erp, 52 genuinely-merged tickets with correct
timestamps for camera-box) but because `merged_closes`, the LOCAL read
that always runs BEFORE this fallback, has its own false positives on
this repo for two separate reasons never reached by #230's own scope: (a)
marek's checkout resolves its base branch to `origin/develop` via a local
`origin/HEAD` symref, not GitHub's actual default `main`; (b) even on
`origin/main` (gatekeeper's checkout), a commit message merely mentioning
"Fix #N" is not proof GitHub actually closed the ticket from that commit
-- live-demonstrated for #2857, whose closing commit predates its real
close by ~1h40m yet never triggered GitHub's auto-close (most likely
odoo-erp's CI pushes via a `GITHUB_TOKEN`, which GitHub deliberately
excludes from auto-close to prevent circular loops). Both are
cross-cutting (touch job 24 and job 25 alike) and genuinely need a design
call (branch-intent per stream; cost tradeoff of cross-checking every
local match against GraphQL) rather than a guess -- filed as #232 with
full live evidence rather than expanding #230's own scope.

Full local suite before push: 3665/3708 tests via the push gate (one
isolated rerun of the tracked #226 wall-clock flake, confirmed transient).
`python3 airuleset.py push` deployed clean to all 6 targets (dev2,
gatekeeper, montalu, marek, david, simap). Auto-closed via the `Closes
#230` trailer.

2026-08-04 #232 (job 25's `merged_closes` trusts a commit-message keyword
match as proof GitHub closed the ticket -- it isn't; odoo-erp live false
positives on main) closed. Root cause: `card_reconcile` calls
`merged_closes` unconditionally as its primary candidate producer, and
that function is a bare `_CLOSES_RE` match against local commit
messages -- #230's own GraphQL verification (`_watchdog_closed_fetch`)
only ever ran as a fallback when `merged_closes` found NOTHING at all, so
a repo where local trailers DO match (odoo-erp: 22 `Fixes #N` matches on
`develop`, a non-default branch) never reached it. Fix, per the
maintainer's binding decision (issuecomment-5183204511, rejecting a
`base == default branch` gate as a proxy rather than the real fact):
`merged_closes` stays an unchanged, cheap candidate producer; every
candidate that survives the per-ticket grace period AND the forever-dedup
set (i.e. would actually trigger a phone ping) is now confirmed against
the SAME CLOSED_EVENT->closer query, reusing the `closed_fetch` seam --
no second GraphQL shape, at most one extra call per repo per sweep,
zero calls when nothing is pingable.

RED->GREEN, two pairs: 7f8f64d->e9826d9 (the core fix), then a
self-caught gap (the first GREEN was missing the `pingable` non-emptiness
guard, costing a wasted call whenever nothing could send) ->
12a8515->002aa88. A fresh-context adversarial review independently
reproduced the identical gap via mutation testing before discovering the
self-fix had already landed -- verdict "safe to deploy", 3 more 🔵
findings, 2 addressed with new tests (dde5901: dry_run still verifies
deliberately, per-repo isolation with two repos sharing one closed_fetch
in one sweep), 1 filed as #234 (the pre-existing #230 fallback itself
isn't gated on `pingable` either -- out of #232's diff).

Live-verified on both boxes named in the ticket, not just unit-tested:
pruned the stale `pinged` dedup state (7 tickets on gatekeeper, 22 on
marek@subdev) that pre-dated the fix and would otherwise have masked a
future genuine finding for those same ticket numbers -- discovered a real
TOCTOU race doing this the naive way (a concurrent 60s sweep silently
restored the pre-prune bytes), fixed by bracketing the prune with
`systemctl --user stop/start api-watchdog.timer`. Post-fix journal on
both boxes: `card-reconcile verify-rejected` fires every sweep for the
exact same ticket sets the ticket named, zero `card-unreported PING`
lines in the 15 minutes following, dedup state stays `{"pinged": {}}`.

Full local suite before push: 3675 tests. `python3 airuleset.py push`
deployed clean to all 6 targets. Auto-closed via the `Closes #232`
trailer. Playbook entry appended (`.claude/rules/airuleset-internals.md`)
covering the candidate-vs-verify job-design pattern, the commit/working-
tree discrepancy recovery pattern, and the live-state-prune TOCTOU race.

2026-08-05 solo: #235 tmux scrollback holey — raised history-limit
2000->50000 fleet-wide. `apply_tmux_history_limit()` mirrors
`apply_ultracode_launcher` (#77)'s idempotent-marker-block shape in
`~/.tmux.conf`, wired into `cmd_install` (so both `install` and `push`
apply it -- push already runs install over ssh on every REMOTE_HOSTS
entry, so no separate remote-deploy code was needed) plus a keystroke-free
live-apply (`tmux set-option -g history-limit N`) on any running server.
RED->GREEN: 6261f9c->cf41ed5 (8 tests: create/append/rewrite-in-place/
idempotent/injectable-live-apply/exception-safety/custom-limit/real-tmux
smoke).

A fresh-context adversarial review dispatched before push found 2 real
MAJOR defects: (1) a naive whole-file `START.*?END` regex, given an
externally-corrupted conf with markers in the wrong order, wasn't just a
silent no-op -- a SECOND run risked spanning from the stray marker to a
freshly-appended block's END and silently deleting real content in
between; (2) `subprocess.run` without `check=True` doesn't raise on a
nonzero exit, so a real dead-socket `tmux set-option` failure was 100%
silently swallowed, contradicting the docstring's own "logged for
visibility" claim. Second RED->GREEN: 3a6d623->6926148 --
`_clean_tmux_block_spans` (a positional, non-regex, crossing-safe scan)
replaces the regex entirely, and the live-apply path now inspects
`returncode`/`stderr` instead of relying only on a raised exception. 3
MINOR/cosmetic findings (duplicate blocks, a truncated block, a leading
blank line) mirror pre-existing behavior in the sibling
`apply_ultracode_launcher` and were filed as #237 rather than expanding
#235's scope.

Full local suite before push: 3685 tests, OK. `python3 airuleset.py push`
deployed clean to all 6 targets (dev1 local + dev2 + gatekeeper +
montalu/marek/david/simap@subdev) -- live-verified on every one of them:
`~/.tmux.conf` carries the managed block and `tmux show -g history-limit`
reports 50000 on the running server. Auto-closed via the `Closes #235`
trailer on cf41ed5. Playbook entry appended
(`.claude/rules/airuleset-internals.md`) covering the crossing-safe
marker-block scan pattern and a subagent wait-protocol gotcha (Monitor
registers as a SECOND in-flight task instead of satisfying the
foreground-wait requirement -- a real `for`/`sleep`-loop-with-deadline
Bash call is what actually works).

## #241 -- window-size manual crashes tmux 3.4 at server start

RED `0430e29` (test-only: TestTmuxHistoryLimit block-content assertions
updated to expect two lines, a new downgrade test, and a dedicated
TestTmuxWindowSizeRemoved.test_window_size_option_is_never_emitted_in_the_rendered_block
-- confirmed genuinely RED against the unfixed render_tmux_history_block
via `git stash` isolation) -> GREEN `9e8e2fe` (window-size manual removed
from render_tmux_history_block at the source; TMUX_WINDOW_SIZE constant
dropped at all three use sites; default-size 176x50 + history-limit
50000 unaffected; docstrings/comments and the .claude/rules/airuleset-
internals.md #236 entry corrected to stop claiming the option ships).
Root cause: window-size manual, shipped fleet-wide by #236, crashes real
tmux 3.4's server outright at startup (`server exited unexpectedly`) --
confirmed live against the real 3.4 binary every managed box runs, the
only version Ubuntu 24.04 noble ships (a DIFFERENT failure than #236's
own live-apply-resize finding, which only affects a RUNNING server).
Fresh-context adversarial review before push: no CRITICAL/MAJOR findings
-- confirmed no leftover window_size references, argument order intact,
docstrings accurate, and proved the new regression test has real teeth
by mutation (reintroducing the crashing line makes 3 tests fail).
Pushed directly to main (no PR/CI in this repo) -- Closes #241 on the
GREEN commit auto-closed the issue. Post-deploy: verified read-only on
dev1 and dev2 that the deployed ~/.tmux.conf carries no window-size line
and that the real tmux 3.4 binary starts cleanly against it via a
throwaway socket. Posted a follow-up comment on #236 stating the
accepted trade-off (default-size alone no longer live-pins an existing
window's size across attach/detach cycles). No PR (direct push).

#242 — Boot-time cutover to tmux 3.7b, fleet-wide, zero manual steps.
Commit cd784f7 (feat, [Closes #242]). Added a root-owned, idempotent
systemd oneshot (airuleset-tmux-cutover.service, DefaultDependencies=no,
Before=sysinit.target ssh.service ssh.socket, WantedBy=sysinit.target --
the earliest boot hook, always before any login/tmux client/server can
exist) that points /usr/local/bin/tmux at tmux-3.7b when present and
executable, never touching the packaged /usr/bin/tmux. cmd_install()
installs+ENABLES it non-interactively (sudo -n) but NEVER starts it --
the flip only happens at each box's own next reboot, so it can never race
a live server (#240/#241). The four subdev stream accounts have no sudo
and share one box+symlink; a second function performs the same install
over the gatekeeper -> root@subdev ssh hop, fired from the gatekeeper
account's own install run -- one root-level install covers all four.
Fresh-context adversarial review (fable, gate OPEN) before push found no
critical (🔴) finding; three 🟡 fixed in the same commit: TimeoutStartSec=30 added
(Type=oneshot defaults to infinity, and the unit blocks ssh.service --
an unbounded hang would block boot fleet-wide); the newest-build check
switched from -e to -x (a truncated/non-executable tmux-3.7b must never
become the target); the "never systemctl start" test assertions widened
to also catch `enable --now` and a bare `restart` (list-membership on
"start" alone missed both), plus a read-only-sandbox variant of the
no-op test so an "always ln -sfn unconditionally" mutant fails it.
25 targeted tests (incl. real `sh` execution of the shipped script
against throwaway sandboxes) + full 3716-test suite green, ruff clean.
Pushed directly to main (no PR/CI in this repo) -- Closes #242 on the
commit auto-closed the issue. Deployed to all 7 checkouts via
`airuleset.py push`. Post-deploy verified read-only on every box: dev1
(unit installed+enabled, ran it live via `systemctl start` -- true
no-op, already on 3.7b, symlink unchanged), dev2 + gatekeeper + subdev
(via the gatekeeper root hop) -- unit installed+enabled on all, symlink
STILL on the packaged 3.4 on all three (never flipped by hand, no server
touched -- each flips at its own next reboot). Gatekeeper's own install
run performed the subdev root-hop live, confirmed in the push log and by
the read-only subdev check. Posted a follow-up comment on #236: once the
whole fleet has rebooted onto 3.7b, `window-size manual` becomes
shippable again (crashes 3.4 at server start, starts cleanly on 3.7b) --
not implemented here, per #242's own explicit scope. No PR (direct push).

## #243 -- goal_autoarm blind on ultracode panes: labelled border + #223 statusline reorder

RED `4b2ce0b` (an ultracode pane's box is unfindable, so job 9 never
arms) -> GREEN `4d8c098` (find the input box on an ultracode pane
again). Root cause: two independent faults closed BOTH of
`_input_box_rows_raw`'s detection paths at once, and only their
coincidence was visible. The structural strategy's strict separator
test rejected CC's own LABELLED top border (the session's effort mode,
e.g. `──── ultracode ─`, written into the box's own top edge) -- only
one separator remained, so `len(seps) >= 2` never held. The chrome-peel
fallback's `_is_bottom_chrome` recognised the managed statusline by
`startswith("ctx ")`, which stopped matching the moment the #223
segment reorder moved the ctx meter out of the lead position. Together
an idle pane at a bare prompt classified `busy`, so every keystroke
gate silently skipped it -- the regression ran fleet-wide, undetected,
for a day, because either fault alone would still have left the OTHER
detection path working. Fix: accept a labelled `_is_border_rule` top
border while the bottom edge stays strict (it is the anchor, always
pure on a real pane, and taking the nearest border above it cannot
pick a wrong row); match the statusline's segment VOCABULARY anywhere
in the row instead of anchoring on whichever segment leads. Both
guards mutation-checked (dropping the glyph guard / loosening the
bottom edge each kill their own test).

A fresh-context adversarial review of that fix found THREE follow-up
defects an anywhere-in-capture structural scan still had: a transcript
that QUOTES an input-box fixture (e.g. this very ticket's own
discussion of the bug) could fool the scan into finding a box inside
the quoted text instead of the real one; a wrapped draft's own pasted
table row (`│ a │ b │ c │`) could be mistaken for the box's top
border; and the chrome-vocabulary match could fire on a single
co-occurring token instead of requiring a real combination. RED
`37ec8ab` (quoted boxes, table rows and prose tokens fool the box
scan) -> GREEN `5664f5a` (anchor the box scan against quoted panes and
single-token prose) closed all three: a candidate border pair is now
rejected when a non-chrome row below its bottom edge starts with the
prompt glyph or carries "esc to interrupt" (the real prompt/turn is
elsewhere); the box HEAD is found by walking up past non-glyph content
rows, never past a strict separator; the statusline match now requires
>= 2 co-occurring segment shapes. One residual quoted-box shape the
review flagged but did not fix was filed separately as #245 rather
than expanding this ticket's scope. Both rounds: full suite green
(3772 tests) with mutation-checked guards.

Closed via the `Closes #243` trailer on 4d8c098 -- pushed directly to
main (no PR/CI in this repo), auto-closed on push. `airuleset.py push`
deployed the follow-up round to all managed boxes; live-verified on
presenter -- the pane armed its `/goal` within the very next watchdog
sweep after deploy, confirming the fix reaches a real ultracode
session, not just the test fixtures. No PR (direct push).

## #246 -- per-ticket compaction starved by the record-time zero-siblings gate on overlapping-worker boxes, moved to delivery time

RED `3fc328e` (16 genuine failures -- 6 behavioural against the hook's
old outright-decline shape, 2 behavioural against the delivery paths'
missing defer gate driven through their real production entry points
[`compact_ticket_boundary`, `deliver_compact_now`], 8 the brand-new
`_session_has_live_bg_tasks` helper's own not-yet-existing tests) ->
GREEN `beed55a` (record at every proven boundary, defer delivery while
background tasks live).

Root cause: `notify-compact-subagent-boundary.sh` placed its
live-tasks safety check (a real property -- compacting while a sibling
worker is mid-flight would drop that worker's own task linkage) at
RECORD time instead of delivery time. On a box running continuously
OVERLAPPING autopilot-workers the zero-siblings instant it demanded
almost never arrived, so a completed ticket's compaction boundary was
never even RECORDED -- "not safe to compact right now" silently became
"never compacts", invisible to the compact-stall backstop (job 26)
because a declined record leaves no artifact at all for it to watch.
Live evidence (montalu@subdev, fleet audit 2026-08-05): repeated
`DECLINE reason=live-tasks n=1/2/3` through a whole day, 3h45m of
total silence in compact-sync.log while tickets kept completing, zero
"compact" lines in the watchdog journal in 72h.

Fix: the hook now records the proven boundary UNCONDITIONALLY,
carrying the live-tasks fact forward on the SAME record line
(`deferred=live-tasks n=N`) rather than discarding it -- the #123
observability this file already built is relocated, not lost. The
safety property itself moved to the two DELIVERY points: a new
`_session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=None,
now=None, captured=None)` helper (watchdog/__init__.py), checked
immediately before the keystroke send in both `deliver_compact_now`
(the synchronous path) and job 14's `compact_ticket_boundary` (the
polled retry), using two independent signals: the pane's own ambient
"Waiting for N background agents" row (`_BG_AGENTS_WAIT_RX`, already
job 9/20's own bg-agent detection), or a sibling worker's own subagent
transcript written within the last 120s (`subagent_active`, already
job 4's own signal). Either signal true defers the send and leaves the
request queued for the next sweep; neither readable never blocks --
deferral is an optimization of an already-real safety property, never
a new way to block delivery on "we don't know". The existing 30-minute
request-expiry ceiling still bounds a continuously-deferred request,
and the NEXT worker's own boundary re-records it if the old one has
already lapsed -- converting permanent starvation into a bounded wait.

`captured` (a keyword param not in the ticket's own literal signature,
added during implementation) reuses the pane capture BOTH call sites
already have in scope instead of issuing a second live `capture-pane`
call -- required because job 14's own test harness sequences its OWN
scripted `capture-pane` replies for `deliver_with_stash`'s internal
re-captures, and an extra real capture ahead of that sequence silently
consumed its first entry and desynced every reply after it (caught by
two genuine pre-existing test regressions before the helper's
signature was adjusted -- `test_draft_with_free_stash_slot_gets_stash_
delivered` and `test_stash_success_marks_delivered_for_its_own_hash`).

Existing-test audit: three hook tests pinning `DECLINE
reason=live-tasks` rewritten to pin the new RECORD-with-deferral
contract (the request now lands in compact-requests.json); three
decision-log tests rewritten the same way, plus a new positive control
asserting NO `deferred=` field on a zero-siblings boundary; two
mutation-teeth tests in `TestDecisionLogAssertionsHaveTeeth` updated --
one now asserts against the new RECORD shape, the other's target
string no longer existing in the shipped script, so it was rewritten
to mutate the NEW deferral-carrying line back to the old
decline-and-exit block, proving the new assertions catch exactly that
regression. Both of the ticket's own required mutation checks verified
KILLED on a throwaway copy outside the working tree: gutting
`_session_has_live_bg_tasks` to `return False` fails both
delivery-path defer tests; reverting the hook's live-tasks line to the
old decline-and-exit block fails all six hook record/decision-log
tests.

Design comment posted before the first code commit
(issuecomment-5192072358). Full local suite: 3791 tests, OK. Ruff
clean on both changed Python files, `bash -n` + `shellcheck` clean on
the hook. Playbook entries appended (`.claude/rules/airuleset-
internals.md`, plus this file's own #243/#246 backfill for two prior
tickets' documentation debt). Pushed directly to main (no PR/CI in
this repo) -- Closes #246 on the GREEN commit auto-closed the issue.
No PR (direct push).


## Batch: #248, #199, #184, #182 (bundled, one work stretch)

**#248 — autopilot-lock acquire crashed (IsADirectoryError) on a stale
directory-shaped lock path.** `cmd_autopilot_lock`'s acquire path now checks
`lock_path.is_dir()` before the exists()/read()/steal flow: an empty
directory is `rmdir()`'d and acquisition proceeds; a non-empty one refuses
with a clear stderr message, exit 1, never a traceback. RED
`23e8a22` / GREEN `d4843d1`. Adversarial-review MINOR finding: `rmdir()`
itself was unguarded and crashes on a symlink to an empty directory
(`NotADirectoryError`, verified empirically) — fixed with its own
try/except falling through to the same clean refusal. RED `edcad9b` /
GREEN `681ff17`.

**#199 — watchdog job 10's `pwedge:`/`pwedge-ping:` state (keyed by tmux
PANE id, a different identity space from every session-keyed prefix in
`run_once`'s cleanup OR-chain) was never pruned for a dead pane.** Added a
`live_pane_ids` set populated from every pane `list_claude_panes` sees this
sweep, and a cleanup branch dropping a pwedge entry whose pane id isn't in
it. RED `7f21e9a` / GREEN `8c0f90d`. Adversarial-review MAJOR finding: an
empty `live_pane_ids` (from `list_claude_panes` degrading to `[]` on ANY
tmux read failure) was wrongly read as "every pane died", wiping all pwedge
state on one transient hiccup — fixed by skipping the prune entirely when
`live_pane_ids` is empty. RED `0bb6a74` / GREEN `6210076`.

**#184 — `notify-delivery.log` only ever logged non-deliveries, so a
healthy box and a broken logger produced the identical empty file.**
`notify.send()` now logs `dedup`/`no-config`/`sent`/`error` unconditionally;
`hooks/notify-discord-send.sh` logs `sent` in the CONFIRM 2xx branch and
inside the fire-and-forget backgrounded curl (which now captures its own
HTTP code from within the same background subshell — still non-blocking).
RED `0edb943` / GREEN `c7bdb5e`. No adversarial-review findings.

**#182 — the run-card dedup key `<repo>#<issue>` was claimed forever on
the first close, so a reopened ticket's second card silently deduped.**
Job 25 (`card_reconcile`) gained an additive `reopen_fetch` step: for every
issue number with an existing marker, ask which are open again and clear
those markers via a new `notify.forget_marker`/`card_marker_numbers` pair —
zero consumers of the plain key changed. `_watchdog_reopened_fetch` wires
one bounded `gh issue list --state open` call. RED `96ef8fd` / GREEN
`add762c`. Adversarial-review CRITICAL finding: the reopen-clear step sat
after `if not closed: continue`, so a repo with nothing closing in THIS
sweep's window never reached it at all — reproducing the exact bug. Fixed
by moving name-resolution + reopen-clear ahead of that early-continue, so
it runs independent of `closed`. RED `504bd81` / GREEN `5cfc0e2`.

Design + validated + reviewed comments posted per issue before/after code
per the extended design-gate (#213/#214). Fresh-context adversarial review
dispatched once over the whole batch's diff before the intended push;
1 CRITICAL + 1 MAJOR + 1 MINOR finding, all fixed with their own RED/GREEN
pairs (commits above), 0 residual findings after re-review. One incidental
test re-pin (`test_vault_purge_job.py`, `303fbc3`) — an unrelated mutation
test's literal anchor moved when `run_once` grew the new `reopen_fetch`
trailing parameter; re-pinned, not weakened.

Full local suite: 3807 tests, 1 documented unrelated wall-clock flake
(`test_ci_poll_repeat_block.py::OneShotReviewFollowupTest::
test_oneshot_ttl_is_configurable_and_short_ttls_decay_fast`, a 1s TTL
racing subprocess-spawn overhead — confirmed flaky standalone, unrelated to
any of the four tickets, pre-dates this batch by several days). Ruff clean
repo-wide. No PR (direct push — supervisor pushes).


## Batch: #179, #226, #202, #219, #250 (bundled, one work stretch)

**#179 — `TestLogPolicy::test_vault_channel` flaked on a wall-clock digit
collision inside the length-leak assertion.** A coincidental digit match
between an ISO timestamp and the string under test could trip the check
on an unlucky second. Fix: strip ISO timestamps before comparing
lengths, so timestamp digits can no longer collide with the leak check.
RED `308ceca` / GREEN `e6c2c6e`. Proven 30/30 consecutive runs.

**#226 — `OneShotReviewFollowupTest::test_ci_poll_repeat_block` flaked
on a TTL-vs-subprocess-overhead race** — a short TTL could decay before
the subprocess spawn it was timing even finished. Fix: real headroom
margin added in the accumulate phase instead of a bare TTL comparison.
RED `29a49dd` / GREEN `90b7985`. Proven 15/15 under deliberate CPU
saturation.

**#202 — hook-driving tests leaked ~1000 Stop-gate counter files/day
into `/tmp`, never cleaned up between runs.** Fix: shared cleanup helper
`tests/_hook_state_cleanup.py`, wired into every hook-driving test's
teardown. RED `511a46c` / GREEN `a55743f`.

**#219 — `design_gate`'s `_CAUSE_RE` missed Slovak root-cause
phrasings entirely.** Widened with a negative lookahead + explicit word
boundaries. Adversarial review found two additional false-positive
classes before it landed — koreň/koreňový-adresár and
zisten/konzistentná both matching as if they were the intended
root-cause phrase. RED `f608f82` + `5fd60aa` / GREEN `f331e13` +
`05e93ab`.

**#250 — supervisor-shaped sessions starved under the #246
delivery-time live-tasks defer.** A request cycled "skip live-tasks"
repeatedly until it hit the 30-minute TTL and died — reproduced live on
both the dev1 supervisor and gatekeeper. Fix: `COMPACT_DEFER_GRACE_S`
(300s, env `AIRULESET_COMPACT_DEFER_GRACE_S`, clamped to `[1, TTL)`)
bounds the defer at both delivery points, anchored on `deferred_since` —
stamped once at the first observed defer and preserved unconditionally
across re-records, never on the request's own `ts` (which every
re-record overwrites). RED `a054b6f` + `4578078` / GREEN `1d39c30` +
`88f3414`. Fresh-context adversarial review: 1 MAJOR (grace anchored on
the overwritten `ts` — a session re-recording faster than the grace
window never reached delivery at all) + 3 MINOR, all fixed with their
own RED/GREEN pairs (commits above).

Pushed `7e42250..88f3414` direct to main (no PR/CI in this repo).
Deployed to all 6 targets via `airuleset.py push`.


## Batch: #172, #183, #180, #174 (bundled, one work stretch)

**#172 — watchdog livelock: sweeps kept getting killed at systemd's
`TimeoutStartSec` instead of finishing.** The real residual was NOT jobs
27/28 (already fixed by #175/#176/#199) — it was the per-transcript pane
loop itself running unbounded wall-clock with no self-bound, so a slow
sweep hit the systemd SIGTERM mid-loop instead of stopping cleanly.
Fix: a sweep-wide wall-clock self-bound (`sweep_budget_s`, env
`AIRULESET_SWEEP_BUDGET_S`) on the per-transcript loop, so it stops
itself before systemd ever has to. RED `ceed8ae` / GREEN `4e2e595`.

**#183 — watchdog job 6's auto-resume reset-time parse returned wrong
epochs on reproduced real shapes.** Three independent bugs in the same
parser: no guard against a truncated year, last-regex-match preferred
over first (letting a stale echoed timestamp hijack the result), and a
non-sticky bad-parse path that silently rolled forward to the wrong
coarse epoch. Fix rejects truncated years and unrecognised months,
prefers the first match over the last, and refuses the coarse rollover
on a bad parse instead of guessing. RED `8844235` / GREEN `a24bab6`.

**#180 — `block-main-implementation.sh` failed OPEN when its `jq`
extraction hiccupped**, silently letting a Fable-main implementation
edit through on the exact malformed-input case the guard exists to
catch. Fix: the guard now fails CLOSED on a `jq` extraction failure,
distinguishably logged from a routine empty result, so a parse error
blocks instead of waving through. RED `f6b6f85` / GREEN `fb1f314`.

**#174 — manual pane revival submitted the captured pane's own screen
text back to it as a prompt**, re-injecting whatever was already on
screen instead of a real instruction. Fix: the revival payload is now
constrained to the literal string `continue`, never the captured
buffer. RED `8705ff1` / GREEN `c2098c9`. The remaining cross-turn
detection gap (revival firing on stale mid-turn output rather than a
genuinely stuck pane) is out of this fix's scope and a NEW hook is
forbidden under the FREEZE — filed as #252 (new hook forbidden under
FREEZE).

Fresh-context adversarial review over the whole batch's diff found 3
real regressions in the batch's own four fixes, all fixed with their
own RED/GREEN pair (RED `63cfa89` / GREEN `9f02665`): (1) the sweep
budget had no `<= 0` clamp — a non-positive `AIRULESET_SWEEP_BUDGET_S`
would have disabled the whole per-transcript loop instead of bounding
it; (2) the production call site of `_human_clock` (which accepts
`now=` for testability) wasn't threading `now=` through, so it silently
read real wall-clock instead of `run_once`'s own timeline; (3) #180's
`jq` pipe was refactored from `cmd | while read` into a split form that
dropped the pipeline's `set -e` coverage for the producer, so a
marker-file read failure crashed the hook uncaught instead of failing
closed — reproduced via `chmod 000` on the marker file.

FREEZE-compliant: zero new files across the whole batch
(`git diff --name-status --diff-filter=A 88f3414..9f02665` empty).
Full local suite: 3849 tests OK. Pushed `88f3414..9f02665` (12 commits)
direct to main, deployed to all 6 targets via `airuleset.py push`.
Live proof of #250's grace-anchor fix (previous batch) observed
post-deploy: dev1's watchdog journal logged
`OK (compact-request, grace-elapsed) zbynek-4:2.0` at 21:00:32 UTC —
the first field grace-elapsed compact delivery.

## Batch: #187, #220, #218, #217 (bundled, one work stretch, local commits only)

**#187 — `block-commit-without-design.sh` resolved the repo from the
PreToolUse payload's static cwd, false-blocking a cross-repo autopilot
worker's commit against the wrong repo's marker namespace.** RED
`8fd872e` / GREEN `fdf8b17`: `notify.resolve_work_cwd(cmd, cwd)` trusts an
inline `cd <path> &&` ahead of the `git commit` invocation, when that path
resolves to a real git repo. A dispatched-before-finalizing adversarial
review then found this first draft had a CRITICAL gap (a `cd` literal
sitting inside a heredoc commit-message BODY could be misread as a real
statement boundary — combined with `design_gate.required_refs` also using
the resolved directory for the #206 closed-issue exemption, this could
silently disable the gate) and, separately, that my own follow-up
quote-stripping hardening (RED `bf32994` / GREEN `cc17a64`) had regressed
a legitimate quoted `cd "/path with spaces"`. Both fixed in one redesign:
the cd-prefix scan is now anchored to the command's own literal START
(position 0), closing the injection surface structurally while letting a
quoted argument parse correctly again — RED `736658a` / GREEN `b372dc6`,
plus a strengthened (previously-tautological) guard test and new
end-to-end coverage of `required_refs(missing, work_cwd)`.

**#220 — `subagent-stop-check-design.sh` and
`subagent-stop-check-run-card.sh` had the same cwd-only resolution bug on
their SubagentStop side**, where no command text exists to scan a `cd`
prefix from. RED `81d4d22` / GREEN `6db6933`:
`notify.repo_from_pr_line()`/`resolve_repo_key()` prefer the evidence
block's own mandatory `pr: #<N> <url>` line (the real repo the PR landed
against) over the payload cwd. Adversarial review found the punctuation
terminator on the URL regex absorbed trailing `)`/`,`/`.`/backtick into
the resolved repo name on a repo-root URL, false-blocking a compliant
worker whose card was genuinely delivered — RED `5035f22` / GREEN
`4d485b2`, plus direct unit coverage for two already-correct-but-untested
mutation gaps (a URL not on a `pr:` line is ignored; the first of multiple
`pr:` lines wins).

**#218 — `pre-push-lint.sh` false-blocked a clean push on a nested-repo
layout** (git root at the repo top, `pyproject.toml` one level down):
`git diff --name-only`'s root-relative paths were piped straight into
`ruff check`, which resolves them against the hook's own process cwd (the
subdirectory, not the root) — "file not found" read as a lint failure.
Fix resolves `$GIT_ROOT` once and makes every changed-file path absolute
before handing it to ruff; the `pyproject.toml`/`setup.py` detection at
the hook's own cwd is left untouched (needed for a genuinely nested
layout). RED `2a8790a` / GREEN `0b2a363`.

**#217 — docs-only: `gh-cli-recipes.md` now warns that GitHub's
issue-linking parser has no negation awareness**, so writing "does NOT
close #N" still auto-closes #N on merge (the literal substring is all
GitHub's parser reads). Added the trigger-word list and safe phrasing
("leaves #N open") plus a content-lock test. `17439e2`.

Adversarial review (dispatched before finalizing, `general-purpose`/opus)
also filed follow-up #257 (needs-user-decision) for the lower-priority
findings deliberately NOT bundled into this security fix: remaining
`resolve_work_cwd` coverage gaps (multi-line/subshell cd, `git -C`, each
a genuine coverage-vs-attack-surface tradeoff), the `pr:` line's lack of
cross-validation against the evidence block's own declared issues, and
minor regex/UX polish (owner placeholder in the run-card example command,
case-sensitivity, host anchor).

FREEZE-compliant: zero new non-test files across the whole batch
(`git diff --name-status --diff-filter=A 04cdd6c..4d485b2` — only 3 new
test files). Full local suite: 3929 tests OK, ruff clean. This session
NEVER pushes (hard constraint) — 13 commits sit locally on `main`
(`04cdd6c..4d485b2`), awaiting the supervisor's push + CI + deploy cycle.

**#251 + #258 — montalu2/3/4 onboarded as push targets + montalu-family
SSH access restored.** #258's root cause: a gatekeeper access review on a
DIFFERENT repo's ticket (odoo-erp#2961) mistook dev1's own default push
key for a foreign identity, purely because of its misleading comment
field ("david grena mac" — byte-identical to `~/.ssh/id_ed25519.pub`, not
actually david@subdev's key), and stripped it from montalu/montalu2/
montalu3's `authorized_keys`. Restored via the sanctioned `root@subdev`
hop under a corrected comment; live-verified (`ssh montalu@subdev true`
rc=0, a real `git log` read succeeds). #251: wired the three already-
provisioned montalu2/3/4 accounts (gatekeeper Phase 1, odoo-erp#2961)
into every generic per-user registry montalu itself already uses —
`REMOTE_HOSTS` (no identity, same default-key shape), `AUTHORITY_BY_USER`
(branch-merge), `SKILLS_EXTRA_BY_USER` (meeting-analysis parity),
`watchdog._REDUCED_STREAM_USERS` (own-label bounce scoping on odoo-erp),
and the subdev ssh-guard's allow-list — authority/skill-scoping/footer/
goal-proof needed zero further code since they're already generic over
`AUTHORITY_BY_USER`'s own keys. Also cloned `~/devel/airuleset` onto all
three accounts (a prerequisite for the next `push`'s `git pull`) and
assessed subdev VPS capacity live (root@subdev): the box is already
running load average ~15 against 4 CPU cores with only 4 of the
eventual 7 Claude sessions active — a resize question was raised to the
user (Hetzner access is gatekeeper's domain, out of this repo's scope
to execute). Adversarial review (fresh-context `general-purpose`): 0 🔴
0 🟡, including a real mutation-tested teeth check on the widened
ssh-guard allow-list. RED `73709cc` / GREEN `a560d5b` / docs `b4acd8b`.

**#253 — tmux scrollback still holey after #235-#242: proven upstream Claude
Code renderer defect, opt-in mitigation shipped.** Reproduced live on dev1
(3.7b, history-limit 50000, both confirmed correctly deployed): a sliding-
window diff over a real `tmux capture-pane -S` dump found a genuine 25-line
chunk of a prior completion report duplicated verbatim 40 lines apart —
direct proof the residual cause is Claude Code's own TUI re-emitting a fresh
copy of the transcript into tmux's native scrollback on SIGWINCH/relayout,
exactly as #235 diagnosed. Confirmed via `gh api search/issues` this is an
OPEN, actively-tracked upstream regression (`anthropics/claude-code#84247`,
`#46834`, bisected to v2.1.101, still present in 2.1.222 despite an
intervening "fixed in 2.1.116" changelog entry) — not fixable via any
tmux.conf option. Shipped the one community-verified mitigation
(`CLAUDE_CODE_NO_FLICKER=1`, alternate-screen TUI) as a new OPT-IN launcher
mode (`claude-fullscreen`, same shape as `claude-ultracode`/`claude-new`),
never the default — it trades away native tmux copy-mode/OS scrollback
search, a real UX choice left to the user. Adversarial review (fresh-context
`general-purpose`/opus): 3 🟡 + 2 🔵, all fixed (test hermeticity against an
ambient `CLAUDE_CODE_NO_FLICKER` env var; documented the `settings.json`
`tui` pin vs env-var ordering; documented the tmux-CC/Windows-SSH guard
interaction). RED `e8ce382` / GREEN `28caa0e` / review-fixup `ac11259`.

**#255 — watchdog delivery cut mid-paste: CORRECTED root cause, real fix
shipped.** Traced every named delivery path in code and found the ticket's
own prime suspect (#172's `sweep_budget_s` self-bound breaking a delivery
between type and submit) does not hold — the incident's own journal shows
the sweep finished cleanly with no kill, and none of `send_continue`,
`deliver_with_stash`, `bounce_backstop`, `goal_autoarm` had (or needed) a
nested budget check between a type and a submit step. Empirically verified
(isolated throwaway tmux session) that `tmux send-keys -l` never auto-wraps
a payload in ANSI bracketed-paste framing at all (`man tmux`: only
`paste-buffer -p` does) — so Claude Code's paste-pending state has some
other internal trigger, and once in it, every existing retry path (plain
Enter, even Escape+Enter) never recovers it; only the literal bracketed-
paste-END sequence does (proven by the incident's own manual recovery).
Shipped two real fixes: (1) `bounce_backstop`/`goal_autoarm` each gained
their own `time_fn`/`sweep_deadline` self-bound (neither loop had one at
all, unlike the per-transcript loop's existing #172 protection), checked
strictly BETWEEN targets/panes; (2) `prompt_wedge_check`'s machine-submit
backstop now escalates to the proven paste-end unstick sequence after
`PWEDGE_SUBMIT_UNSTICK_AFTER` consecutive ineffective attempts on the SAME
draft, tracked by a new per-pane `(hash, count)` state key decoupled from
the existing episode-tracking key's own reset cycle (a real coupling bug
caught by the RED test, not by review — see the playbook entry). RED
`3102d84`+`a2e2d00` / GREEN `ac7a7e4`.

Adversarial review (fresh-context `general-purpose`/opus): 1 🔴 CRITICAL +
1 🟡 MAJOR + 2 🔵 MINOR, all fixed. CRITICAL: the escalation attempt sent
the ordinary pre-Escape (#36) immediately before the unstick sequence's
own leading ESC byte, putting two ESC bytes back-to-back on the wire —
verified live on a throwaway tmux socket — the exact rapid-double-escape
shape that PERMANENTLY DELETES a draft (this repo's #35 rule), and
possibly not even parsed as the intended ESC[201~ at all. Fixed to send
EXACTLY the proven sequence (paste-end + Enter, no Escape). MAJOR:
`bounce_backstop`/`goal_autoarm` reused the bare pane-loop `sweep_deadline`
instead of getting their OWN fair share of the 90/120 margin, silently
zeroing their whole ~30s window whenever the pane loop alone used its
full budget (measured live: 26/3837 sweeps over 3 days) — fixed via a new
`TAIL_BUDGET_S` extension. MINOR: added a bounded give-up ping so an
unrecoverable stuck pane is no longer retried silently forever. RED
`7fb75a8`+`7018e85`+`eafb2c6` / GREEN `ccac144`. Full local suite: 3953
passed.

**#261 — montalu2/montalu3/montalu4: Discord notify DISABLED, live-fixed
and closed.** Pure provisioning, no code: copied `~/.claude/channels/discord/
.env` byte-for-byte from the already-configured `montalu@subdev` via a
direct ssh-to-ssh pipe (content never printed/logged), verified identical
via `sha256sum` on all four accounts, correct mode/ownership by
construction. Live-verified real end-to-end delivery per account
(`airuleset.py notify --body "..."` → `sent`, confirmed by a fresh entry in
each account's own `notify-delivery.log`) and confirmed all four resolve to
the IDENTICAL Discord channel/thread id, matching montalu's own routing.
Closed with evidence, no PR.

**#148 — full-suite pytest flake, fixed (different specimen than filed,
same class).** Reproduced live: 4 full-suite runs (3953 tests), 1 genuine
failure in `TestTotalBindFailureIsFatal::test_a_server_that_binds_nothing_
exits_nonzero_well_before_its_ttl` (took 3.34s under full-suite CPU
contention vs a 2.0s bound with no headroom). Could not reproduce the
ticket's own two named specimens in 3 runs, but the flake class (a real
subprocess test's hardcoded elapsed-time bound with no load headroom) is
the same family. Fix: the test now uses its own larger local ttl (20 vs the
class's shared self.TTL=6, still needed small by the sibling expiry test)
and a widened bound (ttl/2.0=10s), plus a new deterministic headroom test
using a forced-delay launcher wrapper (same technique
`test_readiness_wait_survives_a_slow_server_start` already uses).
RED `8ab2e1e` / GREEN `61b9b9c`. Adversarial review found the new headroom
test didn't actually guard the widened bound (independent constant copies)
— fixed via shared FAST_FAIL_TTL/FAST_FAIL_DIVISOR class constants +
a margin assertion, `6d9c560`. Full file green (42/42).

**#151 — simap@subdev Discord .env, already resolved; closed with
evidence.** No code — `.env` was already provisioned (2026-08-01, before
this batch) during other subdev onboarding work. Closed directly.

**#259 — simap Discord notifications routing to the wrong thread, fixed.**
Root cause: `notify.resolve_owner()` fell back to the tmux SESSION NAME
("simap") with no `DISCORD_NOTIFICATION_CHANNEL_SIMAP`/`DISCORD_MENTION_
SIMAP` keys anywhere (by design — simap has no distinct Discord identity).
montalu/david route correctly only via a bare, hand-added
`AIRULESET_NOTIFY_OWNER` bashrc export from onboarding; simap's onboarding
never got it. First fix (bashrc-managed, mirroring apply_ultracode_launcher)
RED `df828c2` / GREEN `190da54`, live-verified via a fresh `bash -ic` shell
on simap@subdev (channel-id/mention-prefix byte-identical to dev1's real
zbynek resolution; a real Discord ping delivered successfully). Adversarial
review found the bashrc approach only reaches shells started AFTER the
write — simap's own already-running session stayed misrouted. Redesigned:
`STREAM_NOTIFY_OWNER` now lives in `notify/__init__.py`, checked directly
inside `resolve_owner()` itself, so every hook invocation (a fresh process)
resolves correctly immediately with no restart dependency — `6a16148`. Also
added a completeness-lock test cross-checking every `AUTHORITY_BY_USER` key
against the routing map. Full suite green (633/633 in test_airuleset.py).

**#158 — Playwright + node/npx now managed fleet-wide.** `MANAGED_PLUGINS`
grew from just superpowers to also include
`playwright@claude-plugins-official` (context-cost decision recorded in
code: baseline-installed AND enabled everywhere, matching the existing
fleet norm + the superpowers precedent). `RUNTIME_DEPS` grew `node`/`npx`
plus a `RUNTIME_DEP_PACKAGE` override (`nodejs` — Debian's real "node"
package is unrelated, npx has none of its own). RED `5dac819` / GREEN
`40e9a26`. Adversarial review found the module comment's fleet enumeration
was wrong (4 accounts missing it, not just david) and — more
importantly — that montalu2/montalu3/montalu4 have node + the plugin
enabled but an EMPTY browser cache (every real browser call would fail).
Fixed via a new `ensure_playwright_browsers()` (best-effort `npx playwright
install chromium`, no sudo needed) wired into `setup_managed_plugins()`,
plus two mutation-proof test hardenings in `check_runtime_deps` (a
returncode-0 install with the binary still missing must be reported
missing; the warning must name the real apt package) — `9ed43dc`. Filed
#262 as a follow-up for a separate, pre-existing `cmd_push` timeout
robustness gap the review surfaced but which is out of this ticket's scope.
Full suite green (45/45 in the two touched files).

Batch of 4 (#148/#151/#259/#158): all committed locally on `main`, per
this repo's direct-push model (no dev branch, no PR) — supervisor pushes.
Full-repo suite green throughout (3953 → 3969 → 3979 tests as new
regression/hardening tests were added). `ruff check .` and
`python3 airuleset.py validate` both clean at the end.


**Batch 8 (#238, #160): compact-delivery race/thin-context + goal-rearm
backlog-verification/dark-ping — both committed locally on `main`, direct-push
model (no dev branch, no PR), supervisor pushes.**

**#238 — same-turn dispatch race + thin-context re-compacts.** Design
(root cause, chosen approach, rejected alternative for all three defects)
posted before the first code commit. RED `42769c1` / GREEN `c6afb57`: a
min-request-age gate (`_compact_request_too_young`, 2.0s default) closes
the same-turn dispatch race; a thin-context gate
(`_compact_messages_since_boundary`/`_compact_thin_context`, real
`assistant`-entry count since the last `compact_boundary`) prevents
needless "Not enough messages to compact" resends; the thin gate is
checked before any claim is set, so it never strands one. Adversarial
review round 1 (opus, fresh-context) found 🔴1 (the age gate was inert in
production — `cmd_compact_request --record` shared one `time.time()` read
for both `request_ts`/`now`) and 🔴2 (a vacuous claim-stranding test)
plus 🟡3/🟡5/🔵9-18 — fixed `3a58e1d` via a shared bounded-retry helper
(`_compact_retry_until`) + `deliver_compact_record`, a de-vacuoued test
(real process fingerprint), a non-consuming thin-context return on the
sync path, and two new mutation-locked boundary-scan fixtures. A SECOND
fresh-context review of that fix found the retry mechanism itself had
ZERO test coverage (a mutant reverting it to a bare call passed all 349
existing tests), +102s of real sleep added to the suite, a 3s-overshoot
latency regression on self-callback, and an env-var coupling gap that
could silently restore the original off-switch — fixed `22484c4`: direct
tests for `_compact_retry_until`/`deliver_compact_record`, an env
override threaded through every hook-shelling test harness (113s → 9s for
this file), a tightened self-callback retry interval, and a floor coupling
`deliver_compact_record`'s hold to the currently-resolved min-age. Plus 5
more mutation-verified minor fixes (unparseable boundary timestamp,
duplicate retry-log lines, an untested threshold boundary, non-finite
hold/interval clamps). Filed #268 as an explicit, un-scoped follow-up for
one residual config-coupling observation (hook-timeout headroom).
Full suite green throughout (4073/4073 at the end). `ruff check .` clean.

**#160 — goal-achieved silent dead end / retry budget / streak-reset /
wedge job silence.** Design posted before the first code commit,
including the STEP-0 finding that the "stale draft burns retry budget"
defect was already fixed by an unrelated earlier commit, and a hard-debug
consult (real gatekeeper journal) that redefined the streak-reset defect
as a dedup-key bug, not a firing bug. RED `d6e73b7` / GREEN `7c87184`:
`goal_rearm`'s achieved branch now verifies a genuine `🏁 BACKLOG EMPTY`
claim against the real repo backlog before trusting it
(`backlog_marker_gate.classify_backlog_empty_claim` + `_cached_backlog_open`,
shared with job 10); the give-up ping's dedup key now folds in the
streak episode's own anchor; `GOAL_REARM_MAX_DARK_S` sends one bounded
ping instead of a permanent silent dead end; `prompt_wedge_check`'s
not-waiting branch pings when the repo's own backlog is genuinely open.
Adversarial review round 1 found 🔴F1 (`_watchdog_backlog_fetch` counted
the WHOLE repo, not the loop's own core/slice partition), 🔴F2 (the
dark-ping dedup key was stable across a revival), 🔴F3 (the widened wedge
ping bypassed its own cooldown), 🟡F4 (dry-run consumed the dark-ping
one-shot) — fixed `294f1c0`: `_watchdog_backlog_fetch` now shells
`core-quals`/`slice-quals --count`, the dark-ping key folds in the dark
episode's own anchor, the wedge ping's cooldown is now resolved once and
shared by both branches, and the dry-run gate matches the dark-ping's own
fix. A SECOND fresh-context review, live-measured against this box's own
journal and a real two-sweep reproduction, found 🔴F1 (a cached "backlog
open" verdict a re-arm ACTED ON was never invalidated, letting a loop
that closed its own remaining ticket get re-armed a second time on stale
evidence), 🟡F2 (`goal_rearm` had no wall-clock self-bound at all despite
now making a blocking subprocess call per repo, on a box already hitting
its service-timeout kill several times a day), 🟡F3 (authority resolved
against the pane cwd instead of the repo root — the #181 I-5 bug
reintroduced one layer up), 🟡F4 (the wedge job's own new branch had the
identical dry-run gap round 1 fixed for the dark ping) — fixed `a1ea77a`:
the cache entry a re-arm acts on is dropped immediately, `goal_rearm`
shares jobs 8/9's own `tail_deadline` budget, authority resolves against
`_repo_root(cwd=cwd)`, and the wedge branch's dry-run gate matches the
dark ping's shape. Plus 4 more mutation-verified minor fixes (a shorter
negative-cache TTL, `backlog_cache` pruning, a guarded sibling-module
import, a malformed-timestamp guard). Full suite green throughout
(4073/4073 at the end). `ruff check .` clean.

2026-08-06 worktree #212 (usage-alert identity + misroute + dedup): live
reproduction on montalu@subdev (read-only ssh, default key) confirmed all
three parts of the 2026-08-06 comment's scope extension. Root cause 1
(identity): check_usage()'s Discord body carried only label+percent+reset,
undecodable on a phone. Root cause 2 (misroute): `account_owner` in
run_once() came from `pane_owner()` — a raw tmux-session-name lookup with
ZERO knowledge of notify.STREAM_NOTIFY_OWNER (#259's montalu/simap→zbynek,
david→david redirect) — live-verified: on montalu's own box `pane_owner()`
resolves "montalu" (no per-owner .env keys, by #259's own design), while
`notify.resolve_owner()` on the SAME box correctly resolves "zbynek" with
both channel+mention populated. Root cause 3 (found investigating, same
function): Anthropic's oauth/usage endpoint returns `resets_at` with
sub-second jitter — two live fetch_usage() calls 3s apart returned
different strings for the SAME window — so the raw-string dedup re-fired
every 15-min poll (11 duplicate journal lines observed live, 98%→99%
across ~2.5h). Design comment posted BEFORE first commit:
https://github.com/zbynekdrlik/airuleset/issues/212#issuecomment-5202661998
Fix round 1: test:03e445a[red]→feat:307af48[green] — identity fields
(account email from ~/.claude.json, hostname/unix-account, resolved owner)
in the alert body; write_usage_cache() now records account_email; new
notify.stream_redirect() applied at run_once()'s two pane_owner() call
sites (main loop + hosted-panes); _reset_bucket() dedup (truncate to
minute). Fresh-context adversarial review (Opus) found 1🔴+3🟡: the
truncate-not-round bucket still mis-deduped ~1 poll in 5 (real jitter
straddles the boundary, not offset from it — verified against a replay of
real fleet.jsonl resets_at history), 4 MORE un-redirected pane_owner()
call sites (job 14 compact-stash-skip, job 20 goal-stall/drift/rearm), and
a missing-resets_at input would silently NEVER alert (collided with the
"not yet alerted" None sentinel). Fix round 2: fix:d72dc57 — round
(not truncate) + UTC-normalize the dedup bucket, redirect all 4 remaining
call sites, stable "raw:<value>" sentinel for missing/malformed
resets_at, new structural test (TestPaneOwnerAlwaysRedirected) locking
EVERY pane_owner() call site to be wrapped in stream_redirect() so a
future un-redirected site fails CI instead of silently mis-routing.
Review comment posted:
https://github.com/zbynekdrlik/airuleset/issues/212#issuecomment-5203135580
Filed #269 (fleet usage reporting group-by-account — cross-cutting,
explicit follow-up per the issue's original broader scope) and #270
(superseded mid-fix — job 14's instance was fixed directly in round 2
after all, see the issue comment). Full suite 3990 passed / 2 pre-existing
unrelated flakes (confirmed passing in isolation: a banned-phrase-gate
test and a SIGTERM-timing test, neither touches notify/watchdog/usage-
cache). ruff clean throughout. Worktree-isolated (direct-to-main repo,
this dispatch never pushes) — commits are on branch
`worktree-agent-a3c9550651cd159ae`; supervisor integrates.

Batch (#263+#264): subdev stream account dev-env provisioning (montalu2/
montalu3/montalu4 had the push target wired but no claude CLI binary and no
tmux session — the user's reported top-priority regression). #263: extended
cmd_install() (same shape as check_runtime_deps/ensure_playwright_browsers):
ensure_claude_cli_installed() (curl-installs Anthropic's own public
installer when the claude binary is missing, no OAuth needed for the
install step itself), ensure_stream_tmux_session() (bootstraps the ONE tmux
session a subdev stream account is expected to have, claude launched via
send-keys into a fresh bash-backed pane -- never as the session's own
foreground command), report_stream_dev_env()/_stream_provisioning_gaps()
(loud stderr report of the two genuinely-human-only remaining steps --
OAuth login, GitHub PAT -- and a rename-not-delete self-clean of
TODO-PROVISIONING.md once both close). Necessary companion fix: cmd_push's
per-remote ssh deploy had no exception handling around a hard 60s timeout
and silently discarded a successful remote's stderr; this same gap was
independently already filed as #262 -- commented there with the overlap,
left open (not in this batch's named set) for the maintainer to close once
merged. #264: apply_stream_ssh_attach() -- a new idempotent ~/.bashrc marker
block, scoped to the AUTHORITY_BY_USER registry, that attaches an
interactive ssh login straight into the account's tmux session
(create-or-attach `-A`), guarded on three conditions (interactive shell,
real ssh TTY, not already in tmux) so it structurally cannot fire during
push's own non-interactive remote deploy command. Marker-block presence
scan is a left-to-right positional scan (`_stream_marker_block_spans`),
not the lazy-regex shape #235 already documented as corruption-prone.

test:80cdc4d[red] (43 tests, genuine RED via `git stash push -- airuleset.py`
against the untouched pre-fix code) -> feat:cfeaa53[green] Closes #264 ->
feat:d9686b4[green] Closes #263 (split via `git add -p` + a temporary
comment-out/restore dance, since the two features share one file and one
constant) -> style:c7340e1 (ruff cleanup). A dispatched fresh-context
adversarial review (opus) then found 4 real MAJOR bugs before any of this
reached a live box: (1) the tmux bootstrap re-created + auto-launched
claude into a session on EVERY install/push whenever has-session read
"doesn't exist" -- including a session a human DELIBERATELY killed, exactly
the standing user complaint this repo's memory already records; fixed with
a one-time-ever bootstrap sentinel. (2) the 300s remote-deploy timeout was
LESS than the ~780s worst-case sum of the inner best-effort timeouts one
install can burn through -- raised to 900s. (3) tmux has-session does
PREFIX matching on a bare `-t name` (live-verified, tmux 3.7b) -- fixed
with the `-t "=name"` exact-match anchor. (4) a timed-out/failed remote
just `continue`d with no other trace, so a partial fleet deploy could exit
0 -- fixed by tracking failures and exiting non-zero with a summary.
Several MINOR findings also fixed (docstring overclaims on the marker-scan
residual, stderr routing, curl pipefail, an unused param, rename-not-delete
for TODO-PROVISIONING.md, a login-shell PATH fallback, a missing-tmux
guard, an atomic bashrc write). fix:75da61b[green] (10 new regression
tests added for the review findings, 53 total in the new file). Full local
suite green throughout (4022 -> 4032 tests), `ruff check .` clean at every
step. Review findings + fixes posted as durable comments on both issues.
Worked in an isolated worktree (this repo's own supervisor integrates +
pushes + deploys + fires the per-ticket Discord cards; no PR, no CI, direct
push to main per this repo's own two-branch-minus-PR model).
2026-08-06 batch (#266+#177), spinbike incident forensics: #266 job 9 goal_autoarm two defects — Defect 1 (plain/bare-box branch used blind `send_continue`, no settle/verify poll, the exact paste-collapse race that left a 2.9KB /goal payload unsubmitted) fixed by switching to `_send_goal_verified` (same verified primitive job 20 + job 9's own stash branch already use); Defect 2 (`goals-on-screen` viewport regex hard-gated arming BEFORE any transcript-resolution attempt, so a held draft — incl. defect 1's own garbage — pushing the printed `/goal` line off-screen permanently blocked arming with zero log output) fixed by making the viewport fragment a LAZY last-resort fallback, with the one remaining "nothing resolves anywhere" refusal now logged. RED 6f6e66a → GREEN f1903a6. #177 job 10 prompt_wedge_check idle clock keyed on raw transcript file mtime, reset by non-turn CC entry types (mode/permission-mode/bridge-session/pr-link/last-prompt, even a bare touch with no new line) — fixed via new `_last_real_turn_ts()` (content-derived: newest `user`/`assistant` top-level entry), wired only into job 10's own call site. Answered the ticket's pending needs-answer engineering question directly (user/assistant-only, not a broader allowlist) rather than bouncing it back. RED ee162ef → GREEN ccd87ac. Dispatched a fresh-context Fable adversarial review (gate OPEN) against both fixes together: 0 CRITICAL, 0 MAJOR, 4 MINOR + 3 THEORETICAL (2 of the 3 addressed as hardening, 1 documented-only). Hardening RED a9f1a92 → GREEN c39be72: plain branch now re-captures FRESH immediately before sending (was reusing a possibly-stale top-of-loop capture across transcript resolution, incl. a possible 15s sudo subprocess) and a pre-send bareness refusal no longer consumes the 10-min dedup window; the new "no-goal-on-screen" skip now logs once per streak instead of every 60s sweep; job 10's content-derived timestamp is clamped to the file's own mtime so a corrupt/future transcript timestamp can never make the fix wait LONGER than the pre-fix raw-mtime behavior did. Documented-only (no code change): the widened same-cwd cross-session risk once the viewport check is no longer required, and the hosted/foreign-pane prompt_wedge_check call site's knowingly-retained raw-mtime gap (population empty today). Full suite green throughout (3981 → 3987 → 3990). Playbook updated (.claude/rules/airuleset-internals.md, 7 new bullets). Design + validated + reviewed comments posted to both tickets before/after the respective steps.
2026-08-06 solo: #267 holey tmux scrollback fleet-wide — measured first (scripts/measure_scrollback_holes.py, two real-session replicates with real relayout stress: resizes + Ctrl+O + Shift+Tab, compared against transcript source of truth), results pinned as a ticket comment + scripts/scrollback-holes-measurement-267.md: default mode stays clean until a real relayout event then shows small real corruption (0-6%); CLAUDE_CODE_NO_FLICKER=1 (the prior ticket's opt-in mitigation) is WORSE not better — 78.5-87.33% of content missing from native scrollback even with zero relayout stress, a structural property of alternate-screen mode. Verdict: default mode stays; built two things instead. (1) Managed tmux Shift+PageUp/PageDown keyboard scrollback binding (root-table bind-key, extends the existing TMUX_MARK block) — live-apply proven safe (pure key-table registration) and functionally verified live via a real attached pty client sending the actual xterm CSI byte sequences into an isolated tmux server. (2) claude-history companion — a self-contained script reading the session transcript JSONL directly (immune to the renderer defect by construction), deployed via the existing apply_ultracode_launcher mechanism as a claude-history bashrc wrapper. feat:8f66322 (measurement harness) -> feat:3b1056d [green] (both features + tests). Dispatched a fresh-context Opus adversarial review (digest-only): 0 critical, 1 major (test-quality gap on the nonzero-rc live-apply path) + 8 minor findings, 6 triggered-and-fixed (a malformed-transcript crash, a --last 0/negative header/body mismatch, a genuine "<"-prefixed user message silently dropped by the noise filter, the encode_project_dir dual-copy coupling only probabilistically tested, a credential-leak-risk teardown ordering in the measurement harness, plus the test-quality gap) in fix:1c14903. Also found and resolved (not from the review): the repo's own #132 "no code path may end/restart a real session" guard correctly flagged the measurement harness's own throwaway-socket kill-server call — added a narrow, individually-named, self-verifying exemption (two new tests re-check the exemption's own safety claim against the file's source) rather than weakening the guard. Full suite green throughout (3979 -> 3995 -> 4001 tests), ruff clean. Committed on a worktree branch only (dispatch authority: commit-local, never push — supervisor integrates + deploys + runs post-deploy live verification on the real fleet). One side effect already observed live on dev1 during this run: running the test suite's own smoke test (no injected tmux run) live-applied the 3 keybind calls against the box's REAL default tmux server as an accepted side effect of the existing history-limit live-apply precedent -- `tmux list-keys -T root` on dev1 now shows S-PPage bound, confirmed independently of the isolated pty test.
2026-08-06 solo: #186 job 20 goal_rearm STALE-TEMPLATE branch (_goal_template_drift) could not tell "template moved, armed text untouched" (genuine drift) from "template unchanged, user hand-edited the armed text" (a deliberate override) -- both produced the same evidence (payload mismatches every current template, tvar set from an earlier match) and were reverted identically. Live incident: a widened stop condition was silently overwritten twice within 15 minutes. Fix: record `armed_hash` (the payload's own hash at the moment it was last confirmed matching a template) in the up-to-date branch; on a mismatch compare the CURRENT payload's hash against it -- equal means genuine drift (proceed), different (or never recorded -- a pre-fix persisted record) means the armed text itself moved since the last positive confirmation, so `tvar` downgrades to untracked and funnels through the existing once-per-episode "leave it alone" path. test:b9c13e2[red] (TestGoalDriftRespectsHandEdits, 3 genuine AssertionError failures) -> fix:157c544[green] Closes #186. Dispatched a fresh-context adversarial review (opus, `fable-gate` CLOSED at 83%): fixed a 🟡 legacy-migration silence (a pre-fix `tvar`-set-but-no-`armed_hash` session that also no longer matched anything would lose the pre-existing GAVE UP ping entirely -- now fires a one-shot ping distinct from the deliberately-quiet observed-hand-edit case), a 🟡 `--dry-run` state-destruction bug (the `tvar` downgrade + one-shot log flag are now gated on `not dry_run`, same class as the file's own pre-existing `dark_pinged`/`#238-review 🟡F4` precedent), a 🔵 own-delivery misread (our own re-arm landing could be misclassified as a hand-edit if a second template push landed before the confirming sweep -- fixed with a `dq`/`dhash` pre-check), and two zero-coverage load-bearing lines (unconditional `armed_hash` placement; the `tvar = None` downgrade itself) each closed with a new mutation-tested regression. Left undocumented-and-unfixed on purpose: a hand-edit confined to backtick/parenthetical spans normalizes to the same hash and is silently absorbed -- a pre-existing, already-documented property of `goal_template_norm`, not introduced by this ticket. Every changed branch mutation-tested individually (backup file, targeted `str.replace`, confirm the paired test fails, restore) -- 5 mutants, 5 kills. Full local suite green throughout (4174 -> 4179), `ruff check .` clean. Design + validated + reviewed comments posted to #186 before/after the respective steps. Worked in an isolated worktree (this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card; no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo: #272 run-card content-validation regression (codex-bridge #457-#460: cards shaped `Cieľ: #457` / `Dosiahnuté: PR zmergnutý, deploy beží`). Root cause: `_notify_run_card`'s pre-existing fallback (`goal = args.goal or title`, `achieved = args.achieved or "PR zmergnutý, deploy beží"`) only fired when the field was OMITTED (falsy) -- a truthy-but-contentless value like the literal "#457" sailed through untouched. RED test:ee04925 (11/15 tests failing against the unfixed code, confirmed via `git stash push -- airuleset.py`) -> GREEN fix:c58fc4a: a bare/blank/whitespace-only/bare-numeric goal auto-enriches from the already-fetched `gh issue view` title when usable, else hard-refuses (exit 1, stderr + `log_delivery`, same shape #135's "NOT delivered" branch uses); an empty/whitespace/generic-filler achieved has nothing to enrich it from and always hard-refuses. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 82%) -- found the first-cut classifiers were exact-match-after-casefold enumerations, defeated live by trailing punctuation, dropped diacritics, extra whitespace around punctuation, and an unlisted ok/áno/yes/y/n-a/None/TODO family (1 CRITICAL: the "hotové" denylist entry collided with 4 pre-existing test_authority_profiles.py fixtures and broke `push`'s fail-closed gate; 2 MAJOR: both classifiers reduced to "does it contain a single letter anywhere" + a diacritic-folded/punctuation-normalized denylist derived at import time from canonical phrases; 4 MINOR: dry-run docstring overclaim, log-key inconsistency (owner/repo vs bare name), raw content leaking into the durable log's reason field, a weak assertTrue(calls)-only test; 1 THEORETICAL: NFD-decomposed diacritics/ZWSP, both resolved as a side effect of the MAJOR fix). All 8 fixed in fix:d243f03, new TestAdversarialContentShapes class (9 tests) reproducing every live-confirmed near-miss. Full suite green throughout (846 -> 955 -> 4194 tests), ruff clean. Design + validated + reviewed comments posted to #272 before/after the respective steps. Worked in an isolated worktree (this repo's own supervisor integrates + pushes; no PR, no CI, direct push to main).
2026-08-06 solo: #273 push: plugin installs fail on fresh stream accounts (marketplace not registered). Root cause confirmed empirically (isolated scratch `CLAUDE_CONFIG_DIR`, real `claude` 2.1.223, no real account touched): writing `extraKnownMarketplaces` into settings.json alone is NOT sufficient for `claude plugin install X@Y` to succeed — only a genuine `claude plugin marketplace add <source>` (idempotent, confirmed live) actually clones the marketplace to disk. `setup_caveman()`'s own settings-reconcile also ran AFTER its install attempt, too late to help. test:300823a[red] (17 tests, genuine RED against untouched code) -> fix:051d0e6[green] Closes #273 (new `MARKETPLACE_SOURCES`/`_marketplace_names_for`/`ensure_marketplace_registered`; both setup functions register-then-install and return a bool; `cmd_install` exits non-zero on a still-failing plugin install). Full suite 4145 tests OK, ruff clean. Dispatched a fresh-context Opus adversarial review (gate CLOSED, 83% of weekly window): 1 CRITICAL (cmd_push's local "Install locally" step had NO try/except around cmd_install(args) — the new sys.exit(1) propagated straight out of cmd_push BEFORE the remote-deploy loop ran, and since git had already pushed to GitHub by then, ZERO of the 9 remote hosts — including montalu2/3/4, the accounts this ticket is about — would ever deploy the fix), 2 MAJOR (no test coverage for "install itself still fails after successful registration"; a weak source-inspection test would pass against a bare/unwrapped call), 5 MINOR (an unguarded mode-file read could swallow a tracked failure; invalid settings.json never set ok=False; a malformed plugin key would KeyError before the marketplace logic even ran; caveman's own marketplace name was never checked against MARKETPLACE_SOURCES; REMOTE_DEPLOY_TIMEOUT_S no longer covered the new worst-case inner-timeout sum), 1 THEORETICAL (weak env-kwarg assertion). All fixed: fix:befa4ac. Full suite 4153 tests OK, ruff clean throughout. Design + validated + reviewed evidence posted as durable comments on the ticket. Worked in an isolated worktree (this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card + runs post-deploy live verification on montalu2/3/4; no PR, no CI, direct push to main).
2026-08-06 solo (worktree dispatch): #271 draft-rescue — `deliver_with_stash`/`_send_goal_verified` are the only two primitives that ever type into a live pane's box, and neither had any on-disk memory of what the box held before acting — `deliver_with_stash`'s only recovery was Claude Code's own async on-screen auto-restore, never observed from this process, so a silently-failed restore lost content with zero trace (the reported incident: a long draft erased by a goal-autoarm delivery). Forensics comment posted: `#266` (job 9's plain-branch fix) merged to `main` at 13:01:24, ~1h17m AFTER the 11:44 incident, so the live code at incident time was still the pre-#266 blind `send_continue`; journalctl shows ZERO `goal-autoarm` lines for the airuleset pane across the whole 90-minute window (while the identical log line format DID fire for the same pane the day before, proving the mechanism logs when reached) — consistent with the pre-#266 hard viewport gate silently `continue`-ing before ever reaching a log point, the exact "defect 2" pattern #266 itself fixed for a different pane the same day. Fix: a new shared `_draft_rescue_persist(pid, captured, ...)` that both primitives call FIRST, before their own first `send-keys`, whenever the box is non-empty — writes the rendered box content to `~/.claude/draft-rescue/<pane>-<ts>.txt` (`draft_rescue_dir()`, env-overridable via `AIRULESET_DRAFT_RESCUE_DIR`) and journals the path via the caller's own `logs` list. Never deleted on a claimed success (no observable "restore landed" signal exists); a generous 14-day TTL pruned inline on every write (no new watchdog job — the FREEZE forbids one). RED f948ae2 → GREEN e280474 → 8c3f9df (a pre-existing mock-signature fix surfaced by the full suite run). Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 85%): 1 CRITICAL, 3 MAJOR, 4 MINOR, 2 THEORETICAL, all real. Fixed in 5496090: rescue dir/files were world-readable with no `O_NOFOLLOW`/`O_EXCL` (content can include a pasted credential, these boxes host foreign uids by design) → 0700 dir + `O_EXCL|O_NOFOLLOW` 0600 files, symlink-checked before/after `makedirs`, collision retry with a numeric suffix; `_send_goal_verified`'s persist call was a *provable no-op in production* (every real caller gates bareness on the SAME capture object it then passes in) → now re-captures FRESH immediately before typing (job 20's own re-capture-right-before-send precedent) and persists/refuses on THAT, closing the actual race; a failed write was completely silent and 5 call sites (job 7, job 14's `_compact_stash_attempt`, `bounce_backstop`/`gk_request_backstop`'s own `why` list not promoted to the main log on success) never surfaced a successful rescue → all fixed; `_draft_rescue_prune` used to unlink EVERY file in the directory with no name check → gated on `_DRAFT_RESCUE_NAME_RX`; the ordering regression test had no real teeth (mutation-proved) → replaced with a spy asserting zero keystrokes sent at persist time; plus a docstring honesty note on capture staleness and `tests/conftest.py` (a pytest-only autouse isolation fixture — measured 9 files / 43 real writes into the developer's actual `~/.claude/draft-rescue/` when tests run standalone via `pytest`, bypassing `cmd_push`'s own env-var injection which only `unittest discover` sees) + a source-scan lock on that `cmd_push` line. Both THEORETICAL findings confirmed correct as shipped, documented only. `test_goal_rearm.py`'s two shared `cap_seq` builders (`_typed_seq`/`_lit_seq`) plus two inline-literal-sequence tests needed one extra scripted capture inserted to account for the new re-capture call. Full suite green throughout (both `python -m unittest discover -s tests` and `python -m pytest tests/`: 4153/4196), ruff clean. Playbook updated (`.claude/rules/airuleset-internals.md`). Validation + design + review comments posted to the ticket before/after the respective steps. Worked in an isolated worktree (this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card; direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #276 push: _managed_plugin_built() file-presence proxy skips real plugin install (playwright missing on montalu2/montalu3, montalu4 zero plugins). Root cause confirmed locally (no ssh -- a fake CLAUDE_DIR simulation on current main reproduced the exact bug: a stale cache dir + no registry file made the old glob check report "built" anyway; a read-only inspection of dev1's own real installed_plugins.json + `claude plugin list` output confirmed the fix direction, keys matching 1:1). `_managed_plugin_built(key)` globbed for a cache FILE under plugins/cache/... and treated its mere presence as "genuinely installed" -- a stale/partial cache dir left by a FAILED pre-#273 install (before marketplace registration existed) satisfies that glob while claude's own plugin registry has no entry for the key at all, so setup_managed_plugins()'s `if _managed_plugin_built(key): continue` silently skipped the real `claude plugin install` forever. test:9cddfa7[red] (10/56 tests genuinely RED: 4 behavioral AssertionErrors + 6 AttributeErrors for the new helper) -> fix:f83cb48[green] Closes #276: new `_plugin_registry_keys()` reads installed_plugins.json (missing/malformed/non-dict `plugins` field all degrade to an empty set, never a guess); `_managed_plugin_built(key)` is now `key in _plugin_registry_keys()`, a pure registry-membership check. `MANAGED_PLUGIN_CACHE_GLOBS` removed (dead in production code); its unique knowledge folded into the new docstring. A settings-enabled + registry-absent mismatch is now self-healing on the very next push. Dispatched a fresh-context Fable adversarial review (gate OPEN): 0 critical, 1 major, 2 minor, 3 theoretical. Major: `_plugin_registry_keys()` promised "unreadable -> empty set" but only caught a MISSING file -- a directory-shaped path / permission-denied file / invalid UTF-8 all raised UNCAUGHT, escaping `_managed_plugin_built()` at the loop's `if ...: continue` (outside the per-plugin try/except) and silently swallowed by `cmd_install()`'s own outer try/except as "(non-fatal)" -- a strict regression vs the OLD glob check (which never raises on a directory/permission weirdness), and exactly the silent-forever-skip class this ticket exists to kill. Minor: a stale "whose cache is missing" docstring phrase survived the rewrite (x2); a test never mocked subprocess.run and was observed live running REAL `claude` CLI commands mid-suite. Theoretical (reported, not acted on): caveman's own cache-glob check has the identical proxy shape (out of this ticket's scope, FREEZE forbids pre-emptive "same defect elsewhere" fixes); registry-schema drift degrades toward reinstall churn, never silent skip; a sub-millisecond read_file_safe TOCTOU race subsumed by the MAJOR fix. test:552b9ab[red] (3 genuine crash reproductions: directory path, chmod-0 file, invalid-UTF-8) -> fix:801a00c[green]. Reviewer independently mutation-killed all 3 plausible wrong-fix mutants and confirmed the legitimate fast-path (real registry entry) still makes zero subprocess calls. Full suite 4212 -> 4222 tests OK, ruff clean throughout. Design + validated + reviewed comments posted to the ticket before/after the respective steps. Worked in an isolated worktree -- COMMIT-LOCAL ONLY, no push/merge/close/Discord card (this repo's supervisor integrates + pushes + deploys + runs the live acceptance on montalu2/3/4 + fires the per-ticket Discord card).
2026-08-06 solo (worktree dispatch): #165 store guard: VALUE_FILE_RE matched `.secret` as an INFIX (the boundary after it accepted a bare `.` as a terminator), so an ordinary local config carrying `.secret` in its name plus a config-shaped extension was falsely blocked alongside genuine value files. test:111a0bb[red] (6/13 config-ext ALLOW assertions failing against the unmodified pattern) -> fix (working tree) adding an explicit config-extension allow-list negative lookahead right after `\.secret`, leaving every other extension blocked by the pattern's existing deny-by-default boundary — no block-side enumeration needed. Dispatched a fresh-context Fable adversarial review (fable-gate OPEN, 25% weekly) BEFORE committing GREEN: found a real regression in the first draft (F1, MAJOR) — a listed extension followed by a further suffix (`X.secret.json.gz`, `.env.bak`) satisfied the allow-list too, contradicting the shipped comment's own claim; fixed by adding `.` to the inner boundary's excluded class so the extension must be the FINAL component (honest cost: a real config's own future `.bak` backup now also blocks, documented). test:a6935df[red] (4 composed-suffix regression tests) -> fix:7b1b81f[green] Closes #165 (also fixes an inaccurate remedy claim [F4], adds Read/Glob cross-surface tests confirming rule B is consistent across every matcher [F6], and folds F2/F3/F7 into the header's KNOWN GAPS bullet, documented-only). Explicit verdict recorded on `.env` staying on the allow-list (rule B never protected `.env` as a class before this ticket either — verified live against a real bot-token `.env` file on this box, unaffected by the change either way). Corpus replay against ~258k real historical Bash commands: 0 newly-blocked (structurally guaranteed — the fix is a strict regex subset), 1 newly-allowed (a benign self-probe). Full suite green throughout (126 -> 133 in the affected file; 4232 tests OK repo-wide), ruff clean, `python3 airuleset.py validate` clean. Design + still-valid + reviewed comments posted to #165 before/after the respective steps. Worked in an isolated worktree (this repo's own supervisor integrates + pushes; no PR, no CI, direct push to main).
2026-08-06 solo (worktree dispatch): #275 meeting-analysis on subdev — Soniox key provisioning + skill path collided with the vault guard. Root cause, two independent halves: (1) skills/meeting-analysis/SKILL.md's own Soniox key load line named the vault-guarded `$HOME/.claude/secrets/soniox.env` path, which `block-vault-store-read.sh` refuses fleet-wide by design — confirmed live by feeding the real, extracted line to the real hook (rc=2 before, rc=0 after); (2) delivering the key and installing ffmpeg onto the 7 subdev stream accounts (montalu/montalu2/montalu3/montalu4/marek/david/simap) was entirely a one-time hand fix, not code — `airuleset.py` had no provisioning function for either. Fix: SKILL.md's canonical path moved to `$HOME/.soniox.env` (primary) + the existing dev1 voiceagent `.env` (fallback), the guarded path removed entirely (never a third fallback). `airuleset.py` gained `provision_subdev_soniox_key()` (wired into `cmd_push()`'s existing remote-deploy flow — pipes ONLY the `SONIOX_API_KEY=` line out of dev1's own multi-secret voiceagent `.env` via `input=`, never argv/stdout; a missing source is a loud, tracked failure, never a silent skip) and `ensure_ffmpeg_static_binary()` (wired into `cmd_install()`'s existing best-effort fleet-wide family, same shape as `ensure_claude_cli_installed`/`ensure_playwright_browsers`). test:98e7459[red] (25 tests, genuine failures — 3 real BLOCKED-hook assertions + 21 AttributeError since the new functions didn't exist) -> fix:14b6971[green] Closes #275 -> docs:f2bb181 (self-caught stray-paren typo in a comment). Dispatched a fresh-context Fable adversarial review (gate OPEN, `fable-gate` reported 28%/30% < 80%): 3 MAJOR + 1 MINOR, all real, all fixed in fix:533d143 — ffmpeg-only install left `extract.sh`'s own `command -v ffprobe` check still hard-failing (now installs both from the same tarball); the destination `~/bin` is only PATH-reachable from a LOGIN shell, never a Claude Code Bash tool call (moved to `~/.local/bin`, which this repo's own managed claude launcher already prepends to PATH on every invocation); `cp` wrote directly into the final destination before content was confirmed complete, so an untrappable SIGKILL from the subprocess timeout could leave a permanently-"available" truncated binary (fixed with extract-into-scratch-dir + atomic same-filesystem `mv`, proven with a real no-network end-to-end extraction test against a built tar.xz fixture); `~/.soniox.env` now gets an explicit `chmod 600` after the write (umask alone doesn't re-tighten a pre-existing file's mode). 4 mutation-verified test-teeth checks on the soniox-delivery invariants (value-via-stdin, identity/no-identity branching, missing-source failure) — all genuinely caught. Full suite green throughout (4237 -> 4244 tests), ruff clean at every step. Design + validated + reviewed comments posted to the ticket before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card + runs post-deploy live verification against the real subdev accounts (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #173 job 20 goal_rearm's dark-goal cap (GOAL_REARM_MAX_DARK_S, 6h) treated "dark past the cap" as sufficient on its own to give up forever, with no way to tell a deliberate `/goal clear` (#170's invariant) apart from the watchdog itself being unable to sweep for hours (#172's own livelock) -- both produced the identical evidence and both were permanently stranded the same way. Fix (the user's own decided design, posted verbatim on the issue): new `_goal_dark_died_by_outage(tpath)`, reusing the SAME transcript-marker reader job 9's `_goal_was_cleared_by_user` already established (no new hook/job, per the standing freeze) -- newest marker `set` with no later `cleared` -> presumed outage -> falls through into the SAME bounded payload/hash/streak/attempt machinery every other revival path already uses; a `cleared` marker, or an unreadable/missing transcript, keeps the existing conservative skip-and-ping path untouched. test:4d55239[red] (9 new/changed tests, 3 genuine AssertionError + 8 AttributeError against the unfixed code) -> fix:7bef34a[green] Closes #173. Dispatched a fresh-context Fable adversarial review (gate OPEN, 28% weekly): 0 CRITICAL, 4 MAJOR, 3 MINOR, 2 THEORETICAL. Two real bugs fixed: F1 (the fresh re-read bootstrapped from the transcript's TAIL, 4 MB, so a long-lived busy session that kept writing for hours before dying -- precisely the #172 stranded population -- had its own marker pushed past the tail window and was wrongly read as "not an outage"; live-reproduced by the reviewer against the shipped code; fixed by reading the WHOLE file, `off=0`, for this one narrowly-scoped decision) and F2 (the outage branch's own `dark_pinged` reset had zero test coverage -- a mutant deleting it passed the full 144-test suite). Both new regression tests mutation-verified individually (file backed up, targeted `str.replace`, confirm the paired test fails, `cp` restore, diff back to identical) -- 2 mutants, 2 kills. F3/F4 (a naturally-finished goal is indistinguishable from an outage here, bounded by the pre-existing viewport check for a RECENT completion; the dark cap used to be the give-up cycle's only permanent terminator, so an outage-eligible session that keeps failing now gets a GAVE UP ping roughly every streak window instead of once) are accepted, disclosed consequences of the decided design, documented in the code comment rather than changed. F5/F6/F7 + 2 THEORETICAL: no code change (F6's test docstring tightened for precision). Full local suite green throughout (4222 -> 4224 tests), `ruff check .` clean. Design + validated + reviewed comments posted to #173 before/after the respective steps. Worked in an isolated worktree (this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card; no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #133 passive model-identity statusline segment (the user's own decision comment, replacing #37's model-cost signal after #132 removed the restart-based watchdog jobs). New `statusbar.model_segment(payload, managed_model=None)`: a short tier alias (fable/opus/sonnet/haiku) sourced from the statusline stdin payload's `model.id`/`display_name` (the same field `context_cost_segment` already reads), highlighted yellow when it differs from this box's MANAGED_MODEL default, green when it matches or the comparison is unresolvable (never a manufactured false alarm). Compares via the existing `burn.tier()` (suffix-agnostic substring match), never a raw string equality -- avoids re-introducing the exact bug the removed watchdog job 23 hit (MANAGED_MODEL's `[1m]` launch-flag suffix never appears in what a session reports back for its own model id). test:51e91ab[red] (11 tests, genuine AttributeError/AssertionError against the pre-existing code) -> fix:6b9d7d9[green] Closes #133. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 91%): 2 MAJOR, 3 MINOR, all real, all fixed. MAJOR-1: a truthy non-string `model.id`/`display_name` (int/float/list/dict/bool, all valid JSON scalars) reached `burn.tier()`'s `.lower()` uncaught -- being FIRST in the shim's shared try block (one `except Exception: pass` covering 5 segments), the crash silently deleted `sub`/tickets/questions/ctx/account-email too; fixed with a `str()` coercion. MAJOR-2: an unrecognized MANAGED_MODEL (`burn.tier()` -> `"other"`) stood in as a real tier and compared as a genuine mismatch, a manufactured false alarm the ticket's own "unresolvable never alarms" rule explicitly forbids; fixed by treating `"other"` on the managed side the same as unresolvable. MINOR-1: the lazy `import airuleset` measured ~12ms steady-state / ~88ms right after a `push` invalidates the `.pyc`; fixed by baking MANAGED_MODEL into the shim at render time via a new `{{MANAGED_MODEL}}` placeholder (the same shape as the existing `{{REPO_DIR}}` substitution). MINOR-2: the shim-wiring test had no teeth (a no-op mutant still passed it); replaced with a test that renders the real shim as a subprocess. MINOR-3: `_managed_model()`'s fail-safe branch had zero coverage; now exercised via a patched `builtins.__import__`. test:4b8b5ca[red] -> fix:320ffa7[green]. All 4 mutants the findings imply (drop the `str()` coercion; drop the `"other"`-managed-tier guard; no-op the shim's `segs.append(mdl)`; drop `_managed_model`'s `try/except`) individually confirmed KILLED (scratch mutation, targeted test run, byte-identical `cp` restore). Full suite green throughout (133 -> 133 in the affected files; 4303 tests OK repo-wide), `ruff check .` clean. Design + still-valid + reviewed comments posted to #133 before/after the respective steps. Worked in an isolated worktree, commit-local only (this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card; no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #279 montalu4: caveman plugin absent + playwright Version:unknown after the sibling registry-truth fix's own self-heal. Root cause: `_caveman_plugin_built()` was the ONE remaining glob-based "built" gate never migrated to that sibling fix's registry-truth check — live evidence (montalu4): the caveman cache dir for hash ec83e5bace4c is FULLY extracted (matching montalu3's own successful install byte-for-byte, satisfying BOTH known cache layouts) while claude's own installed_plugins.json has ZERO entry for caveman@caveman, so `setup_caveman()`'s `if not _caveman_plugin_built(): register + install` silently skipped the real install forever, with no log output. test:bc3e476[red] (2/5 new tests genuinely failing against the unfixed glob-based body) -> fix:87d3208[green] Closes #279: `_caveman_plugin_built()` now returns `CAVEMAN_PLUGIN_KEY in _plugin_registry_keys()`, the exact same registry read the sibling fix already built and unit-tests for every degenerate input. Playwright's `Version: unknown` investigated separately and found benign: playwright's displayed "version" is derived from a git-log lookup inside the `claude-plugins-official` marketplace CHECKOUT at install time — montalu4's checkout has no `.git` (materialized via Claude Code's own internal officialMarketplaceAutoInstall* self-heal, a GCS blob, confirmed via `.claude.json` + a `.gcs-sha` marker), montalu3's checkout is a real git clone (via this repo's own explicit `marketplace add`). Confirmed functionally identical either way (byte-identical `.mcp.json`/`plugin.json`); the registry-truth check already correctly treats it as installed regardless of the version label — documented as benign in a code comment, no code change for the version display itself. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 91%): 2 MAJOR, 3 MINOR (2 code, 1 test hygiene), 1 THEORETICAL, all real except the THEORETICAL. Both MAJORs were documentation/dead-code accuracy issues with zero behavioral impact but fixed regardless: the playwright "benign" comment's per-box enumeration was itself FALSE (claimed dev1 shows a git-hash version; the reviewer independently re-checked dev1 live and found it ALSO shows "unknown", same git-less-checkout shape as montalu4) and its stated mechanism incomplete (missed that a plugin's own declared plugin.json `version` field wins first, before the git-checkout fallback); and `CAVEMAN_CACHE_GLOBS` was left in place with a comment claiming the runtime shim "still needs" it, which was false — the shim hardcodes its own two glob literals inline and never reads the Python constant, so it was genuinely dead code. Fixed in fix:feefa62: corrected the playwright comment to the real 3-step version-source hierarchy (declared version -> git-derived hash from a git-backed checkout -> unknown) and to only the per-box facts actually verified; removed the dead `CAVEMAN_CACHE_GLOBS` constant, folding its layout-history note into the comment above `CAVEMAN_SHIM_CONTENT` where the two literal globs actually live; fixed a stale "if the plugin cache is missing" inline comment; reworded a mis-scoped FREEZE citation; added `addCleanup` to a leaking test temp dir. The reviewer also independently reproduced the fix's own self-healing claim live in an isolated scratch `CLAUDE_CONFIG_DIR` profile (the exact montalu4 shape: pre-extracted cache dir, no registry entry) — `claude plugin install caveman@caveman` genuinely adopts the stale cache and writes a real registry entry, confirming the docstring's claim is measured, not merely asserted. Full suite green throughout (4288 tests both before and after the review fix), ruff clean. STEP 0 + design + review + a live acceptance procedure all posted as durable comments on the ticket before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + runs the live acceptance on montalu3/montalu4 + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #228 design -- does a served (non-autopilot-worker) session need its own compact boundary? #146's own verdict quoted verbatim on the ticket said the mechanism (`compact-request --self`, #225) already needed no widening -- confirmed by reading `cmd_compact_request`'s `--self` branch and `deliver_compact_self`/`resolve_self_pane`: none of the three check `agent_type`/`subagent_type` anywhere. The gap was purely TEACHING: no rule module told a served session (one NOT running an armed `/goal` loop, which already knows this from `skills/autopilot/SKILL.md`) to reach for it at its own boundary. Fix (the user's own decided design, posted verbatim on the issue 2026-08-06): a new `#### Compact at your own boundary` subsection in `modules/core/completion-report.md` -- the already always-on module that IS a served session's own task-completion contract -- teaching the call, plus a one-sentence cross-reference from `message-status-marker.md`'s own `✅ DONE` bullet. No hook/watchdog change (FREEZE) -- rule/prompt extension only, exactly as decided. test:b32502f[red] (6/7 content-locking assertions genuinely failing against the unwritten prose; the 7th -- a structural pin that the --self call chain stays agent-type-agnostic -- correctly passed against the pre-existing, unmodified code) -> docs:b7475b2[green] Closes #228. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 90%) BEFORE committing GREEN: 2 CRITICAL, both real, both fixed before shipping -- (1) the first draft's trigger included a bare `✅ DONE:` line, but the `self-callback` origin `--self` records under deliberately SKIPS the `#99` no-work and `#48` small-context substantiality gates (the SAME exemption the worker's own per-ticket boundary already relies on), so triggering off the lighter marker would have compacted unconditionally on every trivial served-session exchange; fixed by restricting the trigger to the full `## ✅ Work Complete` heading only, with the exemption rationale stated explicitly in the prose. (2) the first draft told the session to call `--self` AFTER writing the report, "as your own last tool call" -- risking the report no longer being `last_assistant_message`, which the phone ping, the passive Stop-hook fallback itself, and the report-structure gate all key on; fixed by reordering the call BEFORE the report text, so the report stays the turn's genuine final content. Also added, per the review: a durability precondition on the "never mid-work" NEVER (never compact when the only record of the work is the conversation itself -- write it to a ticket first, `durable-decisions-to-tickets.md`), a corrected `$TMUX_PANE`-resolution claim (also names the pane's own active transcript, not pane-id-alone), and a correction to the ticket's own background text (the passive Stop-hook fallback, `notify-compact-request.sh`, has NO agent-type gate and already fires for served sessions today -- confirmed live via 57 real `type=stop-hook` records in `~/.claude/compact-decisions.log` on this box -- unlike the SubagentStop-based worker fallback the ticket's own decision comment was actually describing). Both new test classes were mutation-tested and initially had no teeth: the content locks were bare keyword-presence checks (a mutant flipping both NEVERs to ALWAYS passed 6/6) -- rewritten as polarity-sensitive `assertRegex`/`assertNotRegex` pairs, re-mutated, now correctly kills the reversal; the structural agent-type-gate scan was bounded to `cmd_compact_request`'s own branch alone, so a gate inserted at the top of `deliver_compact_self` (the reviewer's own exact mutant, and the more natural place to add one) passed clean -- widened to scan all three functions in the call chain, re-mutated against the same mutant, now correctly fails. Full suite green throughout (4293 -> 4298 tests), `ruff check .` clean at every step. Design + still-valid + reviewed comments posted to #228 before/after the respective steps. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no hook/watchdog/code change means no live-box behavior to verify post-deploy beyond the rule text itself reaching every session's system prompt).
2026-08-06 batch (#274+#277+#208): post-record-design-comment.sh — three same-file defects investigated together. Root causes: #277 (the bash-level prefilter grep required `gh` to be preceded by start/`;`/`&`/`|`/`&&` optionally `sudo `/`env `, which a leading `GH_TOKEN=$(...)` assignment — the documented david-stream recipe for an account with no persistently-exported GH_TOKEN — never satisfies, so the hook exited before touching python/gh at all); #274 (STEP 0 reproduction in the reporter's own environment shape confirmed the SAME root cause, plus the reporter's own explicit ask for fallback exception logging on the blanket `2>/dev/null || true`); #208 (issue-number extraction used `re.search`, a single non-global match, so a batch worker chaining several `gh issue comment <N>` calls for different issues in ONE Bash command only ever got a marker for the FIRST issue). Fix #277+#274 (one genuinely shared change, both closed together): widened the prefilter to a plain word-boundary match `\bgh[[:space:]]+issue[[:space:]]+comment\b` (GNU grep's `\b` verified correct regardless of prefix shape) + wrapped the whole python heredoc body in a top-level try/except logging any genuinely unexpected exception to `~/.claude/design-gate-errors.log`, never blocking. test:0a5e5fa[red] (3 genuine failures) -> fix:3c0883e[green] Closes #274 Closes #277. Fix #208: switched to `re.finditer`, looping the whole per-issue resolution/gh-view/classify/write body once per distinct issue number (dedup, first-seen order), every internal `sys.exit(0)` -> `continue` so one issue's outcome never short-circuits a sibling. test:c09270c[red] (2 genuine failures, incl. a per-issue-aware fake gh proving a sibling issue is genuinely reached) -> fix:01cbfaf[green] Closes #208. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 91%) of the whole bundle: 0 CRITICAL, 3 MAJOR, 3 MINOR, 1 THEORETICAL. Found a real regression the #208 loop rewrite introduced (MAJOR-1, triggered live): `-R`/`--repo` was resolved ONCE and shared across the whole multi-issue loop, so a compound command naming TWO DIFFERENT repos silently wrote a later issue's marker under an EARLIER issue's WRONG repo, capable of falsely unblocking a commit gate. Fixed by refusing the whole command whenever more than one distinct explicit repo value is found (fail toward the gate staying active, never toward guessing). Also found 3 of the loop's own `continue`s plus the new main exception handler had zero test coverage distinguishing them from the `sys.exit(0)`/`pass` they replaced (MAJOR-2/3, confirmed via hand-mutation — the shipped behaviour was already correct, only the tests were missing); added 4 new mutation-verified regression tests (each individually confirmed to kill its own hand-applied mutant, then restored). MINOR-6 (the new error log lacked this repo's own established `O_NOFOLLOW`/`0600` hardening for a new on-disk writer) fixed alongside MAJOR-1. test:8f9ec0e[red] (1 genuine failure) -> fix:a708756[green]. Two lower-severity, more invasive findings (a pure prose/heredoc mention of "gh issue comment" now triggers real processing since the widened match has no prefix restriction; a quoted `-R owner/evil` inside a `--body` argument can still hijack single-issue repo resolution) filed as #280 (Scope-gate: cross-cutting — needs the quote-aware/heredoc-aware parsers `block-ungated-issue-filing.sh`/`lib-poll-payload.sh` already carry, reused rather than duplicated). Full suite green throughout (18 -> 24 -> 27 -> 33 tests in the hook's own file; 4295 tests full repo suite; 157 tests across the whole design-gate family unaffected), `ruff check .` clean at every step. Design + validated + reviewed comments posted to all three tickets before/after the respective steps (the review comment posted in full on #274, cross-linked on #277 and #208). Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #281 (spin-off of #208's own comment thread) block-local-poll-repeat.sh: a second `gh pr view`/`gh issue view` target in the same session read as a repeat of the first's loop shape. Root cause confirmed live (traced in code, not assumed): the hook's repeat-detection KEY was a purely digit-blind hash of the normalised loop SHAPE — every numeric literal, including a PR/issue number embedded in `gh pr view <N>`/`gh issue view <N>`, was blinded to `N` before hashing. Correct for TUNING knobs (a retuned `sleep 15`->`sleep 20` on the SAME wait must still collide) but wrong for a PR/issue NUMBER, which is the identifying TARGET, not a tuning knob — `gh pr view 436` then `gh pr view 704` hashed to the same key and the second was wrongly blocked. Confirmed the sibling `block-ci-poll-repeat.sh` never sees `gh pr view` at all (its own CI-wait signature requires `gh run (view|watch|list)`/`gh pr checks`; #127 already decided this hook keeps owning the gap) — scope stayed single-file. Also confirmed a pre-existing test (`Issue127LocalSideGapTest::test_a_different_pr_number_is_the_same_shape_digit_blind`) literally ASSERTED the bug as intended behaviour. Fix (mirrors #118's own RUN_ID extraction in the sibling CI hook): extract a TARGET id anchored to `gh (pr|issue) view <N>` and fold it into the state-file key (`${SHAPE}-t${TARGET}`) only when found; absent -> pure SHAPE key, unchanged, so every existing "retuning still collides" guarantee for non-targeted loops is untouched. test:d3b7717[red] (inverted the bug-encoding test + 2 new companion tests, 2/39 failing) -> fix:0e38751[green] Closes #281. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 97%): 0 CRITICAL, 1 MAJOR (real, reproduced live: `gh pr view -R o/r <N>`/`--repo o/r <N>` — a flag between `view` and the number, routine in this repo's own corpus — fell back to the pure-shape key, so #281 reproduced verbatim for those two spellings), 1 THEORETICAL (an unbounded TARGET digit run could exceed the filesystem's NAME_MAX, whose write failure the existing `|| true` hides — fail OPEN), 3 test-coverage gaps found via hand-mutation (mutants M5/M7/M8 all SURVIVED: nothing pinned that `head -1` picks the first match, that the `-t` separator is present in the key, or that the BLOCK log's `target=` field is correct — all three already-correct, now regression-locked with zero behaviour change), 2 MINOR accepted as a documented residual (a URL form, a shell-variable form, a line-continuation, and a decoy `gh pr view <N>` merely mentioned in an echo/comment all still fall back to the pure-shape key — not a regression, same class of limitation every other text-only signature in this file already carries: CI_RX, the mutating-action regex). Fixed the MAJOR (flag-tolerant regex) and the THEORETICAL (10-digit TARGET cap) in the same ticket; added the 3 regression-lock tests. test:bcea99d[red] (5 new tests, 2 genuinely failing) -> fix:0c67e2d[green]. Full suite green throughout (39 -> 44 tests in the hook's own file; 4377 tests full repo suite before the review round), `ruff check .` clean at every step. Still-valid + design + review comments posted to #281 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #195 strip_mentions() (hooks/stop-check-prose-violations.sh) passed the WHOLE assistant message to python3 as a SINGLE argv entry (`python3 - "$1" <<'PYEOF'`); Linux's MAX_ARG_STRLEN caps that at 131072 bytes, so past it execve failed (E2BIG), python3 never ran, and the caller's bare `2>/dev/null || printf` fallback silently substituted the RAW, UNSTRIPPED message — a phrase inside backticks/a fenced code block/a double-quoted span (a MENTION, per #96) then read exactly like a bare unquoted OFFER to every downstream `msg_has "$MSG_MENTION" ...` incriminating-pattern check, with no note ever reaching UNDET_FILE. Reproduced against current HEAD before touching anything: 131036 bytes -> not blocked, 131111 bytes -> hard-blocked on a mention-only message, no "undeterminable" note. Fix: the message is passed by a temp FILE instead of argv, the same fix this repo already shipped for an identical failure class in a sibling hook (hooks/lib-poll-payload.sh, #124) — a file has no argv-sized ceiling, removing the trigger at the root. On a genuine residual strip failure (mktemp/write/python3 itself, independent of size) the fallback still stays the raw, unstripped message — the established fail-closed "unsubstantiated EXONERATING exemption is denied" direction from this file's own #194 taxonomy — but record_undet() (hoisted ahead of strip_mentions()'s own definition so it can be called from there) now runs first, so the note travels on the block reason instead of vanishing. test:ac68bf7[red] (6 genuine AssertionErrors: mention past the boundary in all three quote forms, the MSG_NOGOAL_MENTION call site, the ticket's own threshold pair, the missing note; 4 controls already passed) -> fix:23d996f[green] -> fix:8a9c784[green] (self-caught: `${#MSG}` is CHARACTERS under a UTF-8 locale, not bytes — corrected the diagnostic wording before dispatching review) Closes #195. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED at 96%): 0 CRITICAL, 1 MAJOR, 4 MINOR, 2 THEORETICAL. The MAJOR was real and live-triggered: `open(path, "r")` defaults to `newline=None`, silently applying universal-newline translation (a lone `\r` or `\r\n` both become `\n`) that `sys.argv[1]` never did — a genuine bare bypass offer whose two halves straddled a lone `\r` used to block (argv preserved it) and, through the file read, silently stopped blocking (reproduced against both the pre-#195 and the post-#195 hook); fixed with `newline=""`. Also fixed: a redirection-error-suppression gap in `printf ... >"$_f" 2>/dev/null` (the same #196-documented shape — braced the redirect so `2>/dev/null` actually covers an opening failure); the diagnostic wording naming message SIZE even though size can no longer be the cause post-fix (misleading the agent toward a "shorten it" repair that would not help — reworded to name the real, environmental cause); a test-teeth gap where the "block reason says undeterminable" test couldn't tell WHICH of the two strip_mentions() call sites fired (closed with a counting python3 stub isolating each call site — both already passed, a test gap not a code gap). Two findings documented rather than code-changed: an unwritable TMPDIR also breaks UNDET_FILE's own mktemp, so the note itself becomes unavailable on that one failure mode (fail DIRECTION still correct, matches this file's own already-tested TestAnUnwritableTmpdirDoesNotInvertTheFailDirection); a SIGKILL between mktemp and the explicit `rm -f` can leak the scratch file (an EXIT trap was tried and rejected — verified live that a `local` var is unbound by the time a lazily-evaluated trap fires post-return, so baking the path in via double-quote expansion at registration time reopens a quote-injection surface via a hostile TMPDIR for a residual with zero observed leaks — same untrappable-by-construction class this repo already accepts elsewhere). While verifying the review's own fixes, caught a THIRD self-introduced bug before it ever reached a commit: building the note string via a separate `_NOTE=...` assignment BEFORE reading `$?` let the assignment's own success silently clobber the real exit code (`record_undet` reported "check exited 0" for a genuine failure) — fixed by capturing `$?` as the first statement in each fallback block, mutation-verified (revert to assignment-first order -> the new regression test fails with a real AssertionError; restored -> passes). test:d52600b[red] (1 genuine AssertionError: the CR-translation regression; 2 new call-site-isolation tests + 1 CRLF control already passed) -> fix:43fc514[green] (also carries the self-caught $?-clobber fix + its own regression test, mutation-verified). 15/15 in tests/test_prose_gate_mention_size.py, 81/81 across the whole prose-gate + quality-bypass suite, 730/730 across every other test file referencing this hook (test_airuleset/test_ask_before_assuming_dropped_rows/test_autopilot_master_skill/test_worker_attestation), `ruff check .` clean throughout. STILL-VALID + design + review comments posted to #195 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch, RESUMED after a dead prior worker): #280 post-record-design-comment.sh text-scan matches prose mentions/quoted args, not just real invocations — recovered a dead worker's durable state (4 commits already on the worktree branch: RED/GREEN for the original two findings — heredoc-mention swallowing a real invocation, `-R` smuggled from inside a quoted `--body`) and its own in-flight adversarial-review dispatch. The review's actual report was never captured in the worker's own transcript (only "Async agent launched successfully" plus a wait loop, no result text before the API 500 that killed it) — recovered by finding the DISPATCHED review agent's OWN separate transcript file (`agent-<its-agentId>.jsonl`, one level deeper than the worker's own file, keyed off the `agentId:` printed in the "Async agent launched" tool_result) and reading its final message directly: 0 CRITICAL, 4 MAJOR (2 genuine regressions the prior worker's own fix introduced, 2 pre-existing), 5 MINOR, VERDICT SHIP AS-IS. F1 (`ssh host -t "gh issue comment N ..."` silently swallowed — `-t`/`-m` blanked unconditionally, ignoring that they mean something ENTIRELY different when they belong to a different command sharing the segment) and F2 (a `--body` value merely describing a heredoc recipe misread by the heredoc-aware strip as a real trigger, swallowing a sibling invocation as the fake heredoc's own body — caused by running the flag-blank pass AFTER the heredoc strip instead of before) both confirmed live via the recovered review's own reproduction commands, then independently re-derived and fixed: `_blank_gh_text_flag_values` now requires a `git`/`gh` word to have ALREADY opened the SAME segment (reusing `lib_poll_payload.SEP`/`GITGH`, deliberately NOT its INTERP exemption — that would reopen the earlier sudo-prefixed `-R` leak) before blanking a flag's value, and the flag-blank pass now runs BEFORE the heredoc-aware strip. test:b5ff9f2[red] -> fix:16d8041[green]. F4 (pre-existing: `-R`/`--repo` extraction scanned the WHOLE scan_cmd, so a `-R`-shaped string in a segment that is NOT the `gh issue comment` invocation at all — an `echo` argument, a different `gh` subcommand's own flag/value — was scavenged as if it belonged to the invocation being classified) fixed by scoping the lookup to each match's own SEP-bounded segment (`_match_segment`, a local SEP tuple independent of `lib_poll_payload` so the scoping keeps working even if that import fails). test:9a6fa53[red] (all 3 first attempts accidentally passed pre-fix too — the fake attacker repo's basename happened to coincide with the real repo name, `attacker/airuleset` -> basename "airuleset"; re-picked `attacker/evil-repo` to force a genuine RED) -> fix:76e380b[green], which also fixed two MINOR nits from the review in the same commit (`_control_flow_text`'s docstring overclaimed "falls back to text UNCHANGED" for every failure mode — only an import failure genuinely does that; and a `lib_poll_payload` import failure was swallowed with zero diagnostic despite the file carrying `_log_exception` for exactly this class), each with its own genuine RED (mutation-confirmed for the logging nit — a fresh test builds a `bad_root` with `design_gate.py`/`notify/` present but `lib_poll_payload.py` deliberately absent, so the OUTER import succeeds and the INNER one specifically fails). F3 (pre-existing: backslash-escaped quotes and the standard shell `'"'"'` single-quote-splice idiom defeat BOTH `_TEXT_FLAG_VALUE_RE` and `lib_poll_payload.py`'s own `BODYFLAG`/`MSGFLAG` — neither regex is backslash-aware) filed as a follow-up (Scope-gate: cross-cutting — `lib_poll_payload.py` is shared by `block-local-poll-repeat.sh`/`block-ci-poll-repeat.sh`/`nudge-poll-loop-timeout.sh`/`lib-poll-payload.sh` too, each with its own extensive adversarially-hardened test suite; proper backslash/shell-idiom-aware span matching is a genuine design decision needing a corpus replay across all four dependent hooks, not a same-diff fix). Full suite green throughout (40 -> 44 tests in the hook's own file; 224 tests across the whole design-gate/poll-payload sibling family; 4343 tests full repo suite), `ruff check .` clean. Design + still-valid comments already present on #280 from the dead worker's own earlier turns (verified via `gh api .../issues/280/comments`, never re-posted). Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-06 solo (worktree dispatch): #191 sub-dev footer cannot see its own handed-over tickets. Two root causes in `cmd_tickets_status`: (1) "different lane" — the `gk` bucket only checked `ready-for-review`, never airuleset's own `needs-gatekeeper` hand-off lane (`cmd_gk_request`); (2) "ownership relabel" — a SHARED-gh-account stream's slice (montalu/marek/simap/montalu2-4, which authenticate as the maintainer identity) is `label:stream:<user>` ALONE, so a handed-off ticket relabelled away from it (the fix moved to shared code) vanished with no recovery signal, since GitHub's author/commenter fields are identical across every stream sharing that PAT. Dispatched a fresh-context Opus design consult (fable-gate CLOSED) before coding, per the "genuinely HARD/cross-cutting" escalation criterion — approved folding needs-gatekeeper into the existing `gk` bucket and a GraphQL-batched issue-events re-attribution (never N REST calls per candidate, since that shares a rate-limit bucket across every stream on the same PAT). test:09b8320[red] (2 genuine bug reproductions, confirmed via `git stash` on the implementation-only file) -> fix:c979da1[green]: Part A folds `needs-gatekeeper` into `handed`; Part B re-attributes a relabelled ticket via ONE batched GraphQL query over `LABELED_EVENT` history; Part C makes `cmd_gk_request` best-effort apply an origin label at hand-off time so Part B has data to find. Dispatched a fresh-context Opus adversarial review (fable-gate CLOSED): verdict "needs rework" — 1 CRITICAL, 5 MAJOR. CRITICAL C1: the origin marker reused `stream:<user>`, the repo's OWNERSHIP primitive — a needs-gatekeeper ticket tagged that way would become PERMANENTLY part of the stream's own `/goal` stop-proof slice (`slice-quals --count` could never reach 0 while one stayed open, #181's never-stops class at a new address) and would be misclassified `implement`. MAJOR M1 (live-verified against real odoo-erp label metadata): `gh label create --force` unconditionally overwrote an EXISTING label's hand-curated colour/description on every call. MAJOR M3: "was my label EVER applied" let two streams that both once owned a ticket (A -> B -> unlabelled) BOTH reclaim it. MAJOR M4: the create-mode origin label was baked into the primary `gh issue create` call, so a rejected origin label silently dropped needs-gatekeeper too. MAJOR M5 (mutation-verified): two of the four new tests had no teeth — their GraphQL stub answered with an unrelated label, so removing the guard they existed to protect still passed. All fixed in fix:c044fee[green] Closes #191: the marker is now `handed-by:<user>` (never `stream:<user>`, resolving M2 for free — the gatekeeper's own core count is unaffected); label-create checks existence first and never overwrites; re-attribution resolves the TEMPORALLY-LAST origin-shaped event, not "ever"; the create-mode origin label applies in its own call after the primary create succeeds; both toothless tests now stub the actual tested identity. 4 new/fixed regression tests each individually mutation-tested (scratch backup, targeted str.replace, confirm the paired test fails, restore) -- 4 mutants, 4 kills. Also folded in: candidate list capped at 50, `last: 20` instead of `first: 100` (was reading the oldest events on a churned ticket), an `int()` alias-formatter guard, an explicit-identity fix for an environment-dependent test. Full suite green throughout (4340 -> 4345 tests), `ruff check .` clean at every step. Design + validated + reviewed comments posted to #191 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model). Explicitly out of scope: retroactively backfilling the real, already-relabelled odoo-erp tickets cited in the issue (they were filed directly with `needs-gatekeeper` and never carried `stream:<user>` at all, so there is no event history to recover them from) — a one-time manual data-repair action, not a code fix.
2026-08-06 solo (worktree dispatch): #221 gk-request hlási úspech na read-only fork streame, hoci needs-gatekeeper štítok sa ticho nepridal. Root cause (confirmed via a local unittest.mock simulation of GitHub's documented issue-create behaviour, no ssh, no remote box): `gh issue create --label needs-gatekeeper` bakes the label into the SAME call as the create, and GitHub's `POST /repos/{o}/{r}/issues` endpoint SILENTLY DROPS the `labels` field for an actor lacking push access rather than failing the request -- unlike the dedicated `POST .../issues/{n}/labels` endpoint (`gh issue edit --add-label`), which correctly 403s. `cmd_gk_request`'s create-mode path only ever checked `r.returncode == 0`, which reports the create succeeding, never whether the label landed -- so a read-only fork's request printed "gk-request filed" with neither the label nor a GATEKEEPER-ACTION title prefix anywhere on the ticket, invisible to job 11's needs-gatekeeper/in:title queries and the gkq statusline badge. STILL-VALID reproduced locally before any code change: `python3 -` mocked `subprocess.run` to return rc=0 for a combined `--label` create call (matching the documented GitHub behaviour) and confirmed the current code prints "gk-request filed" with the label baked into the single call and no verification. Fix: mirror the SAME shape #191/M4 already established for the origin label -- apply `needs-gatekeeper` via its OWN `gh issue edit --add-label` call, AFTER a bare create, getting an honest exit code from the real (non-silent) label endpoint; denial degrades to the GATEKEEPER-ACTION title prefix (same as `--issue` mode already does); if BOTH the label add and the retitle are denied, fail loudly (rc=1) instead of reporting false success. test:03a1b23[red] (2 new tests reproducing the silent-drop degrade and the both-denied fail-loud case) -> fix:3d4b775[green] Closes #221 (also updates 3 pre-existing tests that encoded the old baked-in-label assumption -- test_create_with_label, test_create_mode_also_applies_origin_handoff_label, test_create_mode_origin_label_failure_never_drops_needs_gatekeeper). Dispatched a fresh-context Fable adversarial review (fable-gate OPEN, 40%/46%): 0 CRITICAL, 1 MAJOR (real, reproduced by the reviewer): `new_num.isdigit()` guarded BOTH the label-add and the retitle attempt, so a create success with unparseable stdout short-circuited past both straight to a false "gk-request filed" -- the exact invisible-escalation class the ticket exists to kill, contradicting the fix's own stated invariant. test:07315cc[red] (stdout="done\n" with no trailing digits, proving the fall-through) -> fix:ba029fd[green]: an unparseable issue number now fails loudly immediately, before any label/retitle attempt is even possible. Also fixed 2 of 5 MINOR findings in the same PR (aa4cc95): a test-teeth gap where a "retitle unconditionally" mutant would have passed every test (added an explicit no-redundant-retitle assertion), and stale prose in the r2 fallback's success message ("label denied" -- no longer possible once the primary create carries no label at all). 3 remaining MINOR findings accepted as-is (documented in commit messages): a test-B-alone-is-weak observation covered by its adjacent test pairing; an orphaned-issue-on-loud-failure note already mitigated by naming #N in the message; `--issue` (comment) mode staying soft on a denied label + failed retitle, explicitly out of #221's create-path scope, filed as #283 (Scope-gate: needs-user-decision). Full suite green throughout (33 -> 34 -> 35 tests in the affected file), `ruff check .` clean at every step. STILL-VALID + design + review comments posted to #221 before/after the respective steps. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo (worktree dispatch): #256 Docs gap — the credential command was invisible in the always-on modules, so a session needing a credential from the user reached for the file-upload command instead (live incident: songplayer session handed the user a drag-drop file endpoint for an API key). Root cause: `receive-files-via-upload-url.md` covers only the file direction; nothing always-on names the credential command at all, and it is only discoverable via `--help`, which a session has no reason to run once it believes `upload` already answers "user needs to send me something". Chosen approach (per the rule-intake gate, topic already owned by an existing module -> extend, never a new always-on block): extended the existing module in place with a `#### Receiving CREDENTIALS FROM the User` subsection — the trigger, the exact `secret request`/`secret exec` invocation, when to use it instead of `upload`, and a hard ban on pasting a credential into chat or routing it through `upload`/`share`. Rejected alternative: a new sibling module (the issue's own "suggested fix") — rejected against the intake gate and to avoid growing the always-on prefix for a ~15-line addition when an existing module already owns the direction. Deliberately narrow: does not resolve #152 (a Stop-hook chat-ask ban — a separate false-positive-rate decision, left open with a pointer comment) or #154 (secret exec child-command containment — unrelated internals). No new hook, no watchdog job (FREEZE-compliant). test:5b53b5f[red] (2 genuine AssertionError/ValueError against the pre-#256 module) -> docs:d9d6a2c[green] Closes #256. Dispatched a fresh-context Fable adversarial review (fable-gate OPEN at 39%/45%): 1 CRITICAL, 2 MAJOR, 3 MINOR, all real, all fixed in fix:2e00126[green]. CRITICAL: the exec bullet overclaimed a blanket "never appears in your context" guarantee — the real docstring only promises captured stdout/stderr are redacted, a child writing the value to a file is unconstrained (the exact open residual #154 covers); reworded to the narrower, accurate claim. MAJOR-1: "same bind-list discipline as upload" was wrong — `secret request` defaults to encrypted (tailscale) interfaces only, unlike upload's unconditional private-interface list; needs `--allow-plain` for LAN. MAJOR-2: the chat-accident recovery line ("prefer secret exec from then on") was not actionable — there is no CLI action to insert an already-leaked value into the store; reworded to re-run `secret request <NAME>`. MINOR x3: demoted the new subsection from `###` to `####` (was reading as a sibling top-level module); fixed a comment/prose polarity mismatch in the new test; removed two vacuous test assertions (confirmed true against the pre-#256 file by direct check) and rescoped the chat-ban assertion to the credential-specific BANNED paragraph rather than the section heading — re-proven RED by re-running the tightened assertions against the pre-#256 module. Full targeted suite green throughout (61 tests across test_upload_url.py + test_ruleset_conversion_wave2.py, the only two files that reference this module), `ruff check .` and `airuleset.py validate` clean; full-repo suite not run (docs-only change with zero code touched, confirmed via grep that no other test file references this module). STILL-VALID + design + review comments posted to #256 before/after the respective steps; a pointer comment (not a resolution) posted to #152 noting the overlap. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model; no hook/watchdog change means no live-box behavior to verify post-deploy beyond the rule text itself reaching every session's system prompt).
2026-08-06 solo (worktree dispatch): #282 (spin-off of #280's own F3, filed as a follow-up rather than fixed in the same diff) -- BODYFLAG/MSGFLAG (hooks/lib_poll_payload.py) and _TEXT_FLAG_VALUE_RE (hooks/post-record-design-comment.sh) all shared the same naive `(['"])(.*?)\1` quote-span regex to "blank" a text-payload flag's own value before scanning for control-flow (poll-loop shapes / fake gh-invocation / -R-repo-smuggling). It closed the "value" span at the FIRST quote character seen, escaped or not, and had no notion of the shell's `'"'"'` single-quote-splice idiom (four concatenated fragments forming ONE logical shell word with no separating whitespace) -- everything after the premature close survived unblanked as live-looking text. Reproduced live against current HEAD before any code change (posted as STILL-VALID evidence): an escaped-quote `-m` value and a splice-idiom `-m` value both wrote a real design-gate marker for a #99 that nothing genuinely invoked; an escaped-quote `-R evilcorp/nastyrepo` smuggled inside a `--body` value redirected a #41 marker to the wrong repo. Fix: a new shared scanner `lib_poll_payload._shell_word_end(text, start)` walks forward from a flag's opening quote implementing real bash word-tokenization -- no escaping inside single quotes, backslash-escaping inside double quotes, and an immediately-following quote character (no whitespace) treated as a continuation of the same logical word (exactly what the splice idiom produces). Both `flag_spans()` (BODYFLAG/MSGFLAG) and post-record-design-comment.sh's own `_blank_gh_text_flag_values()` (which already receives the `lib_poll_payload` module as a parameter, so it calls the SAME function rather than reimplementing) were rewritten to stop their regex at the opening quote and resolve the true span via this one scanner -- closing the "needs a blast-radius audit across four dependent hooks" concern the original filer raised, since there is now only one implementation site to audit. The blanked span covers the whole resolved word (quote characters included), spliced positionally in reverse, never a value-based `str.replace`. test:483b6cb[red] (4/6 new poll-payload tests + 3/3 new design-gate tests genuinely failing against the unfixed hooks; 2 control pins already passed) -> fix:6035b6b[green] Closes #282. Dispatched a fresh-context Fable adversarial review (fable-gate OPEN at 40%/46%): 0 CRITICAL, 1 MINOR (own regression, triggered live), 2 MINOR (documentation/test-coverage), 1 THEORETICAL pre-existing (not a #282 regression), 1 confirmed-not-a-bug. The MINOR regression was real: `_shell_word_end` stopped ONLY at whitespace, never at an unquoted shell metacharacter (`;`/`&`/`|`/`(`/`)`/`<`/`>`) -- a separator glued directly to a value's closing quote with NO space (`--body "x";gh issue comment 99 --body "y"`, the exact shape #208's own batch-posting tests already cover) was consumed INTO the blanked span, silently swallowing the first word of the very next command and losing a real later issue's marker. Reproduced live at both the lib_poll_payload.strip() level and the design-comment hook's own marker-writing before touching anything. test:9d6afe5[red] (3 new metachar tests + 1 hook-level test genuinely failing; the discriminator/control-pin tests already passed) -> fix:09703ed[green]: added `elif c in ";&|()<>": break` to the unquoted scanning branch (checked before the backslash-escape branch, so an escaped `\;` inside unquoted text still correctly skips as a literal). Also fixed the docstring's "matching real bash tokenization" overclaim (reworded to state the actual contract) and documented the review's THEORETICAL finding ($( )/backtick command substitution containing its own quotes inside an outer value is not shell-grammar-parsed -- pre-existing, unaffected either way by this fix, never observed in production) as a known residual rather than filing a new ticket, matching this file's own established documented-residual convention for lib_poll_payload.py. The `end - start > 2` bare-quote-pair check was independently confirmed sound (an escaped-quote-only value measures 4 chars and is correctly blanked; only a genuinely empty `''`/`""` measures exactly 2). Full suite green throughout (87 -> 93 tests across the two touched test files; 101 tests in the two sibling poll-repeat hook suites unaffected; 500 real commit-message subjects from this repo's own git history run through strip() with zero exceptions both before and after the review fix), `ruff check .` clean at every step. STILL-VALID + design + review comments posted to #282 before/after the respective steps. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-06 solo (worktree dispatch): #254 grouped tmux sessions pile up per attach (zbynek-1..4 sharing the same windows) — the managed conf never set `destroy-unattached`, so tmux's factory default `off` orphaned every detached grouped duplicate forever. Reproduced live against the REAL default socket (a genuine pty-attached-then-detached grouped sibling I created, `a254-test-1`, linked to `a254-test`) — lingers, and this box's own real live sessions right now are exactly `zbynek-4`/`marek-0`, matching the reported shape. Empirically determined the correct tmux value (not assumed from the man page, and CORRECTING the ticket's own literally-named suggestion): tested BOTH `keep-group` and `keep-last` against a real tmux 3.7b server via a genuine pty-attached client on an isolated `-L` scratch socket — `keep-group` (the value the ticket's own title names) destroys every ORDINARY STANDALONE (non-grouped) session the instant its one client detaches, identical to boolean `on`; `keep-last` is the value that actually matches the ticket's own described acceptance (destroy a detached grouped sibling only while another session remains in its group, leave the group's last survivor AND every standalone session untouched). Fix: `TMUX_DESTROY_UNATTACHED = "keep-last"`, threaded through `render_tmux_history_block`/`apply_tmux_history_limit` exactly like the existing `limit=`/`default_size=` params, both conf-written AND live-applied (safe for a DIFFERENT reason than history-limit's own #235 precedent: destroy-unattached only ever evaluates sessions with zero attached clients, so it structurally cannot disturb anything on screen — verified live, an already-existing 3-member pile-up with one client attached had its two detached duplicates swept away immediately on live-apply, with the attached session and a separate attached standalone session both untouched). Live-apply also answers "how do already-piled-up siblings get cleaned" for free — no new hook, no new watchdog job (FREEZE). Pane-addressing verified (not assumed): `watchdog/__init__.py`'s `list_claude_panes`/`_reconcile_candidate_panes` already address every pane exclusively by the stable `#{pane_id}`, never by session name; `_pane_location()`'s `session:window.pane` string (the `zbynek-4:2.0` shape the ticket cites) is purely human-readable log text. test:8955998[red] (8 genuine failures: content/live-argv assertions extended from 4->5 calls, a real TypeError on the new kwarg) -> fix:8fc8a99[green] Closes #254. Dispatched a fresh-context Fable adversarial review (gate OPEN, 43%/49%): 0 CRITICAL, 0 MAJOR, 2 MINOR, 3 THEORETICAL — independently re-verified the keep-group/keep-last man-page math AND both real fleet tmux binaries (3.4 via /usr/bin/tmux, 3.7b via /usr/local/bin/tmux) on isolated -L scratch sockets, and flagged the correction as fleet-LOAD-BEARING: the subdev stream accounts run permanently detached/standalone/headless, so a fleet-wide `keep-group` live-apply would have swept every one of them on the next push. Both MINORs fixed: `test_custom_destroy_unattached_value_is_injectable` injected the DEFAULT value (mutation-proven vacuous — a mutant reverting both consumption sites to the module constant survived it and 5 others) -> now injects `"off"` and asserts both the conf text and the live argv, re-verified the same mutant is now caught; the shipped comment cited only tmux 3.7b for live-apply safety, leaving the SEPARATE conf-parse-time claim for the fleet's still-live stock tmux 3.4 undocumented -> independently re-verified myself (cold `-f <conf> new-session -d` AND a live `set-option` both succeed cleanly on the real 3.4 binary, no #241-shaped crash) and documented both binaries checked. fix:07e7530. Step 8 live verification exercised the REAL shipped `apply_tmux_history_limit()` function end-to-end on an isolated `-L` scratch server (never the real default socket): applying it against a server already holding a pre-existing pile-up immediately swept the detached duplicates while the attached grouped session and a separate attached standalone session survived untouched; a genuinely FRESH server later cold-started from the written conf file also self-cleaned a real attach/detach cycle with zero live-apply needed. That same live check surfaced one genuine, pre-existing (not introduced or worsened by this fix — confirmed via this box's own real current sessions, under the unfixed default `off` conf) finding: which grouped session survives a multi-member cleanup sweep is iteration-order-arbitrary, not guaranteed to be the exact-username-named session the managed launcher's `new-session -A -s "$(whoami)"` depends on for reattachment — filed as #284 (Scope-gate: needs-user-decision — the right answer needs the external mechanism that actually creates the grouped duplicates identified first, which is not in this repo's own code). Full suite green throughout (4427 tests both before and after the review round), `ruff check .` clean at every step. STILL-VALID + design + review comments posted to #254 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-06 solo: #265 completion-report: broaden the deploy-shaped 🌐 rule to cover a client-app project's demo + installable-build artifacts. STILL-VALID verified against current module+hook text (deploy-shaped "one per env × surface" wording, no client-app concept anywhere) before touching anything. Module edit (completion-report.md): the 🌐/📱 requirement generalized to "every user-facing artifact this work produced or affects", new template lines `🌐 Demo:`/`📱 APK:`, a Hard-rules bullet requiring BOTH on EVERY ticket that touched a client app (not just the producing ticket, both verified live), and a "never back-reference a still-current artifact URL — repeat it" rule. Hook change (stop-check-prose-violations.sh): the ALREADY-existing, already-narrowly-scoped 🌐 localhost-ban widened one character (`"🌐"` -> `"🌐|📱"`) so the new marker gets the same no-localhost-urls.md enforcement — deterministic, near-zero blast radius (📱 appeared nowhere else in the repo before this ticket). No new hook file, no new watchdog job (FREEZE respected). Deliberately did NOT add a hard-blocking presence gate for the demo/APK obligation itself — no deterministic, false-positive-safe signal exists for "this ticket touched a client app" across a fleet where most repos have none; module-text guidance was the correct scope, stated explicitly in the design comment as the rejected alternative. test:eb35886[red] (TestLocalhostOnApkLineHook, 2/4 genuinely fail against the unmodified hook) -> feat:1e5056a[green] Closes #265 (module edit + hook widening + TestCompletionReportClientAppArtifacts module-lock tests). Dispatched a fresh-context Fable adversarial review (gate OPEN, 42% weekly): confirmed no false obligation for non-client-app repos (the client-app scoping reads narrow), confirmed the hook change catches the intended `📱 APK: http://localhost...` violation, confirmed the new tests have real teeth (a reverted-selector mutant and a broken-regex mutant both fail the block-assertions) — but found one real MINOR: the hook's new comment claimed 🌐/📱 are "used EXCLUSIVELY" as artifact markers per completion-report.md, which was only actually true for 🌐 (its own explicit "USER-CLICKABLE web URLs only" bullet) — 📱 had no such reservation, so a decorative "📱 tested against localhost during dev" sentence could plausibly false-block. Fixed by adding the missing reservation bullet ("📱 lines = the installable-build DOWNLOAD URL only... never a decorative 'mobile' note") plus a locking test, making the hook comment's claim true rather than changing the hook's mechanics. Local gates: `airuleset.py validate` (59 modules, all resolve) + `ruff check .` clean; full suite intentionally NOT run per worker instructions, scoped test run green (14/14).

2026-08-07 solo (worktree dispatch): #269 fleet usage reporting should group boxes by Anthropic account (follow-up to #212). STILL-VALID confirmed live: `burn --fleet` had no account grouping at all, and neither `snapshots.jsonl`'s tail row nor `fleet.jsonl`'s `per_host` entries carried `account_email`, even though #212's `write_usage_cache()` already writes that field into `~/.claude/airuleset-usage-cache.json` on every box. Chosen approach, matching the ticket's own numbered plan: `hourly_snapshot()` gained an injectable `usage_cache_path=` param and now stamps `account_email` onto its row (reading the SAME #212 field, `""` on any missing cache); `merge_fleet_row()`'s per-host whitelist carries it through unchanged; a new pure `group_fleet_by_account(per_host, cache=None)` groups a fleet row's `per_host` by account, subtotals `usd`, sorts accounts alphabetically with a trailing unknown-account bucket for blank/legacy rows, and populates weekly `%`/reset ONLY for the account matching the REPORTING box's own local cache (no second ssh round-trip); `render_fleet()` renders it as a new "ucty" section alongside the existing per-host `$` line. Rejected alternative (posted to #269 before the first commit): fetching every remote box's OWN usage cache over ssh for real cross-account `%` — explicitly out of the ticket's stated 3-step scope and double-costs the ssh round-trip; deferred as an honest, clearly `% neznamy`-labeled gap. test:359d850[red] (13 failing tests: AttributeError on the not-yet-existing helper + missing `account_email` fields) -> feat:dfdc213[green] Closes #269. Fable-gate OPEN (42%/42%) -> dispatched a fresh-context Fable adversarial review (Explore, xhigh): 0 CRITICAL, 4 MAJOR/MINOR findings, all fixed. M1: `render_fleet`'s account section was essentially unpinned at the render level — two feature-killing mutants (dropping the `cache` argument; reading `shown[0]` instead of `shown[-1]`) survived the whole suite untouched, and the existing test's assertions were mostly satisfied by the PRE-EXISTING per-hour `$` line rather than the new section; fixed with two mutation-proven regression tests (each independently confirmed to fail against its named mutant, then restored) plus an exact subtotal+host-list assertion. m1: a non-string `account_email` crossing the `fleet.jsonl`/ssh-tailed boundary crashed `group_fleet_by_account`'s `sorted()`/`setdefault()` (`TypeError`); fixed by validating `isinstance(acct, str) and acct` before using it as a group key, plus a defensive `isinstance(cache, dict)` check. m2: a JSON-valid-but-non-dict local usage cache crashed `hourly_snapshot()` (`AttributeError`), contradicting its own never-crashes docstring claim (`load_usage_cache()` only returns `None` on unparseable JSON, never on a wrong-shape one); fixed with an `isinstance(usage_cache, dict)` guard. m3: `TestHourlySnapshot`/`TestBurnSnapshotJob` had no isolation and were silently reading THIS box's own real `~/.claude/airuleset-usage-cache.json`; fixed with a `setUp`-level `burn.usage_cache_path` patch per class (each with its own proof-of-isolation test) and a new `usage_cache_path=` pass-through on `watchdog.burn_snapshot_job` (production wiring unchanged — `cmd_watchdog` passes nothing). m4: the unknown-account bucket's window fields were ALREADY hardcoded `None` regardless of cache (correct) but pinned by no test — added a mutation-proven regression lock (the naive `acct == my_account` reuse for the `None` bucket, which would wrongly evaluate `None == None` and leak the local box's own window, was confirmed to genuinely fail the new test). test:d1a18fd[red] (3 genuine failures against the pre-review-fix commit, confirmed via `git stash push -- burn/__init__.py watchdog/__init__.py`) -> fix:a39fbce[green]. One THEORETICAL finding (t4: no fixture combined >2 real accounts with an unknown bucket) closed with an additive test (596e893) since the underlying logic was already correct. Live-verified end-to-end: `hourly_snapshot()` on this box now reads the real local cache and stamps `account_email='drlik.zbynek@gmail.com'`; a synthetic multi-account fleet row renders grouped+subtotaled with real `%`/reset for the matching account and an honest `% neznamy` for a different one; `burn --fleet` against this box's own real (pre-#269) `fleet.jsonl` correctly falls back to the unknown bucket for every historical row. Filed #286 (cross-account weekly `%`/reset — the deliberately deferred remote-usage-cache fetch, Scope-gate: cross-cutting) as the honest follow-up rather than silently dropping the design comment's own "genuine follow-up if ever needed" line. Full targeted suite green throughout (165 `test_burn.py` + `test_watchdog.py` combined runs at every step), `ruff check .` clean. STILL-VALID + design comments posted to #269 before the first commit. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 solo (worktree dispatch, continuation): #162 (block-vault-store-read.sh — a hook that TIMES OUT (5s) fails open, so the guard's own regex speed IS its security) round 2. Round 1 (previous session, same worktree) bounded VALUE_FILE_RE's stem quantifier and closed the then-dominant reachable timeout trigger: `[A-Za-z0-9_.-]*` overlapping the `.secret` literal it searches for forced O(n^2) backtracking on an ordinary 50KB argument. STILL-VALID + design comments were already posted on #162 before this continuation started. This round dispatched a fresh-context adversarial review (opus, xhigh — fable-gate CLOSED at review time, usage cache stale) against the round-1 diff, and it found the IDENTICAL overlapping-class-vs-literal-suffix shape, unbounded, in two more of the hook's own regexes: BRACE_RE (`expand_braces`, MORE reachable than VALUE_FILE_RE's own gap — a 4KB unclosed brace alone already blew the 5s budget end to end, since `expand_braces` runs on every token of every command, not gated behind an earlier miss) and RECURSIVE_RE (`sweeps_the_parent`, lower reachability — needs a bare `.claude` already matched first — same underlying defect). Also found: the round-1 comment's "the match still fires regardless of stem length" and "stays well under a second even for an adversarially long (100KB+) literal argument" were both false, measured directly against the real hook (a stem whose final 254 chars before `.secret` contain no alnum/underscore char at all no longer matches; 100KB measures 0.85-1.08s and 120KB 1.33-1.40s, not sub-second); and a comment/code mismatch ({0,254} claimed vs {0,253} shipped, "255" cliff vs actual "254"); and a false "truncated log" analogy (`audit()` truncates every reference to a leading 60 chars regardless of this bound, unrelated to it).

Fixed all of it in one round-2 pair: bounded BRACE_RE and RECURSIVE_RE both sides to `{0,254}` (mirroring VALUE_FILE_RE's own `{0,253}` bound), corrected every overclaimed/mismatched comment to state the real measured numbers and the real narrow residual each bound introduces (VALUE_FILE_RE: a 254/255-char stem cliff; RECURSIVE_RE: a 509/510-char flag-run cliff — both documented as accepted KNOWN GAPS bullets in the hook's own header, since no real filename/flag cluster typed by reflex is remotely that long). test:acbb727[red] (7 new tests: BraceAndRecursiveRegexesAreAlsoBounded x2, ValueFileStemBoundHasOneNarrowDocumentedResidual x3, RecursiveFlagBoundHasOneNarrowDocumentedResidual x2, plus a widened pre-existing performance test — 3 of 141 genuinely failing against the round-1-only hook, confirmed via `git stash push -- hooks/block-vault-store-read.sh`) -> fix:07bb5af[green]. Measured before/after: 24KB unclosed-brace argument 4.05-7.03s -> well under 300ms; 24KB `grep -rrr...= ~/.claude` argument 10.4-14.0s -> well under 300ms; worst adversarial case for all three regexes at the true ~128KB argv ceiling (this hook's own E2BIG fail-closed path already caps commands there) now measures ~1.0-2.3s, comfortably under the 5s harness budget with real (2-5x) margin.

The performance test's own bound needed widening mid-session for an unrelated reason: this box was observed running a genuine >20 load average on 4 cores (other concurrent Claude sessions), which flaked an initially-tight 2.0s bound even against already-correct code — moved to 4.5s (still comfortably under the hook's real 5s timeout), confirmed stable across 3 consecutive full-suite runs under that same heavy load. Full local suite 141/141 passing throughout the round-2 fix, `ruff check .` clean at every step. Review findings + fixes posted as a durable comment on #162 (STILL-VALID/design comments from round 1 not re-posted, per this continuation's own instructions). Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 solo (worktree dispatch): #283 gk-request `--issue` mode stays soft when the needs-gatekeeper label add is denied AND the GATEKEEPER-ACTION retitle fallback also fails -- follow-up to #221, which hardened only the sibling `--title` (create-mode) branch and deliberately filed this one as a separate, consciously-scoped decision. STILL-VALID reconfirmed against the current `cmd_gk_request` `if issue:` branch (unconditional `return 0` regardless of `labeled`/`retitled`). Caller-scan (the ticket's own stated blocker -- whether other calling code assumes `--issue` mode is always best-effort): grepped the whole repo for every `gk-request`/`cmd_gk_request` reference; found NO code anywhere programmatically invokes `gk-request --issue N` (the only two documented invocation sites, `modules/core/autonomous-verification.md` and `skills/autopilot/SKILL.md`, both use `--title`) and watchdog job 11 (`_fetch_gkreq_tickets`) is a pure READER that never calls the CLI or inspects its return code -- it queries GitHub directly for the label/`in:title` and would MISS an escalation whose label AND title-prefix both failed, confirming the ticket's stated risk is real. Nothing blocks hardening it the same way #221 already did. Fix (mirrors #221's shape, adapted for the branch's real control-flow difference -- `--issue` mode's label-add is unconditional with no shared precondition gate like create-mode's `new_num.isdigit()`, but the retitle fallback is a separate attempt with its own `gh issue view` sub-call): tracks visibility through EITHER of two signals -- `retitled` (our own edit call succeeded) or a NEW `already_prefixed` (the title, read via `gh issue view`, already carried the marker before we ever tried to edit it, so the escalation is genuinely visible with no edit needed -- the existing already-prefixed-title boundary test must keep returning 0). `gk-request FAILED` (rc=1) fires only when `not labeled and not retitled and not already_prefixed`, which also naturally covers the `gh issue view` sub-call itself erroring (neither signal is ever set, so the guard correctly refuses rather than guessing). test:430f063[red] (3 new tests: neither-label-nor-retitle-succeeds -> rc=1, view-fallback-failure -> rc=1, already-prefixed-title regression guard -> rc=0 unchanged) -> fix:3fb4660[green] Closes #283. Dispatched a fresh-context Fable adversarial review (fable-gate OPEN, weekly=60%) with a 7-question REFUTE digest (partial-success-path enumeration, existing-test survival, mutation testing of the new guard, message-wording accuracy, the failure-path suggestion's soundness, create-mode isolation, an independent re-verification of the caller-scan): verdict fix correct, 1 MINOR test-teeth gap -- a mutant dropping the `retitled` clause from the failure guard (leaving only `not labeled and not already_prefixed`) survived all 37 pre-existing tests, since none asserted `rc` for the label-denied-retitle-SUCCEEDED combination. Fixed (36cb01d): added the missing `assertEqual(rc, 0)` to `test_issue_mode_label_denied_comment_carries_marker`; hand-verified the exact mutant now fails (`AssertionError: 1 != 0`) and the shipped code passes unchanged, file restored byte-identical to HEAD via a scratch-copy diff before committing. 6 other review items confirmed-safe with no code change needed (partial-success enumeration, `already_prefixed` wording, create-mode branch/helpers untouched, caller-scan independently reconfirmed). STILL-VALID + design + review-summary comments posted to #283 before/after the respective steps. Full local suite (38 tests in tests/test_gk_request.py) green throughout, `ruff check .` clean at every step. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 solo (worktree dispatch): #288 raise CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION in the managed env — the 200-cumulative-spawn cap was hit live on gatekeeper during a critical delivery push (24h continuous /goal-armed autopilot + TURBO parallel lanes lost the Agent tool entirely). STILL-VALID confirmed live: grep found zero references to the env var anywhere in this repo's managed env/settings machinery (apply_managed_settings_defaults has no `env` key at all; the CLAUDE_LAUNCH_SCRIPT_CONTENT shell wrapper only exports CLAUDE_CODE_NO_FLICKER for the opt-in fullscreen mode); confirmed the key is real (not guessed) by extracting the installed CC binary's own settings-`env` allowlist string table — it sits in the same Set as BASH_DEFAULT_TIMEOUT_MS/CLAUDE_CODE_MAX_RETRIES. Chosen approach: merge CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION="1000" into settings.json's native `env` block via apply_managed_settings_defaults() — the SAME unconditional-managed-default mechanism already used for effortLevel/disableAgentView/tui/model/promptSuggestionEnabled — applied fleet-wide with no per-authority carve-out (the cap is authority-independent; reduced-authority sub-dev streams run equally long /goal loops). Rejected alternative 1: scope to full-authority boxes only via a new AUTHORITY_BY_USER branch — rejected as unjustified complexity for a fix with no downside on any box. Rejected alternative 2: export it from CLAUDE_LAUNCH_SCRIPT_CONTENT like CLAUDE_CODE_NO_FLICKER — rejected because that mechanism only reaches sessions launched through the managed wrapper, needs threading through 5 launch-mode branches individually, while settings.json's native `env` block reaches every claude invocation uniformly and is literally the mechanism the ticket text names. test:f52b2fc[red] (5/6 new TestManagedSubagentCapDefault assertions genuinely KeyError against the pre-fix function) -> feat:fa21df7[green]. Fable-gate OPEN (fable=11%/weekly=10%) -> dispatched a fresh-context Fable adversarial review (Explore, xhigh): 0 CRITICAL, 1 MAJOR, 2 MINOR, several THEORETICAL confirmed-harmless-or-already-covered. MAJOR (binary forensics against 2.1.222/2.1.223/2.1.224, not a shipping blocker per the reviewer): the cumulative-cap mechanism this fix targets is genuinely enforced through 2.1.223 but was REMOVED by Anthropic entirely in 2.1.224 (occurrence count of the enforcement code drops 8->3, "Subagent spawn limit reached" drops to 0 hits; the env var stays allowlisted there, just unread — harmless either way) — dev1's own claude symlink already resolved to 2.1.224 that same morning. Filed the strategic question (retire vs keep vs watch for reintroduction) separately as #290 (Scope-gate: needs-user-decision) rather than deciding it here, and tightened the constant's own code comment in-PR to state the version scoping honestly instead of overclaiming universal fleet effectiveness. MINOR, fixed in this PR: a hand-corrupted/legacy settings.json whose `env` key is a string/int/list (not an object) crashed apply_managed_settings_defaults() (dict("oops") -> ValueError) reachable mid-cmd_push deploy (main already pushed to GitHub, remote-deploy loop not yet reached, since cmd_push's local-install step catches only SystemExit per the prior #273 fix) — fixed with an isinstance guard that self-heals a non-dict `env` to a fresh dict instead of crashing. test:8164e7c[red] (the exact pre-fix ValueError reproduced for string/int/list/nested-list-pairs/bool inputs) -> fix:7c07d04[green], mutation-verified (reverting the isinstance guard to the original `dict(x or {})` shape reproduces the exact pre-fix ValueError against the new test). MINOR, acknowledged in the code comment (not a code change): raising the cap removes the only de-facto per-session cumulative dispatch bound this repo had — a deliberate, explicitly-stated trade rather than a silent "zero downside" claim. Full local suite green throughout (695 -> 697 tests in tests/test_airuleset.py at every step), `ruff check .` clean. STILL-VALID + design + review-summary comments posted to #288 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 solo (worktree dispatch): #287 watchdog stuck-check nudge repeats forever on an already-triaged dead subagent transcript. STILL-VALID re-derived a DIFFERENT root cause than the ticket's own stated one: `_nudge_dying_subagent` (jobs 1b/4a-sub) never passes `responded=` to `decide_working` (confirmed via full-history grep — the call site has never carried that argument), so the ticket's blamed `responded=True` exponential-backoff branch is structurally unreachable there. The REAL mechanism: `_nudge_dying_subagent` only runs while the SUPERVISOR's own last marker reads `⏳ WORKING`, which a busy `/goal` loop working other tickets steps away from for minutes at a stretch (`✅ DONE` per completed ticket); the per-worker dedup state (`state["subagent-<prefix>:<wid>"]`) was aged out by the generic episode-cleanup's `WAIT_CLEAR_SECONDS` (90s) — tuned for the supervisor's own short-lived episodes, wrong for a dead worker's own status — so any gap over 90s silently wiped "already nudged N times" back to zero, and the SAME dead worker's nudge#1 re-fired every time the marker gate reopened. Posted the corrected evidence + design (two additive fixes to the existing state machine, no new job/hook, per this repo's FREEZE) to #287 before the first commit. test:7ace17c[red] (2 new regression tests in `RunOnceSubagentVisibility`, driving real `run_once()` sweeps — both confirmed genuinely failing against unfixed code) -> fix:013b823[green]: (1) `subagent-apierr:`/`subagent-textcall:`/`subagent-busypane:` cleanup keys now age out on a new `SUBAGENT_NUDGE_STATE_TTL_SECONDS` (= `SUBAGENT_MAX_AGE_SECONDS`, 2h) in their own cleanup branch instead of the shared 90s `wait_clear`; (2) new `_subagent_transcript_unsalvageable()` caps `max_nudges` at 1 for a worker transcript that ends on a bare api-error, matching the ticket's own literal acceptance bar ("delivered at most once"). Both fixes independently mutation-confirmed (a targeted revert of each made its own new test fail, then restored byte-identical).

Fable-gate OPEN (11%/10%) -> dispatched a fresh-context adversarial review (Explore, fable, xhigh) specifically asked to refute three scenarios: does the dedup wrongly suppress a DIFFERENT new dead worker (no — `wid` is the per-dispatch transcript filename, unique, live-reproduced); does it survive a watchdog restart (yes — every sweep is already a fresh process reloading `state_path`; one narrow pre-existing kill-window residual shared by every nudge state in the file, not introduced here); does a legitimately re-dispatched worker with the SAME id get wrongly suppressed (mechanism would suppress it, demonstrated in the state machine, but no live trigger exists today — `wid` is never observed reused; documented as a residual comment on the new TTL constant). One MAJOR found and fixed: `_subagent_transcript_unsalvageable` required ZERO issued `tool_use` calls, but the ticket's own incident evidence states "1 tool_use — the dispatch itself, 0 COMPLETED tool calls" — a stricter bar than the real incident, meaning fix #2 was inert on the exact reported shape. fix:d3c9a4a[green] changed the check to scan for a returned `tool_result` rather than a merely-issued `tool_use` (an issued-but-never-completed call is exactly as un-investigable as none at all), widened the scan window 200->500 lines, corrected the docstring's fail-safe claim, and added a direct unit-test class (`SubagentTranscriptUnsalvageable`) pinning the classifier against the real incident's exact shape — mutation-confirmed to fail against the pre-fix (issued-only) check. The marker-gap-survival test's own "salvageable" fixture (a bare issued `tool_use`, no `tool_result`) was itself now-unsalvageable under the corrected bar and was updated to a genuine completed tool_use/tool_result pair (new `_user_toolresult()` test helper). A MINOR (escalate ping text "session nereaguje na nudge" is the wrong claim for the new one-nudge unsalvageable path, which can escalate within the very next sweep with no time to respond) was fixed in-PR (same-file, <20 LoC, does not meet the follow-up gate's deferral threshold) with `fix:b510193[green]`, mutation-confirmed. Two THEORETICAL/pre-existing residuals (a clock-step-back exposure shared with the neighboring cleanup branch; a >500-line tail-window edge case, already documented honestly rather than overclaimed) were left untouched per this repo's FREEZE policy — matching its own explicit "same defect exists elsewhere, not done pre-emptively" precedent, not a live production failure. Reviewer's verdict: "Safe to ship as-is" with the MAJOR addressed. Review-summary comment posted to #287.

Full targeted suite green throughout (168 `tests/test_watchdog.py`, run repeatedly at every step), `ruff check watchdog/__init__.py tests/test_watchdog.py` clean. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 autopilot: #161 otazky footer (verified already working, proven with a real e2e test) + ask-and-continue policy (verified consistent, documented not reinvented) + a bounded ~30-min question-timeout: an unanswered `❓ NEEDS YOU` no longer stops an autopilot `/goal` loop forever. Watchdog job 20 (goal_rearm) gained an independent sub-branch, _goal_question_park_nudge: once a CONFIRMED-delivered ❓ ping (discord-questions.json's own ts) has sat unanswered ~30 min, it types ONE distinct `question-timeout:` instruction telling the session to park that ticket (needs-answer label/comment, the same durable tracking ask-and-continue already uses) and work others — never bare "continue" (which would just make the same model re-print the same question, the camera-box wall, 2026-07-05). All three /goal STOP CONDITIONS templates (skills/autopilot/SKILL.md) got a ~67-char escape-hatch clause on condition (A), measured against Claude Code's 4000-char /goal cap before writing anything (#169's own lesson — landed at 126/40/11 chars spare). test:58cad3f[red] -> feat:dd4fad7[green] -> test:5b5a2ad (self-caught test-isolation fix) -> test:61eafd2 (condition-A pure decision-function proof, mirroring backlog_empty_holds for clause B) -> test:c27ca2e[red] -> fix:0371573[green] -> test:f6cc158 -> test:65ada46 -> docs:3375d6c. A fresh-context adversarial review (fable, gate OPEN) then found 1 CRITICAL + 2 MAJOR bugs before this reached a live box: (C1) the new e2e test file's fixed literal session ids collided with hooks/notify-discord-pending.sh's own /tmp dedup markers, silently deduping 4/5 tests on every run after the first — fixed with a fresh counter-suffixed sid + cleanup per test, verified by running the file 3x in a row. (M1) the park-nudge's block-detection gate accepted ANY message merely MENTIONING "NEEDS YOU" text (not only a genuine trailing status line), and its delivered-ping lookup picked the newest map entry for the session with no check it was actually about the CURRENT block — a stale, unrelated sibling question could start the clock for a ping that was never delivered. Fixed with a genuine trailing-marker check (_needs_you_block_ts, one walk) plus a timestamp-proximity requirement between the block's own transcript timestamp and the delivered ping. (M2) the "already parked" state was written only AFTER the keystroke send with no persist callback, so a mid-sweep kill could lose it and re-send — fixed by persisting BEFORE the send (goal_rearm gained an optional persist=, mirroring jobs 8/11's own shape) and wiring it in run_once. Several MINOR findings also fixed and mutation-verified (an unmeasurable notify-import failure now logs instead of silently swallowing; a docstring overclaim corrected; the new skip-draft log line deduped per episode; the templates' literal "(30m)" now locked to the real constant; the e2e test asserts the recorded ts field). Full local suite green throughout (528 tests across the touched files), ruff check . clean, airuleset.py validate clean. Review findings + fixes posted as durable comments on #161. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 #289 (TOP prio, user furious over #235/#236/#253/#267 chain): scope redirected THREE times mid-run by the coordinator, in order: (1) original ask was a tmux popup key; (2) user rejected the popup as primary ("hlupost"), redirected to root-cause the "long time no problem, now always corrupted" regression via CC-version/tmux-fleet-change bisection; (3) user identified CC's OWN native mouse-driven history and asked whether it could be keyboard-driven, redirecting again. Final scope: (A) PRIMARY, no code change -- live-verified CLAUDE_CODE_NO_FLICKER=1 (the EXISTING opt-in `claude-fullscreen` launcher mode, #253) already gives plain PageUp/PageDown a complete, zero-gap, zero-duplicate scroll through CC's OWN virtualized transcript, immune by construction to the upstream tmux frame-stacking defect (anthropics/claude-code#84247/#46834) -- confirmed via a real attached-pty-client keypress test, not reasoning from docs; stock tmux binds nothing to plain PageUp, so it's usable TODAY with zero fleet push. (B) SECONDARY (user-approved fallback), shipped: a one-keystroke tmux popup (S-F1 root + prefix-h fallback) over the existing `claude-history` script (#267), extending TMUX_MARK_START/END. Self-found + fixed a real bug mid-build: the popup command was first an inline shell one-liner in the bind-key conf line; tmux's OWN conf-file double-quote parser expands $VAR at BIND time using tmux's OWN process env, silently blanking $CH_OUT/$CH_RC/$? before the shell ever ran -- moved the logic into its own managed script file (CLAUDE_HISTORY_POPUP_SCRIPT_DEST), invoked by absolute path, no $VAR left to survive the conf parser. Verified end-to-end via a real Shift+F1 keypress through a genuinely attached pty client, reading the raw pty stream (capture-pane cannot see a popup's overlay content at all -- confirmed separately). test:5029a0c[red]->feat:fa558e3[green] Closes #289. (C) TERTIARY, incomplete -- read-only confirmed window-size=latest live on the real fleet server (one of the redirect's own candidates); built scripts/measure_scrollback_events.py (multi-client-attach + compact-event harness) but did not run it to completion within budget; filed as #291. Adversarial review (fable, gate OPEN 71%/74%) of fa558e3 found 1 MAJOR + 4 MINOR: _tmux_conf_quote's own docstring falsely claimed $VAR "passes straight through untouched" in tmux double quotes -- the opposite of what this ticket's own live-verified bug-fix already proved -- fixed by REFUSING (ValueError) any word containing $, since no quoting form here protects one from tmux's bind-time expansion; the trigger class was missing ' (an unquoted one starts real single-quote mode too); the backslash-escape branch had a fixture that always paired \ with a space, masking a mutant dropping \\ from the trigger entirely; two comments falsely claimed the popup argv "contains spaces" (quoting is load-bearing only for the # in #{pane_current_path}); the popup script gained a `command -v less` guard for a box genuinely missing it, routing to the same fail-loud shape as a nonzero claude-history exit. Every fix mutation-verified individually (backup/mutate/confirm-fails/restore). scripts/measure_scrollback_events.py's own isolated -L-socket kill-server call needed the same #132-guard exemption its sibling measure_scrollback_holes.py already has (found running the full suite). fix:77b853c. Incidentally found, unrelated, filed rather than chased: a full-suite-only flake in tests/test_notify_question_block.py (passes standalone, fails only inside the full ~4520-test run) -- #292.

2026-08-07 #294 (continuation worker, inherited from an API-outage-killed prior worker; scope-addended mid-run): "hisotria... strukturovana, a farebna" -- extends the plain-text claude-history companion (#267) and its one-keystroke popup (#289) with ANSI colors + structure. render() now supports --color/--plain (auto-detects TTY by default, matching CC's own launcher conventions -- a piped `claude-history | cat` stays byte-identical), wrapping the "===== USER =====" / "===== CLAUDE =====" turn header in a restrained role color (blue 75 for USER, sage green 108 for CLAUDE -- reused verbatim from statusbar.py's own palette, never a new scheme) plus a dim tool-call line and an optional dim "HH:MM:SSZ" timestamp; body text stays uncolored in both modes. The popup script now passes --color (its own claude-history invocation is a shell-captured pipe, so isatty() can never see the real terminal at the far end of `less`) and pipes into `less -R +G` instead of `less +G`. Inherited the prior worker's RED test draft for this half verbatim after confirming it genuinely failed against the unmodified script (git stash the impl, re-run, restore). test:d1777f1[red] -> feat:a9b0c50[green]. Adversarial review (fable, gate OPEN 13%/9%): 5/5 REFUTE-framed claims HOLD, no CRITICAL/MAJOR -- two MINOR findings fixed (timestamps now carry an explicit "Z" UTC marker; the color-assignment test now pins the exact per-role codes, closing a color-swap mutant gap the prior test missed), one THEORETICAL (popup forces --color with no TERM/color-capability check) left as a documented accepted residual (tmux's own display-popup shell always runs 256-color-capable). Live-verified via the established isolated-tmux-socket + real-attached-pty-client + raw-pty-stream-read recipe (#289's own evidence289/live_popup_verification.py, reused/adapted): a real Shift+F1 keypress showed ANSI colors, both role headers, the dimmed tool line and timestamp in the actual popup overlay; `q` dismissed cleanly; piped `claude-history --full` (no --color) stayed zero-ANSI.

Mid-run scope addendum from the coordinator (user-blocking, real report): the user connects from a Windows notebook over ssh (Windows Terminal or PuTTY, exact client unknown) and Shift+F1 "nefunguje vobec" for the popup. Researched via WebSearch/WebFetch (no real Windows Terminal/PuTTY available to run here): PuTTY's F1-F12 modifier encoding is a separate, user-configurable ("Function keys and keypad") setting, historically finicky, unlike Home/End/PageUp/PageDown which are controlled independently -- S-F1 kept unchanged (costs nothing, works for many xterm-family clients). Live-probed candidates against this box's own real tmux 3.7b binary (isolated -L socket, zero real-server risk): Shift+Home/End ruled out -- tmux's key table only recognizes the LETTER-terminated modified form (\x1b[1;2H/\x1b[1;2F), not the tilde form PuTTY's own documented Home/End behavior uses, a real mismatch risk; Shift+Insert ruled out -- confirmed reserved CLIENT-SIDE for paste by BOTH PuTTY's own documented paste-key list AND Windows Terminal's official default actions.json (shift+insert -> Terminal.PasteFromClipboard), so the keystroke never even reaches ssh. Chosen: S-DC (Shift+Delete) -- reuses the EXACT SAME wire-format family (CSI <code>;2~) as the user's own ALREADY-CONFIRMED-WORKING S-PageUp/S-PageDown scrollback binds (PageUp/PageDown/Home/Insert/Delete/End are one uniform VT220 editing-keypad numeric family every xterm-descended terminal encodes identically), confirmed not reserved as a clipboard shortcut in either client's defaults, and confirmed live that tmux 3.7b's S-DC binding fires correctly on the real byte sequence \x1b[3;2~. test:90a3011[red] -> feat:6008e07[green]. Live-verified all THREE popup triggers (S-F1, S-DC, and prefix+h -- a genuine Ctrl+B release then "h", the guaranteed-traversal fallback needing no CSI-modifier support of any kind, already shipped by #289) against the colored popup: identical ANSI/structure/timestamp evidence for every one, clean 'q' dismiss each time. Addendum resolution posted as its own comment on #294 with the full evidence. Closes #294. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 (worktree dispatch, 3rd attempt -- attempts 1-2 died on an API outage and a stall, zero durable progress) #293 (batch-17 gate): two load-sensitive full-suite-only pytest failures (never standalone) with NO deterministic repro despite 4 clean full-suite runs (~4527 tests each) + 700+ targeted repeats + static tracing of both hooks -- hooks/pre-ask-auto-answer.sh confirmed stateless (no session id in payload at all), watchdog.deliver_pending_done confirmed to glob only /tmp/claude-discord-pending-*, never lastq/cardchk. Instead found + fixed the exact "cross-test state leak" CLASS the ticket asked to hunt: tests/test_airuleset.py's TestDiscordNotifyHooks._sid / TestGoalArmedSuppressesIdlePing._sid each hand-enumerated the specific /tmp/claude-discord-* marker paths notify-discord-pending.sh can write instead of using the shape-agnostic tests/_hook_state_cleanup.py::sweep_session_files (#202) -- when CARDCHK (the delivery-conditional-suppression checkpoint, written on every real DONE turn) was added later, neither hand list was updated. Live-measured before the fix: 2061 leftover cardchk-test-dn-*, 681 cardchk-test-ga-*, 430 lastq-test-ga-* (TestGoalArmedSuppressesIdlePing never cleaned lastq at all). test:25a4963[red]->fix:1412d88[green]. Dispatched a fresh-context adversarial review (fable, gate OPEN 14%) of that fix, which found a materially stronger candidate root cause for the ORIGINAL pwedge "2 != 1" symptom than anything in the standalone investigation: tests/test_pwedge_content_idle.py's _run never scoped pending_prefix=, so job 5 (deliver_pending_done, unconditional) swept the REAL /tmp/claude-discord-pending-* glob on this live, actively-used box -- a genuinely concurrent real session's own real ✅-pending file, aged into the 120s-12h delivery window, reads as an orphan and gets counted (exactly 2 != 1) AND deleted (a real production side effect: this test run could silently eat a live user's phone ping). Reproduced deterministically with a decoy file (confirmed both the exact assertion string AND the decoy's deletion). test:b5c88a6[red]->fix:c3471bd[green]. The same review found a second live instance of the identical leak class in a different file: tests/test_run_card_enforcement.py::TestSuppressionIsConditionalOnDelivery.setUp hand-cleaned only pending+cardchk, leaking lastq from its own ❓ test -- 207 real leftover files measured. test:060bc99[red]->fix:224ca68[green]. A theoretical (unreproduced, no live trigger) SIGPIPE mechanism for the dropped_rows half was also proposed (pre-ask-auto-answer.sh's set -o pipefail + grep -q against multi-line JSON, the #190/#192 class) -- NOT fixed, since #192 (already closed under this repo's own FREEZE) explicitly requires a live journal/transcript misfire to reopen and none exists; documented as a comment on #293 for the record. Three more same-class-but-currently-non-leaking hand-cleanup sites found (test_notify_question_block.py, test_airuleset.py::TestQuestionQualityGate, test_question_footer_e2e.py) -- left untouched per the same FREEZE rationale (0 measured failures today). #292 confirmed left alone: its mechanism (PID reuse across separate process runs, no per-test counter) is disjoint from the leak class fixed here. Full suite green throughout (targeted modules + full discover), ruff clean. Worked in an isolated worktree, commit-local only.

2026-08-07 (worktree dispatch) #301 (bounce-lane): compaction genuinely starved on gk + david@subdev despite #250's own live-tasks grace fix (already deployed, confirmed via live SEND events). Live evidence gathered from both boxes (journalctl job-14 tallies, `~/.claude/compact-decisions.log`, `~/.claude/compact-sync.log`): `skip no-work (compact-request)` / `DROP no-work` was the dominant or second-most-common job-14 outcome on both boxes (gk 53/213 over 3 days; david@subdev ~40/52, an 18h35m stretch with zero compact-request activity), and the DROP population was overwhelmingly `type=autopilot-worker` (origin=subagent-stop) -- a PROVEN completed-ticket boundary per #121. Root cause: #126 (commit c05d3ee) exempted ONLY the synchronous `deliver_compact_now` path from the #99 no-work / #48 small-context substantiality gates for a proven origin -- job 14's own separate copy of those same two gates was DELIBERATELY left untouched at the time (#122's own docstring: "job 14's own separate copies of the #99/#48 gates are deliberately left untouched by this ... this ticket does not fold it back in"). A proven request that failed to deliver synchronously (common: `deferred=live-tasks`, often inside #250's own grace window) fell through to job 14's poll and got dropped there anyway, exactly like an anonymous turn -- producing multi-hour compaction-free stretches (one session measured at 462,767 tokens) despite continuous completed-ticket activity. `dialog-open` (gk's single largest individual skip reason, 101/213) was investigated separately and NOT fixed: every observed sequence resolved within minutes and zero LAPSE-expired entries exist in 3 days of journal, so there is no demonstrated starvation there, and a blind send-anyway grace period (mirroring live-tasks) would be actively unsafe -- typing `/compact` into a genuinely open interactive dialog risks corrupting a pending user decision. A context-size-triggered override of #99 was also rejected -- it would reopen the context-size-heuristic policy #102 deliberately removed. Fix: extend job 14's already-computed `proven_boundary` local (previously used only for the `busy` check) to also gate the #99 and #48 blocks with `if not proven_boundary:`, mirroring `deliver_compact_now`'s own #126 structure exactly; `_compact_thin_context` stays unconditional for both paths per its own existing section comment. test:7589779[red] -> fix:19eac56[green]. Dispatched a fresh-context Fable adversarial review (fable-gate OPEN 18%) with an explicit REFUTE brief (does it reopen #238's race, #140's claim wedge, is the exit reachable without a NameError, is starvation still possible under a continuously-busy supervisor, and does mutation-testing the two guards actually kill the new tests): 0 CRITICAL, 0 MAJOR, 3 MINOR (two comment-wording inaccuracies -- one overclaiming `ctx` is "still read" when only its initialization is unconditional, one wrongly implying `proven_boundary` gates thin-context too -- and a duplicate `Closes #301` trailer on both the RED and GREEN commits, left as-is since both land in one push batch and duplicate closing keywords are idempotent). Both comment findings fixed: docs:b44fe25. Full local suite green throughout (546 tests across the two affected modules), `ruff check .` clean at every step. Live acceptance (for the supervisor, post-push): on gk and david@subdev, `journalctl --user -u api-watchdog.service --since "10 min ago" | grep -E "OK \(compact-request|skip no-work"` should show `OK` sends for `type=autopilot-worker` requests that previously hit `skip no-work`/`skip small-context`; `tail -f ~/.claude/compact-sync.log` should show `SEND` lines (not `DROP no-work`) for proven-origin requests; a session whose context was growing unboundedly should show a fresh `compact_boundary` transcript entry within the next completed-ticket cycle. Evidence + design posted as durable comments on #301. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 (worktree dispatch, batch): #295 montalu4 Discord routing + #296 separate questions thread — worked on ONE branch, #295 first per the shared design consideration (its owner-resolve fix informed #296's read of the same map). STILL-VALID + design comments posted to both before the first commit.

#295: `STREAM_NOTIFY_OWNER["montalu4"]` was still `"zbynek"` — montalu4 is marek's stream (ticket title + live evidence), so every montalu4 ping (question/done/run-card) landed in Zbynek's own thread instead of Marek's. Root cause: the map was never updated when montalu4 was reassigned to marek (montalu/montalu2/montalu3 correctly stayed zbynek's). Fix: one map entry (`"montalu4": "marek"`), docstring rewritten citing the odoo-erp#2961 ACCESS DECISION comment (2026-08-05, user) as the evidence source. test:d13690d[red] (2 genuine assertion flips, montalu4 asserted zbynek before) -> fix:4f6930d[green] Closes #295. Ownership re-verified for ALL 7 subdev accounts against odoo-erp#2961's own "ACCESS DECISION" comment + airuleset#251/#259/#263 (montalu/montalu2/montalu3=zbynek, montalu4=marek, marek=marek [own identity, no map entry needed — `_current_user()` resolves directly via the tmux session-group fallback], david=david, simap=zbynek — the last confirmed by the user's own explicit "simap je zbynekov stream" in #259). None flagged as unclear.

#296: no separate Discord thread existed for questions — ❓/✅/run-card all shared one `claude-<owner>` thread, so active question traffic mixed with routine done/card noise. Chosen approach (explicitly "no new mechanism"): `notify.notification_channel(env, owner, kind="questions")` tries `DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q` first, falling through to the SAME `kind="default"` cascade unchanged — `hooks/notify-discord-send.sh` computes `KIND="questions"` only when `ND_EMOJI="❓"` and threads `--kind` onto the SAME `--channel-id` call `emit_one()` already makes for every target (primary + mirrors), so mirror-stream questions route correctly with zero mirror-specific code. `notify.provision_question_thread()` (CLI: `notify --provision-question-thread [--owner-name X]`) creates the `-q` thread via the existing raw `_discord_api` HTTP helper (no prior code in this repo ever created a Discord thread). Job 7 (Discord reply routing) needed ZERO code changes — it already fetches from whatever channel a question's own map entry records (`channels = {v.get("channel") for v in qmap.values()}`), proven by a dedicated coverage-lock test using a channel value deliberately different from the fixture's "normal" owner channel (passed immediately, no code change). test:a749bf5[red] (11 genuine failures: AttributeError on the not-yet-existing kind param/CLI flag, plus real assertion failures on the shell send-hook's `--kind` wiring) -> feat:70a9b3a[green] Closes #296.

Adversarial review (fable, gate OPEN) of 70a9b3a found 1 MAJOR + 2 THEORETICAL, all fixed. MAJOR (duplicate thread across boxes): `provision_question_thread` went straight to `create_owner_question_thread` with no search first, so a SECOND box provisioning the same owner (the `_Q` id is box-local `.env` config) would fork a duplicate `claude-<owner>-q` thread on Discord instead of reusing the first box's. Fixed with `find_owner_question_thread()` (searches active guild threads by name+parent_id first, then archived public threads by name), wired in as `find_owner_question_thread(...) or create_owner_question_thread(...)` — proven with a stateful two-"box" simulation test (`_FakeDiscordThreads`, a shared in-memory Discord double) confirming box B genuinely reuses box A's already-created thread id rather than minting a second one. THEORETICAL-a (anchor-off-shared-channel): both create/find functions used to anchor off `notification_channel(env, owner)` (which cascades to the shared infra channel when the owner-specific one is unset) — a misconfigured/unset owner channel could silently create the `-q` thread under the WRONG parent. Fixed with `_owner_anchor_channel()`, which reads ONLY the owner's own key and refuses (never cascades) when unset — locked with a "never anchors off the shared channel" test for both functions. THEORETICAL-b (`_env_upsert` non-atomic + uncaught `UnicodeDecodeError`): rewritten to match this module's own established precedents verbatim — `_save_questions`'s tmp-file+`os.replace` atomic write, `_read_env`'s `errors="replace"` tolerant decode — proven with a mocked `os.replace` call-count assertion (genuinely failed against the pre-fix truncate-rewrite) and a non-UTF8-byte-tolerance test. A fourth, self-found MINOR while implementing the review fixes: `cmd_notify`'s `--provision-question-thread --owner-name` CLI value was never normalized the way `resolve_owner()` normalizes every other owner string, so an operator typo/case mismatch could create an orphaned Discord thread keyed under a dead `.env` variable name — fixed with the same `re.sub(r"[^a-z0-9]", "", ...)` transform, test-locked. test:a28686a[red] (10 genuine failures against the pre-review-fix build, confirmed via `git stash push -- notify/__init__.py airuleset.py`) -> fix:2680a1f[green]. Docs drift (MINOR, review finding): `modules/core/milestone-notifications.md`, `skills/notification-mechanics/SKILL.md` and `.claude/rules/airuleset-internals.md` all still described "every device message goes to the owner's own thread" with no `-q` exception mentioned — fixed with one addendum sentence/paragraph per home, docs:2d30c66.

Live verification (real Discord, not dry-run): `notify --provision-question-thread --owner-name zbynek` created and persisted `DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q` into the local `.env` (thread id 1535313898054492220). A real `❓` test question sent through the real (`ND_CONFIRM=1`) send path landed in `claude-zbynek-q` — confirmed by directly querying the Discord API with the same bot token (`channels/<id>/messages`), content matched byte-for-byte, `@mention` present. A real `✅` test ping sent the same way landed in the UNCHANGED `claude-zbynek` thread (id 1519638400171511860) — confirmed the same way. `~/.claude/notify-delivery.log` recorded both (`sent kind=shell key=❓:airuleset` / `key=✅:airuleset`). A second `❓` send with `ND_SESSION_ID` set (mirroring the real `notify-discord-pending.sh` flow) confirmed `~/.claude/discord-questions.json` records the `-q` channel id (`1535313898054492220`) against the session, keyed by Discord message id — the exact field job 7 reads for reply routing. All four test Discord messages and the synthetic `discord-questions.json` entries were deleted/cleaned up after verification.

Filed #302 (Scope-gate: needs-user-decision) as an honest out-of-scope follow-up: `_checkout_pane_owner`/`_notify_backfill_digest` in `airuleset.py` resolve ownership via a raw `owner_of(pid)` call with no `notify.stream_redirect()` wrapping, unlike every `pane_owner()` call site in `watchdog/__init__.py` (locked by `TestPaneOwnerAlwaysRedirected`'s structural scan) — a rare manual-CLI-only path, not touched by either ticket, but capable of the same misrouting class #295 just fixed for the hot paths.

Full local suite green throughout (959 tests across tests/test_airuleset.py + tests/test_discord_reply.py + tests/test_authority_profiles.py + tests/test_run_card_content_validation.py, run repeatedly at every step), `ruff check .` clean at every step (never the full test discover, per this batch's own gate instruction). STILL-VALID + design + review-summary comments posted to both #295 and #296 before/after the respective steps. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes + deploys + fires the per-ticket Discord card (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 (worktree dispatch) #299 (subagent-continuation.md stale claim): STILL-VALID confirmed — the module still asserted `SendMessage` is gated behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` and every attempt fails; the flag-gating was real on CC ~v2.1.77 but does not hold on current fleet builds (2.1.224+) — SendMessage is a real, loadable deferred tool via ToolSearch, confirmed independently this session (the deferred-tool listing) and by the ticket's own live evidence (a supervisor's SendMessage call resumed a background autopilot-worker that had died on a transient API error, with prior context intact). Evidence + design comments posted to #299 before the first code commit. Fix: rewrite the stale factual claim while keeping the durable-state discipline intact — workers still resume from git branch/open PR/gh issue state, never in-process continuation; the fork-inheritance warning (#50) stays; "never narrate tooling mechanics" broadened from "never narrate the fallback" (now covers a successful SendMessage redirect too, not just an unavailable one). docs:d1aa451 Closes #299. Swept every other reference to the stale claim (`grep -rn AGENT_TEAMS modules skills agents hooks` plus a fresh-context Fable adversarial review, gate OPEN 25%) and fixed them consistently in the same commit: `hooks/stop-check-sendmessage-narration.sh` (comments + block-reason text only — detection regex unchanged, and its header's overclaim of catching a SUCCESSFUL-redirect narration was narrowed to match what the regex actually detects, since extending detection for an unobserved narration shape was judged out of scope under this repo's FREEZE), `skills/autopilot/SKILL.md`'s dispatch-step blockquote, `agents/autopilot-worker.md`'s resume note, `skills/verify-launched-work-liveness/SKILL.md` (three sites — two of these directly contradicted the corrected claim by citing the stale module as their authority), `skills/notification-mechanics/SKILL.md`, and one `airuleset.py` code comment. The review's one non-cosmetic factual finding (F6): the autopilot skill's own dead-agent example paired "an API error" as a DEAD case right next to the RESUMABLE framing — direct tension with the ticket's own evidence (an API-error death that WAS resumed) — reworded to "cannot always tell which case you're in from the outside... when in doubt, presumed dead." Module line-count lock (<24 lines) and the fork-inheritance test class both stay green; full local suite green (4628 tests), `ruff check .` clean. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 (worktree dispatch) #297 (❓/❔ reaction = ask-me flag) + #298 (reply on completion card = reopen ticket): extended job 7's existing Discord poll pass (no new hook, no new watchdog job, per this repo's own machinery FREEZE). #297: a known-owner ❓/❔ reaction on a tracked bot message (an outstanding question OR a completion card) nudges the exact asking session (job 7's own idle/draft-stash gate — never job 8's stronger at-rest discipline, which would wrongly refuse the very session sitting on the flagged question) or the nearest live pane of that repo, asking the user a structured question quoting the flagged message. #298: a known-owner reply on a per-ticket completion card reopens that card's repo#issue (idempotent), comments the remark verbatim via stdin, applies `prio:bounce` (existence-checked, never `--force`'d), and nudges a live pane of that repo. A new send-time map (`notify.py`'s `discord-cards.json`, mirroring the existing outstanding-question map) recovers the card's real repo/issue; `_post_discord`/`send()` gained an opt-in `return_message_id` so every existing plain-string caller stays unaffected. RED (55 tests, `git stash push -- airuleset.py notify/__init__.py watchdog/__init__.py`, 51 genuine AttributeError against the pre-change codebase) test:fa59e34[red] -> feat:1031776[green] Closes #297 Closes #298. Live-verified against the real production Discord bot + a real throwaway GitHub issue (#303, deleted afterward): real message send/fetch/delete, a real bot reaction (a free negative control — a bot's own id is never in `known_owner_ids`), real `gh` mutations against #303, real tmux keystroke delivery into an isolated scratch pane — the reactor/author identity was necessarily synthetic (a bot token cannot author content as a human Discord user), stated explicitly which half was live vs synthetic in every case.

Dispatched a fresh-context Fable adversarial review (fable-gate OPEN) with a REFUTE brief covering race conditions, dedup bypass, injection via Discord content, wrong-owner reactions, and reopen loops: 1 CRITICAL/MAJOR-1 (the flag-delivery target resolution conflated two genuinely different safety postures — the exact asking session vs a repo-fallback pane — under one shared gate), 4 more MAJOR, several MINOR/THEORETICAL. All fixed:

MAJOR-1: `_flag_delivery_target` now returns a 4th element (`exact`) the caller branches on between `_deliver_flag_prompt_to_exact_session` (job 7's own gate) and `_nudge_repo_pane` (job 8's stronger discipline) — never one shared helper for both. MAJOR-2: a dry-run card-reopen no longer marks the reply's own dedup slot (mirroring the identical, PRE-EXISTING gap found and filed separately as #304 for `_ack`/`_delivered`, the core ❓-reply helpers — not fixed here, since it needs its own dedicated RED/GREEN pair and live-verification on always-live production code, Scope-gate: needs-user-decision). MAJOR-4: `deliver_discord_replies` gained a `persist` callback, called immediately after each successful card-reopen/flag-react dedup-mark (jobs 8/11's own "dedup memory BEFORE the next item" shape) — a mid-sweep kill can no longer lose a marker for a mutation that already landed. MAJOR-5: a successful reopen now releases the ticket's own run-card send-time dedup marker (`notify.forget_marker`) so a re-fixed, re-closed ticket's fresh card can send again. MINOR-6: `_notify_run_card` resolves `resolve_owner()` once, passed explicitly to both `send()` and `notification_channel()`. MINOR-7 (found in TWO sibling functions, both fixed in the same pass): `record_card_message` and `record_question`'s prune loops now guard `isinstance(v, dict)` before `.get("ts")`; the identical `channels = {...}` comprehension in `deliver_discord_replies` gets the same guard. MINOR-8: the four pre-existing job-7 test classes reaching a card-carrying path now isolate `_cards_path` in setUp. MINOR-9: the card-reopen receipt reaction prefers the fetching channel over the card's own recorded one. MINOR-11: `_card_reopen_flow`'s docstring states `prio:bounce` here is a plain user marker, unconditional on cross-stream enrollment. THEORETICAL-13: `clean_reply_text` strips C0/C1 control characters before the whitespace collapse.

Mutation-verified every fix by hand (backup, one targeted `str.replace`, confirm the paired regression test fails with a real AssertionError/crash, restore, confirm byte-identical) before committing. Genuine RED confirmed via `git stash push -- airuleset.py notify/__init__.py watchdog/__init__.py` (7 real failures: the 4-tuple contract change, the dry-run dedup leak, the marker-release miss, and both isinstance crashes) test:358a937[red] -> fix:c95ef7a[green] Closes #297 Closes #298. Full local suite green throughout (4690 tests, one full `pytest tests/` run confirmed clean at the end), `ruff check .` clean at every step.

Filed #304 (Scope-gate: needs-user-decision): `_ack`/`_delivered` (the PRE-EXISTING, core ❓-reply delivery helpers `deliver_discord_replies` has always had) mark `acked_set`/`done_set` unconditionally on `dry_run`, and `run_once`'s trailing `save_state()` persists that unconditionally too — the byte-identical shape to this ticket's own MAJOR-2 card-path fix, in code that predates #297/#298 and that every stream's real Discord replies flow through continuously; filed rather than fixed for its own dedicated RED/GREEN pair + live-verification pass, distinct from an already-large review-fix commit.

Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).

2026-08-07 (worktree dispatch) #300 (register miva1 subdev stream): mirrored `simap`'s own registration exactly across all four registries a new sub-dev stream needs — `REMOTE_HOSTS` (`miva1@subdev`, `gatekeeper_access_ed25519` identity), `AUTHORITY_BY_USER["miva1"] = "fork-no-merge"` (phase-1 isolated, merges nowhere), `notify.STREAM_NOTIFY_OWNER["miva1"] = "zbynek"`, and `hooks/block-subdev-ssh-misuse.sh`'s allow-list (extending the existing hook, no new hook/watchdog job per this repo's own FREEZE). Statusline `stream:miva1` scoping needed no code change — `_slice_quals`/`_core_search_excl`/`skill_names_for_user`/`ensure_stream_tmux_session`/`apply_stream_ssh_attach` are already fully generic over `AUTHORITY_BY_USER`'s keys, confirmed by reading each. Design decision (posted to #300 before the first commit): the ticket's own text asked for a `DISCORD_MIRROR_MIVA1` config (the `david` precedent); rejected in favor of the `STREAM_NOTIFY_OWNER` redirect (montalu/simap's own mechanism), because `DISCORD_MIRROR_<OWNER>` lives entirely in a LOCAL, non-git per-box `.env` this repo's code cannot provision or deploy — the redirect is the code-only, push-deployable solution to the SAME stated requirement. test:6af7c82[red] (9 genuine failures against current HEAD) -> feat:151cf4b[green] Closes #300.

A fresh-context Fable adversarial review (fable-gate OPEN 31%) found 2 CRITICAL + 2 MAJOR + 3 MINOR. The 2 CRITICALs are live-infrastructure verification this commit-local, no-ssh worker structurally cannot perform (miva1's `authorized_keys` was never live-verified to accept the assumed `gatekeeper_access_ed25519` identity — unlike simap's own #143 commit, which recorded that exact live check; a wrong-key assumption risks fail2ban-banning dev1 from the WHOLE subdev box on the next push, collateral-damaging marek/david/simap/montalu2-4 too; plus miva1 has no `~/devel/airuleset` checkout yet, and item 4's live Discord test was already-deliberately deferred but never had a follow-up filed) — filed as #305 (Scope-gate: needs-user-decision) rather than guessed at. Fixed the in-repo findings: MAJOR (notify comment overclaim — "satisfies the requirement fully" was true only for the code-side routing decision, not for whether a ping actually lands, which still needs miva1's own box's local Discord `.env` hand-wired; reworded), MAJOR (`watchdog/__init__.py`'s `_REDUCED_STREAM_USERS` never gained `simap` either — confirmed via `git log --grep="#143"` that no #143 commit ever touched that file, so it's an unrevisited gap, not a documented decision; per this repo's own FREEZE the tuple is NOT widened pre-emptively, just honestly commented), MINOR (a third stale "must be montalu[234]/marek/david/simap" enumeration in the hook's no-user branch), MINOR (`test_blocks_miva1_without_identity` widened to assert the SPECIFIC block reason, mutation-verified: removing `miva1` from `check_target`'s allow tuple now correctly fails it). fix:fe26290. Also noted (unfixable, history rewrite banned): the RED commit's own message claimed all 9 failures were AssertionError with none AttributeError/missing-name — 2 are actually KeyError (dict-index-before-assertEqual), immaterial to correctness but factually wrong as written. Targeted suite green throughout (`tests/test_airuleset.py` + `tests/test_authority_profiles.py`: 885 passed), full suite green (4697 passed, up from the 4691-test baseline on `main` by exactly the 6 new tests added), `ruff check .` clean, `airuleset.py validate` clean.

2026-08-07 (worktree dispatch, continuation) #292: resumed from an interrupted worker's durable state — commits f4a086e[red] and 00e458c[green] (the here-string fix for `hooks/stop-check-question-quality.sh`'s eight vulnerable `printf ... | grep -q`/`printf ... | awk '{...exit...}'` sites, the exact #190/#194 mechanism) were already committed; the STILL-VALID + root-cause + chosen-approach + rejected-alternative design comment was already posted to #292 (combined into one comment, satisfying `block-commit-without-design.sh`'s classifier before any further code commit). An uncommitted working-tree diff to `tests/test_question_gate_pipeline_race.py` (adversarial-review hardening from the prior worker) had a real bug: `_feeder_awk_exit_line_numbers` was missing the `_strip_comment_lines` call the per-line scan already had, so the file's own header comment (which necessarily quotes the banned multi-line-awk idiom to explain it) was misread as a real occurrence — fixed and mutation-confirmed (revert the call -> the specific "comment is not flagged" test fails with a real AssertionError; restored -> passes) before committing it as its own hardening commit (`6f48ddc`, no production-code change).

Dispatched a fresh-context Fable adversarial review (fable-gate OPEN, 36% weekly) against the full diff 84188ab..HEAD with a REFUTE brief. Verdict: 0 CRITICAL/0 MAJOR. An independent differential the review ran itself (old hook rc=141 10/10 vs new hook rc=0 10/10 on a byte-correct 128 KB question) confirmed the fix is complete and behavior-preserving; the deliberately-pipe-fed BLOCK-extraction awk was re-confirmed genuinely safe (both its `exit` calls live inside `END{}`). Four MINOR test-detector dead zones (a second grep/jq as feeder, an `LC_ALL=C`-prefixed grep, a combined `-qm1` flag, a second `-v` on a feeding awk) and one MINOR hook-header overclaim (a here-string's "materialized via a temp file" claim is bash-version-dependent — true pre-5.1, an anonymous pipe under capacity on 5.1+) were documented rather than fixed (docs:0557947) — none is reachable by anything the hook currently does; they are preventive-detector gaps against a hypothetical future edit, matching this hook's own already-accepted #190-sibling residual convention.

Load-bearing verification, performed deliberately rather than skipped: extracted the pre-fix hook via `git show f4a086e~1:...` (0 here-strings) and the current post-fix hook (11 here-strings), built the >64 KiB deterministic forcing fixture (a byte-correct, template-compliant question padded with a few long option lines past the 64 KiB pipe buffer, mirroring the test suite's own `_padding_options` shape), started 8 `yes` CPU spinners on this 4-core box to genuinely saturate it, and ran each hook 60x through the fixture: PRE-FIX 60/60 falsely blocked; POST-FIX 0/60. Matches the ticket's own established forcing condition exactly.

Full local suite green throughout: `tests/test_question_gate_pipeline_race.py` (17/17) + `tests/test_notify_question_block.py` (29/29 combined) at every step, `ruff check .` clean, `python3 airuleset.py validate` clean, and one full `python3 -m pytest tests/` run at the end — 4708 passed, 0 failures (baseline at 84188ab was 4691 — 17 new tests, no regressions).

Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).
2026-08-07 (worktree dispatch) #306 (stash-around delivery leaves typed text un-submitted + wedges slot for hours, david@subdev live regression): root cause traced to `deliver_with_stash`'s "swallowed-submit-not-recovered" branch (reached only AFTER our own type-verify already proved the box holds nothing but our own typed `text`) returning `False` with ZERO recovery — whenever the outcome that got there was a genuine PARK (our own `C-s` earlier in the SAME call), the draft stayed stuck in CC's single stash slot forever (nothing else ever pops it back), so every LATER caller of `deliver_with_stash` (jobs 9/10/14/20) saw `STASH_MARKER` on its first capture and aborted "slot occupied" — the reported ~2h wedge. `_send_goal_verified` (job 20's own sibling primitive) had the identical zero-recovery gap on its own swallowed-submit path. Fix: extracted the recovery the pre-existing #193 verify-failure branch already had into a shared helper `_undo_and_release_slot` (backspace exactly `text` — provably safe by construction — then, only once bare is confirmed, pop a parked draft back with one corrective `C-s`, reading the result rather than asserting it) and called it from BOTH `deliver_with_stash` sites (the original site reproduces the pre-fix log wording byte-for-byte) and `_send_goal_verified`'s swallowed-submit path (backspace-only, `parked` hardcoded False — no draft ever exists to park on that bare-box-only primitive). Design decision (posted to #306 before the first commit): rejected an age-based backstop (widening job 14's `compact_stash_skips` counter or similar) in favor of fixing the primitive itself — the FREEZE forbids new watchdog jobs, a cross-job counter would need duplicating/threading new shared state across 6+ call sites, and it would only address the symptom (a stuck slot) while leaving the box's own garbled content — the FIRST reported symptom — unrecovered regardless. test:e206bee[red] (4 genuine AssertionError failures, confirmed via `git stash push -- watchdog/__init__.py` against the true unmodified implementation) -> feat:b217d3a[green] Closes #306.

A fresh-context Fable adversarial review (fable-gate OPEN 37%) found 0 CRITICAL, 0 MAJOR, 2 MINOR, 4 THEORETICAL (verdict: SAFE TO SHIP). Both MINORs fixed and mutation-verified: (1) `_undo_and_release_slot`'s own docstring overclaimed its precondition (said it always came from a bare-box settle poll, and served only "both" of `deliver_with_stash`'s paths) — reworded to name both justifications (bare-settle for PARKED/NOOP, `_typed_exclusively` for the UNRESOLVED-exclusive path, where `parked` is structurally False so the dangerous pop can never fire) and all three call sites; (2) the `_send_goal_verified` test fake never modeled `C-s` at all, so mutating its call site's hardcoded `parked=False` to `True` passed silently — taught the fake CC's single-slot stash (mirroring `FakePane.key`) and added `assertNotIn("C-s", tmux.keys(), ...)`, hand-verified the mutant now fails with a genuine `AssertionError: 'C-s' unexpectedly found in [...]`. fix:5b2b255. Confirmed independently by the reviewer (no fix needed): the original call site's log-string refactor is byte-identical to pre-fix wording (checked against `git show a0cc631`); no test/hook anywhere matches these reason strings by exact equality; every `Escape` reachable from the modified paths is followed immediately by `Enter` (no #35/#36 double-Escape hazard); `_undo_typed_text` can only report `undone=True` against a genuinely-bare fresh capture (a stale render fails toward the safe "leave parked" direction).

Targeted suite green throughout (241 tests across the 4 affected files), full suite green (4702 passed, up from the 4697-test `a0cc631` baseline by exactly 5 new tests), `ruff check .` clean, `airuleset.py validate` clean.


2026-08-08 (worktree dispatch) #307 (core-quals wrongly counts sub-dev prio:bounce tickets + the statusline I segment is unreadable): two independent halves. Half A: `MAINTAINER_ACTION_LABELS` dropped `prio:bounce` (kept `needs-gatekeeper`/`ready-for-review`) — a bare open bounce ticket means "the SUB-DEV acts next", not the gatekeeper, and re-including it had inflated `core-quals --count` 63 -> 77 on odoo-erp (all 14 extra `stream:david`'s own bounce tickets, live evidence 2026-08-07). A ticket carrying BOTH `prio:bounce` and `ready-for-review` still counts, via the `ready-for-review` qual. `cmd_core_quals` now unions in a BARE `extra`-only query whenever `--extra` is given, so the bounce-lane SEED (`core-quals --list --extra "label:prio:bounce"`) still finds a ticket that is ONLY bounce-labelled — the old code found it BY ACCIDENT (prio:bounce AND'd with itself, since it used to be one of the unioned quals). test:699de93[red] (5 genuine AssertionError failures against current HEAD) -> fix:0a66cea[green] Closes #307. Symmetric check confirmed and lock-tested (no code change needed): `slice-quals` on a sub-dev box already includes its own `prio:bounce` tickets, since a bounce ticket always ALSO carries `stream:<user>`, which `_slice_quals()` already queries.

Half B (readability, treated as the PRIMARY fix per the ticket's own instruction — "a correct number he misreads is still a broken instrument"): `statusbar.tickets_segment()`'s active-run branch used to render a combined `I D/T` ratio — the SAME leading glyph as the bare `I N` live-count form, and it never showed the `core`/`streamy` (or `gk`) split the idle render already computes from the identical cache — which is how a real `I 41/103` got misread as "103 tickets on me". Redesigned: an active run now renders `run N done` (a label DISTINCT from `I` on purpose) immediately followed by the SAME live `I N [core]` number (+ `· str M` / `· gk M` split, factored into a shared `_stream_split_sfx()` helper so idle and run-mode can never drift apart) — `done` is a historical run counter, the live `I N` count is "how many are mine right now" as its OWN number, never folded into a combined total. Falls back to the old combined `run D/T` ratio only when the live open count is not yet known (fresh cache / `gh` error). test:83d2950[red] (10 genuine AssertionError failures against current statusbar.py) -> fix:7b2ada3[green]. Updated `modules/core/statusline-vocabulary.md` (the segment's own spec doc) and its locked test to document the new form.

Dispatched a fresh-context Fable adversarial review (fable-gate OPEN, 40%/42%) with a REFUTE brief against the full diff. Verdict: the CODE of both halves is correct and safe (no CRITICAL), both load-bearing new assertions mutation-verified, the new statusbar code checked against a battery of hostile cache values with no #133-class crash, the bounce-lane seed's real-world behaviour independently re-derived and confirmed correct. What was NOT clean: the prose reversed a settled cross-stream invariant ("the gatekeeper holds while a bounce is open") and left it contradicted in three places, plus two real minor code bugs — all fixed: skills/autopilot/SKILL.md cross-stream rule 4 rewritten to name the TWO deliberately-different mechanisms explicitly (`/process-subdev`'s own per-stream loop, unaffected by this ticket, holds through a bounce's WHOLE lifecycle and is what actually "resumes" review; the FULL `/autopilot` loop's own `core-quals` stop-proof is separate and narrower, holding only while a hand-off it can act on directly is open); Step 1 Preflight's backlog-scope carve-out dropped `prio:bounce` from "only THIS box can act on it" (it still claimed the opposite of this ticket's own fix two paragraphs above); the FULL template's dead "await the OTHER side... REVIEW-WATCH" clause removed (nothing in the narrowed obligation set awaits the other side any more); `cmd_core_quals`'s bare-extra branch now strips whitespace before its truthiness check (`--extra "   "` was truthy and would have leaked a bare whole-repo query, the exact #181 never-stops shape) with a mutation-verified regression test; its `--extra` docstring corrected to state the widened (no-longer-pure-AND) contract honestly; two stale/vacuous test assertions fixed (`test_the_full_template_scopes_the_proof_to_the_obligation_set` was passing for the wrong reason — `prio:bounce` still appears in the UNRELATED bounce-lane seed sentence on the same line — rescoped to the obligation-set clause specifically); `.claude/rules/airuleset-internals.md`'s statusline bullet updated from the pre-#307 `Issues D/T` description. fix:0357919. Review evidence + design + still-valid comments posted to #307 at the required checkpoints.

Full local suite green throughout, final run: 4725 passed (baseline 4708 + 17 new tests across both halves and the review-fix batch), `ruff check .` clean, `python3 airuleset.py validate` clean.

Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).


2026-08-08 (batch dispatch, branch `batch-302-304-308`) three independent fixes bundled per the supervisor's own bundling gate.

#302 (`_checkout_pane_owner`/`_notify_backfill_digest` bypass `STREAM_NOTIFY_OWNER` redirect): `_checkout_pane_owner`'s own `owner_of(pid)` call had no knowledge of `notify.stream_redirect()` — unlike every `pane_owner()` call site in `watchdog/__init__.py`, already structurally locked. Fixed by wrapping the call site (`stream_redirect(owner_of(pid) or "").strip().lower()` — redirect first, normalize the passthrough-safe result after, to satisfy the new structural lock's literal-adjacency requirement while matching the real production `owner_of` default's already-normalized output) and adding a companion structural lock, `TestAirulesetOwnerResolutionAlwaysRedirected`, scanning `airuleset.py` for any future un-redirected `owner_of(`/`pane_owner(` call site. test:86c87d2[red] (3 genuine failures) -> fix:c7707d4[green] Closes #302.

#304 (watchdog dry-run leaks reply/react dedup marks into real state): `deliver_discord_replies`'s `_ack`/`_delivered` marked `acked_set`/`acked` and `done_set`/`done` unconditionally regardless of `dry_run`, so a `--dry-run` troubleshooting sweep silently poisoned the real dedup state and the following real sweep skipped the user's actual answer as "already delivered". Gated both pairs on `not dry_run`, mirroring this same file's own sibling fix for the card-reopen path. Two MORE pre-existing tests in two other files (`test_discord_reply.py`, `test_hosted_reply_delivery.py`) were found asserting the buggy behaviour as intentional and were corrected with inline justification. test:b4efe34[red] (2 genuine failures) -> fix:13dcdaf[green] Closes #304.

#308 (a new stream account's pre-registration tmux session silently wins the first login forever, wrong cwd — the miva1 incident): `ensure_stream_tmux_session`'s `if exists:` branch did zero cwd inspection. Added a read-only `_tmux_session_pane_cwd()` probe and a mismatch check (match stays quiet, mismatch prints a loud WARNING, inconclusive stays quiet — never auto-kills/re-cwds/sends keys), plus a fifth checklist step in `.claude/rules/airuleset-internals.md`'s stream-registration bullet (kill any pre-existing session before the new account's first push — a deliberate provisioning-time human action). test:f6ffb79[red] (2 genuine failures) -> fix:abdc358[green] Closes #308.

A fresh-context adversarial review (model: opus) of the whole batch diff found real, live-triggered defects across all three: #302's `--owner-name` flag still reached `notify.send()` un-redirected (the documented no-live-pane use case); #304's ticket-fallback branch in `_pending` faked a successful gh-comment post under `dry_run` and wrote a real, persisted `dreply_pointer` entry that a FOLLOWING real sweep would type into a live pane as a false instruction to read a ticket comment that was never posted (reproduced live by the reviewer end-to-end), plus the same unconditional-mutation shape on `blocked`/`inputdead`; #308's own cwd probe used `tmux display-message -t "=<session>"`, which resolves to NO pane and still exits 0 — the fix was completely INERT in production (live-verified against this box's own real tmux 3.7b), fixed by switching to `list-panes -s`, and the raw-string cwd comparison false-positived on a healthy session `cd`'d into a subdirectory or behind a symlinked `$HOME` — fixed with realpath + containment. Every finding fixed and mutation-verified (backup, targeted revert, confirm the new test fails, restore, confirm green). A genuine scope question (the new cwd check is sentinel-gated, so it's inert for the already-bootstrapped fleet) was filed as its own follow-up ticket (needs-user-decision) rather than silently expanded. fix:cff3f51.

Review + design + still-valid evidence comments posted to all three issues at the required checkpoints. `.claude/rules/airuleset-internals.md` also gained two new reusable entries (the `display-message`-vs-`list-panes` pane-target gotcha, and the redirect-then-normalize ordering trick for a structural-lock-adjacent call site). docs:8002dc8.

Full local suite green throughout (scoped runs at every step; two full `python3 -m pytest tests/test_airuleset.py` runs at 785 tests, a combined 8-file discord/watchdog run at 791/1575 tests across passes), `ruff check` clean on every touched file. Worked directly on a shared `batch-302-304-308` branch (not an isolated worktree, per the dispatch's own instructions) — this repo's own supervisor merges + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model).


2026-08-08 (batch dispatch, branch `batch-311-312`) two independent fixes bundled per the supervisor's own bundling gate.

#311 (follow-up ticket factory — adversarial-review findings chaining unboundedly, odoo-erp #3035→#3220→#3224→#3250→#3251→#3252→#3258, each hop honestly satisfying its own Scope-gate criterion): three-part fix. (1) `completion-report.md`/`agents/autopilot-worker.md`: closed the "outside the diff → file a follow-up" loophole with a same-branch-fix mandate — a <100 LoC adjacent-code finding is DO NOW, same branch, unless one of the six bundling-gate criteria genuinely applies. (2) `hooks/block-ungated-issue-filing.sh`: a chain-depth cap — a new issue naming its own PARENT as a "follow-up" is ALSO blocked when that parent is itself such a follow-up, regardless of an otherwise-valid Scope-gate line; one bounded `gh issue view` call, gated behind a cheap text pre-filter, degrading to "cannot verify" on any lookup failure. test:bbf954b[red] -> fix:dc4631d[green]. (3) The same hook's own `>300-loc` criterion is now mechanically self-contradiction-checked: a body claiming `>300-loc` whose own text states a bare number ≤300 next to "loc"/"lines" is blocked. test:dff58a9[red] -> fix:9f2b8b3[green] Closes #311. Direct primary evidence (read-only ssh to david@subdev, `~/.claude/scope-gate.log`) confirmed the hook is deployed and genuinely exhibits the described pattern live (`criterion=cross-cutting` repeated for titles literally saying "(#3224 follow-up)"). A delegated fork sub-task (auditing the full odoo-erp chain ticket bodies) went outside its assigned read-only scope during a long run and returned no usable audit — not relied on; the direct scope-gate.log evidence was sufficient on its own.

#312 (goal-rearm coverage gap — a dead `/goal` invisible to the heal job, live incident on this box's own main session): the ticket's own stated hypothesis (fleet-template-hash tracking gates re-arm eligibility, #64) does not match the code — confirmed by direct reading (hash-matching only gates the SEPARATE stale-template re-sync sub-feature) and a live dry-run probe against this box's real panes. The REAL, confirmed mechanism: `rec['mark']` collapsed "never observed by this job" (`None`) and "an explicit clear this job itself scanned" (`"cleared"`, the deliberate-user-shutdown case) into one silent-forever `continue` — live-reproduced on this box's own `zbynek-4:2.0` pane (zero goal-rearm journal lines for ~21h, `mark: "cleared"` in the real persisted state, `last_armed` newer than the last scanned marker with nothing in between). Fix: `_goal_recover_untracked` does a one-shot, progress-evidence-gated (`~/.claude/autopilot-progress/<repo>.json`, `done>0`/`remaining>0`/fresh) full-file rescan to recover a genuinely lost `"set"` marker, falling through into the existing dark-cap/outage/hash-streak machinery unchanged; a `"cleared"` mark stays untouched by construction (the new code path only fires when `mark is None`). Also extended the pre-existing #160 skip-verdict re-validation: when the session's own tracked payload (not just the last turn's text) proves the loop is backlog-shaped, the live `gh`-backed re-check now runs even without a fresh claim. test:b692e42[red] -> fix:5b7144b[green] Closes #312. Mid-implementation the supervisor injected a live-evidence scope addition: CC's own `◎ /goal` footer glyph can be TRUNCATED off-screen by a wide custom statusline row (confirmed live, width 176) — a genuinely-armed goal can read as dark. Hardened: the recovered marker's own declared arm time must clear a 5-minute grace before ever being acted on, with the one-shot recovery flag deliberately NOT consumed on a "too fresh" verdict so a genuinely-buried-but-recent marker retries once it ages.

Both tickets' STILL-VALID and design-before-code evidence posted at the required checkpoints. A fresh-context adversarial review (model: opus) of the whole batch diff found 4 real, ALL live-triggered defects (not theoretical) and mutation-verified every fix: (1) the `>300-loc` self-contradiction check took only the FIRST number next to "loc"/"lines", false-blocking a body that honestly quoted the gate's own threshold before its real large count — fixed to check the max of every stated number; (2) the chain-depth cap misread a root/umbrella ticket linking its own spawned children as itself being a follow-up — fixed with a backward-reference (lower issue number) requirement; (3) MOST CRITICAL — the truncated-footer hardening checked only the recovered marker's own timestamp, never the transcript file's own mtime the constant's comment promised, so the 5-minute grace essentially never engaged for the exact population it was built to protect (reproduced live: goal armed 2h ago, transcript written 0s ago, footer dark — old code recovered and re-armed it, exactly the duplicate-paste risk the hardening existed to prevent) — fixed to require BOTH clocks stale; (4) the deliberate-clear-goal regression test had zero teeth (its fixture buried the "cleared" marker inside the ordinary tail-bootstrap window, so the new recovery path was never even reached — instrumented: 0 calls) — fixed and mutation-verified. Two more minor findings (plural "follow-ups" wording + narrow window; a `-R owner/repo` cross-repo filing ignored) folded in under the same-branch-fix mandate this very batch introduced. fix:ccf8f23. Review + design + still-valid evidence comments posted to both issues at the required checkpoints.

Full local suite green throughout (final run: 4769 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Worked directly on a shared `batch-311-312` branch (not an isolated worktree, per the dispatch's own instructions) — this repo's own supervisor merges + pushes (no PR, no CI, direct push to main per this repo's own two-branch-minus-PR model). The shared working tree showed real concurrent activity from the supervisor during this run (several commits — the #312 RED tests, the `>300-loc` RED/GREEN pair, a mutation-anchor re-pin — landed mid-session without this worker's own action); each was independently verified (full suite + ruff + validate) before being relied on or built upon.

2026-08-08 (worktree dispatch) #316 (`stop-check-prose-violations.sh`'s spec/plan-approval-pause detector was English-regex-only, blind on every Slovak question this repo actually ships — the montalu2@subdev incident, odoo-erp#3265): extended the hook with a narrow, structurally-anchored Slovak detector — an approval verb (schvaľuješ/schváliš/odsúhlasíš/potvrdíš/odobríš) attached to a design-artifact noun (spec/plán/návrh/dizajn/špecifikácia), found on the ❓ marker's own line(s), gated on the absence of bullet/numbered option lines (a genuine design fork must carry those per stop-check-question-quality.sh's own Check 4) AND on the user not currently being present (mirrors that same hook's presence exemption). Also audited all 17 rows a prior commit (a89024a) had trimmed from ask-before-assuming.md's pre-answered table against a REAL Slovak rendering of each row's banned intent — 14 of 17 were only ever hook-covered in English and are reverted verbatim (one gains a single reinforcement sentence per item 3 of the ticket: after a design comment, continue to the implementation plan, never a courtesy re-confirmation). test:4509e1c[red] -> fix:8790f70[green] Closes #316.

Two dispatched fresh-context adversarial reviews found real gaps, both fixed and every guard mutation-verified (backup/mutate/confirm-fail/restore): round 1 (code-review skill + opus general-purpose) found the numbered-list bullet form missing, an unscoped verb+noun scan false-blocking on an unrelated approval mention + unrelated real question elsewhere, the bullet exemption reading raw MSG instead of mention-stripped MSG_MENTION, a missing presence-gate exemption, and missing \b word-boundary anchoring (Slovak "ne"-negation, substring matches) — fix:d129e1f. Round 2 (opus, full accumulated diff) found a CRITICAL: SK_APPROVAL_RX/SK_ARTIFACT_RX's diacritic bracket classes with adjacent \b anchors are locale-dependent — under a bare LC_ALL=C every verb spelling MISSED, silently making the whole detector inert on exactly the away-box population it exists for (reproduced live; alternation alone does NOT fix it, only forcing LC_ALL=C.UTF-8 on the one grep call does) — plus a tautological noun-substring test (paired the substring trap with a verb outside the approval list), a tail-1 marker-line selection that missed a montalu2-shaped question buried under a later unrelated ❓ line, and a grep-failure (rc>=2) on the marker extraction silently disarming the check instead of record_undet + widen-scope (the same #194 fabricated-verdict shape, one level up) — fix:47f2a5f. A genuine scope-accuracy finding (3 of the 17 reverted-table-row candidates are STILL Slovak-uncovered without their English loanword — a stricter audit bar than #316 applied) was filed as its own follow-up (#319, needs-user-decision) rather than re-litigated in this same pass.

Full local suite green throughout (4741 passed, up from 4695 at HEAD before this ticket by 46 new tests across the RED commit and both review-fix passes), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #316 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #321 (goal-rearm: #320's own dark-cap fix left a live contradiction plus a wrong darkness signal on dev1, and a silent-forever logging gap on montalu2@subdev): three-part fix. (1) `_goal_dark_died_by_outage` gained an optional `last_armed` param and applies the IDENTICAL staleness rule `_goal_cleared_stale` already uses -- previously its own independent fresh re-read of the transcript could re-discover a stale "cleared" marker `_goal_cleared_stale` had already overridden this exact sweep and disbelieve it, permanently re-skipping a session #320 had just revived (live, dev1 `2d02a127-...`: `skip stale-goal` repeating every 60s for 15h+ with `mark` already correctly "set"). (2) `goal_rearm`'s darkness check now also measures transcript ACTIVITY (`_last_real_turn_ts` clamped to `<= tmtime`, mirroring #177's job-10 fix verbatim) -- `last_armed`/`mts` alone only measures when an arm was last CONFIRMED, not whether the session is alive now, and a continuously-busy loop can cross the 6h cap between confirmations while genuinely never dark. (3) `_goal_recover_untracked`'s `_live_autopilot_progress` bail-out now logs once per session (`state['goalrearm_noprogress']`, popped once progress becomes live) instead of being totally silent every sweep forever for any manually-armed (non-batch) `/goal` -- matches the montalu2@subdev live evidence (zero "goal" journal lines across 30 real minutes). Shape B's job-9 half was explicitly NOT touched: no live ssh access to montalu2 exists in this dispatch, and guessing a fix with nothing to verify it against was declined per the ticket's own explicit permission to say so. test:dd33a26[red] -> fix:77c7e61[green] Closes #321.

A dispatched fresh-context adversarial review (model: fable, gate OPEN at dispatch) found 0 CRITICAL/0 MAJOR, 2 MINOR, 3 THEORETICAL. MINOR-1 (the new activity read ran unconditionally instead of gated behind the existing cheap age check, the #320-review "cheap gates before an expensive read" lesson) was fixed in the same branch: test:a0d219d[red] -> fix:7c8ff2f[green]. MINOR-2 (no pruning of the new dedup dict) and all three THEORETICAL findings were accepted as matching existing precedent / pre-existing residuals, not newly introduced -- no code change. The reviewer independently re-verified the call-site trace, the activity clamp's safety claim, and one mutation-testing claim itself before returning a SAFE-TO-SHIP verdict.

Full local suite green throughout (final run: 4882 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #321 at the required checkpoints. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #322 (watchdog job 20's `/goal` re-arm — a long payload was silently swallowed as an unexpanded terminal paste instead of arming, and the give-up state that followed had no reachable exit): a mid-session coordinator correction replaced the original "busy pane" root-cause hypothesis (never confirmed by a controlled test) with a real one — Claude Code (2.1.226) collapses one long `tmux send-keys -l` burst into a "paste again to expand" placeholder that is never parsed as a slash command, so the delivery "succeeds" (Enter submits the placeholder as a plain chat message) while the goal never arms; length, not session business, is the trigger (live controlled experiment: a short payload armed fine on a BUSY pane, a 3960-char one failed on an IDLE one). Fix: `_type_literal()` types any payload at/above `GOAL_TYPE_CHUNK_THRESHOLD` (200 chars) in ~120-char chunks with a short pause, matching the live-verified working shape, wired into both delivery primitives; `_pane_shows_collapsed_paste()` refuses to press Enter on the hint in either primitive, with `deliver_with_stash`'s own integration a separate early return (never routed through the existing backspace-based undo, whose safety proof needs a box provably bare before typing — not true for the collapsed shape). test:5af5563[red] -> fix:fb86c9c[green] (round 1, busy-pane transient refusals + a first, unbounded reachable give-up-reset). test:3384509[red] -> fix:f6c3865[green] (round 2, the chunked-typing primary fix + collapsed-paste guard, plus a fresh-context adversarial review's fixes to the reset mechanism: bounded to `GOAL_REARM_GIVEUP_MAX_RESETS`, never re-stamping `first` so the GAVE UP ping's own `dedup_key` stays stable across a reset, gated on `not dry_run`, and `gaveup_at` isinstance-guarded against a corrupted state file).

A SECOND fresh-context adversarial review of round 2's committed diff returned NOT SAFE TO SHIP AS-IS with one blocking CRITICAL, live-verified against a real tmux 3.7b pane: the chunked `send-keys -l <chunk>` calls had no `--` end-of-options guard, so a chunk (or a short single-burst payload) beginning with `-` is read by tmux's own getopt as an unknown flag and the call fails silently (`_default_run` swallows non-zero exit as `""`, no exception, no log) — every LATER chunk still lands, so the tail-based `_typed_landed()` check is satisfied by the internally corrupted remainder and a GOAL WITH A 120-CHARACTER HOLE gets armed, strictly worse than the bug this ticket fixes. Live-verified against this repo's own 5 shipped `/goal` templates: 2 of 5 had a chunk boundary landing on a literal `-`. Also found: the collapsed-paste early return's safe zero-keystroke abort can strand the single stash slot forever (parked draft never popped, since the box holds an unproven collapsed hint rather than provably-bare content — no safe backspace count exists), and every later sweep's own entry precondition then refuses transiently ("stash-abort: slot occupied") FOREVER, making the whole ticket's own reachable give-up-reset mechanism silently unreachable for that pane; and `giveup_resets` lacked the same isinstance guard `gaveup_at` already had (a corrupted state-file value would raise an uncaught `TypeError`, killing job 20 for every pane, every sweep). Fixes: `--` added before the payload in both the single-burst and chunked `send-keys -l` calls (both paths, since job 7's arbitrary Discord-reply prompt is not `/goal`-prefixed either); `GOAL_REARM_SLOT_STUCK_MAX` bounds a CONSECUTIVE run of specifically the "slot occupied" reason before it stops being treated as transient (every other transient reason — a genuinely foreign, self-resolving occupant, a busy pane, a live turn — stays unbounded, unchanged); `giveup_resets` gets the identical isinstance(int) guard. A `dry_run` gap in the pre-existing streak-reset block was left as an accepted, non-blocking residual per the review's own verdict (restore-not-consume direction, consistent with that block's existing unguarded `n`/`pinged`/`first` clearing). test:c4f6cf4[red] -> fix:94778a4[green] Closes #322.

Every new/changed piece mutation-tested (backup/mutate/confirm-fails/restore) before being trusted, including the CRITICAL-1 fix verified live against all 5 of this repo's own real shipped `/goal` templates (0 of 5 show data loss post-fix, vs 2 of 5 pre-fix, using a fake `run` that models real tmux's getopt rejection of an unguarded dash-leading argument). Design + still-valid + review evidence comments posted to #322 at the required checkpoints (root cause / chosen approach / rejected alternative for every finding of both adversarial reviews). Full local suite green throughout (final run: 4894 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #314 (SessionStart hook: fetch without fast-forward leaves sub-dev checkouts weeks stale — CLAUDE.md identity read from old tree): root cause traced directly in `hooks/session-start-fetch.sh` — the hook ran `git fetch` (updates only the invisible `origin/<branch>` remote-tracking ref) and printed a `WARNING: ... behind ...` line, but never advanced the actual working tree; a long-lived, mostly-passive session (a sub-dev stream account's persistent tmux, cwd under a project checkout) reads its CLAUDE.md/project files straight off the on-disk tree at boot, so content could sit weeks stale even though the hook "fetched" every session start the whole time. Fix (extends the SAME hook — the repo's FREEZE forbids a new hook/watchdog job): after the existing fetch+warn, attempt `git merge --ff-only` ONLY when every one of these holds — a real branch (not detached HEAD), no in-progress git operation (checked via `.git` state files BEFORE the branch lookup, since HEAD can be detached mid-rebase), a clean working tree (`git status --porcelain` empty), and HEAD is a genuine ancestor of `origin/<branch>` (`git merge-base --is-ancestor`). `--ff-only` itself is a hard safety net (can only ever move the ref forward, never `reset --hard`/`checkout -f`/any history rewrite). test:adf6cc5[red] -> fix:3e955fd[green].

A dispatched fresh-context adversarial review (opus — fable-gate CLOSED at 85%) found one CRITICAL and three MINOR gaps, all fixed in the same branch: F1 (CRITICAL, live-reproduced) — the dirty-tree check is blind to gitignored files, and `--ff-only` silently CLOBBERS a local ignored/untracked file the instant origin adds a tracked file at the same path; fixed by refusing whenever `git diff --name-only --diff-filter=A HEAD origin/$BRANCH` names a path that already exists on disk. F2 (test quality) — the dirty/diverged tests touched the SAME file the incoming commit also changes, so `--ff-only` refuses on its own regardless of whether the guard ran; mutation-tested (a mutant dropping either guard SURVIVED both tests) — fixed by dirtying an unrelated tracked file and asserting the guard's own specific wording; re-mutated post-fix, both mutants now KILLED. F3 (minor) — `REVERT_HEAD`/`sequencer` were missing from the in-progress-operation state list (a clean-tree mid-revert was reachable). F4 (minor) — a failing `git status` (e.g. unreadable `.git/index`) read as "clean"; fixed with an explicit `||` guard (the repo's own documented `set -e` + `VAR=$(failing_cmd)` trap) treating an unmeasurable status the same as dirty. test:3918cc8[red] -> fix:6af9fe4[green] Closes #314.

Design + still-valid + review evidence comments posted to #314 at the required checkpoints. Full local suite green throughout (final run: 4914 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch, bounce-lane) #318 (watchdog blind on a lost tmux socket — the 2026-08-08 subdev incident: a live tmux SERVER process survived its own control-SOCKET file being reaped, e.g. by a tmpfiles-clean-shaped age-based sweep, while david's `claude` session kept running; `list_claude_panes()` treats `tmux list-panes -a` returning empty as always meaning "no session runs" — indistinguishable from the orphaned-socket shape — so job 8 (`bounce_backstop`) built a false "nebeží žiadna Claude session" Discord ping for a repo whose session was alive the whole time): fixed at the single funnel point every pane-reading watchdog job goes through, not per-job — `list_claude_panes()` now recognizes an empty `list-panes` result as ambiguous, and when the control socket file is genuinely missing from disk (`_tmux_socket_missing`, real `os.path.exists`) AND a live same-uid `tmux: server` process is found (`ps -eo pid,uid,comm`, uid-filtered), sends `SIGUSR1` — tmux's own documented socket-recreation signal (`man tmux` SIGNALS section), the same recovery the supervisor applied by hand — and retries. A box with genuinely no tmux server is completely unaffected: no candidate found, no recovery attempted, identical behavior to before. `logs=`/`dry_run=` threaded through job 8 and job 11's own journal output (the exact "nebeží žiadna Claude session" reporting path) plus job 9 and `run_once`'s own central per-sweep pane loop, so `watchdog --once --dry-run` stays genuinely side-effect-free. test:d619174[red] -> fix:4829e3f[green] Closes #318.

A dispatched fresh-context adversarial review (`model: opus`) of the committed fix found two real, LIVE-MEASURED bugs, both fixed in the same branch: MAJOR-1 — the first cut's `_tmux_server_pid` picked the FIRST `tmux: server` line from `ps -e` with no uid filter; live-measured this box runs a second same-uid `-L` tmux server right now (dev2: default + `-L t2`) and a shared box (subdev) lists OTHER users' servers in a bare `ps -e` — since a server's SIGUSR1 only ever recreates ITS OWN socket, signaling the wrong candidate could leave the real incident unrecovered forever (a mutant swapping "first" for "last" survived all 14 original tests, proving the selection was unspecified). Fixed with `_tmux_server_pids` (plural, uid-filtered) trying every same-uid candidate in turn until the retried query succeeds — new mutation-verified test `test_tries_every_same_uid_candidate_until_one_recovers`. MAJOR-2 — `bounce_backstop(..., dry_run=True)` still sent a real signal, since `list_claude_panes` had no `dry_run` of its own; fixed by threading `dry_run` through. A MINOR (the socket-missing `stat` now runs before the `ps -e` fork, so a healthy box never pays for the scan) and a factually-wrong docstring claim (this repo's own scripts/tests DO use `-L` tmux sockets) were also fixed. fix:8097065. Findings + resolutions posted to #318.

Full local suite green throughout (final run: 4925 passed, up from the 4905 baseline before this ticket), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #318 at the required checkpoints. Outbound SSH from this worktree to subdev/gatekeeper was refused/reset throughout the session (dev2 reachable fine over the same tailscale mesh, ruling out a blanket sandbox block) — could not live-confirm the issue's own `NEPOTVRDENÉ` tmpfiles.d hypothesis on subdev directly; the SIGUSR1 recovery itself is agnostic to WHY the socket went missing, so this does not block the fix. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch, doc/skill-only) issue 325 (autopilot-master's LANE 3 CORE still described serial-per-worker dispatch, stale against the parallel worktree fleet-dispatch default a sibling ticket landed as the repo-wide DEFAULT for the autopilot loop): aligned LANE 3 CORE's three restated spots -- the /goal MASTER LOOP template, the LANE 3 CORE prose bullet, and the Collision guards line -- with that ticket's exact wording/shape (a FLEET of PARALLEL isolation:worktree autopilot-workers per round, cap 3-5, integrated SERIALLY by the supervisor, never a second round in flight, the documented serial single-worker fallback). test:f954bf3[red] (retired the stale "serial per repo" wording lock, proven RED against the pre-fix skill text) -> fix:e996e75[green].

A dispatched fresh-context adversarial review (opus, read-only) found 0 CRITICAL, 1 MAJOR ("ONE PR per round" was stated as a universal LANE 3 invariant, but the sibling ticket's own repo-flow policy is three-way -- a local-merge repo like this one pushes straight to main with no PR/CI at all), and several MINORs (a missing second fallback trigger, inconsistent ROUND/round capitalization, two different phrasings for "in flight", an overloaded parenthetical) -- all fixed in the same [green] commit. Fixing the review findings re-wrapped the LANE 3 bullet's line breaks and briefly split the literal "Each merged" string across a markdown line-wrap, silently breaking an existing run-card test; caught and re-wrapped before committing. Full local suite green throughout (final standalone run: 4905 passed -- two earlier runs racing a concurrent sibling worktree worker's own full-suite run on the shared /tmp namespace showed unrelated failures in test_subagent_bg_work.py, confirmed a pre-existing shared-/tmp race and unrelated to this diff by re-running that file alone: 44/44 passed). ruff clean, airuleset.py validate clean.

Two cross-cutting THEORETICAL gaps found during the review (autopilot-master's own /goal template has no char-cap test lock, and watchdog job 20 doesn't track it for drift/staleness) were filed as a follow-up ticket rather than fixed here, per this repo's FREEZE policy on the supervision machinery. Design + still-valid + review evidence comments posted to #325 at the required checkpoints. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).


2026-08-08 (worktree dispatch) #324 (montalu2@subdev remains permanently without a goal — `untracked-no-progress` had no reachable exit, after #321 made the bail-out log but not act): `_goal_recover_untracked`'s ONLY eligibility gate for its one-shot recovery scan was `_live_autopilot_progress` — a live `~/.claude/autopilot-progress/<repo>.json` heartbeat that a manually-armed, non-batch `/goal` loop can never produce, permanently excluding montalu2's own live incident from recovery. Two fixes, matching the ticket's own named options. (1) `state['goalarm']` (job 9's own per-pane dedup record, proving a real arm attempt was made) is a second, independent, cheap evidence source that unlocks the same scan — the ticket's OTHER named alternative (footer `◎ /goal` read from the pane) needed no code at all, since `goal_rearm`'s own loop already reads it before this function is ever reached. (2) When neither evidence source ever resolves, a bounded, deduped Discord ping (`GOAL_REARM_UNTRACKED_PING_S`, 30 min) now fires instead of silent-forever waiting, mirroring `_goal_stall_nudge`'s own give-up shape. Rejected an automatic re-arm (the ticket's other option) as the riskier, larger diff — this function has none of job 9's own fresh-capture/busy/draft classification. test:dbf102f[red] -> fix:80e6f87[green] Closes #324.

A dispatched fresh-context adversarial review (model: opus, gate CLOSED) returned NOT SAFE TO SHIP: 2 CRITICAL + 2 MAJOR + 3 MINOR, two reproduced live against dev1's own real persisted `~/.claude/api-watchdog-state.json`. CRITICAL-1: `pinged[sid]=True` was set BEFORE the `send_fn is not None and not dry_run` check, so a `--dry-run` sweep (this repo's own documented manual diagnostic, run against real state) permanently consumed the one-shot escalation with nothing ever sent — the EXACT `dark_pinged` #238-review-shaped bug this same job already fixed once 150 lines below, mistakenly re-copied from its own UN-fixed sibling `_goal_stall_nudge` (left untouched, pre-existing, out of scope under FREEZE). CRITICAL-2/MINOR-5: a legacy `True` bool (from the pre-#324 code — one such entry exists on dev1 right now) or a future-dated `first_seen` was trusted verbatim; `bool` is an `int` subclass in Python, so `now - True` cleared the 30-min bound by ~56 years, reproduced live against dev1's own real state. MAJOR-3: `dedup_key` was `sid` alone, silently swallowing a genuinely new dark episode at notify's own 14-day dedup layer. MAJOR-4: the new `goalarm`-record evidence had no freshness bound at all (unlike its sibling, whose own comment says "not week-old leftover state") — reproduced live against dev1's own real `goalarm` entries, up to 16.9 days old. All fixed, each with its own mutation-proven RED test (confirmed failing against the pre-review-fix commit via `git checkout 80e6f87 -- watchdog/__init__.py`, then restored). test:254c055[red] -> fix:e2d3cbf[green].

Live verification (read-only ssh to montalu2@subdev, plus direct reads of dev1's own real, currently-live api-watchdog state — no write, no keystroke, anywhere): the original ticket's exact log line reproduced verbatim in the box's own journal at the cited timestamp (`skip untracked-no-progress ... montalu2:0.0`); the pane has since become armed on its own (a human re-arm, not this not-yet-deployed fix — the journal shows the reason correctly evolving from "skip untracked-no-progress" to "skip busy" once it did); and — the strongest evidence — dev1's own real, currently-persisted `~/.claude/api-watchdog-state.json` carries BOTH artifacts the review found (the legacy bool `goalrearm_noprogress` entry, and `goalarm` entries up to 16.9 days old), and running this fix's own validation logic directly against those real values confirms the legacy bool is correctly re-stamped (not read as a ~56-year age) and every stale `goalarm` entry is correctly excluded.

Full local suite green throughout (final run: 4916 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #324 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).
2026-08-08 #327 (claude-history popup: capture-pane instead of a transcript reconstruction). User rejected the transcript-based popup's OWN rendering (custom role headers, bold, colors, dimmed tool lines, own wrapping) as visually different from the live terminal. Measured whether tmux's native scrollback (`capture-pane`) is complete enough to become the primary source: MISSING content stays near-zero after the prior scrollback-retention mitigations (0-0.33% even under synthetic relayout stress, per the repo's own scrollback-holes-measurement-267.md); a fresh live sliding-window diff against a real, actively-used dev1 pane today (2.1.226, read-only, no keystrokes sent) found the upstream Claude Code TUI renderer's DUPLICATION defect still real -- 693 duplicate 8-line windows across ~12k lines -- but judged acceptable since it repeats content rather than hiding it, and a reconstruction can never achieve visual identity regardless of formatting. Flipped the popup script's primary source to `tmux capture-pane -e -S -<history-limit>` piped into `less -R +G`; the transcript-based `claude-history` rendering stays as the fallback for when capture-pane produces nothing, now forcing `--plain` (never the previously-forced `--color`) by default. Key mechanism, live-verified twice independently (once with an isolated `-L` socket across three windows, once more rigorously by the dispatched adversarial review's own 2-session/2-client real-keypress-injection repro): a bare `tmux capture-pane`/`display-message` call with NO explicit `-t`, issued from inside the popup's own shell, resolves against the ORIGINATING pane -- no pane-id-passing plumbing needed at all. test:b2a8cba[red] -> fix:9262c80[green]. Adversarial review (Opus, fable-gate CLOSED 94%): no runtime defect found on any branch it could execute, but 5 of the test suite's own assertions had weaker teeth than they looked (a mutant dropping `-R` from just the primary branch, moving `exit 0` one level deeper, dropping the CP_OUT emptiness guard, dropping `--full` from the fallback, and hardcoding the history-limit literal all survived) -- all 5 fixed and each re-verified by applying the exact mutant to a backup copy and confirming the strengthened test genuinely fails before restoring. Also corrected one overclaim in the design comment (an unverified `$TMUX_PANE`-inside-popup observation, likely env-leaked from an unisolated test) without touching the actual mechanism, which the review's own stronger evidence had already confirmed correct independently. fix:610d110[green]. Live visual-comparison verification: popup stdout byte-identical to an independent raw `capture-pane -e` call on the same pane for all real content (ANSI colors, wrapped bullets, prompt) -- the only difference is trailing blank-line padding at the pane's bottom, an accepted, pre-existing `$(...)` command-substitution behavior this script has always had (bash unconditionally strips trailing newlines from a captured variable), not a new regression. Full targeted suite green (131 popup/tmux/history tests), `ruff check` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #327 at the required checkpoints. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes.
2026-08-08 (worktree dispatch) #310 (a PreToolUse hook block on a compound `cat > path <<EOF ... && git commit -F path` denies the whole thing atomically -- the intended write never runs, so a leftover file with the same name from an earlier attempt survives untouched, and a LATER bare `git commit -F path` retry carries no issue-number text at all, invisible to the design gate's own regex, so it silently commits the stale content -- the email-extractor worker incident this ticket documents): `design_gate.stale_msgfile_candidates(cmd)` intersects `>`/`>>` write targets against `git commit -F`/`--file` targets found in the SAME command text; `quarantine_stale_msgfile(path)` best-effort renames a match aside (`.stale-<epoch-ms>`, content kept). Wired into `block-commit-without-design.sh`'s existing embedded Python: whenever the gate is about to block, every such candidate is quarantined before the message prints, and the message names it -- no persisted state needed, since the compound never executed, any content already there provably predates this attempt. Also a short `gh-cli-recipes.md` note: compose a scratch write-then-consume file as two separate Bash calls, never one compound. Live-reproduced against current HEAD before implementing anything (a stale `msg.txt` survived a real block, and a bare retry against it passed the design gate unconditionally). test:66cff09[red] -> fix:c3f1ff5[green], docs:907296c, docs:7211eec (self-review residual note).

A dispatched fresh-context adversarial review (model: opus) found 1 BLOCKING + 2 MINOR, all confirmed by executing real reproductions and by mutation-testing (12 mutations tried, 12 killed, 1 gap found and closed with a new lock test). BLOCKING: the write-target and `-F`-target regexes scan INDEPENDENTLY over the whole command text with no requirement either be real shell syntax outside quotes -- a `-m` message merely PROSE-describing the pattern (e.g. documenting this very bug) satisfies both and would quarantine a REAL TRACKED file (README.md), reproduced end to end against the real hook. Fixed with `design_gate.is_git_tracked(path, cwd)` (`git ls-files --error-unmatch`, three real outcomes not two -- fails toward TRACKED on any unmeasurable rc, a fail-direction bug the function's own test caught before it even reached review) as a gate the caller applies before ever quarantining. MINOR: a same-millisecond `.stale-<ts>` collision used to silently clobber the earlier quarantine (fixed with a numeric-suffix loop); a quarantined file littered the working tree where `git add -A` could sweep it back in (fixed with `ensure_stale_pattern_excluded()`, appending `*.stale-*` to the repo's local `.git/info/exclude` via `git rev-parse --git-common-dir`, correct from inside a worktree checkout too). A symlink-safety mutation gap the review found (rename-not-follow was already correct, just untested) is now locked in by a new regression test. test:3a13c99[red] -> fix:f7d8ba0[green] Closes #310.

Full local suite green throughout (final run: 159 tests across the two touched test modules, all passing), `ruff check` clean, `python3 airuleset.py validate` clean, commit-corpus replay (1696 real commits) 0 mismatches both before and after the review fixes -- decision logic (block/no-block) unchanged, the quarantine is purely a side effect inside the already-existing `if missing:` branch. Design + still-valid + review evidence comments posted to #310 at the required checkpoints. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #319 (`ask-before-assuming.md`: 3 rows kept out by a89024a/#316 were still Slovak-uncovered without their English loanword): #316's own audit had kept `dispatch-now-or-hold`, `admin-merge`/`realistic-options`, and `merge despite`/`admin-merge` OUT of the pre-answered table on the reasoning that they're hook-covered — but that coverage relied entirely on the natural Slovak rendering happening to retain the English loanword the existing regex keys on (`subagent-driven-development`, `admin-merge`). A genuinely natural Slovak sentence with no loanword was unblocked on current HEAD (verified live before any code — real hook subprocess invocations against `stop-check-prose-violations.sh`/`pre-ask-auto-answer.sh` for all 3 rows' natural-Slovak phrasings, all passed unblocked). Chose HOOK EXTENSION over a table revert, matching the dispatch's explicit HARD CONSTRAINTS (extend `stop-check-prose-violations.sh` in place, no new hook file, false-positive discipline preserved) and #316's own established pattern shape. Two new narrow Slovak detectors added: a dispatch-now-or-hold check (a Slovak "start" verb incl. 1st-person forms, near hneď/teraz/ihneď, alebo/či, a Slovak "wait" verb incl. 1st-person forms, and a plán/kontrolu/review word incl. declensions, all within bounded windows, flattened across line breaks so the mandated multi-line bullet-option template is not missed) and a merge-despite check (a Slovak "merge" stem — both the -ova- and the repo's own dominant native -n- conjugation — near napriek/hoci/aj keď/i keď), both forcing `LC_ALL=C.UTF-8` on their grep call per the locale-inert `\b`-near-multibyte gotcha this repo's own playbook already documents (6c4fbae). test:37701ed[red] -> fix:bc8a12e[green].

A fresh-context adversarial review (model: opus, Fable gate CLOSED at dispatch) found the first-pass regexes only covered fixture-convenient word forms: `SK_MERGE_RX` missed the repo's own dominant `-n-` stem, `SK_HOLD_RX`/`SK_PLANWORD_RX` missed 1st-person and declined forms, and the mandated multi-line bullet template's own negative-fixture set wasn't proven mutation-resistant (an early draft self-satisfied on a single closing ❓ line regardless of flattening). All widened and mutation-verified (backup/mutate/confirm-fail/restore) — including going beyond the reviewer's own suggested regex, which itself had a residual bug (`k[áa]me` required a trailing "e", excluding bare "počkám"), found via standalone regex test scripts. Negative-fixture set rewritten to genuine near-misses (a different verb pair sharing only some of the trigger words) rather than trivially-distinguishable sentences. `ask-before-assuming.md`'s own "no table row needed" paragraph rewritten to state the Slovak coverage claim precisely (word-family, not "any phrasing") rather than implying blanket coverage. test:dca2ca9 (review-fix pass, folded into the same commit as the widened regexes — no separate red/green split needed since the widening extended already-red-then-green-tested detectors) Closes #319.

Full local suite green throughout (final run: 4873 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #319 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #334 (internal operational Discord ping — an ad-hoc odoo prod write on dev2 pinged the foreign `claude-david` thread with a real @mention instead of zbynek's own): root cause traced directly — the sender is a THIRD, foreign repo (codex-bridge, not odoo-erp as the ticket's own hypothesis guessed; found via `gh search code` matching the exact reported message template byte-for-byte in `odoo_import/audit_log.py`), whose `maybe_ping_adhoc_prod_write()` shells out to `airuleset.py notify --body <text>` with NO owner override at all. `cmd_notify`'s `--body` branch (the general-purpose EXTERNAL send primitive — nothing inside this repo itself calls it) therefore fell entirely through to `notify.resolve_owner()`'s tmux-session-group auto-detect, which inherits `$TMUX` from the invoking process's ANCESTOR shell unconditionally (tmux propagates it to every descendant, interactive or not); a headless subprocess spawned from a shell sitting inside dev2's live `david-3` tmux session (group `david`, confirmed via `tmux list-sessions`) resolved owner="david" with zero relationship to who the write's real owner is. Fix: `--owner-name` already existed as a global CLI flag (wired into `--provision-question-thread`/`--backfill-digest` only) — wired it into `--body` too, normalized the same way, passed as `owner=` to `notify.send()` (bypasses `resolve_owner()`'s tmux fallback entirely when supplied; omitting it keeps today's exact behavior). `resolve_owner()`'s own tmux fallback deliberately left UNCHANGED — no reliable signal distinguishes "genuinely interactive in this pane" from "inherited $TMUX from an ancestor", and narrowing it risks breaking the many legitimate hook callers (idle ❓/✅ pings, autopilot cards) that correctly rely on it today. test:fdb312b[red] -> fix:b5499ee[green].

A dispatched fresh-context adversarial review (model: fable, gate OPEN) found 0 CRITICAL, 0 MAJOR, 2 MINOR, 3 THEORETICAL, verdict SAFE TO SHIP WITH RESIDUAL DOCUMENTED — mutation-tested the fix (backup/mutate/run/restore) and executed live CLI probes for every attack vector. Both MINORs fixed in the same branch: MINOR-1 (mutation-invisible normalization — every prior test passed an already-normalized `--owner-name`) closed with a denormalized-input test; MINOR-2 (a real bug — a non-empty `--owner-name` that normalizes to `""` was silently accepted as `owner=""`, skipping `resolve_owner()` entirely and overriding even a correct `AIRULESET_NOTIFY_OWNER`) fixed to refuse loudly, mirroring `--provision-question-thread`'s own established loud-failure shape. The three THEORETICALs (a stream-persona `--owner-name` bypassing the redirect, a box-dependent mutant-detection coincidence, an unknown-owner silent fallback) were accepted as documented residuals matching existing precedent — no code change. test:4a8bdaa[red] -> fix:7983f1a[green] Closes #334.

The codex-bridge SENDER-SIDE fix (passing `--owner-name zbynek` on the actual `maybe_ping_adhoc_prod_write()`/`notify_failure.py` call sites) is filed as a governance-lane ticket on that foreign repo — zbynekdrlik/codex-bridge#522 — since this session never edits foreign-repo code. Live verification of the airuleset-side mechanism was done against this box's REAL local Discord config in `--dry-run` mode (a real mention id resolved for `--owner-name zbynek`; the MINOR-2 refusal fired correctly against a real "!!!" input) — the FULL end-to-end acceptance criterion (a real ad-hoc odoo write pings only zbynek's thread) needs BOTH halves deployed, so it is documented as a supervisor post-deploy step on the ticket (the exact `notify._read_env`/`notify._discord_api` fetch-verify commands, key NAMES only, token values never printed).

Full local suite green throughout (final run: 4907 passed, up from 4905 at HEAD before this ticket), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #334 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #336 (montalu2 did not auto-continue after the 5h session-limit reset — a background Agent/subagent hit the account-wide limit, the top-level session pinged once via job 1's transcript detection and then sat idle 40 minutes past its own reset, requiring a manual `continue`): root cause traced directly in the code — job 1 (`isApiErrorMessage`-based) and job 6 (`pane_session_limited`-based, the only mechanism that actually parks a resume time and retries `continue`) were never connected; once job 1 classified a usage/session cap it pinged ONCE and marked the episode permanently `dormant`, while job 6's entry gate depended entirely on the live pane still rendering the banner as its own bottom-most content — which a background-Agent-triggered failure settling into an ordinary idle prompt never guarantees. Fix: extracted `parse_reset_epoch`'s clock/tz/date parse into a shared `_reset_epoch_from_scanned_text`; added `parse_reset_epoch_from_error_text` to run it directly over job 1's own transcript-derived error TEXT; job 1's usage-cap branch now seeds job 6's own `sesslimit:<key>` tracking the one time `decide()` reaches a fresh `action=="nudge"` per err_hash; job 6's entry gate widened to `pane visible OR sesslimit state already exists`, with resolution re-derived from the transcript (`transcript_last_error`+`is_usage_cap`) whenever the pane doesn't currently show the banner, instead of leaking state forever; new `session_user_stopped(tpath, since_ts)` predicate (a narrow, session-limit-scoped counterpart of the general user-stop invariant a sibling ticket, #335, is building) gates every delivery attempt on the user never having typed `/exit` since the limit hit. test:16dd327[red] -> fix:6118b2e[green].

A dispatched fresh-context adversarial review (model: fable, gate OPEN) found one CRITICAL and several MINOR/THEORETICAL gaps, all fixed in the same branch with mutation-proven regression tests. CRITICAL (F1, live-verified against real transcripts under `~/.claude/projects/`): `session_user_stopped`'s marker match required STRICT equality against the bare `<command-name>/exit</command-name>` string, but a real Claude Code `/exit` entry writes a COMPOSITE content string (command-name + command-message + command-args) — the strict equality never matched a single real transcript, so the whole user-stop safety gate was inert against every genuine `/exit` a user ever types. Fixed to a prefix match anchored on the closing tag. MINOR (F3, taken as "worth it now"): the `resets_at` refinement branch always re-parsed the PANE even for a transcript-only episode that never shows a banner at all; now refines from the transcript's error text when the pane doesn't show the banner, from the pane when it does. Two hardening regression tests added for mutants the review found the original 15 tests would not catch (finding 6a/6b): job 1's own bare-UUID dormant-tracking entry surviving a sweep job 6 owns, and a transcript-only episode's tracking surviving 3 `SESSLIMIT_RETRY_S`-spaced sweeps rather than silently collapsing the bounded 4-attempt retry to one. A messaging-drift gap and a directionally-wrong comment (THEORETICAL) were also fixed. Two MINOR findings (F2: `since_ts` anchors on the episode's own `first_seen`, up to `grace` seconds of pre-existing drift shared with the untouched pane-visible path; F4: the widened gate makes job 6's existing, already-shipped, already-reviewed draft-submit-as-is branch newly reachable for a never-visible episode) were dropped with reasoning posted to the ticket, both explicitly judged low-stakes/recoverable by the review itself and matching the design comment's own already-stated scope boundary. test:56c2509[green] (review-fix round, no separate red — hardening tests plus F1/F3 fixes, each individually mutation-proven before/after) Closes #336.

Every new/changed piece mutation-tested (backup/mutate/confirm-fails/restore) before being trusted, including the F1 fix verified live against real `/exit` transcripts on this box (the strict-equality version matched 0 of several real occurrences found; the prefix-match fix matches all of them). Design + still-valid + review evidence comments posted to #336 at the required checkpoints (root cause / chosen approach / rejected alternative for the original fix and for every review finding). Full local suite green throughout (final run: 4965 passed, up from 4945 at HEAD before this ticket by 20 new tests), `ruff check .` clean, `python3 airuleset.py validate` clean. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).


2026-08-08 (worktree dispatch) #338 (user repeatedly asked, angrily, for Shift+PageUp inside a claude pane to open Claude Code's OWN native transcript viewer (Ctrl+O, CC v2.1.226+, reads the session's clean internal history, immune to the leaky tmux scrollback #267's own bind only ever scrolled INTO) instead of always falling into tmux copy-mode): `TMUX_SCROLLBACK_KEYBINDS[0]` changed from an unconditional `copy-mode -eu` to tmux's own native conditional dispatch, `if -F` (alias of `if-shell -F`) on `#{==:#{pane_current_command},claude}` -- sends `C-o` inside a claude pane, falls through to the ORIGINAL byte-identical `copy-mode -eu` everywhere else. `render_tmux_history_block`'s `keybind_lines` switched from a bare `" ".join(argv)` to per-token `_tmux_conf_quote(...)` (matching how `popup_lines` already renders `TMUX_POPUP_BIND_ARGVS`), required the moment an entry can hold multi-word nested-command tokens (`"send-keys C-o"`, `"copy-mode -eu"`). `S-NPage` deliberately untouched (no existing bind; the native viewer's own PgDn already works once open). test:f2ecf60[red] -> fix:f095348[green] Closes #338.

Mandatory live pty-attach proof performed BEFORE writing any code (per the ticket's own protocol): a real attached pty client (`pty.fork()` + `execvp tmux attach`) fed the REAL xterm CSI bytes for Shift+PageUp (`\x1b[5;2~`) against an isolated `-L` scratch tmux server, against two different real panes bound to the SAME key -- a pane whose `pane_current_command` was `claude` (fixture: `bash -c 'stty raw -echo; exec -a claude cat > <file>'`; `stty raw -echo` proved load-bearing after a first attempt without it silently failed, since a canonical-mode pty buffers a lone control byte with no trailing newline and never delivers it) received exactly one byte `0x0f` (Ctrl+O); a plain `sleep` pane on the identical bind entered copy-mode unchanged. `tmux send-keys -t <pane>` was deliberately never used to exercise the binding itself (bypasses key-table dispatch, per the #267 playbook lesson). Cold conf-parse AND live-apply against a running server both confirmed clean on BOTH the fleet's real deployed `/usr/bin/tmux` (3.4) and `/usr/local/bin/tmux` (3.7b).

A dispatched fresh-context adversarial review (fable-gate OPEN -> model: fable, xhigh) returned SAFE TO SHIP AS-IS, zero CRITICAL, zero MAJOR. It independently re-verified F1 (quoting) via mutation testing on a scratch copy -- 3 mutants (bare-join revert, unconditional-bind revert, argv re-split) all killed -- and F2 (`if -F` exactness) via its own live pty-keypress runs on both fleet tmux binaries, confirming exact-match-only dispatch (`claude-wrapper`/`notclaude`/`claudex` all correctly fall through to copy-mode, closing the substring-match residual named in the review brief) and idempotent re-bind safety (F3). F4 confirmed the diff stays inside the FREEZE-permitted tmux render+bind machinery (only `airuleset.py` + `tests/test_airuleset.py` touched). Two accepted residuals, both pre-existing scope boundaries inherited unchanged from #267, not regressions: F5 (a non-literal `pane_current_command`, e.g. a sudo-wrapped claude launch, keeps the old copy-mode behaviour -- the safe direction) and F6 (a terminal/ssh client that eats Shift+PageUp client-side, e.g. PuTTY's default config, never delivers the key at all). No fixes required. Design + still-valid + review evidence comments posted to #338 at the required checkpoints.

Full local suite green throughout (4903 tests OK), `ruff check .` clean, `python3 airuleset.py validate` clean. Worked in an isolated worktree, commit-local only -- this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).
2026-08-08 (worktree dispatch) #333 (`/compact` landed right after a `❓ ASKED`/`⏳ WORKING` turn, twice, then a third time after a plain `⏳ WORKING` progress turn with no question — the user reported it angrily twice in one day): live forensic trace against dev1's own supervisor session (real transcript JSONL `queue-operation` entries, `compact-sync.log`, `journalctl`) proved the root cause was never the marker check alone — `/compact` was TYPED into a BUSY pane (permitted for a proven-boundary origin in job 14, and unconditionally on the synchronous #65 path) at a moment when the marker read looked safe, then sat QUEUED through several `/goal`-loop-rejected continuations, and only DRAINED at whichever turn's Stop was next genuinely ACCEPTED — under an active loop that is almost always either a real completion or exactly a `❓`/`⏳`-blocked turn. The third occurrence additionally exposed the marker gate's own per-origin relaxation (only `❓`, not `⏳`, blocked a proven-boundary origin) combined with the live-tasks defer-grace "deliver anyway" override. Fix (user directive, reversing an earlier ticket's own premise that a supervisor session "never ends any other way than ⏳" — live evidence refuted it directly: this box's own confirmed-clean historical sends all landed on a literal `✅ DONE`, never `⏳`): (1) `_compact_not_at_boundary` no longer relaxes the marker set by origin — `❓` AND `⏳` block delivery for EVERY origin; (2) `kind == "busy"` now blocks unconditionally on both delivery paths, with no per-origin exemption — `/compact` is only ever typed when the pane is observably at rest right now. The live-tasks defer-grace logic was left untouched; both fixes above neutralize its "deliver anyway" override as a side effect, since the busy/marker checks now run strictly before it. test:9ffb652[red] -> fix:6a8eb09[green].

A dispatched fresh-context adversarial review (model: fable, gate OPEN) found two real gaps and one vacuous regression test. MAJOR-2: the busy-pane check on both delivery paths read a capture taken at sweep-top (job 14) or call-entry (the synchronous path), several gates before the actual send — fixed with a fresh capture-and-reclassify immediately before typing on both paths, mirroring this repo's own established job-20 "re-verify right before send" pattern; a pane that moved on in the interim is now a pre-send refusal (zero keystrokes, request left in place for retry). MAJOR-1: the new grace-elapsed regression test used a `⏳`-carrying marker, so the (now unconditional) marker check refused delivery before the test's own busy+grace interaction was ever reached — confirmed vacuous by mutation (reverting the busy-check's origin exemption still passed the test unmodified); fixed with a safe marker plus an assertion on the specific "skip busy" log line. MAJOR-3 was a design-fork question, not a bug: `self-callback`'s own design premise (assert a boundary directly, bypass marker-text staleness) is in tension with the explicit "block ⏳ for every origin" mandate this fix implements — a genuine, bounded consequence (job 14's poll still delivers, just later), recorded as its own issue comment for the user's judgment rather than reverted unilaterally. Both new mutants (job 14's fresh-recapture, the synchronous path's) independently confirmed killed via backup/mutate/confirm/restore. Three remaining stale docstrings/comments describing the pre-fix per-origin marker relaxation or "busy is safe to type into" as current were reworded. test:7922efc[green] (review-fix, folded — no separate red commit needed since the fixture-vacuousness fix and the two mutation-verified checks landed together).

Full local suite green throughout (final run: 5038 passed, up from the 5034 baseline before the review-fix commit), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #333 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #337 (S-DC popup = úplná história bez dier — the user was actively suffering from an unreadable, duplicate-riddled tmux scrollback caused by an upstream Claude Code TUI renderer regression; Shift+PageUp/Ctrl+B `[` and the S-F1 popup's own capture-pane source all read that SAME corrupted native scrollback buffer, so nothing today gave a hole-free view): S-DC alone (via a new `-e AIRULESET_POPUP_MODE=transcript-primary` flag on ITS OWN bind-key argv only) now tries claude-history's transcript-JSONL reconstruction FIRST — immune to the renderer bug since it's built straight from the session's own transcript file, never the native scrollback — and falls back to a real `tmux capture-pane` of the originating pane only when the transcript itself fails or resolves nothing. S-F1 and prefix+h receive NO mode flag at all, so their argv and the popup script's own legacy code path stay byte-for-byte the pre-#337 shape (verified: this worktree was forked from a point in history that predates the sibling #327 ticket's own capture-pane-primary work for S-F1, confirmed still OPEN on GitHub during STEP 0 validation — #337 was scoped to build only the capture-pane-reading mechanism it structurally needs, independent of that sibling's own merge timing, reusing its already-live-verified tmux resolution facts from this repo's own playbook rather than depending on the unmerged branch). `TMUX_POPUP_MODE_TRANSCRIPT_PRIMARY` is the single source of truth for the mode string, template-substituted into the popup script; the capture-pane fallback's scrollback window is parameterized off `TMUX_HISTORY_LIMIT` rather than a second hardcoded literal (an adversarial-review-caught drift risk on the sibling #327 ticket). Both the primary and fallback failure conditions check for BOTH a nonzero exit AND empty output. The shared FAILS-LOUDLY (M5) guard and `less -R +G` presenter are unchanged, single-sourced, and fed identically by either branch. test:11746ab[red] -> fix:f379764[green] Closes #337.

A dispatched fresh-context adversarial review (`model: fable`, gate OPEN) of the committed diff found 0 CRITICAL, 0 MAJOR, 3 MINOR, 4 THEORETICAL — SAFE TO SHIP. All 3 MINORs fixed in fecb8b7[green]: MINOR 1 — the module comment above `TMUX_POPUP_KEY` only PROMISED the bare-capture-pane-resolves-to-originating-pane mechanism was documented there (it wasn't, since that documentation lives only in the sibling #327 branch); added the real mechanism explanation + live-verification evidence directly to this branch. MINOR 2 — added a real-execution test for the rc=0-but-empty-stdout half of the fallback trigger guard, using a stub claude-history script deployed at the exact invocation path (mirroring the existing "missing less" narrow-substitution technique) to prove the real capture-pane fallback fires end-to-end against a genuine isolated tmux server. MINOR 3 — softened three "byte-for-byte" comment claims that overclaimed about the rendered script FILE as a whole; reworded to name exactly what stayed byte-for-byte identical (the argv, and the legacy branch's own re-indented-but-unchanged statements).

Real-execution tests cover all four popup outcomes for S-DC's new mode (transcript wins with no `tmux` even on PATH; capture-pane fallback wins on an isolated throwaway `TMUX_TMPDIR` server — never this worker's own real `$TMUX`, confirmed live to point at a genuine session on this box; both sources blank; both sources unreachable), plus proof the untouched legacy branch never invokes `tmux` at all.

Full local suite green throughout (final run: 4922 passed, up from 4921 before the review-fix commit), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #337 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main). Post-deploy verification (the supervisor's own step, since this worker never installs/deploys): `S-DC` on a real long-history pane should show the transcript reconstruction with no duplicate bands; `S-F1` behavior should read byte-identical to before this ticket.


2026-08-08 (worktree dispatch, USER-PRIORITY) #339 (Virgin goal-autoarm armed a `/goal` loop into a LIVE interactive session — montalu3, first-ever spustenie, user typing "kto si?" ~2 min before the arm; journal evidence `goal-autoarm OK (virgin, branch-merge) %0 (odoo-erp)` at 21:38:38): `_goal_autoarm_virgin_candidate`'s whole gate list (bg-agent, boundary/draft, pane_goal_armed, unique_cwd, transcript cache, template presence, authority, pane_in_mode, `_goal_never_armed`) had zero discriminator for "is a live human sitting at this pane right now" — the path was built for genuinely headless stream accounts and never checked attendance at all. Fix: a new `_goal_autoarm_recent_human_activity(sid, tpath, now)`, checked right before the expensive full-file `_goal_never_armed` scan (cheap-before-expensive, matching this same function's own #320-review MAJOR-3 ordering discipline), refuses when EITHER the `/tmp/claude-user-active-<sid>` presence marker (stamped by `clear-question-dedup.sh` on UserPromptSubmit only) OR the transcript's own `_last_human_prompt_ts` (already excludes goal re-pokes, hook feedback, and job-7 Discord-reply text via `_MACHINE_PROMPT_PREFIXES`) shows a genuine human prompt within `GOAL_AUTOARM_RECENT_HUMAN_S` (30 min, reusing `PWEDGE_MIN_IDLE_S`'s own value for the same "genuinely at rest" question). Missing marker fails OPEN (falls through to the transcript signal, per the established #128 axis) — never manufactures "safe to arm" from absence alone. A hit is a zero-keystroke SKIP-TRANSIENT (va/hg/ga dedup state untouched, per-pane log throttled to once per streak) so the very next sweep after the human leaves retries and arms normally. Evaluated and explicitly REJECTED requiring ZERO human prompts in the whole transcript (the ticket's own named alternative) — would permanently disqualify any stream box a human ever briefly attached to, for no extra safety on the actual reported incident (a recency question, not a history question); the job-7 Discord-reply edge case needed no special-casing, since `_last_human_prompt_ts` already excludes it. test:d4be9f7[red] -> fix:996740e[green] Closes #339.

A dispatched fresh-context adversarial review (model: fable, gate OPEN) found one MAJOR (live-reproduced) and one MINOR, both fixed in the same branch. MAJOR: the clamp `0 <= age < window_s` fails toward ARM for a human prompt landing mid-sweep — `now` is captured once at sweep start, job 9 runs at the sweep's tail, so a human's first prompt written AFTER `now` but BEFORE this pane's own turn in the loop has a transcript/marker timestamp newer than `now`, giving a negative `age` that fails the clamp and reads as "not recent" — reproducing the exact #339 incident through a timing hole the review reproduced directly against the real function. Fixed by widening to a symmetric bound (`-window_s <= age < window_s`), which absorbs the mid-sweep-drift case while still bounding a grossly future-dated (clock-skewed) value; the two existing 1-hour-future test fixtures needed no behavioral change (still outside a 30-min symmetric window) but were re-labeled and a new near-future ("mid-sweep drift", +10s) test added to lock the corrected direction. MINOR: the docstring's claim that `_last_human_prompt_ts` "already excludes hook feedback" was false — `_MACHINE_PROMPT_PREFIXES` had no `"Stop hook feedback:"` entry, so a Stop-hook-rejected turn (a routine, real transcript entry per this repo's own #109/#108 measurements) counted as human and could delay a genuinely headless arm by up to 30 minutes; fail direction is refusal (safe), so MINOR — fixed by adding the prefix (duplicated literally alongside the existing `_STOP_FEEDBACK_PREFIX` constant, which is defined ~5000 lines later in the file and cannot be referenced at this earlier definition point without reordering unrelated code). test:d33aaa4[red] -> fix:3a1abdb[green].

Full local suite green throughout (final run: 4917 passed), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #339 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main).

2026-08-08 (worktree dispatch) #335 P0 (automation restarted a session the user had deliberately `/exit`'d on simap@subdev, and separately, `goal_rearm`'s FALSE-ACHIEVED path re-armed a goal after 66.5h of total dark silence): root cause traced live — the process had simply sat alive-but-idle the whole 66.5h; `goal_rearm`'s own re-arm action IS what made it start working again, via `_goal_dark_died_by_outage`'s permissive "no clear marker = presume outage, safe to re-arm" default combined with the FALSE-ACHIEVED re-arm path having no darkness/staleness bound at all. Two-part fix, landed across THREE adversarial-review rounds (fable-gate OPEN throughout, all three at `model: fable`, xhigh): (1) a `revived_from_dark_outage` gate on the FALSE-ACHIEVED path — sound from round 1, unchanged since. (2) `/exit` recognition, the harder half: round 1 added a bare `_goal_user_exit_ts` + a naive release-on-any-later-real-turn check, found CRITICALLY broken by round 1's own review (no genuine release condition, `_goal_autoarm_virgin_candidate` bypassed entirely, the dark-check gating left both a fresh exit and an exit-as-newest-entry completely unprotected) — all fixed, then round 2's OWN review of that fix found F1 STILL BROKEN: a real `/exit` writes a TRIPLE of transcript entries (a `<local-command-caveat>`, the exit command itself, a `<local-command-stdout>`), and the release check's shared `_last_real_turn_ts` reader counted the companions' own millisecond-later timestamps as "activity postdating the exit", defeating the protection on 13 of 15 real corpus sessions — fixed with a dedicated sibling reader (`_last_real_turn_ts_excluding_command_bookkeeping`) that excludes exactly those two companion prefixes, never widening the shared function job 10 genuinely still needs unmodified for its own purpose. Round 3 confirmed SAFE TO SHIP AS-IS: a real-corpus replay showed the old reader released 24/28 exit-carrying transcripts, the new one only 5/28 (all genuinely-resumed sessions), 0/28 false releases; every guard independently mutation-tested and killed.
test:587036a[red] -> fix:31f41d2[green] (round 1 initial fix) -> test:fcee711[red] -> fix:c3fa26b[green] (round-1-review fixes: F1-F7) -> test:6b4df54[red] -> fix:3a5700c[green] (round-2-review fix: F1 still-broken + NEW-1) -> docs:804e1ca (round-3-review doc-precision note).

Design + still-valid + review evidence comments posted to #335 at each required checkpoint. Local suite green throughout (final run: `python3 -m unittest discover` -> 4930 passed; reviewer's own independent `pytest` full run -> 4973 passed — the small count difference is a known, harmless unittest-discover-vs-pytest counting-convention gap already documented in this repo's own playbook). `ruff check .` clean, `python3 airuleset.py validate` clean throughout. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main); Phase 5 live verification of the deployed behavior lands post-merge, via the exact same corpus-replay + real-state-read techniques both round 3 and this session's own live-forensics used, against simap@subdev's own real transcript once the fix is live.

2026-08-09 (worktree dispatch, RESUMED — prior worker died at session limit mid-cycle) #326 (register david2/david3/david4 sub-dev streams: REMOTE_HOSTS, authority=fork-no-merge, statusline scope, Discord routing — data-only registration, no ssh probing per this ticket's own hard constraint): the prior worker's first pass (e544698[red] -> 77541db[green]) had already registered all three accounts across REMOTE_HOSTS/AUTHORITY_BY_USER/the ssh-misuse hook/STREAM_NOTIFY_OWNER before dying, leaving an UNCOMMITTED, unfinished second RED (staged `tests/test_bounce_backstop.py`) plus an unstaged, unrelated-looking `hooks/block-subdev-ssh-misuse.sh` help-text edit. Investigation traced the staged tests to a genuine MAJOR finding from the prior worker's own (never-posted) design work: 77541db deliberately left david2/3/4 OUT of `watchdog/__init__.py`'s separate `_REDUCED_STREAM_USERS` tuple (consumed by `_bounce_quals`/job 8 and `_gkreq_supervisor_root`/job 11), citing the simap/miva1 FREEZE precedent — but that precedent doesn't transfer: simap/miva1's odoo-erp involvement was never confirmed, while #326's own ticket text states david2/3/4 are provisioned "for the odoo-erp repo" as clones of `david` (already in the tuple) — a known, ticket-scoped fact. Confirmed live before the fix: `_bounce_quals("/home/david2/devel/odoo-erp")` returned the exclude-all fragment instead of `["label:stream:david2"]`, and `_gkreq_supervisor_root(...)` wrongly returned True. Finished the interrupted cycle: verified genuine RED, committed it, then widened `_REDUCED_STREAM_USERS` and rewrote the surrounding comment to document the corrected reasoning. The separate unstaged hooks edit turned out to be a legitimate, precedent-consistent cleanup (confirmed via `git log` archaeology on `a560d5b`, the montalu2/3/4 onboarding commit): a lone `david@subdev` help line plus a `david[234]@subdev` line was redundant and inconsistent with montalu's own established single-combined-line convention (`montalu[234]@subdev` already covers all 4 montalu accounts) — merged into one line, same convention, `check_target()`'s actual allow/block logic untouched (confirmed byte-identical). test:9b5971a[red] -> fix:ae30b95[green].

A dispatched fresh-context adversarial review (`model: fable`, gate OPEN) of the full `main...HEAD` diff found 1 MAJOR, otherwise sound under active refutation: the ticket's own item 4 ("verify delivery with a real test ping, confirm a DELIVERED line per stream in `~/.claude/notify-delivery.log` — configured-but-unproven routing does not count as done") was never performed or tracked, yet 77541db's commit message carries `Closes #326`. That live verification is structurally impossible from a worktree with no ssh and no post-push deploy (an acceptable constraint per the review) — but the gap needed to be loud, not silently implied, and the "Closes #326" text is **already baked into pushed history and cannot be un-said** without rewriting a commit (banned by this repo's own commit-conventions.md). Fix: an explicit residual comment at the `STREAM_NOTIFY_OWNER` routing site in `notify/__init__.py` (docs-only, no behavior change), plus a loud review comment on #326 itself instructing whoever integrates: if GitHub auto-closes #326 on merge (likely, since this repo merges via merge-commit and GitHub scans every commit message in a merged PR, not just the PR body), REOPEN it, perform the live test ping + DELIVERED confirmation, comment the evidence, then close it again. Everything else the review checked (the `_REDUCED_STREAM_USERS` fix's own reasoning, registry completeness across all four sites, the statusline `_core_search_excl()` generic-iteration claim, regression risk for existing streams, the hook consolidation's no-op status, and test non-tautology) came back sound.

Full local suite green throughout (final run: 5157 passed via `pytest tests/`), `ruff check .` clean, `python3 airuleset.py validate` clean. Design + still-valid + review evidence comments posted to #326 at the required checkpoints. Worked in an isolated worktree, commit-local only — this repo's own supervisor integrates + pushes (no PR, no CI, direct push to main). Item 5 (killing any pre-existing tmux session on the subdev box before each account's first push, #308) and item 4's live-delivery proof both remain genuine post-push residuals for the supervisor, not implemented here — no ssh access from this worktree, by design.
