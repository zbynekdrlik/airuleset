"""#1209: the auto-filed severe disk ticket listed only RECLAIMABLE candidates
under "Top consumers still on disk", so a box whose space was all protected
(dev1, 1.10.2026: 8.0 GB in one live camera-box scratchpad) filed a ticket
saying "(none)". The body now says what the list is and names the largest
live session scratchpad from the status the drain already computed."""

import watchdog.disk_guard_escalation as esc


def _human(b):
    return "%.1fG" % (b / 1e9)


def _compose(status, top=()):
    return esc.compose(status, "dev1", list(top), _human, 75)[1]


def test_the_reclaimable_list_is_named_for_what_it_is():
    body = _compose({"worst_pct": 95, "dim": "bytes"})
    assert "Reclaimable candidates still on disk:" in body
    assert "Top consumers still on disk:" not in body
    assert "- (none)" in body


def test_the_largest_live_scratchpad_is_named():
    path = "/tmp/claude-1000/-home-newlevel-devel-camera-box/90bc51f3-acd5"
    body = _compose({"worst_pct": 95, "dim": "bytes",
                     "largest_live_scratch": {"path": path, "bytes": 8_000_000_000}})
    assert "Largest live session scratchpad (kept while the session lives):" in body
    assert path in body and "8.0G" in body


def test_no_live_scratch_line_without_the_signal():
    assert "Largest live session scratchpad" not in _compose(
        {"worst_pct": 95, "dim": "bytes"})
