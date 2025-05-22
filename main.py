import base64
import os
import uuid
import json
import io
import time
import asyncio
import traceback
import re
from collections import deque # For type hint and usage
from typing import List, Dict, Any, Optional, Tuple

# Import settings first to ensure environment is configured
from config import settings

from dotenv import load_dotenv # Still useful if .env contains other non-Pydantic managed vars or for local override clarity
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from google.oauth2.service_account import Credentials
from google.generativeai import types as genai_types_google

from models import (
    PromptItem, GeneratedContentItem, Slide, Section, Lesson,
    EnhanceRequest, EnhanceResponse,
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


load_dotenv() # Load .env after importing settings if settings doesn't auto-load, or for other uses.
              # pydantic-settings usually handles .env loading if configured.

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
            credentials = None # Ensure it's None if parsing failed
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
    version="1.3.2", # Incremented version
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

@app.post("/enhance", response_model=EnhanceResponse, status_code=status.HTTP_200_OK)
async def enhance_lessons_endpoint(request: EnhanceRequest):
    if not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Generative Analysis service not initialized.")
    if not request.lessons:
        return EnhanceResponse(lessons=[])
    # Allow empty prompts list if user wants to just pass through lesson structure (though less useful for /enhance)
    # if not request.prompts:
    #     print("Warning: No prompts provided in /enhance request. Returning original lesson data.")
    #     return EnhanceResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])

    active_prompts = request.prompts if request.prompts is not None else []
    if not active_prompts:
        print("Warning: No prompts provided in /enhance request. Returning original lesson data.")
        return EnhanceResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])


    print(f"Enhance request: {len(request.lessons)} lessons, {len(active_prompts)} prompt types.")
    
    enhanced_lessons_output: List[Lesson] = [
        lesson.model_copy(deep=True) for lesson in request.lessons
    ]
    
    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()

    total_slide_prompts = 0
    for lesson_idx, lesson_obj in enumerate(enhanced_lessons_output):
        for section_idx, section_obj in enumerate(lesson_obj.sections):
            for slide_idx, _ in enumerate(section_obj.slides):
                for prompt_item in active_prompts: # Use active_prompts
                    data_dependency_deferred_queue.append((lesson_idx, section_idx, slide_idx, prompt_item, 0))
                    total_slide_prompts +=1
    
    if total_slide_prompts == 0:
        print("Warning: No slides found or no prompts provided to process.")
        return EnhanceResponse(lessons=enhanced_lessons_output)

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0
    max_processing_cycles_heuristic = total_slide_prompts * (
        settings.max_api_retries + settings.max_data_dependency_retries + 5
    )

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        if processing_cycles > max_processing_cycles_heuristic:
            print(f"Warning: Max processing cycles ({max_processing_cycles_heuristic}) reached. Breaking.")
            break
        
        if api_retry_queue:
            if not (last_rate_limit_event_time and 
                    (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds)):
                l_idx, s_idx, sl_idx, p_item, fp_text, api_att = api_retry_queue.popleft()
                
                slide_name_log = "Unknown Slide"
                try:
                    slide_name_log = enhanced_lessons_output[l_idx].sections[s_idx].slides[sl_idx].name or f"L{l_idx}S{s_idx}Sl{sl_idx}"
                except IndexError: pass # Keep default log name
                print(f"Processing API retry: Slide '{slide_name_log}', Prompt '{p_item.prompt_name}', API Attempt {api_att + 1}")
                
                rate_limit_or_api_error_on_retry = await _execute_api_call_for_prompt(
                    gemini_service=gemini_analysis_service, 
                    lesson_idx=l_idx, section_idx=s_idx, slide_idx=sl_idx, 
                    prompt_item=p_item, full_prompt_text=fp_text, api_attempt_count=api_att,
                    enhanced_lessons_output=enhanced_lessons_output, api_retry_queue=api_retry_queue
                )
                if rate_limit_or_api_error_on_retry:
                    last_rate_limit_event_time = time.monotonic()
                await asyncio.sleep(0.1) 
                continue

        if data_dependency_deferred_queue:
            l_idx, s_idx, sl_idx, current_prompt_item, dd_attempts = data_dependency_deferred_queue.popleft()
            
            current_slide_being_processed = enhanced_lessons_output[l_idx].sections[s_idx].slides[sl_idx]
            prompt_name = current_prompt_item.prompt_name
            slide_name_log = current_slide_being_processed.name or f"L{l_idx}S{s_idx}Sl{sl_idx}"

            print(f"Checking data deps: Slide '{slide_name_log}', Prompt '{prompt_name}', DD Attempt {dd_attempts + 1}")

            construction_status, full_prompt_text_or_none = _construct_full_prompt(
                current_prompt_item, current_slide_being_processed, active_prompts # Pass active_prompts
            )

            if construction_status == PromptConstructionStatus.SUCCESS:
                if full_prompt_text_or_none is None: 
                     print(f"Error: Prompt construction success but no text for '{prompt_name}' on slide '{slide_name_log}'. Skipping.")
                     output_item_err, _ = get_or_create_output_item(current_slide_being_processed, prompt_name)
                     output_item_err.status = "ERROR_INTERNAL_CONSTRUCTION"
                     output_item_err.output = "Internal error during prompt construction."
                     continue
                
                if last_rate_limit_event_time and \
                   (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                    print(f"Cooldown before API call for '{prompt_name}' on slide '{slide_name_log}'. Re-deferring.")
                    data_dependency_deferred_queue.append((l_idx, s_idx, sl_idx, current_prompt_item, dd_attempts))
                    await asyncio.sleep(0.1) 
                    continue
                
                api_call_requeued_or_failed_max = await _execute_api_call_for_prompt(
                    gemini_service=gemini_analysis_service, 
                    lesson_idx=l_idx, section_idx=s_idx, slide_idx=sl_idx, 
                    prompt_item=current_prompt_item, full_prompt_text=full_prompt_text_or_none, 
                    api_attempt_count=0,
                    enhanced_lessons_output=enhanced_lessons_output, api_retry_queue=api_retry_queue
                )
                if api_call_requeued_or_failed_max:
                    last_rate_limit_event_time = time.monotonic()
                await asyncio.sleep(0.1)

            elif construction_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                if dd_attempts + 1 < settings.max_data_dependency_retries:
                    print(f"Data dependency not met for '{prompt_name}' on slide '{slide_name_log}'. Re-deferring (DD Attempt {dd_attempts + 2}).")
                    data_dependency_deferred_queue.append((l_idx, s_idx, sl_idx, current_prompt_item, dd_attempts + 1))
                    output_item_pend, _ = get_or_create_output_item(current_slide_being_processed, prompt_name)
                    output_item_pend.status = "DATA_DEPENDENCY_PENDING"
                    output_item_pend.output = f"Waiting for dependent data (attempt {dd_attempts + 1})."
                else:
                    err_msg = f"Max data dependency retries ({settings.max_data_dependency_retries}) for '{prompt_name}' on slide '{slide_name_log}'."
                    print(err_msg)
                    output_item_fail, _ = get_or_create_output_item(current_slide_being_processed, prompt_name)
                    output_item_fail.status = "DATA_DEPENDENCY_FAILED"
                    output_item_fail.output = err_msg
            
            await asyncio.sleep(0.05)
            continue
            
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0:
             pass 
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds))) and not data_dependency_deferred_queue:
            if last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds):
                remaining_cooldown = settings.retry_cooldown_seconds - (time.monotonic() - last_rate_limit_event_time)
                if remaining_cooldown > 0.1 : 
                    print(f"Main loop: API cooldown active ({remaining_cooldown:.1f}s), data queue empty. Sleeping.")
                    await asyncio.sleep(remaining_cooldown)

    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warning: Processing finished, but queues are not empty. DataQ: {len(data_dependency_deferred_queue)}, ApiQ: {len(api_retry_queue)}")
        for l_idx, s_idx, sl_idx, prompt_item_left, _ in data_dependency_deferred_queue:
            slide_left = enhanced_lessons_output[l_idx].sections[s_idx].slides[sl_idx]
            output_item_timeout, _ = get_or_create_output_item(slide_left, prompt_item_left.prompt_name)
            if output_item_timeout.status == "DATA_DEPENDENCY_PENDING" or not output_item_timeout.status:
                output_item_timeout.status = "DATA_DEPENDENCY_TIMEOUT"
                output_item_timeout.output = "Processing cycle limit reached while waiting for data."

    print(f"Finished /enhance request. Processed {total_slide_prompts} total slide-prompt tasks in {processing_cycles} cycles.")
    return EnhanceResponse(lessons=enhanced_lessons_output)

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    if not credentials: # Check if credentials object was successfully created
         return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Credentials not loaded. Check GOOGLE_SERVICE_ACCOUNT_JSON in settings."})
    # Check for existence of service objects
    if not drive_service or not pdf_splitter_service or not gemini_analysis_service or not pdf_text_extractor_service:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "One or more required services not initialized. Check configuration and logs."})
    return {"status": "ok"}

# To run locally: uvicorn main:app --reload