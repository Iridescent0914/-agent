from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


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
    stage6_enabled: bool = False
    stage6_worker_interval_seconds: int = Field(default=5, ge=1, le=300)
    stage6_timeout_seconds: int = Field(default=10, ge=1, le=120)
    stage6_lease_seconds: int = Field(default=30, ge=2, le=600)
    stage6_max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def stage6_valid_lease(self):
        if self.stage6_lease_seconds <= self.stage6_timeout_seconds:
            raise ValueError("STAGE6_LEASE_SECONDS 必须大于 STAGE6_TIMEOUT_SECONDS")
        return self

    question_import_dir: Path = Path(__file__).resolve().parents[3] / "data" / "uploads" / "question_imports"
    question_import_max_file_bytes: int = 30 * 1024 * 1024
    question_import_max_batch_bytes: int = 60 * 1024 * 1024
    question_import_max_pdf_pages: int = 50
    question_import_max_files: int = 2
    question_import_max_candidates: int = 200
    question_import_retention_hours: int = 72
    question_import_recent_batches: int = 30
    question_import_worker_interval_seconds: int = 15
    question_import_lease_seconds: int = 120
    question_import_max_attempts: int = 3
    question_import_s2_enabled: bool = False
    question_import_s2_api_key: str = ""
    question_import_s2_operator_token: str = ""
    question_import_s2_base_url: str = ""
    question_import_s2_model: str = "qwen3.8-flash"
    question_import_s2_timeout_seconds: float = 360.0
    question_import_s2_max_output_tokens: int = 16384
    question_import_s2_pages_per_unit: int = 4
    question_import_s2_overlap_pages: int = 1
    question_import_s2_max_calls: int = 30
    question_import_s2_max_budget_cny: float = 20.0
    question_import_s2_pdf_page_price_cny: float = 0.02
    question_import_s2_input_million_price_cny: float = 0.8
    question_import_s2_output_million_price_cny: float = 2.7

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
