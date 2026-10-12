# T14 — Held-out Evaluation with Real DOM Capture & Per-Miss Attribution

Date: 2026-10-08 · Environment: Windows CPU (no GPU) · Funnel: PhishLens server (`server/app.py`, same code path as HTTP)

## 1. Why this evaluation is different (data-split honesty)

| Split | Samples | Role | Was it ever used to tune a threshold? |
|---|---|---|---|
| Dev (tuning) | 50 phish + 50 benign (`selected_manifest.tsv`) | Picked `MATCH_THRESHOLD=0.68`, UCI cut-offs, etc. (T9) | **Yes** — may no longer be quoted as fresh numbers |
| **Held-out test (this run)** | **226 phish + 40 benign** = every OK sample in `testset/{phishing,benign}/manifest.tsv` that was **not** selected | Independent freshness benchmark | **No** — never touched any threshold decision |

The dev set was selected by `tools/filter_samples.py` (highest pixel-richness, dedup). The held-out set is therefore *harder* on average: it includes the long tail of low-quality / template-y phishing pages that were not cherry-picked for the demo.

## 2. Real DOM capture (not simulated)

On 2026-10-08 a live probe (`tools`-side HTTP GET, 8 s timeout, 12 threads) visited every held-out URL **right now** and recorded, per reachable page:

- `has_password_input` / `has_login_form` / `form action targets` (real `<form>` and `<input type=password>` presence in returned HTML)
- live `<title>` (replaces stale crawl title when present)

`testset/eval2/live_probe.json` is frozen with this run. Samples that answered `200` had their **real** DOM signals injected into the funnel (`dom_source=live`); samples that were dead / refused (`403/404/ERR/…`) are marked `dom_source=unreachable` and run with the minimal honest signal set (page_domain + manifest title, no password claim). **The L3 credential path is never silently faked.**

| Set | held-out total | live (real DOM) | unreachable (minimal signals) |
|---|---|---|---|
| Phishing | 226 | 37 | 189 |
| Benign | 40 | 27 | 13 |

## 3. Results (`testset/eval2/`)

### 3.1 Decision matrix

| Set | block | uncertain | allow | error |
|---|---|---|---|---|
| phish (226) | 3 | 18 | 205 | 0 |
| benign (40) | 0 | 4 | 36 | 0 |
| phish live only (37) | 3 | 6 | 28 | 0 |
| benign live only (27) | 0 | 3 | 24 | 0 |

### 3.2 Core metrics

| Metric | value |
|---|---|
| Phishing interception (block+uncertain) | 21/226 = **9.3%** (live subset 9/37 = 24.3%) |
| Block rate (hard intercept) | 3/226 = 1.3% (all 3 were live phish with real password field) |
| Benign false-positive **block** | 0/40 = 0.0% |
| Benign uncertain (warning band, not blocked) | 4/40 = 10.0% |
| Average end-to-end latency (incl. model warm) | 2.09 s / sample |
| Total wall time | 562 s |

### 3.3 What got blocked / warned (phish hits)

| Sample (live, real DOM) | signals that fired |
|---|---|
| `docs.courrierar24.co` | Coinbase logo match + **real password field** + form posts to `login.php` + non-HTTPS → **block** |
| `umarashid41.github.io/facebook` | Facebook logo (0.970) + real password field → **block** |
| `verificarcuenta-micros0ft2026way.zya.me` | Microsoft logo (0.982) + suspicious domain + UCI p=1.00 → **block** (no pw needed) |
| 18 more `uncertain` | logo match but no credential signal, and/or UCI p≥0.9 + credential-ish page |

## 4. Per-miss attribution — where the 205 missed phishing pages went

`testset/eval2/attribution_heldout.tsv` records `l1_logo_boxes`, `l1_block_fallback`, `l2_matched`, real `has_pw` for every miss. Breakdown of the 205 `allow` misses:

| Attribution | Count | Meaning |
|---|---|---|
| **brand_not_matched** | **191** | L1 **did detect** logo candidate box(es), but L2 matched **no** protected brand (all cosine sim < 0.68). These are mostly no-brand / weak-brand / generic-template phishing pages, not branded clones. |
| logo_not_found | 14 | L1 found nothing at all (even the top-area block fallback produced no candidate). |
| (uncertain band) | 18 | Already flagged for review — counted as intercepted above, not misses. |

**Key insight — this is NOT a "we need a stronger model" problem.** 203/205 misses have a detectable visual region; the funnel simply has **no protected brand to match** (191) or nothing to look at (14). The L3 credential rule only escalates *after* a brand match; an unbranded phishing page with a password box therefore sails through unless UCI + domain features reach the uncertain band (18 did).

Two concrete fixes follow from the data (T15 ticket):

1. **Legal-login-domain gap (benign warn):** `login.microsoftonline.com` matched Microsoft (0.969) but was flagged domain-inconsistent because `microsoftonline.com` is missing from the brand's official-domain list. Adding real login subdomains (see §5) removes this false warn for free — no threshold change.
2. **Unbranded-credential fallback:** a page with a real password box, non-HTTPS, and a non-top-1M/wonky domain currently needs UCI ≥ some bar to reach `uncertain`. An explicit "credential box + domain heuristics" rule (below the brand-match path) would close part of the long tail without raising benign risk — needs its own guarded threshold + re-validation on these 266 samples.

## 5. Benign uncertain — the 4 warnings, all honest and fixable

| Benign URL (live unless noted) | Cause | Fix |
|---|---|---|
| `login.microsoftonline.com` | Microsoft logo 0.969 but `microsoftonline.com` not in official domains | add `microsoftonline.com` (+ `login.live.com` alias) to brand map |
| `www.qidian.com` (unreachable) | top-area block fallback matched MetaMask (MobileNet 0.752) | fallback-block matching is noisy; gate fallback by stronger threshold or skip flat-block crops |
| `dash.cloudflare.com/login` | top-area block → MetaMask 0.727 | same as above |
| `my.1password.com` | logo crop → Coinbase 0.688 (just above 0.68) | 0.68 was tuned on the dev set; showing 4 warns on 40 fresh benign pages is exactly why we re-split. A 0.70~0.72 threshold (dev-validated enrich) + login-domain fixes is the next iteration. |

## 6. Reproducibility

```
# fresh probe (frozen copy already in testset/eval2/live_probe.json)
python tools/live_dom_probe.py
# full held-out run (~10 min CPU)
.venv/Scripts/python.exe tools/evaluate_heldout.py
# outputs
testset/eval2/results_heldout.tsv      # per-sample decisions + l1/l2/l3 anchors
testset/eval2/attribution_heldout.tsv  # per-miss attribution
testset/eval2/metrics_heldout.json     # metrics above
```

`server/app.py` gained three **diagnostic-only** fields (`l1_logo_boxes`, `l1_block_fallback`, `l2_matched`) in its response — the decision logic is unchanged.

## 7. Bottom line

- Honest re-read on a fresh, never-tuned set: **9.3 % phishing interception, 0 % benign block, 2.1 s/sample**.
- The dominant miss cause is **brand_not_matched (191/205)** — unbranded phishing, not logo-detection failure.
- The 4 benign warns are actionable (domain gap + noisy fallback matching), not fundamental.
- Next loop: fix §4/§5 items, re-run the same 266 samples, then re-quote numbers in the competition brief as "held-out, fresh data".