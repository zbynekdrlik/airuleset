"""Base-stream rename repo-side prep (#537).

Owner directive (2026-08-18, on #532): the unnumbered base streams violate the
`<name><N>` convention — `montalu` -> `montalu1`, `david` -> `david1`,
`simap` -> `simap1`. `marek` deliberately does NOT rename (owner: stays marek,
possible future removal is out of scope).

This worker does the REPO-SIDE PREP only (the live unix rename runs later, in a
per-stream quiet window the supervisor coordinates). Locked here:

  * `STREAM_RENAME_ALIASES` — the ONE explicit old->new table (marek absent).
  * `_stream_rename_equivalents()` — the symmetric (old<->new) expansion
    primitive both cli_quals consumers share.
  * `_slice_quals()` matches via alias in BOTH directions (a box on the new
    name still sees the old `stream:<old>` tickets, and vice versa) — old
    tickets keep working during transition with ZERO GitHub relabel.
  * `_ticket_is_stream_labeled()` recognises an alias label even after the old
    key is eventually removed from AUTHORITY_BY_USER (the live-op end state).
  * `AUTHORITY_BY_USER` carries the NEW names with the SAME profile as the base
    (added ALONGSIDE the old — old stays until the live rename lands).
  * `REMOTE_HOSTS` carries the NEW names flagged `"pending": True`, and
    `_deployable_hosts()` filters them out of EVERY ssh path (deploy loop +
    soniox) so push never strikes a not-yet-existent account (fail2ban).
  * the C2 false-empty guard tolerates a multi-label (aliased) shared slice.
"""

import os
import sys
import tempfile
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_remote  # noqa: E402


RENAMES = (("montalu", "montalu1"), ("david", "david1"), ("simap", "simap1"))


class TestRenameAliasTable(TestCase):
    def test_table_is_exactly_the_three_base_renames(self):
        self.assertEqual(
            airuleset.STREAM_RENAME_ALIASES,
            {"montalu": "montalu1", "david": "david1", "simap": "simap1"})

    def test_marek_is_absent_as_key_and_as_value(self):
        # Owner: marek stays marek. It must never appear on EITHER side of the
        # rename table (no `marek` key, no `marek1` value).
        self.assertNotIn("marek", airuleset.STREAM_RENAME_ALIASES)
        self.assertNotIn("marek", airuleset.STREAM_RENAME_ALIASES.values())
        self.assertNotIn("marek1", airuleset.STREAM_RENAME_ALIASES.values())

    def test_values_are_the_base_name_plus_a_1_suffix(self):
        for old, new in airuleset.STREAM_RENAME_ALIASES.items():
            self.assertEqual(new, old + "1", old)


class TestStreamRenameEquivalents(TestCase):
    def test_old_name_expands_to_old_and_new(self):
        for old, new in RENAMES:
            eq = airuleset._stream_rename_equivalents(old)
            self.assertEqual(eq[0], old, "name itself must come first")
            self.assertIn(old, eq)
            self.assertIn(new, eq)

    def test_new_name_expands_to_new_and_old_symmetric(self):
        for old, new in RENAMES:
            eq = airuleset._stream_rename_equivalents(new)
            self.assertEqual(eq[0], new, "name itself must come first")
            self.assertIn(new, eq)
            self.assertIn(old, eq)

    def test_a_non_renamed_stream_expands_to_only_itself(self):
        for u in ("marek", "montalu2", "montalu5", "david2", "miva1"):
            self.assertEqual(airuleset._stream_rename_equivalents(u), [u], u)

    def test_no_duplicate_names_in_the_expansion(self):
        for old, new in RENAMES:
            for name in (old, new):
                eq = airuleset._stream_rename_equivalents(name)
                self.assertEqual(len(eq), len(set(eq)), name)


class TestAuthorityCoversNewNames(TestCase):
    def test_new_names_carry_the_same_profile_as_the_base(self):
        for old, new in RENAMES:
            self.assertIn(new, airuleset.AUTHORITY_BY_USER, new)
            self.assertEqual(
                airuleset.AUTHORITY_BY_USER[new],
                airuleset.AUTHORITY_BY_USER[old],
                "%s must inherit %s's authority profile" % (new, old))

    def test_old_names_still_present_added_alongside_not_replaced(self):
        for old, _new in RENAMES:
            self.assertIn(old, airuleset.AUTHORITY_BY_USER, old)

    def test_marek_authority_unchanged_and_no_marek1(self):
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "branch-merge")
        self.assertNotIn("marek1", airuleset.AUTHORITY_BY_USER)


class TestSliceQualsAliasBothDirections(TestCase):
    """A shared-account (gh login == maintainer) stream's slice is
    label-only; a rename stream's slice must carry BOTH the old and the new
    `stream:` label so tickets survive the transition regardless of which
    name the box currently runs as."""

    def test_shared_account_rename_stream_carries_both_labels(self):
        with m.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            for name in ("montalu", "montalu1"):
                q = airuleset._slice_quals(name)
                self.assertIn("label:stream:montalu", q, name)
                self.assertIn("label:stream:montalu1", q, name)

    def test_own_account_rename_stream_carries_both_labels_plus_identity(self):
        with m.patch.object(airuleset, "_gh_login", return_value="kvaskodev"):
            for name in ("david", "david1"):
                q = airuleset._slice_quals(name)
                self.assertIn("assignee:@me", q, name)
                self.assertIn("author:@me", q, name)
                self.assertIn("label:stream:david", q, name)
                self.assertIn("label:stream:david1", q, name)

    def test_simap_rename_stream_carries_both_labels(self):
        with m.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            for name in ("simap", "simap1"):
                q = airuleset._slice_quals(name)
                self.assertIn("label:stream:simap", q, name)
                self.assertIn("label:stream:simap1", q, name)

    def test_a_non_renamed_shared_stream_stays_single_label(self):
        # montalu2..8 are NOT renamed — their slice must stay exactly one label
        # (no alias sprawl onto unrelated streams).
        with m.patch.object(airuleset, "_gh_login", return_value="zbynekdrlik"):
            self.assertEqual(airuleset._slice_quals("montalu2"),
                             ["label:stream:montalu2"])


class TestTicketIsStreamLabeledAlias(TestCase):
    def _labels(self, *names):
        return [{"name": n} for n in names]

    def test_new_name_label_is_recognised(self):
        self.assertTrue(
            airuleset._ticket_is_stream_labeled(self._labels("stream:montalu1")))

    def test_old_name_label_is_recognised(self):
        self.assertTrue(
            airuleset._ticket_is_stream_labeled(self._labels("stream:montalu")))

    def test_old_label_survives_removal_of_old_key_from_authority(self):
        # The live-op END state removes the OLD names from AUTHORITY_BY_USER,
        # keeping only montalu1/david1/simap1. Historical tickets still carry
        # `stream:montalu` — the alias must keep recognising them as
        # stream-owned so they never fall into the full-authority CORE slice.
        only_new = {"montalu1": "branch-merge", "david1": "fork-no-merge",
                    "simap1": "fork-no-merge"}
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", only_new):
            self.assertTrue(
                airuleset._ticket_is_stream_labeled(
                    self._labels("stream:montalu")))
            self.assertTrue(
                airuleset._ticket_is_stream_labeled(
                    self._labels("stream:david")))

    def test_a_non_stream_label_is_not_recognised(self):
        self.assertFalse(
            airuleset._ticket_is_stream_labeled(self._labels("bug", "prio:bounce")))


class TestPendingRemoteHostsAreRegisteredButNeverSshd(TestCase):
    def _pending_entries(self):
        return {h["user"]: h for h in airuleset.REMOTE_HOSTS if h.get("pending")}

    def test_new_names_are_registered_as_pending_targets(self):
        pending = self._pending_entries()
        for _old, new in RENAMES:
            self.assertIn(new, pending, new)
            self.assertEqual(pending[new]["host"], "100.118.174.27", new)

    def test_montalu1_uses_the_default_key_like_montalu(self):
        pending = self._pending_entries()
        self.assertNotIn("identity", pending["montalu1"])

    def test_david1_and_simap1_use_the_operator_identity(self):
        pending = self._pending_entries()
        for new in ("david1", "simap1"):
            self.assertEqual(pending[new].get("identity"),
                             "~/.secrets/gatekeeper_access_ed25519", new)

    def test_deployable_hosts_excludes_every_pending_entry(self):
        deployable = airuleset._deployable_hosts()
        names = {h["user"] for h in deployable}
        for _old, new in RENAMES:
            self.assertNotIn(new, names, new)
        # the base (live) names are still deployable
        for old, _new in RENAMES:
            self.assertIn(old, names, old)

    def test_soniox_provisioning_never_ssh_a_pending_account(self):
        # A pending montalu1 IS in AUTHORITY_BY_USER, so it would pass the
        # stream-account filter — the pending filter is what keeps it out of
        # every ssh (a password attempt against a non-existent account is a
        # fail2ban strike, #341/#300/#326).
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("SONIOX_API_KEY=FAKE-KEY-NEVER-REAL\n")
        hosts = [
            {"name": "montalu@subdev", "host": "9.9.9.9", "user": "montalu",
             "repo_path": "~/devel/airuleset"},
            {"name": "montalu1@subdev", "host": "9.9.9.9", "user": "montalu1",
             "repo_path": "~/devel/airuleset", "pending": True},
        ]
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            {"montalu": "branch-merge", "montalu1": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(hosts=hosts, run=run, source=src)
        joined = " ".join(str(a) for a in calls)
        self.assertNotIn("montalu1@9.9.9.9", joined,
                         "a pending account must never be ssh'd")
        self.assertIn("montalu@9.9.9.9", joined,
                      "the live base account must still be provisioned")


class TestC2GuardWithAliasedSlice(TestCase):
    """The C2 false-empty guard used to key on `len(quals) == 1`. The
    symmetric alias makes a shared-account slice carry TWO labels, so the
    guard is generalised: refuse an empty slice only when NONE of its labels
    exists on the repo — an existing `stream:montalu` (with a transitionally
    absent `stream:montalu1`) makes a genuine 0 trustworthy, not refused."""

    def test_empty_slice_trustworthy_when_at_least_one_alias_label_exists(self):
        quals = ["label:stream:montalu", "label:stream:montalu1"]

        def label_exists(label, cwd=None):
            return label == "stream:montalu"  # old exists, new not yet created

        with m.patch.object(airuleset, "_label_exists_on_repo",
                            side_effect=label_exists), \
                m.patch.object(airuleset, "_search_index_healthy",
                               return_value=True):
            # Must NOT raise SystemExit — a real 0 resting on an existing label.
            airuleset._refuse_unless_empty_is_trustworthy("slice-quals", quals)

    def test_empty_slice_refused_when_no_alias_label_exists(self):
        quals = ["label:stream:montalu", "label:stream:montalu1"]

        def label_exists(label, cwd=None):
            return False  # neither exists — a 0 here is unreliable

        with m.patch.object(airuleset, "_label_exists_on_repo",
                            side_effect=label_exists):
            with self.assertRaises(SystemExit):
                airuleset._refuse_unless_empty_is_trustworthy("slice-quals", quals)


if __name__ == "__main__":
    main()
