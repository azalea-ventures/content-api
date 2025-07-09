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
    worker_timeout_seconds: int = 900  # Gunicorn worker timeout
    
    # Concurrent processing configuration
    max_concurrent_requests: int = 10  # Maximum concurrent API calls
    concurrent_retry_cooldown_seconds: int = 30  # Shorter cooldown for concurrent mode
    enable_concurrent_processing: bool = True  # Enable concurrent processing by default
    
    # Memory management configuration
    enable_memory_efficient_processing: bool = True  # Use memory-efficient processing by default
    section_processing_delay_seconds: float = 0.1  # Delay between section processing
    force_garbage_collection: bool = True  # Force garbage collection after each section

    # Split batching configuration
    split_batch_size: int = 5  # Number of sections to process in each batch
    split_batch_delay_seconds: float = 0.5  # Delay between batches for memory cleanup

settings = Settings()