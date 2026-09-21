"""plan-b9 item 2 -- the child process `run_python` starts must not inherit
the parent's environment, and must not re-read `.env` from disk.

Found by plan-b8/plan-b9: the sandboxed child inherited the whole environment
(ANTHROPIC_API_KEY, OPENAI_API_KEY, and whatever the host process carried),
and `print(os.environ)` returned it straight to the model. Scrubbing the env
is only half a fix: any child that imports `config.settings` would call
`load_dotenv()` and put the keys straight back from disk -- so the child is
started with JARVIS_NO_DOTENV=1 and every `load_dotenv()` call goes through
`config.dotenv_loader`. (The sandbox's read-deny on `.env` is the second,
independent layer -- tests/test_run_python_sandbox.py.)

Nothing here reads a real `.env` or prints a real value: secrets are FAKE
variables injected into the test process, and the "does not re-acquire from
.env" check is done hermetically by putting a FAKE `dotenv` module on the
child's path, so the real library -- and therefore the real `.env` -- is
never involved.

Run with: python -m unittest tests.test_run_python_environment -v
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import config.dotenv_loader as dotenv_loader
from tools import sandbox_python
from tools.sandbox_python import _child_environment

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECRET_LOOKING = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|API|CREDENTIAL", re.IGNORECASE)

FAKE_PARENT = {
    "ANTHROPIC_API_KEY": "sk-FAKE-anthropic-0000",
    "OPENAI_API_KEY": "sk-FAKE-openai-0000",
    "CLAUDE_CODE_MESSAGING_TOKEN": "FAKE-token-0000",
    "GITHUB_PASSWORD": "fake-password",
    "SOME_CREDENTIAL": "fake-credential",
    "PYTHONPATH": "/somewhere/else",
    "RANDOM_APP_SETTING": "x",
    "PATH": "/usr/bin:/bin",
    "HOME": "/Users/fake",
    "TMPDIR": "/tmp/fake/",
    "USER": "fake",
    "SHELL": "/bin/zsh",
    "VIRTUAL_ENV": "/fake/venv",
}


class TestChildEnvironmentBuilder(unittest.TestCase):

    def test_only_the_allowlist_survives(self):
        child = _child_environment(dict(FAKE_PARENT))
        self.assertEqual(
            set(child), {"PATH", "HOME", "TMPDIR", "USER", "SHELL", "VIRTUAL_ENV", "JARVIS_NO_DOTENV"},
        )
        self.assertEqual(child["PATH"], "/usr/bin:/bin")
        self.assertEqual(child["HOME"], "/Users/fake")

    def test_no_secret_looking_name_ever_reaches_the_child(self):
        for name in _child_environment(dict(FAKE_PARENT)):
            with self.subTest(name=name):
                self.assertIsNone(_SECRET_LOOKING.search(name), name)

    def test_the_child_is_told_not_to_reload_dotenv(self):
        self.assertEqual(_child_environment({})["JARVIS_NO_DOTENV"], "1")

    def test_locale_variables_are_copied_only_if_present_and_never_invented(self):
        self.assertNotIn("LANG", _child_environment({"PATH": "/bin"}))
        self.assertFalse([n for n in _child_environment({"PATH": "/bin"}) if n.startswith("LC_")])
        child = _child_environment({"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "LC_CTYPE": "UTF-8", "PATH": "/bin"})
        self.assertEqual(child["LANG"], "en_US.UTF-8")
        self.assertEqual(child["LC_ALL"], "en_US.UTF-8")
        self.assertEqual(child["LC_CTYPE"], "UTF-8")

    def test_a_missing_allowlisted_variable_is_left_out_not_invented(self):
        child = _child_environment({"PATH": "/bin"})
        self.assertNotIn("HOME", child)
        self.assertNotIn("VIRTUAL_ENV", child)

    def test_it_reads_the_live_environment_when_given_no_argument(self):
        with patch.dict(os.environ, {"LC_ALL": "C", "PROBE_FAKE_API_KEY": "fake"}):
            child = _child_environment()
        self.assertEqual(child["LC_ALL"], "C")
        self.assertNotIn("PROBE_FAKE_API_KEY", child)

    def test_the_input_mapping_is_not_mutated(self):
        parent = dict(FAKE_PARENT)
        _child_environment(parent)
        self.assertEqual(parent, FAKE_PARENT)


class TestRealRunPythonChildCannotSeeSecrets(unittest.TestCase):
    """Real `run_python` (sandbox-exec, harness-redirected sandbox dir)."""

    def _child_env_names(self, extra=None):
        # Names only -- a value is never printed.
        with patch.dict(os.environ, extra or {}):
            output = sandbox_python.run_python("import os; print(sorted(os.environ))")
        body = output.split("\n", 1)[1]
        return ast.literal_eval(body.strip()), output  # a list literal of str, printed by the child

    def test_fake_secrets_in_the_parent_are_invisible_to_the_child(self):
        fake = {
            "PROBE_FAKE_API_KEY": "FAKE-VALUE-SHOULD-NEVER-APPEAR",
            "PROBE_FAKE_TOKEN": "FAKE-VALUE-SHOULD-NEVER-APPEAR",
            "PROBE_FAKE_PASSWORD": "FAKE-VALUE-SHOULD-NEVER-APPEAR",
            "PROBE_FAKE_SECRET": "FAKE-VALUE-SHOULD-NEVER-APPEAR",
        }
        names, output = self._child_env_names(fake)
        for name in fake:
            self.assertNotIn(name, names)
        self.assertNotIn("FAKE-VALUE-SHOULD-NEVER-APPEAR", output)

    def test_the_child_environment_contains_no_secret_looking_name_whatever_the_parent_has(self):
        # Whatever this test process happens to carry (real keys loaded from a
        # real .env, a host token, ...), NO secret-looking name may reach the
        # child. Names only: values are never read or printed by this test.
        names, _ = self._child_env_names({"PROBE_FAKE_API_KEY": "fake", "PROBE_FAKE_TOKEN": "fake"})
        offenders = [name for name in names if _SECRET_LOOKING.search(name)]
        self.assertEqual(offenders, [], f"secret-looking names visible to the child: {offenders}")

    def test_the_child_sees_exactly_the_allowlist_plus_the_dotenv_flag(self):
        names, _ = self._child_env_names()
        # __CF_USER_TEXT_ENCODING is NOT from _child_environment (the builder's
        # unit test above proves that): macOS CoreFoundation injects it into
        # every process at start-up, and it carries only an encoding hint.
        allowed = {"PATH", "HOME", "TMPDIR", "USER", "SHELL", "VIRTUAL_ENV", "JARVIS_NO_DOTENV", "LANG",
                   "__CF_USER_TEXT_ENCODING"}
        self.assertLessEqual({n for n in names if not n.startswith("LC_")}, allowed, names)
        self.assertIn("JARVIS_NO_DOTENV", names)

    def test_the_child_really_is_started_with_the_dotenv_flag_set(self):
        output = sandbox_python.run_python("import os; print(os.environ.get('JARVIS_NO_DOTENV'))")
        self.assertTrue(output.strip().endswith("1"), output)


class TestDotenvLoaderHonorsTheFlag(unittest.TestCase):

    def test_skips_loading_when_the_flag_is_set(self):
        with patch.dict(os.environ, {"JARVIS_NO_DOTENV": "1"}), patch("config.dotenv_loader.load_dotenv") as load:
            self.assertFalse(dotenv_loader.load_project_dotenv())
            load.assert_not_called()

    def test_loads_normally_when_the_flag_is_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_NO_DOTENV"}
        with patch.dict(os.environ, env, clear=True), patch("config.dotenv_loader.load_dotenv") as load:
            self.assertTrue(dotenv_loader.load_project_dotenv())
            load.assert_called_once()

    def test_no_module_calls_load_dotenv_directly_so_no_site_can_forget_the_flag(self):
        offenders = []
        for directory, subdirs, files in os.walk(_PROJECT_ROOT):
            subdirs[:] = [d for d in subdirs if d not in {"tests", ".venv", "venv", "build", "dist", ".git", ".relay",
                                                          "JarvisVault", "graphify-out", "node_modules", "__pycache__",
                                                          "CampusPilotAgent.app"} and not d.startswith(".")]
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                rel = os.path.relpath(path, _PROJECT_ROOT).replace(os.sep, "/")
                if rel == "config/dotenv_loader.py":
                    continue
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                        if name == "load_dotenv":
                            offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], f"call config.dotenv_loader.load_project_dotenv instead: {offenders}")

    def test_importing_config_settings_in_a_flagged_child_does_not_call_load_dotenv(self):
        # Hermetic: a FAKE `dotenv` module that explodes if load_dotenv() is
        # ever called replaces the real library, so the real .env is never
        # involved. With the flag set the import must succeed; without it (the
        # control) it must hit the fake -- proving the fake is really in play
        # and the flag is what makes the difference.
        with tempfile.TemporaryDirectory() as fake_root:
            os.makedirs(os.path.join(fake_root, "dotenv"))
            with open(os.path.join(fake_root, "dotenv", "__init__.py"), "w") as handle:
                handle.write("def load_dotenv(*args, **kwargs):\n    raise SystemExit('LOAD_DOTENV_WAS_CALLED')\n")

            def run(flag):
                env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PYTHONPATH": fake_root}
                if flag:
                    env["JARVIS_NO_DOTENV"] = "1"
                return subprocess.run(
                    [sys.executable, "-c", "import config.settings"], cwd=_PROJECT_ROOT, env=env,
                    capture_output=True, text=True, timeout=60,
                )

            flagged = run(True)
            self.assertEqual(flagged.returncode, 0, flagged.stderr[-300:])
            self.assertNotIn("LOAD_DOTENV_WAS_CALLED", flagged.stderr)

            control = run(False)
            self.assertNotEqual(control.returncode, 0)
            self.assertIn("LOAD_DOTENV_WAS_CALLED", control.stderr)


class TestLegitimateRunPythonUseIsUnaffected(unittest.TestCase):

    def test_ordinary_computation_and_stdlib_imports_still_work(self):
        result = sandbox_python.run_python(
            "import json, math, datetime, re, statistics, itertools, collections, os\n"
            "print(math.sqrt(144), json.dumps({'a': [1, 2]}), statistics.mean([1, 2, 3]))\n"
        )
        self.assertIn("12.0", result)
        self.assertIn('{"a": [1, 2]}', result)
        self.assertIn("succeeded", result)

    def test_unicode_output_works_with_no_locale_variables_set(self):
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "LANG" and not k.startswith("LC_")}, clear=True):
            result = sandbox_python.run_python("print('héllo ✓')")
        self.assertIn("héllo ✓", result)

    def test_writing_and_reading_inside_the_sandbox_dir_still_works(self):
        result = sandbox_python.run_python(
            "open('scratch.txt', 'w').write('kept'); print(open('scratch.txt').read())"
        )
        self.assertIn("kept", result)

    def test_the_essentials_are_still_present_in_the_child(self):
        result = sandbox_python.run_python("import os; print('HOME' in os.environ, 'PATH' in os.environ)")
        self.assertIn("True True", result)

    def test_a_subprocess_started_by_the_code_can_still_find_ordinary_programs_on_path(self):
        result = sandbox_python.run_python(
            "import subprocess; print(subprocess.run(['echo', 'hello-from-path'], capture_output=True, text=True).stdout.strip())"
        )
        self.assertIn("hello-from-path", result)


class TestTheCoworkerSpawnIsUntouched(unittest.TestCase):
    """The coworker worker legitimately needs the API keys (ResearchAgent calls
    the model); item 2 scrubs run_python's child ONLY."""

    def test_manager_py_does_not_use_the_run_python_environment_builder(self):
        with open(os.path.join(_PROJECT_ROOT, "agent", "agents", "manager.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("_child_environment", source)
        self.assertNotIn("JARVIS_NO_DOTENV", source)
        self.assertNotIn("sandbox_python", source)


if __name__ == "__main__":
    unittest.main()
