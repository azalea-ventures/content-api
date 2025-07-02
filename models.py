# models.py

from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Union
import uuid # For default task_id

# --- SHARED MODELS (Used by /enhance/* and /extract) ---

class PromptItem(BaseModel):
    """Defines a single prompt to be processed."""
    prompt_name: str
    prompt_template: str
    # For /extract, this refers to outputs of other prompts in the same ExtractTask
    # For /enhance, this refers to 'content' or outputs of other prompts on the same Slide/LessonSimple
    lesson_properties_to_append: List[str] = Field(default_factory=list)
    
    # If different prompts expect different JSON structures (especially for /extract):
    output_json_format_example_str: Optional[str] = None # String representation of the JSON example
                                                        # If None, implies general text or JSON structure defined only in prompt.
    # No 'order' or 'depends_on' for implicit, data-driven ordering

class GeneratedContentItem(BaseModel):
    """Represents a piece of content generated for a specific prompt."""
    prompt_name: str
    output: Optional[str] = None # The raw generated string (JSON string or text)
    status: Optional[str] = None # e.g., "SUCCESS", "RATE_LIMIT", "DATA_DEPENDENCY_PENDING"

# --- Models for /enhance/units ---
class Slide(BaseModel):
    name: Optional[str] = None
    content: str
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}

class Section(BaseModel):
    name: Optional[str] = None
    slides: List[Slide]
    model_config = {"extra": "allow"}

class LessonUnit(BaseModel):
    lesson_id: Optional[str] = None
    title: Optional[str] = None
    sections: List[Section]
    model_config = {"extra": "allow"}

class EnhanceUnitsRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonUnit]

class EnhanceUnitsResponse(BaseModel):
    lessons: List[LessonUnit]

# --- Models for /enhance/lessons ---
class LessonSimple(BaseModel):
    timestamp: Optional[str] = None
    file_id: Optional[str] = None # Often used as an identifier
    url: Optional[str] = None
    folder_path: Optional[str] = None
    file_name: Optional[str] = None
    lesson_date: Optional[str] = None
    iclo_slide: Optional[float] = None
    strategy_application_slide: Optional[float] = None
    learning_objective: Optional[str] = None
    standards: Optional[str] = None
    clc_element: Optional[str] = None
    strategy_application_element: Optional[str] = None
    content: str
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}

class EnhanceLessonsRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonSimple]

class EnhanceLessonsResponse(BaseModel):
    lessons: List[LessonSimple]


# --- MODELS FOR /extract (REVISED) ---

# This might be specific to a type of extraction, can be kept if useful
# or if the output of certain extraction prompts should conform to this.
class ExtractedSectionDataItem(BaseModel):
    page: int
    title: Optional[str] = None
    paragraph: str
ExtractedDataDict = Dict[str, List[ExtractedSectionDataItem]]


class ExtractTask(BaseModel):
    """Defines a task to perform multiple extractions on a single target document."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_drive_file_id: str
    prompts: List[PromptItem]

class ExtractTaskResponseItemSuccess(BaseModel):
    task_id: str
    target_drive_file_id: str
    target_drive_file_name: Optional[str] = None
    extraction_results: List[GeneratedContentItem]

class ExtractTaskResponseItemError(BaseModel):
    task_id: str
    target_drive_file_id: str
    error: str
    detail: Optional[str] = None
    failed_prompt_name: Optional[str] = None # Optionally pinpoint which prompt caused task-level failure

class BatchExtractTaskResult(BaseModel):
    success: bool # Overall success of processing the ExtractTask item
    result: Optional[ExtractTaskResponseItemSuccess] = None
    error_info: Optional[ExtractTaskResponseItemError] = None

# --- Other existing models for /analyze, /split (UNCHANGED) ---
class SectionInfo(BaseModel):
    sectionName: str
    pageRange: str

class AnalyzeRequestItem(BaseModel):
    file_id: str
    prompt_text: str

class AnalyzeResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class AnalyzeResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    sections: List[SectionInfo]

class BatchAnalyzeItemResult(BaseModel):
    success: bool
    result: Optional[AnalyzeResponseItemSuccess] = None
    error_info: Optional[AnalyzeResponseItemError] = None

class UploadedFileInfo(BaseModel):
    sectionName: str
    uploadedDriveFileId: Optional[str] = None
    uploadedDriveFileName: Optional[str] = None

class SplitResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    uploadedSections: List[UploadedFileInfo]

class SplitResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class BatchSplitItemResult(BaseModel):
    success: bool
    result: Optional[SplitResponseItemSuccess] = None
    error_info: Optional[SplitResponseItemError] = None

class SplitRequest(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    sections: List[SectionInfo]