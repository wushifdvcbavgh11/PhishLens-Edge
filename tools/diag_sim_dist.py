"""T9 diagnosis: MobileNet top-1 cosine sim per logo box on selected samples.

Prints, for each sample, every logo box candidate + best brand + sim.
Used to pick a sane MATCH_THRESHOLD for the CN/EN MobileNet fallback route.
"""
import csv, sys, os, time, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

def read_manifest(tsv):
    with open(tsv, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def best_for(shot_path, boxes, threshold=-1.0):
    import cn_brand_matcher as cbm
    cbm.load_index()
    img = __import__("PIL").Image.open(shot_path).convert("RGB")
    best = None
    for i, coord in enumerate(boxes):
        x1, y1, x2, y2 = [float(v) for v in coord]
        crop = img.crop((max(0,int(x1)), max(0,int(y1)), int(x2), int(y2)))
        if crop.size[0] < 8 or crop.size[1] < 8: continue
        arr = __import__("numpy").asarray(crop)
        if arr.std() < 30: continue
        hit = cbm.match_logo_image(crop, threshold=threshold)
        if hit and (best is None or hit["sim"] > best["sim"]):
            best = {**hit, "bbox": [int(x1),int(y1),int(x2),int(y2)]}
    return best

def main():
    import app as srv
    srv.load_models()
    m = srv._MODELS
    targets = sys.argv[1:] or []
    sample_pool = []
    # phish selected
    man = read_manifest(ROOT/"testset"/"phishing"/"selected_manifest.tsv")
    pmap = {}
    with open(ROOT/"testset"/"phishing"/"manifest.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r.get("status") == "ok":
                pmap[r["url"]] = f"phish_{int(r['idx']):03d}.png"
    for r in man:
        png = ROOT/"testset"/"phishing"/pmap.get(r["url"], "")
        if png.exists(): sample_pool.append(("phish", r["url"], png))
    # fake
    for name, url in [("fake_alipay","https://alipay-secure-login.top/login"),
                      ("fake_wechat","https://weixin-verification.icu/login"),
                      ("fake_icbc","https://icbc-ebank-verify.cc/login")]:
        png = ROOT/"testset"/"fake"/f"{name}.png"
        if png.exists(): sample_pool.append(("fake", url, png))
    # benign
    bman = read_manifest(ROOT/"testset"/"benign"/"selected_manifest.tsv")
    bmap = {}
    with open(ROOT/"testset"/"benign"/"manifest.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r.get("status") == "ok":
                bmap[r["url"]] = r["label"] + ".png"
    for r in bman:
        png = ROOT/"testset"/"benign"/bmap.get(r["url"], r["idx"]+".png")
        if png.exists(): sample_pool.append(("benign", r["url"], png))

    out_rows = []
    for set_, url, png in sample_pool:
        if targets and not any(t in url for t in targets): continue
        import base64, tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png.read_bytes()); tmp_path = tmp.name
        try:
            boxes, classes, _ = m["pred_rcnn"](im=tmp_path, predictor=m["awl"])
            logo_boxes, _ = m["find_element_type"](boxes, classes, bbox_type="logo")
            if len(logo_boxes) == 0:
                block_boxes, _ = m["find_element_type"](boxes, classes, bbox_type="block")
                from PIL import Image as PI
                with PI.open(tmp_path) as im: h = im.height
                top_area = [b for b in block_boxes if float(b[1]) < 0.45*h]
                if top_area: logo_boxes = __import__("torch").stack([b for b in top_area])
            best = best_for(tmp_path, logo_boxes) if len(logo_boxes) > 0 else None
            if best:
                print(f"[{set_}] {url[:70]} -> {best['name']} sim={best['sim']:.3f} bbox={best['bbox']}")
                out_rows.append((set_, url, best["name"], round(best["sim"],3)))
        except Exception as e:
            print(f"[{set_}] {url[:70]} ERR {type(e).__name__}: {e}")
        finally:
            try: os.unlink(tmp_path)
            except OSError: pass
    # save
    with open(ROOT/"testset"/"eval"/"sim_dist.tsv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["set","url","brand","sim"])
        w.writerows(out_rows)
    print(f"\nsaved {len(out_rows)} sim rows")

if __name__ == "__main__":
    main()
