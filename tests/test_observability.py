"""Tests for agent/observability.py (structured diagnostics logging,
separate from agent/audit.py's security/action log) and its real
integration into agent/executor.py's tool dispatch -- exercised via a
real, free, local tool call (get_system_status), not a mocked one, so
this proves actual log lines come out of the real pipeline, not just that
the logging functions work in isolation.

Run with: python -m unittest tests.test_observability -v
"""
import io
import json
import logging
import tempfile
import unittest

import agent.jarvis_state as jarvis_state
from agent.observability import _logger, log_event, preview, timed_event
from agent.executor import _run_tool
from agent.request_context import RequestContext

# _run_tool now writes cross-interface status via agent.jarvis_state on
# every real dispatch -- redirected here (module-wide, like every other
# file-backed store's tests in this project) so exercising it doesn't
# clobber the real ~/Library/.../jarvis_state.json the live menu-bar/
# dashboard apps read from.
_real_state_file = jarvis_state.STATE_FILE


def setUpModule():
    jarvis_state.STATE_FILE = tempfile.mktemp(suffix=".json")


def tearDownModule():
    jarvis_state.STATE_FILE = _real_state_file


class _CaptureLogs:
    """Temporarily redirects agent.observability's logger to an in-memory
    buffer so a test can inspect exactly what got logged, without
    depending on stderr."""

    def __enter__(self):
        self.buffer = io.StringIO()
        self.handler = logging.StreamHandler(self.buffer)
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc_info):
        _logger.removeHandler(self.handler)

    def lines(self):
        return [json.loads(line) for line in self.buffer.getvalue().splitlines() if line.strip()]


class TestPreview(unittest.TestCase):

    def test_short_value_unchanged(self):
        self.assertEqual(preview("hello"), "hello")

    def test_long_value_truncated_with_ellipsis(self):
        text = "x" * 500
        result = preview(text, limit=50)
        self.assertEqual(len(result), 51)  # 50 chars + the ellipsis char
        self.assertTrue(result.endswith("…"))

    def test_non_string_value_stringified(self):
        self.assertEqual(preview(12345), "12345")


class TestLogEvent(unittest.TestCase):

    def test_emits_valid_json_with_required_fields(self):
        with _CaptureLogs() as capture:
            log_event("test_thing", request_id="abc123", component="test", foo="bar")
        entries = capture.lines()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["event"], "test_thing")
        self.assertEqual(entry["request_id"], "abc123")
        self.assertEqual(entry["component"], "test")
        self.assertEqual(entry["foo"], "bar")
        self.assertIn("timestamp", entry)
        self.assertEqual(entry["level"], "info")

    def test_duration_recorded_in_milliseconds(self):
        with _CaptureLogs() as capture:
            log_event("test_thing", duration=1.5)
        self.assertEqual(capture.lines()[0]["duration_ms"], 1500.0)

    def test_omits_request_id_and_component_when_not_given(self):
        with _CaptureLogs() as capture:
            log_event("test_thing")
        entry = capture.lines()[0]
        self.assertNotIn("request_id", entry)
        self.assertNotIn("component", entry)


class TestTimedEvent(unittest.TestCase):

    def test_success_logs_started_then_completed(self):
        with _CaptureLogs() as capture:
            with timed_event("op", request_id="r1"):
                pass
        events = [e["event"] for e in capture.lines()]
        self.assertEqual(events, ["op_started", "op_completed"])

    def test_completed_event_has_duration(self):
        with _CaptureLogs() as capture:
            with timed_event("op"):
                pass
        completed = [e for e in capture.lines() if e["event"] == "op_completed"][0]
        self.assertIn("duration_ms", completed)

    def test_failure_logs_started_then_failed_and_reraises(self):
        with _CaptureLogs() as capture:
            with self.assertRaises(ValueError):
                with timed_event("op", request_id="r1"):
                    raise ValueError("boom")
        events = [e["event"] for e in capture.lines()]
        self.assertEqual(events, ["op_started", "op_failed"])

    def test_failed_event_has_error_type_not_message(self):
        with _CaptureLogs() as capture:
            with self.assertRaises(ValueError):
                with timed_event("op"):
                    raise ValueError("a message that could contain user content")
        failed = [e for e in capture.lines() if e["event"] == "op_failed"][0]
        self.assertEqual(failed["error_type"], "ValueError")
        self.assertNotIn("a message that could contain user content", json.dumps(failed))


class TestRealToolDispatchLogging(unittest.TestCase):
    """Exercises the actual executor._run_tool integration, not just the
    logging module in isolation."""

    def test_tool_started_and_completed_logged_with_request_id(self):
        context = RequestContext.create("check status", source="chat")
        with _CaptureLogs() as capture:
            _run_tool("get_system_status", {}, source="chat", context=context)
        events = {e["event"]: e for e in capture.lines()}
        self.assertIn("tool_started", events)
        self.assertIn("tool_completed", events)
        self.assertEqual(events["tool_started"]["request_id"], context.request_id)
        self.assertEqual(events["tool_completed"]["request_id"], context.request_id)
        self.assertEqual(events["tool_started"]["tool"], "get_system_status")

    def test_tool_failure_logged_with_error_type_not_full_traceback(self):
        with _CaptureLogs() as capture:
            with self.assertRaises(KeyError):
                _run_tool("read_document", {}, source="chat")
        failed = [e for e in capture.lines() if e["event"] == "tool_failed"][0]
        self.assertEqual(failed["error_type"], "KeyError")

    def test_no_secret_value_appears_in_logs_for_a_real_request(self):
        from agent.secrets import get_secret
        real_key = get_secret("ANTHROPIC_API_KEY") or get_secret("OPENAI_API_KEY")
        if not real_key:
            self.skipTest("no API key configured in this environment")

        context = RequestContext.create("check status", source="chat")
        with _CaptureLogs() as capture:
            _run_tool("get_system_status", {}, source="chat", context=context)
        raw_output = capture.buffer.getvalue()
        self.assertNotIn(real_key, raw_output)


if __name__ == "__main__":
    unittest.main()
