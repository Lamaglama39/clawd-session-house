"""Tests for install-hooks.py (settings merge, idempotency, uninstall)."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "install_hooks", os.path.join(HERE, "..", "scripts", "install-hooks.py")
)
install_hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_hooks)


def commands(settings, event):
    return [h["command"] for g in settings["hooks"].get(event, []) for h in g["hooks"]]


class InstallTest(unittest.TestCase):
    def test_registers_every_event(self):
        result = install_hooks.install({})
        events = {name for name, _ in install_hooks.HOOK_EVENTS}
        self.assertEqual(set(result["hooks"]), events)
        for name in events:
            cmds = commands(result, name)
            self.assertEqual(len(cmds), 1)
            self.assertIn(install_hooks.SEND_EVENT, cmds[0])
            self.assertTrue(cmds[0].endswith(install_hooks.HOOK_TAG))

    def test_plugin_hooks_match_installer(self):
        # The plugin's hooks.json is maintained by hand, so check its events, matchers, and timeouts
        # match install-hooks.py (only the commands differ for the plugin)
        with open(os.path.join(HERE, "..", "hooks", "hooks.json"), encoding="utf-8") as f:
            plugin = json.load(f)["hooks"]
        expected = install_hooks.install({})["hooks"]
        for event, groups in expected.items():
            for group in groups:
                for handler in group["hooks"]:
                    handler["command"] = '"${CLAUDE_PLUGIN_ROOT}/hooks/send-event.sh"' + (
                        " --autostart" if event == "SessionStart" else ""
                    )
        self.assertEqual(plugin, expected)

    def test_ownership_is_decided_by_tag_not_path(self):
        tag = install_hooks.HOOK_TAG
        # Even when cloned elsewhere, an entry with the tag counts as ours
        self.assertTrue(install_hooks.is_ours({"command": f"/elsewhere/hooks/send-event.sh {tag}"}))
        self.assertFalse(install_hooks.is_ours({"command": install_hooks.SEND_EVENT}))
        self.assertFalse(install_hooks.is_ours({"command": f"echo '{tag}' && say hi"}))
        self.assertFalse(install_hooks.is_ours("not a dict"))

    def test_session_start_autostarts_by_default(self):
        result = install_hooks.install({})
        self.assertIn(" --autostart ", commands(result, "SessionStart")[0])
        self.assertNotIn("--autostart", commands(result, "Stop")[0])
        result = install_hooks.install({}, autostart=False)
        self.assertNotIn("--autostart", commands(result, "SessionStart")[0])

    def test_notification_has_matcher(self):
        result = install_hooks.install({})
        group = result["hooks"]["Notification"][0]
        self.assertEqual(group["matcher"], install_hooks.NOTIFICATION_MATCHER)
        self.assertNotIn("matcher", result["hooks"]["PreToolUse"][0])

    def test_is_idempotent(self):
        once = install_hooks.install({})
        twice = install_hooks.install(once)
        self.assertEqual(once, twice)

    def test_preserves_other_settings_and_hooks(self):
        original = {
            "theme": "dark",
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
                "PreCompact": [{"hooks": [{"type": "command", "command": "echo compact"}]}],
            },
        }
        result = install_hooks.install(original)
        self.assertEqual(result["theme"], "dark")
        self.assertEqual(commands(result, "Stop")[0], "say done")
        self.assertEqual(len(commands(result, "Stop")), 2)
        self.assertEqual(commands(result, "PreCompact"), ["echo compact"])
        # The original dict is not modified
        self.assertEqual(len(original["hooks"]["Stop"]), 1)

    def test_uninstall_removes_only_ours(self):
        original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
        installed = install_hooks.install(original)
        result, removed = install_hooks.uninstall(installed)
        self.assertEqual(removed, len(install_hooks.HOOK_EVENTS))
        self.assertEqual(result, original)

    def test_uninstall_drops_empty_hooks_key(self):
        result, removed = install_hooks.uninstall(install_hooks.install({"model": "x"}))
        self.assertEqual(result, {"model": "x"})
        self.assertGreater(removed, 0)

    def test_port_and_url_options(self):
        result = install_hooks.install({}, port=9000)
        self.assertTrue(commands(result, "Stop")[0].startswith("CLAUDE_STATUS_PORT=9000 "))
        result = install_hooks.install({}, url="http://192.168.1.10:8765/hook")
        cmd = commands(result, "SessionStart")[0]
        self.assertIn("CLAUDE_STATUS_URL=http://192.168.1.10:8765/hook", cmd)
        self.assertNotIn("--autostart", cmd)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            install_hooks.main(["--settings", self.path, *args])
        return out.getvalue()

    def backups(self):
        return [n for n in os.listdir(self.tmp.name) if ".bak-" in n]

    def test_creates_file_when_missing(self):
        self.run_main()
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("SessionStart", data["hooks"])
        self.assertEqual(self.backups(), [])

    def test_backs_up_existing_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"theme": "dark"}, f)
        out = self.run_main()
        self.assertIn("backup:", out)
        self.assertEqual(len(self.backups()), 1)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["theme"], "dark")

    def test_dry_run_does_not_write(self):
        self.run_main("--dry-run")
        self.assertFalse(os.path.exists(self.path))

    def test_second_install_reports_no_changes(self):
        self.run_main()
        self.assertIn("no changes", self.run_main())

    def test_invalid_json_aborts_without_writing(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        with self.assertRaises(SystemExit):
            self.run_main()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ broken")


if __name__ == "__main__":
    unittest.main()
