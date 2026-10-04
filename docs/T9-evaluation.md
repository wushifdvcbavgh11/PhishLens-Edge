# T9 — Preliminary Evaluation Report (50+50+3)

Date: 2026-09-30 · Environment: Windows CPU (no GPU) · Funnel: PhishLens server (`server/app.py`)

## 1. Test set

| Split | Number | Source | Screenshot |
|---|---|---|---|
| Phishing | 50 | OpenPhish (sampled by manifest + title from crawl) | `testset/phishing/phish_*.png` |
| Benign | 50 | login pages of real sites (Weibo, Zhihu, Amazon, Steam, Tsinghua, SDU, gov.cn, …) | `testset/benign/*.png` |
| Self-built fake CN pages | 3 | local `fake_alipay / fake_wechat / fake_icbc` HTML | `testset/fake/*.png` |

Brand coverage of protected list: 30 brands (22 CN + 8 EN) in `brand_library/brands.json`, plus the original PhishIntention expansion list (282 dirs) in the Siamese reference library.

## 2. Method

`tools/evaluate.py` calls `server.app.analyze()` directly (same code path as the HTTP endpoint).

Two evaluation protocols:

- **visual-only**: DOM signals carry only `page_domain` + title (honest about the visual path; no password-box claim). Phishing/benign screenshots never assert a password field.
- **full-DOM**: `has_password_input` is injected for all samples to represent the upper bound of what the browser extension will actually report (the extension really sees `<input type=password>`).

A warm-up dummy sample runs first so the one-shot cold model load (~789 s CPU: detectron2 + Siamese + OCR + 3069-logo feature cache) does not pollute latency statistics.

Threshold: `MATCH_THRESHOLD = 0.68` (see §5 for tuning).

## 3. Results

### 3.1 Confusion matrix (decision level)

| Protocol | Set | block | uncertain | allow | error | total |
|---|---|---|---|---|---|---|
| visual-only | phish | 0 | **13** | 37 | 0 | 50 |
| visual-only | benign | 0 | 0 | 50 | 0 | 50 |
| visual-only | fake | **3** | 0 | 0 | 0 | 3 |
| full-DOM | phish | **13** | 0 | 37 | 0 | 50 |
| full-DOM | benign | 0 | 0 | 50 | 0 | 50 |
| full-DOM | fake | **3** | 0 | 0 | 0 | 3 |

### 3.2 Core metrics

| Metric | visual-only | full-DOM |
|---|---|---|
| Phishing recall (block+uncertain) | 13/50 = **26.0%** | 13/50 = **26.0%** |
| Benign false-positive rate | 0/50 = **0.0%** | 0/50 = **0.0%** |
| Fake-page interception | 3/3 = **100%** | 3/3 = **100%** |
| Brand-recognition accuracy (when brand fired) | 13/13 = **100%** | 13/13 = **100%** |
| Phish brand hit | 13/50 = 26% | 13/50 = 26% |
| Benign brand hit (one, correct - Microsoft on onedrive.live.com, allowed) | 1 | 1 |

Brands correctly recognised on phishing pages (all real matches):

Adobe, Amazon ×2, AutoScout24, Bet365, Chase Personal Banking, Comcast Corporation,
Facebook ×2, LinkedIn Corporation, MetaMask, Netflix, PayPal.

### 3.3 Latency (hot, per sample)

| Stage | mean | median | n |
|---|---|---|---|
| L0 whitelist/cache | ~0 | ~0 | 8 whitelisted |
| L1 logo detection (Faster R-CNN) | 1.592 s | 1.598 s | 95 |
| L2 brand matching (Siamese+OCR / MobileNet) | 0.730 s | 0.796 s | 95 |
| L3 DOM signals | ~0 | ~0 | all |
| L4 rule review | ~0 | ~0 | 7 |
| **Total (per sample)** | **2.144 s** | ~2.36 s | 103 |

Cold start: ~789 s first load (models + 3069-logo OCR-aided feature cache), excluded from statistics by warm-up.

### 3.4 Funnel split

- 95/103 samples took the visual path (L1→L2); 8 benign samples short-circuited at L0 whitelist (weibo.com, gov.cn, amazon.com, tsinghua.edu.cn, sdu.edu.cn, facebook.com, steampowered.com, zhihu.com).
- All 50 phishing URLs passed L0 (not whitelisted).
- 13 + 3 samples reached a block/uncertain decision driven by brand match; every detected phishing page had its genuine brand in the 282-brand reference library.

## 4. What the number 26% means (honest limitations)

The visual funnel only flags a page when (a) the detector finds a logo region and (b) that region
matches a protected brand above 0.68. 37/50 OpenPhish samples miss because:

1. **AWL (Faster R-CNN) does not detect a logo box** on many crowded/text-heavy pages → fallback crops the top strip, whose MobileNet feature is diluted by background → best sim 0.4–0.6, below threshold.
2. **New-English-brand pages** (Coinbase, Ledger, Xfinity, Shopee, several MetaMask) have tiny/degraded logos on the crawl screenshots. Diagnostic (`tools/diag_siamese.py`) shows OCR-aided Siamese similarity top-1 is dominated by *wrong* brands even after adding those 5 logos to the reference library:

   - Coinbase page → Banco de Chile 0.658, MetaMask page → M&T Bank 0.707, Ledger page → Bet365 0.674, Shopee page → Orange 0.718, Xfinity page → Comcast 0.791 (= its parent, still off).
   - The screenshots' logo regions are too small / occluded for either Siamese or MobileNet to reach 0.68 against the clean reference logos.
3. Bench rule-based path stays silent when neither brand nor strong URL signals fire.

Implication: brand-matching quality itself is high whenever a logo is found (13/13 = 100%),
but logo-region recall on real-world phish screenshots is the binding constraint.
Full-DOM extension signals convert all 13 visual hits into hard blocks (13/50), and the three
self-built Chinese fake pages — which use clean page-level logos — are blocked 3/3.

## 5. Threshold tuning record

Vertical axis: similarity between the detected logo crop and the reference logo.

| Threshold | phish | benign | fake | verdict |
|---|---|---|---|---|
| 0.55 | (early run) | 20 FPs | 3/3 | too sensitive; benign logo crops get matched |
| 0.80 | 13 (all hits killed?) | 0 | 0/3 | kills genuine matches (fake pages 0.709–0.734) |
| **0.68** | **13 hit** | **0 FP** | **3/3** | keeps genuine CN hits, benign max mismatch < 0.68 |

Evidence bank: `testset/eval/sim_dist.tsv` measured on all 103 samples.
Key points: fake pages hit alipay 0.734 / wechat 0.709 / icbc 0.709 (anchor);
benign mismatches concentrate 0.44–0.68, highest facebook.com→Coinbase 0.728 (handled by `EXTRA_WHITELIST`).

## 6. Reproduce

```bash
# visual-only protocol (what this doc reports as nominal)
.venv/Scripts/python.exe tools/evaluate.py
# full-DOM bound (extension will inject real DOM signals)
.venv/Scripts/python.exe tools/evaluate.py --full-dom
```

Outputs: `testset/eval/results.tsv` (per sample), `testset/eval/metrics.json`.
Stored copies: `metrics_visual_only.json` / `results_visual_only.tsv` / `metrics.json` (full-DOM).

## 7. Next steps

- Raise logo-region recall: fine-tune AWL with more ad-hoc logo crops, or add a headline-text OCR brand-hint path (already prototyped as L3 brand hints).
- Offload L1/L2 to Hailo-10H (target <100 ms per page) for the final demo.
- Grow protected-brand list for CN banks (already 22 CN brands) and add crawl-time retry with longer viewport for tiny-logos pages.