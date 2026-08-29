"""Tests for agent/history_context.py -- Phase 9 M4.4's foundation:
relevance-filtered, budget-limited, opt-in history retrieval. Exercises
the real agent/history_store.py against a redirected HISTORY_DB (same
"internal application logic, not an external boundary" reasoning as
tests/test_history_tools.py), plus real settings.* field patching via
object.__setattr__ (Settings is a frozen dataclass -- see
tests/test_openclaw_gateway.py for the established convention).

Run with: python -m unittest tests.test_history_context -v
"""
import tempfile
import unittest
from unittest.mock import patch

import agent.history_context as history_context
import agent.history_store as history_store
from agent.history_context import build_history_context
from config.settings import settings


class IsolatedHistoryContextTestCase(unittest.TestCase):

    def setUp(self):
        self._real_history_db = history_store.HISTORY_DB
        history_store.HISTORY_DB = tempfile.mktemp(suffix=".db")

        self._real_enabled = settings.proactive_history_enabled
        self._real_budget = settings.history_context_budget_tokens
        self._real_timeout = settings.history_context_timeout_ms
        self._real_max_results = settings.history_context_max_results
        object.__setattr__(settings, "proactive_history_enabled", True)
        object.__setattr__(settings, "history_context_budget_tokens", 500)
        object.__setattr__(settings, "history_context_timeout_ms", 150)
        object.__setattr__(settings, "history_context_max_results", 3)

    def tearDown(self):
        history_store.HISTORY_DB = self._real_history_db
        object.__setattr__(settings, "proactive_history_enabled", self._real_enabled)
        object.__setattr__(settings, "history_context_budget_tokens", self._real_budget)
        object.__setattr__(settings, "history_context_timeout_ms", self._real_timeout)
        object.__setattr__(settings, "history_context_max_results", self._real_max_results)

    def _seed(self, n=1, prefix="the quarterly budget review"):
        history_store.initialize_history_store(db_path=history_store.HISTORY_DB)
        history_store.create_session("chat", session_id="s1", db_path=history_store.HISTORY_DB)
        for i in range(n):
            history_store.record_turn(
                "s1", "user", f"{prefix} number {i}", request_id=f"req-{i}",
                db_path=history_store.HISTORY_DB,
            )


class TestDisabledByDefault(IsolatedHistoryContextTestCase):

    def test_disabled_returns_empty_without_opening_the_database(self):
        object.__setattr__(settings, "proactive_history_enabled", False)
        with patch("agent.history_context.history_store.search_history") as mock_search:
            context = build_history_context("tell me about the budget review")
        mock_search.assert_not_called()
        self.assertEqual(context.retrieved, [])
        self.assertEqual(context.prompt_text, "")

    def test_default_setting_value_is_true(self):
        # Confirms the real, unpatched default -- setUp above already
        # turns it on for the rest of this file, so this checks the
        # class attribute directly rather than the live (test-patched)
        # instance. Was False at ship time (ROADMAP.md's Phase 9/M4.4
        # entry); flipped to True once the real-use evidence-gathering
        # period itself needed to begin.
        from config.settings import Settings
        self.assertTrue(Settings.proactive_history_enabled)


class TestEmptyInput(IsolatedHistoryContextTestCase):

    def test_empty_string_returns_empty_without_opening_the_database(self):
        with patch("agent.history_context.history_store.search_history") as mock_search:
            context = build_history_context("")
        mock_search.assert_not_called()
        self.assertEqual(context.prompt_text, "")

    def test_whitespace_only_returns_empty(self):
        with patch("agent.history_context.history_store.search_history") as mock_search:
            context = build_history_context("   ")
        mock_search.assert_not_called()
        self.assertEqual(context.prompt_text, "")


class TestEnabledPathReturnsFormattedBlock(IsolatedHistoryContextTestCase):

    def test_real_hit_produces_a_formatted_block_with_provenance(self):
        self._seed(n=1)
        context = build_history_context("budget review")
        self.assertEqual(len(context.retrieved), 1)
        text = context.prompt_text
        self.assertIn("RELEVANT PAST CONVERSATIONS", text)
        self.assertIn("cite what you use".lower(), text.lower())
        item = context.retrieved[0]
        self.assertIn(item.result.created_at, text)
        self.assertIn(item.result.source, text)
        self.assertIn(item.result.role, text)
        self.assertIn(item.result.snippet, text)

    def test_no_match_returns_empty_not_an_error(self):
        self._seed(n=1, prefix="something entirely unrelated")
        context = build_history_context("zzz_no_such_term_zzz")
        self.assertEqual(context.retrieved, [])
        self.assertEqual(context.prompt_text, "")

    def test_max_results_setting_is_passed_through(self):
        with patch("agent.history_context.history_store.search_history") as mock_search:
            mock_search.return_value = []
            build_history_context("budget review")
        self.assertEqual(mock_search.call_args.kwargs["max_results"], 3)

    def test_short_timeout_setting_is_passed_through(self):
        with patch("agent.history_context.history_store.search_history") as mock_search:
            mock_search.return_value = []
            build_history_context("budget review")
        self.assertEqual(mock_search.call_args.kwargs["busy_timeout_ms"], 150)

    def test_never_accepts_or_forwards_a_caller_supplied_db_path(self):
        # build_history_context has no db_path parameter at all -- this
        # just confirms the real store's own HISTORY_DB attribute is
        # what's actually used, matching agent/history_capture.py's
        # established pattern.
        with patch("agent.history_context.history_store.search_history") as mock_search:
            mock_search.return_value = []
            build_history_context("budget review")
        self.assertEqual(mock_search.call_args.kwargs["db_path"], history_store.HISTORY_DB)


class TestBudgetStopsBeforeOverflow(IsolatedHistoryContextTestCase):

    def test_budget_drops_the_remainder_whole_never_truncates(self):
        self._seed(n=5, prefix="the quarterly budget review meeting notes today")
        # Each snippet is nonzero tokens; a budget of exactly one snippet's
        # worth means only the top-ranked hit fits, the rest are dropped
        # whole (never a partial/truncated snippet appended).
        object.__setattr__(settings, "history_context_max_results", 5)
        first_pass = build_history_context("quarterly budget review meeting")
        if not first_pass.retrieved:
            self.skipTest("seed data did not produce a match to size the budget against")
        one_hit_tokens = first_pass.retrieved[0].approx_tokens

        object.__setattr__(settings, "history_context_budget_tokens", one_hit_tokens)
        context = build_history_context("quarterly budget review meeting")

        self.assertEqual(len(context.retrieved), 1)
        # The one included hit's snippet is exactly the top-ranked hit's
        # own snippet from the unbudgeted pass -- never a truncated
        # fragment of it.
        self.assertEqual(context.retrieved[0].result.snippet, first_pass.retrieved[0].result.snippet)
        self.assertEqual(context.retrieved[0].result.turn_id, first_pass.retrieved[0].result.turn_id)

    def test_zero_budget_includes_nothing(self):
        self._seed(n=1)
        object.__setattr__(settings, "history_context_budget_tokens", 0)
        context = build_history_context("budget review")
        self.assertEqual(context.retrieved, [])
        self.assertEqual(context.prompt_text, "")


class TestErrorStatesNeverRaise(IsolatedHistoryContextTestCase):

    def _assert_empty_and_silent(self, exc):
        with patch("agent.history_context.history_store.search_history", side_effect=exc):
            context = build_history_context("anything")
        self.assertEqual(context.retrieved, [])
        self.assertEqual(context.prompt_text, "")

    def test_history_unavailable_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistoryUnavailable("x"))

    def test_history_schema_error_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistorySchemaError("x"))

    def test_history_corruption_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistoryCorruption("x"))

    def test_history_busy_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistoryBusy("x"))

    def test_history_validation_error_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistoryValidationError("x"))

    def test_history_unsupported_runtime_returns_empty(self):
        self._assert_empty_and_silent(history_store.HistoryUnsupportedRuntime("x"))

    def test_unavailable_is_silent_busy_is_debug_others_are_warning(self):
        with patch("agent.history_context.log_event") as mock_log:
            with patch(
                "agent.history_context.history_store.search_history",
                side_effect=history_store.HistoryUnavailable("x"),
            ):
                build_history_context("anything")
            mock_log.assert_not_called()

        with patch("agent.history_context.log_event") as mock_log:
            with patch(
                "agent.history_context.history_store.search_history",
                side_effect=history_store.HistoryBusy("x"),
            ):
                build_history_context("anything")
            mock_log.assert_called_once()
            self.assertEqual(mock_log.call_args.kwargs["level"], "debug")

        with patch("agent.history_context.log_event") as mock_log:
            with patch(
                "agent.history_context.history_store.search_history",
                side_effect=history_store.HistoryCorruption("x"),
            ):
                build_history_context("anything")
            mock_log.assert_called_once()
            self.assertEqual(mock_log.call_args.kwargs["level"], "warning")


class TestLogEventsEmittedPerHit(IsolatedHistoryContextTestCase):

    def test_one_history_retrieved_event_per_included_hit(self):
        self._seed(n=2, prefix="quarterly budget review")
        with patch("agent.history_context.log_event") as mock_log:
            context = build_history_context("quarterly budget review")
        self.assertEqual(mock_log.call_count, len(context.retrieved))
        self.assertGreater(len(context.retrieved), 0)
        for call in mock_log.call_args_list:
            self.assertEqual(call.args[0], "history_retrieved")
            self.assertIn("turn_id", call.kwargs)
            self.assertIn("approx_tokens", call.kwargs)
            self.assertTrue(call.kwargs["included"])

    def test_dropped_hits_beyond_budget_get_no_log_event(self):
        self._seed(n=3, prefix="quarterly budget review meeting")
        object.__setattr__(settings, "history_context_max_results", 3)
        first_pass = build_history_context("quarterly budget review meeting")
        if len(first_pass.retrieved) < 2:
            self.skipTest("seed data did not produce enough matches to test a partial drop")

        object.__setattr__(settings, "history_context_budget_tokens", first_pass.retrieved[0].approx_tokens)
        with patch("agent.history_context.log_event") as mock_log:
            context = build_history_context("quarterly budget review meeting")
        self.assertEqual(len(context.retrieved), 1)
        self.assertEqual(mock_log.call_count, 1)


class TestRetrievalEvidenceSummary(unittest.TestCase):
    """M4.5: aggregation logic only -- events_since() itself (the real
    log-reading half) is agent/observability.py's own concern and
    already covered by tests/test_observability.py::TestEventsSince.
    Mocked here at that boundary so these tests exercise exactly the
    counting/grouping logic, not file I/O."""

    def setUp(self):
        self._real_max_results = settings.history_context_max_results
        object.__setattr__(settings, "history_context_max_results", 3)

    def tearDown(self):
        object.__setattr__(settings, "history_context_max_results", self._real_max_results)

    def test_unreadable_log_returns_none(self):
        with patch("agent.history_context.events_since", return_value=None):
            self.assertIsNone(history_context.retrieval_evidence_summary())

    def test_empty_log_returns_all_zeros(self):
        with patch("agent.history_context.events_since", return_value=[]):
            summary = history_context.retrieval_evidence_summary()
        self.assertEqual(summary.total_requests, 0)
        self.assertEqual(summary.requests_with_retrieval, 0)
        self.assertEqual(summary.total_hits, 0)
        self.assertEqual(summary.total_tokens_added, 0)
        self.assertEqual(summary.max_tokens_in_a_single_request, 0)
        self.assertEqual(summary.requests_with_hits_below_max_results, 0)
        self.assertEqual(summary.failures_by_reason, {})

    def test_counts_distinct_requests_and_requests_with_retrieval(self):
        records = [
            {"event": "request_started", "request_id": "r1"},
            {"event": "request_started", "request_id": "r2"},
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 10},
        ]
        with patch("agent.history_context.events_since", return_value=records):
            summary = history_context.retrieval_evidence_summary()
        self.assertEqual(summary.total_requests, 2)
        self.assertEqual(summary.requests_with_retrieval, 1)

    def test_sums_hits_and_tokens_across_requests(self):
        records = [
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 10},
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 20},
            {"event": "history_retrieved", "request_id": "r2", "approx_tokens": 5},
        ]
        with patch("agent.history_context.events_since", return_value=records):
            summary = history_context.retrieval_evidence_summary()
        self.assertEqual(summary.total_hits, 3)
        self.assertEqual(summary.total_tokens_added, 35)
        self.assertEqual(summary.max_tokens_in_a_single_request, 30)  # r1's 10+20

    def test_below_max_results_counts_requests_with_fewer_hits_than_the_setting(self):
        records = [
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 10},
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 10},
            {"event": "history_retrieved", "request_id": "r1", "approx_tokens": 10},
            {"event": "history_retrieved", "request_id": "r2", "approx_tokens": 10},
        ]
        with patch("agent.history_context.events_since", return_value=records):
            summary = history_context.retrieval_evidence_summary()
        # r1 has 3 hits (== max_results, not below); r2 has 1 (below).
        self.assertEqual(summary.requests_with_hits_below_max_results, 1)

    def test_groups_failures_by_reason_and_error_type(self):
        records = [
            {"event": "history_retrieval_skipped", "reason": "busy", "error_type": "HistoryBusy"},
            {"event": "history_retrieval_skipped", "reason": "busy", "error_type": "HistoryBusy"},
            {"event": "history_retrieval_skipped", "reason": "store_error", "error_type": "HistoryCorruption"},
        ]
        with patch("agent.history_context.events_since", return_value=records):
            summary = history_context.retrieval_evidence_summary()
        self.assertEqual(summary.failures_by_reason, {"busy:HistoryBusy": 2, "store_error:HistoryCorruption": 1})

    def test_records_with_missing_fields_do_not_crash_and_are_skipped(self):
        records = [
            {"event": "history_retrieved", "request_id": "r1"},  # no approx_tokens
            {"event": "history_retrieved", "approx_tokens": 10},  # no request_id
        ]
        with patch("agent.history_context.events_since", return_value=records):
            summary = history_context.retrieval_evidence_summary()
        self.assertEqual(summary.total_hits, 0)
        self.assertEqual(summary.total_tokens_added, 0)

    def test_since_timestamp_is_passed_through_to_events_since(self):
        with patch("agent.history_context.events_since", return_value=[]) as mock_events:
            history_context.retrieval_evidence_summary(since_timestamp=12345.0)
        mock_events.assert_called_once_with(12345.0)


if __name__ == "__main__":
    unittest.main()
