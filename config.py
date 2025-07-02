import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', case_sensitive=False, extra='ignore')

    # google_service_account_json: Optional[str] = None # Old one
    google_service_account_json_base64: Optional[str] = None # New Base64 field
    gemini_api_key: Optional[str] = None
    gemini_model_id: str = None
    max_api_retries: int = 3
    max_data_dependency_retries: int = 5
    retry_cooldown_seconds: int = 60
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    supabase_bucket_name: Optional[str] = None
    storage_backend: str = "supabase"  # Options: 'google_drive', 'supabase'
    
    # New configuration options for better performance and stability
    max_file_size_mb: int = 50  # Maximum file size in MB
    gemini_timeout_seconds: int = 300  # Timeout for Gemini API calls
    file_upload_poll_timeout_seconds: int = 300  # Timeout for file upload polling
    worker_timeout_seconds: int = 600  # Gunicorn worker timeout

settings = Settings()