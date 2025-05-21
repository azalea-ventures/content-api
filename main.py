import os
import uuid
import json
import io
import time
import asyncio # Import asyncio for concurrency
import traceback # For logging exceptions
import re # For sanitizing filenames in split output

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status # Keep status
from fastapi.responses import JSONResponse
# from pydantic import BaseModel # Not directly used for defining models in main.py anymore
from typing import List, Dict, Any, Optional, Union, Tuple # Keep all used types
from collections import deque # Keep deque for queue type hint and usage
# from enum import Enum # Enum is now in enhance_helpers

from google.oauth2.service_account import Credentials
# Use alias for google.generativeai.types if needed to avoid conflict elsewhere
from google.generativeai import types as genai_types_google

# Models are still imported from models.py
from models import (
    PromptItem, GeneratedContentItem, LessonDataEnhance, EnhanceRequest, EnhanceResponse,
    SectionInfo, AnalyzeRequestItem, AnalyzeResponseItemSuccess, AnalyzeResponseItemError,
    BatchAnalyzeItemResult, BatchSplitRequest, SplitResponseItemSuccess, SplitResponseItemError,
    BatchSplitItemResult, UploadedFileInfo, ExtractRequestItem, ExtractedSectionDataItem,
    ExtractedDataDict, ExtractResponseItemSuccess, ExtractResponseItemError, BatchExtractItemResult
)

# Service classes are still imported from services
from services.google_drive_service import GoogleDriveService # Uses Credentials
from services.pdf_splitter_service import PdfSplitterService
from services.pdf_text_extractor_service import PdfTextExtractorService # Uses PyMuPDF
from services.generative_analysis_service import GenerativeAnalysisService # Uses API Key for genai calls
import services.google_drive_service # For SCOPES

# --- Import from new helper modules ---
from helpers.analyze_helpers import process_single_analyze_request
from helpers.split_helpers import process_single_split_request
from helpers.enhance_helpers import (
    PromptConstructionStatus, # Enum
    _construct_full_prompt,
    _get_prompt_status,
    _execute_api_call_for_prompt,
    get_or_create_output_item,
    MAX_API_RETRIES_PER_TASK as ENHANCE_MAX_API_RETRIES # Import with an alias if needed
)


load_dotenv()

# --- Configuration ---
SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL_ID = os.getenv('GEMINI_MODEL_ID', 'gemini-1.5-flash-latest')

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
    if SERVICE_ACCOUNT_JSON:
        credentials_info = json.loads(SERVICE_ACCOUNT_JSON)
        credentials = Credentials.from_service_account_info(
            credentials_info, scopes=services.google_drive_service.SCOPES
        )
        print("Google Credentials loaded.")
    else:
         print("SERVICE_ACCOUNT_JSON not found. Services dependent on credentials will not be initialized.")


    if credentials:
        drive_service = GoogleDriveService(credentials)

    pdf_text_extractor_service = PdfTextExtractorService()
    pdf_splitter_service = PdfSplitterService()

    if GEMINI_API_KEY:
        gemini_analysis_service = GenerativeAnalysisService(GEMINI_API_KEY, GEMINI_MODEL_ID)
        print("Generative Analysis service initialized.")
    else:
         print("GEMINI_API_KEY not found. Generative Analysis service will not be initialized.")
    print("All available services initialized.")
except Exception as e:
    print(f"Failed to initialize credentials or services during startup: {e}")
    traceback.print_exc()
    # Consider whether to raise e here, which would stop the app, or let it run in a degraded state.
    # For now, assume if basic init fails, it's critical.
    raise


# --- FastAPI App Setup ---
app = FastAPI(
    title="Document Processing API (Multimodal)",
    description="Analyzes, extracts, splits, and enhances documents using Gemini AI and PyMuPDF.",
    version="1.2.0", # Incremented version
)

# --- CONSTANTS for main endpoint logic (those not moved to helpers) ---
MAX_DATA_DEPENDENCY_RETRIES = 5
RETRY_COOLDOWN_SECONDS = 60


# --- Helper function for /extract endpoint (REMAINS IN main.py as it wasn't listed for move) ---
async def process_single_extract_request(
    request_item: ExtractRequestItem,
    drive_service: GoogleDriveService,
    pdf_text_extractor_service: PdfTextExtractorService,
    gemini_analysis_service: GenerativeAnalysisService,
    output_json_format_example: Dict[str, Any]  # Assuming ExtractedDataDict is Dict[str, Any] effectively
) -> BatchExtractItemResult:
    prompt_doc_id = request_item.prompt_doc_id
    target_file_id = request_item.target_file_id
    print(f"Processing extract request: Prompt Doc ID {prompt_doc_id}, Target File ID {target_file_id}")

    prompt_pdf_stream: Optional[io.BytesIO] = None
    target_pdf_stream: Optional[io.BytesIO] = None
    uploaded_target_file: Optional[genai_types_google.File] = None # Use the alias
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
            # Fallback to export for Google Docs if direct download fails
            # target_mime_type = target_file_info.get('mimeType')
            # if target_mime_type == 'application/vnd.google-apps.document':
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

        # Assuming extract_structured_data_multimodal returns a Dict that matches ExtractedDataDict
        extracted_data_dict = gemini_analysis_service.extract_structured_data_multimodal(
            uploaded_target_file,
            prompt_text,
            output_json_format_example
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
    if not request.lesson_data: return EnhanceResponse(lesson_data=[])
    if not request.prompts: return EnhanceResponse(lesson_data=[l.model_copy(deep=True) for l in request.lesson_data])

    print(f"Enhance request: {len(request.lesson_data)} lessons, {len(request.prompts)} prompt types.")
    
    enhanced_lessons_output: List[LessonDataEnhance] = [
        lesson.model_copy(deep=True) for lesson in request.lesson_data
    ]
    
    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()

    for lesson_idx, _ in enumerate(enhanced_lessons_output):
        for prompt_item in request.prompts:
            data_dependency_deferred_queue.append((lesson_idx, prompt_item, 0)) # 0 initial data dependency checks

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        # Use MAX_API_RETRIES_PER_TASK from enhance_helpers (imported as ENHANCE_MAX_API_RETRIES)
        # and MAX_DATA_DEPENDENCY_RETRIES from main.py for cycle limit
        if processing_cycles > (len(request.lesson_data) * len(request.prompts) * (ENHANCE_MAX_API_RETRIES + MAX_DATA_DEPENDENCY_RETRIES + 5)):
            print("Warning: Max processing cycles reached. Breaking.")
            break
        
        # 1. Prioritize API Retry Queue
        if api_retry_queue:
            if not (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < RETRY_COOLDOWN_SECONDS)):
                r_lesson_idx, r_prompt_item, r_full_prompt, r_api_attempts = api_retry_queue.popleft()
                print(f"Processing API retry: Lesson {r_lesson_idx}, Prompt '{r_prompt_item.prompt_name}', API Attempt {r_api_attempts + 1}")
                rate_limit_or_api_error_on_retry = await _execute_api_call_for_prompt(
                    gemini_analysis_service, r_lesson_idx, r_prompt_item, r_full_prompt, r_api_attempts,
                    enhanced_lessons_output, api_retry_queue
                )
                if rate_limit_or_api_error_on_retry:
                    last_rate_limit_event_time = time.monotonic()
                await asyncio.sleep(0.1) 
                continue

        # 2. Process Data Dependency Deferred Queue
        if data_dependency_deferred_queue:
            current_lesson_idx, current_prompt_item, dd_attempts = data_dependency_deferred_queue.popleft()
            lesson_being_processed = enhanced_lessons_output[current_lesson_idx]
            prompt_name = current_prompt_item.prompt_name
            print(f"Checking data deps: Lesson {current_lesson_idx}, Prompt '{prompt_name}', DD Attempt {dd_attempts + 1}")

            construction_status, full_prompt_text_or_none = _construct_full_prompt(
                current_prompt_item, lesson_being_processed, request.prompts
            )

            if construction_status == PromptConstructionStatus.SUCCESS:
                if full_prompt_text_or_none is None: 
                     print(f"Error: Prompt construction success but no text for {prompt_name}. Skipping.")
                     output_item_err, _ = get_or_create_output_item(lesson_being_processed, prompt_name)
                     output_item_err.status = "ERROR_INTERNAL_CONSTRUCTION"
                     output_item_err.output = "Internal error during prompt construction."
                     continue
                
                if last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < RETRY_COOLDOWN_SECONDS):
                    print(f"Cooldown before API call for '{prompt_name}'. Re-deferring.")
                    data_dependency_deferred_queue.append((current_lesson_idx, current_prompt_item, dd_attempts))
                    await asyncio.sleep(0.1) 
                    continue
                
                api_call_requeued_or_failed_max = await _execute_api_call_for_prompt(
                    gemini_analysis_service, current_lesson_idx, current_prompt_item, full_prompt_text_or_none, 0,
                    enhanced_lessons_output, api_retry_queue
                )
                if api_call_requeued_or_failed_max:
                    last_rate_limit_event_time = time.monotonic()
                await asyncio.sleep(0.1)

            elif construction_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                if dd_attempts + 1 < MAX_DATA_DEPENDENCY_RETRIES:
                    print(f"Data dependency not met for '{prompt_name}'. Re-deferring (DD Attempt {dd_attempts + 2}).")
                    data_dependency_deferred_queue.append((current_lesson_idx, current_prompt_item, dd_attempts + 1))
                    output_item_pend, _ = get_or_create_output_item(lesson_being_processed, prompt_name)
                    output_item_pend.status = "DATA_DEPENDENCY_PENDING"
                    output_item_pend.output = f"Waiting for dependent data (attempt {dd_attempts + 1})."
                else:
                    err_msg = f"Max data dependency retries ({MAX_DATA_DEPENDENCY_RETRIES}) for '{prompt_name}'."
                    print(err_msg)
                    output_item_fail, _ = get_or_create_output_item(lesson_being_processed, prompt_name)
                    output_item_fail.status = "DATA_DEPENDENCY_FAILED"
                    output_item_fail.output = err_msg
            
            await asyncio.sleep(0.05) # Yield control briefly
            continue
            
        # Cooldown logic if both queues were empty or API queue is in cooldown
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0:
             pass # Queues are empty, loop will naturally terminate on next iteration
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < RETRY_COOLDOWN_SECONDS))) and not data_dependency_deferred_queue:
            # This condition means:
            # 1. API queue is empty OR it's in cooldown AND
            # 2. Data dependency queue is empty.
            # So, if API queue is in cooldown, we should wait.
            if last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < RETRY_COOLDOWN_SECONDS):
                remaining_cooldown = RETRY_COOLDOWN_SECONDS - (time.monotonic() - last_rate_limit_event_time)
                if remaining_cooldown > 0.1 : # Only sleep if meaningful time left
                    print(f"Main loop: API cooldown active ({remaining_cooldown:.1f}s), data queue empty. Sleeping.")
                    await asyncio.sleep(remaining_cooldown)

    # Final status updates for items left in queues
    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warning: Processing finished, but queues are not empty. DataQ: {len(data_dependency_deferred_queue)}, ApiQ: {len(api_retry_queue)}")
        # Update status for items left in data_dependency_deferred_queue (timed out)
        for lesson_idx, prompt_item_left, _ in data_dependency_deferred_queue:
            output_item_timeout, _ = get_or_create_output_item(enhanced_lessons_output[lesson_idx], prompt_item_left.prompt_name)
            # Only update if it's still pending or has no status; don't overwrite a more specific error
            if output_item_timeout.status == "DATA_DEPENDENCY_PENDING" or not output_item_timeout.status:
                output_item_timeout.status = "DATA_DEPENDENCY_TIMEOUT"
                output_item_timeout.output = "Processing cycle limit reached while waiting for data."
        # Items left in api_retry_queue should already have their .output field set appropriately by _execute_api_call_for_prompt

    print(f"Finished /enhance request. Processed {len(request.lesson_data)} lessons in {processing_cycles} cycles.")
    return EnhanceResponse(lesson_data=enhanced_lessons_output)

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    if not credentials:
         return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Credentials not loaded. Check GOOGLE_SERVICE_ACCOUNT_JSON."})
    if not drive_service or not pdf_splitter_service or not gemini_analysis_service or not pdf_text_extractor_service: # Check all services
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Required services not initialized. Check configuration and logs."})
    return {"status": "ok"}

# To run locally: uvicorn main:app --reload