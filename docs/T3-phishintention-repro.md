# T3: PhishIntention 原版复现记录（CPU）

> 日期：2026-09-29 | 环境：Windows 11 + Python 3.11.9 (venv) | 全部 CPU 推理

## 1. 环境与安装

- 仓库：`third_party/PhishIntention`（lindsey98/PhishIntention，`git clone --depth 1`）
- Python 3.11.9 venv：`phishlens-edge/.venv`（与 T2 共用）
- 额外依赖：`selenium==4.49.0`、`helium`（动态分析用 Selenium 点击登录/注册链接）
- chromedriver：本机 Chrome 147.0.7727.56；Selenium Manager 自动解析在国内网络下不可用（挂起），改从 cdn.npmmirror.com 手动下载 chromedriver **147.0.7727.117**（10.3MB，需校验字节数）放到 `PhishIntention/chromedriver/chromedriver.exe`

## 2. 权重获取（hf-mirror.com `code-philia/PhishIntention` 仓库，共 7 个）

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| layout_detector.pth | 330MB | AWL 元素检测 Faster R-CNN（NUM_CLASSES=5，除 bg 外 logo/CRP/other 等 4+1 类） |
| crp_locator.pth | 330MB | CRP 定位器 Faster R-CNN（NUM_CLASSES=1，动态分析时定位登录/注册框） |
| ocr_siamese.pth.tar | 194MB | OCR-aided Siamese 品牌比对模型 |
| crp_classifier.pth.tar | 188MB | CRP 分类器（BiT-M-R50x1V2，判别是否为凭证输入页） |
| ocr_pretrained.pth.tar | 84MB | ASTER OCR 预训练 |
| domain_map.pkl | 221KB | 品牌→域名映射（**PhishIntention 版**，与 Phishpedia 的 12KB 不同） |
| expand_targetlist.zip | 212MB | 品牌 logo 库：**本次复用 Phishpedia 版** zip（解压后 277 品牌 / **3064 张** logo；PhishIntention HF 版为 212,573,017 字节，与 Phishpedia 版 212,261,039 字节可能有个别差异，未造成本次运行问题） |

权重分类头校验通过：cls_score.weight 维度与 NUM_CLASSES 一致，因此 configs.yaml 无需改类别数。

## 3. 需要的补丁（Windows 必改，其余平台可跳过）

**Windows MAX_PATH=260 长路径问题**（`src/phishintention/modules/logo_matching.py` `cache_reference_list`）：
- 症状：构建 logo 特征缓存到第 69/3064 张时报 `FileNotFoundError`；`os.path.exists()` 对磁盘上真实存在的文件返回 False
- 根因：本工作区路径前缀 ~171 字符 + 品牌库内 134 字符长文件名（如 `kisspng-adobe-acrobat-pdf-computer-icons-adobe-reader-edu-6-216-series-compact-arm-pole-mount-5b6d74e0255a30.422316211533900000153.jpg`）→ 全路径 ≈305 字符 > 260
- 修复：构造 `file_name_list` 时，若 `os.name=='nt' and len(os.path.abspath(full_p))>240`，则改为 `'\\\\?\\' + os.path.abspath(full_p)`。PIL `Image.open` 已验证支持 `\\?\` 前缀；下游只做 `Image.open`/`basename`，无破坏。
- 注意：`np.save` 只在全部 3064 张算完后落盘，中途失败会重算全部（首次约 9-10 分钟，之后从 `LOGO_FEATS.npy` 复用）。

## 4. 运行方式

```bash
cd PhishIntention
# 首次会构建 LOGO_FEATS.npy（3064×2560，约 9-10 分钟，0.31s/张 CPU）
PYTHONPATH=src python -m phishintention --folder datasets/test_sites --output_fn test.json
```

## 5. 判定流程（test_orig_phishintention 五步漏斗）

1. **AWL 布局检测**：Faster R-CNN 检测元素框（logo/文本框/按钮等），`pred_rcnn(im=screenshot_path)` → boxes+classes；无任何元素 → benign
2. **logo 检测**：`find_element_type` 取 logo 类框；无 logo → benign
3. **OCR-aided Siamese 品牌比对**（`pred_brand`）：裁剪 logo 框 → OCR 文本辅助 embedding（2560 维，与参考库 3064 张做余弦相似度 top1）→ 相似度 > 阈值(SIAMESE_THRE≈0.9) 才认定品牌；**域名一致性**：若当前页域名 ∈ 品牌官方域名集合 → benign，否则视为仿冒信号
4. **CRP 分类器**：BiT 对整页截图 + `html_heuristic`（html.txt 启发式）判断是否"凭证输入页"；疑似 CRP → 直接 phish；非 CRP → 转动态分析
5. **动态分析**（`crp_locator`）：Selenium headless 访问 URL（page_load_timeout=60s，重试 2 次），尝试点击页面中的登录/注册链接，截图重新进入 1-4；找不到 CRP 页 → benign

**输入**：截图路径 `shot.png` + URL + 同目录 `html.txt`（CRP 分类用）；**输出**：`test.json`（每样本：`phish_category`/`pred_target`/`matched_domain`/`siamese_conf`/分阶段耗时）。

## 6. 测试结果（datasets/test_sites 三样本，CPU）

| 样本 | 域名 | 判定 | 正误 | 品牌 | con fi | AWL | logo | CRP分类 | locator | 总耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| accounts.g.cdcde.com | 仿 Google 登录 | **phish** | ✅ TP | Google | 0.968 | 1.82s | 0.39s | 0.02s | 0 | **2.23s** |
| ru.russianmastercach-republick.xyz | 仿 Mastercard | benign | ❌ FN | Mastercard International | 0.968 | 1.77s | 0.27s | 0.22s | 0 | 2.26s |
| www.paypal.com | 真实 PayPal | benign | ✅ TN | 未匹配(0.775<阈值) | — | 1.30s | 0.74s | — | 0 | 2.04s |

单样本 CPU 总耗时 **约 2.0–2.3s**，AWL 检测占大头（1.3–1.8s/张）。

## 7. 局限与启示（对 T5/T6 本机服务设计的直接输入）

1. **漏报边界**：ru.russianmastercach 的 logo 已匹配 Mastercard 且域名不一致，但 CRP 分类器/动态分析未把该页判为凭证页 → 整体 benign。原版把"定罪"押在 CRP 页识别上，**直接渲染登录表单且动态分析失败时必然漏报**。本机服务的第 3 级（DOM 索要凭据信号）正是补这个洞。
2. **本环境动态分析实际没跑起来**：chromedriver 147.0.7727.117 与本机 Chrome 147.0.7727.56 存在轻微版本差，动态点击阶段 chromedriver 崩溃（日志含崩溃堆栈），被当作"no CRP page"→ benign。测试样本 URL（2018-2021 钓鱼站）本身也已失效，动态分析本就不可能成功。
3. **品牌误拒**：真实 paypal.com 首页 logo 匹配 conf 0.775 < 0.9 未命中（可能因品牌库中 PayPal 参考图多为纯 logo、页面截图带背景差异大）。做首版厂商库时要为每家品牌准备**白底 logo + 登录页截图**两类参考图。
4. siamese 阈值 0.9 + 域名一致性检查是防误报关键；Phishpedia 复用了同款比对逻辑，只是没有 CRP 动态分析层。