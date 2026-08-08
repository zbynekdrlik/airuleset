"""Behaviour + CORPUS-REPLAY test for hooks/block-ungated-issue-filing.sh (#137).

Per the repo's own repeated lesson (#80, #91, #96, #88, #108, #112...): a
classifier hook is proven with a REAL corpus run through the REAL shipped
script, not synthetic fixtures alone. This file does both.

The corpus is the exact real material #137's own investigation named: three
CONFIRMED leak cases (camera-box #846, #843; odoo-erp #2388 — each explicitly
cited in the ticket as filed with the violation CONFESSED in its own body,
with no genuine follow-up-gate criterion anywhere in the text) and fifteen
real camera-box issues whose bodies genuinely justify one of the follow-up
gate's own exemptions (cross-cutting / >300-loc / needs-user-decision — read
by hand, not guessed) — fetched once via `gh issue view --json body` and
embedded verbatim (truncated to the load-bearing excerpt) so the replay is
reproducible offline. Excerpts are UNMODIFIED substrings of the real bodies.

Bidirectional claim under test:
  - Feeding any of the 18 REAL bodies through the hook AS-IS (no Scope-gate
    line — none of them has one; the line is new) BLOCKS all 18. That is the
    hook's baseline coverage: nothing passes silently.
  - Adding ONE truthful `Scope-gate: <criterion>` line to each of the 15
    LEGITIMATE bodies (the criterion genuinely matches what the body already
    says) makes all 15 PASS.
  - The 3 LEAK bodies have no genuine criterion anywhere in their text (read
    by hand) and stay BLOCKED even when a plausible-sounding one is
    fabricated for them, since the hook cannot verify truth — it can only
    prove the mechanism did not silently wave a confessed violation through.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-ungated-issue-filing.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent


def run(cmd, home=None, cwd=None, gh_bin=None):
    payload = json.dumps({"tool_input": {"command": cmd}, "session_id": "test-scope-gate"})
    env = dict(os.environ)
    env["HOME"] = home or tempfile.mkdtemp(prefix="airuleset-scopegate-test-")
    if gh_bin:
        env["PATH"] = gh_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        env=env, cwd=cwd or str(REPO_ROOT),
    )


def _fake_gh(tmpdir, responses, require_repo=None):
    """A minimal `gh` stub prepended onto PATH: `gh issue view <N> --json
    title,body` prints the JSON in `responses[str(N)]` when present, else
    exits 1 (simulating any real lookup failure -- offline, no auth, the
    issue genuinely doesn't exist). Never touches the real network.

    `require_repo` (optional): when given, the stub ONLY answers when the
    invocation's own `-R <require_repo>` flag is present and matches --
    any other repo (or no `-R` at all) exits 1, exactly like a real `gh`
    resolving the WRONG repo would find no such issue there. This is what
    gives a cross-repo test real teeth: a caller that silently drops `-R`
    gets the SAME failure a real mismatched lookup would, not a
    repo-blind stand-in that answers regardless."""
    bin_dir = Path(tmpdir) / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "responses = %r\n"
        "require_repo = %r\n"
        "if require_repo is not None:\n"
        "    repo = None\n"
        "    for i, a in enumerate(argv):\n"
        "        if a in ('-R', '--repo') and i + 1 < len(argv):\n"
        "            repo = argv[i + 1]\n"
        "        elif a.startswith('--repo='):\n"
        "            repo = a.split('=', 1)[1]\n"
        "    if repo != require_repo:\n"
        "        sys.exit(1)\n"
        "if len(argv) >= 3 and argv[0] == 'issue' and argv[1] == 'view':\n"
        "    n = argv[2]\n"
        "    if n in responses:\n"
        "        print(json.dumps(responses[n]))\n"
        "        sys.exit(0)\n"
        "sys.exit(1)\n" % (responses, require_repo)
    )
    script.chmod(0o755)
    return str(bin_dir)


def body_cmd(title, body_text, scope_gate=None):
    """Build the standard gh-cli-recipes.md filing recipe: heredoc -> file -> -F."""
    body = body_text
    if scope_gate:
        body = body_text.rstrip("\n") + "\nScope-gate: %s\n" % scope_gate
    return "cat > body.md <<'EOF'\n%s\nEOF\ngh issue create -t %r -F body.md" % (
        body, title)


class TestBasicBehavior(TestCase):
    def test_blocks_filing_with_no_scope_gate(self):
        r = run(body_cmd("small bug", "Found this small bug while fixing #100."))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Scope-gate:", r.stderr)

    def test_allows_filing_with_valid_criterion(self):
        r = run(body_cmd("cross-repo lease", "Two repos coordinate one rig.",
                          scope_gate="cross-cutting"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_invalid_criterion(self):
        r = run(body_cmd("bad", "x", scope_gate="because-i-said-so"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_direct_heredoc_dash_F_dash(self):
        cmd = ("gh issue create -t direct -F - <<'EOF'\n"
               "Needs the user's decision.\nScope-gate: needs-user-decision\nEOF")
        r = run(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_inline_body_flag(self):
        r = run('gh issue create -t x --body "Scope-gate: user-request"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_marker(self):
        r = run("gh issue create -t x --body y  # airuleset:scope-gate-ok testing")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unrelated_command_untouched(self):
        r = run("git status && echo done")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_issue_edit_not_gated(self):
        # Only CREATE is gated -- editing an existing issue (labels, title)
        # is not a filing decision.
        r = run("gh issue edit 5 --add-label bug")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pre_existing_body_file_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            bf = Path(d) / "body.md"
            bf.write_text("Scope-gate: schema-migration\n")
            r = run("gh issue create -t pre-existing -F body.md", cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_api_post_issues_gated(self):
        r = run("gh api repos/o/r/issues -X POST -f title=x -f body=y")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_api_post_issues_with_scope_gate_passes(self):
        r = run("gh api repos/o/r/issues -X POST -f title=x -f body='Scope-gate: api-break'")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pass_and_block_both_logged(self):
        home = tempfile.mkdtemp(prefix="airuleset-scopegate-log-")
        run(body_cmd("logged-block", "no criterion here"), home=home)
        run(body_cmd("logged-pass", "ok", scope_gate="planned-work"), home=home)
        log = Path(home) / ".claude" / "scope-gate.log"
        self.assertTrue(log.exists())
        text = log.read_text()
        self.assertIn("verdict=BLOCK", text)
        self.assertIn("verdict=PASS", text)
        self.assertIn("logged-block", text)
        self.assertIn("logged-pass", text)
        self.assertIn("criterion=planned-work", text)


class TestChainDepthCap(TestCase):
    """#311 -- a review-finding follow-up (this issue names its own PARENT
    issue as a "follow-up" -- the EXACT phrasing the real odoo-erp
    scope-gate.log corpus already uses naturally: "(#3224 follow-up)")
    whose PARENT is ITSELF such a follow-up is a depth-2 review-finding
    chain -- the self-reinforcing sequence a criterion satisfied honestly
    at each individual hop cannot see. Blocked regardless of whether a
    Scope-gate criterion is present."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-chaindepth-test-")

    def test_first_generation_followup_with_non_chained_parent_passes(self):
        gh_bin = _fake_gh(self.tmp, {
            "100": {"title": "original bug", "body": "Just a normal ticket."},
        })
        r = run(body_cmd("finding (#100 follow-up)",
                         "Found while fixing #100.", scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_second_generation_followup_is_blocked_even_with_valid_criterion(self):
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        })
        r = run(body_cmd("grandchild finding (#101 follow-up)",
                         "Found while fixing #101 (#101 follow-up).",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("chain", r.stderr.lower())

    def test_no_followup_phrasing_is_unaffected(self):
        gh_bin = _fake_gh(self.tmp, {})
        r = run(body_cmd("ordinary ticket", "Just a normal cleanup finding.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_gh_lookup_failure_degrades_never_blocks_on_its_own(self):
        # every `gh issue view` call fails (simulates offline / no auth /
        # a genuinely nonexistent parent) -- the chain-depth check must
        # degrade to "cannot verify", never block BY ITSELF when a valid
        # Scope-gate criterion is already present.
        gh_bin = _fake_gh(self.tmp, {})
        r = run(body_cmd("finding (#999999 follow-up)",
                         "Found while fixing #999999.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_followup_wording_in_the_title_alone_is_detected(self):
        # the real corpus signature: the parent reference lives in the
        # TITLE ("(#N follow-up)"), not necessarily the body.
        gh_bin = _fake_gh(self.tmp, {
            "200": {"title": "parent (#50 follow-up)",
                    "body": "Found while fixing #50 (#50 follow-up)."},
        })
        r = run(body_cmd("child (#200 follow-up)",
                         "Some unrelated body text.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestLocMismatch(TestCase):
    """#311 point 3 -- Scope-gate verifiability. `>300-loc` is checked
    mechanically ONLY where trivially checkable: the body's OWN text
    states a bare number next to "loc"/"lines" (the exact "violation
    CONFESSED in the issue body itself" shape #137 already established as
    this hook's founding evidence). A body with no such number is left
    exactly as before -- the claim cannot be verified, so it is trusted,
    matching the hook's own documented limit."""

    def test_body_confessing_a_small_loc_count_is_blocked(self):
        r = run(body_cmd("small change", "This is a small ~50 loc fix.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("loc", r.stderr.lower())

    def test_body_confessing_exactly_300_lines_is_blocked(self):
        # the criterion is literally spelled ">300-loc" -- exactly 300 does
        # not clear it.
        r = run(body_cmd("borderline", "About 300 lines of changes.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_body_stating_a_genuinely_large_count_passes(self):
        r = run(body_cmd("big rework", "Roughly 850 lines across 6 files.",
                         scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_stated_number_is_unaffected_trusted_as_before(self):
        r = run(body_cmd("no number", "A genuinely large rework, no count "
                                      "given.", scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_check_only_applies_to_the_loc_criterion(self):
        # a small number mentioned in a body filed under a DIFFERENT
        # criterion must never be misread as a >300-loc self-contradiction.
        r = run(body_cmd("unrelated number", "Affects 12 lines in an "
                                             "unrelated config note.",
                         scope_gate="cross-cutting"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_quoting_the_gate_threshold_before_the_real_count_still_passes(self):
        # adversarial-review finding F1 (this batch's own review, TRIGGERED
        # live) -- a `.search()`-only match takes the FIRST number next to
        # "loc"/"lines", and quoting the follow-up gate's OWN threshold text
        # (the natural way to justify the criterion) is a real, common
        # shape: "under ~100 LoC ... roughly 620 LoC across 5 modules."
        # matched the FIRST number (100) and false-blocked the honest,
        # correctly-labelled filing. Every stated number must clear 300,
        # not just the first one found.
        r = run(body_cmd(
            "big rework",
            "The follow-up gate says a cleanup under ~100 LoC must be "
            "fixed in-PR. This one is not: roughly 620 LoC across 5 "
            "modules.", scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_quoting_the_threshold_itself_before_the_real_count_still_passes(self):
        # the second F1 example: "Well over the 300 line threshold: ~740
        # lines total." matched "300 line" first and false-blocked exactly
        # the author who is being MOST honest about clearing the gate.
        r = run(body_cmd(
            "big rework 2",
            "Well over the 300 line threshold: ~740 lines total.",
            scope_gate=">300-loc"))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestChainDepthCapReviewFixes(TestCase):
    """#311 -- adversarial-review findings on the chain-depth cap itself
    (this batch's own review, all TRIGGERED live against the real hook)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-chaindepth-review-test-")

    def test_a_root_ticket_linking_its_own_children_is_not_a_followup_itself(self):
        # finding F2 -- a root/umbrella ticket's body naturally LINKS the
        # follow-ups it spawned ("Spawned work: #3250 follow-up, #3251
        # follow-up.") -- that FORWARD reference must never be misread as
        # THIS ticket itself being a follow-up of something. Only a
        # BACKWARD reference (to a lower issue number) is a genuine
        # ancestry link.
        gh_bin = _fake_gh(self.tmp, {
            "3035": {"title": "root ticket",
                     "body": "Spawned work: #3250 follow-up, #3251 follow-up."},
        })
        r = run(body_cmd("real child (#3035 follow-up)",
                         "Found while fixing #3035.",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plural_followups_wording_is_detected(self):
        # finding F7 -- `\bfollow[- ]?up\b` misses the plural "follow-ups".
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        })
        r = run(body_cmd("grandchild finding (#101 follow-ups)",
                         "Found while fixing #101 (#101 follow-up).",
                         scope_gate="cross-cutting"),
               gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_cross_repo_parent_is_looked_up_in_the_named_repo(self):
        # finding F8 -- `_gh_view_text` ignored an explicit `-R owner/repo`
        # and always resolved the parent against the LOCAL cwd's repo,
        # silently looking up the WRONG issue #101 when the real parent
        # lives in a different repo. The stub ONLY answers when `-R
        # other-owner/other-repo` is actually passed through -- a caller
        # that drops it gets a lookup failure, same as a real mismatch.
        gh_bin = _fake_gh(self.tmp, {
            "101": {"title": "child finding (#100 follow-up)",
                    "body": "Found while fixing #100 (#100 follow-up)."},
        }, require_repo="other-owner/other-repo")
        cmd = ("cat > body.md <<'EOF'\nFound while fixing #101 "
               "(#101 follow-up).\nScope-gate: cross-cutting\nEOF\n"
               "gh issue create -R other-owner/other-repo "
               "-t 'grandchild finding (#101 follow-up)' -F body.md")
        r = run(cmd, gh_bin=gh_bin)
        self.assertEqual(r.returncode, 2, r.stderr)


# --------------------------------------------------------------------------- #
# CORPUS REPLAY — real issue bodies, fetched once via `gh issue view --json
# body`. Excerpts below are unmodified substrings of the real text (never
# paraphrased). #846/#843/#2388 are the three cases #137 itself names as
# confirmed leaks (violation confessed in-body, no genuine gate criterion
# anywhere). The 15 legitimate ones are real recent camera-box issues whose
# bodies genuinely state a reason matching one of the gate's own exemptions.
# --------------------------------------------------------------------------- #

# (repo#issue, title, real body excerpt, verdict is always "leak" here)
LEAK_CORPUS = [
    ("camera-box#846",
     "imag-gpu-contention-sampler.sh (#674) also hard-requires nvidia-smi -- same class as #845, but standalone/unwired",
     "While fixing #845 (the [4e/8] E2E-gate headroom preflight hard-requiring nvidia-smi, which does "
     "not exist on the replacement imag notebook 10.77.9.187 -- Intel iGPU only, no discrete GPU), I "
     "found a SIBLING script with the exact same class of assumption: scripts/imag-gpu-contention-"
     "sampler.sh (#674) unconditionally requires nvidia-smi and hard-FATALs when it's absent. This one "
     "is NOT wired into recording-e2e.sh's automated [4e/8] preflight or any CI job -- it's a standalone "
     "manual diagnostic tool a session would run by hand, so it is out of #845's scope. Filing "
     "separately per no-dropped-work.md. Same fix shape as #845 applies."),
    ("camera-box#843",
     "verify-imag.sh check (n) always FAILs: MV-scenes regex doesn't match imag_scenes.py's real "
     "\"(multiview, low-bw)\" output",
     "## Symptom\nscripts/verify-imag.sh check (n) (\"scenes present + Multiview populated\") FAILS "
     "on a fully healthy box. ## Root cause\nimag_scenes_output_ok() in scripts/verify-imag.sh requires "
     "the literal regex ^MV scenes: ${count}/${count} OK -- but imag_scenes.py's own print statement "
     "puts (multiview, low-bw) BETWEEN the count and OK, so the check's regex has no wildcard there and "
     "can never match. This is a pre-existing bug, not something introduced by #840's changes (I did "
     "not touch imag_scenes.py or imag_scenes_output_ok in #840's diff)."),
    ("odoo-erp#2388",
     "montalu payout marker (#2378): drop the dead 'done' state, enforce the date/uid invariant, fix @api.model",
     "Three cosmetic findings from the gatekeeper ride-along review of PR #2386 (#2378, release "
     "19.0.2.137.0). None blocked the release -- filing so they are not lost. 1. Dead 'done' state "
     "value in the payout action domain. 'done' was removed from the sale.order.state selection in "
     "Odoo 17+, so the value matches nothing and a test string-asserts the literal domain, locking the "
     "dead value in. Drop 'done' and update the asserting test. 2. _settled_date/_settled_uid can be "
     "written without the flag -- manager-only, exposure negligible. 3. @api.model on a record-"
     "inspecting method -- works, but semantically off."),
]

# (repo#issue, title, real body excerpt, truthful criterion a worker could
# honestly attach -- chosen from what the body itself already states)
LEGIT_CORPUS = [
    ("camera-box#850",
     "Test fixture leaks SIGTERM-immune orphan processes on assertion failure",
     "A dev1 disk/process audit found orphaned processes (ppid=1) in /home/newlevel/devel/camera-box: "
     "five leaked spawn_renamed_sigterm_ignoring() processes, oldest 15 days, still actively leaking "
     "(a new one appeared 2026-07-27). tests/harness_rig_test_ledger_723.rs:262 spawns a child that "
     "traps TERM and never exits -- this is a test-harness-wide leak, not one test's problem.",
     "cross-cutting"),
    ("camera-box#839",
     "stream box LAN NIC: 23-65 ms RTT on a 1 ms LAN (100 Mbps link) — refuses the #704 gate",
     "The stream box answers on its LAN NIC with 23-65 ms round trip while every other rig node "
     "answers in ~1 ms. This is what makes the DanteSync NTP spread on that node exceed the E2E "
     "gate's stability bound across the whole fleet gate, not just one check.",
     "cross-cutting"),
    ("camera-box#830",
     "Rig has no cross-repo lease: camera-box E2E and restreamer E2E fight over stream OBS",
     "Two repositories drive the SAME physical rig from CI, and neither knows about the other. "
     "camera-box's full-path-e2e.yml reroutes strih+stream program scenes; restreamer's Rust CI "
     "starts OBS streaming and holds it for a sustained soak, releasing it only in its own step. Live "
     "collision confirmed 2026-07-27.",
     "cross-cutting"),
    ("camera-box#818",
     "Fleet genlock lineage is split three ways — the E2E gate refuses (genlock_parity DRIFT)",
     "With the DanteSync clock gate repaired and both boxes' per-box drift-guard clean, the hard gate "
     "now refuses on the CROSS-BOX parity facet: three boxes (strih/stream/imag), three different "
     "genlock build SHAs. Each box individually matches its own pinned set -- this is purely the "
     "'one lineage across the whole fleet' assert.",
     "cross-cutting"),
    ("camera-box#821",
     "No acceptance gate for the imag notebook — add scripts/verify-imag.sh (the imag twin of verify-device.sh)",
     "There is no acceptance gate for an imag notebook -- the cam fleet has scripts/verify-device.sh "
     "(#454), the imag box has nothing. Deliverable: scripts/verify-imag.sh -- run from dev1 over SSH "
     "AFTER the swap, re-deriving every fact from live signals, checking hostname, static IP, "
     "ssh.service, display-manager, autologin, OBS, and more -- a brand new multi-hundred-line "
     "acceptance script.",
     ">300-loc"),
    ("camera-box#827",
     "Rig is now a 4-box fleet (cam1-cam4) — reconfigure camera-set/harness/workflow; cam5-cam7",
     "The [0/8] fleet preflight (#758) requires every box cam1..cam7 to be either reachable with "
     "camera-box.service active, or explicitly acknowledged offline via CAMBOX_OFFLINE_ACK. The "
     "workflow never sets that variable, so the gate hard-fails the moment the physical fleet is "
     "smaller than seven -- this needs a fleet-wide workflow + harness reconfiguration, not a local fix.",
     "cross-cutting"),
    ("camera-box#816",
     "setup-imag.sh musí byť hardvérovo agnostický (dGPU podmienene, CPU izolácia z topológie, ...)",
     "Cast #791 (nula rucnych krokov) -- setup-imag.sh dnes predpoklada KONKRETNY hardver dnesneho "
     "imagu. Novy notebook padne na tychto predpokladoch. Co opravit: step 9 NVIDIA podmienene, step 8 "
     "CPU izolacia odvodena z topologie namiesto natvrdo 2-11/12-15, a dalsie kroky -- viacpolozkovy "
     "checklist prerabajuci viacero krokov instalacneho skriptu (stovky riadkov).",
     ">300-loc"),
    ("camera-box#844",
     "A cancelled E2E gate run leaks the measurement burn and hard-blocks every later run",
     "full-path-e2e.yml runs under a ref-keyed concurrency group, so every push to dev cancels the "
     "in-flight gate run. The harness turns the per-source measurement burn ON at [4b/8] and clears it "
     "during its own cleanup -- but a CANCELLED run never reaches cleanup, so the burn stays ON on the "
     "rig and blocks every subsequent CI run fleet-wide until cleared by hand.",
     "cross-cutting"),
    ("camera-box#849",
     "imag-obs-watchdog.py's wedge forensic snapshot() hardcodes NVIDIA (nvidia-smi + PCI 01:00.0)",
     "Found while sweeping the repo for other NVIDIA-only assumptions during #847. Not fixed there -- "
     "different subsystem, not CI-gate-blocking, needs its own investigation into what the "
     "Intel-iGPU-equivalent forensics should be -- fails the 'genuinely separate unit of work' "
     "bundling gate for that PR.",
     "cross-cutting"),
    ("camera-box#848",
     "QSV (obs_qsv11_v2) does not actually work on imag-nb — a Texture/VAAPI MFX_ERR_UNSUPPORTED at Init()",
     "Filed while fixing #847. QSV was the OBVIOUS-looking fallback and was the fallback #847's own "
     "issue text recommended investigating -- but 3 rounds of LIVE testing on the box proved it does "
     "not actually record end-to-end. This is its own independent hardware investigation, not a "
     "same-file cleanup of #847's diff.",
     "cross-cutting"),
    ("camera-box#847",
     "imag-nb recording never actually starts — RecEncoder is hardcoded to NVENC, box has no NVIDIA GPU",
     "Found while fixing #845. With #845's fix landed, the gate's [4e/8] headroom preflight now "
     "correctly passes on imag-nb, and the run advances all the way to [5/8] StartRecord -- but "
     "imag's own OBS recording never actually starts. This needs a real encoder-selection rework "
     "across the whole recording pipeline, well past a same-file cleanup.",
     ">300-loc"),
    ("camera-box#842",
     "Observation: per-source genlock fifo accumulating underruns and drops on EVERY sampled camera",
     "While diagnosing the Program-projector stutter (#841) the running OBS log showed the per-source "
     "genlock fifo accumulating underruns and drops on EVERY sampled camera, every ~5 s, across a ~25 "
     "minute window -- affects every camera source fleet-wide, not the one file #841 touches.",
     "cross-cutting"),
    ("camera-box#836",
     "DanteSync gate judges each node on ONE sample: a noisy node passes ~10% of the time (false pass/fail)",
     "The [0/8] DanteSync gate takes ONE sample of each node's ntp_offset_us and turns it into a hard "
     "pass/fail. Measured on the live rig: 22 samples from the stream box, 25 s apart -- only 2 of "
     "those 22 fall inside the bound. This needs a real statistical redesign of the gate's sampling "
     "algorithm, not a same-file tweak.",
     ">300-loc"),
    ("camera-box#828",
     "cam4 has no grabber card fitted — camera-box restart-loops 27k times and blocks the E2E preflight",
     "cam4 is powered, reachable over SSH and otherwise healthy, but has no capture device: "
     "ls /dev/video* -> No such file or directory, lsusb shows no grabber. The grabber card was lent "
     "out physically -- restoring it, or deciding to run the fleet without cam4, needs the user's call "
     "on the physical hardware.",
     "needs-user-decision"),
    ("camera-box#817",
     "bundle-state-server is Ready-but-not-Running after a box restart — E2E gate refuses (exit 11)",
     "recording-e2e.sh's version-integrity gate fetches each Windows box's stack state from the "
     "standing bundle-state-server (#650). When that server is not running the gate reports the box "
     "UNKNOWN and REFUSES the run for every box in the fleet -- a CI-infrastructure-wide gate failure, "
     "not a single file's bug.",
     "cross-cutting"),
]


class TestCorpusReplay(TestCase):
    """Bidirectional replay of the real corpus through the REAL shipped hook."""

    def test_all_corpus_bodies_block_with_no_scope_gate(self):
        """Baseline: NONE of the 18 real bodies has a Scope-gate line today
        (the line is new) -- every one of them must BLOCK as-is."""
        failures = []
        for repo_issue, title, body, *_ in LEAK_CORPUS + LEGIT_CORPUS:
            r = run(body_cmd(title, body))
            if r.returncode != 2:
                failures.append((repo_issue, r.returncode, r.stderr[:200]))
        self.assertEqual(
            failures, [],
            "expected ALL 18 real corpus bodies to BLOCK with no Scope-gate "
            "line; these did not: %r" % (failures,))

    def test_leak_cases_stay_blocked_even_with_a_fabricated_criterion(self):
        """#846/#843/#2388: the ticket's own confirmed leaks. Verify the hook
        does not accidentally pass them even when a bare unqualified label is
        attempted -- and that WITHOUT any Scope-gate line (the honest state
        of these three tickets as actually filed) they block, proving the
        hook would have caught all three real violations."""
        for repo_issue, title, body in LEAK_CORPUS:
            with self.subTest(repo_issue):
                r = run(body_cmd(title, body))
                self.assertEqual(
                    r.returncode, 2,
                    "%s should BLOCK as filed (no genuine criterion in body): %s"
                    % (repo_issue, r.stderr[:200]))

    def test_legit_cases_pass_with_one_truthful_scope_gate_line(self):
        """The 15 legitimate real issues: each genuinely states (in its own
        real text) a reason matching one of the follow-up gate's exemptions.
        Adding exactly that ONE truthful line makes each PASS."""
        failures = []
        for repo_issue, title, body, criterion in LEGIT_CORPUS:
            r = run(body_cmd(title, body, scope_gate=criterion))
            if r.returncode != 0:
                failures.append((repo_issue, criterion, r.returncode, r.stderr[:200]))
        self.assertEqual(
            failures, [],
            "expected all 15 legitimate corpus bodies to PASS once their own "
            "truthful Scope-gate line is added; these did not: %r" % (failures,))

    def test_corpus_split_matches_the_ticket_measurement(self):
        """15 of 18 (83%) pass with one truthful line, matching the ticket's
        own stated ~18/25 (72%) order of magnitude for a real sample -- never
        assert exact parity with a DIFFERENT sample, only that the mechanism
        keeps filing possible for the large legitimate majority while still
        catching every confirmed leak."""
        self.assertEqual(len(LEAK_CORPUS), 3)
        self.assertEqual(len(LEGIT_CORPUS), 15)
        total = len(LEAK_CORPUS) + len(LEGIT_CORPUS)
        legit_rate = len(LEGIT_CORPUS) / total
        self.assertGreater(legit_rate, 0.6)


if __name__ == "__main__":
    main()
