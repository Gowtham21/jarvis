"""Settings loaded from environment variables and an optional .env file.

Every field can be set as JARVIS_<FIELD_NAME>, e.g. JARVIS_USER_NAME=Gowtham.
ANTHROPIC_API_KEY is read by the Anthropic SDK itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identity
    user_name: str = "sir"
    wake_word: str = "jarvis"

    # Claude
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    max_tokens: int = 8000
    web_search_uses: int = 3

    # Speech to text
    stt: Literal["google", "whisper"] = "google"
    whisper_model: str = "base"
    # Languages you speak, most used first, e.g. "en-IN,ta-IN". Google tries them in
    # order; Whisper auto-detects when more than one is listed.
    languages: str = "en-IN"
    listen_timeout: float = 8.0
    phrase_time_limit: float = 12.0

    # Text to speech
    # "clone" = XTTS v2 (17 languages, no Tamil); "indic" = IndicF5 (11 Indian languages
    # including Tamil, trained on Indian speakers). With "clone", Tamil replies are routed
    # to IndicF5 automatically when it is installed.
    tts: Literal["pyttsx3", "edge", "clone", "indic"] = "pyttsx3"
    edge_voice: str = "en-IN-PrabhatNeural"
    speech_rate: int = 170

    # Voice profile: the user's own utterances, saved as reference clips for "clone"
    collect_voice: bool = True
    voice_dir: Path = Path("voice_profile")
    clone_language: str = "en"
    clone_min_seconds: float = 6.0
    clone_temperature: float = 0.75  # higher is more expressive, lower is flatter and safer
    clone_speed: float = 1.0         # 0.8 slower .. 1.2 faster

    # Media folders for the play_media tool
    music_dir: Path | None = None
    video_dir: Path | None = None

    @field_validator("music_dir", "video_dir", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("wake_word", mode="before")
    @classmethod
    def _normalize_wake_word(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def language_list(self) -> list[str]:
        return [item.strip() for item in self.languages.split(",") if item.strip()] or ["en-IN"]

    @property
    def speaks_tamil(self) -> bool:
        return any(item.lower().startswith("ta") for item in self.language_list)

    @property
    def uses_fallbacks(self) -> bool:
        """Server-side refusal fallbacks are not available on Haiku models."""
        return "haiku" not in self.model


def load_settings() -> Settings:
    return Settings()
