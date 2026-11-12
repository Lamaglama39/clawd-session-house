"""Tests for server.py (session state machine and HTTP endpoints)."""

import http.client
import json
import os
import sys
import threading
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "server")
)

import server  # noqa: E402


class FakeClock:
    """Manually advanced clock returning epoch milliseconds."""

    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += ms


def event(name, session_id="s1", cwd="/work/proj", **fields):
    return {"session_id": session_id, "cwd": cwd, "hook_event_name": name, **fields}


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = server.SessionStore(
            ended_linger_ms=60_000, expire_ms=3_600_000, clock=self.clock
        )

    def state(self, session_id="s1"):
        sessions = {s["id"]: s for s in self.store.snapshot()["sessions"]}
        return sessions[session_id]

    def test_session_start_creates_session_in_start_state(self):
        self.store.apply_event(event("SessionStart", source="startup"))
        s = self.state()
        self.assertEqual(s["state"], "START")
        self.assertEqual(s["project"], "proj")
        self.assertEqual(s["cwd"], "/work/proj")

    def test_event_to_state_mapping(self):
        cases = [
            ("UserPromptSubmit", {}, "BUSY"),
            ("PreToolUse", {"tool_name": "Bash"}, "BUSY"),
            ("PermissionRequest", {"tool_name": "Bash"}, "WAIT"),
            ("PostToolUse", {"tool_name": "Bash"}, "BUSY"),
            ("PermissionRequest", {"tool_name": "Edit"}, "WAIT"),
            ("PostToolUseFailure", {"tool_name": "Edit"}, "BUSY"),
            ("Stop", {}, "IDLE"),
            ("UserPromptSubmit", {}, "BUSY"),
            ("PermissionDenied", {"tool_name": "Bash"}, "BUSY"),
            ("Elicitation", {}, "WAIT"),
            ("ElicitationResult", {}, "BUSY"),
            ("StopFailure", {}, "IDLE"),
            ("SessionEnd", {"reason": "other"}, "OFF"),
        ]
        self.store.apply_event(event("SessionStart", source="startup"))
        for name, fields, expected in cases:
            with self.subTest(event=name, expected=expected):
                self.store.apply_event(event(name, **fields))
                self.assertEqual(self.state()["state"], expected)

    def test_notification_types(self):
        self.store.apply_event(event("UserPromptSubmit"))
        self.store.apply_event(event("Notification", notification_type="auth_success"))
        self.assertEqual(self.state()["state"], "BUSY")
        self.store.apply_event(event("Notification", notification_type="permission_prompt"))
        self.assertEqual(self.state()["state"], "WAIT")
        self.store.apply_event(event("Notification", notification_type="idle_prompt"))
        self.assertEqual(self.state()["state"], "IDLE")
        self.store.apply_event(event("Notification", notification_type="elicitation_dialog"))
        self.assertEqual(self.state()["state"], "WAIT")

    def test_compact_session_start_keeps_current_state(self):
        self.store.apply_event(event("UserPromptSubmit"))
        self.store.apply_event(event("SessionStart", source="compact"))
        self.assertEqual(self.state()["state"], "BUSY")

    def test_resume_replays_start_even_if_already_start(self):
        self.store.apply_event(event("SessionStart", source="startup"))
        first_since = self.state()["since"]
        self.clock.advance(5_000)
        self.store.apply_event(event("SessionStart", source="resume"))
        s = self.state()
        self.assertEqual(s["state"], "START")
        self.assertEqual(s["since"], first_since + 5_000)

    def test_since_is_kept_while_state_is_unchanged(self):
        self.store.apply_event(event("UserPromptSubmit"))
        since = self.state()["since"]
        self.clock.advance(1_000)
        self.store.apply_event(event("PreToolUse", tool_name="Read"))
        self.clock.advance(1_000)
        self.store.apply_event(event("PostToolUse", tool_name="Read"))
        s = self.state()
        self.assertEqual(s["since"], since)
        self.assertEqual(s["updatedAt"], since + 2_000)

    def test_tool_name_survives_notification_and_clears_on_stop(self):
        self.store.apply_event(event("PreToolUse", tool_name="Bash"))
        self.store.apply_event(event("PermissionRequest", tool_name="Bash"))
        self.store.apply_event(event("Notification", notification_type="permission_prompt"))
        self.assertEqual(self.state()["tool"], "Bash")
        self.store.apply_event(event("Stop"))
        self.assertIsNone(self.state()["tool"])

    def test_permission_mode_is_recorded(self):
        self.store.apply_event(event("UserPromptSubmit", permission_mode="plan"))
        self.assertEqual(self.state()["mode"], "plan")
        # Events without permission_mode keep the previous value
        self.store.apply_event(event("Notification", notification_type="idle_prompt"))
        self.assertEqual(self.state()["mode"], "plan")

    def test_session_title_is_tracked(self):
        self.store.apply_event(event("SessionStart", source="startup"))
        self.assertIsNone(self.state()["title"])
        self.store.apply_event(event("SessionStart", source="resume", session_title=" auth-fix "))
        self.assertEqual(self.state()["title"], "auth-fix")
        # Events without session_title keep the previous value
        self.store.apply_event(event("PreToolUse", tool_name="Bash"))
        self.store.apply_event(event("Stop"))
        self.assertEqual(self.state()["title"], "auth-fix")
        # The result of /rename arrives with the next UserPromptSubmit
        self.store.apply_event(event("UserPromptSubmit", session_title="READMEの更新"))
        self.assertEqual(self.state()["title"], "READMEの更新")
        # A UserPromptSubmit without the field means the name was removed
        self.store.apply_event(event("UserPromptSubmit"))
        self.assertIsNone(self.state()["title"])

    def test_session_title_is_sanitized(self):
        for value in (123, "", "   ", None):
            with self.subTest(value=value):
                self.store.apply_event(event("UserPromptSubmit", session_title=value))
                self.assertIsNone(self.state()["title"])
        self.store.apply_event(event("UserPromptSubmit", session_title="x" * 500))
        self.assertEqual(len(self.state()["title"]), server.MAX_TITLE_CHARS)

    def test_invalid_payloads_are_ignored(self):
        for payload in (None, [], "x", {}, {"session_id": "s1"}, {"hook_event_name": "Stop"}):
            with self.subTest(payload=payload):
                self.assertIsNone(self.store.apply_event(payload))
        self.assertEqual(self.store.snapshot()["sessions"], [])

    def test_ended_session_lingers_then_is_pruned(self):
        self.store.apply_event(event("SessionStart"))
        self.store.apply_event(event("SessionEnd"))
        self.clock.advance(59_000)
        self.assertFalse(self.store.prune())
        self.assertEqual(len(self.store.snapshot()["sessions"]), 1)
        self.clock.advance(1_000)
        self.assertTrue(self.store.prune())
        self.assertEqual(self.store.snapshot()["sessions"], [])

    def test_restart_after_end_cancels_removal(self):
        self.store.apply_event(event("SessionEnd"))
        self.clock.advance(30_000)
        self.store.apply_event(event("SessionStart", source="resume"))
        self.clock.advance(60_000)
        self.store.prune()
        self.assertEqual(self.state()["state"], "START")
        self.assertNotIn("endedAt", self.state())

    def test_silent_sessions_expire(self):
        self.store.apply_event(event("Stop"))
        self.clock.advance(3_599_999)
        self.assertFalse(self.store.prune())
        self.clock.advance(1)
        self.assertTrue(self.store.prune())

    def test_snapshot_is_ordered_by_start_time(self):
        self.store.apply_event(event("SessionStart", session_id="b"))
        self.clock.advance(10)
        self.store.apply_event(event("SessionStart", session_id="a"))
        self.clock.advance(10)
        self.store.apply_event(event("Stop", session_id="b"))
        ids = [s["id"] for s in self.store.snapshot()["sessions"]]
        self.assertEqual(ids, ["b", "a"])

    def test_remove_and_version(self):
        v0 = self.store.version
        self.store.apply_event(event("SessionStart"))
        self.assertGreater(self.store.version, v0)
        self.assertTrue(self.store.remove("s1"))
        self.assertFalse(self.store.remove("s1"))

    def test_remove_live_session_until_its_next_event(self):
        # A session whose SessionEnd never arrived looks live; the dashboard lets users remove it
        self.store.apply_event(event("SessionStart", source="startup", session_title="Fix"))
        self.store.apply_event(event("PreToolUse", tool_name="Bash"))
        self.assertTrue(self.store.remove("s1"))
        self.assertEqual(self.store.snapshot()["sessions"], [])
        # If the session was still alive, its next event brings the tile back
        self.store.apply_event(event("PostToolUse", tool_name="Bash"))
        s = self.state()
        self.assertEqual(s["state"], "BUSY")
        self.assertIsNone(s["title"])

    def test_wait_for_change_wakes_up(self):
        version = self.store.version
        threading.Timer(0.05, lambda: self.store.apply_event(event("Stop"))).start()
        self.assertNotEqual(self.store.wait_for_change(version, timeout=2), version)

    def test_project_name(self):
        self.assertEqual(server.project_name("/a/b/proj/"), "proj")
        self.assertEqual(server.project_name(""), "(unknown)")
        self.assertEqual(server.project_name("/"), "/")


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = server.SessionStore()
        cls.httpd = server.StatusServer(("127.0.0.1", 0), cls.store, log_events=False)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutting_down.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def post_hook(self, payload, content_type="application/json"):
        return self.request(
            "POST",
            "/hook",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": content_type},
        )

    def test_post_hook_returns_empty_body(self):
        status, headers, body = self.post_hook(event("SessionStart", session_id="http-1"))
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), "0")
        status, _, body = self.request("GET", "/api/sessions")
        ids = [s["id"] for s in json.loads(body)["sessions"]]
        self.assertIn("http-1", ids)

    def test_post_hook_rejects_non_json_content_type(self):
        status, _, _ = self.post_hook(event("Stop"), content_type="text/plain")
        self.assertEqual(status, 415)

    def test_post_hook_rejects_invalid_json(self):
        status, _, _ = self.request(
            "POST", "/hook", body=b"{not json", headers={"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)

    def test_foreign_host_header_is_rejected(self):
        status, _, _ = self.request("GET", "/api/sessions", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET", "/api/sessions", headers={"Host": f"localhost:{self.port}"}
        )
        self.assertEqual(status, 200)

    def test_delete_session(self):
        self.post_hook(event("SessionStart", session_id="to-delete"))
        status, headers, _ = self.request("DELETE", "/api/sessions/to-delete")
        self.assertEqual(status, 204)
        self.assertNotIn("Content-Length", headers)
        status, _, _ = self.request("DELETE", "/api/sessions/to-delete")
        self.assertEqual(status, 404)

    def test_static_files_and_traversal(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Clawd Session House", body)
        self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")
        status, headers, _ = self.request("GET", "/clawd.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        status, headers, _ = self.request("GET", "/favicon.svg")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", headers["Content-Type"])
        for path in ("/../server.py", "/%2e%2e/server.py", "/missing.js"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(status, 404)

    def test_event_stream_sends_snapshot_first(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/events")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.getheader("Content-Type"))
            self.assertEqual(resp.fp.readline(), b"event: snapshot\n")
            data = resp.fp.readline()
            self.assertTrue(data.startswith(b"data: "))
            self.assertIn("sessions", json.loads(data[len(b"data: ") :]))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
