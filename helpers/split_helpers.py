# helpers/split_helpers.py

import io
import os
import asyncio
import traceback
import uuid
import gc
from typing import Optional, List, Dict, Any

from models import (
    BatchSplitItemResult,
    SplitResponseItemError,
    SplitResponseItemSuccess,
    UploadedFileInfo,
    SplitRequest # Input type
)
# Import service class types for type hinting
from services.google_drive_service import StorageService
from services.pdf_splitter_service import PdfSplitterService
from services.generative_analysis_service import GenerativeAnalysisService
from services.google_cloud_storage_service import GoogleCloudStorageService
from config import settings

async def process_single_split_request(
    split_request: SplitRequest,
    storage_service: StorageService,
    pdf_splitter_service: PdfSplitterService,
    gemini_service: Optional[GenerativeAnalysisService] = None
) -> BatchSplitItemResult:
    storage_file_id = split_request.storage_file_id
    file_name = split_request.file_name
    storage_parent_folder_id = split_request.storage_parent_folder_id
    sections_to_split_dicts = split_request.sections # List[SectionInfo] or List[Dict]

    print(f"Processing split request for file ID: {storage_file_id}")

    if not sections_to_split_dicts:
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                storage_file_id=storage_file_id,
                error="No sections provided for splitting."
            )
        )
    # Note: We no longer require storage_parent_folder_id since we're only uploading to Gemini AI
    # The storage service is still needed to download the original file for splitting

    original_pdf_stream: Optional[io.BytesIO] = None
    uploaded_files_info: List[UploadedFileInfo] = []

    try:
        # Get file info first to check size
        file_info = storage_service.get_file_info(storage_file_id)
        if file_info is None:
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="Failed to get file info for splitting."
                )
            )
        
        # Check file size before downloading
        file_size = file_info.get('size', 0)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            )
        
        original_pdf_stream = storage_service.download_file_content(storage_file_id)
        if original_pdf_stream is None:
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="Failed to download original file for splitting."
                )
            )
        
        # Convert SectionInfo objects to dictionaries for the PDF splitter service
        sections_as_dicts = [
            {
                "section_name": section.section_name,
                "page_range": section.page_range
            }
            for section in sections_to_split_dicts
        ]
        
        split_sections_output = await asyncio.to_thread(
            pdf_splitter_service.split_pdf_by_sections,
            original_pdf_stream,
            sections_as_dicts # Now properly formatted as List[Dict[str, str]]
        )

        if not split_sections_output: # This is List[Dict[str, Any]] from PdfSplitterService
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="PDF splitting failed or resulted in no sections."
                )
            )

        base_original_name = os.path.splitext(file_name)[0]

        for section_data in split_sections_output: # section_data is Dict from PdfSplitterService
            section_name_raw = section_data['section_name']
            section_file_stream = section_data['fileContent'] # This is an io.BytesIO
            section_file_name_part = section_data['fileName'] # Sanitized name part from splitter
            
            # Find the original page_range for this section
            page_range = ""
            for original_section in sections_to_split_dicts:
                if original_section.section_name == section_name_raw:
                    page_range = original_section.page_range
                    break
            
            # Upload to Gemini AI only
            genai_file_name = None
            if gemini_service:
                try:
                    # Create a unique display name for Gemini AI
                    unique_id = str(uuid.uuid4())[:8]
                    gemini_display_name = f"{base_original_name}_{section_name_raw}_{unique_id}.pdf"
                    
                    print(f"Uploading section '{section_name_raw}' as '{gemini_display_name}' to Gemini AI")
                    
                    # Create a copy of the stream for Gemini AI upload
                    section_file_stream_copy = io.BytesIO(section_file_stream.getvalue())
                    section_file_stream_copy.seek(0)
                    
                    gemini_file = await gemini_service.upload_pdf_for_analysis(section_file_stream_copy, gemini_display_name)
                    
                    if gemini_file:
                        genai_file_name = gemini_file.name
                        print(f"Successfully uploaded section '{section_name_raw}' to Gemini AI with name: {genai_file_name}")
                    else:
                        print(f"Failed to upload section '{section_name_raw}' to Gemini AI")
                    
                    section_file_stream_copy.close()
                except Exception as e:
                    print(f"Error uploading section '{section_name_raw}' to Gemini AI: {e}")
                    traceback.print_exc()
            else:
                print(f"Warning: No Gemini service available for section '{section_name_raw}'")
            
            section_file_stream.close() # Close stream after upload

            if genai_file_name: # Check if Gemini AI upload was successful
                uploaded_files_info.append(UploadedFileInfo(
                    section_name=section_name_raw,
                    page_range=page_range,
                    genai_file_name=genai_file_name
                ))
            else:
                print(f"Failed to upload section '{section_name_raw}' to Gemini AI.")
                # Decide if one failed upload should fail the whole item or just be omitted.
                # For now, it's omitted from success list.

        if not uploaded_files_info: # If no sections were successfully uploaded
            print(f"No sections were successfully uploaded for file ID {storage_file_id}.")
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="No sections were successfully uploaded to storage."
                )
            )

        print(f"Successfully split and uploaded {len(uploaded_files_info)} sections for file ID {storage_file_id}.")
        return BatchSplitItemResult(
            success=True,
            result=SplitResponseItemSuccess(
                storage_file_id=storage_file_id,
                file_name=file_name,
                storage_parent_folder_id=storage_parent_folder_id,
                sections=uploaded_files_info
            )
        )
    except Exception as ex:
        print(f"An unhandled error occurred processing split for file ID {storage_file_id}: {ex}")
        traceback.print_exc()
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                storage_file_id=storage_file_id,
                error="An internal server error occurred during splitting and upload.",
                detail=str(ex)
            )
        )
    finally:
        if original_pdf_stream:
            original_pdf_stream.close()

async def process_single_split_request_batched(
    split_request: SplitRequest,
    storage_service: StorageService,
    pdf_splitter_service: PdfSplitterService,
    gemini_service: Optional[GenerativeAnalysisService] = None,
    gcs_service: Optional[GoogleCloudStorageService] = None
) -> BatchSplitItemResult:
    """
    Process a split request using batched processing to manage memory usage.
    Processes sections in batches of 5 (configurable) with garbage collection between batches.
    """
    storage_file_id = split_request.storage_file_id
    file_name = split_request.file_name
    storage_parent_folder_id = split_request.storage_parent_folder_id
    sections_to_split_dicts = split_request.sections # List[SectionInfo] or List[Dict]

    print(f"Processing batched split request for file ID: {storage_file_id}")

    if not sections_to_split_dicts:
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                storage_file_id=storage_file_id,
                error="No sections provided for splitting."
            )
        )

    original_pdf_stream: Optional[io.BytesIO] = None
    uploaded_files_info: List[UploadedFileInfo] = []

    try:
        # Get file info first to check size
        file_info = storage_service.get_file_info(storage_file_id)
        if file_info is None:
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="Failed to get file info for splitting."
                )
            )
        
        # Check file size before downloading
        file_size = file_info.get('size', 0)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            )
        
        original_pdf_stream = storage_service.download_file_content(storage_file_id)
        if original_pdf_stream is None:
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="Failed to download original file for splitting."
                )
            )
        
        # Convert SectionInfo objects to dictionaries for the PDF splitter service
        sections_as_dicts = [
            {
                "section_name": section.section_name,
                "page_range": section.page_range
            }
            for section in sections_to_split_dicts
        ]
        
        # Use batched splitting
        split_sections_output = await asyncio.to_thread(
            pdf_splitter_service.split_pdf_by_sections_batched,
            original_pdf_stream,
            sections_as_dicts,
            settings.split_batch_size
        )

        if not split_sections_output:
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="PDF splitting failed or resulted in no sections."
                )
            )

        base_original_name = os.path.splitext(file_name)[0]

        # Process sections in batches for upload
        batch_size = settings.split_batch_size
        for i in range(0, len(split_sections_output), batch_size):
            batch_sections = split_sections_output[i:i + batch_size]
            batch_start = i + 1
            batch_end = min(i + batch_size, len(split_sections_output))
            
            print(f"Processing upload batch {batch_start}-{batch_end} of {len(split_sections_output)} sections.")
            
            batch_uploaded_files = await _process_upload_batch(
                batch_sections, 
                sections_to_split_dicts, 
                base_original_name, 
                gemini_service,
                gcs_service
            )
            uploaded_files_info.extend(batch_uploaded_files)
            
            # Force garbage collection after each batch
            gc.collect()
            print(f"Completed upload batch {batch_start}-{batch_end}, garbage collection performed.")
            
            # Delay between batches if configured
            if settings.split_batch_delay_seconds > 0 and i + batch_size < len(split_sections_output):
                await asyncio.sleep(settings.split_batch_delay_seconds)

        if not uploaded_files_info:
            print(f"No sections were successfully uploaded for file ID {storage_file_id}.")
            return BatchSplitItemResult(
                success=False,
                error_info=SplitResponseItemError(
                    storage_file_id=storage_file_id,
                    error="No sections were successfully uploaded to storage."
                )
            )

        print(f"Successfully split and uploaded {len(uploaded_files_info)} sections for file ID {storage_file_id}.")
        return BatchSplitItemResult(
            success=True,
            result=SplitResponseItemSuccess(
                storage_file_id=storage_file_id,
                file_name=file_name,
                storage_parent_folder_id=storage_parent_folder_id,
                sections=uploaded_files_info
            )
        )
    except Exception as ex:
        print(f"An unhandled error occurred processing batched split for file ID {storage_file_id}: {ex}")
        traceback.print_exc()
        return BatchSplitItemResult(
            success=False,
            error_info=SplitResponseItemError(
                storage_file_id=storage_file_id,
                error="An internal server error occurred during batched splitting and upload.",
                detail=str(ex)
            )
        )
    finally:
        if original_pdf_stream:
            original_pdf_stream.close()

async def _process_upload_batch(
    batch_sections: List[Dict[str, Any]],
    original_sections: List[Any],
    base_original_name: str,
    gemini_service: Optional[GenerativeAnalysisService],
    gcs_service: Optional[GoogleCloudStorageService] = None
) -> List[UploadedFileInfo]:
    """
    Process a batch of sections for upload to Gemini AI and Google Cloud Storage.
    
    Args:
        batch_sections: List of section data from PDF splitter
        original_sections: Original section info for page range lookup
        base_original_name: Base filename for creating display names
        gemini_service: Gemini AI service for uploads
        gcs_service: Google Cloud Storage service for uploads
        
    Returns:
        List of successfully uploaded file info
    """
    batch_uploaded_files = []
    
    for section_data in batch_sections:
        section_name_raw = section_data['section_name']
        section_file_stream = section_data['fileContent'] # This is an io.BytesIO
        section_file_name_part = section_data['fileName'] # Sanitized name part from splitter
        
        # Find the original page_range for this section
        page_range = ""
        for original_section in original_sections:
            if original_section.section_name == section_name_raw:
                page_range = original_section.page_range
                break
        
        # Upload to Gemini AI and Google Cloud Storage
        genai_file_name = None
        gcs_url = None
        
        if gemini_service:
            try:
                # Create a unique display name for Gemini AI
                unique_id = str(uuid.uuid4())[:8]
                gemini_display_name = f"{base_original_name}_{section_name_raw}_{unique_id}.pdf"
                
                print(f"Uploading section '{section_name_raw}' as '{gemini_display_name}' to Gemini AI")
                
                # Create a copy of the stream for Gemini AI upload
                section_file_stream_copy = io.BytesIO(section_file_stream.getvalue())
                section_file_stream_copy.seek(0)
                
                gemini_file = await gemini_service.upload_pdf_for_analysis(section_file_stream_copy, gemini_display_name)
                
                if gemini_file:
                    genai_file_name = gemini_file.name
                    print(f"Successfully uploaded section '{section_name_raw}' to Gemini AI with name: {genai_file_name}")
                else:
                    print(f"Failed to upload section '{section_name_raw}' to Gemini AI")
                
                section_file_stream_copy.close()
            except Exception as e:
                print(f"Error uploading section '{section_name_raw}' to Gemini AI: {e}")
                traceback.print_exc()
        else:
            print(f"Warning: No Gemini service available for section '{section_name_raw}'")
        
        # Upload to Google Cloud Storage
        if gcs_service:
            try:
                # Create a unique filename for GCS
                unique_id = str(uuid.uuid4())[:8]
                gcs_filename = f"split_sections/{base_original_name}/{section_name_raw}_{unique_id}.pdf"
                
                print(f"Uploading section '{section_name_raw}' as '{gcs_filename}' to Google Cloud Storage")
                
                # Create a copy of the stream for GCS upload
                section_file_stream_copy_gcs = io.BytesIO(section_file_stream.getvalue())
                section_file_stream_copy_gcs.seek(0)
                
                # Add metadata for better organization
                metadata = {
                    'original_file': base_original_name,
                    'section_name': section_name_raw,
                    'page_range': page_range,
                    'upload_type': 'split_section'
                }
                
                gcs_url = gcs_service.upload_file_with_metadata(
                    section_file_stream_copy_gcs, 
                    gcs_filename, 
                    metadata
                )
                
                if gcs_url:
                    print(f"Successfully uploaded section '{section_name_raw}' to GCS: {gcs_url}")
                else:
                    print(f"Failed to upload section '{section_name_raw}' to GCS")
                
                section_file_stream_copy_gcs.close()
            except Exception as e:
                print(f"Error uploading section '{section_name_raw}' to GCS: {e}")
                traceback.print_exc()
        else:
            print(f"Warning: No GCS service available for section '{section_name_raw}'")
        
        section_file_stream.close() # Close stream after upload

        if genai_file_name: # Check if Gemini AI upload was successful
            batch_uploaded_files.append(UploadedFileInfo(
                section_name=section_name_raw,
                page_range=page_range,
                genai_file_name=genai_file_name,
                gcs_url=gcs_url  # Add GCS URL to the response
            ))
        else:
            print(f"Failed to upload section '{section_name_raw}' to Gemini AI.")
    
    return batch_uploaded_files