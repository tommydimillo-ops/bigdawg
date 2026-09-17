"""Tests for agent/alexa_daemon.py's HTTP listener. Runs the real
_Handler against a real loopback socket (an ephemeral port, like the
local fake Gateway server tests/test_openclaw_gateway.py already uses
for its own server-side code) -- only agent.executor.execute_task and
agent.telegram_bridge (this process's own external boundaries) are
mocked, never the HTTP layer itself.

Run with: python -m unittest tests.test_alexa_daemon -v
"""
import http.client
import json
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import agent.alexa_bridge as ab
import agent.alexa_daemon as ad

FAKE_TOKEN = "test-bridge-token-xyz"


def _wait_for_alexa_task(timeout=10.0):
    """Deterministically wait for start_task()'s background worker to
    finish -- joins the real thread by name (a real CI runner can be
    materially slower than a quiet local machine, a class of flake this
    project has hit before; see .github/workflows/tests.yml's own
    timeout-raise comment), falling back to a bounded is_busy() poll only
    for the race where the thread already finished and was reaped before
    this could enumerate it."""
    deadline = time.time() + timeout
    for thread in threading.enumerate():
        if thread.name == "alexa-task":
            thread.join(timeout=max(0.0, deadline - time.time()))
            break
    while ab.is_busy() and time.time() < deadline:
        time.sleep(0.02)


class _RealDaemonTestCase(unittest.TestCase):
    """Starts a real ThreadingHTTPServer bound to an OS-assigned loopback
    port, running the real _Handler, for the duration of each test."""

    def setUp(self):
        self._secret_patch = patch("agent.alexa_bridge.get_secret", return_value=FAKE_TOKEN)
        self._secret_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ad._Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        # Join before force-releasing: a still-running worker thread that
        # believes it owns the lock must not have it yanked out from
        # under it, or a later test's start_task() could succeed while
        # this stray thread is still using this test's now-closed mocks.
        _wait_for_alexa_task(timeout=10.0)
        self._secret_patch.stop()
        if ab._run_lock.locked():
            ab._run_lock.release()
        import os
        for path in (ab.LAST_RESULT_FILE, f"{ab.LAST_RESULT_FILE}.tmp"):
            if os.path.exists(path):
                os.remove(path)

    def _request(self, method, path, body=None, token=FAKE_TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        parsed = json.loads(raw) if raw else None
        return resp.status, parsed, resp


class TestHealth(_RealDaemonTestCase):

    def test_health_needs_no_token(self):
        status, payload, _ = self._request("GET", "/health", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["configured"])

    def test_health_does_not_leak_the_python_version(self):
        _, _, resp = self._request("GET", "/health", token=None)
        self.assertEqual(resp.getheader("Server", "").split(" ")[0], "JarvisAlexaBridge/1.0")
        self.assertNotIn("Python", resp.getheader("Server", ""))


class TestAuth(_RealDaemonTestCase):

    def test_task_without_a_token_is_unauthorized(self):
        status, payload, _ = self._request("POST", "/task", body={"task": "x"}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_task_with_the_wrong_token_is_unauthorized(self):
        status, _, _ = self._request("POST", "/task", body={"task": "x"}, token="wrong-token")
        self.assertEqual(status, 401)

    def test_last_without_a_token_is_unauthorized(self):
        status, _, _ = self._request("GET", "/last", token=None)
        self.assertEqual(status, 401)

    def test_unknown_path_without_a_token_is_unauthorized_not_404(self):
        # Auth is checked before routing for every path except /health.
        status, _, _ = self._request("GET", "/nonsense", token=None)
        self.assertEqual(status, 401)

    def test_unknown_path_with_a_token_is_404(self):
        status, _, _ = self._request("GET", "/nonsense")
        self.assertEqual(status, 404)


class TestTaskSubmission(_RealDaemonTestCase):

    def test_valid_task_is_accepted_and_eventually_recorded(self):
        with patch("agent.executor.execute_task", return_value="all done"), \
             patch("agent.telegram_bridge.is_configured", return_value=False):
            status, payload, _ = self._request("POST", "/task", body={"task": "check my calendar"})
            self.assertEqual(status, 202)
            self.assertTrue(payload["accepted"])

            _wait_for_alexa_task(timeout=10.0)

        last = ab.load_last_result()
        self.assertEqual(last["task"], "check my calendar")
        self.assertEqual(last["result"], "all done")

    def test_empty_task_is_rejected(self):
        status, payload, _ = self._request("POST", "/task", body={"task": "   "})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_missing_task_field_is_rejected(self):
        status, _, _ = self._request("POST", "/task", body={})
        self.assertEqual(status, 400)

    def test_malformed_json_is_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST", "/task", body=b"{not json",
            headers={"Authorization": f"Bearer {FAKE_TOKEN}", "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        self.assertEqual(status, 400)

    def test_oversized_body_is_rejected_before_being_read_as_json(self):
        huge_task = "x" * (ad.MAX_BODY_BYTES + 1000)
        status, _, _ = self._request("POST", "/task", body={"task": huge_task})
        self.assertEqual(status, 413)

    def test_zero_length_body_is_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/task", body=b"", headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        self.assertEqual(status, 413)

    def test_unknown_path_returns_404(self):
        status, _, _ = self._request("POST", "/nope", body={"task": "x"})
        self.assertEqual(status, 404)

    def test_second_task_while_busy_gets_409_and_is_not_run(self):
        ab._run_lock.acquire()
        try:
            with patch("agent.executor.execute_task") as exec_mock:
                status, payload, _ = self._request("POST", "/task", body={"task": "should be refused"})
            exec_mock.assert_not_called()
        finally:
            ab._run_lock.release()
        self.assertEqual(status, 409)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["reason"], "busy")


class TestLastEndpoint(_RealDaemonTestCase):

    def test_last_before_anything_ran_reports_the_placeholder(self):
        status, payload, _ = self._request("GET", "/last")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["task"])
        self.assertFalse(payload["busy"])
        self.assertIn("haven't finished", payload["spoken"])

    def test_last_after_a_task_reflects_it(self):
        with patch("agent.executor.execute_task", return_value="the answer"), \
             patch("agent.telegram_bridge.is_configured", return_value=False):
            self._request("POST", "/task", body={"task": "what's 2+2"})
            _wait_for_alexa_task(timeout=10.0)

        status, payload, _ = self._request("GET", "/last")
        self.assertEqual(status, 200)
        self.assertEqual(payload["task"], "what's 2+2")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["spoken"], "the answer")


class TestMainGuards(unittest.TestCase):

    def test_refuses_to_start_without_a_token_and_never_binds_a_socket(self):
        with patch("sys.argv", ["alexa_daemon.py"]), \
             patch("agent.alexa_bridge.is_configured", return_value=False), \
             patch("agent.alexa_daemon.ThreadingHTTPServer") as server_cls:
            exit_code = ad.main()
        self.assertEqual(exit_code, 1)
        server_cls.assert_not_called()

    def test_bad_port_argument_exits_before_checking_configuration(self):
        with patch("sys.argv", ["alexa_daemon.py", "not-a-port"]), \
             patch("agent.alexa_bridge.is_configured") as is_configured_mock:
            exit_code = ad.main()
        self.assertEqual(exit_code, 2)
        is_configured_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
