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

from config import settings

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import JSONResponse

from google.oauth2.service_account import Credentials
from google.generativeai import types as genai_types_google

from models import (
    PromptItem, GeneratedContentItem, Slide, Section, LessonUnit,
    EnhanceUnitsRequest, EnhanceUnitsResponse,
    LessonSimple, EnhanceLessonsRequest, EnhanceLessonsResponse,
    SectionInfo, AnalyzeRequestItem, AnalyzeResponseItemSuccess, AnalyzeResponseItemError,
    BatchAnalyzeItemResult, SplitRequest, SplitResponseItemSuccess, SplitResponseItemError,
    BatchSplitItemResult, UploadedFileInfo, 
    ExtractedDataDict,
    # New refactored extract models
    RefactoredExtractResponse, SectionExtractPrompt, PageInfo, SectionWithPrompts, AnalyzeResultWithPrompts,
    # New extract models for n8n workflow
    ExtractRequest, ExtractResponse
)

from services.google_drive_service import GoogleDriveService, StorageService
from services.supabase_storage_service import SupabaseStorageService
from services.pdf_splitter_service import PdfSplitterService
from services.pdf_text_extractor_service import PdfTextExtractorService
from services.generative_analysis_service import GenerativeAnalysisService
import services.google_drive_service

from helpers.analyze_helpers import process_single_analyze_request
from helpers.split_helpers import process_single_split_request
from helpers.enhance_helpers import (
    PromptConstructionStatus,
    _construct_full_prompt,
    _get_prompt_status,
    _execute_api_call_for_prompt,
    get_or_create_output_item
)
from helpers.refactored_extract_helpers import process_refactored_extract_request, process_extract_request


load_dotenv()

EXTRACT_OUTPUT_FORMAT_EXAMPLE: ExtractedDataDict = {
  "Example Section Name": [
    {"page": 1, "title": "Example Title", "paragraph": "Example paragraph text..."},
    {"page": 2, "paragraph": "Another example paragraph..."}
  ],
  "Another Section Name": [{"page": 5, "paragraph": "Text from another section."}]
}

credentials: Optional[Credentials] = None
storage_service: Optional[StorageService] = None
pdf_splitter_service: Optional[PdfSplitterService] = None
gemini_analysis_service: Optional[GenerativeAnalysisService] = None
pdf_text_extractor_service: Optional[PdfTextExtractorService] = None

try:
    if settings.storage_backend == "google_drive":
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
                credentials = None 
        else:
            print("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 not found in settings.")
        if credentials:
            storage_service = GoogleDriveService(credentials)
    elif settings.storage_backend == "supabase":
        storage_service = SupabaseStorageService()
        print("SupabaseStorageService initialized.")
    else:
        print(f"Unknown storage backend: {settings.storage_backend}")

    if settings.gemini_api_key:
        gemini_analysis_service = GenerativeAnalysisService(settings.gemini_api_key, settings.gemini_model_id)
        print(f"Generative Analysis service initialized with model: {settings.gemini_model_id}.")
    else:
         print("GEMINI_API_KEY not found in settings.")
    
    # Initialize PDF splitter service
    pdf_splitter_service = PdfSplitterService()
    print("PDF Splitter service initialized.")
    
    print("All available services initialized.")
except Exception as e:
    print(f"Failed to initialize credentials or services during startup: {e}")
    traceback.print_exc()
    raise

app = FastAPI(
    title="Content API",
    description="Analyzes, extracts, splits, and enhances documents using Gemini AI and PyMuPDF.",
    version="1.2.0", 
)

@app.post("/extract", response_model=ExtractResponse, status_code=status.HTTP_200_OK)
async def extract_endpoint(request: ExtractRequest):
    if not storage_service or not gemini_analysis_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Required services (Storage, Generative Analysis) are not configured or failed to initialize."
        )

    if not request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No request body provided.")

    print(f"Extract request for file: {request.originalDriveFileId}, section: {request.section.sectionName}")
    result = await process_extract_request(request, storage_service, gemini_analysis_service)
    print(f"Finished extract for file: {request.originalDriveFileId}, section: {request.section.sectionName}")
    return result

@app.post("/analyze", response_model=BatchAnalyzeItemResult, status_code=status.HTTP_200_OK)
async def analyze_documents_endpoint(request: AnalyzeRequestItem):
    if not storage_service or not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Required services for /analyze not initialized.")
    if not request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No request body provided.")
    print(f"Analyze request: file_id={request.file_id}, genai_file_name={request.genai_file_name}")
    result = await process_single_analyze_request(request.file_id, request.prompt_text, storage_service, gemini_analysis_service, request.genai_file_name)
    print(f"Finished analyze for file_id={request.file_id}.")
    return result

@app.post("/split", response_model=BatchSplitItemResult, status_code=status.HTTP_200_OK)
async def split_documents_endpoint(request: SplitRequest):
    if not storage_service or not pdf_splitter_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Required services for /split not initialized.")
    if not request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No request body provided.")
    print(f"Split request: file_id={request.originalDriveFileId}")
    result = await process_single_split_request(request, storage_service, pdf_splitter_service)
    print(f"Finished split for file_id={request.originalDriveFileId}.")
    return result

@app.post("/enhance/units", response_model=EnhanceUnitsResponse, status_code=status.HTTP_200_OK)
async def enhance_units_endpoint(request: EnhanceUnitsRequest):
    if not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Generative Analysis service not initialized.")
    if not request.lessons: return EnhanceUnitsResponse(lessons=[])
    
    active_prompts = request.prompts if request.prompts is not None else []
    if not active_prompts:
        print("Warning: No prompts for /enhance/units. Returning original data.")
        return EnhanceUnitsResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])

    print(f"Enhance Units: {len(request.lessons)} units, {len(active_prompts)} prompts.")
    enhanced_units_output: List[LessonUnit] = [unit.model_copy(deep=True) for unit in request.lessons]
    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()
    total_slide_prompts = 0

    for lesson_idx, lesson_obj in enumerate(enhanced_units_output):
        for section_idx, section_obj in enumerate(lesson_obj.sections):
            for slide_idx, _ in enumerate(section_obj.slides):
                for prompt_item in active_prompts:
                    task_context = {"type": "unit_slide", "lesson_idx": lesson_idx, "section_idx": section_idx, "slide_idx": slide_idx, "prompt_item": prompt_item, "attempt_count": 0}
                    data_dependency_deferred_queue.append(task_context)
                    total_slide_prompts +=1
    
    if total_slide_prompts == 0: return EnhanceUnitsResponse(lessons=enhanced_units_output)

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0
    max_processing_cycles_heuristic = total_slide_prompts * (settings.max_api_retries + settings.max_data_dependency_retries + 5)

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        if processing_cycles > max_processing_cycles_heuristic: break
        
        if api_retry_queue:
            if not (last_rate_limit_event_time and (time.monotonic() - last_rate_limit_event_time < settings.retry_cooldown_seconds)):
                api_task = api_retry_queue.popleft()
                if api_task.get("type") == "unit_slide":
                    l_idx,s_idx,sl_idx,p_item,fp_text,api_att = api_task["lesson_idx"],api_task["section_idx"],api_task["slide_idx"],api_task["prompt_item"],api_task["full_prompt_text"],api_task["api_attempt_count"]
                    item_to_process = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                    item_id_log = item_to_process.name or f"U_L{l_idx}S{s_idx}Sl{sl_idx}"
                    print(f"API Retry (Unit): Item '{item_id_log}', Prompt '{p_item.prompt_name}', Attempt {api_att + 1}")
                    queue_ctx = {"type": "unit_slide", "lesson_idx": l_idx, "section_idx": s_idx, "slide_idx": sl_idx}
                    if await _execute_api_call_for_prompt(gemini_analysis_service,item_to_process,item_id_log,p_item,fp_text,api_att,api_retry_queue,queue_ctx):
                        last_rate_limit_event_time = time.monotonic()
                else: api_retry_queue.appendleft(api_task)
                await asyncio.sleep(0.1); continue

        if data_dependency_deferred_queue:
            data_task = data_dependency_deferred_queue.popleft()
            if data_task.get("type") == "unit_slide":
                l_idx,s_idx,sl_idx,curr_p_item,dd_att = data_task["lesson_idx"],data_task["section_idx"],data_task["slide_idx"],data_task["prompt_item"],data_task["attempt_count"]
                item_being_processed = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                p_name = curr_p_item.prompt_name
                item_id_log = item_being_processed.name or f"U_L{l_idx}S{s_idx}Sl{sl_idx}"
                print(f"Data Dep (Unit): Item '{item_id_log}', Prompt '{p_name}', DD Attempt {dd_att + 1}")
                con_status, fp_text_none = _construct_full_prompt(curr_p_item,item_being_processed,active_prompts)

                if con_status == PromptConstructionStatus.SUCCESS:
                    if fp_text_none is None:
                        out_err,_ = get_or_create_output_item(item_being_processed,p_name); out_err.status="ERROR_CONSTRUCTION"; out_err.output="Error in prompt construction."; continue
                    if last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds):
                        data_task["attempt_count"]=dd_att; data_dependency_deferred_queue.append(data_task); await asyncio.sleep(0.1); continue
                    queue_ctx = {"type":"unit_slide","lesson_idx":l_idx,"section_idx":s_idx,"slide_idx":sl_idx}
                    if await _execute_api_call_for_prompt(gemini_analysis_service,item_being_processed,item_id_log,curr_p_item,fp_text_none,0,api_retry_queue,queue_ctx):
                        last_rate_limit_event_time = time.monotonic()
                elif con_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                    out_pend,_ = get_or_create_output_item(item_being_processed,p_name)
                    if dd_att + 1 < settings.max_data_dependency_retries:
                        data_task["attempt_count"]=dd_att+1; data_dependency_deferred_queue.append(data_task)
                        out_pend.status="DATA_DEPENDENCY_PENDING"; out_pend.output=f"Waiting for data (attempt {dd_att+1})."
                    else:
                        out_pend.status="DATA_DEPENDENCY_FAILED"; out_pend.output=f"Max data retries ({settings.max_data_dependency_retries}) for '{p_name}'."
            else: data_dependency_deferred_queue.appendleft(data_task)
            await asyncio.sleep(0.05); continue
            
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0: pass 
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds))) and not data_dependency_deferred_queue:
            if last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds):
                rem_cool = settings.retry_cooldown_seconds-(time.monotonic()-last_rate_limit_event_time)
                if rem_cool > 0.1: print(f"Loop (Units): Cooldown ({rem_cool:.1f}s)."); await asyncio.sleep(rem_cool)

    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warn (Units): Queues not empty. DataQ:{len(data_dependency_deferred_queue)}, ApiQ:{len(api_retry_queue)}")
        for task in list(data_dependency_deferred_queue):
             if task.get("type") == "unit_slide":
                l_idx,s_idx,sl_idx,p_item_left = task["lesson_idx"],task["section_idx"],task["slide_idx"],task["prompt_item"]
                item_left = enhanced_units_output[l_idx].sections[s_idx].slides[sl_idx]
                out_timeout,_=get_or_create_output_item(item_left,p_item_left.prompt_name)
                if out_timeout.status=="DATA_DEPENDENCY_PENDING" or not out_timeout.status:
                    out_timeout.status="DATA_DEPENDENCY_TIMEOUT"; out_timeout.output="Cycle limit waiting for data."
    print(f"Finished /enhance/units: {total_slide_prompts} tasks, {processing_cycles} cycles.")
    return EnhanceUnitsResponse(lessons=enhanced_units_output)

@app.post("/enhance/lessons", response_model=EnhanceLessonsResponse, status_code=status.HTTP_200_OK)
async def enhance_simple_lessons_endpoint(request: EnhanceLessonsRequest):
    if not gemini_analysis_service:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Generative Analysis service not initialized.")
    if not request.lessons: return EnhanceLessonsResponse(lessons=[])

    active_prompts = request.prompts if request.prompts is not None else []
    if not active_prompts:
        print("Warning: No prompts for /enhance/lessons. Returning original data.")
        return EnhanceLessonsResponse(lessons=[lesson.model_copy(deep=True) for lesson in request.lessons])

    print(f"Enhance Lessons: {len(request.lessons)} lessons, {len(active_prompts)} prompts.")
    enhanced_simple_lessons_output: List[LessonSimple] = [l.model_copy(deep=True) for l in request.lessons]
    api_retry_queue: deque = deque()
    data_dependency_deferred_queue: deque = deque()
    total_lesson_prompts = 0

    for lesson_simple_idx, _ in enumerate(enhanced_simple_lessons_output):
        for prompt_item in active_prompts:
            task_context = {"type":"lesson_simple", "lesson_simple_idx":lesson_simple_idx, "prompt_item":prompt_item, "attempt_count":0}
            data_dependency_deferred_queue.append(task_context)
            total_lesson_prompts += 1
    
    if total_lesson_prompts == 0: return EnhanceLessonsResponse(lessons=enhanced_simple_lessons_output)

    last_rate_limit_event_time: Optional[float] = None
    processing_cycles = 0
    max_processing_cycles_heuristic = total_lesson_prompts * (settings.max_api_retries + settings.max_data_dependency_retries + 5)

    while data_dependency_deferred_queue or api_retry_queue:
        processing_cycles += 1
        if processing_cycles > max_processing_cycles_heuristic: break

        if api_retry_queue:
            if not (last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds)):
                api_task = api_retry_queue.popleft()
                if api_task.get("type") == "lesson_simple":
                    ls_idx,p_item,fp_text,api_att = api_task["lesson_simple_idx"],api_task["prompt_item"],api_task["full_prompt_text"],api_task["api_attempt_count"]
                    item_to_process = enhanced_simple_lessons_output[ls_idx]
                    item_id_log = getattr(item_to_process,'file_name',None) or getattr(item_to_process,'lesson_id',None) or f"LessonS{ls_idx}"
                    print(f"API Retry (LessonS): Item '{item_id_log}', Prompt '{p_item.prompt_name}', Attempt {api_att + 1}")
                    queue_ctx = {"type":"lesson_simple", "lesson_simple_idx":ls_idx}
                    if await _execute_api_call_for_prompt(gemini_analysis_service,item_to_process,item_id_log,p_item,fp_text,api_att,api_retry_queue,queue_ctx):
                        last_rate_limit_event_time = time.monotonic()
                else: api_retry_queue.appendleft(api_task)
                await asyncio.sleep(0.1); continue
        
        if data_dependency_deferred_queue:
            data_task = data_dependency_deferred_queue.popleft()
            if data_task.get("type") == "lesson_simple":
                ls_idx,curr_p_item,dd_att = data_task["lesson_simple_idx"],data_task["prompt_item"],data_task["attempt_count"]
                item_being_processed = enhanced_simple_lessons_output[ls_idx]
                p_name = curr_p_item.prompt_name
                item_id_log = getattr(item_being_processed,'file_name',None) or getattr(item_being_processed,'lesson_id',None) or f"LessonS{ls_idx}"
                print(f"Data Dep (LessonS): Item '{item_id_log}', Prompt '{p_name}', DD Attempt {dd_att + 1}")
                con_status, fp_text_none = _construct_full_prompt(curr_p_item,item_being_processed,active_prompts)

                if con_status == PromptConstructionStatus.SUCCESS:
                    if fp_text_none is None:
                        out_err,_=get_or_create_output_item(item_being_processed,p_name);out_err.status="ERROR_CONSTRUCTION";out_err.output="Error in prompt construction."; continue
                    if last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds):
                        data_task["attempt_count"]=dd_att; data_dependency_deferred_queue.append(data_task); await asyncio.sleep(0.1); continue
                    queue_ctx = {"type":"lesson_simple","lesson_simple_idx":ls_idx}
                    if await _execute_api_call_for_prompt(gemini_analysis_service,item_being_processed,item_id_log,curr_p_item,fp_text_none,0,api_retry_queue,queue_ctx):
                        last_rate_limit_event_time = time.monotonic()
                elif con_status == PromptConstructionStatus.MISSING_DEPENDENCY:
                    out_pend,_ = get_or_create_output_item(item_being_processed,p_name)
                    if dd_att + 1 < settings.max_data_dependency_retries:
                        data_task["attempt_count"]=dd_att+1; data_dependency_deferred_queue.append(data_task)
                        out_pend.status="DATA_DEPENDENCY_PENDING"; out_pend.output=f"Waiting for data (attempt {dd_att+1})."
                    else:
                        out_pend.status="DATA_DEPENDENCY_FAILED"; out_pend.output=f"Max data retries ({settings.max_data_dependency_retries}) for '{p_name}'."
            else: data_dependency_deferred_queue.appendleft(data_task)
            await asyncio.sleep(0.05); continue
            
        if not api_retry_queue and not data_dependency_deferred_queue and processing_cycles > 0: pass
        elif (not api_retry_queue or (last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds))) and not data_dependency_deferred_queue:
            if last_rate_limit_event_time and (time.monotonic()-last_rate_limit_event_time < settings.retry_cooldown_seconds):
                rem_cool = settings.retry_cooldown_seconds-(time.monotonic()-last_rate_limit_event_time)
                if rem_cool > 0.1: print(f"Loop (Lessons): Cooldown ({rem_cool:.1f}s)."); await asyncio.sleep(rem_cool)
    
    if data_dependency_deferred_queue or api_retry_queue:
        print(f"Warn (Lessons): Queues not empty. DataQ:{len(data_dependency_deferred_queue)}, ApiQ:{len(api_retry_queue)}")
        for task in list(data_dependency_deferred_queue):
             if task.get("type") == "lesson_simple":
                ls_idx,p_item_left = task["lesson_simple_idx"],task["prompt_item"]
                item_left = enhanced_simple_lessons_output[ls_idx]
                out_timeout,_=get_or_create_output_item(item_left,p_item_left.prompt_name)
                if out_timeout.status=="DATA_DEPENDENCY_PENDING" or not out_timeout.status:
                    out_timeout.status="DATA_DEPENDENCY_TIMEOUT"; out_timeout.output="Cycle limit waiting for data."

    print(f"Finished /enhance/lessons: {total_lesson_prompts} tasks, {processing_cycles} cycles.")
    return EnhanceLessonsResponse(lessons=enhanced_simple_lessons_output)

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    if not credentials:
         return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Credentials not loaded. Check GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 in settings."})
    if not storage_service or not pdf_splitter_service or not gemini_analysis_service or not pdf_text_extractor_service:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "One or more required services not initialized. Check configuration and logs."})
    return {"status": "ok"}

@app.get("/debug/files", status_code=status.HTTP_200_OK)
async def debug_files():
    """Debug endpoint to list all files in Google AI storage."""
    if not gemini_analysis_service:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "Generative Analysis service not initialized."})
    
    try:
        # Get all files from Google AI storage
        all_files = await gemini_analysis_service.list_all_uploaded_files()
        
        return {
            "google_ai_storage_files": all_files,
            "total_files_in_storage": len(all_files)
        }
    except Exception as e:
        print(f"Error during debug files request: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": str(e)})

# To run locally: uvicorn main:app --reload