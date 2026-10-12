import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from server.portable_assets import load_reference_cache


class PortableCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        np.save(self.root / 'LOGO_FEATS.npy', np.asarray([[1, 0], [0, 1]]))
        self.index = {'features_sha256': hashlib.sha256((self.root / 'LOGO_FEATS.npy').read_bytes()).hexdigest(),
                      'files': ['z.png', 'a.png']}
        for name in self.index['files']:
            (self.root / name).write_bytes(b'fixture')

    def load(self):
        (self.root / 'LOGO_FILES.json').write_text(json.dumps(self.index), encoding='utf-8')
        return load_reference_cache(self.root)

    def test_reference_order_is_independent_of_directory_order(self):
        features, paths = self.load()
        self.assertEqual([Path(p).name for p in paths], ['z.png', 'a.png'])
        self.assertEqual(features.tolist(), [[1, 0], [0, 1]])

    def test_changed_features_fail_closed(self):
        np.save(self.root / 'LOGO_FEATS.npy', np.asarray([[0, 1], [1, 0]]))
        with self.assertRaisesRegex(ValueError, 'do not match'):
            self.load()

    def test_missing_and_outside_reference_paths_are_rejected(self):
        for invalid in ('missing.png', '../outside.png'):
            self.index['files'][0] = invalid
            with self.assertRaisesRegex(ValueError, 'Missing or invalid'):
                self.load()

    def test_feature_count_mismatch_is_rejected(self):
        self.index['files'] = ['a.png']
        with self.assertRaisesRegex(ValueError, 'count'):
            self.load()


if __name__ == '__main__':
    unittest.main()
