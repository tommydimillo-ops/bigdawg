"""Tests for agent/agents/coding.py's REAL execution path (Phase 10
increment 1, config.settings.coding_agent_enabled=True). Mocks only the
external Anthropic client call, per this project's "mock at the
external-call boundary" convention -- everything else (git checkpoint/
restore, real file reads/writes, a real test-suite subprocess run) is
real, against a throwaway fixture repository built fresh per test, never
the actual CampusPilot repo. `agent.agents.coding._PROJECT_ROOT` is
patched to point at that fixture repo for every test in
CodingAgentEnabledTestCase.

Run with: python -m unittest tests.test_agents_coding_enabled -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import agent.agents.coding as coding
import agent.usage as usage
from agent.agents.coding import CodingAgent
from agent.request_context import RequestContext
from config.settings import Settings, settings


def _git(repo, *args):
    result = subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _tool_use_block(name, tool_input, block_id="tool-1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = tool_input
    block.id = block_id
    return block


def _response(content_blocks, stop_reason):
    response = MagicMock(stop_reason=stop_reason)
    response.content = content_blocks
    response.usage = MagicMock(input_tokens=10, output_tokens=5)
    return response


class TestDisabledByDefault(unittest.TestCase):
    def test_settings_default_is_false(self):
        self.assertFalse(Settings.coding_agent_enabled)


class TestTimeoutBudgetCoversTheLoopsOwnWorstCase(unittest.TestCase):
    """Real gap found by code review, not a live incident:
    coding_agent_timeout_seconds must genuinely exceed the loop's own
    worst-case inner budget, not just be "a bigger number" than the
    shared agent_timeout_seconds. agent.agents.manager's real timeout
    path is an immediate proc.kill(), no grace period -- unlike the
    cooperative-cancellation path -- so a mismatch here means the whole
    worker subprocess, including CodingAgent's own checkpoint rollback
    and pruning in execute()'s try/except/finally, gets SIGKILLed before
    any of it runs. This test recomputes the real worst case from the
    actual constants (not a hand-copied number) so a future change to
    any of them that breaks the relationship fails here, not silently in
    production."""

    def test_outer_timeout_exceeds_the_real_worst_case_inner_budget(self):
        worst_case_per_iteration = coding._MODEL_CALL_TIMEOUT_SECONDS + coding._TEST_SUITE_TIMEOUT_SECONDS
        worst_case_total = (
            coding.MAX_ITERATIONS * worst_case_per_iteration + coding._TEST_SUITE_TIMEOUT_SECONDS
        )
        self.assertGreater(
            settings.coding_agent_timeout_seconds, worst_case_total,
            "coding_agent_timeout_seconds must genuinely exceed the loop's own worst-case "
            "budget, or a real timeout SIGKILLs the whole subprocess before execute()'s own "
            "rollback/pruning logic ever runs",
        )


class TestParseTestSummary(unittest.TestCase):
    def test_all_passed(self):
        tests_run, tests_failed = coding._parse_test_summary("Ran 5 tests in 0.01s\n\nOK\n")
        self.assertEqual(tests_run, 5)
        self.assertEqual(tests_failed, 0)

    def test_some_failed(self):
        tests_run, tests_failed = coding._parse_test_summary(
            "Ran 5 tests in 0.01s\n\nFAILED (failures=2, errors=1)\n"
        )
        self.assertEqual(tests_run, 5)
        self.assertEqual(tests_failed, 3)

    def test_unparseable_returns_none_not_a_crash(self):
        tests_run, tests_failed = coding._parse_test_summary("garbage output\nwith no summary line")
        self.assertIsNone(tests_run)
        self.assertIsNone(tests_failed)


class TestRunTestSuite(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-run-test-suite-test-")
        os.makedirs(os.path.join(self.repo, "tests"))
        with open(os.path.join(self.repo, "tests", "__init__.py"), "w"):
            pass

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write_fixture_test(self, body):
        with open(os.path.join(self.repo, "tests", "test_fixture.py"), "w") as file:
            file.write(body)

    def test_passing_suite_reports_zero_exit_and_zero_failures(self):
        self._write_fixture_test("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        result = coding._run_test_suite(self.repo)
        self.assertEqual(result["suite_exit_code"], 0)
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["tests_failed"], 0)

    def test_failing_suite_reports_nonzero_exit(self):
        self._write_fixture_test(
            "import unittest\nclass T(unittest.TestCase):\n    def test_fail(self):\n        self.fail('nope')\n"
        )
        result = coding._run_test_suite(self.repo)
        self.assertNotEqual(result["suite_exit_code"], 0)
        self.assertEqual(result["tests_failed"], 1)


class TestWriteFileDirectly(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-write-file-test-")
        # A real repo, not a bare temp dir: in production _write_file only
        # ever runs after create_checkpoint succeeded (so root is always a
        # git repo), and _write_file now asks git whether the target is
        # gitignored and fails closed if git cannot answer.
        _git(self.repo, "init", "-q")
        # source="agent_worker" matches real usage (agent/agents/
        # worker.py); default autonomy_level (settings.autonomy_level,
        # 4) means these tests exercise the real "default config allows
        # it" path, same as before M10.0's gate existed -- a lower
        # autonomy_level's DENY behavior gets its own dedicated test
        # class below.
        self.context = RequestContext.create("test task", source="agent_worker")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_writes_a_new_file_and_records_it(self):
        files_written = []
        result = coding._write_file(self.repo, "new.txt", "hello", files_written, self.context)
        self.assertIn("Wrote", result)
        self.assertEqual(files_written, ["new.txt"])
        with open(os.path.join(self.repo, "new.txt")) as file:
            self.assertEqual(file.read(), "hello")

    def test_creates_parent_directories(self):
        files_written = []
        coding._write_file(self.repo, "new/nested/dir/file.txt", "hi", files_written, self.context)
        with open(os.path.join(self.repo, "new", "nested", "dir", "file.txt")) as file:
            self.assertEqual(file.read(), "hi")

    def test_refuses_a_denylisted_safety_file(self):
        files_written = []
        result = coding._write_file(self.repo, "agent/autonomy.py", "x = 1", files_written, self.context)
        self.assertIn("Error: refusing to write", result)
        self.assertEqual(files_written, [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "agent", "autonomy.py")))

    def test_refuses_every_denylisted_path(self):
        files_written = []
        for rel in coding._NEVER_WRITABLE_PATHS:
            result = coding._write_file(self.repo, rel, "x", files_written, self.context)
            self.assertIn("Error: refusing to write", result, f"expected {rel} to be refused")
        self.assertEqual(files_written, [])

    def test_refuses_a_denylisted_prefix(self):
        files_written = []
        result = coding._write_file(self.repo, ".github/workflows/tests.yml", "x", files_written, self.context)
        self.assertIn("Error: refusing to write", result)

    def test_refuses_a_denylisted_path_regardless_of_case(self):
        # Real, exploitable bypass found by code review: confine_to_repo
        # resolves via os.path.realpath, which does not correct case,
        # but macOS's default APFS volume is case-insensitive-but-
        # case-preserving -- 'Agent/Autonomy.py' and 'agent/autonomy.py'
        # are the SAME file on disk even though they're different
        # strings. A plain `rel in _NEVER_WRITABLE_PATHS` check (case-
        # sensitive by construction) let a differently-cased path sail
        # through and land on the real protected file.
        files_written = []
        for cased in ("Agent/Autonomy.py", "AGENT/AUTONOMY.PY", "agent/AutoNomy.py"):
            result = coding._write_file(self.repo, cased, "x", files_written, self.context)
            self.assertIn("Error: refusing to write", result, f"expected {cased} to be refused")
        self.assertEqual(files_written, [])

    def test_refuses_a_path_outside_the_repo(self):
        files_written = []
        result = coding._write_file(self.repo, "../outside.txt", "x", files_written, self.context)
        self.assertIn("Error:", result)
        self.assertEqual(files_written, [])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.repo), "outside.txt")))

    def test_refuses_a_path_inside_dot_git(self):
        files_written = []
        result = coding._write_file(self.repo, ".git/hooks/pre-commit", "malicious", files_written, self.context)
        self.assertIn("Error:", result)


class TestWriteFilePermissionGate(unittest.TestCase):
    """M10.0: agent/agents/coding.py's write path now routes through the
    SAME agent.autonomy.should_request_confirmation function agent/
    executor.py's _run_tool already calls, for every real write --
    confirming the chokepoint is actually wired in, not just built."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-write-gate-test-")
        _git(self.repo, "init", "-q")  # see TestWriteFileDirectly.setUp

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_default_autonomy_level_allows_the_write(self):
        context = RequestContext.create("t", source="agent_worker")  # autonomy_level defaults to settings.autonomy_level (4)
        files_written = []
        result = coding._write_file(self.repo, "new.txt", "hi", files_written, context)
        self.assertIn("Wrote", result)
        self.assertEqual(files_written, ["new.txt"])

    def test_low_autonomy_level_denies_the_write(self):
        context = RequestContext.create("t", source="agent_worker")
        context.autonomy_level = 1  # threshold 0 -- permission_level 2 needs confirmation
        files_written = []
        result = coding._write_file(self.repo, "new.txt", "hi", files_written, context)
        self.assertIn("Error: refusing to write", result)
        self.assertIn("autonomy level", result)
        self.assertEqual(files_written, [])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "new.txt")))

    def test_low_autonomy_level_never_hangs_waiting_for_a_confirmation_that_cannot_come(self):
        # The whole point of source="agent_worker" being in agent.
        # autonomy's non-interactive-sources set: a verdict that would
        # otherwise mean "pause and ask" must resolve immediately to a
        # denial, not block or silently proceed. This test's own
        # completion (it doesn't time out) is part of what it proves.
        context = RequestContext.create("t", source="agent_worker")
        context.autonomy_level = 0
        files_written = []
        result = coding._write_file(self.repo, "new.txt", "hi", files_written, context)
        self.assertIn("Error: refusing to write", result)


class TestWriteFileRefusesPathsNoCheckpointCanProtect(unittest.TestCase):
    """Plan-b7 item 1, closing the plan-b6 checkpoint audit's severe
    finding: create_checkpoint's `git add -A` honors .gitignore, so a
    write to an ignored path (the real .env above all) was permanent,
    unreported and unrollbackable. Every refusal is a hard, audited
    error, and the file is never touched."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-write-refuse-test-")
        _git(self.repo, "init", "-q")
        self.context = RequestContext.create("test task", source="agent_worker")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _assert_refused(self, rel, expect="Error: refusing to write"):
        files_written = []
        result = coding._write_file(self.repo, rel, "STOLEN", files_written, self.context)
        self.assertIn(expect, result, f"expected {rel} to be refused, got: {result}")
        self.assertEqual(files_written, [], f"{rel} must not be recorded as written")
        return result

    def test_refuses_every_explicit_never_writable_location(self):
        for rel in (
            ".env", ".env.local", ".env.production", ".env.example", "config/.env",
            "JarvisVault/Knowledge/Decisions/note.md", "logs/menubar.err.log",
            "graphify-out/graph.json", ".relay/plan-b8.md", ".venv/lib/python3/site.py",
            ".gitignore", "sub/dir/.gitignore",
        ):
            with self.subTest(rel=rel):
                self._assert_refused(rel)
                self.assertFalse(os.path.exists(os.path.join(self.repo, rel)), f"{rel} was created")

    def test_matching_is_case_insensitive(self):
        # Same reasoning as test_refuses_a_denylisted_path_regardless_of_case:
        # macOS's default volume is case-insensitive-but-case-preserving.
        for rel in (".ENV", ".Env.Local", "LOGS/x.log", "jarvisvault/x.md", "Graphify-Out/g.json", ".RELAY/p.md"):
            with self.subTest(rel=rel):
                self._assert_refused(rel)

    def test_a_refused_write_never_modifies_an_existing_secret(self):
        env_path = os.path.join(self.repo, ".env")
        with open(env_path, "w") as file:
            file.write("ANTHROPIC_API_KEY=real-looking-secret\n")
        self._assert_refused(".env")
        with open(env_path) as file:
            self.assertEqual(file.read(), "ANTHROPIC_API_KEY=real-looking-secret\n")

    def test_matching_is_on_the_resolved_path_so_a_symlink_cannot_launder_a_write(self):
        env_path = os.path.join(self.repo, ".env")
        with open(env_path, "w") as file:
            file.write("SECRET=1\n")
        os.makedirs(os.path.join(self.repo, "logs"))
        os.symlink(".env", os.path.join(self.repo, "notes.txt"))
        os.symlink("logs", os.path.join(self.repo, "innocent_dir"))

        self._assert_refused("notes.txt")
        self._assert_refused("innocent_dir/x.txt")
        with open(env_path) as file:
            self.assertEqual(file.read(), "SECRET=1\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "logs", "x.txt")))

    def test_refuses_any_other_gitignored_path_not_on_the_explicit_list(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("*.secret\nscratch/\nnested_ignored.txt\n")
        for rel in ("key.secret", "scratch/a.txt", "pkg/nested_ignored.txt"):
            with self.subTest(rel=rel):
                result = self._assert_refused(rel)
                self.assertIn("gitignored", result)
                self.assertFalse(os.path.exists(os.path.join(self.repo, rel)))

    def test_gitignored_preexisting_file_is_left_exactly_as_it_was(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("*.secret\n")
        with open(os.path.join(self.repo, "key.secret"), "w") as file:
            file.write("original\n")
        self._assert_refused("key.secret")
        with open(os.path.join(self.repo, "key.secret")) as file:
            self.assertEqual(file.read(), "original\n")

    def test_refuses_a_force_tracked_file_that_still_matches_an_ignore_rule(self):
        # The scratch index starts empty, so a tracked-but-ignored file is
        # still excluded from every snapshot -- "tracked" is not "covered".
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("forced.txt\n")
        with open(os.path.join(self.repo, "forced.txt"), "w") as file:
            file.write("x\n")
        _git(self.repo, "add", "-f", "forced.txt")
        self.assertIn("forced.txt", _git(self.repo, "ls-files"))
        self._assert_refused("forced.txt")

    def test_an_ordinary_non_ignored_path_is_still_writable(self):
        # The refusal must not have widened into refusing legitimate work.
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("*.secret\n")
        for rel in ("src/module.py", "tests/test_x.py", "docs/notes.md", "environment.md", "my.env.py"):
            with self.subTest(rel=rel):
                files_written = []
                result = coding._write_file(self.repo, rel, "ok", files_written, self.context)
                self.assertIn("Wrote", result)
                self.assertEqual(files_written, [rel])

    def test_fails_closed_when_git_cannot_answer(self):
        not_a_repo = tempfile.mkdtemp(prefix="jarvis-not-a-repo-")
        self.addCleanup(shutil.rmtree, not_a_repo, ignore_errors=True)
        files_written = []
        result = coding._write_file(not_a_repo, "ordinary.txt", "x", files_written, self.context)
        self.assertIn("Error: refusing to write", result)
        self.assertIn("could not verify", result)
        self.assertEqual(files_written, [])
        self.assertFalse(os.path.exists(os.path.join(not_a_repo, "ordinary.txt")))

    def test_every_refusal_is_audited_with_its_reason(self):
        from agent.audit import recent_actions

        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("*.secret\n")
        self._assert_refused(".env")
        self._assert_refused("key.secret")
        self._assert_refused("agent/autonomy.py")  # pre-existing denylist refusals are audited too

        # agent.audit stores each entry's input as a truncated string, not a dict.
        mine = [
            a for a in recent_actions(limit=100)
            if a["tool"] == "coding_agent_write_refused" and self.context.request_id in str(a["input"])
        ]

        def result_for(path):
            matches = [a["result"] for a in mine if f"'{path}'" in str(a["input"]) or f'"{path}"' in str(a["input"])]
            self.assertEqual(len(matches), 1, f"expected exactly one audit entry for {path}, got {mine}")
            return matches[0]

        self.assertIn("denylisted", result_for(".env"))
        self.assertIn("gitignored", result_for("key.secret"))
        self.assertIn("denylisted", result_for("agent/autonomy.py"))


class TestReadFileDirectly(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="jarvis-read-file-test-")
        with open(os.path.join(self.repo, "exists.txt"), "w") as file:
            file.write("real content")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_reads_an_existing_file(self):
        self.assertEqual(coding._read_file(self.repo, "exists.txt"), "real content")

    def test_missing_file_is_a_clear_error_not_a_crash(self):
        result = coding._read_file(self.repo, "does_not_exist.txt")
        self.assertIn("Error:", result)

    def test_refuses_a_path_outside_the_repo(self):
        result = coding._read_file(self.repo, "../../etc/passwd")
        self.assertIn("Error:", result)


class CodingAgentEnabledTestCase(unittest.TestCase):
    """Builds a throwaway git repo with its own tiny `tests/` fixture
    suite, so CodingAgent's real final-verification test run has
    something real -- but small and fully controlled -- to execute
    against. The fixture test checks that value.txt contains "42";
    mocked Claude tool calls are what actually determine whether that
    ends up true, false, or never attempted."""

    def setUp(self):
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

        self._real_enabled = settings.coding_agent_enabled
        object.__setattr__(settings, "coding_agent_enabled", True)

        self.repo = tempfile.mkdtemp(prefix="jarvis-coding-agent-test-")
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@test.com")
        _git(self.repo, "config", "user.name", "test")
        # Matches the real repo's own .gitignore (__pycache__/, *.pyc) --
        # without this, running the fixture suite as part of CodingAgent's
        # own verification step creates .pyc files that `git add -A`
        # would otherwise pick up as "changed", polluting
        # changed_paths_since/restore_paths with bytecode-cache noise
        # that has nothing to do with what the agent actually edited.
        # Caught for real: an earlier version of this fixture had no
        # .gitignore and every test below failed on exactly this.
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("__pycache__/\n*.pyc\n")
        os.makedirs(os.path.join(self.repo, "tests"))
        with open(os.path.join(self.repo, "tests", "__init__.py"), "w"):
            pass
        with open(os.path.join(self.repo, "tests", "test_dummy.py"), "w") as file:
            file.write(
                "import os, unittest\n"
                "class TestDummy(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        path = os.path.join(os.path.dirname(__file__), '..', 'value.txt')\n"
                "        with open(path) as f:\n"
                "            self.assertEqual(f.read().strip(), '42')\n"
            )
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "initial")

        self._project_root_patch = patch("agent.agents.coding._PROJECT_ROOT", self.repo)
        self._project_root_patch.start()

        self.agent = CodingAgent()
        self.context = RequestContext.create("write 42 to value.txt", source="test")

    def tearDown(self):
        self._project_root_patch.stop()
        object.__setattr__(settings, "coding_agent_enabled", self._real_enabled)
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write_fixture(self, relpath, content):
        with open(os.path.join(self.repo, relpath), "w") as file:
            file.write(content)


class TestSuccessfulEdit(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_writes_a_value_and_final_tests_pass(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_text_block("Wrote 42 to value.txt.")], "end_turn"),
        ]
        result = self.agent.execute("write 42 to value.txt", self.context)

        self.assertTrue(result.success)
        self.assertEqual(result.verification_status, "passed")
        self.assertEqual(result.metadata["suite_exit_code"], 0)
        self.assertEqual(result.metadata["changed_paths"], ["value.txt"])
        self.assertNotIn("rolled_back", result.metadata)
        with open(os.path.join(self.repo, "value.txt")) as file:
            self.assertEqual(file.read(), "42")

    @patch("agent.agents.coding.anthropic_client")
    def test_uses_its_own_longer_per_call_timeout_not_the_shared_default(self, mock_client):
        # Real dogfooding finding: this loop's own non-streaming calls
        # accumulate real file content into `messages` across iterations,
        # and hit the shared client's 25s default api_read_timeout for
        # real on a large context. Must be overridden per-call, not by
        # raising the shared default every other caller (chat,
        # ResearchAgent) also uses.
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        self.agent.execute("write 42 to value.txt", self.context)
        for call in mock_client.messages.create.call_args_list:
            self.assertIn("timeout", call.kwargs)
            self.assertGreater(call.kwargs["timeout"], 25)

    @patch("agent.agents.coding.anthropic_client")
    def test_does_not_touch_real_git_log_or_branches(self, mock_client):
        before_log = _git(self.repo, "log", "--oneline")
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        self.agent.execute("write 42 to value.txt", self.context)
        self.assertEqual(_git(self.repo, "log", "--oneline"), before_log)

    @patch("agent.agents.coding.anthropic_client")
    def test_model_calling_run_tests_mid_loop_works(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_tool_use_block("run_tests", {})], "tool_use"),
            _response([_text_block("Tests pass, done.")], "end_turn"),
        ]
        result = self.agent.execute("write 42 to value.txt", self.context)
        self.assertTrue(result.success)
        self.assertEqual(mock_client.messages.create.call_count, 3)


class TestFailingEditRollsBack(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_rolls_back_a_change_that_fails_tests(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "wrong"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        result = self.agent.execute("write 42 to value.txt", self.context)

        self.assertFalse(result.success)
        self.assertEqual(result.verification_status, "failed")
        self.assertNotEqual(result.metadata["suite_exit_code"], 0)
        self.assertTrue(result.metadata["rolled_back"])
        self.assertEqual(result.metadata["restored_paths"], ["value.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "value.txt")))

    @patch("agent.agents.coding.anthropic_client")
    def test_rollback_never_touches_git_log_or_branches_either(self, mock_client):
        before_log = _git(self.repo, "log", "--oneline")
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "wrong"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        self.agent.execute("write 42 to value.txt", self.context)
        self.assertEqual(_git(self.repo, "log", "--oneline"), before_log)


class TestRollbackFailureIsReportedNotLost(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_a_git_level_rollback_failure_keeps_the_verification_result_and_says_so(self, mock_client):
        # A stale/held .git/index.lock blocks `git restore`, which surfaces
        # as plain CheckpointError, not the CheckpointRestoreFailed
        # subclass. _attempt_rollback used to catch only the subclass, so
        # this escaped to execute()'s outer handler: a generic
        # "CheckpointError: ..." with verification_status and rolled_back
        # both gone. Real git failure, produced for real: the lock appears
        # between the test run and the rollback.
        # The agent must modify an already-TRACKED file: a brand-new file is
        # rolled back by os.remove and never reaches `git restore`, so it
        # would not exercise the failing path at all.
        self._write_fixture("notes.txt", "original\n")
        _git(self.repo, "add", "notes.txt")
        _git(self.repo, "commit", "-qm", "add notes")
        lock = os.path.join(self.repo, ".git", "index.lock")
        self.addCleanup(lambda: os.path.exists(lock) and os.remove(lock))
        real_suite = coding._run_test_suite

        def suite_then_hold_the_lock(repo_root):
            result = real_suite(repo_root)
            open(lock, "w").close()
            return result

        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "notes.txt", "content": "changed"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        with patch("agent.agents.coding._run_test_suite", side_effect=suite_then_hold_the_lock):
            result = self.agent.execute("write 42 to value.txt", self.context)

        self.assertFalse(result.success)
        self.assertEqual(result.verification_status, "failed")
        self.assertIs(result.metadata["rolled_back"], False)
        self.assertIn("index.lock", result.metadata["rollback_error"])
        self.assertIn("Could NOT roll back automatically", result.result)
        self.assertEqual(result.metadata["files_written"], ["notes.txt"])
        with open(os.path.join(self.repo, "notes.txt")) as file:
            self.assertEqual(file.read(), "changed")  # left as written, not silently lost or half-restored


class TestAgentCannotOverwriteAGitignoredSecret(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_write_to_env_is_refused_end_to_end_and_the_secret_is_untouched(self, mock_client):
        # .env is the case the plan-b6 audit found: gitignored, so the
        # checkpoint never covered it, and not on the denylist, so a
        # write succeeded silently and changed_paths_since reported
        # nothing changed. Ignored + already present => tree stays clean.
        with open(os.path.join(self.repo, ".gitignore"), "a") as file:
            file.write(".env\n")
        _git(self.repo, "commit", "-qam", "ignore .env")
        with open(os.path.join(self.repo, ".env"), "w") as file:
            file.write("ANTHROPIC_API_KEY=real-looking-secret\n")

        mock_client.messages.create.side_effect = [
            _response([
                _tool_use_block("write_file", {"path": ".env", "content": "OVERWRITTEN"}, "t1"),
                _tool_use_block("write_file", {"path": "value.txt", "content": "42"}, "t2"),
            ], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        result = self.agent.execute("write 42 to value.txt", self.context)

        with open(os.path.join(self.repo, ".env")) as file:
            self.assertEqual(file.read(), "ANTHROPIC_API_KEY=real-looking-secret\n")
        self.assertEqual(result.metadata["files_written"], ["value.txt"])
        self.assertTrue(result.success)
        tool_results = mock_client.messages.create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertTrue(any("Error: refusing to write" in str(item) for item in tool_results))


class TestConcurrentWriterProtection(CodingAgentEnabledTestCase):
    """Real gap found by code review, not a live incident:
    changed_paths_since is a pure tree diff, so on its own it cannot
    tell a concurrent, unrelated process's edit apart from this agent's
    own -- restoring the full diff on rollback would silently discard
    someone else's real work. Relay mode's own premise is a second,
    concurrent Claude Code session in this exact working tree, so this
    is not a theoretical scenario for this specific project."""

    @patch("agent.agents.coding.anthropic_client")
    def test_rollback_never_touches_a_path_the_agent_did_not_write(self, mock_client):
        concurrent_path = os.path.join(self.repo, "concurrent.txt")
        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Simulate a genuinely concurrent, unrelated process
                # writing to this same repo while CodingAgent's own loop
                # is still running.
                with open(concurrent_path, "w") as file:
                    file.write("written by someone else entirely\n")
                return _response(
                    [_tool_use_block("write_file", {"path": "value.txt", "content": "wrong"})], "tool_use",
                )
            return _response([_text_block("Done.")], "end_turn")

        mock_client.messages.create.side_effect = _side_effect
        result = self.agent.execute("write 42 to value.txt", self.context)

        self.assertFalse(result.success)
        # The tree diff correctly sees both changes -- that part is fine.
        self.assertIn("concurrent.txt", result.metadata["changed_paths"])
        self.assertIn("value.txt", result.metadata["changed_paths"])
        # But rollback must only ever touch what the agent itself wrote.
        self.assertNotIn("concurrent.txt", result.metadata.get("restored_paths", []))
        self.assertIn("value.txt", result.metadata.get("restored_paths", []))
        with open(concurrent_path) as file:
            self.assertEqual(file.read(), "written by someone else entirely\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "value.txt")))


class TestUncollectedTestFile(CodingAgentEnabledTestCase):
    """The real, dogfooding-found gap: a new test file using the wrong
    convention is collected by nothing, suite_exit_code stays 0, and
    nothing else in verify_agent_result's own checks would ever notice."""

    @patch("agent.agents.coding.anthropic_client")
    def test_flags_and_rolls_back_a_new_test_file_that_collects_nothing(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_tool_use_block("write_file", {
                "path": "tests/test_bad_convention.py",
                "content": "def test_something():\n    assert 1 == 1\n",
            })], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        result = self.agent.execute("add a test", self.context)

        self.assertFalse(result.success)
        self.assertEqual(result.verification_status, "failed")
        self.assertEqual(result.metadata["suite_exit_code"], 0)  # the suite itself is "green"
        self.assertIn("tests/test_bad_convention.py", result.metadata["uncollected_test_files"])
        self.assertIn("collects zero tests", result.result)
        self.assertTrue(result.metadata["rolled_back"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "value.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "tests", "test_bad_convention.py")))

    @patch("agent.agents.coding.anthropic_client")
    def test_a_properly_written_new_test_file_is_not_flagged(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_tool_use_block("write_file", {
                "path": "tests/test_good_convention.py",
                "content": (
                    "import unittest\n\n\n"
                    "class TestGood(unittest.TestCase):\n"
                    "    def test_ok(self):\n"
                    "        self.assertTrue(True)\n"
                ),
            })], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        result = self.agent.execute("add a test", self.context)

        self.assertTrue(result.success)
        self.assertNotIn("uncollected_test_files", result.metadata)


class TestDirtyTreeRefusal(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_refuses_when_tree_already_dirty(self, mock_client):
        self._write_fixture("value.txt", "already here before the agent started\n")
        result = self.agent.execute("write 42 to value.txt", self.context)

        self.assertFalse(result.success)
        self.assertTrue(result.metadata["dirty_at_checkpoint"])
        mock_client.messages.create.assert_not_called()
        with open(os.path.join(self.repo, "value.txt")) as file:
            self.assertEqual(file.read(), "already here before the agent started\n")


class TestCancellation(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.cancellation_requested", return_value=True)
    @patch("agent.agents.coding.anthropic_client")
    def test_cancelled_before_any_model_call(self, mock_client, mock_cancelled):
        result = self.agent.execute("write 42 to value.txt", self.context)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.success)
        mock_client.messages.create.assert_not_called()


class TestIterationLimit(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_stops_at_the_iteration_cap_and_still_verifies(self, mock_client):
        mock_client.messages.create.return_value = _response(
            [_tool_use_block("read_file", {"path": "value.txt"})], "tool_use",
        )
        result = self.agent.execute("loop forever", self.context)
        self.assertEqual(mock_client.messages.create.call_count, settings.max_agent_iterations)
        self.assertIn("iteration limit", result.result)
        self.assertFalse(result.success)
        self.assertFalse(result.metadata["rolled_back"])


class TestCheckpointPruning(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_prunes_old_checkpoints_beyond_the_retention_count(self, mock_client):
        real_retention = settings.coding_checkpoint_retention_count
        object.__setattr__(settings, "coding_checkpoint_retention_count", 2)
        try:
            mock_client.messages.create.return_value = _response([_text_block("Done.")], "end_turn")

            for i in range(3):
                context = RequestContext.create(f"task {i}", source="test", request_id=f"req-{i}")
                self.agent.execute(f"task {i}", context)

            refs = [
                line for line in
                _git(self.repo, "for-each-ref", "--format=%(refname)", "refs/jarvis/checkpoints/").splitlines()
                if line
            ]
            self.assertEqual(len(refs), 2)
            # Kept the two most recent, not an arbitrary two.
            self.assertIn("refs/jarvis/checkpoints/req-1", refs)
            self.assertIn("refs/jarvis/checkpoints/req-2", refs)
        finally:
            object.__setattr__(settings, "coding_checkpoint_retention_count", real_retention)

    @patch("agent.agents.coding.anthropic_client")
    def test_a_prune_failure_does_not_override_the_real_task_outcome(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("write_file", {"path": "value.txt", "content": "42"})], "tool_use"),
            _response([_text_block("Done.")], "end_turn"),
        ]
        with patch("agent.agents.coding.prune_checkpoints", side_effect=coding.CheckpointError("boom")):
            result = self.agent.execute("write 42 to value.txt", self.context)
        self.assertTrue(result.success)
        self.assertEqual(result.verification_status, "passed")


class TestUnexpectedExceptionNeverEscapes(CodingAgentEnabledTestCase):
    @patch("agent.agents.coding.anthropic_client")
    def test_a_raised_provider_error_becomes_a_clean_failure(self, mock_client):
        mock_client.messages.create.side_effect = RuntimeError("network exploded")
        result = self.agent.execute("write 42 to value.txt", self.context)
        self.assertFalse(result.success)
        self.assertIn("network exploded", result.error)
        self.assertIn("checkpoint_ref", result.metadata)

    @patch("agent.agents.coding.anthropic_client")
    def test_a_truncated_response_is_not_treated_as_a_clean_finish(self, mock_client):
        # Real dogfooding finding: write_file requires the complete new
        # file content every call, so a response cut off mid-generation
        # (stop_reason "max_tokens") previously fell through to the same
        # branch as a clean "end_turn" finish and silently reported
        # "Done." for a call that never actually finished.
        mock_client.messages.create.return_value = _response([_text_block("")], "max_tokens")
        result = self.agent.execute("write 42 to value.txt", self.context)
        self.assertFalse(result.success)
        self.assertIn("truncated", result.error)
        self.assertNotIn("Done.", result.result or "")


if __name__ == "__main__":
    unittest.main()
