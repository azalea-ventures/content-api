# helpers/extract_helpers.py

import io
import os
import time
import uuid
import re
import traceback
import asyncio
from collections import deque # For type hint
from typing import Optional, List, Dict, Any, Tuple

# Pydantic imports
from pydantic import BaseModel, Field # Ensure BaseModel and Field are imported

from google.generativeai import types as genai_types_google

from models import (
    ExtractTask,
    BatchExtractTaskResult,
    ExtractTaskResponseItemError,
    ExtractTaskResponseItemSuccess,
    GeneratedContentItem,
    PromptItem
)
from services.google_drive_service import GoogleDriveService
from services.generative_analysis_service import GenerativeAnalysisService

from helpers.enhance_helpers import PromptConstructionStatus # Import from enhance_helpers
from config import settings


class ExtractionContext(BaseModel):
    target_drive_file_id: str
    target_drive_file_name: Optional[str] = None
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    uploaded_target_gfile: Optional[genai_types_google.File] = None

    # Pydantic model_config for this specific model
    model_config = {
        "extra": "allow",
        "arbitrary_types_allowed": True # <-- ADD THIS LINE
    }


# ... rest of your extract_helpers.py file (functions remain the same) ...

def _construct_extraction_prompt(
    prompt_item: PromptItem,
    extraction_ctx: ExtractionContext,
    all_task_prompts: List[PromptItem]
) -> Tuple[PromptConstructionStatus, Optional[str]]:
    full_prompt_parts = [prompt_item.prompt_template.strip()]
    all_dependencies_met = True
    known_prompt_names_in_task = {p.prompt_name for p in all_task_prompts}

    for prop_key_to_append in prompt_item.lesson_properties_to_append:
        value_to_append: Optional[str] = None
        property_display_name = prop_key_to_append.replace("_", " ").title()

        if prop_key_to_append in known_prompt_names_in_task:
            dependency_found = False
            for gen_output in extraction_ctx.generated_outputs:
                if gen_output.prompt_name == prop_key_to_append:
                    dependency_found = True
                    if gen_output.status == "SUCCESS" and gen_output.output is not None:
                        value_to_append = gen_output.output
                        property_display_name = f"Previously Extracted Data ('{prop_key_to_append}')"
                    else:
                        all_dependencies_met = False 
                    break
            if not dependency_found and all_dependencies_met: 
                all_dependencies_met = False
        elif extraction_ctx.model_extra and prop_key_to_append in extraction_ctx.model_extra:
            try:
                value_to_append = str(extraction_ctx.model_extra[prop_key_to_append])
            except Exception as e:
                print(f"Warning (Extract): Could not convert extra context field '{prop_key_to_append}' to string for prompt '{prompt_item.prompt_name}': {e}")
        else:
            print(f"Warning (Extract): Property '{prop_key_to_append}' requested by prompt '{prompt_item.prompt_name}' is not a known prior prompt for this task nor an extra field. Not appending.")

        if not all_dependencies_met:
            print(f"Info (Extract): Dependency '{prop_key_to_append}' for prompt '{prompt_item.prompt_name}' on TargetID '{extraction_ctx.target_drive_file_id}' not met. Deferring.")
            break

        if value_to_append is not None:
            full_prompt_parts.append(f"---\n{property_display_name}:\n{value_to_append.strip()}")
            
    if not all_dependencies_met:
        return PromptConstructionStatus.MISSING_DEPENDENCY, None
    
    return PromptConstructionStatus.SUCCESS, "\n".join(full_prompt_parts)


async def _execute_extraction_api_call(
    gemini_service: GenerativeAnalysisService,
    extraction_ctx: ExtractionContext,
    prompt_item: PromptItem,
    full_prompt_text_for_instructions: str,
    api_attempt_count: int,
    api_retry_queue: deque, 
    task_id_for_queue: str
) -> bool: 
    prompt_name = prompt_item.prompt_name
    target_id_log = extraction_ctx.target_drive_file_id
    print(f"API Call (Extract): Task '{task_id_for_queue}', Target '{target_id_log}', Prompt '{prompt_name}', API Attempt {api_attempt_count + 1}.")

    if not extraction_ctx.uploaded_target_gfile:
        print(f"Error (Extract): Target Google AI File not found in context for Task '{task_id_for_queue}', Prompt '{prompt_name}'.")
        output_item, _ = get_extraction_output_item(extraction_ctx, prompt_name) 
        output_item.status = "ERROR_INTERNAL_NO_GFILE"
        output_item.output = "Internal error: Target Google AI File was not available for multimodal prompt."
        return False 

    final_instructions = full_prompt_text_for_instructions
    if prompt_item.output_json_format_example_str:
        final_instructions += (
            f"\n\n--- EXPECTED JSON OUTPUT FORMAT EXAMPLE ---\n"
            f"{prompt_item.output_json_format_example_str.strip()}\n"
            f"--- END EXPECTED JSON OUTPUT FORMAT EXAMPLE ---"
        )
    final_instructions += "\nEnsure the output is ONLY the requested information, formatted as specified (likely JSON)."

    multimodal_prompt_parts = [
        extraction_ctx.uploaded_target_gfile, 
        final_instructions                    
    ]
    
    status, api_output_data = await gemini_service.generate_text(multimodal_prompt_parts) 

    output_item, _ = get_extraction_output_item(extraction_ctx, prompt_name) 
    output_item.status = status 
    output_item.output = api_output_data 

    if status == "SUCCESS":
        print(f"SUCCESS (Extract): Task '{task_id_for_queue}', Target '{target_id_log}', Prompt '{prompt_name}'.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status} (Extract): Task '{task_id_for_queue}', Target '{target_id_log}', Prompt '{prompt_name}'. Error: {str(api_output_data)[:100]}")
        if api_attempt_count + 1 < settings.max_api_retries:
            print(f"Re-queuing for API retry (Extract): Task '{task_id_for_queue}', Prompt '{prompt_name}' (API attempt {api_attempt_count + 2}).")
            retry_task_details = {
                "type": "extraction_api_retry", 
                "task_id": task_id_for_queue,
                "prompt_item": prompt_item,
                "full_prompt_text_for_instructions": full_prompt_text_for_instructions, 
                "api_attempt_count": api_attempt_count + 1
            }
            api_retry_queue.append(retry_task_details)
            output_item.status = "PENDING_API_RETRY"
        else:
            err_msg = f"Max API retries ({settings.max_api_retries}) for extraction: Task '{task_id_for_queue}', Prompt '{prompt_name}'. Last: [{status}] {str(api_output_data)[:100]}"
            print(err_msg)
        return True 
    else: 
        err_msg = f"Permanent Error (Extract): Task '{task_id_for_queue}', Prompt '{prompt_name}': [{status}] {str(api_output_data)[:100]}"
        print(err_msg)
        return False


def get_extraction_output_item(extraction_ctx: ExtractionContext, prompt_name: str) -> Tuple[GeneratedContentItem, bool]:
    for item in extraction_ctx.generated_outputs:
        if item.prompt_name == prompt_name:
            return item, False
    new_item = GeneratedContentItem(prompt_name=prompt_name)
    extraction_ctx.generated_outputs.append(new_item)
    return new_item, True

async def process_single_extract_task( 
    extract_task_item: ExtractTask,
    drive_service: GoogleDriveService,
    gemini_analysis_service: GenerativeAnalysisService
) -> BatchExtractTaskResult: 
    
    task_id = extract_task_item.task_id
    target_file_id = extract_task_item.target_drive_file_id
    print(f"Processing Extract Task ID: {task_id}, Target File ID: {target_file_id}")

    # Pass task_id to ExtractionContext if it's part of its model definition
    extraction_ctx = ExtractionContext(target_drive_file_id=target_file_id) 

    try:
        target_file_info = drive_service.get_file_info(target_file_id)
        if target_file_info is None:
            return BatchExtractTaskResult(success=False, error_info=ExtractTaskResponseItemError(task_id=task_id, target_drive_file_id=target_file_id, error="Target Drive file not found or permission denied."))
        
        extraction_ctx.target_drive_file_name = target_file_info.get('name', target_file_id)

        # Check file size before processing
        file_size = target_file_info.get('size', 0)
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return BatchExtractTaskResult(
                success=False, 
                error_info=ExtractTaskResponseItemError(
                    task_id=task_id, 
                    target_drive_file_id=target_file_id, 
                    error=f"File too large ({file_size / (1024*1024):.1f}MB). Maximum size is 50MB."
                )
            )

        # Use the new method that only downloads if needed
        target_gfile_display_name = f"extract_target_{os.path.splitext(extraction_ctx.target_drive_file_name or 'unknown')[0]}_{task_id[:8]}.pdf"
        extraction_ctx.uploaded_target_gfile = await gemini_analysis_service.upload_pdf_for_analysis_by_file_id(
            target_file_id,
            target_gfile_display_name,
            drive_service
        )
        
        if extraction_ctx.uploaded_target_gfile is None:
            return BatchExtractTaskResult(success=False, error_info=ExtractTaskResponseItemError(task_id=task_id, target_drive_file_id=target_file_id, error="Failed to upload target document for AI extraction."))

        api_retry_queue_extract = deque()
        data_dependency_deferred_queue_extract = deque()

        for p_item in extract_task_item.prompts:
            data_dependency_deferred_queue_extract.append(
                {"type": "extraction_data_dependency", "task_id": task_id, "prompt_item": p_item, "dd_attempt_count": 0}
            )
        
        last_rate_limit_time_extract = None
        processing_cycles_extract = 0
        max_cycles_extract = len(extract_task_item.prompts) * (settings.max_api_retries + settings.max_data_dependency_retries + 2)

        while data_dependency_deferred_queue_extract or api_retry_queue_extract:
            processing_cycles_extract += 1
            if processing_cycles_extract > max_cycles_extract:
                print(f"Warning (Extract Task {task_id}): Max processing cycles reached. Breaking.")
                break
            
            if api_retry_queue_extract:
                if not (last_rate_limit_time_extract and (time.monotonic() - last_rate_limit_time_extract < settings.retry_cooldown_seconds)):
                    api_task_details = api_retry_queue_extract.popleft()
                    if api_task_details.get("type") == "extraction_api_retry": # Check type
                        rate_limit_hit_on_retry = await _execute_extraction_api_call(
                            gemini_analysis_service, extraction_ctx, 
                            api_task_details["prompt_item"], 
                            api_task_details["full_prompt_text_for_instructions"],
                            api_task_details["api_attempt_count"],
                            api_retry_queue_extract,
                            task_id # Pass task_id directly as defined in this function
                        )
                        if rate_limit_hit_on_retry: last_rate_limit_time_extract = time.monotonic()
                    else: 
                        api_retry_queue_extract.appendleft(api_task_details)
                    await asyncio.sleep(0.1)
                    continue
            
            if data_dependency_deferred_queue_extract:
                dd_task_details = data_dependency_deferred_queue_extract.popleft()
                if dd_task_details.get("type") == "extraction_data_dependency": # Check type
                    current_prompt_item = dd_task_details["prompt_item"]
                    dd_attempts = dd_task_details["dd_attempt_count"]

                    construction_status, instructions_text = _construct_extraction_prompt(
                        current_prompt_item, extraction_ctx, extract_task_item.prompts
                    )

                    if construction_status == PromptConstructionStatus.SUCCESS:
                        if instructions_text is None:
                            get_extraction_output_item(extraction_ctx, current_prompt_item.prompt_name)[0].status = "ERROR_INTERNAL_CONSTRUCTION"
                            continue
                        
                        if last_rate_limit_time_extract and (time.monotonic() - last_rate_limit_time_extract < settings.retry_cooldown_seconds):
                            dd_task_details["dd_attempt_count"] = dd_attempts
                            data_dependency_deferred_queue_extract.append(dd_task_details)
                            await asyncio.sleep(0.1)
                            continue

                        api_call_requeued = await _execute_extraction_api_call(
                            gemini_analysis_service, extraction_ctx, current_prompt_item, instructions_text, 0,
                            api_retry_queue_extract, task_id # Pass task_id directly
                        )
                        if api_call_requeued: last_rate_limit_time_extract = time.monotonic()

                    elif construction_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                        output_item_status_update, _ = get_extraction_output_item(extraction_ctx, current_prompt_item.prompt_name)
                        if dd_attempts + 1 < settings.max_data_dependency_retries:
                            dd_task_details["dd_attempt_count"] = dd_attempts + 1
                            data_dependency_deferred_queue_extract.append(dd_task_details)
                            output_item_status_update.status = "DATA_DEPENDENCY_PENDING"
                            output_item_status_update.output = f"Waiting for data (attempt {dd_attempts + 1})"
                        else:
                            output_item_status_update.status = "DATA_DEPENDENCY_FAILED"
                            output_item_status_update.output = f"Max data dependency retries ({settings.max_data_dependency_retries}) reached."
                else: 
                    data_dependency_deferred_queue_extract.appendleft(dd_task_details)
                await asyncio.sleep(0.05)
                continue
            
            if not data_dependency_deferred_queue_extract and not api_retry_queue_extract and \
               last_rate_limit_time_extract and (time.monotonic() - last_rate_limit_time_extract < settings.retry_cooldown_seconds):
                await asyncio.sleep(0.2)


        for dd_task_details in list(data_dependency_deferred_queue_extract): # Iterate over copy
            if dd_task_details.get("type") == "extraction_data_dependency":
                output_item_timeout, _ = get_extraction_output_item(extraction_ctx, dd_task_details["prompt_item"].prompt_name)
                if output_item_timeout.status == "DATA_DEPENDENCY_PENDING" or not output_item_timeout.status:
                    output_item_timeout.status = "DATA_DEPENDENCY_TIMEOUT"
                    output_item_timeout.output = "Processing cycle limit reached while waiting for data dependency for extraction."


        print(f"Finished processing prompts for Extract Task ID: {task_id}")
        extraction_results = extraction_ctx.generated_outputs
        return BatchExtractTaskResult(
            success=True,
            result=ExtractTaskResponseItemSuccess(
                task_id=task_id,
                target_drive_file_id=target_file_id,
                target_drive_file_name=extraction_ctx.target_drive_file_name,
                extraction_results=extraction_results,
                genai_file_name=extraction_ctx.uploaded_target_gfile.name
            )
        )

    except Exception as ex:
        print(f"Unhandled critical error processing Extract Task ID {task_id} for file {target_file_id}: {ex}")
        traceback.print_exc()
        return BatchExtractTaskResult(
            success=False,
            error_info=ExtractTaskResponseItemError(
                task_id=task_id, # Ensure task_id is passed back
                target_drive_file_id=target_file_id,
                error="An internal server error occurred during extraction task.",
                detail=str(ex)
            )
        )
    finally:
        # Note: File cleanup is not performed here as files are needed for subsequent processing
        # Gemini AI automatically cleans up unused files after a few hours
        pass