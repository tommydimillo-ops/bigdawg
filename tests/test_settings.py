"""Tests for config/settings.py -- defaults, environment overrides, invalid
values, missing optional values, and that secrets never end up here.

Run with: python -m unittest tests.test_settings -v
"""
import os
import unittest

from config.settings import Settings


class TestDefaults(unittest.TestCase):

    def test_defaults_used_when_nothing_set(self):
        settings = Settings()
        self.assertEqual(settings.default_model, "claude-sonnet-5")
        self.assertEqual(settings.fallback_model, "gpt-5")
        self.assertEqual(settings.max_agent_steps, 8)
        self.assertEqual(settings.model_history_limit, 24)
        self.assertEqual(settings.planner_model, "claude-haiku-4-5-20251001")
        self.assertEqual(settings.scheduler_poll_seconds, 30)
        self.assertEqual(settings.wake_word, "jarvis")
        self.assertEqual(settings.voice_min_signal_level, 100.0)
        self.assertEqual(settings.voice_trigger_chunks, 2)
        self.assertFalse(settings.debug)

    def test_settings_is_frozen(self):
        settings = Settings()
        with self.assertRaises(Exception):
            settings.default_model = "something-else"


class TestEnvironmentOverrides(unittest.TestCase):

    def setUp(self):
        self._to_clear = []

    def tearDown(self):
        for key in self._to_clear:
            os.environ.pop(key, None)

    def _set(self, key, value):
        os.environ[key] = value
        self._to_clear.append(key)

    def test_string_override(self):
        self._set("DEFAULT_MODEL", "claude-opus-5")
        self.assertEqual(Settings.load().default_model, "claude-opus-5")

    def test_int_override(self):
        self._set("MAX_AGENT_STEPS", "12")
        self.assertEqual(Settings.load().max_agent_steps, 12)

    def test_model_history_limit_override(self):
        self._set("MODEL_HISTORY_LIMIT", "10")
        self.assertEqual(Settings.load().model_history_limit, 10)

    def test_float_override(self):
        self._set("API_READ_TIMEOUT", "45.5")
        self.assertEqual(Settings.load().api_read_timeout, 45.5)

    def test_bool_override_true_variants(self):
        for value in ["1", "true", "True", "yes", "on"]:
            with self.subTest(value=value):
                os.environ["DEBUG"] = value
                self.assertTrue(Settings.load().debug)
        del os.environ["DEBUG"]

    def test_bool_override_false_variants(self):
        for value in ["0", "false", "False", "no", "off"]:
            with self.subTest(value=value):
                os.environ["DEBUG"] = value
                self.assertFalse(Settings.load().debug)
        del os.environ["DEBUG"]

    def test_unset_env_vars_fall_back_to_defaults(self):
        # No env vars set in this test -- confirms load() doesn't require
        # everything to be present.
        settings = Settings.load()
        self.assertEqual(settings.default_model, Settings.default_model)


class TestInvalidValues(unittest.TestCase):

    def setUp(self):
        self._to_clear = []

    def tearDown(self):
        for key in self._to_clear:
            os.environ.pop(key, None)

    def _set(self, key, value):
        os.environ[key] = value
        self._to_clear.append(key)

    def test_invalid_int_raises_clear_error(self):
        self._set("MAX_AGENT_STEPS", "not-a-number")
        with self.assertRaises(ValueError) as ctx:
            Settings.load()
        self.assertIn("MAX_AGENT_STEPS", str(ctx.exception))

    def test_invalid_float_raises_clear_error(self):
        self._set("API_READ_TIMEOUT", "not-a-float")
        with self.assertRaises(ValueError) as ctx:
            Settings.load()
        self.assertIn("API_READ_TIMEOUT", str(ctx.exception))

    def test_invalid_bool_raises_clear_error(self):
        self._set("DEBUG", "maybe")
        with self.assertRaises(ValueError) as ctx:
            Settings.load()
        self.assertIn("DEBUG", str(ctx.exception))


class TestMissingOptionalValues(unittest.TestCase):

    def test_empty_string_env_var_falls_back_to_default(self):
        os.environ["JARVIS_USER_NAME"] = ""
        try:
            self.assertEqual(Settings.load().user_name, Settings.user_name)
        finally:
            del os.environ["JARVIS_USER_NAME"]


class TestSecretsNeverLiveHere(unittest.TestCase):

    def test_no_api_key_fields(self):
        field_names = Settings.__dataclass_fields__.keys()
        for name in field_names:
            with self.subTest(field=name):
                lowered = name.lower()
                self.assertNotIn("key", lowered)
                self.assertNotIn("password", lowered)
                # Singular "token" only -- an auth/API/session/bearer
                # token is always singular in a field name; "tokens"
                # (plural) is the ordinary, non-secret LLM-token-count
                # sense (e.g. max_tokens_per_request, Phase 8's usage
                # limit), which this must not flag as a credential.
                self.assertNotRegex(lowered, r"token(?!s)")
                self.assertNotIn("secret", lowered)

    def test_settings_module_does_not_import_secrets_module(self):
        import config.settings as settings_module
        self.assertNotIn("agent.secrets", dir(settings_module))
        self.assertFalse(hasattr(settings_module, "get_secret"))


if __name__ == "__main__":
    unittest.main()
