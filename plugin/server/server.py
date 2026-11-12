#!/usr/bin/env python3
"""Multi-session status server for Claude Code.

Receives Claude Code hook events over HTTP (``POST /hook``), keeps the state of
every session, and serves a browser dashboard (``GET /``) that renders one
animated Clawd per session. State changes are pushed to the dashboard with
Server-Sent Events (``GET /events``).

Every accepted event is also written to a local SQLite history (see history.py)
that the review page (``/history.html``) reads through ``/api/history/*``.

Only the Python standard library is used.

Usage:
    python3 plugin/server/server.py                 # http://127.0.0.1:8765/
    python3 plugin/server/server.py --port 9000
    python3 plugin/server/server.py --host 0.0.0.0  # allow other devices on the LAN to view it
    python3 plugin/server/server.py --db /path/to/history.sqlite3 --history-days 90
    python3 plugin/server/server.py --no-history    # dashboard only, record nothing
"""

import argparse
import copy
import json
import mimetypes
import os
import signal
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from history import DEFAULT_RETENTION_DAYS, HistoryStore, date_range, day_range, now_ms

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
WEB_DIR = os.path.join(ROOT_DIR, "web")
# Under the plugin, store data in the plugin data directory, which survives updates.
# A manual install keeps data/ at the repository root, one level above the plugin directory
DEFAULT_DB = os.path.join(
    os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(os.path.dirname(ROOT_DIR), "data"),
    "history.sqlite3",
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Keep the state words to five (the dashboard maps them to Clawd's scenes)
STATES = ("START", "IDLE", "BUSY", "WAIT", "OFF")

# Hook event name -> state word. Other events keep the state and only update the last event
EVENT_STATES = {
    "SessionStart": "START",
    "UserPromptSubmit": "BUSY",
    "PreToolUse": "BUSY",
    "PermissionRequest": "WAIT",
    # Automatic denial in auto mode. Claude keeps working
    "PermissionDenied": "BUSY",
    "PostToolUse": "BUSY",
    "PostToolUseFailure": "BUSY",
    "Elicitation": "WAIT",
    "ElicitationResult": "BUSY",
    "Stop": "IDLE",
    # Sent instead of Stop when the turn ends with an API error
    "StopFailure": "IDLE",
    "SessionEnd": "OFF",
}

# Notification decides the state by notification_type
NOTIFICATION_STATES = {
    "permission_prompt": "WAIT",
    "elicitation_dialog": "WAIT",
    "elicitation_url_dialog": "WAIT",
    # No hook follows a manual denial or an Esc interrupt; idle_prompt ~60 s later returns to idle
    "idle_prompt": "IDLE",
}

# Events that keep the tool name shown. Any other state change clears it
TOOL_EVENTS = {
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
}

# Events that carry session_title (the name set with --name or /rename).
# In these two a missing field means "no name"; other events never carry the field
TITLE_EVENTS = {"SessionStart", "UserPromptSubmit"}
MAX_TITLE_CHARS = 200

# Tool names that launch a subagent
AGENT_TOOL = "Agent"
# How long a finished subagent stays on screen, asleep
DONE_AGENT_LINGER_MS = 20_000
MAX_AGENTS_PER_SESSION = 20
# Maximum number of Agent tool descriptions kept until the matching SubagentStart arrives
MAX_AGENT_HINTS = 20
MAX_DESCRIPTION_CHARS = 120
MAX_TASKS_PER_SESSION = 200

# PermissionRequest's tool_input can hold large data such as a Write body, so the limit is generous
MAX_BODY_BYTES = 16 * 1024 * 1024
SSE_HEARTBEAT_SEC = 15.0
PRUNE_INTERVAL_SEC = 5.0
HISTORY_PURGE_SEC = 3600.0


def project_name(cwd):
    """Return the last path component of cwd as a display name."""
    if not cwd:
        return "(unknown)"
    name = os.path.basename(os.path.normpath(cwd))
    return name or cwd


def resolve_state(payload):
    """Map a hook input payload to a state word.

    Returns:
        One of STATES, or None when the event should not change the state.
    """
    event = payload.get("hook_event_name")
    if event == "Notification":
        return NOTIFICATION_STATES.get(payload.get("notification_type"))
    # A SessionStart after auto-compaction continues the work, so skip the wake-up scene
    if event == "SessionStart" and payload.get("source") == "compact":
        return None
    return EVENT_STATES.get(event)


def clip_text(value, limit):
    """Return value stripped and truncated to limit characters, or None if empty."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def session_title(payload):
    """Return the custom session title in payload, or None if there is none."""
    return clip_text(payload.get("session_title"), MAX_TITLE_CHARS)


def agent_id_of(payload):
    """Return the subagent id the event fired in, or None for the main agent."""
    agent_id = payload.get("agent_id")
    return agent_id if isinstance(agent_id, str) and agent_id else None


class SessionStore:
    """Thread-safe store of per-session status.

    Args:
        ended_linger_ms: How long a session stays visible after SessionEnd.
        expire_ms: Remove sessions that have sent no event for this long.
        clock: Callable returning epoch milliseconds (injectable for tests).
    """

    def __init__(self, ended_linger_ms=60_000, expire_ms=12 * 3600 * 1000, clock=now_ms):
        self._cond = threading.Condition()
        self._sessions = {}
        # Helper data never shown (pending Agent descriptions, the subagent that waits, etc.)
        self._private = {}
        self._version = 0
        self._ended_linger_ms = ended_linger_ms
        self._expire_ms = expire_ms
        self._clock = clock

    @property
    def version(self):
        """Monotonic counter incremented on every visible change."""
        with self._cond:
            return self._version

    def _bump(self):
        self._version += 1
        self._cond.notify_all()

    def apply_event(self, payload):
        """Apply one hook input payload.

        Args:
            payload: The JSON object Claude Code passes to hooks.

        Returns:
            A deep copy of the updated session, or None if the payload was ignored.
        """
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id")
        event = payload.get("hook_event_name")
        if not isinstance(session_id, str) or not session_id or not isinstance(event, str):
            return None
        # Claude Code's internal agents (e.g. prompt suggestions) have no agent_type; ignore them
        if event in ("SubagentStart", "SubagentStop") and not payload.get("agent_type"):
            return None

        now = self._clock()
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
        agent_id = agent_id_of(payload)

        with self._cond:
            session = self._sessions.get(session_id)
            private = self._private.setdefault(session_id, _new_private())
            if session is None:
                session = _new_session(session_id, cwd, now)
                self._sessions[session_id] = session

            new_state = resolve_state(payload)
            if agent_id is not None and event not in ("SubagentStart", "SubagentStop"):
                new_state = self._subagent_state(session, private, agent_id, new_state)
            elif agent_id is not None:
                new_state = self._subagent_lifecycle_state(session, private, event, agent_id)
            elif new_state is not None and new_state != "WAIT":
                # A parent Notification during a subagent's approval wait keeps who is waiting
                private["waitingAgent"] = None
                private.pop("resumeState", None)

            changed = False
            if new_state and (new_state != session["state"] or new_state == "START"):
                # Replay the wake-up scene when a session with the same ID resumes, e.g. via /resume
                session["state"] = new_state
                session["since"] = now
                changed = True

            if cwd and not session["cwd"]:
                session["cwd"] = cwd
                session["project"] = project_name(cwd)

            # Events that keep the state, such as an approval-wait Notification, keep the tool name.
            # Tools inside a subagent are shown on the subagent, not as the parent's tool name
            tool_name = payload.get("tool_name")
            if agent_id is None and event in TOOL_EVENTS and isinstance(tool_name, str):
                session["tool"] = tool_name
            elif changed and agent_id is None:
                session["tool"] = None

            mode = payload.get("permission_mode")
            if isinstance(mode, str):
                session["mode"] = mode

            if event in TITLE_EVENTS:
                session["title"] = session_title(payload)

            self._track_agents(session, private, payload, event, agent_id, now)
            self._track_tasks(session, private, payload, event)
            self._track_background(session, payload, event, agent_id, now)

            session["event"] = event
            session["updatedAt"] = now
            if session["state"] == "OFF":
                session["endedAt"] = session.get("endedAt") or now
                for agent in session["agents"]:
                    if agent["state"] == "running":
                        _finish_agent(agent, now)
                session["background"] = {}
            else:
                session.pop("endedAt", None)

            self._bump()
            return copy.deepcopy(session)

    # --- Subagents ---

    @staticmethod
    def _subagent_state(session, private, agent_id, new_state):
        """Decide the session state for an event fired inside a subagent."""
        # Only when approval is needed does a subagent put the parent into the approval wait
        if new_state == "WAIT":
            if session["state"] != "WAIT":
                private["resumeState"] = session["state"]
            private["waitingAgent"] = agent_id
            return "WAIT"
        # When the subagent that caused the wait moves on, restore the parent's previous state
        if private["waitingAgent"] == agent_id and session["state"] == "WAIT":
            private["waitingAgent"] = None
            return private.pop("resumeState", None) or "BUSY"
        # Other subagent events leave the parent's state alone (the parent may be idle)
        return None

    @staticmethod
    def _subagent_lifecycle_state(session, private, event, agent_id):
        """Decide the session state for SubagentStart / SubagentStop."""
        if event == "SubagentStop" and private["waitingAgent"] == agent_id:
            private["waitingAgent"] = None
            if session["state"] == "WAIT":
                return private.pop("resumeState", None) or "BUSY"
        return None

    @staticmethod
    def _find_agent(session, agent_id):
        for agent in session["agents"]:
            if agent["id"] == agent_id:
                return agent
        return None

    def _track_agents(self, session, private, payload, event, agent_id, now):
        tool_name = payload.get("tool_name")
        tool_input = (
            payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        )
        hints = private["hints"]

        if agent_id is None and tool_name == AGENT_TOOL:
            hint = {
                "type": clip_text(tool_input.get("subagent_type"), MAX_DESCRIPTION_CHARS) or "",
                "description": clip_text(tool_input.get("description"), MAX_DESCRIPTION_CHARS),
            }
            tool_use_id = payload.get("tool_use_id") or f"anon-{now}"
            if event == "PreToolUse":
                hints[tool_use_id] = hint
                while len(hints) > MAX_AGENT_HINTS:
                    hints.pop(next(iter(hints)))
            elif event == "PostToolUse":
                hint = hints.pop(tool_use_id, None) or hint
                response = payload.get("tool_response")
                launched = response.get("agentId") if isinstance(response, dict) else None
                if isinstance(launched, str) and launched:
                    agent = self._find_agent(session, launched)
                    background = (
                        tool_input.get("run_in_background") is True
                        or response.get("status") == "async_launched"
                    )
                    if agent is not None:
                        agent["description"] = agent["description"] or hint["description"]
                        # In the foreground the Agent tool returns after the subagent finishes.
                        # Some types such as web-fetch never send SubagentStop, so finish them here
                        if not background and agent["state"] == "running":
                            _finish_agent(agent, now)
                    else:
                        # In the background this precedes SubagentStart, so keep the description
                        private["known"][launched] = hint
                        while len(private["known"]) > MAX_AGENT_HINTS:
                            private["known"].pop(next(iter(private["known"])))
            elif event == "PostToolUseFailure":
                hints.pop(tool_use_id, None)
            return

        if agent_id is None:
            return

        agent_type = clip_text(payload.get("agent_type"), MAX_DESCRIPTION_CHARS) or ""
        agent = self._find_agent(session, agent_id)
        if event == "SubagentStart" or (agent is None and event != "SubagentStop"):
            if agent is None:
                hint = private["known"].pop(agent_id, None) or _take_hint(hints, agent_type)
                agent = {
                    "id": agent_id,
                    "type": agent_type or "agent",
                    "description": hint["description"] if hint else None,
                    "state": "running",
                    "startedAt": now,
                    "endedAt": None,
                    "tool": None,
                    "toolCount": 0,
                    "waiting": False,
                }
                session["agents"].append(agent)
                _trim_agents(session["agents"])
            else:
                # A resumed subagent, or a teammate's next message
                agent["state"] = "running"
                agent["endedAt"] = None
        if agent is None:
            return

        if event == "SubagentStop":
            _finish_agent(agent, now)
        elif event in TOOL_EVENTS and isinstance(tool_name, str):
            agent["tool"] = tool_name
            agent["waiting"] = event == "PermissionRequest"
            if event == "PreToolUse":
                agent["toolCount"] += 1
        elif event == "Notification" and resolve_state(payload) == "WAIT":
            agent["waiting"] = True

    # --- Tasks and background work ---

    @staticmethod
    def _track_tasks(session, private, payload, event):
        if event not in ("TaskCreated", "TaskCompleted"):
            return
        task_id = payload.get("task_id")
        if not isinstance(task_id, (str, int)) or task_id == "":
            return
        task_id = str(task_id)
        tasks = private["tasks"]
        subject = clip_text(payload.get("task_subject"), MAX_DESCRIPTION_CHARS)
        if task_id not in tasks:
            tasks[task_id] = {"subject": subject, "done": False}
            while len(tasks) > MAX_TASKS_PER_SESSION:
                tasks.pop(next(iter(tasks)))
        elif subject:
            tasks[task_id]["subject"] = subject
        if event == "TaskCompleted":
            tasks[task_id]["done"] = True
        # Show the last unfinished task in creation order as the task in progress
        current = next((t["subject"] for t in reversed(tasks.values()) if not t["done"]), None)
        session["tasks"] = {
            "total": len(tasks),
            "done": sum(1 for t in tasks.values() if t["done"]),
            "current": current,
        }

    @staticmethod
    def _track_background(session, payload, event, agent_id, now):
        if event not in ("Stop", "SubagentStop"):
            return
        items = payload.get("background_tasks")
        if not isinstance(items, list):
            return
        # Keep only counts per type; commands and descriptions are never stored
        counts = {}
        for item in items:
            kind = item.get("type") if isinstance(item, dict) else None
            kind = kind if isinstance(kind, str) and kind else "other"
            counts[kind] = counts.get(kind, 0) + 1
        session["background"] = counts
        if event == "Stop" and agent_id is None:
            # When the parent's response ends, the only running subagents are those in the
            # background list. Finish the rest here, including types that never send SubagentStop
            # (such as web-fetch) and SubagentStop events that never arrived
            alive = {item.get("id") for item in items if isinstance(item, dict)}
            for agent in session["agents"]:
                if agent["state"] == "running" and agent["id"] not in alive:
                    _finish_agent(agent, now)

    # --- Listing and deletion ---

    def remove(self, session_id):
        """Remove a session. Returns True if it existed."""
        with self._cond:
            self._private.pop(session_id, None)
            if self._sessions.pop(session_id, None) is None:
                return False
            self._bump()
            return True

    def prune(self):
        """Drop ended sessions, long-silent sessions, and finished subagents.

        Returns:
            True if anything visible was removed.
        """
        now = self._clock()
        with self._cond:
            stale = [
                sid
                for sid, s in self._sessions.items()
                if (s["state"] == "OFF" and now - s.get("endedAt", now) >= self._ended_linger_ms)
                or now - s["updatedAt"] >= self._expire_ms
            ]
            for sid in stale:
                del self._sessions[sid]
                self._private.pop(sid, None)
            changed = bool(stale)
            for s in self._sessions.values():
                kept = [
                    a
                    for a in s["agents"]
                    if a["state"] != "done" or now - a["endedAt"] < DONE_AGENT_LINGER_MS
                ]
                if len(kept) != len(s["agents"]):
                    s["agents"] = kept
                    changed = True
            if changed:
                self._bump()
            return changed

    def snapshot(self):
        """Return a JSON-serializable copy ordered by session start time."""
        with self._cond:
            # Deep-copy everything so another thread cannot change nested lists while it is sent
            sessions = sorted(
                (copy.deepcopy(s) for s in self._sessions.values()),
                key=lambda s: (s["startedAt"], s["id"]),
            )
            return {"now": self._clock(), "version": self._version, "sessions": sessions}

    def wait_for_change(self, version, timeout):
        """Block until the version differs from version or timeout elapses.

        Returns:
            The current version.
        """
        with self._cond:
            self._cond.wait_for(lambda: self._version != version, timeout=timeout)
            return self._version


def _new_session(session_id, cwd, now):
    return {
        "id": session_id,
        "project": project_name(cwd),
        "cwd": cwd,
        "state": "IDLE",
        "since": now,
        "startedAt": now,
        "tool": None,
        "mode": None,
        "title": None,
        "agents": [],
        "tasks": {"total": 0, "done": 0, "current": None},
        "background": {},
    }


def _new_private():
    return {"hints": {}, "known": {}, "tasks": {}, "waitingAgent": None}


def _take_hint(hints, agent_type):
    """Pop the oldest Agent-tool hint whose subagent_type matches agent_type."""
    for key, hint in hints.items():
        if not hint["type"] or not agent_type or hint["type"] == agent_type:
            return hints.pop(key)
    return None


def _finish_agent(agent, now):
    agent["state"] = "done"
    agent["endedAt"] = now
    agent["tool"] = None
    agent["waiting"] = False


def _trim_agents(agents):
    # Over the limit, drop the oldest finished ones first (running ones are kept)
    while len(agents) > MAX_AGENTS_PER_SESSION:
        index = next((i for i, a in enumerate(agents) if a["state"] == "done"), 0)
        agents.pop(index)


class StatusServer(ThreadingHTTPServer):
    """HTTP server that owns the session store."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, history=None, quiet=True, log_events=True):
        super().__init__(address, StatusHandler)
        self.store = store
        # History store for the review page (HistoryStore). None means nothing is recorded
        self.history = history
        self.quiet = quiet
        self.log_events = log_events
        self.shutting_down = threading.Event()
        self._history_error_logged = False
        host = address[0]
        self.loopback_only = host in ("127.0.0.1", "localhost", "::1")


class StatusHandler(BaseHTTPRequestHandler):
    """Request handler for hook intake, the dashboard, and the event stream."""

    server_version = "ClaudeStatusDashboard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - signature defined by BaseHTTPRequestHandler
        if not self.server.quiet:
            super().log_message(format, *args)

    # --- Shared helpers ---

    def _host_allowed(self):
        # When listening on loopback, check the Host header to block requests via DNS rebinding
        if not self.server.loopback_only:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            hostname = host[1 : host.find("]")] if "]" in host else host
        else:
            hostname = host.split(":", 1)[0]
        return hostname in ("127.0.0.1", "localhost", "::1")

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        # No body headers on 204 (required by HTTP)
        if status != HTTPStatus.NO_CONTENT:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Never let other sites frame the page, so an overlay cannot trick a click on the x button
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _reject(self, status, message):
        # When refusing without reading the body, close the connection so leftovers are not misread
        self.close_connection = True
        self._send(status, message)

    def _guard(self):
        if not self._host_allowed():
            self._reject(HTTPStatus.FORBIDDEN, b"forbidden host\n")
            return False
        return True

    # --- Routing ---

    def do_GET(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
        if not self._guard():
            return
        path = urlsplit(self.path).path
        if path == "/events":
            self._stream_events()
        elif path == "/api/sessions":
            self._send_json(HTTPStatus.OK, self.server.store.snapshot())
        elif path == "/healthz":
            self._send(HTTPStatus.OK, b"ok\n")
        elif path.startswith("/api/history/"):
            self._serve_history(path)
        else:
            self._serve_static(path)

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        path = urlsplit(self.path).path
        if path != "/hook":
            self._reject(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        # Rejecting anything but application/json stops cross-site browser requests at the preflight
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._reject(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, b"content-type must be application/json\n"
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"invalid body length\n")
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(HTTPStatus.BAD_REQUEST, b"invalid json\n")
            return

        session = self.server.store.apply_event(payload)
        if session is not None and self.server.log_events:
            print(
                f"[{time.strftime('%H:%M:%S')}] {session['project']} "
                f"{session['id'][:8]} {session.get('event')} -> {session['state']}",
                flush=True,
            )
        if session is not None and self.server.history is not None:
            self._record_history(payload, session)
        # Reply with an empty body, so even if read as a hook response it never affects permissions
        self._send(HTTPStatus.OK)

    def _record_history(self, payload, session):
        # A history failure never stops the dashboard or the hook response (logged only once)
        try:
            self.server.history.record(payload, session)
        except Exception as e:  # noqa: BLE001 - swallow any failure to record
            if not self.server._history_error_logged:
                self.server._history_error_logged = True
                print(f"history: failed to record an event: {e}", file=sys.stderr, flush=True)

    def do_DELETE(self):  # noqa: N802
        if not self._guard():
            return
        path = urlsplit(self.path).path
        prefix = "/api/sessions/"
        if not path.startswith(prefix) or len(path) == len(prefix):
            self._send(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        session_id = unquote(path[len(prefix) :])
        if self.server.store.remove(session_id):
            self._send(HTTPStatus.NO_CONTENT)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"no such session\n")

    # --- Individual handlers ---

    def _serve_history(self, path):
        history = self.server.history
        if history is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "history is disabled"})
            return
        query = parse_qs(urlsplit(self.path).query)
        now = self.server.store.snapshot()["now"]
        if "from" in query or "to" in query:
            first, last = query.get("from", [""])[0], query.get("to", [""])[0]
            try:
                start_ms, end_ms = date_range(first, last, now)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "from and to must be YYYY-MM-DD with from <= to"},
                )
                return
            period = {"from": first, "to": last}
            label = f"{first}_{last}"
        else:
            days = _int_param(query, "days", 7, 1, 3650)
            start_ms, end_ms = day_range(days, now)
            period = {"days": days}
            label = f"{days}d"
        rest = path[len("/api/history/") :]
        if rest == "summary":
            self._send_json(HTTPStatus.OK, {**period, **history.summary(start_ms, end_ms)})
        elif rest == "sessions":
            limit = _int_param(query, "limit", 200, 1, 1000)
            sessions = history.sessions(start_ms, end_ms, limit=limit)
            self._send_json(HTTPStatus.OK, {**period, "sessions": sessions})
        elif rest == "sessions.csv":
            body = history.sessions_csv(start_ms, end_ms).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="claude-sessions-{label}.csv"',
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif rest.startswith("sessions/") and len(rest) > len("sessions/"):
            detail = history.session_detail(unquote(rest[len("sessions/") :]))
            if detail is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such session"})
            else:
                self._send_json(HTTPStatus.OK, detail)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        full = os.path.realpath(os.path.join(WEB_DIR, rel))
        web_root = os.path.realpath(WEB_DIR)
        if not full.startswith(web_root + os.sep) or not os.path.isfile(full):
            self._send(HTTPStatus.NOT_FOUND, b"not found\n")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "text/javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self._send(HTTPStatus.OK, body, ctype)

    def _stream_events(self):
        store = self.server.store
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # SSE keeps the response open until disconnect, so end the connection with this request
        self.close_connection = True
        version = None
        try:
            while not self.server.shutting_down.is_set():
                if version is None or store.version != version:
                    snap = store.snapshot()
                    version = snap["version"]
                    data = json.dumps(snap, ensure_ascii=False)
                    self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode("utf-8"))
                else:
                    # Send a heartbeat when nothing changes for a while, to detect disconnects
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                store.wait_for_change(version, SSE_HEARTBEAT_SEC)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass


def _int_param(query, name, default, low, high):
    """Read an integer query parameter clamped to [low, high]."""
    try:
        value = int(query.get(name, [default])[0])
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _prune_loop(server):
    last_purge = time.monotonic()
    while not server.shutting_down.wait(PRUNE_INTERVAL_SEC):
        server.store.prune()
        # Purging old history once an hour is enough
        if server.history is not None and time.monotonic() - last_purge >= HISTORY_PURGE_SEC:
            last_purge = time.monotonic()
            try:
                server.history.purge()
            except Exception as e:  # noqa: BLE001
                print(f"history: purge failed: {e}", file=sys.stderr, flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code multi-session status dashboard")
    parser.add_argument(
        "--host",
        default=os.environ.get("CLAUDE_STATUS_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CLAUDE_STATUS_PORT", DEFAULT_PORT)),
        help="listen port (default: %(default)s)",
    )
    parser.add_argument(
        "--ended-linger",
        type=float,
        default=60.0,
        help="seconds an ended session stays on screen (default: %(default)s)",
    )
    parser.add_argument(
        "--expire-hours",
        type=float,
        default=12.0,
        help="remove sessions silent for this many hours (default: %(default)s)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("CLAUDE_STATUS_DB", DEFAULT_DB),
        help="SQLite file for the session history (default: %(default)s)",
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="keep history for this many days (default: %(default)s)",
    )
    parser.add_argument(
        "--no-history", action="store_true", help="do not record the session history"
    )
    parser.add_argument("--verbose", action="store_true", help="log every HTTP request")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    store = SessionStore(
        ended_linger_ms=int(args.ended_linger * 1000),
        expire_ms=int(args.expire_hours * 3600 * 1000),
    )
    history = None
    if not args.no_history:
        try:
            history = HistoryStore(args.db, retention_days=args.history_days)
            history.purge()
        except Exception as e:  # noqa: BLE001
            # Keep working as a dashboard even if history is unavailable
            print(f"history: disabled ({args.db}: {e})", file=sys.stderr, flush=True)
            history = None
    try:
        server = StatusServer(
            (args.host, args.port), store, history=history, quiet=not args.verbose
        )
    except OSError as e:
        sys.exit(f"cannot listen on {args.host}:{args.port}: {e}")

    # Clean up and exit on SIGTERM from stop-server.sh, the same as on Ctrl+C
    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    threading.Thread(target=_prune_loop, args=(server,), daemon=True).start()
    host_for_url = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"listening on http://{host_for_url}:{args.port}/", flush=True)
    print(f"history: {history.path if history else 'off'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutting_down.set()
        server.server_close()
        if history is not None:
            history.close()
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
