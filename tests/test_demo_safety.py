"""Static, offline checks for local demo pages; never starts a browser."""
from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.scripts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


class DemoSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("demo_generator", ROOT / "tools" / "make_fake_pages.py")
        cls.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.generator)

    def assert_local_demo(self, html):
        parsed = PageParser()
        parsed.feed(html)
        forms = [attrs for tag, attrs in parsed.tags if tag == "form"]
        self.assertTrue(forms, "Demo must retain a form for defensive detection")
        self.assertTrue(all(attrs.get("action") == "#" for attrs in forms), "All form actions must be local #")
        inputs = [attrs for tag, attrs in parsed.tags if tag == "input"]
        self.assertTrue(any(attrs.get("type") == "password" for attrs in inputs), "Keep the password input for detection")
        self.assertTrue(all("name" not in attrs for attrs in inputs), "Demo inputs must not be successful named controls")
        self.assertTrue(all("formaction" not in attrs for _, attrs in parsed.tags), "No submitter may override the local form target")
        policies = [attrs.get("content", "") for tag, attrs in parsed.tags
                    if tag == "meta" and attrs.get("http-equiv", "").lower() == "content-security-policy"]
        self.assertTrue(any("form-action 'none'" in policy for policy in policies), "CSP must block native form submission")
        self.assertTrue(any(attrs.get("id") == "local-demo-banner" for _, attrs in parsed.tags))
        self.assertTrue(any(attrs.get("id") == "local-demo-status" for _, attrs in parsed.tags))
        self.assertIn("本地演示 / 请勿输入真实账号密码", html)
        self.assertIn("position:fixed", html)
        self.assertIn("演示提交（不会登录）", html)
        self.assertEqual(len(parsed.scripts), 1, "Only the local submit guard may run")
        guard = parsed.scripts[0]
        self.assertIn("addEventListener('submit'", guard)
        self.assertIn("event.preventDefault()", guard)
        self.assertIn("status.hidden = false", guard)
        self.assertIn("没有发送账号、密码或验证码", guard)
        for token in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", ".value", "localStorage", "sessionStorage"):
            self.assertNotIn(token, guard, "Demo guard must not read, persist or transmit input")
        for _, attrs in parsed.tags:
            for key in ("src", "href", "action", "formaction"):
                value = attrs.get(key, "")
                self.assertFalse(value.lower().startswith(("http:", "https:", "//")), f"External {key} is forbidden in local demos")

    def test_checked_in_pages_are_local_only(self):
        pages = sorted((ROOT / "testset" / "fake").glob("*.html"))
        self.assertGreaterEqual(len(pages), 4)
        for path in pages:
            with self.subTest(page=path.name):
                html = path.read_text(encoding="utf-8")
                self.assert_local_demo(html)
                self.assertTrue(self.generator.local_demo_html(html) == html, "Safety transformation must be idempotent")

    def test_future_generated_page_is_local_only(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.generator, "FAKE_DIR", Path(directory)), \
                patch.object(self.generator, "logo_of", return_value={"logo_path": "unused"}), \
                patch.object(self.generator, "b64", return_value="dGVzdA=="):
            path = self.generator.render("demo.html", "brand", "never-contact.invalid", "品牌演示",
                                         "演示标题", "演示品牌", "仅用于测试", "#1677ff")
            html = path.read_text(encoding="utf-8")
            self.assert_local_demo(html)
            self.assertNotIn("never-contact.invalid", html)

    def test_html_only_flag_never_launches_browser(self):
        with patch.object(sys, "argv", ["make_fake_pages.py", "--html-only"]), \
                patch.object(self.generator, "render", return_value=ROOT / "testset" / "fake" / "demo.html"), \
                patch.object(self.generator, "screenshot") as screenshot, \
                patch("builtins.print"):
            self.generator.main()
            screenshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
