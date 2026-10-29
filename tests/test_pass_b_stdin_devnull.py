"""Pass B must not hand push's stdin to the test suite (ref issue 1137).

`hooks/session-start-fetch.sh` reads its payload with `$(cat)`. A test that runs
it without `input=` inherits the runner's stdin; under `unittest discover` (Pass
B) that is push's own stdin, and an open never-closing pipe there blocked every
TestSessionStartFetchHook run for its full 30 s timeout. Reproduced:
`sleep 60 | python3 -m unittest ...test_up_to_date_no_op` -> 30.07 s ERROR,
`< /dev/null` -> OK.
"""
import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _discover_calls():
    tree = ast.parse((REPO / "cli_remote.py").read_text())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "run"):
            continue
        argv = node.args[0]
        if isinstance(argv, ast.List) and any(
                isinstance(e, ast.Constant) and e.value == "discover" for e in argv.elts):
            calls.append(node)
    return calls


class PassBStdin(unittest.TestCase):
    def test_the_suite_call_exists(self):
        self.assertEqual(len(_discover_calls()), 1)

    def test_suite_stdin_is_devnull(self):
        call = _discover_calls()[0]
        kw = {k.arg: k.value for k in call.keywords}
        self.assertIn("stdin", kw, "Pass B inherits push's stdin")
        self.assertEqual(ast.unparse(kw["stdin"]), "subprocess.DEVNULL")


if __name__ == "__main__":
    unittest.main()
