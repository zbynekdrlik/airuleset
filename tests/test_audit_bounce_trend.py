"""Tests for scripts/audit_bounce_rule_updates.py and the
cli_quals._bounce_label_events timestamp extraction (#957).

Covers:
- _bounce_label_events: timestamp parsing, Z-suffix, fail-safe on garbage
- _count_bounce_label_events: delegates to _bounce_label_events (one source)
- compute_trends: falling/flat/rising/none, treadmill!
- canonical_stream: rename-equivalence grouping (#537) via STREAM_RENAME_ALIASES
- Zero bounces -> n/a trend
- None timestamps (unparseable) excluded from window math but counted
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main, mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
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

    def test_unparseable_timestamp_gets_none(self):
        """C2 fix: unparseable timestamp -> None (not epoch)."""
        events = json.dumps([
            {"event": "labeled", "label": {"name": "prio:bounce"},
             "created_at": "garbage-date"},
        ])
        result = cli_quals._bounce_label_events(events)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0])

    def test_two_unparseable_no_false_treadmill(self):
        """C2: two None timestamps must not cause a false treadmill!."""
        items = [{
            "number": 99,
            "streams": ["david"],
            "bounce_timestamps": [None, None],
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["treadmill_issues"], [])


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

    def test_count_includes_unparseable(self):
        """An unparseable timestamp is counted (len includes None)."""
        events = json.dumps([
            {"event": "labeled", "label": {"name": "prio:bounce"},
             "created_at": "garbage"},
        ])
        self.assertEqual(cli_quals._count_bounce_label_events(events), 1)


class TestComputeTrends(TestCase):
    """compute_trends: count-based trends and flags."""

    def _now(self):
        return datetime.now(timezone.utc)

    def _ts(self, days_ago):
        return self._now() - timedelta(days=days_ago)

    def test_rising_trend(self):
        """More recent bounces than prior -> rising."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(2), self._ts(3)],
        }, {
            "streams": ["david"],
            "bounce_timestamps": [self._ts(10)],
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn("david", trends)
        self.assertEqual(trends["david"]["trend"], "rising")

    def test_falling_trend(self):
        """More prior bounces than recent -> falling."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(2)],
        }, {
            "streams": ["david"],
            "bounce_timestamps": [self._ts(10), self._ts(11), self._ts(12)],
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["trend"], "falling")

    def test_flat_trend(self):
        """Equal bounces in both windows -> flat."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(2), self._ts(10)],
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["trend"], "flat")

    def test_none_trend(self):
        """No bounces in either window -> none."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [self._ts(30)],  # outside both windows
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
            },
            {
                "streams": ["montalu"],
                "bounce_timestamps": [self._ts(10)],
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
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertIn(42, trends["david"]["treadmill_issues"])

    def test_no_treadmill_when_far_apart(self):
        """Two bounces > 24h apart -> no treadmill!."""
        items = [{
            "number": 42,
            "streams": ["david"],
            "bounce_timestamps": [self._ts(1), self._ts(5)],  # 4 days apart
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["treadmill_issues"], [])

    def test_none_timestamps_excluded_from_windows(self):
        """None timestamps (unparseable) don't count in window buckets."""
        items = [{
            "streams": ["david"],
            "bounce_timestamps": [None, self._ts(2)],
        }]
        trends = abr.compute_trends(items, window_days=7)
        self.assertEqual(trends["david"]["recent_bounces"], 1)


class TestCanonicalStream(TestCase):
    """canonical_stream uses STREAM_RENAME_ALIASES."""

    def test_identity_no_alias(self):
        """A stream with no alias maps to itself."""
        self.assertEqual(abr.canonical_stream("unknown_stream"),
                         "unknown_stream")

    def test_old_name_maps_to_new(self):
        """C1 fix: an old name maps to the rename target."""
        with mock.patch.object(airuleset, "STREAM_RENAME_ALIASES",
                               {"oldname": "newname"}):
            self.assertEqual(abr.canonical_stream("oldname"), "newname")
            self.assertEqual(abr.canonical_stream("newname"), "newname")

    def test_grouping_merges_old_and_new(self):
        """C1 fix: items with old and new stream names land in ONE row."""
        with mock.patch.object(airuleset, "STREAM_RENAME_ALIASES",
                               {"a": "a1"}):
            now = datetime.now(timezone.utc)
            items = [
                {
                    "streams": [abr.canonical_stream("a")],
                    "bounce_timestamps": [now - timedelta(days=1)],
                },
                {
                    "streams": [abr.canonical_stream("a1")],
                    "bounce_timestamps": [now - timedelta(days=2)],
                },
            ]
            trends = abr.compute_trends(items, window_days=7)
            # Both should land in "a1" (the canonical new name).
            self.assertIn("a1", trends)
            self.assertNotIn("a", trends)
            self.assertEqual(trends["a1"]["recent_bounces"], 2)


class TestWeeklyReport(TestCase):
    """Tests for --weekly-report mode (#963)."""

    def _now(self):
        return datetime.now(timezone.utc)

    def _ts(self, days_ago):
        return self._now() - timedelta(days=days_ago)

    def test_first_pass_rate_all_single_bounce(self):
        """All tickets with exactly 1 bounce event -> 100% first-pass."""
        items = [
            {"number": 1, "streams": ["david"],
             "bounce_timestamps": [self._ts(2)]},
            {"number": 2, "streams": ["david"],
             "bounce_timestamps": [self._ts(3)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        rate = abr.first_pass_rate(trends, items)
        self.assertEqual(rate["david"], 1.0)

    def test_first_pass_rate_mixed(self):
        """Mix of 1-bounce and multi-bounce tickets."""
        items = [
            {"number": 1, "streams": ["david"],
             "bounce_timestamps": [self._ts(2)]},
            {"number": 2, "streams": ["david"],
             "bounce_timestamps": [self._ts(1), self._ts(2)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        rate = abr.first_pass_rate(trends, items)
        self.assertAlmostEqual(rate["david"], 0.5)

    def test_first_pass_rate_no_items(self):
        """No items -> empty dict."""
        rate = abr.first_pass_rate({}, [])
        self.assertEqual(rate, {})

    def test_format_weekly_report_markdown(self):
        """Weekly report output is a markdown table."""
        items = [
            {"number": 1, "streams": ["david"],
             "bounce_timestamps": [self._ts(2)]},
            {"number": 2, "streams": ["david"],
             "bounce_timestamps": [self._ts(1), self._ts(3)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        output = abr.format_weekly_report(trends, items)
        self.assertIn("| stream", output)
        self.assertIn("| david", output)
        self.assertIn("first-pass", output)

    def test_format_weekly_report_multiple_streams(self):
        """Report covers all streams."""
        items = [
            {"number": 1, "streams": ["david"],
             "bounce_timestamps": [self._ts(2)]},
            {"number": 2, "streams": ["montalu"],
             "bounce_timestamps": [self._ts(1)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        output = abr.format_weekly_report(trends, items)
        self.assertIn("david", output)
        self.assertIn("montalu", output)

    def test_weekly_report_first_pass_100_pct(self):
        """The first-pass column shows percentage."""
        items = [
            {"number": 1, "streams": ["s1"],
             "bounce_timestamps": [self._ts(1)]},
            {"number": 2, "streams": ["s1"],
             "bounce_timestamps": [self._ts(2)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        output = abr.format_weekly_report(trends, items)
        # Both tickets have exactly 1 bounce -> 100%
        self.assertIn("100%", output)

    def test_first_pass_rate_zero_when_all_multi_bounce(self):
        """All tickets with >1 bounce -> 0% first-pass."""
        items = [
            {"number": 1, "streams": ["david"],
             "bounce_timestamps": [self._ts(1), self._ts(2)]},
            {"number": 2, "streams": ["david"],
             "bounce_timestamps": [self._ts(1), self._ts(3), self._ts(4)]},
        ]
        trends = abr.compute_trends(items, window_days=7)
        rate = abr.first_pass_rate(trends, items)
        self.assertAlmostEqual(rate["david"], 0.0)


class TestDoctrinePresence(TestCase):
    """The always-on module carries the friction doctrine sentence."""

    def test_module_has_friction_sentence(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "modules/core/autonomous-quality-discipline.md").read_text()
        self.assertIn("Integration friction is a bug", text)
        self.assertIn("#957", text)
        self.assertIn("audit_bounce_rule_updates.py", text)

    def test_module_has_first_pass_sentence(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "modules/core/autonomous-quality-discipline.md").read_text()
        self.assertIn("FIRST hand-off", text)
        self.assertIn("#963", text)

    def test_deep_companion_has_detail(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "skills/autonomous-quality-discipline-deep/DEEP.md").read_text()
        self.assertIn("Integration friction is a bug", text)
        self.assertIn("treadmill", text)

    def test_deep_has_first_pass_section(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "skills/autonomous-quality-discipline-deep/DEEP.md").read_text()
        self.assertIn("First-pass doctrine", text)
        self.assertIn("#963", text)
        self.assertIn("pre-flight", text)

    def test_trigger_row_fires_on_handoff(self):
        root = Path(__file__).resolve().parent.parent
        conf = (root / "hooks/situational-triggers.conf").read_text()
        for line in conf.splitlines():
            if line.startswith("autonomous-quality-discipline-deep") and "Bash" in line:
                self.assertIn("airuleset\\.py handoff", line)
                return
        self.fail("autonomous-quality-discipline-deep Bash row not found")


if __name__ == "__main__":
    main()
