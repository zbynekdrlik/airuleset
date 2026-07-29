"""#153 findings 3 and 4 — every residual is stated where a reader MEETS it.

The channel's docstrings were written as a list of properties the code
guarantees. What they left out is the shape of every real leak that remains:
a reader who trusts `cmd_secret`'s "it is never printed by any action here, and
there is deliberately no action that could print it" walks away believing the
value is unreachable, when in fact the store was 0600 under the agent's own uid
and one `cat` away for the whole life of the feature.

The playbook is not where this belongs. A residual has to be readable at the
place someone forms their belief about the guarantee — the module docstring,
the command docstring, the job docstring — because that is what is in front of
them when they decide whether to trust it.

Each assertion below names one residual and the docstring that must carry it.
No test here uses a real credential value.
"""
import re
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
import watchdog as wd                                   # noqa: E402
from filedrop import vault as st                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER_DOC = (ROOT / "filedrop" / "vault_server.py").read_text()


def has(text, *needles):
    """True when every needle (case-insensitive regex) appears."""
    low = text.lower()
    return all(re.search(n, low) for n in needles)


class TheStoreIsReadableByTheAgentsOwnUid(unittest.TestCase):
    """The residual this ticket exists for. It must not be discoverable only
    by reading the hook."""

    def test_the_module_docstring_says_the_uid_can_read_it(self):
        doc = st.__doc__
        self.assertTrue(has(doc, r"uid"),
                        "vault.py must say WHO can read the store")
        self.assertTrue(has(doc, r"guardrail|hook"),
                        "vault.py must name the hook that closes the read path")

    def test_the_module_docstring_does_not_imply_a_uid_boundary(self):
        # NOPASSWD sudo is the fact that makes uid separation unachievable
        # here; claiming a boundary without it would be the over-claim #153
        # is correcting.
        self.assertTrue(has(st.__doc__, r"sudo"),
                        "vault.py must say why uid separation is unachievable")

    def test_the_cli_docstring_no_longer_claims_nothing_could_print_it(self):
        doc = airuleset.cmd_secret.__doc__
        self.assertNotRegex(
            doc, r"there is deliberately no action that could print it",
            "the unqualified claim is what the read hook had to be built for")
        self.assertTrue(has(doc, r"hook|guardrail"),
                        "cmd_secret must point at what actually holds the claim")


class TheChildIsUnconstrainedOnDisk(unittest.TestCase):
    def test_redact_docstring_says_it_only_filters_captured_output(self):
        doc = airuleset._secret_redact.__doc__
        self.assertTrue(has(doc, r"writes|write"),
                        "the child's filesystem writes are not covered")

    def test_the_cli_docstring_says_it_too(self):
        # A reader meets `exec` at the command, not at the private helper.
        doc = airuleset.cmd_secret.__doc__
        self.assertTrue(has(doc, r"child"),
                        "cmd_secret must disclose what the child may do")
        self.assertTrue(has(doc, r"git-tracked|config\.ini|anywhere"),
                        "name the shape: the child can write the value to disk")


class TheCapabilityUrlIsInTheTranscriptByDesign(unittest.TestCase):
    def test_the_server_docstring_names_the_substitution_window(self):
        self.assertTrue(
            has(SERVER_DOC, r"transcript"),
            "vault_server.py worries about a local uid reading argv but the "
            "token is PRINTED into the transcript by design")
        self.assertTrue(
            has(SERVER_DOC, r"substitut"),
            "the risk is a substitute credential POSTed before the user's")

    def test_the_server_docstring_says_what_the_nonce_actually_binds(self):
        self.assertTrue(
            has(SERVER_DOC, r"nonce"),
            "the nonce binds the endpoint to the REQUEST, not to the poster")

    def test_the_cli_docstring_warns_that_the_url_is_a_capability(self):
        doc = airuleset.cmd_secret.__doc__
        self.assertTrue(has(doc, r"transcript"),
                        "cmd_secret prints the URL — it must say where it lands")


class ExecBuffersUntilExit(unittest.TestCase):
    def test_the_cli_docstring_says_there_is_no_streaming(self):
        doc = airuleset.cmd_secret.__doc__
        self.assertTrue(has(doc, r"stream|buffer"),
                        "capture_output=True means no output until the child "
                        "exits, and none at all if the CLI is killed")


class TheTtlIsSweptHourly(unittest.TestCase):
    def test_the_job_docstring_states_the_hourly_granularity(self):
        doc = wd.vault_purge_job.__doc__
        self.assertTrue(has(doc, r"hour"),
                        "job 29 already says hourly")
        self.assertTrue(
            has(doc, r"60s|60 s|minimum|shorter"),
            "a keep shorter than the sweep interval outlives its own TTL — "
            "state it where the TTL is described")

    def test_the_store_docstring_does_not_promise_the_ttl_to_the_second(self):
        doc = st.purge.__doc__ or ""
        combined = (st.__doc__ or "") + doc
        self.assertTrue(has(combined, r"hour"),
                        "the store's TTL text must carry the sweep granularity")


class TheStoreDirIsShared(unittest.TestCase):
    def test_the_module_docstring_says_the_dir_pre_existed(self):
        doc = st.__doc__
        self.assertTrue(has(doc, r"shared|pre-exist|already"),
                        "~/.claude/secrets/ already held sub-dev *.env files")

    def test_it_states_the_two_consequences(self):
        doc = st.__doc__ + (st.ensure_dir.__doc__ or "")
        self.assertTrue(has(doc, r"0700|chmod"),
                        "ensure_dir chmods that shared dir on EVERY call")
        self.assertTrue(has(doc, r"symlink"),
                        "assert_safe_store_dir hard-fails the whole channel "
                        "on a box where it is a symlink")


class NoShippedClaimPointsAtNothing(unittest.TestCase):
    """Finding 4 — the docstring said the command-template alternative was
    'filed separately' and nothing had been filed."""

    def test_the_filed_separately_claim_cites_a_real_issue(self):
        doc = airuleset._secret_redact.__doc__
        m = re.search(r"filed as #(\d+)", doc.lower())
        self.assertIsNotNone(
            m, "the claim must cite the issue number, not gesture at one")
        self.assertGreater(int(m.group(1)), 0)

    def test_no_docstring_still_says_filed_separately_unqualified(self):
        for doc in (airuleset._secret_redact.__doc__,
                    airuleset.cmd_secret.__doc__,
                    st.__doc__, SERVER_DOC):
            with self.subTest(doc=(doc or "")[:40]):
                self.assertNotRegex(
                    (doc or "").lower(), r"filed separately",
                    "an unnumbered 'filed separately' is the claim that "
                    "pointed at nothing")


if __name__ == "__main__":
    unittest.main()
