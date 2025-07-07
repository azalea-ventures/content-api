# helpers/analyze_helpers.py

import io
import os
import re
import traceback # Keep for consistency if you use it
from typing import Optional, List, Dict, Any # Keep Any if used elsewhere

# Assuming 'types' is from google.generativeai for File object hinting
from google.generativeai import types as genai_types_google

from models import (
    BatchAnalyzeItemResult,
    AnalyzeResponseItemError,
    AnalyzeResponseItemSuccess,
    SectionWithPages # Updated to use the new model with page metadata
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
    uploaded_file: Optional[genai_types_google.File] = None
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

        # Check file size before processing to prevent memory issues
        file_size = original_file_info.get('size', 0)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            )

        # Use the new method that only downloads if needed
        uploaded_file = await gemini_analysis_service.upload_pdf_for_analysis_by_file_id(
            file_id, 
            original_file_name, 
            storage_service
        )
        if uploaded_file is None:
            return BatchAnalyzeItemResult(
                success=False,
                error_info=AnalyzeResponseItemError(
                    originalDriveFileId=file_id,
                    error="Failed to upload file for analysis."
                )
            )

        # Pass the user-supplied prompt_text to the analysis service
        sections_info_dicts: Optional[List[Dict[str, Any]]] = await gemini_analysis_service.analyze_sections_multimodal(uploaded_file, prompt_text)
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
        
        # Convert the dictionary data to SectionWithPages objects
        sections_with_pages = []
        for section_dict in sections_info_dicts:
            pages = []
            for page_dict in section_dict.get('pages', []):
                pages.append({
                    'pageNumber': page_dict['pageNumber'],
                    'pageLabel': page_dict['pageLabel']
                })
            
            sections_with_pages.append(SectionWithPages(
                pageRange=section_dict['pageRange'],
                sectionName=section_dict['sectionName'],
                pages=pages
            ))
        
        return BatchAnalyzeItemResult(
            success=True,
            result=AnalyzeResponseItemSuccess(
                originalDriveFileId=file_id,
                originalDriveFileName=original_file_name,
                originalDriveParentFolderId=original_parent_folder_id,
                sections=sections_with_pages,
                genai_file_name=uploaded_file.name
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
        pass