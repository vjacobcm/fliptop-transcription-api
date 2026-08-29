from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "sqlite:///./fliptop.db"
    data_dir: Path = Path("data")

    caption_langs: str = "fil,tl,en"
    use_youtube_captions: bool = True

    # Whisper silently drops speech that sits under music, so gaps in its
    # output are back-filled from the YouTube caption track when one exists.
    fill_whisper_gaps: bool = True

    transcription_backend: str = "local"  # local | groq | none

    whisper_model_size: str = "small"
    whisper_compute_type: str = "int8"
    whisper_language: str = "tl"
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    # Kept to a few words on purpose: Whisper emits its prompt as transcript
    # text over music, so a long prompt shows up as phantom lines.
    whisper_initial_prompt: str = "FlipTop battle rap."

    groq_api_key: str = ""
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "whisper-large-v3"
    groq_chunk_seconds: int = 600

    # Free-tier ceilings (console.groq.com/docs/rate-limits). Uploads are
    # capped at 25 MB and requests at 20/min; paid tiers raise both.
    groq_max_upload_mb: float = 24.0
    groq_chunk_bitrate: str = "64k"
    groq_requests_per_minute: int = 20
    groq_max_retries: int = 5
    groq_daily_audio_seconds: int = 28800

    @property
    def caption_lang_list(self) -> list[str]:
        return [lang.strip() for lang in self.caption_langs.split(",") if lang.strip()]

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
