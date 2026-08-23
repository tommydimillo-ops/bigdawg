"""Meta-tests for tests/_safety.py -- the test-safety bootstrap itself,
not any Jarvis feature. These prove the SAFETY PROPERTIES the Phase 9
reliability audit's implementation pass ("S1 -- Structurally Safe Test
Harness") set out to guarantee: every production persistent store is
redirected before any other test runs, the external-network firewall
blocks everything but loopback, the Keychain/Obsidian/skills/browser/
computer-control seams cannot leak into a canonical run, and the real
Seatbelt sandbox boundary still enforces its policy against the
redirected temp state.

These tests read `tests._safety`'s own bookkeeping (`run_root()`,
`real_production_value()`) rather than hardcoding the run root a second
time, but DO hardcode each store's known real production path once each
-- that duplication is intentional: it is the actual proof that the
captured "real" value equals what production really uses, not just that
it differs from whatever the module happens to have redirected it to.

Run with: python -m unittest tests.test_test_safety -v
"""
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import agent.audit as audit
import agent.execution_history as execution_history
import agent.jarvis_state as jarvis_state
import agent.scheduler_lock as scheduler_lock
import agent.skills.loader as skills_loader
import database.memory as database_memory
import tools.browser as browser
import tools.computer_use as computer_use
import tools.credential_store as credential_store
import tools.sandbox_python as sandbox_python
from agent.history_store import HISTORY_DB
from config.settings import settings
from tests import _safety


class TestCanonicalBootstrap(unittest.TestCase):

    def test_safety_was_installed_before_this_test_ran(self):
        self.assertTrue(_safety.TEST_SAFETY_INSTALLED)

    def test_init_sentinel_matches(self):
        import tests
        self.assertTrue(tests.TEST_SAFETY_INSTALLED)

    def test_run_root_exists_and_is_a_directory(self):
        self.assertTrue(os.path.isdir(_safety.run_root()))

    def test_run_root_is_not_the_real_application_support_directory(self):
        real_app_support = os.path.realpath(
            os.path.expanduser("~/Library/Application Support/CampusPilot")
        )
        self.assertNotEqual(os.path.realpath(_safety.run_root()), real_app_support)

    def test_installing_again_is_a_safe_no_op(self):
        root_before = _safety.run_root()
        _safety.install_test_safety()
        self.assertEqual(_safety.run_root(), root_before)


class TestStoreRedirection(unittest.TestCase):
    """Every constant tests/_safety.py's _install_store_redirects touches,
    checked two ways: (1) the CURRENT value lives under run_root(), and
    (2) the value real_production_value() captured before patching
    matches the actual, independently-known real production path -- not
    just "differs from current," which a bug could satisfy vacuously."""

    HOME = os.path.expanduser("~")

    def _assert_redirected(self, current, dotted_name, expected_real):
        root = _safety.run_root()
        self.assertTrue(
            current == root or current.startswith(root + os.sep),
            f"{dotted_name} = {current!r} is not under run_root() {root!r}",
        )
        real = _safety.real_production_value(dotted_name)
        self.assertEqual(real, expected_real)
        self.assertNotEqual(current, real)

    def test_tts_pid_file(self):
        import agent.tts_control as tts_control
        self._assert_redirected(
            tts_control.TTS_PID_FILE,
            "agent.tts_control.TTS_PID_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/tts.pid"),
        )

    def test_cancellation_dir(self):
        import agent.cancellation as cancellation
        self._assert_redirected(
            cancellation.CANCEL_DIR,
            "agent.cancellation.CANCEL_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot/cancellation_requests"),
        )

    def test_scheduled_tasks_file(self):
        import agent.scheduled_tasks as scheduled_tasks
        self._assert_redirected(
            scheduled_tasks.TASKS_FILE,
            "agent.scheduled_tasks.TASKS_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/scheduled_tasks.json"),
        )

    def test_audit_log_dir_and_file(self):
        self._assert_redirected(
            audit.LOG_DIR,
            "agent.audit.LOG_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot"),
        )
        self._assert_redirected(
            audit.LOG_FILE,
            "agent.audit.LOG_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/audit.log"),
        )

    def test_conversation_file(self):
        import agent.conversation_store as conversation_store
        self._assert_redirected(
            conversation_store.CONVERSATION_FILE,
            "agent.conversation_store.CONVERSATION_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/conversation.json"),
        )

    def test_quiet_mode_file(self):
        import agent.quiet_mode as quiet_mode
        self._assert_redirected(
            quiet_mode.QUIET_MODE_FILE,
            "agent.quiet_mode.QUIET_MODE_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/quiet_mode.json"),
        )

    def test_browser_lock_file(self):
        import agent.browser_lock as browser_lock
        self._assert_redirected(
            browser_lock.BROWSER_LOCK_FILE,
            "agent.browser_lock.BROWSER_LOCK_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/chrome-profile.lock"),
        )

    def test_history_db(self):
        import agent.history_store as history_store
        self._assert_redirected(
            history_store.HISTORY_DB,
            "agent.history_store.HISTORY_DB",
            os.path.expanduser("~/Library/Application Support/CampusPilot/history.db"),
        )
        self.assertEqual(history_store.HISTORY_DB, HISTORY_DB)

    def test_jarvis_state_file(self):
        self._assert_redirected(
            jarvis_state.STATE_FILE,
            "agent.jarvis_state.STATE_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/jarvis_state.json"),
        )

    def test_personal_context_catalog_file(self):
        import agent.personal_context as personal_context
        self._assert_redirected(
            personal_context.CATALOG_FILE,
            "agent.personal_context.CATALOG_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/personal_context.json"),
        )

    def test_execution_history_file(self):
        self._assert_redirected(
            execution_history.HISTORY_FILE,
            "agent.execution_history.HISTORY_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/execution_history.json"),
        )

    def test_scheduler_lock_file(self):
        self._assert_redirected(
            scheduler_lock.SCHEDULER_LOCK_FILE,
            "agent.scheduler_lock.SCHEDULER_LOCK_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/scheduler.lock"),
        )

    def test_usage_file(self):
        import agent.usage as usage
        self._assert_redirected(
            usage.USAGE_FILE,
            "agent.usage.USAGE_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/usage_history.json"),
        )

    def test_memory_file(self):
        self._assert_redirected(
            database_memory.MEMORY_FILE,
            "database.memory.MEMORY_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/memory.json"),
        )

    def test_credential_store_config_dir_and_logins_file(self):
        self._assert_redirected(
            credential_store.CONFIG_DIR,
            "tools.credential_store.CONFIG_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot"),
        )
        self._assert_redirected(
            credential_store.LOGINS_FILE,
            "tools.credential_store.LOGINS_FILE",
            os.path.expanduser("~/Library/Application Support/CampusPilot/logins.json"),
        )

    def test_keychain_service_is_not_the_real_production_service(self):
        real = _safety.real_production_value("tools.credential_store.KEYCHAIN_SERVICE")
        self.assertEqual(real, "CampusPilot")
        self.assertNotEqual(credential_store.KEYCHAIN_SERVICE, "CampusPilot")

    def test_computer_use_screenshot_dir(self):
        self._assert_redirected(
            computer_use.SCREENSHOT_DIR,
            "tools.computer_use.SCREENSHOT_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot/computer_use_screenshots"),
        )

    def test_browser_profile_dir(self):
        self._assert_redirected(
            browser.PROFILE_DIR,
            "tools.browser.PROFILE_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot/chrome-profile"),
        )

    def test_sandbox_dir_and_profile_path(self):
        self._assert_redirected(
            sandbox_python.SANDBOX_DIR,
            "tools.sandbox_python.SANDBOX_DIR",
            os.path.expanduser("~/Library/Application Support/CampusPilot/sandbox"),
        )
        self._assert_redirected(
            sandbox_python.PROFILE_PATH,
            "tools.sandbox_python.PROFILE_PATH",
            os.path.expanduser("~/Library/Application Support/CampusPilot/sandbox.sb"),
        )

    def test_skills_dirs_second_entry_redirected_first_entry_kept(self):
        real_dirs = _safety.real_production_value("agent.skills.loader.DEFAULT_SKILLS_DIRS")
        current = skills_loader.DEFAULT_SKILLS_DIRS
        self.assertEqual(len(real_dirs), 2)
        self.assertEqual(current[0], real_dirs[0])  # repo skills/ dir: reviewed, kept as-is
        self.assertNotEqual(current[1], real_dirs[1])  # real user Application Support skills dir
        self.assertTrue(current[1].startswith(_safety.run_root() + os.sep))
        self.assertEqual(real_dirs[1], os.path.expanduser("~/Library/Application Support/CampusPilot/skills"))

    def test_obsidian_vault_path_forced_none(self):
        self.assertIsNone(settings.obsidian_vault_path)


class TestProductionStoreMetadataUnchanged(unittest.TestCase):
    """Snapshots existence/size/mtime (never content) of the real
    production files BEFORE exercising a representative write through
    each redirected store's real function, then asserts nothing about
    those real files changed. This is the meta-test the S1 spec asked
    for: proof that a representative operation actually lands in
    run_root(), not just that a constant was reassigned somewhere."""

    REAL_PATHS = {
        "memory": os.path.expanduser("~/Library/Application Support/CampusPilot/memory.json"),
        "audit_log": os.path.expanduser("~/Library/Application Support/CampusPilot/audit.log"),
        "jarvis_state": os.path.expanduser("~/Library/Application Support/CampusPilot/jarvis_state.json"),
        "logins": os.path.expanduser("~/Library/Application Support/CampusPilot/logins.json"),
        "execution_history": os.path.expanduser("~/Library/Application Support/CampusPilot/execution_history.json"),
        "history_db": os.path.expanduser("~/Library/Application Support/CampusPilot/history.db"),
    }

    @staticmethod
    def _snapshot(path):
        if not os.path.exists(path):
            return None
        stat = os.stat(path)
        return (stat.st_size, stat.st_mtime_ns)

    def test_representative_operations_do_not_touch_real_files(self):
        before = {name: self._snapshot(path) for name, path in self.REAL_PATHS.items()}

        database_memory.save_memory("test_test_safety_probe", "value")
        audit.log_action("test_test_safety_probe", {}, "ok")
        jarvis_state.reset_to_idle()
        execution_history.record_started("test-test-safety-probe", "probe")
        from agent.history_store import create_session, initialize_history_store
        initialize_history_store(db_path=HISTORY_DB)
        create_session("chat", db_path=HISTORY_DB)

        after = {name: self._snapshot(path) for name, path in self.REAL_PATHS.items()}

        self.assertEqual(before, after)


class TestExternalNetworkFirewall(unittest.TestCase):

    def test_loopback_tcp_connect_succeeds(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(("127.0.0.1", port))
            client.close()
        finally:
            server.close()

    def test_loopback_ipv6_connect_succeeds_where_supported(self):
        try:
            server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            server.bind(("::1", 0))
        except OSError:
            self.skipTest("IPv6 loopback not available in this environment")
        server.listen(1)
        port = server.getsockname()[1]
        try:
            client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(("::1", port))
            client.close()
        finally:
            server.close()

    def test_localhost_hostname_resolves_and_connects(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(("localhost", port))
            client.close()
        finally:
            server.close()

    def test_external_ipv4_connect_is_blocked(self):
        # TEST-NET-1 (RFC 5737) -- guaranteed non-routable, and blocked
        # before any real connection attempt regardless.
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            client.connect(("192.0.2.1", 80))
        client.close()

    def test_external_ipv6_connect_is_blocked(self):
        # 2001:db8::/32 -- the IPv6 documentation prefix, non-routable.
        client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            client.connect(("2001:db8::1", 80))
        client.close()

    def test_external_hostname_blocked_before_dns_resolution(self):
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            socket.getaddrinfo("example.com", 80)

    def test_create_connection_to_external_host_is_blocked(self):
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            socket.create_connection(("192.0.2.1", 80), timeout=2)

    def test_udp_sendto_external_is_blocked(self):
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            client.sendto(b"x", ("192.0.2.1", 53))
        client.close()

    def test_udp_sendto_loopback_is_allowed(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.sendto(b"x", ("127.0.0.1", port))
            data, _ = server.recvfrom(16)
            self.assertEqual(data, b"x")
            client.close()
        finally:
            server.close()

    def test_af_unix_socket_is_never_inspected(self):
        # AF_UNIX addresses are filesystem paths, not network hosts --
        # must never be misread as an external host and blocked. Bound
        # directly under the system temp root (not run_root(), whose
        # longer prefix can exceed AF_UNIX's ~104-byte sun_path limit on
        # macOS).
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("AF_UNIX not available on this platform")
        import tempfile
        sock_path = tempfile.mktemp(suffix=".sock")
        if os.path.exists(sock_path):
            os.remove(sock_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(sock_path)
            client.close()
        finally:
            server.close()
            os.remove(sock_path)


class TestHttpxProviderTripwire(unittest.TestCase):
    """The higher-level httpx tripwire, distinct from the socket-level
    firewall above -- proves it's compatible with real loopback HTTP
    servers (the shape OpenClaw's own tests use) and still blocks an
    external request."""

    @classmethod
    def setUpClass(cls):
        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)

    def test_real_loopback_http_request_succeeds(self):
        import httpx
        response = httpx.get(f"http://127.0.0.1:{self.port}/", timeout=2)
        self.assertEqual(response.status_code, 200)

    def test_external_http_request_is_blocked_locally(self):
        import httpx
        with self.assertRaises(_safety.ExternalNetworkBlocked):
            httpx.get("http://192.0.2.1/", timeout=2)


class TestKeychainAndCredentialStoreIsolation(unittest.TestCase):

    def test_keychain_service_differs_from_production_and_from_mocked_unit_test_name(self):
        # tests/_safety.py's seam ("CampusPilot-TEST") is a defense-in-
        # depth redirect, distinct from both the real production service
        # and this file's own smoke-test-only name -- see
        # tools/keychain_smoke_test.py, never run by this canonical
        # suite.
        self.assertNotEqual(credential_store.KEYCHAIN_SERVICE, "CampusPilot")

    def test_logins_file_is_not_the_real_production_logins_file(self):
        real = os.path.expanduser("~/Library/Application Support/CampusPilot/logins.json")
        self.assertNotEqual(credential_store.LOGINS_FILE, real)


class TestObsidianAndSkillsIsolation(unittest.TestCase):

    def test_obsidian_vault_path_is_none_under_test(self):
        self.assertIsNone(settings.obsidian_vault_path)

    def test_skills_load_without_touching_real_user_skills_dir(self):
        real_user_skills_dir = os.path.expanduser("~/Library/Application Support/CampusPilot/skills")
        loaded = skills_loader.load_all_skills()
        # Only the repo's own reviewed skills/ subdirectories can appear --
        # nothing from a real user's Application Support skills dir.
        repo_skill_names = {
            entry.name for entry in os.scandir(skills_loader.DEFAULT_SKILLS_DIRS[0]) if entry.is_dir()
        }
        loaded_names = {skill.name for skill in loaded}
        self.assertTrue(loaded_names.issubset(repo_skill_names) or loaded_names == repo_skill_names)
        self.assertNotEqual(skills_loader.DEFAULT_SKILLS_DIRS[1], real_user_skills_dir)


class TestBrowserAndComputerUseTripwire(unittest.TestCase):

    def test_sync_playwright_is_poisoned_by_default(self):
        with self.assertRaises(_safety.RealBrowserBlocked):
            browser.sync_playwright()

    def test_pyautogui_every_attribute_is_poisoned_by_default(self):
        with self.assertRaises(_safety.RealBrowserBlocked):
            computer_use.pyautogui.click(1, 1)
        with self.assertRaises(_safety.RealBrowserBlocked):
            computer_use.pyautogui.screenshot()

    def test_per_test_mock_still_overrides_and_restores_cleanly(self):
        from unittest.mock import patch, MagicMock

        with patch("tools.browser.sync_playwright", MagicMock(return_value="mocked")):
            self.assertEqual(browser.sync_playwright(), "mocked")

        # Poisoned default resumes after the patch context exits.
        with self.assertRaises(_safety.RealBrowserBlocked):
            browser.sync_playwright()


class TestRealSandboxIsolationUsesRunRoot(unittest.TestCase):
    """The real sandbox-exec/Seatbelt boundary is not mocked (see
    tests/test_safety.py::TestSandboxIsolation) -- this proves the
    redirected SANDBOX_DIR/PROFILE_PATH the S1 pass introduced actually
    resolve to run_root() and that the real kernel policy built from
    them still both permits an in-sandbox write and denies escape,
    catching the class of symlink/canonicalization bug this pass found
    empirically (macOS's default temp root is itself a symlink)."""

    def test_sandbox_paths_are_under_run_root(self):
        root = _safety.run_root()
        self.assertTrue(sandbox_python.SANDBOX_DIR.startswith(root + os.sep))
        self.assertTrue(sandbox_python.PROFILE_PATH.startswith(root + os.sep))

    def test_real_write_inside_the_redirected_sandbox_succeeds(self):
        result = sandbox_python.run_python(
            "open('probe.txt', 'w').write('ok'); print('wrote-ok')"
        )
        self.assertIn("wrote-ok", result)
        self.assertIn("succeeded", result)
        self.assertTrue(
            os.path.exists(os.path.join(sandbox_python.SANDBOX_DIR, "probe.txt"))
        )

    def test_real_write_outside_the_redirected_sandbox_is_still_denied(self):
        target = os.path.join(_safety.run_root(), "escape_attempt.txt")
        result = sandbox_python.run_python(f"open({target!r}, 'w').write('should not exist')")
        self.assertFalse(os.path.exists(target))
        self.assertIn("exited with code", result)


if __name__ == "__main__":
    unittest.main()
