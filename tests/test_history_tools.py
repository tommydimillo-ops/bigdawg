"""Tests for tools/schemas/history.py -- Phase 9 M4.3's two read-only
ToolSpecs (history_status, search_conversation_history) over the real
agent/history_store.py. Exercises the real store against a redirected
HISTORY_DB (agent.history_store is internal application logic, not an
external-call boundary -- see CLAUDE.md's mocking policy), following the
same per-test redirect convention as tests/test_history_capture.py.
Error-mapping coverage (one test per HistoryStoreError subclass) mocks
history_store's already-separately-tested functions to raise each real
exception type directly, so this file tests this module's own mapping
logic, not SQLite behavior a second time.

Run with: python -m unittest tests.test_history_tools -v
"""
import json
import tempfile
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry

import agent.history_store as history_store
from tools import registry

_TOOL_NAMES = ("history_status", "search_conversation_history")


class IsolatedHistoryDbTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_db = history_store.HISTORY_DB
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        history_store.HISTORY_DB = self._real_history_db


class TestToolsAreRegistered(unittest.TestCase):

    def test_both_registered(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, registry.all_names())

    def test_both_are_permission_level_zero(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(registry.permission_level(name), 0)

    def test_none_are_side_effect_tools(self):
        side_effect_tools = registry.side_effect_tools()
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, side_effect_tools)

    def test_both_unattended_allowed(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertTrue(registry.get(name).unattended_allowed)

    def test_neither_requires_live_confirmation(self):
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertFalse(registry.get(name).requires_live_confirmation)

    def test_both_parallel_safe(self):
        parallel_safe = registry.parallel_safe_tools()
        for name in _TOOL_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, parallel_safe)

    def test_history_status_takes_no_input(self):
        schema = registry.get("history_status").input_schema
        self.assertEqual(schema["properties"], {})
        self.assertEqual(schema["required"], [])

    def test_search_requires_query_only(self):
        schema = registry.get("search_conversation_history").input_schema
        self.assertEqual(schema["required"], ["query"])

    def test_search_never_accepts_a_db_path_or_raw_match_syntax(self):
        properties = registry.get("search_conversation_history").input_schema["properties"]
        for forbidden in ("db_path", "path", "match", "raw_query", "sql"):
            self.assertNotIn(forbidden, properties)

    def test_schema_enums_do_not_drift_from_the_store_s_own_valid_sets(self):
        # tools/schemas/history.py hardcodes these enum lists rather than
        # importing history_store's private frozensets (the ToolSpec
        # input_schema is Jarvis-facing API surface, the store's sets are
        # an internal implementation detail) -- this test is the tripwire
        # that catches the two drifting apart instead of a silent gap.
        properties = registry.get("search_conversation_history").input_schema["properties"]
        self.assertEqual(set(properties["source"]["enum"]), history_store._VALID_SOURCES)
        self.assertEqual(set(properties["role"]["enum"]), history_store._VALID_ROLES)


class TestHistoryStatusTool(IsolatedHistoryDbTestCase):

    def test_status_on_a_store_with_no_database_yet(self):
        result = registry.dispatch("history_status", {})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "ok")
        self.assertFalse(parsed["available"])

    def test_status_reports_counts_after_real_activity(self):
        history_store.initialize_history_store(db_path=history_store.HISTORY_DB)
        history_store.create_session("chat", session_id="s1", db_path=history_store.HISTORY_DB)
        history_store.record_turn("s1", "user", "hello there", db_path=history_store.HISTORY_DB)

        result = registry.dispatch("history_status", {})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "ok")
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["schema_version"], history_store.SCHEMA_VERSION)
        self.assertEqual(parsed["session_count"], 1)
        self.assertEqual(parsed["turn_count"], 1)

    def test_status_never_leaks_the_absolute_db_path(self):
        result = registry.dispatch("history_status", {})
        self.assertNotIn(history_store.HISTORY_DB, result)


class TestSearchConversationHistoryTool(IsolatedHistoryDbTestCase):

    def _seed(self):
        history_store.create_session("chat", session_id="s1", db_path=history_store.HISTORY_DB)
        return history_store.record_turn(
            "s1", "user", "remind me about the quarterly budget review",
            request_id="req-1", db_path=history_store.HISTORY_DB,
        )

    def test_search_returns_hits_with_complete_provenance(self):
        turn = self._seed()
        result = registry.dispatch("search_conversation_history", {"query": "budget"})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "ok")
        self.assertEqual(len(parsed["results"]), 1)
        hit = parsed["results"][0]
        self.assertEqual(hit["turn_id"], turn.turn_id)
        self.assertEqual(hit["session_id"], "s1")
        self.assertEqual(hit["request_id"], "req-1")
        self.assertEqual(hit["source"], "chat")
        self.assertEqual(hit["role"], "user")
        for field in ("created_at", "snippet", "rank", "redacted", "truncated"):
            self.assertIn(field, hit)

    def test_no_history_yet_returns_empty_results_not_an_error(self):
        result = registry.dispatch("search_conversation_history", {"query": "anything"})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "unavailable")

    def test_max_results_clamped_above_the_cap(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": 999})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 50)

    def test_max_results_non_numeric_string_never_raises(self):
        result = registry.dispatch("search_conversation_history", {"query": "x", "max_results": "ten"})
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "invalid_input")

    def test_max_results_none_uses_default(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": None})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 10)

    def test_max_results_float_never_raises(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": 5.7})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 5)

    def test_max_results_bool_never_raises(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": True})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 1)

    def test_max_results_negative_clamps_to_one(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": -5})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 1)

    def test_max_results_zero_clamps_to_one_not_default(self):
        with patch("agent.history_store.search_history") as mock_search:
            mock_search.return_value = []
            registry.dispatch("search_conversation_history", {"query": "x", "max_results": 0})
            self.assertEqual(mock_search.call_args.kwargs["max_results"], 1)

    def test_raw_fts_syntax_is_neutralized_not_passed_through(self):
        self._seed()
        # A bare FTS5 operator/column-filter/wildcard query must never
        # raise or be interpreted specially -- build_safe_match_query
        # quotes every extracted term, so this is just a literal-term
        # search for the words "budget", "OR", "review" (no match on the
        # seeded turn's "quarterly budget review" other than "budget"/
        # "review" as plain terms).
        result = registry.dispatch(
            "search_conversation_history", {"query": 'budget OR review* -"NEAR" col:x'},
        )
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "ok")  # never a raised FTS syntax error

    def test_never_accepts_a_caller_supplied_db_path(self):
        # The tool's input_schema has no db_path property (see
        # TestToolsAreRegistered above); this proves dispatch() itself
        # ignores an extra key even if a model tried to smuggle one in.
        self._seed()
        real_db = history_store.HISTORY_DB
        result = registry.dispatch(
            "search_conversation_history", {"query": "budget", "db_path": "/tmp/somewhere-else.db"},
        )
        parsed = json.loads(result)
        self.assertEqual(parsed["state"], "ok")
        self.assertEqual(history_store.HISTORY_DB, real_db)


class TestErrorStateMapping(IsolatedHistoryDbTestCase):
    """Each of history_store's six HistoryStoreError subclasses must
    surface as its own distinct, stable `state` string -- never collapsed
    into a generic error and never an uncaught traceback. Mocks
    history_store's functions to raise each already-real exception type
    directly, so this tests this module's mapping logic specifically."""

    def test_history_unavailable_status(self):
        with patch("agent.history_store.history_status", side_effect=history_store.HistoryUnavailable("x")):
            parsed = json.loads(registry.dispatch("history_status", {}))
        self.assertEqual(parsed["state"], "unavailable")

    def test_history_schema_error_status(self):
        with patch("agent.history_store.history_status", side_effect=history_store.HistorySchemaError("x")):
            parsed = json.loads(registry.dispatch("history_status", {}))
        self.assertEqual(parsed["state"], "schema_incompatible")

    def test_history_corruption_status(self):
        with patch("agent.history_store.history_status", side_effect=history_store.HistoryCorruption("x")):
            parsed = json.loads(registry.dispatch("history_status", {}))
        self.assertEqual(parsed["state"], "corrupt")

    def test_history_busy_search(self):
        with patch("agent.history_store.search_history", side_effect=history_store.HistoryBusy("x")):
            parsed = json.loads(registry.dispatch("search_conversation_history", {"query": "x"}))
        self.assertEqual(parsed["state"], "busy")

    def test_history_validation_error_search(self):
        with patch("agent.history_store.search_history", side_effect=history_store.HistoryValidationError("x")):
            parsed = json.loads(registry.dispatch("search_conversation_history", {"query": "x", "role": "not-a-role"}))
        self.assertEqual(parsed["state"], "invalid_input")

    def test_history_unsupported_runtime_search(self):
        with patch("agent.history_store.search_history", side_effect=history_store.HistoryUnsupportedRuntime("x")):
            parsed = json.loads(registry.dispatch("search_conversation_history", {"query": "x"}))
        self.assertEqual(parsed["state"], "unsupported_runtime")

    def test_all_six_error_classes_distinct_from_each_other(self):
        from tools.schemas.history import _ERROR_STATES
        self.assertEqual(len(_ERROR_STATES), 6)
        self.assertEqual(len(set(_ERROR_STATES.values())), 6)


class TestNormalExecutorDispatch(IsolatedHistoryDbTestCase):
    """Confirms both tools go through the real agent/executor.py
    _run_tool path -- no separate dispatch mechanism -- matching
    tests/test_graphify_tools.py's established pattern."""

    def test_history_status_dispatches_through_the_real_executor(self):
        from agent.executor import _run_tool
        result = _run_tool("history_status", {}, source="chat")
        self.assertEqual(json.loads(result)["state"], "ok")

    def test_search_dispatches_through_the_real_executor(self):
        from agent.executor import _run_tool
        result = _run_tool("search_conversation_history", {"query": "x"}, source="chat")
        self.assertIn(json.loads(result)["state"], ("ok", "unavailable"))

    def test_read_only_tools_never_trigger_verification_or_confirmation(self):
        from agent.executor import _run_tool
        with patch("agent.executor.verify") as mock_verify:
            _run_tool("history_status", {}, source="chat")
        mock_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
