"""Konfiguration via miljøvariabler (12-factor: konfiguration ligger i miljøet, ikke i koden)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./local.db"
    # Ingen standardværdi: appen nægter at starte uden en API-nøgle (fail closed).
    api_key: str = Field(min_length=8)
    notify_webhook_url: str | None = None
    log_level: str = "INFO"
    # Maks. kald pr. minut pr. klient på /api/v1. 0 slår begrænsningen fra.
    rate_limit_per_minute: int = Field(default=120, ge=0)
