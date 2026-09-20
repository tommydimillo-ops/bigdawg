"""Tests for agent/secret_paths.py -- plan-b8's read-side chokepoint -- and
for every model-reachable reader that must go through it.

A bad write is recoverable (that is what the checkpoint is for); file
content read into a prompt has left the machine and the only remedy is
rotating every key it touched. So the assertion that matters most in the
end-to-end tests is not "a refusal message came back" but "the secret
string never appears anywhere in the payload sent to the model".

Everything here uses FAKE secrets in throwaway temp directories -- nothing
reads or writes a real `.env`, Keychain entry, or key file, and nothing
touches the network (the S1 safety harness blocks it regardless).

The ResearchAgent test is the one that proves the chokepoint is a
chokepoint rather than a single-site patch: `_read_file` (CodingAgent) and
`read_document` (registered tool + ResearchAgent) are different
implementations, and one shared function refuses both.

Run with: python -m unittest tests.test_secret_paths -v
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import tools.schemas  # noqa: F401 -- populates the tool registry
import agent.agents.coding as coding
import agent.obsidian_vault as obsidian_vault
import agent.research_agent as research_agent
from agent.audit import recent_actions
from agent.secret_paths import refuse_secret_read, secret_path_reason
from documents.reader import read_document
from tests.test_agents_coding_enabled import (
    CodingAgentEnabledTestCase,
    _git,
    _response,
    _text_block,
    _tool_use_block,
)
from tools import registry

FAKE_SECRET = "sk-FAKE-not-a-real-secret-do-not-use-0123456789"


def _refusals_by(reader):
    return [
        entry for entry in recent_actions(limit=300)
        if entry["tool"] == "secret_read_refused" and f"'{reader}'" in str(entry["input"])
    ]


class TestSecretPathReason(unittest.TestCase):

    def test_env_files_are_refused_at_any_depth(self):
        for path in (
            ".env", ".env.local", ".env.production", ".env.txt", "config/.env",
            "a/b/c/.env.staging", "/abs/path/.env", "~/proj/.env",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_path_reason(path))

    def test_each_secret_filename_pattern_is_refused(self):
        for path in (
            "server.pem", "tls/private.key", "id_rsa", "id_rsa.pub", "id_ed25519", "id_ecdsa",
            "id_dsa", "cert.p12", "login.keychain", "login.keychain-db", "credentials",
            "credentials.json", "aws/credentials", "github_token.txt", "my_token", "api_token.md",
            "notes.secret",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_path_reason(path))

    def test_matching_is_case_insensitive(self):
        for path in (".ENV", ".Env.Local", "SERVER.PEM", "Credentials.JSON", "ID_RSA"):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_path_reason(path))

    def test_keychain_directory_contents_are_refused_whatever_they_are_called(self):
        for path in (
            "/Users/someone/Library/Keychains/login.keychain-db",
            "/Users/someone/Library/Keychains/anything.txt",
            "~/Library/Keychains/metadata.db",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_path_reason(path))

    def test_tracked_example_files_are_carved_out(self):
        for path in (
            ".env.example", ".env.sample", ".env.template", "config/.env.example",
            ".ENV.EXAMPLE", "/abs/.env.template",
        ):
            with self.subTest(path=path):
                self.assertIsNone(secret_path_reason(path))

    def test_the_carve_out_is_exact_not_a_prefix(self):
        for path in (".env.example.bak", ".env.examples", ".env.sample.local"):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_path_reason(path))

    def test_ordinary_files_are_allowed_including_gitignored_ones_like_logs(self):
        # The write-side "refuse anything gitignored" rule must NOT transfer:
        # reading logs/ to debug is legitimate.
        for path in (
            "agent/executor.py", "README.md", "tests/test_x.py", "docs/notes.md",
            "logs/menubar.err.log", "build/output.txt", "environment.md", "my.env.py",
            "dotenv_loader.py", "token_notes.md", "keyboard.py", "monkey.py",
            "JarvisVault/Knowledge/Decisions/note.md",
        ):
            with self.subTest(path=path):
                self.assertIsNone(secret_path_reason(path))

    def test_empty_and_odd_input_never_raises(self):
        for path in ("", "   ", None):
            self.assertIsNone(secret_path_reason(path))


class TestResolvedPathMatching(unittest.TestCase):

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="secret-paths-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        with open(os.path.join(self.dir, ".env"), "w") as file:
            file.write(f"API_KEY={FAKE_SECRET}\n")
        with open(os.path.join(self.dir, "harmless.txt"), "w") as file:
            file.write("nothing secret\n")

    def test_a_symlink_with_an_innocent_name_is_caught_by_its_resolved_target(self):
        os.symlink(".env", os.path.join(self.dir, "notes.txt"))
        self.assertIsNotNone(secret_path_reason(os.path.join(self.dir, "notes.txt")))

    def test_a_symlink_named_like_an_example_file_cannot_launder_the_real_secret(self):
        os.symlink(".env", os.path.join(self.dir, ".env.example"))
        self.assertIsNotNone(secret_path_reason(os.path.join(self.dir, ".env.example")))

    def test_a_secret_named_symlink_to_something_harmless_is_refused_too_stricter_never_looser(self):
        os.symlink("harmless.txt", os.path.join(self.dir, ".env.local"))
        self.assertIsNotNone(secret_path_reason(os.path.join(self.dir, ".env.local")))

    def test_a_symlink_into_a_real_secret_directory_component_is_caught(self):
        os.makedirs(os.path.join(self.dir, "Library", "Keychains"))
        with open(os.path.join(self.dir, "Library", "Keychains", "innocent.txt"), "w") as file:
            file.write("x")
        os.symlink(os.path.join(self.dir, "Library", "Keychains"), os.path.join(self.dir, "shortcut"))
        self.assertIsNotNone(secret_path_reason(os.path.join(self.dir, "shortcut", "innocent.txt")))


class TestRefuseSecretRead(unittest.TestCase):

    def test_allowed_path_returns_none_and_writes_no_audit_entry(self):
        before = len(_refusals_by("unit-test-reader"))
        self.assertIsNone(refuse_secret_read("agent/executor.py", reader="unit-test-reader"))
        self.assertEqual(len(_refusals_by("unit-test-reader")), before)

    def test_refusal_is_a_hard_audited_error_naming_the_path(self):
        message = refuse_secret_read("some/dir/.env", reader="unit-test-reader")
        self.assertTrue(message.startswith("Error: refusing to read"))
        self.assertIn("some/dir/.env", message)
        entries = _refusals_by("unit-test-reader")
        self.assertEqual(len(entries), 1)
        self.assertIn("refused", entries[0]["result"])

    def test_the_refusal_fires_before_any_existence_check_so_it_cannot_probe_for_secrets(self):
        # A nonexistent .env and a real one must be indistinguishable.
        missing = refuse_secret_read("/definitely/not/here/.env", reader="unit-test-reader")
        self.assertTrue(missing.startswith("Error: refusing to read"))
        self.assertNotIn("Could not find", missing)


class TestReadDocumentIsTheSharedChokepointForTheToolAndResearchAgent(unittest.TestCase):
    """`read_document` (documents/reader.py) backs BOTH the registered tool
    and ResearchAgent's own tool loop. Until plan-b8 the only thing keeping
    `.env` out was its extension whitelist, by accident -- so the cases
    below deliberately include names that ARE supported extensions
    (`.txt`/`.md`) and were readable before the guard."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="secret-read-doc-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for name in (".env", ".env.txt", "credentials.txt", "github_token.md"):
            with open(os.path.join(self.dir, name), "w") as file:
                file.write(f"API_KEY={FAKE_SECRET}\n")
        with open(os.path.join(self.dir, "notes.md"), "w") as file:
            file.write("Ordinary meeting notes.\n")
        with open(os.path.join(self.dir, ".env.example"), "w") as file:
            file.write("API_KEY=changeme\n")

    def _assert_refused_and_secret_not_returned(self, result):
        self.assertIn("Error: refusing to read", result)
        self.assertNotIn(FAKE_SECRET, result)

    def test_registered_tool_refuses_every_secret_file_including_ones_with_supported_extensions(self):
        for name in (".env", ".env.txt", "credentials.txt", "github_token.md"):
            with self.subTest(name=name):
                result = registry.dispatch("read_document", {"file_path": os.path.join(self.dir, name)})
                self._assert_refused_and_secret_not_returned(result)

    def test_researchagent_refuses_the_same_reads_through_its_own_tool_loop(self):
        # The test that proves this is a chokepoint, not a single-site patch.
        for name in (".env", ".env.txt", "credentials.txt", "github_token.md"):
            with self.subTest(name=name):
                result = research_agent._run_tool("read_document", {"file_path": os.path.join(self.dir, name)})
                self._assert_refused_and_secret_not_returned(result)

    def test_researchagent_refusal_is_audited_by_the_chokepoint_itself(self):
        research_agent._run_tool("read_document", {"file_path": os.path.join(self.dir, ".env.txt")})
        entries = _refusals_by("read_document")
        self.assertTrue(any(".env.txt" in str(entry["input"]) for entry in entries), entries)

    def test_ordinary_documents_are_still_readable_on_both_paths(self):
        path = os.path.join(self.dir, "notes.md")
        self.assertIn("Ordinary meeting notes.", registry.dispatch("read_document", {"file_path": path}))
        self.assertIn("Ordinary meeting notes.", research_agent._run_tool("read_document", {"file_path": path}))
        self.assertIn("Ordinary meeting notes.", read_document(path))

    def test_the_example_file_passes_the_guard_and_is_only_stopped_by_the_readers_own_extension_rule(self):
        result = read_document(os.path.join(self.dir, ".env.example"))
        self.assertNotIn("Error: refusing to read", result)
        self.assertIn("Unsupported file type", result)  # read_document supports only pdf/txt/md; unrelated to secrets

    def test_a_symlink_to_a_secret_cannot_be_read_through_read_document(self):
        os.symlink(os.path.join(self.dir, ".env"), os.path.join(self.dir, "readme.txt"))
        result = read_document(os.path.join(self.dir, "readme.txt"))
        self._assert_refused_and_secret_not_returned(result)


class TestCodingAgentReadFile(unittest.TestCase):

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="secret-read-coding-test-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        for directory in ("src", "logs", "tests", "config/deep"):
            os.makedirs(os.path.join(self.repo, directory))
        files = {
            ".env": f"API_KEY={FAKE_SECRET}\n",
            ".env.local": f"API_KEY={FAKE_SECRET}\n",
            "config/deep/.env": f"API_KEY={FAKE_SECRET}\n",
            "server.pem": f"-----BEGIN FAKE-----{FAKE_SECRET}\n",
            "credentials.json": f'{{"key": "{FAKE_SECRET}"}}\n',
            ".env.example": "API_KEY=changeme\n",
            ".env.sample": "API_KEY=changeme\n",
            ".env.template": "API_KEY=changeme\n",
            "src/app.py": "def main():\n    return 42\n",
            "tests/test_app.py": "def test():\n    pass\n",
            "README.md": "# A project\n",
            "logs/debug.log": "ERROR something broke at 12:00\n",
        }
        for rel, content in files.items():
            with open(os.path.join(self.repo, rel), "w") as file:
                file.write(content)

    def test_secret_files_are_refused_and_the_content_is_never_returned(self):
        for rel in (".env", ".env.local", "config/deep/.env", "server.pem", "credentials.json"):
            with self.subTest(rel=rel):
                result = coding._read_file(self.repo, rel)
                self.assertIn("Error: refusing to read", result)
                self.assertNotIn(FAKE_SECRET, result)

    def test_the_refusal_is_audited(self):
        coding._read_file(self.repo, ".env")
        self.assertTrue(_refusals_by("coding_agent_read_file"))

    def test_a_symlink_to_a_secret_is_refused(self):
        os.symlink(".env", os.path.join(self.repo, "innocent.txt"))
        result = coding._read_file(self.repo, "innocent.txt")
        self.assertIn("Error: refusing to read", result)
        self.assertNotIn(FAKE_SECRET, result)

    def test_tracked_example_files_are_readable(self):
        for rel in (".env.example", ".env.sample", ".env.template"):
            with self.subTest(rel=rel):
                self.assertEqual(coding._read_file(self.repo, rel), "API_KEY=changeme\n")

    def test_ordinary_source_test_and_doc_files_are_still_readable(self):
        self.assertIn("return 42", coding._read_file(self.repo, "src/app.py"))
        self.assertIn("def test", coding._read_file(self.repo, "tests/test_app.py"))
        self.assertIn("# A project", coding._read_file(self.repo, "README.md"))

    def test_gitignored_logs_are_still_readable_the_write_rule_does_not_transfer(self):
        with open(os.path.join(self.repo, ".gitignore"), "w") as file:
            file.write("logs/\n")
        self.assertIn("ERROR something broke", coding._read_file(self.repo, "logs/debug.log"))

    def test_a_missing_ordinary_file_still_says_so(self):
        self.assertIn("does not exist", coding._read_file(self.repo, "nope.py"))


class TestSecretNeverReachesTheModelEndToEnd(CodingAgentEnabledTestCase):

    @patch("agent.agents.coding.anthropic_client")
    def test_a_model_asking_to_read_env_gets_a_refusal_and_the_secret_is_absent_from_every_payload(self, mock_client):
        with open(os.path.join(self.repo, ".gitignore"), "a") as file:
            file.write(".env\n")
        _git(self.repo, "commit", "-qam", "ignore .env")
        with open(os.path.join(self.repo, ".env"), "w") as file:
            file.write(f"ANTHROPIC_API_KEY={FAKE_SECRET}\n")

        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("read_file", {"path": ".env"}, "t1")], "tool_use"),
            _response([_text_block("I could not read it.")], "end_turn"),
        ]
        self.agent.execute("show me the api key", self.context)

        self.assertEqual(mock_client.messages.create.call_count, 2)
        second_call = mock_client.messages.create.call_args_list[1]
        payload = str(second_call.kwargs["messages"]) + str(second_call.kwargs.get("system", ""))
        self.assertIn("Error: refusing to read", payload)
        for call in mock_client.messages.create.call_args_list:
            self.assertNotIn(FAKE_SECRET, str(call.kwargs), "the secret reached the model provider's payload")

    @patch("agent.agents.coding.anthropic_client")
    def test_an_ordinary_read_still_reaches_the_model(self, mock_client):
        mock_client.messages.create.side_effect = [
            _response([_tool_use_block("read_file", {"path": "tests/test_dummy.py"}, "t1")], "tool_use"),
            _response([_text_block("Read it.")], "end_turn"),
        ]
        self.agent.execute("read the test", self.context)
        payload = str(mock_client.messages.create.call_args_list[1].kwargs["messages"])
        self.assertIn("class TestDummy", payload)


class TestObsidianReads(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="secret-obsidian-test-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.vault = os.path.join(self.temp, "Vault")
        os.makedirs(self.vault)
        with open(os.path.join(self.vault, "credentials.md"), "w") as file:
            file.write(f"password: {FAKE_SECRET} project apollo launch\n")
        with open(os.path.join(self.vault, "apollo.md"), "w") as file:
            file.write("project apollo launch checklist\n")
        real_default = obsidian_vault._DEFAULT_VAULT_DIR
        obsidian_vault._DEFAULT_VAULT_DIR = os.path.join(self.temp, "NoDefault")
        self.addCleanup(setattr, obsidian_vault, "_DEFAULT_VAULT_DIR", real_default)
        settings_patch = patch("agent.obsidian_vault.settings")
        settings_patch.start().obsidian_vault_path = self.vault
        self.addCleanup(settings_patch.stop)

    def test_a_secret_named_note_cannot_be_read(self):
        content, error = obsidian_vault.read_note("credentials.md")
        self.assertIsNone(content)
        self.assertIn("Error: refusing to read", error)
        self.assertNotIn(FAKE_SECRET, error)

    def test_search_skips_secret_named_notes_and_still_finds_ordinary_ones(self):
        results, error = obsidian_vault.search_notes("project apollo launch")
        self.assertIsNone(error)
        paths = [result.path for result in results]
        self.assertEqual(paths, ["apollo.md"])
        self.assertNotIn(FAKE_SECRET, str([(r.path, r.snippet) for r in results]))

    def test_an_ordinary_note_is_still_readable(self):
        content, error = obsidian_vault.read_note("apollo.md")
        self.assertIsNone(error)
        self.assertIn("launch checklist", content)


if __name__ == "__main__":
    unittest.main()
