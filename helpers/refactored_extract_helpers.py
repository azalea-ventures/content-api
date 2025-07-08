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

from config import settings


class RefactoredExtractionContext(BaseModel):
    target_drive_file_id: str
    target_drive_file_name: Optional[str] = None
    uploaded_target_gfile: Optional[genai_types_google.File] = None
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
    """Execute API call for a single prompt on a section"""
    target_id_log = extraction_ctx.target_drive_file_id
    print(f"API Call (Refactored Extract): Section '{section_name}', Prompt '{prompt.prompt_name}', API Attempt {api_attempt_count + 1}.")

    if not extraction_ctx.uploaded_target_gfile:
        print(f"Error (Refactored Extract): Target Google AI File not found for Section '{section_name}', Prompt '{prompt.prompt_name}'.")
        prompt.result = "Internal error: Target Google AI File was not available for multimodal prompt."
        return False

    # Add section context and page range to the prompt
    section_context = f"Focus on the section '{section_name}' when extracting information."
    page_range_context = f"This extraction applies to pages: {page_range}"
    final_instructions = f"{section_context}\n{page_range_context}\n\n{prompt.prompt_text}"
    final_instructions += "\nEnsure the output is ONLY the requested information for this specific section."

    multimodal_prompt_parts = [
        extraction_ctx.uploaded_target_gfile,
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

    try:
        if request.genai_file_name:
            extraction_ctx.uploaded_target_gfile = await gemini_analysis_service.get_file_by_name(request.genai_file_name)
            if not extraction_ctx.uploaded_target_gfile:
                return RefactoredExtractResponse(
                    success=False,
                    result=None,
                    error=f"Failed to retrieve existing Gemini file: {request.genai_file_name}"
                )
        else:
            target_file_info = storage_service.get_file_info(target_file_id)
            if target_file_info is None:
                return RefactoredExtractResponse(
                    success=False,
                    result=None,
                    error="Target Drive file not found or permission denied."
                )
            file_size = target_file_info.get('size', 0)
            if file_size > 50 * 1024 * 1024:
                return RefactoredExtractResponse(
                    success=False,
                    result=None,
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            extraction_ctx.uploaded_target_gfile = await gemini_analysis_service.upload_pdf_for_analysis_by_file_id(
                request.originalDriveFileId,
                request.originalDriveFileName,
                storage_service
            )
            if extraction_ctx.uploaded_target_gfile is None:
                return RefactoredExtractResponse(
                    success=False,
                    result=None,
                    error="Failed to upload target document for AI extraction."
                )

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
                    sectionName=section.sectionName,
                    pages=section.pages
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
                    sectionName=section.sectionName,
                    pages=section.pages
                )
            sections_with_prompts.append(section_with_prompts)

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
            genai_file_name=extraction_ctx.uploaded_target_gfile.name
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
        # Note: File cleanup is not performed here as files are needed for subsequent processing
        # Gemini AI automatically cleans up unused files after a few hours
        pass 


async def process_extract_request(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService
) -> ExtractResponse:
    """Process the extract request with a single section and sibling prompts array"""
    
    target_file_id = request.originalDriveFileId
    section_name = request.section.sectionName
    page_range = request.section.pageRange
    print(f"Processing Extract Request for file ID: {target_file_id}, section: {section_name}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    try:
        if request.genai_file_name:
            extraction_ctx.uploaded_target_gfile = await gemini_analysis_service.get_file_by_name(request.genai_file_name)
            if not extraction_ctx.uploaded_target_gfile:
                return ExtractResponse(
                    success=False,
                    originalDriveFileId=target_file_id,
                    originalDriveFileName=request.originalDriveFileName,
                    originalDriveParentFolderId=request.originalDriveParentFolderId,
                    section=request.section,
                    prompts=request.prompts,
                    error=f"Failed to retrieve existing Gemini file: {request.genai_file_name}",
                    genai_file_name=None
                )
        else:
            # Upload the target file to Gemini AI
            print(f"Uploading target file to Gemini AI: {target_file_id}")
            extraction_ctx.uploaded_target_gfile = await gemini_analysis_service.upload_file_to_gemini(
                target_file_id, storage_service
            )
            if not extraction_ctx.uploaded_target_gfile:
                return ExtractResponse(
                    success=False,
                    originalDriveFileId=target_file_id,
                    originalDriveFileName=request.originalDriveFileName,
                    originalDriveParentFolderId=request.originalDriveParentFolderId,
                    section=request.section,
                    prompts=request.prompts,
                    error="Failed to upload target file to Gemini AI",
                    genai_file_name=None
                )

        # Process each prompt for the section
        api_retry_queue = deque()
        data_dependency_deferred_queue = deque()

        # Queue all prompts for processing
        for prompt in request.prompts:
            data_dependency_deferred_queue.append({
                "type": "extract_data_dependency",
                "section_name": section_name,
                "page_range": page_range,
                "prompt": prompt,
                "dd_attempt_count": 0
            })

        last_rate_limit_time = None
        processing_cycles = 0
        total_prompts = len(request.prompts)
        max_cycles = total_prompts * (settings.max_api_retries + settings.max_data_dependency_retries + 2)

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
                        page_range,
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
            print(f"Warning (Extract): Queues not empty. DataQ:{len(data_dependency_deferred_queue)}, ApiQ:{len(api_retry_queue)}")
            for task in list(data_dependency_deferred_queue):
                if task.get("type") == "extract_data_dependency":
                    task["prompt"].result = "Processing cycle limit reached while waiting for data dependency."

        # Build the response
        print(f"Finished processing extract for file ID: {target_file_id}, section: {section_name}")
        return ExtractResponse(
            success=True,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            section=request.section,
            prompts=request.prompts,
            genai_file_name=extraction_ctx.uploaded_target_gfile.name
        )

    except Exception as ex:
        print(f"Unhandled critical error processing extract for file {target_file_id}, section {section_name}: {ex}")
        traceback.print_exc()
        return ExtractResponse(
            success=False,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            section=request.section,
            prompts=request.prompts,
            error=f"An internal server error occurred during extraction: {str(ex)}",
            genai_file_name=None
        )
    finally:
        # Note: File cleanup is not performed here as files are needed for subsequent processing
        # Gemini AI automatically cleans up unused files after a few hours
        pass 