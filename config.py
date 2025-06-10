import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', case_sensitive=False, extra='ignore')

    # google_service_account_json: Optional[str] = None # Old one
    google_service_account_json_base64: Optional[str] = None # New Base64 field
    gemini_api_key: Optional[str] = None
    gemini_model_id: str = "gemini-2.0-flash-latest"
    max_api_retries: int = 3
    max_data_dependency_retries: int = 5
    retry_cooldown_seconds: int = 60

settings = Settings()