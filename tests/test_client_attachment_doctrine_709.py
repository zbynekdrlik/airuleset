"""#709 — client-message reading must be MULTIMODAL: attachments (Odoo
ir.attachment) read BEFORE the text, always.

Incident (montalu stream, 2026-08-25/26): a stream read a client's Discuss
reply (mail.message 1742799) over the Odoo API WITHOUT fetching
`attachment_ids`, interpreted the request from the bare text alone, and
shipped odoo-erp #5162 with the wrong/incomplete interpretation — the client
had attached a screenshot (ir.attachment 13204) circling the exact UI element
the text alone did not make clear. Root cause (confirmed in the STEP-0
validation + design comment on the ticket): no airuleset surface anywhere
mandated fetching attachment_ids on a client-message read — `view-image-urls`
covered only a pasted URL; `odoo-discuss-xmlrpc` covered only POSTING.

Fix, four small edits inside the EXISTING mechanism (rule-intake gate step 3:
extend the owning module, never invent a parallel one):

  1. `modules/core/view-image-urls.md` (always-on stub, must stay <8 lines
     per test_dynamic_application.py) gets one short generalized paragraph.
  2. `skills/view-image-urls/SKILL.md` gets a matching "system / client-
     message attachments" section.
  3. A NEW companion `skills/odoo-discuss-xmlrpc/read-with-attachments.md`
     carries the concrete Odoo XML-RPC recipe — mirroring the file's
     EXISTING SKILL.md/handover-compose.md split (SKILL.md must stay lean
     enough to co-fit comprehensive-logging on a `.py` message_post write,
     per the #521/#576/#598 co-fit budget lessons — the companion has no
     such co-fit constraint and can carry the full recipe).
  4. A new `Write|Edit` trigger row (`odoo-discuss-read-attachments`) binds
     the companion to a Write/Edit whose content carries `attachment_ids` —
     bound to CONTENT, not a file path, because the code this doctrine must
     reach lives in a DIFFERENT repo (odoo-erp) where a `rules/*.md`
     path-scoped rule could never fire at all.

These are content-lock tests (the #498/#500 per-line `_teeth` pattern — a
finder token unique to the operative line, mutation-verified by hand: revert
the line, the specific test fails) plus functional injection + co-fit-budget
tests driving the REAL hook (the #598 lesson: raw-body arithmetic
under-counts the hook's real wrapped budget, so only a functional drive of
`inject-situational-rule.sh` proves delivery).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "modules" / "core" / "view-image-urls.md"
SKILL_IMG = ROOT / "skills" / "view-image-urls" / "SKILL.md"
SKILL_ODOO = ROOT / "skills" / "odoo-client-messaging" / "SKILL.md"
COMPANION = ROOT / "skills" / "odoo-client-messaging" / "read-with-attachments.md"
COMP_LOGGING = ROOT / "skills" / "comprehensive-logging" / "SKILL.md"
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse whitespace runs to a single space (survives markdown re-wrap
    — internals-tests.md's documented single-line-anchor + normalize
    convention)."""
    return " ".join(text.split())


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


def run_hook(tool_input, tool_name="Write", session_id="sess-709", tmpdir=None):
    payload = json.dumps(
        {"session_id": session_id, "tool_name": tool_name, "tool_input": tool_input}
    )
    env = dict(os.environ)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


def injected(result):
    out = result.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    return data["hookSpecificOutput"]["additionalContext"]


class _Teeth:
    """Per-line content-lock mixin (#498/#500) — a finder token unique to the
    operative physical line, all co-tokens must sit on that SAME line, so a
    partial revert of only that line fails the test."""

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


class TestModuleStubExtended(_Teeth, TestCase):
    """The always-on stub (modules/core/view-image-urls.md) must stay <8
    lines (test_dynamic_application.py's own stub cap) but now carries the
    generalized attachment doctrine as one extra short paragraph."""

    def setUp(self):
        self.text = read(MODULE)

    def test_still_a_stub_under_eight_lines(self):
        self.assertLess(len(self.text.splitlines()), 8)

    def test_attachment_doctrine_present(self):
        self._teeth("SYSTEM attachment", "ir.attachment", "primary source")

    def test_fetch_download_read_before_text(self):
        self._teeth("Fetch `attachment_ids`", "download", "Read EVERY attachment",
                    "BEFORE interpreting the text")

    def test_incident_cited(self):
        self._teeth("mail.message` 1742799", "ir.attachment` 13204", "2026-08-25/26")

    def test_points_at_companion_recipe(self):
        self._teeth("Recipe:", "odoo-discuss-xmlrpc", "read-with-attachments.md")


class TestViewImageUrlsSkillSection(_Teeth, TestCase):
    def setUp(self):
        self.text = read(SKILL_IMG)

    def test_section_header_present(self):
        self.assertIn(
            "#### System / client-message attachments (Odoo ir.attachment, mail attachments)",
            self.text,
        )

    def test_primary_source_equal_to_text(self):
        self._teeth("PRIMARY source, equal to the text", "never optional")

    def test_incident_citation(self):
        self._teeth("mail.message` 1742799", "ir.attachment` 13204", "#709")

    def test_recipe_pointer(self):
        self._teeth("full XML-RPC recipe", "odoo-discuss-xmlrpc", "read-with-attachments.md")

    def test_anti_pattern_present(self):
        self._teeth('"spracoval som správu"', "attachment_ids", "banned")

    def test_applies_to_all_rewordings(self):
        self.assertIn("Applies to all rewordings and semantic equivalents", self.text)

    def test_description_mentions_attachments(self):
        front = self.text.split("---", 2)[1]
        self.assertIn("ir.attachment", front)
        # existing URL/social triggers must survive untouched
        for needle in ("prnt.sc", "imgur", "gyazo", "x.com", "twitter.com",
                       "instagram", "linkedin"):
            self.assertIn(needle, front.lower(), needle)


class TestReadWithAttachmentsCompanion(_Teeth, TestCase):
    """Prose here WRAPS across several indented physical lines (a markdown
    paragraph, not a single unwrapped bullet) — per internals-tests.md's
    documented #500 distinction, a WRAPPED region is content-locked via a
    norm()-collapsed substring check, not the per-line `_teeth` mixin (which
    needs every co-token on ONE raw physical line)."""

    def setUp(self):
        self.assertTrue(COMPANION.exists(), COMPANION)
        self.raw = read(COMPANION)
        self.text = self.raw          # kept for _teeth-based tests below
        self.t = norm(self.raw)

    def test_header(self):
        self.assertIn(
            "# Reading a client Discuss message — attachments FIRST (ir.attachment)",
            self.raw,
        )

    def test_read_side_counterpart_framing(self):
        self.assertIn("READ-side counterpart", self.t)
        self.assertIn("SKILL.md", self.t)
        self.assertIn("posting recipe", self.t)

    def test_fetch_with_attachment_ids(self):
        self.assertIn('"attachment_ids"', self.t)
        self.assertIn("search_read", self.t)
        self.assertIn("never a bare", self.t)

    def test_ir_attachment_read_base64(self):
        self._teeth("ir.attachment.read", "base64", "datas")

    def test_read_before_interpreting(self):
        self.assertIn("Read it BEFORE interpreting the text", self.t)
        self.assertIn("Read tool", self.t)
        self.assertIn("no-browser-needed path", self.t)

    def test_incident_numbers(self):
        t = self.raw
        self.assertIn("1742799", t)
        self.assertIn("13204", t)
        self.assertIn("odoo-erp #5162", t)
        self.assertIn("odoo-erp #5214", t)
        self.assertIn("airuleset #709", t)
        self.assertIn("2026-08-25/26", t)

    def test_anti_pattern_all_rewordings(self):
        self.assertIn('"Spracoval som správu"', self.t)
        self.assertIn("attachment_ids", self.t)
        self.assertIn("banned", self.t)
        self.assertIn("any system attachment channel", self.t)

    def test_generalizes_view_image_urls_doctrine(self):
        self.assertIn("view-image-urls", self.raw)


class TestSkillStaysLeanAndPointsAtCompanion(_Teeth, TestCase):
    """SKILL.md keeps only a SHORT pointer (the co-fit budget lesson,
    #521/#576/#598) — the recipe body must NOT be restated there."""

    def setUp(self):
        self.raw = read(SKILL_ODOO)
        self.t = norm(self.raw)

    def test_pointer_present(self):
        # #891: SKILL.md is now a channel-agnostic pointer — the companion
        # read-with-attachments.md exists alongside it in the same dir
        companion = SKILL_ODOO.parent / "read-with-attachments.md"
        self.assertTrue(companion.exists(),
                        "read-with-attachments.md must exist alongside SKILL.md")

    def test_recipe_body_not_restated(self):
        # the operative recipe code/phrases must live ONLY in the companion
        for needle in ("ir.attachment.read", "base64.b64decode", "search_read",
                       '"attachment_ids"'):
            self.assertNotIn(needle, self.raw)


class TestTriggerRow(TestCase):
    def setUp(self):
        self.rows = load_conf_rows()
        self.by_topic = {r[0]: r for r in self.rows}

    def test_row_present(self):
        self.assertIn("odoo-discuss-read-attachments", self.by_topic)

    def test_row_shape(self):
        topic, tool, pattern, body = self.by_topic["odoo-discuss-read-attachments"]
        self.assertEqual(tool, "Write|Edit")
        self.assertIn("attachment_ids", pattern)
        self.assertEqual(body, "skills/odoo-client-messaging/read-with-attachments.md")

    def test_body_file_exists(self):
        _, _, _, body = self.by_topic["odoo-discuss-read-attachments"]
        self.assertTrue((ROOT / body).exists())

    def test_topics_still_unique(self):
        topics = [r[0] for r in self.rows]
        self.assertEqual(len(topics), len(set(topics)), "duplicate topic in table")

    def test_existing_message_post_row_untouched(self):
        topic, tool, pattern, body = self.by_topic["odoo-discuss-xmlrpc"]
        self.assertEqual(tool, "Write|Edit")
        self.assertEqual(pattern, "message_post")
        self.assertEqual(body, "skills/odoo-client-messaging/SKILL.md")


class TestCoFitBudget(TestCase):
    """#521/#576/#598 co-fit lesson: comprehensive-logging + the new
    companion co-fire on a `.py` Write carrying `attachment_ids` — both the
    raw-arithmetic guard AND the real, wrapper-inclusive hook drive."""

    def test_raw_arithmetic_cofit(self):
        companion = len(strip_frontmatter(read(COMPANION)).strip())
        comp = len(strip_frontmatter(read(COMP_LOGGING)).strip())
        budget = hook_max_total()
        self.assertLessEqual(
            companion + comp, budget,
            "companion (%d) + comprehensive-logging (%d) = %d exceeds MAX_TOTAL %d"
            % (companion, comp, companion + comp, budget),
        )

    def test_skill_md_pointer_still_cofits_with_comprehensive_logging(self):
        # #598: the raw-arithmetic guard under-counts the real wrapped
        # budget, so this is an early-warning arithmetic check ONLY — the
        # functional test below is the one with real teeth.
        skill = len(strip_frontmatter(read(SKILL_ODOO)).strip())
        comp = len(strip_frontmatter(read(COMP_LOGGING)).strip())
        budget = hook_max_total()
        self.assertLessEqual(skill + comp, budget)

    def test_both_bodies_inject_on_an_attachment_ids_write(self):
        """Drive the REAL hook (the #598-taught functional check): a `.py`
        Write whose content carries `attachment_ids` (but NOT `message_post`)
        must inject BOTH comprehensive-logging AND the new read recipe."""
        with tempfile.TemporaryDirectory() as td:
            r = run_hook(
                {
                    "file_path": "/repo/reader.py",
                    "content": "atts = models.execute_kw(db, uid, key, "
                               "'ir.attachment', 'read', [msg['attachment_ids']])",
                },
                session_id="cofit-709-real",
                tmpdir=td,
            )
        self.assertEqual(r.returncode, 0, "injector must never block: %r" % r.stderr)
        self.assertIn(
            "Reading a client Discuss message", r.stdout,
            "the new read-with-attachments recipe DEFERRED on a .py write",
        )
        self.assertIn(
            "Comprehensive Logging", r.stdout,
            "comprehensive-logging did not inject on the same .py write",
        )

    def test_message_post_row_still_injects_alone(self):
        """A `.py` Write carrying `message_post` but NOT `attachment_ids`
        must still inject the (unchanged) posting recipe — the new pointer
        line did not push it over budget with comprehensive-logging."""
        with tempfile.TemporaryDirectory() as td:
            r = run_hook(
                {
                    "file_path": "/repo/importer.py",
                    "content": "channel.message_post(body=h, body_is_html=True)",
                },
                session_id="cofit-709-postonly",
                tmpdir=td,
            )
        self.assertEqual(r.returncode, 0, "injector must never block: %r" % r.stderr)
        self.assertIn("Odoo Client Messaging", r.stdout,
                      "the odoo message_post recipe DEFERRED — the pointer SKILL.md didn't inject")
        self.assertIn("Comprehensive Logging", r.stdout)


if __name__ == "__main__":
    main()
