"""Versioned Logo references and bounded, domain-independent visual matching.

No image/title/alt text supplied by a page can establish a brand on its own.
Only screenshot pixels are embedded. Scores are similarities, not probabilities.
"""
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

PREPROCESS_VERSION = 'siamese-rgba-white-square-v1'
MATCH_THRESHOLD = 0.90
BRAND_MARGIN = 0.04
MAX_REGIONS = 10


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def rgb_image(image):
    rgba = image.convert('RGBA')
    background = Image.new('RGBA', rgba.size, 'white')
    return Image.alpha_composite(background, rgba).convert('RGB')


class BrandIndex:
    def __init__(self, features, records, version):
        self.features = np.asarray(features, dtype=np.float32)
        self.records = records
        self.version = version
        self.root = None
        self.aligned_features = OrderedDict()
        if self.features.ndim != 2 or len(self.features) != len(records) or not len(records):
            raise ValueError('Brand reference count/shape mismatch')
        if not np.isfinite(self.features).all():
            raise ValueError('Non-finite brand features')
        norms = np.linalg.norm(self.features, axis=1)
        if not np.allclose(norms, 1, atol=0.01):
            raise ValueError('Brand features must be normalized')

    @classmethod
    def load(cls, root):
        root = Path(root).resolve()
        folder = root / 'brand_library/siamese'
        manifest = json.loads((folder / 'references.json').read_text(encoding='utf-8'))
        if manifest['preprocess'] != PREPROCESS_VERSION:
            raise ValueError('Brand preprocessing version mismatch')
        feature_path = folder / manifest['feature_file']
        if feature_path.resolve().parent != folder.resolve() or sha256(feature_path) != manifest['features_sha256']:
            raise ValueError('Brand feature hash mismatch')
        for item in [*manifest['models'], *manifest['records']]:
            path = (root / item['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file() or sha256(path) != item['sha256']:
                raise ValueError('Brand model/reference changed: ' + item['path'])
        index = cls(np.load(feature_path, allow_pickle=False), manifest['records'], manifest['features_sha256'][:16])
        index.root = root
        return index

    def rank(self, feature):
        feature = np.asarray(feature, dtype=np.float32).reshape(-1)
        if feature.shape[0] != self.features.shape[1] or not np.isfinite(feature).all():
            raise ValueError('Invalid query embedding')
        feature /= max(float(np.linalg.norm(feature)), 1e-9)
        scores = self.features @ feature
        # Aggregate images BEFORE computing the top-two different-brand margin.
        brands = {}
        for i in np.argsort(scores)[::-1]:
            record = self.records[int(i)]
            if record['brand'] not in brands:
                brands[record['brand']] = {
                    'name': record['brand'], 'domains': record['domains'],
                    'aliases': record.get('aliases', [record['brand']]),
                    'similarity': round(float(scores[i]), 5),
                    'reference': record['path'], 'size': record['size'],
                }
            if len(brands) == 3:
                break
        return list(brands.values())

    def align_resolution(self, crop, candidates, embed):
        """Compare shortlist references at a shared resolution, without lowering gates.

        Both sides retain their aspect ratio. A 64px short-edge cap also avoids
        treating browser/DPI upscaling as newly available visual detail. All three
        different-brand candidates get the same refinement opportunity.
        """
        if self.root is None or candidates[0]['similarity'] < .75:
            return candidates
        aligned = []
        query_features = {}
        for candidate in candidates:
            item = dict(candidate)
            item['raw_similarity'] = item['similarity']
            short = min(64, min(crop.size), min(item['size']))
            def dimensions(size):
                scale = short / min(size)
                return tuple(max(1, round(side * scale)) for side in size)
            query_size = dimensions(crop.size)
            reference_size = dimensions(item['size'])
            if query_size not in query_features:
                query_features[query_size] = embed(crop.resize(query_size, Image.Resampling.LANCZOS))
            key = (item['reference'], reference_size)
            if key not in self.aligned_features:
                with Image.open(self.root / item['reference']) as reference:
                    self.aligned_features[key] = embed(rgb_image(reference).resize(reference_size, Image.Resampling.LANCZOS))
                if len(self.aligned_features) > 128:
                    self.aligned_features.popitem(last=False)
            self.aligned_features.move_to_end(key)
            similarity = float(np.dot(query_features[query_size], self.aligned_features[key]))
            item['aligned_similarity'] = round(similarity, 5)
            item['similarity'] = max(item['similarity'], item['aligned_similarity'])
            aligned.append(item)
        return sorted(aligned, key=lambda item: item['similarity'], reverse=True)


def choose_regions(size, logo_boxes, block_boxes=(), dom_regions=(), viewport=None):
    width, height = size
    proposed = []
    # DOM coordinates only locate pixels; their labels are never recognition evidence.
    if viewport and viewport.get('width', 0) > 0 and viewport.get('height', 0) > 0:
        sx, sy = width / viewport['width'], height / viewport['height']
        if abs(sx / sy - 1) < 0.05:
            for region in dom_regions[:8]:
                box = region.get('box', [])
                if len(box) == 4:
                    source = 'third_party_control' if region.get('interactive') else 'dom_image'
                    proposed.append((source, [box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy]))
    proposed.extend(('awl_logo', box) for box in logo_boxes)
    # Blocks remain diagnostic-only. Never promote an arbitrary page block.
    if not proposed:
        proposed.extend(('page_block', box) for box in block_boxes if float(box[1]) < height * .45)
    selected = []
    controls = [box for source, box in proposed if source == 'third_party_control']
    for source, raw in proposed:
        raw = [float(x) for x in raw]
        if len(raw) != 4 or not all(np.isfinite(raw)):
            continue
        x1, y1, x2, y2 = raw
        if source == 'awl_logo' and any(a <= (x1+x2)/2 <= c and b <= (y1+y2)/2 <= d
                                       for a,b,c,d in controls):
            source = 'third_party_control'
        # Partial logos at a viewport boundary are not silently treated as complete.
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
            continue
        w, h = x2-x1, y2-y1
        if min(w, h) < 12 or max(w, h) > max(width, height)*.65 or w*h > width*height*.25:
            continue
        box = [int(x1), int(y1), int(x2), int(y2)]
        duplicate = False
        for existing in selected:
            a, b, c, d = existing['box']
            intersection = max(0, min(x2,c)-max(x1,a))*max(0,min(y2,d)-max(y1,b))
            union = w*h + (c-a)*(d-b) - intersection
            if union and intersection/union > .85:
                duplicate = True
                break
        if not duplicate:
            selected.append({'box': box, 'source': source})
        if len(selected) >= MAX_REGIONS:
            break
    return selected


def inspect_regions(image, regions, index, embed):
    diagnostics, accepted = [], []
    for region in regions:
        crop = rgb_image(image.crop(region['box']))
        record = {**region, 'candidates': [], 'rejection': None}
        if float(np.asarray(crop).std(axis=(0, 1)).mean()) < 12:
            record['rejection'] = 'FLAT_REGION'
        else:
            candidates = index.rank(embed(crop))
            if region['source'] not in ('page_block', 'third_party_control'):
                candidates = index.align_resolution(crop, candidates, embed)
            record['candidates'] = candidates
            best = candidates[0]
            margin = best['similarity'] - (candidates[1]['similarity'] if len(candidates) > 1 else 0)
            record['brand_margin'] = round(margin, 5)
            crop_ratio = crop.width / crop.height
            reference_ratio = best['size'][0] / best['size'][1]
            if region['source'] == 'third_party_control':
                record['rejection'] = 'THIRD_PARTY_CONTROL'
            elif region['source'] == 'page_block':
                record['rejection'] = 'PAGE_BLOCK_ONLY'
            elif best['similarity'] < MATCH_THRESHOLD:
                record['rejection'] = 'LOW_SIMILARITY'
            elif margin < BRAND_MARGIN:
                record['rejection'] = 'AMBIGUOUS_BRANDS'
            elif max(crop_ratio, reference_ratio)/min(crop_ratio, reference_ratio) > 2.5:
                record['rejection'] = 'ASPECT_RATIO_MISMATCH'
            else:
                accepted.append({**best, 'box': region['box'], 'source': region['source']})
        diagnostics.append(record)
    identities = {hit['name'] for hit in accepted}
    status = ('NO_LOGO_REGION' if not regions else 'BRAND_UNCONFIRMED')
    hit = None
    if len(identities) > 1:
        status = 'MULTIPLE_BRANDS'
    elif accepted:
        status = 'BRAND_CONFIRMED'
        hit = max(accepted, key=lambda item: item['similarity'])
    elif any(item['rejection'] == 'AMBIGUOUS_BRANDS' for item in diagnostics):
        status = 'AMBIGUOUS_BRANDS'
    return {'hit': hit, 'status': status, 'regions': diagnostics,
            'reference_version': index.version,
            'threshold': MATCH_THRESHOLD, 'brand_margin_required': BRAND_MARGIN}
