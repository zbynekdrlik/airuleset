"""#1315 — the per-user quota ceilings must fit the disk TOGETHER.

subdev (13.11.2026, 95 %): every shared-stream account was capped at
``used + 50 % of the free space``, computed per user, so 18 accounts were
promised ~18 x half of the free space and no account hit its quota before the
disk filled. The bound is now an equal share: ``free * 50 % / N`` for the N
shared-stream homes, so the headroom granted to ALL accounts is at most half
of the free space.

Runs the rendered hourly ceiling script under STATEFUL multi-user stubs
(setquota records each user's limits, repquota reports them back) — never a
real quota tool, never root.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_resource_guards as crg  # noqa: E402

G = 1024 * 1024  # 1 GiB in KiB


class TestFairShareCeiling(unittest.TestCase):

    def _setup(self, td, used, avail_kib, extra_homes=()):
        """``used`` = {user: KiB} shared-stream accounts; ``extra_homes`` are
        homes WITHOUT the shared-stream marker (never counted, never capped)."""
        log = os.path.join(td, "calls.log")
        state = os.path.join(td, "limits")
        os.makedirs(state)
        bindir = os.path.join(td, "bin")
        os.makedirs(bindir)
        for u in used:
            home = os.path.join(td, "home", u, ".claude")
            os.makedirs(home)
            with open(os.path.join(home, "airuleset-box-class"), "w") as f:
                f.write("shared-stream\n")
        for u in extra_homes:
            os.makedirs(os.path.join(td, "home", u, ".claude"))
        rows = "".join(
            'lim=$(cat %s/%s 2>/dev/null || echo "0 0"); '
            'echo "%s  --  %d $lim  0 0 0"\n' % (state, u, u, kib)
            for u, kib in used.items())
        stubs = {
            "setquota": ('echo "setquota $*" >> %s\n'
                         '[ "$1" = -u ] && echo "$3 $4" > %s/"$2"\nexit 0'
                         % (log, state)),
            "repquota": rows,
            "df": 'echo "  Avail"; echo "  %d"' % avail_kib,
            "systemctl": 'echo "systemctl $*" >> %s' % log,
        }
        for name, body in stubs.items():
            p = os.path.join(bindir, name)
            with open(p, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(p, 0o755)
        aq = os.path.join(td, "aquota.user")
        open(aq, "w").close()
        script = crg.render_quota_ceiling_script()
        script = script.replace("/aquota.user", aq)
        script = script.replace("/home/*", "%s/home/*" % td)
        script = script.replace(crg.QUOTA_LOCK_PATH, os.path.join(td, "q.lock"))
        script = script.replace("flock -w %d" % crg.QUOTA_LOCK_WAIT_S, "flock -w 1")
        path = os.path.join(td, "ceiling.sh")
        with open(path, "w") as f:
            f.write(script)
        env = dict(os.environ)
        env["PATH"] = bindir + ":" + env.get("PATH", "")
        r = subprocess.run(["bash", path], env=env, capture_output=True,
                           text=True, timeout=30)
        hard = {}
        for ln in open(log).read().splitlines():
            parts = ln.split()
            if parts[:2] == ["setquota", "-u"]:
                hard[parts[2]] = (int(parts[3]), int(parts[4]))
        return r, hard

    def test_granted_headroom_sums_to_at_most_half_the_free_space(self):
        """The subdev shape: one heavy account, several small ones, little
        free space. The old per-user bound granted each one half the free
        space (5 x 7G = 35G on 14G free)."""
        used = {"david3": 40 * G, "montalu1": 18 * G, "montalu2": 3 * G,
                "marek": 1 * G, "miva1": 2 * G}
        avail = 14 * G
        with tempfile.TemporaryDirectory() as td:
            r, lim = self._setup(td, used, avail)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sorted(lim), sorted(used))
            headroom = sum(lim[u][1] - used[u] for u in used)
            self.assertLessEqual(headroom, avail * 50 // 100)
            share = avail * 50 // 100 // len(used)
            for u in used:
                self.assertEqual(lim[u][1], used[u] + share, u)
                self.assertLessEqual(lim[u][0], lim[u][1], u)
            self.assertIn("david3 verified", r.stdout)

    def test_share_never_exceeds_the_usage_formula(self):
        """Plenty of free space: the share is above used*1.2 / the 10G floor,
        so the old usage-aware formula still decides."""
        used = {"a": 20 * G, "b": 1 * G}
        with tempfile.TemporaryDirectory() as td:
            r, lim = self._setup(td, used, 1024 * G)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(lim["a"], ((20 * G * 110 + 99) // 100,
                                        (20 * G * 120 + 99) // 100))
            self.assertEqual(lim["b"], (8 * G, 10 * G))

    def test_only_shared_stream_homes_count(self):
        """A home without the shared-stream marker is neither capped nor a
        share holder (root's / a workstation account's space is not quota'd)."""
        used = {"a": 5 * G, "b": 5 * G}
        avail = 8 * G
        with tempfile.TemporaryDirectory() as td:
            r, lim = self._setup(td, used, avail, extra_homes=("x", "y", "z"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            share = avail * 50 // 100 // 2
            self.assertEqual(lim["a"][1], 5 * G + share)
            self.assertEqual(lim["b"][1], 5 * G + share)

    def test_math_stays_in_the_one_limits_block(self):
        """The share is part of the ONE renderer every root writer embeds."""
        blk = crg._render_quota_limits_block()
        for writer in (crg.render_quota_ceiling_script(),
                       crg.render_quota_refresh_script(),
                       crg._render_quota_apply_block()):
            self.assertIn(blk, writer)
        self.assertIn("fs_share_n", blk)


if __name__ == "__main__":
    unittest.main()
