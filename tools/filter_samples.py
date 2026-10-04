"""T8 测试集筛选：剔除空白页/拦截页/同模板重复，选出有效样本。

判定标准：
  - 灰度标准差 graystd > MIN_STD（非纯色/空白页）
  - 文件大小 > MIN_BYTES
  - 内容哈希去重（同模板多域名重复只留第一个）
输出：
  testset/phishing/selected_manifest.tsv / testset/benign/selected_manifest.tsv
  （含 idx, url, bytes, std, title）
"""
import hashlib
import os
import sys

import numpy as np
from PIL import Image

MIN_STD = 30.0
MIN_BYTES = 12000
MAX_TOTAL = 50  # 目标样本数（钓鱼取前 MAX_TOTAL 个有效、按 std 排序）

def img_stats(path: str):
    try:
        im = Image.open(path).convert("L")
        a = np.asarray(im, dtype=np.float32)
        return float(a.std()), a.shape
    except Exception as e:
        return None, str(e)

def collect(src_dir: str, manifest: str, target_tsv: str):
    rows = []
    with open(manifest, encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            d = dict(zip(header, parts))
            if d.get("status") != "ok":
                continue
            if src_dir.endswith("phishing"):
                png = f"{src_dir}/phish_{int(d['idx']):03d}.png"
            else:
                png = f"{src_dir}/{d['label']}.png"
            if not os.path.exists(png):
                continue
            rows.append((d, png))
    seen = {}
    kept = []
    dropped = {"small": 0, "blank": 0, "dupe": 0, "err": 0}
    for d, png in rows:
        sz = os.path.getsize(png)
        std, shape = img_stats(png)
        if std is None:
            dropped["err"] += 1
            continue
        if sz < MIN_BYTES:
            dropped["small"] += 1
            continue
        if std < MIN_STD:
            dropped["blank"] += 1
            continue
        with open(png, "rb") as f:
            h = hashlib.md5(f.read(65536)).hexdigest()  # 前64KB哈希去重（同模板页面）
        if h in seen:
            dropped["dupe"] += 1
            continue
        seen[h] = True
        kept.append((std, sz, h, d, png))
    # 按丰富度排序，取前 MAX_TOTAL
    kept.sort(key=lambda x: -x[0])
    chosen = kept[:MAX_TOTAL]
    with open(target_tsv, "w", encoding="utf-8") as f:
        f.write("idx\turl\tbytes\tstd\ttitle\n")
        for i, (std, sz, h, d, png) in enumerate(chosen):
            f.write(f"{i}\t{d.get('url','')}\t{sz}\t{std:.1f}\t{d.get('title','')}\n")
    print(f"[{src_dir}] 候选 {len(rows)} | 保留去重后 {len(kept)} | 选中 {len(chosen)}")
    print(f"  剔除: 小文件 {dropped['small']} 空白 {dropped['blank']} 重复 {dropped['dupe']} 读取失败 {dropped['err']}")
    return len(chosen)

if __name__ == "__main__":
    srcs = [
        ("testset/phishing", "testset/phishing/manifest.tsv", "testset/phishing/selected_manifest.tsv"),
        ("testset/benign", "testset/benign/manifest.tsv", "testset/benign/selected_manifest.tsv"),
    ]
    ok = 0
    for s in srcs:
        n = collect(*s)
        if n >= 50:
            ok += 1
            print(f"  -> {s[0]} 达标 ({n} >= 50)")
        else:
            print(f"  -> {s[0]} 不足 ({n} < 50)，需补抓 {50-n} 张")
    print(f"两集达标数: {ok}/2")
    sys.exit(0 if ok == 2 else 1)