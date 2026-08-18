"""view-image-urls skill (#541).

After #415 flipped Playwright to OPTIONAL_PLUGINS (installed but default-DISABLED
per project), the owner's cross-project "just works" habit of reading pasted
prnt.sc/Lightshot screenshots broke: the skill still said "ALWAYS render in
Playwright", claimed "the CDN blocks non-browser referers and returns a
placeholder", and told the session to "ask for the TOOL" / that Playwright "is
not installed". #541 proved LIVE (https://prnt.sc/oWhJ7vO62WGg) that the
screenshot reads with NO browser at all — curl the wrapper (browser UA) → extract
og:image → curl the CDN (UA + Referer) → Read the local .png — and rewrote the
skill so that download-and-Read is the FIRST path, browser only for a hostile
JS-gated CDN or a genuinely JS-walled social page, and the "not installed"
phrasing is banned as factually false.

These are content-lock teeth (#498/#500): the coarse whole-file assertIn catches a
FULL deletion; the per-line `_teeth` catches a PARTIAL (operative-line-only)
revert — the finder token is UNIQUE to the operative line, never a nearby why-prose
line, and each has been mutation-verified by hand (revert the one line → the
specific test fails).
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "view-image-urls" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


def _description(path):
    """The `description:` line from the SKILL.md YAML frontmatter — the text
    that makes a session LOAD this skill. One physical line in the frontmatter."""
    for line in read(path).splitlines():
        if line.startswith("description:"):
            return line
    return ""


class _Teeth:
    """Per-line content-lock mixin (#500 single-physical-line shape).

    `_teeth(finder, *cotokens)` asserts SOME single physical line of the skill
    contains the (finder-unique) token AND every co-token — so a partial revert
    of that one operative line (which drops finder+co-tokens together) fails the
    test, where a coarse whole-file assertIn would still pass on the co-tokens
    surviving elsewhere.
    """

    text = ""  # set by subclass setUp

    def _teeth(self, finder, *cotokens):
        hits = [ln for ln in self.text.splitlines() if finder in ln]
        self.assertTrue(
            hits, "finder %r not on any single line of the skill" % finder)
        for ln in hits:
            if all(tok in ln for tok in cotokens):
                return ln
        self.fail(
            "no single line carries finder %r together with all of %r; "
            "lines matching the finder: %r" % (finder, cotokens, hits))


class TestSkillRegisteredAndDeployedEverywhere(TestCase):
    def test_in_skill_names(self):
        self.assertIn("view-image-urls", airuleset.SKILL_NAMES)

    def test_skill_md_exists(self):
        self.assertTrue(SKILL.exists(), SKILL)

    def test_hidden_from_slash_picker(self):
        # a model-loaded knowledge skill (#447) — never a user-typed slash command.
        self.assertIn("user-invocable: false", read(SKILL))

    def test_deploys_to_every_box(self):
        self.assertNotIn("view-image-urls", airuleset.SKILLS_MAINTAINER_ONLY)
        self.assertNotIn("view-image-urls", airuleset.SKILLS_FULL_AUTHORITY_ONLY)
        for u in ("newlevel", "gatekeeper", "montalu", "david", "marek"):
            self.assertIn("view-image-urls",
                          airuleset.skill_names_for_user(u), u)


class TestDescriptionTriggersAndNoBrowserFraming(TestCase):
    """The description is what makes a session LOAD the skill — it must still
    trigger on the image/screenshot + social hosts AND now advertise the
    no-browser download-and-Read first move."""

    def test_description_keeps_triggers(self):
        desc = _description(SKILL)
        for needle in ("prnt.sc", "imgur", "gyazo", "x.com", "twitter.com",
                       "instagram", "linkedin"):
            self.assertIn(needle, desc.lower(), needle)

    def test_description_advertises_no_browser_first(self):
        desc = _description(SKILL)
        self.assertIn("download it locally with curl", desc)
        self.assertIn("Read tool", desc)

    def test_description_bans_not_installed_claim(self):
        # the #415 honesty requirement reaches the LOAD-trigger line itself.
        self.assertIn('NEVER claim Playwright "is not installed"', _description(SKILL))


class TestNoBrowserPathIsFirst(_Teeth, TestCase):
    """The load-bearing #541 change: download-locally + Read is the FIRST path
    for images, with the exact live-proven recipe + placeholder detection."""

    def setUp(self):
        self.text = read(SKILL)

    def test_no_browser_section_precedes_browser_and_social(self):
        first = self.text.find("FIRST path — images")
        browser = self.text.find("browser fallback")
        social = self.text.find("JS-walled pages, genuinely need a browser")
        self.assertGreater(first, -1, "no-browser FIRST path section missing")
        self.assertGreater(browser, first, "browser fallback must come AFTER the no-browser path")
        self.assertGreater(social, browser, "social section must come AFTER the browser fallback")

    def test_read_renders_local_pixels(self):
        # the core insight: pixels of a LOCAL file need no browser.
        self._teeth("Read tool renders the local image pixels", "SEE the screenshot")

    def test_wrapper_fetch_needs_browser_ua(self):
        # the empirical fact: bare curl on the wrapper → HTTP 520.
        self._teeth("wrapper needs a browser UA", "curl", "520")

    def test_og_image_extracted_from_static_meta(self):
        self._teeth("og:image[^>]*content", "grep", "wrapper.html")

    def test_cdn_download_uses_ua_and_referer(self):
        # the live-proven header set: UA + Referer=<wrapper> into a local file.
        self._teeth('-e "$WRAPPER_URL"', "curl", '-A "$UA"', "-o")

    def test_verify_it_is_a_real_image(self):
        self._teeth("Verify it is a real image", "file", "image data")

    def test_placeholder_detection_no_og_image_is_removed(self):
        self._teeth("the screenshot is gone", "og:image", "removed / expired")

    def test_live_proof_recorded(self):
        # the concrete evidence the recipe works, so a future edit can't quietly
        # revert to "ALWAYS Playwright" without dropping the proof.
        t = self.text
        self.assertIn("img.lightshot.app", t)
        self.assertIn("#541", t)


class Test415AwareMessaging(_Teeth, TestCase):
    """On a managed box Playwright is INSTALLED but default-disabled (#415);
    the 'not installed' phrasing is BANNED as factually false, and 'I can't
    read this' stays banned."""

    def setUp(self):
        self.text = read(SKILL)

    def test_states_installed_but_default_disabled(self):
        self._teeth("Playwright is INSTALLED", "DEFAULT-DISABLED", "#415")

    def test_bans_not_installed_phrasing(self):
        self._teeth('NEVER say "Playwright is not installed"',
                    "nie je nainštalovaný", "BANNED")

    def test_never_i_cant_read_this(self):
        self.assertIn('NEVER answer "I can\'t read this"', self.text)

    def test_false_cdn_blocks_claim_removed(self):
        # the disproven claim (curl reached the CDN live) must not return —
        # this exact phrase is absent from the rewrite, so assertNotIn has teeth
        # against re-adding it (it is never a token the correct skill contains).
        self.assertNotIn("CDN blocks non-browser referers", self.text)
        self.assertNotIn("Playwright is not installed, that is a missing-tool", self.text)


class TestBrowserFallbackNamed(_Teeth, TestCase):
    """The two sanctioned browser fallbacks for a genuinely hostile CDN:
    per-project one-line opt-in + a bundled-Chromium agent."""

    def setUp(self):
        self.text = read(SKILL)

    def test_per_project_one_line_opt_in(self):
        self._teeth("enabledPlugins", "playwright@claude-plugins-official", "true")

    def test_bundled_chromium_agent_is_the_sanctioned_fallback(self):
        self._teeth("bundled Chromium", "general-purpose agent", "sanctioned")


class TestSocialSectionIntact(TestCase):
    """The JS-walled social section genuinely needs a browser — it must stay."""

    def setUp(self):
        self.text = read(SKILL)

    def test_social_platforms_and_snapshot_present(self):
        for needle in ("x.com", "instagram.com", "linkedin.com",
                       "browser_snapshot", "browser_navigate"):
            self.assertIn(needle, self.text, needle)

    def test_login_wall_retry_gotcha_kept(self):
        self.assertIn("Log in to see more", self.text)
        self.assertIn("nitter", self.text)


if __name__ == "__main__":
    main()
