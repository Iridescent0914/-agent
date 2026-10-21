from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "数学教学 Agent Demo"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://math_agent:math_agent_dev@localhost:5432/math_agent"
    )
    cors_origins: str = "http://localhost:5173"
    public_asset_base_url: str = "http://localhost:8000/assets"
    web_asset_dir: Path = Path(__file__).resolve().parents[3] / "data" / "web"
    public_teacher_asset_base_url: str = "http://localhost:8000/assets/teacher"
    teacher_asset_dir: Path = Path(__file__).resolve().parents[3] / "data" / "web" / "teacher"
    question_asset_max_upload_bytes: int = 8 * 1024 * 1024
    question_asset_max_pixels: int = 20_000_000
    teacher_asset_orphan_grace_hours: int = 24
    recognition_upload_dir: Path = Path(__file__).resolve().parents[3] / "data" / "uploads" / "m6"
    recognition_max_upload_bytes: int = 8 * 1024 * 1024
    recognition_max_pixels: int = 20_000_000
    recognition_retention_days: int = 30
    recognition_unreviewed_max_retention_days: int = 180
    recognition_processing_timeout_seconds: int = 300
    maintenance_interval_seconds: int = 3600
    vision_enabled: bool = False
    vision_provider: str = "fake"
    vision_model: str = "fake-m6-v1"
    vision_api_base_url: str = ""
    vision_api_key: str = ""
    vision_timeout_seconds: float = 45.0
    vision_max_tokens: int = 2048

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
