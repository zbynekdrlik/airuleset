"""#578 part 2 — gk core-set COUNTING AUDIT.

The owner's `gk I 16` complaint suspected a foreign `needs-acceptance` (the
odoo-erp #4007 shape: `needs-acceptance` + `stream:montalu3`, NO
`ready-for-review`/`needs-gatekeeper`) being wrongly counted in the
full-authority `I`. The audit (STEP-0 probe against the live code) found the
count is CORRECT — but the correctness rests ENTIRELY on ONE mechanical guard
(`_core_search_excl()` excluding every reduced-authority stream at the SEARCH
layer), with NO defense-in-depth at the partition layer (`_partition_workable`'s
#539 chained-I branch would route a bare foreign `needs-acceptance` to `I` if it
ever leaked past the search exclusion, because it cannot see the box's
authority). So this file LOCKS the exclusion + DOCUMENTS the deliberate
`stream:core` inclusion, mutation-verified, so a future regress of either can
never ship silently.

Locks:
  1. `_obligation_quals()` core-slice fragment EXCLUDES every non-full
     AUTHORITY_BY_USER stream (montalu3 incl.) — a foreign bare needs-acceptance
     never enters the core-slice query. (mutation: drop a stream -> RED)
  2. `_obligation_quals()` core-slice does NOT exclude `stream:core` — the
     full-authority box's own umbrella tickets stay counted (deliberate).
  3. the deliberate-`stream:core` decision is documented in the code (a #498
     content-lock so the reasoning can't be silently dropped).
  4. `_partition_workable` DOES route a bare needs-acceptance with no delivered
     draft to `workable` (I) — the #539 chained-I behaviour that makes the
     search exclusion load-bearing for a full-authority box.
  5-6. END-TO-END (a filter-aware fake gh + a readable-empty question map, the
     production gk chained-I route): the #4007-shaped foreign acceptance is
     ABSENT from `core-quals` `--list`/`--waiting`/`--ops-wait`/`--count`, while
     the `stream:core` umbrella (#4520 shape) IS counted.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


def _labels(*names):
    return [{"name": n} for n in names]


class ObligationExclusionLock(unittest.TestCase):
    """Pure — the SEARCH-layer guarantee that a foreign stream ticket (incl. a
    bare needs-acceptance) never enters the core obligation query."""

    def _core_slice_fragment(self):
        quals = airuleset._obligation_quals()
        # element 0 is `_core_search_excl()` (the core slice); 1/2 are the
        # maintainer-action label queries (`_obligation_quals` contract).
        return quals[0]

    def test_core_slice_excludes_every_reduced_authority_stream(self):
        frag = self._core_slice_fragment()
        missing = []
        for user, profile in airuleset.AUTHORITY_BY_USER.items():
            if profile == "full":
                continue
            for name in airuleset._stream_rename_equivalents(user):
                if ("-label:stream:%s" % name) not in frag:
                    missing.append(name)
        self.assertEqual(
            missing, [],
            "the core-slice obligation query must exclude EVERY reduced-authority "
            "stream (a foreign `needs-acceptance` without rfr/needs-gk enters the "
            "gk obligation set ONLY if its stream is not excluded here — #4007). "
            "Missing exclusions: %r" % missing)
        # #4007's own stream, explicitly.
        self.assertIn("-label:stream:montalu3", frag)

    def test_core_slice_does_not_exclude_stream_core(self):
        # `core` is not a reduced-authority AUTHORITY_BY_USER stream, so a
        # `stream:core` ticket (the full-authority box's OWN umbrella work) is
        # DELIBERATELY not filtered out — it stays counted in `I` (#4520).
        self.assertNotIn("-label:stream:core", self._core_slice_fragment())
        self.assertNotIn("core", airuleset.AUTHORITY_BY_USER,
                         "if `core` ever becomes a reduced-authority stream this "
                         "test AND the #578 doc comment must be revisited")


class StreamCoreDocumentedLock(unittest.TestCase):
    """#498-style content-lock: the deliberate `stream:core`-inclusion reasoning
    is present in the code, so a future edit cannot silently drop it and leave a
    maintainer guessing whether `stream:core` counting is a bug."""

    def test_stream_core_deliberate_inclusion_is_documented(self):
        src = (Path(__file__).resolve().parent.parent / "cli_quals.py").read_text()
        # Anchor on tokens UNIQUE to the #578 deliberate-inclusion decision — not
        # a nearby token that a partial revert could leave behind. assertTrue(x in
        # src), never assertIn — so a miss does not dump the whole file (#419).
        for needle in ("#578 counting audit",
                       "stream:core` is DELIBERATELY NOT excluded",
                       "full-authority box's OWN work marker"):
            self.assertTrue(needle in src,
                            "missing #578 stream:core doc anchor: %r" % needle)


class ChainedIForeignAcceptanceRoute(unittest.TestCase):
    """Pure partition — the #539 chained-I branch that makes the search exclusion
    load-bearing. A bare needs-acceptance with a readable-but-EMPTY
    `acceptance_present` (no delivered draft — the production gk state for a
    foreign ticket) routes to `workable` (I). This is EXISTING #539 behaviour;
    locking it here documents WHY a leaked foreign acceptance would inflate `I`."""

    def test_bare_needs_acceptance_no_draft_routes_to_workable_I(self):
        rows = {4007: {"number": 4007,
                       "labels": _labels("needs-acceptance", "stream:montalu3")}}
        # acceptance_present is a readable-empty set (map readable, #4007 not in
        # it) -> the #539 chained-I branch -> workable, NOT user-waiting.
        w, u, ow = airuleset._partition_workable(rows, acceptance_present=set())
        self.assertEqual(set(w), {4007},
                         "a bare needs-acceptance with no delivered draft is #539 "
                         "chained-I -> workable; so a FOREIGN one leaking past the "
                         "search exclusion would inflate a full-authority `I`")
        self.assertEqual(u, {})
        self.assertEqual(ow, {})


# --- END-TO-END: filter-aware fake gh over the real derivation ---------------

_FIXTURE = [
    {"number": 1, "title": "plain workable", "createdAt": "2026-08-01T00:00:00Z",
     "labels": _labels("bug")},
    {"number": 4007, "title": "foreign acceptance",
     "createdAt": "2026-08-02T00:00:00Z",
     "labels": _labels("needs-acceptance", "stream:montalu3")},
    {"number": 4459, "title": "release-tail core (no labels)",
     "createdAt": "2026-08-03T00:00:00Z", "labels": []},
    {"number": 4497, "title": "gk review lane",
     "createdAt": "2026-08-04T00:00:00Z",
     "labels": _labels("ready-for-review", "stream:montalu5")},
    {"number": 4520, "title": "core umbrella",
     "createdAt": "2026-08-05T00:00:00Z", "labels": _labels("stream:core")},
    {"number": 4535, "title": "correctly ops-wait",
     "createdAt": "2026-08-06T00:00:00Z", "labels": _labels("ops-wait")},
]

# A fake `gh` that MODELS gh's server-side `--search` label filtering: a
# `-label:X` token EXCLUDES rows carrying X, a `label:X` token REQUIRES it. This
# is what makes the end-to-end faithful — a dumb echo fake (returning all rows
# regardless of the query) would defeat the very search exclusion under test.
_FAKE_GH = r'''#!/usr/bin/env python3
import json, sys
FIXTURE = %s
args = sys.argv[1:]
if "repo" in args and "view" in args:
    print("zbynekdrlik/demo"); sys.exit(0)
if "issue" in args and "list" in args and "--search" in args:
    search = args[args.index("--search") + 1]
    exclude, require = set(), set()
    for tok in search.split():
        if tok.startswith("-label:"):
            exclude.add(tok[len("-label:"):])
        elif tok.startswith("label:"):
            require.add(tok[len("label:"):])
    out = []
    for row in FIXTURE:
        names = {l["name"] for l in row.get("labels") or []}
        if names & exclude:
            continue
        if require and not (require <= names):
            continue
        out.append(row)
    print(json.dumps(out)); sys.exit(0)
# any other gh call (defensive): empty JSON list
print("[]")
'''


class ForeignAcceptanceCountingEndToEnd(unittest.TestCase):
    def _prep(self, home, repo, bindir):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        gh = Path(bindir) / "gh"
        gh.write_text(_FAKE_GH % json.dumps(_FIXTURE))
        gh.chmod(0o755)
        # A READABLE-but-empty question map -> _acceptance_present_set returns an
        # empty set (map readable, no refs) so a bare needs-acceptance routes to
        # chained-I (the production gk box state), NOT the unreadable-map -> U
        # fallback. This is what makes a leaked #4007 land in --list (mutation RED).
        claude = Path(home) / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "discord-questions.json").write_text("{}")

    def _run(self, flag, repo, home, bindir):
        return subprocess.run(
            [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
             "core-quals", flag],
            capture_output=True, text=True, cwd=repo,
            env={**os.environ, "HOME": home,
                 "PATH": f"{bindir}:{os.environ['PATH']}"})

    def test_foreign_acceptance_absent_from_the_whole_obligation_set(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            # workable count = {1, 4459, 4520, 4497} = 4 (4007 excluded, 4535 -> W)
            rc = self._run("--count", repo, home, bindir)
            self.assertEqual(rc.returncode, 0, rc.stderr)
            self.assertEqual(rc.stdout.strip(), "4", rc.stderr)
            # 4007 absent from EVERY surface (I / U / W), not just the count.
            for flag in ("--list", "--waiting", "--ops-wait"):
                r = self._run(flag, repo, home, bindir)
                self.assertEqual(r.returncode, 0, r.stderr)
                nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines()
                        if ln.strip()}
                self.assertNotIn(
                    "4007", nums,
                    "a foreign `needs-acceptance` (stream:montalu3, no rfr/needs-gk) "
                    "must never appear in the gk obligation set's %s" % flag)

    def test_stream_core_umbrella_is_counted(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = self._run("--list", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines()
                    if ln.strip()}
            self.assertIn("4520", nums,
                          "a `stream:core` umbrella ticket is the full-authority "
                          "box's own work -> deliberately counted in I (#4520)")


if __name__ == "__main__":
    unittest.main()
