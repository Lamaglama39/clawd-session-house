"""Browser tests for the live view and the history page, saving screenshots.

Run them with ``npm run test:ui``, which provides Playwright and drives the installed Google
Chrome. Screenshots go to .tmp/screenshots/<date-time>/. A plain ``npm test`` has no Playwright,
so these tests are skipped there.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "plugin", "server"))

import server  # noqa: E402
from history import HistoryStore  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

DEMO = os.path.join(ROOT, "plugin", "scripts", "demo.py")
SHOTS = os.path.join(ROOT, ".tmp", "screenshots", time.strftime("%Y%m%d-%H%M%S"))

# Sessions shown on the live view: one per state, so every kind of tile is on the screenshots
LIVE_SESSIONS = {
    "s-busy": [
        ("UserPromptSubmit", {"session_title": "READMEの更新"}),
        ("PreToolUse", {"tool_name": "Bash"}),
    ],
    "s-wait": [("UserPromptSubmit", {}), ("PermissionRequest", {"tool_name": "Edit"})],
    "s-idle": [("UserPromptSubmit", {}), ("Stop", {})],
    "s-off": [("SessionStart", {"source": "startup"}), ("SessionEnd", {"reason": "exit"})],
}


@unittest.skipUnless(sync_playwright, "Playwright is not installed; run npm run test:ui")
class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls.tmp.name, "history.sqlite3")
        subprocess.run(
            [sys.executable, DEMO, "--seed-history", db, "--days", "90", "--seed", "1"],
            check=True,
            capture_output=True,
        )
        cls.history = HistoryStore(db)
        cls.httpd = server.StatusServer(
            ("127.0.0.1", 0), server.SessionStore(), history=cls.history, log_events=False
        )
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        for session_id, events in LIVE_SESSIONS.items():
            for name, fields in events:
                cls.send(session_id, name, **fields)

        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(channel="chrome")
        except Exception:
            # No Google Chrome: fall back to Playwright's own Chromium if it is installed
            cls.browser = cls.pw.chromium.launch()
        os.makedirs(SHOTS, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutting_down.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.history.close()
        cls.tmp.cleanup()
        print(f"\nscreenshots: {SHOTS}", file=sys.stderr)

    @classmethod
    def send(cls, session_id, name, **fields):
        payload = {"session_id": session_id, "cwd": f"/work/{session_id}", "hook_event_name": name}
        req = urllib.request.Request(
            f"{cls.base}/hook",
            data=json.dumps({**payload, **fields}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).close()

    def live_ids(self):
        with urllib.request.urlopen(f"{self.base}/api/sessions", timeout=5) as resp:
            return {s["id"] for s in json.load(resp)["sessions"]}

    def open(self, path, width, height=900, locale="ja-JP"):
        context = self.browser.new_context(
            viewport={"width": width, "height": height}, locale=locale
        )
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.base + path)
        if path.startswith("/history"):
            page.wait_for_selector("#sessions-pager .pager-range")
        else:
            page.wait_for_function(
                f"document.querySelectorAll('.tile').length >= {len(LIVE_SESSIONS)}"
            )
        return page

    def shot(self, target, name, full_page=True):
        path = os.path.join(SHOTS, f"{name}.png")
        if full_page:
            target.screenshot(path=path, full_page=True)
        else:
            target.screenshot(path=path)

    def assert_no_overflow(self, page):
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        self.assertLessEqual(overflow, 0, "the page scrolls sideways")

    @staticmethod
    def opacity(locator):
        return locator.evaluate("(el) => getComputedStyle(el).opacity")

    @staticmethod
    def card_heights(page):
        return page.eval_on_selector_all(
            ".cols .card", "(cards) => cards.map((c) => Math.round(c.offsetHeight))"
        )

    # --- Live view ---

    def test_live_view_fits_each_width(self):
        for width, height in ((1920, 1080), (1100, 800), (420, 800)):
            with self.subTest(width=width):
                page = self.open("/", width, height)
                self.shot(page, f"live-{width}")
                self.assert_no_overflow(page)

    def test_live_tile_needs_a_second_press(self):
        self.send("dismiss-live", "UserPromptSubmit")
        self.send("dismiss-off", "SessionStart", source="startup")
        self.send("dismiss-off", "SessionEnd", reason="exit")
        page = self.open("/", 1100, 800)
        page.mouse.move(0, 0)
        tile = page.locator('.tile[data-id="dismiss-live"]')
        dismiss = tile.locator(".tile-dismiss")
        # Hidden until the pointer is over the tile, so a wall of tiles stays calm
        self.assertEqual(self.opacity(dismiss), "0")
        tile.hover()
        self.assertEqual(self.opacity(dismiss), "1")
        self.shot(tile, "tile-hover", full_page=False)

        dismiss.click()
        self.assertEqual(dismiss.inner_text(), "消す")
        self.shot(tile, "tile-confirm", full_page=False)
        self.assertIn("dismiss-live", self.live_ids())
        dismiss.click()
        tile.wait_for(state="detached")
        self.assertNotIn("dismiss-live", self.live_ids())

        # An ended tile always shows the x and goes with one press
        page.mouse.move(0, 0)
        ended = page.locator('.tile[data-id="dismiss-off"]')
        self.assertEqual(self.opacity(ended.locator(".tile-dismiss")), "1")
        ended.locator(".tile-dismiss").click()
        ended.wait_for(state="detached")
        self.assertNotIn("dismiss-off", self.live_ids())

    # --- History page ---

    def test_history_fits_each_width(self):
        for width in (1440, 1100, 600, 420):
            with self.subTest(width=width):
                page = self.open("/history.html#days=90", width)
                self.shot(page, f"history-{width}")
                self.assert_no_overflow(page)
                if width >= 1100:
                    # The three summary cards share a row there and should be level
                    self.assertEqual(len(set(self.card_heights(page))), 1)

    def test_history_paging(self):
        page = self.open("/history.html#days=90", 1440, 1000)
        projects = page.locator("#projects")
        self.assertRegex(projects.locator(".pager-range").inner_text(), r"^1–5 / \d+件$")
        heights = self.card_heights(page)
        projects.locator('.pager-step[data-dir="1"]').click()
        self.assertRegex(projects.locator(".pager-range").inner_text(), r"^6–10 / \d+件$")
        self.assertEqual(self.card_heights(page), heights)

        sessions = page.locator("#sessions-pager")
        page.mouse.wheel(0, 5000)
        sessions.locator('.pager-step[data-dir="1"]').click()
        self.assertRegex(sessions.locator(".pager-range").inner_text(), r"^21–40 / \d+件$")
        # After a page turn the card heading is back in view, just under the sticky header
        top = page.evaluate(
            "document.querySelector('#sessions').closest('.card').getBoundingClientRect().top"
            " - document.querySelector('.hbar').offsetHeight"
        )
        self.assertGreaterEqual(top, 0)
        self.shot(page, "history-paging")

        page.select_option("#page-size", "50")
        self.assertEqual(page.locator("#sessions tbody tr").count(), 50)
        page.reload()
        page.wait_for_selector("#sessions-pager .pager-range")
        self.assertEqual(page.input_value("#page-size"), "50")
        self.assertEqual(page.locator("#sessions tbody tr").count(), 50)

    # --- English ---

    def test_english_labels(self):
        page = self.open("/", 420, 800, locale="en-US")
        tile = page.locator('.tile[data-id="s-busy"]')
        tile.hover()
        tile.locator(".tile-dismiss").click()
        self.assertEqual(tile.locator(".tile-dismiss").inner_text(), "Remove")
        self.shot(page, "live-420-en")

        page = self.open("/history.html#days=90", 420, locale="en-US")
        self.assertEqual(page.inner_text(".page-size span"), "Rows per page")
        self.assertTrue(re.match(r"^1–5 of \d+$", page.inner_text("#projects .pager-range")))
        self.shot(page, "history-420-en")
        self.assert_no_overflow(page)


if __name__ == "__main__":
    unittest.main()
