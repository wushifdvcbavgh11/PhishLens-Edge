"""Lifecycle protocol tests; model loading is mocked, not claimed as real inference."""
import os,threading,time,unittest
from unittest.mock import patch
from types import SimpleNamespace
from server import app as s

class WarmupTests(unittest.TestCase):
    def setUp(self):
        for name,value in [('MODELS_READY',False),('MODELS_LOAD_ERROR',None),('_MODELS',{}),
            ('_MODEL_STATE','uninitialized'),('_MODEL_STARTED',None),('_MODEL_FINISHED',None),
            ('_MODELS_LAST_FAILURE',float('-inf')),('_MODEL_WARMUP_THREAD',None)]:
            p=patch.object(s,name,value);p.start();self.addCleanup(p.stop)

    def test_health_distinguishes_uninitialized_from_failed(self):
        self.assertEqual(s.health()['model_initialization']['state'],'uninitialized')
        self.assertIsNone(s.health()['models_load_error'])

    def test_background_loading_is_nonblocking_and_not_prematurely_ready(self):
        release=threading.Event();entered=threading.Event()
        def init():
            entered.set();release.wait(2)
            return {'brand_index':SimpleNamespace(version='test'),'load_stages_s':{'awl':.1}}
        with patch.object(s,'_initialize_models',side_effect=init) as mock:
            try:
                start=time.monotonic();s.start_model_warmup();self.assertLess(time.monotonic()-start,.5)
                self.assertTrue(entered.wait(1));h=s.health()
                self.assertFalse(h['models_ready']);self.assertEqual(h['model_initialization']['state'],'loading')
                for _ in range(5):s.start_model_warmup()
                self.assertEqual(mock.call_count,1)
            finally:release.set();s._MODEL_WARMUP_THREAD.join(2)
        self.assertTrue(s.health()['models_ready']);self.assertEqual(s.health()['model_initialization']['state'],'ready')

    def test_failure_cooldown_and_recovery_are_visible(self):
        with patch.object(s,'_initialize_models',side_effect=[OSError('missing weight'),{'brand_index':SimpleNamespace(version='test')}]) as mock:
            s.start_model_warmup();s._MODEL_WARMUP_THREAD.join(2)
            self.assertEqual(s.health()['model_initialization']['state'],'failed')
            self.assertIn('missing weight',s.health()['models_load_error'])
            self.assertGreater(s.health()['model_initialization']['retry_after_s'],0)
            s.start_model_warmup();self.assertEqual(mock.call_count,1)
            s._MODELS_LAST_FAILURE-=31
            s.start_model_warmup();s._MODEL_WARMUP_THREAD.join(2)
            self.assertTrue(s.health()['models_ready']);self.assertIsNone(s.health()['models_load_error'])

    def test_initializer_never_changes_process_working_directory(self):
        with patch.object(s.os,'chdir') as change,patch.object(s.yaml,'safe_load',side_effect=ValueError('test')):
            with self.assertRaises(ValueError):s._initialize_models()
        change.assert_not_called()

    def test_warmup_http_does_not_infer_or_claim_a_page_verdict(self):
        from fastapi.testclient import TestClient
        with patch.object(s,'start_model_warmup') as warm,patch.object(s,'_analyze_original') as infer:
            r=TestClient(s.app).post('/warmup').json()
            self.assertEqual(r['model_initialization']['state'],'uninitialized')
            self.assertNotIn('decision',r);infer.assert_not_called();warm.assert_called_once()
