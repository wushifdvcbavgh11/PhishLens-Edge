# T2: Phishpedia 原版复现记录（CPU）

> 日期：2026-09-29 | 环境：Windows 11 + Python 3.11.9 (venv) | 全部 CPU 推理

## 1. 环境与安装

- 仓库：`third_party/Phishpedia`（lindsey98/Phishpedia，`git clone --depth 1`）
- Python 3.11.9 venv：`phishlens-edge/.venv`
- 关键依赖（版本经过 Windows 适配）：
  - `torch==2.1.0+cpu` + `torchvision==0.16.0+cpu`（来自 download.pytorch.org/whl/cpu）
  - `detectron2==0.6+18f6958pt2.1.0cpu`（MiroPsota 预编译 Windows wheel，cp311）
  - `numpy==1.26.4`（torch 2.1 要求 numpy<2；Phishpedia 原锁 1.23.0 无 cp311 wheel）
  - `Pillow>=10`、`opencv-python==4.10.0.84`（原锁 8.4.0/最新无 cp311 兼容，功能等价）
  - 其余：scikit-learn、spacy、bs4、matplotlib、pandas、nltk、tqdm、unidecode、gdown、tldextract、scipy、fvcore、lxml、psutil、flask、flask-cors、pycocotools

## 2. 权重获取（Google Drive 不可达时的替代源）

Phishpedia README 的权重在 Google Drive（国内网络不可达），改用 HuggingFace 镜像 hf-mirror.com 的 `code-philia/Phishpedia`：

| 文件 | 远端路径 | 大小 | 本地位置 |
| --- | --- | --- | --- |
| rcnn_bet365.pth | `detectron2_pedia/output/rcnn_2/rcnn_bet365.pth` | 330MB | `models/rcnn_bet365.pth` |
| resnetv2_rgb_new.pth.tar | `siamese_pedia/resnetv2_rgb_new.pth.tar` | 192MB | `models/resnetv2_rgb_new.pth.tar` |
| expand_targetlist.zip | `siamese_pedia/expand_targetlist.zip` | 212MB | `models/expand_targetlist.zip`（解压到 `models/expand_targetlist/`，277 品牌 / 3063 logo） |
| domain_map.pkl | `siamese_pedia/domain_map.pkl` | 12KB | `models/domain_map.pkl` |

## 3. 需要的补丁（复现原版必须）

1. **`models/faster_rcnn.yaml` 缺失**（原仓库不含，需自建）：官方新版用 rcnn_bet365.pth（3 类输出：bg+2），配置以 PhishIntention 的 `faster_rcnn_web.yaml` 为蓝本，把 `ROI_HEADS.NUM_CLASSES` 从 5 改为 **2**、去掉训练相关段。文件在 `models/faster_rcnn.yaml`，已按权重实测可用（用 `torch.load` 检查了 `cls_score.weight (3,1024)` 与 `bbox_pred (8,1024)` 确认 NUM_CLASSES=2 / mask off）。
2. **首次运行崩溃修复**（`configs.py`）：`cache_reference_list()` 返回 list，而 `chunked_dot()` 调用 `.shape`；原版在首次缓存构建后必然报 `AttributeError: 'list' object has no attribute 'shape'`。修复：缓存后 `np.array(LOGO_FEATS, dtype=np.float32)` / `np.array(LOGO_FILES)` 再保存。
3. **品牌目录位置**：expand_targetlist.zip 解压后目录必须位于 `models/expand_targetlist/`（configs.py 按 `zip 名去后缀` 拼路径）。

## 4. 运行方式

```bash
cd third_party/Phishpedia
KMP_DUPLICATE_LIB_OK=TRUE .venv/Scripts/python.exe phishpedia.py --folder datasets/test_sites --output_txt results.txt
```

输入目录结构（每站一个子目录）：
```
test_sites/<domain_name>/
  shot.png    # 网页截图（RGB，任意尺寸）
  html.txt    # 页面 HTML（本次流程未使用）
  info.txt    # 访问的 URL
```

## 5. 输入输出格式（test_orig_phishpedia）

- **输入**：`url: str`、`screenshot_path: str`、`html_path: str`
- **输出**（tuple）：
  `phish_category`（0=良性, 1=钓鱼）、`pred_target`（预测品牌 str/None）、`matched_domain`（品牌域名列表/None）、`plotvis`（标注图）、`siamese_conf`（置信度/None）、`pred_boxes`（Nx4 logo 框/None）、`logo_recog_time`、`logo_match_time`

## 6. 检测流程（内部逻辑）

1. **Step1 元素检测（logo_recog.py）**：`pred_rcnn()` → Detectron2 Faster-RCNN（rcnn_bet365.pth，DETECT_THRE=0.05）检测 5 类元素，取 `pred_classes==1` 作为 logo 框。
2. **Step2 品牌比对（logo_matching.py）**：`check_domain_brand_inconsistency()`：
   - BiT-M-R50x1 孪生网络提取截图 logo 区域 embedding（128x128、L2 归一化）
   - 与 3063 个品牌 logo 参考 embedding 做余弦相似度，阈值 **0.87**（configs.yaml MATCH_THRE）
   - `pred_brand()` 取 top-3 候选，按 `domain_map.pkl` 查品牌合法域名
   - **域名一致性检查**：若页面域名在品牌合法域名列表 → 良性；若二级域名相同且国家 TLD → 良性；否则 → 钓鱼（brand/domain 不一致）
3. 输出预测品牌 + 置信度 + 判定。

## 7. 测试结果（CPU，datetime=2026-09-29）

| 样本目录 | URL | 判定 | 预测品牌 | siamese_conf | logo_recog_time | logo_match_time |
| --- | --- | --- | --- | --- | --- | --- |
| accounts.g.cdcde.com | 仿 Google 登录 | **1（钓鱼）** | Google | 0.9501 | 1.70s | 0.13s |
| ru.russianmastercach-republick.xyz | 仿 Mastercard | **1（钓鱼）** | Mastercard International | 0.9413 | 1.70s | 0.11s |
| www.paypal.com | 真 PayPal | **0（良性）** | None（域名一致） | 0.8700* | 1.21s | 0.10s |

\* paypal 样本虽然匹配到 PayPal 品牌（conf 0.87），但页面域名 www.paypal.com 与品牌合法域名一致 → 良性。这是一个关键机制：**匹配到品牌 ≠ 钓鱼，品牌+域名不一致才是钓鱼**（正是竞赛方案"三条件判定"中的两条）。

- 特征库缓存构建耗时：约 5 分钟一次，之后 `LOGO_FEATS.npy`/`LOGO_FILES.npy` 直接加载。
- 单张截图端到端耗时：约 1.3–1.8s（纯 CPU，其中 logo 检测约 1.2–1.7s 是大头，比对约 0.1s）。

## 8. 已知限制（供后续漏斗设计参考）

- 原版 277 品牌以欧美为主，**缺支付宝、微信、国内银行、12306、学信网等中文品牌** → 需要在 T7 品牌库阶段增量扩展 targetlist。
- logo 检测对中文页面截图（文本型 logo）识别率待验证。
- 首次运行需构建特征缓存（5 分钟），后续由 npy 加载（秒级）。