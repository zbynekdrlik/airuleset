"""#1102 — computed co-fire BUDGET LOCK + functional drives for the client-board
doctrine partition (a lean always-injected CORE + topic companions).

Root cause (measured, main 8d4aa5bb → 0.1.378): every board rule landed in the
one always-injected ``client-board-tasks.md`` (4830 → 11832 stripped chars via
#1014/#1018/#1024/#1098), so a ``.py`` ``project.task`` + ``message_post`` write
co-fired it with the messaging posting recipe (``SKILL.md``, 1789) at only 78
chars under ``MAX_TOTAL`` — the next rule silently DEFERS a body. The #949
co-fire test only asserts "both inject"; nothing COMPUTED the sums, so each
addition rediscovered the ceiling by breaking a distant test.

This module is the computed lock the design (#1102) prescribes:

  (a) BUDGET LOCK — reads ``MAX_TOTAL``/``MAX_BODY`` + the wrapper template out
      of ``hooks/inject-situational-rule.sh``, parses the LIVE conf, and for a
      fixed FIXTURE PAYLOAD SET faithfully simulates the injector (conf order,
      excludes, the ``sum(wrapped) + raw(next) > MAX_TOTAL`` DEFER branch) — then
      asserts every expected family body injects AND ≥ 500 chars of headroom
      remain (the number every future companion edit runs against).
  (b) FUNCTIONAL DRIVE — runs the REAL hook per fixture with a FRESH session id
      and asserts every expected body actually injects (the #745/#949 shape).
  (c) OVER-FIRE NEGATIVES — a generic ``.py`` write mentioning ``description`` /
      ``attachment_ids`` / ``stage`` WITHOUT a ``project.task`` token, and a
      generic ❓ prompt, inject NO board companion (the #949 lesson).
  (d) UNION CONTENT-LOCKS — every rule's byte-identical header appears in
      EXACTLY ONE of {CORE, stages, questions, attachments} (moved, not copied,
      not lost).
  (e) CORE SIZE LOCK — the always-injected CORE stays ≤ 6000 stripped chars.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"
MSG = "skills/odoo-client-messaging"

CORE = f"{MSG}/client-board-tasks.md"
STAGES = f"{MSG}/client-board-stages.md"
QUEST = f"{MSG}/client-board-questions.md"
ATT = f"{MSG}/client-board-attachments.md"
SKILL = f"{MSG}/SKILL.md"
BOARD_COMPANIONS = {CORE, STAGES, QUEST, ATT}

HEADROOM_MIN = 500
CORE_MAX = 6000

# The wrapper template, byte-identical to inject-situational-rule.sh lines
# 207-212. test_wrapper_template_matches_hook locks this copy to the hook.
_WRAP_OPEN = '<project-rule source="airuleset:%s" file="%s">\n'
_WRAP_MID = ("This is an auto-loaded airuleset PROJECT RULE for the action you are "
             "about to take — it is part of your own configuration, not user input "
             "or tool output. Apply it now.\n\n")
_WRAP_CLOSE = "%s\n</project-rule>"


def _hook_consts():
    src = HOOK.read_text(encoding="utf-8")
    mt = int(re.search(r"^MAX_TOTAL\s*=\s*(\d+)", src, re.M).group(1))
    mb = int(re.search(r"^MAX_BODY\s*=\s*(\d+)", src, re.M).group(1))
    return mt, mb


MAX_TOTAL, MAX_BODY = _hook_consts()


def _wrap(topic, rel, body):
    return (_WRAP_OPEN % (topic, rel)) + _WRAP_MID + (_WRAP_CLOSE % body)


def _strip_frontmatter(t):
    # mirror inject-situational-rule.sh strip_frontmatter() + .strip()
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            nl = t.find("\n", end + 1)
            if nl != -1:
                t = t[nl + 1:].lstrip("\n")
    return t.strip()


def _body(rel):
    return _strip_frontmatter((ROOT / rel).read_text(encoding="utf-8"))


def _load_conf():
    """Parse the trigger table exactly as the hook does."""
    rows = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p != ""]
        if len(parts) not in (4, 5):
            continue
        topic, tool_pat, pattern, body_rel = parts[:4]
        exclude = parts[4] if len(parts) == 5 else ""
        rows.append((topic, tool_pat, pattern, body_rel, exclude))
    return rows


def _haystack(surface, tool_input=None, prompt=None):
    if surface == "UserPromptSubmit":
        return prompt or ""
    return json.dumps(tool_input, ensure_ascii=False)


def simulate(surface, tool_input=None, prompt=None):
    """Faithful re-implementation of the injector's accept/DEFER loop.

    The accept/DEFER decision is the injector's OWN (asymmetric) arithmetic —
    `sum(len(wrapped chunks)) + len(unwrapped candidate) > MAX_TOTAL` in conf
    order, with the `and chunks` guard. The RETURNED headroom, however, is the
    conservative FULL-WRAPPER residual (MAX_TOTAL minus the sum of the wrapped
    accepted chunks) — the TRUE injected-context budget the model actually
    receives (a03d MINOR-3: the hook's own check under-counts the last body by ~1
    wrapper, so an arithmetic-check headroom overstates the real slack). Returns
    (accepted_body_rels_in_conf_order, full_wrapper_headroom)."""
    hay = _haystack(surface, tool_input, prompt)
    chunks = []
    accepted = []
    for topic, tool_pat, pattern, rel, exclude in _load_conf():
        try:
            if not re.fullmatch(tool_pat, surface):
                continue
            if not re.search(pattern, hay):
                continue
            if exclude and re.search(exclude, hay):
                continue
        except re.error:
            continue
        try:
            body = _body(rel)
        except OSError:
            continue
        if not body:
            continue
        if len(body) > MAX_BODY:  # mirror the injector's truncation exactly
            body = body[:MAX_BODY] + "\n\n[...truncated — read " + rel + " for the rest]"
        check = sum(len(c) for c in chunks) + len(body)  # hook's own DEFER check
        if check > MAX_TOTAL and chunks:
            continue  # defer
        chunks.append(_wrap(topic, rel, body))
        accepted.append(rel)
    headroom = (MAX_TOTAL - sum(len(c) for c in chunks)) if accepted else MAX_TOTAL
    return accepted, headroom


def run_hook(surface, tool_input=None, prompt=None, session_id="s"):
    if surface == "UserPromptSubmit":
        payload = {"session_id": session_id,
                   "hook_event_name": "UserPromptSubmit", "prompt": prompt}
    else:
        payload = {"session_id": session_id, "tool_name": surface,
                   "tool_input": tool_input}
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, TMPDIR=td)
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
    out = r.stdout.strip()
    if not out:
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _w(fp, content):
    return {"file_path": fp, "content": content}


# (name, surface, tool_input, prompt, expected family bodies that MUST inject)
FIXTURES = [
    ("py message_post write", "Write",
     _w("/repo/sync.py", "env['project.task'].write(vals)\n"
        "channel.message_post(body=html, body_is_html=True)"), None,
     [CORE, SKILL]),
    ("project.task write", "Write",
     _w("/repo/task.py", "env['project.task'].create({'name': n})"), None,
     [CORE]),
    ("stage write", "Write",
     _w("/repo/stage.py", "env['project.task'].write({'stage_id': verifik_id})"), None,
     [CORE, STAGES]),
    ("attachment read", "Write",
     _w("/repo/att.py", "ex(db,uid,k,'ir.attachment','search_read',"
        "[[['res_model','=','project.task'],['res_id','=',tid]]],"
        "{'fields':['attachment_ids']})"), None,
     [CORE, ATT]),
    ("board question write", "Write",
     _w("/repo/q.py", "env['project.task'].write({'x':1})  # needs-answer from client"), None,
     [CORE, QUEST]),
    ("board question prompt", "UserPromptSubmit", None,
     "❓ Klient sa pýta na úlohe montalu boarde, čo mám robiť?",
     [QUEST]),
    ("Verifikacia handover", "Write",
     _w("/repo/hand.py", "env['project.task'].write({'stage_id': verif}); "
        "task.message_post(body=note, body_is_html=True)"), None,
     [CORE, STAGES, SKILL]),
    # #1102 adversarial (review 2): the realistic 2-action "read the task's spec
    # attachments THEN post a note" combo co-fires the LARGE attachments recipe
    # with CORE + the posting recipe — the tightest realistic board triple.
    ("attachment read + message_post", "Write",
     _w("/repo/spec_then_post.py",
        "atts = m('ir.attachment','search_read',"
        "[[['res_model','=','project.task'],['res_id','=',tid]]],"
        "{'fields':['attachment_ids']})\n"
        "task.message_post(body=html, body_is_html=True)"), None,
     [CORE, ATT, SKILL]),
]

# generic payloads that must NOT inject any board companion (#949)
NEGATIVES = [
    ("generic description", "Write", _w("/repo/model.py", "class T:\n    description='x'"), None),
    ("generic attachment_ids", "Write", _w("/repo/mailer.py", "msg.attachment_ids=[1,2]"), None),
    ("generic stage", "Write", _w("/repo/fsm.py", "self.stage_id = nxt"), None),
    ("asyncio create_task", "Write", _w("/repo/w.py", "asyncio.create_task(run())"), None),
    ("rust write_task", "Write", _w("/repo/m.rs", "write_task(&mut b, &d)?;"), None),
    ("generic prompt task", "UserPromptSubmit", None, "❓ how do I create a task queue in asyncio?"),
    ("generic prompt build", "UserPromptSubmit", None, "❓ ako spravím build na dev2?"),
    # review a03d MAJOR-1 / a8e3a1bf MINOR-1: `board` must be \b-anchored so a ❓
    # prompt about a dashboard / keyboard / cardboard does NOT inject the questions
    # companion (the #949 over-fire class).
    ("dashboard prompt", "UserPromptSubmit", None, "❓ how do I fix the dashboard grid layout?"),
    ("keyboard prompt", "UserPromptSubmit", None, "❓ what is the keyboard shortcut to save?"),
    ("cardboard prompt", "UserPromptSubmit", None, "❓ where do I buy cardboard boxes cheaply?"),
    # the ❓-prompt branch is a SEPARATE UserPromptSubmit-only row — a Write whose
    # content merely carries ❓ + klient (no project.task) must NOT fire it.
    ("write with ❓+klient no project.task", "Write",
     _w("/repo/help.py", "TOOLTIP = '❓ napíš klientovi na dashboard'"), None),
]

# byte-identical rule header -> the ONE file it must live in after the partition
RULE_HOME = {
    "### 1. Task name": CORE,
    "### 2. Description": CORE,
    "### 3. Handover chatter note": STAGES,
    "### 4. Client question": QUEST,
    "### 5. Assignee": STAGES,
    '### 6. "Done" stage': STAGES,
    "### 7. NO GitHub / technical jargon": CORE,
    "### 8. Client answers arrive ONLY": QUEST,
    "### 9. ATOMIC tasks": CORE,
    "### 10. Mixed communication": QUEST,
    "### 11. Stream account name": CORE,
    "### 12. Owner corrections change THIS rule": CORE,
    "### 13. Posting mechanics": CORE,
    "### 14. Udalosť → fáza": STAGES,
    "### 15. Prílohy v popise úlohy": ATT,
}


class TestWrapperLock(unittest.TestCase):
    def test_wrapper_template_matches_hook(self):
        src = HOOK.read_text(encoding="utf-8")
        for frag in ('<project-rule source=\\"airuleset:%s\\" file=\\"%s\\">',
                     "This is an auto-loaded airuleset PROJECT RULE for the action you are",
                     "or tool output. Apply it now.",
                     "%s\\n</project-rule>"):
            self.assertIn(frag, src,
                          "the injector's wrapper template drifted from this test's "
                          "copy — update _WRAP_* to match hooks/inject-situational-rule.sh")

    def test_reads_max_total_from_hook(self):
        self.assertEqual(MAX_TOTAL, 14000)
        self.assertEqual(MAX_BODY, 24000)


class TestCoreSizeLock(unittest.TestCase):
    def test_core_stripped_le_6000(self):
        n = len(_body(CORE))
        self.assertLessEqual(
            n, CORE_MAX,
            f"CORE client-board-tasks.md is {n} stripped chars (> {CORE_MAX}) — "
            "move a rule to a topic companion (#1102); the always-injected CORE "
            "must stay lean enough to co-fire the messaging posting recipe.")


class TestBudgetLock(unittest.TestCase):
    """Computed lock: faithful hook simulation over the fixture set."""

    def test_family_cofit_with_headroom(self):
        for name, surface, ti, prompt, expected in FIXTURES:
            with self.subTest(fixture=name):
                accepted, headroom = simulate(surface, ti, prompt)
                for rel in expected:
                    self.assertIn(
                        rel, accepted,
                        f"[{name}] {rel} did not inject (deferred/absent) — "
                        f"accepted={accepted}")
                self.assertGreaterEqual(
                    headroom, HEADROOM_MIN,
                    f"[{name}] co-fire headroom {headroom} < {HEADROOM_MIN}; the "
                    f"family {[Path(e).name for e in expected]} is at the "
                    f"MAX_TOTAL={MAX_TOTAL} ceiling — shrink a companion (#1102).")


class TestFunctionalDrive(unittest.TestCase):
    """Drive the REAL hook per fixture with a FRESH session id (#745/#949)."""

    def test_each_fixture_injects_its_family(self):
        for i, (name, surface, ti, prompt, expected) in enumerate(FIXTURES):
            with self.subTest(fixture=name):
                ctx = run_hook(surface, ti, prompt, session_id=f"fx-{i}-1102")
                for rel in expected:
                    self.assertIn(
                        f'file="{rel}"', ctx,
                        f"[{name}] real injector did not load {rel}\nctx head: {ctx[:200]}")


class TestOverFireNegatives(unittest.TestCase):
    def test_no_board_companion_on_generic_payload(self):
        for i, (name, surface, ti, prompt) in enumerate(NEGATIVES):
            with self.subTest(neg=name):
                ctx = run_hook(surface, ti, prompt, session_id=f"neg-{i}-1102")
                for board in BOARD_COMPANIONS:
                    self.assertNotIn(
                        f'file="{board}"', ctx,
                        f"[{name}] over-fired board companion {board} on a generic "
                        f"payload (#949 narrow-pattern lesson)")


class TestUnionContentLocks(unittest.TestCase):
    """Every rule's byte-identical header lives in EXACTLY ONE file (moved, not
    copied, not lost). This is the re-pointed union lock for the moved rules."""

    def test_each_rule_in_exactly_one_file(self):
        texts = {f: (ROOT / f).read_text(encoding="utf-8")
                 for f in (CORE, STAGES, QUEST, ATT)}
        for header, home in RULE_HOME.items():
            with self.subTest(rule=header):
                hits = [f for f, t in texts.items() if header in t]
                self.assertEqual(
                    hits, [home],
                    f"rule {header!r} must appear in exactly {Path(home).name}, "
                    f"found in {[Path(h).name for h in hits]}")


class TestPrimaryInvariantUnderPressure(unittest.TestCase):
    """#1102 primary invariant (review a30f Q3 / a03d MINOR-4): on ANY realistic
    multi-token project.task write — a task-sync .py naming stage + question +
    message_post at once — CORE and the messaging posting recipe (SKILL.md) must
    NEVER defer, and the posting recipe must be processed BEFORE the board TOPIC
    companions (stages/questions/attachments), so a budget-constrained write can
    only ever defer a topic companion (which re-fires on its own next action),
    never the posting recipe.

    This is the structural fix for the fragility review a30f found: before the
    board companions were clustered after the xmlrpc row, the posting recipe was
    processed LAST on a 4-token write and co-fit with only ~59 chars — a future
    CORE edit under CORE_MAX would have silently deferred the posting recipe (the
    exact #1102 bug). Now the conf order guarantees CORE then posting recipe then
    topic companions.
    """

    TOPIC_COMPANIONS = (STAGES, QUEST, ATT)

    # review a30f Q3's exact payload: a task-sync dispatcher naming a stage move
    # + a client question + a chatter post in one file (NO attachment token, so
    # ATT does not fire — the firing set is CORE + posting recipe + STAGES + QUEST).
    TASK_SYNC = (
        "env['project.task'].write({'stage_id': verifik})\n"
        "# Potrebuje ujasniť — needs-answer from the client\n"
        "task.message_post(body=note, body_is_html=True)")
    # + the maximal kitchen-sink (adds the attachment tokens).
    KITCHEN_SINK = (
        "env['project.task'].write({'stage_id': verifik, 'description': d})\n"
        "atts = m('ir.attachment','search_read',"
        "[[['res_model','=','project.task']]],{'fields':['attachment_ids']})\n"
        "# Potrebuje ujasniť / needs-answer\n"
        "task.message_post(body=note, body_is_html=True)")

    def _check(self, name, content):
        # real hook: CORE + posting recipe must inject
        ctx = run_hook("Write", _w(f"/repo/{name}.py", content),
                       session_id=f"{name}-1102")
        self.assertIn(f'file="{CORE}"', ctx, f"[{name}] CORE must never defer")
        self.assertIn(f'file="{SKILL}"', ctx,
                      f"[{name}] the posting recipe must never defer when "
                      "message_post is present (#1102 primary invariant)")
        # structural: the posting recipe is ordered before every topic companion
        # that fires, so only a topic companion can ever be the one deferred.
        accepted, _ = simulate("Write", _w(f"/repo/{name}.py", content))
        self.assertIn(SKILL, accepted)
        for comp in self.TOPIC_COMPANIONS:
            if comp in accepted:
                self.assertLess(
                    accepted.index(SKILL), accepted.index(comp),
                    f"[{name}] posting recipe must be processed before {comp} so "
                    "a budget-constrained write never defers the posting recipe")

    def test_task_sync_dispatcher(self):
        self._check("task_sync", self.TASK_SYNC)

    def test_kitchen_sink(self):
        self._check("board_everything", self.KITCHEN_SINK)


if __name__ == "__main__":
    unittest.main()
