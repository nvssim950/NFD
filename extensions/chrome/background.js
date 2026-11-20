/**
 * NassimDL Chrome Extension — Background Service Worker
 * Automatically intercepts Chrome downloads and redirects them to NassimDL.
 */

const NASSIMDL_API = "http://127.0.0.1:8000/api/downloads";

// Initialize Context Menu on install
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "nassimdl_download",
    title: "Download with NassimDL",
    contexts: ["link", "image", "video", "audio"]
  });
  // Default interception to enabled
  chrome.storage.local.set({ interceptionEnabled: true });
});

// Handle Context Menu clicks
chrome.contextMenus.onClicked.addListener((info) => {
  const targetUrl = info.linkUrl || info.srcUrl;
  if (targetUrl) {
    sendToNassimDL(targetUrl);
  }
});

// Intercept downloads initiated by Chrome
chrome.downloads.onCreated.addListener(async (downloadItem) => {
  const { interceptionEnabled = true } = await chrome.storage.local.get("interceptionEnabled");
  if (!interceptionEnabled) return;

  const url = downloadItem.url;
  // Ignore local blobs, data URIs, or internal chrome:// URLs
  if (!url || url.startsWith("blob:") || url.startsWith("data:") || url.startsWith("chrome://")) {
    return;
  }

  // Cancel Chrome's slow built-in download
  chrome.downloads.cancel(downloadItem.id, () => {
    chrome.downloads.erase({ id: downloadItem.id });
  });

  // Forward to NassimDL
  sendToNassimDL(url, downloadItem.filename);
});

/**
 * Sends a URL to NassimDL local REST API.
 */
async function sendToNassimDL(url, filename = null) {
  try {
    const response = await fetch(NASSIMDL_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: url,
        filename: filename || null
      })
    });

    if (response.ok) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icon.png",
        title: "NassimDL Accelerator",
        message: "Download transferred to NassimDL! Running parallel streams 🚀"
      });
    } else {
      console.warn("NassimDL server responded with status:", response.status);
    }
  } catch (error) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon.png",
      title: "NassimDL is Not Running",
      message: "Please launch NassimDL on your PC (python -m nassimdl app) to download."
    });
  }
}
