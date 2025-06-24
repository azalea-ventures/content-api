import io
import json
import os
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload # Import MediaIoBaseUpload

# Define the scopes needed for Google Drive and Generative AI
# Need 'drive' scope for both read (including metadata) and write (upload)
SCOPES = [
    'https://www.googleapis.com/auth/drive', # Full Drive access
    'https://www.googleapis.com/auth/cloud-platform' # General scope for Generative Language API
]

class StorageService(ABC):
    @abstractmethod
    def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def download_file_content(self, file_id: str) -> Optional[io.BytesIO]:
        pass

    @abstractmethod
    def export_google_doc_as_pdf(self, file_id: str) -> Optional[io.BytesIO]:
        pass

    @abstractmethod
    def upload_file_to_folder(self, file_name: str, mime_type: str, file_stream: io.BytesIO, folder_id: str) -> Optional[str]:
        pass

class GoogleDriveService(StorageService):
    # Change __init__ to accept the Credentials object
    def __init__(self, credentials: Credentials):
        """
        Initializes the GoogleDriveService with Google Credentials.

        Args:
            credentials: A GoogleCredential object (should include the 'drive' scope).
        """
        if not credentials:
             raise ValueError("Google Credentials object is missing for Drive service.")

        try:
            # Build the Drive service client using the provided credentials
            self.drive_service = build('drive', 'v3', credentials=credentials)
            print("GoogleDriveService initialized successfully.")
        except Exception as e:
            print(f"Error initializing GoogleDriveService: {e}")
            raise RuntimeError("Failed to initialize Google Drive service.") from e

    # get_file_info, download_file_content, export_google_doc_as_pdf, upload_file_to_folder
    # ... (These methods remain the same, using self.drive_service) ...

    # Copy the existing methods from the previous complete response here:
    def get_file_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets the name and parent folder IDs of a Google Drive file.
        Uses the drive.files().get method with fields='name,parents'.
        Requires drive.readonly or drive scope.
        """
        try:
            # Request the file name and parents field
            # This is the call that returned 404
            file = self.drive_service.files().get(fileId=file_id, fields="name,parents").execute()

            print(f"Successfully retrieved info for file ID: {file_id}")
            return file
        except HttpError as e:
            print(f"Google Drive HTTP Error getting file info {file_id}: {e}")
            if e.resp.status == 404:
                print("File not found.")
            elif e.resp.status == 403:
                 print("Permission denied.")
            return None
        except Exception as e:
            print(f"Error getting file info {file_id} from Google Drive: {e}")
            return None


    def download_file_content(self, file_id: str) -> Optional[io.BytesIO]:
        """
        Downloads a file's content from Google Drive by ID using MediaIoBaseDownload.
        Uses the drive.files().get_media method.
        Requires drive.readonly or drive scope.
        """
        try:
            # Get the media request for the file content
            request = self.drive_service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()

            # Use MediaIoBaseDownload to download the file
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                # print(f"Download {int(status.progress() * 100)}%.") # Optional progress

            file_stream.seek(0) # Rewind the stream
            print(f"Successfully downloaded file content for file ID: {file_id}")
            return file_stream
        except HttpError as e:
            print(f"Google Drive HTTP Error downloading file {file_id}: {e}")
            if e.resp.status == 404:
                print("File not found or service account doesn't have permission.")
            elif e.resp.status == 403:
                 print("Permission denied for service account to access this file.")
            return None
        except Exception as e:
            print(f"Error downloading file {file_id} from Google Drive: {e}")
            return None

    def export_google_doc_as_pdf(self, file_id: str) -> Optional[io.BytesIO]:
        """
        Exports a Google Doc file as PDF using MediaIoBaseDownload.
        Uses the drive.files().export method.
        Requires drive scope.
        """
        try:
            request = self.drive_service.files().export(fileId=file_id, mimeType='application/pdf')
            file_stream = io.BytesIO()

            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                # print(f"Export {int(status.progress() * 100)}%.") # Optional progress

            file_stream.seek(0)
            print(f"Successfully exported Google Doc {file_id} as PDF.")
            return file_stream
        except HttpError as e:
             print(f"Google Drive HTTP Error exporting Google Doc {file_id}: {e}")
             if e.resp.status == 400:
                  print("File is likely not a Google Doc or cannot be exported to PDF with this mimeType.")
             elif e.resp.status == 403:
                  print("Permission denied for service account to export this file.")
             return None
        except Exception as e:
             print(f"Error exporting Google Doc {file_id}: {e}")
             return None


    def upload_file_to_folder(self, file_name: str, mime_type: str, file_stream: io.BytesIO, folder_id: str) -> Optional[str]:
        """
        Uploads a file to a specific folder in Google Drive.
        Uses the drive.files().create method.
        Requires drive scope and permissions on the target folder.
        """
        try:
            file_stream.seek(0) # Ensure stream is at the beginning for upload

            # Define the file metadata
            file_metadata = {
                'name': file_name,
                'parents': [folder_id] # Specify the parent folder ID
            }

            # Use MediaIoBaseUpload to upload the file content
            media = MediaIoBaseUpload(file_stream, mime_type, resumable=True)

            # Create the file
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id' # Request only the file ID in the response
            ).execute()

            file_id = file.get('id')
            print(f"Successfully uploaded file '{file_name}' to folder {folder_id} with ID: {file_id}")
            return file_id

        except HttpError as e:
            print(f"Google Drive HTTP Error uploading file '{file_name}' to folder {folder_id}: {e}")
            if e.resp.status == 403:
                 print("Permission denied to upload to this folder.")
            return None
        except Exception as e:
            print(f"Error uploading file '{file_name}' to Google Drive: {e}")
            return None