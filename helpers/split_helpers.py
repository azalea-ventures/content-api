# helpers/split_helpers.py

import io
import os
import asyncio
import traceback
from typing import Optional, List, Dict, Any

from models import (
    BatchSplitItemResult,
    SplitResponseItemError,
    SplitResponseItemSuccess,
    UploadedFileInfo,
    AnalyzeResponseItemSuccess # Input type
)
# Import service class types for type hinting
from services.google_drive_service import GoogleDriveService
from services.pdf_splitter_service import PdfSplitterService

async def process_single_split_request(
    analyze_response_item: AnalyzeResponseItemSuccess,
    drive_service: GoogleDriveService,
    pdf_splitter_service: PdfSplitterService
) -> BatchSplitItemResult:
    original_drive_file_id = analyze_response_item.originalDriveFileId
    original_drive_file_name = analyze_response_item.originalDriveFileName
    original_drive_parent_folder_id = analyze_response_item.originalDriveParentFolderId
    sections_to_split_dicts = analyze_response_item.sections # List[SectionInfo] or List[Dict]

    print(f"Processing split request for Drive file ID: {original_drive_file_id}")

    if not sections_to_split_dicts:
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                originalDriveFileId=original_drive_file_id,
                error="No sections provided for splitting."
            )
        )
    if original_drive_parent_folder_id is None:
        # This check is important as uploading requires a parent folder.
        print(f"Original Drive file ID {original_drive_file_id} has no parent folder. Cannot upload split sections.")
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                originalDriveFileId=original_drive_file_id,
                error="Original file is not in a folder. Cannot upload sections."
            )
        )

    original_pdf_stream: Optional[io.BytesIO] = None
    uploaded_files_info: List[UploadedFileInfo] = []

    try:
        original_pdf_stream = drive_service.download_file_content(original_drive_file_id)
        if original_pdf_stream is None:
            # Add export fallback if necessary, similar to analyze helper
            if original_pdf_stream is None:
                return BatchSplitItemResult(
                    success=False,
                    error_info=SplitResponseItemError(
                        originalDriveFileId=original_drive_file_id,
                        error="Failed to download original Drive file for splitting."
                    )
                )
        
        # Ensure sections_to_split_dicts matches what split_pdf_by_sections expects (List[Dict] or List[SectionInfo])
        # If it's List[SectionInfo], you might need to convert to List[Dict] if the service expects dicts.
        # Assuming it's already in the correct format (List[Dict] as per previous context of analyze_sections_multimodal)
        split_sections_output = await asyncio.to_thread(
            pdf_splitter_service.split_pdf_by_sections,
            original_pdf_stream,
            sections_to_split_dicts # This should be List[Dict[str, str]]
        )

        if not split_sections_output: # This is List[Dict[str, Any]] from PdfSplitterService
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    originalDriveFileId=original_drive_file_id,
                    error="PDF splitting failed or resulted in no sections."
                )
            )

        base_original_name = os.path.splitext(original_drive_file_name)[0]

        for section_data in split_sections_output: # section_data is Dict from PdfSplitterService
            section_name_raw = section_data['sectionName']
            section_file_stream = section_data['fileContent'] # This is an io.BytesIO
            section_file_name_part = section_data['fileName'] # Sanitized name part from splitter
            
            final_uploaded_name = f"{base_original_name}_{section_file_name_part}" # Construct full name

            print(f"Attempting to upload section '{section_name_raw}' as '{final_uploaded_name}' to Drive folder {original_drive_parent_folder_id}.")

            uploaded_drive_file_id = drive_service.upload_file_to_folder(
                file_name=final_uploaded_name,
                mime_type='application/pdf', # Assuming split sections are PDFs
                file_stream=section_file_stream,
                folder_id=original_drive_parent_folder_id
            )
            section_file_stream.close() # Close stream after upload

            if uploaded_drive_file_id: # Check if upload was successful
                uploaded_files_info.append(UploadedFileInfo(
                    sectionName=section_name_raw,
                    uploadedDriveFileId=uploaded_drive_file_id,
                    uploadedDriveFileName=final_uploaded_name
                ))
            else:
                print(f"Failed to upload section '{section_name_raw}' to Drive.")
                # Decide if one failed upload should fail the whole item or just be omitted.
                # For now, it's omitted from success list.

        if not uploaded_files_info: # If no sections were successfully uploaded
            print(f"No sections were successfully uploaded to Drive for file ID {original_drive_file_id}.")
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    originalDriveFileId=original_drive_file_id,
                    error="No sections were successfully uploaded to Google Drive."
                )
            )

        print(f"Successfully split and uploaded {len(uploaded_files_info)} sections for file ID {original_drive_file_id}.")
        return BatchSplitItemResult(
            success=True,
            result=SplitResponseItemSuccess(
                originalDriveFileId=original_drive_file_id,
                originalDriveFileName=original_drive_file_name,
                originalDriveParentFolderId=original_drive_parent_folder_id,
                uploadedSections=uploaded_files_info
            )
        )
    except Exception as ex:
        print(f"An unhandled error occurred processing split for file ID {original_drive_file_id}: {ex}")
        traceback.print_exc()
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                originalDriveFileId=original_drive_file_id,
                error="An internal server error occurred during splitting and upload.",
                detail=str(ex)
            )
        )
    finally:
        if original_pdf_stream:
            original_pdf_stream.close()