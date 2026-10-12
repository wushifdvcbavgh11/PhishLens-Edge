# T15 — Chinese Login Scenarios: Brand Library Expansion & Ambiguity Protection

Date: 2026-10-08 · Environment: Windows CPU · Scope: brand library (logo + domain) for Chinese banks / campus SSO, plus a top-2 ambiguity guard against cross-brand mis-binding.

## 1. Why this iteration

The held-out evaluation (T14) showed the biggest miss cause is `brand_not_matched` — the funnel works, but the brand table doesn't cover what Chinese users actually log into. This iteration adds the login surfaces that matter for the target persona: **domestic banks, enterprise webmail, and university unified authentication**, and fixes one real protection gap found in held-out data (`login.microsoftonline.com` not mapped to Microsoft).

## 2. Brand table now (39 brands)

| Category | Brands (`bid` → logo file) |
|---|---|
| Banks (domestic) | icbc, ccb, abchina, boc, cmb, bankcomm, **psbc, pingan, citic, spdb** |
| University SSO | **tsinghua, pku, fudan, zju, sjtu** |
| Enterprise/edu mail | alimail, exmail, qiye163, feishu, dingtalk, qq |
| Consumer | alipay, taobao, tmall, jd, pinduoduo, wechat, 12306, chsi (学信网), geeren, gjzwfw |
| International | paypal, google, apple, microsoft365, amazon, facebook, instagram, github, coinbase, metamask, ledger, xfinity, shopee, steam |

New in T15: **psbc · pingan · citic · spdb · tsinghua · pku · fudan · zju · sjtu** (9 brands). Feature vectors rebuilt → `brand_library/logo_feats_mnet.npy` shape `(39, 960)`, `brands.json` regenerated.

## 3. Logo sources (all fetched & verified)

| Brand | bid | Source URL | Note |
|---|---|---|---|
| 邮储银行 | psbc | `https://www.psbc.com/cn/images/psbc_hLogo_v2020.PNG` | official site header logo (624×53) |
| 平安银行 | pingan | `https://resources.pingan.com/app_inc/bank/v2023/images/PClogo_bank.svg` | SVG → resvg rendered (420×52) |
| 中信银行 | citic | `https://www.citicbank.com/include/top/logo/201512/W020260409637632341390.png` | official site header (228×63) |
| 浦发银行 | spdb | `https://www.spdb.com.cn/images/spdb-logo-header_sy.svg` | SVG → resvg rendered (182×51) |
| 清华大学 | tsinghua | `https://www.tsinghua.edu.cn/image/logo180.png` | off. site (180×58) |
| 北京大学 | pku | Wikimedia Commons `Peking University seal.svg` | 360×360 |
| 复旦大学 | fudan | `https://www.fudan.edu.cn/_upload/site/00/02/2/logo.png` | off. site (233×72) |
| 浙江大学 | zju | `https://zjuam.zju.edu.cn/cas/assets/images/cas_logo.png` | **login-page logo** (510×74) — the exact asset users see |
| 上海交通大学 | sjtu | `https://www.sjtu.edu.cn/resource/assets/img/logo.png` | off. site (156×157) |

SVG path: `svglib`+reportlab rejected (missing `libcairo-2.dll` on this Windows box) → switched to `resvg_py` (bundled native lib). Tsinghua only: header logo verified reachable via `curl -k` (requests hit `SSLError: unsafe legacy renegotiation disabled` on one bank site; curl worked — noted as a tooling lesson, not a product issue).

## 4. Domain mapping (USER_FACING_BRAND) — verified with `hostname_matches`

| Brand | valid domains (substring match) |
|---|---|
| 邮储银行 | psbc.com, psbcltd.cn |
| 平安银行 | pingan.com, bank.pingan.com |
| 中信银行 | citicbank.com, ecitic.com |
| 浦发银行 | spdb.com.cn |
| 清华大学 | tsinghua.edu.cn, id.tsinghua.edu.cn |
| 北京大学 | pku.edu.cn, iaaa.pku.edu.cn, portal.pku.edu.cn |
| 复旦大学 | fudan.edu.cn, id.fudan.edu.cn, ehall.fudan.edu.cn |
| 浙江大学 | zju.edu.cn, zjuam.zju.edu.cn, my.zju.edu.cn |
| 上海交通大学 | sjtu.edu.cn, jaccount.sjtu.edu.cn |
| Microsoft *(fixed)* | microsoft.com, office.com, **microsoftonline.com**, **live.com**, **outlook.com**, **sharepoint.com**, **azure.com** |

Spot checks (from the actual `app.hostname_matches`, output of this run):

| Host | Verdict |
|---|---|
| `login.microsoftonline.com` | **MATCH (Microsoft)** — gap from T14 closed |
| `outlook.live.com` | MATCH (Microsoft) |
| `jaccount.sjtu.edu.cn` / `id.tsinghua.edu.cn` / `zjuam.zju.edu.cn` | MATCH ✓ |
| `safe.bank.pingan.com` / `ebank.spdb.com.cn` / `www.psbc.com` / `bank.ecitic.com` | MATCH ✓ |
| `verify.mail-sjtu.com` | **NOT MATCH (上海交通大学)** → correctly flagged as inconsistent |

## 5. Ambiguity guard (`AMBIGUITY_RATIO`)

Full 39×39 cross-similarity showed several brand pairs with thin top-1 margins, e.g. **citic↔spdb 0.774 vs 0.796**, microsoft365↔bankcomm, abchina↔boc, fudan↔citic. A degraded logo crop (real-photo blur, resize) could otherwise be bound to the *wrong* brand → domain-inconsistent + password field → **false block of a legitimate bank login page**.

Fix in `server/cn_brand_matcher.py`: when `top2 / top1 > AMBIGUITY_RATIO (0.97)`, `match_logo_image` returns `None` (no brand binding) instead of guessing. The guard is **relative**, not an absolute gap: a decisive-but-low-scoring match (real alipay crop: alipay 0.734 vs feishu 0.692 → ratio 0.943) is kept, while sibling-bank confusions lie above 0.97 and are rejected. (An absolute `0.05` gap was tried first and over-rejected the alipay demo scene — fixed in the same session.)

Verification (this run):

- Clean logos: psbc/citic/spdb/pku self-match `sim=1.0`, matched correctly (guard does not hurt the happy path).
- Real alipay demo crop: alipay 0.734 vs feishu 0.692 → **matched 支付宝** ✓ (guard lets decisive low-score matches through)
- 6× degenerate blur of citic: returns `None` (guard fires — refuses to bind a brand it can't confidently tell apart) ✓
- E2E `fake_alipay.html` + real DOM signals (`127.0.0.1` page, `alipay-secure-login.top` form action): **decision `block`, brand 支付宝 (sim 0.734)**, reasons chain complete ✓

## 6. Artifacts changed

| File | Change |
|---|---|
| `brand_library/logos/{psbc,pingan,citic,spdb,tsinghua,fudan,zju,sjtu,pku}/logo*.png` | 9 new/updated logos |
| `brand_library/logo_feats_mnet.npy` / `brands.json` | rebuilt: 39 brands, 960-d MobileNet features |
| `brand_library/extract_features.py` | BRANDS += 9 brands; Microsoft domain list extended |
| `brand_library/upgrade_logos.py` | download + SVG-render entries for the 9 brands |
| `server/app.py` | `USER_FACING_BRAND` += 9 brands & Microsoft login domains |
| `server/cn_brand_matcher.py` | `AMBIGUITY_GAP=0.05` top-2 guard |
| `docs/T15-cn-login-scenarios.md` | this doc |

## 7. Tooling lessons (for the report's dev-log)

1. requests → `SSLError: unsafe legacy renegotiation disabled` on `spdb.com.cn`; `curl -k` works. Real endpoint tested with curl.
2. `svglib` renderPM needs `libcairo-2.dll` (absent here) → `resvg_py` renders SVG→PNG with bundled native lib, no system deps.
3. Wikimedia Commons filename guessing is unreliable for Chinese brand marks (only `Peking University seal.svg` hit); official-site header images and **the actual SSO login-page logo** (zju) are the dependable sources.