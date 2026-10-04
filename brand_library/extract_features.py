"""Extract e-commerce-grade logo embeddings for the brand library.

Uses MobileNetV3-Large (IMAGENET1K_V2) penultimate features, L2-normalized.
Outputs:
  brand_library/brands.json        - metadata (id/name/cn/domain(s)/logo_path/source)
  brand_library/logo_feats_mnet.npy - (N, 1280) float32 embeddings aligned with brands.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision import models, transforms

ROOT = Path("C:/Users/wushi/.qianfan/workspace/sessions/458fc032984144e19e0b2a360d5a5c27/2026-09-28/new-chat/phishlens-edge/brand_library")
LOGO_DIR = ROOT / "logos"

# brand_id -> (name_en, name_cn, official domains, logo file preference)
BRANDS = {
    "alipay":      ("Alipay", "支付宝", ["alipay.com"], "logo_appstore.png"),
    "wechat":      ("WeChat", "微信", ["weixin.qq.com", "wechat.com"], "logo_appstore.png"),
    "qq":          ("QQ", "QQ", ["im.qq.com", "qq.com"], "logo_appstore.png"),
    "icbc":        ("ICBC", "工商银行", ["icbc.com.cn"], "logo_appstore.png"),
    "ccb":         ("CCB", "建设银行", ["ccb.com"], "logo_appstore.png"),
    "abchina":     ("ABC", "农业银行", ["abchina.com"], "logo_appstore.png"),
    "boc":         ("BOC", "中国银行", ["boc.cn"], "logo_appstore.png"),
    "cmb":         ("CMB", "招商银行", ["cmbchina.com"], "logo_appstore.png"),
    "bankcomm":    ("BOCOM", "交通银行", ["bankcomm.com"], "logo_appstore.png"),
    "12306":       ("12306", "铁路12306", ["12306.cn"], "logo_appstore.png"),
    "geeren":      ("IIT", "个人所得税", ["chinatax.gov.cn"], "logo_appstore.png"),
    "gjzwfw":      ("NGS", "国家政务服务平台", ["gjzwfw.www.gov.cn"], "logo_appstore.png"),
    "chsi":        ("CHSI", "学信网", ["chsi.com.cn"], "logo_appstore.png"),
    "exmail":      ("Tencent Exmail", "腾讯企业邮", ["exmail.qq.com"], "logo_appstore.png"),
    "qiye163":     ("NetEase Mail", "网易企业邮", ["qiye.163.com"], "logo_appstore.png"),
    "alimail":     ("Alibaba Mail", "阿里邮箱", ["alimail.alibaba.com"], "logo_appstore.png"),
    "dingtalk":    ("DingTalk", "钉钉", ["dingtalk.com"], "logo_appstore.png"),
    "feishu":      ("Feishu/Lark", "飞书", ["feishu.cn", "larksuite.com"], "logo_appstore.png"),
    "microsoft365": ("Microsoft 365", "微软365", ["microsoft.com", "office.com"], "logo_appstore.png"),
    "taobao":      ("Taobao", "淘宝", ["taobao.com"], "logo_appstore.png"),
    "jd":          ("JD.com", "京东", ["jd.com"], "logo_appstore.png"),
    "pinduoduo":   ("Pinduoduo", "拼多多", ["pinduoduo.com"], "logo_appstore.png"),
    "coinbase":    ("Coinbase", "Coinbase", ["coinbase.com"], "logo_appstore.png"),
    "metamask":    ("MetaMask", "MetaMask", ["metamask.io"], "logo_appstore.png"),
    "ledger":      ("Ledger", "Ledger", ["ledger.com"], "logo_appstore.png"),
    "instagram":   ("Instagram", "Instagram", ["instagram.com"], "logo_appstore.png"),
    "xfinity":     ("Xfinity", "Xfinity", ["xfinity.com"], "logo_appstore.png"),
    "shopee":      ("Shopee", "Shopee", ["shopee.com"], "logo_appstore.png"),
    "steam":       ("Steam", "Steam", ["steampowered.com"], "logo_appstore.png"),
    "paypal":      ("PayPal", "PayPal", ["paypal.com"], "logo_appstore.png"),
}

TF = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def load_embedder():
    m = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2)
    m.eval()
    # penultimate layer: features.avgpool + flatten
    m = torch.nn.Sequential(*(list(m.children())[:-1]))  # remove classifier
    return m


def main():
    embedder = load_embedder()
    ids, names_en, names_cn, domains, logo_paths, sources = [], [], [], [], [], []
    feats = []
    with torch.no_grad():
        for bid, (en, cn, dms, pref) in BRANDS.items():
            d = LOGO_DIR / bid
            logo = d / pref
            if not logo.exists():
                # fallback: any png/jpg in dir with max size
                cands = [f for f in d.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg") and "appstore" not in f.name]
                if not cands:
                    print(f"[WARN] {bid}: no image found, skipped")
                    continue
                logo = max(cands, key=lambda f: max(Image.open(f).size))
            im = Image.open(logo).convert("RGB")
            t = TF(im).unsqueeze(0)
            feat = embedder(t).flatten(1).numpy()[0]
            feat = feat / (np.linalg.norm(feat) + 1e-9)
            feats.append(feat)
            ids.append(bid); names_en.append(en); names_cn.append(cn)
            domains.append(dms); logo_paths.append(str(logo))
            sources.append("appstore/offical")  # placeholder, refine later
            print(f"{bid:14s} {str(im.size):12s} feat_dim={feat.shape[0]}")

    feats = np.stack(feats).astype(np.float32)
    meta = []
    for i in range(len(ids)):
        meta.append({"id": ids[i], "name": names_en[i], "name_cn": names_cn[i],
                     "domains": domains[i], "logo_path": logo_paths[i]})
    np.save(ROOT / "logo_feats_mnet.npy", feats)
    (ROOT / "brands.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(ids)} brands -> brands.json + logo_feats_mnet.npy shape={feats.shape}")


if __name__ == "__main__":
    main()