from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    extraction_model: str = Field(default="gpt-5.4-nano")
    audit_model: str = Field(default="gpt-5.4-mini")
    openai_api_key: str | None = None


def load_settings() -> Settings:
    for env_path in (Path(".env"), Path(".env.local"), Path("/env")):
        if env_path.exists():
            load_dotenv(env_path, override=False)

    return Settings(
        extraction_model=os.getenv("RECEIPT_EXTRACTION_MODEL", "gpt-5.4-nano"),
        audit_model=os.getenv("RECEIPT_AUDIT_MODEL", "gpt-5.4-mini"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )
