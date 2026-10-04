"""T8 - capture benign/login-page screenshots for the positive (non-phish) class.

Same Chrome headless throwaway profile as the phishing capture, but navigates
to a curated list of legitimate login pages / portals. Output to testset/benign/.
"""
from __future__ import annotations

import csv
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "testset" / "benign"
MANIFEST = OUT_DIR / "manifest.tsv"

# (label, url) - login pages & portals (mix of brand login & neutral sites)
SITES = [
    ("zhihu", "https://www.zhihu.com/signin"),
    ("baidu", "https://www.baidu.com/"),
    ("bilibili", "https://www.bilibili.com/"),
    ("weibo", "https://weibo.com/login.php"),
    ("douban", "https://www.douban.com/"),
    ("qq", "https://mail.qq.com/"),
    ("163mail", "https://mail.163.com/"),
    ("alipay", "https://auth.alipay.com/login/index.htm"),
    ("taobao", "https://login.taobao.com/member/login.jhtml"),
    ("jd", "https://passport.jd.com/new/login.aspx"),
    ("pinduoduo", "https://mobile.yangkeduo.com/login.html"),
    ("wechat", "https://wx.qq.com/"),
    ("12306", "https://kyfw.12306.cn/otn/resources/login.html"),
    ("chsi", "https://my.chsi.com.cn/archive/j_common/login.jsp"),
    ("icbc", "https://mybank.icbc.com.cn/"),
    ("ccb", "https://ibsnew.ccb.com.cn/"),
    ("boc", "https://ebsnew.boc.cn/"),
    ("cmb", "https://pbsz.ebank.cmbchina.com/CmbBank_GenShell/UI/GenShellPC/Login/Login.aspx"),
    ("bankcomm", "https://online.bankcomm.com/"),
    ("abchina", "https://www.abchina.com/cn/"),
    ("dingtalk", "https://login.dingtalk.com/"),
    ("feishu", "https://www.feishu.cn/"),
    ("microsoft", "https://login.microsoftonline.com/"),
    ("google", "https://accounts.google.com/"),
    ("apple", "https://appleid.apple.com/"),
    ("github", "https://github.com/login"),
    ("gov", "https://www.gov.cn/"),
    ("gitlab", "https://gitlab.com/users/sign_in"),
    ("sina", "https://mail.sina.com.cn/"),
    ("tianya", "https://www.tianya.cn/"),
    ("csdn", "https://passport.csdn.net/login"),
    ("163", "https://www.163.com/"),
    ("sohu", "https://www.sohu.com/"),
    ("sina_news", "https://news.sina.com.cn/"),
    ("zhongguancun", "https://www.zol.com.cn/"),
    ("qidian", "https://www.qidian.com/"),
    ("xiaohongshu", "https://www.xiaohongshu.com/"),
    ("douyin", "https://www.douyin.com/"),
    ("kuaishou", "https://www.kuaishou.com/"),
    ("iqiyi", "https://www.iqiyi.com/"),
    ("youku", "https://www.youku.com/"),
    ("eleme", "https://www.ele.me/"),
    ("meituan", "https://www.meituan.com/"),
    ("dianping", "https://www.dianping.com/"),
    ("ctrip", "https://www.ctrip.com/"),
    ("fliggy", "https://www.fliggy.com/"),
    ("zhongguojiaoyuzai", "https://www.hep.com.cn/"),
    ("sdu", "https://www.sdu.edu.cn/"),
    ("tsinghua", "https://www.tsinghua.edu.cn/"),
    ("pku", "https://www.pku.edu.cn/"),
    # ---- round 2 ----
    ("zhihu2", "https://www.zhihu.com/"),
    ("baidu2", "https://passport.baidu.com/v2/?login"),
    ("bilibili2", "https://passport.bilibili.com/login"),
    ("weibo2", "https://passport.weibo.com/sso/signin"),
    ("netease_mail2", "https://reg.163.com/"),
    ("microsoft2", "https://www.microsoft.com/"),
    ("apple2", "https://www.apple.com/"),
    ("google2", "https://mail.google.com/"),
    ("qq2", "https://i.qq.com/"),
    ("jd2", "https://www.jd.com/"),
    ("taobao2", "https://www.taobao.com/"),
    ("alipay2", "https://www.alipay.com/"),
    ("unionpay", "https://www.unionpay.com/"),
    ("psbc", "https://www.psbc.com/cn/"),
    ("citicbank", "https://www.citicbank.com/"),
    ("cib", "https://www.cib.com.cn/"),
    ("pingan", "https://www.pingan.com/"),
    ("evergrande", "https://www.evergrande.com/"),
    ("qqmail2", "https://mail.qq.com/cgi-bin/login"),
    ("outlook", "https://outlook.live.com/"),
    ("yandex", "https://passport.yandex.com/"),
    ("mojang", "https://www.minecraft.net/"),
    ("steam", "https://store.steampowered.com/"),
    ("epic", "https://www.epicgames.com/"),
    ("spotify", "https://accounts.spotify.com/"),
    ("netflix", "https://www.netflix.com/"),
    ("tiktok", "https://www.tiktok.com/"),
    ("linkedin", "https://www.linkedin.com/login"),
    ("twitter", "https://twitter.com/login"),
    ("facebook", "https://www.facebook.com/login"),
    ("amazon", "https://www.amazon.com/"),
    ("ebay", "https://www.ebay.com/"),
    ("etsy", "https://www.etsy.com/"),
    ("paypal", "https://www.paypal.com/signin"),
    ("stripe", "https://dashboard.stripe.com/"),
    ("square", "https://squareup.com/"),
    ("shopify", "https://www.shopify.com/"),
    ("cloudflare", "https://dash.cloudflare.com/login"),
    ("notion", "https://www.notion.so/login"),
    ("figma", "https://www.figma.com/login"),
    ("slack", "https://slack.com/signin"),
    ("zoom", "https://zoom.us/signin"),
    ("teams", "https://teams.microsoft.com/"),
    ("dropbox", "https://www.dropbox.com/login"),
    ("onedrive", "https://onedrive.live.com/"),
    ("canva", "https://www.canva.com/"),
    ("adobe", "https://account.adobe.com/"),
    ("oracle", "https://www.oracle.com/"),
    ("sap", "https://www.sap.com/"),
    ("salesforce", "https://login.salesforce.com/"),
    ("hubspot", "https://app.hubspot.com/"),
    ("zendesk", "https://www.zendesk.com/"),
    ("atlassian", "https://id.atlassian.com/"),
    ("okta", "https://www.okta.com/"),
    ("duo", "https://duo.com/"),
    ("auth0", "https://auth0.com/"),
    ("1password", "https://my.1password.com/"),
    ("lastpass", "https://www.lastpass.com/"),
    ("dashlane", "https://www.dashlane.com/"),
    ("bitwarden", "https://vault.bitwarden.com/"),
    ("kraken", "https://www.kraken.com/"),
    ("coinbase", "https://www.coinbase.com/"),
    ("binance", "https://www.binance.com/"),
    ("cryptocom", "https://crypto.com/"),
    ("metamask", "https://metamask.io/"),
    ("electrum", "https://electrum.org/"),
    ("multisig", "https://multisig.co/"),
    ("ledger", "https://www.ledger.com/"),
    ("trezor", "https://trezor.io/"),
    # ---- round 3 ----
    ("bing", "https://www.bing.com/"),
    ("duckduckgo", "https://duckduckgo.com/"),
    ("wikipedia", "https://www.wikipedia.org/"),
    ("reddit", "https://www.reddit.com/"),
    ("quora", "https://www.quora.com/"),
    ("medium", "https://medium.com/"),
    ("instagram", "https://www.instagram.com/"),
    ("telegram", "https://web.telegram.org/"),
    ("discord", "https://discord.com/"),
    ("twitch", "https://www.twitch.tv/"),
    ("hsbc", "https://www.hsbc.com.hk/"),
    ("wellsfargo", "https://www.wellsfargo.com/"),
    ("bankofamerica", "https://www.bankofamerica.com/"),
    ("mi", "https://www.mi.com/"),
    ("oppo", "https://www.oppo.com/"),
    ("vivo", "https://www.vivo.com.cn/"),
    ("bytedance", "https://www.bytedance.com/"),
    ("toutiao", "https://www.toutiao.com/"),
    ("juejin", "https://juejin.cn/"),
    ("gitee", "https://gitee.com/"),
    ("oschina", "https://www.oschina.net/"),
    ("fudan", "https://www.fudan.edu.cn/"),
    ("sjtu", "https://www.sjtu.edu.cn/"),
    ("zju", "https://www.zju.edu.cn/"),
    ("whu", "https://www.whu.edu.cn/"),
    ("syst", "https://www.sysu.edu.cn/"),
    ("hust", "https://www.hust.edu.cn/"),
]

NAV_TIMEOUT_S = 20


def main():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    for exe in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(exe):
            opts.binary_location = exe
            break
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument("--user-data-dir=" + tempfile.mkdtemp(prefix="phishlens_benign_"))
    _driver = webdriver.Chrome(options=opts)
    _driver.set_page_load_timeout(NAV_TIMEOUT_S)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(MANIFEST, "a", encoding="utf-8", newline="")
    writer = csv.writer(fh, delimiter="\t")
    if MANIFEST.stat().st_size == 0 or not MANIFEST.exists():
        writer.writerow(["label", "url", "status", "ts", "bytes", "title"])

    for i, (label, url) in enumerate(SITES):
        if i > 0 and i % 12 == 0:
            # rebuild the browser process every 12 sites to avoid stale-state crashes
            try:
                _driver.quit()
            except Exception:  # noqa: BLE001
                pass
            _driver = webdriver.Chrome(options=opts)
            _driver.set_page_load_timeout(NAV_TIMEOUT_S)
        path = OUT_DIR / f"{label}.png"
        if path.exists():
            print(f"[skip] {label}")
            continue
        try:
            _driver.get(url)
            time.sleep(3.0)
            _driver.save_screenshot(str(path))
            size = path.stat().st_size
            title = (_driver.title or "")[:80]
            writer.writerow([label, url, "ok", time.strftime("%Y-%m-%d %H:%M:%S"), size, title])
            fh.flush()
            print(f"[OK]   {label:14s} {size}B title={title!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            writer.writerow([label, url, f"err:{type(exc).__name__}", time.strftime("%Y-%m-%d %H:%M:%S"), 0, ""])
            fh.flush()
            print(f"[ERR]  {label:14s} {type(exc).__name__}", flush=True)
    fh.close()
    try:
        _driver.quit()
    except Exception:  # noqa: BLE001
        pass
    print("benign capture done")


if __name__ == "__main__":
    main()