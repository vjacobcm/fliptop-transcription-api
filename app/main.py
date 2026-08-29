import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.db import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

app = FastAPI(
    title="FlipTop Transcription API",
    description=(
        "Transcribes FlipTop battles from YouTube. Uses YouTube caption tracks "
        "when they exist and falls back to Whisper when they do not."
    ),
    version="0.1.0",
)

# The browser extension will call this from youtube.com.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.youtube.com", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
