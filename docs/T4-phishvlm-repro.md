# T4: PhishVLM 复现记录（CPU + API 阻塞点分析）

> 日期：2026-09-29 | 环境：Windows 11 + Python 3.11.9 (venv) | CPU 推理

## 1. 结论速览

- PhishVLM 的**视觉骨干（logo 检测 + OCR-aided Siamese 编码）已在本地 CPU 完整跑通**，权重与 PhishIntention 同源，直接复用。
- 它的"大模型复核"环节（品牌识别 / CRP 分类 / 登录 UI 排序）**不是本地模型，而是云端闭源 API**（默认 `gpt-4o-mini-2024-07-18`），并额外依赖 **Google Programmable Search API** 做品牌验证。
- 两条 API key 本地均无，代码硬依赖（缺则直接抛异常）→ **当前无法端到端跑通，记录为外部凭证阻塞**，不是代码/环境问题。降级方案见 §6。
- 这恰好印证参赛方案选型：第 4 级复核采用**本地 VLM（Qwen2.5-VL 系列）替代闭源 API**，才满足"截图不出本机"的隐私卖点。

## 2. 仓库与环境

- 仓库：`third_party/PhishVLM`（code-philia/PhishVLM，已存在可直接用；USENIX Sec 24 论文延伸项目）
- 复用 `phishlens-edge/.venv`（Phishpedia/PhishIntention 同一套 torch2.1+cpu / detectron2 / numpy1.26）
- 凭据文件约定：`datasets/openai_key.txt`（OpenAI key）；`datasets/google_api_key.txt`（第 1 行 API key + 第 2 行 Search Engine ID）。**两者 git-ignored**

## 3. 输入 / 输出格式

**输入**（每站点一个子目录）：`shot.png`（整页截图）+ `info.txt`（URL）+ `html.txt`（页面源码）
**输出**：`{日期}_phishllm.txt`（TSV：folder / phish_prediction / target_prediction / brand_recog_time / crp_prediction_time / crp_transition_time）；判定 `phish` 时另存 `predict.png`（标注框）。

## 4. 四步判定流程（scripts/pipeline/phishvlm.py）

| 步骤 | 输入 | 模型/机制 | 输出 | 默认参数 |
| --- | --- | --- | --- | --- |
| 1 品牌识别 `brand_recognition_llm` | logo 裁剪图（RCNN 检出框） | **GPT-4o-mini** 视觉问答（prompt: brand_recog_prompt.json，答品牌官方域名） | 品牌域名（如 usenix.org） | temperature=0, max_tokens=10, sleep 0.5 |
| 1.5 hosting 白名单短路 | 品牌域名 | `datasets/hosting_blacklists.txt` | 命中 → benign | — |
| 2 域名一致性 `domain_brand_inconsistent` | 品牌域名 vs 当前 URL | tldextract 比较 domain/suffix | 不一致 = 可疑 | — |
| 3 品牌验证 `brand_validation` | 页面 logo 图 | **Google Custom Search 图搜 top-k** + siamese 相似度 | 通过/失败（决定是否继续） | k=10, siamese_thre=0.7；可关（activate:True） |
| 4a CRP 分类 `crp_prediction_llm` | 整页截图 | **GPT-4o-mini** CoT 分类（A 凭据页/B 非凭据页） | A → 进入决策；B → 步骤 4b | temperature=0, max_tokens=200 |
| 4b CRP 转换 `ranking_model` | 所有可点 UI 元素截图 | **GPT-4o-mini** 排序 `[idx]` → Selenium 点击最可能登录 UI（循环） | 新页面截图 → 回步骤 1 | depth_limit=1, max_uis=30, page_load_timeout=30s |

**最终判定 `phish` 需同时满足**：① 品牌域名 ≠ 页面域名；② 品牌验证通过（图搜+siamese ≥0.7；关闭时改为品牌域名存活检查）；③ CRP 分类为凭据页（A）。托管商品牌（hosting_blacklists.txt）直接放行。

## 5. CPU 视觉骨干实测（tools/verify_phishvlm_vision.py，百度 demo 截图）

| 模块 | 结果 | 耗时 |
| --- | --- | --- |
| 模型加载（layout_detector+ocr_siamese+ocr_pretrained） | — | 0.8s |
| LayoutDetector（预算框） | 28 个元素框 | 1.82s |
| LogoDetector（logo 类） | 2 个框 | 1.79s |
| LogoEncoder（OCR-aided Siamese，2560 维 L2 归一） | embedding (2560,) | 0.44s |

- 权重复用 PhishIntention：`layout_detector.pth`、`ocr_siamese.pth.tar`、`ocr_pretrained.pth.tar`（+ crp_classifier/crp_locator/expand_targetlist/domain_map 备用），`scripts/phishintention/models/`
- siamese_thre = 0.87（configs.yaml）
- ΦhishVLM 的 model_config 不回本地参考库缓存（品牌验证走图搜），故无 T3 的长路径问题

## 6. 阻塞点与降级方案

**阻塞链（已逐层实证）**：
1. 缺 `openai` / `webdriver-manager` → pip 安装即可（已装）
2. 缺 `datasets/openai_key.txt` → `OpenAIError: Missing credentials`（phishvlm.py 初始化 OpenAI client 时抛）
3. 填 key 后仍缺 `datasets/google_api_key.txt` → `FileNotFoundError`（phishvlm.py `__init__` 硬读）

→ 两条 key 需要**用户自备**（OpenAI 需海外账号/额度；Google PSE 免费但需 GCP 项目）。提供后即可端到端跑通原版。

**降级方案（与参赛方案第 4 级设计一致，无需任何外部凭证）**：
- 品牌识别：本地 OCR-aided Siamese + expand_targetlist（3064 logo 库，已有缓存）替代 GPT-4o-mini 品牌问答；或将 logo 图交给本地 Qwen2.5-VL 判断品牌
- 品牌验证：本地参考库 top-k 相似度（阈值 0.7/0.87）替代 Google 图搜
- CRP 判定：`html_heuristic`（关键词启发式：password/login/sign in…）+ 本地 Qwen2.5-VL-3B/7B（int4 量化 CPU 可跑）替代 GPT-4o-mini
- 登录 UI 排序：基于 DOM 的规则（找 input[type=password] 所在表单）替代 VLM 排序，简化后仅保留一次点击

## 7. 对后续步骤的启示

1. 参赛简报的 Methodology 可写"我们以 PhishVLM 的 CoT 复核为参照，但将 LLM 环节本地化（Qwen2.5-VL）以支撑隐私卖点"——这是与闭源方案的差异化点。
2. 视觉骨干（检测 1.8s + 编码 0.44s）为 T6 本机服务的第 1-2 级直接可用；阈值 0.87 可在评测中调。
3. gpt-4o-mini 的 CoT CRP 判定（先找敏感关键字再结论）可沉淀为 T6 第 4 级的 VLM prompt 模板。