// PhishLens Edge Guard - background service worker (MV3)
// Sole job: capture the visible tab as PNG on demand from content scripts.
// Chrome extensions cannot screenshot from content scripts directly,
// so content.js delegates to chrome.tabs.captureVisibleTab here.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'CAPTURE') {
    const windowId = sender.tab ? sender.tab.windowId : undefined;
    chrome.tabs.captureVisibleTab(windowId, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        sendResponse({ dataUrl: null, error: chrome.runtime.lastError.message });
        return;
      }
      sendResponse({ dataUrl });
    });
    return true; // keep the messaging channel open for the async reply
  }
  return false;
});