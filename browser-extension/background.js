// PhishLens Edge Guard - background service worker (MV3)
// Sole job: capture the ACTIVE visible tab as PNG on demand from content scripts,
// Verify the requesting tab before AND after capture. The content script also
// binds the result to its current page revision; a screenshot is not a durable
// identity for a page that can navigate or change while analysis is in flight.

const pendingCaptures = new Set();
chrome.tabs.onActivated.addListener(({ windowId }) => {
  for (const pending of pendingCaptures) {
    if (pending.windowId === windowId) pending.changed = true;
  }
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (!change.url && change.status !== 'loading') return;
  for (const pending of pendingCaptures) {
    if (pending.tabId === tabId) pending.changed = true;
  }
});
chrome.tabs.onRemoved.addListener((tabId) => {
  for (const pending of pendingCaptures) {
    if (pending.tabId === tabId) pending.changed = true;
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== 'CAPTURE') {
    return false;
  }

  const tabId = sender.tab ? sender.tab.id : null;
  if (tabId == null) {
    sendResponse({ dataUrl: null, error: 'NO_TAB' });
    return false;
  }
  if (sender.frameId != null && sender.frameId !== 0) {
    sendResponse({ dataUrl: null, error: 'UNSUPPORTED_FRAME' });
    return false;
  }
  const pending = { tabId, windowId: sender.tab.windowId, changed: false };
  pendingCaptures.add(pending);
  const reply = (value) => {
    pendingCaptures.delete(pending);
    sendResponse(value);
  };

  chrome.tabs.get(tabId, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      reply({ dataUrl: null, error: 'TAB_GONE' });
      return;
    }
    // Do NOT capture if the requesting tab is not the active one —
    // captureVisibleTab only grabs the active tab and would mismatch.
    if (!tab.active) {
      reply({ dataUrl: null, error: 'TAB_NOT_ACTIVE' });
      return;
    }
    const capturedUrl = tab.url;
    if (pending.changed || !capturedUrl || (sender.url && sender.url !== capturedUrl)) {
      reply({ dataUrl: null, error: 'PAGE_CHANGED' });
      return;
    }
    const windowId = tab.windowId;
    pending.windowId = windowId;
    // captureVisibleTab(windowId) targets the active tab of the given window,
    // which is exactly the tab that sent this message (verified active above).
    // The window does not need to be OS-focused — specifying windowId makes the
    // capture unambiguous (and headless/automated Chrome never reports focus).
    chrome.tabs.captureVisibleTab(windowId, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        reply({ dataUrl: null, error: chrome.runtime.lastError.message });
        return;
      }
      if (!dataUrl) {
        reply({ dataUrl: null, error: 'EMPTY_CAPTURE' });
        return;
      }
      chrome.tabs.get(tabId, (currentTab) => {
        if (chrome.runtime.lastError || !currentTab) {
          reply({ dataUrl: null, error: 'TAB_GONE' });
          return;
        }
        if (pending.changed || !currentTab.active || currentTab.windowId !== windowId || currentTab.url !== capturedUrl) {
          reply({ dataUrl: null, error: 'PAGE_CHANGED' });
          return;
        }
        reply({ dataUrl, tabId });
      });
    });
  });

  return true; // keep the messaging channel open for the async reply
});
