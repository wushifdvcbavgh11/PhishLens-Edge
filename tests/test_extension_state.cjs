// Deterministic content-script regressions. No browser, service or dependencies.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../browser-extension/content.js'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../browser-extension/manifest.json'), 'utf8'));
const instrumented = source.replace('\n})();', `
  globalThis.testApi = { analyze, pageSignature, onLoadAnalyze, visualDomContext,
    state: () => ({ lastDecision, pageState, consentOnce, warned, activeRequest }) };
})();`);
assert.notEqual(instrumented, source, 'test instrumentation must be inserted into the closure');

function verdict(decision = 'allow', extra = {}) {
  return { decision, analysis_status: 'completed', analysis_mode: 'browser',
    policy_version: 'test-v1', reason_codes: ['TEST'], reasons: ['Test reason'], brand: 'Test brand', ...extra };
}
function response(result) { return { ok: true, status: 200, json: async () => result }; }
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

function harness(options = {}) {
  const transports = new Map();
  let now = 100000, nextTimer = 0, observer;
  const timers = new Map(), events = {}, windowEvents = {}, requests = [], writes = [], captures = [];
  const legacyStorage = options.storage || {};
  function queue(record) { if (observer?.observing) observer.records.push(record); }
  function element(tag = 'div') {
    return {
      tagName: tag.toUpperCase(), children: [], style: {}, dataset: {}, listeners: {},
      parentNode: null, textContent: '',
      appendChild(node) {
        node.parentNode = this; node.parentElement = this; this.children.push(node);
        queue({ type: 'childList', target: this, addedNodes: [node], removedNodes: [] });
        return node;
      },
      remove() {
        if (!this.parentNode) return;
        const parent = this.parentNode;
        parent.children = parent.children.filter((node) => node !== this);
        this.parentNode = null;
        queue({ type: 'childList', target: parent, addedNodes: [], removedNodes: [this] });
      },
      closest() { return null; },
      addEventListener(type, listener) { this.listeners[type] = listener; },
      click(isTrusted = false) { (this.listeners.click || this.onclick)?.({ isTrusted }); },
      attachShadow() {
        const nodes = {};
        this.shadowRoot = {
          nodes,
          set innerHTML(html) {
            for (const match of html.matchAll(/id="([^"]+)"/g)) nodes[match[1]] = element();
          },
          getElementById(id) { return nodes[id]; }
        };
        return this.shadowRoot;
      }
    };
  }
  const body = element('body'), root = element('html');
  root.appendChild(body);
  const text = body.appendChild({ nodeType: 3, nodeValue: 'Login page' });
  const password = { tagName: 'INPUT', type: 'password', matches: () => true };
  const sensitiveForm = { tagName: 'FORM', action: 'https://example.test/login', elements: [password] };
  const ordinaryForm = { tagName: 'FORM', action: 'https://example.test/search', elements: [] };
  const forms = options.sensitive === false ? [ordinaryForm] : [sensitiveForm, ordinaryForm];
  const controls = options.sensitive === false ? [] : [password];
  const document = {
    body, documentElement: root, title: 'Login', readyState: 'loading', visibilityState: 'visible',
    contentType: options.mime === undefined ? 'text/html' : options.mime,
    createElement: element,
    createTreeWalker(node) {
      const texts = [];
      function walk(n) { if (n.nodeType === 3) texts.push(n); for (const child of n.children || []) walk(child); }
      walk(node); let at = 0;
      return { nextNode: () => texts[at++] || null };
    },
    querySelectorAll(selector) {
      if (selector === 'form') return forms;
      if (selector.startsWith('input')) return controls;
      return [];
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    addEventListener(type, listener) { events[type] = listener; }
  };
  class MutationObserver {
    constructor(callback) { this.callback = callback; this.records = []; observer = this; }
    observe() { this.observing = true; }
    takeRecords() { const records = this.records; this.records = []; return records; }
  }
  const context = {
    document, MutationObserver, AbortController, URL,
    Date: class extends Date { static now() { return now; } },
    location: { hostname: new URL(options.url || 'https://example.test/login').hostname,
      href: options.url || 'https://example.test/login' },
    window: { addEventListener(type, listener) { windowEvents[type] = listener; } },
    history: { back() {} }, console: { log() {}, error() {} },
    setTimeout(fn, delay) { timers.set(++nextTimer, { fn, at: now + delay, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, delay) { timers.set(++nextTimer, { fn, at: now + delay, delay, interval: true }); return nextTimer; },
    chrome: {
      storage: { local: { get(keys, callback) { callback(legacyStorage); }, set(value, callback) { writes.push(value); callback(); } } },
      runtime: { getManifest: () => manifest, sendMessage(message, callback) {
        if (options.sendMessageError?.(message)) throw new Error('Extension context invalidated.');
        if (message.type === 'CANCEL_ANALYZE') {
          transports.get(message.requestId)?.abort(); callback({cancelled:true}); return;
        }
        if (message.type === 'MODEL_STATUS') {
          if (options.modelStatus) return options.modelStatus(callback);
          callback({ok:true,status:200,body:JSON.stringify({models_ready:true,model_initialization:{state:'ready'}})});
          return;
        }
        if (message.type === 'ANALYZE') {
          const controller = new AbortController(); transports.set(message.requestId, controller);
          context.fetch('http://127.0.0.1:8765/analyze', {method:'POST',body:JSON.stringify(message.payload),signal:controller.signal})
            .then(async r => {
              let body = '';
              if (r.ok) { try { body = JSON.stringify(await r.json()); } catch (_) { body = '{'; } }
              callback({ok:r.ok,status:r.status,body});
            }, error => callback({error:error.message}));
          return;
        }
        captures.push(message);
        if (options.capture) return options.capture(callback);
        callback({ dataUrl: 'data:image/png;base64,TEST', tabId: 1 });
      } }
    },
    fetch(url, init) {
      requests.push({ url, ...init });
      return options.fetch ? options.fetch(url, init) : Promise.resolve(response(verdict()));
    }
  };
  vm.createContext(context);
  vm.runInContext(instrumented, context, { filename: 'content.js' });
  const api = context.testApi;
  function submit(form = sensitiveForm) {
    let prevented = false;
    events.submit({ target: form, preventDefault() { prevented = true; }, stopImmediatePropagation() {} });
    return prevented;
  }
  function node(id) {
    for (const child of root.children) {
      const found = child.shadowRoot?.getElementById(id);
      if (found) return found;
    }
    return null;
  }
  async function advance(ms) {
    const until = now + ms;
    while (true) {
      const next = [...timers.entries()].filter(([, timer]) => timer.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      const [id, timer] = next; now = timer.at;
      if (timer.interval) timer.at += timer.delay; else timers.delete(id);
      timer.fn(); await flush();
    }
    now = until; await flush();
  }
  return { api, context, document, requests, writes, captures, forms, ordinaryForm, sensitiveForm, password,
    events, windowEvents, timers, observer, root, body, text, node, submit, advance,
    mutate(record = { type: 'characterData', target: text }) { queue(record); observer.callback(observer.takeRecords()); } };
}

test('completed browser allow retains the full result and uses qualified copy', async () => {
  const h = harness();
  await h.api.analyze('test');
  assert.equal(h.api.state().lastDecision.brand, 'Test brand');
  assert.equal(h.api.state().lastDecision.reason_codes[0], 'TEST');
  assert.equal(h.api.state().lastDecision.analysis_status, 'completed');
  assert.equal(h.api.state().lastDecision.policy_version, 'test-v1');
  assert.equal(h.node('msg').textContent, '本页未发现明确风险。');
  assert.equal(h.submit(), false);
  assert.equal(JSON.parse(h.requests[0].body).analysis_mode, 'browser');
});

test('diagnostic client version matches the installed manifest', () => {
  const h = harness();
  const probe = h.body.children.find(n => n.id === '__pleg_probe');
  assert.equal(probe.dataset.clientVersion, manifest.version);
});

test('cold readiness wait does not consume the 30-second screenshot/analysis budget', async () => {
  let ready = false;
  const h = harness({modelStatus: cb => cb({ok:true,status:200,body:JSON.stringify({models_ready:ready,model_initialization:{state:ready?'ready':'loading'}})})});
  const pending=h.api.analyze('test'); await flush();
  await h.advance(31000);
  assert.equal(h.captures.length,0); assert.equal(h.requests.length,0);
  assert.equal(h.api.state().lastDecision,null);
  assert.match(h.node('msg').textContent,/初始化/);
  ready=true; await h.advance(1000); await pending;
  assert.equal(h.api.state().pageState,'allow'); assert.equal(h.captures.length,1);
});

test('initialization wait is bounded at 60 seconds with no automatic infinite restart', async () => {
  const h=harness({modelStatus:cb=>cb({ok:true,status:200,body:JSON.stringify({models_ready:false,model_initialization:{state:'loading'}})})});
  const pending=h.api.analyze('test');await flush();await h.advance(60001);await pending;
  assert.equal(h.api.state().pageState,'unavailable');assert.equal(h.api.state().lastDecision,null);
  assert.match(h.node('msg').textContent,/60 秒/);assert.equal(h.captures.length,0);
  await h.advance(120000);assert.equal(h.requests.length,0);
});

for(const state of ['failed','ready','garbage']) test(`unready or invalid initialization state ${state} cannot produce allow`,async()=>{
  const h=harness({modelStatus:cb=>cb({ok:true,status:200,body:JSON.stringify({models_ready:false,model_initialization:{state}})})});
  await h.api.analyze('test');assert.equal(h.api.state().pageState,'unavailable');assert.equal(h.api.state().lastDecision,null);assert.equal(h.captures.length,0);
});

test('navigation to management during model wait prevents capture and stale verdict',async()=>{
  const h=harness({modelStatus:cb=>cb({ok:true,status:200,body:JSON.stringify({models_ready:false,model_initialization:{state:'loading'}})})});
  const pending=h.api.analyze('test');await flush();
  h.context.location.href='http://127.0.0.1:8000/shadow';h.context.location.hostname='127.0.0.1';
  await h.advance(2000);await pending;
  assert.equal(h.captures.length,0);assert.equal(h.api.state().lastDecision,null);
});

for (const stage of ['CAPTURE', 'ANALYZE']) {
  test(`invalidated extension during ${stage} requires page refresh without server retry`, async () => {
    let invalid = false;
    const h = harness({sendMessageError: message => invalid && message.type === stage});
    await h.api.analyze('test');
    assert.equal(h.api.state().pageState, 'allow');
    invalid = true;
    await h.api.analyze('retry');
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.equal(h.api.state().lastDecision, null);
    assert.equal(h.api.state().warned, false);
    assert.match(h.node('msg').textContent, /扩展.*更新|扩展.*加载/);
    assert.match(h.node('msg').textContent, /刷新/);
    assert.doesNotMatch(h.node('msg').textContent, /无法连接|自动.*重试/);
    const captures = h.captures.length;
    const requests = h.requests.length;
    await h.advance(30000);
    assert.equal(h.captures.length, captures);
    assert.equal(h.requests.length, requests);
  });
}

test('status starts as a dot and opening details never requests analysis or grants consent', async () => {
  const h = harness({ fetch: async () => response(verdict('uncertain')) });
  await h.api.analyze('test');
  assert.equal(h.node('toast').dataset.expanded, 'false');
  assert.equal(h.node('pls-toggle').ariaExpanded, 'false');
  const requests = h.requests.length;
  h.node('pls-toggle').click(true);
  assert.equal(h.node('toast').dataset.expanded, 'true');
  assert.equal(h.node('pls-toggle').ariaExpanded, 'true');
  assert.equal(h.requests.length, requests);
  assert.equal(h.api.state().consentOnce, null);
  assert.equal(h.submit(), true);
  assert.equal(h.node('toast').dataset.expanded, 'true');
  h.node('pls-toggle').click(true);
  assert.equal(h.node('toast').dataset.expanded, 'false');
});

test('status updates preserve expansion and Escape collapses without hiding the indicator', async () => {
  const h = harness({ fetch: async () => response(verdict('uncertain')) });
  await h.api.analyze('test');
  h.node('pls-toggle').click(true);
  await h.api.analyze('retry');
  assert.equal(h.node('toast').dataset.expanded, 'true');
  let prevented = false;
  h.node('toast').listeners.keydown({ key: 'Escape', preventDefault() { prevented = true; } });
  assert.ok(prevented);
  assert.equal(h.node('toast').dataset.expanded, 'false');
  assert.equal(h.node('toast').style.display, 'flex');
  assert.equal(h.api.state().consentOnce, null);
});

test('completed status remains available to open after the old toast timeout', async () => {
  const h = harness();
  await h.api.analyze('test');
  await h.advance(5000);
  assert.equal(h.node('toast').style.display, 'flex');
  h.node('pls-toggle').click(true);
  await h.advance(5000);
  assert.equal(h.node('toast').dataset.expanded, 'true');
  assert.equal(h.node('toast').style.display, 'flex');
});

for (const [name, result] of [
  ['legacy response', { decision: 'allow' }],
  ['degraded allow', verdict('allow', { analysis_status: 'degraded' })],
  ['failed allow', verdict('allow', { analysis_status: 'failed' })],
  ['screenshot-only result', verdict('allow', { analysis_mode: 'screenshot_only' })],
  ['missing reason codes', verdict('allow', { reason_codes: undefined })],
  ['missing policy version', verdict('allow', { policy_version: undefined })],
  ['unknown decision', verdict('skipped')]
]) {
  test(`${name} cannot be cached as allow or displayed as completed`, async () => {
    const h = harness({ fetch: async () => response(result) });
    await h.api.analyze('test');
    assert.equal(h.api.state().lastDecision, null);
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.match(h.node('toast').className, /offline/);
    assert.equal(h.submit(), true);
  });
}

test('older allow cannot overwrite newer uncertain even if fetch ignores abort', async () => {
  const pending = [];
  const h = harness({ fetch: () => { const d = deferred(); pending.push(d); return d.promise; } });
  const older = h.api.analyze('page_load'); await flush();
  const newer = h.api.analyze('password_focus'); await flush();
  assert.equal(h.requests[0].signal.aborted, true);
  pending[1].resolve(response(verdict('uncertain'))); await newer;
  pending[0].resolve(response(verdict('allow'))); await older;
  assert.equal(h.api.state().lastDecision.decision, 'uncertain');
  assert.equal(h.submit(), true);
  assert.match(h.node('msg').textContent, /Test brand.*Test reason/);
});

for (const kind of ['url', 'dom']) {
  test(`${kind} changes discard an outstanding allow and invalidate existing consent`, async () => {
    const pending = deferred();
    const h = harness({ fetch: () => pending.promise });
    const analysis = h.api.analyze('test'); await flush();
    if (kind === 'url') h.context.location.href += '?next=1';
    else { h.text.nodeValue = 'Changed login page'; h.mutate(); }
    pending.resolve(response(verdict())); await analysis;
    assert.equal(h.api.state().lastDecision, null);
    assert.notEqual(h.api.state().pageState, 'allow');
  });
}

test('DOM mutation invalidates cached allow, but extension-owned DOM does not', async () => {
  const h = harness(); await h.api.analyze('test');
  const signature = h.api.pageSignature();
  const probe = h.body.children.find((node) => node.id === '__pleg_probe');
  probe.appendChild({ nodeType: 3, nodeValue: 'Extension diagnostics text' });
  h.observer.callback(h.observer.takeRecords());
  assert.equal(h.api.pageSignature(), signature);
  assert.equal(h.submit(), false);
  h.mutate({ type: 'attributes', target: h.body });
  assert.equal(h.api.state().lastDecision, null);
});

test('failure clears an earlier allow and does not interrupt ordinary forms', async () => {
  let offline = false;
  const h = harness({ fetch: async () => { if (offline) throw new Error('offline'); return response(verdict()); } });
  await h.api.analyze('test'); offline = true;
  await h.api.analyze('recheck');
  assert.equal(h.api.state().lastDecision, null);
  assert.equal(h.submit(h.ordinaryForm), false);
  assert.equal(h.submit(), true);
});

test('a page without sensitive inputs never has ordinary forms intercepted', async () => {
  const h = harness({ sensitive: false, fetch: async () => { throw new Error('offline'); } });
  assert.equal(h.submit(h.ordinaryForm), false);
  await h.api.analyze('test');
  assert.equal(h.submit(h.ordinaryForm), false);
});

for (const stage of ['capture', 'fetch', 'json']) {
  test(`30-second timeout cancels ${stage} waiting and cleans its timer`, async () => {
    const never = new Promise(() => {});
    const h = harness({
      capture: stage === 'capture' ? () => {} : undefined,
      fetch: () => stage === 'fetch' ? never : Promise.resolve({ ok: true, status: 200, json: () => never })
    });
    const analysis = h.api.analyze('test'); await flush();
    await h.advance(30000); await analysis;
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.equal(h.api.state().lastDecision, null);
    assert.equal(h.api.state().activeRequest, null);
    assert.equal([...h.timers.values()].some((timer) => !timer.interval && timer.delay === 30000), false);
    if (h.requests.length) assert.equal(h.requests[0].signal.aborted, true);
    assert.match(h.node('msg').textContent, /超时/);
  });
}

test('capture errors clean timers and never retain allow', async () => {
  const h = harness({ capture: (callback) => callback({ error: 'TAB_NOT_ACTIVE' }) });
  await h.api.analyze('test');
  assert.equal(h.requests.length, 0);
  assert.equal(h.api.state().pageState, 'unavailable');
  assert.equal([...h.timers.values()].some((timer) => !timer.interval && timer.delay === 30000), false);
});

for (const failure of ['http', 'json']) {
  test(`${failure} errors clear the cached allow and timeout`, async () => {
    let fail = false;
    const h = harness({ fetch: async () => {
      if (!fail) return response(verdict());
      return failure === 'http' ? { ok: false, status: 503 }
        : { ok: true, status: 200, json: async () => { throw new Error('invalid json'); } };
    } });
    await h.api.analyze('test'); fail = true; await h.api.analyze('recheck');
    assert.equal(h.api.state().lastDecision, null);
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.equal([...h.timers.values()].some((timer) => !timer.interval && timer.delay === 30000), false);
  });
}

test('old persistent skip is ignored, including after reload', async () => {
  const storage = { 'phishlens_skip_example.test': { at: 1 } };
  for (let reload = 0; reload < 2; reload++) {
    const h = harness({ storage }); h.api.onLoadAnalyze(); await h.advance(1800);
    assert.equal(h.requests.length, 1);
    assert.equal(h.submit(), false);
    assert.equal(h.writes.length, 0);
  }
});

for (const decision of ['block', 'uncertain']) {
  test(`${decision}: synthetic clicks fail; trusted confirmation grants one short-lived submit`, async () => {
    const h = harness({ fetch: async () => response(verdict(decision)) });
    await h.api.analyze('test');
    if (decision === 'uncertain') h.submit();
    const button = h.node(decision === 'block' ? 'pleg-continue' : 'act');
    button.click(false);
    assert.equal(h.api.state().consentOnce, null);
    assert.equal(h.submit(), true);
    button.click(true);
    assert.ok(h.api.state().consentOnce);
    assert.equal(h.submit(), false);
    assert.equal(h.api.state().consentOnce, null);
    assert.equal(h.submit(), true);
    assert.equal(h.writes.length, 0);
  });
}

test('confirmation expires and is revoked by URL or DOM changes', async () => {
  for (const change of ['expiry', 'url', 'dom']) {
    const h = harness({ fetch: async () => response(verdict('uncertain')) });
    await h.api.analyze('test'); h.submit(); h.node('act').click(true);
    if (change === 'expiry') await h.advance(30001);
    if (change === 'url') h.context.location.href += '#new';
    if (change === 'dom') h.mutate();
    assert.equal(h.submit(), true, change);
    assert.equal(h.api.state().consentOnce, null, change);
    await flush();
  }
});

test('warning insertion/removal and a delayed page load preserve a valid one-time grant', async () => {
  const h = harness({ fetch: async () => response(verdict('block')) });
  await h.api.analyze('test');
  const signature = h.api.pageSignature();
  h.observer.callback(h.observer.takeRecords());
  assert.equal(h.api.state().lastDecision.decision, 'block');
  h.node('pleg-continue').click(true);
  h.observer.callback(h.observer.takeRecords());
  assert.equal(h.api.pageSignature(), signature);
  h.api.onLoadAnalyze(); await h.advance(1800);
  assert.equal(h.requests.length, 1);
  assert.doesNotMatch(h.node('toast').className, /checking/);
  assert.equal(h.submit(), false);
});

test('an expired completed allow triggers a new check before sensitive submit', async () => {
  const h = harness(); await h.api.analyze('test'); await h.advance(60001);
  assert.equal(h.submit(), true);
  assert.equal(h.api.state().lastDecision, null);
  await flush();
  assert.equal(h.requests.length, 2);
  assert.equal(h.submit(), false);
});

test('unavailable confirmation rejects synthetic clicks and permits only one explicit submit', async () => {
  const h = harness({ fetch: async () => { throw new Error('offline'); } });
  await h.api.analyze('test'); h.submit();
  h.node('act').click(false); assert.equal(h.api.state().consentOnce, null);
  h.node('act').click(true); assert.equal(h.submit(), false);
  assert.equal(h.submit(), true);
});

test('repeated password focus cannot extend an active request deadline', async () => {
  const h = harness({ fetch: () => new Promise(() => {}) });
  h.events.focusin({ target: h.password }); await flush();
  await h.advance(20000);
  h.events.focusin({ target: h.password }); await flush();
  assert.equal(h.requests.length, 1);
  await h.advance(10000);
  assert.equal(h.api.state().pageState, 'unavailable');
  assert.match(h.node('msg').textContent, /超时/);
});

test('a page-read exception after the response ends the checking state', async () => {
  const pending = deferred();
  const h = harness({ fetch: () => pending.promise });
  const analysis = h.api.analyze('test'); await flush();
  h.document.querySelectorAll = () => { throw Error('page read failed'); };
  pending.resolve(response(verdict()));
  await analysis;
  assert.equal(h.api.state().pageState, 'unavailable');
  assert.equal(h.api.state().lastDecision, null);
  assert.equal(h.api.state().activeRequest, null);
  assert.doesNotMatch(h.node('toast').className, /checking/);
});

test('completed block hides the waiting toast and displays the warning', async () => {
  const h = harness({ fetch: async () => response(verdict('block')) });
  await h.api.analyze('test');
  assert.equal(h.node('toast').style.display, 'none');
  assert.ok(h.node('pleg-continue'));
  assert.equal(h.api.state().activeRequest, null);
});

test('hidden tab defers capture and retries once when visible', async () => {
  let captures = 0;
  const h = harness({ capture: (cb) => { captures++; cb({dataUrl:'data:image/png;base64,TEST'}); } });
  h.document.visibilityState = 'hidden';
  await h.api.analyze('page_load');
  assert.equal(captures, 0);
  assert.match(h.node('msg').textContent, /切回此标签/);
  h.document.visibilityState = 'visible';
  h.events.visibilitychange(); h.events.visibilitychange();
  await h.advance(350);
  assert.equal(captures, 1);
  assert.equal(h.requests.length, 1);
  h.events.visibilitychange(); await h.advance(1000);
  assert.equal(captures, 1);
});

test('a successful retry clears old error diagnostics', async () => {
  let fail = true;
  const h = harness({ capture: cb => cb(fail ? {error:'TAB_NOT_ACTIVE'} : {dataUrl:'data:image/png;base64,TEST'}) });
  await h.api.analyze('page_load');
  const probe = h.body.children.find(n => n.id === '__pleg_probe');
  assert.equal(probe.dataset.captureError,'TAB_NOT_ACTIVE');
  fail = false; await h.api.analyze('retry');
  assert.equal(probe.dataset.captureError,undefined);
  assert.equal(probe.dataset.abortReason,undefined);
  assert.equal(probe.dataset.httpStatus,'200');
});

test('deferring a hidden tab cannot be overwritten by the aborted old request', async () => {
  const h=harness({fetch:()=>new Promise(()=>{})});
  const older=h.api.analyze('page_load');await flush();
  h.document.visibilityState='hidden';await h.api.analyze('password_focus');await older;
  assert.match(h.node('msg').textContent,/切回此标签/);
  assert.equal(h.api.state().activeRequest,null);
});

test('late capture errors cannot contaminate a newer successful request', async () => {
  const callbacks=[];
  const h=harness({capture:cb=>callbacks.push(cb)});
  const first=h.api.analyze('page_load'); await flush();
  const second=h.api.analyze('retry'); await flush();
  callbacks[1]({dataUrl:'data:image/png;base64,TEST'}); await second;
  callbacks[0]({error:'TAB_NOT_ACTIVE'}); await first;
  const probe=h.body.children.find(n=>n.id==='__pleg_probe');
  assert.equal(probe.dataset.captureError,undefined);
  assert.equal(h.api.state().pageState,'allow');
});

test('no visible logo explains the limitation and exposes a retry button', async () => {
  const h=harness({fetch:async()=>response(verdict('uncertain',{
    brand:null,reason_codes:['BRAND_UNCONFIRMED','NO_LOGO_REGION']}))});
  await h.api.analyze('test');
  assert.match(h.node('msg').textContent,/品牌标志所在位置/);
  assert.ok(h.node('act'));
});

test('logo proposals contain coordinates but never image URLs or input values', () => {
  const h=harness();
  h.context.window.innerWidth=1000;h.context.window.innerHeight=700;
  const image={getBoundingClientRect:()=>({left:100,top:100,right:164,bottom:164,width:64,height:64}),closest:()=>null};
  h.document.querySelectorAll=selector=>selector==='img, svg'?[image]:[];
  const container={getBoundingClientRect:()=>({left:80,top:80,right:480,bottom:680,width:400,height:600}),
    querySelector:()=>image,querySelectorAll:()=>[{textContent:'支付宝安全中心'}]};
  const form={querySelector:selector=>selector==='input[type="password"]'?h.password:null,parentElement:container};
  h.password.getBoundingClientRect=()=>({left:100,top:300,right:400,bottom:344,width:300,height:44});
  Object.defineProperty(h.password,'value',{get(){throw Error('must not read credentials');}});
  const context=h.api.visualDomContext([form]);
  assert.equal(context.logo_regions.length,1);
  assert.equal(context.credential_contexts[0].text,'支付宝安全中心');
  assert.equal(context.logo_regions[0].box.join(','),'100,100,164,164');
});

for (const failure of ['capture', 'network', 408, 429, 500, 503]) {
  test(`${failure} failure retries automatically after 2 seconds and clears diagnostics on recovery`, async () => {
    let failing = true, captures = 0;
    const h = harness({
      capture(cb) {
        captures++;
        cb(failing && failure === 'capture' ? { error: 'CAPTURE_FAILED' } : { dataUrl: 'data:image/png;base64,TEST' });
      },
      fetch: async () => {
        if (failing && failure === 'network') throw new TypeError('Failed to fetch');
        return failing && typeof failure === 'number' ? { ok: false, status: failure } : response(verdict());
      }
    });
    await h.api.analyze('test');
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.match(h.node('msg').textContent, /自动.*重试|自动.*检测/);
    failing = false;
    await h.advance(1999); assert.equal(captures, 1);
    await h.advance(1); assert.equal(captures, 2);
    assert.equal(h.api.state().pageState, 'allow');
    const probe = h.body.children.find(n => n.id === '__pleg_probe');
    for (const key of ['captureError', 'abortReason', 'fetchError']) assert.equal(probe.dataset[key], undefined, key);
    assert.equal(probe.dataset.httpStatus, '200');
    await h.advance(20000); assert.equal(captures, 2, 'success cancels further retries');
  });
}

test('automatic retries stop after 2/5/10-second attempts and only trusted manual retry resets the budget', async () => {
  const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
  await h.api.analyze('test');
  for (const [delay, before] of [[2000, 1], [5000, 2], [10000, 3]]) {
    await h.advance(delay - 1); assert.equal(h.requests.length, before);
    await h.advance(1); assert.equal(h.requests.length, before + 1);
  }
  await h.advance(30000); assert.equal(h.requests.length, 4);
  assert.match(h.node('msg').textContent, /重新检测|重试/);
  h.node('act').click(false); await flush();
  assert.equal(h.requests.length, 4);
  h.node('act').click(true); await flush();
  assert.equal(h.requests.length, 5);
  await h.advance(1999); assert.equal(h.requests.length, 5);
  await h.advance(1); assert.equal(h.requests.length, 6);
});

test('timeout retries after the original wait ends and late completion cannot replace the recovered result', async () => {
  const old = deferred();
  let attempts = 0;
  const h = harness({ fetch: () => ++attempts === 1 ? old.promise : Promise.resolve(response(verdict())) });
  const first = h.api.analyze('test'); await flush();
  await h.advance(30000); await first;
  assert.equal(h.requests[0].signal.aborted, true);
  await h.advance(1999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.api.state().pageState, 'allow');
  old.resolve(response(verdict('block'))); await flush();
  assert.equal(h.api.state().pageState, 'allow');
  assert.equal(h.api.state().warned, false);
});

for (const failure of ['protocol', 'json', 400, 401, 403, 404, 422]) {
  test(`${failure} failure explains the cause without an automatic retry loop`, async () => {
    const h = harness({ fetch: async () => {
      if (failure === 'protocol') return response({ decision: 'allow' });
      if (failure === 'json') return { ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token'); } };
      return { ok: false, status: failure };
    } });
    await h.api.analyze('test');
    assert.equal(h.api.state().pageState, 'unavailable');
    assert.match(h.node('msg').textContent, failure === 'protocol' ? /协议|兼容/ : failure === 'json' ? /JSON|格式|解析/ : new RegExp(`HTTP ${failure}`));
    await h.advance(20000);
    assert.equal(h.requests.length, 1);
    assert.equal(h.node('act').style.display, 'inline-block');
  });
}

for (const kind of ['dom', 'url']) {
  test(`${kind} change clears old errors immediately and debounces a fresh check`, async () => {
    let failing = true;
    const h = harness({ fetch: async () => failing ? { ok: false, status: 503 } : response(verdict()) });
    await h.api.analyze('test');
    const probe = h.body.children.find(n => n.id === '__pleg_probe');
    assert.equal(probe.dataset.httpStatus, '503');
    function change() {
      if (kind === 'url') { h.context.location.href += '#changed'; h.windowEvents.hashchange(); }
      else { h.text.nodeValue += ' changed'; h.mutate(); }
    }
    change();
    assert.equal(h.api.state().pageState, 'unchecked');
    for (const key of ['captureError', 'abortReason', 'fetchError', 'httpStatus', 'decision', 'visualStatus']) {
      assert.equal(probe.dataset[key], undefined, key);
    }
    assert.doesNotMatch(h.node('msg').textContent, /503/);
    failing = false;
    await h.advance(500); change();
    await h.advance(749); assert.equal(h.requests.length, 1);
    await h.advance(1); assert.equal(h.requests.length, 2);
    assert.equal(h.api.state().pageState, 'allow');
    await h.advance(3000); assert.equal(h.requests.length, 2, 'old error retry was cancelled');
  });
}

for (const stage of ['capture', 'fetch']) {
  test(`hiding an in-flight ${stage} invalidates it and visibility resumes exactly once`, async () => {
    const callbacks = [], pending = [];
    const h = harness({
      capture: stage === 'capture' ? cb => callbacks.push(cb) : undefined,
      fetch: stage === 'fetch' ? () => { const d = deferred(); pending.push(d); return d.promise; } : undefined
    });
    const first = h.api.analyze('test'); await flush();
    const firstRequest = h.api.state().activeRequest;
    h.document.visibilityState = 'hidden'; h.events.visibilitychange(); await flush();
    assert.equal(firstRequest.controller.signal.aborted, true);
    assert.equal(h.api.state().activeRequest, null);
    assert.equal(h.api.state().lastDecision, null);
    await h.advance(5000);
    assert.equal(stage === 'capture' ? callbacks.length : h.requests.length, 1);
    h.document.visibilityState = 'visible';
    h.events.visibilitychange(); h.events.visibilitychange();
    await h.advance(349);
    assert.equal(stage === 'capture' ? callbacks.length : h.requests.length, 1);
    await h.advance(1);
    assert.equal(stage === 'capture' ? callbacks.length : h.requests.length, 2);
    if (stage === 'capture') callbacks[1]({ dataUrl: 'data:image/png;base64,TEST' });
    else pending[1].resolve(response(verdict()));
    await flush();
    if (stage === 'capture') callbacks[0]({ error: 'TAB_NOT_ACTIVE' });
    else pending[0].resolve(response(verdict('block')));
    await first; await flush();
    assert.equal(h.api.state().pageState, 'allow');
    assert.equal(h.api.state().warned, false);
    assert.equal(h.body.children.find(n => n.id === '__pleg_probe').dataset.captureError, undefined);
    h.events.visibilitychange(); await h.advance(5000);
    assert.equal(stage === 'capture' ? callbacks.length : h.requests.length, 2);
  });
}

test('TAB_NOT_ACTIVE arriving after the visibility event still recovers with bounded retries', async () => {
  const callbacks = [];
  const h = harness({ capture: cb => callbacks.push(cb) });
  const first = h.api.analyze('page_load'); await flush();
  h.events.visibilitychange();
  callbacks[0]({ error: 'TAB_NOT_ACTIVE' }); await first;
  await h.advance(1999); assert.equal(callbacks.length, 1);
  await h.advance(1); assert.equal(callbacks.length, 2);
  callbacks[1]({ dataUrl: 'data:image/png;base64,TEST' }); await flush();
  assert.equal(h.api.state().pageState, 'allow');
});

test('delayed page_load leaves an active matching request and its original deadline intact', async () => {
  const h = harness({ fetch: () => new Promise(() => {}) });
  h.api.analyze('password_focus'); await flush();
  const request = h.api.state().activeRequest;
  h.api.onLoadAnalyze(); await h.advance(1800);
  assert.equal(h.requests.length, 1);
  assert.equal(h.api.state().activeRequest, request);
  assert.equal(request.controller.signal.aborted, false);
  await h.advance(28200);
  assert.equal(h.api.state().activeRequest, null);
  assert.equal(h.api.state().pageState, 'unavailable');
});

test('pagehide cancels retry, load and page-change timers; a persisted pageshow starts a fresh check', async () => {
  for (const pending of ['retry', 'load', 'change', 'visible']) {
    const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
    if (pending === 'retry') await h.api.analyze('test');
    if (pending === 'load') h.api.onLoadAnalyze();
    if (pending === 'change') {
      await h.api.analyze('test');
      h.text.nodeValue += ' changed'; h.mutate();
    }
    if (pending === 'visible') {
      h.document.visibilityState = 'hidden'; await h.api.analyze('page_load');
      h.document.visibilityState = 'visible'; h.events.visibilitychange();
    }
    const before = h.requests.length;
    h.windowEvents.pagehide(); await h.advance(20000);
    assert.equal(h.requests.length, before, pending);
    assert.equal(h.api.state().activeRequest, null, pending);
    h.windowEvents.pageshow({ persisted: true });
    await h.advance(1799); assert.equal(h.requests.length, before, pending);
    await h.advance(1); assert.equal(h.requests.length, before + 1, pending);
  }
});

test('visual service failure explains its cause and respects the model-load cooldown before retrying', async () => {
  let failing = true;
  const h = harness({ fetch: async () => response(failing
    ? verdict('uncertain', { analysis_status: 'degraded', reason_codes: ['VISUAL_ANALYSIS_FAILED'] })
    : verdict()) });
  await h.api.analyze('test');
  assert.equal(h.api.state().lastDecision, null);
  assert.match(h.node('msg').textContent, /视觉/);
  assert.match(h.node('msg').textContent, /失败|加载|不可用/);
  assert.equal(h.node('act').style.display, 'inline-block');
  failing = false;
  await h.advance(29999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.api.state().pageState, 'allow');
});

test('completed no-logo uncertainty does not automatically repeat an unchanged screenshot', async () => {
  const h = harness({ fetch: async () => response(verdict('uncertain', {
    brand: null, reason_codes: ['BRAND_UNCONFIRMED', 'NO_LOGO_REGION']
  })) });
  await h.api.analyze('test');
  await h.advance(60000);
  assert.equal(h.requests.length, 1);
  assert.equal(h.api.state().pageState, 'uncertain');
  assert.match(h.node('msg').textContent, /品牌标志所在位置/);
});

test('a trusted one-time confirmation cancels the queued automatic retry without consuming consent', async () => {
  const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
  await h.api.analyze('test');
  assert.equal(h.submit(), true);
  h.node('act').click(true);
  assert.ok(h.api.state().consentOnce);
  await h.advance(10000);
  assert.equal(h.requests.length, 1);
  assert.ok(h.api.state().consentOnce);
  assert.equal(h.submit(), false);
  assert.equal(h.submit(), true);
});

test('hiding during backoff preserves the automatic retry budget when the tab becomes visible', async () => {
  const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
  await h.api.analyze('test');
  h.document.visibilityState = 'hidden'; h.events.visibilitychange();
  await h.advance(10000); assert.equal(h.requests.length, 1);
  h.document.visibilityState = 'visible'; h.events.visibilitychange();
  await h.advance(350); assert.equal(h.requests.length, 2);
  await h.advance(4999); assert.equal(h.requests.length, 2, 'the visible retry used the first automatic attempt');
  await h.advance(1); assert.equal(h.requests.length, 3);
  await h.advance(10000); assert.equal(h.requests.length, 4);
  await h.advance(20000); assert.equal(h.requests.length, 4, 'visibility does not reset the retry limit');
});

test('returning visible during a pending retry keeps the remaining backoff', async () => {
  const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
  await h.api.analyze('test');
  h.document.visibilityState = 'hidden'; h.events.visibilitychange();
  await h.advance(500);
  h.document.visibilityState = 'visible'; h.events.visibilitychange();
  await h.advance(1499); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2);
  await h.advance(4999); assert.equal(h.requests.length, 2);
  await h.advance(1); assert.equal(h.requests.length, 3);
});

test('switching tabs does not bypass the pending visual-model cooldown', async () => {
  let failing = true;
  const h = harness({ fetch: async () => response(failing
    ? verdict('uncertain', { analysis_status: 'degraded', reason_codes: ['VISUAL_ANALYSIS_FAILED'] })
    : verdict()) });
  await h.api.analyze('test');
  await h.advance(1000);
  h.document.visibilityState = 'hidden'; h.events.visibilitychange();
  await h.advance(1000);
  h.document.visibilityState = 'visible'; h.events.visibilitychange();
  failing = false;
  await h.advance(27999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2);
  assert.equal(h.api.state().pageState, 'allow');
});

test('delayed page_load preserves a scheduled retry and its existing budget', async () => {
  const h = harness({ fetch: async () => { throw new TypeError('Failed to fetch'); } });
  h.api.onLoadAnalyze(); await h.api.analyze('password_focus');
  await h.advance(1800); assert.equal(h.requests.length, 1);
  await h.advance(200); assert.equal(h.requests.length, 2);
  h.api.onLoadAnalyze();
  await h.advance(1800); assert.equal(h.requests.length, 2);
  await h.advance(3200); assert.equal(h.requests.length, 3);
  await h.advance(10000); assert.equal(h.requests.length, 4);
  await h.advance(20000); assert.equal(h.requests.length, 4);
});

test('delayed page_load keeps a fresh completed verdict and renews it after expiry', async () => {
  const h = harness();
  h.api.onLoadAnalyze(); await h.api.analyze('password_focus');
  const completed = h.api.state().lastDecision;
  await h.advance(1800);
  assert.equal(h.requests.length, 1);
  assert.equal(h.api.state().lastDecision, completed);
  await h.advance(60000);
  h.api.onLoadAnalyze(); await h.advance(1800);
  assert.equal(h.requests.length, 2);
  assert.equal(h.api.state().pageState, 'allow');
});

test('shadow high-risk suggestion displays evidence but never changes enforcement', async () => {
  const h = harness({fetch: async () => response(verdict('allow', {
    shadow_fusion: {mode:'shadow', status:'completed', suggested_decision:'block',
      url_analysis:{status:'completed',ml_score:.91}, visual:{status:'completed',brand:'Test'},
      form_risk:{level:'high'}}
  }))});
  await h.api.analyze('manual');
  assert.equal(h.api.state().pageState, 'allow');
  assert.equal(h.api.state().warned, false);
  assert.equal(h.api.state().lastDecision.decision, 'allow');
  assert.match(h.node('msg').children.at(-1).textContent, /91.0%/);
  assert.match(h.node('msg').children.at(-1).textContent, /不改变拦截/);
});

for (const [level, title] of [['not_observed','通用检查已完成'],['caution','发现需核实的信号'],['high','发现凭据风险']]) {
  test('general risk '+level+' does not masquerade as a brand verdict or hide details', async()=>{
    const h=harness({fetch:async()=>response(verdict('uncertain', {
      brand:null, reason_codes:['BRAND_UNCONFIRMED'],
      general_risk:{status:'completed',level,summary:'具体通用风险证据；不能保证网站安全'}
    }))});
    await h.api.analyze('manual');
    assert.equal(h.node('status-title').textContent,title);
    assert.match(h.node('msg').textContent,/具体通用风险证据/);
    assert.equal(h.api.state().warned,false);
    assert.equal(h.api.state().lastDecision.decision,'uncertain');
  });
}

for (const route of ['/', '/shadow', '/shadow/?view=latest#records', '/checker',
  '/alerts', '/docs', '/redoc', '/health', '/openapi.json', '/api/v1/stats/shadow']) {
  test(`management route ${route}: load, refresh, polling, focus and visibility never analyze`, async () => {
    const h = harness({url:'http://127.0.0.1:8000'+route});
    h.windowEvents.load();
    for (let i = 0; i < 3; i++) {
      h.text.nodeValue = 'Updated status '+i;
      h.mutate();
      h.mutate({type:'childList',target:h.body,addedNodes:[{}],removedNodes:[{}]});
      await h.advance(30000);
      h.windowEvents.load();
      h.events.focusin({target:h.password});
      h.document.visibilityState = 'hidden'; h.events.visibilitychange();
      h.document.visibilityState = 'visible'; h.events.visibilitychange();
      await h.api.analyze('retry');
    }
    assert.equal(h.captures.length,0);
    assert.equal(h.requests.length,0);
    assert.equal(h.api.state().pageState,'skipped');
    assert.equal(h.api.state().lastDecision,null);
    assert.equal(h.submit(),false, 'excluded pages do not enter the submission guard');
  });
}

for (const mime of ['image/png','image/jpeg','image/svg+xml','text/css','text/javascript',
  'application/javascript','font/woff2','application/pdf','application/json','text/plain']) {
  test(`top-level ${mime} skips even when URL ends in .html`, async () => {
    const h=harness({url:'http://192.168.1.10:8081/fake_alipay.html',mime});
    h.windowEvents.load();
    h.mutate();
    await h.advance(60000);
    await h.api.analyze('manual');
    assert.equal(h.captures.length,0);
    assert.equal(h.requests.length,0);
    assert.equal(h.api.state().pageState,'skipped');
    assert.equal(h.api.state().lastDecision,null);
  });
}

for (const [url,mime] of [
  ['http://192.168.1.10:8081/fake_alipay.html','text/html'],
  ['http://127.0.0.1:8001/fake_alipay.html','text/html'],
  ['http://localhost:8001/fake_alipay.html','text/html'],
  ['http://[::1]:8001/shadow','text/html'],
  ['http://127.0.0.1:8000/unrelated-page','text/html'],
  ['http://127.0.0.1:8000/shadow-imitator','text/html'],
  ['https://public.example/shadow','text/html'],
  ['http://127.0.0.1.attacker.example:8000/shadow','text/html'],
  ['http://192.168.1.10:8081/alipay_logo.png','text/html'],
  ['https://ordinary.example/no-extension','application/xhtml+xml'],
  ['https://ordinary.example/no-extension',''],
]) {
  test(`eligible ${url} (${mime || 'unknown MIME'}) loads and rechecks real DOM changes`, async () => {
    const h=harness({url,mime});
    h.windowEvents.load(); await h.advance(1800);
    assert.equal(h.requests.length,1);
    assert.equal(JSON.parse(h.requests[0].body).url,url);
    h.text.nodeValue='Changed login page'; h.mutate();
    await h.advance(750);
    assert.equal(h.requests.length,2);
    assert.equal(JSON.parse(h.requests[1].body).trigger,'page_change');
    assert.equal(h.captures.length,2);
  });
}

test('navigation into management cancels pending analysis; late block cannot apply; leaving resumes', async () => {
  const pending=deferred();
  const h=harness({url:'http://127.0.0.1:8000/unrelated-page',fetch:()=>pending.promise});
  const running=h.api.analyze('manual'); await flush();
  assert.equal(h.requests.length,1);
  h.context.location.href='http://127.0.0.1:8000/shadow';
  h.windowEvents.popstate(); await flush();
  assert.equal(h.requests[0].signal.aborted,true);
  pending.resolve(response(verdict('block'))); await running; await flush();
  assert.equal(h.api.state().pageState,'skipped');
  assert.equal(h.api.state().lastDecision,null);
  assert.equal(h.api.state().warned,false);
  await h.advance(60000); assert.equal(h.requests.length,1);
  h.context.location.href='http://127.0.0.1:8000/unrelated-page';
  h.windowEvents.popstate(); await h.advance(750);
  assert.equal(h.requests.length,2);
  assert.equal(h.api.state().lastDecision.decision,'block');
});

test('confirmed non-HTML context cancels capture and does not turn into allow', async () => {
  let captured;
  const h=harness({capture:callback=>{captured=callback;}});
  const running=h.api.analyze('manual'); await flush();
  h.document.contentType='image/png'; h.mutate();
  captured({dataUrl:'data:image/png;base64,LATE'}); await running; await flush();
  await h.advance(60000);
  assert.equal(h.requests.length,0);
  assert.equal(h.api.state().pageState,'skipped');
  assert.equal(h.api.state().lastDecision,null);
});
