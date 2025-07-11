import io
import os
from typing import Optional, Dict, Any
from google.cloud import storage
from google.oauth2.service_account import Credentials
import base64
import json

class GoogleCloudStorageService:
    """
    Service for uploading files to Google Cloud Storage.
    Uses the same service account credentials as the Google Drive service.
    """
    
    def __init__(self, credentials: Credentials, bucket_name: str):
        """
        Initialize the Google Cloud Storage service.
        
        Args:
            credentials: Google service account credentials
            bucket_name: Name of the GCS bucket to upload to
        """
        if not credentials:
            raise ValueError("Google Credentials object is missing for Cloud Storage service.")
        
        if not bucket_name:
            raise ValueError("Bucket name is required for Cloud Storage service.")
        
        try:
            # Create storage client with the provided credentials
            self.storage_client = storage.Client(credentials=credentials)
            self.bucket_name = bucket_name
            self.bucket = self.storage_client.bucket(bucket_name)
            
            # Skip bucket verification since uploads work without it
            print(f"GoogleCloudStorageService initialized with bucket: {bucket_name}")
            print("Bucket access will be verified during upload operations.")
            
            print(f"GoogleCloudStorageService initialized successfully with bucket: {bucket_name}")
        except Exception as e:
            print(f"Error initializing GoogleCloudStorageService: {e}")
            raise RuntimeError(f"Failed to initialize Google Cloud Storage service: {e}") from e
    
    def upload_file_stream(self, file_stream: io.BytesIO, destination_blob_name: str, 
                          content_type: str = 'application/pdf') -> Optional[str]:
        """
        Upload a file stream to Google Cloud Storage.
        
        Args:
            file_stream: The file stream to upload
            destination_blob_name: The name/path for the file in GCS
            content_type: MIME type of the file (default: application/pdf)
            
        Returns:
            The public URL of the uploaded file, or None if upload failed
        """
        try:
            # Ensure stream is at the beginning
            file_stream.seek(0)
            
            # Create a blob object
            blob = self.bucket.blob(destination_blob_name)
            
            # Set content type
            blob.content_type = content_type
            
            # Upload the file stream
            blob.upload_from_file(file_stream, content_type=content_type)
            
            # Get the public URL
            public_url = blob.public_url
            
            print(f"Successfully uploaded file to GCS: {destination_blob_name}")
            print(f"Public URL: {public_url}")
            
            return public_url
            
        except Exception as e:
            print(f"Error uploading file to Google Cloud Storage: {e}")
            return None
    
    def upload_file_with_metadata(self, file_stream: io.BytesIO, destination_blob_name: str,
                                 metadata: Dict[str, str], content_type: str = 'application/pdf') -> Optional[str]:
        """
        Upload a file stream to Google Cloud Storage with custom metadata.
        
        Args:
            file_stream: The file stream to upload
            destination_blob_name: The name/path for the file in GCS
            metadata: Dictionary of custom metadata to attach to the blob
            content_type: MIME type of the file (default: application/pdf)
            
        Returns:
            The public URL of the uploaded file, or None if upload failed
        """
        try:
            # Ensure stream is at the beginning
            file_stream.seek(0)
            
            # Create a blob object
            blob = self.bucket.blob(destination_blob_name)
            
            # Set content type and metadata
            blob.content_type = content_type
            blob.metadata = metadata
            
            # Upload the file stream
            blob.upload_from_file(file_stream, content_type=content_type)
            
            # Get the public URL
            public_url = blob.public_url
            
            print(f"Successfully uploaded file to GCS with metadata: {destination_blob_name}")
            print(f"Public URL: {public_url}")
            print(f"Metadata: {metadata}")
            
            return public_url
            
        except Exception as e:
            print(f"Error uploading file with metadata to Google Cloud Storage: {e}")
            return None
    
    def delete_file(self, blob_name: str) -> bool:
        """
        Delete a file from Google Cloud Storage.
        
        Args:
            blob_name: The name/path of the file in GCS
            
        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            blob = self.bucket.blob(blob_name)
            blob.delete()
            print(f"Successfully deleted file from GCS: {blob_name}")
            return True
        except Exception as e:
            print(f"Error deleting file from Google Cloud Storage: {e}")
            return False
    
    def file_exists(self, blob_name: str) -> bool:
        """
        Check if a file exists in Google Cloud Storage.
        
        Args:
            blob_name: The name/path of the file in GCS
            
        Returns:
            True if file exists, False otherwise
        """
        try:
            blob = self.bucket.blob(blob_name)
            return blob.exists()
        except Exception as e:
            print(f"Error checking file existence in Google Cloud Storage: {e}")
            return False 