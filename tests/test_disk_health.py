"""Tests for agent/disk_health.py and its two consumers
(tools/system_status.py, agent/greeting.py's prompt block).

Mocks only the external boundaries -- `shutil.disk_usage` (a real
filesystem call) and `subprocess.run` (pmset/df/uptime/ipconfig, macOS-only
binaries) -- never the threshold logic itself. Settings is a frozen
dataclass, so per-test overrides go through object.__setattr__ (see
tests/test_history_context.py for the established convention).

Run with: python -m unittest tests.test_disk_health -v
"""
import os
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import MagicMock, patch

import agent.disk_health as disk_health
from agent.disk_health import (
    LEVEL_CRITICAL,
    LEVEL_LOW,
    LEVEL_OK,
    LEVEL_UNKNOWN,
    DiskHealth,
    check_disk_health,
    format_disk_warning,
)
from agent.greeting import format_greeting_context
from config.settings import Settings, settings
from tools.system_status import get_system_status

_Usage = namedtuple("_Usage", "total used free")
_GB = 1024 ** 3


def _usage_with_free_gb(free_gb, total_gb=200):
    free = int(free_gb * _GB)
    return _Usage(total=total_gb * _GB, used=total_gb * _GB - free, free=free)


def _patched_usage(free_gb):
    return patch.object(disk_health.shutil, "disk_usage", return_value=_usage_with_free_gb(free_gb))


class _ThresholdSettingsTestCase(unittest.TestCase):

    def setUp(self):
        self._real_warning = settings.low_disk_warning_gb
        self._real_critical = settings.low_disk_critical_gb
        object.__setattr__(settings, "low_disk_warning_gb", 5.0)
        object.__setattr__(settings, "low_disk_critical_gb", 1.0)

    def tearDown(self):
        object.__setattr__(settings, "low_disk_warning_gb", self._real_warning)
        object.__setattr__(settings, "low_disk_critical_gb", self._real_critical)


class TestCheckDiskHealth(_ThresholdSettingsTestCase):

    def test_plenty_of_space_is_ok(self):
        with _patched_usage(50):
            health = check_disk_health()
        self.assertEqual(health.level, LEVEL_OK)
        self.assertAlmostEqual(health.free_gb, 50.0, places=3)

    def test_below_warning_is_low(self):
        with _patched_usage(3):
            self.assertEqual(check_disk_health().level, LEVEL_LOW)

    def test_below_critical_is_critical(self):
        with _patched_usage(0.2):
            self.assertEqual(check_disk_health().level, LEVEL_CRITICAL)

    def test_zero_bytes_free_is_critical(self):
        with _patched_usage(0):
            self.assertEqual(check_disk_health().level, LEVEL_CRITICAL)

    def test_exactly_at_warning_threshold_is_still_ok(self):
        with _patched_usage(5):
            self.assertEqual(check_disk_health().level, LEVEL_OK)

    def test_exactly_at_critical_threshold_is_low_not_critical(self):
        with _patched_usage(1):
            self.assertEqual(check_disk_health().level, LEVEL_LOW)

    def test_unreadable_volume_is_unknown_not_an_exception(self):
        with patch.object(disk_health.shutil, "disk_usage", side_effect=OSError("boom")):
            health = check_disk_health()
        self.assertEqual(health.level, LEVEL_UNKNOWN)
        self.assertIsNone(health.free_bytes)
        self.assertIsNone(health.free_gb)

    def test_thresholds_default_to_current_settings_at_call_time(self):
        object.__setattr__(settings, "low_disk_warning_gb", 100.0)
        with _patched_usage(50):
            self.assertEqual(check_disk_health().level, LEVEL_LOW)

    def test_explicit_thresholds_override_settings(self):
        with _patched_usage(50):
            self.assertEqual(check_disk_health(warning_gb=80, critical_gb=60).level, LEVEL_CRITICAL)

    def test_missing_directory_is_measured_at_nearest_existing_ancestor(self):
        with tempfile.TemporaryDirectory() as existing:
            missing = os.path.join(existing, "not", "created", "yet")
            with _patched_usage(50) as fake:
                check_disk_health(path=missing)
        self.assertEqual(os.path.realpath(fake.call_args.args[0]), os.path.realpath(existing))

    def test_real_call_against_real_volume_returns_a_valid_reading(self):
        health = check_disk_health()
        self.assertIn(health.level, (LEVEL_OK, LEVEL_LOW, LEVEL_CRITICAL))
        self.assertGreater(health.total_bytes, 0)
        self.assertGreaterEqual(health.free_bytes, 0)


class TestFormatDiskWarning(unittest.TestCase):

    def test_healthy_and_unknown_produce_no_warning(self):
        self.assertIsNone(format_disk_warning(DiskHealth(LEVEL_OK, 50 * _GB, 200 * _GB)))
        self.assertIsNone(format_disk_warning(DiskHealth(LEVEL_UNKNOWN, None, None)))

    def test_low_warning_names_the_free_space(self):
        text = format_disk_warning(DiskHealth(LEVEL_LOW, int(3.5 * _GB), 200 * _GB))
        self.assertTrue(text.startswith("WARNING:"))
        self.assertIn("3.5 GB", text)
        self.assertNotIn("almost full", text)

    def test_critical_warning_is_distinctly_stronger(self):
        text = format_disk_warning(DiskHealth(LEVEL_CRITICAL, int(0.4 * _GB), 200 * _GB))
        self.assertTrue(text.startswith("WARNING:"))
        self.assertIn("almost full", text)
        self.assertIn("0.4 GB", text)


class TestSystemStatusSurfacesWarning(_ThresholdSettingsTestCase):

    @staticmethod
    def _fake_run(args, **kwargs):
        result = MagicMock()
        result.stdout = "Filesystem Size Used Avail Capacity\n/dev/x 233Gi 200Gi 3Gi 99%\n" if args[0] == "df" else ""
        return result

    def _status(self, free_gb):
        with patch("tools.system_status.subprocess.run", side_effect=self._fake_run), _patched_usage(free_gb):
            return get_system_status()

    def test_low_disk_adds_a_warning_line_after_the_disk_line(self):
        lines = self._status(3).splitlines()
        disk_index = next(i for i, line in enumerate(lines) if line.startswith("Disk:"))
        self.assertTrue(lines[disk_index + 1].startswith("WARNING:"))

    def test_healthy_disk_adds_no_warning(self):
        status = self._status(50)
        self.assertIn("Disk:", status)
        self.assertNotIn("WARNING", status)

    def test_existing_disk_line_is_unchanged_by_the_warning(self):
        self.assertIn("Disk: 3Gi free of 233Gi (99% used)", self._status(0.1))


class TestGreetingBlockRelaysWarning(unittest.TestCase):

    def test_block_tells_model_to_relay_a_warning_line_once_and_only_then(self):
        block = format_greeting_context("Battery: 82%", "Location: Tampa, Florida")
        self.assertIn('line starting with "WARNING:"', block)
        self.assertIn("inside that same single reply", block)
        self.assertIn("otherwise mention nothing", block)

    def test_status_warning_line_reaches_the_block_verbatim(self):
        status = "Disk: 1Gi free of 233Gi (99% used)\nWARNING: disk space is getting low — 1.0 GB free."
        block = format_greeting_context(status, "Location: Tampa, Florida")
        self.assertIn("WARNING: disk space is getting low — 1.0 GB free.", block)


class TestSettingsEnvOverride(unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(Settings.low_disk_warning_gb, 5.0)
        self.assertEqual(Settings.low_disk_critical_gb, 1.0)

    def test_env_overrides_are_honored(self):
        with patch.dict(os.environ, {"LOW_DISK_WARNING_GB": "12.5", "LOW_DISK_CRITICAL_GB": "2"}):
            loaded = Settings.load()
        self.assertEqual(loaded.low_disk_warning_gb, 12.5)
        self.assertEqual(loaded.low_disk_critical_gb, 2.0)


if __name__ == "__main__":
    unittest.main()
