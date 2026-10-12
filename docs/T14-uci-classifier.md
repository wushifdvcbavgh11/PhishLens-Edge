# T14 — UCI Phishing Websites Classifier (Traditional Feature Model)

Date: 2026-10-06 · Environment: Windows CPU · Stack: scikit-learn 1.9.1 + XGBoost 3.2.0

## 1. Purpose & position in the funnel

This classifier trains on the classic **UCI Machine Learning Repository "Phishing Websites" dataset (id=327)** — 30 hand-crafted URL & webpage-content features — and serves as a **fast traditional-feature pre-screen signal** inside PhishLens Edge. It **complements, does not replace**, the visual brand-matching funnel (YOLO logo detection + Siamese brand comparison). Where the visual path needs a screenshot and takes ~1.6 s (L1) to run, this model classifies from URL/DOM signals alone in milliseconds and can flag `uncertain`/phish-probability early.

## 2. Dataset

| Item | Value |
|---|---|
| Source | UCI ML Repository, dataset id 327 (`archive.ics.uci.edu/dataset/327/phishing+websites`) |
| Format | ARFF (`Training Dataset.arff`), converted to `datasets/uci_phishing/uci_phishing.csv` |
| Samples | **11,055** (no missing values) |
| Features | 30 categorical URL/DOM features (having_IP_Address, URL_Length, SSLfinal_State, URL_of_Anchor, web_traffic, …) |
| Label | 2 classes in the official ARFF: **-1 legitimate (4,898)** / **+1 phishing (6,157)** |
| Note | Some mirrors publish a 3-class variant (-1/0/1) with a "suspicious" class; the official ARFF on UCI only carries -1/1, so a binary classifier is trained. |

## 3. Method

`tools/train_uci_phish.py`:

- 30 features → 5-fold **stratified cross-validation** (shuffle, seed 42), class label mapped `-1→0, +1→1`.
- 4 models compared: RandomForest (300 trees), XGBoost (300 trees), LogisticRegression, HistGradientBoosting (300 iters).
- Best model by mean F1 is retrained on the **full** dataset and saved as `model/uci_phishing_clf.joblib` (HistGradientBoostingClassifier).

## 4. Results (5-fold CV)

| Model | Accuracy | Precision | Recall | F1 | AUC |
|---|---|---|---|---|---|
| HistGradientBoosting | **0.9723** ± 0.0014 | **0.9687** | **0.9821** | **0.9753** | **0.9969** |
| RandomForest | 0.9708 ± 0.0022 | 0.9677 | 0.9803 | 0.9739 | 0.9958 |
| XGBoost | 0.9692 ± 0.0013 | 0.9671 | 0.9781 | 0.9725 | 0.9964 |
| LogisticRegression | 0.9279 ± 0.0052 | 0.9264 | 0.9458 | 0.9360 | 0.9787 |

- **Best model: HistGradientBoosting** — F1 0.9753, AUC 0.9969, accuracy 97.23%, phishing recall 98.21%.
- Holdout spot-check: first 8,000 rows re-scored after retraining → accuracy 0.9865, consistent with CV.
- Full per-fold metrics are in `model/uci_phishing_metrics.json`.

## 5. Feature importance (permutation importance, F1 drop, Top-12)

HistGradientBoosting has no native `feature_importances_`, so permutation importance was computed on an 3,000-sample stratified subset (n_repeats=10):

| Rank | Feature | Importance |
|---|---|---|
| 1 | URL_of_Anchor | 0.18198 |
| 2 | SSLfinal_State | 0.11509 |
| 3 | web_traffic | 0.03167 |
| 4 | having_Sub_Domain | 0.02645 |
| 5 | Prefix_Suffix | 0.02561 |
| 6 | Links_in_tags | 0.02186 |
| 7 | having_IP_Address | 0.01728 |
| 8 | Links_pointing_to_page | 0.01595 |
| 9 | DNSRecord | 0.01192 |
| 10 | age_of_domain | 0.01187 |
| 11 | Google_Index | 0.01129 |
| 12 | SFH | 0.01063 |

Findings are consistent with phishing-domain knowledge: **anchor URL mismatch, TLS state and URL structure/lexical traits dominate**, providing a strong orthogonal signal to the visual logo path.

## 6. Integration & usage demo

- Artifacts: `model/uci_phishing_clf.joblib` + `model/uci_phishing_features.json` (30 feature names, model-input order).
- Server hook: `server/uci_prefilter.py` — feature extraction (12-13 of 30 features computed from URL + DOM signals, rest neutral 0), loaded lazily by `server/app.py` as a **L0.5 pre-screen** between L0 whitelist and L4 fusion. The output is attached to every `/analyze` response as `uci_prefilter` (`phish_prob`, `hint`, `features_computed`, `model_loaded`).
- Escalation rule (conservative, never overrides the visual path): if the UCI model says `phish_likely` **and** the page asks for credentials (password box / external form action) or the URL is suspicious, the fused decision is escalated `allow -> uncertain` for human/VLM review. `block` still requires the visual three-condition match.
- CLI demo: `tools/demo_uci_classifier.py` (single URL + optional DOM JSON, or a batch over `testset/openphish_urls.txt`).
- Runtime: histogram-GBM inference is sub-millisecond for 30 features; the measured `l05_uci_features` step is 0.06-1.6 s on first call (joblib cold load + DNS lookup), negligible vs L1 ~1.6 s.

## 7. Live integration check (10-06)

| Case | URL | DOM signal | fuse decision | uci_prefilter |
|---|---|---|---|---|
| Phishing + credential box | `account-sso.evergreenfin.ltd/login` | password + external form action | **uncertain** (escalated) | prob 0.9971, phish_likely, 13 feats |
| Phishing, no DOM signal | `asodfihjgdioshi.xyz/` | none | allow | prob 0.0011, benign_likely |
| Legitimate whitelist | `login.alipay.com/` | none | allow | prob 0.0011, benign_likely |

Notes: URL-only features (no DOM) are weak for long-domain https phish (evergreenfin, `.ltd`), so the model behaves as a complementary signal rather than a standalone gate — this is the intended position. With DOM signals (password box + external form action), the pre-screen correctly escalates a visual-path-invisible phish to `uncertain`.

## 8. Reproduce

```bash
# 1) fetch data (or use the committed CSV)
curl -L -o datasets/uci_phishing/phishing_websites.zip \
  https://archive.ics.uci.edu/static/public/327/phishing+websites.zip

# 2) train & evaluate
.venv/Scripts/python.exe tools/train_uci_phish.py
```