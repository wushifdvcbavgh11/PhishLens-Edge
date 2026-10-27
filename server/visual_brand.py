"""Versioned Logo references and bounded, domain-independent visual matching.

No image/title/alt text supplied by a page can establish a brand on its own.
Only screenshot pixels are embedded. Scores are similarities, not probabilities.
"""
import hashlib
import json
from collections import OrderedDict
from itertools import zip_longest
from pathlib import Path

import numpy as np
from PIL import Image

PREPROCESS_VERSION = 'siamese-rgba-white-square-v1'
MATCH_THRESHOLD = 0.90
BRAND_MARGIN = 0.04
MAX_REGIONS = 10
MAX_CROP_REFINEMENTS = 2


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


def uniform_background_box(image, *, min_retained=.20):
    """Tighten a padded logo without selecting characters or connected parts.

    Only trim a consistent, near-uniform outer background. Keep the bounding
    rectangle of ALL contrasting pixels; textured/edge-touching crops abstain.
    Coordinates are relative to the supplied image and the right edge is exclusive.
    """
    pixels = np.asarray(rgb_image(image), dtype=np.int16)
    height, width = pixels.shape[:2]
    if min(width, height) < 16:
        return None
    corners = pixels[[0, 0, -1, -1], [0, -1, 0, -1]]
    background = np.median(corners, axis=0)
    edge = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    if np.mean(np.max(np.abs(edge-background), axis=1) <= 16) < .95:
        return None
    ys, xs = np.where(np.max(np.abs(pixels-background), axis=2) > 24)
    if len(xs) < 24:
        return None
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)
    if min(x1, y1, width-x2, height-y2) < 2 or min(x2-x1, y2-y1) < 12:
        return None
    retained = (x2-x1)*(y2-y1)/(width*height)
    if not min_retained <= retained <= .85:
        return None
    return [x1, y1, x2, y2]


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

    def foreground_aspect_evidence(self, crop, best, embed):
        """Recheck a padded reference, retaining the original identity/score gates."""
        if self.root is None:
            return None
        with Image.open(self.root / best['reference']) as image:
            reference = rgb_image(image)
        box = uniform_background_box(reference, min_retained=.05)
        if box is None:
            return None
        reference = reference.crop(box)
        ratios = (crop.width / crop.height, reference.width / reference.height)
        ratio_factor = max(ratios) / min(ratios)
        result = {'reference_content_box': box, 'ratio_factor': round(ratio_factor, 5),
                  'similarity': None, 'accepted': False}
        if ratio_factor > 2.5:
            return result
        short = min(64, min(crop.size), min(reference.size))
        def resize(image):
            scale = short / min(image.size)
            return image.resize(tuple(max(1, round(v*scale)) for v in image.size), Image.Resampling.LANCZOS)
        similarity = float(np.dot(embed(resize(crop)), embed(resize(reference))))
        result['similarity'] = round(similarity, 5)
        result['accepted'] = similarity >= MATCH_THRESHOLD
        return result


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

    def valid_box(raw):
        raw = [float(x) for x in raw]
        if len(raw) != 4 or not all(np.isfinite(raw)):
            return None
        x1, y1, x2, y2 = raw
        # Partial logos at a viewport boundary are not silently treated as complete.
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
            return None
        w, h = x2-x1, y2-y1
        if min(w, h) < 12 or max(w, h) > max(width, height)*.65 or w*h > width*height*.25:
            return None
        return [int(x1), int(y1), int(x2), int(y2)]

    valid = []
    for source, raw in proposed:
        box = valid_box(raw)
        if box is not None:
            valid.append((source, box))
    # Classify before deduplication/budgeting. A duplicate DOM crop must not
    # erase a control annotation and turn a payment badge into page identity.
    controls = [box for source, box in valid if source == 'third_party_control']
    groups = {'dom_image': [], 'awl_logo': [], 'third_party_control': []}
    for source, box in valid:
        x1, y1, x2, y2 = box
        if any(a <= (x1+x2)/2 <= c and b <= (y1+y2)/2 <= d for a,b,c,d in controls):
            source = 'third_party_control'
        groups[source].append((source, box))
    # Share the fixed inference budget between independent proposal sources.
    # Unrelated DOM images must not consume eight of ten slots before AWL runs.
    ordered = [item for pair in zip_longest(groups['dom_image'], groups['awl_logo'])
               for item in pair if item is not None]
    ordered.extend(groups['third_party_control'])
    # Blocks remain diagnostic-only, including when every proposal was invalid.
    if not valid:
        for raw in block_boxes:
            box = valid_box(raw)
            if box is not None and box[1] < height * .45:
                ordered.append(('page_block', box))
    selected = []
    for source, box in ordered:
        x1, y1, x2, y2 = box
        w, h = x2-x1, y2-y1
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


def mark_auxiliary_toolbars(image, regions, control_boxes):
    """Abstain on a small logo in a separated control strip above a larger logo.

    Pure geometry/pixels, not a brand or URL exception. Requires multiple buttons,
    a near-full-width separator and a larger independent logo below the strip.
    This only removes identity evidence; it never grants a safe/allow decision.
    """
    pixels = np.asarray(rgb_image(image), dtype=np.int16)
    height, width = pixels.shape[:2]
    output = []
    for region in regions:
        item = dict(region)
        x1,y1,x2,y2 = region['box']
        w,h = x2-x1,y2-y1
        if (region['source'] in ('awl_logo','dom_image') and y2 <= height*.08
                and h <= height*.045 and w <= width*.15):
            buttons = [b for b in control_boxes if len(b)==4 and b[0]>=x2
                       and 0 < b[3]-b[1] <= height*.06
                       and abs((b[1]+b[3])/2-(y1+y2)/2) <= h*.5]
            larger = [r for r in regions if r['source'] in ('awl_logo','dom_image')
                      and r['box'][1] >= y2+h*.5 and r['box'][1] < height*.35
                      and r['box'][3]-r['box'][1] >= h*1.5
                      and (r['box'][2]-r['box'][0])*(r['box'][3]-r['box'][1]) >= w*h*3]
            if len(buttons)>=2 and larger:
                end = min(int(height*.10), min(r['box'][1] for r in larger), height-4)
                for y in range(max(0,y2), end):
                    row = pixels[y]
                    uniform = np.max(np.quantile(row,.95,axis=0)-np.quantile(row,.05,axis=0)) <= 20
                    boundary = np.mean(np.max(np.abs(row-pixels[y+3]),axis=1)>=8) >= .85
                    if uniform and boundary:
                        item.update(source='auxiliary_toolbar', original_source=region['source'],
                                    toolbar_separator_y=y, identity_exclusion='control_strip_above_larger_logo')
                        break
        output.append(item)
    return output


def inspect_regions(image, regions, index, embed, *, refine=True):
    diagnostics, accepted = [], []
    refinement_attempts = 0
    aspect_attempts = 0
    for region in regions:
        crop = rgb_image(image.crop(region['box']))
        record = {**region, 'candidates': [], 'rejection': None}
        if float(np.asarray(crop).std(axis=(0, 1)).mean()) < 12:
            record['rejection'] = 'FLAT_REGION'
        else:
            candidates = index.rank(embed(crop))
            if region['source'] not in ('page_block', 'third_party_control', 'auxiliary_toolbar'):
                candidates = index.align_resolution(crop, candidates, embed)
            record['candidates'] = candidates
            best = candidates[0]
            margin = best['similarity'] - (candidates[1]['similarity'] if len(candidates) > 1 else 0)
            record['brand_margin'] = round(margin, 5)
            crop_ratio = crop.width / crop.height
            reference_ratio = best['size'][0] / best['size'][1]
            if region['source'] == 'third_party_control':
                record['rejection'] = 'THIRD_PARTY_CONTROL'
            elif region['source'] == 'auxiliary_toolbar':
                record['rejection'] = 'AUXILIARY_TOOLBAR'
            elif region['source'] == 'page_block':
                record['rejection'] = 'PAGE_BLOCK_ONLY'
            elif best['similarity'] < MATCH_THRESHOLD:
                record['rejection'] = 'LOW_SIMILARITY'
            elif margin < BRAND_MARGIN:
                record['rejection'] = 'AMBIGUOUS_BRANDS'
            elif max(crop_ratio, reference_ratio)/min(crop_ratio, reference_ratio) > 2.5:
                record['rejection'] = 'ASPECT_RATIO_MISMATCH'
                if refine and aspect_attempts < MAX_CROP_REFINEMENTS:
                    aspect_attempts += 1
                    aspect = index.foreground_aspect_evidence(crop, best, embed)
                    record['reference_aspect_check'] = aspect
                    if aspect and aspect['similarity'] is not None:
                        aspect['brand_margin'] = round(aspect['similarity'] - (candidates[1]['similarity'] if len(candidates)>1 else 0), 5)
                        aspect['accepted'] = aspect['accepted'] and aspect['brand_margin'] >= BRAND_MARGIN
                    if aspect and aspect['accepted']:
                        record['rejection'] = None
                        accepted.append({**best, 'box':region['box'], 'source':region['source']})
            else:
                accepted.append({**best, 'box': region['box'], 'source': region['source']})
        # A fallback for padded, otherwise plausible logo crops. Never refine
        # page blocks, controls, ambiguous identities or already accepted logos.
        if (refine and refinement_attempts < MAX_CROP_REFINEMENTS
                and record['rejection'] == 'LOW_SIMILARITY'
                and region['source'] in ('awl_logo', 'dom_image')
                and record['candidates'][0]['similarity'] >= .75):
            relative = uniform_background_box(crop)
            if relative is not None:
                refinement_attempts += 1
                x, y = region['box'][:2]
                box = [relative[0]+x, relative[1]+y, relative[2]+x, relative[3]+y]
                refined = inspect_regions(image, [{'box': box, 'source': region['source']}],
                                          index, embed, refine=False)
                attempt = refined['regions'][0]
                hit = refined['hit']
                same_identity = bool(attempt['candidates'] and
                    attempt['candidates'][0]['name'] == record['candidates'][0]['name'])
                record['crop_refinement'] = {**attempt,
                    'accepted': bool(hit and same_identity),
                    'identity_preserved': same_identity}
                if hit and same_identity:
                    record.update(original_box=region['box'],
                                  original_candidates=record['candidates'],
                                  box=box, candidates=attempt['candidates'],
                                  brand_margin=attempt['brand_margin'], rejection=None)
                    accepted.append(hit)
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
    return {'hit': hit, 'matches': accepted, 'status': status, 'regions': diagnostics,
            'reference_version': index.version,
            'threshold': MATCH_THRESHOLD, 'brand_margin_required': BRAND_MARGIN,
            'crop_refinement_attempts': refinement_attempts,
            'reference_aspect_attempts': aspect_attempts}
