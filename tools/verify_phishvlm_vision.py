# -*- coding: utf-8 -*-
"""T4 visual-backbone verification for PhishVLM (CPU, no LLM API).
Runs the logo detector + OCR-aided siamese encoder + layout detector
on the bundled demo site to confirm the vision stack reproduces locally.
"""
import os
import sys
import time

os.environ.setdefault('CUDA_VISIBLE_DEVICES', '')
ROOT = os.path.abspath('.')
sys.path.insert(0, ROOT)

from scripts.phishintention.model_config import load_config
from scripts.utils.PhishIntentionWrapper import LogoDetector, LogoEncoder, LayoutDetector

t0 = time.time()
AWL_MODEL, SIAMESE_MODEL, OCR_MODEL, SIAMESE_THRE = load_config()
t1 = time.time()
logo_extractor = LogoDetector(AWL_MODEL)
logo_encoder = LogoEncoder(SIAMESE_MODEL, OCR_MODEL, SIAMESE_THRE)
layout_extractor = LayoutDetector(AWL_MODEL)
print('model load time: %.1fs' % (t1 - t0))

site = os.path.join('datasets', 'test_sites', 'www.baidu.com')
shot = os.path.join(site, 'shot.png')
print('shot exists:', os.path.exists(shot))

# Step A: layout detector
t2 = time.time()
boxes, classes = layout_extractor(shot)
t3 = time.time()
print('layout boxes:', None if boxes is None else boxes.shape,
      'classes:', None if classes is None else classes.shape, 'time: %.2fs' % (t3 - t2))

# Step B: logo detector
t4 = time.time()
logo_boxes = logo_extractor(shot)
t5 = time.time()
print('logo boxes:', None if logo_boxes is None else logo_boxes.shape, 'time: %.2fs' % (t5 - t4))

# Step C: OCR-aided siamese embedding on the first logo crop
emb_out = None
if logo_boxes is not None and len(logo_boxes) > 0:
    from PIL import Image
    img = Image.open(shot).convert('RGB')
    x1, y1, x2, y2 = [int(v) for v in logo_boxes[0][:4]]
    crop = img.crop((x1, y1, x2, y2))
    print('logo crop size:', crop.size)
    t6 = time.time()
    emb_out = logo_encoder(crop)
    t7 = time.time()
    print('logo embedding shape:', emb_out.shape, 'time: %.2fs' % (t7 - t6))
else:
    print('no logo box detected on baidu demo shot')

print('DONE siamese_thre=', SIAMESE_THRE)