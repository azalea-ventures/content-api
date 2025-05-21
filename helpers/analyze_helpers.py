# helpers/analyze_helpers.py

import io
import os
import uuid
import re
import traceback # Keep for consistency if you use it
from typing import Optional, List, Dict, Any # Keep Any if used elsewhere

# Assuming 'types' is from google.generativeai for File object hinting
from google.generativeai import types as genai_types_google

from models import (
    BatchAnalyzeItemResult,
    AnalyzeResponseItemError,
    AnalyzeResponseItemSuccess,
    SectionInfo # If SectionInfo is directly used as a type hint within, otherwise not needed here
)
# Import service class types for type hinting
from services.google_drive_service import GoogleDriveService
from services.generative_analysis_service import GenerativeAnalysisService


async def process_single_analyze_request(
    file_id: str,
    drive_service: GoogleDriveService,
    gemini_analysis_service: GenerativeAnalysisService
) -> BatchAnalyzeItemResult:
    print(f"Processing analyze request for file ID: {file_id}")
    pdf_stream: Optional[io.BytesIO] = None
    uploaded_file: Optional[genai_types_google.File] = None # Use alias for clarity
    try:
        original_file_info = drive_service.get_file_info(file_id)
        if original_file_info is None:
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Original Drive file not found or permission denied to get info."
                )
            )
        original_file_name = original_file_info.get('name', file_id)
        original_parent_folder_id = original_file_info.get('parents', [None])[0]

        pdf_stream = drive_service.download_file_content(file_id)
        if pdf_stream is None:
            # Attempt export if direct download fails (e.g. Google Doc)
            # This assumes your drive_service has an export_google_doc_as_pdf or similar
            # For now, let's keep it simple; you can add this logic if it exists in your service
            # mime_type = original_file_info.get('mimeType')
            # if mime_type == 'application/vnd.google-apps.document':
            #     pdf_stream = drive_service.export_google_doc_as_pdf(file_id)
            if pdf_stream is None:
                return BatchAnalyzeItemResult(
                    success=False,
                    error_info=AnalyzeResponseItemError(
                        originalDriveFileId=file_id,
                        error="Failed to download original Drive file content."
                    )
                )

        display_name_base = os.path.splitext(original_file_name)[0]
        display_name_sanitized = re.sub(r'[^\w\s.-]', '', display_name_base).strip()
        if not display_name_sanitized:
            display_name_sanitized = file_id
        display_name = f"{display_name_sanitized}_{uuid.uuid4().hex}.pdf"

        uploaded_file = gemini_analysis_service.upload_pdf_for_analysis(pdf_stream, display_name)
        if uploaded_file is None:
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Failed to upload document to Google AI for analysis."
                )
            )

        # Assuming analyze_sections_multimodal returns List[Dict[str, str]] which pydantic can handle
        # If SectionInfo model is used for stricter typing of this return, import it.
        sections_info_dicts: Optional[List[Dict[str, str]]] = gemini_analysis_service.analyze_sections_multimodal(uploaded_file)
        if sections_info_dicts is None:
            print(f"AI analysis failed for file ID: {file_id}")
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Failed to analyze document sections with AI."
                )
            )

        print(f"Successfully analyzed file ID: {file_id}. Returning results.")
        return BatchAnalyzeItemResult(
            success=True,
            result=AnalyzeResponseItemSuccess(
                originalDriveFileId=file_id,
                originalDriveFileName=original_file_name,
                originalDriveParentFolderId=original_parent_folder_id,
                sections=sections_info_dicts # This should match List[SectionInfo] or List[Dict]
            )
        )
    except Exception as ex:
        print(f"An unhandled error occurred processing analyze for file ID {file_id}: {ex}")
        traceback.print_exc() # Good for debugging
        return BatchAnalyzeItemResult(
            success=False,
            error_info=AnalyzeResponseItemError(
                originalDriveFileId=file_id,
                error="An internal server error occurred during analysis.",
                detail=str(ex)
            )
        )
    finally:
        if pdf_stream:
            pdf_stream.close()
        if uploaded_file and hasattr(uploaded_file, 'name') and gemini_analysis_service:
            try:
                gemini_analysis_service.delete_uploaded_file(uploaded_file.name)
            except Exception as cleanup_ex:
                print(f"Error during cleanup of uploaded file {uploaded_file.name}: {cleanup_ex}")