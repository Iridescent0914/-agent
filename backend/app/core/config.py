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
    public_asset_base_url: str = "http://localhost:8000/assets/rational"
    web_asset_dir: Path = Path(__file__).resolve().parents[3] / "data" / "web" / "rational"

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
