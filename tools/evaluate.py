"""T9 - offline evaluation of the full PhishLens funnel on the T8 test set.

Calls server.app.analyze() directly (same code path as HTTP, no network) for:
  - 50 selected phishing screenshots (testset/phishing/selected_manifest.tsv)
  - 50 selected benign login screenshots (testset/benign/selected_manifest.tsv)
  - 3 self-built CN fake login pages (testset/fake/*.html -> their PNG)

DOM signals: only page_domain + title are real (from manifest); has_password
is NOT injected for the screenshot corpus (keeps the evaluation honest about
the visual path). The 3 fake pages carry full real DOM signals.

Outputs:
  testset/eval/results.tsv        per-sample rows
  testset/eval/metrics.json       confusion metrics
  docs/T9-evaluation.md           written by caller after review
"""
from __future__ import annotations

import base64
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import app as srv  # noqa: E402  (imports server.app, loads nothing heavy until analyze)


def load_request(url: str, png: Path, title: str, page_domain: str,
                 full_dom: bool = False) -> srv.AnalyzeRequest:
    b64 = base64.b64encode(png.read_bytes()).decode()
    dom = {
        "page_domain": page_domain,
        "has_password_input": full_dom,
        "has_login_form": full_dom,
        "form_action_domains": [page_domain] if full_dom else [],
        "title": title,
        "favicon": "",
        "brand_hints": [],
    }
    return srv.AnalyzeRequest(url=url, screenshot_base64="data:image/png;base64," + b64,
                              dom_signals=srv.DomSignals(**dom), trigger="eval")


def read_manifest(tsv: Path) -> list[dict]:
    rows = []
    with open(tsv, encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            rows.append(r)
    return rows


def benign_file_map() -> dict:
    """URL -> png filename (label.png) from the raw benign manifest."""
    m = {}
    with open(ROOT / "testset" / "benign" / "manifest.tsv", encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            if r.get("status") == "ok":
                m[r["url"]] = r["label"] + ".png"
    return m


def phish_file_map() -> dict:
    """URL -> png filename (phish_XXX.png) from the raw phishing manifest."""
    m = {}
    with open(ROOT / "testset" / "phishing" / "manifest.tsv", encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for r in rd:
            if r.get("status") == "ok":
                m[r["url"]] = f"phish_{int(r['idx']):03d}.png"
    return m


def main():
    use_full_dom = "--full-dom" in sys.argv
    out_dir = ROOT / "testset" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    phish_man = read_manifest(ROOT / "testset" / "phishing" / "selected_manifest.tsv")
    benign_man = read_manifest(ROOT / "testset" / "benign" / "selected_manifest.tsv")

    # --- phishing 50 ---
    pmap = phish_file_map()
    for (i, r) in enumerate(phish_man):
        png = ROOT / "testset" / "phishing" / pmap.get(r["url"], "")
        if not png.exists():
            print(f"  !! phishing 文件缺失: {r['url']}", flush=True)
            continue
        url = r["url"]
        host = url.split("//")[-1].split("/")[0]
        req = load_request(url, png, r.get("title", ""), host, full_dom=use_full_dom)
        rows.append({"set": "phish", "url": url, "req": req, "png": str(png)})

    # --- benign 50 ---
    bmap = benign_file_map()
    for (i, r) in enumerate(benign_man):
        png = ROOT / "testset" / "benign" / bmap.get(r["url"], r["idx"] + ".png")
        if not png.exists():
            print(f"  !! benign 文件缺失: {png}", flush=True)
            continue
        url = r["url"]
        host = url.split("//")[-1].split("/")[0]
        req = load_request(url, png, r.get("title", ""), host, full_dom=use_full_dom)
        rows.append({"set": "benign", "url": url, "req": req, "png": str(png)})

    # --- fake 3 (full DOM) ---
    for name in ["fake_alipay", "fake_wechat", "fake_icbc"]:
        png = ROOT / "testset" / "fake" / f"{name}.png"
        al = {"fake_alipay": ("https://alipay-secure-login.top/login", "支付宝", "alipay-secure-login.top"),
              "fake_wechat": ("https://weixin-verification.icu/login", "微信安全中心 - 登录", "weixin-verification.icu"),
              "fake_icbc": ("https://icbc-ebank-verify.cc/login", "中国工商银行 - 网上银行", "icbc-ebank-verify.cc")}[name]
        req = load_request(al[0], png, al[1], al[2], full_dom=True)
        rows.append({"set": "fake", "url": al[0], "req": req, "png": str(png)})

    # --- run funnel per sample ---
    results: list[dict] = []
    model_load_shown = False
    t_start = time.time()

    # warmup: run one dummy sample first so one-shot model cold-load
    # (detectron2 + Siamese + OCR, ~10 min on CPU) does not pollute
    # avg_total_s / avg_<level>_s / wall-time statistics.
    if rows:
        warm = rows[0]
        try:
            srv.analyze(srv.AnalyzeRequest(
                url="https://warmup.invalid/phish", screenshot_base64=warm["req"].screenshot_base64,
                dom_signals=srv.DomSignals(page_domain="warmup.invalid", has_password_input=False,
                                           has_login_form=False, form_action_domains=[],
                                           title="", favicon="", brand_hints=[]),
                trigger="eval_warmup"))
        except Exception:
            pass
        print(f"  warmup done (models loaded)  ({time.time()-t_start:.0f}s)", flush=True)

    for i, row in enumerate(rows):
        t0 = time.time()
        try:
            res = srv.analyze(row["req"])  # returns dict (FastAPI-annotated fn is callable)
        except Exception as exc:
            res = {"decision": "error", "brand": None, "official_domain": None,
                   "reasons": [f"{type(exc).__name__}: {exc}"], "level_times": {}, "total_time_s": 0}
        el = time.time() - t0
        lt = res.get("level_times", {})
        row_res = {
            "set": row["set"],
            "url": row["url"],
            "decision": res.get("decision"),
            "brand": res.get("brand"),
            "official": (res.get("official_domain") or "")[:60],
            "l0": lt.get("l0_whitelist", lt.get("l0_cache_hit", "")),
            "l1": lt.get("l1_logo_det", ""),
            "l2": lt.get("l2_logo_match", ""),
            "l3": lt.get("l3_dom", ""),
            "l4": lt.get("l4_rules", ""),
            "total": round(res.get("total_time_s", el), 3),
            "wall": round(el, 3),
            "reasons": " | ".join(res.get("reasons", []))[:220],
            "visual_error": res.get("visual_error") or "",
        }
        results.append(row_res)
        if not model_load_shown and res.get("models_load_time_s"):
            row_res["models_load_s"] = res["models_load_time_s"]
            model_load_shown = True
        if (i + 1) % 10 == 0:
            print(f"  processed {i+1}/{len(rows)}  ({time.time()-t_start:.0f}s)", flush=True)

    # --- write results.tsv ---
    fields = ["set", "url", "decision", "brand", "official", "l0", "l1", "l2", "l3", "l4",
              "total", "wall", "models_load_s", "reasons", "visual_error"]
    with open(out_dir / "results.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    # --- metrics ---
    phish = [r for r in results if r["set"] == "phish"]
    benign = [r for r in results if r["set"] == "benign"]
    fake = [r for r in results if r["set"] == "fake"]

    def cnt(rs, dec):
        return sum(1 for r in rs if r["decision"] == dec)

    metrics = {
        "n_phish": len(phish), "n_benign": len(benign), "n_fake": len(fake),
        "phish": {"block": cnt(phish, "block"), "uncertain": cnt(phish, "uncertain"),
                  "allow": cnt(phish, "allow"), "error": cnt(phish, "error")},
        "benign": {"block": cnt(benign, "block"), "uncertain": cnt(benign, "uncertain"),
                   "allow": cnt(benign, "allow"), "error": cnt(benign, "error")},
        "fake": {"block": cnt(fake, "block"), "uncertain": cnt(fake, "uncertain"),
                 "allow": cnt(fake, "allow"), "error": cnt(fake, "error")},
        # recall/latency aggregates
        "phish_brand_hit": sum(1 for r in phish if r["brand"]),
        "benign_brand_hit": sum(1 for r in benign if r["brand"]),
        "avg_total_s": round(sum(r["total"] or 0 for r in results) / len(results), 3) if results else 0,
        "avg_l1_s": 0, "avg_l2_s": 0, "avg_l0_s": 0, "avg_l3_s": 0, "avg_l4_s": 0,
        "n_visual_path": 0, "n_block_screen": 0,
    }
    # per-level averages over samples that ran that level
    for key in ("l0", "l1", "l2", "l3", "l4"):
        vals = [r[key] for r in results if isinstance(r[key], (int, float))]
        metrics[f"avg_{key}_s"] = round(sum(vals) / len(vals), 3) if vals else 0
    metrics["n_block_screen"] = cnt(phish, "block") + cnt(fake, "block")
    metrics["n_visual_path"] = sum(1 for r in results if isinstance(r["l2"], (int, float)))

    with open(out_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # --- quick console summary ---
    print("\n=== METRICS ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"\n总耗时 {time.time()-t_start:.0f}s")


if __name__ == "__main__":
    main()