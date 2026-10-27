import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw
from server.visual_brand import BrandIndex, PREPROCESS_VERSION, choose_regions, inspect_regions, sha256, uniform_background_box
from server import app as service


def gallery(vectors=None):
    vectors = vectors if vectors is not None else [[1., 0.], [.999, .04471], [0., 1.]]
    return BrandIndex(vectors, [
        {'brand': name, 'domains': [name.lower()+'.test'], 'aliases': [name], 'path': str(i), 'size': [64,64]}
        for i, name in enumerate(['A', 'A', 'B'])], 'test-index')


class BrandMatchingTests(unittest.TestCase):
    def padded_symbol(self):
        image = Image.new('RGB', (80, 80), 'navy')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 59, 59), outline='white', width=2)
        draw.line((25, 25, 54, 54), fill='white', width=3)
        return image

    def test_trim_keeps_all_foreground_and_rejects_edge_touching_content(self):
        image = self.padded_symbol()
        self.assertEqual(uniform_background_box(image), [20,20,60,60])
        ImageDraw.Draw(image).rectangle((0,30,5,50), fill='white')
        self.assertIsNone(uniform_background_box(image))
        self.assertIsNone(uniform_background_box(Image.new('RGB',(80,80),'white')))

    def test_trim_can_confirm_same_identity_without_lowering_threshold(self):
        features = iter([[.8,.6],[1,0]])
        result = inspect_regions(self.padded_symbol(), [{'box':[0,0,80,80],'source':'awl_logo'}],
                                 gallery(), lambda im: next(features))
        self.assertEqual(result['hit']['name'], 'A')
        self.assertEqual(result['hit']['box'], [20,20,60,60])
        self.assertEqual(result['regions'][0]['original_box'], [0,0,80,80])
        self.assertEqual(result['threshold'], .9)

    def test_trim_cannot_change_brand_or_promote_controls(self):
        features = iter([[.8,.6],[0,1]])
        result = inspect_regions(self.padded_symbol(), [{'box':[0,0,80,80],'source':'awl_logo'}],
                                 gallery(), lambda im: next(features))
        self.assertIsNone(result['hit'])
        self.assertFalse(result['regions'][0]['crop_refinement']['identity_preserved'])
        for source in ('page_block','third_party_control'):
            result = inspect_regions(self.padded_symbol(), [{'box':[0,0,80,80],'source':source}],
                                     gallery(), lambda im:[.8,.6])
            self.assertEqual(result['crop_refinement_attempts'], 0)
            self.assertIsNone(result['hit'])

    def test_trim_attempts_are_bounded_and_do_not_accept_still_low_refinements(self):
        features = iter([[.8,.6],[.72,.69],[.8,.6],[.72,.69],[.8,.6]])
        result = inspect_regions(self.padded_symbol(),
            [{'box':[0,0,80,80],'source':'awl_logo'}]*3, gallery(), lambda im: next(features))
        self.assertEqual(result['crop_refinement_attempts'], 2)
        self.assertIsNone(result['hit'])

    def test_second_reference_of_same_brand_is_not_ambiguity(self):
        ranked = gallery().rank([1, 0])
        self.assertEqual([r['name'] for r in ranked], ['A', 'B'])
        self.assertEqual(ranked[0]['similarity'], 1)

    def test_inspects_later_logo_and_preserves_official_brand_identity(self):
        image = Image.new('RGB',(400,200),'white')
        ImageDraw.Draw(image).rectangle((110,10,140,70),fill='black')
        regions = [{'box':[0,0,64,64],'source':'awl_logo'},
                   {'box':[100,0,164,64],'source':'awl_logo'}]
        result = inspect_regions(image, regions, gallery(), lambda im: [1,0])
        self.assertEqual(result['regions'][0]['rejection'], 'FLAT_REGION')
        self.assertEqual(result['hit']['name'], 'A')
        self.assertEqual(result['hit']['box'], [100,0,164,64])

    def test_near_identical_different_brands_remain_unknown(self):
        index = gallery([[1,0],[1,0],[.999,.04471]])
        image = Image.new('RGB',(64,64),'white')
        ImageDraw.Draw(image).rectangle((1,1,30,60),fill='black')
        result = inspect_regions(image,[{'box':[0,0,64,64],'source':'awl_logo'}],index,lambda im:[1,0])
        self.assertIsNone(result['hit'])
        self.assertEqual(result['status'],'AMBIGUOUS_BRANDS')

    def test_page_blocks_and_payment_controls_cannot_confirm(self):
        image=Image.new('RGB',(64,64),'white')
        ImageDraw.Draw(image).rectangle((1,1,30,60),fill='black')
        for source in ('page_block','third_party_control'):
            result=inspect_regions(image,[{'box':[0,0,64,64],'source':source}],gallery(),lambda im:[1,0])
            self.assertIsNone(result['hit'])

    def test_saturated_solid_color_is_not_visual_logo_evidence(self):
        def unexpected(image):
            self.fail('solid color must not reach brand matching')
        result=inspect_regions(Image.new('RGB',(64,64),'blue'),
            [{'box':[0,0,64,64],'source':'dom_image'}],gallery(),unexpected)
        self.assertIsNone(result['hit'])
        self.assertEqual(result['regions'][0]['rejection'],'FLAT_REGION')

    def test_two_distinct_accepted_brands_require_review(self):
        image=Image.new('RGB',(128,64),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((1,1,30,60),fill='black');draw.rectangle((65,1,95,60),fill='black')
        features=iter([[1,0],[0,1]])
        result=inspect_regions(image,[{'box':[0,0,64,64],'source':'awl_logo'},
            {'box':[64,0,128,64],'source':'awl_logo'}],gallery(),lambda im:next(features))
        self.assertEqual(result['status'],'MULTIPLE_BRANDS')
        self.assertIsNone(result['hit'])

    def test_dpi_scaling_deduplicates_boxes_and_excludes_partial_logo(self):
        result=choose_regions((1000,800),[[20,20,148,148]],[],
            [{'box':[10,10,74,74]},{'box':[-10,2,30,50]}],{'width':500,'height':400})
        self.assertEqual(result,[{'box':[20,20,148,148],'source':'dom_image'}])

    def test_dom_images_do_not_crowd_out_later_detector_logos(self):
        dom = [{'box': [20+i*90, 20, 60+i*90, 60]} for i in range(8)]
        logos = [[20+i*90, 200, 60+i*90, 240] for i in range(5)]
        result = choose_regions((1000, 800), logos, dom_regions=dom,
                                viewport={'width': 1000, 'height': 800})
        self.assertEqual(len(result), 10)
        self.assertIn(logos[4], [r['box'] for r in result])

    def test_control_annotation_survives_duplicate_dom_and_detector_boxes(self):
        box = [20, 20, 84, 84]
        for dom in ([{'box': box}, {'box': box, 'interactive': True}],
                    [{'box': box, 'interactive': True}, {'box': box}]):
            result = choose_regions((800, 600), [box], dom_regions=dom,
                                    viewport={'width': 800, 'height': 600})
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['source'], 'third_party_control')

    def test_invalid_dom_regions_do_not_suppress_diagnostic_fallback(self):
        result = choose_regions((800, 600), [], [[10, 10, 100, 100]],
                                [{'box': [-100, 0, -20, 80]}],
                                {'width': 800, 'height': 600})
        self.assertEqual(result, [{'box': [10, 10, 100, 100], 'source': 'page_block'}])

    def test_credential_link_uses_other_confirmed_logo_of_same_brand(self):
        image = Image.new('RGB', (800, 600), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((11, 11, 40, 70), fill='black')
        draw.rectangle((211, 211, 240, 270), fill='black')
        features = iter([[1, 0], [.98, .199]])
        evidence = inspect_regions(image, [
            {'box': [10, 10, 74, 74], 'source': 'awl_logo'},
            {'box': [210, 210, 274, 274], 'source': 'awl_logo'}],
            gallery(), lambda im: next(features))
        evidence['image_size'] = [800, 600]
        self.assertEqual(evidence['hit']['box'], [10, 10, 74, 74])
        ds = service.DomSignals(page_domain='merchant.test', has_password_input=True,
            viewport={'width': 800, 'height': 600}, credential_contexts=[
                {'box': [180, 180, 450, 500], 'text': 'Sign in to Brand A'}])
        # A one-character brand is intentionally insufficient for text linking.
        evidence['hit']['aliases'] = ['Brand A']
        for match in evidence.get('matches', []):
            match['aliases'] = ['Brand A']
        self.assertTrue(service._credential_brand_link(ds, evidence))

    def test_diagnostic_or_other_brand_logo_cannot_link_credentials(self):
        hit = {'name': 'Brand A', 'box': [10, 10, 74, 74], 'source': 'awl_logo'}
        ds = service.DomSignals(page_domain='merchant.test', has_password_input=True,
            viewport={'width': 800, 'height': 600}, credential_contexts=[
                {'box': [180, 180, 450, 500], 'text': 'Brand A and Brand B login'}])
        for name, source in [('Brand A', 'third_party_control'),
                             ('Brand A', 'page_block'), ('Brand B', 'awl_logo')]:
            evidence = {'hit': hit, 'image_size': [800, 600], 'matches': [
                {'name': name, 'box': [210, 210, 274, 274], 'source': source}]}
            self.assertFalse(service._credential_brand_link(ds, evidence))

    def test_missing_form_association_does_not_accuse_brand(self):
        evidence={'hit': {'name':'支付宝','aliases':['支付宝','Alipay'],'box':[50,50,100,100]},
                  'image_size':[800,600]}
        base={'page_domain':'merchant.test','has_password_input':True,'viewport':{'width':800,'height':600}}
        for context in ([],[{'box':[0,0,400,500],'text':'商店会员登录'}],
                        [{'box':[400,0,700,500],'text':'支付宝登录'}]):
            self.assertFalse(service._credential_brand_link(service.DomSignals(**base,credential_contexts=context),evidence))
        self.assertTrue(service._credential_brand_link(service.DomSignals(**base,credential_contexts=[
            {'box':[0,0,400,500],'text':'欢迎登录支付宝安全中心'}]),evidence))


class ReferenceIntegrityTests(unittest.TestCase):
    def test_replaced_image_same_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); target=root/'brand_library/siamese';target.mkdir(parents=True)
            Image.new('RGB',(64,64),'white').save(root/'logo.png')
            (root/'model.pth').write_bytes(b'model')
            np.save(target/'features.npy', np.asarray([[1.,0.]],dtype=np.float32))
            manifest={'preprocess':PREPROCESS_VERSION,'feature_file':'features.npy',
                'features_sha256':sha256(target/'features.npy'),
                'models':[{'path':'model.pth','sha256':sha256(root/'model.pth')}],
                'records':[{'brand':'A','domains':['a.test'],'path':'logo.png',
                            'size':[64,64],'sha256':sha256(root/'logo.png')}]}
            (target/'references.json').write_text(json.dumps(manifest),encoding='utf-8')
            self.assertEqual(BrandIndex.load(root).records[0]['brand'],'A')
            Image.new('RGB',(64,64),'black').save(root/'logo.png')
            with self.assertRaisesRegex(ValueError,'reference changed'):
                BrandIndex.load(root)


if __name__=='__main__':
    unittest.main()
