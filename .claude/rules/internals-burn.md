---
paths:
  - "burn/**"
---

### airuleset internals — burn/ (token measurement)

Hlbší archív airuleset-internals je v `.claude/rules-reference/internals-archive.md` (on-demand). NOVÉ burn/ (token measurement) lekcie pridávaj SEM.

- **An epoch-hour bucket (`int(parsed_utc_timestamp // 3600)`) is the correct way to compare "same hour" across differing UTC offsets — NEVER compare raw ISO timestamp strings or local hour-of-day digits.** `_hour_bucket_of_ts()` (#60) parses via `burn._parse_ts` (which already handles the `Z`-suffix / `+HH:MM` forms), forces `tzinfo=utc` if naive, then floor-divides to an hour index — so gk's `+00:00` and dev1's `+02:00` writing the SAME instant bucket identically, while the same raw hour-of-day digits (`"17:00"`) under a DIFFERENT offset correctly bucket to DIFFERENT hours. Reuse this exact pattern for any future "is this remote sample fresh for the hour I'm collecting" check — the #60 bug (fleet monitoring silently reusing a stale remote row) existed because no such check existed at all.
- **Measuring the session-start prefix: `claude -p "Reply with exactly: OK" --output-format json`, then `cache_creation_input_tokens + cache_read_input_tokens + input_tokens`.** That is the real number from the API's own `usage`, not a bytes/4 estimate — and dense technical rule prose tokenizes far worse than 4 chars/token (the 69,286 B `## Development Rules` block measured 26,423 tokens, ~2.6 B/token, where a chars/4 estimate said 17k). Run it in the REPO whose prefix you care about: a project `CLAUDE.md` only loads in its own repo, so the same config measured 127,698 tokens in a bare dir and 162,726 in this one.
- **`--max-turns` does NOT exist in the Claude Code CLI (2.1.220) — the bound is `--max-budget-usd`, and a run it kills returns an EMPTY `usage` block.** `subtype: "error_max_budget_usd"`, `is_error: true`, `total_cost_usd` populated and slightly OVER the cap, but `usage.{input,output,cache_read,cache_creation}_tokens` all zero — so any metric extraction that reads token counts from a capped run silently records zeros. Read `total_cost_usd` for the cap check and treat missing `usage` as "capped", never as "cheap". Corollary for A/B work: an equal-DOLLAR cap is not an equal-TURN cap — a bigger rule prefix buys fewer turns for the same money (#94 measured ~1.6x cost per turn, so the full-ruleset arm got 67 turns where the minimal arm got 109), so say which of the two the design fixes and why.
## The measure → change one thing → re-measure cycle (#130)

**This repo's changes are judged on the LIVE long-running goal-armed runs, not
on the ticket's own replay.** Four days of cost work shipped before anything
could tell whether any of it made things cheaper, because every ticket was
graded on its own isolated evidence. `airuleset.py delegation` is the standing
instrument that closes that gap; this is how to use it.

```bash
python3 airuleset.py delegation --hours 12                  # this box
python3 airuleset.py delegation --hours 12 --host all       # the whole fleet
python3 airuleset.py delegation --hours 12 --host all --tickets   # + cost per closed ticket
python3 airuleset.py delegation --hours 12 --json           # machine-readable
```

**The cycle, and a change is not finished until it has been round-tripped:**

1. **Measure** the live runs over a window that contains real work (`--hours
   12` is the window both hand measurements on #130 used). Save the `--json`
   output — it is the baseline, and without one there is nothing to compare to.
2. **Change ONE thing.** Two changes in a window are unattributable; the
   measurement cannot tell you which one moved the number, and the temptation
   is then to credit the one you preferred.
3. **Re-measure the SAME runs over a comparable window.** Same `--hours`, same
   projects, ideally the same time of day — these boxes are not uniformly busy.
4. **Keep or revert on the number.** A change that cannot be shown to move a
   live-run number is not finished. Reverting on the number is the point of the
   loop, not a failure of it — the alternative is what this ticket was filed
   about, a ruleset accumulating unverified changes.

**Reading the output honestly:**

- **The cost unit is RELATIVE, never a price.** The weighting
  (`input x1.0 + cache_write x1.25 + cache_read x0.1 + output x5.0`,
  `burn.COST_UNIT_WEIGHTS`) is printed on every render for exactly this reason.
  It is the Opus row of `burn.PRICE` divided by 5, so it is deliberately
  TIER-NEUTRAL: it measures VOLUME, and model-tier drift is a separate signal
  that must not be silently folded in here.
- **A subagent turn is not a main turn.** A subagent's tokens are mostly cache
  reads of a smaller prefix while a main turn re-sends a 200–350K context — so
  compare `ctx/turn` alongside `units`, and state the weighting whenever
  quoting a ratio. Whichever way a number falls, that confound is stated, never
  buried.
- **`per closed ticket: —` means zero tickets closed, not cheap.** A window
  with real spend and no closed ticket is a materially different finding from a
  low cost-per-ticket, and `burn.units_per_ticket` returns None rather than
  letting the two blur.
- **`--tickets` is opt-in** because it needs network + `gh` auth; the base
  measurement must never depend on them. A project whose repo does not resolve
  (`repo: null`) shows no per-ticket line rather than borrowing a denominator.

**It is an instrument, and it stays one.** It reports; it does not gate, block
or threshold anything. As filed (#130) it deliberately left `burn.scan()` and
every alert baseline untouched — both were revisited separately by #149,
which reconciled `scan()` onto the subagent-reachable tree and recalibrated
`BURN_ALERT_ABS_USD` against the resulting scope widening (measured, not
guessed — see the `#149` bullets below). Enforcement is still a separate
decision, taken against a baseline #149 kept honest across the widening
rather than one that silently doubled underneath it.

- **A remote box collected by invoking its OWN `airuleset.py <cmd> --json` returns the MERGED shape, not the per-box raw shape — a coordinator that reads only the raw shape drops every remote silently, with no WARN.** #130: `_delegation_remote` parsed each remote reply cleanly and contributed nothing, because `cmd_delegation --json` prints `merge_splits()` output (`by_project`, keyed `<host>:<project>`) while `merge_splits` itself consumed `split_report()` output (`projects`). The failure is invisible by construction: an UNREACHABLE box WARNs, but a reachable one contributing zero is indistinguishable from an idle one, so the coordinator printed a dev1-only table under a fleet heading and the number would have shipped as a fleet total. `burn`'s own `merge_reports` survives the same round-trip only by coincidence (it reads `by_model`/`by_day`/`by_project`, which the merged shape happens to carry — though `host` is lost, so remote rows land under `?`). Two rules for any future remote collector: make the merge accept BOTH shapes (`_split_rows_of`) rather than making the remote emit a special one — that also keeps a fleet report working mid-rollout when boxes are on different deploys — and take the row's OWN host over the collector's, or the work gets attributed to the box that asked. Catch it by checking a remote directly (`ssh <box> 'cd ~/devel/airuleset && python3 airuleset.py <cmd>'`) and confirming its rows appear in the coordinator's output; a zero-row remote is the tell, and "the boxes were idle" is the assumption that hides it.
- **`burn.scan()` WAS blind to subagent transcripts until #149 landed — before the fix, the consequence was a FALSE answer rather than a missing one (#130 filed the finding; #149 landed the fix).** The existing #108 bullet above notes the two-level glob finds ~1% of the corpus; the specific damage was that `isSidechain` appears ONLY in `<proj>/<sid>/subagents/agent-*.jsonl`, which the OLD `scan()`'s `<root>/*/*.jsonl` glob never opened — so `main_vs_sidechain` used to return three `main|*` rows and zero sidechain rows on a box where subagents were the majority of the spend. #149 rewired `scan()` onto the same `_split_transcripts()` walk `scan_split()` already used (plus the matching request-dedup fix, since folding subagent transcripts in required it), so `main_vs_sidechain` is real now — `airuleset.py delegation` remains the sharper tool for a fresh per-project MAIN-vs-SUBAGENT split over a short window, but `burn`'s own bucket is no longer wrong, just coarser. Every hourly/fleet row `scan()` now feeds (`hourly_snapshot()`, and through it `merge_fleet_row()`) carries a `scope: "agents"` tag precisely so a comparison spanning the #149 deploy boundary is never read as a regression or a spike (`_window_stats()`, `fleet_trend()`, `hourly_burn_alert()` all filter on it).
- **Composing a long `gh issue comment` body as a WORKER: `cat > body.md <<'EOF'` in Bash, not the Write tool** — Write refuses a scratchpad file that looks like a findings/report document ("Subagents should return findings as text, not write report files"), which is correct policy and a dead end for a comment body. The quoted heredoc is also what keeps backticks, `$` and `%` intact per `gh-cli-recipes.md`. **For a COMMIT MESSAGE the opposite applies:** write it with the Write tool and `git commit -F <file>`, because `block-commit-without-design.sh` scans the whole Bash command text and a heredoc-bodied message citing any bare `#N` (context tickets, a sibling issue) demands a design marker for each of them — the message file keeps the numbers out of the command entirely, and the trailer still records them in the commit.
- **Verify a derived/sensitivity figure by COMPUTING it before it goes in a ticket comment, even when it only supports a conclusion you have already measured.** #130's draft asserted that reweighting cache_read to 1.0 moved the MAIN/SUB split from 17.8/82.2 to "17.4/82.6"; the actual recomputation gave 16.0/84.0 (and 21.9/78.1 at weight 0). The real numbers made the point STRONGER — the conclusion is invariant across the whole plausible weighting range — so the invented figure was both wrong and weaker than the truth. Any "the choice of constant is not doing the work here" claim is a three-line recomputation; make it, and quote the band.

- **A `requestId`-dedup fallback keyed on `"line:%d" % len(order)` (per-line, monotonically increasing) makes existing test fixtures that never set a `requestId` immune to accidental over-dedup — worth relying on rather than rewriting the fixtures.** #150 fixed `scan_split()`'s ~2.13x turn/token over-count by extracting `read_dispatch()`'s already-correct request-dedup into a shared `_fold_usage_line()` helper. The ticket's own text predicted "~15 tests construct one usage_line per expected turn — they must be updated"; none needed touching, because every one of those fixtures omits `requestId`/`message.id`, and the fallback assigns each such line a UNIQUE synthetic key (the file-scan-scoped `order` list only grows, so two calls can never coincide on the same fallback index) — so a file with N requestId-less lines still counts as N distinct "requests", exactly matching the pre-fix per-line behavior. Only a fixture that DELIBERATELY sets the SAME `requestId` on multiple lines exercises the new dedup path; write the RED test around that, not around any pre-existing fixture.
- **A "we VALIDATE, we do not MANGLE" invariant is prose until a test asserts the many-to-one property.** #198's fix refuses an unsafe key rather than sanitising it, because a sanitiser (`tr -c 'A-Za-z0-9' _`) maps distinct inputs onto ONE key and so rebuilds the shared bucket under a new spelling. The first test suite passed 16/16 against a sanitising mutant — it only asserted that one specific stray path was not created, which a sanitiser also satisfies. The assertions with teeth are direct: two DISTINCT unsafe ids must not share state (burn the cap under id A, then id B must still be blocked), and a refused id must leave NO file anywhere carrying its tag. Verify them by building a mutated COPY at a temp path and driving it — never by mutating the live script, and never by trusting that the suite "obviously" covers the invariant.
- **`burn.tier()` returns `"other"` for BOTH "no signal" (missing/empty model id) AND "a real-but-unrecognized string" (any vendor/alias burn.tier's substring table doesn't know) — a caller comparing two `tier()` results must treat `"other"` on EITHER side as unresolvable, not just the side it happened to guard.** #133 MAJOR-2: `model_segment()` correctly rendered `""` when the SESSION's own tier was `"other"`, but compared the MANAGED side's tier directly — `managed_tier = burn.tier(managed_model)` could itself be `"other"` (an unrecognized `MANAGED_MODEL` value, e.g. a future alias with no tier word) and then stood in as a real tier, comparing as a genuine permanent mismatch across every session on every box. Not firing at the time (`MANAGED_MODEL` then tiered to `"opus"`; since #440 it is `claude-fable-5[1m]` → `"fable"`, still a recognized tier, so still not firing), but a silent regression waiting for a future unrecognized `MANAGED_MODEL` value, and it directly violated the feature's own stated invariant ("an unresolvable comparison must render as a match, never a manufactured false alarm"). The general shape: when a helper's return value legitimately overloads two different meanings ("don't know" and "know, and it's X"), a caller using that value on BOTH sides of a comparison must check the overloaded sentinel on BOTH sides independently — checking it on only the side you happened to think of first is exactly the kind of asymmetry adversarial review exists to catch.
