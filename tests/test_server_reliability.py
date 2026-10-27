"""Offline HTTP/decision regression tests with real FastAPI and Pydantic imports.

Only heavy visual inference is mocked. No model is downloaded, DNS lookup is
performed, or service process is started; TestClient calls the ASGI app in memory.
Run from the repository root: python -m unittest discover -s tests -p test_server_reliability.py -v
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from PIL import Image
from server import app as service


def screenshot(color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def visual_result(brand=None, domain=None):
    return {
        "brand": brand, "official_domain": domain, "reasons": [],
        "level_times": {"l1_logo_det": 0.01, "l2_logo_match": 0.01},
        "l1_logo_boxes": int(brand is not None), "l1_block_fallback": False,
        "l2_matched": brand is not None,
    }


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(service.app)
        self.cache = service.LRUCache()
        self.cache_patch = patch.object(service, "RESULT_CACHE", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.log_patch = patch.object(service, "_record_event")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.visual_patch = patch.object(service, "_analyze_visual", return_value=visual_result())
        self.visual = self.visual_patch.start()
        self.addCleanup(self.visual_patch.stop)
        self.payload = {
            "url": "https://login.example.com/",
            "screenshot_base64": screenshot(),
            "dom_signals": {"page_domain": "login.example.com", "has_password_input": False},
        }

    def analyze(self, payload=None):
        response = self.client.post("/analyze", json=payload or self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertIn(result["decision"], {"allow", "block", "uncertain"})
        self.assertIn(result["analysis_status"], {"completed", "degraded", "failed"})
        self.assertEqual(result["policy_version"], service.POLICY_VERSION)
        self.assertIsInstance(result["reason_codes"], list)
        self.assertIn(result["analysis_mode"], {"browser", "screenshot_only"})
        self.assertEqual(result["uci_prefilter"]["hint"], "disabled")
        self.assertEqual(result["uci_prefilter"]["reason_code"], service.UCI_DISABLED_REASON)
        if result["analysis_status"] != "completed":
            self.assertEqual(result["decision"], "uncertain")
        return result

    def test_browser_default_has_complete_protocol(self):
        result = self.analyze()
        self.assertEqual(result["analysis_mode"], "browser")
        self.assertEqual(result["analysis_status"], "completed")
        self.assertEqual(result["decision"], "allow")

    def test_invalid_request_body_is_rejected_by_real_http_schema(self):
        for body in ({"url": "https://example.com/"},
                     {"screenshot_base64": screenshot(), "analysis_mode": "unknown"}):
            with self.subTest(body=body):
                response = self.client.post("/analyze", json=body)
                self.assertEqual(response.status_code, 422)
                self.assertIn("detail", response.json())
        response = self.client.post("/analyze", content="{broken", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.visual.assert_not_called()
        self.assertEqual(len(self.cache), 0)

    def test_visual_failure_is_not_allow_or_cached_and_can_recover(self):
        self.visual.side_effect = [RuntimeError("model missing"), visual_result()]
        failed = self.analyze()
        self.assertEqual(failed["analysis_status"], "failed")
        self.assertIn("VISUAL_ANALYSIS_FAILED", failed["reason_codes"])
        self.assertEqual(len(self.cache), 0)
        recovered = self.analyze()
        cached = self.analyze()
        self.assertEqual(recovered["analysis_status"], "completed")
        self.assertFalse(recovered["from_cache"])
        self.assertTrue(cached["from_cache"])
        self.assertEqual(self.visual.call_count, 2)

    def test_suspicious_password_page_does_not_become_allow_on_failure(self):
        self.payload["url"] = "https://demo.xyz/"
        self.payload["dom_signals"] = {"page_domain": "demo.xyz", "has_password_input": True}
        self.visual.side_effect = RuntimeError("initialization failed")
        result = self.analyze()
        self.assertEqual(result["decision"], "uncertain")
        self.assertEqual(result["analysis_status"], "failed")

    def test_image_failure_even_on_whitelist_never_allows(self):
        self.payload.update(url="https://github.com/", dom_signals={
            "page_domain": "github.com", "has_password_input": False})
        for invalid in ("", "%%%", base64.b64encode(b"not an image").decode(), "data:image/png;base64,"):
            with self.subTest(invalid=invalid):
                self.payload["screenshot_base64"] = invalid
                result = self.analyze()
                self.assertEqual(result["analysis_status"], "failed")
                self.assertIn("IMAGE_DECODE_FAILED", result["reason_codes"])
        self.visual.assert_not_called()
        self.assertEqual(len(self.cache), 0)

    def test_screenshot_only_is_degraded_without_invented_context(self):
        self.payload = {"screenshot_base64": screenshot(), "analysis_mode": "screenshot_only"}
        result = self.analyze()
        self.assertEqual(result["analysis_status"], "degraded")
        self.assertIn("SCREENSHOT_ONLY", result["reason_codes"])
        self.assertEqual(self.visual.call_args.args[1], "")
        self.assertEqual(len(self.cache), 0)

    def test_screenshot_only_cannot_claim_official_domain_or_password(self):
        self.visual.return_value = visual_result("Example Bank", "bank.example")
        self.payload.update(analysis_mode="screenshot_only", url="https://github.com/",
                            dom_signals={"page_domain": "github.com", "has_password_input": True})
        result = self.analyze()
        self.assertEqual(result["analysis_status"], "degraded")
        self.assertEqual(result["decision"], "uncertain")
        self.visual.assert_called_once()

    def test_screenshot_only_visual_failure_is_failed(self):
        self.payload["analysis_mode"] = "screenshot_only"
        self.visual.side_effect = ImportError("torch unavailable")
        self.assertEqual(self.analyze()["analysis_status"], "failed")

    def test_active_window_sentinel_is_not_a_real_url(self):
        self.payload["url"] = "active_window"
        result = self.analyze()
        self.assertEqual(result["analysis_status"], "failed")
        self.assertIn("INVALID_URL", result["reason_codes"])
        self.visual.assert_not_called()

    def test_invalid_urls_cannot_return_allow(self):
        for invalid in ("", "github.com", "file:///a.png", "https://[", "https://site:bad", "https://bad_host"):
            with self.subTest(url=invalid):
                self.payload["url"] = invalid
                result = self.analyze()
                self.assertEqual(result["analysis_status"], "failed")
                self.assertIn("INVALID_URL", result["reason_codes"])

    def test_missing_dom_or_unknown_password_is_degraded(self):
        for dom in (None, {}, {"page_domain": "login.example.com"}):
            with self.subTest(dom=dom):
                self.payload["dom_signals"] = dom
                result = self.analyze()
                self.assertEqual(result["analysis_status"], "degraded")
                self.assertIn("BROWSER_CONTEXT_MISSING", result["reason_codes"])
        self.assertEqual(len(self.cache), 0)

    def test_declared_official_domain_cannot_override_real_url(self):
        self.payload["dom_signals"]["page_domain"] = "github.com"
        result = self.analyze()
        self.assertEqual(result["analysis_status"], "failed")
        self.assertIn("PAGE_DOMAIN_MISMATCH", result["reason_codes"])
        self.visual.assert_not_called()
        self.assertEqual(len(self.cache), 0)

    def test_explicit_whitelist_keeps_response_contract(self):
        self.payload.update(url="https://github.com/", dom_signals={
            "page_domain": "github.com", "has_password_input": False})
        result = self.analyze()
        self.assertEqual(result["analysis_status"], "completed")
        self.assertEqual(result["decision"], "allow")
        self.assertIn("WHITELIST_MATCH", result["reason_codes"])
        self.visual.assert_not_called()

    def test_public_suffixes_are_not_whitelisted(self):
        for host in ("unknown.edu.cn", "unknown.gov.cn"):
            with self.subTest(host=host):
                self.payload.update(url=f"https://{host}/", dom_signals={
                    "page_domain": host, "has_password_input": False})
                result = self.analyze()
                self.assertNotIn("WHITELIST_MATCH", result["reason_codes"])
        self.assertEqual(self.visual.call_count, 2)

    def test_block_requires_brand_wrong_domain_and_observed_password(self):
        self.visual.return_value = visual_result("Example Bank", "bank.example")
        self.payload["dom_signals"]["has_password_input"] = True
        self.payload['dom_signals'].update(viewport={'width':800,'height':600},
            credential_contexts=[{'box':[0,0,400,500],'text':'Example Bank login'}])
        self.visual.return_value['visual_evidence'] = {
            'hit':{'name':'Example Bank','aliases':['Example Bank'],'box':[50,50,100,100]},
            'image_size':[800,600]}
        result = self.analyze()
        self.assertEqual(result["decision"], "block")
        self.assertIn("THREE_CONDITION_MATCH", result["reason_codes"])

    def test_unconfirmed_brand_is_neither_block_nor_allow(self):
        # Even an inconsistent upstream candidate cannot become a named verdict.
        self.visual.return_value = {**visual_result("Feishu", "feishu.cn"),
            "brand_unconfirmed": True,
            "brand_candidate": {"name": "Feishu", "similarity": 0.9,
                                "source": "mobilenet_similarity"}}
        for password in (False, True):
            self.cache.clear()
            self.payload["dom_signals"]["has_password_input"] = password
            result = self.analyze()
            self.assertEqual(result["decision"], "uncertain")
            self.assertIsNone(result["brand"])
            self.assertIsNone(result["official_domain"])
            self.assertIn("BRAND_UNCONFIRMED", result["reason_codes"])
            self.assertNotIn("THREE_CONDITION_MATCH", result["reason_codes"])

    def test_suspicious_url_and_external_form_cannot_replace_password(self):
        self.visual.return_value = visual_result("Example Bank", "bank.example")
        self.payload.update(url="http://bank-login.xyz/", dom_signals={
            "page_domain": "bank-login.xyz", "has_password_input": False,
            "has_login_form": True, "form_action_domains": ["outside.example"],
        })
        result = self.analyze()
        self.assertEqual(result["decision"], "uncertain")
        self.assertEqual(result["analysis_status"], "completed")
        self.assertNotIn("PASSWORD_INPUT_DETECTED", result["reason_codes"])

    def test_official_brand_domain_does_not_block(self):
        self.visual.return_value = visual_result("Example Bank", "bank.example")
        self.payload.update(url="https://bank.example/", dom_signals={
            "page_domain": "bank.example", "has_password_input": True,
            "password_forms": [{"action_domain":"bank.example", "action_scheme":"https", "method":"post"}]})
        self.assertEqual(self.analyze()["decision"], "allow")

    def test_changed_pixels_reanalyze_and_cache_results_are_copies(self):
        original = self.analyze()
        key = service.build_cache_key(service.AnalyzeRequest(**self.payload),
                                      service._decode_screenshot(self.payload["screenshot_base64"]))
        cached = self.analyze()
        self.assertTrue(cached["from_cache"])
        stored = self.cache.get(key)
        self.assertFalse(stored["from_cache"])
        self.assertIn("l1_logo_det", stored["level_times"])
        self.payload["screenshot_base64"] = screenshot("black")
        changed = self.analyze()
        self.assertFalse(changed["from_cache"])
        self.assertEqual(self.visual.call_count, 2)
        self.assertFalse(original["from_cache"])


class VisualEvidenceTests(unittest.TestCase):
    def test_recognition_is_independent_of_official_domain(self):
        from server.visual_brand import BrandIndex
        import numpy as np
        feature = np.asarray([[1.,0.],[0.,1.]], dtype=np.float32)
        index = BrandIndex(feature, [
            {'brand':n,'domains':[d],'aliases':[n],'path':n,'size':[16,16]}
            for n,d in [('Example Bank','bank.example'),('Other','other.example')]], 'test')
        models={'awl':None,'brand_index':index,'embed_logo':lambda im:[1.,0.],
                'find_element_type': lambda *args,**kw: ([[0,0,16,16]] if kw['bbox_type']=='logo' else [],None)}
        stream=io.BytesIO()
        im=Image.new('RGB',(100,100),'white')
        from PIL import ImageDraw
        ImageDraw.Draw(im).rectangle((1,1,8,15),fill='black');im.save(stream,format='PNG')
        with patch.object(service,'load_models'), patch.object(service,'_MODELS',models), \
             patch.object(service,'_pred_rcnn_from_bytes',return_value=([],[],[])):
            result=service._analyze_visual(stream.getvalue(),'https://bank.example')
        self.assertEqual(result['brand'],'Example Bank')
        self.assertEqual(result['official_domain'],'bank.example')
        self.assertFalse(result['brand_unconfirmed'])


class CacheTests(unittest.TestCase):
    def test_key_covers_complete_dom_modes_pixels_and_versions(self):
        payload = {"url": "https://example.com/", "screenshot_base64": "a",
                   "dom_signals": {"title": "x" * 200, "has_password_input": False}}
        key = service.build_cache_key(service.AnalyzeRequest(**payload))
        for update in ({"screenshot_base64": "b"}, {"analysis_mode": "screenshot_only"},
                       {"dom_signals": {"title": "x" * 199 + "y", "has_password_input": False}},
                       {"dom_signals": None}):
            changed = deepcopy(payload)
            changed.update(update)
            self.assertNotEqual(key, service.build_cache_key(service.AnalyzeRequest(**changed)))
        with patch.object(service, "POLICY_VERSION", "next"):
            self.assertNotEqual(key, service.build_cache_key(service.AnalyzeRequest(**payload)))
        with patch.object(service, "MODEL_VERSION", "next-model"):
            self.assertNotEqual(key, service.build_cache_key(service.AnalyzeRequest(**payload)))

    def test_ttl_and_deepcopy(self):
        now = [0.0]
        cache = service.LRUCache(capacity=2, ttl_seconds=10, clock=lambda: now[0])
        source = {"nested": [1]}
        cache.put("a", source)
        source["nested"].append(2)
        first = cache.get("a")
        first["nested"].append(3)
        self.assertEqual(cache.get("a"), {"nested": [1]})
        now[0] = 10.0
        self.assertIsNone(cache.get("a"))
        self.assertEqual(len(cache), 0)

    def test_lru_and_concurrent_access_stay_bounded(self):
        cache = service.LRUCache(capacity=2)
        cache.put("a", {})
        cache.put("b", {})
        cache.get("a")
        cache.put("c", {})
        self.assertIsNone(cache.get("b"))

        def worker(index):
            for i in range(100):
                cache.put(f"{index}-{i}", {"value": [i]})
                cache.get(f"{index}-{i}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(8)))
        self.assertLessEqual(len(cache), 2)


class ModelLifecycleTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("MODELS_READY", False), ("MODELS_LOAD_ERROR", None),
                            ("_MODELS_LAST_FAILURE", float("-inf")), ("_MODELS", {})):
            p = patch.object(service, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_concurrent_requests_initialize_once(self):
        barrier = threading.Barrier(6)

        def initialize():
            time.sleep(0.02)
            return {"loaded": True}

        def worker(_):
            barrier.wait(timeout=2)
            service.load_models()

        with patch.object(service, "_initialize_models", side_effect=initialize) as init:
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(worker, range(6)))
            self.assertEqual(init.call_count, 1)
        self.assertTrue(service.MODELS_READY)
        self.assertEqual(service._MODELS, {"loaded": True})

    def test_failure_retries_only_after_cooldown(self):
        now = [10.0]
        with patch.object(service.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(service, "MODEL_RETRY_DELAY_S", 30.0), \
             patch.object(service, "_initialize_models", side_effect=[OSError("missing"), {"ok": True}]) as init:
            with self.assertRaises(RuntimeError):
                service.load_models()
            with self.assertRaises(RuntimeError):
                service.load_models()
            self.assertEqual(init.call_count, 1)
            now[0] = 40.0
            service.load_models()
            self.assertEqual(init.call_count, 2)
        self.assertTrue(service.MODELS_READY)
        self.assertIsNone(service.MODELS_LOAD_ERROR)

    def test_initializer_restores_real_cwd_on_failure(self):
        before = os.getcwd()
        with patch.object(service.yaml, "safe_load", side_effect=ValueError("invalid config")):
            with self.assertRaises(ValueError):
                service._initialize_models()
        self.assertEqual(os.getcwd(), before)


if __name__ == "__main__":
    unittest.main()
