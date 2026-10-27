import copy
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from shadow_fusion import combine, fetch_url_analysis
from server import app as service
from test_server_reliability import screenshot, visual_result


class ShadowTests(unittest.TestCase):
    def test_history_empty_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            with patch.dict(os.environ, {"PHISHLENS_SHADOW_LOG": str(path)}):
                self.assertEqual(service.shadow_events()["events"], [])
                path.write_text("\n".join(json.dumps({"ts":i}) for i in range(25))+"\n",
                                encoding="utf-8")
                events = service.shadow_events()["events"]
                self.assertEqual(len(events), 20)
                self.assertEqual(events[0]["ts"], 24)

    def url(self, score=.8, match=False):
        return dict(status="completed", ml_score=score, threshold=.4,
                    phishtank_match=match, model_version="test")

    def result(self, decision="allow", status="completed"):
        return dict(decision=decision, analysis_status=status,
                    general_risk={"level": "high"}, brand=None)

    def test_original_block_cannot_be_overridden(self):
        original = self.result("block")
        saved = copy.deepcopy(original)
        r = combine(original, self.url(.01), True)
        self.assertEqual(r["suggested_decision"], "block")
        self.assertEqual(original, saved)

    def test_high_url_and_insecure_form_only_request_review(self):
        r = combine(self.result(), self.url(), True)
        self.assertEqual(r["suggested_decision"], "uncertain")
        self.assertEqual(r["enforced_decision"], "allow")

    def test_database_hit_adds_advisory_not_enforcement(self):
        r = combine(self.result(), self.url(.01, True), True)
        self.assertEqual(r["suggested_decision"], "block")
        self.assertEqual(r["enforced_decision"], "allow")

    def test_missing_url_and_failed_visual_not_safe(self):
        r = combine(self.result(status="failed"), {"status": "unavailable"}, False)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["suggested_decision"], "uncertain")

    def test_bridge_failure_is_bounded_and_reported(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError):
            self.assertEqual(fetch_url_analysis("https://example.com")["status"], "unavailable")

    def test_wrapper_preserves_original_and_fresh_url_on_cache_hit(self):
        req = service.AnalyzeRequest(url="https://ordinary.example/",
            screenshot_base64=screenshot(), dom_signals={
                "page_domain": "ordinary.example", "has_password_input": False})
        with patch.dict(os.environ, {"PHISHLENS_SHADOW_URL": "1",
                                    "PHISHLENS_SHADOW_LOG": os.devnull}), \
             patch.object(service, "_record_event"), \
             patch.object(service, "RESULT_CACHE", service.LRUCache()), \
             patch.object(service, "_analyze_visual", return_value=visual_result()), \
             patch("shadow_fusion.fetch_url_analysis", side_effect=[self.url(.01), self.url(.9)]):
            a, b = service.analyze(req), service.analyze(req)
        self.assertEqual(a["decision"], b["decision"])
        self.assertTrue(b["from_cache"])
        self.assertEqual(a["shadow_fusion"]["url_analysis"]["ml_score"], .01)
        self.assertEqual(b["shadow_fusion"]["url_analysis"]["ml_score"], .9)

    def test_visual_failure_still_returns_url_evidence(self):
        req = service.AnalyzeRequest(url="https://ordinary.example/", screenshot_base64="bad",
            dom_signals={"page_domain":"ordinary.example","has_password_input":False})
        with patch.dict(os.environ, {"PHISHLENS_SHADOW_URL":"1","PHISHLENS_SHADOW_LOG":os.devnull}), \
             patch.object(service, "_record_event"), \
             patch("shadow_fusion.fetch_url_analysis", return_value=self.url()):
            r = service.analyze(req)
        self.assertEqual(r["decision"], "uncertain")
        self.assertEqual(r["analysis_status"], "failed")
        self.assertEqual(r["shadow_fusion"]["url_analysis"]["status"], "completed")

    def test_iana_low_score_keeps_review_and_separate_original_explanation(self):
        original = self.result("uncertain")
        original.update(general_risk={"level": "not_observed"},
                        reason_codes=["BRAND_UNCONFIRMED"],
                        visual_evidence={"status": "BRAND_UNCONFIRMED"})
        saved = copy.deepcopy(original)
        result = combine(original, self.url(.037050573), True)
        self.assertEqual(result["enforced_decision"], "uncertain")
        self.assertEqual(result["suggested_decision"], "uncertain")
        self.assertEqual(result["original_reason_codes"], ["BRAND_UNCONFIRMED"])
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["visual"]["status"], "completed")
        self.assertEqual(result["visual"]["evidence_status"], "BRAND_UNCONFIRMED")
        result["original_reason_codes"].append("DISPLAY_ONLY")
        self.assertEqual(original, saved)

    def test_high_url_without_brand_or_form_evidence_never_blocks(self):
        for score in (.4, .962, 1.0):
            with self.subTest(score=score):
                original = self.result("uncertain")
                original.update(general_risk={"level": "not_observed"},
                                reason_codes=["BRAND_UNCONFIRMED"])
                result = combine(original, self.url(score), True)
                self.assertEqual(result["enforced_decision"], "uncertain")
                self.assertEqual(result["suggested_decision"], "uncertain")
                self.assertEqual(result["reason_codes"], ["URL_MODEL_REVIEW"])
                self.assertEqual(result["original_reason_codes"], ["BRAND_UNCONFIRMED"])

    def test_explanation_fields_do_not_change_block_or_incomplete_results(self):
        for decision, status, codes, evidence_status in [
            ("block", "completed", ["THREE_CONDITION_MATCH"], "BRAND_CONFIRMED"),
            ("uncertain", "completed", ["NO_LOGO_REGION"], "NO_LOGO_REGION"),
            ("uncertain", "failed", ["VISUAL_ANALYSIS_FAILED"], None),
            ("allow", "completed", ["WHITELIST_MATCH"], None),
        ]:
            with self.subTest(decision=decision, status=status, codes=codes):
                original = self.result(decision, status)
                original.update(reason_codes=codes, general_risk={"level": "not_observed"},
                                visual_evidence={"status": evidence_status})
                result = combine(original, self.url(.01), True)
                self.assertEqual(result["enforced_decision"], decision)
                self.assertEqual(result["suggested_decision"], decision)
                self.assertEqual(result["original_reason_codes"], codes)
                self.assertEqual(result["visual"]["evidence_status"], evidence_status)
                if codes == ["WHITELIST_MATCH"]:
                    self.assertEqual(result["visual"]["status"], "skipped")
