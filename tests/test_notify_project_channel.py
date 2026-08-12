"""#369 — separate Discord threads PER PROJECT / per subdev stream, instead of
one owner pile. Today `notification_channel()` resolves exactly two axes
(owner, kind ∈ {default, questions}); this adds a THIRD axis — `project` — so
run-cards / idle ✅ / gk-request-backstop / api-error pings for the SAME
project always land in `claude-<owner>-<project-slug>`, mirroring #296's own
`kind="questions"` design byte-for-byte:

  - `notification_channel(env, owner, project=X)` tries the owner+project key
    FIRST, falls through to the SAME cascade `kind="default"` already uses
    (owner's normal thread -> shared) whenever the project thread isn't
    provisioned yet -- a project with no thread yet keeps today's exact
    behaviour instead of losing a ping.
  - `project` is a no-op under `kind="questions"` -- the design's OWN
    deliberate choice (see #369's design comment): questions stay
    CENTRALIZED in the existing per-owner `-q` thread, never project-split.
  - `find_owner_project_thread` / `create_owner_project_thread` /
    `provision_project_thread()` mirror the existing question-thread trio:
    find-before-create (never fork a duplicate thread across boxes),
    anchored as a sibling of the owner's normal thread.
  - `resolve_project_channel()` is the side-effecting wrapper (mirrors
    `resolve_questions_channel()`): on a genuinely-missing project thread it
    logs a LOUD `fallback` line and kicks a guarded, detached, find-only
    self-heal spawn -- a delivery is NEVER silently dropped just because the
    thread doesn't exist yet.
  - `project_label_for(cwd)` = `stream_qualified(repo_name_for(cwd) or
    basename)` -- the SAME label already used for run-card headers, so a
    project's thread name and its own card header text always agree.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AIRULESET = ROOT / "airuleset.py"


class _HomeIsolated(unittest.TestCase):
    """Every test here points `notify._claude_dir()` (== expanduser('~')/
    .claude, via $HOME) at a scratch dir -- the live api-watchdog executes
    this repo's working tree every 60s on this box, so an un-isolated test
    races production state (real delivery log, real spawn-guard markers,
    real .env writes)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-pchan-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._env))

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# 1. project_label_for -- the canonical, origin-derived, stream-qualified
#    project label used for BOTH routing and (via the shell hooks, tested
#    separately) header text.
# --------------------------------------------------------------------------- #

class TestProjectLabelFor(unittest.TestCase):

    def test_origin_derived_name_wins_over_directory_basename(self):
        # The exact bug class this repo's own history documents repeatedly:
        # a local checkout dir named differently from the real repo.
        def run(argv):
            return "git@github.com:zbynekdrlik/odoo-erp.git\n"
        with m.patch("getpass.getuser", return_value="newlevel"):
            self.assertEqual(
                notify.project_label_for("/home/x/devel/odoo-slovnormal", run=run),
                "odoo-erp")

    def test_stream_qualified_for_a_stream_persona(self):
        def run(argv):
            return "git@github.com:zbynekdrlik/odoo-erp.git\n"
        with m.patch("getpass.getuser", return_value="david2"):
            self.assertEqual(
                notify.project_label_for("/home/david2/devel/odoo-erp", run=run),
                "odoo-erp-david2")

    def test_personal_box_stays_plain(self):
        def run(argv):
            return "git@github.com:zbynekdrlik/airuleset.git\n"
        with m.patch("getpass.getuser", return_value="newlevel"):
            self.assertEqual(
                notify.project_label_for("/home/newlevel/devel/airuleset", run=run),
                "airuleset")

    def test_no_origin_falls_back_to_basename(self):
        # A bare directory (no git repo / no origin) must still resolve to
        # SOMETHING stable -- never empty for a real cwd.
        def run(argv):
            return ""
        with m.patch("getpass.getuser", return_value="newlevel"):
            self.assertEqual(
                notify.project_label_for("/home/x/some-random-dir", run=run),
                "some-random-dir")

    def test_never_empty_for_an_empty_cwd(self):
        def run(argv):
            return ""
        with m.patch("getpass.getuser", return_value="newlevel"):
            label = notify.project_label_for("", run=run)
        self.assertTrue(label)


# --------------------------------------------------------------------------- #
# 2. notification_channel's new `project` axis
# --------------------------------------------------------------------------- #

class TestNotificationChannelProjectAxis(unittest.TestCase):

    def test_configured_project_thread_is_used(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP": "333"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", project="odoo-erp"),
            "333")

    def test_unconfigured_project_falls_through_to_owner_thread(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", project="odoo-erp"),
            "111")

    def test_unconfigured_project_and_owner_falls_through_to_shared(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "999"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", project="odoo-erp"),
            "999")

    def test_kind_default_is_the_default_and_reads_project(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_X": "333"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", kind="default",
                                        project="x"),
            "333")

    def test_kind_questions_IGNORES_project_entirely(self):
        # #369's own design decision: questions stay centralized. A project
        # key must NEVER be consulted when kind == "questions", even if one
        # is configured -- the owner's -q thread (or its own fallback) wins.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "222",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP": "333"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", kind="questions",
                                        project="odoo-erp"),
            "222")

    def test_a_project_named_q_cannot_collide_with_the_questions_key(self):
        # DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q (questions) vs
        # DISCORD_NOTIFICATION_CHANNEL_<OWNER>_P_Q (a project literally
        # named "q") must be two DISTINCT keys.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "222",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_Q": "444"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", project="q"), "444")
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek", kind="questions"),
            "222")

    def test_no_project_is_byte_identical_to_pre_369_behaviour(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        self.assertEqual(
            notify.notification_channel(env, owner="zbynek"),
            notify.notification_channel(env, owner="zbynek", project=None))

    def test_no_owner_never_reads_a_project_key(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "999"}
        self.assertEqual(
            notify.notification_channel(env, owner="", project="odoo-erp"), "999")


# --------------------------------------------------------------------------- #
# 3. find/create -- mocked Discord API, mirrors the question-thread pair
# --------------------------------------------------------------------------- #

class TestFindCreateOwnerProjectThread(unittest.TestCase):

    def _env(self):
        return {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "555"}

    def test_find_returns_empty_with_no_token(self):
        self.assertEqual(
            notify.find_owner_project_thread({}, "zbynek", "odoo-erp"), "")

    def test_find_locates_an_active_thread(self):
        calls = []

        def http(token, method, path, payload=None):
            calls.append((method, path))
            if path == "channels/555":
                return {"parent_id": "PARENT1", "guild_id": "G1"}
            if path == "guilds/G1/threads/active":
                return {"threads": [{"id": "TID1", "parent_id": "PARENT1",
                                     "name": "claude-zbynek-odoo-erp"}]}
            return None

        tid = notify.find_owner_project_thread(self._env(), "zbynek", "odoo-erp",
                                                http=http)
        self.assertEqual(tid, "TID1")

    def test_find_locates_an_archived_thread(self):
        def http(token, method, path, payload=None):
            if path == "channels/555":
                return {"parent_id": "PARENT1", "guild_id": "G1"}
            if path == "guilds/G1/threads/active":
                return {"threads": []}
            if path == "channels/PARENT1/threads/archived/public":
                return {"threads": [{"id": "TID2",
                                     "name": "claude-zbynek-odoo-erp"}]}
            return None

        tid = notify.find_owner_project_thread(self._env(), "zbynek", "odoo-erp",
                                                http=http)
        self.assertEqual(tid, "TID2")

    def test_find_returns_empty_when_nothing_matches(self):
        def http(token, method, path, payload=None):
            if path == "channels/555":
                return {"parent_id": "PARENT1", "guild_id": "G1"}
            if path == "guilds/G1/threads/active":
                return {"threads": []}
            if path == "channels/PARENT1/threads/archived/public":
                return {"threads": []}
            return None

        self.assertEqual(
            notify.find_owner_project_thread(self._env(), "zbynek", "odoo-erp",
                                             http=http), "")

    def test_create_posts_a_thread_under_the_owner_parent(self):
        posted = {}

        def http(token, method, path, payload=None):
            if method == "GET" and path == "channels/555":
                return {"parent_id": "PARENT1"}
            if method == "POST" and path == "channels/PARENT1/threads":
                posted.update(payload)
                return {"id": "NEWTID"}
            return None

        tid = notify.create_owner_project_thread(self._env(), "zbynek", "odoo-erp",
                                                  http=http)
        self.assertEqual(tid, "NEWTID")
        self.assertEqual(posted["name"], "claude-zbynek-odoo-erp")
        self.assertEqual(posted["type"], 11)
        self.assertEqual(posted["auto_archive_duration"], 10080)

    def test_thread_name_uses_the_dash_slug(self):
        # A project label carrying spaces/mixed case must still produce a
        # legal, predictable Discord thread name.
        posted = {}

        def http(token, method, path, payload=None):
            if method == "GET" and path == "channels/555":
                return {"parent_id": "PARENT1"}
            if method == "POST":
                posted.update(payload)
                return {"id": "X"}
            return None

        notify.create_owner_project_thread(self._env(), "zbynek",
                                           "Odoo ERP (david2)", http=http)
        self.assertEqual(posted["name"], "claude-zbynek-odoo-erp-david2")


# --------------------------------------------------------------------------- #
# 4. provision_project_thread -- idempotent find-then-create-and-save
# --------------------------------------------------------------------------- #

class TestProvisionProjectThread(_HomeIsolated):

    def test_already_configured_is_a_zero_network_read(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP": "EXISTING"}
        called = []
        tid = notify.provision_project_thread(
            "zbynek", "odoo-erp", env=env,
            http=lambda *a, **k: called.append(1))
        self.assertEqual(tid, "EXISTING")
        self.assertEqual(called, [])

    def test_finds_before_creating(self):
        env_path = self.home / ".claude" / "channels" / "discord" / ".env"

        def http(token, method, path, payload=None):
            if path == "channels/555":
                return {"parent_id": "P1", "guild_id": "G1"}
            if path == "guilds/G1/threads/active":
                return {"threads": [{"id": "FOUND", "parent_id": "P1",
                                     "name": "claude-zbynek-odoo-erp"}]}
            if method == "POST":
                raise AssertionError("must never CREATE when FIND succeeds")
            return None

        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "555"}
        tid = notify.provision_project_thread(
            "zbynek", "odoo-erp", env=env, env_path=str(env_path), http=http)
        self.assertEqual(tid, "FOUND")
        self.assertIn("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP=FOUND",
                      env_path.read_text())

    def test_creates_when_find_comes_up_empty(self):
        env_path = self.home / ".claude" / "channels" / "discord" / ".env"

        def http(token, method, path, payload=None):
            if method == "GET" and path == "channels/555":
                return {"parent_id": "P1", "guild_id": "G1"}
            if method == "GET" and path == "guilds/G1/threads/active":
                return {"threads": []}
            if method == "GET" and path == "channels/P1/threads/archived/public":
                return {"threads": []}
            if method == "POST":
                return {"id": "CREATED"}
            return None

        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "555"}
        tid = notify.provision_project_thread(
            "zbynek", "odoo-erp", env=env, env_path=str(env_path), http=http)
        self.assertEqual(tid, "CREATED")

    def test_find_only_never_creates(self):
        def http(token, method, path, payload=None):
            if method == "GET" and path == "channels/555":
                return {"parent_id": "P1", "guild_id": "G1"}
            if method == "GET":
                return {"threads": []}
            raise AssertionError("create=False must never POST")

        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "555"}
        tid = notify.provision_project_thread(
            "zbynek", "odoo-erp", env=env, create=False, http=http)
        self.assertEqual(tid, "")

    def test_no_owner_or_project_returns_empty(self):
        self.assertEqual(notify.provision_project_thread("", "x"), "")
        self.assertEqual(notify.provision_project_thread("zbynek", ""), "")


# --------------------------------------------------------------------------- #
# 5. resolve_project_channel -- the side-effecting (log + self-heal) wrapper
# --------------------------------------------------------------------------- #

class TestResolveProjectChannel(_HomeIsolated):

    def test_configured_project_thread_is_used_no_log_no_spawn(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP": "333"}
        calls = []
        chan = notify.resolve_project_channel(
            env=env, owner="zbynek", project="odoo-erp",
            spawn=lambda o, p: calls.append((o, p)))
        self.assertEqual(chan, "333")
        self.assertEqual(calls, [])
        self.assertEqual(self.log_lines(), [])

    def test_missing_project_thread_falls_back_to_the_normal_thread(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        chan = notify.resolve_project_channel(
            env=env, owner="zbynek", project="odoo-erp",
            spawn=lambda o, p: None)
        self.assertEqual(chan, "111")

    def test_missing_project_thread_logs_the_fallback_loudly(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        notify.resolve_project_channel(env=env, owner="zbynek",
                                       project="odoo-erp", spawn=lambda o, p: None)
        lines = self.log_lines()
        self.assertTrue(lines)
        self.assertTrue(
            any("fallback" in ln and "zbynek" in ln and "odoo-erp" in ln
                and "p-thread-not-provisioned" in ln for ln in lines), lines)

    def test_missing_project_thread_spawns_a_provision_attempt(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        calls = []
        notify.resolve_project_channel(env=env, owner="zbynek", project="odoo-erp",
                                       spawn=lambda o, p: calls.append((o, p)))
        self.assertEqual(calls, [("zbynek", "odoo-erp")])

    def test_no_project_never_logs_or_spawns(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        calls = []
        chan = notify.resolve_project_channel(
            env=env, owner="zbynek", project=None,
            spawn=lambda o, p: calls.append((o, p)))
        self.assertEqual(chan, "111")
        self.assertEqual(calls, [])
        self.assertEqual(self.log_lines(), [])

    def test_no_bot_token_never_logs_fallback_or_spawns(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}   # no token
        calls = []
        chan = notify.resolve_project_channel(
            env=env, owner="zbynek", project="odoo-erp",
            spawn=lambda o, p: calls.append((o, p)))
        self.assertEqual(chan, "111")
        self.assertEqual(calls, [])
        self.assertEqual(self.log_lines(), [])

    def test_default_spawn_is_the_real_provisioner(self):
        env = {"DISCORD_BOT_TOKEN": "tok",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        with m.patch.object(notify, "_spawn_provision_project_thread") as fake:
            notify.resolve_project_channel(env=env, owner="zbynek",
                                           project="odoo-erp")
        fake.assert_called_once_with("zbynek", "odoo-erp")


# --------------------------------------------------------------------------- #
# 6. the atomic spawn guard, reused verbatim from the questions-thread guard
# --------------------------------------------------------------------------- #

class TestSpawnProvisionProjectThreadGuard(_HomeIsolated):

    def test_spawn_guard_marker_throttles(self):
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_project_thread("zbynek", "odoo-erp")
            notify._spawn_provision_project_thread("zbynek", "odoo-erp")
        self.assertEqual(len(calls), 1)

    def test_different_projects_are_not_throttled_by_each_other(self):
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_project_thread("zbynek", "odoo-erp")
            notify._spawn_provision_project_thread("zbynek", "airuleset")
        self.assertEqual(len(calls), 2)

    def test_questions_guard_and_project_guard_never_collide(self):
        # Two INDEPENDENT self-heal mechanisms for the SAME owner must never
        # share one guard file -- a burst of both kinds must spawn one of EACH.
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_question_thread("zbynek")
            notify._spawn_provision_project_thread("zbynek", "odoo-erp")
        self.assertEqual(len(calls), 2)

    def test_spawn_calls_provision_project_thread_for_owner_and_project(self):
        calls = []

        def fake_popen(*a, **k):
            calls.append((a, k))
            return m.Mock()

        with m.patch.object(notify.subprocess, "Popen", fake_popen):
            notify._spawn_provision_project_thread("zbynek", "odoo-erp")
        self.assertEqual(len(calls), 1)
        (argv,), kwargs = calls[0]
        self.assertEqual(argv[0], notify.sys.executable)
        self.assertTrue(argv[1].endswith("airuleset.py"), argv[1])
        self.assertTrue(os.path.isfile(argv[1]))
        self.assertEqual(
            argv[2:],
            ["notify", "--provision-project-thread", "--owner-name", "zbynek",
             "--project", "odoo-erp", "--find-only"],
            "the AUTOMATIC self-heal must be FIND-only -- never auto-CREATE "
            "a new Discord thread unattended")
        self.assertIs(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("stderr"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("start_new_session"), True)

    def test_concurrent_spawns_for_the_same_owner_project_yield_exactly_one(self):
        calls = []
        lock = threading.Lock()

        def fake_popen(*a, **k):
            with lock:
                calls.append(a)
            return m.Mock()

        with m.patch.object(notify.subprocess, "Popen", fake_popen):
            threads = [threading.Thread(
                target=notify._spawn_provision_project_thread,
                args=("zbynek", "odoo-erp")) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)


# --------------------------------------------------------------------------- #
# 7. `send()` threads `project` through and self-heals only on a REAL send
# --------------------------------------------------------------------------- #

class TestSendProjectRouting(_HomeIsolated):

    def _post(self, ok=True):
        orig = notify._post_discord
        notify._post_discord = lambda *a, **k: ok
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))

    def test_real_send_routes_to_the_configured_project_thread(self):
        self._post(True)
        posted = []
        orig = notify._post_discord

        def fake(token, channel, content):
            posted.append(channel)
            return orig(token, channel, content)

        notify._post_discord = fake
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))
        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP": "333"}
        st = notify.send("hi", env=env, owner="zbynek", project="odoo-erp")
        self.assertEqual(st, "sent")
        self.assertEqual(posted, ["333"])

    def test_real_send_with_no_project_thread_still_self_heals(self):
        self._post(True)
        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        with m.patch.object(notify, "_spawn_provision_project_thread") as fake:
            st = notify.send("hi", env=env, owner="zbynek", project="odoo-erp")
        self.assertEqual(st, "sent")
        fake.assert_called_once_with("zbynek", "odoo-erp")

    def test_dry_run_never_spawns_a_self_heal(self):
        # A preview call must have ZERO side effects beyond printing.
        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        with m.patch.object(notify, "_spawn_provision_project_thread") as fake:
            notify.send("hi", env=env, owner="zbynek", project="odoo-erp",
                       dry_run=True)
        fake.assert_not_called()
        self.assertEqual(self.log_lines(), [])

    def test_no_project_is_unaffected(self):
        self._post(True)
        env = {"DISCORD_BOT_TOKEN": "t",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        with m.patch.object(notify, "_spawn_provision_project_thread") as fake:
            st = notify.send("hi", env=env, owner="zbynek")
        self.assertEqual(st, "sent")
        fake.assert_not_called()


# --------------------------------------------------------------------------- #
# 8. CLI wiring -- subprocess, proves the WIRING (not just the function)
# --------------------------------------------------------------------------- #

class TestChannelIdCliWiresProjectRouting(_HomeIsolated):

    def _write_env(self, p=False, token=True):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        if token:
            lines.append("DISCORD_BOT_TOKEN=xxtokenxx")
        lines.append("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=111")
        if p:
            lines.append("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_P_ODOO_ERP=333")
        (d / ".env").write_text("\n".join(lines) + "\n")

    def _run(self, extra):
        env = {**os.environ, "HOME": str(self.home),
               "AIRULESET_NOTIFY_OWNER": "zbynek"}
        return subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--channel-id", *extra],
            capture_output=True, text=True, env=env)

    def test_configured_prints_the_project_channel(self):
        self._write_env(p=True)
        r = self._run(["--project", "odoo-erp"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "333")

    def test_unconfigured_no_token_falls_back_silently(self):
        self._write_env(p=False, token=False)
        r = self._run(["--project", "odoo-erp"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "111")
        self.assertEqual(self.log_lines(), [])

    def test_no_project_flag_is_the_unchanged_default_kind_behaviour(self):
        self._write_env(p=True)
        r = self._run([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "111")   # never the project thread

    def test_kind_questions_ignores_a_project_flag(self):
        self._write_env(p=True)
        d = self.home / ".claude" / "channels" / "discord" / ".env"
        d.write_text(d.read_text() +
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=222\n")
        r = self._run(["--kind", "questions", "--project", "odoo-erp"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "222")


class TestProjectLabelCli(_HomeIsolated):

    def test_prints_the_origin_derived_stream_qualified_label(self):
        repo = Path(tempfile.mkdtemp(prefix="airuleset-pl-repo-"))
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin",
                        "git@github.com:zbynekdrlik/odoo-erp.git"],
                       cwd=repo, check=True)
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--project-label",
             "--cwd", str(repo)],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # personal box user -> unqualified (this test's own runtime user
        # varies by box; assert the repo name is a substring at minimum)
        self.assertIn("odoo-erp", r.stdout.strip())

    def test_bare_directory_falls_back_to_basename(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-pl-plain-"))
        self.addCleanup(shutil.rmtree, d, True)
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--project-label",
             "--cwd", str(d)],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(d.name, r.stdout.strip())


class TestProvisionProjectThreadCli(_HomeIsolated):

    def test_find_only_no_token_fails_loudly(self):
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(
            [sys.executable, str(AIRULESET), "notify",
             "--provision-project-thread", "--owner-name", "zbynek",
             "--project", "odoo-erp", "--find-only"],
            capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr.strip())
        self.assertTrue(any("provision-failed" in ln for ln in self.log_lines()),
                        self.log_lines())


if __name__ == "__main__":
    unittest.main()
