"""Emcees to collect, plus spelling variants and alter egos in titles."""

from __future__ import annotations

import re

# Canonical name -> aliases as they appear on FlipTop thumbnails/titles.
# Alter egos (Sinagtala, Freak Sanchez) are the same person, so they fold in.
EMCEES: dict[str, tuple[str, ...]] = {
    "GL": ("GL", "G.L.", "Sinagtala", "Sinag Tala", "Sinag-Tala"),
    "BLKD": ("BLKD", "BLK D", "Blk D", "BLK-D"),
    "Loonie": ("Loonie",),
    "Tipsy D": (
        "Tipsy D",
        "TipsyD",
        "Tipsy-D",
        "Freak Sanchez",
        "FreakSanchez",
        "Freak-Sanchez",
    ),
}

# Compilations, press, and coming-soon uploads that mention a name but are not the battle.
_SKIP_RE = re.compile(
    r"\b("
    r"trailer|teaser|press\s*con(ference)?|interview|vlog|"
    r"abangan|coming\s+soon|flyer|poster|promo|"
    r"behind\s+the\s+scenes|reaction|audio|music\s*video|"
    r"best\s+of|top\s+\d+|compilation|highlights?"
    r")\b",
    re.I,
)

# "vs", "vs.", "v.s.", "v/s", "versus" with space on both sides.
_BATTLE_RE = re.compile(r"\s+(?:vs\.?|v\.s\.?|v/s|versus)\s+", re.I)


def headline(title: str) -> str:
    """The card itself, dropping coming-soon text after a pipe."""
    return title.split("|", 1)[0].strip()


def normalize(name: str) -> str:
    """Collapse a name to letters/digits so 'Tipsy D' and 'Tipsy-D' match."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def alias_index() -> dict[str, str]:
    """Normalized alias -> canonical emcee name."""
    mapping: dict[str, str] = {}
    for canonical, aliases in EMCEES.items():
        mapping[normalize(canonical)] = canonical
        for alias in aliases:
            mapping[normalize(alias)] = canonical
    return mapping


def is_battle_title(title: str) -> bool:
    """True only for vs-cards, not interviews, flyers, or recaps."""
    card = headline(title)
    if not card or not _BATTLE_RE.search(card):
        return False
    return _SKIP_RE.search(card) is None


def bounded_match(title: str, alias: str) -> bool:
    """True when `alias` appears in `title` as its own token, not inside another word.

    Needed so 'GL' does not match 'ANGLE', while still catching titles the
    matchup parser fails to split (event name glued onto an emcee).
    """
    haystack = title.lower()
    needle = alias.lower()
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        end = index + len(needle)
        left_ok = index == 0 or not haystack[index - 1].isalnum()
        right_ok = end == len(haystack) or not haystack[end].isalnum()
        if left_ok and right_ok:
            return True
        start = index + 1
