"""The Alexa bridge listener -- owns the HTTP loop, agent/alexa_bridge.py
owns the logic. Same split as telegram_daemon/telegram_bridge.

Run with:  python3 -m agent.alexa_daemon
(or via the launcher: bash ~/CampusPilot/start_alexa_bridge.sh)

Binds 127.0.0.1 only. The Alexa skill cannot reach a loopback port on
this Mac by itself -- a tunnel (cloudflared) forwards to it, which means
the tunnel is the single ingress and nothing else on the local network
can talk to this directly.

Routes, all requiring the bearer token except /health:

  POST /task   {"task": "..."}  -> 202 immediately, runs in background
  GET  /last                    -> the last finished task, for read-back
  GET  /health                  -> liveness only, no token, no secrets

/health is deliberately unauthenticated and deliberately says nothing
useful: it exists so the launcher can wait for the port, and an
unauthenticated scanner learns only that something is listening.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent import alexa_bridge
from agent.observability import log_event

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# A task instruction is a sentence, not a payload. Anything larger is
# not a thing an Echo produced, so it is refused before it is read.
MAX_BODY_BYTES = 4096


class _Handler(BaseHTTPRequestHandler):
    server_version = "JarvisAlexaBridge/1.0"
    sys_version = ""  # don't advertise the Python version

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if alexa_bridge.verify_token(self.headers.get("Authorization")):
            return True
        # Logged without the presented value: it is attacker-controlled
        # and, if a near-miss of the real token, not something to write
        # to disk.
        log_event(
            "alexa_bridge_unauthorized", component="alexa_daemon",
            level="warning", path=self.path,
        )
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's interface
        if self.path == "/health":
            self._send(200, {"ok": True, "configured": alexa_bridge.is_configured()})
            return
        if not self._authorized():
            return
        if self.path == "/last":
            record = alexa_bridge.load_last_result()
            self._send(200, {
                "spoken": alexa_bridge.spoken_summary(record),
                "busy": alexa_bridge.is_busy(),
                "task": record.get("task"),
                "ok": record.get("ok"),
                "finished_at": record.get("finished_at"),
            })
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return
        if self.path != "/task":
            self._send(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad content length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(413, {"error": "body too large or empty"})
            return

        try:
            payload = json.loads(self.rfile.read(length))
            task = str(payload.get("task", "")).strip()
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return
        if not task:
            self._send(400, {"error": "empty task"})
            return

        if alexa_bridge.is_busy():
            # Answered, not queued: a Dot that misfires twice shouldn't
            # stack up agent turns, and the skill can say so out loud.
            self._send(409, {"accepted": False, "reason": "busy"})
            return

        accepted = alexa_bridge.start_task(task)
        self._send(202 if accepted else 409, {"accepted": accepted})

    def log_message(self, *args):
        """Silence stderr access logging -- agent.observability.log_event
        is this project's record, and raw request lines would put
        attacker-controlled paths straight into the terminal."""
        return


def main() -> int:
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"bad port: {sys.argv[1]}", file=sys.stderr)
            return 2

    if not alexa_bridge.is_configured():
        print(
            "No ALEXA_BRIDGE_TOKEN configured -- the bridge would accept\n"
            "unauthenticated instructions, so it is refusing to start.\n"
            "Set one with:  python3 -c \"from agent.secrets import set_secret; \"\\\n"
            "  \"set_secret('ALEXA_BRIDGE_TOKEN', 'YOUR-LONG-RANDOM-STRING')\"",
            file=sys.stderr,
        )
        return 1

    server = ThreadingHTTPServer((HOST, port), _Handler)
    log_event("alexa_bridge_listening", component="alexa_daemon", port=port)
    print(f"Jarvis Alexa bridge listening on http://{HOST}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
        log_event("alexa_bridge_stopped", component="alexa_daemon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
