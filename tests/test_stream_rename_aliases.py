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

import sys
import tempfile
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


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
    # Transition-aware (runbook-537 step 8): pre-rename the OLD name stays in
    # AUTHORITY_BY_USER alongside the new; once the live rename lands (simap,
    # 2026-08-18) the old OS account + its row are gone. Profiles pinned per
    # base so the check survives the old key's removal.
    BASE_PROFILE = {"montalu": "branch-merge", "david": "fork-no-merge",
                    "simap": "fork-no-merge"}

    def _old_still_live(self, old):
        return any(h["user"] == old for h in airuleset.REMOTE_HOSTS)

    def test_new_names_carry_the_base_profile(self):
        for old, new in RENAMES:
            self.assertIn(new, airuleset.AUTHORITY_BY_USER, new)
            self.assertEqual(airuleset.AUTHORITY_BY_USER[new],
                             self.BASE_PROFILE[old],
                             "%s must carry %s's profile" % (new, old))

    def test_old_names_tracked_with_their_remote_hosts_lifecycle(self):
        for old, _new in RENAMES:
            if self._old_still_live(old):
                self.assertIn(old, airuleset.AUTHORITY_BY_USER, old)
            else:
                self.assertNotIn(
                    old, airuleset.AUTHORITY_BY_USER,
                    "%s renamed away — stale AUTHORITY_BY_USER row" % old)

    def test_marek_removed_from_authority_882(self):
        # marek decommissioned #882 — no longer in AUTHORITY_BY_USER
        self.assertNotIn("marek", airuleset.AUTHORITY_BY_USER)
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
    """The renames land ONE STREAM AT A TIME (runbook-537, per-stream quiet
    windows), so per stream exactly ONE of two states holds — and this test
    is transition-aware rather than locking the pre-rename snapshot (the
    original shape asserted every numbered entry pending, which went stale
    the moment the first live rename landed — simap1, 2026-08-18):

    - PRE-rename:  <new>@subdev registered with `pending: True` (never
      ssh'd), <old>@subdev live/deployable.
    - POST-rename: <new>@subdev live/deployable (no `pending`), <old>@subdev
      GONE entirely (the OS account no longer exists).
    - POST-rename + PAUSED (#851): <new>@subdev registered, <old>@subdev
      gone, but `paused: "<why>"` excludes it from every ssh path anyway
      (e.g. simap1@subdev, owner directive 2026-09-02).
    Never both live, never both gone, and a pending OR paused entry is
    never ssh'd."""

    def _by_user(self):
        return {h["user"]: h for h in airuleset.REMOTE_HOSTS}

    def test_each_rename_is_in_exactly_one_valid_transition_state(self):
        hosts = self._by_user()
        deployable = {h["user"] for h in airuleset._deployable_hosts()}
        for old, new in RENAMES:
            self.assertIn(new, hosts, new)
            self.assertEqual(hosts[new]["host"], "100.118.174.27", new)
            if hosts[new].get("pending"):
                # PRE-rename: old account still live and deployable.
                self.assertIn(old, hosts, old)
                self.assertIn(old, deployable, old)
                self.assertNotIn(new, deployable,
                                 "a pending account must never be ssh'd")
            elif hosts[new].get("paused"):
                # POST-rename, but PAUSED (#851, e.g. simap1@subdev): old
                # entry is gone (the live rename landed), but the new entry
                # is deliberately excluded from every automatic ssh path
                # until the owner deletes the `paused` flag.
                self.assertNotIn(old, hosts,
                                 "%s renamed away — stale target" % old)
                self.assertNotIn(new, deployable,
                                 "a paused account must never be ssh'd")
            else:
                # POST-rename: numbered entry live, old entry gone.
                self.assertNotIn(old, hosts,
                                 "%s renamed away — stale target" % old)
                self.assertIn(new, deployable, new)

    def test_montalu1_uses_the_default_key_like_montalu(self):
        self.assertNotIn("identity", self._by_user()["montalu1"])

    def test_david1_and_simap1_use_the_operator_identity(self):
        hosts = self._by_user()
        for new in ("david1", "simap1"):
            self.assertEqual(hosts[new].get("identity"),
                             "~/.secrets/gatekeeper_access_ed25519", new)

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


class TestFleetBurnSkipsPendingHosts(TestCase):
    """#537 review 🔴: the pending filter must cover the FLEET-BURN ssh path
    (watchdog job 16, hourly on dev1) and the burn/delegation `--host all`
    paths too — `_deployable_hosts()` must guard EVERY REMOTE_HOSTS ssh
    consumer, not only the deploy loop + soniox. A pending account does not
    exist, so an hourly ssh to it is the exact fail2ban strike the flag exists
    to prevent."""

    def _hosts(self):
        return [
            {"name": "montalu@subdev", "host": "9.9.9.9", "user": "montalu",
             "repo_path": "~/devel/airuleset"},
            {"name": "montalu1@subdev", "host": "9.9.9.9", "user": "montalu1",
             "repo_path": "~/devel/airuleset", "pending": True},
        ]

    def test_watchdog_fleet_fetch_never_rows_a_pending_host(self):
        import cli_burn
        rowed = []

        def fake_row(remote, want_hour_bucket, timeout=15):
            rowed.append(remote["name"])
            return {"error": "stub"}

        with m.patch.object(cli_burn, "_fleet_remote_row", side_effect=fake_row):
            result = airuleset._watchdog_fleet_fetch(self._hosts(), want_hour_bucket=123)
        self.assertIn("montalu@subdev", rowed)
        self.assertNotIn(
            "montalu1@subdev", rowed,
            "a pending account must never be ssh'd by the hourly fleet-burn job")
        self.assertNotIn("montalu1@subdev", result)

    def test_watchdog_fleet_fetch_default_excludes_pending_from_real_registry(self):
        # hosts=None must resolve to REMOTE_HOSTS MINUS pending (montalu1 etc.).
        import cli_burn
        rowed = []

        def fake_row(remote, want_hour_bucket, timeout=15):
            rowed.append(remote["user"])
            return {"error": "stub"}

        with m.patch.object(cli_burn, "_fleet_remote_row", side_effect=fake_row):
            airuleset._watchdog_fleet_fetch(hosts=None, want_hour_bucket=123)
        # Derive the still-pending set live — a landed rename (simap1,
        # 2026-08-18) moves its numbered name out of pending, and this test
        # must keep holding for the remaining transitions without relock.
        still_pending = {h["user"] for h in airuleset.REMOTE_HOSTS
                         if h.get("pending")}
        self.assertTrue(
            still_pending.issubset({new for _o, new in RENAMES}),
            "unexpected pending entries: %s" % still_pending)
        for pending in still_pending:
            self.assertNotIn(pending, rowed, pending)


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


class TestCoreSearchExclAliasExpansion(TestCase):
    """#561: `_core_search_excl()` is the THIRD `_stream_rename_equivalents()`
    consumer the #537 staging missed. It builds the full-authority CORE
    gh-search exclusion straight from AUTHORITY_BY_USER keys, so after the live
    rename removed the OLD `montalu`/`simap` keys (their OS accounts are gone)
    it stopped excluding the OLD `stream:montalu`/`stream:simap` labels the
    odoo-erp tickets still carry (live gk audit 2026-08-19: 69 open
    `stream:montalu`, 0 `stream:montalu1`), leaking ~50 tickets into gk
    core-quals / footer `I` / the /goal stop-proof / the Discord card.

    Patched to the post-rename END state (only-new keys) so the test is
    hermetic and survives the live registry evolving, mirroring
    `TestTicketIsStreamLabeledAlias.test_old_label_survives_removal_of_old_key
    _from_authority` above."""

    ONLY_NEW = {"montalu1": "branch-merge", "simap1": "fork-no-merge",
                "david1": "fork-no-merge", "marek": "branch-merge",
                "montalu2": "branch-merge"}

    def test_new_names_are_still_excluded(self):
        # Exact-token membership (`excl.split()`), never substring `in excl`:
        # `-label:stream:montalu` is a SUBSTRING of `-label:stream:montalu1`,
        # so a substring assertion would pass for the wrong reason.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            toks = airuleset._core_search_excl().split()
        for name in ("montalu1", "simap1", "david1", "marek", "montalu2"):
            self.assertIn("-label:stream:%s" % name, toks, name)

    def test_legacy_alias_labels_are_also_excluded(self):
        # THE #561 regression: the OLD labels the repo still carries must be
        # excluded too, via the SAME `_stream_rename_equivalents()` primitive
        # `_slice_quals`/`_ticket_is_stream_labeled` already use — never a
        # second parallel alias table. Exact-token membership: `montalu` is a
        # prefix of `montalu1`, so a substring `in` would falsely pass.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            toks = airuleset._core_search_excl().split()
        self.assertIn("-label:stream:montalu", toks)   # FAILS before the fix
        self.assertIn("-label:stream:simap", toks)     # FAILS before the fix

    def test_a_full_profile_entry_is_still_never_excluded(self):
        # M-5 invariant preserved: a `full` entry is not a sub-dev stream, and
        # excluding its label would remove a whole population from every count.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            {"david": "fork-no-merge", "boss": "full"}):
            toks = airuleset._core_search_excl().split()
        self.assertIn("-label:stream:david", toks)
        self.assertNotIn("-label:stream:boss", toks)

    def test_output_is_deduped_and_sorted_deterministic(self):
        # `david` AND `david1` are BOTH keys pre-rename and each expands to the
        # SAME pair, so a naive nested comprehension would emit duplicate
        # `-label:stream:david`/`-label:stream:david1` fragments. Collect into a
        # set, sorted-join — no duplicate tokens, deterministic order.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            {"david": "fork-no-merge", "david1": "fork-no-merge"}):
            excl = airuleset._core_search_excl()
        toks = excl.split()
        self.assertEqual(len(toks), len(set(toks)), "duplicate exclusion terms: %r" % excl)
        self.assertEqual(toks, sorted(toks), "non-deterministic order: %r" % excl)


def _gh_filtered(items):
    """A `_gh_out` stand-in with REAL `-label:` exclusion semantics (a mirror
    of `authority_testlib._fake_gh_search_filtered`, inlined so this file
    stays self-contained): `-label:X` genuinely REMOVES an item carrying X, so
    it can prove that an exclusion term added to the built query actually drops
    a ticket — a pure substring-inclusion fake cannot."""
    import json as _json

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        matched = []
        for it in items:
            labels = it.get("labels") or set()
            ok = True
            for tok in search.split():
                if tok.startswith("-label:"):
                    names = tok[len("-label:"):].split(",")
                    if any(n in labels for n in names):
                        ok = False
                        break
                elif tok.startswith("label:"):
                    names = tok[len("label:"):].split(",")
                    if not any(n in labels for n in names):
                        ok = False
                        break
            if ok:
                matched.append(it)
        if "-q" in args:
            return str(len(matched))
        return _json.dumps([
            {"number": it["number"], "title": "t%d" % it["number"],
             "createdAt": "2026-01-01T00:00:00Z",
             "labels": [{"name": n} for n in sorted(it.get("labels") or [])]}
            for it in matched])

    return gh


class TestCoreQualsCountExcludesLegacyStreamTicket(TestCase):
    """#561 end-to-end reproduction of the live 5->61 regression, driven
    through the actual `cmd_core_quals` count path (not just the derivation),
    so it fails when the COUNT leaks — the number the /goal stop-proof reads."""

    ONLY_NEW = {"montalu1": "branch-merge", "simap1": "fork-no-merge",
                "david1": "fork-no-merge", "marek": "branch-merge"}

    def _count(self):
        import contextlib
        import io
        items = [
            {"number": 11, "labels": set()},                  # genuine gk core work
            {"number": 3062, "labels": {"stream:montalu"}},   # renamed-away label
        ]
        gh = _gh_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            with m.patch.object(airuleset, "resolve_authority", return_value="full"):
                with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_core_quals(m.Mock(
                            count=True, list=False, waiting=False,
                            ops_wait=False, audit=False, extra=None))
        return buf.getvalue().strip()

    def test_a_stream_montalu_ticket_is_not_counted_into_gk_core(self):
        self.assertEqual(
            self._count(), "1",
            "a renamed-away stream:montalu ticket leaked into the gk core "
            "count — the exact 5->61 regression #561 fixes")


class TestStreamOwnerOfAlias(TestCase):
    """#561: `_stream_owner_of()` — the `implement`/`action-only` row
    discriminator (`_row_action`, the issue's exact 'appear as implement rows'
    evidence) and the re-adoption filter — must recognise a legacy
    `stream:<old>` label, or a `stream:montalu`+`needs-gatekeeper` hand-off
    (odoo-erp #2396/#2377, legitimately in the obligation UNION) would render
    as `implement` and invite the gatekeeper to write montalu's code."""

    ONLY_NEW = {"montalu1": "branch-merge", "david1": "fork-no-merge"}

    def test_legacy_label_resolves_to_the_canonical_renamed_stream(self):
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            # returns the AUTHORITY_BY_USER KEY (current canonical name), so a
            # montalu1 box's own old-labeled ticket matches its own_stream and
            # still reads `implement`, while gk (own_stream="") reads action-only.
            self.assertEqual(
                airuleset._stream_owner_of([{"name": "stream:montalu"}]),
                "montalu1")                                    # "" before the fix
            self.assertEqual(
                airuleset._stream_owner_of([{"name": "stream:montalu1"}]),
                "montalu1")

    def test_a_non_stream_label_is_not_owned(self):
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            self.assertEqual(
                airuleset._stream_owner_of([{"name": "bug"}]), "")

    def test_a_full_profile_entry_is_never_owned(self):
        # M-5 for the OWNERSHIP path (review A 🔵): a `profile == "full"` entry
        # is not a sub-dev stream — its label must NEVER read as ownership, or
        # its tickets would wrongly become untouchable. Mirrors the same guard
        # `test_a_full_profile_entry_is_still_never_excluded` locks for
        # `_core_search_excl`.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            {"montalu1": "branch-merge", "boss": "full"}):
            self.assertEqual(
                airuleset._stream_owner_of([{"name": "stream:boss"}]), "")
            self.assertEqual(
                airuleset._stream_owner_of([{"name": "stream:montalu"}]),
                "montalu1")

    def test_a_row_with_a_legacy_stream_handoff_is_marked_action_only(self):
        # End-to-end through `cmd_core_quals --list`: a stream:montalu +
        # needs-gatekeeper ticket surfaces via the obligation union and MUST be
        # `action-only` (gk reviews it, never implements). `implement` before fix.
        import contextlib
        import io
        items = [
            {"number": 11, "labels": set()},                            # core -> implement
            {"number": 2396, "labels": {"stream:montalu", "needs-gatekeeper"}},
        ]
        gh = _gh_filtered(items)
        buf = io.StringIO()
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", self.ONLY_NEW):
            with m.patch.object(airuleset, "resolve_authority", return_value="full"):
                with m.patch.object(airuleset, "_gh_out", side_effect=gh):
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_core_quals(m.Mock(
                            count=False, list=True, waiting=False,
                            ops_wait=False, audit=False, extra=None))
        out = buf.getvalue()
        row = [ln for ln in out.splitlines() if ln.startswith("2396\t")]
        self.assertTrue(row, out)
        self.assertIn("action-only", row[0])
        core = [ln for ln in out.splitlines() if ln.startswith("11\t")]
        self.assertTrue(core, out)
        self.assertIn("implement", core[0])


class TestBounceQualsFullAuthorityAlias(TestCase):
    """#561: the watchdog full-authority (dev1) bounce-nudge exclusion
    (`_bounce_quals`, cross_stream.py:108) is the SAME `stream:%s`-from-a-stream-
    registry leak class — after the rename `_REDUCED_STREAM_USERS` carries
    `montalu1` but not `montalu`, so dev1's job-8 query would NOT exclude an
    old `stream:montalu` bounce ticket and nudge the wrong person about it."""

    def test_full_authority_exclusion_covers_the_legacy_alias(self):
        import watchdog as wd
        quals = wd._bounce_quals("/home/newlevel/devel/odoo-erp")
        self.assertEqual(len(quals), 1)
        toks = quals[0].split()  # exact-token: `montalu` is a prefix of `montalu1`
        self.assertIn("-label:stream:montalu1", toks)
        self.assertIn("-label:stream:montalu", toks)       # FAILS before the fix

    def test_a_reduced_box_own_slice_expands_to_equivalents(self):
        # #564 (INVERTS the #537-locked own-slice narrowing): a montalu1 box's
        # OWN bounce slice must ALSO cover the legacy `stream:montalu` label so
        # it does not under-nudge itself about its own old-labeled bounce
        # tickets during the transition. #537 deliberately left this narrowed
        # ("own-slice narrowing is a separate concern, not a gk leak"); issue 564
        # item 2 is precisely that separate concern, routed through the SAME
        # _stream_rename_equivalents primitive as the full-authority branch. The
        # quals are UNIONED per-qual by _fetch_bounce_tickets, so an extra
        # alias label never narrows the result. RED before the fix.
        import watchdog as wd
        self.assertEqual(wd._bounce_quals("/home/montalu1/devel/odoo-erp"),
                         ["label:stream:montalu1", "label:stream:montalu"])

    def test_a_non_renamed_reduced_box_own_slice_is_unchanged(self):
        # #564: a stream NOT involved in any rename (montalu2) expands to just
        # itself, so its own slice is byte-identical to before.
        import watchdog as wd
        self.assertEqual(wd._bounce_quals("/home/montalu2/devel/odoo-erp"),
                         ["label:stream:montalu2"])


if __name__ == "__main__":
    main()
