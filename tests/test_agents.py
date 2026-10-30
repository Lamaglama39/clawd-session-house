"""Tests for subagent, task, and background-work tracking in server.SessionStore."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

import server  # noqa: E402


class FakeClock:
    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += ms


def event(name, **fields):
    return {"session_id": "s1", "cwd": "/work/proj", "hook_event_name": name, **fields}


def agent_tool(name, tool_use_id, subagent_type="Explore", description="調査", **fields):
    return event(
        name,
        tool_name="Agent",
        tool_use_id=tool_use_id,
        tool_input={"subagent_type": subagent_type, "description": description, "prompt": "x"},
        **fields,
    )


def in_agent(name, agent_id="a1", agent_type="Explore", **fields):
    return event(name, agent_id=agent_id, agent_type=agent_type, **fields)


class SubagentTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = server.SessionStore(clock=self.clock)
        self.apply(event("SessionStart", source="startup"))
        self.apply(event("UserPromptSubmit"))

    def apply(self, payload):
        return self.store.apply_event(payload)

    def session(self):
        return self.store.snapshot()["sessions"][0]

    def agents(self):
        return self.session()["agents"]

    def test_foreground_agent_gets_description_from_pre_tool_use(self):
        # Foreground: PreToolUse(Agent) -> SubagentStart -> ... -> SubagentStop -> PostToolUse
        self.apply(agent_tool("PreToolUse", "t1", description="認証まわりの調査"))
        self.apply(in_agent("SubagentStart"))
        self.apply(in_agent("PreToolUse", tool_name="Grep"))
        [agent] = self.agents()
        self.assertEqual(agent["type"], "Explore")
        self.assertEqual(agent["description"], "認証まわりの調査")
        self.assertEqual(agent["state"], "running")
        self.assertEqual(agent["tool"], "Grep")
        self.assertEqual(agent["toolCount"], 1)
        # Subagent tools do not overwrite the parent's tool name
        self.assertEqual(self.session()["tool"], "Agent")
        self.apply(in_agent("SubagentStop"))
        self.apply(agent_tool("PostToolUse", "t1", tool_response={"agentId": "a1"}))
        [agent] = self.agents()
        self.assertEqual(agent["state"], "done")
        self.assertIsNone(agent["tool"])

    def test_background_agent_matches_description_by_agent_id(self):
        # Background: PostToolUse(Agent) with agentId arrives before SubagentStart
        self.apply(agent_tool("PreToolUse", "t1", description="一つ目"))
        self.apply(agent_tool("PreToolUse", "t2", description="二つ目"))
        self.apply(
            agent_tool(
                "PostToolUse",
                "t2",
                description="二つ目",
                tool_response={"agentId": "a2", "status": "async_launched"},
            )
        )
        self.apply(in_agent("SubagentStart", agent_id="a2"))
        self.assertEqual(self.agents()[0]["description"], "二つ目")

    def test_hint_is_matched_by_subagent_type(self):
        self.apply(agent_tool("PreToolUse", "t1", subagent_type="Plan", description="計画"))
        self.apply(agent_tool("PreToolUse", "t2", subagent_type="Explore", description="探索"))
        self.apply(in_agent("SubagentStart", agent_id="b", agent_type="Explore"))
        self.apply(in_agent("SubagentStart", agent_id="a", agent_type="Plan"))
        by_id = {a["id"]: a for a in self.agents()}
        self.assertEqual(by_id["b"]["description"], "探索")
        self.assertEqual(by_id["a"]["description"], "計画")

    def test_background_agent_does_not_wake_idle_parent(self):
        self.apply(agent_tool("PreToolUse", "t1"))
        self.apply(agent_tool("PostToolUse", "t1", tool_response={"agentId": "a1"}))
        self.apply(in_agent("SubagentStart"))
        self.apply(event("Stop", background_tasks=[{"id": "a1", "type": "subagent"}]))
        self.assertEqual(self.session()["state"], "IDLE")
        self.assertEqual(self.agents()[0]["state"], "running")
        # A background subagent's tool calls do not bring an idle parent back to busy
        self.apply(in_agent("PreToolUse", tool_name="Bash"))
        self.apply(in_agent("PostToolUse", tool_name="Bash"))
        self.assertEqual(self.session()["state"], "IDLE")
        self.assertEqual(self.agents()[0]["tool"], "Bash")

    def test_subagent_permission_request_waits_and_restores(self):
        self.apply(agent_tool("PreToolUse", "t1"))
        self.apply(in_agent("SubagentStart"))
        self.assertEqual(self.session()["state"], "BUSY")
        self.apply(in_agent("PermissionRequest", tool_name="Bash"))
        self.assertEqual(self.session()["state"], "WAIT")
        self.assertTrue(self.agents()[0]["waiting"])
        # Even with a parent Notification in between, subagent events restore the state
        self.apply(event("Notification", notification_type="permission_prompt"))
        self.apply(in_agent("PostToolUse", tool_name="Bash"))
        self.assertEqual(self.session()["state"], "BUSY")
        self.assertFalse(self.agents()[0]["waiting"])

    def test_background_subagent_wait_restores_idle(self):
        self.apply(in_agent("SubagentStart"))
        self.apply(event("Stop"))
        self.apply(in_agent("PermissionRequest", tool_name="Bash"))
        self.assertEqual(self.session()["state"], "WAIT")
        self.apply(in_agent("PostToolUse", tool_name="Bash"))
        self.assertEqual(self.session()["state"], "IDLE")

    def test_rejected_subagent_stop_clears_wait(self):
        self.apply(in_agent("SubagentStart"))
        self.apply(in_agent("PermissionRequest", tool_name="Bash"))
        self.apply(in_agent("SubagentStop"))
        self.assertEqual(self.session()["state"], "BUSY")

    def test_internal_agents_are_ignored(self):
        # Internal agents such as prompt suggestions have no agent_type and only send SubagentStop
        self.assertIsNone(self.apply(in_agent("SubagentStop", agent_id="int", agent_type="")))
        self.assertIsNone(self.apply(in_agent("SubagentStart", agent_id="int", agent_type="")))
        self.apply(in_agent("SubagentStop", agent_id="unknown"))
        self.assertEqual(self.agents(), [])

    def test_finished_agents_linger_then_disappear(self):
        self.apply(in_agent("SubagentStart"))
        self.apply(in_agent("SubagentStop"))
        self.clock.advance(server.DONE_AGENT_LINGER_MS - 1)
        self.assertFalse(self.store.prune())
        self.assertEqual(len(self.agents()), 1)
        self.clock.advance(1)
        self.assertTrue(self.store.prune())
        self.assertEqual(self.agents(), [])

    def test_resumed_agent_runs_again(self):
        self.apply(in_agent("SubagentStart"))
        self.apply(in_agent("SubagentStop"))
        self.apply(in_agent("SubagentStart"))
        [agent] = self.agents()
        self.assertEqual(agent["state"], "running")
        self.assertIsNone(agent["endedAt"])

    def test_agent_seen_mid_run_is_created(self):
        # Subagents started before the server are still shown from their tool events
        self.apply(in_agent("PreToolUse", agent_id="late", tool_name="Read"))
        [agent] = self.agents()
        self.assertEqual(agent["id"], "late")
        self.assertEqual(agent["tool"], "Read")

    def test_foreground_agent_without_subagent_events_finishes_with_agent_tool(self):
        # web-fetch sends no SubagentStart/SubagentStop, only tool events
        # (confirmed with Claude Code v2.1.308)
        self.apply(agent_tool("PreToolUse", "t1", subagent_type="web-fetch"))
        self.apply(in_agent("PreToolUse", agent_type="web-fetch", tool_name="WebFetch"))
        self.apply(in_agent("PostToolUse", agent_type="web-fetch", tool_name="WebFetch"))
        self.assertEqual(self.agents()[0]["state"], "running")
        self.apply(
            agent_tool(
                "PostToolUse", "t1", subagent_type="web-fetch", tool_response={"agentId": "a1"}
            )
        )
        self.assertEqual(self.agents()[0]["state"], "done")

    def test_background_agent_without_subagent_stop_finishes_when_no_longer_listed(self):
        self.apply(agent_tool("PreToolUse", "t1", subagent_type="web-fetch"))
        self.apply(
            agent_tool(
                "PostToolUse",
                "t1",
                subagent_type="web-fetch",
                tool_response={"agentId": "a1", "status": "async_launched"},
            )
        )
        self.apply(in_agent("PreToolUse", agent_type="web-fetch", tool_name="WebFetch"))
        # The Agent tool completing right after launch does not finish it
        self.assertEqual(self.agents()[0]["state"], "running")
        # While it runs in the background, it is listed in the parent's Stop background list
        self.apply(event("Stop", background_tasks=[{"id": "a1", "type": "subagent"}]))
        self.assertEqual(self.agents()[0]["state"], "running")
        # When it ends, the completion notice wakes the parent and it drops off the next Stop's list
        self.apply(event("UserPromptSubmit"))
        self.apply(event("Stop", background_tasks=[]))
        self.assertEqual(self.agents()[0]["state"], "done")

    def test_stop_without_background_list_keeps_agents(self):
        # A Stop without the list (older versions) cannot tell whether it runs, so do not finish it
        self.apply(in_agent("SubagentStart"))
        self.apply(event("Stop"))
        self.assertEqual(self.agents()[0]["state"], "running")

    def test_session_end_finishes_running_agents(self):
        self.apply(in_agent("SubagentStart"))
        self.apply(event("SessionEnd"))
        self.assertEqual(self.agents()[0]["state"], "done")

    def test_agent_list_is_capped(self):
        for i in range(server.MAX_AGENTS_PER_SESSION + 5):
            self.apply(in_agent("SubagentStart", agent_id=f"a{i}"))
            self.apply(in_agent("SubagentStop", agent_id=f"a{i}"))
        self.apply(in_agent("SubagentStart", agent_id="running"))
        agents = self.agents()
        self.assertEqual(len(agents), server.MAX_AGENTS_PER_SESSION)
        self.assertIn("running", [a["id"] for a in agents])

    def test_snapshot_is_a_deep_copy(self):
        self.apply(in_agent("SubagentStart"))
        snap = self.store.snapshot()
        snap["sessions"][0]["agents"].clear()
        self.assertEqual(len(self.agents()), 1)


class TaskAndBackgroundTest(unittest.TestCase):
    def setUp(self):
        self.store = server.SessionStore(clock=FakeClock())
        self.store.apply_event(event("UserPromptSubmit"))

    def session(self):
        return self.store.snapshot()["sessions"][0]

    def test_task_progress(self):
        self.assertEqual(self.session()["tasks"], {"total": 0, "done": 0, "current": None})
        self.store.apply_event(event("TaskCreated", task_id="1", task_subject="設計"))
        self.store.apply_event(event("TaskCreated", task_id="2", task_subject="実装"))
        self.store.apply_event(event("TaskCreated", task_id="3", task_subject="テスト"))
        self.store.apply_event(event("TaskCompleted", task_id="1", task_subject="設計"))
        self.assertEqual(self.session()["tasks"], {"total": 3, "done": 1, "current": "テスト"})
        self.store.apply_event(event("TaskCompleted", task_id="3"))
        self.store.apply_event(event("TaskCompleted", task_id="2"))
        self.assertEqual(self.session()["tasks"], {"total": 3, "done": 3, "current": None})

    def test_task_completed_without_creation_is_counted(self):
        self.store.apply_event(event("TaskCompleted", task_id="x", task_subject="途中から"))
        self.assertEqual(self.session()["tasks"]["total"], 1)
        self.assertEqual(self.session()["tasks"]["done"], 1)

    def test_background_counts_by_type_without_contents(self):
        tasks = [
            {"id": "1", "type": "shell", "command": "npm run dev"},
            {"id": "2", "type": "shell", "command": "secret"},
            {"id": "3", "type": "subagent", "agent_type": "Explore"},
            {"id": "4"},
        ]
        self.store.apply_event(event("Stop", background_tasks=tasks))
        self.assertEqual(self.session()["background"], {"shell": 2, "subagent": 1, "other": 1})
        self.assertNotIn("npm run dev", str(self.store.snapshot()))
        self.store.apply_event(event("Stop", background_tasks=[]))
        self.assertEqual(self.session()["background"], {})

    def test_session_end_clears_background(self):
        self.store.apply_event(event("Stop", background_tasks=[{"type": "shell"}]))
        self.store.apply_event(event("SessionEnd"))
        self.assertEqual(self.session()["background"], {})


if __name__ == "__main__":
    unittest.main()
