"""gates.filing -- the ungated-issue-filing gate (#137/#329/#390/#842/#962/#993/
#1020), migrated in #1020 Part 2 out of hooks/block-ungated-issue-filing.sh's
embedded `python3 <<PYEOF` heredoc into an importable, testable package.

Submodules by CONCERN, each under the 600-line ratchet:
  * parse    -- command + issue-body parsing (pure; adopts gates.shellcmd)
  * caps     -- gh-backed lookups, soft caps, near-dup, stream-routing, ratchet
  * presence -- the UNATTENDED read + dismissal-word / owner-quote gates
  * render   -- the verbatim block-message walls + summary formatting
  * __main__ -- the orchestrator run as `python3 -m gates.filing`

Behaviour is byte-preserving vs the shipped bash classifier -- proven by
tests/test_scope_gate.py (the ~120-test end-to-end oracle) + the
tests/test_gates_filing_char.py characterization pins. STDLIB ONLY at import
time; `airuleset` (authority) and `ratchet_counts` (net-drain) import lazily
inside caps, resolved via the adapter's PYTHONPATH=REPO_ROOT.
"""
