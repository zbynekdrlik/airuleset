"""machine_prompts -- what MACHINERY (not the owner) types into a session.

ONE home for the prompt-classification constants (#1379). They used to live in
``watchdog/__init__.py``, where a Stop gate could not read them without
importing the whole watchdog package (~0.3 s); the gate family never imports
``watchdog``. ``watchdog`` re-exports them under their old private names
(``_MACHINE_PROMPT_EXACT`` / ``_MACHINE_PROMPT_PREFIXES``), ``watchdog.questions``
reads ``DISCORD_ANSWER_PREFIXES`` and ``watchdog.owner_stop`` reads
``INTERRUPT_PREFIX`` from here. Pure data, stdlib-free, no imports.
"""

# #522 -- a Discord-relayed answer IS a genuine user answer (the human answered,
# just via the phone): `watchdog.questions` counts it as human, while the
# stricter "typed DIRECTLY at the terminal" question treats it as machine-typed
# (it is in MACHINE_PROMPT_PREFIXES below).
DISCORD_ANSWER_PREFIXES = (
    "Odpoveď z Discordu:", "Odpoveď užívateľa na tvoju otázku")

# A prompt Claude Code writes when the owner presses Esc mid-turn
# ("[Request interrupted by user]" / "... for tool use]").
INTERRUPT_PREFIX = "[Request interrupted by user"

# Prompts TYPED BY MACHINERY into a session (watchdog nudges/deliveries,
# auto-armed /goal, harness task-notifications, slash-command echoes) must
# never count as "the user answered the pending ❓ at the terminal".
MACHINE_PROMPT_EXACT = ("continue",)
MACHINE_PROMPT_PREFIXES = (
    # #602 -- the OAuth-rotation 401-revoked resume prompt (OAUTH_REVOKED_NUDGE_TEXT)
    # is machine-injected by job 1, not a human answer, so its `user` turn must
    # not count as "the user answered at the terminal" (mirrors bare `continue`
    # in MACHINE_PROMPT_EXACT).
    "oauth-resume:",
    "stuck-check:", "Priorita: prio:bounce", "bounce-backstop:",
    "gk-request backstop:", "/goal ", "nudge:",  # #1157 s3: every machine nudge
    *DISCORD_ANSWER_PREFIXES,
    "<task-notification>", "<local-command", "<command-", "<system-reminder",
    # #339 adversarial-review MINOR: a Stop-hook-rejected turn is ALSO
    # machine-injected, not human-typed -- this repo's own
    # a `_STOP_FEEDBACK_PREFIX` constant used to carry the identical literal
    # for job 14's own `_stop_already_rejected` gate -- both retired by the
    # #402 compact collapse (that gate's whole premise, an earlier hook in
    # the SAME Stop-hook batch already rejecting the turn, does not apply
    # to compact's two surviving origins). The literal string itself stays
    # duplicated here regardless. Without this entry, a Stop-hook rejection
    # (a routine, real transcript entry -- #108/#109 measured thousands of
    # them corpus-wide) counted as a genuine human prompt and could delay
    # a genuinely-headless virgin arm by up to GOAL_AUTOARM_RECENT_HUMAN_S.
    "Stop hook feedback:",
    # #366 -- GOAL_QUESTION_PARK_TEXT (the "30 min bez odpovede" unanswered-
    # question backstop in watchdog) is ALSO a machine-typed nudge,
    # not a human answer -- without this entry a session whose ❓ went
    # unanswered 30+ minutes would have that nudge itself misread as "the
    # user answered", pruning the still-pending question entry. #366
    # review MINOR-1: this entry ALSO propagates into the derived
    # `_GOAL_BLOCKED_ANSWER_TRANSPARENT_PREFIXES` tuple in watchdog (built FROM
    # this list) -- verified correct for that sibling caller too (a park
    # nudge landing before the user answers a genuine ❓ NEEDS YOU must
    # stay BLOCKED, never read as "released"), locked by its own test in
    # `tests/test_goal_rearm.py`.
    "question-timeout:")
