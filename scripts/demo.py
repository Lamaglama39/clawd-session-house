#!/usr/bin/env python3
"""Send fake hook events to the status server to preview the dashboard.

Usage:
    python3 scripts/demo.py                # 4 sessions that keep changing state (Ctrl+C to end)
    python3 scripts/demo.py --sessions 9
    python3 scripts/demo.py --static 6     # 6 sessions frozen in mixed states, then exit
    python3 scripts/demo.py --url http://127.0.0.1:9000/hook
    python3 scripts/demo.py --seed-history /tmp/demo-history.sqlite3 --days 30
                                           # write 30 days of fake history for the review page
"""

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_URL = "http://127.0.0.1:8765/hook"

PROJECTS = (
    "clawd-session-house",
    "todo-app",
    "infra-terraform",
    "api-server",
    "web-frontend",
    "data-pipeline",
    "mobile-app",
    "docs-site",
    "infra-terraform",
    "ml-experiments",
    "dotfiles",
    "cli-tools",
)
# Session names paired with PROJECTS by position (None means no name; mixed to check both displays)
TITLES = (
    None,
    "通知機能の追加",
    None,
    "認証APIのリファクタ",
    None,
    "夜間バッチの失敗調査",
    None,
    None,
    "VPCモジュールの分割",
    None,
    None,
    None,
)
TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "Bash", "WebFetch")
SUBAGENTS = (
    ("Explore", "コードベースの調査"),
    ("Plan", "実装方針の検討"),
    ("general-purpose", "テストの追加"),
    ("Explore", "依存関係の確認"),
)
TASK_SUBJECTS = ("要件の整理", "設計", "実装", "テスト", "ドキュメント更新")
# Order of the states laid out by --static (to check every color combination at once)
STATIC_STATES = ("BUSY", "WAIT", "IDLE", "BUSY", "IDLE", "WAIT", "START", "BUSY", "IDLE", "OFF")
# Events that carry session_title from Claude Code
TITLE_EVENTS = ("SessionStart", "UserPromptSubmit")


class HttpSink:
    """Deliver events to a running status server."""

    def __init__(self, url):
        self.url = url

    def __call__(self, payload):
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except (urllib.error.URLError, OSError) as e:
            print(f"send failed ({payload['hook_event_name']}): {e}")


class FakeSession:
    """One fake Claude Code session that posts hook events."""

    def __init__(self, sink, index, mode="default"):
        project = PROJECTS[index % len(PROJECTS)]
        self.sink = sink
        self.session_id = str(uuid.uuid4())
        self.cwd = f"/Users/demo/github/{project}"
        self.mode = mode
        self.title = TITLES[index % len(TITLES)]

    def send(self, event, **fields):
        payload = {
            "session_id": self.session_id,
            "transcript_path": f"/tmp/{self.session_id}.jsonl",
            "cwd": self.cwd,
            "permission_mode": self.mode,
            "hook_event_name": event,
            **fields,
        }
        if self.title and event in TITLE_EVENTS:
            payload["session_title"] = self.title
        self.sink(payload)

    # --- Building-block event sequences ---

    def tool(self, name, agent=None):
        extra = {"agent_id": agent[0], "agent_type": agent[1]} if agent else {}
        self.send("PreToolUse", tool_name=name, tool_input={}, **extra)
        return extra

    def tool_done(self, name, agent=None):
        extra = {"agent_id": agent[0], "agent_type": agent[1]} if agent else {}
        self.send("PostToolUse", tool_name=name, tool_input={}, tool_response={}, **extra)

    def start_agent(self, agent_type, description, background=False):
        """Launch a subagent through the Agent tool. Returns (agent_id, agent_type)."""
        tool_use_id = f"toolu_{uuid.uuid4().hex[:12]}"
        agent_id = uuid.uuid4().hex[:17]
        tool_input = {"subagent_type": agent_type, "description": description, "prompt": "(demo)"}
        self.send("PreToolUse", tool_name="Agent", tool_use_id=tool_use_id, tool_input=tool_input)
        if background:
            # In the background, PostToolUse(Agent) arrives right after the launch
            self.send(
                "PostToolUse",
                tool_name="Agent",
                tool_use_id=tool_use_id,
                tool_input=tool_input,
                tool_response={"agentId": agent_id, "status": "async_launched"},
            )
        self.send("SubagentStart", agent_id=agent_id, agent_type=agent_type)
        return agent_id, agent_type, tool_use_id, tool_input

    def stop_agent(self, launched, foreground=True):
        agent_id, agent_type, tool_use_id, tool_input = launched
        self.send("SubagentStop", agent_id=agent_id, agent_type=agent_type)
        if foreground:
            self.send(
                "PostToolUse",
                tool_name="Agent",
                tool_use_id=tool_use_id,
                tool_input=tool_input,
                tool_response={"agentId": agent_id, "status": "completed"},
            )

    def create_tasks(self, subjects):
        ids = []
        for subject in subjects:
            task_id = uuid.uuid4().hex[:8]
            self.send("TaskCreated", task_id=task_id, task_subject=subject)
            ids.append((task_id, subject))
        return ids

    def reach(self, state, index):
        """Send the minimal event sequence that leaves the session in state."""
        self.send("SessionStart", source="startup")
        if state == "START":
            return
        if state == "OFF":
            self.send("SessionEnd", reason="other")
            return
        self.send("UserPromptSubmit", prompt="(demo)")
        tool = random.choice(TOOLS)
        if index == 0:
            # Two foreground subagents are running, and one has just finished
            done = self.start_agent("general-purpose", "テストの追加")
            self.tool("Edit", done[:2])
            self.stop_agent(done)
            for agent_type, description, inner in (
                ("Explore", "コードベースの調査", "Grep"),
                ("Plan", "実装方針の検討", "Read"),
            ):
                launched = self.start_agent(agent_type, description)
                self.tool(inner, launched[:2])
            return
        elif index == 2:
            # The parent waits for input while a subagent and a shell run in the background
            launched = self.start_agent("Explore", "依存関係の確認", background=True)
            self.tool("Bash", launched[:2])
            self.send(
                "Stop",
                stop_hook_active=False,
                background_tasks=[
                    {"id": "b1", "type": "shell", "status": "running"},
                    {"id": "b2", "type": "subagent", "agent_type": "Explore"},
                ],
            )
            return
        elif index == 3:
            tasks = self.create_tasks(TASK_SUBJECTS)
            for task_id, subject in tasks[:2]:
                self.send("TaskCompleted", task_id=task_id, task_subject=subject)
            self.tool(tool)
            return
        elif index == 5:
            # Waiting for approval inside a subagent
            launched = self.start_agent("general-purpose", "マイグレーションの実行")
            self.send(
                "PermissionRequest",
                tool_name="Bash",
                tool_input={},
                agent_id=launched[0],
                agent_type=launched[1],
            )
            return
        if state == "BUSY":
            self.tool(tool)
        elif state == "WAIT":
            self.tool("Bash")
            self.send("PermissionRequest", tool_name="Bash", tool_input={})
        elif state == "IDLE":
            self.send("Stop", stop_hook_active=False)


def run_turn(session, nap, stop=None):
    """One realistic prompt-to-stop cycle with tools, approvals, subagents, and tasks."""
    session.send("UserPromptSubmit", prompt="(demo)")
    tasks = session.create_tasks(random.sample(TASK_SUBJECTS, 3)) if random.random() < 0.3 else []
    for step in range(random.randint(2, 6)):
        if stop is not None and stop.is_set():
            break
        if random.random() < 0.25:
            agent_type, description = random.choice(SUBAGENTS)
            launched = session.start_agent(agent_type, description)
            for _ in range(random.randint(2, 4)):
                inner = random.choice(TOOLS[:3])
                session.tool(inner, launched[:2])
                nap(0.5, 2)
                session.tool_done(inner, launched[:2])
            session.stop_agent(launched)
        else:
            tool = random.choice(TOOLS)
            session.tool(tool)
            if tool in ("Bash", "Write", "WebFetch") and random.random() < 0.4:
                session.send("PermissionRequest", tool_name=tool, tool_input={})
                nap(3, 8)
            nap(1, 3)
            session.tool_done(tool)
        if step < len(tasks):
            task_id, subject = tasks[step]
            session.send("TaskCompleted", task_id=task_id, task_subject=subject)
        nap(0.5, 2)
    session.send("Stop", stop_hook_active=False)


def run_live(session, stop, speed):
    """Loop through turns until stop is set."""

    def nap(lo, hi):
        stop.wait(random.uniform(lo, hi) / speed)

    session.send("SessionStart", source="startup")
    nap(2, 5)
    while not stop.is_set():
        run_turn(session, nap, stop)
        nap(4, 12)
    session.send("SessionEnd", reason="other")


def seed_history(path, days):
    """Write ``days`` days of fake sessions directly into a history database."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
    import history  # noqa: PLC0415 - only needed when generating fake history
    import server  # noqa: PLC0415

    if os.path.realpath(path) == os.path.realpath(server.DEFAULT_DB):
        sys.exit("refusing to write fake history into the default database; pick another path")

    clock = {"now": 0}

    def tick():
        return clock["now"]

    store = server.SessionStore(clock=tick)
    db = history.HistoryStore(path, retention_days=max(days + 1, 1), clock=tick)

    def sink(payload):
        session = store.apply_event(payload)
        if session is not None:
            db.record(payload, session)

    def nap(lo, hi):
        clock["now"] += int(random.uniform(lo, hi) * 1000)

    today = time.localtime()
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
    created = 0
    for back in range(days - 1, -1, -1):
        day_start = int((midnight - back * 86400) * 1000)
        # Fewer sessions on weekends
        weekday = time.localtime(day_start / 1000).tm_wday
        for _ in range(random.randint(0, 2) if weekday >= 5 else random.randint(1, 4)):
            clock["now"] = day_start + int(random.uniform(9, 19) * 3600 * 1000)
            session = FakeSession(sink, random.randrange(len(PROJECTS)))
            session.send("SessionStart", source="startup")
            for _ in range(random.randint(1, 5)):
                nap(20, 300)
                # History segments run several times longer than real time, for realistic durations
                run_turn(session, lambda lo, hi: nap(lo * 8, hi * 8))
            nap(30, 600)
            session.send("SessionEnd", reason="prompt_input_exit")
            created += 1
    db.close()
    print(f"wrote {created} fake session(s) over {days} day(s) to {path}")


def main():
    parser = argparse.ArgumentParser(description="Preview the dashboard with fake sessions")
    parser.add_argument("--url", default=DEFAULT_URL, help="hook endpoint (default: %(default)s)")
    parser.add_argument("--sessions", type=int, default=4, help="number of live sessions")
    parser.add_argument("--speed", type=float, default=1.0, help="speed multiplier for live mode")
    parser.add_argument(
        "--static",
        type=int,
        metavar="N",
        help="create N sessions in fixed mixed states and exit",
    )
    parser.add_argument(
        "--seed-history",
        metavar="PATH",
        help="write fake history into this SQLite file (never the default database) and exit",
    )
    parser.add_argument("--days", type=int, default=30, help="days of fake history to write")
    parser.add_argument("--seed", type=int, help="random seed for reproducible demos")
    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    if args.seed_history:
        seed_history(args.seed_history, max(1, args.days))
        return

    sink = HttpSink(args.url)
    if args.static:
        for i in range(args.static):
            mode = "plan" if i == 4 else "default"
            FakeSession(sink, i, mode).reach(STATIC_STATES[i % len(STATIC_STATES)], i)
            # Tiles are ordered by start time, so leave a short gap to keep the order stable
            time.sleep(0.02)
        print(f"created {args.static} static session(s)")
        return

    stop = threading.Event()
    sessions = [FakeSession(sink, i) for i in range(max(1, args.sessions))]
    threads = [
        threading.Thread(target=run_live, args=(s, stop, args.speed), daemon=True) for s in sessions
    ]
    for t in threads:
        t.start()
        time.sleep(0.3)
    print(f"running {len(sessions)} fake session(s). Ctrl+C to end them.")
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(timeout=3)


if __name__ == "__main__":
    main()
