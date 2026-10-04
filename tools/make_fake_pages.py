"""T8 - build 3 local Chinese fake login pages (fully offline).

Each page: embeds a real brand logo (base64), fake title, password input,
form action pointing to a fake domain, styled like a genuine login page.
Then screenshot with headless Chrome (same throwaway profile approach)
-> testset/fake/fake_alipay.png / fake_wechat.png / fake_icbc.png
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_DIR = ROOT / "testset" / "fake"
FAKE_DIR.mkdir(parents=True, exist_ok=True)

BRANDS = json.load(open(ROOT / "brand_library" / "brands.json", encoding="utf-8"))


def b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def logo_of(brand_id: str):
    for b in BRANDS:
        if b["id"] == brand_id:
            return b
    raise KeyError(brand_id)


def render(fname: str, brand_id: str, fake_domain: str, page_title: str,
           heading: str, service: str, footer_note: str, accent: str) -> Path:
    b = logo_of(brand_id)
    img_b64 = b64(b["logo_path"])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'PingFang SC','Microsoft YaHei',sans-serif; background:#f5f6f8; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ width:420px; background:#fff; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,.08); padding:42px 44px; }}
  .brand {{ text-align:center; margin-bottom:8px; }}
  .brand img {{ width:64px; height:64px; border-radius:14px; }}
  .brand h1 {{ font-size:21px; color:#202124; margin-top:12px; font-weight:600; }}
  .brand p {{ font-size:13px; color:#8a8f99; margin-top:6px; }}
  .tip {{ font-size:12px; color:#6b7280; background:#f3f5f8; border-radius:6px; padding:8px 12px; margin:18px 0; text-align:center; }}
  label {{ display:block; font-size:13px; color:#333; margin:14px 0 6px; }}
  input {{ width:100%; height:44px; border:1px solid #d9dde3; border-radius:8px; padding:0 14px; font-size:14px; outline:none; }}
  input:focus {{ border-color:{accent}; box-shadow:0 0 0 2px {accent}22; }}
  button {{ width:100%; height:46px; border:none; border-radius:8px; background:{accent}; color:#fff; font-size:15px; font-weight:600; margin-top:24px; cursor:pointer; }}
  .sec {{ display:flex; justify-content:space-between; font-size:12px; color:#5f6672; margin-top:12px; }}
  .foot {{ text-align:center; font-size:12px; color:#9aa0aa; margin-top:26px; padding-top:16px; border-top:1px solid #eef0f3; }}
</style>
</head>
<body>
<div class="card">
  <div class="brand">
    <img src="data:image/png;base64,{img_b64}" alt="{service}">
    <h1>{heading}</h1>
    <p>欢迎登录{service}统一安全中心</p>
  </div>
  <div class="tip">系统检测到您的账号存在异常登录风险，请立即验证身份，逾期将冻结账号</div>
  <form action="https://{fake_domain}/login" method="post" autocomplete="off">
    <label for="u">账号</label>
    <input id="u" name="account" type="text" placeholder="请输入手机号/邮箱">
    <label for="p">登录密码</label>
    <input id="p" name="password" type="password" placeholder="请输入登录密码">
    <label for="v">短信验证码</label>
    <div style="display:flex; gap:10px;">
      <input id="v" name="code" type="text" placeholder="6位验证码" style="flex:1;">
      <button type="button" style="width:120px; height:44px; margin-top:0; background:#fff; color:{accent}; border:1px solid {accent}; border-radius:8px; font-size:13px;">获取验证码</button>
    </div>
    <button type="submit">立即验证并登录</button>
  </form>
  <div class="sec"><span>忘记密码</span><span>新用户注册</span></div>
  <div class="foot">{footer_note}</div>
</div>
</body>
</html>
"""
    out = FAKE_DIR / fname
    out.write_text(html, encoding="utf-8")
    return out


def screenshot(url: str, out_png: Path):
    import selenium.webdriver as wd
    from selenium.webdriver.chrome.options import Options

    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    opts = Options()
    opts.binary_location = chrome
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--force-device-scale-factor=1")
    tmp = tempfile.mkdtemp(prefix="fake_profile_")
    opts.add_argument(f"--user-data-dir={tmp}")
    drv = wd.Chrome(options=opts)
    try:
        drv.get(url)
        time.sleep(1.5)
        drv.save_screenshot(str(out_png))
        print(f"  OK {out_png.name} ({os.path.getsize(out_png)}B)")
    finally:
        drv.quit()


def main():
    pages = [
        # (fname_html, brand_id, fake_domain, page_title, heading, service, footer, accent)
        ("fake_alipay.html", "alipay", "alipay-secure-login.top",
         "支付宝 - 安全中心", "安全验证中心", "支付宝", "Copyright © 2026 Alipay Security Center", "#1677ff"),
        ("fake_wechat.html", "wechat", "weixin-verification.icu",
         "微信安全中心 - 登录", "微信账号安全中心", "微信", "Copyright © 2026 WeChat Security", "#07c160"),
        ("fake_icbc.html", "icbc", "icbc-ebank-verify.cc",
         "中国工商银行 - 网上银行", "中国工商银行个人网上银行", "中国工商银行", "Copyright © 2026 ICBC e-Banking", "#c7000b"),
    ]
    htmls = []
    for (h, bid, dom, title, heading, svc, foot, accent) in pages:
        p = render(h, bid, dom, title, heading, svc, foot, accent)
        htmls.append((p, dom))
        print("  wrote", p.name)
    # screenshot via file://
    for (h, bid, dom, title, heading, svc, foot, accent), (p, _) in zip(pages, htmls):
        url = p.as_uri()
        png = FAKE_DIR / (h.replace(".html", ".png"))
        screenshot(url, png)


if __name__ == "__main__":
    main()