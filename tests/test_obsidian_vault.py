"""Tests for agent/obsidian_vault.py -- the optional, human-readable
Obsidian vault integration (separate from agent/memory/, untouched by
this). Every test isolates both settings.obsidian_vault_path (via
@patch("agent.obsidian_vault.settings")) and the module's project-
relative default-vault fallback (agent.obsidian_vault._DEFAULT_VAULT_DIR,
redirected to a temp path) so nothing here ever touches this
repository's own real JarvisVault/ directory.

Run with: python -m unittest tests.test_obsidian_vault -v
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the registry
import agent.obsidian_vault as obsidian_vault
from tools import registry


class _IsolatedVaultTestCase(unittest.TestCase):
    """Redirects the project-relative default-vault fallback to a path
    inside a fresh temp directory that doesn't exist unless a test
    explicitly creates it -- so "no vault configured" is genuinely true
    by default, and nothing here can ever resolve to this repository's
    real JarvisVault/."""

    def setUp(self):
        self._real_default_vault_dir = obsidian_vault._DEFAULT_VAULT_DIR
        self._temp_dir = tempfile.mkdtemp(prefix="obsidian-test-")
        obsidian_vault._DEFAULT_VAULT_DIR = os.path.join(self._temp_dir, "DefaultVault")

    def tearDown(self):
        obsidian_vault._DEFAULT_VAULT_DIR = self._real_default_vault_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _make_vault(self, name="ExplicitVault"):
        vault_dir = os.path.join(self._temp_dir, name)
        os.makedirs(vault_dir, exist_ok=True)
        return vault_dir


class TestVaultDiscovery(_IsolatedVaultTestCase):

    def test_not_available_when_nothing_configured(self):
        self.assertFalse(obsidian_vault.is_available())

    @patch("agent.obsidian_vault.settings")
    def test_explicit_setting_wins_when_it_exists(self, mock_settings):
        vault = self._make_vault()
        mock_settings.obsidian_vault_path = vault
        self.assertTrue(obsidian_vault.is_available())
        self.assertEqual(obsidian_vault._resolve_vault_root(), vault)

    @patch("agent.obsidian_vault.settings")
    def test_falls_back_to_project_relative_default(self, mock_settings):
        mock_settings.obsidian_vault_path = None
        os.makedirs(obsidian_vault._DEFAULT_VAULT_DIR, exist_ok=True)
        self.assertTrue(obsidian_vault.is_available())
        self.assertEqual(obsidian_vault._resolve_vault_root(), obsidian_vault._DEFAULT_VAULT_DIR)

    @patch("agent.obsidian_vault.settings")
    def test_explicit_setting_pointing_nowhere_falls_back_to_default(self, mock_settings):
        mock_settings.obsidian_vault_path = "/nonexistent/not-a-real-path"
        os.makedirs(obsidian_vault._DEFAULT_VAULT_DIR, exist_ok=True)
        self.assertEqual(obsidian_vault._resolve_vault_root(), obsidian_vault._DEFAULT_VAULT_DIR)

    @patch("agent.obsidian_vault.settings")
    def test_not_available_when_configured_path_and_default_both_missing(self, mock_settings):
        mock_settings.obsidian_vault_path = "/nonexistent/not-a-real-path"
        self.assertFalse(obsidian_vault.is_available())


class TestReadNote(_IsolatedVaultTestCase):

    def setUp(self):
        super().setUp()
        self.vault = self._make_vault()
        self._settings_patch = patch("agent.obsidian_vault.settings")
        mock_settings = self._settings_patch.start()
        mock_settings.obsidian_vault_path = self.vault

    def tearDown(self):
        self._settings_patch.stop()
        super().tearDown()

    def test_reads_an_existing_note(self):
        with open(os.path.join(self.vault, "hello.md"), "w") as f:
            f.write("Hello, world.")
        content, error = obsidian_vault.read_note("hello.md")
        self.assertIsNone(error)
        self.assertEqual(content, "Hello, world.")

    def test_extension_is_added_automatically(self):
        with open(os.path.join(self.vault, "hello.md"), "w") as f:
            f.write("Hello, world.")
        content, error = obsidian_vault.read_note("hello")
        self.assertIsNone(error)
        self.assertEqual(content, "Hello, world.")

    def test_missing_note_returns_clear_error_not_exception(self):
        content, error = obsidian_vault.read_note("does-not-exist.md")
        self.assertIsNone(content)
        self.assertIn("No note found", error)

    def test_path_escaping_the_vault_is_rejected(self):
        content, error = obsidian_vault.read_note("../../../etc/passwd")
        self.assertIsNone(content)
        self.assertIn("outside the vault", error)

    def test_malformed_note_undecodable_bytes_does_not_crash(self):
        bad_path = os.path.join(self.vault, "bad.md")
        with open(bad_path, "wb") as f:
            f.write(b"\xff\xfe\x00not valid utf-8 \x80\x81")
        content, error = obsidian_vault.read_note("bad.md")
        self.assertIsNone(content)
        self.assertIsNotNone(error)

    def test_reads_a_note_in_a_subfolder(self):
        subfolder = os.path.join(self.vault, "Knowledge")
        os.makedirs(subfolder)
        with open(os.path.join(subfolder, "python.md"), "w") as f:
            f.write("Python notes.")
        content, error = obsidian_vault.read_note("Knowledge/python.md")
        self.assertIsNone(error)
        self.assertEqual(content, "Python notes.")


class TestWriteNote(_IsolatedVaultTestCase):

    def setUp(self):
        super().setUp()
        self.vault = self._make_vault()
        self._settings_patch = patch("agent.obsidian_vault.settings")
        mock_settings = self._settings_patch.start()
        mock_settings.obsidian_vault_path = self.vault

    def tearDown(self):
        self._settings_patch.stop()
        super().tearDown()

    def test_creates_a_new_note(self):
        success, error = obsidian_vault.write_note("new.md", "Some content.")
        self.assertTrue(success)
        self.assertIsNone(error)
        with open(os.path.join(self.vault, "new.md")) as f:
            self.assertIn("Some content.", f.read())

    def test_creates_parent_folders_automatically(self):
        success, error = obsidian_vault.write_note("Projects/jarvis.md", "Project note.")
        self.assertTrue(success)
        self.assertTrue(os.path.isfile(os.path.join(self.vault, "Projects", "jarvis.md")))

    def test_overwrite_replaces_existing_content_by_default(self):
        obsidian_vault.write_note("note.md", "First version.")
        obsidian_vault.write_note("note.md", "Second version.")
        content, _ = obsidian_vault.read_note("note.md")
        self.assertNotIn("First version", content)
        self.assertIn("Second version", content)

    def test_append_adds_to_existing_note(self):
        obsidian_vault.write_note("log.md", "First entry.")
        obsidian_vault.write_note("log.md", "Second entry.", append=True)
        content, _ = obsidian_vault.read_note("log.md")
        self.assertIn("First entry.", content)
        self.assertIn("Second entry.", content)

    def test_append_creates_the_note_if_missing(self):
        success, error = obsidian_vault.write_note("new-log.md", "First entry.", append=True)
        self.assertTrue(success)
        content, _ = obsidian_vault.read_note("new-log.md")
        self.assertIn("First entry.", content)

    def test_refuses_content_that_looks_like_a_credential(self):
        success, error = obsidian_vault.write_note("secrets.md", "api_key: sk-ant-abcdefghijklmnopqrstuvwxyz")
        self.assertFalse(success)
        self.assertIn("Refused", error)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "secrets.md")))

    def test_path_escaping_the_vault_is_rejected(self):
        success, error = obsidian_vault.write_note("../outside.md", "content")
        self.assertFalse(success)
        self.assertIn("outside the vault", error)


class TestSearchNotes(_IsolatedVaultTestCase):

    def setUp(self):
        super().setUp()
        self.vault = self._make_vault()
        self._settings_patch = patch("agent.obsidian_vault.settings")
        mock_settings = self._settings_patch.start()
        mock_settings.obsidian_vault_path = self.vault

    def tearDown(self):
        self._settings_patch.stop()
        super().tearDown()

    def _write(self, path, content):
        full = os.path.join(self.vault, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    def test_finds_a_relevant_note(self):
        self._write("Knowledge/python.md", "Notes about the Python programming language.")
        self._write("Knowledge/weather.md", "Notes about local weather patterns.")
        results, error = obsidian_vault.search_notes("python programming")
        self.assertIsNone(error)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, os.path.join("Knowledge", "python.md"))

    def test_more_relevant_note_ranks_first(self):
        # Scoring is unique significant-word overlap (matching agent/
        # memory/manager.py's own _relevance_score design), not term
        # frequency -- a note matching more distinct query words ranks
        # above one matching fewer, regardless of repetition.
        self._write("a.md", "python programming language basics")
        self._write("b.md", "just mentions python")
        results, _ = obsidian_vault.search_notes("python programming language")
        self.assertEqual([r.path for r in results], ["a.md", "b.md"])

    def test_no_match_returns_empty_list_not_error(self):
        self._write("a.md", "content about gardening")
        results, error = obsidian_vault.search_notes("astrophysics")
        self.assertEqual(results, [])
        self.assertIsNone(error)

    def test_skips_the_obsidian_config_directory(self):
        os.makedirs(os.path.join(self.vault, ".obsidian"), exist_ok=True)
        with open(os.path.join(self.vault, ".obsidian", "workspace.json"), "w") as f:
            f.write('{"query": "python"}')
        results, _ = obsidian_vault.search_notes("query python")
        self.assertEqual(results, [])

    def test_respects_the_limit(self):
        for i in range(5):
            self._write(f"note{i}.md", "python content")
        results, _ = obsidian_vault.search_notes("python", limit=2)
        self.assertEqual(len(results), 2)


class TestVaultUnavailable(_IsolatedVaultTestCase):
    """Obsidian integration disabled / vault missing -- every operation
    must fail gracefully with a clear message, never raise, so the rest
    of Jarvis keeps working."""

    def test_read_when_unavailable(self):
        content, error = obsidian_vault.read_note("anything.md")
        self.assertIsNone(content)
        self.assertIn("not configured or not available", error)

    def test_write_when_unavailable(self):
        success, error = obsidian_vault.write_note("anything.md", "content")
        self.assertFalse(success)
        self.assertIn("not configured or not available", error)

    def test_search_when_unavailable(self):
        results, error = obsidian_vault.search_notes("anything")
        self.assertEqual(results, [])
        self.assertIn("not configured or not available", error)

    def test_is_available_is_false(self):
        self.assertFalse(obsidian_vault.is_available())


class TestObsidianTools(_IsolatedVaultTestCase):
    """The registered tools (tools/schemas/obsidian.py), exercised through
    the real dispatch path (tools.registry.dispatch), not called
    directly -- proves the schema/handler wiring actually works, not
    just the underlying module."""

    def setUp(self):
        super().setUp()
        self.vault = self._make_vault()
        self._settings_patch = patch("agent.obsidian_vault.settings")
        mock_settings = self._settings_patch.start()
        mock_settings.obsidian_vault_path = self.vault

    def tearDown(self):
        self._settings_patch.stop()
        super().tearDown()

    def test_tools_are_registered(self):
        for name in ("read_obsidian_note", "search_obsidian_vault", "write_obsidian_note"):
            self.assertIsNotNone(registry.get(name), f"{name} not registered")

    def test_write_then_read_through_dispatch(self):
        write_result = registry.dispatch("write_obsidian_note", {"path": "note.md", "content": "hi there"})
        self.assertIn("Saved note", write_result)
        read_result = registry.dispatch("read_obsidian_note", {"path": "note.md"})
        self.assertEqual(read_result, "hi there\n")

    def test_search_through_dispatch(self):
        registry.dispatch("write_obsidian_note", {"path": "topic.md", "content": "all about kayaking"})
        result = registry.dispatch("search_obsidian_vault", {"query": "kayaking"})
        self.assertIn("topic.md", result)

    def test_read_missing_note_through_dispatch_returns_message_not_exception(self):
        result = registry.dispatch("read_obsidian_note", {"path": "nope.md"})
        self.assertIn("No note found", result)

    def test_write_refuses_credential_through_dispatch(self):
        result = registry.dispatch(
            "write_obsidian_note", {"path": "s.md", "content": "password: hunter2ishuntertoolong"},
        )
        self.assertIn("Could not save note", result)


if __name__ == "__main__":
    unittest.main()
