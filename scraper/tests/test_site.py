"""Fixture tests for the official-site scraper and glossary import."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRAPER = ROOT / "scraper"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRAPER))
sys.path.insert(0, str(ROOT))

from fliptop_scraper.normalize import (  # noqa: E402
    compile_entries,
    keep_group,
    person_blurb,
    split_parts,
    split_reppin,
)
from fliptop_scraper.site import (  # noqa: E402
    crawl_emcees,
    parse_emcee_index,
    parse_emcee_profile,
)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParseTests(unittest.TestCase):
    def test_index_cards_and_last_page(self) -> None:
        cards, last_page = parse_emcee_index(_html("emcees_index.html"))
        slugs = [card["slug"] for card in cards]
        self.assertEqual(slugs, ["gl", "aklas"])
        self.assertEqual(cards[0]["name"], "GL")
        self.assertEqual(last_page, 9)

    def test_gl_profile(self) -> None:
        profile = parse_emcee_profile(
            _html("emcee_gl.html"),
            slug="gl",
            url="https://www.fliptop.com.ph/emcees/gl",
        )
        self.assertEqual(profile["name"], "GL")
        self.assertEqual(profile["hometown"], "Palo, Leyte")
        self.assertEqual(
            profile["reppin_raw"], "Lorem Ipsum, Alas Kwatro, Passion's Fruit"
        )
        self.assertEqual(profile["division"], "Visayas")
        self.assertEqual(profile["year_joined"], 2019)
        self.assertEqual(profile["titles"], ["Isabuhay 2024 Champion"])
        self.assertIn("underground battle leagues", profile["bio"])

    def test_aklas_profile(self) -> None:
        profile = parse_emcee_profile(
            _html("emcee_aklas.html"),
            slug="aklas",
            url="https://www.fliptop.com.ph/emcees/aklas",
        )
        self.assertEqual(profile["name"], "Aklas")
        self.assertEqual(profile["hometown"], "Las Pinas, Iloilo")
        self.assertEqual(profile["reppin_raw"], "Mulat Krew, Batch 1, S.O.S")
        self.assertEqual(profile["division"], "Metro Manila")
        self.assertEqual(profile["year_joined"], 2010)

    def test_crawl_respects_limit(self) -> None:
        index = _html("emcees_index.html")
        pages = {
            "https://www.fliptop.com.ph/emcees": index,
            "https://www.fliptop.com.ph/emcees/gl": _html("emcee_gl.html"),
            "https://www.fliptop.com.ph/emcees/aklas": _html("emcee_aklas.html"),
        }

        def fetch(url: str) -> str:
            if url in pages:
                return pages[url]
            if "page=" in url:
                return "<html></html>"
            raise AssertionError(url)

        profiles = crawl_emcees(fetch, limit=1)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["slug"], "gl")


class NormalizeTests(unittest.TestCase):
    def test_skip_placeholder_and_batch(self) -> None:
        self.assertFalse(keep_group("Lorem Ipsum"))
        self.assertFalse(keep_group("Batch 1"))
        self.assertFalse(keep_group("Mindanao Batch 2"))
        self.assertFalse(keep_group("N/A"))
        self.assertFalse(keep_group("Myself"))
        self.assertFalse(keep_group("Bars"))
        self.assertTrue(keep_group("Mulat Krew"))
        self.assertEqual(
            split_reppin("Mulat Krew, Batch 1, S.O.S, Lorem Ipsum"),
            ["Mulat Krew", "S.O.S"],
        )

    def test_hometown_split_and_piñas_alias(self) -> None:
        self.assertEqual(split_parts("Las Pinas, Iloilo"), ["Las Pinas", "Iloilo"])
        self.assertEqual(split_parts("Pasig/Cebu"), ["Pasig", "Cebu"])
        self.assertEqual(split_parts("Cavite & Quezon City"), ["Cavite", "Quezon City"])
        self.assertEqual(split_parts("Laguna | Las Pinas City"), ["Laguna", "Las Pinas City"])
        self.assertEqual(split_parts("2110"), [])
        prepared, entries = compile_entries(
            [
                {
                    "slug": "aklas",
                    "name": "Aklas",
                    "hometown": "Las Pinas, Iloilo",
                    "reppin_raw": "Mulat Krew, Batch 1, S.O.S",
                    "division": "Metro Manila",
                    "titles": ["Isabuhay 2013 Champion"],
                }
            ]
        )
        self.assertEqual(prepared[0]["hometown_parts"], ["Las Pinas", "Iloilo"])
        self.assertEqual(prepared[0]["reppin"], ["Mulat Krew", "S.O.S"])
        places = {row["name"]: row for row in entries if row["kind"] == "place"}
        self.assertIn("Las Piñas", places["Las Pinas"]["aliases"])
        self.assertIn("Metro Manila", places)

    def test_group_member_aggregation(self) -> None:
        _, entries = compile_entries(
            [
                {
                    "slug": "aklas",
                    "name": "Aklas",
                    "hometown": "Las Pinas",
                    "reppin_raw": "Mulat Krew",
                    "division": "Metro Manila",
                },
                {
                    "slug": "asser",
                    "name": "Asser",
                    "hometown": "Manila",
                    "reppin_raw": "Mulat Krew",
                    "division": "Metro Manila",
                },
            ]
        )
        groups = [row for row in entries if row["kind"] == "group"]
        self.assertEqual(len(groups), 1)
        self.assertIn("Aklas", groups[0]["blurb"])
        self.assertIn("Asser", groups[0]["blurb"])

    def test_person_alias_beats_place(self) -> None:
        _, entries = compile_entries(
            [
                {
                    "slug": "abra",
                    "name": "Abra",
                    "hometown": "Abra",
                    "reppin_raw": "",
                    "division": "",
                }
            ]
        )
        kinds = {row["name"]: row["kind"] for row in entries}
        self.assertEqual(kinds["Abra"], "person")
        self.assertNotIn("place", {row["kind"] for row in entries if row["name"] == "Abra"})

    def test_person_blurb(self) -> None:
        text = person_blurb(
            {
                "hometown_parts": ["Las Pinas", "Iloilo"],
                "division": "Metro Manila",
                "reppin": ["Mulat Krew", "S.O.S"],
                "titles": ["Isabuhay 2013 Champion"],
            }
        )
        self.assertIn("from Las Pinas and Iloilo (Metro Manila)", text)
        self.assertIn("Reps Mulat Krew and S.O.S", text)
        self.assertIn("Isabuhay 2013 Champion", text)


class SeedTests(unittest.TestCase):
    def test_seed_glossary_from_snapshot(self) -> None:
        from sqlmodel import Session, SQLModel, create_engine, select

        from app.models import Entry
        from app.services.glossary import seed_glossary

        _, entries = compile_entries(
            [
                {
                    "slug": "aklas",
                    "name": "Aklas",
                    "hometown": "Las Pinas, Iloilo",
                    "reppin_raw": "Mulat Krew, Batch 1, S.O.S",
                    "division": "Metro Manila",
                    "titles": ["Isabuhay 2013 Champion"],
                }
            ]
        )
        payload = {"entries": entries}
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", encoding="utf-8", delete=False
        ) as handle:
            json.dump(payload, handle)
            path = Path(handle.name)
        try:
            with Session(engine) as session:
                added = seed_glossary(session, site_path=path)
                rows = list(session.exec(select(Entry)))
            by_slug = {row.slug: row for row in rows}
            self.assertGreaterEqual(added, 3)
            self.assertEqual(by_slug["aklas"].kind, "person")
            self.assertIn("Mulat Krew", by_slug["aklas"].blurb)
            self.assertEqual(by_slug["mulat-krew"].kind, "group")
            self.assertIn("Aklas", by_slug["mulat-krew"].blurb)
            self.assertEqual(by_slug["las-pinas"].kind, "place")
            self.assertIn("Aklas", by_slug["iloilo"].blurb)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
