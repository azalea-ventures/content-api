# helpers/refactored_extract_helpers.py

import io
import os
import time
import uuid
import re
import traceback
import asyncio
import psutil
from collections import deque
from asyncio import Semaphore
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
    genai_file: Optional[genai_types_google.File] = None
    genai_file_name: Optional[str] = None
    # New fields for section-based extraction
    section_gemini_files: Dict[str, genai_types_google.File] = Field(default_factory=dict)
    section_pdf_streams: Dict[str, io.BytesIO] = Field(default_factory=dict)
    use_section_splitting: bool = True  # Default to using section splitting

    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True
    }


async def _split_and_upload_sections(
    extraction_ctx: RefactoredExtractionContext,
    storage_service: StorageService,
    gemini_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService,
    sections: List[Any]
) -> bool:
    """Split PDF into sections and upload each section as a separate file to Gemini AI"""
    try:
        print(f"Splitting PDF into {len(sections)} sections for file ID: {extraction_ctx.target_drive_file_id}")
        
        # Download the original PDF
        original_pdf_stream = storage_service.download_file_content(extraction_ctx.target_drive_file_id)
        if not original_pdf_stream or original_pdf_stream.getbuffer().nbytes == 0:
            print("Failed to download original PDF for splitting")
            return False
        
        # Convert sections to the format expected by the PDF splitter
        sections_for_splitting = [
            {
                "sectionName": section.sectionName,
                "pageRange": section.pageRange
            }
            for section in sections
        ]
        
        # Split the PDF into sections
        split_results = await asyncio.to_thread(
            pdf_splitter_service.split_pdf_by_sections,
            original_pdf_stream,
            sections_for_splitting
        )
        
        if not split_results:
            print("PDF splitting failed or resulted in no sections")
            original_pdf_stream.close()
            return False
        
        # Upload each section to Gemini AI
        base_filename = os.path.splitext(extraction_ctx.target_drive_file_name or "document")[0]
        
        for split_result in split_results:
            section_name = split_result["sectionName"]
            pdf_stream = split_result["fileContent"]
            
            # Create a unique display name for this section
            unique_id = str(uuid.uuid4())[:8]
            display_name = f"{base_filename}_{section_name}_{unique_id}.pdf"
            
            print(f"Uploading section '{section_name}' as '{display_name}' to Gemini AI")
            
            # Upload the section to Gemini AI
            gemini_file = await gemini_service.upload_pdf_for_analysis(pdf_stream, display_name)
            
            if gemini_file:
                extraction_ctx.section_gemini_files[section_name] = gemini_file
                extraction_ctx.section_pdf_streams[section_name] = pdf_stream
                print(f"Successfully uploaded section '{section_name}' to Gemini AI")
            else:
                print(f"Failed to upload section '{section_name}' to Gemini AI")
                pdf_stream.close()
        
        # Close the original PDF stream
        original_pdf_stream.close()
        
        print(f"Successfully split and uploaded {len(extraction_ctx.section_gemini_files)} sections")
        return len(extraction_ctx.section_gemini_files) > 0
        
    except Exception as e:
        print(f"Error during PDF splitting and upload: {e}")
        traceback.print_exc()
        return False


async def _cleanup_section_files(extraction_ctx: RefactoredExtractionContext, gemini_service: GenerativeAnalysisService):
    """Clean up section files from Gemini AI and close PDF streams"""
    try:
        # Delete section files from Gemini AI
        for section_name, gemini_file in extraction_ctx.section_gemini_files.items():
            try:
                await gemini_service.delete_file(gemini_file)
                print(f"Deleted section file '{section_name}' from Gemini AI")
            except Exception as e:
                print(f"Error deleting section file '{section_name}' from Gemini AI: {e}")
        
        # Close PDF streams
        for section_name, pdf_stream in extraction_ctx.section_pdf_streams.items():
            try:
                pdf_stream.close()
                print(f"Closed PDF stream for section '{section_name}'")
            except Exception as e:
                print(f"Error closing PDF stream for section '{section_name}': {e}")
        
        # Clear the dictionaries
        extraction_ctx.section_gemini_files.clear()
        extraction_ctx.section_pdf_streams.clear()
        
    except Exception as e:
        print(f"Error during section file cleanup: {e}")


async def _execute_section_extraction_api_call(
    gemini_service: GenerativeAnalysisService,
    extraction_ctx: RefactoredExtractionContext,
    section_name: str,
    page_range: str,
    prompt: SectionExtractPrompt,
    api_attempt_count: int,
    api_retry_queue: deque
) -> bool:
    """Execute API call for a single prompt on a section using section-specific files"""
    target_id_log = extraction_ctx.target_drive_file_id
    print(f"API Call (Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}', API Attempt {api_attempt_count + 1}.")

    # Get the section-specific Gemini file
    if extraction_ctx.use_section_splitting:
        genai_file = extraction_ctx.section_gemini_files.get(section_name)
        if not genai_file:
            print(f"Error (Section Extract): Section Gemini File not found for section '{section_name}', Prompt '{prompt.prompt_name}'.")
            prompt.result = "Internal error: Section Gemini File was not available for multimodal prompt."
            return False
    else:
        # Fallback to the original single file approach
        genai_file = extraction_ctx.genai_file
        if not genai_file:
            print(f"Error (Section Extract): Gemini File not found for file ID '{target_id_log}', Prompt '{prompt.prompt_name}'.")
            prompt.result = "Internal error: Gemini File was not available for multimodal prompt."
            return False

    # Add section context and section number to the prompt
    section_context = f"Focus on the section '{section_name}' when extracting information."
    
    # Extract section number from section name if it contains a number
    section_number = ""
    section_number_match = re.search(r'(\d+)', section_name)
    if section_number_match:
        section_number = f"Section number: {section_number_match.group(1)}. "
    
    final_instructions = f"{section_context}\n{section_number}\n\n{prompt.prompt_text}"
    final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section."

    multimodal_prompt_parts = [
        genai_file,
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
        print(f"SUCCESS (Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status} (Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'. Error: {str(api_output_data)[:100]}")
        if api_attempt_count + 1 < settings.max_api_retries:
            print(f"Re-queuing for API retry (Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}' (API attempt {api_attempt_count + 2}).")
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
        err_msg = f"Permanent Error (Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}': [{status}] {str(api_output_data)[:100]}"
        print(err_msg)
        prompt.result = f"Permanent error: {str(api_output_data)[:100]}"
        return False


async def _execute_section_extraction_api_call_concurrent(
    gemini_service: GenerativeAnalysisService,
    extraction_ctx: RefactoredExtractionContext,
    section_name: str,
    page_range: str,
    prompt: SectionExtractPrompt,
    api_attempt_count: int
) -> Dict[str, Any]:
    """Execute API call for a single prompt on a section - concurrent version with section files"""
    target_id_log = extraction_ctx.target_drive_file_id
    print(f"API Call (Concurrent Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}', API Attempt {api_attempt_count + 1}.")

    # Get the section-specific Gemini file
    if extraction_ctx.use_section_splitting:
        genai_file = extraction_ctx.section_gemini_files.get(section_name)
        if not genai_file:
            print(f"Error (Concurrent Section Extract): Section Gemini File not found for section '{section_name}', Prompt '{prompt.prompt_name}'.")
            return {
                "success": False,
                "section_name": section_name,
                "prompt": prompt,
                "error": "Internal error: Section Gemini File was not available for multimodal prompt.",
                "rate_limit_hit": False
            }
    else:
        # Fallback to the original single file approach
        genai_file = extraction_ctx.genai_file
        if not genai_file:
            print(f"Error (Concurrent Section Extract): Gemini File not found for file ID '{target_id_log}', Prompt '{prompt.prompt_name}'.")
            return {
                "success": False,
                "section_name": section_name,
                "prompt": prompt,
                "error": "Internal error: Gemini File was not available for multimodal prompt.",
                "rate_limit_hit": False
            }

    # Add section context and section number to the prompt
    section_context = f"Focus on the section '{section_name}' when extracting information."
    
    # Extract section number from section name if it contains a number
    section_number = ""
    section_number_match = re.search(r'(\d+)', section_name)
    if section_number_match:
        section_number = f"Section number: {section_number_match.group(1)}. "
    
    final_instructions = f"{section_context}\n{section_number}\n\n{prompt.prompt_text}"
    final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section."

    multimodal_prompt_parts = [
        genai_file,
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
        print(f"SUCCESS (Concurrent Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'.")
        return {
            "success": True,
            "section_name": section_name,
            "prompt": prompt,
            "rate_limit_hit": False
        }
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status} (Concurrent Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}'. Error: {str(api_output_data)[:100]}")
        return {
            "success": False,
            "section_name": section_name,
            "prompt": prompt,
            "error": str(api_output_data),
            "rate_limit_hit": status == "RATE_LIMIT",
            "api_attempt_count": api_attempt_count
        }
    else:
        err_msg = f"Permanent Error (Concurrent Section Extract): Section '{section_name}', Prompt '{prompt.prompt_name}': [{status}] {str(api_output_data)[:100]}"
        print(err_msg)
        prompt.result = f"Permanent error: {str(api_output_data)[:100]}"
        return {
            "success": False,
            "section_name": section_name,
            "prompt": prompt,
            "error": str(api_output_data),
            "rate_limit_hit": False
        }


async def _get_or_upload_genai_file(
    extraction_ctx: RefactoredExtractionContext,
    storage_service: StorageService,
    gemini_service: GenerativeAnalysisService,
    genai_file_name: Optional[str] = None
) -> bool:
    """Get existing Gemini AI file or upload new one if needed"""
    try:
        # If genai_file_name is provided, try to get the existing file
        if genai_file_name:
            print(f"Checking for existing Gemini AI file: {genai_file_name}")
            existing_file = await gemini_service.get_file_by_name(genai_file_name)
            if existing_file:
                print(f"Found existing Gemini AI file: {genai_file_name}")
                extraction_ctx.genai_file = existing_file
                extraction_ctx.genai_file_name = genai_file_name
                return True
            else:
                print(f"Gemini AI file not found: {genai_file_name}. Will proceed with normal upload.")
        
        # If no existing file found, upload the file
        print(f"Uploading file to Gemini AI: {extraction_ctx.target_drive_file_id}")
        uploaded_file = await gemini_service.upload_pdf_for_analysis_by_file_id(
            extraction_ctx.target_drive_file_id,
            extraction_ctx.target_drive_file_name or "document.pdf",
            storage_service
        )
        
        if uploaded_file:
            extraction_ctx.genai_file = uploaded_file
            extraction_ctx.genai_file_name = uploaded_file.name
            print(f"Successfully uploaded file to Gemini AI: {uploaded_file.name}")
            return True
        else:
            print("Failed to upload file to Gemini AI")
            return False

    except Exception as e:
        print(f"Error during Gemini AI file handling: {e}")
        traceback.print_exc()
        return False


async def process_extract_request(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    """Process the extract request using section-based PDF splitting approach"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    try:
        # Split PDF into sections and upload each section to Gemini AI
        if not await _split_and_upload_sections(
            extraction_ctx,
            storage_service,
            gemini_analysis_service,
            pdf_splitter_service,
            request.sections
        ):
            return ExtractResponse(
                success=False,
                originalDriveFileId=target_file_id,
                originalDriveFileName=request.originalDriveFileName,
                originalDriveParentFolderId=request.originalDriveParentFolderId,
                sections=request.sections,
                prompt=request.prompt,
                error="Failed to split and upload sections to Gemini AI",
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

        # Clean up section files
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)

        # Build the response
        print(f"Finished processing extract for file ID: {target_file_id}")
        return ExtractResponse(
            success=True,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=request.sections,
            prompt=request.prompt,
            genai_file_name=None  # No single genai_file_name when using section splitting
        )

    except Exception as ex:
        print(f"Unhandled critical error processing extract for file {target_file_id}: {ex}")
        traceback.print_exc()
        
        # Clean up section files even on error
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)
        
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


async def process_refactored_extract_request(
    request: AnalyzeResponseItemSuccess,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> RefactoredExtractResponse:
    """Process the refactored extract request using section-based PDF splitting approach"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Refactored Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    try:
        # Split PDF into sections and upload each section to Gemini AI
        if not await _split_and_upload_sections(
            extraction_ctx,
            storage_service,
            gemini_analysis_service,
            pdf_splitter_service,
            request.sections
        ):
            return RefactoredExtractResponse(
                success=False,
                result=None,
                error="Failed to split and upload sections to Gemini AI"
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
                        dd_task_details["section_name"],
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

        # Clean up section files
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)

        # Build the response
        print(f"Finished processing refactored extract for file ID: {target_file_id}")
        return RefactoredExtractResponse(
            success=True,
            result=transformed_request
        )

    except Exception as ex:
        print(f"Unhandled critical error processing refactored extract for file {target_file_id}: {ex}")
        traceback.print_exc()
        
        # Clean up section files even on error
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)
        
        return RefactoredExtractResponse(
            success=False,
            result=None,
            error=f"An internal server error occurred during extraction: {str(ex)}"
        ) 


async def process_extract_request_concurrent(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    """Process the extract request using concurrent API calls with section-based PDF splitting"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Concurrent Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(target_drive_file_id=target_file_id)
    extraction_ctx.target_drive_file_name = request.originalDriveFileName

    try:
        # Split PDF into sections and upload each section to Gemini AI
        if not await _split_and_upload_sections(
            extraction_ctx,
            storage_service,
            gemini_analysis_service,
            pdf_splitter_service,
            request.sections
        ):
            return ExtractResponse(
                success=False,
                originalDriveFileId=target_file_id,
                originalDriveFileName=request.originalDriveFileName,
                originalDriveParentFolderId=request.originalDriveParentFolderId,
                sections=request.sections,
                prompt=request.prompt,
                error="Failed to split and upload sections to Gemini AI",
                genai_file_name=None
            )

        # Create tasks for all sections
        tasks = []
        for section in request.sections:
            # Create a copy of the prompt for this section to avoid modifying the original
            section_prompt = SectionExtractPrompt(
                prompt_name=request.prompt.prompt_name,
                prompt_text=request.prompt.prompt_text,
                result=None
            )
            
            # Add the prompt to the section's prompts array
            if section.prompts is None:
                section.prompts = []
            if section_prompt not in section.prompts:
                section.prompts.append(section_prompt)
            
            # Create task for this section
            task = _execute_section_extraction_api_call_concurrent(
                gemini_analysis_service,
                extraction_ctx,
                section.sectionName,
                section.pageRange,
                section_prompt,
                0  # Initial attempt
            )
            tasks.append(task)

        # Execute all tasks concurrently
        print(f"Executing {len(tasks)} concurrent API calls...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle retries for failed requests
        failed_tasks = []
        rate_limit_hit = False
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Handle unexpected exceptions
                section = request.sections[i]
                print(f"Exception in concurrent call for section '{section.sectionName}': {result}")
                failed_tasks.append({
                    "section_name": section.sectionName,
                    "page_range": section.pageRange,
                    "prompt": section.prompts[i] if section.prompts else None,
                    "error": str(result),
                    "api_attempt_count": 0
                })
            elif not result["success"]:
                if result["rate_limit_hit"]:
                    rate_limit_hit = True
                    failed_tasks.append({
                        "section_name": result["section_name"],
                        "page_range": request.sections[i].pageRange,
                        "prompt": result["prompt"],
                        "api_attempt_count": result.get("api_attempt_count", 0)
                    })
                else:
                    # Permanent error, don't retry
                    print(f"Permanent error for section '{result['section_name']}': {result['error']}")

        # Handle retries if needed
        if failed_tasks and not rate_limit_hit:
            print(f"Retrying {len(failed_tasks)} failed requests...")
            retry_tasks = []
            for failed_task in failed_tasks:
                if failed_task["api_attempt_count"] < settings.max_api_retries:
                    retry_task = _execute_section_extraction_api_call_concurrent(
                        gemini_analysis_service,
                        extraction_ctx,
                        failed_task["section_name"],
                        failed_task["page_range"],
                        failed_task["prompt"],
                        failed_task["api_attempt_count"] + 1
                    )
                    retry_tasks.append(retry_task)
            
            if retry_tasks:
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                # Process retry results (similar to above)
                for i, retry_result in enumerate(retry_results):
                    if isinstance(retry_result, Exception):
                        print(f"Exception in retry call: {retry_result}")
                    elif not retry_result["success"]:
                        print(f"Retry failed for section '{retry_result['section_name']}': {retry_result['error']}")

        # Clean up section files
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)

        # Build the response
        print(f"Finished processing concurrent extract for file ID: {target_file_id}")
        return ExtractResponse(
            success=True,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=request.sections,
            prompt=request.prompt,
            genai_file_name=None  # No single genai_file_name when using section splitting
        )

    except Exception as ex:
        print(f"Unhandled critical error processing concurrent extract for file {target_file_id}: {ex}")
        traceback.print_exc()
        
        # Clean up section files even on error
        await _cleanup_section_files(extraction_ctx, gemini_analysis_service)
        
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


async def process_extract_request_concurrent_enhanced(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    """Process the extract request using enhanced concurrent section processing"""
    
    target_file_id = request.originalDriveFileId
    print(f"Processing Enhanced Concurrent Extract Request for file ID: {target_file_id}")

    # Create semaphores for resource control
    section_task_semaphore = Semaphore(settings.max_concurrent_section_tasks)
    upload_semaphore = Semaphore(settings.max_concurrent_uploads)
    split_semaphore = Semaphore(settings.max_concurrent_splits)

    try:
        # Download the original PDF once (shared resource)
        original_pdf_stream = storage_service.download_file_content(target_file_id)
        if not original_pdf_stream or original_pdf_stream.getbuffer().nbytes == 0:
            return ExtractResponse(
                success=False,
                originalDriveFileId=target_file_id,
                originalDriveFileName=request.originalDriveFileName,
                originalDriveParentFolderId=request.originalDriveParentFolderId,
                sections=request.sections,
                prompt=request.prompt,
                error="Failed to download original PDF",
                genai_file_name=None
            )

        # Create tasks for all sections with resource control
        tasks = []
        for section in request.sections:
            # Create a copy of the prompt for this section
            section_prompt = SectionExtractPrompt(
                prompt_name=request.prompt.prompt_name,
                prompt_text=request.prompt.prompt_text,
                result=None
            )
            
            # Add the prompt to the section's prompts array
            if section.prompts is None:
                section.prompts = []
            if section_prompt not in section.prompts:
                section.prompts.append(section_prompt)
            
            # Create enhanced task for this section
            task = process_single_section_task_enhanced(
                section=section,
                section_prompt=section_prompt,
                original_pdf_stream=original_pdf_stream,
                storage_service=storage_service,
                gemini_analysis_service=gemini_analysis_service,
                pdf_splitter_service=pdf_splitter_service,
                section_task_semaphore=section_task_semaphore,
                upload_semaphore=upload_semaphore,
                split_semaphore=split_semaphore
            )
            tasks.append(task)

        # Execute all tasks concurrently with resource limits
        print(f"Executing {len(tasks)} enhanced concurrent section tasks...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle retries
        failed_tasks = []
        successful_sections = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                section = request.sections[i]
                print(f"Exception in enhanced concurrent task for section '{section.sectionName}': {result}")
                failed_tasks.append({
                    "section": section,
                    "prompt": section.prompts[i] if section.prompts else None,
                    "error": str(result),
                    "retry_count": 0
                })
            elif result["success"]:
                successful_sections.append(result)
            else:
                # Handle failed but non-exception results
                failed_tasks.append({
                    "section": request.sections[i],
                    "prompt": result.get("prompt"),
                    "error": result.get("error", "Unknown error"),
                    "retry_count": 0
                })

        # Handle retries for failed tasks
        if failed_tasks:
            print(f"Retrying {len(failed_tasks)} failed tasks...")
            retry_results = await retry_failed_section_tasks(
                failed_tasks,
                original_pdf_stream,
                storage_service,
                gemini_analysis_service,
                pdf_splitter_service,
                section_task_semaphore,
                upload_semaphore,
                split_semaphore
            )
            successful_sections.extend([r for r in retry_results if r["success"]])

        # Close the original PDF stream
        original_pdf_stream.close()

        # Build the response
        print(f"Finished processing enhanced concurrent extract for file ID: {target_file_id}")
        return ExtractResponse(
            success=len(successful_sections) > 0,
            originalDriveFileId=target_file_id,
            originalDriveFileName=request.originalDriveFileName,
            originalDriveParentFolderId=request.originalDriveParentFolderId,
            sections=request.sections,
            prompt=request.prompt,
            genai_file_name=None
        )

    except Exception as ex:
        print(f"Unhandled critical error in enhanced concurrent extract for file {target_file_id}: {ex}")
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


async def process_single_section_task_enhanced(
    section: Any,
    section_prompt: SectionExtractPrompt,
    original_pdf_stream: io.BytesIO,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService,
    section_task_semaphore: Semaphore,
    upload_semaphore: Semaphore,
    split_semaphore: Semaphore
) -> Dict[str, Any]:
    """Process a single section with enhanced resource control and monitoring"""
    
    async with section_task_semaphore:
        section_name = section.sectionName
        print(f"Starting enhanced section task for '{section_name}'")
        
        try:
            # Check memory usage before starting
            if settings.enable_memory_monitoring:
                await check_and_throttle_memory()
            
            # Step 1: Split this specific section
            async with split_semaphore:
                section_pdf_stream = await split_single_section_enhanced(
                    original_pdf_stream, 
                    section, 
                    pdf_splitter_service
                )
            
            if not section_pdf_stream:
                return {
                    "success": False,
                    "section_name": section_name,
                    "error": "Failed to split section",
                    "prompt": section_prompt
                }
            
            # Step 2: Upload this section to Gemini AI
            async with upload_semaphore:
                gemini_file = await upload_single_section_enhanced(
                    section_pdf_stream, 
                    section_name, 
                    gemini_analysis_service
                )
            
            if not gemini_file:
                section_pdf_stream.close()
                return {
                    "success": False,
                    "section_name": section_name,
                    "error": "Failed to upload section to Gemini AI",
                    "prompt": section_prompt
                }
            
            # Step 3: Process with Gemini AI
            result = await process_section_with_gemini_enhanced(
                gemini_file, 
                section_prompt, 
                section_name, 
                gemini_analysis_service
            )
            
            # Step 4: Cleanup
            await cleanup_section_resources_enhanced(
                section_pdf_stream, 
                gemini_file, 
                gemini_analysis_service
            )
            
            return {
                "success": True,
                "section_name": section_name,
                "result": result,
                "prompt": section_prompt
            }
            
        except asyncio.TimeoutError:
            print(f"Timeout in enhanced section task for '{section_name}'")
            return {
                "success": False,
                "section_name": section_name,
                "error": "Section task timed out",
                "prompt": section_prompt
            }
        except Exception as e:
            print(f"Error in enhanced section task for '{section_name}': {e}")
            return {
                "success": False,
                "section_name": section_name,
                "error": str(e),
                "prompt": section_prompt
            }


async def split_single_section_enhanced(
    original_pdf_stream: io.BytesIO,
    section: Any,
    pdf_splitter_service: PdfSplitterService
) -> Optional[io.BytesIO]:
    """Split a single section with timeout and error handling"""
    
    try:
        # Create a copy of the original stream for this section
        original_pdf_stream.seek(0)
        section_copy = io.BytesIO(original_pdf_stream.read())
        
        # Convert section to the format expected by the PDF splitter
        section_for_splitting = {
            "sectionName": section.sectionName,
            "pageRange": section.pageRange
        }
        
        # Split with timeout
        split_results = await asyncio.wait_for(
            asyncio.to_thread(
                pdf_splitter_service.split_pdf_by_sections,
                section_copy,
                [section_for_splitting]
            ),
            timeout=settings.split_timeout_seconds
        )
        
        if split_results and len(split_results) > 0:
            return split_results[0]["fileContent"]
        else:
            section_copy.close()
            return None
            
    except asyncio.TimeoutError:
        print(f"Timeout splitting section '{section.sectionName}'")
        return None
    except Exception as e:
        print(f"Error splitting section '{section.sectionName}': {e}")
        return None


async def upload_single_section_enhanced(
    section_pdf_stream: io.BytesIO,
    section_name: str,
    gemini_service: GenerativeAnalysisService
) -> Optional[Any]:
    """Upload a single section with timeout and error handling"""
    
    try:
        # Create a unique display name for this section
        unique_id = str(uuid.uuid4())[:8]
        display_name = f"{section_name}_{unique_id}.pdf"
        
        # Upload with timeout
        gemini_file = await asyncio.wait_for(
            gemini_service.upload_pdf_for_analysis(section_pdf_stream, display_name),
            timeout=settings.upload_timeout_seconds
        )
        
        return gemini_file
        
    except asyncio.TimeoutError:
        print(f"Timeout uploading section '{section_name}'")
        return None
    except Exception as e:
        print(f"Error uploading section '{section_name}': {e}")
        return None


async def process_section_with_gemini_enhanced(
    gemini_file: Any,
    section_prompt: SectionExtractPrompt,
    section_name: str,
    gemini_service: GenerativeAnalysisService
) -> str:
    """Process a section with Gemini AI with enhanced error handling"""
    
    try:
        # Add section context to the prompt
        section_context = f"Focus on the section '{section_name}' when extracting information."
        final_instructions = f"{section_context}\n\n{section_prompt.prompt_text}"
        final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section."

        multimodal_prompt_parts = [gemini_file, final_instructions]

        # Process with timeout
        response = await asyncio.wait_for(
            gemini_service.model.generate_content_async(multimodal_prompt_parts),
            timeout=settings.gemini_timeout_seconds
        )
        
        if response and response.text:
            return response.text
        else:
            return "No response text received"
            
    except asyncio.TimeoutError:
        print(f"Timeout processing section '{section_name}' with Gemini")
        return "Processing timed out"
    except Exception as e:
        print(f"Error processing section '{section_name}' with Gemini: {e}")
        return f"Processing error: {str(e)}"


async def cleanup_section_resources_enhanced(
    section_pdf_stream: io.BytesIO,
    gemini_file: Any,
    gemini_service: GenerativeAnalysisService
):
    """Clean up section resources with error handling"""
    
    try:
        # Close PDF stream
        if section_pdf_stream:
            section_pdf_stream.close()
        
        # Delete Gemini file
        if gemini_file:
            await gemini_service.delete_file(gemini_file)
            
    except Exception as e:
        print(f"Error during section resource cleanup: {e}")


async def check_and_throttle_memory():
    """Check memory usage and throttle if necessary"""
    
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        if memory_mb > settings.max_memory_usage_mb:
            print(f"Memory usage {memory_mb:.1f}MB exceeds limit {settings.max_memory_usage_mb}MB. Throttling...")
            await asyncio.sleep(5)  # Throttle for 5 seconds
        elif memory_mb > settings.memory_throttle_threshold_mb:
            print(f"Memory usage {memory_mb:.1f}MB above threshold {settings.memory_throttle_threshold_mb}MB. Adding delay...")
            await asyncio.sleep(1)  # Small delay
            
    except Exception as e:
        print(f"Error checking memory usage: {e}")


async def retry_failed_section_tasks(
    failed_tasks: List[Dict[str, Any]],
    original_pdf_stream: io.BytesIO,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService,
    section_task_semaphore: Semaphore,
    upload_semaphore: Semaphore,
    split_semaphore: Semaphore
) -> List[Dict[str, Any]]:
    """Retry failed section tasks with exponential backoff"""
    
    retry_results = []
    
    for failed_task in failed_tasks:
        if failed_task["retry_count"] < settings.max_api_retries:
            # Add exponential backoff delay
            delay = min(2 ** failed_task["retry_count"], 30)  # Max 30 seconds
            await asyncio.sleep(delay)
            
            # Retry the task
            retry_task = process_single_section_task_enhanced(
                section=failed_task["section"],
                section_prompt=failed_task["prompt"],
                original_pdf_stream=original_pdf_stream,
                storage_service=storage_service,
                gemini_analysis_service=gemini_analysis_service,
                pdf_splitter_service=pdf_splitter_service,
                section_task_semaphore=section_task_semaphore,
                upload_semaphore=upload_semaphore,
                split_semaphore=split_semaphore
            )
            
            try:
                result = await retry_task
                retry_results.append(result)
            except Exception as e:
                print(f"Retry failed for section '{failed_task['section'].sectionName}': {e}")
                retry_results.append({
                    "success": False,
                    "section_name": failed_task["section"].sectionName,
                    "error": f"Retry failed: {str(e)}",
                    "prompt": failed_task["prompt"]
                })
    
    return retry_results 