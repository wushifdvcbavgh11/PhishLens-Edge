"""Rebuild all reference embeddings offline with an explicit, hashed row index."""
import argparse
import json
import os
from pathlib import Path
import pickle
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'third_party/PhishIntention/src')]
from visual_brand import PREPROCESS_VERSION, rgb_image, sha256


def main():
    import numpy as np
    import torch
    from PIL import Image
    from phishintention.modules.logo_matching import (
        siamese_model_config, ocr_model_config, get_ocr_aided_siamese_embedding)
    from phishintention.utils.brand_utils import brand_converter
    torch.set_num_threads(2)
    upstream = ROOT / 'third_party/PhishIntention'
    with (upstream / 'models/domain_map.pkl').open('rb') as stream:
        domains = pickle.load(stream)
    metadata = json.loads((ROOT / 'brand_library/brands.json').read_text(encoding='utf-8'))
    records = []
    for folder in sorted((upstream / 'models/expand_targetlist').iterdir()):
        if not folder.is_dir() or folder.name.startswith('.'):
            continue
        brand = brand_converter(folder.name)
        if brand not in domains:
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in ('.png', '.jpg', '.jpeg') and not path.name.startswith(('loginpage', 'homepage', '.')):
                records.append({'brand': brand, 'aliases': [brand], 'domains': domains[brand], 'path': path.relative_to(ROOT).as_posix()})
    for brand in metadata:
        paths = set()
        for kind in ('logos', 'logos_web'):
            folder = ROOT / 'brand_library' / kind / brand['id']
            if folder.exists():
                paths.update(p for p in folder.iterdir() if p.suffix.lower() in ('.png','.jpg','.jpeg'))
        for path in sorted(paths):
            records.append({'brand': brand['name_cn'], 'aliases': [brand['name_cn'], brand['name'], brand['id']],
                            'domains': brand['domains'], 'path': path.relative_to(ROOT).as_posix()})
    # Merge aliases of existing English brands so the same identity cannot compete
    # with itself merely because a supplemental library uses a translated name.
    alias_map = {}
    for brand in metadata:
        for alias in (brand['name'], brand['name_cn'], brand['id']):
            alias_map[alias.casefold()] = brand
    for record in records:
        canonical = alias_map.get(record['brand'].casefold())
        if canonical:
            record.update(brand=canonical['name_cn'], domains=canonical['domains'],
                          aliases=[canonical['name_cn'],canonical['name'],canonical['id']])
    model_paths = [upstream / 'models/ocr_siamese.pth.tar', upstream / 'models/ocr_pretrained.pth.tar']
    model = siamese_model_config(277, str(model_paths[0]))
    ocr = ocr_model_config(str(model_paths[1]))
    features = []
    for i, record in enumerate(records):
        path = ROOT / record['path']
        with Image.open(path) as im:
            im = rgb_image(im)
            record['size'] = list(im.size)
            features.append(get_ocr_aided_siamese_embedding(im, model, ocr))
        record['sha256'] = sha256(path)
        if i % 100 == 0:
            print(f'{i+1}/{len(records)} reference embeddings', flush=True)
    folder = ROOT / 'brand_library/siamese'
    folder.mkdir(exist_ok=True)
    temporary = folder / 'features.build.npy'
    np.save(temporary, np.asarray(features, dtype=np.float32))
    digest = sha256(temporary)
    feature_path = folder / ('features-' + digest[:16] + '.npy')
    os.replace(temporary, feature_path)
    manifest = {'schema': 1, 'preprocess': PREPROCESS_VERSION, 'feature_file': feature_path.name,
                'features_sha256': digest, 'records': records,
                'models': [{'path': p.relative_to(ROOT).as_posix(), 'sha256': sha256(p)} for p in model_paths]}
    temporary_manifest = folder / 'references.build.json'
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary_manifest, folder / 'references.json')
    print(json.dumps({'references': len(records), 'brands': len({r['brand'] for r in records}),
                      'version': digest[:16]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
