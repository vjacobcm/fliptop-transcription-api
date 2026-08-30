const enabledEl = document.getElementById("enabled");
const mentionsEl = document.getElementById("showMentions");
const apiEl = document.getElementById("apiBase");
const statusEl = document.getElementById("status");
const stateEl = document.getElementById("state");
const titleEl = document.getElementById("title");
const metaEl = document.getElementById("meta");

const COPY = {
  idle: "Open a YouTube watch page.",
  disabled: "Companion is off.",
  loading: "Looking up this battle…",
  ready: "Companion is on this battle.",
  missing: "This video is not in the catalogue.",
  not_ready: "This battle is ingested but not ready yet.",
  offline: "Can't reach the API. Is uvicorn running?",
  error: "The API returned an error.",
};

function paintStatus(watch) {
  const state = watch?.state || "idle";
  statusEl.dataset.state = state;
  stateEl.textContent = COPY[state] || COPY.idle;

  if (watch?.title) {
    titleEl.hidden = false;
    titleEl.textContent = watch.title;
  } else {
    titleEl.hidden = true;
  }

  const bits = [];
  if (watch?.segments) bits.push(`${watch.segments} lines`);
  if (watch?.source) bits.push(watch.source.replace(/_/g, " "));
  if (watch?.mentions) bits.push(`${watch.mentions} mentions`);
  if (watch?.detail && (state === "error" || state === "not_ready")) {
    bits.push(watch.detail);
  }
  if (bits.length) {
    metaEl.hidden = false;
    metaEl.textContent = bits.join(" · ");
  } else {
    metaEl.hidden = true;
  }
}

async function load() {
  const settings = await FT.settings();
  enabledEl.checked = settings.enabled;
  mentionsEl.checked = settings.showMentions;
  apiEl.value = settings.apiBase;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) {
    try {
      const watch = await chrome.tabs.sendMessage(tab.id, { type: "status" });
      paintStatus(watch);
      return;
    } catch {
      // content script is not on this tab
    }
  }

  const session = await chrome.storage.session.get({ watch: null });
  paintStatus(session.watch);
}

enabledEl.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabledEl.checked });
});

mentionsEl.addEventListener("change", () => {
  chrome.storage.local.set({ showMentions: mentionsEl.checked });
});

apiEl.addEventListener("change", () => {
  const value = apiEl.value.trim() || FT.DEFAULTS.apiBase;
  apiEl.value = value;
  chrome.storage.local.set({ apiBase: value.replace(/\/$/, "") });
});

load();
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.watch) paintStatus(changes.watch.newValue);
});
