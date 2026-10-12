"""Relocatable, ordered reference-logo cache used by the portable build."""
import hashlib
import json
from pathlib import Path

import numpy as np


def load_reference_cache(root):
    root = Path(root).resolve()
    index = json.loads((root / 'LOGO_FILES.json').read_text(encoding='utf-8'))
    feature_path = root / 'LOGO_FEATS.npy'
    if hashlib.sha256(feature_path.read_bytes()).hexdigest() != index['features_sha256']:
        raise ValueError('Reference features do not match the ordered file index')
    files = []
    for relative in index['files']:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Missing or invalid reference logo: ' + relative)
        files.append(str(path))
    features = np.load(feature_path, allow_pickle=False)
    if features.ndim != 2 or len(features) != len(files):
        raise ValueError('Reference feature count does not match the ordered file index')
    return features, np.asarray(files)
