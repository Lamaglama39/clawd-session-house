"""Tests for the repository layout: only plugin/ is shipped as the plugin."""

import json
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLUGIN = os.path.join(ROOT, "plugin")

# Everything in plugin/ is copied to users. Add an entry here only for something users need
SHIPPED = {".claude-plugin", "hooks", "scripts", "server", "skills", "web", "LICENSE", "NOTICE"}
# Development files that must never be copied to users, at any depth
DEV_ONLY = {
    ".claude",
    "CLAUDE.md",
    "CLAUDE.local.md",
    "docs",
    "tests",
    "node_modules",
    "package.json",
    # A lockfile makes Claude Code install dependencies on every install and update
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
}


class PluginLayoutTest(unittest.TestCase):
    def test_marketplace_points_at_the_plugin_directory(self):
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as f:
            entry = json.load(f)["plugins"][0]
        self.assertEqual(entry["source"], "./plugin")
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["name"], entry["name"])
        # The repository root is the marketplace only, so it must not be a plugin as well
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".claude-plugin", "plugin.json")))

    def test_plugin_holds_only_shipped_files(self):
        self.assertEqual(set(os.listdir(PLUGIN)) - {".DS_Store"}, SHIPPED)
        for dirpath, dirnames, filenames in os.walk(PLUGIN):
            found = DEV_ONLY & set(dirnames + filenames)
            self.assertFalse(found, f"{os.path.relpath(dirpath, ROOT)}: {sorted(found)}")
            # claude plugin validate does not read through symlinks, so ship real files only
            links = [n for n in dirnames + filenames if os.path.islink(os.path.join(dirpath, n))]
            self.assertFalse(links, f"{os.path.relpath(dirpath, ROOT)}: {links}")

    def test_license_and_notice_match_the_repository_root(self):
        for name in ("LICENSE", "NOTICE"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as a:
                with open(os.path.join(PLUGIN, name), encoding="utf-8") as b:
                    self.assertEqual(a.read(), b.read(), name)


if __name__ == "__main__":
    unittest.main()
