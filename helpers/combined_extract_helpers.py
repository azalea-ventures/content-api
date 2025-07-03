# helpers/combined_extract_helpers.py

import io
import os
import uuid
import traceback
from typing import Optional

from models import (
    CombinedExtractRequest,
    CombinedExtractResponse,
    SectionExtractionResult,
    SectionInfo
)
from services.google_drive_service import StorageService
from services.generative_analysis_service import GenerativeAnalysisService

async def process_combined_extract_request(
    request: CombinedExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService
) -> CombinedExtractResponse:
    """
    Processes a combined extract request: analyzes sections and extracts data.
    Uploads the file once to Gemini and uses it for both operations.
    """
    pdf_stream: Optional[io.BytesIO] = None
    uploaded_file: Optional[Any] = None  # types.File from genai
    
    try:
        print(f"Processing combined extract request for file ID: {request.target_drive_file_id}")
        
        # Download the target file
        pdf_stream = storage_service.download_file_content(request.target_drive_file_id)
        if not pdf_stream:
            return CombinedExtractResponse(
                target_drive_file_id=request.target_drive_file_id,
                success=False,
                error="Failed to download target file"
            )
        
        # Get file info for display name
        file_info = storage_service.get_file_info(request.target_drive_file_id)
        file_name = file_info.get('name', request.target_drive_file_id) if file_info else request.target_drive_file_id
        
        # Check file size before uploading
        file_size = file_info.get('size', 0) if file_info else 0
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return CombinedExtractResponse(
                target_drive_file_id=request.target_drive_file_id,
                success=False,
                error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
            )
        
        # Upload file once to Gemini
        display_name = f"combined_extract_{os.path.splitext(file_name)[0]}_{uuid.uuid4().hex[:8]}.pdf"
        uploaded_file = await gemini_analysis_service.upload_pdf_for_analysis(pdf_stream, display_name)
        
        if not uploaded_file:
            return CombinedExtractResponse(
                target_drive_file_id=request.target_drive_file_id,
                success=False,
                error="Failed to upload file to Gemini"
            )
        
        print(f"Successfully uploaded file to Gemini. Starting combined analysis and extraction...")
        
        # Perform combined analysis and extraction
        sections_info, extraction_results = await gemini_analysis_service.combined_analyze_and_extract(
            uploaded_file,
            request.analysis_prompt,
            request.extraction_prompts,
            request.output_json_format_example
        )
        
        if not sections_info:
            return CombinedExtractResponse(
                target_drive_file_id=request.target_drive_file_id,
                success=False,
                error="Failed to analyze document sections"
            )
        
        # Convert to response format
        section_extractions = []
        for result in extraction_results or []:
            section_extractions.append(SectionExtractionResult(
                section_name=result['section_name'],
                page_range=result['page_range'],
                extracted_data=result['extracted_data']
            ))
        
        # Convert sections_info to SectionInfo objects
        sections = [
            SectionInfo(sectionName=section['sectionName'], pageRange=section['pageRange'])
            for section in sections_info
        ]
        
        print(f"Successfully completed combined extract for file: {request.target_drive_file_id}")
        print(f"Found {len(sections)} sections, extracted data from {len(section_extractions)} sections")
        
        return CombinedExtractResponse(
            target_drive_file_id=request.target_drive_file_id,
            target_drive_file_name=file_name,
            sections=sections,
            section_extractions=section_extractions,
            success=True
        )
        
    except Exception as e:
        print(f"An unhandled error occurred processing combined extract for file ID {request.target_drive_file_id}: {e}")
        traceback.print_exc()
        return CombinedExtractResponse(
            target_drive_file_id=request.target_drive_file_id,
            success=False,
            error=f"An internal server error occurred during combined extract: {str(e)}"
        )
    finally:
        # Cleanup
        if pdf_stream:
            try:
                pdf_stream.close()
                del pdf_stream
            except Exception as e:
                print(f"Error closing PDF stream: {e}")
        if uploaded_file and hasattr(uploaded_file, 'name') and gemini_analysis_service:
            try:
                gemini_analysis_service.delete_uploaded_file(uploaded_file.name)
                print(f"Successfully cleaned up uploaded file: {uploaded_file.name}")
            except Exception as cleanup_ex:
                print(f"Error during cleanup of uploaded file {uploaded_file.name}: {cleanup_ex}") 