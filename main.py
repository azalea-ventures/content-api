import base64
import os
import uuid
import json
import io
import time
import asyncio
import traceback
import re
from collections import deque
from typing import List, Dict, Any, Optional, Tuple

# Import settings first to ensure environment is configured
from config import settings # Assuming config.py is in the same directory or accessible via PYTHONPATH

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from google.oauth2.service_account import Credentials
from google.generativeai import types as genai_types_google

from models import (
    # Enhance specific models
    PromptItem, GeneratedContentItem, Slide, Section, LessonUnit, # Renamed Lesson to LessonUnit
    EnhanceUnitsRequest, EnhanceUnitsResponse, # Renamed
    LessonSimple, EnhanceLessonsRequest, EnhanceLessonsResponse, # New models
    SectionInfo, AnalyzeRequestItem, AnalyzeResponseItemSuccess, AnalyzeResponseItemError,
    BatchAnalyzeItemResult, BatchSplitRequest, SplitResponseItemSuccess, SplitResponseItemError,
    BatchSplitItemResult, UploadedFileInfo, ExtractRequestItem, ExtractedSectionDataItem,
    ExtractedDataDict, ExtractResponseItemSuccess, ExtractResponseItemError, BatchExtractItemResult
)

from services.google_drive_service import GoogleDriveService
from services.pdf_splitter_service import PdfSplitterService
from services.pdf_text_extractor_service import PdfTextExtractorService
from services.generative_analysis_service import GenerativeAnalysisService
import services.google_drive_service # For SCOPES

from helpers.analyze_helpers import process_single_analyze_request
from helpers.split_helpers import process_single_split_request
from helpers.enhance_helpers import (
    PromptConstructionStatus,
    _construct_full_prompt,
    _get_prompt_status,
    _execute_api_call_for_prompt,
    get_or_create_output_item
    # MAX_API_RETRIES_PER_TASK is now used as settings.max_api_retries within enhance_helpers
)
from helpers.extract_helpers import process_single_extract_request


load_dotenv()

# --- Configuration (EXTRACT_OUTPUT_FORMAT_EXAMPLE can stay here or be moved to config.py if preferred) ---
EXTRACT_OUTPUT_FORMAT_EXAMPLE: ExtractedDataDict = {
  "Example Section Name": [
    {
      "page": 1,
      "title": "Example Title",
      "paragraph": "Example paragraph text..."
    },
    {
      "page": 2,
      "paragraph": "Another example paragraph..."
    }
  ],
  "Another Section Name": [
       {
         "page": 5,
         "paragraph": "Text from another section."
       }
  ]
}


# --- Initialize Credentials and Services ---
credentials: Optional[Credentials] = None
drive_service: Optional[GoogleDriveService] = None
pdf_splitter_service: Optional[PdfSplitterService] = None
gemini_analysis_service: Optional[GenerativeAnalysisService] = None
pdf_text_extractor_service: Optional[PdfTextExtractorService] = None

try:
    # Corrected: Use google_service_account_json_base64 and decode
    if settings.google_service_account_json_base64:
        try:
            decoded_json_string = base64.b64decode(settings.google_service_account_json_base64).decode('utf-8')
            credentials_info = json.loads(decoded_json_string)
            credentials = Credentials.from_service_account_info(
                credentials_info, scopes=services.google_drive_service.SCOPES
            )
            print("Google Credentials loaded and decoded from Base64 settings.")
        except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"Error decoding GOOGLE_SERVICE_ACCOUNT_JSON_BASE64: {e}")
            print("Ensure the environment variable contains a valid Base64 encoded JSON string.")
            credentials = None 
    else:
         print("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 not found in settings. Services dependent on credentials will not be initialized.")

    if credentials:
        drive_service = GoogleDriveService(credentials)

    pdf_text_extractor_service = PdfTextExtractorService()
    pdf_splitter_service = PdfSplitterService()

    if settings.gemini_api_key:
        gemini_analysis_service = GenerativeAnalysisService(settings.gemini_api_key, settings.gemini_model_id)
        print(f"Generative Analysis service initialized with model: {settings.gemini_model_id}.")
    else:
         print("GEMINI_API_KEY not found in settings. Generative Analysis service will not be initialized.")
    print("All available services initialized.")
except Exception as e:
    print(f"Failed to initialize credentials or services during startup: {e}")
    traceback.print_exc()
    raise


# --- FastAPI App Setup ---
app = FastAPI(
    title="Document Processing API (Multimodal)",
    description="Analyzes, extracts, splits, and enhances documents using Gemini AI and PyMuPDF.",
    version="1.4.0", # Incremented version
)

# --- API Endpoints ---

@app.post("/extract", response_model=List[BatchExtractItemResult], status_code=status.HTTP_200_OK)
async def extract_data_endpoint(requests: List[ExtractRequestItem]):
    if not drive_service or not pdf_text_extractor_service or not gemini_analysis_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Required services (Drive, PDF Extractor, Generative Analysis) are not configured or failed to initialize."
        )
    if not requests:
        return []
    print(f"Received batch extract request for {len(requests)} items.")
    extract_tasks = [
        process_single_extract_request(
            req,
            drive_service,
            pdf_text_extractor_service,
            gemini_analysis_service,
            EXTRACT_OUTPUT_FORMAT_EXAMPLE
        ) for req in requests
    ]
    batch_results = await asyncio.gather(*extract_tasks)
    print(f"Finished batch extract request. Processed {len(requests)} items.")
    return batch_results

@app.post("/analyze", response_model=List[BatchAnalyzeItemResult], status_code=status.HTTP_200_OK)
async def analyze_documents_endpoint(requests: List[AnalyzeRequestItem]):
    if not drive_service or not gemini_analysis_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Required services (Drive, Generative Analysis) are not configured or failed to initialize."
        )
    if not requests:
        return []
    print(f"Received batch analyze request for {len(requests)} files.")
    analysis_tasks = [
        process_single_analyze_request(req.file_id, drive_service, gemini_analysis_service) for req in requests
    ]
    batch_results = await asyncio.gather(*analysis_tasks)
    print(f"Finished batch analyze request. Processed {len(requests)} files.")
    return batch_results

@app.post("/split", response_model=List[BatchSplitItemResult], status_code=status.HTTP_200_OK)
async def split_documents_endpoint(request: BatchSplitRequest):
    if not drive_service or not pdf_splitter_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Required services (Drive, PDF Splitter) are not configured or failed to initialize."
        )
    files_to_split = request.files_to_split
    if not files_to_split:
        return []
    print(f"Received batch split request for {len(files_to_split)} files.")
    split_tasks = [
        process_single_split_request(item, drive_service, pdf_splitter_service) for item in files_to_split
    ]
    batch_results = await asyncio.gather(*split_tasks)
    print(f"Finished batch split request. Processed {len(files_to_split)} files.")
    return batch_results

@app.post("/enhance/units", response_model=EnhanceUnitsResponse, status_code=status.HTTP_200_OK)
async def enhance_units_endpoint(request: EnhanceUnitsRequest):
    if not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Generative Analysis service not initialized.")
    if not request.lessons:
        return EnhanceUnitsResponse(lessons=[])
    
    active_prompts = request.prompts if request.prompts is not None else []
    if not active_prompts:
        print("Warning: No prompts provided in /enhance/units request. Returning original unit data.")
        return EnhanceUnitsResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])

    print(f"Enhance Units request: {len(request.lessons)} units, {len(active_prompts)} prompt types.")
    
    enhanced_units_output: List[LessonUnit] = [
        unit.model_copy(deep=True) for unit in request.lessons
    ]
    
    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()

    total_slide_prompts = 0
    for lesson_idx, lesson_obj in enumerate(enhanced_units_output):
        for section_idx, section_obj in enumerate(lesson_obj.sections):
            for slide_idx, _ in enumerate(section_obj.slides): # Slide object itself not needed for queue init
                for prompt_item in active_prompts:
                    # Task context for unit slides
                    task_context = {
                        "type": "unit_slide", 
                        "lesson_idx": lesson_idx, 
                        "section_idx": section_idx, 
                        "slide_idx": slide_idx,
                        "prompt_item": prompt_item,
                        "attempt_count": 0 # For data dependency
                    }
                    data_dependency_deferred_queue.append(task_context)
                    total_slide_prompts +=1
    
    if total_slide_prompts == 0:
        print("Warning: No slides found in the provided units to process.")
        return EnhanceUnitsResponse(lessons=enhanced_units_output)

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0
    max_processing_cycles_heuristic = total_slide_prompts * (
        settings.max_api_retries + settings.max_data_dependency_retries + 5
    )

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        if processing_cycles > max_processing_cycles_heuristic:
            print(f"Warning: Max processing cycles ({max_processing_cycles_heuristic}) reached for /enhance/units. Breaking.")
            break
        
        if api_retry_queue:
            if not (last_rate_limit_event_time and 
                    (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds)):
                api_task = api_retry_queue.popleft()
                # Ensure this task is for a unit_slide
                if api_task.get("type") == "unit_slide":
                    l_idx, s_idx, sl_idx = api_task["lesson_idx"], api_task["section_idx"], api_task["slide_idx"]
                    p_item, fp_text, api_att = api_task["prompt_item"], api_task["full_prompt_text"], api_task["api_attempt_count"]
                    
                    item_to_process = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                    item_id_log = item_to_process.name or f"UnitL{l_idx}S{s_idx}Sl{sl_idx}"
                    
                    print(f"Processing API retry (Unit): Item '{item_id_log}', Prompt '{p_item.prompt_name}', API Attempt {api_att + 1}")
                    
                    queue_ctx_for_helper = {"type": "unit_slide", "lesson_idx": l_idx, "section_idx": s_idx, "slide_idx": sl_idx}

                    rate_limit_hit = await _execute_api_call_for_prompt(
                        gemini_service=gemini_analysis_service, 
                        item_to_process=item_to_process,
                        item_identifier_for_log=item_id_log,
                        prompt_item=p_item, 
                        full_prompt_text=fp_text, 
                        api_attempt_count=api_att,
                        api_retry_queue=api_retry_queue,
                        queue_context=queue_ctx_for_helper
                    )
                    if rate_limit_hit:
                        last_rate_limit_event_time = time.monotonic()
                else: # Not a unit_slide task, put it back (shouldn't happen if queues are separate)
                    api_retry_queue.appendleft(api_task)
                await asyncio.sleep(0.1) 
                continue

        if data_dependency_deferred_queue:
            data_task = data_dependency_deferred_queue.popleft()
            if data_task.get("type") == "unit_slide":
                l_idx, s_idx, sl_idx = data_task["lesson_idx"], data_task["section_idx"], data_task["slide_idx"]
                current_prompt_item, dd_attempts = data_task["prompt_item"], data_task["attempt_count"]
                
                item_being_processed = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                prompt_name = current_prompt_item.prompt_name
                item_id_log = item_being_processed.name or f"UnitL{l_idx}S{s_idx}Sl{sl_idx}"

                print(f"Checking data deps (Unit): Item '{item_id_log}', Prompt '{prompt_name}', DD Attempt {dd_attempts + 1}")

                construction_status, full_prompt_text_or_none = _construct_full_prompt(
                    current_prompt_item, item_being_processed, active_prompts
                )

                if construction_status == PromptConstructionStatus.SUCCESS:
                    if full_prompt_text_or_none is None: 
                        print(f"Error: Prompt construction success but no text for '{prompt_name}' on item '{item_id_log}'. Skipping.")
                        output_item_err, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_err.status = "ERROR_INTERNAL_CONSTRUCTION"
                        output_item_err.output = "Internal error during prompt construction."
                        continue
                    
                    if last_rate_limit_event_time and \
                       (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                        print(f"Cooldown before API call for '{prompt_name}' on item '{item_id_log}'. Re-deferring.")
                        data_task["attempt_count"] = dd_attempts # Keep same dd_attempts
                        data_dependency_deferred_queue.append(data_task)
                        await asyncio.sleep(0.1) 
                        continue
                    
                    queue_ctx_for_helper = {"type": "unit_slide", "lesson_idx": l_idx, "section_idx": s_idx, "slide_idx": sl_idx}
                    api_call_requeued = await _execute_api_call_for_prompt(
                        gemini_service=gemini_analysis_service, 
                        item_to_process=item_being_processed,
                        item_identifier_for_log=item_id_log,
                        prompt_item=current_prompt_item, 
                        full_prompt_text=full_prompt_text_or_none, 
                        api_attempt_count=0,
                        api_retry_queue=api_retry_queue,
                        queue_context=queue_ctx_for_helper
                    )
                    if api_call_requeued:
                        last_rate_limit_event_time = time.monotonic()
                
                elif construction_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                    if dd_attempts + 1 < settings.max_data_dependency_retries:
                        print(f"Data dependency not met for '{prompt_name}' on item '{item_id_log}'. Re-deferring (DD Attempt {dd_attempts + 2}).")
                        data_task["attempt_count"] = dd_attempts + 1
                        data_dependency_deferred_queue.append(data_task)
                        output_item_pend, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_pend.status = "DATA_DEPENDENCY_PENDING"
                        output_item_pend.output = f"Waiting for dependent data (attempt {dd_attempts + 1})."
                    else:
                        err_msg = f"Max data dependency retries ({settings.max_data_dependency_retries}) for '{prompt_name}' on item '{item_id_log}'."
                        print(err_msg)
                        output_item_fail, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_fail.status = "DATA_DEPENDENCY_FAILED"
                        output_item_fail.output = err_msg
            else: # Not a unit_slide task for this queue (should not happen with current init)
                data_dependency_deferred_queue.appendleft(data_task)
            await asyncio.sleep(0.05)
            continue
            
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0:
             pass 
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds))) and not data_dependency_deferred_queue:
            if last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                remaining_cooldown = settings.retry_cooldown_seconds - (time.monotonic() - last_rate_limit_event_time)
                if remaining_cooldown > 0.1 : 
                    print(f"Main loop (Units): API cooldown active ({remaining_cooldown:.1f}s), data queue empty. Sleeping.")
                    await asyncio.sleep(remaining_cooldown)

    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warning: Processing /enhance/units finished, but queues are not empty. DataQ: {len(data_dependency_deferred_queue)}, ApiQ: {len(api_retry_queue)}")
        for task in data_dependency_deferred_queue:
             if task.get("type") == "unit_slide":
                l_idx, s_idx, sl_idx = task["lesson_idx"], task["section_idx"], task["slide_idx"]
                prompt_item_left = task["prompt_item"]
                item_left = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                output_item_timeout, _ = get_or_create_output_item(item_left, prompt_item_left.prompt_name)
                if output_item_timeout.status == "DATA_DEPENDENCY_PENDING" or not output_item_timeout.status:
                    output_item_timeout.status = "DATA_DEPENDENCY_TIMEOUT"
                    output_item_timeout.output = "Processing cycle limit reached while waiting for data."

    print(f"Finished /enhance/units request. Processed {total_slide_prompts} total slide-prompt tasks in {processing_cycles} cycles.")
    return EnhanceUnitsResponse(lessons=enhanced_units_output)


@app.post("/enhance/lessons", response_model=EnhanceLessonsResponse, status_code=status.HTTP_200_OK)
async def enhance_simple_lessons_endpoint(request: EnhanceLessonsRequest):
    if not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Generative Analysis service not initialized.")
    if not request.lessons:
        return EnhanceLessonsResponse(lessons=[])

    active_prompts = request.prompts if request.prompts is not None else []
    if not active_prompts:
        print("Warning: No prompts provided in /enhance/lessons request. Returning original lesson data.")
        return EnhanceLessonsResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])

    print(f"Enhance Lessons request: {len(request.lessons)} lessons, {len(active_prompts)} prompt types.")

    enhanced_simple_lessons_output: List[LessonSimple] = [
        lesson.model_copy(deep=True) for lesson in request.lessons
    ]

    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()

    total_lesson_prompts = 0
    for lesson_simple_idx, _ in enumerate(enhanced_simple_lessons_output):
        for prompt_item in active_prompts:
            task_context = {
                "type": "lesson_simple",
                "lesson_simple_idx": lesson_simple_idx,
                "prompt_item": prompt_item,
                "attempt_count": 0 # For data dependency
            }
            data_dependency_deferred_queue.append(task_context)
            total_lesson_prompts += 1
    
    if total_lesson_prompts == 0:
        print("No lesson-prompt tasks to process for /enhance/lessons.")
        return EnhanceLessonsResponse(lessons=enhanced_simple_lessons_output)

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0
    max_processing_cycles_heuristic = total_lesson_prompts * (settings.max_api_retries + settings.max_data_dependency_retries + 5)

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        if processing_cycles > max_processing_cycles_heuristic:
            print(f"Warning: Max processing cycles reached for /enhance/lessons. Breaking.")
            break

        if api_retry_queue:
            if not (last_rate_limit_event_time and 
                    (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds)):
                api_task = api_retry_queue.popleft()
                if api_task.get("type") == "lesson_simple":
                    ls_idx = api_task["lesson_simple_idx"]
                    p_item, fp_text, api_att = api_task["prompt_item"], api_task["full_prompt_text"], api_task["api_attempt_count"]
                    
                    item_to_process = enhanced_simple_lessons_output[ls_idx]
                    # Use a field from LessonSimple for logging if available, e.g., file_name or a lesson_id
                    item_id_log = getattr(item_to_process, 'file_name', None) or \
                                  getattr(item_to_process, 'lesson_id', None) or \
                                  f"LessonSimple{ls_idx}"

                    print(f"Processing API retry (LessonSimple): Item '{item_id_log}', Prompt '{p_item.prompt_name}', API Attempt {api_att + 1}")
                    
                    queue_ctx_for_helper = {"type": "lesson_simple", "lesson_simple_idx": ls_idx}
                    rate_limit_hit = await _execute_api_call_for_prompt(
                        gemini_service=gemini_analysis_service,
                        item_to_process=item_to_process,
                        item_identifier_for_log=item_id_log,
                        prompt_item=p_item,
                        full_prompt_text=fp_text,
                        api_attempt_count=api_att,
                        api_retry_queue=api_retry_queue,
                        queue_context=queue_ctx_for_helper
                    )
                    if rate_limit_hit: last_rate_limit_event_time = time.monotonic()
                else: 
                    api_retry_queue.appendleft(api_task) # Put back if not for this endpoint type
                await asyncio.sleep(0.1)
                continue
        
        if data_dependency_deferred_queue:
            data_task = data_dependency_deferred_queue.popleft()
            if data_task.get("type") == "lesson_simple":
                ls_idx = data_task["lesson_simple_idx"]
                current_prompt_item, dd_attempts = data_task["prompt_item"], data_task["attempt_count"]

                item_being_processed = enhanced_simple_lessons_output[ls_idx]
                prompt_name = current_prompt_item.prompt_name
                item_id_log = getattr(item_being_processed, 'file_name', None) or \
                              getattr(item_being_processed, 'lesson_id', None) or \
                              f"LessonSimple{ls_idx}"
                
                print(f"Checking data deps (LessonSimple): Item '{item_id_log}', Prompt '{prompt_name}', DD Attempt {dd_attempts + 1}")

                construction_status, full_prompt_text_or_none = _construct_full_prompt(
                    current_prompt_item, item_being_processed, active_prompts
                )

                if construction_status == PromptConstructionStatus.SUCCESS:
                    if full_prompt_text_or_none is None:
                        print(f"Error: Prompt construction success but no text for '{prompt_name}' on item '{item_id_log}'. Skipping.")
                        output_item_err, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_err.status = "ERROR_INTERNAL_CONSTRUCTION"
                        output_item_err.output = "Internal error during prompt construction."
                        continue

                    if last_rate_limit_event_time and \
                       (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                        print(f"Cooldown before API call for '{prompt_name}' on item '{item_id_log}'. Re-deferring.")
                        data_task["attempt_count"] = dd_attempts 
                        data_dependency_deferred_queue.append(data_task)
                        await asyncio.sleep(0.1) 
                        continue
                    
                    queue_ctx_for_helper = {"type": "lesson_simple", "lesson_simple_idx": ls_idx}
                    api_call_requeued = await _execute_api_call_for_prompt(
                        gemini_service=gemini_analysis_service,
                        item_to_process=item_being_processed,
                        item_identifier_for_log=item_id_log,
                        prompt_item=current_prompt_item,
                        full_prompt_text=full_prompt_text_or_none,
                        api_attempt_count=0,
                        api_retry_queue=api_retry_queue,
                        queue_context=queue_ctx_for_helper
                    )
                    if api_call_requeued: last_rate_limit_event_time = time.monotonic()
                
                elif construction_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                    if dd_attempts + 1 < settings.max_data_dependency_retries:
                        print(f"Data dependency not met for '{prompt_name}' on item '{item_id_log}'. Re-deferring (DD Attempt {dd_attempts + 2}).")
                        data_task["attempt_count"] = dd_attempts + 1
                        data_dependency_deferred_queue.append(data_task)
                        output_item_pend, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_pend.status = "DATA_DEPENDENCY_PENDING"
                        output_item_pend.output = f"Waiting for dependent data (attempt {dd_attempts + 1})."
                    else:
                        err_msg = f"Max data dependency retries ({settings.max_data_dependency_retries}) for '{prompt_name}' on item '{item_id_log}'."
                        print(err_msg)
                        output_item_fail, _ = get_or_create_output_item(item_being_processed, prompt_name)
                        output_item_fail.status = "DATA_DEPENDENCY_FAILED"
                        output_item_fail.output = err_msg
            else:
                data_dependency_deferred_queue.appendleft(data_task) # Put back if not for this endpoint type
            await asyncio.sleep(0.05)
            continue
            
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0:
             pass 
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds))) and not data_dependency_deferred_queue:
            if last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                remaining_cooldown = settings.retry_cooldown_seconds - (time.monotonic() - last_rate_limit_event_time)
                if remaining_cooldown > 0.1 : 
                    print(f"Main loop (Lessons): API cooldown active ({remaining_cooldown:.1f}s), data queue empty. Sleeping.")
                    await asyncio.sleep(remaining_cooldown)
    
    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warning: Processing /enhance/lessons finished, but queues are not empty. DataQ: {len(data_dependency_deferred_queue)}, ApiQ: {len(api_retry_queue)}")
        for task in list(data_dependency_deferred_queue): # Iterate over a copy if modifying
             if task.get("type") == "lesson_simple":
                ls_idx = task["lesson_simple_idx"]
                prompt_item_left = task["prompt_item"]
                item_left = enhanced_simple_lessons_output[ls_idx]
                output_item_timeout, _ = get_or_create_output_item(item_left, prompt_item_left.prompt_name)
                if output_item_timeout.status == "DATA_DEPENDENCY_PENDING" or not output_item_timeout.status:
                    output_item_timeout.status = "DATA_DEPENDENCY_TIMEOUT"
                    output_item_timeout.output = "Processing cycle limit reached while waiting for data."

    print(f"Finished /enhance/lessons request. Processed {total_lesson_prompts} total lesson-prompt tasks in {processing_cycles} cycles.")
    return EnhanceLessonsResponse(lessons=enhanced_simple_lessons_output)

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    if not credentials:
         return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Credentials not loaded. Check GOOGLE_SERVICE_ACCOUNT_JSON in settings."})
    if not drive_service or not pdf_splitter_service or not gemini_analysis_service or not pdf_text_extractor_service:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "One or more required services not initialized. Check configuration and logs."})
    return {"status": "ok"}

# To run locally: uvicorn main:app --reload