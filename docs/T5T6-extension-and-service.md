# T5+T6: 浏览器扩展与本机判定服务（方案第 2–3 步）

> 日期：2026-09-29 | 环境：Windows 11 + Python 3.11.9 (venv) | Chrome/Edge 147，MV3

## 1. 组件布局

```
phishlens-edge/
├── browser-extension/          # T5: Chrome/Edge MV3 扩展
│   ├── manifest.json           # MV3，content_scripts + service worker
│   ├── content.js              # 截图、DOM 信号采集、发请求、警告层
│   └── background.js           # captureVisibleTab 截屏（content script 无截屏权限）
└── server/                     # T6: 本机 FastAPI 判定服务
    ├── app.py                  # /analyze 五级漏斗 + /health
    ├── requirements.txt
    └── test_analyze.py         # 端到端冒烟测试（三样本）
```

## 2. 扩展（browser-extension/）

- **manifest.json（MV3）**：`content_scripts` 注入 `content.js`（`<all_urls>`，document_idle）；`background.service_worker` = `background.js`；`host_permissions: <all_urls>`（截屏需全站权限）；仅 `activeTab` + `storage` 两个 permission。
- **content.js** 职责：
  1. **页面加载完成**后 1.8s 发一次 `CAPTURE` 消息，由 background 调 `chrome.tabs.captureVisibleTab` 截 PNG 并回传 dataURL（content script 本身无截屏 API，这是 MV3 的标准做法）；
  2. **密码框 focus**（`focusin` 委托监听 `INPUT[type=password]`）时延迟 300ms 二次截图并重新分析；
  3. **DOM 信号**：`page_domain`、有无 `input[type=password]`、登录表单、`form action` 域名列表、页面标题、favicon、品牌线索（标题/OG 匹配正则）；
  4. `fetch → http://127.0.0.1:8765/analyze`（JSON: url + screenshot_base64 + dom_signals + trigger）；
  5. 返回 `decision === 'block'` 时，用 **Shadow DOM** 注入全屏警告层（不污染页面样式），显示：警告徽标、**仿冒品牌名**、**当前域名 vs 正规域名**、"**返回**"按钮（history.back）与"**我确认安全，继续访问**"按钮（写 `localStorage` 跳过标记，本页不再拦截）。
- **background.js**：`onMessage` 收 `CAPTURE` → `captureVisibleTab` → 回 `{dataUrl}`，保持通道异步。
- 隐私：截图与判定的全部通信只打到 127.0.0.1，不上任何公网（警告层也有提示文案）。

## 3. 本机服务（server/app.py，FastAPI + uvicorn，端口 8765）

### 多级漏斗（/analyze）

| 级 | 内容 | 实现 | 本机实测耗时 |
| --- | --- | --- | --- |
| L0 | 域名白名单 + LRU 缓存 | 内置 20+ 品牌官方域名（`USER_FACING_BRAND`）+ 搜索/政教域名白名单；`LRUCache(256)` 按 URL 缓存结果 | ~0.02s（命中缓存直接返回） |
| L1-2 | logo 检测 + 品牌比对 | 惰性加载 **PhishIntention 原版权重**：Faster R-CNN AWL 检测（0.3 阈值）+ OCR-aided Siamese 品牌匹配（阈值 0.87），品牌→域名用 `domain_map.pkl`；复用 T3 构建的 `LOGO_FEATS.npy`（3064 特征） | 检测 1.3–1.8s + 比对 0.3–0.8s（CPU） |
| L3 | DOM 凭据信号 | 密码框存在、登录表单、**表单 action 指向外部域名**（凭据外泄信号）、页面文本品牌提示与视觉品牌交叉验证 | <0.01s |
| L4 | 规则复核（VLM 钩子） | URL 可疑性打分（IP 直接访问/国际化域名混淆/可疑 TLD/超长域名/非 HTTPS）；预留本地 Qwen2-VL 接入点 | <0.01s |

### 三条件判定融合
- **视觉品牌匹配 + 域名与正规域名不一致**（`check_domain_brand_inconsistency` 只在域名不一致时才返回品牌）+ **索要凭据/DOM 可疑** → **block**
- 仅视觉匹配（无凭据信号）→ uncertain（建议 VLM 复核）
- 无视觉匹配但凭据信号 + 高可疑分 → uncertain
- 白名单命中或全无信号 → allow

### 平台差异处理（Windows 实测必需）
1. `check_domain_brand_inconsistency` 与 `pred_brand` 内部用**相对路径**打开 `models/domain_map.pkl` 与 logo 参考图 → 服务启动时 `os.chdir(PHISHINTENTION_ROOT)` 并回切；`domain_map_path` 与 `logo_files` 一律**绝对路径化**，`>240` 字符补 `\\?\` 前缀（与 T3 同款补丁）。
2. 权重惰性加载，第一次请求约 +3s，之后常驻内存（AWL 约 1.3GB 含 detectron2 开销）；`MODELS_READY` 标健康。
3. CORS `allow_origins=["*"]`，保证扩展任何站点上下文都能读响应。

## 4. 端到端验证结果（test_analyze.py，三样本）

| 样本 | 期望 | 实际 | 品牌 | 耗时(总) | 说明 |
| --- | --- | --- | --- | --- | --- |
| accounts.g.cdcde.com（仿 Google） | block | **block** ✅ | Google (0.968) | 5.1s（首次含模型加载） | 视觉+凭据双重命中 |
| ru.russianmastercach-republick.xyz（仿 Mastercard） | block | **block** ✅ | Mastercard International (0.968) | 2.0s | **原版 PhishIntention 漏报(FN)，本服务 L3 DOM 信号成功补拦** |
| www.paypal.com（真 PayPal） | allow | **allow** ✅ | 未匹配 | 2.1s | 访真站不误报 |

**与 T3 原版对比的改进**：russianmastercach 样本在原版管线被判 benign（CRP 分类器未识别为凭证页 + 动态分析崩溃兜底失败），本服务把"是否索要凭据"下沉到 L3 DOM 信号独立判断，补上了原版漏报点——正是参赛方案多级漏斗设计的核心收益。

## 5. 运行方式

```bash
# 启动本机服务（首次请求才加载模型）
"phishlens-edge/.venv/Scripts/python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8765 --app-dir phishlens-edge/server

# 冒烟测试（三样本）
"phishlens-edge/.venv/Scripts/python.exe" phishlens-edge/server/test_analyze.py

# 加载扩展：chrome://extensions → 开发者模式 → 加载已解压的扩展程序 → browser-extension/
```

未安装依赖：`python -m pip install -r server/requirements.txt`（fastapi/uvicorn 之外的视觉栈在 T2/T3 已装好）。