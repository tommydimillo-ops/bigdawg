"""plan-b9 item 3 -- run_python's Seatbelt profile.

Before this, the profile was `(allow default)` plus network/write denies, and
its own comment said "File reads are NOT restricted": sandboxed code could
open('.env') and print it, and the output went straight back to the model.
The profile now also denies (a) reads of secret files, generated from the SAME
pattern list the file-reader chokepoint uses, (b) exec of the binaries that
reach outside the sandbox, and (c) the LaunchServices Mach service.

(c) is here because it was MEASURED, not assumed: PyObjC is installed in this
venv, and with only the exec-denies sandboxed code could still reach
LaunchServices two ways and send an AppleEvent to another process -- i.e. write
a .command file and open it, outside the sandbox, with network.

Everything uses FAKE files in temp directories. Exec probes use only harmless
targets, so if a deny ever regressed the worst that would run is a hidden
`open -g -j -a Finder`, `security list-keychains`, `osascript -e 'return 1'` or
`launchctl version`. NO test here calls an open-a-path API or launches anything.

A separate one-off run, recorded in the plan-b9 report, imported all 281
top-level venv and stdlib modules under the old and new profiles: 0 regressions.

Run with: python -m unittest tests.test_run_python_sandbox -v
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

import agent.secret_paths as secret_paths
from agent.secret_paths import EXAMPLE_BASENAMES, seatbelt_read_rules
from tools import sandbox_python

_REGEX_LITERAL = re.compile(r'#"([^"]*)"')


def _regexes_in(block):
    return _REGEX_LITERAL.findall(block)


def _deny_and_allow_regexes(rules):
    deny_block, allow_block = rules.split("(allow file-read*")
    return _regexes_in(deny_block), _regexes_in(allow_block.split("(deny file-read*")[0])


def _probe(code):
    """Runs code in the real sandbox and returns (exited_abnormally, body)."""
    output = sandbox_python.run_python(code)
    head, _, body = output.partition("\n")
    return "exited with code" in head, body.strip()


class TestRuleGeneration(unittest.TestCase):

    def test_every_secret_pattern_has_a_deny_rule_so_the_two_lists_cannot_drift(self):
        deny, _ = _deny_and_allow_regexes(seatbelt_read_rules(home="/Users/someone"))
        self.assertEqual(len(deny), len(secret_paths._SECRET_BASENAME_PATTERNS))

    def test_every_tracked_example_name_is_carved_back_out(self):
        _, allow = _deny_and_allow_regexes(seatbelt_read_rules(home="/Users/someone"))
        for name in EXAMPLE_BASENAMES:
            with self.subTest(name=name):
                self.assertTrue(any(re.search(rule, "/proj/" + name) for rule in allow), name)

    def test_generated_deny_regexes_match_what_the_python_side_chokepoint_refuses(self):
        deny, allow = _deny_and_allow_regexes(seatbelt_read_rules(home="/Users/someone"))
        compiled_deny = [re.compile(rule) for rule in deny]
        compiled_allow = [re.compile(rule) for rule in allow]

        def sandbox_denies(path):
            denied = any(rule.search(path) for rule in compiled_deny)
            return denied and not any(rule.search(path) for rule in compiled_allow)

        for path in ("/p/.env", "/p/.ENV", "/p/.Env.Local", "/p/.env.production", "/p/a/server.pem", "/p/tls.KEY",
                     "/p/id_rsa", "/p/ID_ED25519", "/p/x.p12", "/p/login.keychain-db", "/p/credentials",
                     "/p/credentials.json", "/p/github_token.txt", "/p/a.secret", "/p/.netrc", "/p/.npmrc",
                     "/p/token.json", "/p/.env.py"):
            with self.subTest(denied=path):
                self.assertTrue(sandbox_denies(path), path)
                self.assertIsNotNone(secret_paths.secret_path_reason(path), path)
        for path in ("/p/.env.example", "/p/.env.sample", "/p/.env.template", "/p/notes.txt", "/p/.environment",
                     "/p/myenv", "/p/README.md", "/p/agent/executor.py", "/p/keyboard.py", "/p/token_notes.md"):
            with self.subTest(allowed=path):
                self.assertFalse(sandbox_denies(path), path)

    def test_the_new_netrc_npmrc_and_token_json_names_are_refused_by_the_python_chokepoint_too(self):
        for path in (".netrc", ".npmrc", "token.json", "sub/dir/.npmrc"):
            with self.subTest(path=path):
                self.assertIsNotNone(secret_paths.secret_path_reason(path))

    def test_keychain_directories_are_denied_for_the_given_home_and_the_system_one(self):
        rules = seatbelt_read_rules(home="/Users/someone")
        self.assertIn('(subpath "/Users/someone/Library/Keychains")', rules)
        self.assertIn('(subpath "/Library/Keychains")', rules)

    def test_the_keychain_home_defaults_to_the_real_home_read_at_call_time(self):
        with patch.dict(os.environ, {"HOME": "/Users/probe-home"}):
            self.assertIn("Library/Keychains", seatbelt_read_rules())

    def test_the_library_tree_carve_out_covers_only_the_two_loose_patterns_and_only_library_trees(self):
        _, allow = _deny_and_allow_regexes(seatbelt_read_rules(home="/Users/someone"))
        compiled = [re.compile(rule) for rule in allow]

        def carved_out(path):
            return any(rule.search(path) for rule in compiled)

        # inside a library tree: the loose patterns are allowed (this is what keeps `import anthropic`,
        # `import packaging.markers` and `import keyring` working)
        for path in ("/v/.venv/lib/python3.14/site-packages/anthropic/lib/credentials",
                     "/v/.venv/lib/python3.14/site-packages/packaging/_tokenizer.py",
                     "/x/dist-packages/keyring/credentials.py",
                     "/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/test/test_tokenize.py"):
            with self.subTest(allowed=path):
                self.assertTrue(carved_out(path), path)
        # outside a library tree, or any other pattern: NOT carved out
        for path in ("/Users/x/proj/credentials_backup.py", "/Users/x/proj/my_token.py", "/Users/x/proj/credentials",
                     "/v/.venv/lib/python3.14/site-packages/pkg/cacert.pem", "/v/lib/python3.14/site-packages/.env",
                     "/v/lib/python3.14/site-packages/pkg/id_rsa"):
            with self.subTest(not_carved_out=path):
                self.assertFalse(carved_out(path), path)

    def test_the_profile_has_the_exec_denies_the_sandbox_dir_exec_deny_and_the_launchservices_deny(self):
        profile = sandbox_python._sandbox_profile(home="/Users/someone")
        for binary in ("/usr/bin/open", "/usr/bin/security", "/usr/bin/osascript", "/bin/launchctl"):
            self.assertIn(f'(literal "{binary}")', profile)
        exec_block = profile.split("(deny process-exec")[1].split("(deny mach-lookup")[0]
        self.assertIn(f'(subpath "{os.path.realpath(sandbox_python.SANDBOX_DIR)}")', exec_block)
        self.assertIn("com.apple.coreservices.launchservicesd", profile)
        self.assertIn("com.apple.lsd.", profile)

    def test_the_original_guarantees_are_still_in_the_profile(self):
        profile = sandbox_python._sandbox_profile(home="/Users/someone")
        self.assertIn("(deny network*)", profile)
        self.assertIn("(deny file-write*)", profile)
        self.assertIn(f'(subpath "{sandbox_python.SANDBOX_DIR}")', profile)


class TestSandboxDeniesSecretReads(unittest.TestCase):
    """Real sandbox-exec against fake files."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="sandbox-read-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _make(self, *names):
        for name in names:
            path = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("FAKE-CONTENT-NOT-A-SECRET\n")

    def _readable(self, *names):
        code = (
            "import os\nd = %r\nfor n in %r:\n    try:\n        open(os.path.join(d, n)).read(); r = 'READABLE'\n"
            "    except PermissionError:\n        r = 'denied'\n    except Exception as e:\n        r = type(e).__name__\n"
            "    print(n, r)\n" % (self.dir, list(names))
        )
        _, body = _probe(code)
        return dict(line.rsplit(" ", 1) for line in body.splitlines())

    def test_secret_named_files_are_denied_including_case_variants(self):
        names = [".env", ".ENV", ".Env.Local", ".env.production", "server.pem", "tls.key", "id_rsa", "id_ed25519",
                 "cert.p12", "login.keychain-db", "credentials", "credentials.json", "credentials.py",
                 "github_token.txt", "my_token.py", "a.secret", ".netrc", ".npmrc", "token.json"]
        self._make(*names)
        result = self._readable(*names)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(result[name], "denied", f"{name}: {result[name]}")

    def test_tracked_example_files_and_ordinary_files_stay_readable(self):
        names = [".env.example", ".env.sample", ".env.template", "notes.txt", "harmless.py", "README.md", "data.csv"]
        self._make(*names)
        result = self._readable(*names)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(result[name], "READABLE", f"{name}: {result[name]}")

    def test_a_symlink_to_a_secret_is_denied_and_a_symlink_named_like_an_example_cannot_launder_it(self):
        self._make(".env")
        os.symlink(os.path.join(self.dir, ".env"), os.path.join(self.dir, "innocent.txt"))
        os.symlink(os.path.join(self.dir, ".env"), os.path.join(self.dir, ".env.example.link"))
        result = self._readable("innocent.txt", ".env.example.link")
        self.assertEqual(result["innocent.txt"], "denied")
        self.assertEqual(result[".env.example.link"], "denied")

    def test_a_subprocess_the_code_spawns_cannot_cat_the_file_either(self):
        # The profile is inherited by children, so `cat` is no way around it.
        self._make(".env")
        _, body = _probe(
            "import subprocess\nr = subprocess.run(['cat', %r], capture_output=True, text=True)\n"
            "print('rc', r.returncode, '| stdout-empty', r.stdout == '')" % os.path.join(self.dir, ".env")
        )
        self.assertIn("stdout-empty True", body)
        self.assertNotIn("rc 0", body)

    def test_the_contents_of_a_denied_file_never_appear_in_what_the_model_would_receive(self):
        self._make(".env")
        output = sandbox_python.run_python("print(open(%r).read())" % os.path.join(self.dir, ".env"))
        self.assertNotIn("FAKE-CONTENT-NOT-A-SECRET", output)
        self.assertIn("PermissionError", output)

    def test_the_keychain_directory_is_denied_for_the_home_the_profile_was_built_with(self):
        fake_home = os.path.join(self.dir, "fakehome")
        self._make("fakehome/Library/Keychains/login.keychain-db", "fakehome/Library/Keychains/anything.txt",
                   "fakehome/Library/Other/fine.txt")
        real = secret_paths.seatbelt_read_rules
        with patch.object(sandbox_python, "seatbelt_read_rules", lambda home=None: real(home=fake_home)):
            _, body = _probe(
                "import os\nfor p in %r:\n    try:\n        open(p).read(); print(os.path.basename(p), 'READABLE')\n"
                "    except PermissionError:\n        print(os.path.basename(p), 'denied')\n" % [
                    os.path.join(fake_home, "Library", "Keychains", "login.keychain-db"),
                    os.path.join(fake_home, "Library", "Keychains", "anything.txt"),
                    os.path.join(fake_home, "Library", "Other", "fine.txt"),
                ]
            )
        result = dict(line.rsplit(" ", 1) for line in body.splitlines())
        self.assertEqual(result["login.keychain-db"], "denied")
        self.assertEqual(result["anything.txt"], "denied")
        self.assertEqual(result["fine.txt"], "READABLE")

    def test_a_loose_pattern_directory_or_file_inside_a_library_tree_is_readable_but_other_patterns_still_are_not(self):
        # What keeps `import anthropic` (its package DIRECTORY is named `credentials`) working, without weakening
        # anything else: a hardcoded-credentials file outside a library tree stays denied, and so does a .pem
        # or .env inside one.
        self._make("lib/python3.14/site-packages/pkg/credentials/__init__.py",
                   "lib/python3.14/site-packages/pkg/_tokenizer.py",
                   "lib/python3.14/site-packages/pkg/cacert.pem",
                   "lib/python3.14/site-packages/pkg/.env",
                   "project/credentials_backup.py")
        result = self._readable("lib/python3.14/site-packages/pkg/credentials/__init__.py",
                                "lib/python3.14/site-packages/pkg/_tokenizer.py",
                                "lib/python3.14/site-packages/pkg/cacert.pem",
                                "lib/python3.14/site-packages/pkg/.env",
                                "project/credentials_backup.py")
        self.assertEqual(result["lib/python3.14/site-packages/pkg/credentials/__init__.py"], "READABLE")
        self.assertEqual(result["lib/python3.14/site-packages/pkg/_tokenizer.py"], "READABLE")
        self.assertEqual(result["lib/python3.14/site-packages/pkg/cacert.pem"], "denied")
        self.assertEqual(result["lib/python3.14/site-packages/pkg/.env"], "denied")
        self.assertEqual(result["project/credentials_backup.py"], "denied")


class TestSandboxDeniesTheEscapeBinaries(unittest.TestCase):

    ATTEMPT = (
        "import subprocess\n"
        "def attempt(argv):\n"
        "    try:\n"
        "        subprocess.run(argv, capture_output=True, text=True, timeout=8)\n"
        "        return 'RAN'\n"
        "    except PermissionError:\n"
        "        return 'BLOCKED'\n"
        "    except Exception as e:\n"
        "        return type(e).__name__\n"
    )

    def _blocked(self, argv):
        _, body = _probe(self.ATTEMPT + "print(attempt(%r))" % (argv,))
        return body

    def test_the_named_binaries_cannot_be_executed_harmless_arguments_only(self):
        for argv in (
            ["/usr/bin/open", "-g", "-j", "-a", "Finder"],
            ["/usr/bin/security", "list-keychains"],
            ["/usr/bin/osascript", "-e", "return 1"],
            ["/bin/launchctl", "version"],
        ):
            with self.subTest(argv=argv[0]):
                self.assertEqual(self._blocked(argv), "BLOCKED")

    def test_open_is_blocked_by_name_through_path_lookup_too(self):
        self.assertEqual(self._blocked(["open", "-g", "-j", "-a", "Finder"]), "BLOCKED")

    def test_a_copy_of_a_denied_binary_placed_in_the_sandbox_dir_cannot_be_run(self):
        # The only place this code can write is the sandbox dir, so without the
        # exec-deny on that dir it could simply copy `open` there and run it.
        _, body = _probe(
            self.ATTEMPT
            + "import shutil, os\n"
            "copy = os.path.join(os.getcwd(), 'open_copy')\n"
            "shutil.copy('/usr/bin/open', copy); os.chmod(copy, 0o755)\n"
            "print(attempt([copy, '-g', '-j', '-a', 'Finder']))\n"
        )
        self.assertEqual(body, "BLOCKED")

    def test_any_executable_the_code_wrote_itself_cannot_run(self):
        _, body = _probe(
            self.ATTEMPT
            + "import os\n"
            "script = os.path.join(os.getcwd(), 'mine.sh')\n"
            "open(script, 'w').write('#!/bin/sh\\necho hi\\n'); os.chmod(script, 0o755)\n"
            "print(attempt([script]))\n"
        )
        self.assertEqual(body, "BLOCKED")

    def test_ordinary_programs_still_run(self):
        _, body = _probe(
            self.ATTEMPT + "import sys\n"
            "print(attempt(['/bin/echo', 'hi']), attempt(['/bin/ls', '/']), attempt([sys.executable, '-c', 'print(1)']))"
        )
        self.assertEqual(body, "RAN RAN RAN")


class TestSandboxCannotReachLaunchServicesDirectly(unittest.TestCase):
    """The route the exec-denies alone do NOT close. These only LOOK UP where
    Finder lives -- nothing is opened or launched.

    Each test first runs the same probe under a profile WITHOUT the deny (the
    control) and skips if LaunchServices is not reachable there at all -- e.g. a
    headless CI runner -- because "not reachable" would then prove nothing.

    Deliberately NOT tested here: sending an AppleEvent (NSAppleScript). The
    plan-b9 probes measured that it is also blocked, but the blocked process does
    not fail cleanly -- it aborts, writes a crash report, and can wedge in the
    kernel (state UE) where it cannot be killed. A test that does that on every
    run would litter crash reports and could hang CI, and the two clean lookups
    below prove the same Mach deny."""

    CTYPES = (
        "import ctypes\n"
        "cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')\n"
        "cs = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')\n"
        "cf.CFStringCreateWithCString.restype = ctypes.c_void_p\n"
        "cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]\n"
        "cs.LSCopyApplicationURLsForBundleIdentifier.restype = ctypes.c_void_p\n"
        "cs.LSCopyApplicationURLsForBundleIdentifier.argtypes = [ctypes.c_void_p, ctypes.c_void_p]\n"
        "r = cs.LSCopyApplicationURLsForBundleIdentifier("
        "cf.CFStringCreateWithCString(None, b'com.apple.finder', 0x08000100), None)\n"
        "print('REACHABLE' if r else 'blocked')\n"
    )
    PYOBJC = (
        "try:\n"
        "    from AppKit import NSWorkspace\n"
        "except ImportError:\n"
        "    print('NOIMPORT')\n"
        "else:\n"
        "    u = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_('com.apple.finder')\n"
        "    print('REACHABLE' if u else 'blocked')\n"
    )

    def _control_reachable(self, code):
        """Runs the probe under the profile with the LaunchServices deny stripped out."""
        original = sandbox_python._sandbox_profile

        def without_launchservices(home=None):
            return original(home).split("(deny mach-lookup")[0]

        with patch.object(sandbox_python, "_sandbox_profile", without_launchservices):
            _, body = _probe(code)
        return "REACHABLE" in body

    def _assert_blocked_by_the_deny(self, code):
        if not self._control_reachable(code):
            self.skipTest("LaunchServices is not reachable from the sandbox in this environment even without the deny "
                          "(headless runner, or PyObjC missing) -- the test would prove nothing here")
        _, body = _probe(code)
        self.assertNotIn("REACHABLE", body)

    def test_launchservices_lookup_via_ctypes_is_blocked(self):
        self._assert_blocked_by_the_deny(self.CTYPES)

    def test_launchservices_lookup_via_pyobjc_is_blocked(self):
        self._assert_blocked_by_the_deny(self.PYOBJC)


class TestATimedOutOrWedgedChildCanNeverHangTheAgent(unittest.TestCase):
    """plan-b9's own probes produced a child wedged in the kernel that SIGKILL
    could not reach; run_python must return regardless."""

    def test_a_child_that_outruns_the_timeout_is_killed_and_reported(self):
        with patch.object(sandbox_python, "TIMEOUT", 1):
            result = sandbox_python.run_python("import time; time.sleep(60)")
        self.assertIn("timed out after 1s", result)

    def test_a_child_that_cannot_be_killed_is_abandoned_not_waited_on_forever(self):
        from unittest.mock import MagicMock
        import subprocess

        fake = MagicMock()
        fake.pid = 2 ** 22 + 12345  # not a real process: killpg raises ProcessLookupError, which must be swallowed
        # communicate() times out on the first call (the real timeout) AND again after the kill (unkillable child).
        fake.communicate.side_effect = [
            subprocess.TimeoutExpired("sandbox-exec", 15),
            subprocess.TimeoutExpired("sandbox-exec", 3),
        ]
        with patch.object(sandbox_python.subprocess, "Popen", return_value=fake), \
                patch.object(sandbox_python, "_KILL_GRACE_SECONDS", 0):
            result = sandbox_python.run_python("print(1)")
        self.assertIn("timed out", result)
        self.assertEqual(fake.communicate.call_count, 2, "must not wait a third time on an unkillable child")
        fake.kill.assert_called_once()

    def test_the_child_is_started_in_its_own_process_group_so_the_whole_tree_can_be_killed(self):
        from unittest.mock import MagicMock

        fake = MagicMock()
        fake.communicate.return_value = ("ok", "")
        fake.returncode = 0
        with patch.object(sandbox_python.subprocess, "Popen", return_value=fake) as popen:
            sandbox_python.run_python("print(1)")
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)


class TestLegitimateRunPythonUseIsUnaffectedByTheProfile(unittest.TestCase):

    def test_imports_that_touch_loose_pattern_names_still_work(self):
        # packaging/_tokenizer.py, keyring/credentials.py and anthropic's
        # `credentials` PACKAGE DIRECTORY are exactly what a naive credentials*/
        # *_token* deny breaks -- an import-regression run found the last one.
        for module in ("packaging.markers", "packaging.requirements", "keyring", "certifi", "anthropic"):
            with self.subTest(module=module):
                abnormal, body = _probe(f"import {module}\nprint('imported')")
                self.assertFalse(abnormal, body)
                self.assertEqual(body, "imported")

    def test_ordinary_computation_and_file_io_in_the_sandbox_dir_still_work(self):
        _, body = _probe(
            "import json, math, statistics\nopen('scratch.txt', 'w').write('kept')\n"
            "print(math.sqrt(144), json.dumps({'a': 1}), statistics.mean([1, 2, 3]), open('scratch.txt').read())"
        )
        self.assertEqual(body, '12.0 {"a": 1} 2 kept')

    def test_network_and_writes_outside_the_sandbox_dir_are_still_denied(self):
        # The write target is the sandbox dir's PARENT: denied by the profile, and if that ever
        # regressed it would only land in the test harness's temp run root. (Not
        # tempfile.gettempdir(): it falls back to the cwd -- the sandbox dir, where writing is
        # allowed -- when TMPDIR is unwritable, which made an earlier draft of this test wrong.)
        _, body = _probe(
            "import socket, os\n"
            "try:\n    socket.create_connection(('127.0.0.1', 9), timeout=2); print('net RAN')\n"
            "except Exception as e:\n    print('net', type(e).__name__)\n"
            "try:\n    open(os.path.join(os.path.dirname(os.getcwd()), 'should_not_exist_probe.txt'), 'w'); print('write RAN')\n"
            "except Exception as e:\n    print('write', type(e).__name__)\n"
        )
        self.assertNotIn("RAN", body)


if __name__ == "__main__":
    unittest.main()
