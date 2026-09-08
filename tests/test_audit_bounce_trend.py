"""Tests for scripts/audit_bounce_rule_updates.py and the
cli_quals._bounce_label_events timestamp extraction (#957).

Covers:
- _bounce_label_events: timestamp parsing, Z-suffix, fail-safe on garbage
- _count_bounce_label_events: delegates to _bounce_label_events (one source)
- compute_trends: falling/flat/rising/none, treadmill!, prevencia_missing
- canonical_stream: rename-equivalence grouping (#537)
- Zero hand-offs -> n/a rate
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals  # noqa: E402

# Import the script's helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_bounce_rule_updates as abr  # noqa: E402


def _make_events_json(*timestamps):
    """Build a GitHub events API JSON array with prio:bounce label-add events
    at the given ISO 8601 timestamps."""
    events = []
    for ts in timestamps:
        events.append({
            "event": "labeled",
            "label": {"name": "prio:bounce"},
            "created_at": ts,
        })
    return json.dumps(events)


class TestBounceTimestampExtraction(TestCase):
    """cli_quals._bounce_label_events returns UTC datetimes."""

    def test_single_event_z_suffix(self):
        raw = _make_events_json("2026-09-01T10:00:00Z")
        result = cli_quals._bounce_label_events(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].year, 2026)
        self.assertEqual(result[0].month, 9)
        self.assertEqual(result[0].day, 1)
        self.assertEqual(result[0].hour, 10)
        self.assertIsNotNone(result[0].tzinfo)

    def test_multiple_events(self):
        raw = _make_events_json(
            "2026-09-01T10:00:00Z",
            "2026-09-02T12:00:00Z",
            "2026-09-05T08:00:00Z",
        )
        result = cli_quals._bounce_label_events(raw)
        self.assertEqual(len(result), 3)

    def test_empty_input(self):
        self.assertEqual(cli_quals._bounce_label_events(""), [])
        self.assertEqual(cli_quals._bounce_label_events(None), [])

    def test_garbage_input(self):
        self.assertEqual(cli_quals._bounce_label_events("not json"), [])
        self.assertEqual(cli_quals._bounce_label_events("42"), [])

    def test_non_bounce_events_excluded(self):
        events = json.dumps([
            {"event": "labeled", "label": {"name": "bug"},
             "created_at": "2026-09-01T10:00:00Z"},
            {"event": "labeled", "label": {"name": "prio:bounce"},
             "created_at": "2026-09-01T11:00:00Z"},
        ])
        result = cli_quals._bounce_label_events(events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].hour, 11)

    def test_unparseable_timestamp_gets_epoch(self):
        events = json.dumps([
            {"event": "labeled", "label": {"name": "prio:bounce"},
             "created_at": "garbage-date"},
        ])
        result = cli_quals._bounce_label_events(events)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].year, 1970)


class TestCountDelegatesToTimestamps(TestCase):
    """_count_bounce_label_events is len(_bounce_label_events) — one source."""

    def test_count_matches_len(self):
        raw = _make_events_json(
            "2026-09-01T10:00:00Z",
            "2026-09-02T12:00:00Z",
        )
        count = cli_quals._count_bounce_label_events(raw)
        timestamps = cli_quals._bounce_label_events(raw)
        self.assertEqual(count, len(timestamps))
        self.assertEqual(count, 2)

    def test_count_zero_on_empty(self):
        self.assertEqual(cli_quals._count_bounce_label_events(""), 0)


class TestComputeTrends(TestCase):
    """compute_trends: rate trends and flags."""

    def _now(self):
        return datetime.now(timezone.utc)

    def _ts(self, days_ago):
        return self._now() - timedelta(days=days_ago)

    def test_rising_trend(self):
        """More recent bounces than prior -> rising."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(2), self._ts(3)],
            "handoff_count": 3,
            "prevencia_missing": False,
        }, {
            "streams": ["david"],
            "bounce_timestamps": [self._ts(10)],
            "handoff_count": 1,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn("david", trends)
        self.assertEqual(trends["david"]["trend"], "rising")

    def test_falling_trend(self):
        """More prior bounces than recent -> falling."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(2)],
            "handoff_count": 1,
            "prevencia_missing": False,
        }, {
            "streams": ["david"],
            "bounce_timestamps": [self._ts(10), self._ts(11), self._ts(12)],
            "handoff_count": 3,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["trend"], "falling")

    def test_flat_trend(self):
        """Equal bounces in both windows -> flat."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(2), self._ts(10)],
            "handoff_count": 2,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["trend"], "flat")

    def test_none_trend(self):
        """No bounces in either window -> none."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(30)],  # outside both windows
            "handoff_count": 1,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["trend"], "none")

    def test_empty_input(self):
        trends = abr.compute_trends([], window_days=7)
        self.assertEqual(trends, {})

    def test_multiple_streams(self):
        """Per-stream grouping keeps streams separate."""
        items = [
            {
                "streams": ["david"],
                "bounce_timestamps": [self._ts(1)],
                "handoff_count": 1,
                "prevencia_missing": False,
            },
            {
                "streams": ["montalu"],
                "bounce_timestamps": [self._ts(10)],
                "handoff_count": 1,
                "prevencia_missing": False,
            },
        ]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn("david", trends)
        self.assertIn("montalu", trends)
        self.assertEqual(trends["david"]["recent_bounces"], 1)
        self.assertEqual(trends["montalu"]["prior_bounces"], 1)

    def test_treadmill_flag_within_24h(self):
        """Two bounces on same ticket within 24h -> treadmill!."""
        items = [{
            "number": 42,
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(1.5)],  # 12h apart
            "handoff_count": 2,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn(42, trends["david"]["treadmill_issues"])

    def test_no_treadmill_when_far_apart(self):
        """Two bounces > 24h apart -> no treadmill!."""
        items = [{
            "number": 42,
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(5)],  # 4 days apart
            "handoff_count": 2,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["treadmill_issues"], [])

    def test_prevencia_missing_flag(self):
        """Issue with >= 2 bounces and prevencia_missing -> flagged."""
        items = [{
            "number": 99,
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(2)],
            "handoff_count": 2,
            "prevencia_missing": True,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn(99, trends["david"]["prevencia_missing_issues"])

    def test_zero_handoffs_rate_is_na(self):
        """Zero hand-offs in window -> rate is n/a, not division by zero."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [],
            "handoff_count": 0,
            "prevencia_missing": False,
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["recent_rate"], "n/a")
        self.assertEqual(trends["david"]["prior_rate"], "n/a")


class TestCanonicalStream(TestCase):
    """canonical_stream uses _stream_rename_equivalents."""

    def test_identity(self):
        # A stream with no alias maps to itself.
        self.assertEqual(abr.canonical_stream("unknown_stream"),
                         "unknown_stream")

    def test_returns_string(self):
        result = abr.canonical_stream("david")
        self.assertIsInstance(result, str)


class TestDoctrinePresence(TestCase):
    """The always-on module carries the friction doctrine sentence."""

    def test_module_has_friction_sentence(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "modules/core/autonomous-quality-discipline.md").read_text()
        self.assertIn("Integration friction is a bug", text)
        self.assertIn("#957", text)
        self.assertIn("audit_bounce_rule_updates.py", text)

    def test_deep_companion_has_detail(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "skills/autonomous-quality-discipline-deep/DEEP.md").read_text()
        self.assertIn("Integration friction is a bug", text)
        self.assertIn("treadmill", text)
        self.assertIn("Prevencia", text)

    def test_trigger_row_fires_on_handoff(self):
        root = Path(__file__).resolve().parent.parent
        conf = (root / "hooks/situational-triggers.conf").read_text()
        # The aqd-deep row must fire on airuleset.py handoff.
        for line in conf.splitlines():
            if line.startswith("autonomous-quality-discipline-deep") and "Bash" in line:
                self.assertIn("airuleset\\.py handoff", line)
                return
        self.fail("autonomous-quality-discipline-deep Bash row not found")


if __name__ == "__main__":
    main()
