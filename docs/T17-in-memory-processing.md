# T17：截图全程内存处理（无临时文件落盘）

评审反馈指出「主检测流程不依赖云端、但截图会生成临时文件，『仅在内存处理』还没做到」。
本里程碑把这最后一块补齐：**从扩展收到 base64 到判定返回，截图只在内存中流转，
不再写入任何临时文件**。

## 改动内容（`server/app.py` + `server/cn_brand_matcher.py`）

| 位置 | 改动前 | 改动后 |
| --- | --- | --- |
| L1 logo 检测 | `tempfile.NamedTemporaryFile(suffix=".png", delete=False)` 写盘 → `pred_rcnn(im=路径)` | 新增 `_pred_rcnn_from_bytes(img_bytes, predictor)`：`cv2.imdecode(np.frombuffer(...))` 直接得到 BGR ndarray，行为与上游 `pred_rcnn`（`cv2.imread`）等价（含 BGRA→BGR 分支），返回 `(pred_boxes, pred_classes, pred_scores)` |
| block-fallback 取页面高度 | `PIL.Image.open(临时路径)` 取 height | `PIL.Image.open(io.BytesIO(img_bytes))` |
| L2 品牌比对（PhishIntention） | `check_domain_brand_inconsistency(..., tmp_path, ...)` | 传入独立 `io.BytesIO(img_bytes)`（PIL 的 `Image.open` 原生支持 file-like） |
| L2 中文品牌 fallback | `cn_brand_matcher.match_logo_boxes(tmp_path, ...)` | `match_logo_boxes(io.BytesIO(img_bytes), ...)`，函数签名放宽为「路径或 file-like」 |
| 收尾清理 | `os.unlink(tmp_path)` | 删除（无文件可删），`import tempfile` 一并移除 |

注意点：PIL `Image.close()` 会关闭其底层 file-like，因此**每个 PIL 消费点各用独立
BytesIO**，不共享同一个缓冲，避免「一个分支 close 后毒化其他分支」。

## 验证（CPU 实跑，服务 :8765）

| 用例 | 期望 | 实测 | L1/L2 耗时 |
| --- | --- | --- | --- |
| `accounts.g.cdcde.com`（仿 Google 登录） | block | **block**（brand=Google，域不一致） | 1.761s / 0.293s |
| `ru.russianmastercach-republick.xyz`（仿 Mastercard） | block | **block**（brand=Mastercard Inc.，.xyz + UCI 0.99） | 1.676s / 0.297s |
| `www.paypal.com`（真站） | allow | **allow**（L0 白名单短路，不触视觉） | — |
| `alipay-secure-login.top`（中文假页） | block | **block**（brand=支付宝，MobileNet 0.734，走 BytesIO fallback 路径） | 1.646s / 0.915s |

四个用例全部符合预期，中文品牌 fallback 的 BytesIO 分支被单独覆盖。
代码层确认：`grep tempfile app.py` 无残留。

## 隐私表述同步

- `README.md` Privacy & ethics：由「写临时 PNG 后立即删除」更新为「**全程不写盘**」。
- `docs/T16-setup-and-deps.md` 1.4 节第 2 条的「如实修正」记录相应更新为用户侧已完成项。

## 边界

- event.log 仍记录 URL/品牌/判定及分级耗时（不含截图与 DOM 载荷），演示与审计不受影响。
- PhishIntention 内部 `pred_brand` 对品牌参考 logo 的读取仍走盘（那是 logo 库文件，
  非用户截图，不涉及隐私）。