"""#1270: go-live gates the handover document, not only the account.

wall-bridge and songplayer passed `accounts verify` 12/12 while their new
sessions had no status, no test procedure and no site facts (findings on the
ticket). An account that declares `handover` FAILs until that file has every
required section with real content. The check proves shape; the supervisor
reads the text."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_verify as verify  # noqa: E402

DOC = "docs/HANDOVER.md"


def _doc(skip=(), thin=()):
    out = ["# Handover\n"]
    for title in verify.HANDOVER_SECTIONS:
        if title in skip:
            continue
        body = "TODO" if title in thin else "Real facts about this part of the project."
        out.append("## %s\n\n%s\n" % (title, body))
    return "\n".join(out)


def _probe(doc):
    with tempfile.TemporaryDirectory() as home:
        spec = {"project_dir": "devel/p", "handover": DOC, "repo": "o/p", "host": "dev1"}
        if doc is not None:
            f = Path(home, "devel/p", DOC)
            f.parent.mkdir(parents=True)
            f.write_text(doc, encoding="utf-8")
        env = dict(os.environ, HOME=home)
        r = subprocess.run(["bash", "-c", verify.render_probe("p", spec)], env=env,
                           capture_output=True, text=True, timeout=60)
        return spec, verify.parse_probe(r.stdout)


class TestHandoverCheck(unittest.TestCase):

    def test_not_declared_passes(self):
        ok, detail = verify._check_handover({}, {})
        self.assertTrue(ok)
        self.assertIn("not declared", detail)

    def test_complete_document_passes(self):
        spec, s = _probe(_doc())
        ok, detail = verify._check_handover(spec, s)
        self.assertTrue(ok, detail)
        self.assertIn(DOC, detail)

    def test_missing_file_fails(self):
        spec, s = _probe(None)
        ok, detail = verify._check_handover(spec, s)
        self.assertFalse(ok)
        self.assertIn(DOC, detail)

    def test_missing_section_fails_and_names_it(self):
        spec, s = _probe(_doc(skip=("How to run a test",)))
        ok, detail = verify._check_handover(spec, s)
        self.assertFalse(ok)
        self.assertIn("How to run a test", detail)

    def test_placeholder_section_fails(self):
        spec, s = _probe(_doc(thin=("Runners",)))
        ok, detail = verify._check_handover(spec, s)
        self.assertFalse(ok)
        self.assertIn("Runners", detail)

    def test_check_is_part_of_the_gate(self):
        self.assertIn("handover", verify.CHECKS)

    def test_the_key_is_a_valid_declaration(self):
        spec = dict(bootstrap.account_spec("wall-bridge"), handover=DOC)
        self.assertEqual(bootstrap.validate_account("wall-bridge", spec), [])
        spec["handover"] = "../x.md"
        self.assertTrue(bootstrap.validate_account("wall-bridge", spec))


if __name__ == "__main__":
    unittest.main()
