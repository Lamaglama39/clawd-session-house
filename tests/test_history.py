"""Tests for history.py (SQLite recording and aggregation) and the history API."""

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "server")
)

import history  # noqa: E402
import server  # noqa: E402

TZ = "Asia/Tokyo"
MIN = 60_000


def at(*args):
    """Epoch ms of a wall-clock time in TZ."""
    return int(datetime(*args, tzinfo=ZoneInfo(TZ)).timestamp() * 1000)


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        # Pin the time zone so the daily and hourly totals do not depend on the machine
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = TZ
        time.tzset()
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock(at(2026, 10, 20, 10, 0))
        self.store = server.SessionStore(clock=self.clock)
        self.history = history.HistoryStore(
            os.path.join(self.tmp.name, "sub", "history.sqlite3"), clock=self.clock
        )

    def tearDown(self):
        self.history.close()
        self.tmp.cleanup()
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()

    def send(self, when, name, session_id="s1", cwd="/work/api", **fields):
        self.clock.now = when
        payload = {"session_id": session_id, "cwd": cwd, "hook_event_name": name, **fields}
        session = self.store.apply_event(payload)
        if session is not None:
            self.history.record(payload, session)
        return session

    def basic_session(self):
        self.send(at(2026, 10, 20, 10, 0), "SessionStart", source="startup")
        self.send(at(2026, 10, 20, 10, 1), "UserPromptSubmit", prompt="SECRET_PROMPT")
        self.send(
            at(2026, 10, 20, 10, 2), "PreToolUse", tool_name="Bash", tool_input={"command": "ls"}
        )
        self.send(at(2026, 10, 20, 10, 3), "PermissionRequest", tool_name="Bash")
        self.send(at(2026, 10, 20, 10, 5), "PostToolUse", tool_name="Bash")
        self.send(at(2026, 10, 20, 10, 6), "Stop")
        self.send(at(2026, 10, 20, 10, 10), "SessionEnd", reason="prompt_input_exit")


class RecordingTest(HistoryTestCase):
    def test_creates_parent_directory_and_schema(self):
        self.assertTrue(os.path.exists(self.history.path))
        with sqlite3.connect(self.history.path) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        self.assertTrue({"sessions", "events", "subagents", "meta"} <= tables)

    def test_prompt_and_tool_input_are_not_stored(self):
        self.basic_session()
        with sqlite3.connect(self.history.path) as conn:
            dump = "\n".join(conn.iterdump())
        self.assertNotIn("SECRET_PROMPT", dump)
        self.assertNotIn('"command"', dump)

    def test_session_row_tracks_title_and_end(self):
        self.send(at(2026, 10, 20, 9, 0), "SessionStart", session_title="認証の修正")
        self.send(at(2026, 10, 20, 9, 1), "UserPromptSubmit")
        self.send(at(2026, 10, 20, 9, 2), "SessionEnd", reason="other")
        [row] = self.history.sessions(0, at(2026, 10, 21, 0, 0))
        # Even after the title is removed, the review keeps the last name it had
        self.assertEqual(row["title"], "認証の修正")
        self.assertEqual(row["endedAt"], at(2026, 10, 20, 9, 2))
        self.assertEqual(row["endReason"], "other")
        # Resuming with the same ID clears the end time
        self.send(at(2026, 10, 20, 9, 30), "SessionStart", source="resume")
        [row] = self.history.sessions(0, at(2026, 10, 21, 0, 0))
        self.assertIsNone(row["endedAt"])

    def test_purge_removes_old_rows(self):
        self.send(at(2026, 1, 1, 10, 0), "UserPromptSubmit", session_id="old")
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit", session_id="new")
        removed = self.history.purge()
        self.assertEqual(removed, 1)
        ids = [s["id"] for s in self.history.sessions(0, at(2026, 10, 21, 0, 0))]
        self.assertEqual(ids, ["new"])


class SummaryTest(HistoryTestCase):
    def test_basic_totals(self):
        self.basic_session()
        start, end = at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0)
        s = self.history.summary(start, end)
        t = s["totals"]
        self.assertEqual(t["sessions"], 1)
        self.assertEqual(t["prompts"], 1)
        self.assertEqual(t["toolCalls"], 1)
        self.assertEqual(t["approvals"], 1)
        # Busy: 10:01-10:03 and 10:05-10:06; waiting for approval: 10:03-10:05
        self.assertEqual(t["busyMs"], 3 * MIN)
        self.assertEqual(t["waitMs"], 2 * MIN)
        self.assertEqual(t["waitCount"], 1)
        self.assertEqual(t["avgWaitMs"], 2 * MIN)
        self.assertEqual(
            s["daily"],
            [
                {
                    "date": "2026-10-20",
                    "busyMs": 3 * MIN,
                    "waitMs": 2 * MIN,
                    "prompts": 1,
                    "sessions": 1,
                }
            ],
        )
        self.assertEqual(s["hourly"][10]["busyMs"], 3 * MIN)
        self.assertEqual(s["projects"][0]["project"], "api")
        self.assertEqual(s["tools"], [{"tool": "Bash", "count": 1}])

    def test_long_gaps_are_capped(self):
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit")
        self.send(at(2026, 10, 20, 12, 0), "Stop")
        t = self.history.summary(at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0))["totals"]
        self.assertEqual(t["busyMs"], history.MAX_INTERVAL_MS)

    def test_interval_crossing_range_start_is_clipped(self):
        self.send(at(2026, 10, 19, 23, 50), "UserPromptSubmit")
        self.send(at(2026, 10, 20, 0, 10), "Stop")
        t = self.history.summary(at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0))["totals"]
        self.assertEqual(t["busyMs"], 10 * MIN)
        self.assertEqual(t["prompts"], 0)

    def test_live_session_counts_until_now(self):
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit")
        self.clock.now = at(2026, 10, 20, 10, 7)
        t = self.history.summary(at(2026, 10, 20, 0, 0), self.clock.now)["totals"]
        self.assertEqual(t["busyMs"], 7 * MIN)

    def test_daily_series_includes_empty_days(self):
        self.send(at(2026, 10, 18, 10, 0), "UserPromptSubmit", session_id="a")
        self.send(at(2026, 10, 18, 10, 5), "Stop", session_id="a")
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit", session_id="b", cwd="/work/web")
        self.send(at(2026, 10, 20, 10, 1), "Stop", session_id="b", cwd="/work/web")
        s = self.history.summary(at(2026, 10, 18, 0, 0), at(2026, 10, 21, 0, 0))
        self.assertEqual(
            [d["date"] for d in s["daily"]], ["2026-10-18", "2026-10-19", "2026-10-20"]
        )
        self.assertEqual([d["busyMs"] for d in s["daily"]], [5 * MIN, 0, 1 * MIN])
        self.assertEqual([p["project"] for p in s["projects"]], ["api", "web"])

    def test_subagents_and_tasks(self):
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit")
        self.send(
            at(2026, 10, 20, 10, 1),
            "PreToolUse",
            tool_name="Agent",
            tool_use_id="t1",
            tool_input={"subagent_type": "Explore", "description": "コード調査"},
        )
        self.send(at(2026, 10, 20, 10, 1), "SubagentStart", agent_id="a1", agent_type="Explore")
        for minute in (2, 3):
            self.send(
                at(2026, 10, 20, 10, minute),
                "PreToolUse",
                agent_id="a1",
                agent_type="Explore",
                tool_name="Grep",
            )
        self.send(at(2026, 10, 20, 10, 4), "SubagentStop", agent_id="a1", agent_type="Explore")
        self.send(at(2026, 10, 20, 10, 5), "TaskCreated", task_id="1", task_subject="設計")
        self.send(at(2026, 10, 20, 10, 6), "TaskCompleted", task_id="1", task_subject="設計")
        self.send(at(2026, 10, 20, 10, 7), "Stop")
        s = self.history.summary(at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0))
        self.assertEqual(s["totals"]["subagents"], 1)
        self.assertEqual(s["agents"], [{"type": "Explore", "count": 1, "avgDurationMs": 3 * MIN}])
        # Tools inside subagents count toward the per-tool totals
        self.assertEqual({t["tool"]: t["count"] for t in s["tools"]}, {"Agent": 1, "Grep": 2})
        detail = self.history.session_detail("s1")
        [agent] = detail["subagents"]
        self.assertEqual(agent["description"], "コード調査")
        self.assertEqual(agent["toolCount"], 2)
        self.assertEqual(detail["tasks"][0]["subject"], "設計")
        self.assertEqual(detail["session"]["subagents"], 1)

    def test_internal_agent_stop_is_not_recorded(self):
        self.send(at(2026, 10, 20, 10, 0), "UserPromptSubmit")
        self.send(at(2026, 10, 20, 10, 1), "SubagentStop", agent_id="x", agent_type="")
        with sqlite3.connect(self.history.path) as conn:
            events = [r[0] for r in conn.execute("SELECT event FROM events")]
        self.assertEqual(events, ["UserPromptSubmit"])


class SessionListTest(HistoryTestCase):
    def test_sessions_and_detail_timeline(self):
        self.basic_session()
        [s] = self.history.sessions(at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0))
        self.assertEqual(s["durationMs"], 10 * MIN)
        self.assertEqual((s["busyMs"], s["waitMs"]), (3 * MIN, 2 * MIN))
        self.assertEqual((s["prompts"], s["toolCalls"]), (1, 1))
        detail = self.history.session_detail("s1")
        states = [(seg["state"], seg["end"] - seg["start"]) for seg in detail["segments"]]
        self.assertEqual(
            states,
            [
                ("START", 1 * MIN),
                ("BUSY", 2 * MIN),
                ("WAIT", 2 * MIN),
                ("BUSY", 1 * MIN),
                ("IDLE", 4 * MIN),
                ("OFF", 0),
            ],
        )
        self.assertIsNone(self.history.session_detail("missing"))

    def test_sessions_outside_range_are_excluded(self):
        self.basic_session()
        self.assertEqual(self.history.sessions(at(2026, 10, 21, 0, 0), at(2026, 10, 22, 0, 0)), [])

    def test_csv_export(self):
        self.send(at(2026, 10, 20, 10, 0), "SessionStart", session_title="名前, 付き")
        self.send(at(2026, 10, 20, 10, 1), "UserPromptSubmit", session_title="名前, 付き")
        text = self.history.sessions_csv(at(2026, 10, 20, 0, 0), at(2026, 10, 21, 0, 0))
        self.assertTrue(text.startswith("﻿session_id,project,title"))
        self.assertIn('"名前, 付き"', text)


class DayRangeTest(unittest.TestCase):
    def setUp(self):
        self._old_tz = os.environ.get("TZ")
        os.environ["TZ"] = TZ
        time.tzset()

    def tearDown(self):
        if self._old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._old_tz
        time.tzset()

    def test_day_range(self):
        now = at(2026, 10, 20, 15, 30)
        self.assertEqual(history.day_range(1, now), (at(2026, 10, 20, 0, 0), now))
        self.assertEqual(history.day_range(7, now), (at(2026, 10, 14, 0, 0), now))
        self.assertEqual(history.day_range(0, now), (at(2026, 10, 20, 0, 0), now))

    def test_date_range(self):
        now = at(2026, 10, 20, 15, 30)
        self.assertEqual(
            history.date_range("2026-10-01", "2026-10-03", now),
            (at(2026, 10, 1, 0, 0), at(2026, 10, 4, 0, 0)),
        )
        # A range that includes today is cut at the current time, like day_range
        self.assertEqual(
            history.date_range("2026-10-14", "2026-10-20", now), history.day_range(7, now)
        )
        # A range of only future dates is empty
        start, end = history.date_range("2026-11-01", "2026-11-02", now)
        self.assertEqual(start, end)
        for first, last in [
            ("2026-10-05", "2026-10-04"),
            ("2026/10/01", "2026-10-02"),
            ("", "2026-10-02"),
            ("2026-10-01", "2026-10-02x"),
        ]:
            with self.assertRaises(ValueError):
                history.date_range(first, last, now)
        with self.assertRaises(ValueError):
            history.date_range("2020-01-01", "2026-10-20", now, max_days=365)


class HistoryApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.history = history.HistoryStore(os.path.join(cls.tmp.name, "h.sqlite3"))
        cls.httpd = server.StatusServer(
            ("127.0.0.1", 0), server.SessionStore(), history=cls.history, log_events=False
        )
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        for name in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"):
            cls.post(
                {"session_id": "api-1", "cwd": "/w/p", "hook_event_name": name, "tool_name": "Read"}
            )

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutting_down.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.history.close()
        cls.tmp.cleanup()

    @classmethod
    def post(cls, payload):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        conn.request(
            "POST", "/hook", body=json.dumps(payload), headers={"Content-Type": "application/json"}
        )
        conn.getresponse().read()
        conn.close()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def test_summary(self):
        status, ctype, body = self.get("/api/history/summary?days=7")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["days"], 7)
        self.assertEqual(data["totals"]["prompts"], 1)
        self.assertEqual(len(data["daily"]), 7)

    def test_sessions_and_detail(self):
        status, _, body = self.get("/api/history/sessions?days=1")
        self.assertEqual(status, 200)
        [s] = json.loads(body)["sessions"]
        self.assertEqual(s["id"], "api-1")
        status, _, body = self.get("/api/history/sessions/api-1")
        self.assertEqual(status, 200)
        self.assertIn("segments", json.loads(body))
        status, _, _ = self.get("/api/history/sessions/nope")
        self.assertEqual(status, 404)

    def test_csv(self):
        status, ctype, body = self.get("/api/history/sessions.csv?days=30")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", ctype)
        self.assertIn(b"api-1", body)

    def test_bad_params_are_clamped(self):
        status, _, body = self.get("/api/history/summary?days=abc")
        self.assertEqual(json.loads(body)["days"], 7)
        status, _, body = self.get("/api/history/summary?days=-5")
        self.assertEqual(json.loads(body)["days"], 1)
        status, _, _ = self.get("/api/history/unknown")
        self.assertEqual(status, 404)

    def test_date_range_params(self):
        today = history.local_date(history.now_ms())
        status, _, body = self.get(f"/api/history/summary?from=2020-01-01&to={today}")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual((data["from"], data["to"]), ("2020-01-01", today))
        self.assertEqual(data["totals"]["prompts"], 1)
        status, _, body = self.get("/api/history/sessions?from=2020-01-01&to=2020-01-31")
        self.assertEqual(json.loads(body)["sessions"], [])
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/api/history/sessions.csv?from={today}&to={today}")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertIn(f"claude-sessions-{today}_{today}.csv", resp.getheader("Content-Disposition"))
        for query in ["from=2026-10-05&to=2026-10-04", "from=abc&to=2026-10-04", "to=2026-10-04"]:
            status, _, _ = self.get(f"/api/history/summary?{query}")
            self.assertEqual(status, 400, query)


class HistoryDisabledTest(unittest.TestCase):
    def test_returns_404_when_disabled(self):
        httpd = server.StatusServer(("127.0.0.1", 0), server.SessionStore(), log_events=False)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
            conn.request("GET", "/api/history/summary")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            self.assertIn("disabled", json.loads(resp.read())["error"])
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
