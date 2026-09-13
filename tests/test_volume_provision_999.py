"""#999 — managed provisioning krok `volume` (fstab mount by-id + relocation).

Owner directive (2026-09-12): the attached-but-unmounted 20 GB Hetzner volume
`gk-vol1` is used to FREE local disk on the gk box by relocating the 7
self-hosted runners (`/home/gh-runner`), docker `data-root` (`/var/lib/docker`)
and `~/.cache` onto it — the same provisioning layer as `provision_swap`
(#993, `cli_disk_guard_root.py`): a per-box `volume` declaration in the fleet
record drives an idempotent render → `sudo -n` apply → status row.

Safety (owner: "NIKDY NESMIE MAINTENANCE POSKODIT beziacu robotu"): never
swapoff, never delete originals (kept as `.relocated-<date>`), runners ONE AT A
TIME (abort on a failed `is-active`), docker only with 0 running containers,
fstab `nofail`, idempotent (a second apply is all no-op lines).

Tests execute the RENDERED bash under a temp root with STUB commands
(findmnt/mount/systemctl/rsync/docker) — no real fs/systemd is touched.
"""

import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_disk_guard_root as dg  # noqa: E402
import cli_fleet  # noqa: E402

GK_DECL = {
    "by_id": "scsi-0HC_Volume_106853757",
    "mount": "/mnt/gk-vol1",
    "relocate": ["/home/gh-runner", "/var/lib/docker", "~/.cache"],
}


# --------------------------------------------------------------------------- #
# Fake `run` recorder (same shape as tests/test_swap_provision_993.py).
# --------------------------------------------------------------------------- #
class _Rec:
    def __init__(self, rules=None):
        self.rules = rules or []      # list of (substr, rc, stdout)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        joined = " ".join(argv)
        for sub, rc, out in self.rules:
            if sub in joined:
                return _R(rc, out)
        return _R(0, "")

    def ran(self, substr):
        return any(substr in " ".join(a) for a in self.calls)


class _R:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


# --------------------------------------------------------------------------- #
# Execution harness — run the rendered bash with stub commands under a root.
# --------------------------------------------------------------------------- #
def _stub_bin(d, *, isactive_fail="", docker_ps="", docker_root="", rsync_fail=""):
    b = os.path.join(d, "bin")
    os.makedirs(b, exist_ok=True)
    log = os.path.join(d, "calls.log")

    def w(name, body):
        p = os.path.join(b, name)
        with open(p, "w") as f:
            f.write("#!/bin/bash\n" + body)
        os.chmod(p, 0o755)

    w("findmnt", "exit 0\n")  # always "mounted" so relocation proceeds
    w("mount", 'echo "mount $*" >> "%s"\nexit 0\n' % log)
    # rsync_fail: a substring; a matching rsync argv exits 23 (partial xfer)
    # AFTER logging — to model a copy that dies mid-relocation.
    w("rsync", (
        'echo "rsync $*" >> "%s"\n'
        'if [ -n "%s" ]; then case "$*" in *%s*) exit 23;; esac; fi\n'
        'mkdir -p "${@: -1}"\n'
        'exit 0\n' % (log, rsync_fail, rsync_fail)
    ))
    w("systemctl", (
        'echo "systemctl $*" >> "%s"\n'
        'if [ "$1" = "is-active" ]; then\n'
        '  if [ -n "%s" ]; then case "$*" in *%s*) exit 3;; esac; fi\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n' % (log, isactive_fail, isactive_fail)
    ))
    w("docker", (
        'echo "docker $*" >> "%s"\n'
        'if [ "$1" = "ps" ]; then printf "%%s" "%s"; [ -n "%s" ] && echo; exit 0; fi\n'
        'if [ "$1" = "info" ]; then echo "%s"; exit 0; fi\n'
        'exit 0\n' % (log, docker_ps, docker_ps, docker_root)
    ))
    return b, log


def _seed_root(root, home, n_runners=3):
    os.makedirs(root + "/etc", exist_ok=True)
    with open(root + "/etc/fstab", "w") as f:
        f.write("# fstab\nUUID=x / ext4 defaults 0 1\n")
    ghr = root + "/home/gh-runner"
    for i in range(1, n_runners + 1):
        rd = ghr + "/actions-runner-%d" % i
        os.makedirs(rd, exist_ok=True)
        with open(rd + "/config.sh", "w") as f:
            f.write("x")
        with open(rd + "/.service", "w") as f:
            f.write("actions.runner.acme-repo.r%d.service\n" % i)
    dk = root + "/var/lib/docker"
    os.makedirs(dk + "/overlay2", exist_ok=True)
    with open(dk + "/x", "w") as f:
        f.write("x")
    cache = root + home + "/.cache"
    os.makedirs(cache, exist_ok=True)
    with open(cache + "/c", "w") as f:
        f.write("x")


class _Harness:
    """Render + execute the volume script under a temp root with stub cmds."""

    HOME = "/home/gatekeeper"

    def __init__(self, tc, *, isactive_fail="", docker_ps="", docker_root="",
                 rsync_fail=""):
        self.tc = tc
        self.d = tempfile.mkdtemp(prefix="voltest-")
        tc.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        self.root = os.path.join(self.d, "root")
        os.makedirs(self.root)
        _seed_root(self.root, self.HOME)
        self.binp, self.log = _stub_bin(
            self.d, isactive_fail=isactive_fail, docker_ps=docker_ps,
            docker_root=docker_root, rsync_fail=rsync_fail)
        self.script = dg.render_volume_setup_script(
            GK_DECL, home=self.HOME, root=self.root)
        self.spath = os.path.join(self.d, "script.sh")
        with open(self.spath, "w") as f:
            f.write(self.script)

    def bash_n(self):
        return subprocess.run(["bash", "-n", self.spath],
                              capture_output=True, text=True)

    def run(self):
        env = dict(os.environ)
        env["PATH"] = self.binp + ":" + env["PATH"]
        return subprocess.run(["bash", self.spath], capture_output=True,
                              text=True, env=env)

    def r(self, rel):
        return self.root + rel


# --------------------------------------------------------------------------- #
# Render — structural / token assertions (the guards, not mere substrings).
# --------------------------------------------------------------------------- #
class TestRenderTokens(TestCase):
    def setUp(self):
        self.s = dg.render_volume_setup_script(GK_DECL, home="/home/gatekeeper")

    def test_set_euo_pipefail(self):
        self.assertTrue(self.s.startswith("set -euo pipefail"))

    def test_fstab_line_shape_nofail_discard(self):
        self.assertIn(
            "/dev/disk/by-id/scsi-0HC_Volume_106853757 /mnt/gk-vol1 ext4 "
            "defaults,nofail,discard 0 2", self.s)
        # dedup guard + trailing-newline guard (same shape as the swap render)
        self.assertIn("grep -qE '^/dev/disk/by-id/scsi-0HC_Volume_106853757"
                      "[[:space:]]'", self.s)
        self.assertIn("tail -c1", self.s)

    def test_mount_guarded_by_findmnt(self):
        self.assertIn("findmnt /mnt/gk-vol1", self.s)
        self.assertIn("mount /mnt/gk-vol1", self.s)
        # aborts relocation if the volume is not mounted (never relocate onto /)
        self.assertIn("not mounted — skipping relocation", self.s)

    def test_never_deletes_originals_keeps_relocated_backup(self):
        self.assertIn(".relocated-", self.s)
        # the script never `rm`s an original directory anywhere
        self.assertNotIn("rm -rf", self.s)
        self.assertNotIn("rm -r ", self.s)

    def test_never_swapoff(self):
        self.assertNotIn("swapoff", self.s)

    def test_runner_phase_one_at_a_time_and_isactive_abort(self):
        self.assertIn("actions-runner*/", self.s)
        self.assertIn('systemctl stop "$_unit"', self.s)
        self.assertIn('systemctl start "$_unit"', self.s)
        self.assertIn('systemctl is-active --quiet "$_unit"', self.s)
        self.assertIn("aborting", self.s)
        self.assertIn("exit 1", self.s)          # abort stops the whole loop
        # backups are skipped on re-run (idempotence guard)
        self.assertIn("*.relocated-*) continue", self.s)

    def test_docker_phase_refuses_with_containers(self):
        self.assertIn("docker ps -q", self.s)
        self.assertIn("container(s) running", self.s)
        self.assertIn("data-root", self.s)
        self.assertIn("systemctl restart docker", self.s)

    def test_cache_home_expansion(self):
        self.assertIn("/home/gatekeeper/.cache", self.s)
        # relocated to <mount>/<basename>
        self.assertIn("/mnt/gk-vol1/.cache", self.s)

    def test_no_declaration_render_is_not_called(self):
        # a decl without relocate still renders the mount, no relocation phases
        s = dg.render_volume_setup_script(
            {"by_id": "x", "mount": "/mnt/v", "relocate": []},
            home="/home/u")
        self.assertIn("mount /mnt/v", s)
        self.assertNotIn("actions-runner", s)


# --------------------------------------------------------------------------- #
# Render — behavioral execution under stubs (no real fs/systemd).
# --------------------------------------------------------------------------- #
class TestRenderExecution(TestCase):
    def test_bash_n_valid_syntax(self):
        h = _Harness(self)
        syn = h.bash_n()
        self.assertEqual(syn.returncode, 0, syn.stderr)

    def test_first_run_relocates_symlinks_and_fstab(self):
        h = _Harness(self)
        r = h.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        # fstab got the nofail entry
        self.assertIn("nofail,discard", open(h.r("/etc/fstab")).read())
        # runner + cache became symlinks onto the volume
        self.assertTrue(os.path.islink(h.r("/home/gh-runner/actions-runner-1")))
        self.assertTrue(os.path.islink(h.r("/home/gatekeeper/.cache")))
        # originals preserved as .relocated-* (never deleted)
        parent = h.r("/home/gh-runner")
        self.assertTrue(any(".relocated-" in n for n in os.listdir(parent)))
        # docker data-root written to daemon.json
        dj = h.r("/etc/docker/daemon.json")
        self.assertTrue(os.path.exists(dj))
        self.assertIn("data-root", open(dj).read())

    def test_second_run_is_idempotent_noop(self):
        h = _Harness(self)
        self.assertEqual(h.run().returncode, 0)
        r2 = h.run()
        self.assertEqual(r2.returncode, 0, r2.stderr)
        # every phase reports "already relocated / already ... skip"
        self.assertIn("already relocated", r2.stdout)
        # no NEW relocation happened: any "relocated -> " line must be an
        # "already ... skip" no-op, and docker never freshly writes data-root
        for line in r2.stdout.splitlines():
            if "relocated -> " in line:
                self.assertIn("already", line,
                              "fresh relocation on 2nd run: " + line)
            self.assertNotIn("data-root ->", line)

    def test_runner_aborts_one_at_a_time_on_isactive_fail(self):
        # runner r2's service fails is-active -> abort before r3, before cache
        h = _Harness(self, isactive_fail="r2")
        r = h.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED is-active", r.stdout)
        # r1 done, r3 never touched (still a real dir, not a symlink)
        self.assertTrue(os.path.islink(h.r("/home/gh-runner/actions-runner-1")))
        self.assertFalse(os.path.islink(h.r("/home/gh-runner/actions-runner-3")))
        # cache never reached (abort stopped the whole script)
        self.assertFalse(os.path.islink(h.r("/home/gatekeeper/.cache")))

    def test_docker_refused_when_containers_running(self):
        h = _Harness(self, docker_ps="deadbeefcont")
        r = h.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIPPED", r.stdout)
        # daemon.json NOT written, docker NOT restarted
        self.assertFalse(os.path.exists(h.r("/etc/docker/daemon.json")))
        self.assertNotIn("restart docker", open(h.log).read())
        # but the (independent) runner + cache relocations still happened
        self.assertTrue(os.path.islink(h.r("/home/gatekeeper/.cache")))


# --------------------------------------------------------------------------- #
# Finding 1 (🟡, #999 adversarial review): the docker data-root migration must
# stop BOTH docker.socket and docker.service before the rsync. Stopping only
# docker.service leaves docker.socket listening, so any client touching the
# socket socket-activates (restarts) docker MID-rsync — corrupting the copy and
# violating "maintenance must never harm running work". Both are stopped before
# the copy and restarted after daemon.json is written; the "0 running
# containers" precondition is unchanged.
# --------------------------------------------------------------------------- #
class TestDockerSocketStop(TestCase):
    def test_render_stops_docker_socket_and_service(self):
        s = dg.render_volume_setup_script(GK_DECL, home="/home/gatekeeper")
        # both units stopped before the copy (socket, not just the service)
        self.assertIn("systemctl stop docker.socket", s)
        self.assertIn("systemctl stop docker.service", s)
        # both brought back after the migration
        self.assertIn("systemctl restart docker.socket docker.service", s)

    def test_socket_stopped_before_rsync_and_restarted_after(self):
        h = _Harness(self)                       # 0 containers -> docker migrates
        r = h.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = open(h.log).read().splitlines()

        def idx(pred):
            for i, ln in enumerate(lines):
                if pred(ln):
                    return i
            return -1

        rsync_i = idx(lambda ln: ln.startswith("rsync") and "/var/lib/docker/" in ln)
        self.assertGreaterEqual(rsync_i, 0, "docker rsync never ran")
        sock_stop_i = idx(lambda ln: "systemctl stop docker.socket" in ln)
        svc_stop_i = idx(lambda ln: "systemctl stop docker.service" in ln)
        self.assertGreaterEqual(sock_stop_i, 0, "docker.socket never stopped")
        self.assertGreaterEqual(svc_stop_i, 0, "docker.service never stopped")
        # both stopped BEFORE the copy (no socket-activation mid-rsync)
        self.assertLess(sock_stop_i, rsync_i)
        self.assertLess(svc_stop_i, rsync_i)
        # both restarted AFTER the copy
        restart_i = idx(lambda ln: "restart docker.socket docker.service" in ln)
        self.assertGreater(restart_i, rsync_i, "docker not restarted after copy")


# --------------------------------------------------------------------------- #
# Finding 2 (🔵, #999 adversarial review): a phase that stops a service and then
# fails mid-flight (rsync error, is-active fail) must NOT leave that service
# down — "maintenance must never harm running work". A per-phase ERR/EXIT trap
# restarts what the phase stopped and prints a recovery line. Phases stay
# independent: a failed later phase never undoes an already-completed earlier
# one.
# --------------------------------------------------------------------------- #
class TestRelocationFailureRecovery(TestCase):
    def test_docker_rsync_failure_restarts_docker(self):
        h = _Harness(self, rsync_fail="/var/lib/docker/")   # docker copy dies
        r = h.run()
        self.assertNotEqual(r.returncode, 0, "docker phase should fail")
        # loud recovery line printed
        self.assertIn("restarting docker", r.stdout)
        lines = open(h.log).read().splitlines()
        rsync_i = next((i for i, ln in enumerate(lines)
                        if ln.startswith("rsync") and "/var/lib/docker/" in ln), -1)
        self.assertGreaterEqual(rsync_i, 0, "docker rsync never ran")
        # after the failed copy, docker is started again (never left stopped)
        start_after = [i for i, ln in enumerate(lines)
                       if i > rsync_i and "systemctl start docker" in ln]
        self.assertTrue(start_after, "docker not restarted after failed copy")
        # the earlier runner phase completed — previous work is not blocked
        self.assertTrue(os.path.islink(h.r("/home/gh-runner/actions-runner-1")))

    def test_runner_rsync_failure_restarts_stopped_unit(self):
        # runner r2's copy fails after its unit was stopped
        h = _Harness(self, rsync_fail="actions-runner-2")
        r = h.run()
        self.assertNotEqual(r.returncode, 0, "runner phase should fail")
        self.assertIn("restarting", r.stdout)
        lines = open(h.log).read().splitlines()
        unit = "actions.runner.acme-repo.r2.service"
        stop_i = next((i for i, ln in enumerate(lines)
                       if ("stop " + unit) in ln), -1)
        self.assertGreaterEqual(stop_i, 0, "r2 unit never stopped")
        # r2 restarted AFTER its stop (recovery) — never left down
        start_after = [i for i, ln in enumerate(lines)
                       if i > stop_i and ("start " + unit) in ln]
        self.assertTrue(start_after, "r2 not restarted after failed copy")
        # r1 completed first; r3 never reached (abort stops the script)
        self.assertTrue(os.path.islink(h.r("/home/gh-runner/actions-runner-1")))
        self.assertFalse(os.path.islink(h.r("/home/gh-runner/actions-runner-3")))


# --------------------------------------------------------------------------- #
# _local_volume_decl — resolve this box's declaration by user==pw_name.
# --------------------------------------------------------------------------- #
class TestLocalVolumeDecl(TestCase):
    HOSTS = [
        {"name": "dev2", "user": "newlevel"},
        {"name": "gatekeeper", "user": "gatekeeper", "volume": dict(GK_DECL)},
        {"name": "montalu2", "user": "montalu2"},
    ]

    def test_matches_by_user(self):
        d = dg._local_volume_decl(hosts=self.HOSTS, user="gatekeeper")
        self.assertEqual(d["mount"], "/mnt/gk-vol1")

    def test_none_when_user_has_no_volume(self):
        self.assertIsNone(dg._local_volume_decl(hosts=self.HOSTS, user="newlevel"))

    def test_none_when_user_absent(self):
        self.assertIsNone(dg._local_volume_decl(hosts=self.HOSTS, user="nobody"))


# --------------------------------------------------------------------------- #
# provision_volume — sudo-n gate, no-decl skip, apply, non-fatal failure.
# --------------------------------------------------------------------------- #
class TestProvisionVolume(TestCase):
    def test_no_declaration_is_a_skip(self):
        rec = _Rec()
        msg = dg.provision_volume(run=rec, decl=None)
        self.assertIn("no declaration", msg.lower())
        self.assertFalse(rec.ran("bash"))

    def test_no_sudo_skips_with_clear_line(self):
        rec = _Rec([("sudo -n true", 1, "")])
        msg = dg.provision_volume(run=rec, decl=dict(GK_DECL))
        self.assertIn("sudo", msg.lower())
        self.assertIn("skip", msg.lower())
        self.assertFalse(rec.ran("bash"))

    def test_applies_with_sudo(self):
        rec = _Rec([("sudo -n true", 0, "")])
        msg = dg.provision_volume(run=rec, decl=dict(GK_DECL))
        self.assertIn("applied", msg.lower())
        self.assertIn("/mnt/gk-vol1", msg)
        self.assertTrue(any("bash" in " ".join(a) and "sudo" in " ".join(a)
                            for a in rec.calls))

    def test_failure_is_non_fatal(self):
        rec = _Rec([("sudo -n true", 0, ""), ("bash", 1, "")])
        msg = dg.provision_volume(run=rec, decl=dict(GK_DECL))
        self.assertIn("fail", msg.lower())


# --------------------------------------------------------------------------- #
# volume_status — the `airuleset.py status` row.
# --------------------------------------------------------------------------- #
class TestVolumeStatus(TestCase):
    def test_no_declaration_row(self):
        line = dg.volume_status(decl=None, run=_Rec())
        self.assertIn("volume", line.lower())
        self.assertIn("none", line.lower())

    def test_row_format_with_df(self):
        rec = _Rec([("df", 0, "Used  Size\n3.1G   20G\n")])
        line = dg.volume_status(decl=dict(GK_DECL), run=rec)
        self.assertIn("/mnt/gk-vol1", line)
        self.assertIn("3.1G/20G", line)
        self.assertIn("relocated", line)

    def test_count_relocated_injected(self):
        # 2 of 3 relocated: gh-runner (isdir target) + .cache (islink orig)
        target_dirs = {"/mnt/gk-vol1/gh-runner", "/mnt/gk-vol1/.cache"}
        links = {"/home/gatekeeper/.cache"}
        n = dg._count_relocated(
            dict(GK_DECL),
            islink=lambda p: p in links,
            isdir=lambda p: p in target_dirs,
            home="/home/gatekeeper")
        # gh-runner counts (target isdir); docker does not (target not isdir);
        # .cache counts (orig islink + target isdir) -> 2
        self.assertEqual(n, 2)


# --------------------------------------------------------------------------- #
# Fleet declaration — gatekeeper carries `volume`, other boxes do not.
# --------------------------------------------------------------------------- #
class TestFleetDeclaration(TestCase):
    def _entry(self, name):
        for h in cli_fleet.REMOTE_HOSTS:
            if h.get("name") == name:
                return h
        return None

    def test_gatekeeper_has_volume_declaration(self):
        gk = self._entry("gatekeeper")
        self.assertIsNotNone(gk)
        vol = gk.get("volume")
        self.assertIsInstance(vol, dict)
        self.assertEqual(vol["by_id"], "scsi-0HC_Volume_106853757")
        self.assertEqual(vol["mount"], "/mnt/gk-vol1")
        self.assertIn("/home/gh-runner", vol["relocate"])
        self.assertIn("/var/lib/docker", vol["relocate"])
        self.assertIn("~/.cache", vol["relocate"])

    def test_other_boxes_have_no_volume_key(self):
        others = [h for h in cli_fleet.REMOTE_HOSTS
                  if h.get("name") != "gatekeeper"]
        self.assertTrue(others)
        for h in others:
            self.assertNotIn("volume", h,
                             "%s must not carry a volume decl" % h.get("name"))


# --------------------------------------------------------------------------- #
# Install / status wiring in airuleset.py — install PRINTS state, never relocates.
#
# MAIN REVIEW blocker (2026-09-13): cmd_install used to call provision_volume()
# UNCONDITIONALLY, so a fleet install on gk would EXECUTE the relocation (stop
# runners one at a time, move the docker data-root, mount) with no owner
# present. ROZHODNUTÉ on #999: install is idempotent CONFIGURATION and NEVER
# moves data or stops services — it prints a status/plan line; the relocation
# is the EXPLICIT operator command `airuleset.py volume --apply`. These tests
# replace the old ones (which asserted the forbidden unconditional call).
# --------------------------------------------------------------------------- #
class TestInstallStatusWiring(TestCase):
    SRC = (REPO / "airuleset.py").read_text()

    def test_cmd_install_does_not_execute_relocation(self):
        # the forbidden unconditional `provision_volume()` call must be gone;
        # install prints the pure status line instead.
        self.assertNotIn("provision_volume()", self.SRC,
                         "install must NOT call provision_volume() — it would "
                         "relocate unattended")
        self.assertIn("volume_install_status", self.SRC,
                      "install must print the volume status/plan line")

    def test_volume_status_line_before_swap(self):
        # the volume status line stays ahead of the swap step for readability
        vol = self.SRC.index("volume_install_status")
        swap = self.SRC.index("provision_swap()")
        self.assertLess(vol, swap,
                        "volume status line must precede the swap step")

    def test_status_row_wired(self):
        self.assertIn("volume_status", self.SRC)


# --------------------------------------------------------------------------- #
# volume_install_status — the install-time line. PURE: it never shells out to
# relocate (no bash/sudo/mount/rsync/systemctl-stop), only reads state.
# --------------------------------------------------------------------------- #
class TestVolumeInstallStatus(TestCase):
    def _mutating(self, rec):
        return (rec.ran("bash") or rec.ran("sudo") or rec.ran("mount ")
                or rec.ran("rsync") or rec.ran("systemctl stop"))

    def test_no_declaration_line(self):
        rec = _Rec()
        line = dg.volume_install_status(decl=None, run=rec)
        self.assertIn("volume", line.lower())
        self.assertIn("none", line.lower())
        self.assertFalse(self._mutating(rec))

    def test_declared_not_applied_line(self):
        # nothing relocated yet (islink/isdir always False) -> plan line
        rec = _Rec([("df", 0, "Used  Size\n0   20G\n")])
        line = dg.volume_install_status(
            decl=dict(GK_DECL), run=rec,
            islink=lambda p: False, isdir=lambda p: False,
            home="/home/gatekeeper")
        self.assertIn("declared, not applied", line.lower())
        self.assertIn("volume --apply", line)
        self.assertFalse(self._mutating(rec),
                         "the install line must never shell out to relocate")

    def test_fully_applied_returns_status_row(self):
        # all 3 relocate entries already on the volume -> normal status row
        target_dirs = {"/mnt/gk-vol1/gh-runner", "/mnt/gk-vol1/docker",
                       "/mnt/gk-vol1/.cache"}
        links = {"/home/gatekeeper/.cache"}
        rec = _Rec([("df", 0, "Used  Size\n3.1G   20G\n")])
        line = dg.volume_install_status(
            decl=dict(GK_DECL), run=rec,
            islink=lambda p: p in links,
            isdir=lambda p: p in target_dirs,
            home="/home/gatekeeper")
        self.assertIn("/mnt/gk-vol1", line)
        self.assertIn("relocated", line.lower())
        self.assertNotIn("not applied", line.lower())
        self.assertFalse(self._mutating(rec))


# --------------------------------------------------------------------------- #
# cmd_volume — the EXPLICIT operator command. --plan renders + reads only;
# --apply is the ONLY path that executes the relocation (via sudo -n).
# --------------------------------------------------------------------------- #
class TestVolumeCommand(TestCase):
    def _run_cmd(self, apply, run, decl):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = airuleset.cmd_volume(SimpleNamespace(apply=apply),
                                      run=run, decl=decl)
        return rc, buf.getvalue()

    def test_dispatch_registered(self):
        self.assertIn("volume", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["volume"], airuleset.cmd_volume)

    def test_plan_prints_declaration_and_script_executes_nothing(self):
        rec = _Rec([("findmnt", 1, ""), ("df", 0, "Used Size\n0 20G\n")])
        rc, out = self._run_cmd(False, rec, dict(GK_DECL))
        self.assertIn(rc, (0, None))
        # declaration + rendered script are shown
        self.assertIn("/mnt/gk-vol1", out)
        self.assertIn("scsi-0HC_Volume_106853757", out)
        self.assertIn("set -euo pipefail", out)   # the rendered script
        # NOTHING mutating ran
        self.assertFalse(rec.ran("bash"))
        self.assertFalse(rec.ran("sudo"))
        self.assertFalse(rec.ran("systemctl stop"))
        self.assertFalse(rec.ran("rsync"))

    def test_apply_executes_via_runner(self):
        rec = _Rec([("sudo -n true", 0, "")])
        rc, out = self._run_cmd(True, rec, dict(GK_DECL))
        self.assertIn(rc, (0, None))
        self.assertIn("applied", out.lower())
        self.assertTrue(rec.ran("bash"),
                        "--apply must execute the rendered script")
        self.assertTrue(rec.ran("sudo"))

    def test_apply_failure_is_nonzero(self):
        rec = _Rec([("sudo -n true", 0, ""), ("bash", 1, "")])
        rc, out = self._run_cmd(True, rec, dict(GK_DECL))
        self.assertEqual(rc, 1)
        self.assertIn("fail", out.lower())

    def test_apply_no_declaration_refuses(self):
        orig = dg._local_volume_decl
        dg._local_volume_decl = lambda *a, **k: None
        try:
            rec = _Rec()
            rc, out = self._run_cmd(True, rec, None)
        finally:
            dg._local_volume_decl = orig
        self.assertNotEqual(rc, 0)
        self.assertIn("no declaration", out.lower())
        self.assertFalse(rec.ran("bash"))

    def test_plan_no_declaration_is_clean_noop(self):
        orig = dg._local_volume_decl
        dg._local_volume_decl = lambda *a, **k: None
        try:
            rec = _Rec()
            rc, out = self._run_cmd(False, rec, None)
        finally:
            dg._local_volume_decl = orig
        self.assertIn(rc, (0, None))
        self.assertIn("no declaration", out.lower())
        self.assertFalse(rec.ran("bash"))


if __name__ == "__main__":
    main()
