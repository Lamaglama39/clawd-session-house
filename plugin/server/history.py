"""Local SQLite history of Claude Code sessions for the review page.

The status server calls :meth:`HistoryStore.record` for every hook event it
accepts. Only metadata is stored (session ids, folder names, titles, event
names, states, tool names, subagent types and descriptions, task subjects);
prompt text and tool inputs are never written.

Aggregations are computed in Python from the event rows: the state a session
was in between two consecutive events is treated as one interval, capped at
``MAX_INTERVAL_MS``.
"""

import csv
import io
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 180

# Segments of one state are cut off at this length, so a state left behind without a hook
# (for example after an Esc interrupt) cannot inflate busy and approval times by hours
MAX_INTERVAL_MS = 30 * 60 * 1000

MAX_DETAIL_CHARS = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    cwd TEXT NOT NULL DEFAULT '',
    title TEXT,
    started_at INTEGER NOT NULL,
    last_event_at INTEGER NOT NULL,
    ended_at INTEGER,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    event TEXT NOT NULL,
    state TEXT NOT NULL,
    tool TEXT,
    agent_id TEXT,
    agent_type TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_session_ts ON events (session_id, ts);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS subagents (
    session_id TEXT NOT NULL,
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    tool_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, id)
);
CREATE INDEX IF NOT EXISTS subagents_started ON subagents (started_at);
"""


def now_ms():
    """Return the current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


def local_date(ms):
    """Return the local calendar date of an epoch-millisecond timestamp as YYYY-MM-DD."""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def local_hour(ms):
    """Return the local hour (0-23) of an epoch-millisecond timestamp."""
    return datetime.fromtimestamp(ms / 1000).hour


def day_range(days, now):
    """Return (start_ms, end_ms) covering the last ``days`` local calendar days up to now.

    ``days=1`` means "today" (from local midnight).
    """
    days = max(1, int(days))
    today = datetime.fromtimestamp(now / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    return int(start.timestamp() * 1000), now


def date_range(first, last, now, max_days=3650):
    """Return (start_ms, end_ms) covering local calendar days ``first``..``last`` (YYYY-MM-DD).

    The end is capped at ``now`` so that a range ending today behaves like ``day_range``.

    Raises:
        ValueError: If a date is malformed, ``last`` is before ``first``, or the span exceeds
            ``max_days``.
    """
    start = datetime.strptime(first, "%Y-%m-%d")
    end = datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
    if not 0 < (end - start).days <= max_days:
        raise ValueError(f"invalid date range: {first}..{last}")
    start_ms = int(start.timestamp() * 1000)
    return start_ms, max(start_ms, min(int(end.timestamp() * 1000), now))


def _detail(payload, event):
    """Pick a short, non-sensitive detail string worth keeping for an event."""
    if event == "Notification":
        value = payload.get("notification_type")
    elif event == "SessionStart":
        value = payload.get("source")
    elif event == "SessionEnd":
        value = payload.get("reason")
    elif event in ("TaskCreated", "TaskCompleted"):
        value = payload.get("task_subject")
    elif event == "StopFailure":
        value = payload.get("error")
    else:
        value = None
    if not isinstance(value, str):
        return None
    return value.strip()[:MAX_DETAIL_CHARS] or None


class HistoryStore:
    """SQLite-backed event history.

    Writes go through one connection and reads through another so that a slow
    aggregation never blocks hook intake (the database uses WAL mode).

    Args:
        path: Database file path. Parent directories are created.
        retention_days: Rows older than this many days are purged.
        clock: Callable returning epoch milliseconds (injectable for tests).
    """

    def __init__(self, path, retention_days=DEFAULT_RETENTION_DAYS, clock=now_ms):
        self.path = path
        self.retention_days = retention_days
        self._clock = clock
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self._write_lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._writer = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._writer.execute("PRAGMA journal_mode=WAL")
        self._writer.execute("PRAGMA synchronous=NORMAL")
        self._writer.executescript(SCHEMA)
        self._writer.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._reader = sqlite3.connect(path, check_same_thread=False)
        self._reader.row_factory = sqlite3.Row

    def close(self):
        """Close both database connections."""
        with self._write_lock:
            self._writer.close()
        with self._read_lock:
            self._reader.close()

    # --- Writes ---

    def record(self, payload, session):
        """Store one hook event.

        Args:
            payload: The hook input payload.
            session: The session as it is after the event (from SessionStore.apply_event).
        """
        event = payload.get("hook_event_name")
        ts = session["updatedAt"]
        agent_id = payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None
        agent_type = payload.get("agent_type")
        agent_type = agent_type if isinstance(agent_type, str) and agent_type else None
        tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else None
        ended = event == "SessionEnd"
        end_reason = _detail(payload, event) if ended else None

        with self._write_lock:
            cur = self._writer.cursor()
            cur.execute("BEGIN")
            try:
                cur.execute(
                    """
                    INSERT INTO sessions
                        (id, project, cwd, title, started_at, last_event_at, ended_at, end_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        project = excluded.project,
                        cwd = CASE WHEN sessions.cwd = '' THEN excluded.cwd ELSE sessions.cwd END,
                        title = COALESCE(excluded.title, sessions.title),
                        last_event_at = excluded.last_event_at,
                        ended_at = excluded.ended_at,
                        end_reason = excluded.end_reason
                    """,
                    (
                        session["id"],
                        session["project"],
                        session["cwd"],
                        session.get("title"),
                        session["startedAt"],
                        ts,
                        ts if ended else None,
                        end_reason,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO events
                        (session_id, ts, event, state, tool, agent_id, agent_type, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["id"],
                        ts,
                        event,
                        session["state"],
                        tool,
                        agent_id,
                        agent_type,
                        _detail(payload, event),
                    ),
                )
                self._record_subagent(cur, payload, session, event, ts, agent_id, tool)
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise

    @staticmethod
    def _record_subagent(cur, payload, session, event, ts, agent_id, tool):
        if event == "SubagentStart" and agent_id:
            agent = next((a for a in session.get("agents", []) if a["id"] == agent_id), None)
            cur.execute(
                """
                INSERT INTO subagents (session_id, id, type, description, started_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (session_id, id) DO UPDATE SET ended_at = NULL
                """,
                (
                    session["id"],
                    agent_id,
                    (agent or {}).get("type") or payload.get("agent_type") or "agent",
                    (agent or {}).get("description"),
                    ts,
                ),
            )
        elif event == "SubagentStop" and agent_id:
            cur.execute(
                "UPDATE subagents SET ended_at = ? WHERE session_id = ? AND id = ?",
                (ts, session["id"], agent_id),
            )
        elif event == "PreToolUse" and agent_id:
            cur.execute(
                "UPDATE subagents SET tool_count = tool_count + 1 WHERE session_id = ? AND id = ?",
                (session["id"], agent_id),
            )
        elif event == "PostToolUse" and agent_id is None and tool == "Agent":
            # In the foreground the description may arrive after SubagentStart, so fill it in here
            response = payload.get("tool_response")
            launched = response.get("agentId") if isinstance(response, dict) else None
            tool_input = payload.get("tool_input")
            description = tool_input.get("description") if isinstance(tool_input, dict) else None
            if isinstance(launched, str) and isinstance(description, str):
                cur.execute(
                    """
                    UPDATE subagents SET description = ?
                    WHERE session_id = ? AND id = ? AND description IS NULL
                    """,
                    (description.strip()[:MAX_DETAIL_CHARS], session["id"], launched),
                )

    def purge(self):
        """Delete rows older than the retention period. Returns the number of events removed."""
        cutoff = self._clock() - self.retention_days * 86_400_000
        with self._write_lock:
            cur = self._writer.cursor()
            cur.execute("BEGIN")
            try:
                removed = cur.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
                cur.execute("DELETE FROM subagents WHERE started_at < ?", (cutoff,))
                cur.execute("DELETE FROM sessions WHERE last_event_at < ?", (cutoff,))
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
        return removed

    # --- Aggregation ---

    def _query(self, sql, params=()):
        with self._read_lock:
            return [dict(r) for r in self._reader.execute(sql, params).fetchall()]

    @staticmethod
    def _intervals(events, sessions, end_ms, now):
        """Split each session's event rows into capped state intervals.

        The state recorded with an event lasts until the next event of the same
        session. Each interval is capped at ``MAX_INTERVAL_MS``.

        Args:
            events: Event rows ordered by session_id, ts.
            sessions: Mapping session id -> session row.
            end_ms: Upper bound for the still-open last interval of a live session.
            now: Current time.

        Returns:
            Mapping session id -> list of (state, start_ms, duration_ms).
        """
        by_session = {}
        for e in events:
            by_session.setdefault(e["session_id"], []).append(e)
        result = {}
        for sid, rows in by_session.items():
            ended = (sessions.get(sid) or {}).get("ended_at") is not None
            intervals = []
            for i, row in enumerate(rows):
                if i + 1 < len(rows):
                    next_ts = rows[i + 1]["ts"]
                elif ended or row["state"] == "OFF":
                    next_ts = row["ts"]
                else:
                    # An ongoing session's last state counts up to now (or the end of the period)
                    next_ts = max(row["ts"], min(now, end_ms))
                intervals.append(
                    (row["state"], row["ts"], min(next_ts - row["ts"], MAX_INTERVAL_MS))
                )
            result[sid] = intervals
        return result

    @staticmethod
    def _merge(intervals):
        """Merge back-to-back intervals of the same state into timeline segments.

        Intervals that were cut by the cap are not contiguous, so they stay separate
        and the timeline shows the gap.
        """
        segments = []
        for state, start, ms in intervals:
            end = start + ms
            last = segments[-1] if segments else None
            if last and last["state"] == state and last["end"] == start:
                last["end"] = end
                last["ms"] += ms
            else:
                segments.append({"state": state, "start": start, "end": end, "ms": ms})
        return segments

    def _load(self, start_ms, end_ms, session_ids=None):
        """Load sessions and their events overlapping [start_ms, end_ms)."""
        if session_ids is None:
            sessions = self._query(
                """
                SELECT * FROM sessions
                WHERE id IN (SELECT DISTINCT session_id FROM events WHERE ts >= ? AND ts < ?)
                """,
                (start_ms, end_ms),
            )
        else:
            marks = ",".join("?" * len(session_ids))
            sessions = self._query(f"SELECT * FROM sessions WHERE id IN ({marks})", session_ids)
        sessions = {s["id"]: s for s in sessions}
        if not sessions:
            return sessions, []
        marks = ",".join("?" * len(sessions))
        # Read one maximum segment length earlier, so segments started just before the period count
        events = self._query(
            f"""
            SELECT session_id, ts, event, state, tool, agent_id, agent_type, detail FROM events
            WHERE session_id IN ({marks}) AND ts >= ? AND ts < ?
            ORDER BY session_id, ts, id
            """,
            (*sessions, start_ms - MAX_INTERVAL_MS, end_ms),
        )
        return sessions, events

    def summary(self, start_ms, end_ms):
        """Aggregate activity in [start_ms, end_ms) for the review page."""
        now = self._clock()
        sessions, events = self._load(start_ms, end_ms)
        intervals = self._intervals(events, sessions, end_ms, now)

        days = []
        day = datetime.fromtimestamp(start_ms / 1000).replace(hour=0, minute=0, second=0)
        last_day = local_date(max(start_ms, end_ms - 1))
        while True:
            key = day.strftime("%Y-%m-%d")
            days.append(key)
            if key >= last_day:
                break
            day += timedelta(days=1)
        daily = {
            d: {"date": d, "busyMs": 0, "waitMs": 0, "prompts": 0, "sessions": 0} for d in days
        }
        hourly = [{"hour": h, "busyMs": 0} for h in range(24)]
        projects = {}
        totals = {
            "sessions": 0,
            "prompts": 0,
            "busyMs": 0,
            "waitMs": 0,
            "waitCount": 0,
            "avgWaitMs": 0,
            "toolCalls": 0,
            "approvals": 0,
            "subagents": 0,
            "apiErrors": 0,
        }

        def project_of(sid):
            name = (sessions.get(sid) or {}).get("project") or "(unknown)"
            if name not in projects:
                projects[name] = {
                    "project": name,
                    "sessions": 0,
                    "prompts": 0,
                    "busyMs": 0,
                    "waitMs": 0,
                    "toolCalls": 0,
                }
            return projects[name]

        for sid, items in intervals.items():
            for state, seg_start, ms in items:
                if state not in ("BUSY", "WAIT"):
                    continue
                # Clip the parts outside the period before counting
                start = max(seg_start, start_ms)
                end = min(seg_start + ms, end_ms)
                if end <= start:
                    continue
                part = end - start
                key = "busyMs" if state == "BUSY" else "waitMs"
                totals[key] += part
                project_of(sid)[key] += part
                if local_date(start) in daily:
                    daily[local_date(start)][key] += part
                if state == "BUSY":
                    hourly[local_hour(start)]["busyMs"] += part
            # Count consecutive approval waits as one, and only those that began within the period
            for seg in self._merge(items):
                if seg["state"] == "WAIT" and start_ms <= seg["start"] < end_ms:
                    totals["waitCount"] += 1

        tools = {}
        active_days = {}
        for e in events:
            if not start_ms <= e["ts"] < end_ms:
                continue
            active_days.setdefault(e["session_id"], set()).add(local_date(e["ts"]))
            name = e["event"]
            if name == "UserPromptSubmit":
                totals["prompts"] += 1
                project_of(e["session_id"])["prompts"] += 1
                if local_date(e["ts"]) in daily:
                    daily[local_date(e["ts"])]["prompts"] += 1
            elif name == "PreToolUse":
                totals["toolCalls"] += 1
                project_of(e["session_id"])["toolCalls"] += 1
                key = e["tool"] or "(unknown)"
                tools[key] = tools.get(key, 0) + 1
            elif name == "PermissionRequest":
                totals["approvals"] += 1
            elif name == "StopFailure":
                totals["apiErrors"] += 1

        for sid, dates in active_days.items():
            totals["sessions"] += 1
            project_of(sid)["sessions"] += 1
            for d in dates:
                if d in daily:
                    daily[d]["sessions"] += 1

        agents = []
        for row in self._query(
            """
            SELECT type, COUNT(*) AS count,
                   AVG(CASE WHEN ended_at IS NOT NULL THEN ended_at - started_at END) AS avg_ms
            FROM subagents WHERE started_at >= ? AND started_at < ?
            GROUP BY type ORDER BY count DESC, type
            """,
            (start_ms, end_ms),
        ):
            agents.append(
                {
                    "type": row["type"],
                    "count": row["count"],
                    "avgDurationMs": int(row["avg_ms"]) if row["avg_ms"] is not None else None,
                }
            )
            totals["subagents"] += row["count"]

        if totals["waitCount"]:
            totals["avgWaitMs"] = totals["waitMs"] // totals["waitCount"]

        return {
            "range": {"start": start_ms, "end": end_ms},
            "totals": totals,
            "daily": [daily[d] for d in days],
            "hourly": hourly,
            "projects": sorted(
                projects.values(), key=lambda p: (-p["busyMs"], -p["prompts"], p["project"])
            ),
            "tools": _ranked(tools, "tool"),
            "agents": agents,
        }

    def _session_stats(self, rows, now):
        """Compute whole-session statistics for the given session rows."""
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        sessions, events = self._load(0, now + 1, session_ids=ids)
        intervals = self._intervals(events, sessions, now + 1, now)
        counts = {sid: {"prompts": 0, "toolCalls": 0} for sid in ids}
        for e in events:
            if e["event"] == "UserPromptSubmit":
                counts[e["session_id"]]["prompts"] += 1
            elif e["event"] == "PreToolUse":
                counts[e["session_id"]]["toolCalls"] += 1
        agent_counts = {
            r["session_id"]: r["count"]
            for r in self._query(
                f"""
                SELECT session_id, COUNT(*) AS count FROM subagents
                WHERE session_id IN ({",".join("?" * len(ids))}) GROUP BY session_id
                """,
                ids,
            )
        }
        result = []
        for r in rows:
            items = intervals.get(r["id"], [])
            end = r["ended_at"] or r["last_event_at"]
            result.append(
                {
                    "id": r["id"],
                    "project": r["project"],
                    "cwd": r["cwd"],
                    "title": r["title"],
                    "startedAt": r["started_at"],
                    "lastEventAt": r["last_event_at"],
                    "endedAt": r["ended_at"],
                    "endReason": r["end_reason"],
                    "durationMs": max(0, end - r["started_at"]),
                    "busyMs": sum(ms for state, _, ms in items if state == "BUSY"),
                    "waitMs": sum(ms for state, _, ms in items if state == "WAIT"),
                    "prompts": counts[r["id"]]["prompts"],
                    "toolCalls": counts[r["id"]]["toolCalls"],
                    "subagents": agent_counts.get(r["id"], 0),
                }
            )
        return result

    def sessions(self, start_ms, end_ms, limit=200):
        """List sessions active in [start_ms, end_ms), newest first, with whole-session stats."""
        rows = self._query(
            """
            SELECT * FROM sessions
            WHERE id IN (SELECT DISTINCT session_id FROM events WHERE ts >= ? AND ts < ?)
            ORDER BY last_event_at DESC LIMIT ?
            """,
            (start_ms, end_ms, int(limit)),
        )
        return self._session_stats(rows, self._clock())

    def session_detail(self, session_id):
        """Return one session with its state timeline, subagents, tools, and tasks, or None."""
        now = self._clock()
        rows = self._query("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not rows:
            return None
        sessions, events = self._load(0, now + 1, session_ids=[session_id])
        items = self._intervals(events, sessions, now + 1, now).get(session_id, [])
        tools = {}
        tasks = []
        for e in events:
            if e["event"] == "PreToolUse":
                key = e["tool"] or "(unknown)"
                tools[key] = tools.get(key, 0) + 1
            elif e["event"] == "TaskCompleted" and e["detail"]:
                tasks.append({"subject": e["detail"], "completedAt": e["ts"]})
        subagents = self._query(
            """
            SELECT id, type, description, started_at AS startedAt, ended_at AS endedAt,
                   tool_count AS toolCount
            FROM subagents WHERE session_id = ? ORDER BY started_at
            """,
            (session_id,),
        )
        return {
            "session": self._session_stats(rows, now)[0],
            "segments": [
                {"state": s["state"], "start": s["start"], "end": s["end"]}
                for s in self._merge(items)
            ],
            "subagents": subagents,
            "tools": _ranked(tools, "tool"),
            "tasks": tasks,
        }

    def sessions_csv(self, start_ms, end_ms):
        """Return the session list for [start_ms, end_ms) as CSV text (UTF-8 with BOM for Excel)."""
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "session_id",
                "project",
                "title",
                "cwd",
                "started_at",
                "ended_at",
                "duration_min",
                "busy_min",
                "wait_min",
                "prompts",
                "tool_calls",
                "subagents",
            ]
        )

        def fmt(ms):
            return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds") if ms else ""

        for s in self.sessions(start_ms, end_ms, limit=100_000):
            writer.writerow(
                [
                    s["id"],
                    s["project"],
                    s["title"] or "",
                    s["cwd"],
                    fmt(s["startedAt"]),
                    fmt(s["endedAt"]),
                    round(s["durationMs"] / 60000, 1),
                    round(s["busyMs"] / 60000, 1),
                    round(s["waitMs"] / 60000, 1),
                    s["prompts"],
                    s["toolCalls"],
                    s["subagents"],
                ]
            )
        # Prepend a BOM so Excel does not garble the text
        return "﻿" + out.getvalue()


def _ranked(counts, key):
    """Turn {name: count} into a list sorted by count desc, then name."""
    return [{key: k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
