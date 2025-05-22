# helpers/extract_helpers.py

import io
import os
import uuid
import re
import traceback
from typing import Optional, List, Dict, Any

# Assuming 'types' is from google.generativeai for File object hinting
from google.generativeai import types as genai_types_google

from models import (
    BatchExtractItemResult,
    ExtractRequestItem, # Input type
    ExtractResponseItemError,
    ExtractResponseItemSuccess,
    ExtractedDataDict # For type hinting output_json_format_example if strict
)
# Import service class types for type hinting
from services.google_drive_service import GoogleDriveService
from services.pdf_text_extractor_service import PdfTextExtractorService
from services.generative_analysis_service import GenerativeAnalysisService

async def process_single_extract_request(
    request_item: ExtractRequestItem,
    drive_service: GoogleDriveService,
    pdf_text_extractor_service: PdfTextExtractorService,
    gemini_analysis_service: GenerativeAnalysisService,
    output_json_format_example: ExtractedDataDict # Use the type alias
) -> BatchExtractItemResult:
    prompt_doc_id = request_item.prompt_doc_id
    target_file_id = request_item.target_file_id
    print(f"Processing extract request: Prompt Doc ID {prompt_doc_id}, Target File ID {target_file_id}")

    prompt_pdf_stream: Optional[io.BytesIO] = None
    target_pdf_stream: Optional[io.BytesIO] = None
    uploaded_target_file: Optional[genai_types_google.File] = None
    prompt_text: Optional[str] = None
    target_file_info: Optional[Dict[str, Any]] = None

    try:
        target_file_info = drive_service.get_file_info(target_file_id)
        if target_file_info is None:
            return BatchExtractItemResult(
                success=False,
                error_info=ExtractResponseItemError(
                    promptDriveDocId=prompt_doc_id,
                    targetDriveFileId=target_file_id,
                    error="Target Drive file not found or permission denied to get info."
                )
            )
        target_file_name = target_file_info.get('name', target_file_id)
        target_parent_folder_id = target_file_info.get('parents', [None])[0]

        prompt_pdf_stream = drive_service.export_google_doc_as_pdf(prompt_doc_id)
        if prompt_pdf_stream is None:
            return BatchExtractItemResult(
                success=False,
                error_info=ExtractResponseItemError(
                    promptDriveDocId=prompt_doc_id,
                    targetDriveFileId=target_file_id,
                    error="Failed to download/export prompt Google Doc as PDF."
                )
            )

        prompt_text = pdf_text_extractor_service.extract_full_text_from_pdf(prompt_pdf_stream)
        if not prompt_text:
            return BatchExtractItemResult(
                success=False,
                error_info=ExtractResponseItemError(
                    promptDriveDocId=prompt_doc_id,
                    targetDriveFileId=target_file_id,
                    error="Could not extract text from prompt document."
                )
            )

        target_pdf_stream = drive_service.download_file_content(target_file_id)
        if target_pdf_stream is None:
            # Consider adding fallback logic if target_file_id could be a Google Doc
            # target_mime_type = target_file_info.get('mimeType')
            # if target_mime_type == 'application/vnd.google-apps.document':
            #     print(f"Attempting to export target Google Doc {target_file_id} as PDF for extraction.")
            #     target_pdf_stream = drive_service.export_google_doc_as_pdf(target_file_id)
            
            if target_pdf_stream is None: # If still None after potential fallback
                return BatchExtractItemResult(
                    success=False,
                    error_info=ExtractResponseItemError(
                        promptDriveDocId=prompt_doc_id,
                        targetDriveFileId=target_file_id,
                        error="Failed to download target PDF file content."
                    )
                )

        target_display_name_base = os.path.splitext(target_file_name)[0]
        target_display_name_sanitized = re.sub(r'[^\w\s.-]', '', target_display_name_base).strip()
        if not target_display_name_sanitized:
            target_display_name_sanitized = target_file_id
        target_display_name = f"{target_display_name_sanitized}_{uuid.uuid4().hex}.pdf"

        uploaded_target_file = gemini_analysis_service.upload_pdf_for_analysis(target_pdf_stream, target_display_name)
        if uploaded_target_file is None:
            return BatchExtractItemResult(
                success=False,
                error_info=ExtractResponseItemError(
                    promptDriveDocId=prompt_doc_id,
                    targetDriveFileId=target_file_id,
                    error="Failed to upload target document for AI extraction."
                )
            )

        extracted_data_dict = gemini_analysis_service.extract_structured_data_multimodal(
            uploaded_target_file,
            prompt_text,
            output_json_format_example # This is ExtractedDataDict
        )
        if extracted_data_dict is None:
            return BatchExtractItemResult(
                success=False,
                error_info=ExtractResponseItemError(
                    promptDriveDocId=prompt_doc_id,
                    targetDriveFileId=target_file_id,
                    error="Failed to extract structured data with AI."
                )
            )

        print(f"Successfully extracted data from target file ID {target_file_id} using prompt Doc ID {prompt_doc_id}.")
        return BatchExtractItemResult(
            success=True,
            result=ExtractResponseItemSuccess(
                promptDriveDocId=prompt_doc_id,
                targetDriveFileId=target_file_id,
                targetDriveFileName=target_file_name,
                targetDriveParentFolderId=target_parent_folder_id,
                extractedData=extracted_data_dict
            )
        )
    except Exception as ex:
        print(f"Unhandled error processing extract for Prompt Doc {prompt_doc_id}, Target File {target_file_id}: {ex}")
        traceback.print_exc()
        return BatchExtractItemResult(
            success=False,
            error_info=ExtractResponseItemError(
                promptDriveDocId=prompt_doc_id,
                targetDriveFileId=target_file_id,
                error="Internal server error during extraction.",
                detail=str(ex)
            )
        )
    finally:
        if prompt_pdf_stream: prompt_pdf_stream.close()
        if target_pdf_stream: target_pdf_stream.close()
        if uploaded_target_file and hasattr(uploaded_target_file, 'name') and gemini_analysis_service:
            try:
                gemini_analysis_service.delete_uploaded_file(uploaded_target_file.name)
            except Exception as cleanup_ex:
                print(f"Error during cleanup of uploaded file {uploaded_target_file.name}: {cleanup_ex}")