### ci-monitoring.md — History & Incident Narratives

Moved VERBATIM from `modules/core/ci-monitoring.md` during context diet (#859 batch 2).
The poll recipes, hook pointers, and the "ALL jobs terminal" rule stay in the always-on module.

---

#### #110 probe results (2026-07-27)

This is the module's load-bearing line, and it is the one thing a model does NOT do unprompted — shown a run still in progress with lint and test passing, a rules-free model reported *"CI is green — no further action needed"* in 8 of 8 probes (#110, 2026-07-27).

#### gh run watch measurement

**Do NOT use `gh run watch`**, which is exactly what an unprompted model reaches for (8/8, #110) and is measurably the wrong tool: watching one real run cost **71 API calls and 9.7 KB of output per minute** — approximately 4100/h against GitHub's 5000/h limit — and `--interval 30` only brings that to ~2200/h because it re-polls every job.

#### Owner quote on polling cost (#107)

Each repeat is a separate full-context TURN, five in a row for one 2-hour run: *"preco monitoring nespravis tak aby si kazdych 9min nemusel spravit dalsi plateny token tah!!"*

#### Recovery archaeology (CC issue #29193)

The two issues this rule used to cite described the OPPOSITE failure mode and were wrong — the archaeology is in the playbook, not here.

#### Deploy overshoot incident (montalu5)

A worker "watching the deploy" long after the version is live on PROD is the trust-damaging failure (owner report, montalu5).

#### Shadow-gate run incident (#365, 2026-08-12)

A 50+-job shadow-gate run whose critical E2E job already failed can stay `in_progress` for hours before anyone notices (#365, 2026-08-12).

#### Subagent CI poll failure rate

~40% of autopilot-worker failures were caused by subagents backgrounding CI polls and terminating.
