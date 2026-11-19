"""tests/test_handoff_body_file_stamp_1344.py -- #1344: the --body-file
pass-through hand-off must STAMP `Verified-at-UTC:` at post time.

The compose path stamps it (cmd_handoff), but `_cmd_handoff_post_body_file`
posted the stream's body verbatim, so a body without the line (the stream
trusting the documented "the CLI stamps Verified-at-UTC + HEAD") or with a
copied-forward stale one reached the odoo-erp RFR gate, which FAILs on a
missing / malformed / inconsistent `Verified-at-UTC:` (REQUIRED_FIELDS,
#5869, #6377 batch 3). Checked against the vendored pure gate validators.
"""
import contextlib
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mk

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tests"))

import airuleset  # noqa: E402
sys.path.insert(0, os.path.join(_REPO, "tests", "fixtures"))
import handoff_gate_pure as gate  # noqa: E402

HEAD = "0123456789abcdef0123456789abcdef01234567"
SLUG = "owner/repo"
BRANCH = "lane-x"
STALE = "2020-01-01T00:00:00Z"
FENCE = "```\n$ pytest -q\n3 passed  ran 2026-01-01T00:00Z\n```\n"
FENCED_VA = "```\nVerified-at-UTC: 2019-05-05T05:05:05Z\n```\n"


def _body(extra=""):
    return ("READY-FOR-REVIEW: %s\n- Branch: %s\n- HEAD: %s\n- Stack: odoo\n"
            "%s- Harness: unit\nSelf-review-model: claude-opus-5-5\n\n%s"
            % (BRANCH, BRANCH, HEAD[:12], extra, FENCE))


class _Proc:
    def __init__(self):
        self.posted = []

    def __call__(self, argv, *a, **k):
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(
                argv, 0, "%s\trefs/heads/%s\n" % (HEAD, BRANCH), "")
        if argv[:3] == ["gh", "issue", "comment"]:
            with open(argv[argv.index("--body-file") + 1]) as f:
                self.posted.append(f.read())
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


class TestBodyFileStampsVerifiedAt(unittest.TestCase):
    def _post(self, body):
        proc = _Proc()
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "body.md")
            with open(src, "w") as f:
                f.write(body)
            home = os.path.join(td, "home")
            os.makedirs(home)
            none = mk.MagicMock(return_value=None)
            with contextlib.ExitStack() as st:
                st.enter_context(mk.patch.dict(os.environ, {"HOME": home}))
                for name in ("_handoff_gk_preflight", "_handoff_guide_preflight",
                             "_handoff_agent_eval_preflight",
                             "_handoff_spec_preflight",
                             "_handoff_prod_transfer_preflight",
                             "gk_watch_issue"):
                    st.enter_context(mk.patch.object(airuleset, name, none))
                st.enter_context(mk.patch.object(
                    airuleset, "_handoff_epic_preflight",
                    return_value=(None, {})))
                st.enter_context(mk.patch.object(
                    airuleset, "_handoff_clear_bounce", return_value=False))
                st.enter_context(mk.patch.object(
                    airuleset, "_stream_self_login", return_value="me"))
                st.enter_context(mk.patch.object(
                    airuleset, "_bounce_round", return_value=1))
                st.enter_context(mk.patch("subprocess.run", proc))
                out = st.enter_context(
                    contextlib.redirect_stdout(io.StringIO()))
                rc = airuleset._cmd_handoff_post_body_file(SLUG, 7, BRANCH, src)
                with open(src) as f:
                    source_after = f.read()
                gdir = os.path.join(home, airuleset.HANDOFF_GATE_DIR)
                receipts = [json.load(open(os.path.join(gdir, n)))
                            for n in os.listdir(gdir) if n.endswith(".json")]
        return rc, out.getvalue(), proc.posted, receipts, source_after

    def _assert_fresh(self, posted):
        fields = gate.parse_readiness_fields(posted)
        self.assertEqual(gate.missing_fields(fields), [], posted)
        val = fields["verified_at_utc"]
        self.assertTrue(gate.VERIFIED_AT_UTC_TIMESTAMP_RE.match(val), val)
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(val.replace("Z", "+00:00")))
        self.assertLess(abs(age.total_seconds()), 120, val)
        return val

    def test_missing_line_is_stamped_and_gate_clean(self):
        rc, out, posted, receipts, src = self._post(_body())
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(posted), 1)
        self._assert_fresh(posted[0])
        self.assertNotIn("Verified-at-UTC: 20", src.split("```")[0],
                         "the stream's own file must stay unmodified")

    def test_stale_line_is_restamped_fresh(self):
        rc, out, posted, _, _ = self._post(_body("- Verified-at-UTC: %s\n" % STALE))
        self.assertEqual(rc, 0, out)
        self.assertNotIn(STALE, posted[0])
        self._assert_fresh(posted[0])

    def test_bold_label_shape_is_restamped(self):
        rc, out, posted, _, _ = self._post(
            _body("**Verified-at-UTC:** %s\n" % STALE))
        self.assertEqual(rc, 0, out)
        self.assertNotIn(STALE, posted[0])
        self._assert_fresh(posted[0])

    def test_fence_and_head_pass_through_untouched(self):
        rc, out, posted, _, _ = self._post(_body("- Verified-at-UTC: %s\n" % STALE))
        self.assertEqual(rc, 0, out)
        self.assertIn(FENCE, posted[0])
        self.assertEqual(posted[0].count("HEAD: %s" % HEAD[:12]), 1)

    def test_receipt_hash_matches_the_posted_body(self):
        rc, out, posted, receipts, _ = self._post(_body())
        self.assertEqual(rc, 0, out)
        self.assertEqual(receipts[0]["sha256"],
                         hashlib.sha256(posted[0].encode()).hexdigest())

    def test_value_on_continuation_line_is_refused(self):
        rc, out, posted, receipts, _ = self._post(
            _body("- Verified-at-UTC:\n  - %s\n" % STALE))
        self.assertEqual(rc, 1, out)
        self.assertIn("Verified-at-UTC", out)
        self.assertEqual((posted, receipts), ([], []))

    def test_fenced_field_line_is_refused(self):
        # The gate reads field labels inside fences too (first match wins,
        # B3 INCONSISTENT on two values), so a fenced copy cannot be left
        # stale and must not be rewritten: refuse loudly, post nothing.
        rc, out, posted, receipts, _ = self._post(_body() + FENCED_VA)
        self.assertEqual(rc, 1, out)
        self.assertIn("Verified-at-UTC", out)
        self.assertEqual((posted, receipts), ([], []))


if __name__ == "__main__":
    unittest.main()
