# helpers/refactored_extract_helpers.py

import io
import os
import time
import uuid
import re
import traceback
import asyncio
from collections import deque
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel, Field
from google.generativeai import types as genai_types_google

from models import (
    RefactoredExtractResponse,
    SectionExtractPrompt,
    SectionWithPrompts,
    AnalyzeResultWithPrompts,
    AnalyzeResponseItemSuccess,
    ExtractRequest,
    ExtractResponse
)
from services.google_drive_service import StorageService
from services.generative_analysis_service import GenerativeAnalysisService
from services.pdf_splitter_service import PdfSplitterService

from config import settings


class RefactoredExtractionContext(BaseModel):
    target_drive_file_id: str
    target_drive_file_name: Optional[str] = None
    original_pdf_stream: Optional[io.BytesIO] = None
    section_pdf_streams: Dict[str, io.BytesIO] = Field(default_factory=dict)
    section_gemini_files: Dict[str, genai_types_google.File] = Field(default_factory=dict)
    section_results: Dict[str, List[SectionExtractPrompt]] = Field(default_factory=dict)

    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True
    }


async def _execute_section_extraction_api_call(
    gemini_service: GenerativeAnalysisService,
    extraction_ctx: RefactoredExtractionContext,
    section_name: str,
    page_range: str,
    prompt: SectionExtractPrompt,
    api_attempt_count: int,
    api_retry_queue: deque
) -> bool:
    """Execute API call for a single prompt on a section using the section's split PDF"""
    target_id_log = extraction_ctx.target_drive_file_id
    print(f"API Call (Refactored Extract): Section '{section_name}', Prompt '{prompt.prompt_name}', API Attempt {api_attempt_count + 1}.")

    # Get the Gemini file for this section
    section_gemini_file = extraction_ctx.section_gemini_files.get(section_name)
    if not section_gemini_file:
        print(f"Error (Refactored Extract): Section Gemini File not found for Section '{section_name}', Prompt '{prompt.prompt_name}'.")
        prompt.result = "Internal error: Section Gemini File was not available for multimodal prompt."
        return False

    # Add section context and page range to the prompt
    section_context = f"Focus on the section '{section_name}' when extracting information."
    page_range_context = f"This extraction applies to pages: {page_range}"
    final_instructions = f"{section_context}\n{page_range_context}\n\n{prompt.prompt_text}"
    final_instructions += "\nEnsure the output is ONLY the requested information for this specific section."

    multimodal_prompt_parts = [
        section_gemini_file,
        final_instructions
    ]

    # For multimodal prompts (file + text), use generate_content_async directly
    try:
        response = await gemini_service.model.generate_content_async(multimodal_prompt_parts)
        if response and response.text:
            api_output_data = response.text
            status = "SUCCESS"
        else:
            api_output_data = "No response text received"
            status = "ERROR_EMPTY"
    except Exception as e:
        api_output_data = str(e)
        status = "ERROR_API"

    if status == "SUCCESS":
        prompt.result = api_output_data
        print(f"SUCCESS (Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status} (Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'. Error: {str(api_output_data)[:100]}")
        if api_attempt_count + 1 < settings.max_api_retries:
            print(f"Re-queuing for API retry (Extract): Section '{section_name}', Prompt '{prompt.prompt_name}' (API attempt {api_attempt_count + 2}).")
            retry_task_details = {
                "type": "extract_api_retry",
                "section_name": section_name,
                "page_range": page_range,
                "prompt": prompt,
                "api_attempt_count": api_attempt_count + 1
            }
            api_retry_queue.append(retry_task_details)
        else:
            err_msg = f"Max API retries ({settings.max_api_retries}) for extraction: Section '{section_name}', Prompt '{prompt.prompt_name}'. Last: [{status}] {str(api_output_data)[:100]}"
            print(err_msg)
            prompt.result = f"Error after {settings.max_api_retries} retries: {str(api_output_data)[:100]}"
        return True
    else:
        err_msg = f"Permanent Error (Extract): Section '{section_name}', Prompt '{prompt.prompt_name}': [{status}] {str(api_output_data)[:100]}"
        print(err_msg)
        prompt.result = f"Permanent error: {str(api_output_data)[:100]}"
        return False


async def _split_and_upload_sections(
    extraction_ctx: RefactoredExtractionContext,
    sections: List[SectionWithPrompts],
    storage_service: StorageService,
    gemini_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> bool:
    """Split the PDF into sections and upload each section to Gemini AI"""
    try:
        # Download the original PDF
        print(f"Downloading original PDF: {extraction_ctx.target_drive_file_id}")
        extraction_ctx.original_pdf_stream = storage_service.download_file_content(extraction_ctx.target_drive_file_id)
        if not extraction_ctx.original_pdf_stream or extraction_ctx.original_pdf_stream.getbuffer().nbytes == 0:
            print("Failed to download original PDF")
            return False

        # Prepare sections for splitting
        sections_for_splitting = []
        for section in sections:
            sections_for_splitting.append({
                "sectionName": section.sectionName,
                "pageRange": section.pageRange
            })

        # Split the PDF into sections
        print(f"Splitting PDF into {len(sections_for_splitting)} sections")
        split_results = pdf_splitter_service.split_pdf_by_sections(
            extraction_ctx.original_pdf_stream, 
            sections_for_splitting
        )

        if not split_results:
            print("Failed to split PDF into sections")
            return False

        # Upload each section to Gemini AI
        print(f"Uploading {len(split_results)} sections to Gemini AI")
        for split_result in split_results:
            section_name = split_result["sectionName"]
            pdf_stream = split_result["fileContent"]
            
            # Create a unique display name for this section
            display_name = f"{extraction_ctx.target_drive_file_name}_{section_name}_{uuid.uuid4().hex[:8]}"
            
            print(f"Uploading section '{section_name}' to Gemini AI")
            gemini_file = await gemini_service.upload_pdf_for_analysis(pdf_stream, display_name)
            
            if gemini_file:
                extraction_ctx.section_gemini_files[section_name] = gemini_file
                extraction_ctx.section_pdf_streams[section_name] = pdf_stream
                print(f"Successfully uploaded section '{section_name}' to Gemini AI")
            else:
                print(f"Failed to upload section '{section_name}' to Gemini AI")
                return False

        return True

    except Exception as e:
        print(f"Error during PDF splitting and upload: {e}")
        traceback.print_exc()
        return False


async def _cleanup_section_files(extraction_ctx: RefactoredExtractionContext, gemini_service: GenerativeAnalysisService):
    """Clean up section files from Gemini AI and close streams"""
    try:
        # Delete Gemini AI files
        for section_name, gemini_file in extraction_ctx.section_gemini_files.items():
            try:
                await gemini_service.delete_file(gemini_file)
                print(f"Deleted Gemini AI file for section '{section_name}'")
            except Exception as e:
                print(f"Error deleting Gemini AI file for section '{section_name}': {e}")

        # Close PDF streams
        for section_name, pdf_stream in extraction_ctx.section_pdf_streams.items():
            try:
                pdf_stream.close()
                print(f"Closed PDF stream for section '{section_name}'")
            except Exception as e:
                print(f"Error closing PDF stream for section '{section_name}': {e}")

        # Close original PDF stream
        if extraction_ctx.original_pdf_stream:
            try:
                extraction_ctx.original_pdf_stream.close()
                print("Closed original PDF stream")
            except Exception as e:
                print(f"Error closing original PDF stream: {e}")

    except Exception as e:
        print(f"Error during cleanup: {e}")


async def process_refactored_extract_request(
    request: AnalyzeResponseItemSuccess,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService
) -> RefactoredExtractResponse:
    """Process the refactored extract request that accepts analyze response data directly"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Refactored Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    # Initialize PDF splitter service
    pdf_splitter_service = PdfSplitterService()

    try:
        # Transform the sections to include prompts for extraction
        # Use prompts from the request if available, otherwise create a default prompt
        sections_with_prompts = []
        for section in request.sections:
            # Check if the section already has prompts
            if section.prompts:
                # Use the existing prompts from the section
                section_with_prompts = SectionWithPrompts(
                    prompts=section.prompts,
                    pageRange=section.pageRange,
                    sectionName=section.sectionName
                )
            else:
                # Create a default extraction prompt for sections without prompts
                default_prompt = SectionExtractPrompt(
                    prompt_name="extract_content",
                    prompt_text="Extract all relevant content from this section, including any key information, data, or important details."
                )
                
                section_with_prompts = SectionWithPrompts(
                    prompts=[default_prompt],
                    pageRange=section.pageRange,
                    sectionName=section.sectionName
                )
            sections_with_prompts.append(section_with_prompts)

        # Split PDF into sections and upload each to Gemini AI
        if not await _split_and_upload_sections(
            extraction_ctx, 
            sections_with_prompts, 
            storage_service, 
            gemini_analysis_service,
            pdf_splitter_service
        ):
            return RefactoredExtractResponse(
                success=False,
                result=None,
                error="Failed to split PDF into sections and upload to Gemini AI"
            )

        # Create the transformed request structure
        transformed_request = AnalyzeResultWithPrompts(
            originalDriveFileId=request.originalDriveFileId,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=sections_with_prompts
        )

        # Process each section's prompts
        api_retry_queue = deque()
        data_dependency_deferred_queue = deque()

        # Queue all prompts for processing
        for section in transformed_request.sections:
            for prompt in section.prompts:
                data_dependency_deferred_queue.append({
                    "type": "refactored_extraction_data_dependency",
                    "section_name": section.sectionName,
                    "page_range": section.pageRange,
                    "prompt": prompt,
                    "dd_attempt_count": 0
                })

        last_rate_limit_time = None
        processing_cycles = 0
        total_prompts = sum(len(section.prompts) for section in transformed_request.sections)
        max_cycles = total_prompts * (settings.max_api_retries + settings.max_data_dependency_retries + 2)

        while data_dependency_deferred_queue or api_retry_queue:
            processing_cycles += 1
            if processing_cycles > max_cycles:
                print(f"Warning (Refactored Extract): Max processing cycles reached. Breaking.")
                break

            # Process API retry queue
            if api_retry_queue:
                if not (last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds)):
                    api_task_details = api_retry_queue.popleft()
                    if api_task_details.get("type") == "refactored_extraction_api_retry":
                        rate_limit_hit = await _execute_section_extraction_api_call(
                            gemini_analysis_service,
                            extraction_ctx,
                            api_task_details["section_name"],
                            api_task_details["page_range"],
                            api_task_details["prompt"],
                            api_task_details["api_attempt_count"],
                            api_retry_queue
                        )
                        if rate_limit_hit:
                            last_rate_limit_time = time.monotonic()
                    else:
                        api_retry_queue.appendleft(api_task_details)
                    await asyncio.sleep(0.1)
                    continue

            # Process data dependency queue
            if data_dependency_deferred_queue:
                dd_task_details = data_dependency_deferred_queue.popleft()
                if dd_task_details.get("type") == "refactored_extraction_data_dependency":
                    section_name = dd_task_details["section_name"]
                    prompt = dd_task_details["prompt"]
                    dd_attempts = dd_task_details["dd_attempt_count"]

                    # Check if we're in rate limit cooldown
                    if last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds):
                        dd_task_details["dd_attempt_count"] = dd_attempts
                        data_dependency_deferred_queue.append(dd_task_details)
                        await asyncio.sleep(0.1)
                        continue

                    # Execute the API call
                    api_call_requeued = await _execute_section_extraction_api_call(
                        gemini_analysis_service,
                        extraction_ctx,
                        section_name,
                        dd_task_details["page_range"],
                        prompt,
                        0,
                        api_retry_queue
                    )
                    if api_call_requeued:
                        last_rate_limit_time = time.monotonic()

                else:
                    data_dependency_deferred_queue.appendleft(dd_task_details)
                await asyncio.sleep(0.05)
                continue

            # Handle cooldown periods
            if not data_dependency_deferred_queue and not api_retry_queue and \
               last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds):
                await asyncio.sleep(0.2)

        # Handle any remaining tasks in queues
        if data_dependency_deferred_queue or api_retry_queue:
            print(f"Warning (Refactored Extract): Queues not empty. DataQ:{len(data_dependency_deferred_queue)}, ApiQ:{len(api_retry_queue)}")
            for task in list(data_dependency_deferred_queue):
                if task.get("type") == "refactored_extraction_data_dependency":
                    task["prompt"].result = "Processing cycle limit reached while waiting for data dependency."

        # Build the response
        print(f"Finished processing refactored extract for file ID: {target_file_id}")
        return RefactoredExtractResponse(
            success=True,
            result=transformed_request,
            genai_file_name=None  # No single file name since we use multiple section files
        )

    except Exception as ex:
        print(f"Unhandled critical error processing refactored extract for file {target_file_id}: {ex}")
        traceback.print_exc()
        return RefactoredExtractResponse(
            success=False,
            result=None,
            error=f"An internal server error occurred during extraction: {str(ex)}"
        )
    finally:
        # Clean up section files and streams
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)


async def process_extract_request(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService
) -> ExtractResponse:
    """Process the extract request with multiple sections and sibling prompts array"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    # Initialize PDF splitter service
    pdf_splitter_service = PdfSplitterService()

    try:
        # Transform sections to include prompts for splitting
        sections_with_prompts = []
        for section in request.sections:
            # Create a copy of the prompt for this section
            section_prompt = SectionExtractPrompt(
                prompt_name=request.prompt.prompt_name,
                prompt_text=request.prompt.prompt_text,
                result=None
            )
            
            section_with_prompts = SectionWithPrompts(
                prompts=[section_prompt],
                pageRange=section.pageRange,
                sectionName=section.sectionName
            )
            sections_with_prompts.append(section_with_prompts)

        # Split PDF into sections and upload each to Gemini AI
        if not await _split_and_upload_sections(
            extraction_ctx, 
            sections_with_prompts, 
            storage_service, 
            gemini_analysis_service,
            pdf_splitter_service
        ):
            return ExtractResponse(
                success=False,
                originalDriveFileId=target_file_id,
                originalDriveFileName=request.originalDriveFileName,
                originalDriveParentFolderId=request.originalDriveParentFolderId,
                sections=request.sections,
                prompt=request.prompt,
                error="Failed to split PDF into sections and upload to Gemini AI",
                genai_file_name=None
            )

        # Process the single prompt against each section
        api_retry_queue = deque()
        data_dependency_deferred_queue = deque()

        # Queue all sections for processing with the single prompt
        for section in request.sections:
            # Create a copy of the prompt for this section to avoid modifying the original
            section_prompt = SectionExtractPrompt(
                prompt_name=request.prompt.prompt_name,
                prompt_text=request.prompt.prompt_text,
                result=None
            )
            data_dependency_deferred_queue.append({
                "type": "extract_data_dependency",
                "section_name": section.sectionName,
                "page_range": section.pageRange,
                "prompt": section_prompt,
                "section": section,
                "dd_attempt_count": 0
            })

        last_rate_limit_time = None
        processing_cycles = 0
        total_sections = len(request.sections)
        max_cycles = total_sections * (settings.max_api_retries + settings.max_data_dependency_retries + 2)

        while data_dependency_deferred_queue or api_retry_queue:
            processing_cycles += 1
            if processing_cycles > max_cycles:
                print(f"Warning (Extract): Max processing cycles reached. Breaking.")
                break

            # Process API retry queue
            if api_retry_queue:
                if not (last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds)):
                    api_task_details = api_retry_queue.popleft()
                    if api_task_details.get("type") == "extract_api_retry":
                        rate_limit_hit = await _execute_section_extraction_api_call(
                            gemini_analysis_service,
                            extraction_ctx,
                            api_task_details["section_name"],
                            api_task_details["page_range"],
                            api_task_details["prompt"],
                            api_task_details["api_attempt_count"],
                            api_retry_queue
                        )
                        if rate_limit_hit:
                            last_rate_limit_time = time.monotonic()
                    else:
                        api_retry_queue.appendleft(api_task_details)
                    await asyncio.sleep(0.1)
                    continue

            # Process data dependency queue
            if data_dependency_deferred_queue:
                dd_task_details = data_dependency_deferred_queue.popleft()
                if dd_task_details.get("type") == "extract_data_dependency":
                    prompt = dd_task_details["prompt"]
                    section = dd_task_details["section"]
                    dd_attempts = dd_task_details["dd_attempt_count"]

                    # Check if we're in rate limit cooldown
                    if last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds):
                        dd_task_details["dd_attempt_count"] = dd_attempts
                        data_dependency_deferred_queue.append(dd_task_details)
                        await asyncio.sleep(0.1)
                        continue

                    # Execute the API call
                    api_call_requeued = await _execute_section_extraction_api_call(
                        gemini_analysis_service,
                        extraction_ctx,
                        section.sectionName,
                        section.pageRange,
                        prompt,
                        0,
                        api_retry_queue
                    )
                    
                    # Add the prompt to the section's prompts array
                    if section.prompts is None:
                        section.prompts = []
                    if prompt not in section.prompts:
                        section.prompts.append(prompt)
                    
                    if api_call_requeued:
                        last_rate_limit_time = time.monotonic()

                else:
                    data_dependency_deferred_queue.appendleft(dd_task_details)
                await asyncio.sleep(0.05)
                continue

            # Handle cooldown periods
            if not data_dependency_deferred_queue and not api_retry_queue and \
               last_rate_limit_time and (time.monotonic() - last_rate_limit_time < settings.retry_cooldown_seconds):
                await asyncio.sleep(0.2)

        # Handle any remaining tasks in queues
        if data_dependency_deferred_queue or api_retry_queue:
            print(f"Warning (Extract): Queues not empty. DataQ:{len(data_dependency_deferred_queue)}, ApiQ:{len(api_retry_queue)}")
            for task in list(data_dependency_deferred_queue):
                if task.get("type") == "extract_data_dependency":
                    task["prompt"].result = "Processing cycle limit reached while waiting for data dependency."

        # Build the response
        print(f"Finished processing extract for file ID: {target_file_id}")
        return ExtractResponse(
            success=True,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=request.sections,
            prompt=request.prompt,
            genai_file_name=None  # No single file name since we use multiple section files
        )

    except Exception as ex:
        print(f"Unhandled critical error processing extract for file {target_file_id}: {ex}")
        traceback.print_exc()
        return ExtractResponse(
            success=False,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=request.sections,
            prompt=request.prompt,
            error=f"An internal server error occurred during extraction: {str(ex)}",
            genai_file_name=None
        )
    finally:
        # Clean up section files and streams
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service) 