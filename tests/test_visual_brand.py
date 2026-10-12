import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw
from server.visual_brand import BrandIndex, PREPROCESS_VERSION, choose_regions, inspect_regions, sha256
from server import app as service


def gallery(vectors=None):
    vectors = vectors if vectors is not None else [[1., 0.], [.999, .04471], [0., 1.]]
    return BrandIndex(vectors, [
        {'brand': name, 'domains': [name.lower()+'.test'], 'aliases': [name], 'path': str(i), 'size': [64,64]}
        for i, name in enumerate(['A', 'A', 'B'])], 'test-index')


class BrandMatchingTests(unittest.TestCase):
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
