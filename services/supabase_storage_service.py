import io
from typing import Optional, Dict, Any
from supabase import create_client, Client
from services.google_drive_service import StorageService
from config import settings
import requests

class SupabaseStorageService(StorageService):
    def __init__(self):
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError("Supabase URL and Key must be set in config.")
        self.supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

    def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.supabase.table("files").select("id, name, file_path, size, type, user_id, created_at, updated_at").eq("id", file_id).single().execute()
            if response.data:
                return response.data
            return None
        except Exception as e:
            print(f"SupabaseStorageService: Error getting file info for {file_id}: {e}")
            return None

    def download_file_content(self, file_id: str) -> Optional[io.BytesIO]:
        try:
            file_info = self.get_file_info(file_id)
            bucket_name = settings.supabase_bucket_name or "pdfs"  # Use configured bucket name or default to "pdfs"
            if not file_info or "name" not in file_info:
                print(f"SupabaseStorageService: File info or name not found for {file_id}")
                return None

            response = self.supabase.storage.from_(bucket_name).download(file_info["file_path"])
            file_stream = io.BytesIO(response)

            if file_stream:
                return file_stream
            else:
                print(f"SupabaseStorageService: Failed to download file {file_info.get('file_path')} from bucket {bucket_name}. Status: {getattr(response, 'status_code', 'unknown')}")
                return None
        except Exception as e:
            print(f"SupabaseStorageService: Error downloading file content for {file_id}: {e}")
            return None

    def export_google_doc_as_pdf(self, file_id: str) -> Optional[io.BytesIO]:
        # Not applicable for Supabase, return None or raise NotImplementedError
        print("SupabaseStorageService: export_google_doc_as_pdf is not supported.")
        return None

    def upload_file_to_folder(self, file_name: str, mime_type: str, file_stream: io.BytesIO, folder_id: str) -> Optional[str]:
        try:
            file_stream.seek(0)
            # Compose storage path (e.g., folder_id/file_name)
            storage_path = f"{folder_id}/{file_name}"
            bucket_name = settings.supabase_bucket_name or "pdfs"  # Use configured bucket name or default to "pdfs"
            # Upload to Supabase Storage bucket
            storage_response = self.supabase.storage.from_(bucket_name).upload(storage_path, file_stream, file_options={"content-type": mime_type, "upsert": True})
            if not storage_response:
                print(f"SupabaseStorageService: Failed to upload file to storage for {file_name}")
                return None
            # Get public URL
            public_url = self.supabase.storage.from_(bucket_name).get_public_url(storage_path)
            # Insert metadata into files table
            file_size = file_stream.getbuffer().nbytes
            insert_response = self.supabase.table("files").insert({
                "name": file_name,
                "file_path": public_url,
                "size": file_size,
                "type": mime_type,
                "user_id": folder_id,  # Assuming folder_id is user_id for this context
            }).execute()
            if insert_response.data and len(insert_response.data) > 0:
                return insert_response.data[0]["id"]
            return None
        except Exception as e:
            print(f"SupabaseStorageService: Error uploading file {file_name}: {e}")
            return None 