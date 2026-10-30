"""Integration tests for start-server.sh / stop-server.sh."""

import os
import shutil
import socket
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
START = os.path.join(ROOT, "scripts", "start-server.sh")
STOP = os.path.join(ROOT, "scripts", "stop-server.sh")


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


class ServerScriptsTest(unittest.TestCase):
    def setUp(self):
        # Keep runtime files in a temporary directory so the real run/ is never touched
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("CLAUDE_STATUS_", "CLAUDE_PLUGIN_"))
        }
        self.env["CLAUDE_STATUS_RUN_DIR"] = self.tmp.name
        # Keep the database in a temporary directory too, so the server never creates data/
        self.env["CLAUDE_STATUS_DB"] = os.path.join(self.tmp.name, "history.sqlite3")
        self.ports = [unused_port(), unused_port()]

    def tearDown(self):
        for port in self.ports:
            self.run_script(STOP, "--port", str(port))
        self.tmp.cleanup()

    def run_script(self, script, *args, port=None):
        env = dict(self.env)
        if port is not None:
            env["CLAUDE_STATUS_PORT"] = str(port)
        return subprocess.run([script, *args], env=env, capture_output=True, text=True, timeout=20)

    def pid_of(self, port):
        with open(os.path.join(self.tmp.name, f"server-{port}.pid"), encoding="utf-8") as f:
            return f.read().strip()

    def test_start_is_idempotent_and_stop_stops(self):
        port = self.ports[0]
        self.assertEqual(self.run_script(START, port=port).returncode, 0)
        self.assertTrue(healthy(port))
        first = self.pid_of(port)
        self.assertEqual(self.run_script(START, port=port).returncode, 0)
        self.assertEqual(self.pid_of(port), first)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, f"server-{port}.log")))
        out = self.run_script(STOP, "--port", str(port)).stdout
        self.assertIn("stopped", out)
        self.assertFalse(healthy(port))

    def test_servers_on_different_ports_do_not_interfere(self):
        a, b = self.ports
        self.assertEqual(self.run_script(START, "--port", str(a)).returncode, 0)
        self.assertEqual(self.run_script(START, port=b).returncode, 0)
        self.assertTrue(healthy(a))
        self.assertTrue(healthy(b))
        # Stopping b leaves a's server running
        self.run_script(STOP, port=b)
        self.assertFalse(healthy(b))
        self.assertTrue(healthy(a))

    def read_run_file(self, name, run_dir=None):
        with open(os.path.join(run_dir or self.tmp.name, name), encoding="utf-8") as f:
            return f.read().strip()

    def test_plugin_data_dir_holds_runtime_files_and_history(self):
        port = self.ports[0]
        data = os.path.join(self.tmp.name, "plugin-data")
        env = {
            k: v
            for k, v in self.env.items()
            if k not in ("CLAUDE_STATUS_RUN_DIR", "CLAUDE_STATUS_DB")
        }
        env.update(CLAUDE_PLUGIN_DATA=data, CLAUDE_STATUS_PORT=str(port))

        def run(script):
            return subprocess.run([script], env=env, capture_output=True, text=True, timeout=20)

        self.assertEqual(run(START).returncode, 0)
        self.addCleanup(run, STOP)
        self.assertTrue(healthy(port))
        run_dir = os.path.join(data, "run")
        self.assertTrue(os.path.exists(os.path.join(run_dir, f"server-{port}.pid")))
        self.assertTrue(os.path.samefile(self.read_run_file(f"server-{port}.root", run_dir), ROOT))
        self.assertTrue(os.path.exists(os.path.join(data, "history.sqlite3")))
        self.assertIn("stopped", run(STOP).stdout)

    def test_orphaned_plugin_version_is_replaced(self):
        port = self.ports[0]
        # The plugin keeps each version in its own directory; start the pre-update one elsewhere
        old = os.path.join(self.tmp.name, "old-version")
        for name in ("server/server.py", "server/history.py", "scripts/start-server.sh"):
            os.makedirs(os.path.join(old, os.path.dirname(name)), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, name), os.path.join(old, name))
        self.assertEqual(
            self.run_script(os.path.join(old, "scripts", "start-server.sh"), port=port).returncode,
            0,
        )
        old_pid = self.pid_of(port)
        # Without the mark, older sessions may still use that version, so it is not replaced
        self.assertEqual(self.run_script(START, port=port).returncode, 0)
        self.assertEqual(self.pid_of(port), old_pid)
        # Once Claude Code marks the old version, start again with the new version
        open(os.path.join(old, ".orphaned_at"), "w").close()
        self.assertEqual(self.run_script(START, port=port).returncode, 0)
        self.assertNotEqual(self.pid_of(port), old_pid)
        self.assertTrue(healthy(port))
        self.assertTrue(os.path.samefile(self.read_run_file(f"server-{port}.root"), ROOT))

    def test_stop_without_server_is_harmless(self):
        result = self.run_script(STOP, port=self.ports[0])
        self.assertEqual(result.returncode, 0)
        self.assertIn("not running", result.stdout)


if __name__ == "__main__":
    unittest.main()
