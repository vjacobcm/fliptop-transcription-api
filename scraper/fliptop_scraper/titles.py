"""Parse the matchup out of a FlipTop video title.

Titles are consistent enough to mine: `FlipTop - Sinagtala vs 1ce Water` or
`FlipTop - Loonie vs Zaito @ Isabuhay 2018 Semifinals`, with tag teams joined
by `&`.
"""

import re
from dataclasses import dataclass, field

_PREFIX_RE = re.compile(r"^\s*flip\s*top\b[^A-Za-z0-9]*(battle league)?[\s\-:|]*", re.I)
_NOISE_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]\s*$")
_EVENT_RE = re.compile(r"\s+@\s*(?P<event>.+)$")
_VS_RE = re.compile(r"\s+(?:vs\.?|v\.s\.?|v/s|versus)\s+", re.I)
_TEAM_RE = re.compile(r"(?:\s+(?:&|\+|and)\s+|/)", re.I)


@dataclass
class Matchup:
    sides: list[list[str]] = field(default_factory=list)
    event: str | None = None

    @property
    def emcees(self) -> list[str]:
        return [name for side in self.sides for name in side]

    @property
    def is_complete(self) -> bool:
        return len(self.sides) >= 2 and all(self.sides)

    def describe(self) -> str:
        return " vs ".join(" & ".join(side) for side in self.sides)


def _clean(text: str) -> str:
    return " ".join(text.replace("_", " ").split()).strip(" -–—:|")


def parse_matchup(title: str) -> Matchup:
    """Best-effort matchup extraction. Returns empty sides when unrecognised."""
    if not title:
        return Matchup()

    # Promos glue a coming-soon line after '|'; only the card itself is the battle.
    title = title.split("|", 1)[0]
    body = _NOISE_RE.sub("", title).strip()
    body = _PREFIX_RE.sub("", body)

    event = None
    match = _EVENT_RE.search(body)
    if match:
        event = _clean(match.group("event")) or None
        body = body[: match.start()]

    parts = _VS_RE.split(_clean(body))
    if len(parts) < 2:
        return Matchup(event=event)

    sides = []
    for part in parts:
        names = [_clean(name) for name in _TEAM_RE.split(part)]
        sides.append([name for name in names if name])

    if not all(sides):
        return Matchup(event=event)

    return Matchup(sides=sides, event=event)
