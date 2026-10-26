### References Carry Their Meaning — Never a Bare #N

**Every issue/PR number in user-facing prose carries its title or topic** right next to it (`#42 (karaoke sanitizer)`, `PR #7: Refactor driver.rs`), copied from `gh issue view <N> --json title`, never guessed. Never a bare range or pile (`#684–#740`): name the few that matter, or describe the group in plain words. Exception: a bare `Closes #42` inside a commit message.

**An Odoo Discuss thread = its NAME + clickable deep URL, never a bare channel id** (#657, extends #650/#595): `vlákno „Zakaznicky portal 3" — https://<instancia>/odoo/discuss?active_id=discuss.channel_288`, confirmed live before pasting. Backstop: `stop-check-prose-violations.sh`.

**Odoo Task References in owner questions** (#907): a `❓` question about a client's Odoo board task carries the task name + a FUNCTIONAL action URL (`/odoo/project/<pid>/tasks/<tid>` or `/odoo/action-<xmlid>/<id>`, never the raw model-form `/odoo/project.task/<id>`) + its current stage; a GitHub issue only rides alongside. No board task (infra, slovnormal #1258) → no task line at all (owner 26.10.2026).
