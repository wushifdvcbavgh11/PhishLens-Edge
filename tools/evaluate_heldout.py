"""T14 - held-out evaluation with real DOM capture and per-miss attribution.

Data split (tuning vs test):
  - Dev (threshold tuning)   = testset/{phishing,benign}/selected_manifest.tsv
                              (50+50, used to pick MATCH_THRESHOLD=0.68 etc.)
  - Held-out test (this run) = every OK sample NOT selected: 226 phish + 40 benign.
                              None of them ever touched a threshold decision.

Real DOM capture:
  - testset/eval2/live_probe.json was fetched live on 2026-10-08 with HTTP(S).
  - Samples that answered 200 get their REAL DOM signals injected
    (has_password_input, has_login_form, form_action_domains, live title).
  - Unreachable samples are marked dom_source=unreachable and evaluated with
    the minimal honest signal set (page_domain + manifest title only) so the
    credential/L3 path is not silently faked.

Attribution:
  For every phish sample that ended up allow (a miss), record:
    l1_logo_boxes   - logo box count from L1 (0 => logo not found)
    l1_block_fallback - whether top-area block fallback was used
    l2_matched      - whether any brand matched at L2
    l3_cred_signal  - whether DOM credential signal fired
    l4_score        - URL suspicion score
    mission.attribution column: logo_not_found / brand_not_matched /
    logout_matched_brand_but_consistent / cred_not_detected / url_domain_ok

Outputs:
  testset/eval2/results_heldout.tsv
  testset/eval2/metrics_heldout.json
  testset/eval2/attribution_heldout.tsv
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

import app as srv  # noqa: E402


def load_manifest(tsv: Path) -> list[dict]:
    with open(tsv, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def unselected(pool_tsv: Path, selected_tsv: Path, subdir: str) -> list[dict]:
    """All OK samples from pool_tsv that are NOT in selected_tsv, with png path."""
    selected = {r["url"] for r in load_manifest(selected_tsv)}
    out = []
    for r in load_manifest(pool_tsv):
        if r.get("status") != "ok" or r["url"] in selected:
            continue
        if subdir == "phishing":
            png = ROOT / "testset" / subdir / f"phish_{int(r['idx']):03d}.png"
        else:
            png = ROOT / "testset" / subdir / (r["label"] + ".png")
        if png.exists():
            out.append((r, png))
    return out


def build_dom(r, live: dict | None) -> tuple[dict, str]:
    """Return (DomSignals dict, dom_source)."""
    url = r["url"]
    host = url.split("//")[-1].split("/")[0]
    if live is not None and live.get("status") == 200:
        form_domains = []
        for fa in live.get("forms", []):
            if fa.startswith("http"):
                try:
                    from urllib.parse import urlparse
                    form_domains.append(urlparse(fa).hostname or "")
                except Exception:  # noqa: BLE001
                    pass
            elif fa == "":
                form_domains.append(host)
            elif not fa.startswith("/") and "." in fa:
                form_domains.append(fa.split("/")[0])
        return {
            "page_domain": host,
            "has_password_input": bool(live.get("has_pw")),
            "has_login_form": bool(live.get("has_form")),
            "form_action_domains": form_domains,
            "title": live.get("title") or r.get("title", ""),
            "favicon": "",
            "brand_hints": [],
        }, "live"
    return {
        "page_domain": host,
        "has_password_input": False,
        "has_login_form": False,
        "form_action_domains": [],
        "title": r.get("title", ""),
        "favicon": "",
        "brand_hints": [],
    }, "unreachable"


def main():
    probe = {}
    pf = ROOT / "testset" / "eval2" / "live_probe.json"
    if pf.exists():
        probe = {p["url"]: p for p in json.loads(pf.read_text(encoding="utf-8"))}
    out_dir = ROOT / "testset" / "eval2"
    out_dir.mkdir(parents=True, exist_ok=True)

    phish = unselected(ROOT / "testset" / "phishing" / "manifest.tsv",
                       ROOT / "testset" / "phishing" / "selected_manifest.tsv", "phishing")
    benign = unselected(ROOT / "testset" / "benign" / "manifest.tsv",
                        ROOT / "testset" / "benign" / "selected_manifest.tsv", "benign")
    print(f"held-out test set: phish={len(phish)} benign={len(benign)}", flush=True)

    rows: list[tuple[str, dict, Path]] = []
    for r, png in phish:
        rows.append(("phish", r, png))
    for r, png in benign:
        rows.append(("benign", r, png))

    results = []
    t_start = time.time()
    warm_done = False
    for i, (kind, r, png) in enumerate(rows):
        url = r["url"]
        dom, dom_src = build_dom(r, probe.get(url))
        req = srv.AnalyzeRequest(
            url=url,
            screenshot_base64="data:image/png;base64," + base64.b64encode(png.read_bytes()).decode(),
            dom_signals=srv.DomSignals(**dom),
            trigger="eval_heldout",
        )
        if not warm_done:
            try:
                srv.analyze(srv.AnalyzeRequest(
                    url="https://warmup.heldout.invalid/x", screenshot_base64=req.screenshot_base64,
                    dom_signals=srv.DomSignals(page_domain="warmup.heldout.invalid",
                                               has_password_input=False, has_login_form=False,
                                               form_action_domains=[], title="", favicon="", brand_hints=[]),
                    trigger="eval_warmup"))
            except Exception:  # noqa: BLE001
                pass
            warm_done = True
            print(f"  warmup done  ({time.time()-t_start:.0f}s)", flush=True)
        t0 = time.time()
        try:
            res = srv.analyze(req)
        except Exception as exc:  # noqa: BLE001
            res = {"decision": "error", "brand": None, "official_domain": None,
                   "reasons": [f"{type(exc).__name__}: {exc}"], "level_times": {},
                   "total_time_s": 0}
        lt = res.get("level_times", {})
        results.append({
            "set": kind, "url": url, "dom_source": dom_src,
            "has_pw": dom["has_password_input"], "has_form": dom["has_login_form"],
            "decision": res.get("decision"), "brand": res.get("brand"),
            "official": (res.get("official_domain") or "")[:50],
            "l1_logo_boxes": res.get("l1_logo_boxes", ""),
            "l1_block_fallback": res.get("l1_block_fallback", ""),
            "l2_matched": res.get("l2_matched", ""),
            "l0": lt.get("l0_whitelist", lt.get("l0_cache_hit", "")),
            "l1": lt.get("l1_logo_det", ""), "l2": lt.get("l2_logo_match", ""),
            "l3": lt.get("l3_dom", ""), "l4": lt.get("l4_rules", ""),
            "total": round(res.get("total_time_s", time.time() - t0), 3),
            "visual_error": res.get("visual_error") or "",
            "reasons": " | ".join(res.get("reasons", []))[:200],
        })
        if (i + 1) % 50 == 0:
            print(f"  processed {i+1}/{len(rows)} ({time.time()-t_start:.0f}s)", flush=True)

    # ---- write results ----
    fields = ["set", "url", "dom_source", "has_pw", "has_form", "decision", "brand",
              "official", "l1_logo_boxes", "l1_block_fallback", "l2_matched",
              "l0", "l1", "l2", "l3", "l4", "total", "visual_error", "reasons"]
    with open(out_dir / "results_heldout.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    # ---- metrics ----
    def cnt(rs, dec):
        return sum(1 for r in rs if r["decision"] == dec)

    ph = [r for r in results if r["set"] == "phish"]
    be = [r for r in results if r["set"] == "benign"]
    live_ph = [r for r in ph if r["dom_source"] == "live"]
    live_be = [r for r in be if r["dom_source"] == "live"]
    metrics = {
        "n_phish": len(ph), "n_benign": len(be),
        "live_phish": len(live_ph), "live_benign": len(live_be),
        "phish": {"block": cnt(ph, "block"), "uncertain": cnt(ph, "uncertain"),
                  "allow": cnt(ph, "allow"), "error": cnt(ph, "error")},
        "benign": {"block": cnt(be, "block"), "uncertain": cnt(be, "uncertain"),
                   "allow": cnt(be, "allow"), "error": cnt(be, "error")},
        "phish_live": {"block": cnt(live_ph, "block"), "uncertain": cnt(live_ph, "uncertain"),
                       "allow": cnt(live_ph, "allow"), "error": cnt(live_ph, "error")},
        "benign_live": {"block": cnt(live_be, "block"), "uncertain": cnt(live_be, "uncertain"),
                        "allow": cnt(live_be, "allow"), "error": cnt(live_be, "error")},
        "avg_total_s": round(sum(float(r["total"] or 0) for r in results) / len(results), 3),
        "wall_s": round(time.time() - t_start, 1),
    }
    with open(out_dir / "metrics_heldout.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # ---- per-miss attribution ----
    attr = []
    for r in ph:
        if r["decision"] in ("allow", "uncertain"):
            brand = r["brand"] or ""
            tr = dict(r)
            if r["decision"] == "allow":
                if not r["l1_logo_boxes"]:
                    tr["attribution"] = "logo_not_found"
                elif not r["l2_matched"]:
                    tr["attribution"] = "brand_not_matched"
                elif not r["has_pw"] and not r["has_form"]:
                    tr["attribution"] = "cred_not_detected"
                else:
                    tr["attribution"] = "brand_match_but_policy_allow"
            else:
                tr["attribution"] = "uncertain (needs review)"
            attr.append(tr)
    with open(out_dir / "attribution_heldout.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["attribution"], delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in sorted(attr, key=lambda x: (x["attribution"], x["url"])):
            w.writerow(r)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"attribution rows: {len(attr)}  -> {out_dir/'attribution_heldout.tsv'}")


if __name__ == "__main__":
    main()