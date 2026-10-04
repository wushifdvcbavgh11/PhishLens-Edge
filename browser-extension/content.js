// PhishLens Edge Guard - content script (MV3)
// Responsibilities:
//   1. Capture a screenshot after page load + a second one when a password box gains focus
//   2. Collect DOM signals (password input, form action domains, title, favicon, brand hints)
//   3. POST screenshot + URL + DOM signals to the local service (127.0.0.1:8765)
//   4. On "block": overlay a full-screen warning layer

(() => {
  'use strict';

  const SERVER = 'http://127.0.0.1:8765';
  const SKIP_PREFIX = 'phishlens_skip_';
  const BRAND_RE = /(支付宝|微信|淘宝|天猫|京东|拼多多|百度|知乎|微博|QQ|工商银行|建设银行|农业银行|中国银行|招商银行|交通银行|邮储银行|12306|学信网|PayPal|Google|Gmail|Apple|iCloud|Microsoft|Amazon|Facebook|Instagram|LinkedIn|Twitter|Netflix|Steam|Alipay|WeChat)/i;

  let warned = false;

  const isSkipped = () => {
    try { return !!localStorage.getItem(SKIP_PREFIX + location.hostname); } catch (e) { return false; }
  };

  const markSkipped = () => {
    try { localStorage.setItem(SKIP_PREFIX + location.hostname, '1'); } catch (e) { /* ignore */ }
  };

  function collectDomSignals() {
    const host = location.hostname;
    const passwordInput = document.querySelector('input[type="password"]');
    const forms = Array.prototype.slice.call(document.querySelectorAll('form'));
    const actionDomains = [];
    forms.forEach((f) => {
      try {
        const u = new URL(f.action || '', location.href);
        if (u.hostname) actionDomains.push(u.hostname);
      } catch (e) { /* relative/invalid action */ }
    });
    const faviconLink = document.querySelector('link[rel~="icon"]');
    const title = document.title || '';
    const brandHints = [];
    const m = title.match(BRAND_RE);
    if (m) brandHints.push(m[1]);
    document.querySelectorAll('meta[property="og:site_name"]').forEach((el) => {
      const v = (el.getAttribute('content') || '').trim();
      if (v) brandHints.push(v);
    });
    return {
      page_domain: host,
      has_password_input: !!passwordInput,
      has_login_form: forms.length > 0,
      form_action_domains: Array.from(new Set(actionDomains)),
      title: title.slice(0, 200),
      favicon: faviconLink ? faviconLink.href.slice(0, 500) : '',
      brand_hints: Array.from(new Set(brandHints))
    };
  }

  function captureVisibleTab() {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: 'CAPTURE' }, (resp) => {
          if (chrome.runtime.lastError || !resp || !resp.dataUrl) resolve(null);
          else resolve(resp.dataUrl);
        });
      } catch (e) {
        resolve(null);
      }
    });
  }

  async function analyze(trigger) {
    if (warned || isSkipped()) return;
    const screenshotBase64 = await captureVisibleTab();
    if (!screenshotBase64) return; // service worker not ready / no permission
    const payload = {
      url: location.href,
      screenshot_base64: screenshotBase64,
      dom_signals: collectDomSignals(),
      trigger: trigger
    };
    let resp;
    try {
      resp = await fetch(SERVER + '/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      return; // local service offline -> silent
    }
    if (!resp.ok) return;
    let result;
    try { result = await resp.json(); } catch (e) { return; }
    if (result && result.decision === 'block') {
      showWarning(result);
    }
  }

  function showWarning(result) {
    if (warned) return;
    warned = true;

    const brand = result.brand || '未知品牌';
    const official = (result.official_domain || '').split(',').filter(Boolean).join(' / ');
    const current = location.hostname;
    const reasons = (result.reasons || []).join('；') || '页面与所声称品牌不符，且正在向您索取账号密码等敏感信息。';

    const host = document.createElement('div');
    const shadow = host.attachShadow({ mode: 'open' });
    document.documentElement.appendChild(host);

    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        .pleg-overlay {
          position: fixed; inset: 0; z-index: 2147483647;
          background: rgba(120, 8, 8, 0.94);
          color: #fff; font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
          display: flex; align-items: center; justify-content: center;
        }
        .pleg-card {
          background: #fff; color: #111; border-radius: 16px;
          max-width: 520px; width: 90%; padding: 28px 32px;
          box-shadow: 0 24px 64px rgba(0,0,0,.35); text-align: left;
        }
        .pleg-badge {
          display: inline-block; background: #d93025; color: #fff;
          font-size: 13px; font-weight: 700; padding: 4px 12px; border-radius: 999px;
          letter-spacing: 1px; margin-bottom: 14px;
        }
        .pleg-title { font-size: 22px; font-weight: 700; margin: 0 0 12px; }
        .pleg-msg { font-size: 14px; line-height: 1.7; color: #444; margin: 0 0 18px; }
        .pleg-domains { border: 1px solid #eee; border-radius: 10px; padding: 12px 16px; margin-bottom: 20px; background: #fafafa; }
        .pleg-domains div { display: flex; justify-content: space-between; font-size: 14px; padding: 4px 0; }
        .pleg-domains .lbl { color: #888; }
        .pleg-domains .cur { color: #d93025; font-weight: 700; word-break: break-all; text-align: right; padding-left: 16px; }
        .pleg-domains .off { color: #1a7f37; font-weight: 700; word-break: break-all; text-align: right; padding-left: 16px; }
        .pleg-btns { display: flex; gap: 12px; }
        .pleg-btn {
          flex: 1; padding: 12px 0; border-radius: 10px; border: none;
          font-size: 15px; font-weight: 600; cursor: pointer;
        }
        .pleg-back { background: #303030; color: #fff; }
        .pleg-continue { background: #fff; color: #303030; border: 1px solid #ccc; }
        .pleg-priv { margin-top: 14px; font-size: 12px; color: #999; }
      </style>
      <div class="pleg-overlay">
        <div class="pleg-card">
          <span class="pleg-badge">⚠ 疑似钓鱼网站</span>
          <h1 class="pleg-title">这可能不是 ${escapeHtml(brand)} 官方页面</h1>
          <p class="pleg-msg">${escapeHtml(reasons)}</p>
          <div class="pleg-domains">
            <div><span class="lbl">当前域名</span><span class="cur">${escapeHtml(current)}</span></div>
            <div><span class="lbl">正规域名</span><span class="off">${official ? escapeHtml(official) : '未知'}</span></div>
          </div>
          <div class="pleg-btns">
            <button class="pleg-btn pleg-back" id="pleg-back">返回</button>
            <button class="pleg-btn pleg-continue" id="pleg-continue">我确认安全，继续访问</button>
          </div>
          <p class="pleg-priv">截图与分析均在本机完成，不会上传到任何服务器。</p>
        </div>
      </div>`;

    shadow.getElementById('pleg-back').addEventListener('click', () => {
      try { history.back(); } catch (e) { /* no history */ }
      setTimeout(() => { host.remove(); warned = false; }, 3000);
    });
    shadow.getElementById('pleg-continue').addEventListener('click', () => {
      markSkipped();
      host.remove();
      warned = false;
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // --- Trigger 1: page loaded (wait for layout/logo render) ---
  window.addEventListener('load', () => {
    setTimeout(() => analyze('page_load'), 1800);
  });

  // --- Trigger 2: password box gains focus (second screenshot) ---
  document.addEventListener('focusin', (e) => {
    const el = e.target;
    if (el && el.tagName === 'INPUT' && el.type === 'password') {
      setTimeout(() => analyze('password_focus'), 300);
    }
  }, true);

  // Fallback for SPA pages that never fire 'load'
  if (document.readyState === 'complete') {
    setTimeout(() => analyze('page_load'), 1800);
  }
})();