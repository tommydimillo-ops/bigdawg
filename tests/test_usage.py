"""Tests for agent/usage.py -- Phase 8 part 1's real token/cost accounting
and part 3's usage-limit checks.

USAGE_FILE is redirected to a temp file for every test here, matching this
project's established pattern for every other file-backed store's tests
(see tests/test_execution_history.py) -- so none of this ever touches the
real ~/Library/.../usage_history.json the dashboard reads from.

Run with: python -m unittest tests.test_usage -v
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

import agent.usage as usage
from config.settings import settings


class IsolatedUsageTestCase(unittest.TestCase):

    def setUp(self):
        self._real_usage_file = usage.USAGE_FILE
        usage.USAGE_FILE = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        for path in (usage.USAGE_FILE, f"{usage.USAGE_FILE}.lock"):
            if os.path.exists(path):
                os.remove(path)
        usage.USAGE_FILE = self._real_usage_file


class TestUsageRecordRoundTrip(unittest.TestCase):

    def test_to_dict_and_from_dict_round_trip(self):
        record = usage.UsageRecord(
            request_id="abc123", agent="research", provider="anthropic",
            model="claude-sonnet-5", operation="chat",
            input_tokens=100, output_tokens=20, estimated_cost_usd=0.0006,
        )
        restored = usage.UsageRecord.from_dict(record.to_dict())
        self.assertEqual(restored, record)

    def test_from_dict_ignores_unknown_fields(self):
        data = {
            "request_id": "abc123", "agent": None, "provider": "openai",
            "model": "gpt-5", "operation": "chat", "some_future_field": "ignored",
        }
        restored = usage.UsageRecord.from_dict(data)
        self.assertEqual(restored.provider, "openai")


class TestAsNumberHardening(unittest.TestCase):
    """A mocked API response's `.usage.input_tokens` in a test is a
    MagicMock, not a missing/None value -- this must degrade to 0 rather
    than let a MagicMock flow into a UsageRecord and fail later at JSON
    serialization time."""

    def test_magicmock_degrades_to_zero(self):
        self.assertEqual(usage._as_number(MagicMock(), int), 0)
        self.assertEqual(usage._as_number(MagicMock(), float), 0.0)

    def test_real_numbers_pass_through(self):
        self.assertEqual(usage._as_number(42, int), 42)
        self.assertEqual(usage._as_number(3.5, float), 3.5)

    def test_none_degrades_to_zero(self):
        self.assertEqual(usage._as_number(None, int), 0)

    def test_bool_is_treated_as_number(self):
        self.assertEqual(usage._as_number(True, int), 1)


class TestEstimateCost(unittest.TestCase):

    def test_token_priced_model(self):
        cost = usage._estimate_cost("anthropic", "claude-sonnet-5", 1_000_000, 1_000_000, None, None)
        self.assertAlmostEqual(cost, 3.00 + 15.00)

    def test_per_minute_audio_model(self):
        cost = usage._estimate_cost("openai", "gpt-4o-transcribe", 0, 0, 120.0, None)
        self.assertAlmostEqual(cost, 0.012)

    def test_per_1k_chars_tts_model(self):
        cost = usage._estimate_cost("openai", "gpt-4o-mini-tts", 0, 0, None, 2000)
        self.assertAlmostEqual(cost, 0.03)

    def test_unknown_model_falls_back_to_default_token_price(self):
        cost = usage._estimate_cost("anthropic", "some-future-model", 1_000_000, 0, None, None)
        self.assertAlmostEqual(cost, usage._DEFAULT_TOKEN_PRICE["input"])

    def test_unknown_provider_falls_back_to_default_token_price(self):
        cost = usage._estimate_cost("some-new-provider", "whatever", 0, 1_000_000, None, None)
        self.assertAlmostEqual(cost, usage._DEFAULT_TOKEN_PRICE["output"])


class TestRecordLlmUsage(IsolatedUsageTestCase):

    def test_records_and_persists(self):
        record = usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=1000, output_tokens=200,
        )
        self.assertGreater(record.estimated_cost_usd, 0)
        recent = usage.get_recent()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].request_id, "req-1")

    def test_never_raises_on_magicmock_usage_object(self):
        mock_usage = MagicMock()
        try:
            record = usage.record_llm_usage(
                provider="anthropic", model="claude-sonnet-5", operation="chat",
                request_id="req-1", input_tokens=mock_usage.input_tokens,
                output_tokens=mock_usage.output_tokens,
            )
        except Exception as error:
            self.fail(f"record_llm_usage raised on MagicMock input: {error}")
        self.assertEqual(record.input_tokens, 0)
        self.assertEqual(record.output_tokens, 0)
        self.assertEqual(record.estimated_cost_usd, 0.0)

    def test_persisted_record_is_json_serializable(self):
        import json
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=MagicMock(), output_tokens=MagicMock(),
        )
        with open(usage.USAGE_FILE) as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)

    def test_bounded_by_usage_history_limit(self):
        original_limit = settings.usage_history_limit
        try:
            object.__setattr__(settings, "usage_history_limit", 3)
            for i in range(5):
                usage.record_llm_usage(
                    provider="anthropic", model="claude-sonnet-5", operation="chat",
                    request_id=f"req-{i}",
                )
            self.assertEqual(len(usage.get_recent()), 3)
        finally:
            object.__setattr__(settings, "usage_history_limit", original_limit)


class TestQueryHelpers(IsolatedUsageTestCase):

    def setUp(self):
        super().setUp()
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=1000, output_tokens=100,
        )
        usage.record_llm_usage(
            provider="openai", model="gpt-5", operation="fallback",
            request_id="req-1", input_tokens=2000, output_tokens=200,
        )
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-2", input_tokens=500, output_tokens=50,
        )

    def test_total_cost_for_request_sums_only_matching_request(self):
        cost = usage.total_cost_for_request("req-1")
        expected = usage.total_cost_for_request("req-1")
        self.assertGreater(cost, 0)
        self.assertEqual(cost, expected)
        self.assertLess(usage.total_cost_for_request("req-2"), cost)

    def test_total_tokens_for_request(self):
        self.assertEqual(usage.total_tokens_for_request("req-1"), 1000 + 100 + 2000 + 200)
        self.assertEqual(usage.total_tokens_for_request("req-2"), 500 + 50)
        self.assertEqual(usage.total_tokens_for_request("nonexistent"), 0)

    def test_request_count_for_request(self):
        self.assertEqual(usage.request_count_for_request("req-1"), 2)
        self.assertEqual(usage.request_count_for_request("req-2"), 1)
        self.assertEqual(usage.request_count_for_request("nonexistent"), 0)

    def test_get_since_filters_by_timestamp(self):
        import time
        future_cutoff = time.time() + 3600
        self.assertEqual(usage.get_since(future_cutoff), [])
        self.assertEqual(len(usage.get_since(0)), 3)


class TestCostSince(IsolatedUsageTestCase):
    """cost_since/cost_today back the menu-bar's "Estimated Cost" item --
    None must mean "couldn't read usage data" specifically, never confused
    with a genuine $0.00 day, since the menu bar treats them differently
    (an alert saying data isn't available vs. a real zero-cost figure)."""

    def test_sums_multiple_records(self):
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=1_000_000, output_tokens=0,
        )
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-2", input_tokens=0, output_tokens=1_000_000,
        )
        cost = usage.cost_since(0)
        self.assertAlmostEqual(cost, 3.00 + 15.00)

    def test_empty_usage_history_is_zero_not_none(self):
        self.assertEqual(usage.cost_since(0), 0.0)
        self.assertEqual(usage.cost_today(), 0.0)

    def test_missing_usage_file_is_zero_not_none(self):
        self.assertFalse(os.path.exists(usage.USAGE_FILE))
        self.assertEqual(usage.cost_today(), 0.0)

    def test_corrupt_json_returns_none(self):
        os.makedirs(os.path.dirname(usage.USAGE_FILE), exist_ok=True)
        with open(usage.USAGE_FILE, "w") as f:
            f.write("{not valid json")
        self.assertIsNone(usage.cost_since(0))
        self.assertIsNone(usage.cost_today())

    def test_malformed_record_shape_returns_none(self):
        import json
        os.makedirs(os.path.dirname(usage.USAGE_FILE), exist_ok=True)
        with open(usage.USAGE_FILE, "w") as f:
            json.dump(["not a record dict"], f)
        self.assertIsNone(usage.cost_since(0))

    def test_cost_today_excludes_records_before_midnight(self):
        import time
        yesterday = time.time() - 86400
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-old", input_tokens=1_000_000, output_tokens=0,
        )
        recorded = usage.get_recent()[0]
        recorded.timestamp = yesterday
        usage._save_raw([recorded.to_dict()])
        self.assertEqual(usage.cost_today(), 0.0)


class TestCheckRequestLimits(IsolatedUsageTestCase):

    def setUp(self):
        super().setUp()
        self._originals = {
            "max_requests_per_execution": settings.max_requests_per_execution,
            "max_tokens_per_request": settings.max_tokens_per_request,
            "max_cost_per_request_usd": settings.max_cost_per_request_usd,
        }

    def tearDown(self):
        for name, value in self._originals.items():
            object.__setattr__(settings, name, value)
        super().tearDown()

    def test_no_request_id_never_exceeds(self):
        result = usage.check_request_limits(None)
        self.assertFalse(result.exceeded)

    def test_no_usage_recorded_never_exceeds(self):
        result = usage.check_request_limits("req-empty")
        self.assertFalse(result.exceeded)

    def test_max_requests_per_execution_exceeded(self):
        object.__setattr__(settings, "max_requests_per_execution", 2)
        for _ in range(2):
            usage.record_llm_usage(provider="anthropic", model="claude-sonnet-5", operation="chat", request_id="req-1")
        result = usage.check_request_limits("req-1")
        self.assertTrue(result.exceeded)
        self.assertIn("max_requests_per_execution", result.reason)

    def test_max_tokens_per_request_exceeded(self):
        object.__setattr__(settings, "max_tokens_per_request", 100)
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=80, output_tokens=30,
        )
        result = usage.check_request_limits("req-1")
        self.assertTrue(result.exceeded)
        self.assertIn("max_tokens_per_request", result.reason)

    def test_max_cost_per_request_usd_exceeded(self):
        object.__setattr__(settings, "max_cost_per_request_usd", 0.001)
        object.__setattr__(settings, "max_tokens_per_request", None)
        object.__setattr__(settings, "max_requests_per_execution", None)
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=1_000_000, output_tokens=1_000_000,
        )
        result = usage.check_request_limits("req-1")
        self.assertTrue(result.exceeded)
        self.assertIn("max_cost_per_request_usd", result.reason)

    def test_disabled_limits_never_exceed_regardless_of_usage(self):
        object.__setattr__(settings, "max_requests_per_execution", None)
        object.__setattr__(settings, "max_tokens_per_request", None)
        object.__setattr__(settings, "max_cost_per_request_usd", None)
        for _ in range(50):
            usage.record_llm_usage(
                provider="anthropic", model="claude-sonnet-5", operation="chat",
                request_id="req-1", input_tokens=1_000_000, output_tokens=1_000_000,
            )
        result = usage.check_request_limits("req-1")
        self.assertFalse(result.exceeded)

    def test_under_all_limits_does_not_exceed(self):
        object.__setattr__(settings, "max_requests_per_execution", 25)
        object.__setattr__(settings, "max_tokens_per_request", 200_000)
        object.__setattr__(settings, "max_cost_per_request_usd", 1.00)
        usage.record_llm_usage(
            provider="anthropic", model="claude-sonnet-5", operation="chat",
            request_id="req-1", input_tokens=100, output_tokens=20,
        )
        result = usage.check_request_limits("req-1")
        self.assertFalse(result.exceeded)


if __name__ == "__main__":
    unittest.main()
