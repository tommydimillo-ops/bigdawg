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
import os
import tempfile
import unittest

import agent.jarvis_state as jarvis_state
import agent.observability as observability
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


class TestEventsSince(unittest.TestCase):
    """M4.5: events_since() is the read-back half of log_event's write-only
    output -- the mechanism M4.4's own defaults (500 tokens/150ms/top-3)
    cannot be validated without. Never touches the real MENUBAR_LOG_FILE;
    every test writes its own throwaway file."""

    def setUp(self):
        self.log_path = tempfile.mktemp(suffix=".log")

    def tearDown(self):
        try:
            os.remove(self.log_path)
        except FileNotFoundError:
            pass

    def _write_lines(self, *records):
        with open(self.log_path, "w") as file:
            for record in records:
                file.write(json.dumps(record) + "\n")

    def test_missing_file_returns_none_not_empty_list(self):
        result = observability.events_since(0, log_path=tempfile.mktemp())
        self.assertIsNone(result)

    def test_readable_but_empty_file_returns_empty_list(self):
        self._write_lines()
        result = observability.events_since(0, log_path=self.log_path)
        self.assertEqual(result, [])

    def test_filters_by_cutoff_timestamp(self):
        self._write_lines(
            {"timestamp": 100.0, "event": "a"},
            {"timestamp": 200.0, "event": "b"},
        )
        result = observability.events_since(150.0, log_path=self.log_path)
        self.assertEqual([r["event"] for r in result], ["b"])

    def test_filters_by_event_name(self):
        self._write_lines(
            {"timestamp": 100.0, "event": "a"},
            {"timestamp": 100.0, "event": "b"},
        )
        result = observability.events_since(0, event="b", log_path=self.log_path)
        self.assertEqual([r["event"] for r in result], ["b"])

    def test_no_event_filter_returns_everything_in_the_window(self):
        self._write_lines(
            {"timestamp": 100.0, "event": "a"},
            {"timestamp": 100.0, "event": "b"},
        )
        result = observability.events_since(0, log_path=self.log_path)
        self.assertEqual(len(result), 2)

    def test_malformed_line_is_skipped_not_fatal(self):
        with open(self.log_path, "w") as file:
            file.write("not json at all\n")
            file.write(json.dumps({"timestamp": 100.0, "event": "a"}) + "\n")
            file.write('{"truncated": tr\n')
        result = observability.events_since(0, log_path=self.log_path)
        self.assertEqual([r["event"] for r in result], ["a"])

    def test_non_dict_json_line_is_skipped(self):
        with open(self.log_path, "w") as file:
            file.write(json.dumps([1, 2, 3]) + "\n")
            file.write(json.dumps({"timestamp": 100.0, "event": "a"}) + "\n")
        result = observability.events_since(0, log_path=self.log_path)
        self.assertEqual([r["event"] for r in result], ["a"])

    def test_line_missing_timestamp_is_skipped(self):
        with open(self.log_path, "w") as file:
            file.write(json.dumps({"event": "no_timestamp"}) + "\n")
        result = observability.events_since(0, log_path=self.log_path)
        self.assertEqual(result, [])

    def test_default_log_path_reads_the_redirected_menubar_constant(self):
        # No log_path passed -- must resolve observability.MENUBAR_LOG_FILE
        # dynamically inside the function body, not a value captured at
        # import/definition time (the exact class of bug CLAUDE.md's "How
        # to test" section warns about).
        self._write_lines({"timestamp": 100.0, "event": "a"})
        real_constant = observability.MENUBAR_LOG_FILE
        try:
            observability.MENUBAR_LOG_FILE = self.log_path
            result = observability.events_since(0)
        finally:
            observability.MENUBAR_LOG_FILE = real_constant
        self.assertEqual([r["event"] for r in result], ["a"])

    def test_never_writes_to_the_log_file(self):
        self._write_lines({"timestamp": 100.0, "event": "a"})
        before = os.path.getmtime(self.log_path)
        observability.events_since(0, log_path=self.log_path)
        after = os.path.getmtime(self.log_path)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
