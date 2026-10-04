"""Fixture tests for the Battle BARS PH transcript scraper. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRAPER = ROOT / "scraper"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRAPER))

from fliptop_scraper.transcripts import (  # noqa: E402
    battle_url,
    clean_lines,
    crawl_battles,
    is_filler,
    parse_battle,
    parse_sitemap,
    parse_timestamp,
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TimestampTests(unittest.TestCase):
    def test_srt_form(self) -> None:
        self.assertEqual(parse_timestamp("00:01:51,879"), 111.879)

    def test_hours_and_dot_separator(self) -> None:
        self.assertEqual(parse_timestamp("01:02:03.040"), 3723.04)

    def test_zero(self) -> None:
        self.assertEqual(parse_timestamp("00:00:00,000"), 0.0)

    def test_short_form_without_hours(self) -> None:
        self.assertEqual(parse_timestamp("02:30"), 150.0)

    def test_rejects_junk(self) -> None:
        for value in ("", "later", "1:2:3:4"):
            with self.assertRaises(ValueError):
                parse_timestamp(value)


class SitemapTests(unittest.TestCase):
    def test_keeps_battle_ids_only(self) -> None:
        xml = """<?xml version="1.0"?><urlset>
          <url><loc>https://battlebarsph.com/</loc></url>
          <url><loc>https://battlebarsph.com/emcee/gl</loc></url>
          <url><loc>https://battlebarsph.com/battles/dQw4w9WgXcQ</loc></url>
          <url><loc>https://battlebarsph.com/battles/-04s-hMjTgI</loc></url>
          <url><loc>https://battlebarsph.com/battles/dQw4w9WgXcQ</loc></url>
        </urlset>"""
        self.assertEqual(parse_sitemap(xml), ["dQw4w9WgXcQ", "-04s-hMjTgI"])

    def test_empty(self) -> None:
        self.assertEqual(parse_sitemap(""), [])


class FillerTests(unittest.TestCase):
    def test_whisper_stock_phrases(self) -> None:
        for text in (
            "Outro",
            "Thank you for watching!",
            "🎵 Music 🎵",
            "🎵 Intro Music 🎵",
            "  ",
        ):
            self.assertTrue(is_filler(text), text)

    def test_keeps_sung_lines(self) -> None:
        """Note symbols also wrap lines an emcee sang, which are real content."""
        for text in ("🎵 a line that was sung 🎵", "♪ another sung line ♪"):
            self.assertFalse(is_filler(text), text)

    def test_keeps_real_lines(self) -> None:
        for text in ("an opening line", "Outro rhymes with no one", "yo!", "diba?"):
            self.assertFalse(is_filler(text), text)

    def test_collapses_consecutive_repeats(self) -> None:
        rows = [
            {"startTime": "00:00:01,000", "endTime": "00:00:02,000", "text": "same"},
            {"startTime": "00:00:02,000", "endTime": "00:00:04,000", "text": "Same"},
            {"startTime": "00:00:05,000", "endTime": "00:00:06,000", "text": "other"},
        ]
        lines = clean_lines(rows)
        self.assertEqual([line["text"] for line in lines], ["same", "other"])
        # The repeat extends the line it duplicates rather than vanishing.
        self.assertEqual(lines[0]["end"], 4.0)

    def test_skips_rows_with_unusable_timestamps(self) -> None:
        rows = [{"startTime": "soon", "endTime": "later", "text": "orphan"}]
        self.assertEqual(clean_lines(rows), [])


class ParseBattleTests(unittest.TestCase):
    def test_server_rendered_page(self) -> None:
        battle = parse_battle(
            _fixture("battle_page.html"),
            video_id="dQw4w9WgXcQ",
            url=battle_url("dQw4w9WgXcQ"),
        )
        self.assertEqual(battle["title"], "Test Battle - Alpha vs Bravo")
        self.assertEqual(battle["video_id"], "dQw4w9WgXcQ")
        # Music marker and the "Thank you for watching!" hallucination go; the
        # cased repeat collapses into the line before it.
        self.assertEqual(battle["line_count"], 2)
        self.assertEqual(battle["dropped"], 3)
        self.assertTrue(battle["has_speakers"])

        first, second = battle["lines"]
        self.assertEqual(first["start"], 90.0)
        self.assertEqual(first["end"], 115.0)
        self.assertEqual(first["speaker"], "Alpha")
        self.assertEqual(first["round"], "Round 1")
        # The payload is split across two script tags mid-object.
        self.assertEqual(second["start"], 3723.04)
        self.assertEqual(second["speaker"], "Bravo")
        self.assertIn('"quote"', second["text"])
        self.assertEqual(battle["duration"], 3728.0)

    def test_rsc_payload(self) -> None:
        battle = parse_battle(
            _fixture("battle_rsc.txt"),
            video_id="dQw4w9WgXcQ",
            url=battle_url("dQw4w9WgXcQ"),
        )
        self.assertEqual(battle["title"], "Test Battle - Alpha vs Bravo")
        self.assertEqual([line["text"] for line in battle["lines"]], ["an opening line"])
        self.assertFalse(battle["has_speakers"])

    def test_page_without_a_transcript(self) -> None:
        with self.assertRaises(LookupError):
            parse_battle("<html><body>nope</body></html>", video_id="x", url="")


class UrlTests(unittest.TestCase):
    def test_canonical_and_payload_forms(self) -> None:
        self.assertEqual(
            battle_url("dQw4w9WgXcQ"), "https://battlebarsph.com/battles/dQw4w9WgXcQ"
        )
        # Without ?_rsc the site 307s the request, costing a second round trip.
        self.assertEqual(
            battle_url("dQw4w9WgXcQ", rsc=True),
            "https://battlebarsph.com/battles/dQw4w9WgXcQ?_rsc",
        )


class CrawlTests(unittest.TestCase):
    def test_reports_failures_without_stopping(self) -> None:
        pages = {
            battle_url("dQw4w9WgXcQ"): _fixture("battle_rsc.txt"),
            battle_url("aaaaaaaaaaa"): "<html>no payload</html>",
        }
        results = list(
            crawl_battles(lambda url: pages[url], ["aaaaaaaaaaa", "dQw4w9WgXcQ"])
        )
        self.assertEqual([row[0] for row in results], ["aaaaaaaaaaa", "dQw4w9WgXcQ"])
        self.assertIsNone(results[0][1])
        self.assertIn("no transcript payload", results[0][2] or "")
        self.assertIsNotNone(results[1][1])
        self.assertIsNone(results[1][2])

    def test_rsc_mode_requests_the_payload_url(self) -> None:
        asked: list[str] = []

        def fetch(url: str) -> str:
            asked.append(url)
            return _fixture("battle_rsc.txt")

        results = list(crawl_battles(fetch, ["dQw4w9WgXcQ"], rsc=True))
        self.assertEqual(asked, [battle_url("dQw4w9WgXcQ", rsc=True)])
        # The stored url stays canonical even though the payload url was used.
        self.assertEqual(results[0][1]["url"], battle_url("dQw4w9WgXcQ"))

    def test_workers_return_every_battle(self) -> None:
        video_ids = [f"id{index:09d}" for index in range(12)]
        results = list(
            crawl_battles(
                lambda url: _fixture("battle_rsc.txt"), video_ids, workers=4
            )
        )
        self.assertEqual(len(results), len(video_ids))
        self.assertEqual({row[0] for row in results}, set(video_ids))
        self.assertTrue(all(row[1] is not None for row in results))


if __name__ == "__main__":
    unittest.main()
