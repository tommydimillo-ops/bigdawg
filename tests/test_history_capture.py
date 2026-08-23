"""Tests for agent/history_capture.py -- Phase 9 M4.2's deterministic,
non-model-controlled capture of real Jarvis interactions into
agent.history_store, wired into agent.executor.execute_task_stream at
fixed points (never behind a ToolSpec, never behind a model decision).

Two layers of coverage: direct unit tests against agent.history_capture's
own public functions (session lifecycle, idempotency, failure isolation,
privacy, source validation), and integration tests through the real
execute_task_stream() with only the provider network call mocked --
matching this project's established policy (see tests/test_executor_
multi_provider_fallback.py) -- verifying the actual persisted rows via
agent.history_store reads, not just that no exception was raised.

Every test here redirects agent.history_store.HISTORY_DB explicitly in
its own setUp/tearDown -- see tests/__init__.py's docstring for why the
package-level central guard alone is NOT sufficient under this project's
real `python -m unittest discover -s tests` invocation.

Run with: python -m unittest tests.test_history_capture -v
"""
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import tools.schemas  # noqa: F401 -- populates the registry

import agent.execution_history as execution_history
import agent.history_capture as history_capture
import agent.history_store as history_store
import agent.jarvis_state as jarvis_state
import agent.usage as usage
from agent import provider_health
from agent.autonomy import Decision
from agent.execution_state import register_active, unregister_active
from agent.executor import PARTIAL_EXECUTION_MESSAGE, execute_task_stream

FAKE_OPENAI_KEY = "sk-" + ("a1B2c3D4e5F6g7H8i9J0" * 2)


class IsolatedHistoryCaptureTestCase(unittest.TestCase):
    """Redirects every file-backed store execute_task_stream touches, plus
    resets agent.history_capture's in-memory process-local session state
    (chat/voice session cache, in-flight request->session map) so one
    test's session can never leak into another -- this project's existing
    per-file isolation convention (see tests/test_claude_gateway.py) plus
    the module-state reset agent.history_capture itself requires."""

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        self._real_history_db = history_store.HISTORY_DB
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")
        history_capture._reset_for_tests()

    def tearDown(self):
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
            usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock",
            history_store.HISTORY_DB, f"{history_store.HISTORY_DB}-wal", f"{history_store.HISTORY_DB}-shm",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        usage.USAGE_FILE = self._real_usage_file
        history_store.HISTORY_DB = self._real_history_db
        history_capture._reset_for_tests()

    def _rows(self):
        conn = sqlite3.connect(history_store.HISTORY_DB)
        sessions = conn.execute("SELECT session_id, source FROM history_session").fetchall()
        turns = conn.execute(
            "SELECT session_id, request_id, role, content FROM history_turn ORDER BY turn_id"
        ).fetchall()
        conn.close()
        return sessions, turns


class _MockStream:
    def __init__(self, chunks, final_message):
        self._chunks, self._final_message = chunks, final_message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final_message


def _claude_success(text="Hi there!"):
    return _MockStream([text], MagicMock(stop_reason="end_turn"))


class TestSessionLifecycle(IsolatedHistoryCaptureTestCase):

    def test_chat_turns_reuse_one_process_local_session(self):
        history_capture.capture_user_turn("chat", "req-1", "first")
        history_capture.capture_user_turn("chat", "req-2", "second")
        sessions, turns = self._rows()
        self.assertEqual(len(sessions), 1)
        session_ids = {t[0] for t in turns}
        self.assertEqual(session_ids, {sessions[0][0]})

    def test_voice_uses_a_different_session_from_chat(self):
        history_capture.capture_user_turn("chat", "req-1", "typed")
        history_capture.capture_user_turn("voice", "req-2", "spoken")
        sessions, _ = self._rows()
        self.assertEqual(len(sessions), 2)
        sources = {s[1] for s in sessions}
        self.assertEqual(sources, {"chat", "voice"})

    def test_repeated_voice_turns_reuse_voice_session(self):
        history_capture.capture_user_turn("voice", "req-1", "one")
        history_capture.capture_user_turn("voice", "req-2", "two")
        history_capture.capture_user_turn("voice", "req-3", "three")
        sessions, _ = self._rows()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0][1], "voice")

    def test_scheduled_requests_each_get_a_distinct_session(self):
        history_capture.capture_user_turn("scheduled", "req-1", "task one")
        history_capture.capture_user_turn("scheduled", "req-2", "task two")
        sessions, _ = self._rows()
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s[1] == "scheduled" for s in sessions))

    def test_concurrent_scheduled_requests_remain_distinct(self):
        errors = []

        def run(i):
            try:
                history_capture.capture_user_turn("scheduled", f"req-{i}", f"task {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        sessions, _ = self._rows()
        self.assertEqual(len(sessions), 10)

    def test_session_map_thread_safety_for_chat(self):
        errors = []

        def run(i):
            try:
                history_capture.capture_user_turn("chat", f"req-{i}", f"msg {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        sessions, turns = self._rows()
        # Every thread must have converged on exactly one chat session,
        # never a separate one each -- this is the race the lock in
        # _get_or_create_session_id protects against.
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(turns), 20)


class TestCaptureSuccessPair(IsolatedHistoryCaptureTestCase):

    def test_user_and_assistant_turns_recorded_once_each_same_session_and_request(self):
        history_capture.capture_user_turn("chat", "req-1", "what's the weather")
        history_capture.capture_assistant_turn("chat", "req-1", "It's sunny.")

        sessions, turns = self._rows()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(turns), 2)
        user_turn = next(t for t in turns if t[2] == "user")
        assistant_turn = next(t for t in turns if t[2] == "assistant")
        self.assertEqual(user_turn[0], assistant_turn[0])  # same session
        self.assertEqual(user_turn[1], "req-1")
        self.assertEqual(assistant_turn[1], "req-1")
        self.assertEqual(user_turn[3], "what's the weather")
        self.assertEqual(assistant_turn[3], "It's sunny.")

    def test_assistant_capture_without_prior_user_capture_still_records(self):
        # Per spec section 15: if user capture failed/never happened but
        # assistant capture later succeeds, record it anyway rather than
        # dropping it -- session state (chat's process-local session)
        # still makes this safe.
        history_capture.capture_assistant_turn("chat", "req-1", "orphaned answer")
        sessions, turns = self._rows()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0][2], "assistant")


class TestIdempotency(IsolatedHistoryCaptureTestCase):

    def test_duplicate_user_capture_same_request_id_does_not_duplicate(self):
        history_capture.capture_user_turn("chat", "req-1", "first attempt")
        history_capture.capture_user_turn("chat", "req-1", "retried attempt")
        _, turns = self._rows()
        self.assertEqual(len(turns), 1)

    def test_duplicate_assistant_capture_same_request_id_does_not_duplicate(self):
        history_capture.capture_user_turn("chat", "req-1", "q")
        history_capture.capture_assistant_turn("chat", "req-1", "a1")
        history_capture.capture_assistant_turn("chat", "req-1", "a2")
        _, turns = self._rows()
        assistant_turns = [t for t in turns if t[2] == "assistant"]
        self.assertEqual(len(assistant_turns), 1)


class TestSourceValidation(IsolatedHistoryCaptureTestCase):

    def test_chat_voice_scheduled_all_accepted(self):
        for i, source in enumerate(("chat", "voice", "scheduled")):
            history_capture.capture_user_turn(source, f"req-{i}", "hi")
        sessions, _ = self._rows()
        self.assertEqual(len(sessions), 3)

    def test_unsupported_source_does_not_raise(self):
        try:
            history_capture.capture_user_turn("not-a-real-source", "req-1", "hi")
            history_capture.capture_assistant_turn("not-a-real-source", "req-1", "hi back")
        except Exception as error:
            self.fail(f"unsupported source raised: {error}")
        # An unsupported source is rejected before any SQLite call is ever
        # made, so the temp database is never even created -- matching
        # M4.1's own "importing/calling creates nothing unless a real
        # write happens" guarantee.
        self.assertFalse(os.path.exists(history_store.HISTORY_DB))

    @patch("agent.history_capture.log_event")
    def test_unsupported_source_logs_a_skip_event(self, mock_log_event):
        history_capture.capture_user_turn("bogus", "req-1", "hi")
        events = [call.args[0] for call in mock_log_event.call_args_list]
        self.assertIn("history_capture_skipped", events)


class TestFailureIsolation(IsolatedHistoryCaptureTestCase):
    """Every capture failure mode must be swallowed: never raised to the
    caller, always logged as a bounded warning, never containing raw turn
    content."""

    @patch("agent.history_capture.history_store.create_session")
    def test_session_creation_failure_does_not_raise(self, mock_create_session):
        mock_create_session.side_effect = RuntimeError("db is locked")
        try:
            history_capture.capture_user_turn("chat", "req-1", "hello")
        except Exception as error:
            self.fail(f"capture_user_turn raised: {error}")

    @patch("agent.history_capture.history_store.record_turn")
    def test_user_record_failure_does_not_raise(self, mock_record_turn):
        mock_record_turn.side_effect = RuntimeError("disk full")
        try:
            history_capture.capture_user_turn("chat", "req-1", "hello")
        except Exception as error:
            self.fail(f"capture_user_turn raised: {error}")

    @patch("agent.history_capture.history_store.record_turn")
    def test_assistant_record_failure_does_not_raise(self, mock_record_turn):
        mock_record_turn.side_effect = RuntimeError("disk full")
        try:
            history_capture.capture_assistant_turn("chat", "req-1", "hello back")
        except Exception as error:
            self.fail(f"capture_assistant_turn raised: {error}")

    @patch("agent.history_capture.log_event")
    @patch("agent.history_capture.history_store.record_turn")
    def test_failure_emits_bounded_warning_without_raw_content(self, mock_record_turn, mock_log_event):
        secret_content = "the password is hunter2, please remember it forever"
        mock_record_turn.side_effect = RuntimeError("disk full")

        history_capture.capture_user_turn("chat", "req-1", secret_content)

        self.assertTrue(mock_log_event.called)
        for call in mock_log_event.call_args_list:
            serialized = repr(call)
            self.assertNotIn(secret_content, serialized)
            self.assertNotIn("hunter2", serialized)
        event_names = [call.args[0] for call in mock_log_event.call_args_list]
        self.assertIn("history_capture_failed", event_names)
        failed_call = next(c for c in mock_log_event.call_args_list if c.args[0] == "history_capture_failed")
        self.assertEqual(failed_call.kwargs.get("level"), "warning")
        self.assertEqual(failed_call.kwargs.get("request_id"), "req-1")
        self.assertIn("error_type", failed_call.kwargs)


class TestPrivacy(IsolatedHistoryCaptureTestCase):

    def test_secret_in_user_turn_is_redacted_in_db(self):
        history_capture.capture_user_turn("chat", "req-1", f"my key is {FAKE_OPENAI_KEY}")
        _, turns = self._rows()
        content = turns[0][3]
        self.assertNotIn(FAKE_OPENAI_KEY, content)
        self.assertIn("[redacted]", content)

    def test_secret_in_assistant_turn_is_redacted_in_db(self):
        history_capture.capture_user_turn("chat", "req-1", "what's my key")
        history_capture.capture_assistant_turn("chat", "req-1", f"it's {FAKE_OPENAI_KEY}")
        _, turns = self._rows()
        assistant_content = next(t[3] for t in turns if t[2] == "assistant")
        self.assertNotIn(FAKE_OPENAI_KEY, assistant_content)

    def test_long_content_is_truncated(self):
        long_text = "word " * (history_store.MAX_TURN_LENGTH // 4)
        history_capture.capture_user_turn("chat", "req-1", long_text)
        _, turns = self._rows()
        self.assertLessEqual(len(turns[0][3]), history_store.MAX_TURN_LENGTH)


class TestNoRetries(IsolatedHistoryCaptureTestCase):

    @patch("agent.history_capture.history_store.record_turn")
    def test_a_single_failure_makes_exactly_one_attempt(self, mock_record_turn):
        mock_record_turn.side_effect = RuntimeError("locked")
        history_capture.capture_user_turn("chat", "req-1", "hello")
        self.assertEqual(mock_record_turn.call_count, 1)


class TestNeverCreatesProductionDb(unittest.TestCase):

    def test_importing_executor_creates_no_production_db(self):
        real_db = os.path.expanduser("~/Library/Application Support/CampusPilot/history.db")
        existed_before = os.path.exists(real_db)
        import agent.executor  # noqa: F401
        import agent.history_capture  # noqa: F401
        self.assertEqual(os.path.exists(real_db), existed_before)


# --- Integration tests: real execute_task_stream(), provider mocked ------

class IsolatedExecutorTestCase(unittest.TestCase):
    """Also clears agent.provider_health's in-memory failure-cooldown
    state -- real, module-global, in-process state that a simulated
    provider failure in one of this class's tests (e.g.
    TestRealFailureCapture, which makes a real, in-memory
    provider_health.record_failure("anthropic") call) would otherwise
    leak into whichever test runs next in this same process, silently
    changing which provider the REAL router picks there. This is exactly
    how a real, unmocked call to the live OpenAI API happened during this
    suite's own development (see tests/test_executor_multi_provider_
    fallback.py's IsolatedExecutorTestCase, which already does this for
    the same reason)."""

    def setUp(self):
        self._real_history_file = execution_history.HISTORY_FILE
        self._real_state_file = jarvis_state.STATE_FILE
        self._real_usage_file = usage.USAGE_FILE
        self._real_history_db = history_store.HISTORY_DB
        execution_history.HISTORY_FILE = tempfile.mktemp(suffix=".json")
        jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")
        history_capture._reset_for_tests()
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)

        # Tripwires: every test in this class pins build_fallback_chain
        # to a single "anthropic" candidate and mocks agent.executor.
        # claude_client itself -- these three patches exist purely so
        # that IF a future change to this test file (or to the real
        # router) ever causes a DIFFERENT provider path to be reached
        # despite that, the test fails immediately and locally with a
        # clear AssertionError, instead of a real, unmocked network call
        # reaching a live provider (which happened twice for real during
        # this suite's own development -- see CHANGELOG.md). Each test
        # method's own build_fallback_chain patch takes precedence for
        # ROUTING; these only fire if something actually tries to call
        # through to a non-anthropic client. Patches the whole top-level
        # client reference (matching this project's existing
        # @patch("agent.executor.openai_client") convention) rather than
        # a `.chat.completions.create` sub-attribute path -- xai_client
        # is None in this dev environment (no XAI_API_KEY configured),
        # and attribute-chain patching a None value raises its own
        # AttributeError during setUp, before any test body even runs.
        _tripwire_openai = MagicMock()
        _tripwire_openai.chat.completions.create.side_effect = AssertionError(
            "unexpected real OpenAI call -- test harness misconfigured"
        )
        _tripwire_xai = MagicMock()
        _tripwire_xai.chat.completions.create.side_effect = AssertionError(
            "unexpected real xAI call -- test harness misconfigured"
        )
        self._tripwire_patches = [
            patch("agent.executor.openai_client", _tripwire_openai),
            patch("agent.executor.xai_client", _tripwire_xai),
            patch("agent.executor._call_perplexity_agent",
                  side_effect=AssertionError("unexpected real Perplexity call -- test harness misconfigured")),
        ]
        for tripwire in self._tripwire_patches:
            tripwire.start()

    def tearDown(self):
        for tripwire in self._tripwire_patches:
            tripwire.stop()
        for path in (
            execution_history.HISTORY_FILE, f"{execution_history.HISTORY_FILE}.tmp",
            jarvis_state.STATE_FILE, f"{jarvis_state.STATE_FILE}.tmp",
            usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock",
            history_store.HISTORY_DB, f"{history_store.HISTORY_DB}-wal", f"{history_store.HISTORY_DB}-shm",
        ):
            if os.path.exists(path):
                os.remove(path)
        execution_history.HISTORY_FILE = self._real_history_file
        jarvis_state.STATE_FILE = self._real_state_file
        usage.USAGE_FILE = self._real_usage_file
        history_store.HISTORY_DB = self._real_history_db
        history_capture._reset_for_tests()
        for provider in ("anthropic", "openai", "xai", "perplexity"):
            provider_health.clear_failure(provider)

    def _turns(self):
        # No DB file yet means no capture ever happened in this test --
        # a legitimate outcome (e.g. a generator that was never started),
        # not an error, so this returns an empty list rather than letting
        # sqlite3 raise "no such table".
        if not os.path.exists(history_store.HISTORY_DB):
            return []
        conn = sqlite3.connect(history_store.HISTORY_DB)
        rows = conn.execute(
            "SELECT request_id, role, content, session_id FROM history_turn ORDER BY turn_id"
        ).fetchall()
        conn.close()
        return rows


class TestProviderTripwireActuallyFires(IsolatedExecutorTestCase):
    """Proves the tripwires configured in IsolatedExecutorTestCase.setUp
    actually catch a misrouted call -- i.e. that if build_fallback_chain
    were ever misconfigured (as happened twice for real during this
    suite's own development), the failure surfaces locally as a clear
    AssertionError instead of silently reaching a live provider. Without
    this test, the tripwires above are just as easy to silently break as
    the thing they're meant to catch."""

    @patch("agent.executor.build_fallback_chain")
    def test_routing_to_openai_without_a_local_mock_fails_locally(self, mock_chain):
        from agent.model_router import ModelChoice
        # Deliberately does NOT re-mock openai_client for this one test
        # -- the class-level tripwire from setUp is the only thing
        # standing between this and a real network call. execute_task_
        # stream() catches a provider exception and reports it as
        # user-facing text rather than letting it escape (existing,
        # correct, unrelated behavior) -- so the proof here is that the
        # tripwire's AssertionError is what got caught and surfaced,
        # which is only possible if the mock intercepted the call
        # locally instead of a real request leaving the process.
        mock_chain.return_value = [ModelChoice(provider="openai", model="gpt-5.6-luna")]

        result = "".join(execute_task_stream("say hi", source="chat"))
        self.assertIn("unexpected real OpenAI call", result)


class TestRealEndToEndSuccess(IsolatedExecutorTestCase):
    """Section 23: at least one test drives the REAL execute_task_stream
    -> real agent.history_capture -> real agent.history_store, against a
    temporary SQLite database, with only the provider network call
    mocked. No paid API call, deterministic output."""

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_real_pair_persisted_and_streaming_unchanged(self, mock_claude, mock_chain):
        from agent.model_router import ModelChoice
        # Pinned rather than relying on the real router's current default
        # ranking -- see TestRealPartialToolExecutionCapture's comment for
        # why this matters (a real, unmocked provider call happened during
        # this suite's own development from relying on that default).
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _claude_success("Hi there!")

        chunks = list(execute_task_stream("say hi", source="chat"))

        self.assertEqual(chunks, ["Hi there!"])  # streaming untouched, chunk-for-chunk

        rows = self._turns()
        self.assertEqual(len(rows), 2)
        request_ids = {r[0] for r in rows}
        self.assertEqual(len(request_ids), 1)
        user_row = next(r for r in rows if r[1] == "user")
        assistant_row = next(r for r in rows if r[1] == "assistant")
        self.assertEqual(user_row[2], "say hi")
        self.assertEqual(assistant_row[2], "Hi there!")


class TestRealFailureCapture(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_user_turn_persists_even_when_every_provider_fails(self, mock_claude, mock_chain):
        from agent.model_router import ModelChoice
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.side_effect = RuntimeError("anthropic is down")

        result = "".join(execute_task_stream("say hi", source="chat"))

        self.assertIn("Agent error", result)
        rows = self._turns()
        user_row = next(r for r in rows if r[1] == "user")
        self.assertEqual(user_row[2], "say hi")
        assistant_row = next(r for r in rows if r[1] == "assistant")
        # The exact text the caller actually saw, nothing more/less.
        self.assertEqual(assistant_row[2], result)
        self.assertNotIn("Traceback", assistant_row[2])


class TestRealCancellationCapture(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_cancelled_before_any_provider_call_captures_stopped_message(self, mock_claude, mock_chain):
        from agent.cancellation import request_cancel
        from agent.model_router import ModelChoice
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]

        captured_state = {}

        def _on_state_created(state):
            captured_state["state"] = state
            request_cancel(state.request_id)

        try:
            chunks = list(execute_task_stream(
                "say hi", source="chat", on_state_created=_on_state_created,
            ))
        finally:
            unregister_active(captured_state["state"].request_id)

        self.assertEqual(chunks, ["Stopped, as requested."])
        rows = self._turns()
        user_row = next(r for r in rows if r[1] == "user")
        self.assertEqual(user_row[2], "say hi")
        assistant_row = next(r for r in rows if r[1] == "assistant")
        self.assertEqual(assistant_row[2], "Stopped, as requested.")
        # exactly one of each -- no duplicate terminal capture
        self.assertEqual(len([r for r in rows if r[1] == "assistant"]), 1)


class TestRealPartialToolExecutionCapture(IsolatedExecutorTestCase):
    """Section 18: a tool with a real side effect ran, then the NEXT model
    call fails -- agent.executor.PartialToolExecution. Exactly one user
    turn, at most one assistant turn, equal to what the caller actually
    received."""

    @patch("agent.executor.should_request_confirmation", return_value=Decision.ALLOW)
    @patch("agent.executor.registry.parallel_safe_tools", return_value=set())
    @patch("agent.executor.registry.side_effect_tools", return_value={"fake_side_effect_tool"})
    @patch("agent.executor.registry.dispatch", return_value="done")
    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_partial_tool_execution_captures_exactly_one_pair(
        self, mock_claude, mock_chain, mock_dispatch, mock_side_effect_tools, mock_parallel_safe, mock_confirm,
    ):
        from agent.model_router import ModelChoice
        # Pin the fallback chain to a single anthropic candidate -- without
        # this, the real router can rank a different provider first (it
        # did during development, sending one real, unmocked request to
        # the live OpenAI API before this was caught and fixed). Every
        # other test in this class/file that exercises a real provider
        # failure path does this explicitly for the same reason.
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]

        tool_use_block = MagicMock(type="tool_use", name="fake_side_effect_tool", id="tool-1")
        tool_use_block.name = "fake_side_effect_tool"
        tool_use_block.input = {}
        first_response = MagicMock(stop_reason="tool_use", content=[tool_use_block])

        mock_claude.messages.stream.side_effect = [
            _MockStream(["Let me do that..."], first_response),
            RuntimeError("dropped after committing the tool call"),
        ]

        chunks = list(execute_task_stream("do the thing", source="chat"))
        result = "".join(chunks)

        self.assertEqual(result, "Let me do that..." + PARTIAL_EXECUTION_MESSAGE)

        rows = self._turns()
        user_rows = [r for r in rows if r[1] == "user"]
        assistant_rows = [r for r in rows if r[1] == "assistant"]
        self.assertEqual(len(user_rows), 1)
        self.assertEqual(len(assistant_rows), 1)
        self.assertEqual(assistant_rows[0][2], result)


class TestRealCaptureFailureNeverChangesExecutorOutcome(IsolatedExecutorTestCase):

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.history_capture.history_store.record_turn")
    @patch("agent.executor.claude_client")
    def test_history_write_failure_does_not_change_the_real_answer(self, mock_claude, mock_record_turn, mock_chain):
        from agent.model_router import ModelChoice
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_record_turn.side_effect = RuntimeError("db exploded")
        mock_claude.messages.stream.return_value = _claude_success("Still works fine")

        result = "".join(execute_task_stream("say hi", source="chat"))

        self.assertEqual(result, "Still works fine")
        # And, since record_turn was mocked to always fail, nothing was
        # actually persisted -- confirms the failure was real, not
        # accidentally bypassed.
        rows = self._turns()
        self.assertEqual(rows, [])


class TestGeneratorLifecycle(IsolatedExecutorTestCase):
    """Phase 9 M4.2 lifecycle hardening: execute_task_stream() is a
    generator, and a caller can abandon it (partial iteration + close(),
    or simply dropping the reference) before any of the four normal
    terminal branches ever runs. GeneratorExit is a BaseException, not an
    Exception, so it is not caught by `except PartialToolExecution`/
    `except Exception` -- only the `finally:` block (which now calls
    _finalize_history_capture() unconditionally) can guarantee cleanup
    here."""

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_closing_after_first_chunk_captures_exactly_that_chunk(self, mock_claude, mock_chain):
        from agent.model_router import ModelChoice
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _MockStream(
            ["first chunk ", "second chunk ", "third chunk (never consumed)"],
            MagicMock(stop_reason="end_turn"),
        )

        gen = execute_task_stream("say hi", source="chat")
        first_chunk = next(gen)
        self.assertEqual(first_chunk, "first chunk ")
        gen.close()

        rows = self._turns()
        user_rows = [r for r in rows if r[1] == "user"]
        assistant_rows = [r for r in rows if r[1] == "assistant"]
        self.assertEqual(len(user_rows), 1)
        self.assertEqual(user_rows[0][2], "say hi")
        self.assertEqual(len(assistant_rows), 1)
        # Exactly the chunk actually delivered -- not the second/third
        # chunks that were still sitting unconsumed in the stream.
        self.assertEqual(assistant_rows[0][2], "first chunk ")
        self.assertNotIn("second chunk", assistant_rows[0][2])
        self.assertNotIn("third chunk", assistant_rows[0][2])
        self.assertEqual(user_rows[0][0], assistant_rows[0][0])  # same request_id

        # Bookkeeping cleaned: the in-flight request->session map must not
        # retain an entry after the generator has been closed.
        self.assertNotIn(user_rows[0][0], history_capture._request_sessions)

        # The process-level chat session cache itself must survive --
        # only the per-request mapping is cleaned, never the process cache.
        self.assertIn("chat", history_capture._process_sessions)

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_process_session_cache_still_usable_after_an_abandoned_request(self, mock_claude, mock_chain):
        from agent.model_router import ModelChoice
        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.return_value = _MockStream(
            ["abandoned chunk"], MagicMock(stop_reason="end_turn"),
        )
        gen = execute_task_stream("first request", source="chat")
        next(gen)
        gen.close()
        abandoned_session = history_capture._process_sessions["chat"]

        mock_claude.messages.stream.return_value = _MockStream(
            ["completed normally"], MagicMock(stop_reason="end_turn"),
        )
        result = "".join(execute_task_stream("second request", source="chat"))
        self.assertEqual(result, "completed normally")

        rows = self._turns()
        second_user_row = next(r for r in rows if r[1] == "user" and r[2] == "second request")
        self.assertEqual(second_user_row[3], abandoned_session)  # session_id column

    def test_closing_a_never_started_generator_captures_nothing(self):
        # A generator's body does not run at all until the first next()
        # call -- verified empirically (not assumed) before writing this
        # test. Closing one that was never started is therefore a true
        # no-op: not even capture_user_turn() has run yet, so this is a
        # different (simpler) case than "started but yielded zero text,"
        # which the real _run_claude_loop_stream design can't actually
        # produce -- every successful iteration yields at least a
        # fallback message ("I'm not sure how to respond to that.",
        # "Stopped, as requested.", etc.), so a generator that has been
        # started at least once always has at least one captured chunk
        # by the time it could be closed.
        gen = execute_task_stream("never consumed", source="chat")
        try:
            gen.close()
        except Exception as error:
            self.fail(f"closing a never-started generator raised: {error}")
        rows = self._turns()
        self.assertEqual(rows, [])

    @patch("agent.executor.build_fallback_chain")
    @patch("agent.executor.claude_client")
    def test_unexpected_base_exception_still_cleans_up_and_propagates_unchanged(self, mock_claude, mock_chain):
        from agent.model_router import ModelChoice

        class _WeirdBaseException(BaseException):
            """Stands in for an unexpected BaseException subclass (e.g. a
            real KeyboardInterrupt/SystemExit-shaped failure) that none
            of execute_task_stream's `except PartialToolExecution`/
            `except Exception` clauses catch -- only `finally:` sees it."""

        mock_chain.return_value = [ModelChoice(provider="anthropic", model="claude-sonnet-5")]
        mock_claude.messages.stream.side_effect = _WeirdBaseException("simulated unexpected failure")

        gen = execute_task_stream("say hi", source="chat")
        with self.assertRaises(_WeirdBaseException):
            next(gen)

        # The original exception must reach the caller completely
        # unchanged -- cleanup must never mask or replace it.
        rows = self._turns()
        user_rows = [r for r in rows if r[1] == "user"]
        self.assertEqual(len(user_rows), 1)
        self.assertEqual(user_rows[0][2], "say hi")
        # No assistant turn -- zero chunks were ever yielded before the
        # exception hit.
        self.assertEqual(len([r for r in rows if r[1] == "assistant"]), 0)
        self.assertNotIn(user_rows[0][0], history_capture._request_sessions)


if __name__ == "__main__":
    unittest.main()
