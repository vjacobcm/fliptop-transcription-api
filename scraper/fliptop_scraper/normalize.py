"""Turn scraped emcee profiles into glossary rows."""

from __future__ import annotations

import logging
import re
logger = logging.getLogger(__name__)

KIND_PERSON = "person"
KIND_GROUP = "group"
KIND_PLACE = "place"

_SPLIT_RE = re.compile(r"[,/|&]")
_BATCH_RE = re.compile(r"\bbatch\s*\d+\b", re.I)
_SKIP_GROUPS = frozenset(
    {
        "lorem ipsum",
        "n/a",
        "na",
        "none",
        "none.",
        "-",
        "--",
        "tbd",
        "tba",
        "independent",
        "unknown",
        "myself",
        "himself",
        "herself",
        # Common words that show up as Reppin filler and would light up every battle.
        "bars",
        "speech",
        "copyright",
    }
)
_MEMBER_CAP = 8


def tidy(text: str) -> str:
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(text.split()).strip()


def _norm(text: str) -> str:
    return tidy(text).lower()


def keep_group(token: str) -> bool:
    folded = _norm(token)
    if len(folded) < 2:
        return False
    if folded in _SKIP_GROUPS:
        return False
    if _BATCH_RE.search(folded):
        return False
    return True


def split_parts(raw: str) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in _SPLIT_RE.split(raw or ""):
        token = tidy(chunk)
        key = _norm(token)
        if len(token) < 2 or token.isdigit() or key in seen:
            continue
        seen.add(key)
        parts.append(token)
    return parts


def split_reppin(raw: str) -> list[str]:
    return [part for part in split_parts(raw) if keep_group(part)]


def accent_aliases(name: str) -> list[str]:
    aliases = [name]
    swapped = name.replace("Pinas", "Piñas").replace("pinas", "piñas")
    if swapped != name:
        aliases.append(swapped)
    return aliases


def group_aliases(name: str) -> list[str]:
    aliases = [name]
    compact = re.sub(r"[.\s]", "", name)
    if compact != name and len(compact) >= 3:
        aliases.append(compact)
    return aliases


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _capped_members(members: list[str]) -> str:
    names = sorted(members, key=str.lower)
    if len(names) > _MEMBER_CAP:
        return f"{_join_names(names[:_MEMBER_CAP])}, and others"
    return _join_names(names)


def prepare_emcee(raw: dict) -> dict:
    hometown = tidy(raw.get("hometown") or "")
    if raw.get("reppin_raw"):
        reppin = split_reppin(raw.get("reppin_raw") or "")
    else:
        reppin = [part for part in (raw.get("reppin") or []) if keep_group(part)]
    return {
        "slug": raw.get("slug") or "",
        "name": tidy(raw.get("name") or raw.get("slug") or ""),
        "hometown": hometown,
        "hometown_parts": split_parts(hometown),
        "reppin": reppin,
        "division": tidy(raw.get("division") or ""),
        "year_joined": raw.get("year_joined"),
        "titles": [tidy(title) for title in (raw.get("titles") or []) if tidy(title)],
        "bio": tidy(raw.get("bio") or ""),
        "url": raw.get("url") or "",
        "reppin_raw": tidy(raw.get("reppin_raw") or ""),
    }


def person_blurb(emcee: dict) -> str:
    parts: list[str] = []
    origin = list(emcee.get("hometown_parts") or [])
    division = emcee.get("division") or ""
    if origin and division:
        parts.append(f"FlipTop emcee from {_join_names(origin)} ({division}).")
    elif origin:
        parts.append(f"FlipTop emcee from {_join_names(origin)}.")
    elif division:
        parts.append(f"FlipTop emcee from {division}.")
    else:
        parts.append("FlipTop emcee.")
    reppin = list(emcee.get("reppin") or [])
    if reppin:
        parts.append(f"Reps {_join_names(reppin)}.")
    titles = list(emcee.get("titles") or [])
    if titles:
        title = titles[0].rstrip(".")
        parts.append(f"{title}.")
    return " ".join(parts)


def group_blurb(name: str, members: list[str]) -> str:
    listed = _capped_members(members)
    if listed:
        return f"FlipTop crew. Members include {listed}."
    return "FlipTop crew."


def place_blurb(name: str, members: list[str], *, division: bool) -> str:
    listed = _capped_members(members)
    if division:
        if listed:
            return f"FlipTop division. Emcees include {listed}."
        return "FlipTop division."
    if listed:
        return f"Hometown of {listed}."
    return f"Place referenced in FlipTop battles: {name}."


class _AliasClaimer:
    def __init__(self) -> None:
        self.claimed: set[str] = set()

    def take(self, name: str, aliases: list[str]) -> list[str] | None:
        usable: list[str] = []
        seen: set[str] = set()
        for label in (name, *aliases):
            token = tidy(label)
            key = _norm(token)
            if not token or key in seen:
                continue
            seen.add(key)
            if key in self.claimed:
                if token == name or _norm(name) == key:
                    logger.info("Skipping %r; alias already claimed", name)
                    return None
                logger.info("Skipping alias %r for %r; already claimed", token, name)
                continue
            usable.append(token)
        if not usable:
            return None
        for label in usable:
            self.claimed.add(_norm(label))
        return usable


def compile_entries(emcees: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build prepared emcee records and glossary entries (people, groups, places)."""
    prepared = [prepare_emcee(raw) for raw in emcees]
    claimer = _AliasClaimer()
    entries: list[dict] = []

    groups: dict[str, dict] = {}
    hometowns: dict[str, dict] = {}
    divisions: dict[str, dict] = {}

    for emcee in prepared:
        if not emcee["name"]:
            continue
        aliases = claimer.take(emcee["name"], [emcee["name"]])
        if aliases is None:
            continue
        entries.append(
            {
                "name": emcee["name"],
                "kind": KIND_PERSON,
                "aliases": aliases,
                "blurb": person_blurb(emcee),
            }
        )
        for crew in emcee["reppin"]:
            key = _norm(crew)
            bucket = groups.setdefault(key, {"name": crew, "members": []})
            bucket["members"].append(emcee["name"])
        for place in emcee["hometown_parts"]:
            key = _norm(place)
            bucket = hometowns.setdefault(key, {"name": place, "members": []})
            bucket["members"].append(emcee["name"])
        division = emcee["division"]
        if division:
            key = _norm(division)
            bucket = divisions.setdefault(key, {"name": division, "members": []})
            bucket["members"].append(emcee["name"])

    for crew in groups.values():
        aliases = claimer.take(crew["name"], group_aliases(crew["name"]))
        if aliases is None:
            continue
        entries.append(
            {
                "name": crew["name"],
                "kind": KIND_GROUP,
                "aliases": aliases,
                "blurb": group_blurb(crew["name"], crew["members"]),
            }
        )

    for place in hometowns.values():
        aliases = claimer.take(place["name"], accent_aliases(place["name"]))
        if aliases is None:
            continue
        entries.append(
            {
                "name": place["name"],
                "kind": KIND_PLACE,
                "aliases": aliases,
                "blurb": place_blurb(place["name"], place["members"], division=False),
            }
        )

    for place in divisions.values():
        if _norm(place["name"]) in hometowns:
            continue
        aliases = claimer.take(place["name"], accent_aliases(place["name"]))
        if aliases is None:
            continue
        entries.append(
            {
                "name": place["name"],
                "kind": KIND_PLACE,
                "aliases": aliases,
                "blurb": place_blurb(place["name"], place["members"], division=True),
            }
        )

    return prepared, entries
