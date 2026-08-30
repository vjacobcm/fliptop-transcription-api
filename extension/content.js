(() => {
  const HOST_ID = "ft-companion-host";

  const watch = {
    videoId: null,
    enabled: true,
    apiBase: FT.DEFAULTS.apiBase,
    showMentions: true,
    battle: null,
    segments: [],
    mentionsByIdx: new Map(),
    collapsed: false,
    lastCueIdx: null,
    lastMentionKey: "",
    overlayCss: "",
    raf: 0,
    looking: false,
    generation: 0,
  };

  function videoIdFromLocation() {
    const url = new URL(location.href);
    if (url.pathname !== "/watch") return null;
    return url.searchParams.get("v");
  }

  function playerEl() {
    return document.getElementById("movie_player");
  }

  function videoEl(player) {
    return player?.querySelector("video");
  }

  function api(path) {
    const base = watch.apiBase.replace(/\/$/, "");
    return chrome.runtime
      .sendMessage({ type: "api", url: `${base}${path}` })
      .then((response) => response || { ok: false, status: 0, error: "No response" })
      .catch((error) => ({ ok: false, status: 0, error: String(error) }));
  }

  function setWatchStatus(partial) {
    const payload = {
      videoId: watch.videoId,
      enabled: watch.enabled,
      ...partial,
    };
    chrome.storage.session.set({ watch: payload }).catch(() => {});
  }

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function mentionsFor(cue) {
    if (!cue || !watch.showMentions) return [];
    const parent = cue.parentIdx ?? cue.idx;
    const from = cue.charStart ?? 0;
    const to = cue.charEnd ?? Number.POSITIVE_INFINITY;
    return (watch.mentionsByIdx.get(parent) || []).filter(
      (mention) => mention.char_start < to && mention.char_end > from
    );
  }

  function uniqueMentions(mentions) {
    const seen = new Set();
    const unique = [];
    for (const mention of mentions) {
      const key = mention.entry?.id ?? mention.id;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(mention);
    }
    return unique;
  }

  function mentionCard(mention) {
    const card = document.createElement("article");
    card.className = "ft-card";
    card.dataset.kind = mention.entry.kind;

    const name = document.createElement("h2");
    name.className = "ft-card-name";
    name.textContent = mention.entry.name;

    const kind = document.createElement("p");
    kind.className = "ft-card-kind";
    kind.textContent = mention.entry.kind;

    card.append(name, kind);

    const gloss = mention.line_gloss || mention.entry.blurb;
    if (gloss) {
      const note = document.createElement("p");
      note.className = "ft-card-gloss";
      note.textContent = gloss;
      card.append(note);
    }
    return card;
  }

  function highlightLine(text, mentions, shift = 0) {
    if (!mentions.length) return escapeHtml(text);

    const spans = [...mentions]
      .filter((mention) => mention.char_end > mention.char_start)
      .sort((a, b) => a.char_start - b.char_start);

    let cursor = 0;
    let html = "";
    for (const mention of spans) {
      const start = Math.max(mention.char_start - shift, cursor);
      const end = Math.min(mention.char_end - shift, text.length);
      if (end <= start) continue;
      html += escapeHtml(text.slice(cursor, start));
      html += `<mark class="ft-ref" data-kind="${escapeHtml(mention.entry.kind)}">${escapeHtml(
        text.slice(start, end)
      )}</mark>`;
      cursor = end;
    }
    html += escapeHtml(text.slice(cursor));
    return html;
  }

  async function ensureRoot() {
    const player = playerEl();
    if (!player) return null;

    let host = player.querySelector(`#${HOST_ID}`);
    if (!host) {
      host = document.createElement("div");
      host.id = HOST_ID;
      const shadow = host.attachShadow({ mode: "open" });
      if (!watch.overlayCss) {
        const url = chrome.runtime.getURL("overlay.css");
        watch.overlayCss = await fetch(url).then((res) => res.text());
      }
      shadow.innerHTML = `<style>${watch.overlayCss}</style><div class="ft" id="root" hidden></div>`;
      player.appendChild(host);
    }
    return host.shadowRoot.getElementById("root");
  }

  function renderChrome(root) {
    if (root.dataset.ready === "1") return;
    const matchup = FT.parseMatchup(watch.battle?.title || "");
    root.innerHTML = `
      <aside class="ft-rail" hidden></aside>
      <div class="ft-captions">
        <p class="ft-line"></p>
        <button type="button" class="ft-pill" title="Hide or show companion lines">
          <span class="ft-dot"></span>
          <span class="ft-pill-label"></span>
        </button>
      </div>
    `;
    root.dataset.ready = "1";
    const pill = root.querySelector(".ft-pill");
    pill.querySelector(".ft-pill-label").textContent = matchup.label || "FlipTop";
    pill.addEventListener("click", () => {
      watch.collapsed = !watch.collapsed;
      root.classList.toggle("ft-collapsed", watch.collapsed);
    });
  }

  function paint(root, cue) {
    const line = root.querySelector(".ft-line");
    const rail = root.querySelector(".ft-rail");
    const mentions = mentionsFor(cue);
    const cards = uniqueMentions(mentions);
    const mentionKey = cards.map((mention) => mention.entry?.id ?? mention.id).join(",");

    const cueId = cue.id ?? cue.idx;
    if (cueId !== watch.lastCueIdx) {
      line.innerHTML = highlightLine(cue.text, mentions, cue.charStart ?? 0);
      watch.lastCueIdx = cueId;
    }

    if (mentionKey !== watch.lastMentionKey) {
      if (!cards.length) {
        rail.hidden = true;
        rail.replaceChildren();
      } else {
        rail.hidden = false;
        rail.replaceChildren(...cards.map(mentionCard));
      }
      root.classList.toggle("ft-has-rail", cards.length > 0);
      watch.lastMentionKey = mentionKey;
    }
  }

  function hideOverlay() {
    const host = playerEl()?.querySelector(`#${HOST_ID}`);
    const root = host?.shadowRoot?.getElementById("root");
    if (root) {
      root.hidden = true;
      root.dataset.ready = "";
      root.replaceChildren();
    }
    watch.lastCueIdx = null;
    watch.lastMentionKey = "";
    if (watch.raf) {
      cancelAnimationFrame(watch.raf);
      watch.raf = 0;
    }
  }

  function tick() {
    watch.raf = 0;
    const player = playerEl();
    const video = videoEl(player);
    const host = player?.querySelector(`#${HOST_ID}`);
    const root = host?.shadowRoot?.getElementById("root");
    if (!root || !video || !watch.segments.length) return;

    const width = player.clientWidth;
    host.style.fontSize = `${Math.max(15, Math.min(28, width / 34))}px`;

    const cue = FT.findCue(watch.segments, video.currentTime);
    if (!cue) {
      root.classList.add("ft-gap");
    } else {
      root.classList.remove("ft-gap");
      paint(root, cue);
    }

    watch.raf = requestAnimationFrame(tick);
  }

  async function startOverlay() {
    const root = await ensureRoot();
    if (!root || !watch.battle) return;
    renderChrome(root);
    root.hidden = false;
    root.classList.toggle("ft-collapsed", watch.collapsed);
    if (!watch.raf) watch.raf = requestAnimationFrame(tick);
  }

  async function loadBattle(videoId) {
    const generation = ++watch.generation;
    watch.looking = true;
    watch.battle = null;
    watch.segments = [];
    watch.mentionsByIdx = new Map();
    hideOverlay();
    setWatchStatus({ state: "loading", title: "" });

    const battleRes = await api(`/battles/${videoId}`);
    if (generation !== watch.generation) return;
    if (!battleRes.ok && battleRes.status === 0) {
      setWatchStatus({ state: "offline", title: "" });
      watch.looking = false;
      return;
    }
    if (!battleRes.ok) {
      setWatchStatus({
        state: battleRes.status === 404 ? "missing" : "error",
        title: "",
        detail: battleRes.body?.detail || "",
      });
      watch.looking = false;
      return;
    }

    const battle = battleRes.body;
    if (battle.status !== "ready" || !battle.segment_count) {
      setWatchStatus({
        state: "not_ready",
        title: battle.title,
        status: battle.status,
        detail: battle.error || "",
      });
      watch.looking = false;
      return;
    }

    const [transcriptRes, mentionsRes] = await Promise.all([
      api(`/battles/${videoId}/transcript?format=json`),
      api(`/battles/${videoId}/mentions`),
    ]);
    if (generation !== watch.generation) return;

    if (!transcriptRes.ok) {
      setWatchStatus({
        state: "error",
        title: battle.title,
        detail: transcriptRes.body?.detail || "Transcript failed",
      });
      watch.looking = false;
      return;
    }

    const mentions = mentionsRes.ok ? mentionsRes.body.mentions || [] : [];
    const byIdx = new Map();
    for (const mention of mentions) {
      if (mention.status === "rejected") continue;
      const list = byIdx.get(mention.segment_idx) || [];
      list.push(mention);
      byIdx.set(mention.segment_idx, list);
    }

    watch.battle = battle;
    watch.segments = FT.splitCues(transcriptRes.body.segments || []);
    watch.mentionsByIdx = byIdx;
    watch.looking = false;

    setWatchStatus({
      state: "ready",
      title: battle.title,
      source: battle.source,
      segments: watch.segments.length,
      mentions: mentions.filter((mention) => mention.status !== "rejected").length,
    });

    await startOverlay();
  }

  function clearWatch() {
    watch.generation += 1;
    watch.videoId = null;
    watch.looking = false;
    watch.battle = null;
    watch.segments = [];
    watch.mentionsByIdx = new Map();
    hideOverlay();
    setWatchStatus({ state: "idle", title: "", videoId: null });
  }

  async function syncPage() {
    const settings = await FT.settings();
    watch.enabled = settings.enabled;
    watch.apiBase = settings.apiBase;
    watch.showMentions = settings.showMentions;

    const videoId = videoIdFromLocation();
    if (!settings.enabled) {
      watch.videoId = videoId;
      hideOverlay();
      setWatchStatus({ state: "disabled", title: "", videoId });
      return;
    }

    if (!videoId) {
      clearWatch();
      return;
    }

    if (videoId === watch.videoId && (watch.battle || watch.looking)) {
      if (watch.battle) await startOverlay();
      return;
    }

    watch.videoId = videoId;
    await loadBattle(videoId);
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.enabled || changes.apiBase || changes.showMentions) {
      watch.videoId = null;
      syncPage();
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "status") {
      chrome.storage.session.get({ watch: null }).then((data) => sendResponse(data.watch));
      return true;
    }
    return undefined;
  });

  document.addEventListener("yt-navigate-finish", () => syncPage());
  window.addEventListener("yt-page-data-updated", () => syncPage());

  setInterval(() => {
    if (watch.battle && watch.enabled && !playerEl()?.querySelector(`#${HOST_ID}`)) {
      startOverlay();
    }
  }, 1000);

  syncPage();
})();
