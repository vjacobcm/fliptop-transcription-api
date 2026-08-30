importScripts("shared.js");

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "api") return undefined;

  fetch(message.url, { headers: { Accept: "application/json" } })
    .then(async (response) => {
      const text = await response.text();
      let body = null;
      if (text) {
        try {
          body = JSON.parse(text);
        } catch {
          body = text;
        }
      }
      sendResponse({ ok: response.ok, status: response.status, body });
    })
    .catch((error) => {
      sendResponse({ ok: false, status: 0, error: String(error) });
    });

  return true;
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "toggle-companion") return;
  const settings = await FT.settings();
  await chrome.storage.local.set({ enabled: !settings.enabled });
});

async function refreshBadge() {
  const settings = await FT.settings();
  const session = await chrome.storage.session.get({ watch: null });
  const watch = session.watch;

  if (!settings.enabled) {
    await chrome.action.setBadgeText({ text: "off" });
    await chrome.action.setBadgeBackgroundColor({ color: "#555555" });
    return;
  }

  if (watch?.state === "ready") {
    await chrome.action.setBadgeText({ text: "on" });
    await chrome.action.setBadgeBackgroundColor({ color: "#c41e3a" });
    return;
  }

  await chrome.action.setBadgeText({ text: "" });
}

chrome.storage.onChanged.addListener(refreshBadge);
refreshBadge();
