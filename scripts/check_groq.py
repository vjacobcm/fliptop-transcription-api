#!/usr/bin/env python3
"""Verify the Groq setup before spending any of the daily quota.

Checks the backend setting, that the key authenticates, and that the model
named in .env actually exists.

    python scripts/check_groq.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


def ok(label: str, detail: str = "") -> None:
    print(f"  [ok]   {label}{f' -> {detail}' if detail else ''}")


def bad(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}{f' -> {detail}' if detail else ''}")


def main() -> int:
    print("\nConfiguration")

    backend = settings.transcription_backend.lower()
    if backend == "groq":
        ok("TRANSCRIPTION_BACKEND", backend)
    else:
        bad("TRANSCRIPTION_BACKEND", f"{backend} (set it to 'groq' in .env)")

    if not settings.groq_api_key:
        bad("GROQ_API_KEY", "empty")
        print(
            "\nCreate a free key at https://console.groq.com/keys "
            "and paste it into .env as GROQ_API_KEY=gsk_...\n"
        )
        return 1

    # Only ever show the shape of the key, never the key itself.
    key = settings.groq_api_key
    ok("GROQ_API_KEY", f"{key[:4]}…{key[-4:]} ({len(key)} chars)")

    print("\nConnectivity")
    endpoint = f"{settings.groq_api_base.rstrip('/')}/models"
    try:
        response = httpx.get(
            endpoint,
            headers={"Authorization": f"Bearer {key}"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        bad("reach api.groq.com", str(exc))
        return 1

    if response.status_code == 401:
        bad("authenticate", "401 — the key was rejected. Create a new one.")
        return 1
    if response.status_code != 200:
        bad("list models", f"HTTP {response.status_code}: {response.text[:200]}")
        return 1

    ok("authenticate", "key accepted")

    models = sorted(m["id"] for m in response.json().get("data", []))
    whisper = [m for m in models if "whisper" in m]

    if settings.groq_model in models:
        ok("GROQ_MODEL", settings.groq_model)
    else:
        bad("GROQ_MODEL", f"{settings.groq_model} not available")
        print(f"         available: {', '.join(whisper) or 'none'}")
        return 1

    if whisper:
        print(f"\n  speech models on this account: {', '.join(whisper)}")

    print("\nFree-tier budget")
    print(f"  {settings.groq_daily_audio_seconds / 3600:.0f}h of audio per day")
    print(f"  {settings.groq_requests_per_minute} requests/min")
    print(f"  {settings.groq_max_upload_mb:.0f} MB max upload")

    print("\nReady. Plan a run with:")
    print("  python scripts/backfill.py --upgrade --limit 2 --dry-run\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
