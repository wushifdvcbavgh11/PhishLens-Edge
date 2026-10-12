# T16: 安装、依赖与启动检查统一（Setup, Dependencies & Offline Demo Notes）

> 日期：2026-10-08 | 目标：让评审/合作者能按 README 顺利装起来，并如实区分
> 「已实现」与「规划中」能力，纠正文档中与实际行为不符的表述。

## 1. 本轮修正了什么

### 1.1 新增 `tools/check_env.py`（环境就绪自检）

纯 stdlib、只读、失败提示带修复命令。运行：
```bash
.venv/Scripts/python tools/check_env.py
```
覆盖 6 大类检查，全部 `[OK]` 时输出 `READY`，exit code 0：

| 类 | 检查项 |
|---|---|
| Python | >= 3.11（vision wheels 为 cp311 构建） |
| 依赖 | fastapi/uvicorn/pydantic/tldextract/PyYAML/numpy/Pillow/torch/torchvision/detectron2/sklearn/joblib/cv2 的 import 与最低版本（Pillow 按发行名 `Pillow` 读版本，避免 PIL 误报） |
| 模型权重 | 从 `configs/configs.yaml` **动态解析** 7 个路径（layout_detector.pth、crp_classifier.pth.tar、crp_locator.pth、ocr_siamese.pth.tar、ocr_pretrained.pth.tar、domain_map.pkl、expand_targetlist/） |
| 品牌库 | brands.json、logo_feats_mnet.npy、注册品牌数、logos 目录（兼容 logo.png / logo_apple.png / logo_appstore.png / logo_official.png / logo_iphone2x.png / logo_pad2x.png / mg_*.png / logo.in.svg 等真实命名） |
| UCI 预筛选 | model/uci_phishing_clf.joblib + uci_phishing_features.json |
| 扩展与演示页 | manifest.json（MV3）/ content.js / background.js；testset/fake 三件套 |
| 端口 | 8765（分析服务）/ 8001（演示页）是否已被占用 |

已知阈值说明：MobileNet-V3 embedder（CN 品牌比对用）首次运行需联网从
torch hub 下载 ~22MB 权重并缓存到 `~/.cache/torch/checkpoints/`，此后离线可用；
本机已缓存，脚本以 `[WARN]` 提示（仅提示，不阻塞）。

### 1.2 新增 `tools/setup_vision.py`（vision 栈安装器）

```bash
.venv/Scripts/python tools/setup_vision.py
```
- 安装 `numpy<2` + `Pillow>=10`（PyPI）；
- torch/torchvision/detectron2：**优先使用 `third_party/wheels/` 内已备好的本地
  wheel**（torch-2.1.0+cpu、torchvision-0.16.0+cpu、detectron2-0.6+18f6958pt2.1.0cpu，
  cp311/Windows），缺失时回退到官方 CPU 索引
  （`https://download.pytorch.org/whl/cpu`）；
- 幂等，重跑安全；检测到已满足的包直接跳过。

### 1.3 新增 `tools/download_weights.py`（权重下载器）

```bash
.venv/Scripts/python tools/download_weights.py [--force]
```
从 hf-mirror.com 下载 L1/L2 漏斗所需的 7 个文件到
`third_party/PhishIntention/models/` 并解压 `expand_targetlist.zip`。已有文件
自动跳过（本机复现时核对过字节数，见下方来源表）。

**关键事实（本次核实）**：HF 仓库里文件的**实际路径**与 `configs.yaml` 期望的
文件名不是一一对应，必须做映射（此前 README 只写"放 detection_models/"，
路径是错的）：

| 本地文件名（configs.yaml 期望） | 远端路径（hf-mirror.com） | 字节数 |
|---|---|---|
| layout_detector.pth | code-philia/PhishIntention: `AWL_detector_utils/output/website_lr0.001/model_final.pth` | 330,145,096 |
| crp_locator.pth | code-philia/PhishIntention: `crp_locator_utils/login_finder/output/lr0.001_finetune/model_final.pth` | 329,981,096 |
| crp_classifier.pth.tar | code-philia/PhishIntention: `crp_classifier_utils/output/Increase_resolution_lr0.005/BiT-M-R50x1V2_0.005.pth.tar` | 188,221,125 |
| ocr_siamese.pth.tar | code-philia/PhishIntention: `OCR_siamese_utils/output/targetlist_lr0.01/bit.pth.tar` | 193,786,609 |
| ocr_pretrained.pth.tar | code-philia/PhishIntention: `OCR_siamese_utils/demo_downgrade.pth.tar` | 84,424,941 |
| domain_map.pkl | code-philia/PhishIntention: `phishpedia_siamese/domain_map.pkl` | 220,978（本地工作副本多 3 个品牌条目，见下） |
| expand_targetlist.zip | code-philia/Phishpedia: `siamese_pedia/expand_targetlist.zip` | 212,261,039 |

- domain_map.pkl：本机工作副本（221,059 字节，5603 品牌）比远端多
  Coinbase / Ledger / Xfinity 三个条目，是测试期补充的；脚本因"已有文件跳过"
  行为不会覆盖它，全新环境拿到的是 5600 条原版，对漏斗相关品牌无功能差异。
- expand_targetlist.zip 用的是 **Phishpedia 版**（212,261,039 字节）而非
  PhishIntention 版（212,573,017 字节）——T3 复现时确认的做法，未造成差异。

### 1.4 README.md 对齐事实（纠正 5 处）

1. 品牌数：`30 protected brands` → **39 protected brands**；
2. 截图隐私表述：「never written to disk」→ **已实现（T17）**：截图以 base64
   在内存中传递、不离开本机；视觉检测用 `_pred_rcnn_from_bytes` 直接解码
   ndarray，品牌比对相关 PIL 消费点传 `io.BytesIO`，**全程不写任何临时文件**
   （`server/app.py` 已移除 `NamedTemporaryFile` / `os.unlink`）；
3. 权重路径：`detection_models/` → **`models/`（相对 PhishIntention 仓库根，
   与 configs.yaml 一致）**；
4. 浏览器前提：Chrome/Edge → **Microsoft Edge 154+**（Chrome 147 无法从命令行
   加载 unpacked 扩展）；
5. 评测结果：并入 **held-out 独立评测口径**（226 phish + 40 benign，从未参与
   调参），并明确区分 dev（调参集）与 held-out（独立集）两套数字；
6. 明确标注：L4 VLM 复核（Qwen2-VL-2B）与 Hailo-10H 硬件加速为**规划中**能力，
   当前发布版跑的是 L0–L3 漏斗（CPU）+ 规则型 L4 兜底。

### 1.5 `start_demo.bat` 修正

- 「chrome://extensions」→「edge://extensions」（与 Edge 导向一致）；
- 行尾保持 CRLF（DOS batch 必需）。

## 2. 离线演示说明（评审/无网环境）

- 分析服务 8765 + 演示页服务 8001 全部绑定 `127.0.0.1`，**不依赖公网**；
- 唯一需要**一次联网**的时刻：
  1. 首次安装 vision 栈（若 `third_party/wheels/` 随仓库分发则完全离线）；
  2. `tools/download_weights.py` 下载 ~1.6GB 权重（一次）；
  3. CN 品牌比对的 MobileNet-V3 embedder 首次从 torch hub 缓存 ~22MB；
- 本机演示路径：`start_demo.bat` → Edge 加载 `browser-extension/` → 打开
  `http://127.0.0.1:8001/fake_alipay.html`、`fake_icbc.html`、`fake_wechat.html`
  → 点击密码框 → 全屏警告覆盖层（品牌名 + 当前域名 vs 正规域名 + 返回 +
  「我确认安全，继续访问」）。

## 3. 已实现 vs 规划中（如实）

| 能力 | 状态 |
|---|---|
| MV3 扩展（截图、DOM 信号、密码框聚焦二次截图、全屏警告页） | **已实现**（v1.0） |
| FastAPI 五级漏斗：L0 域名白名单+LRU 缓存 → L1 Faster R-CNN 元素检测 → L2 OCR-aided Siamese + MobileNet 中文品牌比对 → L3 DOM 凭证索要判断 → L4 规则兜底 | **已实现**（CPU） |
| UCI 文本预筛选（sklearn 分类器, L0'） | **已实现** |
| 39 品牌库（中行/高校/平台）+ AMBIGUITY_RATIO 防兄弟品牌混淆 | **已实现** |
| 事件日志 event.log（URL/品牌/判定/分级耗时，不含截图与 DOM 载荷） | **已实现** |
| L4 本地 VLM 复核（Qwen2-VL-2B） | **规划中**（接口已留，规则兜底在跑） |
| Hailo-10H / UGen300 硬件加速（40 TOPS, INT4, ~2.5W） | **规划中**（决赛阶段） |

## 4. 验证结果

- `tools/check_env.py` 全项 `[OK]`，`READY`，exit 0；0 warning；
- `tools/setup_vision.py` 幂等运行 OK（本机依赖已满足，全部跳过）；
- `tools/download_weights.py` 的远端 URL 全部经 HEAD 探活 + Content-Length 与
  本机工作副本逐字节比对确认（唯一例外 domain_map.pkl 差 81 字节 = 本地
  +3 品牌条目，见上）。