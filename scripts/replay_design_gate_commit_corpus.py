#!/usr/bin/env python3
"""Corpus replay for hooks/block-commit-without-design.sh (#136, verification
step 2) -- proves the gate discriminates on REAL historical commit messages
from this repo, not just hand-written fixtures, and that the ONLY behavioural
change it introduces is blocking an issue-referencing worker commit with no
delivered design marker.

Two arms, same corpus, same real shipped hook (`hooks/block-commit-without-
design.sh`), invoked exactly the way Claude Code invokes a PreToolUse hook
(JSON on stdin, `.tool_input.command`):

  ON  arm: agent_type=autopilot-worker, scratch $HOME with NO markers ever
           written -- this is the gate's real operating mode.
  OFF arm: agent_type=None (an ordinary human/main-session commit) -- the
           hook's own documented scope check makes this an always-exit-0
           "gate disabled" control, with zero code path shared beyond the
           very first `[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0`.

BIDIRECTIONAL verdict per commit:
  - no issue reference in the message (per design_gate.issue_refs, the SAME
    function the hook uses)             -> BOTH arms must be 0 (AGREE).
  - an issue reference IS present        -> ON blocks (2), OFF passes (0)
                                             (the gate's intended DELTA).
A mismatch outside that pattern (ON passes an issue-referencing commit, or
OFF ever blocks anything, or the two arms disagree on a no-reference commit)
is a genuine hook bug and is reported, never hidden.

Usage: python3 scripts/replay_design_gate_commit_corpus.py [--limit N]
Read-only: only ever calls `git log` locally and the hook itself. #206 added
a `gh issue view` state check inside the hook for still-unmarked refs -- a
STUBBED `gh` (see _stub_gh_bindir below) is prepended to PATH for every
run_hook() call so this stays true: no real gh, no network, no real
~/.claude touched (HOME is scratch-relocated per call as before). The stub
always answers OPEN, which reproduces this script's pre-#206 invariant
exactly (the closed-issue exemption is unit-tested separately, with a
controllable stub, in tests/test_design_gate.py).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-commit-without-design.sh"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                    # noqa: E402


# The corpus slice is a MOVING window (the N most recent distinct subjects),
# and the replay invariant is shape-stable for every future commit -- the ref
# extractor IS the hook's own design_gate.issue_refs -- EXCEPT one class: the
# hook honors its `[no-design: reason]` / bare `[no-design]` bypass tag
# BEFORE extracting refs (pass resp. block regardless of refs), so the FIRST
# commit subject ever to carry the tag would break CI retroactively. Exclude
# that single class here; the bypass path itself stays tested in
# tests/test_block_commit_without_design.py (TestBypass: bare tag rejected
# rc 2, reasoned tag honored rc 0 + audit-logged). (#683 run-3 hardening.)
_BYPASS_TAG_RE = re.compile(r"\[no-design\b")


def _replayable(subject):
    """False for the one corpus shape whose ON-arm expectation is not
    derivable from issue_refs alone (the hook's own bypass tag)."""
    return not _BYPASS_TAG_RE.search(subject)


def _scratch_home(prefix):
    """A relocated $HOME for one hook arm -- isolates ~/.claude marker state.

    It must carry its OWN git trust config: in a container CI the job runs
    as uid 0 while the bind-mounted workspace is owned by the host runner
    uid, so without `safe.directory` every `git -C ROOT` the hook runs dies
    `fatal: detected dubious ownership` -- repo_name_for() then returns ""
    and the hook's documented unmeasurable->never-block fail-open silently
    passes EVERY commit (run 32836799214: all 28 has-ref corpus commits
    misreported as [has-ref-but-ON-passed] at once). A real operating $HOME
    always carries whatever trust config makes the repo readable; the
    scratch HOME replicates exactly that, nothing more."""
    home = Path(tempfile.mkdtemp(prefix=prefix))
    (home / ".claude").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        "[safe]\n\tdirectory = %s\n" % ROOT, encoding="utf-8")
    return home


def real_commit_subjects(limit=None):
    """Every distinct commit SUBJECT line across all branches of this repo's
    own history (`git log --all --format=%s`) -- the real shapes the hook
    must classify: `(#N)`, `Closes #N`, `#A/#B`, and plenty with no
    reference at all. Deduplicated (the corpus has many near-identical
    RED/GREEN pairs); order preserved. Bypass-tagged subjects are excluded
    (see _replayable above)."""
    r = subprocess.run(["git", "-C", str(ROOT), "log", "--all", "--format=%s"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return []
    seen, out = set(), []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        if not _replayable(line):
            continue
        out.append(line)
    return out[:limit] if limit else out


def _stub_gh_bindir():
    """A `gh` that always answers OPEN for `gh issue view <n> --json state
    --jq .state` -- reproduces the pre-#206 unconditional-required
    behaviour exactly (no real gh call, no network), so this script's own
    "no gh, no network" contract holds after #206."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-replay-gh-"))
    fake = d / "gh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = issue ] && [ "$2" = view ]; then echo OPEN; exit 0; fi\n'
        'exit 1\n')
    fake.chmod(0o755)
    return d


def run_hook(message, agent_type, home, gh_bindir):
    payload = {"tool_input": {"command": 'git commit -m "%s"' % message.replace('"', '\\"')},
              "session_id": "replay-sess", "cwd": str(ROOT)}
    if agent_type is not None:
        payload["agent_type"] = agent_type
        payload["agent_id"] = "replay-agent"
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(gh_bindir) + os.pathsep + env.get("PATH", "")
    try:
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env, timeout=15)
        return r.returncode
    except subprocess.TimeoutExpired:
        return -1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    subjects = real_commit_subjects(args.limit)
    if not subjects:
        print("replay: no commit subjects found (git log failed?)", file=sys.stderr)
        return 1

    home_on = _scratch_home("airuleset-replay-on-")
    home_off = _scratch_home("airuleset-replay-off-")
    gh_bindir = _stub_gh_bindir()

    n_total = 0
    n_has_ref = 0
    n_no_ref = 0
    n_agree_no_ref = 0
    n_intended_delta = 0
    mismatches = []
    sample_refs = []

    try:
        for msg in subjects:
            n_total += 1
            refs = dg.issue_refs(msg)
            rc_on = run_hook(msg, "autopilot-worker", home_on, gh_bindir)
            rc_off = run_hook(msg, None, home_off, gh_bindir)

            if rc_off != 0:
                mismatches.append(("OFF-arm-blocked", msg, rc_on, rc_off, refs))
                continue

            if not refs:
                n_no_ref += 1
                if rc_on == 0:
                    n_agree_no_ref += 1
                else:
                    mismatches.append(("no-ref-but-ON-blocked", msg, rc_on, rc_off, refs))
            else:
                n_has_ref += 1
                if len(sample_refs) < 15:
                    sample_refs.append((msg, refs))
                if rc_on == 2:
                    n_intended_delta += 1
                else:
                    mismatches.append(("has-ref-but-ON-passed", msg, rc_on, rc_off, refs))
    finally:
        shutil.rmtree(home_on, ignore_errors=True)
        shutil.rmtree(home_off, ignore_errors=True)
        shutil.rmtree(gh_bindir, ignore_errors=True)

    print("=== CORPUS REPLAY: block-commit-without-design.sh ===")
    print("repo: %s" % ROOT)
    print("total distinct commit subjects examined: %d" % n_total)
    print()
    print("no issue reference: %d  (both arms AGREE=0 for %d, mismatch for %d)"
          % (n_no_ref, n_agree_no_ref, n_no_ref - n_agree_no_ref))
    print("has issue reference: %d  (ON correctly blocked %d, mismatch for %d)"
          % (n_has_ref, n_intended_delta, n_has_ref - n_intended_delta))
    print()
    print("OFF arm (agent_type=None) blocked ANYTHING: %d (must be 0)"
          % sum(1 for m in mismatches if m[0] == "OFF-arm-blocked"))
    print()
    print("--- sample of extracted issue refs (first 15 has-ref commits) ---")
    for msg, refs in sample_refs:
        print("  refs=%-12s %s" % (str(refs), msg[:90]))
    print()
    if mismatches:
        print("=== MISMATCHES (%d) ===" % len(mismatches))
        for kind, msg, rc_on, rc_off, refs in mismatches[:40]:
            print("  [%s] refs=%s rc_on=%s rc_off=%s :: %s"
                  % (kind, refs, rc_on, rc_off, msg[:100]))
        if len(mismatches) > 40:
            print("  ... and %d more" % (len(mismatches) - 40))
        return 1
    print("VERDICT: 0 mismatches -- the two arms agree on every no-reference "
          "commit, and the ON arm blocks exactly the has-reference commits "
          "(the gate's intended, and ONLY, behavioural delta).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
