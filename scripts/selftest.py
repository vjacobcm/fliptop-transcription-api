#!/usr/bin/env python3
"""Offline smoke test: caption parsing, storage, and the API surface.

Uses synthetic captions so it runs without touching YouTube.

    python scripts/selftest.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Battle, BattleStatus, TranscriptSource  # noqa: E402
from app.services.captions import parse_caption_payload  # noqa: E402
from app.services.ingest import _replace_segments  # noqa: E402
from app.services.youtube import extract_video_id  # noqa: E402

VIDEO_ID = "selftest123"

# Real-looking 11-character id, used to exercise link parsing.
SAMPLE_ID = "Xfsbnz_WTLs"

ACCEPTED_LINKS = (
    f"https://www.youtube.com/watch?v={SAMPLE_ID}",
    f"http://youtube.com/watch?v={SAMPLE_ID}",
    f"https://youtu.be/{SAMPLE_ID}",
    f"https://youtu.be/{SAMPLE_ID}?si=abc123",
    f"https://www.youtube.com/watch?v={SAMPLE_ID}&list=PLxxx&t=42",
    f"https://www.youtube.com/shorts/{SAMPLE_ID}",
    f"https://www.youtube.com/embed/{SAMPLE_ID}",
    f"https://www.youtube.com/live/{SAMPLE_ID}",
    f"https://m.youtube.com/watch?v={SAMPLE_ID}",
    f"https://music.youtube.com/watch?v={SAMPLE_ID}",
    f"youtu.be/{SAMPLE_ID}",
    f"  {SAMPLE_ID}  ",
)

REJECTED_LINKS = (
    "hello world",
    "https://vimeo.com/12345",
    "",
    "   ",
    "https://notyoutube.com.evil.test/watch?v=abc",
    "https://www.youtube.com/",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/@FlipTopBattles",
    f"https://evil-youtu.be/{SAMPLE_ID}",
)

# Mimics YouTube auto-caption output: each event repeats the previous line
# and appends a couple of words.
ROLLING_JSON3 = json.dumps(
    {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "first line"}]},
            {
                "tStartMs": 1000,
                "dDurationMs": 2000,
                "segs": [{"utf8": "first line continues"}],
            },
            {
                "tStartMs": 3000,
                "dDurationMs": 2000,
                "segs": [{"utf8": "second line here"}],
            },
            {"tStartMs": 5000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]},
        ]
    }
)

SAMPLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.000
alpha bravo

00:00:02.000 --> 00:00:04.000
<c>charlie</c> delta
"""


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{f' -> {detail}' if detail else ''}")
    return condition


def main() -> int:
    print("\nURL parsing")
    accepted = []
    for link in ACCEPTED_LINKS:
        try:
            accepted.append(extract_video_id(link) == SAMPLE_ID)
        except ValueError:
            accepted.append(False)
    ok = check(
        "valid youtube links resolve to the video id",
        all(accepted),
        f"{sum(accepted)}/{len(ACCEPTED_LINKS)} accepted",
    )

    rejected = []
    for link in REJECTED_LINKS:
        try:
            extract_video_id(link)
            rejected.append(False)
        except ValueError:
            rejected.append(True)
    ok &= check(
        "junk and non-youtube links are rejected",
        all(rejected),
        f"{sum(rejected)}/{len(REJECTED_LINKS)} rejected",
    )

    print("\nCaption parsing")
    rolling = parse_caption_payload(ROLLING_JSON3, "json3")
    ok &= check(
        "rolling duplicates collapsed",
        len(rolling) == 2,
        f"{len(rolling)} segments (expected 2)",
    )
    ok &= check(
        "rolling text kept longest form",
        rolling[0]["text"] == "first line continues",
        rolling[0]["text"],
    )
    ok &= check(
        "overlaps clamped",
        rolling[0]["end"] <= rolling[1]["start"],
        f"{rolling[0]['end']} <= {rolling[1]['start']}",
    )

    vtt = parse_caption_payload(SAMPLE_VTT, "vtt")
    ok &= check("vtt parsed", len(vtt) == 2, f"{len(vtt)} segments")
    ok &= check("vtt tags stripped", vtt[1]["text"] == "charlie delta", vtt[1]["text"])

    print("\nStorage + API")
    init_db()
    with Session(engine) as session:
        battle = session.get(Battle, VIDEO_ID) or Battle(
            video_id=VIDEO_ID, url=f"https://www.youtube.com/watch?v={VIDEO_ID}"
        )
        battle.title = "Self Test Battle"
        battle.status = BattleStatus.READY
        battle.source = TranscriptSource.YOUTUBE_AUTO
        battle.language = "fil"
        battle.segment_count = len(rolling)
        session.add(battle)
        session.commit()
        _replace_segments(session, VIDEO_ID, rolling)

    client = TestClient(app)

    ok &= check("GET /health", client.get("/health").json() == {"status": "ok"})

    listed = client.get("/battles").json()
    ok &= check(
        "GET /battles includes battle",
        any(item["video_id"] == VIDEO_ID for item in listed),
    )

    payload = client.get(f"/battles/{VIDEO_ID}/transcript").json()
    ok &= check(
        "transcript json has segments",
        len(payload["segments"]) == 2,
        f"{len(payload['segments'])} segments",
    )
    ok &= check("source recorded", payload["source"] == "youtube_auto", payload["source"])

    srt = client.get(f"/battles/{VIDEO_ID}/transcript", params={"format": "srt"}).text
    ok &= check("srt numbering", srt.startswith("1\n"), repr(srt.splitlines()[0]))
    ok &= check("srt timestamp format", "-->" in srt and "," in srt.split("\n")[1])

    vtt_out = client.get(
        f"/battles/{VIDEO_ID}/transcript", params={"format": "vtt"}
    ).text
    ok &= check("vtt header", vtt_out.startswith("WEBVTT"))

    ok &= check("404 for unknown battle", client.get("/battles/nope").status_code == 404)

    print("\nLookup by link")
    watch_url = f"https://www.youtube.com/watch?v={VIDEO_ID}"

    by_url = client.get("/transcript", params={"url": watch_url}).json()
    ok &= check(
        "GET /transcript?url= resolves watch link",
        by_url["video_id"] == VIDEO_ID,
        by_url.get("video_id", ""),
    )

    short = client.get("/transcript", params={"url": f"https://youtu.be/{VIDEO_ID}"})
    ok &= check("youtu.be short link resolves", short.json()["video_id"] == VIDEO_ID)

    as_text = client.get(
        "/transcript", params={"url": VIDEO_ID, "format": "text"}
    ).text
    ok &= check("bare video id accepted", as_text.startswith("first line continues"))

    bad_url = client.get("/transcript", params={"url": "https://www.youtube.com/"})
    ok &= check(
        "400 for malformed youtube url", bad_url.status_code == 400, str(bad_url.status_code)
    )

    # Well formed, so it gets past validation and misses on lookup instead.
    missing = client.get("/transcript", params={"url": "https://youtu.be/aaaaaaaaaaa"})
    ok &= check(
        "404 for valid link that is not ingested",
        missing.status_code == 404,
        str(missing.status_code),
    )

    # Already READY, so this returns inline without reaching YouTube.
    cached = client.post("/transcript", json={"url": watch_url})
    ok &= check(
        "POST /transcript serves stored battle inline",
        cached.status_code == 200 and len(cached.json()["segments"]) == 2,
        str(cached.status_code),
    )

    with Session(engine) as session:
        stored = session.get(Battle, VIDEO_ID)
        stored.status = BattleStatus.PROCESSING
        session.add(stored)
        session.commit()

    in_flight = client.post("/transcript", json={"url": watch_url})
    ok &= check(
        "POST /transcript returns 202 while ingest is running",
        in_flight.status_code == 202
        and in_flight.json()["status_url"] == f"/battles/{VIDEO_ID}",
        str(in_flight.status_code),
    )

    client.delete(f"/battles/{VIDEO_ID}")

    print("\n" + ("All checks passed.\n" if ok else "Some checks FAILED.\n"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
