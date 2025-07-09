# helpers/refactored_extract_helpers.py

import io
import os
import time
import uuid
import re
import traceback
import asyncio
import gc
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
    storage_file_id: str
    file_name: Optional[str] = None
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


async def process_extract_request_with_preloaded_files(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    """
    Process extract request using pre-loaded files from split operation.
    This eliminates the need for splitting in the extract endpoint.
    """
    print(f"Processing extract request with pre-loaded files for file: {request.storage_file_id}")
    
    try:
        # Check if sections have genai_file_name (indicating they were pre-loaded)
        sections_with_genai_files = []
        sections_without_genai_files = []
        
        for section in request.sections:
            if section.genai_file_name:
                sections_with_genai_files.append(section)
            else:
                sections_without_genai_files.append(section)
        
        if sections_without_genai_files:
            print(f"Warning: {len(sections_without_genai_files)} sections don't have genai_file_name. These will be skipped.")
            for section in sections_without_genai_files:
                print(f"  - Section '{section.section_name}' missing genai_file_name")
        
        if not sections_with_genai_files:
            return ExtractResponse(
                success=False,
                storage_file_id=request.storage_file_id,
                file_name=request.file_name,
                storage_parent_folder_id=request.storage_parent_folder_id,
                sections=request.sections,
                prompt=request.prompt,
                error="No sections have pre-loaded genai_file_name. Please run /split first."
            )
        
        # Process each section with pre-loaded files
        processed_sections = []
        api_retry_queue = deque()
        
        for section in sections_with_genai_files:
            print(f"Processing section '{section.section_name}' with genai_file_name: {section.genai_file_name}")
            
            # Get the pre-loaded file from Gemini AI
            try:
                genai_file = await gemini_analysis_service.get_file_by_name(section.genai_file_name)
                if not genai_file:
                    print(f"Warning: Could not retrieve file '{section.genai_file_name}' from Gemini AI for section '{section.section_name}'")
                    # Create a section with error result
                    error_prompt = SectionExtractPrompt(
                        prompt_name=request.prompt.prompt_name,
                        prompt_text=request.prompt.prompt_text,
                        result=f"Error: Could not retrieve pre-loaded file '{section.genai_file_name}' from Gemini AI"
                    )
                    processed_section = section.model_copy(deep=True)
                    processed_section.prompts = [error_prompt]
                    processed_sections.append(processed_section)
                    continue
                
                # Execute the prompt for this section
                section_context = f"Focus on the section '{section.section_name}' when extracting information."
                section_number = ""
                section_number_match = re.search(r'(\d+)', section.section_name)
                if section_number_match:
                    section_number = f"Section number: {section_number_match.group(1)}. "
                
                final_instructions = f"{section_context}\n{section_number}\n\n{request.prompt.prompt_text}"
                final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section."
                
                multimodal_prompt_parts = [
                    genai_file,
                    final_instructions
                ]
                
                # Execute the API call
                response = await gemini_analysis_service.model.generate_content_async(multimodal_prompt_parts)
                
                if response and response.text:
                    result_text = response.text
                    status = "SUCCESS"
                else:
                    result_text = "No response received from Gemini AI"
                    status = "ERROR"
                
                # Create the result prompt
                result_prompt = SectionExtractPrompt(
                    prompt_name=request.prompt.prompt_name,
                    prompt_text=request.prompt.prompt_text,
                    result=result_text
                )
                
                # Create the processed section
                processed_section = section.model_copy(deep=True)
                processed_section.prompts = [result_prompt]
                processed_sections.append(processed_section)
                
                print(f"Successfully processed section '{section.section_name}'")
                
            except Exception as e:
                print(f"Error processing section '{section.section_name}': {e}")
                traceback.print_exc()
                
                # Create a section with error result
                error_prompt = SectionExtractPrompt(
                    prompt_name=request.prompt.prompt_name,
                    prompt_text=request.prompt.prompt_text,
                    result=f"Error processing section: {str(e)}"
                )
                processed_section = section.model_copy(deep=True)
                processed_section.prompts = [error_prompt]
                processed_sections.append(processed_section)
        
        # Create the final response
        result_prompt = SectionExtractPrompt(
            prompt_name=request.prompt.prompt_name,
            prompt_text=request.prompt.prompt_text,
            result="Processing completed for all sections"
        )
        
        return ExtractResponse(
            success=True,
            storage_file_id=request.storage_file_id,
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
            sections=processed_sections,
            prompt=result_prompt,
            genai_file_name=None  # Not needed for this workflow
        )
        
    except Exception as e:
        print(f"Error in process_extract_request_with_preloaded_files: {e}")
        traceback.print_exc()
        return ExtractResponse(
            success=False,
            storage_file_id=request.storage_file_id,
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
            sections=request.sections,
            prompt=request.prompt,
            error=f"Internal error: {str(e)}"
        )


async def _split_and_upload_sections(
    extraction_ctx: RefactoredExtractionContext,
    storage_service: StorageService,
    gemini_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService,
    sections: List[Any]
) -> bool:
    """Split PDF into sections and upload each section as a separate file to Gemini AI"""
    try:
        print(f"Splitting PDF into {len(sections)} sections for file ID: {extraction_ctx.storage_file_id}")
        
        # Download the original PDF
        original_pdf_stream = storage_service.download_file_content(extraction_ctx.storage_file_id)
        if not original_pdf_stream or original_pdf_stream.getbuffer().nbytes == 0:
            print("Failed to download original PDF for splitting")
            return False
        
        # Convert sections to the format expected by the PDF splitter
        sections_for_splitting = [
            {
                "section_name": section.section_name,
                "page_range": section.page_range
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
        base_filename = os.path.splitext(extraction_ctx.file_name or "document")[0]
        
        for split_result in split_results:
            section_name = split_result["section_name"]
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
    target_id_log = extraction_ctx.storage_file_id
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
    target_id_log = extraction_ctx.storage_file_id
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
        print(f"Uploading file to Gemini AI: {extraction_ctx.storage_file_id}")
        uploaded_file = await gemini_service.upload_pdf_for_analysis_by_file_id(
            extraction_ctx.storage_file_id,
            extraction_ctx.file_name or "document.pdf",
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
    
    target_file_id = request.storage_file_id
    print(f"Processing Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(storage_file_id=target_file_id)
    extraction_ctx.file_name = request.file_name

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
                file_name=request.file_name,
                storage_parent_folder_id=request.storage_parent_folder_id,
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
                "section_name": section.section_name,
                "page_range": section.page_range,
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
                        section.section_name,
                        section.page_range,
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
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
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
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
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
    
    target_file_id = request.storage_file_id
    print(f"Processing Refactored Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(storage_file_id=target_file_id)
    extraction_ctx.file_name = request.file_name

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
                    page_range=section.page_range,
                    section_name=section.section_name
                )
            else:
                # Create a default extraction prompt for sections without prompts
                default_prompt = SectionExtractPrompt(
                    prompt_name="extract_content",
                    prompt_text="Extract all relevant content from this section, including any key information, data, or important details."
                )
                section_with_prompts = SectionWithPrompts(
                    prompts=[default_prompt],
                    page_range=section.page_range,
                    section_name=section.section_name
                )
            sections_with_prompts.append(section_with_prompts)

        # Create the transformed request structure
        transformed_request = AnalyzeResultWithPrompts(
            originalDriveFileId=request.originalDriveFileId,
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
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
                    "section_name": section.section_name,
                    "page_range": section.page_range,
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
    
    target_file_id = request.storage_file_id
    print(f"Processing Concurrent Extract Request for file ID: {target_file_id}")

    extraction_ctx = RefactoredExtractionContext(storage_file_id=target_file_id)
    extraction_ctx.file_name = request.file_name

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
                file_name=request.file_name,
                storage_parent_folder_id=request.storage_parent_folder_id,
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
                section.section_name,
                section.page_range,
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
                print(f"Exception in concurrent call for section '{section.section_name}': {result}")
                failed_tasks.append({
                    "section_name": section.section_name,
                    "page_range": section.page_range,
                    "prompt": section.prompts[i] if section.prompts else None,
                    "error": str(result),
                    "api_attempt_count": 0
                })
            elif not result["success"]:
                if result["rate_limit_hit"]:
                    rate_limit_hit = True
                    failed_tasks.append({
                        "section_name": result["section_name"],
                        "page_range": request.sections[i].page_range,
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
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
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
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
            sections=request.sections,
            prompt=request.prompt,
            error=f"An internal server error occurred during extraction: {str(ex)}",
            genai_file_name=None
        ) 


async def _process_section_memory_efficient(
    section: Any,
    storage_file_id: str,
    storage_service: StorageService,
    gemini_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService,
    prompt: SectionExtractPrompt,
    base_filename: str
) -> Tuple[bool, str]:
    """
    Process a single section with immediate cleanup to prevent memory issues.
    Returns (success, result_or_error)
    """
    section_name = section.section_name
    page_range = section.page_range
    
    try:
        print(f"Processing section '{section_name}' with memory-efficient approach")
        
        # Download the original PDF using the passed storage_file_id
        original_pdf_stream = storage_service.download_file_content(storage_file_id)
        if not original_pdf_stream or original_pdf_stream.getbuffer().nbytes == 0:
            return False, "Failed to download original PDF"
        
        try:
            # Split only this section
            sections_for_splitting = [
                {
                    "section_name": section_name,
                    "page_range": page_range
                }
            ]
            
            # Split the PDF into this single section
            split_results = await asyncio.to_thread(
                pdf_splitter_service.split_pdf_by_sections,
                original_pdf_stream,
                sections_for_splitting
            )
            
            if not split_results:
                return False, "PDF splitting failed for this section"
            
            # Get the section PDF stream
            section_data = split_results[0]
            section_pdf_stream = section_data["fileContent"]
            
            try:
                # Create a unique display name for this section
                unique_id = str(uuid.uuid4())[:8]
                display_name = f"{base_filename}_{section_name}_{unique_id}.pdf"
                
                print(f"Uploading section '{section_name}' as '{display_name}' to Gemini AI")
                
                # Upload the section to Gemini AI
                gemini_file = await gemini_service.upload_pdf_for_analysis(section_pdf_stream, display_name)
                
                if not gemini_file:
                    return False, f"Failed to upload section '{section_name}' to Gemini AI"
                
                try:
                    # Process the section with the prompt
                    section_context = f"Focus on the section '{section_name}' when extracting information."
                    
                    # Extract section number from section name if it contains a number
                    section_number = ""
                    section_number_match = re.search(r'(\d+)', section_name)
                    if section_number_match:
                        section_number = f"Section number: {section_number_match.group(1)}. "
                    
                    final_instructions = f"{section_context}\n{section_number}\n\n{prompt.prompt_text}"
                    final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section."

                    multimodal_prompt_parts = [
                        gemini_file,
                        final_instructions
                    ]

                    # Execute the API call
                    response = await gemini_service.model.generate_content_async(multimodal_prompt_parts)
                    
                    if response and response.text:
                        result = response.text
                        print(f"Successfully processed section '{section_name}'")
                        return True, result
                    else:
                        return False, "No response text received from Gemini AI"
                        
                finally:
                    # Clean up the Gemini AI file immediately
                    try:
                        await gemini_service.delete_file(gemini_file)
                        print(f"Deleted section file '{section_name}' from Gemini AI")
                    except Exception as e:
                        print(f"Error deleting section file '{section_name}' from Gemini AI: {e}")
                
            finally:
                # Clean up the section PDF stream immediately
                try:
                    section_pdf_stream.close()
                    print(f"Closed PDF stream for section '{section_name}'")
                except Exception as e:
                    print(f"Error closing PDF stream for section '{section_name}': {e}")
                
        finally:
            # Clean up the original PDF stream
            try:
                original_pdf_stream.close()
                print(f"Closed original PDF stream for section '{section_name}'")
            except Exception as e:
                print(f"Error closing original PDF stream for section '{section_name}': {e}")
        
    except Exception as e:
        error_msg = f"Error processing section '{section_name}': {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return False, error_msg
    finally:
        # Force garbage collection after each section if enabled
        if settings.force_garbage_collection:
            gc.collect()


async def process_extract_request_memory_efficient(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    """
    Process the extract request using memory-efficient sequential processing.
    Each section is processed individually with immediate cleanup to prevent memory issues.
    """
    
    target_file_id = request.storage_file_id
    print(f"Processing Memory-Efficient Extract Request for file ID: {target_file_id}")

    # Create a copy of sections to avoid modifying the original request
    sections = request.sections.copy()
    base_filename = os.path.splitext(request.file_name or "document")[0]
    
    # Process each section sequentially with immediate cleanup
    processed_sections = []
    
    for i, section in enumerate(sections):
        print(f"Processing section {i+1}/{len(sections)}: {section.section_name}")
        
        # Create a copy of the prompt for this section
        section_prompt = SectionExtractPrompt(
            prompt_name=request.prompt.prompt_name,
            prompt_text=request.prompt.prompt_text,
            result=None
        )
        
        # Process this section - pass target_file_id directly
        success, result = await _process_section_memory_efficient(
            section,
            target_file_id,
            storage_service,
            gemini_analysis_service,
            pdf_splitter_service,
            section_prompt,
            base_filename
        )
        
        if success:
            section_prompt.result = result
        else:
            section_prompt.result = f"Error: {result}"
        
        # Create the processed section
        processed_section = SectionWithPrompts(
            section_name=section.section_name,
            page_range=section.page_range,
            prompts=[section_prompt]
        )
        processed_sections.append(processed_section)
        
        # Force garbage collection after each section if enabled
        if settings.force_garbage_collection:
            gc.collect()
        
        # Delay between section processing to prevent overwhelming the system
        await asyncio.sleep(settings.section_processing_delay_seconds)
    
    print(f"Finished memory-efficient processing for file ID: {target_file_id}")
    
    return ExtractResponse(
        success=True,
        originalDriveFileId=target_file_id,
        file_name=request.file_name,
        storage_parent_folder_id=request.storage_parent_folder_id,
        sections=processed_sections,
        prompt=request.prompt,
        error=None,
        genai_file_name=None
    ) 


async def _execute_section_extraction_with_preloaded_file(
    gemini_analysis_service: GenerativeAnalysisService,
    section: Any,
    prompt: SectionExtractPrompt
) -> Dict[str, Any]:
    section_name = section.section_name
    genai_file_name = section.genai_file_name
    try:
        genai_file = await gemini_analysis_service.get_file_by_name(genai_file_name)
        if not genai_file:
            return {"success": False, "section_name": section_name, "prompt": prompt, "error": f"Could not retrieve file '{genai_file_name}'", "rate_limit_hit": False}
        instructions = f"Focus on the section '{section_name}' when extracting information.\n\n{prompt.prompt_text}\n\nEnsure the output is ONLY the requested information for this specific section."
        response = await gemini_analysis_service.model.generate_content_async([genai_file, instructions])
        if response and response.text:
            prompt.result = response.text
            return {"success": True, "section_name": section_name, "prompt": prompt, "result": response.text, "rate_limit_hit": False}
        else:
            return {"success": False, "section_name": section_name, "prompt": prompt, "error": "No response from Gemini AI", "rate_limit_hit": False}
    except Exception as e:
        error_str = str(e).lower()
        rate_limit_hit = any(phrase in error_str for phrase in ["rate limit", "quota exceeded", "too many requests", "429"])
        return {"success": False, "section_name": section_name, "prompt": prompt, "error": str(e), "rate_limit_hit": rate_limit_hit}

async def process_extract_request_with_preloaded_files_concurrent(
    request: ExtractRequest,
    storage_service: StorageService,
    gemini_analysis_service: GenerativeAnalysisService,
    pdf_splitter_service: PdfSplitterService
) -> ExtractResponse:
    print(f"Processing concurrent extract request with pre-loaded files for file: {request.storage_file_id}")
    sections_with_genai_files = [s for s in request.sections if s.genai_file_name]
    if not sections_with_genai_files:
        return ExtractResponse(
            success=False,
            storage_file_id=request.storage_file_id,
            file_name=request.file_name,
            storage_parent_folder_id=request.storage_parent_folder_id,
            sections=request.sections,
            prompt=request.prompt,
            error="No sections have pre-loaded genai_file_name. Please run /split first."
        )
    batch_size = settings.max_concurrent_requests
    processed_sections = []
    for batch_start in range(0, len(sections_with_genai_files), batch_size):
        batch = sections_with_genai_files[batch_start:batch_start+batch_size]
        tasks = []
        for section in batch:
            prompt = SectionExtractPrompt(
                prompt_name=request.prompt.prompt_name,
                prompt_text=request.prompt.prompt_text,
                result=None
            )
            section.prompts = [prompt]
            tasks.append(_execute_section_extraction_with_preloaded_file(gemini_analysis_service, section, prompt))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            section = batch[i]
            if isinstance(result, Exception) or not result.get("success"):
                error = str(result) if isinstance(result, Exception) else result.get("error", "Unknown error")
                section.prompts[0].result = f"Error: {error}"
            processed_sections.append(section)
    return ExtractResponse(
        success=True,
        storage_file_id=request.storage_file_id,
        file_name=request.file_name,
        storage_parent_folder_id=request.storage_parent_folder_id,
        sections=processed_sections,
        prompt=request.prompt,
        genai_file_name=None
    ) 