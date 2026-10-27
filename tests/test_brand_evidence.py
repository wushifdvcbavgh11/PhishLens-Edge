import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from brand_evidence import summarize_brand_evidence
from shadow_fusion import combine


class BrandEvidenceTests(unittest.TestCase):
    def result(self, brand='微信'):
        candidate = dict(name=brand, similarity=.96, domains=['wechat.com'], reference='reference.png')
        return dict(decision='block', analysis_status='completed', brand=brand,
                    official_domain='wechat.com', reason_codes=['BRAND_DOMAIN_MISMATCH'],
                    visual_evidence=dict(hit=candidate, reference_version='verified-index',
                        threshold=.9, brand_margin_required=.04, image_size=[1280,900],
                        regions=[dict(box=[10,20,90,100], source='dom_image', rejection=None,
                                      candidates=[candidate])]))

    def test_each_brand_uses_its_actual_reference_and_preserves_decision(self):
        for brand in ('支付宝', '微信', '工商银行', 'PayPal'):
            original = self.result(brand)
            before = copy.deepcopy(original)
            result = combine(original, {'status':'unavailable'}, True, 'http://127.0.0.1:8001/')
            self.assertEqual(result['visual']['brand'], brand)
            self.assertEqual(result['visual']['similarity'], .96)
            self.assertEqual(result['visual']['domain_check'], 'mismatch')
            self.assertEqual(result['suggested_decision'], 'block')
            result['visual']['candidates'][0]['domains'].append('display-change')
            self.assertEqual(original, before)

    def test_unknown_nearest_neighbour_is_not_confirmed(self):
        original = self.result()
        original.update(brand=None, official_domain=None, decision='uncertain')
        original['visual_evidence']['hit'] = None
        original['visual_evidence']['regions'][0]['rejection'] = 'LOW_SIMILARITY'
        view = summarize_brand_evidence(original, 'http://127.0.0.1/', True)
        self.assertIsNone(view['brand'])
        self.assertIsNone(view['similarity'])
        self.assertEqual(view['official_domains'], [])
        self.assertEqual(view['domain_check'], 'not_evaluated')
        self.assertEqual(view['candidates'][0]['rejection'], 'LOW_SIMILARITY')

    def test_failed_skipped_or_incomplete_cannot_revive_stale_identity(self):
        for state in ('failed','degraded','skipped'):
            original = self.result()
            if state == 'skipped': original['reason_codes'] = ['WHITELIST_MATCH']
            else: original['analysis_status'] = state
            view = summarize_brand_evidence(original, 'https://wechat.com/', True)
            self.assertIsNone(view['brand'])
            self.assertEqual(view['candidates'], [])
            self.assertEqual(view['domain_check'], 'not_evaluated')

    def test_domain_boundaries_and_incomplete_context(self):
        for host,expected in [('wechat.com','match'),('login.wechat.com','match'),
                              ('fakewechat.com','mismatch'),('wechat.com.evil.test','mismatch')]:
            self.assertEqual(summarize_brand_evidence(self.result(), 'https://'+host, True)['domain_check'], expected)
        self.assertEqual(summarize_brand_evidence(self.result(), 'https://wechat.com', False)['domain_check'], 'not_evaluated')

    def test_candidates_deduplicated_bounded_and_controls_not_promoted(self):
        original = self.result()
        region = original['visual_evidence']['regions'][0]
        region['rejection'] = 'THIRD_PARTY_CONTROL'
        region['source'] = 'third_party_control'
        region['candidates'] = [dict(name=n,similarity=s,domains=[],reference=n) for n,s in [('A',.99),('B',.98),('C',.97)]]
        original['visual_evidence']['regions'] = [region]*15
        view = summarize_brand_evidence(original, '', True)
        self.assertEqual(len(view['regions']),10)
        self.assertEqual(len(view['candidates']),3)
        self.assertEqual(view['brand'],'微信')
        self.assertEqual(view['similarity'],.96)
        self.assertEqual(view['candidates'][0]['rejection'],'THIRD_PARTY_CONTROL')

    def test_legacy_result_without_evidence_does_not_invent_similarity(self):
        original = self.result()
        del original['visual_evidence']
        self.assertIsNone(summarize_brand_evidence(original, '', False)['similarity'])


if __name__ == '__main__':
    unittest.main()
