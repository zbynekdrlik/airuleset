"""#745 — an emoji REACTION on OUR question message counts as a full answer.

Owner directive (2026-08-29, montalu3 session): „ale to nie je pravda ze speta
neodpovedal, dal palec hore na nasu spravu a to je plnohodnotna odpoved. Treba
dat pokyn airuleset aby sa sledovali aj odpovede cez emiji na otazku."

Incident: Pavol Špetta reacted 👍 to an acceptance question (montalu PROD,
discuss.channel_288, mail.message 1739648), the stream read only new MESSAGES
(never `mail.message.reaction`) and for 5 days reported „bez odpovede",
preparing a needless reminder — the owner had to fix it by hand.

Root cause (confirmed in the STEP-0 validation + design comment on #745): no
airuleset surface treated a reaction as a reply. The W-push doctrine
(`statusline-vocabulary.md` W bullet — #607 „či neprišla odpoveď", the
2026-08-16 „third party replies → CLEARS ops-wait", #570 `stale!`) reads only
new messages; the `odoo-discuss-xmlrpc` skill covered POST + attachments, never
reactions.

Fix (extend the EXISTING mechanism — rule-intake gate step 3, exactly like
#709): a #745 clause on the always-on W bullet + a NEW companion
`read-reactions.md` carrying the read recipe (the 403 obstacle + the fresh-
prod-copy fallback + the pending odoo-erp ACL cross-ref) + a content-bound
`Write|Edit` trigger row.

Content-lock tests use the #498/#500 per-line `_teeth` pattern (a finder token
unique to the operative line, co-tokens on that SAME line) plus a functional
drive of the REAL injector (#598: raw-body arithmetic under-counts the hook's
real wrapped budget, so only a functional drive proves delivery).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "modules" / "core" / "statusline-vocabulary.md"
COMPANION = ROOT / "skills" / "odoo-discuss-xmlrpc" / "read-reactions.md"
COMP_LOGGING = ROOT / "skills" / "comprehensive-logging" / "SKILL.md"
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"


def read(p):
    return p.read_text(encoding="utf-8")


def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[nl + 1:].lstrip("\n")
    return text


def load_conf_rows():
    rows = []
    for line in read(CONF).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p != ""]
        assert len(parts) in (4, 5), "malformed trigger row: %r" % line
        rows.append(tuple(parts[:4]))
    return rows


def hook_max_total():
    import re
    m = re.search(r"MAX_TOTAL\s*=\s*(\d+)", read(HOOK))
    assert m, "MAX_TOTAL not found in inject-situational-rule.sh"
    return int(m.group(1))


def run_hook(tool_input, tool_name="Write", session_id="sess-745", tmpdir=None):
    payload = json.dumps(
        {"session_id": session_id, "tool_name": tool_name, "tool_input": tool_input}
    )
    env = dict(os.environ)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


class _Teeth:
    """Per-line content-lock mixin (#498/#500)."""

    text = ""

    def _teeth(self, finder, *cotokens):
        hits = [ln for ln in self.text.splitlines() if finder in ln]
        self.assertTrue(hits, "finder %r not on any single line" % finder)
        for ln in hits:
            if all(tok in ln for tok in cotokens):
                return ln
        self.fail(
            "no single line carries finder %r together with all of %r; "
            "matching lines: %r" % (finder, cotokens, hits))


FINDER_745 = "Emoji reakcia na NAŠU otázku = plnohodnotná odpoveď (#745"


class TestStatuslineWBulletCarries745(_Teeth, TestCase):
    """The always-on W bullet (statusline-vocabulary.md) must count a reaction
    on our question as a full answer — the surface that reaches every stream
    AND every dispatched worker (#104)."""

    def setUp(self):
        self.text = read(STATUS)

    def test_745_clause_present_on_the_W_bullet_line(self):
        # the whole W bullet is ONE physical line; the clause + its operative
        # co-tokens must all sit on it.
        self._teeth(
            FINDER_745,
            "mail.message.reaction",
            "plnohodnotná odpoveď",
            "read-reactions.md",
        )

    def test_745_finder_is_unique(self):
        # #578 whole-line teeth rule: a partial revert of the #745 clause must
        # genuinely fail — the finder occurs exactly once in the whole module.
        self.assertEqual(read(STATUS).count(FINDER_745), 1,
                         "the #745 finder must be unique (partial-revert teeth)")

    def test_745_names_the_selfservice_read_path(self):
        # a reaction read is a prod-STATE read with a self-service path
        # (#500/#608) — the clause must not read as "can't verify".
        self._teeth(FINDER_745, "403", "REFRESH-DEV-BOX-FROM-PROD")


class TestReadReactionsCompanion(_Teeth, TestCase):
    """The NEW companion carries the concrete XML-RPC read recipe + the 403
    obstacle + the fallback + the pending odoo-erp ACL cross-ref."""

    def setUp(self):
        self.text = read(COMPANION)

    def test_companion_exists_with_no_frontmatter(self):
        # rides the odoo-discuss-xmlrpc skill dir symlink; injected verbatim.
        self.assertTrue(COMPANION.exists(), "read-reactions.md companion missing")
        self.assertFalse(self.text.startswith("---"),
                         "companion must have NO YAML frontmatter (injected as-is)")

    def test_header_names_reaction_read(self):
        self._teeth("Reading a client's REACTION")

    def test_reaction_model_recipe(self):
        self._teeth("mail.message.reaction", "search_read", "message_id")

    def test_403_obstacle_documented(self):
        self._teeth("403", "base.group_user", "handover")

    def test_fresh_prod_copy_fallback(self):
        self._teeth("REFRESH-DEV-BOX-FROM-PROD", "psql")

    def test_pending_odoo_erp_acl_crossref(self):
        self._teeth("odoo-erp", "company_base", "ACL")

    def test_doctrine_pointer(self):
        self._teeth("statusline-vocabulary", "#745", "plnohodnotná")

    def test_incident_cited(self):
        self._teeth("discuss.channel_288", "1739648")


class TestTriggerRow(TestCase):
    """A content-bound Write|Edit trigger row binds the recipe to a reaction
    read — bound to CONTENT (never Bash / a rules path) because the code lives
    in a DIFFERENT repo (odoo-erp)."""

    def test_row_present_and_well_formed(self):
        rows = load_conf_rows()
        match = [r for r in rows if r[0] == "odoo-discuss-read-reactions"]
        self.assertEqual(len(match), 1,
                         "exactly one odoo-discuss-read-reactions trigger row")
        topic, event, pattern, body = match[0]
        self.assertIn("Write", event)
        self.assertIn("Edit", event)
        self.assertIn("mail", pattern)
        self.assertIn("reaction", pattern)
        self.assertEqual(body, "skills/odoo-discuss-xmlrpc/read-reactions.md")

    def test_topic_is_unique(self):
        topics = [r[0] for r in load_conf_rows()]
        self.assertEqual(topics.count("odoo-discuss-read-reactions"), 1)


class TestFunctionalInjection(TestCase):
    """Drive the REAL hook (#598): a `.py` Write whose content carries
    `mail.message.reaction` (but NOT `message_post`/`attachment_ids`) must
    inject BOTH the new read recipe AND comprehensive-logging."""

    def test_reaction_read_injects_recipe_and_cofits_logging(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_hook(
                {
                    "file_path": "/repo/reactcheck.py",
                    "content": "rx = models.execute_kw(db, uid, key, "
                               "'mail.message.reaction', 'search_read', "
                               "[[['message_id', '=', mid]]], "
                               "{'fields': ['content', 'partner_id']})",
                },
                session_id="cofit-745-real",
                tmpdir=td,
            )
        self.assertEqual(r.returncode, 0, "injector must never block: %r" % r.stderr)
        self.assertIn(
            "Reading a client's REACTION", r.stdout,
            "the new read-reactions recipe DEFERRED on a .py reaction read",
        )
        self.assertIn(
            "Comprehensive Logging", r.stdout,
            "comprehensive-logging did not inject on the same .py write",
        )

    def test_three_way_cofire_all_inject(self):
        """#745 review finding: a `.py` write touching BOTH `attachment_ids`
        AND `mail.message.reaction` co-fires THREE rows (comprehensive-logging
        + read-attachments + read-reactions). Drive the REAL hook (a FRESH
        session so nothing is pre-marked) and assert ALL THREE inject — the
        read-reactions body must be lean enough that it does NOT defer at the
        exact moment a session reads a client message fully (#521/#598)."""
        with tempfile.TemporaryDirectory() as td:
            r = run_hook(
                {
                    "file_path": "/repo/readfull.py",
                    "content": "a = ex(db,uid,k,'mail.message','search_read',"
                               "[[['id','=',mid]]],{'fields':['attachment_ids']})\n"
                               "rx = ex(db,uid,k,'mail.message.reaction',"
                               "'search_read',[[['message_id','=',mid]]])",
                },
                session_id="cofit-745-threeway",
                tmpdir=td,
            )
        self.assertEqual(r.returncode, 0, "injector must never block: %r" % r.stderr)
        self.assertIn("Comprehensive Logging", r.stdout, "comp deferred on the 3-way")
        self.assertIn("Reading a client Discuss message", r.stdout,
                      "read-attachments deferred on the 3-way")
        self.assertIn("Reading a client's REACTION", r.stdout,
                      "read-reactions DEFERRED on the 3-way — trim it leaner")

    def test_companion_cofits_comprehensive_logging_arithmetic(self):
        # #576/#598 early-warning arithmetic guard (the functional test above
        # is the one with real teeth).
        comp = len(strip_frontmatter(read(COMP_LOGGING)).strip())
        companion = len(strip_frontmatter(read(COMPANION)).strip())
        budget = hook_max_total()
        self.assertLessEqual(
            comp + companion, budget,
            "companion (%d) + comprehensive-logging (%d) exceeds MAX_TOTAL %d"
            % (companion, comp, budget),
        )


if __name__ == "__main__":
    main()
