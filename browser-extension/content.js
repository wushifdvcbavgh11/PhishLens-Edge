// PhishLens Edge Guard - content script (MV3)
// Responsibilities:
//   1. Capture a screenshot after page load + a second one when a password box gains focus
//   2. Collect DOM signals (password input, form action domains, title, favicon, brand hints)
//      plus a page-content fingerprint (used by the server to key its result cache)
//   3. POST screenshot + URL + DOM signals to the local service (127.0.0.1:8765)
//   4. On "block": overlay a full-screen warning layer
//   5. Show explicit status for "checking" / "uncertain" / "service offline"
//      so the user is never silently left unprotected
//   6. Intercept sensitive form submission until a fresh verdict exists, and re-check
//      when a password field gains focus
//
// Security notes:
//   - Legacy persistent skip/verdict records are deliberately ignored.
//   - Page confirmations require a trusted event and permit one sensitive submit
//     for 30 seconds on the same page. DOM UI is still page-controlled and is NOT
//     a trusted browser surface; an extension-owned confirmation UI is follow-up work.
//   - This guards native sensitive-form submit events, not AJAX, form.submit(),
//     nested frames or arbitrary network traffic. Background capture still needs
//     stronger tab/document identity checks across the capture operation.

(() => {
  'use strict';
  try {
    console.log('[PhishLens] content.js loaded on', location.href);
  } catch (e) { /* some environments silently drop console — ignore */ }

  const SERVER = 'http://127.0.0.1:8765';
  const CLIENT_VERSION = '0.2.4';
  const VERDICT_TTL_MS = 60 * 1000; // a verdict protects at most 60s, then re-check
  const BYPASS_TTL_MS = 30 * 1000;
  const TIMEOUT_MS = 30 * 1000;
  const RETRY_DELAYS_MS = [2000, 5000, 10000];
  const SENSITIVE_SELECTOR = 'input[type="password"], input[autocomplete="current-password"], input[autocomplete="new-password"], input[autocomplete="one-time-code"]';
  const BRAND_RE = /(支付宝|微信|淘宝|天猫|京东|拼多多|百度|知乎|微博|QQ|工商银行|建设银行|农业银行|中国银行|招商银行|交通银行|邮储银行|12306|学信网|PayPal|Google|Gmail|Apple|iCloud|Microsoft|Amazon|Facebook|Instagram|LinkedIn|Twitter|Netflix|Steam|Alipay|WeChat)/i;

  let warned = false;          // warning overlay shown for this page load
  let consentOnce = null;      // {signature, expiresAt}; consumed by one sensitive submit
  let lastDecision = null;     // complete protocol result + {at, signature}, memory only
  let pageState = 'unchecked';
  let unavailableMessage = '';
  let requestSequence = 0;
  let activeRequest = null;
  let pageRevision = 0;
  let knownUrl = location.href;
  let pageObserver = null;
  let warningHost = null;
  let pendingVisibleRetry = false;
  let visibleRetryTimer = null;
  let retryTimer = null;
  let retryPlan = null; // Keep the deadline/budget when a tab is hidden during backoff.
  let pageRetryTimer = null;
  let loadTimer = null;
  let retryCount = 0;
  let pageSuspended = false;
  const ownedNodes = new WeakSet();

  // ---------------------------------------------------------------- DOM signals
  function buildPageFingerprint() {
    let textSample = '';
    try {
      if (document.body) {
        const walker = document.createTreeWalker(document.body, 4); // text nodes; never input values
        let node;
        while (textSample.length < 200 && (node = walker.nextNode())) {
          if (isOwnedNode(node) || (node.parentElement && node.parentElement.closest('script, style, noscript, template'))) continue;
          textSample += ' ' + (node.nodeValue || '').replace(/\s+/g, ' ');
        }
        textSample = textSample.trim().slice(0, 200);
      }
    } catch (e) { /* ignore */ }
    const pwdCount = document.querySelectorAll('input[type="password"]').length;
    return ['dom', document.title || '', String(pwdCount), textSample].join('|');
  }

  function visibleBox(el) {
    if (!el || !el.getBoundingClientRect) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width < 12 || rect.height < 12 || rect.right <= 0 || rect.bottom <= 0
        || rect.left >= window.innerWidth || rect.top >= window.innerHeight) return null;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0)) return null;
    return [rect.left, rect.top, rect.right, rect.bottom].map((n) => Math.round(n * 10) / 10);
  }

  function visualDomContext(forms) {
    if (!window.innerWidth || !window.innerHeight) return {};
    const images = Array.from(document.querySelectorAll('img, svg')).slice(0, 100)
      .filter((el) => !isOwnedNode(el));
    const regions = [];
    for (const el of images) {
      const box = visibleBox(el);
      if (!box || box[2]-box[0] > 600 || box[3]-box[1] > 350) continue;
      const control = el.closest('button, a, [role="button"]');
      const interactive = !!(control && (control.tagName === 'BUTTON'
        || /(登录|支付|授权|sign.?in|log.?in|continue|pay with)/i.test(
          (control.textContent || '') + ' ' + (control.getAttribute('aria-label') || ''))));
      regions.push({ box, interactive });
      if (regions.length === 8) break;
    }
    const contexts = [];
    for (const form of forms.slice(0, 8)) {
      if (!form.querySelector || !form.querySelector('input[type="password"]')) continue;
      const passwordFields = form.querySelectorAll
        ? Array.from(form.querySelectorAll('input[type="password"]'))
        : [form.querySelector('input[type="password"]')];
      if (!passwordFields.some((field) => visibleBox(field))) continue;
      let container = form;
      for (let depth = 0; depth < 3; depth++) {
        if (container.querySelector('img, svg')) break;
        if (!container.parentElement || /^(BODY|HTML)$/.test(container.parentElement.tagName)) break;
        container = container.parentElement;
      }
      const box = visibleBox(container);
      if (!box) continue;
      // Static labels only. Never read input values or serialize the form.
      const text = Array.from(container.querySelectorAll('h1, h2, h3, p, label'))
        .filter((node) => !isOwnedNode(node)).slice(0, 24)
        .map((node) => (node.textContent || '').slice(0, 150)).join('\n').slice(0, 600);
      contexts.push({ box, text });
    }
    return { viewport: { width: window.innerWidth, height: window.innerHeight },
      logo_regions: regions, credential_contexts: contexts };
  }

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
      brand_hints: Array.from(new Set(brandHints)),
      page_fingerprint: buildPageFingerprint(),
      ...visualDomContext(forms)
    };
  }

  // ---------------------------------------------------------------- debug probe
  // Visible-in-page diagnostics for E2E automation & troubleshooting. The probe
  // div is hidden by default; automation reads its data-* attributes.
  let probe = null;
  function ensureProbe() {
    if (probe) return;
    probe = document.createElement('div');
    ownedNodes.add(probe);
    probe.id = '__pleg_probe';
    probe.style.cssText = 'display:none';
    (document.body || document.documentElement).appendChild(probe);
  }
  function setProbe(k, v) {
    try {
      ensureProbe();
      probe.dataset[k] = (typeof v === 'string') ? v : JSON.stringify(v);
    } catch (e) { /* ignore */ }
  }
  setProbe('contentLoaded', '1');
  setProbe('clientVersion', CLIENT_VERSION);

  // ---------------------------------------------------------------- screenshot
  function captureVisibleTab(request) {
    return new Promise((resolve) => {
      try {
        console.log('[PhishLens] requesting CAPTURE from background...');
        chrome.runtime.sendMessage({ type: 'CAPTURE' }, (resp) => {
          if (activeRequest !== request || request.id !== requestSequence || request.controller.signal.aborted) {
            resolve({ error: 'STALE_CAPTURE' });
            return;
          }
          if (chrome.runtime.lastError) {
            console.error('[PhishLens] capture lastError:', chrome.runtime.lastError.message);
            setProbe('captureError', 'lastError:' + chrome.runtime.lastError.message);
            resolve({ error: chrome.runtime.lastError.message });
            return;
          }
          if (!resp) { console.error('[PhishLens] capture no response'); setProbe('captureError', 'noResp'); resolve({ error: 'NO_RESPONSE' }); return; }
          if (resp.dataUrl) {
            console.log('[PhishLens] capture OK, len=', resp.dataUrl.length, 'tabId=', resp.tabId);
            setProbe('captureLen', String(resp.dataUrl.length));
            resolve(resp.dataUrl);
            return;
          }
          // Tab/window mismatch or capture failure -> abort this analysis
          console.error('[PhishLens] capture FAILED:', resp.error || 'CAPTURE_FAILED');
          setProbe('captureError', resp.error || 'CAPTURE_FAILED');
          resolve({ error: resp.error || 'CAPTURE_FAILED' });
        });
      } catch (e) {
        console.error('[PhishLens] capture throw:', e);
        setProbe('captureError', 'throw:' + e.message);
        resolve({ error: e.message || 'CAPTURE_FAILED' });
      }
    });
  }

  // ---------------------------------------------------------------- status UI
  // Small toast in the corner so "checking / uncertain / offline" are never silent.
  let statusHost = null;
  let statusExpanded = false;
  const SHIELD_ICON = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3 4.5 6v5.4c0 4.3 2.8 7.6 7.5 9.6 4.7-2 7.5-5.3 7.5-9.6V6L12 3Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="11" cy="10.5" r="3" stroke="currentColor" stroke-width="1.6"/><path d="m13.3 13 2.4 2.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  function getStatusHost() {
    if (statusHost) return statusHost;
    const host = document.createElement('div');
    ownedNodes.add(host);
    const shadow = host.attachShadow({ mode: 'open' });
    document.documentElement.appendChild(host);
    shadow.innerHTML = `
      <style>
        :host { all: initial; color-scheme: dark; }
        *, *::before, *::after { box-sizing: border-box; }
        .pls-toast {
          --accent: #8db6ff;
          position: fixed; right: 24px; bottom: 24px; z-index: 2147483646;
          display: none; align-items: center; gap: 14px;
          width: 420px; max-width: calc(100vw - 32px); padding: 18px 32px;
          border: 1px solid #33435d; border-radius: 999px;
          background: #101e36; color: #fff; text-align: left;
          font: 12px/1.65 "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif;
          box-shadow: 0 8px 28px #101e3630; animation: pls-enter .2s ease-out;
        }
        .pls-mark { width: 30px; height: 30px; color: var(--accent); flex: none;
          display: flex; align-items: center; justify-content: center; padding: 0;
          border: 0; background: transparent; border-radius: 50%; cursor: pointer; }
        .pls-mark svg { display: block; width: 100%; height: 100%; }
        .pls-orb { display: none; width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }
        .pls-mark:hover { background: #ffffff16; }
        .pls-mark:focus-visible { outline: 2px solid #a9caff; outline-offset: 3px; }
        .pls-body { min-width: 0; flex: 1; }
        .pls-name { font-size: 10px; font-weight: 600; letter-spacing: .4px; color: #a9bdd8; margin-bottom: 2px; }
        .pls-state { display: flex; gap: 8px; align-items: center; margin-bottom: 3px; }
        .pls-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--accent); }
        .pls-checking .pls-dot { animation: pls-blink 1.6s ease-in-out infinite; }
        .pls-uncertain { --accent: #f4c26b; }
        .pls-offline { --accent: #ffaca4; }
        .pls-ok { --accent: #80d6b5; }
        .pls-title { font-size: 14px; font-weight: 600; color: #fff; }
        .pls-msg { margin: 0; color: #c0cde0; overflow-wrap: anywhere;
          max-height: 79px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: #73859f transparent; }
        .pls-btn { margin-top: 8px; border: 1px solid #637793; background: #203650; color: #eef4ff;
          border-radius: 999px; padding: 4px 12px; cursor: pointer; font: inherit;
          font-weight: 600; max-width: 100%; }
        .pls-btn:hover { background: #304b6e; border-color: #a0b7d8; }
        .pls-btn:focus-visible, .pls-msg:focus-visible { outline: 2px solid #a9caff; outline-offset: 2px; }
        @keyframes pls-blink { 50% { opacity: .35; } }
        @keyframes pls-enter { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
        @media (max-width: 480px) { .pls-toast { right: 16px; bottom: 16px; padding: 18px 28px; gap: 10px; } }
        @media (max-height: 350px) { .pls-msg { max-height: 40px; } .pls-toast { bottom: 8px; padding-top: 12px; padding-bottom: 12px; } }
        .pls-toast[data-expanded="false"] { width: 28px; height: 28px; padding: 0; gap: 0;
          border-radius: 50%; justify-content: center; box-shadow: 0 3px 12px #101e3630; }
        .pls-toast[data-expanded="false"] .pls-mark { width: 26px; height: 26px; }
        .pls-toast[data-expanded="false"] .pls-mark svg, .pls-toast[data-expanded="false"] .pls-body { display: none; }
        .pls-toast[data-expanded="false"] .pls-orb { display: block; }
        .pls-checking .pls-orb { animation: pls-blink 1.6s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .pls-toast, .pls-checking .pls-dot { animation: none; } }
        @media (prefers-reduced-motion: reduce) { .pls-checking .pls-orb { animation: none; } }
      </style>
      <div class="pls-toast" id="toast" data-expanded="false" role="region" aria-label="PhishLens 检测状态">
        <button type="button" class="pls-mark" id="pls-toggle" aria-expanded="false" aria-controls="pls-body" aria-label="展开检测详情"><span class="pls-orb" aria-hidden="true"></span>${SHIELD_ICON}</button>
        <div class="pls-body" id="pls-body">
          <div class="pls-name">PhishLens · 本机防护</div>
          <div role="status" aria-live="polite" aria-atomic="true">
            <div class="pls-state"><span class="pls-dot" aria-hidden="true"></span><strong class="pls-title" id="status-title"></strong></div>
            <p class="pls-msg" id="msg" tabindex="0" aria-label="检测详情"></p>
          </div>
          <button type="button" class="pls-btn" id="act" style="display:none"></button>
        </div>
      </div>`;
    statusHost = shadow;
    shadow.getElementById('pls-toggle').addEventListener('click', (event) => {
      if (event.isTrusted) setStatusExpanded(!statusExpanded);
    });
    shadow.getElementById('toast').addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && statusExpanded) {
        event.preventDefault();
        setStatusExpanded(false);
        shadow.getElementById('pls-toggle').focus?.({ preventScroll: true });
      }
    });
    return statusHost;
  }

  function setStatusExpanded(expanded) {
    const s = getStatusHost();
    statusExpanded = expanded;
    s.getElementById('toast').dataset.expanded = String(expanded);
    const toggle = s.getElementById('pls-toggle');
    toggle.ariaExpanded = String(expanded);
    toggle.ariaLabel = 'PhishLens：' + s.getElementById('status-title').textContent
      + (expanded ? '，收起检测详情' : '，展开检测详情');
    toggle.title = toggle.ariaLabel;
  }

  function showStatus(kind, text, actionLabel, onAction) {
    const s = getStatusHost();
    const toast = s.getElementById('toast');
    toast.className = 'pls-toast pls-' + kind;
    s.getElementById('status-title').textContent = {
      checking: '正在检测当前页面', uncertain: '需要进一步核实',
      offline: '检测暂未完成', ok: '未发现明确风险'
    }[kind] || '页面检测状态';
    s.getElementById('msg').textContent = text.replace(/^PhishLens(?:\s+[\d.]+)?[：\s]*/, '');
    const act = s.getElementById('act');
    if (actionLabel && onAction) {
      act.textContent = actionLabel;
      act.style.display = 'inline-block';
      act.onclick = onAction;
    } else {
      act.style.display = 'none';
      act.onclick = null;
    }
    toast.style.display = 'flex';
    // Keep the user's choice across analysis/retry updates. A completed check
    // remains available as a dot instead of disappearing before it is opened.
    setStatusExpanded(statusExpanded);
  }

  function hideStatus() {
    if (!statusHost) return;
    statusHost.getElementById('toast').style.display = 'none';
  }

  // ---------------------------------------------------------------- page identity / analysis
  function removeWarning() {
    if (warningHost) warningHost.remove();
    warningHost = null;
    warned = false;
  }

  function isOwnedNode(node) {
    for (let n = node; n; n = n.parentNode) {
      if (ownedNodes.has(n)) return true;
    }
    return false;
  }

  function hasPageMutation(records) {
    return records.some((record) => {
      if (isOwnedNode(record.target)) return false;
      if (record.type !== 'childList') return true;
      return Array.from(record.addedNodes).concat(Array.from(record.removedNodes))
        .some((node) => !isOwnedNode(node));
    });
  }

  function clearDiagnostics() {
    if (!probe) return;
    try {
      for (const key of ['captureError', 'abortReason', 'fetchError', 'httpStatus', 'captureLen',
        'decision', 'visualStatus', 'analysisStatus', 'reasonCodes', 'requestId', 'lastTrigger',
        'retryAttempt', 'retryInMs']) delete probe.dataset[key];
    } catch (e) { /* diagnostics must never interrupt protection */ }
  }

  function cancelRecovery(resetBudget = false, preserveRetry = false) {
    clearTimeout(retryTimer);
    clearTimeout(pageRetryTimer);
    clearTimeout(visibleRetryTimer);
    retryTimer = pageRetryTimer = visibleRetryTimer = null;
    pendingVisibleRetry = false;
    if (!preserveRetry) retryPlan = null;
    setProbe('retryInMs', '');
    if (resetBudget) retryCount = 0;
  }

  function cancelActiveRequest() {
    requestSequence++;
    const previous = activeRequest;
    activeRequest = null;
    if (previous) previous.controller.abort();
  }

  function retryWhenVisible() {
    if (pageSuspended || document.visibilityState === 'hidden' || !pendingVisibleRetry || visibleRetryTimer) return;
    if (retryPlan) {
      pendingVisibleRetry = false;
      armRetry(retryPlan, 350);
      return;
    }
    showStatus('checking', 'PhishLens：标签已恢复可见，即将继续检测。');
    visibleRetryTimer = setTimeout(() => {
      visibleRetryTimer = null;
      if (!pageSuspended && document.visibilityState !== 'hidden' && pendingVisibleRetry && !activeRequest) {
        analyze('tab_visible');
      }
    }, 350);
  }

  function invalidatePage(reschedule = true) {
    const hadState = pageState !== 'unchecked' || !!consentOnce || !!pageRetryTimer || pendingVisibleRetry;
    pageRevision++;
    cancelActiveRequest();
    cancelRecovery(true);
    lastDecision = null;
    consentOnce = null;
    unavailableMessage = '';
    clearDiagnostics();
    pageState = 'unchecked';
    removeWarning();
    if (!hadState || !reschedule || pageSuspended) return;
    if (document.visibilityState === 'hidden') {
      showUnavailable('PhishLens：页面已变化，请切回此标签，页面可见后将自动继续检测。', { waitVisible: true });
      return;
    }
    showStatus('checking', 'PhishLens：页面已变化，等待页面稳定后自动重新检测。');
    pageRetryTimer = setTimeout(() => {
      pageRetryTimer = null;
      if (!pageSuspended) analyze('page_change');
    }, 750);
  }

  function checkPageChanges(records) {
    const mutations = records || (pageObserver ? pageObserver.takeRecords() : []);
    const urlChanged = knownUrl !== location.href;
    knownUrl = location.href;
    if (urlChanged || hasPageMutation(mutations)) invalidatePage();
  }

  function pageSignature() {
    checkPageChanges();
    return JSON.stringify([location.href, pageRevision, collectDomSignals(),
      Array.from(document.querySelectorAll('form'), (form) => form.action || location.href)]);
  }

  function isCurrent(request) {
    const signature = pageSignature();
    if (request.id !== requestSequence) return false;
    if (request.signature !== signature) {
      invalidatePage();
      return false;
    }
    return true;
  }

  function hasConsent(signature) {
    if (!consentOnce) return false;
    if (consentOnce.signature !== signature || Date.now() >= consentOnce.expiresAt) {
      consentOnce = null;
      return false;
    }
    return true;
  }

  function grantOnce(event, signature) {
    if (!event || !event.isTrusted || pageSignature() !== signature) return;
    cancelRecovery(true);
    consentOnce = { signature, expiresAt: Date.now() + BYPASS_TTL_MS };
    removeWarning();
    showStatus('uncertain', 'PhishLens：请在 30 秒内重新提交，本页仅放行一次；这不代表页面安全。');
  }

  function validResult(result) {
    return result && ['allow', 'block', 'uncertain'].includes(result.decision)
      && ['completed', 'degraded', 'failed'].includes(result.analysis_status)
      && result.analysis_mode === 'browser'
      && typeof result.policy_version === 'string' && result.policy_version.trim().length > 0
      && Array.isArray(result.reason_codes) && result.reason_codes.every((code) => typeof code === 'string')
      && (result.reasons == null || (Array.isArray(result.reasons) && result.reasons.every((reason) => typeof reason === 'string')))
      && (result.brand == null || typeof result.brand === 'string')
      && (result.official_domain == null || typeof result.official_domain === 'string');
  }

  function showUnavailable(message, options = {}) {
    clearTimeout(retryTimer);
    retryTimer = null;
    setProbe('retryInMs', '');
    lastDecision = null;
    pageState = 'unavailable';
    removeWarning();
    if (options.waitVisible) {
      pendingVisibleRetry = true;
      retryWhenVisible();
    } else if (options.retryable && !pageSuspended) {
      if (retryCount < RETRY_DELAYS_MS.length) {
        const delay = Math.max(RETRY_DELAYS_MS[retryCount], options.minDelay || 0);
        retryPlan = { dueAt: Date.now() + delay, revision: pageRevision, attempt: retryCount + 1, message };
        if (document.visibilityState === 'hidden') {
          pendingVisibleRetry = true;
          message += ' 请切回此标签，页面可见后将自动继续检测。';
        } else {
          armRetry(retryPlan);
          return;
        }
      } else {
        message += ' 已停止自动重试，请按提示处理后点击“重新检测”。';
      }
    }
    unavailableMessage = message;
    showStatus('offline', message, '重新检测', (event) => {
      if (event.isTrusted) analyze('retry');
    });
  }

  function armRetry(plan, minDelay = 0) {
    clearTimeout(retryTimer);
    const delay = Math.max(minDelay, plan.dueAt - Date.now(), 0);
    setProbe('retryInMs', String(delay));
    unavailableMessage = plan.message + ' ' + Math.ceil(delay / 1000) + ' 秒后自动重试（'
      + plan.attempt + '/' + RETRY_DELAYS_MS.length + '）。';
    showStatus('offline', unavailableMessage, '重新检测', (event) => {
      if (event.isTrusted) analyze('retry');
    });
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (pageSuspended || retryPlan !== plan || pageRevision !== plan.revision || activeRequest) return;
      if (document.visibilityState === 'hidden') {
        showUnavailable('PhishLens：请切回此标签，页面可见后将自动继续检测。', { waitVisible: true });
        return;
      }
      retryCount = plan.attempt;
      analyze('auto_retry');
    }, delay);
  }

  function incompleteReason(result) {
    const explanations = {
      INVALID_URL: '当前网址不是有效的 HTTP(S) 网页，请打开正常网页后重新检测。',
      PAGE_DOMAIN_MISMATCH: '页面域名与当前网址不一致，请刷新页面后重新检测。',
      BROWSER_CONTEXT_MISSING: '未能取得完整的页面信息，请刷新页面后重新检测。',
      SCREENSHOT_ONLY: '本次只有截图，无法确认网址和登录表单，请使用浏览器扩展重新检测。',
      IMAGE_DECODE_FAILED: '页面截图为空或损坏，需要重新截图。',
      VISUAL_ANALYSIS_FAILED: '视觉模型尚不可用或推理失败；若持续失败，请检查本机服务日志。'
    };
    const code = Object.keys(explanations).find((key) => result.reason_codes.includes(key));
    return {
      message: 'PhishLens：' + (code ? explanations[code]
        : (result.reasons || []).slice(0, 2).join('；') || '服务未能完成检测，请检查本机服务后重新检测。')
        + ' 本页未完成检测，不能据此认定安全。',
      retryable: ['IMAGE_DECODE_FAILED', 'VISUAL_ANALYSIS_FAILED'].includes(code),
      minDelay: code === 'VISUAL_ANALYSIS_FAILED' ? 30000 : 0
    };
  }

  async function analyze(trigger) {
    if (pageSuspended) return null;
    let signature;
    try {
      signature = pageSignature();
    } catch (error) {
      cancelActiveRequest();
      cancelRecovery(true);
      clearDiagnostics();
      showUnavailable('PhishLens：无法读取当前页面信息，本页未完成检测。请刷新页面后重新检测。');
      return null;
    }
    if (hasConsent(signature)) return null;
    // A delayed load event must not restart a request, backoff or completed check.
    if (trigger === 'page_load' && (activeRequest || retryTimer || pageRetryTimer
      || pendingVisibleRetry || (lastDecision && Date.now() - lastDecision.at < VERDICT_TTL_MS)
      || pageState === 'unavailable')) return null;
    cancelRecovery(!['auto_retry', 'tab_visible'].includes(trigger));
    cancelActiveRequest();
    unavailableMessage = '';
    clearDiagnostics();
    if (document.visibilityState === 'hidden') {
      showUnavailable('PhishLens：请切回此标签，页面可见后将自动继续检测。', { waitVisible: true });
      return null;
    }
    const request = { id: ++requestSequence, signature, controller: new AbortController(), timedOut: false };
    activeRequest = request;
    lastDecision = null; // a failing re-check must never leave an earlier allow in force
    pageState = 'checking';
    removeWarning();
    setProbe('requestId', String(request.id));
    setProbe('lastTrigger', trigger);
    setProbe('retryAttempt', String(retryCount));
    showStatus('checking', 'PhishLens ' + CLIENT_VERSION + '：正在获取页面截图（最多等待 30 秒）…');
    const aborted = new Promise((resolve, reject) => {
      request.controller.signal.addEventListener('abort', () => reject(new Error('ANALYSIS_ABORTED')), { once: true });
    });
    const timer = setTimeout(() => {
      request.timedOut = true;
      request.controller.abort();
    }, TIMEOUT_MS);

    let stage = 'capture';
    try {
      const shot = await Promise.race([captureVisibleTab(request), aborted]);
      if (!isCurrent(request)) return null;
      if (!shot || shot.error) {
        setProbe('abortReason', shot ? shot.error : 'shotNull');
        const code = shot ? shot.error : 'EMPTY_CAPTURE';
        if (/extension context invalidated/i.test(code)) {
          showUnavailable('PhishLens：扩展已更新或重新加载，请刷新此页面以恢复检测。');
        } else if (['NO_TAB', 'TAB_GONE', 'UNSUPPORTED_FRAME'].includes(code)
          || /cannot access|permission|not allowed/i.test(code)) {
          showUnavailable('PhishLens：浏览器不允许截取此页面，请确认扩展权限并在普通网页重新检测。');
        } else if (code === 'TAB_NOT_ACTIVE' && document.visibilityState === 'hidden') {
          showUnavailable('PhishLens：请切回此标签，页面可见后将自动继续检测。', { waitVisible: true });
        } else {
          const message = code === 'TAB_NOT_ACTIVE' ? '标签切换尚未完成，暂时无法获取截图。'
            : code === 'PAGE_CHANGED' ? '截图时页面或标签发生变化，需要重新获取截图。'
              : '暂时无法获取页面截图，请保持此标签可见。';
          showUnavailable('PhishLens：' + message + ' 本页未完成检测。', { retryable: true });
        }
        return null;
      }
      const payload = { url: location.href, screenshot_base64: shot, dom_signals: collectDomSignals(), trigger, analysis_mode: 'browser' };
      showStatus('checking', 'PhishLens ' + CLIENT_VERSION + '：正在等待本机分析结果（总计最多 30 秒）…');
      stage = 'fetch';
      const resp = await Promise.race([fetch(SERVER + '/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), signal: request.controller.signal
      }), aborted]);
      if (!isCurrent(request)) return null;
      setProbe('httpStatus', String(resp.status));
      if (!resp.ok) {
        const retryable = [408, 429].includes(resp.status) || resp.status >= 500;
        const reason = resp.status === 429 ? '本机服务请求过多'
          : resp.status >= 500 ? '本机服务暂时异常'
            : resp.status === 408 ? '本机服务请求超时' : '本机服务拒绝了请求，请检查服务版本及配置';
        showUnavailable('PhishLens：' + reason + '（HTTP ' + resp.status + '），本页未完成检测。', { retryable });
        return null;
      }
      stage = 'json';
      const result = await Promise.race([resp.json(), aborted]);
      if (!isCurrent(request)) return null;
      if (!validResult(result)) {
        showUnavailable('PhishLens 服务协议不兼容或返回异常，本页未完成检测。请更新本机服务。');
        return null;
      }
      if (result.analysis_status !== 'completed') {
        setProbe('analysisStatus', result.analysis_status);
        setProbe('reasonCodes', result.reason_codes);
        const failure = incompleteReason(result);
        showUnavailable(failure.message, failure);
        return null;
      }
      cancelRecovery(true);
      unavailableMessage = '';
      setProbe('analysisStatus', result.analysis_status);
      setProbe('reasonCodes', result.reason_codes);
      lastDecision = Object.assign({}, result, { at: Date.now(), signature });
      setProbe('decision', result.decision);
      setProbe('visualStatus', result.visual_evidence ? result.visual_evidence.status : '');
      pageState = result.decision;
      if (result.decision === 'block') {
        hideStatus();
        showWarning(result);
      } else if (result.decision === 'uncertain') {
        const brand = result.brand ? result.brand + '，' : '';
        const top = (result.reasons || []).slice(0, 2).join('；');
        const detail = {
          NO_LOGO_REGION: '当前画面没有可用 Logo，请回到品牌标志所在位置重新检测。',
          AMBIGUOUS_BRANDS: '多个品牌外观相近，暂时无法区分，请核对网址。',
          MULTIPLE_BRANDS: '检测到多个品牌，尚不能确认登录表单所属品牌。',
          CREDENTIAL_BRAND_UNLINKED: '品牌与网址不符，但尚不能确认密码框属于该品牌，请核对登录对象。',
          BRAND_UNCONFIRMED: '视觉证据不足，暂时无法确认品牌，请核对网址。'
        };
        const code = Object.keys(detail).find((key) => result.reason_codes.includes(key));
        showStatus('uncertain', code ? 'PhishLens：' + detail[code]
          : 'PhishLens 发现疑点：' + brand + (top || '建议进一步确认后再输入账号密码。'),
          '重新检测', (event) => { if (event.isTrusted) analyze('retry'); });
      } else {
        showStatus('ok', 'PhishLens：本页未发现明确风险。');
      }
      return result;
    } catch (error) {
      // A broken page-signature read must not leave the UI in "checking".
      if (activeRequest === request && request.id === requestSequence) {
        setProbe('fetchError', error.message);
        if (request.timedOut) {
          showUnavailable('PhishLens：' + (stage === 'capture' ? '获取截图' : '等待本机分析结果')
            + '超时（30 秒），本页未完成检测。首次加载模型可能较慢，请确认本机服务正在运行。', { retryable: true });
        } else if (stage === 'json') {
          showUnavailable('PhishLens：本机服务返回的结果格式无法解析，本页未完成检测。请更新或重启本机服务后重新检测。');
        } else {
          showUnavailable('PhishLens：无法连接本机分析服务，本页未完成检测。请先运行“启动项目.bat”。', { retryable: true });
        }
      }
      return null;
    } finally {
      clearTimeout(timer);
      if (activeRequest === request) activeRequest = null;
    }
  }

  // ---------------------------------------------------------------- warning overlay
  function showWarning(result) {
    if (warned) return;
    warned = true;

    const brand = result.brand || '未知品牌';
    const official = (result.official_domain || '').split(',').filter(Boolean).join(' / ');
    const current = location.hostname;
    const reasons = (result.reasons || []).join('；') || '页面与所声称品牌不符，且正在向您索取账号密码等敏感信息。';

    const host = document.createElement('div');
    ownedNodes.add(host);
    warningHost = host;
    const signature = pageSignature();
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
        .pleg-overlay { overflow: auto; padding: 16px; box-sizing: border-box; }
        .pleg-card { flex: none; margin: auto; box-sizing: border-box; width: 100%; max-width: 584px; }
        .pleg-title, .pleg-msg, .pleg-priv { overflow-wrap: anywhere; }
        .pleg-domains .lbl { flex: none; }
        .pleg-domains .cur, .pleg-domains .off { min-width: 0; }
        .pleg-btn:focus-visible { outline: 3px solid #98bfff; outline-offset: 3px; }
        @media (max-width: 480px) { .pleg-card { padding: 22px; } .pleg-btns { flex-direction: column; } }
      </style>
      <div class="pleg-overlay">
        <div class="pleg-card" role="alertdialog" aria-modal="true" aria-labelledby="pleg-title" aria-describedby="pleg-description">
          <span class="pleg-badge">⚠ 疑似钓鱼网站</span>
          <h1 class="pleg-title" id="pleg-title">这可能不是 ${escapeHtml(brand)} 官方页面</h1>
          <p class="pleg-msg" id="pleg-description">${escapeHtml(reasons)}</p>
          <div class="pleg-domains">
            <div><span class="lbl">当前域名</span><span class="cur">${escapeHtml(current)}</span></div>
            <div><span class="lbl">正规域名</span><span class="off">${official ? escapeHtml(official) : '未知'}</span></div>
          </div>
          <div class="pleg-btns">
            <button class="pleg-btn pleg-back" id="pleg-back">返回</button>
            <button class="pleg-btn pleg-continue" id="pleg-continue">了解风险，本页放行一次</button>
          </div>
          <p class="pleg-priv">截图发送至本机服务。仅放行接下来一次敏感表单提交，30 秒后失效。页面内提示无法防止网页脚本自行发送数据。</p>
        </div>
      </div>`;

    shadow.getElementById('pleg-back').addEventListener('click', (event) => {
      if (!event.isTrusted) return;
      try { history.back(); } catch (e) { /* no history */ }
    });
    shadow.getElementById('pleg-continue').addEventListener('click', (event) => grantOnce(event, signature));
    // Keep keyboard navigation inside the visible warning without bypassing it.
    const back = shadow.getElementById('pleg-back');
    const proceed = shadow.getElementById('pleg-continue');
    back.focus?.({ preventScroll: true });
    shadow.addEventListener?.('keydown', (event) => {
      if (event.key !== 'Tab') return;
      if (event.shiftKey && shadow.activeElement === back) {
        event.preventDefault(); proceed.focus();
      } else if (!event.shiftKey && shadow.activeElement === proceed) {
        event.preventDefault(); back.focus();
      }
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ---------------------------------------------------------------- submission guard
  // Native sensitive forms only. This is not a network exfiltration boundary.
  function isSensitiveForm(form) {
    if (!form || form.tagName !== 'FORM') return false;
    return Array.from(form.elements || []).some((control) => control.matches && control.matches(SENSITIVE_SELECTOR));
  }

  document.addEventListener('submit', (event) => {
    if (!isSensitiveForm(event.target)) return;
    const signature = pageSignature();
    if (hasConsent(signature)) {
      consentOnce = null;
      return;
    }
    const fresh = lastDecision && lastDecision.signature === signature
      && lastDecision.analysis_status === 'completed' && Date.now() - lastDecision.at < VERDICT_TTL_MS;
    if (fresh && lastDecision.decision === 'allow') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (fresh && lastDecision.decision === 'block') {
      showWarning(lastDecision);
      return;
    }
    if (fresh && lastDecision.decision === 'uncertain') {
      const brand = lastDecision.brand ? lastDecision.brand + '，' : '';
      const top = (lastDecision.reasons || []).slice(0, 2).join('；');
      showStatus(
        'uncertain',
        'PhishLens 判定本页存在疑点（' + brand + (top || '未完全确认安全') + '）。输入账号密码前请谨慎核对网址。',
        '本页放行一次',
        (click) => grantOnce(click, signature)
      );
      return;
    }
    if (pageState === 'unavailable') {
      showStatus('offline', unavailableMessage + ' 若确认继续，请再次提交；放行仅限本页一次、30 秒有效。',
        '了解风险，放行一次', (click) => grantOnce(click, signature));
      // Refocusing the password field also retries a recovered local service.
      return;
    }
    showStatus('checking', 'PhishLens 正在重新检测本页，请稍候再提交。');
    if (!activeRequest) analyze('before_submit');
  }, true);

  // ---------------------------------------------------------------- triggers
  // Trigger 1: page loaded (wait for layout/logo render)
  function onLoadAnalyze() {
    clearTimeout(loadTimer);
    loadTimer = setTimeout(() => {
      loadTimer = null;
      analyze('page_load');
    }, 1800);
  }
  window.addEventListener('load', onLoadAnalyze);
  document.addEventListener('visibilitychange', () => {
    if (pageSuspended) return;
    if (document.visibilityState === 'hidden') {
      if (activeRequest || retryTimer || pageRetryTimer || pendingVisibleRetry) {
        cancelActiveRequest();
        cancelRecovery(false, true);
        clearDiagnostics();
        showUnavailable('PhishLens：检测已暂停，请切回此标签，页面可见后将自动继续检测。', { waitVisible: true });
      }
      return;
    }
    retryWhenVisible();
  });

  // Trigger 2: password box gains focus -> re-check right before typing
  document.addEventListener('focusin', (e) => {
    const el = e.target;
    if (el && el.tagName === 'INPUT' && el.type === 'password') {
      const signature = pageSignature();
      // Repeated focus events must not reset the 30-second deadline.
      if (activeRequest && activeRequest.signature === signature) return;
      if (!hasConsent(signature)) analyze('password_focus');
    }
  }, true);

  // Conservative invalidation includes logo src, CSS and form targets. Dynamic
  // clocks/animations may invalidate frequently; compatibility needs real-browser
  // testing before narrowing this policy. Our own UI nodes are excluded above.
  pageObserver = new MutationObserver((records) => checkPageChanges(records));
  pageObserver.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  window.addEventListener('popstate', () => checkPageChanges());
  window.addEventListener('hashchange', () => checkPageChanges());
  window.addEventListener('pagehide', () => {
    pageSuspended = true;
    clearTimeout(loadTimer);
    loadTimer = null;
    invalidatePage(false);
    hideStatus();
  });
  window.addEventListener('pageshow', (event) => {
    pageSuspended = false;
    if (event.persisted) onLoadAnalyze();
  });
  // pushState/replaceState in the page's isolated JS world have no standard event.
  // Results and submits synchronously check the URL too; this poll updates idle UI.
  setInterval(() => { if (!pageSuspended) checkPageChanges(); }, 500);

  // Fallback for a late-injected script.
  if (document.readyState === 'complete') {
    onLoadAnalyze();
  }
})();
try { console.log('[PhishLens] content.js EOF reached'); } catch (e) {}
