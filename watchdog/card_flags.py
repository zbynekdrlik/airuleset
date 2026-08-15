"""#297/#298 flag-reaction + card-reopen cluster (issue #433 item G, step 8).

Job 7's two ❓/❔-reaction (#297) and completion-card-reply (#298) extensions:
the at-rest pane-nudge helpers, the `gh` reopen/comment/label flow, the
flag-emoji detection, the flag-target resolution, and the exact-session
flag-prompt delivery.

Back-reference module (bare ``import watchdog``): every call to a name that is
a top-level ``watchdog`` package attribute (the tmux/pane/cross-stream helpers,
and ``clean_reply_text`` still resident in ``__init__``) goes through
``watchdog.<name>`` at CALL time, so the existing
``monkeypatch.setattr(watchdog, ...)`` seams keep working identically after the
move (the module-split design's #1 hazard). The three cluster-private constants
(``BOUNCE_REMARK_LABEL``, ``_FLAG_EMOJI``, ``_FLAG_PROMPT_TEMPLATE``) are read
module-locally — grep-proven unpatched by any test — and re-exported into the
package namespace by ``__init__``'s positional facade so ``watchdog.<CONST>``
stays resolvable.
"""
import json

import watchdog


BOUNCE_REMARK_LABEL = "prio:bounce"


def _repo_live_pane(repo_name, cwd_by_sid, panes_by_sid, run=None):
    """(sid, pid, cwd) of a live `claude` pane whose cwd's repo NAME matches
    `repo_name` (bare, case-sensitive as `gh`/`repo_name_for` render it), or
    None. Resolved via `repo_name_for` (a single local `git remote get-url`)
    — the SAME repo identity job 24/25 already key on, never the directory
    basename. Shared by #297's flag-delivery fallback and #298's
    post-reopen nudge, so both features agree on what 'a live session of
    that repo' means."""
    from notify import repo_name_for
    target = str(repo_name or "").strip()
    if not target:
        return None
    for sid, cwd in cwd_by_sid.items():
        if sid not in panes_by_sid:
            continue
        if repo_name_for(cwd, run=run) == target:
            return sid, panes_by_sid[sid][0], cwd
    return None


def _nudge_repo_pane(pid, cwd, run, text, dry_run, projects_dir, logs=None):
    """Best-effort: type `text` into a live pane at TRUE REST — job 8's own
    at-rest discipline (`_safe_to_bounce_nudge`), reused verbatim so a
    genuinely mid-work pane is NEVER interrupted (never a new mechanism, the
    exact same check `bounce_backstop` already relies on). A pane holding a
    foreign draft is stashed around (issue #35); a busy/copy-mode/live-work
    pane gets NOTHING this sweep. Returns True when delivered (or, in
    dry-run, would have been)."""
    captured = watchdog.capture_pane(pid, run)
    if watchdog.pane_in_mode(pid, run):
        return False
    if not watchdog._safe_to_bounce_nudge(captured, cwd, projects_dir):
        return False
    if not watchdog.pane_at_idle_prompt(captured):
        return watchdog._try_stash_nudge(pid, captured, text, run, dry_run, logs=logs)
    if not dry_run:
        watchdog.send_continue(pid, text, run)
    return True


def _card_remark_comment_body(text):
    return ("**Pripomienka z Discordu k done karte:**\n\n%s\n\n"
            "(Ticket bol automaticky znovu otvorený — dokonči ho podľa "
            "tejto pripomienky.)" % text)


def compose_card_reopen_nudge(issue):
    return ("Discord pripomienka: ticket #%s bol znovu otvorený s "
            "pripomienkou od užívateľa (je v poslednom komentári na "
            "tickete) — prečítaj si ju a dokonči ticket podľa nej."
            % issue)


def _gh_call(argv, input_text=None, timeout=25):
    """One `gh` subprocess call. Returns (ok, stdout) — ok=False on any
    failure/exception, stdout="" then. Auth via `_gh_env()`, the SAME
    fallback the ticket-fallback comment (`_gh_comment`) already uses."""
    import subprocess
    try:
        r = subprocess.run(argv, env=watchdog._gh_env(), input=input_text,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "")
    except Exception:
        return False, ""


def _card_reopen_flow(repo, issue, remark_text, gh_fn=None):
    """The #298 durable hand-off on `repo`#`issue`: reopen (best-effort —
    `gh issue reopen` on an already-open issue is idempotent, so a SECOND
    reply on the same card never double-reopens), comment the user's remark
    VERBATIM via stdin (never shell-interpolated), and apply
    `prio:bounce` — creating the label first ONLY when it is genuinely
    absent from `repo` (a read-only `gh label list --search` check FIRST,
    `gh label create` never `--force`'d — the #191-established discipline
    against clobbering an existing label's own colour/description on a
    shared repo).

    Returns True only when the COMMENT (the durable record of the remark)
    succeeded — reopen/label are best-effort on top of that; a repo whose
    label-list read itself fails still gets the `--add-label` attempt (gh's
    own error there is harmless — never blocks the comment, which already
    landed).

    MINOR-11 (#297/#298 review): `prio:bounce` is applied on EVERY `repo`
    here, unconditional on `_repo_in_cross_stream_flow()` — deliberately,
    since a #298 card-reopen is a plain user priority marker on THIS one
    ticket, not a cross-stream gatekeeper<->sub-dev hand-off signal. On a
    repo NOT enrolled in `_CROSS_STREAM_REPOS` the label carries no
    automated meaning at all (job 8's `bounce_backstop` only ever queries
    enrolled repos, per its own established discipline against reading a
    bare `prio:bounce` on an unrelated repo as a protocol artifact) — it is
    just a normal, human-visible priority tag here."""
    call = gh_fn or watchdog._gh_call
    call(["gh", "issue", "reopen", str(issue), "-R", repo])
    ok, _out = call(["gh", "issue", "comment", str(issue), "-R", repo, "-F", "-"],
                    input_text=watchdog._card_remark_comment_body(remark_text))
    if not ok:
        return False
    list_ok, list_out = call(["gh", "label", "list", "-R", repo, "--search",
                              BOUNCE_REMARK_LABEL, "--json", "name"])
    have_label = False
    if list_ok:
        try:
            have_label = BOUNCE_REMARK_LABEL in [
                x.get("name") for x in json.loads(list_out or "[]")]
        except Exception:
            have_label = False
    if not have_label:
        call(["gh", "label", "create", BOUNCE_REMARK_LABEL, "-R", repo,
             "--color", "D93F0B", "--description",
             "Bounce lane — jumps the autopilot queue, seeds next batch"])
    call(["gh", "issue", "edit", str(issue), "-R", repo, "--add-label",
         BOUNCE_REMARK_LABEL])
    return True


_FLAG_EMOJI = ("❓", "❔")


def _flagged_emoji(msg):
    """The ❓/❔ emoji NAME on `msg` with a nonzero reaction count, or "".
    Discord's own message object already carries a `reactions` COUNT
    summary on every channel fetch — no extra call is needed to DETECT a
    flag; verifying WHO reacted (`fetch_reaction_users`) is a separate call,
    spent only on a message this job actually TRACKS (see `_flag_target`)."""
    if not isinstance(msg, dict):
        return ""
    for r in (msg.get("reactions") or []):
        if not isinstance(r, dict):
            continue
        name = ((r.get("emoji") or {}).get("name") or "")
        if name in _FLAG_EMOJI and (r.get("count") or 0) > 0:
            return name
    return ""


def _flag_target(msg_id, qmap, cardmap):
    """What a flagged message id resolves to for #297 — a tracked ❓
    question (exact asking session known) or a tracked completion card
    (repo known). None when `msg_id` is untracked by either map (the
    message is not one job 7 can act on — silently not actionable, never a
    guess)."""
    q = qmap.get(msg_id)
    if isinstance(q, dict):
        return {"kind": "question", "session": str(q.get("session") or ""),
                "cwd": str(q.get("cwd") or "")}
    c = cardmap.get(msg_id)
    if isinstance(c, dict):
        return {"kind": "card", "repo": str(c.get("repo") or ""),
                "issue": c.get("issue")}
    return None


def _flag_delivery_target(target, panes_by_sid, cwd_by_sid, run=None):
    """(sid, pid, cwd, exact) of the pane to deliver a #297 flag-prompt
    into, or None. Prefers the EXACT asking session while it is still alive
    (question kind); otherwise falls back to the nearest live pane whose
    repo matches — the ticket's own explicit fallback clause, and the SAME
    resolution `_repo_live_pane` gives #298's post-reopen nudge.

    `exact` (adversarial-review MAJOR-1) tells the caller WHICH delivery
    gate to use: True only for the exact-asking-session branch, where the
    flagged message IS the very ❓ that session's transcript is sitting on
    — job 7's own idle/draft gate must apply there (never job 8's
    at-rest discipline, which REFUSES a session whose last marker is ❓,
    which would make the dominant #297 use case never deliver at all).
    False for every repo-fallback / card-kind resolution, where the target
    pane may be doing something UNRELATED and the stronger at-rest check is
    correct."""
    from notify import repo_name_for
    if target.get("kind") == "question":
        sess = target.get("session") or ""
        pane = panes_by_sid.get(sess)
        if pane:
            return sess, pane[0], target.get("cwd", ""), True
        cwd = target.get("cwd") or ""
        repo = repo_name_for(cwd, run=run) if cwd else ""
    else:
        repo = str(target.get("repo") or "").rstrip("/").split("/")[-1]
    if not repo:
        return None
    found = watchdog._repo_live_pane(repo, cwd_by_sid, panes_by_sid, run=run)
    return (found[0], found[1], found[2], False) if found else None


def _deliver_flag_prompt_to_exact_session(pid, run, text, dry_run, logs=None):
    """Deliver a #297 flag-prompt into the EXACT session that is (or was)
    asking the flagged question — job 7's OWN idle/draft delivery gate
    (`pane_at_idle_prompt`/`deliver_with_stash` via `_try_stash_nudge`),
    never `_nudge_repo_pane`'s job-8-style `_safe_to_bounce_nudge` check.

    Adversarial-review MAJOR-1: that stronger gate refuses ANY session
    whose transcript's last marker is ❓ ("never interject before the user
    answers") — but a tracked outstanding question's asking session is, BY
    CONSTRUCTION, sitting at exactly that marker (it is what fired the ❓
    ping being flagged). This is job 7's OWN everyday operating condition
    for delivering a REPLY, so the same gate is correct here too — a
    genuinely BUSY (mid-turn / dialog / copy-mode) pane still gets
    nothing, via `pane_in_mode`/`pane_at_idle_prompt` exactly as job 7's
    reply flow already enforces."""
    captured = watchdog.capture_pane(pid, run)
    if watchdog.pane_in_mode(pid, run):
        return False
    if not watchdog.pane_at_idle_prompt(captured):
        return watchdog._try_stash_nudge(pid, captured, text, run, dry_run, logs=logs)
    if not dry_run:
        watchdog.send_continue(pid, text, run)
    return True


_FLAG_PROMPT_TEMPLATE = (
    "Užívateľ označil túto tvoju Discord správu ikonkou ❓ ("
    "\"niečo sa mi na nej nezdá\"). Označená správa: «%s» — polož mu k tomu "
    "štruktúrovanú otázku (per user-questions-slovak.md: úvod čo/prečo, "
    "možnosti, jasné rozhodnutie), zisti čo konkrétne mu na nej nesedí, a "
    "konaj podľa jeho odpovede."
)


def compose_flag_prompt(flagged_text):
    text = watchdog.clean_reply_text(flagged_text)[:900] or "(bez textu)"
    return _FLAG_PROMPT_TEMPLATE % text
