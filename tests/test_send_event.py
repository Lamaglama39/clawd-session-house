"""Integration tests for hooks/send-event.sh against a real server."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import server  # noqa: E402

SEND_EVENT = os.path.join(HERE, "..", "hooks", "send-event.sh")


def run_hook(payload, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_STATUS_")}
    env.update(env_extra)
    return subprocess.run(
        [SEND_EVENT],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SendEventTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = server.SessionStore()
        cls.httpd = server.StatusServer(("127.0.0.1", 0), cls.store, log_events=False)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutting_down.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_forwards_payload_silently(self):
        payload = {
            "session_id": "hook-1",
            "cwd": "/work/demo",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "echo 'quoted' \"text\" $HOME"},
        }
        result = run_hook(payload, {"CLAUDE_STATUS_PORT": str(self.port)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        sessions = {s["id"]: s for s in self.store.snapshot()["sessions"]}
        self.assertEqual(sessions["hook-1"]["state"], "WAIT")
        self.assertEqual(sessions["hook-1"]["tool"], "Bash")

    def test_url_variable_overrides_port(self):
        url = f"http://127.0.0.1:{self.port}/hook"
        payload = {"session_id": "hook-2", "hook_event_name": "Stop"}
        result = run_hook(payload, {"CLAUDE_STATUS_URL": url, "CLAUDE_STATUS_PORT": "1"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("hook-2", [s["id"] for s in self.store.snapshot()["sessions"]])

    def test_server_down_is_silent_and_fast(self):
        started = time.monotonic()
        result = run_hook(
            {"session_id": "x", "hook_event_name": "Stop"},
            {"CLAUDE_STATUS_PORT": str(unused_port())},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
