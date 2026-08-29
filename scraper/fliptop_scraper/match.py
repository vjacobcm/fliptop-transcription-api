"""Decide which of the watched emcees appear in a video title."""

from __future__ import annotations

from fliptop_scraper.emcees import (
    EMCEES,
    alias_index,
    bounded_match,
    headline,
    is_battle_title,
    normalize,
)
from fliptop_scraper.titles import parse_matchup

_ALIASES = alias_index()


def matched_emcees(title: str) -> list[str]:
    """Canonical emcee names that this battle involves, in EMCEES order.

    Alter egos (Sinagtala, Freak Sanchez) resolve to GL and Tipsy D.
    Non-vs uploads never match.
    """
    card = headline(title)
    if not is_battle_title(card):
        return []

    found: set[str] = set()
    matchup = parse_matchup(card)
    for name in matchup.emcees:
        canonical = _ALIASES.get(normalize(name))
        if canonical:
            found.add(canonical)

    if not found:
        for canonical, aliases in EMCEES.items():
            if any(bounded_match(card, alias) for alias in aliases):
                found.add(canonical)

    return [name for name in EMCEES if name in found]
