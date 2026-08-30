const FT = {
  DEFAULTS: {
    enabled: true,
    apiBase: "http://127.0.0.1:8000",
    showMentions: true,
  },

  async settings() {
    const stored = await chrome.storage.local.get(FT.DEFAULTS);
    return { ...FT.DEFAULTS, ...stored };
  },

  parseMatchup(title) {
    if (!title) return { label: "", event: "" };
    let body = title.replace(/\s*[\(\[][^\)\]]*[\)\]]\s*$/, "").trim();
    body = body.replace(/^\s*flip\s*top\b[^A-Za-z0-9]*(battle league)?[\s\-:|]*/i, "");
    let event = "";
    const at = body.match(/\s+@\s*(.+)$/);
    if (at) {
      event = at[1].trim();
      body = body.slice(0, at.index);
    }
    const parts = body.split(/\s+(?:vs\.?|versus)\s+/i);
    if (parts.length !== 2) return { label: body.trim(), event };
    return { label: parts.map((side) => side.trim()).join(" vs "), event };
  },

  findCue(segments, time) {
    let lo = 0;
    let hi = segments.length - 1;
    let idx = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].start <= time) {
        idx = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    if (idx < 0) return null;
    const cue = segments[idx];
    return time < cue.end ? cue : null;
  },

  // Whisper often dumps a whole 16-bar stretch into one segment. The overlay
  // chops those for display so the next punchline is not already on screen.
  MAX_CUE_WORDS: 10,

  splitCues(segments, maxWords = FT.MAX_CUE_WORDS) {
    const cues = [];
    for (const segment of segments || []) {
      const text = segment.text || "";
      const pieces = FT._cuePieces(text, maxWords);
      const weights = pieces.map((piece) => Math.max(piece.text.length, 1));
      const total = weights.reduce((sum, weight) => sum + weight, 0);
      const duration = Math.max(segment.end - segment.start, 0);
      let cursor = segment.start;

      pieces.forEach((piece, index) => {
        const slice = duration * (weights[index] / total);
        const start = cursor;
        const end = index === pieces.length - 1 ? segment.end : cursor + slice;
        cursor = end;
        cues.push({
          id: `${segment.idx}:${piece.charStart}`,
          idx: segment.idx,
          parentIdx: segment.idx,
          start,
          end,
          text: piece.text,
          charStart: piece.charStart,
          charEnd: piece.charEnd,
        });
      });
    }
    return cues;
  },

  _cuePieces(text, maxWords) {
    const words = [];
    const token = /\S+/g;
    let match;
    while ((match = token.exec(text))) {
      words.push({
        start: match.index,
        end: match.index + match[0].length,
        text: match[0],
      });
    }

    if (words.length <= maxWords) {
      return [{ charStart: 0, charEnd: text.length, text }];
    }

    const pieces = [];
    let index = 0;
    while (index < words.length) {
      const hard = Math.min(index + maxWords, words.length);
      let cut = hard;
      const earliest = index + Math.max(2, Math.ceil(maxWords * 0.45));
      const latest = Math.min(index + maxWords + 3, words.length);

      for (let look = earliest; look <= latest; look += 1) {
        if (/[.!?…]$/.test(words[look - 1].text)) {
          cut = look;
          break;
        }
      }
      if (cut === hard) {
        for (let look = latest; look >= earliest; look -= 1) {
          if (/[,;:—–]$/.test(words[look - 1].text)) {
            cut = look;
            break;
          }
        }
      }

      const charStart = words[index].start;
      const charEnd = words[cut - 1].end;
      pieces.push({
        charStart,
        charEnd,
        text: text.slice(charStart, charEnd),
      });
      index = cut;
    }
    return pieces;
  },
};
