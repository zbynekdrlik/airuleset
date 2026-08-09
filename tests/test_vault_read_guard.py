"""hooks/block-vault-store-read.sh — the credential store is not read by hand.

#153 finding 1. #144 shipped a channel whose central claim is that a
credential's VALUE never reaches the session transcript, and then rested that
claim on the agent choosing `secret exec` over `cat`. The store is 0600 under
`~/.claude/secrets/`, owned by the very uid the agent's Bash runs as, and no
hook gated it — so one `cat ~/.claude/secrets/DB_PASS.secret` put the value in
the transcript permanently. That is #134's shape exactly: a guarantee deferring
to an unenforced action.

This hook is the artifact the guarantee now rests on. It matches the RAW
command text rather than parsed argv, because the interesting evasions all hide
the path inside a quoted string (`python3 -c 'open("…").read()'`, a `<`
redirection, `$(<file)`) where token parsing cannot see it. Deny-by-default on
any store reference, with a small allowlist of provably metadata-only heads.

Its honest limit is asserted here too (test_the_refusal_states_its_own_limit):
the agent's uid holds NOPASSWD sudo on these boxes, so this is a guardrail that
makes the unsafe path refused-by-default and leaves an audit artifact when it is
deliberately circumvented — never a boundary a determined process cannot cross.
No test in this file uses a real credential value.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-vault-store-read.sh"

STORE = "~/.claude/secrets"
ABS = "/home/newlevel/.claude/secrets"


def run_payload(payload_obj, env_extra=None):
    """Feed an arbitrary PreToolUse payload — for the non-Bash tools."""
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload_obj),
                          capture_output=True, text=True, env=env)


def run_payload_text(text, env_extra=None):
    """Feed RAW bytes — the malformed-payload cases are not JSON at all."""
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["/bin/bash", str(HOOK)], input=text,
                          capture_output=True, text=True, env=env)


def run(cmd, env_extra=None):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                          "cwd": "/home/newlevel/devel/airuleset"})
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    if env_extra:
        env.update(env_extra)
    # /bin/bash by ABSOLUTE path: the interpreter is resolved through the env
    # we pass, so a test that empties PATH to break the CHECK would otherwise
    # just fail to find bash and never run the hook at all (127, not a verdict).
    return subprocess.run(["/bin/bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


class Blocks(unittest.TestCase):
    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstdout=%s\nstderr=%s"
                         % (cmd, r.stdout, r.stderr))

    # --- the one-command leak this ticket exists to close --------------------
    def test_the_incident_shape(self):
        self.assertBlocked("cat %s/DB_PASS.secret" % STORE)

    def test_absolute_path(self):
        self.assertBlocked("cat %s/DB_PASS.secret" % ABS)

    def test_home_variable_path(self):
        self.assertBlocked("cat $HOME/.claude/secrets/DB_PASS.secret")

    def test_relative_after_a_cd(self):
        # The store-dir prefix is gone; the `.secret` extension is what carries
        # the match here, which is why the second pattern exists.
        self.assertBlocked("cd %s && cat DB_PASS.secret" % STORE)

    # --- every reader the ticket names --------------------------------------
    def test_each_named_reader(self):
        for reader in ("cat", "less", "more", "head", "tail", "xxd", "od",
                       "strings", "base64", "cp", "bat", "nl", "tac", "rev",
                       "hexdump", "base32", "shasum", "md5sum", "awk", "sed",
                       "grep", "sort", "uniq", "cut", "diff", "tee", "gzip",
                       "scp", "rsync", "tar", "mv", "ln", "install", "dd"):
            with self.subTest(reader=reader):
                self.assertBlocked("%s %s/DB_PASS.secret" % (reader, STORE))

    def test_a_reader_this_hook_never_heard_of(self):
        # The point of deny-by-default: an unanticipated reader must fail
        # CLOSED. A blocklist of reader names would let this through silently.
        self.assertBlocked("some-future-pager %s/DB_PASS.secret" % STORE)

    # --- shapes that hide the path from argv parsing ------------------------
    def test_python_open(self):
        self.assertBlocked(
            "python3 -c 'print(open(\"%s/DB_PASS.secret\").read())'" % ABS)

    def test_python_pathlib_read_text(self):
        self.assertBlocked(
            'python3 -c "import pathlib;'
            'print(pathlib.Path(\'%s/DB_PASS.secret\').read_text())"' % ABS)

    def test_input_redirection(self):
        self.assertBlocked(
            "while read l; do echo \"$l\"; done < %s/DB_PASS.secret" % STORE)

    def test_dollar_paren_less_than(self):
        self.assertBlocked("echo \"$(<%s/DB_PASS.secret)\"" % STORE)

    def test_command_substitution_inside_an_allowlisted_head(self):
        # `ls` is allowlisted, but the substitution inside it is a read. If the
        # hook only looked at the segment head this would leak.
        self.assertBlocked("ls \"$(cat %s/DB_PASS.secret)\"" % STORE)

    def test_backtick_substitution_inside_an_allowlisted_head(self):
        self.assertBlocked("stat `cat %s/DB_PASS.secret`" % STORE)

    def test_glob_over_the_store(self):
        self.assertBlocked("cat %s/*" % STORE)

    def test_glob_with_the_extension(self):
        self.assertBlocked("cat %s/*.secret" % STORE)

    def test_the_whole_dir_as_an_archive(self):
        self.assertBlocked("tar cf - %s | base64" % STORE)

    # --- prefixes must not launder the read ---------------------------------
    def test_sudo_does_not_launder_it(self):
        self.assertBlocked("sudo cat %s/DB_PASS.secret" % STORE)

    def test_env_assignment_prefix_does_not_launder_it(self):
        self.assertBlocked("FOO=1 cat %s/DB_PASS.secret" % STORE)

    def test_a_later_segment_is_still_checked(self):
        self.assertBlocked("ls -l /tmp && cat %s/DB_PASS.secret" % STORE)

    def test_a_piped_segment_is_still_checked(self):
        self.assertBlocked("true | cat %s/DB_PASS.secret" % STORE)

    # --- writing a value by hand is the same leak, from the other side ------
    def test_writing_a_value_into_the_store_is_blocked(self):
        # Typing a credential into a shell command puts it in the transcript —
        # exactly what `secret request` exists to avoid.
        self.assertBlocked("echo NOT_A_REAL_VALUE > %s/DB_PASS.secret" % STORE)

    def test_appending_a_value_into_the_store_is_blocked(self):
        self.assertBlocked("printf x >> %s/DB_PASS.secret" % STORE)


class BlocksAfterAdversarialReview(unittest.TestCase):
    """Bypasses the Fable adversarial review found in the first green version.

    Each of these READ a credential and were ALLOWED. They are grouped so the
    reason each allowlist entry was removed stays attached to the command that
    defeated it.
    """

    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstderr=%s" % (cmd, r.stderr))

    # F4 — `cd` was allowlisted, so the store could be entered and then read
    # with a bare glob carrying no store reference at all.
    def test_cd_into_the_store_then_read_by_glob(self):
        self.assertBlocked("cd %s && cat *" % STORE)

    def test_cd_into_the_store_at_all(self):
        # Blocked even alone: the Bash tool's cwd PERSISTS across calls, so an
        # allowed `cd` in one call makes `cat *` in the NEXT call invisible to
        # a stateless hook. Refusing the `cd` is what closes that.
        self.assertBlocked("cd %s" % STORE)

    # F5 — an allowlisted head piped into a reader.
    def test_allowlisted_head_piped_into_a_reader(self):
        self.assertBlocked("ls %s/* | xargs cat" % STORE)

    def test_allowlisted_head_piped_at_all(self):
        self.assertBlocked("ls %s | while read f; do cat $f; done" % STORE)

    # F6 — `file` and `du` ingest a file as a NAME LIST and echo its content
    # back in their own error text, defeating "provably metadata-only".
    def test_file_reads_content_via_a_name_list(self):
        self.assertBlocked("file -f %s/DB_PASS.secret" % STORE)

    def test_du_reads_content_via_files0_from(self):
        self.assertBlocked("du --files0-from=%s/DB_PASS.secret" % STORE)

    # F7 — mutations are not metadata. Handing a 0600 credential to another
    # uid on a box that hosts foreign uids by design is not a read, but it is
    # not something an allowlist called "metadata only" may permit.
    def test_chown_to_another_uid(self):
        self.assertBlocked("sudo chown otheruser %s/DB_PASS.secret" % STORE)

    def test_chmod_world_readable(self):
        self.assertBlocked("chmod 644 %s/DB_PASS.secret" % STORE)

    def test_rm_is_not_the_sanctioned_deletion_path(self):
        # `secret forget` exists and reports honestly; a bare rm destroys the
        # user's store silently.
        self.assertBlocked("rm -f %s/DB_PASS.secret" % STORE)

    # F3 — a GLOB filename evaded the `<stem>.secret` pattern, and the
    # containing dir was named only as the ancestor.
    def test_find_by_glob_name_then_exec_cat(self):
        self.assertBlocked("find ~/.claude -name '*.secret' -exec cat {} +")

    def test_a_bare_glob_value_filename(self):
        self.assertBlocked("cat /some/dir/*.secret")

    # F2 — a recursive sweep of the store's PARENT never named the store.
    def test_recursive_sweep_of_the_parent_dir(self):
        self.assertBlocked("grep -r password ~/.claude")

    def test_recursive_sweep_of_the_parent_dir_absolute(self):
        self.assertBlocked("grep -rn '' /home/newlevel/.claude")

    def test_archiving_the_parent_dir(self):
        self.assertBlocked("tar czf /tmp/c.tgz ~/.claude")


class GlobbingWalksPastTheSpelling(unittest.TestCase):
    """#156 hole 1. The path predicate read the command's SPELLING while the
    shell reads the POST-EXPANSION path, and globbing is an entire expansion
    layer between the two that the hook had no model of.

    Every command here was measured ALLOW against the first green version, one
    hook invocation per row, with a fake name. Each one reads a credential.
    """

    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstderr=%s" % (cmd, r.stderr))

    def test_every_literal_component_elided_at_once(self):
        self.assertBlocked("cat $HOME/.cl*/sec*/*")

    def test_the_dir_and_the_extension_both_truncated(self):
        self.assertBlocked("cat ~/.claude/secr*/DB_PASS.sec*")

    def test_single_character_wildcards(self):
        self.assertBlocked("cat ~/.claud?/secret?/DB_PASS.secre?")

    def test_bracket_expressions(self):
        self.assertBlocked("cat ~/.claud[e]/secret[s]/*")

    def test_find_exec_over_a_globbed_store_dir(self):
        self.assertBlocked("find ~/.claude/secr* -type f -exec cat {} +")

    def test_archiving_a_globbed_store_dir(self):
        self.assertBlocked("tar cf - ~/.claude/secr* | base64")

    def test_cd_to_the_parent_then_glob_the_store(self):
        # Within ONE command the hook can see both halves, so the relative
        # token is resolved against the cd target. (Across CALLS it cannot —
        # the cwd persists and this hook is stateless; that stays a stated gap.)
        self.assertBlocked("cd ~/.claude && cat sec*/*")


class OtherLayersBetweenTheSpellingAndThePath(unittest.TestCase):
    """The adversarial review's findings on the hole-1 fix — same root cause,
    layers other than globbing.

    Each was ALLOW and each was then VERIFIED to actually read a credential
    against a sandbox HOME. Globbing is not the only thing the shell resolves
    between the text this hook reads and the path it opens.
    """

    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstderr=%s" % (cmd, r.stderr))

    # --- path noise between the two components. Both names are spelled
    # LITERALLY here, so this defeated the original adjacency regex too.
    def test_a_dot_component_between_the_two_names(self):
        self.assertBlocked("cat %s/./DB_PASS.sec*" % STORE.replace(
            "/secrets", "/./secrets"))

    def test_dot_between_the_parent_and_the_store(self):
        self.assertBlocked("cat ~/.claude/./secrets/*")

    def test_a_doubled_slash_after_the_dot(self):
        self.assertBlocked("cat ~/.claude/.//secrets/*")

    def test_a_dotdot_traversal_through_a_sibling(self):
        self.assertBlocked("cat ~/.claude/x/../secrets/*")

    # --- brace expansion: a SECOND expansion layer, exactly like globbing
    def test_brace_alternative_naming_the_store(self):
        self.assertBlocked("cat ~/.claude/{secrets,x}/*")

    def test_brace_inside_a_component(self):
        self.assertBlocked("cat ~/.claude/{s,y}ecrets/*")

    def test_brace_over_the_parent_component(self):
        self.assertBlocked("cat ~/{.claude,x}/secrets/*")

    # --- `find` walks a tree by construction, with no -r flag to detect
    def test_find_over_the_parent_execing_a_reader(self):
        self.assertBlocked("find ~/.claude -type f -exec cat {} +")

    def test_a_bare_find_listing_names_is_not_a_sweep(self):
        # `find <parent> -name x` prints NAMES, exactly like the `ls -R` the
        # allowlist already permits. Requiring the reading action costs 5 real
        # commands; treating any find over the parent as a sweep costs 104
        # more that only ever listed names.
        r = run("find ~/.claude -maxdepth 3 -name '*.json'")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_piped_find_is_admitted_rather_than_closed(self):
        # `find <parent> -type f | xargs cat` DOES read every credential and is
        # NOT blocked — the consumer is a separate segment, and deciding it
        # would mean enumerating reader commands, which this guard rejects on
        # principle. It falls under the xargs-fed-a-name-list gap the header
        # already carries; the header must name this shape specifically, since
        # it has now been measured leaking rather than merely suspected.
        r = run("find ~/.claude -type f | xargs cat")
        self.assertEqual(r.returncode, 0,
                         "if this now blocks, the header's admission is stale")
        self.assertIn("xargs", HOOK.read_text())
        self.assertIn("find <parent> -type f | xargs cat", HOOK.read_text())


class TheWideningDoesNotDenyOrdinaryWork(unittest.TestCase):
    """The other half of hole 1, and the reason it was measured rather than
    reasoned about.

    This hook runs on every Bash/Read/Grep/Glob call in every session on every
    managed box, so a careless widening is a fleet-wide denial of ordinary
    work. Each case below is a real shape drawn from the corpus replay
    (212,557 unique real commands) that an over-eager anchor rule DID match on
    the way to this one — regexes and -name patterns are not paths, which is
    the distinction the guard already makes deliberately for Grep's `pattern`.
    """

    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for: %s\nstderr=%s" % (cmd, r.stderr))

    def test_a_grep_regex_that_merely_looks_like_a_path(self):
        self.assertAllowed(
            'grep -niE "password|passphrase|api[_-]?key|secret.*=" notes.md')

    def test_a_grep_regex_whose_tail_looks_like_the_parent_dir(self):
        self.assertAllowed(
            "grep -E '^[[:space:]]*//.*[Cc]laude' src/main.rs")

    def test_a_find_name_pattern_for_claude_files_elsewhere(self):
        self.assertAllowed('find /mnt/backup -name "*.claude*" | head -20')

    def test_a_wildcard_pair_in_an_unrelated_tree(self):
        self.assertAllowed("cat /tmp/build/*/*")

    def test_a_project_with_its_own_secrets_directory(self):
        self.assertAllowed("cat secrets/config.json")

    def test_cd_elsewhere_then_a_relative_secrets_directory(self):
        self.assertAllowed("cd /tmp/proj && cat secrets/config.json")

    def test_an_ordinary_dotfile_after_a_cd_into_the_parent(self):
        self.assertAllowed("cd ~/.claude && cat settings.json")

    def test_the_transcript_sweeps_this_repo_depends_on(self):
        self.assertAllowed("grep -rn 'compact_boundary' ~/.claude/projects/*")

    def test_a_size_report_over_the_parent(self):
        # `du -sh ~/.claude/*` occurs repeatedly in the real corpus and reports
        # SIZES, never content. Blocking it to close an unanchored-wildcard
        # spelling nobody types by reflex was the trade this rule declined.
        self.assertAllowed("du -sh ~/.claude/* 2>/dev/null | sort -rh")

    def test_a_sibling_dotdir_that_merely_starts_the_same_way(self):
        self.assertAllowed("cat ~/.claude-backup/notes.txt")


class ValueFileInfixVsConfigExtension(unittest.TestCase):
    """#165 — `VALUE_FILE_RE` matched `.secret` as an INFIX, not just a
    terminal extension, so an ordinary `<name>.secret.<config-ext>` config
    (a gitignored local config named to remind that it holds secrets) was
    falsely refused. The fix narrows the match with an explicit ALLOW-list
    of common config extensions, while every OTHER extension (including a
    copy/archive of a real value file) stays blocked by the pattern's own
    existing deny-by-default boundary — no explicit block-side enumeration
    needed. See the design comment on the ticket for the full reasoning.
    """

    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstdout=%s\nstderr=%s"
                         % (cmd, r.stdout, r.stderr))

    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for: %s\nstderr=%s" % (cmd, r.stderr))

    # --- the ticket's own false-positive examples must now be ALLOWED ------
    def test_config_json_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("cat config.secret.json")

    def test_config_yaml_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("cat app.secret.yaml")

    def test_config_env_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("vim deploy.secret.env")

    # --- the rest of the config-ext allow-list, not literally in the ticket,
    # covered here for completeness of the shipped extension list -----------
    def test_config_yml_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("cat service.secret.yml")

    def test_config_toml_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("cat settings.secret.toml")

    def test_config_ini_with_a_secret_infix_is_allowed(self):
        self.assertAllowed("cat app.secret.ini")

    # --- a bare value file and its copies/archives must STAY blocked -------
    def test_the_bare_terminal_value_file_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret")

    def test_a_bak_copy_of_a_value_file_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret.bak")

    def test_a_gz_archive_of_a_value_file_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret.gz")

    def test_a_tilde_backup_of_a_value_file_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret~")

    def test_an_unlisted_extension_stays_blocked_by_default(self):
        # No enumeration needed on the block side: anything NOT on the
        # allow-list stays blocked, including an extension nobody thought of.
        self.assertBlocked("cat DB_PASS.secret.mystery")

    # --- boundary correctness: a config extension must match WHOLE --------
    def test_a_config_extension_prefix_that_is_not_the_whole_extension_blocks(self):
        # `.jsonx` is not `.json` — the allow-list match must not be fooled
        # by a mere prefix of a listed extension.
        self.assertBlocked("cat config.secret.jsonx")

    # --- the vault's own real value files are unaffected --------------------
    def test_the_real_store_value_file_by_absolute_path_still_blocks(self):
        self.assertBlocked("cat %s/DB_PASS.secret" % STORE)

    # --- a listed extension used as a MIDDLE component, not the terminal one
    # (adversarial review finding F1) — a copy/archive of a real value file
    # renamed through an allow-listed extension must NOT slip the net just
    # because a further suffix follows it.
    def test_a_listed_extension_followed_by_a_further_suffix_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret.json.gz")

    def test_a_listed_extension_followed_by_an_archive_extension_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret.env.bak")

    def test_a_listed_extension_followed_by_a_second_config_extension_still_blocks(self):
        self.assertBlocked("cat DB_PASS.secret.json.bak")

    # --- the allow-list is a WHOLE-EXTENSION match: it must be the FINAL
    # extension, not merely present somewhere in the trailing suffix chain
    # (a real config's own backup, `config.secret.json.bak`, is the honest
    # cost of closing the above — it now stays blocked too, and that trade
    # is documented in the hook's header).
    def test_a_backup_of_an_otherwise_allowed_config_now_blocks_too(self):
        self.assertBlocked("cat config.secret.json.bak")

    # --- rule B also gates Read/Grep/Glob (#153), not just Bash (review F6):
    # the config-ext allow-list must apply identically on that surface.
    def test_read_tool_on_a_config_ext_infix_name_is_allowed(self):
        r = run_payload({"tool_name": "Read",
                         "tool_input": {"file_path": "/tmp/deploy.secret.env"}})
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for Read on deploy.secret.env\nstderr=%s"
                         % r.stderr)

    def test_glob_tool_on_a_config_ext_infix_pattern_is_allowed(self):
        r = run_payload({"tool_name": "Glob",
                         "tool_input": {"pattern": "**/*.secret.json"}})
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for Glob on *.secret.json\nstderr=%s"
                         % r.stderr)

    def test_read_tool_on_the_real_store_file_still_blocks(self):
        r = run_payload({"tool_name": "Read",
                         "tool_input": {"file_path": "%s/DB_PASS.secret" % ABS}})
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for Read on the real store file")


class Allows(unittest.TestCase):
    def assertAllowed(self, cmd, **kw):
        r = run(cmd, **kw)
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for: %s\nstderr=%s" % (cmd, r.stderr))

    # --- the sanctioned CLI surface -----------------------------------------
    def test_secret_exec_is_the_whole_point(self):
        self.assertAllowed("python3 airuleset.py secret exec DB_PASS -- psql -c 'select 1'")

    def test_secret_request_status_list_forget(self):
        for action in ("request DB_PASS", "status DB_PASS", "list", "forget DB_PASS",
                       "purge"):
            with self.subTest(action=action):
                self.assertAllowed("python3 airuleset.py secret %s" % action)

    # --- metadata-only heads ------------------------------------------------
    def test_listing_the_store(self):
        self.assertAllowed("ls -l %s/" % STORE)
        self.assertAllowed("ls %s" % STORE)

    def test_stat_and_test(self):
        # The allowlist is now exactly the heads that are PROVABLY content-free
        # AND non-mutating: ls, stat, test/[. `rm`, `chmod`, `chown`, `touch`,
        # `shred`, `file`, `du`, `cd` and `pushd` were removed after the
        # adversarial review (F4/F6/F7) — see BlocksAfterAdversarialReview.
        self.assertAllowed("stat %s/DB_PASS.secret" % STORE)
        self.assertAllowed("test -f %s/DB_PASS.secret && echo present" % STORE)

    def test_searching_this_repo_for_unrelated_claude_subdirs(self):
        # The ancestor-sweep rule (F2) must not block the transcript greps this
        # repo's own work depends on — a sibling subdir cannot reach the store.
        self.assertAllowed("grep -rn 'compact_boundary' ~/.claude/projects/")

    # --- unrelated traffic must not be caught -------------------------------
    def test_the_unrelated_ssh_key_dir(self):
        # `~/.secrets/` (plural, no .claude) is the gatekeeper access-key dir
        # from block-subdev-ssh-misuse.sh — a different thing entirely.
        self.assertAllowed("ssh -i ~/.secrets/gatekeeper_access_ed25519 marek@subdev")

    def test_grepping_the_word_secrets(self):
        self.assertAllowed("grep -rn secrets hooks/")

    def test_the_upload_log(self):
        self.assertAllowed("grep SAVED ~/.claude/upload-logs/upload-8788.log")

    def test_running_the_suite(self):
        self.assertAllowed("python3 -m pytest tests/ -q")

    def test_an_ordinary_dotfile_read(self):
        self.assertAllowed("cat ~/.claude/settings.json")

    def test_empty_and_unrelated(self):
        self.assertAllowed("git status --porcelain")


class BypassIsForTheUserNotTheAgent(unittest.TestCase):
    """The deliberate deviation from this repo's hook convention.

    Every other hook here takes an inline `# airuleset:<x>-ok <reason>` marker.
    This one must NOT: a marker the agent can append to its own command is the
    voluntary compliance #153 exists to remove. Only an env bypass remains, and
    it is logged.
    """

    def test_an_inline_marker_does_not_open_the_store(self):
        for marker in ("# airuleset:secret-read-ok testing",
                       "# airuleset:destructive-ok testing",
                       "# airuleset:scope-gate-ok testing",
                       "# airuleset:script-ok testing"):
            with self.subTest(marker=marker):
                r = run("cat %s/DB_PASS.secret  %s" % (STORE, marker))
                self.assertEqual(r.returncode, 2,
                                 "an inline marker must not open the store: %s"
                                 % marker)

    def test_the_env_bypass_works_and_is_logged(self):
        with tempfile.TemporaryDirectory() as td:
            r = run("cat %s/DB_PASS.secret" % STORE,
                    env_extra={"AIRULESET_ALLOW_VAULT_READ": "1",
                               "AIRULESET_VAULT_READ_AUDIT": str(Path(td) / "a.log")})
            self.assertEqual(r.returncode, 0, r.stderr)
            logged = (Path(td) / "a.log").read_text()
            self.assertIn("env-bypass", logged)
            self.assertIn("DB_PASS.secret", logged)


class TheAuditLogIsNotASecondPlaceTheValueRests(unittest.TestCase):
    """#157. The channel exists so a credential is never written down in the
    clear, and the guard's own audit log wrote the FULL command text.

    An allowed WRITE carries its value in its own text, so a bypassed
    `echo <value> > <store>` deposited that value verbatim into a plaintext log
    inside the repo tree — a second resting place, and unlike the store itself
    that one had no guard on reading it, no restrictive mode and no TTL.

    No real credential is used here; the value below is a fake.
    """

    FAKE = "NotARealValue-9f3a7c21b5"

    def _bypass(self, cmd):
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        log = Path(td) / "a.log"
        r = run(cmd, env_extra={"AIRULESET_ALLOW_VAULT_READ": "1",
                                "AIRULESET_VAULT_READ_AUDIT": str(log)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return log

    def test_a_written_value_does_not_reach_the_log_in_the_clear(self):
        log = self._bypass("echo '%s' > %s/DB_PASS.secret" % (self.FAKE, STORE))
        self.assertNotIn(self.FAKE, log.read_text(),
                         "the guard's own audit log is now the second place "
                         "the value comes to rest")

    def test_a_value_passed_as_an_argument_does_not_reach_it_either(self):
        log = self._bypass("printf %%s '%s' | tee %s/DB_PASS.secret"
                           % (self.FAKE, STORE))
        self.assertNotIn(self.FAKE, log.read_text())

    def test_the_trail_still_identifies_what_was_touched(self):
        # Redaction that destroys the audit trail would trade one defect for
        # another: the line exists to prove a bypass happened and against what.
        log = self._bypass("cat %s/DB_PASS.secret" % STORE)
        body = log.read_text()
        self.assertIn("env-bypass", body)
        self.assertIn("DB_PASS.secret", body)      # the NAME, never a value
        self.assertIn("Bash", body)                 # which tool was bypassed

    def test_the_same_command_fingerprints_the_same_way(self):
        # Correlating two entries is what the raw text used to be good for.
        a = self._bypass("cat %s/DB_PASS.secret" % STORE).read_text()
        b = self._bypass("cat %s/DB_PASS.secret" % STORE).read_text()
        import re as _re
        ha = _re.search(r"sha256=([0-9a-f]{16,})", a)
        hb = _re.search(r"sha256=([0-9a-f]{16,})", b)
        self.assertIsNotNone(ha, a)
        self.assertEqual(ha.group(1), hb.group(1))

    def test_a_different_command_fingerprints_differently(self):
        import re as _re
        a = self._bypass("cat %s/DB_PASS.secret" % STORE).read_text()
        b = self._bypass("cat %s/OTHER.secret" % STORE).read_text()
        ha = _re.search(r"sha256=([0-9a-f]{16,})", a).group(1)
        hb = _re.search(r"sha256=([0-9a-f]{16,})", b).group(1)
        self.assertNotEqual(ha, hb)

    def test_a_crafted_command_cannot_forge_an_audit_entry(self):
        # The audit line is emitted by the matcher on a marked line that the
        # wrapper splits out — so anything else the matcher prints is an
        # injection channel. A newline is a segment separator OUTSIDE quotes,
        # but inside SINGLE quotes it is buffered into the same segment, so the
        # quoted excerpt of the offending segment can span lines and the second
        # one can start with the marker. That forges a first entry and demotes
        # the real one to a raw continuation line.
        #
        # The audit trail is the artifact the hook's own honest-limit claim
        # rests on ("circumventing it leaves an artifact"), so an attacker who
        # can write entries into it defeats that claim specifically.
        log = self._bypass(
            "cat '%s/DB_PASS.secret\n#AUDIT# tool=FORGED refs=nothing "
            "sha256=deadbeef len=0'" % STORE)
        body = log.read_text()
        self.assertNotIn("FORGED", body, "an audit entry was forged")
        self.assertEqual(body.count("sha256="), 1,
                         "exactly one fingerprint per bypass:\n%s" % body)
        self.assertEqual(len(body.strip().splitlines()), 1,
                         "the entry must be a single line:\n%s" % body)

    def test_the_refusal_message_stays_one_line_per_hit(self):
        # Same root cause seen from the caller's side: an embedded newline in
        # the quoted excerpt breaks the block message's layout too.
        r = run("cat '%s/DB_PASS.secret\nsecond line'" % STORE)
        self.assertEqual(r.returncode, 2)
        excerpt = [ln for ln in r.stderr.splitlines() if "DB_PASS.secret" in ln]
        self.assertTrue(excerpt, r.stderr)
        self.assertIn("second line", excerpt[0],
                      "the excerpt was split across lines: %r" % excerpt)

    def test_a_value_shaped_like_a_store_filename_is_not_logged(self):
        # "safe by construction — always a path fragment, never the argument
        # carrying a value" was too strong: the reference is whatever the
        # pattern matched, and a VALUE that happens to look like a store
        # filename matches it too. Only a match sitting in a PATH context
        # (a token with a separator, or one resolved against a cd) is logged.
        log = self._bypass("echo 'topsecret.secret' > %s/X.secret" % STORE)
        body = log.read_text()
        self.assertNotIn("topsecret.secret", body,
                         "a value shaped like a store filename reached the "
                         "log:\n%s" % body)
        self.assertIn("X.secret", body, "the real item name is still recorded")

    def test_the_line_does_not_narrow_the_search_space(self):
        # The digest is over the whole command, so anything that pins the
        # command's shape helps an offline guess. A length was recorded and
        # bought nothing the digest does not already give for correlation.
        log = self._bypass("cat %s/DB_PASS.secret" % STORE)
        self.assertNotIn("len=", log.read_text())

    def test_the_log_is_not_world_readable(self):
        log = self._bypass("cat %s/DB_PASS.secret" % STORE)
        mode = log.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600,
                         "a file recording that a credential was touched must "
                         "not inherit the ambient umask, got %o" % mode)


class AMalformedPayloadMustNotFailOpen(unittest.TestCase):
    """#156 hole 2. Claim (e) of the shipping ticket was "fail-closed"; for the
    payload path it was false.

    `except Exception: payload = {}` assigned a DICT, and the raw-text fallback
    below it then tested `not isinstance(payload, dict)` — statically always
    False on the very path it was written for. So a payload the hook could not
    parse produced no command, no tool fields, and exit 0.
    """

    def assertFailsClosed(self, payload_text, label):
        r = run_payload_text(payload_text)
        self.assertEqual(r.returncode, 2,
                         "a payload the guard cannot parse must not open the "
                         "store (%s)\nstdout=%s\nstderr=%s"
                         % (label, r.stdout, r.stderr))
        self.assertIn("fail-closed", r.stderr)

    def test_unparseable_text(self):
        self.assertFailsClosed("cat ~/.claude/secrets/DB_PASS.secret",
                               "raw text, not JSON")

    def test_truncated_json(self):
        self.assertFailsClosed(
            '{"tool_name":"Bash","tool_input":{"command":"cat ~/.cl',
            "truncated mid-string")

    def test_a_json_scalar_is_not_a_payload(self):
        self.assertFailsClosed("5", "parses, but is not an object")

    def test_a_json_list_is_not_a_payload(self):
        self.assertFailsClosed('["tool_input"]', "parses, but is not an object")

    def test_a_string_tool_input_is_still_inspected(self):
        # This one is understandable input in an unexpected SHAPE, not
        # unparseable input — so it is scanned rather than failed closed.
        # Failing closed on an unknown-but-valid tool would deny every call
        # to it; allowing it unscanned is what the ticket measured.
        r = run_payload({"tool_name": "Bash",
                         "tool_input": "cat ~/.claude/secrets/DB_PASS.secret"})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_string_tool_input_with_nothing_to_hide_is_allowed(self):
        r = run_payload({"tool_name": "Bash", "tool_input": "git status"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_list_shaped_tool_input_is_still_inspected(self):
        # The string branch is inspected; a LIST fell through to `tin = {}`,
        # `cmd = ""` and exited 0 having looked at nothing. Same class of
        # unexpected-but-understandable shape, opposite treatment.
        r = run_payload({"tool_name": "Bash",
                         "tool_input": ["cat %s/DB_PASS.secret" % STORE]})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_nested_list_payload_is_still_inspected(self):
        r = run_payload({"tool_name": "Read",
                         "tool_input": {"file_path": ["%s/DB_PASS.secret" % ABS]}})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_list_shaped_tool_input_with_nothing_to_hide_is_allowed(self):
        r = run_payload({"tool_name": "Bash", "tool_input": ["git status"]})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_dead_fallback_is_gone(self):
        # The bug was a CONJUNCTION that could never be true, not the type test
        # itself — a top-level `if not isinstance(payload, dict)` guard is the
        # correct fix and legitimately contains that token. Forbidding the bare
        # token would be a lock that no correct implementation can satisfy.
        dead = "not cmd and not isinstance(payload, dict)"
        offenders = [i for i, ln in enumerate(HOOK.read_text().splitlines(), 1)
                     if dead in ln]
        self.assertEqual(offenders, [],
                         "the unreachable fallback is back at line(s) %s"
                         % offenders)


class RefusalQuality(unittest.TestCase):
    def test_the_refusal_points_at_secret_exec(self):
        r = run("cat %s/DB_PASS.secret" % STORE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("secret exec", r.stderr)

    def test_the_refusal_states_its_own_limit(self):
        # The ticket's explicit instruction: do not imply a stronger guarantee
        # than is delivered. The agent's uid has NOPASSWD sudo on these boxes,
        # so uid separation is unachievable and this hook is a guardrail.
        r = run("cat %s/DB_PASS.secret" % STORE)
        self.assertRegex(r.stderr.lower(), r"guardrail|not a (security )?boundary")

    def test_it_fails_closed_when_the_check_itself_breaks(self):
        # A malfunctioning guard must not silently open the store. The failure
        # injected is the REAL one — python3 (which runs the matcher) missing
        # from PATH, with bash itself still reachable so the hook actually runs
        # and has to decide. Reading the payload with a shell builtin instead
        # of `cat` is what makes that distinguishable from "no payload".
        with tempfile.TemporaryDirectory() as empty:
            r = run("cat %s/DB_PASS.secret" % STORE,
                    env_extra={"PATH": empty})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-closed", r.stderr)


class DocumentedGaps(unittest.TestCase):
    """Honest scope. These are NOT caught, and the hook says so rather than
    letting a reader assume the store is sealed."""

    def test_the_hook_documents_what_it_cannot_see(self):
        text = HOOK.read_text()
        self.assertIn("KNOWN GAPS", text)
        for gap in ("sudo", "computed", "settings.json"):
            with self.subTest(gap=gap):
                self.assertIn(gap, text)

    def test_authoring_then_running_is_declined_in_writing(self):
        # #156 hole 3. `Write` a reader script, then `bash` it: both halves are
        # ALLOW and the ticket asked for this to be DECIDED, not left implied.
        # It is declined — so the header must say so, and say why, rather than
        # leaving a reader to infer coverage that does not exist.
        text = HOOK.read_text()
        self.assertIn("Write", text)
        for word in ("authoring", "its own tests", "runtime"):
            with self.subTest(word=word):
                self.assertIn(word, text)

    def test_the_glob_residual_is_named_not_implied(self):
        # The specific asymmetry #156 reported: the header admitted runtime
        # assembly and wrappers while silently omitting globbing. Whatever is
        # left open must be spelled the way someone would type it.
        text = HOOK.read_text()
        for spelling in ("*ecrets", "[s]ecrets"):
            with self.subTest(spelling=spelling):
                self.assertIn(spelling, text)

    def test_the_audit_log_gap_matches_what_is_written(self):
        # The KNOWN GAPS entry used to promise the opposite of the fix (#157).
        stale = "The audit line records the full command"
        offenders = [i for i, ln in enumerate(HOOK.read_text().splitlines(), 1)
                     if stale in ln]
        self.assertEqual(offenders, [],
                         "the header still promises the full command at "
                         "line(s) %s" % offenders)

    def test_the_write_edit_extension_is_documented(self):
        # #154: the header must say it now also guards Write/Edit, and why
        # (a template file lives in the same dir), not leave it implied by
        # the wiring alone.
        text = HOOK.read_text()
        self.assertIn("#154", text)
        self.assertIn("template", text.lower())
        self.assertIn("Write", text)
        self.assertIn("Edit", text)


class ToolsOtherThanBash(unittest.TestCase):
    """Adversarial review finding F1 (CRITICAL) — Bash was never the most
    reflexive route to the store.

    An agent asked what is in the store reaches for the `Read` TOOL long before
    it reaches for `cat`, and a prompt-injected one has a route that no
    Bash-matched hook can see. The original hook header called this "outside
    its reach by construction", which was a design choice presented as a law:
    Claude Code matches PreToolUse per tool name, so Read/Grep/Glob can be
    matched exactly as Bash is.
    """

    def assertBlocked(self, obj):
        r = run_payload(obj)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for payload: %s\nstderr=%s" % (obj, r.stderr))
        # rc==2 ALONE cannot tell a real refusal from a crashed check: the
        # fail-closed branch exits 2 as well, by design. So every one of these
        # passed while the whole tool branch was dying on an exception. Assert
        # the DECISION, not just the exit code.
        self.assertNotIn("fail-closed", r.stderr,
                         "the check crashed and fail-closed caught it; the "
                         "block is real but the guard is broken:\n%s" % r.stderr)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertIn("secret exec", r.stderr,
                      "a real refusal points at the sanctioned route")

    def assertAllowed(self, obj):
        r = run_payload(obj)
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for payload: %s\nstderr=%s" % (obj, r.stderr))

    # --- Read ---------------------------------------------------------------
    def test_read_tool_on_a_value_file(self):
        self.assertBlocked({"tool_name": "Read",
                            "tool_input": {"file_path": "%s/DB_PASS.secret" % ABS}})

    def test_read_tool_on_the_store_dir(self):
        self.assertBlocked({"tool_name": "Read",
                            "tool_input": {"file_path": "%s/anything" % ABS}})

    def test_read_tool_on_an_ordinary_file(self):
        self.assertAllowed({"tool_name": "Read",
                            "tool_input": {"file_path": "/home/newlevel/.claude/settings.json"}})

    # --- Grep ---------------------------------------------------------------
    def test_grep_tool_pointed_at_the_store(self):
        self.assertBlocked({"tool_name": "Grep",
                            "tool_input": {"pattern": ".", "path": ABS,
                                           "output_mode": "content"}})

    def test_grep_tool_with_a_secret_glob(self):
        self.assertBlocked({"tool_name": "Grep",
                            "tool_input": {"pattern": ".", "path": "/home/newlevel/.claude",
                                           "glob": "*.secret"}})

    def test_grep_SEARCHING_FOR_the_path_is_not_a_read(self):
        # The search PATTERN is not a path. Blocking on it would make the guard
        # unusable in the very repo that maintains it.
        self.assertAllowed({"tool_name": "Grep",
                            "tool_input": {"pattern": "\\.claude/secrets",
                                           "path": "/home/newlevel/devel/airuleset/hooks"}})

    # --- Glob ---------------------------------------------------------------
    def test_glob_tool_enumerating_value_files(self):
        # Here the PATTERN genuinely is a path pattern.
        self.assertBlocked({"tool_name": "Glob",
                            "tool_input": {"pattern": "**/*.secret",
                                           "path": "/home/newlevel/.claude"}})

    def test_glob_tool_on_an_unrelated_tree(self):
        self.assertAllowed({"tool_name": "Glob",
                            "tool_input": {"pattern": "**/*.py",
                                           "path": "/home/newlevel/devel/airuleset"}})

    def test_a_tool_bypass_is_audited_like_a_bash_one(self):
        # The audit trail must cover the route an agent reaches for FIRST.
        # This is also the assertion that would have caught the crash: a
        # broken branch writes no line at all.
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        log = Path(td) / "a.log"
        r = run_payload({"tool_name": "Read",
                         "tool_input": {"file_path": "%s/DB_PASS.secret" % ABS}},
                        env_extra={"AIRULESET_ALLOW_VAULT_READ": "1",
                                   "AIRULESET_VAULT_READ_AUDIT": str(log)})
        self.assertEqual(r.returncode, 0, r.stderr)
        body = log.read_text()
        self.assertIn("tool=Read", body)
        self.assertIn("DB_PASS.secret", body)
        self.assertIn("sha256=", body)

    # --- Write / Edit (#154) -------------------------------------------------
    # A `<name>.template` command-lock file lives in the SAME `.claude/secrets/`
    # dir a value file does, so it is caught by the SAME `file_path`-scanning
    # branch above — no Python change, only the settings/hooks.json wiring.
    def test_write_tool_on_a_value_file(self):
        self.assertBlocked({"tool_name": "Write",
                            "tool_input": {"file_path": "%s/DB_PASS.secret" % ABS,
                                           "content": "x"}})

    def test_write_tool_on_a_template_file(self):
        self.assertBlocked({"tool_name": "Write",
                            "tool_input": {"file_path": "%s/DB_PASS.template" % ABS,
                                           "content": "psql -h host"}})

    def test_write_tool_on_the_store_dir(self):
        self.assertBlocked({"tool_name": "Write",
                            "tool_input": {"file_path": "%s/anything" % ABS,
                                           "content": "x"}})

    def test_write_tool_on_an_ordinary_file_is_allowed(self):
        self.assertAllowed(
            {"tool_name": "Write",
             "tool_input": {"file_path": "/home/newlevel/devel/airuleset/scratch.md",
                            "content": "hi"}})

    def test_edit_tool_on_a_template_file(self):
        self.assertBlocked({"tool_name": "Edit",
                            "tool_input": {"file_path": "%s/DB_PASS.template" % ABS,
                                           "old_string": "a", "new_string": "b"}})

    def test_edit_tool_on_a_value_file(self):
        self.assertBlocked({"tool_name": "Edit",
                            "tool_input": {"file_path": "%s/DB_PASS.secret" % ABS,
                                           "old_string": "a", "new_string": "b"}})

    def test_edit_tool_on_an_ordinary_file_is_allowed(self):
        self.assertAllowed(
            {"tool_name": "Edit",
             "tool_input": {"file_path": "/home/newlevel/devel/airuleset/scratch.md",
                            "old_string": "a", "new_string": "b"}})

    def test_a_write_bypass_is_audited_too(self):
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        log = Path(td) / "a.log"
        r = run_payload({"tool_name": "Write",
                         "tool_input": {"file_path": "%s/DB_PASS.template" % ABS,
                                        "content": "psql -h host"}},
                        env_extra={"AIRULESET_ALLOW_VAULT_READ": "1",
                                   "AIRULESET_VAULT_READ_AUDIT": str(log)})
        self.assertEqual(r.returncode, 0, r.stderr)
        body = log.read_text()
        self.assertIn("tool=Write", body)
        # VALUE_FILE_RE only ever recognises the `.secret` extension (by
        # design, #165) — a `.template` reference is caught by the
        # DIRECTORY rule (A) instead, so the trail records the dir, not
        # the specific stem. Still a real, non-empty audit line, which is
        # what this asserts.
        self.assertIn(".claude/secrets", body)


class PerformanceIsBoundedFarBelowTheHarnessTimeout(unittest.TestCase):
    """#162 — a hook that TIMES OUT (settings/hooks.json: timeout=5) is treated
    by Claude Code as a non-blocking error, so the guarded command runs. The
    architectural gap (a harness-level kill mid-python3-start) cannot be closed
    from inside the script — but reachable trigger REGEXES can be, and round 1
    of this ticket closed the then-DOMINANT one: VALUE_FILE_RE (`hooks/block-
    vault-store-read.sh`, see its own comment right above its definition) had a
    stem class overlapping the literal it searches for (`.secret` starts with
    characters the stem class itself accepts), so `.search()`/`.finditer()`
    over a long run with no `.secret` anywhere forced a full greedy-then-
    backtrack sweep at every start offset — O(n^2). Measured on dev1 before the
    fix: 50 KB -> 10,497 ms, 100 KB -> 54,643 ms, both far past the 5,000 ms
    budget, from an entirely ordinary long argument (a base64 blob, an
    embedded file body) with no glob, no exploit shape, nothing hidden.

    A round-2 adversarial review found the IDENTICAL shape, unbounded, in two
    more of this file's own regexes (BRACE_RE, RECURSIVE_RE — see
    BraceAndRecursiveRegexesAreAlsoBounded below) and found BRACE_RE reachable
    at a MUCH smaller size (4KB). Both are now bounded the same way.

    This test uses a 50KB payload (not the ticket's original 18KB) for a wide
    margin in BOTH directions — an earlier 18KB/1.5s version had only a 2.3%
    RED margin on this box (an adversarial review finding, #162 round 2):
    pre-fix ~10.5s vs the bound below is a wide RED margin, and post-fix this
    payload normally completes in well under 500ms. BOUND_S is deliberately
    set close to (but under) the hook's own 5s harness timeout rather than
    the sub-second figure post-fix normally shows — this dev1 box routinely
    runs many concurrent Claude sessions, and a tight bound flaked under
    real, observed heavy load (a genuine >20 load average on 4 cores) even
    though the regex fix itself was correctly applied; the WIDE bound still
    catches the real O(n^2) blowup (>2x the bound even under that same
    load) while not flaking on ordinary system contention.
    """

    BOUND_S = 4.5

    def test_a_long_literal_argument_completes_well_under_the_timeout(self):
        cmd = "cat " + "x" * 50000
        start = time.perf_counter()
        r = run(cmd)
        elapsed = time.perf_counter() - start
        self.assertLess(
            elapsed, self.BOUND_S,
            "hook took %.3fs on a 50KB literal argument — this alone is close "
            "to (or past) the 5s harness timeout on a real box under load, "
            "which is the #162 fail-open this test exists to prevent"
            % elapsed)
        # And the verdict must still be correct — a long literal with no store
        # reference anywhere is genuinely allowed, not accidentally blocked.
        self.assertEqual(r.returncode, 0)


class BraceAndRecursiveRegexesAreAlsoBounded(unittest.TestCase):
    """#162 round 2 — an adversarial review of round 1's VALUE_FILE_RE fix found
    the IDENTICAL overlapping-class-vs-literal-suffix ReDoS shape, still
    unbounded, in two more of this hook's own regexes: BRACE_RE
    (`expand_braces`) and RECURSIVE_RE (`sweeps_the_parent`). Both `[^{}]*`/
    `[A-Za-z]*` groups accept the literal character(s) the pattern must find
    next (`,`/`}` for BRACE_RE, `r`/`R` for RECURSIVE_RE), so a long run with
    no match anywhere forces the same full greedy-then-backtrack sweep.

    BRACE_RE was found MORE reachable than VALUE_FILE_RE's own gap, not less:
    `expand_braces` runs on every token of every command (not gated behind an
    earlier miss), so a 4KB unclosed brace alone already exceeded the 5s
    budget — an order of magnitude smaller than VALUE_FILE_RE's 50KB trigger.
    RECURSIVE_RE needs a bare `.claude`/glob-of-it already present in the
    command (gated behind CLAUDE_ROOT_RE/globbed_parent_ref), so its
    reachability is lower, but the underlying regex defect is identical.

    Measured on dev1 before this round's fix (`{0,254}`/`{0,254}` per side):
    a 24KB unclosed brace -> 4.05-7.03s, a 24KB `grep -rrr...= ~/.claude` ->
    10.4-14.0s, both closing on the 5s harness timeout on their own. Post-fix
    both normally complete in well under 300ms; BOUND_S is set close to (but
    under) the hook's own 5s timeout rather than that sub-second figure for
    the same real-load reason PerformanceIsBoundedFarBelowTheHarnessTimeout
    (above) states — this box routinely runs many concurrent Claude
    sessions, and a tight bound flaked under genuinely observed heavy load.
    """

    BOUND_S = 4.5

    def test_an_unclosed_brace_completes_well_under_the_timeout(self):
        # `expand_braces` sees this as ONE token (no whitespace) via
        # TOKEN_RE — a long run of comma-separated non-brace text with no
        # closing `}` anywhere, the exact BRACE_RE overlap shape.
        cmd = "echo {" + "c," * 12000  # 24006 chars
        start = time.perf_counter()
        r = run(cmd)
        elapsed = time.perf_counter() - start
        self.assertLess(
            elapsed, self.BOUND_S,
            "hook took %.3fs on a 24KB unclosed-brace argument — the #162 "
            "round-2 BRACE_RE ReDoS gap this test exists to prevent"
            % elapsed)
        # No real store reference anywhere — genuinely allowed.
        self.assertEqual(r.returncode, 0)

    def test_a_long_recursive_flag_run_completes_well_under_the_timeout(self):
        # RECURSIVE_RE only runs once CLAUDE_ROOT_RE has already matched a
        # bare `.claude` in the command — this reproduces that gate plus a
        # long run of `r` characters with NO reachable whitespace boundary
        # (an `=` immediately follows, not a space), the exact shape that
        # forces exhaustive double-backtracking pre-fix: for EVERY position
        # where `[rR]` could match inside the run, the trailing `[A-Za-z]*`
        # also backtracks fully before the whole alternative fails — O(n^2).
        # The verdict is "not a recognised recursive flag" (rc=0) BOTH
        # before and after the fix; only the TIME changes, exactly like the
        # sibling VALUE_FILE_RE/BRACE_RE performance tests in this file.
        cmd = "grep -" + "r" * 24000 + "= " + "~" + "/" + "." + "claude"
        start = time.perf_counter()
        r = run(cmd)
        elapsed = time.perf_counter() - start
        self.assertLess(
            elapsed, self.BOUND_S,
            "hook took %.3fs on a 24KB recursive-flag argument — the #162 "
            "round-2 RECURSIVE_RE ReDoS gap this test exists to prevent"
            % elapsed)
        self.assertEqual(r.returncode, 0)


class RecursiveFlagBoundHasOneNarrowDocumentedResidual(unittest.TestCase):
    """#162 round 2 — RECURSIVE_RE's two `{0,254}` bounds create the SAME
    class of narrow reachability residual VALUE_FILE_RE's stem bound has
    (see ValueFileStemBoundHasOneNarrowDocumentedResidual above), found
    while writing this round's own regression tests rather than by the
    dispatched review: a homogeneous run of `r`/`R` characters immediately
    followed by whitespace matches only up to 509 total characters after
    the leading `-` (254 + the required `[rR]` + 254) — the unbounded
    original had no such ceiling and matched a run of ANY length. Past 509,
    `sweeps_the_parent`'s ONLY recursive-flag detector for a head outside
    BULK_HEADS/TREE_WALK_HEADS stops firing, so a deliberately-obfuscated
    flag cluster of 510+ repeated `r` characters (`cp -rrrr...(510 r's)
    ~/.claude/x /tmp` — syntactically a normal, if absurd, combined
    short-flag cluster to most CLI tools) evades the recursive-sweep check
    entirely. Accepted as a KNOWN GAPS residual rather than widened further:
    no real flag cluster typed by reflex (or even deliberately, short of
    this exact obfuscation) is remotely 509 characters long, and rule A
    still catches any command whose value/store path is named literally
    inside such a sweep. These tests lock the exact boundary.
    """

    def _cmd(self, r_count):
        return "grep -" + "r" * r_count + " " + "~" + "/" + "." + "claude"

    def test_a_509_character_recursive_flag_still_blocks(self):
        r = run(self._cmd(509))
        self.assertEqual(r.returncode, 2,
                         "a 509-char -r run is within the {0,254}x2 bound "
                         "and must still be blocked as a recursive sweep")

    def test_a_510_character_recursive_flag_is_the_documented_residual(self):
        r = run(self._cmd(510))
        self.assertEqual(r.returncode, 0,
                         "a 510-char -r run is one past the reachability "
                         "ceiling and is the documented #162 round-2 "
                         "residual — must stay unblocked unless the bound "
                         "is deliberately widened")


class ValueFileStemBoundHasOneNarrowDocumentedResidual(unittest.TestCase):
    """#162 round 2 — VALUE_FILE_RE's stem quantifier is bounded to `{0,253}`
    (1 required leading char + 253 more = 254 total stem chars max before the
    literal `.secret`). An adversarial review found and reproduced the ONE
    real behavioural difference from the unbounded original: a stem whose
    final 254 characters before `.secret` contain NO alnum/underscore
    character at all has nowhere for the required leading `[A-Za-z0-9_]` to
    anchor within the bounded window, so the match does NOT fire — the
    unbounded original always found an earlier alnum character no matter how
    far back it sat. This is documented as an accepted, narrow KNOWN GAP (see
    the hook's own header comment) rather than fixed further, because no stem
    shaped this way can ever name a real value file anyway (it already
    exceeds Linux's own NAME_MAX). These tests lock the EXACT boundary so a
    future retuning of the bound is a deliberate, visible change.
    """

    def _cmd(self, dash_count):
        # "a" (the one alnum lead) + N dashes + ".secret" -> stem length is
        # dash_count + 1.
        return "cat a" + "-" * dash_count + "." + "secret"

    def test_a_stem_of_exactly_254_characters_still_blocks(self):
        # 253 dashes + the leading "a" = 254 total stem chars -- exactly at
        # the bound ({0,253} allows up to 253 MORE after the required lead).
        r = run(self._cmd(253))
        self.assertEqual(r.returncode, 2,
                         "a 254-char stem is within the {0,253} bound and "
                         "must still be blocked")

    def test_a_stem_of_255_characters_is_the_documented_residual(self):
        # 254 dashes + the leading "a" = 255 total stem chars -- one past
        # the bound, and every character after the lead is non-alnum, so no
        # later start position can anchor either. This is the exact,
        # accepted KNOWN GAPS residual, locked here so it stays deliberate.
        r = run(self._cmd(254))
        self.assertEqual(r.returncode, 0,
                         "a 255-char all-non-alnum-tail stem is the "
                         "documented #162 round-2 residual and must stay "
                         "unblocked unless the bound is deliberately widened")

    def test_a_stem_of_300_characters_is_the_same_documented_residual(self):
        # The adversarial review's own reproduction size, well past the
        # boundary -- confirms the residual isn't a one-character fluke.
        r = run(self._cmd(300))
        self.assertEqual(r.returncode, 0)


class Registered(unittest.TestCase):
    def _pretooluse(self, matcher):
        cfg = json.loads((Path(__file__).resolve().parent.parent
                          / "settings" / "hooks.json").read_text())
        return [h["command"]
                for entry in cfg["hooks"]["PreToolUse"]
                if entry.get("matcher") == matcher
                for h in entry["hooks"]]

    def test_it_is_wired_into_the_pretooluse_bash_chain(self):
        bash_hooks = self._pretooluse("Bash")
        self.assertTrue(any("block-vault-store-read.sh" in c for c in bash_hooks),
                        "a hook nothing runs is not an artifact: %s" % bash_hooks)

    def test_it_is_wired_for_the_file_reading_tools_too(self):
        # EXACT tool-name matchers, one entry each — this repo registers Write
        # and Edit separately for the same reason: an alternation regex has
        # been observed to silently never match, and a guard that never runs
        # is worse than no guard because it reads as coverage.
        for matcher in ("Read", "Grep", "Glob"):
            with self.subTest(matcher=matcher):
                cmds = self._pretooluse(matcher)
                self.assertTrue(
                    any("block-vault-store-read.sh" in c for c in cmds),
                    "no vault guard registered for %s: %s" % (matcher, cmds))

    def test_it_is_wired_for_write_and_edit_too(self):
        # #154: a `<name>.template` command-lock file lives in this SAME
        # guarded directory — an agent's reflexive Write/Edit against it
        # needs the identical refusal a value file already had. Both
        # matcher blocks already exist in settings/hooks.json (for
        # pre-write-script-check.sh / block-main-implementation.sh); this
        # is one more entry in each, not a new hook file.
        for matcher in ("Write", "Edit"):
            with self.subTest(matcher=matcher):
                cmds = self._pretooluse(matcher)
                self.assertTrue(
                    any("block-vault-store-read.sh" in c for c in cmds),
                    "no vault guard registered for %s: %s" % (matcher, cmds))


if __name__ == "__main__":
    unittest.main()
