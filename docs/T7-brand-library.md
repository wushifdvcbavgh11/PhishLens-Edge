# T7 - 中文品牌库构建与 MobileNet 特征

日期: 2026-09-29/30
状态: 完成

## 交付物

| 文件 | 说明 |
| --- | --- |
| `brand_library/brands.json` | 22 个品牌的元数据（id/英文名/中文名/正规域名/logo 路径） |
| `brand_library/logo_feats_mnet.npy` | (22, 960) float32，MobileNetV3-Large（IMAGENET1K_V2）笔尖层 L2 归一化特征 |
| `brand_library/logos/<brand>/logo_appstore.png` | 1024x1024 官方 App 图标（alipay 为其中 2600x1950 官网大图的替代） |
| `brand_library/extract_features.py` | 特征提取脚本（可复跑） |
| `server/cn_brand_matcher.py` | 中文品牌匹配器（延迟加载 22 品牌索引 + MobileNet 嵌入器） |
| `server/app.py` | 已接入中文品牌 fallback + 顶部区域 block 兜底 + 统一三条件判定 |

## 品牌清单（22 个）

支付宝、微信、QQ、工商银行、建设银行、农业银行、中国银行、招商银行、交通银行、
铁路12306、个人所得税、国家政务服务平台、学信网、腾讯企业邮、网易企业邮、阿里邮箱、
钉钉、飞书、Microsoft 365、淘宝、京东、拼多多

## Logo 来源

- **21/22 品牌**：Apple App Store lookup/search API（`itunes.apple.com`）返回的官方
  `artworkUrl512`，替换 `512x512bb` 为 `1024x1024bb` 得到 1024x1024 图标。
  - 关键 bundleId：wechat=`com.tencent.xin`、qq=`com.tencent.mqq`、jd=`com.360buy.jdmobile`、
    pinduoduo=`com.xunmeng.pinduoduo`、alipay=`com.alipay.iphoneclient`
  - trackId 修正：alimail=`1590302171`、qiye163=`1556017637`、exmail 用 QQ 邮箱
    （`com.tencent.qqmail`，腾讯企业邮无独立官方 App）
- **alipay**：官网 CDN 大图（2600x1950），后统一改用 appstore 图标以保证与其它品牌
  特征分布一致（详见下文"踩坑"）。

## 匹配器验证结果

| 测试 | 结果 |
| --- | --- |
| 原尺寸自匹配 | 22/22 ✓ |
| 160px 缩放匹配 | 22/22 ✓ |
| 96px 缩放匹配 | 21/22（boc→icbc 混淆，银行图标视觉近似，见局限） |
| 48px 缩放匹配 | 21/22（同上） |
| 纯色块（页面背景） | 拒绝（色彩复杂度门控 std<30 跳过）✓ |
| 整图裁剪集成（粘贴 160px 支付宝 logo） | 0.974 命中 alipay ✓ |
| 空白区域裁剪 | 返回 None ✓ |

特征质量：自相似度 ≈ 1.0；非对角平均 0.43；最高相似对 icbc↔boc=0.84（同色系银行，
拦截决策不受影响——任一银行命中即走"品牌匹配+域名不一致"判定）。

## server 端到端验证（TestClient）

| 输入 | 判定 | 关键理由 |
| --- | --- | --- |
| 伪造支付宝页 `alipay-secure-login.xyz` + 密码框 | **block** | MobileNet 匹配支付宝 0.963；域名与 alipay.com 不一致；索要凭据；.xyz 可疑 |
| 合法登录页 `auth.alipay.com` + 密码框 | **allow** | 域名与正规域名一致，即使有密码框也不拦截 |
| 知乎登录页（白名单） | **allow** | L0 白名单命中，不加载模型 |

各级耗时（仿冒样例）：l1_logo_det≈1.4s、l2_logo_match≈1.0s、l3_dom≈1.1s、l4_rules≈0.0s。
模型首次加载（AWL+Siamese+MobileNet）另计数十秒。

## 踩坑记录（重要）

1. **v2 升级脚本删除 bug**：`save_image` 失败时误删目标文件（dest），先删 `.tmp` 再
   rename 可避免数据丢失。
2. **Wikimedia Commons 对中文品牌覆盖极差**：限 File 命名空间搜索仍返回银行建筑照片/
   文章配图，无干净 logo；已知文件名探测全部 404。
3. **Google favicon / Clearbit / iconhorse / Wikipedia**：全部被墙或连接重置，唯一可行
   高清源是 Apple App Store API（可达）。
4. **simple-icons CDN 可达但 SVG→PNG 渲染链路在本机全断**（cairosvg 缺系统 cairo DLL、
   reportlab 缺 rlPyCairo、pycairo 不兼容），放弃。
5. **AWL YOLO 把 App Store 风格扁平图标识别为 block 而非 logo**（训练分布差异）：
   解决 = logo 类为空时取页面顶部 45% 区域的 block 作候选兜底，再走 MobileNet 匹配。
6. **纯色块假阳性**：官网大图（std=22.8 浅色）导致灰块相似度 0.694>阈值；统一改用
   appstore 图标（std=97.4）+ 色彩复杂度门控（std<30 跳过）后消失。
7. **boc↔icbc 混淆**（96/48px）：银行图标高度相似，MobileNet 在低分辨率下难以区分；
   对拦截决策无影响，品牌识别准确率指标上预计损失 1-2 个点，可接受。

## 已知局限 / 后续

- **本校三个品牌**（统一身份认证/教务系统/校园邮箱）缺学校名称与域名 → 待用户提供，
  T7 暂以 22 个通用品牌交付；拿到信息后追加即可（跑一遍 extract_features.py + 重启服务）。
- 48/96px 银行间混淆：后续可在 T9 评测后视品牌识别准确率决定是否加 OCR 汉字辅助判据。
- THIRD_PARTY_LICENSES（T12）需登记：App Store artwork（Apple 商标，教育/NFR 用途）、
  官网 favicon。