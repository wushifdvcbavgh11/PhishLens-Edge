# T8 测试集采集与构建记录

> 阶段：10/5–10/7（品牌库与测试数据）
> 完成：2026-09-30
> 隔离方案：**本机无 Docker/VM** → 采用 Chrome headless + 一次性临时 user-data-dir（无扩展、无持久 cookie、截图即弃），每 12 站点重建 driver 防崩溃级联。

## 1. 交付物总览

| 交付物 | 位置 | 规模 | 状态 |
| --- | --- | --- | --- |
| 钓鱼 URL 源 | `testset/openphish_urls.txt` | 300 条（OpenPhish feed） | ✅ |
| 钓鱼截图全集 | `testset/phishing/` | 262 张 OK（manifest 262 行） | ✅ |
| **精选钓鱼测试集** | `testset/phishing/selected_manifest.tsv` | **50 张** | ✅ |
| 正常站点截图全集 | `testset/benign/` | 90 张 OK | ✅ |
| **精选正常测试集** | `testset/benign/selected_manifest.tsv` | **50 张** | ✅ |
| 自建中文仿冒页 | `testset/fake/fake_{alipay,wechat,icbc}.html` | 3 页（离线、logo base64 内嵌） | ✅ |
| 仿冒页截图 | `testset/fake/fake_{alipay,wechat,icbc}.png` | 3 张（1280×800） | ✅ |

## 2. 采集脚本

- `tools/capture_screenshots.py` — 钓鱼页采集（OpenPhish 300 条，随机种子 42 shuffle，NAV_TIMEOUT=20s，失败记入 manifest）
- `tools/capture_benign.py` — 正常站登录页采集（3 轮共约 150 站点清单，每 12 站点重建 driver）
- `tools/filter_samples.py` — 质量筛选：灰度 std ≥ 30、文件 ≥ 12KB、前 64KB MD5 去重，按丰富度取前 50
- `tools/make_fake_pages.py` — 生成 3 个本地中文仿冒登录页 + headless 截图

## 3. 筛选统计

### 钓鱼集（262 候选）
- 剔除：小文件 20、空白页 55、同模板重复 125 → 去重后 76 → 按灰度 std 排序取前 50
- 覆盖品牌/主题：Coinbase、Amazon、Xfinity、LinkedIn、iCloud、Roblox（多域名仿冒）、Shopee 促销骗局、imToken、DANA、Chase、Spotify、Microsoft 登录、Slack 邮件钓鱼等
- 说明：evergreenfin.ltd（"Ondo Tokenized Stocks" 投资诈骗模板）等多域名同模板已被 hash 去重只留一张

### 正常集（90 候选）
- 剔除：小文件 6、空白 27、重复 1 → 去重后 56 → 取前 50
- 覆盖：支付宝/微信/QQ 邮箱/163 邮箱/淘宝/京东/拼多多/12306/学信网/个人所得税、工行/建行/农行/中行/招行/交行/邮储/兴业/中信、微软/谷歌/苹果/亚马逊/Shopify/Netflix、知乎/百度/B 站/微博/爱奇艺/携程、国内外高校（山大/复旦/上交/浙大/武大/中山/华科）等

## 4. 自建仿冒页设计（3 例）

| 页面 | 仿冒品牌 | 正规域名 | 仿冒域名 | 特征 |
| --- | --- | --- | --- | --- |
| fake_alipay.html | 支付宝 | alipay.com | alipay-secure-login.top | logo 内嵌、密码框+验证码、「异常登录风险」恐吓文案 |
| fake_wechat.html | 微信 | weixin.qq.com | weixin-verification.icu | 同上 + 微信绿主题色 |
| fake_icbc.html | 工商银行 | icbc.com.cn | icbc-ebank-verify.cc | 同上 + 工行红主题色、e-Banking 标题 |

均表单 `action` 指向仿冒域名、含 `input[type=password]`，用于验证第 3 级 DOM 凭据判断与品牌比对能命中真实品牌 logo。

## 5. 已知缺口

- 精选集 50+50 中部分为 Cloudflare 拦截页被自动剔除（钓鱼抓取常态），实际有效钓鱼页占比约 29%（76/262）
- T7 遗留：本校三个品牌（统一身份认证/教务系统/校园邮箱）缺学校名称与域名，待用户补充后可追加
- 数据规模 50+50 满足方案第 8 节初赛评测要求；决赛阶段可按同流程扩到 500+500