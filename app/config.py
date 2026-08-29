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

    transcription_backend: str = "local"  # local | groq | none

    whisper_model_size: str = "small"
    whisper_compute_type: str = "int8"
    whisper_language: str = "tl"
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    whisper_initial_prompt: str = (
        "Ito ay FlipTop battle rap sa Tagalog at English. "
        "Mga bar, punchline, rebuttal, at flip."
    )

    groq_api_key: str = ""
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "whisper-large-v3"
    groq_chunk_seconds: int = 600

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
