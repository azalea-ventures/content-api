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
from services.google_drive_service import StorageService
from services.generative_analysis_service import GenerativeAnalysisService


async def process_single_analyze_request(
    file_id: str,
    prompt_text: str,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService
) -> BatchAnalyzeItemResult:
    print(f"Processing analyze request for file ID: {file_id}")
    pdf_stream: Optional[io.BytesIO] = None
    uploaded_file: Optional[genai_types_google.File] = None # Use alias for clarity
    try:
        original_file_info = storage_service.get_file_info(file_id)
        if original_file_info is None:
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Original file not found or permission denied to get info."
                )
            )
        original_file_name = original_file_info.get('name', file_id)
        # For GoogleDriveService, 'parents' is a list; for Supabase, use 'user_id' as parent/folder equivalent
        original_parent_folder_id = original_file_info.get('parents', [None])[0] if 'parents' in original_file_info else original_file_info.get('user_id')

        # Check file size before downloading to prevent memory issues
        file_size = original_file_info.get('size', 0)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            )

        pdf_stream = storage_service.download_file_content(file_id)
        if pdf_stream is None:
            # Attempt export if direct download fails (e.g. Google Doc)
            pdf_stream = storage_service.export_google_doc_as_pdf(file_id)
            if pdf_stream is None:
                return BatchAnalyzeItemResult(
                    success=False,
                    error_info=AnalyzeResponseItemError(
                        originalDriveFileId=file_id,
                        error="Failed to download original file content."
                    )
                )

        display_name_base = os.path.splitext(original_file_name)[0]
        display_name_sanitized = re.sub(r'[^\w\s.-]', '', display_name_base).strip()
        if not display_name_sanitized:
            display_name_sanitized = file_id
        display_name = f"{display_name_sanitized}_{uuid.uuid4().hex}.pdf"

        uploaded_file = await gemini_analysis_service.upload_pdf_for_analysis(pdf_stream, display_name)
        if uploaded_file is None:
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Failed to upload document to Google AI for analysis."
                )
            )

        # Pass the user-supplied prompt_text to the analysis service
        sections_info_dicts: Optional[List[Dict[str, str]]] = await gemini_analysis_service.analyze_sections_multimodal(uploaded_file, prompt_text)
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
        # Explicit memory cleanup
        if pdf_stream:
            try:
                pdf_stream.close()
                del pdf_stream
            except Exception as e:
                print(f"Error closing PDF stream: {e}")
        if uploaded_file and hasattr(uploaded_file, 'name') and gemini_analysis_service:
            try:
                gemini_analysis_service.delete_uploaded_file(uploaded_file.name)
            except Exception as cleanup_ex:
                print(f"Error during cleanup of uploaded file {uploaded_file.name}: {cleanup_ex}")