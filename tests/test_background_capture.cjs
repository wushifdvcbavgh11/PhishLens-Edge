const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../browser-extension/background.js'), 'utf8');

function capture({ before = {}, after = {}, sender = {}, captureError = null, duringCapture = null, dataUrl = 'data:image/png;base64,test' } = {}) {
  let listener;
  let reads = 0;
  let captures = 0;
  const base = { id: 7, windowId: 3, active: true, url: 'https://example.test/login' };
  const runtime = { lastError: null, onMessage: { addListener(fn) { listener ||= fn; } } };
  const events = {};
  vm.runInNewContext(source, {
    chrome: {
      runtime,
      tabs: {
        onActivated: { addListener(fn) { events.activated = fn; } },
        onUpdated: { addListener(fn) { events.updated = fn; } },
        onRemoved: { addListener(fn) { events.removed = fn; } },
        get(id, callback) { reads++; callback({ ...base, ...(reads === 1 ? before : after) }); },
        captureVisibleTab(windowId, options, callback) {
          captures++;
          assert.equal(windowId, 3);
          if (duringCapture) duringCapture(events);
          runtime.lastError = captureError && { message: captureError };
          callback(dataUrl);
          runtime.lastError = null;
        }
      }
    }
  });
  let result;
  listener({ type: 'CAPTURE' }, { tab: base, frameId: 0, url: base.url, ...sender }, value => { result = value; });
  return { result, reads, captures };
}

test('returns a screenshot only for the unchanged active tab', () => {
  const { result, reads } = capture();
  assert.equal(result.tabId, 7);
  assert.ok(result.dataUrl);
  assert.equal(reads, 2);
});

test('does not capture an inactive tab or a different document URL', () => {
  const inactive = capture({ before: { active: false } });
  assert.equal(inactive.result.error, 'TAB_NOT_ACTIVE');
  assert.equal(inactive.captures, 0);
  const changed = capture({ before: { url: 'https://example.test/other' } });
  assert.equal(changed.result.error, 'PAGE_CHANGED');
  assert.equal(changed.captures, 0);
});

test('discards captures after navigation, tab activation change, or window move', () => {
  for (const after of [{ active: false }, { url: 'https://example.test/other' }, { windowId: 8 }]) {
    const { result } = capture({ after });
    assert.equal(result.error, 'PAGE_CHANGED');
    assert.equal(result.dataUrl, null);
  }
});

test('does not misrepresent a subframe screenshot as a top-level page', () => {
  const { result, captures } = capture({ sender: { frameId: 4 } });
  assert.equal(result.error, 'UNSUPPORTED_FRAME');
  assert.equal(captures, 0);
});

test('discards switch-away-and-back or same-URL reload during capture', () => {
  for (const duringCapture of [
    events => { events.activated({ windowId: 3, tabId: 8 }); events.activated({ windowId: 3, tabId: 7 }); },
    events => events.updated(7, { status: 'loading' }),
    events => events.removed(7)
  ]) {
    assert.equal(capture({ duringCapture }).result.error, 'PAGE_CHANGED');
  }
});

test('activity in another window does not invalidate this capture', () => {
  const { result } = capture({ duringCapture: events => events.activated({ windowId: 4, tabId: 9 }) });
  assert.ok(result.dataUrl);
});

test('capture errors and empty captures never contain an image', () => {
  assert.equal(capture({ captureError: 'capture unavailable' }).result.dataUrl, null);
  assert.equal(capture({ dataUrl: '' }).result.error, 'EMPTY_CAPTURE');
});

test('accepts a stable fragment navigation despite the sender retaining its document URL', () => {
  const url = 'https://example.test/login#password';
  const { result, captures } = capture({ before: { url }, after: { url } });
  assert.ok(result.dataUrl);
  assert.equal(captures, 1);
});

test('fragment handling still rejects changed paths, queries and navigation during capture', () => {
  for (const url of ['https://other.test/login#password', 'https://example.test/other#password',
    'https://example.test/login?new=1#password']) {
    const { result, captures } = capture({ before: { url }, after: { url } });
    assert.equal(result.error, 'PAGE_CHANGED');
    assert.equal(captures, 0);
  }
  assert.equal(capture({ after: { url: 'https://example.test/login#new' } }).result.error, 'PAGE_CHANGED');
  assert.equal(capture({ duringCapture: events => events.updated(7, { url: 'https://example.test/login#new' }) }).result.error, 'PAGE_CHANGED');
});
